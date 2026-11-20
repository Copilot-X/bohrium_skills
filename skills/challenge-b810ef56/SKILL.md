---
name: evidence-driven-writer
description: 文献证据驱动写作：先建来源白名单与 claim 台账，再逐句写作并把每个句子映射到具体段落、图或数据；无依据的句子删除或标为作者推理。Write or rewrite prose with every claim bound to a passage, figure or dataset, producing text.md and claim-evidence.json. Use for 证据驱动写作、文献综述段落、可溯源改写、引用支撑写作。
---

# 文献证据驱动写作（Evidence-Driven Writer）

## 用途与触发

task.md 出现"每条论断要有出处""不得超出材料""依据文献写作"等要求时使用。
与 `academic-manuscript-writer` 的区别：本题的核心产物是**论断-证据映射**，文本是映射的投影。

## 输入

- `/input/task.md`：写作目标、允许使用的材料范围、引用格式、篇幅
- `/input/case.json`：`case_id`、`source_scope`、`output_contract`
- 材料：文献全文/摘要、数据表、图、笔记、已有草稿

## 执行步骤

1. **划定证据边界**：把 task.md 允许引用的材料列成白名单
   `/app/work/source-whitelist.json`。白名单外的内容一律不得作为事实来源。
2. **抽取断言**：从材料中抽出可支撑写作的原子断言，写入
   `/app/work/claims.json`：
   ```json
   {"claims": [
     {"claim_id": "C01", "text": "催化剂 A 在 350 °C 的选择性为 92%",
      "type": "quantitative", "value": "92", "unit": "%", "condition": "350 °C, 1 bar",
      "source": {"path": "materials/paper1.pdf", "locator": "p.4, Fig.2a"},
      "quote": "…", "strength": "reported|derived|inferred",
      "limits": "仅在本研究条件下"}]}
   ```
3. **写前先配**：为目标文本的每个句子分配 claim id。配不上的句子只有两种归宿：
   删除，或标记为 `author_reasoning` 并说明它不声称事实。
4. **起草**：按 claim 写句，保持原文的限定词（"可能""在…条件下""相关"）。
   不得把 `inferred` 写成 `reported`，不得去掉原文的样本量与方法限定。
5. **反向核验**：逐句回读，检查
   - 每个数字与 `quote` 一致
   - 引文确实支撑该句（不是"同主题"而是"同断言"）
   - 没有把两篇文献的结论拼接成第三篇没说过的话
6. **生成产物**：`text.md`、`claim-evidence.json`、`result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "evidence-driven-writing",
  "status": "completed|partial",
  "text": {"path": "text.md", "language": "zh|en", "word_count": 0, "genre": "review|paragraph|abstract"},
  "artifacts": [
    {"role": "editable", "path": "text.md", "format": "markdown"},
    {"role": "evidence", "path": "claim-evidence.json", "format": "json"}
  ],
  "sentences": [
    {"sentence_id": "S01", "text": "…", "claims": ["C01"],
     "support": "direct|partial|none", "note": "…"}
  ],
  "unsupported": [
    {"sentence": "…", "reason": "白名单材料无对应断言", "action": "deleted|marked_as_reasoning"}
  ],
  "coverage": {"sentences": 0, "fully_supported": 0, "partially_supported": 0, "unsupported": 0},
  "uncertainty": [{"item": "…", "reason": "…", "impact": "…"}],
  "change_log": [{"step": 1, "action": "…", "evidence": "…"}]
}
```

## 证据强度分级

| 级别 | 含义 | 写作要求 |
|---|---|---|
| `reported` | 材料明写 | 可直接陈述，保留条件 |
| `derived` | 由材料数值计算 | 给出算式，标注"计算得" |
| `inferred` | 推理得出的解释 | 必须用限定语（"提示""可能"） |
| `background` | 领域常识、无需引用 | 仅限不涉及具体数据与优先权的句子 |

## 规则

1. 一句话多证据时，`claims` 列全部；任一证据不支持即降级为 `partial`。
2. 转述保留限定词；删掉"significant""proves""demonstrates"等未经检验的强度词。
3. 不同文献结论冲突时，并列呈现并注明各自条件，不取平均、不选边。
4. 引用编号与文末一一对应；`quote` 必须是原文片段（允许省略号），不得改写。
5. 材料里没有的数字，即使"显然合理"也不能写。

## 异常处理

| 情况 | 处理 |
|---|---|
| 只有摘要没有全文 | 只写摘要能支撑的内容，标注"基于摘要" |
| 文献结论互相矛盾 | 并列 + 条件对比，`conflict` 记录 |
| 写作目标要求超出材料 | 该段标 `not_supported`，在 `unsupported` 说明 |
| 引文跨页/跨段 | 定位到起止页，`quote` 用省略号连接 |
| PDF 无法提取文本 | OCR 后标注识别风险，关键数字人工复核 |

## 自检

- [ ] 每个句子都有 `support` 判定
- [ ] 所有数字可回查到 `quote`
- [ ] `inferred` 句都带限定语
- [ ] 引用编号与文末一致
- [ ] `unsupported` 里的句子确实已删除或标记

## 快速路径（预算内）

1. **0–2 分钟**：读 task.md/case.json，划出允许引用的材料白名单。
2. **2–7 分钟**：先抽 5–8 条核心 claim，写出第一段并落盘 `text.md` 骨架。
3. **7–13 分钟**：逐句配 claim，补齐引用，处理不支持句。
4. **最后 2 分钟**：做反向核验（数字/引文/强度），写 `unsupported`。

## 方法增强（融合参考实践）

- **来源白名单**：白名单外的内容一律不得作为事实来源；领域常识也只能用于不涉及具体数据与优先权的句子。
- **claim 台账**：每条 claim 记 `text / type / value / unit / condition / source / quote / strength / limits`。
- **句子-claim 映射**：为目标文本每个句子分配 claim id；配不上的句子只有删除或标 `author_reasoning` 两种归宿。
- **证据强度**：`reported`（明写）> `derived`（计算）> `inferred`（推理）；推断必须用“提示/可能”等限定语。
- **反向核验**：逐句回读，确认引文支撑的是“同断言”而非“同主题”；数字与 quote 逐字一致。
- **冲突并列**：不同文献结论冲突时并列呈现各自条件，不取平均、不选边。

### 定向方法增强

- **来自 Claim Verifier**：先把草稿拆成原子 claim（一句多断言要拆开）；每条给 `verified / weak / conflicting / not_found` 四态之一与 0–1 置信度；超过默认条数分批核验，不抽样冒充全量。
- **来自 Claim Verifier**：对易变事实设置时效窗口；`strict_mode` 下 `weak` 与 `conflicting` 一律不得作为定论。
- **来自 Find Paper References**：建 12 字段引文审核表——正文位置、原句、显示编号、对应条目、首次出现顺序、重复出现位置、编号状态、真实性状态、完整性状态、原句支持度、问题、处理建议。
- **来自 Find Paper References**：显式检测缺号/跳号/孤儿条目、正文未引用条目、重复引用、虚假或撤稿文献、元数据不符、引用不支持原句、引用距离过远；候选文献的英文标题必须逐字完整保留，不得缩写、翻译或改写成主题标签。
- **两篇合取后的改造**：`claim-evidence.json` 增加 `confidence` 与 `citation_audit[]`；标题字段只放原文标题，相关性评价另设字段，避免“标题即结论”。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 句子覆盖 | 每个句子有 `support` 判定 | 补判定；无依据句删除或标记 |
| G2 | 数字 | 每个数字可回查到 quote | 补 quote 或删除数字 |
| G3 | 强度 | `inferred` 句带限定语，未冒充 reported | 降级措辞 |
| G4 | 引用 | 正文引用与文末一一对应 | 修编号；孤儿引用删除 |
| G5 | 不支持句 | `unsupported` 中的句子已删除或标记 | 逐条核对，不留悬空 |
| G6 | 结构 | `case_id`、`text`、`sentences`、`coverage` 齐全 | 先修 JSON 再补内容 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 听起来合理但无出处 | 句子没有 claim id | 删除或补来源；不能补则标 author_reasoning |
| 引用不支持该句 | 引文主题相关但断言不同 | 换引文或改写句子 |
| 强度拔高 | “可能”写成“证明” | 恢复限定语 |
| 拼接两篇文献 | 结论是第三篇没说过的话 | 拆句，分别挂来源 |
| 丢失条件 | 省略样本量/温度/剂量 | 补回条件，不剥离语境 |
| quote 被改写 | 引文与原文措辞不同 | 改回逐字摘录 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`text`、`sentences[]`、`unsupported[]`、`coverage`
- [ ] `text.md` 与 `claim-evidence.json` 真实存在
- [ ] 每个句子有 `support` 判定
- [ ] 所有数字可回查 quote
- [ ] `inferred` 句均带限定语
- [ ] 引用编号与文末一致
- [ ] 不支持句已删除或明确标记

## 最小示例（示例）

输入：2 篇文献 + 写作要求"写一段 150 词的进展综述"。
输出：`text.md` 6 句，`sentences` 6 条（5 direct、1 partial），
`unsupported` 1 条（要求中提到的机制文献未提供，已删除）；
`claim-evidence.json` 含 7 条 claim，每条带页码与原文 quote。
