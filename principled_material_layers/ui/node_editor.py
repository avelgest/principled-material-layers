# SPDX-License-Identifier: GPL-2.0-or-later

import bpy
from bpy.types import Panel

from .common import (layer_stack_PT_base,
                     layer_stack_channels_PT_base,
                     active_layer_PT_base,
                     settings_PT_base,
                     UDIM_PT_base,
                     debug_PT_base
                     )

from ..preferences import running_as_proper_addon
from ..utils.layer_stack_utils import get_layer_stack


class NodeEdPanel(Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Material Layers"

    @classmethod
    def poll(cls, context):
        shader_type = getattr(context.space_data, "shader_type", None)

        if shader_type != 'OBJECT':
            return False

        # True if there is an active and initialized layer stack
        return super().poll(context)


class PML_PT_layer_stack_ne(NodeEdPanel, layer_stack_PT_base):

    _can_init_from = {"ShaderNodeBsdfPrincipled",
                      "ShaderNodeGroup",
                      "ShaderNodeOutputMaterial", }

    @classmethod
    def poll(cls, context):
        space = context.space_data
        layer_stack = get_layer_stack(context)

        if getattr(space, "shader_type", None) != 'OBJECT':
            return False

        if layer_stack is None:
            return False

        if layer_stack.is_initialized:
            return True
        if context.active_node is None:
            return False

        obj = context.active_object
        if obj is None or obj.active_material is None:
            return False

        # Only allow initialization from certain nodes and only when
        # editing the active material's node tree.
        return (context.active_node.bl_idname in cls._can_init_from
                and obj.active_material.node_tree == space.edit_tree)

    def draw_uninitialized(self, context):
        layout = self.layout
        op_props = layout.operator("material.pml_initialize_layer_stack",
                                   text="Initialize")
        op_props.use_active_node = True

    def draw_initialized(self, context):
        super().draw_initialized(context)

        layout = self.layout

        layout.separator()
        layout.operator("node.pml_link_sockets_by_name")


class PML_PT_layer_stack_channels_ne(NodeEdPanel,
                                     layer_stack_channels_PT_base):
    pass


class PML_PT_active_layer_ne(NodeEdPanel, active_layer_PT_base):
    pass


class PML_PT_udim_layout_ne(NodeEdPanel, UDIM_PT_base):
    pass


class PML_PT_layer_stack_settings_ne(NodeEdPanel, settings_PT_base):
    pass


class PML_PT_debug_ne(NodeEdPanel, debug_PT_base):

    def draw(self, context):
        layout = self.layout

        layout.operator("node.pml_verify_layer_outputs")
        layout.separator()

        super().draw(context)


classes = (PML_PT_layer_stack_ne,
           PML_PT_active_layer_ne,
           PML_PT_layer_stack_channels_ne,
           PML_PT_udim_layout_ne,
           PML_PT_layer_stack_settings_ne,
           PML_PT_debug_ne
           )

_register, unregister = bpy.utils.register_classes_factory(classes)


def register():
    if not running_as_proper_addon():
        # New sidebar categories may not appear if not running as a
        # proper addon
        NodeEdPanel.bl_category = "Node"
    _register()
