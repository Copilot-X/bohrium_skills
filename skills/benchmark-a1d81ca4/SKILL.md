---
name: literature-benchmark-builder
description: 文献阅读 Benchmark 与评测工具（workorder 优先）：读取组织方 benchmark workorder 的四项题卡，构建带金证据的题集，运行确定性评分器，输出逐题分数、汇总指标、失败分析与版本快照。Run a source-conditioned four-item literature-reading benchmark with a real scorer, per-item scores and failure analysis. Use for 评测集、Benchmark、评分工具、基线对比、失败分析、版本迭代。
---

# 文献阅读 Benchmark 与评测工具（Workorder-First Benchmark）

## 用途与触发

task.md 要求“设计评测”“构建 Benchmark”“评测工具”“基线对比”“逐题评分/失败分析”时使用。

核心：**先读组织方 workorder，按它给的题卡/材料/字段做四项 benchmark；不要自己发明题集或把合成检查器日志冒充官方 DSH 分数。**

## 输入

- `/input/task.md`：评测目标、题量、输出格式、可用材料。
- `/input/case.json`：`case_id`、材料路径、`output_contract`。
- `/input/benchmark-workorder.json`（或 `*workorder*.json`）：四项题卡、目标能力、评分要求；**优先读它**。
- 材料：段落/表格文本（不是 PDF 像素金标准）。
- 输出：`/app/work`、`/app/output`。

## 路径解析（先做）

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件（按需使用，不阻塞）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/benchmark-design.md` | 定能力维度、题卡字段、指标、基线前 | 按小节查阅 |
| `templates/benchmark-protocol.json` | 协议骨架 | 复制后填真实 items/metrics；workorder 字段优先 |
| `scripts/score_benchmark.py` | 对 gold/pred 打分 | `python3 "$SKILL_DIR/scripts/score_benchmark.py" --gold /app/work/gold.jsonl --pred /app/work/pred.jsonl --out /app/work/metrics.json --bootstrap 1000 --seed 42 --json` |
| `scripts/validate_benchmark.py` | 可选一致性审计 | `python3 "$SKILL_DIR/scripts/validate_benchmark.py" --protocol /app/work/benchmark-protocol.json --metrics /app/work/metrics.json --result /app/output/result.json --root /app/output --json` |

评分器 stdlib-only；缺预测的题计入 `coverage` 与 `errors[]`，不静默跳过。checker 失败不阻塞，记录 `audit_unavailable`。

## 执行步骤（交付物优先）

### 第 1 步（约 15% 预算）：读 workorder，抄四项题卡

先找 `/input/benchmark-workorder.json`（或任意 `*workorder*.json`），读：

- 题目/材料/目标能力；
- 四项题卡（question、gold_points、gold_evidence、type、difficulty、capability、scoring_rule）；
- 输出字段与版本要求；
- 明确“不可比/未报告”的规则。

把 workorder 原文抄到 `/app/work/workorder.json`，并在 `/app/work/workorder-checklist.md` 列出四项 id。**不要增加 workorder 没有的题；workorder 缺项写 `not_provided`。**

### 第 2 步（约 25% 预算）：写 gold 与协议

每题一个 JSON item，写 `/app/work/gold.jsonl`：

```json
{"item_id":"i01","question":"...","gold_points":["91%"],
 "gold_evidence":[{"paragraph_id":"p6","quote":"reached 91% methanol selectivity"}],
 "type":"fact|condition|compare|inference|abstain",
 "difficulty":"easy|medium|hard","capability":"locate|extract|compare|infer|abstain",
 "source":"materials/paper.md","scoring_rule":"..."}
```

- `gold_evidence` 必须来自给定段落/表格文本；不用模型生成金标准。
- 四项至少覆盖 2 个能力维度，并含 1 道 `abstain` 题（材料不足时正确拒答）。
- 写 `/app/work/benchmark-protocol.json`：items、metrics、split、baselines、limitations；
  `metrics[]` 要声明 scorer 实际返回的全部指标名（含 formula/range），否则 validator 会告警。
- 不确定的字段写 `not_provided`，不补造。

### 第 3 步（约 20% 预算）：准备 pred 并真实评分

`pred.jsonl` 每题一行，至少 `item_id/answer_points/evidence`；拒答写 `abstain: true`。

把评分器复制到输出目录，便于复核：

```bash
mkdir -p /app/output/scripts
cp "$SKILL_DIR/scripts/score_benchmark.py" /app/output/scripts/score_benchmark.py
python3 /app/output/scripts/score_benchmark.py \
  --gold /app/work/gold.jsonl --pred /app/work/pred.jsonl \
  --out /app/work/metrics.json --bootstrap 1000 --seed 42 \
  --run-id run-001 --json
```

必须真实运行；`result.json` 的指标值只能来自 `metrics.json`。未运行指标写 `value: null, status: not_run`。

### 第 4 步（约 30% 预算）：逐题分数、失败分析、版本快照

- 从 scorer 输出或逐题对比生成 `/app/work/per-item-scores.json`：
  `{item_id, answer_score, evidence_score, status, failure_type}`；
- 失败类型至少：`missing_evidence`、`wrong_value`、`overclaim`、`abstain_error`、`format_error`；
- 写 `/app/work/failure-analysis.json`：按题型/能力统计失败数、典型例子、改进建议；
- 写 `/app/work/version.json`：`version_id`、`changed_items`、`comparable_previous_scores`；
- 写 `/app/output/result.json`（见下）。

### 第 5 步（约 10% 预算）：可选审计与落盘核对

- `validate_benchmark.py` 可选；失败不阻塞，记录 `audit_unavailable`。
- 手工核对：四项齐全；每项有 gold evidence；指标来自真实运行；逐题分数与汇总一致；
  failure-analysis 不编造；版本/可比性说明清楚。
- 如果 workorder 缺失：`status=input_error`，写清缺什么，不伪造题集。

## 输出契约

```json
{
  "case_id": "与输入一致",
  "status": "answered|partial|input_error",
  "task_status": "completed|partial|input_error",
  "result": {"summary": "四项 benchmark 的本地运行结论"},
  "claims": [
    {"claim_id": "C1", "text": "四项题卡均来自 workorder", "kind": "reported",
     "evidence": [{"source": "/input/benchmark-workorder.json"}]}
  ],
  "uncertainties": ["n=4，指标 CI 宽，不能外推"],
  "workorder": {
    "path": "/input/benchmark-workorder.json",
    "items": ["i01", "i02", "i03", "i04"],
    "source_conditioned": true
  },
  "dataset_snapshot": {
    "version_id": "v1",
    "source_hashes": [{"path": "materials/paper.md", "sha256": "..."}],
    "items": 4
  },
  "protocol": {
    "task": "literature reading QA",
    "items": [
      {"item_id": "i01", "question": "...", "gold_points": ["..."],
       "gold_evidence": [{"paragraph_id": "p1"}], "type": "fact",
       "difficulty": "easy", "capability": "extract", "source": "materials/paper.md"}
    ],
    "capabilities": ["locate", "extract", "compare", "abstain"],
    "metrics": [
      {"name": "answer_accuracy", "formula": "exact_match/total", "range": [0, 1]}
    ],
    "scoring_tool": {
      "path": "scripts/score_benchmark.py",
      "usage": "python3 scripts/score_benchmark.py --gold ... --pred ... --out ... --json"
    },
    "baselines": [{"name": "abstain_all"}]
  },
  "baselines": [{"name": "abstain_all", "metrics": {"answer_accuracy": 0.0}}],
  "results": [
    {"run_id": "run-001", "n": 4, "metrics": {"answer_accuracy": 0.5},
     "ci95": {"answer_accuracy": [0.15, 0.85]}, "synthetic": true}
  ],
  "metrics": [
    {"name": "answer_accuracy", "value": 0.75, "n": 4, "ci95": [0.30, 0.95], "status": "ok"}
  ],
  "per_item_scores": [
    {"item_id": "i01", "answer_score": 1, "evidence_score": 1, "status": "ok", "failure_type": null}
  ],
  "failure_analysis": {
    "by_type": {"missing_evidence": 1, "wrong_value": 0},
    "examples": [{"item_id": "i03", "failure_type": "missing_evidence", "detail": "..."}],
    "recommendations": ["补充段落级 quote 检查"]
  },
  "version": {
    "version_id": "v1",
    "changed_items": ["i01"],
    "comparable_previous_scores": false,
    "reason": "题卡/材料变化后总分不可直接比较"
  },
  "raw_outputs": {
    "gold": "work/gold.jsonl",
    "pred": "work/pred.jsonl",
    "metrics": "work/metrics.json"
  },
  "artifacts": [
    {"role": "protocol", "path": "work/benchmark-protocol.json", "format": "json"},
    {"role": "metrics", "path": "work/metrics.json", "format": "json"},
    {"role": "failure_analysis", "path": "work/failure-analysis.json", "format": "json"}
  ],
  "limitations": ["n=4，指标置信区间很宽，不能外推"],
  "uncertainty": [],
  "change_log": [{"step": 1, "action": "读取 workorder", "evidence": "workorder.json"}]
}
```

规则：

- `workorder.items` 必须与输入一致；不得多造题。
- 顶层官方 reading 别名 `status/result/claims/uncertainties` 必须存在；`status` 用 `answered|partial|input_error`，内部完成度可写 `task_status: completed|partial|input_error`；`claims` 至少说明 workorder/题卡来源，`uncertainties` 写样本量与不可外推。
- `metrics` 数值必须来自 `score_benchmark.py` 的 `metrics.json`。
- `per_item_scores` 覆盖四项；`failure_analysis` 必须引用真实失败项。
- 不把合成检查器日志写成官方 DSH 分数；`synthetic: true` 只表示本地自测。
- n<30 必须给 CI 并说明不能外推；版本变化后说明历史分数是否可比。

## 异常处理

| 情况 | 处理 |
|---|---|
| 缺 benchmark-workorder.json | `status=input_error`，写清缺什么，不伪造题集 |
| workorder 少于/多于四项 | 按 workorder 实际数量；在 `limitations` 说明 |
| gold 缺 evidence | 该题标 `not_provided`，不生成假金标准 |
| pred 缺题 | 计入 coverage/errors，不静默跳过 |
| 评分器失败 | 手工按同一公式复算，记录 `audit_unavailable`；不编造 metrics |
| 版本不可比 | `comparable_previous_scores=false` 并说明原因 |

## 自检

- [ ] 读到了 workorder，四项 id/字段与输入一致
- [ ] 每题有 gold_points + gold_evidence；含 abstain 题
- [ ] `score_benchmark.py` 真实运行过，metrics 来自输出
- [ ] `per_item_scores` 覆盖全部题；failure_analysis 有真实例子
- [ ] `version` 说明变更与可比性
- [ ] 官方 reading 别名 `status/result/claims/uncertainties` 若任务要求则补齐
- [ ] 没有把本地合成结果写成官方 DSH 分数

## 最小示例（示例，不是正式答案）

输入：workorder 四项题卡 + 一段论文文本。  
输出：`gold.jsonl` 4 条、`pred.jsonl` 4 条、`metrics.json`（answer_accuracy=0.75，n=4，CI 宽）；
`per_item_scores` 4 条；`failure_analysis` 指出 1 条 `missing_evidence`；
`version.comparable_previous_scores=false`。示例不是正式案例结果。

## 版本与限制

- 版本：0.6.0（workorder-first、四项题卡、逐题分数/失败分析/版本快照）。
- n=4 时指标只能作为本地 smoke test，不能外推。
- 合成检查器日志不是官方 DSH 分数。
- 平台可能把技能装到题目专属目录；必须先解析 `$SKILL_DIR`。
