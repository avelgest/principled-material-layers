# SPDX-License-Identifier: GPL-2.0-or-later

bl_info = {
    "name": "Principled Material Layers",
    "description": "Addon for painting node-based material layers",
    "author": "Avelgest",
    "version": (0, 5, 2),
    "blender": (3, 0, 1),
    "category": "Material",
    "location": ("View3D > Texture Paint Sidebar > Material Layers "
                 "or Shader Editor > Sidebar > Material Layers"),
    "warning": "Early beta version",
    "doc_url": ("https://github.com/avelgest/"
                "principled-material-painting/blob/main/docs.md")
}

if "import_utils" in locals():
    import importlib
    importlib.reload(locals()["import_utils"])
else:
    from . import import_utils


submodule_names = [
    "preferences",
    "utils",
    "on_load_manager",
    "blending",
    "channel",
    "material_layer",
    "image_manager",
    "pml_node_tree",
    "node_manager",
    "layer_stack",
    "pml_node",
    "bake",
    "asset_helper",
    "operators",
    "ui"]


if "_registered" not in locals():
    _registered = False


def register():
    global _registered
    _registered = True
    import_utils.register_all(submodules)


def unregister():
    global _registered
    _registered = False
    import_utils.unregister_all(submodules)


def import_submodules(exclude=None):
    if exclude:
        names = [x for x in submodule_names if x not in exclude]
    else:
        names = submodule_names
    submods = import_utils.import_all(names, __name__)
    globals().update(zip(names, submods))

    return submods


if _registered:
    # Should have already been added to globals in import_submodules.
    # Import again just in case.
    from . import preferences

if _registered and not preferences.running_as_proper_addon():
    # "Reload Scripts" seems not to unregister addons specified on the
    # command line so unregister then reregister the addon.
    unregister()
    submodules = import_submodules()
    register()

else:
    submodules = import_submodules()
