# SPDX-License-Identifier: GPL-2.0-or-later

from contextlib import ExitStack
from typing import Any, Container, Dict, List, NamedTuple, Optional, Union

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

from ..preferences import get_addon_preferences

from ..asset_helper import append_material_asset

from ..utils.duplicate_node_tree import duplicate_node_tree
from ..utils.layer_stack_utils import get_layer_stack
from ..utils.materials import (IsMaterialCompat,
                               check_material_compat,
                               check_material_asset_compat)
from ..utils.nodes import (delete_nodes_not_in,
                           get_node_by_type,
                           get_output_node,
                           reference_inputs,
                           sort_sockets_by)
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

        nodes = space.node_tree.nodes

        for node in nodes:
            node.select = True

        # Delete any added nodes on cleanup
        old_nodes = list(nodes)
        exit_stack.callback(lambda: delete_nodes_not_in(nodes, old_nodes))

        # N.B. Crashes in Blender 3.0.1
        bpy.ops.node.duplicate()
        bpy.ops.node.group_make()

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

    def select_socket_values(self,
                             socket_values: List[_SocketInputValue],
                             modified: bool,
                             enabled: bool) -> List[_SocketInputValue]:

        enabled_channels = {ch.name for ch in self.layer_stack.channels
                            if ch.enabled}

        return [x for x in socket_values
                if (modified and x.is_modified)
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
            if soc_value.name not in node_tree.outputs:
                node_tree.outputs.new(type=soc_value.type, name=soc_value.name)

        for soc_value in socket_values:
            group_out_soc = group_out.inputs[soc_value.name]
            tree_out = node_tree.outputs[soc_value.name]

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

    sort_sockets_by(layer.node_tree.outputs, layer.layer_stack.channels)


class PML_UL_load_material_list(UIList):
    _ma_compat_cache: Dict[str, bool] = {}

    def _should_cache_compat(self, ma: Material) -> bool:
        return ma.library is not None

    def draw_filter(self, context, layout):
        layout.scale_y = 0.5

        row = layout.row(align=True)
        row.prop(self, "filter_name", text="")
        row.prop(self, "use_filter_invert", text="", icon="ARROW_LEFTRIGHT")

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):

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

    ma_asset_index: IntProperty(
        name="Material Index (Asset)",
        description="The selected material's index in the asset view",
        default=0
    )

    ma_select_mode: EnumProperty(
        name="Material Selection",
        items=(('LOCAL', "Local", "A material contained or linked by the "
                                  "current .blend file", 'NONE', 0),
               ('ASSET', "Asset", "A material from an asset library",
                'EXPERIMENTAL', 1)
               ),
        default='LOCAL'
    )

    displayed_asset_lib = None

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

        layout.prop(self, "ma_select_mode", expand=True)

        # TODO live update property? (replace layer's material on
        # selection change)

        if self.ma_select_mode == 'LOCAL':
            row = layout.row()
            row.scale_y = 2.0
            row.template_list("PML_UL_load_material_list", "",
                              bpy.data, "materials", self, "ma_index",
                              type='GRID', rows=4, columns=2)

        elif self.ma_select_mode == 'ASSET':
            self.draw_ma_asset_list(context, layout)

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

    def invoke(self, context, event):
        self.layer_name = get_layer_stack(context).active_layer.name

        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def _check_asset_compat(self, context) -> Optional[IsMaterialCompat]:
        layer_stack = get_layer_stack(context)
        asset = self.selected_asset
        if asset is None:
            return None
        asset_lib = bpy.context.asset_library_ref

        return check_material_asset_compat(asset, asset_lib,
                                           layer_stack, delayed=True)

    def _temp_append_material_asset(self) -> Optional[Material]:
        """Temporarily append the selected asset to the file and tell
        the exit stack to delete it on exit. If the file has a local_id
        then the local_id is just returned and will not deleted."""
        asset = self.selected_asset

        if getattr(asset, "local_id", None) is not None:
            return asset.local_id

        try:
            ma = append_material_asset(asset, bpy.context.asset_library_ref)
        except NotImplementedError:
            self.report({'ERROR'}, "Replacing the layer material with asset is"
                                   "not supported for this version.")
            return None

        if self.exit_stack is not None:
            self.exit_stack.callback(lambda: bpy.data.materials.remove(ma))

        return ma

    def _get_material(self, layer_stack) -> Optional[Material]:

        if self.material_name:
            material = bpy.data.materials.get(self.material_name)
            if material is None:
                self.report({'WARNING'},
                            f"Material '{self.material_name}' not found.")
                return None

        elif self.ma_select_mode == 'LOCAL' and self.ma_index >= 0:
            material = bpy.data.materials[self.ma_index]

        elif self.ma_select_mode == 'ASSET' and self.ma_asset_index >= 0:
            material = self._temp_append_material_asset()
        else:
            self.report({'WARNING'}, "No material specified.")
            return None

        if not self.check_material_valid(material, layer_stack):
            return None

        return material

    def draw_asset_compat(self, context, layout):
        prefs = get_addon_preferences()

        row = layout.row()
        if isinstance(prefs, bpy.types.AddonPreferences):
            row.prop(prefs, "check_assets_compat", text="Check Compatible")

        if prefs.check_assets_compat:
            is_compat = self._check_asset_compat(context)
            if is_compat is not None:
                row.label(text=is_compat.label_text_short,
                          icon=is_compat.label_icon)

    def draw_ma_asset_list(self, context, layout):
        wm = context.window_manager
        ws = context.workspace
        cls = type(self)

        if (len(wm.pml_ma_assets) > 24
                and cls.displayed_asset_lib == str(ws.asset_library_ref)):

            # template_asset_view doesn't seem to work very well with
            # large asset libraries fall back on a simpler UIList
            col = layout.column(align=True)
            col.label(text="Material assets can also be loaded via the ",
                           icon='INFO')
            col.label(text="sidebar of the Asset Browser.", icon='BLANK1')

            self.draw_asset_compat(context, col)

            col.prop(ws, "asset_library_ref")
            col.template_list("PML_UL_material_asset_list", "",
                              wm, "pml_ma_assets",
                              self, "ma_asset_index",
                              type='GRID', columns=3)
        else:
            self.draw_asset_compat(context, layout)

            # FIXME Can only select with the arrow keys
            layout.label(text="The selection can be changed using the arrow "
                              "keys")
            layout.template_asset_view(
                "pml_ma_asset_list",
                ws, "asset_library_ref",
                wm, "pml_ma_assets",
                self, "ma_asset_index",
                filter_id_types={"filter_material"}
            )
            if self.ma_asset_index >= len(wm.pml_ma_assets):
                self.ma_asset_index = 0
        # Even when falling back on a UIList need to display asset_view
        # once per-library to fill pml_ma_assets do this by comparing
        # displayed_asset_lib with asset_library_ref
        cls.displayed_asset_lib = str(ws.asset_library_ref)

    @property
    def selected_asset(self) -> Optional[bpy.types.AssetHandle]:
        wm = bpy.context.window_manager
        if not wm.pml_ma_assets:
            return None
        return wm.pml_ma_assets[self.ma_asset_index]


class PML_OT_replace_layer_material_ab(ReplaceLayerMaOpBase, Operator):
    """Replace Layer Material operator for the Asset Browser."""
    bl_idname = "material.pml_replace_layer_material_ab"
    bl_label = "Replace Layer Material"
    bl_description = ("Replaces the material of the active principled  "
                      "material layer")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not SpaceAssetInfo.is_asset_browser(context.space_data):
            return False

        active_file = context.active_file
        if active_file is None or active_file.id_type != 'MATERIAL':
            return False

        layer_stack = get_layer_stack(context)
        if not layer_stack or layer_stack.active_layer is None:
            return False
        return True

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        col.prop(self, "ch_detect_mode")
        if self.ch_detect_mode in ('MODIFIED_ONLY', 'MODIFIED_OR_ENABLED'):
            col.prop(self, "auto_enable_channels")

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

            return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def _get_material(self, context) -> Optional[Material]:
        if context.active_file is None:
            return None

        local_id = context.active_file.local_id
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
            ma = append_material_asset(context.active_file,
                                       context.asset_library_ref)
        except NotImplementedError:
            self.report({'ERROR'}, "Replacing the layer material with an "
                                   "asset is not supported for this version.")
            return None

        self.exit_stack.callback(lambda: bpy.data.materials.remove(ma))

        return ma


classes = (PML_UL_load_material_list,
           PML_OT_replace_layer_material,
           PML_OT_replace_layer_material_ab)

_register, _unregister = bpy.utils.register_classes_factory(classes)


def register():
    _register()

    bpy.types.WindowManager.pml_ma_assets = CollectionProperty(
                                              type=bpy.types.AssetHandle)


def unregister():
    del bpy.types.WindowManager.pml_ma_assets

    _unregister()
