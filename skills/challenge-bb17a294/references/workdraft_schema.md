# Work draft schema（segments / glossary / result）

本文件定义 `main.py prepare` 与 `main.py finalize` 读写的文件结构。字段名是
契约：脚本、`scripts/kit.py` 与 `result.json` 必须一致。所有 JSON 均为 UTF-8，
无 BOM；Markdown 也按 UTF-8 保存。

---

## 1. 工作目录与产物

```text
/input/case.json                运行时提供；case_id 与 output_contract 原样保留
/input/task.md                  运行时提供；方向、文体、读者、格式以此为准
/input/<source>.md              真实原文（md/txt/tex；PDF/DOCX 先由运行时工具提取）

/app/work/segments.json         prepare 生成；段级原文与编号
/app/work/glossary-candidates.json  prepare 生成；候选术语（建议，不是锁定）
/app/work/bilingual-source.md   prepare 生成；待填写的对照脚手架
/app/work/workorder.json        prepare 生成；方向、文体、契约字段
/app/work/source.json           原文路径、sha256、候选文件、异常
/app/work/prepare-summary.json  prepare 摘要
/app/work/finalize-summary.json finalize 摘要

/app/output/translation.md      必需；完整纯译文（先于 result 落盘）
/app/output/bilingual.md        必需；段级对照（先于 result 落盘）
/app/output/glossary.json       finalize 生成或读取；锁定术语 + register
/app/output/translation-check.json  审计报告（可为 audit_unavailable）
/app/output/result.json         最终结果；含官方四字段
```

`result.artifacts[]` 只列真实存在且非空的输出文件。

---

## 2. `segments.json`

```json
{
  "schema_version": "1.0",
  "case_id": "CASE-ID",
  "direction": "zh->en",
  "source": "/input/source.md",
  "count": 2,
  "segments": [
    {
      "segment_id": "S01",
      "type": "heading",
      "line_start": 1,
      "line_end": 1,
      "source": "# 摘要"
    },
    {
      "segment_id": "S02",
      "type": "paragraph",
      "line_start": 3,
      "line_end": 3,
      "source": "氧空位是……"
    }
  ]
}
```

规则：

- `segment_id` 固定 `S01…Snn`，与 `bilingual.md`、`result.segments[]`
  完全同号、同序；
- `type` 取 `heading|paragraph|list|table|code|formula`；
- `source` 是原文块本身，不重写、不合并、不截断；
- 标题、图注、表注、代码块、公式块各自成段。

---

## 3. `glossary-candidates.json`

```json
{
  "schema_version": "1.0",
  "source": "/input/source.md",
  "count": 2,
  "candidates": [
    {
      "term": "氧空位",
      "kind": "bilingual_pair",
      "count": 2,
      "segment_ids": ["S03", "S08"],
      "confidence": "high",
      "suggested_target": "oxygen vacancy"
    }
  ]
}
```

`kind` 取 `acronym|bilingual_pair|cjk_ngram|latin_term`。候选项只是建议；
只有显式双语对（`术语（target）`）会在 `finalize` 自动锁定时采用。

---

## 4. `glossary.json`

```json
{
  "schema_version": "1.0",
  "case_id": "CASE-ID",
  "direction": "zh->en",
  "register": {
    "genre": "abstract",
    "formality": "academic",
    "person": "third-person",
    "tense": "present-for-claims; past-for-methods",
    "english_variant": "en-US",
    "avoid": ["don't", "can't"]
  },
  "terms": [
    {
      "source": "氧空位",
      "target": "oxygen vacancy",
      "locked": true,
      "aliases": ["oxygen vacancies", "oxygen-vacancy"],
      "forbidden": ["oxygen defect"],
      "case_sensitive": false
    }
  ],
  "do_not_translate": [
    {"text": "CeO2", "reason": "chemical formula"}
  ],
  "abbreviations": [
    {"short": "BET", "full_target": "Brunauer-Emmett-Teller",
     "expand_on_first_use": false}
  ],
  "conflicts": []
}
```

- `locked:true` 的条目必须出现在 `result.glossary[]` 且全文只允许
  `target`/`aliases` 的写法；`forbidden` 命中即 error。
- `target` 为空表示待确认；不参与逐段术语检查，也不进 `result.glossary[]`。
- `aliases` 只放复数、连字符形容词、官方变体，不放“差不多”的译法。
- `do_not_translate` 只放源文侧必须在译文中原样出现的 token。

---

## 5. `bilingual.md`

```markdown
# Bilingual Alignment

## S01
- **Source (zh):** # 摘要
- **Target (en):** # Abstract

## S02
- **Source (zh):** 氧空位是……
- **Target (en):** Oxygen vacancies are …
```

规则：

- 每个 `Sxx` 恰好一次，顺序与 `segments.json` 一致；
- `Target` 行放纯文本；译者注另起一行 `> **[译注]**：...`；
- `Target` 为空表示该段未译，必须记入 `untranslated` 并使
  `status=partial`。

---

## 6. `result.json`

```json
{
  "case_id": "CASE-ID",
  "task_family": "translation",
  "mode": "translation",
  "status": "completed",
  "direction": "zh->en",
  "artifacts": [
    {"role": "translation", "path": "translation.md", "format": "markdown"},
    {"role": "editable", "path": "bilingual.md", "format": "markdown"},
    {"role": "glossary", "path": "glossary.json", "format": "json"},
    {"role": "audit", "path": "translation-check.json", "format": "json"}
  ],
  "register": {"genre": "abstract", "formality": "academic"},
  "segments": [
    {
      "segment_id": "S01",
      "type": "paragraph",
      "source": "氧空位是……",
      "target": "Oxygen vacancies are …",
      "terms": ["氧空位"],
      "added": [],
      "omitted": [],
      "flags": [],
      "confidence": "high"
    }
  ],
  "glossary": [
    {"source": "氧空位", "target": "oxygen vacancy", "locked": true}
  ],
  "preservation": {
    "numbers_match": true,
    "units_match": true,
    "formulas_match": true,
    "citation_numbers_match": true,
    "figure_refs_match": true,
    "negation_match": true,
    "modality_match": true
  },
  "back_translation_check": [],
  "untranslated": [],
  "uncertainty": [],
  "change_log": [
    {"action": "translated", "scope": "all segments",
     "reason": "target-language translation was written first",
     "human_review": false}
  ],
  "translation_check": {
    "path": "translation-check.json",
    "exit_code": 0,
    "errors": 0,
    "warnings": 2,
    "note": "optional audit; non-zero never blocks delivery"
  },
  "deliverable": {
    "path": "translation.md",
    "format": "markdown",
    "role": "target-language translation",
    "segments": 1
  },
  "preserved_facts": [
    {"kind": "number", "source": "120 °C", "target": "120 °C", "locator": "S02"}
  ],
  "evidence_map": [
    {"claim_id": "C01", "claim": "Oxygen vacancies are …",
     "source": "S02", "locator": "S02 sentence 1",
     "quote": "氧空位是……", "paraphrase": "Oxygen vacancies are …"}
  ],
  "path_check": {"ok": true, "problems": []}
}
```

### 官方四字段（必须是顶层、非空、可回查）

| 字段 | 类型 | 约束 |
|---|---|---|
| `deliverable` | object | `path` 指向真实存在的 `translation.md`（或 `bilingual.md`）；`format` 为 `markdown` |
| `preserved_facts` | array | 每条含 `kind/source/target/locator`；来自真实数字、单位、引用、术语、否定、情态 |
| `evidence_map` | array | 每条含 `claim_id/claim/source/locator`，并至少有 `quote` 或 `paraphrase` |
| `change_log` | array | 每条含 `action/scope/reason/human_review`；至少记录翻译与组装动作 |

`output_contract` 中未被上述字段覆盖的额外键会原样保留到顶层；`case_id`
原样保留。`segments[]` 的 `source/target` 必须与切分和译文一致；
`glossary[]` 必须覆盖 `glossary.json` 中所有有 target 的 locked 条目。

---

## 7. 校验与降级

```bash
# 真实运行时
python3 scripts/main.py prepare  --input /input --work /app/work --output /app/output
# 写 translation.md + bilingual.md
python3 scripts/main.py finalize --work /app/work --output /app/output
```

- `main.py` 返回 0 表示已写出该阶段文件；审计 findings 不改变退出码。
- 脚本崩溃时写 `work/main-error.json` 与 `uncertainty`，不伪造译文。
- `status=completed` 要求 `untranslated=[]`；否则必须 `partial` 或
  `input_error` 并说明限制。
