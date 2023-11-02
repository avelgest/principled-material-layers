# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it
import typing
import warnings

from collections import defaultdict
from typing import Callable, Optional

import bpy

from bpy.types import NodeReroute, NodeSocket

from . import pml_node_tree
from .on_load_manager import pml_trusted_callback
from .pml_node_tree import NodeNames
from .preferences import get_addon_preferences
from .utils.layer_stack_utils import (get_layer_stack_by_id,
                                      get_layer_stack_from_prop)
from .utils.nodes import EnabledSocketsNode, ensure_outputs_match_channels


class NodeManager(bpy.types.PropertyGroup):
    """Class responsible for building and updating a LayerStack's
    internal node tree. Normally the node tree is rebuilt from scratch
    (using rebuild_node_tree) when any changes are required, though
    some changes (e.g. changing the active layer or changing a layer's
    blend_mode) simply update the existing node tree.
    """

    # Stores the msgbus owners for each instance of this class
    # (mapped by layer_stack.identifier).
    _cls_msgbus_owners = defaultdict(lambda: defaultdict(dict))

    # Dict of layer stack ids to functions to rebuild each layer stack
    _rebuild_functions: dict[str, Callable[[], None]] = {}

    node_names = NodeNames()

    # Rebuilding can sometimes fail due to an incorrect context this is
    # the max number of times to try rebuilding before raising an error.
    MAX_REBUILD_RETRIES = 32

    def initialize(self) -> None:
        """Initializes the layer_stack. Must be called before the
        NodeManager is used.
        """
        layer_stack = self.layer_stack
        if layer_stack is None:
            raise RuntimeError("NodeManager instance must be a property of a "
                               "LayerStack.")

        self._initialize_node_tree()

        self._register_msgbus()

        self["_on_load_cb"] = layer_stack.add_on_load_callback(self._on_load)
        self["layer_stack_id"] = layer_stack.identifier

    def delete(self) -> None:
        """Deletes the NodeManager. Initialize must be called before
        the NodeManager can be used again.
        """
        on_load_cb = self.get("_on_load_cb")
        if on_load_cb:
            self.layer_stack.remove_on_load_callback(on_load_cb)

        self._unregister_msgbus()

    @pml_trusted_callback
    def _on_load(self) -> None:
        """Called when the blend file is loaded."""
        self._register_msgbus()

    def get_layer_input_socket(self, layer, channel, nodes=None):
        """Returns the input socket that connects to the output of
        channel on the previous layer.
        """
        if nodes is None:
            nodes = self.nodes
        if layer.is_base_layer:
            return None

        node_name = NodeNames.blend_node(layer, channel)
        node = nodes[node_name]
        inputs = EnabledSocketsNode(node).inputs

        if len(inputs) == 1:
            return inputs[0]
        return inputs[1]

    def get_layer_output_socket(self, layer, channel, nodes=None):
        """Returns the socket that gives layer's output for channel,
        i.e. the blended value for most layers or just the output from
        the material if layer is the base layer.
        This is the socket that connects to the layer above (or the
        group output if layer is the top layer).
        The node tree's nodes collection can be passed as nodes to
        avoid refetching it.
        """
        if nodes is None:
            nodes = self.nodes

        if channel.renormalize:
            node = nodes.get(NodeNames.renormalize(layer, channel))
            if node:
                return node.outputs[0]

        if layer.is_base_layer or channel.usage != 'BLENDING':
            # Can return a baked value for channels that don't blend
            node = nodes.get(NodeNames.baked_value(layer, channel))
            if node is not None:
                return node.outputs[0]

        else:
            # Look for a blending node
            node = nodes.get(NodeNames.blend_node(layer, channel))
            if node is not None:
                return EnabledSocketsNode(node).outputs[0]

        # Look for a socket on the layer's material group
        node = nodes[NodeNames.layer_material(layer)]
        output_socket = node.outputs.get(channel.name)
        if output_socket is None:
            warnings.warn(f"Socket for {channel.name} not found in "
                          "layer node group.")
            # Value socket which is always 0
            return self._zero_const_output_socket

        return output_socket

    def get_layer_final_alpha_socket(self, layer, nodes=None):
        """Returns the socket that gives the alpha value of the layer
        after any masks and the opacity have been applied.
        """
        if nodes is None:
            nodes = self.nodes

        node_name = NodeNames.layer_alpha_x_opacity(layer)
        return nodes[node_name].outputs[0]

    def get_ma_group_output_socket(self, layer, channel,
                                   use_baked=True, nodes=None) -> NodeSocket:
        """Returns the output socket of layer's Group Node that matches
        channel. If use_baked is True and the layer's material is baked
        then the socket of the image it is baked to is returned.
        """
        if nodes is None:
            nodes = self.nodes

        if channel.is_baked and use_baked:
            ma_group_output = self._get_bake_image_socket(layer, channel,
                                                          nodes=nodes)
        else:
            ma_group = nodes[NodeNames.layer_material(layer)]
            ma_group_output = ma_group.outputs.get(channel.name)

        if ma_group_output is not None:
            return ma_group_output

        warnings.warn(f"Cannot find output socket '{channel.name}' for "
                      f"the node group of layer '{layer.name}' "
                      f"{'(baked)' if channel.is_baked else ''}")
        return self._zero_const_output_socket

    def _get_bake_image_socket(self, layer, layer_ch, nodes=None):
        if nodes is None:
            nodes = self.nodes
        node_name = NodeNames.baked_value(layer, layer_ch)
        return nodes[node_name].outputs[0]

    def has_hardness_threshold(self, layer, channel) -> bool:
        """Returns true if a hardness threshold node exists for channel
        of layer.
        """
        return NodeNames.hardness_threshold(layer, channel) in self.nodes

    def has_channel_opacity(self, layer, channel) -> bool:
        """Returns true if a opacity node exists for channel of layer."""
        return NodeNames.channel_opacity(layer, channel) in self.nodes

    def update_blend_node(self, layer, channel) -> None:
        # Since child nodes are not yet supported ignore any layer that
        # is not top level in the stack (also ignore any unintialized
        # layer).
        if not layer or not layer.is_top_level:
            return

        nodes = self.layer_stack.node_tree.nodes

        making_info = channel.blend_node_make_info

        node_name = NodeNames.blend_node(layer, channel)
        node = nodes.get(node_name)

        if node is None:
            return

        if not channel.enabled:
            # Just ensure that node is a reroute node
            if not isinstance(node, NodeReroute):
                self.rebuild_node_tree()

        elif node.bl_idname == making_info.bl_idname:
            # Just update the options of the existing node
            making_info.update_node(node, channel)
            # Updating the node may have enabled/disabled sockets so
            # may need to rebuild the node tree after all.
            node = EnabledSocketsNode(node)
            if not all(x.is_linked for x in it.chain(node.inputs[:3],
                                                     node.outputs[:1])):
                self.rebuild_node_tree()
        else:
            self.rebuild_node_tree()

    def _connect_output_baked(self):
        """Connects the sockets of the group output node when the layer
        stack is baked.
        """
        layer_stack = self.layer_stack
        nodes = self.nodes
        links = self.links

        output_node = nodes[NodeNames.output()]

        assert layer_stack.is_baked

        for ch in layer_stack.channels:
            if not ch.is_baked or ch.name not in output_node.inputs:
                continue
            if ch.bake_image_channel == -1:
                bake_node = nodes[NodeNames.bake_image(ch.bake_image)]
                bake_socket = bake_node.outputs[0]
            else:
                bake_node = nodes[NodeNames.bake_image_rgb(ch.bake_image)]
                bake_socket = bake_node.outputs[ch.bake_image_channel]

            links.new(output_node.inputs[ch.name], bake_socket)

    def connect_output_layer(self):
        """Connects the sockets of the group output node to the outputs
        of the top layer of the node stack
        """
        layer_stack = self.layer_stack
        layer = layer_stack.top_enabled_layer
        nodes = self.nodes
        links = self.links

        output_node = nodes[NodeNames.output()]

        if layer is None:
            return

        for ch in layer_stack.channels:
            if not ch.enabled:
                continue
            in_socket = output_node.inputs.get(ch.name)
            if in_socket is None:
                warnings.warn(f"No socket found for {ch.name} in PML internal "
                              "node tree's group output.")
                continue
            out_socket = self.get_layer_output_socket(layer, ch, nodes)
            links.new(in_socket, out_socket)

        if layer.is_base_layer:
            output_node.location.x = 400
        else:
            layer_frame = nodes[NodeNames.layer_frame(layer)]
            output_node.location.x = layer_frame.location.x + 1000

        if layer_stack.is_baked:
            self._connect_output_baked()

    def reconnect_ma_groups(self, baked: bool) -> None:
        """Reconnect the Group node of each layer's material. If baked
        is True then existing baked images are connected instead
        (when present).
        """
        layer_stack = self.layer_stack
        nodes = self.layer_stack.node_tree.nodes
        links = self.layer_stack.node_tree.links

        for layer in layer_stack.layers:
            if not layer or not layer.enabled:
                continue
            for ch in layer.channels:
                ma_output = self.get_ma_group_output_socket(layer, ch,
                                                            use_baked=baked,
                                                            nodes=nodes)
                blend_node = nodes.get(NodeNames.blend_node(layer, ch))
                if blend_node is not None:
                    blend_node = EnabledSocketsNode(blend_node)
                    links.new(blend_node.inputs[2], ma_output)

    def _on_active_image_change(self):
        layer_stack = self.layer_stack
        im = layer_stack.image_manager

        self.active_layer_image = im.active_image

    def _register_msgbus(self):
        layer_stack = self.layer_stack
        image_manager = layer_stack.image_manager
        owners = self._msgbus_owners

        layer_stack_id = layer_stack.identifier
        msgbus_options = {'PERSISTENT'}

        def update_node_tree_sockets():
            layer_stack = get_layer_stack_by_id(layer_stack_id)
            self = layer_stack.node_manager

            self.update_node_tree_sockets()
            self.connect_output_layer()

        bpy.msgbus.subscribe_rna(
            key=layer_stack.channels,
            owner=owners,
            args=tuple(),
            notify=update_node_tree_sockets,
            options=msgbus_options
        )

        def on_active_image_change():
            layer_stack = get_layer_stack_by_id(layer_stack_id)
            if layer_stack is not None:
                self = layer_stack.node_manager

                self._on_active_image_change()

        bpy.msgbus.subscribe_rna(
            key=image_manager.path_resolve("active_image_change", False),
            owner=owners,
            args=tuple(),
            notify=on_active_image_change,
            options=msgbus_options
        )

        def update_uv_map():
            layer_stack = get_layer_stack_by_id(layer_stack_id)
            self = layer_stack.node_manager

            uv_map_node = self.nodes[NodeNames.uv_map()]
            uv_map_node.uv_map = layer_stack.uv_map_name

        bpy.msgbus.subscribe_rna(
            key=layer_stack.path_resolve("uv_map_name", False),
            owner=owners,
            args=tuple(),
            notify=update_uv_map,
            options=msgbus_options
        )

        for ch in layer_stack.channels:
            bpy.msgbus.subscribe_rna(
                key=ch.path_resolve("hardness", False),
                owner=owners,
                args=(layer_stack_id,),
                notify=_rebuild_node_tree,
                options=msgbus_options
            )
            bpy.msgbus.subscribe_rna(
                key=ch.path_resolve("blend_mode", False),
                owner=owners,
                args=(layer_stack_id,),
                notify=_rebuild_node_tree,
                options=msgbus_options
            )

        for layer in layer_stack.layers:
            if layer.is_initialized:
                self._register_msgbus_layer(layer)

    def _register_msgbus_layer(self, layer):
        layer_stack_id = self.layer_stack.identifier
        layer_id = layer.identifier

        # The msgbus owner for the subscriptions to this layer
        owner = self._msgbus_owners[layer.identifier]

        msgbus_options = {'PERSISTENT'}

        bpy.msgbus.subscribe_rna(
            key=layer.path_resolve("enabled", False),
            owner=owner,
            notify=_rebuild_node_tree,
            args=(layer_stack_id,),
            options=msgbus_options
        )

        # Define a function since msgbus doesn't accept methods
        def layer_channels_changed(layer_id):
            # Avoid keeping python references to blender objects
            layer_stack = get_layer_stack_by_id(layer_stack_id)
            self = layer_stack.node_manager
            layer = layer_stack.get_layer_by_id(layer_id)

            self.rebuild_node_tree()
            self._unregister_msgbus_layer(layer_id)
            if layer is not None:
                self._register_msgbus_layer(layer)

        # Resubscribe RNA and rebuild the node tree when channels are
        # added or removed from the layer.
        bpy.msgbus.subscribe_rna(
            key=layer.channels,
            owner=owner,
            args=(layer_id,),
            notify=layer_channels_changed,
            options=msgbus_options
        )

        def update_blend_node(layer_id, ch_name):
            layer_stack = get_layer_stack_by_id(layer_stack_id)

            self = layer_stack.node_manager
            layer = layer_stack.get_layer_by_id(layer_id)
            if layer is None:
                return
            ch = layer.channels.get(ch_name)
            if ch is None:
                return

            self.update_blend_node(layer, ch)

        # Update the blend node when a layer's 'enabled' or 'blend_mode'
        # properties are changed.
        for ch in layer.channels:
            if ch.name in owner:
                continue

            ch_owner = owner[ch.name] = object()

            bpy.msgbus.subscribe_rna(
                key=ch.path_resolve("hardness", False),
                owner=ch_owner,
                args=(layer_stack_id,),
                notify=_rebuild_node_tree,
                options=msgbus_options
                )

            for key in ("enabled", "blend_mode"):
                bpy.msgbus.subscribe_rna(
                    key=ch.path_resolve(key, False),
                    owner=ch_owner,
                    args=(layer.identifier, ch.name),
                    notify=update_blend_node,
                    options=msgbus_options
                )

    def _unregister_msgbus(self):
        """Clear all RNA subscriptions for this node_manager.
        Safe to call even when this object has no subscriptions.
        """
        msgbus_owners = self._msgbus_owners

        bpy.msgbus.clear_by_owner(msgbus_owners)

        for layer_owner in msgbus_owners.values():
            bpy.msgbus.clear_by_owner(layer_owner)

            for ch_owner in layer_owner.values():
                bpy.msgbus.clear_by_owner(ch_owner)
        msgbus_owners.clear()

    def _unregister_msgbus_layer(self, layer) -> None:
        """Clear RNA subscriptions for this node_manager that relate
        to a specific layer.
        Params:
            layer: A MaterialLayer instance or its identifier
        """
        if isinstance(layer, str):
            layer_id = layer
        else:
            layer_id = layer.identifier

        msgbus_owners = self._msgbus_owners

        # The msgbus owner for the subscriptions to this layer
        owner = msgbus_owners.get(layer_id)

        if owner is not None:
            bpy.msgbus.clear_by_owner(owner)
            for ch_owner in owner.values():
                bpy.msgbus.clear_by_owner(ch_owner)

            del msgbus_owners[layer_id]

    def reregister_msgbus(self) -> None:
        self._unregister_msgbus()
        self._register_msgbus()

    def _initialize_node_tree(self) -> None:
        node_tree = self.node_tree

        if node_tree is None:
            raise RuntimeError("layer_stack.node_tree cannot be None")

        node_tree.inputs.clear()
        node_tree.outputs.clear()

        for ch in self.layer_stack.channels:
            node_tree.outputs.new(name=ch.name,
                                  type=ch.socket_type_bl_idname)

        self.rebuild_node_tree(True)

    def insert_layer(self, layer) -> None:
        self.rebuild_node_tree()
        self._register_msgbus_layer(layer)

    def remove_layer(self, layer_id: str) -> None:
        self._unregister_msgbus_layer(layer_id)
        self.rebuild_node_tree()

    def update_node_tree_sockets(self) -> None:
        """Adds, removes, and sets the type of the node tree's output
        sockets so they match the layer stack's channels.
        Does not rebuild the node tree.
        """
        # Ignore shader sockets e.g Node Wrangler's tmp_viewer sockets
        ensure_outputs_match_channels(self.node_tree.outputs,
                                      self.layer_stack.channels,
                                      ignore_shader=True)

    def rebuild_node_tree(self, immediate=False):
        """Rebuild the layer stack's internal node tree."""

        # layer_stack_id should have been set in initialize, but check
        # here for compatibility with old versions
        if "layer_stack_id" not in self:
            self["layer_stack_id"] = self.layer_stack.identifier

        if immediate or get_addon_preferences().debug_immediate_rebuild:
            self.rebuild_function()
        elif not bpy.app.timers.is_registered(self.rebuild_function):
            bpy.app.timers.register(self.rebuild_function)

    def set_active_layer(self, layer):
        layer_stack = self.layer_stack
        im = layer_stack.image_manager
        nodes = layer_stack.node_tree.nodes

        self.active_layer_image = im.active_image

        # Set the value of all is_active nodes to 0.0
        for x in layer_stack.top_level_layers:
            is_active = nodes.get(NodeNames.layer_is_active(x))
            if is_active is not None:
                is_active.outputs[0].default_value = 0.0

        # Set the active layer's is_active node's value to 1.0
        is_active = nodes.get(NodeNames.layer_is_active(layer))
        if is_active is not None:
            is_active.outputs[0].default_value = 1.0

    @property
    def active_layer_image(self) -> Optional[bpy.types.Image]:
        """The current Image in the active_layer_image node."""
        active_layer_node = self.nodes[NodeNames.active_layer_image()]
        return active_layer_node.image

    @active_layer_image.setter
    def active_layer_image(self, image: Optional[bpy.types.Image]) -> None:
        if image is None:
            # Use blank image instead
            image = self.layer_stack.image_manager.blank_image

        active_layer_node = self.nodes[NodeNames.active_layer_image()]
        active_layer_node.image = image

    @property
    def _msgbus_owners(self) -> typing.DefaultDict[str, dict]:
        """The msgbus owner dict for this object. A DefaultDict of
        layer identifiers to dicts.
        """
        layer_stack_id = self.layer_stack.identifier
        return self._cls_msgbus_owners[layer_stack_id]

    @property
    def links(self):
        return self.layer_stack.node_tree.links

    @property
    def layer_stack(self):
        return get_layer_stack_from_prop(self)

    @property
    def node_tree(self):
        return self.layer_stack.node_tree

    @property
    def nodes(self):
        return self.layer_stack.node_tree.nodes

    @property
    def rebuild_function(self) -> Callable[[], None]:
        """A function taking no arguments that can be called to rebuild
        the node tree. The returned function is safe to call even if the
        layer stack has been reallocated/deleted.
        """
        layer_stack_id = self.layer_stack.identifier
        fnc = self._rebuild_functions.get(layer_stack_id)
        if fnc is None:
            fnc = _rebuild_node_tree_factory(layer_stack_id)
            self._rebuild_functions[layer_stack_id] = fnc

        return fnc

    @property
    def _zero_const_output_socket(self):
        """The output socket of the zero_const node."""
        return self.nodes[NodeNames.zero_const()].outputs[0]

    @property
    def _one_const_output_socket(self):
        """The output socket of the one_const node."""
        return self.nodes[NodeNames.one_const()].outputs[0]


def _rebuild_node_tree_factory(layer_stack_id: str) -> Callable[[], None]:
    """Creates a function for rebuilding the layer stack with identifier
    layer_stack_id.
    The returned function takes no arguments, returns None and is always
    safe to call.
    """
    if not layer_stack_id:
        raise ValueError("layer_stack_id is empty")

    retry_count = 0

    def rebuild_node_tree() -> None:
        nonlocal retry_count

        layer_stack = get_layer_stack_by_id(layer_stack_id)

        if not layer_stack:
            return

        try:
            pml_node_tree.rebuild_node_tree(layer_stack)
        except pml_node_tree.RebuildContextError as e:
            if get_addon_preferences().debug_immediate_rebuild:
                raise e

            # Retry later if the current state prevents rebuilding
            retry_count += 1

            if retry_count > NodeManager.MAX_REBUILD_RETRIES:
                retry_count = 0
                raise RuntimeError("Retry limit exceeded trying to rebuild "
                                   f"node tree: {e}") from e

            bpy.app.timers.register(rebuild_node_tree,
                                    first_interval=0.01)
        else:
            retry_count = 0
    return rebuild_node_tree


def _rebuild_node_tree(layer_stack_id: str) -> None:
    """Rebuilds the node tree of the layer stack with the given id.
    For use as a msgbus callback.
    """
    layer_stack = get_layer_stack_by_id(layer_stack_id)
    if layer_stack:
        layer_stack.node_manager.rebuild_node_tree()


def register():
    bpy.utils.register_class(NodeManager)


def unregister():
    bpy.utils.unregister_class(NodeManager)
