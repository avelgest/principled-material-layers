# SPDX-License-Identifier: GPL-2.0-or-later

import inspect
import traceback

from collections.abc import Callable, Container, Sequence
from warnings import warn

import bpy

from bpy.app.handlers import persistent
from bpy.types import PropertyGroup

from .utils.naming import unique_name_in


def pml_trusted_callback(fnc):
    OnLoadManager.add_trusted_callback(fnc)
    return fnc


def add_trusted_callback(fnc: Callable) -> None:
    OnLoadManager.add_trusted_callback(fnc)


class OnLoadManager(PropertyGroup):
    """Callbacks can be added to instances of this class that are
    saved with the blend file and are triggered after the blend file
    is loaded.
    Any callbacks must be decorated with pml_trusted_callback or the
    add_trusted_callback class method must be called before it can be
    used.
    """

    _trusted_functions = {}

    @classmethod
    def add_trusted_callback(cls, fnc: Callable) -> None:
        if not callable(fnc):
            raise TypeError("fnc is not callable")

        fnc_id = cls._function_identifier(fnc)
        cls._trusted_functions[fnc_id] = fnc

    @classmethod
    def is_trusted(cls, fnc_identifier: str) -> bool:

        return fnc_identifier in cls._trusted_functions

    @classmethod
    def _function_identifier(cls, fnc):
        return f"{fnc.__module__}.{fnc.__qualname__}"

    def initialize(self):
        pass

    def add_callback(self,
                     callback: Callable,
                     args: Sequence = None,
                     priority: int = 1) -> str:

        if inspect.ismethod(callback):
            cb_self = callback.__self__
            callback = callback.__func__

            if not isinstance(cb_self, bpy.types.bpy_struct):
                raise TypeError("Only methods from bpy_struct instances"
                                "are supported")
        else:
            cb_self = None

        cb_str = self._function_identifier(callback)

        if not self.is_trusted(cb_str):
            raise ValueError(
                "callback is not a trusted function. "
                "OnLoadManager.add_trusted_function should be called before "
                "it is added")

        callbacks = self.callbacks
        cb_name = unique_name_in(callbacks, 8)

        if args is not None:
            args = {str(i): self._storable_arg(x) for i, x in enumerate(args)}

        callbacks[cb_name] = {"callback": cb_str,
                              "self": self._storable_arg(cb_self),
                              "args": args,
                              "priority": int(priority)
                              }
        return cb_name

    def remove_callback(self, name: str) -> None:
        self.callbacks.pop(name, None)

    def clear(self) -> None:
        self.callbacks.clear()

    def call_all(self) -> None:
        callbacks = self.get("callbacks", {}).values()
        callbacks = sorted(callbacks, key=lambda x: -x.get("priority", 1))
        for cb_data in callbacks:
            try:
                self._call_callback(cb_data)
            except Exception as e:
                warn(f"{type(e).__name__} calling on_load callback: {e}")
                traceback.print_exc()

    def _call_callback(self, cb_data):
        cb_str = cb_data["callback"]
        if not self.is_trusted(cb_str):
            raise RuntimeError("Callable is not trusted")

        callback = self._trusted_functions[cb_str]
        # TODO handle cases where self cannot be resolved
        self_ = self._resolve_arg(cb_data["self"])

        stored_args = cb_data["args"]
        if stored_args is not None:
            args = [self._resolve_arg(stored_args[str(x)])
                    for x in len(stored_args)]
        else:
            args = []

        if self_ is not None:
            callback(self_, *args)
        else:
            callback(*args)

    @property
    def callbacks(self):
        callbacks = self.get("callbacks")
        if callbacks is None:
            self["callbacks"] = {}
            return self["callbacks"]
        return callbacks

    def _storable_arg(self, arg):
        if isinstance(arg, bpy.types.bpy_struct):

            if arg.id_data.is_embedded_data:
                return self._storable_arg_embedded_data(arg)

            # FIXME breaks if component in path renamed
            return {"id_data": arg.id_data,
                    "id_path": arg.path_from_id()}
        return arg

    def _storable_arg_embedded_data(self, arg: bpy.types.bpy_struct) -> dict:
        """Returns a storable value for arguments where
           arg.id_data.is_embedded_data == True
        """
        assert arg.id_data.is_embedded_data

        id_data = arg.id_data

        # Pointers to some embedded data become None on reload
        # so handle as special cases
        if isinstance(id_data, bpy.types.ShaderNodeTree):
            # Store pointer to material instead
            ma = next((ma for ma in bpy.data.materials
                       if ma.node_tree is id_data), None)

            if ma is not None:
                return {"id_data": ma,
                        "id_path": f"node_tree.{arg.path_from_id()}"}

            warn("Unable to find material for embedded ShaderNodeTree"
                 f"{arg.name}. Argument might not be resolved on reload")

        else:
            warn("Storage of object with ID data type "
                 f"{type(id_data).__name__} has not been tested. "
                 "Argument might not be resolved on reload")

        return {"id_data": arg.id_data,
                "id_path": arg.path_from_id()}

    def _resolve_arg(self, arg):
        if isinstance(arg, Container):
            if "id_data" in arg and "id_path" in arg:
                return arg["id_data"].path_resolve(arg["id_path"])
        return arg


@persistent
def _pml_load_post_handler(dummy):
    for ma in bpy.data.materials:
        if ma.pml_layer_stack.is_initialized:
            ma.pml_layer_stack.on_load_manager.call_all()


classes = (OnLoadManager,)

_register, _unregister = bpy.utils.register_classes_factory(classes)


def register():
    _register()

    bpy.app.handlers.load_post.append(_pml_load_post_handler)


def unregister():
    bpy.app.handlers.load_post.remove(_pml_load_post_handler)

    _unregister()
