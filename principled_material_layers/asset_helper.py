# SPDX-License-Identifier: GPL-2.0-or-later

import os
import typing

from dataclasses import dataclass
from typing import Optional, Union

import bpy

from bpy.types import (AssetHandle,
                       AssetLibraryReference,
                       FileSelectEntry,
                       Material)


@dataclass
class DelayedMaterialImport:
    """Class for delayed material imports. The asset will not be
    linked/appended to the blend file until the import_material mthod
    is called.
    """
    name: str
    library_path: str
    link: bool
    _done: bool = False

    def __call__(self) -> Material:
        return self.import_material()

    def import_material(self) -> Material:
        """Immediately link/append the material asset to the blend file."""
        if self._done:
            raise RuntimeError("Import has already been performed.")
        self._done = True
        return _import_material_asset_path(self.name,
                                           self.library_path,
                                           self.link)


def append_material_asset(asset: Union[AssetHandle, FileSelectEntry],
                          library: AssetLibraryReference) -> Material:
    """Append a material asset to the blend file."""
    return import_material_asset(asset, library, False)


def link_material_asset(asset: Union[AssetHandle, FileSelectEntry],
                        library: AssetLibraryReference) -> Material:
    """Link a material asset to the blend file."""
    return import_material_asset(asset, library, True)


def delayed_append_material_asset(asset: Union[AssetHandle, FileSelectEntry],
                                  library: AssetLibraryReference
                                  ) -> DelayedMaterialImport:
    """Returns a DelayedMaterialImport, the import_material method can
    be used to append the material to the blend file.
    """
    return import_material_asset(asset, library, link=False, delayed=True)


def delayed_link_material_asset(asset: Union[AssetHandle, FileSelectEntry],
                                library: AssetLibraryReference
                                ) -> DelayedMaterialImport:
    """Returns a DelayedMaterialImport, the import_material method can
    be used to link the material to the blend file.
    """
    return import_material_asset(asset, library, link=True, delayed=True)


def import_material_asset(asset: Union[AssetHandle, FileSelectEntry],
                          library: AssetLibraryReference,
                          link: bool,
                          delayed: bool = False) -> Material:
    if isinstance(asset, FileSelectEntry):
        file_data = asset
    elif not hasattr(asset, "file_data"):
        raise NotImplementedError("No 'file_data' attribute on asset")
    else:
        file_data = asset.file_data

    # Path to the blend file containing the asset
    library_path = AssetHandle.get_full_library_path(file_data, library)

    if delayed:
        return DelayedMaterialImport(file_data.name, library_path, link)

    return _import_material_asset_path(file_data.name, library_path, link)


def _import_material_asset_path(name: str,
                                library_path: str,
                                link: bool) -> Material:
    import_op = bpy.ops.wm.link if link else bpy.ops.wm.append

    if link:
        existing = _get_linked_material(name, library_path)
        if existing is not None:
            return existing
    else:
        # The names of all local materials (will be used to find the
        # newly appended material later)
        existing = _local_material_names()

    # Link or append the material into the current blend file
    result = import_op(filepath=library_path,
                       directory=os.path.join(library_path, "Material"),
                       filename=name)

    if 'FINISHED' not in result:
        raise RuntimeError(f"Could not link asset '{name}' from "
                           f"'{library_path}'")

    # Find the newly added material
    if link:
        material = _get_linked_material(name, library_path)
        if material is None:
            raise RuntimeError("Could not find linked material "
                               f"'{name}' from '{library_path}'")
        return material

    # For append look for the new material by searching for a material
    # name that wasn't there before
    added = existing.symmetric_difference(_local_material_names())

    assert len(added) <= 1, "Expected only one new material"

    if not added:
        raise RuntimeError("Could not find appended material "
                           f"'{name}' from '{library_path}'")

    return bpy.data.materials[added.pop()]


def _local_material_names() -> typing.Set[str]:
    """Returns a set of the names of all the local materials in the
    blend file.
    """
    return {ma.name for ma in bpy.data.materials if ma.library is None}


def _get_linked_material(name: str, library_path: str) -> Optional[Material]:
    """Find a material linked from the library with path library_path
    Params:
        name: the simple name (not full_name) of the material to find
        library_path: the path of the library the material was linked from
    Returns:
        The material, or None if it could not be found
    """
    material = bpy.data.materials.get(name)
    if material is None:
        return None
    # Check that the material is linked from the given library path
    if material.library is None or material.library.filepath != library_path:
        # Search through all materials in the blend file
        for ma in bpy.data.materials:
            if (ma.name == name and material.library is not None
                    and material.library.filepath == library_path):
                return ma
        return None

    return material
