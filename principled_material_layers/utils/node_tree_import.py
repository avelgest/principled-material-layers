# SPDX-License-Identifier: GPL-2.0-or-later

import json
import os
import sys
import warnings

from typing import Any, Iterable, Optional, Union

import bpy
import mathutils


# Sentinel object for attributes that can't be found
_NOT_FOUND = object()

# Props to ignore when saving bpy_struct intances
_STRUCT_IGNORE_PROPS = {"bl_rna", "id_data", "rna_type"}

# Props to ignore when saving nodes
_NODE_IGNORE_PROPS = {"dimensions", "internal_links", "inputs", "outputs",
                      "interface", "select", "type", "width_hidden"
                      } | _STRUCT_IGNORE_PROPS


# References to nodes and bpy.types.ID instances are stored as strings
# prefixed with these values.
_NODE_PREFIX = "#NODE_IO_NODE"
_ID_PREFIX = "#NODE_IO_ID"

# Ignore node properties with these default values
_NODE_DEFAULTS = {"parent": None, "show_options": True}


def _prop_to_py(value: Any) -> Any:
    if isinstance(value, (bpy.types.bpy_prop_collection,
                          bpy.types.bpy_prop_array,
                          mathutils.Color,
                          mathutils.Vector)):
        return [_prop_to_py(x) for x in value]

    if isinstance(value, bpy.types.bpy_struct):
        if isinstance(value, bpy.types.Node):
            return f"{_NODE_PREFIX}:{value.name}"
        if isinstance(value, bpy.types.ID):
            return _id_data_to_str(value)

        out = {}
        for prop in value.bl_rna.properties:
            if (not prop.identifier.startswith("bl_")
                    and prop.identifier not in _STRUCT_IGNORE_PROPS):
                prop_value = getattr(value, prop.identifier, _NOT_FOUND)
                if prop_value is not _NOT_FOUND:
                    out[prop.identifier] = _prop_to_py(prop_value)
        return out
    return value


def _set_prop_from(struct: bpy.types.bpy_struct,
                   prop_name: Union[str, int],
                   py_value: Any) -> None:
    if isinstance(prop_name, int):
        prop = struct[prop_name]
    else:
        prop = getattr(struct, prop_name)

    if isinstance(py_value, dict):
        # Assume prop is a bpy_struct
        for k, v in py_value.items():
            _set_prop_from(prop, k, v)

    elif isinstance(py_value, (list, tuple)):
        # Assume prop is a bpy_prop_collection etc
        out_array = prop
        if len(out_array) != len(py_value):
            _new_array_values(out_array, len(py_value) - len(out_array))
        for i, v in enumerate(py_value):
            _set_prop_from(out_array, i, v)

    else:
        if isinstance(py_value, str):
            if py_value.startswith(_NODE_PREFIX):
                node_name = py_value[len(_NODE_PREFIX + ":"):]
                py_value = struct.id_data.nodes.get(node_name)
            elif py_value.startswith(_ID_PREFIX):
                py_value = _id_data_from_str(py_value)

        if isinstance(prop_name, int):
            struct[prop_name] = py_value
        else:
            setattr(struct, prop_name, py_value)


def _is_prop_default(prop: bpy.types.Property, value: Any) -> bool:
    """Returns True if value equals the default value of prop
    (if prop does not have a default value returns False).
    """
    if not hasattr(prop, "default"):
        return False

    is_array = getattr(prop, "is_array", False)
    if is_array:
        try:
            return (len(value) == len(prop.default_array)
                    and all(a == b for a, b in zip(value, prop.default_array)))
        except TypeError:
            return False

    return value == prop.default


def _id_data_to_str(id_data: bpy.types.ID) -> str:
    """Store a reference to a bpy.types.ID as a string."""
    return f"{_ID_PREFIX}:{id_data.bl_rna.identifier}:{id_data.name}"


def _id_data_from_str(id_str: str) -> Optional[bpy.types.ID]:
    """Attempt to find a bpy.types.ID from a string created by
    _id_data_to_str. Returns None if the ID can't be found.
    """
    prefix, rna_identifier, name = id_str.split(":", 2)
    if prefix != _ID_PREFIX:
        raise ValueError(f"id_str does not start with {_ID_PREFIX}")

    rna_identifier = rna_identifier.lower()
    if "nodetree" in rna_identifier:
        collection = bpy.data.node_groups
    else:
        collection = getattr(bpy.data, rna_identifier + "s", None)

    if collection is None:
        raise NotImplementedError("Could not find data collection for type "
                                  f"{rna_identifier}")

    return collection.get(name)


def _new_array_values(array, number: int) -> None:
    """Create number new values in a bpy_prop_collection or
    bpy_prop_array.
    """
    if number == 0:
        return
    new_fn = array.bl_rna.functions.get("new")
    if new_fn is None:
        raise TypeError("array has no 'new' method")

    param_defaults = [x.default_array if x.is_array else x.default
                      for x in new_fn.parameters if x.is_required]
    for _ in range(number):
        array.new(*param_defaults)


def _sockets_to_dict(sockets: Iterable[bpy.types.NodeSocket]
                     ) -> dict[str, Any]:
    sockets_dict = {}
    for socket in sockets:
        # Skip disabled sockets
        if not socket.enabled:
            continue

        soc_dict = {}

        # Only save the 'hide' prop if it is True
        if getattr(socket, "hide", False):
            soc_dict["hide"] = socket.hide

        if (hasattr(socket, "default_value")
                and not socket.is_output
                and not socket.is_linked):
            soc_dict["default_value"] = _prop_to_py(socket.default_value)

        if soc_dict:
            sockets_dict[_socket_to_identifier(socket)] = soc_dict

    return sockets_dict


def _socket_values_from_dict(sockets, sockets_dict) -> None:
    for socket in sockets:
        soc_dict = sockets_dict.get(_socket_to_identifier(socket))
        if soc_dict is not None:
            for prop_name, value in soc_dict.items():
                setattr(socket, prop_name, value)


def _socket_interface_to_dict(socket: bpy.types.NodeSocketInterface
                              ) -> dict[str, Any]:
    props = ("name", "default_value", "description", "hide_value",
             "max_value", "min_value", "bl_socket_idname")

    out_dict = {}

    for prop_name in props:
        value = getattr(socket, prop_name, _NOT_FOUND)
        if value is _NOT_FOUND:
            continue

        out_dict[prop_name] = _prop_to_py(value)
    return out_dict


def _socket_interface_from_dict(node_tree: bpy.types.NodeTree,
                                socket_dict: dict[str, Any],
                                is_output: bool
                                ) -> Optional[bpy.types.NodeSocketInterface]:
    try:
        name = socket_dict.pop("name")
        bl_socket_idname = socket_dict.pop("bl_socket_idname")
    except KeyError as e:
        raise ValueError(f"Invalid socket_dict: {e}") from e

    socket_col = node_tree.outputs if is_output else node_tree.inputs
    socket = socket_col.new(type=bl_socket_idname, name=name)

    for prop_name, value in socket_dict.items():
        try:
            setattr(socket, prop_name, value)
        except AttributeError:
            warnings.warn(f"Could not set {prop_name} on socket "
                          f"{socket.identifier}")
    return socket


def _node_to_dict(node: bpy.types.Node) -> dict[str, None]:
    props = {}
    for prop in node.bl_rna.properties:
        if (prop.identifier.startswith("bl_")
                or prop.identifier in _NODE_IGNORE_PROPS):
            continue
        # Skip the color prop if it's unused
        if prop.identifier == "color" and not node.use_custom_color:
            continue

        value = getattr(node, prop.identifier)
        if _is_prop_default(prop, value):
            continue

        # Skip properties with specific default values
        if value == _NODE_DEFAULTS.get(prop.identifier, _NOT_FOUND):
            continue

        props[prop.identifier] = _prop_to_py(value)

    inputs = _sockets_to_dict(node.inputs)
    outputs = _sockets_to_dict(node.outputs)

    out_dict = {"bl_idname": node.bl_idname,
                "inputs": inputs, "outputs": outputs,
                "props": props}
    # Filter empty items
    return {k: v for k, v in out_dict.items() if v}


def _set_node_values_from_py(node: bpy.types.Node,
                             node_dict: dict[str, Any]) -> None:
    for prop_name, value in node_dict["props"].items():
        try:
            _set_prop_from(node, prop_name, value)
        except AttributeError as e:
            warnings.warn(f"Unable to set prop {prop_name} = {value} "
                          f"on node of type {type(node)}. {e}")
            continue

        prop = getattr(node, prop_name)
        if hasattr(prop, "update") and callable(prop.update):
            # Needed for e.g. Float Curve node
            prop.update()

    if "inputs" in node_dict:
        _socket_values_from_dict(node.inputs, node_dict["inputs"])
    if "outputs" in node_dict:
        _socket_values_from_dict(node.outputs, node_dict["outputs"])


def _nodes_from_py(node_tree: bpy.types.NodeTree,
                   nodes_list: list[dict[str, Any]]) -> bpy.types.Node:

    # Create and name all nodes before setting properties so that the
    # 'parent' property can be set.
    for node_dict in nodes_list:
        node = node_tree.nodes.new(node_dict["bl_idname"])
        node.name = node_dict["props"]["name"]

    for node_dict in nodes_list:
        node = node_tree.nodes[node_dict["props"]["name"]]
        _set_node_values_from_py(node, node_dict)


def _socket_to_identifier(socket):
    node = socket.node
    socket_col = node.outputs if socket.is_output else node.inputs

    if getattr(node, "type", "") in ('GROUP_INPUT', 'GROUP_OUTPUT', ""):
        return [x for x in socket_col if x.enabled].index(socket)
    return socket.identifier


def _socket_from_identifier(socket_col,
                            identifier: Union[str, int]
                            ) -> bpy.types.NodeSocket:
    """Deserialize a socket given by identifier (either the socket's
    identifier property or its index in the enabled sockets of
    socket_col). socket_col is a collection of NodeSockets that the
    socket can be found in.
    Raises an IndexError error if the socket can't be found.
    """
    if isinstance(identifier, int):
        return [x for x in socket_col if x.enabled][identifier]
    return [x for x in socket_col if x.identifier == identifier][0]


def _link_to_dict(link: bpy.types.NodeLink) -> dict[str, str]:
    return {"from_node": link.from_node.name,
            "from_socket": _socket_to_identifier(link.from_socket),
            "to_node": link.to_node.name,
            "to_socket": _socket_to_identifier(link.to_socket)}


def _link_from_dict(node_tree: bpy.types.NodeTree,
                    link_dict: dict) -> Optional[bpy.types.NodeLink]:
    nodes = node_tree.nodes
    try:
        from_node = nodes[link_dict["from_node"]]
        from_socket = _socket_from_identifier(from_node.outputs,
                                              link_dict["from_socket"])

        to_node = nodes[link_dict["to_node"]]
        to_socket = _socket_from_identifier(to_node.inputs,
                                            link_dict["to_socket"])
    except (IndexError, KeyError) as e:
        warnings.warn(f"Could not create link from {link_dict}. Error: {e}")
        return None

    return node_tree.links.new(to_socket, from_socket)


# TODO Add option for also saving/loading the sub-trees of group nodes


def tree_to_dict(node_tree: bpy.types.NodeTree) -> dict[str, Any]:
    """Serialize a node tree to a dict of JSON compatible objects."""

    nodes = [_node_to_dict(x) for x in node_tree.nodes]

    links = [_link_to_dict(x) for x in node_tree.links]

    group_inputs = [_socket_interface_to_dict(x) for x in node_tree.inputs]

    group_outputs = [_socket_interface_to_dict(x) for x in node_tree.outputs]

    return {"name": node_tree.name,
            "bl_idname": node_tree.bl_idname,
            "nodes": nodes, "links": links,
            "inputs": group_inputs, "outputs": group_outputs}


def tree_from_dict(tree_dict: dict[str, Any],
                   out: Optional[bpy.types.NodeTree] = None
                   ) -> bpy.types.NodeTree:
    """Deserialize and return a node tree from a dict created by the
    tree_to_dict function. If out is not None then its contents will be
    replaced with the deserailized node tree otherwise a new node tree
    will be created.
    """
    if out is None:
        node_tree = bpy.data.node_groups.new(name=tree_dict["name"],
                                             type=tree_dict["bl_idname"])
    elif out.bl_idname != tree_dict["bl_idname"]:
        raise TypeError(f"out has type {out.bl_idname}, expected type "
                        f"{tree_dict['bl_idname']}")
    else:
        # Clear the output node tree
        node_tree = out
        node_tree.nodes.clear()
        node_tree.inputs.clear()
        node_tree.outputs.clear()

    # Add the inputs and outputs to node_tree
    for socket_dict in tree_dict["inputs"]:
        _socket_interface_from_dict(node_tree, socket_dict, False)

    for socket_dict in tree_dict["outputs"]:
        _socket_interface_from_dict(node_tree, socket_dict, True)

    _nodes_from_py(node_tree, tree_dict["nodes"])

    for link_dict in tree_dict["links"]:
        _link_from_dict(node_tree, link_dict)

    return node_tree


def save_tree_json(node_tree: bpy.types.NodeTree, filename: str,
                   compact: bool = False) -> None:
    """Saves 'node_tree' as a JSON file with path 'filename'. If compact
    is True then the JSON will be unindented and on a single line."""
    tree_dict = tree_to_dict(node_tree)

    with open(filename, "w", encoding="utf-8") as json_file:
        json.dump(tree_dict, json_file,
                  indent=None if compact else 4)


def load_tree_json(filename: str) -> bpy.types.NodeTree:
    """Loads and returns a node tree from JSON file 'filename'.
    The JSON file should have been created using save_tree_json.
    """
    with open(filename, "r", encoding="utf-8") as json_file:
        tree_dict = json.load(json_file)

    return tree_from_dict(tree_dict)


def get_addon_node_group_dir() -> str:
    """Returns the path of the directory that this add-on should load
    node groups from.
    """
    # Return the node_groups folder in the add-on's base directory

    # Name of the add-on's base package ("principled_material_layers")
    addon_package = __package__.split(".", 1)[0]

    # Path of the add-on's base directory
    base_dir = os.path.dirname(sys.modules[addon_package].__file__)

    return os.path.join(base_dir, "node_groups")


def load_addon_node_group(name: str, hide: bool = True) -> bpy.types.NodeTree:
    """Loads a node group named 'name' from this addons node_groups
    directory. If a node_group with this name already exists then it is
    returned instead. If hide is True then the node group will have '.'
    prepended to its name.
    """
    existing = (bpy.data.node_groups.get(name)
                or bpy.data.node_groups.get(f".{name}"))
    if existing is not None:
        return existing

    node_group_dir = get_addon_node_group_dir()
    filename = os.path.join(node_group_dir, f"{name}.json")

    node_tree = load_tree_json(filename)
    node_tree.name = name

    if hide and not node_tree.name.startswith("."):
        node_tree.name = f".{name}"

    return node_tree
