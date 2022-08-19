# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import contextlib

from dataclasses import dataclass, field
from typing import (Collection,
                    Generator,
                    Iterable,
                    List,
                    NamedTuple,
                    Optional)

import bpy

from bpy.types import (NodeSocket,
                       ShaderNodeTree)

from .channel import Channel
from .utils.image import SplitChannelImageRGB
from .utils.nodes import is_socket_simple_const
from .utils.temp_changes import TempChanges, TempNodes

from .utils.layer_stack_utils import get_layer_stack_by_id


@dataclass
class PMLBakeSettings:
    """Settings used by SocketBaker instances.
    Attributes:
        image_width: The width (in pixels) of bake target images.
        image_height: The height (in pixels) of bake target images.
        uv_map: The name of the UV map to use when baking.
        share_images: If True the SocketBaker will try to bake
            multiple scalar sockets to the same image.
        always_use_float: If True then all sockets are baked to 32-bit
            float images. Otherwise only certain sockets will use float
            images.
        samples: The number of samples to use for baking.
        bake_target_tree: The tree in which to place the bake target
            node (should be a material node tree and not a node group).
    """
    image_width: int
    image_height: int

    uv_map: str = ""
    share_images: bool = True
    always_use_float: bool = False
    samples: int = 2
    bake_target_tree: Optional[ShaderNodeTree] = None


class _SocketBakeType(NamedTuple):
    """Defines the type of image that a socket should be baked to."""
    is_float: bool = False
    is_data: bool = True
    width: Optional[int] = None
    height: Optional[int] = None

    @classmethod
    def from_socket(cls, socket: NodeSocket, baker: SocketBaker):
        return cls(is_float=baker.use_float_for(socket),
                   is_data=socket.type != 'RGBA',
                   width=baker.settings.image_width,
                   height=baker.settings.image_height)

    def is_image_compatible(self, image: SplitChannelImageRGB) -> bool:
        return (image.is_float == self.is_float
                and image.is_data == self.is_data
                and (self.width is None or image.width == self.width)
                and (self.height is None or image.height == self.height))


@dataclass
class BakedSocket:
    """The result baking a socket. Returned from various methods of
    SocketBaker.
    Attributes:
        socket: The NodeSocket that was baked.
        image: SplitChannelImageRGB that the socket was baked to.
        image_ch: The index of the RGB channel of image that the socket
            was baked to. -1 if the socket uses all channels of the
            image.
    """
    socket: NodeSocket
    image: SplitChannelImageRGB
    image_ch: int = -1

    image_id: str = field(init=False)  # Identifier used by ImageManager
    _layer_stack_id: str = field(init=False, default="")

    def __post_init__(self):
        self.image_id = getattr(self.image, "identifier", "")

        layer_stack = getattr(self.image, "layer_stack", None)
        if layer_stack:
            self._layer_stack_id = layer_stack.identifier

    def get_image_safe(self) -> Optional[SplitChannelImageRGB]:
        """A safe way of getting the image attribute. Since image may
        be a SplitChannelImageProp accessing it via a python variable
        can sometimes cause crashes.
        """
        if not self._layer_stack_id or not self.image_id:
            return self.image

        layer_stack = get_layer_stack_by_id(self._layer_stack_id)

        if layer_stack is None:
            raise RuntimeError("Cannot find LayerStack with id "
                               f"{self._layer_stack_id}")

        return layer_stack.image_manager.get_image_by_id(self.image_id)

    @property
    def b_image(self) -> bpy.types.Image:
        """The bpy.types.Image that the socket is baked to.
        The image may be shared with other baked sockets.
        """
        return None if self.image is None else self.image.image


class SocketBaker:
    """Class for baking output node sockets. Temporarily creates and
    links the required nodes and uses bpy.ops.object.bake to bake the
    sockets' values to images.
    Supports baking multiple scalar sockets to different channels of
    the same image.
    """

    @classmethod
    def socket_str(cls, socket: NodeSocket) -> str:
        """Unique string for a socket."""
        return socket.path_from_id()

    def __init__(self, node_tree: ShaderNodeTree, settings: PMLBakeSettings):
        self.node_tree = node_tree
        self.settings = settings

        self.temp_nodes = None

        self.emit_node = None
        self.emit_node_rgb = None
        self.bake_target_node = None
        self._existing_img_node = None
        self._existing_img_node_rgb = None

    def _initialize_nodes(self) -> None:
        temp_nodes = self.temp_nodes
        assert temp_nodes is not None

        links = self.node_tree.links

        out_node = temp_nodes.new("ShaderNodeOutputMaterial")
        out_node.target = 'CYCLES'
        out_node.is_active_output = True

        emit_node = temp_nodes.new("ShaderNodeEmission")

        emit_node_rgb = temp_nodes.new("ShaderNodeCombineRGB")

        links.new(out_node.inputs[0], emit_node.outputs[0])

        existing_img_node = temp_nodes.new("ShaderNodeTexImage")
        existing_img_node_rgb = temp_nodes.new("ShaderNodeSeparateRGB")
        links.new(existing_img_node_rgb.inputs[0],
                  existing_img_node.outputs[0])

        # TODO try passing uv map as uv_layer argument to
        # bpy.ops.object.bake
        if self.settings.uv_map:
            uv_map_node = temp_nodes.new("ShaderNodeUVMap")
            uv_map_node.uv_map = self.settings.uv_map

            links.new(existing_img_node.inputs[0], uv_map_node.outputs[0])

        self.emit_node = emit_node
        self.emit_node_rgb = emit_node_rgb
        self._existing_img_node = existing_img_node
        self._existing_img_node_rgb = existing_img_node_rgb

    def _init_bake_target_node(self, nodes: TempNodes) -> None:
        """Initialize the image node that is the target for baking.
        Params:
            nodes: The node collection in which to create the image
                   node. Should be a TempNodes instance created
                   from self.bake_target_node_tree.
        """

        bake_target_node = nodes.new("ShaderNodeTexImage")
        bake_target_node.label = bake_target_node.name = "Bake Target"
        bake_target_node.hide = True

        links = self.bake_target_node_tree.links

        if self.settings.uv_map:
            uv_map_node = nodes.new("ShaderNodeUVMap")
            uv_map_node.uv_map = self.settings.uv_map

            links.new(bake_target_node.inputs[0], uv_map_node.outputs[0])

        self.bake_target_node = bake_target_node

    def _reset_state(self) -> None:
        self.temp_nodes = None

        self.emit_node = None
        self.emit_node_rgb = None
        self.bake_target_node = None
        self._existing_img_node = None
        self._existing_img_node_rgb = None

    def _set_bake_target_active(self, image: bpy.types.Image) -> None:
        """Sets the bake target as the active node in its node tree.
        Params:
            image: The image to bake to.
        """
        node_tree = self.bake_target_node_tree

        self.bake_target_node.image = image
        node_tree.nodes.active = self.bake_target_node

    def _bake_socket_unshared(
            self,
            socket: NodeSocket,
            images: Iterable[SplitChannelImageRGB]) -> BakedSocket:
        """Bake a single socket to an empty image from images. If there
        isn't an available image in images then a new one is created.
        """
        bake_type = _SocketBakeType.from_socket(socket, self)

        # Look for a completely free image in images
        bake_img = next((x for x in images if x.is_empty
                         and bake_type.is_image_compatible(x)), None)
        if bake_img is None:
            bake_img = self.create_image(socket)

        self._set_bake_target_active(bake_img.image)
        self.node_tree.links.new(self.emit_node.inputs[0], socket)

        bpy.ops.object.bake(type='EMIT')

        self.allocate_image_to(bake_img, -1, socket)

        return BakedSocket(socket, bake_img, -1)

    def _bake_shared(self, image, sockets) -> List[BakedSocket]:
        """Bakes multiple sockets to a single image. Expects at most
        three sockets. Returns a list of BakedSocket instances.
        """
        assert len(sockets) < 4
        links = self.node_tree.links

        # The return value
        baked_sockets: List[BakedSocket] = []

        links.new(self.emit_node.inputs[0], self.emit_node_rgb.outputs[0])

        if not image.is_empty:
            for ch_idx, ch_val in enumerate(image.channel_contents):
                if ch_val:
                    links.new(self.emit_node_rgb.inputs[ch_idx],
                              self._existing_img_node_rgb.inputs[ch_idx])

        for socket in sockets:
            ch_idx = image.get_unused_channel()
            assert ch_idx is not None
            assert ch_idx < 4

            self.allocate_image_to(image, ch_idx, socket)

            links.new(self.emit_node_rgb.inputs[ch_idx], socket)
            baked_sockets.append(BakedSocket(socket, image, ch_idx))

        self._set_bake_target_active(image.image)

        if image.is_empty:
            bpy.ops.object.bake(type='EMIT')
        else:
            img_copy = image.image.copy()
            try:
                self._existing_img_node.image = img_copy
                bpy.ops.object.bake(type='EMIT')
            finally:
                bpy.data.images.remove(img_copy)

        return baked_sockets

    def _bake_socket_shared_gen(
            self,
            images: Collection[SplitChannelImageRGB],
            bake_type: _SocketBakeType
            ) -> Generator[List[BakedSocket], NodeSocket, None]:
        """Returns a generator iterator that bakes sockets for a specific
        image type. The generator iterator receives sockets using send and
        performs the bake when it has enough to fill an image, or when it
        is told to finish by being sent None.
        Params:
            images: A collection of SplitChannelImageRGB to bake to. If
                there aren't enough images more will be created by the
                create_image method. Only images compatible with bake_type
                will be used.
            bake_type: A _SocketBakeType defining the type of image to bake
                to.
        Returns:
            A generator iterator that should be sent sockets and yields
            lists of BakedSocket when a bake has occured, otherwise yields
            None.
        """

        # Images compatible with bake_type
        compat_images = (x for x in images if not x.is_full
                         and bake_type.is_image_compatible(x))

        def _bake_socket_shared_gen_() -> Optional[List[BakedSocket]]:

            to_bake = []
            image = None

            socket = yield
            while socket is not None:
                if image is None:
                    image = next(compat_images, None)
                    if image is None:
                        image = self.create_image(socket)
                    else:
                        # FIXME Potential issues with compat_images generator
                        # and removing items from images
                        images.remove(image)

                to_bake.append(socket)
                if len(to_bake) == image.num_unused:
                    ret = self._bake_shared(image, to_bake)
                    to_bake.clear()
                    image = None
                    socket = yield ret
                else:
                    socket = yield None
            if to_bake:
                yield self._bake_shared(image, to_bake)

        return _bake_socket_shared_gen_()

    def _bake_sockets_shared(self, sockets, images):
        """Bake sockets to images with multiple sockets to each image.
        If there aren't enough images more will be created.
        """

        # Dict of _SocketBakeType to generator iterators
        bake_socket_gen_its = {}

        baked_sockets: List[BakedSocket] = []

        for socket in sockets:
            if socket.type == 'SHADER':
                continue

            bake_type = _SocketBakeType.from_socket(socket, self)

            gen_it = bake_socket_gen_its.get(bake_type)
            if gen_it is None:
                # Initialize a bake generator iterator
                gen_it = self._bake_socket_shared_gen(images, bake_type)
                bake_socket_gen_its[bake_type] = gen_it
                next(gen_it)

            # Send the generator the socket
            baked = gen_it.send(socket)
            # The generator will return None if it is waiting for more
            # sockets before performing the bake.
            if baked is not None:
                baked_sockets += baked

        # Tell all bake genrators not to expect any more sockets and
        # bake any sockets they have not yet baked.
        for gen_it in bake_socket_gen_its.values():
            baked = next(gen_it, None)
            if baked is not None:
                baked_sockets += baked

        return baked_sockets

    def allocate_image_to(self, image: SplitChannelImageRGB,
                          image_ch: int,
                          socket: NodeSocket) -> None:
        image.allocate_to(self.socket_str(socket), image_ch)

    @property
    def bake_target_node_tree(self) -> ShaderNodeTree:
        """The node tree in which to place the bake target node"""
        bake_target_tree = self.settings.bake_target_tree
        return (bake_target_tree if bake_target_tree is not None
                else self.node_tree)

    def bake_sockets(self,
                     sockets: Iterable[NodeSocket],
                     images: Collection[SplitChannelImageRGB] = tuple()
                     ) -> List[BakedSocket]:
        """Bakes the given sockets. If any of the images in 'images'
        are compatible and empty (or have spare channels depending on
        settings.share_images) then they may be used as bake targets.
        If there are not enough usable images the new ones will be
        created.

        Params:
            sockets: The sockets to bake.
            images: A collection of SplitChannelImageRGB that can be
                used as bake targets.
        Returns:
            A list of BakedSocket instances.
        """
        scene = bpy.context.scene
        images = list(images)

        sockets = tuple(sockets)
        if not all(x.is_output for x in sockets):
            raise ValueError("Expected only output sockets.")

        with contextlib.ExitStack() as exit_stack:
            exit_stack.callback(self._reset_state)

            self.temp_nodes = TempNodes(self.node_tree)
            exit_stack.enter_context(self.temp_nodes)

            # Create TempNodes instance for self.bake_target_node_tree
            # if it's not the same as self.node_tree
            target_node_tree = self.bake_target_node_tree

            if target_node_tree is self.node_tree:
                target_temp_nodes = self.temp_nodes
            else:
                target_temp_nodes = TempNodes(target_node_tree)
                exit_stack.enter_context(target_temp_nodes)

            self._init_bake_target_node(target_temp_nodes)

            self._initialize_nodes()

            render_props = exit_stack.enter_context(
                            TempChanges(scene.render, False))
            cycles_props = exit_stack.enter_context(
                            TempChanges(scene.cycles, False))
            bake_props = exit_stack.enter_context(
                            TempChanges(scene.render.bake, False))

            render_props.engine = 'CYCLES'
            render_props.use_bake_multires = False

            cycles_props.bake_type = 'EMIT'
            cycles_props.film_exposure = 1.0
            # cycles_props.use_preview_adaptive_sampling = True
            cycles_props.samples = self.settings.samples
            cycles_props.use_denoising = False

            bake_props.target = 'IMAGE_TEXTURES'
            bake_props.use_clear = True
            bake_props.use_selected_to_active = False

            # Remove duplicates but keep order
            sockets = list(dict.fromkeys(sockets))

            # Bake these sockets into only a single channel (multiple
            # sockets share images)
            shared = [x for x in sockets if self.settings.share_images
                      and self.num_channels_for(x) == 1]

            # These sockets will each be baked to individual images
            unshared = [x for x in sockets if x not in shared]

            baked_sockets = []
            baked_sockets += self._bake_sockets_shared(shared, images)

            for socket in unshared:
                if socket.type == 'SHADER':
                    continue
                baked = self._bake_socket_unshared(socket, images)
                baked_sockets.append(baked)

        return baked_sockets

    def create_image(self, socket: NodeSocket) -> SplitChannelImageRGB:
        settings = self.settings

        name = self._image_name([socket])

        use_float = self.use_float_for(socket)
        is_data = not self.use_srgb_for(socket)

        image = bpy.data.images.new(
            name, settings.image_width, settings.image_height,
            alpha=False, is_data=is_data, float_buffer=use_float)
        return SplitChannelImageRGB(image)

    def _image_name(self, sockets) -> str:
        if not sockets:
            return "bake image"

        assert len(sockets)

        return f"{sockets[0].node.name} {''.join(x.name for x in sockets)}"

    def num_channels_for(self, socket: NodeSocket) -> int:
        """Returns the number of RGB channels that a socket requires
           when baked.
        """
        return 1 if socket.type == 'VALUE' else 3

    def use_srgb_for(self, socket: NodeSocket) -> bool:
        return socket.type == 'RGBA'

    def use_float_for(self, socket: NodeSocket) -> bool:
        if self.settings.always_use_float:
            return True

        socket_type_name = socket.bl_rna.identifier
        # N.B. Need to use float for vectors to support negative values

        # Only use ints for color sockets or 'fac' scalar sockets
        return not ("Color" in socket_type_name
                    or socket.name.lower() == "fac")


class ChannelSocket(NamedTuple):
    """A Channel / NodeSocket pair.

    Attributes:
        channel - Channel instance. May be from a MaterialLayer or a
            layer stack.
        socket - NodeSocket. Should be a ShaderNode's output socket and
            is the socket that should be used when baking the channel.
    """
    channel: Channel
    socket: NodeSocket


class LayerStackBaker(SocketBaker):
    """Subclass of socket baker for baking the channels of a LayerStack"""

    def __init__(self, layer_stack, settings: PMLBakeSettings):
        self.layer_stack = layer_stack

        # The channels to bake with their input socket on the layer
        # stack's output node
        self.baking_sockets: List[ChannelSocket] = self.get_baking_sockets()

        super().__init__(layer_stack.node_tree, settings)

    def allocate_image_to(self,
                          image: SplitChannelImageRGB,
                          image_ch: int,
                          socket: NodeSocket) -> None:
        """Uses the layer stack's image manager to allocate an image to
        the channel associated with 'socket'.
        Override of method in SocketBaker.
        """
        channel = next(ch for ch, soc in self.baking_sockets
                       if soc is socket)
        self.image_manager.allocate_bake_image(channel, image, image_ch)

    def bake(self) -> List[BakedSocket]:
        sockets = [x.socket for x in self.baking_sockets]

        if self.layer_stack.material.node_tree is None:
            raise ValueError("Layer stack's material has no node tree.")

        return self.bake_sockets(sockets)

    def create_image(self, socket: NodeSocket) -> SplitChannelImageRGB:
        """Creates a new image (via the layer stack's image manager)
        to bake to.
        Override of method in SocketBaker.
        """
        is_data = not self.use_srgb_for(socket)
        is_float = self.use_float_for(socket)

        image = self.image_manager.create_bake_image(is_data=is_data,
                                                     is_float=is_float)
        self.rename_image(image, socket)
        return image

    def get_baking_sockets(self) -> List[ChannelSocket]:
        """Returns a list of the channels and sockets that should be
        baked.
        Returns:
            A list of ChannelSocket, each contains a Channel and its
            corresponding NodeSocket.
        """
        nodes = self.layer_stack.node_tree.nodes
        node_names = self.layer_stack.node_manager.node_names

        # The group output node of the layer stack's node tree
        ma_node = nodes.get(node_names.output())
        if ma_node is None:
            raise RuntimeError("Could not find output node for layer stack.")

        baking_sockets = []
        for ch in self.layer_stack.channels:
            if not ch.enabled:
                continue
            input_socket = ma_node.inputs[ch.name]
            if input_socket.is_linked:
                output_socket = input_socket.links[0].from_socket
                baking_sockets.append(ChannelSocket(ch, output_socket))

        return baking_sockets

    def rename_image(self,
                     image: SplitChannelImageRGB,
                     socket: NodeSocket) -> None:
        """Rename an image newly created by this baker."""
        channel_socket = next((x for x in self.baking_sockets
                               if x.socket is socket), None)
        if channel_socket is None:
            return

        channel = channel_socket.channel
        layer_stack = channel.layer_stack
        ma = layer_stack.material

        image.name = f"{ma.name} {channel.name} baked"

    def get_channel(self, socket: NodeSocket) -> Channel:
        try:
            return next(x.channel for x in self.baking_sockets
                        if x.socket is socket)
        except StopIteration as e:
            raise ValueError("Could not find socket {socket.name} "
                             "in self.baking_sockets") from e

    def use_srgb_for(self, socket: NodeSocket) -> bool:
        """Use SRGB for this socket. Should only be True for color.
        Override of method in SocketBaker.
        """
        ch_type = self.get_channel(socket).socket_type
        return ch_type == 'COLOR'

    def use_float_for(self, socket: NodeSocket) -> bool:
        """Whether or not to use a float for a particular socket.
        Override of method in SocketBaker.
        """
        ch_type = self.get_channel(socket).socket_type
        # Always use float for FLOAT and VECTOR
        # Can only use scalar for COLOR or FLOAT_FACTOR
        if ch_type in ('FLOAT', 'VECTOR'):
            return True
        return self.settings.always_use_float

    @property
    def bake_target_node_tree(self) -> ShaderNodeTree:
        """The node tree in which to place the bake target node.
        Override of property in SocketBaker.
        """
        return self.layer_stack.material.node_tree

    @property
    def image_manager(self):
        """The layer stack's image manager."""
        return self.layer_stack.image_manager


class LayerBaker(LayerStackBaker):
    """Subclass of SocketBaker for baking the channels of a MaterialLayer"""
    def __init__(self, layer):
        self._layer = layer

        layer_stack = layer.layer_stack
        im = layer_stack.image_manager

        settings = PMLBakeSettings(image_width=im.bake_size[0],
                                   image_height=im.bake_size[1],
                                   uv_map=layer_stack.uv_map_name,
                                   always_use_float=im.bake_float_always,
                                   share_images=im.bake_shared,
                                   samples=im.bake_samples)

        super().__init__(layer_stack, settings)

    def get_baking_sockets(self) -> List[ChannelSocket]:
        layer = self._layer
        nodes = self.layer_stack.node_tree.nodes
        node_names = self.layer_stack.node_manager.node_names

        ma_node = nodes.get(node_names.layer_material(layer))
        if ma_node is None:
            raise RuntimeError("Could not find material node for layer "
                               f"{layer.name}.")

        baking_sockets = [ChannelSocket(ch, ma_node.outputs[ch.name])
                          for ch in layer.channels]

        return baking_sockets

    def bake(self, skip_simple_const=True) -> List[BakedSocket]:
        sockets = [x.socket for x in self.baking_sockets]

        if skip_simple_const:
            sockets = [x for x in sockets if not is_socket_simple_const(x)]

        if self.layer_stack.material.node_tree is None:
            raise ValueError("Layer stack's material has no node tree.")

        baked_sockets = self.bake_sockets(sockets)

        return baked_sockets

    def rename_image(self, image, socket) -> None:
        """Rename an image newly created by this baker.
        Override of LayerStackBaker method.
        """
        # No code. Don't rename and just keep the default name.


def apply_node_mask_bake(layer, samples) -> bpy.types.Image:
    layer_stack = layer.layer_stack

    if layer.node_mask is None:
        raise ValueError("layer has no node mask.")
    if layer.is_base_layer:
        raise ValueError("Cannot bake the base layer's layer mask.")

    nm = layer_stack.node_manager
    im = layer_stack.image_manager
    socket_to_bake = nm.get_layer_final_alpha_socket(layer)

    settings = PMLBakeSettings(image_width=im.image_width,
                               image_height=im.image_height,
                               uv_map=layer_stack.uv_map_name,
                               always_use_float=True,
                               share_images=False,
                               samples=samples,
                               bake_target_tree=layer_stack.material.node_tree)

    baker = SocketBaker(layer_stack.node_tree, settings)

    old_opacity = layer.opacity
    try:
        layer.opacity = 1.0
        baked = baker.bake_sockets((socket_to_bake,))[0]
    finally:
        layer.opacity = old_opacity

    image = baked.b_image
    image.name = f"{layer.name} Node Mask"
    return image
