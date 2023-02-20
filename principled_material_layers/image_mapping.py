# SPDX-License-Identifier: GPL-2.0-or-later

import typing
import warnings

from typing import Optional

from bpy.types import ShaderNodeTree, ShaderNode

from . import utils

# The projection used by any image nodes in the material
IMG_PROJ_MODES = (('ORIGINAL', "Original", ""),
                  ('FLAT', "Flat", ""),
                  ('BOX', "Box", ""))

COORD_MAP_NODE_NAME = "pml_proj_coords_map"
COORD_NODE_NAME = "pml_proj_tex_coords"


def set_layer_projection(layer, proj_mode: str) -> None:
    """Changes the projection of any Image Texture nodes
    in layer's material
    """
    if proj_mode not in next(zip(*IMG_PROJ_MODES)):
        raise ValueError(f"Unsupported projection mode '{proj_mode}'")

    node_tree = layer.node_tree

    if layer.img_proj_mode == 'ORIGINAL':
        if proj_mode == 'ORIGINAL':
            return
        # Store links etc of image nodes so they can be restored later
        _store_orig_values(node_tree)

    if proj_mode == 'ORIGINAL':
        _set_layer_projection_orig(layer)
    elif proj_mode == 'BOX':
        _set_layer_projection_box(layer)
    elif proj_mode == 'FLAT':
        _set_layer_projection_flat(layer)
    else:
        raise NotImplementedError(f"proj_mode {proj_mode} not yet implemented")

    layer.img_proj_mode = proj_mode


def _init_mapping_node(node_tree: ShaderNodeTree,
                       pos_nodes: Optional[typing.List[ShaderNode]] = None,
                       coords: str = "Object") -> ShaderNode:
    """Creates a Mapping and Texture Coordinate node in node_tree for
    the Image Texture nodes' input coordinates. The nodes can be
    removed by calling _remove_mapping_node. Does nothing if the nodes
    already exist. The node will be positioned to the left of pos_nodes.
    """

    mapping_node = node_tree.nodes.get(COORD_MAP_NODE_NAME)
    if mapping_node is None:
        mapping_node = node_tree.nodes.new("ShaderNodeMapping")
        mapping_node.name = COORD_MAP_NODE_NAME

        # Position the mapping_node
        if pos_nodes is None:
            pos_nodes = _get_img_nodes(node_tree)
        pos_nodes_bb = utils.nodes.nodes_bounding_box(pos_nodes)

        mapping_node.location = (pos_nodes_bb.left - 300,
                                 pos_nodes_bb.center[1])

    coord_node = node_tree.nodes.get(COORD_NODE_NAME)
    if coord_node is None:
        coord_node = node_tree.nodes.new("ShaderNodeTexCoord")
        coord_node.name = COORD_NODE_NAME

        coord_node.location = mapping_node.location
        coord_node.location.x -= 300

    if coords not in coord_node.outputs:
        warnings.warn(f"\"{coords}\n not found in coord_node outputs")
        coords = "Object"

    node_tree.links.new(mapping_node.inputs[0], coord_node.outputs[coords])

    return mapping_node


def _remove_mapping_node(node_tree):
    """Removes the Mapping and Texture Coordinate nodes created by
    _init_mapping_node (if present).
    """
    coord_node = node_tree.nodes.get(COORD_NODE_NAME)
    if coord_node is not None:
        node_tree.nodes.remove(coord_node)

    mapping_node = node_tree.nodes.get(COORD_MAP_NODE_NAME)
    if mapping_node is not None:
        node_tree.nodes.remove(mapping_node)


def _set_layer_projection_orig(layer) -> None:
    node_tree = layer.node_tree
    _remove_mapping_node(node_tree)
    _restore_orig_values(node_tree)


# FIXME Simply setting the projection to BOX is incorrect for tangent
# space normal maps.
def _set_layer_projection_box(layer) -> None:
    node_tree = layer.node_tree

    img_nodes = _get_img_nodes(node_tree)
    if not img_nodes:
        return

    mapping_node = _init_mapping_node(node_tree, img_nodes, "Object")

    # Set all Image Texture nodes to BOX, link them to the mapping node
    # and drive their blend value
    for node in img_nodes:
        node.projection = 'BOX'
        node_tree.links.new(node.inputs[0], mapping_node.outputs[0])
        _add_blend_driver(node, layer)

    layer.proj_mode = 'BOX'


def _set_layer_projection_flat(layer) -> None:
    node_tree = layer.node_tree
    img_nodes = _get_img_nodes(node_tree)

    mapping_node = _init_mapping_node(node_tree, img_nodes, "UV")

    for node in img_nodes:
        node.projection = 'FLAT'
        node_tree.links.new(node.inputs[0], mapping_node.outputs[0])
    layer.proj_mode = 'FLAT'


def _get_img_nodes(node_tree: ShaderNodeTree) -> typing.List[ShaderNode]:
    return list(utils.nodes.get_nodes_by_type(node_tree, "ShaderNodeTexImage"))


def _store_orig_values(node_tree: ShaderNodeTree) -> None:
    """Store the original link and projection of the node_tree's
    image nodes so they can be restored by restore_orig.
    """
    for node in _get_img_nodes(node_tree):
        node["_pml_orig_proj"] = node.projection
        node["_pml_orig_link"] = utils.nodes.link_to_string(
            node.inputs[0].links[0] if node.inputs[0].is_linked else None)


def _restore_orig_values(node_tree: ShaderNodeTree) -> None:
    """Restores the links/values stored by store_orig_values."""
    for node in _get_img_nodes(node_tree):
        orig_proj = node.get("_pml_orig_proj")
        if orig_proj:
            node.projection = orig_proj

        orig_link_str = node.get("_pml_orig_link")
        if orig_link_str is not None:
            utils.nodes.make_link_from_string(
                node_tree, orig_link_str, to_socket=node.inputs[0])


def _add_blend_driver(node: ShaderNode, layer) -> None:
    """Add a driver that drives node's projection_blend prop with
    layer's img_proj_blend prop.
    """
    if not hasattr(node, "projection_blend"):
        return

    # TODO add generalized version to utils
    f_curve = node.driver_add("projection_blend")
    f_curve.driver.type = 'SUM'

    var = f_curve.driver.variables.new()
    var.name = "var"
    var.type = 'SINGLE_PROP'

    target = var.targets[0]
    target.id_type = 'MATERIAL'
    target.id = layer.id_data
    target.data_path = layer.path_from_id("img_proj_blend")
