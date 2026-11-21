# 实验结果解释与异常分析：执行规则

## 1. 任务口径

- 目标：把一批实验读数解释成**趋势、异常点、可能原因、复核步骤、后续实验**五段可检查结论。
- 主产物：`output/experiment-report.md`；辅助产物 `output/interpretation.json`、
  `output/anomalies.json`、`output/follow-up.json`。
- 必填字段：`observations`, `interpretations`, `trends`, `anomalies`,
  `recheck_steps`, `next_experiments`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 异常点的 `value`/`run_id` 必须能在观测、结果表或原始材料中找到；
4. 找不到原文时写入 `uncertainties`，不要用常识补全；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 五段结构（缺一不可）

### 3.1 趋势（trends）

```json
{"id":"T1","metric":"conversion","direction":"up","interval":"runs 1-4",
 "statement":"conversion rises from 40% to 62%",
 "evidence":{"path":"data/results.csv","locator":"rows 1-4","quote":"run-1,40"}}
```

- `direction` 取 `up/down/flat/mixed`（或中文 上升/下降/持平/混合）。
- 写明区间、对照与拟合/比较方法；不能只复述读数。

### 3.2 异常点（anomalies）

```json
{"id":"A1","observation_id":"O1","value":62,"run_id":"run-4",
 "threshold":"mean+3sd of runs 1-3","method":"robust z-score","severity":"high",
 "action":"repeat run 4","rationale":"run-4 exceeds the threshold",
 "evidence":{"path":"data/results.csv","locator":"row 4","quote":"run-4,62"}}
```

- 必须引用真实 `observation_id`；`value`/`run_id` 必须出现在数据中。
- 必须写判定阈值/规则、方法、严重度与处置；不能把正常波动写成异常。

### 3.3 可能原因（interpretations）

```json
{"id":"I2","statement":"higher temperature causes the conversion increase",
 "type":"causal_hypothesis","strength":"suggestive",
 "support":["T1: conversion rises with temperature"],
 "exclusion":["flow rate was not held constant"],
 "confounders":["flow rate","catalyst batch"],
 "evidence":{"path":"data/results.csv","locator":"rows 1-4","quote":"run-1,40"}}
```

- `type` 必须是 `observation/inference/causal/causal_hypothesis/association/hypothesis`；
  观测与推断分开。
- 因果措辞（导致/引起/造成/causes/drives/leads to…）只能用于 `causal`/`causal_hypothesis`，
  且必须有 `support`、`exclusion`、`strength`；否则按相关/推断表述。
- 给出按证据强度排序的候选原因与排除过程；相关不等于因果。
- 有因果主张时必须提供 `alternative_explanations`，逐条写 `status`
  （`excluded/not_excluded/untested/supported`）与检验方式。

### 3.4 复核步骤（recheck_steps）

```json
{"id":"R1","action":"re-read the run-4 raw file and verify flow rate",
 "observation_id":"O1","expected":"flow rate matches runs 1-3",
 "evidence":{"path":"data/results.csv","locator":"row 4","quote":"run-4,62"}}
```

- 必须是第二个人可执行的原子动作：回查具体文件/字段、核对批次/设备日志、加做对照。
- 每步绑定 observation/anomaly/interpretation 或证据；写预期结果。
- 禁止“检查数据质量”这类不可执行表述。

### 3.5 后续实验（next_experiments）

```json
{"id":"E1","experiment":"temperature sweep with flow held constant",
 "purpose":"separate temperature from flow confounder","observation_id":"O1",
 "interpretation_id":"I2","expected_outcome":"conversion rises at constant flow",
 "decision_rule":"positive slope with p<0.05"}
```

- 每个建议写目的、绑定异常/原因、预期结果与判读规则。
- 不把未经确认的设备/安全/伦理条件写成已具备。

## 4. 产物

- `experiment-report.md` 必须含趋势/异常/原因/复核/后续五节，并引用至少一个观测 id。
- `interpretation.json`、`anomalies.json`、`follow-up.json` 的 id 集合必须与 work file 一致。
- 失败/跳过/权限不足要记录，不把未完成写成完成。

## 5. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 趋势只复述读数 | 无解释 | 写方向、区间、拐点、幅度与依据 |
| 正常波动当异常 | 假阳性 | 写阈值/方法并与对照比较 |
| 异常不绑定对象 | 无法复核 | 引用 observation/run_id |
| 相关当因果 | 过度推断 | 改 inference，补 support/exclusion/confounders |
| 原因无排除过程 | 不可信 | 列候选、支持/排除证据与强度 |
| 复核动作不可执行 | 无法落地 | 写具体文件/字段/批次/设备 |
| 后续实验无判读标准 | 无法决策 | 写预期结果与 decision rule |
| 伪造在线结果 | 学术不端风险 | 只用输入材料，写缺口 |
| 静默改数据 | 结果不可复现 | 保留原始快照与变更日志 |

## 6. 引擎检查

finalize 会对本 mode 额外执行：
- `trends`/`anomalies`/`recheck_steps`/`next_experiments` 非空且结构完整；
- 因果措辞与 `type`/`support`/`exclusion`/`strength` 一致；
- 异常 `value`/`run_id` 能在数据中找到；异常引用真实 observation；
- 复核/后续建议绑定观测或证据；
- 报告五节齐全并引用 observation id；
- 三个 JSON 产物的 id 与 work file 一致。

不通过时 `result.json` 降级 `partial`，并写入 `work/interpretation-audit.json`。
