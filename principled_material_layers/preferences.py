# SPDX-License-Identifier: GPL-2.0-or-later

from typing import Optional

import bpy

from bpy.types import AddonPreferences

from bpy.props import (BoolProperty,
                       EnumProperty,
                       FloatProperty)


if "_is_proper_addon" not in globals():
    _is_proper_addon: Optional[bool] = None


class PMLPreferences(AddonPreferences):
    bl_idname = __package__

    # Cached value for preferences.
    _prefs: AddonPreferences = None

    default_values = {
                      "debug": False,
                      "use_numpy": False,
                      "check_assets_compat": True,
                      "show_misc_ops": False,
                      "show_popover_panel": True,
                      "show_previews": True,
                      "layer_ui_scale": 2.0,
                      "layers_share_images": True,
                      "on_asset_import": 'SHOW_POPUP',
                      "use_tiled_storage_default": False,
                      "use_large_icons": False,
                      "use_undo_workaround": bpy.app.version < (3, 2, 0),
                      "use_op_based_ma_copy": bpy.app.version > (3, 1, 0)
                      }

    # N.B. Not editable from the UI
    debug_immediate_rebuild: BoolProperty(
        default=False
    )

    use_numpy: BoolProperty(
        name="Use NumPy",
        description=("Use numpy for pixel operations on images. Faster but "
                     "may cause a slight delay when first imported"),
        default=default_values["use_numpy"]
    )

    check_assets_compat: BoolProperty(
        name="Check Asset Compatibility",
        description=("Check the compatibility of material assets when they "
                     "are selected. This involves temporarily appending the "
                     "asset"),
        default=default_values["check_assets_compat"]
    )

    layer_ui_scale: FloatProperty(
        name="Layer UI Scale",
        description="The scale of the layer list in the UI",
        min=1.0, max=5.0,
        default=default_values["layer_ui_scale"]
    )

    layers_share_images: BoolProperty(
        name="Layers Share Images",
        description="Multiple layers may use different channels of the same "
                    "image to store their data. Reduces memory usage, but "
                    "changing the active layer becomes slower",
        default=default_values["layers_share_images"]
    )

    on_asset_import: EnumProperty(
        name="On Material Import",
        description="What should happen when 'Import as New Layer' or "
                    "'Replace Layer Material' are used in the Asset Browser",
        items=[
            ('SHOW_POPUP', "Show Pop-up", "Always show a pop-up"),
            ('DEFAULT_SETTINGS', "Use Default Settings",
             "Never show a pop-up and use the default settings. \n"
             "Default settings: Import all channels enabled on the layer "
             "stack or modified by the material and automatically enable "
             "channels on the layer stack"),
            ('REMEMBER', "Remember Settings",
             "Show a pop-up once and use the same settings for subsequent "
             "imports without showing a pop-up for the rest of the session"),
        ],
        default=default_values["on_asset_import"]
    )

    show_misc_ops: BoolProperty(
        name="Register Additional Operators",
        description="Enables the 'Bake Node Outputs' and 'Bake Node Inputs' "
                    "operators",
        default=default_values["show_misc_ops"]
    )

    show_popover_panel: BoolProperty(
        name="Enable Popover Panel",
        description="Shows a popover panel to quickly change layers in "
                    "Texture Paint mode's header",
        default=default_values["show_popover_panel"]
    )

    show_previews: BoolProperty(
        name="Show Layer Material Previews",
        description="Show previews for material layers in the UI",
        default=default_values["show_previews"]
    )

    use_large_icons: BoolProperty(
        name="Large Previews",
        description="Use large icons for material layer previews."
                    "May cause occasional crashes.",
        default=default_values["use_large_icons"],
    )

    use_op_based_ma_copy: BoolProperty(
        name="Use Op-Based Material Copy",
        description="Use operators to copy material node trees. Copies "
                    "materials better, but may cause crashes during 'Replace "
                    "Layer Material' in some Blender versions",
        default=default_values["use_op_based_ma_copy"]
    )

    use_undo_workaround: BoolProperty(
        name="Undo Bug Workaround",
        description=("Workaround for a bug where the canvas image in image "
                     "paint loses its data after a global undo/redo. May not "
                     "be needed for Blender 3.2+"),
        default=default_values["use_undo_workaround"]
    )

    use_tiled_storage_default: BoolProperty(
        name="Tiled Storage by Default",
        description=("Layer stacks will use tiled storage copies by "
                     "default. "
                     "Only needed if shader compilation fails due to "
                     "exceeding the fragment shader image unit limit."
                     "Copies the images used by the addon to a tiled image "
                     "to bypass the image limit. Significantly increases "
                     "memory usage."),
        default=default_values["use_tiled_storage_default"],
    )

    @classmethod
    def clear_cache(cls):
        """Clear the cached value of get_prefs."""
        cls._prefs = None

    def draw(self, _context):
        layout = self.layout

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(self, "show_previews")
        row.prop(self, "use_large_icons")

        col.prop(self, "show_popover_panel")
        col.prop(self, "check_assets_compat")
        col.prop(self, "use_numpy")
        col.prop(self, "layers_share_images")
        col.prop(self, "show_misc_ops")

        layout.prop(self, "on_asset_import")

        layout.separator()
        col = layout.column(align=True)
        col.prop(self, "use_undo_workaround")
        col.prop(self, "use_op_based_ma_copy")

    @classmethod
    def get_prefs(cls) -> AddonPreferences:
        """Gets the preferences for this addon. If the AddonPreferences
        instance cannot be found (e.g. if the addon was enabled by the
        command line) then a SimpleNamespace with the preferences
        initiailized to their default values is returned. Otherwise
        returns a normal AddonPreferences instance.
        """
        if cls._prefs is not None:
            return cls._prefs

        global _is_proper_addon

        addon = bpy.context.preferences.addons.get(__package__)

        if addon is None:
            # E.g. if addon is loaded from the command line
            addon = bpy.context.preferences.addons.new()
            addon.module = __package__

            _is_proper_addon = False
        elif _is_proper_addon is None:
            _is_proper_addon = True

        prefs = addon.preferences
        cls._prefs = prefs
        return prefs


def get_addon_preferences() -> PMLPreferences:
    """Gets the preferences for this addon. If the AddonPreferences
    instance cannot be found (e.g. if the addon was enabled by the
    command line) then a SimpleNamespace with the preferences
    initiailized to their default values is returned. Otherwise
    returns a normal AddonPreferences instance.
    """
    return PMLPreferences.get_prefs()


def running_as_proper_addon() -> bool:
    """Returns True if running as a normal addon (rather than specified
    on the command line).
    """
    if _is_proper_addon is None:
        get_addon_preferences()
    return bool(_is_proper_addon)


def register():
    PMLPreferences.clear_cache()
    bpy.utils.register_class(PMLPreferences)


def unregister():
    PMLPreferences.clear_cache()
    bpy.utils.unregister_class(PMLPreferences)
