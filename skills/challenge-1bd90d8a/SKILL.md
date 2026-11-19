---
name: source-value-chart-builder
description: 科研数据图表生成与编辑：从数据表建立列/单位/样本量索引，按数据结构选图型，逐点映射来源行，交付可编辑 SVG 与真实 PDF/PNG，并做数值回读和视觉复核。Turn supplied data into a publication-quality chart with row-level provenance, real vector/raster exports and a value audit. Use for 数据图、折线/柱状/散点图、误差线、图表复现与编辑。
---

# 科研数据图表生成与编辑（Source-Value Chart）

## 用途与触发

task.md 要求“根据数据画图”“生成数据图表”“改一张数据图”时使用。组织方
workorder 为 `source_value_chart`。核心约束：

**图上的每个数值、单位、误差线和分类都必须能回到材料中的表/文/图；
技术性文件检查通过不等于科学正确。**

## 输入与先判定

- `/input/task.md`：图种偏好、尺寸/栏宽、期刊、必须呈现的比较、语言。
- `/input/case.json`：`case_id`、`workorder`、`data_files[]`、`starting_figure`。
- 数据：CSV/TSV/XLSX 导出的表、正文表格、图注（含误差类型、n、缺失标记）。
- 若只有图没有原始表：读数必须标 `estimated: true` 并给读数误差，不能伪造原表。
- 编辑模式：先复制起始图/规格，只改工单点名的面板、序列、轴或标注。

## 支持文件（按需；脚本失败不阻塞主交付物）

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output/figures /app/output/preview
```

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/chart-source-playbook.md` | 建索引、选图型、定轴/误差前 | 按小节查阅 |
| `templates/chart-spec.json` | 图表规格骨架 | 复制后填 panels/series/axes/errors/source |
| `scripts/make_chart.py` | 由 spec 生成可编辑 SVG | `python3 "$SKILL_DIR/scripts/make_chart.py" --spec /app/work/figure-spec.json --out /app/output/figures/fig2.svg --json` |
| `scripts/svg_export.py` | 真实 PDF/PNG 导出 | cairosvg/rsvg/inkscape 不可用时用 matplotlib 重绘 SVG 图元 |
| `scripts/check_chart_result.py` | 逐点数值/契约审计 | 交付前运行；失败写 `audit_failed`，不阻塞主交付物 |
| `README.md` | 依赖/接口/限制 | 与本文同步 |

## 执行步骤（交付物优先）

### 第 1 步（约 10% 预算）：读数据，建列索引

写 `/app/work/data-index.json`：

```json
{"tables": [
  {"file": "materials/results.csv",
   "columns": [
     {"name": "catalyst", "role": "group", "unit": null},
     {"name": "T_C", "role": "x", "unit": "°C"},
     {"name": "conv_pct", "role": "y", "unit": "%"},
     {"name": "sd", "role": "error", "unit": "%", "error_type": "SD"},
     {"name": "n", "role": "n", "unit": null}
   ],
   "rows": 12, "missing": ["NA"], "notes": "conv_pct 为三次重复均值"}]}
```

逐列确认含义、单位、角色、缺失标记和行范围；x 是否递增、是否有重复组、
量级是否异常。不要在索引前画图。

### 第 2 步（约 20% 预算）：把每个点映射到源行

写 `/app/work/series.json`：

```json
{"series": [
  {"name": "Catalyst A", "panel": "a",
   "x": ["300", "350", "400"],
   "values": [72.1, 84.3, 91.0],
   "error": {"type": "SD", "values": [2.1, 1.8, 2.4]},
   "n": 3, "unit": "%",
   "source": {"file": "materials/results.csv", "locator": "rows 2-4, cols C-E"}}]}
```

- 每条 series 必须有 `source.locator`（行区间/过滤条件/列名）；
- 误差线必须有类型（SD/SEM/CI）和 n；没有误差数据就不画误差线；
- 显著性标记只在材料给出检验和水平时使用，不能凭“看起来显著”加星号；
- 单位换算要记录系数与来源；缺失值不插值冒充实测。

### 第 3 步（约 20% 预算）：选图型并写规格

写 `/app/work/figure-spec.json`（`make_chart.py` 的输入）：

```json
{"figure_id": "Fig.2", "width_mm": 180, "height_mm": 90,
 "font_family": "Liberation Sans", "font_size_pt": 8,
 "layout": {"cols": 2},
 "panels": [
  {"id": "a", "type": "bar", "title": "Conversion vs catalyst",
   "x": {"label": "Catalyst", "categories": ["A", "B", "C"]},
   "y": {"label": "Conversion", "unit": "%", "min": 0, "max": 100},
   "series": [{"name": "350 °C", "values": [84.3, 61.2, 55.0],
               "errors": [1.8, 2.0, 1.5], "source": "results.csv#rows2-4"}]}]}
```

选型规则：

- 分组比较 → 柱/点/箱线；趋势 → 折线/散点；分布 → 箱线/小提琴/散点；
  矩阵 → 热图；相关 → 散点；不要因为“好看”选 3D 或双 y 轴；
- 柱状图 y 轴从 0 起；对数轴必须显式写 `log scale`；截断轴必须标注；
- 轴标签写“量 + 单位”；分类用 Okabe–Ito 色板并同时用标记/线型区分；
- 字号按最终印刷尺寸 ≥7 pt；图例不遮数据；面板编号统一 `(a)(b)`；
- 选型理由写入 `chart_type_rationale`，不要只写“bar”。

### 第 4 步（约 25% 预算）：出可编辑 SVG

```bash
python3 "$SKILL_DIR/scripts/make_chart.py" \
  --spec /app/work/figure-spec.json \
  --out /app/output/figures/fig2.svg --json
```

若脚本不可用或图型超出其支持范围，手工写 SVG；无论如何必须保留：

- 根 `<svg>` + `viewBox`；文字是 `<text>`；
- 每个面板/系列有可识别的 `<g>` 或 `data-*`；
- 无外部引用、无位图化文字、无 3D/渐变装饰；
- 实际数据点与 `series.json` 一致。

### 第 5 步（约 15% 预算）：数值回读 + 真实导出 + 视觉复核

```bash
python3 "$SKILL_DIR/scripts/svg_export.py" \
  --svg /app/output/figures/fig2.svg --outdir /app/output \
  --formats pdf,png --dpi 300 --basename fig2 --json
```

数值回读（关键）：从生成后的 SVG/规格逐点核对 `series.json`，写
`/app/work/value-check.json`：

```json
{"all_match": true, "checked_points": 9, "mismatches": []}
```

**强制视觉复核**：打开 PNG/PDF，检查

1. 轴标签/单位是否齐全，刻度是否真实反映范围；
2. 误差线是否有端点横线，类型/n 是否写在图注；
3. 图例/系列颜色/标记是否一一对应，灰度是否仍可辨；
4. 文字是否被裁切、重叠、压住数据；
5. 缩到 200–300 px 宽后主结论是否仍可读。

发现问题先改 spec/布局再重渲染；不得用“看起来差不多”代替数值核对。

### 第 6 步（约 10% 预算）：写 result.json

保留任务字段并追加 media 官方别名。`status` 用 `answered|partial|...`，
`task_status` 保留题目口径；`export_artifact` 只在真实 PDF/PNG 存在时写对象。

## 输出契约

```json
{
  "case_id": "与输入一致",
  "task_family": "data-chart",
  "workorder": "source_value_chart",
  "status": "answered|partial|insufficient_evidence|input_error",
  "task_status": "completed|partial|input_error",
  "figure": {"figure_id": "Fig.2", "panels": 1, "chart_types": ["bar"],
             "canvas_mm": [180, 90]},
  "artifacts": [
    {"role": "editable", "path": "figures/fig2.svg", "format": "svg"},
    {"role": "export", "path": "figures/fig2.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/fig2.png", "format": "png",
     "width_px": 2126, "height_px": 1063, "dpi": 300}
  ],
  "series_map": [
    {"panel": "a", "series": "350 °C", "values": [84.3, 61.2, 55.0],
     "error": {"type": "SD", "n": 3}, "kind": "reported",
     "source": {"path": "materials/results.csv", "locator": "rows 2-4"}}
  ],
  "axes": [
    {"panel": "a", "x": {"label": "Catalyst", "unit": null},
     "y": {"label": "Conversion", "unit": "%", "scale": "linear"}}
  ],
  "value_check": {"all_match": true, "checked_points": 9, "mismatches": []},
  "chart_type_rationale": "分类比较使用柱状图；趋势面板使用折线图。",
  "palette": {"name": "Okabe-Ito",
              "hex": ["#E69F00", "#56B4E9", "#009E73", "#0072B2"]},
  "journal_preset": "custom|nature|science|cell",
  "caption": "只描述图中可见的数据、误差类型、n 和条件。",
  "alt_text": "一句话结论 + 关键数值 + 条件。",
  "checks": {"xml_valid": true, "text_as_text": true, "external_refs": 0,
             "overlap_free": true, "min_font_pt": 8,
             "colorblind_safe": true, "grayscale_distinguishable": true,
             "visual_reviewed": true},
  "compliance_checks": {"xml_valid": true, "text_as_text": true,
                        "no_external_refs": true, "export_signature": "pdf",
                        "dpi": 300, "axes_have_units": true},
  "editable_source": {"path": "figures/fig2.svg", "format": "svg"},
  "export_artifact": {"path": "figures/fig2.pdf", "format": "pdf"},
  "estimated": [],
  "exceptions": [],
  "uncertainty": [],
  "change_log": []
}
```

规则：

- `series_map` 覆盖每个 panel/series；每个点能追到源行/列或标 `estimated`；
- `value_check.all_match` 必须来自真实比对，不能默认写 true；
- `caption`/`alt_text` 不能引入图中没有的数据或因果；
- `checks.visual_reviewed` 只有真的打开预览文件后才能写 true；
- 没有 PDF/PNG 时 `status=partial`、`export_artifact=null`、`exceptions` 写
  `export_unavailable`。

## 异常处理

| 情况 | 处理 |
|---|---|
| 缺列/空值 | 该点不画，`uncertainty` 说明；不插值 |
| 只有图没有表 | 读数 + `estimated` + 读数误差 |
| 图注与正文数值冲突 | 两值记 `conflicts[]`，图面用正文值并说明 |
| 单位不统一 | 统一换算并记录系数/来源 |
| 误差线无类型/n | 补标注；补不了就删误差线 |
| 双轴误导/截断轴 | 改单轴或显式标注截断 |
| 导出失败 | SVG + HTML 预览，`partial` + `export_unavailable` |
| 图型超出脚本能力 | 手工 SVG，保留同一 `figure-spec` 与来源映射 |

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 来源 | 每条 series 有行区间/过滤条件；无材料外数据点 |
| G2 | 数值 | `value_check` 抽检点与源表完全一致 |
| G3 | 轴/单位 | 两轴有标签与单位；范围未欺骗性截断 |
| G4 | 误差 | 有误差线则标 SD/SEM/CI 与 n |
| G5 | 可读 | 无文字/图例遮挡；字号 ≥7 pt；灰度可辨 |
| G6 | 导出 | SVG 可解析；PDF/PNG 文件头有效、尺寸/dpi 记录真实 |
| G7 | 视觉复核 | 真正打开预览并逐项检查；不是只跑 XML lint |
| G8 | 契约 | 6 个 media 别名齐全；`compliance_checks` 是 object |

## 自检

- [ ] 数据索引记录了列含义、单位、角色、缺失与行范围
- [ ] 每个数据点可追到源表行/列或标 `estimated`
- [ ] `value_check` 全部 match；图值与表值一致
- [ ] 误差类型/n/单位齐全；没有凭空加显著性
- [ ] SVG 文字可编辑；PDF/PNG 真实有效
- [ ] 缩略图/灰度检查通过；无遮挡、无裁切
- [ ] `result.json` 路径真实；`content_map`/`series_map` 覆盖完整
- [ ] 没有把模板、示例或“常见数据”当成材料事实

## 最小示例（示例）

输入：12 行 CSV（含 sd/n）+ 要求 2 面板。输出：`fig2.svg`（bar + line）；
`value-check.json` 9 点全 match；`fig2.pdf` 1 页；`preview/fig2.png` 2126×1063
@300 dpi；`status: "answered"`。示例不是正式案例结果。

## 版本与限制

- 版本：0.8.0（列索引 + 行级来源 + 数值回读 + 真实导出 + 视觉复核）。
- 不依赖 python-pptx；不调用网络；可选渲染器失败时诚实降级。
- `make_chart.py` 支持 bar/line/scatter；其他图型手工 SVG 或拆面板。
- 平台可能把技能装到题目专属目录，必须先解析 `$SKILL_DIR`。
