# SPDX-License-Identifier: GPL-2.0-or-later

from ..import_utils import import_all

submodule_names = ("naming",
                   "ops",
                   "image",
                   "layer_stack_utils",
                   "nodes",
                   "node_tree",
                   "duplicate_node_tree",
                   "materials",
                   "temp_changes",
                   "node_tree_import"
                   )

_submodules = import_all(submodule_names, __name__)

globals().update(zip(submodule_names, _submodules))
