"""Color swatches and the on-canvas controls note."""

import blf
import bpy
import bpy.utils.previews
import gpu
from gpu_extras.batch import batch_for_shader

from .graph import NODE_TYPES, RELATIONS


swatches = None


def register_swatches():
    global swatches
    swatches = bpy.utils.previews.new()
    for group, categories in (("NODE", NODE_TYPES), ("RELATION", RELATIONS)):
        for key, (_, color) in categories.items():
            preview = swatches.new(f"{group}:{key}")
            preview.icon_size = (16, 16)
            preview.icon_pixels_float = (*color[:3], 1.0) * 256


def unregister_swatches():
    global swatches
    if swatches is not None:
        bpy.utils.previews.remove(swatches)
        swatches = None


def swatch_icon(group, key):
    return swatches[f"{group}:{key}"].icon_id


NOTE = (
    ("Quick Controls", 14, (0.92, 0.94, 0.98, 1.0)),
    ("Wheel   Zoom   ·   MMB   Pan", 12, (0.78, 0.82, 0.88, 1.0)),
    ("B   Box Select   ·   G   Move", 12, (0.78, 0.82, 0.88, 1.0)),
    ("Home   Frame all nodes", 12, (0.78, 0.82, 0.88, 1.0)),
    ("Arrow   Expand properties", 12, (0.78, 0.82, 0.88, 1.0)),
    ("Right-click   Add / connect", 12, (0.78, 0.82, 0.88, 1.0)),
    ("Filters   Click a color to toggle", 12, (0.78, 0.82, 0.88, 1.0)),
    ("Reset Filters   Restore visibility", 12, (0.78, 0.82, 0.88, 1.0)),
)


def draw_note():
    from .editor import editor_tree

    context = bpy.context
    tree = editor_tree(context)
    region = context.region
    if not tree or not tree.show_note or not region:
        return
    scale = context.preferences.system.ui_scale
    left, top = 16 * scale, region.height - 14 * scale
    width, height = 222 * scale, 173 * scale
    if top < height + 10 * scale or region.width < width + 32 * scale:
        return
    right, bottom = left + width, top - height
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": (
        (left, bottom, 0), (right, bottom, 0), (right, top, 0), (left, top, 0),
    )})
    old_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind()
        shader.uniform_float("color", (0.06, 0.075, 0.10, 0.93))
        batch.draw(shader)
    finally:
        gpu.state.blend_set(old_blend)
    for index, (label, size, color) in enumerate(NOTE):
        blf.size(0, max(1, round(size * scale)))
        blf.color(0, *color)
        blf.position(0, left + 12 * scale, top - (23 + index * 19) * scale, 0)
        blf.draw(0, label)
