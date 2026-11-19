# 工作台账与 result.json 字段说明

本文件对应 `scripts/workorder.py`、`scripts/evidence.py`、
`scripts/citation_map.py`、`scripts/numbers_check.py`、`scripts/docx_out.py`
和 `scripts/result_writing.py` 的落盘格式。所有 JSON 均为 UTF-8、可解析、
不依赖第三方库。

## 1. workorder.json

```json
{
  "case_id": "输入原样保留",
  "source_title": "材料标题",
  "genre": "popular_article|project_application|impact_statement|tech_brief|investment_memo|generic_rewrite",
  "genre_confidence": 0.85,
  "genre_evidence": ["task.md 中的判定依据"],
  "audience": {
    "role": "public|reviewers|industry|investors|policymakers|students|peers|unknown",
    "background": "none|cross_domain|same_domain|unknown",
    "reading_goal": "understand_significance|evaluate_feasibility|judge_value|understand_method|unknown",
    "evidence": ["原文依据"],
    "source": "task_explicit|genre_default"
  },
  "language": "zh|en",
  "length_limit": {"raw": "800 字", "kind": "characters|words|pages|none", "value": 800},
  "deliverable_name": "article.md",
  "required_sections": [{"heading": "为什么值得关注", "source": "genre_default|task_explicit"}],
  "must_keep": ["关键数字与单位", "实验/样本条件"],
  "can_compress": ["背景综述", "方法细节"],
  "must_not_add": ["材料没有的市场规模/竞品参数"],
  "task_requirements": ["task.md 中的可执行要求"],
  "output_contract": {"deliverable": "可编辑的目标文稿/结构化产物"},
  "contract_fields": [{"field": "deliverable", "requirement": "...", "source": "output_contract|official_default"}],
  "constraints": {"no_network": true, "editable_output": true, "source_files": ["materials/paper.md"]},
  "uncertainty": [{"item": "genre_ambiguous", "detail": "...", "action": "..."}],
  "generated_at": "ISO-8601"
}
```

规则：

- `case_id` 原样保留；缺失时写 `unknown-case` 并记录 uncertainty。
- `deliverable_name` 决定主稿文件名；task.md 明确文件名时以 task.md 为准。
- `contract_fields` 至少覆盖 `case_id`、`deliverable`、`preserved_facts`、
  `evidence_map`、`change_log`；额外契约字段逐条列出。

## 2. evidence.json

```json
{
  "status": "ok|empty",
  "case_id": "输入原样保留",
  "sources": [{"path": "materials/paper.md", "sha256": "...", "chars": 1234,
               "locator_style": "line|page|paragraph"}],
  "evidence": [
    {
      "evidence_id": "E001",
      "claim_id": "C001",
      "statement": "在 350 °C、1 bar、实验室规模下转化率为 92%",
      "quote": "源文原句",
      "kind": "quantitative|observation|inference|limitation|speculation|definition|author_claim|restricted_fact|citation",
      "tier": "fact|inference|speculation",
      "quantities": [{"raw": "92%", "value": "92", "unit": "%",
                      "normalized_unit": "%", "comparison": null}],
      "value": "92",
      "unit": "%",
      "conditions": ["350 °C", "1 bar", "实验室规模"],
      "comparison": "相比参比催化剂 72%",
      "denominator": "n=3",
      "maturity": "lab|bench|pilot|prototype|field|clinical|commercial|unknown",
      "source": {"path": "materials/paper.md", "locator": "L12-L14", "quote": "..."},
      "confidence": "high|medium|low",
      "disclosable": false,
      "restricted_reason": "材料标注保密",
      "assumptions": ["外推到放大场景的假设"],
      "to_be_validated": "需要中试数据",
      "score": 7
    }
  ],
  "key_facts": ["E001"],
  "claims": ["C001"],
  "terminology": [{"term": "选择性", "abbreviation": "SEL", "definition": "目标产物占比",
                   "source": "explicit_expansion|definition"}],
  "citations": [{"kind": "doi|bracket|author_year|figure|table|reference_entry",
                 "raw": "10.1000/example"}],
  "restricted": [{"evidence_id": "E009", "statement": "...", "reason": "...",
                  "source": {"path": "...", "locator": "..."}}],
  "metrics": {"转化率": ["E001"]},
  "uncertainty": [{"item": "possible_data_conflict", "metric": "转化率",
                   "unit": "%", "values": ["88", "92"], "action": "回源核对条件"}],
  "stats": {"evidence": 20, "quantitative": 8, "claims": 15,
            "restricted": 1, "citations": 6},
  "generated_at": "ISO-8601"
}
```

规则：

- 每条 `fact` 必须有 `source.path` 与 `source.locator`（行号/页/段落）。
- `speculation` 必须有 `assumptions` 与 `to_be_validated`。
- `disclosable=false` 的句子只能留在内部台账；`result.json` 的 `restricted`
  只保留 id、原因和来源定位，不复制敏感正文。
- 数字用字符串保存，避免浮点误差；`normalized_unit` 只做别名归一，不做换算。

## 3. citation-map.json

```json
{
  "status": "ok|no_mapped_claims",
  "case_id": "...",
  "draft": {"sections": 5, "paragraphs": 8, "chars": 900},
  "claim_map": [
    {"claim_id": "C001", "claim": "...", "kind": "quantitative", "tier": "fact",
     "source": {"path": "...", "locator": "L12-L14"},
     "quote": "源文原句", "status": "mapped|not_in_draft|source_missing",
     "draft_locators": ["§3 ¶2"], "match_score": 11,
     "evidence_id": "E001", "conditions": ["350 °C"], "speculation": false}
  ],
  "evidence_map": [
    {"claim_id": "C001", "claim": "...", "source": {"path": "...", "locator": "..."},
     "quote": "...", "draft_locator": "§3 ¶2", "section": "看到了什么",
     "evidence_id": "E001", "tier": "fact", "status": "mapped"}
  ],
  "unmapped_key_claims": [
    {"claim_id": "C004", "claim": "...", "reason": "not_in_draft", "source": {...}}
  ],
  "draft_claims_without_source": [
    {"draft_locator": "§2 ¶1", "section": "...", "excerpt": "...",
     "unsupported_numbers": ["99|%"]}
  ],
  "stats": {"claims": 15, "mapped": 9, "not_in_draft": 6, "unsourced_paragraphs": 0},
  "generated_at": "ISO-8601"
}
```

规则：

- `mapped` 要求：draft 中出现该论断的数字或足够关键词，且 evidence 有 path+locator。
- `draft_claims_without_source` 是主要幻觉风险；出现后要回源或删除。
- 映射失败不等于正文错误，但必须在 `result.json.checks` 或 `uncertainty` 中体现。

## 4. numbers-check.json

```json
{
  "ok": true,
  "errors": [{"code": "number_not_in_source|unit_mismatch|restricted_leak|placeholder_text",
              "token": "99%", "detail": "..."}],
  "warnings": [{"code": "missing_key_facts|condition_not_nearby|over_length|under_length|unverifiable_hype",
                "detail": "..."}],
  "draft": {"chars": 900, "words": 520, "paragraphs": 8, "sha256": "..."},
  "coverage": {"key_facts": 10, "source_number_keys": 30,
               "preserved_number_keys": 14, "missing": []},
  "length": {"limit": {"kind": "characters", "value": 800},
             "measured": 900, "status": "checked"},
  "generated_at": "ISO-8601"
}
```

硬错误（`ok=false`）：

- 主稿出现材料中没有的“数字+单位”。
- 数值存在但单位不同（计数单位 `n/次/例/人` 的转述除外）。
- 保密语句进入正文。
- 占位符（TODO/TBD/待补充等）。

警告不阻塞交付：缺失关键事实、条件不在附近、篇幅偏差、无法核实的夸大措辞、
图表编号不在源文、年份未核验。

## 5. result.json（官方字段）

```json
{
  "case_id": "输入原样保留",
  "status": "completed|partial",
  "genre": "popular_article",
  "language": "zh",
  "title": "成稿标题（取自主稿 H1，缺失时用材料标题）",
  "source_title": "材料标题",
  "audience_profile": {
    "genre": "popular_article",
    "audience": {"role": "public", "background": "none",
                 "reading_goal": "understand_significance"},
    "length_limit": {"kind": "characters", "value": 800},
    "must_keep": ["..."], "can_compress": ["..."], "must_not_add": ["..."]
  },
  "deliverable": {
    "path": "article.md",
    "format": "markdown",
    "genre": "popular_article",
    "title": "...",
    "word_count": 520,
    "char_count": 900,
    "sections": ["为什么值得关注", "看到了什么"],
    "sha256": "...",
    "docx": "article.docx",
    "writing_mode": "agent_draft|missing_draft"
  },
  "preserved_facts": [
    {"fact_id": "E001", "text": "...", "value": "92", "unit": "%",
     "values": [{"value": "350", "unit": "°C", "raw": "350 °C"},
                {"value": "92", "unit": "%", "raw": "92%"}],
     "condition": "350 °C；1 bar", "conditions": ["350 °C", "1 bar"],
     "comparison": "相比 72%", "denominator": "n=3", "maturity": "lab",
     "tier": "fact", "source": {"path": "...", "locator": "L12-L14"},
     "quote": "...", "in_draft": true}
  ],
  "evidence_map": [
    {"claim_id": "C001", "claim": "...", "source": {"path": "...", "locator": "..."},
     "quote": "...", "draft_locator": "§3 ¶2", "section": "...",
     "evidence_id": "E001", "tier": "fact", "status": "mapped"}
  ],
  "change_log": [
    {"section": "看到了什么", "action": "keep|compress|rewrite|add|delete",
     "before": "源文原句", "after": "成稿句子", "reason": "...",
     "evidence_ids": ["E001"], "status": "applied"}
  ],
  "content_decisions": [
    {"item": "关键数字与条件", "decision": "keep", "reason": "...", "where": "..."}
  ],
  "readability": {"term_expansions": [{"term": "选择性", "expansion": "..."}],
                  "jargon": ["SEL"], "avg_sentence_chars": 24.5},
  "output_contract": {"原样": "复制 case.json 的 output_contract"},
  "contract_coverage": [
    {"field": "deliverable", "requirement": "...", "addressed_by": "article.md",
     "status": "satisfied|acknowledged|empty|missing"}
  ],
  "sources": [{"path": "materials/paper.md", "sha256": "...", "readable": true,
               "chars": 1234, "note": ""}],
  "restricted": [{"evidence_id": "E009", "reason": "...",
                  "source": {"path": "...", "locator": "..."}}],
  "artifacts": [
    {"role": "editable|office|evidence|evidence_map|check|workorder|inventory|preview",
     "path": "article.md", "format": "markdown|docx|json|pdf",
     "bytes": 1234, "sha256": "...", "exists": true}
  ],
  "checks": {
    "numbers_ok": true,
    "number_errors": [],
    "number_warnings": [],
    "citation_map": {"mapped": 9, "unsourced_paragraphs": 0}
  },
  "uncertainty": [{"item": "...", "detail": "...", "action": "..."}],
  "unresolved_questions": [{"question": "...", "action": "...", "owner": "author"}],
  "limitations": ["..."],
  "run": {"pipeline": ["inventory", "workorder", "evidence"],
          "draft_source": "...", "started_at": "...", "finished_at": "..."},
  "generated_at": "ISO-8601"
}
```

规则：

- 五个顶层字段必须存在：`case_id`、`deliverable`、`preserved_facts`、
  `evidence_map`、`change_log`。
- 主稿缺失时入口仍写 `status=partial` 的 `result.json` 并 exit 0：
  `deliverable.path` 为 `null`，`limitations` 说明未找到模型主稿；
  不得用抽取式保底稿冒充 `completed`。
- `deliverable.path` 必须指向真实主稿；`artifacts` 中每个 `path` 必须存在。
- `preserved_facts` 覆盖正文关键数字/条件/引用；没有就写 `[]` 并在
  `limitations` 说明。
- `evidence_map` 只写有来源的论断；市场判断不得作为 fact。
- `change_log` 记录实际改动；即使只改一段也要有记录。
- `status=partial` 时必须在 `uncertainty` 或 `limitations` 给出原因。
- `output_contract` 原样复制；`contract_coverage` 逐字段说明是否满足。
- `title` 是成稿标题（主稿 H1），`source_title` 是材料标题；两者不要混用。
- `preserved_facts[].value/unit` 选最像结论的量（优先 `%` 与结果指标），
  `values[]` 保留同一句里的全部量，便于回溯条件数字。
- 商业体裁可增加 `market_data` / `market_sources` / `ip` / `not_evaluated`；
  没有来源时写 `not_available` / `not_provided`。
- `/app/output/evidence.json` 是台账副本：`disclosable=false` 的 `statement` /
  `quote` 会替换为 `[redacted: restricted material]`，完整台账留在 `/app/work/`。

## 6. 文件落盘位置

| 文件 | 位置 | 说明 |
|---|---|---|
| 主稿 | `/app/output/<genre>.md` | 可编辑，先落盘 |
| DOCX | `/app/output/<genre>.docx` | stdlib OOXML，可编辑 |
| result.json | `/app/output/result.json` | 官方字段 + 台账 |
| evidence.json | `/app/output/evidence.json` | 证据台账副本 |
| citation-map.json | `/app/output/citation-map.json` | 断言→来源映射 |
| numbers-check.json | `/app/output/numbers-check.json` | 数字检查报告 |
| workorder.json | `/app/work/workorder.json` | 任务归一化 |
| inventory.json | `/app/work/inventory.json` | 输入文件清单 |
| run-report.json | `/app/work/run-report.json` | 本次运行摘要 |
