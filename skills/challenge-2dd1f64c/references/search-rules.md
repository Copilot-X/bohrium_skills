# 文献筛选与智能排序：执行规则

## 1. 任务口径

- 目标：相关性判断、分层标签、稳定排序与逐条解释。
- 主产物：`output/search-results.json`。
- 必填字段：`queries`, `candidates`, `decision_rules`, `ranked_keep`,
  `metrics`, `screening`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

保留/待定/排除各有判据；排序可解释；关键文献在前；冲突升级人工。

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
- 标签一致率、nDCG@k、待定区规模、逐条理由可复核；keep/maybe/exclude 各有判据。
- rank/score/tie_break 必须稳定；重复记录用 dedupe_key 合并并保留 merged_from。
- 输入是候选集时不要编造 queries；query 来源写在 screening.source。

## 7. 可复算排序算法

1. **判标签**：按 `decision_rules.priority` 从小到大，取第一条命中的规则；
   `keep`/`maybe`/`exclude` 分别写入 `screening` 三层。不得先排序后补标签。
2. **排 keep**：先按 `score` 降序；同分按 `tie_break` 字段升序
   （`candidate_id`/`title`/`year`）；仍同分则 `candidate_id` 升序。
   rank 从 1 连续编号；maybe/exclude 的 rank 为 null。
3. **算 top-k**：`effective_k = min(k, 候选总数)`；
   `ranked_keep = keep 按 rank 的前 effective_k 个`。
   若 keep 数小于 effective_k，写 `k_shortfall_note`。
4. **算指标**：
   - `Precision@k = ranked_keep 中 keep 数 / effective_k`
   - `Recall@k = ranked_keep 中 keep 数 / keep 总数`（keep 总数为 0 时写
     `not_computable`）
   - `nDCG@k` 需要分级相关性；只有二元标签时写清采用的二元折算口径。
   - `metadata_accuracy = 与输入逐字段一致的候选数 / 候选总数`。
5. **版本合并**：同标题同作者的版本只保留正式版参与排序；被合并版本
   写入 `merged_from`，并在 `version_basis` 写 DOI/arXiv/年份对应关系。
   同标题不同作者必须分开，写 `keep_separate_reason`。
6. **缺失元数据**：缺摘要/年份/DOI 的候选默认 `maybe` +
   `escalated_to_human=true`；不得仅因缺失判 `exclude`。

## 8. 算例（synthetic，仅演示）

输入 4 条：C1（期刊版，相关）、C2（C1 的预印本）、C3（主题相关但无目标
指标）、C4（儿科，不相关）。规则 R1=keep、R2=maybe、R3=exclude。

| ID | label | rank | score | tie_break | 说明 |
|---|---|---|---|---|---|
| C1 | keep | 1 | 0.90 | candidate_id | 正式版；merged_from=[C2] |
| C2 | exclude | null | null | - | duplicate_of C1，version_basis=DOI/arXiv |
| C3 | maybe | null | null | - | 无目标指标，escalated_to_human=true |
| C4 | exclude | null | null | - | 儿科人群，R3 |

k=2，keep 数=1，`effective_k=min(2,4)=2`，`ranked_keep=[C1]`，
`k_shortfall_note` 必填；`Precision@k=1/2=0.5`，`Recall@k=1/1=1.0`。
若把 C2 当作第二篇 keep，预检会报版本未合并/重复计数。

## 9. 预检脚本

`scripts/audit_work.py` 从输入候选集复算覆盖、label 一致性、rank/score/
tie_break、top-k 与 Precision@k，并检查版本合并、缺失元数据升级、
元数据逐字段一致与逐字证据。退出码非 0 时先修工作文件。
