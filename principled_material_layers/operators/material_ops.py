# SPDX-License-Identifier: GPL-2.0-or-later

import warnings

from contextlib import ExitStack
from typing import (Any, Callable, Container, Dict, List, NamedTuple,
                    Optional, Tuple, Union)

import bpy

from bpy.props import (BoolProperty,
                       CollectionProperty,
                       EnumProperty,
                       IntProperty,
                       StringProperty)

from bpy.types import (Material,
                       NodeLink,
                       NodeSocket,
                       Operator,
                       ShaderNode,
                       ShaderNodeTree,
                       UIList)

from bpy_extras.asset_utils import SpaceAssetInfo

from .. import asset_helper
from .. import tiled_storage

from ..preferences import get_addon_preferences

from ..channel import BasicChannel

from ..utils.duplicate_node_tree import duplicate_node_tree
from ..utils.layer_stack_utils import get_layer_stack
from ..utils.materials import (check_material_compat,
                               remove_appended_material)
from ..utils.nodes import (delete_nodes_not_in,
                           get_node_by_type,
                           get_output_node,
                           nodes_bounding_box,
                           reference_inputs,
                           vector_socket_link_default_generic)
from ..utils.node_tree import (clear_node_tree_sockets,
                               get_node_tree_socket,
                               new_node_tree_socket,
                               sort_outputs_by)
from ..utils.ops import pml_op_poll


# Which channels should be added when replacing a layer's material
CHANNEL_DETECT_MODES = (
    ('ALL', "All Channels",
     "The layer will have all the layer stack's channels"),
    ('ALL_ENABLED', "All Enabled",
     "The layer will have all channels that are currently enabled "
     "on the layer stack"),
    ('MODIFIED_OR_ENABLED', "Modified or Enabled",
     "The layer will have all channels that are enabled on the "
     "layer stack or are affected by the new material"),
    ('MODIFIED_ONLY', "Modified Only",
     "The layer will have only channels that are affected by the "
     "new material")
    )


def _temp_switch_to_node_editor(context, exit_stack, node_tree) -> None:
    old_area_type = context.area.type
    exit_stack.callback(lambda: setattr(context.area, "type",
                                        old_area_type))
    context.area.type = 'NODE_EDITOR'
    space = context.space_data

    old_tree_type = space.tree_type
    exit_stack.callback(lambda: setattr(space, "tree_type",
                                        old_tree_type))
    space.tree_type = "ShaderNodeTree"

    old_pin = space.node_tree if space.pin else None
    if old_pin is not None:
        exit_stack.callback(lambda: setattr(space, "node_tree",
                                            old_pin))
    else:
        exit_stack.callback(lambda: setattr(space, "pin", False))

    space.pin = True
    space.node_tree = node_tree


def _duplicate_ma_node_tree(context,
                            material: Material) -> ShaderNodeTree:
    """Duplicate a material's node tree as a new node group."""

    if not get_addon_preferences().use_op_based_ma_copy:
        return duplicate_node_tree(material.node_tree)

    # Duplicates a material's node tree using bpy.ops.duplicate to
    # copy the nodes and bpy.ops.group_make to convert the
    # selection to a node group.

    with ExitStack() as exit_stack:
        if material.library is not None:
            # Library materials cannot be edited so need to create
            # a copy.
            material = material.copy()
            exit_stack.callback(lambda:
                                bpy.data.materials.remove(material))

        node_tree = material.node_tree

        _temp_switch_to_node_editor(context, exit_stack, node_tree)

        nodes = node_tree.nodes

        for node in nodes:
            node.select = True

        # Delete any added nodes on cleanup
        old_nodes = list(nodes)
        exit_stack.callback(lambda: delete_nodes_not_in(nodes, old_nodes))

        # N.B. Crashes in Blender 3.0.1
        try:
            bpy.ops.node.duplicate()
            bpy.ops.node.group_make()
        except RuntimeError as e:
            # May occur if called when blend data cannot be modified
            warnings.warn(f"Error duplicating node tree: {e}")
            # Fall back on non-op based function
            return duplicate_node_tree(material.node_tree)

        space = bpy.context.space_data
        new_node_tree = space.edit_tree

        assert space.edit_tree is not node_tree

    return bpy.data.node_groups[new_node_tree.name]


_SocketValueDict = Dict[str, Union[Any, NodeLink]]


class _SocketInputValue(NamedTuple):
    """The connection and default value of a NodeSocket. Stores link
    information using the node and socket name, so may also be used
    with a duplicated node tree.
    """
    name: str
    type: str
    is_modified: bool
    default_value: Optional[Any] = None
    link_node_name: Optional[str] = None
    link_socket_name: Optional[str] = None

    @classmethod
    def from_socket(cls, socket: NodeSocket, is_modified=True):
        if socket.is_output:
            raise ValueError("Expected an input socket.")

        default_value = getattr(socket, "default_value", None)
        if isinstance(default_value, bpy.types.bpy_prop_array):
            default_value = tuple(default_value)

        if not socket.is_linked:
            return cls(socket.name, socket.bl_idname,
                       is_modified, default_value)

        link = socket.links[0]
        return cls(socket.name,
                   socket.bl_idname,
                   is_modified,
                   default_value=default_value,
                   link_node_name=link.from_node.name,
                   link_socket_name=link.from_socket.name)

    def get_linked_socket(self, node_tree) -> NodeSocket:
        if self.link_node_name is None:
            return None

        node = node_tree.nodes.get(self.link_node_name)
        if node is None:
            return None

        return node.outputs.get(self.link_socket_name)


class _ReplaceMaterialHelper:
    """A helper class for the replace_layer_material function"""

    def __init__(self, layer, material):
        self.layer = layer
        self.material = material

        self.layer_stack = layer.layer_stack

    def _get_surface_shader(self,
                            output_node: bpy.types.ShaderNodeOutputMaterial
                            ) -> ShaderNode:
        """Returns the node connected to the surface shader socket of
        a material output node."""
        socket = output_node.inputs[0]
        if not socket.is_linked:
            return None
        return socket.links[0].from_node

    def get_channel_socket_values(self,
                                  node_tree: ShaderNodeTree,
                                  ) -> List[_SocketInputValue]:
        """Gets the value of each of the sockets of the node_tree
        associated with a channel from the layer stack.
        Returns the linked state and default_value etc of the
        corresponding sockets as _SocketInputValue instances.
        Params:
            node_tree: A material's ShaderNodeTree that should contain
                a material output node.
        Returns:
            A list of _SocketInputValue instances
        """

        channel_names = {ch.name for ch in self.layer_stack.channels}

        socket_values = []

        # Identify channels from the material output node and the
        # shader node connected to the 'Surface' socket
        output_node = get_output_node(node_tree)
        if output_node is not None:
            socket_values = self._socket_values(output_node, channel_names)

            surface_shader = self._get_surface_shader(output_node)

            if surface_shader is not None:
                socket_values += self._socket_values(surface_shader,
                                                     channel_names)

        return socket_values

    def _socket_values(self,
                       node: ShaderNode,
                       socket_names: Container[str],
                       ) -> List[_SocketInputValue]:
        """Returns a list of _SocketInputValue for node's inputs.
        Only values for sockets with names in socket_names are returned.
        """

        socket_values = []

        # Default socket values for this node
        ref_inputs = {x.name: x for x in reference_inputs(node)}

        for socket in node.inputs:
            if socket.name not in socket_names:
                continue

            ref_soc = ref_inputs.get(socket.name, None)

            # Does the socket count as modified (different from the
            # socket on a default reference node)
            is_modified = (socket.is_linked
                           or ref_soc is None
                           or not ref_soc.default_values_equal(socket))

            soc_value = _SocketInputValue.from_socket(socket, is_modified)
            socket_values.append(soc_value)
        return socket_values

    def _modified_filter_factory(self,
                                 socket_values: List[_SocketInputValue]
                                 ) -> Callable[[_SocketInputValue], bool]:
        """Create a filter function to determine if a socket marked as
        modified should actually be imported. The filter function takes
        a _SocketInputValue and returns False if the socket should be
        ignored.
        """
        soc_val_dict = {x.name: x for x in socket_values}

        def modified_filter(soc_value) -> bool:
            """Filter for special cases. Returns False if the socket
            should not be included.
            """
            # Ignore emission color/strength if color is fully black
            # or the strength is 0
            if soc_value.name in ("Emission Color", "Emission Strength"):
                emit_color = getattr(soc_val_dict.get("Emission Color"),
                                     "default_value", (0, 0, 0, 1))
                emit_str = getattr(soc_val_dict.get("Emission Strength"),
                                   "default_value", 0.0)
                if not emit_str or not any(emit_color[:3]):
                    return False
            return True

        return modified_filter

    def select_socket_values(self,
                             socket_values: List[_SocketInputValue],
                             modified: bool,
                             enabled: bool) -> List[_SocketInputValue]:

        enabled_channels = {ch.name for ch in self.layer_stack.channels
                            if ch.enabled}

        if modified:
            modified_filter = self._modified_filter_factory(socket_values)

        return [x for x in socket_values
                if (modified and x.is_modified and modified_filter(x))
                or (enabled and x.name in enabled_channels)]

    def setup_layer_node_tree(self, node_tree: ShaderNodeTree) -> None:
        """Ensures the node group has an output node and sets
        their locations.
        """

        # Remove the group input node
        group_in = get_node_by_type(node_tree, "NodeGroupInput")
        if group_in is not None:
            node_tree.nodes.remove(group_in)

        # Ensure that there's a group output node
        group_out = get_node_by_type(node_tree, "NodeGroupOutput")
        if group_out is None:
            group_out = node_tree.nodes.new("NodeGroupOutput")

        # The material output node
        ma_output_node = get_output_node(node_tree)
        if ma_output_node is not None:
            # Set the group output's location to the same as the
            # material output
            group_out.location = ma_output_node.location

            # Remove the surface shader node
            surface_shader = self._get_surface_shader(ma_output_node)
            if surface_shader is not None:
                node_tree.nodes.remove(surface_shader)

        # Remove all material output nodes
        for node in list(node_tree.nodes):
            if isinstance(node, bpy.types.ShaderNodeOutputMaterial):
                node_tree.nodes.remove(node)

    def set_group_output_values(self,
                                node_tree: ShaderNodeTree,
                                socket_values: List[_SocketInputValue]
                                ) -> None:
        """Set the default_value of and link the input sockets of a
        NodeGroupOutput using the values given in socket_values.
        """

        group_out = get_node_by_type(node_tree, "NodeGroupOutput")

        for soc_value in socket_values:
            if get_node_tree_socket(node_tree,
                                    soc_value.name, 'OUTPUT') is None:
                new_node_tree_socket(node_tree, soc_value.name,
                                     'OUTPUT', soc_value.type)

        for soc_value in socket_values:
            group_out_soc = group_out.inputs[soc_value.name]
            tree_out = get_node_tree_socket(node_tree,
                                            soc_value.name, 'OUTPUT')

            if soc_value.default_value is not None:
                group_out_soc.default_value = soc_value.default_value
                tree_out.default_value = soc_value.default_value
            if soc_value.link_node_name:
                node_tree.links.new(group_out_soc,
                                    soc_value.get_linked_socket(node_tree))

    def add_all_layer_stack_channels(self, layer, enabled_only) -> None:
        layer_stack_chs = [ch for ch in self.layer_stack.channels
                           if not enabled_only or ch.enabled]
        for ch in layer_stack_chs:
            if ch.name not in layer.channels:
                layer_ch = layer.add_channel(ch)
                layer_ch.enabled = ch.enabled


class _CombineMaterialHelper(_ReplaceMaterialHelper):

    # Nodes to use to replace the default_value of unlinked sockets
    _value_node_types = {'VALUE': "ShaderNodeValue",
                         'RGBA': "ShaderNodeRGB",
                         'VECTOR': "ShaderNodeCombineXYZ"}

    @staticmethod
    def _link_default_node(socket) -> Tuple[ShaderNode, NodeSocket]:
        node_tree = socket.id_data
        value_node = None

        if socket.type == 'VECTOR':
            # For normal or tangent sockets use the appropriate nodes
            value_node = vector_socket_link_default_generic(socket)
            if value_node is not None:
                value_soc = next(x for x in value_node.outputs if x.is_linked)
                return value_node, value_soc

            # For other sockets use the default_value with a CombineXYZ node
            value_node = node_tree.nodes.new("ShaderNodeCombineXYZ")
            for i, component in enumerate(socket.default_value):
                value_node.inputs[i].default_value = component

        elif socket.type == 'VALUE':
            value_node = node_tree.nodes.new("ShaderNodeValue")
            value_node.outputs[0].default_value = socket.default_value

        elif socket.type == 'RGBA':
            value_node = node_tree.nodes.new("ShaderNodeRGB")
            value_node.outputs[0].default_value = socket.default_value
            value_node.hide = True
        else:
            return None, None

        value_node.label = socket.name
        return value_node, value_node.outputs[0]

    def setup_combine_node_tree(self, node_tree: ShaderNodeTree) -> None:
        """Setup a node tree to be combined with a material's existing
        node tree.
        """

        clear_node_tree_sockets(node_tree, 'OUTPUT')

        # Ensure that there's a group output node
        group_out = get_node_by_type(node_tree, "NodeGroupOutput")
        if group_out is None:
            group_out = node_tree.nodes.new("NodeGroupOutput")

        # The material output node
        ma_output_node = get_output_node(node_tree)
        if ma_output_node is not None:
            # Replace the surface shader with reroute nodes etc.
            surface_shader = self._get_surface_shader(ma_output_node)
            if surface_shader is not None:
                self._replace_surface_shader(surface_shader, group_out)

        # Remove all material output nodes
        for node in list(node_tree.nodes):
            if isinstance(node, bpy.types.ShaderNodeOutputMaterial):
                node_tree.nodes.remove(node)

    def _replace_surface_shader(self,
                                surface_shader: ShaderNode,
                                group_out: ShaderNode):
        """Replace the node surface_shader shader with reroute nodes
        and value nodes and connect the new nodes to Group Output
        node group_out.
        """
        node_tree = surface_shader.id_data

        # Dict of channel names to reroute nodes that output the
        # channels' values
        channel_nodes: Dict[str: ShaderNode] = {}

        y_pos = surface_shader.location.y
        x_pos = surface_shader.location.y + surface_shader.width
        for ch in self.layer_stack.channels:
            if not ch.enabled or ch.name not in surface_shader.inputs:
                continue

            socket = surface_shader.inputs[ch.name]

            reroute = node_tree.nodes.new("NodeReroute")
            reroute.label = socket.name
            reroute.location = (x_pos, y_pos)

            channel_nodes[socket.name] = reroute

            if socket.is_linked:
                link = socket.links[0]
                node_tree.links.new(reroute.inputs[0], link.from_socket)

            elif socket.type in ('VALUE', 'RGBA', 'VECTOR'):
                # Add a value/color node etc and link it to replace
                # socket's default_value
                value_node, value_soc = self._link_default_node(socket)
                if value_node is None:
                    continue

                value_node.location = (x_pos - 200, y_pos)

                node_tree.links.new(reroute.inputs[0], value_soc)
                y_pos -= value_node.height
            y_pos -= 20

            # Add a socket for the channel to the node group output
            if get_node_tree_socket(node_tree, socket.name, 'OUTPUT') is None:
                new_node_tree_socket(node_tree, socket.name, 'OUTPUT',
                                     socket.bl_rna.identifier)

            # Connect the reroute node to group_out
            group_out_soc = group_out.inputs[socket.name]
            node_tree.links.new(group_out_soc, reroute.outputs[0])

        node_tree.nodes.remove(surface_shader)

    def expand_group_node(self, group_node: ShaderNode) -> bpy.types.NodeFrame:
        """Expands a group node into the node tree. Returns a NodeFrame
        containing the extracted nodes.
        """
        node_tree = group_node.id_data

        for node in node_tree.nodes:
            node.select = (node == group_node)
        node_tree.nodes.active = group_node

        # Ungroup (expand) the group into node_tree
        with ExitStack() as exit_stack:
            _temp_switch_to_node_editor(bpy.context, exit_stack, node_tree)
            bpy.ops.node.group_ungroup()

        frame = node_tree.nodes.new("NodeFrame")

        # Parent the new nodes (now selected) to frame
        for node in node_tree.nodes:
            if node.select and node.parent is None:
                node.parent = frame
        frame.select = True

        return frame

    def position_frame(self, frame) -> None:
        """Positions the frame containing the new nodes from the combined
        material.
        """
        node_tree = frame.id_data

        nodes_to_check = [x for x in node_tree.nodes if x.parent is None]
        bb = nodes_bounding_box(nodes_to_check)

        group_out = get_node_by_type(node_tree, "NodeGroupOutput")

        # TODO Improve positioning

        nodes_in_frame = [x for x in node_tree.nodes if x.parent == frame]
        framebb = nodes_bounding_box(nodes_in_frame)
        frame.location.y = bb.bottom - framebb.height/2 - 200
        frame.location.x = group_out.location.x - framebb.width/2 - 200

    def position_group(self, group_node) -> None:
        """Positions the Group node containing the added material's
        node tree.
        """
        node_tree = group_node.id_data
        nodes = node_tree.nodes

        group_out = get_node_by_type(node_tree, "NodeGroupOutput")

        group_node.location.x = group_out.location.x - 300
        group_node.location.y = group_out.location.y + 400

        # Ensure that the node is not directly on top of any other node
        for _ in range(10):
            for node in nodes:
                if node == group_node:
                    continue
                if (node.location - group_node.location).length_squared < 2000:
                    group_node.location.x -= 300
                    break
            else:
                break


def replace_layer_material(context,
                           layer,
                           material: Material,
                           ch_select: str = 'MODIFIED_OR_ENABLED') -> None:
    """Replaces the node tree of MaterialLayer 'layer' with a node
    group created from material.node_tree
    Params:
        context: A bpy.types.Context instance. Should have a valid
            space_data attribute.
        layer: The MaterialLayer to replace the node tree of.
        material: A bpy.types.Material to copy the node tree from.
        ch_select: Which channels the layer should have. Enum str in
            {'ALL', 'ALL_ENABLED', 'MODIFIED_OR_ENABLED', 'MODIFIED_ONLY'}.
    """
    if context.space_data is None:
        raise ValueError("context has no space data.")

    helper = _ReplaceMaterialHelper(layer, material)

    # Duplicate the material's node tree as a node group
    node_tree = _duplicate_ma_node_tree(context, material)

    # List of _SocketInputValue for each socket associated with a
    # channel of the layer stack
    out_socket_values = helper.get_channel_socket_values(node_tree)

    helper.setup_layer_node_tree(node_tree)

    if ch_select != 'ALL':
        # Filter the socket values list based on ch_select
        out_socket_values = helper.select_socket_values(
                out_socket_values,
                modified=ch_select in ('MODIFIED_ONLY', 'MODIFIED_OR_ENABLED'),
                enabled=ch_select in ('ALL_ENABLED', 'MODIFIED_OR_ENABLED')
                )

    helper.set_group_output_values(node_tree, out_socket_values)

    layer.replace_node_tree(node_tree, update_channels=True)

    if ch_select != 'MODIFIED_ONLY':
        # Add channels to the layer even if material doesn't have any
        # corresponding sockets.
        helper.add_all_layer_stack_channels(layer,
                                            enabled_only=ch_select != 'ALL')

    sort_outputs_by(layer.node_tree, layer.layer_stack.channels)


def combine_layer_material(context,
                           layer,
                           material: Material,
                           channel_names: List[str],
                           expand_group: bool = True) -> None:
    helper = _CombineMaterialHelper(layer, material)
    layer_nt = layer.node_tree

    # Duplicate the material's node tree as a node group
    node_tree = _duplicate_ma_node_tree(context, material)
    node_tree.name = material.name

    helper.setup_combine_node_tree(node_tree)

    group_node = layer_nt.nodes.new("ShaderNodeGroup")
    group_node.node_tree = node_tree

    # Assumes the layer has only one group output node
    layer_output = get_node_by_type(layer_nt, "NodeGroupOutput")
    if layer_output is None:
        layer_output = layer_nt.nodes.new("NodeGroupOutput")

    if layer_output is not None:
        # Connect the requested channels from the group node to the
        # layer output
        for ch_name in channel_names:
            group_soc = group_node.outputs.get(ch_name)
            layer_soc = layer_output.inputs.get(ch_name)
            if group_soc is not None and layer_soc is not None:
                layer_nt.links.new(layer_soc, group_soc)

    if expand_group:
        frame = helper.expand_group_node(group_node)
        helper.position_frame(frame)
        frame.label = material.name

        bpy.data.node_groups.remove(node_tree)
    else:
        helper.position_group(group_node)


class PML_UL_load_material_list(UIList):
    _ma_compat_cache: Dict[str, bool] = {}

    def _should_cache_compat(self, ma: Material) -> bool:
        return ma.library is not None

    def draw_filter(self, _context, layout):
        layout.scale_y = 0.5

        row = layout.row(align=True)
        row.prop(self, "filter_name", text="")
        row.prop(self, "use_filter_invert", text="", icon="ARROW_LEFTRIGHT")

    def draw_item(self, _context, layout, _data, item, icon, _active_data,
                  _active_property, _index=0, _flt_flag=0):

        ma = item
        layout.scale_y = 0.5
        layout.template_icon(icon, scale=2.0)
        layout.label(text=ma.name)

    def filter_items(self, context, data, propname):
        layer_stack = get_layer_stack(context)
        materials = getattr(data, propname)

        helper = bpy.types.UI_UL_list

        shown_flag = self.bitflag_filter_item
        assert isinstance(self.filter_name, str)

        if self.filter_name:
            flags = helper.filter_items_by_name(self.filter_name, shown_flag,
                                                materials, "name")
            if self.use_filter_invert:
                # Will be switched again later
                flags = [x ^ shown_flag for x in flags]
        else:
            flags = [shown_flag] * len(materials)

        # FIXME Compatibility may have changed since material was cached
        compat_cache = self._ma_compat_cache

        # Should materials with names starting with "." be shown
        show_hidden_materials = self.filter_name.startswith(".")

        for idx, ma in enumerate(materials):
            if not flags[idx] & shown_flag:
                continue

            if ma.name.startswith(".") and not show_hidden_materials:
                # Hide hidden materials unless searching for them
                flags[idx] &= ~shown_flag
                continue

            cached = compat_cache.get(ma.name_full, None)

            if cached is None:
                compatible = check_material_compat(ma, layer_stack)
                if self._should_cache_compat(ma):
                    compat_cache[ma.name_full] = compatible
            else:
                compatible = cached

            if not compatible:
                flags[idx] &= ~shown_flag

        # use_filter_invert automatically inverts the flags, but since
        # the inversion was already performed manually after
        # filter_items_by_name we toggle the flags here to counter the
        # effects of the automatic inversion.
        if self.use_filter_invert:
            flags = [x ^ shown_flag for x in flags]

        return flags, []  # flags, order


class ReplaceLayerMaOpBase:
    """Base class for ops that replace a layer's material."""
    auto_enable_channels: BoolProperty(
        name="Auto-Enable Layer Stack Channels",
        description="Automatically enable any channels used by the new "
                    "material that are not already enabled on the layer stack",
        default=True
    )

    ch_detect_mode: EnumProperty(
        name="Channels",
        items=CHANNEL_DETECT_MODES,
        default='MODIFIED_OR_ENABLED'
    )

    tiled_storage_add: BoolProperty(
        name="Add Images to Tiled Storage",
        description="Add any images in the material to the layer stack's "
                    "tiled storage",
        default=False
    )

    def __init__(self):
        # Used during execute for deleting temporarily appended materials
        self.exit_stack: Optional[ExitStack] = None

    def check_material_valid(self, material: Material, layer_stack) -> bool:
        is_compat = check_material_compat(material, layer_stack)
        if not is_compat:
            self.report({'WARNING'}, is_compat.reason)
            return False
        return True

    def enable_stack_channels(self, layer_stack, layer) -> None:
        """Enable all channels in layer on both the layer_stack and
        the layer itself.
        """
        for ch in layer.channels:
            layer_stack_ch = layer_stack.channels.get(ch.name)

            if layer_stack_ch is not None:
                layer_stack.set_channel_enabled(ch.name, True)
                ch.enabled = True

    def replace_layer_material(self, context, layer, material):
        layer_stack = get_layer_stack(context)

        layer.free_bake()

        replace_layer_material(context, layer, material,
                               ch_select=self.ch_detect_mode)

        if (self.ch_detect_mode in ('MODIFIED_ONLY', 'MODIFIED_OR_ENABLED')
                and self.auto_enable_channels):
            # Ensure all channels in layer are enabled on the layer
            # and the layer stack
            self.enable_stack_channels(layer_stack, layer)

        if (self.tiled_storage_add
                and tiled_storage.tiled_storage_enabled(layer_stack)):
            tiled_storage.add_nodes_to_tiled_storage(layer_stack,
                                                     *layer.node_tree.nodes)

        layer_stack.node_manager.rebuild_node_tree()


class PML_OT_replace_layer_material(ReplaceLayerMaOpBase, Operator):
    bl_idname = "material.pml_replace_layer_material"
    bl_label = "Replace Layer Material"
    bl_description = ("Replaces the material of a principled material "
                      "painting layer")
    bl_options = {'REGISTER', 'UNDO'}

    material_name: StringProperty(
        name="Material",
        description="The material to copy from",
        default=""
    )

    layer_name: StringProperty(
        name="Layer",
        description="The layer to replace the material of"
    )

    ma_index: IntProperty(
        name="Material Index",
        description="The selected material's index in the UIList",
        default=-1
    )

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def draw(self, context):
        layout = self.layout

        layer_stack = get_layer_stack(context)
        layer = layer_stack.layers.get(self.layer_name)

        if layer is None:
            return

        col = layout.column(align=True)
        col.prop(self, "ch_detect_mode")
        if self.ch_detect_mode in ('MODIFIED_ONLY', 'MODIFIED_OR_ENABLED'):
            col.prop(self, "auto_enable_channels")
        else:
            layout.separator(factor=2.0)
        if layer_stack.image_manager.uses_tiled_storage:
            col.prop(self, "tiled_storage_add")

        col = layout.column(align=True)
        col.label(text="Use the Asset Browser context menu or sidebar ",
                  icon='INFO')
        col.label(text="to add asset materials to layers.", icon='BLANK1')

        # TODO live update property? (replace layer's material on
        # selection change)

        row = layout.row()
        row.scale_y = 2.0
        row.template_list("PML_UL_load_material_list", "",
                          bpy.data, "materials", self, "ma_index",
                          type='GRID', rows=4, columns=2)

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        layer = layer_stack.layers.get(self.layer_name)

        if layer is None:
            self.report({'WARNING'}, f"Layer '{self.layer_name}' not found.")
            return {'CANCELLED'}

        with ExitStack() as self.exit_stack:
            material = self._get_material(layer_stack)
            if material is None:
                return {'CANCELLED'}

            self.replace_layer_material(context, layer, material)

            return {'FINISHED'}

    def invoke(self, context, _event):
        self.layer_name = get_layer_stack(context).active_layer.name

        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def _get_material(self, layer_stack) -> Optional[Material]:

        if self.material_name:
            material = bpy.data.materials.get(self.material_name)
            if material is None:
                self.report({'WARNING'},
                            f"Material '{self.material_name}' not found.")
                return None

        elif self.ma_index >= 0:
            material = bpy.data.materials[self.ma_index]

        else:
            self.report({'WARNING'}, "No material specified.")
            return None

        if not self.check_material_valid(material, layer_stack):
            return None

        return material


class ReplaceLayerMaOpAssetBrowser(ReplaceLayerMaOpBase):
    """Replace Layer Material operator for the Asset Browser."""

    # Should be set to True when a subclass executes successfully
    # and the on_asset_import pref iis set to REMEMBER
    _remembering_props: bool = False

    @classmethod
    def poll(cls, context):
        if not SpaceAssetInfo.is_asset_browser(context.space_data):
            return False

        if not asset_helper.material_asset_active(context):
            return False

        layer_stack = get_layer_stack(context)
        if not layer_stack or layer_stack.active_layer is None:
            return False
        return True

    def draw(self, context):
        layout = self.layout
        layer_stack = get_layer_stack(context)

        col = layout.column(align=True)
        col.prop(self, "ch_detect_mode")
        if self.ch_detect_mode in ('MODIFIED_ONLY', 'MODIFIED_OR_ENABLED'):
            col.prop(self, "auto_enable_channels")
        if layer_stack.image_manager.uses_tiled_storage:
            col.prop(self, "tiled_storage_add")

        asset = context.active_file

        layout.separator()
        layout.label(text="Selected Material: "
                          f"{asset.name}")
        if asset.preview_icon_id:
            layout.template_icon(asset.preview_icon_id, scale=5.0)

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        layer = layer_stack.active_layer

        with ExitStack() as self.exit_stack:
            material = self._get_material(context)
            if material is None:
                return {'CANCELLED'}

            self.replace_layer_material(context, layer, material)

            self.update_op_remember()
            return {'FINISHED'}

    def invoke(self, context, _event):
        on_ma_import = get_addon_preferences().on_asset_import

        if on_ma_import == 'DEFAULT_SETTINGS':
            self.auto_enable_channels = True
            self.ch_detect_mode = 'MODIFIED_OR_ENABLED'
            self.tiled_storage_add = False
            return self.execute(context)
        if on_ma_import == 'REMEMBER' and self.remembering_props:
            # When 'REMEMBER' don't show pop-up if operator has already
            # run successfully this session
            return self.execute(context)

        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def _get_material(self, context) -> Optional[Material]:
        if context.active_file is None:
            return None

        local_id = asset_helper.asset_local_id(context)
        if local_id is not None:
            ma = local_id
        else:
            ma = self.import_material(context)

        layer_stack = get_layer_stack(context)

        if ma is None or not self.check_material_valid(ma, layer_stack):
            return None
        return ma

    def import_material(self, context) -> Optional[Material]:
        if self.exit_stack is None:
            raise RuntimeError("self.exit_stack is None.")

        try:
            ma = asset_helper.append_active_material_asset(context)
        except NotImplementedError:
            self.report({'ERROR'}, "Replacing the layer material with an "
                                   "asset is not supported for this version.")
            return None

        self.exit_stack.callback(lambda: remove_appended_material(ma))

        return ma

    def update_op_remember(self) -> None:
        """Should be called when the op completes successfully. If the
        on_asset_import pref is 'REMEMBER' then the props pop-up won't
        be shown in the future.
        """
        if get_addon_preferences().on_asset_import == 'REMEMBER':
            ReplaceLayerMaOpAssetBrowser._remembering_props = True
        else:
            ReplaceLayerMaOpAssetBrowser._remembering_props = False

    @property
    def remembering_props(self) -> bool:
        """True if this op remembers the properties from a previous
        invokation so doesn't need to show the op's pop-up when the
        on_asset_import pref is 'REMEMBER'.
        """
        return ReplaceLayerMaOpAssetBrowser._remembering_props


class PML_OT_replace_layer_material_ab(ReplaceLayerMaOpAssetBrowser, Operator):
    bl_idname = "material.pml_replace_layer_material_ab"
    bl_label = "Replace Layer Material"
    bl_description = ("Replaces the material of the active principled  "
                      "material layer")
    bl_options = {'REGISTER', 'UNDO'}


class PML_OT_new_layer_material_ab(ReplaceLayerMaOpAssetBrowser, Operator):
    """Import a material as a new layer (for the Asset Browser)."""
    bl_idname = "material.pml_new_layer_material_ab"
    bl_label = "Import as New Layer"
    bl_description = "Imports the selected material as a new layer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        # Adding a new layer seems to cause context.active_file to
        # become None. So can't add a layer and then call
        # super().execute to replace the material.

        with ExitStack() as self.exit_stack:
            material = self._get_material(context)
            if material is None:
                return {'CANCELLED'}

            new_layer = layer_stack.insert_layer(material.name or "Layer", -1)

            try:
                self.replace_layer_material(context, new_layer, material)
            except Exception as e:
                layer_stack.remove_layer(new_layer)
                raise e

            layer_stack.active_layer = new_layer

            self.update_op_remember()
            return {'FINISHED'}


class PML_OT_combine_material_ab(ReplaceLayerMaOpAssetBrowser, Operator):
    bl_idname = "material.pml_combine_material_ab"
    bl_label = "Combine with Active Layer"
    bl_description = ("Adds/replaces some of the active layer's channels "
                      "using channels from the selected material")
    bl_options = {'REGISTER', 'UNDO'}

    channels: CollectionProperty(
        name="Channels",
        type=BasicChannel,
        description="The channels of the imported material"
    )

    keep_as_node_group: BoolProperty(
        name="As Node Group",
        description="Add the selected material as a group node",
        default=False
    )

    def draw(self, context):
        layout = self.layout
        layer_stack = get_layer_stack(context)

        layout.prop(self, "keep_as_node_group")

        layout.label(text="Channels")
        flow = layout.grid_flow(columns=2, even_columns=True, align=True)

        # Show a bool prop for each channel in the material that is
        # also enabled on the layer stack
        layer_stack_chs = [x for x in layer_stack.channels if x.enabled]
        for layer_stack_ch in layer_stack_chs:
            ch = self.channels.get(layer_stack_ch.name)
            if ch is not None:
                flow.prop(ch, "enabled", text=ch.name)

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        channels_to_replace = [ch.name for ch in self.channels if ch.enabled]

        if not channels_to_replace:
            return {'CANCELLED'}

        with ExitStack() as self.exit_stack:
            material = self._get_material(context)
            combine_layer_material(context,
                                   layer_stack.active_layer,
                                   material,
                                   channels_to_replace,
                                   expand_group=not self.keep_as_node_group)

        return {'FINISHED'}

    def invoke(self, context, _event):
        layer_stack = get_layer_stack(context)

        with ExitStack() as self.exit_stack:
            material = self._get_material(context)

            if material is None:
                return {'CANCELLED'}
            if material.node_tree is None:
                self.report({'WARNING'},
                            f"{material.name} does not use nodes")
                return {'CANCELLED'}

            self._populate_channels(layer_stack, material)

        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def _populate_channels(self, layer_stack, ma: Material) -> None:
        """Populate this operator's channels property from material ma."""
        helper = _ReplaceMaterialHelper(layer_stack.active_layer, ma)
        socket_values = helper.get_channel_socket_values(ma.node_tree)

        for soc_value in socket_values:
            new_ch = self.channels.add()
            new_ch.name = soc_value.name  # Only need the socket name
            new_ch.enabled = False


classes = (PML_UL_load_material_list,
           PML_OT_new_layer_material_ab,
           PML_OT_replace_layer_material,
           PML_OT_replace_layer_material_ab,
           PML_OT_combine_material_ab,
           )

_register, _unregister = bpy.utils.register_classes_factory(classes)


def register():
    _register()

    bpy.types.WindowManager.pml_ma_assets = CollectionProperty(
                                              type=bpy.types.AssetHandle)


def unregister():
    del bpy.types.WindowManager.pml_ma_assets

    _unregister()
