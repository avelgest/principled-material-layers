# SPDX-License-Identifier: GPL-2.0-or-later

import typing

from typing import Optional

import bpy

from bpy.types import Node, ShaderNode, ShaderNodeTree

from .utils.naming import cap_enum
from .utils.nodes import NodeMakeInfo, get_node_by_type

_HARDNESS_TYPES = (
    "DEFAULT",
    None,
    "LINEAR",
    "BINARY",
    "SMOOTHSTEP",
    "SMOOTHERSTEP",
    "SMOOTHERSTEP_X2",
    "SMOOTHERSTEP_X3",
    None,
    "CUSTOM"
)

_HARDNESS_DESCR = {
    "DEFAULT": "Use the layer stack's default hardness for this channel type",
    "BINARY": "Channel instantly transitions (at threshold value) between 0%"
              " and 100%",
    "SMOOTHSTEP": "Channel transitions using a smoothstep function",
    "SMOOTHERSTEP": "Channel transitions using a smootherstep function "
                    "(sharper than Smoothstep)",
    "SMOOTHERSTEP_X2": "Channel transitions using a chain of two smootherstep"
                       "functions (sharper than Smootherstep)",
    "SMOOTHERSTEP_X3": "Channel transitions using a chain of three "
                       "smootherstep functions (sharper than Smootherstep x3)",
    "CUSTOM": "Use a custom node group for this channel's hardness"
}


# Set of hardness modes that support a threshold value
_SUPPORTS_THRESHOLD = {'BINARY'}

# HARDNESS_MODES enum
# Looks like: (('DEFAULT', "default", ""), None, ('BINARY', "binary", ""), ...)
# May contain None as a separator
HARDNESS_MODES = tuple(None if x is None
                       else (x, cap_enum(x), _HARDNESS_DESCR.get(x, ""))
                       for x in _HARDNESS_TYPES
                       )


# Dict of enum strings to their indices in HARDNESS_MODES
_HARDNESS_INDICES = {x[0]: idx for idx, x in enumerate(HARDNESS_MODES)
                     if x is not None}


def hardness_display_name(hardness: str) -> str:
    """Returns the display name of a hardness enum value."""
    idx = _HARDNESS_INDICES[hardness]
    return HARDNESS_MODES[idx][1]


def hardness_description(hardness: str) -> str:
    """Returns the description of a hardness enum value."""
    idx = _HARDNESS_INDICES[hardness]
    return HARDNESS_MODES[idx][2]


def supports_threshold(hardness: str,
                       custom: Optional[ShaderNodeTree]) -> bool:
    """Returns whether this hardness type supports using a threshold.
    If hardness is 'CUSTOM' the node group used should be passed using
    the custom parameter.
    """
    if hardness == 'CUSTOM' and custom is not None:
        return len(custom.inputs) > 1
    return hardness in _SUPPORTS_THRESHOLD


def _create_node_group(name: str, threshold=False) -> ShaderNodeTree:
    """Create a new node group for a hardness mode"""
    node_group = bpy.data.node_groups.new(name, "ShaderNodeTree")
    node_group.inputs.new("NodeSocketFloat", "In")
    node_group.outputs.new("NodeSocketFloat", "Out")

    if threshold:
        node_group.inputs.new("NodeSocketFloat", "Threshold")

    node_group.nodes.new("NodeGroupInput")
    node_group.nodes.new("NodeGroupOutput").location.x += 200

    return node_group


def _chain_nodes(nodes: typing.Sequence[Node],
                 input_idx: int = 0,
                 output_idx: int = 0) -> bpy.types.Node:
    """Connects and positions nodes in a chain using the given input
    and output socket indices. Returns the last node in the chain.
    """
    if not nodes:
        raise ValueError("No nodes given.")

    prev_node = node = nodes[0]
    links = prev_node.id_data.links

    for node in nodes[1:]:
        links.new(node.inputs[input_idx], prev_node.outputs[output_idx])
        node.location = prev_node.location
        node.location.x += node.width + 100
        prev_node = node
    return node


def _map_range_xn_group(interp_type: str, n: int):
    """Returns a node group containing a chain of linked Map Range
    nodes (with group input/output nodes). Either returns an existing
    node group or creates a new one.
    Params:
        interp_type: The value of the interpolation_type property of
            the Map Range nodes.
        n: The number of Map Range nodes in the chain.
    Returns:
        A ShaderNodeGroup.
    """
    group_name = f".pml_{interp_type.lower()}_x{n}"

    # Check for an existing node group
    node_tree = bpy.data.node_groups.get(group_name)
    if node_tree is not None:
        return node_tree

    # Create the node_group
    node_tree = _create_node_group(group_name)
    nodes = node_tree.nodes
    links = node_tree.links

    # Create the Map Range nodes and set their interpolation_type
    make = NodeMakeInfo("ShaderNodeMapRange",
                        {"interpolation_type": interp_type})
    nodes = [make.simple_make(node_tree) for _ in range(n)]
    end_node = _chain_nodes(nodes)

    # Link the group inputs and outputs to the node chain
    group_in = get_node_by_type(node_tree, "NodeGroupInput")
    group_in.location.x = nodes[0].location.x - 200
    links.new(nodes[0].inputs[0], group_in.outputs[0])

    group_out = get_node_by_type(node_tree, "NodeGroupOutput")
    group_out.location.x = end_node.location.x + 200
    links.new(end_node.outputs[0], group_out.inputs[0])

    return node_tree


def is_group_hardness_compat(node_group: Optional[bpy.types.NodeTree],
                             strict: bool = False) -> bool:
    """Whether node_group can be used as a custom hardness function
    based on its inputs and outputs.
    If strict is True then require <= 2 input sockets and only 1 output
    socket (all must be scalar sockets).
    """
    if node_group is None:
        return False

    inputs = node_group.inputs
    outputs = node_group.outputs
    if node_group.type != 'SHADER' or not inputs or not outputs:
        return False
    if not strict:
        # When not strict accept at least one input and output socket
        # of any type
        return True

    # Require exact match in strict mode
    # Support 1 or 2 input sockets (may have 'threshold' socket)
    return (len(inputs) <= 2 and len(outputs) == 1
            and inputs[0].type == 'VALUE'
            and outputs[0].type == 'VALUE'
            and (len(outputs) == 1 or outputs[1].type == 'VALUE'))


def create_custom_hardness_default(name: str) -> ShaderNodeTree:
    """Creates a node group for use as a custom hardness function. The
    group will have a default setup (i.e. a Float Curve node connected
    to the group input/ouput).
    """

    node_group = _create_node_group(name, threshold=True)

    group_in = get_node_by_type(node_group, "NodeGroupInput")
    group_out = get_node_by_type(node_group, "NodeGroupOutput")

    group_in.location.x = -200
    group_out.location.x = 300

    float_curve = node_group.nodes.new("ShaderNodeFloatCurve")
    node_group.links.new(float_curve.inputs[1], group_in.outputs[0])
    node_group.links.new(group_out.inputs[0], float_curve.outputs[0])

    return node_group


_FALLBACK_GROUP_NAME = ".pml_hardness_fallback"


def _get_fallback_node_group() -> ShaderNodeTree:
    """Gets or creates the node group used when a custom hardness node
    group is missing or invalid.
    """
    node_tree = bpy.data.node_groups.get(_FALLBACK_GROUP_NAME)
    if node_tree is not None:
        return node_tree

    node_tree = _create_node_group(_FALLBACK_GROUP_NAME)
    assert node_tree.name == _FALLBACK_GROUP_NAME

    # Just link the group input directly to the group output
    group_in = get_node_by_type(node_tree, "NodeGroupInput")
    group_out = get_node_by_type(node_tree, "NodeGroupOutput")
    node_tree.links.new(group_out.inputs[0], group_in.outputs[0])
    return node_tree


def _smootherstep_xn(node: bpy.types.ShaderNode, n: int) -> None:
    """Sets the node_tree property of node to a node group
    containing a chain of smootherstep Map Range nodes.
    """
    node_group = _map_range_xn_group('SMOOTHERSTEP', n)
    node.node_tree = node_group


def _custom_hardness_fnc(node: ShaderNode, channel) -> None:
    """Function used by the 'CUSTOM' hardness's NodeMakeInfo.
    Sets the node tree for a group node to the channel's
    hardness_custom property. Uses a fallback group if the property's
    value is incompatible.
    """
    node.node_tree = channel.effective_hardness_custom

    if not is_group_hardness_compat(node.node_tree, strict=False):
        node.node_tree = _get_fallback_node_group()


HARDNESS_NODE_INFO = {
    "LINEAR": None,
    "BINARY": NodeMakeInfo("ShaderNodeMath", {"operation": 'GREATER_THAN'}),
    "SMOOTHSTEP": NodeMakeInfo("ShaderNodeMapRange",
                               {"interpolation_type": 'SMOOTHSTEP'}),
    "SMOOTHERSTEP": NodeMakeInfo("ShaderNodeMapRange",
                                 {"interpolation_type": 'SMOOTHERSTEP'}),
    "SMOOTHERSTEP_X2": NodeMakeInfo("ShaderNodeGroup", None,
                                    lambda node, _: _smootherstep_xn(node, 2)),
    "SMOOTHERSTEP_X3": NodeMakeInfo("ShaderNodeGroup", None,
                                    lambda node, _: _smootherstep_xn(node, 3)),
    "CUSTOM": NodeMakeInfo("ShaderNodeGroup", None, _custom_hardness_fnc)
}
