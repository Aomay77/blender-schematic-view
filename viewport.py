# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aomay Huang
# Schematic View for Blender. Modified versions must say so and keep this notice.
"""Track visibility changes owned by Schematic filters.⁠​‌​‌‌​‌‌‌​‌​‌‌‌​​​‌​‌‌​​‌​‌‌‌‌‌‌‌‌​‌​​‌​‌‌​​​‌‌‌​‌‌​‌​​​‌​‌​​​‌‌​​​​​​‌​‌​‌​‌‌​‌‌​‌‌‌​‌​​​‌​‌​​‌⁠"""

import bpy
from bpy.props import PointerProperty, StringProperty
from bpy.types import PropertyGroup

from .graph import Entity, matching_nodes


class SCHEMATIC_PG_viewport_hidden(PropertyGroup):
    object_ref: PointerProperty(type=bpy.types.Object)
    bone: StringProperty()
    view_layer: StringProperty()


def set_hidden(entity, layer, hidden):
    obj = entity.obj
    if entity.bone:
        owner = entity.owner
        if owner is None:
            return True
        if owner.is_property_readonly("hide"):
            return False
        owner.hide = hidden
        return owner.hide == hidden
    if layer is None or layer.objects.get(obj.name) != obj:
        return True
    obj.hide_set(hidden, view_layer=layer)
    return obj.hide_get(view_layer=layer) == hidden


def restore(tree, keep=()):
    records = tree.viewport_hidden
    for index in reversed(range(len(records))):
        record = records[index]
        obj, bone, layer_name = record.object_ref, record.bone, record.view_layer
        if (obj, bone, layer_name) in keep:
            continue
        layer = tree.scene.view_layers.get(layer_name) if layer_name else None
        try:
            if not obj or set_hidden(Entity(obj, bone), layer, False):
                records.remove(index)
        except (RuntimeError, ReferenceError, TypeError):
            continue


def sync(tree, layer, entities):
    matches = set(matching_nodes(tree, entities))
    # A bone found by name or property still needs its armature in the viewport.
    if "ARMATURE" in tree.node_types:
        matches.update({(obj, "") for obj, bone in matches if bone})
    excluded = {}
    for key, entity in entities.items():
        if key in matches or layer.objects.get(entity.obj.name) != entity.obj:
            continue
        layer_name = "" if entity.bone else layer.name
        excluded[(*key, layer_name)] = entity

    # Object visibility is per view layer. Bone visibility belongs to the pose object.
    # Preserve still-active object filters in other view layers until they are reset.
    keep = set(excluded)
    for record in tree.viewport_hidden:
        key = record.object_ref, record.bone
        if record.view_layer and record.view_layer != layer.name and key in entities and key not in matches:
            keep.add((*key, record.view_layer))
    restore(tree, keep)
    owned = {(record.object_ref, record.bone, record.view_layer) for record in tree.viewport_hidden}
    for key, entity in excluded.items():
        obj, bone, layer_name = key
        if bone and entity.owner is None:
            continue
        already_hidden = entity.owner.hide if bone else obj.hide_get(view_layer=layer)
        if already_hidden:
            continue
        try:
            if set_hidden(entity, layer, True) and key not in owned:
                record = tree.viewport_hidden.add()
                record.object_ref, record.bone, record.view_layer = obj, bone, layer_name
        except (RuntimeError, ReferenceError, TypeError):
            continue
