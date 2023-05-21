# SPDX-License-Identifier: GPL-2.0-or-later

import bpy
from bpy.types import Panel

from .common import (layer_stack_PT_base,
                     layer_stack_channels_PT_base,
                     active_layer_PT_base,
                     active_layer_channels_PT_base,
                     active_layer_node_mask_PT_base,
                     active_layer_image_map_PT_base,
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


class PML_PT_active_layer_image_map_ip(active_layer_image_map_PT_base,
                                       ImgPaintPanel):
    bl_parent_id = "PML_PT_active_layer_ip"


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
        layout = self.layout
        layer_stack = get_layer_stack(context)

        self.draw_layers_list(layout, layer_stack, rows=4)

        self._draw_preview_menus(layout, layer_stack)

    def _draw_preview_menus(self, layout, layer_stack) -> None:
        preview_ch = layer_stack.preview_channel

        row = layout.row(align=True)
        row.context_pointer_set("pml_preview_channel",  preview_ch)

        if preview_ch is None:
            menu_text = "Preview"
        elif preview_ch.is_layer_channel:
            menu_text = f"{preview_ch.name} ({preview_ch.layer.name})"
        else:
            menu_text = preview_ch.name

        row.menu("PML_MT_set_preview_channel", text=menu_text)
        if preview_ch is None:
            return

        row.menu("PML_MT_set_preview_modifier",
                 text=row.enum_item_name(preview_ch, "preview_modifier",
                                         preview_ch.preview_modifier))
        row.operator("node.pml_clear_preview_channel", text="",
                     icon="X")


classes = (PML_PT_layer_stack_ip,
           PML_PT_active_layer_ip,
           PML_PT_layer_stack_channels_ip,
           PML_PT_active_layer_image_map_ip,
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
