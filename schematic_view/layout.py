"""Arrange bones by the anatomical terms in their rig names."""

from collections import defaultdict, deque
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


def hierarchy_positions(keys, edges):
    children = defaultdict(list)
    parents = defaultdict(int)
    columns = dict.fromkeys(keys, 0)
    for edge in edges:
        if (edge.kind in {"PARENT", "MEMBER"}
                and edge.source.key in columns and edge.target.key in columns):
            children[edge.source.key].append(edge.target.key)
            parents[edge.target.key] += 1
    ready = deque(key for key in keys if not parents[key])
    while ready:
        key = ready.popleft()
        for child in children[key]:
            columns[child] = max(columns[child], columns[key] + 1)
            parents[child] -= 1
            if not parents[child]:
                ready.append(child)
    rows = defaultdict(int)
    result = {}
    for key in keys:
        column = columns[key]
        result[key] = (column * COLUMN, -rows[column] * ROW)
        rows[column] += 1
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


def arrange(kinds, edges):
    ordered = sorted(kinds, key=lambda key: (key[0].name.casefold(), key[1].casefold()))
    by_rig = defaultdict(list)
    for key in ordered:
        if key[1]:
            by_rig[key[0]].append(key)
    positions = {}
    x_offset = 0
    for rig, keys in by_rig.items():
        body = body_positions(keys, kinds)
        if not body:
            continue
        left = min(x for x, _ in body.values())
        positions.update({key: (x + x_offset - left, y) for key, (x, y) in body.items()})
        if (rig, "") in kinds:
            positions[rig, ""] = (x_offset - left, 2 * ROW)
        x_offset += max(x for x, _ in body.values()) - left + 2 * COLUMN

    remaining = [key for key in ordered if key not in positions]
    for key, (x, y) in hierarchy_positions(remaining, edges).items():
        positions[key] = (x + x_offset, y)
    return positions
