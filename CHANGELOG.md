# Schematic View changelog

Blender extension that inspects object, bone, and driver relationships, in the spirit of 3ds Max Schematic View. Version 0.7.0 requires Blender 5.3 or later. Author: Aomay Huang.

Install: **Edit → Preferences → Get Extensions → Install from Disk**, and choose [`releases/schematic_view-0.7.0.zip`](releases/schematic_view-0.7.0.zip?raw=true).

## 0.7.0 — Units, Map Range, per-target expressions, Relative and Absolute

- **Offset** is **Relative** (default) or **Absolute**, for Parent, Constraint, and Driver:
  - **Driver, Relative.** The expression becomes the target's current value plus `f(var) − f(source's current value)`. The target stays where it is and follows only how much the source changes.
  - **Constraint, Relative.** Copy Location and Copy Scale turn on `use_offset`. Copy Rotation sets `mix_mode` to `AFTER`. Copy Transforms sets `AFTER_FULL`. Up to four corrective `matrix_basis` passes then put the target back on its original world position (checked on objects and bones). Child Of runs Set Inverse on its own. Other constraints (Track, IK, and similar) have no relative meaning, and the panel says so.
  - **Parent, Relative** is Keep Transform. The old `keep_transform` property is no longer in the UI.
  - **Copy Once** is always an absolute value.
- **Auto Units** (on by default). Angles are degrees inside expressions and Map Range: a source angle is `degrees(var)`, and a target angle is wrapped in `radians()`. An integer target is wrapped in `round()`. A boolean target uses `> 0.5`. Units come from the RNA unit or type; custom properties use the Python value type. The panel shows a unit hint.
- **Map Range.** From Min/Max maps onto To Min/Max, with an optional Clamp built from `min` / `max` (Auto Run Python Scripts is not required). The mapped value is what Expression sees as `var`.
- **Per-target expressions.** Each checked target value can keep its own expression. They live in an overrides collection that grows and shrinks with the checks. A blank override uses the shared Expression.
- The red alert background from 0.6.1 is removed, as requested. Source stays fixed on the first row.

### 0.7.0 中文

- Offset：Relative（預設）／Absolute，Parent、Constraint、Driver 都適用：
  - Driver 的 Relative：運算式變成「目標目前值 + f(var) − f(來源目前值)」，目標留在原位，只跟著來源的變化量走。
  - Constraint 的 Relative：Copy Location／Scale 開 use_offset，Copy Rotation 設 mix_mode AFTER，Copy Transforms 設 AFTER_FULL，再用最多 4 次修正 matrix_basis，讓目標回到原本的世界位置（物件和骨頭都測過）。Child Of 自動 Set Inverse；其他約束（Track、IK 等）沒有相對的意義，面板會提示。
  - Parent 的 Relative = Keep Transform；原本的 keep_transform 屬性已不在介面上。
  - Copy Once 一律是絕對值。
- Auto Units（預設開啟）：角度在運算式和 Map Range 裡都用「度」（來源角度 var→degrees(var)；目標角度包 radians()）；目標整數包 round()；目標開關用「> 0.5」；單位從 RNA unit/type 判斷，自訂屬性看 Python 值型別；面板顯示單位提示。
- Map Range：From Min/Max → To Min/Max，可選 Clamp（用 min/max 實作，不需 Auto Run Python Scripts）；算完的值再代入 Expression 的 var。
- 每個勾選的目標值可以有自己的運算式：存在 overrides 集合，跟著勾選同步增減；留空用共用 Expression。
- 依使用者要求拿掉 0.6.1 的紅色底（alert），Source 仍固定在第一列。

## 0.6.x — Link Workbench

- There is a single Source, and it always stays on the first row. Choosing Source from the menu pins it to the top. Up and down do not move it past that row. The swap control turns the selected Target into the Source.
- Sort targets only: By Name (natural order), By Hierarchy, or Reverse.
- Two levels of collapsing. Click a group name to select the whole group. The list shows only the rotation the object is using now. Delta is its own category.
- Side by side (Source on the left, Target on the right) or Stacked. A Values button. Targets can be multi-selected.

### 0.6.x 中文

- Source 只有一個且永遠在第一列（下拉選 Source 自動置頂；▲▼ 不越過；⇄ 把選取的 Target 換成 Source）。
- 排序：By Name（自然排序）、By Hierarchy、Reverse；只排 Target。
- 兩層收折、點組名選整組；只顯示物件目前用的旋轉；Delta 另成分類。
- 並排（左 Source、右 Target）或 Stacked；Values 按鈕；目標複選。

## 0.5.0 — Property browser, expressions, chaining, and Notes

- Browse categories: Transform, Custom, Delta, Object, Data, Modifier, Constraint, Shape Keys, Material. A property is stored as `ROOT|path|index` (`index` `-1` means the whole vector). Implementation is in `props.py`.
- Chain, Copy Once, and material drivers drawn on the graph.
- Note: a Frame node can hold a Text data-block. Ctrl+J joins notes.

### 0.5.0 中文

- Browse 分類：Transform、Custom、Delta、Object、Data、Modifier、Constraint、Shape Keys、Material。屬性記錄成「ROOT|path|index」（index -1 = 整組向量），實作在 props.py。
- Chain；Copy Once；材質 driver 畫在關係圖上。
- Note：Frame 節點加 Text 資料塊、Ctrl+J。

## 0.4.0 — First Link Workbench

- Open it from the 3D Viewport right-click menu or the N panel. After you close it, you can return to the original tab. Parent, twelve constraint types, and Driver. Links Between These can Unlink. Two-way wires are not supported.

### 0.4.0 中文

- 3D 視窗右鍵或 N 面板開啟；關掉後可叫回原分頁。Parent、12 種 Constraint、Driver；Links Between These 可 Unlink；不支援雙向 wire。

## 0.3.0 / 0.2.0 — Skeleton Shape layout

- Humanoid and bird rigs lay out from the Front. Quadrupeds and fish lay out from the Top. The face sits above the head. Left is tinted blue and right is tinted red.

### 0.3.0 / 0.2.0 中文

- 人形和鳥 → Front；四足和魚 → Top；臉部排在頭上方；L 染藍、R 染紅。

## 0.1.0

- Caches for speed, side-effect fixes, more relationship types, auto refresh, and Max-style interaction.

### 0.1.0 中文

- 效能快取、副作用修正、更多關係類型、自動更新、Max 式互動。

## Known limits

- The first time a Scene tab with about a thousand bones opens, building it takes around two seconds.
- With Emulate 3 Button Mouse on, Alt plus the mouse can conflict with navigation.
- Fingers and claws still clump together.
- Side-by-side layout truncates text in a narrow sidebar.
- Relative mode for Transform takes its starting value from `matrix_basis` or the world matrix. A bone's Transform Space can differ slightly from the value you see.

### 已知限制

- 第一次開上千骨的 Scene 分頁約 2 秒。
- Emulate 3 Button Mouse 開啟時 Alt+滑鼠會衝突。
- 手指和爪子仍會擠成小團。
- 並排在窄側欄裡文字會被截短。
- Transform 模式的 Relative 起始值從 matrix_basis 或世界矩陣算；骨頭的 Transform Space 和實際值可能有些微差異。
