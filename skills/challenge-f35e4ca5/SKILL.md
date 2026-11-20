---
name: scientific-figure-parser
description: 科研图表解析：读取折线图、柱状图、散点图、热图与多面板图，提取坐标轴、单位、图例、系列、误差棒和显著性标记，按 figure_id 与 panel 给出可定位证据。Parse scientific figures (axes, units, series, trends, error bars, significance) from a fixed case bundle into /app/output/result.json with figures[], observations[] and uncertainty. Use for 读图、图表描述、趋势比较、误差棒解释和 panel-level evidence.
---

# 科研图表解析（Scientific Figure Parser）

## 用途与触发

输入含图片/图注/坐标数据，或 task.md 要求「读图」「描述趋势」「比较曲线」「解释误差棒」时使用。

## 输入

- `/input/case.json`：`case_id`、`figures[]`（可能含 `figure_id`、`caption`、`image_path`、`panels`）、`questions[]`
- 图像文件（PNG/JPG/SVG/PDF）
- `/input/task.md`：输出要求，**以它为准**

## 执行步骤

1. **盘点图件**：`figure_id → 文件路径 → caption 全文 → panel 列表`。
2. **先读图注**：图注常直接给出变量含义、样本量 n、误差类型。
3. **解析坐标轴**：x/y 轴标签、单位、量纲、线性/对数、范围、刻度、断轴。
4. **解析系列**：图例、每个系列对应的条件、颜色/线型/标记。
5. **读数据**：优先读图上标注数值；无标注时用像素坐标反推，标 `estimated: true` 和 `read_error`。
6. **统计要素**：误差棒类型（SD/SEM/CI）、n、显著性标记（*/**/***/n.s.）及检验方法。
7. **回答**：每个问题给出结论 + 图/panel 定位 + 读数依据。

## 输出契约

```json
{
  "case_id": "...",
  "figures": [
    {
      "figure_id": "Fig.2",
      "caption": "原文图注",
      "panels": ["a", "b"],
      "axes": {"x": {"label": "...", "unit": "...", "scale": "linear"},
               "y": {"label": "...", "unit": "...", "scale": "log"}},
      "series": [{"name": "...", "condition": "...", "marker": "..."}],
      "statistics": {"error_bar": "SD|SEM|CI|none", "n": 3,
                     "significance": [{"comparison": "A vs B", "mark": "***", "test": "..."}]},
      "observations": [
        {"text": "在 350 °C 达到峰值 92%", "evidence": {"figure_id": "Fig.2", "panel": "a"},
         "estimated": true, "read_error": "±2%"}
      ]
    }
  ],
  "answers": [
    {"question_id": "q1", "answer": "...", "status": "answered|partial|insufficient_evidence",
     "claims": [{"claim_id": "q1-c1", "text": "...", "kind": "reported|derived|inferred",
                 "evidence": [{"figure_id": "Fig.2", "panel": "a", "quote": "图注或标注原文"}]}],
     "unanswered_parts": []}
  ],
  "uncertainty": [{"item": "...", "reason": "...", "impact": "..."}],
  "change_log": [{"step": 1, "action": "...", "evidence": "..."}]
}
```

## 读图规则

1. 不凭印象描述趋势；每个趋势必须给区间与端点。
2. 对数轴必须先判断再读数，误判量级是最严重错误。
3. 误差棒重叠不等于无显著差异；显著性只按原文标注陈述。
4. 图上无数字时，读数保留 2 位有效数字并给误差范围。
5. 多 panel 时逐 panel 编号，不要合并不同 panel 的条件。
6. 图与正文冲突时，列出双方，标 `conflict`，不裁决。

## 异常处理

| 情况 | 处理 |
|---|---|
| 图像无法解析 | `status=insufficient_evidence`，记录文件名与错误 |
| 图注缺失 | 从正文引用该图的段落补全，标注来源 |
| 轴单位缺失 | 保留原始刻度，标 `unit_missing: true` |
| 彩色曲线无法区分 | 说明限制，改用图例顺序推断并标 `inferred` |

## 自检

- 每个结论都能定位到 figure_id/panel
- 读数带误差与 `estimated` 标记
- 不把图注推断写成实测数值

## 快速路径（预算内）

平台预算 64 次模型请求 / 900 秒 / 单次 16384 token。先保证有可评结果，再增强：

1. **0–2 分钟**：读 `task.md` 与 `case.json`，抄下 `output_contract`；建立 `figures[] → 文件 → caption → panels` 索引。
2. **2–7 分钟**：先解析 1–2 张主图，写出 `figures[]` 骨架和 `result.json`（`status: "partial"` 也可）。
3. **7–13 分钟**：补齐其余图、逐 panel 读数、统计要素和 `answers[]`。
4. **最后 2 分钟**：只跑质量闸门与落盘核对，不再开新图。

## 方法增强（融合参考实践）

- **版面阅读顺序**：先图注，再坐标轴，再图例，再数据点，最后正文引用；不按“第一眼印象”作答。
- **Panel 清单**：每个 panel 单独记录 `panel_id / 变量 / 条件 / 系列数 / 统计标记`，禁止把不同 panel 的条件合并。
- **像素反推标定**：用轴端刻度建立像素→数值映射；对数轴先取对数再标定；每个读数值都带 `estimated` 与 `read_error`。
- **图表类型决策**：先判图种（折线/柱/散点/箱线/热图/多面板）；不同图种的“可读数”和“不可读数”不同。
- **数字二次核对**：对图中标注数字逐位复核（尤其 6/8/9/0、小数点、负号、±）；与正文或图注不一致时进入 `conflict`。
- **证据分级**：`reported`（图上明标）> `derived`（由轴/图例换算）> `inferred`（趋势解释）；推断不得写成实测。

### 定向方法增强

- **来自 MinerU 文档提取**：先判内容类型再选解析方式——文字型（坐标轴/标注/表格）走确定性读数，非文字型（显微图/凝胶/照片）只做视觉描述，混合型分别处理并标 `content_kind: text|visual|mixed`。
- **来自 MinerU 文档提取**：显式声明 `extraction_mode ∈ {deterministic, visual_description, hybrid}`；视觉描述对细节更敏感但可能编造刻度、比例尺和统计标记，凡无像素/标注支撑的描述标 `hallucination_risk: high` 并降级为 `inferred`。
- **来自 MinerU 文档提取**：多页 PDF 记录页范围与目标图所在页；语言影响 OCR（中英混排分别设定），公式/上下标按区域单独读，不与图注混读。
- **来自 图像读取器**：三模式分流——OCR（只取文字，保留原格式）、图像描述（场景/结构/趋势）、通用分析（两者都给）；文字型图不得只给“看起来上升”的描述而漏掉轴上的具体数值。
- **来自 图像读取器**：错误分类要可区分——无法解码、无文字层、格式不支持、加密、分辨率不足；分别写 `error_class`，不笼统写“读图失败”。
- **来自 图像读取器**：该参考依赖多模态云 API，本环境不可用；只用其分流方法，不调用外部服务，`hosted_service: "not_used"`。
- **两篇合取后的改造**：`figures[]` 增加 `content_kind`、`extraction_mode`、`hallucination_risk`、`language_hint`、`error_class`；`observations[]` 中 `visual_description` 类不得进入数值比较。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 图件覆盖 | `figures[]` 覆盖 task.md 要求的每张图/panel | 缺图写 `insufficient_evidence`，说明文件名与原因 |
| G2 | 坐标轴 | 每轴有 label、unit、scale、range；对数轴已标明 | 单位/刻度缺失写 `unit_missing`，不猜 |
| G3 | 读数 | 每个 observation 有 figure_id+panel；估读有 `estimated` 与误差 | 无法定位的读数删除或降级为 `inferred` |
| G4 | 统计 | 误差棒类型、n、显著性标记与原文一致 | 原文未给就不写“显著”，只描述数值差异 |
| G5 | 数值一致 | 标注数字与 quote/正文一致；冲突已列 | 冲突未解决前不得二选一 |
| G6 | 结构 | `result.json` 可解析，`case_id` 与输入一致 | 先修 JSON 再补内容 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 对数轴当线性轴读 | 相邻刻度数值跨度异常大 | 回看轴标签/log 标记，重新标定 |
| 把图注推断当实测 | observation 没有图上数字支撑 | 改 `kind: "inferred"`，加限定语 |
| 误差棒重叠即判“无差异” | 无显著性标记却写“无显著差异” | 只陈述重叠，不裁决显著性 |
| 多 panel 条件串用 | 同一结论引用了两个 panel 的不同条件 | 拆成两条 observation，各自挂 panel |
| 颜色/线型不可辨 | 灰度或色盲模式下系列混淆 | 用图例顺序 + 标记形状交叉确认，标 `inferred` |
| 单位/量级漂移 | 同一指标在不同图中单位不同 | 统一换算并保留原值，写换算系数 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`figures[]`、`answers[]`、`uncertainty`、`change_log`
- [ ] 每张图有 caption 来源与 panel 列表
- [ ] 每个 observation 有 figure_id/panel 定位
- [ ] 估读值有 `estimated: true` 与 `read_error`
- [ ] 统计要素（误差类型、n、显著性）与原文一致
- [ ] 无材料外趋势、无编造数值

## 最小示例（示例）

输入：一张折线图，x=温度(°C)，y=转化率(%)，三条曲线对应三种催化剂。
输出：`series` 三条，`observations` 给出各曲线峰值温度与峰值转化率，`estimated: true`，`read_error: "±2%"`。
