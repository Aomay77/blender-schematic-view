# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aomay Huang
# Schematic View for Blender. Modified versions must say so and keep this notice.
"""Link Workbench: pick objects or bones, then parent, constrain or drive them from one panel.⁠​‌​‌‌​‌‌‌​‌​‌‌‌​​​‌​‌‌​​‌​‌‌‌‌‌‌‌‌​‌​​‌​‌‌​​​‌‌‌​‌‌​‌​​​‌​‌​​​‌‌​​​​​​‌​‌​‌​‌‌​‌‌​‌‌‌​‌​​​‌​‌​​‌⁠

Inspired by 3ds Max's Parameter Wiring dialog. Blender drivers are one-way, so there is no
two-way wire; everything else (parent, constraints, drivers with expressions) is native data.
"""

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy.types import Menu, Operator, Panel, PropertyGroup, UIList

from .actions import TRANSFORM_DEPENDENCIES, channel, set_parent, unlink, would_cycle
from .editor import (TREE_ID, SCHEMATIC_NT_scene, SchematicOperator, activate_tab, editor_tree, frame_after_draw, node_entity,
                     refresh, selected_objects, tab_for_scope)
from .graph import RELATIONS, Entity
from .props import Resolved, categories, make_spec, parse_spec, spec_label
from .wires import load_wires


CATEGORY = "Link"

LINK_CONSTRAINTS = [
    ("COPY_TRANSFORMS", "Copy Transforms", "Copy location, rotation and scale", "CON_TRANSLIKE", 0),
    ("COPY_LOCATION", "Copy Location", "Copy location", "CON_LOCLIKE", 1),
    ("COPY_ROTATION", "Copy Rotation", "Copy rotation", "CON_ROTLIKE", 2),
    ("COPY_SCALE", "Copy Scale", "Copy scale", "CON_SIZELIKE", 3),
    ("CHILD_OF", "Child Of", "Reversible parent", "CON_CHILDOF", 4),
    ("DAMPED_TRACK", "Damped Track", "Aim at the source", "CON_TRACKTO", 5),
    ("TRACK_TO", "Track To", "Aim at the source with an up axis", "CON_TRACKTO", 6),
    ("LOCKED_TRACK", "Locked Track", "Aim around one locked axis", "CON_LOCKTRACK", 7),
    ("STRETCH_TO", "Stretch To", "Stretch toward the source", "CON_STRETCHTO", 8),
    ("LIMIT_DISTANCE", "Limit Distance", "Keep a distance from the source", "CON_DISTLIMIT", 9),
    ("TRANSFORMATION", "Transformation", "Map the source's transform to the target's", "CON_TRANSFORM", 10),
    ("IK", "Inverse Kinematics", "Use the source as IK target (pose bones only)", "CON_KINEMATIC", 11),
]
SPACES = [
    ("WORLD", "World", "World space"),
    ("POSE", "Pose", "Armature space (bones only)"),
    ("LOCAL_WITH_PARENT", "Local with Parent", "Local space including the parent (bones only)"),
    ("LOCAL", "Local", "Local space"),
]
SOURCE_CHANNELS = [
    ("LOC_X", "Location X", ""), ("LOC_Y", "Location Y", ""), ("LOC_Z", "Location Z", ""),
    ("ROT_X", "Rotation X", ""), ("ROT_Y", "Rotation Y", ""), ("ROT_Z", "Rotation Z", ""),
    ("ROT_W", "Rotation W", ""),
    ("SCALE_X", "Scale X", ""), ("SCALE_Y", "Scale Y", ""), ("SCALE_Z", "Scale Z", ""),
    ("SCALE_AVG", "Scale Average", ""),
]
DRIVEN_CHANNELS = [
    ("location:0", "Location X", ""), ("location:1", "Location Y", ""), ("location:2", "Location Z", ""),
    ("rotation_euler:0", "Rotation X (Euler)", ""), ("rotation_euler:1", "Rotation Y (Euler)", ""),
    ("rotation_euler:2", "Rotation Z (Euler)", ""),
    ("rotation_quaternion:0", "Rotation W (Quaternion)", ""), ("rotation_quaternion:1", "Rotation X (Quaternion)", ""),
    ("rotation_quaternion:2", "Rotation Y (Quaternion)", ""), ("rotation_quaternion:3", "Rotation Z (Quaternion)", ""),
    ("scale:0", "Scale X", ""), ("scale:1", "Scale Y", ""), ("scale:2", "Scale Z", ""),
]
EXPRESSION_PRESETS = (
    ("var", "Same value"),
    ("-var", "Inverted"),
    ("var * 2", "Doubled"),
    ("var * 0.5", "Halved"),
    ("max(0, min(1, var))", "Clamp 0–1"),
    ("abs(var)", "Absolute"),
    ("1 - var", "Reverse 0–1"),
    ("var + 1", "Offset +1"),
)


class SCHEMATIC_PG_link_item(PropertyGroup):
    object_ref: PointerProperty(type=bpy.types.Object)
    bone: StringProperty()
    role: EnumProperty(items=[
        ("SOURCE", "Source", "Controls the others: parent, constraint target or driver input", "EXPORT", 0),
        ("TARGET", "Target", "Gets parented, constrained or driven", "IMPORT", 1),
    ], default="TARGET", update=lambda self, context: role_changed(self))


class SCHEMATIC_PG_link_override(PropertyGroup):
    spec: StringProperty()
    expression: StringProperty(name="Own Expression",
                               description="Formula for this target value only; empty = use the shared Expression")


class SCHEMATIC_PG_link_settings(PropertyGroup):
    items: CollectionProperty(type=SCHEMATIC_PG_link_item)
    overrides: CollectionProperty(type=SCHEMATIC_PG_link_override)
    offset_mode: EnumProperty(name="Offset", items=[
        ("RELATIVE", "Relative", "Targets keep where they are now and follow the source's change from here", "PIVOT_CURSOR", 0),
        ("ABSOLUTE", "Absolute", "Targets take the source's value as is (they may jump)", "PIVOT_BOUNDBOX", 1),
    ], default="RELATIVE")
    auto_units: BoolProperty(
        name="Auto Units", default=True,
        description="Angles in degrees (as shown in the UI), round() for whole-number targets, on/off for checkboxes",
    )
    use_remap: BoolProperty(name="Map Range", description="Map a source range onto a target range")
    from_min: FloatProperty(name="From Min", default=0.0)
    from_max: FloatProperty(name="From Max", default=1.0)
    to_min: FloatProperty(name="To Min", default=0.0)
    to_max: FloatProperty(name="To Max", default=1.0)
    remap_clamp: BoolProperty(name="Clamp", default=True, description="Stay inside the To range")
    active_index: IntProperty()
    action: EnumProperty(name="Link", items=[
        ("PARENT", "Parent", "Parent every target to the source", "LINKED", 0),
        ("CONSTRAINT", "Constraint", "Add a constraint on every target that points at the source", "CONSTRAINT", 1),
        ("DRIVER", "Driver", "Drive a channel on every target from a channel of the source", "DRIVER", 2),
    ], default="DRIVER")
    keep_transform: BoolProperty(name="Keep Transform", default=True)

    constraint_type: EnumProperty(name="Type", items=LINK_CONSTRAINTS, default="COPY_TRANSFORMS")
    influence: FloatProperty(name="Influence", default=1.0, min=0.0, max=1.0, subtype="FACTOR")
    target_space: EnumProperty(name="Source Space", items=SPACES, default="WORLD")
    owner_space: EnumProperty(name="Target Space", items=SPACES, default="WORLD")

    source_mode: EnumProperty(name="Read", items=[
        ("PICKED", "Browse", "Pick any value of the source in the Source Properties list below"),
        ("TRANSFORM", "Transform", "Read a location, rotation or scale channel, with a choice of space"),
        ("PROPERTY", "Path", 'Type a custom property such as ["ik_fk"] or any property path'),
    ], default="PICKED")
    source_spec: StringProperty()
    target_spec: StringProperty()
    source_filter: StringProperty(name="Search", options={"TEXTEDIT_UPDATE"})
    target_filter: StringProperty(name="Search", options={"TEXTEDIT_UPDATE"})
    expanded: StringProperty(default="S:transform;T:transform")
    browser_layout: EnumProperty(name="Layout", items=[
        ("SIDE", "Side by Side", "Source values on the left, target values on the right", "SPLIT_HORIZONTAL", 0),
        ("STACKED", "Stacked", "Source values above target values", "SPLIT_VERTICAL", 1),
    ], default="SIDE")
    show_values: BoolProperty(name="Values", default=True, description="Show the current values next to each entry")
    link_vector: BoolProperty(
        name="All Components",
        description="When both picks are vectors or colors, link X→X, Y→Y, Z→Z (R→R, G→G …) in one go",
    )
    chain: BoolProperty(
        name="Chain",
        description="Pass the value down the list: Source → 1st target → 2nd target …, "
                    "each target reads the one before it",
    )
    source_channel: EnumProperty(name="Channel", items=SOURCE_CHANNELS, default="LOC_X")
    source_space: EnumProperty(name="Space", items=[
        ("LOCAL_SPACE", "Local", "Relative to the rest pose / parent, best for controllers"),
        ("TRANSFORM_SPACE", "Transform", "The channel values shown in the N panel"),
        ("WORLD_SPACE", "World", "World space"),
    ], default="LOCAL_SPACE")
    source_path: StringProperty(name="Property", default='["value"]',
                                description='Custom property like ["ik_fk"], or a path like location')
    source_index: IntProperty(name="Index", min=0)

    driven_mode: EnumProperty(name="Drive", items=[
        ("PICKED", "Browse", "Pick any value of the target in the Target Properties list below"),
        ("TRANSFORM", "Transform", "Drive a location, rotation or scale channel"),
        ("PROPERTY", "Path", "Type a custom property or any numeric property path"),
        ("SHAPE_KEY", "Shape Key", "Drive a shape key value on a mesh target"),
    ], default="PICKED")
    driven_channel: EnumProperty(name="Channel", items=DRIVEN_CHANNELS, default="location:0")
    driven_path: StringProperty(name="Property", default='["value"]')
    driven_index: IntProperty(name="Index", min=0)
    shape_key: StringProperty(name="Shape Key")
    expression: StringProperty(name="Expression", default="var",
                               description="Python expression; the source value is called var")
    replace_existing: BoolProperty(name="Replace Existing Driver",
                                   description="Overwrite a driver that is already on the target channel")


def settings(tree):
    return tree.link if tree else None


def item_entity(item):
    obj = item.object_ref
    if not obj:
        return None
    entity = Entity(obj, item.bone if obj.pose else "")
    return entity if entity.owner is not None else None


def split_roles(tree):
    sources, targets = [], []
    for item in settings(tree).items:
        entity = item_entity(item)
        if entity:
            (sources if item.role == "SOURCE" else targets).append(entity)
    return sources, targets


def current_selection(context, tree=None):
    """Selected nodes in the graph, else selected objects / pose bones in the viewport. Active last."""
    if tree:
        from .selection import graph_selection
        picked = graph_selection(tree)
        if picked:
            return picked
    layer = context.view_layer
    active = layer.objects.active
    entities = []
    for obj in layer.objects:
        if not obj.select_get(view_layer=layer) and obj != active:
            continue
        if obj.mode == "POSE" and obj.pose:
            bones = [bone for bone in obj.pose.bones if bone.select]
            active_bone = obj.data.bones.active
            bones.sort(key=lambda bone: active_bone is not None and bone.name == active_bone.name)
            entities.extend(Entity(obj, bone.name) for bone in bones)
            if bones:
                continue
        if obj.select_get(view_layer=layer):
            entities.append(Entity(obj))
    entities.sort(key=lambda entity: entity.obj == active)
    return entities


_ordering = False


def ensure_source_first(link):
    """Exactly one source, always at the top of the list (targets keep their order)."""
    global _ordering
    if _ordering:
        return
    _ordering = True
    try:
        sources = [i for i, item in enumerate(link.items) if item.role == "SOURCE"]
        for i in sources[1:]:
            link.items[i].role = "TARGET"
        if sources and sources[0] > 0:
            link.items.move(sources[0], 0)
            link.active_index = 0
    finally:
        _ordering = False


def role_changed(item):
    """Picking Source in a row's dropdown makes it the only source and moves it to the top."""
    global _ordering
    if _ordering:
        return
    link = item.id_data.link
    if item.role != "SOURCE":
        return
    _ordering = True
    try:
        chosen = item.as_pointer()
        for other in link.items:
            if other.as_pointer() != chosen and other.role == "SOURCE":
                other.role = "TARGET"
    finally:
        _ordering = False
    ensure_source_first(link)


def load_items(tree, entities):
    """Fill the workbench; the active (last) item becomes the source, like Blender's Ctrl+P."""
    link = settings(tree)
    link.items.clear()
    for index, entity in enumerate(entities):
        item = link.items.add()
        item.object_ref, item.bone = entity.obj, entity.bone
        item.role = "SOURCE" if index == len(entities) - 1 and len(entities) > 1 else "TARGET"
    ensure_source_first(link)
    link.active_index = 0


def show_link_tab(area):
    for region in area.regions:
        if region.type == "UI":
            try:
                region.active_panel_category = CATEGORY
            except (TypeError, ValueError):
                pass


# ---------------------------------------------------------------- applying links

def value_kind(owner, prop, value):
    """(unit, type) of a property: unit is ROTATION / LENGTH / NONE, type is FLOAT / INT / BOOLEAN."""
    rna = None if prop.startswith("[") else owner.bl_rna.properties.get(prop)
    if rna is not None:
        return rna.unit, rna.type
    if isinstance(value, bool):
        return "NONE", "BOOLEAN"
    if isinstance(value, int):
        return "NONE", "INT"
    return "NONE", "FLOAT"


def read_value(owner, prop, index):
    value = owner[prop[2:-2]] if prop.startswith("[") else owner.path_resolve(prop)
    if index >= 0 and hasattr(value, "__len__"):
        value = value[index]
    return float(value)


class Slot:
    """A driven channel: owner struct, property name and array index (-1 for scalars)."""

    def __init__(self, owner, prop, index, transform=False, spec=""):
        self.owner, self.prop, self.index, self.transform, self.spec = owner, prop, index, transform, spec
        raw = owner[prop[2:-2]] if prop.startswith("[") else owner.path_resolve(prop)
        sample = raw[max(index, 0)] if hasattr(raw, "__len__") else raw
        self.unit, self.type = value_kind(owner, prop, sample)

    @property
    def value(self):
        return read_value(self.owner, self.prop, self.index)

    @value.setter
    def value(self, new):
        if self.type == "INT":
            new = int(round(new))
        elif self.type == "BOOLEAN":
            new = bool(new)
        if self.prop.startswith("["):
            key = self.prop[2:-2]
            if self.index >= 0:
                self.owner[key][self.index] = new
            else:
                self.owner[key] = new
        elif self.index >= 0:
            getattr(self.owner, self.prop)[self.index] = new
        else:
            setattr(self.owner, self.prop, new)


def target_specs(link):
    """Target values picked in Browse mode (several may be ticked)."""
    return [spec for spec in link.target_spec.split("\n") if spec]


def override_for(link, spec):
    for item in link.overrides:
        if item.spec == spec:
            return item
    return None


def driven_slot(target, link, spec=None):
    if link.driven_mode == "PICKED":
        if not spec:
            raise ValueError("Pick a target value in Target Properties")
        resolved = Resolved(target, spec)
        return Slot(resolved.owner, resolved.prop, resolved.index if resolved.length else -1,
                    resolved.is_transform, spec)
    if link.driven_mode == "SHAPE_KEY":
        keys = getattr(target.obj.data, "shape_keys", None) if not target.bone else None
        block = keys.key_blocks.get(link.shape_key) if keys else None
        if block is None:
            raise ValueError(f"{target.name} has no shape key named {link.shape_key!r}")
        return Slot(block, "value", -1)
    if link.driven_mode == "TRANSFORM":
        path, index = link.driven_channel.split(":")
        return Slot(target.owner, path, int(index), True)
    path, index = channel(target.owner, link.driven_path, link.driven_index, writable=True)
    return Slot(target.owner, path, index)


def id_type_of(block):
    return getattr(block, "id_type", None) or type(block).__name__.upper()


class Reader:
    """How a driver variable reads the source: a picked value (spec) or the mode's own setting."""

    def __init__(self, source, link, spec=None):
        self.transform = False
        self.source = source
        if spec is None and link.source_mode == "TRANSFORM":
            self.kind = "TRANSFORMS"
            self.id, self.bone = source.obj, source.bone
            self.channel, self.space = link.source_channel, link.source_space
            self.transform = True
            self.unit = "ROTATION" if self.channel.startswith("ROT") and self.channel != "ROT_W" else (
                "LENGTH" if self.channel.startswith("LOC") else "NONE")
            self.type = "FLOAT"
            return
        self.kind = "SINGLE_PROP"
        if spec is not None:
            resolved = Resolved(source, spec)
            self.id, self.path = resolved.id, resolved.driver_path
            self.transform = resolved.is_transform
            owner, prop = resolved.owner, resolved.prop
        elif link.source_mode == "PICKED":
            raise ValueError("Pick a source value in Source Properties")
        else:
            prop, index = channel(source.owner, link.source_path, link.source_index)
            owner = source.owner
            self.id = source.obj
            self.path = source.owner.path_from_id(prop) if source.bone else prop
            if index >= 0:
                self.path += f"[{index}]"
        sample = self.id.path_resolve(self.path)
        self.unit, self.type = value_kind(owner, prop, sample)

    @property
    def value(self):
        """The value the driver variable reads right now (for relative offsets and Copy Once)."""
        if self.kind == "SINGLE_PROP":
            return float(self.id.path_resolve(self.path))
        owner = self.source.owner
        if self.space == "WORLD_SPACE":
            matrix = self.id.matrix_world @ owner.matrix if self.bone else owner.matrix_world
        else:
            matrix = owner.matrix_basis
        location, rotation, scale = matrix.decompose()
        kind, axis = self.channel.split("_")
        if kind == "LOC":
            return location["XYZ".index(axis)]
        if kind == "SCALE":
            return sum(scale) / 3 if axis == "AVG" else scale["XYZ".index(axis)]
        if axis == "W":
            return rotation.w
        return rotation.to_euler()["XYZ".index(axis)]

    def apply(self, variable):
        if self.kind == "TRANSFORMS":
            variable.type = "TRANSFORMS"
            slot = variable.targets[0]
            slot.id = self.id
            slot.bone_target = self.bone
            slot.transform_type = self.channel
            slot.transform_space = self.space
        else:
            variable.type = "SINGLE_PROP"
            slot = variable.targets[0]
            slot.id_type = id_type_of(self.id)
            slot.id = self.id
            slot.data_path = self.path


def number(value):
    """Short, exact-enough literal for an expression."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in {"-0", ""} else "0"


def build_expression(link, reader, slot, user_expression):
    """The driver expression for one job: units, Map Range, the user's formula, int / bool targets."""
    import re
    value = "var"
    if link.auto_units and reader.unit == "ROTATION":
        value = "degrees(var)"  # Angles are typed and mapped in degrees, as the UI shows them.
    if link.use_remap:
        span = link.from_max - link.from_min
        if abs(span) < 1e-12:
            raise ValueError("Map Range: From Min and From Max must differ")
        scale = (link.to_max - link.to_min) / span
        value = f"{number(link.to_min)} + ({value} - {number(link.from_min)}) * {number(scale)}"
        if link.remap_clamp:
            low, high = sorted((link.to_min, link.to_max))
            value = f"min(max({value}, {number(low)}), {number(high)})"
    body = (user_expression or "").strip() or "var"
    if value != "var":
        body = re.sub(r"\bvar\b", f"({value})", body)
    if link.auto_units:
        if slot.unit == "ROTATION":
            body = f"radians({body})"
        if slot.type == "INT":
            body = f"round({body})"
        elif slot.type == "BOOLEAN":
            body = f"({body}) > 0.5"
    return body


def evaluate(expression, value):
    """Evaluate a driver-style expression once (relative offsets, Copy Once)."""
    import math
    names = {name: getattr(math, name) for name in dir(math) if not name.startswith("_")}
    names.update(abs=abs, min=min, max=max, round=round, int=int, float=float, bool=bool, var=value,
                 clamp=lambda x, low=0.0, high=1.0: min(max(x, low), high),
                 lerp=lambda a, b, t: a + (b - a) * t)
    return eval(compile(expression.strip() or "var", "<expression>", "eval"), {"__builtins__": {}}, names)


def final_expression(link, reader, slot, user_expression):
    """Absolute: the formula as is. Relative: offset so the target keeps its current value."""
    body = build_expression(link, reader, slot, user_expression)
    if link.offset_mode != "RELATIVE" or slot.type == "BOOLEAN":
        return body
    try:
        at_rest = float(evaluate(body, reader.value))
    except Exception as error:
        raise ValueError(f"cannot work out the relative offset ({error}); use Absolute") from None
    keep = slot.value
    if abs(keep - at_rest) < 1e-7:
        return body
    return f"{number(keep)} + ({body}) - {number(at_rest)}"


def existing_driver(owner, path, index):
    animation = owner.id_data.animation_data
    if not animation:
        return None
    return animation.drivers.find(owner.path_from_id(path), index=max(index, 0))


def write_driver(reader, slot, expression, replace, label):
    owner, path, index = slot.owner, slot.prop, slot.index
    if existing_driver(owner, path, index):
        if not replace:
            raise ValueError(f"{label}: {path} already has a driver (tick Replace Existing Driver)")
        owner.driver_remove(path, index)
    if reader.kind == "SINGLE_PROP" and reader.id == owner.id_data and reader.path == (
            owner.path_from_id(path) + (f"[{index}]" if index >= 0 else "")):
        raise ValueError("The source and driven channel are the same")
    # Custom properties set from Python do not tag the depsgraph, so the evaluated source can
    # lack them and the new driver would be flagged invalid. Tag it first.
    reader.id.update_tag()
    curve = owner.driver_add(path, index)
    try:
        driver = curve.driver
        driver.type = "SCRIPTED"
        driver.expression = expression
        while driver.variables:
            driver.variables.remove(driver.variables[0])
        variable = driver.variables.new()
        variable.name = "var"
        reader.apply(variable)
        driver.is_valid = True
        owner.id_data.update_tag()
    except Exception:
        owner.driver_remove(path, index)
        raise


def component(spec, index):
    root, path, _ = parse_spec(spec)
    return make_spec(root, path, index)


def whole_length(entity, spec):
    """Number of components when the spec picks a whole vector / colour, else 0."""
    if not spec:
        return 0
    resolved = Resolved(entity, spec)
    return resolved.length if resolved.length and resolved.index < 0 else 0


def spec_pairs(link, source, target, source_spec, target_spec):
    """Expand one source pick and one target pick into per-component (source, target) specs."""
    source_whole = whole_length(source, source_spec)
    target_whole = whole_length(target, target_spec) if target_spec else 0
    if source_whole and target_whole:
        return [(component(source_spec, i), component(target_spec, i))
                for i in range(min(source_whole, target_whole))]
    if source_whole:
        raise ValueError("A whole vector source needs a whole vector target; or pick one component")
    if target_whole:
        # One value drives every component, e.g. Location X → Scale X, Y and Z.
        return [(source_spec, component(target_spec, i)) for i in range(target_whole)]
    if link.link_vector and source_spec and target_spec:
        source_length = Resolved(source, source_spec).length
        target_length = Resolved(target, target_spec).length
        if source_length and target_length:
            return [(component(source_spec, i), component(target_spec, i))
                    for i in range(min(source_length, target_length))]
    return [(source_spec, target_spec)]


def hop_source_spec(link, previous):
    """In a chain every hop after the first reads the value the previous target received."""
    if previous is not None:
        picks = target_specs(link)
        return picks[0] if picks else None
    return link.source_spec if link.source_mode == "PICKED" else None


def driver_jobs(link, source, target, previous=None):
    """(reader, slot, expression) for every driver one source → target hop creates."""
    source_spec = hop_source_spec(link, previous)
    if link.source_mode == "PICKED" and previous is None and not source_spec:
        raise ValueError("Pick a source value in Source Properties")
    picks = target_specs(link) if link.driven_mode == "PICKED" else [None]
    if not picks:
        raise ValueError("Pick at least one target value in Target Properties")
    jobs = []
    for target_spec in picks:
        override = override_for(link, target_spec) if target_spec else None
        expression = override.expression if override and override.expression.strip() else link.expression
        for source_part, target_part in spec_pairs(link, source, target, source_spec, target_spec):
            jobs.append((Reader(source, link, source_part), driven_slot(target, link, target_part), expression))
    return jobs


def add_link_driver(source, target, link, previous=None):
    # Work out every expression first, so a relative offset reads values before any driver exists.
    planned = [(reader, slot, final_expression(link, reader, slot, expression))
               for reader, slot, expression in driver_jobs(link, source, target, previous)]
    for reader, slot, expression in planned:
        write_driver(reader, slot, expression, link.replace_existing, target.name)
    target.obj.update_tag()


def copy_values(context, tree):
    """Write the formula's result into each target once, without creating drivers (always absolute)."""
    link = settings(tree)
    sources, targets = split_roles(tree)
    if len(sources) != 1 or not targets:
        raise ValueError("Mark one Source and at least one Target")
    done, errors = 0, []
    for source, target, previous in hop_pairs(link, sources[0], targets):
        try:
            for reader, slot, expression in driver_jobs(link, source, target, previous):
                slot.value = evaluate(build_expression(link, reader, slot, expression), reader.value)
            target.obj.update_tag()
            context.view_layer.update()
            done += 1
        except Exception as error:  # Expressions can raise anything.
            errors.append(f"{target.name}: {error}")
    context.view_layer.update()
    return done, errors


def hop_pairs(link, source, targets):
    """[(source, target, previous_target_or_None)] — chained or fanned out from one source."""
    if not link.chain:
        return [(source, target, None) for target in targets]
    order = [source, *targets]
    return [(order[i], order[i + 1], order[i] if i else None) for i in range(len(targets))]


RELATIVE_CONSTRAINTS = {
    # Settings that make the constraint add to the target's own transform instead of replacing it.
    "COPY_LOCATION": {"use_offset": True},
    "COPY_ROTATION": {"mix_mode": "AFTER"},
    "COPY_SCALE": {"use_offset": True},
    "COPY_TRANSFORMS": {"mix_mode": "AFTER_FULL"},
}


def world_matrix(entity):
    if entity.bone:
        return entity.obj.matrix_world @ entity.owner.matrix
    return entity.obj.matrix_world.copy()


def keep_in_place(context, entity, goal, attempts=4):
    """Adjust the target's own transform until it sits where it was before the link."""
    for _ in range(attempts):
        context.view_layer.update()
        current = world_matrix(entity)
        if all(abs(a - b) < 1e-5 for row_a, row_b in zip(current, goal) for a, b in zip(row_a, row_b)):
            return True
        owner = entity.owner
        owner.matrix_basis = owner.matrix_basis @ (current.inverted_safe() @ goal)
    context.view_layer.update()
    return False


def add_link_constraint(context, source, target, link):
    if link.constraint_type == "IK" and not target.bone:
        raise ValueError("IK needs a pose bone as the target")
    relative = link.offset_mode == "RELATIVE"
    context.view_layer.update()
    goal = world_matrix(target)
    constraints = target.owner.constraints
    constraint = constraints.new(link.constraint_type)
    try:
        constraint.target = source.obj
        if source.bone:
            constraint.subtarget = source.bone
        constraint.influence = link.influence
        bone_spaces = {"POSE", "LOCAL_WITH_PARENT"}
        if hasattr(constraint, "target_space"):
            if source.bone or link.target_space not in bone_spaces:
                constraint.target_space = link.target_space
        if hasattr(constraint, "owner_space"):
            if target.bone or link.owner_space not in bone_spaces:
                constraint.owner_space = link.owner_space
        if link.constraint_type == "CHILD_OF" and relative:
            constraint.set_inverse_pending = True
        if relative:
            for name, value in RELATIVE_CONSTRAINTS.get(link.constraint_type, {}).items():
                setattr(constraint, name, value)
    except Exception:
        constraints.remove(constraint)
        raise
    if relative and link.constraint_type in RELATIVE_CONSTRAINTS:
        if not keep_in_place(context, target, goal):
            return "kept close to its position (spaces or parents make it approximate)"
    return ""


def apply_link(context, tree):
    """Apply the workbench action from the one source to every target. Returns (done, errors)."""
    link = settings(tree)
    sources, targets = split_roles(tree)
    if len(sources) != 1:
        raise ValueError("Mark exactly one item as Source")
    if not targets:
        raise ValueError("Mark at least one item as Target")
    source = sources[0]
    if link.chain and link.action == "DRIVER" and link.driven_mode != "PICKED":
        raise ValueError("Chain needs Browse on the target side")
    done, errors = 0, []
    for source, target, previous in hop_pairs(link, source, targets) if link.action == "DRIVER" else [
            (source, target, None) for target in targets]:
        try:
            if target == source:
                raise ValueError("Source and target are the same")
            if not target.obj.is_editable:
                raise ValueError("read-only")
            if link.action == "DRIVER":
                transform_link = any(reader.transform and slot.transform
                                     for reader, slot, _expression in driver_jobs(link, source, target, previous))
            else:
                transform_link = True
            if transform_link and would_cycle(tree.scene, source, target, TRANSFORM_DEPENDENCIES):
                raise ValueError("would create a circular dependency")
            if link.action == "PARENT":
                if target.bone and (not source.bone or source.obj != target.obj):
                    raise ValueError("a bone can only be parented to a bone in its own armature; use Child Of")
                if not target.bone and link.offset_mode == "ABSOLUTE":
                    target.obj.parent = source.obj
                    target.obj.parent_type = "BONE" if source.bone else "OBJECT"
                    target.obj.parent_bone = source.bone
                    target.obj.matrix_parent_inverse.identity()
                else:
                    set_parent(context, source, target)
            elif link.action == "CONSTRAINT":
                note = add_link_constraint(context, source, target, link)
                if note:
                    errors.append(f"ℹ {target.name}: {note}")
            else:
                add_link_driver(source, target, link, previous)
            done += 1
        except (RuntimeError, TypeError, ValueError, AttributeError, KeyError) as error:
            errors.append(f"{target.name}: {error}")
    context.view_layer.update()
    return done, errors


def links_between(tree):
    """Visible wires whose both ends are workbench items: [(wire index, record)]."""
    keys = {(item.object_ref, item.object_ref.pose and item.bone or "")
            for item in settings(tree).items if item.object_ref}
    found = []
    for index, record in enumerate(load_wires(tree)):
        ends = [node_entity(tree.nodes.get(name)) for name in record[:2]]
        if all(ends) and all(entity.key in keys for entity in ends):
            found.append((index, record))
    return found


# ---------------------------------------------------------------- operators

class SCHEMATIC_OT_link_load(SchematicOperator, Operator):
    bl_idname = "schematic.link_load"
    bl_label = "Load Selection"
    bl_description = "Put the selected nodes, objects or pose bones on the workbench (active = source)"
    bl_options = {"UNDO"}

    def execute(self, context):
        tree = editor_tree(context)
        entities = current_selection(context, tree)
        if not entities:
            self.report({"WARNING"}, "Select objects or bones first")
            return {"CANCELLED"}
        load_items(tree, entities)
        return {"FINISHED"}


class SCHEMATIC_OT_link_item(SchematicOperator, Operator):
    bl_idname = "schematic.link_item"
    bl_label = "Workbench Item"
    bl_options = {"UNDO"}

    action: EnumProperty(items=[
        ("REMOVE", "Remove", "Take this item off the workbench"),
        ("MAKE_SOURCE", "Make Source", "Use this item as the source; the others become targets"),
        ("SWAP", "Swap Source", "Make the selected target (or the first target) the source"),
        ("CLEAR", "Clear", "Empty the workbench"),
        ("UP", "Move Up", "Move this target up (order matters for Chain)"),
        ("DOWN", "Move Down", "Move this target down (order matters for Chain)"),
    ])
    index: IntProperty(default=-1)

    def execute(self, context):
        link = settings(editor_tree(context))
        items = link.items
        has_source = bool(len(items)) and items[0].role == "SOURCE"
        if self.action == "CLEAR":
            items.clear()
        elif self.action == "SWAP":
            targets = [i for i, item in enumerate(items) if item.role == "TARGET"]
            if not targets:
                return {"CANCELLED"}
            pick = link.active_index if link.active_index in targets else targets[0]
            items[pick].role = "SOURCE"
        elif 0 <= self.index < len(items):
            if self.action in {"UP", "DOWN"}:
                other = self.index + (-1 if self.action == "UP" else 1)
                top = 1 if has_source else 0  # The source stays on top.
                if self.index >= top and top <= other < len(items):
                    items.move(self.index, other)
                    link.active_index = other
            elif self.action == "REMOVE":
                items.remove(self.index)
                link.active_index = max(0, min(link.active_index, len(items) - 1))
            else:
                items[self.index].role = "SOURCE"
        ensure_source_first(link)
        return {"FINISHED"}


class SCHEMATIC_OT_link_apply(SchematicOperator, Operator):
    bl_idname = "schematic.link_apply"
    bl_label = "Apply Link"
    bl_description = "Create the chosen relationship from the source to every target"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = editor_tree(context)
        if context.mode not in {"OBJECT", "POSE"}:
            self.report({"ERROR"}, "Link in Object or Pose Mode")
            return {"CANCELLED"}
        try:
            done, errors = apply_link(context, tree)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        refresh(tree)
        for message in errors[:5]:
            self.report({"WARNING"}, message)
        if not done:
            return {"CANCELLED"}
        skipped = len([message for message in errors if not message.startswith("ℹ")])
        self.report({"INFO"}, f"Linked {done} target(s)" + (f", {skipped} skipped" if skipped else ""))
        return {"FINISHED"}


class SCHEMATIC_OT_link_unlink(SchematicOperator, Operator):
    bl_idname = "schematic.link_unlink"
    bl_label = "Unlink"
    bl_description = "Remove this relationship"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty()

    def execute(self, context):
        tree = editor_tree(context)
        wires = load_wires(tree)
        if not 0 <= self.index < len(wires):
            return {"CANCELLED"}
        record = wires[self.index]
        source, target = (node_entity(tree.nodes.get(name)) for name in record[:2])
        try:
            message = unlink(context, source, target, record[2], record[4])
        except (RuntimeError, TypeError, ValueError, AttributeError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        refresh(tree)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class SCHEMATIC_OT_link_pick(SchematicOperator, Operator):
    bl_idname = "schematic.link_pick"
    bl_label = "Pick Property"
    bl_description = "Use this value"
    bl_options = {"UNDO"}

    side: StringProperty()
    spec: StringProperty()

    def execute(self, context):
        link = settings(editor_tree(context))
        if self.side == "S":
            link.source_spec = "" if link.source_spec == self.spec else self.spec
        elif self.spec == "*CLEAR*":
            link.target_spec = ""
            link.overrides.clear()
        else:
            # Targets are a multi-pick: click to tick, click again to untick.
            picks = target_specs(link)
            if self.spec in picks:
                picks.remove(self.spec)
            else:
                picks.append(self.spec)
            link.target_spec = "\n".join(picks)
            for index in reversed(range(len(link.overrides))):
                if link.overrides[index].spec not in picks:
                    link.overrides.remove(index)
            for spec in picks:
                if override_for(link, spec) is None:
                    link.overrides.add().spec = spec
        return {"FINISHED"}


class SCHEMATIC_OT_link_expand(SchematicOperator, Operator):
    bl_idname = "schematic.link_expand"
    bl_label = "Expand Category"
    bl_description = "Show or hide this group of values"

    key: StringProperty()

    def execute(self, context):
        link = settings(editor_tree(context))
        keys = set(filter(None, link.expanded.split(";")))
        keys ^= {self.key}
        link.expanded = ";".join(sorted(keys))
        return {"FINISHED"}


def natural_key(text):
    import re
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]


def hierarchy_depth(entity):
    depth = 0
    if entity.bone:
        parent = entity.owner.parent
        while parent:
            depth += 1
            parent = parent.parent
        owner = entity.obj
        depth += 1
    else:
        owner = entity.obj
    parent = owner.parent
    while parent:
        depth += 1
        parent = parent.parent
    return depth


class SCHEMATIC_OT_link_sort(SchematicOperator, Operator):
    bl_idname = "schematic.link_sort"
    bl_label = "Sort Items"
    bl_description = "Sort the workbench list (the order is used by Chain)"
    bl_options = {"UNDO"}

    mode: EnumProperty(items=[
        ("NAME", "By Name", "Cube, Cube.001, Cube.002 … (numbers in natural order)"),
        ("HIERARCHY", "By Hierarchy", "Parents before children, then by name"),
        ("REVERSE", "Reverse", "Reverse the current order"),
    ])

    def execute(self, context):
        link = settings(editor_tree(context))
        records = [(item.object_ref, item.bone, item.role) for item in link.items]
        if self.mode == "REVERSE":
            records.reverse()
            records.sort(key=lambda record: record[2] != "SOURCE")
        else:
            def key(record):
                obj, bone, _role = record
                name = (natural_key(obj.name if obj else ""), natural_key(bone))
                if self.mode == "HIERARCHY" and obj:
                    entity = Entity(obj, bone if obj.pose else "")
                    depth = hierarchy_depth(entity) if entity.owner is not None else 0
                    return (depth, name)
                return (0, name)
            records.sort(key=key)
        # The source always stays first; only targets are reordered.
        records.sort(key=lambda record: record[2] != "SOURCE")
        for item, (obj, bone, role) in zip(link.items, records):
            item.object_ref, item.bone, item.role = obj, bone, role
        return {"FINISHED"}


class SCHEMATIC_MT_link_sort(Menu):
    bl_idname = "SCHEMATIC_MT_link_sort"
    bl_label = "Sort"

    def draw(self, context):
        self.layout.operator("schematic.link_sort", text="By Name", icon="SORTALPHA").mode = "NAME"
        self.layout.operator("schematic.link_sort", text="By Hierarchy", icon="OUTLINER").mode = "HIERARCHY"
        self.layout.operator("schematic.link_sort", text="Reverse", icon="ARROW_LEFTRIGHT").mode = "REVERSE"


class SCHEMATIC_OT_link_copy(SchematicOperator, Operator):
    bl_idname = "schematic.link_copy"
    bl_label = "Copy Value Once"
    bl_description = "Write the expression result into the targets now, without a live driver"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = editor_tree(context)
        try:
            done, errors = copy_values(context, tree)
        except ValueError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        for message in errors[:5]:
            self.report({"WARNING"}, message)
        if not done:
            return {"CANCELLED"}
        self.report({"INFO"}, f"Copied to {done} target(s)")
        return {"FINISHED"}


class SCHEMATIC_OT_open_link(Operator):
    bl_idname = "schematic.open_link"
    bl_label = "Schematic Link Workbench"
    bl_description = "Open the Link Workbench for the selected objects or bones (reopens a closed one)"

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode in {"OBJECT", "POSE"}

    def execute(self, context):
        tree = editor_tree(context)
        entities = current_selection(context, tree)
        if not entities:
            self.report({"WARNING"}, "Select objects or bones first")
            return {"CANCELLED"}
        if tree and tree.scene == context.scene:
            load_items(tree, entities)
            show_link_tab(context.area)
            return {"FINISHED"}
        result = bpy.ops.schematic.open()
        if result != {"FINISHED"}:
            return result
        # schematic.open shows (and reuses) the tab for these objects in its floating window.
        anchors = selected_objects(context) or list(dict.fromkeys(entity.obj for entity in entities))
        tree = tab_for_scope(context.scene, anchors)
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                space = area.spaces.active
                if area.type == "NODE_EDITOR" and space.tree_type == TREE_ID:
                    if space.node_tree != tree and window.parent:
                        activate_tab(tree, window, area)
                        frame_after_draw(context, tree, area, window)
                    if space.node_tree == tree:
                        load_items(tree, entities)
                        show_link_tab(area)
                        area.tag_redraw()
                        return {"FINISHED"}
        load_items(tree, entities)
        return {"FINISHED"}


# ---------------------------------------------------------------- UI

class SCHEMATIC_UL_link_items(UIList):
    bl_idname = "SCHEMATIC_UL_link_items"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        entity = item_entity(item)
        row = layout.row(align=True)
        is_source = item.role == "SOURCE"
        if entity is None:
            row.label(text="(missing)", icon="ERROR")
        else:
            text = f"{entity.bone}  ·  {entity.obj.name}" if entity.bone else entity.obj.name
            row.label(text=text, icon="BONE_DATA" if entity.bone else "OBJECT_DATA")
        row.prop(item, "role", text="", icon_only=False, emboss=True)
        if is_source:
            row.label(text="", icon="BLANK1")
            row.label(text="", icon="BLANK1")
        else:
            op = row.operator("schematic.link_item", text="", icon="TRIA_UP", emboss=False)
            op.action, op.index = "UP", index
            op = row.operator("schematic.link_item", text="", icon="TRIA_DOWN", emboss=False)
            op.action, op.index = "DOWN", index
        op = row.operator("schematic.link_item", text="", icon="X", emboss=False)
        op.action, op.index = "REMOVE", index


class SCHEMATIC_MT_link_expressions(Menu):
    bl_idname = "SCHEMATIC_MT_link_expressions"
    bl_label = "Expression Presets"

    def draw(self, context):
        for expression, label in EXPRESSION_PRESETS:
            op = self.layout.operator("wm.context_set_string", text=f"{label}   {expression}")
            op.data_path, op.value = "space_data.edit_tree.link.expression", expression


class LinkPanel:
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = CATEGORY

    @classmethod
    def poll(cls, context):
        tree = editor_tree(context)
        return bool(tree and tree.scene == context.scene)


class SCHEMATIC_PT_link(LinkPanel, Panel):
    bl_idname = "SCHEMATIC_PT_link"
    bl_label = "Link Workbench"

    def draw(self, context):
        tree = editor_tree(context)
        link = settings(tree)
        layout = self.layout
        row = layout.row(align=True)
        row.operator("schematic.link_load", icon="RESTRICT_SELECT_OFF")
        row.menu("SCHEMATIC_MT_link_sort", text="", icon="SORTSIZE")
        row.operator("schematic.link_item", text="", icon="ARROW_LEFTRIGHT").action = "SWAP"
        row.operator("schematic.link_item", text="", icon="TRASH").action = "CLEAR"
        layout.template_list("SCHEMATIC_UL_link_items", "", link, "items", link, "active_index", rows=4)
        sources, targets = split_roles(tree)
        if len(sources) != 1:
            layout.label(text="Mark exactly one Source", icon="ERROR")
        elif not targets:
            layout.label(text="Mark at least one Target", icon="ERROR")
        else:
            layout.label(text=f"{sources[0].name}  →  {len(targets)} target(s)", icon="FORWARD")

        layout.row().prop(link, "action", expand=True)
        row = layout.row(align=True)
        row.prop(link, "offset_mode", expand=True)
        box = layout.box()
        if link.action == "PARENT":
            box.label(text="Relative keeps targets where they are" if link.offset_mode == "RELATIVE"
                      else "Absolute snaps targets onto the parent", icon="INFO")
            box.label(text="Bones parent within their own armature", icon="BONE_DATA")
        elif link.action == "CONSTRAINT":
            box.prop(link, "constraint_type")
            if link.offset_mode == "RELATIVE" and link.constraint_type not in {*RELATIVE_CONSTRAINTS, "CHILD_OF"}:
                box.label(text="Relative has no effect on this constraint", icon="INFO")
            box.prop(link, "influence")
            box.prop(link, "target_space")
            box.prop(link, "owner_space")
        else:
            self.draw_driver(box, link, sources, targets)
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("schematic.link_apply", text=f"Link {len(targets)} Target(s)", icon="CHECKMARK")
        if link.action == "DRIVER" and link.source_mode == "PICKED" and link.driven_mode == "PICKED":
            row.operator("schematic.link_copy", text="Copy Once", icon="COPYDOWN")
        if link.action == "DRIVER" and "PICKED" in {link.source_mode, link.driven_mode}:
            row = layout.row(align=True)
            row.prop(link, "browser_layout", expand=True)
            row.prop(link, "show_values", toggle=True, icon="HIDE_OFF" if link.show_values else "HIDE_ON")
            if link.browser_layout == "SIDE":
                split = layout.split(factor=0.5, align=False)
                left, right = split.column(), split.column()
                if link.source_mode == "PICKED":
                    draw_browser(left.box(), link, "S", (sources or [None])[0])
                else:
                    left.label(text="Source: not Browse")
                if link.driven_mode == "PICKED":
                    draw_browser(right.box(), link, "T", (targets or [None])[0])
                else:
                    right.label(text="Target: not Browse")

    @staticmethod
    def draw_driver(box, link, sources, targets):
        column = box.column(align=True)
        column.label(text="Source (var)", icon="EXPORT")
        column.row().prop(link, "source_mode", expand=True)
        if link.source_mode == "PICKED":
            column.label(text=picked_text(sources[0] if sources else None, link.source_spec),
                         icon="CHECKMARK" if link.source_spec else "QUESTION")
        elif link.source_mode == "TRANSFORM":
            column.prop(link, "source_channel")
            column.prop(link, "source_space")
        else:
            row = column.row(align=True)
            row.prop(link, "source_path")
            row.prop(link, "source_index", text="")
        box.separator()
        column = box.column(align=True)
        column.label(text="Target", icon="IMPORT")
        column.row().prop(link, "driven_mode", expand=True)
        if link.driven_mode == "PICKED":
            picks = target_specs(link)
            if not picks:
                column.label(text="Pick values in the list below (several allowed)", icon="QUESTION")
            for spec in picks[:6]:
                row = column.row(align=True)
                split = row.split(factor=0.55, align=True)
                split.label(text=picked_text(targets[0] if targets else None, spec), icon="CHECKMARK")
                override = override_for(link, spec)
                if override is not None:
                    split.prop(override, "expression", text="", placeholder="same formula")
                op = row.operator("schematic.link_pick", text="", icon="X", emboss=False)
                op.side, op.spec = "T", spec
            if len(picks) > 6:
                column.label(text=f"+{len(picks) - 6} more")
            if len(picks) > 1:
                op = column.operator("schematic.link_pick", text=f"Clear {len(picks)} Picks", icon="TRASH")
                op.side, op.spec = "T", "*CLEAR*"
        elif link.driven_mode == "TRANSFORM":
            column.prop(link, "driven_channel")
        elif link.driven_mode == "PROPERTY":
            row = column.row(align=True)
            row.prop(link, "driven_path")
            row.prop(link, "driven_index", text="")
        else:
            keys = next((getattr(t.obj.data, "shape_keys", None) for t in targets
                         if not t.bone and getattr(t.obj.data, "shape_keys", None)), None)
            if keys:
                column.prop_search(link, "shape_key", keys, "key_blocks")
            else:
                column.prop(link, "shape_key")
        box.separator()
        row = box.row(align=True)
        row.prop(link, "expression")
        row.menu("SCHEMATIC_MT_link_expressions", text="", icon="PRESET")
        row = box.row(align=True)
        row.prop(link, "use_remap", toggle=True, icon="MOD_LENGTH")
        row.prop(link, "auto_units", toggle=True, icon="DRIVER_TRANSFORM")
        if link.use_remap:
            grid = box.grid_flow(columns=2, align=True)
            grid.prop(link, "from_min")
            grid.prop(link, "to_min")
            grid.prop(link, "from_max")
            grid.prop(link, "to_max")
            box.prop(link, "remap_clamp")
        hint = unit_hint(link, sources, targets)
        if hint:
            box.label(text=hint, icon="INFO")
        row = box.row(align=True)
        row.prop(link, "chain", toggle=True, icon="LINKED")
        box.prop(link, "replace_existing")
        if link.chain and targets:
            names = " → ".join(entity.name for entity in [*sources[:1], *targets])
            box.label(text=names, icon="FORWARD")


UNIT_NAMES = {"ROTATION": "angle (°)", "LENGTH": "distance (m)", "NONE": "number"}


def unit_hint(link, sources, targets):
    """One line telling how units are converted for the first source → target pair."""
    if not sources or not targets:
        return ""
    try:
        reader, slot, _expression = driver_jobs(link, sources[0], targets[0])[0]
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return ""
    left = UNIT_NAMES.get(reader.unit, "number")
    right = "whole number" if slot.type == "INT" else "on/off" if slot.type == "BOOLEAN" else UNIT_NAMES.get(
        slot.unit, "number")
    if not link.auto_units:
        return f"{left} → {right} · no conversion (angles are radians)" if "ROTATION" in {
            reader.unit, slot.unit} else ""
    if left == right:
        return ""
    return f"{left} → {right} · converted automatically"


def picked_text(entity, spec):
    if not spec:
        return "Pick a value in the list below"
    if entity is None:
        return spec
    try:
        return f"{entity.name} · {spec_label(entity, spec)}"
    except (ValueError, ReferenceError):
        return spec


def draw_browser(layout, link, side, entity):
    """Foldable list of an entity's values. Side 'S' picks one source, 'T' ticks several targets."""
    title = "Target" if side == "T" else "Source"
    if entity is None:
        layout.label(text=f"Mark a {title}", icon="INFO")
        return
    name = entity.name if not entity.bone else f"{entity.bone} · {entity.obj.name}"
    layout.label(text=f"{title}: {name}", icon="IMPORT" if side == "T" else "EXPORT")
    query_prop = "source_filter" if side == "S" else "target_filter"
    layout.prop(link, query_prop, text="", icon="VIEWZOOM")
    query = getattr(link, query_prop).strip().casefold()
    chosen = {link.source_spec} if side == "S" else set(target_specs(link))
    expanded = set(link.expanded.split(";"))
    column = layout.column(align=True)
    shown = 0
    for key, label, icon, build in categories(entity):
        full_key = f"{side}:{key}"
        is_open = bool(query) or full_key in expanded
        if not query:
            row = column.row(align=True)
            row.operator("schematic.link_expand", text=label, emboss=False,
                         icon="DOWNARROW_HLT" if is_open else "RIGHTARROW").key = full_key
            row.label(text="", icon=icon)
        if not is_open:
            continue
        rows = build()
        if query:
            rows = [row for row in rows if query in row.label.casefold() or query in label.casefold()
                    or any(query in f"{row.label} {child.label}".casefold() for child in row.children)]
            if not rows:
                continue
            column.label(text=label, icon=icon)
        for row_data in rows[:60]:
            draw_row(column, link, side, entity, row_data, chosen, expanded, key, bool(query))
            shown += 1
        if len(rows) > 60:
            column.label(text=f"+{len(rows) - 60} more · type in Search")
    if query and not shown:
        column.label(text="Nothing matches", icon="INFO")


def draw_row(column, link, side, entity, row_data, chosen, expanded, category_key, searching):
    """A value, or a vector / colour that folds open into its components."""
    row = column.row(align=True)
    row.separator(factor=1.2)
    if row_data.children:
        fold_key = f"{side}:{category_key}:{row_data.spec}"
        is_open = searching or fold_key in expanded
        row.operator("schematic.link_expand", text="", emboss=False,
                     icon="DOWNARROW_HLT" if is_open else "RIGHTARROW").key = fold_key
    else:
        is_open = False
    pick = row.operator("schematic.link_pick", text=row_data.label, depress=row_data.spec in chosen)
    pick.side, pick.spec = side, row_data.spec
    if link.show_values:
        draw_value(row, entity, row_data.spec, wide=bool(row_data.children))
    if not is_open:
        return
    for child in row_data.children:
        sub = column.row(align=True)
        sub.separator(factor=4.0)
        pick = sub.operator("schematic.link_pick", text=child.label, depress=child.spec in chosen)
        pick.side, pick.spec = side, child.spec
        if link.show_values:
            draw_value(sub, entity, child.spec)


def draw_value(row, entity, spec, wide=False):
    """Live, editable value next to an entry."""
    try:
        resolved = Resolved(entity, spec)
    except ValueError:
        return
    sub = row.row(align=True)
    sub.scale_x = 1.4 if wide else 0.7
    index = resolved.index if resolved.length and resolved.index >= 0 else -1
    sub.prop(resolved.owner, resolved.prop, text="", index=index)


class BrowserPanel(LinkPanel):
    bl_parent_id = "SCHEMATIC_PT_link"
    side = "S"

    @classmethod
    def poll(cls, context):
        tree = editor_tree(context)
        if not (tree and tree.scene == context.scene):
            return False
        link = settings(tree)
        mode = link.source_mode if cls.side == "S" else link.driven_mode
        return link.action == "DRIVER" and mode == "PICKED" and link.browser_layout == "STACKED"

    def draw(self, context):
        tree = editor_tree(context)
        sources, targets = split_roles(tree)
        entity = (sources or [None])[0] if self.side == "S" else (targets or [None])[0]
        draw_browser(self.layout, settings(tree), self.side, entity)


class SCHEMATIC_PT_link_source_props(BrowserPanel, Panel):
    bl_idname = "SCHEMATIC_PT_link_source_props"
    bl_label = "Source Properties"
    side = "S"


class SCHEMATIC_PT_link_target_props(BrowserPanel, Panel):
    bl_idname = "SCHEMATIC_PT_link_target_props"
    bl_label = "Target Properties"
    side = "T"


class SCHEMATIC_PT_link_existing(LinkPanel, Panel):
    bl_idname = "SCHEMATIC_PT_link_existing"
    bl_label = "Links Between These"
    bl_parent_id = "SCHEMATIC_PT_link"

    def draw(self, context):
        tree = editor_tree(context)
        found = links_between(tree)
        if not found:
            self.layout.label(text="No relationships yet")
            return
        labels = {node.name: node.label for node in tree.nodes}
        column = self.layout.column(align=True)
        for index, record in found:
            row = column.row(align=True)
            kind = RELATIONS.get(record[2], (record[2],))[0]
            row.label(text=f"{labels.get(record[0], record[0])} → {labels.get(record[1], record[1])}  ·  {kind}")
            row.operator("schematic.link_unlink", text="", icon="UNLINKED").index = index


def viewport_menu(self, context):
    self.layout.separator()
    self.layout.operator("schematic.open_link", icon="LINKED")


CLASSES = (
    SCHEMATIC_PG_link_item, SCHEMATIC_PG_link_override, SCHEMATIC_PG_link_settings,
    SCHEMATIC_OT_link_load, SCHEMATIC_OT_link_item, SCHEMATIC_OT_link_apply, SCHEMATIC_OT_link_unlink,
    SCHEMATIC_OT_link_pick, SCHEMATIC_OT_link_expand, SCHEMATIC_OT_link_copy, SCHEMATIC_OT_link_sort,
    SCHEMATIC_MT_link_sort,
    SCHEMATIC_OT_open_link, SCHEMATIC_UL_link_items, SCHEMATIC_MT_link_expressions,
    SCHEMATIC_PT_link, SCHEMATIC_PT_link_source_props, SCHEMATIC_PT_link_target_props,
    SCHEMATIC_PT_link_existing,
)
MENUS = ("VIEW3D_MT_object_context_menu", "VIEW3D_MT_pose_context_menu")


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    SCHEMATIC_NT_scene.link = PointerProperty(type=SCHEMATIC_PG_link_settings)
    for name in MENUS:
        getattr(bpy.types, name).append(viewport_menu)


def unregister():
    for name in MENUS:
        menu = getattr(bpy.types, name, None)
        if menu:
            menu.remove(viewport_menu)
    del SCHEMATIC_NT_scene.link
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
