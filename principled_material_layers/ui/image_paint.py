# SPDX-License-Identifier: GPL-2.0-or-later

import bpy
from bpy.types import Panel

from .common import (layer_stack_PT_base,
                     layer_stack_channels_PT_base,
                     active_layer_PT_base,
                     settings_PT_base,
                     debug_PT_base
                     )

from ..preferences import running_as_proper_addon


class PML_PT_layer_stack_ip(layer_stack_PT_base, Panel):
    bl_label = "Material Painting"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = 'imagepaint'
    bl_category = "Material Layers"
    bl_options = set()


class PML_PT_layer_stack_channels_ip(layer_stack_channels_PT_base, Panel):
    bl_label = "Layer Stack Channels"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = 'imagepaint'
    bl_parent_id = "PML_PT_layer_stack_ip"
    bl_options = {'DEFAULT_CLOSED'}


class PML_PT_active_layer_ip(active_layer_PT_base, Panel):
    bl_label = "Active Layer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = 'imagepaint'
    bl_parent_id = "PML_PT_layer_stack_ip"


class PML_PT_layer_stack_settings_ip(settings_PT_base, Panel):
    bl_label = "Settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = 'imagepaint'
    bl_parent_id = "PML_PT_layer_stack_ip"
    bl_options = {'DEFAULT_CLOSED'}


class PML_PT_debug_ip(debug_PT_base, Panel):
    bl_label = "Debug"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = 'imagepaint'
    bl_parent_id = "PML_PT_layer_stack_ip"
    bl_options = {'DEFAULT_CLOSED'}


classes = (PML_PT_layer_stack_ip,
           PML_PT_active_layer_ip,
           PML_PT_layer_stack_channels_ip,
           PML_PT_layer_stack_settings_ip,
           PML_PT_debug_ip
           )

_register, unregister = bpy.utils.register_classes_factory(classes)


def register():
    if not running_as_proper_addon():
        # New sidebar categories may not appear if not running as a
        # proper addon
        PML_PT_layer_stack_ip.bl_category = "Tool"

    _register()
