# 文献证据问答参考（逐题、原子论断、逐字引文）

配合 `templates/evidence-qa.json` 和 `scripts/check_evidence_qa.py` 使用。
**task.md 与 case.json 优先**；材料没有的答案不要补，找不到证据就明确拒答/部分回答。

## 1. 题型与答法

| 题型 | 示例 | 答法 |
|---|---|---|
| fact 事实 | “转化率是多少？” | 直接给值 + 条件 + quote |
| condition 条件 | “在什么条件下成立？” | 列条件，逐条挂证据 |
| compare 比较 | “A 与 B 谁更高？” | 分别给值/方向，避免跨条件比较 |
| causal 因果 | “为什么选择性提高？” | 只写原文支持的原因；推断标 inferred |
| inference 推断 | “这说明什么？” | 明确前提、推断链、置信度 |
| gap 缺口 | “是否报告误差？” | 没有就写“材料未报告”，不猜 |
| calculation 计算 | “比值是多少？” | 公式 + 输入值 + 结果 + 单位 |

## 2. 检索与定位

1. 建段落索引：`paragraph_id / 首句 / 数字 / 实体 / 章节`。
2. 关键词 + 数值 + 实体召回候选段落；回读原文确认，不凭索引摘要作答。
3. 每个结论必须能指回至少一个 `paragraph_id`；跨段结论挂多条 evidence。
4. 找不到：记录检索过的范围，`status=insufficient_evidence`，不要用常识补。

## 3. 原子论断

一条 claim 只表达一个事实/判断：

- “转化率 92% 且选择性 91%” → 拆成两条 claim。
- 比较句拆成“A 的值”“B 的值”“比较关系”。
- 因果句拆成“相关性证据”“机制证据”“推断结论”。

每条 claim 记：

```json
{"claim_id":"q1-c1","text":"350 °C 下转化率为 92%",
 "kind":"reported|derived|inferred","evidence":[{"paragraph_id":"p6","quote":"..."}],
 "formula":"仅 derived","rationale":"仅 inferred","confidence":0.9,
 "claim_strength":"fact|inference|opinion"}
```

`inferred` 必须有 `rationale`；`derived` 必须有 `formula`；`reported` 不得夹带解释。

## 4. 引文规则

- quote 逐字复制原文，不翻译、不改写、不补省略内容。
- 长度以能独立支撑论断为准，通常 8–40 词；跨段时分别摘录。
- 引文中的数字、单位、条件必须与原文一致；范围不要缩成单点。
- `answer` 中的每个数字都应能在同题某条 quote 中找到；否则补 quote 或删除数字。

## 5. 状态与拒答

| 状态 | 条件 |
|---|---|
| `answered` | 材料直接支持完整回答 |
| `partial` | 只支持一部分，未答部分写入 `unanswered_parts` |
| `insufficient_evidence` | 检索不到/材料未报告 |
| `input_error` | 题目/材料结构错误 |

拒答不是失败：明确写“材料未报告 X；已检索 §2–§4”，比编造更有价值。

## 6. 冲突与失真

- 同一问题出现相反证据：两条都保留，`conflict: {"detected": true, "resolution": "unresolved"}`。
- 改写失真检查：数字、范围（10–20% → 约 20%）、条件（剂量/温度/样本）是否被改动；
  写入 `distortion_check`。
- 二手引用标 `secondary_source` 并降低置信度；无法回溯的来源标 `unverifiable`。
- 偏差提示只描述可观察现象（只引支持证据、样本单一），不推测作者动机。

## 7. 覆盖与不确定性

`coverage` 记录：

- `documents_read` / `sections_read`；
- `sections_skipped` + 原因；
- `notes`（哪些题型答得多、哪些不足）。

`uncertainty` 每条写 `item / reason / impact`；不要只写“存在不确定性”。

## 8. 常见失败模式

| 信号 | 问题 | 修复 |
|---|---|---|
| 有答案无引文 | claim.evidence 为空 | 回原文定位；找不到降级 |
| 引文是转述 | quote 与原文措辞不同 | 逐字摘录 |
| 漏题 | answers 少于 questions | 逐题补齐 |
| 过度回答 | 引入材料没有的解释 | 删除或改 inferred |
| 冲突被抹平 | 只留支持结论 | 恢复反例并标 conflict |
| 数字无出处 | answer 数字不在 quote | 补 quote 或删数字 |
| 把部分当完整 | status=answered 但少答 | 改 partial + unanswered_parts |

## 9. 交付前检查

- [ ] 每个 `question_id` 都有 answer 条目。
- [ ] 每条 claim 有 `paragraph_id` + 逐字 quote。
- [ ] `inferred` 有 rationale，`derived` 有 formula。
- [ ] answer 中数字能在同题 quote 找到。
- [ ] 冲突、未答部分、覆盖范围、不确定性均已记录。
- [ ] `check_evidence_qa.py` 返回 `ok: true`。
