---
name: evidence-provenance-checker
description: 文献证据溯源与事实核验：把待核验陈述拆成原子断言，逐条定位到段落级证据，判定 supported / partially_supported / contradicted / insufficient_evidence，附支持与反驳证据、置信度、检索范围和来源受限的改写。Verify claims in /input/claims-to-verify.json against source material and write verdicts[], evidence[], summary and uncertainty into /app/output/result.json. Use for 事实核验、证据溯源、引用核查、断言拆解、不实表述改写、claim verification、evidence provenance.
---

# 文献证据溯源与事实核验（Evidence Provenance Checker）

## 用途与触发

用于：给定待核验陈述（claims）与论文/报告/段落材料，要求判断每条陈述是否成立、证据在
哪一段、支持到什么程度，并给出可复核的核验报告。

触发信号：

- `/input/claims-to-verify.json` 存在，或 task.md 出现「核验」「溯源」「找依据」
  「判断是否成立」「改写不实表述」「claim」「verdict」「entailment」等词。
- 输入是论文正文 + 一组待核验断言/问题，要求段落级定位与支持度判定。
- 需要检查引用是否真的支持正文表述、是否存在外推、强度拔高或二手转引。

不适用：纯文献格式排版、纯翻译、无材料支撑的开放式写作。遇到这类请求先说明边界，
不要假装做过联网核验。

## 支持文件（必须使用）

`{SKILL_DIR}` 为已安装 Skill 目录（评测环境通常为 `/skills/weekly-skill`）。

| 文件 | 何时用 | 准确命令 |
|---|---|---|
| `references/claim-verification-playbook.md` | 开始拆断言、判定、处理冲突前 | 先读；按其中的类型表、判定顺序、失败模式执行 |
| `templates/verification-report.json` | 写 `/app/output/result.json` 骨架时 | 复制结构，替换 `REPLACE_WITH_*` 并逐条填 verdict |
| `scripts/inspect_claims.py` | 进入输入后第一步：盘点 claims 与候选段落 | `python3 {SKILL_DIR}/scripts/inspect_claims.py /input --out /app/work/claim_index.json --json` |
| `scripts/check_claim_verification.py` | 每次写盘后、交付前 | `python3 {SKILL_DIR}/scripts/check_claim_verification.py --result /app/output/result.json --input /input --json --out /app/work/verification_check.json` |
| `scripts/inspect_input.py` | 不确定输入结构/资源时 | `python3 {SKILL_DIR}/scripts/inspect_input.py /input --out /app/work/contract_hints.json` |
| `scripts/validate_result.py` | 通用契约与引文回查 | `python3 {SKILL_DIR}/scripts/validate_result.py /app/output/result.json --input /input --check-quotes` |

规则：

- `inspect_claims.py` 只给候选段落（recall hint），**不能**代替回读原文；判定前必须读
  命中段落及其前后段。
- `check_claim_verification.py` 的 `ok` 为 `false` 时不要交付；按 problems 逐条修，
  修不了就在 `uncertainty` 说明并保留诚实状态。
- 如果材料是 PDF/Office 且 `/input` 里只有二进制，先用环境提供的 `pdf-text-tool`
  / `office-renderer` 抽取文本到 `/app/work/extracted_text/`，再给校验器加
  `--source /app/work/extracted_text`，否则 quote 逐字检查会提示无法读取源文本。

## 输入

- `/input/claims-to-verify.json`：待核验断言。兼容 `claims[]`、`claims_to_verify[]`、
  `assertions[]` 键；每条含 `claim_id`（或 `id`）与 `text`（或 `claim`/`statement`）。
- `/input/task.md`：输出字段、判定词表、案例边界；**以它为准**。
- 材料：论文段落 JSON、PDF 抽取文本、Markdown/HTML、表格或图注；段落通常带
  `paragraph_id`/`pid`，多文档时带 `doc_id`。
- claims 是**问题**，不是已确认事实；不得把 claim 文本本身当作证据来源。

先运行：

```bash
python3 {SKILL_DIR}/scripts/inspect_claims.py /input --out /app/work/claim_index.json --json
```

输出包含每条 claim 的 `suggested_claim_type`、数值/比较/否定/显著性标记和候选段落
（带 `paragraph_id`、score、reasons）。随后建立人工可读的段落索引
`/app/work/index.json`：

```json
{"case_id":"...","documents":[{"doc_id":"paper1","paragraphs":[
  {"paragraph_id":"p2","section":"Results","numbers":["92%","350 °C"],
   "entities":["Ni/Al2O3"],"negation_terms":[],"first_sentence":"..."}]}]}
```

## 执行步骤

1. **盘点输入**：读 task.md、claims-to-verify.json 与材料；列出全部 `claim_id`、材料范围、
   可用的 locator（`paragraph_id` 优先，其次 page/section/table/figure）。
2. **拆原子断言**：一句只留一个事实；比较、因果、数值、范围、否定、显著性句强制拆分。
   每个子断言分配唯一 `assertion_id`，保留 `claim_id` 指回输入。
3. **检索定位**：用数值 + 单位、实体、谓词检索；命中后回读整段及相邻段。记录支持段落
   与反例段落，不能只找支持证据。
4. **判定**：按 `supported → contradicted → partially_supported →
   insufficient_evidence` 的顺序判断；具体规则见下节与 playbook。
5. **挂证据**：支持证据与反驳证据分开；每条含 `doc_id`、`paragraph_id`（或等价
   locator）与逐字 `quote`。`contradicted` 必须有 counter quote。
6. **写理由与改写**：`reason` 说明判定逻辑；`rewrite` 只在原文支持范围内改写。
   `insufficient_evidence` 写「材料未报告/无法判断」，不补新事实。
7. **汇总**：统计四类 verdict、`high_risk`、`checked_range`、`unchecked`、
   `uncertainty`、`change_log`、`support_files_used`。
8. **交付校验**：先写盘骨架，再补齐；最后运行专用校验器与通用校验器。

## 输出契约

`/app/output/result.json`：

```json
{
  "case_id": "与输入一致",
  "status": "completed | partial | insufficient_evidence | input_error",
  "task_family": "reading",
  "material_version": "与输入一致或省略",
  "verdicts": [
    {
      "assertion_id": "a1",
      "claim_id": "c1",
      "source_claim": "原文待核验陈述",
      "assertion": "原子化后的单事实断言",
      "claim_type": "fact|comparison|causal|numeric|range|negation|significance|attribution|other",
      "comparator": "比较对象与指标；无则空字符串",
      "negation": false,
      "significance": "p 值/CI/显著性口径；无则空字符串",
      "conditions": ["350 °C", "1 bar"],
      "verdict": "supported|partially_supported|contradicted|insufficient_evidence",
      "confidence": 0.0,
      "supporting_evidence": [
        {"doc_id": "paper1", "paragraph_id": "p2", "quote": "逐字原文",
         "evidence_level": "primary|review|secondary", "secondary_source": false}
      ],
      "counter_evidence": [],
      "limitations": [],
      "reason": "判定理由",
      "search_scope": "检索过的段落/章节/关键词；insufficient 时必填",
      "numeric_check": {"claimed": "", "observed": "", "conversion": "", "unit": ""},
      "rewrite": "受原文约束的改写",
      "match_quality": "exact|near_exact|partial|mismatch|not_found|ambiguous",
      "severity": "critical|major|minor|none",
      "verification_route": "local_text|manual|metadata",
      "retraction_flag": false,
      "preprint_flag": false
    }
  ],
  "summary": {"total": 6, "supported": 3, "partially_supported": 1,
              "contradicted": 1, "insufficient_evidence": 1,
              "high_risk": ["a3"]},
  "checked_range": "c1-c6 全部覆盖；逐条核验，未抽样",
  "unchecked": [{"claim_id": "c7", "reason": "材料未提供该文档"}],
  "bibliography_issues": [],
  "uncertainty": [{"item": "...", "reason": "...", "impact": "..."}],
  "change_log": [{"step": 1, "action": "...", "evidence": "p2"}],
  "support_files_used": [
    "references/claim-verification-playbook.md",
    "templates/verification-report.json",
    "scripts/inspect_claims.py",
    "scripts/check_claim_verification.py"
  ]
}
```

规则：

- `verdicts` 必须覆盖**每一个**输入 `claim_id`；复合断言拆成多个 `assertion_id`，
  同一 `claim_id` 可对应多条。
- 只使用四个官方 verdict 词；不要写 `not_found`、`ambiguous`、`mostly_true`、`true/false`。
  旧词 `not_found` / `ambiguous` 统一归入 `insufficient_evidence`，在 `reason` 区分
  「未检索到」与「存在但含糊/冲突」。
- `status` 是任务完成状态，不是「所有断言都 supported」；有 `contradicted` 或
  `insufficient_evidence` 仍可 `completed`。材料不可读用 `input_error`。
- `summary.total` 等于 `verdicts` 条数；四类计数必须与 verdicts 完全一致。
- `high_risk` 只放真实存在的 `assertion_id`。

## 判定规则

| verdict | 通过条件 | 必需证据 |
|---|---|---|
| `supported` | 原文直接蕴含断言，总体、条件、方向、强度一致 | ≥1 supporting quote；counter 必须为空 |
| `partially_supported` | 数值/方向部分成立，但人群、范围、条件或强度不足 | ≥1 supporting quote + `limitations`（或 counter） |
| `contradicted` | 原文明确相反、数值不相容或明确否定 | ≥1 counter quote；rewrite 给出正确表述 |
| `insufficient_evidence` | 检索后无直接证据，或仅间接/二手/含糊证据 | 非空 `search_scope` + `reason`；rewrite 写“材料未报告” |

判定顺序与边界：

1. 先找**直接蕴含**；间接推断最多 `partially_supported`。
2. `not_found` ≠ `contradicted`：没有反例不得判相反；未被实验检验的终点判
   `insufficient_evidence`。
3. **外推不是反驳**：动物/体外→人、单一剂量→所有剂量、单一模型→其他模型，按证据
   覆盖范围判 `partially_supported` 或 `insufficient_evidence`，除非原文明确否认。
4. **强度校准**：原文“可能/提示/相关/未确认”写成“证明/导致/确定”属于强度拔高。
   拆分后逐条判；只有原文明确否认因果时才判 `contradicted`。
5. **数值**：比对数值、单位、分母/基线、时间点、条件、统计口径；单位不同先换算，
   换算写入 `numeric_check`；换算不了判 `insufficient_evidence`。
6. **比较/否定/显著性**：保留比较对象、否定辖域与显著性口径（p 值/CI）；
   “无显著差异”不等于“完全相同”。
7. **冲突**：不同来源相反时，双方证据都保留，verdict 用 `partially_supported`
   或 `insufficient_evidence`，在 `limitations` 写冲突；不得静默择一。
8. **二手引用**：综述转述标 `secondary_source: true` 或 `evidence_level: review`，
   优先回溯原文；无法回溯时降低 confidence 并写入 `uncertainty`。
9. **改写边界**：rewrite 只能使用原文/证据里的信息；`insufficient_evidence` 改写为
   “材料未报告/无法判断”或删除；rewrite 中的数值必须来自证据或原断言。

## 异常处理

| 情况 | 处理 |
|---|---|
| claims-to-verify.json 不可读/缺 claims | `status=input_error`，在 `uncertainty` 写原因；不要编造 claim_id |
| 材料不可读（无文本层/扫描件） | 先 OCR/抽取；仍不可读则相关断言 `insufficient_evidence`，注明 `input_error` 范围 |
| 找不到 paragraph_id | 用 page/section/table/figure locator；仍无则保留 quote 并在 `uncertainty` 说明 |
| 引用为二手（综述引原文） | 标 `secondary_source: true`，优先回溯原文 |
| 多文档段落 id 重复 | 证据同时写 `doc_id + paragraph_id`；校验器按 doc 级别检查 |
| 断言含多个事实 | 强制拆分，逐条判定；不要用一条 verdict 掩盖部分支持 |
| 预算将尽 | 先把已完成的 verdicts 写盘，`status=partial`，在 `uncertainty` 列未完成项 |

## 自检

```bash
python3 {SKILL_DIR}/scripts/inspect_claims.py /input --out /app/work/claim_index.json --json
python3 {SKILL_DIR}/scripts/check_claim_verification.py \
  --result /app/output/result.json --input /input --json \
  --out /app/work/verification_check.json
python3 {SKILL_DIR}/scripts/validate_result.py \
  /app/output/result.json --input /input --check-quotes
```

- 每个输入 `claim_id` 都有 verdict；没有漏项，也没有重复造等价断言。
- verdict 只使用四个官方词；`supported` 没有 counter evidence。
- 每条证据有 `doc_id + paragraph_id`（或等价 locator）与逐字 quote。
- `contradicted` 有 counter quote；`insufficient_evidence` 有 `search_scope`。
- 数值、单位、比较基线、时间点、条件已比对；`numeric_check` 有换算记录。
- `summary` 计数与 `verdicts` 一致；`high_risk` 指向真实 `assertion_id`。
- `rewrite` 未引入材料外信息；rewrite 数值都能在证据/原断言中找到。
- `uncertainty`、`change_log`、`checked_range`、`support_files_used` 已填。
- `check_claim_verification.py` 返回 `ok: true`（退出码 0）；`validate_result.py` 无
  problems。

## 快速路径（预算内）

1. **0–2 分钟**：读 task.md、claims-to-verify.json；运行 `inspect_claims.py`，列出全部
   `claim_id` 与材料范围。
2. **2–6 分钟**：拆前 3–5 条断言，建立段落索引，先写 `result.json` 骨架
   （`status: "partial"`）。
3. **6–13 分钟**：补齐其余断言，挂支持/反驳证据，写 `reason` 与 `rewrite`。
4. **最后 2 分钟**：跑专用校验器与通用校验器，修 problems；无法修的写入
   `uncertainty` 并保持诚实状态。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 断言覆盖 | 每个输入 `claim_id` 都有 verdict | 补 `insufficient_evidence` + 检索范围，不省略 |
| G2 | 拆分 | 每条 assertion 只含一个事实；无等价重复 | 重新拆分并去重 |
| G3 | 证据定位 | 每条证据有 locator + 逐字 quote | 回原文重摘；找不到则降级 |
| G4 | 判定一致 | `supported` 无 counter；`contradicted` 有 counter；`partially` 有局限 | 按 verdict 规则修正 |
| G5 | 数值 | 数值/单位/基线/条件已比对，换算有记录 | 补 `numeric_check` 或改判 |
| G6 | 冲突 | 冲突双方证据都在，verdict 已降级 | 恢复反例并写明冲突 |
| G7 | 改写 | rewrite 不引入材料外信息，数值有出处 | 删除越界内容或改“材料未报告” |
| G8 | 汇总 | summary 计数、high_risk、uncertainty、change_log 一致 | 重算并修正 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 主题相关当支持 | 证据只谈同一主题，没有断言谓词 | 降为 `insufficient_evidence` 或找直接证据 |
| 未检索到当反驳 | 没有反例 quote 却判 `contradicted` | 改 `insufficient_evidence`，写检索范围 |
| 外推当反驳 | 原文只是没测目标人群/终点 | 改 `partially_supported`/`insufficient_evidence` |
| 强度拔高 | 原文“可能”，断言“证明” | 拆分子断言，按证据强度判 |
| 比较基线错误 | 把组内变化当成组间差异 | 重算差值，写 `numeric_check` |
| 否定辖域丢失 | “无显著差异”写成“完全相同” | 保留否定范围，改 rewrite |
| 二手当原始 | 综述转述被标 primary | 标 `secondary_source`，回溯原文 |
| quote 非逐字 | 引文与原文措辞不同 | 重新摘录，省略用 `…` |
| 一句多事实 | 一条 assertion 含两个判断 | 拆分后逐条判定 |
| 改写越界 | rewrite 出现材料没有的数字/机制 | 删除或改为“材料未报告” |
| 冲突被抹平 | 只留支持证据 | 双方证据并列，verdict 降级 |
| 汇总不一致 | summary 计数与 verdicts 不符 | 重算并修正 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`status`、`verdicts[]`、`summary`、
      `checked_range`、`uncertainty`、`change_log`、`support_files_used`
- [ ] 每个输入 `claim_id` 都有 verdict，复合断言已拆分
- [ ] verdict 只用四个官方词
- [ ] 每条证据有 `doc_id + paragraph_id`（或等价 locator）与逐字 quote
- [ ] `supported` 无 counter；`contradicted` 有 counter quote
- [ ] `insufficient_evidence` 有 `search_scope`，rewrite 未补新事实
- [ ] 数值/单位/条件/比较基线/否定/显著性已核对
- [ ] `summary` 计数与 verdicts 一致；`high_risk` 指向真实断言
- [ ] `check_claim_verification.py` 与 `validate_result.py` 均通过
- [ ] 无 TODO/占位符，JSON 可解析

## 最小示例（示例，非正式结果）

断言「该催化剂在 300 °C 下转化率超过 95%」；原文为「At 350 °C the conversion reached
92%，and at 250 °C it was below 50%」。判定：条件与数值均不相容 → `contradicted`，
counter quote 取原文句，`numeric_check` 记录 300/95 vs 350/92，rewrite 改为
「在 350 °C 下转化率为 92%」。若原文只报告 350 °C、未测 300 °C，则不能判
`contradicted`，应判 `insufficient_evidence` 并在 `search_scope` 写检索范围。

## 已知限制

- 本环境不保证联网：只能做材料内一致性核对；不得声称已在线核验 DOI/PMID。
  外部核验条目标 `verification_route: "manual"`。
- 扫描版 PDF/图片没有文本层时，quote 校验依赖 OCR/抽取结果，需在 `uncertainty`
  标注 OCR 置信度。
- 材料只给摘要/图注时，证据强度受限；不能把摘要结论当作全文结果的完整证据。
- 判定是证据蕴含判断，不是对作者动机或研究质量的全面评价。
