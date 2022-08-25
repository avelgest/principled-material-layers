# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it

import bpy

from bpy.types import Operator

from bpy.props import (BoolProperty,
                       CollectionProperty,
                       EnumProperty,
                       IntProperty,
                       StringProperty)

from .material_ops import replace_layer_material

from ..channel import BasicChannel, is_socket_supported

from ..utils.nodes import reference_inputs
from ..utils.ops import pml_op_poll, pml_is_supported_editor
from ..utils.layer_stack_utils import get_layer_stack


def set_keep_ratio(self, value):
    if value:
        self.ratio = self.image_width / self.image_height
    else:
        self.ratio = 0.0


def width_keep_ratio_update(self, context):
    if self.keep_size_ratio:
        new_height = int(round(self.image_width / self.ratio))
        if self.image_height != new_height:
            self.image_height = new_height


def height_keep_ratio_update(self, context):
    if self.keep_size_ratio:
        new_width = int(round(self.image_height * self.ratio))
        if self.image_width != new_width:
            self.image_width = new_width


def _select_enum_prop_update(self, context):
    if self.select == 'ALL':
        for ch in self.channels:
            ch.enabled = True
    elif self.select == 'NONE':
        for ch in self.channels:
            ch.enabled = False


class PML_OT_initialize_layer_stack(Operator):
    """Initialize Principled Material Layers for the active material"""
    bl_idname = "material.pml_initialize_layer_stack"
    bl_label = "Initialize Principled Material Layers"
    bl_description = "Initialize a layer stack on the active material"
    bl_options = {'REGISTER', 'UNDO'}

    image_width: IntProperty(
        name="Width",
        description="Horizontal resolution of image-based layers",
        min=1, soft_max=2**14, default=1024,
        subtype='PIXEL',
        update=width_keep_ratio_update
    )
    image_height: IntProperty(
        name="Height",
        description="Vertical resolution of image-based layers",
        min=1, soft_max=2**14, default=1024,
        subtype='PIXEL',
        update=height_keep_ratio_update
    )
    keep_size_ratio: BoolProperty(
        name="Lock Ratio",
        description="Keep the width-height ratio constant",
        default=True,
        get=lambda self: self.ratio > 0.0,
        set=set_keep_ratio,
    )
    ratio: bpy.props.FloatProperty(
        default=1.0,
        description="The ratio of image_width to image_height or 0.0 if "
        "keep ratio is unchecked"
    )
    use_float_images: BoolProperty(
        name="32-bit Float",
        description="Use images with 32-bit float bit depth"
    )
    channels: CollectionProperty(
        name="Channels",
        type=BasicChannel,
        description="Lists material channels that can potentially be used"
    )
    uv_map: StringProperty(
        name="UV Map"
    )
    shader_node_name: StringProperty(
        name="Shader Node",
        description="Name of the node to connect to"
    )
    use_active_node: BoolProperty(
        name="Channels from Active Node",
        description="Get channels from the active node",
        default=True
    )
    base_layer_from_current: BoolProperty(
        name="Active Material as Base Layer",
        description="Use the active material as the layer stack's base layer",
        default=True
    )
    replace_connections: BoolProperty(
        name="Replace Shader Connections",
        description="Replace the links of the active material's surface "
                    "shader node with links to the newly created 'Material "
                    "Layers' node",
        default=True
    )
    auto_connect_shader: BoolProperty(
        name="Auto-Connect New Sockets",
        description="Automatically connect sockets of this layer stack's "
                    "Material Layer node to the shader node whenever a "
                    "channel is added/enabled",
        default=True
    )
    select: EnumProperty(
        name="Select",
        items=(('ALL', "Select All", ""),
               ('NONE', "Select None", "")),
        default='NONE',
        update=_select_enum_prop_update
    )

    @classmethod
    def poll(cls, context):
        if not pml_is_supported_editor(context):
            return False
        obj = context.active_object
        if obj is None or obj.active_material is None:
            return False

        return True

    def __init__(self):
        self._shader_node = None
        self._output_node = None
        self._node_tree = None

    def check_object_compatible(self, obj, reports=True):
        report = self.report if reports else lambda *args: None
        ma = obj.active_material

        if ma is None:
            report({'WARNING'}, f"{obj.name} has no active material")
            return False

        if ma.pml_layer_stack.is_initialized:
            report({'WARNING'}, f"{ma.name} already has an initialized"
                                " layer stack")
            return False

        if not hasattr(obj.data, "uv_layers") or len(obj.data.uv_layers) == 0:
            report({'WARNING'}, f"{obj.name} has no uv maps")
            return False

        return True

    def draw(self, context):
        obj = context.active_object
        mesh = obj.data
        layout = self.layout
        ma = obj.active_material

        if not ma.use_nodes:
            layout.label(
                text=f"Material {ma.name} does not use nodes. "
                     "Initializing the layer stack will enable nodes "
                     "for this material", icon='INFO')

        split = layout.split(factor=0.3)
        split.separator()

        col = split.column(align=True)
        col.label(text="Image Settings")
        col.prop(self, "image_width")
        col.prop(self, "image_height")
        col.prop(self, "keep_size_ratio")

        layout.prop_search(mesh.uv_layers, "active", mesh, "uv_layers",
                           text="UV Map")

        col = layout.column(align=True)
        col.prop(self, "base_layer_from_current")
        col.prop(self, "replace_connections")
        col.prop(self, "auto_connect_shader")

        row = layout.row()
        row.label(text="Channels")
        row.prop(self, "select", text="")
        flow = layout.grid_flow(columns=2, even_columns=True, align=True)
        for ch in self.channels:
            flow.prop(ch, "enabled", text=ch.name)

    def execute(self, context):
        obj = context.active_object
        ma = obj.active_material
        ma_layer_stack = ma.pml_layer_stack

        if not self.check_object_compatible(obj):
            return {'CANCELLED'}

        uv_map = self.get_uv_map(obj)
        if uv_map is None:
            return {'CANCELLED'}

        if not ma.use_nodes:
            ma.use_nodes = True

        node_group = None
        shader_node = self.get_shader_node(context)

        node_group = (shader_node.node_tree
                      if shader_node.bl_idname == "ShaderNodeGroup"
                      else None)

        ma_layer_stack.initialize(self.channels,
                                  image_width=self.image_width,
                                  image_height=self.image_height,
                                  use_float=self.use_float_images,
                                  uv_map=uv_map,
                                  node_group=node_group)

        assert ma_layer_stack.is_initialized

        ma_layer_stack.auto_connect_shader = self.auto_connect_shader

        if self.base_layer_from_current and context.space_data is not None:
            # Replace the base layers node tree with a copy of the
            # active material's node tree.
            base_layer = ma_layer_stack.base_layer
            replace_layer_material(context, base_layer, ma)
            self.enable_channels_from_layer(ma_layer_stack, base_layer)

        self._create_pml_node(ma_layer_stack)

        context.scene.tool_settings.image_paint.mode = 'IMAGE'

        return {'FINISHED'}

    def enable_channels_from_layer(self, layer_stack, layer) -> None:
        """Ensure all channels on layer are enabled on both 'layer' and
        'layer_stack'.
        """
        for ch in layer.channels:
            if ch.name in layer_stack.channels:
                ch.enabled = True
                layer_stack.set_channel_enabled(ch.name, True)

    def find_active_output_node(self, node_tree):
        """Returns the active material output node of a ShaderNodeTree"""

        for x in ('ALL', 'EEVEE', 'CYCLES'):
            output = node_tree.get_output_node(x)
            if output is not None:
                return output
        return None

    def get_uv_map(self, obj):
        """Gets the UV map given in the operator properties from obj.
           Returns None if the UV map cannot be found"""
        if self.uv_map:
            uv_map = obj.data.uv_layers.get(self.uv_map, None)
            if uv_map is None:
                self.report({'ERROR'}, f"Could not find UV Map {self.uv_map}")
                return None

        elif len(obj.data.uv_layers) == 0:
            self.report({'ERROR'}, f"{obj.name} has no UV Maps")
            return None
        else:
            uv_map = obj.data.uv_layers.active or obj.data.uv_layers[0]

        return uv_map

    def _create_pml_node(self, context):
        node_tree = self.get_node_tree(context)
        shader_node = self.get_shader_node(context)

        pml_node = node_tree.nodes.new("ShaderNodePMLStack")
        pml_node.location = shader_node.location
        pml_node.location.x -= (pml_node.width + 100)

        pml_node.connect_outputs(shader_node, replace=self.replace_connections)

        return pml_node

    def invoke(self, context, event):
        obj = context.active_object
        ma = obj.active_material

        if not self.check_object_compatible(obj):
            return {'CANCELLED'}

        ma_output_node = self.get_output_node(context)
        if ma_output_node is None:
            self.report('WARNING', f"{ma.name} has no active output node")
            return {'CANCELLED'}

        self.populate_channels_list(context)

        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def populate_channels_list(self, context) -> None:
        """Fills the 'channels' property
        Params:
            ma_output_node: A ShaderNodeOutputMaterial node
        """

        self.channels.clear()

        shader_node = self.get_shader_node(context)
        output_node = self.get_output_node(context)

        sockets = shader_node.inputs

        ref_sockets = {x.name: x for x in reference_inputs(shader_node)}

        if output_node is not None:
            # Also use the inputs from the output node
            sockets = it.chain(sockets, output_node.inputs)
            ref_sockets.update({x.name: x
                                for x in reference_inputs(output_node)})

        for _input in sockets:
            if _input.name in self.channels:
                # If multiple inputs have the same name then only use
                # the first
                continue

            if is_socket_supported(_input):
                ch = self.channels.add()
                ch.init_from_socket(_input)

                ref_soc = ref_sockets.get(ch.name)
                # Enable the channel if its socket is linked or its
                # default_value has been changed
                ch.enabled = (_input.is_linked
                              or ref_soc is None
                              or not ref_soc.default_values_equal(_input))

    def get_shader_node(self, context):
        shader_node = getattr(self, "_shader_node", None)
        if shader_node is not None:
            return shader_node

        ma_output_node = None

        if self.use_active_node:
            shader_node = getattr(context, "active_node", None)

            if shader_node is not None:
                if shader_node.bl_idname == "ShaderNodeOutputMaterial":
                    ma_output_node = shader_node
                else:
                    self._shader_node = shader_node
                    return shader_node

        if ma_output_node is None:
            ma_output_node = self.get_output_node(context)

        # The 'Surface' input of the material output node
        surface_in = ma_output_node.inputs[0]

        if not surface_in.is_linked:
            return None

        self._shader_node = surface_in.links[0].from_node
        return self._shader_node

    def get_output_node(self, context):
        output_node = getattr(self, "_output_node", None)
        if output_node is None:
            node_tree = self.get_node_tree(context)
            self._output_node = self.find_active_output_node(node_tree)
            return self._output_node
        return output_node

    def get_node_tree(self, context):
        node_tree = getattr(self, "_node_tree", None)
        if node_tree is None:
            ma = context.active_object.active_material
            self._node_tree = node_tree = ma.node_tree
        return node_tree


class PML_OT_delete_layer_stack(Operator):
    """Delete the Principled Material Layers layer stack for the
    active material.
    """

    bl_idname = "material.pml_delete_layer_stack"
    bl_label = "Delete Layer Stack"
    bl_description = ("Deletes the active Principled Material Layers "
                      "layer stack")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        if not layer_stack:
            self.report({'ERROR'}, "Active material does not have an "
                                   "initialized layer stack")
            return {'CANCELLED'}

        layer_stack.delete()
        return {'FINISHED'}


def register():
    bpy.utils.register_class(PML_OT_initialize_layer_stack)
    bpy.utils.register_class(PML_OT_delete_layer_stack)


def unregister():
    bpy.utils.unregister_class(PML_OT_initialize_layer_stack)
    bpy.utils.unregister_class(PML_OT_delete_layer_stack)
