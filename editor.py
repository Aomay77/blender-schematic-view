"""Native Blender node editor for the projected scene relationships."""

from collections import defaultdict
import json

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Menu, Node, NodeTree, Operator, Panel, PropertyGroup

from . import selection, viewport
from .floating import place_new_window, thread_windows
from .graph import (Entity, NODE_TYPES, RELATIONS, collect, is_scene_scope, scope_objects, scoped_graph,
                    signature, visible_graph)
from .guide import swatch_icon
from .layout import COLUMN, ROW, arrange, name_side


TREE_ID = "SCHEMATIC_NT_scene"
NODE_ID = "SCHEMATIC_ND_entity"
VERSION = "0.3.0"


def editor_tree(context):
    space = context.space_data
    if space and space.type == "NODE_EDITOR":
        tree = space.edit_tree
        if tree and tree.bl_idname == TREE_ID:
            return tree
    return None


def frame_after_draw(context, tree, area=None, window=None):
    if bpy.app.background or not tree.nodes:
        return
    window = window or context.window
    area = area or context.area
    attempts = 0

    def frame():
        nonlocal attempts
        try:
            screen = window.screen
            if area not in screen.areas[:] or area.type != "NODE_EDITOR":
                return None
            if area.spaces.active.edit_tree != tree or not tree.nodes:
                return None
            region = next((region for region in area.regions if region.type == "WINDOW"), None)
            if region and region.width > 1 and any(node.dimensions.x > 0 for node in tree.nodes):
                with bpy.context.temp_override(window=window, area=area, region=region):
                    if bpy.ops.node.view_all.poll():
                        bpy.ops.node.view_all()
                        return None
            attempts += 1
            return 0.1 if attempts < 10 else None
        except ReferenceError:
            return None

    bpy.app.timers.register(frame, first_interval=0.1)


def node_entity(node):
    if node and node.bl_idname == NODE_ID and node.object_ref:
        entity = Entity(node.object_ref, node.bone_name)
        if entity.owner is not None:
            return entity
    return None


def active_entity(tree):
    node = tree.nodes.active
    return node_entity(node) if node and node.select else None


def marked_entity(tree):
    if tree.source_object:
        entity = Entity(tree.source_object, tree.source_bone)
        if entity.owner is not None:
            return entity
    return None


SIDE_TINTS = {1: (0.20, 0.42, 0.95), -1: (0.95, 0.25, 0.25)}  # Left blue, right red.
SIDE_TINT_AMOUNT = 0.35


def enum_items(mapping):
    return [(key, name, name, 1 << index)
            for index, (key, (name, _color)) in enumerate(mapping.items())]


_suspend_updates = False


def filters_changed(tree, context):
    if not _suspend_updates and tree.scene is not None and tree.layout_revision:
        refresh(tree, sync_viewport=True)


def viewport_option_changed(tree, context):
    if tree.hide_in_viewport:
        refresh(tree, sync_viewport=True)
    else:
        viewport.restore(tree)


class SCHEMATIC_PG_scope_object(PropertyGroup):
    object_ref: PointerProperty(type=bpy.types.Object)


class SCHEMATIC_NT_scene(NodeTree):
    bl_idname = TREE_ID
    bl_label = "Schematic View"
    bl_icon = "NODETREE"

    scene: PointerProperty(type=bpy.types.Scene)
    rig: PointerProperty(type=bpy.types.Object)
    tab_title: StringProperty(name="Tab Name")
    scope_mode: EnumProperty(items=[
        ("SELECTION", "Selection", "Selected objects and their direct relationships"),
        ("SCENE", "Scene", "All objects and bones in the scene"),
    ], options={"HIDDEN"})
    scope_objects: CollectionProperty(type=SCHEMATIC_PG_scope_object)
    source_object: PointerProperty(type=bpy.types.Object)
    source_bone: StringProperty()
    layout_revision: IntProperty(options={"HIDDEN"})
    wires: StringProperty(options={"HIDDEN"})
    hidden_layout: StringProperty(options={"HIDDEN"})
    viewport_hidden: CollectionProperty(type=viewport.SCHEMATIC_PG_viewport_hidden)
    show_note: BoolProperty(name="Show Quick Controls", default=True)
    node_types: EnumProperty(
        name="Node Types", items=enum_items(NODE_TYPES),
        options={"ENUM_FLAG"}, default=set(NODE_TYPES),
    )
    relation_types: EnumProperty(
        name="Relationships", items=enum_items(RELATIONS),
        options={"ENUM_FLAG"}, default=set(RELATIONS),
    )
    search: StringProperty(name="Name", description="Match object or bone names",
                          update=lambda self, context: filters_changed(self, context))
    bone_collection: StringProperty(
        name="Bone Collection", description="Match bone collection names",
        update=lambda self, context: filters_changed(self, context),
    )
    custom_property: StringProperty(
        name="Custom Property", description="Match object or bone custom property names",
        update=lambda self, context: filters_changed(self, context),
    )
    visible_only: BoolProperty(
        name="Visible in View Layer Only",
        description="Hide objects and bones that are hidden in the active view layer",
        update=lambda self, context: filters_changed(self, context),
    )
    display_mode: EnumProperty(
        name="Mode", default="ALL",
        items=[
            ("ALL", "All", "Show every relationship"),
            ("HIERARCHY", "Hierarchy", "Parent / child tree only, like 3ds Max Hierarchy Mode"),
            ("REFERENCES", "References", "Constraints, drivers, modifiers and instances, like 3ds Max References Mode"),
        ],
        update=lambda self, context: filters_changed(self, context),
    )
    layout_style: EnumProperty(
        name="Arrange", default="SHAPE",
        items=[
            ("SHAPE", "Skeleton Shape", "Place bones where they are in the rest pose, for any creature"),
            ("HUMAN", "Body (by Names)", "Group bones by body-part names such as arm, leg, head"),
            ("HORIZONTAL", "Tree →", "Hierarchy tree from left to right"),
            ("VERTICAL", "Tree ↓", "Hierarchy tree from top to bottom"),
        ],
    )
    shape_view: EnumProperty(
        name="View", default="AUTO",
        items=[
            ("AUTO", "Auto", "Front for upright rigs, Side for quadrupeds, Top for flat rigs"),
            ("FRONT", "Front", "Look at the rig from the front"),
            ("SIDE", "Side", "Look at the rig from the side"),
            ("TOP", "Top", "Look at the rig from above"),
        ],
    )
    wire_style: EnumProperty(
        name="Wires", default="CIRCUIT",
        items=[
            ("CIRCUIT", "Circuit", "Right-angled wires with rounded corners"),
            ("BEZIER", "Bezier", "Curved wires"),
            ("STRAIGHT", "Line", "Straight wires"),
        ],
    )
    side_tint: BoolProperty(
        name="Tint Left / Right", default=True,
        description="Tint left bones blue and right bones red so the two sides are easy to tell apart",
        update=lambda self, context: filters_changed(self, context),
    )
    side_labels: StringProperty(options={"HIDDEN"})
    active_wire: IntProperty(default=-1, options={"HIDDEN"})
    graph_signature: StringProperty(options={"HIDDEN"})
    auto_refresh: BoolProperty(
        name="Auto Update", default=True,
        description="Rebuild the graph when relationships change in the scene",
    )
    sync_selection: BoolProperty(
        name="Sync Selection", default=True,
        description="Select clicked nodes in the 3D Viewport",
    )
    hide_in_viewport: BoolProperty(
        name="Filters Hide in Viewport", default=False,
        description="Node filters also hide the filtered objects and bones in the 3D Viewport",
        update=lambda self, context: viewport_option_changed(self, context),
    )


class SCHEMATIC_ND_entity(Node):
    bl_idname = NODE_ID
    bl_label = "Scene Entity"
    bl_width_default = 224

    object_ref: PointerProperty(type=bpy.types.Object)
    bone_name: StringProperty()
    caption: StringProperty()
    details: StringProperty()

    @classmethod
    def poll(cls, tree):
        return tree.bl_idname == TREE_ID

    def draw_buttons(self, context, layout):
        layout.label(text=self.caption, icon="BONE_DATA" if self.bone_name else "OBJECT_DATA")
        entity = node_entity(self)
        if entity and entity.bone:
            layout.label(text=entity.obj.name, icon="ARMATURE_DATA")
            groups = ", ".join(group.name for group in entity.owner.bone.collections)
            if groups:
                layout.label(text=groups, icon="GROUP_BONE")
        if (self.object_ref and self.object_ref == self.id_data.source_object
                and self.bone_name == self.id_data.source_bone):
            layout.label(text="Relationship Source", icon="PINNED")
        if not entity:
            return
        # Like an expanded 3ds Max node: list what is stacked on this object or bone.
        owner = entity.owner
        if not entity.bone:
            for modifier in entity.obj.modifiers[:6]:
                layout.label(text=modifier.name, icon="MODIFIER_ON" if modifier.show_viewport else "MODIFIER_OFF")
            if len(entity.obj.modifiers) > 6:
                layout.label(text=f"+{len(entity.obj.modifiers) - 6} modifiers")
            materials = [slot.material.name for slot in entity.obj.material_slots if slot.material]
            if materials:
                layout.label(text=", ".join(materials[:3]) + (" …" if len(materials) > 3 else ""), icon="MATERIAL")
            if entity.obj.data and entity.obj.data.users > 1:
                layout.label(text=f"Instance · {entity.obj.data.name} ×{entity.obj.data.users}", icon="LINKED")
        for constraint in owner.constraints[:6]:
            layout.label(text=constraint.name, icon="CONSTRAINT" if constraint.enabled else "HIDE_ON")
        if len(owner.constraints) > 6:
            layout.label(text=f"+{len(owner.constraints) - 6} constraints")
        custom = dict(custom_properties(entity))
        for name, value in list(custom.items())[:2]:
            layout.label(text=f"{name} = {value}")
        if len(custom) > 2:
            layout.label(text=f"{len(custom)} custom properties · see sidebar")


def custom_properties(entity):
    owner = entity.owner
    props = dict(owner.items())
    if entity.bone:
        props = dict(owner.bone.items()) | props
    return sorted((key, value) for key, value in props.items() if key != "_RNA_UI")


def layout_key(key):
    obj, bone = key
    return json.dumps((obj.library.filepath if obj.library else "", obj.name, bone))


def refresh(tree, reset_layout=False, view_layer=None, sync_viewport=False, collected=None):
    if tree.scene is None:
        return 0, 0
    collected = collected if collected is not None else collect(tree.scene)
    tree.graph_signature = str(signature(*collected))
    scoped = scoped_graph(tree, collected)
    if sync_viewport and tree.hide_in_viewport and (layer := view_layer or bpy.context.view_layer):
        viewport.sync(tree, layer, scoped[0])
    elif not tree.hide_in_viewport and tree.viewport_hidden:
        viewport.restore(tree)
    kinds, edges = visible_graph(tree, view_layer, scoped)
    migrating = tree.layout_revision == 0
    reset_layout = reset_layout or migrating
    previous = {entity.key: node for node in tree.nodes
                if (entity := node_entity(node)) is not None}
    hidden = json.loads(tree.hidden_layout) if tree.hidden_layout and not reset_layout else {}
    if not reset_layout:
        for key, node in previous.items():
            if key not in kinds and tree.scene.objects.get(key[0].name) == key[0]:
                hidden[layout_key(key)] = (*node.location, node.hide, node.width)
    # Forget saved cards for objects and bones that no longer exist.
    existing = {layout_key(key) for key in collected[0]}
    hidden = {key: value for key, value in hidden.items() if key in existing}
    tree.links.clear()
    for node in list(tree.nodes):
        entity = node_entity(node)
        if not entity or entity.key not in kinds or previous[entity.key] != node:
            tree.nodes.remove(node)

    nodes = {}
    for key, kind in kinds.items():
        obj, bone = key
        node = previous.get(key)
        if node is None:
            node = tree.nodes.new(NODE_ID)
            node.object_ref = obj
            node.bone_name = bone
            node.select = False
        if node.inputs:
            node.inputs.clear()
        if node.outputs:
            node.outputs.clear()
        if migrating or key not in previous:
            node.hide = True
        label = bone or obj.name
        if node.label != label:
            node.label = label
        node.caption, color = NODE_TYPES[kind]
        side = name_side(bone) if bone and tree.side_tint else 0
        if side:
            tint = SIDE_TINTS[side]
            color = tuple(c * (1 - SIDE_TINT_AMOUNT) + t * SIDE_TINT_AMOUNT for c, t in zip(color, tint))
        node.color = color
        node.use_custom_color = True
        nodes[key] = node

    details = defaultdict(list)
    wires = {}
    for edge in edges:
        source = edge.source.key
        target = edge.target.key
        label = RELATIONS[edge.kind][0]
        suffix = f" · {edge.detail}" if edge.detail else ""
        details[target].append(f"← {label} · {edge.source.name}{suffix}")
        details[source].append(f"→ {label} · {edge.target.name}{suffix}")
        record = wires.setdefault((nodes[source].name, nodes[target].name, edge.kind), ([], []))
        record[0].append(edge.detail)
        record[1].append(list(edge.ref))
    for key, node in nodes.items():
        node.details = "\n".join(details[key])

    # Lay out with every parent link, even when the relationship filter hides it.
    layout_edges = [edge for edge in scoped[1] if edge.source.key in kinds and edge.target.key in kinds]
    labels = []
    positions = arrange(kinds, layout_edges, tree.layout_style, tree.shape_view, labels)
    if reset_layout or not tree.side_labels:
        tree.side_labels = json.dumps(labels)
    occupied = {(round(node.location.x / COLUMN), round(node.location.y / ROW))
                for key, node in nodes.items() if key in previous and not reset_layout}
    restored = set()
    for key in nodes.keys() - previous.keys():
        saved = hidden.pop(layout_key(key), None)
        if saved is not None:
            x, y, collapsed, width = saved
            node = nodes[key]
            node.hide, node.width = collapsed, width
            cell = (round(x / COLUMN), round(y / ROW))
            if cell not in occupied:
                node.location = (x, y)
                restored.add(key)
                occupied.add(cell)
    # Reserve the intended slots so new nodes only shift when an existing card is in the way.
    reserved = {(x // COLUMN, y // ROW) for x, y in positions.values()}
    for key, (x, y) in positions.items():
        if key in restored or (not reset_layout and key in previous):
            continue
        col, row = x // COLUMN, y // ROW
        if (col, row) in occupied:
            while (col, row) in occupied or (col, row) in reserved:
                row -= 1
        nodes[key].location = (col * COLUMN, row * ROW)
        occupied.add((col, row))
    tree.wires = json.dumps([[*names, *record] for names, record in wires.items()])
    tree.active_wire = -1
    tree.hidden_layout = json.dumps(hidden)
    tree.layout_revision = 1
    selection.remember(tree)
    return len(nodes), len(edges)


def scene_tabs(scene):
    return [tree for tree in bpy.data.node_groups if tree.bl_idname == TREE_ID and tree.scene == scene]


def tab_title(tree):
    if tree.tab_title.strip():
        return tree.tab_title.strip()
    if is_scene_scope(tree):
        return "Entire Scene"
    anchors = scope_objects(tree)
    if not anchors:
        return "No Selection"
    suffix = f" +{len(anchors) - 1}" if len(anchors) > 1 else ""
    return f"{anchors[0].name}{suffix}"


def selected_objects(context):
    tree = editor_tree(context)
    if tree and tree.scene == context.scene:
        entities = selection.graph_selection(tree)
        if entities:
            return list(dict.fromkeys(entity.obj for entity in reversed(entities)))
    layer = context.view_layer
    selected = [obj for obj in layer.objects if obj.select_get(view_layer=layer)]
    active = layer.objects.active
    if active in selected:
        selected.remove(active)
        selected.insert(0, active)
    return selected


def tab_for_scope(scene, anchors, entire_scene=False):
    anchor_set = set(anchors)
    for tree in scene_tabs(scene):
        if is_scene_scope(tree) == entire_scene and (entire_scene or set(scope_objects(tree)) == anchor_set):
            return tree
    tree = bpy.data.node_groups.new("Schematic", TREE_ID)
    tree.scene = scene
    tree.use_fake_user = True
    tree.scope_mode = "SCENE" if entire_scene else "SELECTION"
    for obj in anchors:
        tree.scope_objects.add().object_ref = obj
    tree.name = f"Schematic · {tab_title(tree)}"
    return tree


def activate_tab(tree, window, area):
    # Only the tab being shown should own visibility changes in this scene.
    for other in scene_tabs(tree.scene):
        if other != tree and other.viewport_hidden:
            viewport.restore(other)
    refresh(tree, view_layer=window.view_layer, sync_viewport=True)
    if area.type != "NODE_EDITOR":
        area.type = "NODE_EDITOR"
    space = area.spaces.active
    space.tree_type = TREE_ID
    space.pin = True
    space.node_tree = tree
    space.show_region_ui = True
    area.tag_redraw()


class SCHEMATIC_OT_open(Operator):
    bl_idname = "schematic.open"
    bl_label = "Open Schematic View"

    def execute(self, context):
        scene = context.scene
        current = editor_tree(context)
        anchors = selected_objects(context)
        origin = context.window
        windows = context.window_manager.windows
        parent = origin.parent or origin
        window = next((candidate for candidate in windows
                       if candidate.parent == parent and candidate.scene == scene
                       and len(candidate.screen.areas) == 1
                       and candidate.screen.areas[0].type == "NODE_EDITOR"
                       and candidate.screen.areas[0].spaces.active.tree_type == TREE_ID), None)
        native_before = set()
        if window is None:
            before = {candidate.as_pointer() for candidate in windows}
            native_before = thread_windows()
            if not bpy.ops.wm.window_new.poll() or bpy.ops.wm.window_new() != {"FINISHED"}:
                self.report({"WARNING"}, "Could not open a floating Blender window")
                return {"CANCELLED"}
            window = next((candidate for candidate in windows
                           if candidate.as_pointer() not in before), None)
            if window is None or len(window.screen.areas) != 1:
                self.report({"WARNING"}, "Could not find the new editor window")
                return {"CANCELLED"}
        area = window.screen.areas[0]
        tree = current if current and current.scene == scene else tab_for_scope(scene, anchors)
        activate_tab(tree, window, area)
        place_new_window(native_before)
        frame_after_draw(context, tree, area, window)
        return {"FINISHED"}


class SchematicOperator:
    @classmethod
    def poll(cls, context):
        tree = editor_tree(context)
        return bool(tree and tree.scene == context.scene)


class SCHEMATIC_OT_back_to_view(Operator):
    bl_idname = "schematic.back_to_view"
    bl_label = "Back to 3D View"

    def execute(self, context):
        if context.window.parent and len(context.window.screen.areas) == 1:
            return bpy.ops.wm.window_close()
        context.area.type = "VIEW_3D"
        return {"FINISHED"}


class SCHEMATIC_OT_refresh(SchematicOperator, Operator):
    bl_idname = "schematic.refresh"
    bl_label = "Refresh / Apply Filters"
    bl_options = {"REGISTER", "UNDO"}

    reset_layout: BoolProperty(name="Auto Layout")

    def execute(self, context):
        tree = editor_tree(context)
        was_empty = not tree.nodes
        nodes, edges = refresh(tree, self.reset_layout, sync_viewport=True)
        if self.reset_layout or was_empty:
            frame_after_draw(context, tree)
        self.report({"INFO"}, f"{nodes} nodes · {edges} relationships")
        return {"FINISHED"}


class SCHEMATIC_OT_scope(SchematicOperator, Operator):
    bl_idname = "schematic.scope"
    bl_label = "Save Selection as Tab"
    bl_options = {"UNDO"}

    entire_scene: BoolProperty()

    def execute(self, context):
        anchors = [] if self.entire_scene else selected_objects(context)
        if not self.entire_scene and not anchors:
            self.report({"WARNING"}, "Select an object, armature or bone first")
            return {"CANCELLED"}
        tree = tab_for_scope(context.scene, anchors, self.entire_scene)
        associate_tab(context, tree)
        return {"FINISHED"}


def associate_tab(context, tree):
    activate_tab(tree, context.window, context.area)
    frame_after_draw(context, tree)


class SCHEMATIC_OT_switch_tab(SchematicOperator, Operator):
    bl_idname = "schematic.switch_tab"
    bl_label = "Switch Schematic Tab"
    bl_options = {"UNDO"}

    tree_name: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        tree = bpy.data.node_groups.get(self.tree_name)
        if not tree or tree.bl_idname != TREE_ID or tree.scene != context.scene:
            return {"CANCELLED"}
        associate_tab(context, tree)
        return {"FINISHED"}


class SCHEMATIC_MT_tabs(Menu):
    bl_idname = "SCHEMATIC_MT_tabs"
    bl_label = "Schematic Tabs"

    def draw(self, context):
        current = editor_tree(context)
        for tree in scene_tabs(context.scene):
            icon = "RADIOBUT_ON" if tree == current else "RADIOBUT_OFF"
            self.layout.operator("schematic.switch_tab", text=tab_title(tree), icon=icon).tree_name = tree.name
        self.layout.separator()
        self.layout.operator("schematic.scope", text="Entire Scene Tab", icon="SCENE_DATA").entire_scene = True


FILTER_GROUPS = {"NODE": ("node_types", NODE_TYPES), "RELATION": ("relation_types", RELATIONS)}


class SCHEMATIC_OT_toggle_filter(SchematicOperator, Operator):
    bl_idname = "schematic.toggle_filter"
    bl_label = "Toggle Category Visibility"
    bl_description = "Click to show or hide this category"
    bl_options = {"UNDO"}

    group: EnumProperty(items=[("NODE", "Node", ""), ("RELATION", "Relationship", "")])
    category: StringProperty()

    def execute(self, context):
        prop, categories = FILTER_GROUPS[self.group]
        if self.category not in categories:
            return {"CANCELLED"}
        tree = editor_tree(context)
        before = len(tree.nodes)
        setattr(tree, prop, getattr(tree, prop) ^ {self.category})
        refresh(tree, sync_viewport=self.group == "NODE")
        if not before and tree.nodes:
            frame_after_draw(context, tree)
        context.area.tag_redraw()
        return {"FINISHED"}


class SCHEMATIC_OT_reset_filters(SchematicOperator, Operator):
    bl_idname = "schematic.reset_filters"
    bl_label = "Reset Filters"
    bl_description = "Restore default filters and reveal items hidden by Schematic View"
    bl_options = {"UNDO"}

    def execute(self, context):
        global _suspend_updates
        tree = editor_tree(context)
        _suspend_updates = True
        try:
            tree.node_types = set(NODE_TYPES)
            tree.relation_types = set(RELATIONS)
            tree.search = tree.bone_collection = tree.custom_property = ""
            tree.visible_only = False
            tree.display_mode = "ALL"
        finally:
            _suspend_updates = False
        viewport.restore(tree)
        refresh(tree)
        frame_after_draw(context, tree)
        if tree.viewport_hidden:
            self.report({"WARNING"}, "Some viewport visibility settings could not be restored")
        return {"FINISHED"}


class SCHEMATIC_PT_launch(Panel):
    bl_idname = "SCHEMATIC_PT_launch"
    bl_label = f"Schematic View {VERSION}"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Schematic"

    def draw(self, context):
        self.layout.operator("schematic.open", icon="NODETREE")


class SCHEMATIC_PT_controls(Panel):
    bl_idname = "SCHEMATIC_PT_controls"
    bl_label = "Schematic View"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Schematic"

    @classmethod
    def poll(cls, context):
        return bool(editor_tree(context))

    def draw(self, context):
        tree = editor_tree(context)
        layout = self.layout
        if tree.scene != context.scene:
            layout.label(text="Graph belongs to another scene", icon="ERROR")
            layout.operator("schematic.open", icon="NODETREE")
            return
        layout.menu("SCHEMATIC_MT_tabs", text=tab_title(tree), icon="FILE_FOLDER")
        layout.operator("schematic.scope", text="Save Selection as Tab", icon="ADD")
        layout.prop(tree, "tab_title")
        if not is_scene_scope(tree) and not scope_objects(tree):
            layout.label(text="Select objects, then save a tab", icon="INFO")
        layout.row().prop(tree, "display_mode", expand=True)
        layout.prop(tree, "search", icon="VIEWZOOM")
        layout.prop(tree, "bone_collection")
        layout.prop(tree, "custom_property")
        layout.prop(tree, "visible_only")
        row = layout.row(align=True)
        row.operator("schematic.refresh", icon="FILE_REFRESH")
        row.operator("schematic.reset_filters", text="", icon="LOOP_BACK")
        row = layout.row(align=True)
        row.prop(tree, "layout_style", text="")
        if tree.layout_style == "SHAPE":
            row.prop(tree, "shape_view", text="")
        row.operator("schematic.refresh", text="Arrange", icon="SORTSIZE").reset_layout = True
        row = layout.row(align=True)
        row.operator("node.view_all", text="Frame All", icon="VIEWZOOM")
        row.operator("node.view_selected", text="Frame Selected")
        row = layout.row(align=True)
        row.operator("schematic.expand_selected", text="Expand", icon="TRIA_DOWN")
        row.operator("schematic.expand_selected", text="Collapse", icon="TRIA_RIGHT").collapse = True
        layout.menu("SCHEMATIC_MT_select", icon="RESTRICT_SELECT_OFF")
        source = marked_entity(tree)
        layout.label(text=f"Source · {source.name}" if source else "Source · —", icon="PINNED")
        active = active_entity(tree)
        if active:
            layout.label(text=f"Target · {active.name}", icon="RESTRICT_SELECT_OFF")
        if context.mode == "EDIT_ARMATURE":
            layout.operator("schematic.finish_bone_edit", icon="CHECKMARK")
        if context.window.parent and len(context.window.screen.areas) == 1:
            layout.operator("schematic.back_to_view", text="Close Floating Window", icon="X")
        else:
            layout.operator("schematic.open", text="Open Floating Window", icon="WINDOW")
            layout.operator("schematic.back_to_view", icon="VIEW3D")


class SCHEMATIC_PT_options(SchematicOperator, Panel):
    bl_idname = "SCHEMATIC_PT_options"
    bl_label = "Display & Behaviour"
    bl_parent_id = "SCHEMATIC_PT_controls"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        tree = editor_tree(context)
        layout = self.layout
        layout.prop(tree, "wire_style")
        layout.prop(tree, "side_tint")
        layout.prop(tree, "auto_refresh")
        layout.prop(tree, "sync_selection")
        layout.prop(tree, "hide_in_viewport")
        layout.prop(tree, "show_note")


def draw_filter_categories(layout, tree, group):
    prop, categories = FILTER_GROUPS[group]
    enabled = getattr(tree, prop)
    column = layout.column(align=True)
    for key, (label, _) in categories.items():
        operator = column.operator(
            "schematic.toggle_filter", text=label, icon_value=swatch_icon(group, key),
            depress=key in enabled,
        )
        operator.group, operator.category = group, key


class SCHEMATIC_PT_node_filters(SchematicOperator, Panel):
    bl_idname = "SCHEMATIC_PT_node_filters"
    bl_label = "Node Filters"
    bl_parent_id = "SCHEMATIC_PT_controls"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_filter_categories(self.layout, editor_tree(context), "NODE")


class SCHEMATIC_PT_relationship_filters(SchematicOperator, Panel):
    bl_idname = "SCHEMATIC_PT_relationship_filters"
    bl_label = "Relationship Filters"
    bl_parent_id = "SCHEMATIC_PT_controls"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_filter_categories(self.layout, editor_tree(context), "RELATION")


def wire_label(record):
    source, target, kind = record[:3]
    return f"{RELATIONS.get(kind, (kind,))[0]} · {source} → {target}"


class SCHEMATIC_PT_wire(SchematicOperator, Panel):
    bl_idname = "SCHEMATIC_PT_wire"
    bl_label = "Selected Wire"
    bl_parent_id = "SCHEMATIC_PT_controls"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"

    @classmethod
    def poll(cls, context):
        tree = editor_tree(context)
        return bool(tree and tree.active_wire >= 0)

    def draw(self, context):
        from .wires import load_wires

        tree = editor_tree(context)
        wires = load_wires(tree)
        if tree.active_wire >= len(wires):
            self.layout.label(text="Click a wire with Alt+Click")
            return
        record = wires[tree.active_wire]
        source, target = (tree.nodes.get(name) for name in record[:2])
        layout = self.layout
        layout.label(text=RELATIONS.get(record[2], (record[2],))[0], icon_value=swatch_icon("RELATION", record[2]))
        layout.label(text=f"{source.label if source else record[0]} → {target.label if target else record[1]}")
        for detail in record[3][:8]:
            if detail:
                layout.label(text=detail)
        row = layout.row(align=True)
        row.operator("schematic.focus_wire", text="Select Ends", icon="RESTRICT_SELECT_OFF").index = tree.active_wire
        row.operator("schematic.delete_wire", text="Unlink", icon="UNLINKED")


class SCHEMATIC_PT_list(SchematicOperator, Panel):
    bl_idname = "SCHEMATIC_PT_list"
    bl_label = "Relationship List"
    bl_parent_id = "SCHEMATIC_PT_controls"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        from .wires import load_wires

        tree = editor_tree(context)
        wires = load_wires(tree)
        layout = self.layout
        if not wires:
            layout.label(text="No visible relationships")
            return
        labels = {node.name: node.label for node in tree.nodes}
        column = layout.column(align=True)
        limit = 150
        for index, record in enumerate(wires[:limit]):
            source, target, kind = record[:3]
            text = f"{labels.get(source, source)} → {labels.get(target, target)}"
            column.operator(
                "schematic.focus_wire", text=text, icon_value=swatch_icon("RELATION", kind),
                depress=index == tree.active_wire,
            ).index = index
        if len(wires) > limit:
            layout.label(text=f"+{len(wires) - limit} more · use filters to narrow")


class SCHEMATIC_PT_details(Panel):
    bl_idname = "SCHEMATIC_PT_details"
    bl_label = "Node Properties & Relationships"
    bl_parent_id = "SCHEMATIC_PT_controls"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        tree = editor_tree(context)
        entity = active_entity(tree)
        if not entity:
            self.layout.label(text="Select a node to see its properties")
            return
        for name, value in custom_properties(entity):
            self.layout.label(text=f"{name} = {value}")
        details = tree.nodes.active.details.splitlines()
        if not details:
            self.layout.label(text="No visible relationships")
        for line in details:
            self.layout.label(text=line)


CLASSES = (
    viewport.SCHEMATIC_PG_viewport_hidden, SCHEMATIC_PG_scope_object, SCHEMATIC_NT_scene, SCHEMATIC_ND_entity,
    SCHEMATIC_OT_open, SCHEMATIC_OT_back_to_view, SCHEMATIC_OT_refresh, SCHEMATIC_OT_scope,
    SCHEMATIC_OT_switch_tab, SCHEMATIC_MT_tabs,
    SCHEMATIC_OT_toggle_filter, SCHEMATIC_OT_reset_filters, SCHEMATIC_PT_launch, SCHEMATIC_PT_controls,
    SCHEMATIC_PT_options, SCHEMATIC_PT_node_filters, SCHEMATIC_PT_relationship_filters,
    SCHEMATIC_PT_wire, SCHEMATIC_PT_list, SCHEMATIC_PT_details,
)
