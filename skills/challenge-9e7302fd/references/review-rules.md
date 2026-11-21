# 研究方法与统计审查：执行规则

## 1. 任务口径

- 目标：研究设计、变量定义、统计方法、样本量与检验效能、结果报告五个检查面。
- 主产物：`output/review-report.md`。
- 必填字段：`findings`, `facets_checked`, `decision_rules`,
  `reporting_checklist`, `evidence`, `checks`, `verdict`。
- 提交前必须运行 `scripts/review_quality_gate.py`，读取
  `work/review-gate.json` 的 `problems[]` 并逐条修复。

## 2. 证据与定位

1. 每条 finding 必须有 `locator.path + locator.locator`（段落/行/表/图/节）
   和 `original_quote`；`evidence` 再给 `path/locator/quote`。
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
   找不到原文时写入 `uncertainties`，不要用常识补全。
3. 同一材料的不同版本/格式不构成独立证据；
4. 外部未提供的文献、DOI、数值、设备状态不得编造；
5. 审查报告中的每个 ID 必须能在 `findings.json` 找到，反之亦然。

## 3. 五检查面清单

### 3.1 研究设计（design）

- 设计类型（RCT/队列/病例对照/横断面/自身对照）与所回答的问题是否匹配；
- 对照、分组、随机化、分配隐藏、盲法、偏倚控制是否报告且成立；
- 主要终点、假设、分析集是否在结果之前预设；
- 样本独立性：重复测量/聚类/配对结构是否被忽略。

### 3.2 变量定义（variables）

- 暴露、结局、协变量、分组变量的定义、测量方式与编码是否一致；
- 分类切点、单位换算、缺失值处理是否有依据；
- 变量类型（连续/有序/二分类/计数/时间）与后续模型是否匹配；
- Methods、Table、Results 中的变量名与层级是否一致。

### 3.3 统计方法（statistics）

- 检验/模型与数据结构（配对/独立、重复测量、聚类、删失）是否匹配；
- 正态性、方差齐性、线性、独立性等前提是否检查或合理说明；
- 多重比较、交互项、亚组、敏感性分析是否被正确处理；
- 缺失数据机制（MCAR/MAR/MNAR）与处理方法是否报告；
- 不要把“未报告前提”写成“前提不成立”，除非原文明确做了错误假设。

### 3.4 样本量与检验效能（sample_size）

- 是否报告样本量估算依据、效应量来源、α、把握度、失访率；
- 实际入组量/分析量 vs 估算量；剔除与失访是否说明；
- 事后功效计算不能替代事前样本量估算；
- 小样本下不要把不显著直接解释为“无效应”。

### 3.5 结果报告（reporting）

- 效应量与 95% CI、精确 p 值、检验统计量与自由度；
- n 在 Methods/Results/Table/Figure 中是否一致；
- 图表与正文数值是否一致；百分比分母是否清楚；
- 是否满足对应研究报告规范（CONSORT/STROBE/PRISMA 等，
  以材料提供的规范为准，未提供时写 `not_checked`）。

## 4. 决策规则（`decision_rules` 至少 4 条）

建议覆盖：

| rule | condition | decision |
|---|---|---|
| paired vs unpaired | 同一受试者前后测量 / 配对设计 | 用配对检验；独立组用独立检验 |
| normality | 连续结局、小样本、需 t 检验 | 报告正态性检查；不满足时用非参数或说明 |
| equal variance | 两组连续结局 | 报告方差齐性检验；不齐时用 Welch |
| multiplicity | 多组/多终点/多亚组 | 报告校正方法（Bonferroni/Holm/FDR）或说明为探索性 |
| repeated measures | 同一受试者多次测量 | 混合模型/重复测量 ANOVA，不按独立点处理 |
| missing data | 有失访/缺失 | 报告缺失比例、机制假设与处理方法 |
| effect size | 报告 p 值 | 同时报告效应量与 CI；p 值不等于效应大小 |

每条规则写明 `rule`、`condition`、`decision`；引用原文依据时加
`source`/`evidence`。

## 5. 严重度与类型

- `severity`：
  - `critical`：结论可能无效或存在学术不端/无法复核；
  - `major`：主要方法或报告缺陷，影响结论解释；
  - `moderate`：需要补充说明或敏感性分析；
  - `minor`：表述/格式/可读性问题；
  - `info`：非缺陷的观察或建议。
- `type`：`error`（做错）、`insufficient_evidence`（未报告/证据不足）、
  `reporting_gap`（报告规范缺失）、`suggestion`（可改进）、
  `statistical`/`design`/`variable`/`sample_size`（专项）。
- 同一缺陷只写一条；不要把“未报告”一律写成“错误”。

## 6. 报告清单（`reporting_checklist`）

至少覆盖：`n`, `effect`, `ci`, `exact_p`, `df`, `missing_data`,
`multiplicity`；可选 `randomization`, `blinding`, `software`, `itt`。
每个值为 `reported|missing|not_applicable|inconsistent|not_checked`，
或 `{status, value, locator, note}`。`inconsistent` 必须对应一条 finding。

## 7. 结果报告与产物

- `output/review-report.md`：范围/材料、五检查面、发现表、证据/定位、
  待复核/不确定、复现边界（见 `templates/review-report.template.md`）。
- `output/findings.json`：与工作文件 `findings` 一致。
- `output/evidence-map.json`：逐条 `path/locator/quote`。
- `output/review-gate.json`：质量闸门的真实输出。
- 失败/跳过/权限不足要记录，不把未完成写成完成。

## 8. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 只给“统计方法不当” | 无法复核 | 补原文位置、检验名、前提与修改建议 |
| 把未报告写成做错 | 误判 | 用 `insufficient_evidence`/`reporting_gap` |
| 严重度一律 major | 审查单不可用 | 按第 5 节分级 |
| 五个检查面只做统计 | 完整性失分 | 每个 facet 至少 1 条 `checks[]` |
| quote 是改写/拼接 | 证据链断裂 | 回到原文逐字重摘 |
| 把 p>0.05 写成“无效应” | 统计误读 | 报告效应量与 CI，说明功效不足 |
| 把事后功效当事前估算 | 方法错误 | 标为 `post hoc`，不替代样本量估算 |
| 忽略配对/重复测量 | 独立性假设错误 | 用 `decision_rules` 说明结构 |
| 报告清单缺 n/CI/df | 报告不完整 | 补 `reporting_checklist` 与 finding |
| 闸门失败仍强行 finalize | 结果不可复核 | 按 `problems[]` 修复或写 `uncertainties` |
