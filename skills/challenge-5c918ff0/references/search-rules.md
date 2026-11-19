# 多数据库联合检索：执行规则

## 1. 任务口径

- 目标：多源查询、字段归一、去重合并与覆盖比较。
- 主产物：`output/search-results.json`。
- 必填字段：`databases`, `records`, `coverage`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

每条记录有来源库与字段来源；合并规则稳定；未覆盖库/字段明确。

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

- 查询：`id/round/query/source/purpose/stop_condition`；至少一轮补充查询，停止条件可解释。
- 候选：`authors/venue/doi/rank/score/relevance/reason/dedupe_key/merged_from`；重复用 DOI 或 title+year 合并并保留依据。
- 指标：`k`、`precision_at_k`、`ndcg_at_k`、`recall_at_k`、`metadata_accuracy`，写清分子/分母/口径；无法计算写 `not_computable`。
- 排序：`rank/score/tie_break` 稳定；关键文献在前；空结果、缺失元数据、来源冲突写 `uncertainties`。
- 合并身份准确率、逐字段来源可追溯率、去重判定可解释性、库/字段覆盖率。
- 每条记录有 source_dbs 与 field_sources；合并规则和冲突处置必须可复跑。
