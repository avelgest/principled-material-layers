# SPDX-License-Identifier: GPL-2.0-or-later

import itertools as it
import warnings

import bpy
from bpy.types import NodeReroute, NodeSocket
from mathutils import Vector

from .preferences import get_addon_preferences


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
        """Value node. 1.0 if layer is the active layer otherwise 0."""
        return f"{layer.identifier}.is_active"

    @staticmethod
    def layer_is_active_mix(layer):
        """MixRGB node. Mixes the value from the layer's image with
        the value of active_layer_image using the layer's is_active
        value.
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
    def tiled_storage_image(image: bpy.types.Image):
        return f"tiled_storage.{image.name}"

    @staticmethod
    def tiled_storage_image_rgb(image: bpy.types.Image):
        return f"tiled_storage.{image.name}.rgb"

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

        top_level_layers = layer_stack.top_level_layers

        # Only enabled top level layers
        self.enabled_tl_layers = [x for x in top_level_layers if x.enabled]

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

        # Add nodes for TiledStorage instances of the image_manager
        # (Only when preferences.use_tiled_storage is True)
        self._add_tiled_storage_nodes()

        # Add Group nodes for the node trees of disabled layers
        self._add_disabled_layers_ma_nodes()

        if not self.enabled_tl_layers:
            return

        # Enabled top-level layers not including the base layer
        enabled_layers_it = iter(self.enabled_tl_layers)

        self._add_base_layer(next(enabled_layers_it))

        for layer in enabled_layers_it:
            self._insert_layer(layer)

        for bake_group in layer_stack.bake_groups:
            if bake_group.is_baked:
                self._connect_bake_group(bake_group)

        self.node_manager.connect_output_layer()

        self.node_manager.set_active_layer(layer_stack.active_layer)

    def _add_base_layer(self, layer) -> None:
        """Creates the nodes for the base layer of the layer stack."""
        self._insert_layer_ma_group_node(layer, None)

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

            split_rgb_node = self._add_split_rgb_to(image_node)
            split_rgb_node.name = NodeNames.bake_image_rgb(image)
            split_rgb_node.hide = True

    def _add_disabled_layers_ma_nodes(self) -> None:
        """Adds Group nodes with the layers' node trees for layers
        where layer.enabled == False. Done so that baking works even
        for disabled layers.
        """
        disabled_layers = [x for x in self.layer_stack.layers
                           if x and not x.enabled]

        for idx, layer in enumerate(disabled_layers):
            ma_group = self._insert_layer_ma_group_node(layer, None)
            ma_group.label = f"{ma_group.label} (disabled)"
            ma_group.hide = True
            ma_group.location = (-800, idx * -100)

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

    def _add_split_rgb_to(self, node) -> bpy.types.ShaderNodeSeparateRGB:
        """Adds a Separate RGB node next to node and connects its
        input to node's first output. Returns the added node.
        """
        split_rgb_node = self.nodes.new("ShaderNodeSeparateRGB")
        split_rgb_node.label = f"{node.label or node.name} RGB"
        split_rgb_node.location = node.location
        split_rgb_node.location.x += (node.width + 40)

        self.links.new(split_rgb_node.inputs[0], node.outputs[0])
        return split_rgb_node

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
        uv_map.location = (-800, 200)
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

    def _add_tiled_storage_nodes(self) -> None:
        """Adds nodes for when storing copies of images as UDIM tiles.
        See the TiledStorage class for details.
        """
        if not get_addon_preferences().use_tiled_storage:
            return

        im = self.layer_stack.image_manager
        nodes = self.nodes
        links = self.links

        if not im.tiles_data and not im.tiles_srgb:
            return

        uv_map_out = nodes[NodeNames.uv_map()].outputs[0]

        # The y position starts below the existing bake images
        y_pos_count = it.count(len(im.bake_images))

        for tile_store in (im.tiles_srgb, im.tiles_data):
            for num_str, img in tile_store.tiles.items():
                if img is None:
                    continue

                num = int(num_str)
                img_node = nodes.new("ShaderNodeTexImage")
                img_node.name = NodeNames.tiled_storage_image(img)
                img_node.label = img.name
                img_node.image = tile_store.udim_image
                img_node.width = 120
                img_node.hide = True
                img_node.location = (-400, -240 - next(y_pos_count)*40)

                img_node_rgb = self._add_split_rgb_to(img_node)
                img_node_rgb.name = NodeNames.tiled_storage_image_rgb(img)
                img_node_rgb.hide = True

                # Node to translate UV coords onto the correct UDIM tile
                uv_shift = nodes.new("ShaderNodeVectorMath")
                uv_shift.label = f"UDIM Tile {num} UVs"
                uv_shift.operation = 'ADD'
                uv_shift.location = img_node.location
                uv_shift.location.x -= 200
                uv_shift.width = 120
                uv_shift.hide = True

                shift_vec = uv_shift.inputs[1].default_value
                shift_vec[0] = (num - 1) % 10      # x coord of the UDIM tile
                shift_vec[1] = (num - 1001) // 10  # y coord of the UDIM tile

                links.new(uv_shift.inputs[0], uv_map_out)
                links.new(img_node.inputs[0], uv_shift.outputs[0])

    def _connect_bake_group(self, bake_group) -> None:
        if not bake_group.is_baked:
            return

        layer_stack = self.layer_stack
        nm = self.node_manager
        links = self.links
        nodes = self.nodes

        layer_above = bake_group.get_enabled_layer_above()
        layer_below = bake_group.get_enabled_layer_below()

        for ch in bake_group.channels:
            if not ch.is_baked or ch.name not in layer_stack.channels:
                continue
            bake_socket = self._get_baked_channel_socket(ch)

            if layer_above is not None:
                socket = nm.get_layer_input_socket(layer_above, ch, nodes)
                links.new(socket, bake_socket)

            if layer_below is not None:
                socket = nm.get_layer_output_socket(layer_below, ch, nodes)
                links.new(bake_socket, socket)

    def _get_layer_final_alpha_socket(self, layer) -> NodeSocket:
        """Returns the socket that gives the alpha value of layer
        after any masks and the opacity have been applied.
        """
        return self.node_manager.get_layer_final_alpha_socket(layer,
                                                              self.nodes)

    def _get_layer_output_socket(self, layer, channel):
        return self.node_manager.get_layer_output_socket(layer, channel,
                                                         self.nodes)

    def _get_ma_group_output_socket(self, layer, channel):
        """Returns the output socket of layer's Group Node that matches
        channel.
        """
        return self.node_manager.get_ma_group_output_socket(layer, channel,
                                                            nodes=self.nodes)

    def _get_baked_channel_socket(self, ch) -> NodeSocket:
        nodes = self.nodes

        # Check if the image is not shared with other channels
        # (bake_image_channel == -1 if the channel uses the whole image)
        if ch.bake_image_channel < 0:
            # Check for an image tile first
            bake_node = nodes.get(NodeNames.tiled_storage_image(ch.bake_image))
            if bake_node is None:
                bake_node = nodes[NodeNames.bake_image(ch.bake_image)]
            return bake_node.outputs[0]

        # Shared bake image. channel's data is in a single RGB channel.
        bake_node = nodes.get(NodeNames.tiled_storage_image_rgb(ch.bake_image))
        if bake_node is None:
            bake_node = nodes[NodeNames.bake_image_rgb(ch.bake_image)]
        return bake_node.outputs[ch.bake_image_channel]

    def _get_paint_image_socket(self, layer):

        if layer.layer_type == 'MATERIAL_FILL':
            return self._one_const_socket

        nodes = self.nodes

        # For layers that use all RGB channels of their image
        if not layer.has_shared_image:
            # Check tiled storage first
            node = nodes.get(NodeNames.tiled_storage_image(layer.image))
            if node is None:
                node = nodes[NodeNames.paint_image(layer.image)]

            # node should be an Image Texture node
            return node.outputs[0]

        # For layers using a shared image
        # (Check tiled storage first)
        node = nodes.get(NodeNames.tiled_storage_image_rgb(layer.image))
        if node is None:
            node = self.nodes[NodeNames.paint_image_rgb(layer.image)]

        # node should be a SeparateRGB node
        return node.outputs[layer.image_channel]

    def _insert_layer(self, layer) -> bpy.types.NodeFrame:
        nodes = self.nodes
        links = self.links

        # Index of the layer in the top level of the layer stack
        position = self.enabled_tl_layers.index(layer)

        if position == 0:
            raise NotImplementedError("Replacing base layer not implemented")

        previous_layer = self.enabled_tl_layers[position-1]

        # Frame containing all the nodes specific to this layer
        frame = nodes.new("NodeFrame")
        frame.name = NodeNames.layer_frame(layer)
        frame.label = f"{layer.name}"
        frame.use_custom_color = True
        frame.color = (0.1, 0.1, 0.6)

        # The Group node containing this layer's node tree
        ma_group = self._insert_layer_ma_group_node(layer, frame)
        ma_group.location = (0, -100)
        ma_group.hide = True

        opacity = nodes.new("ShaderNodeValue")
        opacity.name = NodeNames.layer_opacity(layer)
        opacity.label = f"{layer.name} Opacity"
        opacity.parent = frame
        opacity.location = (200, 300)

        self._add_opacity_driver(opacity.outputs[0], layer)

        if layer.any_channel_baked:
            self._insert_layer_bake_nodes(layer, parent=frame)

        # N.B. Now _insert_layer_shared is used for layers that don't
        # use shared images as well
        self._insert_layer_shared(layer, frame)
        alpha_x_opacity = nodes[NodeNames.layer_alpha_x_opacity(layer)]

        if layer.layer_type == 'MATERIAL_FILL':
            # Ignore active_* nodes when using fill layers since
            # they can't be painted on.
            links.new(alpha_x_opacity.inputs[1], self._one_const_socket)

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

            bake_socket = self._get_baked_channel_socket(ch)

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
        x_opacity_node = nodes[names.layer_alpha_x_opacity(layer)]

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

    def _insert_layer_ma_group_node(self, layer, parent):
        """Adds the Group node containing layer's node tree."""
        ma_group = self.nodes.new("ShaderNodeGroup")
        ma_group.node_tree = layer.node_tree
        ma_group.name = NodeNames.layer_material(layer)
        ma_group.label = layer.name
        ma_group.parent = parent

        return ma_group

    @property
    def _one_const_socket(self):
        return self.nodes[NodeNames.one_const()].outputs[0]

    @property
    def _zero_const_socket(self):
        return self.nodes[NodeNames.zero_const()].outputs[0]


def rebuild_node_tree(layer_stack):
    builder = NodeTreeBuilder(layer_stack)
    builder.rebuild_node_tree()
