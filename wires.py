# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aomay Huang
# Schematic View for Blender. Modified versions must say so and keep this notice.
"""Draw schematic relationships behind Blender's node cards.⁠​‌​‌‌​‌‌‌​‌​‌‌‌​​​‌​‌‌​​‌​‌‌‌‌‌‌‌‌​‌​​‌​‌‌​​​‌‌‌​‌‌​‌​​​‌​‌​​​‌‌​​​​​​‌​‌​‌​‌‌​‌‌​‌‌‌​‌​​​‌​‌​​‌⁠

Wire geometry is built once in view space and cached; panning and zooming only change
the GPU matrix, so redraws cost one batch draw instead of rebuilding every curve in Python.
"""

from collections import defaultdict
import json
from math import hypot

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from .graph import RELATIONS


_cache = {}
_CACHE_SALT = 0x5bae2cbfd2c768a302adba29
# Rubber band shown while dragging a new relationship, in region pixels.
drag_line = None


def load_wires(tree):
    """Wire records: [source node, target node, kind, details, refs]."""
    try:
        records = json.loads(tree.wires) if tree.wires else []
    except ValueError:
        return []
    return [(*record, [], [])[:5] if len(record) < 5 else record for record in records]


def segments(wires, centers, spacing=4.0):
    """Yield (start, end, kind, wire index) for each record, offsetting parallel wires."""
    pairs = defaultdict(list)
    for index, (source, target, kind, *_rest) in enumerate(wires):
        if source in centers and target in centers:
            pairs[tuple(sorted((source, target)))].append((source, target, kind, index))
    for (first, second), connections in pairs.items():
        x1, y1, right = centers[first]
        x2, y2, _ = centers[second]
        if first == second:
            for i, (_, _, kind, index) in enumerate(connections):
                yield (right - 2, y1 + i * spacing), (right + 16, y1 + i * spacing), kind, index
            continue
        dx, dy = x2 - x1, y2 - y1
        distance = hypot(dx, dy)
        if distance == 0:
            continue
        for i, (source, _, kind, index) in enumerate(connections):
            offset = spacing * (i - (len(connections) - 1) / 2)
            ox, oy = -dy * offset / distance, dx * offset / distance
            start, end = (x1 + ox, y1 + oy), (x2 + ox, y2 + oy)
            if source != first:
                start, end = end, start
            yield start, end, kind, index


def circuit_route(start, end, radius_limit=18.0):
    x1, y1 = start
    x2, y2 = end
    dx, dy = abs(x2 - x1), abs(y2 - y1)
    if min(dx, dy) <= 1:
        return [start, end]
    if dx >= dy:
        mid = (x1 + x2) / 2
        corners = ((mid, y1), (mid, y2))
    else:
        mid = (y1 + y2) / 2
        corners = ((x1, mid), (x2, mid))
    radius = min(radius_limit, dx / 4, dy / 4)
    path = [start]
    route = (start, *corners, end)
    for before, corner, after in zip(route, route[1:], route[2:]):
        x, y = corner
        d1 = hypot(before[0] - x, before[1] - y)
        d2 = hypot(after[0] - x, after[1] - y)
        entry = (x + (before[0] - x) * radius / d1, y + (before[1] - y) * radius / d1)
        exit_ = (x + (after[0] - x) * radius / d2, y + (after[1] - y) * radius / d2)
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            s = 1 - t
            path.append((
                s * s * entry[0] + 2 * s * t * x + t * t * exit_[0],
                s * s * entry[1] + 2 * s * t * y + t * t * exit_[1],
            ))
    path.append(end)
    return path


def bezier_route(start, end, samples=16):
    x1, y1 = start
    x2, y2 = end
    handle = max(40.0, abs(x2 - x1) / 2)
    direction = 1 if x2 >= x1 else -1
    c1 = (x1 + handle * direction, y1)
    c2 = (x2 - handle * direction, y2)
    path = []
    for i in range(samples + 1):
        t = i / samples
        s = 1 - t
        path.append((
            s ** 3 * x1 + 3 * s * s * t * c1[0] + 3 * s * t * t * c2[0] + t ** 3 * x2,
            s ** 3 * y1 + 3 * s * s * t * c1[1] + 3 * s * t * t * c2[1] + t ** 3 * y2,
        ))
    return path


ROUTES = {
    "CIRCUIT": circuit_route,
    "BEZIER": bezier_route,
    "STRAIGHT": lambda start, end: [start, end],
}


def arrow(path, size=7.0):
    """A small chevron two thirds along the path that points at the target."""
    lengths = [hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:])]
    total = sum(lengths)
    if total < size * 4:
        return []
    goal = total * 0.66
    for (a, b), length in zip(zip(path, path[1:]), lengths):
        if goal <= length and length > 0:
            t = goal / length
            tip = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            back = (tip[0] - ux * size, tip[1] - uy * size)
            left = (back[0] - uy * size * 0.6, back[1] + ux * size * 0.6)
            right = (back[0] + uy * size * 0.6, back[1] - ux * size * 0.6)
            return [(left, tip), (right, tip)]
        goal -= length
    return []


def node_centers(tree, scale):
    from .editor import NODE_ID

    centers = {}
    for node in tree.nodes:
        if node.bl_idname != NODE_ID:
            continue
        x, y = node.location_absolute
        # Anchor every wire behind the title bar, even when the card is expanded.
        centers[node.name] = ((x + node.width / 2) * scale, (y - 10) * scale, (x + node.width) * scale)
    return centers


def geometry(tree, scale):
    """Cached view-space polylines: {"paths": [(points, index)], "batch": ...}."""
    centers = node_centers(tree, scale)
    key = (_CACHE_SALT, tree.wires, tree.wire_style, scale, tuple(sorted(centers.items())))
    cached = _cache.get(tree.session_uid)
    if cached and cached["key"] == key:
        return cached
    wires = load_wires(tree)
    route = ROUTES.get(tree.wire_style, circuit_route)
    paths = []
    positions = []
    colors = []
    for start, end, kind, index in segments(wires, centers, 4.0 * scale):
        path = route(start, end)
        paths.append((path, index))
        color = RELATIONS.get(kind, ("", (1, 1, 1, 1)))[1]
        pieces = list(zip(path, path[1:])) + arrow(path, 7.0 * scale)
        for first, second in pieces:
            positions.extend(((*first, 0.0), (*second, 0.0)))
            colors.extend((color, color))
    shader = gpu.shader.from_builtin("POLYLINE_SMOOTH_COLOR")
    batch = batch_for_shader(shader, "LINES", {"pos": positions, "color": colors}) if positions else None
    cached = {"key": key, "paths": paths, "batch": batch, "shader": shader}
    _cache[tree.session_uid] = cached
    return cached


def view_transform(region):
    """Affine map view → region pixels as (scale x, scale y, offset x, offset y)."""
    view2d = region.view2d
    x0, y0 = view2d.region_to_view(0, 0)
    x1, y1 = view2d.region_to_view(region.width, region.height)
    sx = region.width / (x1 - x0) if x1 != x0 else 1.0
    sy = region.height / (y1 - y0) if y1 != y0 else 1.0
    return sx, sy, -x0 * sx, -y0 * sy


def point_segment_distance(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length))
    return hypot(p[0] - (ax + dx * t), p[1] - (ay + dy * t))


def pick_wire(tree, region, x, y, scale, tolerance_px=8.0):
    """Index of the wire nearest the region point, or -1."""
    sx, _sy, ox, oy = view_transform(region)
    view_point = region.view2d.region_to_view(x, y)
    tolerance = tolerance_px / sx if sx else tolerance_px
    best, best_index = tolerance, -1
    for path, index in geometry(tree, scale)["paths"]:
        for a, b in zip(path, path[1:]):
            distance = point_segment_distance(view_point, a, b)
            if distance < best:
                best, best_index = distance, index
    return best_index


def draw_polyline_batch(shader, batch, width, region):
    shader.bind()
    shader.uniform_float("lineWidth", width)
    shader.uniform_float("viewportSize", (region.width, region.height))
    batch.draw(shader)


def draw_wires():
    # Import here so Blender registration can load the editor and renderer together.
    from .editor import editor_tree

    context = bpy.context
    tree = editor_tree(context)
    region = context.region
    if not tree or not region:
        return
    scale = context.preferences.system.ui_scale
    cached = geometry(tree, scale) if tree.wires else None
    sx, sy, ox, oy = view_transform(region)
    if cached and cached["batch"]:
        with gpu.matrix.push_pop():
            gpu.matrix.translate((ox, oy))
            gpu.matrix.scale((sx, sy))
            draw_polyline_batch(cached["shader"], cached["batch"], 1.6 * scale, region)
            highlight = [path for path, index in cached["paths"] if index == tree.active_wire]
            if highlight:
                positions = [(*point, 0.0) for path in highlight
                             for pair in zip(path, path[1:]) for point in pair]
                batch = batch_for_shader(cached["shader"], "LINES", {
                    "pos": positions, "color": [(1.0, 1.0, 1.0, 1.0)] * len(positions),
                })
                draw_polyline_batch(cached["shader"], batch, 3.5 * scale, region)
    if drag_line:
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINES", {"pos": [(*drag_line[0], 0.0), (*drag_line[1], 0.0)]})
        shader.bind()
        shader.uniform_float("lineWidth", 2.5 * scale)
        shader.uniform_float("viewportSize", (region.width, region.height))
        shader.uniform_float("color", (1.0, 1.0, 1.0, 0.9))
        batch.draw(shader)


def clear_cache():
    _cache.clear()
