# 论文逻辑与论证审查：执行规则

本文件是 `SKILL.md` 的可执行补充，字段在 `templates/work.schema.json`、
`scripts/logic_review.py` 的输出和 `scripts/fam_tracks.py` 的 review 检查中
都有对应项。

## 1. 任务口径

- 目标：对给定**全文**做一轮可交付的论证审查，交付主张清单、跨章节核对
  与逐条问题，而不是整篇评语。
- 主产物：`output/review-report.md`。
- 必填字段：`claim_inventory`, `cross_section_checks`, `findings`,
  `facets_checked`, `review_status`, `evidence`。
- 关键原则：**先分段、再抽取；先定位、再判断；先排除误报、再定严重度。**

## 2. 全文分段与 locator

1. 必须处理实际全文，不是题面短摘录。章节至少识别 abstract/introduction/
   methods/results/discussion/conclusion/references；缺失章节要写
   `partial/not_checked`。
2. 段落 locator 用源文本的 `P1`/`P2` 标记；没有显式标记时用
   `P12 (line 34)` 或 `U1 (line 3)`，保证 quote 可回查。
3. 同一段落内多个句子用 `P1#S2` 或在 finding 中直接给逐字句子；不要只给
   章节名。
4. 跨章节检查必须同时给承诺位置和交付位置；只有一处不算完成。
5. 长文按章节分块读取；报告中写清覆盖的章节范围与未覆盖部分。

## 3. 主张清单（claim_inventory）

字段：`id/claim/type/section/speaker/support/strength/locator/evidence/
support_basis`。

### 3.1 speaker

| 值 | 判定 | 处理 |
|---|---|---|
| `author` | 作者自己的研究问题、结果、结论 | 可以成为 finding 的对象 |
| `cited` | 转述他人研究/文献观点（`previous work [1]`、`X et al. reported`、引号内他人观点） | 只能按引用支持规则处理，不能把被引观点当作者缺陷 |
| `assumption` | 作者明确标注的假设、hypothetical、`if we assume` | 不判为错误；最多提示“假设未检验” |

### 3.2 type / strength

- `type`：`causal/mediation/association/generalization/proof/result/
  prediction/definition/assumption/background`。
- `strength`：`descriptive/associative/suggestive/causal/absolute/assumption`。
- `support`：`full/partial/none/unverifiable`。`none` 只表示“当前材料没有
  找到能支撑该强度的证据”，不等于作者一定错误；还要看设计、结果和作者
  已声明的局限。

## 4. 跨章节承诺-交付（cross_section_checks）

字段：`promise/promise_locator/delivery/delivery_locator/consistency/note/
evidence`。

`consistency` 枚举：

| 值 | 判定 | 例子 |
|---|---|---|
| `delivered` | 承诺的分析/设计在方法或结果中真实交付 | 引言称做中介分析，方法/结果报告路径系数 |
| `partial` | 部分交付或只交付了替代分析 | 承诺纵向，结果只有横断面 |
| `not_delivered` | 找不到对应交付 | 承诺机制分析，全文无中介/机制结果 |
| `contradicted` | 方法与承诺直接矛盾 | 承诺随机试验，方法写横断面/未实施干预 |
| `unverifiable` | 材料不足，无法判断 | 结果段缺失或无法解析 |

必须核对：摘要/引言承诺 → 方法与结果；方法设计 → 结果口径；
摘要结论 → 正文结果；讨论推断 → 方法设计。

## 5. 逐条问题（findings）

最小字段：`id/facet/subfacet/locator/original_quote/severity/type/status/
impact/fix/false_positive_check/evidence`。

### 5.1 facet / subfacet

六个官方 facet：`fact_consistency`, `citation_support`, `method_design`,
`statistical_reporting`, `argument_chain`, `academic_compliance`。
逻辑专项 subfacet：`claim_extraction`, `evidence_support`, `reasoning_chain`,
`causal_inference`, `cross_section_consistency`, `severity_and_fix`。

### 5.2 type

| type | 定义 | 例子 |
|---|---|---|
| `error` | 两处原文可直接对照的错误或矛盾 | 称随机分配，方法写交替分配 |
| `insufficient_evidence` | 材料未报告/无法判断，需要补证据 | 声称中介但没有中介分析 |
| `norm_violation` | 规范问题 | 仅当输入显式涉及伦理/声明时 |
| `suggestion` | 可改进但不构成错误 | 措辞可更限定、补充敏感性分析 |

不要把“未报告”写成 `error`；不要把 `suggestion` 混进必须修改项。

### 5.3 status

- `confirmed`：两处逐字原文直接矛盾，且不依赖外部知识（例如随机 vs 交替、
  摘要 p 值与结果段同一分析矛盾）。
- `needs_recheck`：需要语义/学科判断的候选（因果 vs 相关、外推、引用强度）。
- 脚本候选的 `candidate` 不得原样进入最终 findings。

### 5.4 severity

| severity | 判据 |
|---|---|
| `critical` | 改变主结论、涉及学术不端或安全性 |
| `major` | 影响可信度、可复现性、因果解释或引用有效性 |
| `minor` | 局部表述/元数据，不影响主结论 |
| `info` | 建议性改进 |

严重度要与 `impact` 一致；不要一律标 major。

### 5.5 fix

必须写“改哪句/哪段 + 改成什么口径 + 补什么证据”，例如：
“把 P11 的 causes 改为 associated with；若保留因果结论，在方法补充随机化、
时间顺序与混杂控制，并在结果报告对应效应量。”

## 6. 规则表（logic_review.py）

| rule_id | facet/subfacet | 触发 | 默认状态 |
|---|---|---|---|
| `mediation_without_analysis` | argument_chain/reasoning_chain | 作者声称中介/机制，但方法明确未做中介分析 | confirmed（方法原文明确）/needs_recheck |
| `causal_from_observational_design` | method_design/causal_inference | 因果/证明措辞 + 横断面/观察性/非随机设计 | needs_recheck |
| `proof_language_outruns_evidence` | argument_chain/evidence_support | prove/establish/demonstrate + 证据只支持部分 | needs_recheck |
| `generalization_overreach` | argument_chain/evidence_support | all/everyone/generalize + 有限样本 | needs_recheck |
| `circular_definition` | argument_chain/reasoning_chain | 定义与测量共用同一组词 | confirmed（重叠≥2）/needs_recheck |
| `unsupported_result_claim` | method_design/evidence_support | 结果报告干预/改善，方法无干预/对照 | needs_recheck |
| `randomization_inconsistency` | method_design/method_result_consistency | 称随机，方法写交替/按楼层 | confirmed |
| `cited_source_misattributed` | citation_support/citation_support | 转述观点用因果/证明措辞，条目设计不支持 | needs_recheck |
| `abstract_result_direction_conflict` | fact_consistency/fact_consistency | 摘要称显著改善，结果段首个/主要检验不显著 | needs_recheck |

规则只产生候选，不替代语义阅读。每条候选必须人工确认、改写或删除。

## 7. 误报控制

1. **引用观点**：被引文献说“相关”，作者转述时用“导致”，这是引用支持
   问题（`cited_source_misattributed`），不是作者实验结论错误。
2. **作者假设**：`we assume`、`hypothetical`、`if` 引导的情形不判缺陷；
   最多写“假设未检验”的 suggestion。
3. **学科惯例**：横断面研究讨论因果机制时，若作者已明确声明
   “association/cannot infer causation”，不要把整段判错；只标措辞过强
   或已声明局限。
4. **探索性设计**：作者明确声明探索性/描述性时，缺样本量计算、缺效应量
   等应降为 insufficient_evidence 或 suggestion。
5. **材料缺口**：来源只有书目信息、图表未提供、PDF 无文字层时，写入
   `uncertainties`，不要判作者错误。

每条 finding 的 `false_positive_check` 至少核对以上一项，并写出结论。

## 8. 证据与产物

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. quote 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造；
6. 预检原始文件 `output/logic-check.json`、`output/logic-precheck.md` 不得
   篡改；人工补充写到 `output/manual-review.md`；
7. `output/findings.json`、`output/evidence-map.json` 必须与工作文件一致。

## 9. 结果状态

- `review_status=pass`：全文已查且无 confirmed finding；报告写清查了什么、
  为何通过。
- `issues`：至少一条 confirmed finding。
- `needs_recheck`：只有待复核项，或主要结论依赖无法核对的材料。
- `not_checked`：材料不足/无法开始；必须列 `not_checked` 清单。

`not_checked` 与 `uncertainties` 要对应：报告列出的每个未查项，工作文件里
都要有原因、影响和下一步动作。

## 10. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 只审摘要/短摘录 | 跨章节矛盾漏检 | 先跑全文分段，按章节覆盖 |
| 把引用观点当作者结论 | 误报 | speaker=cited，按引用支持处理 |
| 把作者假设当缺陷 | 误报 | speaker=assumption，不判错 |
| 只给章节名 | 无法复核 | 补段落/行号 + 逐字 quote |
| 所有问题都标 major | 严重度失真 | 按影响与可修复性分级 |
| 为凑数强判缺陷 | 可信度下降 | 无问题就写 pass + 证据 |
| 伪造在线结果 | 学术不端风险 | 只用输入材料，写缺口 |
| 静默改数据/引文 | 不可复现 | 保留原始快照与变更日志 |
