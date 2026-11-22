---
name: experiment-parameter-optimization-skill
description: 实验参数优化建议：参数空间、历史数据、约束、推荐值与验证实验。用可执行脚本读取真实 CSV/JSON，计算敏感性/趋势、可行域与推荐区间，输出 parameter-analysis.json 与可读报告；交付参数空间、可行域、推荐值、约束、风险与验证/停止条件，保留逐字证据与不确定性，并生成含官方 reading/writing 字段的 result.json。Use for 实验参数优化建议、parameter-optimization、experiment、敏感性分析、约束感知推荐、证据绑定、结果复核.
---

# 实验参数优化建议（experiment track · parameter-optimization）

## 用途与触发

task.md 要求“参数空间、历史数据、约束、推荐值与验证实验”，或 `/input/case.json` 给出 `experiment` / `parameter-optimization`
相关字段时使用。本题属于 **实验参数优化** 类任务，核心交付是
**参数空间、历史数据、约束、推荐值与验证实验**，不是一段泛泛建议。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代
同行评审/机构审查/临床决策。材料不足时只交付可确认部分，并把缺口写成
`uncertainty`，不要补造。

核心原则：**有输入、有依据、有产物、有边界**。脚本负责盘点材料、读取真实
表格、建立可行域、计算敏感性/趋势、校验证据/字段/产物并组装
`result.json`；模型负责判断、取舍、风险分析与最终复核。脚本不会替你编造
候选、测量值、文献或最优解。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。若含
  `parameters`/`responses`/`objective`/`constraints`，脚本会优先使用。
- `/input/task.md`：本题的具体问题、范围、输出要求与证据范围。
- `/input` 下的材料：`.md/.txt/.json/.csv/.pdf/.docx/.tex/.yaml` 等。
  参数/结果表优先读取 `.csv` 与结构化 `.json` 数组；`scripts/parsers.py`
  会为证据审计提取文本、JSON 数据与 CSV 行。
- 可选：已有工作文件、数据字典、约束表、候选列表、查询日志。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/parameter_optimizer.py` | **本题专项脚本**：读取真实 CSV/JSON，识别参数/响应，解析约束，计算敏感性/趋势、可行域、推荐区间与验证计划；写 `output/parameter-analysis.json` 与 `.md` |
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/experiment-plan.json` 的字段契约 |
| `references/experiment-rules.md` | 实验参数优化 的可执行规则、计算口径与失败模式 |
| `examples/synthetic-input.json` | synthetic 输入示意（非正式案例） |

## 工作流

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，提取文本/JSON/CSV，写 `work/inventory.json` 与
   `work/materials.json`；
2. 生成 `work/experiment-plan.json` 骨架（已存在则不覆盖）；
3. 写 `output/README-partial.md` 与 provisional `result.json`。

先读 `work/materials.json` 与 `task.md`，确认材料边界和缺失项；特别确认
是否存在参数/结果表、约束材料与验证预算。

### 第 2 步：运行 `scripts/parameter_optimizer.py`（约 20% 预算）

```bash
python3 "$SKILL_DIR/scripts/parameter_optimizer.py" \
  --input /input --work /app/work --output /app/output
```

**何时必须运行**：

- `/input` 中存在 `.csv` 或含行数组的 `.json`（哪怕只有 1 行）时；
- 题面要求敏感性、趋势、推荐区间、可行域或验证实验时；
- 数据看起来不足时也要运行：脚本会写出 `status=not_computable` 的真实记录，
  比口头声称“数据不足”更可复核。

**何时需要显式指定列**：自动识别只按列名启发式（temperature/time/loading/
yield/impurity/cost 等）。出现以下情况时，按题面或 `case.json` 显式传入：

```bash
python3 "$SKILL_DIR/scripts/parameter_optimizer.py" \
  --input /input --output /app/output \
  --parameters temperature_C,time_min --responses yield_pct,impurity_pct \
  --objective yield_pct:max
```

- 列名不含常见参数/响应词；
- 同名列有多个（如 `impurity_pct` 与 `impurity_ppm`）；
- 题面明确目标方向（max/min）而文本未写；
- 只想用一个指定数据文件：加 `--file history.csv`。

其他可调参数：`--tolerance`（推荐邻域的目标容差，默认 0.05）、
`--max-runs`（验证点上限，默认 12）、`--replicates`（中心重复，默认 2）。
报告中必须记录实际使用的参数，保证可复跑。

**脚本输出**：

- `output/parameter-analysis.json`：`status`、`dataset`（含数据指纹）、
  `detection`（参数/响应/目标）、`data_quality`、`constraints`、
  `feasible_domain`、`sensitivity`、`recommendations`、`tradeoffs`、
  `design`、`validation_plan`、`anomalies`、`not_computable`、`evidence`。
- `output/parameter-analysis.md`：与 JSON 对齐的可读报告。

**运行后必须逐项复核（不能直接照抄）**：

1. `detection.parameters/responses/objective`：角色是否与题面一致。误判时用
   显式 `--parameters/--responses/--objective` 重跑，并在报告中记录。若
   `objective.ambiguity` 非空（题面有多个最大化/最小化目标），必须显式选定
   主目标或写清多目标优先次序，不能默默沿用第一个匹配。
2. `data_quality`：缺失、非数值、重复参数组合与冲突重复。跳过行不能静默
   补值；`conflicting_duplicates` 必须原样报告。
2b. `dataset.record_type`：`measurements` 是实测/报告观测；`candidate_plans`
   是候选方案或预测值，置信度会被压到 `low`，必须写成“待验证方案”而不是
   实测结论；`contract_table`（价格/资源/预约表）不会进入敏感性计算，应作为
   约束与成本口径人工读取。只有合同表而没有观测表时脚本会返回
   `not_computable`，不要把报价表当历史数据。
3. `constraints.items`：`needs_review=true` 或 `source=text_pattern` 的条目
   是正则抽取的建议，必须回到 `path+locator+quote` 原文逐字核对后才能写进
   `work/experiment-plan.json`；`unmapped` 约束不得静默忽略，要写入
   `uncertainties` 并转人工确认。
4. `constraints.conflicts`：多个上限/下限时采用**最严格值**；较宽松值不能
   写成可执行边界。冲突必须进入风险与确认项。
5. `feasible_domain`：检查 `infeasible_rows` 与 `no_overlap`。推荐值必须同时
   落在观测区间与硬边界交集内。
6. `sensitivity`：`basis=feasible_subset` 优先于全体行；`n<5` 只能描述，
   不能当因果或预测保证。`design.confounded_pairs` 非空时，历史参数相关，
   必须用 one-at-a-time 或正交验证，不能把相关当因果。
7. `recommendations`：逐条检查 `support_rows` 是否真实、`recommended_value`
   是否在 `recommended_interval` 与 `hard_bounds` 内；`confidence=low` 的
   参数不得写成“已验证最优”。
8. `validation_plan`：`status=suggested_not_executed`，不是实测结果；
   `extrapolation=true` 的点必须标注为超出历史窗口的探测点。
9. `not_computable`：不得用常识填补。能补材料就补；不能补就原样进入
   `uncertainties`，状态用 `partial`/`input_error`。
10. `evidence`：CSV 行的逐字证据在 finalize 中按 **tab 分隔**校验（脚本已按
    `70\t10\t1.0\t76\t...` 输出）；JSON 行只有 locator，除非原文确有逐字
    片段，否则不要编造 quote。

脚本无法运行时：不要编造数值；手写一份
`output/parameter-analysis.json`，至少含 `status: "not_computable"`、
`reason`、`not_computable` 列表，并在 `work/experiment-plan.json` 的
`uncertainties` 中记录“脚本未运行”。

### 第 3 步：写 `work/experiment-plan.json`（约 40% 预算）

按 `templates/work.schema.json` 写结构化工作文件。必填字段为
`recommendations`, `parameters`, `evidence`, `validation_experiment`，
并建议写全 `parameter_space`, `constraints`, `history`,
`parameter_analysis`。每条关键判断/候选/步骤都要绑定 `evidence`
（`path + locator + quote`）。

```json
{
  "case_id": "SYNTH-EXPERIMENT",
  "mode": "parameter-optimization",
  "summary": "Synthetic example only.",
  "hypothesis": "Synthetic hypothesis",
  "variables": {
    "independent": ["Synthetic variable"],
    "dependent": ["Synthetic outcome"],
    "controls": ["Synthetic control"]
  },
  "steps": [
    {
      "id": "S1",
      "action": "Synthetic step",
      "parameters": {"temperature": "350 C"},
      "duration": "2 h",
      "materials": "Synthetic material"
    }
  ],
  "parameters": [
    {
      "name": "temperature",
      "value": "350",
      "unit": "C",
      "basis": "synthetic.md P1"
    }
  ],
  "risks": [
    {
      "risk": "Synthetic risk",
      "impact": "medium",
      "mitigation": "Synthetic mitigation"
    }
  ],
  "confirmation_items": ["Synthetic item to confirm"],
  "evidence": [
    {
      "path": "synthetic.md",
      "locator": "P1",
      "quote": "synthetic source sentence"
    }
  ],
  "uncertainties": [],
  "parameter_space": [
    {"name": "temperature", "range": [300, 400], "unit": "C"}
  ],
  "constraints": [
    {"constraint": "synthetic constraint", "source": "synthetic.md"}
  ],
  "history": [
    {"parameters": {"temperature": 350}, "outcome": 92}
  ],
  "recommendations": [
    {
      "parameters": {"temperature": 350},
      "expected": "high selectivity",
      "basis": "synthetic.md P1 + synthetic history row 1"
    }
  ],
  "validation_experiment": {
    "steps": ["synthetic validation"],
    "stop_condition": "confirmed or budget exhausted"
  },
  "parameter_analysis": {
    "script": "scripts/parameter_optimizer.py",
    "path": "output/parameter-analysis.json",
    "status": "synthetic placeholder, not a real analysis",
    "not_computable": []
  }
}
```

同时写以下产物：

- `output/experiment-report.md`（主交付物，必须真实存在且非空）
- `output/parameter-recommendations.json`
- `output/validation-plan.json`
- `output/risk-assessment.json`

`output/parameter-analysis.json` 与 `.md` 由专项脚本生成；若脚本未运行，按
第 2 步的降级方式手写，不得留空或伪造数字。

写工作文件时：

- `recommendations[]` 每条必须有 `parameters`、`expected`、`basis`，并尽量
  附 `support_rows`（来自脚本）与 `uncertainty`；无依据的推荐直接删掉。
- `parameters[]` 每个参数给出值、单位、硬边界与依据；`basis` 指向数据行或
  约束原文，不能只写“经验”。
- `constraints[]` 每条给出 `source` 与逐字 `quote`；文本正则抽取项必须先
  人工核对。**任何含 `quote` 的嵌套对象**（包括 `constraints`、
  `recommendations[].support_rows`、风险证据）都必须同时带 `path` 与
  `locator`，否则 finalize 会把它当成空定位的无效证据并降级为 `partial`。
- `validation_experiment` 必须含步骤、判读标准、停止条件（成功、失败、超预算、
  安全越界）以及每点重复/成本口径。
- 越界推荐不得写成可执行；不可行历史最优点只能作为“为什么不推荐”的证据。

### 第 4 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/experiment-plan.json`（或 JSON 主产物），检查必填字段
   `recommendations`, `parameters`, `evidence`, `validation_experiment`；
   并做 experiment track 专项检查（推荐 basis、参数范围越界、风险为空等）；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记主产物与 `output/parameter-recommendations.json`,
   `output/validation-plan.json`, `output/risk-assessment.json`,
   `output/parameter-analysis.json`, `output/parameter-analysis.md` 的真实文件；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `completed|partial|input_error`。有缺字段、证据
不匹配、主产物或声明产物缺失时用 `partial`；材料为空/不可读用
`input_error`。退出码只要写出可解析 `result.json` 就是 0，不得因缺字段
硬崩溃。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/experiment-report.md` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`、`preserved_facts[]`、`evidence_map[]`；
- `artifacts[]`：主产物、3 个结构化产物与参数分析产物；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`。

若 `/input/case.json` 的 `output_contract` 含本题特有键（例如
`feasible_domain`、`recommendations`、`tradeoffs`、`validation_plan`、
`constraints`），必须：

1. 在 `result.json` 顶层保留原键名；
2. 优先写同名产物 `output/<field>.json/.csv/.md`，或把对应 canonical
   字段/artifact 路径填入该键；
3. finalize 会检查 `contract_coverage`，未覆盖的键会降级 `partial` 并写入
   `uncertainties`。`parameter-analysis.json` 已按这些键组织内容，但必须
   转写到工作文件/产物并保留契约键名，不能只引用脚本路径。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/experiment-report.md` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `recommendations`, `parameters`, `evidence`, `validation_experiment` 齐全 |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查（CSV 用 tab 分隔行） |
| G4 | 边界 | 未报告、冲突、失败、未检查项写入 `uncertainties` |
| G5 | 专项 | 推荐值有依据；约束明确；给出验证/停止条件；推荐不越界 |
| G6 | 产物 | 声明的每个 artifact 路径真实存在且非空 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致，contract_coverage 全覆盖 |
| G8 | 参数分析 | `parameter-analysis.json` 状态诚实；`not_computable` 不被静默填补；异常已复核 |
| G9 | 可复跑 | `tests/test_parameter_optimizer.py` 的正常/空输入/缺列三路通过；同输入重跑一致 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 参数空间、历史数据、约束、推荐值与验证实验；推荐值有依据；约束明确；给出验证/停止条件 | `output/experiment-report.md` + evidence |
| 使用增益 | 可复跑的敏感性/趋势计算、约束过滤、验证点生成与日志 | `output/parameter-analysis.json`, `output/parameter-recommendations.json`, `output/validation-plan.json` |
| 创新增益 | 约束感知推荐、相关参数设计警告、异常最优点对照与停止判据 | `scripts/parameter_optimizer.py` + `references/experiment-rules.md` |
| 可靠体验 | 空结果、缺列、不可计算、约束冲突、重复冲突的识别与降级 | `not_computable` + `uncertainties` + `output/result.json` |
| 生态贡献 | SKILL.md、schema、规则、可执行脚本、测试与 synthetic 示例可复用 | `SKILL.md` + `templates/` + `references/` + `examples/` + `tests/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；脚本写 `not_computable` JSON；不生成伪候选/测量值/最优解 |
| 没有参数/结果表 | `not_computable`；报告说明缺什么表、需要哪些列，不把摘要文字当数据 |
| 只有候选方案/预测值 | 可做描述性比较并标 `candidate_plans`，置信度 `low`；不得写成实测最优 |
| 只有价格/资源/预约合同表 | `not_computable`；这些表用作成本/资源约束，不能当参数-响应历史 |
| 参数缺列 | 用显式 `--parameters` 重跑；仍缺失则 `not_computable` + `uncertainties`，不要静默改用其他列 |
| 单元格缺失/非数值 | 记录 skipped 行与原因；不得无说明插值；推荐只基于完整行 |
| 约束冲突（设备/材料/安全） | 采用最严格值并列入确认项；较宽松值不得作为可执行边界 |
| 历史最优点越界 | 不推荐该点；保留冲突证据，推荐可行域内最优点，并说明差距 |
| 无可执行可行点 | `not_computable`；列出冲突约束，建议放宽/换材料/转人工，不硬凑推荐 |
| 重复参数组合结果矛盾 | 原样列出两行；未查明原因前不得平均或只取有利值 |
| 历史参数相关/非正交 | 用 one-at-a-time 或正交验证；不把相关性写成因果 |
| 证据 quote 找不到 | 回到原文重摘；CSV 用 tab 分隔行；JSON 行用 locator；无法逐字则标 `uncertainty` |
| 脚本异常 | 脚本仍写合法 JSON/Markdown 并退出 0；查看 traceback，不伪造数值 |
| 声明产物缺失 | 写诚实的 `not_computable` 占位产物；finalize 标 `partial`，不得让路径悬空 |
| 权限/接口/格式错误 | 写 `uncertainty` 与已完成范围；建议人工接管 |

## 最小示例（synthetic，不是正式答案）

见 `examples/synthetic-input.json`。示例只说明接口，不冒充正式案例结果；
不要复制其中的实体或数值。

## 版本与限制

- 版本 `1.1.0`：新增 `scripts/parameter_optimizer.py` 与三路测试；真实读取
  CSV/JSON，计算 Pearson/Spearman/OLS/LOOCV、约束感知可行域、推荐区间、
  验证点与停止条件；数据不足写 `not_computable`。
- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_tracks`；模型写内容，脚本做
  证据/字段/产物审计。
- 不联网：只使用 `/input` 提供的材料或导出；不伪造在线查询结果。
- 统计量是描述性的，不构成因果或已验证的代理模型；外推点必须标
  `extrapolation`。
- 正则抽取的约束必须逐字复核；自动列角色识别可能误判，必要时显式传参。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断；示例是 synthetic 接口
  样例，不是正式案例答案。
