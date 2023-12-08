# SPDX-License-Identifier: GPL-2.0-or-later

import contextlib
import typing

from typing import Optional, Sequence, Union

import bpy

from bpy.types import NodeTree

NodeSocketInterface = Union["bpy.types.NodeSocketInterface",
                            "bpy.types.NodeTreeInterfaceSocket"]

# Use NodeTree.interface instead of NodeTree.input and NodeTree.output
_use_interface = bpy.app.version > (4,)


def clear_node_tree_sockets(node_tree: NodeTree, in_out: str,
                            remove_empty_panels: bool = True) -> None:
    """Clears the sockets of node_tree's interface. in_out specifys
    which sockets should be cleared and can be 'INPUT', 'OUTPUT',
    or 'BOTH'. If remove_empty_panels is True then any panels that
    are now empty will be removed.
    """
    if in_out not in ('INPUT', 'OUTPUT', 'BOTH'):
        raise ValueError("in_out must be in {'INPUT', 'OUTPUT', 'BOTH'}")

    if _use_interface:
        interface = node_tree.interface
        for item in reversed(list(interface.items_tree)):
            if (item.item_type == 'SOCKET'
                    and in_out == 'BOTH' or item.in_out == in_out):
                interface.remove(item)
        if remove_empty_panels:
            for item in reversed(list(interface.items_tree)):
                if item.item_type == 'PANEL' and not item.interface_items:
                    interface.remove(item)
    else:
        if in_out != 'OUTPUT':
            node_tree.inputs.clear()
        if in_out != 'INPUT':
            node_tree.outputs.clear()


def ensure_outputs_match_channels(node_tree: bpy.types.NodeTree,
                                  channels: Sequence["BasicChannel"],
                                  ignore_shader: bool = True) -> None:
    """Adds, removes, sets the type of, and reorders node_tree's output
    sockets so they match channels.
    Params:
        node_tree: Modifiy the output sockets of this node tree.
        channels: A sequence of BasicChannel instances, as found in
            LayerStack.channels or MaterialLayer.channels.
        ignore_shader: Don't remove shader sockets. These will be moved
            to the end of the socket collection.
    Returns:
        None
    """

    for idx, ch in enumerate(channels):
        output = get_node_tree_socket(node_tree, ch.name, 'OUTPUT')

        if output is None:
            # If no output is found then create one
            output = new_node_tree_socket(node_tree, ch.name, 'OUTPUT',
                                          ch.socket_type_bl_idname)

        elif node_tree_socket_type(output) != ch.socket_type_bl_enum:
            set_node_tree_socket_type(output, ch.socket_type_bl_enum)

        if ch.socket_type == 'VECTOR':
            output.hide_value = True
        elif ch.socket_type == 'FLOAT_FACTOR':
            output.min_value = 0.0
            output.max_value = 1.0

        # Order the outputs so that they are the same as in channels
        if not _use_interface or output.position != idx:
            move_node_tree_socket(node_tree, output, idx)

    # As the ordering of outputs is the same as in channels
    # any outputs that are not in channels will have been pushed to
    # the back of outputs
    for output in reversed(get_node_tree_sockets(node_tree, 'OUTPUT')):
        if ignore_shader and node_tree_socket_type(output) == 'SHADER':
            continue

        output_check = get_node_tree_socket(node_tree, output.name, 'OUTPUT')

        # Delete if not in channels or is a duplicate channel
        if output.name not in channels or output_check != output:
            remove_node_tree_socket(node_tree, output)
        else:
            break


def get_node_tree_socket(node_tree: NodeTree, name: str, in_out: str
                         ) -> Optional[NodeSocketInterface]:
    """Returns an interface socket with name 'name' from node_tree. Compatible
    with both Blender 3 and Blender 4.
    Params:
        node_tree: A NodeTree to get the interface socket from.
        name: The name of the socket to find.
        in_out: str in {'INPUT', 'OUTPUT'} whether to look for an input
            or output interface socket.
    Returns:
        A NodeSocketInterface or NodeTreeInterfaceSocket if the socket
        was found. Otherwise None.
    """
    if in_out not in ('INPUT', 'OUTPUT'):
        raise ValueError("in_out must be either 'INPUT' or 'OUTPUT'")

    if _use_interface:
        existing = node_tree.interface.items_tree.get(name)
        if existing is None:
            return None
        elif existing.item_type == 'SOCKET' and existing.in_out == in_out:
            return existing

        # In case of items with duplicate name may have to search all items
        return next((x for x in node_tree.interface.items_tree
                     if x.name == name and x.item_type == 'SOCKET'
                     and x.in_out == in_out),
                    None)
    else:
        collection = (node_tree.outputs
                      if in_out == 'OUTPUT' else node_tree.inputs)
        return collection.get(name)


def get_node_tree_sockets(node_tree: NodeTree,
                          in_out: str) -> list[NodeSocketInterface]:
    """Returns a list of the inputs or outputs of node_tree.
    in_out should be a string in {'INPUT', 'OUTPUT'}.
    Compatible with both Blender 3 and Blender 4.
    """
    if in_out not in ('INPUT', 'OUTPUT'):
        raise ValueError("in_out must be either 'INPUT' or 'OUTPUT'")
    if _use_interface:
        # N.B. Assumes that items in items_tree are always sorted
        # by position attribute
        return [x for x in node_tree.interface.items_tree
                if x.item_type == 'SOCKET' and x.in_out == in_out]
    return list(node_tree.outputs if in_out == 'OUTPUT'
                else node_tree.inputs)


def _simplify_socket_type(socket_type: str) -> str:
    """Attempts to simplify socket_type into a value compatible with
    both NodeTreeInterface.new_socket and NodeTreeInputs/NodeTreeOutputs.
    """

    if socket_type.isupper():
        return _socket_types_map.get(socket_type, None) or socket_type

    for x in ("NodeSocketColor", "NodeSocketFloat", "NodeSocketVector"):
        if socket_type.startswith(x):
            return x
    return socket_type


def new_node_tree_socket(node_tree: NodeTree, name: str, in_out: str,
                         socket_type: str, description: str = "",
                         parent=None) -> NodeSocketInterface:
    """Creates and returns an interface socket on node_tree. Compatible
    with both Blender 3 and Blender 4. Arguments are the same as in
    NodeTreeInterface.new_socket.
    description and parent are ignored in Blender 3.
    """
    if in_out not in ('INPUT', 'OUTPUT'):
        raise ValueError("in_out must be either 'INPUT' or 'OUTPUT'")

    socket_type = _simplify_socket_type(socket_type)

    if _use_interface:
        return node_tree.interface.new_socket(name, description=description,
                                              in_out=in_out,
                                              socket_type=socket_type,
                                              parent=parent)
    else:
        sockets = node_tree.outputs if in_out == 'OUTPUT' else node_tree.inputs
        return sockets.new(socket_type, name)


def node_tree_socket_type(socket: NodeSocketInterface) -> str:
    if _use_interface:
        bl_type = socket.socket_type
        if "Float" in bl_type:
            return 'FLOAT'
        if "Color" in bl_type:
            return 'RGBA'
        if "Vector" in bl_type:
            return 'VECTOR'
        if "Shader" in bl_type:
            return 'SHADER'
        return 'UNKNOWN'
    else:
        return socket.type


def set_node_tree_socket_type(socket, socket_type):

    if _use_interface:
        socket_type = socket_type_to_interface_type(socket_type)

        if socket_type is None:
            return
        socket.socket_type = socket_type
    else:
        if socket_type != 'UNKNOWN':
            socket.type = socket_type


_socket_types_map = {
    'VALUE': "NodeSocketFloat",
    'RGBA': "NodeSocketColor",
    'SHADER': "NodeSocketShader",
    'VECTOR': "NodeSocketVector",
    'UNKNOWN': None
}


def socket_type_to_interface_type(socket_type: str) -> str:
    try:
        return _socket_types_map[socket_type]
    except KeyError as e:
        raise ValueError("socket_type must be a string in "
                         f"{set(_socket_types_map.keys())}") from e


def move_node_tree_socket(node_tree: NodeTree,
                          socket: NodeSocketInterface,
                          new_pos: int) -> None:
    """Change the position of a node tree's interface socket.
    Params:
        node_tree: The node_tree containing socket.
        socket: The socket to move.
        new_pos: The position to move the socket to relative to others
            with the same in_out. E.g. if socket is an input and
            new_pos is 0 then it will now be position before any other
            input sockets, but after any output sockets.
    """
    if _use_interface:
        offset = 0
        if socket.item_type == 'PANEL':
            # Panels always come after sockets
            offset = len([x for x in socket.parent.interface_items
                          if x.item_type == 'SOCKET'])
        elif socket.in_out == 'INPUT':
            # Inputs always come after outputs so offset by number of outputs
            offset += len([x for x in socket.parent.interface_items
                           if x.item_type == 'SOCKET'
                           and x.in_out == 'OUTPUT'])
        node_tree.interface.move(socket, offset + new_pos)
    else:
        socket_col = (node_tree.outputs if socket.is_output
                      else node_tree.inputs)
        current_idx = socket_col.find(socket.name)

        if current_idx < 0:
            raise KeyError("socket not found in node_tree's interface")
        if socket_col[current_idx] != socket:
            current_idx = next(idx for idx, soc in enumerate(socket_col)
                               if soc == socket)
        if current_idx != new_pos:
            socket_col.move(current_idx, new_pos)


def remove_node_tree_socket(node_tree: NodeTree,
                            socket: NodeSocketInterface) -> None:
    """Removes an interface socket from node_tree. Does nothing if the
    socket is not found. Compatible with both Blender 3 and Blender 4.
    """
    if _use_interface:
        if not socket.item_type == 'SOCKET':
            raise TypeError(f"{socket.item_type} item given. Expected SOCKET")
        node_tree.interface.remove(socket)
    else:
        sockets = node_tree.outputs if socket.is_output else node_tree.inputs
        with contextlib.suppress(RuntimeError):
            sockets.remove(socket)


def sort_outputs_by(node_tree: NodeTree,
                    reference: typing.Collection) -> None:
    """Sorts the outputs of node_tree so that their order matches the
    order of reference using the name attribute to determine identity.
    Params:
        node_tree: The node tree.
        reference: The collection to use for reference may be another
            collection of sockets or a collection of channels etc.
    """
    # N.B. Assumes no two sockets have the same name
    ref_indices = {x.name: idx for idx, x in enumerate(reference)}
    len_refs = len(reference)

    # List of sockets sorted by the index in 'reference'. Any sockets
    # not found in ref_indices should be at the back of the list.
    sockets = get_node_tree_sockets(node_tree, 'OUTPUT')
    sockets_sorted = sorted(sockets,
                            key=lambda x: ref_indices.get(x.name, len_refs))

    for target_idx, socket in enumerate(sockets_sorted):
        move_node_tree_socket(node_tree, socket, target_idx)
