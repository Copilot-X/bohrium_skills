---
name: review-citation-fact-check-skill
description: 引用与事实核验：从 /input 材料中抽取文内引用与参考文献表，先运行 verify_citations.py 做可复现匹配，识别 missing_reference、uncited_claim、citation_mismatch、quote_mismatch，再由模型逐条人工复核，交付 claim_source_matrix、source_status、findings、evidence、uncertainties 与 evidence-map。review skill for citation-fact-check with a deterministic precheck, source-bound work file, evidence checks and an honest result envelope. Use for 引用与事实核验、论断-来源匹配、编造/错配检出、证据绑定、结果复核.
---

# 引用与事实核验（review track · citation-fact-check）

## 用途与触发

task.md 要求“引用与事实核验、论断-来源匹配、编造/错配检出”，或 `/input/case.json`
给出 `review` / `citation-fact-check` 相关字段时使用。核心交付是
**claim-source 矩阵、来源状态、逐条问题与可回查证据**，不是一段泛泛建议。

本 Skill 的工作顺序是固定的：

1. **先运行确定性脚本** `scripts/verify_citations.py`，从材料中抽取文内引用、
   参考文献表、撤稿/版本通知，完成可复现的匹配与数值检查；
2. **再由模型人工复核**每一条候选：回到原文核对引文、判断来源是否真正支持结论、
   剔除假阳性、补充脚本无法覆盖的语义问题；
3. 最后写 `work/review-findings.json` 与 `output/` 产物并运行 `finalize`。

脚本只产生**候选**，不替模型下语义结论。引用存在不等于支持结论；脚本判
`full` 也不等于可以原样采纳，仍须人工确认。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代
同行评审/机构审查/临床决策。材料不足时只交付可确认部分，并把缺口写成
`uncertainty`，不要补造文献、DOI、数值或撤稿状态。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：本题的具体要求、范围、输出要求与证据范围。
- `/input` 下的材料：`.md/.txt/.json/.csv` 为脚本直接支持格式；
  `.tex/.bib/.ris` 支持确定性解析；`.pdf/.docx` 先由 `prepare` 提取，
  脚本会复用 `work/materials.json` 中的文本。无文字层 PDF 的 OCR 失败
  如实记录，不编造内容。
- 可选：已有工作文件、参考文献导出、撤稿通知、预印本/版本说明。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/verify_citations.py` | **本题专项预检**：抽取引用与参考文献、匹配、数值/撤稿/版本检查，写候选 |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/review-findings.json` 的字段契约 |
| `references/review-rules.md` | 四类问题的判定规则、严重度、来源状态与复核清单 |
| `examples/synthetic-input.json` | synthetic 输入示意（非正式案例，含人工复核字段） |

## 工作流

### 第 1 步：prepare（约 5% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会盘点 `/input`、提取文本/JSON/CSV、写 `work/inventory.json` 与
`work/materials.json`，生成 `work/review-findings.json` 骨架和 provisional
`result.json`。先读 `work/materials.json` 与 `task.md`，确认材料边界与缺失项。

### 第 2 步：运行确定性预检（约 10% 预算，必须先做）

```bash
python3 "$SKILL_DIR/scripts/verify_citations.py" \
  --input /input --work /app/work --output /app/output
```

脚本离线运行，支持 md/txt/json/csv/tex/bib/ris；PDF/DOCX 复用 prepare 的
提取文本。无论输入是否为空、是否缺参考文献表，**都 exit 0**，并写出：

- `work/citation-check.json`：完整确定性结果（引用、文献、矩阵、候选问题、
  来源状态、证据、不确定性与检查项）；
- `work/review-candidates.json`：可合并的候选数组；
- `output/citation-check.json`：随结果包交付的副本；
- `output/citation-precheck.md`：给人看的预检清单。

先读这四份文件（JSON 看字段，Markdown 看全貌），再开始判断。脚本状态：

| `status` | 含义 | 后续处理 |
|---|---|---|
| `ok_with_candidates` | 抽到材料并产生候选 | 逐条人工复核 |
| `ok_no_issues` | 未发现确定性候选 | 仍须做语义复核，不能写成“无问题” |
| `missing_reference_list` | 有文内引用但无参考文献表 | 每条引用按缺文献处理，并标注材料缺口 |
| `no_materials` | 没有可读材料 | `input_error`/`partial`，不生成伪结论 |
| `error` | 脚本内部异常 | 阅读 `warnings` 的 traceback，人工接管并记录 |

### 第 3 步：人工复核（约 50% 预算，不可跳过）

对 `work/citation-check.json` 的每一行 `claim_source_matrix`：

1. **核引文**：打开 `evidence` 的 `path + locator + quote`，确认 quote 逐字可查；
2. **判支持**：阅读来源可核验文本（摘要、引文片段、数据集条目或撤稿通知），
   自行在 `full / partial / none / unverifiable` 中选一个；
   - `full`：来源结论与正文断言的范围、方向、数值一致；
   - `partial`：主题相关但存在过度推断、范围扩大或数值只部分吻合；
   - `none`：来源不支持、结论相反、来源已撤稿或引用条目不可回查；
   - `unverifiable`：材料不足、仅有书目信息或来源无法定位。
3. **查假阳性**：数值是否出现在来源其他段落？引用是否只作背景？
   来源是否为综述/评论而非原始研究？不成立就放入 `rejected_candidates` 并写原因；
4. **补语义问题**：脚本未覆盖的断章取义、偷换结论、引用年份/版本错配、
   间接引用当直接证据、撤稿后仍作为主要依据等；
5. **记录复核**：`reviewed_candidates` 写接受/拒绝与理由，`uncertainties`
   写无法判断项。**未复核的候选不得直接写入 `findings`。**

四类问题的判定与严重度见 `references/review-rules.md`；摘要：

| 类型 | 含义 | 典型证据 | 默认严重度 |
|---|---|---|---|
| `missing_reference` | 文内引用在参考文献表/`.bib` 中无对应条目，或条目只是“未提供”占位 | 正文引用行 + 文献表缺失位置 | major |
| `uncited_claim` | 含数值/结果性表述的断言没有任何文内引用 | 断言原句 + 同段上下文 | minor；含统计量时 major |
| `citation_mismatch` | 引用存在但不支持结论：数值不符、年份不符、因果/绝对化过度推断、来源撤稿或仅为评论 | 断言原句 + 来源可核验文本/撤稿通知 | major；撤稿 critical |
| `quote_mismatch` | 引号内文字无法在匹配来源中逐字找到 | 断言原句 + 来源文本 | major |

### 第 4 步：写 `work/review-findings.json`（约 25% 预算）

按 `templates/work.schema.json` 写结构化工作文件。必填：
`claim_source_matrix`, `source_status`, `findings`, `evidence`, `checks`, `verdict`,
`review_status`（`pass/issues/needs_recheck/not_checked`）。
建议同时写 `uncertainties`, `reviewed_candidates`, `rejected_candidates`, `precheck`。

- `claim_source_matrix[]`：`claim_id/claim/claim_locator/source/source_locator/
  source_statement/verdict/type/reason/evidence`；`verdict` 只用四档枚举；
- `source_status[]`：`source/status/version_note/needs_recheck/evidence`；
  `status` 只用 `published/preprint/retracted/unknown`，`unknown` 必须
  `needs_recheck=true`；数值型 claim 补 `source_value/manuscript_value`；
- `findings[]`：`location{path,locator}/check_item/original_quote/issue/
  severity/type/fix/status/evidence`；`status` 用 `confirmed` 或
  `needs_recheck`，不要保留脚本的 `candidate`；
- `evidence[]`：每条 `path + locator + quote`，quote 必须逐字可查；
- `checks[]`：引用可回查、论断-来源匹配、数值一致性、来源状态、未引用论断
  等检查项，`status` 用 `pass/fail/not_checked`；未检查不等于通过；
- `uncertainties[]`：材料不足、仅有书目信息、无法联网核验、待人工确认。

### 第 5 步：写交给平台的主产物

- `output/review-report.md`：主交付物，逐条问题、位置、严重度、修改建议、
  覆盖范围、未检查项与限制；必须引用 finding id 与逐字引文；
- `output/findings.json`：`findings` 数组（可带 `reviewed_candidates`）；
- `output/evidence-map.json`：`evidence` 数组，含 path/locator/quote；
- `output/citation-precheck.md` 与 `output/citation-check.json` 由预检脚本生成，
  不要删除或改写脚本原始结果；如需补充，另写 `output/manual-review.md`。

### 第 6 步：finalize（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会读取工作文件、检查必填字段与逐字证据、登记真实产物，并写
`output/result.json`（含 `status/result/claims/uncertainties` 与
`deliverable/preserved_facts/evidence_map/change_log`）。`status` 取
`completed|partial|input_error`；缺字段、证据不匹配、主产物缺失时用 `partial`。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/review-report.md` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、产物列表；
- `claims[]`：`findings`/`candidates`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote；
- `artifacts[]`：主产物、`output/findings.json`、`output/evidence-map.json`、
  `output/citation-precheck.md`、`output/citation-check.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`；
- 顶层同时保留 `claim_source_matrix`、`source_status`、`findings`、`checks`、
  `verdict`、`review_status`，便于直接检查。

若 `/input/case.json` 的 `output_contract` 含本题特有键，必须保留原键名并
映射到真实字段或同名产物；未覆盖的键会让 finalize 降级 `partial`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 预检已运行 | `work/citation-check.json`、`output/citation-precheck.md` 存在且非空 |
| G2 | 主产物 | `output/review-report.md` 存在、非空、与任务口径一致 |
| G3 | 必填字段 | `claim_source_matrix`, `source_status`, `findings`, `evidence`, `checks`, `verdict`, `review_status` 齐全 |
| G4 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查 |
| G5 | 专项 | 每条引用可回查；论断与来源结论匹配；四类问题类型使用正确 |
| G6 | 来源状态 | `published/preprint/retracted/unknown` 合法；unknown 标 needs_recheck |
| G7 | 边界 | 未报告、冲突、失败、未检查项写入 `uncertainties`，区分错误/证据不足/建议 |
| G8 | 产物与契约 | 声明的每个 artifact 真实存在；result.json 可解析，case_id 与输入一致 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 逐条问题（位置、原文、判定、严重度、修改建议）；引用存在≠支持结论 | `output/review-report.md` + `output/findings.json` |
| 使用增益 | 可复跑的预检、匹配记录与同条件复核 | `output/citation-check.json` + `output/citation-precheck.md` |
| 创新增益 | 引用-来源对齐、段落/行级定位、撤稿/版本状态、四类问题分级 | `references/review-rules.md` + 矩阵 |
| 可靠体验 | 空输入、缺参考文献表、扫描件、撤稿/版本冲突的识别与降级 | `uncertainties` + `output/result.json` |
| 生态贡献 | SKILL.md、schema、规则、synthetic 示例可被他人复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error` 或 `partial`；不生成伪候选/结论；记录缺口 |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要”；`status=partial` |
| 缺参考文献表 | 预检状态 `missing_reference_list`；每条引用记 `missing_reference`，并写 uncertainty |
| 引用标签无法解析 | 保留原引用串，矩阵 `verdict=unverifiable`，写 uncertainty，不猜对应文献 |
| 仅有书目信息、无摘要/引文 | 矩阵 `unverifiable`；人工回查原文，不把“引用存在”写成“支持结论” |
| 数值在来源中找不到 | 记 `citation_mismatch/numeric_mismatch`；人工确认是否在来源其他段落 |
| 来源撤稿/版本不一致 | `source_status=retracted/preprint`；写撤稿/版本证据；报告影响 |
| 引文引号内文字找不到 | 记 `quote_mismatch`；无法逐字核实时删除引号或改为转述 |
| PDF/DOCX 无文本层 | 如实记录 OCR 失败或 page 缺失；不补造原文 |
| 脚本内部异常 | 读取 `warning` 中 traceback；人工接管，仍写合法 `result.json` |
| 代码执行失败 | 保留日志与失败原因；不伪造输出 |

## 最小示例（synthetic，不是正式答案）

```json
{
  "case_id": "SYNTH-REVIEW",
  "mode": "citation-fact-check",
  "summary": "Synthetic example only.",
  "checks": [
    {"item": "每条引用可回查", "status": "pass", "detail": "synthetic only"},
    {"item": "论断与来源结论匹配", "status": "not_checked", "detail": "synthetic only"}
  ],
  "verdict": "major_revision",
  "findings": [
    {
      "id": "R001",
      "location": {"path": "synthetic.md", "locator": "L1"},
      "check_item": "论断与来源结论匹配",
      "original_quote": "synthetic source sentence",
      "issue": "Synthetic issue",
      "severity": "major",
      "type": "citation_mismatch",
      "status": "confirmed",
      "fix": "synthetic fix",
      "evidence": {"path": "synthetic.md", "locator": "L1", "quote": "synthetic source sentence"}
    }
  ],
  "evidence": [
    {"path": "synthetic.md", "locator": "L1", "quote": "synthetic source sentence"}
  ],
  "uncertainties": [],
  "claim_source_matrix": [
    {
      "claim_id": "C1",
      "claim": "synthetic claim",
      "claim_locator": "synthetic.md:L1",
      "source": "[A] synthetic.md",
      "source_locator": "synthetic.md:L1",
      "source_statement": "synthetic source sentence",
      "verdict": "partial",
      "type": "citation_mismatch",
      "reason": "synthetic only",
      "evidence": {"path": "synthetic.md", "locator": "L1", "quote": "synthetic source sentence"}
    }
  ],
  "source_status": [
    {
      "source": "[A] synthetic.md",
      "status": "unknown",
      "version_note": "synthetic only",
      "needs_recheck": true,
      "evidence": {"path": "synthetic.md", "locator": "L1", "quote": "synthetic source sentence"}
    }
  ]
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。完整候选流程见
`examples/synthetic-input.json` 与脚本 `--selftest`。

## 版本与限制

- 版本 `1.1.0`：prepare/finalize 单入口 + `verify_citations.py` 确定性预检 +
  模型人工复核 + 证据/字段/产物审计。
- 不联网：参考文献真实性、DOI、撤稿与最新版本只能在 `/input` 材料范围内核验；
  不伪造在线查询结果。
- 预检是启发式：引用正则、作者-年份匹配和数值比较可能漏检或误报，必须人工复核。
- PDF/DOCX 依赖 prepare 的文本提取；扫描件无 OCR 时只能交付可确认部分并标注限制。
- 示例是 synthetic 接口样例，不是正式案例答案。
