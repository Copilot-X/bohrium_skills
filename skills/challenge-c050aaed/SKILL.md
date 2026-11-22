---
name: library-annotation-skill
description: 阅读批注与知识沉淀：从批注导出、PDF/Word/网页/扫描件中逐条提取批注，保留原文锚点、页码/段落、字符区间、类型、颜色与文件哈希；把批注结构化为可检索笔记，建立有依据的批注-笔记-主题关联，并交付 library-results.json、索引、操作日志与含官方 reading 字段的 result.json。Use for 阅读批注、锚点、知识沉淀、可检索笔记、annotation、library.
---

# 阅读批注与知识沉淀（library track · annotation）

## 用途与触发

task.md 要求“阅读批注”“整理标注”“把高亮/页边批注变成可检索笔记”
“保留批注到原文的锚点”，或 `/input/case.json` 给出 `library` /
`annotation` 相关字段时使用。

核心交付是**可回到原文的批注闭环**：

1. **盘点**：文件清单、版本、哈希、重复副本、权限与来源；
2. **抽取**：逐条批注的原文、页码/段落、字符区间、类型、颜色、来源文件；
3. **结构化**：批注 → 笔记（问题/方法/结论/可复用点/疑点），每条论断有原文；
4. **关联**：批注-笔记-主题/文献/方法之间建立有明确 `basis` 的链接，
   控制过度连接；
5. **检索**：按主题、标签、实体、来源文献和时间建立入口；
6. **记录**：操作日志、失败/跳过/冲突、低 OCR、旧版本批注、重复文件
   与人工复核项。

不适用：文献检索、文献阅读与内容判断、学术写作、长期归档制度建设。
材料不足时只交付可确认部分，把缺口写成 `uncertainties`，不要补造。

核心原则：**有输入、有锚点、有依据、有边界**。脚本负责盘点材料、
建立工作区、校验证据/字段/产物并组装 `result.json`；模型负责解析批注、
判断锚点、组织结构化笔记和关联。脚本不会替你生成批注或笔记。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：具体问题、范围、输出要求与证据范围。
- 阅读材料：`.pdf/.docx/.md/.txt/.html/.png/.jpg/.json` 等。
- 批注导出（如有）：`.md/.txt/.json`，常见格式为每行
  `A1 | file: ... | version: ... | page: ... | paragraph: ... | type: ... | color: ... | quote: ... | note: ... | ocr_confidence: ...`。
- 清单（如有）：`manifest.md/.json`，给出 `path/version/sha256/note`。
- 可选：已有工作文件、术语表、标签体系、历史版本目录。

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
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX/HTML 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、产物登记与 result 信封 |
| `scripts/annotation_gate.py` | 批注锚点、版本哈希、笔记支撑、关联依据、索引与契约自检 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/library-plan.json` 的字段契约 |
| `references/library-rules.md` | 锚点、版本、OCR、关联、检索与失败模式规则 |
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
2. 生成 `work/library-plan.json` 骨架（已存在则不覆盖）；
3. 写 `output/README-partial.md` 与 provisional `result.json`。

先读 `work/materials.json`、`work/inventory.json` 与 `task.md`，确认：
材料目录、文件类型、版本/副本、批注导出是否可用、扫描件/OCR、权限、
题目指定的输出契约键。缺失项立即写入 `uncertainties`。

### 第 2 步：建立文件清单与版本关系（约 15% 预算）

为 `/input` 下每个阅读材料建 `items[]` 条目：

```json
{
  "id": "D1",
  "path": "document.md",
  "kind": "md|pdf|docx|html|image|annotation-export",
  "metadata": {"title": "...", "authors": "...", "year": "..."},
  "version": "v2",
  "hash": "sha256:<hex>",
  "size": 1234,
  "status": "indexed|duplicate|superseded|unreadable|permission_denied",
  "duplicate_of": "",
  "supersedes": "",
  "text_layer": true,
  "evidence": {"path": "manifest.md", "locator": "D1", "quote": "..."}
}
```

规则：

1. **哈希优先**：用 `sha256` 区分内容相同与版本不同。相同哈希的副本
   标 `duplicate_of`，只保留一个 canonical 条目；不得把副本当独立证据。
2. **版本关系**：`version` 来自 manifest 或文件名（`_v1/_v2`）；旧版
   标 `superseded`，保留用于历史批注核对，但不与新版混为一条。
3. **不可读/无权限**：标 `unreadable` / `permission_denied`，写入
   `uncertainties` 与 `operations`，不静默跳过。
4. `hash` 必须与文件真实内容一致；无法计算时写 `hash_status=unavailable`
   并说明原因，不要填假哈希。

### 第 3 步：抽取批注（约 25% 预算）

优先从批注导出读取；没有导出时尝试从 PDF 注释对象/Word 批注/
扫描 OCR 中提取。每条 `annotations[]` 必须保留：

```json
{
  "id": "A1",
  "doc": "document.md",
  "path": "document.md",
  "version": "v2",
  "doc_sha256": "<真实哈希>",
  "page": 2,
  "paragraph": "P3",
  "char_start": 10,
  "char_end": 78,
  "quote": "逐字原文片段",
  "type": "highlight|underline|margin|handwriting|sticky|comment",
  "color": "yellow|blue|red|green|...",
  "anchor_status": "verified|unverified_low_ocr|version_mismatch|missing_source|needs_review|duplicate",
  "ocr_confidence": 0.42,
  "mapped_to_current": false,
  "note": "该批注的原始短注（如有）",
  "evidence": {"path": "annotations.md", "locator": "A1", "quote": "A1 | ..."}
}
```

锚点规则：

0. **`doc` 与 `path` 分工**：`doc` 是批注所属的阅读材料；`path` 是
   用来校验 quote 的文本来源。文本类材料两者相同；PDF/扫描件/图片等
   无文本层材料，`path` 指向批注导出或 OCR 文本（例如
   `annotations.md`），`doc` 仍指向原文件。finalize 会把每条带
   `quote` 的对象当作证据，因此二进制批注必须给 `path` 文本源，
   否则会出现 “source path not found”。

1. **逐字原文**：`quote` 必须能在 `doc` 对应版本中逐字找到（允许空白
   折叠/省略号分段）；找不到就标 `anchor_status=missing_source`，不要
   用“大意”代替。
2. **字符区间**：能在当前文本定位时给 `char_start/char_end`（Python
   字符串下标）；PDF 坐标另给 `rect`/`page`，不要伪造字符偏移。
3. **低 OCR**：扫描/手写批注带 `ocr_confidence`；低于 0.60（或题目
   给定阈值）必须标 `unverified_low_ocr`，`mapped_to_current=false`，
   进人工复核队列，不得标 `verified`。
4. **版本一致**：`doc_sha256` 必须等于该批注所属版本的哈希。若批注来自
   旧版（`version` 或哈希与当前版不同），必须标
   `anchor_status=version_mismatch`、`mapped_to_current=false`，不得
   自动挂到新版；只有人工确认改写后才可建立 `migrated_from` 链接。
5. **类型与颜色分离**：`type` 记录高亮/划线/页边/手写/便签，`color`
   单独记录颜色含义；不要把不同类型合并成 `annotation`。
6. **重复批注**：同一内容在重复副本/旧版中的批注标 `duplicate` 或
   `version_mismatch`，保留 canonical 来源。

### 第 4 步：结构化笔记（约 25% 预算）

每条笔记必须由至少一条批注/原文支撑，建议字段：

```json
{
  "id": "N1",
  "title": "Buffer drift and activity",
  "question": "Does buffer pH drift affect activity?",
  "method": "activity assay at 4 C and 25 C",
  "conclusion": "Activity decreased while pH drifted.",
  "reusable_points": ["cold storage requirement"],
  "caveats": ["single study"],
  "annotation_ids": ["A1", "A2"],
  "claims": [
    {"text": "Activity decreased by 18 percent.",
     "evidence": {"path": "document.md", "locator": "P3",
                  "quote": "Activity decreased by 18 percent after 12 months at 25 C."}}
  ],
  "tags": ["activity", "buffer"],
  "links": []
}
```

规则：

1. 笔记不是摘抄：至少包含问题/方法/结论/可复用点/疑点中的 3 类；
2. 每条 `claims[].evidence.quote` 必须逐字来自原文；无法逐字时删除该
   claim 或标 `needs_review`；
3. `annotation_ids` 必须指向真实批注；一条批注可支撑多条笔记；
4. 旧版/低 OCR 批注支撑的笔记标 `evidence_status=needs_review`，不能
   当作已确认结论；
5. 不把作者原话与你的推断混在同一 `conclusion`：推断写进 `caveats`
   或 `reusable_points` 并标明。

### 第 5 步：关联与检索（约 15% 预算）

`links[]`：

```json
{"from": "A1", "to": "N1", "basis": "same_quote|same_section|same_entity|same_doi_version|explicit_note",
 "confidence": "high|medium|low", "evidence": {"path": "document.md", "locator": "P3", "quote": "..."}}
```

规则：

1. `basis` 必须可解释；只共享一个通用词不算关联；
2. 同一对对象只保留一条链接；过度连接（一个笔记挂几十条低置信链接）
   要拆成主题/实体或降 confidence；
3. 低 OCR/旧版本批注的链接标 `needs_review`，不进入默认检索结果；
4. 跨版本批注只在人工确认后建立 `migrated_from`，并在 `operations`
   记录依据。

`search_index[]` 至少覆盖主题、标签、实体、来源文献、时间：

```json
{"term": "buffer pH", "kind": "topic", "annotation_ids": ["A2"],
 "note_ids": ["N1"], "source_docs": ["document.md"], "confidence": "high"}
```

规则：每个 `annotation_ids`/`note_ids` 必须存在；每个 verified
批注至少出现在一个索引项；旧版/低 OCR 项单独标注 `needs_review`，
不混进默认检索。

### 第 6 步：写产物与操作日志

同时写：

- `output/library-results.json`（主交付物，必须非空）：包含
  `case_id`, `summary`, `items`, `annotations`, `notes`, `links`,
  `search_index`, `operations`, `uncertainties`；
- `output/library-index.json`：文件/版本/哈希/重复/批注计数与检索索引；
- `output/operations.log.json`：每个抽取/校验/索引/跳过/失败操作的
  `action/target/status/detail/evidence`；
- 若 `output_contract` 有自定义键（例如 `operation_evidence`、
  `failure_recovery`）：在 result.json 顶层保留原键名，并写
  `output/results/<key>.json`（finalize 会登记 `output/results/` 下的
  文件；写到根目录可能导致契约未覆盖）。

### 第 7 步：自检（finalize 前必须运行）

```bash
python3 "$SKILL_DIR/scripts/annotation_gate.py" \
  --input /input --work /app/work --output /app/output
```

自检会检查：文件哈希与路径、批注锚点/字符区间/版本哈希、低 OCR 与
旧版本状态、笔记 claim 支撑、链接 basis、索引完整性、操作日志、
契约键产物。输出 `output/self-check.json`。有 `problems` 先修，再重跑。finalize 之后
**再运行一次** `annotation_gate.py --apply`：若仍有 problems，会把
`result.json` 的 `status` 从 `answered` 降为 `partial` 并写入
`self_check` uncertainty，避免旧版/低 OCR/契约缺口被标成 answered。

### 第 8 步：finalize（约 15% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/library-plan.json`（或 JSON 主产物），检查必填字段
   `annotations`, `evidence`；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/library-results.json` 与 `output/library-index.json`,
   `output/operations.log.json` 的真实文件；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `answered|partial|input_error`。有缺字段、证据不匹配、
主产物缺失或执行失败时用 `partial`；材料不可读时用 `input_error`。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`、`profile`；
- `deliverable`：`output/library-results.json` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings`/`notes[].claims`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote/claim 映射；
- `artifacts[]`：主产物、`output/library-index.json`、
  `output/operations.log.json`、`output/self-check.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、
  `contract_coverage`。

若 `/input/case.json` 的 `output_contract` 含本题特有键，必须：

1. 在 `result.json` 顶层保留原键名（finalize 会做别名映射）；
2. 写 `output/results/<key>.json` 或把 canonical 字段/artifact 路径
   填入该键；
3. 查看 `contract_coverage`，任一 `present=false` 都视为交付不完整，
   写入 `uncertainties` 并降级 `partial`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/library-results.json` 存在、非空、含 items/annotations/notes/links/search_index |
| G2 | 必填字段 | `annotations`, `evidence` 齐全且非空 |
| G3 | 锚点 | 每条批注有 doc/path、逐字 quote、page 或 paragraph 或字符区间 |
| G4 | 版本 | `doc_sha256` 与文件真实哈希一致；旧版批注 `version_mismatch` 且 `mapped_to_current=false` |
| G5 | OCR | 低置信度批注 `unverified_low_ocr`，不进默认检索 |
| G6 | 笔记 | 每条 note 有 annotation_ids 或逐字证据；claim 有原文 |
| G7 | 关联 | 每条 link 有 basis；无重复对；低置信/旧版链接标 needs_review |
| G8 | 检索 | 索引项引用真实 annotation/note；verified 批注至少被索引一次 |
| G9 | 日志 | 每个操作有 action/target/status/detail；失败/跳过不静默 |
| G10 | 契约/产物 | result.json 可解析；artifact 真实存在；contract_coverage 无 false |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；不生成伪批注/伪笔记 |
| 无批注导出、PDF 无注释对象 | 尝试 OCR/文本定位；仍无则写 `no_annotations_found` uncertainty，不编造 |
| 扫描件/OCR 置信度低 | `unverified_low_ocr` + 复核队列；不标 verified |
| 批注来自旧版本 | `version_mismatch` + `mapped_to_current=false`；保留旧引用，不自动迁移 |
| 重复文件 | 按哈希合并，标 `duplicate_of`；批注只保留 canonical 来源 |
| 权限不足/文件损坏 | 记 `permission_denied`/`unreadable` 与 operations；不静默跳过 |
| 批注 quote 找不到 | 标 `missing_source` 或进 uncertainties；不改写成“大意” |
| 笔记结论无原文 | 删除结论或标 `needs_review`；不把推断写成原文事实 |
| 关联依据不足 | 降 confidence 或删除 link；不按关键词硬连 |
| 输出契约键未覆盖 | 写 `output/results/<key>.json` 或映射 canonical 字段 |
| 时间不足 | 先交付可核对批注 + 最小笔记 + 索引，再补关联与复核队列 |

## 最小示例（synthetic，不是正式答案）

```json
{
  "case_id": "SYNTH-LIBRARY",
  "mode": "annotation",
  "summary": "Synthetic example only.",
  "items": [
    {"id": "D1", "path": "synthetic.md", "kind": "md", "version": "v1",
     "hash": "sha256:synthetic", "status": "indexed",
     "evidence": {"path": "synthetic.md", "locator": "P1", "quote": "synthetic source sentence"}}
  ],
  "annotations": [
    {"id": "AN1", "doc": "synthetic.md", "version": "v1",
     "doc_sha256": "synthetic", "page": 1, "paragraph": "P1",
     "char_start": 0, "char_end": 10, "quote": "synthetic",
     "type": "highlight", "color": "yellow", "anchor_status": "verified",
     "evidence": {"path": "synthetic.md", "locator": "P1", "quote": "synthetic source sentence"}}
  ],
  "notes": [
    {"id": "N1", "text": "Synthetic note", "claims": ["synthetic claim"],
     "annotation_ids": ["AN1"]}
  ],
  "links": [{"from": "AN1", "to": "N1", "basis": "same_quote"}],
  "search_index": [{"term": "synthetic", "annotation_ids": ["AN1"]}],
  "operations": [{"action": "index", "target": "synthetic.md", "status": "ok", "detail": "synthetic only"}],
  "evidence": [{"path": "synthetic.md", "locator": "P1", "quote": "synthetic source sentence"}],
  "uncertainties": []
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。

## 版本与限制

- 版本 `1.1.0`：锚点/版本/OCR 专项规则 + `scripts/annotation_gate.py`
  自检；模型写内容，脚本做锚点与可核对性审计。
- 不联网：只使用 `/input` 提供的材料或导出；不伪造在线检索结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断；批注/笔记的
  学术含义仍需作者复核。
- PDF 注释对象/Word 批注能否读取取决于运行时依赖；读不到时改用
  批注导出或文本定位，并如实记录。
- 示例是 synthetic 接口样例，不是正式案例答案。

## 本题专项检查

- 阅读批注、锚点、知识沉淀与可检索笔记
- 每条批注有原文锚点
- 标签/主题一致
- 可导出复用
