# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from typing import Optional

import bpy

from bpy.props import (BoolProperty,
                       EnumProperty,
                       IntProperty,
                       PointerProperty,
                       StringProperty)

from bpy.types import NodeSocket, ShaderNodeTree

from . import blending
from . import hardness
from .utils.layer_stack_utils import get_layer_stack_from_prop

SOCKET_TYPES = (('FLOAT', "Float", "Float in [-inf, inf]"),
                ('FLOAT_FACTOR', "Float Factor", "Float in [0, 1]"),
                ('COLOR', "Color", "RGB color"),
                ('VECTOR', "Vector", "3D vector"),
                )

SOCKET_CLASSES = {'FLOAT': bpy.types.NodeSocketFloat,
                  'FLOAT_FACTOR': bpy.types.NodeSocketFloatFactor,
                  'COLOR': bpy.types.NodeSocketColor,
                  'VECTOR': bpy.types.NodeSocketVector,
                  }

# Maps the SOCKET_TYPES enum to the NodeSocket.type enum
_SOCKET_BL_ENUM_TYPES = {'FLOAT': 'VALUE',
                         'FLOAT_FACTOR': 'VALUE',
                         'COLOR': 'RGBA',
                         'VECTOR': 'VECTOR',
                         }


def is_socket_supported(socket: NodeSocket) -> bool:
    """Returns True if a channel can be initialized from socket."""
    type_name = type(socket).__name__

    return (type_name.startswith("NodeSocketFloat")
            or type_name.startswith("NodeSocketVector")
            or isinstance(socket, bpy.types.NodeSocketColor))


def get_socket_type(socket: NodeSocket) -> str:
    """Get the type of channel that should be used for this socket.
    Raises a TypeError if the socket is not a supported type.

    Params:
        socket: a NodeSocket instance
    Returns:
        A SOCKET_TYPES enum. One of 'FLOAT', 'FLOAT_FACTOR', 'Vector',
        'COLOR', 'SHADER'
    """
    type_name = type(socket).__name__

    if not isinstance(socket, bpy.types.NodeSocket):
        raise TypeError(f"Expected a NodeSocket not a {type_name}")

    if isinstance(socket, bpy.types.NodeSocketFloatFactor):
        if "IOR" in socket.name:
            # Special case for Subsurface IOR which uses
            # NodeSocketFloatFactor but is not in [0, 1]
            return 'FLOAT'
        return 'FLOAT_FACTOR'
    if type_name.startswith("NodeSocketFloat"):
        return 'FLOAT'
    if type_name.startswith("NodeSocketVector"):
        return 'VECTOR'
    if isinstance(socket, bpy.types.NodeSocketColor):
        return 'COLOR'

    raise TypeError(f"Socket type not supported ({type_name})")


class BasicChannel(bpy.types.PropertyGroup):
    """PropertyGroup containing basic information needed to initialize
    a Channel instance. Unlike its subclass Channel this class can be
    used as a property on operators."""
    public_props = ("name", "enabled", "socket_type")

    name: StringProperty(
        name="Name"
    )
    enabled: BoolProperty(
        name="Enabled",
        default=True
    )
    socket_type: EnumProperty(
        name="Socket Type",
        items=SOCKET_TYPES,
        default='FLOAT_FACTOR'
    )

    def initialize(self, name: str, socket_type: str):
        self.name = name
        self.socket_type = socket_type

    def init_from_channel(self, channel: Channel) -> None:
        for prop in self.public_props:
            setattr(self, prop, getattr(channel, prop))

    def init_from_socket(self, socket: NodeSocket) -> None:
        self.name = socket.name
        self.socket_type = get_socket_type(socket)

    def delete(self) -> None:
        return

    @property
    def socket_type_bl_idname(self) -> str:
        socket_class = SOCKET_CLASSES[self.socket_type]

        return socket_class.__name__

    @property
    def socket_type_bl_enum(self) -> str:
        return _SOCKET_BL_ENUM_TYPES[self.socket_type]


def _publish_rna_callback_factory(property_name: str) -> callable:
    def _callback(self, dummy_context):
        bpy.msgbus.publish_rna(key=self.path_resolve(property_name, False))
    return _callback


class Channel(BasicChannel):
    """A channel used by a LayerStack and its layers. Unless a custom
    channel is added each channel will have a corresponding input socket
    on the node that the LayerStack was created against (by default a
    Principled BSDF), with the same name and matching value type.
    """
    name: StringProperty(
        name="Name"
    )
    enabled: BoolProperty(
        name="Enabled",
        description="Whether or not this channel is enabled",
        default=True,
        update=_publish_rna_callback_factory("enabled")
    )
    blend_mode: EnumProperty(
        name="Blend Mode",
        items=blending.BLEND_MODES,
        default='DEFAULT'
    )
    # Node group for 'CUSTOM' blend mode
    blend_mode_custom: PointerProperty(
        type=bpy.types.ShaderNodeTree,
        name="Custom Blend Mode",
        description="The node group used to blend this channel",
        update=_publish_rna_callback_factory("blend_mode")
    )
    hardness: EnumProperty(
        items=hardness.HARDNESS_MODES,
        name="Hardness",
        description="How smoothly this channel transitions. E.g Binary "
                    "instantly changes between two values",
        default='DEFAULT'
    )
    hardness_custom: PointerProperty(
        type=bpy.types.ShaderNodeTree,
        name="Custom Hardness",
        description="The node group used for this channel's custom hardness",
        update=_publish_rna_callback_factory("hardness")
    )
    bake_image: PointerProperty(
        type=bpy.types.Image,
        name="Bake Image",
        description="The image that this channel is currently baked to"
    )
    bake_image_channel: IntProperty(
        name="The image channel of 'bake_image' in which this channel "
             "is baked",
        min=-1, max=3, default=-1
    )
    # The identifier of the layer this channel belongs to. May be "" if
    # this channel instance is in LayerStack.channels rather than on a
    # layer
    layer_identifier: StringProperty(
        name="Layer ID",
        default=""
    )

    def initialize(self, name: str, socket_type: str, layer=None) -> None:
        """Initializes the channel. This or another of the init methods
        should be called before this channel is used.
        """
        super().initialize(name, socket_type)
        self._init_props(layer)

    def init_from_channel(self, channel: BasicChannel, layer=None) -> None:
        """Initializes the channel from a BasicChannel instance."""
        super().init_from_channel(channel)
        self._init_props(layer)

    def init_from_socket(self, socket: NodeSocket, layer=None) -> None:
        """Initializes the channel from a NodeSocket."""
        super().init_from_socket(socket)
        self._init_props(layer)

    def _init_props(self, layer) -> None:
        if layer is not None:
            self.layer_identifier = layer.identifier

        if not self.is_layer_channel:
            self.blend_mode = 'MIX'
            self.hardness = 'LINEAR'

    def delete(self) -> None:
        super().delete()
        self.layer_identifier = ""
        self.free_bake()

    def free_bake(self) -> None:
        if self.is_baked:
            self.layer_stack.image_manager.deallocate_bake_image(self)

    def make_blend_node(self, node_tree: ShaderNodeTree):
        return self.blend_node_make_info.make(node_tree, self)

    def set_bake_image(self, image, channel: int = -1):
        self.bake_image = image
        self.bake_image_channel = channel

    @property
    def hardness_node_make_info(self):
        """The NodeMakeInfo needed to create a node for this channel's
        hardness.
        """
        # If default hardness then use the hardness value of the layer
        # stack's channel (or LINEAR if this is a layer_stack channel
        # itself)
        if self.hardness == 'DEFAULT':
            if not self.is_layer_channel:
                return hardness.HARDNESS_NODE_INFO['LINEAR']
            layer_stack_ch = self.layer_stack.channels.get(self.name)

            return (layer_stack_ch.hardness_node_make_info
                    if layer_stack_ch is not None
                    else hardness.HARDNESS_NODE_INFO['LINEAR'])
        return hardness.HARDNESS_NODE_INFO[self.hardness]

    @property
    def default_hardness_custom(self) -> Optional[ShaderNodeTree]:
        """The default value of hardness_custom for this channel.
        This is the hardness_custom property of the matching channel
        in layer_stack.channels.
        """
        if not self.is_layer_channel:
            return self.hardness_custom

        layer_stack_ch = self.layer_stack_channel
        if layer_stack_ch is None:
            return None
        return layer_stack_ch.hardness_custom

    @property
    def blend_node_make_info(self):
        blend_node_info = blending.BLEND_MODES_NODE_INFO
        if self.blend_mode == 'DEFAULT':
            return blend_node_info[self.default_blend_mode]
        return blend_node_info[self.blend_mode]

    @property
    def default_blend_mode(self):
        """The default value of blend_mode that this channel should have.
        This is the same for all channels in the layer stack with the
        same name. Readonly when this channel belongs to a layer rather
        than a layer stack.
        """
        if not self.is_layer_channel:
            return ('MIX' if self.blend_mode == 'DEFAULT'
                    else self.blend_mode)

        # For MaterialLayer channels return the blend_mode of the
        # matching channel on the layer stack
        layer_stack_ch = self.layer_stack_channel
        if layer_stack_ch is None:
            return 'MIX'

        assert not layer_stack_ch.is_layer_channel
        return layer_stack_ch.default_blend_mode

    @property
    def default_blend_mode_custom(self) -> Optional[ShaderNodeTree]:
        """The default value of blend_mode_custom for this channel.
        This is the blend_mode_custom property of the matching channel
        in layer_stack.channels.
        """
        if not self.is_layer_channel:
            return self.blend_mode_custom

        layer_stack_ch = self.layer_stack_channel
        if layer_stack_ch is None:
            return None
        return layer_stack_ch.blend_mode_custom

    @property
    def is_baked(self) -> bool:
        return self.bake_image is not None

    @property
    def is_layer_channel(self) -> bool:
        """Returns true if this channel belongs to a MaterialLayer"""
        return bool(self.layer_identifier)

    @property
    def layer(self):
        if not self.layer_identifier:
            return None
        return self.layer_stack.get_layer_by_id(self.layer_identifier)

    @property
    def layer_stack(self):
        return get_layer_stack_from_prop(self)

    @property
    def layer_stack_channel(self) -> Optional[Channel]:
        """The channel on the layer stack with the same name as this
        channel.
        """
        layer_stack = self.layer_stack
        return layer_stack and layer_stack.channels.get(self.name)


classes = (Channel, BasicChannel,)

register, unregister = bpy.utils.register_classes_factory(classes)
