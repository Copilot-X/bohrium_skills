# 字段字典（comparison-workorder v2）

配合 `SKILL.md`、`templates/comparison-matrix.json` 和
`references/multi-doc-synthesis-playbook.md` 使用。字段名大小写与拼写固定；不要用
`doc_id` 代替 `source_document_id`。

## 1. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `case_id` | string | 是 | 与输入一致，原样回显 |
| `status` | enum | 是 | `answered` / `partial` / `insufficient_evidence` / `input_error` |
| `task_family` | string | 否 | 固定 `reading` |
| `workorder` | object | 是 | 见 §2 |
| `documents` | array | 是 | 见 §3 |
| `dimensions` | array[string] | 是 | 比较维度；包含 workorder 的 `comparison_dimensions` |
| `comparison_table` | array | 是 | 见 §4 |
| `comparability_gate` | array | 是 | 六项，见 §5 |
| `gate_summary` | object | 是 | 见 §6 |
| `multi_document` | object | 是 | `document_count`、`satisfied`、`basis` |
| `synthesis` | object | 是 | 见 §7 |
| `conflicts` | array | 是 | 同条件数值冲突；保留双方 |
| `uncertainty` | array | 是 | 未报告、不可比、未知、审计失败 |
| `change_log` | array | 是 | 每步动作与证据 |
| `support_files_used` | array | 建议 | 实际调用的支持文件 |
| `result` | object | 是 | 官方别名；主结果摘要 |
| `claims` | array | 是 | 官方别名；见 §8 |
| `uncertainties` | array | 是 | 官方别名；与 `uncertainty` 一致或更全 |

## 2. `workorder`

| 字段 | 类型 | 说明 |
|---|---|---|
| `path` | string | 通常 `/input/comparison-workorder.json` |
| `loaded` | bool | 是否成功读取 |
| `source_document_ids` | array[string] | 逐字抄录，保留大小写/连字符/版本号 |
| `paragraph_id_scheme` | string/object | 段落定位规则或映射 |
| `comparison_dimensions` | array[string] | 逐字抄录，矩阵行名与之一致 |
| `target_material` | string/array | 目标材料；范围外文档标记而不合并 |
| `target_pollutant` | string/array | 目标污染物/染料；不同染料不得合并 |
| `incomparability_rules` | array[string] | 不可比规则；原样保留 |
| `key_map` | object | 实际使用的原键名 → 语义名 |
| `raw_keys` | array | 可选；未识别的原键名 |

## 3. `documents[]`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `source_document_id` | string | 是 | exact id，与 workorder 一致 |
| `title` | string | 建议 | 文档标题 |
| `type` | enum | 建议 | `research` / `review` / `standard` / `dataset` / `opinion` / `unknown` |
| `read_scope` | array[string] | 建议 | 实际读过的段落/表格/图 |
| `included` | bool | 建议 | 是否纳入比较 |
| `reason` | string | 建议 | 纳入/排除理由 |

## 4. `comparison_table[]` 单元格

每个 row：`{"dimension": "...", "cells": [...]}`。每个 cell 固定七字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `source_document_id` | string | 是 | exact id；不得用 `doc_id` 代替 |
| `paragraph_id` | string/int/null | 是 | `reported` 必填；`not_reported` 为 null；不得伪造 |
| `dimension` | string | 是 | 与所在 row 的 `dimension` 一致 |
| `value` | any/null | 是 | 原值；`not_reported`/`unreadable` 为 null |
| `unit` | string/null | 是 | 单位/量纲；定性字段可 null，但建议写 `reason` 或说明 |
| `condition` | string/null | 是 | 该值成立的条件（剂量/时间/光源/pH/污染物等） |
| `status` | enum | 是 | `reported` / `not_reported` / `incomparable` / `unreadable` |

可选字段：`quote`（逐字原文）、`reason`（不可比/未报告/不可读原因）、`locator`
（`table_id`/`row_index`/`figure_id`/`panel`）、`conversion`（换算）、
`condition_evidence`（条件来自另一段时）。

状态语义：

| status | value | paragraph_id | 必填额外字段 |
|---|---|---|---|
| `reported` | 非空 | 非空 | 建议 `quote` |
| `not_reported` | null | null | `reason`；并进 `synthesis.nonreported[]` |
| `incomparable` | 非空 | 建议非空 | `reason`；并进 `synthesis.noncomparable[]` |
| `unreadable` | null | 可 null | `reason`；不推断内容 |

## 5. `comparability_gate[]`（恰好六项）

| `item` | 判定问题 |
|---|---|
| `task` | 是否回答同一个比较问题/同一目标 |
| `metric` | 指标定义是否同一 |
| `units` | 单位/量纲/基准是否一致或可明示换算 |
| `material` | 材料/相/组成/改性是否同一 |
| `pollutant` | 污染物/染料是否同一 |
| `budget` | 投加量、时间、光强/能量、成本等预算是否同一 |

每个 item：

| 字段 | 类型 | 说明 |
|---|---|---|
| `item` | enum | 上表六项之一 |
| `verdict` | enum | `comparable` / `incomparable` / `unknown` |
| `reason` | string | 必填；不可比/未知必须写清 |
| `affected_documents` | array[string] | `incomparable` 至少 2 个已知 id |
| `evidence` | array | `{source_document_id, paragraph_id}`；`incomparable` 至少 1 条 |

`unknown` 按不可比处理（保守原则），不得默认 comparable。

## 6. `gate_summary`

| 字段 | 类型 | 说明 |
|---|---|---|
| `quantitative_synthesis` | enum | `allowed` / `restricted` / `blocked` |
| `permitted_subset` | array | `restricted` 必须非空；每项 `{dimension, source_document_ids(>=2), reason}` |
| `reason` | string | 必填；说明哪些组可比、哪些不可比 |

- `allowed`：所有相关 gate 项 comparable，且至少 2 篇在同一子集。
- `restricted`：存在 ≥2 篇的可比子集，但全量不可比；只能在该子集内做定量结论。
- `blocked`：没有 ≥2 篇的可比子集；禁止任何跨文档均值/排序/倍数。

## 7. `synthesis`

| 字段 | 类型 | 说明 |
|---|---|---|
| `agreements` | array | 每条 `text` + `support`（≥2 个不同 `source_document_id`） |
| `divergences` | array | 每条 `text` + `sides`（≥2 篇）+ `resolution`（`unresolved`/`explained_by_condition`）+ `reason` |
| `gaps` | array | 每条 `text` + `affected_dimension` + 可选 `support` |
| `noncomparable` | array | 每个被阻断比较：`item`（gate 项或维度）+ `dimension` + `source_document_ids`（≥2）+ `reason` + `evidence` |
| `nonreported` | array | 每个未报告单元格：`source_document_id` + `dimension` + `paragraph_id`(null) + `search_scope` + `reason` |
| `single_document_only` | array | 只由 1 篇支持的结论：`text` + `source_document_id` + `paragraph_id` + `note` |

单篇结论不得写成「领域共识」；`agreements` 中发现单篇支持即为错误，应移入
`single_document_only`。

## 8. 官方 `claims[]`

| 字段 | 类型 | 说明 |
|---|---|---|
| `claim_id` | string | 唯一 id |
| `kind` | enum | `agreement` / `divergence` / `gap` / `noncomparable` / `nonreported` / `single_document` |
| `text` | string | 结论文本；不得引入矩阵外新信息 |
| `evidence` | array | `{source_document_id, paragraph_id}`；absent 类结论可为空 |
| `source_id` | string | 主来源 `source_document_id` |
| `location` | string | 主定位（段落/表/图/检索范围） |

## 9. 命名与格式

- `source_document_id`、`paragraph_id`、`dimension` 原样保留；不要改写、缩写或翻译。
- 数值与单位分开：`value` 是解析值，`unit` 是单位/量纲；原文无单位写 null + reason。
- 百分比（`%`）与百分点（percentage points）分开；区间写 `{min,max}`，不取中点冒充点估计。
- 明示换算写 `conversion: {from, to, factor, source}`；未明示换算不得静默合并。
- 所有 quote 逐字来自原文；表格 quote 用整行原文，不用重新排版后的 CSV 冒充。
