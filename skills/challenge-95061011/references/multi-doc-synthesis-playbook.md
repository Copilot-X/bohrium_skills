# 多文档比较与整合 Playbook（workorder-first）

配合 `templates/comparison-matrix.json` 和 `scripts/check_synthesis.py` 使用。
**task.md、`/input/comparison-workorder.json` 与实际 corpus 优先**；综合结论不得引入任何单篇文档都没有的信息。

## 0. 方法依据（只吸收方法，不复制文件）

- **PRISMA 式可追溯**：记录纳入/排除理由、读取范围、段落定位；未读、未报告、不可读分开登记；
  后续加入新论文时按同一字段表增量更新，不重做全表。
- **SWiM 式可比性分组**：综合前先按 `task/metric/units/material/pollutant/budget` 分组；
  不可比组不合并、不做汇总效应量或排名；明确报告分组规则和不可比原因。
- **可比性矩阵**：先固定共同字段和判定规则，再逐格填值；保留
  `noncomparable`/`nonreported`/`single_document_only`，使他人能按记录抽样复核。

## 1. Workorder-first：先抄口径，再读材料

在打开任何论文之前，先读 `/input/comparison-workorder.json`，把以下内容逐字写进 `result.workorder`：

| 要抄的内容 | 用途 | 注意 |
|---|---|---|
| `source_document_ids[]` | 矩阵行/单元格的文档键 | 保留大小写、连字符、版本号；不得缩写或改写 |
| `paragraph_id_scheme` / `paragraph_map` | 唯一的段落定位口径 | 不得用自造 id 冒充 |
| `comparison_dimensions[]` | 矩阵行名 | 维度名逐字保留；后续不得边读边改 |
| `target_material` | 目标材料范围 | 范围外材料标记而不合并 |
| `target_pollutant` | 目标污染物/染料范围 | 不同染料不得合并 |
| `incomparability_rules[]` | 不可比规则 | 原样保留；与默认规则冲突时以 workorder 为准 |

字段名别名：`source_documents/documents/document_ids/corpus`、`dimensions/fields`、
`target_materials/material`、`target_pollutants/pollutant/dye`、
`comparability_rules/rules/constraints/notes`。把原键名写入 `workorder.key_map`。

workorder 缺失时：回退 `case.json` + task.md，`workorder.loaded=false`，
`uncertainty` 记 `workorder_missing`；继续交付，不编造 id/维度。

## 2. 文档盘点

每篇记：

```json
{"source_document_id": "DOC-A", "title": "…", "type": "research|review|standard|dataset",
 "read_scope": ["p1", "p6"], "included": true, "reason": "…"}
```

- 逐篇确认 workorder id 与实际文件对应；找不到的 id 标 `included=false` 并写原因。
- 未读部分写进 `read_scope` 或 `not_read`，不要假装读过。
- 综述类文档不能当原始证据；引用综述结论时标 `source_type: "review"`。
- 多来源去重：先 DOI 精确匹配，再标题相似度兜底；保留信息更完整的记录，写 `dedup_log`。
- **少于 2 篇可读文档不构成多文档比较**：`status=insufficient_evidence`，
  `multi_document.satisfied=false`，只输出单篇事实与缺口。

## 3. 矩阵单元格：七字段 + 四状态

```json
{"source_document_id": "DOC-A", "paragraph_id": "p5",
 "dimension": "degradation_efficiency", "value": 92, "unit": "%",
 "condition": "0.5 g/L, 60 min, 365 nm, methylene blue", "status": "reported"}
```

| 状态 | 含义 | 必填 | 禁止 |
|---|---|---|---|
| `reported` | 原文报告 | `value` + `paragraph_id` + `unit`/`condition`（可 null 但要有原因） | 无定位的数值 |
| `not_reported` | 原文没有 | `value=null`、`paragraph_id=null`、`reason` | 填 0、均值或猜测值 |
| `incomparable` | 有值但不能进入同一比较 | `value/unit/condition` + `reason` | 静默换算后合并 |
| `unreadable` | 材料存在但读不出 | `reason` | 推断内容 |

规则：

- 每个维度覆盖全部文档；缺单元格就是漏项，不是「不适用」。
- 数值、单位、条件必须同源；条件来自另一段时用 `condition_evidence` 单独挂定位。
- 原文无单位：`unit=null` + `reason`，不按领域常识补 `%`/`°C`/`h^-1`。
- 表格用 `table_id + row_index`，图用 `figure_id + panel`，作为 `locator` 补充。
- 单元格 `dimension` 必须与所在行的 `dimension` 一致。

## 4. 六项可比性门（定量综合前必须完成）

| item | 判定问题 | comparable | incomparable / unknown 信号 |
|---|---|---|---|
| `task` | 同一比较问题/目标？ | 研究问题、目标一致，或 workorder 限定子集 | 机理 vs 性能；综述 vs 原始研究 |
| `metric` | 指标定义同一？ | 都是降解效率/速率常数/去除率，定义一致 | 降解效率 vs 速率常数 vs 吸附容量 vs TOC |
| `units` | 单位/量纲/基准一致或可换算？ | 同单位，或有明示换算因子和基准 | `%` vs `fraction` vs `mg/L`；不同波长/干湿重基准 |
| `material` | 材料/相/组成/改性同一？ | 同一材料、同一相、同一改性水平 | 纯 TiO2 vs 复合材料；不同掺杂量 |
| `pollutant` | 污染物/染料同一？ | 同一污染物（如都是 MB） | MB vs RhB vs phenol；不同染料不得合并 |
| `budget` | 投加量/时间/光强/成本同一？ | 剂量、时间、光源、成本口径一致 | 0.5 vs 1.0 g/L；60 vs 120 min；不同灯功率 |

输出形状：

```json
{"item": "units", "verdict": "incomparable",
 "reason": "DOC-A 用 %，DOC-B 用 fraction of initial absorbance，未给换算",
 "affected_documents": ["DOC-A", "DOC-B"],
 "evidence": [{"source_document_id": "DOC-B", "paragraph_id": "p5"}]}
```

判定规则：

1. `unknown` 按不可比处理（保守原则），写清缺哪份证据；不得默认 comparable。
2. `incomparable` 必须有 `reason`、`affected_documents`、`evidence`；不能只写一句「不一致」。
3. 可以逐对判定：同一 item 允许「A/B comparable，C incomparable」，写进 `affected_documents`。
4. 只有与当前定量结论相关的项全部 comparable，才允许对该子集做定量合并。
5. 明示换算不消除其他差异：仍要检查 metric/material/pollutant/budget；
   换算写 `conversion: {from, to, factor, source}`。
6. 严禁把不同数据集、不同染料/污染物、不同材料、不同单位或不同预算的数值直接合并。

`gate_summary`：

- `allowed`：所有相关项 comparable，且 ≥2 篇同子集；
- `restricted`：存在 ≥2 篇的可比子集，但全量不可比；`permitted_subset[]` 非空；
- `blocked`：没有任何 ≥2 篇的可比子集；禁止跨文档均值/排序/倍数。

## 5. 综合：多篇、单篇、未报告、不可比分开写

- `agreements[]`：每条 `support[]` 至少 2 个不同 `source_document_id`；只支持方向一致，
  不要求数值相同；数值相同也要在可比子集内。
- `divergences[]`：双方并列，`resolution ∈ {unresolved, explained_by_condition}`；
  能由条件解释的写清是 sample、method、condition 还是 metric。
- `gaps[]`：理论/方法/数据/应用 gap，必须由矩阵空白或冲突支撑。
- `noncomparable[]`：每个被阻断的比较一条：

```json
{"item": "pollutant", "dimension": "degradation_efficiency",
 "source_document_ids": ["DOC-A", "DOC-B", "DOC-C"],
 "reason": "DOC-A/DOC-B 为 MB，DOC-C 为 RhB；不同染料不得合并",
 "evidence": [{"source_document_id": "DOC-C", "paragraph_id": "p1"}]}
```

- `nonreported[]`：每个 `not_reported` 单元格一条：

```json
{"source_document_id": "DOC-C", "dimension": "band_gap",
 "paragraph_id": null, "search_scope": "p1-p6", "reason": "DOC-C 未报告带隙"}
```

- `single_document_only[]`：只由 1 篇支持的结论全部放这里：

```json
{"text": "DOC-A 报告 {001} 晶面暴露度 42%，并联系到 MB 降解增强",
 "source_document_id": "DOC-A", "paragraph_id": "p5",
 "note": "仅单篇支持，不作为领域共识"}
```

- 单篇结论**不得**写成「领域共识」「普遍认为」「已有研究表明」；只能写「DOC-X 报告了……」。
- 不搞「投票式多数决」：两篇对一篇不等于前者正确。
- 相同条件下数值不一致 → `conflicts[]` 保留双方，不平均、不静默择一。

## 6. 常见失败模式

| 信号 | 问题 | 修复 |
|---|---|---|
| 没读 workorder 就建表 | 维度/id 与任务不一致 | 回第 1 步逐字抄 source id/维度/规则 |
| 单元格用 `doc_id` | 与 exact `source_document_id` 不符 | 统一改 `source_document_id`，逐字保留 |
| 单位不同直接平均 | 出现「平均降解率」 | 改 `units=incomparable`，禁止合并 |
| MB 与 RhB 合并 | 「不同染料总体」 | `pollutant=incomparable`，分开报告 |
| 0.5 与 1.0 g/L 合并 | 忽略投加量差异 | `budget=incomparable`，写 `permitted_subset` |
| 单篇结论写成共识 | 只有 1 个 support | 移到 `single_document_only[]` |
| 未报告填 0 | 矩阵出现无出处数值 | `not_reported` + `nonreported[]` |
| 只写矩阵不写综合 | 没有 agreements/divergences | 至少 1 条 ≥2 篇 agreement，或说明无法综合 |
| 只写综合不写矩阵 | 结论无法逐格回查 | 先补矩阵，再写综合 |
| 只有 1 篇文档仍标 answered | 不满足多文档比较 | `insufficient_evidence` + `multi_document.satisfied=false` |

## 7. 交付前检查

- [ ] workorder 的 source ids、段落规则、维度、目标材料/污染物、不可比规则已抄录。
- [ ] 每个维度覆盖全部文档；单元格七字段齐全，exact `source_document_id/paragraph_id`。
- [ ] 六项 gate 齐全；`incomparable/unknown` 有 reason + affected_documents + evidence。
- [ ] `gate_summary` 与 `noncomparable[]` 一致；没有跨组定量合并。
- [ ] `agreements` 每条 ≥2 篇；单篇结论只在 `single_document_only[]`。
- [ ] `nonreported[]` 覆盖全部 `not_reported`。
- [ ] 官方别名 `status/result/claims/uncertainties` 齐全，JSON 可解析。
- [ ] `check_synthesis.py` 通过；失败项已手工核对并写入 `uncertainty`。
