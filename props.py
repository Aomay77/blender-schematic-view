# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aomay Huang
# Schematic View for Blender. Modified versions must say so and keep this notice.
"""Browse and address the animatable data of an object or bone, like 3ds Max's wiring tree.⁠​‌​‌‌​‌‌‌​‌​‌‌‌​​​‌​‌‌​​‌​‌‌‌‌‌‌‌‌​‌​​‌​‌‌​​​‌‌‌​‌‌​‌​​​‌​‌​​​‌‌​​​​​​‌​‌​‌​‌‌​‌‌​‌‌‌​‌​​​‌​‌​​‌⁠

A property is addressed by a spec "ROOT|path|index" relative to an entity, so the same spec
can be resolved on another object or bone (used to chain A → B → C):

  OBJECT      the object itself                 location, modifiers["Bevel"].width, ["my_prop"]
  POSEBONE    the pose bone                     location, constraints["IK"].influence
  DATA        the object's data (light, camera) energy, lens
  SHAPE       the object's shape key block      key_blocks["Smile"].value
  MAT:<slot>  the material in that slot         node_tree.nodes["Principled BSDF"].inputs[2].default_value
"""

import bpy


SEP = "|"
TRANSFORM_PROPS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                   "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")
SKIP_PROPS = {"rna_type", "name", "show_expanded", "is_active", "is_override_data", "use_pin_to_last",
              "show_in_editmode", "show_on_cage", "show_viewport", "show_render", "show_in_front",
              "is_valid", "active", "mute", "error_location", "error_rotation", "persistent_uid",
              "execution_time", "select", "select_head", "select_tail", "hide_select"}
NUMERIC = {"FLOAT", "INT", "BOOLEAN"}
SOCKET_VALUE = {"VALUE", "INT", "BOOLEAN", "VECTOR", "RGBA", "ROTATION"}
AXES = {"XYZ": "XYZ", "QUATERNION": "WXYZ", "AXISANGLE": "WXYZ", "COLOR": "RGBA",
        "COLOR_GAMMA": "RGBA", "EULER": "XYZ", "TRANSLATION": "XYZ", "DIRECTION": "XYZ",
        "VELOCITY": "XYZ", "ACCELERATION": "XYZ"}


def make_spec(root, path, index=-1):
    return f"{root}{SEP}{path}{SEP}{index}"


def parse_spec(spec):
    root, path, index = spec.rsplit(SEP, 2) if spec.count(SEP) >= 2 else ("", "", "-1")
    return root, path, int(index)


def root_struct(entity, root):
    """The struct a spec's path starts from, or None when it does not exist on this entity."""
    obj = entity.obj
    if root == "POSEBONE":
        return entity.owner if entity.bone else None
    if entity.bone:
        return None
    if root == "OBJECT":
        return obj
    if root == "DATA":
        return obj.data
    if root == "SHAPE":
        return getattr(obj.data, "shape_keys", None)
    if root.startswith("MAT:"):
        slot = int(root[4:])
        if slot < len(obj.material_slots):
            return obj.material_slots[slot].material
    return None


def join(base, path):
    if not base:
        return path
    return base + path if path.startswith("[") else f"{base}.{path}"


def split_last(full_path):
    """'a.b["c"].d' → ('a.b["c"]', 'd');  'a["x"]' → ('a', '["x"]')."""
    if full_path.endswith('"]'):
        # Leaf paths that end in ["..."] are always custom properties.
        start = full_path.rfind('["')
        return full_path[:start].rstrip("."), full_path[start:]
    owner, _, prop = full_path.rpartition(".")
    return owner, prop


class Resolved:
    """A spec resolved on one entity: the owning ID, full path from it, and the leaf struct."""

    def __init__(self, entity, spec):
        root, path, index = parse_spec(spec)
        base = root_struct(entity, root)
        if base is None:
            raise ValueError(f"{entity.name} has no {describe_root(root)}")
        self.id = base.id_data
        base_path = "" if base == self.id else base.path_from_id()
        self.full_path = join(base_path, path)
        self.index = index
        owner_path, self.prop = split_last(self.full_path)
        try:
            self.owner = self.id.path_resolve(owner_path) if owner_path else self.id
            value = self.owner.path_resolve(self.prop) if not self.prop.startswith("[") else self.owner[self.prop[2:-2]]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ValueError(f"{entity.name} has no {path}") from None
        self.length = len(value) if hasattr(value, "__len__") and not isinstance(value, str) else 0
        if self.length and index >= self.length:
            raise ValueError(f"{entity.name}: {path} has no component {index}")
        self.root = root

    @property
    def value(self):
        value = self.id.path_resolve(self.full_path)
        return value[self.index] if self.length and self.index >= 0 else value

    @value.setter
    def value(self, new):
        if self.length and self.index < 0:
            raise ValueError("Set one component at a time")
        if self.prop.startswith("["):
            key = self.prop[2:-2]
            if self.length:
                self.owner[key][self.index] = new
            else:
                self.owner[key] = type(self.owner[key])(new)
            return
        if self.length:
            array = getattr(self.owner, self.prop)
            array[self.index] = new
        else:
            current = getattr(self.owner, self.prop)
            setattr(self.owner, self.prop, type(current)(new))

    @property
    def driver_path(self):
        """Path including the component, for a SINGLE_PROP driver variable."""
        return f"{self.full_path}[{self.index}]" if self.length and self.index >= 0 else self.full_path

    @property
    def is_transform(self):
        return self.root in {"OBJECT", "POSEBONE"} and self.full_path.split(".")[-1] in TRANSFORM_PROPS


def describe_root(root):
    if root.startswith("MAT:"):
        return f"material slot {int(root[4:]) + 1}"
    return {"OBJECT": "object property", "POSEBONE": "pose bone", "DATA": "object data",
            "SHAPE": "shape keys"}.get(root, root)


# ---------------------------------------------------------------- listing

def component_labels(prop, length):
    letters = AXES.get(prop.subtype, "")
    if len(letters) >= length:
        return list(letters[:length])
    return [str(i) for i in range(length)]


class Row:
    """One line of the browser. A row with children is a vector / colour that folds open."""

    __slots__ = ("label", "spec", "children")

    def __init__(self, label, spec, children=None):
        self.label, self.spec, self.children = label, spec, children or []


def vector_row(label, root, path, letters):
    children = [Row(letter, make_spec(root, path, i)) for i, letter in enumerate(letters)]
    return Row(label, make_spec(root, path, -1), children)


def flatten(rows):
    """[(label, spec)] for every row and component, e.g. ('Location', …), ('Location X', …)."""
    result = []
    for row in rows:
        result.append((row.label, row.spec))
        result.extend((f"{row.label} {child.label}", child.spec) for child in row.children)
    return result


def rna_rows(struct, root, base, skip=(), only=None):
    """Rows for numeric, animatable properties of a struct."""
    rows = []
    names = only if only is not None else [p.identifier for p in struct.bl_rna.properties]
    for identifier in names:
        prop = struct.bl_rna.properties.get(identifier)
        if (prop is None or identifier in SKIP_PROPS or identifier in skip or prop.type not in NUMERIC
                or prop.is_readonly or not prop.is_animatable):
            continue
        path = join(base, identifier)
        name = prop.name or identifier
        if prop.array_length:
            rows.append(vector_row(name, root, path, component_labels(prop, prop.array_length)))
        else:
            rows.append(Row(name, make_spec(root, path)))
    return rows


def custom_rows(struct, root, base=""):
    rows = []
    for key in struct.keys():
        if key.startswith("_") or key == "cycles":
            continue
        value = struct[key]
        path = join(base, f'["{bpy.utils.escape_identifier(key)}"]')
        if isinstance(value, (int, float, bool)):
            rows.append(Row(key, make_spec(root, path)))
        elif hasattr(value, "__len__") and not isinstance(value, str) and all(
                isinstance(v, (int, float)) for v in value):
            rows.append(vector_row(key, root, path, [str(i) for i in range(len(value))]))
    return rows


def material_rows(material, root):
    rows = []
    tree = material.node_tree
    if tree is None:
        return rna_rows(material, root, "", only=["diffuse_color", "metallic", "roughness"])
    for node in tree.nodes:
        sockets = [(f"inputs[{i}]", socket) for i, socket in enumerate(node.inputs) if not socket.is_linked]
        if node.type == "VALUE":
            sockets.append(("outputs[0]", node.outputs[0]))
        for socket_path, socket in sockets:
            if socket.type not in SOCKET_VALUE or not hasattr(socket, "default_value") or socket.hide_value:
                continue
            path = f'node_tree.nodes["{bpy.utils.escape_identifier(node.name)}"].{socket_path}.default_value'
            label = f"{node.label or node.name} › {socket.name}"
            value = socket.default_value
            if hasattr(value, "__len__"):
                letters = "RGBA" if socket.type == "RGBA" else "XYZW"
                rows.append(vector_row(label, root, path, letters[:len(value)]))
            else:
                rows.append(Row(label, make_spec(root, path)))
    return rows


def rotation_props(owner, delta=False):
    """Only the rotation the object or bone actually uses."""
    mode = owner.rotation_mode
    prefix = "delta_" if delta else ""
    if mode == "QUATERNION":
        return [f"{prefix}rotation_quaternion"]
    if mode == "AXIS_ANGLE" and not delta:
        return ["rotation_axis_angle"]
    return [f"{prefix}rotation_euler"]


def categories(entity):
    """[(key, label, icon, builder)] — builders run only for expanded categories."""
    obj = entity.obj
    if entity.bone:
        bone = entity.owner
        result = [
            ("transform", "Transform", "ORIENTATION_GIMBAL",
             lambda: rna_rows(bone, "POSEBONE", "", only=["location", *rotation_props(bone), "scale"])),
            ("custom", "Custom Properties", "PROPERTIES", lambda: custom_rows(bone, "POSEBONE")),
            ("bone", "Bone", "BONE_DATA",
             lambda: rna_rows(bone, "POSEBONE", "", skip=TRANSFORM_PROPS)),
        ]
        for constraint in bone.constraints:
            base = f'constraints["{bpy.utils.escape_identifier(constraint.name)}"]'
            result.append((f"con:{constraint.name}", f"Constraint · {constraint.name}", "CONSTRAINT",
                           lambda c=constraint, b=base: rna_rows(c, "POSEBONE", b)))
        return result

    result = [
        ("transform", "Transform", "ORIENTATION_GIMBAL",
         lambda: rna_rows(obj, "OBJECT", "", only=["location", *rotation_props(obj), "scale"])),
        ("custom", "Custom Properties", "PROPERTIES", lambda: custom_rows(obj, "OBJECT")),
        ("delta", "Delta Transform", "ORIENTATION_PARENT",
         lambda: rna_rows(obj, "OBJECT", "", only=["delta_location", *rotation_props(obj, True), "delta_scale"])),
        ("object", "Object", "OBJECT_DATA",
         lambda: rna_rows(obj, "OBJECT", "", skip=TRANSFORM_PROPS)),
    ]
    data = obj.data
    if data is not None and obj.type not in {"ARMATURE", "MESH"}:
        icon = "OUTLINER_DATA_" + obj.type if obj.type in {"LIGHT", "CAMERA", "CURVE"} else "DATA"
        result.append(("data", f"Data · {data.name}", icon,
                       lambda: rna_rows(data, "DATA", "") + custom_rows(data, "DATA")))
    for modifier in obj.modifiers:
        base = f'modifiers["{bpy.utils.escape_identifier(modifier.name)}"]'
        result.append((f"mod:{modifier.name}", f"Modifier · {modifier.name}", "MODIFIER",
                       lambda m=modifier, b=base: rna_rows(m, "OBJECT", b)))
    for constraint in obj.constraints:
        base = f'constraints["{bpy.utils.escape_identifier(constraint.name)}"]'
        result.append((f"con:{constraint.name}", f"Constraint · {constraint.name}", "CONSTRAINT",
                       lambda c=constraint, b=base: rna_rows(c, "OBJECT", b)))
    keys = getattr(data, "shape_keys", None)
    if keys:
        result.append(("shape", "Shape Keys", "SHAPEKEY_DATA", lambda k=keys: [
            Row(block.name, make_spec("SHAPE", f'key_blocks["{bpy.utils.escape_identifier(block.name)}"].value'))
            for block in k.key_blocks[1:]]))
    for slot_index, slot in enumerate(obj.material_slots):
        if slot.material:
            result.append((f"mat:{slot_index}", f"Material · {slot.material.name}", "MATERIAL",
                           lambda m=slot.material, r=f"MAT:{slot_index}": material_rows(m, r)))
    return result


def spec_label(entity, spec):
    """Human label for a spec on an entity, e.g. 'Location X' or 'Principled BSDF › Roughness'."""
    root, path, index = parse_spec(spec)
    for _key, label, _icon, build in categories(entity):
        for entry_label, entry_spec in flatten(build()):
            if entry_spec == spec:
                return f"{label.split(' · ')[-1]} › {entry_label}" if " · " in label else entry_label
    suffix = f"[{index}]" if index >= 0 else ""
    return f"{path}{suffix}"
