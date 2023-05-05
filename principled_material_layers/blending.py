# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import itertools as it
import warnings

from typing import Optional

import bpy

from bpy.types import NodeTree, ShaderNode, ShaderNodeTree

from .channel import Channel
from .utils import node_tree_import
from .utils.nodes import NodeMakeInfo, get_node_by_type
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
# Blend types which use a node group bundled with the add-on
_ADDON_BLEND_TYPES = []

_OTHER_BLEND_TYPES = ('DEFAULT', 'CUSTOM',)

# Override the default naming for these blend types
_BLEND_NAMES = {}

# Override the default (empty) description for these blend types
_BLEND_DESCR = {
}

# The node groups used by the _ADDON_BLEND_TYPES blend modes
# Should match a file in the add-on's node_groups directory
_ADDON_BLEND_TYPES_GROUPS = {
}


def _enum_to_tuple(enum: Optional[str]) -> Optional[tuple[str, str, str]]:
    """Create a tuple for use in an EnumProperty items list from a blend
    type identifier. If enum is None then this just returns None.
    """
    if enum is None:
        return None
    name = _BLEND_NAMES.get(enum)
    if name is None:
        name = cap_enum(enum)
    description = _BLEND_DESCR.get(enum, "")

    return (enum, name, description)


# BLEND_MODES enum
# Looks like: (('MIX', "Mix", ""), None, ('DARKEN', "Darken", ""), ...)
# May contain None as a separator
BLEND_MODES = tuple(_enum_to_tuple(x) for x in it.chain(_MIX_NODE_BLEND_TYPES,
                                                        (None,),
                                                        _ADDON_BLEND_TYPES,
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


def is_group_blending_compat(node_group: Optional[NodeTree],
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
    if node_group is None or node_group.type != 'SHADER':
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


def _mix_node_info(blend_mode: str) -> NodeMakeInfo:
    """Returns a NodeMakeInfo tuple for a blend_mode that
    uses a MixRGB node with a blend_type of blend_mode.
    """
    return NodeMakeInfo("ShaderNodeMixRGB", {"blend_type": blend_mode})


def _addon_blend_mode_fnc(node: ShaderNode, channel: Channel) -> None:
    """Function used by the NodeMakeInfo of blend modes that use node
    groups bundled with the add-on. Uses a fallback group if the
    node group can't be loaded.
    """
    blend_mode = channel.blend_mode
    if blend_mode == 'DEFAULT':
        blend_mode = channel.default_blend_mode

    group_name = _ADDON_BLEND_TYPES_GROUPS.get(blend_mode)
    if group_name is None:
        warnings.warn(f"Can't find the name of {blend_mode}' s node group")
        node.node_tree = _get_fallback_node_group()
        return

    try:
        node.node_tree = node_tree_import.load_addon_node_group(group_name)
    except Exception as e:
        node.node_tree = _get_fallback_node_group()
        raise e


def _addon_node_info(_blend_mode: str) -> NodeMakeInfo:
    """Returns a NodeMakeInfo tuple for a blend_mode that uses
    a group node and a node group bundled with the add-on.
    """
    return NodeMakeInfo("ShaderNodeGroup", function=_addon_blend_mode_fnc)


def _create_node_group(name: str):
    """Create a node group for a blend mode."""
    node_group = bpy.data.node_groups.new(name=name, type="ShaderNodeTree")

    node_group.inputs.new(name="Blend Fac", type="NodeSocketFloatFactor")
    node_group.inputs.new(name="Input 1", type="NodeSocketColor")
    node_group.inputs.new(name="Input 2", type="NodeSocketColor")

    out = node_group.outputs.new(name="Output", type="NodeSocketColor")
    out.hide_value = True

    group_in = node_group.nodes.new(type="NodeGroupInput")
    group_out = node_group.nodes.new(type="NodeGroupOutput")

    group_in.location.x -= 200
    group_out.location.x += 200

    return node_group


_FALLBACK_GROUP_NAME = ".pml_blend_fallback"


def _get_fallback_node_group() -> ShaderNodeTree:
    """Gets or creates the node group used when a custom blend mode
    node group is missing or invalid.
    """
    node_tree = bpy.data.node_groups.get(_FALLBACK_GROUP_NAME)
    if node_tree is not None:
        return node_tree

    # Create a node group that is just a wrapper around a MixRGB
    # node with the 'MIX' blend_type
    node_tree = _create_node_group(_FALLBACK_GROUP_NAME)
    assert node_tree.name == _FALLBACK_GROUP_NAME

    group_in = get_node_by_type(node_tree, "NodeGroupInput")
    group_out = get_node_by_type(node_tree, "NodeGroupOutput")

    mix_node = node_tree.nodes.new("ShaderNodeMixRGB")
    mix_node.location.x = group_in.location.x + 300
    mix_node.blend_type = 'MIX'
    for out, in_ in zip(group_in.outputs, mix_node.inputs):
        node_tree.links.new(in_, out)

    group_out.location.x = mix_node.location.x + 300
    node_tree.links.new(group_out.inputs[0], mix_node.outputs[0])

    return node_tree


def create_custom_blend_default(name: str) -> ShaderNodeTree:
    """Creates a node group for use as a custom blending operation. The
    group will have a default setup (i.e. a MixRGB node connected to
    the group inputs/ouput).
    """
    node_group = _create_node_group(name)

    group_in = get_node_by_type(node_group, "NodeGroupInput")
    group_out = get_node_by_type(node_group, "NodeGroupOutput")

    group_in.location.x = -200
    group_out.location.x = 200

    assert is_group_blending_compat(node_group, strict=True)

    # Add MixRGB node
    mix_node = node_group.nodes.new(type="ShaderNodeMixRGB")
    mix_node.location = (group_in.location + group_out.location) / 2

    for out_soc, in_soc in zip(group_in.outputs, mix_node.inputs):
        node_group.links.new(in_soc, out_soc)

    node_group.links.new(group_out.inputs[0], mix_node.outputs[0])

    return node_group


def _custom_blend_mode_fnc(node: ShaderNode, channel: Channel) -> None:
    """Function used by the 'CUSTOM' blend mode's NodeMakeInfo.
    Sets the node tree for a group node to the channel's
    blend_mode_custom property. Uses a fallback group if the property's
    value is incompatible.
    """
    if channel.blend_mode == 'DEFAULT':
        node.node_tree = channel.default_blend_mode_custom
    else:
        node.node_tree = channel.blend_mode_custom

    if not is_group_blending_compat(node.node_tree, strict=False):
        node.node_tree = _get_fallback_node_group()


# Dict of blend modes to NodeMakeInfo
BLEND_MODES_NODE_INFO = {
    'CUSTOM': NodeMakeInfo('ShaderNodeGroup',
                           function=_custom_blend_mode_fnc)
    }

# Create NodeMakeInfo for mix node blend modes.
for mode_enum in _MIX_NODE_BLEND_TYPES:
    if mode_enum is not None:
        BLEND_MODES_NODE_INFO[mode_enum] = _mix_node_info(mode_enum)

# Create NodeMakeInfo for add-on blend modes.
for mode_enum in _ADDON_BLEND_TYPES:
    if mode_enum is not None:
        BLEND_MODES_NODE_INFO[mode_enum] = _addon_node_info(mode_enum)


# Check that all blend modes that need one have a NodeMakeInfo

# List of blend modes that should have NodeMakeInfo
_BLEND_MODES_W_MAKEINFO = [x[0] for x in BLEND_MODES
                           if x is not None and x[0] != 'DEFAULT']

assert len(_BLEND_MODES_W_MAKEINFO) == len(BLEND_MODES_NODE_INFO)
for mode_enum in _BLEND_MODES_W_MAKEINFO:
    assert mode_enum in BLEND_MODES_NODE_INFO, (f"{mode_enum} not found")
