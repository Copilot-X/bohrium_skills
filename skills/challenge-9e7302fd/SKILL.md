---
name: review-methods-stats-review-skill
description: 研究方法与统计审查：研究设计、变量定义、统计假设、样本量、多重比较与报告完整性。交付五检查面、统计决策规则、报告清单与可执行质量闸门，保留来源/定位与不确定性，并生成含官方 reading/writing 字段的 result.json。Methods and statistics review with five-facet coverage, a verbatim-evidence quality gate and an honest result envelope. Use for 研究方法与统计审查、methods-stats-review、review、证据绑定、结果复核.
---

# 研究方法与统计审查（review track · methods-stats-review）

## 用途与触发

task.md 要求“研究方法、统计假设、样本量、多重比较与报告完整性”，或 `/input/case.json` 给出 `review` / `methods-stats-review`
相关字段时使用。本题属于 **方法与统计审查** 类任务，核心交付是
**检查面、统计决策规则与报告清单**，不是一段泛泛建议。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代
同行评审/机构审查/临床决策。材料不足时只交付可确认部分，并把缺口写成
`uncertainty`，不要补造。

核心原则：**有输入、有依据、有产物、有边界**。脚本负责盘点材料、建立
工作区、校验证据/字段/产物并组装 `result.json`；模型负责判断、筛选、
分析或写作。脚本不会替你生成候选、结论或审查意见。

### 五检查面契约（必须全部覆盖）

| facet（写进 `facets_checked[].facet`） | 必查内容 | 典型 check 项 |
|---|---|---|
| `design` | 设计类型与研究问题匹配；对照、分组、随机化、盲法、偏倚控制；主要终点/假设是否预设 | design_vs_question, randomization, blinding, control_group, confounding |
| `variables` | 暴露/结局/协变量/分组变量的定义、测量、编码、单位、切点、缺失值处理；变量类型与模型匹配 | variable_definition, measurement, coding, unit, missing_data |
| `statistics` | 检验方法与数据结构/分布/独立性匹配；配对与否；正态/方差齐性；多重比较；重复测量；交互；敏感性分析 | normality, paired_vs_unpaired, multiple_comparison, repeated_measures, interaction, sensitivity |
| `sample_size` | 样本量估算依据、效应量来源、α/把握度；实际入组 vs 估算；失访/剔除 | power_basis, effect_size_source, attrition, achieved_vs_planned |
| `reporting` | 效应量与置信区间、精确 p、统计量与自由度、图表与正文数值一致；报告规范完整性 | effect_and_ci, exact_p, df, n_consistency, table_vs_text |

每条 `facets_checked[]` 必须有 `facet`、`status`
（`checked|not_checked|insufficient_material|not_applicable`）和
`items[]`（具体检查项）；`checked` 但 `items=[]` 视为未覆盖。
每个检查面至少要有 1 条 `checks[]` 记录，未检查不等于通过。

### 审查结论分类

- `severity`：`critical|major|moderate|minor|info`（不要一律写 major）。
- `type`：`error|insufficient_evidence|reporting_gap|suggestion|compliance|statistical|design|variable|sample_size`。
- `status`：`open|resolved|needs_recheck|not_applicable`。
- 把“作者做错了”（error）、“作者没有报告”（insufficient_evidence/reporting_gap）
  与“可以更好”（suggestion）分开；没有原文依据的结论写 `not_applicable`
  或放入 `uncertainties`。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：本题的具体问题、范围、输出要求与证据范围。
- `/input` 下的材料：`.md/.txt/.json/.csv/.pdf/.docx/.tex/.yaml` 等。
  `scripts/parsers.py` 会提取文本、JSON 数据与 CSV 行；无文字层 PDF
  尝试 OCR，失败如实记录。
- 可选：已有工作文件、术语表、数据字典、候选列表、查询日志。

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
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `scripts/review_quality_gate.py` | **提交前质量闸门**：五检查面、报告清单、决策规则、逐字证据与报告章节 |
| `scripts/finalize_with_gate.py` | **finalize 唯一入口**：先跑闸门，再调用 `run_case.py finalize`；闸门失败时降级为 `partial` |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/review-findings.json` 的字段契约（含枚举） |
| `templates/review-report.template.md` | 审查报告章节骨架（synthetic 占位符） |
| `references/review-rules.md` | 五检查面、统计决策规则、严重度与失败模式 |
| `examples/synthetic-source.md` | 与示例 quote 对应的 synthetic 材料（非正式案例） |
| `examples/synthetic-input.json` | 五检查面齐全的 synthetic 工作文件（非正式案例） |
| `examples/synthetic-review-report.md` | 与工作文件对应的 synthetic 报告（非正式答案） |

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

先读 `work/materials.json` 与 `task.md`，确认材料边界和缺失项。

### 第 2 步：写 `work/review-findings.json`（约 60% 预算）

按 `templates/work.schema.json` 写结构化工作文件。至少包含
`findings`, `facets_checked`, `decision_rules`, `reporting_checklist`, `evidence`，并为每条关键判断/候选/步骤绑定 `evidence`：

```json
{
  "case_id": "SYNTH-REVIEW",
  "mode": "methods-stats-review",
  "summary": "Synthetic example only.",
  "findings": [
    {
      "id": "S001",
      "facet": "statistics",
      "locator": {
        "path": "synthetic.md",
        "locator": "P1"
      },
      "original_quote": "synthetic source sentence",
      "severity": "major",
      "type": "insufficient_evidence",
      "status": "open",
      "fix": "synthetic fix",
      "assumption_checked": [
        "normality"
      ],
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "checks": [
    {
      "item": "synthetic check",
      "status": "pass",
      "detail": "synthetic only"
    }
  ],
  "verdict": "major_revision",
  "evidence": [
    {
      "path": "synthetic.md",
      "locator": "P1",
      "quote": "synthetic source sentence"
    }
  ],
  "uncertainties": [],
  "facets_checked": [
    {
      "facet": "statistics",
      "status": "checked",
      "items": [
        "normality",
        "multiplicity"
      ]
    }
  ],
  "decision_rules": [
    {
      "rule": "paired vs unpaired",
      "condition": "independent samples",
      "decision": "synthetic only"
    }
  ],
  "reporting_checklist": {
    "n": "reported",
    "effect": "missing",
    "ci": "missing",
    "exact_p": "reported",
    "df": "missing",
    "itt": "not_applicable"
  }
}
```

同时写以下产物：

- `output/review-report.md`
- `output/findings.json`
- `output/evidence-map.json`

- `output/review-report.md` 是主交付物，必须真实存在且非空。
- 所有 `quote` 必须能在对应 `path` 的材料中逐字（允许空白折叠/省略号分段）
  找到；找不到的写入 `uncertainties`，不要改写成“大意”。
- 区分做错与未报告；前提检查。

**报告至少包含这些章节**（中英文均可，见
`templates/review-report.template.md`）：范围与材料、研究设计、变量定义、
统计方法、样本量与检验效能、结果报告、逐条发现（编号/位置/原文/严重度/
类型/修改建议）、证据与定位、待复核与不确定性、复现边界。

### 第 2b 步：运行质量闸门（约 5% 预算，finalize 前必须做）

```bash
python3 "$SKILL_DIR/scripts/review_quality_gate.py" \
  --work /app/work --output /app/output --input /input --json
```

闸门会检查：五个 `facet` 是否齐全、每个检查面是否有 `checks[]`、
`decision_rules` 是否 ≥4、`reporting_checklist` 是否覆盖 n/effect/ci/
exact_p/df/missing_data/multiplicity、每条 finding 是否有
`locator.path+locator` 与逐字 `original_quote`、`review-report.md`
章节是否齐全。退出码 0 且 `work/review-gate.json` 的 `ok=true` 才进入
finalize；否则按 `problems[]` 逐条修复。无法修复的写入 `uncertainties`，
不要删掉检查面或伪造原文。

### 第 3 步：finalize（约 30% 预算，必须用带闸门的入口）

```bash
python3 "$SKILL_DIR/scripts/finalize_with_gate.py" \
  --input /input --work /app/work --output /app/output
```

`finalize_with_gate.py` 会先运行闸门，再调用
`scripts/run_case.py finalize`；如果闸门 `ok=false` 或 gate 文件缺失，
即使 `fam_tracks` 的信封检查通过，它也会把 `status=completed` 降为
`status=partial`，并写入 `review_gate` uncertainty。不要绕过这个入口
直接调用 `run_case.py finalize`，否则闸门结论不会进入结果。

入口会：

1. 读取 `work/review-findings.json`（或 JSON 主产物），检查必填字段
   `findings`, `facets_checked`, `decision_rules`, `reporting_checklist`,
   `evidence`, `checks`, `verdict`；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/review-report.md` 与 `output/findings.json`,
   `output/evidence-map.json`, `output/review-gate.json` 的真实文件；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `completed|partial|input_error`。有缺字段、证据
不匹配、主产物缺失或执行失败时用 `partial`。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/review-report.md` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings`/`candidates`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote；
- `artifacts[]`：主产物与 `output/findings.json`, `output/evidence-map.json`,
  `output/review-gate.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`。

`output/review-gate.json` 是闸门真实输出：`ok/problems/warnings/coverage`。
`ok=false` 时 finalize 仍可运行，但应在 `uncertainties` 中保留闸门问题；
不得为了闸门通过而删掉检查面、缩短 quote 或伪造定位。

若 `/input/case.json` 的 `output_contract` 含本题特有键（例如
`cleaned_table`、`quality_report`、`rule_log`、`original_snapshot`），必须：

1. 在 `result.json` 顶层保留原键名；
2. 优先写同名产物 `output/<field>.json/.csv/.md`，或把对应 canonical
   字段/artifact 路径填入该键；
3. finalize 会检查 `contract_coverage`，未覆盖的键会降级 `partial` 并写入
   `uncertainties`；不要只写 `status/result/claims` 而忽略原契约字段。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/review-report.md` 存在、非空、章节齐全、与任务口径一致 |
| G2 | 必填字段 | `findings`, `facets_checked`, `decision_rules`, `reporting_checklist`, `evidence`, `checks`, `verdict` 齐全 |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查 |
| G4 | 边界 | 未报告、冲突、失败、未检查项写入 `uncertainties` |
| G5 | 五检查面 | `facets_checked` 覆盖 design/variables/statistics/sample_size/reporting；每个面有 `checks[]` |
| G6 | 统计专项 | 假设/检验匹配；决策规则 ≥4；报告清单覆盖 n/effect/ci/exact_p/df/missing_data/multiplicity |
| G7 | 产物 | 声明的每个 artifact 路径真实存在 |
| G8 | 质量闸门 | `output/review-gate.json` 存在，`ok=true`；未通过项逐条写明 |
| G9 | 契约 | result.json 可解析，case_id 与输入一致 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 研究方法、统计假设、样本量、多重比较与报告完整性；统计判断有依据；假设/检验匹配；缺失报告不补造 | `output/review-report.md` + evidence |
| 使用增益 | 可复跑的查询/步骤/规则/日志，以及同条件对照 | `output/findings.json`, `output/evidence-map.json` |
| 创新增益 | 本题专项方法（methods-stats-review）及其与基线方案的边界 | `references/review-rules.md` + `output/review-report.md` |
| 可靠体验 | 空结果、冲突、缺字段、权限/格式错误的识别与降级 | `uncertainties` + `output/result.json` |
| 生态贡献 | SKILL.md、schema、规则、synthetic 示例可被他人复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；不生成伪候选/结论/审查意见 |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要”；`status=partial` |
| 某个检查面材料不足 | `facets_checked[].status=insufficient_material`，写 `items` 与缺口，不补造 |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 统计输出缺失 | `reporting_checklist` 标 `missing`；finding 类型用 `reporting_gap` |
| 检验/假设不匹配 | 在 `decision_rules` 写清条件与决策，finding 类型用 `statistical` |
| 数字/统计不一致 | 以数据/原文为准；报告、表格与 `findings.json` 对齐 |
| 闸门 `ok=false` | 按 `problems[]` 修复；修不了写入 `uncertainties`，不要删检查面 |
| 代码执行失败 | 保留 `execution.log` 与失败原因；不伪造输出 |
| 权限/接口/格式错误 | 写 `uncertainty` 与已完成范围；建议人工接管 |

## 最小示例（synthetic，不是正式答案）

```json
{
  "case_id": "SYNTH-REVIEW",
  "mode": "methods-stats-review",
  "summary": "Synthetic example only.",
  "findings": [
    {
      "id": "S001",
      "facet": "statistics",
      "locator": {
        "path": "synthetic.md",
        "locator": "P1"
      },
      "original_quote": "synthetic source sentence",
      "severity": "major",
      "type": "insufficient_evidence",
      "status": "open",
      "fix": "synthetic fix",
      "assumption_checked": [
        "normality"
      ],
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "checks": [
    {
      "item": "synthetic check",
      "status": "pass",
      "detail": "synthetic only"
    }
  ],
  "verdict": "major_revision",
  "evidence": [
    {
      "path": "synthetic.md",
      "locator": "P1",
      "quote": "synthetic source sentence"
    }
  ],
  "uncertainties": [],
  "facets_checked": [
    {
      "facet": "statistics",
      "status": "checked",
      "items": [
        "normality",
        "multiplicity"
      ]
    }
  ],
  "decision_rules": [
    {
      "rule": "paired vs unpaired",
      "condition": "independent samples",
      "decision": "synthetic only"
    }
  ],
  "reporting_checklist": {
    "n": "reported",
    "effect": "missing",
    "ci": "missing",
    "exact_p": "reported",
    "df": "missing",
    "itt": "not_applicable"
  }
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。

## 版本与限制

- 版本 `1.1.0`：共享引擎 + `fam_tracks`；新增可执行质量闸门
  `scripts/review_quality_gate.py`、带闸门的 finalize 入口
  `scripts/finalize_with_gate.py`、五检查面契约、报告清单与决策规则
  枚举、逐字证据校验、报告章节检查；`required_fields` 增加 `checks/verdict`。
- 不联网：检索/追踪/数据库类任务只使用 `/input` 提供的材料或导出；
  不伪造在线查询结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断；统计判断仍需模型
  结合原文与方法学常识完成。
- 示例是 synthetic 接口样例，不是正式案例答案。
