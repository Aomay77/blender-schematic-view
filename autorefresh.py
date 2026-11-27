# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aomay Huang
# Schematic View for Blender. Modified versions must say so and keep this notice.
"""Keep open schematic tabs in step with the scene, like 3ds Max's live Schematic View.⁠​‌​‌‌​‌‌‌​‌​‌‌‌​​​‌​‌‌​​‌​‌‌‌‌‌‌‌‌​‌​​‌​‌‌​​​‌‌‌​‌‌​‌​​​‌​‌​​​‌‌​​​​​​‌​‌​‌​‌‌​‌‌​‌‌‌​‌​​​‌​‌​​‌⁠

Depsgraph updates only stamp a time. A timer waits until the scene has been quiet for a
moment, then rebuilds a tab only when its relationship fingerprint actually changed, so
dragging objects around or scrubbing the timeline never rebuilds the graph.
"""

import time

import bpy
from bpy.app.handlers import persistent


QUIET = 0.4
_last_change = 0.0


def schedule():
    global _last_change
    _last_change = time.monotonic()
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=QUIET)


@persistent
def on_depsgraph_update(_scene, depsgraph):
    from .editor import TREE_ID

    for update in depsgraph.updates:
        block = update.id
        if not (isinstance(block, bpy.types.NodeTree) and block.bl_idname == TREE_ID):
            schedule()
            return


@persistent
def on_undo(_scene, _dummy=None):
    schedule()


def open_tabs():
    """(tree, window) for every schematic tab shown in an editor."""
    from .editor import TREE_ID

    shown = {}
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "NODE_EDITOR":
                continue
            tree = area.spaces.active.edit_tree
            if tree and tree.bl_idname == TREE_ID and tree.auto_refresh and tree.scene == window.scene:
                shown.setdefault(tree, window)
    return shown


def tick():
    from .editor import refresh
    from .graph import collect, signature

    if time.monotonic() - _last_change < QUIET:
        return QUIET
    try:
        windows = bpy.context.window_manager.windows
        if any(window.screen.is_animation_playing for window in windows):
            return QUIET * 2
        tabs = open_tabs()
        collected = {}
        for tree, window in tabs.items():
            if any(obj.mode.startswith("EDIT") for obj in window.view_layer.objects if obj.mode != "OBJECT"):
                continue
            scene = tree.scene
            if scene not in collected:
                graph = collect(scene)
                collected[scene] = (graph, str(signature(*graph)))
            graph, fingerprint = collected[scene]
            if fingerprint != tree.graph_signature:
                refresh(tree, view_layer=window.view_layer, collected=graph)
                for area in window.screen.areas:
                    if area.type == "NODE_EDITOR":
                        area.tag_redraw()
    except (ReferenceError, RuntimeError, AttributeError):
        pass
    return None


def register():
    bpy.app.handlers.depsgraph_update_post.append(on_depsgraph_update)
    bpy.app.handlers.undo_post.append(on_undo)
    bpy.app.handlers.redo_post.append(on_undo)


def unregister():
    for handlers, callback in ((bpy.app.handlers.depsgraph_update_post, on_depsgraph_update),
                               (bpy.app.handlers.undo_post, on_undo),
                               (bpy.app.handlers.redo_post, on_undo)):
        if callback in handlers:
            handlers.remove(callback)
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
