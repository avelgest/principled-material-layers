# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import warnings

from typing import List, Optional, Tuple

import bpy

from bpy.props import (BoolProperty,
                       CollectionProperty,
                       IntProperty,
                       PointerProperty,
                       StringProperty)

from bpy.types import PropertyGroup

from .utils.image import (clear_channel,
                          copy_image_channel,
                          copy_image_channel_to_rgb,
                          SplitChannelImageRGB)
from .utils.naming import unique_name_in

from .channel import Channel
from .material_layer import MaterialLayer
from .preferences import get_addon_preferences


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
    )
    r: StringProperty(
        name="Red Channel",
        default=unallocated_value
    )
    g: StringProperty(
        name="Green Channel",
        default=unallocated_value
    )
    b: StringProperty(
        name="Blue Channel",
        default=unallocated_value
    )

    def delete(self):
        if self.image is not None:
            if not self.image.filepath:
                bpy.data.images.remove(self.image)
            self.image = None

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

        self.image = bpy.data.images.new(name, im.image_width, im.image_height,
                                         alpha=False, is_data=True,
                                         float_buffer=im.use_float)

        self.name = self.image.name

        # Alter the image data so that the image can be packed
        self.image.pixels[0] = 0.0
        self.image.pack()

    def initialize_as_bake_image(self,
                                 image_manager: ImageManager,
                                 is_data: bool,
                                 is_float: bool) -> None:
        """Initialize the image so that it can be used for baking
        MaterialLayer channels."""
        if self.image is not None:
            raise RuntimeError("Already initialized")

        im = image_manager

        name = ".pml_bake_image"
        width, height = im.bake_size

        self.image = bpy.data.images.new(name, width, height,
                                         alpha=False,
                                         is_data=is_data,
                                         float_buffer=is_float)

        self.name = self.image.name
        # TODO check name not in image_manager.bake_images


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
    bake_shared: BoolProperty(
        name="Shared Bake Images",
        description="Pack multiple scalar channels into the same image",
        default=True
    )

    # Name of the image to use when a blank image is needed
    _BLANK_IMAGE_NAME = ".pml_blank_image"

    def initialize(self, layer_stack, image_width=1024, image_height=1024,
                   use_float=False) -> None:
        self.image_width = image_width
        self.image_height = image_height
        self.use_float = use_float

        prefs = get_addon_preferences()

        self.layers_share_images = prefs.layers_share_images

        self["layer_stack_path"] = layer_stack.path_from_id()

        self["active_layer_id"] = ""

        if self.blank_image is None:
            self._create_blank_image()

        if layer_stack.active_layer is not None:
            self.set_active_layer(layer_stack.active_layer)

    def delete(self) -> None:
        """Deletes the image manager. This removes all images created
        by the manager from the blend file."""
        self._delete_tmp_active_image(self.active_layer)

        for img in self.layer_images:
            img.delete()
        self.layer_images.clear()

        for img in self.bake_images:
            img.delete()
        self.bake_images.clear()

    def active_image_name(self, layer: MaterialLayer) -> str:
        """If a temporary active image is needed to paint on layer
        (i.e if the layer uses a shared image) then this function
        returns the name the image should have.
        Returns:
            A string
        """
        layer_stack_id = self.layer_stack.identifier
        layer_id = layer.identifier

        return f".plm_active_image.{layer_stack_id}.{layer_id}"

    def _add_layer_image(self) -> SplitChannelImageProp:
        layer_image = self.layer_images.add()
        name = unique_name_in(bpy.data.images, format_str=".pml_layer_data.{}")
        layer_image.initialize_as_layer_image(name, self)

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
            raise RuntimeError(f"Cannot find image {layer.image.name} in "
                               "layer_images")

        # TODO check that the layer_image is actually allocated to layer
        if not layer.uses_shared_image:
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
                          is_float: bool) -> SplitChannelImageProp:
        """Creates and stores an image used when baking layers."""

        bake_image = self.bake_images.add()
        bake_image.initialize_as_bake_image(self,
                                            is_data=is_data,
                                            is_float=is_float)
        return bake_image

    def allocate_bake_image(self,
                            channel: Channel,
                            image: SplitChannelImageProp,
                            image_ch: int) -> None:

        if image.name not in self.bake_images:
            raise RuntimeError("image not found in bake_images collection")
        if channel.is_baked:
            self.deallocate_bake_image(channel)

        if image.channel_allocated(image_ch):
            raise ValueError("image channel has already been allocated")

        image.allocate_to_channel_bake(channel, image_ch)
        channel.set_bake_image(image.image, image_ch)

    def deallocate_bake_image(self, channel: Channel) -> None:
        image, image_ch = channel.bake_image, channel.bake_image_channel
        if image is None:
            return
        if image.name not in self.bake_images:
            # image may have been renamed
            bake_image = next((x for x in self.bake_images
                               if x.image is image), None)
            bake_image.name = image.name
            if bake_image is None:
                raise RuntimeError("image not found in bake_images collection")
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

    def reload_tmp_active_layer(self) -> None:
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
                and active.image is not None
                and active.uses_shared_image):

            copy_image_channel_to_rgb(active.image,
                                      active.image_channel,
                                      self.active_image,
                                      copy_alpha=True)

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

    def _replace_active_image(self,
                              layer: MaterialLayer,
                              old_layer: MaterialLayer) -> None:

        if not layer.has_image:
            new_active_img = None

        elif not layer.uses_shared_image:
            # Use the actual image that the layer stores its data in
            new_active_img = layer.image

        else:
            # Use a new image that is not referenced by the layer and
            # fill it with the layer's image data

            new_active_img = self._create_tmp_active_image(layer)

        if new_active_img is self.active_image:
            # No changes if the image is already active
            return

        # Only deletes active images made by _create_tmp_active_image
        self._delete_tmp_active_image(old_layer)

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
                and old_layer.image is not None
                and old_layer.uses_shared_image):

            copy_image_channel(self.active_image,
                               0,
                               old_layer.image,
                               old_layer.image_channel)

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

    def resize_all_layers(self, width: int, height: int) -> None:
        """Resize all layer images created by this image manager."""
        for image in self.layer_images:
            bl_image = image.image
            bl_image.scale(width, height)
        if self.active_image is not None:
            self.active_image.scale(width, height)

        self.image_width = width
        self.image_height = height

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
        try:
            layer_stack_path = self["layer_stack_path"]
        except KeyError as e:
            raise RuntimeError("No id property 'layer_stack_path'."
                               "Prehaps the image manager has not been "
                               "initialized") from e

        return self.id_data.path_resolve(layer_stack_path)


classes = (SplitChannelImageProp, ImageManager)

register, unregister = bpy.utils.register_classes_factory(classes)
