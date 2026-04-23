# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import functools
import os
import typing
import warnings

from dataclasses import dataclass
from typing import Optional, Union

import bpy

from bpy.types import (AssetLibraryReference,
                       FileSelectEntry,
                       Material)
try:
    from bpy.types import AssetHandle
except ImportError:
    # Blender >= 5.0
    from bpy.types import AssetRepresentation as AssetHandle
asset_handle_name = str(AssetHandle).replace("<class '", "").replace("'>", "")



class AssetInfo(typing.NamedTuple):
    """NamedTuple class that should be used instead of raw FileSelectEntry,
    AssetRepresentation, etc to ensure compatibility across Blender versions.
    """
    file_entry: FileSelectEntry
    asset_rep: Optional["AssetRepresentation"]
    library: AssetLibraryReference

    @classmethod
    def from_active(cls, context) -> Optional[AssetInfo]:
        """Returns an AssetInfo instance from the active asset of
        context. Returns None if no active asset can be found.
        """
        if context.active_file is None:
            return None
        if hasattr(context, "asset") and context.asset is None:
            return None

        return cls(context.active_file,
                   getattr(context, "asset", None),
                   asset_library_ref(context))

    def import_material(self,
                        link: bool = False,
                        delayed: bool = False
                        ) -> Union[Material, DelayedMaterialImport]:
        """Imports this asset as a Material. Raises a TypeError if this
        asset is not a material.
        """
        if self.id_type != 'MATERIAL':
            raise TypeError("Not a material asset")
        return import_material_asset(self, link=link, delayed=delayed)

    append_material = functools.partialmethod(import_material, link=False)
    link_material = functools.partialmethod(import_material, link=True)

    @property
    def local_id(self) -> Optional[bpy.types.ID]:
        """The local id of the active asset or None."""
        if self.asset_rep is not None:
            # Blender 4.0+
            return self.asset_rep.local_id
        if hasattr(self.file_entry, "local_id"):
            return self.file_entry.local_id

        warnings.warn("Cannot find local_id prop for asset "
                      f"{self.file_entry.name}")
        return None

    @property
    def full_library_path(self) -> str:
        """The absolute path to this asset's blend file."""
        return full_library_path(self.file_entry,
                                 self.asset_rep,
                                 self.library)

    @property
    def id_type(self) -> str:
        """The type of the asset data-block."""
        if self.asset_rep is not None:
            return self.asset_rep.id_type

        return self.file_entry.id_type

    @property
    def relative_path(self) -> str:
        return self.file_entry.relative_path


def full_library_path(asset: Union[asset_handle_name,
                                   "bpy.types.FileSelectEntry"],
                      asset_rep: Optional["AssertionError"],
                      library: AssetLibraryReference) -> str:
    if asset_rep is not None:
        return asset_rep.full_library_path
    if isinstance(asset, AssetHandle):
        return full_library_path(asset.file_data, asset_rep, library)

    file_entry = asset
    return AssetHandle.get_full_library_path(file_entry,
                                             asset_library_ref=library)


def asset_local_id(context) -> Optional[bpy.types.ID]:
    """Returns the local id of the active asset or None."""
    if hasattr(context, "asset"):
        # Blender 4.0+
        take_from = context.asset
    elif hasattr(context, "active_file"):
        take_from = context.active_file
    else:
        return None
    return take_from.local_id if take_from is not None else None


def asset_library_ref(context) -> Optional[AssetLibraryReference]:
    if hasattr(context, "asset_library_reference"):
        # Blender 4.0+
        return context.asset_library_reference
    return context.asset_library_ref


def material_asset_active(context) -> bool:
    """Returns True if a material asset is active in the asset browser."""
    if hasattr(context, "asset"):
        # Blender 4.0+
        return (context.asset is not None
                and context.asset.id_type == 'MATERIAL')

    elif hasattr(context, "active_file"):
        return (context.active_file is not None
                and context.active_file.id_type == 'MATERIAL')
    return False


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


def import_active_material_asset(context,
                                 link: bool = False,
                                 delayed: bool = False
                                 ) -> Union[Material, DelayedMaterialImport]:
    asset_info = AssetInfo.from_active(context)
    if asset_info is None:
        raise ValueError("Cannot find active asset from context")
    return import_material_asset(asset_info, link=link, delayed=delayed)


append_active_material_asset = functools.partial(import_active_material_asset,
                                                 link=False)
link_active_material_asset = functools.partial(import_active_material_asset,
                                               link=True)


def import_material_asset(asset: AssetInfo,
                          link: bool,
                          delayed: bool = False
                          ) -> Union[Material, DelayedMaterialImport]:

    # Path to the blend file containing the asset
    library_path = asset.full_library_path
    file_data = asset.file_entry

    if delayed:
        return DelayedMaterialImport(file_data.name, library_path, link)

    return _import_material_asset_path(file_data.name, library_path, link)


link_material_asset = functools.partial(import_material_asset, link=True)
append_material_asset = functools.partial(import_material_asset, link=False)


def _import_material_asset_path(name: str,
                                library_path: str,
                                link: bool) -> Material:

    if not hasattr(bpy.data.libraries, "load"):
        ma = _import_material_asset_path2(name, library_path, link)
    else:
        with bpy.data.libraries.load(library_path,
                                     link=link,
                                     assets_only=True) as (_, data_to):
            data_to.materials = [name]
        ma = data_to.materials[0]
    if not link:
        ma.asset_clear()
    return ma


def _import_material_asset_path2(name: str,
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

    kwargs = {}
    if not link:
        kwargs["do_reuse_local_id"] = True

    # Link or append the material into the current blend file
    result = import_op(filepath=library_path,
                       directory=os.path.join(library_path, "Material"),
                       filename=name,
                       link=link, **kwargs)

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

    # Check both absolute and relative paths
    lib_paths = (bpy.path.relpath(library_path),
                 bpy.path.abspath(library_path))

    # Check that the material is linked from the given library path
    if material.library is None or material.library.filepath not in lib_paths:
        # Search through all materials in the blend file
        for ma in bpy.data.materials:
            if (ma.name == name and ma.library is not None
                    and ma.library.filepath in lib_paths):
                return ma
        return None

    return material
