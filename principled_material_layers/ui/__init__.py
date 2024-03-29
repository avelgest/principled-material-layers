# SPDX-License-Identifier: GPL-2.0-or-later

from ..import_utils import import_all, register_all, unregister_all


submodule_names = ("menus",
                   "panels",
                   "ui_lists",

                   "image_paint",
                   "node_editor",
                   "asset_browser")

submodules = import_all(submodule_names, __name__)

globals().update(zip(submodule_names, submodules))


def register():
    register_all(submodules)


def unregister():
    unregister_all(submodules)
