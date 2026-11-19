# 结果与讨论写作参考

配合 `templates/results-discussion-plan.json` 和 `scripts/check_results_discussion.py` 使用。
**数据/图表/统计输出优先**；Results 只报告，Discussion 解释但不引入新数据。

## 1. 结果台账

```json
{"result_id":"R01","quantity":"conversion","value":92.1,"unit":"%",
 "group":"catalyst A","condition":"350 °C, 1 bar","n":3,
 "error":{"type":"SD","value":1.8},
 "test":{"name":"one-way ANOVA","p":0.003,"posthoc":"Tukey"},
 "source":{"path":"materials/results.csv","locator":"rows 4-6"},
 "figure":"Fig.2a"}
```

没有的字段写 `null`；误差类型不明写 `not_stated`；只有图没有表时读数标 `estimated`。

## 2. Results 只报告

顺序：主结果 → 次要结果 → 阴性/异常结果。
每段：变量 → 数值（±误差）→ 条件 → 对照/统计。
每段至少引用一张图表；不写“表明/提示/由于”等解释性词。

## 3. Discussion 分层

1. 一句话回答引言核心问题（只用 Results 数据）。
2. 与文献比较：一致/冲突，冲突给条件差异。
3. 机制解释：标 `speculative`/`suggested`，不写成事实。
4. 局限：样本/条件/测量/统计功效，写“限制→影响→缓解”。
5. 结论与意义：不超出数据边界。

## 4. 解释强度

| strength | 含义 |
|---|---|
| `supported` | 数据直接支持 |
| `suggested` | 数据提示，需进一步验证 |
| `speculative` | 机制/外推推测 |

每条解释 `based_on` 至少一个 result_id；讨论中的每个数字必须能在 Results 找到。

## 5. 常见失败模式

| 信号 | 问题 | 修复 |
|---|---|---|
| Results 里讲机制 | “因为/说明” | 移入 Discussion |
| 数值缺 n/误差 | 只有均值 | 补 n、SD/SEM、检验 |
| 因果越界 | 无干预写“导致” | 降级为相关 |
| 只报显著结果 | 阴性结果缺失 | 补报或说明排除 |
| 局限套话 | “样本有限”无影响 | 写具体限制与影响 |
| 讨论出现新数据 | 结果里没有的数 | 删除或补结果 |

## 6. 交付前检查

- [ ] 每个数值有来源、条件、n、误差类型。
- [ ] Results 无解释性词，Discussion 无新数据。
- [ ] 显著性陈述与统计输出一致。
- [ ] 每个解释有 based_on；强度标注正确。
- [ ] 局限具体到因素与影响。
- [ ] `check_results_discussion.py` 返回 `ok: true`。
