# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from collections import defaultdict
from typing import List, Optional
from warnings import warn

import bpy

from bpy.props import StringProperty

from bpy.types import ShaderNode, ShaderNodeCustomGroup, ShaderNodeTree

from .on_load_manager import pml_trusted_callback
from .utils.layer_stack_utils import (get_layer_stack_from_ma,
                                      get_layer_stack_by_id)
from .utils.naming import unique_name_in
from .utils.nodes import get_closest_node_of_type, get_output_node


# Cache of node names to identifiers
_node_id_name_cache: dict[str, str] = {}


def _get_node(layer_stack_id: str, node_id: str) -> ShaderNodePMLStack:
    """Gets a node with the given identifier from the node tree of
    a layer stack's material.
    """
    layer_stack = get_layer_stack_by_id(layer_stack_id)

    if not layer_stack or not layer_stack.is_initialized:
        return None

    ma = layer_stack.material
    if not ma.node_tree:
        return None

    # Check the cache first
    cached_name = _node_id_name_cache.get(node_id)
    if cached_name is not None:
        found = ma.node_tree.nodes.get(cached_name)
        if found is not None and getattr(found, "identifier") == node_id:
            return found

        del _node_id_name_cache[node_id]

    found = _get_node_by_id(ma.node_tree, node_id)
    if found is not None:
        _node_id_name_cache[node_id] = found.name
        return found

    # Search in any group nodes
    for node in ma.node_tree.nodes:
        if (not isinstance(node, bpy.types.ShaderNodeGroup)
                or node.node_tree is None):
            continue
        found = _get_node_by_id(node.node_tree, node_id)
        if found is not None:
            return found

    return None


def _get_node_by_id(node_tree: ShaderNodeTree,
                    node_id: str) -> Optional[ShaderNode]:
    for node in node_tree.nodes:
        if getattr(node, "identifier", None) == node_id:
            return node
    return None


class ShaderNodePMLStack(ShaderNodeCustomGroup):
    bl_idname = 'ShaderNodePMLStack'
    bl_label = "Material Layers"

    identifier: StringProperty(
        name="Identifier"
    )

    _msgbus_owners_cls = defaultdict(object)

    @staticmethod
    def _reregister_msgbus(layer_stack_id: str, node_id: str) -> None:
        node = _get_node(layer_stack_id, node_id)
        if node is None:
            return

        node._unregister_msgbus()
        node._register_msgbus()

    def init(self, context) -> None:
        if context is None:
            context = bpy.context

        ma = context.active_object.active_material
        self["material"] = ma

        self.identifier = unique_name_in(
            set(getattr(x, "identifier", None) for x in self.id_tree.nodes),
            num_bytes=4)

        layer_stack = get_layer_stack_from_ma(ma)
        if not layer_stack.is_initialized:
            warn("ShaderNodePMLStack created without an initialized "
                 " layer stack")
            return

        self.node_tree = layer_stack.node_tree

        for output in self.outputs:
            pml_channel = layer_stack.channels[output.name]
            output.enabled = pml_channel.enabled

        self._register_msgbus()

        cb_id = layer_stack.add_on_load_callback(self._register_msgbus)
        self["on_load_id"] = cb_id
        assert cb_id

        cb_id = layer_stack.add_msgbus_resub_callback(
            self._reregister_msgbus,
            (layer_stack.identifier, self.identifier)
        )
        self["on_resub_id"] = cb_id
        assert cb_id

    def free(self) -> None:
        self._unregister_msgbus()

        layer_stack = self.layer_stack
        layer_stack.remove_on_load_callback(self.get("on_load_id", ""))
        layer_stack.remove_msgbus_resub_callback(self.get("on_resub_id", ""))

    def draw_buttons(self, _context, layout):
        if not self._is_valid:
            layout.label(icon='ERROR', text="Node is invalid")
            return
        layout.context_pointer_set("pml_layer_stack", self.layer_stack)
        layout.menu("PML_MT_open_layer_group")

    def _delayed_refresh_disabled_outputs(self):
        """Registers a timer that disables outputs of this node
        to match the disabled channels of the layer stack.
        """
        layer_stack_id = self.layer_stack.identifier
        node_id = self.identifier

        def set_outputs_enabled():
            self = _get_node(layer_stack_id, node_id)
            if self is None:
                return

            disabled_channels = {x.name for x in self.layer_stack.channels
                                 if not x.enabled}
            for x in self.outputs:
                if x.name in disabled_channels:
                    x.enabled = False
        bpy.app.timers.register(set_outputs_enabled)

    def update(self):
        # Bug in Blender version 3.5.0 where sockets are re-enabled on
        # node graph updates. So refresh the enabled state of sockets
        # after a small delay
        if bpy.app.version >= (3, 5, 0):
            self._delayed_refresh_disabled_outputs()

    @pml_trusted_callback
    def _register_msgbus(self) -> None:
        layer_stack = self.layer_stack
        owner = self._msgbus_owner

        layer_stack_id = layer_stack.identifier
        node_id = self.identifier

        bpy.msgbus.subscribe_rna(
            key=layer_stack.channels,
            owner=owner,
            args=(layer_stack_id, node_id),
            notify=self._reregister_msgbus,
            options={'PERSISTENT'}
        )

        def refresh_output_hidden(name):
            self = _get_node(layer_stack_id, node_id)
            if self is not None:
                self._refresh_output_hidden(name)

        for output in self.outputs:
            pml_channel = layer_stack.channels.get(output.name)

            if pml_channel is not None:
                bpy.msgbus.subscribe_rna(
                    key=pml_channel.path_resolve("enabled", False),
                    owner=owner,
                    args=(output.name,),
                    notify=refresh_output_hidden,
                    options={'PERSISTENT'}
                )

    def _unregister_msgbus(self):
        bpy.msgbus.clear_by_owner(self._msgbus_owner)

    def reregister_msgbus(self) -> None:
        self._unregister_msgbus()
        self._register_msgbus()

    def connect_outputs(self,
                        node: ShaderNode,
                        replace: bool = False) -> None:
        """Links this node's outputs with the corresponding inputs of
           another node (uses socket names).

           Params:
                node: The node to create links to.
                replace: If True replace existing links, otherwise only
                    create links to unlinked sockets.
        """
        layer_stack_chs = self.layer_stack.channels
        links = self.id_tree.links

        for output in self.outputs:
            if (not output.enabled
                    or output.name not in layer_stack_chs
                    or not layer_stack_chs[output.name].enabled):
                continue

            to_input = node.inputs.get(output.name)
            if to_input is not None:
                if replace or not to_input.is_linked:
                    links.new(to_input, output)

    def _refresh_output_hidden(self, name: str) -> None:
        layer_stack = self.layer_stack
        node_tree = self.id_tree

        out_socket = self.outputs.get(name)
        stack_ch = layer_stack.channels.get(name)

        if out_socket is not None and stack_ch is not None:
            out_socket.enabled = stack_ch.enabled

            if (layer_stack.auto_connect_shader
                    and out_socket.enabled
                    and not out_socket.is_linked):
                in_socket = self._find_socket_to_link_to(name)

                if in_socket is not None and not in_socket.is_linked:
                    node_tree.links.new(in_socket, out_socket)

    def _find_socket_to_link_to(self, name: str) -> Optional[ShaderNode]:
        """Finds an input socket on the closest node that the layer
        stack can"""
        layer_stack = self.layer_stack

        sh_node = get_closest_node_of_type(self,
                                           layer_stack.shader_node_type,
                                           layer_stack.group_to_connect)

        if sh_node is not None:
            socket = sh_node.inputs.get(name)
            if socket is not None:
                return socket

        # Look for a socket on a Material Output node
        out_node = get_output_node(self.id_tree)
        return None if out_node is None else out_node.inputs.get(name)

    @property
    def id_tree(self) -> ShaderNodeTree:
        """The ShaderNodeTree containing this node."""
        return self.id_data

    @property
    def layer_stack(self):
        return get_layer_stack_from_ma(self["material"])

    @property
    def _is_valid(self) -> bool:
        ma = self.get("material")
        return ma is not None and get_layer_stack_from_ma(ma)

    @property
    def _msgbus_owner(self):
        return self._msgbus_owners_cls[self.identifier]


def get_pml_nodes(layer_stack,
                  check_groups: bool = False) -> List[ShaderNodePMLStack]:
    """Returns a list of all Layer Stack nodes in the layer_stack's
    material that use layer_stack. If check_groups is True then Group
    nodes are also checked.
    """
    node_tree = layer_stack.material.node_tree
    if node_tree is None:
        return []
    return get_pml_nodes_from(node_tree, layer_stack, check_groups)


def get_pml_nodes_from(node_tree: ShaderNodeTree,
                       layer_stack,
                       check_groups: bool = False) -> List[ShaderNodePMLStack]:
    """Gets all Layer Stack nodes in node_tree that use layer_stack.
    If check_groups is True then also check the node trees of any Group
    Nodes in node_tree.
    """
    pml_id_name = ShaderNodePMLStack.bl_idname

    if not layer_stack.is_initialized:
        return []

    pml_nodes = []

    for node in node_tree.nodes:
        node_type_str = node.bl_rna.identifier

        if node_type_str == pml_id_name and node.layer_stack == layer_stack:
            pml_nodes.append(node)
        elif (node_type_str == "ShaderNodeGroup"
                and node.node_tree is not None and check_groups):
            pml_nodes += get_pml_nodes_from(node.node_tree, layer_stack, True)

    return pml_nodes


# Reregistering ShaderNodePMLStack can cause crashes if there is a panel
# from the add-on visible. So refuse to unregister the class whilst
# there are initialized pml_layer_stacks
if "_registered_info" not in globals():
    _registered_info = {"is_registered": False,
                        "PML_Node_Class": None}


def register():
    if not _registered_info["is_registered"]:
        bpy.utils.register_class(ShaderNodePMLStack)
        _registered_info["is_registered"] = True
        _registered_info["PML_Node_Class"] = ShaderNodePMLStack


def unregister():
    if not any(ma.pml_layer_stack for ma in bpy.data.materials):
        PML_Node_Class = _registered_info["PML_Node_Class"]
        if PML_Node_Class is not None:
            bpy.utils.unregister_class(PML_Node_Class)
        _registered_info["is_registered"] = False
