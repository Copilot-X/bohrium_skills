# 科研知识库问答：执行规则

## 1. 任务口径

- 目标：知识库问答、检索增强、引用定位与拒答边界。
- 主产物：`output/library-results.json`。
- 必填字段：`answers`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

答案每句有来源；无证据时拒答；冲突材料并列。

### 3.1 逐句引用

- 每个 answered sentence 至少一条 citation，citation 的 quote 必须包含该句的
  数值与关键实体；只逐字存在但主题无关的句子不构成支持。
- `support` 标 `direct|partial|conflict`；`partial` 要在句中说明限定。
- 引用给 `path + version + page/section/line + quote`，不接受只有文件名。

### 3.2 拒答

- 知识库无依据：`status=insufficient_evidence`，`sentences:[]`，
  `missing_evidence[]` 写缺的指标/实体/文件，`retrieval_scope` 写检索范围。
- 文件存在但读不出（扫描件/加密/损坏/无权限）与“知识库没有”分开记录。

### 3.3 冲突

- 至少两个不同来源或明确版本，各自带引用；`conflict.sources[]` ≥2。
- `resolution` 明确未解决/以某版本为准/需人工确认；不得静默择一。
- 若冲突来自版本差异，先说明 current/superseded，再回答当前版。

### 3.4 版本与副本

- 同一内容的副本（sha256 相同）只算一条证据，其余标 `duplicate_of`。
- 同族 v1/v2/revised：引用必须带 version；不同版本不当作相互印证的独立来源。

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
- 答案每句至少一条 citation；无证据时拒答并写 missing_evidence。
- 冲突材料并列；引用必须绑定到可回查的 path/locator/quote。
