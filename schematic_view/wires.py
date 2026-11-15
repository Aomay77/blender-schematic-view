"""Draw schematic relationships behind Blender's node cards."""

from collections import defaultdict
import json
from math import hypot

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from .graph import RELATIONS


def segments(wires, centers):
    pairs = defaultdict(list)
    for source, target, kind in wires:
        if source in centers and target in centers:
            pairs[tuple(sorted((source, target)))].append((source, target, kind))
    for (first, second), connections in pairs.items():
        x1, y1, right = centers[first]
        x2, y2, _ = centers[second]
        if first == second:
            for i, (_, _, kind) in enumerate(connections):
                yield (right - 2, y1 + i * 3), (right + 13, y1 + i * 3), kind
            continue
        dx, dy = x2 - x1, y2 - y1
        distance = hypot(dx, dy)
        if distance == 0:
            continue
        for i, (source, _, kind) in enumerate(connections):
            offset = 2.5 * (i - (len(connections) - 1) / 2)
            ox, oy = -dy * offset / distance, dx * offset / distance
            start, end = (x1 + ox, y1 + oy), (x2 + ox, y2 + oy)
            if source != first:
                start, end = end, start
            yield start, end, kind


def rounded_route(start, end):
    x1, y1 = start
    x2, y2 = end
    dx, dy = abs(x2 - x1), abs(y2 - y1)
    if min(dx, dy) <= 1:
        return (start, end)
    if dx >= dy:
        mid = (x1 + x2) / 2
        corners = ((mid, y1), (mid, y2))
    else:
        mid = (y1 + y2) / 2
        corners = ((x1, mid), (x2, mid))
    radius = min(18.0, dx / 4, dy / 4)
    path = [start]
    route = (start, *corners, end)
    for before, corner, after in zip(route, route[1:], route[2:]):
        x, y = corner
        d1 = hypot(before[0] - x, before[1] - y)
        d2 = hypot(after[0] - x, after[1] - y)
        entry = (x + (before[0] - x) * radius / d1, y + (before[1] - y) * radius / d1)
        exit = (x + (after[0] - x) * radius / d2, y + (after[1] - y) * radius / d2)
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            s = 1 - t
            path.append((
                s * s * entry[0] + 2 * s * t * x + t * t * exit[0],
                s * s * entry[1] + 2 * s * t * y + t * t * exit[1],
            ))
    path.append(end)
    return path


def draw_wires():
    # Import here so Blender registration can load the editor and renderer together.
    from .editor import NODE_ID, editor_tree

    context = bpy.context
    tree = editor_tree(context)
    if not tree or not tree.wires or not context.region:
        return
    region = context.region
    scale = context.preferences.system.ui_scale
    to_region = region.view2d.view_to_region
    centers = {}
    for node in tree.nodes:
        if node.bl_idname != NODE_ID:
            continue
        x, y = node.location_absolute
        # Anchor every wire behind the title bar, even when the card is expanded.
        cx, cy = to_region((x + node.width / 2) * scale, (y - 10) * scale, clip=False)
        right, _ = to_region((x + node.width) * scale, y * scale, clip=False)
        centers[node.name] = (cx, cy, right)

    positions = []
    colors = []
    for start, end, kind in segments(json.loads(tree.wires), centers):
        path = rounded_route(start, end)
        color = RELATIONS[kind][1]
        for first, second in zip(path, path[1:]):
            positions.extend(((*first, 0.0), (*second, 0.0)))
            colors.extend((color, color))
    if positions:
        shader = gpu.shader.from_builtin("POLYLINE_SMOOTH_COLOR")
        batch = batch_for_shader(shader, "LINES", {"pos": positions, "color": colors})
        shader.bind()
        shader.uniform_float("lineWidth", 1.6 * scale)
        shader.uniform_float("viewportSize", (region.width, region.height))
        batch.draw(shader)
