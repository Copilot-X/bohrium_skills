# 文献断言核验参考（原子化、证据定位、四类判定）

配合 `templates/verification-report.json` 与 `scripts/check_claim_verification.py` 使用。
**`/input/task.md` 与 `/input/claims-to-verify.json` 优先**：claims 是待核验问题，不是
已确认事实；材料没有写的内容不要补，找不到就明确写检索范围。

## 1. 输入与输出边界

典型输入：

- `/input/claims-to-verify.json`：待核验断言。字段可能是 `claims[]` / `claims_to_verify[]`，
  每条含 `claim_id`（或 `id`）、`text`（或 `claim` / `statement`），可能带 `source_id`。
- 论文正文、段落 JSON、PDF 抽取文本、表格或图注；段落通常有 `paragraph_id` / `pid`。
- `/input/task.md`：输出字段与判定词表以它为准。

输出写到 `/app/output/result.json`。核心链路必须可复核：

```
claim → 原子 assertion → 来源定位（doc_id + paragraph_id + 逐字 quote）
      → verdict + confidence → reason → 受限改写
```

本环境的判定词表固定为四个：

| verdict | 含义 |
|---|---|
| `supported` | 材料直接蕴含该断言，且总体、条件、方向、强度一致 |
| `partially_supported` | 只支持一部分：数值/方向对但范围、人群、条件或强度不足 |
| `contradicted` | 材料明确给出相反方向、不相容数值或明确否定 |
| `insufficient_evidence` | 检索后没有直接证据，或只有间接/二手/含糊证据 |

不要输出 `not_found`、`ambiguous`、`mostly_true` 等旧词或自造词。旧技能里的
`not_found` / `ambiguous` 一律归入 `insufficient_evidence`，并在 `reason` 写清是
“未检索到”还是“存在但含糊/冲突”。

## 2. 断言类型与拆分

一句陈述只保留一个可独立核验的判断。含比较、因果、数值、范围、否定、显著性的句子
通常要拆。拆分后保留父子映射：`claim_id` 指回输入断言，`assertion_id` 唯一。

| 类型 | 识别信号 | 拆分方式 |
|---|---|---|
| `fact` | 单一主谓宾 | 直接核验实体、关系、时间 |
| `comparison` | 高于/低于/超过/优于/相比；higher/lower/more than | 拆成 A 值、B 值、比较关系三条 |
| `causal` | 导致/因为/使/证明；cause/lead to/prove | 拆成关联、时间顺序、机制、因果结论 |
| `numeric` | 数字 + 单位 | 拆成数值、单位、条件、比较基准 |
| `range` | 10–20%、不超过、至少 | 保留上下界与是否含端点 |
| `negation` | 不/未/无/没有；not/no/without | 保留否定辖域：否定的是存在、差异还是显著性 |
| `significance` | 显著、p 值、95% CI；significant | 拆成效应方向、效应量、统计显著性 |
| `attribution` | 某研究证明/某综述指出 | 区分原作者结论与二手转述 |

拆分检查：

1. 每个子断言只含一个谓词或一个数值判断。
2. 比较句不能只留“更高”，必须写清比较对象与指标。
3. 因果句不能把“相关/提示/可能”直接升级为“证明/导致”。
4. 一个断言只在一个段落就能判定的，不要再拆。
5. 拆分不能遗漏限定词（人群、剂量、温度、时间、模型、统计口径）。
6. 同一 `claim_id` 下允许多个 `assertion_id`；不要重复造等价的子断言。

示例：

> 原句：“该催化剂在 300 °C 下转化率超过 95%，且稳定性优于参比样。”

拆为：

- `a1`：该催化剂在 300 °C 下的转化率超过 95%（numeric + comparison）。
- `a2`：该催化剂在相同条件下的稳定性高于参比样（comparison）。
- `a3`：上述比较是在同一测试条件下完成的（condition，若原文未说明则单独标
  `insufficient_evidence`）。

## 3. 来源定位

### 3.1 建索引

先运行 `scripts/inspect_claims.py` 生成 `/app/work/claim_index.json`，再人工回读原文。
索引至少包含：

- `doc_id`、`paragraph_id`、章节、首句；
- 数字与单位（92%、350 °C、0.4 percentage points）；
- 实体（材料、基因、药物、人群、模型）；
- 否定词与比较词；
- 表格/图注编号。

### 3.2 检索顺序

1. 精确数字 + 单位；单位不同先换算，换算口径写入 `numeric_check`。
2. 实体名 + 谓词（“转化率”“降低”“increase”“no significant difference”）。
3. 同义词与缩写；中文可用连续二字词召回，英文用长度 ≥3 的词。
4. 命中段落的**前后各一段**一起读，防止断章取义。
5. 摘要与正文冲突时以正文/结果为准，并记录冲突；只有摘要可用时在
   `coverage.notes` 或 `uncertainty` 说明。

### 3.3 最小充分证据

- 证据定位优先 `doc_id + paragraph_id`；其次 `page`、`section_id`、`table_id`、`figure_id`。
- `quote` 逐字摘录，不翻译、不改写、不拼接；省略用 `…`，省略不能改变语义。
- 长度以能独立支撑/反驳该断言为准，通常 8–40 词（中文 15–80 字）。
- 只给篇名、作者或摘要结论不算“最小充分证据”。
- 跨段论证挂多条 evidence，并在 `reason` 说明如何组合。

## 4. 四类判定

### 4.1 判定顺序

按以下顺序问，命中即停：

1. 原文是否有**直接蕴含**该断言的逐字证据，且总体、条件、方向、强度一致？
   → `supported`
2. 原文是否有**不相容**的明确表述（相反方向、不同数值、明确否定）？
   → `contradicted`
3. 原文是否只支持一部分（数值对但人群/条件不同，或只支持较弱版本）？
   → `partially_supported`
4. 以上都没有，或证据只有间接/二手/含糊？
   → `insufficient_evidence`

### 4.2 强度与范围

- 原文“可能/提示/相关/未排除”，待核验断言写成“证明/导致/确定”，属于强度拔高。
  拆成“关联存在”和“因果成立”两条：前者可 `supported`，后者按原文证据判
  `partially_supported` 或 `insufficient_evidence`；只有原文明确否认因果时才判
  `contradicted`。
- 动物/体外结果外推到人、单一剂量外推到所有剂量、单一模型外推到其他模型：
  属于**外推**，通常判 `partially_supported`（数值/机制部分成立）或
  `insufficient_evidence`（目标范围没有数据）。
- **未被实验检验 ≠ 被实验反驳**。原文没有测某个终点，不能判 `contradicted`，
  应判 `insufficient_evidence`，除非原文明确说“未观察到/没有差异”。
- 同一断言在材料内自相矛盾：双方证据都保留，判 `partially_supported` 或
  `insufficient_evidence`，在 `limitations` 写“来源冲突”，不得静默择一。

### 4.3 数值核验

必须逐项比对：

| 项 | 检查 |
|---|---|
| 数值 | 92 vs 92.0；百分比 vs 百分点 |
| 单位 | %、°C、mg/kg、h、nM；先换算再比较 |
| 分母/基线 | 相对变化 vs 绝对变化；“compared with placebo”是否扣除了对照变化 |
| 时间点 | 12 周 vs 24 周；峰值 vs 终点 |
| 条件 | 温度、剂量、pH、样本量、模型 |
| 统计 | p 值、95% CI、显著性检验类型；显著 ≠ 效应大 |

换算写进 `numeric_check`：

```json
{"claimed": "0.4 percentage points vs placebo",
 "observed": "0.4 points within intervention; 0.1 points within placebo",
 "conversion": "between-group difference = 0.3 points",
 "unit": "percentage points"}
```

换算不了或原文单位缺失：判 `insufficient_evidence`，不要猜。

### 4.4 比较、否定、显著性、条件

- **比较**：确认比较对象、指标、方向、基线一致；“高于对照”不等于“最高”。
- **否定**：保留辖域。“未发现显著差异”不等于“两组相同”；“不支持因果”不等于
  “证明无因果”。
- **显著性**：`p < 0.05` 只说明统计显著；断言若写“大幅/显著改善”，还要看效应量。
- **条件**：温度、剂量、人群、模型、时间窗必须与断言一致；条件不同先判
  `partially_supported`，不能直接 `supported`。
- **比较词/否定词/显著性标记**是否被保留，可用校验器的 warning 辅助检查：
  断言含比较词但 `comparator` 为空、含否定词但缺 `negation`、含显著性词但缺
  `significance`，都应补齐。

## 5. 冲突、二手引用与时效

- **冲突**：不同来源给出相反结论时，`supporting_evidence` 与 `counter_evidence`
  都要有定位；verdict 用 `partially_supported`（可解释的差异）或
  `insufficient_evidence`（无法裁决），并在 `limitations` 写冲突点。
- **二手引用**：综述里的转述不能当作原始研究证据。`evidence_level` 标
  `review` 或 `secondary_source: true`，优先回溯原始论文；无法回溯时降低
  confidence，并在 `uncertainty` 说明。
- **撤稿/预印本**：`retraction_flag` / `preprint_flag` 必须记录；撤稿论文不能支撑
  正向结论，预印本与正式版分开核。
- **时效**：医学/政策/标准类断言要记录材料覆盖的时间范围；超过时效窗口的结论
  在 `uncertainty` 标注，不要假定仍然成立。
- **联网边界**：本环境无联网检索时，只能做“材料内一致性核对”，不得声称已在线
  核验 DOI/PMID；需要外部核验的条目标 `verification_route: "manual"`。

## 6. 引用验真与一致性检查（材料内）

有参考文献表时，除逐条断言核验外，再做一次文献表体检：

1. **匹配质量六档**：`exact / near_exact / partial / mismatch / not_found / ambiguous`。
2. **处置五档**：`verified / minor / major / critical / manual`；匹配质量与处置分开记。
3. **双向对齐**：正文引用标记 ↔ 文末条目；孤儿引用（正文有、文末无）与未被引条目
   （文末有、正文无）都列。
4. **全表问题**：重复条目、编号跳号/乱序、期刊缩写不一致、中英文混排、预印本冒充
   正式版、撤稿未标。
5. **语言路由**：英文文献优先 DOI/PubMed/Crossref；中文文献优先中文标题
   （CNKI/万方/官网），中文无 DOI 不算缺陷。
6. **禁止编造**：不得虚构 DOI、PMID、页码、作者或“已核验”状态；材料内无法确认的
   标 `manual`。

## 7. 证据卡与结果字段

每条 verdict 至少包含：

```json
{
  "assertion_id": "a1",
  "claim_id": "c1",
  "source_claim": "原文待核验陈述（可截取）",
  "assertion": "原子化后的单事实断言",
  "claim_type": "fact|comparison|causal|numeric|range|negation|significance|other",
  "comparator": "比较对象与指标；无则空字符串",
  "negation": false,
  "significance": "p 值/CI/显著性口径；无则空字符串",
  "conditions": ["350 °C", "1 bar"],
  "verdict": "supported|partially_supported|contradicted|insufficient_evidence",
  "confidence": 0.9,
  "supporting_evidence": [
    {"doc_id": "paper1", "paragraph_id": "p2", "quote": "逐字原文",
     "evidence_level": "primary|review|secondary", "secondary_source": false}
  ],
  "counter_evidence": [],
  "limitations": [],
  "reason": "为什么该 verdict 成立",
  "search_scope": "检索过的段落/章节/关键词；insufficient 时必填",
  "numeric_check": {"claimed": "", "observed": "", "conversion": "", "unit": ""},
  "rewrite": "受原文约束的改写",
  "match_quality": "exact|near_exact|partial|mismatch|not_found|ambiguous",
  "severity": "critical|major|minor|none",
  "verification_route": "local_text|manual|metadata",
  "retraction_flag": false,
  "preprint_flag": false
}
```

规则：

- `supported` 至少 1 条 `supporting_evidence`，且 `counter_evidence` 为空。
- `contradicted` 至少 1 条 `counter_evidence`；rewrite 必须给出正确表述。
- `partially_supported` 至少 1 条 `supporting_evidence`，并写清 `limitations`
  （或给出 `counter_evidence`）。
- `insufficient_evidence` 必须有 `search_scope` 与 `reason`；rewrite 写
  “材料未报告/无法判断”，不得补新事实。
- `confidence` ∈ [0,1]；直接原文证据可高，二手/间接证据必须降低。
- 断言中的数值必须能在对应证据 quote 中找到；换算值写进 `numeric_check`。
- rewrite 中出现的数值必须来自证据 quote；否则视为引入材料外信息。

## 8. 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 主题相关当支持 | 证据只谈同一主题，没有断言谓词 | 降为 `insufficient_evidence` 或找直接证据 |
| 未检索到当反驳 | 没有反例 quote 却判 `contradicted` | 改 `insufficient_evidence`，写检索范围 |
| 外推当反驳 | 原文只是没测目标人群/终点 | 改 `partially_supported`/`insufficient_evidence` |
| 强度拔高 | 原文“可能”，断言“证明” | 拆分子断言，按证据强度判 |
| 比较基线错误 | 把组内变化当成组间差异 | 重算差值，写 `numeric_check` |
| 否定辖域丢失 | “无显著差异”写成“完全相同” | 保留否定范围，改 rewrite |
| 二手当原始 | 综述转述被标 primary | 标 `secondary_source`，回溯原文 |
| quote 非逐字 | 引文与原文措辞不同 | 重新摘录，省略用 `…` |
| 一句多事实 | 一条 assertion 含两个判断 | 拆分后逐条判定 |
| 改写越界 | rewrite 出现材料没有的数字/机制 | 删除或改为“材料未报告” |
| 冲突被抹平 | 只留支持证据 | 双方证据并列，verdict 降级 |
| 汇总不一致 | summary 计数与 verdicts 不符 | 重算后修正 |

## 9. 交付前检查

- [ ] `/input/claims-to-verify.json` 中每个 `claim_id` 都有 verdict 覆盖；
      复合断言已拆成多个 `assertion_id`。
- [ ] verdict 只使用四个官方词，未混入旧词/自造词。
- [ ] 每条 verdict 有 `doc_id + paragraph_id`（或等价 locator）与逐字 quote；
      `contradicted` 有 counter quote。
- [ ] 数值、单位、比较基线、时间点、条件已比对；换算写入 `numeric_check`。
- [ ] `supported` 没有 counter evidence；冲突断言双方定位都在。
- [ ] `insufficient_evidence` 写了 `search_scope`，rewrite 没有补新事实。
- [ ] rewrite 中的数字都能在证据 quote 找到。
- [ ] `summary` 计数与 `verdicts` 一致，`high_risk` 指向真实 `assertion_id`。
- [ ] `uncertainty`、`change_log`、`checked_range`、`support_files_used` 已填。
- [ ] `python3 {SKILL_DIR}/scripts/check_claim_verification.py --result
      /app/output/result.json --input /input --json` 返回 `ok: true`。
