---
name: literature-evidence-qa
description: 文献智能问答：对固定案例中的每个问题逐题作答，答案由原子论断组成，每条论断附 paragraph_id 与逐字引文，材料不足时明确拒答或标 partial。Answer every question in a fixed literature bundle with paragraph-level verbatim evidence into /app/output/result.json (answers[], claims[], evidence[], coverage, uncertainty). Use for 文献问答、证据定位、quote 溯源、依据问答。
---

# 文献智能问答（Literature Evidence QA）

## 用途与触发

当任务是：给定一篇（或多篇）带段落编号的论文文本 + 一组问题，要求**逐题回答并给出证据定位**时使用。

触发信号：输入含 `questions` / `paragraphs`；task.md 出现「回答」「依据」「paragraph_id」「quote」「evidence」等词。

## 支持文件（按需使用，不阻塞）

先解析技能目录，不要照抄字面量 `{SKILL_DIR}`：

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"
test -n "$SKILL_DIR" && echo "SKILL_DIR=$SKILL_DIR"
```

后续命令用 `$SKILL_DIR/scripts/...`。平台可能装到 `/skills/weekly-skill`，也可能装到
`/skills/literature-qa` 之类的题目专属目录。

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/evidence-qa-playbook.md` | 拆题、检索、写 claim、处理冲突前 | 先读；按题型与证据规则执行 |
| `templates/evidence-qa.json` | 结果骨架 | 复制结构，逐题填 answer/claims/evidence |
| `scripts/check_evidence_qa.py` | 可选审计 | `python3 "$SKILL_DIR/scripts/check_evidence_qa.py" --result /app/output/result.json --input /input --json` |
| `scripts/inspect_input.py` | 开始前盘点输入（可选） | `python3 "$SKILL_DIR/scripts/inspect_input.py" /input --out /app/work/contract_hints.json` |
| `scripts/validate_result.py` | 可选通用契约/引文回查 | `python3 "$SKILL_DIR/scripts/validate_result.py" /app/output/result.json --input /input --check-quotes` |

专用校验器会逐题检查覆盖、quote 是否逐字来自输入、inferred 是否有 rationale、
derived 是否有 formula、answer 数字是否能在同题 claims/quotes 找到。**它是审计器，
不是交付闸门**：如果命令失败或时间不足，继续手工核对并把 `audit_unavailable` 写入
`uncertainty`；不要为了让校验通过而编造证据或删掉真实答案。

## 输入

- `/input/case.json`：至少含 `case_id`、`paper_id`、`material_version`、`paragraphs[]`、`questions[]`
- `/input/task.md`：任务说明与输出契约；**以它为准**
- 材料可能还有表格、图注、参考文献著录

先运行：

```bash
python3 /skills/weekly-skill/scripts/inspect_input.py /input --out /app/work/contract_hints.json
```

然后建立段落索引 `/app/work/index.json`：

```json
{"paper_id":"...","paragraphs":[{"paragraph_id":"p1","first_sentence":"...","numbers":["92%"],
  "entities":["Ni/Al2O3"],"tables":[],"figures":[]}]}
```

## 执行步骤（交付物优先）

1. **解析问题（约 15% 预算）**：建立问题分解表，把每个 `question_id` 拆成
   1–4 个子问，标注类型（事实/条件/比较/因果/推断/缺口/计算）和需要读的段落。
   写 `/app/work/question-plan.json`：
   ```json
   [{"question_id":"q1","subquestions":["q1a ...","q1b ..."],
     "type":"condition","target_sections":["Methods","Results"]}]
   ```
2. **先写完整答案（约 40% 预算）**：按问题分解表逐题写出 `answers[]` 的
   `answer` / `conditions` / `status` / `unanswered_parts`，先落盘到
   `/app/output/result.json`。此时允许 `claims` 只写核心几条；**不要为了凑证据表
   而推迟答案**。
3. **检索并挂证据（约 25% 预算）**：按关键词 + 数值 + 实体召回候选段落，回读原文；
   每条 claim 至少 1 条 `{paragraph_id, quote}`，quote 逐字摘自原文。优先选择
   同时包含“数值 + 条件”的最短片段。
4. **补齐边界（约 10% 预算）**：材料未报告、冲突、部分回答分别进入
   `status` / `unanswered_parts` / `uncertainties`；`coverage` 记录读了哪些章节、
   跳过了什么及原因。
5. **可选审计（约 10% 预算）**：运行 `check_evidence_qa.py` 和
   `validate_result.py --check-quotes`。失败不阻塞交付：记录 `audit_unavailable`，
   手工核对题号覆盖、quote 逐字、数字出处，然后保持 `status=partial` 或
   `answered`（按实际证据）。
6. **落盘核对**：JSON 可解析；每个 question_id 都有条目；`status` 与证据一致；
   顶层 `status/result/claims/uncertainties` 存在。

## 输出契约

`/app/output/result.json`：

```json
{
  "case_id": "与输入一致",
  "paper_id": "与输入一致",
  "material_version": "与输入一致",
  "status": "answered|partial|insufficient_evidence|input_error",
  "result": {"summary": "全部问题的简要结论，逐题细节见 answers"},
  "claims": ["顶层汇总；可由 answers[].claims 展平，或使用适配器生成"],
  "uncertainties": ["材料未报告、无法判断、冲突与人工确认项"],
  "answers": [
    {
      "question_id": "q1",
      "answer": "自然语言回答",
      "conditions": ["成立条件"],
      "status": "answered | partial | insufficient_evidence | input_error",
      "claims": [
        {
          "claim_id": "q1-c1",
          "text": "原子论断",
          "kind": "reported | derived | inferred",
          "evidence": [{"paragraph_id": "p12", "quote": "逐字引文"}],
          "formula": "仅 derived 时给出算式",
          "rationale": "仅 inferred 时给出推断链",
          "confidence": 0.0
        }
      ],
      "unanswered_parts": [{"part": "子问题", "reason": "材料未报告/无法定位"}]
    }
  ],
  "coverage": {
    "documents_read": ["..."],
    "sections_read": ["..."],
    "sections_skipped": [{"section": "...", "reason": "..."}],
    "notes": "..."
  },
  "unverifiable": [{"claim_id": "q3-c1", "reason": "来源不可回溯或材料未提供"}],
  "bias_notes": [{"signal": "只引支持证据", "observed": "q3 未列反例", "impact": "结论可能偏乐观"}],
  "support_files_used": ["references/evidence-qa-playbook.md",
                         "scripts/check_evidence_qa.py"],
  "uncertainty": [{"item": "...", "reason": "...", "impact": "..."}],
  "change_log": [{"step": 1, "action": "...", "evidence": "..."}]
}
```

官方别名 `status / result / claims / uncertainties` 必须存在。可以用适配器生成：

```bash
python3 "$SKILL_DIR/scripts/emit_official.py" \
  --result /app/output/result.json --out /app/output/result.json \
  --main-field answers --profile reading --json
```

适配器只补别名，不改 `answers[]`；命令失败时手工补四个字段即可，不影响交付。

规则：
- `answers` 必须覆盖**每一个** question_id，不能省略无法回答的题。
- `answer` 中的数值必须能在同一题的某条 evidence quote 中找到。
- `kind=inferred` 不得写成 `reported`。
- `conditions` 为空数组时写 `[]`，不要省略字段。

## 证据规则

1. quote 逐字复制，保持英文原文（不翻译）。
2. 引用长度以能独立支撑论断为准，通常 8–40 词。
3. 一个论断跨方法/结果/讨论时，挂多条 evidence。
4. 材料冲突：两条 evidence 都保留，`conflict: {"detected": true, "resolution": "unresolved"}`。
5. 找不到证据：`status=insufficient_evidence`，并在 `unanswered_parts` 写明检索过的范围。

## 异常处理

| 情况 | 处理 |
|---|---|
| case.json 缺 questions | 从 task.md 提取问题清单；仍无则 `status=input_error` |
| paragraph_id 不存在 | 该条 evidence 无效，重新定位；确无则标 `insufficient_evidence` |
| 材料为扫描图片 | OCR 后用 OCR 文本定位，quote 标注 `ocr_confidence` |
| 预算将尽 | 立即写盘已完成的 answers，`status=partial` |

## 自检

```bash
python3 "$SKILL_DIR/scripts/validate_result.py" /app/output/result.json --check-quotes --input /input
```

- 每题都有条目；status 与实际证据一致
- 每条 claim 有 `paragraph_id` + `quote`
- quote 能在输入文本中逐字找到，未翻译/改写
- answer 中的数字能在同题 claims/quotes 找到
- inferred 有 rationale，derived 有 formula
- `check_evidence_qa.py` 与 `validate_result.py --check-quotes` 为**可选审计**；
  未运行或失败时，在 `uncertainties` 写 `audit_unavailable` 并手工核对上述各项
- 无 TODO/占位符

## 快速路径（预算内）

1. **0–2 分钟**：读 `task.md`、`case.json`，建问题分解表，列出全部 `question_id`。
2. **2–7 分钟**：先写完整答案（事实/条件题优先），落盘 `answers[]`；允许核心 claims 先简写。
3. **7–12 分钟**：补比较/因果/推断题，逐条挂 quote，处理冲突与未答部分。
4. **最后 3 分钟**：补顶层官方字段、覆盖与不确定性；可选跑 checker，失败不阻塞。

## 方法增强（融合参考实践）

- **原子论断**：一句只含一个事实；比较、因果、数值句强制拆分，避免一条 claim 掩盖多个判断。
- **证据等级**：`reported`（原文明写）> `derived`（由原文数值计算）> `inferred`（推断）；推断必须给 `rationale`，不得冒充 reported。
- **回答/拒答政策**：材料直接支持 → `answered`；只支持一部分 → `partial`；检索不到 → `insufficient_evidence` 并写检索范围，不猜。
- **引文最小充分**：quote 通常 8–40 词，必须能独立支撑该 claim；跨段论断挂多条 evidence。
- **冲突保留**：同一问题出现相反证据时，两条都保留，标 `conflict.detected=true`，不静默择一。
- **覆盖审计**：`coverage` 记录读了哪些文档/章节、跳过了什么及原因，防止“只答容易题”。

### 定向方法增强

- **来自 Claim Verifier**：每条 claim 记原子化后的置信度 `confidence ∈ [0,1]` 与判定依据；strict 模式下“证据弱”不得升级为 supported；有时效窗口的主张要记录材料覆盖的时间范围。
- **来自 证据化研究综述**：拆 claim 时同时标主张强度——`fact`（可核事实）/ `inference`（作者推断）/ `opinion`（评价）；opinion 不能按事实标准判 supported。
- **来自 证据化研究综述**：一致性核验专查“改写失真”——数字、范围（“10–20%”变“约 20%”）、条件（剂量、样本、温度）是否被改动；失真单列 `distortion_check[]`，不只判真/假。
- **来自 证据化研究综述**：记录来源可获取性与时效（`source_recency`、`accessible`），无法回溯的二手引用标 `secondary_source` 并降低置信度。
- **来自 证据化研究综述**：产出必须包含“无出处清单”与“偏差提示”（选择性引用、只引支持证据、样本单一），偏差只描述可观察现象，不推测作者动机。
- **来自 证据化研究综述**：该参考的云端付费路径在本环境不可用，也不应调用；只用其本地方法，`hosted_service: "not_used"`。
- **两篇合取后的改造**：`assertions[]` 增加 `claim_strength`、`confidence`、`source_recency{year, accessible}`、`distortion_check{numbers, ranges, conditions}`；顶层增加 `unverifiable[]` 与 `bias_notes[]`。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 题号覆盖 | 每个 question_id 都有 answer 条目 | 缺失题补 `insufficient_evidence`，不省略 |
| G2 | 证据 | 每条 claim 至少 1 条 `{paragraph_id, quote}` | 无证据的 claim 删除或降级 |
| G3 | 引文 | quote 能在输入中逐字找到，未翻译/改写 | 重新摘录；仍找不到则标 `not_found` |
| G4 | 数值 | answer 中每个数字都能在同题 quote 中找到 | 补 quote 或删除数字 |
| G5 | 状态 | `answered/partial/insufficient_evidence/input_error` 与实际证据一致 | 不把部分回答写成 answered |
| G6 | 结构 | `case_id/paper_id/material_version` 与输入一致 | 先修字段再补内容 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 有答案无引文 | claim.evidence 为空 | 回原文定位；找不到就降级 |
| 引文是转述 | quote 与原文措辞不同 | 改为逐字摘录，必要时加省略号 |
| 过度回答 | answer 引入材料没有的解释 | 删除或改标 `inferred` 并给 rationale |
| 漏题 | answers 数量少于 questions | 逐题对照 question_id 补齐 |
| 冲突被抹平 | 只保留支持结论的一条 | 恢复反例，标 `conflict` |
| 编造引用 | paragraph_id 不存在 | 重新定位；确认无则 `insufficient_evidence` |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`answers[]`、`coverage`、
      `support_files_used`、`uncertainty`、`change_log`
- [ ] 每个 question_id 都有条目且状态诚实
- [ ] 每条 claim 有 `paragraph_id` + 逐字 `quote`
- [ ] `kind=inferred` 有 `rationale`，`derived` 有 `formula`
- [ ] answer 中数字能在同题 claims/quotes 找到
- [ ] 冲突、未答部分、覆盖范围、不确定性均已记录
- [ ] 顶层官方字段 `status/result/claims/uncertainties` 存在
- [ ] `check_evidence_qa.py` 与 `validate_result.py` 为可选审计；失败时已记录 `audit_unavailable`
- [ ] 无 TODO/占位符，JSON 可解析

## 最小示例（示例，非正式结果）

见 `references/02-reading.md` 第 3 节。

## 版本与限制

- 版本：0.5.0（交付物优先流程；脚本改为可选审计）。
- 仅依据给定材料；材料未报告的内容不会补全。
- 图表读数会标 `estimated: true` 并给估计误差。
- 平台可能把技能装到题目专属目录；必须先解析 `$SKILL_DIR`，不要照抄 `{SKILL_DIR}`。
