---
name: research-review-generation-skill
description: 文献综述生成：主题综述、分组比较、证据矩阵与研究空白。交付主题、证据矩阵与研究空白，保留来源/定位与不确定性，并生成含官方 reading/writing 字段的 result.json。research skill with a source-bound work file, evidence checks and an honest result envelope. Use for 文献综述生成、review-generation、research、证据绑定、结果复核.
---

# 文献综述生成（research track · review-generation）

## 用途与触发

task.md 要求“主题综述、分组比较、证据矩阵与研究空白”，或 `/input/case.json` 给出 `research` / `review-generation`
相关字段时使用。本题属于 **文献综述** 类任务，核心交付是
**主题、证据矩阵与研究空白**，不是一段泛泛建议。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代
同行评审/机构审查/临床决策。材料不足时只交付可确认部分，并把缺口写成
`uncertainty`，不要补造。

核心原则：**有输入、有依据、有产物、有边界**。脚本负责盘点材料、建立
工作区、校验证据/字段/产物并组装 `result.json`；模型负责判断、筛选、
分析或写作。脚本不会替你生成候选、结论或审查意见。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：本题的具体问题、范围、输出要求与证据范围。
- `/input` 下的材料：`.md/.txt/.json/.csv/.pdf/.docx/.tex/.yaml` 等。
  `scripts/parsers.py` 会提取文本、JSON 数据与 CSV 行；无文字层 PDF
  尝试 OCR，失败如实记录。
- 可选：已有工作文件、术语表、数据字典、候选列表、查询日志。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_skill.py` | **一键入口**：prepare → research_profile → finalize → 契约补全；空输入/缺字段/缺产物仍写合法 `result.json` |
| `scripts/research_profile.py` | **研究 profile 执行器**：解析 9 来源/10 记录、固定问题、前瞻性筛选协议、证据矩阵、主题/冲突/空白/综述结构草稿与全部声明产物 |
| `scripts/run_case.py` | 分步入口：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物、必填字段与 `record_profile` |
| `templates/work.schema.json` | `work/research-plan.json` 的字段契约 |
| `references/research-rules.md` | 文献综述 的可执行规则与失败模式 |
| `examples/synthetic-input.json` | synthetic 输入示意（非正式案例） |

## 研究 profile（9 来源 / 10 记录）

题面末尾的固定数据版本是 **9 actual sources / 10 records**：9 份真实来源材料、10 条唯一文献记录（重复导出行单列，不占唯一名额），固定研究问题，前瞻性筛选/检索协议，逐字来源证据，以及显式的有界场景。

`scripts/research_profile.py` 的确定性步骤：

1. **来源注册**：只计实际来源文档（排除 `task.md`、`case.json`、README、检索/约束注记），记录 sha256、字符数与记录映射。
2. **记录注册**：从 CSV/JSON/BibTeX/正文题录解析；重复行标 `duplicate_of`；仅摘要/题录与缺字段单列；不补造记录。
3. **固定问题**：优先 `case.json` 的 `research_question`/`question`，其次 task.md 的 `Topic`/`Research question`；缺失写 `to_confirm`。
4. **前瞻性筛选协议**：Boolean 检索式、数据库/范围、纳排规则、逐条 `screening_log`；默认 `executed=false` 并说明只筛选所提供语料；计数必须闭合。
5. **证据矩阵**：每条记录至少一个 `path + locator + quote`；quote 逐字可查；`output/evidence-matrix.json` 与 `.csv` 同步。
6. **综述结构**：`themes`（每个主题至少 2 条记录）、`findings`（`support=full/partial/none/unverifiable` 与 `strength`）、`conflict_check`（冲突并列）、`synthesis.conclusions`、`coverage` 六面、`gaps`（含替代解释与 `why_not_omission`）、`review_structure`（逐节草稿与引用记录）。
7. **产物**：`output/research-report.md`、`output/evidence-matrix.json`、`output/references.json`、`output/screening-protocol.json`、`output/source-registry.json`、`output/evidence-matrix.csv`、`output/review-structure.json`、`output/domain-audit.json`。

实际来源数不是 9 或唯一记录不是 10 时，写入 `profile_summary`、`domain-audit.json` 与 `uncertainties`，不填充占位记录。

## 工作流

### 第 0 步：一条命令生成可复核初稿（推荐）

```bash
python3 "$SKILL_DIR/scripts/run_skill.py" \
  --input /input --work /app/work --output /app/output
```

它依次执行 prepare、`research_profile.py` 确定性抽取/分析、finalize，并检查：

1. 3 个声明产物与额外 profile 产物都存在；
2. `output_contract` 的每个键都有真实内容或明确的 partial 指针；
3. 输入为空/不可读时仍写合法 `result.json`（`status=input_error`）、退出码 0；
4. 声明输出缺失时自动重跑抽取/补写并重新 finalize。

随后读 `output/research-report.md`、`work/domain-status.json` 与 `output/domain-audit.json`，用剩余预算校正主题边界与转述；不要删除已生成的逐字证据。

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，提取文本/JSON/CSV，写 `work/inventory.json` 与
   `work/materials.json`；
2. 生成 `work/research-plan.json` 骨架（已存在则不覆盖）；
3. 写 `output/README-partial.md` 与 provisional `result.json`。

先读 `work/materials.json` 与 `task.md`，确认材料边界和缺失项。

### 第 2 步：写 `work/research-plan.json`（约 60% 预算）

按 `templates/work.schema.json` 写结构化工作文件。至少包含
`screening_criteria`, `screening_log`, `themes`, `gaps`, `findings`, `evidence`，并为每条关键判断/候选/步骤绑定 `evidence`：

```json
{
  "case_id": "SYNTH-RESEARCH",
  "mode": "review-generation",
  "summary": "Synthetic example only.",
  "question": "Synthetic research question",
  "findings": [
    {
      "id": "F001",
      "statement": "Synthetic finding",
      "sources": [
        "synthetic.md"
      ],
      "support": "partial",
      "strength": "suggestive",
      "limitations": [],
      "evidence": [
        {
          "path": "synthetic.md",
          "locator": "P1",
          "quote": "synthetic source sentence"
        }
      ]
    }
  ],
  "references": [
    {
      "key": "ref1",
      "title": "Synthetic reference",
      "doi": null
    }
  ],
  "gap_analysis": [
    {
      "gap": "Synthetic gap",
      "basis": "synthetic.md P1"
    }
  ],
  "evidence": [
    {
      "path": "synthetic.md",
      "locator": "P1",
      "quote": "synthetic source sentence"
    }
  ],
  "uncertainties": [],
  "screening_criteria": {
    "version": "v1",
    "include": [
      "synthetic include"
    ],
    "exclude": [
      "synthetic exclude"
    ],
    "priority": []
  },
  "screening_log": [
    {
      "record_id": "R001",
      "decision": "include",
      "reason": "synthetic only",
      "full_text": false,
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "themes": [
    {
      "id": "TH1",
      "name": "Synthetic theme",
      "records": [
        "R001",
        "R002"
      ],
      "boundary": "synthetic only",
      "conflicts": [],
      "evidence": [
        {
          "path": "synthetic.md",
          "locator": "P1",
          "quote": "synthetic source sentence"
        }
      ]
    }
  ],
  "gaps": [
    {
      "gap": "Synthetic gap",
      "basis": "TH1",
      "alternative_explanations": []
    }
  ]
}
```

同时写以下产物：

- `output/research-report.md`
- `output/evidence-matrix.json`
- `output/references.json`

- `output/research-report.md` 是主交付物，必须真实存在且非空。
- 所有 `quote` 必须能在对应 `path` 的材料中逐字（允许空白折叠/省略号分段）
  找到；找不到的写入 `uncertainties`，不要改写成“大意”。
- 引用存在不等于引用支持；空白由证据推出。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/research-plan.json`（或 JSON 主产物），检查必填字段 `screening_criteria`, `screening_log`, `themes`, `gaps`, `findings`, `evidence`；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/research-report.md` 与 `output/evidence-matrix.json`, `output/references.json` 的真实文件；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `completed|partial|input_error`。有缺字段、证据
不匹配、主产物缺失或执行失败时用 `partial`。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/research-report.md` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings`/`candidates`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote；
- `artifacts[]`：主产物与 `output/evidence-matrix.json`, `output/references.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`。

若 `/input/case.json` 的 `output_contract` 含本题特有键（例如
`cleaned_table`、`quality_report`、`rule_log`、`original_snapshot`），必须：

1. 在 `result.json` 顶层保留原键名；
2. 优先写同名产物 `output/<field>.json/.csv/.md`，或把对应 canonical
   字段/artifact 路径填入该键；
3. finalize 会检查 `contract_coverage`，未覆盖的键会降级 `partial` 并写入
   `uncertainties`；不要只写 `status/result/claims` 而忽略原契约字段。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/research-report.md` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `screening_criteria`, `screening_log`, `themes`, `gaps`, `findings`, `evidence` 齐全 |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查 |
| G4 | 边界 | 未报告、冲突、失败、未检查项写入 `uncertainties` |
| G5 | 专项 | 按维度分组；每篇转述准确；空白由证据推出 |
| G6 | 产物 | 声明的每个 artifact 路径真实存在 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致 |
| G8 | 研究 profile | `source_registry`/`records`/`screening_log`/`evidence_matrix` 齐全；来源数与唯一记录数如实写入 `profile_summary` |
| G9 | 筛选计数 | `identified - duplicates = screened` 且 `screened - excluded = included`；`full_text >= included` |
| G10 | 综述结构 | 每个主题至少 2 条记录；`findings` 有 `support`/`strength`；`conflict_check.checked`；`coverage` 六面状态合法；`review_structure` 逐节有记录/证据数 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 主题综述、分组比较、证据矩阵与研究空白；按维度分组；每篇转述准确；空白由证据推出 | `output/research-report.md` + evidence |
| 使用增益 | 可复跑的查询/步骤/规则/日志，以及同条件对照 | `output/evidence-matrix.json`, `output/references.json` |
| 创新增益 | 本题专项方法（review-generation）及其与基线方案的边界 | `references/research-rules.md` + `output/research-report.md` |
| 可靠体验 | 空结果、冲突、缺字段、权限/格式错误的识别与降级 | `uncertainties` + `output/result.json` |
| 生态贡献 | SKILL.md、schema、规则、synthetic 示例可被他人复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；不生成伪候选/结论/审查意见 |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要”；`status=partial` |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 候选/文献重复 | 用 DOI/标题+年份合并；保留合并依据 |
| 空结果/无命中 | 明确写“未检索到/未命中”，不把空白写成排除 |
| 数字/统计不一致 | 以数据文件为准；报告与结果文件对齐 |
| 代码执行失败 | 保留 `execution.log` 与失败原因；不伪造输出 |
| 权限/接口/格式错误 | 写 `uncertainty` 与已完成范围；建议人工接管 |

## 最小示例（synthetic，不是正式答案）

```json
{
  "case_id": "SYNTH-RESEARCH",
  "mode": "review-generation",
  "summary": "Synthetic example only.",
  "question": "Synthetic research question",
  "findings": [
    {
      "id": "F001",
      "statement": "Synthetic finding",
      "sources": [
        "synthetic.md"
      ],
      "support": "partial",
      "strength": "suggestive",
      "limitations": [],
      "evidence": [
        {
          "path": "synthetic.md",
          "locator": "P1",
          "quote": "synthetic source sentence"
        }
      ]
    }
  ],
  "references": [
    {
      "key": "ref1",
      "title": "Synthetic reference",
      "doi": null
    }
  ],
  "gap_analysis": [
    {
      "gap": "Synthetic gap",
      "basis": "synthetic.md P1"
    }
  ],
  "evidence": [
    {
      "path": "synthetic.md",
      "locator": "P1",
      "quote": "synthetic source sentence"
    }
  ],
  "uncertainties": [],
  "screening_criteria": {
    "version": "v1",
    "include": [
      "synthetic include"
    ],
    "exclude": [
      "synthetic exclude"
    ],
    "priority": []
  },
  "screening_log": [
    {
      "record_id": "R001",
      "decision": "include",
      "reason": "synthetic only",
      "full_text": false,
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "themes": [
    {
      "id": "TH1",
      "name": "Synthetic theme",
      "records": [
        "R001",
        "R002"
      ],
      "boundary": "synthetic only",
      "conflicts": [],
      "evidence": [
        {
          "path": "synthetic.md",
          "locator": "P1",
          "quote": "synthetic source sentence"
        }
      ]
    }
  ],
  "gaps": [
    {
      "gap": "Synthetic gap",
      "basis": "TH1",
      "alternative_explanations": []
    }
  ]
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。

## 版本与限制

- 版本 `2.0.0`：新增 `scripts/research_profile.py` 与 `scripts/run_skill.py`，
  直接处理题面固定的 9 来源/10 记录：来源注册、记录注册（重复/仅摘要
  单列）、固定问题、前瞻性筛选协议、逐条证据矩阵、主题归纳、冲突证据、
  研究空白、综述结构草稿与全部声明产物；计数不闭合时降级并写入
  uncertainties。
- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_tracks`；模型写内容，脚本做
  证据/字段/产物审计。
- 不联网：检索/追踪/数据库类任务只使用 `/input` 提供的材料或导出；
  不伪造在线查询结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断。
- 示例是 synthetic 接口样例，不是正式案例答案。
