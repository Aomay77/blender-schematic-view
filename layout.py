# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aomay Huang
# Schematic View for Blender. Modified versions must say so and keep this notice.
"""Arrange bones by the anatomical terms in their rig names.⁠​‌​‌‌​‌‌‌​‌​‌‌‌​​​‌​‌‌​​‌​‌‌‌‌‌‌‌‌​‌​​‌​‌‌​​​‌‌‌​‌‌​‌​​​‌​‌​​​‌‌​​​​​​‌​‌​‌​‌‌​‌‌​‌‌‌​‌​​​‌​‌​​‌⁠"""

from collections import defaultdict
from functools import lru_cache
from math import ceil
import re


COLUMN = 275
ROW = 110

# (Upper-to-lower band, distance from the torso in columns)
REGIONS = {
    "head": (0, 0), "neck": (1, 0), "chest": (2, 0), "shoulder": (2, 3),
    "arm": (3, 4), "forearm": (4, 5), "hand": (5, 6), "finger": (6, 7),
    "spine": (4, 0), "pelvis": (6, 0), "thigh": (7, 2), "leg": (8, 2),
    "foot": (9, 2), "toe": (10, 2), "root": (11, 0),
}

TERMS = (
    ("finger", r"finger|thumb|index|middle|ring|pinky|little"),
    ("toe", r"toe"), ("foot", r"foot|ankle|heel"),
    ("thigh", r"thigh|upper_?leg|upleg|hips?_?(?:side|twist)"),
    ("leg", r"calf|shin|lower_?leg|knee|(?:^|_)leg(?:_|$)"),
    ("hand", r"hand|wrist|palm"),
    ("forearm", r"forearm|lower_?arm|elbow"),
    ("shoulder", r"shoulder|clavicle|collar|scapula"),
    ("arm", r"upper_?arm|(?:^|_)arms?(?:_|$)"),
    ("head", r"head|face|jaw|cheek|chin|lip|eye|brow|nose|teeth|tongue|ear"),
    ("neck", r"neck"), ("chest", r"chest|ribcage|breast|spine_?0?[34]"),
    ("spine", r"spine|waist|abdomen|belly"),
    ("pelvis", r"pelvis|hips?|root"), ("root", r"traj|(?:^|_)pos(?:_|$)|master"),
)
REGION_PATTERNS = [(part, re.compile(pattern)) for part, pattern in TERMS]
SIDE_PATTERN = re.compile(r"(?:^|_)(left|right|l|r)(?=_|$)")


@lru_cache(maxsize=8192)
def body_region(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.rsplit(":", 1)[-1])
    name = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
    side_match = SIDE_PATTERN.search(name)
    side = 0
    if side_match:
        # The character faces the viewer.
        side = 1 if side_match[1] in {"l", "left"} else -1
    region = next((part for part, pattern in REGION_PATTERNS if pattern.search(name)), None)
    return region, side


def tree_positions(keys, edges, vertical=False):
    """Tidy tree like 3ds Max's Hierarchy mode: each parent sits beside its first child."""
    keyset = set(keys)
    parent = {}
    children = defaultdict(list)
    for edge in edges:
        source, target = edge.source.key, edge.target.key
        if (edge.kind in {"PARENT", "MEMBER"} and source in keyset and target in keyset
                and target not in parent and source != target):
            parent[target] = source
            children[source].append(target)
    order = {key: index for index, key in enumerate(keys)}
    for items in children.values():
        items.sort(key=order.__getitem__)
    result = {}
    slot = 0
    for root in keys:
        if root in parent or root in result:
            continue
        stack = [(root, 0)]
        while stack:
            key, depth = stack.pop()
            if key in result:
                continue
            if vertical:
                result[key] = (slot * COLUMN, -depth * 2 * ROW)
            else:
                result[key] = (depth * COLUMN, -slot * ROW)
            kids = [child for child in children[key] if child not in result]
            if not kids:
                slot += 1
            stack.extend((child, depth + 1) for child in reversed(kids))
    for key in keys:
        if key not in result:
            result[key] = (0, -slot * ROW) if not vertical else (slot * COLUMN, 0)
            slot += 1
    return result


def body_positions(keys, kinds):
    groups = defaultdict(list)
    other = []
    for key in keys:
        region, side = body_region(key[1])
        if region is None:
            other.append(key)
        else:
            groups[region, side].append(key)
    if not groups:
        return {}

    band_rows = defaultdict(lambda: 1)
    for (region, _), group in groups.items():
        band = REGIONS[region][0]
        band_rows[band] = max(band_rows[band], ceil(len(group) / 3))
    band_top = {}
    row = 0
    for band in range(12):
        band_top[band] = row
        row += band_rows[band]

    positions = {}
    priority = {"CONTROL": 0, "DEFORM": 1, "BONE": 2, "REFERENCE": 3}
    occupied = set()
    for (region, side), group in sorted(groups.items(), key=lambda item: (
        REGIONS[item[0][0]][0], REGIONS[item[0][0]][1], item[0][1],
    )):
        band, distance = REGIONS[region]
        group.sort(key=lambda key: (priority.get(kinds[key], 4), key[1].casefold()))
        for i, key in enumerate(group):
            col, rank = i % 3, i // 3 + band_top[band]
            x = side * (max(2, distance) + col) if side else (col - 1 if len(group) > 1 else 0)
            direction = side or 1
            while (x, rank) in occupied:
                x += direction
            occupied.add((x, rank))
            positions[key] = (x * COLUMN, -rank * ROW)

    right = max(x for x, _ in positions.values()) + 2 * COLUMN
    for i, key in enumerate(other):
        positions[key] = (right + (i // row) * COLUMN, -(i % row) * ROW)
    return positions


OBLIQUE = 0.35  # Side view: depth is drawn diagonally so left and right limbs do not stack.
SPLAY = 1.0     # Top view: limbs below the back are laid flat outward, like a pelt seen from above.
CROWDING = 2.5  # Grid cells reserved per bone inside the skeleton's silhouette.
FACE_CROWDING = 2.0
FACE_GAP = 3    # Empty rows between the head and the face controls above it.
FACE_ROOT = re.compile(r"(?:^|[_.\s:])(?:c_)?(face|head|skull)(?:$|[_.\s\d])", re.IGNORECASE)


def name_side(name):
    """1 for the character's left, -1 for right, 0 for centre, read from the bone name."""
    return body_region(name)[1]


def auto_view(names, points):
    """Top view for long horizontal bodies (quadrupeds, fish), front view for people and birds."""
    if any("wing" in name.casefold() for name in names):
        return "FRONT"
    extents = [max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)]
    if extents[1] > max(extents[0], extents[2]):
        return "TOP"
    return "FRONT"


def project(points, view):
    """Flatten rest-pose points to graph space (x right, y up)."""
    if view == "SIDE":
        # Blender rigs face -Y, so the head is on the left; left limbs sit up and to the right.
        return {key: (p[1] + OBLIQUE * p[0], p[2] + OBLIQUE * p[0]) for key, p in points.items()}
    if view == "TOP":
        # Seen from above with the head at the top, the creature's left is on screen left.
        centre = [p[2] for p in points.values() if abs(p[0]) < 0.05 * max(abs(q[0]) for q in points.values()) + 1e-6]
        back = sorted(centre)[len(centre) // 2] if centre else max(p[2] for p in points.values())
        flat = {}
        for key, (x, y, z) in points.items():
            side = (x > 1e-4) - (x < -1e-4)
            spread = x + side * max(0.0, back - z) * SPLAY
            flat[key] = (-spread, -y)
        return flat
    # Front: the character faces the viewer, so its left is on screen right.
    return {key: (p[0], p[2]) for key, p in points.items()}


def ring(radius):
    """Grid cells on the boundary of a box that is `radius` columns wide (cells are ~2.5:1)."""
    tall = 3 * radius
    for dc in (-radius, radius):
        for dr in range(-tall, tall + 1):
            yield dc, dr
    for dc in range(-radius + 1, radius):
        for dr in range(tall - 2, tall + 1):
            yield dc, dr
            yield dc, -dr


def nearest_free(target, occupied):
    """Closest free grid cell to a pixel position, searching outward ring by ring."""
    tx, ty = target
    col, row = round(tx / COLUMN), round(ty / ROW)
    best, best_distance = None, float("inf")
    radius = 0
    while True:
        cells = [(0, 0)] if radius == 0 else ring(radius)
        for dc, dr in cells:
            cell = (col + dc, row + dr)
            if cell in occupied:
                continue
            distance = ((cell[0] * COLUMN - tx) ** 2 + (cell[1] * ROW - ty) ** 2) ** 0.5
            if distance < best_distance:
                best, best_distance = cell, distance
        # Anything outside this box is farther than its nearest edge.
        if best is not None and best_distance <= min((radius + 0.5) * COLUMN, (3 * radius + 0.5) * ROW):
            return best
        radius += 1


def place(flat, lengths, crowding):
    """Snap flattened points to free grid cells, long bones first. Returns {key: (col, row)}."""
    xs = [p[0] for p in flat.values()]
    ys = [p[1] for p in flat.values()]
    width = max(max(xs) - min(xs), 1e-6)
    height = max(max(ys) - min(ys), 1e-6)
    count = len(flat)
    scale = (crowding * count * COLUMN * ROW / (width * height)) ** 0.5
    scale = min(scale, crowding * count * COLUMN / width, crowding * count * ROW / height)
    left, top = min(xs), max(ys)
    occupied = set()
    cells = {}
    for key in sorted(flat, key=lambda key: (-lengths[key], key[1])):
        x, y = flat[key]
        cell = nearest_free(((x - left) * scale, (y - top) * scale), occupied)
        occupied.add(cell)
        cells[key] = cell
    return cells


def face_bones(rig, keys):
    """Bones that belong to the face: everything under the face / head / skull bone."""
    keyset = {key[1] for key in keys}
    best = None
    for bone in rig.data.bones:
        match = FACE_ROOT.search(bone.name)
        if not match or bone.name not in keyset:
            continue
        below = [child.name for child in bone.children_recursive if child.name in keyset]
        if len(below) >= 4 and (best is None or len(below) > len(best[1])):
            include_root = match[1].casefold() == "face"
            best = (bone.name, below + ([bone.name] if include_root else []))
    return set(best[1]) if best else set()


def shape_positions(rig, keys, view="AUTO", labels=None):
    """Place bone cards where the bones are in the rest pose, so the graph shows the creature."""
    bones = rig.data.bones
    points, lengths = {}, {}
    for key in keys:
        bone = bones.get(key[1])
        if bone is None:
            continue
        head, tail = bone.head_local, bone.tail_local
        points[key] = tuple((head[i] + tail[i]) / 2 for i in range(3))
        lengths[key] = (tail - head).length
    if len(points) < 2:
        return {}
    if view == "AUTO":
        view = auto_view([key[1] for key in points], list(points.values()))

    face = face_bones(rig, list(points))
    body_keys = {key: p for key, p in points.items() if key[1] not in face}
    face_keys = {key: p for key, p in points.items() if key[1] in face}
    if len(body_keys) < 2:
        body_keys, face_keys = points, {}

    cells = place(project(body_keys, view), lengths, CROWDING)
    if face_keys:
        # The face is drawn as a front-on portrait above the head, with a gap so it is easy to pick.
        face_cells = place(project(face_keys, "FRONT"), lengths, FACE_CROWDING)
        anchor_names = {bone.parent.name for bone in (bones[key[1]] for key in face_keys)
                        if bone.parent and bone.parent.name not in face}
        anchors = [cells[key] for key in body_keys if key[1] in anchor_names] or list(cells.values())
        head_col = round(sum(c for c, _ in anchors) / len(anchors))
        top_row = max(r for _, r in cells.values())
        face_cols = [c for c, _ in face_cells.values()]
        face_rows = [r for _, r in face_cells.values()]
        shift_col = head_col - round((min(face_cols) + max(face_cols)) / 2)
        shift_row = top_row + FACE_GAP - min(face_rows)
        cells.update({key: (c + shift_col, r + shift_row) for key, (c, r) in face_cells.items()})

    result = {key: (c * COLUMN, r * ROW) for key, (c, r) in cells.items()}
    if labels is not None and view in {"FRONT", "TOP"}:
        cols = [c for c, _ in cells.values()]
        rows = [r for _, r in cells.values()]
        left_text, right_text = ("Right (R)", "Left (L)") if view == "FRONT" else ("Left (L)", "Right (R)")
        mid_row = (min(rows) + max(rows)) / 2
        labels.append(((min(cols) - 2) * COLUMN, mid_row * ROW, left_text))
        labels.append(((max(cols) + 2) * COLUMN, mid_row * ROW, right_text))
    return result


def arrange(kinds, edges, style="SHAPE", view="AUTO", labels=None):
    ordered = sorted(kinds, key=lambda key: (key[0].name.casefold(), key[1].casefold()))
    if style in {"HORIZONTAL", "VERTICAL"}:
        return tree_positions(ordered, edges, vertical=style == "VERTICAL")
    by_rig = defaultdict(list)
    for key in ordered:
        if key[1]:
            by_rig[key[0]].append(key)
    positions = {}
    x_offset = 0
    for rig, keys in by_rig.items():
        if style == "SHAPE":
            rig_labels = []
            body = shape_positions(rig, keys, view, rig_labels)
            # Bones without a rest position (none in practice) fall back to the body layout.
            missing = [key for key in keys if key not in body]
            if missing and body:
                right = max(x for x, _ in body.values()) + 2 * COLUMN
                body.update({key: (right, -i * ROW) for i, key in enumerate(missing)})
        else:
            body = body_positions(keys, kinds)
        if not body:
            continue
        left = min(x for x, _ in body.values())
        top = max(y for _, y in body.values())
        positions.update({key: (x + x_offset - left, y) for key, (x, y) in body.items()})
        if style == "SHAPE" and labels is not None:
            labels.extend((x + x_offset - left, y, text) for x, y, text in rig_labels)
        if (rig, "") in kinds:
            positions[rig, ""] = (x_offset, top + 2 * ROW)
        x_offset += max(x for x, _ in body.values()) - left + 2 * COLUMN

    remaining = [key for key in ordered if key not in positions]
    for key, (x, y) in tree_positions(remaining, edges).items():
        positions[key] = (x + x_offset, y)
    return positions
