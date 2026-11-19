# 文献阅读 Benchmark 与评测工具 Skill（v0.6.0）

## 用途

读取组织方 benchmark workorder 的题卡，构建四项带金证据的文献阅读题集，运行确定性评分器，输出逐题分数、汇总指标、失败分析与版本快照。

## 输入 / 输出

- 输入：`/input/task.md`、`/input/case.json`、`/input/benchmark-workorder.json`、段落/表格材料
- 输出：`/app/output/result.json`；过程文件 `work/gold.jsonl`、`pred.jsonl`、`metrics.json`、`per-item-scores.json`、`failure-analysis.json`
- `result.json` 核心字段：`workorder`、`dataset_snapshot`、`protocol`、`metrics`、`per_item_scores`、`failure_analysis`、`version`

## 快速开始

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
# 1) 读 benchmark-workorder.json，抄四项题卡
# 2) 写 gold.jsonl / pred.jsonl
python3 "$SKILL_DIR/scripts/score_benchmark.py" --gold /app/work/gold.jsonl --pred /app/work/pred.jsonl --out /app/work/metrics.json --bootstrap 1000 --seed 42 --json
```

## 最小示例（示例，不是正式答案）

输入：workorder 四项题卡 + 一段论文文本。  
输出：4 条 gold、4 条 pred；`answer_accuracy=0.75`（n=4，CI 宽）；`per_item_scores` 4 条；`failure_analysis` 指出 1 条 `missing_evidence`；`version.comparable_previous_scores=false`。

## 设计要点

- workorder 优先：四项题卡与输入一致，不自己加题。
- 金证据来自给定段落/表格文本，不用模型生成。
- 指标必须由 `score_benchmark.py` 真实运行得到。
- 逐题分数 + 失败类型 + 版本快照，保证可追。
- 本地合成结果不能写成官方 DSH 分数。

## 已知限制

- n=4 时指标只能作为 smoke test，不能外推。
- 缺 workorder 时只报 `input_error`，不伪造题集。
- 平台可能把技能装到题目专属目录，命令必须先解析 `$SKILL_DIR`。
