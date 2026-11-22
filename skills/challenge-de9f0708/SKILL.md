---
name: review-compliance-review-skill
description: 学术规范与合规检查：学术规范、引用/数据/伦理/利益冲突与合规检查。运行可执行 compliance_check.py，从 /input 抽取声明与适用范围，生成带 path+locator+quote 证据的合规矩阵、findings 与 uncertainties；未报告不写成合规，缺依据标待复核，不替代机构审查。Use for 学术规范与合规检查、compliance-review、review、证据绑定、结果复核.
---

# 学术规范与合规检查（review track · compliance-review）

## 用途与触发

task.md 要求“学术规范、引用/数据/伦理/利益冲突与合规检查”，或
`/input/case.json` 给出 `review` / `compliance-review` 相关字段时使用。
核心交付是**逐项有结论、有依据位置、有缺口与整改建议的合规矩阵**，
不是一段泛泛建议。

本 Skill 覆盖 14 个检查项：伦理审批、知情同意、数据可用性、利益冲突、
基金、作者贡献、致谢、重复发表、抄袭/查重、材料内部文字复用、版权与
许可、隐私与可识别信息、投稿要求、报告规范。每项都要有状态（pass /
issue / needs_recheck / not_checked / not_applicable）、依据位置、缺口与
整改建议。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代同行
评审/机构审查/伦理委员会/法务意见。材料不足时只交付可确认部分，并把
缺口写成 `uncertainties`，不要补造。

核心原则：**有输入、有依据、有产物、有边界**。脚本负责盘点材料、抽取
声明、校验证据/字段/产物并组装 `result.json`；模型负责复核、筛选、
判断与写作。脚本不会替你生成最终审查意见。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：本题的具体问题、范围、输出要求与证据范围。
- `/input` 下的材料：`.md/.txt/.json/.csv/.pdf/.docx/.tex/.yaml/.bib` 等。
  `scripts/parsers.py` 会提取文本、JSON 数据与 CSV 行；无文字层 PDF
  会尝试 OCR，失败如实记录。图片内容需人工查看。
- 可选：声明页、伦理批件、同意书模板、授权/许可、目标期刊指南、
  查重报告、数据字典、作者列表。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/compliance_check.py` | **专项脚本**：从 `/input` 抽取声明与适用范围，生成合规矩阵、findings、uncertainties 与逐字证据（不写最终报告） |
| `scripts/selftest.py` | synthetic 自测：正常材料、空输入、缺伦理/数据声明；有入口时跑 prepare→finalize 与缺产物/缺字段回归 |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/review-findings.json` 的字段契约（含 `compliance_matrix`） |
| `references/review-rules.md` | 14 个 facet、状态口径、证据/负向检索、误报抑制与人工复核边界 |
| `examples/synthetic-input.json` | synthetic 工作文件示意（非正式案例） |

## 工作流

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，提取文本/JSON/CSV，写 `work/inventory.json` 与
   `work/materials.json`；
2. 生成 `work/review-findings.json` 骨架（已存在则不覆盖）；
3. 写 `output/README-partial.md` 与 provisional `result.json`。

先读 `work/materials.json`、`work/inventory.json` 与 `task.md`，确认
稿件版本、可用材料、题目指定的检查项、输出契约键和缺失项。材料缺失、
文件不可读、图表无文字层要立即写入 `uncertainties`，不要在后面假装查过。

### 第 2 步：运行 compliance_check.py（约 20% 预算）

```bash
python3 "$SKILL_DIR/scripts/compliance_check.py" \
  --input /input --work /app/work --output /app/output --merge-work
```

脚本真实执行以下动作：

1. 优先读取 `work/materials.json`；没有时直接扫描 `/input` 的文本/JSON/
   CSV/TeX/YAML，PDF/DOCX 尝试复用 `parsers.py`，读不到就登记 warning；
2. 按段落建立 `P1..Pn` 定位与行号，抽取伦理、同意、数据、利益冲突、
   基金、贡献、致谢、重复发表、查重、版权、隐私、投稿要求、报告规范
   等声明与适用范围句；
3. 区分“声明存在”“声明不完整”“声明与材料矛盾”“未报告”“无法判断”；
   **未报告写成 `issue`，不写成 `pass`**；
4. 生成 `output/compliance-matrix.json`、`output/compliance-findings.json`、
   `output/compliance-evidence-map.json`、`output/compliance-check.md` 与
   `work/compliance-draft.json`；
5. `--merge-work` 只填充 `work/review-findings.json` 中为空的字段，
   不覆盖模型已写内容。

脚本输出的是**证据索引与缺口清单**，不是最终结论。模型必须逐项复核：
确认 quote 回查原文、确认适用性、剔除误报、补充脚本未覆盖的语义判断。

### 第 3 步：复核并写 `work/review-findings.json`（约 40% 预算）

按 `templates/work.schema.json` 写结构化工作文件。必需字段：
`compliance_checklist`, `compliance_matrix`, `guideline_source`,
`review_status`, `findings`, `evidence`。建议同时写 `checks`,
`uncertainties`, `not_checked`, `verdict`, `human_review_items`。

矩阵行最小结构：

```json
{
  "check_id": "ethics_approval",
  "facet": "ethics_approval",
  "rule_id": "CMP-ETH-01",
  "rule_version": "v1",
  "status": "needs_recheck",
  "applicability": "applicable",
  "severity_if_issue": "major",
  "reason": "有审批声明，但未提供批件编号或委员会名称。",
  "evidence": [
    {"path": "manuscript.md", "locator": "P6", "line_start": 8,
     "line_end": 8, "quote": "The study was approved by the institutional review board.",
     "supports": "declaration"}
  ],
  "gaps": ["缺少伦理委员会名称、批件编号、批准日期与覆盖范围"],
  "actions": ["补充上述字段；如为豁免，写明豁免依据与批准机构"],
  "manual_review": true,
  "confidence": "medium",
  "finding_ids": ["C001"]
}
```

finding 的最小结构：

```json
{
  "id": "C001",
  "check_id": "ethics_approval",
  "rule_id": "CMP-ETH-01",
  "rule_version": "v1",
  "locator": {"path": "manuscript.md", "locator": "P6",
              "line_start": 8, "line_end": 8},
  "original_quote": "The study was approved by the institutional review board.",
  "severity": "major",
  "type": "compliance",
  "issue_type": "incomplete_declaration",
  "status": "needs_recheck",
  "fix": "补充伦理委员会全称、批件编号、批准日期与覆盖范围。",
  "impact": "无法确认审批范围覆盖本研究。",
  "false_positive_check": "已检出审批声明，问题是信息不足，不是未审批。",
  "evidence": [
    {"path": "manuscript.md", "locator": "P6", "quote": "The study was approved by the institutional review board."}
  ]
}
```

硬规则：

1. **未报告 ≠ 合规**；缺失声明必须是 `issue`，并给出缺口/整改。
2. **未检查 ≠ 通过**；没有指南/查重语料/批件原件时写
   `needs_recheck` 或 `not_checked`。
3. 每条 finding 必须有 `path + locator + quote`；quote 必须逐字来自
   材料（允许空白折叠/省略号分段），找不到就放进 `uncertainties`。
4. 缺失类 finding 写 `absence_check`（检索范围、检索词、真实命中数）与
   `false_positive_check`；不得把“未命中”说成“已确认不存在”。
5. 不替代机构审查；不编造审批号、同意范围、DOI、许可、期刊条款或数据。

### 第 4 步：写产物（约 20% 预算）

必须写：

- `output/review-report.md`：主交付物，逐条问题、位置、严重度、依据、
  整改建议；显式写“不替代机构审查”与待复核/未检查项。
- `output/findings.json`：与 work 中 `findings` 一致。
- `output/evidence-map.json`：与 work 中 `evidence` 一致。
- `output/compliance-matrix.json`：脚本已生成；模型可补充语义判断，
  但不得删除真实证据或把未报告项改成 pass。

`output/review-report.md` 建议包含：审查范围与输入、方法与规则版本、
合规矩阵摘要、逐条 findings、证据定位、待复核/未检查项、人工复核边界。

### 第 5 步：finalize（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/review-findings.json`，检查必填字段与 `case_id`；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/review-report.md` 与 `output/findings.json`,
   `output/evidence-map.json`, `output/compliance-matrix.json` 的真实文件；
4. 写 `output/result.json`，含 `status/result/claims/uncertainties` 与
   `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `completed|partial|input_error`。有缺字段、证据不匹配、
主产物缺失或执行失败时用 `partial`。

## 状态口径

| 状态 | 含义 |
|---|---|
| `pass` | 输入中有声明，且在输入范围内未发现缺口；不是认证 |
| `issue` | 应报告而未报告，或声明与材料直接矛盾 |
| `needs_recheck` | 有声明但不完整/需批件、同意书、许可或指南原件核对 |
| `not_checked` | 输入不足，无法判断；**不等于合规** |
| `not_applicable` | 材料明确说明不适用，且无相反指标 |

`review_status` 取 `pass|issues|needs_recheck|not_checked`，必须与矩阵
一致：有 `issue` 行时不得写 `pass`；有未检查项时不得把整体写成 `pass`。

## 人工复核边界

必须在报告与 `human_review_items` 中写明：

- 本工具与报告不替代机构审查、伦理委员会、法务或目标期刊编辑意见。
- 声明存在性来自关键词/段落规则；未命中不等于不存在，需人工通读。
- 不联网、不查重、不核验批件/文献/DOI/期刊指南真伪。
- 未提供批件、同意书、许可、指南或查重语料时，只报待复核/未检查。
- PDF/扫描件无文字层、图片内容与补充材料需人工查看。
- 材料缺失、无法判断与运行失败分别说明，不能合并成“通过”。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/review-report.md` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings`/`candidates`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote；
- `artifacts[]`：主产物与 `output/findings.json`, `output/evidence-map.json`,
  `output/compliance-matrix.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`。

若 `/input/case.json` 的 `output_contract` 含本题特有键，必须：

1. 在 `result.json` 顶层保留原键名；
2. 优先写同名产物 `output/<field>.json/.csv/.md`，或把对应 canonical
   字段/artifact 路径填入该键；
3. finalize 会检查 `contract_coverage`，未覆盖的键会降级 `partial` 并写入
   `uncertainties`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/review-report.md` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `compliance_checklist`, `compliance_matrix`, `guideline_source`, `review_status`, `findings`, `evidence` 齐全 |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查 |
| G4 | 边界 | 未报告、冲突、失败、未检查项写入 `uncertainties` |
| G5 | 专项 | 检查项逐条有结论；证据不足标待复核；不替代机构审查 |
| G6 | 产物 | 声明的每个 artifact 路径真实存在 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致 |
| G8 | 矩阵一致 | 有 `issue` 行时 `review_status` 不为 `pass`；未报告项不是 `pass` |
| G9 | 可复跑 | `scripts/selftest.py` 通过；同一输入重复运行结论一致 |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；不生成伪候选/结论/审查意见；脚本仍写合法 JSON 并 exit 0 |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要”；`status=partial` |
| PDF/扫描件无文字层 | 写 `uncertainties`；未读区域不得写成已检查 |
| 目标期刊指南缺失 | `submission_requirements=not_checked`；不做格式合规判定 |
| 批件/同意书/许可缺失 | `needs_recheck`；写明需要哪份文件与哪些字段 |
| 查重语料缺失 | `plagiarism=not_checked`；不把“未发现”写成“无抄袭” |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 声明与材料冲突 | 标 `inconsistent_declaration`，并列两处证据；不推断作者动机 |
| 空结果/无命中 | 明确写“未检出/未命中”与检索范围；不把空白写成排除 |
| 代码执行失败 | 保留日志与失败原因；不伪造输出 |
| 权限/接口/格式错误 | 写 `uncertainty` 与已完成范围；建议人工接管 |

## 最小示例（synthetic，不是正式答案）

```bash
python3 "$SKILL_DIR/scripts/compliance_check.py" \
  --input /input --work /app/work --output /app/output --merge-work
```

示意输出（`output/compliance-matrix.json` 摘要，**synthetic only**）：

```json
{
  "case_id": "SYNTH-REVIEW",
  "status": "needs_recheck",
  "matrix": [
    {
      "check_id": "ethics_approval",
      "rule_id": "CMP-ETH-01",
      "rule_version": "v1",
      "status": "not_checked",
      "reason": "Synthetic example only; no ethics statement in the synthetic material.",
      "evidence": [
        {"path": "synthetic.md", "locator": "P1",
         "quote": "synthetic source sentence", "supports": "absence_search_anchor"}
      ],
      "gaps": ["synthetic gap"],
      "actions": ["synthetic action"],
      "manual_review": true
    }
  ],
  "uncertainties": [
    {"item": "synthetic", "reason": "Synthetic example only.",
     "impact": "No real conclusion.", "action": "Run on actual material."}
  ]
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。

## 版本与限制

- 版本 `1.1.0`：单入口 + `compliance_check.py` 专项抽取 + 共享引擎；
  模型写内容，脚本做证据/字段/产物审计。
- 不联网：检索/追踪/数据库类任务只使用 `/input` 提供的材料或导出；
  不伪造在线查询结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断。
- 声明存在性检测是关键词/段落启发式，未命中不等于不存在。
- 示例与自测材料都是 synthetic，不是正式案例答案。
