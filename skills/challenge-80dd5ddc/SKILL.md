---
name: library-kb-qa-skill
description: 科研知识库问答：知识库问答、检索增强、引用定位与拒答边界。交付逐句引用、拒答与冲突并列，保留来源/定位与不确定性，并生成含官方 reading/writing 字段的 result.json。library skill with a source-bound work file, evidence checks and an honest result envelope. Use for 科研知识库问答、kb-qa、library、证据绑定、结果复核.
---

# 科研知识库问答（library track · kb-qa）

## 用途与触发

task.md 要求“知识库问答、检索增强、引用定位与拒答边界”，或 `/input/case.json` 给出 `library` / `kb-qa`
相关字段时使用。本题属于 **知识库问答** 类任务，核心交付是
**逐句引用、拒答与冲突并列**，不是一段泛泛建议。

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
- 本版实际可能在 `/input` 给出一个**真实文件库**：多目录文件、改名副本（alias）、
  行政版本说明（supersedes / 修订 / 作废）、元数据与权限记录，以及一组问题或
  workorder。先按实际文件处理，不按文件名猜内容。

```bash
python3 "$SKILL_DIR/scripts/build_library_index.py" --input /input \
  --out /app/work/library-index-build.json --json
```

索引给出 `path/sha256/version_token/version_family/sections/blocks[]`、
`duplicate_groups`、`version_groups`、`notes[]`（alias/版本说明）与 `unreadable`。
引用定位用 `page`（PDF）或 `section + line`（文本），不能只给文件名。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/library-plan.json` 的字段契约 |
| `references/library-rules.md` | 知识库问答 的可执行规则与失败模式 |
| `scripts/build_library_index.py` | **问答前必跑**：路径/版本/章节/行号/别名/重复/不可读索引 |
| `scripts/check_kb_answers.py` | **finalize 前必跑**：逐句引用支持、拒答、冲突、版本与契约检查 |
| `examples/synthetic-input.json` | synthetic 输入示意（非正式案例） |

## 工作流

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，提取文本/JSON/CSV，写 `work/inventory.json` 与
   `work/materials.json`；
2. 生成 `work/library-plan.json` 骨架（已存在则不覆盖）；
3. 写 `output/README-partial.md` 与 provisional `result.json`。

先读 `work/materials.json` 与 `task.md`，确认材料边界和缺失项。

### 第 2 步：写 `work/library-plan.json`（约 60% 预算）

按 `templates/work.schema.json` 写结构化工作文件。至少包含
`questions`, `items`, `answers`, `retrieval`, `evidence`，并为每条关键判断/候选/
步骤绑定 `evidence`。先用 `library-index-build.json` 定位文件与段落，再回读原文
摘 quote；不要只凭文件名或索引摘要回答：

```json
{
  "case_id": "SYNTH-LIBRARY",
  "mode": "kb-qa",
  "summary": "Synthetic example only.",
  "items": [
    {
      "id": "I001",
      "path": "synthetic.md",
      "kind": "text",
      "metadata": {
        "title": "Synthetic item"
      },
      "version": "v1",
      "hash": "sha256:synthetic",
      "status": "indexed",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "operations": [
    {
      "action": "index",
      "target": "synthetic.md",
      "status": "ok",
      "detail": "synthetic only"
    }
  ],
  "findings": [
    {
      "id": "F001",
      "statement": "Synthetic finding",
      "evidence": [
        {
          "path": "synthetic.md",
          "locator": "P1",
          "quote": "synthetic source sentence"
        }
      ]
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
  "answers": [
    {
      "id": "A1",
      "question": "Synthetic question",
      "status": "answered",
      "sentences": [
        {
          "text": "Synthetic answer sentence.",
          "citations": [
            {
              "path": "synthetic.md",
              "locator": "P1",
              "quote": "synthetic source sentence"
            }
          ]
        }
      ],
      "missing_evidence": [],
      "conflict": []
    }
  ],
  "retrieval": [
    {
      "answer_id": "A1",
      "record_id": "R001",
      "locator": "P1",
      "snippet": "synthetic source sentence"
    }
  ]
}
```

### 第 2b 步：逐句引用、拒答与冲突（本题得分核心）

1. **逐句引用且支持该句**：`answered` 的每个 sentence 至少一条 citation；
   citation 的 quote 必须包含该句的数字与关键实体（如 OD600、ZX-99、pH 7.4）。
   **逐字存在但与句子无关的引用不算支持**；找不到支持证据的句子删除或改为
   `insufficient_evidence`。
2. **无证据拒答**：`status: "insufficient_evidence" | "refused"`，`sentences: []`，
   `missing_evidence[]` 写缺什么（具体指标/实体/文件），并给已检索范围
   `retrieval_scope`；不得用“没有找到相关描述”一句带过，也不得补造。
3. **冲突并列**：`status: "conflict"`，至少两句、两个不同 path（或明确版本），
   `conflict.sources[]` ≥2，`resolution` 写“未解决/以某版本为准/需人工确认”及依据。
   **不得为了给出单一答案而丢掉一方**。
4. **版本与副本**：同一 `version_family` 的多个版本不当作独立证据；引用时写
   `version`，并在 `items[].status` 标 `current|superseded|duplicate`，
   `duplicate_of`/`superseded_by` 给路径。冲突涉及版本差异时说明版本关系。
5. **文件层覆盖**：`items[]` 覆盖输入文件类型、元数据、目录层级、重复文件、
   版本差异、权限与来源六个维度；无文本层/损坏/加密/无权限进
   `unreadable[]`/`uncertainties[]`，不写成“知识库没有”。

回答与检索记录示例（synthetic）：

```json
{
  "questions": [{"id": "Q1", "text": "original buffer pH?"}, {"id": "Q3", "text": "OD600 for ZX-99?"}],
  "answers": [
    {"id": "Q1", "question": "What buffer pH was used in the original protocol?",
     "status": "answered",
     "sentences": [{"text": "The original protocol used 20 mM Tris-HCl at pH 7.4.",
                    "citations": [{"path": "kb/paper_a.md", "version": "v1",
                                   "locator": "L3", "quote": "The running buffer was 20 mM Tris-HCl at pH 7.4."}],
                    "support": "direct"}],
     "missing_evidence": [], "conflict": []},
    {"id": "Q3", "question": "What was the OD600 for strain ZX-99?",
     "status": "insufficient_evidence", "sentences": [],
     "missing_evidence": ["OD600 for strain ZX-99", "any document mentioning ZX-99"],
     "retrieval_scope": "all 9 documents; 0 matches for ZX-99 or OD600",
     "conflict": []}
  ],
  "retrieval": [{"answer_id": "Q1", "path": "kb/paper_a.md", "locator": "L3",
                 "snippet": "The running buffer was 20 mM Tris-HCl at pH 7.4.", "version": "v1"}]
}
```

同时写以下产物：

- `output/library-results.json`（主交付物，必须真实存在且非空）
- `output/library-index.json`（文件/版本/别名/重复索引）
- `output/operations.log.json`（retrieval/answer/refusal/recovery 操作及结果）
- `output/results/operation_evidence.json`（检索与引用证据）
- `output/results/failure_recovery.json`（不可读/权限/版本冲突/拒答处理）
- 若 `output_contract` 还有其他键，一律写 `output/results/<key>.json`（或 `.csv/.md`），
  **不要只写在 output 根目录**。

### 第 2c 步：自查（finalize 前必跑）

```bash
python3 "$SKILL_DIR/scripts/check_kb_answers.py" --input /input \
  --work /app/work --output /app/output --json
```

`ok:false` 时逐条修：问题漏答、句子无引用、引用与句子实体/数值不匹配、
拒答缺 missing_evidence、冲突少于两源、同族版本当独立证据、quote 非逐字、
契约产物缺失。修不了的在 `uncertainties` 写清并降级 `partial`。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/library-plan.json`（或 JSON 主产物），检查必填字段 `answers`, `evidence`；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/library-results.json` 与 `output/library-index.json`, `output/operations.log.json`，
   以及 `output/results/` 下所有契约产物；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `answered|partial|input_error`。有缺字段、证据
不匹配、主产物缺失或执行失败时用 `partial`。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/library-results.json` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings`/`candidates`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote；
- `artifacts[]`：主产物与 `output/library-index.json`, `output/operations.log.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`。

若 `/input/case.json` 的 `output_contract` 含本题特有键（例如
`cleaned_table`、`quality_report`、`rule_log`、`original_snapshot`），必须：

1. 在 `result.json` 顶层保留原键名；
2. 写同名产物到 **`output/results/<field>.json`**（或 `.csv/.md`）；`output/results/`
   会被 finalize 自动登记。根目录只放主产物与 `result.json`；
3. finalize 会检查 `contract_coverage`，未覆盖的键会降级 `partial` 并写入
   `uncertainties`；不要只写 `status/result/claims` 而忽略原契约字段。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/library-results.json` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `answers`, `evidence` 齐全 |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查 |
| G4 | 边界 | 未报告、冲突、失败、未检查项写入 `uncertainties` |
| G5 | 专项 | 答案每句有引用，且引用含该句数字/实体；无证据拒答；冲突≥2源 |
| G5b | 版本 | 同族版本/副本不当作独立证据；引用带 version |
| G6 | 产物 | 声明的每个 artifact 路径真实存在；契约键在 output/results/ 有产物 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致 |
| G8 | 自查 | `check_kb_answers.py` 返回 `ok:true` |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 知识库问答、检索增强、引用定位与拒答边界；答案每句有来源；无证据时拒答；冲突材料并列 | `output/library-results.json` + evidence |
| 使用增益 | 可复跑的查询/步骤/规则/日志，以及同条件对照 | `output/library-index.json`, `output/operations.log.json` |
| 创新增益 | 本题专项方法（kb-qa）及其与基线方案的边界 | `references/library-rules.md` + `output/library-results.json` |
| 可靠体验 | 空结果、冲突、缺字段、权限/格式错误的识别与降级 | `uncertainties` + `output/result.json` |
| 生态贡献 | SKILL.md、schema、规则、synthetic 示例可被他人复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；不生成伪候选/结论/审查意见 |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要”；`status=partial` |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 候选/文献重复 | 用 DOI/标题+年份合并；保留合并依据 |
| 空结果/无命中 | `insufficient_evidence` + `missing_evidence` + `retrieval_scope` |
| 数字/统计不一致 | 以数据文件为准；报告与结果文件对齐 |
| 代码执行失败 | 保留 `execution.log` 与失败原因；不伪造输出 |
| 权限/接口/格式错误 | 写 `unreadable[]`/`uncertainty` 与已完成范围；建议人工接管 |
| 重名/改名文件 | sha256 判重 + alias 来源；副本写 `duplicate_of`，不当独立证据 |
| 版本冲突 | `status=conflict` 或引用带 version 并说明 current/superseded |
| 扫描件无文本层 | 尝试 OCR；失败进 `unreadable[]`，不写成“知识库没有” |
| 问题不在知识库内 | 拒答并写缺什么；不得用相近主题的句子冒充答案 |

## 最小示例（synthetic，不是正式答案）

```json
{
  "case_id": "SYNTH-LIBRARY",
  "mode": "kb-qa",
  "summary": "Synthetic example only.",
  "items": [
    {
      "id": "I001",
      "path": "synthetic.md",
      "kind": "text",
      "metadata": {
        "title": "Synthetic item"
      },
      "version": "v1",
      "hash": "sha256:synthetic",
      "status": "indexed",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "operations": [
    {
      "action": "index",
      "target": "synthetic.md",
      "status": "ok",
      "detail": "synthetic only"
    }
  ],
  "findings": [
    {
      "id": "F001",
      "statement": "Synthetic finding",
      "evidence": [
        {
          "path": "synthetic.md",
          "locator": "P1",
          "quote": "synthetic source sentence"
        }
      ]
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
  "answers": [
    {
      "id": "A1",
      "question": "Synthetic question",
      "status": "answered",
      "sentences": [
        {
          "text": "Synthetic answer sentence.",
          "citations": [
            {
              "path": "synthetic.md",
              "locator": "P1",
              "quote": "synthetic source sentence"
            }
          ]
        }
      ],
      "missing_evidence": [],
      "conflict": []
    }
  ],
  "retrieval": [
    {
      "answer_id": "A1",
      "record_id": "R001",
      "locator": "P1",
      "snippet": "synthetic source sentence"
    }
  ]
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_tracks`；模型写内容，脚本做
  证据/字段/产物审计。
- 不联网：检索/追踪/数据库类任务只使用 `/input` 提供的材料或导出；
  不伪造在线查询结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断。
- 示例是 synthetic 接口样例，不是正式案例答案。
