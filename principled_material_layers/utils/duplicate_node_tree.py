# SPDX-License-Identifier: GPL-2.0-or-later

import collections

from typing import Optional

import bpy

from bpy.types import (bpy_prop_collection,
                       Node,
                       NodeSocket,
                       ShaderNodeTree)


def _copy_prop_collection(from_col: bpy_prop_collection,
                          to_col: bpy_prop_collection) -> None:

    # Add or remove elements from to_col until it's the same size as from_col
    if len(to_col) != len(from_col):

        if len(to_col) < len(from_col):
            if not hasattr(to_col, "new"):
                raise RuntimeError("Expected bpy_prop_collection to have a "
                                   "'new' method.")

            new_params = to_col.bl_rna.functions["new"].parameters
            default_args = {x.identifier: x.default for x in new_params
                            if not isinstance(x, bpy.types.PointerProperty)}

            while len(to_col) < len(from_col):
                to_col.new(**default_args)
        else:
            while len(to_col) > len(from_col):
                to_col.remove(to_col[0])

    for from_elmt, to_elmt in zip(from_col, to_col):
        _copy_props(from_elmt, to_elmt)


def _copy_props(from_struct, to_struct, exclude_props=None) -> None:
    # TODO Recursively record and exclude all objects previously copied
    # to prevent recursion errors
    exclude = {"data", "id_data", "internal_links", "links", "node",
               "rna_type"}
    if exclude_props is not None:
        exclude.update(exclude_props)

    if isinstance(from_struct, bpy.types.bpy_prop_collection):
        _copy_prop_collection(from_struct, to_struct)
        return

    not_found = object()
    to_retry = []

    for prop_info in from_struct.bl_rna.properties:
        attr_name = prop_info.identifier

        if (attr_name.startswith("bl_")
                or attr_name.startswith("_")
                or attr_name in exclude):
            continue

        from_value = getattr(from_struct, attr_name, not_found)
        if from_value is not_found:
            raise RuntimeError(f"Could not find value {attr_name} in "
                               f"{type()}")
        if from_value is from_struct:
            continue

        if prop_info.is_readonly:
            to_value = getattr(to_struct, attr_name)

            if isinstance(from_value, bpy.types.bpy_struct):
                _copy_props(from_value, to_value)

            elif isinstance(from_value, bpy.types.bpy_prop_collection):
                _copy_prop_collection(from_value, to_value)
        else:
            try:
                setattr(to_struct, attr_name, from_value)
            except (ValueError, TypeError):
                to_retry.append((attr_name, from_value))

    # In case settng the value depends on another value being set
    for attr_name, from_value in to_retry:
        setattr(to_struct, attr_name, from_value)


def _copy_node_props(from_node: Node, to_node: Node) -> None:
    if type(from_node) is not type(to_node):
        raise TypeError("Nodes must be of the same type.")

    exclude = {"id_data", "internal_links", "parent", "inputs", "outputs"}

    if hasattr(from_node, "node_tree"):
        to_node.node_tree = from_node.node_tree

    _copy_props(from_node, to_node, exclude_props=exclude)

    if isinstance(from_node, bpy.types.NodeReroute):
        # Handle reroutes as a special case since the type of their
        # sockets' default value depends on the type of the socket

        to_node.inputs[0].type = from_node.inputs[0].type
        to_node.outputs[0].type = from_node.outputs[0].type
        to_node.inputs[0].default_value = from_node.inputs[0].default_value
        to_node.outputs[0].default_value = from_node.outputs[0].default_value
    else:
        _copy_prop_collection(from_node.inputs, to_node.inputs)
        _copy_prop_collection(from_node.outputs, to_node.outputs)

    # Set the parent if it exists
    if from_node.parent is not None:
        to_node_tree = to_node.id_data
        to_node.parent = to_node_tree.nodes.get(from_node.parent.name, None)

    # For nodes with a CurveMapping
    if hasattr(to_node, "mapping") and hasattr(to_node.mapping, "update"):
        to_node.mapping.update()


def _num_ancestors(node: Node) -> int:
    """The number of ancestors this node has. 0 if the node has no parent,
    1 if the node has a parent but no grandparent etc.
    """
    count = 0
    while node.parent is not None and count < 10000:
        count += 1
        node = node.parent
    return count


def _get_matching_socket(socket_to_match, sockets) -> Optional[NodeSocket]:
    """Returns a socket on 'sockets' that matches 'socket_to_match'"""
    if len(sockets) == 1:
        return sockets[0]
    found = sockets.get(socket_to_match.identifier)
    if found is None:
        found = sockets.get(socket_to_match.name)
    return found


def duplicate_node_tree(node_tree: ShaderNodeTree) -> ShaderNodeTree:

    to_tree = bpy.data.node_groups.new(node_tree.name, "ShaderNodeTree")

    # Ensure parent nodes are processed first
    from_nodes = sorted(node_tree.nodes, key=_num_ancestors)

    for node in from_nodes:
        new_node = to_tree.nodes.new(node.bl_idname)
        _copy_node_props(node, new_node)

    # DefaultDict of node names to a list of links to their input sockets
    input_links_cache = collections.defaultdict(list)
    for link in node_tree.links:
        input_links_cache[link.to_node.name].append(link)

    for new_node in to_tree.nodes:
        for link in input_links_cache[new_node.name]:
            from_node = to_tree.nodes[link.from_node.name]
            from_socket = _get_matching_socket(link.from_socket,
                                               from_node.outputs)

            to_socket = _get_matching_socket(link.to_socket, new_node.inputs)
            if to_socket is not None and from_socket is not None:
                to_tree.links.new(to_socket, from_socket)

    # TODO node_tree.animation_data?
    return to_tree
