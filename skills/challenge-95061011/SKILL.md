---
name: multi-document-synthesizer
description: 多文档比较与整合（workorder-first）：读取 /input/comparison-workorder.json 与全部 corpus，按 exact source_document_id/paragraph_id 建立共同字段矩阵，先对 task/metric/units/material/pollutant/budget 逐项判定可比性，再综合；保留 noncomparable/nonreported/single_document_only，单篇结论不得写成领域共识。Compare multiple scientific documents into /app/output/result.json with exact provenance and a six-item comparability gate. Use for 多篇论文比较、文献综述整合、跨文档异同分析、比较矩阵、comparison-workorder.
---

# 多文档比较与整合（Multi-document Synthesizer）

## 用途与触发

围绕同一研究问题，把多篇论文/材料落到同一套字段上，交付**逐格可对照、逐格可回查**的比较矩阵和综合结论；不可比、未报告、单篇结论必须单独保留，不得合并成领域共识。

触发信号：

- `/input/comparison-workorder.json` 存在，或 task.md 出现「多文档比较」「跨文档综合」「比较矩阵」「共同字段」「comparable」「workorder」等词。
- 输入含 2 篇以上论文/报告/材料，要求比较方法、条件、指标、结论或识别分歧。
- 只有一个文档时**不满足多文档比较**：按负向路径输出 `insufficient_evidence`，不得把单篇总结冒充综合结论。

## 输入

| 输入 | 作用 |
|---|---|
| `/input/comparison-workorder.json` | **第一权威**：`source_document_id`、`paragraph_id` 规则、比较维度、目标材料/污染物、不可比规则 |
| `/input/case.json` | `case_id`、文档清单、路径、段落/表格/图注标识；`result.case_id` 必须回显 |
| `/input/task.md` | 输出契约、语言、评分边界；与 workorder 冲突时以 task.md 的结构为准，并记录冲突 |
| `/input` 下全部 corpus 文件 | 实际论文/材料。先列目录再读；PDF 用环境提供的 `pdf-text-tool`/`ocr-tool`，不得凭文件名猜内容 |

workorder 字段名可能不同，按以下别名读取，并把原键名写入 `workorder.key_map`：

| 语义 | 优先键 | 兼容键 |
|---|---|---|
| 文档 id | `source_document_ids` | `source_documents`, `documents`, `document_ids`, `corpus` |
| 段落定位 | `paragraph_id_scheme` | `paragraph_map`, `paragraph_ids`, `locator_scheme` |
| 比较维度 | `comparison_dimensions` | `dimensions`, `fields`, `comparison_fields` |
| 目标材料 | `target_material` | `target_materials`, `material` |
| 目标污染物 | `target_pollutant` | `target_pollutants`, `pollutant`, `dye` |
| 不可比规则 | `incomparability_rules` | `comparability_rules`, `rules`, `constraints`, `notes` |

## 交付物优先流程（必须按顺序）

### 第 1 步（约 15% 预算）：workorder-first + 材料盘点

1. 先读 `/input/comparison-workorder.json`。把下列内容**逐字抄进** `result.workorder`：
   - `source_document_ids[]`：保留大小写、连字符、版本号，不改写、不缩写；
   - `paragraph_id_scheme` 或段落映射：这是唯一合法的段落定位口径；
   - `comparison_dimensions[]`：维度名逐字保留，后续矩阵行名与之一致；
   - `target_material`、`target_pollutant`：目标范围，范围外文档要标记而不合并；
   - `incomparability_rules[]`：如「不同数据集不得合并」「不同染料不得合并」「无换算不得比较」。
2. 列出 `/input` 下全部 corpus 文件，建立 `source_document_id → title → type → read_scope` 清单；确认 workorder 里的每个 id 都能找到对应文件。
3. 若 workorder 缺失：回退 `/input/case.json` + task.md，写 `workorder.loaded=false`、`uncertainty: workorder_missing`，并继续交付矩阵；不得编造 id 或维度。
4. 若实际可读文档少于 2 篇，或 workorder 指定文档缺失且无法补齐：顶层 `status=insufficient_evidence`，`multi_document.satisfied=false`，写清「不满足多文档比较」；仍输出清单和缺口，不写综合共识。

### 第 2 步（约 40% 预算）：先写完整矩阵并落盘

1. 立即写 `/app/output/result.json` 骨架：`case_id`、`status`、`workorder`、`documents`、`dimensions`、`comparison_table`、`multi_document`。先保证 JSON 可解析，再补内容。
2. `comparison_table[]` 每个维度覆盖全部 workorder 文档；每个单元格固定七字段：

```json
{"source_document_id": "DOC-A", "paragraph_id": "p5",
 "dimension": "degradation_efficiency", "value": 92, "unit": "%",
 "condition": "0.5 g/L, 60 min, 365 nm, methylene blue", "status": "reported"}
```

3. 状态只用四种：
   - `reported`：原文报告，必须有 `paragraph_id`，并尽量补 `quote`；
   - `not_reported`：原文没有；`value=null`，`paragraph_id=null`，在 `synthesis.nonreported[]` 登记；
   - `incomparable`：原文有值，但单位/指标/材料/污染物/预算不同，不能进入同一比较；保留 `value/unit/condition` 和 `reason`；
   - `unreadable`：材料存在但读不出（扫描、损坏、权限）；`value=null`，写 `reason`，不推断内容。
4. 每个非空单元格至少带一个定位：优先 `paragraph_id`；表格用 `table_id + row_index`，图用 `figure_id + panel`，放在 `locator` 里作为补充。**不得用自造 id 冒充 workorder 的 paragraph_id**。
5. 数值必须与单位、条件同源记录；原文没给单位就写 `unit=null` + `reason`，不按常识补单位。

### 第 3 步（约 25% 预算）：六项可比性门 + 综合

1. 在做任何定量综合（均值、排序、倍数、百分比差、合并结论）之前，逐项判定：
   `task / metric / units / material / pollutant / budget`。
2. 每一项输出 `verdict ∈ {comparable, incomparable, unknown}`；`incomparable` 和 `unknown` 必须写 `reason`、`affected_documents`、`evidence`。判定口径见下节。
3. 写 `gate_summary`：
   - `quantitative_synthesis ∈ {allowed, restricted, blocked}`；
   - `restricted` 时必须给 `permitted_subset[]`（维度 + 文档 id，至少 2 篇且相关项均 comparable）；
   - `blocked` 时禁止任何跨文档均值/排序/倍数结论。
4. 写 `synthesis`：
   - `agreements[]`：每条 `support[]` 至少含 **2 个不同 `source_document_id`**；单篇支持的不算 agreement；
   - `divergences[]`：双方并列，`resolution ∈ {unresolved, explained_by_condition}`，能解释的写清条件；
   - `gaps[]`：由矩阵空白或冲突支撑；
   - `noncomparable[]`：每个被阻断的比较一条，写 item/dimension/文档/原因/证据；
   - `nonreported[]`：每个未报告单元格一条，写文档/维度/检索范围/原因；
   - `single_document_only[]`：只由 1 篇支持的结论全部放这里，写 `text/source_document_id/paragraph_id`，并注明「不作为领域共识」。
5. 禁止把不同数据集、不同染料/污染物、不同材料、不同单位或不同预算的数值直接合并；禁止用「平均/总体/普遍认为」掩盖不可比。

### 第 4 步（约 15% 预算）：可选审计，不阻塞交付

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
python3 "$SKILL_DIR/scripts/check_synthesis.py" \
  --result /app/output/result.json \
  --workorder /input/comparison-workorder.json --json
```

脚本是可选加速器。命令失败、文件缺失或时间不足时，直接按下方质量闸门手工核对，并在 `uncertainty` 写 `audit_unavailable`；**不得为了通过 checker 删减真实内容或编造字段**。

### 第 5 步（约 5% 预算）：落盘核对

- JSON 可解析；`case_id` 与输入一致；`status` 四选一。
- 矩阵每个维度覆盖全部文档；每个 `reported` 有定位；每个 `not_reported` 在 `nonreported[]`。
- 六项 gate 齐全；`incomparable/unknown` 有原因；没有跨组定量合并。
- `agreements` 每条 ≥2 篇；单篇结论只出现在 `single_document_only[]`。
- 官方别名 `status/result/claims/uncertainties` 已补齐。

## 可比性门（六项，定量综合前必须逐项判定）

方法上对齐 SWiM 的可比性分组与 PRISMA 式可追溯记录：先分组、后综合；不可比组不合并；只吸收方法，不复制外部文件。

| item | 判定问题 | comparable 的条件 | 典型 incomparable 信号 |
|---|---|---|---|
| `task` | 是否回答同一个比较问题、同一目标 | 研究问题/目标一致，或 workorder 明确限定子集 | 一篇做机理、一篇做性能；一篇是综述、一篇是原始研究 |
| `metric` | 指标定义是否同一 | 都是降解效率 / 都是速率常数 / 都是去除率，定义一致 | 降解效率 vs 速率常数 vs 吸附容量 vs TOC 去除 |
| `units` | 单位、量纲、基准是否一致或可换算 | 同单位，或有明示换算因子和基准 | `%` vs `fraction` vs `mg/L`；干重 vs 湿重；不同波长基准 |
| `material` | 材料/相/组成/改性是否同一 | 同一材料、同一相、同一改性水平 | 纯 TiO2 vs 复合材料；不同掺杂量；不同晶相 |
| `pollutant` | 目标污染物/染料是否同一 | 同一污染物（如都是 methylene blue） | methylene blue vs rhodamine B vs phenol；不同染料不得合并 |
| `budget` | 催化剂投加量、时间、光强/能量、成本等预算是否同一 | 投加量、时间、光源/光强、成本口径一致 | 0.5 g/L vs 1.0 g/L；60 min vs 120 min；不同灯功率 |

判定规则：

1. `unknown` 按**不可比**处理（保守原则），不得默认 comparable；写清缺哪份证据。
2. `incomparable` 必须同时写 `reason`、`affected_documents`（至少 2 个，或明确受影响子集）和 `evidence`（`source_document_id + paragraph_id`）。
3. 逐对比较与逐项判定分开：同一个 item 可以「DOC-A 与 DOC-B comparable，但 DOC-C incomparable」，在 `affected_documents` / `permitted_subset` 中写清。
4. 只有六项中与当前定量结论相关的项全部 `comparable` 时，才允许对该子集做定量合并；任何一项 `incomparable/unknown` 都只能做定性并列。
5. 明示换算不自动消除其他差异：即使单位可换算，仍要检查 metric/material/pollutant/budget；换算记录写在 `conversion: {from, to, factor, source}`。

`gate_summary` 判定：

- `allowed`：所有相关项 comparable，且至少 2 篇在同一子集；
- `restricted`：存在可比子集（≥2 篇、相关项 comparable），但全量不可比；`permitted_subset[]` 必须非空；
- `blocked`：找不到任何 ≥2 篇的可比子集；禁止任何跨文档定量结论。

## 单篇 vs 多篇（硬约束）

- `agreements[]` 每条必须有 **≥2 个不同 source_document_id**；只有 1 篇支持的结论放入 `single_document_only[]`。
- `single_document_only[]` 的结论不得写成「领域共识」「普遍认为」「已有研究表明」；只能写「DOC-X 报告了……」。
- 单篇总结、单篇摘要卡片、只读一篇的综述**不满足多文档比较**；若输入只有 1 篇，顶层 `status=insufficient_evidence`，`multi_document.satisfied=false`，并说明原因。
- 综述类文档只用于建立框架或定位原始研究，不能作为 agreement 的唯一证据；引用时标 `source_type: "review"`。

## 输出契约

`/app/output/result.json` 必须同时保留题目字段和官方 reading 别名。下面是关键字段形状（示例为节选，正式结果必须覆盖全部维度和文档；示例值来自合成材料，不是正式答案）：

```json
{
  "case_id": "与输入一致",
  "status": "answered|partial|insufficient_evidence|input_error",
  "task_family": "reading",
  "workorder": {
    "path": "/input/comparison-workorder.json",
    "loaded": true,
    "source_document_ids": ["DOC-A", "DOC-B", "DOC-C"],
    "paragraph_id_scheme": "p1..pN within each document",
    "comparison_dimensions": ["material", "pollutant", "metric", "degradation_efficiency", "unit", "budget", "condition", "conclusion"],
    "target_material": "anatase TiO2",
    "target_pollutant": "methylene blue",
    "incomparability_rules": ["Do not pool different datasets.", "Do not pool different dyes/pollutants."],
    "key_map": {"source_document_ids": "source_document_ids"}
  },
  "documents": [
    {"source_document_id": "DOC-A", "title": "Paper A", "type": "research",
     "read_scope": ["p1", "p6"], "included": true, "reason": "直接相关"}
  ],
  "dimensions": ["material", "pollutant", "metric", "degradation_efficiency", "unit", "budget", "condition", "conclusion"],
  "comparison_table": [
    {
      "dimension": "degradation_efficiency",
      "cells": [
        {"source_document_id": "DOC-A", "paragraph_id": "p3", "dimension": "degradation_efficiency",
         "value": 92, "unit": "%", "condition": "0.5 g/L, 60 min, 365 nm, methylene blue",
         "status": "reported", "quote": "Degradation efficiency reached 92% ..."},
        {"source_document_id": "DOC-B", "paragraph_id": "p5", "dimension": "degradation_efficiency",
         "value": 0.81, "unit": "fraction of initial absorbance", "condition": "0.5 g/L, 60 min, 365 nm, methylene blue",
         "status": "incomparable", "reason": "unit/basis differs; no conversion reported"},
        {"source_document_id": "DOC-C", "paragraph_id": null, "dimension": "degradation_efficiency",
         "value": null, "unit": null, "condition": null,
         "status": "not_reported", "reason": "DOC-C 只报告 RhB，不含 MB 降解效率"}
      ]
    }
  ],
  "comparability_gate": [
    {"item": "task", "verdict": "comparable", "reason": "同一光催化降解问题",
     "affected_documents": ["DOC-A", "DOC-B", "DOC-C"],
     "evidence": [{"source_document_id": "DOC-A", "paragraph_id": "p1"}]},
    {"item": "metric", "verdict": "comparable", "reason": "均以 degradation efficiency 为主指标", "affected_documents": ["DOC-A", "DOC-B"], "evidence": []},
    {"item": "units", "verdict": "incomparable", "reason": "DOC-A 用 %，DOC-B 用 fraction of initial absorbance，未给换算", "affected_documents": ["DOC-A", "DOC-B"], "evidence": [{"source_document_id": "DOC-B", "paragraph_id": "p5"}]},
    {"item": "material", "verdict": "comparable", "reason": "均为 anatase TiO2", "affected_documents": ["DOC-A", "DOC-B", "DOC-C"], "evidence": []},
    {"item": "pollutant", "verdict": "incomparable", "reason": "DOC-A/DOC-B 为 MB，DOC-C 为 RhB；不同染料不得合并", "affected_documents": ["DOC-A", "DOC-B", "DOC-C"], "evidence": [{"source_document_id": "DOC-C", "paragraph_id": "p1"}]},
    {"item": "budget", "verdict": "incomparable", "reason": "DOC-C 投加量 1.0 g/L，DOC-A/DOC-B 为 0.5 g/L", "affected_documents": ["DOC-A", "DOC-C"], "evidence": [{"source_document_id": "DOC-C", "paragraph_id": "p2"}]}
  ],
  "gate_summary": {
    "quantitative_synthesis": "blocked",
    "permitted_subset": [],
    "reason": "降解效率因单位/污染物/预算不可比；没有 ≥2 篇的可比子集，禁止合并"
  },
  "multi_document": {"document_count": 3, "satisfied": true, "basis": "3 篇 + 2 条多篇 agreement"},
  "synthesis": {
    "agreements": [
      {"text": "三篇都把材料鉴定为 anatase TiO2",
       "support": [{"source_document_id": "DOC-A", "paragraph_id": "p1"},
                   {"source_document_id": "DOC-B", "paragraph_id": "p4"},
                   {"source_document_id": "DOC-C", "paragraph_id": "p4"}]}
    ],
    "divergences": [
      {"text": "DOC-A 与 DOC-B 的降解数值单位/基准不同，不能直接比较",
       "sides": [{"source_document_id": "DOC-A", "paragraph_id": "p3", "claim": "92%"},
                 {"source_document_id": "DOC-B", "paragraph_id": "p5", "claim": "0.81 fraction"}],
       "resolution": "explained_by_condition",
       "reason": "测量基准不同，且未报告换算"}
    ],
    "gaps": [{"text": "缺少同一预算下跨污染物比较", "affected_dimension": "pollutant",
              "support": [{"source_document_id": "DOC-C", "paragraph_id": "p2"}]}],
    "noncomparable": [
      {"item": "units", "dimension": "degradation_efficiency",
       "source_document_ids": ["DOC-A", "DOC-B"], "reason": "单位/基准不同且无换算",
       "evidence": [{"source_document_id": "DOC-B", "paragraph_id": "p5"}]},
      {"item": "pollutant", "dimension": "degradation_efficiency",
       "source_document_ids": ["DOC-A", "DOC-B", "DOC-C"], "reason": "DOC-C 为 RhB；不同染料不得合并",
       "evidence": [{"source_document_id": "DOC-C", "paragraph_id": "p1"}]},
      {"item": "budget", "dimension": "degradation_efficiency",
       "source_document_ids": ["DOC-A", "DOC-C"], "reason": "1.0 g/L vs 0.5 g/L",
       "evidence": [{"source_document_id": "DOC-C", "paragraph_id": "p2"}]}
    ],
    "nonreported": [
      {"source_document_id": "DOC-C", "dimension": "degradation_efficiency", "paragraph_id": null,
       "search_scope": "p1-p6", "reason": "DOC-C 只报告 RhB，不含 MB 降解效率"}
    ],
    "single_document_only": [
      {"text": "DOC-A 报告 {001} 晶面暴露度为 42%，并将其与 MB 降解增强联系",
       "source_document_id": "DOC-A", "paragraph_id": "p5",
       "note": "仅单篇支持，不作为领域共识"}
    ]
  },
  "conflicts": [],
  "uncertainty": ["workorder 字段名已按别名解析", "DOC-B 未报告单位换算"],
  "change_log": [{"step": 1, "action": "读取 workorder 并盘点 3 篇文档", "evidence": "comparison-workorder.json"}],
  "support_files_used": ["references/multi-doc-synthesis-playbook.md", "scripts/check_synthesis.py"],
  "result": {
    "summary": "3 篇文档、六项可比性门、矩阵与非可比/未报告/单篇结论已记录",
    "matrix_location": "top-level comparison_table",
    "agreement_count": 1,
    "divergence_count": 1,
    "noncomparable_count": 1,
    "nonreported_count": 1,
    "single_document_only_count": 1,
    "quantitative_synthesis": "blocked"
  },
  "claims": [
    {"claim_id": "C1", "kind": "agreement", "text": "三篇都把材料鉴定为 anatase TiO2",
     "evidence": [{"source_document_id": "DOC-A", "paragraph_id": "p1"},
                  {"source_document_id": "DOC-B", "paragraph_id": "p4"}],
     "source_id": "DOC-A", "location": "p1"}
  ],
  "uncertainties": ["DOC-C 为 RhB，未与 MB 合并"]
}
```

强制规则：

- `status` 只能四选一：`answered|partial|insufficient_evidence|input_error`。
- `comparison_table[]` 的单元格七字段齐全；`dimension` 与所在行一致。
- `reported` 必须有非空 `value` 与 `paragraph_id`；`not_reported` 的 `value/paragraph_id` 为 `null`；`unreadable` 必须有 `reason`；`incomparable` 必须有 `reason`。
- `comparability_gate[]` 恰好覆盖 `task/metric/units/material/pollutant/budget` 六项；不得省略、不得合并成一项。
- `agreements[]` 每条 `support[]` 至少 2 个不同 `source_document_id`；单篇结论只能进 `single_document_only[]`。
- `noncomparable[]` 与 gate 中 `incomparable/unknown` 对应；`nonreported[]` 与矩阵 `not_reported` 对应；不得只写其中一个。
- `claims[]` 的每条关键结论有 `claim_id/kind/text/evidence/source_id/location`，且不得引入矩阵之外的新信息。
- 官方字段是**追加**：`status/result/claims/uncertainties` 与题目字段同时存在；`uncertainties` 汇总未报告、不可比、未知和审计失败。

## 支持文件（按需使用，不阻塞）

| 文件 | 何时用 | 命令/用法 |
|---|---|---|
| `README.md` | 安装、快速开始、最小示例、已知限制 | 分发与人工阅读；命令中的 `$SKILL_DIR` 解析见该文件 |
| `references/field-dictionary.md` | 写字段名、判定状态或对接他人结果前 | 逐字段查表；字段名与枚举以此为准 |
| `references/multi-doc-synthesis-playbook.md` | 第 1 步定 workorder 字段、第 3 步判定可比性和综合前 | 先读；按其中字段表、六项门、失败模式执行 |
| `templates/comparison-matrix.json` | 写 `result.json` 骨架时 | 复制结构，替换 `REPLACE_WITH_*`；不得原样交付 |
| `scripts/check_synthesis.py` | 第 4 步可选审计 | `python3 "$SKILL_DIR/scripts/check_synthesis.py" --result /app/output/result.json --workorder /input/comparison-workorder.json --json` |
| `scripts/check_synthesis.py --self-test` | 首次使用或修改校验器后 | `python3 "$SKILL_DIR/scripts/check_synthesis.py" --self-test --json` |

`$SKILL_DIR` 通常为 `/skills/weekly-skill`，按上面的 `find` 命令解析；不要硬编码个人路径。校验器只做结构审计，不判断科学正确性；失败时按 `problems` 修数据，修不了就在 `uncertainty` 记录并继续交付。

## 质量闸门

**必须（交付前逐条确认）**

1. `workorder` 已抄下 `source_document_ids`、段落规则、`comparison_dimensions`、`target_material`、`target_pollutant`、`incomparability_rules`；缺失有记录。
2. 矩阵每个维度覆盖全部文档；单元格使用 exact `source_document_id` 和 `paragraph_id`；七字段齐全。
3. 六项 gate 齐全，`verdict` 合法；`incomparable/unknown` 有原因和证据。
4. 没有跨不可比组的均值、排序、倍数或「总体」结论；`gate_summary` 与 `noncomparable[]` 一致。
5. `agreements` 每条 ≥2 篇；单篇结论只在 `single_document_only[]`，且没有「领域共识」措辞。
6. `nonreported[]` 覆盖所有 `not_reported`；`noncomparable[]` 覆盖所有被阻断比较。
7. `/app/output/result.json` 可解析，`case_id` 回显，官方别名齐全。
8. 输入 <2 篇时 `status=insufficient_evidence` 且 `multi_document.satisfied=false`。

**建议（不阻塞交付）**

- 每个 `reported` 单元格有逐字 `quote`，能直接回查原文。
- `divergences` 双方都有定位；能由条件解释的写清条件。
- 综述类文档标 `source_type: "review"`，不作为唯一证据。
- 运行 `check_synthesis.py` 返回 `ok:true`；失败原因写入 `uncertainty`。

## 异常处理

| 情况 | 处理 |
|---|---|
| `comparison-workorder.json` 缺失/损坏 | 回退 `case.json` + task.md；`workorder.loaded=false`，`uncertainty` 记 `workorder_missing`；仍按可推断的 id/维度交付 |
| workorder 指定文档缺失 | 在 `documents[]` 标 `included=false`/`unreadable`，写缺失原因；不得用其他文档顶替该 id |
| 文档不可读/扫描件 | 单元格 `status=unreadable` + `reason`；能 OCR 则 OCR 并标 `ocr_confidence`，不能则登记 `nonreported` 或 `unreadable` |
| 段落 id 缺失/不一致 | 优先使用 workorder/case 的映射；没有映射时用原文自身锚点，并写 `paragraph_id_source` 与 `paragraph_id_map`，不得伪造官方 id |
| 单位不同 | 先查是否有明示换算；有则写 `conversion`，无则 `units=incomparable`，对应单元格 `status=incomparable`，禁止静默换算 |
| 污染物/材料/预算不同 | 对应 gate 项 `incomparable`，写 `affected_documents`；不同染料/数据集不得合并 |
| 只有 1 篇文档 | `status=insufficient_evidence`，`multi_document.satisfied=false`，明确「不满足多文档比较」；只输出单篇事实和缺口，不写 agreement |
| 时间不足 | 先保证矩阵 + gate + 至少 1 条多篇 agreement 落盘；其余写 `partial` 和 `uncertainty`，不编造综合结论 |
| checker 失败 | 手工按质量闸门核对；把 `audit_unavailable` 或具体 problem 写入 `uncertainty`，继续交付 |

## 最小示例（示例，不是正式答案）

三篇合成文档：DOC-A 报告 MB 降解 92%（%），DOC-B 报告 MB 降解 0.81（fraction of initial absorbance），DOC-C 报告 RhB 降解 88%（1.0 g/L）。正确处理：

- `units` = `incomparable`（A/B 单位/基准不同）；
- `pollutant` = `incomparable`（C 是 RhB，不是 MB）；
- `budget` = `incomparable`（C 为 1.0 g/L）；
- `quantitative_synthesis` = `restricted` 或 `blocked`，不得输出「三篇平均降解率」；
- A/B/C 都报告 anatase TiO2 → 可写 1 条 ≥2 篇的 agreement；
- A 的 {001} 晶面结论只有 1 篇 → 进 `single_document_only[]`；
- C 未报告 band gap → 进 `nonreported[]`。

## 版本与限制

- 版本：`0.6.0`（workorder-first + 六项可比性门）。
- 已知限制：workorder 的真实键名/嵌套结构可能随案例变化；本 Skill 用别名兼容，若仍无法映射，保留原对象到 `workorder.key_map` 并在 `uncertainty` 说明。
- 不联网、不补造数据；材料缺失、不可读、未报告分别登记，不互相冒充。
- checker 是可选审计器，不是评分器；通过 checker 不等于结论正确。
- 维护：修改模板/checker 时同步更新本文件的字段表、命令和版本号。
