"""Read Blender relationships independently of the editor and its filters."""

from collections import defaultdict
from dataclasses import dataclass
import re

import bpy


NODE_TYPES = {
    "MESH": ("Mesh", (0.14, 0.49, 0.43)),
    "ARMATURE": ("Armature", (0.44, 0.29, 0.61)),
    "EMPTY": ("Empty", (0.33, 0.38, 0.48)),
    "CAMERA": ("Camera", (0.27, 0.40, 0.63)),
    "LIGHT": ("Light", (0.61, 0.45, 0.19)),
    "OBJECT": ("Other Object", (0.29, 0.40, 0.42)),
    "CONTROL": ("ARP Controller", (0.10, 0.47, 0.64)),
    "REFERENCE": ("ARP Reference", (0.62, 0.32, 0.35)),
    "DEFORM": ("Deform Bone", (0.55, 0.33, 0.20)),
    "BONE": ("Other Bone", (0.40, 0.32, 0.48)),
}

RELATIONS = {
    "PARENT": ("Parent", (0.35, 0.72, 0.94, 1.0)),
    "CONSTRAINT": ("Constraint", (0.98, 0.64, 0.24, 1.0)),
    "DRIVER": ("Driver", (0.83, 0.46, 0.94, 1.0)),
    "ARMATURE": ("Skin / Armature", (0.31, 0.83, 0.54, 1.0)),
    "MEMBER": ("Armature Member", (0.66, 0.65, 0.76, 1.0)),
}

POSE_PATH = re.compile(r'^pose\.bones\["((?:[^"\\]|\\.)*)"\]')


@dataclass(frozen=True)
class Entity:
    obj: object
    bone: str = ""

    @property
    def key(self):
        return self.obj, self.bone

    @property
    def owner(self):
        if not self.bone:
            return self.obj
        return self.obj.pose.bones.get(self.bone) if self.obj.pose else None

    @property
    def name(self):
        return self.bone or self.obj.name


@dataclass(frozen=True)
class Edge:
    source: Entity
    target: Entity
    kind: str
    detail: str = ""


def arp_rig(obj):
    if obj.type != "ARMATURE" or not obj.pose:
        return False
    return (
        any(key in block for block in (obj, obj.data)
            for key in ("arp_rig_type", "rig_type", "has_match_to_rig"))
        or (obj.pose.bones.get("c_traj") is not None
            and obj.pose.bones.get("c_pos") is not None)
        or (obj.pose.bones.get("root_ref.x") is not None
            and obj.pose.bones.get("spine_01_ref.x") is not None)
    )


def kind_of(entity, is_arp):
    if not entity.bone:
        return entity.obj.type if entity.obj.type in NODE_TYPES else "OBJECT"
    name = entity.bone
    bone = entity.owner.bone
    if is_arp and (name.endswith("_ref") or "_ref." in name):
        return "REFERENCE"
    if is_arp and (name.startswith(("c_", "cc_")) or "cc" in entity.owner or any(
        collection.name.casefold() in {"main", "secondary", "controllers"}
        for collection in bone.collections
    )):
        return "CONTROL"
    return "DEFORM" if bone.use_deform else "BONE"


def pose_bone_from_path(path):
    match = POSE_PATH.match(path)
    return bpy.utils.unescape_identifier(match[1]) if match else ""


def collect(scene):
    entities = {}
    edges = []
    objects = list(scene.objects)
    aliases = defaultdict(list)
    for obj in objects:
        entities[obj, ""] = Entity(obj)
        if obj.pose:
            for bone in obj.pose.bones:
                entities[obj, bone.name] = Entity(obj, bone.name)
        if obj.data:
            aliases[obj.data].append(obj)
            shape_keys = getattr(obj.data, "shape_keys", None)
            if shape_keys:
                aliases[shape_keys].append(obj)

    def add(obj, bone, target, kind, detail=""):
        source = entities.get((obj, bone))
        if source and (source != target or kind == "DRIVER"):
            edges.append(Edge(source, target, kind, detail))

    for entity in entities.values():
        obj, bone = entity.key
        owner = entity.owner
        if bone:
            if owner.parent:
                detail = "Connected Joint" if owner.bone.use_connect else ""
                add(obj, owner.parent.name, entity, "PARENT", detail)
            else:
                add(obj, "", entity, "MEMBER")
        else:
            if obj.parent:
                parent_bone = obj.parent_bone if obj.parent_type == "BONE" else ""
                add(obj.parent, parent_bone, entity, "PARENT")
            for modifier in obj.modifiers:
                if modifier.type == "ARMATURE" and modifier.object:
                    add(modifier.object, "", entity, "ARMATURE", modifier.name)
        for constraint in owner.constraints:
            endpoints = [(getattr(constraint, field, None), subtarget)
                         for field, subtarget in (
                             ("target", getattr(constraint, "subtarget", "")),
                             ("pole_target", getattr(constraint, "pole_subtarget", "")),
                             ("space_object", getattr(constraint, "space_subtarget", "")),
                             ("camera", ""),
                         )]
            endpoints.extend((item.target, item.subtarget)
                             for item in getattr(constraint, "targets", ()))
            for target_obj, target_bone in dict.fromkeys(endpoints):
                if target_obj:
                    add(target_obj, target_bone, entity, "CONSTRAINT",
                        f"{constraint.name} [{constraint.type}]")

    for block in (*objects, *aliases):
        animation = getattr(block, "animation_data", None)
        if not animation:
            continue
        owners = [block] if isinstance(block, bpy.types.Object) else aliases[block]
        for curve in animation.drivers:
            if not curve.driver:
                continue
            for obj in owners:
                target = entities.get((obj, pose_bone_from_path(curve.data_path)))
                if not target:
                    continue
                for variable in curve.driver.variables:
                    for slot in variable.targets:
                        source_id = slot.id
                        if not source_id:
                            continue
                        source_bone = (getattr(slot, "bone_target", "")
                                       or pose_bone_from_path(getattr(slot, "data_path", "")))
                        sources = ([source_id] if isinstance(source_id, bpy.types.Object)
                                   else aliases.get(source_id, ()))
                        for source_obj in sources:
                            add(source_obj, source_bone, target, "DRIVER",
                                f"{curve.data_path}[{curve.array_index}] ← {variable.name}")
    return entities, list(dict.fromkeys(edges))


def scope_objects(tree):
    if tree.is_property_set("scope_mode"):
        return [entry.object_ref for entry in tree.scope_objects if entry.object_ref]
    return [tree.rig] if tree.rig else []


def is_scene_scope(tree):
    if tree.is_property_set("scope_mode"):
        return tree.scope_mode == "SCENE"
    return not tree.rig


def scoped_graph(tree):
    entire_scene = is_scene_scope(tree)
    if not entire_scene:
        anchors = set(scope_objects(tree))
        if not anchors:
            return {}, []
        anchors.intersection_update(tree.scene.objects)
        if not anchors:
            return {}, []
    entities, edges = collect(tree.scene)
    if entire_scene:
        return entities, edges
    scope = {key for key in entities if key[0] in anchors}
    for edge in edges:
        if edge.source.obj in anchors or edge.target.obj in anchors:
            scope.update((edge.source.key, edge.target.key))
    return {key: entity for key, entity in entities.items() if key in scope}, edges


def matching_nodes(tree, entities):
    rigs = {obj: arp_rig(obj) for obj in {obj for obj, _ in entities}}
    search = tree.search.strip().casefold()
    collection = tree.bone_collection.strip().casefold()
    custom_property = tree.custom_property.strip().casefold()
    matches = {}
    for key, entity in entities.items():
        owner = entity.owner
        kind = kind_of(entity, rigs[entity.obj])
        if kind not in tree.node_types:
            continue
        if collection and entity.bone and not any(
            collection in group.name.casefold() for group in owner.bone.collections
        ):
            continue
        if search and not any(search in value.casefold() for value in (entity.name, entity.obj.name)):
            continue
        if custom_property:
            properties = list(owner.keys())
            if entity.bone:
                properties.extend(owner.bone.keys())
            if not any(custom_property in name.casefold() for name in properties):
                continue
        matches[key] = kind
    return matches


def viewport_visible(entity, layer):
    obj = entity.obj
    if layer and layer.objects.get(obj.name) != obj:
        return False
    if obj.hide_viewport or not obj.visible_get(view_layer=layer):
        return False
    if not entity.bone:
        return True
    owner = entity.owner
    return not (owner.hide or owner.bone.hide or (
        owner.bone.collections
        and not any(group.is_visible_effectively for group in owner.bone.collections)
    ))


def visible_graph(tree, view_layer=None, scoped=None):
    entities, edges = scoped if scoped is not None else scoped_graph(tree)
    visible = matching_nodes(tree, entities)
    if tree.visible_only:
        layer = view_layer or bpy.context.view_layer
        visible = {key: kind for key, kind in visible.items()
                   if viewport_visible(entities[key], layer)}
    return visible, [edge for edge in edges if edge.kind in tree.relation_types
                     and edge.source.key in visible and edge.target.key in visible]
