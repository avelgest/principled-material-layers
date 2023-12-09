# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import contextlib
import itertools as it

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, DefaultDict, Dict, Optional

import bpy

from bpy.types import Material

from .. import asset_helper

from .layer_stack_utils import get_layer_stack_by_id
from ..utils.nodes import get_output_node

_LayerStackID = str
_AssetStrKey = str

_CompatCache = Dict[_AssetStrKey, bool]

_asset_compat_caches: DefaultDict[_LayerStackID,
                                  _CompatCache] = defaultdict(dict)


def _asset_cache_key(asset: asset_helper.AssetInfo) -> _AssetStrKey:
    if asset.local_id is not None:
        return asset.local_id.name_full
    return asset.relative_path


def _get_cached_asset_compat(asset, layer_stack) -> IsMaterialCompat:
    cache = _asset_compat_caches[layer_stack.identifier]
    return cache.get(_asset_cache_key(asset))


def _set_cached_asset_compat(asset,
                             layer_stack,
                             value: IsMaterialCompat) -> None:
    cache = _asset_compat_caches[layer_stack.identifier]
    cache[_asset_cache_key(asset)] = value


def del_cached_asset_compat(asset, layer_stack) -> None:
    cache = _asset_compat_caches[layer_stack.identifier]
    cache.pop(_asset_cache_key(asset), None)


@dataclass(frozen=True)
class IsMaterialCompat:
    """The result of checking whether a material is compatible with a
    layer stack. Instances have a bool value of True if they are compatible
    or False if they are incompatible or if the check is in progress.
    When an instance has a False bool value it will have a reason for this
    in its reason property.
    """
    _given_reason: Optional[str]
    matched_sockets: int = 0
    unmatched_channels: int = 0
    in_progress: bool = False

    def __bool__(self):
        return not self.reason

    @property
    def label_text(self) -> str:
        if self:
            if not self.unmatched_channels:
                return "Compatible: All Channels Found"
            if self.unmatched_channels < self.matched_sockets:
                return (f"Mostly Compatible: {self.unmatched_channels} "
                        "Channels Not Found")
            return (f"Low Compatibility: {self.unmatched_channels} "
                    "Channels Not Found")

        if self.in_progress:
            return "Checking Compatibility..."

        return f"Incompatible: {self.reason}."

    @property
    def label_text_short(self) -> str:
        if self:
            if not self.unmatched_channels:
                return "Fully Compatible"
            if self.unmatched_channels < self.matched_sockets:
                text = "Mostly Compatible"
            else:
                text = "Low Compatibility"
            return f"{text} {self.matched_sockets}:{self.unmatched_channels}"

        if self.in_progress:
            return "Checking Compatibility..."

        return f"Incompatible: {self.reason}."

    @property
    def label_icon(self) -> str:
        if self:
            if self.unmatched_channels < self.matched_sockets:
                return 'CHECKMARK'
            return 'ERROR'
        if self.in_progress:
            return 'NONE'
        return 'ERROR'

    @classmethod
    def make_in_progress(cls):
        """Returns a new instance with in_progress set to True.
        The new instance will a False bool value.
        """
        return cls("Check is in progress", in_progress=True)

    @property
    def reason(self) -> Optional[str]:
        """The reason why this material is incompatible. None if the
        material is compatible.
        """
        if self._given_reason:
            return self._given_reason
        if self.matched_sockets == 0:
            return "No matching sockets"
        return None

    @property
    def total_channels(self) -> int:
        """The number of channels in the layer stack"""
        return self.matched_sockets + self.unmatched_channels


def check_material_compat(ma: Material,
                          layer_stack) -> IsMaterialCompat:
    if ma.node_tree is None:
        return IsMaterialCompat("Material has no node tree")
    if ma is layer_stack.material:
        return IsMaterialCompat("Material contains this layer stack")

    output = get_output_node(ma.node_tree)
    if output is None:
        return IsMaterialCompat("No Material Output node")

    if not output.inputs[0].is_linked:
        return IsMaterialCompat("No linked surface shader")

    surface_shader = output.inputs[0].links[0].from_node

    sockets = {x.name for x in it.chain(surface_shader.inputs, output.inputs)}
    if not sockets:
        return IsMaterialCompat("No sockets on surface shader")

    shared = sockets.intersection({x.name for x in layer_stack.channels})
    num_unmatched_ch = len(layer_stack.channels) - len(shared)

    return IsMaterialCompat(None, matched_sockets=len(shared),
                            unmatched_channels=num_unmatched_ch)


MaterialAppender = Callable[[], Material]


def check_material_asset_compat(asset: asset_helper.AssetInfo,
                                layer_stack,
                                delayed: bool = False,
                                ma_appender: Optional[MaterialAppender] = None
                                ) -> IsMaterialCompat:
    """Checks whether a material asset is compatible with layer_stack.
    Params:
        asset: A asset_helper.AssetInfo instance.
        layer_stack: The LayerStack to check compatibility with.
        delayed: If True use bpy.app.timers to delay importing the asset.
            For use in situations where the BlendData cannot be changed
            (e.g. during a draw method)
        ma_appender: An optional callable that appends the Material to the
            blend file when called. If present will be used instead of the
            asset and library params.
    Returns:
        An IsMaterialCompat instance. This has a boolean value of True if
        the material is compatible.
    """

    # Support layer_stack being a layer stack identifier
    if isinstance(layer_stack, str):
        layer_stack = get_layer_stack_by_id(layer_stack)
        if not layer_stack:
            raise ValueError("Cannot find layer_stack")

    if not layer_stack.is_initialized:
        raise ValueError("layer_stack has not been initialized")

    # Don't cache local materials
    if asset.local_id is not None:
        return check_material_compat(asset.local_id, layer_stack)

    # If delayed then schedule the check and return a
    if delayed:
        return _delayed_check_ma_asset_compat(asset, layer_stack)

    # Check the cache, if cached.in_progress is True the the material
    # should be appended and checked now.
    cached = _get_cached_asset_compat(asset, layer_stack)
    if cached is not None and not cached.in_progress:
        return cached

    with contextlib.ExitStack() as exit_stack:
        try:
            if ma_appender:
                ma = ma_appender()
                if not isinstance(ma, Material):
                    raise RuntimeError("ma_appender did not return a Material")
            else:
                ma = asset.link_material(delayed=False)

        except OSError as e:
            is_compat = IsMaterialCompat(f"Error: {e}")
            _set_cached_asset_compat(asset, layer_stack, is_compat)

        except Exception as e:
            is_compat = IsMaterialCompat(f"Error: {e}")
            _set_cached_asset_compat(asset, layer_stack, is_compat)
            raise e

        exit_stack.callback(lambda: remove_appended_material(ma))

        is_compat = check_material_compat(ma, layer_stack)
        _set_cached_asset_compat(asset, layer_stack, is_compat)

        return is_compat


class _FakeAsset:
    """Contains enough of AssetInfo's attributes to be used as
    an argument to check_material_asset_compat. Intended to replace
    the AssetInfo instance when using delayed checking to prevent
    errors/crashes due to reallocation.
    """
    def __init__(self, asset):
        self.relative_path = asset.relative_path
        self.local_id = None


def _delayed_check_ma_asset_compat(asset: asset_helper.AssetInfo,
                                   layer_stack) -> IsMaterialCompat:
    """A delayed material compatibility check. If a cached value
    already exists then it is returned. Otherwise this schedules the
    check and returns an IsMaterialCompat instance with in_progress set
    to True. The boolean value of the IsMaterialCompat will be False.
    """
    cached = _get_cached_asset_compat(asset, layer_stack)
    if cached is not None:
        return cached

    # Use only pure Python classes to prevent crashes
    ma_appender = asset.link_material(delayed=True)
    fake_asset = _FakeAsset(asset)
    layer_stack_id = layer_stack.identifier

    def delayed_check():
        check_material_asset_compat(fake_asset, layer_stack_id,
                                    delayed=False, ma_appender=ma_appender)
    bpy.app.timers.register(delayed_check)

    is_compat = IsMaterialCompat.make_in_progress()
    _set_cached_asset_compat(asset, layer_stack, is_compat)
    return is_compat


def remove_appended_material(ma: Material) -> None:
    """Remove a linked or appended material and any images or
    node groups imported with it. Note: Despite the name currently
    only removes local or linked materials.
    """
    # Removing appended material assets can cause a crash when
    # linking to a library in the future.
    # So only delete local or linked materials
    if not ma.library and ma.library_weak_reference:
        return

    images = set()
    node_groups = set()
    if ma.node_tree is not None:
        for node in ma.node_tree.nodes:
            if (isinstance(node, bpy.types.ShaderNodeTexImage)
                    and node.image is not None):
                images.add(node.image)
            if (isinstance(node, bpy.types.ShaderNodeGroup)
                    and node.node_tree is not None):
                node_groups.add(node.node_tree)

    lib = ma.library  # May be None

    bpy.data.materials.remove(ma)

    # Remove all linked images and node groups previously used by ma
    # that now have 0 users
    for img in images:
        if img.library and not img.users:
            bpy.data.images.remove(img)
    for node_group in node_groups:
        if node_group.library and not node_group.users:
            bpy.data.node_groups.remove(node_group)

    if lib is not None and not lib.users_id:
        bpy.data.libraries.remove(lib)
