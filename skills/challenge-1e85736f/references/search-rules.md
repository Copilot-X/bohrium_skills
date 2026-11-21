# 最新文献发现与追踪：执行规则

## 1. 任务口径

- 目标：增量水位、时间窗、去重、版本更新与提醒抑制。
- 主产物：`output/search-results.json`。
- 必填字段：`subscription`, `window`, `watermark`, `snapshot_before`,
  `snapshot_after`, `increments`, `alert_log`, `suppressed`, `replay_key`,
  `evidence`（其中容器字段由 domain checks 校验存在性与内容，空窗口允许
  `increments=[]`）。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

只报告相对上次快照的新增；窗口/时区固定；重跑幂等；版本记录可回放。

### 3.1 窗口与水位

- `window.start/end/timezone` 必须显式；边界规则为 **含起点、不含终点**。
- 日期统一归一化为 UTC `YYYY-MM-DD`；只有年/月的记录保留
  `date_precision` 并在 `uncertainties` 提示边界误差。
- 窗口必须从上一窗口的 `end` 连续推进；`watermark` 记录已覆盖到的时间点。
- 不得用“当前时间”隐式决定窗口；空窗口也是合法结果。

### 3.2 去重与版本链

- 分组键优先级：`doi:<lower>` → `arxiv:<id-without-version>` → `title:<normalized>|<year>`。
- 只有记录没有 DOI/arXiv 时才使用标题键；不同 DOI 的相同标题**不得**
  合并（除非某条记录同时携带两个标识符）。
- 同一 work 的预印本/正式版/修订版合并为一组，保留 `versions[]` 与
  `merged_from`；主记录优先 `published > corrected > preprint > unknown > retracted`。
- 组内分类按**窗口内成员**判定：撤稿 > 更正 > 已有基线（version_update）> new。

### 3.3 提醒与抑制

- Skill 只准备提醒，不发送：`delivery=not_sent_by_skill`。
- `alert_log[].status ∈ {queued_for_digest, already_prepared,
  suppressed_digest_limit, delivered}`。
- 单窗口最多 10 条排队提醒，其余必须进入 `suppressed` 并写
  `reason=digest_limit`；越窗条目写 `reason=outside_window`；同 replay_key
  重跑写 `reason=already_prepared_idempotent`。
- `replay_key` 由订阅、窗口、时区、去重规则版本与来源集合哈希得到；
  `work/tracking-state.json` 保存已准备 alert_id，保证不二次入队。

### 3.4 指标

- 只报告可从输入复算的计数（记录数、唯一 work 数、窗口内记录数、
  新增/版本更新/撤稿/更正、提醒与抑制数）。
- 没有标注参考集时，`precision_at_k/recall_at_k` 写 `not_computable_without_labels`，
  不得编造数值。

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
- 新增判定正确率、窗口连续性、重复/误报抑制率、重跑幂等与回放一致性。
- 只报告相对上次快照的新增；窗口/时区固定；alert_log/suppressed 记录投递与抑制。
