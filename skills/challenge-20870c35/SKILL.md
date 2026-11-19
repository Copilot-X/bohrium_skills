---
name: talk-content-organizer
description: 学术汇报内容组织：先定听众结果与唯一 throughline，再设计 3–5 幕、逐页结论式标题、证据映射、分钟级时间预算、讲者备注与 Q&A，生成可编辑 deck 和真实 PDF。Organise an academic talk with audience fit, causal storyline, page-level evidence, timing and Q&A. Use for 演讲结构、报告内容、口头汇报、答辩陈述。
---

# 学术汇报内容组织（Talk Content Organisation）

## 用途与触发

task.md 要求“组织汇报内容”“设计讲述逻辑”“排时间”“准备答辩/组会/会议报告”时使用。
与“论文转 PPT”的区别：本题评分重点是**内容结构与时间控制**，幻灯片只是承载物；
讲稿与 Q&A 准备是一等产物。

固定案例硬约束：受众、时长、页数、模板由 task.md/case.json 指定；题面给了
`slide_budget` 就必须等量交付；每页论断可回到源材料的图/表/章节/数据行。
**不使用 python-pptx。**

## 输入

- `/input/task.md`：场合（组会/答辩/会议/科普）、受众、时长、页数、是否提问、
  语言、模板、场地限制。
- `/input/case.json`：`case_id`、`source_materials[]`、`required_points[]`、`output_contract`。
- 材料：论文/数据/旧 deck/评审意见；图号、表号、章节或数据行号。
- 可选模板 `.pptx`、起始稿。

## 支持文件（按需；失败不阻塞主交付物）

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output/preview
```

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/talk-narrative-playbook.md` | 设计故事线、时间、Q&A 前 | 按小节查阅 |
| `templates/talk-plan.json` | 计划骨架 | 复制后填 talk/budget/must_cover/slides/qa |
| `scripts/check_talk_plan.py` | 页数/秒数/覆盖/Q&A 审计 | 交付前运行；失败先修计划 |
| `scripts/make_pptx.py` | 由 deck-spec 生成可编辑 PPTX | 标准库 OOXML；支持 title/section/content/stat/timeline/comparison/closing |
| `scripts/deck_pdf.py` | 同源 PDF/PNG | 与 deck-spec 一致，适合视觉复核 |
| `scripts/pptx_export.py` | 优先平台渲染 PDF | office-renderer/soffice 优先；否则内容回退并标注 |
| `scripts/pptx_to_preview.py` | 无 office 渲染时，从 PPTX 文本/图片生成内容预览 | 只在没有原生渲染器时使用；结果标 `content-fallback` |
| `scripts/pptx_edit.py` | 套模板/回读/改 notes | `apply-template`/`verify`/`list`/`set-notes` |
| `scripts/validate_result.py` | 结果契约检查 | 可选 |
| `README.md` | 依赖/接口/限制 | 与本文同步 |

## 执行步骤（交付物优先）

### 第 1 步（约 15% 预算）：听众结果与 throughline

写 `/app/work/talk-brief.json`：

```json
{
  "occasion": "组会",
  "audience": "同领域研究生",
  "listener_question": "氧空位到底改变了什么？",
  "desired_outcome": "听众能复述空位—选择性—寿命三者的关系",
  "throughline": "氧空位把选择性的温度窗口向上推，但工业寿命仍是边界",
  "total_min": 15,
  "qa_min": 4,
  "buffer_min": 1,
  "slide_budget": 12,
  "language": "zh",
  "must_cover": ["研究问题", "方法", "关键结果", "局限"],
  "nice_to_have": ["与文献对比"]
}
```

`throughline` 必须是听众结果句，不是主题词。受众跨学科时，术语首次出现给一句解释。

### 第 2 步（约 20% 预算）：3–5 幕与时间预算

写 `/app/work/talk-plan.json`（字段与 `templates/talk-plan.json` 对齐）：

```json
{
  "case_id": "与输入一致",
  "talk": {
    "occasion": "组会", "audience": "同领域研究生",
    "total_min": 15, "qa_min": 4, "buffer_min": 1,
    "throughline": "氧空位把选择性的温度窗口向上推，但工业寿命仍是边界",
    "must_cover": ["研究问题", "方法", "关键结果", "局限"],
    "nice_to_have": ["与文献对比"]
  },
  "sections": [
    {"id": "S1", "name": "问题", "minutes": 2, "slides": [1, 2],
     "purpose": "让听众看到高温选择性塌陷的代价"},
    {"id": "S2", "name": "方法", "minutes": 3, "slides": [3],
     "purpose": "说明如何证明空位与选择性的关系"},
    {"id": "S3", "name": "结果", "minutes": 4, "slides": [4],
     "purpose": "给出主结果及其边界"},
    {"id": "S4", "name": "结论", "minutes": 1, "slides": [5],
     "purpose": "收束到可带走的判断"}
  ],
  "slides": [
    {"no": 1, "section": "S1", "role": "title", "layout": "title",
     "title": "氧空位让 CO2 加氢在 350 °C 保持 92% 选择性",
     "subtitle": "组会 · 15 min", "claim": "", "covers": [],
     "evidence": [], "visual": "", "seconds": 30,
     "notes": "开场只给结论与边界，不先讲目录", "transition": "先看问题…"},
    {"no": 2, "section": "S1", "role": "question", "layout": "section",
     "number": "01", "title": "为什么高温下选择性会塌陷？",
     "claim": "高温下选择性从 92% 降到 71%，是本文要解决的缺口",
     "covers": ["研究问题"],
     "evidence": [{"source": "materials/paper.pdf", "locator": "Fig.1b",
                   "strength": "moderate"}],
     "visual": "Fig.1b", "seconds": 45,
     "notes": "…", "transition": "…", "page_rhythm": "breathing"},
    {"no": 3, "section": "S2", "role": "method", "layout": "content",
     "title": "空位浓度梯度 + 原位表征把相关变成可检验机制",
     "claim": "三档空位浓度与 XPS/STEM 对照可区分表面效应与体相效应",
     "covers": ["方法"],
     "evidence": [{"source": "materials/paper.pdf", "locator": "Methods §2.2",
                   "strength": "strong"}],
     "visual": "Methods 图", "seconds": 60,
     "notes": "…", "transition": "…", "page_rhythm": "dense"},
    {"no": 4, "section": "S3", "role": "result", "layout": "content",
     "title": "氧空位浓度与选择性呈单调正相关",
     "claim": "空位浓度每增加 0.1 ML，选择性 +6.8 个百分点",
     "covers": ["关键结果"],
     "evidence": [{"source": "materials/paper.pdf", "locator": "Fig.2a",
                   "strength": "strong"}],
     "visual": "Fig.2a", "seconds": 60,
     "notes": "先指图，再给数字，最后说边界", "transition": "…",
     "page_rhythm": "dense"},
    {"no": 5, "section": "S4", "role": "conclusion", "layout": "closing",
     "title": "氧空位是选择性的可调旋钮，但工业寿命仍需验证",
     "claim": "实验室条件下 92% 选择性成立，长周期与杂质耐受尚未证明",
     "covers": ["局限"],
     "evidence": [{"source": "materials/paper.pdf", "locator": "Discussion §4.2",
                   "strength": "moderate"}],
     "visual": "", "seconds": 30,
     "notes": "…", "transition": "进入提问", "page_rhythm": "breathing"}
  ],
  "qa": [
    {"question": "空位浓度怎么测？", "class": "method",
     "answer_seconds": 30, "evidence": "Methods §2.2", "backup_slide": 6}
  ],
  "tradeoffs": []
}
```

时间规则：

```
讲述时间 = total_min − qa_min − buffer_min
逐页 seconds 之和 ≈ 讲述时间 × 60（偏差 ≤ 15%）
```

1 页 ≈ 45–75 秒；方法 ≤30%，结果 ≥30%，Q&A 15–25%。超时先删背景复述与方法
细节，不删研究问题、主结果、局限、结论。可删内容进 backup 或 `tradeoffs[]`。

### 第 3 步（约 20% 预算）：逐页信息与证据

每页填：

- `role`：title/motivation/question/approach/method/result/discussion/
  limitation/conclusion/summary/transition/acknowledgment；
- `title`：结论句（条件 + 判断 + 边界），禁止“结果/方法/背景”；
- `claim`：该页唯一主张；`covers`：对应 `must_cover`；
- `evidence[]`：每条 `{source, locator, strength}`，精确到 Fig./Table/§/页码/数据列；
- `visual`：结果页必须有图/表；`seconds`；`notes`；`transition`；
- `page_rhythm`：dense/breathing；连续 dense 不超过 3 页。

每页结尾写过渡；连续读所有标题应能复述整条论证。

### 第 4 步（约 15% 预算）：Q&A 与取舍

15 分钟报告预判 8–12 问，5 分钟快报至少 5 问。每问写
`class`（method/data/scope/literature/application/hostile）、30 秒回答、证据位置、
可选 `backup_slide`。不知道时明确说不知道并承诺跟进，不编数据。

`must_cover` 必须被 `covers` 覆盖；删掉的内容写 `tradeoffs[]` 与原因。

### 第 5 步（约 20% 预算）：计划校验 → 生成 deck → 真实预览

```bash
python3 "$SKILL_DIR/scripts/check_talk_plan.py" \
  --plan /app/work/talk-plan.json --out /app/work/talk-plan-check.json --json
```

要求 `errors=0`；交付前再跑 `--strict`，把 placeholder、缺 transition 等警告清零。
失败先修计划，不改校验器。

把 plan 转成 `/app/work/deck-spec.json`（每页 `layout/title/bullets/image/notes`），
生成：

```bash
python3 "$SKILL_DIR/scripts/make_pptx.py" \
  --spec /app/work/deck-spec.json --out /app/output/presentation.pptx
python3 "$SKILL_DIR/scripts/make_pptx.py" --check /app/output/presentation.pptx
```

有模板时用真实版式：

```bash
python3 "$SKILL_DIR/scripts/pptx_edit.py" apply-template /app/output/presentation.pptx \
  --template /input/materials/template.pptx --out /app/output/presentation.pptx --json
python3 "$SKILL_DIR/scripts/pptx_edit.py" verify /app/output/presentation.pptx --json
```

预览：

```bash
python3 "$SKILL_DIR/scripts/pptx_export.py" \
  --pptx /app/output/presentation.pptx \
  --out /app/output/preview/presentation.pdf \
  --png-dir /app/output/preview --json
```

无 office 时用 `deck_pdf.py --spec /app/work/deck-spec.json` 生成同源 PDF；
`render.native` 如实写。

### 第 6 步（约 10% 预算）：落盘与交叉校验

写 `outline.md`、`timing.json`、`qa-prep.md`、`result.json`；result 的页号、标题、
秒数、claim 与 plan 必须一致；`timing_check` 由逐页 seconds 重算。最后跑带
`--result` 的 `check_talk_plan.py` 和 `validate_result.py`。

## 输出契约

```json
{
  "case_id": "与输入一致",
  "task_family": "talk-organisation",
  "status": "answered|partial|insufficient_evidence|input_error",
  "task_status": "completed|partial|input_error",
  "talk": {"occasion": "组会", "audience": "…", "total_min": 15,
           "speaking_min": 10, "qa_min": 4, "throughline": "…"},
  "plan": {"path": "work/talk-plan.json", "sections": 4, "slides": 12},
  "artifacts": [
    {"role": "plan", "path": "work/talk-plan.json", "format": "json"},
    {"role": "outline", "path": "outline.md", "format": "markdown"},
    {"role": "timing", "path": "timing.json", "format": "json"},
    {"role": "qa", "path": "qa-prep.md", "format": "markdown"},
    {"role": "editable", "path": "presentation.pptx", "format": "pptx"},
    {"role": "render", "path": "preview/presentation.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/slide-01.png", "format": "png"}
  ],
  "slides": [
    {"no": 7, "section": "S3", "role": "result",
     "title": "…", "claim": "…",
     "evidence": [{"source": "materials/paper.pdf", "locator": "Fig.3a",
                   "strength": "strong"}],
     "visual": "Fig.3a", "seconds": 60, "notes": "…", "transition": "…"}
  ],
  "timing_check": {"planned_min": 10.0, "estimated_min": 10.0,
                   "deviation": 0.0, "within_tolerance": true},
  "qa": [{"question": "…", "class": "method", "answer_seconds": 30,
          "evidence": "slide 6; Fig.2a", "backup_slide": 13}],
  "render": {"engine": "office-renderer|soffice|matplotlib-from-spec|none",
             "native": true, "pdf": "preview/presentation.pdf",
             "png": ["preview/slide-01.png"], "unavailable": false},
  "checks": {"slides_parse": true, "page_count_match": true,
             "every_content_slide_has_message": true,
             "limitation_slide": true, "timing_within_tolerance": true,
             "visual_reviewed": true},
  "editable_source": {"path": "presentation.pptx", "format": "pptx"},
  "export_artifact": {"path": "preview/presentation.pdf", "format": "pdf"},
  "content_map": [
    {"slide": 7, "claim": "…", "maps_to": {"path": "materials/paper.pdf",
                                           "locator": "Fig.3a"}}
  ],
  "compliance_checks": {"pptx_valid": true, "timing_within_tolerance": true,
                        "qa_min_count": true, "export_signature": "pdf"},
  "exceptions": [],
  "uncertainty": [],
  "change_log": []
}
```

规则：

- `talk-plan.json` 是单一事实源；`slides[]` 的页号/标题/claim/evidence/notes/seconds
  与 result 一致；
- `timing_check` 由逐页 seconds 重算，不能手写一个“看起来对”的数；
- `status=answered` 时必须有可编辑 deck；没有真实预览就标 `partial`；
- `must_cover` 全覆盖，取舍有 `tradeoffs[]`；局限页不能藏进 backup。

## 异常处理

| 情况 | 处理 |
|---|---|
| 时长极短（≤5 min） | 问题 → 方法一句 → 1–2 结果 → 结论；其余进 backup |
| 受众跨学科 | 术语首次解释；方法降为示意；结论写边界 |
| must_cover 过多 | 按时间取舍，写 `tradeoffs[]` |
| 页数小于内容量 | 严格按 `slide_budget`；合并方法细节 |
| 无讲稿要求 | 仍写要点式 notes（每页 3 条） |
| 问题不可控 | 准备“不知道”模板 + 会后跟进；hostile 先承认差异 |
| 模板/投影受限 | 提高对比度、字号 ≥18 pt；套模板后 verify |
| 渲染不可用 | 交付 PPTX + HTML/同源 PDF；`render.native=false`，不伪造 |

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 听众 | 画像、desired_outcome、术语详略匹配 |
| G2 | 主线 | 3–5 幕，每幕有 purpose；throughline 是结果句 |
| G3 | 时长 | 逐页 seconds 累计 = 讲述时间 ±15% |
| G4 | 单点 | 每页一个 claim；标题是结论句 |
| G5 | 证据 | 每页 claim 有 source+locator；结果页有图/表 |
| G6 | 覆盖 | must_cover 全覆盖；取舍有记录 |
| G7 | Q&A | ≥5（15 min ≥8），有证据位置/backup |
| G8 | 交付 | deck 页数一致、notes 存在；PDF/PNG 真实 |

## 自检

- [ ] 受众、时长、页数与 task.md 一致
- [ ] throughline 是听众结果句；3–5 幕有因果桥
- [ ] 逐页秒数相加等于讲述时间（±15%）
- [ ] 每页标题结论句、一个 claim、证据 + locator
- [ ] must_cover 全覆盖；局限页在正文
- [ ] Q&A ≥5（15 min ≥8），有证据/backup
- [ ] `check_talk_plan.py` 0 errors；deck 页数与 plan 一致
- [ ] PDF/PNG 真实；`render.native` 与引擎一致
- [ ] 真的打开预览做过视觉复核

## 最小示例（示例，不是运行记录）

输入：“15 分钟组会 + 4 分钟提问 + 1 分钟缓冲，同领域研究生，12 页”。
计划：4 幕（问题 2 / 方法 3 / 结果 4 / 边界 1），逐页 seconds 合计 600 s；
Q&A 8 条；must_cover 4 项全覆盖；结果页均带 Fig./Table locator。
输出：`presentation.pptx` 12 页、`preview/presentation.pdf` 12 页、`timing_check`
偏差 ≤15%、`status="answered"`。示例不是正式答案。

## 版本与限制

- 版本：0.8.0（听众结果 + 页级 claim + 时间/Q&A + 真实预览）。
- 不使用 python-pptx；PPTX 由标准库 OOXML 生成；PDF 优先平台渲染器。
- 没有 office 渲染时，同源 PDF 是内容/版式预览，不冒充原生渲染。
- 平台可能把技能装到题目专属目录，必须先解析 `$SKILL_DIR`。
