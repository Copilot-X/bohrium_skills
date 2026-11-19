---
name: evidence-bound-literature-graph
description: 文献知识图谱构建：从两篇独立论文的段落索引出发，抽取真实出现的实体与有证据支持的关系，区分直接证据与推测，处理跨篇别名与同名冲突，交付带 paragraph 级证据的 GraphML/CSV/查询结果。Evidence-bound literature knowledge graph with entity alignment, merge decisions, executable queries and traceable paragraph anchors. Use for 文献知识图谱、实体关系抽取、证据绑定、别名归一、跨篇对齐、GraphML、图谱查询.
---

# 文献知识图谱构建（Evidence-Bound Literature Graph）

## 用途与触发

task.md 要求“从论文抽取实体和关系”“绑定证据”“图谱导出”“查询图谱”
“跨篇实体对齐/合并”“构建文献知识图谱”，或 `/input` 提供
`corpus-manifest.json`、`material.json`、`primary-material.json`、
`secondary-material.json` 时使用。

成功标准不是画出一张好看的图，而是：**抽得准、连得对、对得齐、查得到**。
脚本负责解析语料、建立段落索引、校验类型/证据/合并/查询并导出
GraphML、CSV、Markdown；模型负责判断哪些是真实实体、哪条关系有原文支持、
哪些同名概念必须保持分离。

## 输入

- `/input/case.json`：`case_id`、可选 `input_revision`、`output_contract`；
  字段原样保留到 `result.json`。
- `/input/task.md`：主题、论文范围、要求的实体/关系类型、查询数量。
- `/input/corpus-manifest.json`：`papers[]` 的 `source_id`、`doi`、`title`、
  `authors`、`year`、`license`、`path`。**每篇论文的 DOI/title/authors 只能
  归属该论文，不得把第一篇的元数据套到第二篇。**
- `/input/material.json`：两篇论文正文段落的汇总索引。
- `/input/primary-material.json`：第一篇（既有论文）。
- `/input/secondary-material.json`：第二篇（新增论文）。
- 段落位置是组织方 JATS 段落 ID（如 `jats-p-0002`、`jats-p-0101`）；
  本任务**没有 PDF 页码**，不得把自建页码写成原 PDF 页。
- 正文行内公式/上下标可能被线性化；本包不含图像像素、补充附件或底层实验
  数据，不从这些缺失内容推断数值。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 文件盘点、段落切分、证据引文校验、result.json 信封 |
| `scripts/parsers.py` | JSON/PDF/DOCX/TeX 等材料提取 |
| `scripts/fam_kg.py` | 语料加载、图谱校验、GraphML/CSV/查询导出 |
| `task.json` | family、module、profile 与输出契约 |
| `templates/graph.schema.json` | `work/graph.json` 的 nodes/edges/merges 字段 |
| `templates/query.schema.json` | `queries[]` 的字段与最小查询数 |
| `references/kg-evidence-rules.md` | 实体、证据、关系、合并、查询的可执行规则 |
| `examples/synthetic-corpus.json` | synthetic 双论文段落（非正式案例） |
| `examples/graph.example.json` | 对应 synthetic 图谱片段（非正式答案） |

## 工作流（先索引，再建图，最后查询与审计）

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input` 并提取每篇论文，写 `work/inventory.json`、`work/context.json`；
2. 按 `primary-material.json` → `secondary-material.json` → `material.json`
   → `corpus-manifest.json` 合并语料，按 `source_id` 去重；
3. 写 `work/corpus.json`：
   - `papers[]`：`source_id`、`doi`、`title`、`authors`、`license`、
     `version`、`paragraphs[]`；
   - 每个段落含 `source_id`、`paragraph_id`、`text`、`locator`；
   - `paragraph_index`：键为 `"<source_id>:<paragraph_id>"`，如
     `"P1:jats-p-0002"`；
4. 写 `work/graph-template.json` 与 `work/query-template.json`；
5. 写 `status=partial` 的 provisional `result.json`。

先核对 `work/corpus.json`：`papers` 应包含两篇，`doi/title/authors` 各自独立，
`paragraph_count` 与两篇段落数之和一致。若只有一篇或没有正文，先查
`work/inventory.json` 的 `warnings`，并把缺口写进 `graph.json` 的
`queries[].uncertain` 或 `merges[].conflict`；不要用领域常识补全语料。

### 第 2 步：模型写 `/app/work/graph.json`（约 60% 预算）

以 `corpus.json` 为依据写一个 JSON 对象，字段固定为
`nodes`、`edges`、`merges`、`queries`（可保留 `case_id` 便于核对）：

```json
{
  "case_id": "与输入一致",
  "nodes": [
    {"id": "N1", "label": "原文中的实体表述", "type": "material",
     "aliases": ["原文中的别名"],
     "source_ids": ["P1"],
     "evidence": [{"source_id": "P1", "paragraph_id": "jats-p-0002",
                   "quote": "逐字来自该段的一句短引"}]}
  ],
  "edges": [
    {"id": "E1", "source": "N1", "target": "N2", "type": "outperforms",
     "direction": "forward", "conditions": ["350 degrees C", "1 bar"],
     "evidence": [{"source_id": "P1", "paragraph_id": "jats-p-0002",
                   "quote": "支持该关系的原文短引"}],
     "strength": "direct"}
  ],
  "merges": [
    {"candidate_ids": ["N1", "N2"], "decision": "keep_separate",
     "basis": "为何合并或为何不合并", "conflict": "不同样品/条件/数值"}
  ],
  "queries": [
    {"id": "Q1", "question": "可由图谱回答的真实问题",
     "start_node_ids": ["N1"], "edge_types": ["outperforms"],
     "result_node_ids": ["N1", "N2"], "answer": "基于子图的回答",
     "evidence": [{"source_id": "P1", "paragraph_id": "jats-p-0002",
                   "quote": "支撑回答的原文"}],
     "uncertain": ["无法确定的部分"]}
  ]
}
```

硬规则（详见 `references/kg-evidence-rules.md`）：

1. **实体真实**：`label` 必须是该论文段落中真实出现的科研对象
   （研究者、材料、方法、任务、指标、结论、条件等），不是常识补全的概念。
   `type` 只能取
   `researcher|material|method|task|metric|finding|concept|dataset|condition|organism|model|other`。
2. **证据可回查**：每条 node/edge evidence 必须有 `source_id` +
   `paragraph_id`，且 `quote` 是该段原文的逐字子串；跨不连续片段用 `…` 连接。
   **不要写 PDF 页码**，也不要把自建页码放进 `locator`。
3. **关系有方向、有条件**：`type` 只能取
   `uses|compares|outperforms|underperforms|supports|contradicts|measured_by|part_of|is_a|predicts|causes|associated_with|reports|evaluated_on|trained_on|derived_from|related_to|enhances|reduces|modulates|interacts_with|co_occurs_with|has_part|has_property|measured_as|expresses|encodes|treats|prevents|induces|represses|other`；
   `source`、`target` 必须已存在；`conditions` 记录适用样品/温度/压力等；
   `strength=direct` 仅用于原文直接陈述，`indirect` 用于 suggest/may/间接证据。
4. **两篇分离**：P1 的 DOI/title/authors 不得写到 P2；跨篇同名通用术语可以
   合并，但“相同方法/材料术语”不等于“同一物理样品、同一数据集或同一实验
   条件”。不同样品、不同条件、不同数值结果**不得合并**，也不得合成一个结论。
5. **合并可解释**：每个候选对给 `merge` 或 `keep_separate`，并写 `basis`；
   有样品/条件/结果冲突时写 `conflict`。允许保留多个别名节点，不做强制归一。
6. **查询真实执行**：至少 3 条 query，每条给 `question`、`start_node_ids`
   或 `result_node_ids`（至少一个非空）、`edge_types`（可为空）、`answer`、
   `evidence`、`uncertain`。答案只陈述图谱中存在的节点/边能支持的内容；
   跨篇数值不可直接比较时，在 `answer`/`uncertain` 明确写出。
7. **不编造**：没有原文支持的节点、边、合并、查询结果都不写；未报告项写入
   `uncertain`，源内冲突保留双方证据。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/graph.json` 与 `work/corpus.json`，校验节点类型、边类型、
   `source_ids`、evidence 的段落存在性与引文逐字性、merge 决策与 `basis`、
   query 数量与结果子图；
2. 按 `queries[]` 的 `start_node_ids` + `edge_types` 计算子图
   （给了 `result_node_ids` 时以显式结果为准）；
3. 写 `output/graph.json`（含 `papers` 与图谱本体）、
   `output/graph.graphml`、`output/nodes.csv`、`output/edges.csv`、
   `output/queries.json`（完整查询结果与子图）、
   `output/merge-report.json`、`output/graph.md`；
4. 写 `output/result.json`（reading 字段 + writing 字段）。

`status` 取 `answered|partial|insufficient_evidence|input_error`：
全部校验通过 → `answered`；有证据缺失、类型未知、merge 缺 `basis`、
查询不足或无结果等 → `partial`，逐条写入 `uncertainties`；
语料不可读且未建图 → `input_error`。

## 输出契约

最终产物（全部位于 `/app/output`）：

| 文件 | 内容 |
|---|---|
| `graph.json` | `case_id`、`papers[]`、`nodes[]`、`edges[]`、`merges[]`、`queries[]`（问题文本）、`input_revision` |
| `graph.graphml` | 带 label/type/source_ids/aliases 与边 type/conditions 的 GraphML |
| `nodes.csv` | `id,label,type,aliases,source_ids,evidence_count` |
| `edges.csv` | `id,source,target,type,conditions,strength` |
| `queries.json` | 每条查询的 `result_nodes`、`result_edges`、`answer`、`evidence`、`uncertain` |
| `merge-report.json` | `merges[]`、`merge_count`、`keep_separate_count` |
| `graph.md` | 节点/边/论文计数与查询回答的可读摘要 |
| `result.json` | 状态、结果摘要、claims、uncertainties 与 artifact 路径 |

`result.json` 关键字段（由入口生成，不要手工伪造）：

```json
{
  "case_id": "与输入一致",
  "status": "answered",
  "result": {"summary": "...", "node_count": 6, "edge_count": 4,
             "query_count": 3, "checks": {"problems": []}},
  "claims": [{"claim_id": "N1", "text": "节点标签", "evidence": []}],
  "uncertainties": [{"item": "...", "reason": "...", "impact": "...", "action": "..."}],
  "deliverable": {"path": "graph.json", "format": "json", "exists": true},
  "preserved_facts": [{"kind": "number", "source": "18%", "target": "18%",
                       "locator": "P1 jats-p-0002", "value": "18%", "unit": ""}],
  "evidence_map": [{"claim_id": "N1", "claim": "节点标签",
                    "source": {"path": "corpus", "locator": "P1 jats-p-0002"},
                    "quote": "...", "supports": "node"}],
  "change_log": [{"step": 1, "action": "graph", "reason": "..."}],
  "artifacts": [{"path": "graph.json", "role": "deliverable", "exists": true}],
  "output_contract": {},
  "contract_coverage": [],
  "artifact_paths_valid": true
}
```

`deliverable.path` 固定为 `graph.json`；`artifacts[]` 中每个路径必须真实存在。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 两篇身份 | P1/P2 的 `doi/title/authors` 各自独立，无套用；`graph.json.papers` 与 `corpus.json` 一致 |
| G2 | 实体真实 | 每个 `label` 能在其 `source_ids` 引用的段落中找到；`type` 合法 |
| G3 | 证据回查 | 每条 evidence 的 `source_id:paragraph_id` 存在，`quote` 逐字可查，无页码 locator |
| G4 | 关系正确 | `type` 合法、方向明确、`conditions` 记录适用条件、`strength` 与原文语气一致 |
| G5 | 合并有据 | 每个候选对都有 `merge|keep_separate` 与 `basis`；不同样品/条件/数值未合并 |
| G6 | 查询可执行 | ≥3 条查询，均有 question/answer/结果子图，无法确定项写入 `uncertain` |
| G7 | 导出完整 | graph.json、graph.graphml、nodes.csv、edges.csv、queries.json、merge-report.json、graph.md 均存在 |
| G8 | 结果信封 | `result.json` 可解析，status 合理，artifact 路径真实，`output_contract` 原样保留 |

## 异常处理

| 情况 | 处理 |
|---|---|
| 语料为空/不可读 | prepare 返回 `input_error`；不生成凭常识编造的图谱 |
| 只有一篇论文 | 只建该篇图谱；在 `queries[].uncertain` 写明缺失论文，finalize 会按实际校验结果给出 `status`/`uncertainties` |
| 段落 ID 对不上 | 以 `work/corpus.json` 的 `paragraph_index` 为准；找不到就换 `source_id/paragraph_id` 或删掉该证据，绝不编 ID |
| 引文不是逐字子串 | 从原文重新截取；允许用 `…` 连接不连续片段；不要改写成同义句 |
| 同名概念疑似跨篇同一对象 | 可 `merge`（写 DOI 与原文等价依据）或 `keep_separate`；样品/条件不同必须 `keep_separate` |
| 两篇证据冲突 | 保留两条边（如 `supports` 与 `contradicts`）各带证据；不投票、不合并结论 |
| 查询无结果 | 修 `start_node_ids`/`edge_types` 或改用 `result_node_ids`；仍无结果则记录 `uncertain`，不伪造答案 |
| 时间不足 | 先写已核实的 nodes/edges/3 条 query，尽早 finalize；status 可为 partial |
| 入口异常 | 读 `work/main-error.json`；保留已写 `work/graph.json`，修复后重跑 finalize |

## 最小示例（synthetic，不是正式答案）

两篇 synthetic 论文（电池方向）：

- Q1（P1）：`Al2O3-coated LiFePO4 delivered 140 mAh g-1 at 1 C, while uncoated LiFePO4 delivered 110 mAh g-1.`
- Q2（P2）：`In a separate LiFePO4 sample, Al2O3 coating reduced charge-transfer resistance.`

```json
{"nodes": [
  {"id": "N1", "label": "LiFePO4", "type": "material", "aliases": [],
   "source_ids": ["P1"],
   "evidence": [{"source_id": "P1", "paragraph_id": "jats-p-0002",
                 "quote": "uncoated LiFePO4 delivered 110 mAh g-1"}]},
  {"id": "N2", "label": "Al2O3-coated LiFePO4", "type": "material", "aliases": [],
   "source_ids": ["P1"],
   "evidence": [{"source_id": "P1", "paragraph_id": "jats-p-0002",
                 "quote": "Al2O3-coated LiFePO4 delivered 140 mAh g-1"}]},
  {"id": "N3", "label": "LiFePO4 sample", "type": "material", "aliases": [],
   "source_ids": ["P2"],
   "evidence": [{"source_id": "P2", "paragraph_id": "jats-p-0101",
                 "quote": "In a separate LiFePO4 sample"}]}],
 "edges": [
  {"id": "E1", "source": "N2", "target": "N1", "type": "outperforms",
   "direction": "forward", "conditions": ["1 C"],
   "evidence": [{"source_id": "P1", "paragraph_id": "jats-p-0002",
                 "quote": "Al2O3-coated LiFePO4 delivered 140 mAh g-1 at 1 C"}],
   "strength": "direct"}],
 "merges": [
  {"candidate_ids": ["N2", "N3"], "decision": "keep_separate",
   "basis": "P2 只说 separate LiFePO4 sample，未声明与 P1 涂层样品相同",
   "conflict": "不同样品与测试条件"}],
 "queries": [
  {"id": "Q1", "question": "Which material delivered higher capacity at 1 C?",
   "start_node_ids": ["N2", "N1"], "edge_types": ["outperforms"],
   "result_node_ids": ["N2", "N1"],
   "answer": "Al2O3-coated LiFePO4 delivered 140 mAh g-1 vs 110 mAh g-1 for uncoated LiFePO4 at 1 C (P1).",
   "evidence": [{"source_id": "P1", "paragraph_id": "jats-p-0002",
                 "quote": "Al2O3-coated LiFePO4 delivered 140 mAh g-1 at 1 C"}],
   "uncertain": []}]}
```

`synthetic` 只演示字段与判断方式；正式运行时必须使用 `/input` 的真实段落，
并给出 ≥3 条查询。`finalize` 后应能核对：`graph.json`、`graph.graphml`、
`nodes.csv`、`edges.csv`、`queries.json`、`merge-report.json`、`graph.md`
均存在，`result.json.status` 与 `checks.problems` 一致。

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_kg.py`；prepare 建语料索引，
  finalize 校验并导出 GraphML/CSV/查询。
- 实体/关系类型是固定枚举；超出的关系先放入 `other` 并在 `uncertain` 说明。
- 证据校验是逐字/规范化匹配，不能替代人工语义判断；同义改写不算证据。
- 本包无 PDF 页码、图像像素与补充数据；所有定位只到段落 ID。
- 示例是 synthetic 接口样例，不是本题正式答案；不要复制其中的实体或来源。
