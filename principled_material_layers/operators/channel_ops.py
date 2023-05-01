# SPDX-License-Identifier: GPL-2.0-or-later

import bpy

from bpy.props import StringProperty
from bpy.types import Operator

from ..blending import blend_mode_description, blend_mode_display_name

from .. import utils
from ..pml_node import get_pml_nodes
from ..utils.layer_stack_utils import get_layer_stack
from ..utils.ops import pml_op_poll

# The name of the Group node used for previewing layer channels
PREVIEW_GROUP_NODE_NAME = "pml_preview_group_node"


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


_PREVIEW_OLD_LINK_NODE_PROP = "pml_preview_old_link_node"
_PREVIEW_OLD_LINK_SOCKET_PROP = "pml_preview_old_link_socket"


def store_output_link(node_tree, ma_output) -> None:
    """Store the names of the node and socket currently connected
    to the surface socket socket of the Material Output node ma_output
    as properties on the layer stack.
    These can be restored by restore_old_link.
    """
    nt = node_tree
    # If this operator is run twice or more without calling
    # restore_old_link then only the first call stores a link.
    if _PREVIEW_OLD_LINK_NODE_PROP in nt:
        return

    to_soc = ma_output.inputs[0]
    if to_soc.is_linked:
        from_soc = to_soc.links[0].from_socket
        nt[_PREVIEW_OLD_LINK_NODE_PROP] = from_soc.node.name
        nt[_PREVIEW_OLD_LINK_SOCKET_PROP] = from_soc.name
    else:
        nt[_PREVIEW_OLD_LINK_NODE_PROP] = ""
        nt[_PREVIEW_OLD_LINK_SOCKET_PROP] = ""


def restore_old_link(node_tree) -> None:
    """Restores the link of the surface socket of the Material Output
    node that was stored by store_output_link. Does nothing
    if the linked socket/node has been deleted/renamed or
    store_output_link has not been called.
    """
    node_name = node_tree.pop(_PREVIEW_OLD_LINK_NODE_PROP, "")
    socket_name = node_tree.pop(_PREVIEW_OLD_LINK_SOCKET_PROP, "")

    if node_name and socket_name:
        node = node_tree.nodes.get(node_name)
        if node is not None:
            socket = node.outputs.get(socket_name)
            if socket is not None:
                ma_output = utils.nodes.get_output_node(node_tree)
                node_tree.links.new(ma_output.inputs[0], socket)


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
            group_node.location = (self.ma_output.location.x - 300,
                                   self.ma_output.location.y + 100)
        return group_node

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

        store_output_link(self.node_tree, self.ma_output)

        self.node_tree.links.new(self.ma_output.inputs[0], socket)

        self.layer_stack.preview_channel = ch

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
        socket = group_node.outputs.get(self.channel_name)

        if socket is None:
            self.node_tree.remove(group_node)
            return {'CANCELLED'}

        store_output_link(self.node_tree, self.ma_output)
        self.node_tree.links.new(self.ma_output.inputs[0], socket)

        # Hide all other sockets on the group node
        for socket in group_node.outputs:
            socket.hide = (socket.name != self.channel_name)

        self.layer_stack.preview_channel = ch

        return {'FINISHED'}


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

        # Delete the layer preview group node
        group_node = node_tree.nodes.get(PREVIEW_GROUP_NODE_NAME)
        if group_node is not None:
            node_tree.nodes.remove(group_node)

        restore_old_link(node_tree)

        return {'FINISHED'}


classes = (PML_OT_channel_set_blend_mode,
           PML_OT_channel_set_custom_blend,
           PML_OT_channel_set_custom_hardness,
           PML_OT_copy_hardness_to_all,
           PML_OT_copy_hardness_to_all_ls,
           PML_OT_preview_channel,
           PML_OT_clear_preview_channel)


register, unregister = bpy.utils.register_classes_factory(classes)
