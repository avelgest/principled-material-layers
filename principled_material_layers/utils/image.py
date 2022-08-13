# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import struct

from array import array
from typing import Any, Optional, Tuple, Union

from bpy.types import Image

from ..preferences import get_addon_preferences

# 1.0 as bytes (used in _copy_image_channel_to_rgb_no_numpy)
_FLOAT_ONE_BYTES = struct.pack("f", 1.0)


def _use_numpy() -> bool:
    """Whether or not these functions should use numpy"""
    return get_addon_preferences().use_numpy


class SplitChannelImageRGB:

    unallocated_value = None

    # Channel indices to attribute names
    _channel_names = {0: "r", 1: "g", 2: "b"}

    def __init__(self, image):
        self.image = image
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
    def is_data(self) -> bool:
        return self.image.colorspace_settings.is_data

    @property
    def is_float(self) -> bool:
        return self.image.is_float

    @property
    def image_name(self) -> str:
        return self.image.name

    @image_name.setter
    def image_name(self, name: str):
        self.image.name = name

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


def copy_image(from_img: Image, to_img: Image) -> None:
    """Copies an image's pixel data to another image."""

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
