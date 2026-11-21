# 文献阅读 Benchmark 设计参考

配合 `templates/benchmark-protocol.json`、`scripts/score_benchmark.py`、
`scripts/validate_benchmark.py` 使用。**task.md 与 case.json 优先**；材料没有的金标准
不要用模型生成。

## 1. 能力维度

| 维度 | 测什么 | 典型题型 |
|---|---|---|
| locate 定位 | 能否找到支持结论的段落/图表 | evidence retrieval |
| extract 抽取 | 能否读出字段、数值、条件、单位 | fact / table |
| compare 比较 | 能否跨文档/跨条件对照 | comparison |
| infer 推理 | 能否从证据推出未明说的结论 | inference |
| abstain 拒答 | 材料不足时是否承认不知道 | adversarial / boundary |

每个维度单独计分，不要只报一个“总分”掩盖能力差异。题目至少覆盖 3 个维度，
并包含 1 道对抗题（材料不支持却诱导作答）和 1 道边界题。

## 2. 题目 schema

```json
{
  "item_id": "i01",
  "question": "在 350 °C 下甲醇选择性是多少？",
  "gold_points": ["91%"],
  "gold_evidence": [{"paragraph_id": "p6", "quote": "reached 91% methanol selectivity"}],
  "type": "fact|condition|compare|inference|abstain",
  "difficulty": "easy|medium|hard",
  "capability": "extract",
  "source": "materials/paper.md",
  "notes": "答案必须带单位"
}
```

规则：

- `item_id` 唯一；`gold_points` 是可判定的要点，不是整段答案。
- `gold_evidence` 必须来自给定材料，写 `paragraph_id`/表/图/页码；
  不由模型生成，不写“见原文”。
- 题干不能泄漏答案；不要把一个 item 写成完整答案。
- 难度是预估，不是实际通过率；报告中可做 calibration。
- 题集不包含正式案例答案；示例数据标 `synthetic`。

## 3. 指标

| 指标 | 公式 | 用途 |
|---|---|---|
| `answer_accuracy` | 完全匹配 gold points 的题数 / 总题数 | 严格正确率 |
| `answer_f1` | 逐题 point precision/recall 的宏平均 | 部分正确 |
| `evidence_precision` | 预测证据中命中 gold 的比例 | 证据是否过度 |
| `evidence_recall` | gold 证据中被预测覆盖的比例 | 证据是否缺失 |
| `citation_groundedness` | 带可定位证据的预测要点比例 | 结论是否有据 |
| `abstention_correctness` | 拒答判断与 gold 一致的比例 | 不知道时是否承认 |
| `coverage` | 有预测的题数 / 总题数 | 是否漏答 |
| `hallucination_rate` | 1 − answer precision（宏平均） | 编造程度 |

每个指标必须写公式、范围、n、口径；未运行写 `value: null`、`status: "not_run"`。
不要用字符串完全匹配冒充语义正确；评分器要说明匹配规则（归一化、包含、token Jaccard）。

## 4. 基线与不确定性

- 至少一个 rule-based 基线：`abstain_all`（全拒答）和/或 `empty_answer`（全空）。
- n < 30 时必须给 Wilson 或 bootstrap 95% CI；不要把点估计当稳定结论。
- 多次运行看分布，不凭单次结果下结论；记录 `repeats` 与每轮指标。
- 题集小、样本偏斜、题型不均要在 `limitations` 写清，不外推。

## 5. 评测器与偏差

若用 LLM-as-Judge：

- 直接打分要给 1–5 行为锚点；成对比较要交换 A/B 位置消除位置偏差。
- 校准 rubric：用 2–3 个已知好/差样本试评，直到好样本 ~4.5–5、差样本 ~2。
- 记录 judge 的置信度与证据；低分必须点名“哪题、哪个字段、丢了什么”。
- 计算与人工判断的一致性（Cohen's κ / Spearman），κ<0.4 不宣称可靠。
- 机器预检（缺证据、TODO、占位符）只能标“参考”，不能替代真实评分。

## 6. 数据泄漏与防 gaming

- 题集、gold、评分脚本分离；示例/开发集不得混入测试集。
- 同一材料的不同题不要互相泄漏答案。
- 评分脚本对同一输入确定性输出；随机种子固定。
- 不允许针对单一指标优化：报告至少 3 个维度，并列出失败案例。
- 不把合成检查器日志冒充官方 DSH 分数。

## 6.1 逐题 0/1/2 rubric 与部分分

任务建议按 0/1/2 记录逐题分。`run_benchmark_case.py` 只从 scorer 的逐题事实推导，
不另造判断：

| 分值 | 答案 | 证据 | 拒答 |
|---|---|---|---|
| 2 | 完全匹配 gold points | gold evidence 被引用且可定位 | 正确拒答且有依据 |
| 1 | 部分匹配 | 答案正确但缺证据 / 证据不全 | 拒答但缺依据 |
| 0 | 错误或缺失 | — | 该拒答却作答、该作答却拒答 |

部分分覆盖要求 `per_item_scores` 覆盖**全部** workorder 题：缺预测的题也保留一行，
`failure_type=missing_prediction`、`score_0_2=0`，并计入 `coverage`，不能静默跳过。
汇总同时给 `rubric.total/max/mean` 与按 capability/type 的失败分布。

## 6.2 trace 索引

每个 `per_item_scores[]` 必须能通过 `trace_index[]` 找到：
gold 行（`work/gold.jsonl#k`）、prediction 行、scorer `items[]` 下标、`run_id`、
`work/metrics.json` 路径。版本快照记录 workorder 与材料 sha256，说明历史分数是否可比。

## 7. 报告结构

```json
{
  "case_id": "…",
  "protocol": {"task": "literature reading QA", "items": [], "split": {},
               "scoring_tool": {"path": "scripts/score_benchmark.py",
                                 "usage": "python3 scripts/score_benchmark.py --gold gold.jsonl --pred pred.jsonl --out metrics.json"}},
  "metrics": [{"name": "answer_accuracy", "formula": "exact/total",
               "range": [0, 1], "value": null, "status": "not_run", "n": 0, "ci95": null}],
  "baselines": [{"name": "abstain_all", "description": "全部拒答",
                 "metrics": {"answer_accuracy": 0.0}}],
  "results": [{"run_id": "r1", "n": 0, "metrics": {}, "ci95": {},
               "repeats": 3, "synthetic": true}],
  "limitations": ["题量小，CI 宽"],
  "uncertainty": [],
  "change_log": []
}
```

## 8. 评分脚本约定

```bash
python3 {SKILL_DIR}/scripts/score_benchmark.py \
  --gold /app/work/gold.jsonl --pred /app/work/pred.jsonl \
  --out /app/work/metrics.json --bootstrap 1000 --seed 42 --json
```

输入 JSONL 每行一个 item；输出包含 `metrics[]`、`items[]`、`errors[]`、`baselines`、
`run_id`、`synthetic`。缺预测的题不能静默跳过：计入 `coverage` 并在 `errors[]` 说明。

## 9. 常见失败模式

| 信号 | 问题 | 修复 |
|---|---|---|
| gold 无原文定位 | 模型生成金标准 | 改为材料定位 |
| 指标只有名称 | 无法复算 | 补公式、范围、匹配规则 |
| n<30 只给点估计 | 过度自信 | 加 Wilson/bootstrap CI |
| 题集含完整答案 | 泄漏 | 改成要点/检查项 |
| 评分器两次不同 | 随机未固定 | 去随机、固定排序/种子 |
| 基线无 run_id | 伪造分数 | 标 `not_run` 或删除 |
| 只报总分 | 掩盖能力差异 | 按 capability/type 拆分 |
| 单次评分当结论 | 方差未知 | 重复 3 次并报告分布 |

## 10. 交付前检查

- [ ] 题集覆盖 ≥3 个能力维度、≥1 对抗题、≥1 边界题。
- [ ] 每题有材料来源的 `gold_evidence`。
- [ ] 指标公式可复算，评分脚本真实运行过。
- [ ] 至少一个基线；n<30 有 CI。
- [ ] `validate_benchmark.py` 通过；未运行指标标 `not_run`。
- [ ] `limitations` 具体，不写“样本量小”了事。
- [ ] 没有伪造的 DSH/官方分数。
