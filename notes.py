# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Aomay Huang
# Schematic View for Blender. Modified versions must say so and keep this notice.
"""Sticky notes in the Schematic View: coloured frames with a title and a few lines of text.⁠​‌​‌‌​‌‌‌​‌​‌‌‌​​​‌​‌‌​​‌​‌‌‌‌‌‌‌‌​‌​​‌​‌‌​​​‌‌‌​‌‌​‌​​​‌​‌​​​‌‌​​​​​​‌​‌​‌​‌‌​‌‌​‌‌‌​‌​​​‌​‌​​‌⁠

Notes are ordinary Blender frame nodes (shown behind the cards) whose text lives in a Text
datablock, so they are saved with the .blend and survive graph refreshes and re-layouts.
"""

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel

from .editor import NODE_ID, SchematicOperator, editor_tree


NOTE_KEY = "sv_note"
PADDING = 40
COLORS = {
    "YELLOW": ("Yellow", (0.55, 0.48, 0.18)),
    "BLUE": ("Blue", (0.20, 0.32, 0.55)),
    "GREEN": ("Green", (0.22, 0.45, 0.28)),
    "RED": ("Red", (0.55, 0.22, 0.22)),
    "PURPLE": ("Purple", (0.40, 0.27, 0.55)),
    "GREY": ("Grey", (0.30, 0.30, 0.32)),
}


def is_note(node):
    return node is not None and node.bl_idname == "NodeFrame" and node.get(NOTE_KEY) is not None


def notes(tree):
    return [node for node in tree.nodes if is_note(node)]


def active_note(tree):
    node = tree.nodes.active
    return node if is_note(node) and node.select else None


def card_rect(node, scale=1.0):
    """(left, top, right, bottom) of an entity card in node space."""
    x, y = node.location_absolute
    width = node.dimensions.x / scale if node.dimensions.x else node.width
    height = node.dimensions.y / scale if node.dimensions.y else (30 if node.hide else 120)
    return x, y, x + width, y - height


def fit(frame, nodes, scale=1.0):
    rects = [card_rect(node, scale) for node in nodes]
    left = min(r[0] for r in rects) - PADDING
    top = max(r[1] for r in rects) + PADDING + 30  # room for the title
    right = max(r[2] for r in rects) + PADDING
    bottom = min(r[3] for r in rects) - PADDING
    frame.location = (left, top)
    frame.width = right - left
    frame.height = top - bottom


def create_note(tree, title, color_key="YELLOW", nodes=(), center=(0.0, 0.0), scale=1.0):
    frame = tree.nodes.new("NodeFrame")
    frame[NOTE_KEY] = 1
    frame.shrink = False
    frame.label = title
    frame.label_size = 20
    frame.use_custom_color = True
    frame.color = COLORS[color_key][1]
    text = bpy.data.texts.new(f"Note · {title}")
    text.write("Describe what this part does")
    frame.text = text
    frame.select = False
    if nodes:
        fit(frame, nodes, scale)
    else:
        frame.width, frame.height = 700, 360
        frame.location = (center[0] - 350, center[1] + 180)
    return frame


def remove_note(tree, frame):
    text = frame.text
    tree.nodes.remove(frame)
    still_used = any(note.text == text for group in bpy.data.node_groups for note in group.nodes
                     if note.bl_idname == "NodeFrame")
    if text and not still_used and text.name.startswith("Note · "):
        bpy.data.texts.remove(text)


class SCHEMATIC_OT_note_add(SchematicOperator, Operator):
    bl_idname = "schematic.note_add"
    bl_label = "Add Note"
    bl_description = "Add a note around the selected nodes, or in the middle of the view"
    bl_options = {"REGISTER", "UNDO"}

    title: StringProperty(name="Title", default="Note")
    color: EnumProperty(name="Color", items=[(key, name, "") for key, (name, _) in COLORS.items()])

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        tree = editor_tree(context)
        scale = context.preferences.system.ui_scale
        selected = [node for node in tree.nodes if node.select and node.bl_idname == NODE_ID]
        center = (0.0, 0.0)
        region = context.region
        if region and region.type == "WINDOW":
            cx, cy = region.view2d.region_to_view(region.width / 2, region.height / 2)
            center = (cx / scale, cy / scale)
        frame = create_note(tree, self.title.strip() or "Note", self.color, selected, center, scale)
        for node in tree.nodes:
            node.select = node == frame
        tree.nodes.active = frame
        return {"FINISHED"}


class SCHEMATIC_OT_note_edit(SchematicOperator, Operator):
    bl_idname = "schematic.note_edit"
    bl_label = "Edit Note"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(items=[
        ("FIT", "Fit to Selected", "Resize the note around the selected nodes"),
        ("ADD_LINE", "Add Line", "Add a line of text"),
        ("REMOVE_LINE", "Remove Line", "Remove this line"),
        ("COLOR", "Color", "Change the note colour"),
        ("DELETE", "Delete Note", "Delete this note"),
        ("FOCUS", "Show", "Select the note and frame it in the view"),
    ])
    note: StringProperty(options={"HIDDEN"})
    line: IntProperty(default=-1, options={"HIDDEN"})
    color: EnumProperty(items=[(key, name, "") for key, (name, _) in COLORS.items()], options={"HIDDEN"})

    def execute(self, context):
        tree = editor_tree(context)
        frame = tree.nodes.get(self.note) if self.note else active_note(tree)
        if not is_note(frame):
            return {"CANCELLED"}
        text = frame.text
        if self.action == "FIT":
            selected = [node for node in tree.nodes if node.select and node.bl_idname == NODE_ID]
            if not selected:
                self.report({"WARNING"}, "Select the nodes this note should cover")
                return {"CANCELLED"}
            fit(frame, selected, context.preferences.system.ui_scale)
        elif self.action == "ADD_LINE" and text:
            text.cursor_set(len(text.lines) - 1, character=len(text.lines[-1].body))
            text.write("\n")
        elif self.action == "REMOVE_LINE" and text and 0 <= self.line < len(text.lines):
            lines = [line.body for line in text.lines]
            del lines[self.line]
            text.clear()
            text.write("\n".join(lines))
        elif self.action == "COLOR":
            frame.color = COLORS[self.color][1]
        elif self.action == "DELETE":
            remove_note(tree, frame)
        elif self.action == "FOCUS":
            for node in tree.nodes:
                node.select = node == frame
            tree.nodes.active = frame
            if bpy.ops.node.view_selected.poll():
                bpy.ops.node.view_selected()
        context.area.tag_redraw()
        return {"FINISHED"}


class SCHEMATIC_PT_notes(Panel):
    bl_idname = "SCHEMATIC_PT_notes"
    bl_label = "Notes"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Schematic"

    @classmethod
    def poll(cls, context):
        tree = editor_tree(context)
        return bool(tree and tree.scene == context.scene)

    def draw(self, context):
        tree = editor_tree(context)
        layout = self.layout
        layout.operator("schematic.note_add", icon="ADD")
        frame = active_note(tree)
        if frame:
            box = layout.box()
            box.prop(frame, "label", text="Title")
            row = box.row(align=True)
            for key, (name, color) in COLORS.items():
                op = row.operator("schematic.note_edit", text="", icon="RADIOBUT_OFF" if tuple(
                    round(c, 2) for c in frame.color) != color else "RADIOBUT_ON")
                op.action, op.color, op.note = "COLOR", key, frame.name
            row = box.row(align=True)
            row.prop(frame, "label_size", text="Title Size")
            text = frame.text
            if text:
                column = box.column(align=True)
                for index, line in enumerate(text.lines):
                    row = column.row(align=True)
                    row.prop(line, "body", text="")
                    op = row.operator("schematic.note_edit", text="", icon="X")
                    op.action, op.note, op.line = "REMOVE_LINE", frame.name, index
                op = column.operator("schematic.note_edit", text="Add Line", icon="ADD")
                op.action, op.note = "ADD_LINE", frame.name
            row = box.row(align=True)
            op = row.operator("schematic.note_edit", text="Fit to Selected", icon="FULLSCREEN_ENTER")
            op.action, op.note = "FIT", frame.name
            op = row.operator("schematic.note_edit", text="", icon="TRASH")
            op.action, op.note = "DELETE", frame.name
        all_notes = notes(tree)
        if all_notes:
            column = layout.column(align=True)
            for note in all_notes:
                row = column.row(align=True)
                op = row.operator("schematic.note_edit", text=note.label or note.name, icon="TEXT",
                                  depress=note == frame)
                op.action, op.note = "FOCUS", note.name
        else:
            layout.label(text="Ctrl+J: note around selected nodes", icon="INFO")


CLASSES = (SCHEMATIC_OT_note_add, SCHEMATIC_OT_note_edit, SCHEMATIC_PT_notes)
