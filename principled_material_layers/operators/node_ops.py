# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it
import typing

from typing import Optional

import bpy

from bpy.types import Node, Operator

from bpy.props import (BoolProperty,
                       StringProperty)

from .. import blending
from .. import hardness
from .. import tiled_storage
from .. import utils
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

    set_nodes_active: BoolProperty(
        name="Set Nodes Active",
        description="Try to find a path of Group nodes to Node Group and sets"
                    "them as active. Needed for compatibility with the Node "
                    "Wrangler's preview function",
        default=True
    )

    @classmethod
    def description(cls, _context, properties):
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

        if context.area in node_editor_areas:
            # Move the context's area to the front of the areas to check
            node_editor_areas.remove(context.area)
            node_editor_areas.insert(0, context.area)

        # Look for a shader editor without a pinned tree
        for area in node_editor_areas:
            space = area.spaces[0]  # The active space

            if self.can_use_space(context, space):

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

            if cls.can_use_space(context, space):
                return space
        return None

    @classmethod
    def can_use_space(cls, context, space) -> bool:
        return (space.type == 'NODE_EDITOR'
                and space.tree_type == 'ShaderNodeTree'
                # Allow pinned spaces only for context's space
                and (not space.pin or space == context.space_data)
                and space.node_tree is not None)

    def _close_node_group(self, context, area):
        op_caller = utils.ops.OpCaller(context, area=area,
                                       space_data=area.spaces[0],
                                       region=area.regions[0])

        op_caller.call(bpy.ops.node.group_edit)

    def _open_node_group(self, node_group, context, area):
        space = area.spaces[0]

        op_caller = utils.ops.OpCaller(context, area=area,
                                       space_data=space,
                                       region=area.regions[0])

        with TempNodes(space.edit_tree) as nodes:
            group_node = nodes.new("ShaderNodeGroup")
            group_node.node_tree = node_group
            group_node.select = True

            nodes.active = group_node

            op_caller.keywords["selected_nodes"] = [group_node]
            op_caller.keywords["active_node"] = group_node

            op_caller.call(bpy.ops.node.group_edit)

        if self.set_nodes_active:
            self._set_path_nodes_active(space, node_group)

    def _find_group_path(self, from_tree, to_tree, depth=0, max_depth=3
                         ) -> Optional[typing.List[Node]]:
        """Recursively finds a path of Group nodes between from_tree
        and to_tree. Returns a List of Group nodes or None if no path
        can be found or max_depth recursions are reached.
        """
        if depth == max_depth:
            return None

        for node in from_tree.nodes:
            if hasattr(node, "node_tree") and node.node_tree is not None:
                if node.node_tree == to_tree:
                    return [node]
                path = self._find_group_path(node.node_tree, to_tree, depth+1)
                if path is not None:
                    return [node] + path
        return None

    def _set_path_nodes_active(self, space, node_group) -> None:
        """Finds a path between the base node tree and node_group
        setting each group node to be the active node of their
        respective tree. Needed for compatibility with the Node
        Wrangler's preview op.
        """
        base_tree = space.node_tree

        path = self._find_group_path(base_tree, node_group)
        if path is not None:
            for node in path:
                node.id_data.nodes.active = node


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

    def draw(self, _context):
        layout = self.layout
        layout.activate_init = True
        layout.prop(self, "new_name")

        layout.active_default = True

    def execute(self, _context):
        node_group = bpy.data.node_groups.get(self.node_group_str)

        if node_group is None:
            self.report({'WARNING'}, "Cannot find node group "
                                     f"'{self.node_group_str}'.")
            return {'CANCELLED'}

        node_group.name = self.new_name
        return {'FINISHED'}

    def invoke(self, context, _event):
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

    def draw(self, _context):
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
                      "based on their socket names. Prioritizes linking Layer "
                      "Stack nodes. Does not replace input socket links")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.space_data.type != 'NODE_EDITOR':
            return False
        if context.active_node is None or len(context.selected_nodes) < 2:
            cls.poll_message_set("At least 2 nodes must be selected")
            return False
        return True

    def execute(self, context):
        active_node = context.active_node

        # Prioritize linking Layer Stacks over other nodes
        # N.B. Only do for PML nodes without inputs otherwise it would
        # be impossible to link those inputs with this operator.
        for node in context.selected_nodes:
            if (node.bl_rna.identifier == "ShaderNodePMLStack"
                    and not node.inputs):
                active_node = node
                break

        node_tree = context.space_data.edit_tree
        if node_tree.nodes.get(active_node.name) != active_node:
            self.report({'WARNING'}, "Active node is not in edit tree.")
            return {'CANCELLED'}

        input_sockets = it.chain(*[x.inputs for x in context.selected_nodes
                                   if x is not active_node])

        output_names = {x.name for x in active_node.outputs
                        if x.enabled and not x.hide}

        for in_socket in input_sockets:
            if in_socket.name in output_names and not in_socket.is_linked:
                node_tree.links.new(in_socket,
                                    active_node.outputs[in_socket.name])

        return {'FINISHED'}


class PML_OT_connect_to_group_output(Operator):
    bl_idname = "node.pml_connect_to_group_output"
    bl_label = "Link to Group Output"
    bl_description = ("Connects the output sockets of the selected nodes to "
                      "their corresponding sockets on the Group Output node")
    bl_options = {'REGISTER', 'UNDO'}

    replace_links: BoolProperty(
        name="Replace Links",
        description="",
        default=False
    )

    @classmethod
    def poll(cls, context):
        if getattr(context.space_data, "type", "") != 'NODE_EDITOR':
            return False
        edit_tree = context.space_data.edit_tree
        if edit_tree is None or not context.selected_nodes:
            return False
        # Check if edit_tree is a node group
        return not edit_tree.is_embedded_data

    def execute(self, context):
        node_tree = context.space_data.edit_tree

        output = utils.nodes.get_node_by_type(node_tree, "NodeGroupOutput")
        if output is None:
            self.report({'WARNING'}, "Cannot find a Group Output node")
            return {'CANCELLED'}

        group_out_socs = {x.name.casefold(): x for x in output.inputs}

        for node in context.selected_nodes:
            for out_socket in node.outputs:
                in_socket = group_out_socs.get(out_socket.name.casefold())
                if (in_socket is not None
                        and in_socket.type == out_socket.type
                        and (not in_socket.is_linked or self.replace_links)):
                    node_tree.links.new(in_socket, out_socket)

        return {'FINISHED'}

    def invoke(self, context, _event):
        return self.execute(context)


class PML_OT_add_to_tiled_storage(Operator):
    bl_idname = "node.pml_add_to_tiled_storage"
    bl_label = "Add to Tiled Storage"
    bl_description = ("Adds the images of the selected Image Texture nodes to "
                      "the layer stack's tiled storage")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if (not pml_op_poll(context)
                or not getattr(context, "selected_nodes", None)):
            return False

        return tiled_storage.tiled_storage_enabled(get_layer_stack(context))

    def execute(self, context):
        img_nodes = [x for x in context.selected_nodes
                     if x.bl_idname == "ShaderNodeTexImage"]
        if not img_nodes:
            self.report({'WARNING'}, "No Image Texture nodes selected")
            return {'CANCELLED'}

        layer_stack = get_layer_stack(context)
        tiled_storage.add_nodes_to_tiled_storage(layer_stack, *img_nodes)
        return {'FINISHED'}


class PML_OT_remove_from_tiled_storage(Operator):
    bl_idname = "node.pml_remove_from_tiled_storage"
    bl_label = "Remove from Tiled Storage"
    bl_description = ("Removes the images of the selected Image Texture nodes "
                      "from the layer stack's tiled storage")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not pml_op_poll(context) or not hasattr(context, "selected_nodes"):
            return False
        return any(x for x in context.selected_nodes
                   if x.bl_idname == "ShaderNodeTexImage"
                   and tiled_storage.is_tiled_storage_node(x))

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        img_nodes = [x for x in context.selected_nodes
                     if x.bl_idname == "ShaderNodeTexImage"]

        tiled_storage.remove_from_tiled_storage(layer_stack, *img_nodes)
        return {'FINISHED'}


classes = (PML_OT_view_shader_node_group,
           PML_OT_rebuild_pml_stack_node_tree,
           PML_OT_new_blending_node_group,
           PML_OT_new_hardness_node_group,
           PML_OT_rename_node_group,
           PML_OT_add_pml_node,
           PML_OT_verify_layer_outputs,
           PML_OT_link_sockets_by_name,
           PML_OT_connect_to_group_output,
           PML_OT_add_to_tiled_storage,
           PML_OT_remove_from_tiled_storage)

register, unregister = bpy.utils.register_classes_factory(classes)
