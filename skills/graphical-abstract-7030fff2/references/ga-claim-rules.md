# Graphical Abstract claim 与元素规则

图形摘要只回答一件事：**这篇材料做了什么、发现了什么、为什么重要。**
版式和装饰服从这条主线；任何无法回到材料的词都不上图。

## 1. 单一 claim

- 全图只有一句 take-home claim，建议 ≤12 个英文词或 ≤30 个汉字。
- 每个词都要能在 `claim.support` 找到 `path + locator`。
- 数字只保留 1–2 个核心值，必须与来源中的数值、单位、条件一致。
- 并列结论写入 `not_claimed`，不塞进面板；相关关系不得改写成因果。
- 材料没有给出结论时：写 `partial`，并在 `uncertainty` 说明，不得补造。

## 2. 3–5 个角色化元素

| role | 含义 | 典型内容 |
|---|---|---|
| `problem` | 研究问题/瓶颈 | 现有方法在高盐条件下失活 |
| `approach` | 方法/策略 | 氧空位调控吸附构型 |
| `method` | 具体手段 | 原位表征 + 对照实验 |
| `result` | 核心结果 | 350 °C 下 92% 选择性 |
| `implication` | 意义 | 为低温合成提供路径 |
| `conclusion` | 结论 | 缺陷工程稳定活性位点 |

- 3–5 个；超过 5 个说明还没想清楚：合并同类或删除。
- 每个元素一条 `source`（`path` + `locator`）；`kind` 取
  `reported | schematic | inferred | estimated`。
- `schematic` 必须是概念示意，不能暗示具体数值；`inferred` 必须在
  `not_claimed` 或 `uncertainty` 中说明推断边界。

## 3. 关系与阅读顺序

- 默认从左到右：problem → approach → result。
- `relations[]` 每项有 `id`（`E1`…）、`from`、`to`、可选 `label`；
  端点必须是已存在的元素 id。
- 箭头只表示流程/转化/因果；纯并列用留白或分隔线，不用箭头。
- 关系来源默认继承目标元素的 `source`；显式推断需写 `kind="inferred"`。
- SVG 中每个元素是 `<g id="G*">`，每个关系是 `<g id="E*">`，id 与 spec 一致。

## 4. 版式与可读性

- 画布默认 1200×600 px @300 dpi；期刊给尺寸时用期刊尺寸并记录来源。
- 字号 ≥ task.md 给出的下限（默认 8 pt）；缩到 240–300 px 宽仍能读出 claim。
- ≤5 种颜色，同一实体同一颜色，色盲友好且灰度可辨。
- 标签极简：每个面板 ≤3 行短标签；图例/单位/条件只保留必要项。
- 不用 emoji、位图图标、3D、阴影、渐变、海报式堆叠。

## 5. 来源与失败处理

| 情况 | 处理 |
|---|---|
| 找不到 claim | `partial` + `claim_missing`，请 Agent 补 spec |
| 元素缺 source | 补 `path/locator` 或删除该元素；`audit.py` 报 MISSING_IDS |
| 数字无出处 | 不上图；写入 `uncertainty` |
| 期刊尺寸未给 | 1200×600 @300 dpi + `uncertainty` |
| 无主图/只有位图 | 用几何示意重绘，标 `schematic` |
| 导出失败 | 保留 SVG，`partial` + `export_unavailable` |
| 缩略图不可读 | 减少元素/缩短 claim，不缩小字号 |
