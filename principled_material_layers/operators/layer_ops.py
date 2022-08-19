# SPDX-License-Identifier: GPL-2.0-or-later

import bpy

from bpy.types import Operator

from bpy.props import (BoolProperty,
                       EnumProperty,
                       IntProperty,
                       IntVectorProperty,
                       StringProperty)

from ..bake import apply_node_mask_bake
from ..blending import blend_mode_description, blend_mode_display_name
from ..channel import SOCKET_TYPES
from ..material_layer import LAYER_TYPES

from ..utils.image import copy_image
from ..utils.layer_stack_utils import get_layer_stack, get_layer_stack_by_id
from ..utils.naming import suffix_num_unique_in
from ..utils.nodes import get_nodes_by_type
from ..utils.ops import ensure_global_undo, pml_op_poll, save_all_modified


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

    def execute(self, context):
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
    bl_description = "Adds a new paint layer to the top of the stack."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        new_layer = layer_stack.insert_layer("Layer", -1)
        layer_stack.active_layer = new_layer

        ensure_global_undo()

        return {'FINISHED'}


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

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

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
        return pml_op_poll(context) and get_layer_stack(context).active_layer

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        if active_layer == layer_stack.base_layer:
            self.report("Masks are not supported for the base layer")
            return {'CANCELLED'}

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
        active_layer = get_layer_stack(context).active_layer
        return (active_layer
                and active_layer.node_mask is not None
                and active_layer.image is not None)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "samples")
        layout.prop(self, "keep_node_mask")

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer
        im = layer_stack.image_manager

        if im.active_image is None:
            self.report({'ERROR'}, "The layer stack's image manager has no "
                                   "active image.")
            return {'CANCELLED'}
        if not context.selected_objects:
            self.report({'WARNING'}, "No objects are selected for baking")
            return {'CANCELLED'}

        save_all_modified()

        image = apply_node_mask_bake(active_layer, self.samples)

        try:
            copy_image(image, im.active_image)
        finally:
            bpy.data.images.remove(image)

        if not self.keep_node_mask:
            active_layer.node_mask = None

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


# TODO Register and add to panel
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

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        active_layer = layer_stack.active_layer

        if active_layer is None:
            self.report({'WARNING'}, "No active layer.")
            return {'CANCELLED'}
        active_layer.convert_to(self.new_type)
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

    def invoke(self, context, event):
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


class PML_OT_channel_set_blend_mode(Operator):
    bl_idname = "material.pml_channel_set_blend_mode"
    bl_label = "Set Blend Mode"
    bl_description = "Sets the blend mode of a layer's channel."
    bl_options = {'INTERNAL', 'REGISTER'}

    layer_name: StringProperty(name="Layer")
    channel_name: StringProperty(name="Channel Name")
    blend_mode: StringProperty(name="Blend Mode")

    @classmethod
    def description(cls, context, properties):
        blend_mode = properties.blend_mode
        descript = blend_mode_description(blend_mode)
        return descript or blend_mode_display_name(blend_mode)

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def draw(self, context):
        return

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        layer = layer_stack.layers.get(self.layer_name, None)
        if layer is None:
            self.report({'ERROR'}, "Current layer_stack has no layer named"
                                   f"{self.layer_name}")
            return {'CANCELLED'}

        channel = layer.channels.get(self.channel_name, None)
        if channel is None:
            self.report({'ERROR'}, f"Layer {self.layer_name} has no channel"
                                   f"named {self.channel_name}")
            return {'CANCELLED'}

        channel.blend_mode = self.blend_mode
        # Seems to be necessary to explicitly publish the rna
        bpy.msgbus.publish_rna(key=channel.path_resolve("blend_mode", False))
        return {'FINISHED'}


class PML_OT_channel_set_custom_blend(Operator):
    bl_idname = "material.pml_channel_set_custom_blend"
    bl_label = "Set Custom Blend Group"
    bl_description = "Sets the custom blend group of a channel"
    bl_options = {'INTERNAL', 'REGISTER'}

    custom_blend: StringProperty(
        name="Blend Group",
        description=("The name of the node group to use for this channel's "
                     "blending.")
    )

    def execute(self, context):
        channel = getattr(context, "pml_channel", None)
        if channel is None:
            self.report(
                {'ERROR'},
                "pml_channel should be set using context_pointer_set "
                "before this operator is called.")
            return {'CANCELLED'}

        blend_group = bpy.data.node_groups.get(self.custom_blend)
        if blend_group is None:
            self.report({'WARNING'}, "Cannot find node group "
                                     f"'{self.custom_blend}'")
            return {'CANCELLED'}

        channel.blend_mode_custom = blend_group

        # Act as if the blend mode has been changed
        bpy.msgbus.publish_rna(key=channel.path_resolve("blend_mode", False))

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

    def invoke(self, context, event):
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

    def invoke(self, context, event):
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
        "Reload the canvas image from the image that stores "
        "the layer data. This is only for layers that pack their data in "
        "images shared with other layers")
    bl_options = {'INTERNAL', 'REGISTER'}

    @classmethod
    def poll(cls, context):
        active_layer = get_layer_stack(context).active_layer
        if not pml_op_poll(context):
            return False

        active_layer = get_layer_stack(context).active_layer
        return active_layer is not None and active_layer.uses_shared_image

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        if not active_layer.uses_shared_image:
            self.report({'WARNING'}, "Active layer does not use a shared "
                                     "image")
            return {'CANCELLED'}

        layer_stack.image_manager.reload_active_layer()

        return {'FINISHED'}


classes = (PML_OT_set_active_layer_index,
           PML_OT_add_layer,
           PML_OT_remove_layer,
           PML_OT_move_layer_up,
           PML_OT_move_layer_down,
           PML_OT_new_node_mask,
           PML_OT_apply_node_mask,
           PML_OT_layer_add_channel,
           PML_OT_layer_remove_channel,
           PML_OT_channel_set_blend_mode,
           PML_OT_channel_set_custom_blend,
           PML_OT_stack_add_channel,
           PML_OT_stack_remove_channel,
           PML_OT_stack_resize_layers,
           PML_OT_resubscribe_msgbus,
           PML_OT_reload_active_layer,
           )

register, unregister = bpy.utils.register_classes_factory(classes)
