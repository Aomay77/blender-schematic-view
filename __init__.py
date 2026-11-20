"""Schematic View for Blender."""

import bpy

from . import actions, autorefresh, editor, guide, selection, viewport, wires


CLASSES = (*editor.CLASSES, *actions.CLASSES)
draw_handles = []


def register():
    guide.register_swatches()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.NODE_MT_context_menu.append(actions.context_menu)
    bpy.app.handlers.load_post.append(selection.clear_state)
    autorefresh.register()
    actions.register_keymaps()
    for callback, stage in (
        (wires.draw_wires, "BACKDROP"), (guide.draw_side_labels, "POST_PIXEL"),
        (guide.draw_note, "POST_PIXEL"), (selection.observe, "POST_PIXEL"),
    ):
        draw_handles.append(bpy.types.SpaceNodeEditor.draw_handler_add(callback, (), "WINDOW", stage))


def unregister():
    while draw_handles:
        bpy.types.SpaceNodeEditor.draw_handler_remove(draw_handles.pop(), "WINDOW")
    # Give back anything Schematic filters hid in the viewport before the add-on goes away.
    for tree in bpy.data.node_groups:
        if tree.bl_idname == editor.TREE_ID and tree.viewport_hidden:
            try:
                viewport.restore(tree)
            except (ReferenceError, RuntimeError):
                pass
    actions.unregister_keymaps()
    autorefresh.unregister()
    bpy.types.NODE_MT_context_menu.remove(actions.context_menu)
    bpy.app.handlers.load_post.remove(selection.clear_state)
    if bpy.app.timers.is_registered(selection.flush):
        bpy.app.timers.unregister(selection.flush)
    selection.clear_state()
    wires.clear_cache()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    guide.unregister_swatches()
