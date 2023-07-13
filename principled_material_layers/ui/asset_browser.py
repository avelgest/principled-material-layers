# SPDX-License-Identifier: GPL-2.0-or-later

from typing import Optional

import bpy

from bpy.types import FileSelectEntry

from bpy_extras.asset_utils import SpaceAssetInfo

from ..preferences import get_addon_preferences

from ..utils.layer_stack_utils import get_layer_stack
from ..utils.materials import IsMaterialCompat, check_material_asset_compat


class PML_PT_asset_browser_panel(bpy.types.Panel):
    bl_label = "Material Painting"
    bl_space_type = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'
    bl_options = set()

    @classmethod
    def poll(cls, context):
        if not SpaceAssetInfo.is_asset_browser(context.space_data):
            return False

        active_file = context.active_file

        if active_file is None or active_file.id_type != 'MATERIAL':
            return False

        layer_stack = get_layer_stack(context)
        return layer_stack and layer_stack.active_layer is not None

    def draw(self, context):
        layout = self.layout

        prefs = get_addon_preferences()
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        if isinstance(prefs, bpy.types.AddonPreferences):
            layout.prop(prefs, "check_assets_compat", text="Check Compatible")

        if prefs.check_assets_compat:
            is_compat = self.check_compat(context, layer_stack)
            layout.label(text=is_compat.label_text,
                         icon=is_compat.label_icon)
        else:
            is_compat = None

        layout.label(text="Active Layer: "
                          f"{active_layer.name if active_layer else 'None'}")

        col = layout.column(align=True)
        col.enabled = (is_compat is None or bool(is_compat))
        col.operator("material.pml_new_layer_material_ab")
        col.operator("material.pml_replace_layer_material_ab")
        col.operator("material.pml_combine_material_ab")

    def check_compat(self, context, layer_stack) -> IsMaterialCompat:
        """Checks the compatibility of the active asset."""
        asset_file = self.get_active_ma_asset(context)

        if asset_file is None:
            return IsMaterialCompat("No active asset")

        return check_material_asset_compat(asset_file,
                                           context.asset_library_ref,
                                           layer_stack,
                                           delayed=True)

    def get_active_ma_asset(self, context) -> Optional[FileSelectEntry]:
        active_file = context.active_file

        if active_file is None or active_file.id_type != 'MATERIAL':
            return None

        return active_file


def asset_context_menu_func(self, context):
    if (not get_layer_stack(context)
            or not getattr(context, "active_file", None)
            or context.active_file.id_type != 'MATERIAL'):
        return

    layout = self.layout
    layout.separator()
    col = layout.column(align=True)
    col.operator_context = 'INVOKE_DEFAULT'
    col.operator("material.pml_new_layer_material_ab")
    col.operator("material.pml_replace_layer_material_ab")
    col.operator("material.pml_combine_material_ab")


def register():
    bpy.utils.register_class(PML_PT_asset_browser_panel)

    bpy.types.ASSETBROWSER_MT_context_menu.append(asset_context_menu_func)


def unregister():
    bpy.utils.unregister_class(PML_PT_asset_browser_panel)

    bpy.types.ASSETBROWSER_MT_context_menu.remove(asset_context_menu_func)
