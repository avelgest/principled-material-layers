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
            layout.prop(prefs, "check_assets_compat")

        if prefs.check_assets_compat:
            is_compat = self.check_compat(context, layer_stack)
            self.draw_is_compat(is_compat, layout)

        layout.label(text="Active Layer: "
                          f"{active_layer.name if active_layer else 'None'}")

        col = layout.column()
        col.enabled = bool(locals().get("is_compat", True))
        col.operator("material.pml_replace_layer_material_ab")

    def check_compat(self, context, layer_stack) -> IsMaterialCompat:
        """Checks the compatibility of the active asset."""
        asset_file = self.get_active_ma_asset(context)

        if asset_file is None:
            return IsMaterialCompat("No active asset")

        return check_material_asset_compat(asset_file,
                                           context.asset_library_ref,
                                           layer_stack,
                                           delayed=True)

    def draw_is_compat(self,
                       is_compat: IsMaterialCompat,
                       layout: bpy.types.UILayout) -> None:
        if is_compat:
            text = "Compatible: "
            if not is_compat.unmatched_channels:
                text = "Compatible: All Channels Found"
                icon = 'CHECKMARK'
            elif is_compat.unmatched_channels <= is_compat.matched_sockets:
                text = ("Mostly Compatible: {is_compat.unmatched_channels} "
                        "Channels Not Found")
                icon = 'CHECKMARK'
            else:
                text = ("Low Compatibility: {is_compat.unmatched_channels} "
                        "Channels Not Found")
                icon = 'INFO'
            layout.label(text=text, icon=icon)

        elif is_compat.in_progress:
            layout.label(text="Waiting for check to complete")
        else:
            layout.label(text=f"Incompatible: {is_compat.reason}.",
                         icon='ERROR')

    def get_active_ma_asset(self, context) -> Optional[FileSelectEntry]:
        active_file = context.active_file

        if active_file is None or active_file.id_type != 'MATERIAL':
            return None

        return active_file


def register():
    bpy.utils.register_class(PML_PT_asset_browser_panel)


def unregister():
    bpy.utils.unregister_class(PML_PT_asset_browser_panel)
