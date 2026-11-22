# 科研图表解析参考（v0.6）

配合 `templates/figure-analysis.json`、`scripts/check_figure_analysis.py`、
`scripts/calibrate_figure_axes.py` 使用。**task.md 与 case.json 优先**；图上没有的
数值不要补，读数必须带定位与误差。本文件的第一原则是：**先判模式，再答题；答案优先，
证据随后。**

## 1. 先判 figure_mode（不要跳过）

| 模式 | 识别信号 | 处理方式 |
|---|---|---|
| `quantitative_chart` | 有数值坐标轴：折线、柱、散点、箱线、热图 colorbar、光谱/色谱、剂量曲线 | 读轴 → 图例 → 点/柱 → 统计标记；建 plot table |
| `visual_figure` | 显微图、凝胶/电泳/印迹、照片、装置图、无数据坐标轴的示意图 | 只写 panel、结构/形态/相对位置、比例尺原文；不做数值反推 |
| `multi_panel` | 同一 figure 含多个 panel，类型可能不同 | 逐 panel 判模式，写 `panel_modes`；禁止跨 panel 合并条件 |

判定顺序：

1. 有没有**数值坐标轴**？有 → `quantitative_chart`；没有 → `visual_figure`。
2. 一张图里部分 panel 有轴、部分没有 → `multi_panel`，`panel_modes` 覆盖全部 panel。
3. 图上印刷的数字（柱顶标签、比例尺文字、显微图标注）可引用为 `reported`；
   没有印刷数字的视觉内容只能用 `kind: "visual"` 描述。
4. 无法解码、缺文件、只有图注没有像素 → 进 `unreadable[]`，不要猜图型。

同时记录 `content_kind`（text|visual|mixed）、`extraction_mode`
（deterministic|visual_description|hybrid）、`hallucination_risk`（low|medium|high）。

## 2. 答案优先的读图顺序

1. **读 task.md**：输出契约、必做对象（哪些图/表/panel）、问题清单。
2. **抄 questions**：保留 `question_id` 与原问句；逐题确认问的是数值、趋势、差异、
   机制解释还是视觉特征。
3. **先答完所有问题**：写 `answers[]`（`answer` / `status` / `claims[]` /
   `unanswered_parts[]`），每题至少挂一个 `figure_id + panel`。
4. **再补证据**：定量图补 `plot_tables` / `axis_check` / `panel_map`；视觉图补
   `observations` 与比例尺描述。
5. **最后**补 `question_figure_map`、`panel_coverage`、`unreadable[]` 和官方字段。

时间不足时保住第 3 步；不要为了建全表而不答题。

## 3. 定量图：读轴与读数

读数顺序：**图注 → 坐标轴 → 图例 → 数据点/柱/区域 → 统计要素 → 正文引用**。

- 轴三要素：label、unit、scale（linear/log）、range、ticks、断轴、双轴。
- 对数轴先取 `log10` 再标定；把 log 当 linear 是最严重的量级错误。
- 图上明标数字优先：`estimated: false`；无标注才估读：`estimated: true` +
  `read_error`（半最小刻度，或满量程 1–2%）。
- 读数保留 2 位有效数字；`unit_missing: true` 时保留原始刻度，不猜单位。
- 只读问题相关点、峰值、端点、交点、比较点；不逐点穷举。

像素反推标定（仅定量图）：

```
线性: value = v1 + (pixel - p1) * (v2 - v1) / (p2 - p1)
对数: log10(value) 先做线性插值，再取 10^x
```

```bash
python3 "$SKILL_DIR/scripts/calibrate_figure_axes.py" \
  --calib /app/work/axis-calibration.json \
  --points /app/work/pixel-points.json --out /app/work/readings.json --json
```

标定脚本只做数学换算，不识别图像；轴像素和数值必须由你从图上读出。

### 3.1 图型可读性速查

| 图型 | 可读 | 不可读/易错 |
|---|---|---|
| 折线 | 峰值、端点、交点、趋势区间 | 无标注的精确中间值 |
| 柱状 | 柱高、误差棒、显著性 | 3D 柱的透视高度 |
| 散点 | 范围、聚类、离群点 | 点重叠处的个体值 |
| 箱线 | 中位数、四分位、须 | 原始样本分布 |
| 热图 | colorbar 对应值、区块 | 无 colorbar 的绝对数值 |
| 光谱/色谱 | 峰位、峰高、相对强度 | 无标注的绝对浓度或物质归属 |
| 显微/凝胶 | 结构、相对位置、标尺 | 绝对定量、分子量精确值 |

## 4. 视觉图：只描述，不反推

`visual_figure` 的 observations：

- `kind: "visual"`、`quantitative: false`、`estimated: false`。
- 写目标结构、形态、颜色/对比度、相对位置、panel 编号、比例尺**原文**。
- 不写“平均长度 X µm”“强度提高 Y%”“分子量 Z kDa”这类没有图上标注支撑的数值。
- 比例尺默认只转录文字（如 `100 µm`），不做像素测量。仅当 task.md 明确要求尺寸且
  比例尺清晰时，才可做一次近似换算：`estimated: true`、`method: "scale_bar"`、
  `read_error`；否则在 `uncertainty` 写 `size_not_measured`。
- 照片/示意图没有定量对象时，`statistics.error_bar` 写 `not_applicable`，`n: null`，
  `uncertainty` 说明“不适用”。
- 视觉描述不得进入数值比较；只能支撑“可见/不可见、增多/减少、位于某区域”等定性结论。

凝胶/印迹特别注意：条带**位置、相对强弱、有无**可描述；分子量、灰度定量、归一化
数值没有标注就不写。显微图特别注意：比例尺、视野、目标结构、聚集/分布可描述；
没有比例尺就没有尺寸。

## 5. 多面板

- 每个 panel 单独记录 `panel_id / figure_mode / 变量或特征 / 条件 / 统计标记`。
- 同一结论引用两个 panel 时拆成两条 observation，各自挂 panel。
- 不把 a 的数值挂到 b；不用总图标题代替 panel 证据。
- `panel_coverage` 覆盖每个 panel；未覆盖的写 `covered: false` + `reason`。
- `multi_panel` 的 `panel_modes` 必须覆盖实际 panel；模式不一致时在 `uncertainty` 说明。

## 6. 误差棒与显著性

- 误差棒类型只按图注写：SD / SEM / CI / IQR / min–max / none / not_provided；
  视觉图或非定量对象写 `not_applicable`。
- n 只在图注/正文明确给出时写；没有写 `n: null`。
- 误差棒重叠不等于“无显著差异”；显著性只按原文标记陈述。
- `* / ** / *** / n.s.` 记录比较对象与检验方法；没有就写 `test: null`。
- 不把“看起来分离”写成“显著”。

## 7. 问题—证据映射

```json
{"question_id": "q1", "figure_id": "Fig.2", "panel": "a",
 "locator_kind": "figure|table|paragraph", "note": "峰值、误差棒"}
```

- 每问至少一条 `{figure_id, panel}`。
- 纯原表问题用 `figure_id: "Table.1"`、`panel: "table"`、`locator_kind: "table"`，
  并在 `uncertainty` 说明该定位不是图像 panel。
- 映射指向的 figure/panel 必须真实存在；不存在就改映射或标
  `insufficient_evidence`。

## 8. 字面表格（材料要求原表时）

- `tables[]` 逐字转录：`table_id`、`source_locator`、`columns`（含单位）、`rows`、
  `conditions`、`notes`、`verbatim: true`。
- 不改列、不交换行列、不换算单位、不补缺失单元格；读不出写 `null` 并说明。
- 表头数字与单位逐字保留；没有单位不等于无量纲，写 `unit_missing` 或 `not_provided`。
- 表格结论回到 table_id + 单元格定位，不用图注推断替代。

## 9. 证据分级

- `reported`：图上明标数字/图注明说/表格原文；
- `derived`：由轴/图例/比例尺换算；
- `inferred`：趋势解释；
- `visual`：视觉描述（不含数值）。

推断不得写成实测；`inferred` 与 `visual` 要加限定语并说明依据。
图与正文冲突时列出双方，标 `conflict`，不裁决。

## 9.1 常见失败模式与处置

| 信号 | 风险 | 处置 |
|---|---|---|
| 无坐标刻度 | 无法建立像素→数值映射 | `insufficient_evidence`，不猜刻度 |
| 图被裁剪 | 端点/范围不完整 | 只描述可见区域，记录裁剪，不外推 |
| 图例缺失 | 系列对应错误 | 用图注/正文/标记形状推断，标 `inferred` |
| 单位不明 | 量级错误 | `unit_missing: true`，保留原刻度，不猜单位 |
| 图像过曝/低对比 | 结构或颜色不可辨 | 描述可见部分，标不可读区域，不补细节 |
| 曲线重叠 | 个体值不可分 | 只读可分辨段，标 overlap，不拆出个体值 |
| 热图无 colorbar | 绝对数值不可读 | 只描述区块与相对高低 |
| 无比例尺的显微图 | 尺寸不可换算 | `size_not_measured`，不给 µm 数值 |
| 多 panel 条件串用 | 结论错配 | 拆 observation，逐 panel 挂证据 |

## 10. 方法来源（只吸收方法）

- **ChartQA**：问题常需多步逻辑/算术推理，且包含视觉特征题；答案要直接，但 claims
  要保留推理链与图/panel 定位。
- **DePlot**：两阶段——先把图转成线性化表格，再在表格上推理。本 skill 只对
  quantitative panel 采用这一步，且只读问题相关点，避免“为建表而建表”。
- **MatCha**：plot deconstruction + 数值推理；支持先拆轴/图例/统计要素再回答。
- **WebPlotDigitizer**：两点（或多点）轴标定，linear/log 分开处理，人工给像素点并
  核验；`scripts/calibrate_figure_axes.py` 只做对应数学换算。
- **多 panel 实践**：逐 panel 独立判模式与证据，禁止跨 panel 合并条件。
- 未复制任何外部文件、代码、提示词或数据。

## 11. 交付前检查

- [ ] 每个 question 有 `answers[]` 条目、非空 `answer`、至少一条 figure/panel 映射。
- [ ] 输入的每张图被 `figures[]` 或 `unreadable[]` 覆盖；每个 panel 在
      `panel_coverage` 有记录。
- [ ] `visual_figure` 没有 plot table、没有数值 series、没有 `quantitative: true`。
- [ ] 定量图每轴有 label/unit/scale/range；`estimated: true` 必有 `read_error`。
- [ ] 误差类型、n、显著性只按原文写，缺失写 `not_provided`/`null`。
- [ ] `answers[].claims[].evidence` 能指回 figure/panel/图注/表格。
- [ ] 顶层 `status/result/claims/uncertainties` 存在。
- [ ] `check_figure_analysis.py` 为可选审计；失败时已记录 `audit_unavailable`。
