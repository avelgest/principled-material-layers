# SPDX-License-Identifier: GPL-2.0-or-later

from typing import Any, Union

from bpy.types import Node, NodeTree

_NOT_FOUND = object()


class TempChanges:
    """Context manager that allows attributes to be temporarily added
    or modified on an object. All changes are reverted when the context
    manager exits or the revert_all method is called. Changes may be
    kept using the keep or keep_all methods.
    """

    def __init__(self, obj: Any, allow_new: bool = False):
        """Params:
            obj: The object to make the temporary changes to.
            allow_new: When True allows new attributes to be added.
                Otherwise an AttributeError is raised when attempting
                to modifiy a non-existant attribute.
        """
        self._obj = obj
        self._old_values = {}
        self._allow_new = allow_new

    def __del__(self):
        self.revert_all()

    def __getattr__(self, name):
        return getattr(self._obj, name)

    def __setattr__(self, name, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
            return

        old_value = getattr(self._obj, name, _NOT_FOUND)

        if old_value is _NOT_FOUND and not self._allow_new:
            raise AttributeError(f"'{self._obj!r}' has no attribute '{name}'")

        setattr(self._obj, name, value)

        self._old_values.setdefault(name, old_value)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.revert_all()

    def keep(self, name: str) -> None:
        """Keep the change made to an attribute.
        Params:
            name: The attribute name.
        """
        if name not in self._old_values:
            raise KeyError(f"No change found for {name}")
        del self._old_values[name]

    def keep_all(self) -> None:
        """Keep all changes."""
        self._old_values.clear()

    def revert(self, name: str) -> None:
        """Revert an attribute to its original value.
        Params:
            name: The attribute name.
        """
        value = self._old_values.pop(name)

        if value is _NOT_FOUND:
            delattr(self._obj, name)
        else:
            setattr(name, value)

    def revert_all(self) -> None:
        """Revert all attributes to their original values."""
        obj = self._obj

        for k, v in reversed(list(self._old_values.items())):
            if v is _NOT_FOUND:
                delattr(obj, k)
            else:
                setattr(obj, k, v)
        self._old_values.clear()


class TempNodes:
    """Context manager that allows nodes to be temporarily added to a
    node tree. All added nodes are removed when the context manager
    exits or remove_all is called. Nodes may be kept using the keep or
    keep_all methods.
    """

    def __init__(self, node_tree: NodeTree):
        self.node_tree = node_tree

        self._added_nodes = []
        self._old_active = node_tree.nodes.active

    def __del__(self):
        if self._added_nodes:
            self.remove_all()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.active = self._old_active
        self.remove_all()

    def new(self, node_type: str) -> Node:
        """Add a new node that will be deleted when this context
        manager exits.
        Params:
            node_type: The node type. Same as what would normally be
                passed to Nodes.new.
        Returns:
            The new node.
        """
        new_node = self.node_tree.nodes.new(node_type)
        self._added_nodes.append(new_node)
        return new_node

    def keep_all(self) -> None:
        """Keep all nodes."""
        self._added_nodes.clear()

    def keep(self, node: Union[Node, str]) -> None:
        """Keep a specific node (the node will not be deleted when this
        context manager exits).
        Params:
            node: The node to keep or the name of the node.
        """
        if isinstance(node, str):
            node = self.node_tree.nodes[node]
        self._added_nodes.remove(node)

    def remove(self, node: Union[Node, str]) -> None:
        """Remove a specific node added using this context manager from
        the node tree.
        Params:
            node: The node to remove or the name of the node.
        """
        if isinstance(node, str):
            node = self.node_tree.nodes[node]

        self._added_nodes.remove(node)
        self.node_tree.nodes.remove(node)

    def remove_all(self) -> None:
        """Remove all nodes added using this context manager."""
        nodes = self.node_tree.nodes

        for node in self._added_nodes:
            try:
                nodes.remove(node)
            except RuntimeError:
                # Ignore if node can't be found
                pass
        self._added_nodes.clear()

    @property
    def active(self) -> Node:
        return self.node_tree.nodes.active

    @active.setter
    def active(self, node: Node) -> None:
        self.node_tree.nodes.active = node
