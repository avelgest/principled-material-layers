# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from typing import Callable, NamedTuple, Optional

import bpy

from bpy.props import (BoolProperty,
                       EnumProperty,
                       FloatProperty,
                       IntProperty,
                       PointerProperty,
                       StringProperty)

from bpy.types import NodeSocket, ShaderNodeTree

from . import blending
from . import hardness

from .utils.layer_stack_utils import get_layer_stack_from_prop
from .utils.node_tree_import import load_addon_node_group

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
        description="How smoothly this channel transitions between values",
        default='DEFAULT'
    )
    hardness_threshold: FloatProperty(
        name="Hardness Threshold",
        description="Affects the central value of the hardness transition"
                    "(depending on hardness funtion used)",
        subtype='FACTOR',
        default=0.5, min=0.0, max=1.0
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
        name="Bake Image Channel",
        description="The image channel of 'bake_image' in which this channel "
                    "is baked",
        min=-1, max=3, default=-1
    )

    opacity: FloatProperty(
        name="Opacity",
        description="The opacity of this channel for this layer",
        min=0.0, max=1.0, default=1.0,
        subtype='FACTOR',
        update=lambda self, _: self._opacity_update()
    )

    # renormalize is only used by the layer stack, not individual layers
    renormalize: BoolProperty(
        name="Renormalize",
        description="Should this channel be renormalized after blending",
        get=lambda self: self._renormalize,
        set=lambda self, value: setattr(self, "_renormalize", value),
    )

    # When a channel is previewed a node group may be used to modify
    # the channel's output before it is displayed
    preview_modifier: EnumProperty(
        items=PREVIEW_MODIFIERS_ENUM,
        name="Preview As",
        description="How this channel should be previewed",
        default='NONE',
        update=lambda self, _: self._preview_modifier_update()
    )

    usage: EnumProperty(
        items=(
            ('BLENDING', "Blending", "Channel can be blended with channels "
             "in other layers"),
            ('LAYER_ALPHA', "Layer Alpha", "Channel outputs a layer's alpha"),
            ('NONE', "None", "Channel is not unused"),
        ),
        name="Channel Usage",
        description="How the channel is used",
        default='BLENDING'
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

    def _opacity_update(self):
        if self.opacity > 1.0-1e-3 and self.opacity < 1.0:
            self.opacity = 1.0
            return
        if self.opacity < 1.0 and self.is_layer_channel:
            nm = self.layer_stack.node_manager
            if not nm.has_channel_opacity(self.layer, self):
                nm.rebuild_node_tree()

    def _preview_modifier_update(self) -> None:
        # The preview_modifier of this channel on every layer should
        # match the same channel on the layer stack itself.

        preview_mod = self.preview_modifier
        if self.is_layer_channel:
            # For layer channels set the layer stack channel's value
            # as well.
            layer_stack_ch = self.layer_stack_channel
            if (layer_stack_ch is not None
                    and layer_stack_ch.preview_modifier != preview_mod):
                layer_stack_ch.preview_modifier = preview_mod
        else:
            # For layer stack channels set the value on the matching
            # channel for each layer.
            for layer in self.layer_stack.layers:
                layer_ch = layer.channels.get(self.name)
                if (layer_ch is not None
                        and layer_ch.preview_modifier != preview_mod):
                    layer_ch.preview_modifier = preview_mod

    @property
    def effective_blend_mode(self) -> str:
        """The blend_mode enum actually used by this channel. The same
        as self.blend_mode unless the value is 'DEFAULT' in which case
        the value on the layer stack's matching channel is returned.
        """
        if not self.is_layer_channel:
            return self.blend_mode if self.blend_mode != 'DEFAULT' else 'MIX'

        if self.blend_mode == 'DEFAULT':
            layer_stack_ch = self.layer_stack_channel
            if layer_stack_ch is not None:
                return layer_stack_ch.effective_blend_mode

        return self.blend_mode

    @property
    def effective_hardness(self) -> str:
        """The hardness enum actually used by this channel. The same as
        self.hardness unless the value is 'DEFAULT' in which case the
        value on the layer stack's matching channel is returned.
        """
        if not self.is_layer_channel:
            return self.hardness if self.hardness != 'DEFAULT' else 'LINEAR'

        if self.hardness == 'DEFAULT':
            layer_stack_ch = self.layer_stack_channel
            if layer_stack_ch is not None:
                return layer_stack_ch.effective_hardness
        return self.hardness

    @property
    def effective_hardness_custom(self) -> Optional[ShaderNodeTree]:
        """The value of hardness_custom actually used by this channel.
        If hardness is 'DEFAULT' this is the hardness_custom property
        of the matching channel in layer_stack.channels. Returns None
        if hardness is not 'CUSTOM'.
        """
        if self.hardness == 'DEFAULT':
            layer_stack_ch = self.layer_stack.channels.get(self.name)
            if layer_stack_ch is not None:
                return layer_stack_ch.effective_hardness_custom
        return self.hardness_custom if self.hardness == 'CUSTOM' else None

    @property
    def hardness_node_make_info(self):
        """The NodeMakeInfo needed to create a node for this channel's
        hardness.
        """
        return hardness.HARDNESS_NODE_INFO[self.effective_hardness]

    @property
    def hardness_supports_threshold(self) -> bool:
        """Returns True if this channels effective hardness supports
        using a threshold value.
        """
        effective_hardness = self.effective_hardness
        if effective_hardness != 'CUSTOM':
            return hardness.supports_threshold(effective_hardness, None)

        return hardness.supports_threshold(effective_hardness,
                                           self.effective_hardness_custom)

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
        """The layer that this channel belongs to. May be None if e.g.
        this channel is on the layer stack itself.
        """
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
        return None if not layer_stack else layer_stack.channels.get(self.name)

    @property
    def _renormalize_default_val(self) -> bool:
        """Default value for the channel's renormalize property."""
        name = self.name.lower()
        return ("normal" in name or "tangent" in name)

    @property
    def _renormalize(self) -> bool:
        """Whether this channel should be renormalized. Always False if
        this channel is not a vector channel on the layer stack itself.
        """
        if self.socket_type != 'VECTOR':
            return False

        if not self.is_layer_channel:
            value = self.get("renormalize")
            if value is None:
                value = self["renormalize"] = self._renormalize_default_val
            return value

        layer_stack_ch = self.layer_stack_channel
        return False if layer_stack_ch is None else layer_stack_ch.renormalize

    @_renormalize.setter
    def _renormalize(self, value: bool):
        if not self.is_layer_channel:
            self["renormalize"] = bool(value)


class PreviewModifier(NamedTuple):
    """A preview modifier is a node group placed between the socket of
    a channel being sampled and the material output.
    Attributes:
        enum: String that should be used as an identifier in an
            EnumProperty.
        name: The name of this preview modifier in the interface.
        description: An optional description that can be shown in a
            tooltip.
        node_group_name: The name of the node group to use. Should
            match a file in the node_groups directory of the add-on.
            If None then the preview will shown unmodified.
        channel_types: Should be a container or None. If a container
            the preview modifier will only be available for channels
            with a socket_type contained in channel_types.
        condition: If not None should be a callable that takes a
            channel as its only argument and returns a bool. This
            preview modifier will only be available for a channel
            if condition is None or returns True for the channel.
    """
    enum: str
    name: str
    description: str = ""
    node_group_name: Optional[str] = None
    channel_types: Optional[set[str]] = None
    condition: Optional[Callable[[Channel], bool]] = None

    def load_node_group(self) -> Optional[bpy.types.ShaderNodeTree]:
        """Loads this PreviewModifier's node group from its file and
        returns it. Will return None if node_group_name is None.
        """
        if not self.node_group_name:
            return None
        node_group = load_addon_node_group(self.node_group_name)
        if node_group.type != 'SHADER':
            return None
        return node_group

    def to_enum_tuple(self) -> tuple[str, str, str]:
        return (self.enum, self.name, self.description)

    def should_show_for(self, channel: Channel) -> bool:
        """Returns True if this PreviewModifier should be available
        for channel.
        """
        if (self.channel_types is not None
                and channel.socket_type not in self.channel_types):
            return False

        return True if self.condition is None else self.condition(channel)


PREVIEW_MODIFIERS = (
    PreviewModifier('NONE', "Unmodified",
                    "Preview the channel's actual value (may not be very "
                    "helpful for some channels e.g. normals)"),
    PreviewModifier('GRAYSCALE', "Grayscale",
                    "Preview the luminance of a color",
                    "PML Preview Grayscale", channel_types={'COLOR'}),
    PreviewModifier('OBJECT_TO_TANGENT', "Tangent Space",
                    "Preview a vector channel in tangent space",
                    "PML Object to Tangent Space", channel_types={'VECTOR'}),
    PreviewModifier('HEATMAP_FACTOR', "Heat Map",
                    "Heatmap from blue to red. Blue is 0; red is 1",
                    "PML Heatmap Factor",
                    channel_types={'FLOAT', 'FLOAT_FACTOR'}),
)
PREVIEW_MODIFIERS_ENUM = [x.to_enum_tuple() for x in PREVIEW_MODIFIERS]


def preview_modifier_from_enum(enum: str) -> PreviewModifier:
    """Returns the PreviewModifier instance with the given enum string."""
    for x in PREVIEW_MODIFIERS:
        if x.enum == enum:
            return x
    raise KeyError(f"Unknown preview modifier enum '{enum}'")


classes = (Channel, BasicChannel,)

register, unregister = bpy.utils.register_classes_factory(classes)
