# SPDX-License-Identifier: GPL-2.0-or-later

import importlib
import sys

from types import ModuleType
from typing import Collection, List


def import_all(module_names: Collection[str],
               package: str) -> List[ModuleType]:
    """Imports (or reimports) all submodules given in module_names
    and returns the result as a list

    Params:
        module_names: a sequence of strings
        package: the name of package from which to import the submodules

    Returns:
        A list containing the imported modules
    """

    imported = []
    for mod_name in module_names:
        full_name = f"{package}.{mod_name}"

        module = sys.modules.get(full_name)
        if module is None:
            module = importlib.import_module("." + mod_name, package)
        else:
            module = importlib.reload(module)

        imported.append(module)

    return imported


def register_all(modules: Collection[ModuleType]) -> None:
    """Calls each module's register function. Igonres any module
    without a register function.
    """

    for mod in modules:
        if hasattr(mod, "register") and callable(mod.register):
            mod.register()


def unregister_all(modules: Collection[ModuleType]) -> None:
    """Calls each module's unregister function. Igonres any module
    without an unregister function.
    """

    for mod in reversed(modules):
        if hasattr(mod, "unregister") and callable(mod.unregister):
            mod.unregister()
