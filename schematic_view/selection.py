"""Reflect graph selection in the scene without writing data during drawing."""

from collections import defaultdict

import bpy
from bpy.app.handlers import persistent

from .graph import viewport_visible


seen = {}
pending = {}


def graph_selection(tree):
    from .editor import active_entity, node_entity

    entities = [entity for node in tree.nodes if node.select
                and (entity := node_entity(node)) is not None]
    active = active_entity(tree)
    if active in entities:
        entities.remove(active)
        entities.append(active)
    return entities


def remember(tree):
    uid = tree.session_uid
    seen[uid] = tuple((entity.obj.session_uid, entity.bone) for entity in graph_selection(tree))
    pending.pop(uid, None)


def observe():
    from .editor import editor_tree

    context = bpy.context
    tree = editor_tree(context)
    if not tree or tree.scene != context.scene or not context.window:
        return
    entities = graph_selection(tree)
    signature = tuple((entity.obj.session_uid, entity.bone) for entity in entities)
    uid = tree.session_uid
    previous = seen.setdefault(uid, ())
    if previous == signature:
        return
    seen[uid] = signature
    if not entities:
        pending.pop(uid, None)
        return
    pending[uid] = (context.window, context.area, tree)
    if not bpy.app.timers.is_registered(flush):
        bpy.app.timers.register(flush, first_interval=0.0)


def flush():
    from .editor import TREE_ID

    work = list(pending.values())
    pending.clear()
    for window, graph_area, tree in work:
        try:
            if (graph_area not in window.screen.areas[:] or graph_area.type != "NODE_EDITOR"
                    or graph_area.spaces.active.tree_type != TREE_ID
                    or graph_area.spaces.active.edit_tree != tree or window.scene != tree.scene):
                continue
            owner = window.parent or window
            view = max((area for area in owner.screen.areas if area.type == "VIEW_3D"),
                       key=lambda area: area.width * area.height, default=None)
            target_window, area = (owner, view) if view else (window, graph_area)
            region = next((region for region in area.regions if region.type == "WINDOW"), None)
            if region:
                with bpy.context.temp_override(window=target_window, area=area, region=region):
                    select_in_viewport(bpy.context, graph_selection(tree))
            for area in owner.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
        except (ReferenceError, RuntimeError):
            continue
    return None


def select_in_viewport(context, entities):
    layer = context.view_layer
    selected = [entity for entity in entities
                if not entity.obj.hide_select and viewport_visible(entity, layer)
                and (not entity.bone or not entity.owner.bone.hide_select)]
    if not selected:
        return
    mode = context.mode
    original_active = layer.objects.active

    if mode == "EDIT_ARMATURE":
        from .actions import select_edit_bones

        if not original_active or not original_active.data:
            return
        bones = original_active.data.edit_bones
        names = [entity.bone for entity in selected if entity.obj == original_active
                 and entity.bone and (bone := bones.get(entity.bone))
                 and not bone.hide and not bone.hide_select]
        if names:
            select_edit_bones(bones, names)
        return
    if mode not in {"OBJECT", "POSE"}:
        return

    primary = selected[-1].obj
    if mode == "POSE" and primary.mode != "POSE":
        if bpy.ops.object.mode_set(mode="OBJECT") != {"FINISHED"}:
            return
        mode = "OBJECT"
    if mode == "POSE":
        selected = [entity for entity in selected if entity.obj.mode == "POSE"]

    objects = defaultdict(list)
    for entity in selected:
        if entity.bone:
            objects[entity.obj].append(entity.bone)
        else:
            objects.setdefault(entity.obj, [])
    for obj in layer.objects:
        value = obj in objects
        if obj.select_get(view_layer=layer) != value:
            obj.select_set(value, view_layer=layer)
    layer.objects.active = primary
    for obj, names in objects.items():
        if not obj.pose or (not names and mode != "POSE"):
            continue
        picked = set(names)
        for bone in obj.pose.bones:
            bone.select = bone.name in picked
        obj.data.bones.active = obj.data.bones.get(names[-1]) if names else None


@persistent
def clear_state(_dummy=None):
    seen.clear()
    pending.clear()
