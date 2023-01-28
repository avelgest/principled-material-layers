# SPDX-License-Identifier: GPL-2.0-or-later

import bpy
from bpy.types import Panel

from .common import (layer_stack_PT_base,
                     layer_stack_channels_PT_base,
                     active_layer_PT_base,
                     active_layer_channels_PT_base,
                     active_layer_node_mask_PT_base,
                     settings_PT_base,
                     UDIM_PT_base,
                     debug_PT_base
                     )

from ..preferences import get_addon_preferences, running_as_proper_addon

from ..utils.layer_stack_utils import get_layer_stack


class ImgPaintPanel(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = 'imagepaint'
    bl_category = "Material Layers"


class PML_PT_layer_stack_ip(ImgPaintPanel, layer_stack_PT_base):
    pass


class PML_PT_layer_stack_channels_ip(ImgPaintPanel,
                                     layer_stack_channels_PT_base):
    pass


class PML_PT_active_layer_ip(active_layer_PT_base, ImgPaintPanel):
    pass


class PML_PT_active_layer_node_mask_ip(active_layer_node_mask_PT_base,
                                       ImgPaintPanel):
    bl_parent_id = "PML_PT_active_layer_ip"


class PML_PT_active_layer_channels_ip(active_layer_channels_PT_base,
                                      ImgPaintPanel):
    bl_parent_id = "PML_PT_active_layer_ip"


class PML_PT_layer_stack_settings_ip(ImgPaintPanel, settings_PT_base):
    pass


class PML_PT_udim_layout_ip(ImgPaintPanel, UDIM_PT_base):
    pass


class PML_PT_debug_ip(ImgPaintPanel, debug_PT_base):
    pass


class PML_PT_layer_stack_popover_ip(ImgPaintPanel, layer_stack_PT_base):
    bl_context = ".imagepaint"
    bl_category = "Tool"
    bl_options = {'DEFAULT_CLOSED', 'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        if (not super().poll(context)
                or not get_addon_preferences().show_popover_panel):
            return False
        # Only show when there is an initailized layer_stack
        return bool(get_layer_stack(context))

    def draw(self, context):
        if not self.is_popover:
            return
        super().draw(context)

    def draw_initialized(self, context):
        layer_stack = get_layer_stack(context)

        self.draw_layers_list(self.layout, layer_stack, rows=4)


classes = (PML_PT_layer_stack_ip,
           PML_PT_active_layer_ip,
           PML_PT_layer_stack_channels_ip,
           PML_PT_active_layer_node_mask_ip,
           PML_PT_active_layer_channels_ip,
           PML_PT_udim_layout_ip,
           PML_PT_layer_stack_settings_ip,
           PML_PT_debug_ip,
           PML_PT_layer_stack_popover_ip
           )

_register, unregister = bpy.utils.register_classes_factory(classes)


def register():
    if not running_as_proper_addon():
        # New sidebar categories may not appear if not running as a
        # proper addon
        ImgPaintPanel.bl_category = "Tool"

    _register()
