# SPDX-License-Identifier: GPL-2.0-or-later

import contextlib
import io
import sys

import bpy

from bpy.types import Context

from .layer_stack_utils import get_layer_stack


def ensure_global_undo() -> None:
    """Tries to ensure the next undo step pushed is a global undo step
    by making a temporary change to the current blend data.
    """
    # Make an edit to the blend data so that a global update is pushed
    tmp = bpy.data.texts.new(name="pml_tmp")
    bpy.data.texts.remove(tmp)


def save_all_modified() -> None:
    """Saves all the modified images of the active layer stack."""
    if not bpy.ops.image.save_all_modified.poll():
        return

    layer_stack = get_layer_stack(bpy.context)
    im = layer_stack.image_manager

    images = set(im.layer_images_blend)
    if im.active_image is not None:
        images.add(im.active_image)

    op_ctx = bpy.context.copy()
    with filter_stdstream(prefix="Info:", stdout=True):
        for img in images:
            if not img.is_dirty:
                continue
            op_ctx["edit_image"] = img
            bpy.ops.image.save(op_ctx)


def save_image(image: bpy.types.Image, dirty_only=True) -> None:
    if dirty_only and not image.is_dirty:
        return
    op_ctx = bpy.context.copy()
    op_ctx["edit_image"] = image
    with filter_stdstream(prefix="Info:", stdout=True):
        bpy.ops.image.save(op_ctx)


def pml_is_supported_editor(context: Context) -> bool:
    """Returns True if currently in a supported editor."""
    space = context.space_data

    if space is None:
        return True
    if space.type == 'VIEW_3D' and context.mode == 'PAINT_TEXTURE':
        return True
    if space.type == 'NODE_EDITOR' and space.tree_type == 'ShaderNodeTree':
        return True
    return False


def pml_op_poll(context: Context) -> bool:
    """Returns True if currently in a supported editor with an active
    layer stack. This is the most common poll function for the
    operators in this addon.
    """
    layer_stack = get_layer_stack(context)
    if layer_stack is None or not layer_stack.is_initialized:
        return False

    space = context.space_data

    if space is None:
        return True
    if space.type == 'VIEW_3D' and context.mode == 'PAINT_TEXTURE':
        return True
    if space.type == 'NODE_EDITOR':
        edit_tree = space.edit_tree
        if edit_tree is None or space.shader_type != 'OBJECT':
            return False

        ma_tree = layer_stack.material.node_tree
        if edit_tree == ma_tree or space.path[0].node_tree == ma_tree:
            return True
    return False


@contextlib.contextmanager
def filter_stdstream(*strings: str, prefix=None,
                     stdout: bool = True, stderr: bool = False):
    """Context manager that filters strings from stdout or stderr
    printing any unfiltered strings when the context manager exits.
    """
    assert not isinstance(strings, str)
    to_filter = set(strings)

    filter_buffers = {"stdout": io.StringIO() if stdout else None,
                      "stderr": io.StringIO() if stderr else None
                      }

    try:
        with contextlib.ExitStack() as stack:
            for st_type, buffer in filter_buffers.items():
                if st_type == "stdout" and buffer is not None:
                    stack.enter_context(contextlib.redirect_stdout(buffer))
                if st_type == "stderr" and buffer is not None:
                    stack.enter_context(contextlib.redirect_stderr(buffer))
            yield stack
    finally:
        for st_type, buffer in filter_buffers.items():
            if not buffer:
                continue
            lines = buffer.getvalue().split("\n")
            if lines[-1] == "\n":
                lines.pop()

            stream = getattr(sys, st_type)
            for line in lines:
                if line in to_filter or (prefix and line.startswith(prefix)):
                    continue
                print(line, file=stream)


class WMProgress:
    """Context manager for showing progress using
    window_manager.progress_begin etc.
    progress_update is called when the value property is changed.
    """
    def __init__(self, min_: int, max_: int):
        self.min_value = min_
        self.max_value = max_
        self._value = min_

    def __enter__(self):
        self.window_manager.progress_begin(self.min_value, self.max_value)
        self.update(self.min_value)
        return self

    def __exit__(self, *args):
        self.window_manager.progress_end()

    def update(self, value: int) -> None:
        """Calls window_manager.progress_update with value."""
        value = min(value, self.max_value)
        self.window_manager.progress_update(value)
        self._value = value

    @property
    def value(self) -> int:
        """The current progress value."""
        return self._value

    @value.setter
    def value(self, new_value: int):
        self.update(new_value)

    @property
    def window_manager(self):
        return bpy.context.window_manager
