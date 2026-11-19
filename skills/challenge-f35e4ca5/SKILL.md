---
name: scientific-figure-parser
description: 科研图表解析：先判定 quantitative_chart / visual_figure / multi_panel，再按问题逐条回答。定量图做坐标轴、单位、图例、系列、误差棒、显著性标记与 plot table；显微图/凝胶/照片只做 panel、特征、比例尺与视觉描述，不反推数值。Parse scientific figures into /app/output/result.json with answer-first questions[], figure_id + panel evidence, question_figure_map, panel_coverage and honest uncertainty. Use for 读图、图表描述、趋势比较、误差棒解释、光谱峰、显微图像、多面板证据定位.
---

# 科研图表解析（Scientific Figure Parser）

## 用途与触发

把固定 case 中的原图片（必要时含原表）读成**可核验的结构化结果**：先答完
`questions[]`，每个答案都带 `figure_id + panel` 定位；再补定量图的 plot table、
轴检查与视觉图的特征描述。

触发信号：`/input/case.json` 含 `figures[]` / `questions[]`；task.md 出现「读图」
「图表解析」「趋势」「比较曲线」「误差棒」「显著性」「光谱峰」「显微图像」
「比例尺」「panel」「提取坐标」「curve extraction」「figure」等词。

不适用：没有材料的开放绘图、纯写作、纯翻译。遇到时说明边界，不要硬套读图流程。

核心原则：

1. **先判模式，再答题**：定量图与视觉图用不同证据规则，绝不混用。
2. **答案优先**：plot table 是证据，不是前置闸门；先让每个问题有答案和定位。
3. **不编造**：视觉图不反推数值；未给像素的图只列缺口；读不出就写读不出。

## 输入与路径

```bash
# 平台通常把技能装在 /skills/weekly-skill；先解析，不要照抄字面量
SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
if [ ! -f "$SKILL_DIR/SKILL.md" ]; then
  SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
fi
echo "SKILL_DIR=$SKILL_DIR"
```

- `/input/case.json`：`case_id`、`figures[]`（`figure_id`、`caption`、`image_path`、
  `panels`）、`questions[]`；可能含 `tables[]` / `material.json`
- 图像文件（PNG/JPG/SVG/PDF）；PDF 只读目标页，记录页码
- `/input/task.md`：输出要求与必做对象，**以它为准**
- 运行预算：64 次模型请求 / 900 秒 / 单次 16384 token。先落盘可评结果，再增强。

## 支持文件（按需使用，不阻塞）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/figure-reading-playbook.md` | 第一次判模式、读数、写视觉描述前 | 先读；按其中的模式决策、读数顺序与失败模式执行 |
| `templates/figure-analysis.json` | 写 `/app/output/result.json` 骨架时 | 复制结构，替换示例为真实 figure/panel/答案 |
| `templates/figure-legend.md` | task.md 要求补图注时 | 写可独立成立的图注；不加图上没有的数值 |
| `scripts/check_figure_analysis.py` | 可选审计 | `python3 "$SKILL_DIR/scripts/check_figure_analysis.py" --result /app/output/result.json --input /input/case.json --json` |
| `scripts/calibrate_figure_axes.py` | 可选：仅定量图需要像素反推时 | `python3 "$SKILL_DIR/scripts/calibrate_figure_axes.py" --calib /app/work/axis-calibration.json --points /app/work/pixel-points.json --out /app/work/readings.json --json` |
| `README.md` | 了解接口与最小调用 | 与 SKILL.md 同步维护 |

脚本是**可选加速器**：失败、缺依赖或时间不足时直接手工完成同一检查，并在
`uncertainty` 写 `audit_unavailable`。**不要为了通过 checker 删减真实答案或编造字段。**
标定脚本只做数学换算，不识别图像；轴像素与数值必须由你从图上读出。视觉图不使用标定脚本。

## 第 1 步：盘点输入并判定 figure_mode（约 15% 预算）

先读 `task.md`、`case.json` 和 `output_contract`，把 `questions[]` 原样抄下
（`question_id` 与问句都要保留）。task.md 列出的**必做原表**也要逐项抄下，
`table_ids` 与必做对象一一对应；若 case.json 有
`organizer_supplied_scope.required_literal_source_table_ids`，逐个抄入 `table_ids`。
写 `/app/work/figure-inventory.json`：

```json
{"case_id": "C01",
 "figures": [{"figure_id": "Fig.1", "path": "artifacts/fig1.png",
              "caption": "图注原文", "panels": ["a", "b"],
              "figure_mode": "quantitative_chart|visual_figure|multi_panel",
              "panel_modes": {"a": "quantitative_chart", "b": "visual_figure"},
              "scale_bar": "100 µm（图上标注原文）或 null",
              "readable": true, "table_ids": []}],
 "questions": [{"question_id": "q1", "text": "原问句"}]}
```

### figure_mode 判定表（第一步必做）

| 模式 | 识别信号 | 允许做 | 明确禁止 |
|---|---|---|---|
| `quantitative_chart` | 数值坐标轴；折线/柱/散点/箱线/热图；有 x/y 刻度的光谱、色谱、剂量曲线 | 轴三要素、图例、系列、plot table、像素标定、峰值/端点/交点读数 | 无标注时把估读写成 `reported`；逐点穷举挤占答题预算 |
| `visual_figure` | 显微图、凝胶/电泳/印迹、照片、装置图、无数据坐标轴的示意图 | panel 编号、结构/形态/颜色/相对位置、比例尺原文、视觉描述 | 反推轴值、峰强、series 数值；把“看起来更大”写成测量值；无比例尺或题目未要求时做尺寸换算 |
| `multi_panel` | 同一 figure 含多个 panel，且 panel 类型可能不同 | 逐 panel 判模式并写 `panel_modes`；逐 panel 描述/读数 | 跨 panel 合并条件、把 a 的数值挂到 b、用总图结论代替 panel 证据 |

判定规则：

1. 有数值坐标轴 → `quantitative_chart`；没有数值坐标轴、只有图像内容 →
   `visual_figure`。混合图（如带标注/比例尺的显微图、带示意图的统计图）按
   `multi_panel` 逐 panel 处理；只有一个 panel 时按该 panel 的模式处理，并在
   `content_kind: "mixed"` 说明。
2. 图上**印刷的数字**（如柱顶标签、比例尺文字、显微图标注）属于 `reported`，
   可以引用；`visual_figure` 的其他内容一律 `kind: "visual"`，不做数值换算。
3. 同一 figure 内既有定量 panel 又有视觉 panel 时，顶层 `figure_mode` 写
   `multi_panel`，`panel_modes` 必须覆盖每个 panel。
4. 图像无法解码、文件缺失、只有图注没有像素 → 不猜图型，直接进 `unreadable[]`；
   `error_class` 取 `decode_failed|missing_file|unsupported_format|no_pixels|not_provided`，
   `not_provided` 表示原论文有图注但组织方未给像素，是**缺口**，不是必须补齐的任务。

## 第 2 步：先答完全部 questions（约 40% 预算）

对 `questions[]` 的每一个 `question_id` 写一条 `answers[]`，**不允许跳过**：

- `answer`：直接回答问句，包含数值、单位、条件、比较对象；问“是什么/趋势/差异/依据”
  就正面回答，不写空泛综述。
- `status`：`answered`（有材料支撑的完整回答）| `partial`（只答出部分子问题）|
  `insufficient_evidence`（材料不足/图不可读，说明缺什么）。
- `claims[]`：支撑该答案的关键论断；每条有 `claim_id`、`text`、`kind`
  （`reported|derived|inferred|visual`）和 `evidence[]`（至少一个
  `figure_id + panel + quote`）。
- `unanswered_parts[]`：哪些子问题没答、为什么。

答题预算守卫：

- 每个问题先给 1 条主答案；再补 1–4 条关键 claims，不把答案写成小作文。
- 问题涉及的数值点优先于全图数字化；只读峰值、端点、交点、比较点和图注明标值。
- 允许先落盘 `status: "partial"` 的 `result.json`，随后逐题增强；不要为了补表推迟答题。
- 某个问题无法从图读出时，写 `insufficient_evidence` 并说明缺轴/缺标注/缺像素，
  不要用其他 panel 或常识补数。
- 原表问题：evidence 用 `{figure_id: "Table.N", panel: "table", quote: 表头+单元格原文}`，
  数字、单位、条件逐字保留；不用图注推断替代表格原文。

## 第 3 步：补定量图证据（约 20% 预算，仅 quantitative panel）

只为**被问题问到且确为定量**的 figure/panel 建 `plot_tables`：

```json
{"figure_id": "Fig.2", "panel": "a", "chart_type": "line|bar|scatter|box|heatmap",
 "axes": {"x": {"label": "Temperature", "unit": "°C", "scale": "linear", "range": [300, 400]},
          "y": {"label": "Conversion", "unit": "%", "scale": "linear", "range": [0, 100]}},
 "series": [{"name": "Catalyst A", "condition": "1 bar",
             "points": [{"x": 350, "y": 92, "estimated": false, "read_error": "0"}]}],
 "statistics": {"error_bar": "SD|SEM|CI|IQR|min-max|none|not_provided|not_applicable",
                "n": 3, "significance": []}}
```

规则：

1. 先读轴：label、unit、linear/log、range、ticks、断轴、双轴；log 轴先判再读数。
2. 图上明标数字 → `estimated: false`；估读 → `estimated: true` 且必须有
   `read_error`（如 `"±2%"` 或 `"±0.5 °C"`），读数保留 2 位有效数字。
3. 误差棒类型、n、显著性标记与检验方法**只按图注/正文写**；没写就
   `not_provided` / `null`，不得补造。误差棒重叠不等于“无显著差异”。
4. 只覆盖问题相关的点，不做逐点穷举。点数多时在 `plot_tables[].sampling`
   写 `"selected_points"` 及选择标准。
5. 同时把轴结论写 `axis_check`、panel 条件写 `panel_map`（结构见输出契约）；
   两者只针对定量 panel，不为视觉 panel 生成。

## 第 4 步：补视觉图描述与 panel 覆盖（约 10% 预算）

`visual_figure`（或 `multi_panel` 中的视觉 panel）只写以下内容：

- `observations[]`：`kind: "visual"`，`quantitative: false`，`estimated: false`；
  描述目标结构、形态、颜色/对比度、相对位置、panel 编号、比例尺**原文**。
- 比例尺：把图上文字原样写入 `scale_bar`（如 `"100 µm"`）；默认**不做**像素测量。
  仅当 task.md 明确要求尺寸且比例尺清晰时，才可做一次近似换算，并写
  `estimated: true`、`method: "scale_bar"`、`read_error`；否则在 `uncertainty`
  写 `size_not_measured`，不要给数字。
- 统计标记只按图注写；照片/示意图没有定量对象时，`statistics.error_bar` 写
  `not_applicable`，`n: null`，并在 `uncertainty` 说明“不适用”。
- 视觉描述不得进入数值比较，不得支撑“更高/更低 X%”这类定量结论；只能支撑
  “可见/不可见、增多/减少、位于某区域”等定性结论。

每个 figure/panel 都要进 `panel_coverage`：

```json
{"figure_id": "Fig.3", "panel": "b", "figure_mode": "visual_figure",
 "covered": true, "evidence": "observations:Fig.3/b", "notes": ""}
```

`covered: false` 必须有 `reason`，并与 `unreadable[]` 或 `uncertainty` 一致。
`multi_panel` 的 `panel_modes` 与实际处理方式必须一致。

## 第 5 步：官方字段、边界与冲突（约 10% 预算）

- 顶层官方 reading 别名**必须存在**：`status`、`result`、`claims`、`uncertainties`。
  `status ∈ answered|partial|insufficient_evidence|input_error`。聚合规则：全部问题
  有完整答案 → `answered`；有部分/未答 → `partial`；关键图不可读且无法回答 →
  `insufficient_evidence`；输入缺失或损坏 → `input_error`。
- `result` 放题目主结果（建议：`summary` + `answers` 摘要）；`claims` 至少汇总
  `answers[].claims`；`uncertainties` 与内部 `uncertainty` 保持一致。
- `uncertainty[]` 逐条写未报告、无法判断、冲突、人工复核项；有 `item/reason/impact`。
- 图与正文/图注冲突时列双方，标 `conflict`，不裁决、不静默择一。
- 材料含**字面表格**时（task.md 要求“原表”），用 `tables[]` 逐字转录：
  `table_id`、`source_locator`、`columns`（含单位）、`rows`、`conditions`、`notes`、
  `verbatim: true`。不改列、不换算单位、不补缺失单元格；读不出写 `null` 并说明。

## 官方输出契约

`example-input.json` 的 `output_contract` 要求顶层
`case_id/status/result/claims/uncertainties`。题目字段全部保留，官方字段是**追加**。
可运行官方适配器补别名（失败就手工补）：

```bash
python3 "$SKILL_DIR/scripts/emit_official.py" \
  --result /app/output/result.json --out /app/output/result.json \
  --main-field result --profile reading --json
```

该适配器只在提交包内含 `scripts/emit_official.py` 时可用；没有该文件就手工写四个字段。

## 输出契约（精简但完整）

```json
{
  "case_id": "C01",
  "figures": [
    {
      "figure_id": "Fig.2",
      "caption": "图注原文",
      "panels": ["a"],
      "figure_mode": "quantitative_chart",
      "panel_modes": {"a": "quantitative_chart"},
      "content_kind": "text",
      "extraction_mode": "deterministic",
      "hallucination_risk": "low",
      "axes": {
        "x": {"label": "Temperature", "unit": "°C", "scale": "linear", "range": [300, 400]},
        "y": {"label": "Conversion", "unit": "%", "scale": "linear", "range": [0, 100]}
      },
      "series": [{"name": "Catalyst A", "condition": "1 bar", "marker": "circle"}],
      "statistics": {"error_bar": "SD", "n": 3,
                     "significance": [{"comparison": "A vs B", "mark": "***", "test": "t-test"}]},
      "observations": [
        {"text": "Catalyst A 在 350 °C 达到峰值 92%", "kind": "derived",
         "quantitative": true, "estimated": true, "read_error": "±2%",
         "evidence": {"figure_id": "Fig.2", "panel": "a", "quote": "graph reading"}}
      ]
    },
    {
      "figure_id": "Fig.3",
      "caption": "显微图图注：scale bar 100 µm",
      "panels": ["a"],
      "figure_mode": "visual_figure",
      "panel_modes": {"a": "visual_figure"},
      "content_kind": "visual",
      "extraction_mode": "visual_description",
      "hallucination_risk": "medium",
      "axes": null,
      "series": [],
      "statistics": {"error_bar": "not_applicable", "n": null, "significance": []},
      "observations": [
        {"text": "可见较多针状晶体，沿 panel 左侧聚集；比例尺标注 100 µm",
         "kind": "visual", "quantitative": false, "estimated": false,
         "evidence": {"figure_id": "Fig.3", "panel": "a", "quote": "scale bar 100 µm"}}
      ]
    }
  ],
  "question_figure_map": [
    {"question_id": "q1", "figure_id": "Fig.2", "panel": "a",
     "locator_kind": "figure", "note": "峰值与误差棒"},
    {"question_id": "q2", "figure_id": "Fig.3", "panel": "a",
     "locator_kind": "figure", "note": "形貌与比例尺"}
  ],
  "panel_coverage": [
    {"figure_id": "Fig.2", "panel": "a", "figure_mode": "quantitative_chart",
     "covered": true, "evidence": "plot_tables:Fig.2/a", "notes": ""},
    {"figure_id": "Fig.3", "panel": "a", "figure_mode": "visual_figure",
     "covered": true, "evidence": "observations:Fig.3/a", "notes": ""}
  ],
  "unreadable": [],
  "plot_tables": [
    {"figure_id": "Fig.2", "panel": "a", "chart_type": "line",
     "axes": {"x": {"label": "Temperature", "unit": "°C", "scale": "linear"},
              "y": {"label": "Conversion", "unit": "%", "scale": "linear"}},
     "series": [{"name": "Catalyst A", "condition": "1 bar",
                 "points": [{"x": 350, "y": 92, "estimated": true, "read_error": "±2%"}]}],
     "statistics": {"error_bar": "SD", "n": 3, "significance": []},
     "sampling": "selected_points"}
  ],
  "axis_check": {"Fig.2": {"x": "linear 300–400 °C", "y": "linear 0–100 %", "issues": []}},
  "panel_map": {"Fig.2": [{"panel": "a", "variables": ["Temperature", "Conversion"],
                            "conditions": ["Catalyst A/B", "1 bar"]}]},
  "answers": [
    {"question_id": "q1", "answer": "Catalyst A 在 350 °C 达峰 92%（估读 ±2%），高于 B 的 78%。",
     "status": "answered",
     "claims": [
       {"claim_id": "q1-c1", "text": "A 峰值 92% at 350 °C", "kind": "derived",
        "evidence": [{"figure_id": "Fig.2", "panel": "a", "quote": "peak"}]},
       {"claim_id": "q1-c2", "text": "B 峰值 78% at 360 °C", "kind": "derived",
        "evidence": [{"figure_id": "Fig.2", "panel": "a", "quote": "peak"}]}
     ],
     "unanswered_parts": []},
    {"question_id": "q2", "answer": "显微图可见针状晶体沿左侧聚集；比例尺 100 µm；未做尺寸换算。",
     "status": "answered",
     "claims": [
       {"claim_id": "q2-c1", "text": "针状晶体沿左侧聚集；比例尺 100 µm", "kind": "visual",
        "evidence": [{"figure_id": "Fig.3", "panel": "a", "quote": "scale bar 100 µm"}]}
     ],
     "unanswered_parts": []}
  ],
  "tables": [],
  "support_files_used": ["references/figure-reading-playbook.md"],
  "uncertainty": [
    {"item": "Fig.2 读数", "reason": "无逐点数据，按轴估读", "impact": "±2%"}
  ],
  "change_log": [{"step": 1, "action": "判模式并建立 question_figure_map", "evidence": "case.json"}],
  "status": "answered",
  "result": {"summary": "q1: A 在 350 °C 达峰 92%；q2: 显微图可见针状晶体，比例尺 100 µm"},
  "claims": [
    {"claim_id": "q1-c1", "kind": "derived", "evidence": "Fig.2 panel a",
     "source_id": "C01-PAPER", "location": "Fig.2a"},
    {"claim_id": "q2-c1", "kind": "visual", "evidence": "Fig.3 panel a",
     "source_id": "C01-PAPER", "location": "Fig.3a"}
  ],
  "uncertainties": [
    {"item": "Fig.2 读数", "reason": "无逐点数据，按轴估读", "impact": "±2%"}
  ]
}
```

## 轻量自检（交付前手工核对，不依赖脚本）

1. `result.json` 可解析；`case_id` 与输入一致。
2. `questions[]` 每个 id 都有 `answers[]` 条目，且 `answer` 非空。
3. 每个问题在 `question_figure_map` 至少一条 `{figure_id, panel}`；指向的
   figure/panel 真实存在（原表用 `figure_id: "Table.1", panel: "table"` 约定，
   `locator_kind: "table"`）。
4. 输入的每张图要么在 `figures[]`，要么在 `unreadable[]` 且写明 `error_class`；
   每个 panel 在 `panel_coverage` 有记录。
5. `visual_figure` 没有 `plot_tables` 条目、没有数值 `series.points`、没有
   `quantitative: true` 的 observation；只引用图上印刷的数字。
6. 定量图每轴有 label/unit/scale；`estimated: true` 必有 `read_error`；
   `unit_missing` 时没有猜单位。
7. 误差棒类型、n、显著性只按原文；没有写 `not_provided`/`null`。
8. 顶层 `status/result/claims/uncertainties` 四个官方字段存在且类型正确。

## 质量闸门

**必须通过（缺一即修）：**

| # | 检查 | 不通过时 |
|---|---|---|
| G1 | result.json 存在、可解析、case_id 一致 | 先修 JSON |
| G2 | 每个 question 有答案和 `figure_id + panel` 映射 | 补答案/映射；确实无证据则 `insufficient_evidence` + 缺口说明 |
| G3 | 每张输入图被 `figures[]` 或 `unreadable[]` 覆盖 | 补盘点；`not_provided` 只列缺口，不构造 |
| G4 | 每个 panel 在 `panel_coverage` 有记录 | 补覆盖 |
| G5 | visual figure 无定量输出（见自检 5） | 删除编造数值，改 `kind: "visual"` |
| G6 | 定量图轴三要素齐全；估读有 `read_error` | 补轴或标 `unit_missing` |
| G7 | 官方四字段存在 | 手工补或运行适配器 |

**建议（不阻塞）：** 运行 `check_figure_analysis.py` 且 `ok: true`；用标定脚本复核
像素读数；补写图注；把关键读数与正文二次核对。

## 异常处理

| 情况 | 处理 |
|---|---|
| 缺 `case.json` / 无法解析 | `status: "input_error"`，记录路径与错误，不输出猜测 |
| 图像无法解码 | 进 `unreadable[]`，`error_class: "decode_failed"`；相关题 `insufficient_evidence` |
| 只有图注、组织方未给像素 | `error_class: "not_provided"`；明确列为缺口，**不是**待补任务 |
| 轴单位缺失 | 保留原始刻度，`unit_missing: true`，不猜单位 |
| log/线性无法判断 | 说明依据不足，`insufficient_evidence`；不按线性硬读 |
| 图注缺失 | 从正文引用该图的段落补，标来源；没有就写 `caption_missing` |
| 多 panel 编号不清 | 按图注/正文顺序编号并在 `uncertainty` 说明推断依据 |
| 图被裁剪/过曝/低对比 | 只描述可见区域，记录不可读区域；不补全被裁掉的数据 |
| 曲线重叠/颜色不可辨 | 只读可分辨段，用图例顺序 + 标记形状交叉确认，标 `inferred` |
| PDF 页渲染/文字层失败 | 记录页码与错误；用可读页/文字层继续，相关题 `insufficient_evidence` |
| 视觉图无法定量 | 明确写“不做定量”，只给视觉描述；不生成 plot table |
| 时间不足 | 保住 `answers[]` + `question_figure_map` + 官方字段；其余标 `partial` |
| checker/标定脚本失败 | 不阻塞；手工核对，`uncertainty` 写 `audit_unavailable` |

## 最小示例（示例，不是正式答案）

**定量图**：折线图 x=温度(°C)、y=转化率(%)、三条催化剂曲线。先答“哪种催化剂在
何温度达峰、峰值多少、与对照差多少”，每题挂 `Fig.2/a`；再补
`plot_tables` 的峰值/端点，估读标 `estimated: true`、`read_error: "±2%"`。

**视觉图**：显微图带 `100 µm` 比例尺。只写“可见针状晶体、沿边缘聚集、比例尺
100 µm”，`kind: "visual"`，不写“平均长度 X µm”（除非题目明确要求尺寸且做了
带误差的近似换算）。

**多面板**：Fig.4a 柱状图（误差棒 SEM、`**`）→ 定量；Fig.4b 凝胶条带 → 视觉。
`panel_modes: {"a": "quantitative_chart", "b": "visual_figure"}`；两者分别写证据，
不合并条件、不跨 panel 比较数值。

## 方法来源（只吸收方法，不复制文件）

- **ChartQA / DePlot**：定量图先 derender 成线性化表格（轴、图例、series、点）再推理；
  本 skill 把这一步限制在 quantitative panel，且只覆盖问题相关点，答案先行、表格随后。
- **MatCha**：plot deconstruction + 数值推理；对应轴/图例/统计要素检查，但不改变输出契约。
- **WebPlotDigitizer**：两点轴标定（linear/log）+ 人工核验；对应
  `scripts/calibrate_figure_axes.py`，像素点由人给出，脚本只做换算。
- **多 panel 实践**：逐 panel 独立判模式、独立证据，禁止跨 panel 合并条件。
- 以上仅为方法参考；未复制任何外部代码、提示词或数据文件，也未使用外部服务。

## 版本与限制

- 版本：0.6.0（双模式 + 答案优先；plot table 降为定量图的可选证据结构）。
- 视觉图只做定性描述；不保证对显微结构做生物学解释，也不做无比例尺的尺寸测量。
- 定量图读数受图像分辨率与标注限制；估读误差按半刻度或满量程 1–2% 给。
- 不逐点穷举整图，只覆盖问题相关点、峰值、端点、交点与图注明标值。
- 平台可能把技能装到题目专属目录；任何脚本命令前先解析 `$SKILL_DIR`。
