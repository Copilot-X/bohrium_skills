---
name: biological-pathway-illustrator
description: 生物机制图与通路图：从材料建立区室/分子/交互台账，区分激活、抑制、转位、结合、修饰与推断，交付可编辑 SVG 和真实 PDF/PNG，并做逐边语义审计与视觉复核。Draw a source-bound biological mechanism or pathway figure with compartment layout, typed edges, real exports and a per-edge audit. Use for 机制图、信号通路、调控网络、生物示意图、pathway figure。
---

# 生物机制图与通路图（Mechanism & Pathway）

## 用途与触发

task.md 要求“画机制图 / 信号通路图 / 调控网络 / 生物示意图”，或 workorder 为
`biological_mechanism_pathway` 时使用。交付物是：

**一张真实、可编辑的 SVG + 真实 PDF/PNG + 逐元素来源 + 逐边语义审计。**

评分重点不是“像教科书”，而是：基因/蛋白/复合物/细胞区室不混用；激活、抑制、
结合、修饰、转位、相关可区分；方向与材料一致；图例与图面自洽；源文件可复核。

## 输入与先判定

- `/input/task.md`：物种/体系、通路范围、必须出现的分子、区室、画布、格式。
- `/input/case.json`：`case_id`、`workorder`、`source_materials[]`、`starting_figure`。
- 材料：结果段、图注、表、综述/数据库条目；综述图只能作结构参考，不能当本研究证据。
- 先判定 `mode = create | edit`；编辑模式先复制起始图，只改工单点名元素。

## 支持文件（按需；脚本失败不阻塞主交付物）

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output/figures /app/output/preview
```

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/pathway-figure-playbook.md` | 建表、语义核对、布局前 | 按小节查阅 |
| `templates/pathway-entities.json` | 起步模板 | 复制后替换；模板不是证据 |
| `scripts/pathway_to_spec.py` | 表 → spec + SVG + result 草稿 | 见第 3 步；结构错误返回 JSON |
| `scripts/svg_export.py` | 真实 PDF/PNG | 探测 cairosvg/rsvg/inkscape；不可用时 matplotlib 图元重绘 |
| `scripts/check_pathway_result.py` | 可选语义/结构审计 | 失败写 `audit_failed`，不阻塞 |
| `README.md` | 依赖/接口/限制 | 与本文同步 |

## 执行步骤（交付物优先）

### 第 1 步（约 10% 预算）：读题，锁定机制问题

在 `/app/work/contract.md` 写清：

- `case_id`、mode、图型、画布、格式、语言；
- 必须出现的区室、分子、交互、方向、数值/单位、图例；
- 每类材料的来源路径与 locator；
- 输出契约字段（任务字段 + media 别名）；
- 起始图与修改工单（编辑模式）。

不要在这一步跑脚本或先画坐标；先确认“这张图要回答什么机制问题”。

### 第 2 步（约 20% 预算）：建区室/分子/交互台账

写 `/app/work/pathway.json`：

```json
{
  "figure_id": "Fig.3",
  "compartments": [
    {"id": "C1", "label": "Extracellular", "order": 0,
     "source": {"path": "materials/results.md", "locator": "p.2"}},
    {"id": "C2", "label": "Cytoplasm", "order": 1,
     "source": {"path": "materials/results.md", "locator": "p.2"}},
    {"id": "C3", "label": "Nucleus", "order": 2,
     "source": {"path": "materials/results.md", "locator": "p.3"}}
  ],
  "molecules": [
    {"id": "M1", "label": "Ligand L", "entity_kind": "protein",
     "type": "ligand", "compartment": "C1", "rank": 0, "order": 0,
     "aliases": [], "sublabel": "",
     "source": {"path": "materials/results.md", "locator": "p.3", "quote": "…"}}
  ],
  "interactions": [
    {"id": "I1", "from": "M1", "to": "M2",
     "effect": "activate|inhibit|bind|translocate|modify|association|unknown_link",
     "mechanism": "binding|phosphorylation|transcription|nuclear import|…",
     "label": "+|-|binds|nuclear import", "direction": "forward",
     "kind": "reported|inferred", "evidence_strength": "strong|moderate|weak",
     "source": {"path": "materials/results.md", "locator": "p.3", "quote": "…"}}
  ],
  "legend": {"edge_semantics": [
    {"effect": "activate", "label": "activation"},
    {"effect": "inhibit", "label": "inhibition"}]},
  "data_encoding": [], "omitted": [], "conflicts": [], "uncertainty": []
}
```

硬规则：

- 一个实体一个节点；同义名/旧名放 `aliases[]`；修饰状态放 `sublabel`（如 `p-S473`）。
- 基因斜体、蛋白正体；`entity_kind` 记 `gene|protein|complex|cell|metabolite`。
- 复合物/家族按材料合并；只有亚基有独立边时才拆。
- 每条 `reported` 交互必须有 `source{path,locator,quote?}`；材料只说“相关/伴随”
  时用 `association`，不画因果箭头。
- 证据不足但需要表达的连接用 `kind: "inferred"`，图面灰色虚线，逐条写
  `schematic_disclaimers`。
- 缺中间节点写 `unknown_link` 或 `omitted[]`，不凭领域常识补“经典通路”。

### 第 3 步（约 35% 预算）：先落盘完整可编辑 SVG

```bash
python3 "$SKILL_DIR/scripts/pathway_to_spec.py" \
  --pathway /app/work/pathway.json \
  --out-spec /app/work/figure-spec.json \
  --out-svg /app/output/figures/fig3.svg \
  --result-out /app/output/result.json \
  --root /app/output --reviewed --json
```

脚本不可用或复杂拓扑需要手工时，直接写同结构 SVG：

- 根 `<svg>` 有 `viewBox`，自包含，无 `<script>`/外部引用；
- 文字全为 `<text>`；每区室 `<g id="C1" data-role="compartment">`、
  每分子 `<g id="M1" data-shape="...">`、每条边 `<g id="I1" data-kind="...">`；
- 区室框在底层；箭头停在节点边缘，不穿文字；
- 推断边灰色虚线；抑制边 T 形端；转位虚线；结合无箭头；图例覆盖实际线型；
- 字号 ≥7 pt；分子名不换行超过 2 行；不写“↑/↓”替代箭头。

### 第 4 步（约 15% 预算）：真实导出 PDF/PNG

```bash
python3 "$SKILL_DIR/scripts/svg_export.py" \
  --svg /app/output/figures/fig3.svg --outdir /app/output \
  --formats pdf,png --dpi 300 --basename fig3 --json
```

- 每个输出校验 `%PDF` / `\x89PNG` 文件头与尺寸；
- 全部失败：SVG + 自包含 HTML 预览，`status=partial`、`export_artifact=null`、
  `exceptions` 写 `export_unavailable`；
- 不伪造导出件；不把 SVG/HTML 当 PDF/PNG。

### 第 5 步（约 10% 预算）：逐边语义审计 + 强制视觉复核

写 `/app/work/semantic-check.json`：

```json
{"arrows_reported": 8, "arrows_inferred": 1,
 "inhibitions_total": 2, "inhibitions_verified": 2,
 "reported_edges_with_source": 8, "unsupported_removed": [],
 "all_effects_sourced": true, "reviewed": true}
```

**强制视觉复核**：打开导出的 PNG/PDF，逐项检查：

1. 每个区室/分子/边是否都在图中，方向是否正确；
2. 激活/抑制/转位/结合线型与端点是否一致，图例是否覆盖；
3. 推断边是否灰色虚线且有免责说明；
4. 文字是否溢出/压线/与箭头重叠；缩略图是否可读；
5. 基因斜体、蛋白正体、复合物/家族是否正确。

发现问题改台账/布局后重渲染；最多 2 轮。不可修复的写 `uncertainty`，不要只跑
XML checker 就声称完成。

## 输出契约（任务字段 + media 别名）

```json
{
  "case_id": "与输入一致",
  "task_family": "pathway",
  "workorder": "biological_mechanism_pathway",
  "status": "answered|partial|insufficient_evidence|input_error",
  "task_status": "completed|partial|input_error",
  "figure": {"figure_id": "Fig.3", "compartments": 3, "molecules": 10,
             "interactions": 9, "reported": 8, "inferred": 1,
             "canvas_mm": [180, 120], "layout_type": "LR"},
  "artifacts": [
    {"role": "editable", "path": "figures/fig3.svg", "format": "svg"},
    {"role": "export", "path": "figures/fig3.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/fig3.png", "format": "png",
     "width_px": 2126, "height_px": 1417, "dpi": 300}
  ],
  "pathway": {"compartments": [], "molecules": [], "interactions": []},
  "semantic_check": {"arrows_reported": 8, "arrows_inferred": 1,
                     "inhibitions_total": 2, "inhibitions_verified": 2,
                     "reported_edges_with_source": 8,
                     "all_effects_sourced": true, "reviewed": true},
  "legend": {"edge_semantics": [], "encodings": []},
  "data_encoding": [],
  "omitted": [], "conflicts": [], "schematic_disclaimers": [],
  "caption": "只描述图中可见的分子、关系与条件。",
  "alt_text": "面向无障碍的机制描述，不引入图外结论。",
  "checks": {"xml_valid": true, "text_as_text": true, "external_refs": 0,
             "named_groups": 24, "node_count": 10, "edge_count": 9,
             "text_fit": true, "arrow_clearance": true,
             "legend_complete": true, "visual_reviewed": true},
  "editable_source": {"path": "figures/fig3.svg", "format": "svg"},
  "export_artifact": {"path": "figures/fig3.pdf", "format": "pdf"},
  "content_map": [
    {"element_id": "M1", "element_type": "molecule", "kind": "reported",
     "maps_to": {"path": "materials/results.md", "locator": "p.3"}},
    {"element_id": "I1", "element_type": "interaction", "kind": "reported",
     "maps_to": {"path": "materials/results.md", "locator": "p.3"}}
  ],
  "compliance_checks": {"xml_valid": true, "text_as_text": true,
                        "no_external_refs": true, "viewbox_present": true,
                        "node_groups": "10/10", "edge_groups": "9/9",
                        "all_reported_edges_sourced": true,
                        "legend_covers_edge_kinds": true,
                        "inferred_edges_dashed": true,
                        "export_artifact_present": true},
  "exceptions": [],
  "uncertainty": [],
  "change_log": []
}
```

规则：

- `status` 用 media 词表；`answered` = 主图 + 要求导出件都完成；
- `editable_source` 指向真实 SVG；`export_artifact` 只在真实 PDF/PNG 存在时写对象；
- `content_map` 覆盖每个区室/分子/交互；每条 reported 边有 locator；
- `compliance_checks` 是 object；`schematic_disclaimers` 覆盖每条 inferred 边；
- `checks.visual_reviewed` 只有真的打开预览后才能写 true。

## 图形语义规范（强约束）

| 关系 | 线型 | 端点 | 原文依据 |
|---|---|---|---|
| 激活 `activate` | 实线 | 箭头 | activate/promote/induce/upregulate/recruit |
| 抑制 `inhibit` | 实线 | T 形横杠 | inhibit/suppress/block/reduce/negative regulation |
| 转运 `translocate` | 虚线 | 箭头 | translocate/import/export/shuttle/traffic |
| 结合 `bind` | 细实线 | 无箭头 | bind/associate/form complex/co-IP |
| 修饰 `modify` | 实线 | 箭头 + P/Ub | phosphorylate/ubiquitinate/cleave/acetylate |
| 相关 `association` | 细实线 | 无箭头 | correlate/associated with/co-varies |
| 未知 `unknown_link` | 虚线 | 箭头 + ? | 上下游出现但机制缺失 |
| 推断 `inferred` | 灰色虚线 | 箭头 + 极性 | 由间接证据推断，必须写免责 |

跨区室判定看“分子本身是否移动”；信号跨膜保留 activate/inhibit，并在
`mechanism` 写明传递方式。配体在胞外、受体在质膜、激酶级联在胞质、TF/基因在核内。

## 异常处理

| 情况 | 处理 |
|---|---|
| 缺材料/读不了 | `input_error` 或 `partial`；不臆造实体 |
| 实体表为空/畸形 | `input_error`；补表后重跑 |
| 未知实体/区室 | 不猜；修表或删边，缺口写 `omitted`/`uncertainty` |
| 缺来源 | 改 inferred + 虚线 + 免责，或补来源；`status=partial` |
| 原文只说“相关” | 用 association 无箭头，不画因果 |
| 方向不完整 | 只画有证据片段；用 unknown_link 标缺口 |
| 名称歧义 | 先在 uncertainty 消歧；无法消歧的实体不入图 |
| 综述图冲突 | 以本研究材料为准，写 `conflicts[]` |
| 导出失败 | SVG + HTML 预览；`partial` + `export_unavailable` |

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| M1 | 主交付物 | SVG 存在、可解析、有 `viewBox` |
| M2 | 可编辑 | 文字为 `<text>`；区室/分子/边各有 `<g id>` |
| M3 | 来源 | content_map 覆盖全部；每条 reported 边有 locator |
| M4 | 语义 | 激活/抑制/转运/结合/修饰/推断线型与端点正确 |
| M5 | 定位 | 分子在正确区室；跨区室用 translocate |
| M6 | 图面 | 无穿字/溢出/未说明推断；图例完整；字号 ≥7 pt |
| M7 | 导出诚实 | PDF/PNG 文件头有效；否则 partial |
| M8 | 契约 | 6 个 media 别名齐全；`compliance_checks` 是 object |

## 自检

- [ ] 每个分子、每条边有来源或 inferred 标记
- [ ] 激活/抑制/转位/结合/修饰语义与原文一致
- [ ] 无材料没有的“经典通路”中间节点
- [ ] 推断边虚线 + 免责说明；图例覆盖线型
- [ ] 区室定位正确，箭头不穿节点文字
- [ ] SVG 可单独打开；PDF/PNG 真实有效
- [ ] `content_map` 完整；`status` 诚实
- [ ] 真的打开预览做过视觉复核

## 最小示例（示例）

输入：结果段 2 页 + 图注 3 条，描述 L→R→K→TF→gene 级联与一条负反馈。
输出：`fig3.svg`（3 区室 / 10 分子 / 9 边，8 reported + 1 inferred）；
`semantic_check.all_effects_sourced=true`；`fig3.pdf` 与 `preview/fig3.png` 真实存在；
`status="answered"`。示例不是正式案例结果。

## 版本与限制

- 版本：0.8.0（区室/分子/边台账 + 真实导出 + 逐边语义审计 + 视觉复核）。
- 不依赖 python-pptx；不调用网络；可选渲染器失败时诚实降级。
- 复杂反馈环、密集交叉分支仍建议手工指定 rank/order 或拆图。
- 平台可能把技能装到题目专属目录，必须先解析 `$SKILL_DIR`。
