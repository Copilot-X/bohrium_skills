---
name: paper-to-deck-builder
description: 论文转学术 PPT：先把论文拆成页级 claim 台账，再按听众/时长/页数组织结论式标题、证据、图表引用、讲者备注与时间预算，生成可编辑 PPTX 和真实 PDF 预览。Turn a paper into an academic deck with a claim ledger, action titles, page-level evidence and a real PDF preview. Use for 论文转 PPT、组会汇报、会议报告、答辩幻灯片。
---

# 论文转学术 PPT（Paper → Academic Deck）

## 用途与触发

task.md 要求“把论文做成汇报 PPT”“组会/答辩 slides”“按听众和时长出 deck”时使用。
组织方口径：**受众、时长、页数由题面固定；每页要点可回到论文；技术性文件检查
不等于演示的科学性与质量。**

交付物：**可编辑 `presentation.pptx` + 真实 PDF 预览 + 页级 claim/证据映射 +
逐页讲者备注 + 时间预算。** 不使用 python-pptx；PPTX 由标准库 OOXML 生成。

## 输入

- `/input/task.md`：受众、时长、页数/上限、语言、必须覆盖内容、比例、模板。
- `/input/case.json`：`case_id`、`paper`/`source_materials[]`、`template_id`、`output_contract`。
- 材料：论文全文/摘要/图表、补充材料；只读实际文件，不凭印象取数。
- 可选：模板 `.pptx`、起始稿。

## 支持文件（按需；失败不阻塞主交付物）

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output/preview
```

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/paper-to-deck-playbook.md` | 定预算、建 claim 台账、写故事线前 | 按小节查阅 |
| `templates/deck-plan.json` | 页级计划骨架 | 复制后填 budget/claims/outline/slides |
| `scripts/make_pptx.py` | 由 deck-spec 生成可编辑 PPTX | 标准库 OOXML；支持 title/section/content/stat/timeline/comparison/closing 等版式 |
| `scripts/deck_pdf.py` | 由同一 spec 生成真实 PDF/PNG 预览 | 版式与 PPTX 同源，适合视觉复核 |
| `scripts/pptx_export.py` | 优先用平台渲染器把 PPTX 转 PDF | office-renderer/soffice 优先；否则内容回退并标注 |
| `scripts/pptx_to_preview.py` | 无 office 渲染时，从 PPTX 文本/图片生成内容预览 | 只在没有原生渲染器时使用；结果标 `content-fallback` |
| `scripts/adapt_deck.py` | 有模板时把内容填入真实版式 | 见第 4 步 |
| `scripts/check_paper_deck.py` | 可选页数/标题/notes/证据审计 | 失败写 `audit_failed`，不阻塞 |
| `README.md` | 依赖/接口/限制 | 与本文同步 |

## 执行步骤（交付物优先）

### 第 1 步（约 15% 预算）：读论文，建 typed digest

写 `/app/work/paper-digest.json`：

```json
{
  "title": "…",
  "research_question": "…",
  "approach": "…",
  "headline_result": "A keeps 92% selectivity at 350 °C",
  "key_numbers": [
    {"value": "92", "unit": "%", "condition": "350 °C, 1 bar",
     "source": {"path": "materials/paper.pdf", "locator": "Fig.2a"}}
  ],
  "figures": [
    {"id": "Fig.2a", "caption": "…", "path": "materials/fig2a.png",
     "source": {"path": "materials/paper.pdf", "locator": "Fig.2a"}}
  ],
  "limitations": ["仅实验室规模", "未做长周期毒性测试"],
  "uncertainties": []
}
```

只记录论文实际写了的内容；图/表/数字必须带 locator。没有的数字不补，
缺失资产写 `unavailable`，不要用示意图冒充实验数据。

### 第 2 步（约 15% 预算）：定预算、故事线与页级 claim

写 `/app/work/deck-plan.json`：

```json
{
  "audience": "领域同行",
  "duration_min": 15,
  "qa_min": 4,
  "slide_budget": 13,
  "throughline": "氧空位把选择性的温度窗口向上推，但工业寿命仍是边界",
  "must_cover": ["问题", "方法", "关键结果", "局限"],
  "sections": [
    {"id": "S1", "title": "问题", "minutes": 2},
    {"id": "S2", "title": "方法", "minutes": 3},
    {"id": "S3", "title": "结果", "minutes": 5},
    {"id": "S4", "title": "边界与结论", "minutes": 2}
  ],
  "slides": [
    {"no": 1, "role": "title", "layout": "title",
     "title": "氧空位让 CO2 加氢在 350 °C 保持 92% 选择性",
     "subtitle": "组会汇报 · 15 min", "seconds": 30, "notes": "…"},
    {"no": 2, "role": "question", "layout": "section", "number": "01",
     "title": "为什么高温下选择性会塌陷？", "seconds": 40, "notes": "…"},
    {"no": 3, "role": "result", "layout": "content",
     "title": "氧空位浓度与选择性呈单调正相关",
     "claim": "空位浓度每增加 0.1 ML，选择性 +6.8 个百分点",
     "bullets": ["350 °C 下由 71% 提升到 92%", "对照样品仅 74%"],
     "evidence": [{"source": "materials/paper.pdf", "locator": "Fig.2a"}],
     "visual": "Fig.2a", "seconds": 60, "notes": "…",
     "transition": "接下来看机制证据…"}
  ]
}
```

规则：

- 15 分钟 ≈ 12–15 页；页数服从 task.md；超限先砍方法细节，不砍结果与局限；
- 每页一个 action title（条件 + 判断 + 边界）；标题连读能复述论证；
- 每页一个 `claim`，`evidence` 精确到 Fig./Table/§/页码；
- 结果页 ≥1/3，方法页 ≤1/4；结果页必须有图/表或明确数据；
- 每页 `notes` 写“为什么这页在这里 + 怎么讲 + 过渡”，不照读页面；
- `seconds` 之和 ≈（duration − qa）×60，偏差 ≤15%。

### 第 3 步（约 20% 预算）：先出可编辑 PPTX

把 plan 转成 `/app/work/deck-spec.json`，使用 `make_pptx.py` 支持的版式：

- `title`、`section`、`content`、`stat`、`timeline`、`comparison`、
  `cards`、`quote`、`closing`；
- 主题用 `{"name": "scholar"}` 或从模板审计得到；不要默认蓝色模板；
- 每页有 `title`、`notes`、`source`；内容页有 `bullets`/`claim`/`image`；
- 每页至少一个视觉元素（图、统计卡、流程、对照卡），不要纯文字墙。

```bash
python3 "$SKILL_DIR/scripts/make_pptx.py" \
  --spec /app/work/deck-spec.json --out /app/output/presentation.pptx
python3 "$SKILL_DIR/scripts/make_pptx.py" --check /app/output/presentation.pptx
```

生成后立即回读：页数、页序、标题、notes、图片关系、占位符残留。

### 第 4 步（约 15% 预算）：模板/起始稿（若 task 提供）

- 有模板：先审计真实版式，再用 `adapt_deck.py` 把内容填入占位符：

```bash
python3 "$SKILL_DIR/scripts/audit_ppt_template.py" \
  --template /input/materials/template.pptx --out /app/work/template-audit.json --json
cp "$SKILL_DIR/templates/ppt-format-spec.json" /app/work/ppt-format-spec.json
# 按 template-audit.json 的真实版式名修改 roles
python3 "$SKILL_DIR/scripts/adapt_deck.py" \
  --template /input/materials/template.pptx --spec /app/work/deck-spec.json \
  --format-spec /app/work/ppt-format-spec.json \
  --out /app/output/presentation.pptx --report /app/work/adapt-report.json --json
```

- 有起始稿：先复制基线，只改工单点名内容；不得删除/重排未点名页面。
- 没有模板：用默认 `scholar` 主题；不要硬套一个与内容无关的模板。
- 模板字体缺失时做同族替代并记录 `font_substitution`；不静默换字体。

### 第 5 步（约 20% 预算）：真实 PDF/PNG 预览与视觉 QA

优先平台原生渲染：

```bash
python3 "$SKILL_DIR/scripts/pptx_export.py" \
  --pptx /app/output/presentation.pptx \
  --out /app/output/preview/presentation.pdf \
  --png-dir /app/output/preview --json
```

若平台没有 office-renderer/soffice，用同一 deck-spec 生成同源 PDF：

```bash
python3 "$SKILL_DIR/scripts/deck_pdf.py" \
  --spec /app/work/deck-spec.json \
  --out /app/output/preview/presentation.pdf \
  --png-dir /app/output/preview --json
```

`pptx_export.py` 的 `native` 必须如实写入 `render.native`；`content-fallback`
不能写成“原生渲染”。

**强制视觉复核**：打开 PDF/PNG，逐页检查：

1. 标题是否被裁切/换行过多；正文是否溢出或压住图；
2. 图/表是否与标题结论对应，图注/编号是否正确；
3. 每页 notes 是否存在，且不是页面文字复述；
4. 模板页是否保留母版/版式/字体/配色；
5. 页数、页序与 plan 一致；无 “Click to edit / Lorem / TODO”。

发现问题先改 spec/模板，再重渲染；最多 2 轮。

### 第 6 步（约 15% 预算）：写 result.json

把 plan 的页级映射同步到 result；不要另写一份与 deck 矛盾的 JSON。

## 输出契约

```json
{
  "case_id": "与输入一致",
  "task_family": "paper-to-deck",
  "status": "answered|partial|insufficient_evidence|input_error",
  "task_status": "completed|partial|input_error",
  "deck": {"path": "presentation.pptx", "slide_count": 13,
           "aspect_ratio": "16:9", "template": null,
           "template_fidelity": "none|master+layouts+theme|theme-only"},
  "artifacts": [
    {"role": "editable", "path": "presentation.pptx", "format": "pptx"},
    {"role": "plan", "path": "work/deck-plan.json", "format": "json"},
    {"role": "spec", "path": "work/deck-spec.json", "format": "json"},
    {"role": "render", "path": "preview/presentation.pdf", "format": "pdf"},
    {"role": "preview", "path": "preview/slide-01.png", "format": "png"}
  ],
  "throughline": "…",
  "page_evidence": [
    {"slide": 3, "role": "result",
     "title": "氧空位浓度与选择性呈单调正相关",
     "claim": "空位浓度每增加 0.1 ML，选择性 +6.8 个百分点",
     "evidence": [{"path": "materials/paper.pdf", "locator": "Fig.2a"}],
     "visual": "Fig.2a", "notes_present": true}
  ],
  "slides": [
    {"no": 3, "role": "result",
     "title": "氧空位浓度与选择性呈单调正相关",
     "claims": [{"claim_id": "C3", "source": {"path": "materials/paper.pdf",
                                              "locator": "Fig.2a"}}],
     "notes_seconds": 60}
  ],
  "source_coverage": {"claims_total": 1, "claims_mapped": 1,
                      "unmapped": []},
  "timing": {"planned_min": 10.0, "slide_seconds": 600,
             "within_tolerance": true},
  "render": {"engine": "office-renderer|soffice|matplotlib-from-spec|none",
             "native": true, "pdf": "preview/presentation.pdf",
             "png": ["preview/slide-01.png"], "unavailable": false},
  "checks": {"zip_valid": true, "relationships_resolved": true,
             "slide_count_match": true, "every_slide_has_title": true,
             "notes_complete": true, "page_evidence_complete": true,
             "no_placeholder_junk": true, "text_overflow_risk": [],
             "visual_reviewed": true},
  "editable_source": {"path": "presentation.pptx", "format": "pptx"},
  "export_artifact": {"path": "preview/presentation.pdf", "format": "pdf"},
  "content_map": [
    {"slide": 3, "claim": "…", "maps_to": {"path": "materials/paper.pdf",
                                           "locator": "Fig.2a"}}
  ],
  "compliance_checks": {"pptx_valid": true, "slide_count_match": true,
                        "notes_complete": true, "export_signature": "pdf",
                        "template_fidelity": "none"},
  "exceptions": [],
  "uncertainty": [],
  "change_log": []
}
```

规则：

- `page_evidence` 覆盖每一页；`slides`/`source_coverage` 是给页级检查器的兼容别名，
  内容必须与 `page_evidence` 一致；不能只有标题没有 claim/evidence；
- 数字、单位、条件与论文逐字一致；图/表编号对得上；
- 无法提取的图表写 `unavailable`，不要编造；
- `render.native=false` 时必须写 `engine="matplotlib-from-spec"` 或
  `content-fallback`，不能冒充原生渲染；
- 没有真实 PDF/PNG 时 `status=partial`、`export_artifact=null`。

## 异常处理

| 情况 | 处理 |
|---|---|
| 论文缺图/图是位图 | 用真实图并标 `unavailable` 或重绘示意图，不冒充数据 |
| 公式为图片 | 保留原图并标 `formula_image=true`，不伪造公式 |
| PDF 加密/读不了 | `input_error` 或 `partial`，写清缺什么 |
| 模板占位符不足 | 用最接近版式 + `layout_fallback`；不新造版式 |
| 页数超限 | 合并方法细节，保留结果与局限，写 `tradeoffs` |
| 字体缺失 | 同族替代并记录 `font_substitution` |
| 渲染不可用 | 交付 PPTX + `render.unavailable=true`，不伪造 PDF |

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 预算 | 页数、时长、受众与 task.md 一致；逐页秒数误差 ≤15% |
| G2 | 主线 | 每页 action title；连读能复述论证 |
| G3 | 证据 | 每页 claim 有 source+locator；数字/单位/图号一致 |
| G4 | 内容 | 结果页 ≥1/3；方法页 ≤1/4；有局限页 |
| G5 | 视觉 | 无溢出/重叠/裁切；每页有视觉元素；无模板残留 |
| G6 | 备注 | 每页 notes 非空、非页面复述、含过渡 |
| G7 | 模板 | 有模板时母版/版式/主题真实继承；fallback 已记录 |
| G8 | 交付 | PPTX 可打开、页数正确；PDF/PNG 真实有效 |

## 自检

- [ ] 论文 digest 记录了问题、方法、主结果、关键数字、图/表、局限
- [ ] 页数/时长/受众与 task.md 一致；逐页秒数误差 ≤15%
- [ ] 每页标题是结论句；每页只有一个 claim
- [ ] 每个内容页有 evidence + locator；结果页有图/表
- [ ] 每页 notes 非空，含“为什么/怎么讲/过渡”
- [ ] PPTX 可编辑、页数正确、图片关系可解析
- [ ] 有模板时真实使用母版/版式/主题；无模板时不伪造
- [ ] PDF/PNG 真实存在；`render.native` 与引擎一致
- [ ] 真的打开预览做过视觉复核

## 最小示例（示例）

输入：论文 + 15 min + 13 页 + 模板。输出：`presentation.pptx` 13 页，
`page_evidence` 13 条，`timing` 600 s ±15%，`preview/presentation.pdf` 13 页；
`render.native=true`（平台渲染）或 `false` + `matplotlib-from-spec`（无 office）。
示例不是正式答案。

## 版本与限制

- 版本：0.8.0（页级 claim 台账 + 设计版式 + 真实 PDF + 强制视觉复核）。
- 不使用 python-pptx；PPTX 由标准库 OOXML 生成；PDF 优先平台渲染器。
- 没有 office-renderer/soffice 时，同源 PDF 是内容/版式预览，不冒充原生渲染。
- 平台可能把技能装到题目专属目录，必须先解析 `$SKILL_DIR`。
