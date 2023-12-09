# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import os
import re
import struct
import warnings

from array import array
from contextlib import ExitStack
from random import randint
from typing import Any, Dict, Optional, Tuple, Union

import bpy
from bpy.types import Image

from .. import preferences

from .temp_changes import TempChanges

# 1.0 as bytes (used in _copy_image_channel_to_rgb_no_numpy)
_FLOAT_ONE_BYTES = struct.pack("f", 1.0)


def _use_numpy() -> bool:
    """Whether or not these functions should use numpy"""
    return preferences.get_addon_preferences().use_numpy


_CAN_PACK_UDIMS = bpy.app.version >= (3, 3)


def can_pack_udims() -> bool:
    """Returns True if this version of Blender supports packing tiled
    images in the blend file.
    """
    return _CAN_PACK_UDIMS


class SplitChannelImageRGB:

    unallocated_value = None

    # Channel indices to attribute names
    _channel_names = {0: "r", 1: "g", 2: "b"}

    def __init__(self, image):
        # Use image name to get image from bpy.data.images
        # N.B. Prevents crashes but breaks if image is renamed
        self._image_name = image.name

        self.r = self.unallocated_value
        self.g = self.unallocated_value
        self.b = self.unallocated_value

    def __getitem__(self, key):
        attr_name = self._channel_names.get(key)
        if attr_name is not None:
            return getattr(self, attr_name)
        if hasattr(super(), "__getitem__"):
            return super().__getitem__(key)
        raise KeyError(f"{key} not found")

    def __setitem__(self, key, value):
        attr_name = self._channel_names.get(key)
        if attr_name is not None:
            setattr(self, attr_name, value)
        elif hasattr(super(), "__setitem__"):
            super().__setitem__(key, value)
        else:
            raise KeyError(f"{key} not found")

    def allocate_to(self, value: Any, ch: Union[int, str] = -1) -> None:
        if ch == -1:
            self.r = self.g = self.b = value
        else:
            self[ch] = value

    def allocate_all_to(self, value: Any) -> None:
        self.allocate_to(value, -1)

    def allocate_single_to(self, value: Any, ch: Union[int, str]) -> None:
        self.allocate_to(value, ch)

    def deallocate(self, ch: Union[int, str] = -1) -> None:
        if ch == -1:
            self.r = self.g = self.b = self.unallocated_value
        else:
            self[ch] = self.unallocated_value

    def deallocate_all(self) -> None:
        self.deallocate(-1)

    def deallocate_single(self, ch: Union[int, str]) -> None:
        self.deallocate(ch)

    def channel_allocated(self, ch: Union[int, str]) -> bool:
        if ch == -1:
            return not self.is_empty
        return self[ch] != self.unallocated_value

    @property
    def channel_contents(self) -> Tuple[Any, Any, Any]:
        return (self.r, self.g, self.b)

    @property
    def image(self) -> Image:
        try:
            return bpy.data.images[self._image_name]
        except KeyError as e:
            raise KeyError(f"Cannot find image named {self._image_name}. The "
                           "image may have be renamed without using the name "
                           "property of this SplitChannelImageRGB instance"
                           ) from e

    @property
    def image_name(self) -> str:
        return self.name

    @image_name.setter
    def image_name(self, name: str):
        self.name = name

    @property
    def name(self) -> str:
        return self._image_name

    @name.setter
    def name(self, name: str):
        image = self.image
        image.name = name
        # Name may be different from argument
        self._image_name = image.name

    @property
    def is_data(self) -> bool:
        return self.image.colorspace_settings.is_data

    @property
    def is_float(self) -> bool:
        return self.image.is_float

    @property
    def size(self) -> Tuple[int, int]:
        return tuple(self.image.size)

    @property
    def width(self) -> int:
        return self.image.size[0]

    @property
    def height(self) -> int:
        return self.image.size[1]

    def get_unused_channel(self) -> Optional[int]:
        for idx, contents in enumerate(self.channel_contents):
            if not contents:
                return idx

        return None

    @property
    def num_unused(self) -> int:
        return (not self.r) + (not self.g) + (not self.b)

    @property
    def is_empty(self) -> bool:
        return not (self.r or self.g or self.b)

    @property
    def is_full(self) -> bool:
        return self.r and self.g and self.b

    @property
    def is_shared(self) -> bool:
        return not (self.r == self.g == self.b)


def save_image_copy(image: Image, filepath: str,
                    image_format: str = 'AUTO',
                    float_bit_depth: int = 16,
                    settings: Optional[Dict[str, Any]] = None) -> None:
    """Saves image to disk using the given settings without altering
    its filepath.
    Params:
        image: A bpy.types.Image. The image will not be modified.
        filepath: Where to save the file to.
        image_format: The file format to save as. One of the enum used
            by Image.file_format. May also be 'AUTO' which uses
            OPEN_EXR for float images or PNG otherwise.
        float_bit_depth: The bit depth to use for saving float images.
            Should be in {16, 32}
        settings: Dict of names to values that can be set on an
            ImageFormatSettings instance. May be None.
    """

    if image_format == 'AUTO':
        image_format = 'OPEN_EXR' if image.is_float else 'PNG'

    scene = bpy.context.scene
    bit_depth = float_bit_depth if image.is_float else 8
    color_mode = 'RGBA' if has_alpha(image) else 'RGB'

    if "OPEN_EXR" in image_format and bit_depth == 8:
        bit_depth = 16

    with ExitStack() as exit_stack:

        # File format settings
        image_settings = exit_stack.enter_context(
                            TempChanges(scene.render.image_settings))

        image_settings.file_format = image_format
        image_settings.color_depth = str(bit_depth)
        image_settings.color_management = 'OVERRIDE'
        image_settings.color_mode = color_mode
        image_settings.compression = 15
        image_settings.use_preview = False
        if hasattr(image_settings, "use_zbuffer"):
            image_settings.use_zbuffer = False

        if settings:
            for k, v in settings:
                setattr(image_settings, k, v)

        display_settings = exit_stack.enter_context(
                            TempChanges(image_settings.display_settings))
        display_settings.display_device = 'sRGB'

        # Colorspace settings (for OPEN_EXR etc)
        cs_settings = exit_stack.enter_context(
                        TempChanges(image_settings.linear_colorspace_settings))

        if "OPEN_EXR" in image_format:
            if bpy.app.version > (4,):
                cs_settings.name = "Linear Rec.709"
            else:
                cs_settings.name = "Linear"
        else:
            cs_settings.name = image.colorspace_settings.name

        # Color settings (for PNG, TIFF etc)
        view_settings = exit_stack.enter_context(
                            TempChanges(image_settings.view_settings))

        view_settings.exposure = 0.0
        view_settings.gamma = 1.0
        view_settings.look = 'None'
        view_settings.use_curve_mapping = False
        view_settings.view_transform = 'Raw'

        image.save_render(filepath)


def get_image_pixels(image: Image) -> Union[array, "numpy.ndarray"]:
    img_size = len(image.pixels)

    if _use_numpy():
        import numpy as np
        px_array = np.empty(img_size, dtype=np.float32)
    else:
        px_array = array('f', [0.0])*img_size

    image.pixels.foreach_get(px_array)
    return px_array


def clear_channel(image: Image, ch: int) -> None:
    """Zeros the specified channel of image."""
    if ch < 0 or ch > 3:
        raise ValueError("Expected ch to be between 0 and 3.")

    pixels = get_image_pixels(image)
    if _use_numpy():
        pixels[ch::image.channels] = 0.0
    else:
        zeros = bytes(len(pixels) * 4 // image.channels)

        with memoryview(pixels) as mem_view:
            mem_view[ch::image.channels] = memoryview(zeros).cast('f')

    image.pixels.foreach_set(pixels)


def has_alpha(image: Image) -> bool:
    """Returns True if the image has an alpha channel."""
    return image.depth in (32, 64, 128)


def create_image_copy(image: Image) -> Image:
    """Creates a copy of image with a copy of image's pixel data."""

    supported = ('GENERATED', 'FILE', 'TILED')

    if image.source not in supported:
        raise ValueError(f"image source must be in {set(supported)}.")

    img_copy = bpy.data.images.new(f"{image.name}.copy",
                                   image.size[0], image.size[1],
                                   alpha=has_alpha(image),
                                   float_buffer=image.is_float,
                                   is_data=image.colorspace_settings.is_data,
                                   tiled=image.source == 'TILED')
    try:
        copy_image(image, img_copy)
    except Exception as e:
        bpy.data.remove(img_copy)
        raise e
    return img_copy


def delete_image_and_files(image: Image, tempdir_only=True) -> None:
    """Deletes image and the file(s) in it's filepath (for 'FILE' or
    'TILED' images). If tempdir_only is True then files are only
    deleted if they are in Blender's temporary folder.
    """
    filepath = bpy.path.abspath(image.filepath_raw)

    if filepath and (not tempdir_only
                     or bpy.path.is_subdir(filepath, bpy.app.tempdir)):
        if image.source == 'TILED':
            delete_udim_files(image)
        elif os.path.exists(image.filepath):
            try:
                os.remove(filepath)
            except IOError as e:
                warnings.warn(f"Could not delete file {filepath}: {e}")
    bpy.data.images.remove(image)


def delete_udim_files(image: Image) -> None:
    """Deletes any files used by a tiled image."""
    if not image.source == 'TILED':
        raise ValueError("Expected a tiled image.")

    folder, filename = os.path.split(image.filepath_raw)
    if not folder:
        folder = "."

    if "<UDIM>" not in filename:
        return

    dir_files = next(os.walk(folder))[2]

    split_fn = [re.escape(x) for x in filename.split("<UDIM>")]
    filename_re = fr"{split_fn[0]}\d{{4}}{split_fn[-1]}"

    to_delete = [os.path.join(folder, x) for x in dir_files
                 if re.search(filename_re, x, flags=re.A | re.I)]

    for filepath in to_delete:
        try:
            os.remove(filepath)
        except OSError as e:
            warnings.warn(f"Could not delete UDIM file: {e}")


def _copy_tiled_image(from_img: Image, to_img: Image) -> None:
    filename = f"pml_copy_tiled_{randint(0, 2**32):08x}.<UDIM>.exr"
    filepath = os.path.join(bpy.app.tempdir, filename)

    save_image_copy(from_img, filepath, 'AUTO', float_bit_depth=32)

    to_img.filepath = filepath
    to_img.colorspace_settings.name = from_img.colorspace_settings.name


def copy_image(from_img: Image, to_img: Image) -> None:
    """Copies an image's pixel data to another image. For tiled images
    this creates image files in Blender's temp folder, these can be
    deleted using the delete_udim_files function.
    """

    if from_img.source == 'TILED' and to_img.source == 'TILED':
        _copy_tiled_image(from_img, to_img)
        return

    if (from_img.size[0] != to_img.size[0]
            or from_img.size[1] != to_img.size[0]):
        raise ValueError("Images must have the same size.")

    img_size = len(from_img.pixels)

    if len(to_img.pixels) != img_size:
        raise ValueError("Image pixel data must be the same length.")

    if _use_numpy():
        import numpy as np
        px_array = np.empty(img_size, dtype=np.float32)
    else:
        px_array = array('f', [0.0])*img_size

    from_img.pixels.foreach_get(px_array)
    to_img.pixels.foreach_set(px_array)

    to_img.update()


def copy_image_channel(from_img: Image, from_ch: int,
                       to_img: Image, to_ch: int) -> None:
    """Copies a single channel from one image to a single channel of
    another image of the same size.
    """

    if to_img is from_img:
        copy_same_image_channel(from_img, from_ch, to_ch)
        return

    if from_ch < 0 or from_ch > 3:
        raise ValueError("from_ch must be between 0 and 3")
    if to_ch < 0 or to_ch > 3:
        raise ValueError("to_ch must be between 0 and 3")

    if (from_img.size[0] != to_img.size[0]
            or from_img.size[1] != to_img.size[1]):
        raise ValueError("Images must have the same size")

    # TODO support different number of channels for to_img
    assert from_img.channels == to_img.channels

    img_size = len(from_img.pixels)
    n_ch = from_img.channels

    if _use_numpy():
        import numpy as np
        from_px_array = np.empty(img_size, dtype=np.float32)
        to_px_array = np.empty(img_size, dtype=np.float32)

    else:
        # NB creation of arrays is relatively slow compared to numpy
        from_px_array = array('f', [0.0])*img_size
        to_px_array = array('f', from_px_array)

    from_img.pixels.foreach_get(from_px_array)
    to_img.pixels.foreach_get(to_px_array)

    to_px_array[to_ch::n_ch] = from_px_array[from_ch::n_ch]

    to_img.pixels.foreach_set(to_px_array)
    to_img.update()


def copy_same_image_channel(img: Image, from_ch: int, to_ch: int) -> None:
    """Copies an image channel to a different channel on the same image"""

    if from_ch == to_ch:
        return

    img_size = len(img.pixels)

    assert img.channels == img_size // (img_size[0] * img.size[1])

    if _use_numpy():
        import numpy as np
        px_array = np.empty(img_size, dtype=np.float32)
    else:
        px_array = array('f', [0.0])*img_size

    img.pixels.foreach_get(px_array)
    px_array[to_ch::img.channels] = px_array[from_ch::img.channels]
    img.pixels.foreach_set(px_array)

    img.update()


def copy_image_channel_to_rgb(from_img: Image, from_ch: int,
                              to_img: Image, copy_alpha=True) -> None:
    """Copies a single channel from from_img to each of the rgb channels
    of to_img. If copy_alpha is True then the alpha channel is also copied
    to to_img, otherwise the to_img's alpha is set to fully opaque.
    """

    assert from_img.channels == to_img.channels

    if (from_img.size[0] != to_img.size[0]
            or from_img.size[1] != to_img.size[1]
            or len(to_img.pixels) != len(from_img.pixels)):

        raise ValueError("Images must have the same size")

    if _use_numpy():
        _copy_image_channel_to_rgb_numpy(from_img, from_ch, to_img,
                                         copy_alpha)
    else:
        _copy_image_channel_to_rgb_no_numpy(from_img, from_ch, to_img,
                                            copy_alpha)

    to_img.update()


def _copy_image_channel_to_rgb_numpy(from_img, from_ch, to_img,
                                     copy_alpha=True):
    import numpy as np

    img_size = len(from_img.pixels)
    n_ch = from_img.channels

    px_array = np.empty(img_size, dtype=np.float32)
    from_img.pixels.foreach_get(px_array)

    ch_data = px_array[from_ch::n_ch]

    # Copy from_ch to the other channels
    for i in range(3):
        if i != from_ch:
            px_array[i::n_ch] = ch_data

    if not copy_alpha and n_ch == 4:
        # Set all alpha values to 1.0
        px_array[4::4] = 1.0

    to_img.pixels.foreach_set(px_array)


def _copy_image_channel_to_rgb_no_numpy(from_img, from_ch, to_img,
                                        copy_alpha=True):

    img_size = len(from_img.pixels)
    n_ch = from_img.channels

    px_array = array('f', [0.0]) * img_size
    from_img.pixels.foreach_get(px_array)

    # Copy from_ch to the other channels
    mem_view = memoryview(px_array)
    ch_data = mem_view[from_ch::n_ch]
    for i in range(3):
        if i != from_ch:
            mem_view[i::n_ch] = ch_data

    if not copy_alpha and n_ch == 4:
        # Set all alpha values to 1.0
        ones = _FLOAT_ONE_BYTES * (img_size // 4)
        mem_view[4::4] = memoryview(ones).cast("f")

    to_img.pixels.foreach_set(px_array)
