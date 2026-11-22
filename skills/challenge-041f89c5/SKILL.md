---
name: research-topic-selection-skill
description: 科研选题与创新性评估：选题评估、创新性/可行性/证据缺口与风险。交付问题定义、比较矩阵、创新审计与可行性，保留来源/定位与不确定性，并生成含官方 reading/writing 字段的 result.json。research skill with a source-bound work file, evidence checks and an honest result envelope. Use for 科研选题与创新性评估、topic-selection、research、证据绑定、结果复核.
---

# 科研选题与创新性评估（research track · topic-selection）

## 用途与触发

task.md 要求“选题评估、创新性/可行性/证据缺口与风险”，或 `/input/case.json` 给出 `research` / `topic-selection`
相关字段时使用。本题属于 **选题评估** 类任务，核心交付是
**问题定义、比较矩阵、创新审计与可行性**，不是一段泛泛建议。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代
同行评审/机构审查/临床决策。材料不足时只交付可确认部分，并把缺口写成
`uncertainty`，不要补造。

核心原则：**有输入、有依据、有产物、有边界**。脚本负责盘点材料、建立
工作区、抽取材料中真实存在的文献记录、做确定性的覆盖/可行性核对、校验
证据/字段/产物并组装 `result.json`；模型负责领域判断、补证与最终复核。
脚本不会凭空生成文献、DOI、空白结论或“全球首次”判断。

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
| `scripts/run_skill.py` | **推荐入口**：prepare → research_profile → finalize → 契约补全；输入不完整也写合法 `result.json` |
| `scripts/research_profile.py` | **研究 profile 执行器**：解析 9 来源/10 记录、固定问题、前瞻性筛选协议、证据矩阵、问题定义/比较矩阵/创新边界/可行性算术/有界方案与全部声明产物 |
| `scripts/domain_work.py` | legacy 确定性初稿（保留兼容）；`run_skill.py` 优先调用 `research_profile.py` |
| `scripts/run_case.py` | 分步入口：prepare / finalize / run / inspect |
| `scripts/selftest.py` | 正向 + 空输入边界自测，真实执行并断言产物/契约 |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/research-plan.json` 的字段契约 |
| `references/research-rules.md` | 选题评估 的可执行规则与失败模式 |
| `examples/synthetic-input.json` | synthetic 输入示意（非正式案例） |

## 研究 profile（9 来源 / 10 记录）

题面末尾的固定数据版本是 **9 actual sources / 10 records**：9 份真实来源材料、10 条唯一文献记录（重复导出行单列），固定领域研究问题，前瞻性筛选/检索协议，逐字来源证据，以及显式的有界提案/约束场景。

`scripts/research_profile.py` 的确定性步骤：

1. **来源注册**：只计实际来源文档（排除 `task.md`、`case.json`、README、约束/说明注记），记录 sha256、字符数与记录映射。
2. **记录注册**：从 CSV/JSON/BibTeX/正文题录解析；重复行标 `duplicate_of`；仅摘要/题录与缺字段单列；不补造记录。
3. **固定问题**：优先 `case.json` 的 `research_question`/`question`，其次 proposal/task.md 的 `Research question`/`Proposal`/`Topic`；缺失写 `not_stated_in_materials`。
4. **前瞻性筛选协议**：Boolean 检索式、数据库/范围、纳排规则、逐条 `screening_log`；默认 `executed=false` 并说明只筛选所提供语料；计数必须闭合。
5. **证据矩阵**：每条记录至少一个 `path + locator + quote`；quote 逐字可查；`output/evidence-matrix.json` 与 `.csv` 同步。
6. **选题评估**：`problem_definition`（对象/变量/基线/预期结论/证伪条件）、`comparison_matrix`、`novelty_claims`（`scope=provided_corpus_only`，禁止无检索证据的“全球首次”）、`feasibility`（前提来源或 `unknown`）、`bounded_scenarios`（原方案/缩减模型/去范围化内部验证，附算术）、`conflict_check`。
7. **产物**：`output/research-report.md`、`output/evidence-matrix.json`、`output/references.json`、`output/screening-protocol.json`、`output/source-registry.json`、`output/evidence-matrix.csv`、`output/novelty-assessment.json`、`output/feasibility-constraints.json`、`output/domain-audit.json`。

实际来源数不是 9 或唯一记录不是 10 时，写入 `profile_summary`、`domain-audit.json` 与 `uncertainties`，不填充占位记录。

## 工作流

### 第 0 步：一条命令拿到可复核初稿（推荐，先保证不空转）

```bash
python3 "$SKILL_DIR/scripts/run_skill.py" \
  --input /input --work /app/work --output /app/output
```

它会依次执行 prepare、`research_profile.py` 确定性抽取/分析、finalize，并检查：

1. `work/research-plan.json`、3 个声明产物和 `output/result.json` 都存在；
2. `output_contract` 的每个键都有真实内容或明确的 partial 指针；
3. 输入为空/不可读时仍写合法 `result.json`（`status=input_error`）、退出码 0；
4. 任何声明输出缺失时自动补写并重跑 finalize。

随后读 `output/research-report.md`、`output/evidence-matrix.json` 与
`work/domain-status.json`，用剩余预算补充领域判断、纠正误判，再运行
`run_case.py finalize`。确定性初稿只覆盖 `provided_corpus_only`。

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

按 `templates/work.schema.json` 写结构化工作文件。必填字段为
`problem_definition`, `comparison_matrix`, `novelty_claims`, `feasibility`,
`references`, `evidence`，并为每条关键判断/文献绑定 `evidence`。
`problem_definition` 必须含 question/population/variables/baseline/
falsification_conditions；缺少时明确写 `not_stated_in_materials`，不能留空。
`novelty_claims[].scope` 若不是 `live_search`，禁止写“首次/尚无人/no prior”。

```json
{
  "case_id": "SYNTH-RESEARCH",
  "mode": "topic-selection",
  "summary": "Synthetic example only.",
  "question": "Synthetic research question",
  "findings": [
    {
      "id": "F001",
      "statement": "Synthetic finding",
      "sources": [
        "synthetic.md"
      ],
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
  "problem_definition": {
    "question": "Synthetic question",
    "scope": "synthetic corpus",
    "constraints": [
      "synthetic constraint"
    ]
  },
  "comparison_matrix": [
    {
      "option": "Synthetic option",
      "dimension": "novelty",
      "value": "medium",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "novelty_claims": [
    {
      "claim": "Synthetic novelty claim",
      "covered_by": [],
      "verdict": "not_established",
      "scope": "corpus"
    }
  ],
  "feasibility": {
    "premises": [
      {
        "premise": "synthetic premise",
        "value": "synthetic value",
        "source": "synthetic.md",
        "status": "assumption"
      }
    ],
    "calculation": "synthetic only",
    "conclusion": "conditional",
    "risks": []
  }
}
```

同时写以下产物：

- `output/research-report.md`
- `output/evidence-matrix.json`
- `output/references.json`

- `output/research-report.md` 是主交付物，必须真实存在且非空。
- 所有 `quote` 必须能在对应 `path` 的材料中逐字（允许空白折叠/省略号分段）
  找到；找不到的写入 `uncertainties`，不要改写成“大意”。
- 不写“首次/尚无人”除非有在线检索证据。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/research-plan.json`（或 JSON 主产物），检查必填字段与 `research` 专项结构；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/research-report.md` 与 `output/evidence-matrix.json`, `output/references.json` 的真实文件；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `completed|partial|input_error`。`run_skill.py` 读取
`work/domain-status.json`：无材料时强制 `input_error`；专项检查有阻塞项
（问题定义缺字段、创新措辞越界、文献无证据等）时降级 `partial`。
退出码：写出可解析 `result.json` 即 0；只有 `run_case.py` 缺失才返回 2。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/research-report.md` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings`/`candidates`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote；
- `artifacts[]`：主产物与 `output/evidence-matrix.json`, `output/references.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`；
- `run_skill`：各步骤返回码、fallback 是否触发、声明产物大小；
- `domain-audit.json`：来源数、解析到的文献数、专项 problems/warnings。

若 `/input/case.json` 的 `output_contract` 含本题特有键（例如
`研究产物`、`证据矩阵`、`不确定性` 或 `references`），必须：

1. 在 `result.json` 顶层保留原键名；
2. 写同名产物 `output/<field>.json`（中文键直接作为文件名），内容必须是
   真实结构或明确的 partial 指针；
3. finalize 检查 `contract_coverage`；`run_skill.py` 最后再补一次映射并
   重算覆盖表。未覆盖的键会降级 `partial` 并写入 `uncertainties`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/research-report.md` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `problem_definition`, `comparison_matrix`, `novelty_claims`, `feasibility`, `references`, `evidence` 齐全 |
| G3 | 证据 | 每条关键判断/文献有 path/locator/quote；quote 逐字可查 |
| G4 | 边界 | 未报告、冲突、撤稿、失败、未检查项写入 `uncertainties` |
| G5 | 专项 | 问题定义含对象/变量/基线/证伪；创新逐条划界；可行性每个前提有来源或标 unknown |
| G6 | 产物 | 声明的每个 artifact 路径真实存在且非空 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致，contract_coverage 全覆盖 |
| G8 | 不空转 | 材料不可读时仍写合法 result.json（`input_error`）且退出码 0 |
| G9 | 可复跑 | `python3 scripts/selftest.py` 正反向均通过；同一语料重跑结果一致 |
| G10 | 研究 profile | `source_registry`/`records`/`screening_log`/`evidence_matrix` 齐全；来源数与唯一记录数如实写入 `profile_summary` |
| G11 | 筛选计数 | `identified - duplicates = screened` 且 `screened - excluded = included`；`full_text >= included` |
| G12 | 有界评估 | `novelty_claims` 全部 `scope=provided_corpus_only`；`feasibility` 前提有来源或 `unknown`；`bounded_scenarios` 至少含原方案/缩减模型/去范围化内部验证 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 选题评估、创新性/可行性/证据缺口与风险；每个评分有依据；创新性不夸大；可行性条件明确 | `output/research-report.md` + evidence |
| 使用增益 | 可复跑的查询/步骤/规则/日志，以及同条件对照 | `output/evidence-matrix.json`, `output/references.json` |
| 创新增益 | 本题专项方法（topic-selection）及其与基线方案的边界 | `references/research-rules.md` + `output/research-report.md` |
| 可靠体验 | 空结果、冲突、缺字段、权限/格式错误的识别与降级 | `uncertainties` + `output/result.json` |
| 生态贡献 | SKILL.md、schema、规则、synthetic 示例可被他人复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；仍写报告/证据矩阵/文献表与合法 result.json，但全部标“无可读材料”，不生成伪文献或 DOI |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要/仅元数据”；`status=partial` |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 候选/文献重复 | 用 DOI/arXiv/标题+年份合并；保留合并依据 |
| 文献被撤稿/更正 | 保留记录并标 `retracted`/`corrected`，不得作为支持证据 |
| 检索为空/无命中 | 明确写“所提供语料未命中”，不写成“全球尚无人研究” |
| 问题定义缺字段 | 写 `not_stated_in_materials` 并转 PI 确认，不替用户补造 |
| 创新措辞越界 | 只给 `provided_corpus_only` 判定；全球新颖性必须另行在线检索并留证 |
| 脚本异常 | `run_skill.py` 仍会 finalize 并写 result.json；查 `run_skill.steps` 与 `work/domain-status.json` |
| 数字/统计不一致 | 以数据文件为准；报告与结果文件对齐 |
| 代码执行失败 | 保留 `execution.log` 与失败原因；不伪造输出 |
| 权限/接口/格式错误 | 写 `uncertainty` 与已完成范围；建议人工接管 |

## 最小示例（synthetic，不是正式答案）

```json
{
  "case_id": "SYNTH-RESEARCH",
  "mode": "topic-selection",
  "summary": "Synthetic example only.",
  "question": "Synthetic research question",
  "findings": [
    {
      "id": "F001",
      "statement": "Synthetic finding",
      "sources": [
        "synthetic.md"
      ],
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
  "problem_definition": {
    "question": "Synthetic question",
    "scope": "synthetic corpus",
    "constraints": [
      "synthetic constraint"
    ]
  },
  "comparison_matrix": [
    {
      "option": "Synthetic option",
      "dimension": "novelty",
      "value": "medium",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "novelty_claims": [
    {
      "claim": "Synthetic novelty claim",
      "covered_by": [],
      "verdict": "not_established",
      "scope": "corpus"
    }
  ],
  "feasibility": {
    "premises": [
      {
        "premise": "synthetic premise",
        "value": "synthetic value",
        "source": "synthetic.md",
        "status": "assumption"
      }
    ],
    "calculation": "synthetic only",
    "conclusion": "conditional",
    "risks": []
  }
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。

## 版本与限制

- 版本 `2.0.0`：新增 `research_profile.py` 处理题面固定的 9 来源/10 记录：
  来源注册、记录注册（重复/仅摘要单列）、固定问题、前瞻性筛选协议、逐条
  证据矩阵、比较矩阵、创新边界、可行性算术、有界方案场景与冲突证据；
  `run_skill.py` 优先调用该脚本，`domain_work.py` 仅作 legacy 回退。
- 版本 `1.1.0`：新增 `run_skill.py` 一键兜底入口与 `domain_work.py`
  确定性文献抽取/覆盖核对；无材料/缺产物时仍写合法 result.json 与全部
  声明输出；创新性只在 `provided_corpus_only` 范围内判定。
- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_tracks`；模型写内容，脚本做
  证据/字段/产物审计。
- 不联网：检索/追踪/数据库类任务只使用 `/input` 提供的材料或导出；
  不伪造在线查询结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断。
- 示例是 synthetic 接口样例，不是正式案例答案。
