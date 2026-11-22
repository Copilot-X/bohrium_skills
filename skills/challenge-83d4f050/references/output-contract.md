# 输出契约（Pathway / media）

本技能只有一个运行入口：

```bash
python3 "$SKILL_DIR/scripts/run_case.py" \
  --input /input --out /app/output --work /app/work --json
```

它按 `inventory → ledger → spec → editable SVG → PDF/PNG → audit → result.json`
执行。任何一步失败都要留下 `result.json`；`partial` / `input_error` 也退出 0，
只有无法创建输出目录或无法写 `result.json` 时才退出 2。

## 1. 输入发现顺序

1. `--case` / `--ledger` 显式路径；
2. `/app/work/pathway.json`（模型按 `templates/pathway-spec.schema.json` 写的台账）；
3. `/input/case.json`、`/input/pathway.json`；
4. `/input/figure-workorder.json`、`/input/artifacts/figure-workorder.json`；
5. `/input` 中任何含 `compartments/molecules/interactions`（或
   `nodes/edges/entities/relations`）的 JSON；
6. 没有台账时，从 `paper.txt` / `task.md` 生成**临时草稿**并强制
   `status=partial`，`exceptions` 写 `auto_draft`；草稿不能当作最终答案。

`paper.txt`、`task.md` 与材料文件用于核对 `source.path/locator/quote`。
`source.path` 必须是输入中真实存在的文件；不要用标签当路径。

## 2. 输出文件

```
/app/output/figures/<base>.svg     # 可编辑源文件
/app/output/figures/<base>.pdf     # 真实 PDF（渲染器可用时）
/app/output/preview/<base>.png     # 真实 PNG 预览
/app/output/result.json            # 官方字段 + 任务字段
/app/work/inventory.json           # 输入盘点
/app/work/figure-spec.json         # 渲染 spec（含 layout 检查）
/app/work/coverage.json            # audit 报告
/app/work/run-report.json          # 运行摘要
/app/work/pathway-draft.json       # 仅无台账时的自动草稿（不覆盖 pathway.json）
```

模型应把最终台账写到 `/app/work/pathway.json`；自动草稿写到
`pathway-draft.json`，并强制 `status=partial`、`exceptions=[{code:"auto_draft"}]`。

`<base>` 默认由 `figure_id` 派生（如 `Fig.2` → `Fig.2`）。

## 3. `result.json` 字段

### 3.1 官方 media 字段（必须由 `scripts/result_graphics.py` 生成）

| 字段 | 类型 | 规则 |
|---|---|---|
| `status` | string | `completed` / `partial` / `insufficient_evidence` / `input_error` |
| `editable_source` | object/null | 真实 SVG：`{"path": "figures/x.svg", "format": "svg"}` |
| `export_artifact` | object/null | 真实 PDF 或 PNG；没有合格导出件时为 `null` |
| `content_map` | array | 每个区室、分子、边一条；`maps_to` 为 `{path,locator,quote?}` |
| `compliance_checks` | object | 由 `scripts/audit.py` 生成，不得手写 |
| `exceptions` | array | 每项 `{code,detail}`；没有异常时为空数组 |

### 3.2 任务字段

```json
{
  "case_id": "与输入一致",
  "task_family": "pathway",
  "workorder": "biological_mechanism_pathway",
  "status": "completed",
  "task_status": "completed",
  "figure": {"figure_id": "Fig.2", "compartments": 4, "molecules": 9,
             "interactions": 8, "reported": 7, "inferred": 1,
             "canvas_mm": [180, 118], "layout_type": "LR"},
  "artifacts": [{"role": "editable", "path": "figures/Fig.2.svg", "format": "svg"},
                {"role": "export", "path": "figures/Fig.2.pdf", "format": "pdf"},
                {"role": "preview", "path": "preview/Fig.2.png", "format": "png",
                 "width_px": 1062, "height_px": 696, "dpi": 150}],
  "pathway": {"compartments": [], "molecules": [], "interactions": []},
  "semantic_check": {"arrows_reported": 7, "arrows_inferred": 1,
                     "inhibitions_total": 2, "inhibitions_verified": 2,
                     "reported_edges_with_source": 7,
                     "all_effects_sourced": true, "reviewed": true},
  "legend": {"edge_semantics": [], "encodings": []},
  "data_encoding": [], "omitted": [], "conflicts": [],
  "schematic_disclaimers": [],
  "caption": "只描述图中可见的实体、关系与条件。",
  "alt_text": "面向无障碍的机制描述。",
  "checks": {"xml_valid": true, "text_as_text": true, "external_refs": 0,
             "named_groups": 26, "node_count": 9, "edge_count": 8,
             "text_fit": true, "arrow_clearance": true,
             "legend_complete": true, "semantic_edges_ok": true,
             "visual_reviewed": true, "element_coverage": 1.0,
             "source_coverage": 1.0},
  "coverage": {"total": 21, "mapped": 21, "element_coverage": 1.0,
               "source_coverage": 1.0, "reported_source_coverage": 1.0},
  "input_inventory": {"root": "/input", "total": 3, "files": []},
  "uncertainty": [], "change_log": [],
  "status_vocabulary": "media"
}
```

`status` 与 `task_status` 的映射：

| status | task_status | 条件 |
|---|---|---|
| `completed` | `completed` | SVG 可解析、audit 无 error、coverage=1.0、reported 边有来源、PDF/PNG 文件头有效、PNG 像素复核通过 |
| `partial` | `partial` | 图已生成但缺来源、缺导出、审计失败或使用了自动草稿 |
| `insufficient_evidence` | `partial` | 只有推断关系或没有可用实体 |
| `input_error` | `input_error` | 没有台账/材料，或台账结构不合法 |

## 4. 审计产物与判定

`scripts/audit.py` 检查并写入 `compliance_checks`：

- SVG 是合法 XML、含 `viewBox`、无 `<script>`、无外部 `href/src`、无位图；
- 文字全部为 `<text>`，每个区室/分子标签都能在 SVG 文本中找到；
- 每个区室、分子、边都有命名 `<g id>`；
- 边线型与端点符合语义表（`activate/inhibit/bind/translocate/modify/
  association/unknown_link/inferred`）；
- 推断边为灰色虚线且保留极性端点；
- 图例覆盖实际出现的边类型；
- PDF/PNG 文件头、PNG 尺寸、非空白像素比例与内容边界；
- `coverage.element_coverage` 与 `coverage.source_coverage`，reported 边来源比例。

`coverage.element_coverage < 1.0`、reported 边缺来源、导出无效或像素复核失败
都会进入 `exceptions`，并把状态降为 `partial`。

## 5. 诚信规则

- 不伪造 PDF/PNG，不把 SVG/HTML 当 PDF/PNG；
- 不把自动抽取草稿写成 `completed`；
- 不把未实际运行的检查写成通过；
- `schematic_disclaimers` 覆盖每条 inferred 边；
- `content_map` 的 `maps_to` 必须来自真实输入文件。
