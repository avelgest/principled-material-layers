# SPDX-License-Identifier: GPL-2.0-or-later

from collections.abc import Collection, Container
from typing import Optional, Tuple

import bpy

from mathutils import Vector
from bpy.props import (BoolProperty,
                       IntProperty,
                       StringProperty)

from bpy.types import (NodeSocket,
                       Operator,
                       ShaderNodeSeparateRGB,
                       ShaderNodeTexImage,
                       ShaderNodeTree,
                       )

from ..bake import (BakedSocket,
                    LayerBaker,
                    LayerStackBaker,
                    PMLBakeSettings,
                    SocketBaker)

from ..preferences import get_addon_preferences

from ..utils.image import SplitChannelImageRGB
from ..utils.layer_stack_utils import get_layer_stack
from ..utils.ops import ensure_global_undo, pml_op_poll, save_all_modified


class BakeNodeOpBase:

    img_width: IntProperty(
        name="Width", subtype='PIXEL',
        default=1024, min=1, soft_max=2**14,
        description="The width of the images to bake to"
    )
    img_height: IntProperty(
        name="Height", subtype='PIXEL',
        default=1024, min=1, soft_max=2**14,
        description="The height of the images to bake to"
    )

    always_use_float: BoolProperty(
        name="Use Float",
        description="Bake to 32-bit float images for all sockets",
        default=False
    )

    share_images: BoolProperty(
        name="Scalars Share Images",
        description="Bake multiple scalar sockets to the same image",
        default=True
    )

    samples: IntProperty(
        name="Samples",
        default=4, min=0, soft_max=1024,
        description="The number of samples to use for baking "
                    "(unlimited if 0)"
    )

    uv_map: StringProperty(
        name="UV Map",
        description="The UV Map used by the baked images"
    )

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return (space.type == 'NODE_EDITOR'
                and space.tree_type == 'ShaderNodeTree')

    def draw(self, context):

        layout = self.layout

        layout.prop(self, "img_width", text="Width")
        layout.prop(self, "img_height", text="Height")

        layout.separator()
        layout.prop(self, "always_use_float")
        layout.prop(self, "share_images")

        layout.separator()
        layout.prop(self, "samples", text="Samples")

        obj = context.active_object

        if obj is not None and isinstance(obj.data, bpy.types.Mesh):
            layout.prop_search(self, "uv_map", obj.data, "uv_layers",
                               text="UV Map")

    def _create_nodes_for_baked_socket(
        self,
        node_tree: ShaderNodeTree,
        socket: NodeSocket,
        image: SplitChannelImageRGB) -> Tuple[ShaderNodeTexImage,
                                              Optional[ShaderNodeSeparateRGB]]:
        """Creates node(s) for a socket baked to 'image'"""

        socket_idx = socket.node.outputs.find(socket.name)

        img_node = node_tree.nodes.new("ShaderNodeTexImage")
        img_node.image = image.image

        img_node.location = (socket.node.location
                             + Vector((200, socket_idx * 40)))
        img_node.width = 140
        img_node.hide = True

        rgb_node = None

        if image.is_shared:
            rgb_node = node_tree.nodes.new("ShaderNodeSeparateRGB")
            rgb_node.location = img_node.location + Vector((200, 0))
            rgb_node.width = 100
            rgb_node.hide = True

            node_tree.links.new(rgb_node.inputs[0],
                                img_node.outputs[0])

        return img_node, rgb_node

    def _replace_with_baked(self, baked_sockets, only_replace=None):
        added_img_nodes = {}
        img_rgb_nodes = {}

        for baked in baked_sockets:
            socket, image, ch_idx = baked.socket, baked.image, baked.image_ch

            node_tree = socket.id_data
            assert isinstance(node_tree, ShaderNodeTree)

            img_node = added_img_nodes.get(image.image_name)
            if img_node is None:
                # rgb_node may be None
                img_node, rgb_node = self._create_nodes_for_baked_socket(
                                        node_tree, socket, image)

                added_img_nodes[image.image_name] = img_node
                img_rgb_nodes[image.image_name] = rgb_node

            if image.is_shared:
                assert ch_idx >= 0
                baked_output = img_rgb_nodes[image.image_name].outputs[ch_idx]
            else:
                baked_output = img_node.outputs[0]

            for link in socket.links:
                if only_replace is None or link.to_socket in only_replace:
                    node_tree.links.new(link.to_socket, baked_output)

    def bake_sockets(self, sockets: Collection[NodeSocket],
                     only_replace: Optional[Container] = None):
        """Bakes the given output sockets and replaces their links
        with links to the baked images. If given only_replace should
        be a container of input sockets and only links to the sockets
        in only_replace will be replaced."""

        if not sockets:
            return {'CANCELLED'}

        node_tree = sockets[0].node.id_data

        settings = PMLBakeSettings(image_width=self.img_width,
                                   image_height=self.img_height,
                                   uv_map=self.uv_map,
                                   samples=self.samples,
                                   share_images=self.share_images,
                                   always_use_float=self.always_use_float
                                   )

        baker = SocketBaker(node_tree, settings)
        baked = baker.bake_sockets(sockets)

        self._replace_with_baked(baked, only_replace)

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager

        if context.active_node is None:
            self.report({'WARNING'}, "No active node")
            return {'CANCELLED'}

        return wm.invoke_props_dialog(self)


class PML_OT_bake_node_outputs(BakeNodeOpBase, Operator):
    bl_idname = "node.pml_bake_node_outputs"
    bl_label = "Bake Node Outputs"
    bl_description = "Bake the outputs of the active node"
    bl_options = {'REGISTER', 'UNDO'}

    linked_only: BoolProperty(
        name="Linked Only", default=True,
        description="Only bake connected sockets"
    )

    @classmethod
    def poll(cls, context):
        if not get_addon_preferences().show_misc_ops:
            return False
        space = context.space_data
        if space is not None:
            if (space.type != 'NODE_EDITOR'
                    or space.tree_type != 'ShaderNodeTree'):
                return False
        return bool(context.active_node)

    def draw(self, context):
        super().draw(context)

        self.layout.prop(self, "linked_only")

    def execute(self, context):
        node = context.active_node

        if node is None:
            self.report({'ERROR'}, "No active node")
            return {'CANCELLED'}

        # The sockets to bake
        sockets = [x for x in node.outputs
                   if x.is_linked or not self.linked_only]

        if not sockets:
            msg = ("No linked output sockets found." if self.linked_only
                   else "No output sockets found.")

            self.report({'WARNING'}, msg)
            return {'CANCELLED'}

        return self.bake_sockets(sockets)


class PML_OT_bake_node_inputs(BakeNodeOpBase, Operator):
    bl_idname = "node.pml_bake_node_inputs"
    bl_label = "Bake Node Inputs"
    bl_description = "Bake any linked input of the active node"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not get_addon_preferences().show_misc_ops:
            return False
        space = context.space_data
        if space is not None:
            if (space.type != 'NODE_EDITOR'
                    or space.tree_type != 'ShaderNodeTree'):
                return False
        return bool(context.active_node)

    def execute(self, context):
        node = context.active_node

        if node is None:
            self.report({'ERROR'}, "No active node")
            return {'CANCELLED'}

        # The output sockets that the input sockets are linked with
        output_sockets = [x.links[0].from_socket for x in node.inputs
                          if x.is_linked]

        if not output_sockets:
            self.report({'WARNING'}, "No linked input sockets to bake.")
            return {'CANCELLED'}

        return self.bake_sockets(output_sockets,
                                 only_replace=list(node.inputs))


class PML_OT_bake_layer(Operator):
    bl_idname = "material.pml_bake_layer"
    bl_label = "Bake Layer"
    bl_description = ("Bake the layer's channels to images to improve "
                      "performance. The layer can still be painted on, but "
                      "changes to the layer's node tree will not take effect "
                      "until the bake is freed")
    bl_options = {'REGISTER', 'UNDO'}

    layer_name: StringProperty(
        name="Layer",
        description="The layer to bake"
    )

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        if not context.selected_objects:
            self.report({'WARNING'}, "No objects are selected for baking")
            return {'CANCELLED'}

        layer = layer_stack.layers.get(self.layer_name)
        if layer is None:
            self.report({'ERROR'}, "Layer stack has no layer named "
                                   f"'{self.layer_name}'")
            return {'CANCELLED'}

        if next((x for x in layer.channels if x.enabled), None) is None:
            self.report({'WARNING'}, f"Layer {layer.name} has no enabled "
                                     "channels")
            return {'CANCELLED'}

        obj = context.active_object
        if obj is None:
            self.report({'WARNING'}, "No active object")
            return {'CANCELLED'}

        save_all_modified()

        baker = LayerBaker(layer)

        baker.bake(skip_simple_const=True)

        layer.is_baked = True
        layer_stack.node_manager.rebuild_node_tree()

        ensure_global_undo()

        return {'FINISHED'}

    def invoke(self, context, event):
        if not self.layer_name:
            self.layer_name = get_layer_stack(context).active_layer.name
        return self.execute(context)


class PML_OT_free_layer_bake(Operator):
    bl_idname = "material.pml_free_layer_bake"
    bl_label = "Free Layer Bake"
    bl_description = "Frees all the layer's baked channels"
    bl_options = {'REGISTER', 'UNDO'}

    layer_name: StringProperty(
        name="Layer",
        description="The name of the layer to free the bake of"
    )

    @classmethod
    def poll(cls, context):
        return pml_op_poll(context)

    def execute(self, context):
        layer_stack = get_layer_stack(context)

        layer = layer_stack.layers.get(self.layer_name)
        if layer is None:
            self.report({'ERROR'}, "Layer stack has no layer named "
                                   f"'{self.layer_name}'")
            return {'CANCELLED'}

        save_all_modified()

        layer.free_bake()

        layer_stack.node_manager.rebuild_node_tree()

        ensure_global_undo()

        return {'FINISHED'}

    def invoke(self, context, event):
        if not self.layer_name:
            self.layer_name = get_layer_stack(context).active_layer.name
        return self.execute(context)


class PML_OT_bake_layer_stack(Operator):
    bl_idname = "material.pml_bake_layer_stack"
    bl_label = "Bake Layer Stack"
    bl_description = ("Bake all the channels of the layer stack. The bake "
                      "must be freed before any changes to the layer stack "
                      "can take effect")
    bl_options = {'REGISTER', 'UNDO'}

    hide_images: BoolProperty(
        name="Hide Images",
        description="Prefix all created images with '.'",
        default=True
    )
    samples: IntProperty(
        name="Samples",
        description="Number of samples to use for baking",
        default=64,
        min=1
    )
    size_percent: IntProperty(
        name="Bake Image Size", subtype='PERCENTAGE',
        description="Size of image to bake to",
        default=100, min=1, soft_max=100
    )
    use_float: BoolProperty(
        name="Use Float",
        description="Use 32-bit float images",
        default=False
    )

    @classmethod
    def poll(cls, context):
        return (pml_op_poll(context)
                and not get_layer_stack(context).is_baked)

    def draw(self, context):
        layout = self.layout

        im = get_layer_stack(context).image_manager

        layout.prop(self, "samples")
        layout.prop(self, "use_float")

        layout.separator()
        bake_size = self._get_bake_size(im)
        layout.prop(self, "size_percent",
                    text=f"Bake Size: {bake_size[0]} x {bake_size[1]}")

        layout.separator()
        layout.prop(self, "hide_images")

    def execute(self, context):
        layer_stack = get_layer_stack(context)
        im = layer_stack.image_manager

        if layer_stack.is_baked:
            self.report({'WARNING'}, "Layer stack is already baked.")
            return {'CANCELLED'}

        if not context.selected_objects:
            self.report({'WARNING'}, "No objects are selected for baking")
            return {'CANCELLED'}

        save_all_modified()

        bake_size = self._get_bake_size(im)
        settings = PMLBakeSettings(image_width=bake_size[0],
                                   image_height=bake_size[1],
                                   uv_map=layer_stack.uv_map_name,
                                   always_use_float=self.use_float,
                                   share_images=False,
                                   samples=self.samples)

        baker = LayerStackBaker(layer_stack, settings)

        baked = baker.bake()

        if self.hide_images:
            self._ensure_images_hidden(baked)

        layer_stack.node_manager.rebuild_node_tree()

        ensure_global_undo()

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)

    def _ensure_images_hidden(self, baked_sockets: BakedSocket) -> None:
        b_images = [x.b_image for x in baked_sockets]
        for img in b_images:
            if not img.name.startswith("."):
                img.name = f".{img.name}"

    def _get_bake_size(self, image_manager) -> Tuple[int, int]:
        ratio = self.size_percent / 100
        width = int(image_manager.image_width * ratio) // 32 * 32
        height = int(image_manager.image_height * ratio) // 32 * 32
        return (max(width, 32), max(height, 32))


class PML_OT_free_layer_stack_bake(Operator):
    bl_idname = "material.pml_free_layer_stack_bake"
    bl_label = "Free Layer Stack Bake"
    bl_description = "Frees any baked channels of the layer stack"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (pml_op_poll(context)
                and get_layer_stack(context).is_baked)

    def execute(self, context):
        save_all_modified()

        layer_stack = get_layer_stack(context)
        layer_stack.free_bake()
        layer_stack.node_manager.rebuild_node_tree()

        ensure_global_undo()

        return {'FINISHED'}


classes = (PML_OT_bake_node_inputs,
           PML_OT_bake_node_outputs,
           PML_OT_bake_layer,
           PML_OT_free_layer_bake,
           PML_OT_bake_layer_stack,
           PML_OT_free_layer_stack_bake)

_register, _unregister = bpy.utils.register_classes_factory(classes)


def register():
    _register()


def unregister():
    _unregister()
