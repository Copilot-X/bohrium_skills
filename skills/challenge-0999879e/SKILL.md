---
name: review-quality-review-skill
description: 论文质量审查：先用 quality_review.py 对实际全文做章节/段落/句子分段，抽取主张清单、方法/统计/图表题注与引用，生成 fulltext_coverage、claim_inventory、cross_section_checks、facet_subchecks 和带逐字证据的候选 findings，再由模型逐条核验；围绕事实核验、引用支持、方法设计、统计报告、论证链、学术规范六类检查项，产出可定位、分严重度的审查问题，附 claim-source 对照、误报检查与可执行修改建议；交付 review-report.md、findings.json、evidence-map.json 和含官方 reading/writing 字段的 result.json。Use for 论文质量审查、quality-review、review、证据绑定、claim-source matrix、结果复核.
---

# 论文质量审查（review track · quality-review）

## 用途与触发

task.md 要求“论文质量审查、逐条问题、严重度与修改建议”，或
`/input/case.json` 给出 `review` / `quality-review` 相关字段时使用。
核心交付是**逐条、可定位、可核对的问题清单**：

- 每条问题有 facet、原文位置、逐字原文、严重度、问题类型、影响与修改建议；
- 引用类问题有“本文论断 → 被引文献实际主张 → 判定”的 claim-source 对照；
- 六类检查项逐项有状态，未检查项显式列出，不用“通过”掩盖未查；
- 错误、证据不足、规范问题和建议性意见分开；
- 误报检查逐条记录，学科惯例、期刊格式差异和作者有意取舍不判为缺陷。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代
同行评审/机构审查/临床决策。材料不足时只交付可确认部分，并把缺口写成
`uncertainties`，不要补造。

核心原则：**有输入、有依据、有产物、有边界，先分面审查再汇总结论**。
脚本负责盘点材料、建立工作区、校验证据/字段/产物、运行分面自检并组装
`result.json`；模型负责逐段阅读、判断、定位与写修改建议。脚本不会替你
生成问题、结论或审查意见。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：本题的具体问题、范围、输出要求与证据范围。
- `/input` 下的材料：`.md/.txt/.json/.csv/.pdf/.docx/.tex/.yaml` 等。
  `scripts/parsers.py` 会提取文本、JSON 数据与 CSV 行；无文字层 PDF
  尝试 OCR，失败如实记录。
- 可选：已有工作文件、术语表、数据字典、模板、参考文献导出。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、产物登记与 result 信封 |
| `scripts/quality_review.py` | **规则扫描**：抽取章节/段落/句子/图表题注/统计量与引文，生成带逐字证据的候选 findings、claim-source 行和覆盖建议；写 `work/rule-review.json` |
| `scripts/review_gate.py` | 分面覆盖、finding 子字段、逐字证据、claim-source 与报告自检 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/review-findings.json` 的字段契约 |
| `templates/review-report-template.md` | 审查报告骨架（按此写，不照抄结论） |
| `references/review-rules.md` | 六类检查项、严重度锚点、误报抑制与判定规则 |
| `examples/synthetic-input.json` | synthetic 工作文件示意（非正式案例） |

## 工作流

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，提取文本/JSON/CSV，写 `work/inventory.json` 与
   `work/materials.json`；
2. 生成 `work/review-findings.json` 骨架（已存在则不覆盖）；
3. 写 `output/README-partial.md` 与 provisional `result.json`。

先读 `work/materials.json`、`work/inventory.json` 与 `task.md`，确认：
稿件版本、可用材料、题目指定的检查项、输出契约键和缺失项。
材料缺失、文件不可读、图表无文字层要立即写入 `uncertainties` 与
`not_checked`，不要在后面假装查过。

### 第 2 步：规则扫描 + 分面审查（约 60% 预算）

#### 2.1 先运行规则扫描（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/quality_review.py" \
  --input /input --work /app/work --output /app/output
```

脚本会从 `/input`（或 prepare 生成的 `work/materials.json`）抽取：章节与
段落序列（`P1`/`U1` + 行号 locator）、句子、方法段、p 值/样本量/效应量/
置信区间/检验统计量、图表题注、引用编号与 LaTeX `\cite{}` 键，并对
**可判定的文本模式**做窄
规则检查。输出 `work/rule-review.json` 与 `output/rule-review.json`：

| 字段 | 内容 |
|---|---|
| `engine_status` | `ok` / `partial`（缺方法或结果段）/ `input_error`（无可读材料） |
| `extraction` | 章节、段落（含 `stats`）、题注、参考文献与统计摘要 |
| `findings[]` | 候选问题：`rule_id`、facet、locator、逐字 quote、severity/type、`confidence`、`rule_status`、fix、`false_positive_check` |
| `claim_source_matrix[]` | 从正文引用与参考文献条目自动配对的候选行（多为 `unverifiable`） |
| `claim_inventory[]` | 全文主张清单：speaker(author/cited/assumption)、type、section、support、strength、locator、逐字证据 |
| `cross_section_checks[]` | 摘要/引言承诺在方法/结果中是否交付；consistency 五档枚举 |
| `fulltext_coverage` | 章节/段落/句子/字数、各章节主张数与图表题注；证明审的是全文而非摘录 |
| `facet_subchecks[]` | 具体审稿面：结构完整性、摘要-正文、术语、引用可回查、设计/分配、样本量、统计完整性、因果边界、伦理、数据/代码、利益冲突、注册 |
| `coverage[]` | 六面建议状态（checked/partial/not_checked + reason） |
| `structure_gaps[]` / `uncertainties[]` | 缺段、缺图注、缺样本量说明等“无法逐字引用”的缺口 |

规则扫描与模型的分工是硬边界：

| 环节 | `quality_review.py` | 模型 |
|---|---|---|
| 抽取与定位 | 输出原文切片、行号、逐字 quote | 抽查并补足脚本未覆盖的段落/图表 |
| 候选问题 | 只按规则产生候选，`confidence` 标明强度 | **逐条回原文核验**，接受、改写或删除 |
| 严重度/类型 | 给默认值，不声称最终结论 | 结合学科惯例与影响确定 final severity/type |
| 误报检查 | 预填已做的核对项 | 补充期刊/学科/作者取舍判断，必要时标 needs_recheck |
| 结论与报告 | **不生成**任何审稿结论 | 写摘要、总体判断、问题清单与限制 |
| claim-source | 生成候选行，能判定的写 `none`，其余 `unverifiable` | 查看摘要/全文后改为 full/partial/none 并写 note |

常用规则：

| rule_id | facet | 触发条件 |
|---|---|---|
| `p_value_mismatch` | fact_consistency | 摘要首个 p 值与结果段首个 p 值不同；若后文出现相同 p 值则标 needs_recheck |
| `significance_threshold_conflict` | statistical_reporting | 同一句称 significant 但 p ≥ 0.05，且无 not/non-significant |
| `randomization_conflict` | method_design | 摘要/方法称 randomized，方法段写 assigned alternately |
| `sample_size_not_reported` | method_design | 明确写未做先验样本量/效能计算；仅“未找到”则进 gap |
| `effect_size_missing` | statistical_reporting | 有 p 值但全文无效应量（同时记录 CI/检验统计量缺失） |
| `causal_overclaim` / `durability_overclaim` / `generalization_overreach` | argument_chain | 因果措辞 vs 非随机设计；无随访却称持久；窄样本外推到所有人群 |
| `citation_claim_mismatch` | citation_support | 干预/因果论断引用 cross-sectional/survey/no-intervention 等设计不匹配的条目 |
| `citation_reference_missing` / `reference_metadata_incomplete` | citation_support | 正文编号或 `\cite{}` 无条目；条目明写 incomplete/unknown；多条目缺年份时进 gap 而非逐条 finding |
| `figure_not_provided` | fact_consistency | 正文明确写图未提供/缺失；仅“未见图注”进 gap |
| `compliance_declarations_missing` / `data_availability_missing` / `coi_statement_missing` | academic_compliance | 人类参与者研究未见伦理/同意/数据/利益冲突声明 |
| `sample_size_inconsistency` | fact_consistency | 摘要与方法段的 n 值集合不相交 |
| `abstract_body_direction_conflict` | fact_consistency | 摘要称显著/改善，结果段首个或主要检验不显著 |
| `conclusion_exceeds_results` | argument_chain | 讨论/结论使用显著/因果措辞，但结果段没有对应显著结果 |
| `promise_not_delivered` | argument_chain | 引言/摘要承诺机制或纵向分析，方法/结果未交付 |
| `circular_definition` | argument_chain | 构念定义与测量共用同一组词 |
| `control_group_missing` | method_design | 方法描述干预，但没有对照/等待组或组内比较 |
| `trial_registration_missing` | academic_compliance | 随机/试验设计但未见注册/预注册声明 |

审核后如要把候选导入工作文件，再运行：

```bash
python3 "$SKILL_DIR/scripts/quality_review.py" \
  --input /input --work /app/work --output /app/output \
  --merge --min-confidence medium
```

`--merge` 只追加不覆盖：已存在的 ID/引文不重复；新候选编号为 `A01...`，
**一律写成 `status=needs_recheck`**，并保留 `rule_status` 说明规则的原始判定。
低置信度候选只留在 `work/rule-review.json`。模型必须逐条确认（改
`status=confirmed`）、降级或删除；不得原样把全部候选当最终结论。
直接用 `--merge` 而不复核，报告中必须说明这是未复核候选。

空输入、缺方法段、缺结果段的降级由脚本保证：`engine_status` 分别为
`input_error` / `partial`，对应 facet 写 `not_checked` 并给出 reason，
findings 为空或只覆盖可判定部分，进程仍 `exit 0`。三路 synthetic 自测：

```bash
python3 "$SKILL_DIR/scripts/quality_review.py" --selftest
```

自测只使用临时目录中的 synthetic 文本（正常稿件、空输入、缺方法/结果段），
不写入正式答案；失败时退出码为 1，并打印每个 case 的缺失规则/字段。

#### 2.2 模型分面审查（约 50% 预算）

按下面六类逐面阅读并记录。**每一面都先写覆盖状态，再写发现**；没有
问题的面要给出“查了什么、为何通过”的证据，而不是留空。

| facet | 必查内容 | 典型证据 |
|---|---|---|
| `fact_consistency` | 摘要/正文/图表/结论的数据与术语是否一致；数值、单位、样本量、方向是否冲突 | 两处原文 + 各自 locator |
| `citation_support` | 每处引用是否支持所在论断；被引文献主张、设计与本文论断是否匹配；参考文献元数据是否完整 | 论断原文 + 文献原句/条目 + claim-source 行 |
| `method_design` | 对照、随机化、盲法、样本量与抽样、纳排标准、混杂控制、重复与随机化说明 | 方法段原文 + 缺失项说明 |
| `statistical_reporting` | 效应量、置信区间、检验统计量、p 值口径、多重比较、缺失数据处理、事后分析 | 结果段原文 + 应报未报量 |
| `argument_chain` | 假设→方法→结果→结论是否闭合；因果/相关混淆、外推边界、循环论证、替代解释 | 结论句 + 支持/不支持它的证据 |
| `academic_compliance` | 伦理、知情同意、利益冲突、数据/代码可用性、图表版权与来源、目标期刊规范 | 声明缺失或不合规的原文/位置 |

分面规则：

1. **先定位再判断**。任何问题都要能指到 `path` + locator（段落号、
   行号、页码、章节名或图表号）并复制逐字原文；不能逐字复制的内容
   不算已确认问题，写入 `uncertainties`。
2. **区分四类**：
   - `error`：事实错误、数据冲突、引用与论断错配、方法描述自相矛盾；
   - `insufficient_evidence`：材料未报告、无法判断、需要补充证据；
   - `norm_violation`：伦理/数据声明/引用格式等规范问题；
   - `suggestion`：可改进但不构成错误的写作或报告建议。
3. **严重度与影响匹配**：`critical` 只给会改变主要结论或涉及学术
   不端的问题；`major` 给影响可信度/可复现性的问题；`minor` 给局部
   表述或格式问题；`info` 给建议。每条 finding 写一句 `impact`。
4. **误报检查**：每条 finding 必填 `false_positive_check`，至少核对
   以下之一：学科写作惯例、目标期刊格式差异、作者明确声明的取舍、
   方法本身可接受但报告不全。无法排除时把 finding 标
   `status=needs_recheck` 并写入 `uncertainties`。
5. **建议要可执行**：`fix` 必须写“改哪句/哪段 + 改成什么口径 +
   需要补什么证据”，不能只写“建议完善”。
6. **不硬凑问题**：这一面确实没有发现时，`checks[]` 写
   `status=pass`、`detail` 说明查过的具体位置与判定依据；不要为了
   数量编造缺陷，也不要把“未报告”写成“错误”。

### 第 3 步：写 `work/review-findings.json`（与第 2 步同步）

按 `templates/work.schema.json` 写结构化工作文件。必需字段：
`findings`, `facets_checked`, `review_status`, `evidence`。建议同时写
`checks`, `claim_source_matrix`, `claim_inventory`, `cross_section_checks`,
`fulltext_coverage`, `facet_subchecks`, `coverage_summary`, `not_checked`,
`verdict`, `uncertainties`, `rule_review`, `structure_gaps`。

若用 `--merge` 导入了规则候选：逐条围绕 `rule_id` 与 `confidence` 回原文
核对；确认的改为 `status=confirmed` 并补齐 `impact`；不成立的删除并在
`uncertainties` 记录驳回原因；改写的保留 `rule_id`/`rule_status` 便于复核。
最终 ID 可保留 `A01...` 或统一重编号，但必须与 `output/findings.json`、
`output/evidence-map.json` 和报告完全一致。规则没有覆盖的面，仍需模型
按 2.2 的六面协议补查，`rule_review` 不是“已全部检查”的证明。

finding 的最小结构（字段名必须一致）：

```json
{
  "id": "Q01",
  "facet": "citation_support",
  "locator": {"path": "manuscript.md", "locator": "P2 / References P12"},
  "original_quote": "Prior work suggests mindfulness training may improve attention [1].",
  "severity": "major",
  "type": "error",
  "status": "confirmed",
  "impact": "The cited survey cannot support an interventional claim.",
  "fix": "Rewrite P2 as an association or cite an intervention study; keep [1] only for the survey claim.",
  "false_positive_check": "Checked against discipline convention: a cross-sectional survey still cannot support an intervention claim.",
  "evidence": [
    {"path": "manuscript.md", "locator": "P2", "quote": "Prior work suggests mindfulness training may improve attention [1]."},
    {"path": "manuscript.md", "locator": "P12", "quote": "A cross-sectional survey of meditation experience and self-rated attention (n=210). No intervention was tested."}
  ]
}
```

逐字证据规则：

- `original_quote` 与 `evidence[].quote` 必须能在对应 `path` 的材料中
  逐字找到（允许空白折叠、省略号分段，不允许改写或翻译）；
- `locator` 至少给出段落号/行号/页码/章节之一；引用类问题同时给
  论断位置和文献条目位置；
- 同一问题需要多处原文才能成立时，把多处都放进 `evidence[]`；
- 无法逐字核对的候选问题不要放进 `findings`，放进 `uncertainties`
  并标 `needs_recheck`。

### 第 4 步：claim-source 对照（`citation_support` 必做）

对正文中每一处引用（或每个引用键/编号），至少给一行
`claim_source_matrix`：

```json
{
  "claim_id": "CS01",
  "claim": "Mindfulness training may improve attention.",
  "claim_locator": "manuscript.md P2",
  "source": "[1] / reference P12",
  "cited_source": "[1] / reference P12",
  "source_statement": "A cross-sectional survey of meditation experience and self-rated attention (n=210). No intervention was tested.",
  "source_locator": "manuscript.md P12",
  "verdict": "none",
  "note": "Design mismatch: no intervention and no training manipulation."
}
```

`source` 是 finalize 引擎要求的字段，写被引文献/条目标识（可与 `cited_source` 相同）；
`verdict` 只能是 `full`（直接支持）、`partial`（部分支持/需限定）、
`none`（不支持或支持的是另一主张）、`unverifiable`（材料不足）。
同一文献支撑多个不同论断时分别建行。矩阵行与 findings 可以多对一：
一个矩阵行可以支撑一条 finding，也可以只是“已核对且 full”的记录。

### 第 5 步：覆盖矩阵与结果状态

在 `facets_checked` 里逐面记录（六面都要有）：

```json
{"facet": "statistical_reporting", "status": "checked",
 "items": ["effect size", "95% CI", "test statistic", "missing-data handling"],
 "evidence": ["Results P7-P8"]}
```

`status` 取 `checked|partial|not_applicable|not_checked`。`partial`、
`not_checked` 与 `not_applicable` 都要在 `reason` 说明原因；例如
“材料未提供图 1，图表一致性无法核对”。`review_status` 取
`pass|issues|needs_recheck|not_checked`，表示稿件结论，不是流程状态：
有 confirmed finding 用 `issues`；只有待复核问题用 `needs_recheck`；
材料不足无法审查用 `not_checked`；确实未发现问题才用 `pass`。

### 第 6 步：写报告（`output/review-report.md`）

按 `templates/review-report-template.md` 的骨架写，至少包含：

1. **审查元信息**：case_id、稿件版本/文件、审查范围、材料清单与缺口；
2. **方法与覆盖**：六面状态表（checked/partial/not_checked）、每面查了
   什么、用什么证据；
3. **结论摘要**：总体判断（接受/小修/大修/无法判断）及理由，最多 5 条；
4. **问题清单**：按严重度分组，表格列 `ID | facet | 位置 | 严重度 |
   类型 | 问题 | 修改建议`；随后逐条给出原文引文、impact、
   误报检查与证据 locator；
5. **引用-主张对照**：claim-source 矩阵（至少覆盖所有
   citation_support findings 涉及的引用）；
6. **错误/证据不足/建议分栏**：把三类分开，避免把建议写成缺陷；
7. **待复核与未检查**：逐项写原因、影响和需要谁补什么材料；
8. **可复核性**：locator 约定、版本/哈希（若有）、运行与工具限制。

报告要能独立阅读：每条 finding 的 ID 必须同时出现在 `findings.json`
与报告中；报告中的引文必须与 `findings.json` 一致。不要写“总体
结构完整、论证较充分”这类无法交办的整篇评语。

同时写：

- `output/review-report.md`（主交付物，必须非空）
- `output/findings.json`（findings 的独立导出，字段与工作文件一致）
- `output/evidence-map.json`（evidence 与 claim-source 的合并索引）
- 若 `output_contract` 有自定义键：在 result.json 顶层保留原键名，
  并写 `output/results/<key>.json`（该目录会被 finalize 登记）。

### 第 7 步：自检（finalize 前必须运行）

```bash
python3 "$SKILL_DIR/scripts/review_gate.py" \
  --input /input --work /app/work --output /app/output
```

自检会检查：六面覆盖、finding 子字段、逐字证据、claim-source 矩阵、
报告章节与 ID 一致性、review_status/not_checked 一致性、契约键
产物。输出 `output/self-check.json`。有 `problems` 时先修工作文件或
报告，再重跑；不要带着未解决项 finalize。`warnings` 需在报告的
“限制”里说明。finalize 之后**再运行一次** `review_gate.py --apply`：
若仍有 problems，会把 `result.json` 的 `status` 降为 `partial` 并写入
`self_check` uncertainty，避免把不合格结果标成 completed。

### 第 8 步：finalize（约 20% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/review-findings.json`（或 JSON 主产物），检查必填字段
   `findings`, `facets_checked`, `review_status`, `evidence`；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/review-report.md` 与 `output/findings.json`,
   `output/evidence-map.json` 的真实文件；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `completed|partial|input_error`。有缺字段、证据不匹配、
主产物缺失或执行失败时用 `partial`；material 不可读时用 `input_error`。
若 finalize 退出异常或 `output/result.json` 缺失，先检查错误原因；
不要手工伪造 completed。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`、`profile`；
- `deliverable`：`output/review-report.md` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings` 的投影（claim_id/text/evidence）；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote/claim 映射；
- `artifacts[]`：主产物、`output/findings.json`、
  `output/evidence-map.json`、`output/rule-review.json`（规则扫描运行证据）
  与 `output/self-check.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、
  `contract_coverage`。

`output/rule-review.json` 是辅助运行证据，**不是**审查结论或主产物；
其 `findings` 只是候选。`result.json` 的 `claims[]` 与报告必须来自
模型核验后的 `work/review-findings.json`。

若 `/input/case.json` 的 `output_contract` 含本题特有键（例如
`review_report`、`findings`、`evidence_map`、`status`），必须：

1. 在 `result.json` 顶层保留原键名（finalize 会做别名映射）；
2. 同时写 `output/results/<key>.json` 或把 canonical 字段/artifact
   路径填入该键；
3. 查看 `contract_coverage`，任一 `present=false` 都视为交付不完整，
   写入 `uncertainties` 并降级 `partial`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G0 | 规则扫描 | 已运行 `scripts/quality_review.py`；`work/rule-review.json` 存在且 `engine_status` 如实；每条候选有确认/改写/删除记录；不得声称脚本结论即最终结论 |
| G0b | 全文分段 | `fulltext_coverage` 记录章节/段落/句子/字数与各章节主张数；不得只审摘要或短摘录 |
| G0c | 具体审稿面 | `facet_subchecks` 覆盖结构、摘要-正文、术语、引用、设计/分配、样本量、统计、因果、伦理、数据、利益冲突、注册；缺失项有 reason |
| G1 | 主产物 | `output/review-report.md` 存在、非空、含 5 个必备章节 |
| G2 | 必填字段 | `findings`, `facets_checked`, `review_status`, `evidence` 齐全且非空 |
| G3 | 定位与原文 | 每条 finding 有 path+locator+逐字 `original_quote`；多证据逐条可查 |
| G4 | 分面覆盖 | 六类检查项都有状态；`partial/not_checked/not_applicable` 有 reason |
| G5 | 类型与严重度 | `type` ∈ error/insufficient_evidence/norm_violation/suggestion；`severity` ∈ critical/major/minor/info；`fix` 可执行 |
| G6 | 误报 | 每条 finding 有 `false_positive_check`；无法排除的标 needs_recheck |
| G7 | 引用对照 | 所有 citation_support findings 都能在 claim_source_matrix 找到对应行；verdict 合法 |
| G8 | 结果状态 | `review_status` 合法；`not_checked` 与实际未查面一致；不把未查写成通过 |
| G9 | 报告一致 | 报告中出现每条 finding ID 与对应引文；不写整篇泛评 |
| G10 | 契约/产物 | result.json 可解析，case_id 与输入一致；所有 artifact 真实存在；contract_coverage 无 false |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 六面覆盖、逐条问题可定位、原文准确、严重度与类型合理、误报可控 | `output/review-report.md` + `findings.json` |
| 使用增益 | 可复跑的分面步骤、检查项、证据索引与日志；同条件可对照 | `output/self-check.json` + `evidence-map.json` |
| 创新增益 | 可定位到原句的问题清单、claim-source 逐条对照、严重度分级、误报抑制 | `claim_source_matrix` + `references/review-rules.md` |
| 可靠体验 | 空材料/缺图表/解析失败/超长稿件的降级与待复核 | `uncertainties` + `not_checked` + `result.json` |
| 生态贡献 | SKILL.md、schema、报告模板、规则、synthetic 示例可复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；不生成伪问题/伪结论；`quality_review.py` 仍写 `engine_status=input_error` 且 exit 0 |
| 只有摘要/部分材料 | 只审可确认部分；正文标“基于摘要”；`status=partial` |
| 缺方法段/结果段 | 规则扫描 `engine_status=partial`，对应 facet 为 `not_checked` 并写 reason；只审可判定条目，不把缺失写成“通过” |
| 规则扫描异常 | `work/rule-review.json` 记录 `engine_status=error` 与 traceback；模型改为人工分面审查，finalize 如实写 `partial` |
| 规则候选疑似误报 | 回到原文与学科惯例核对；删除、降为 `suggestion` 或标 `needs_recheck`，并在 `uncertainties` 记录原因；不得为凑数保留 |
| 图表/补充材料缺失 | 写入 `uncertainties` 与 `not_checked`，不把“未见”判成“错误” |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该 finding 或标 `needs_recheck` |
| 引用元数据不全 | 记 `insufficient_evidence`；不补造作者/年份/DOI |
| 统计量缺失 | 区分“未报告”（insufficient_evidence）与“数值冲突”（error） |
| 学科惯例/期刊差异 | 先做 `false_positive_check`；无法确认时降为 suggestion 或待复核 |
| 稿件超长 | 按 facet 分块读取；在报告中写清覆盖的章节范围与未覆盖部分 |
| 代码执行失败 | 保留 `execution.log` 与失败原因；不伪造输出 |
| 权限/接口/格式错误 | 写 `uncertainty` 与已完成范围；建议人工接管 |

## 最小示例（synthetic，不是正式答案）

工作文件片段：

```json
{
  "case_id": "SYNTH-REVIEW",
  "mode": "quality-review",
  "summary": "Synthetic only.",
  "findings": [
    {
      "id": "Q01",
      "facet": "method_design",
      "locator": {"path": "synthetic.md", "locator": "P1"},
      "original_quote": "synthetic source sentence",
      "severity": "major",
      "type": "error",
      "status": "confirmed",
      "impact": "Synthetic impact.",
      "fix": "Synthetic fix targeting P1.",
      "false_positive_check": "Checked against synthetic convention.",
      "evidence": [
        {"path": "synthetic.md", "locator": "P1", "quote": "synthetic source sentence"}
      ]
    }
  ],
  "facets_checked": [
    {"facet": "method_design", "status": "checked", "items": ["control"], "evidence": ["P1"]}
  ],
  "claim_source_matrix": [],
  "review_status": "issues",
  "not_checked": [],
  "evidence": [
    {"path": "synthetic.md", "locator": "P1", "quote": "synthetic source sentence"}
  ]
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。

## 版本与限制

- 版本 `1.3.0`：六面审查协议 + finding 子字段 + claim-source 矩阵 +
  `scripts/quality_review.py` 全文分段/主张清单/跨章节核对/具体审稿面/
  规则扫描（抽取/候选/覆盖建议/三路自测）+ `scripts/review_gate.py` 自检；
  模型写内容与最终判断，脚本只做抽取、候选生成和可核对性审计。
- 规则扫描是文本模式匹配：只能发现可逐字定位的候选，不能判断语义蕴含、
  研究设计是否真的正确，也不能替代同行评审。所有 `Axx` 候选必须复核。
- 不联网：只使用 `/input` 提供的材料或导出；不伪造在线检索结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断；引用是否真正
  支持论断仍需作者/审稿人复核。
- 示例是 synthetic 接口样例，不是正式案例答案。
- 材料不足、扫描质量差、图表缺失时，报告只覆盖已核对范围，并把
  未覆盖部分显式列为 `not_checked`。
