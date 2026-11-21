# 科研选题与创新性评估：执行规则

## 1. 任务口径

- 目标：选题评估、创新性/可行性/证据缺口与风险。
- 主产物：`output/research-report.md`。
- 必填字段：`problem_definition`, `comparison_matrix`, `novelty_claims`,
  `feasibility`, `references`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

每个评分有依据；创新性不夸大；可行性条件明确。

### 3.1 问题定义的可检验性

`problem_definition` 必须包含：

| 字段 | 要求 |
|---|---|
| `question` | 一句话可检验问题，不是主题词 |
| `population` | 研究对象/数据范围 |
| `variables` | 自变量、因变量/主要终点 |
| `baseline` | 比较基线或当前最好方法 |
| `falsification_conditions` | 什么结果会推翻设想（至少一条） |
| `scope` | `provided_corpus_only` 或 `live_search` |

缺字段写 `not_stated_in_materials`，并在 `uncertainties` 要求 PI 补充；
不得用通用套话补齐。

### 3.2 新颖性判定（禁止夸大）

- 只允许三种判定：`possibly_covered`（语料内有较高词面重叠）、
  `weakly_related`、`no_overlap_in_provided_corpus`。
- 词面重叠只是筛查信号，不是语义覆盖证明；报告必须写明 limitation。
- 含“首次/尚无人/no prior/first”的陈述，若 `scope != live_search`，
  必须判为不支持全球新颖性并降级 `partial`。
- 在线检索必须保留检索式、数据库、日期、结果快照；没有这些证据就不写
  “全球首次”。

### 3.3 比较矩阵

每条记录至少给出 option/dimension/value/directly_comparable/reason。
缺 task/method/data/evaluation/conclusion 时写 `not_extracted`，并把
`directly_comparable=false`；不能把“标题相似”当成“方法相同”。

### 3.4 可行性前提

数据、算力、时间、人员、伦理、设备六类前提逐项给
`evidence_in_material` / `unknown`；每个前提有来源或明确 unknown，
结论只能是 `conditional` 或 `insufficient_information`。

## 4. 产物

- 主产物必须非空、可编辑、可复核；
- 每个声明 artifact 必须真实存在；
- 分析类任务保留原始快照、规则/代码、日志与结果文件；
- 失败/跳过/权限不足要记录，不把未完成写成完成。

## 5. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 以结果定义分母 | 指标虚高 | 先冻结参考集/输入，再计算 |
| 把未找到当不相关 | 遗漏被掩盖 | 单列未命中与原因 |
| 只给结论不给证据 | 无法复核 | 补 path/locator/quote |
| 合并不同对象 | 事实错误 | 保留 `keep_separate` 与依据 |
| 伪造在线结果 | 学术不端风险 | 只用输入材料，写缺口 |
| 静默改数据 | 结果不可复现 | 保留原始快照与变更日志 |

## 6. 本题指标与字段

- 结论：每条有 `statement/sources/limitations/evidence`；区分观测、推断与空白。
- 证据矩阵：按维度分组比较，保留每篇的结论、条件与局限；冲突并列。
- 系统综述：PRISMA 式检索/纳排/纳入计数闭合；每个排除有理由码。
- 趋势/选题：时间窗、检索范围与数量口径明确；不把共现当因果，不夸大创新性。
- 四项交付齐全：问题定义/比较矩阵/创新审计/可行性。
- 创新性不得写“首次/尚无人”除非有在线检索证据；可行性每个前提有来源或标 assumption。
