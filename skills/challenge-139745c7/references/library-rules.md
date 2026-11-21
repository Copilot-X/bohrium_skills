# 科研文件智能检索：执行规则

## 1. 任务口径

- 目标：文件/全文检索、字段过滤、排序与结果定位。
- 主产物：`output/library-results.json`。
- 必填字段：`items`, `filters`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

每条结果有路径和命中片段；排序可解释；无结果说明。

### 3.1 硬过滤

- `filters.must` 是硬约束：每个 accepted item 必须有可核验的满足证据
  （`filter_matches` 或 metadata 字段）；不得因为结果太少而放宽。
- `filters.must_not` 命中项必须 `rank:null` 且 `excluded_by` 写明条件；
  `kept+excluded` 必须等于候选总数。
- 条件无法满足时 `no_result` 显式，给 `reason` 与 `searched_scope`。

### 3.2 章节定位

- PDF：`page` 是物理页；若材料给出印刷页码，同时写 `printed_page` 与
  `page_offset`，不得把印刷页直接当物理页。
- 文本/Word：`section_id`（标题）或 `section_label` + `line`，并给命中句。
- 双栏/跨页表格：`locator` 写栏/页范围；quote 用同页原文，不跨栏拼接。

### 3.3 版本与副本

- 同一内容的副本（sha256 相同）只算一条证据，其余标 `duplicate_of`。
- 同一文档的 v1/v2/revised：当前版作为 ranked hit；旧版进
  `version_conflict[]`，写 `current/superseded/basis`。
- 行政版本说明（supersedes / 作废 / 已取代）是版本判据，不是内容答案。

### 3.4 别名与权限

- alias / 又名 / 曾用名必须带来源定位；用别名命中时同时给原文件名。
- 无权限、加密、损坏、无文本层分别记录，不得混写成“无结果”。

## 4. 产物

- 主产物必须非空、可编辑、可复核；
- 每个声明 artifact 必须真实存在；
- 分析类任务保留原始快照、规则/代码、日志与结果文件；
- 失败/跳过/权限不足要记录，不把未完成写成完成。

## 5. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 以结果定义分母 | 指标虚高 | 先冻结参考集/输入，再计算 |
| 把未找到当不相关 | 遗漏被掩盖 | 单列未命中与原因 |
| 只给结论不给证据 | 无法复核 | 补 path/locator/quote |
| 合并不同对象 | 事实错误 | 保留 `keep_separate` 与依据 |
| 伪造在线结果 | 学术不端风险 | 只用输入材料，写缺口 |
| 静默改数据 | 结果不可复现 | 保留原始快照与变更日志 |

## 6. 本题指标与字段

- 条目：`path/kind/metadata/version/hash/status/evidence`；每个文件或记录一条。
- 操作：索引/移动/分类/批注/问答均记录 `action/target/status/detail`，失败或跳过不静默。
- 检索/问答：命中片段 + 路径 + 行号/段落；无证据时拒答，不编造。
- 分类/批注：每类有判据，低置信度进待复核；批注有原文锚点。
- 每条结果有路径、命中片段、排序理由；返回项必须满足 filters。
- 过滤条件逐条记录 kept/excluded 数量；无结果/不可读/版本冲突明确说明。
