# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import itertools as it

from typing import Callable, NamedTuple, Optional

import bpy

from bpy.types import NodeTree, ShaderNode, ShaderNodeTree

from .channel import Channel
from .utils.naming import cap_enum


# Blend types which use a single MixRGB node
_MIX_NODE_BLEND_TYPES = ('MIX',
                         None,
                         'DARKEN',
                         'MULTIPLY',
                         'BURN',
                         None,
                         'LIGHTEN',
                         'SCREEN',
                         'DODGE',
                         'ADD',
                         None,
                         'OVERLAY',
                         'SOFT_LIGHT',
                         'LINEAR_LIGHT',
                         None,
                         'DIFFERENCE',
                         'SUBTRACT',
                         'DIVIDE',
                         None,
                         'HUE',
                         'SATURATION',
                         'COLOR',
                         'VALUE'
                         )
_OTHER_BLEND_TYPES = ('CUSTOM',)


# BLEND_MODES enum
# Looks like: (('MIX', "Mix", ""), None, ('DARKEN', "Darken", ""), ...)
# May contain None as a separator
BLEND_MODES = tuple(None if x is None else (x, cap_enum(x), "")
                    for x in it.chain(_MIX_NODE_BLEND_TYPES,
                                      (None,),
                                      _OTHER_BLEND_TYPES)
                    )

# Dict of enum strings to their indices in BLEND_MODES
_BLEND_MODE_INDICES = {x[0]: idx for idx, x in enumerate(BLEND_MODES)
                       if x is not None}


def blend_mode_display_name(blend_mode: str) -> str:
    """Returns the display name of a blend mode enum value."""
    idx = _BLEND_MODE_INDICES[blend_mode]
    return BLEND_MODES[idx][1]


def blend_mode_description(blend_mode: str) -> str:
    """Returns the description of a blend mode enum value."""
    idx = _BLEND_MODE_INDICES[blend_mode]
    return BLEND_MODES[idx][2]


def is_group_blending_compat(node_group: NodeTree,
                             strict: bool = False) -> bool:
    """Returns whether or not a node group can be used as a custom
    blending operation. This function checks the input and ouput
    sockets of the group to determine compatibility.
    Params:
        node_group: The node_group to check.
        strict: If True then the input and output sockets of the group
            must be exactly as expected. Otherwise just check that the
            group has at least the correct number of inputs and
            outputs.
    Returns:
        True if the group is compatible, False otherwise.
    """
    if not isinstance(node_group, ShaderNodeTree):
        return False
    if not strict:
        # Require at least 3 inputs and 1 output
        return len(node_group.inputs) >= 3 and node_group.outputs

    # strict == True
    # Require exactly 3 inputs and 1 output
    if len(node_group.inputs) != 3 or len(node_group.outputs) != 1:
        return False

    # Check the sockets are compatible types
    for socket in it.chain(node_group.inputs, node_group.outputs):
        if socket.type == 'SHADER':
            return False

    return True


class _BlendModeNodeInfo(NamedTuple):
    """Contains the information needed to instantiate a node that performs
    a blending operation.

    Attributes:
        bl_idname: The bl_idname of the node class
        options: dict of property names to values which will be set
                 on the node
        function: A callable that takes two arguments: the node instance
                  and the channel for which the node was made
    """
    bl_idname: str
    options: dict = {}
    function: Optional[Callable[[ShaderNode, Channel], None]] = None

    def make(self, node_tree: ShaderNodeTree, channel: Channel) -> ShaderNode:
        node = node_tree.nodes.new(self.bl_idname)
        self.update_node(node, channel)
        return node

    def update_node(self, node: ShaderNode, channel: Channel) -> None:

        if self.options:
            for attr, value in self.options.items():
                setattr(node, attr, value)

        if self.function is not None:
            self.function(node, channel)


def _mix_node_info(blend_mode: str) -> _BlendModeNodeInfo:
    """Returns a _BlendModeNodeInfo tuple for a blend_mode that
    uses a MixRGB node with a blend_type of blend_mode.
    """
    return _BlendModeNodeInfo("ShaderNodeMixRGB", {"blend_type": blend_mode})


_FALLBACK_GROUP_NAME = ".pml_blend_fallback"


def _get_fallback_node_group() -> ShaderNodeTree:
    """Gets or creates the node group used when a custom blend mode
    node group is missing or invalid.
    """
    node_tree = bpy.data.node_groups.get(_FALLBACK_GROUP_NAME)

    if node_tree is None:
        # Create a node group that is just a wrapper around a MixRGB
        # node with the 'MIX' blend_type
        node_tree = bpy.data.node_groups.new(_FALLBACK_GROUP_NAME,
                                             "ShaderNodeTree")
        assert node_tree.name == _FALLBACK_GROUP_NAME

        node_tree.inputs.new("NodeSocketFloat", "Fac")
        node_tree.inputs.new("NodeSocketColor", "Color1")
        node_tree.inputs.new("NodeSocketColor", "Color2")

        node_tree.outputs.new("NodeSocketColor", "Color")

        group_in = node_tree.nodes.new("NodeGroupInput")

        mix_node = node_tree.nodes.new("ShaderNodeMixRGB")
        mix_node.location.x += 300
        mix_node.blend_type = 'MIX'
        for out, in_ in zip(group_in.outputs, mix_node.inputs):
            node_tree.links.new(in_, out)

        group_out = node_tree.nodes.new("NodeGroupOutput")
        group_out.location.x += 600
        node_tree.links.new(group_out.inputs[0], mix_node.outputs[0])

    return node_tree


def _custom_blend_mode_fnc(node: ShaderNode, channel: Channel) -> None:
    """Function used by the 'CUSTOM' blend mode's _BlendModeNodeInfo.
    Sets the node tree for a group node to the channel's
    blend_mode_custom property. Uses a fallback group if the property's
    value is incompatible.
    """
    node.node_tree = channel.blend_mode_custom

    if not is_group_blending_compat(node.node_tree, strict=False):
        node.node_tree = _get_fallback_node_group()


# Dict of blend modes to _BlendModeNodeInfo
BLEND_MODES_NODE_INFO = {
    'CUSTOM': _BlendModeNodeInfo('ShaderNodeGroup',
                                 function=_custom_blend_mode_fnc)
    }

# Create _BlendModeNodeInfo for mix node blend modes.
for mode_enum in _MIX_NODE_BLEND_TYPES:
    if mode_enum is not None:
        BLEND_MODES_NODE_INFO[mode_enum] = _mix_node_info(mode_enum)

_BLEND_MODES_NO_NONE = [x for x in BLEND_MODES if x is not None]

# Check that all (not None) blend modes have a _BlendModeNodeInfo
assert len(_BLEND_MODES_NO_NONE) == len(BLEND_MODES_NODE_INFO)
for mode_enum in _BLEND_MODES_NO_NONE:
    assert mode_enum[0] in BLEND_MODES_NODE_INFO, (f"{mode_enum[0]} not found")
