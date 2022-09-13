# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from typing import Collection, List, Optional

import bpy

from bpy.props import CollectionProperty, StringProperty
from bpy.types import NodeSocket

from .bake import (BakedSocketGen,
                   ChannelSocket,
                   LayerStackBaker,
                   PMLBakeSettings)
from .channel import Channel
from .material_layer import MaterialLayer, MaterialLayerRef

from .utils.image import SplitChannelImageRGB
from .utils.layer_stack_utils import get_layer_stack_from_prop
from .utils.naming import suffix_num_unique_in

BAKE_LAYERS_BELOW_NAME = ".pml_bake_layers_below"


class BakeGroup(bpy.types.PropertyGroup):
    name: StringProperty(
    )
    channels: CollectionProperty(
        type=Channel
    )

    def __contains__(self, value) -> bool:
        value_id = getattr(value, "identifier", "")
        if not value_id:
            return False
        return value_id in self.layer_ids

    def initialize(self, name="BakeGroup"):
        layer_stack = self.layer_stack
        if layer_stack is None:
            raise RuntimeError("BakeGroup must be a property of an ID with "
                               "a layer stack.")

        self.name = suffix_num_unique_in(name, layer_stack.bake_groups)

        self["layers"] = None
        self["channel_images"] = {}

    def init_from_layers(self, name, from_layer, to_layer):
        self.initialize(name=name)

        layer_refs = self.layer_stack.top_level_layers_ref
        from_idx = next(idx for idx, x in enumerate(layer_refs)
                        if x == from_layer)
        to_idx = next(idx for idx, x in enumerate(layer_refs)
                      if x == to_layer)

        if from_idx > to_idx:
            raise ValueError("from_layer is above to_layer")
        for i in range(from_idx, to_idx+1):
            self.add_layer(layer_refs[i])

    def _ensure_channel(self, channel: Channel) -> None:
        if channel.name in self.channels:
            return
        new_ch = self.channels.add()
        new_ch.init_from_channel(channel)

    def _remove_channel(self, channel: Channel) -> None:
        idx = self.channels.find(channel.name)
        if idx != -1:
            self.channels.remove(idx)

    def add_layer(self, layer: MaterialLayer) -> None:
        if not getattr(layer, "identifier", ""):
            raise TypeError("Expected layer to have a valid identifier")

        if isinstance(layer, MaterialLayerRef):
            layer = layer.resolve()

        for ch in layer.channels:
            self._ensure_channel(ch)

        layer_ids = self.layer_ids
        layer_ids.append(layer.identifier)
        self.layer_ids = layer_ids

    def remove_layer(self, layer: MaterialLayer) -> bool:
        layer_ids = self.layer_ids
        if layer.identifier in layer_ids:
            layer_ids.remove(layer.identifier)
            self.layer_ids = layer_ids

            self.update_channels()
            return True
        return False

    def update_channels(self) -> None:
        layer_stack = self.layer_stack

        new_channels = set()
        for layer_id in self.layer_ids:
            layer = layer_stack.get_layer_by_id(layer_id)
            if layer is None:
                self.remove_layer(layer_id)
                continue
            for ch in layer.channels:
                new_channels.add(ch.name)

        for ch in list(self.channels):
            if ch.name not in new_channels:
                self._remove_channel(ch)
            new_channels.remove(ch)
        for ch_name in new_channels:
            ch = layer_stack.channels.get(ch_name)
            if ch is not None:
                self._ensure_channel(ch)

    def bake(self):
        baker = BakeGroupBaker(self)
        return baker.bake()

    def free_bake(self) -> None:
        for ch in self.channels:
            ch.free_bake()

    def overlaps(self, other_group: BakeGroup) -> bool:
        """Returns True if other_group contains any layer that this
        group contains.
        """
        other_layer_ids = other_group.layer_ids
        for x in self.layer_ids:
            if x in other_layer_ids:
                return True
        return False

    def get_enabled_layer_above(self) -> Optional[MaterialLayer]:
        """Returns the first enabled layer in the layer stack above the
        top layer of this group.
        """
        layer_stack = self.layer_stack

        layer = layer_stack.get_layer_above(self.top_layer)
        while layer is not None and not layer.enabled:
            layer = layer_stack.get_layer_above(layer)
        return layer

    def get_enabled_layer_below(self) -> Optional[MaterialLayer]:
        """Returns the first enabled layer in the layer stack below the
        bottom layer of this group.
        """
        layer_stack = self.layer_stack

        layer = layer_stack.get_layer_below(self.bottom_layer)
        while layer is not None and not layer.enabled:
            layer = layer_stack.get_layer_below(layer)
        return layer

    @property
    def is_baked(self) -> bool:
        return any(ch.is_baked for ch in self.channels)

    @property
    def is_empty(self) -> bool:
        return bool(self["layers"])

    @property
    def layer_stack(self):
        return get_layer_stack_from_prop(self)

    @property
    def layer_ids(self) -> List[str]:
        return self["layers"] or []

    @layer_ids.setter
    def layer_ids(self, value):
        if not isinstance(value, Collection):
            raise TypeError("Expected a collection.")
        self["layers"] = value or None

    @property
    def top_layer(self):
        layer_ids = self.layer_ids
        if not layer_ids:
            return None
        for ref in reversed(self.layer_stack.top_level_layers_ref):
            if ref.identifier in layer_ids:
                return ref.resolve()
        return None

    @property
    def bottom_layer(self):
        layer_ids = self.layer_ids
        if not layer_ids:
            return None
        for ref in self.layer_stack.top_level_layers_ref:
            if ref.identifier in layer_ids:
                return ref.resolve()
        return None


class BakeGroupBaker(LayerStackBaker):
    def __init__(self, bake_group):
        self.bake_group = bake_group

        layer_stack = bake_group.layer_stack
        im = layer_stack.image_manager

        settings = PMLBakeSettings(image_width=im.bake_size[0],
                                   image_height=im.bake_size[1],
                                   uv_map=layer_stack.uv_map_name,
                                   always_use_float=im.bake_float_always,
                                   share_images=im.bake_shared,
                                   samples=im.bake_samples)

        super().__init__(layer_stack, settings)

    def get_baking_sockets(self) -> List[ChannelSocket]:
        nm = self.layer_stack.node_manager
        nodes = self.layer_stack.node_tree.nodes

        top_layer = self.bake_group.top_layer

        baking_sockets = []
        for ch in self.bake_group.channels:
            socket = nm.get_layer_output_socket(top_layer, ch, nodes)
            baking_sockets.append(ChannelSocket(ch, socket))

        return baking_sockets

    def bake(self) -> BakedSocketGen:
        if not self.bake_group.is_empty:
            raise RuntimeError("bake_group is empty.")

        layer_stack = self.layer_stack
        nm = layer_stack.node_manager
        node_tree = layer_stack.node_tree

        # The lowest layer in the bake group
        bottom_layer = self.bake_group.bottom_layer
        if not bottom_layer.is_base_layer:
            # The output of the Zero Constant value node
            zero_socket = node_tree.nodes[nm.node_names.zero_const()].output[0]

            # Link so that the channel inputs of the bottom layer are 0
            # (like the layer below in the layer stack outputs 0 for
            # all channels)
            for ch, _ in self.baking_sockets:
                in_socket = nm.get_layer_input_socket(bottom_layer, ch,
                                                      node_tree.nodes)
                node_tree.links.new(in_socket, zero_socket)

        return self.bake_sockets([x.socket for x in self.baking_sockets])

    def rename_image(self,
                     image: SplitChannelImageRGB,
                     socket: NodeSocket) -> None:
        """Rename an image newly created by this baker."""
        ch = next((x.channel for x in self.baking_sockets
                   if x.socket is socket), None)
        if ch is None:
            return

        # TODO rename_image is only called when a new image is created,
        # should be called after each socket is baked.
        if image.name.startswith(f".{self.bake_group.name}"):
            image.name = f"{image.name} {ch.name}"
        else:
            image.name = f".{self.bake_group.name} {ch.name}"


def register():
    bpy.utils.register_class(BakeGroup)


def unregister():
    bpy.utils.unregister_class(BakeGroup)
