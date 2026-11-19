# 结果与讨论章节写作 Skill（v0.5.0）

## 用途

从原始数据、表格、图和统计输出写 Results 与 Discussion：Results 只报告实测值（含单位、n、误差类型、显著性），Discussion 在证据边界内解释，不引入 Results 没有的新数据。

## 输入 / 输出

- 输入：`/input/task.md`、`/input/case.json`、`.csv/.xlsx/.json`、图与图注、统计输出
- 输出：`/app/output/results-discussion.md`、`claim-table.json`、`result.json`
- `result.json` 核心字段：`sections`、`results[]`、`interpretations[]`、`limitations[]`、`deliverable`、`preserved_facts`、`evidence_map`、`change_log`

## 快速开始

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
# 1) 读数据，建 result-table.json（value/unit/n/error/significance/source/figure）
# 2) 先写完整 Results，再写 Discussion，落盘 results-discussion.md
python3 "$SKILL_DIR/scripts/check_results_discussion.py" --result /app/output/result.json --manuscript /app/output/results-discussion.md --root /app/output --json
```

## 最小示例（示例，不是正式答案）

输入：`results.csv`（3 催化剂 × 3 温度 × n=3）+ `Fig.2`。  
输出：Results 3 段（每段引用一个 panel），Discussion 5 段；`results` 9 条、`interpretations` 4 条（2 supported、1 suggested、1 speculative）、`limitations` 3 条。

## 设计要点

- 先写完整章节，再补数据表与 result.json；checker 是可选审计。
- Results 只陈述，Discussion 才解释；两节不互相顶替。
- 每个数值带单位、条件、n、误差类型；没有的写 `null`/`not_stated`。
- 没有统计输出时不写“显著”；因果结论必须有实验支持，否则降级。
- Discussion 不出现 Results 没有的新数据；文献引用必须真实存在。

## 已知限制

- 图表读数可能有估读误差；估读值标 `estimated: true` 与误差范围。
- 文献无法核实时标 `source_needed`，不编 DOI。
- 平台可能把技能装到题目专属目录，命令必须先解析 `$SKILL_DIR`。
