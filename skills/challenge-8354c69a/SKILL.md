---
name: experiment-result-interpretation-skill
description: 实验结果解释与异常分析：把读数解释成趋势、异常点、按证据排序的原因候选、可执行复核步骤与后续实验，并区分观测/推断、避免把相关当因果。交付五段式报告、interpretation/anomalies/follow-up 三个 JSON 与含官方字段的 result.json。Use for 实验结果解释、异常识别、原因候选、复核清单、后续实验.
---

# 实验结果解释与异常分析（experiment track · result-interpretation）

## 用途与触发

task.md 要求“结果解释、异常识别、替代解释与后续实验”，或 `/input/case.json` 给出
`experiment` / `result-interpretation` 相关字段时使用。核心交付是五段可检查结论：

1. **趋势**：方向、区间、拐点、平台期、偏离幅度；
2. **异常点**：对象（样本/批次/通道/孔位/时间点）、值、阈值与严重度；
3. **可能原因**：按证据强度排序的候选，含支持/排除证据与混杂因素；
4. **复核步骤**：第二个人可执行的原子核查动作；
5. **后续实验**：补对照、加密采样、调参数或扩重复，含预期与判读规则。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代同行评审/临床决策。
材料不足时只交付可确认部分，并把缺口写成 `uncertainty`，不要补造。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：具体问题、范围、输出要求与证据范围。
- `/input` 下的材料：读数表 `.csv/.json`、曲线/图谱文本、设备日志、`.md/.txt/.pdf/.docx` 等。
  `scripts/parsers.py` 提取文本/JSON/CSV 行；无文字层 PDF 尝试 OCR，失败如实记录。
- 可选：数据字典、批次/通道映射、历史对照、设备日志。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/trace_analyzer.py` | **本题专项**：读取 CSV/TSV/JSON/XLSX 轨迹/workbook，按 trace/run 分组算趋势与稳健异常，写五段初稿与产物 |
| `scripts/fam_tracks.py` | 盘点、证据/字段/产物审计；本 mode 额外做五段与因果闸门 |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `task.json` | 工作文件、产物、必填字段与 mode metrics |
| `templates/work.schema.json` | `work/experiment-plan.json` 字段契约 |
| `references/experiment-rules.md` | 五段结构、异常阈值、因果与失败模式 |
| `examples/synthetic-input.json` | synthetic 输入示意（非正式案例） |

## 工作流

## 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口盘点 `/input`，提取文本/JSON/CSV，写 `work/inventory.json` 与
`work/materials.json`，生成 `work/experiment-plan.json` 骨架和暂定 `result.json`。
先读 `work/materials.json` 与 `task.md`，确认数据文件、字段、单位、批次日志与缺失项。

## 第 1.5 步：生成轨迹/workbook 初稿（推荐，先保证不空转）

```bash
python3 "$SKILL_DIR/scripts/trace_analyzer.py"   --input /input --work /app/work --output /app/output
```

脚本会读取 CSV/TSV/JSON 表（有 openpyxl 时也读 XLSX），按 trace/run/sample
分组，计算斜率、方向、均值/中位数与 median/MAD 稳健异常，把逐行证据写入
`work/trace-evidence.md`，并生成五段 work 与四个声明产物。它只写**关联**，
不写因果；模型必须复核 trace/单位/批次，补上原因候选与后续实验判读。

## 第 2 步：写 `work/experiment-plan.json`（约 60% 预算）

按 `templates/work.schema.json` 写完整五段。每条关键结论绑定 `path + locator + quote`。

### 2.1 observations（观测，不是解释）

```json
{"id":"O1","statement":"conversion is 62% in run 4","value":62,"unit":"%",
 "run_id":"run-4","evidence":{"path":"data/results.csv","locator":"row 4","quote":"run-4,62"}}
```

- 只写读数、对象、单位、批次/通道/时间点；不写“因为”“导致”。
- 有数字就写 `value`+`unit`（或明确说明单位缺失）；有运行标识就写 `run_id/sample_id`。

### 2.2 trends（趋势）

```json
{"id":"T1","metric":"conversion","direction":"up","interval":"runs 1-4",
 "statement":"conversion rises from 40% to 62% as temperature increases",
 "evidence":{"path":"data/results.csv","locator":"rows 1-4","quote":"run-1,40"}}
```

- `direction` 取 `up/down/flat/mixed`；写区间、对照与比较/拟合方法。
- 不能只复述读数；拐点/平台期要写位置与幅度。

### 2.3 anomalies（异常点）

```json
{"id":"A1","observation_id":"O1","value":62,"run_id":"run-4",
 "threshold":"mean + 3 sd of runs 1-3","method":"robust z-score","severity":"high",
 "action":"repeat run 4 with flow logged","rationale":"run-4 exceeds the threshold",
 "evidence":{"path":"data/results.csv","locator":"row 4","quote":"run-4,62"}}
```

- 必须引用真实 `observation_id`；`value`/`run_id` 必须能在观测/结果表/材料中找到。
- 必须写阈值或判定规则、方法、严重度与处置；正常波动不得写成异常。
- 多个异常要分别绑定对象，不能把整批数据笼统标异常。

### 2.4 interpretations（原因候选，区分观测/推断/因果）

```json
{"id":"I2","statement":"higher temperature causes the conversion increase",
 "type":"causal_hypothesis","strength":"suggestive",
 "support":["T1: conversion rises with temperature","O1: run-4 conversion is 62%"],
 "exclusion":["flow rate was not held constant in run 4","catalyst deactivation not measured"],
 "confounders":["flow rate","catalyst batch"],
 "evidence":{"path":"data/results.csv","locator":"rows 1-4","quote":"run-1,40"}}
```

- `type` ∈ `observation/inference/causal/causal_hypothesis/association/hypothesis`。
- 只有 `causal`/`causal_hypothesis` 可以出现因果措辞（导致/引起/造成/causes/drives/
  leads to…），且必须有 `support`、`exclusion`、`strength`；否则改为推断/相关表述。
- 有因果主张时必须写 `alternative_explanations`，逐条 `status`
  （`excluded/not_excluded/untested/supported`）与检验方式。
- 按证据强度排序，不把相关当因果；证据不足写 `speculative`/`uncertainty`。

### 2.5 recheck_steps（复核步骤）与 next_experiments（后续实验）

```json
{"id":"R1","action":"re-read the run-4 raw file and verify the flow rate",
 "observation_id":"O1","expected":"flow rate matches runs 1-3",
 "evidence":{"path":"data/results.csv","locator":"row 4","quote":"run-4,62"}}
```

```json
{"id":"E1","experiment":"temperature sweep with flow held constant",
 "purpose":"separate temperature from flow confounder","observation_id":"O1",
 "interpretation_id":"I2","expected_outcome":"conversion rises at constant flow",
 "decision_rule":"positive slope with p<0.05","evidence":{"path":"data/results.csv","locator":"rows 1-4","quote":"run-1,40"}}
```

- 复核动作必须具体到文件/字段/批次/设备日志；禁止“检查数据质量”。
- 后续实验写目的、绑定异常/原因、预期结果与判读规则。
- 设备/安全/伦理未确认的写 `confirmation_items`，不写成已具备。

### 2.6 同时写四个产物

- `output/experiment-report.md`（主交付物，五节齐全，引用 observation id）
- `output/interpretation.json`（interpretations 数组）
- `output/anomalies.json`（anomalies 数组）
- `output/follow-up.json`（next_experiments 数组）

三个 JSON 的 id 集合必须与 work file 一致；内容可加 `case_id/generated_at` 包装。

## 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：
1. 检查必填字段 `observations/interpretations/trends/anomalies/recheck_steps/
   next_experiments/evidence`；
2. 逐条核验证据 quote；
3. 执行 result-interpretation 闸门：
   - 因果措辞与 type/support/exclusion/strength 一致；
   - 异常 value/run_id 可在数据中找到，且引用真实 observation；
   - 复核/后续绑定观测或证据，写预期结果；
   - 报告五节齐全并引用 observation id；
   - 三个 JSON 产物 id 与 work file 一致；
4. 写 `output/result.json`（官方字段 + `interpretation_audit`）；
   任何问题降级 `partial` 并写入 `work/interpretation-audit.json`。

## 输出契约

`result.json` 必须含：
- `case_id`、`status`、`task_status`；
- `deliverable`：`output/experiment-report.md` 的真实路径；
- `result`：摘要、mode、必填字段、证据数、产物列表；
- `claims[]`、`preserved_facts[]`、`evidence_map[]`、`artifacts[]`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`；
- `interpretation_audit`：五段/因果/异常/产物一致性检查结果。

若 `output_contract` 含本题特有键（如 `experiment_report`、`interpretation`、
`anomalies`、`follow_up`、`trends`），在顶层保留原键名并填入真实内容或同名产物路径；
未覆盖会降级 `partial` 并写入 `uncertainties`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `experiment-report.md` 存在、非空、五节齐全并引用 observation id |
| G2 | 必填字段 | observations/interpretations/trends/anomalies/recheck_steps/next_experiments/evidence |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查 |
| G4 | 观测/推断 | observation 不含因果措辞；推断与因果分型清楚 |
| G5 | 因果 | 因果主张有 support/exclusion/strength + alternative_explanations |
| G6 | 异常 | observation_id 真实；value/run_id 可追溯；有阈值/方法/处置 |
| G7 | 复核/后续 | 可执行、绑定观测/证据、有预期结果/判读规则 |
| G8 | 产物 | 三个 JSON 的 id 与 work file 一致；每个 artifact 真实存在 |
| G9 | 契约 | result.json 可解析，case_id 一致，output_contract 全覆盖 |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；不生成伪观测/结论 |
| 只有摘要/部分数据 | 只交付可确认部分；正文标“基于部分数据”；`status=partial` |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 数据缺失/单位不一致 | 写 `uncertainty`；不猜单位或补造读数 |
| 重复数不足/对照缺失 | 在 trends/interpretations 标注限制，降低强度 |
| 读数超量程/饱和 | 单列异常并建议复核设备；不当作真实趋势 |
| 结论互相冲突 | 保留冲突双方与证据，写 `uncertainty` 与裁决所需实验 |
| 代码执行失败 | 保留 `execution.log` 与失败原因；不伪造输出 |
| 权限/格式错误 | 写 `uncertainty` 与已完成范围；建议人工接管 |

## 最小示例（synthetic，不是正式答案）

见 `examples/synthetic-input.json` 与 `task.json.example`：合成数据演示趋势/异常/因果候选/
复核/后续五段；`classify` 无关。示例只说明接口，不冒充正式案例结果，不要复制其中实体或数值。

## 版本与限制

- 版本 2.0.0：五段结构、异常可追溯、因果闸门、三个 JSON 一致性检查。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断；因果判断仍需领域专家复核。
- 不联网：只使用 `/input` 提供的材料或导出；不伪造在线查询结果。
## 本题专项检查

- 结果解释、异常识别、替代解释与后续实验
- 五段齐全：趋势、异常、可能原因、复核、后续实验
- 解释区分观测/推断
- 异常 value/run_id 可追溯
- 因果有 support/exclusion/strength
- 复核/后续可执行，并绑定观测或证据
