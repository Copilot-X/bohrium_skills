# 节点类型、关系与来源约定（Node Types）

`extract_material.py` 先按材料语义给节点定类型；`build_mindmap.py` 用类型决定配色/形状；
`check_mindmap.py` 校验类型与来源是否匹配。类型可以按任务调整，但不要静默新增未定义类型。

## 1. 节点类型

| type | 含义 | 典型证据 | 默认重要性 | 渲染 |
|---|---|---|---|---|
| `topic` | 中心主题，全图唯一 | task.md 标题/案例标题 | core | 白底，粗边框 |
| `concept` | 概念、背景、术语 | 定义句、背景段 | supporting | 分支色浅底 |
| `question` | 研究问题、未决问题、假设 | 问句、objective/gap | core | 浅紫底 |
| `method` | 方法、材料、实验/分析步骤 | method/experiment 段 | core/supporting | 浅绿底 |
| `evidence` | 直接证据：数据、图表、引用 | 数值句、引用句 | core | 浅青底 |
| `result` | 结果/结论（含数值或方向） | results/conclusion 段 | core | 浅橙底 |
| `claim` | 作者解释、判断、推论 | discussion/claim 句 | supporting | 浅灰底 |
| `risk` | 局限、风险、偏差、反例 | limitation/risk 段 | supporting | 浅棕底 |
| `gap` | 研究空白、后续方向 | future/gap 段 | supporting | 浅紫底 |
| `task` | 任务要求的组织性节点 | task.md 显式要求 | core | 虚线边框 |

规则：

1. 根节点只能是 `topic`，且 `source` 指向 task.md/案例标题或材料标题。
2. `result` 节点应包含数值、方向词或明确结论；没有数值的结果不要伪造数字。
3. `evidence` 节点必须能落到 path + locator + quote；落不到就降级为 `claim` 并标 `inferred`。
4. 组织性分支（背景/问题/方法/结果/局限）如果没有材料标题支撑，`kind=task`，`source` 写 `task`。
5. `task` 类型不参与 “推断比例” 统计，但也不能借组织性名义塞入材料外事实。

## 2. kind（来源性质）

| kind | 含义 | 要求 |
|---|---|---|
| `reported` | 材料中直接出现 | 必须有 path + locator；建议带 quote |
| `task` | 来自 task.md 的组织/格式要求 | source 含 `task`，不得当作事实 |
| `inferred` | 由材料推断/归并 | detail 写推断依据；总量 ≤ 20% |

## 3. 关系词（cross_links.relation）

| relation | 方向含义 | 触发线索（示例） |
|---|---|---|
| `supports` | A 支持/证明 B | 支持、表明、证明、indicate、confirm |
| `contrasts` | A 与 B 对比/相反 | 相比、然而、高于、versus、whereas |
| `depends` | A 依赖 B | 依赖、取决于、based on、requires |
| `explains` | A 解释 B 的机制 | 原因是、机制、attribute to |
| `causes` | A 导致 B | 导致、使得、因此、lead to、result in |
| `precedes` | A 先于 B | 首先、随后、after、followed by |
| `part_of` | A 属于/组成 B | 包括、组成、part of |
| `conflicts` | A 与 B 冲突 | 矛盾、不一致、contradict |
| `cites` | A 引用 B | 引用、参见、et al |
| `link` | 兜底弱关系 | 仅在无法归类时使用，并需 quote |

硬规则：

- 两端必须存在且不同，禁止自环。
- 每条 cross-link 必须带 source{path, locator, quote} 或显式 `kind=inferred`。
- 同一句里的共现最多生成 1 条关系，避免“关系泛滥”。
- 方向按证据句语义，不按节点在图中的左右位置。

## 4. source 对象

```json
{"path": "materials/paper.md", "locator": "§Results L42", "quote": "在 350 °C 下选择性达到 91%"}
```

- `path`：相对 `/input` 的真实路径；组织性节点写 `task`。
- `locator`：章节、页码、行号、段落均可；必须能让人回到原文。
- `quote`：逐字片段，不改写；用于 `evidence_map` 复核。
- 来源缺失时：要么删除节点，要么标 `kind=inferred` 并写依据；不得留空冒充 reported。

## 5. 类型与关系的一致性

- `method -> result` 常用 `causes/depends`；`result -> method` 用 `supports`。
- `evidence -> claim` 用 `supports`；`claim` 与 `claim` 相反用 `contrasts/conflicts`。
- `gap/risk` 通常由 `result/limitation` 派生，方向为 `result -> gap` 或 `limitation -> risk`。
- 没有明确关系词的跨分支关联不要画；层级位置本身已表达从属关系。
