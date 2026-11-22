---
name: source-value-chart-builder
description: 源数据图表构建：把给定数据表转成出版级图表，每条序列可追溯到行区间，坐标轴带单位，误差线标类型与 n，输出可编辑 SVG 与真实 PNG/PDF。Turn supplied data into a publication-quality chart with every series traced to source rows and real exports. Use for 数据图、折线/柱状/散点图、图表复现。
---

# 科研数据图表生成与编辑（Source-Value Chart）

## 用途与触发

task.md 要求"根据数据画图""生成数据图表""改一张数据图"时使用。
组织方口径（硬约束）：workorder 为 `source_value_chart`；
**图上的每个数值都必须来自材料中的表/文/图**；
"技术性文件检查"不证明科学正确性。

## 输入

- `/input/task.md`：图种（柱/折线/散点/组合）、尺寸、期刊格式、必须呈现的对比
- `/input/case.json`：`case_id`、`workorder`、`data_files[]`、`starting_figure`（若有）
- 数据：CSV/TSV/XLSX 导出的表、正文表格、图注（含误差类型与 n）
- 可选：已有图（要求编辑时）

## 执行步骤

1. **读数据、建索引**：`/app/work/data-index.json`
   ```json
   {"tables": [
     {"file": "materials/results.csv", "columns": ["catalyst", "T_C", "conv_pct", "sd", "n"],
      "rows": 12, "notes": "conv_pct 为三次重复均值"}]}
   ```
   逐列确认含义、单位、缺失标记（`NA`、`-`、空）。
2. **抽取序列**：`/app/work/series.json`
   ```json
   {"series": [
     {"name": "Catalyst A", "x": ["300", "350", "400"],
      "values": [72.1, 84.3, 91.0], "error": {"type": "SD", "values": [2.1, 1.8, 2.4]},
      "n": 3, "unit": "%",
      "source": {"file": "materials/results.csv", "locator": "rows 2-4, cols C-E"}}]}
   ```
   只有图没有表时：读数 → `estimated: true` + 读数误差；**不得伪造原始表**。
3. **写图表规格** `/app/work/figure-spec.json`（`make_chart.py` 的输入）：
   ```json
   {"figure_id": "Fig.2", "width_mm": 180, "height_mm": 90,
    "font_family": "Liberation Sans", "font_size_pt": 8,
    "layout": {"cols": 2},
    "panels": [
      {"id": "a", "type": "bar", "title": "Conversion vs catalyst",
       "x": {"label": "Catalyst", "categories": ["A", "B", "C"]},
       "y": {"label": "Conversion", "unit": "%", "min": 0, "max": 100},
       "series": [{"name": "350 °C", "values": [84.3, 61.2, 55.0],
                   "errors": [1.8, 2.0, 1.5], "source": "results.csv#rows2-4"}]},
      {"id": "b", "type": "line",
       "x": {"label": "Temperature", "unit": "°C", "values": [300, 350, 400]},
       "y": {"label": "Selectivity", "unit": "%"},
       "series": [{"name": "A", "values": [88.0, 92.1, 90.4], "marker": "circle",
                   "errors": [1.2, 0.9, 1.4], "source": "results.csv#colsC-E"}]}]}
   ```
   - `type`：`bar`（分组柱）、`line`、`scatter`；
   - 每序列必须有 `source`；误差用 `errors` + `error.type` + `n`；
   - 显著性标记只在材料给出时用 `significance`，否则**不画**。
4. **生成**：
   ```bash
   python3 /skills/weekly-skill/scripts/make_chart.py \
     --spec /app/work/figure-spec.json --out /app/output/figures/fig2.svg --json
   ```
5. **数值回读校验**（关键）：生成后从 SVG 或规格里逐点核对，
   与 `series.json` 完全一致；不一致即停下修正，不"差不多就行"。
   写 `/app/work/value-check.json`：`{series, expected, rendered, match}`。
6. **校验与导出**：
   ```bash
   python3 /skills/weekly-skill/scripts/svg_lint.py /app/output/figures/fig2.svg --json
   python3 /skills/weekly-skill/scripts/make_chart.py --check /app/output/figures/fig2.svg --json
   ```
   PDF/PNG 由 SVG 渲染（≥300 dpi）；回读尺寸并写入 `result.json`。
7. **编辑模式**：只改工单指定的序列/轴/标注；保留其他面板与样式，逐项记 diff。
8. **落盘**：`figures/fig2.svg`、`figures/fig2.pdf`、`preview/fig2.png`、
   `data-index.json`、`value-check.json`、`result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "data-chart",
  "workorder": "source_value_chart",
  "status": "completed|partial",
  "figure": {"figure_id": "Fig.2", "panels": 2, "chart_types": ["bar", "line"],
             "canvas_mm": [180, 90]},
  "artifacts": [
    {"role": "editable", "path": "figures/fig2.svg", "format": "svg"},
    {"role": "export", "path": "figures/fig2.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/fig2.png", "format": "png",
     "width_px": 0, "height_px": 0, "dpi": 300}
  ],
  "series_map": [
    {"panel": "a", "series": "350 °C",
     "source": {"path": "materials/results.csv", "locator": "rows 2-4"},
     "values": [84.3, 61.2, 55.0], "error": {"type": "SD", "n": 3},
     "kind": "reported"}
  ],
  "axes": [
    {"panel": "a", "x": {"label": "Catalyst", "unit": null},
     "y": {"label": "Conversion", "unit": "%", "scale": "linear"}}
  ],
  "value_check": {"all_match": true, "checked_points": 9, "mismatches": []},
  "checks": {"xml_valid": true, "text_as_text": true, "external_refs": 0,
             "colorblind_safe": true, "grayscale_distinguishable": true,
             "export_from_source": true},
  "estimated": [{"panel": "b", "series": "B", "reason": "仅原图，无原始表",
                 "reading_error": "±2%"}],
  "uncertainty": [],
  "change_log": []
}
```

## 绘图规范

- 轴：标签 + 单位齐全；柱状图 y 轴从 0 起；对数轴显式写 `log scale`。
- 误差：类型（SD/SEM/CI）与 n 写进图注；误差棒必须有端点横线。
- 数值：不改精度、不做四舍五入到"好看"的数；单位与源表一致。
- 配色：Okabe–Ito；不同序列同时用颜色 + 标记形状区分，灰度可辨。
- 版式：面板编号 `(a)(b)` 统一左上；图例不遮数据；柱宽一致。
- 禁止：3D、渐变填充、截断 y 轴不标注、未给显著性却画星号。

## 异常处理

| 情况 | 处理 |
|---|---|
| 表格有缺失值 | 该点不画并在 `uncertainty` 说明，不插值冒充实测 |
| 只有图没有表 | 读数 + `estimated: true` + 读数误差 |
| 图注与正文数值冲突 | 两值都记入 `conflicts[]`，图面用正文值并注明 |
| 单位不统一 | 统一换算并记录换算系数与来源 |
| 需要组合图 | 拆成多个 panel，共享图例，不叠双 y 轴除非材料如此 |
| 导出工具缺失 | 交付 SVG + 说明，`status=partial`，不伪造 PNG/PDF |

## 自检

- [ ] 每个数据点可追到源表行/列或标 `estimated`
- [ ] `value-check.json` 全部 match
- [ ] 误差类型、n、单位齐全
- [ ] 轴标签与刻度真实反映范围，未截断
- [ ] SVG 通过 lint，文本可编辑
- [ ] 预览确由源文件渲染，dpi/尺寸达标
- [ ] 无材料外的数据点、拟合线或显著性标记

## 快速路径（预算内）

1. **0–2 分钟**：读数据表与 task.md，建 `data-index`（列含义、单位、行范围）。
2. **2–7 分钟**：确定图表类型与 series 映射，先出 SVG 骨架并落盘。
3. **7–12 分钟**：补轴、误差线、图例、注释，导出 PNG/PDF。
4. **最后 2 分钟**：逐点核对图值与表值一致。

## 方法增强（融合参考实践）

- **数据索引**：每列记 `name / unit / role (x|y|group|error|n)`；x 递增性、缺失与异常先检查。
- **series 映射**：每条序列绑定 `row_range + filter`，写入 `series_map`；图上每个点都能回到源行。
- **图表选型**：比较用柱/点，趋势用线，分布用箱线/散点，相关用散点；不因好看选 3D 或双轴。
- **轴与单位**：两轴都有标签与单位；范围不截断数据；必要时注明对数轴。
- **误差线**：标 `SD/SEM/CI` 与 `n`；无误差数据不画误差线。
- **可读性**：色盲友好配色（不单靠红绿）、线型/标记辅助区分、字号 ≥ 7pt、图例不遮挡数据。
- **数值复核**：随机抽 3–5 点，比较图上坐标与表中数值；不一致即停。

### 定向方法增强

- **来自 Academic Figures**：先按数据结构选图型——分组比较→柱/箱线/小提琴，趋势→折线/散点，矩阵→热图，生存/诊断等专用数据→对应专用图；选型理由写入 `chart_type_rationale`，不为好看选 3D 或双轴。
- **来自 Academic Figures**：期刊预设同时锁定图宽、字号、字体与 dpi（Nature 单栏 89 mm、双栏 183 mm、正文 7 pt、最小 5 pt、600 dpi）；给了 `journal` 就以它为准，不再自由改图宽。
- **来自 Academic Figures**：同输入必须同字节输出；交付前做“重叠门禁”——文字、图例与数据元素发生遮挡即视为未通过，不交付“差不多”的图。
- **来自 Scientific Visualization**：线稿/数据图用矢量（PDF/SVG）或 600–1200 dpi，位图照片 300–600 dpi；科学数据绝不用 JPEG；多格式导出保持同一版式。
- **来自 Scientific Visualization**：分类色用 Okabe–Ito 八色（`#E69F00 #56B4E9 #009E73 #F0E442 #0072B2 #D55E00 #CC79A7 #000000`）；连续色用 viridis/plasma/cividis 等感知均匀色图，禁用 jet/rainbow 与红绿对立，并过灰度测试。
- **来自 Scientific Visualization**：轴标签写“量 + 单位”，去掉上/右边框，图例不描边；最小字号按最终印刷尺寸算（轴标签 7–9 pt），不按屏幕预览判断。
- **两篇合取后的改造**：`result.json` 增加 `chart_type_rationale`、`palette{name, hex[]}`、`journal_preset`、`checks.overlap_free`、`checks.min_font_pt` 与 `alt_text`（一句话结论，供无障碍与图注复用）；期刊预设与自定义宽高冲突时以预设为准并记入 `change_log`。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 来源 | 每条 series 有行区间与过滤条件 | 补映射 |
| G2 | 数值 | 抽检点与表值一致 | 修数据映射后重绘 |
| G3 | 轴 | 两轴有标签与单位，范围合理 | 补标签/改范围 |
| G4 | 误差 | 有误差线则标类型与 n | 补标注或删除误差线 |
| G5 | 可读 | 无遮挡、配色可辨、字号达标 | 调整布局与配色 |
| G6 | 导出 | SVG 可解析；PNG/PDF 文件头有效 | 重新渲染 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 图值与表值不符 | 抽检点偏移 | 检查 series 映射与排序 |
| 轴无单位 | 只有数字 | 补单位与量纲 |
| 误差线无类型 | 有棒无说明 | 标 SD/SEM/CI 与 n |
| 双轴误导 | 两轴缩放夸大差异 | 改单轴或分面 |
| 颜色不可辨 | 仅红绿区分 | 加线型/标记 |
| 截断 y 轴 | 从非零开始夸大差异 | 从零或注明截断 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`series[]`、`series_map`、`artifacts`
- [ ] `chart.svg` 可编辑，PNG/PDF 真实有效
- [ ] 每条序列可追溯到行区间
- [ ] 两轴有标签与单位
- [ ] 误差线标类型与 n
- [ ] 抽检数值与源表一致
- [ ] 配色与字号满足可读性

## 最小示例（示例）

输入：`results.csv`（12 行，含 sd 与 n）+ 要求 2 面板。
输出：`fig2.svg`（bar + line）；`value-check.json` 9 点全 match；
`fig2.pdf` 1 页；`preview/fig2.png` 2126×1063 @300 dpi；`status: "completed"`。
## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/inspect_input.py` | 盘点输入与数据表 |
| `scripts/make_chart.py` | 按 figure-spec 生成 SVG/PNG/PDF |
| `scripts/validate_result.py` | 校验 result.json 与产物 |
| `references/04-graphics.md` | 图表与视觉规范 |
| `references/08-quality-gates.md` | 交付质量闸门 |
| `templates/result_envelope.json` | result.json 信封示例 |

## 本题交付对照

- 可编辑源文件：示意图、机制图、流程图或图表的分层源文件；完成标准：实体、关系、图例和数据映射正确。
- 目标格式、尺寸、分辨率、字体和色彩检查；完成标准：可在目标场景打开和使用。
- 来源与修改记录：元素来源、版本、数据映射和人工确认；完成标准：关键图面可核验，异常有说明。
