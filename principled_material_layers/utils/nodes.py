# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import itertools as it
import math
import typing

from collections.abc import Collection, Container, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Callable, List, NamedTuple, Optional, Tuple, Union

import bpy

from bpy.types import (Node,
                       NodeSocket,
                       NodeSocketInterface,
                       NodeTree,
                       ShaderNode,
                       ShaderNodeTree)
from mathutils import Vector

from .temp_changes import TempNodes


# Nodes that are relatively inexpensive to compute
# Note that this is not measured, but just guessed from the nodes' glsl
# code in source/blender/gpu/shaders/material
_SIMPLE_NODES = {
    "ShaderNodeBlackbody",
    "ShaderNodeBrightContrast",
    "ShaderNodeClamp",
    "ShaderNodeCombineHSV",
    "ShaderNodeCombineRGB",
    "ShaderNodeCombineXYZ",
    "ShaderNodeHueSaturation",
    "ShaderNodeInvert",
    "ShaderNodeLightPath",
    "ShaderNodeMapping",
    "ShaderNodeMapRange",
    "ShaderNodeMath",
    "ShaderNodeMixRGB",
    "ShaderNodeObjectInfo",
    "ShaderNodeRGB",
    "ShaderNodeRGBToBW",
    "ShaderNodeSeparateColor",
    "ShaderNodeSeparateHSV",
    "ShaderNodeSeparateRGB",
    "ShaderNodeSeparateXYZ",
    "ShaderNodeTangent",
    "ShaderNodeTexCoord",
    "ShaderNodeUVMap",
    "ShaderNodeVectorMath",
}


def _get_node_simplicity(node: ShaderNode,
                         threshold: int,
                         ignore: Optional[typing.Set[ShaderNode]] = None,
                         ) -> int:

    if node.rna_type.identifier not in _SIMPLE_NODES:
        return threshold + 1

    if ignore is None:
        ignore = {node}
    else:
        ignore.add(node)

    simplicity = 1

    for socket in node.inputs:
        if socket.is_linked:
            linked_node = socket.links[0].from_node
            if linked_node not in ignore:
                simplicity += _get_node_simplicity(linked_node,
                                                   threshold,
                                                   ignore)
                if simplicity > threshold:
                    break
    return simplicity


def is_socket_simple(socket: NodeSocket,
                     threshold: int = 8) -> bool:
    """Approximates whether a shader node socket's value is
    computationally inexpensive. Returns True if the socket is only
    influenced by at most 'threshold' nodes, none of which perform
    expensive operations.

    Params:
        socket: An input or output socket of a shader node.
        threshold: The max number of 'simple' nodes a socket can be
            influenced by before this function returns False.
    Returns:
        A boolean.
    """
    if socket.is_output:
        if isinstance(socket.node, bpy.types.ShaderNodeGroup):
            # For Group Nodes return whether the socket on the Group
            # Output node is simple or True if there is no such node.
            node_tree = socket.node.node_tree
            if socket.node.node_tree is None:
                return True
            output_node = get_node_by_type(node_tree,
                                           bpy.types.NodeGroupOutput)
            if output_node is None:
                return True
            return is_socket_simple(output_node.inputs[socket.name], threshold)

        # Will return whether the socket's node is a simple node
        node = socket.node
    else:
        # socket is an input
        if not socket.is_linked:
            return True

        node = socket.links[0].from_node

    return _get_node_simplicity(node, threshold) <= threshold


def get_output_node(node_tree: ShaderNodeTree):
    for x in ('ALL', 'EEVEE', 'CYCLES'):
        output = node_tree.get_output_node(x)
        if output is not None:
            return output
    return None


def get_nodes_by_type(node_tree: NodeTree,
                      node_type: Union[str, type]) -> Iterator[Node]:
    """Returns an iterator over all nodes of the given type in
    node_tree.
    """
    if isinstance(node_type, str):
        return (x for x in node_tree.nodes if x.bl_idname == node_type)

    return (x for x in node_tree.nodes if isinstance(x, node_type))


def get_node_by_type(node_tree: NodeTree,
                     node_type: Union[str, type]) -> Optional[Node]:
    """Returns the first node with the given type or None if no nodes
    in node_tree have this type.
    """
    return next(get_nodes_by_type(node_tree, node_type), None)


def get_closest_node_of_type(closest_to: Node,
                             node_type: Union[str, type],
                             group_tree: Optional[NodeTree] = None
                             ) -> Optional[Node]:
    node_tree = closest_to.id_data

    if group_tree:
        nodes = [x for x in get_nodes_by_type(node_tree, node_type)
                 if x.node_tree is group_tree]
    else:
        nodes = get_nodes_by_type(node_tree, node_type)

    closest_to_loc = closest_to.location
    return min(nodes, key=lambda x: x.location - closest_to_loc, default=None)


def delete_nodes_not_in(nodes: bpy.types.Nodes,
                        container: Container[Node]) -> None:
    """Delete any nodes not in container"""
    container = set(container)
    to_remove = [x for x in nodes if x not in container]

    for node in to_remove:
        nodes.remove(node)


@dataclass
class Rect:
    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width

    @right.setter
    def right(self, value) -> None:
        self.width = value - self.left

    @property
    def bottom(self) -> float:
        return self.top - self.height

    @bottom.setter
    def bottom(self, value) -> None:
        self.height = self.top - value


def nodes_bounding_box(nodes: Collection[Node]) -> Rect:
    if not nodes:
        return Rect(0, 0, 0, 0)

    box = None

    for node in nodes:
        left, top = node.location
        width, height = node.dimensions
        right = left + width
        bottom = top - height

        if box is None:
            box = Rect(left, top, width, height)
            continue

        if left < box.left:
            box.left = left
        elif right > box.right:
            box.right = right
        if top > box.top:
            box.top = top
        elif bottom < box.bottom:
            box.bottom = bottom
    return box


def ensure_outputs_match_channels(outputs: bpy.types.NodeTreeOutputs,
                                  channels: Sequence["BasicChannel"]) -> None:
    """Adds, removes, sets the type of, and reorders the sockets in
    outputs so they match channels.
    Params:
        outputs: NodeTreeOutputs (a collection of NodeSocketInterface)
        channels: A sequence of BasicChannel instances, as found in
            LayerStack.channels or MaterialLayer.channels.
    Returns:
        None
    """
    for idx, ch in enumerate(channels):
        output_idx = outputs.find(ch.name)
        # output_idx == -1 if ch.name not found

        if output_idx != -1:
            output = outputs[output_idx]
            if output.type != ch.socket_type_bl_enum:
                # Convert the existing output if it has the wrong type
                output.type = ch.socket_type_bl_enum
        else:
            # If no output is found then create one
            output = outputs.new(name=ch.name,
                                 type=ch.socket_type_bl_idname)
            output_idx = outputs.find(output.name)

        if ch.socket_type == 'VECTOR':
            output.hide_value = True
        elif ch.socket_type == 'FLOAT_FACTOR':
            output.min_value = 0.0
            output.max_value = 1.0

        # Order the outputs so that they are the same as in channels
        if output_idx != idx:
            outputs.move(output_idx, idx)

    # As the ordering of outputs is the same as in channels
    # any outputs that are not in channels will have been pushed to
    # the back of outputs
    for output in reversed(outputs):
        # Delete if not in channels or is a duplicate channel
        if output.name not in channels or outputs[output.name] != output:
            outputs.remove(output)
        else:
            break


def set_node_group_vector_defaults(node_group: ShaderNodeTree):
    """Link any unconnected normal or tangent group outputs to
    Texture Coordinate or Tangent nodes so that they have the same value
    they would have if left unconnected in a material's node tree.
    """

    # Do for all Group Output nodes in the node tree
    for group_out in get_nodes_by_type(node_group, "NodeGroupOutput"):
        # Only create defaults for normals or tangents
        normal_sockets = []
        tangent_sockets = []

        # Find any sockets needing a default
        for socket in group_out.inputs:
            if socket.type != 'VECTOR' or socket.is_linked:
                continue

            socket_name = socket.name.casefold()
            if "normal" in socket_name:
                normal_sockets.append(socket)
            elif "tangent" in socket_name:
                tangent_sockets.append(socket)

        if normal_sockets:
            default_normals = _ensure_default_normals_socket(node_group)

            for socket in normal_sockets:
                node_group.links.new(socket, default_normals)

        if tangent_sockets:
            default_tangents = _ensure_default_tangents_socket(node_group)

            for socket in tangent_sockets:
                node_group.links.new(socket, default_tangents)


def group_output_link_default(socket: NodeSocketInterface) -> None:
    """Link each Group Output node socket matching the
    NodeSocketInterface socket to a node that provides them with a
    correct default value.
    """
    if socket.type != 'VECTOR':
        return

    node_tree = socket.id_data
    assert isinstance(node_tree, ShaderNodeTree)

    for group_out in get_nodes_by_type(node_tree, "NodeGroupOutput"):
        out_socket = group_out.inputs[socket.name]
        vector_socket_link_default(out_socket)


def vector_socket_link_default(socket: NodeSocket) -> None:
    """Link an unconnected normal or tangent input socket to a node that
    provides them with a correct default value. This is for use with
    the inputs of Group Output nodes so that they have the same value
    they would have if left unconnected in a material's node tree.
    """
    if socket.is_output:
        raise ValueError("Expected an input socket.")

    if socket.type != 'VECTOR' or socket.is_linked:
        return

    node_tree = socket.id_data
    assert isinstance(node_tree, ShaderNodeTree)

    socket_name = socket.name.casefold()

    if "normal" in socket_name:
        default_vec_socket = _ensure_default_normals_socket(node_tree)
    elif "tangent" in socket_name:
        default_vec_socket = _ensure_default_tangents_socket(node_tree)
    else:
        return

    node_tree.links.new(socket, default_vec_socket)


# TODO rename?
def vector_socket_link_default_generic(socket) -> ShaderNode:
    """Links an unconnected normal or tangent input socket to a new
    node that provides them with a correct default value. Returns the
    new node or None if no node was necesscary.
    """
    node_tree = socket.id_data
    socket_name_lower = socket.name.lower()

    node = None

    if "normal" in socket_name_lower:
        node = node_tree.nodes.new("ShaderNodeTexCoord")
        node.label = "Normal"
        for x in node.outputs:
            if "normal" not in x.name.lower():
                x.hide = True
            else:
                node_tree.links.new(socket, x)

    elif "tangent" in socket_name_lower:
        node = node_tree.nodes.new("ShaderNodeTangent")
        node.label = "Default Tangent"
        node_tree.links.new(socket, node.outputs[0])
    else:
        return None

    node.hide = True
    return node


def _get_group_output_bottom_left(node_tree: ShaderNodeTree) -> Vector:
    """Returns the position of the bottom left of the first Group Output
    node in node_tree as a 2D Vector.
    """
    group_out = get_node_by_type(node_tree, bpy.types.NodeGroupOutput)
    if group_out is None:
        return Vector((0., 0.))

    group_out_loc = group_out.location
    group_out_height = group_out.dimensions.y
    if group_out_height == 0:
        # Approximate the height of the node
        group_out_height = len(group_out.inputs) * 22 + 50

    return Vector((group_out_loc.x, group_out_loc.y - group_out_height))


def _ensure_default_normals_socket(node_tree: ShaderNodeTree) -> NodeSocket:
    """Ensures a node that provides a default value for normals exists
    in node_tree and returns the relevent output socket of that node.
    """
    # The name attribute of the node to use for default normals
    default_normals_name = "default_normals"

    # Check for an existing node first
    existing = node_tree.nodes.get(default_normals_name)
    if existing:
        return existing.outputs["Normal"]

    # Create a new Texture Coordinate node
    coord_node = node_tree.nodes.new("ShaderNodeTexCoord")
    coord_node.name = default_normals_name
    coord_node.label = "Default Normals"
    coord_node.hide = True

    # Place the node near the bottom left of the Group Output node
    align_to = _get_group_output_bottom_left(node_tree)
    coord_node.location = (align_to.x - coord_node.width - 80, align_to.y + 50)

    for output in coord_node.outputs:
        if output.name != "Normal":
            output.hide = True

    return coord_node.outputs["Normal"]


def _ensure_default_tangents_socket(node_tree: ShaderNodeTree) -> NodeSocket:
    """Ensures a node that provides a default value for normals exists
    in node_tree and returns the relevent output socket of that node.
    """
    # The name attribute of the node to use for default normals
    default_tangents_name = "default_tangents"

    # Check for an existing node first
    existing = node_tree.nodes.get(default_tangents_name)
    if existing:
        return existing.outputs[0]

    # Create a new Tangent node
    tangent_node = node_tree.nodes.new("ShaderNodeTangent")
    tangent_node.name = default_tangents_name
    tangent_node.label = "Default Tangent"
    tangent_node.hide = True

    # Place the node near the bottom left of the Group Output node
    align_to = _get_group_output_bottom_left(node_tree)
    tangent_node.location = (align_to.x - tangent_node.width - 80,
                             align_to.y + 20)

    return tangent_node.outputs[0]


class DefaultSocket(NamedTuple):
    """Contains reference default value of a NodeSocket, i.e. the
    default_value attribute of a socket when its node has just been
    created.
    Attributes:
        name: The name of the socket
        identifier: The identifier attribute of the socket.
        default_value: The default_value attribute of a socket. If the
            original value was an iterable blender type then this will
            be a tuple instead.
    """
    _cache = {}

    name: str
    identifier: str
    default_value: Any

    @classmethod
    def from_socket(cls,
                    socket: Union[NodeSocket, NodeSocketInterface]
                    ) -> DefaultSocket:
        """Initialize a DefaultSocket from a socket or socket interface."""
        default_value = (socket.default_value
                         if hasattr(socket, "default_value") else None)
        if isinstance(default_value, typing.Iterable):
            default_value = tuple(default_value)
        return cls(socket.name,
                   socket.identifier,
                   default_value)

    @classmethod
    def add_cached(cls, key: str, values: Sequence[DefaultSocket]) -> None:
        cls._cache[key] = tuple(values)

    @classmethod
    def get_cached(cls, key: str) -> Optional[Tuple[DefaultSocket, ...]]:
        return cls._cache.get(key, None)

    def default_values_equal(self, socket: NodeSocket) -> bool:
        """Returns True if the socket has a default_value equal to the
        default_value this DefaultSocket represents.
        """
        socket_default = getattr(socket, "default_value", None)
        self_default = self.default_value

        if self_default is None or socket_default is None:
            return False

        if (isinstance(socket_default, typing.Iterable)
                and isinstance(self_default, typing.Iterable)):

            return all(math.isclose(x, y, rel_tol=1e-06)
                       for x, y in it.zip_longest(self_default, socket_default)
                       )

        return socket_default == self_default

    def set_default_value(self, socket: NodeSocket) -> None:
        if self.default_value is None:
            return

        if not hasattr(socket, "default_value") is None:
            raise TypeError("socket has no default_value attribute.")

        socket.default_value = self.default_value


def reference_inputs_from_type(node_type: type,
                               node_tree: ShaderNodeTree
                               ) -> List[DefaultSocket]:
    """Returns the reference default_values the input sockets for a,
    node type i.e. the sockets' values when a node has just been
    created.
    Params:
        node_type: The ShaderNode subclass to get the reference inputs
            of. Must not be ShaderNodeGroup.
        node_tree: A ShaderNodeTree which can be used to initialize an
            instance of node_type.
    Returns:
        A tuple of DefaultSocket instances.
    """
    if node_type is bpy.types.ShaderNodeGroup:
        raise ValueError("ShaderNodeGroup is not supported.")

    type_name = node_type.__name__

    cached = DefaultSocket.get_cached(type_name)
    if cached is not None:
        return list(cached)

    with TempNodes(node_tree) as temp_nodes:
        ref_node = temp_nodes.new(type_name)

        ref_inputs = [DefaultSocket.from_socket(x) for x in ref_node.inputs]

    ref_inputs = tuple(ref_inputs)

    DefaultSocket.add_cached(type_name, ref_inputs)
    return ref_inputs


def reference_inputs(node: ShaderNode) -> Tuple[DefaultSocket, ...]:
    """Returns the reference default_values of a node's input sockets,
    i.e. the sockets' values when the node has just been created (or
    after the group has been set in the case of a group node).
    Params:
        node: The ShaderNode to get the reference inputs of.
    Returns:
        A tuple of DefaultSocket instances.
    """
    node_tree = node.id_data
    assert isinstance(node_tree, bpy.types.NodeTree)

    if isinstance(node, bpy.types.ShaderNodeGroup):
        if node.node_tree is None:
            # For empty group nodes just return an empty tuple
            return tuple()
        # Use the node_tree's input NodeSocketInterface values
        return [DefaultSocket.from_socket(x) for x in node.node_tree.inputs]

    return reference_inputs_from_type(type(node), node_tree)


def sort_sockets_by(sockets: bpy.types.bpy_prop_collection,
                    reference: typing.Collection) -> None:
    """Sorts 'sockets' so that its order matches the order of
    'reference' using the name attribute to determine identity.
    Params:
        sockets: A collection of inputs or outputs from a node or
            node tree.
        reference: The collection to use for reference may be another
            collection of sockets or a collection of channels etc.
    """
    # N.B. Assumes no two sockets have the same name
    ref_indices = {x.name: idx for idx, x in enumerate(reference)}
    len_refs = len(reference)

    # List of sockets sorted by the index in 'reference'. Any sockets
    # not found in ref_indices should be at the back of the list.
    sockets_sorted = sorted(sockets,
                            key=lambda x: ref_indices.get(x.name, len_refs))

    for target_idx, socket in enumerate(sockets_sorted):
        current_idx = sockets.find(socket.name)
        if current_idx != target_idx:
            sockets.move(current_idx, target_idx)


class NodeMakeInfo(NamedTuple):
    """Contains the information needed to instantiate a node with
    specific settings.

    Attributes:
        bl_idname: The bl_idname of the node class
        options: dict of property names to values which will be set
                 on the node or None
        function: A callable that takes two arguments: the node instance
                  and the channel for which the node was made
    """
    bl_idname: str
    options: Optional[typing.Dict[str, Any]] = {}
    function: Optional[Callable[[ShaderNode, "Channel"], None]] = None

    def make(self, node_tree: ShaderNodeTree,
             channel: Optional["Channel"]) -> ShaderNode:
        node = node_tree.nodes.new(self.bl_idname)
        self.update_node(node, channel)
        return node

    def simple_make(self, node_tree: ShaderNodeTree) -> ShaderNode:
        return self.make(node_tree, None)

    def update_node(self, node: ShaderNode, channel) -> None:

        if self.options:
            for attr, value in self.options.items():
                setattr(node, attr, value)

        if self.function is not None:
            self.function(node, channel)
