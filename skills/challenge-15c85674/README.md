# 文献智能问答 Skill（v0.5.0）

## 用途

给定带段落编号的论文文本与问题清单，逐题回答，并用 `paragraph_id + 逐字 quote` 给出可回查证据；材料不足时明确拒答或标 `partial`。

## 输入 / 输出

- 输入：`/input/case.json`（`case_id`、`paper_id`、`material_version`、`paragraphs[]`、`questions[]`）、`/input/task.md`
- 输出：`/app/output/result.json`，核心字段 `answers[]`、`conditions`、`claims[]`、`evidence[]`、`unanswered_parts[]`、`coverage`；顶层另有 `status`、`result`、`claims`、`uncertainties`。

## 快速开始

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
python3 "$SKILL_DIR/scripts/inspect_input.py" /input --out /app/work/contract_hints.json
# 先写完整 answers[] 并落盘，再补 claims/evidence
python3 "$SKILL_DIR/scripts/check_evidence_qa.py" --result /app/output/result.json --input /input --json
```

## 最小示例（示例，不是正式答案）

输入问题：`q1: 催化剂 A 在什么条件下达到最高选择性？`  
输出：`answer: 350 °C、1 bar 下达到 92%`；`conditions: ["350 °C", "1 bar"]`；`claims[0].evidence: [{"paragraph_id":"p4","quote":"...92% selectivity at 350 °C and 1 bar..."}]`。

## 设计要点

- 交付物优先：先写完整答案，再补证据表；checker 是可选审计，不是闸门。
- 逐题覆盖：每个 `question_id` 都必须有条目，不能只答容易题。
- 引文最小充分：优先选同时包含数值与条件的最短原文片段。
- 拒答也是有效回答：材料未报告时写 `insufficient_evidence` 与检索范围。

## 已知限制

- 仅依据给定材料，不联网、不补造数据。
- 扫描件依赖 OCR 时，quote 可能带识别误差，需在 `uncertainties` 标注。
- 平台可能把技能装到题目专属目录，命令必须先解析 `$SKILL_DIR`。
