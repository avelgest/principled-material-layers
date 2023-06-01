# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it

import bpy

from bpy.props import BoolProperty
from bpy.types import Menu, NodeGroupOutput, UIList, UI_UL_list

from .. import bake_group
from .. import blending
from .. import hardness
from .. import image_mapping
from .. import material_layer
from .. import utils

from ..channel import PREVIEW_MODIFIERS
from ..preferences import get_addon_preferences

from ..utils.image import can_pack_udims
from ..utils.layer_stack_utils import get_layer_stack

# UILists


class PML_UL_material_layers_list(UIList):
    """UIList for displaying the layer stack's layers"""

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):

        layer = item

        prefs = get_addon_preferences()

        layout.scale_y = 1/prefs.layer_ui_scale

        row = layout.row(align=True)

        if prefs.show_previews:
            if prefs.use_large_icons:
                row.template_icon(layer.preview_icon,
                                  scale=prefs.layer_ui_scale)
            else:
                row.label(icon_value=layer.preview_icon)

        row.prop(layer, "name", text="", emboss=False)

        self.draw_layer_buttons(layout, layer)

    def draw_layer_buttons(self, layout, layer):
        col = layout.column(align=True)

        layer_stack = layer.layer_stack
        is_base_layer = layer.is_base_layer

        # First row
        row = col.row(align=True)

        # View layer nodes
        if layer.node_tree is not None:
            op_props = row.operator("node.pml_view_shader_node_group",
                                    text="", icon='NODETREE', emboss=False)
            op_props.node_group = layer.node_tree.name
            op_props.custom_description = ("Edit this layer's node tree in an "
                                           "open shader editor")
        # Layer enabled
        row1 = row.row()
        # N.B. The base layer's enabled prop is ignored
        row1.enabled = not is_base_layer
        row1.prop(layer, "enabled", icon_only=True, emboss=False,
                  icon="HIDE_OFF" if layer.enabled else "HIDE_ON")

        # Second row
        row = col.row(align=True)
        row.alignment = 'RIGHT'

        # Is in bake group indicator
        if layer_stack.bake_groups and layer in layer_stack.bake_groups[0]:
            # Currently only support one bake group (which must contain
            # the base layer)
            row.label(icon='TRIA_DOWN' if not is_base_layer
                           else 'TRIA_DOWN_BAR')

        # Bake layer material op
        bake_op = ("material.pml_free_layer_bake" if layer.is_baked
                   else "material.pml_bake_layer")

        op_props = row.operator(bake_op, text="", icon='EVENT_B',
                                emboss=layer.is_baked,
                                depress=layer.is_baked)
        op_props.layer_name = layer.name

    def draw_filter(self, context, layout):
        prefs = get_addon_preferences()

        col = layout.column(align=True)
        col.scale_y = 1/prefs.layer_ui_scale
        if isinstance(prefs, bpy.types.AddonPreferences):
            col.prop(prefs, "show_previews", text="Show Previews")
            col.prop(prefs, "use_large_icons", text="Large Icons")

    def filter_items(self, context, data, propname):
        layer_stack = data
        layers = getattr(data, propname)

        layer_indices = layer_stack.ordered_layer_indices()
        flags = [0] * len(layers)
        order = [0] * len(layers)

        shown_flag = self.bitflag_filter_item

        for idx, layer_idx in enumerate(layer_indices):
            flags[layer_idx] |= shown_flag
            order[layer_idx] = idx

        return flags, order


def draw_ch_preview_btn(layout, layer_stack, *, layer, channel) -> None:

    # If no layer is given then don't check the channels are on the
    # the same layer
    ignore_layer = (layer is not None)

    # Show the clear operator if given channel is currently previewed
    if layer_stack.is_channel_previewed(channel, ignore_layer=ignore_layer):
        # Use SHADING_SOLID icon if the previewed channel is on a layer
        # rather than on the layer stack itself
        icon = ("SHADING_SOLID" if layer_stack.layer_channel_previewed
                else "SHADING_TEXTURE")
        layout.operator("node.pml_clear_preview_channel", text="",
                        emboss=True, depress=True, icon=icon)
    else:
        op_props = layout.operator("node.pml_preview_channel", text="",
                                   emboss=False, icon="SHADING_TEXTURE")
        op_props.channel_name = channel.name
        op_props.layer_name = "" if layer is None else layer.name


class PML_UL_layer_stack_channels_list(UIList):
    """UIList for displaying the layer stack's channels."""

    sort_enabled: BoolProperty(
        name="Sort by Enabled",
        description="Show enabled channels at the top of the list",
        default=True
    )

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):

        layer_stack = data
        channel = item
        row = layout.row(align=True)
        row.prop(channel, "enabled", text="")
        row.label(text=channel.name)

        if channel.enabled:
            draw_ch_preview_btn(row, layer_stack, layer=None, channel=channel)
        else:
            row.label(text="", icon='BLANK1')

    def draw_filter(self, context, layout):
        layout.prop(self, "sort_enabled")

    def filter_items(self, context, data, propname):
        if not self.sort_enabled:
            return [], []

        channels = getattr(data, propname)

        num_enabled = len([x for x in channels if x.enabled])

        # Supplies the indices for enabled channels
        top_idxs = it.count()
        # Supplies the indices for disabled channels
        # (starts where top_idxs should end)
        bottom_idxs = it.count(num_enabled)

        # Take a value from top_idxs if the channel is enabled
        # or bottom_idxs if the channel is disabled
        order = [next(top_idxs if ch.enabled else bottom_idxs)
                 for ch in channels]

        return [], order


class PML_UL_layer_channels_list(UIList):
    """UIList for displaying a layers channels."""
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):

        layer = data
        channel = item
        layer_stack = layer.layer_stack

        if layer.is_base_layer or channel.usage != 'BLENDING':
            # Only show label for base layer channels
            row = layout.row()
            row.separator(factor=2.0)
            row.label(text=channel.name)

        else:
            split = layout.split(factor=0.55, align=True)
            row = split.row(align=True)

            row.prop(channel, "enabled", text="")
            row.label(text=channel.name)

            row = split.row(align=True)
            blend_name = blending.blend_mode_display_name(channel.blend_mode)
            row.context_pointer_set(name="pml_channel", data=channel)
            row.menu("PML_MT_channel_blend_mode", text=blend_name)

        draw_ch_preview_btn(row, layer_stack, layer=layer, channel=channel)

    def filter_items(self, context, data, propname):
        # Sort the channels by their order in layer_stack.channels
        layer_stack = data.layer_stack
        ls_channels = layer_stack.channels

        channels = getattr(data, propname)

        sort_data = list(enumerate(channels))
        order = UI_UL_list.sort_items_helper(
            sort_data,
            key=lambda x: ls_channels.find(x[1].name))

        return [], order

    def draw_filter(self, context, layout):
        pass


# Menus

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
                  new_op=None, set_op=None, compat=None):
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

    def draw(self, context):
        layout = self.layout
        for val, name, descr, *_ in image_mapping.IMG_PROJ_MODES:

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

# Panels


class layer_stack_PT_base:
    bl_label = "Material Painting"
    bl_options = set()

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def draw(self, context):
        layer_stack = get_layer_stack(context)
        if layer_stack is None or not layer_stack.is_initialized:
            self.draw_uninitialized(context)
        else:
            self.draw_initialized(context)

    def draw_initialized(self, context):
        layout = self.layout

        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer

        self.draw_layers_list(layout, layer_stack)

        col = layout.column(align=True)

        if active_layer:
            # Opacity Slider
            opacity_row = col.row()
            opacity_row.prop(active_layer, "opacity", slider=True)

            if active_layer.is_base_layer:
                # Cannot change opacity of the base layer
                opacity_row.enabled = False

            self.draw_edit_nodes_btn(col, active_layer)

            # Load material
            op_props = col.operator("material.pml_replace_layer_material")

            # Change layer type
            col.menu("PML_MT_convert_layer")

        layout.separator()

        # Layer stack baking / free bake operator
        is_baked = layer_stack.is_baked
        col = layout.column(align=True)
        if not is_baked:
            col.operator("material.pml_bake_layer_stack")
        else:
            col.operator("material.pml_free_layer_stack_bake")

        # Bake Layers Below / free bake operator
        if bake_group.BAKE_LAYERS_BELOW_NAME in layer_stack.bake_groups:
            op_props = col.operator("material.pml_free_bake_group",
                                    text="Free Baked Layers Below")
            op_props.group_name = bake_group.BAKE_LAYERS_BELOW_NAME
        else:
            col.operator("material.pml_bake_layers_below")

        # Apply Layer Stack
        col.operator("material.pml_apply_layer_stack")

    def draw_edit_nodes_btn(self, layout, active_layer):
        row = layout.row()
        op_props = row.operator("node.pml_view_shader_node_group",
                                text="Edit Nodes")
        op_props.custom_description = "Edit this layer's node tree"
        if active_layer and active_layer.node_tree is not None:
            op_props.node_group = active_layer.node_tree.name
        else:
            row.enabled = False

    def draw_layers_list(self, layout, layer_stack, rows=5):
        prefs = get_addon_preferences()

        row = layout.row(align=True)

        col = row.column()
        col.scale_y = prefs.layer_ui_scale

        col.template_list("PML_UL_material_layers_list", "", layer_stack,
                          "layers", layer_stack, "active_layer_index",
                          sort_lock=True, sort_reverse=True, rows=rows)
        col = row.column(align=True)
        col.menu("PML_MT_new_layer_menu", icon='ADD', text="")
        col.operator("material.pml_add_layer", icon='ADD', text="")
        col.operator("material.pml_remove_layer", icon='REMOVE', text="")

        col.separator()
        col.operator("material.pml_move_layer_up", icon='TRIA_UP', text="")
        col.operator("material.pml_move_layer_down", icon='TRIA_DOWN', text="")

    def draw_uninitialized(self, context):
        self.layout.operator("material.pml_initialize_layer_stack",
                             text="Initialize")


class layer_stack_channels_PT_base:
    bl_label = "Layer Stack Channels"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        layer_stack = get_layer_stack(context)
        return layer_stack is not None and layer_stack.is_initialized

    @classmethod
    def draw_ch_preview_options(cls, layout, layer_stack, channel) -> None:
        """Draws the preview type menu / preview button for channel."""
        row = layout.row(align=True)
        row.context_pointer_set("pml_preview_channel", channel)

        row.label(text="Preview Type:")

        menu_text = row.enum_item_name(channel, "preview_modifier",
                                       channel.preview_modifier)
        row.menu("PML_MT_set_preview_modifier", text=menu_text)

    def draw(self, context):
        layout = self.layout

        layer_stack = get_layer_stack(context)

        self.draw_channels_list(layout, layer_stack)

        active_channel = layer_stack.active_channel
        if active_channel is None:
            return

        row = layout.row()
        row.enabled = False
        row.prop(active_channel, "socket_type", text="Type")

        # Draw the preview type menu / preview button
        self.draw_ch_preview_options(layout, layer_stack, active_channel)

        if active_channel.socket_type == 'VECTOR':
            layout.prop(active_channel, "renormalize")

        # The blend modes of the layer stack's channels are the defaults
        # for its layers' channels
        layout.separator()
        layout.label(text="Default Blend Mode")
        layout.prop(active_channel, "blend_mode", text="")

        self.draw_custom_blending_props(layout, active_channel)

        # Effective value of hardness for layers with 'DEFAULT' hardness
        layout.separator()
        self.draw_hardness(layout, active_channel)

    def draw_channels_list(self, layout, layer_stack):
        active_channel = layer_stack.active_channel

        row = layout.row(align=True)
        row.template_list("PML_UL_layer_stack_channels_list", "",
                          layer_stack, "channels",
                          layer_stack, "active_channel_index",
                          maxrows=8, sort_lock=True)

        col = row.column(align=True)
        col.operator("material.pml_stack_add_channel", icon='ADD', text="")

        if active_channel is not None:
            op_props = col.operator("material.pml_stack_remove_channel",
                                    icon='REMOVE', text="")
            op_props.channel_name = active_channel.name

    def draw_hardness(self, layout, channel) -> None:
        col = layout.column(align=True)
        col.label(text="Default Hardness")

        row = col.row(align=True)
        row.prop(channel, "hardness", text="")
        row.operator("material.pml_copy_hardness_to_all_ls",
                     text="", icon='DUPLICATE')

        if channel.hardness_supports_threshold:
            col.prop(channel, "hardness_threshold")
        self.draw_custom_hardness_props(col, channel)

    @staticmethod
    def draw_custom_ch_node_group(layout, channel, prop, menu, compat):
        layout.context_pointer_set("pml_channel", channel)

        col = layout.column(align=True)
        col.label(text=type(channel).bl_rna.properties[prop].name)

        node_group = channel.path_resolve(prop)

        if node_group is None or not compat(node_group):
            col.label(text="Warning: the selected group is "
                           "incompatible.",
                      icon="ERROR")

        group_name = "" if node_group is None else node_group.name

        menu_text = group_name or "No node group selected"
        col.menu(menu, text=menu_text)

        row = col.row(align=True)
        row.enabled = node_group is not None

        op_props = row.operator("node.pml_view_shader_node_group",
                                text="Edit")
        op_props.node_group = group_name
        op_props = row.operator("node.pml_rename_node_group",
                                text="Rename")
        op_props.node_group_str = group_name

    @classmethod
    def draw_custom_blending_props(cls, layout, channel) -> None:
        if channel.blend_mode != 'CUSTOM':
            return
        cls.draw_custom_ch_node_group(layout, channel,
                                      "blend_mode_custom",
                                      menu="PML_MT_custom_blend_mode_select",
                                      compat=blending.is_group_blending_compat)

    @classmethod
    def draw_custom_hardness_props(cls, layout, channel) -> None:
        if channel.hardness != 'CUSTOM':
            return
        cls.draw_custom_ch_node_group(layout, channel,
                                      "hardness_custom",
                                      menu="PML_MT_custom_hardness_select",
                                      compat=hardness.is_group_hardness_compat)


class active_layer_PT_base:
    bl_label = "Active Layer"
    bl_options = set()

    @classmethod
    def poll(cls, context):
        layer_stack = get_layer_stack(context)
        return layer_stack is not None and layer_stack.is_initialized

    def draw(self, context):
        layout = self.layout

        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer
        if active_layer is None:
            return

        # Layer baking operators
        if active_layer.is_baked:
            op_props = layout.operator("material.pml_free_layer_bake")
        else:
            op_props = layout.operator("material.pml_bake_layer")
        op_props.layer_name = active_layer.name


class active_layer_channels_PT_base:
    """Base class for Channels subpanel of the active layer panel"""
    bl_label = "Channels"
    bl_options = set()

    def draw(self, context):
        layout = self.layout
        layer_stack = get_layer_stack(context)

        active_layer = layer_stack.active_layer
        if active_layer is None:
            return

        active_channel = active_layer.active_channel
        if active_channel is None:
            return

        col = layout.column(align=True)

        col.template_list("PML_UL_layer_channels_list", "", active_layer,
                          "channels", active_layer, "active_channel_index",
                          maxrows=8, sort_lock=False)

        is_base_layer = active_layer.is_base_layer

        if is_base_layer:
            col.label(text="Base layer channels are always enabled.")
        else:
            # Add/remove layer channel buttons
            row = col.row(align=True)
            row.menu("PML_MT_add_channel_layer", icon='ADD', text="")

        if active_channel is not None:
            # Preview type menu / preview button
            layer_stack_channels_PT_base.draw_ch_preview_options(
                    col, layer_stack, active_channel
                )

            if not is_base_layer and active_channel.usage == 'BLENDING':
                op_props = row.operator("material.pml_layer_remove_channel",
                                        icon='REMOVE', text="")
                op_props.channel_name = active_channel.name

                layout.prop(active_channel, "opacity")

                # Custom blend mode
                # Same UI as for layer stack channels
                self.draw_custom_blending_props(layout, active_channel)
                layout.separator()

                # Hardness
                self.draw_hardness(layout, active_channel)
                layout.separator()

        node_tree = active_layer.node_tree
        if node_tree is None or active_channel is None:
            return

        output_node = next((x for x in node_tree.nodes
                           if isinstance(x, NodeGroupOutput)), None)
        socket = output_node.inputs.get(active_channel.name)

        if output_node is not None and socket is not None:
            layout.template_node_view(node_tree, output_node, socket)

    @classmethod
    def draw_custom_blending_props(cls, layout, ch) -> None:
        # Same UI as for layer stack channels
        layer_stack_channels_PT_base.draw_custom_blending_props(layout, ch)

    @classmethod
    def draw_custom_hardness_props(cls, layout, ch) -> None:
        # Same UI as for layer stack channels
        layer_stack_channels_PT_base.draw_custom_hardness_props(layout, ch)

    @classmethod
    def draw_hardness(cls, layout, channel) -> None:
        col = layout.column(align=True)
        col.label(text="Hardness")

        row = col.row(align=True)
        row.prop(channel, "hardness", text="")
        row.operator("material.pml_copy_hardness_to_all",
                     text="", icon='DUPLICATE')

        if (channel.hardness != 'DEFAULT'
                and channel.hardness_supports_threshold):
            col.prop(channel, "hardness_threshold")

        cls.draw_custom_hardness_props(col, channel)


class active_layer_node_mask_PT_base:
    bl_label = "Node Mask"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layer_stack = get_layer_stack(context)

        active_layer = layer_stack.active_layer
        if active_layer is None:
            return

        row = layout.row(align=True)
        row.enabled = not active_layer.is_base_layer
        row.template_ID(active_layer, "node_mask",
                        new="material.pml_new_node_mask")

        if active_layer.node_mask is not None:
            op_props = row.operator("node.pml_view_shader_node_group",
                                    text="", icon='NODETREE')
            op_props.custom_description = "Edit this layer's node mask"
            op_props.node_group = getattr(active_layer.node_mask, "name", "")

            col = layout.column(align=True)
            col.operator("material.pml_apply_node_mask")
            col.operator("material.pml_node_mask_to_stencil")

            layout.separator()
            self.draw_node_view(layout, active_layer.node_mask)

    @classmethod
    def draw_node_view(cls, layout, node_mask) -> None:
        group_out = utils.nodes.get_node_by_type(node_mask, "NodeGroupOutput")
        if group_out is not None and group_out.inputs:
            layout.template_node_view(node_mask, group_out,
                                      group_out.inputs[0])


class active_layer_image_map_PT_base:
    bl_label = "Image Mapping"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        layer = get_layer_stack(context).active_layer
        if layer is None:
            return

        current_mode = layout.enum_item_name(layer, "img_proj_mode",
                                             layer.img_proj_mode)
        layout.menu("PML_MT_set_image_proj", text=current_mode)

        if layer.img_proj_mode == 'BOX':
            layout.prop(layer, "img_proj_blend")

        node_tree = layer.node_tree
        coord_map_node = node_tree.nodes.get(image_mapping.COORD_MAP_NODE_NAME)
        if coord_map_node is not None:
            col = layout.column(align=True)
            for socket in coord_map_node.inputs:
                if not socket.is_linked:
                    col.prop(socket, "default_value", text=socket.name)


class settings_PT_base:
    bl_label = "Settings"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        layer_stack = get_layer_stack(context)
        return layer_stack is not None and layer_stack.is_initialized

    def draw(self, context):
        layout = self.layout

        layer_stack = get_layer_stack(context)
        im = layer_stack.image_manager
        mesh = self._get_mesh(context)

        layout.prop_search(layer_stack, "uv_map_name",
                           mesh, "uv_layers", text="UV Map")
        layout.prop(layer_stack, "auto_connect_shader")
        layout.separator()

        if not im.uses_tiled_images:
            layout.label(text="Layer Size: {} x {}".format(*im.layer_size))
            layout.operator("material.pml_stack_resize_layers")

            col = layout.column(align=True)
            col.prop(im, "uses_tiled_storage")

            if im.uses_tiled_storage:
                col.prop(im, "bake_srgb_never")

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Bake Settings")
        col.prop(im, "bake_size_percent")
        row = col.row(align=True)
        row.alignment = 'LEFT'
        row.separator(factor=4.0)
        row.label(text="Bake Size:")
        row.label(text="{} x {}".format(*im.bake_size))

        col = layout.column(align=True)
        col.prop(im, "bake_samples")
        col.prop(im, "bake_float_always")
        col.prop(im, "bake_shared")
        col.prop(im, "bake_skip_simple")

    def _get_mesh(self, context):
        obj = context.active_object
        return None if obj is None else obj.data


class UDIM_PT_base:
    bl_label = "UDIM Tiles"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        layer_stack = get_layer_stack(context)
        return (layer_stack is not None
                and layer_stack.is_initialized
                and layer_stack.image_manager.uses_tiled_images)

    def draw(self, context):
        layout = self.layout
        layer_stack = get_layer_stack(context)
        udim_layout = layer_stack.image_manager.udim_layout

        if not can_pack_udims():
            layout.prop(udim_layout, "image_dir", text="Folder")
            layout.separator()

        row = layout.row()
        row.template_list("UI_UL_list", "pml_udim_tiles_list",
                          udim_layout, "tiles",
                          udim_layout, "active_index", rows=4)
        col = row.column(align=True)
        col.operator("material.pml_add_udim_layout_tile", text="", icon='ADD')
        col.operator("material.pml_remove_udim_layout_tile", text="",
                     icon='REMOVE')

        tile = udim_layout.active_tile
        if tile is not None:
            col = layout.column(align=True)
            col.alignment = 'RIGHT'
            col.label(text=f"{tile.number}")
            col.label(text=f"{tile.width} x {tile.height}, "
                           f"{'float' if tile.is_float else 'byte'}")


class debug_PT_base:
    bl_label = "Debug"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        layer_stack = get_layer_stack(context)
        return layer_stack is not None and layer_stack.is_initialized

    def draw(self, context):
        layout = self.layout
        layer_stack = get_layer_stack(context)

        op_props = layout.operator("node.pml_view_shader_node_group",
                                   text="View Stack Node Tree")
        op_props.node_group = layer_stack.node_tree.name
        op_props.custom_description = ("View the layer stack's internal "
                                       "node tree")

        col = layout.column(align=True)
        col.operator("material.pml_rebuild_stack_node_tree",
                     text="Rebuild Node Tree")

        col.operator("material.pml_reload_active_layer")

        col.operator("material.pml_resubscribe_msgbus")

        layout.separator()

        layout.operator("material.pml_delete_layer_stack")


classes = (
    PML_UL_material_layers_list,
    PML_UL_layer_stack_channels_list,
    PML_UL_layer_channels_list,
    PML_MT_open_layer_group,
    PML_MT_new_layer_menu,
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
