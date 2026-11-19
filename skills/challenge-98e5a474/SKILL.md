---
name: scientific-schematic-editor
description: 科研示意图生成与编辑：从材料建立实体-关系-证据台账，按来源约束布局，交付可编辑 SVG 与真实 PDF/PNG，并给出逐元素来源、图注、alt text 和保真检查。Build or edit a source-bound scientific schematic with a claim ledger, editable SVG, real exports and a visual audit. Use for 科研示意图、机制图、关系图、流程图、图形改稿、示意图编辑。
---

# 科研示意图生成与编辑（Evidence-Bound Schematic）

## 用途与触发

task.md 要求“画科研示意图 / 机制图 / 关系图 / 流程图”，或要求在已有图上做
source-preserving edit 时使用。交付物不是“一张好看的图”，而是：

**材料 → 实体/关系台账 → 可编辑 SVG → 真实 PDF/PNG → 逐元素来源与视觉复核。**

组织方 workorder 为 `editable_relationship_schematic`。评分看实体、关系、方向、
层级、数值/单位与来源是否一致；“导出成功”不等于科学正确。

## 输入与先判定

- `/input/task.md`：图的用途、画布/栏宽、必须出现的实体与关系、语言、导出格式。
- `/input/case.json`：`case_id`、`workorder`、`source_materials[]`、`starting_figure`。
- 材料：方法/结果段、表格、图注、已有矢量图；PDF 读实际文件，不靠文件名猜内容。
- 先判定 `mode = create | edit`；编辑模式必须先复制起始图，禁止整图重画冒充编辑。

## 支持文件（按需使用；失败不阻塞主交付物）

先解析安装目录，不要照抄字面占位符：

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output/figures /app/output/preview
echo "SKILL_DIR=$SKILL_DIR"
```

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/schematic-editing-playbook.md` | 建表、语义核对、改稿前 | 按小节查阅；与 task.md 冲突时以 task.md 为准 |
| `templates/entities.json` | 新建模式起步 | 复制后替换；模板是示例，不是证据 |
| `scripts/build_schematic.py` | 表驱动出 SVG | 见第 3 步；结构错误返回 JSON，不 traceback |
| `scripts/svg_export.py` | 真实导出 PDF/PNG | 运行时探测 cairosvg/rsvg/inkscape；都不可用时用 matplotlib 重绘 SVG 图元 |
| `scripts/check_schematic_result.py` | 可选结构审计 | 缺文件/空表返回 JSON；失败写 `audit_failed`，不阻塞 |
| `scripts/compare_svg.py` | 编辑范围核对 | 比较 before/after 的 `g id` 与文字 |
| `README.md` | 接口/依赖/限制速查 | 与本文同步 |

脚本是加速器。命令失败或时间不足时，手工完成同一检查，并把
`audit_unavailable` / `renderer_error` 写入 `exceptions`；不要为了过 checker
删掉真实元素或编造字段。

## 执行步骤（交付物优先）

### 第 1 步（约 10–15% 预算）：读题，锁定图面契约

在 `/app/work/contract.md` 写清：

- `case_id`、`mode`、图型、画布尺寸、栏宽、语言、目标格式；
- 必须出现的实体、关系、方向、数值/单位、图例项；
- 材料路径 + 可定位位置（页/段/图注/表行）；
- 输出契约要求的全部字段（任务字段 + media 官方别名）；
- 起始图路径与修改工单（编辑模式）。

这一步不跑脚本、不套模板。先确认“这张图要回答什么科学问题”。

### 第 2 步（约 20% 预算）：建立实体—关系—证据台账

写 `/app/work/entities.json`：

```json
{
  "figure_id": "Fig.1",
  "entities": [
    {"id": "E1", "label": "Catalyst A", "kind": "material",
     "source": {"path": "materials/method.md", "locator": "§2.1", "quote": "…"}}
  ],
  "relations": [
    {"id": "R1", "from": "E1", "to": "E2", "semantic": "flow",
     "label": "conversion", "direction": "forward", "kind": "reported",
     "source": {"path": "materials/results.md", "locator": "Fig.2a", "quote": "…"}}
  ],
  "groups": [{"id": "G1", "label": "Reactor", "nodes": ["E1", "E2"]}],
  "legend": [{"kind": "flow", "label": "material flow"}],
  "values": [{"element_id": "R1", "value": "92", "unit": "%",
              "source": "Fig.2a"}]
}
```

硬规则：

- 一个实体一个节点；同义名/别名放 `aliases[]`，不另造节点；
- 每条 `reported` 关系必须有 `source{path,locator,quote?}`；
- 材料只说“相关/伴随”时用 `association`，不画因果箭头；
- 证据不足但读者需要理解的连接标 `kind: "inferred"`，图面灰色虚线，并逐条写
  `schematic_disclaimers`；
- 材料没有的实体、数值、浓度、时间点、单位一律不补；
- 被裁掉的支路写 `omitted[]` 与理由；冲突写 `conflicts[]`。

### 第 3 步（约 35% 预算）：先落盘完整可编辑 SVG

优先用表驱动，减少手写坐标：

```bash
python3 "$SKILL_DIR/scripts/build_schematic.py" \
  --entities /app/work/entities.json \
  --out /app/output/figures/fig1.svg --json
```

编辑模式：先 `cp /input/starting-figure.svg /app/work/start.svg`，只改工单点名的
`<g id>`；未点名的节点、边、文字、坐标、样式保持原样。不要整图重画。

SVG 硬性结构（评委会直接看）：

- 根 `<svg>` 有 `xmlns`、`viewBox`、白色背景、`<title>`/`<desc>`；
- 所有文字是 `<text>`，不转曲、不位图化；
- 每个实体 `<g id="E1">`、每条关系 `<g id="R1">`，id 与台账一致；
- 无 `<script>`、无 http/file 外部引用；
- 文字在框内、不压线、不与箭头重叠；字号 ≥7 pt；
- 关系语义：激活实线箭头、抑制 T 形端、流向/转化实线箭头、转运虚线、
  包含用分区框、结合用细实线无箭头；同一图内线型语义不混用；
- 图例覆盖实际使用的线型/形状；数值编码有单位、尺度和来源。

这一步结束必须有一个**单独可打开、可评价**的 SVG；后续导出失败也还能交付它。

### 第 4 步（约 15% 预算）：真实导出 PDF/PNG

```bash
python3 "$SKILL_DIR/scripts/svg_export.py" \
  --svg /app/output/figures/fig1.svg --outdir /app/output \
  --formats pdf,png --dpi 300 --basename fig1 --json
```

- 探测顺序：cairosvg → rsvg-convert → inkscape → matplotlib 图元重绘；
- 每个输出必须通过 `%PDF` / `\x89PNG` 文件头与尺寸校验；
- 全部失败：交付 SVG + 自包含 HTML 预览，`status: "partial"`，
  `export_artifact: null`，`exceptions` 写 `export_unavailable`；
- 不得用 SVG/HTML 冒充 PDF/PNG，不得写不存在的尺寸或 dpi。

### 第 5 步（约 10% 预算）：写 result.json、图注与视觉复核

先写 `/app/output/result.json`，再跑可选审计：

```bash
python3 "$SKILL_DIR/scripts/check_schematic_result.py" \
  --result /app/output/result.json --entities /app/work/entities.json \
  --svg /app/output/figures/fig1.svg --root /app/output --json
```

**强制视觉复核**（不是可选）：打开导出的 PNG/PDF，逐项检查：

1. 每个实体/关系是否都在图中，且方向正确；
2. 文字是否被裁切、溢出、压线或与箭头重叠；
3. 图例是否覆盖实际线型，颜色/形状是否与分类数一致；
4. 缩略到 300 px 宽后主信息是否仍可读；
5. 推断关系是否有虚线 + 免责说明。

发现问题就改 SVG 或台账后重渲染；最多 2 轮。无法修复的写进 `uncertainty`，
不要声称“已通过视觉检查”。

## 输出契约（任务字段 + media 官方别名）

```json
{
  "case_id": "与输入一致",
  "task_family": "schematic",
  "workorder": "editable_relationship_schematic",
  "status": "answered|partial|insufficient_evidence|input_error",
  "task_status": "completed|partial|input_error",
  "figure": {"figure_id": "Fig.1", "canvas_mm": [180, 90],
             "entities": 4, "relations": 3, "inferred": 1},
  "artifacts": [
    {"role": "editable", "path": "figures/fig1.svg", "format": "svg"},
    {"role": "export", "path": "figures/fig1.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/fig1.png", "format": "png",
     "width_px": 2126, "height_px": 1063, "dpi": 300}
  ],
  "entities": [
    {"element_id": "E1", "label": "Catalyst A", "kind": "material",
     "source": {"path": "materials/method.md", "locator": "§2.1"},
     "status": "reported"}
  ],
  "relations": [
    {"id": "R1", "from": "E1", "to": "E2", "semantic": "flow",
     "label": "conversion", "direction": "forward", "status": "reported",
     "source": {"path": "materials/results.md", "locator": "Fig.2a"}}
  ],
  "visual_claim_ledger": [
    {"claim_id": "C1", "text": "A converts 92% at 350 °C",
     "visual_elements": ["E1", "R1", "E2"],
     "evidence": [{"path": "materials/results.md", "locator": "Fig.2a"}],
     "status": "reported"}
  ],
  "content_map": [
    {"element_id": "E1", "element_type": "entity", "kind": "reported",
     "maps_to": {"path": "materials/method.md", "locator": "§2.1"}},
    {"element_id": "R1", "element_type": "relation", "kind": "reported",
     "maps_to": {"path": "materials/results.md", "locator": "Fig.2a"}}
  ],
  "edit": {"mode": "create", "starting_figure": null, "diff": [],
           "preserved": [], "removed": []},
  "caption": "一句话图注，只描述图中可见内容与来源。",
  "alt_text": "面向无障碍的等价描述；不引入图中没有的结论。",
  "checks": {"xml_valid": true, "text_as_text": true, "external_refs": 0,
             "named_groups": 7, "text_fit": true, "arrow_clearance": true,
             "legend_complete": true, "visual_reviewed": true},
  "compliance_checks": {"xml_valid": true, "text_as_text": true,
                        "no_external_refs": true, "export_signature": "pdf",
                        "dpi": 300, "coverage": "all_entities_relations"},
  "editable_source": {"path": "figures/fig1.svg", "format": "svg"},
  "export_artifact": {"path": "figures/fig1.pdf", "format": "pdf"},
  "exceptions": [],
  "uncertainty": [],
  "change_log": []
}
```

规则：

- `status` 用 media 词表；`answered` 表示主图与要求的导出件都完成，缺导出写
  `partial`，缺输入写 `input_error`。
- `editable_source` 指向真实 SVG；`export_artifact` 只在真实 PDF/PNG 存在时写对象，
  否则 `null`。
- `content_map` 覆盖每个实体与关系；`visual_claim_ledger` 覆盖图中每个科学断言。
- `checks`/`compliance_checks` 只写实际测过的项；`visual_reviewed` 必须真的打开过
  预览文件才能写 `true`。
- 编辑模式 `edit.diff` 的 action 只能是 `added|modified|removed|unchanged`；
  未点名元素列入 `preserved`。

## 异常处理

| 情况 | 处理 |
|---|---|
| 缺 task/case/材料 | `status=input_error`，写清缺什么，不编造图 |
| 起始图不可解析 | 不重画冒充编辑；`partial` + `starting_figure_unreadable` |
| 实体/关系表为空 | `input_error`；先补台账再出图 |
| 未知实体/未知端点 | 不猜；修表或删除该边，缺口写 `omitted`/`uncertainty` |
| 只有“相关”表述 | 用 `association` 无箭头，不画因果 |
| 需要 PDF/PNG 但无渲染器 | SVG + HTML 预览；`partial` + `export_unavailable` |
| 文字溢出/箭头压字 | 调整布局/换行/缩短标签，不缩字号到 7 pt 以下 |
| 复杂反馈环/交叉线 | 用分区、旁路或分层；仍不可读则拆图并说明 |

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| M1 | 主交付物 | SVG 存在、可解析、有 `viewBox` |
| M2 | 可编辑 | 文字为 `<text>`；实体/关系各有 `<g id>`；无转曲 |
| M3 | 来源 | `content_map` 覆盖全部实体/关系；每条 reported 边有 locator |
| M4 | 语义 | 激活/抑制/转运/结合/推断线型与端点正确；图例覆盖实际线型 |
| M5 | 图面 | 无文字溢出、无箭头压字、无未说明的推断；字号 ≥7 pt |
| M6 | 导出诚实 | PDF/PNG 文件头有效；没有就 `partial` + `export_unavailable` |
| M7 | 契约 | `case_id`、任务字段、6 个 media 别名齐全；`compliance_checks` 是 object |
| M8 | 视觉复核 | 真正打开预览并记录检查项；不能只跑 XML 检查 |

## 自检

- [ ] 读到了 task/case，写下了图面契约与 mode
- [ ] 每个实体/关系有来源或明确的 `inferred`
- [ ] SVG 可单独打开；文字可编辑；每个实体/关系有 id
- [ ] PDF/PNG 真实存在且文件头正确；否则诚实 `partial`
- [ ] 图例、单位、数值与源材料一致
- [ ] 缩略图测试通过；无文字溢出/箭头压字
- [ ] result.json 的路径都真实存在，`content_map` 覆盖完整
- [ ] 没有把示例、模板或推断写成来源事实

## 最小示例（示例，不是正式答案）

输入：方法 §2.1–2.3 与结果图注，要求 4 个实体、3 条关系、1 条推断边。
输出：`figures/fig1.svg`（4 个 `<g id="E*">`、3 个 `<g id="R*">`、1 条灰色虚线）；
`svg_export.py` 产出 `figures/fig1.pdf` 与 `preview/fig1.png`；
`result.json` 的 `content_map` 覆盖 7 个元素，`visual_claim_ledger` 覆盖 3 个断言；
视觉复核记录 `text_fit=true, arrow_clearance=true, visual_reviewed=true`。
示例不是正式案例结果。

## 版本与限制

- 版本：0.8.0（实体—关系—证据台账 + 真实导出 + 强制视觉复核）。
- 不依赖 python-pptx；不调用网络；可选渲染器失败时诚实降级。
- 复杂拓扑仍建议手工指定 `rank/order` 或拆分多面板。
- 平台可能把技能装到题目专属目录，必须先解析 `$SKILL_DIR`。
