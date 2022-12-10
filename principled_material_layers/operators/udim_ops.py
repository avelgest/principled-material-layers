# SPDX-License-Identifier: GPL-2.0-or-later

import bpy

from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import Operator

from bpy_extras.io_utils import ImportHelper

from .. import tiled_storage

from ..utils.layer_stack_utils import get_layer_stack
from ..utils.ops import ensure_global_undo, pml_op_poll, save_all_modified


class PML_OT_select_udim_dir(Operator, ImportHelper):
    bl_idname = "material.pml_select_udim_dir"
    bl_label = "Select UDIM Folder"
    bl_description = "Select the folder to use for saving UDIM tiles"
    bl_options = {'INTERNAL'}

    filter_folder: BoolProperty(default=True, options={'HIDDEN'})
    relative_path: BoolProperty(
        name="Relative Path",
        description="Select a path relative to the current blend file",
        default=True
    )

    @classmethod
    def poll(cls, context):
        return get_layer_stack(context) is not None

    def draw(self, context):
        layout = self.layout
        layout.label(text="Select the folder to store UDIM tiles in.")
        layout.separator()
        layout.prop(self, "relative_path")

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        udim_layout = layer_stack.image_manager.udim_layout

        if self.relative_path and bpy.data.is_saved:
            udim_layout.image_dir = bpy.path.relpath(self.filepath)
        else:
            udim_layout.image_dir = self.filepath

        return {'FINISHED'}


class PML_OT_add_udim_layout_tile(Operator):
    bl_idname = "material.pml_add_udim_layout_tile"
    bl_label = "Add Tile"
    bl_description = "Adds a new UDIM tile to all paint layers"
    bl_options = {'REGISTER', 'UNDO'}

    number: IntProperty(
        name="Number",
        description="The tile's UDIM number",
        min=1001, soft_max=1099, max=2000, default=1001
    )
    label: StringProperty(
        name="Label",
        description="Label of the new tile"
    )
    width: IntProperty(
        name="Width",
        description="The width of the new tile",
        min=1, soft_max=2**14, default=1024,
        subtype='PIXEL'
    )
    height: IntProperty(
        name="Height",
        description="The height of the new tile",
        min=1, soft_max=2**14, default=1024,
        subtype='PIXEL'
    )

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        im = get_layer_stack(context).image_manager
        udim_layout = im.udim_layout

        tile = udim_layout.add_tile(self.number, self.width, self.height,
                                    is_float=im.use_float, label=self.label)
        udim_layout.active_tile = tile
        im.update_udim_images()

        ensure_global_undo()

        return {'FINISHED'}

    def invoke(self, context, _event):
        im = get_layer_stack(context).image_manager
        udim_layout = im.udim_layout

        if udim_layout.tiles:
            self.number = udim_layout.next_free_number
        self.width = im.image_width
        self.height = im.image_height

        wm = context.window_manager
        return wm.invoke_props_dialog(self)


class PML_OT_remove_udim_layout_tile(Operator):
    bl_idname = "material.pml_remove_udim_layout_tile"
    bl_label = "Remove Tile"
    bl_description = "Removes the selected UDIM tile from all paint layers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context):
            return False
        im = get_layer_stack(context).image_manager
        return im.udim_layout.active_tile is not None

    def execute(self, context):
        save_all_modified()

        im = get_layer_stack(context).image_manager
        udim_layout = im.udim_layout

        udim_layout.remove_tile(udim_layout.active_tile.number)

        im.update_udim_images()

        ensure_global_undo()
        return {'FINISHED'}


class PML_OT_add_to_tiled_storage(Operator):
    bl_idname = "material.pml_add_to_tiled_storage"
    bl_label = "Add to Tiled Storage"
    bl_description = ""
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        active_node = getattr(context, "active_node", None)
        return (pml_op_poll(context)
                and active_node is not None
                and isinstance(active_node, bpy.types.ShaderNodeTexImage)
                and get_layer_stack(context).image_manager.uses_tiled_storage)

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        nodes = set(context.selected_nodes)
        nodes.add(context.active_node)

        # Filter invalid nodes
        nodes = [x for x in nodes
                 if isinstance(x, bpy.types.ShaderNodeTexImage)
                 and x.image is not None
                 and x.image.source == 'FILE'
                 and not tiled_storage.is_tiled_storage_node(x)]

        if not nodes:
            self.report({'WARNING'}, "No valid nodes selected")
            return {'CANCELLED'}

        tiled_storage.add_nodes_to_tiled_storage(layer_stack, *nodes)

        return {'FINISHED'}


class PML_OT_remove_from_tiled_storage(Operator):
    bl_idname = "material.pml_remove_from_tiled_storage"
    bl_label = "Remove from Tiled Storage"
    bl_description = ""
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        active_node = getattr(context, "active_node", None)
        return (pml_op_poll(context)
                and active_node is not None
                and tiled_storage.is_tiled_storage_node(active_node))

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        nodes = set(context.selected_nodes)
        nodes.add(context.active_node)

        nodes = {x for x in nodes
                 if isinstance(x, bpy.types.ShaderNodeTexImage)
                 and tiled_storage.is_tiled_storage_node(x)}

        if not nodes:
            self.report({'WARNING'}, "No valid nodes selected")
            return {'CANCELLED'}

        tiled_storage.remove_from_tiled_storage(layer_stack, *nodes)

        return {'FINISHED'}


classes = (PML_OT_select_udim_dir,
           PML_OT_add_udim_layout_tile,
           PML_OT_remove_udim_layout_tile,
           PML_OT_add_to_tiled_storage,
           PML_OT_remove_from_tiled_storage
           )

register, unregister = bpy.utils.register_classes_factory(classes)
