# SPDX-License-Identifier: GPL-2.0-or-later

import inspect

from types import SimpleNamespace
from typing import Any, Optional, Union

import bpy

from bpy.types import AddonPreferences

from bpy.props import (BoolProperty,
                       FloatProperty)


class PMLPreferences(AddonPreferences):
    bl_idname = __package__

    # Cached value for preferences.
    # Will be either a PMLPreferences or SimpleNamespace instance.
    _prefs: Union[AddonPreferences, SimpleNamespace] = None

    # Preferences to use when PMLPreferences cannot be found
    _mock_prefs: Optional[SimpleNamespace] = None

    default_values = {
                      "debug": False,
                      "use_numpy": False,
                      "show_misc_ops": False,
                      "show_previews": True,
                      "layer_ui_scale": 2.0,
                      "layers_share_images": True,
                      "use_undo_workaround": bpy.app.version < (3, 2, 0),
                      "use_op_based_ma_copy": bpy.app.version > (3, 1, 0)
                      }

    # Differences in debug mode:
    #   - node_manager always rebuilds immediately rather than using a
    #     timer.
    debug: BoolProperty(
        name="Debug Mode",
        description="Enable debug mode",
        default=default_values["debug"]
    )

    use_numpy: BoolProperty(
        name="Use NumPy",
        description=("Use numpy for pixel operations on images. Faster but "
                     "may cause a slight delay when first imported"),
        default=default_values["use_numpy"]
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

    show_misc_ops: BoolProperty(
        name="Register Additional Operators",
        description="Enables the 'Bake Node Outputs' and 'Bake Node Inputs' "
                    "operators",
        default=default_values["show_misc_ops"]
    )

    show_previews: BoolProperty(
        name="Show Layer Material Previews",
        description="Show previews for material layers in the UI",
        default=default_values["show_previews"]
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

    @classmethod
    def clear_cache(cls):
        """Clear the cached value of get_prefs."""
        cls._prefs = None

    @classmethod
    def _init_mock_prefs(cls) -> SimpleNamespace:
        """Returns an SimpleNamespace containing the bpy properties of
        PMLPreferences as python variables initialized to their default
        values. For use when this addons preferences are missing from
        bpy.context (e.g. if the addon was enabled by the command line).

        Sets the _mock_prefs class variable to the return value.
        """
        mock_prefs = SimpleNamespace()

        for attr_name, prop in _get_annotations(cls).items():
            # Check the class variable 'default_values' first
            default = cls.default_values.get(attr_name)

            # TODO may need to use eval(prop) if all annotations are
            # stored as strings in later Python versions
            if default is None and hasattr(prop, "keywords"):
                default = prop.keywords.get("default", None)

            setattr(mock_prefs, attr_name, default)
        cls._mock_prefs = mock_prefs
        return mock_prefs

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        col.prop(self, "use_numpy")
        col.prop(self, "show_previews")
        col.prop(self, "layers_share_images")
        col.prop(self, "show_misc_ops")

        layout.separator()
        col = layout.column(align=True)
        col.prop(self, "debug")
        col.prop(self, "use_undo_workaround")
        col.prop(self, "use_op_based_ma_copy")

    @classmethod
    def get_prefs(cls) -> Union[AddonPreferences, SimpleNamespace]:
        """Gets the preferences for this addon. If the AddonPreferences
        instance cannot be found (e.g. if the addon was enabled by the
        command line) then a SimpleNamespace with the preferences
        initiailized to their default values is returned. Otherwise
        returns a normal AddonPreferences instance.
        """
        if cls._prefs is not None:
            return cls._prefs

        addon = bpy.context.preferences.addons.get(__package__)
        if addon is None:
            if not cls._mock_prefs:
                cls._init_mock_prefs()
            prefs = cls._mock_prefs
        else:
            prefs = addon.preferences

        cls._prefs = prefs
        return prefs


def _get_annotations(obj: Any) -> dict:
    if hasattr(inspect, "get_annotations"):
        return inspect.get_annotations(obj)
    if isinstance(obj, type):
        return getattr(obj.__dict__, "__annotations__", {})
    return getattr(obj, "__annotations__", {})


def get_addon_preferences() -> Union[PMLPreferences, SimpleNamespace]:
    """Gets the preferences for this addon. If the AddonPreferences
    instance cannot be found (e.g. if the addon was enabled by the
    command line) then a SimpleNamespace with the preferences
    initiailized to their default values is returned. Otherwise
    returns a normal AddonPreferences instance.
    """
    return PMLPreferences.get_prefs()


def running_as_proper_addon() -> bool:
    """Returns True if the addon has its preferences in
    bpy.context.addons"""
    return isinstance(get_addon_preferences(), AddonPreferences)


def register():
    PMLPreferences.clear_cache()
    bpy.utils.register_class(PMLPreferences)


def unregister():
    PMLPreferences.clear_cache()
    bpy.utils.unregister_class(PMLPreferences)
