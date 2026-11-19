# 引用与事实核验：执行规则

## 1. 任务口径

- 目标：引用与事实核验、论断-来源匹配、编造/错配检出。
- 主产物：`output/review-report.md`。
- 必填字段：`claim_source_matrix`, `source_status`, `findings`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

每条引用可回查；论断与来源结论匹配；不确定项明确。

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

- 问题：`location/check_item/issue/severity/comment/evidence`；位置到段落或行。
- 检查项：`checks[].item/status(pass|fail|not_checked)/detail`；未检查不等于通过。
- 判断：区分事实错误、证据不足与改进建议；给出可执行修复。
- 边界：不替代机构审查；材料不足写 `uncertainties` 与人工复核点。
- claim/source/verdict 四档枚举（full/partial/none/unverifiable）；引用存在≠支持结论。
- 来源状态 published/preprint/retracted/unknown；unknown 标 needs_recheck；数值 claim 有 source_value/manuscript_value。
