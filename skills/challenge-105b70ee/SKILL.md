---
name: literature-fact-extractor
description: 文献结论与数据提取：按 /input/target-fields.json 的 field_id 逐项抽取标量、单位、统计量、实验条件、比较对象与逐字段段落/表格/图注原句，明确 reported / not_reported / source_conflict / extraction_failed，写入 /app/output/result.json。Extract literature conclusions and numerical results with unit/condition/comparator binding and exact paragraph_id/quote provenance. Use for 数据抽取、字段表、结论汇总、文献矩阵、target-fields.
---

# 文献结论与数据提取（Literature Fact Extractor）

## 用途与触发

用于：给定论文/报告与目标字段表，要求「提取字段」「结论汇总」「数据表」「结果抽取」
「实验条件表」「字段级证据定位」时，把散落在正文、表格、图注和补充材料中的数值与结论
还原成一份可核对、可复用的结构化结果。

触发信号：

- `/input/target-fields.json` 存在，或 task.md 出现「抽取」「字段」「指标」「实验条件」
  「结论」「数据表」「field_id」「extract」等词。
- 输入含论文正文、表格、图注或补充材料，要求数值必须带单位和成立条件。
- 需要区分“原文未报告”“材料冲突”“抽取失败”，而不是给一段压缩总结。

不适用：纯翻译、纯排版、无材料支撑的开放写作。遇到这类请求先说明边界。

核心约束：**一个 `field_id` 一条结果；值、单位、统计量、条件、比较对象、原句定位一起
交付；没有就显式写没有；冲突保留候选，不静默择一。**

## 支持文件（必须使用）

`{SKILL_DIR}` 为已安装 Skill 目录（评测环境通常为 `/skills/weekly-skill`）。

| 文件 | 何时用 | 准确命令 |
|---|---|---|
| `references/data-extraction-playbook.md` | 开始定字段、抽取数值、处理表格/缺失/冲突前 | 先读；按字段表、五元组、状态词表、失败模式执行 |
| `references/00-protocol.md` | 第一次运行、确认目录/预算/工具 | 按其中路径与预算安排步骤 |
| `references/01-evidence.md` | 选 quote、定 locator、区分 reported/inferred | 每个值按该规则绑证据 |
| `references/02-reading.md` | 判断问题类型与缺口型回答 | 缺口必须 `not_reported`/`insufficient_evidence` |
| `references/08-quality-gates.md` | 交付前总检 | 与本文件质量闸门一起执行 |
| `templates/extraction-schema.json` | 写 `/app/output/result.json` 骨架时 | 复制结构，替换 `REPLACE_WITH_*`；逐 `field_id` 填写 |
| `templates/result_envelope.json` | 官方 reading 信封 | 补齐 status/result/claims/uncertainties |
| `scripts/extract_tables.py` | 输入含 Markdown 表格、CSV/TSV 或需要表格候选索引时 | `python3 {SKILL_DIR}/scripts/extract_tables.py --input /input --out /app/work/tables.json --json` |
| `scripts/check_extraction.py` | 每次写盘后、交付前 | `python3 {SKILL_DIR}/scripts/check_extraction.py --result /app/output/result.json --input /input --json --out /app/work/extraction_check.json` |
| `scripts/check_extraction.py`（自测模式） | 首次使用或修改校验器后 | `python3 {SKILL_DIR}/scripts/check_extraction.py --self-test --json` |
| `scripts/validate_result.py` | 通用契约与 quote 回查 | `python3 {SKILL_DIR}/scripts/validate_result.py /app/output/result.json --input /input --check-quotes` |
| `scripts/inspect_input.py` | 不确定输入结构/资源时 | `python3 {SKILL_DIR}/scripts/inspect_input.py /input --out /app/work/contract_hints.json` |

规则：

- `extract_tables.py` 只产出**候选索引**（表头、行原文、数值单元格、单位），不能代替
  回读原表与语义判断。
- `check_extraction.py` 的 `ok` 为 `false` 时不要交付；按 `problems` 逐条修，修不了就在
  `uncertainty` 说明并保留诚实状态。
- 若结果中引用了 PDF 抽取文本，给校验器加 `--input /app/work/extracted_text` 或在
  `/app/work` 建可读文本索引，确保 quote 逐字可查。
- `templates/extraction-schema.json` 是骨架，不是答案；不得把占位符原样交付。

## 输入

- `/input/target-fields.json`：**权威字段表**。每条至少含 `field_id` 与字段名，可能含
  `type`、`unit`/`expected_unit`、`required`、`description`。兼容 `fields[]`、
  `target_fields[]`、`requested_fields[]`，以及 `{field_id: {...}}` 映射。
  `field_id` 必须逐字保留，不改大小写、不加前缀、不合并。
- `/input/task.md`：输出契约、字段口径、语言与边界；与 target-fields 冲突时以 task.md
  的**输出结构**为准，以 target-fields 的 **field_id 集合**为准，并在 `uncertainty` 记录。
- `/input/case.json`：`case_id`、文档清单、段落/表格/图注标识；`result.case_id` 必须回显。
- 实际论文文件：PDF 先用环境提供的 `pdf-text-tool` 抽文本；扫描页用 `ocr-tool` 或视觉
  识别，并标 `page_text_status: none|text|scanned`。不得只凭文件名/摘要猜内容。

若没有 `target-fields.json`：从 task.md 的字段表建立 `schema[]`，`definition_source`
写 `task.md`；task.md 也没有时按默认集 `study_id, material/system, method, key_metric,
value, unit, condition, conclusion, limitation`，并声明为 `inferred`。

先盘点：

```bash
python3 {SKILL_DIR}/scripts/inspect_input.py /input --out /app/work/contract_hints.json
python3 {SKILL_DIR}/scripts/extract_tables.py --input /input --out /app/work/tables.json --json
```

## 执行步骤

1. **定字段表**：读 `target-fields.json`，固化 `schema[]`（`field_id/name/type/unit/
   required/definition_source`）。字段表确定后不再边读边加，避免跨文档不可比。
2. **建材料索引**：`doc_id → 标题 → 类型（原始研究/综述/标准/数据）→ 可读范围`；
   标出正文段落 id、表格 id、图注 id。PDF 标 `page_text_status`。
3. **表格/图注先行**：用 `extract_tables.py` 得到候选表；跨页表重建表头路径，续页缺
   表头时回查上一页；图注数值用 `figure_id` 定位。
4. **逐字段抽取**：按 `field_id` 搜索指标名、同义词、单位与数值模式；命中后回读整段、
   整行或整条图注，确认主语、指标、条件、比较对象。
5. **绑定五元组**：`value + original + unit + condition + locator`；再补 `statistic`、
   `comparator`、`sample`、`time_point`、`uncertainty`。条件来自另一段时加
   `condition_evidence`。
6. **语义核对**：填 `semantic_check`，确认 quote 的主语与指标确实对应 field 定义；
   逐字匹配但主语/指标不符时，改 `extraction_failed`，不能算 `reported`。
7. **缺失与冲突**：未找到 → `not_reported` + `missing_reason` + `missing[]`；材料存在但
   读不出 → `extraction_failed`；同条件多值 → `source_conflict` + `candidates[]` +
   `conflicts[]`。
8. **汇总与写盘**：按实际状态计算 `summary`；先写 `/app/output/result.json` 骨架，再补齐。
9. **交付校验**：运行 `check_extraction.py` 与 `validate_result.py`；有 problem 先修，
   修不了把顶层 `status` 改为 `partial` 并在 `uncertainty` 写清。

## 官方输出契约（必须补齐）

官方 `example-input` 的 `output_contract` 要求顶层字段：`status`、`result`、`claims`、`uncertainties`（status 取 answered|partial|insufficient_evidence|input_error）。
实际案例可能在 `/input/case.json` 的 `output_contract` 中给出额外或改名的键：**先读实际
键名并原样保留**，本契约只是超集。`claims[]` 至少覆盖每个 `reported` 与 `source_conflict`
字段，每条含 `claim_id/kind/evidence/source_id/location`；`kind` 取
`reported|derived|inferred|not_reported`，`location` 用
`doc_id + paragraph_id/table_id/figure_id + quote`。题目原有字段全部保留；交付前运行：

```bash
python3 {SKILL_DIR}/scripts/emit_official.py \
  --result /app/output/result.json --out /app/output/result.json \
  --main-field extractions --profile reading --json
```

该适配器只补齐官方别名，不改变任务字段；`validate_result.py` 之后仍需运行。

## 输出契约

`/app/output/result.json` 保留 case 标识、逐字段结果、证据/来源定位、运行状态与不确定性：

```json
{
  "case_id": "与输入一致",
  "status": "answered|partial|insufficient_evidence|input_error",
  "task_family": "reading",
  "schema": [
    {
      "field_id": "f2",
      "name": "conversion",
      "type": "number",
      "unit": "%",
      "required": true,
      "definition_source": "target-fields.json"
    }
  ],
  "extractions": [
    {
      "field_id": "f2",
      "field_name": "conversion",
      "status": "reported",
      "values": [
        {
          "value": 92,
          "original": "92%",
          "unit": "%",
          "unit_status": "reported",
          "unit_missing": false,
          "statistic": "point_estimate",
          "condition": "350 °C, GHSV 6000 h^-1",
          "condition_status": "reported",
          "comparator": "none",
          "sample": "not_reported",
          "time_point": "not_reported",
          "uncertainty": {
            "type": "not_reported",
            "value": null
          },
          "locator": {
            "doc_id": "paper1",
            "paragraph_id": "p2",
            "table_id": null,
            "figure_id": null,
            "page": 1,
            "row_index": null,
            "column": null,
            "quote": "The catalyst reached 92% conversion at 350 °C and GHSV 6000 h^-1."
          },
          "source_kind": "paragraph",
          "condition_evidence": null,
          "semantic_check": {
            "requested_metric": "propane conversion",
            "quoted_metric": "propane conversion",
            "subject": "Ni/Al2O3",
            "matches_requested_field": true,
            "note": "主语、指标与条件一致"
          },
          "digit_check": {
            "checked": true,
            "conflicts": []
          }
        }
      ],
      "candidates": [],
      "resolution": null,
      "resolution_note": null,
      "missing_reason": null,
      "search_scope": "p1-p4, t1, f1"
    }
  ],
  "missing": [
    {
      "field_id": "f4",
      "field": "space_velocity",
      "kind": "unit_missing",
      "reason": "原文只写 6000，未给单位",
      "search_scope": "p2-p4, t1"
    }
  ],
  "conflicts": [
    {
      "field_id": "f2",
      "field": "conversion",
      "candidates": [
        {
          "original": "92%",
          "value": 92,
          "unit": "%",
          "locator": {
            "paragraph_id": "p2",
            "quote": "..."
          }
        },
        {
          "original": "90",
          "value": 90,
          "unit": "%",
          "locator": {
            "table_id": "t1",
            "row_index": 1,
            "quote": "..."
          }
        }
      ],
      "note": "正文与表 1 不一致，保留双方候选"
    }
  ],
  "summary": {
    "field_count": 6,
    "reported_count": 5,
    "not_reported_count": 0,
    "extraction_failed_count": 0,
    "conflict_count": 1,
    "unit_missing_count": 0,
    "condition_missing_count": 0,
    "complete_rate": 0.8333
  },
  "corpus_scope": {
    "documents": 1,
    "year_range": "not_reported",
    "languages": [
      "en"
    ]
  },
  "uncertainty": [],
  "change_log": [
    {
      "step": 1,
      "action": "fixed six-field schema",
      "evidence": "target-fields.json"
    }
  ],
  "support_files_used": [
    "references/data-extraction-playbook.md",
    "scripts/extract_tables.py",
    "scripts/check_extraction.py"
  ],
  "result": {
    "summary": "题目主结果，按题目任务卡补充结构化字段"
  },
  "claims": [
    {
      "claim_id": "C1",
      "kind": "reported",
      "evidence": "…",
      "source_id": "…",
      "location": "…"
    }
  ],
  "uncertainties": []
}
```

强制规则：

- task.md 若定义额外顶层字段（如 `output_contract`、案例特有字段），必须原样保留；
  本契约是超集，可追加字段，不得删减或改名。
- `extractions[]` 与 target-fields 的 `field_id` **一一对应**，不重不漏；每个 field 只出现一次。
- `status` 只能四选一：`reported|not_reported|source_conflict|extraction_failed`。
- `reported` 必须有非空 `values[]`；`not_reported`/`extraction_failed` 的 `values` 必须为空；
  `source_conflict` 必须有 ≥2 个 `candidates[]`。
- 每个非空数值必须带 `unit`（或显式 `unit_status=not_reported` + `unit_missing=true`）、
  `statistic`、`condition`、`comparator`、`locator.quote`、`semantic_check`。
- `missing[]` 覆盖所有 `not_reported`/`extraction_failed`，以及所有 `unit_missing`/
  `condition_missing`；每条有 `reason`。
- `conflicts[]` 与所有 `source_conflict` 对应；候选各自带来源；选中某值必须写
  `resolution` + `resolution_note`。
- `summary` 的计数与 `complete_rate` 必须与实际 `extractions[]` 一致；`complete_rate`
  只统计无 unit/condition 缺口的 `reported` 字段。
- 不得出现占位符、猜测值、未记录的单位换算或材料外结论。

## 数值与条件绑定规则

1. **五元组同源**：`value`、`original`、`unit`、`condition`、`locator` 必须来自同一处
   直接证据；条件来自另一段时，用 `condition_evidence` 单独挂定位。
2. **原文优先**：`original` 逐字保留（含 `±`、`%`、括号负数、千分位、上标）；`value`
   是解析值。换算必须写 `conversion: {from, to, factor, note}`，并保留原值。
3. **单位显式**：原文无单位 → `unit:null`、`unit_status:"not_reported"`、
   `unit_missing:true`、`missing[] kind:"unit_missing"`；**禁止按领域常识补单位**。
4. **统计量显式**：`statistic ∈ {point_estimate, mean, median, min, max, range, sum,
   percentage, ratio, not_reported}`；区间写 `{min,max}`，不取中点冒充点估计。
5. **比较对象显式**：`comparator` 写与谁比、比什么指标；没有比较写 `"none"`。
   比较级表述必须能落到同条件数值；否则改写为“在某条件下报告了更高值”。
6. **百分比与百分点分开**：`%` 是比例，`percentage points` 是差值，不混用。
7. **误差独立**：`uncertainty` 写 `sd|se|ci95|range` 与值；不把 `±` 后面的数并进主值。
8. **条件可追溯**：条件里的数字必须出现在 value 的 quote 或 `condition_evidence.quote`；
   否则条件不可复核。

## 表格、图注与扫描页

1. **候选表索引**：先跑 `extract_tables.py`；它给出 `table_id`、`caption`、`header`、
   `column_units`、`rows[].raw`、`numeric_cells[]`，只作索引，不作结论。
2. **表头路径**：多级表头记 `header_path`；合并单元格记 `rowspan/colspan`；续页缺表头
   时回查上一页，不得把续页第一行当表头。
3. **表格 quote**：用整行原始文本（如 `| Ni/Al2O3 | 350 | 92 | 87 |`），并写
   `table_id`、`row_index`、`column`；不要用重新排版后的 CSV 冒充原行。
4. **单位继承**：表头单位（如 `Conversion (%)`）作用于整列；单元格后缀单位优先；
   两者冲突时以单元格为准并记录。
5. **图注**：图注中的数值可抽，`figure_id` + quote；从坐标轴读出的值标
   `read_from:"plot"`，给轴标定与读数误差。
6. **扫描/纯图页**：先判文本层；`page_text_status:"none"` 且未 OCR 时整条
   `extraction_failed`，原因写清；OCR 数字做逐位复核并标 `ocr_confidence`。
7. **合计复算**：有明示合计时本地复算，差额进 `uncertainty`；禁止为凑平修改原值。

## 缺失、冲突与来源优先级

| 情况 | 输出 | 禁止 |
|---|---|---|
| 原文未报告 | `not_reported` + `missing_reason` + `missing[]` | 填 0、均值或“合理猜测” |
| 材料存在但读不出 | `extraction_failed` + 原因 + `missing[]` | 冒充 `not_reported` |
| 单位缺失 | 保留原值；`unit_missing:true` + `missing[]` | 自动补 `%`/`°C`/`h^-1` |
| 条件缺失 | `condition_status:"not_reported"` + `missing[]` | 把条件塞进 value |
| 同条件多个值 | `source_conflict` + `candidates[]` + `conflicts[]` | 只留一个候选 |
| 不同条件多个值 | 分别记录多值，各自绑定条件 | 当成冲突或强行合并 |
| 综述转述 | `source_kind:"review"`，尽量回溯原始研究 | 冒充原始数据 |

来源优先级（仅用于解释，不用于删除候选）：正文结果段 ≈ 表格 > 图注 > 摘要 >
讨论/综述转述 > 补充材料，具体以 task.md 为准。

## 异常处理

| 情况 | 处理 |
|---|---|
| `target-fields.json` 缺失/损坏 | 回退 task.md；记录 `definition_source` 与 `uncertainty`；仍无法确定则 `status=input_error` |
| 字段定义模糊（指标/单位/时间点不清） | 先做最小澄清；无法澄清时按原文最直接口径抽取并记录假设 |
| PDF 无文本层 | 用 OCR/视觉识别；逐页标 `page_text_status`；读不出标 `extraction_failed` |
| 表格跨页/合并单元格 | 重建表头路径与续页关系；列错位未解决时 `extraction_failed` |
| 同一字段正文与表格冲突 | `source_conflict`；候选各自带 locator；不静默择一 |
| 材料语言非中文 | 保留原文 quote；解释文字按 task.md 语言 |
| 材料超预算/长文 | 按与字段相关性排序读摘要、结果、表格、图注；在 `search_scope` 记录跳过范围 |

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 字段覆盖 | `extractions[]` 与 target-fields 的 field_id 一一对应 | 补抽取或写 missing，不留空 |
| G2 | 证据 | 每个非空值有 paragraph/table/figure locator + 逐字 quote | 删除无证据值或标 extraction_failed |
| G3 | 单位条件 | value/original/unit/condition 绑定；条件数字可追溯 | 补 unit/condition_evidence；缺单位显式登记 |
| G4 | 语义 | 每个 reported 值 `semantic_check.matches_requested_field=true` | 主语/指标不符改 extraction_failed |
| G5 | 缺失 | `missing[]` 覆盖全部 not_reported/extraction_failed/unit_missing/condition_missing | 补 reason，不静默省略 |
| G6 | 冲突 | source_conflict 有 ≥2 候选且进 `conflicts[]` | 恢复候选与双方来源 |
| G7 | 结构 | `case_id/schema/extractions/missing/conflicts/summary` 齐全，JSON 可解析 | 先修结构再补内容 |
| G8 | 校验 | `check_extraction.py` 返回 `ok:true` | 逐条修 problems；修不了标 partial |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 字段漏抽 | 请求 f5，结果只有 f1-f4 | 按 field_id 全覆盖，缺的进 missing |
| 条件与数值脱钩 | 有 92%，不知温度/空速 | 补 condition + condition_evidence |
| 静默补单位 | 原文“6000”，结果写 `h^-1` | 改 `unit:null` + `unit_missing:true` |
| 缺失填零/均值 | 未报告字段出现“合理”数值 | 改 `not_reported` + reason |
| 冲突静默择一 | 只有一个候选且无 conflicts | 恢复全部候选与来源 |
| 逐字但对错指标 | quote 有数字但主语/指标不符 | semantic_check 置 false，改 extraction_failed |
| 跨页表头错位 | 同列出现不同量纲 | 重建 header_path，回查续页 |
| 单位换算无记录 | value 与 original 对不上 | 补 conversion{from,to,factor} |
| 综述冒充原始数据 | 唯一来源是 review | 标 review，回溯原始研究或降级 |
| 结构完整但内容空 | 字段齐全但 values 全是 null | 逐字段读取原文；找不到写 missing |

## 自检

- `schema[]` 覆盖全部 `field_id`；`extractions[]` 不重不漏。
- 每个 `reported` 值都有 `value/original/unit/statistic/condition/comparator/locator/quote/
  semantic_check`。
- 条件数字能在 value quote 或 `condition_evidence.quote` 中找到。
- 所有 `unit_missing`/`condition_missing`/`not_reported`/`extraction_failed` 都在
  `missing[]`；所有 `source_conflict` 都在 `conflicts[]`。
- 所有 quote 在输入语料中逐字可查；没有翻译、改写或拼接。
- `summary` 计数与实际一致；`support_files_used` 列出实际调用的文件。
- 运行 `check_extraction.py --json` 返回 `ok:true`，再运行 `validate_result.py
  --check-quotes` 无 problem。

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`status`、`schema[]`、`extractions[]`、
      `missing[]`、`conflicts[]`、`summary`、`uncertainty`、`change_log`、
      `support_files_used`
- [ ] 每个请求的 `field_id` 恰好一条 extraction，状态词合法
- [ ] 每个 reported 数值绑定单位、统计量、条件、比较对象与 locator/quote
- [ ] 每个 reported 值有 `semantic_check`，且主语/指标与字段定义一致
- [ ] `missing[]` 覆盖全部缺失项（含单位/条件缺失）
- [ ] 冲突项保留 ≥2 个候选与来源，未静默择一
- [ ] `summary` 计数与 `complete_rate` 和实际一致
- [ ] 官方契约键（按 `/input/case.json` 实际键名）齐全，`claims[]` 覆盖每个
      `reported`/`source_conflict` 字段且含 source_id + location
- [ ] `check_extraction.py` 与 `validate_result.py --check-quotes` 通过
- [ ] 无编造数值、无未记录的单位换算、无材料外结论

## 快速路径（预算内）

1. **0–2 分钟**：读 `target-fields.json`/task.md，固化 `schema[]` 与 `case_id`；跑
   `inspect_input.py` 与 `extract_tables.py`，建立段落/表格/图注索引。
2. **2–8 分钟**：先抽第一篇的 1–2 个完整 `reported` 字段，落盘 `result.json` 骨架；
   同时确认 quote 逐字可查。
3. **8–13 分钟**：补齐其余字段；做单位/条件绑定、条件证据、缺失与冲突登记。
4. **最后 2 分钟**：跑 `check_extraction.py` 与 `validate_result.py`；只修问题，不再
   新增字段。

## 最小示例（示例，非正式答案）

合成材料中 `f2=conversion` 原文报告 92%，`f4=space_velocity` 原文只写 6000 未给单位：

```json
{"extractions": [
  {"field_id": "f2", "status": "reported",
   "values": [{"value": 92, "original": "92%", "unit": "%",
               "statistic": "point_estimate", "condition": "350 °C",
               "comparator": "none",
               "locator": {"paragraph_id": "p2", "quote": "conversion reached 92% at 350 °C"},
               "semantic_check": {"matches_requested_field": true}}]},
  {"field_id": "f4", "status": "reported",
   "values": [{"value": 6000, "original": "6000", "unit": null,
               "unit_status": "not_reported", "unit_missing": true,
               "statistic": "point_estimate", "condition": "350 °C",
               "comparator": "none",
               "locator": {"paragraph_id": "p3", "quote": "GHSV was 6000"},
               "semantic_check": {"matches_requested_field": true}}]}
],
 "missing": [{"field_id": "f4", "kind": "unit_missing",
              "reason": "原文未给单位，未按常识补 h^-1"}]}
```

该示例只展示接口形状；正式结果必须来自实际材料，且补齐示例省略的字段。
