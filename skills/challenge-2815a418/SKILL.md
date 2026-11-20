---
name: peer-review-responder
description: Turn reviewer comments into a point-by-point response letter and apply the corresponding real edits to the supplied manuscript baseline, refusing to claim experiments, acceptances or revisions that did not happen. Produces response-letter.md, revised manuscript and point-by-point.json. Use when review comments and a manuscript baseline are supplied.
---

# 审稿回复与论文修改（Peer-Review Responder）

## 用途与触发

输入含审稿意见（原文或摘要）+ 稿件基线，task.md 要求"逐条回复""修改论文"时使用。

## 组织方口径（硬约束，违反即扣分）

- 输入**不代表**已联网核验原评审全文；不要声称"已核对期刊系统"。
- **不得冒称**：真实审稿人的逐字要求、真实补做了实验、原作者已改稿、
  编辑部已接收、整篇全文返修已完成。
- 意见摘要中的**实质性修改必须真实落在本演练给定的基线里**；
  不能只写"材料缺失"就跳过可执行的修改。
- 缺失、部分完成、有效低分，都不得包装成满分或"完整复现"。

## 输入

- `/input/task.md`：回复格式、是否需要改稿、语言、字数
- `/input/case.json`：`case_id`、`review_id`、`manuscript_baseline`、`output_contract`
- 审稿意见：`reviews/*.md|json`（可能分 reviewer 1/2/3）
- 稿件基线：`manuscript/**`（按章节）
- 可选：编辑决定信、作者说明、补充材料

## 执行步骤

1. **拆意见**：每条意见拆成原子条目，写入 `/app/work/comments.json`：
   ```json
   {"comment_id": "R1-C2", "reviewer": "R1", "text": "…",
    "type": "method|statistics|clarity|novelty|format|data",
    "severity": "major|minor",
    "actionable": true, "needs_new_data": false,
    "target": {"chapter": "2.3", "paragraph_id": "p2.3-4"}}
   ```
   一条意见含多个诉求时拆成 `R1-C2a/C2b`，回复时逐条对应。
2. **分类处理**，四类导向四种真实动作：
   | 类型 | 动作 |
   |---|---|
   | 可编辑解决（表述、结构、补充说明） | **改稿**，给 before/after |
   | 需补充分析（已有数据可重算） | 在基线数据上真实计算，给方法与结果 |
   | 需新实验 | 明确写"本次未开展新实验"，给计划与限制，**不编结果** |
   | 不认同 | 有理有据说明，引用文献或数据，保持礼貌 |
3. **执行修改**：只改基线文件，逐条留 diff 到 `/app/work/diffs/`。
   每条 actionable 意见必须有对应改动或明确的不改动理由。
4. **写回复信**：逐条 `意见原文 → 回应 → 稿件改动位置`。
   改动位置要具体（章节 + 段 + 原句片段），不能只写"已修改"。
5. **一致性核对**：回复信声称的每处改动都能在 revised 稿中找到；
   revised 稿的改动都能在回复信中找到出处。
6. **落盘**：`response-letter.md`、`manuscript-revised.md`、
   `point-by-point.json`、`result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "review-response",
  "status": "completed|partial",
  "reviewers": [{"id": "R1", "comments": 5, "addressed": 4, "deferred": 1}],
  "responses": [
    {"comment_id": "R1-C2", "reviewer": "R1", "comment": "…",
     "response": "…", "action": "revised|clarified|computed|deferred|disagreed",
     "manuscript_change": {"chapter": "2.3", "paragraph_id": "p2.3-4",
                           "before": "…", "after": "…", "diff": "diffs/R1-C2.diff"},
     "new_analysis": {"performed": true, "method": "…", "result": "…",
                      "data": "materials/data.csv"},
     "deferred_reason": null}
  ],
  "artifacts": [
    {"role": "editable", "path": "response-letter.md", "format": "markdown"},
    {"role": "editable", "path": "manuscript-revised.md", "format": "markdown"},
    {"role": "evidence", "path": "point-by-point.json", "format": "json"}
  ],
  "deferred": [{"comment_id": "R2-C4", "reason": "需新实验，本次未开展", "plan": "…"}],
  "disagreements": [{"comment_id": "R1-C5", "basis": "…"}],
  "honesty": {
    "new_experiments_claimed": false,
    "acceptance_claimed": false,
    "full_revision_claimed": false
  },
  "uncertainty": [],
  "change_log": []
}
```

## 回复写作规范

1. 结构：`Comment R1-C2:` 原文 → `Response:` 回应 → `Changes:` 位置与改法。
2. 开头致谢一句，不冗长；不卑不亢，不空泛道歉。
3. 每条回应第一句直接给结论（"We have added…""We respectfully disagree because…"）。
4. 引用改动时给原文片段，方便审稿人定位。
5. 无法完成的事项：说明原因 + 替代方案 + 对结论的影响，不含糊。
6. 语言与 task.md 一致；中英混排时术语保持一致。

## 规则

1. **不伪造**：不写"we performed additional experiments"除非基线材料里真有新数据。
2. 不写"the manuscript has been accepted"或任何编辑部状态。
3. 不把"删掉有争议的句子"当成"解决了问题"——要说明为什么删、对结论的影响。
4. 统计相关意见必须给检验方法与结果，不能只写"已重新分析"。
5. 回复信与改稿必须一一对应，孤儿改动和空头承诺都算失败。
6. 保持原稿的科学立场，除非有新证据；不因审稿压力改变结论。

## 异常处理

| 情况 | 处理 |
|---|---|
| 意见互相矛盾 | 分别回应，指出冲突，请编辑裁定 |
| 意见指向的段落不存在 | 说明基线版本差异，按最接近段落处理并标注 |
| 需要新实验 | `deferred` + 计划 + 对结论的限制，不编数据 |
| 意见涉及未提供的补充材料 | 说明不可得，给出可执行的替代分析 |
| 时间/预算不足 | 先完成 major 意见，minor 列清单，`status=partial` |

## 自检

- [ ] 每条意见都有 `action` 与回应
- [ ] 每条 `revised` 都有真实 diff
- [ ] 回复信与改稿双向可对应
- [ ] `honesty` 三项均为 false（除非客观为真且可证）
- [ ] 未做的新实验已明确声明
- [ ] 统计意见有方法与数值

## 最小示例（示例）

输入：3 位审稿人共 11 条意见 + 6 章基线。
输出：`responses` 11 条（8 revised、1 computed、1 deferred、1 disagreed）；
`manuscript-revised.md` 含 8 处真实改动；`deferred` 1 条注明"需新实验，本次未开展"。
