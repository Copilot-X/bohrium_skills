---
name: talk-content-organizer
description: 学术演讲内容组织：先分析听众与核心 takeaway，再设计主线故事、逐分钟时间预算、每页一个信息点、讲稿与问答准备，输出大纲与对应 deck。Organise an academic talk with audience/takeaway analysis, storyline, timing, one message per slide, speaker script and Q&A. Use for 演讲结构、报告内容、口头汇报、答辩陈述。
---

# 学术汇报内容组织（Talk Content Organisation）

## 用途与触发

task.md 要求"组织汇报内容""设计讲述逻辑""排时间""准备答辩/组会/会议报告"时使用。
与"论文转 PPT"（`ppt-c7b80f0d`）的区别：本题的评分重点是**内容结构与时间控制**，
幻灯片只是承载物；讲稿与 Q&A 准备是一等产物。

## 输入

- `/input/task.md`：场合（组会/答辩/会议/科普）、受众、时长、是否提问、语言
- `/input/case.json`：`case_id`、`source_materials[]`、`required_points[]`
- 材料：论文/数据/旧 deck/评审意见
- 可选：模板、场地限制（无网络、投影比例）

## 执行步骤

1. **受众与目标**：`/app/work/audience.json`
   ```json
   {"occasion": "thesis defence", "audience": "committee (3 experts + 2 adjacent)",
    "prior_knowledge": {"methods": "high", "domain": "medium"},
    "listener_question": "这个方法凭什么可信？",
    "takeaways": ["问题真实存在", "方法可复现", "结论有边界"],
    "forbidden": ["未发表数据", "过度承诺"]}
   ```
   听众关心什么，决定先讲什么——不按论文顺序照搬。
2. **主线一句话**：`throughline`，全篇所有页都服务于它。
3. **时间预算**：`/app/work/timing.json`
   ```json
   {"total_min": 15, "speaking_min": 11, "qa_min": 4,
    "blocks": [
      {"block": "开场", "min": 1.0, "slides": [1]},
      {"block": "问题", "min": 2.0, "slides": [2, 3]},
      {"block": "方法", "min": 3.0, "slides": [4, 5, 6]},
      {"block": "结果", "min": 4.0, "slides": [7, 8, 9, 10]},
      {"block": "局限与结论", "min": 1.0, "slides": [11, 12]}]}
   ```
   经验值：1 页 ≈ 45–60 秒；方法不超过总时长 30%；结果 ≥ 30%；
   留 15–25% 给提问。
4. **逐页信息设计**：`/app/work/outline.md` + `deck-spec.json`
   - 每页写：角色、结论式标题、≤ 3 个支撑点、需要的图表、讲稿要点、时长；
   - 角色序列建议：`title → motivation → question → approach → method →
     result×N → limitation → conclusion`；
   - 每页只回答一个问题；相邻页之间写一句过渡语。
5. **讲稿**：每页 30–60 秒口语稿，写"为什么这页在这里 + 听众该记住什么"，
   不照读页面文字；标记重音与停顿。
6. **Q&A 准备**：`/app/work/qa-prep.md`
   - 预判 8–12 个问题，分三类：方法细节、结论边界、与文献对比；
   - 每个问题给 30 秒回答 + 支撑材料位置（页号/图号）；
   - 明确"不知道"的标准回答，不编数据。
7. **生成 deck**：
   ```bash
   python3 /skills/weekly-skill/scripts/make_pptx.py \
     --spec /app/work/deck-spec.json --out /app/output/deck.pptx --json
   python3 /skills/weekly-skill/scripts/pptx_edit.py verify /app/output/deck.pptx --json
   ```
8. **时间复核**：按讲稿字数估算语速（中文 ≈ 220 字/分钟，英文 ≈ 130 词/分钟），
   逐页累加与 `timing.json` 比对，偏差 > 15% 必须调整。
9. **落盘**：`outline.md`、`timing.json`、`qa-prep.md`、`deck.pptx`、
   `preview/`、`result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "talk-organisation",
  "status": "completed|partial",
  "talk": {"occasion": "…", "audience": "…", "total_min": 15,
           "speaking_min": 11, "qa_min": 4, "throughline": "…"},
  "artifacts": [
    {"role": "editable", "path": "outline.md", "format": "markdown"},
    {"role": "timing", "path": "timing.json", "format": "json"},
    {"role": "qa", "path": "qa-prep.md", "format": "markdown"},
    {"role": "editable", "path": "deck.pptx", "format": "pptx"},
    {"role": "preview", "path": "preview/slide-01.png", "format": "png"}
  ],
  "slides": [
    {"no": 4, "role": "method", "title": "…", "message": "…",
     "supports": ["…"], "visual": "Fig.2a", "seconds": 45,
     "transition": "由此引出选择性来源的问题"}
  ],
  "timing_check": {"planned_min": 11.0, "estimated_min": 11.4,
                   "deviation": 0.036, "within_tolerance": true},
  "qa": [{"question": "…", "class": "method|scope|literature",
          "answer_seconds": 30, "evidence": "slide 6; Fig.2a"}],
  "checks": {"slides_parse": true, "page_count_match": true,
             "every_content_slide_has_message": true,
             "limitation_slide": true},
  "uncertainty": [],
  "change_log": []
}
```

## 组织原则

1. **先问题后方法**：听众先要知道"为什么难"，才愿意听"怎么做"。
2. **结论先行**：每个结果页标题先给结论，图作证据。
3. **一页一事**：讲两件事就拆两页；页数不够就砍，不合并两个主张。
4. **过渡显式**：每页结尾一句话把听众带到下一页。
5. **局限前置**：不要说"我们方法完美"；主动说边界，答辩时更可信。
6. **时间诚实**：宁可少讲一页，不要超时被截断；把可删页标 `optional`。
7. **备份页**：把细节/补充数据放 `backup` 页，提问时跳转，不讲。

## 异常处理

| 情况 | 处理 |
|---|---|
| 时长极短（≤5 min） | 只保留 问题→方法一句→1 个结果→结论；其余进 backup |
| 受众跨学科 | 术语首次出现给一句解释；方法页降为示意 |
| 必须覆盖的点过多 | 列 `must_cover` 与 `nice_to_have`，按时间取舍并说明 |
| 无讲稿要求 | 仍写要点式 notes（每页 3 条），保证可讲 |
| 提问不可控 | 准备"不知道"模板 + 会后跟进承诺 |
| 模板/投影受限 | 提高对比度、字号 ≥ 18 pt，去掉浅色细线 |

## 自检

- [ ] 受众、时长、场合与 task.md 一致
- [ ] 有且仅有一条 throughline
- [ ] 每页一个主张，标题为结论句
- [ ] 时间预算逐块相加等于总时长，估算偏差 ≤ 15%
- [ ] 有局限页与结论页
- [ ] Q&A ≥ 8 条，每条有证据位置
- [ ] deck 页数与 outline 一致，`verify` 通过

## 快速路径（预算内）

1. **0–2 分钟**：读 task.md，写清听众、场合、时长与唯一 takeaway。
2. **2–7 分钟**：定 3–5 幕主线与每幕信息点，出大纲并落盘。
3. **7–13 分钟**：分配分钟预算，写每页标题、讲稿要点与过渡。
4. **最后 2 分钟**：做时长核算与 Q&A 准备。

## 方法增强（融合参考实践）

- **听众画像**：专业背景、关心什么、会质疑什么；同一内容对专家与跨学科听众的详略不同。
- **唯一 takeaway**：演讲结束希望听众记住的一句话；所有页面服务于它。
- **故事线**：问题 → 缺口 → 方法 → 关键结果 → 意义，幕间有明确过渡句。
- **时间预算**：正文分钟数 = 总时长 − 开场 − 过渡 − Q&A；每页按 1–2 分钟估算，超时先删背景页。
- **每页一个信息点**：标题即结论（结论式标题），正文只放支撑该结论的图/数/短句。
- **讲稿而非全文**：每页 3–5 个口语要点 + 关键数字，不逐字念幻灯片。
- **Q&A 准备**：预判 5–8 个问题（方法、样本、替代解释、应用），各备 2–3 句回答与可指回的页码。

### 定向方法增强

- **来自 PPT Craft Master**：标题即观点，不写“研究背景”“结果（2）”这类章节名；每页只回答一个问题，连续读标题应能复述整条论证。
- **来自 PPT Craft Master**：每页按“事实 → 洞察 → 观点”三层组织；内容页正文不超过约 260 中文字、要点不超过 6 条，超出就拆页而不是缩字号。
- **来自 PPT Craft Master**：留白是版式决策——内容页留白 ≥35%、封面/过渡页 ≥50%；密度页（数据/列表）之后接一页透气页（金句/大图），相邻页不重复同一版式。
- **来自 PPT Craft Master**：每 3–5 页埋 1 句可复述的判断句；出现“随着…的发展”“综上所述”“具有重要意义”等套话即重写。
- **来自 Storytelling**：先把目标锚定成一个听众结果（听懂/相信/决策/记住），而不是“讲完这些内容”；所有取舍服从这个结果。
- **来自 Storytelling**：用因果弧而非主题清单——context→tension→decision→action→result，相邻两幕没有因果桥就补桥或删幕；证据放在怀疑最高点（重大主张之前、大胆承诺之后、转折处）。
- **来自 Storytelling**：草稿与判断分两遍——先放开写素材，再统一删、排、锐化；结尾只给一个明确动作或结论，不用“展望未来”收尾。
- **两篇合取后的改造**：`outline` 增加 `causal_arc[]`、`skepticism_points[]`、`page_rhythm ∈ {dense, breathing}`、`closing_action` 与 `ai_cliche_scan`；`throughline` 必须是听众结果句，不是主题词。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 听众 | 画像与详略匹配 | 调整术语与背景篇幅 |
| G2 | 主线 | 幕数 3–5 且过渡清楚 | 合并或重排 |
| G3 | 时长 | 逐页累计 ≤ 可用时长 | 删减低优先级页 |
| G4 | 单点 | 每页只有一个信息点 | 拆页或删内容 |
| G5 | 标题 | 结论式、可独立读懂 | 改写标题 |
| G6 | Q&A | 至少 5 个预判问题及回答 | 补问题与证据页 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 内容超时 | 页数 × 1.5 > 时长 | 删背景、合并方法页 |
| 标题是标签 | “结果”“方法” | 改成结论式标题 |
| 一页多点 | 标题下有 3 个并列结论 | 拆页 |
| 讲稿念 PPT | 逐字稿等于页面文字 | 改为口语要点 |
| 无过渡 | 幕间跳转生硬 | 补过渡句 |
| Q&A 空白 | 无预判问题 | 补 5–8 个问答 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`audience`、`takeaway`、`outline[]`、`timing`
- [ ] 大纲与 deck 一致
- [ ] 每页有结论式标题与单一信息点
- [ ] 逐页时长累计不超时
- [ ] 每页有讲稿要点
- [ ] Q&A 预判问题 ≥ 5
- [ ] 主线过渡清楚

## 最小示例（示例）

输入："15 分钟组会 + 5 分钟提问，受众为同领域研究生"。
输出：`outline.md` 12 页（含 2 页 backup）；`timing.json` 11+4 分钟；
`timing_check.deviation = 0.04`；`qa-prep.md` 10 问；
`deck.pptx` 12 页；`status: "completed"`。
