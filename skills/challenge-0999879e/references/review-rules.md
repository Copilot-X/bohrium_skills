# 论文质量审查：执行规则与判定锚点

本文件是 `SKILL.md` 的可执行补充。所有字段都能在
`templates/work.schema.json`、`scripts/fam_tracks.py` 的 review 检查或
`scripts/review_gate.py` 的自检中找到对应项。

## 1. 任务口径

- 目标：对给定稿件做一次可复核的质量审查，交付逐条问题清单。
- 主产物：`output/review-report.md`。
- 必填字段：`findings`, `facets_checked`, `review_status`, `evidence`。
- 关键原则：**先分面、后汇总；先原文、后判断；先排除误报，再定严重度。**

## 2. 六类检查项（facet）

| facet | 查什么 | 不算问题的情形（误报来源） |
|---|---|---|
| `fact_consistency` | 摘要/正文/图表/结论的数值、单位、方向、术语是否一致；样本量与分组是否对得上 | 不同实验条件下的不同数值；正文明确解释的口径差异 |
| `citation_support` | 每处引用是否支持所在论断；被引文献设计/结论是否匹配；元数据是否可核对 | 引用用于背景铺垫而非直接证据；综述性引用；期刊要求的合并引用 |
| `method_design` | 对照、随机化、盲法、样本量/抽样、纳排标准、混杂控制、重复与随机化说明 | 观察性研究本就不做随机化；作者明确声明探索性设计 |
| `statistical_reporting` | 效应量、置信区间、检验统计量、p 值口径、多重比较、缺失数据处理、事后分析 | 目标期刊明确不要求 CI；描述性研究只报告描述统计 |
| `argument_chain` | 假设→方法→结果→结论是否闭合；因果/相关混淆、外推、循环论证、替代解释 | 作者明确标注为假设或推测；讨论中已声明局限 |
| `academic_compliance` | 伦理、知情同意、利益冲突、数据/代码可用性、图表版权与来源、目标期刊规范 | 目标期刊允许省略；材料明确说明声明在补充文件 |

**覆盖要求**：六个 facet 都要有 `facets_checked` 条目。与本文无关的
facet 用 `not_applicable` 并写 reason；材料不足无法核对的用
`not_checked` 或 `partial` 并写 reason。不得把未检查写成 `checked`。

## 3. 证据与定位

1. 每条 finding 必须有 `locator`（`path` + 段落/行/页/章节）和逐字
   `original_quote`；引用类问题同时给论断位置与文献条目位置。
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写、
   翻译、拼接不同段落或用“大意”。
3. 同一问题需要多处原文才能成立时，把多处都放进 `evidence[]`；
   `evidence[]` 中每个 quote 都要能单独回查。
4. 找不到原文的候选问题只能进 `uncertainties`，并标
   `needs_recheck`；不得凭常识补全。
5. 同一材料的不同版本/格式不构成独立证据；版本不同要分别定位。

## 4. 问题类型

| type | 定义 | 例子 |
|---|---|---|
| `error` | 可判定为错误：事实错误、数据冲突、引用与论断错配、方法自相矛盾 | 摘要 p=0.03，主分析 p=0.06；称随机但方法写交替分配 |
| `insufficient_evidence` | 材料未报告或无法判断，需要补充证据 | 缺样本量计算；缺效应量/CI；图 1 未提供 |
| `norm_violation` | 伦理、数据可用性、利益冲突、引用格式等规范问题 | 缺伦理批件号；缺数据可用性声明 |
| `suggestion` | 可改进但不构成错误 | 段落顺序、术语统一、补充敏感性分析 |

不要把 `insufficient_evidence` 写成 `error`：未报告不等于做错。
不要把 `suggestion` 混进必须修改项。

## 5. 严重度锚点

| severity | 判据 | 处理 |
|---|---|---|
| `critical` | 会改变主要结论、影响安全性/伦理，或涉及学术不端 | 必须在结论摘要置顶，给出修改或撤稿级建议 |
| `major` | 影响可信度、可复现性、引用有效性或统计推断 | 必须修改后再审 |
| `minor` | 局部表述、格式、元数据不完整，不影响主要结论 | 可修改或说明 |
| `info` | 建议性改进 | 作者自行取舍 |

严重度要与 `impact` 一致。不要把所有问题都标 `critical`；也不要把
引用错配、统计缺失等影响结论的问题标成 `minor`。

## 6. 误报检查（每条 finding 必填）

`false_positive_check` 至少核对以下一项，并写出结论：

1. **学科惯例**：该写法在本学科是否常见且可接受？
2. **期刊/格式差异**：目标期刊是否允许这种结构或引用格式？
3. **作者有意取舍**：作者是否明确声明了探索性、描述性或范围限制？
4. **方法可接受但报告不全**：方法本身是否合理，只是缺报告？

若无法排除误报，处理顺序：
把 `type` 降为 `suggestion` → 或标 `status=needs_recheck` →
或从 `findings` 移到 `uncertainties`。不要靠“看起来不对”下结论。

## 7. claim-source 矩阵

对每处引用（或每个引用键/编号）建行：

```json
{
  "claim_id": "CS01",
  "claim": "本文论断",
  "claim_locator": "manuscript.md P2",
  "source": "[1] / reference P12",
  "cited_source": "[1] / reference P12",
  "source_statement": "被引文献的原文主张或条目原文",
  "source_locator": "manuscript.md P12",
  "verdict": "full|partial|none|unverifiable",
  "note": "为何 full/partial/none/unverifiable"
}
```

`source` 必填（finalize 引擎用 `claim`+`source` 校验矩阵行）；
`cited_source` 可作更细的描述字段。

判定锚点：

- `full`：被引文献的设计、对象与结论直接支持本文断言；
- `partial`：只支持部分条件，或需要限定后才成立；
- `none`：文献研究的是另一主张/另一人群/另一设计，或结论相反；
- `unverifiable`：材料只给条目、无摘要/全文，无法判断支持关系。

`citation_support` 的每条 finding 必须能对应到至少一行矩阵；
矩阵中 `none`/`partial` 行即使不形成 finding，也要在报告中列出，
便于复核。

## 8. 结果状态

- `review_status=pass`：六面已查且无 confirmed 问题；仍需在报告
  写清“查了什么、为何通过”。
- `issues`：至少一条 confirmed finding。
- `needs_recheck`：只有待复核项，或主要结论依赖无法核对的材料。
- `not_checked`：材料不足、无法开始或关键部分不可读；必须列
  `not_checked` 清单。

`not_checked` 与 `uncertainties` 要对应：报告里列出的每个未查项，
工作文件里都要有原因、影响和下一步动作。

## 9. 报告要求

报告必须让另一个人不打开工作文件也能逐条核对。最低结构：

1. 审查元信息（case_id、稿件版本、材料清单、审查范围）；
2. 方法与六面覆盖表（状态 + 查了什么 + 证据 locator）；
3. 结论摘要与总体判断；
4. 问题清单（按严重度），每条含 ID、facet、位置、原文、类型、
   严重度、impact、修改建议、误报检查；
5. claim-source 矩阵；
6. 错误/证据不足/建议分栏；
7. 待复核、未检查、材料缺口；
8. 可复核性与限制。

禁止：

- 只给整篇评语或内容复述，没有问题条目；
- 只给检查清单，没有具体定位与判断；
- 报告里出现工作文件中没有的 finding ID 或引文；
- 把“未报告/未提供”直接写成“错误”；
- 把学科惯例或期刊差异写成缺陷而不做误报检查。

## 10. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 以结果定义分母 | 指标虚高 | 先冻结稿件版本与检查项，再计算覆盖 |
| 把未找到当不相关 | 遗漏被掩盖 | 单列未命中/未检查与原因 |
| 只给结论不给证据 | 无法复核 | 补 path/locator/quote |
| 合并不同对象/版本 | 事实错误 | 保留版本与定位，分别建条 |
| 伪造在线结果 | 学术不端风险 | 只用输入材料，写缺口 |
| 静默改数据 | 不可复现 | 保留原始快照与变更日志 |
| 把所有建议写成缺陷 | 误报失控 | 用 suggestion 类型并做误报检查 |

## 11. 规则扫描（quality_review.py）

运行顺序：`prepare` → `quality_review.py` → 模型分面审查 → `review_gate.py`
→ `finalize` → `review_gate.py --apply`。

### 11.1 脚本输出

`work/rule-review.json` 与 `output/rule-review.json`：

| 字段 | 用途 |
|---|---|
| `engine_status` | `ok` / `partial`（缺方法或结果段）/ `input_error`（无材料）/ `error`（脚本异常） |
| `extraction` | 章节、段落（locator + 行号 + 统计标记）、图表题注、参考文献条目 |
| `findings[]` | 候选问题与逐字证据；`confidence` ∈ high/medium/low，`rule_status` 为规则原始判定 |
| `claim_source_matrix[]` | 引用编号与文献条目自动配对；只有设计关键词明显不匹配时判 `none`，否则 `unverifiable` |
| `coverage[]` | 六面建议状态；缺段/缺图注时为 `not_checked` 并写 reason |
| `structure_gaps[]` | 无法逐字引用的缺口（缺章节、缺图注、缺样本量说明） |

### 11.2 候选处置规则

1. `--merge` 导入的候选一律为 `status=needs_recheck`，编号 `A01...`；
   `rule_status` 只表示脚本曾判为 confirmed，**不是最终结论**。
2. 逐条回原文核验：确认 → `status=confirmed`；改写 → 保留 `rule_id` 并写
   `rule_status`；驳回 → 删除并写入 `uncertainties`（原因 + 需要的材料）。
3. 规则未覆盖的段落、图表与语义问题仍按六面协议人工补查；
   `rule_review` 不是“已全部检查”的证明。
4. 低置信度候选默认不合并；若模型认为有价值，可手动加入并说明理由。
5. 脚本永不生成 summary、verdict、报告结论；这些只能由模型写。

### 11.3 已知误报来源

| 规则 | 主要误报来源 | 处置 |
|---|---|---|
| `p_value_mismatch` | 摘要报告的是次要/校正后分析；不同结局各有一个 p | 核对该 p 对应的分析集与结局，标 needs_recheck 或删除 |
| `effect_size_missing` | 效应量只在补充材料/表格图片中 | 检查补充材料；若存在则删除 |
| `citation_claim_mismatch` | 引用仅作背景铺垫；文献条目是节选 | 查看文献全文/摘要；背景引用不判 error |
| `compliance_declarations_missing` | 声明在投稿系统、标题页或补充文件 | 材料中确认存在即删除 |
| `sample_size_inconsistency` | 入组数 vs 纳入分析数不同 | 若正文明确区分分析集，改为 suggestion/删除 |
| `generalization_overreach` | 局限段已明确声明推广限制 | 若结论句本身无保留，保留 finding 但降级 |

### 11.4 自测

```bash
python3 scripts/quality_review.py --selftest
```

覆盖三路 synthetic 输入：正常稿件（应触发 p 值冲突、随机化冲突、引用错配、
因果/外推、缺效应量、缺伦理声明等候选）、空输入（`input_error` 且无
findings）、缺方法/结果段（`partial`、对应 facet `not_checked`、有
structure_gaps、引文仍逐字可查）。自测输出明确标 `synthetic_only`，
不写入正式结果。

## 12. 自检清单

运行 `scripts/review_gate.py` 后确认：

1. `problems=[]`；若有 `warnings`，在报告“限制”中解释；
2. 六面覆盖齐全，`partial/not_checked/not_applicable` 都有 reason；
3. 每条 finding 的 quote 逐字可查，且报告中出现对应 ID 与引文；
4. `citation_support` 的每条 finding 都有 claim-source 行；
5. `review_status` 与 `not_checked`、`uncertainties` 一致；
6. `output_contract` 每个键都有产物或 canonical 映射。
