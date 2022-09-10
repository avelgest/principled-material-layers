# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it

from typing import Optional

import bpy

from bpy.types import Operator

from bpy.props import (BoolProperty,
                       StringProperty)

from .. import blending
from .. import hardness
from ..utils.layer_stack_utils import get_layer_stack
from ..utils.nodes import ensure_outputs_match_channels
from ..utils.ops import pml_op_poll
from ..utils.temp_changes import TempNodes


class PML_OT_rebuild_pml_stack_node_tree(Operator):
    bl_idname = "material.pml_rebuild_stack_node_tree"
    bl_label = "Rebuild Layer Stack Node Tree"
    bl_description = ("Rebuilds the internal node tree of the active "
                      "material's PML layer stack")
    bl_options = {'INTERNAL', 'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        if not layer_stack.is_initialized:
            self.report({'WARNING'}, "Layer stack has not been initialized.")
            return {'CANCELLED'}

        layer_stack.node_manager.rebuild_node_tree()
        return {'FINISHED'}


class PML_OT_view_shader_node_group(Operator):
    bl_idname = "node.pml_view_shader_node_group"
    bl_label = "View Node Tree"
    bl_description = "View a node group in an open shader node editor"
    bl_options = {'INTERNAL', 'REGISTER'}

    node_group: StringProperty(
        name="Node Group",
        description="The name of the node group to view",
        default=""
    )

    custom_description: StringProperty(
        name="Description",
        default=""
    )

    @classmethod
    def description(cls, context, properties):
        return properties.custom_description or cls.bl_description

    @classmethod
    def poll(cls, context):
        if cls.find_available_editor(context) is None:
            cls.poll_message_set("Could not find an available "
                                 "shader node editor")
            return False
        return True

    def execute(self, context):
        if not self.node_group:
            self.report({'ERROR'}, "No node tree given")
            return {'CANCELLED'}

        node_group_to_view = bpy.data.node_groups.get(self.node_group)

        if node_group_to_view is None:
            self.report({'ERROR'}, "Could not find node group "
                                   f"{self.node_group}")
            return {'CANCELLED'}

        node_editor_areas = [a for a in context.screen.areas
                             if a.type == 'NODE_EDITOR']

        for area in node_editor_areas:
            node_tree = getattr(area.spaces[0], "edit_tree")

            # If node_tree is already open then cancel
            if node_tree is not None and node_tree is node_group_to_view:
                self.report({'INFO'}, "Node group is already open")
                return {'CANCELLED'}

        # Look for a shader editor without a pinned tree
        for area in node_editor_areas:
            space = area.spaces[0]  # The active space

            if (space.type == 'NODE_EDITOR' and
                    space.tree_type == 'ShaderNodeTree' and
                    not space.pin):

                if (space.edit_tree is not space.node_tree
                        and space.edit_tree.name.startswith(".")):
                    # If a node group is already open and it's a hidden
                    # node group then close it.
                    self._close_node_group(context, area)

                self._open_node_group(node_group_to_view, context, area)

                break
        else:
            self.report({'WARNING'},
                        "Could not find an available shader node editor")
            return {'CANCELLED'}

        return {'FINISHED'}

    @classmethod
    def find_available_editor(cls, context) -> Optional[bpy.types.Area]:
        node_editor_areas = (a for a in context.screen.areas
                             if a.type == 'NODE_EDITOR')

        for area in node_editor_areas:
            space = area.spaces[0]  # The active space

            if (space.type == 'NODE_EDITOR' and
                    space.tree_type == 'ShaderNodeTree' and
                    not space.pin):
                return area
        return None

    def _close_node_group(self, context, area):
        context = context.copy()
        context["area"] = area
        context["space_data"] = area.spaces[0]
        context["region"] = area.regions[0]

        bpy.ops.node.group_edit(context)

    def _open_node_group(self, node_group, context, area):
        space = area.spaces[0]

        # TODO Use temp_override in versions targeting only Blender 3.2+
        context = context.copy()
        context["area"] = area
        context["space_data"] = space
        context["region"] = area.regions[0]

        with TempNodes(space.edit_tree) as nodes:
            group_node = nodes.new("ShaderNodeGroup")
            group_node.node_tree = node_group
            group_node.select = True

            nodes.active = group_node

            context["selected_nodes"] = [group_node]
            context["active_node"] = group_node

            bpy.ops.node.group_edit(context)


class NewCustomHardnessBlendBase:
    """Base class for operators that create a new node group for a
    custom hardness or blending function.
    """
    open_in_editor: BoolProperty(
        name="Open in Shader Editor",
        default=False
    )
    set_on_active_channel: BoolProperty(
        default=False,
        description=("Set the property of the active layer's "
                     "active channel to the new node group")
    )

    def after_group_made(self, context, node_group, prop) -> None:
        """Method that should be called after the new group is made.
        Handles opening the group and setting it on the active channel.
        """
        # Set the active channel's prop to the new node group
        if self.set_on_active_channel:
            # If pml_channel has been by context_pointer_set then use
            # that otherwise use the active channel of the active layer
            channel = getattr(context, "pml_channel", None)
            if channel is None:
                layer_stack = get_layer_stack(context)
                if layer_stack and layer_stack.active_layer:
                    channel = layer_stack.active_layer.active_channel

            if channel is not None:
                setattr(channel, prop, node_group)
            else:
                assert hasattr(self, "report")
                self.report({'WARNING'}, "No active channel found: could not "
                            f"set {prop}.")

        if self.open_in_editor:
            open_op = bpy.ops.node.pml_view_shader_node_group
            if open_op.poll():
                open_op(node_group=node_group.name)


class PML_OT_new_blending_node_group(NewCustomHardnessBlendBase, Operator):
    bl_idname = "node.pml_new_blending_node_group"
    bl_label = "New Blending Group"
    bl_description = ("Create a new shader node group for use as a custom"
                      "blending operation")
    bl_options = {'INTERNAL', 'REGISTER'}

    def execute(self, context):
        # Create a default group
        node_group = blending.create_custom_blend_default("Custom Blend Group")

        self.after_group_made(context, node_group, "blend_mode_custom")

        return {'FINISHED'}


class PML_OT_new_hardness_node_group(NewCustomHardnessBlendBase, Operator):
    bl_idname = "node.pml_new_hardness_node_group"
    bl_label = "New Hardness Group"
    bl_description = ("Create a new shader node group for use as a custom"
                      "hardness function")
    bl_options = {'INTERNAL', 'REGISTER'}

    def execute(self, context):
        # Create a default group
        name = "Custom Hardness Group"
        node_group = hardness.create_custom_hardness_default(name)

        self.after_group_made(context, node_group, "hardness_custom")

        return {'FINISHED'}


class PML_OT_rename_node_group(Operator):
    bl_idname = "node.pml_rename_node_group"
    bl_label = "Rename"
    bl_description = ("Renames a node group")
    bl_options = {'INTERNAL', 'REGISTER', 'UNDO'}

    new_name: StringProperty(
        name="Name",
        description="The new name of the node group"
    )
    node_group_str: StringProperty(
        name="Node Group",
        description="The current name of the node group to rename"
    )

    def draw(self, context):
        layout = self.layout
        layout.activate_init = True
        layout.prop(self, "new_name")

        layout.active_default = True

    def execute(self, context):
        node_group = bpy.data.node_groups.get(self.node_group_str)

        if node_group is None:
            self.report({'WARNING'}, "Cannot find node group "
                                     f"'{self.node_group_str}'.")
            return {'CANCELLED'}

        node_group.name = self.new_name
        return {'FINISHED'}

    def invoke(self, context, event):
        node_group = bpy.data.node_groups.get(self.node_group_str)

        if node_group is None:
            self.report({'WARNING'}, "Cannot find node group "
                                     f"'{self.node_group_str}'.")
            return {'CANCELLED'}

        self.new_name = node_group.name

        wm = context.window_manager
        return wm.invoke_props_dialog(self)


class PML_OT_add_pml_node(Operator):
    bl_idname = "node.pml_add_pml_node"
    bl_label = "Add Layer Stack Node"
    bl_description = "Adds a Princripled Material Layers layer stack node"
    bl_options = {'REGISTER', 'UNDO'}

    connect_to_active: BoolProperty(
        name="Connect to Active",
        description="Create links between the added node and the active node",
        default=False
    )

    @classmethod
    def poll(cls, context):
        layer_stack = get_layer_stack(context)
        space = context.space_data

        return (layer_stack
                and space.type == 'NODE_EDITOR'
                and space.edit_tree is layer_stack.material.node_tree)

    def draw(self, context):
        return

    def execute(self, context):
        ma = context.active_object.active_material
        node_tree = ma.node_tree

        active_node = node_tree.nodes.active

        pml_node = node_tree.nodes.new("ShaderNodePMLStack")

        if self.connect_to_active and active_node is not None:
            pml_node.connect_outputs(active_node)

        return {'FINISHED'}


class PML_OT_verify_layer_outputs(Operator):
    bl_idname = "node.pml_verify_layer_outputs"
    bl_label = "Verify Layer Outputs"
    bl_description = ("Ensures the group output node of the active layer "
                      "has the correct output sockets")
    bl_options = {'INTERNAL', 'REGISTER'}

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        active_layer = layer_stack.active_layer
        if active_layer is None:
            return {'CANCELLED'}
        if active_layer.node_tree is None:
            self.report({'WARNING'}, "Active layer has no node group")
            return {'CANCELLED'}

        outputs = active_layer.node_tree.outputs
        channels = active_layer.channels

        ensure_outputs_match_channels(outputs, channels)

        return {'FINISHED'}


class PML_OT_link_sockets_by_name(Operator):
    bl_idname = "node.pml_link_sockets_by_name"
    bl_label = "Link Sockets by Name"
    bl_description = ("Link the active node's outputs to the selected node(s) "
                      "based on their socket names")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.space_data.type == 'NODE_EDITOR'
                and context.active_node is not None
                and len(context.selected_nodes) >= 2)

    def execute(self, context):
        active_node = context.active_node

        node_tree = context.space_data.edit_tree
        if node_tree.nodes.get(active_node.name) != active_node:
            self.report({'WARNING'}, "Active node is not in edit tree.")
            return {'CANCELLED'}

        input_sockets = it.chain(*[x.inputs for x in context.selected_nodes
                                   if x != active_node])

        output_names = {x.name for x in active_node.outputs if not x.hide}

        for in_socket in input_sockets:
            if in_socket.name in output_names:
                node_tree.links.new(in_socket,
                                    active_node.outputs[in_socket.name])

        return {'FINISHED'}


def add_pml_node_menu_func(self, context):
    layout = self.layout
    if PML_OT_add_pml_node.poll(context):
        op_props = layout.operator("node.add_node",
                                   text="Material Layers")
        op_props.type = "ShaderNodePMLStack"
        op_props.use_transform = True


classes = (PML_OT_view_shader_node_group,
           PML_OT_rebuild_pml_stack_node_tree,
           PML_OT_new_blending_node_group,
           PML_OT_new_hardness_node_group,
           PML_OT_rename_node_group,
           PML_OT_add_pml_node,
           PML_OT_verify_layer_outputs,
           PML_OT_link_sockets_by_name)

_register, _unregister = bpy.utils.register_classes_factory(classes)


def register():
    _register()
    bpy.types.NODE_MT_add.append(add_pml_node_menu_func)


def unregister():
    _unregister()
    bpy.types.NODE_MT_add.remove(add_pml_node_menu_func)
