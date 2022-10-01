# SPDX-License-Identifier: GPL-2.0-or-later

import bpy

from bpy.props import StringProperty
from bpy.types import Operator

from ..blending import blend_mode_description, blend_mode_display_name

from ..utils.layer_stack_utils import get_layer_stack
from ..utils.ops import pml_op_poll


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


classes = (PML_OT_channel_set_blend_mode,
           PML_OT_channel_set_custom_blend,
           PML_OT_channel_set_custom_hardness,
           PML_OT_copy_hardness_to_all,
           PML_OT_copy_hardness_to_all_ls)


register, unregister = bpy.utils.register_classes_factory(classes)
