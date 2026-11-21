---
name: editable-schematic-builder
description: 科研示意图生成与编辑：先从材料抽出实体与关系，再交付真实可编辑 SVG（文字保持 <text>、节点/边各有 <g id>），随后真实导出 PDF/PNG；改图时保留工单未点名的全部元素并给出逐元素来源映射。Build or edit a source-bound scientific schematic with an editable SVG, real exports, per-element source map and source-preserving edits. Use for 科研示意图、机制图、关系图、流程图、图形改稿、示意图编辑。
---

# 科研示意图生成与编辑（Editable Relationship Schematic）

## 用途与触发

task.md 要求"画科研示意图 / 机制图 / 关系图 / 流程图"或"在给定图上修改"时使用。
交付物是：**一张真实可编辑的 SVG + 真实导出件（PDF/PNG）+ 逐元素来源映射**。
组织方 workorder 为 `editable_relationship_schematic`；"导出成功"不等于科学正确，
判分仍看实体、关系、方向、层级与材料是否对得上。

## 输入

- `/input/task.md`：图的用途、画布/栏宽、必须出现的实体与关系、语言、导出格式；**以它为准**。
- `/input/case.json`：`case_id`、`workorder`、`source_materials[]`、`starting_figure`（若有）。
- 材料：正文方法/结果段、表格、图注、已有图片；PDF 用实际文件，不靠标签猜内容。
- 编辑模式额外输入：起始 SVG（或可解析的矢量源）与修改点。
- 环境工具：不用假设；用 `export_schematic.py` 自动探测或 `command -v rsvg-convert inkscape`。

## 交付物优先流程（必须按顺序）

### 第 1 步（约 15% 预算）：读题并列出契约

读 `/input/task.md`、`/input/case.json` 和材料，写下（可记在 `/app/work/contract.md`）：

- `case_id`、图型、画布尺寸/栏宽、目标格式；
- 必须出现的实体、关系、方向、数值/单位、图例项；
- 新建还是编辑（`starting_figure` 是否存在）；
- 输出契约要求的所有字段。

不要在这一步运行脚本或搭模板。

### 第 2 步（约 40% 预算）：先落盘完整可用的主交付物 SVG

目标路径：`/app/output/figures/fig1.svg`（按 task.md 命名）。

- **新建模式**：直接写 SVG，或用可选的 `build_schematic.py` 由实体表生成。
- **编辑模式**：先把起始 SVG 复制到输出路径，再**只改工单点名的元素**；
  未点名的节点、边、文字、坐标、样式保持原样；不要整图重画冒充编辑。
- SVG 硬性结构（评委会直接看）：
  - 根元素 `<svg>` 带 `viewBox`；
  - 所有文字是 `<text>`，不要转曲、不要用位图文字；
  - 每个节点 `<g id="E1">`、每条边 `<g id="R1">`，id 与实体/关系表一致；
  - 无 `<script>`，无 http/file 外部引用（自包含）；位图只用 data: URI 或同目录文件并注明来源；
  - 文本落在框内、不压线、不与箭头重叠；字号 ≥7 pt。
- 关系语义：激活实线箭头、抑制 T 形端、流向/转化实线箭头、转运虚线、
  包含用分区框；推断关系用灰色虚线并在图注说明。同一图内不混用相同含义的线型。
- **这一步结束时必须有一个真实、可单独打开的 SVG**；后面全部失败也还能交付它。

### 第 3 步（约 25% 预算）：补齐结构化字段

- 实体/关系表：每个元素有 `id/label/type` 与 `source{path,locator}`；推断项标 `kind: inferred`。
- `element_source_map` / `content_map`：覆盖每个节点与边。
- 编辑模式：`edit.diff`（added/modified/removed/unchanged）、`edit.preserved`、`edit.removed`；
  文字没变但坐标变了的，也要在 `edit.diff` 里记 `modified` 并说明。
- `checks` / `compliance_checks`：只写实际检查过的项，布尔值必须与事实一致。
- media 官方别名字段：`status`、`editable_source`、`export_artifact`、`content_map`、
  `compliance_checks`、`exceptions`（见输出契约）。
- 先写 `result.json` 草稿并落盘，再继续。

### 第 4 步（约 15% 预算）：真实导出，再做可选审计

```bash
python3 "$SKILL_DIR/scripts/export_schematic.py" \
  --svg /app/output/figures/fig1.svg --outdir /app/output --dpi 300 \
  --json > /app/work/export.json
```

- 探测顺序：`cairosvg` → `rsvg-convert` → `inkscape` → `matplotlib`
  （`scientific-python` 通常预装 matplotlib；最后一级是真实 SVG 子集渲染器，
  不是伪造导出）。输出必须通过 PNG `\x89PNG` / PDF `%PDF` 文件头校验，
  校验失败的文件会被删除，**绝不伪造导出件**。
- 全部不可用：交付 SVG + **自包含 HTML 预览**（脚本自动生成），
  `status: "partial"`，`exceptions` 写 `export_unavailable`，`export_artifact: null`。
- 把完整 JSON 报告保存到 `work/export.json`，下一步组装 result.json 要用。
- 可选审计（失败不阻塞交付）：

```bash
python3 "$SKILL_DIR/scripts/check_schematic_result.py" \
  --result /app/output/result.json --entities /app/work/entities.json \
  --svg /app/output/figures/fig1.svg --root /app/output --json
python3 "$SKILL_DIR/scripts/compare_svg.py" \
  --before /app/work/start.svg --after /app/output/figures/fig1.svg \
  --workorder /app/work/workorder.json --json
```

`check_schematic_result.py` 的退出码：`0` 通过、`1` 有问题、`2` result 不可读；
`compare_svg.py`：`0` 改动在范围内、`1` 越界、`2` SVG/workorder 不可读。
**非 0 = 审计未通过，但不阻塞交付**：把失败原因写进 `exceptions`
（`audit_unavailable` / `audit_failed`）与 `uncertainty`，继续手工核对。
不要为了过 checker 删减真实内容或编造字段。

### 第 4.5 步（约 5% 预算）：用脚本组装契约完整的 result.json

```bash
python3 "$SKILL_DIR/scripts/assemble_result.py" \
  --entities /app/work/entities.json --svg /app/output/figures/fig1.svg \
  --root /app/output --export /app/work/export.json \
  --out /app/output/result.json --case-id "$CASE_ID" \
  [--before /app/work/start.svg --workorder /app/work/workorder.json] --json
```

- 脚本从真实实体表、真实 SVG 和真实导出报告生成全部任务字段与 media 别名；
  缺 PDF/PNG 时自动写 `export_artifact: null`、`export_unavailable`、`partial`，
  不伪造。
- 编辑模式传 `--before` + `--workorder`，脚本调用 `compare_svg.py` 生成
  `edit.diff/preserved/removed` 并检查越界改动。
- 退出码 0 = 无缺口；1 = 已写 result 但有 contract_gaps；2 = 核心输入不可读。
  有缺口时按 `contract_gaps` 修正后重跑。

### 第 5 步（约 5% 预算）：落盘核对

- `result.json` 能被 `json.load`；`case_id` 与输入一致；
- `artifacts[]` / `editable_source` / `export_artifact` 的路径都真实存在；
- `content_map` 覆盖每个实体/关系；编辑模式 `preserved` 与对比结果一致；
- `status` 诚实：缺导出就是 `partial`，缺输入就是 `input_error`。

## 支持文件（按需使用，不阻塞）

**先解析 skill 目录，不要照抄字面占位符**（平台可能装到 `/skills/weekly-skill`，
也可能装到题目专属目录）：

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
if [ -z "$SKILL_DIR" ] || [ ! -f "$SKILL_DIR/scripts/build_schematic.py" ]; then
  echo "SKILL_DIR not resolved; list /skills and set it manually" >&2
fi
echo "SKILL_DIR=$SKILL_DIR"
```

确认 `$SKILL_DIR` 非空且目录存在后，再用 `"$SKILL_DIR/scripts/..."`。
本地测试时脚本名可能带保护后缀 `.py.md`，运行时包内已还原为 `.py`。

| 文件 | 何时用 | 命令/说明 |
|---|---|---|
| `references/schematic-editing-playbook.md` | 建表、改稿、语义核对前 | 可选方法参考；与 task.md 冲突时以 task.md 为准 |
| `templates/entities.json` | 新建模式起步 | 可选；复制后替换，模板来源是示例，不能当证据 |
| `scripts/build_schematic.py` | 想用表驱动出图时 | `python3 "$SKILL_DIR/scripts/build_schematic.py" --entities /app/work/entities.json --out /app/output/figures/fig1.svg --json`；结构错误返回 JSON（exit 2），不会 traceback |
| `scripts/export_schematic.py` | 需要 PDF/PNG 或降级预览 | 见第 4 步；探测 cairosvg/rsvg/inkscape/matplotlib；全不可用时生成 HTML 预览并报 `export_unavailable` |
| `scripts/assemble_result.py` | 组装 result.json | 见第 4.5 步；从实体表/SVG/导出报告生成全部字段与 media 别名；支持 `--self-test` |
| `scripts/check_schematic_result.py` | 交付前自查 | 见第 4 步；缺文件/空表也返回可读 JSON |
| `scripts/compare_svg.py` | 编辑模式核对改动范围 | 先写 `/app/work/workorder.json`：`{"targets":["E2"],"add":["E6"],"remove":["E3"],"source":"task.md §…"}` |
| `README.md` | 安装/接口/限制速查 | 与 SKILL.md 同步维护的简明说明 |

- **脚本是可选加速器**：命令失败、路径不存在或时间不足时，直接手工完成同一检查，
  并在 `uncertainty`/`exceptions` 记录 `audit_unavailable`。
- **不要为了通过 checker 而删减真实内容或编造字段。**
- 不要依赖 `latex-compiler`、`office-renderer`、`pdf-render-tool`、`browser-automation`
  等不确定工具；导出只用第 4 步的三个探测目标。

## 输出契约

任务字段与 media 官方别名**同时存在**；官方字段是追加，不是替换。

```json
{
  "case_id": "case-001",
  "task_family": "schematic",
  "workorder": "editable_relationship_schematic",
  "status": "answered",
  "task_status": "completed",
  "figure": {
    "figure_id": "Fig.1", "canvas_mm": [180, 90],
    "nodes": 4, "edges": 3, "compartments": 1, "inferred": 1
  },
  "artifacts": [
    {"role": "editable", "path": "figures/fig1.svg", "format": "svg"},
    {"role": "export", "path": "figures/fig1.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/fig1.png", "format": "png",
     "width_px": 2126, "height_px": 1063, "dpi": 300}
  ],
  "element_source_map": [
    {"element_id": "E1", "maps_to": {"path": "materials/method.md", "locator": "§2.1"},
     "kind": "reported", "label": "Catalyst A"},
    {"element_id": "R3", "maps_to": {"path": "materials/results.md", "locator": "p.5"},
     "kind": "inferred", "note": "材料只说明相关，方向为示意"}
  ],
  "edit": {
    "mode": "edit",
    "starting_figure": "materials/fig1_start.svg",
    "diff": [{"element_id": "E2", "action": "modified",
              "before": "Catalyst", "after": "Catalyst A"}],
    "preserved": ["E1", "E3", "R1"],
    "removed": []
  },
  "checks": {
    "xml_valid": true, "text_as_text": true, "external_refs": 0,
    "named_groups": 9, "fonts": ["Liberation Sans"],
    "viewbox_check": true, "marker_check": true,
    "text_fit_check": true, "arrow_clearance_check": true,
    "export_from_source": true, "entity_group_coverage": "4/4"
  },
  "schematic_disclaimers": ["R3 为推断关系，材料只说明两者相关"],
  "uncertainty": [],
  "change_log": [{"action": "modified", "element_id": "E2",
                  "detail": "按工单更新标签；其余元素保持不变"}],
  "support_files_used": ["scripts/build_schematic.py",
                         "scripts/export_schematic.py"],
  "editable_source": {"path": "figures/fig1.svg", "format": "svg"},
  "export_artifact": {"path": "figures/fig1.pdf", "format": "pdf"},
  "content_map": [
    {"element_id": "E1", "maps_to": {"path": "materials/method.md", "locator": "§2.1"},
     "kind": "reported"}
  ],
  "compliance_checks": {
    "xml_valid": true, "text_as_text": true, "no_external_refs": true,
    "export_signature": "pdf", "dpi": 300, "dpi_source": "exported by rsvg-convert"
  },
  "exceptions": []
}
```

字段规则：

- `status` 用官方词表：`answered`（主图与要求导出件都完成）、`partial`（缺导出/缺材料等）、
  `insufficient_evidence`、`input_error`；`task_status` 保留原题口径。
- `editable_source` 必须指向真实存在的 SVG；`export_artifact` 指向真实 PDF/PNG，
  没有就写 `null`（不得指向 SVG 或 HTML 冒充）。
- `content_map` 与 `element_source_map` 内容一致，覆盖每个实体与关系。
- `compliance_checks` 与 `checks` 一致，只写实测项。
- `exceptions` 代码建议：`export_unavailable`、`missing_material`、`missing_target`、
  `audit_unavailable`、`audit_failed`、`svg_parse_error`、`input_error`。
- 无渲染器时：`artifacts` 用 `{"role":"preview","path":"preview/fig1.html","format":"html"}`，
  `status: "partial"`，`export_artifact: null`，`exceptions` 写 `export_unavailable`。
- 不要用通用适配器（如 `emit_official.py`）把 HTML 预览写成 `export_artifact`；
  没有真实 PDF/PNG 时手工保留 `null`，宁缺毋假。

## 质量闸门

**必须（缺一不可）**

| # | 检查 | 通过标准 |
|---|---|---|
| M1 | 主交付物 | `figures/fig1.svg` 存在、可解析、有 `viewBox` |
| M2 | 可编辑性 | 文字为 `<text>`；每个节点/边都有 `<g id>`；无转曲 |
| M3 | 自包含 | 无 `<script>`、无 http/file 外部引用 |
| M4 | 契约 | `result.json` 可解析；`case_id`、`artifacts`、`element_source_map`、`checks` 与 6 个 media 别名齐全 |
| M5 | 来源 | `content_map` 覆盖每个实体/关系；推断项有 `inferred` 与免责说明 |
| M6 | 导出诚实 | 每个导出件路径存在且文件头正确；没有就 `partial` + `export_unavailable`，不得伪造 |
| M7 | 编辑范围 | 编辑模式未点名元素保留；`edit.diff` action 合法；`preserved` 可核对 |

**建议（尽力而为）**

- 语义线型一致、图例覆盖实际线型、箭头不压字、节点不重叠、字号 ≥7 pt；
- 色盲友好（Okabe–Ito），灰度打印仍可区分；
- `check_schematic_result.py` 返回 `ok: true`；`compare_svg.py` 无 violations；
- 导出 PDF 且 PNG ≥300 dpi；字体名记录在 `checks.fonts`。

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料缺失/读不了 | `status: "input_error"` 或 `partial`；`exceptions` 写 `missing_material`，不臆造实体 |
| 起始图不可解析 | 重建同结构图，`status: "partial"`，记录丢失的原属性 |
| 工单目标不存在 | 记 `missing_target`；不新建同名元素冒充 |
| 实体表为空/畸形 | 脚本返回结构化错误（exit 2）；手工补表或直接手写 SVG，不阻塞 |
| 脚本失败/路径不存在 | 在 `exceptions` 记 `audit_unavailable`，继续手工核对并交付 |
| 导出工具不可用 | 交付 SVG + 自包含 HTML 预览；`status: "partial"`；`export_artifact: null`；不伪造 PNG/PDF |
| 时间不足 | 先保证第 2 步 SVG 与第 3 步核心字段；高级检查写进 `uncertainty` |
| 关系方向不明 | 无箭头连线 + `direction: "undetermined"` + 免责说明 |

## 最小示例（示例，不是正式答案）

输入：方法段给出 "Catalyst A converts Reactant to Product" 与一张起始 SVG；
工单要求把 `E2` 的标签从 `Catalyst` 改为 `Catalyst A`。

输出（示例）：

- `figures/fig1.svg`：4 节点 / 3 边 / 1 分区；`E1..E4`、`R1..R3` 各有 `<g id>`；
- `edit.mode: "edit"`，`diff` 1 条 modified，`preserved` 包含 `E1/E3/E4/R1`；
- `export_schematic.py` 无可用渲染器 → `preview/fig1.html` + `status: "partial"`；
- `result.json` 同时含任务字段与 6 个 media 别名。

## 版本与限制

- 版本：`0.6.0`（交付物优先流程；matplotlib 真实导出回退；result.json 组装器）。
- 核心脚本 stdlib-only（Python 3.12+）；导出额外探测 `cairosvg`/`rsvg-convert`/`inkscape`，
  最后使用 `scientific-python` 的 matplotlib 渲染 SVG 子集，产出真实 PDF/PNG。
- `compare_svg.py` 只比较 `<g id>` 与组内文本，**不检测纯坐标移动**；
  坐标级改动必须写进 `edit.diff` 并人工复核。
- 自动布局只保证基本不重叠；复杂拓扑仍建议手工指定 `rank/order` 或坐标。
- 无任何渲染器（含 matplotlib）时 PDF/PNG 不可得，只能交付 SVG + HTML 预览（诚实降级）。
- matplotlib 回退渲染的是本 skill 生成的 SVG 子集（rect/circle/ellipse/polygon/
  polyline/line/path/text + 箭头标记）；复杂的滤镜、裁剪路径和外部字体不会完全保真。
- 维护：改脚本后同步更新本文件、`README.md` 与 `references/schematic-editing-playbook.md`；
  不写无法验证的分数、运行记录或截图。
