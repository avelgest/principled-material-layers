# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it
import typing
import warnings

from collections import defaultdict
from typing import Optional

import bpy

from mathutils import Vector
from bpy.types import NodeReroute, ShaderNode

from .on_load_manager import pml_trusted_callback
from .preferences import get_addon_preferences
from .utils.layer_stack_utils import (get_layer_stack_by_id,
                                      get_layer_stack_from_prop)
from .utils.nodes import ensure_outputs_match_channels


class NodeNames:
    """The methods in this class return the names used for the nodes
    created by the NodeManager.
    All methods are static methods that return strings.
    """

    @staticmethod
    def active_layer_image():
        """Image node containing the active image of the layer stack
        (the image that can currently be painted on).
        """
        return "pml_active_layer_image"

    @staticmethod
    def active_layer_image_rgb():
        """Split RGB node. The RGB values of active_layer_image."""
        return "pml_active_layer_image.split"

    @staticmethod
    def bake_image(image: bpy.types.Image):
        """Image node. Contains the baked data of 1-3 channels."""
        return image.name

    @staticmethod
    def bake_image_rgb(image: bpy.types.Image):
        """Split RGB node. The RGB values of a bake_image."""
        return f"{image.name}.rgb"

    @staticmethod
    def baked_value(layer, channel):
        """Reroute node that connects to a bake_image or bake_image_rgb
        node. Used instead of layer_material's output socket when the
        channel is baked.
        """
        return f"{layer.identifier}.baked.{channel.name}"

    @staticmethod
    def blend_node(layer, channel):
        """MixRGB or group node. Blends a layers channel with the channel
        from the previous layer. Will be a group node if using a custom
        blending function otherwise a MixRGB node.
        """
        return f"{layer.identifier}.blend.{channel.name}"

    @staticmethod
    def one_const():
        """Value node. Always has the value 1.0"""
        return "pml_one_const"

    @staticmethod
    def layer_alpha_x_opacity(layer):
        """Math node. Multiplies a layer's alpha value by its opacity."""
        return f"{layer.identifier}.alpha_x_opacity"

    @staticmethod
    def layer_frame(layer):
        """Frame containing the nodes specific to the layer."""
        return f"{layer.identifier}.frame"

    @staticmethod
    def layer_is_active(layer):
        """Value node. 1.0 if layer is the active layer otherwise 0.
        Only present if the layer uses a shared image.
        """
        return f"{layer.identifier}.is_active"

    @staticmethod
    def layer_is_active_mix(layer):
        """MixRGB node. Mixes the value from the layer's image with
        the value of active_layer_image using the layer's is_active
        value.
        Only present if the layer uses a shared image.
        """
        return f"{layer.identifier}.is_active_mix"

    @staticmethod
    def layer_material(layer):
        """Group node containing the layer's node tree (i.e. the
        material of the layer). Should have no inputs and an output
        for each channel of the layer.
        """
        return f"{layer.identifier}.material"

    @staticmethod
    def layer_node_mask(layer):
        """Group node containing the layer's node mask."""
        return f"{layer.identifier}.node_mask"

    @staticmethod
    def layer_opacity(layer):
        """Value node containing the layer's opacity.
        Only present if the layer's node mask is not None.
        """
        return f"{layer.identifier}.opacity"

    @staticmethod
    def layer_opacity_x_node_mask(layer):
        """Math node. Multiplies the layer's opacity by its node mask.
        Only present if the layer's node mask is not None.
        """
        return f"{layer.identifier}.opacity_x_node_mask"

    @staticmethod
    def output():
        """Group Output node for the layer stack's internal node tree."""
        return "pml_output"

    @staticmethod
    def paint_image(image):
        """Image node. Contains the image data for 1-3 layers."""
        return f"{image.name}"

    @staticmethod
    def paint_image_rgb(image):
        """Split RGB node. The individual channels of a paint_image.
        Each channel may contain a different layer's image data.
        """
        return f"{image.name}.split"

    @staticmethod
    def uv_map():
        """UV Map node. The UV map used by the layer stack."""
        return "pml_uv_map"

    @staticmethod
    def zero_const():
        """Value node. Always has the value 0.0"""
        return "pml_zero_const"


class NodeManager(bpy.types.PropertyGroup):
    """Class responsible for building and updating a LayerStack's
    internal node tree. Normally the node tree is rebuilt from scratch
    (using rebuild_node_tree) when any changes are required, though
    some changes (e.g. changing the active layer or changing a layer's
    blend_mode) simply update the existing node tree.
    """
    node_names = NodeNames()

    # Stores the msgbus owners for each instance of this class
    # (mapped by layer_stack.identifier).
    _cls_msgbus_owners = defaultdict(lambda: defaultdict(dict))

    def initialize(self, layer_stack) -> None:
        """Initializes the layer_stack. Must be called before the
        NodeManager is used.
        """
        if layer_stack.id_data is not self.id_data:
            raise ValueError("layer_stack has a different id_data to this "
                             "node manager")

        self["layer_stack_path"] = layer_stack.path_from_id()

        self.initialize_node_tree()

        self._register_msgbus()

        self["_on_load_cb"] = layer_stack.add_on_load_callback(self._on_load)

    def delete(self) -> None:
        """Deletes the NodeManager. Initialize must be called before
        the NodeManager can be used again.
        """
        on_load_cb = self.get("_on_load_cb")
        if on_load_cb:
            self.layer_stack.remove_on_load_callback(on_load_cb)

        self._unregister_msgbus()
        self.pop("layer_stack_path", None)

    @pml_trusted_callback
    def _on_load(self) -> None:
        """Called when the blend file is loaded."""
        self._register_msgbus()

    def _add_base_layer(self, layer) -> None:
        """Creates the nodes for the base layer of the layer stack."""
        node_tree = self.layer_stack.node_tree
        nodes = node_tree.nodes

        base_ma_group = nodes.new("ShaderNodeGroup")
        base_ma_group.name = self.node_names.layer_material(layer)
        base_ma_group.label = layer.name
        base_ma_group.node_tree = layer.node_tree

        if layer.any_channel_baked:
            self._insert_layer_bake_nodes(layer)

    def _add_paint_image_nodes(self):
        """Add nodes for the images that store the layers' alpha values"""
        image_manager = self.layer_stack.image_manager
        nodes = self.nodes
        links = self.links

        uv_map = nodes.get(self.node_names.uv_map())

        for idx, image in enumerate(image_manager.layer_images_blend):
            image_node = nodes.new("ShaderNodeTexImage")
            image_node.name = self.node_names.paint_image(image)
            image_node.label = image.name
            image_node.image = image
            image_node.width = 120
            image_node.location = (idx * 500, 600)

            links.new(image_node.inputs[0], uv_map.outputs[0])

            split_rgb_node = nodes.new("ShaderNodeSeparateRGB")
            split_rgb_node.name = self.node_names.paint_image_rgb(image)
            split_rgb_node.label = f"{image.name} RGB"
            split_rgb_node.location = (idx * 500 + 200, 600)

            links.new(split_rgb_node.inputs[0], image_node.outputs[0])

    def _add_bake_image_nodes(self) -> None:
        """Add nodes that store the values of any baked channels (from
        either layers or the layer stack).
        """
        node_tree = self.node_tree
        nodes = node_tree.nodes
        links = node_tree.links

        image_manager = self.layer_stack.image_manager
        uv_map = nodes.get(self.node_names.uv_map())

        for idx, image in enumerate(image_manager.bake_images_blend):
            image_node = nodes.new("ShaderNodeTexImage")
            image_node.name = self.node_names.bake_image(image)
            image_node.label = image_node.name
            image_node.image = image
            image_node.width = 120
            image_node.hide = True
            image_node.location = (-400, -240 - idx*40)

            links.new(image_node.inputs[0], uv_map.outputs[0])

            split_rgb_node = nodes.new("ShaderNodeSeparateRGB")
            split_rgb_node.name = self.node_names.bake_image_rgb(image)
            split_rgb_node.label = f"{image.name} RGB"
            split_rgb_node.hide = True

            split_rgb_node.location = image_node.location
            split_rgb_node.location.x += 160

            links.new(split_rgb_node.inputs[0], image_node.outputs[0])

    def _add_opacity_driver(self, socket, layer):
        """Adds a driver to a float socket so that it is driven by the
        layer's opacity value.
        """
        f_curve = socket.driver_add("default_value")
        driver = f_curve.driver
        driver.type = 'SUM'

        var = driver.variables.new()
        var.name = "var"
        var.type = 'SINGLE_PROP'

        var_target = var.targets[0]
        var_target.id_type = 'MATERIAL'
        var_target.id = layer.id_data
        var_target.data_path = layer.path_from_id("opacity")

        return f_curve

    def get_layer_final_alpha_socket(self, layer, nodes=None):
        """Returns the socket that gives the alpha value of the layer
        after any masks and the opacity have been applied.
        """
        if nodes is None:
            nodes = self.nodes

        node_name = self.node_names.layer_alpha_x_opacity(layer)
        return nodes[node_name].outputs[0]

    def _get_ma_group_output_socket(self, layer, channel, ma_group=None):
        if ma_group is None:
            ma_group = self.nodes[self.node_names.layer_material(layer)]

        if channel.is_baked:
            ma_group_output = self._get_bake_image_socket(layer, channel)
        else:
            ma_group_output = ma_group.outputs.get(channel.name)

        if ma_group_output is None:
            warnings.warn(f"Cannot find output socket '{channel.name}' for "
                          f"the node group of layer '{layer.name}' "
                          f"{'(baked)' if channel.is_baked else ''}")
            ma_group_output = self._zero_const_output_socket

        return ma_group_output

    def _get_layer_output_socket(self, layer, channel):

        if layer == self.layer_stack.base_layer:
            node_name = self.node_names.layer_material(layer)
            node = self.nodes[node_name]
            output_socket = node.outputs.get(channel.name)
            if output_socket is None:
                warnings.warn(f"Socket for {channel.name} not found in base "
                              "layer node group.")
                return self._zero_const_output_socket
            return node.outputs[channel.name]

        node_name = self.node_names.blend_node(layer, channel)
        return self.nodes[node_name].outputs[0]

    def _get_bake_image_socket(self, layer, layer_ch):
        node_name = self.node_names.baked_value(layer, layer_ch)
        return self.nodes[node_name].outputs[0]

    def _get_paint_image_socket(self, layer):
        names = self.node_names

        if layer.layer_type == 'MATERIAL_FILL':
            return self.nodes[names.one_const()].outputs[0]

        if layer.uses_shared_image:
            node = self.nodes[names.paint_image_rgb(layer.image)]
            return node.outputs[layer.image_channel]

        node = self.nodes[names.paint_image(layer.image)]
        return node.outputs[0]

    def _insert_layer_bake_nodes(self, layer, parent=None) -> None:
        """Adds a reroute node for each baked channel of 'layer', that
        connects to the channel's baked value. The parent of the new
        nodes will be set to 'parent'.
        """
        node_tree = self.node_tree
        nodes = node_tree.nodes
        links = node_tree.links
        node_names = self.node_names

        ma_group = nodes[node_names.layer_material(layer)]

        for idx, ch in enumerate(layer.channels):
            if not ch.is_baked:
                continue

            if ch.bake_image_channel >= 0:
                bake_node = nodes[node_names.bake_image_rgb(ch.bake_image)]
                bake_socket = bake_node.outputs[ch.bake_image_channel]
            else:
                bake_node = nodes[node_names.bake_image(ch.bake_image)]
                bake_socket = bake_node.outputs[0]

            baked_value_node = nodes.new("NodeReroute")
            baked_value_node.name = node_names.baked_value(layer, ch)
            baked_value_node.label = ch.name
            baked_value_node.location = ma_group.location
            baked_value_node.location.x += 160
            baked_value_node.location.y -= idx * 20

            baked_value_node.parent = parent

            links.new(baked_value_node.inputs[0], bake_socket)

    def _insert_layer_blend_nodes(self, layer, previous_layer, alpha_socket,
                                  parent=None) -> None:

        layer_stack = self.layer_stack
        node_tree = layer_stack.node_tree
        links = node_tree.links

        node_names = self.node_names

        # The ShaderNodeGroup using this layer's node tree
        ma_group = self.nodes[node_names.layer_material(layer)]

        ch_count = it.count()
        for ch in layer_stack.channels:
            if not ch.enabled:
                continue

            layer_ch = layer.channels.get(ch.name)
            if layer_ch is None or not layer_ch.enabled:
                ch_blend = node_tree.nodes.new("NodeReroute")

            else:
                ch_blend = layer_ch.make_blend_node(node_tree)
                ch_blend.hide = True

            ch_blend.name = node_names.blend_node(layer, ch)
            ch_blend.label = f"{ch.name} Blend"
            ch_blend.parent = parent
            ch_blend.location = (640, next(ch_count) * -50 + 150)

            prev_layer_ch_out = self._get_layer_output_socket(previous_layer,
                                                              ch)

            if isinstance(ch_blend, NodeReroute):
                links.new(ch_blend.inputs[0], prev_layer_ch_out)
                continue

            ma_group_output = self._get_ma_group_output_socket(
                                    layer, layer_ch, ma_group)

            links.new(ch_blend.inputs[0], alpha_socket)
            links.new(ch_blend.inputs[1], prev_layer_ch_out)
            links.new(ch_blend.inputs[2], ma_group_output)

    def _insert_layer(self, layer) -> bpy.types.NodeFrame:
        layer_stack = self.layer_stack
        nodes = layer_stack.node_tree.nodes
        links = layer_stack.node_tree.links

        names = self.node_names

        # Index of the layer in the top level of the layer stack
        position = layer_stack.top_level_layers_ref.find(layer.identifier)

        if position == 0:
            raise NotImplementedError("Replacing base layer not implemented")

        previous_layer = layer_stack.top_level_layers_ref[position-1].resolve()

        # Frame containing all the nodes specific to this layer
        frame = nodes.new("NodeFrame")
        frame.name = names.layer_frame(layer)
        frame.label = f"{layer.name}"
        frame.use_custom_color = True
        frame.color = (0.1, 0.1, 0.6)

        # The Group node containing this layer's node tree
        ma_group = nodes.new("ShaderNodeGroup")
        ma_group.node_tree = layer.node_tree
        ma_group.name = names.layer_material(layer)
        ma_group.label = layer.name
        ma_group.parent = frame
        ma_group.hide = True
        ma_group.location = (0, -100)

        opacity = nodes.new("ShaderNodeValue")
        opacity.name = names.layer_opacity(layer)
        opacity.label = f"{layer.name} Opacity"
        opacity.parent = frame
        opacity.location = (200, 300)

        self._add_opacity_driver(opacity.outputs[0], layer)

        if layer.any_channel_baked:
            self._insert_layer_bake_nodes(layer, parent=frame)

        # The socket for this layer's image data
        layer_image_socket = self._get_paint_image_socket(layer)

        if layer_stack.layers_share_images:
            # The image node for the layer stack's active layer
            active_layer_image = nodes[names.active_layer_image_rgb()]

            is_active = nodes.new("ShaderNodeValue")
            is_active.name = names.layer_is_active(layer)
            is_active.label = f"{layer.name} Is Active?"
            is_active.parent = frame
            is_active.location = (0, 300)

            is_active_mix = nodes.new("ShaderNodeMixRGB")
            is_active_mix.blend_type = 'MIX'
            is_active_mix.name = names.layer_is_active_mix(layer)
            is_active_mix.label = f"{layer.name} Is Active? Mix"
            is_active_mix.parent = frame
            is_active_mix.hide = True
            is_active_mix.location = (200, 200)

            links.new(is_active_mix.inputs[0], is_active.outputs[0])
            links.new(is_active_mix.inputs[1], layer_image_socket)
            links.new(is_active_mix.inputs[2], active_layer_image.outputs[0])

            alpha_x_opacity = nodes.new("ShaderNodeMath")
            alpha_x_opacity.operation = 'MULTIPLY'
            alpha_x_opacity.name = names.layer_alpha_x_opacity(layer)
            alpha_x_opacity.label = f"{layer.name} Active x Opacity"
            alpha_x_opacity.parent = frame
            alpha_x_opacity.hide = True
            alpha_x_opacity.location = (400, 250)

            links.new(alpha_x_opacity.inputs[0], opacity.outputs[0])
            links.new(alpha_x_opacity.inputs[1], is_active_mix.outputs[0])

        else:
            alpha_x_opacity = nodes.new("ShaderNodeMath")
            alpha_x_opacity.operation = 'MULTIPLY'
            alpha_x_opacity.name = names.layer_alpha_x_opacity(layer)
            alpha_x_opacity.label = f"{layer.name} Alpha x Opacity"
            alpha_x_opacity.parent = frame
            alpha_x_opacity.location = (400, 300)

            links.new(alpha_x_opacity.inputs[0], opacity.outputs[0])
            links.new(alpha_x_opacity.inputs[1], layer_image_socket)

        if layer.node_mask is not None:
            self._insert_layer_mask_node(layer)

        self._insert_layer_blend_nodes(layer, previous_layer,
                                       alpha_x_opacity.outputs[0],
                                       parent=frame)

        frame.location = (850*(position-1) + 300, -100)
        return frame

    def _insert_layer_mask_node(self, layer) -> None:
        layer_stack = self.layer_stack
        node_tree = layer_stack.node_tree
        nodes = node_tree.nodes
        links = node_tree.links

        names = self.node_names

        if not layer.node_mask.outputs:
            warnings.warn(f"{layer.name}'s node_mask must have at least one "
                          "output.")
            return

        # The node that contains the layer's opacity value
        opacity_node = nodes[names.layer_opacity(layer)]

        # The node that multiplies the opacity value
        x_opacity_node = nodes[names.layer_alpha_x_opacity(layer)
                               if layer.uses_shared_image
                               else names.layer_alpha_x_opacity(layer)]

        group_node = nodes.new("ShaderNodeGroup")
        group_node.node_tree = layer.node_mask
        group_node.name = names.layer_node_mask(layer)
        group_node.label = "Node Mask"
        group_node.hide = True
        group_node.location = opacity_node.location + Vector((100, 50))
        group_node.parent = opacity_node.parent

        opacity_x_node_mask = nodes.new("ShaderNodeMath")
        opacity_x_node_mask.operation = 'MULTIPLY'
        opacity_x_node_mask.name = names.layer_opacity_x_node_mask(layer)
        opacity_x_node_mask.label = f"{layer.name} Opacity x Node Mask"
        opacity_x_node_mask.hide = True
        opacity_x_node_mask.location = opacity_node.location + Vector((160, 0))
        opacity_x_node_mask.parent = opacity_node.parent

        links.new(opacity_x_node_mask.inputs[0], group_node.outputs[0])
        links.new(opacity_x_node_mask.inputs[1], opacity_node.outputs[0])

        links.new(x_opacity_node.inputs[0], opacity_x_node_mask.outputs[0])

    def update_blend_node(self, layer, channel) -> Optional[ShaderNode]:
        # TODO refactor this and _insert_layer_blend_nodes

        # Since child nodes are not yet supported ignore any layer that
        # is not top level in the stack (also ignore any unintialized
        # layer).
        if not layer or not layer.is_top_level:
            return None

        layer_stack = self.layer_stack
        nodes = layer_stack.node_tree.nodes
        links = layer_stack.node_tree.links

        making_info = channel.blend_mode_node_info

        node_name = self.node_names.blend_node(layer, channel)

        node = nodes.get(node_name)

        if node is None:
            return None

        node_location = node.location
        parent = node.parent

        if not channel.enabled:
            if isinstance(node, NodeReroute):
                # No changes needed
                return node

            new_node = nodes.new("NodeReroute")

        elif node.bl_idname == making_info.bl_idname:
            # No need to make a new node just update the existing one
            making_info.update_node(node, channel)
            return node
        else:
            new_node = making_info.make(layer_stack.node_tree, channel)
            new_node.hide = True

        # Prevent naming collisions
        node.name = node.name + "_old"

        new_node.name = node_name
        new_node.label = f"{channel.name} Blend"
        new_node.location = parent.location + node_location
        new_node.parent = parent

        # Copy links from the old node's first output
        for link in node.outputs[0].links:
            links.new(link.to_socket, new_node.outputs[0])

        # Get prev_layer_ch_out from the old layer
        if isinstance(node, NodeReroute):
            # The output socket of this channel on the previous layer
            prev_layer_ch_out = node.inputs[0].links[0].from_socket
        else:
            prev_layer_ch_out = node.inputs[1].links[0].from_socket

        # Delete the old node
        nodes.remove(node)
        del node

        # Connect the new node's inputs

        if isinstance(new_node, NodeReroute):
            links.new(new_node.inputs[0], prev_layer_ch_out)
            return new_node

        alpha_socket = self.get_layer_final_alpha_socket(layer, nodes)

        # The ShaderNodeGroup of layer
        ma_group_output = self._get_ma_group_output_socket(layer, channel)

        links.new(new_node.inputs[0], alpha_socket)
        links.new(new_node.inputs[1], prev_layer_ch_out)
        links.new(new_node.inputs[2], ma_group_output)

        assert new_node.name == node_name
        return new_node

    def _connect_output_baked(self):
        """Connects the sockets of the group output node when the layer
        stack is baked.
        """
        layer_stack = self.layer_stack
        nodes = self.nodes
        links = self.links
        node_names = self.node_names

        output_node = nodes[node_names.output()]

        assert layer_stack.is_baked

        for ch in layer_stack.channels:
            if not ch.is_baked or ch.name not in output_node.inputs:
                continue
            if ch.bake_image_channel == -1:
                bake_node = nodes[node_names.bake_image_rgb(ch.bake_image)]
                bake_socket = bake_node.outputs[0]
            else:
                bake_node = nodes[node_names.bake_image_rgb(ch.bake_image)]
                bake_socket = bake_node.outputs[ch.bake_image_channel]

            links.new(output_node.inputs[ch.name], bake_socket)

    def _connect_output_layer(self):
        """Connects the sockets of the group output node to the outputs
        of the top layer of the node stack
        """
        layer_stack = self.layer_stack
        layer = layer_stack.top_layer
        nodes = self.nodes
        node_names = self.node_names

        output_node = nodes[node_names.output()]

        if layer == layer_stack.base_layer:
            ma_group = nodes.get(node_names.layer_material(layer))

            for socket in output_node.inputs:
                out_socket = ma_group.outputs.get(socket.name)

                if out_socket is not None:
                    self.links.new(socket, out_socket)

            output_node.location.x = 400
        else:
            for socket in output_node.inputs:
                node = nodes.get(node_names.blend_node(layer, socket))
                if node is not None:
                    self.links.new(socket, node.outputs[0])

            layer_frame = nodes[node_names.layer_frame(layer)]
            output_node.location.x = layer_frame.location.x + 900

        if layer_stack.is_baked:
            self._connect_output_baked()

    def _on_active_image_change(self):
        layer_stack = self.layer_stack
        im = layer_stack.image_manager
        active_layer = layer_stack.active_layer

        if not active_layer.uses_shared_image:
            self.active_layer_image = active_layer.image
        else:
            self.active_layer_image = im.active_image

    def _register_msgbus(self):
        layer_stack = self.layer_stack
        image_manager = layer_stack.image_manager
        owners = self._msgbus_owners

        layer_stack_id = layer_stack.identifier

        def update_node_tree_sockets():
            layer_stack = get_layer_stack_by_id(layer_stack_id)
            self = layer_stack.node_manager

            self.update_node_tree_sockets()
            self._connect_output_layer()

        bpy.msgbus.subscribe_rna(
            key=layer_stack.channels,
            owner=owners,
            args=tuple(),
            notify=update_node_tree_sockets,
            options={'PERSISTENT'}
        )

        def on_active_image_change():
            layer_stack = get_layer_stack_by_id(layer_stack_id)
            self = layer_stack.node_manager

            self._on_active_image_change()

        bpy.msgbus.subscribe_rna(
            key=image_manager.path_resolve("active_image_change", False),
            owner=owners,
            args=tuple(),
            notify=on_active_image_change,
            options={'PERSISTENT'}
        )

        def update_uv_map():
            layer_stack = get_layer_stack_by_id(layer_stack_id)
            self = layer_stack.node_manager

            uv_map_node = self.nodes[self.node_names.uv_map()]
            uv_map_node.uv_map = layer_stack.uv_map_name

        bpy.msgbus.subscribe_rna(
            key=layer_stack.path_resolve("uv_map_name", False),
            owner=owners,
            args=tuple(),
            notify=update_uv_map
        )

        for layer in layer_stack.layers:
            if layer.is_initialized:
                self._register_msgbus_layer(layer)

    def _register_msgbus_layer(self, layer):
        layer_stack_id = self.layer_stack.identifier
        layer_id = layer.identifier

        # The msgbus owner for the subscriptions to this layer
        owner = self._msgbus_owners[layer.identifier]

        # Define a function since msgbus doesn't accept methods
        def layer_channels_changed(layer_id):
            # Avoid keeping python references to blender objects
            layer_stack = get_layer_stack_by_id(layer_stack_id)
            self = layer_stack.node_manager
            layer = layer_stack.get_layer_by_id(layer_id)

            self.rebuild_node_tree()
            self._unregister_msgbus_layer(layer_id)
            if layer is not None:
                self._register_msgbus_layer(layer)

        # Resubscribe RNA and rebuild the node tree when channels are
        # added or removed from the layer.
        bpy.msgbus.subscribe_rna(
            key=layer.channels,
            owner=owner,
            args=(layer_id,),
            notify=layer_channels_changed,
            options={'PERSISTENT'}
        )

        def update_blend_node(layer_id, ch_name):
            layer_stack = get_layer_stack_by_id(layer_stack_id)

            self = layer_stack.node_manager
            layer = layer_stack.get_layer_by_id(layer_id)
            if layer is None:
                return
            ch = layer.channels.get(ch_name)
            if ch is None:
                return

            self.update_blend_node(layer, ch)

        # Update the blend node when a layer's 'enabled' or 'blend_mode'
        # properties are changed.
        for ch in layer.channels:
            if ch.name in owner:
                continue

            ch_owner = owner[ch.name] = object()

            for key in ("enabled", "blend_mode"):
                bpy.msgbus.subscribe_rna(
                    key=ch.path_resolve(key, False),
                    owner=ch_owner,
                    args=(layer.identifier, ch.name),
                    notify=update_blend_node,
                    options={'PERSISTENT'}
                )

    def _unregister_msgbus(self):
        """Clear all RNA subscriptions for this node_manager.
        Safe to call even when this object has no subscriptions.
        """
        msgbus_owners = self._msgbus_owners

        bpy.msgbus.clear_by_owner(msgbus_owners)

        for layer_owner in msgbus_owners.values():
            bpy.msgbus.clear_by_owner(layer_owner)

            for ch_owner in layer_owner.values():
                bpy.msgbus.clear_by_owner(ch_owner)
        msgbus_owners.clear()

    def _unregister_msgbus_layer(self, layer) -> None:
        """Clear RNA subscriptions for this node_manager that relate
        to a specific layer.
        Params:
            layer: A MaterialLayer instance or its identifier
        """
        if isinstance(layer, str):
            layer_id = layer
        else:
            layer_id = layer.identifier

        msgbus_owners = self._msgbus_owners

        # The msgbus owner for the subscriptions to this layer
        owner = msgbus_owners.get(layer_id)

        if owner is not None:
            bpy.msgbus.clear_by_owner(owner)
            for ch_owner in owner.values():
                bpy.msgbus.clear_by_owner(ch_owner)

            del msgbus_owners[layer_id]

    def reregister_msgbus(self) -> None:
        self._unregister_msgbus()
        self._register_msgbus()

    def initialize_node_tree(self) -> None:
        node_tree = self.node_tree

        if node_tree is None:
            raise RuntimeError("layer_stack.node_tree cannot be None")

        node_tree.inputs.clear()
        node_tree.outputs.clear()

        for ch in self.layer_stack.channels:
            node_tree.outputs.new(name=ch.name,
                                  type=ch.socket_type_bl_idname)

        self.rebuild_node_tree(True)

    def insert_layer(self, layer) -> None:
        self.rebuild_node_tree()
        self._register_msgbus_layer(layer)

    def remove_layer(self, layer_id: str) -> None:
        self._unregister_msgbus_layer(layer_id)
        self.rebuild_node_tree()

    def update_node_tree_sockets(self) -> None:
        """Adds, removes, and sets the type of the node tree's output
        sockets so they match the layer stack's channels.
        Does not rebuild the node tree.
        """

        ensure_outputs_match_channels(self.node_tree.outputs,
                                      self.layer_stack.channels)

    def rebuild_node_tree(self, immediate=False):
        """Rebuild the layer stack's internal node tree. """
        if immediate or get_addon_preferences().debug:
            self._rebuild_node_tree()
        elif not bpy.app.timers.is_registered(self._rebuild_node_tree):
            bpy.app.timers.register(self._rebuild_node_tree)

    def _rebuild_node_tree(self):
        """Clears the layer stack's node tree and reconstructs it"""
        layer_stack = self.layer_stack
        node_tree = layer_stack.node_tree

        node_names = self.node_names

        if not layer_stack.is_initialized:
            return

        if node_tree is None:
            raise RuntimeError("layer_stack.node_tree cannot be None")

        nodes = node_tree.nodes
        links = node_tree.links

        nodes.clear()

        # If there is a channel in layer_stack that has no socket in
        # the node tree then update the node tree sockets.
        if [ch for ch in layer_stack.channels
                if ch.name not in node_tree.outputs]:

            self.update_node_tree_sockets()

        group_out = nodes.new("NodeGroupOutput")
        group_out.name = node_names.output()

        one_const = nodes.new("ShaderNodeValue")
        one_const.name = node_names.one_const()
        one_const.label = "Fill Constant"
        one_const.outputs[0].default_value = 1.0
        one_const.location = (-400, 600)

        zero_const = nodes.new("ShaderNodeValue")
        zero_const.name = node_names.zero_const()
        zero_const.label = "Zero Constant"
        zero_const.outputs[0].default_value = 0.0
        zero_const.location = (-400, 480)

        uv_map = nodes.new("ShaderNodeUVMap")
        uv_map.name = node_names.uv_map()
        uv_map.location = (-600, 200)
        uv_map.uv_map = layer_stack.uv_map_name

        active_layer_node = nodes.new("ShaderNodeTexImage")
        active_layer_node.name = node_names.active_layer_image()
        active_layer_node.label = "Active Layer"
        active_layer_node.width = 160
        active_layer_node.location = (-400, 100)

        links.new(active_layer_node.inputs[0], uv_map.outputs[0])

        active_layer_rgb = nodes.new("ShaderNodeSeparateRGB")
        active_layer_rgb.name = node_names.active_layer_image_rgb()
        active_layer_rgb.label = "Active Layer RGB"
        active_layer_rgb.location = (-200, 50)

        links.new(active_layer_rgb.inputs[0], active_layer_node.outputs[0])

        # Add nodes for the images that store the layers' alpha values
        self._add_paint_image_nodes()

        # Add nodes for the images that store baked layer channels
        self._add_bake_image_nodes()

        layers = layer_stack.top_level_layers

        if not layers:
            return

        # Enabled top-level layers not including the base layer
        enabled_layers = [x for x in layers[1:] if x.enabled]

        self._add_base_layer(layer_stack.base_layer)
        for layer in enabled_layers:
            self._insert_layer(layer)

        self._connect_output_layer()

        self.set_active_layer(layer_stack.active_layer)

    def set_active_layer(self, layer):

        # TODO use image_manager.active_image to set active_layer_image

        layers = self.layer_stack.top_level_layers
        node_names = self.node_names

        if layer.image is None:
            self.active_layer_image = None
            nodes = self.nodes
            # Set the value of all is_active nodes to 0.0
            for x in layers:
                is_active = nodes.get(node_names.layer_is_active(x))
                if is_active is not None:
                    is_active.outputs[0].default_value = 0.0

        elif layer.image_channel < 0:
            # Image is not shared so uses all rgb channels
            self.active_layer_image = layer.image
        else:
            # Uses a shared image so assume that the image manager has
            # already copied the values from the correct layer.image
            # channel to image_manager.active_image

            im = self.layer_stack.image_manager
            self.active_layer_image = im.active_image

            # Set this layer's is_active node to 1.0 and all other
            # layers' to 0.0

            nodes = self.nodes
            for x in layers:
                is_active = nodes.get(node_names.layer_is_active(x))
                if is_active is not None:
                    is_active_value = 1.0 if x == layer else 0.0

                    is_active.outputs[0].default_value = is_active_value

    @property
    def active_layer_image(self) -> Optional[bpy.types.Image]:
        """The current Image in the active_layer_image node."""
        active_layer_node = self.nodes[self.node_names.active_layer_image()]
        return active_layer_node.image

    @active_layer_image.setter
    def active_layer_image(self, image: Optional[bpy.types.Image]) -> None:
        if image is None:
            # Use blank image instead
            image = self.layer_stack.image_manager.blank_image

        active_layer_node = self.nodes[self.node_names.active_layer_image()]
        active_layer_node.image = image

    @property
    def _msgbus_owners(self) -> typing.DefaultDict[str, dict]:
        """The msgbus owner dict for this object. A DefaultDict of
        layer identifiers to dicts.
        """
        layer_stack_id = self.layer_stack.identifier
        return self._cls_msgbus_owners[layer_stack_id]

    @property
    def links(self):
        return self.layer_stack.node_tree.links

    @property
    def layer_stack(self):
        return get_layer_stack_from_prop(self)

    @property
    def node_tree(self):
        return self.layer_stack.node_tree

    @property
    def nodes(self):
        return self.layer_stack.node_tree.nodes

    @property
    def _zero_const_output_socket(self):
        """The output socket of the zero_const node."""
        return self.nodes[self.node_names.zero_const()].outputs[0]

    @property
    def _one_const_output_socket(self):
        """The output socket of the one_const node."""
        return self.nodes[self.node_names.one_const()].outputs[0]


def register():
    bpy.utils.register_class(NodeManager)


def unregister():
    bpy.utils.unregister_class(NodeManager)
