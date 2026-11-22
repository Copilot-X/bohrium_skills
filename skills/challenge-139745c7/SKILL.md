---
name: library-file-search-skill
description: 科研文件智能检索：文件/全文检索、字段过滤、排序与结果定位。交付命中片段、过滤条件与排序，保留来源/定位与不确定性，并生成含官方 reading/writing 字段的 result.json。library skill with a source-bound work file, evidence checks and an honest result envelope. Use for 科研文件智能检索、file-search、library、证据绑定、结果复核.
---

# 科研文件智能检索（library track · file-search）

## 用途与触发

task.md 要求“文件/全文检索、字段过滤、排序与结果定位”，或 `/input/case.json` 给出 `library` / `file-search`
相关字段时使用。本题属于 **文件检索** 类任务，核心交付是
**命中片段、过滤条件与排序**，不是一段泛泛建议。

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
  行政版本说明（supersedes / 修订 / 作废）、元数据与权限记录，以及检索问题或
  workorder。先按实际文件处理，不按文件名猜内容，也不把旧版/副本当独立证据。

```bash
python3 "$SKILL_DIR/scripts/build_library_index.py" --input /input \
  --out /app/work/library-index-build.json --json
```

索引给出 `path`、`sha256`、`version_token`、`version_family`、`sections`、
`blocks[].page/line/first_sentence`、`duplicate_groups`、`version_groups`、
`notes[]`（alias / 行政版本说明）与 `unreadable`。**命中定位优先用
`page`（PDF）或 `section + line`（文本）**；文件级路径不能替代章节定位。

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
| `references/library-rules.md` | 文件检索 的可执行规则与失败模式 |
| `scripts/build_library_index.py` | **语义检索前必跑**：建立路径/版本/章节/行号/别名/重复/不可读索引 |
| `scripts/search_library.py` | **本题专项初稿**：按查询/过滤器检索索引块，输出排序命中、逐字片段与 no_result |
| `scripts/check_library_plan.py` | **finalize 前必跑**：硬过滤、no_result、版本、引用逐字与契约产物检查 |
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

### 第 1.5 步：生成检索初稿（推荐）

```bash
python3 "$SKILL_DIR/scripts/build_library_index.py" --input /input   --out /app/work/library-index-build.json --json
python3 "$SKILL_DIR/scripts/search_library.py"   --input /input --work /app/work --output /app/output --top-k 5
```

第二个脚本按 `case.json` 的 queries/filters 检索索引块，输出 rank/path/version/
locator/snippet/score 与逐字证据；无结果明确写 `no_result`。模型必须复核过滤
条件、版本冲突与排序理由，不能只交词面命中。

### 第 2 步：写 `work/library-plan.json`（约 60% 预算）

按 `templates/work.schema.json` 写结构化工作文件。至少包含
`question`, `queries`, `items`, `filters`, `filter_execution`, `no_result`,
`version_conflict`, `unreadable`, `evidence`，并为每条关键判断/候选/步骤绑定
`evidence`。先用 `library-index-build.json` 选候选，再回读原文摘 quote；不要只凭
文件名或索引摘要下结论。以下为字段形状（synthetic）：

```json
{
  "case_id": "SYNTH-LIBRARY",
  "mode": "file-search",
  "summary": "Synthetic example only.",
  "items": [
    {
      "id": "R001",
      "rank": 1,
      "path": "synthetic.md",
      "version": "v1",
      "locator": "P1",
      "snippet": "synthetic source sentence",
      "hash": "sha256:synthetic",
      "score_reason": "synthetic only",
      "excluded_by": [],
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
  "filters": {
    "must": [
      "synthetic filter"
    ],
    "must_not": [],
    "applied_to": "all records"
  },
  "filter_execution": [
    {
      "filter": "synthetic filter",
      "kept": 1,
      "excluded": 0
    }
  ],
  "no_result": false,
  "unreadable": [],
  "version_conflict": []
}
```

### 第 2b 步：硬过滤、版本与无结果（本题得分核心）

先读请求本身（`/input/case.json`、`task.md`、workorder 中的 query/questions），
把条件拆成 `filters.must` / `filters.must_not`，逐条落到字段或原文证据：

1. **硬过滤不放松**：`must` 条件必须是返回项可核验的属性（项目号、年份、文件类型、
   批次、版本、权限）。每个 accepted item 写
   `filter_matches: {"<filter 原文>": {"path": ..., "locator": ..., "quote": ...}}`；
   命中 `must_not` 的项必须 `rank:null` 且 `excluded_by` 写具体条件与原因。
2. **计数闭合**：`filter_execution[]` 覆盖每个 must/must_not，`kept + excluded`
   等于 `items` 总数，`kept` 等于 accepted 数。条件无法满足时写
   `no_result: {"is_no_result": true, "reason": "...", "searched_scope": "..."}`
   （或 `true` + `no_result_reason`），**不得放宽条件凑结果**。
3. **版本与副本**：同一 `version_family` 只把当前版作为 ranked hit；旧版、副本、
   改名文件写入 `version_conflict[]` 或 `excluded_by`，说明“非独立证据”。别名通过
   `notes[]` 里的 alias 或元数据解析，不靠猜。每个 item 的 `hash`/`sha256`
   **从 `library-index-build.json` 原样复制**；同 hash 的副本共享 hash 并标
   `duplicate_of`，不得伪造 hash。
4. **章节定位**：`locator` 用 `page`（PDF，必要时换算印刷页码与物理页偏移）
   或 `section_id/label + line`（文本/Word）；`snippet` 给命中段落首句或原句。
   `score_reason` 写为什么这段回答该问题（同义/缩写/跨语言命中要说明）。
5. **不可读与权限**：无文本层扫描件、损坏、加密、无权限分别写
   `unreadable[]`/`uncertainties[]`，不把“读不到”写成“未命中”。

```json
{
  "question": "batch 3 的缓冲液配方写在哪一版？",
  "queries": [{"query": "running buffer HEPES Tris batch 3", "purpose": "buffer formulation", "round": 1}],
  "filters": {"must": ["project=PA", "year=2024", "type=md"], "must_not": ["superseded=true"], "applied_to": "all materials"},
  "filter_execution": [
    {"filter": "project=PA", "kept": 1, "excluded": 3},
    {"filter": "year=2024", "kept": 1, "excluded": 3},
    {"filter": "type=md", "kept": 1, "excluded": 3},
    {"filter": "superseded=true", "kept": 1, "excluded": 1}
  ],
  "items": [
    {"id": "R1", "rank": 1, "path": "library/projectA_batch3_v2.md", "version": "v2",
     "hash": "sha256:synthetic-v2",
     "locator": "section 2, L5", "snippet": "The running buffer was changed to 25 mM HEPES, pH 7.8.",
     "score_reason": "v2 is the current version and states the buffer directly",
     "excluded_by": [],
     "filter_matches": {"project=PA": {"path": "library/projectA_batch3_v2.md", "locator": "L1", "quote": "Project A / batch 3"}},
     "evidence": {"path": "library/projectA_batch3_v2.md", "locator": "section 2, L5", "quote": "The running buffer was changed to 25 mM HEPES, pH 7.8."}},
    {"id": "R2", "rank": null, "path": "library/projectA_batch3_v1.md", "version": "v1",
     "locator": "section 2, L4", "snippet": "20 mM Tris-HCl, pH 7.4",
     "score_reason": "historical version", "excluded_by": ["superseded by v2"],
     "evidence": {"path": "library/projectA_batch3_v1.md", "locator": "section 2, L4", "quote": "The running buffer was 20 mM Tris-HCl, pH 7.4."}}
  ],
  "version_conflict": [{"family": "projectA_batch3", "current": "v2", "superseded": ["v1"], "basis": "Version note: v2 supersedes v1."}],
  "no_result": false
}
```

同时写以下产物：

- `output/library-results.json`（主交付物，必须真实存在且非空）
- `output/library-index.json`（文件/章节/版本索引摘要）
- `output/operations.log.json`（index/search/filter/exclude/recovery 操作及结果）
- `output/results/operation_evidence.json`（索引与检索证据）
- `output/results/failure_recovery.json`（跳过/冲突/无权限/不可读/恢复）
- 若 `output_contract` 还有其他键，一律写 `output/results/<key>.json`（或同名 `.csv/.md`），
  **不要只写在 output 根目录**；finalize 会登记 results/ 下的契约产物。

### 第 2c 步：自查（finalize 前必跑）

```bash
python3 "$SKILL_DIR/scripts/check_library_plan.py" --input /input \
  --work /app/work --output /app/output --json
```

`ok:false` 时逐条修：accepted 项不满足 must、must_not 项仍 ranked、
`kept+excluded` 不闭合、`no_result` 未显式、同一版本两个 ranked、quote 非逐字、
契约产物缺失。修不了的在 `uncertainties` 写清并降级 `partial`，不要跳过。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/library-plan.json`（或 JSON 主产物），检查必填字段 `items`, `filters`, `evidence`；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/library-results.json` 与 `output/library-index.json`, `output/operations.log.json`，
   以及 `output/results/` 下所有契约产物；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `answered|partial|input_error`。有缺字段、证据
不匹配、主产物缺失或执行失败时用 `partial`。finalize 前若没跑
`check_library_plan.py`，先跑；不要把 `ok:false` 的计划交给 finalize。

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
   会被 finalize 自动登记。根目录只放主产物与 `result.json`，不要依赖根目录同名文件；
3. finalize 会检查 `contract_coverage`，未覆盖的键会降级 `partial` 并写入
   `uncertainties`；不要只写 `status/result/claims` 而忽略原契约字段。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/library-results.json` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `items`, `filters`, `evidence` 齐全 |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查 |
| G4 | 边界 | 未报告、冲突、失败、未检查项写入 `uncertainties` |
| G5 | 专项 | 每条结果有路径+章节/页码+命中片段；排序可解释；no_result 显式 |
| G5b | 过滤 | accepted 项满足全部 must；must_not 项 rank=null + excluded_by；计数闭合 |
| G5c | 版本 | 同族版本只有一个 ranked；副本/旧版有非独立证据说明 |
| G6 | 产物 | 声明的每个 artifact 路径真实存在；契约键在 output/results/ 有产物 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致 |
| G8 | 自查 | `check_library_plan.py` 返回 `ok:true` |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 文件/全文检索、字段过滤、排序与结果定位；每条结果有路径和命中片段；排序可解释；无结果说明 | `output/library-results.json` + evidence |
| 使用增益 | 可复跑的查询/步骤/规则/日志，以及同条件对照 | `output/library-index.json`, `output/operations.log.json` |
| 创新增益 | 本题专项方法（file-search）及其与基线方案的边界 | `references/library-rules.md` + `output/library-results.json` |
| 可靠体验 | 空结果、冲突、缺字段、权限/格式错误的识别与降级 | `uncertainties` + `output/result.json` |
| 生态贡献 | SKILL.md、schema、规则、synthetic 示例可被他人复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；不生成伪候选/结论/审查意见 |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要”；`status=partial` |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 候选/文献重复 | 用 DOI/标题+年份合并；保留合并依据 |
| 空结果/无命中 | `no_result{is_no_result:true, reason, searched_scope}`，不把空白写成排除 |
| 数字/统计不一致 | 以数据文件为准；报告与结果文件对齐 |
| 代码执行失败 | 保留 `execution.log` 与失败原因；不伪造输出 |
| 权限/接口/格式错误 | 写 `unreadable[]`/`uncertainty` 与已完成范围；建议人工接管 |
| 重名/改名文件 | 用 sha256 或内容相似度判重；alias 写来源，不把副本当独立证据 |
| 旧版本混入 | 当前版 ranked；旧版进 `version_conflict`/`excluded_by`，说明依据 |
| 扫描件无文本层 | 尝试 OCR；失败写 `unreadable[]`，不把“读不到”写成“未命中” |
| 过滤条件互相矛盾 | 如实 `no_result` 并说明矛盾，不放宽任一硬条件 |

## 最小示例（synthetic，不是正式答案）

```json
{
  "case_id": "SYNTH-LIBRARY",
  "mode": "file-search",
  "summary": "Synthetic example only.",
  "items": [
    {
      "id": "R001",
      "rank": 1,
      "path": "synthetic.md",
      "version": "v1",
      "locator": "P1",
      "snippet": "synthetic source sentence",
      "hash": "sha256:synthetic",
      "score_reason": "synthetic only",
      "excluded_by": [],
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
  "filters": {
    "must": [
      "synthetic filter"
    ],
    "must_not": [],
    "applied_to": "all records"
  },
  "filter_execution": [
    {
      "filter": "synthetic filter",
      "kept": 1,
      "excluded": 0
    }
  ],
  "no_result": false,
  "unreadable": [],
  "version_conflict": []
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
