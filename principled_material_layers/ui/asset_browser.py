# SPDX-License-Identifier: GPL-2.0-or-later

import bpy

from bpy_extras.asset_utils import SpaceAssetInfo

from ..utils.layer_stack_utils import get_layer_stack


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
        layer_stack = get_layer_stack(context)
        layout.label(text=f"Active Layer: {layer_stack.active_layer.name}")
        layout.operator("material.pml_replace_layer_material_ab")


def register():
    bpy.utils.register_class(PML_PT_asset_browser_panel)


def unregister():
    bpy.utils.unregister_class(PML_PT_asset_browser_panel)
