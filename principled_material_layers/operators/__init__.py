# SPDX-License-Identifier: GPL-2.0-or-later

from ..import_utils import import_all, register_all, unregister_all


submodule_names = ("initialize_layer_stack",
                   "layer_ops",
                   "node_ops",
                   "material_ops",
                   "bake_ops",
                   "udim_ops")

submodules = import_all(submodule_names, __name__)

globals().update(zip(submodule_names, submodules))


def register():
    register_all(submodules)


def unregister():
    unregister_all(submodules)
