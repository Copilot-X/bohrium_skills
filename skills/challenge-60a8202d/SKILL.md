---
name: search-citation-tracking-skill
description: 引文追踪与关联文献发现：前向/后向追踪、引用路径、身份合并与关联发现。交付引用边、逐跳路径、身份合并与关联文献，保留来源/定位与不确定性，并生成含官方 reading/writing 字段的 result.json。search skill with a source-bound work file, evidence checks and an honest result envelope. Use for 引文追踪与关联文献发现、citation-tracking、search、证据绑定、结果复核.
---

# 引文追踪与关联文献发现（search track · citation-tracking）

## 用途与触发

task.md 要求“前向/后向追踪、引用路径、身份合并与关联发现”，或 `/input/case.json` 给出 `search` / `citation-tracking`
相关字段时使用。本题属于 **引文追踪** 类任务，核心交付是
**引用边、逐跳路径、身份合并与关联文献**，不是一段泛泛建议。

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
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `scripts/audit_work.py` | **预检闸门**：校验引用方向/逐跳路径、身份合并、关联依据、未知方向与输入覆盖 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/search-plan.json` 的字段契约 |
| `references/search-rules.md` | 引文追踪 的可执行规则与失败模式 |
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
2. 生成 `work/search-plan.json` 骨架（已存在则不覆盖）；
3. 写 `output/README-partial.md` 与 provisional `result.json`。

先读 `work/materials.json` 与 `task.md`，确认材料边界和缺失项。

### 第 1.5 步：构建引用图初稿（推荐）

```bash
python3 "$SKILL_DIR/scripts/build_reference_graph.py"   --input /input --work /app/work --output /app/output
```

脚本只扫描固定文档和其中的 DOI/引用行，构建节点、边、方向、BFS 路径、
DOI/标题身份组与共享参考文献的相关作品；没有明确方向的边进
`direction_unknown`，不冒充前向/后向。模型必须复核每条边的方向、身份合并与
“相关”依据，再补充检索结论。

### 第 2 步：写 `work/search-plan.json`（约 60% 预算）

按 `templates/work.schema.json` 写结构化工作文件。至少包含
`queries`, `candidates`, `tracking_scope`, `seeds`, `edges`, `paths`,
`identity_groups`, `related_works`, `direction_unknown`, `evidence`，
并为每条关键判断/候选/步骤绑定 `evidence`：

```json
{
  "case_id": "SYNTH-SEARCH",
  "mode": "citation-tracking",
  "summary": "Synthetic example only.",
  "question": "Synthetic research question",
  "queries": [
    {
      "id": "Q1",
      "round": 1,
      "type": "supplied_export",
      "query": "retained citation graph supplied in input/citations.md",
      "source": "citations.md",
      "purpose": "provenance; no live query was run",
      "stop_condition": "all supplied citation records classified"
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
    },
    {
      "id": "C002",
      "title": "Synthetic Citing Work",
      "authors": "L. Okafor",
      "year": "2022",
      "venue": "Synthetic Replication Letters",
      "doi": "10.0000/synth.002",
      "relevance": "follow_up",
      "rank": 1,
      "score": 1.0,
      "dedupe_key": "doi:10.0000/synth.002",
      "merged_from": [],
      "reason": "Cites the seed and improves the method.",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P2",
        "quote": "synthetic citing sentence"
      }
    },
    {
      "id": "C003",
      "title": "Synthetic Parallel Work",
      "authors": "M. Chen",
      "year": "2021",
      "venue": "SYNTHCONF",
      "doi": "10.0000/synth.003",
      "relevance": "parallel",
      "rank": 2,
      "score": 0.8,
      "dedupe_key": "doi:10.0000/synth.003",
      "merged_from": [],
      "reason": "Shares references with the seed but does not cite it.",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P3",
        "quote": "synthetic parallel sentence"
      }
    },
    {
      "id": "C004",
      "title": "Synthetic Direction-Unknown Record",
      "authors": "Q. Zhang",
      "year": "2021",
      "venue": "Synthetic Other Journal",
      "doi": "",
      "relevance": "unresolved",
      "rank": null,
      "score": null,
      "dedupe_key": "title:direction unknown",
      "merged_from": [],
      "reason": "The supplied list does not state citation direction.",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P4",
        "quote": "synthetic unknown-direction sentence"
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
  "seeds": [
    {
      "id": "C001",
      "title": "Synthetic seed",
      "doi": "10.0000/synth.001",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "tracking_scope": {
    "levels": 2,
    "stop_condition": "stop when no new record has a source-backed citation edge",
    "budget": "synthetic: 10 records",
    "sources": [
      "citations.md",
      "seed.md"
    ]
  },
  "edges": [
    {
      "id": "E1",
      "from": "C001",
      "to": "C002",
      "direction": "forward",
      "hop": 1,
      "basis": "C002 cites the seed (C001)",
      "source": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "paths": [
    {
      "id": "PATH1",
      "nodes": [
        "C001",
        "C002"
      ],
      "edges": [
        "E1"
      ],
      "relation": "cites",
      "direct": true
    }
  ],
  "identity_groups": [
    {
      "canonical_id": "C001",
      "members": [
        "C001"
      ],
      "merged": false,
      "basis": "single supplied record; doi:10.0000/synth.001",
      "version_basis": "",
      "keep_separate_reason": ""
    }
  ],
  "related_works": [
    {
      "id": "C003",
      "relation": "bibliographic_coupling",
      "basis": "shares two references with the seed but does not cite it",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "direction_unknown": {
    "checked": true,
    "items": [
      {
        "id": "C004",
        "reason": "the supplied list does not state whether it cites the seed",
        "source": "synthetic.md"
      }
    ],
    "policy": "undetermined records are excluded from directed paths"
  }
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
- 方向不明标 undetermined，不猜。

#### 2.1 引用边与方向（硬要求）

统一约定：`from` 是路径当前节点，`to` 是下一跳；`direction` 描述
`to` 相对 `from` 的关系：

| direction | 含义 | 例 |
|---|---|---|
| `forward` | `to` 引用 `from`（走向施引文献） | 种子 → 后续改进 |
| `backward` | `from` 引用 `to`（走向参考文献） | 种子 → 方法出处 |
| 未知 | 不建有向边，放入 `direction_unknown` | 列表未说明是否引用 |

每条 edge 必须含 `from/to/direction/hop/source/basis`；`source` 的
path+locator+quote 必须指向输入中的原句。输入 note 明确写
“seed cites this work”时方向必须是 backward；写“this paper cites the
seed”时必须是 forward。矛盾时预检失败。

#### 2.2 路径逐跳可回指（硬要求）

- `paths.nodes` 与 `paths.edges` 长度满足 `len(nodes)=len(edges)+1`；
- 第 i 条边必须真实连接 `nodes[i] -> nodes[i+1]`，且按 edge 的
  `from->to` 顺序遍历；反向遍历要拆成另一条边或明确改写；
- `direct` 标志与 hop 数一致（1 跳=直接引用，>1=间接可达）；
- 每条 path 写 `relation`（cites/cited_by/mixed）与 `basis`；
- 没有边证据的“最短路径”不能写。

#### 2.3 身份合并（硬要求）

`identity_groups` 每组含：

- `canonical_id` 与 `members`（含 canonical）；
- `merged`（true/false）与 `basis`（匹配字段：DOI、标题、作者、年份）；
- 合并版本时写 `version_basis`（预印本/会议版/期刊版的 DOI、arXiv、
  年份、标题与作者对应关系）；
- 同标题不同作者/不同研究问题：`merged=false` +
  `keep_separate_reason`，禁止合并。

每个输入记录恰好出现在一个身份组中（单条记录也建组）。

#### 2.4 关联文献与未知方向（硬要求）

- `related_works` 每条含 `id/relation/basis/evidence`；
  `relation ∈ {bibliographic_coupling, co_citation, shared_dataset,
  shared_author, topic, method_lineage}`；不能写 `cites/cited_by`，
  也不能把已进入引用边的记录伪装成“平行工作”。
- 关联依据必须能回指输入（共享参考文献、共享数据集、同作者/机构等）。
- `direction_unknown` 是对象：
  `{"checked": true, "items": [...], "policy": "..."}`；
  每项含 `id/reason/source`。输入明确说方向未知的记录必须在此列出；
  这些记录不得进入有向 `paths`。

#### 2.5 覆盖与停止（硬要求）

- `tracking_scope` 写 `levels/stop_condition/budget/sources`；
  至少覆盖 1 层前向 + 1 层后向（输入同时提供两类记录时）。
- 输入中的每条引用记录必须被以下之一覆盖：candidate、edge 端点、
  identity_group 成员、related_works、direction_unknown；静默遗漏会被
  预检发现。
- 种子无引文记录时：`edges=[]` 允许，但必须写
  `direction_unknown.checked=true` 与 `uncertainties` 说明“无引文记录”，
  不能伪造边。

#### 2.6 运行预检（必做，通过后才可 finalize）

```bash
python3 "$SKILL_DIR/scripts/audit_work.py" \
  --input /input --work /app/work --output /app/output
```

预检会校验方向语义、路径逐跳连接、身份合并/拆分、关联依据、未知方向、
输入记录覆盖与逐字证据。退出码必须为 `0`；有 `problems` 先修工作文件，
不要 finalize。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/search-plan.json`（或 JSON 主产物），检查必填字段 `queries`, `candidates`, `tracking_scope`, `seeds`, `edges`, `paths`, `identity_groups`, `related_works`, `direction_unknown`, `evidence`；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/search-results.json` 与 `output/candidates.csv`, `output/search-log.json`, `output/evidence.json` 的真实文件；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `answered|partial|input_error`。有缺字段、证据
不匹配、主产物缺失或执行失败时用 `partial`。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/search-results.json` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings`/`candidates`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote；
- `artifacts[]`：主产物与 `output/candidates.csv`, `output/search-log.json`, `output/evidence.json`；
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
| G1 | 主产物 | `output/search-results.json` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `queries`, `candidates`, `tracking_scope`, `seeds`, `edges`, `paths`, `identity_groups`, `related_works`, `direction_unknown`, `evidence` 齐全且子字段非空壳 |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查；种子/记录元数据与输入一致 |
| G4 | 边界 | 未报告、冲突、失败、未检查项写入 `uncertainties`；未知方向单列 |
| G5 | 专项 | 每条引用边有方向与来源；路径逐跳可回指；版本合并/同名拆分有依据；关联文献非引用边 |
| G8 | 预检 | `scripts/audit_work.py` 退出码 0，`output/quality-audit.json` 存在 |
| G6 | 产物 | 声明的每个 artifact 路径真实存在 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 前向/后向追踪、引用路径、身份合并与关联发现；每条引用边有方向与来源；路径逐跳可回指；预印本/正式版合并有依据 | `output/search-results.json` + evidence |
| 使用增益 | 可复跑的查询/步骤/规则/日志，以及同条件对照 | `output/candidates.csv`, `output/search-log.json`, `output/evidence.json` |
| 创新增益 | 本题专项方法（citation-tracking）及其与基线方案的边界 | `references/search-rules.md` + `output/search-results.json` |
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
  "case_id": "SYNTH-SEARCH",
  "mode": "citation-tracking",
  "summary": "Synthetic example only.",
  "question": "Synthetic research question",
  "queries": [
    {
      "id": "Q1",
      "round": 1,
      "type": "supplied_export",
      "query": "retained citation graph supplied in input/citations.md",
      "source": "citations.md",
      "purpose": "provenance; no live query was run",
      "stop_condition": "all supplied citation records classified"
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
    },
    {
      "id": "C002",
      "title": "Synthetic Citing Work",
      "authors": "L. Okafor",
      "year": "2022",
      "venue": "Synthetic Replication Letters",
      "doi": "10.0000/synth.002",
      "relevance": "follow_up",
      "rank": 1,
      "score": 1.0,
      "dedupe_key": "doi:10.0000/synth.002",
      "merged_from": [],
      "reason": "Cites the seed and improves the method.",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P2",
        "quote": "synthetic citing sentence"
      }
    },
    {
      "id": "C003",
      "title": "Synthetic Parallel Work",
      "authors": "M. Chen",
      "year": "2021",
      "venue": "SYNTHCONF",
      "doi": "10.0000/synth.003",
      "relevance": "parallel",
      "rank": 2,
      "score": 0.8,
      "dedupe_key": "doi:10.0000/synth.003",
      "merged_from": [],
      "reason": "Shares references with the seed but does not cite it.",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P3",
        "quote": "synthetic parallel sentence"
      }
    },
    {
      "id": "C004",
      "title": "Synthetic Direction-Unknown Record",
      "authors": "Q. Zhang",
      "year": "2021",
      "venue": "Synthetic Other Journal",
      "doi": "",
      "relevance": "unresolved",
      "rank": null,
      "score": null,
      "dedupe_key": "title:direction unknown",
      "merged_from": [],
      "reason": "The supplied list does not state citation direction.",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P4",
        "quote": "synthetic unknown-direction sentence"
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
  "seeds": [
    {
      "id": "C001",
      "title": "Synthetic seed",
      "doi": "10.0000/synth.001",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "tracking_scope": {
    "levels": 2,
    "stop_condition": "stop when no new record has a source-backed citation edge",
    "budget": "synthetic: 10 records",
    "sources": [
      "citations.md",
      "seed.md"
    ]
  },
  "edges": [
    {
      "id": "E1",
      "from": "C001",
      "to": "C002",
      "direction": "forward",
      "hop": 1,
      "basis": "C002 cites the seed (C001)",
      "source": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "paths": [
    {
      "id": "PATH1",
      "nodes": [
        "C001",
        "C002"
      ],
      "edges": [
        "E1"
      ],
      "relation": "cites",
      "direct": true
    }
  ],
  "identity_groups": [
    {
      "canonical_id": "C001",
      "members": [
        "C001"
      ],
      "merged": false,
      "basis": "single supplied record; doi:10.0000/synth.001",
      "version_basis": "",
      "keep_separate_reason": ""
    }
  ],
  "related_works": [
    {
      "id": "C003",
      "relation": "bibliographic_coupling",
      "basis": "shares two references with the seed but does not cite it",
      "evidence": {
        "path": "synthetic.md",
        "locator": "P1",
        "quote": "synthetic source sentence"
      }
    }
  ],
  "direction_unknown": {
    "checked": true,
    "items": [
      {
        "id": "C004",
        "reason": "the supplied list does not state whether it cites the seed",
        "source": "synthetic.md"
      }
    ],
    "policy": "undetermined records are excluded from directed paths"
  }
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。

## 版本与限制

- 版本 `1.1.0`：单入口 + 共享引擎 + `fam_tracks`；新增
  `scripts/audit_work.py` 校验方向语义、路径逐跳、身份合并、关联依据与
  未知方向；模型写内容，脚本只做审计。
- 不联网：检索/追踪/数据库类任务只使用 `/input` 提供的材料或导出；
  不伪造在线查询结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断。
- 示例是 synthetic 接口样例，不是正式案例答案。
