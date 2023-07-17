# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it

import bpy

from bpy.types import Menu

from .. import blending
from .. import hardness
from .. import image_mapping
from .. import material_layer

from ..channel import PREVIEW_MODIFIERS
from ..preferences import get_addon_preferences
from ..utils.layer_stack_utils import get_layer_stack


class PML_MT_open_layer_group(Menu):
    bl_label = "Layers"
    bl_idname = "PML_MT_open_layer_group"
    bl_description = "Edit a layer's node tree"

    def draw(self, context):
        layout = self.layout
        prefs = get_addon_preferences()

        # Can be set by context_pointer_set
        layer_stack = getattr(context, "pml_layer_stack", None)
        if layer_stack is None:
            layer_stack = get_layer_stack(context)
        if layer_stack is None:
            return

        for layer in reversed(layer_stack.top_level_layers):
            icon_value = layer.preview_icon if prefs.show_previews else 0

            if layer.node_tree is None:
                continue

            op_props = layout.operator("node.pml_view_shader_node_group",
                                       text=layer.name, icon_value=icon_value)
            op_props.node_group = layer.node_tree.name
            op_props.custom_description = "Edit this layer's node tree"


class PML_MT_new_layer_menu(Menu):
    bl_label = "New Layer"
    bl_idname = "PML_MT_new_layer_menu"
    bl_description = ("Select a type of new layer to add to the top of the "
                      "stack")

    def draw(self, _context):
        layout = self.layout

        for enum_tuple in material_layer.LAYER_TYPES:
            enum, name = enum_tuple[:2]
            op_props = layout.operator("material.pml_add_layer", text=name)
            op_props.layer_type = enum

        layout.menu("PML_MT_new_single_channel_layer")


class PML_MT_new_single_channel_layer(Menu):
    """Menu for adding layers that directly paint the value of a single
    channel (Custom Alpha layers with an image node connected to the alpha
    and channel output).
    """

    bl_label = "Channel Paint"
    bl_idname = "PML_MT_new_single_channel_layer"
    bl_description = ("Add a layer for painting a channel's value directly."
                      "e.g. paint the RGB values of a color channel")

    def draw(self, context):
        layout = self.layout
        layout.operator_context = 'EXEC_DEFAULT'

        layer_stack = get_layer_stack(context)

        for ch in layer_stack.channels:
            if ch.enabled:
                op_props = layout.operator("material.pml_add_layer",
                                           text=ch.name)
                op_props.layer_type = 'MATERIAL_W_ALPHA'
                op_props.single_channel = ch.name


class PML_MT_convert_layer(Menu):
    """Menu for converting the active layer to a new layer type."""
    bl_label = "Convert Layer To"
    bl_idname = "PML_MT_convert_layer"
    bl_description = "Convert the active layer to a new layer type"

    def draw(self, context):
        layout = self.layout

        active_layer = get_layer_stack(context).active_layer
        if active_layer is None:
            return

        current_type = active_layer.layer_type

        for enum, name, *_ in material_layer.LAYER_TYPES:
            if current_type == enum:
                continue
            op_props = layout.operator("material.pml_convert_layer", text=name)
            op_props.new_type = enum
            op_props.keep_image = True


class PML_MT_add_channel_layer(Menu):
    """Menu for adding a channel to the active layer. The menu is a
    list of all the layer stack's enabled layers that are not on the
    active layer.
    """
    bl_label = "Add Channel"
    bl_idname = "PML_MT_add_channel_layer"
    bl_description = "Add a channel to the active layer"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = 'EXEC_DEFAULT'

        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        for ch in layer_stack.channels:
            if ch.enabled and ch.name not in active_layer.channels:
                op_props = layout.operator("material.pml_layer_add_channel",
                                           text=ch.name)
                op_props.channel_name = ch.name


class PML_MT_channel_blend_mode(Menu):
    """Menu for selecting the blend_mode of a channel of the active
    layer. The channel to change is specified using context_pointer_set
    with 'pml_channel'. Otherwise it defaults to the layer's active
    channel.
    """
    bl_label = "Blend Mode"
    bl_idname = "PML_MT_channel_blend_mode"
    bl_description = ("Selects the blend mode of the selected channel "
                      "of the active layer")

    def draw(self, context):
        layout = self.layout
        layout.operator_context = 'EXEC_DEFAULT'

        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        if active_layer is None:
            return

        # pml_channel should be set using context_pointer_set
        channel = getattr(context, "pml_channel", active_layer.active_channel)

        layout = layout.row(align=True)
        col = layout.column()
        row_counter = it.count()
        for enum_tuple in self._get_avail_blend_modes(channel):
            row_num = next(row_counter)

            if enum_tuple is None:
                if row_num >= 10:
                    row_counter = it.count()
                    col = layout.column()
                else:
                    col.separator()
                continue

            identifier, name, _ = enum_tuple[:3]
            op_props = col.operator("material.pml_channel_set_blend_mode",
                                    text=name)
            op_props.blend_mode = identifier

            op_props.layer_name = active_layer.name
            op_props.channel_name = channel.name

    def _get_avail_blend_modes(self, channel):
        """Returns a sequence of blend mode enum tuples for the blend
        modes that are available to channel."""
        if not channel.is_layer_channel:
            return [x for x in blending.BLEND_MODES
                    if x is None or x[0] != 'DEFAULT']
        return blending.BLEND_MODES


class CustomHardnessBlendSelectBase:
    """Base class for a menu that selects a custom hardness or blending
    node group.
    """

    @classmethod
    def poll(cls, context):
        layer_stack = get_layer_stack(context)
        if not layer_stack:
            return False

        active_layer = layer_stack.active_layer
        return (active_layer is not None
                and active_layer.active_channel is not None)

    def draw_menu(self, context, layout,
                  new_op=None, set_op=None, compat=None) -> bpy.types.UILayout:
        layout.operator_context = 'EXEC_DEFAULT'

        # pml_channel can be set using context_pointer_set
        channel = getattr(context, "pml_channel", None)
        if channel is None:
            channel = get_layer_stack(context).active_layer.active_channel

        layout.context_pointer_set("pml_channel", channel)

        row = layout.row(align=True)
        col = row.column()

        if new_op is not None:
            op_props = col.operator(new_op, text="New")
            op_props.open_in_editor = True
            op_props.set_on_active_channel = True

        for node_group in bpy.data.node_groups:
            if (node_group.name.startswith(".")
                    or not isinstance(node_group, bpy.types.ShaderNodeTree)):
                continue

            if compat and compat(node_group, strict=True):
                op_props = col.operator(set_op, text=node_group.name)
                op_props.node_group = node_group.name
        return col


class PML_MT_custom_blend_mode_select(CustomHardnessBlendSelectBase, Menu):
    """Menu for selecting the node group used by a channel with a custom
    blend_mode. The channel is the active_channel of the layer_stack's
    active_layer. This menu only displays node groups that can be used
    as blending operations.
    """
    bl_label = "Custom Blend Mode"
    bl_idname = "PML_MT_custom_blend_mode_select"
    bl_description = ("Select the node group to be used as a custom blending "
                      "operation. Only compatible node groups are displayed")

    def draw(self, context):
        self.draw_menu(context, self.layout,
                       new_op="node.pml_new_blending_node_group",
                       set_op="material.pml_channel_set_custom_blend",
                       compat=blending.is_group_blending_compat)


class PML_MT_custom_hardness_select(CustomHardnessBlendSelectBase, Menu):
    """Menu for selecting the node group used by a channel with a custom
    blend_mode. The channel is the active_channel of the layer_stack's
    active_layer. This menu only displays node groups that can be used
    as blending operations.
    """
    bl_label = "Custom Hardness"
    bl_idname = "PML_MT_custom_hardness_select"
    bl_description = ("Select the node group to be used as a custom hardness "
                      "function. Only compatible node groups are displayed")

    def draw(self, context):
        self.draw_menu(context, self.layout,
                       new_op="node.pml_new_hardness_node_group",
                       set_op="material.pml_channel_set_custom_hardness",
                       compat=hardness.is_group_hardness_compat)


class PML_MT_set_image_proj(Menu):
    """Menu for setting the projection of all image nodes in a layer's
    material. Used in the Image Mapping subpanel.
    """
    bl_idname = "PML_MT_set_image_proj"
    bl_label = "Set Image Projection"
    bl_description = ("Sets the projection of all image nodes in a layer's "
                      "material")

    def draw(self, _context):
        layout = self.layout
        for val, name, *_ in image_mapping.IMG_PROJ_MODES:

            layout.operator("material.pml_set_layer_img_proj",
                            text=name).proj_mode = val


class PML_MT_set_preview_channel(Menu):
    """Menu for setting the preview channel."""
    bl_idname = "PML_MT_set_preview_channel"
    bl_label = "Preview"
    bl_description = ("Connect a channel to the material output. Shift-click "
                      "a channel name to preview the same channel of the "
                      "active layer")

    def draw(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer
        active_layer_name = active_layer.name if active_layer else ""

        layout = self.layout
        for ch in layer_stack.channels:
            op_props = layout.operator("node.pml_preview_channel",
                                       text=ch.name)
            op_props.layer_name = active_layer_name
            op_props.channel_name = ch.name


class PML_MT_set_preview_modifier(Menu):
    """Menu for setting the preview modifier of the channel given by
    context.pml_preview_channel.
    """
    bl_idname = "PML_MT_set_preview_modifier"
    bl_label = "Set Preview Type"
    bl_description = ("Sets the preview type of the channel. Only takes "
                      "effect when the channel is being previewed")

    def draw(self, context):
        channel = getattr(context, "pml_preview_channel", None)
        if channel is None:
            return

        layer_name = "" if not channel.is_layer_channel else channel.layer.name
        channel_name = channel.name

        layout = self.layout
        for preview_modifier in PREVIEW_MODIFIERS:
            if not preview_modifier.should_show_for(channel):
                continue
            op_props = layout.operator("node.pml_set_preview_modifier",
                                       text=preview_modifier.name)
            op_props.preview_modifier = preview_modifier.enum
            op_props.layer_name = layer_name
            op_props.channel_name = channel_name


classes = (
    PML_MT_open_layer_group,
    PML_MT_new_layer_menu,
    PML_MT_new_single_channel_layer,
    PML_MT_convert_layer,
    PML_MT_add_channel_layer,
    PML_MT_channel_blend_mode,
    PML_MT_custom_blend_mode_select,
    PML_MT_custom_hardness_select,
    PML_MT_set_image_proj,
    PML_MT_set_preview_channel,
    PML_MT_set_preview_modifier,
    )


register, unregister = bpy.utils.register_classes_factory(classes)
