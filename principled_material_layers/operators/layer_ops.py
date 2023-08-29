# SPDX-License-Identifier: GPL-2.0-or-later

import bpy

from bpy.types import Operator

from bpy.props import (BoolProperty,
                       EnumProperty,
                       IntProperty,
                       IntVectorProperty,
                       StringProperty)

from .. import image_mapping, utils
from ..bake import apply_node_mask_bake, bake_node_mask_to_image
from ..channel import SOCKET_TYPES
from ..material_layer import LAYER_TYPES

from ..utils.image import copy_image
from ..utils.layer_stack_utils import get_layer_stack, get_layer_stack_by_id
from ..utils.naming import suffix_num_unique_in
from ..utils.nodes import get_nodes_by_type
from ..utils.ops import ensure_global_undo, pml_op_poll, save_all_modified

from . import channel_ops


class PML_OT_set_active_layer_index(Operator):
    bl_idname = "material.pml_set_active_layer_index"
    bl_label = "Set Active Layer"
    bl_options = {'INTERNAL', 'REGISTER', 'UNDO'}

    layer_stack_id: StringProperty(
        name="Layer Stack Identifier"
    )

    layer_index: IntProperty(
        name="Layer Index",
        min=0
    )

    def execute(self, _context):
        layer_stack = get_layer_stack_by_id(self.layer_stack_id)
        if layer_stack is None:
            return {'CANCELLED'}

        # Save all modified images to help prevent issues with undo
        save_all_modified()

        layer_stack.set_active_layer_index(self.layer_index)

        # Save all modified again (since set_active_layer_index may
        # have modified images)
        save_all_modified()

        ensure_global_undo()

        bpy.ops.ed.undo_push(message="Set Active Layer")

        return {'FINISHED'}


class PML_OT_add_layer(Operator):
    bl_idname = "material.pml_add_layer"
    bl_label = "Add Material Layer"
    bl_description = "Adds a new layer above the active layer"
    bl_options = {'REGISTER', 'UNDO'}

    layer_type: EnumProperty(
        items=LAYER_TYPES,
        name="Type",
        description="The type of layer to add",
        default='MATERIAL_PAINT'
    )

    single_channel: StringProperty(
        name="Single Channel",
        description="If specified adds a layer for directly painting the"
                    "value of a single channel",
        default="",
        options={'SKIP_SAVE'}
    )

    _new_layer_names = {
        'MATERIAL_PAINT': "Paint Layer",
        'MATERIAL_FILL': "Fill Layer",
        'MATERIAL_W_ALPHA': "Custom Alpha Layer"
    }

    @classmethod
    def description(cls, _context, properties) -> str:
        if (properties.layer_type == 'MATERIAL_W_ALPHA'
                and properties.single_channel):
            return "Adds a new layer for painting on a single channel"
        enum_tuple = next(x for x in LAYER_TYPES
                          if x[0] == properties.layer_type)
        return (f"Adds a new {enum_tuple[1]} layer above the active layer. \n"
                f"{enum_tuple[1]}: {enum_tuple[2]}")

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def draw(self, context):
        self.layout.prop(self, "layer_type")
        if self.layer_type == 'MATERIAL_W_ALPHA':
            layer_stack = get_layer_stack(context)
            self.layout.prop_search(self, "single_channel",
                                    layer_stack, "channels")

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer
        is_single_ch_layer = (self.layer_type == 'MATERIAL_W_ALPHA'
                              and self.single_channel)

        if active_layer:
            top_level_layers_ref = layer_stack.top_level_layers_ref
            position = top_level_layers_ref.find(active_layer.identifier)
            # Increment position by 1 to insert above the active layer
            position = -1 if position < 0 else position + 1
        else:
            # If there is no active layer add the new layer to the top
            # of the stack.
            position = -1

        if is_single_ch_layer:
            ch = layer_stack.channels.get(self.single_channel)
            if ch is None:
                self.report({'WARNING'},
                            f"Cannot find channel {self.single_channel}")
                return {'CANCELLED'}

        new_layer = layer_stack.insert_layer(
                        self.new_layer_name, position,
                        layer_type=self.layer_type,
                        channels=[ch] if is_single_ch_layer else None)

        # Initialization for single channel layers
        if is_single_ch_layer:
            self.init_single_channel_layer(new_layer, ch)

        layer_stack.active_layer = new_layer

        ensure_global_undo()

        return {'FINISHED'}

    @property
    def is_layer_single_channel(self) -> bool:
        return self.layer_type == 'MATERIAL_W_ALPHA' and self.single_channel

    @property
    def new_layer_name(self) -> str:
        if self.is_layer_single_channel:
            return f"{self.single_channel} Layer"

        return self._new_layer_names.get(self.layer_type, "Layer")

    def init_single_channel_layer(self, layer, channel) -> None:
        node_tree = layer.node_tree
        im = layer.layer_stack.image_manager

        # FIXME Should initialize tiles for UDIMs
        image = bpy.data.images.new(f"{channel.name} Layer",
                                    im.image_width, im.image_height,
                                    alpha=True,
                                    is_data=(channel.socket_type != 'COLOR'),
                                    float_buffer=im.use_float)
        image.generated_color = (0, 0, 0, 0)
        image.alpha_mode = 'STRAIGHT'
        image.pixels[0] = image.pixels[0]  # Needed to pack image
        image.pack()

        img_node = node_tree.nodes.new("ShaderNodeTexImage")
        img_node.image = image

        group_output = utils.nodes.get_node_by_type(node_tree,
                                                    "NodeGroupOutput")
        if group_output is None:
            group_output = node_tree.nodes.new("NodeGroupOutput")

        img_node.location = group_output.location
        img_node.location.x -= 400

        out_socket = group_output.inputs.get(channel.name)
        if out_socket is not None:
            node_tree.links.new(out_socket, img_node.outputs[0])

        alpha_ch = layer.custom_alpha_channel
        out_socket = group_output.inputs.get(alpha_ch.name if alpha_ch else "")
        if out_socket is not None:
            node_tree.links.new(out_socket, img_node.outputs[1])


class PML_OT_remove_layer(Operator):
    bl_idname = "material.pml_remove_layer"
    bl_label = "Delete Material Layer"
    bl_description = "Removes the active layer from the layer stack."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context):
            return False
        active_layer = get_layer_stack(context).active_layer
        return active_layer is not None and not active_layer.is_base_layer

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        if not layer_stack.is_initialized or active_layer is None:
            return {'CANCELLED'}

        layer_stack.remove_layer(active_layer)

        ensure_global_undo()
        return {'FINISHED'}


class MoveLayerBase(Operator):

    direction = ""

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context):
            return False
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        if active_layer is None:
            return False
        if active_layer.is_base_layer:
            cls.poll_message_set("Cannot move the base layer")
            return False
        if cls.direction == 'UP' and active_layer == layer_stack.top_layer:
            return False
        if cls.direction == 'DOWN':
            layer_below = active_layer.get_layer_below()
            if not layer_below or layer_below.is_base_layer:
                return False
        return True

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        try:
            layer_stack.move_layer(layer_stack.active_layer,
                                   self.direction)
        except ValueError as e:
            self.report({'WARNING'}, str(e))
            return {'CANCELLED'}
        ensure_global_undo()

        return {'FINISHED'}


class PML_OT_move_layer_up(MoveLayerBase, Operator):
    bl_idname = "material.pml_move_layer_up"
    bl_label = "Move Material Layer Up"
    bl_description = "Moves the active layer upwards."
    bl_options = {'REGISTER', 'UNDO'}

    direction = 'UP'


class PML_OT_move_layer_down(MoveLayerBase, Operator):
    bl_idname = "material.pml_move_layer_down"
    bl_label = "Move Material Layer Down"
    bl_description = "Moves the active layer downwards."
    bl_options = {'REGISTER', 'UNDO'}

    direction = 'DOWN'


class PML_OT_new_node_mask(Operator):
    bl_idname = "material.pml_new_node_mask"
    bl_label = "New Node Mask"
    bl_description = "Create a new node mask for the active layer"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context):
            return False
        active_layer = get_layer_stack(context).active_layer
        if not active_layer:
            return False
        if active_layer.is_base_layer:
            cls.poll_message_set("Cannot add a node mask to the base layer")
            return False
        return True

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        group_name = f"{active_layer.name} Node Mask"

        new_group = self.create_mask_node_group(group_name)
        active_layer.node_mask = new_group

        return {'FINISHED'}

    @staticmethod
    def create_mask_node_group(name: str = "") -> bpy.types.ShaderNodeTree:
        if not name:
            name = "Node Mask"

        node_group = bpy.data.node_groups.new(type="ShaderNodeTree", name=name)

        output = node_group.outputs.new("NodeSocketFloatFactor", "Fac")
        output.min_value = 0.0
        output.max_value = 1.0
        output.default_value = 1.0

        node_group.nodes.new("NodeGroupOutput")

        return node_group


class PML_OT_unlink_node_mask(Operator):
    bl_idname = "material.pml_unlink_node_mask"
    bl_label = "Unlink Node Mask"
    bl_description = "Unlink the node mask"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer
        if active_layer is None or active_layer.node_mask is None:
            return {'CANCELLED'}

        # If the node mask is being previewed by the layer stack then
        # clear previews.
        # TODO Move to update callback of MaterialLayer.node_mask
        if active_layer.node_mask == layer_stack.preview_group:
            channel_ops.clear_preview_channel(layer_stack)
        active_layer.node_mask = None
        return {'FINISHED'}


class PML_OT_apply_node_mask(Operator):
    bl_idname = "material.pml_apply_node_mask"
    bl_label = "Apply Node Mask"
    bl_description = ("Apply the active layer's node mask. This permanently "
                      "multiplies the layer's alpha by the node mask")
    bl_options = {'REGISTER', 'UNDO'}

    keep_node_mask: BoolProperty(
        name="Keep Node Mask",
        description="Keep using the same node group as this layer's node mask "
                    "after it has been applied",
        default=False
    )
    samples: IntProperty(
        name="Samples",
        description="The number of samples to use when baking the node mask",
        default=8
    )

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context):
            return False
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer
        if not active_layer:
            return False
        if not active_layer.enabled:
            cls.poll_message_set("Cannot apply node mask to a disabled layer")
            return False
        if layer_stack.image_manager.uses_tiled_images:
            cls.poll_message_set("Not yet supported for UDIMs")
            return False
        return (active_layer.node_mask is not None
                and active_layer.image is not None)

    def draw(self, context):
        layout = self.layout
        layer_stack = get_layer_stack(context)

        if layer_stack.active_layer.layer_type != "MATERIAL_PAINT":
            layout.label(icon="ERROR", text="Layer will be converted into a "
                         "paint layer.")
        layout.prop(self, "samples")
        layout.prop(self, "keep_node_mask")

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer
        im = layer_stack.image_manager

        if not context.selected_objects:
            self.report({'WARNING'}, "No objects are selected for baking")
            return {'CANCELLED'}

        save_all_modified()

        image = apply_node_mask_bake(active_layer, self.samples)

        try:
            if active_layer.layer_type != 'MATERIAL_PAINT':
                layer_stack.convert_layer(active_layer, 'MATERIAL_PAINT')

            copy_image(image, im.active_image)

        finally:
            bpy.data.images.remove(image)

        if not self.keep_node_mask:
            active_layer.node_mask = None

            # If the node mask is currently being previewed
            if layer_stack.preview_group == active_layer.node_mask:
                channel_ops.clear_preview_channel(layer_stack)

        layer_stack.node_manager.rebuild_node_tree()

        return {'FINISHED'}

    def invoke(self, context, _event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


class PML_OT_node_mask_to_stencil(Operator):
    bl_idname = "material.pml_node_mask_to_stencil"
    bl_label = "Set as Stencil Mask"
    bl_description = ("Converts the node mask to an image and sets it as "
                      "the current Stencil Mask")
    bl_options = {'REGISTER', 'UNDO'}

    MASK_IMAGE_NAME = "Node Mask Image (PML)"

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context):
            return False
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        if layer_stack.image_manager.uses_tiled_images:
            cls.poll_message_set("Not yet supported for UDIMs")
            return False

        return (active_layer is not None
                and active_layer.node_mask is not None)

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        baked_image = bake_node_mask_to_image(active_layer)

        existing = bpy.data.images.get(self.MASK_IMAGE_NAME)
        if existing is not None:
            bpy.data.images.remove(existing)

        baked_image.name = self.MASK_IMAGE_NAME

        self.set_as_stencil_mask(baked_image, context)

        return {'FINISHED'}

    def set_as_stencil_mask(self, image, context) -> None:
        paint_settings = context.tool_settings.image_paint
        paint_settings.use_stencil_layer = True
        paint_settings.stencil_image = image


class PML_OT_convert_layer(Operator):
    bl_idname = "material.pml_convert_layer"
    bl_label = "Change Layer Type"
    bl_description = "Change the type of the active layer"
    bl_options = {'REGISTER', 'UNDO'}

    new_type: EnumProperty(
        items=LAYER_TYPES,
        name="Type",
        description="The new type of the layer"
    )

    keep_image: BoolProperty(
        name="Keep Image",
        description="Keep the layer's image data even if converting to a type "
                    "that does not use an image",
        default=True
    )

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context):
            return False
        active_layer = get_layer_stack(context).active_layer
        if active_layer is None:
            cls.poll_message_set("No active layer")
            return False
        if active_layer.is_base_layer:
            cls.poll_message_set("Cannot change base layer type")
            return False
        return True

    def draw(self, _context):
        layout = self.layout
        row = layout.row()
        row.prop(self, "new_type")
        if len(LAYER_TYPES) == 2:
            row.enabled = False
        if self.new_type != 'MATERIAL_PAINT':
            layout.prop(self, "keep_image")

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        active_layer = layer_stack.active_layer

        if active_layer.layer_type == self.new_type:
            self.report({'INFO'}, "Active layer is already a "
                                  f"{self.new_type_name}")
            return {'CANCELLED'}

        save_all_modified()

        layer_stack.convert_layer(active_layer, self.new_type, self.keep_image)

        ensure_global_undo()

        return {'FINISHED'}

    def invoke(self, context, _event):
        active_layer = get_layer_stack(context).active_layer

        # Set to a value different from the layer's current type
        self.new_type = next(x[0] for x in LAYER_TYPES
                             if x[0] != active_layer.layer_type)

        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    @property
    def new_type_name(self) -> str:
        return self.layout.enum_item_name(self, "new_type", self.new_type)


class PML_OT_layer_img_projection(Operator):
    bl_idname = "material.pml_set_layer_img_proj"
    bl_label = "Set Image Projection"
    bl_description = ("Changes the projection of any Image Texture nodes "
                      "in the active layer's material")
    bl_options = {'INTERNAL', 'REGISTER'}

    proj_mode: EnumProperty(
        items=image_mapping.IMG_PROJ_MODES,
        name="Projection",
        default='FLAT',
    )

    @classmethod
    def poll(cls, context):
        return (pml_op_poll(context)
                and get_layer_stack(context).active_layer is not None)

    def execute(self, context):
        active_layer = get_layer_stack(context).active_layer
        node_tree = active_layer.node_tree

        if not any(get_nodes_by_type(node_tree, "ShaderNodeTexImage")):
            self.report({'INFO'}, "No Image nodes in layer's node tree")
            return {'CANCELLED'}
        if active_layer.img_proj_mode == self.proj_mode == 'ORIGINAL':
            return {'CANCELLED'}

        image_mapping.set_layer_projection(active_layer, self.proj_mode)

        return {'FINISHED'}


class PML_OT_layer_add_channel(Operator):
    bl_idname = "material.pml_layer_add_channel"
    bl_label = "Add Layer Channel"
    bl_description = "Adds a channel to the active layer"
    bl_options = {'REGISTER', 'UNDO'}

    channel_name: StringProperty(
        name="Channel"
    )

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def draw(self, context):
        layout = self.layout
        layer_stack = get_layer_stack(context)

        layout.prop_search(self, "channel_name",
                           layer_stack, "channels", text="Channel")

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        layer_stack_ch = layer_stack.channels.get(self.channel_name)
        if layer_stack_ch is None:
            self.report({'WARNING'}, "Active layer stack has no channel "
                                     f"named '{self.channel_name}'")
            return {'CANCELLED'}

        if self.channel_name in active_layer.channels:
            self.report({'WARNING'}, "Active layer already has channel "
                                     f"'{self.channel_name}'")
            return {'CANCELLED'}

        channel = active_layer.add_channel(layer_stack_ch)
        active_layer.active_channel = channel

        layer_stack.node_manager.rebuild_node_tree()
        return {'FINISHED'}

    def invoke(self, context, _event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


class PML_OT_layer_remove_channel(Operator):
    bl_idname = "material.pml_layer_remove_channel"
    bl_label = "Remove Layer Channel"
    bl_description = "Removes a channel from the active layer."
    bl_options = {'REGISTER', 'UNDO'}

    channel_name: StringProperty(
        name="Channel"
    )

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        if len(active_layer.channels) == 1:
            self.report({'WARNING'}, "Cannot have a layer with no channels.")
            return {'CANCELLED'}

        try:
            active_layer.remove_channel(self.channel_name)
        except (ValueError, RuntimeError) as e:
            self.report({'ERROR'}, f"Could not remove {self.channel_name} "
                                   f"from layer {active_layer.name}: {e}.")
            return {'CANCELLED'}

        layer_stack.node_manager.rebuild_node_tree()
        return {'FINISHED'}


class PML_OT_stack_add_channel(Operator):
    bl_idname = "material.pml_stack_add_channel"
    bl_label = "Add Layer Stack Channel"
    bl_description = ("Adds a new channel to the active layer stack")
    bl_options = {'INTERNAL', 'REGISTER', 'UNDO'}

    channel_name: StringProperty(
        name="Channel",
        description="The name of the channel to add."
    )
    channel_type: EnumProperty(
        items=SOCKET_TYPES,
        name="Type",
        description="The type of socket that the new channel should use."
    )

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def draw(self, context):
        layout = self.layout
        layer_stack = get_layer_stack(context)

        layout.prop(self, "channel_name")
        if self.channel_name in layer_stack.channels:
            layout.label(icon='ERROR',
                         text="Layer stack already has a "
                              f"channel named {self.channel_name}")

        layout.prop(self, "channel_type")

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        try:
            ch = layer_stack.add_channel(self.channel_name, self.channel_type)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        layer_stack.active_channel = ch

        save_all_modified()
        ensure_global_undo()
        return {'FINISHED'}

    def invoke(self, context, _event):
        layer_stack = get_layer_stack(context)

        # Default name unique in layer_stack.channels
        self.channel_name = suffix_num_unique_in("New Channel",
                                                 layer_stack.channels)
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


class PML_OT_stack_remove_channel(Operator):
    bl_idname = "material.pml_stack_remove_channel"
    bl_label = "Remove Layer Stack Channel"
    bl_description = ("Removes a channel from the active layer stack")
    bl_options = {'INTERNAL', 'REGISTER', 'UNDO'}

    channel_name: StringProperty(
        name="Channel",
        description="The name of the channel to add."
    )

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context):
            return False

        layer_stack = get_layer_stack(context)
        # Layer stacks with no channels are not supported
        return len(layer_stack.channels) > 1

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        try:
            layer_stack.remove_channel(self.channel_name)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        save_all_modified()
        ensure_global_undo()
        return {'FINISHED'}


class PML_OT_stack_resize_layers(Operator):
    bl_idname = "material.pml_stack_resize_layers"
    bl_label = "Resize Layers"
    bl_description = ("Resizes all the image based layers of the active "
                      "layer stack")
    bl_options = {'INTERNAL', 'REGISTER', 'UNDO'}

    size: IntVectorProperty(
        name="Size",
        description="The new size to use for image based layers",
        min=1, soft_max=2**14,
        subtype="XYZ",
        size=2,
    )

    # TODO Add lock ratio option

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        im = get_layer_stack(context).image_manager

        im.resize_all_layers(self.size[0], self.size[1])

        ensure_global_undo()

        return {'FINISHED'}

    def invoke(self, context, _event):
        im = get_layer_stack(context).image_manager
        self.size = (im.image_width, im.image_height)

        wm = context.window_manager
        return wm.invoke_props_dialog(self)


class PML_OT_resubscribe_msgbus(Operator):
    bl_idname = "material.pml_resubscribe_msgbus"
    bl_label = "Msgbus Resubscribe"
    bl_description = (
        "Resubscribe all bpy.msgbus subscriptions of the active layer stack. "
        "If e.g. adding/removing layers or changing the blend "
        "modes stop working properly then this may fix it")
    bl_options = {'INTERNAL', 'REGISTER'}

    @classmethod
    def poll(cls, context):
        return bool(get_layer_stack(context))

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        if layer_stack is None:
            self.report({'WARNING'}, "No active layer stack")
            return {'CANCELLED'}
        if not layer_stack.is_initialized:
            self.report({'WARNING'}, "Layer stack is not initialized")
            return {'CANCELLED'}

        layer_stack.reregister_msgbus()

        material = layer_stack.material
        if not material.node_tree:
            return {'FINISHED'}

        # Resubscribe RNA for any ShaderNodePMLStack of the layer_stack
        pml_nodes = list(get_nodes_by_type(material.node_tree,
                                           "ShaderNodePMLStack"))
        for node in pml_nodes:
            node.reregister_msgbus()
        self.report({'INFO'}, f"Resubscribed {material.name}'s layer stack "
                              f"and {len(pml_nodes)} node(s)")
        return {'FINISHED'}


class PML_OT_reload_active_layer(Operator):
    bl_idname = "material.pml_reload_active_layer"
    bl_label = "Reload Active Layer"
    bl_description = (
        "Reload the canvas image from the image that stores the layer's "
        "data, discarding all changes since the last time the layer was "
        "active. This is only for layers that pack their data in images "
        "shared with other layers")
    bl_options = {'INTERNAL', 'REGISTER'}

    @classmethod
    def poll(cls, context):
        active_layer = get_layer_stack(context).active_layer
        if not pml_op_poll(context):
            return False

        active_layer = get_layer_stack(context).active_layer
        return active_layer is not None and active_layer.has_shared_image

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        if not active_layer.has_shared_image:
            self.report({'WARNING'}, "Active layer does not use a shared "
                                     "image")
            return {'CANCELLED'}

        layer_stack.image_manager.reload_tmp_active_image()

        return {'FINISHED'}


classes = (PML_OT_set_active_layer_index,
           PML_OT_add_layer,
           PML_OT_remove_layer,
           PML_OT_move_layer_up,
           PML_OT_move_layer_down,
           PML_OT_new_node_mask,
           PML_OT_unlink_node_mask,
           PML_OT_apply_node_mask,
           PML_OT_node_mask_to_stencil,
           PML_OT_convert_layer,
           PML_OT_layer_img_projection,
           PML_OT_layer_add_channel,
           PML_OT_layer_remove_channel,
           PML_OT_stack_add_channel,
           PML_OT_stack_remove_channel,
           PML_OT_stack_resize_layers,
           PML_OT_resubscribe_msgbus,
           PML_OT_reload_active_layer,
           )

register, unregister = bpy.utils.register_classes_factory(classes)
