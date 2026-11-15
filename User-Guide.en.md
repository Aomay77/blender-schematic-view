# Blender Schematic View User Guide

Extension version **0.0.10** · Requires **Blender 5.3 or later**

Schematic View displays object and armature relationships in a separate window. Use the graph to select scene items, switch between character tabs, and create parenting links, constraints, and Blender Drivers.

## 1. Install and open

1. In Blender, open **Edit → Preferences → Get Extensions**.
2. From the top-right menu, choose **Install from Disk** and select `schematic_view-0.0.10.zip`.
3. Select an object or armature in the 3D Viewport, then press **N** and choose **Schematic → Open Schematic View**.
4. The floating window opens with the selection and its direct relationships. An empty selection opens the **No Selection** tab.

If an older version left the graph in a docked editor, use **Open Floating Window** in that editor's sidebar. New floating windows on Windows open on the left and can be resized using the window border.

## 2. Create tabs for characters and object groups

1. Select one or more nodes in the graph and click **Save Selection as Tab** in the right sidebar. Selecting a bone makes its armature part of the tab's scope.
2. Use **Tab Name** to give the tab a name such as “Character A” or “Props.”
3. Click the current tab name in the right sidebar to switch to another character, armature, or object group.

Opening the extension from the 3D Viewport with a new selection also creates or switches to a matching tab. Each tab keeps its node positions, expansion state, and filters in the `.blend` file. Choose **Entire Scene Tab** from the tab menu when you need the full scene.

## 3. Work in the graph

| Action | How to do it |
| --- | --- |
| Select nodes | Click a node or **Shift-click** to add nodes to the selection. |
| Box-select and move | Press **B** and drag a selection box, then press **G** to move the selected nodes. |
| Pan, zoom, and frame | Drag with the middle mouse button to pan and use the wheel to zoom. Press **Home** or click **Frame All** to fit the graph. |
| Inspect a node | Click the arrow on its left. **Node Properties & Relationships** in the sidebar shows properties and relationship details for the active, expanded node. |
| Expand or collapse a selection | Use **Expand Selected** or **Collapse Selected**. |
| Arrange a rig | **Human Layout** arranges the head, torso, and limbs by bone name. The character's left appears on the right of the graph. |

Graph selections also select the corresponding items in the 3D Viewport. Selecting a bone node selects its armature in **Object Mode** and the bone itself in **Pose Mode**. Selecting a regular object while in Pose Mode returns Blender to Object Mode.

Node colors distinguish categories such as Auto-Rig Pro controllers, reference bones, and deform bones. Wires are blue for parenting, orange for constraints, purple for Drivers, green for Armature Modifier bindings, and gray-purple for links from an armature to its root bones.

## 4. Filter and restore visibility

- **Node Filters** toggle categories in the graph and the corresponding objects or bones in the 3D Viewport. Click a category again to restore it. Turning off the Armature category also hides its bones in the viewport.
- **Relationship Filters** toggle graph wires. A wire is visible when both endpoint nodes are visible.
- Set the name, Bone Collection, custom property name, or **Visible in View Layer Only** filter, then click **Refresh / Apply Filters**. Use the same button to update the graph after editing the scene elsewhere in Blender.
- **Reset Filters** restores the active tab's defaults and reveals items hidden by the extension. Items you had already hidden manually stay hidden.

## 5. Create relationships from the context menu

| Selection | Available actions |
| --- | --- |
| One node | **Add Constraint** opens a dialog to choose a target object and optional bone. Use **Add Empty** to create an Empty, or **Add Bone** when an armature or bone is selected. |
| Two nodes | Use **Add Constraint**, **Add Driver**, or **Create Parent Link**. The active node receives the constraint or Driver, or becomes the child. Use the dialog to swap the direction. |
| A marked source | Right-click a node and choose **Mark as Relationship Source**. Select the destination node and use the marked source from its context menu. |

**Add Constraint** supports Copy Transforms, Copy Location, Copy Rotation, Copy Scale, Child Of, Damped Track, and IK. Add IK to a bone node.

### Driver example

Use an existing `["ik_fk"]` property on a controller bone to drive an Empty's Z scale:

1. Select the controller bone, then **Shift-click** the Empty and choose **Add Driver**.
2. Verify that the dialog shows the controller bone as the source and the Empty as the driven node.
3. Enter `["ik_fk"]` for **Source Property** and `scale` for **Driven Property**. Set **Driven Array Index** to `2`. Array indexes start at `0`.
4. Confirm, change the controller property, and check the Empty's scale in the 3D Viewport. A destination channel with an existing Driver keeps its current Driver.

## 6. Edit bones and object parenting

- Select bones in the same armature and open **Bone Edit Tools** for Split, Separate Bones, Subdivide, Switch Direction, and Symmetrize. With two bones selected, use **Join / Connect Bones** to connect the child's head to the parent's tail.
- **Disconnect Selected Bones** disconnects the joints while keeping their parents. To remove parenting, use **Clear Parent Links**.
- **Blender Armature Menu** enters Edit Mode and gives access to commands for names, Bone Collections, and bone settings. Use **Finish Bone Editing** to update the graph when done.
- In Object Mode, select two object nodes and open **Object Parenting** for **Make Parent without Inverse**, with an option to keep the world transform. For already parented objects, use **Clear Parent**, **Clear and Keep Transformation**, or **Clear Parent Inverse**.

## If the graph looks empty

1. If the tab says **No Selection**, select an object or armature in the 3D Viewport and open Schematic View again.
2. If the item is outside the current scope, switch tabs or select it in the 3D Viewport and reopen the extension.
3. Use **Reset Filters** if nodes are filtered out. Press **Home** over the graph if the nodes are offscreen.
