# 页序规划与内容取舍

本文件是 `scripts/build.py` 的规划规则说明，也是模型写 `deck-spec.json` 时的检查表。
核心原则：**先有证据，再有页面；一页一个结论；宁少一页，不编一句。**

## 1. 先判定工作流

| 输入信号 | 模式 | 行为 |
|---|---|---|
| `artifacts/slide-workorder.json` 有 `edits` | edit | 起始稿只读，只改点名项 |
| 有 `starting_deck` + `target_template` | adapt | 内容保留，换真实母版/版式/主题 |
| 只有 `target_template` | adapt | 在真实模板上生成 |
| 无起始稿/模板，task 说“做一份 PPT” | generate | 从源材料规划新稿 |

正式案例以 workorder 的 `mode` 为准；文件名只作线索，不作事实。
`可编辑 PPTX` 是交付格式要求，不等于 edit 模式。

## 2. 从材料中抽主线

只读实际文件（PDF/TXT/MD/DOCX/PPTX/CSV/XLSX），不要从文件名或领域标签推断内容。
按以下顺序找：

1. **研究问题**：含 `?/？`、`we ask/investigate/examine`、`aim/objective`、`本研究/是否` 的句子。
2. **方法**：Methods/材料与方法/实验 段落；步骤压到 3–4 条。
3. **结果**：含数字、单位、比较、显著性、`提高/降低/优于` 的句子；每条保留数字与条件。
4. **解释/讨论**：讨论段；不得越过材料下因果结论。
5. **局限**：Limitations/局限/不足/未来工作；没有就写“材料未报告”，不补造。
6. **结论**：Conclusion/摘要末句；最多 3 条。

源材料语言与汇报语言不一致时，模型可以翻译页面文字，但数字、单位、图号、
专有名词必须保留原值，并在 `claims.source` 写原文定位。

## 3. 页数与角色预算

默认 10–14 页；以 task/workorder 的页数为准。建议比例：

| 角色 | 页数 | 说明 |
|---|---:|---|
| title | 1 | 题目、副标题、场合/时长 |
| agenda | 0–1 | 只用真实章节名 |
| background | 1–2 | 背景 + 研究问题 |
| method | 1 | 3–4 步可复现流程 |
| result | 2–4 | 每页一个结论；至少 1 页有真实图/表 |
| discussion | 0–1 | 解释与边界 |
| limitations | 1 | 材料明确写出的限制 |
| conclusion | 1 | ≤3 条 |
| evidence | 1 | 文件 + 定位索引 |

结果页不少于总页数的 20%；方法页不超过 35%。页数不够时优先合并方法细节，
不要用重复句或空页凑数；页数超限时先删讨论/大纲，再删多余结果，保留
title/background/method/result/conclusion。

## 4. 每页字段

```json
{
  "kind": "content",
  "role": "result",
  "title": "Ni-Co 在 750 °C 维持 92% 转化率",
  "bullets": ["The Ni-Co catalyst achieved 92% CH4 conversion at 750 °C."],
  "image": "/input/materials/fig2a.png",
  "caption": "Fig. 2a · materials/paper.pdf p.5（原始图，未重绘）",
  "notes": "讲稿：……。依据：paper.pdf p.5。",
  "source": "paper.pdf · p.5",
  "source_ref": {"path": "materials/paper.pdf", "locator": "p.5"},
  "claims": [
    {"claim": "92% CH4 conversion at 750 °C",
     "source": {"path": "materials/paper.pdf", "locator": "p.5"}}
  ],
  "figure_source": {"path": "materials/paper.pdf", "locator": "p.5"}
}
```

规则：

- `title` 是 action title（结论句），不是“结果”“方法”这种空标签。
- 每条 `bullets` 只放一个 claim；数字、单位、条件与源文逐字一致。
- `notes` 2–4 句，说明“讲什么、为什么、下一页怎么接”，不得出现材料外数字。
- 内容页必须有 `claims` 或 `source_ref`；没有定位就不算证据。
- 每页至少一个视觉元素：真实图、统计卡、步骤卡、对照卡或要点；不要纯文字墙。

## 5. 图表取舍

- 源材料有真实图片（PNG/JPEG，或 PDF 内嵌位图）→ 直接嵌入，图注写文件 + 定位。
- 有真实数据表（CSV/XLSX）→ 可按表重绘，图注写表名、sheet/行范围、列名。
- 只有位图、没有数值 → 只引用原图，不从图里“读”数据。
- 缺图 → 用文字/统计卡，`figure_status` 记 `unavailable`；**禁止占位图、禁止编造曲线**。
- 同一张图不要在多页重复；图号、单位、图注与源文一致。

## 6. 模板与起始稿

- 有模板：复制真实 `slideMasters/slideLayouts/theme`，按 layout 名称映射角色；
  不新建同名版式，不覆盖主题字体/配色。layout 不匹配时用最接近版式并在
  `template.layouts_used` 记录 fallback。
- 有起始稿（edit）：基线只读；只改 workorder 点名的 slide/run/notes/image；
  未点名页 hash 不变，写入 `preserved.untouched_slides`。
- unsupported 操作（重排、删页、加页、跨格式换图）不静默执行，写
  `unresolved_edits` 并说明原因。

## 7. 受众与时长

- 组会：方法与结果细节多，背景短。
- 项目评审/基金：问题、创新点、可行性、局限与计划并重。
- 答辩：方法严谨性、结果完整性、局限与贡献。
- 同行：术语不解释，图表信息密度高。
- 12 分钟约 10–12 页；20 分钟约 14–18 页；页数上限优先于内容量。

## 8. 失败与不确定性

| 情况 | 处理 |
|---|---|
| 扫描 PDF 无文本 | 记 `uncertainty`，尝试 OCR 工具；不可用则 partial |
| 材料缺方法/局限 | 不补造；在 `exceptions` 说明缺失 |
| 数字无法回查 | 删除该数字或标 `unavailable`，不得保留 |
| 模板损坏 | 记录原因，退回独立版式并标 `template_fidelity=none` |
| 页数不够 | 合并方法/讨论，保留结论与证据索引 |
| 渲染器缺失 | 输出同源内容预览并如实标 `render.native=false` |
