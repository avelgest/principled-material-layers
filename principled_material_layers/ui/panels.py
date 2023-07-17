# SPDX-License-Identifier: GPL-2.0-or-later

from bpy.types import NodeGroupOutput

from .. import bake_group
from .. import blending
from .. import hardness
from .. import image_mapping
from .. import utils

from ..preferences import get_addon_preferences
from ..utils.image import can_pack_udims
from ..utils.layer_stack_utils import get_layer_stack


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
        col.operator("material.pml_add_layer",
                     icon='ADD', text="").layer_type = 'MATERIAL_PAINT'
        col.operator("material.pml_remove_layer", icon='REMOVE', text="")

        col.separator()
        col.operator("material.pml_move_layer_up", icon='TRIA_UP', text="")
        col.operator("material.pml_move_layer_down", icon='TRIA_DOWN', text="")

    def draw_uninitialized(self, _context) -> None:
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
    def draw_ch_preview_options(cls, layout, channel) -> None:
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
        self.draw_ch_preview_options(layout, active_channel)

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

    def draw_channels_list(self, layout, layer_stack) -> None:
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
    def draw_custom_ch_node_group(layout, channel, prop, menu, compat) -> None:
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
                    col, active_channel
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
