---
name: peer-review-responder
description: 审稿意见回复：把评审意见逐条拆解为可回应条目，生成 point-by-point 回复信并落到真实稿件修改，拒绝声称未做的实验、未获得的接受或未发生的修改。Turn reviewer comments into a point-by-point response letter with real manuscript edits, never claiming work that did not happen. Use for 审稿回复、response letter、返修、逐条答复。
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

## 支持文件（必须使用）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/response-letter-playbook.md` | 拆意见、分类处置、写回复前 | 先读；按四类动作与诚实红线执行 |
| `templates/point-by-point.json` | 逐条回复台账 | 复制后填 reviewers/responses/manuscript_change/deferred |
| `scripts/check_response_letter.py` | 交付前 | `python3 {SKILL_DIR}/scripts/check_response_letter.py --result /app/output/result.json --manuscript /app/output/manuscript-revised.md --root /app/output --json` |

`{SKILL_DIR}` 通常为 `/skills/weekly-skill`。校验器检查每条意见的 action/回应、
revised 的 before/after/diff 是否真实落在修改稿、deferred 的原因/计划/影响、
honesty 与 artifacts；不要为了让校验通过而声称未做的新实验或接收状态。

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
7. **交付校验**：运行 `check_response_letter.py`，检查每条回复与修改稿/台账一一对应、
   diff 存在、deferred/disagreed 依据与 honesty；有 problem 先修，修不了标 `status=partial`。

## 官方输出契约（必须补齐）

官方 `example-input` 的 `output_contract` 要求顶层字段：`deliverable`、`preserved_facts`、`evidence_map`、`change_log`。
题目原有字段全部保留；交付前运行：

```bash
python3 {SKILL_DIR}/scripts/emit_official.py \
  --result /app/output/result.json --out /app/output/result.json \
  --profile writing --json
```

该适配器只补齐官方别名，不改变任务字段；`validate_result.py` 之后仍需运行。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "review-response",
  "status": "completed|partial",
  "reviewers": [
    {
      "id": "R1",
      "comments": 5,
      "addressed": 4,
      "deferred": 1
    }
  ],
  "responses": [
    {
      "comment_id": "R1-C2",
      "reviewer": "R1",
      "comment": "…",
      "response": "…",
      "action": "revised|clarified|computed|deferred|disagreed",
      "manuscript_change": {
        "chapter": "2.3",
        "paragraph_id": "p2.3-4",
        "before": "…",
        "after": "…",
        "diff": "diffs/R1-C2.diff"
      },
      "new_analysis": {
        "performed": true,
        "method": "…",
        "result": "…",
        "data": "materials/data.csv"
      },
      "deferred_reason": null
    }
  ],
  "artifacts": [
    {
      "role": "editable",
      "path": "response-letter.md",
      "format": "markdown"
    },
    {
      "role": "editable",
      "path": "manuscript-revised.md",
      "format": "markdown"
    },
    {
      "role": "evidence",
      "path": "point-by-point.json",
      "format": "json"
    }
  ],
  "deferred": [
    {
      "comment_id": "R2-C4",
      "reason": "需新实验，本次未开展",
      "plan": "…"
    }
  ],
  "disagreements": [
    {
      "comment_id": "R1-C5",
      "basis": "…"
    }
  ],
  "honesty": {
    "new_experiments_claimed": false,
    "acceptance_claimed": false,
    "full_revision_claimed": false
  },
  "support_files_used": [
    "references/response-letter-playbook.md",
    "scripts/check_response_letter.py"
  ],
  "uncertainty": [],
  "change_log": [],
  "deliverable": {
    "path": "…",
    "format": "markdown"
  },
  "preserved_facts": [],
  "evidence_map": []
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
- [ ] 每条 `revised` 都有真实 diff，`after` 能在修改稿中找到
- [ ] 回复信与改稿双向可对应
- [ ] `honesty` 三项均为 false（除非客观为真且可证）
- [ ] 未做的新实验已明确声明
- [ ] 统计意见有方法与数值
- [ ] `check_response_letter.py` 返回 `ok: true`

## 快速路径（预算内）

1. **0–2 分钟**：把评审意见按编号拆成原子条目，建 `comments[]`。
2. **2–7 分钟**：先处理“接受并修改”类条目，同步改稿并落盘。
3. **7–13 分钟**：处理需解释/反驳/无法完成的条目，写回复与证据。
4. **最后 2 分钟**：核对每条回复与稿件修改一一对应。

## 方法增强（融合参考实践）

- **评论原子化**：一条评论含多个问题时拆成 `C01a/C01b`，逐点回应，不漏答。
- **分类处置**：`accept_edit`（接受并改）/ `clarify`（澄清）/ `respectful_disagree`（有据反驳）/ `cannot_do`（无法完成，说明原因与替代）。
- **回复矩阵**：每条记 `comment_id / quote / response / manuscript_change / location / evidence`。
- **真实修改**：声称“已修改”的必须在稿中可定位；未改的不写已改。
- **诚实标记**：未做实验、未补数据、未获伦理批准等，明确写“未开展/无法提供”，给替代方案。
- **语气**：感谢—回应—依据—修改位置，四段式；不卑不亢，不空泛道歉。
- **反驳有据**：引用文献、数据或评审误读的具体位置，不对人。

### 定向方法增强

- **来自 投稿助手（Submission Helper）**：先按 7 大类 45 项做投稿前检查（稿件、图表、引用、声明、附件、格式、系统信息），再进入回复；审稿意见按 6 类处理（方法/数据、写作表达、补充实验、文献、伦理/合规、期刊范围），不同类型用不同回复框架。
- **来自 投稿助手（Submission Helper）**：多位审稿人意见冲突时单列“歧见处理”——先找共同关切，再分别回应，不假装两方一致；回复质量按“模板匹配、逐条针对性、清单完整、策略匹配”四维自评。
- **来自 Author Response Builder**：先做输入充分性检查（评论、修改状态、实际改动、无法改动项是否齐全）；回复单元可按审稿人、评论簇或主题组织；每条先判 `acceptance / explanation / rebuttal / additional analysis`。
- **来自 Author Response Builder**：每条回复必须链接到具体修改类型（改措辞、加段落、加分析、改图表、加补充材料、加限制声明、无修改但解释）；未解决项写“能做到什么、仍受什么限制、稿件如何反映该限制”，不得用礼貌掩盖。
- **两篇合取后的改造**：回复信采用 A–H 结构——输入匹配检查、回复范围、模式分布、逐条回复、修改链接摘要、语气与边界风险、回复逻辑说明、仍需补充的信息；`point-by-point.json` 增加 `response_mode` 与 `revision_link`。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 覆盖 | 每条评论都有回应 | 补回应或标 `cannot_do` |
| G2 | 对应 | 声称的修改在稿中可定位 | 补修改或改口径 |
| G3 | 诚实 | 无虚构实验/接受/批准 | 删除不实声明并说明 |
| G4 | 证据 | 反驳与澄清均有依据 | 补引用或改为接受 |
| G5 | 结构 | 回复信按评论编号组织 | 重排并加目录 |
| G6 | 一致 | 回复、修改稿、point-by-point 三者一致 | 以修改稿为准同步 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 漏答子问题 | 一条评论只答一半 | 拆原子条目重查 |
| 声称已改但无改动 | 回复有 location，稿中无 | 补真实修改或改口径 |
| 虚构补充实验 | “我们新增了 n=30”但无数据 | 删除，改说明限制 |
| 空泛感谢 | 无实质回应 | 加具体修改与依据 |
| 反驳无据 | 只有“我们不同意” | 补数据/文献或接受 |
| 三处不一致 | 信、稿、表说法不同 | 以修改稿为准统一 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`reviewers`、`responses[]`、`artifacts`、
      `deferred`、`disagreements`、`honesty`、`support_files_used`
- [ ] `response-letter.md`、`manuscript-revised.md`、`point-by-point.json` 真实存在
- [ ] 每条评论（含子问题）均有回应
- [ ] 声称的修改均可在稿中定位，diff 真实存在
- [ ] 无虚构实验、接受或批准
- [ ] 反驳/澄清均有证据
- [ ] `check_response_letter.py` 通过，三者一致

## 最小示例（示例）

输入：3 位审稿人共 11 条意见 + 6 章基线。
输出：`responses` 11 条（8 revised、1 computed、1 deferred、1 disagreed）；
`manuscript-revised.md` 含 8 处真实改动；`deferred` 1 条注明"需新实验，本次未开展"。
