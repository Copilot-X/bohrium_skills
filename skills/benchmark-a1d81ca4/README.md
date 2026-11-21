# 文献阅读 Benchmark 与评测工具 Skill（v1.0.0）

## 用途

读取组织方 `benchmark-workorder.json` 的四项题卡，逐字生成 gold、核验段落/表格证据，
用确定性 scorer 真实评分，输出 0/1/2 逐题分、部分分覆盖、失败分析、trace 索引与版本快照。

## 输入 / 输出

- 输入：`/input/case.json`、`/input/task.md`、`/input/benchmark-workorder.json`、段落/表格材料
- 输出：`/app/output/result.json`；原始记录 `output/work/gold.jsonl`、`metrics.json`、
  `per-item-scores.json`、`failure-analysis.json`、`evidence-map.json`
- `result.json` 核心字段：`workorder`、`dataset_snapshot`、`protocol`、`metrics`、
  `per_item_scores`、`failure_analysis`、`rubric`、`trace_index`、`version`、`validation`

## 快速开始

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
python3 "$SKILL_DIR/scripts/run_benchmark_case.py" prepare \
  --input /input --work /app/work --output /app/output
# 读材料，写 /app/work/predictions.jsonl（每题一行，带 locator + 逐字 quote）
python3 "$SKILL_DIR/scripts/run_benchmark_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

自测（不需要真实案例）：

```bash
python3 "$SKILL_DIR/scripts/run_benchmark_case.py" self-test --json
python3 "$SKILL_DIR/scripts/score_benchmark.py" --self-test --json
python3 "$SKILL_DIR/scripts/validate_benchmark.py" --self-test --json
```

## 最小示例（synthetic，不是正式答案）

`examples/synthetic-case/` 提供可直接跑通的 4 题输入和预测：

```bash
python3 scripts/run_benchmark_case.py run \
  --input examples/synthetic-case/input \
  --work examples/synthetic-case/work \
  --output /tmp/bench-example-output
```

预期：`status=answered`、4 条逐题分、`answer_accuracy=1.0`、`deterministic=true`；
示例文本与数字均为合成，不冒充正式案例结果。

## 设计要点

- workorder 优先：题卡逐字复制，不多造题。
- gold evidence 必须能在给定文本中逐字找到；找不到标记为 `verified=false`。
- 指标必须由 `score_benchmark.py` 真实运行得到；同 seed 跑两遍检查确定性。
- 逐题 0/1/2 + 失败类型 + 部分分 + trace 索引，保证每个分数可追。
- 原 `output_contract` 键逐项覆盖；未覆盖降级为 `partial`。
- 本地运行明确标记 `official_dsh_score: false`。

## 已知限制

- n=4 时指标只能作为 smoke test，不能外推；CI 很宽。
- 缺 workorder 时只报 `input_error`，不伪造题集。
- 平台可能把技能装到题目专属目录，命令必须先解析 `$SKILL_DIR`。
