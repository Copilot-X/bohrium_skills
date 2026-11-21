---
name: search-latest-tracking-skill
description: 最新文献发现与追踪：增量水位、时间窗、去重、版本更新与提醒抑制。交付增量清单、窗口/水位、提醒与抑制，保留来源/定位与不确定性，并生成含官方 reading/writing 字段的 result.json。search skill with a source-bound work file, evidence checks and an honest result envelope. Use for 最新文献发现与追踪、latest-tracking、search、证据绑定、结果复核.
---

# 最新文献发现与追踪（search track · latest-tracking）

## 用途与触发

task.md 要求“增量水位、时间窗、去重、版本更新与提醒抑制”，或 `/input/case.json` 给出 `search` / `latest-tracking`
相关字段时使用。本题属于 **最新文献追踪** 类任务，核心交付是
**增量清单、窗口/水位、提醒与抑制**，不是一段泛泛建议。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代
同行评审/机构审查/临床决策。材料不足时只交付可确认部分，并把缺口写成
`uncertainty`，不要补造。

核心原则：**有输入、有依据、有产物、有边界**。脚本负责盘点材料、建立
工作区、按固定窗口与去重键计算增量、准备提醒、记录抑制与回放状态，
并组装 `result.json`；模型负责领域判断、相关性复核与最终改写。脚本不会
凭空生成文献、DOI、日期或“已发送提醒”；所有新增都必须来自输入记录。

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
| `scripts/run_skill.py` | **推荐入口**：prepare → 增量/去重/提醒 → finalize → 契约补全；输入不完整也写合法 `result.json` |
| `scripts/domain_work.py` | 本模式确定性初稿：固定窗口、水位、快照、去重合并、增量、提醒/抑制、回放键 |
| `scripts/run_case.py` | 分步入口：prepare / finalize / run / inspect |
| `scripts/selftest.py` | 正向 + 幂等重跑 + 空输入边界自测 |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/search-plan.json` 的字段契约 |
| `references/search-rules.md` | 最新文献追踪 的可执行规则与失败模式 |
| `examples/synthetic-input.json` | synthetic 输入示意（非正式案例） |

## 工作流

### 第 0 步：一条命令拿到可回放的增量（推荐，先保证不空转）

```bash
python3 "$SKILL_DIR/scripts/run_skill.py" \
  --input /input --work /app/work --output /app/output
```

它会依次执行 prepare、`domain_work.py` 增量计算、finalize，并检查：

1. `work/search-plan.json`、4 个声明产物和 `output/result.json` 都存在；
2. 窗口/时区/水位/回放键/快照/提醒/抑制齐全；
3. 输入为空/不可读时仍写合法 `result.json`（`status=input_error`）、退出码 0；
4. 任何声明输出缺失时自动补写并重跑 finalize；
5. 同一 `replay_key` 重跑时，已入队 alert_id 标为 `already_prepared`，
   写入 `suppressed.reason=already_prepared_idempotent`，不二次入队。

随后读 `output/tracking-digest.md`、`output/search-results.json` 与
`work/domain-status.json`，人工复核相关性/版本合并，再运行 finalize。

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，提取文本/JSON/CSV，写 `work/inventory.json` 与
   `work/materials.json`；
2. 生成 `work/search-plan.json` 骨架（已存在则不覆盖）；
3. 写 `output/README-partial.md` 与 provisional `result.json`。

先读 `work/materials.json` 与 `task.md`，确认材料边界和缺失项。

### 第 2 步：写 `work/search-plan.json`（约 60% 预算）

按 `templates/work.schema.json` 写结构化工作文件。必填字段为
`subscription`, `window`, `watermark`, `snapshot_before`, `snapshot_after`,
`increments`, `alert_log`, `suppressed`, `replay_key`, `evidence`。
窗口必须写 `start/end/timezone`（固定 UTC 日期，含起点不含终点）；每条增量
必须有 `dedupe_key`、`category`、`evidence`；提醒必须区分
`queued_for_digest`/`already_prepared`/`suppressed_digest_limit`，并说明
Skill 不发送消息。

```json
{
  "case_id": "SYNTH-SEARCH",
  "mode": "latest-tracking",
  "summary": "Synthetic example only.",
  "question": "Synthetic research question",
  "queries": [
    {
      "id": "Q1",
      "round": 1,
      "query": "synthetic query",
      "source": "provided corpus",
      "purpose": "recall",
      "stop_condition": "no new eligible records in 2 rounds"
    }
  ],
  "candidates": [
    {
      "id": "C001",
      "title": "Synthetic paper",
      "authors": "A. Example",
      "year": "2026",
      "venue": "Journal of Synthetic Examples",
      "doi": "10.0000/synth.001",
      "relevance": "core",
      "rank": 1,
      "score": 1.0,
      "dedupe_key": "doi:10.0000/synth.001",
      "merged_from": [],
      "reason": "Synthetic relevance reason",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "metrics": {
    "k": 10,
    "precision_at_k": 1.0,
    "ndcg_at_k": 1.0,
    "recall_at_k": 1.0,
    "metadata_accuracy": 1.0,
    "definition": "synthetic values; replace with measured values"
  },
  "screening": {
    "include": [
      "C001"
    ],
    "exclude": [],
    "uncertain": []
  },
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
  "subscription": {
    "id": "SUB1",
    "query": "synthetic query",
    "sources": [
      "SyntheticDB"
    ]
  },
  "window": {
    "start": "2026-01-01",
    "end": "2026-02-01",
    "timezone": "UTC"
  },
  "watermark": "2026-01-01T00:00:00Z",
  "snapshot_before": {
    "id": "SNAP1",
    "count": 0
  },
  "snapshot_after": {
    "id": "SNAP2",
    "count": 1
  },
  "increments": [
    {
      "id": "N1",
      "record_id": "R001",
      "category": "new",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "alert_log": [
    {
      "alert_id": "A1",
      "record_id": "R001",
      "status": "delivered"
    }
  ],
  "suppressed": [
    {
      "record_id": "R002",
      "reason": "already delivered"
    }
  ],
  "replay_key": "SUB1:2026-01-01:2026-02-01"
}
```

同时写以下产物：

- `output/search-results.json`
- `output/candidates.csv`
- `output/search-log.json`
- `output/evidence.json`

- `output/search-results.json` 是主交付物，必须真实存在且非空。
- 所有 `quote` 必须能在对应 `path` 的材料中逐字（允许空白折叠/省略号分段）
  找到；找不到的写入 `uncertainties`，不要改写成“大意”。
- 只报相对上次快照的新增；重跑幂等。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/search-plan.json`（或 JSON 主产物），检查必填字段与 `search` 专项结构；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/search-results.json` 与 `output/candidates.csv`, `output/search-log.json`, `output/evidence.json` 的真实文件；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `answered|partial|input_error`。`run_skill.py` 读取
`work/domain-status.json`：无材料时强制 `input_error`；窗口/水位/回放键/
提醒缺失或增量重复时降级 `partial`。退出码：写出可解析 result.json 即 0；
只有 `run_case.py` 缺失才返回 2。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/search-results.json` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings`/`candidates`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote；
- `artifacts[]`：主产物与 `output/candidates.csv`, `output/search-log.json`, `output/evidence.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`；
- `run_skill`：各步骤返回码、fallback、声明产物大小；
- `domain-audit.json`：记录数、增量数、提醒数、抑制数、回放键。

若 `/input/case.json` 的 `output_contract` 含本题特有键（例如
`检索结果`、`证据记录`、`状态说明` 或 `alert_log`），必须：

1. 在 `result.json` 顶层保留原键名；
2. 写同名产物 `output/<field>.json`（中文键直接作为文件名），内容必须是
   真实结构或明确的 partial 指针；
3. finalize 检查 `contract_coverage`；`run_skill.py` 最后再补一次映射并
   重算覆盖表。未覆盖的键会降级 `partial` 并写入 `uncertainties`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/search-results.json` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `subscription`, `window`, `watermark`, `snapshot_before/after`, `increments`, `alert_log`, `suppressed`, `replay_key`, `evidence` 齐全 |
| G3 | 证据 | 每条记录/增量有 path/locator/quote；quote 逐字可查 |
| G4 | 边界 | 空窗口、越窗、重复、撤稿、日期缺失写入 `suppressed`/`uncertainties` |
| G5 | 专项 | 只报相对上次快照的新增；窗口含起点不含终点；版本合并有依据；重跑幂等；提醒不重复入队 |
| G6 | 产物 | 声明的每个 artifact 路径真实存在且非空 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致，contract_coverage 全覆盖 |
| G8 | 不空转 | 材料不可读时仍写合法 result.json（`input_error`）且退出码 0 |
| G9 | 可复跑 | `python3 scripts/selftest.py` 正向/幂等/边界均通过 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 增量水位、时间窗、去重、版本更新与提醒抑制；只报告相对上次快照的新增；窗口/时区固定；重跑幂等；版本记录可回放 | `output/search-results.json` + evidence |
| 使用增益 | 可复跑的查询/步骤/规则/日志，以及同条件对照 | `output/candidates.csv`, `output/search-log.json`, `output/evidence.json` |
| 创新增益 | 本题专项方法（latest-tracking）及其与基线方案的边界 | `references/search-rules.md` + `output/search-results.json` |
| 可靠体验 | 空结果、冲突、缺字段、权限/格式错误的识别与降级 | `uncertainties` + `output/result.json` |
| 生态贡献 | SKILL.md、schema、规则、synthetic 示例可被他人复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；仍写增量文件/候选表/日志/证据与合法 result.json，但全部为空并说明原因，不生成伪文献/DOI |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要/仅元数据”；`status=partial` |
| 空窗口 | 明确写“本窗口无新增”，不把空白写成检索失败；`alert_log=[]` 合法 |
| 日期只有年/月 | 记录 `date_precision`，在 `uncertainties` 提示窗口边界可能偏差 |
| 记录重复/多源 | 按 DOI→arXiv→标题+年份的优先级合并，保留 `versions[]` 与 `merged_from` |
| 撤稿/更正 | 类别标 `retraction`/`correction`，提醒优先级单独说明 |
| 重跑 | 同一 `replay_key` 已入队 alert 标 `already_prepared`，不二次入队 |
| 提醒发送 | Skill 只入队，不声称已发送；`delivery=not_sent_by_skill` |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 候选/文献重复 | 用 DOI/标题+年份合并；保留合并依据 |
| 空结果/无命中 | 明确写“未检索到/未命中”，不把空白写成排除 |
| 数字/统计不一致 | 以数据文件为准；报告与结果文件对齐 |
| 代码执行失败 | 保留 `execution.log` 与失败原因；不伪造输出 |
| 权限/接口/格式错误 | 写 `uncertainty` 与已完成范围；建议人工接管 |

## 最小示例（synthetic，不是正式答案）

```json
{
  "case_id": "SYNTH-SEARCH",
  "mode": "latest-tracking",
  "summary": "Synthetic example only.",
  "question": "Synthetic research question",
  "queries": [
    {
      "id": "Q1",
      "round": 1,
      "query": "synthetic query",
      "source": "provided corpus",
      "purpose": "recall",
      "stop_condition": "no new eligible records in 2 rounds"
    }
  ],
  "candidates": [
    {
      "id": "C001",
      "title": "Synthetic paper",
      "authors": "A. Example",
      "year": "2026",
      "venue": "Journal of Synthetic Examples",
      "doi": "10.0000/synth.001",
      "relevance": "core",
      "rank": 1,
      "score": 1.0,
      "dedupe_key": "doi:10.0000/synth.001",
      "merged_from": [],
      "reason": "Synthetic relevance reason",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "metrics": {
    "k": 10,
    "precision_at_k": 1.0,
    "ndcg_at_k": 1.0,
    "recall_at_k": 1.0,
    "metadata_accuracy": 1.0,
    "definition": "synthetic values; replace with measured values"
  },
  "screening": {
    "include": [
      "C001"
    ],
    "exclude": [],
    "uncertain": []
  },
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
  "subscription": {
    "id": "SUB1",
    "query": "synthetic query",
    "sources": [
      "SyntheticDB"
    ]
  },
  "window": {
    "start": "2026-01-01",
    "end": "2026-02-01",
    "timezone": "UTC"
  },
  "watermark": "2026-01-01T00:00:00Z",
  "snapshot_before": {
    "id": "SNAP1",
    "count": 0
  },
  "snapshot_after": {
    "id": "SNAP2",
    "count": 1
  },
  "increments": [
    {
      "id": "N1",
      "record_id": "R001",
      "category": "new",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "alert_log": [
    {
      "alert_id": "A1",
      "record_id": "R001",
      "status": "delivered"
    }
  ],
  "suppressed": [
    {
      "record_id": "R002",
      "reason": "already delivered"
    }
  ],
  "replay_key": "SUB1:2026-01-01:2026-02-01"
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。

## 版本与限制

- 版本 `1.1.0`：新增 `run_skill.py` 一键兜底入口与 `domain_work.py`
  确定性增量计算；固定窗口/水位/快照/去重键/回放键；同一 replay_key 重跑
  不二次入队；无材料/缺产物时仍写合法 result.json 与全部声明输出。
- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_tracks`；模型写内容，脚本做
  证据/字段/产物审计。
- 不联网：检索/追踪/数据库类任务只使用 `/input` 提供的材料或导出；
  不伪造在线查询结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断。
- 示例是 synthetic 接口样例，不是正式案例答案。
