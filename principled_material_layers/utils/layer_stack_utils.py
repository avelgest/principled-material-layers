# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from typing import Dict

import bpy


def get_layer_stack(context):
    """Returns the active layer stack of context (may be uninitialized)
    or None
    """
    obj = context.active_object
    if obj is None or obj.active_material is None:
        return None

    return obj.active_material.pml_layer_stack


# Dict of identifiers to indices in bpy.data.materials
_layer_stack_id_cache: Dict[str, int] = {}


def get_layer_stack_by_id(identifier: str):
    if not identifier:
        return None

    materials = bpy.data.materials

    cached_idx = _layer_stack_id_cache.get(identifier)
    if cached_idx is not None and cached_idx < len(materials):
        ma = materials[cached_idx]

        if ma.pml_layer_stack.identifier != identifier:
            _layer_stack_id_cache.pop(identifier)
        else:
            return ma.pml_layer_stack

    for idx, ma in enumerate(bpy.data.materials):
        if ma.pml_layer_stack.identifier == identifier:
            _layer_stack_id_cache[identifier] = idx
            return ma.pml_layer_stack

    return None


def get_layer_stack_from_ma(ma: bpy.types.Material):
    return ma.pml_layer_stack


def get_layer_stack_from_prop(prop: bpy.types.bpy_struct):
    if prop.id_data is None:
        return None
    return getattr(prop.id_data, "pml_layer_stack", None)


def is_layer_stack_initialized(context):
    return bool(get_layer_stack(context))
