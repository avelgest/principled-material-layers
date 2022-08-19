# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from collections import defaultdict
from warnings import warn

import bpy

from bpy.props import StringProperty

from bpy.types import ShaderNode, ShaderNodeCustomGroup

from .on_load_manager import pml_trusted_callback
from .utils.layer_stack_utils import (get_layer_stack_from_ma,
                                      get_layer_stack_by_id)
from .utils.naming import unique_name_in


def _get_node(layer_stack_id: str, node_id: str) -> ShaderNodePMLStack:
    """Gets a node with the given identifier from the node tree of
    a layer stack's material.
    """
    layer_stack = get_layer_stack_by_id(layer_stack_id)

    if not layer_stack or not layer_stack.is_initialized:
        return None

    ma = layer_stack.material
    if not ma.node_tree:
        return None

    # TODO cache node name to optimize
    for node in layer_stack.material.node_tree.nodes:
        if getattr(node, "identifier", None) == node_id:
            return node
    return None


class ShaderNodePMLStack(ShaderNodeCustomGroup):
    bl_idname = 'ShaderNodePMLStack'
    bl_label = "Material Layers"

    identifier: StringProperty(
        name="Identifier"
    )

    _msgbus_owners_cls = defaultdict(object)

    @staticmethod
    def _reregister_msgbus(layer_stack_id: str, node_id: str) -> None:
        node = _get_node(layer_stack_id, node_id)
        if node is None:
            return

        node._unregister_msgbus()
        node._register_msgbus()

    def init(self, context) -> None:
        if context is None:
            context = bpy.context

        ma = context.active_object.active_material
        self["material"] = ma

        self.identifier = unique_name_in(
            set(getattr(x, "identifier", None) for x in self.id_data.nodes),
            num_bytes=4)

        layer_stack = get_layer_stack_from_ma(ma)
        if not layer_stack.is_initialized:
            warn("ShaderNodePMLStack created without an initialized "
                 " layer stack")
            return

        self.node_tree = layer_stack.node_tree

        for output in self.outputs:
            pml_channel = layer_stack.channels[output.name]
            output.hide = not pml_channel.enabled

        self._register_msgbus()

        cb_id = layer_stack.add_on_load_callback(self._register_msgbus)
        self["on_load_id"] = cb_id
        assert cb_id

        cb_id = layer_stack.add_msgbus_resub_callback(
            self._reregister_msgbus,
            (layer_stack.identifier, self.identifier)
        )
        self["on_resub_id"] = cb_id
        assert cb_id

    def free(self) -> None:
        self._unregister_msgbus()

        layer_stack = self.layer_stack
        layer_stack.remove_on_load_callback(self.get("on_load_id", ""))
        layer_stack.remove_msgbus_resub_callback(self.get("on_resub_id", ""))

    def draw_buttons(self, context, layout):
        if not self._is_valid:
            layout.label(icon='ERROR', text="Node is invalid")

    @pml_trusted_callback
    def _register_msgbus(self) -> None:
        layer_stack = self.layer_stack
        owner = self._msgbus_owner

        layer_stack_id = layer_stack.identifier
        node_id = self.identifier

        bpy.msgbus.subscribe_rna(
            key=layer_stack.channels,
            owner=owner,
            args=(layer_stack_id, node_id),
            notify=self._reregister_msgbus,
            options={'PERSISTENT'}
        )

        def refresh_output_hidden(name):
            self = _get_node(layer_stack_id, node_id)
            if self is not None:
                self._refresh_output_hidden(name)

        for output in self.outputs:
            pml_channel = layer_stack.channels.get(output.name)

            if pml_channel is not None:
                bpy.msgbus.subscribe_rna(
                    key=pml_channel.path_resolve("enabled", False),
                    owner=owner,
                    args=(output.name,),
                    notify=refresh_output_hidden,
                    options={'PERSISTENT'}
                )

    def _unregister_msgbus(self):
        bpy.msgbus.clear_by_owner(self._msgbus_owner)

    def reregister_msgbus(self) -> None:
        self._unregister_msgbus()
        self._register_msgbus()

    def connect_outputs(self,
                        node: ShaderNode,
                        replace: bool = False) -> None:
        """Links this node's outputs with the corresponding inputs of
           another node (uses socket names).

           Params:
                node: The node to create links to.
                replace: If True replace existing links, otherwise only
                    create links to unlinked sockets.
        """
        links = self.id_data.links

        for output in self.outputs:
            if output.hide:
                continue

            to_input = node.inputs.get(output.name)
            if to_input is not None:
                if replace or not to_input.is_linked:
                    links.new(to_input, output)

    def _refresh_output_hidden(self, name: str) -> None:
        node_output = self.outputs.get(name)
        stack_ch = self.layer_stack.channels.get(name)

        if node_output is not None and stack_ch is not None:
            # If the output should be hidden then delete all its links
            if not stack_ch.enabled and node_output.is_linked:
                for link in node_output.links:
                    self.id_data.links.remove(link)

            # TODO use dict to store and recover removed links

            node_output.hide = not stack_ch.enabled

    @property
    def layer_stack(self):
        return get_layer_stack_from_ma(self["material"])

    @property
    def _is_valid(self) -> bool:
        ma = self.get("material")
        return ma is not None and get_layer_stack_from_ma(ma)

    @property
    def _msgbus_owner(self):
        return self._msgbus_owners_cls[self.identifier]


# Reregistering ShaderNodePMLStack can cause crashes if there is a panel
# from the add-on visible. So refuse to unregister the class whilst
# there are initialized pml_layer_stacks
if "_registered_info" not in globals():
    _registered_info = {"is_registered": False,
                        "PML_Node_Class": None}


def register():
    if not _registered_info["is_registered"]:
        bpy.utils.register_class(ShaderNodePMLStack)
        _registered_info["is_registered"] = True
        _registered_info["PML_Node_Class"] = ShaderNodePMLStack


def unregister():
    if not any(ma.pml_layer_stack for ma in bpy.data.materials):
        PML_Node_Class = _registered_info["PML_Node_Class"]
        if PML_Node_Class is not None:
            bpy.utils.unregister_class(PML_Node_Class)
        _registered_info["is_registered"] = False
