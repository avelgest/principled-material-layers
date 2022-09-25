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


class ImgPaintPanel(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = 'imagepaint'
    bl_category = "Material Layers"


class PML_PT_layer_stack_ip(layer_stack_PT_base, ImgPaintPanel):
    pass


class PML_PT_layer_stack_channels_ip(layer_stack_channels_PT_base,
                                     ImgPaintPanel):
    pass


class PML_PT_active_layer_ip(active_layer_PT_base, ImgPaintPanel):
    pass


class PML_PT_layer_stack_settings_ip(settings_PT_base, ImgPaintPanel):
    pass


class PML_PT_udim_layout_ip(UDIM_PT_base, ImgPaintPanel):
    pass


class PML_PT_debug_ip(debug_PT_base, ImgPaintPanel):
    pass


classes = (PML_PT_layer_stack_ip,
           PML_PT_active_layer_ip,
           PML_PT_layer_stack_channels_ip,
           PML_PT_udim_layout_ip,
           PML_PT_layer_stack_settings_ip,
           PML_PT_debug_ip
           )

_register, unregister = bpy.utils.register_classes_factory(classes)


def register():
    if not running_as_proper_addon():
        # New sidebar categories may not appear if not running as a
        # proper addon
        ImgPaintPanel.bl_category = "Tool"

    _register()
