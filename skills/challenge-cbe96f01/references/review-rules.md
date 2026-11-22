# 引用与事实核验：执行规则

## 1. 任务口径

- 目标：引用与事实核验、论断-来源匹配、编造/错配检出。
- 主产物：`output/review-report.md`。
- 必填字段：`claim_source_matrix`, `source_status`, `findings`, `evidence`,
  `checks`, `verdict`。
- 工作顺序：先运行 `scripts/verify_citations.py`，再逐条人工复核，最后写
  `work/review-findings.json` 并 finalize。脚本候选不是最终结论。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 定位串要能回查：文本用行号/段落号，PDF 用页码，表格用表号+行，数据集用条目 id；
4. 找不到原文时写入 `uncertainties`，不要用常识补全；
5. 同一材料的不同版本/格式不构成独立证据；
6. 外部未提供的文献、DOI、数值、设备状态不得编造；
7. 撤稿/预印本/版本差异必须引用输入中的通知原文，不能凭印象判断。

## 3. 四类问题

### 3.1 `missing_reference`

- 定义：正文有引用标记，但参考文献表、`.bib`/`.ris` 或 JSON/CSV 文献数据中
  没有可回查的对应条目；或条目只是“未提供/未列入/opinion piece”之类占位说明。
- 最小证据：正文引用所在句 + 文献表中缺失位置或占位条目原文。
- 严重度：major。若该引用支撑核心结论，建议按 critical 处理。
- 修复：补全真实书目信息；无法补全则删除引用并重写结论，不能保留“看似有引用”。

### 3.2 `uncited_claim`

- 定义：包含可核验数值、统计量或结果性表述的断言，没有任何文内引用。
- 典型信号：百分比、样本量、效应量、`found/reported/showed/improved/persisted`
  等结果动词。
- 最小证据：断言原句 + 同段上下文；若方法/限制说明中已有依据，应排除。
- 严重度：minor；含统计量或用于核心结论时 major。
- 修复：补引用；无法确认来源时改为限定性表述或列入待复核。

### 3.3 `citation_mismatch`

引用存在，但来源不支持该断言。至少覆盖以下子类：

| 子类 | 判定 | 证据 | 默认严重度 |
|---|---|---|---|
| `numeric_mismatch` | 正文数值在来源可核验文本中找不到或方向相反 | 正文数值 + 来源数值 | major |
| `year_mismatch` | 文内年份与条目年份不一致 | 引用串 + 条目书目行 | major |
| `overclaim_or_negation` | 来源只说关联/局限，正文用因果、绝对化或反向措辞 | 断言 + 来源限制性原文 | major |
| `retracted_source` | 来源被输入中的撤稿通知标记为撤稿 | 引用串 + 撤稿通知原文 | critical |
| `opinion_as_evidence` | 评论/观点/社论被当作原始实证证据 | 引用串 + 来源类型 | major |

- 数值比较要排除 DOI、年份、卷期、页码等书目噪声；只在来源可核验文本
  （摘要、引文片段、数据条目、通知）内比较。
- 来源只有书目信息、没有可核验文本时，不要判 `numeric_mismatch`；
  应判 `unverifiable` 并写不确定性。
- 来源结论与正文方向相反（如来源说“不支持持续获益”、正文说“持续五年”）
  属于 mismatch，不是简单措辞问题。

### 3.4 `quote_mismatch`

- 定义：正文引号内文字无法在匹配来源的可核验文本中逐字找到。
- 最小证据：带引号的断言 + 来源文本（或“来源无可核验文本”的说明）。
- 严重度：major。
- 修复：回到来源逐字重摘；不能逐字核实时删除引号、改为转述或移入待复核。
- 只有书目信息时不得判 mismatch；标 `unverifiable`。

## 4. verdict 四档

| verdict | 含义 | 使用条件 |
|---|---|---|
| `full` | 完整支持 | 来源结论、范围、方向与数值一致；未发现反向或超范围信号 |
| `partial` | 部分支持 | 主题相关，但有过度推断、范围扩大或部分数值不吻合 |
| `none` | 不支持 | 来源反向、数值矛盾、来源撤稿、引用不可回查 |
| `unverifiable` | 无法判定 | 材料不足、仅有书目信息、定位失败、来源歧义 |

“引用存在”只说明可回查，不等于支持结论；不得把 `missing_reference` 之外的
“条目存在”直接写成 `full`。

## 5. 来源状态

`source_status[]` 的 `status` 只用四个枚举：

- `published`：条目含期刊/会议/出版社与年份或 DOI；仍须声明“离线仅核书目信息”；
- `preprint`：输入材料或条目标记为预印本；
- `retracted`：输入中有匹配的撤稿通知（作者+年份、DOI 或标题特征匹配）；
- `unknown`：条目信息不足、无法匹配通知或只有占位说明。

规则：

1. `unknown` 必须 `needs_recheck=true`；
2. `retracted`/`preprint`/存在版本差异时 `needs_recheck=true`；
3. `version_note` 记录预印本与正式版的数值/结论差异，并注明涉及数值；
4. `evidence` 必须是撤稿通知、版本说明或条目书目行原文；
5. 不联网时不得声称“已确认未撤稿”，只能写“在输入材料范围内未发现撤稿通知”。

## 6. 数值一致性

数值型 claim 必须尽量填 `manuscript_value` 与 `source_value`：

- 比较前剔除 DOI/URL、年份、卷期、页码；
- 百分比、样本量、效应量分别比较，不能拿 62% 与 26% 的“主题相同”掩盖数值矛盾；
- 来源同时出现多个数值时，正文数值只需命中其一才算数值层面一致；
- 数值一致不等于语义支持，仍需判断结论方向与范围。

## 7. 产物与复核记录

- `work/review-findings.json` 是唯一工作文件；
- `output/review-report.md` 必须逐条列出 finding id、位置、原文、判定、
  严重度、修改建议，并写覆盖范围、未检查项、限制；
- `output/findings.json`、`output/evidence-map.json` 与工作文件保持一致；
- `output/citation-check.json`、`output/citation-precheck.md` 是脚本原始预检，
  不得篡改；人工补充写到 `output/manual-review.md`；
- `reviewed_candidates[]`：记录接受/拒绝的候选与理由；
- `rejected_candidates[]`：记录假阳性类型（数值在来源其他段落、引用仅作背景、
  来源为综述、定位错误等）；
- `checks[]`：`pass/fail/not_checked`，未检查不等于通过。

## 8. 人工复核清单

1. 打开每条候选 evidence，确认 quote 逐字可查；
2. 阅读来源可核验文本，独立判定 verdict；
3. 检查数值是否在来源其他段落出现；
4. 检查引用是否只作背景、是否把综述/评论当原始证据；
5. 检查引用年份/版本是否与条目一致；
6. 检查撤稿/预印本状态及其对结论的影响；
7. 补充脚本漏掉的语义超范围、偷换结论、循环论证；
8. 把假阳性写入 `rejected_candidates`，把不确定项写入 `uncertainties`；
9. 只有确认过的候选才写入 `findings`，`status` 用 `confirmed` 或
   `needs_recheck`，不要保留 `candidate`。

## 9. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 把引用存在当作支持结论 | 误判为通过 | 补来源可核验文本并重判 verdict |
| 只给结论不给证据 | 无法复核 | 补 path/locator/quote |
| 用常识补全缺失文献 | 编造风险 | 删除或写入 uncertainties |
| 数值比较混入 DOI/页码 | 假阴性 | 先剔书目噪声再比较 |
| 把未找到当不存在 | 遗漏被掩盖 | 单列未命中与检索范围 |
| 静默改数据/引文 | 结果不可复现 | 保留原始快照与变更日志 |
| 报告与 JSON 字段不一致 | 无法自动核验 | 以工作文件为准同步三份产物 |

## 10. 本题指标与字段

- 问题：`location/check_item/original_quote/issue/severity/type/subtype/facet/
  fix/status/evidence`；
- 检查项：`checks[].item/status(pass|fail|not_checked)/detail`；
- 矩阵：`claim_id/claim/claim_locator/claim_section/source/source_locator/
  source_statement/source_design/verdict/type/subtype/reason/manuscript_value/
  source_value/evidence`；
- 来源状态：`source/status/version_note/needs_recheck/status_basis/evidence`；
- 覆盖：`facets_checked[]`（六个官方面）与 `citation_facets[]`（八个引用子面）；
- 判断：区分事实错误、证据不足与改进建议；给出可执行修复；
- 边界：不替代机构审查；材料不足写 `uncertainties` 与人工复核点。

## 11. 全文分段与章节覆盖

`verify_citations.py` 先按标题识别 abstract/introduction/methods/results/
discussion/conclusion/limitations/references，并输出：

- `fulltext.sections`：全文识别到的章节；
- `fulltext.documents[]`：每份材料的章节、段落、句子与 citation claim 数；
- `fulltext.claims_by_section` / `citations_by_section`：各章节的引用负担；
- `citation_facets[].facet=section_coverage`：是否缺摘要/方法/结果段。

规则：

1. 章节缺失时，相关 facet 只能 `partial/not_checked`，不能写 `checked/pass`；
2. 没有标题的连续正文，locator 仍用行号；`section_coverage` 标 `partial`；
3. 同一引用出现在摘要、方法、结果、讨论时，要分别建矩阵行或说明是同一
   论断的重复引用；不要只审摘要；
4. 超长全文按章节分块；报告中写清覆盖范围。

## 12. 具体引用子面（citation_facets）

| 子面 | 通过标准 | 失败/降级条件 |
|---|---|---|
| `citation_resolvable` | 每个文内引用都能映射到条目/键/作者-年份 | 无条目、占位条目、键无法解析 |
| `quote_verbatim` | 引号内文字在来源可核验文本中逐字可查 | 找不到、只在其他段落出现、来源无文本（`not_checked`） |
| `numeric_consistency` | 正文数值命中来源数值集合，方向一致 | 数值缺失、方向相反、样本量/百分比不符 |
| `source_status` | published/preprint/retracted/unknown 合法，通知有原文 | 撤稿仍作依据、版本差异未说明、unknown 未标 needs_recheck |
| `design_scope_match` | 来源设计与论断强度匹配 | 横断面/定性/评论被用来支持干预/因果结论 |
| `uncited_results` | 含数值/结果动词的断言都有引用 | 关键结果无引用；方法/限制说明可排除 |
| `reference_metadata` | 条目含年份与期刊/会议/DOI | 缺年份、缺出处、占位说明 |
| `section_coverage` | 摘要/方法/结果等关键章节可识别并核对 | 缺章节、只有摘要、扫描件无文本 |

`facets_checked[]` 的六个官方面与这些子面不是一一对应：例如
`citation_support` 汇总可回查、语义支持、逐字、设计、未引用与来源状态；
`fact_consistency`/`statistical_reporting` 关注数值；`method_design` 关注
来源设计与论断强度；`academic_compliance` 关注撤稿/版本/元数据。

## 13. 误报抑制与“不强判缺陷”

1. 引用存在不等于支持结论；但也不能因为“来源是综述/评论”就一律判错，
   要看它在句中是背景、定义、方法依据还是结果证据。
2. 数值在来源其他段落/表格出现时，先扩大核对范围，再决定是否保留候选。
3. 来源只有书目信息时，`verdict=unverifiable`，不要用“未找到”推出“编造”。
4. 引用只作背景铺垫时，不应按结果证据要求它支持强因果结论；可降为
   `suggestion` 或删除候选。
5. 撤稿/版本差异必须引用输入中的通知原文；没有通知就写“在输入范围内
   未发现”，不要声称已在线确认。
6. 同一引用支撑多个不同论断时分别建行；不要把多个论断合并成一个
   “引用错误”。

## 14. 预检候选处置

1. 运行 `verify_citations.py --merge` 后，导入的候选一律
   `status=needs_recheck`，编号 `A01...`；
2. 逐条回原文确认 quote、来源文本与数值；确认的改 `confirmed`，不成立的
   写入 `rejected_candidates` 并说明原因；
3. 脚本未覆盖的语义问题（断章取义、偷换结论、循环论证、间接引用当直接
   证据）由模型补入 `findings`；
4. 低置信度候选可留在 `uncertainties`，不要为凑数保留；
5. 预检原始文件 `output/citation-check.json`、`output/citation-precheck.md`
   不得篡改；人工补充写到 `output/manual-review.md`。
