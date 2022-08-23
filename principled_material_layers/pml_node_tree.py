# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it
import warnings

import bpy
from bpy.types import NodeReroute, NodeSocket
from mathutils import Vector


class NodeNames:
    """The methods in this class return the names used for the nodes
    in the internal node tree of a ShaderNodePMLStack.
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
        """MixRGB or group node. Blends a layers channel with the
        channel from the previous layer. Will be a group node if using
        a custom blending function otherwise a MixRGB node.
        """
        return f"{layer.identifier}.blend.{channel.name}"

    @staticmethod
    def one_const():
        """Value node. Always has the value 1.0"""
        return "pml_one_const"

    @staticmethod
    def hardness_node(layer, channel):
        """A node that controls how smoothly a layer's channel
        transitions between values. May be None or any node with at
        least one input and output. Sockets other than the first
        input/output will be ignored.
        """
        return f"{layer.identifier}.hardness.{channel.name}"

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


class NodeTreeBuilder:
    """Class that builds the internal node tree of a ShaderNodePMLStack.
    Note that this only sets-up the node tree, updating and management
    should be done by a NodeManager instance.
    This is a pure python class so instances should not be stored on
    Blender objects. Instead new instances should be created whenever
    the node tree needs to be rebuilt.
    """

    def __init__(self, layer_stack):
        self.layer_stack = layer_stack
        self.node_manager = layer_stack.node_manager
        self.node_tree = layer_stack.node_tree

        self.nodes = self.node_tree.nodes
        self.links = self.node_tree.links

    def rebuild_node_tree(self):
        """Clears the layer stack's node tree and reconstructs it"""
        layer_stack = self.layer_stack
        node_tree = self.node_tree

        if not layer_stack.is_initialized:
            return

        if node_tree is None:
            raise RuntimeError("layer_stack.node_tree cannot be None")

        self.nodes.clear()

        # If there is a channel in layer_stack that has no socket in
        # the node tree then update the node tree sockets.
        if [ch for ch in layer_stack.channels
                if ch.name not in node_tree.outputs]:

            self.node_manager.update_node_tree_sockets()

        self._add_standard_nodes()

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

        self.node_manager.connect_output_layer()

        self.node_manager.set_active_layer(layer_stack.active_layer)

    def _add_base_layer(self, layer) -> None:
        """Creates the nodes for the base layer of the layer stack."""
        nodes = self.nodes

        base_ma_group = nodes.new("ShaderNodeGroup")
        base_ma_group.name = NodeNames.layer_material(layer)
        base_ma_group.label = layer.name
        base_ma_group.node_tree = layer.node_tree

        if layer.any_channel_baked:
            self._insert_layer_bake_nodes(layer)

    def _add_bake_image_nodes(self) -> None:
        """Add nodes that store the values of any baked channels (from
        either layers or the layer stack).
        """
        nodes = self.nodes
        links = self.links

        image_manager = self.layer_stack.image_manager
        uv_map = nodes.get(NodeNames.uv_map())

        for idx, image in enumerate(image_manager.bake_images_blend):
            image_node = nodes.new("ShaderNodeTexImage")
            image_node.name = NodeNames.bake_image(image)
            image_node.label = image_node.name
            image_node.image = image
            image_node.width = 120
            image_node.hide = True
            image_node.location = (-400, -240 - idx*40)

            links.new(image_node.inputs[0], uv_map.outputs[0])

            split_rgb_node = nodes.new("ShaderNodeSeparateRGB")
            split_rgb_node.name = NodeNames.bake_image_rgb(image)
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

    def _add_paint_image_nodes(self):
        """Add nodes for the images that store the layers' alpha values"""
        image_manager = self.layer_stack.image_manager
        nodes = self.nodes
        links = self.links

        uv_map = nodes.get(NodeNames.uv_map())

        for idx, image in enumerate(image_manager.layer_images_blend):
            image_node = nodes.new("ShaderNodeTexImage")
            image_node.name = NodeNames.paint_image(image)
            image_node.label = image.name
            image_node.image = image
            image_node.width = 120
            image_node.location = (idx * 500, 600)

            links.new(image_node.inputs[0], uv_map.outputs[0])

            split_rgb_node = nodes.new("ShaderNodeSeparateRGB")
            split_rgb_node.name = NodeNames.paint_image_rgb(image)
            split_rgb_node.label = f"{image.name} RGB"
            split_rgb_node.location = (idx * 500 + 200, 600)

            links.new(split_rgb_node.inputs[0], image_node.outputs[0])

    def _add_standard_nodes(self) -> None:
        """Adds Group Output, UV Map, Value nodes for constants, and
        Image + Split RGB nodes that will contain the active image.
        """
        nodes = self.nodes
        links = self.links

        group_out = nodes.new("NodeGroupOutput")
        group_out.name = NodeNames.output()

        one_const = nodes.new("ShaderNodeValue")
        one_const.name = NodeNames.one_const()
        one_const.label = "Fill Constant"
        one_const.outputs[0].default_value = 1.0
        one_const.location = (-400, 600)

        zero_const = nodes.new("ShaderNodeValue")
        zero_const.name = NodeNames.zero_const()
        zero_const.label = "Zero Constant"
        zero_const.outputs[0].default_value = 0.0
        zero_const.location = (-400, 480)

        uv_map = nodes.new("ShaderNodeUVMap")
        uv_map.name = NodeNames.uv_map()
        uv_map.location = (-600, 200)
        uv_map.uv_map = self.layer_stack.uv_map_name

        active_layer_node = nodes.new("ShaderNodeTexImage")
        active_layer_node.name = NodeNames.active_layer_image()
        active_layer_node.label = "Active Layer"
        active_layer_node.width = 160
        active_layer_node.location = (-400, 100)

        links.new(active_layer_node.inputs[0], uv_map.outputs[0])

        active_layer_rgb = nodes.new("ShaderNodeSeparateRGB")
        active_layer_rgb.name = NodeNames.active_layer_image_rgb()
        active_layer_rgb.label = "Active Layer RGB"
        active_layer_rgb.location = (-200, 50)

        links.new(active_layer_rgb.inputs[0], active_layer_node.outputs[0])

    def _get_layer_final_alpha_socket(self, layer) -> NodeSocket:
        """Returns the socket that gives the alpha value of layer
        after any masks and the opacity have been applied.
        """
        return self.node_manager.get_layer_final_alpha_socket(layer,
                                                              self.nodes)

    def _get_layer_output_socket(self, layer, channel):

        if layer == self.layer_stack.base_layer:
            node = self.nodes[NodeNames.layer_material(layer)]
            output_socket = node.outputs.get(channel.name)
            if output_socket is None:
                warnings.warn(f"Socket for {channel.name} not found in base "
                              "layer node group.")
                # Value socket which is always 0
                return self.nodes[NodeNames.zero_const()].outputs[0]
            return node.outputs[channel.name]

        node_name = NodeNames.blend_node(layer, channel)
        return self.nodes[node_name].outputs[0]

    # TODO Merge with NodeManager.get_ma_group_output_socket
    def _get_ma_group_output_socket(self, layer, channel):
        """Returns the output socket of layer's Group Node that matches
        channel.
        """
        ma_group = self.nodes[NodeNames.layer_material(layer)]

        if channel.is_baked:
            ma_group_output = self._get_bake_image_socket(layer, channel)
        else:
            ma_group_output = ma_group.outputs.get(channel.name)

        if ma_group_output is not None:
            return ma_group_output

        warnings.warn(f"Cannot find output socket '{channel.name}' for "
                      f"the node group of layer '{layer.name}' "
                      f"{'(baked)' if channel.is_baked else ''}")

        return self.nodes[NodeNames.zero_const()].outputs[0]

    def _get_bake_image_socket(self, layer, layer_ch):
        node_name = NodeNames.baked_value(layer, layer_ch)
        return self.nodes[node_name].outputs[0]

    def _get_paint_image_socket(self, layer):

        if layer.layer_type == 'MATERIAL_FILL':
            return self.nodes[NodeNames.one_const()].outputs[0]

        if layer.uses_shared_image:
            node = self.nodes[NodeNames.paint_image_rgb(layer.image)]
            return node.outputs[layer.image_channel]

        node = self.nodes[NodeNames.paint_image(layer.image)]
        return node.outputs[0]

    def _insert_layer(self, layer) -> bpy.types.NodeFrame:
        layer_stack = self.layer_stack
        nodes = self.nodes
        links = self.links

        # Index of the layer in the top level of the layer stack
        position = layer_stack.top_level_layers_ref.find(layer.identifier)

        if position == 0:
            raise NotImplementedError("Replacing base layer not implemented")

        previous_layer = layer_stack.top_level_layers_ref[position-1].resolve()

        # Frame containing all the nodes specific to this layer
        frame = nodes.new("NodeFrame")
        frame.name = NodeNames.layer_frame(layer)
        frame.label = f"{layer.name}"
        frame.use_custom_color = True
        frame.color = (0.1, 0.1, 0.6)

        # The Group node containing this layer's node tree
        ma_group = nodes.new("ShaderNodeGroup")
        ma_group.node_tree = layer.node_tree
        ma_group.name = NodeNames.layer_material(layer)
        ma_group.label = layer.name
        ma_group.parent = frame
        ma_group.hide = True
        ma_group.location = (0, -100)

        opacity = nodes.new("ShaderNodeValue")
        opacity.name = NodeNames.layer_opacity(layer)
        opacity.label = f"{layer.name} Opacity"
        opacity.parent = frame
        opacity.location = (200, 300)

        self._add_opacity_driver(opacity.outputs[0], layer)

        if layer.any_channel_baked:
            self._insert_layer_bake_nodes(layer, parent=frame)

        if layer_stack.layers_share_images:
            self._insert_layer_shared(layer, frame)
            alpha_x_opacity = nodes[NodeNames.layer_alpha_x_opacity(layer)]
        else:
            # The socket for this layer's image data
            layer_image_socket = self._get_paint_image_socket(layer)

            alpha_x_opacity = nodes.new("ShaderNodeMath")
            alpha_x_opacity.operation = 'MULTIPLY'
            alpha_x_opacity.name = NodeNames.layer_alpha_x_opacity(layer)
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

    def _insert_layer_shared(self, layer, parent):
        """Insert nodes used by layers that share their alpha image with
        other layers.
        """
        nodes = self.nodes
        links = self.links

        # The image node for the layer stack's active layer
        active_layer_image = nodes[NodeNames.active_layer_image_rgb()]

        # The socket for this layer's image data
        layer_image_socket = self._get_paint_image_socket(layer)

        # The Value node containing this layer's opacity
        opacity = nodes[NodeNames.layer_opacity(layer)]

        is_active = nodes.new("ShaderNodeValue")
        is_active.name = NodeNames.layer_is_active(layer)
        is_active.label = f"{layer.name} Is Active?"
        is_active.parent = parent
        is_active.location = (0, 300)

        is_active_mix = nodes.new("ShaderNodeMixRGB")
        is_active_mix.blend_type = 'MIX'
        is_active_mix.name = NodeNames.layer_is_active_mix(layer)
        is_active_mix.label = f"{layer.name} Is Active? Mix"
        is_active_mix.parent = parent
        is_active_mix.hide = True
        is_active_mix.location = (200, 200)

        links.new(is_active_mix.inputs[0], is_active.outputs[0])
        links.new(is_active_mix.inputs[1], layer_image_socket)
        links.new(is_active_mix.inputs[2], active_layer_image.outputs[0])

        alpha_x_opacity = nodes.new("ShaderNodeMath")
        alpha_x_opacity.operation = 'MULTIPLY'
        alpha_x_opacity.name = NodeNames.layer_alpha_x_opacity(layer)
        alpha_x_opacity.label = f"{layer.name} Active x Opacity"
        alpha_x_opacity.parent = parent
        alpha_x_opacity.hide = True
        alpha_x_opacity.location = (400, 250)

        links.new(alpha_x_opacity.inputs[0], opacity.outputs[0])
        links.new(alpha_x_opacity.inputs[1], is_active_mix.outputs[0])

    def _insert_layer_bake_nodes(self, layer, parent=None) -> None:
        """Adds a reroute node for each baked channel of 'layer', that
        connects to the channel's baked value. The parent of the new
        nodes will be set to 'parent'.
        """
        nodes = self.nodes
        links = self.links

        ma_group = nodes[NodeNames.layer_material(layer)]

        for idx, ch in enumerate(layer.channels):
            if not ch.is_baked:
                continue

            if ch.bake_image_channel >= 0:
                bake_node = nodes[NodeNames.bake_image_rgb(ch.bake_image)]
                bake_socket = bake_node.outputs[ch.bake_image_channel]
            else:
                bake_node = nodes[NodeNames.bake_image(ch.bake_image)]
                bake_socket = bake_node.outputs[0]

            baked_value_node = nodes.new("NodeReroute")
            baked_value_node.name = NodeNames.baked_value(layer, ch)
            baked_value_node.label = ch.name
            baked_value_node.location = ma_group.location
            baked_value_node.location.x += 160
            baked_value_node.location.y -= idx * 20

            baked_value_node.parent = parent

            links.new(baked_value_node.inputs[0], bake_socket)

    def _insert_layer_blend_nodes(self, layer, previous_layer, alpha_socket,
                                  parent=None) -> None:
        layer_stack = self.layer_stack
        nodes = self.nodes
        links = self.links

        ch_count = it.count()
        for ch in layer_stack.channels:
            if not ch.enabled:
                continue

            layer_ch = layer.channels.get(ch.name)
            if layer_ch is None or not layer_ch.enabled:
                ch_blend = nodes.new("NodeReroute")

            else:
                ch_blend = layer_ch.make_blend_node(self.node_tree)
                ch_blend.hide = True

            ch_blend.name = NodeNames.blend_node(layer, ch)
            ch_blend.label = f"{ch.name} Blend"
            ch_blend.parent = parent
            ch_blend.location = (640, next(ch_count) * -50 + 150)

            prev_layer_ch_out = self._get_layer_output_socket(previous_layer,
                                                              ch)

            if isinstance(ch_blend, NodeReroute):
                links.new(ch_blend.inputs[0], prev_layer_ch_out)
                continue

            ma_group_output = self._get_ma_group_output_socket(layer, layer_ch)

            links.new(ch_blend.inputs[0], alpha_socket)
            links.new(ch_blend.inputs[1], prev_layer_ch_out)
            links.new(ch_blend.inputs[2], ma_group_output)

            self._insert_layer_hardness_nodes(layer, layer_ch, parent)

    def _insert_layer_hardness_nodes(self, layer, ch, parent) -> None:
        node_make = ch.hardness_node_make_info

        if node_make is None:
            return

        final_alpha_soc = self._get_layer_final_alpha_socket(layer)
        blend_node = self.nodes[NodeNames.blend_node(layer, ch)]

        hardness_node = node_make.make(self.node_tree, ch)
        hardness_node.name = NodeNames.hardness_node(layer, ch)
        hardness_node.label = f"Hardness: {ch.name}"
        hardness_node.hide = True
        hardness_node.width = 100
        hardness_node.parent = parent
        hardness_node.location = blend_node.location + Vector((-120, 30))

        # Show only the first input/output
        for x in it.chain(hardness_node.inputs[1:], hardness_node.outputs[1:]):
            x.hide = True

        # Insert the node into the link between the blend node and
        # the layer's final alpha (i.e. layer_alpha_x_opacity)
        self.links.new(hardness_node.inputs[0], final_alpha_soc)
        self.links.new(blend_node.inputs[0], hardness_node.outputs[0])

    def _insert_layer_mask_node(self, layer) -> None:
        nodes = self.nodes
        links = self.links

        names = NodeNames

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


def rebuild_node_tree(layer_stack):
    builder = NodeTreeBuilder(layer_stack)
    builder.rebuild_node_tree()
