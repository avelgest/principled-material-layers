# SPDX-License-Identifier: GPL-2.0-or-later

from typing import Optional

import bpy

from bpy.props import StringProperty
from bpy.types import Operator

from ..blending import blend_mode_description, blend_mode_display_name
from ..channel import PREVIEW_MODIFIERS_ENUM, preview_modifier_from_enum

from .. import utils
from ..pml_node import get_pml_nodes
from ..utils.layer_stack_utils import get_layer_stack
from ..utils.ops import pml_op_poll

# The name of the Group node used for previewing layer channels
PREVIEW_GROUP_NODE_NAME = "pml_preview_group_node"

# The name of the Group node between the preview and the material output
PREVIEW_MOD_NODE_NAME = "pml_preview_modifier_node"


class ChannelSetHardnessBlend:
    layer_name: StringProperty(name="Layer")
    channel_name: StringProperty(name="Channel Name")

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def draw(self, context):
        return

    def set_hardness_or_blend(self, context, prop, value):
        layer_stack = get_layer_stack(context)

        layer = layer_stack.layers.get(self.layer_name, None)
        if layer is None:
            self.report({'ERROR'}, "Current layer_stack has no layer named"
                                   f"{self.layer_name}")
            return {'CANCELLED'}

        channel = layer.channels.get(self.channel_name, None)
        if channel is None:
            self.report({'ERROR'}, f"Layer {self.layer_name} has no channel"
                                   f"named {self.channel_name}")
            return {'CANCELLED'}

        setattr(channel, prop, value)
        # Seems to be necessary to explicitly publish the rna
        bpy.msgbus.publish_rna(key=channel.path_resolve("blend_mode", False))
        return {'FINISHED'}


class PML_OT_channel_set_blend_mode(ChannelSetHardnessBlend, Operator):
    bl_idname = "material.pml_channel_set_blend_mode"
    bl_label = "Set Blend Mode"
    bl_description = "Sets the blend mode of a layer's channel."
    bl_options = {'INTERNAL', 'REGISTER'}

    blend_mode: StringProperty(name="Blend Mode")

    @classmethod
    def description(cls, context, properties):
        blend_mode = properties.blend_mode
        descript = blend_mode_description(blend_mode)
        return descript or blend_mode_display_name(blend_mode)

    def execute(self, context):
        return self.set_hardness_or_blend(context, "blend_mode",
                                          self.blend_mode)


class ChannelSetCustomHardnessBlend:
    """Base class for operators that set a custom hardness or blending
    node group on the channel specified by the context's pml_channel
    prop (should be set by context_pointer_set).
    """
    node_group: StringProperty(
        name="Node Group",
        description=("The name of the node group to use")
    )

    def set_custom_hardness_blend(self, context, prop, publish_prop):
        """Sets prop on the context's pml_channel to the operator's
        node_group property. Calls msgbus.publish_rna on the channel's
        property given by publish_prop."""
        channel = getattr(context, "pml_channel", None)
        if channel is None:
            self.report(
                {'ERROR'},
                "pml_channel should be set using context_pointer_set "
                "before this operator is called.")
            return {'CANCELLED'}

        node_group = bpy.data.node_groups.get(self.node_group)
        if node_group is None:
            self.report({'WARNING'}, "Cannot find node group "
                                     f"'{self.node_group}'")
            return {'CANCELLED'}

        # Set the given property to node_group
        setattr(channel, prop, node_group)

        # Publish rna on publish_prop.
        # Should be "blend_mode" or "hardness"
        bpy.msgbus.publish_rna(key=channel.path_resolve(publish_prop, False))

        return {'FINISHED'}


class PML_OT_channel_set_custom_blend(ChannelSetCustomHardnessBlend, Operator):
    bl_idname = "material.pml_channel_set_custom_blend"
    bl_label = "Set Custom Blend Group"
    bl_description = "Sets the custom blend group of a channel"
    bl_options = {'INTERNAL', 'REGISTER'}

    def execute(self, context):
        return self.set_custom_hardness_blend(context, "blend_mode_custom",
                                              "blend_mode")


class PML_OT_channel_set_custom_hardness(ChannelSetCustomHardnessBlend,
                                         Operator):
    bl_idname = "material.pml_channel_set_custom_hardness"
    bl_label = "Set Custom Hardness Group"
    bl_description = "Sets the custom hardness group of a channel"
    bl_options = {'INTERNAL', 'REGISTER'}

    def execute(self, context):
        return self.set_custom_hardness_blend(context, "hardness_custom",
                                              "hardness")


class PML_OT_copy_hardness_to_all(Operator):
    bl_idname = "material.pml_copy_hardness_to_all"
    bl_label = "Copy Hardness to All"
    bl_description = ("Copies the hardness of the active channel to every "
                      "other channel on the layer")
    bl_options = {'INTERNAL', 'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context):
            return False
        active_layer = get_layer_stack(context).active_layer
        return (active_layer is not None
                and active_layer.active_channel is not None)

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        layer = layer_stack.active_layer
        active_ch = layer.active_channel

        # Don't copy group/threshold for DEFAULT hardness
        # (since DEFAULT may refer to different hardness functions)
        if active_ch.hardness == 'DEFAULT':
            for ch in layer.channels:
                ch.hardness = active_ch.hardness
            return {'FINISHED'}

        is_custom = active_ch.hardness == 'CUSTOM'
        supports_threshold = active_ch.hardness_supports_threshold

        for ch in layer.channels:
            ch.hardness = active_ch.hardness

            if is_custom:
                ch.hardness_custom = active_ch.hardness_custom
            if supports_threshold:
                ch.hardness_threshold = active_ch.hardness_threshold

        layer_stack.node_manager.rebuild_node_tree()
        return {'FINISHED'}


# Version of pml_copy_hardness_to_all for the layer stack's channels
class PML_OT_copy_hardness_to_all_ls(Operator):
    bl_idname = "material.pml_copy_hardness_to_all_ls"
    bl_label = "Copy Hardness to All"
    bl_description = ("Copies the hardness of the active channel to every "
                      "other channel on the layer stack")
    bl_options = {'INTERNAL', 'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context):
            return False
        return get_layer_stack(context).active_channel is not None

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_ch = layer_stack.active_channel

        supports_threshold = active_ch.hardness_supports_threshold

        for ch in layer_stack.channels:
            ch.hardness = active_ch.hardness
            if active_ch.hardness == 'CUSTOM':
                ch.hardness_custom = active_ch.hardness_custom
            if supports_threshold:
                ch.hardness_threshold = active_ch.hardness_threshold

        layer_stack.node_manager.rebuild_node_tree()
        return {'FINISHED'}


# Prefix for id_props that store the old links of a material output
# node before a channel is previewed
_PREVIEW_OLD_LINK_PREFIX = "pml_preview_old_link"


def _preview_old_link_props(socket) -> tuple[str, str]:
    """id_prop names used to store the old links of a material output
    socket before a channel is previewed. Returns a tuple of two
    strings: the first is the property name that stores the name of the
    linked node and the other stores the name of the linked socket.
    """
    return (f"{_PREVIEW_OLD_LINK_PREFIX}_node_{socket.name}",
            f"{_PREVIEW_OLD_LINK_PREFIX}_socket_{socket.name}")


def store_output_links(node_tree, ma_output) -> None:
    """Store the names of the nodes and sockets currently connected
    to the input sockets of the Material Output node ma_output
    as properties on the node_tree.
    These can be restored by restore_old_links.
    """
    # TODO Remove node_tree parameter and access node tree through
    # ma_output.id_data?

    input_sockets = [x for x in ma_output.inputs if x.enabled]

    for socket in input_sockets:
        node_prop, socket_prop = _preview_old_link_props(socket)

        # If this operator is run twice or more without calling
        # restore_old_links then only the first call stores a link.
        if node_prop in node_tree:
            continue

        if socket.is_linked:
            from_soc = socket.links[0].from_socket
            node_tree[node_prop] = from_soc.node.name
            node_tree[socket_prop] = from_soc.name
        else:
            node_tree[node_prop] = ""
            node_tree[socket_prop] = ""


def restore_old_links(node_tree) -> None:
    """Restores the links of the input sockets of the Material Output
    node that were stored by store_output_links. Does nothing
    if the linked sockets/nodes have been deleted/renamed or
    store_output_links has not been called.
    """

    ma_output = utils.nodes.get_output_node(node_tree)
    input_sockets = [x for x in ma_output.inputs if x.enabled]

    # For backwards compatibility with old save files
    # TODO Remove in later version
    if ("pml_preview_old_link_node" in node_tree
            and "pml_preview_old_link_socket" in node_tree):
        node_prop, socket_prop = _preview_old_link_props(ma_output.inputs[0])
        node_tree[node_prop] = node_tree["pml_preview_old_link_node"]
        node_tree[socket_prop] = node_tree["pml_preview_old_link_socket"]
        del node_tree["pml_preview_old_link_node"]
        del node_tree["pml_preview_old_link_socket"]

    for socket in input_sockets:
        node_prop, socket_prop = _preview_old_link_props(socket)

        node_name = node_tree.pop(node_prop, "")
        socket_name = node_tree.pop(socket_prop, "")

        if node_name and socket_name:
            node = node_tree.nodes.get(node_name)
            if node is not None:
                from_soc = node.outputs.get(socket_name)
                if from_soc is not None:
                    node_tree.links.new(socket, from_soc)


class PML_OT_preview_channel(Operator):
    bl_idname = "node.pml_preview_channel"
    bl_label = "Preview Channel"
    bl_description = ("Connects the output of this channel directly to the "
                      "Material Output node")
    bl_options = {'INTERNAL', 'REGISTER'}

    layer_name: StringProperty(
        name="Layer Name",
        description="The name of the layer containing the channel to be "
                    "previewed"
    )
    channel_name: StringProperty(
        name="Channel Name",
        description="The name of the channel to be previewed"
    )

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    @classmethod
    def insert_preview_modifier(cls, channel, from_socket, to_socket) -> None:
        """Adds or removes the preview modifier group node based on
        channel's preview_modifier prop. preview_socket should be the
        socket that the preview node is linked to.
        """
        preview_modifier = preview_modifier_from_enum(channel.preview_modifier)
        node_tree = from_socket.id_data

        modifier_group = preview_modifier.load_node_group()

        modifier_node = node_tree.nodes.get(PREVIEW_MOD_NODE_NAME)
        if modifier_group is None:
            # If the preview modifier does not have a valid node group
            # (e.g. 'NONE') then just delete the preview modifier node
            if modifier_node is not None:
                node_tree.nodes.remove(modifier_node)
            return

        if modifier_node is None:
            modifier_node = node_tree.nodes.new("ShaderNodeGroup")
            modifier_node.name = PREVIEW_MOD_NODE_NAME
            modifier_node.label = "Preview Modifier"

        modifier_node.node_tree = modifier_group

        modifier_node.location = from_socket.node.location
        modifier_node.location.x += from_socket.node.width + 30
        modifier_node.location.y += 140

        if modifier_node.inputs:
            node_tree.links.new(modifier_node.inputs[0], from_socket)
        if modifier_node.outputs:
            node_tree.links.new(to_socket, modifier_node.outputs[0])

    def __init__(self):
        self.layer_stack = None
        self.node_tree = None
        self.ma_output = None

    def execute(self, context):
        self.layer_stack = get_layer_stack(context)
        self.node_tree = self.layer_stack.material.node_tree

        if self.node_tree is None:
            return {'CANCELLED'}

        self.ma_output = utils.nodes.get_output_node(self.node_tree)
        if self.ma_output is None:
            self.report({'WARNING'}, "Could not find a Material Output node")
            return {'CANCELLED'}

        if not self.layer_name:
            return self._preview_stack_channel()

        return self._preview_layer_channel()

    def _ensure_layer_group_node(self) -> bpy.types.ShaderNodeGroup:
        """Returns the group node for accessing the value of layers'
        channels. Creating a new node if necessary.
        """
        group_node = self.node_tree.nodes.get(PREVIEW_GROUP_NODE_NAME)
        if group_node is None:
            group_node = self.node_tree.nodes.new("ShaderNodeGroup")
            group_node.name = PREVIEW_GROUP_NODE_NAME
            group_node.hide = True
            group_node.location = (self.ma_output.location.x - 360,
                                   self.ma_output.location.y + 100)
        return group_node

    def _save_and_delete_ma_output_links(self) -> None:
        node_tree = self.node_tree
        ma_output = self.ma_output

        store_output_links(self.node_tree, ma_output)

        # TODO Move to store_output_links?
        for socket in ma_output.inputs:
            if socket.is_linked:
                node_tree.links.remove(socket.links[0])

    def _delete_layer_group_node(self) -> None:
        """Deletes the preview layer group node if it exists,"""
        group_node = self.node_tree.nodes.get(PREVIEW_GROUP_NODE_NAME)
        if group_node is not None:
            self.node_tree.nodes.remove(group_node)

    def _preview_stack_channel(self):
        pml_node = get_pml_nodes(self.layer_stack)[0]
        ch = self.layer_stack.channels.get(self.channel_name)

        if ch is None:
            self.report({'WARNING'}, "Channel {self.channel_name} not found")
            return {'CANCELLED'}

        socket = pml_node.outputs.get(self.channel_name)
        if socket is None:
            self.report({'WARNING'}, "Cannot find socket for channel "
                                     f"{self.channel_name}")
            return {'CANCELLED'}

        self._delete_layer_group_node()

        self._save_and_delete_ma_output_links()

        self.node_tree.links.new(self._ma_output_socket, socket)

        self.layer_stack.preview_channel = ch

        self.insert_preview_modifier(ch, socket, self._ma_output_socket)

        return {'FINISHED'}

    def _preview_layer_channel(self):
        layer = self.layer_stack.layers.get(self.layer_name)
        if layer is None:
            self.report({'WARNING'}, f"Cannot find layer {self.layer_name}")
            return {'CANCELLED'}

        ch = layer.channels.get(self.channel_name)
        if ch is None:
            self.report({'WARNING'}, f"Layer {layer.name} does not have a "
                                     f"{self.channel_name} channel")
            return {'CANCELLED'}

        group_node = self._ensure_layer_group_node()

        group_node.label = f"{layer.name} Preview"
        group_node.node_tree = layer.node_tree
        out_socket = group_node.outputs.get(self.channel_name)

        if out_socket is None:
            self.node_tree.remove(group_node)
            return {'CANCELLED'}

        self._save_and_delete_ma_output_links()
        self.node_tree.links.new(self._ma_output_socket, out_socket)

        # Hide all other sockets on the group node
        for socket in group_node.outputs:
            socket.hide = (socket.name != self.channel_name)

        self.layer_stack.preview_channel = ch

        self.insert_preview_modifier(ch, out_socket, self._ma_output_socket)

        return {'FINISHED'}

    @property
    def _ma_output_socket(self) -> bpy.types.NodeSocket:
        return self.ma_output.inputs[0]


class PML_OT_clear_preview_channel(Operator):
    bl_idname = "node.pml_clear_preview_channel"
    bl_label = "Clear Preview Channel"
    bl_description = ("Stop previewing a channel and restore the Material "
                      "Output node's previous connection")
    bl_options = {'INTERNAL', 'REGISTER'}

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        layer_stack.preview_channel = None

        node_tree = layer_stack.material.node_tree
        if node_tree is None:
            return {'CANCELLED'}

        # Delete the layer preview and the preview modifier nodes
        for node_name in (PREVIEW_GROUP_NODE_NAME, PREVIEW_MOD_NODE_NAME):
            node = node_tree.nodes.get(node_name)
            if node is not None:
                node_tree.nodes.remove(node)

        restore_old_links(node_tree)

        return {'FINISHED'}


class PML_OT_set_preview_modifier(Operator):
    bl_idname = "node.pml_set_preview_modifier"
    bl_label = "Set Preview Modifier"
    bl_description = "Sets a channel's preview type"
    bl_options = {'INTERNAL', 'REGISTER'}

    preview_modifier: bpy.props.EnumProperty(
        items=PREVIEW_MODIFIERS_ENUM,
        name="Preview Type",
        default='NONE'
    )
    layer_name: StringProperty(
        name="Layer Name",
        description="The name of the layer containing the channel to be "
                    "previewed"
    )
    channel_name: StringProperty(
        name="Channel Name",
        description="The name of the channel to be previewed"
    )

    @classmethod
    def description(cls, _context, properties):
        preview_mod = preview_modifier_from_enum(properties.preview_modifier)
        return preview_mod.description

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    @staticmethod
    def _get_preview_socket(node_tree,
                            channel) -> Optional[bpy.types.NodeSocket]:
        if channel.is_layer_channel:
            preview_node = node_tree.nodes.get(PREVIEW_GROUP_NODE_NAME)
            if preview_node:
                return preview_node.outputs.get(channel.name)
            return None

        pml_node = get_pml_nodes(channel.layer_stack)[0]
        return pml_node.outputs.get(channel.name)

    def _get_channel(self, layer_stack) -> Optional:
        if self.layer_name:
            layer = layer_stack.layers.get(self.layer_name)
            if layer:
                return layer.channels.get(self.channel_name)
            return None
        return layer_stack.channels.get(self.channel_name)

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        node_tree = layer_stack.material.node_tree

        if not self.channel_name:
            self.report({'WARNING'}, "channel_name prop not given or is empty")

        channel = self._get_channel(layer_stack)
        if channel is None:
            return {'CANCELLED'}

        channel.preview_modifier = self.preview_modifier

        # Replace or add the preview modifier group node only if
        # channel is currently being previewed
        if node_tree is None or not layer_stack.is_channel_previewed(channel):
            return {'FINISHED'}

        preview_socket = self._get_preview_socket(node_tree, channel)
        if preview_socket is None:
            return {'FINISHED'}

        ma_output = utils.nodes.get_output_node(node_tree)
        if ma_output is None:
            self.report({'WARNING'}, "Cannot find a Material Output node")
            return {'FINISHED'}

        ma_output_soc = ma_output.inputs[0]
        PML_OT_preview_channel.insert_preview_modifier(channel, preview_socket,
                                                       ma_output_soc)
        if not ma_output_soc.is_linked:
            # If the preview modifier node was just deleted
            # E.g. If changing to 'NONE' preview_modifier
            node_tree.links.new(ma_output_soc, preview_socket)

        return {'FINISHED'}


classes = (PML_OT_channel_set_blend_mode,
           PML_OT_channel_set_custom_blend,
           PML_OT_channel_set_custom_hardness,
           PML_OT_copy_hardness_to_all,
           PML_OT_copy_hardness_to_all_ls,
           PML_OT_preview_channel,
           PML_OT_clear_preview_channel,
           PML_OT_set_preview_modifier)


register, unregister = bpy.utils.register_classes_factory(classes)
