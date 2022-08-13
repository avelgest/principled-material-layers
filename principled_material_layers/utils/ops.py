# SPDX-License-Identifier: GPL-2.0-or-later

import contextlib
import io

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
    with contextlib.redirect_stdout(io.StringIO()):
        print("test")
        for img in images:
            if not img.is_dirty:
                continue
            op_ctx["edit_image"] = img
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
        # Return True if currently editing the active layer's node tree
        if (space.edit_tree is not None
                and layer_stack.active_layer is not None
                and space.edit_tree == layer_stack.active_layer.node_tree):
            return True

        # Return True if a ShaderNodePMLStack is selected
        if (context.active_node is not None
                and context.active_node.bl_idname == "ShaderNodePMLStack"):
            return True
    return False
