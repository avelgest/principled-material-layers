# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it
import os

from typing import Optional

import bpy

from bpy.props import (BoolProperty,
                       CollectionProperty,
                       IntProperty,
                       StringProperty)
from bpy.types import Image, PropertyGroup

from .utils.layer_stack_utils import get_layer_stack_from_prop


def save_tiled_image(image: Image) -> None:
    context = bpy.context.copy()
    context["edit_image"] = image

    bpy.ops.image.save(context)


class UDIMTileProps(PropertyGroup):
    number: IntProperty(
        name="Number",
        description="The number of this UDIM tile"
    )
    label: StringProperty(
        name="Label",
        get=lambda self: self.get("name", ""),
        set=lambda self, value: self.__setitem__("name", value)
    )
    width: IntProperty(
        name="Width",
    )
    height: IntProperty(
        name="Height",
    )
    is_float: BoolProperty(
        name="Use Float"
    )
    has_alpha: BoolProperty(
        name="Has Alpha",
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


class UDIMLayout(PropertyGroup):
    image_dir: StringProperty(
        name="UDIM Folder",
        subtype="DIR_PATH",
        default="//"
    )
    tiles: CollectionProperty(
        type=UDIMTileProps,
        name="Tiles"
    )
    active_index: IntProperty(
        name="Active Tile Index"
    )

    def initialize(self, image_dir: Optional[str] = None,
                   add_tile=True) -> None:
        if image_dir is not None:
            self.image_dir = image_dir

        if add_tile:
            im = self._image_manager
            self.add_tile(1001, im.image_width, im.image_height, im.use_float)

    def delete(self) -> None:
        self.image_dir = ""
        self.tiles.clear()
        self.active_index = 0

    def create_tiled_image(self, name, is_data=True) -> Image:
        first_tile = self.first_tile
        if first_tile is None:
            raise RuntimeError("UDIMLayout has no tiles.")

        image = bpy.data.images.new(
                                   name=name,
                                   width=first_tile.width,
                                   height=first_tile.height,
                                   float_buffer=first_tile.is_float,
                                   alpha=first_tile.has_alpha,
                                   is_data=is_data,
                                   tiled=True)
        if image.is_float:
            image.file_format = 'OPEN_EXR'
            file_ext = '.exr'
        else:
            image.file_format = 'PNG'
            file_ext = '.png'

        filename = f"{image.name}.<UDIM>{file_ext}"
        image.filepath_raw = os.path.join(self.image_dir, filename)

        self.update_tiles(image)
        return image

    def add_tile(self, number, width, height,
                 is_float=False, alpha=False, label="") -> UDIMTileProps:
        if self._get_tile(number) is not None:
            raise ValueError(f"Tile {number} already exists.")

        new_tile = self.tiles.add()
        new_tile.initialize(number, width, height,
                            is_float=is_float, has_alpha=alpha, label=label)
        return new_tile

    def remove_tile(self, number) -> None:
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
        layout of the UDIMLayout.
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
        if not self.tiles:
            return None
        return min(self.tiles, key=lambda x: x.number)

    @property
    def _image_manager(self):
        layer_stack = get_layer_stack_from_prop(self)
        return None if layer_stack is None else layer_stack.image_manager

    @property
    def next_free_number(self) -> int:
        numbers = {x.number for x in self.tiles}
        counter = it.count(self.first_tile.number)

        while True:
            num = next(counter)
            if num not in numbers:
                return num


classes = (UDIMTileProps, UDIMLayout)

register, unregister = bpy.utils.register_classes_factory(classes)
