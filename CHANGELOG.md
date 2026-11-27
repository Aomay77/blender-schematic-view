# Schematic View changelog

Requires Blender 5.3 or later. [Demo video](https://www.youtube.com/watch?v=gUM2HK-_Bac).

## 0.7.0

Relative mode keeps each target where it is and follows only the change. Absolute mode applies the source value directly. Auto Units writes angles in degrees, converts angle and distance, rounds integers, and switches checkboxes above 0.5. Map Range with Clamp, plus a separate expression for each target value.

### 中文

Relative 讓目標留在原位、只跟著變化量走；Absolute 直接套用來源的值。Auto Units 用「度」寫角度、自動換算角度和距離、整數四捨五入、開關超過 0.5 就打開。Map Range＋Clamp，每個目標值也能有自己的運算式。

## 0.6.x

Link Workbench keeps one source with one or more targets. Sort and collapse the targets, select several at once, and show them side by side or stacked.

### 中文

Link Workbench 以一個來源搭配一個或多個目標。目標可以排序、收合、複選，並排或上下排列。

## 0.5.0

Browse custom properties, modifiers, shape keys, and materials, such as a material's emission. Chain links source → target → the next target, Copy Once writes a value once with no live driver, and Notes (Ctrl+J) are colored frames with text saved in the .blend.

### 中文

可以瀏覽自訂屬性、Modifier、Shape Keys 和材質，例如驅動材質的 Emission。Chain 把來源 → 目標 → 下一個目標串起來，Copy Once 只寫入一次、不留下 Driver，Note（Ctrl+J）是存在 .blend 裡的彩色文字框。

## 0.4.0

Link Workbench opens from the 3D Viewport. Create a Parent, one of 12 constraint types, or a Driver, and unlink wires between the loaded items.

### 中文

從 3D Viewport 開啟 Link Workbench。可以建立 Parent、12 種 Constraint 或 Driver，也能解除已載入項目之間的連線。

## 0.3.0

Skeleton Shape covers human, quadruped, and other rigs. The left side is blue and the right side is red.

### 中文

Skeleton Shape 涵蓋人形、四足和其他骨架。左邊藍色、右邊紅色。

## 0.2.0

Bones are laid out like the rest pose instead of a plain hierarchy list.

### 中文

骨頭依照 rest pose 排列，不再只是一條父子清單。

## 0.1.0

The graph refreshes when you add objects. 3ds Max-style keys: [ parent, ] children, double-click selects the tree, L selects dependents, X deletes a selected wire.

### 中文

新增物件時關係圖會自動更新。3ds Max 風格快捷鍵：[ 選父物件，] 選子物件，雙擊選整棵樹，L 選相依物件，X 刪除選取的連線。
