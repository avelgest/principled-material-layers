# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it

import bpy

from bpy.props import BoolProperty
from bpy.types import UIList, UI_UL_list

from .. import blending

from ..preferences import get_addon_preferences


class PML_UL_material_layers_list(UIList):
    """UIList for displaying the layer stack's layers"""

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_property, index=0, flt_flag=0):

        layer = item

        prefs = get_addon_preferences()

        layout.scale_y = 1/prefs.layer_ui_scale

        row = layout.row(align=True)

        self.draw_layer_icon(row, layer)

        self.draw_layer_name(row, layer)

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

    def draw_layer_icon(self, layout, layer) -> None:
        prefs = get_addon_preferences()
        if not prefs.show_previews:
            return

        show_icon = (len(layer.channels) > 2)

        if prefs.use_large_icons:
            layout.template_icon(layer.preview_icon if show_icon else 0,
                                 scale=prefs.layer_ui_scale)
        elif not show_icon:
            layout.label(icon='BLANK1')
        else:
            layout.label(icon_value=layer.preview_icon)

    def draw_layer_name(self, layout, layer) -> None:
        channels = layer.channels
        show_channels = (channels and len(channels) <= 2)

        # For layers with a single blend channel show the channel name
        # below the layer name
        if show_channels:
            blend_chs = [x for x in channels if x.usage == 'BLENDING']
            ch_name = blend_chs[0].name if len(blend_chs) == 1 else ""

            col = layout.column(align=True)
            col.prop(layer, "name", text="", emboss=False)
            col.label(text=f"  [{ch_name}]")
        else:
            layout.prop(layer, "name", text="", emboss=False)

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


classes = (
    PML_UL_material_layers_list,
    PML_UL_layer_stack_channels_list,
    PML_UL_layer_channels_list,
    )

register, unregister = bpy.utils.register_classes_factory(classes)
