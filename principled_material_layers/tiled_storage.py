# SPDX-License-Identifier: GPL-2.0-or-later

import os
import warnings

from typing import Collection, Set

import bpy

from bpy.props import PointerProperty
from bpy.types import Image

from .utils.image import save_image_copy
from .utils.layer_stack_utils import get_layer_stack_from_prop


class TiledStorage(bpy.types.PropertyGroup):
    """Store copies of images as the tiles of a UDIM image. Can be used
    if the fragment shader's texture limit is reached since the tiles
    of a UDIM use the same sampler.
    The image copies are stored on disk in Blender's temporary folder
    and are not saved with the .blend file.
    An instance's initialize method must be called before use.
    """
    udim_image: PointerProperty(
        type=bpy.types.Image,
        name="Tiled Image"
    )

    def __bool__(self):
        return self.is_initialized

    def __contains__(self, image: Image):
        return next((True for x in self.tiles.values() if x is image), False)

    def initialize(self, is_data) -> None:
        """Initialize this instance and set whether it is for sRGB or
        non-color images.
        """
        self["is_data"] = bool(is_data)

        layer_stack = self.layer_stack
        if layer_stack is None:
            raise RuntimeError("Cannot find layer stack.")

        self.udim_image = self._init_image()

        self["tiles"] = {}

    def _init_image(self) -> bpy.types.Image:
        layer_stack = self.layer_stack

        name_suffix = 'data' if self.is_data else 'srgb'
        name = f".pml_{layer_stack.identifier}_tiled_storage_{name_suffix}"

        image = bpy.data.images.new(name, 32, 32, alpha=False,
                                    float_buffer=self.is_data,
                                    is_data=self.is_data,
                                    tiled=True)
        filename = f"{name}.<UDIM>.exr"
        image.file_format = 'OPEN_EXR'
        image.filepath_raw = os.path.join(bpy.app.tempdir, filename)
        image.use_half_precision = True
        return image

    def delete(self) -> None:
        """Removes all tiles. Deleting all image copies.
        Does nothing if the instance has not been initialized.
        """
        if not self.is_initialized:
            return

        numbers = [int(x) for x in self.tiles.keys()]
        for num in numbers:
            # This deletes the file on disk as well
            self.remove_image_by_number(num)

        bpy.data.images.remove(self.udim_image)

    def add_image(self, image: Image) -> int:
        """Stores a copy of image as a UDIM tile. The image source must
        be 'GENERATED' or 'FILE'. Returns the tile number the image was
        added as.
        """
        if image.source not in ('GENERATED', 'FILE'):
            raise ValueError("image source must be in {'GENERATED', 'FILE'}")

        number: int = self._next_free_number

        self.tiles[str(number)] = image

        self._save_image_as_tile(image, number)

        return number

    def get_image_tile_num(self, image: Image) -> int:
        """Returns the tile number that image is saved as. Raises a
        ValueError if the image cannot be found.
        """
        for num_str, tile_image in self.tiles.items():
            if tile_image is image:
                return int(num_str)
        raise ValueError("image not found in tiles")

    def remove_image(self, image: Image) -> None:
        """Removes the tile containing a copy of image. Raises a
        ValueError if the image cannot be found."""
        number = self.get_image_tile_num(image)

        self.remove_image_by_number(number)

    def remove_image_by_number(self, number: int) -> None:
        """Deletes the UDIM tile given by number."""
        del self.tiles[str(number)]
        self._delete_tile_file(number)

        if number == 1001:
            # Keep a generated (float) image for the first tile
            # (prevents reloading images as 8-bit)
            self._gen_default_first_tile()
        else:
            # Remove the tile from the UDIM image
            tile = self.udim_image.tiles.get(number)
            if tile is not None:
                self.udim_image.tiles.remove(tile)

    def rewrite_image(self, image: Image) -> None:
        """Writes image to disk as a UDIM tile."""
        number = self.get_image_tile_num(image)

        self._save_image_as_tile(image, number)

    def update_from(self, images: Collection[Image]) -> None:
        """Adds copies of all images in images as UDIM tiles or updates
        the copies of any that have already been added. Any images with
        incompatible colorspaces are ignored. Also removes any tiles
        for which the images are no longer valid (e.g. if they have
        been deleted).
        """
        is_srgb = not self.is_data

        self._clear_invalid_tiles()

        # Images that already have a tile assigned
        existing = set(self.tiles.values())

        # Filter out images with incorrect colorspaces
        images = {x for x in images
                  if (x.colorspace_settings.name == "sRGB") == is_srgb}

        for img in images:
            if img in existing:
                try:
                    self.rewrite_image(img)
                except ValueError as e:
                    warnings.warn(str(e))
            else:
                self.add_image(img)
        if images:
            self.reload()

    def reload(self) -> None:
        """Reloads all tiles from disk."""
        self.udim_image.reload()

    def _clear_invalid_tiles(self) -> None:
        """Deletes all tiles that no longer have a valid image (e.g. if
        the image has been deleted).
        """
        tiles = self.tiles
        invalid = [num for num, img in tiles.items() if img is None]
        for num in invalid:
            self.remove_image_by_number(int(num))

    def _get_filepath(self, number: int) -> str:
        """Returns the filepath of the UDIM tile with the given number."""
        return self.udim_image.filepath_raw.replace("<UDIM>", str(number))

    def _delete_tile_file(self, number: int) -> None:
        filepath = self._get_filepath(number)

        # Do nothing if no file exists
        if not os.path.exists(filepath):
            return

        # Only delete files in Blender's temp dir
        if bpy.path.is_subdir(filepath, bpy.app.tempdir):
            try:
                os.remove(filepath)
            except IOError as e:
                warnings.warn(f"Could not delete {filepath}: {e}")
        else:
            warnings.warn(f"File {filepath} is not in this blend file's "
                          "temporary directory.")

    def _save_image_as_tile(self, image: Image, number: int) -> None:
        """Saves image to disk as the UDIM tile given by number."""
        # N.B. Need to save first tile as float, otherwise Blender will
        # load all tiles as int
        if self.is_srgb:
            fmt = 'PNG'
        else:
            fmt = ('OPEN_EXR'
                   if image.is_float or number == 1001
                   else 'PNG')
        save_image_copy(image,
                        self._get_filepath(number),
                        image_format=fmt)

    def _gen_default_first_tile(self, number=1001) -> None:
        context = bpy.context.copy()
        context["edit_image"] = self.udim_image

        op_kwargs = {"width": 32,
                     "height": 32,
                     "float": not self.is_srgb,
                     "alpha": False}

        tile = self.udim_image.tiles.get(number)
        if tile is None:
            if bpy.ops.image.tile_add.poll(context):
                bpy.ops.image.tile_add(context, number=number, **op_kwargs)
        else:
            self.udim_image.tiles.active = tile
            if bpy.ops.image.tile_fill.poll(context):
                bpy.ops.image.tile_fill(context, **op_kwargs)

    @property
    def is_data(self) -> bool:
        """True if this TiledStorage is for non-color data, False if
        this TiledStorage is for sRGB data."""
        return self["is_data"]

    @property
    def is_srgb(self) -> bool:
        """True if this TiledStorage is for sRGB data."""
        return not self["is_data"]

    @property
    def is_initialized(self) -> bool:
        return self.udim_image is not None

    @property
    def image_manager(self):
        return self.layer_stack.image_manager

    @property
    def layer_stack(self):
        return get_layer_stack_from_prop(self)

    @property
    def _next_free_number(self) -> int:
        """The number of the lowest available UDIM tile."""
        existing: Set[str] = set(self.tiles.keys())
        for x in range(1001, 2000):
            if str(x) not in existing:
                return x
        raise RuntimeError("Cannot find free tile between 1001 and 2000")

    @property
    def tiles(self):
        """Returns a map of UDIM tile numbers (as strings) to the image
        that the tile contains a copy of.
        """
        try:
            return self["tiles"]
        except KeyError:
            self["tiles"] = {}
            return self["tiles"]


def register():
    bpy.utils.register_class(TiledStorage)


def unregister():
    bpy.utils.unregister_class(TiledStorage)
