# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import functools

from collections import defaultdict
from typing import (Any, Callable, Collection, DefaultDict, Dict, List,
                    Optional, Tuple)

import bpy

from bpy.props import (CollectionProperty,
                       IntProperty,
                       IntVectorProperty,
                       PointerProperty,
                       StringProperty)

from . import bl_info
from .utils.naming import unique_name, unique_name_in
from .utils.nodes import reference_inputs_from_type
from .utils.layer_stack_utils import get_layer_stack_by_id

from .channel import BasicChannel, Channel
from .image_manager import ImageManager
from .material_layer import MaterialLayer, MaterialLayerRef
from .node_manager import NodeManager
from .on_load_manager import OnLoadManager, pml_trusted_callback
from .preferences import get_addon_preferences

# Types
LayerStackID = str

Callback = Callable[[Any, ...], None]
CallbackArgs = Tuple[Any, ...]

CallbackDict = Dict[str, Tuple[Callback, CallbackArgs]]


class _UndoInvariant:
    """Class that stores variables for the layer stack that do not
    change after an undo, since ordinary python variables on blender
    objects are lost when undoing or redoing.
    N.B. Instances of this class are not saved to the .blend file.
    """

    # Contains all the instances of this class
    _instances: DefaultDict[
        LayerStackID, _UndoInvariant] = defaultdict(lambda: _UndoInvariant())

    @classmethod
    def get(cls, identifier: LayerStackID) -> _UndoInvariant:
        """Get the _UndoInvariant instance for the given identifier,
        creating one if not found.
        """
        return cls._instances[identifier]

    def __init__(self):
        # The active layer identifier before the last undo.
        # Only when the layer stack is active.
        self.pre_active_layer_id: str = ""

        # The memory address of the layer stack before the last undo
        self.pre_pointer: int = 0

        # Owner of msgbus subscriptions for the layer stack
        self.msgbus_owner = object()

        # Callbacks for the layer stack to call in reregister_msgbus
        self.rna_resub_callbacks: CallbackDict = {}

        # True if undo/redo callbacks should return immediately
        self.skip_undo_callbacks: bool = False


class LayerStack(bpy.types.PropertyGroup):
    """The main class of the addon. A stack of MaterialLayer instances
    each containing at most one of any of the channels found in the
    stack's channels property.
    Registered so that each bpy.types.Material contains a pointer
    property to an (initially uninitialized) instance.

    Each layer stack has its own ShaderNodeTree which is used by any
    ShaderNodePMLStack node in its material's node tree. The layer
    stacks node tree is managed by the stack's node_manager property.

    All images used by the stack's layers are managed by the
    image_manager property.

    A LayerStack instance has a boolean value of False if it is
    uninitialized.
    """

    identifier: StringProperty(
        name="Identifier",
        description="A unique identifier for this layer stack",
        default=""
    )
    # 'layers' contains all of the layers in this layer stack
    # (including child layers) in an arbitrary order. The order of
    # layers in the layer stack is determined by 'top_level_layers_ref'
    # and the 'children' CollectionProperty of each layer.
    # This CollectionProperty may contain uninitialized layers after
    # layers have been deleted.
    # Layers can be accessed by their names.
    layers: CollectionProperty(
        type=MaterialLayer,
        name="Layers"
    )
    # References to the top level layers (those which are not child
    # layers). The order of the items in this CollectionProperty
    # determines how they are ordered in the stack. Index 0 is the
    # base layer while -1 is the top layer.
    # MaterialLayerRef(s) can be accessed by their layer identifiers.
    top_level_layers_ref: CollectionProperty(
        type=MaterialLayerRef,
        name="Top Level Layers"
    )
    # The index (in 'layers') of the active layer.
    active_layer_index: IntProperty(
        name="Active Layer Index",
        description="The index of the layer stack's active layer",
        get=lambda self: self.get("_active_layer_index", 0),
        set=lambda self, value: self._set_active_layer_index_op(value),
    )
    # The index of the active layer for use in the node editor panel etc.
    # changing this does not change the actual active layer
    # Currently unused
    active_layer_index_ui_only: IntProperty(
        name="Active Layer Index"
    )
    # The index of the selected channel in the UI. Changing this
    # doesn't affect anything else.
    active_channel_index: IntProperty(
        name="Selected Channel",
        description="The currently selected channel in the UI"
    )
    node_tree: PointerProperty(
        type=bpy.types.ShaderNodeTree,
        name="Internal Node Tree",
        description="The internal node tree of this layer stack"
    )
    group_to_connect: PointerProperty(
        type=bpy.types.ShaderNodeTree,
        name="Connects to",
        description=("What node group is this layer stack supposed to connect"
                     "to. If None then this layer stack should connect to a "
                     "Principled BSDF node. Otherwise should connect to a "
                     "group node set to this node group.")
    )
    channels: CollectionProperty(
        type=Channel,
        name="Channels"
    )
    image_manager: PointerProperty(
        type=ImageManager,
        name="Image Manager"
    )
    node_manager: PointerProperty(
        type=NodeManager,
        name="Node Manager"
    )
    on_load_manager: PointerProperty(
        type=OnLoadManager,
        name="Load Post Manager"
    )
    uv_map_name: StringProperty(
        name="UV Map",
        description="Name of the UV map used by layers",
        get=lambda self: self.get("_uv_map_name", ""),
        set=lambda self, value: self._set_uv_map_name(value)
    )
    version: IntVectorProperty(
        name="Version",
        description=("The version of the addon in which this layer stack"
                     "was created or updated"),
        size=2
    )

    @classmethod
    def _add_handler_method(cls, method: Callable,
                            handlers: List[Callable]) -> Callable:
        """Adds a method as a handler to bpy.app callaback list.
        The method is wrapped in a function then added to the specified
        handler list. If the layer stack is removed then the function
        stays in the handler list but returns without calling 'method'.

        Params:
            method: The method to use as a handler.
            handlers: A list from bpy.app.handlers.
        Returns:
            The wrapper function that was added to handlers.
        """
        func = method.__func__
        layer_stack = method.__self__
        identifier = layer_stack.identifier

        @functools.wraps(func)
        def method_handler(*args, **kwargs):
            layer_stack = get_layer_stack_by_id(identifier)
            if layer_stack is not None:
                func(layer_stack, *args, **kwargs)

        handlers.append(method_handler)
        return method_handler

    def __bool__(self):
        return self.is_initialized

    def __eq__(self, other):
        if isinstance(other, LayerStack):
            return self.identifier == other.identifier

        return super().__eq__(other)

    def initialize(self, channels: Collection[BasicChannel],
                   *,
                   uv_map: Optional[bpy.types.MeshUVLoopLayer] = None,
                   node_group: Optional[bpy.types.ShaderNodeTree] = None,
                   image_width: int = 1024,
                   image_height: int = 1024,
                   use_float: bool = False) -> None:
        """Initializes this layer stack. This should be called before
        the layer stack is used in any way. When called a second time
        this method has no effect.
        Params:
            channels: A collection of BasicChannel instances.
            image_width: The width of images used for paint layers.
            image_height: The height of images used for paint layers.
            use_float: Whether to use 32-bit float images for paint
                layers.
            uv_map: The name of the UV map used by this layer stack.
            node_groups: If this this layer stack should connect to
                a group node then this is the node group that the node
                should have.
        """

        if self.is_initialized:
            return

        assert isinstance(self.id_data, bpy.types.Material)

        self.version = bl_info["version"][:2]

        # Unique identifier for this layer stack
        self.identifier = unique_name(
            lambda x: get_layer_stack_by_id(x) is None,
            num_bytes=4)

        # Cache of layer identifiers against indices in self.layers
        self["layer_id_cache"] = {}

        # The internal node tree used by ShaderNodePMLStack
        self.node_tree = bpy.data.node_groups.new(
                            type='ShaderNodeTree',
                            name=f".{self.material.name}.PML_Node_Tree")

        self.uv_map_name = uv_map.name if uv_map else ""
        self.group_to_connect = node_group

        self.layers.clear()
        self.top_level_layers_ref.clear()

        if channels is not None:
            for ch in channels:
                new_ch = self.channels.add()
                new_ch.init_from_channel(ch)

        self.image_manager.initialize(self, image_width, image_height,
                                      use_float=use_float)

        base_layer = self.layers.add()
        base_layer.initialize("Base Material",
                              self,
                              layer_type='MATERIAL_FILL',
                              enabled_channels_only=True,
                              channels=self.channels)

        self.top_level_layers_ref.add().set(base_layer)
        self.active_layer = base_layer

        self.node_manager.initialize(self)

        self.on_load_manager.initialize()
        self.add_on_load_callback(self._on_load)

        self._on_load()

    def delete(self) -> None:
        """Deletes the layer stack leaving it in an uninitialized
        state. All of the layer stack's layers and channels are
        deleted, along with any images or node trees used by them.
        """
        if not self.is_initialized:
            return

        self._unregister_msgbus()

        self.free_bake()
        for layer in list(self.layers):
            layer.delete()

        self.top_level_layers_ref.clear()
        self.layers.clear()
        self["layer_id_cache"].clear()

        self.node_manager.delete()
        self.image_manager.delete()

        for ch in self.channels:
            ch.delete()
        self.channels.clear()

        self.on_load_manager.clear()

        bpy.data.node_groups.remove(self.node_tree)
        self.node_tree = None

        self["_active_layer_index"] = 0
        self.active_layer_index_ui_only = 0
        self.active_channel_index = 0

        self.identifier = ""
        assert not self.is_initialized

    @pml_trusted_callback
    def _on_load(self) -> None:
        """Called when the blend file is loaded. Adds bpy.app undo/redo
        handlers, registers msgbus subscriptions, and frees all bakes.
        """
        add_handler = self._add_handler_method

        add_handler(self._undo_pre, bpy.app.handlers.undo_pre)
        add_handler(self._undo_post, bpy.app.handlers.undo_post)
        add_handler(self._redo_pre, bpy.app.handlers.redo_pre)
        add_handler(self._redo_post, bpy.app.handlers.redo_post)

        self._register_msgbus()

        self.free_bake()
        for layer in self.layers:
            layer.free_bake()

        self.node_manager.rebuild_node_tree()

    def _register_msgbus_channel(self, ch: BasicChannel, owner=None) -> None:
        if owner is None:
            owner = self._msgbus_owner

        bpy.msgbus.subscribe_rna(key=ch.path_resolve("enabled", False),
                                 owner=owner,
                                 args=(self.identifier, ch.name),
                                 notify=_on_channel_enabled,
                                 options={'PERSISTENT'})

    def _register_msgbus(self) -> None:
        msgbus_owner = self._msgbus_owner
        for ch in self.channels:
            self._register_msgbus_channel(ch, msgbus_owner)

    def _unregister_msgbus(self):
        bpy.msgbus.clear_by_owner(self._msgbus_owner)

    def _reregister_msgbus_self_only(self):
        """Reregister msgbus subscriptions only for those registered
        to self._msgbus_owner."""
        self._unregister_msgbus()
        self._register_msgbus()

    def reregister_msgbus(self):
        self._unregister_msgbus()
        self._register_msgbus()

        self.node_manager.reregister_msgbus()

        for callback, args in self._rna_resub_callbacks.values():
            callback(*args)

    def set_active_layer_index(self, value):
        """Sets self.active_layer_index without calling an operator."""

        if self.active_layer_index == value:
            if self._is_active_in_image_paint:
                self._set_paint_canvas()
            return

        if value < 0 or value >= len(self.layers):
            raise IndexError("Index out of range")

        self["_active_layer_index"] = value

        self._active_layer_changed()

    def _set_active_layer_index_op(self, value):
        """Sets self.active_layer_index using an operator (to allow for
        the change to be undone).
        This is used by the setter of self.active_layer_index."""

        bpy.ops.material.pml_set_active_layer_index(
            layer_index=value,
            layer_stack_id=self.identifier)

    def _set_uv_map_name(self, value):
        if value == self.uv_map_name:
            return
        if not isinstance(value, str):
            raise TypeError("Expected a string.")

        self["_uv_map_name"] = value

        for layer in self.layers:
            if layer and layer.is_baked:
                layer.free_bake()

    def _redo_pre(self, *dummy):
        undo_invariant = self._undo_invariant
        if undo_invariant.skip_undo_callbacks:
            return

        undo_invariant.pre_pointer = self.as_pointer()

    def _redo_post(self, *dummy):
        undo_invariant = self._undo_invariant
        if undo_invariant.skip_undo_callbacks:
            return

        if self._is_active_in_image_paint:

            if undo_invariant.pre_pointer != self.as_pointer():
                self._undo_workaround()

            # Set the image paint canvas to the layer stack's active
            # image unless editing an image not created by the addon
            paint_settings = bpy.context.scene.tool_settings.image_paint
            if (not paint_settings.canvas
                    or paint_settings.canvas.name.startswith(".pml")):
                paint_settings.canvas = self.image_manager.active_image

        if undo_invariant.pre_pointer != self.as_pointer():
            layer_stack_id = self.identifier

            def redo_post_resub_rna():
                self = get_layer_stack_by_id(layer_stack_id)
                if self is not None:
                    self.reregister_msgbus()
            bpy.app.timers.register(redo_post_resub_rna)

    def _undo_pre(self, *dummy):
        undo_invariant = self._undo_invariant
        if undo_invariant.skip_undo_callbacks:
            return

        undo_invariant.pre_pointer = self.as_pointer()

        active_layer = self.active_layer
        undo_invariant.pre_active_layer_id = (None if active_layer is None
                                              else active_layer.identifier)

    def _undo_post(self, *dummy):
        undo_invariant = self._undo_invariant
        if undo_invariant.skip_undo_callbacks:
            return

        if self._is_active_in_image_paint:
            # Set the image paint canvas to the layer stack's active
            # image unless editing an image not created by the addon
            paint_settings = bpy.context.scene.tool_settings.image_paint

            if (not paint_settings.canvas
                    or paint_settings.canvas.name.startswith(".pml")):

                paint_settings.canvas = self.image_manager.active_image

            pre_undo_layer_id = undo_invariant.pre_active_layer_id
            active_layer_id = getattr(self.active_layer, "identifier", None)

            if get_addon_preferences().use_undo_workaround:
                if undo_invariant.pre_pointer != self.as_pointer():
                    self._undo_workaround()
            else:
                if pre_undo_layer_id != active_layer_id:
                    self.image_manager.reload_tmp_active_image()

        # Check whether the layer stack has been reallocated
        if undo_invariant.pre_pointer != self.as_pointer():

            # There seems to be a bug after undoing when using msgbus
            # with layer channels so need to resubscribe all
            self.reregister_msgbus()

    def _undo_workaround_function(self) -> None:
        """Used by _undo_workaround"""

        if not bpy.ops.ed.undo.poll():
            return

        undo_invariant = self._undo_invariant
        undo_invariant.skip_undo_callbacks = True

        try:
            bpy.ops.ed.undo()
            bpy.ops.ed.redo()
        finally:
            undo_invariant.skip_undo_callbacks = False

    def _undo_workaround(self) -> None:
        """When undoing or redoing a global undo step the image paint
        canvas image may suddenly lose its image data. This is a
        workaround to be called after an undo or redo that performs
        both an undo and a redo which seems to resotore the image's
        data.
        """
        if not get_addon_preferences().use_undo_workaround:
            return

        bpy.app.timers.register(self._undo_workaround_function)

    def _active_layer_changed(self):
        if not self.is_initialized:
            return

        layer = self.active_layer

        if layer is not None:
            self.image_manager.set_active_layer(layer)
            self.node_manager.set_active_layer(layer)

        self._set_paint_canvas()

    def _set_paint_canvas(self):
        paint_settings = bpy.context.scene.tool_settings.image_paint

        paint_settings.mode = 'IMAGE'

        layer = self.active_layer

        if layer.image is None:
            paint_settings.canvas = None
        elif layer.uses_shared_image:
            paint_settings.canvas = self.image_manager.active_image
        else:
            paint_settings.canvas = layer.image

    def _search_for_layer_index_by_id(self, identifier: str) -> int:
        for idx, layer in enumerate(self.layers):
            if layer.identifier == identifier:
                return idx
        return -1

    def get_layer_by_id(self, identifier: str) -> Optional[MaterialLayer]:
        """Finds a layer by its 'identifier' property.
        Params:
            identifier: a string
        Returns:
            The layer or None if no layer has the specified identifier
        """
        if not identifier:
            # Uninitialised/deleted layers have "" as an identifier
            return None

        # Cache of layer identifiers to indices
        id_index_cache = self["layer_id_cache"]

        # First check the cache
        cached_idx = id_index_cache.get(identifier)
        if cached_idx is not None and cached_idx < len(self.layers):
            layer = self.layers[cached_idx]

            # Since the layer index may have changed check the
            # identifier to make sure that this is the correct layer
            if layer.identifier == identifier:
                return layer

        layer_idx = self._search_for_layer_index_by_id(identifier)
        if layer_idx < 0:
            return None

        id_index_cache[identifier] = layer_idx
        return self.layers[layer_idx]

    def ordered_layer_indices(self) -> List[int]:
        """Returns a list of indices of this stacks 'layers'
        CollectionProperty.
        The indices are ordered by their position in the stack as they
        would appear in a UIList e.g the first element will be the
        index of base_layer or one of its children, whilst the last
        element will always be top_layer.
        Only indices for valid (is_initialized == True) layers are
        returned.
        """

        # Dict of layers' identifiers to their indices in self.layers
        indices = {x.identifier: idx for idx, x in enumerate(self.layers)}

        ordered = []

        # Starts from the base_layer
        for layer in self.top_level_layers:
            if layer.children:
                ordered += [indices[x.identifier] for x in layer.descendents]

            ordered.append(indices[layer.identifier])

        return ordered

    def get_layer_above(self, layer: MaterialLayer) -> Optional[MaterialLayer]:
        """Returns the layer above 'layer' in the layer stack or None
        if 'layer' is the top layer.
        Only top level layers are returned.
        Params:
            layer: A top level layer contained by this layer stack.
        Returns:
            The MaterialLayer above 'layer' or None.
        """
        if not layer.is_top_level:
            raise ValueError("Expected top level layer")

        index = self.top_level_layers_ref.find(layer.identifier)
        if index < -1:
            raise ValueError("layer not found")
        if index == len(self.top_level_layers_ref) - 1:
            return None
        return self.top_level_layers_ref[index+1].resolve()

    def get_layer_below(self, layer: MaterialLayer) -> Optional[MaterialLayer]:
        """Returns the layer below 'layer' in the layer stack or None
        if 'layer' is the base layer.
        Only top level layers are returned.
        Params:
            layer: A top level layer contained by this layer stack.
        Returns:
            The MaterialLayer below 'layer' or None.
        """
        if not layer.is_top_level:
            raise ValueError("Expected top level layer")

        index = self.top_level_layers_ref.find(layer.identifier)
        if index < -1:
            raise ValueError("layer not found")
        if index == 0:
            return None
        return self.top_level_layers_ref[index-1].resolve()

    def add_channel(self, name: str, socket_type: str) -> Channel:
        """Adds a new channel to this layer stack.

        Params:
            name: The name of the new channel. Raises a ValueError if
                the layer stack already has a channel with this name.
            socket_type: The socket_type of the new channel. Must be
                one of {'FLOAT', 'FLOAT_FACTOR', 'COLOR', 'VECTOR'}.
        Returns:
            The added channel.
        """

        if not name:
            raise ValueError(f"'{name}' is not a valid name.")
        if name in self.channels:
            raise ValueError("Layer stack already has a channel with "
                             f"name '{name}'.")

        new_channel = self.channels.add()
        try:
            new_channel.initialize(name, socket_type)
        except Exception as e:
            ch_idx = self.channels.find(new_channel.name)
            assert ch_idx >= 0
            self.channels.remove(ch_idx)

            raise type(e) from e

        self._register_msgbus_channel(new_channel)

        # N.B. Node tree sockets are updated in node_manager by an RNA
        # subscription.

        bpy.msgbus.publish_rna(key=self.channels)

        # Add channel to base layer
        base_layer = self.base_layer
        if base_layer is not None:
            base_layer.add_channel(new_channel)

        self.node_manager.rebuild_node_tree()

        return new_channel

    def remove_channel(self, name: str) -> None:
        """Removes a channel from this layer stack and all of its
        layers. Raises a ValueError if the channel is not found.

        Params:
            name: The name of the channel to remove.
        """
        if len(self.channels) == 1:
            raise RuntimeError("A LayerStack must have at least one channel.")

        channel = self.channels.get(name)

        if channel is None:
            raise ValueError(f"Channel {name} not found.")

        # The index of the channel to remove.
        ch_idx = self.channels.find(channel.name)
        assert ch_idx >= 0

        # The index ofr the active channel
        active_ch_idx = self.active_channel_index

        if ch_idx >= active_ch_idx:
            # Adjust the active_channel_index so it still refers to
            # the same channel (or the channel below if removing the
            # active channel) after the removal.
            self.active_channel_index = max(active_ch_idx - 1, 0)

        for layer in self.layers:
            if layer and name in layer.channels:
                layer.remove_channel(name)

        self.channels.remove(ch_idx)

        # Reregister msgbus subscriptions
        self._reregister_msgbus_self_only()

        # N.B. Node tree sockets are updated in node_manager by an RNA
        # subscription.

        bpy.msgbus.publish_rna(key=self.channels)

        self.node_manager.rebuild_node_tree()

    def set_channel_enabled(self, name: str, enabled: bool) -> None:
        channel = self.channels.get(name)
        if channel is None:
            raise ValueError(f"Channel {name} not found.")

        channel.enabled = enabled
        base_layer = self.base_layer
        is_in_base_layer = name in base_layer.channels

        if enabled and not is_in_base_layer:
            base_layer.add_channel(channel)
        elif not enabled and is_in_base_layer:
            base_layer.remove_channel(name)

    def get_channel_default_value(self,
                                  channel: BasicChannel) -> Optional[Any]:
        if channel.name not in self.channels:
            raise ValueError(f"Layer stack has no channel '{channel.name}'")

        # If this layer stack should connect to a group node then use
        # the default_value of the node group's input socket interface.
        if self.group_to_connect is not None:
            socket = self.group_to_connect.inputs.get(channel.name)
            return getattr(socket, "default_value", None)

        default_sockets = reference_inputs_from_type(
                                bpy.types.ShaderNodeBsdfPrincipled,
                                self.node_tree)
        return next((x.default_value for x in default_sockets
                     if x.name == channel.name), None)

    def append_layer(self, name: str) -> MaterialLayer:
        """Append a new layer to the top of this layer stack at the
        top level.
        Params:
            name: The name of the new layer. The layer's actual name
                may be different (e.g. if a layer already exists with
                this name).
        Returns:
            The new layer. The top_layer property will now be the new
            layer.
        """
        return self.insert_layer(name, -1)

    def insert_layer_above(self, name: str,
                           above: MaterialLayer) -> MaterialLayer:
        """Inserts a new layer into the top level of the layer stack
        above the specified layer.
        Params:
            name: The name of the new layer. The layer's actual name
                may be different (e.g. if a layer already exists with
                this name).
            above: A MaterialLayer to insert the new layer above. A
                ValueError is raised if the layer stack does not
                contain this layer or it is not a top level layer.
        Returns:
            The new layer.
        """

        above_idx = self.top_level_layers_ref.find(above.identifier)
        if above_idx < 0:
            raise ValueError(f"{above.name} ({above.identifier}) is not a top "
                             "level layer of this layer stack")

        return self.insert_layer(name, above_idx + 1)

    def insert_layer(self, name: str, position: int) -> MaterialLayer:
        """Inserts a new layer into the top level of the layer stack.
        Params:
            name: The name of the new layer. The layer's actual name
                may be different (e.g. if a layer already exists with
                this name).
            position: The position in the top level of the layer stack
                in which to insert the new layer.
        Returns:
            The new layer.
        """
        top_lvl = self.top_level_layers_ref

        # Support negative indices
        if position < 0:
            position = len(top_lvl) - position - 1
            if position < 0:
                raise IndexError("position is out of range")

        new_layer = self.layers.add()
        new_layer.initialize(name, self, channels=self.channels)

        new_layer_ref = top_lvl.add()
        new_layer_ref.set(new_layer)

        # Only self.top_level_layers_ref determines the order of top
        # level layers
        top_lvl.move(len(top_lvl)-1, position)

        self.node_manager.insert_layer(new_layer)

        return new_layer

    def move_layer(self, layer: MaterialLayer,
                   direction: str,
                   steps: int = 1) -> None:
        """
        Moves a layer up or down the layer stack. Warning: any variables
        that refer to an item of self.top_level_layers_ref may become
        invalid.
        Params:
            layer: The layer to move.
            direction: Either 'UP' or 'DOWN' (case sensitive)
            steps: The number of places to move the layer by.
        """
        layer_idx = self.top_level_layers_ref.find(layer.identifier)

        if layer_idx < 0:
            raise ValueError(f"Layer {layer.name} is not in layer stack")
        if steps == 0:
            return

        if direction == 'DOWN':
            steps = -steps
        elif direction != 'UP':
            raise ValueError("direction must be either 'UP' or 'DOWN'")

        n_top_level = len(self.top_level_layers_ref)
        new_idx = max(min(layer_idx+steps, n_top_level-1), 0)

        # TODO allow moving base_layer
        if new_idx == 0 and layer != self.base_layer:
            raise ValueError("Cannot replace base layer")
        if layer == self.base_layer:
            raise ValueError("Cannot move base layer")

        self.top_level_layers_ref.move(layer_idx, new_idx)

        self.node_manager.rebuild_node_tree()

    def remove_layer(self, layer: MaterialLayer) -> None:
        """Deletes a layer from this layer stack. Raises a KeyError if
        the layer is not found or a ValueError if the specified layer
        is the base layer.
        Warning: any variables that refer to an item of 'layers' or
        'top_level_layers_ref' may become invalid.

        Params:
            layer: The layer to remove. Must not be the base layer.
        """
        layer_idx = self.layers.find(layer.name)
        if layer_idx < 0:
            raise KeyError(f"No layer named {layer.name} in layer stack")
        if layer == self.base_layer:
            raise ValueError("Removing base_layer is not supported.")

        layer_id = layer.identifier
        layer_ref_idx = self.top_level_layers_ref.find(layer_id)

        if self.active_layer_index == layer_idx:
            # If removing the active layer then change the active layer
            # to the layer below.
            layer_below_ref = self.top_level_layers_ref[layer_ref_idx-1]
            self.active_layer = layer_below_ref.resolve()

        active_layer_name = self.active_layer.name

        self["layer_id_cache"].pop(layer_id, None)

        if layer_ref_idx >= 0:
            self.top_level_layers_ref.remove(layer_ref_idx)

        layer.delete()
        # N.B. Removing items from a collection property may invalidate
        # variables refering to any of its items.
        self.layers.remove(layer_idx)

        # Make sure the active layer index is correct
        self["_active_layer_index"] = self.layers.find(active_layer_name)

        self.node_manager.remove_layer(layer_id)

    def clear(self) -> None:
        """Deletes all layers from this layer stack and adds a new
        base layer.
        """

        for layer in list(self.layers):
            layer.delete()

        self.top_level_layers_ref.clear()
        self.layers.clear()
        self["layer_id_cache"].clear()

        base_layer = self.layers.add()
        base_layer.initialize("Base Material",
                              self,
                              layer_type='MATERIAL_FILL',
                              enabled_channels_only=False,
                              channels=self.channels)

        self.top_level_layers_ref.add().set(base_layer)

        self.active_layer = base_layer

        self.node_manager.rebuild_node_tree()

    def add_on_load_callback(self, callback: Callable[[], None]) -> str:
        """Adds a callback to be called whenever this blend file is
        loaded.
        Params:
            callback: A callable taking no arguments.
        Returns:
            A unique identifier string for the added callback.
        """
        return self.on_load_manager.add_callback(callback)

    def remove_on_load_callback(self, callback_id: str) -> None:
        """Removes a callback added by add_on_load_callback.
        No error is raised if callaback_id is not found.
        Params:
            callback_id: An identifier string returned from
                add_on_load_callback
        """
        self.on_load_manager.remove_callback(callback_id)

    def add_msgbus_resub_callback(self, callback, args=tuple()) -> str:
        """Add a callback that should be called whenever the layer
        stack reregisters its msgbus subscriptions.
        """
        if not callable(callback):
            raise TypeError("callback must be callable")

        callbacks = self._rna_resub_callbacks
        callback_id = unique_name_in(callbacks)

        callbacks[callback_id] = (callback, tuple(args))

        return callback_id

    def remove_msgbus_resub_callback(self, callback_id: str) -> None:
        """Removes an resub callback added with add_msgbus_resub_callback.
        No error is raised if callback_id is not found.
        """
        self._rna_resub_callbacks.pop(callback_id, None)

    def free_bake(self) -> None:
        for ch in self.channels:
            ch.free_bake()

    @property
    def active_layer(self) -> Optional[MaterialLayer]:
        """The active layer or None if active_layer_index is out of
        range.
        """
        layers = self.layers

        try:
            return layers[self.active_layer_index]
        except IndexError as e:
            if not layers:
                return None
            raise e

    @active_layer.setter
    def active_layer(self, layer: MaterialLayer):
        if layer == self.active_layer:
            return

        layer_idx = self.layers.find(layer.name)
        if layer_idx < 0:
            raise KeyError("Layer stack has no layer {layer}")

        self.set_active_layer_index(layer_idx)

    @property
    def active_channel(self) -> Optional[Channel]:
        """The active channel or None if this layer stack has no
        channels.
        """
        if not self.channels:
            return None
        if self.active_channel_index >= len(self.channels):
            self.active_channel_index = len(self.channels) - 1

        return self.channels[self.active_channel_index]

    @active_channel.setter
    def active_channel(self, channel: BasicChannel):
        ch_idx = self.channels.find(channel.name)
        if ch_idx < 0:
            raise ValueError(f"LayerStack has no channel named {channel.name}")

        self.active_channel_index = ch_idx

    @property
    def base_layer(self) -> Optional[MaterialLayer]:
        """The bottom-most top level layer, or None if this layer stack
        has no layers.
        """
        if not self.top_level_layers_ref:
            return None
        return self.top_level_layers_ref[0].resolve()

    @property
    def top_layer(self) -> Optional[MaterialLayer]:
        """The top-most top level layer, or None if this layer stack
        has no layers.
        """
        if not self.top_level_layers_ref:
            return None
        return self.top_level_layers_ref[-1].resolve()

    @property
    def _is_active_in_image_paint(self) -> bool:
        """Is this LayerStack's material active in image paint"""
        obj = bpy.context.image_paint_object
        if obj is not None and obj.active_material is self.material:
            return True
        return False

    @property
    def is_baked(self) -> bool:
        return any(ch.is_baked for ch in self.channels)

    @property
    def is_initialized(self) -> bool:
        return self.node_tree is not None

    @property
    def layers_share_images(self) -> bool:
        return self.image_manager.layers_share_images

    @layers_share_images.setter
    def layers_share_images(self, value: bool):
        self.image_manager.layers_share_images = value

    @property
    def material(self) -> bpy.types.Material:
        return self.id_data

    @property
    def _msgbus_owner(self) -> object:
        return _UndoInvariant.get(self.identifier).msgbus_owner

    @property
    def _rna_resub_callbacks(self):
        return _UndoInvariant.get(self.identifier).rna_resub_callbacks

    @property
    def _undo_invariant(self) -> _UndoInvariant:
        return _UndoInvariant.get(self.identifier)

    @property
    def top_level_layers(self) -> List[MaterialLayer]:
        """A list of layers with a stack depth of 0. The list is
        ordered from lowest in the stack (base_layer) to highest.
        """
        return [x.resolve() for x in self.top_level_layers_ref]


def _on_channel_enabled(layer_stack_id: LayerStackID, ch_name: str) -> None:
    """Msgbus callback for when a layer stack's channel's enabled
    property is changed.
    """
    layer_stack = get_layer_stack_by_id(layer_stack_id)
    if layer_stack is None:
        return
    channel = layer_stack.channels.get(ch_name)
    if channel is None:
        return

    layer_stack.set_channel_enabled(ch_name, channel.enabled)

    layer_stack.node_manager.rebuild_node_tree()


def _rebuild_node_tree(layer_stack_id: LayerStackID) -> None:
    """Function to rebuild the node tree with the given layer_stack_id.
    For use in msgbus subscription etc.
    """
    layer_stack = get_layer_stack_by_id(layer_stack_id)
    if layer_stack is not None:
        layer_stack.node_manager.rebuild_node_tree()


def register():
    bpy.utils.register_class(LayerStack)

    bpy.types.Material.pml_layer_stack = PointerProperty(
        type=LayerStack)


def unregister():
    bpy.utils.unregister_class(LayerStack)

    del bpy.types.Material.pml_layer_stack
