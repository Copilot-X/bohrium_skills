# 引文追踪与关联文献发现：执行规则

## 1. 任务口径

- 目标：前向/后向追踪、引用路径、身份合并与关联发现。
- 主产物：`output/search-results.json`。
- 必填字段：`queries`, `candidates`, `tracking_scope`, `seeds`, `edges`,
  `paths`, `identity_groups`, `related_works`, `direction_unknown`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

每条引用边有方向与来源；路径逐跳可回指；预印本/正式版合并有依据。

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
- 引用方向正确率、路径可回指率、版本/身份合并准确率、关联文献依据。
- 每条 edge 必须有 from/to/direction/hop/source；每条 path 逐跳可回指；预印本/正式版合并写 basis。

## 7. 方向语义（必须固定）

| 字段 | 定义 |
|---|---|
| edge.from | 路径当前节点 |
| edge.to | 下一跳节点 |
| direction=forward | `to` 引用 `from`（走向施引文献/后续工作） |
| direction=backward | `from` 引用 `to`（走向参考文献/方法出处） |
| direction 未知 | 不建有向边，写入 `direction_unknown` |

输入 note 是方向判定的第一依据：
“The seed paper cites this work” → backward；
“This paper cites the seed paper” → forward；
“does not cite the seed paper but shares references” → related_works；
“does not state whether they cite” → direction_unknown。

## 8. 路径与身份算法

1. **建边**：只为有 source 明确支持的引用关系建边；每个边写 hop 与 basis。
2. **建路径**：从种子出发，按 `from->to` 逐跳排列；
   `len(nodes)=len(edges)+1`；第 i 条边必须连接 nodes[i]→nodes[i+1]；
   `direct = (len(edges)==1)`。
3. **身份键**：DOI > 规范化标题+作者+年份；同标题同作者且年份/venue
   不同的版本合并到正式版，写 `version_basis`；同标题不同作者不合并。
4. **关联**：没有引用边但共享参考文献/数据集/作者/主题的记录进
   `related_works`，relation 用 coupling/co_citation/shared_dataset 等，
   禁止写 cites。
5. **未知方向**：无法确定方向的记录进 `direction_unknown.items`，
   `checked=true`，并写 policy（不得进入有向路径）。
6. **覆盖**：输入每条记录必须被 candidate、edge、identity_group、
   related_works 或 direction_unknown 之一覆盖。

## 9. 算例（synthetic，仅演示）

输入：种子 S；参考文献 B（seed cites B）；施引文献 F（F cites seed）；
F 的预印本 F-PRE（同标题同作者）；平行工作 R（不引用 S，共享 2 篇参考）；
AMB（同标题不同作者）；F9/F10（方向未知）。

| 记录 | 处理 |
|---|---|
| B | edge S→B, direction=backward |
| F | edge S→F, direction=forward |
| F-PRE | identity_group {canonical:F, members:[F,F-PRE], merged:true, version_basis:...} |
| R | related_works {relation:bibliographic_coupling, basis:shares 2 references} |
| AMB | identity_group {merged:false, keep_separate_reason:different authors/RQ} |
| F9/F10 | direction_unknown.items + checked=true |

路径 S→F 的 nodes=[S,F]、edges=[E_SF]、direct=true；
若写成 nodes=[F,S] 或 uses E_SF 但方向不符，预检会失败。

## 10. 预检脚本

`scripts/audit_work.py` 校验方向语义、路径逐跳、身份合并/拆分、关联依据、
未知方向、输入记录覆盖与逐字证据。退出码非 0 时先修工作文件。
