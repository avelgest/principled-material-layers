# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import itertools as it
import typing
import warnings

from typing import Iterable, List, Optional, Tuple

import bpy

from bpy.props import (BoolProperty,
                       CollectionProperty,
                       IntProperty,
                       PointerProperty,
                       StringProperty)

from bpy.types import Image, PropertyGroup

from . import tiled_storage

from .utils.image import (clear_channel,
                          copy_image_channel,
                          copy_image_channel_to_rgb,
                          delete_udim_files,
                          SplitChannelImageRGB)
from .utils.layer_stack_utils import get_layer_stack_from_prop
from .utils.naming import unique_name_in

from .channel import Channel
from .material_layer import MaterialLayer
from .preferences import get_addon_preferences
from .udim_layout import UDIMLayout


class SplitChannelImageProp(SplitChannelImageRGB, PropertyGroup):
    """A wrapper around bpy.types.Image. Each RGB channel may be
    allocated to a layer or channel. This is used by layers that
    share their images with other layers or by baked layer channels.
    """

    # The value of r, g, and b when they are not allocated
    unallocated_value = ""

    image: PointerProperty(
        type=bpy.types.Image
    )
    name: StringProperty(
        name="Name",
        update=SplitChannelImageProp._name_update
    )
    identifier: StringProperty(
        name="Identifier",
        description="A unique identifier",
        default=""
    )
    r: StringProperty(
        name="Red Channel",
        default=""
    )
    g: StringProperty(
        name="Green Channel",
        default=""
    )
    b: StringProperty(
        name="Blue Channel",
        default=""
    )

    def __eq__(self, other):
        if isinstance(other, SplitChannelImageRGB):
            return self.identifier == other.identifier

        return super().__eq__(other)

    def delete(self):
        image = self.image

        if image is None:
            return
        if image.source == 'TILED':
            im = self.image_manager
            if im.udim_layout.is_temp_image(image):
                delete_udim_files(image)

        # Remove hidden images or images that are not saved
        if (image.name.startswith(".")
                or (not image.filepath_raw and not image.packed_files)):
            bpy.data.images.remove(image)

        self.image_manager.remove_from_tiled_storage(image)

        self.image = None

    def release_image(self) -> Optional[Image]:
        """Disassociate the underlying bpy.types.Image from this
        instance, setting self.image to None. Returns the
        bpy.types.Image or None if this instance has no image.
        """
        image = self.image
        if image is None:
            return None
        self.image_manager.remove_from_tiled_storage(image)
        self.image = None
        return image

    def allocate_all_to_layer(self, layer: MaterialLayer) -> None:
        self.allocate_all_to(layer.identifier)

    def allocate_single_to_layer(self, layer: MaterialLayer, ch: int) -> None:
        self.allocate_single_to(layer.identifier, ch)

    def allocate_to_layer(self, layer: MaterialLayer, ch: int = -1) -> None:
        self.allocate_to(layer.identifier, ch)

    def allocate_to_channel_bake(self, channel, ch: int = -1) -> None:
        if channel.layer is not None:
            channel_str = f"{channel.layer.identifier}.{channel.name}"
        else:
            channel_str = f"{channel.layer_stack.identifier}.{channel.name}"
        self.allocate_to(channel_str, ch)

    def initialize_as_layer_image(self,
                                  name: str,
                                  image_manager: ImageManager) -> None:
        """Initialize the image so that it can be used to store
        MaterialLayer image data.
        """
        if self.image is not None:
            raise RuntimeError("Already initialized")

        im = image_manager

        # TODO Move to ImageManager
        if im.uses_tiled_images:
            self.image = im.udim_layout.create_tiled_image(
                            name, is_data=True, is_float=im.use_float)
        else:
            self.image = bpy.data.images.new(name,
                                             im.image_width, im.image_height,
                                             alpha=False, is_data=True,
                                             float_buffer=im.use_float)

        self.name = self.image.name
        self.identifier = im.create_identifier()

        # Alter the image data so that the image can be packed
        if not im.uses_tiled_images:
            self.image.pixels[0] = 0.0
            self.image.pack()

    def initialize_as_bake_image(self,
                                 image_manager: ImageManager,
                                 is_data: bool,
                                 is_float: bool,
                                 size: Tuple[int, int]) -> None:
        """Initialize the image so that it can be used for baking
        MaterialLayer channels."""
        if self.image is not None:
            raise RuntimeError("Already initialized")

        im = image_manager

        name = ".pml_bake_image"

        if im.uses_tiled_images:
            # TODO Use the size argument
            self.image = im.udim_layout.create_tiled_image(
                            name, is_data=is_data, is_float=is_float,
                            temp=True)
        else:
            self.image = bpy.data.images.new(name, size[0], size[1],
                                             alpha=False,
                                             is_data=is_data,
                                             float_buffer=is_float)

        self.name = self.image.name
        self.identifier = im.create_identifier()
        # TODO check name not in image_manager.bake_images

    @property
    def image_manager(self) -> ImageManager:
        return get_layer_stack_from_prop(self).image_manager

    @property
    def layer_stack(self):
        return get_layer_stack_from_prop(self)

    @staticmethod
    def _name_update(self_, context):
        self = self_
        if self.image.name == self.name:
            return
        self.image.name = self.name
        if self.name != self.image.name:
            self.name = self.image.name


class ImageManager(bpy.types.PropertyGroup):
    """Manages all the images used by a layer stack. This includes the
    images that store the alpha value for layers as well as the images
    produced by baking layers.
    """

    layer_images: CollectionProperty(
        type=SplitChannelImageProp,
        name="Layer Images",
        description="Images that store the value of paint layers"
    )
    # Image used for painting on the active layer. This will be None if
    # the active layer has no image (e.g. if it's a fill layer), an
    # image containing a copy of the layer's image data if the layer
    # uses a shared image, or the same image used by the layer
    # otherwise.
    active_image: PointerProperty(
        type=bpy.types.Image,
        name="Active Image",
        description="An image containing the data of the active paint layer",
    )
    # Property for msgbus subscriptions to use, since subscribing to
    # active_image directly doesn't seem to work
    active_image_change: PointerProperty(
        type=bpy.types.Image,
        name=""
    )
    image_width: IntProperty(
        name="Width",
        description="Horizontal resolution of image-based layers",
        min=1, soft_max=2**14, default=1024,
        subtype='PIXEL'
    )
    image_height: IntProperty(
        name="Height",
        description="Vertical resolution of image-based layers",
        min=1, soft_max=2**14, default=1024,
        subtype='PIXEL'
    )
    use_float: BoolProperty(
        name="32-bit Float",
        description="Layers use images with 32-bit float bit depth"
    )
    layers_share_images: BoolProperty(
        name="Layers Share Images",
        description="Upto three layers are stored in a single image. "
                    "Uses much less memory, but changing the active layer "
                    "becomes slower",
    )

    bake_images: CollectionProperty(
        type=SplitChannelImageProp,
        name="Bake Images",
        description="Images that channels can be baked to"
    )
    bake_samples: IntProperty(
        name="Bake Samples",
        description="Number of samples to use when baking layers",
        default=4, min=1, soft_max=128
    )
    bake_size_percent: IntProperty(
        name="Bake Size", subtype='PERCENTAGE',
        description="",
        default=100, min=1, soft_max=100
    )
    bake_float_always: BoolProperty(
        name="Always Bake as Float",
        description="Always use 32-bit float images when baking layers",
        default=False
    )
    bake_srgb_never: BoolProperty(
        name="Never Bake to sRGB",
        description="Always bake images as non-color data. Reduces the number "
                    "of shader image units used when using tiled storage",
        default=False,
        get=lambda self: (self.uses_tiled_storage
                          and self.get("bake_srgb_never", False)),
        set=lambda self, value: self.__setitem__("bake_srgb_never", value)
    )
    bake_shared: BoolProperty(
        name="Shared Bake Images",
        description="Pack multiple scalar channels into the same image",
        default=True
    )
    bake_skip_simple: BoolProperty(
        name="Skip Simple",
        description="Don't bake channels with values that are relatively "
                    "inexpensive to compute",
        default=True
    )

    # Props for when using tiled storage (copies all images to a UDIM
    # and uses that in the shader instead of indivdual images)
    uses_tiled_storage: BoolProperty(
        name="Use Tiled Storage",
        description=("Only needed if shader compilation fails due to "
                     "exceeding the shader image unit limit."
                     "Copies the images used by the add-on to a tiled image "
                     "to bypass the image limit. Significantly increases "
                     "memory usage."),
        default=False,
        update=lambda self, _: self._uses_tiled_storage_update()
    )
    tiles_srgb: PointerProperty(
        type=tiled_storage.TiledStorage,
        name="sRGB Bake Tiles",
        description="TiledStorage for sRGB images"
    )
    tiles_data: PointerProperty(
        type=tiled_storage.TiledStorage,
        name="Data Bake Tiles",
        description="TiledStorage for non-color images"
    )

    # UDIM Layout used if the image manager is initialized with
    # tiled=True
    udim_layout: PointerProperty(
        type=UDIMLayout,
        name="UDIM Layout"
    )

    # Name of the image to use when a blank image is needed
    _BLANK_IMAGE_NAME = ".pml_blank_image"

    def initialize(self, image_width: int = 1024, image_height: int = 1024,
                   use_float: bool = False, tiled: bool = False) -> None:
        """Initialize the instance. This should be called before the
        image manager is used.
        Params:
            image_width: The width (in px) of layer images.
            image_height: The height (in px) of layer images.
            use_float: If True then float images are used for layers.
            tiled: If True then layers use tiled images.
        """
        self.image_width = image_width
        self.image_height = image_height
        self.use_float = use_float

        self["uses_tiled_images"] = tiled
        if tiled:
            self.udim_layout.initialize()

        prefs = get_addon_preferences()

        # N.B. Sharing is not supported for tiled images
        self.layers_share_images = (prefs.layers_share_images
                                    and not self.uses_tiled_images)

        layer_stack = self.layer_stack
        if layer_stack is None:
            raise RuntimeError("ImageManager instance must be a property of a"
                               " LayerStack")

        self["active_layer_id"] = ""

        if self.blank_image is None:
            self._create_blank_image()

        if layer_stack.active_layer is not None:
            self.set_active_layer(layer_stack.active_layer)

        if prefs.use_tiled_storage_default and not self.uses_tiled_images:
            self.uses_tiled_storage = True

    def delete(self) -> None:
        """Deletes the image manager. This removes all images created
        by the manager from the blend file."""
        self._delete_tmp_active_image(self.active_layer)

        self.delete_tiled_storage()

        for img in self.layer_images:
            img.delete()
        self.layer_images.clear()

        for img in self.bake_images:
            img.delete()
        self.bake_images.clear()

        self.udim_layout.delete()

    def on_load(self) -> None:
        """Called by the layer stack instance when a blend file is
        loaded.
        """
        self.tiles_srgb.on_load()
        self.tiles_data.on_load()

    def active_image_name(self, layer: MaterialLayer) -> str:
        """If a temporary active image is needed to paint on layer
        (i.e if the layer uses a shared image) then this function
        returns the name the image should have.
        Returns:
            The name of the active image as a string
        """
        layer_stack_id = self.layer_stack.identifier
        layer_id = layer.identifier

        return f".plm_active_image.{layer_stack_id}.{layer_id}"

    def _add_layer_image(self) -> SplitChannelImageProp:
        layer_image = self.layer_images.add()
        name = unique_name_in(bpy.data.images, format_str=".pml_layer_data.{}")
        layer_image.initialize_as_layer_image(name, self)

        self.update_tiled_storage((layer_image.image,))

        return layer_image

    def _create_blank_image(self) -> bpy.types.Image:
        """Creates and returns the image used by the blank_image
        property. If the image already exists then the existing
        image is returned instead.
        """
        existing = bpy.data.images.get(self._BLANK_IMAGE_NAME)
        if existing is not None:
            return existing

        image = bpy.data.images.new(name=self._BLANK_IMAGE_NAME,
                                    width=32, height=32,
                                    float_buffer=False,
                                    is_data=True)

        if not image.name == self._BLANK_IMAGE_NAME:
            image.name = self._BLANK_IMAGE_NAME
            if not image.name == self._BLANK_IMAGE_NAME:
                warnings.warn("Unable to correctly name blank_image. name="
                              f"{image.name}, want {self._BLANK_IMAGE_NAME}")
        return image

    def create_identifier(self) -> str:
        """Creates a unique (in this ImageManager) identifier for a
        SplitChannelImageProp.
        """
        # All SplitChannelImageProp used by this ImageManager
        all_split_images = it.chain(self.layer_images, self.bake_images)

        identifiers = {x.identifier for x in all_split_images}
        return unique_name_in(identifiers)

    def _get_unused_layer_image_channel(self):
        """Finds a layer image with an unused channel; if none can be
        found then a new image is created.

        Returns:
        A tuple containing the layer image and the free channel's index
        """
        for layer_image in self.layer_images:
            if not layer_image.is_full:
                return (layer_image, layer_image.get_unused_channel())

        new_layer_image = self._add_layer_image()

        return (new_layer_image, 0)

    def _get_unused_layer_image(self) -> SplitChannelImageProp:
        """Finds a layer image with all of its channels free; if none
        can be found then a new image is created.

        Returns:
        The layer image
        """
        for layer_image in self.layer_images:
            if layer_image.is_empty:
                return layer_image

        new_layer_image = self._add_layer_image()

        return new_layer_image

    def allocate_image_to_layer(self, layer: MaterialLayer) -> None:
        """Allocates an image for the layer to store its alpha value in
        If layers_share_images is True then only a single channel of the
        image is allocated.
        This sets the 'image' and 'image_channel' properties on the layer.
        'image' is the Blender image used by the layer.
        'image_channel' is the index of the channel of 'image' used (-1)
        if all channels are used.
        """
        if layer.has_image:
            self.deallocate_layer_image(layer)
        assert not layer.has_image

        if self.layers_share_images:
            layer_img, ch = self._get_unused_layer_image_channel()

            layer_img.allocate_single_to_layer(layer, ch)
        else:
            layer_img = self._get_unused_layer_image()
            layer_img.allocate_all_to_layer(layer)
            ch = -1
        layer.image = layer_img.image
        layer.image_channel = ch

    def deallocate_layer_image(self, layer: MaterialLayer) -> None:
        """If layer has an image or image channel allocated to it then
        the image is deallocated. This function sets the properties
        'image' and 'image_channel' on the layer.
        Does nothing if no image is allocated to the layer
        """
        if not layer.has_image:
            return

        layer_image = self.layer_images.get(layer.image.name)
        if layer_image is None:
            return

        # TODO check that the layer_image is actually allocated to layer
        if not layer.has_shared_image:
            layer_image.deallocate_all()
        else:
            layer_image.deallocate_single(layer.image_channel)
            clear_channel(layer.image, layer.image_channel)

        layer.image = None
        layer.image_channel = -1

        if layer_image.is_empty:
            self._delete_layer_image(layer_image)

    def create_bake_image(self,
                          is_data: bool,
                          is_float: bool,
                          size: Optional[Tuple[int, int]] = None
                          ) -> SplitChannelImageProp:
        """Creates and stores an image used when baking layers."""

        if size is None:
            size = self.bake_size

        bake_image = self.bake_images.add()
        bake_image.initialize_as_bake_image(self,
                                            is_data=is_data,
                                            is_float=is_float,
                                            size=size)
        return bake_image

    def allocate_bake_image(self,
                            channel: Channel,
                            image: SplitChannelImageProp,
                            image_ch: int) -> None:
        """Allocates a channel(s) of a bake image to a material channel."""
        if image.name not in self.bake_images:
            raise RuntimeError("image not found in bake_images collection")
        if channel.is_baked:
            self.deallocate_bake_image(channel)

        if image.channel_allocated(image_ch):
            raise ValueError("image channel has already been allocated")

        image.allocate_to_channel_bake(channel, image_ch)
        channel.set_bake_image(image.image, image_ch)

    def deallocate_bake_image(self, channel: Channel) -> None:
        """Deallocates a material channel's bake image (if any)."""
        image, image_ch = channel.bake_image, channel.bake_image_channel
        if image is None:
            return
        if image.name not in self.bake_images:
            # image may have been renamed
            bake_image = next((x for x in self.bake_images
                               if x.image is image), None)
            if bake_image is None:
                return
            bake_image.name = image.name
        else:
            bake_image = self.bake_images[image.name]

        bake_image.deallocate(image_ch)
        if bake_image.is_empty:
            self._delete_bake_image(bake_image)

        channel.set_bake_image(None)
        assert not channel.is_baked

    def _delete_bake_image(self, image: SplitChannelImageProp) -> None:
        idx = self.bake_images.find(image.name)
        if idx < 0:
            raise ValueError("image not found in bake_images")

        image.delete()
        self.bake_images.remove(idx)

    def _delete_layer_image(self, image: SplitChannelImageProp) -> None:
        idx = self.layer_images.find(image.name)
        if idx < 0:
            raise ValueError("image not found in layer_images")

        image.delete()
        self.layer_images.remove(idx)

    def get_image_by_id(self,
                        identifier: str) -> Optional[SplitChannelImageProp]:
        """Returns a SplitChannelImageProp (used for layer images and
        bake images) with the given identifier."""
        return next((x for x in it.chain(self.layer_images, self.bake_images)
                     if x.identifier == identifier), None)

    def release_image(self, image: Image) -> None:
        """Disassociate image from this image manager. The image will
        not be deleted when this image manager is deleted.
        """

        for img_coll in (self.layer_images, self.bake_images):
            identifiers = [x.identifier for x in img_coll if x.image is image]
            for identifier in identifiers:
                split_image = self.get_image_by_id(identifier)
                split_image.release_image()
                split_image.delete()

                img_coll.remove(img_coll.find(split_image.name))

    def reload_tmp_active_image(self) -> None:
        """If a temporary active image is being used instead of the
        active layer's 'image' property then this loads the active
        layer's alpha into all the RGB channels of theactive image.

        Any changes made to the active image but not written back
        to the layer will be lost.
        """
        # The active layer
        active = self.active_layer
        if active is None:
            return

        if (active is not None
                and active.uses_image
                and active.has_shared_image):

            copy_image_channel_to_rgb(active.image,
                                      active.image_channel,
                                      self.active_image,
                                      copy_alpha=True)

    def reload_active_layer(self) -> None:
        """Reloads the active image from the active layer."""
        self._set_active_layer(self.active_layer)

    def _create_tmp_active_image(self,
                                 layer: MaterialLayer) -> bpy.types.Image:
        """Create an image suitable for painting on for the given layer
        and fill its RGB channels with the layer's alpha value.
        """
        image_name = self.active_image_name(layer)

        if image_name in bpy.data.images:
            self._delete_tmp_active_image(layer)

        new_active_img = bpy.data.images.new(
                    image_name,
                    self.image_width, self.image_height,
                    alpha=False, is_data=True,
                    float_buffer=self.use_float)

        # Copy the image channel that the layer stores its alpha in
        # to all rgb channels of new_active_img
        copy_image_channel_to_rgb(layer.image,
                                  layer.image_channel,
                                  new_active_img,
                                  copy_alpha=True)
        new_active_img.pack()

        return new_active_img

    def _delete_tmp_active_image(self, layer: MaterialLayer) -> None:
        """Deletes any active image made for the given layer. Does
        nothing if there is no active image for the layer.
        """
        if layer is None:
            return

        image_name = self.active_image_name(layer)

        image = bpy.data.images.get(image_name)
        if image is not None:
            bpy.data.images.remove(image)

    @property
    def _is_using_tmp_active_image(self) -> bool:
        tmp_image_name = self.active_image_name(self.active_layer)
        return (self.active_image is not None
                and self.active_image.name == tmp_image_name)

    def _replace_active_image(self,
                              layer: MaterialLayer,
                              old_layer: MaterialLayer) -> None:

        # Only deletes active images made by _create_tmp_active_image
        self._delete_tmp_active_image(old_layer)

        if not layer.uses_image:
            new_active_img = None

        elif not layer.has_shared_image:
            # Use the actual image that the layer stores its data in
            new_active_img = layer.image

        else:
            # Use a new image that is not referenced by the layer and
            # fill it with the layer's image data

            new_active_img = self._create_tmp_active_image(layer)

        if new_active_img is self.active_image:
            # No changes if the image is already active
            return

        self.active_image = new_active_img

        bpy.msgbus.publish_rna(key=self.active_image)
        bpy.msgbus.publish_rna(
            key=self.path_resolve("active_image_change", False))

    def _set_active_layer(self,
                          new_layer: MaterialLayer) -> None:
        """Changes the active layer from old_layer to new_layer"""

        # The currently active layer
        old_layer = self.active_layer

        if (old_layer is not None
                and old_layer.has_image
                and old_layer.has_shared_image
                and self._is_using_tmp_active_image):

            copy_image_channel(self.active_image,
                               0,
                               old_layer.image,
                               old_layer.image_channel)

        if (self.uses_tiled_storage
                and old_layer is not None
                and old_layer.has_image):
            self.update_tiled_storage((old_layer.image,))

        self._replace_active_image(new_layer, old_layer)

    def set_active_layer(self, layer: MaterialLayer) -> None:
        """Sets the active layer. This will also set the active_image
        property to an appropriate value for the layer.
        If currently using a temp active image then its data will be
        written back to the previous active layer.
        """
        # The identifier of the currently active layer
        current_id = self["active_layer_id"]

        if layer.identifier == current_id:
            return

        self._set_active_layer(layer)

        self["active_layer_id"] = layer.identifier

    def set_paint_canvas(self, context=None) -> None:
        """Sets the image paint canvas to this ImageManager's active
        image."""
        if context is None:
            context = bpy.context

        paint_settings = context.scene.tool_settings.image_paint

        paint_settings.mode = 'IMAGE'

        paint_settings.canvas = self.active_image

    def resize_all_layers(self, width: int, height: int) -> None:
        """Resize all layer images created by this image manager."""
        for image in self.layer_images:
            bl_image = image.image
            bl_image.scale(width, height)

        active_image = self.active_image
        if active_image is not None:
            if tuple(active_image.size) != (width, height):
                active_image.scale(width, height)

            # Need to edit pixel data after scale or texture paint may
            # display blank tiles when trying to paint (cause unknown).
            active_image.pixels[0] = active_image.pixels[0]
            active_image.update()

        self.image_width = width
        self.image_height = height

    def _uses_tiled_storage_update(self):
        """Called when the uses_tiled_storage prop changes."""
        if self.uses_tiled_storage:
            # Initialize tiled storage using all this image_manager's
            # images
            self.update_tiled_storage_all()
        else:
            # Clear tiled storage
            self.delete_tiled_storage()
        self.layer_stack.node_manager.rebuild_node_tree()

    def delete_tiled_storage(self) -> None:
        """Clears all TiledStorage instances used by this image manager.
        Can be called even if the instances are uninitialized.
        """
        self.tiles_srgb.delete()
        self.tiles_data.delete()

    def find_in_tiled_storage(self,
                              image: Image
                              ) -> Tuple[tiled_storage.TiledStorage, int]:
        """Searches for image in this ImageManager's TiledStorage
        instances returning the instance and the tile number of image.
        Params:
            image: a bpy.types.Image
        Returns:
            A tuple, (TiledStorage instance, tile_number) or (None, -1)
                if the image was not found.
        """
        if image in self.tiles_srgb:
            return self.tiles_srgb, self.tiles_srgb.get_image_tile_num(image)
        if image in self.tiles_data:
            return self.tiles_data, self.tiles_data.get_image_tile_num(image)
        return None, -1

    def remove_from_tiled_storage(self, image: Image) -> None:
        """Remove an image from tiled storage."""
        if self.tiles_srgb and image in self.tiles_srgb:
            self.tiles_srgb.remove_image(image)
        if self.tiles_data and image in self.tiles_data:
            self.tiles_data.remove_image(image)

    def update_tiled_storage_all(self) -> None:
        """Updates the tiled storage with all the layer images and
        bake images of this image manager. Will initialize the
        TiledStorage instances if necessary.
        """
        images = self.layer_images_blend + self.bake_images_blend
        self.update_tiled_storage(images)

    def update_tiled_storage(self,
                             modified_images: Optional[Iterable[Image]] = None
                             ) -> None:
        """Updates the tiled storage with modified_images and clears
        any tiles that are no longer valid. If this image manager does
        not use tiled storage then this method does nothing. Will
        initialize the TiledStorage instances if necessary.
        """
        if not self.uses_tiled_storage:
            return

        if not self.tiles_srgb:
            self.tiles_srgb.initialize(is_data=False)
        if not self.tiles_data:
            self.tiles_data.initialize(is_data=True)

        if modified_images is None:
            modified_images = []
        elif not isinstance(modified_images, typing.Collection):
            modified_images = list(modified_images)

        self.tiles_srgb.update_from(modified_images)
        self.tiles_data.update_from(modified_images)

    def update_udim_images(self) -> None:
        """Ensures all of this ImageManager's layer images have the
        same tile layout given by self.udim_layout.
        """
        for img in self.layer_images_blend:
            self.udim_layout.update_tiles(img)

    @property
    def active_layer(self):
        active_id = self["active_layer_id"]
        return (None if not active_id
                else self.layer_stack.get_layer_by_id(active_id))

    @active_layer.setter
    def active_layer(self, value):
        self.set_active_layer(value)

    @property
    def bake_size(self) -> Tuple[int, int]:
        """The size (in pixels) of the images used for baking. Tuple
        of 2 integers (width, height). Always multiples of 32."""
        ratio = self.bake_size_percent / 100
        width = int(self.image_width * ratio) // 32 * 32
        height = int(self.image_height * ratio) // 32 * 32
        return (max(width, 32), max(height, 32))

    @property
    def blank_image(self) -> Optional[bpy.types.Image]:
        """A blank solid black image."""
        # TODO maybe store reference as id_prop instead of accessing
        # by name
        image = bpy.data.images.get(self._BLANK_IMAGE_NAME)
        if image is None:
            self._create_blank_image()
            image = bpy.data.images.get(self._BLANK_IMAGE_NAME)
        return image

    @property
    def layer_images_blend(self) -> List[bpy.types.Image]:
        """The bpy.types.Image values of 'layer_images' as a list."""
        return [x.image for x in self.layer_images]

    @property
    def bake_images_blend(self) -> List[bpy.types.Image]:
        """The bpy.types.Image values of 'bake_images' as a list."""
        return [x.image for x in self.bake_images]

    @property
    def layer_size(self) -> Tuple[int, int]:
        """"The size (in pixels) of the images for image-based layers."""
        return (self.image_width, self.image_height)

    @property
    def layer_stack(self):
        return get_layer_stack_from_prop(self)

    @property
    def uses_tiled_images(self) -> bool:
        """True if layers use tiled images (UDIMs).
        Not to be confused with uses_tiled_storage.
        """
        return self.get("uses_tiled_images", False)


classes = (SplitChannelImageProp, ImageManager)

register, unregister = bpy.utils.register_classes_factory(classes)
