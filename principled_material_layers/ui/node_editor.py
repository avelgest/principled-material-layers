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


class PML_PT_layer_stack_ne(layer_stack_PT_base, Panel):
    bl_label = "Material Painting"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Material Layers"
    bl_options = set()

    _can_init_from = {"ShaderNodeBsdfPrincipled",
                      "ShaderNodeGroup",
                      "ShaderNodeOutputMaterial", }

    @classmethod
    def poll(cls, context):
        layer_stack = get_layer_stack(context)
        if layer_stack is None:
            return False

        if layer_stack.is_initialized:
            return True
        if context.active_node is None:
            return False

        obj = context.active_object
        if obj is None or obj.active_material is None:
            return False

        space = context.space_data

        # Only allow initialization from certain nodes and only when
        # editing the active material's node tree.
        return (context.active_node.bl_idname in cls._can_init_from
                and obj.active_material.node_tree == space.edit_tree)

    def draw_uninitialized(self, context):
        layout = self.layout
        op_props = layout.operator("material.pml_initialize_layer_stack",
                                   text="Initialize")
        op_props.use_active_node = True


class PML_PT_layer_stack_channels_ne(layer_stack_channels_PT_base, Panel):
    bl_label = "Layer Stack Channels"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_parent_id = "PML_PT_layer_stack_ne"
    bl_options = {'DEFAULT_CLOSED'}


class PML_PT_active_layer_ne(active_layer_PT_base, Panel):
    bl_label = "Active Layer"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_parent_id = "PML_PT_layer_stack_ne"


class PML_PT_udim_layout_ne(UDIM_PT_base, Panel):
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_parent_id = "PML_PT_layer_stack_ne"


class PML_PT_layer_stack_settings_ne(settings_PT_base, Panel):
    bl_label = "Settings"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_parent_id = "PML_PT_layer_stack_ne"
    bl_options = {'DEFAULT_CLOSED'}


class PML_PT_debug_ne(debug_PT_base, Panel):
    bl_label = "Debug"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_parent_id = "PML_PT_layer_stack_ne"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        # TODO Move to more visible panel?
        col = layout.column(align=True)
        col.operator("node.pml_verify_layer_outputs")
        col.operator("node.pml_link_sockets_by_name")

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
        PML_PT_layer_stack_ne.bl_category = "Node"
    _register()
