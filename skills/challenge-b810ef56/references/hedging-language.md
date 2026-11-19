# 限定语与证据强度

本文件解决两个常见扣分点：**把相关写成因果**，以及**把单篇弱证据写成强结论**。
规则只有一句：正文的动词强度不得超过证据设计允许的强度。

## 1. 证据设计的强度上限

默认从强到弱（按任务领域调整，不能机械套用）：

1. 系统综述 / meta 分析（注意异质性与纳入标准）；
2. 随机对照试验；
3. 前瞻性队列 / 准实验；
4. 回顾性队列 / 病例对照；
5. 横断面调查 / 病例系列；
6. 体外、动物、建模或单一小样本实验；
7. 专家意见、社论、预印本、摘要。

| 设计 | 可以说 | 不可以说 |
|---|---|---|
| 随机对照 | 在该试验人群中，干预组低于对照组 | 对所有人有效、机制已证明 |
| 前瞻性队列 | 暴露与结局相关、伴随 | 暴露导致结局 |
| 回顾性/横断面 | 两者相关，存在混杂可能 | 时间顺序、因果 |
| 单实验室小样本 | 在该条件下观察到…… | 领域共识、普适、工业可用 |
| 摘要/预印本 | 摘要报告…… | 全文表明、已被独立重复 |
| 模型/模拟 | 模型预测、在假设……下 | 实验测得、实际实现 |

## 2. 动词强度阶梯

### 观测/报告

- 可用：报告、观察到、测得、在……条件下为、achieved/reported/measured；
- 慎用：显示（show）——最好保留“报告显示”；
- 禁用（除非 quote 原文如此）：证明、证实、conclusively proves。

### 相关

- 可用：与……相关、关联、伴随、is associated with、correlates with；
- 禁用：导致、引起、决定、causes、leads to、drives。

### 推断

- 可用：提示、可能、或许、据……推测、suggest(s)、may、might、appears to；
- 禁用：表明（未加限定）、证实、demonstrates。

### 边界

- 可用：不能建立因果、不能外推到……、现有材料未报告、尚不能排除；
- 英文：cannot establish causality、not reported in the available material、
  remains unclear、under these conditions。

### 多来源

- 可用：多项研究一致报告（列出来源）、在不同研究中均观察到；
- 禁用：领域公认、众所周知、首次、领先、最佳——除非有独立检索证据。

## 3. 中文限定语词表

可能、或许、提示、推测、似乎、倾向于、据……推测、尚不能排除、有待证实、
在……条件下、与该结果一致、相关而非因果、现有材料未报告、不能外推、
单一实验、观察性设计、样本量有限。

## 4. 英文限定语词表

may, might, could, suggest(s), indicate(s), likely, possibly, appears to,
is consistent with, is associated with, correlates with, cannot establish,
remains unclear, under these conditions, in this cohort, in this model,
not reported in the available material.

## 5. 强度与 `claims.py` 字段

| 正文情况 | `strength` | `status` | 额外要求 |
|---|---|---|---|
| 原文直接报告数值/现象 | `reported` | `supported` | quote 含数字与条件 |
| 由原文数值计算 | `derived` | `supported` | `calculation.expression/result`；正文写“计算得” |
| 由证据推理/外推 | `inferred` | `supported/partial` | 正文有限定语；`limits` 写不能推出什么 |
| 领域常识、不含数字 | `background` | `supported` | 不带 evidence、不含数字 |

`claims.py` 只在两个条件同时成立时给 `reported`：
正文没有“可能/提示”等限定语，或 quote 本身就是一个明确的限制陈述
（cannot/does not establish/未报告）。否则给 `inferred`，并由人工补限定语。

## 6. 冲突证据

- 正文并列呈现双方，写清各自条件、样本和设计；
- 不取平均、不选边、不只引支持方；
- claim 标 `conflicting`，evidence 同时包含 `direct` 与 `contradicts`；
- 无法用现有材料裁决时，写入 `uncertainty`，说明需要什么材料。

本流水线的 `conflicts[]` 默认为空：语义冲突不能靠关键词自动判定。
如果人工确认冲突，请在 `claim-evidence.json` 中手工填写 `conflicts[]` 与
`status=conflicting`，再运行 `main.py finalize` 重新投影 result.json。

## 7. 禁止的升档写法

| 写法 | 问题 | 改法 |
|---|---|---|
| 证明 / 证实 / conclusively proves | 强度超过设计 | 报告、提示、与……一致 |
| 导致 / causes（观察性研究） | 相关写成因果 | 与……相关、伴随 |
| 显著改善（无统计检验） | “显著”被滥用 | 报告差值，注明未检验 |
| 领域共识 / 众所周知 | 单篇升格 | 多项研究一致报告（列来源） |
| 首次 / 领先 / 最佳 | 优先权无依据 | 删除或引权威检索证据 |
| 约 92%（无来源） | 模糊数字不可核验 | 给原文精确值或删除 |
| 工业可用 / 可规模化（实验室数据） | 跨越验证层级 | 写“实验室条件下”，并说明缺口 |

## 8. 交付前逐句检查

1. 这句的动词属于哪一级？
2. 支撑它的研究设计允许这一级吗？
3. 限定语是否被翻译或改写时删掉？
4. 条件、样本量、比较对象是否还在句子里？
5. quote 原文是否也用了同样强度的词？
6. 如果删掉限定语，句子是否变成更强的主张？若是，恢复限定语。
