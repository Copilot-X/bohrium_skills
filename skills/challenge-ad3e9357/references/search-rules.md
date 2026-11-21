# 专利与技术文献检索：执行规则

## 1. 任务口径

- 目标：专利/技术文献检索、同族合并、法律状态与技术分类。
- 主产物：`output/search-results.json`。
- 必填字段：`patents`, `families`, `legal_status`, `classification`, `queries`,
  `candidates`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

专利号/申请号可回溯；同族合并有依据；法律状态与日期字段完整。

### 3.1 号码规范化（可复算）

- 输入保留 `patent_number_raw`；规范号去空格、逗号、连字符，转大写，
  保留国家码与 kind code：
  `US 11,123,456 B2` → `US11123456B2`；`WO 2020/123456 A1` → `WO2020123456A1`。
- 公开号、申请号、优先权号分别字段化；不得把优先权日当申请日。
- 规范号必须能由原始号按同一规则复算；否则判号码错误。

### 3.2 同族归并（只认显式依据）

- 仅当材料给出 `family_id` 或共享 `priority_number` 时合并。
- 每个 family 写 `basis_type`（`family_id`/`priority_number`/`none`）、
  `basis_value`、`members`、`scope`、`confidence`。
- 无显式依据时按单件保留（`basis_type=none`，`confidence=low`），
  不按标题/申请人/摘要相似度推测同族。
- 所有成员必须共享同一 basis；简单族/扩展族口径不明时写
  `unspecified`，不混用两种口径。

### 3.3 法律状态（逐条来源）

- 状态枚举：`granted|expired|withdrawn|rejected|pending|unknown`。
- 每个观察值写 `status/date/source/locator/quote`；未知状态必须
  `needs_verification=true`。
- 同一专利出现多个已知状态 → `legal_status=conflict`，保留全部观察值，
  不合并、不取最新值冒充官方登记。
- 禁止从标题、摘要或转载页面推断法律状态。

### 3.4 分类与授权边界

- IPC/CPC 必须来自材料并规范为 `SECTIONnn CLASS nnnn/nn`；缺失写
  `classification_missing`，不得补造。
- 每个来源记录 access（public/licensed/restricted/unknown）与可展示范围；
  受限来源只保留元数据与定位，不复制权利要求/全文。
- 权利要求描述必须逐字引用；未提供权利要求时写 `not_available`，
  不做自由实施或侵权结论。

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
- 专利号/申请号可回溯、同族合并准确率、法律状态与日期完整率、技术分类匹配。
- 同族合并写 basis；法律状态不能靠标题猜测，必须有来源。
