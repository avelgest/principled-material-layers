# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it
import os
import random

from typing import Optional

import bpy

from bpy.props import (BoolProperty,
                       CollectionProperty,
                       IntProperty,
                       StringProperty)
from bpy.types import Image, PropertyGroup

from .utils.image import can_pack_udims
from .utils.layer_stack_utils import get_layer_stack_from_prop
from .utils.ops import save_image


def save_tiled_image(image: Image) -> None:
    context = bpy.context.copy()
    context["edit_image"] = image

    bpy.ops.image.save(context)


def _pack_image(image: Image) -> None:
    if not image.is_dirty:
        image.pixels[0] = image.pixels[0]
    image.pack()


class UDIMTileProps(PropertyGroup):
    number: IntProperty(
        name="Number",
        description="The number of this UDIM tile"
    )
    label: StringProperty(
        name="Label",
        description="Optional label used to display this tile",
        get=lambda self: self.get("name", ""),
        set=lambda self, value: self.__setitem__("name", value)
    )
    width: IntProperty(
        name="Width",
        description="The width of this tile in pixels",
        subtype='PIXEL'
    )
    height: IntProperty(
        name="Height",
        description="The height of this tile in pixels",
        subtype='PIXEL'
    )
    is_float: BoolProperty(
        name="Use Float",
        description="Should this tile be a floating-point image"
    )
    has_alpha: BoolProperty(
        name="Has Alpha",
        description="Does this tile use an alpha channel",
        default=False
    )

    def initialize(self, number, width, height, is_float,
                   has_alpha=False, label=""):
        self.number = number
        self.width = width
        self.height = height
        self.is_float = is_float
        self.has_alpha = has_alpha
        self.label = label or str(self.number)

    def __setitem__(self, item, value):
        if item == "name" and not value:
            value = str(self.number)
        super().__setitem__(item, value)


class UDIMLayout(PropertyGroup):
    """Stores a tiled image layout that can be used to create UDIM
    images with the same tile layout.
    """
    image_dir: StringProperty(
        name="UDIM Folder",
        description="The folder in which to store UDIM tiles",
        subtype="DIR_PATH",
        default="//"
    )
    tiles: CollectionProperty(
        type=UDIMTileProps,
        name="Tiles"
    )
    active_index: IntProperty(
        name="Active Tile Index",
        description="The index of the currently active tile"
    )

    def initialize(self, image_dir: Optional[str] = None,
                   add_tile=True) -> None:
        """Initialize this UDIMLayout.
        Params:
            image_dir: If given should be a directory path sting where
                UDIM tiles should be saved.
            add_tile: Add the 1001 tile to this layout using the
                ImageManager's settings.
        """
        if image_dir is not None:
            self.image_dir = image_dir

        if add_tile:
            im = self._image_manager
            self.add_tile(1001, im.image_width, im.image_height, im.use_float)

    def delete(self) -> None:
        self.image_dir = ""
        self.tiles.clear()
        self.active_index = 0

    def create_tiled_image(self, name: str,
                           is_data: bool = True,
                           is_float: bool = False,
                           temp: bool = False) -> Image:
        """Creates a tiled Image with this UDIMLayout's layout.
        Params:
            name: The name of the new image.
            is_data: Use a non-color colorspace
            is_float: Use a floating-point image
            temp: Set the new image's filepath to be in Blender's
                temporary directory.
        """
        first_tile = self.first_tile
        if first_tile is None:
            raise RuntimeError("UDIMLayout has no tiles.")

        image = bpy.data.images.new(
                                   name=name,
                                   width=first_tile.width,
                                   height=first_tile.height,
                                   float_buffer=is_float,
                                   alpha=first_tile.has_alpha,
                                   is_data=is_data,
                                   tiled=True)

        self.update_tiles(image)

        # Pack the image if using Blender 3.3+
        if not temp and can_pack_udims():
            _pack_image(image)

        else:
            self._set_filename(image, temp=temp)

        return image

    def _set_filename(self, image: Image, temp: bool = False) -> None:
        if image.is_float:
            image.file_format = 'OPEN_EXR'
            file_ext = '.exr'
        else:
            image.file_format = 'PNG'
            file_ext = '.png'

        if not temp:
            filename = f"{image.name}.<UDIM>{file_ext}"
            image.filepath_raw = os.path.join(self.image_dir, filename)
        else:
            layer_stack_id = self._layer_stack.identifier
            rand_id = f"{random.randint(0, 2**32):06x}"

            filename = f"temp.{layer_stack_id}.{rand_id}.<UDIM>{file_ext}"
            image.filepath_raw = os.path.join(bpy.app.tempdir, filename)
            assert self.is_temp_image(image)

    def is_temp_image(self, image: Image) -> bool:
        """Checks if a tiled image is temporary, i.e. it's name starts
        with temp or it's files are in bpy.app.tempdir.
        """
        if image.source != 'TILED':
            raise ValueError("Expected a tiled image")

        filename = os.path.split(image.filepath_raw)[1]
        if filename.startswith("temp"):
            return True

        abs_path = bpy.path.abspath(image.filepath_raw)
        if bpy.path.is_subdir(abs_path, bpy.app.tempdir):
            return True
        return False

    def make_image_permanent(self, image: Image, dir_path: str,
                             save: bool = False) -> None:
        """Ensure image is either packed or located outside of
        Blender's temp directory.
        """
        if image.source != 'TILED':
            raise ValueError("Expected a tiled image")

        if not self.is_temp_image(image):
            return

        if dir_path:
            file_ext = ".exr" if image.is_float else ".png"

            filename = bpy.path.display_name_to_filepath(image.name)
            filename = f"{filename}.<UDIM>{file_ext}"

            image.filepath_raw = os.path.join(dir_path, filename)

        if can_pack_udims():
            _pack_image(image)

        elif save:
            save_image(image)

        assert not self.is_temp_image(image)

    def add_tile(self, number, width, height,
                 is_float=False, alpha=False, label="") -> UDIMTileProps:
        """Adds a new UDIM tile. Raises a ValueError if a tile with the
        same number already exists.
        """
        if self._get_tile(number) is not None:
            raise ValueError(f"Tile {number} already exists.")

        new_tile = self.tiles.add()
        new_tile.initialize(number, width, height,
                            is_float=is_float, has_alpha=alpha, label=label)
        return new_tile

    def remove_tile(self, number) -> None:
        """Removes the tile with number 'number' from this layout.
        Raises a KeyError if no such tile exists.
        """
        for idx, tile in enumerate(self.tiles):
            if tile.number == number:
                break
        else:
            raise KeyError(f"No tile with number {number}.")

        if idx == self.active_index:
            self.active_index = max(idx-1, 0)
        self.tiles.remove(idx)

    def _add_image_tile(self, image, tile) -> None:
        # Allow tile numbers to be used as tile argument
        if isinstance(tile, int):
            number = tile
            tile = self._get_tile(number)
            if tile is None:
                raise KeyError(f"No tile with number {number}.")

        context = bpy.context.copy()
        context["edit_image"] = image

        bpy.ops.image.tile_add(context, number=tile.number,
                               width=tile.width,
                               height=tile.height,
                               float=tile.is_float,
                               alpha=tile.has_alpha)

    def _remove_image_tile(self, image, number) -> None:
        for tile in image.tiles:
            if tile.number == number:
                break
        else:
            raise KeyError(f"image has not tile with number {number}")

        image.tiles.remove(tile)

    def _get_tile(self, number) -> Optional[UDIMTileProps]:
        for x in self.tiles:
            if x.number == number:
                return x
        return None

    def update_tiles(self, image) -> None:
        """Adds or removes tiles from image so that it matches the
        layout of this UDIMLayout.
        """
        img_tiles = {x.number for x in image.tiles}
        layout_tiles = {x.number for x in self.tiles}

        # Tile numbers in img_tiles that are not in layout_tiles
        for number in img_tiles.difference(layout_tiles):
            self._remove_image_tile(image, number)

        # Tile numbers in layout_tiles that are not in img_tiles
        for number in layout_tiles.difference(img_tiles):
            self._add_image_tile(image, number)

    @property
    def active_tile(self) -> Optional[UDIMTileProps]:
        """The tile that is currently selected in the UI."""
        if self.active_index < 0 or self.active_index >= len(self.tiles):
            return None
        return self.tiles[self.active_index]

    @active_tile.setter
    def active_tile(self, tile) -> None:
        for idx, other_tile in enumerate(self.tiles):
            if other_tile.number == tile.number:
                self.active_index = idx
                break
        else:
            raise KeyError(f"No tile with number {tile.number} found")

    @property
    def first_tile(self) -> Optional[UDIMTileProps]:
        """The first tile in this layout (the tile with the lowest
        number).
        """
        if not self.tiles:
            return None
        return min(self.tiles, key=lambda x: x.number)

    @property
    def _image_manager(self):
        layer_stack = get_layer_stack_from_prop(self)
        return None if layer_stack is None else layer_stack.image_manager

    @property
    def _layer_stack(self):
        return get_layer_stack_from_prop(self)

    @property
    def next_free_number(self) -> int:
        """The next available tile number greater than the number of
        first tile.
        """
        numbers = {x.number for x in self.tiles}
        if not numbers:
            return 1001

        counter = it.count(self.first_tile.number)
        return next(x for x in counter if x not in numbers)


classes = (UDIMTileProps, UDIMLayout)

register, unregister = bpy.utils.register_classes_factory(classes)
