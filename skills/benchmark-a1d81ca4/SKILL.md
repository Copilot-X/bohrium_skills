---
name: literature-benchmark-builder
description: 文献阅读 Benchmark 与评测工具（workorder-first，真实 runner）：读取组织方四项 benchmark workorder，逐字生成 gold、核验段落/表格证据、用确定性 scorer 真实评分，输出 0/1/2 逐题分、部分分覆盖、失败分析、trace 索引与版本快照。Run a source-conditioned literature-reading benchmark end to end with a real scorer and inspectable result.json. Use for 评测集、Benchmark、评分工具、基线对比、失败分析、版本迭代。
---

# 文献阅读 Benchmark 与评测工具（Workorder-First，真实 runner）

## 用途与触发

task.md 要求“设计评测 / 构建 Benchmark / 评测工具 / 基线对比 / 逐题评分 / 失败分析”时使用。
本题的核心不是写一段评测建议，而是**用组织方 workorder 真实跑完一轮可复核的 benchmark**：

1. 题卡来自 workorder（不自己加题、不用模型生成金标准）；
2. gold evidence 回到给定段落/表格文本并逐字核验；
3. 用确定性 scorer 对模型预测真实评分，输出逐题 0/1/2 与部分分；
4. 每个分数可顺着 `item_id` 追到原始输出、运行配置和评分记录；
5. 本地运行不得写成官方 DSH 分数。

## 输入契约

| 输入 | 必需 | 说明 |
|---|---|---|
| `/input/case.json` | 是 | `case_id`、`output_contract`；原样保留，字段名不得改写 |
| `/input/task.md` | 是 | 题目与输出要求 |
| `/input/benchmark-workorder.json` | 是 | 四项题卡；也接受 `*workorder*.json` |
| `/input/materials/*` | 是 | 段落/表格文本；不是 PDF 像素金标准 |
| `/input/environment/resources.yaml` | 可选 | 预装工具清单 |

workorder 每项至少包含：`item_id`、`question`、`gold_points`、`gold_evidence`
（`paragraph_id/table_id/page/locator` + 逐字 `quote`）、`type`、`difficulty`、
`capability`、`source`。workorder 缺项写 `not_provided`，**不补造题或金标准**。

## 输出契约

最终必须写 `/app/output/result.json`，并保留官方 reading 别名
`status / result / claims / uncertainties`。核心字段：

| 字段 | 要求 |
|---|---|
| `case_id` | 与输入一致 |
| `status` / `task_status` | `answered` / `partial` / `input_error` |
| `workorder` | 路径、sha256、item id 列表、`source_conditioned: true` |
| `dataset_snapshot` | `version_id`、材料 sha256、题数 |
| `protocol` | 题卡、能力维度、8 个指标公式/范围、`scoring_tool.path`、基线、rubric |
| `metrics` | 只能来自 `score_benchmark.py` 的真实输出；未运行写 `value: null, status: not_run` |
| `per_item_scores` | 覆盖**全部** workorder 题；含 `score_0_2`、`answer_score`、`evidence_score`、`failure_type` |
| `failure_analysis` | 按失败类型/能力统计、真实例子、改进建议、部分分统计 |
| `rubric` | 0/1/2 总分、max、均值、部分分题数 |
| `version` | `version_id`、材料/题卡哈希、`changed_items`、`comparable_previous_scores` |
| `trace_index` | 每题 → gold 行、prediction 行、scorer 下标、run_id、metrics 路径 |
| `evidence_map` | 每条 gold evidence 的 path/locator/quote/`verified` |
| `raw_outputs` | `work/gold.jsonl`、`predictions.jsonl`、`metrics.json` 等真实路径 |
| `artifacts` | 每个声明的产物真实存在 |
| `validation` | `validate_benchmark.py` 的真实结果；失败记 `audit_unavailable` |
| `output_contract` / `contract_coverage` | 原契约键逐项覆盖；未覆盖降级 `partial` 并写明 |

另写 `output/metrics.json`、`output/per-item-scores.json`、
`output/failure-analysis.json`，以及 `output/work/` 下的原始记录，便于复核。

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_benchmark_case.py` | **唯一入口**：prepare / finalize / run / self-test；真实评分并组装 result.json |
| `scripts/score_benchmark.py` | 确定性 scorer；支持 `--self-test`，缺预测计入 coverage/errors |
| `scripts/validate_benchmark.py` | 协议/指标/result 契约门禁；支持 `--self-test` |
| `templates/benchmark-protocol.json` | 协议骨架；workorder 字段优先 |
| `templates/predictions.example.json` | 预测文件字段示例（synthetic） |
| `references/benchmark-design.md` | 能力维度、题卡、指标、rubric、失败模式 |
| `examples/synthetic-case/` | 可直接跑通的 synthetic 输入（不是正式答案） |

## 工作流

### 第 1 步（约 15% 预算）：prepare 并核对输入

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
python3 "$SKILL_DIR/scripts/run_benchmark_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会盘点 workorder 和材料，把题卡**逐字**写入 `work/gold.jsonl`，
核验每条 gold quote 是否能在材料中找到，并生成
`work/predictions.template.jsonl` 与暂定 `output/result.json`。

先读 `work/workorder.json`、`work/materials.json` 和 `work/README-next.md`：
- 题数与 id 必须与 workorder 一致；
- 缺 workorder 时状态为 `input_error`，不生成题集；
- quote 找不到时看 `gold_evidence[].verify_status`，回到原文重摘，不要改写。

### 第 2 步（约 40% 预算）：写预测，不抄金标准

复制 `work/predictions.template.jsonl` 为 `work/predictions.jsonl`，每题一行：

```json
{"item_id":"i01","answer_points":["91%"],"evidence":[{"paragraph_id":"p6","quote":"reached 91% methanol selectivity"}],"abstain":false}
```

规则：
- 预测必须来自阅读材料后的回答；**不得把 workorder 的 `gold_points` 直接当预测**；
- 材料不足时写 `abstain: true`，并在 `reason` 说明缺什么；
- 每个答案点尽量配 `path/locator/quote`；引用必须逐字；
- 四项都要有预测行；缺行会计入 `coverage` 并在逐题分中标 `missing_prediction`。

如需记录版本变更，可先写 `work/version.json`：
`{"version_id":"v2","changed_items":["i03"],"comparable_previous_scores":false,"reason":"..."}`。

### 第 3 步（约 35% 预算）：finalize 真实评分

```bash
python3 "$SKILL_DIR/scripts/run_benchmark_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：
1. 用 `score_benchmark.py` 真实评分（同 seed 跑两遍并记录是否确定性）；
2. 生成 8 个指标、2 个 rule-based 基线和逐题 0/1/2；
3. 依据 scorer 的 `items[]` 生成失败类型、部分分统计和建议；
4. 运行 `validate_benchmark.py`，把 problems/warnings 写入 `validation` 和 `uncertainties`；
5. 组装 `result.json`，逐项检查原 `output_contract` 覆盖。

### 第 4 步（约 10% 预算）：按结果修正，不粉饰

- `status=partial` 时逐条看 `validation.problems`、`uncertainties` 和
  `per_item_scores[].failure_type`，修 prediction 或证据后重跑 finalize；
- `missing_prediction` → 补预测行；
- `missing_evidence` → 补 locator + 逐字 quote；
- `abstain_error` → 材料不足时拒答，材料充足时给出答案；
- `wrong_value` / `overclaim` → 只保留材料支持的点；
- 缺 workorder/材料 → 保持 `input_error`/`partial`，不编造指标。

## 评分规则（0/1/2）与指标

| 分值 | 条件（由 scorer 的逐题事实推导） |
|---|---|
| 2 | 答案完全匹配且 gold evidence 被引用；或正确拒答且有可定位依据 |
| 1 | 部分要点匹配；或答案正确但缺证据；或拒答但缺依据 |
| 0 | 未作答、答案错误、或该拒答却作答/该作答却拒答 |

指标：`answer_accuracy`、`answer_f1`、`evidence_precision`、`evidence_recall`、
`abstention_correctness`、`citation_groundedness`、`coverage`、
`hallucination_rate`。n<30 必须报告 CI，并声明不能外推。

## 异常处理

| 情况 | 处理 |
|---|---|
| 缺 workorder | `status=input_error`，写清缺什么，不伪造题集 |
| workorder 少于/多于四项 | 按实际数量；在 `limitations` 说明 |
| gold 缺 evidence/points | 标 `not_provided`，该题 0 分且写入 uncertainty，不生成假金标准 |
| pred 缺题/重复/未知 id | 计入 coverage/errors；重复与未知 id 记 `format_error` |
| 证据 quote 找不到 | `evidence_map.verified=false`，状态降级 `partial` |
| scorer/validator 不可用 | 记 `audit_unavailable`，不编造 metrics |
| 输出为空/超长/格式不合规 | 记 `format_error`，保留原始输出路径 |
| 版本不可比 | `comparable_previous_scores=false` 并说明原因 |

## 自检

- [ ] 题数与 id 与 workorder 完全一致，没有多造题
- [ ] 每题有 gold_points + gold_evidence；有 abstain/边界题
- [ ] `gold_evidence[].verified=true`（或如实标 false）
- [ ] `score_benchmark.py` 真实运行过，metrics 来自其输出
- [ ] `per_item_scores` 覆盖全部题，含 0/1/2 与 failure_type
- [ ] `validation.ok=true`，原 `output_contract` 全覆盖
- [ ] `trace_index` 能从分数追到 gold/pred/metrics
- [ ] 没有把本地运行写成官方 DSH 分数

## 最小调用示例（synthetic，不是正式答案）

```bash
python3 scripts/run_benchmark_case.py run \
  --input examples/synthetic-case/input \
  --work /tmp/bench-work --output /tmp/bench-output
python3 scripts/run_benchmark_case.py self-test --json
```

示例输出（synthetic）：4 条 gold、4 条 prediction，`answer_accuracy=1.0`，
逐题 0/1/2 均为 2，`deterministic=true`；示例只说明接口，不冒充正式案例结果。
`examples/synthetic-case/` 中的材料与答案均为合成文本。

## 版本与限制

- 版本 1.0.0：单入口真实 runner + 逐字 gold + 证据核验 + 0/1/2 + 部分分 + trace 索引。
- n=4 时指标只能作为本地 smoke test，CI 很宽，不能外推。
- 本地 scorer 结果不是官方 DSH 分数；不把合成日志写成评审结论。
- 平台可能把技能装到题目专属目录；命令必须先解析 `$SKILL_DIR`。
