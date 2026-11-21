---
name: library-classification-skill
description: 文献与资料自动分类：定义分类体系与判据，把每个输入文件真实归入目录、生成标签与规范文件名，识别重复/版本并输出复核队列。交付 library-results/index/operations 三个真实产物与可回滚日志，并生成含官方 reading 字段的 result.json。Use for 文献与资料自动分类、classification、library、taxonomy、复核队列、批量整理.
---

# 文献与资料自动分类（library track · classification）

## 用途与触发

task.md 要求“分类体系、批量归类、边界案例与人工复核”，或 `/input/case.json` 给出
`library` / `classification` 相关字段时使用。核心交付是
**分类体系、逐文件归类、真实操作日志与复核队列**，不是一段泛泛建议。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代同行评审/机构审查。
材料不足时只交付可确认部分，并把缺口写成 `uncertainty`，不要补造。

核心原则：**有输入、有依据、有产物、有边界**。模型负责判断与分类；脚本负责盘点、
哈希、证据核对、复核队列审计、真实复制/命名与产物组装。脚本不会替你选类别。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：本题范围、目录/命名规则、输出要求与证据范围。
- `/input` 下的材料：`.md/.txt/.json/.csv/.pdf/.docx/.tex/.yaml` 等。
- 可选：已有目录快照、元数据表、术语表、命名模板、版本说明。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **入口**：prepare / finalize / inspect |
| `scripts/classify_library.py` | **分类执行器**：校验 taxonomy/证据/覆盖/复核队列，写三个产物，`--materialize` 真实复制并记录回滚 |
| `scripts/fam_tracks.py` | finalize 的分类专项闸门（classification 分支）；与 plan/产物不一致时降级 `partial` |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `task.json` | family/mode、工作文件、产物、必填字段与质量闸门 |
| `templates/work.schema.json` | `work/library-plan.json` 字段契约 |
| `references/library-rules.md` | 分类、命名、去重、版本、复核与 no_result 规则 |
| `examples/synthetic-input.json` | synthetic 工作文件示例（非正式案例） |

## 工作流

## 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口盘点 `/input`，提取文本/JSON/CSV，写 `work/inventory.json` 与
`work/materials.json`，生成 `work/library-plan.json` 骨架和暂定 `result.json`。
先读 `work/materials.json` 与 `task.md`，确认真实文件清单、目录约定与缺失项。

## 第 2 步：设计分类体系并逐文件归类（约 50% 预算）

在 `work/library-plan.json` 中先写 `taxonomy`，再写 `items`。**每个输入文件恰好一条 item**。

### 2.1 taxonomy（先定义，再分类）

```json
"taxonomy": {
  "categories": ["期刊论文", "预印本", "会议论文", "数据集", "补充材料"],
  "criteria": {
    "期刊论文": "有期刊名/卷期/DOI 且经过同行评审",
    "预印本": "标题或页面明确标注 preprint/arXiv/bioRxiv，未见正式卷期",
    "会议论文": "有会议名、年份与 proceedings 标识",
    "数据集": "表格/数组数据，字段可解析，非叙述性文档",
    "补充材料": "SI/附录/图表源数据；无法可靠判断时的人工复核兜底"
  },
  "threshold": 0.7
}
```

- 类别数按材料实际情况定，通常 3–8 类；类别名稳定、可复用。
- 每类必须有**可执行判据**（看哪些字段/文本），不能写“其他”。
- `threshold` 是复核阈值：`confidence < threshold` 必须进 `review_queue`。
- 跨主题文件只选**一个主类别**；其余维度写进 `tags`，不要一文件多类别。

### 2.2 items（逐文件、可核对）

```json
{
  "id": "I001",
  "path": "materials/paper-a.pdf",
  "category": "期刊论文",
  "tags": ["催化", "图神经网络"],
  "confidence": 0.93,
  "new_name": "2024-Zhang-gnn-catalyst.pdf",
  "hash": "sha256:...",
  "review": false,
  "rationale": "标题、期刊名与 DOI 均指向同行评审论文；方法段使用 GNN",
  "evidence": {
    "path": "materials/paper-a.pdf",
    "locator": "p1 title block / p3 methods",
    "quote": "Journal of Catalysis 421 (2024) 112-125"
  }
}
```

规则：
- `path` 必须是输入文件相对路径；不得虚构文件。
- `hash` 用输入文件 sha256；`classify_library.py` 会重算并核对。
- `quote` 必须逐字来自材料（允许空白折叠/省略号分段）；找不到就重摘或标 uncertainty。
- 元数据缺失时如实写 `null`/`unknown`，不补造作者、年份、DOI。
- `new_name` 按题目模板；去掉路径分隔符与非法字符，保留扩展名；同名冲突由脚本追加 `-2`。
- 低置信度、跨类别、无文本层 PDF、同名不同版本 → `review: true` 并进复核队列。
- 无法读取的文件也要有一条 item，`review: true`，证据写可确认的元数据/文件名，不能静默丢弃。

### 2.3 去重与版本

- `duplicate_groups`：sha256 相同或 DOI+标题完全相同的条目组；至少 2 个 item id。
- `version_groups`：预印本/正式版/修订版/补充材料；写 `versions`、`preferred`（保留哪份）
  与 `reason`；不同版本不得当作重复删除。
- 去重只归档，不物理删除；每个操作要有 `rollback`。

### 2.4 复核队列、筛选与 no_result

- `review_queue` 必须覆盖所有低置信度/边界/不可读条目，每条给 `item_id`、`reason`、
  建议动作；`review` 标志与队列必须一致。
- 复核中的条目**不得移动或改名**；先保留原位，人工确认后再执行。
- 题目给筛选条件时，写 `filters`（must/must_not）与 `filter_execution`（kept/excluded 计数）；
  未命中要写 `no_result: true` 与原因，不把“没找到”写成“排除”。
- 输入确实没有材料时：`no_result: true`，summary 说明，不生成伪分类。

## 第 3 步：运行分类执行器（约 25% 预算）

```bash
python3 "$SKILL_DIR/scripts/classify_library.py" \
  --input /input --work /app/work --output /app/output \
  --materialize --json
```

它会：
1. 盘点输入并重算每个文件 sha256；
2. 校验 taxonomy/criteria/threshold、每文件恰好一次、category/rationale/hash；
3. 逐字核验每条 evidence；不匹配直接报错，不写假结果；
4. 校验 review_queue、operations、duplicate/version groups；
5. 写 `output/library-results.json`、`output/library-index.json`、`output/operations.log.json`；
6. `--materialize` 时真实复制到 `output/library/<category>/<new_name>`，
   记录 `sha_before/sha_after/rollback`；同名冲突追加 `-2`，不覆盖原文件。

先 `--check-only` 可只诊断不改产物。有 problems 时先修 plan，再重跑；不要绕过。

## 第 4 步：finalize（约 15% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

finalize 会再次审核必填字段、证据、产物和 `output_contract` 覆盖；
`fam_tracks.py` 的 classification 分支额外核对：
- taxonomy 判据与 threshold；
- 每个输入文件恰好一次、复核队列与 review 标志一致；
- 低置信度条目未被移动/改名；
- 三个输出文件与 plan 的 item/path 一致；
- 声称 materialized 时 `output/library/` 真实存在。

任何问题都会写入 `work/classification-audit.json`，并把 `result.json` 降级为 `partial`。

## 输出契约

`result.json` 必须含 `case_id`、`status`、`task_status`，以及
`deliverable`、`result`、`claims[]`、`preserved_facts[]`、`evidence_map[]`、
`artifacts[]`、`uncertainties[]`、`change_log[]`、`output_contract`、
`contract_coverage`、`classification_audit`。

`output/library-results.json` 主交付物必须非空，含：
`taxonomy`（类别+判据+threshold）、`items`（逐文件 category/tags/confidence/new_name/hash/
review/rationale/evidence/target）、`counts`、`coverage`（input/classified/unclassified/
unknown/no_result）、`review_queue`、`duplicate_groups`、`version_groups`、`operations`、
`input_snapshot`、`materialized`。

`output/library-index.json`：类别 → 文件列表与每个文件的 target/tags/hash/confidence/review。
`output/operations.log.json`：真实操作与 `planned_operations`、`rollback_all`。

若 `output_contract` 含本题特有键（如 `classification_results`、`review_queue`），
必须保留原键名并填入真实内容或同名产物路径；未覆盖会降级 `partial`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | taxonomy | 类别非空、每类有判据、threshold 合法 |
| G2 | 覆盖 | 每个输入文件恰好一条 item；无未知路径 |
| G3 | 证据 | 每条分配有 path/locator/quote，quote 逐字可查 |
| G4 | 复核 | 低置信度/边界/不可读进队列；队列与 review 一致；不得移动 |
| G5 | 重复/版本 | 组内引用真实 item；重复同哈希；版本有 preferred + reason |
| G6 | 产物 | 三个输出真实存在且与 plan 一致；materialize 时目录树存在 |
| G7 | 操作 | 移动/改名有 from/to、sha before/after、rollback |
| G8 | 契约 | result.json 可解析，case_id 一致，output_contract 全覆盖 |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `no_result=true` 或 `input_error`；不生成伪分类 |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要”；`status=partial` |
| 无文本层 PDF/扫描件 | 归入复核队列，记录可确认元数据，不猜正文 |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 uncertainty |
| 元数据缺失 | 写 `null/unknown`；不补造作者、年份、DOI |
| 同名不同版本/来源 | 保留全部，版本组写 preferred + reason；不覆盖/删除 |
| 同名目标已存在 | 脚本追加 `-2`，记录 rollback；不静默覆盖 |
| 代码执行失败 | 保留 `classification-audit.json` 与失败原因；不伪造输出 |
| 权限/格式错误 | 写 uncertainty 与已完成范围；建议人工接管 |

## 最小示例（synthetic，不是正式答案）

见 `examples/synthetic-input.json`：一个类别、一条 item、一条可回滚操作；
`classify_library.py --self-test` 会构造 6 个合成文件（含重复、版本、边界）并断言
分类覆盖、复核队列、逐字证据、真实复制与回滚记录。示例只说明接口，不冒充正式案例。

## 版本与限制

- 版本 2.0.0：taxonomy/threshold、逐文件覆盖、复核队列强校验、真实 materialize 与回滚。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断；低置信度仍需人工复核。
- 不联网：只使用 `/input` 提供的材料或导出；不伪造在线查询结果。
