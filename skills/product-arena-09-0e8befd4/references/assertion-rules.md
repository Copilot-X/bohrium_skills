# 原子断言与 Nanopublication 执行规则

本文件是 `work/assertions.json` 的写作与自检规则，与
`templates/assertions.schema.json` 配套。目标不是“多写”，而是每条断言
都能被独立判真假、被一段原文支撑、被 provenance 追溯到论文。

## 1. 抽取顺序

1. 读 `work/paragraphs.json` 的 `paragraphs[]` 与 `metadata`；
2. 按章节顺序扫描摘要、结果、讨论、结论；
3. 标记可判真假的结论句（含方向、比较、数值、条件）；
4. 剔除背景铺垫、方法步骤中不含结论的句子；
5. 对每条候选做“原子性测试”（第 2 节）；
6. 为每条选择同一段内的最小证据（第 4 节）；
7. 填 subject/relation/object、conditions、evidence_type、confidence；
8. 无法绑定证据的候选写入 `unresolved`。

不要把整段摘要塞进一个 `assertion` 字段。一条 assertion 只回答一个
“谁—做了什么—对谁/什么—在什么条件下”。

## 2. 原子性判据

一条合格断言：

- 只有一个 subject、一个 relation、一个 object；
- 是陈述句，能给出真/假，而不是问题、命令或评价；
- 长度 ≤420 字符；
- 不靠 “and/but/while/whereas/although/;” 串联两个独立命题；
- 条件放在 `conditions`，不要堆进主语或从句；
- 保留原文的限定词（may/might/suggest/associated with），不升级为
  prove/causes。

脚本的实际判据（`skillcore.atomicity_issues`）：

| 触发 | 结果 |
|---|---|
| assertion 为空 | `empty assertion` |
| 长度 > 420 字符 | `assertion longer than 420 characters` |
| `;` 或 `and/but/while/whereas/although` 合计出现 ≥2 次 | `multiple clauses; consider splitting` |

注意：单个 `and` 只计 1 次，不会触发；触发的是“多个并列/转折结构”。
这是启发式，不等于语义上一定复合——但任何 warning 都会让
`status` 变成 `partial`，所以宁拆勿合。

拆分示例：

```text
原文：The coated cell retained 88% capacity and the uncoated cell retained 71% after 100 cycles.
拆为：
A001  The coated cell retained 88% capacity after 100 cycles.
A002  The uncoated cell retained 71% capacity after 100 cycles.
```

拆出的每条必须各自有证据；不能一条拆成两条却共用一条只覆盖半句的 quote。

## 3. subject / relation / object

### 3.1 受控关系（35 个，大小写不敏感）

| relation | 用于 |
|---|---|
| `CAUSES` | 有实验/机制依据的因果 |
| `ASSOCIATED_WITH` | 相关，不主张因果 |
| `CORRELATES_WITH` | 统计相关（可含方向） |
| `INHIBITS` / `ACTIVATES` | 抑制/激活 |
| `INCREASES` / `DECREASES` | 提高/降低某指标 |
| `REGULATES` | 调控（上游对下游） |
| `BINDS` | 分子/材料结合 |
| `EXPRESSED_IN` | 基因/蛋白在组织或条件下的表达 |
| `LOCATED_IN` | 空间定位 |
| `IS_A` / `PART_OF` | 分类/组成 |
| `USES` | 方法或系统使用某材料/工具 |
| `MEASURED_BY` | 指标由某方法测量 |
| `PREDICTS` | 预测关系 |
| `SUPPORTS` / `CONTRADICTS` | 证据支持/反驳某命题 |
| `COMPARES_WITH` | 对照比较（不必然优于） |
| `OUTPERFORMS` | 明确优于（需有比较数据） |
| `DERIVED_FROM` | 由某数据/模型推导 |
| `ENHANCES` | 增强某效应/性能（比 INCREASES 更泛化） |
| `REDUCES` | 降低某量（比 DECREASES 更泛化） |
| `MODULATES` | 调节（不指定升高或降低） |
| `INTERACTS_WITH` | 分子/材料/因素间相互作用 |
| `CO_OCCURS_WITH` | 共现，不主张因果 |
| `HAS_PART` | 整体拥有部分（与 PART_OF 反向） |
| `HAS_PROPERTY` | 实体具有某属性 |
| `MEASURED_AS` | 某量以另一量度表示 |
| `EXPRESSES` | 细胞/组织表达某基因或蛋白 |
| `ENCODES` | 基因/序列编码某产物 |
| `TREATS` | 处理/治疗（材料处理或疾病干预，需按上下文） |
| `PREVENTS` | 阻止某事件发生 |
| `INDUCES` | 诱导某表达/过程 |
| `REPRESSES` | 抑制表达（转录层面，区别于 INHIBITS） |

规范化：脚本把非字母数字替换为 `_` 并转大写，因此
`associated with` → `ASSOCIATED_WITH`、`is a` → `IS_A`、
`co-occurs with` → `CO_OCCURS_WITH`（**不在词表，判 problem**）。
不要发明新关系；无法映射时写入 `unresolved`。

### 3.2 方向与类型

- `subject`/`object` 必须是非空名词短语；不要写成整句或代词（“it”）。
- 方向与原文一致：
  - “A 提高 B” → subject=A, relation=INCREASES, object=B；
  - “B 被 A 提高”同样映射为 A→INCREASES→B，不要反过来写。
- 比较关系必须有比较对象：`OUTPERFORMS` 的 object 是被比较的基线。
- `type` 建议值：`material|method|finding|metric|organism|process`；
  脚本不强校验，但必须语义合理且全文一致。
- 相关不写因果：原文 `associated with/consistent with/correlates with`
  不得改成 `CAUSES`；确需因果时必须在 limitations 说明证据边界。

## 4. 证据（最小、逐字、可回查）

每条 evidence：

```json
{"path": "paper.md", "locator": "paper.md P010",
 "quote": "The coated cell retained 88% capacity under the same conditions."}
```

规则：

1. **locator 必须精确命中一段**：使用 `paragraphs[].locator`
   （`paper.md P010`）或 `paragraph_id`（`P010`）。写页码、章节号或
   `P10` 都可能匹配不到。
2. locator 命中时，quote 只在该段文本内校验；因此**一条 quote 不要跨段**。
   需要跨段证据时：拆成两条断言，或选最决定性的一段，其余写入
   `conditions/limitations`。
3. quote 必须是连续原文。空白、大小写、全半角差异会被归一化；
   标点、词序、同义替换不会被容忍。
4. 需要连接同一段内两处原文时，可用 `...` 或 `…` 分隔；每个片段都必须
   逐字存在。例：
   `"The uncoated cell retained 71% capacity after 100 cycles. ... The coated cell retained 88% capacity under the same conditions."`
5. 最小充分：quote 必须同时包含比较对象、方向、关键数值与成立条件；
   不要把整段复制来“保证命中”。
6. 如果 locator 未命中，脚本会退化为全文 quote 检查：可能通过，但
   provenance 的定位粒度不合格。提交前必须修正 locator。
7. 图表证据：quote 表注/图注/表格行中的逐字文本；纯图形且无可引用文字时，
   用 `evidence_type=indirect` 并在 limitations 说明，或放入 `unresolved`。

### evidence_type

| 值 | 含义 |
|---|---|
| `direct` | 原文直接报告该断言 |
| `indirect` | 需要结合上下文/图表/另一句才能成立 |
| `inferred` | 由作者或你推导，不是原文直述 |

### confidence

| 值 | 建议判据 |
|---|---|
| `high` | direct 证据；条件、数值、比较对象齐全；重复实验 |
| `moderate` | direct 但单一实验/条件有限，或 indirect 且上下文充分 |
| `low` | inferred、证据边界不明、仅有相关性 |

confidence 不参与 finalize 的机械判定，但会写入 nanopublication；
不得为了让结果“好看”而虚标。

## 5. source_metadata 与 provenance

- 首选完整复制 `work/paragraphs.json` 的 `metadata`；如需用 case.json
  的值，整块替换，不要只写 `{title}`——模型提供的 `source_metadata`
  会整体覆盖 prepare 的 metadata，缺字段会丢失 DOI/year/version。
- `paper_id` 使用 case_id；`source_path` 使用 `paragraphs.source_path`。
- title/DOI/authors/year/version 只用输入中真实出现的值；没有就留空。
  DOI 与 title 同时缺失时 finalize 会记录 `source_metadata` uncertainty，
  但不会因此降级——仍须在交付说明中承认来源偏弱。
- `provenance.path/locator/quote` 来自该断言的 evidence，必须一致。

## 6. 去重与标识

- 同一 subject+relation+object+conditions：只保留一条，合并证据。
- 同一发现的不同表述：选最贴近原文、信息最全的一条。
- `id` 用 `A001…`，唯一；重复 id 会判 problem。
- 不要自造 `nanopub_id`；入口按
  `sha256(case_id + id + assertion)[:24]` 确定性生成，模型无法预设。
- `unresolved` 只出现在 `output/assertions.json`，不会进入
  `nanopublications.json`；不要把未解决候选混进 assertions。

## 7. 运行与审计顺序

1. `prepare` 后核对 `paragraphs.json` 的段数与章节覆盖；
2. 写 `work/assertions.json`；
3. 自查：每条单命题、关系在词表、locator 命中、quote 逐字；
4. 跑 `finalize`；
5. 读 `output/quality-report.json`：
   - `problems=[]` 且 `atomicity_warnings=[]` → `answered`；
   - 任一非空 → `partial`，按 `result.json` 的 `uncertainty[].action` 修复；
6. 抽样把 `assertions.csv` 的 quote 放回原文复核。

## 8. 失败模式与处置

| 失败 | 症状 | 处置 | 状态影响 |
|---|---|---|---|
| 整段摘要当断言 | 断言超长/多命题 | 按 subject-relation-object 拆分 | warning → `partial` |
| 关系词不在词表 | `relation 'X' not in controlled vocabulary` | 映射到允许关系或 unresolved | `partial` |
| subject/object 缺 label | `subject label missing` | 补名词短语 | `partial` |
| quote 被改写 | `part not found: ...` | 从指定段落复制原文 | `partial` |
| locator 写错 | 全文回退检查（不报错）或 quote 不匹配 | 改用 `Pxxx`/`paper.md Pxxx` | 可能 `partial`，provenance 弱 |
| 跨段 quote | 段内校验失败 | 拆断言或改用单段证据 | `partial` |
| id 重复 | `duplicate assertion id` | 重新编号 | `partial` |
| 断言为空 | `empty assertion` | 删除或补全 | `partial` |
| assertions 缺失 | `assertions.json missing or empty` | 按 schema 重写 | `partial`，无交付物 |
| 编造 DOI/年份 | provenance 与输入不符 | 恢复空串或真实值 | 人工审查风险 |
| 无主论文 | prepare `input_error` | 说明输入限制，停止 | `input_error` |

## 9. 最小提交前检查清单

- [ ] `case_id` 与 `/input/case.json` 一致；
- [ ] 每条断言单命题、≤420 字符、无多并列结构；
- [ ] 所有 relation 在 35 个受控值中，方向与原文一致；
- [ ] 每条 evidence 的 path/locator 指向同一真实段落，quote 逐字；
- [ ] subject/object 均有 label 与合理 type；
- [ ] 无重复 id，无重复语义断言；
- [ ] `source_metadata` 完整或整块留空回退；
- [ ] 5 个输出存在；`quality-report.json` 的 problems 与 warnings 为空；
- [ ] `result.json` 可解析，`deliverable.path=nanopublications.json`，
      `status` 属于 reading 枚举，`artifact_paths_valid=true`。
