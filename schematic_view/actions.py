"""Apply native Blender edits from the node editor context menu."""

from collections import defaultdict
from contextlib import contextmanager
from numbers import Real

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator
from mathutils import Vector

from .editor import SchematicOperator, active_entity, editor_tree, marked_entity, node_entity, refresh
from .graph import Entity, collect


CONSTRAINTS = [
    ("COPY_TRANSFORMS", "Copy Transforms", "Copy location, rotation and scale"),
    ("COPY_LOCATION", "Copy Location", "Copy location"),
    ("COPY_ROTATION", "Copy Rotation", "Copy rotation"),
    ("COPY_SCALE", "Copy Scale", "Copy scale"),
    ("CHILD_OF", "Child Of", "Add a reversible parent constraint"),
    ("DAMPED_TRACK", "Damped Track", "Aim at the source"),
    ("IK", "Inverse Kinematics", "Use the source as an IK target for a pose bone"),
]
TRANSFORM_CHANNELS = {
    "location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
    "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale",
}
TRANSFORM_DEPENDENCIES = {"PARENT", "MEMBER", "CONSTRAINT", "ARMATURE"}


def would_cycle(scene, source, target, kinds):
    _, edges = collect(scene)
    affected = defaultdict(list)
    for edge in edges:
        if edge.kind in kinds:
            affected[edge.source].append(edge.target)
    seen = set()
    pending = [target]
    while pending:
        entity = pending.pop()
        if entity == source:
            return True
        if entity not in seen:
            seen.add(entity)
            pending.extend(affected[entity])
    return False


def validate_armature(context, obj):
    if obj.data.library or obj.data.override_library or not obj.is_editable:
        raise ValueError("Bone edits require a local, editable armature")
    if obj.data.users > 1:
        raise ValueError("Make the Armature data single-user before editing its bones")
    layer = context.view_layer
    if layer.objects.get(obj.name) != obj or not obj.visible_get(view_layer=layer):
        raise ValueError("Show the armature in the active view layer before editing bones")


@contextmanager
def edit_armature(context, obj):
    validate_armature(context, obj)
    layer = context.view_layer
    previous = layer.objects.active
    mode = previous.mode if previous else "OBJECT"
    if mode != "OBJECT" and (previous != obj or mode != "POSE"):
        raise ValueError("Use Object Mode or Pose Mode on this armature")
    if mode == "POSE":
        bpy.ops.object.mode_set(mode="OBJECT")
    selected = [item for item in layer.objects if item.select_get(view_layer=layer)]
    for item in selected:
        if item != obj:
            item.select_set(False, view_layer=layer)
    layer.objects.active = obj
    obj.select_set(True, view_layer=layer)
    try:
        bpy.ops.object.mode_set(mode="EDIT")
        yield obj.data.edit_bones
    finally:
        if obj.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        obj.select_set(obj in selected, view_layer=layer)
        for item in selected:
            item.select_set(True, view_layer=layer)
        layer.objects.active = previous
        if mode == "POSE":
            bpy.ops.object.mode_set(mode="POSE")


def set_parent(context, source, target, connected=False):
    if connected and not target.bone:
        raise ValueError("Join requires two bones")
    if target.bone:
        if not source.bone or source.obj != target.obj:
            raise ValueError("A bone can only be parented to a bone in its own armature. Use Child Of across objects.")
        with edit_armature(context, source.obj) as bones:
            child = bones[target.bone]
            parent = bones[source.bone]
            if child.parent != parent:
                child.use_connect = False
                child.parent = parent
            if connected:
                child.use_connect = True
        return

    obj = target.obj
    context.view_layer.update()
    world = obj.matrix_world.copy()
    obj.parent = source.obj
    obj.parent_type = "BONE" if source.bone else "OBJECT"
    obj.parent_bone = source.bone
    obj.matrix_parent_inverse.identity()
    context.view_layer.update()
    if not obj.constraints:
        # Use Blender's evaluated parent transform for both objects and bone parents.
        parent_matrix = obj.matrix_world @ obj.matrix_basis.inverted_safe()
        obj.matrix_parent_inverse = parent_matrix.inverted_safe()
    obj.matrix_world = world
    context.view_layer.update()


def add_constraint(context, source, target, constraint_type):
    if constraint_type == "IK" and not target.bone:
        raise ValueError("IK requires a target pose bone")
    constraints = target.owner.constraints
    constraint = constraints.new(constraint_type)
    try:
        constraint.target = source.obj
        constraint.subtarget = source.bone
        if constraint_type == "CHILD_OF":
            constraint.set_inverse_pending = True
        context.view_layer.update()
    except Exception:
        constraints.remove(constraint)
        raise


def channel(owner, path, index, writable=False):
    path = path.strip()
    if path.startswith('["') and path.endswith('"]'):
        key = bpy.utils.unescape_identifier(path[2:-2])
        value = owner.get(key)
    elif path.isidentifier() and path in owner.bl_rna.properties:
        if writable and owner.is_property_readonly(path):
            raise ValueError(f"Read-only channel: {path}")
        value = getattr(owner, path)
    else:
        raise ValueError('Enter a channel such as location or a custom property such as ["ik_fk"]')
    if isinstance(value, Real):
        return path, -1
    if value is not None and not isinstance(value, (str, bytes)) and hasattr(value, "__len__"):
        try:
            if isinstance(value[index], Real):
                return path, index
        except (IndexError, KeyError, TypeError):
            pass
    raise ValueError(f"Channel or numeric component not found: {path}[{index}]")


def full_path(entity, path):
    return entity.owner.path_from_id(path) if entity.bone else path


def add_driver(source, target, source_path, source_index, driven_path, driven_index):
    source_path, source_index = channel(source.owner, source_path, source_index)
    driven_path, driven_index = channel(target.owner, driven_path, driven_index, writable=True)
    source_full_path = full_path(source, source_path)
    driven_full_path = full_path(target, driven_path)
    if (source.obj == target.obj and source_full_path == driven_full_path
            and source_index == driven_index):
        raise ValueError("The source and driven channel are the same")
    if source_index >= 0:
        source_full_path += f"[{source_index}]"
    animation = target.obj.animation_data
    if animation and any(
        curve.data_path == driven_full_path and curve.array_index == max(0, driven_index)
        for curve in animation.drivers
    ):
        raise ValueError("This channel already has a driver")
    source.obj.update_tag()
    curve = target.owner.driver_add(driven_path, driven_index)
    try:
        driver = curve.driver
        driver.type = "AVERAGE"
        variable = driver.variables.new()
        variable.name = "source_value"
        variable.type = "SINGLE_PROP"
        variable.targets[0].id = source.obj
        variable.targets[0].data_path = source_full_path
        target.obj.update_tag()
    except Exception:
        target.owner.driver_remove(driven_path, driven_index)
        raise


def selected_entities(tree):
    selected = [entity for node in tree.nodes if node.select
                and (entity := node_entity(node)) is not None]
    target = active_entity(tree)
    if target in selected:
        selected.remove(target)
        selected.append(target)
    return selected


def selected_bones(tree):
    selected = selected_entities(tree)
    if selected and all(entity.bone and entity.obj == selected[0].obj for entity in selected):
        return selected
    return []


def select_edit_bones(bones, names):
    selected = set(names)
    for bone in bones:
        bone.select = bone.select_head = bone.select_tail = False
    for name in names:
        bone = bones[name]
        bone.select = bone.select_head = bone.select_tail = True
        if bone.use_connect and bone.parent and bone.parent.name not in selected:
            bone.parent.select_tail = True
    bones.active = bones[names[-1]]


class SCHEMATIC_OT_mark_source(SchematicOperator, Operator):
    bl_idname = "schematic.mark_source"
    bl_label = "Mark as Relationship Source"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = editor_tree(context)
        entity = active_entity(tree)
        if not entity:
            return {"CANCELLED"}
        tree.source_object, tree.source_bone = entity.key
        context.area.tag_redraw()
        return {"FINISHED"}


class SCHEMATIC_OT_connect(SchematicOperator, Operator):
    bl_idname = "schematic.connect"
    bl_label = "Create Relationship"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(items=[
        ("PARENT", "Parent Link", "Parent the selected node to the marked source"),
        ("JOIN", "Join / Connect Bones", "Connect the child head to the parent tail"),
        ("CONSTRAINT", "Constraint", "Add a constraint targeting the marked source"),
        ("DRIVER", "Driver", "Drive a numeric property from the marked source"),
    ])
    constraint_type: EnumProperty(name="Constraint", items=CONSTRAINTS)
    source_path: StringProperty(name="Source Property", default="location")
    source_index: IntProperty(name="Source Array Index", min=0, default=0)
    driven_path: StringProperty(name="Driven Property", default="location")
    driven_index: IntProperty(name="Driven Array Index", min=0, default=0)
    manual_source: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})
    constraint_object: StringProperty(name="Target Object")
    constraint_bone: StringProperty(name="Target Bone")
    reverse: BoolProperty(name="Swap Source and Driven Node", options={"SKIP_SAVE"})

    def endpoints(self, tree):
        selected = selected_entities(tree)
        if self.manual_source and len(selected) == 1:
            obj = tree.scene.objects.get(self.constraint_object)
            if obj:
                bone = self.constraint_bone if obj.pose else ""
                if not bone or obj.pose.bones.get(bone):
                    return Entity(obj, bone), selected[0]
            return None, selected[0]
        if len(selected) == 2:
            return tuple(reversed(selected)) if self.reverse else tuple(selected)
        if len(selected) == 1:
            return marked_entity(tree), selected[0]
        return None, None

    def invoke(self, context, event):
        if self.manual_source:
            tree = editor_tree(context)
            source = marked_entity(tree)
            self.constraint_object = self.constraint_bone = ""
            if source and source != active_entity(tree):
                self.constraint_object, self.constraint_bone = source.obj.name, source.bone
        return context.window_manager.invoke_props_dialog(self, width=430)

    def draw(self, context):
        tree = editor_tree(context)
        source, target = self.endpoints(tree)
        layout = self.layout
        if self.manual_source:
            layout.prop_search(self, "constraint_object", tree.scene, "objects")
            obj = tree.scene.objects.get(self.constraint_object)
            if obj and obj.pose:
                layout.prop_search(self, "constraint_bone", obj.pose, "bones")
        if not target:
            layout.label(text="Select one or two nodes", icon="ERROR")
            return
        if not self.manual_source and len(selected_entities(tree)) == 2:
            layout.prop(self, "reverse")
        if source:
            layout.label(text=f"{source.name} → {target.name}")
        if self.action == "CONSTRAINT":
            layout.prop(self, "constraint_type")
        elif self.action == "DRIVER":
            layout.label(text='Use location, scale, or ["custom_property"]')
            layout.prop(self, "source_path")
            layout.prop(self, "source_index")
            layout.prop(self, "driven_path")
            layout.prop(self, "driven_index")
        elif self.action == "JOIN":
            layout.label(text="Source = parent · driven node = child", icon="BONE_DATA")
            layout.label(text="The child's head snaps to the parent's tail")
        elif target.bone:
            layout.label(text="This edits the armature's rest hierarchy", icon="INFO")
        else:
            layout.label(text="This sets the target object's parent", icon="INFO")

    def execute(self, context):
        tree = editor_tree(context)
        source, target = self.endpoints(tree)
        if not source or not target:
            self.report({"ERROR"}, "Choose a source object or select two nodes")
            return {"CANCELLED"}
        if any(tree.scene.objects.get(entity.obj.name) != entity.obj for entity in (source, target)):
            self.report({"ERROR"}, "Both objects must be in this scene")
            return {"CANCELLED"}
        if not target.obj.is_editable:
            self.report({"ERROR"}, "The target object is read-only")
            return {"CANCELLED"}
        if context.mode not in {"OBJECT", "POSE"}:
            self.report({"ERROR"}, "Create relationships in Object or Pose Mode")
            return {"CANCELLED"}
        try:
            if self.action in {"CONSTRAINT", "PARENT", "JOIN"}:
                if would_cycle(tree.scene, source, target, TRANSFORM_DEPENDENCIES):
                    raise ValueError("This link would create a circular dependency")
            if self.action in {"PARENT", "JOIN"}:
                set_parent(context, source, target, connected=self.action == "JOIN")
            elif self.action == "CONSTRAINT":
                add_constraint(context, source, target, self.constraint_type)
            else:
                if (source != target and self.source_path.strip() in TRANSFORM_CHANNELS
                        and self.driven_path.strip() in TRANSFORM_CHANNELS
                        and would_cycle(tree.scene, source, target, TRANSFORM_DEPENDENCIES)):
                    raise ValueError("This transform driver would create a circular dependency")
                add_driver(source, target, self.source_path, self.source_index,
                           self.driven_path, self.driven_index)
        except (RuntimeError, TypeError, ValueError, AttributeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        refresh(tree)
        return {"FINISHED"}


class SCHEMATIC_OT_create_node(SchematicOperator, Operator):
    bl_idname = "schematic.create_node"
    bl_label = "Add Scene Entity"
    bl_options = {"UNDO"}

    kind: EnumProperty(items=[("EMPTY", "Empty", ""), ("BONE", "Bone", "")])
    entity_name: StringProperty(name="Name")
    bone_length: FloatProperty(name="Length", default=0.2, min=0.001, subtype="DISTANCE")
    deform: BoolProperty(name="Deform Bone")
    parent_empty: BoolProperty(name="Parent to Selected Node", default=True)

    def invoke(self, context, event):
        self.entity_name = "Bone" if self.kind == "BONE" else "Empty"
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        self.layout.prop(self, "entity_name")
        if self.kind == "BONE":
            self.layout.prop(self, "bone_length")
            self.layout.prop(self, "deform")
        else:
            self.layout.prop(self, "parent_empty")

    def execute(self, context):
        tree = editor_tree(context)
        selected = selected_entities(tree)
        if len(selected) != 1 or context.mode not in {"OBJECT", "POSE"}:
            self.report({"ERROR"}, "Select one node in Object Mode or Pose Mode")
            return {"CANCELLED"}
        source = selected[0]
        name = self.entity_name.strip() or ("Bone" if self.kind == "BONE" else "Empty")
        try:
            if self.kind == "BONE":
                if source.obj.type != "ARMATURE":
                    raise ValueError("Select an armature or one of its bones")
                with edit_armature(context, source.obj) as bones:
                    parent = bones.get(source.bone)
                    bone = bones.new(name)
                    try:
                        bone.use_deform = self.deform
                        if parent:
                            bone.head = parent.tail
                            bone.tail = bone.head + (parent.tail - parent.head).normalized() * self.bone_length
                            bone.parent = parent
                            for collection in parent.collections:
                                collection.assign(bone)
                        else:
                            bone.tail = Vector((0, self.bone_length, 0))
                    except Exception:
                        bones.remove(bone)
                        raise
            else:
                empty = bpy.data.objects.new(name, None)
                try:
                    (context.collection or tree.scene.collection).objects.link(empty)
                    empty.empty_display_type = "PLAIN_AXES"
                    empty.matrix_world = (source.obj.matrix_world @ source.owner.matrix
                                          if source.bone else source.obj.matrix_world)
                    if self.parent_empty:
                        set_parent(context, source, Entity(empty))
                except Exception:
                    bpy.data.objects.remove(empty, do_unlink=True)
                    raise
        except (RuntimeError, TypeError, ValueError, AttributeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        refresh(tree)
        return {"FINISHED"}


BONE_EDITS = (
    ("DISCONNECT", "Disconnect", "Disconnect the joint and keep the bone's parent"),
    ("CLEAR_PARENT", "Clear Parent", "Remove the parent relationship"),
    ("SPLIT", "Split", "Split the selected bones from the other bones"),
    ("SEPARATE", "Separate Bones", "Move the selected bones to a new armature"),
    ("SUBDIVIDE", "Subdivide", "Divide each selected bone into a chain"),
    ("SWITCH_DIRECTION", "Switch Direction", "Reverse the selected bone direction"),
    ("SYMMETRIZE", "Symmetrize", "Mirror selected bones using Blender's naming conventions"),
)


class SCHEMATIC_OT_edit_bones(SchematicOperator, Operator):
    bl_idname = "schematic.edit_bones"
    bl_label = "Edit Selected Bones"
    bl_options = {"UNDO"}

    action: EnumProperty(items=BONE_EDITS)
    number_cuts: IntProperty(name="Number of Cuts", default=1, min=1, max=100)

    def invoke(self, context, event):
        if self.action == "SUBDIVIDE":
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def draw(self, context):
        self.layout.prop(self, "number_cuts")

    def execute(self, context):
        tree = editor_tree(context)
        selected = selected_bones(tree)
        if not selected:
            self.report({"ERROR"}, "Select bones from one armature")
            return {"CANCELLED"}
        try:
            with edit_armature(context, selected[0].obj) as bones:
                if self.action in {"DISCONNECT", "CLEAR_PARENT"}:
                    for entity in selected:
                        bone = bones[entity.bone]
                        bone.use_connect = False
                        if self.action == "CLEAR_PARENT":
                            bone.parent = None
                else:
                    select_edit_bones(bones, [entity.bone for entity in selected])
                    operator = getattr(bpy.ops.armature, self.action.lower())
                    args = {"number_cuts": self.number_cuts} if self.action == "SUBDIVIDE" else {}
                    if operator(**args) != {"FINISHED"}:
                        return {"CANCELLED"}
        except (RuntimeError, ValueError, TypeError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        refresh(tree)
        return {"FINISHED"}


class SCHEMATIC_OT_native_bone_menu(SchematicOperator, Operator):
    bl_idname = "schematic.native_bone_menu"
    bl_label = "Blender Armature Menu"
    bl_description = "Open Blender's native Edit Mode armature menu for the selected bones"

    def execute(self, context):
        selected = selected_bones(editor_tree(context))
        if not selected:
            return {"CANCELLED"}
        obj = selected[0].obj
        names = [entity.bone for entity in selected]
        layer = context.view_layer
        try:
            validate_armature(context, obj)
            if context.mode == "POSE":
                bpy.ops.object.mode_set(mode="OBJECT")
            elif context.mode not in {"OBJECT", "EDIT_ARMATURE"}:
                raise ValueError("Use Object Mode or Pose Mode on this armature")
            if context.mode == "OBJECT":
                for item in layer.objects:
                    if item != obj and item.select_get(view_layer=layer):
                        item.select_set(False, view_layer=layer)
                obj.select_set(True, view_layer=layer)
                layer.objects.active = obj
                bpy.ops.object.mode_set(mode="EDIT")
            if context.edit_object != obj:
                raise ValueError("Finish editing the active armature first")
            select_edit_bones(obj.data.edit_bones, names)
            return bpy.ops.wm.call_menu(name="VIEW3D_MT_edit_armature")
        except (RuntimeError, ValueError, TypeError, KeyError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class SCHEMATIC_MT_bone_edit(Menu):
    bl_idname = "SCHEMATIC_MT_bone_edit"
    bl_label = "Bone Edit Tools"

    def draw(self, context):
        layout = self.layout
        if context.mode != "EDIT_ARMATURE":
            for key, label, _ in BONE_EDITS:
                layout.operator("schematic.edit_bones", text=label).action = key
            layout.separator()
        layout.operator("schematic.native_bone_menu", icon="BONE_DATA")


class SCHEMATIC_OT_finish_bone_edit(SchematicOperator, Operator):
    bl_idname = "schematic.finish_bone_edit"
    bl_label = "Finish Bone Editing"
    bl_options = {"UNDO"}

    def execute(self, context):
        if context.mode != "EDIT_ARMATURE":
            return {"CANCELLED"}
        tree = editor_tree(context)
        if bpy.ops.object.mode_set(mode="OBJECT") != {"FINISHED"}:
            return {"CANCELLED"}
        refresh(tree)
        return {"FINISHED"}


class SCHEMATIC_OT_expand_selected(SchematicOperator, Operator):
    bl_idname = "schematic.expand_selected"
    bl_label = "Expand Selected Nodes"
    bl_options = {"UNDO"}

    collapse: BoolProperty(options={"SKIP_SAVE"})

    def execute(self, context):
        tree = editor_tree(context)
        for node in tree.nodes:
            if node.select and node_entity(node):
                node.hide = self.collapse
        context.area.tag_redraw()
        return {"FINISHED"}


PARENT_ACTIONS = (
    ("NO_INVERSE", "Make Parent without Inverse", "Parent and place the child at the parent's origin"),
    ("NO_INVERSE_KEEP", "Make Parent without Inverse (Keep Transform)", "Parent while retaining world transform"),
    ("CLEAR", "Clear Parent", "Remove the parent relationship"),
    ("CLEAR_KEEP_TRANSFORM", "Clear and Keep Transformation", "Remove the parent while retaining world transform"),
    ("CLEAR_INVERSE", "Clear Parent Inverse", "Reset the inverse matrix and keep the parent relationship"),
)


class SCHEMATIC_OT_object_parent(SchematicOperator, Operator):
    bl_idname = "schematic.object_parent"
    bl_label = "Object Parenting"
    bl_options = {"UNDO"}

    action: EnumProperty(items=PARENT_ACTIONS)

    def execute(self, context):
        tree = editor_tree(context)
        selected = selected_entities(tree)
        if context.mode != "OBJECT" or not selected or any(entity.bone for entity in selected):
            self.report({"ERROR"}, "Select object nodes in Object Mode")
            return {"CANCELLED"}
        try:
            if self.action.startswith("NO_INVERSE"):
                if len(selected) != 2:
                    raise ValueError("Select two objects. The active node is the child.")
                source, target = selected
                if not source.obj.is_editable or not target.obj.is_editable:
                    raise ValueError("Select two editable objects")
                if would_cycle(tree.scene, source, target, TRANSFORM_DEPENDENCIES):
                    raise ValueError("This link would create a circular dependency")
                context.view_layer.update()
                with context.temp_override(active_object=source.obj, object=source.obj,
                                           selected_editable_objects=[target.obj]):
                    result = bpy.ops.object.parent_no_inverse_set(
                        keep_transform=self.action == "NO_INVERSE_KEEP",
                    )
            else:
                children = [entity.obj for entity in selected if entity.obj.parent]
                if not children or any(not obj.is_editable for obj in children):
                    raise ValueError("Select objects with editable parent relationships")
                with context.temp_override(selected_editable_objects=children):
                    result = bpy.ops.object.parent_clear(type=self.action)
        except (RuntimeError, ValueError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        if result != {"FINISHED"}:
            return result
        refresh(tree)
        return {"FINISHED"}


class SCHEMATIC_MT_object_parent(Menu):
    bl_idname = "SCHEMATIC_MT_object_parent"
    bl_label = "Object Parenting"

    def draw(self, context):
        selected = selected_entities(editor_tree(context))
        layout = self.layout
        if len(selected) == 2:
            layout.label(text=f"Child · {selected[-1].name}")
            for key, label, _ in PARENT_ACTIONS[:2]:
                layout.operator("schematic.object_parent", text=label).action = key
        if any(entity.obj.parent for entity in selected):
            if len(selected) == 2:
                layout.separator()
            for key, label, _ in PARENT_ACTIONS[2:]:
                layout.operator("schematic.object_parent", text=label).action = key


def context_menu(self, context):
    tree = editor_tree(context)
    if not tree or tree.scene != context.scene:
        return
    selected = selected_entities(tree)
    if not selected:
        return
    target = selected[-1]
    layout = self.layout
    layout.separator()
    layout.operator_context = "INVOKE_DEFAULT"
    layout.operator("schematic.expand_selected", text="Expand Selected", icon="TRIA_DOWN")
    layout.operator("schematic.expand_selected", text="Collapse Selected", icon="TRIA_RIGHT").collapse = True
    if (context.mode == "OBJECT" and all(not entity.bone for entity in selected)
            and (len(selected) == 2 or any(entity.obj.parent for entity in selected))):
        layout.menu("SCHEMATIC_MT_object_parent", icon="OBJECT_DATA")
    bones = selected_bones(tree)
    if bones:
        layout.menu("SCHEMATIC_MT_bone_edit", icon="BONE_DATA")
        if len(bones) > 1 and context.mode != "EDIT_ARMATURE":
            layout.operator("schematic.edit_bones", text="Disconnect Selected Bones", icon="UNLINKED").action = "DISCONNECT"
            layout.operator("schematic.edit_bones", text="Clear Parent Links").action = "CLEAR_PARENT"
        if len(bones) == 2 and context.mode != "EDIT_ARMATURE":
            layout.operator("schematic.connect", text="Join / Connect Bones…", icon="LINKED").action = "JOIN"
    if len(selected) > 2 or context.mode == "EDIT_ARMATURE":
        return
    if len(selected) == 2:
        layout.operator("schematic.connect", text="Add Constraint", icon="CONSTRAINT").action = "CONSTRAINT"
        layout.operator("schematic.connect", text="Add Driver", icon="DRIVER").action = "DRIVER"
        layout.operator("schematic.connect", text="Create Parent Link", icon="LINKED").action = "PARENT"
        return
    constraint = layout.operator("schematic.connect", text="Add Constraint…", icon="CONSTRAINT")
    constraint.action, constraint.manual_source = "CONSTRAINT", True
    if target.obj.type == "ARMATURE":
        layout.operator("schematic.create_node", text="Add Bone…", icon="BONE_DATA").kind = "BONE"
    layout.operator("schematic.create_node", text="Add Empty…", icon="EMPTY_DATA").kind = "EMPTY"
    if active_entity(tree):
        layout.operator("schematic.mark_source", icon="PINNED")
    source = marked_entity(tree)
    if source:
        if source != target:
            layout.operator("schematic.connect", text="Parent to Marked Source", icon="LINKED").action = "PARENT"
        layout.operator("schematic.connect", text="Add Driver from Marked Source", icon="DRIVER").action = "DRIVER"


CLASSES = (
    SCHEMATIC_OT_mark_source, SCHEMATIC_OT_connect, SCHEMATIC_OT_create_node,
    SCHEMATIC_OT_edit_bones, SCHEMATIC_OT_native_bone_menu, SCHEMATIC_OT_finish_bone_edit,
    SCHEMATIC_OT_expand_selected, SCHEMATIC_OT_object_parent,
    SCHEMATIC_MT_bone_edit, SCHEMATIC_MT_object_parent,
)
