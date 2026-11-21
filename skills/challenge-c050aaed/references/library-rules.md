# 阅读批注与知识沉淀：执行规则

本文件是 `SKILL.md` 的可执行补充。字段与
`templates/work.schema.json`、`scripts/fam_tracks.py` 的 library 检查、
`scripts/annotation_gate.py` 的自检一致。

## 1. 任务口径

- 目标：批注抽取 → 笔记结构化 → 关联 → 检索，交付可复核结果。
- 主产物：`output/library-results.json`。
- 必填字段：`annotations`, `evidence`。
- 关键原则：**每条批注能回到原文；每条笔记论断有原文支撑；每条关联
  有依据；未完成与失败不静默。**

## 2. 文件清单与版本

每个输入文件一条 `items[]`：

| 字段 | 要求 |
|---|---|
| `path` | 相对 `/input` 的真实路径 |
| `kind` | pdf/docx/md/txt/html/image/annotation-export |
| `metadata` | 标题、作者、年份等能从材料读到的字段；读不到留空 |
| `version` | manifest 或文件名中的版本；没有则标 `unknown` |
| `hash` | 真实 sha256；无法计算标 `hash_status=unavailable` |
| `status` | indexed/duplicate/superseded/unreadable/permission_denied |
| `duplicate_of` / `supersedes` | 重复或版本关系的 canonical 指向 |
| `evidence` | 支撑该条目的清单/原文 path+locator+quote |

规则：

1. 相同哈希 = 内容相同，标 `duplicate_of`，只保留一个 canonical；
2. 不同哈希但同文不同版，标 `supersedes`/`superseded`，批注按版本分开；
3. 无权限/损坏/无文本层都要记录，不能从清单里消失；
4. 哈希必须真实；无法计算时说明原因，不填假值。

## 3. 批注锚点

每条批注至少保留：

| 字段 | 要求 |
|---|---|
| `id` / `annotation_id` | 唯一 |
| `doc` + `version` + `doc_sha256` | 指向真实文件与真实哈希 |
| `path` | 校验 quote 的文本来源；二进制/扫描件指向批注导出或 OCR 文本 |
| `page` | 页码（PDF/扫描件）；文本文件可空但要有 paragraph |
| `paragraph` | 段落号/行号/章节；文本文件至少给一个 |
| `char_start`/`char_end` | 能在当前文本定位时给 Python 字符串下标；PDF 坐标用 `rect`，不伪造 |
| `quote` | 逐字原文；允许空白折叠/省略号分段 |
| `type` | highlight/underline/margin/handwriting/sticky/comment |
| `color` | 原颜色或颜色语义 |
| `anchor_status` | 见下表 |
| `evidence` | 批注导出/清单的原文 |

`anchor_status` 取值：

- `verified`：quote 在对应版本逐字找到，且版本哈希一致；
- `unverified_low_ocr`：扫描/手写 OCR 置信度低于阈值；
- `version_mismatch`：批注来自旧版/哈希不一致；
- `missing_source`：找不到原文或源文件缺失；
- `needs_review`：定位有歧义、跨栏/跨页、人工确认；
- `duplicate`：重复副本中的同一批注。

规则：

0. 文本材料的 `path` 通常等于 `doc`；PDF/扫描件/图片等无文本层
   材料要把 `path` 指向批注导出或 OCR 文本，否则 finalize 的逐字
   证据检查会报 `source path not found`；
1. `verified` 必须有 page 或 paragraph，以及（能定位时）字符区间；
2. 低 OCR（< 0.60 或题目阈值）不得标 `verified`，且
   `mapped_to_current=false`；
3. `doc_sha256` 必须等于当前版本哈希；旧版批注标
   `version_mismatch`，不得自动挂到新版；
4. `quote` 找不到时标 `missing_source`，不要改写或翻译；
5. 同一批注在多个副本中出现时，保留 canonical，其余标 `duplicate`。

## 4. 笔记结构

每条 note 至少包含 3 类信息：问题、方法、结论、可复用点、疑点。

```json
{
  "id": "N1",
  "question": "...",
  "method": "...",
  "conclusion": "...",
  "reusable_points": ["..."],
  "caveats": ["..."],
  "annotation_ids": ["A1"],
  "claims": [
    {"text": "...", "evidence": {"path": "document.md", "locator": "P3",
                                 "quote": "逐字原文"}}
  ],
  "tags": ["..."]
}
```

规则：

1. `annotation_ids` 必须指向真实批注；没有批注支撑的笔记要有独立原文证据；
2. 每条 claim 的 quote 逐字可查；
3. 作者的结论与你的推断分开：推断放 caveats/reusable_points 并标
   `author_inference` 或 `needs_review`；
4. 旧版/低 OCR 批注支撑的 claim 标 `evidence_status=needs_review`。

## 5. 关联

`links[]` 的 `basis` 必须可解释：

| basis | 含义 | 例子 |
|---|---|---|
| `same_quote` | 批注与笔记引用同一原文 | A1 ↔ N1 |
| `same_section` | 同一章节/段落 | A1 ↔ N2 |
| `same_entity` | 同一材料/方法/数据集（有原文依据） | 同名样本 |
| `same_doi_version` | 同一文献/版本链 | v1 ↔ v2 迁移 |
| `explicit_note` | 批注短注明确指向笔记 | note: mechanism |

规则：

1. 只共享一个通用词（“activity”“method”）不构成关联；
2. 每对对象只保留一条 link；重复链接合并并保留最强依据；
3. 低置信/旧版/低 OCR 链接标 `needs_review`；
4. 一个笔记挂过多低置信链接时，拆成主题/实体索引，不要让关联图失控。

## 6. 检索索引

`search_index[]` 至少覆盖主题、标签、实体、来源文献、时间：

```json
{"term": "buffer pH", "kind": "topic", "annotation_ids": ["A2"],
 "note_ids": ["N1"], "source_docs": ["document.md"],
 "confidence": "high", "needs_review": false}
```

规则：

1. `annotation_ids`/`note_ids` 必须存在；
2. 每个 verified 批注至少被一个索引项覆盖；
3. 旧版/低 OCR 批注的索引项 `needs_review=true`，默认检索可排除；
4. 检索词的召回不足/误召回写入 `uncertainties`，不要假装全覆盖。

## 7. 操作日志

`operations[]` 每条：

```json
{"action": "extract|anchor|index|link|skip|fail|recover",
 "target": "A1", "status": "ok|skipped|failed|needs_review",
 "detail": "...", "evidence": {"path": "...", "locator": "...", "quote": "..."}}
```

失败、跳过、权限不足、文件损坏、旧版本、重复、低 OCR 都要有记录；
`operations.log.json` 是 `operations[]` 的独立导出。

## 8. 输出契约

- `library-results.json`：主交付物，包含全部字段；
- `library-index.json`：文件/版本/哈希/重复/批注计数 + 检索索引；
- `operations.log.json`：操作记录；
- `output/results/<contract_key>.json`：`case.json` 的每个自定义契约键
  一个文件（如 `operation_evidence.json`、`failure_recovery.json`）；
  写在 `output/results/` 下才会被 finalize 稳定登记。
- `result.json`：`case_id/status/deliverable/evidence_map/uncertainties/
  artifacts/output_contract/contract_coverage`。

## 9. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 把未找到当没有批注 | 遗漏被掩盖 | 单列 `no_annotations_found` 与检索范围 |
| 旧版批注挂到新版 | 版本串味 | `version_mismatch` + 人工确认 |
| 低 OCR 标 verified | 错误锚点 | `unverified_low_ocr` + 复核队列 |
| 笔记只摘抄 | 无知识沉淀 | 补问题/方法/结论/可复用点/疑点 |
| 关联靠关键词 | 过度连接 | 用可解释 basis，降 confidence |
| 重复文件重复计数 | 统计虚高 | 按哈希合并，保留 canonical |
| 伪造哈希/坐标 | 不可复现 | 只写真实值，缺失说明 |
| 契约键未覆盖 | partial | 写 `output/results/<key>.json` |

## 10. 自检

运行 `scripts/annotation_gate.py` 后确认：

1. `problems=[]`；`warnings` 在报告/uncertainties 说明；
2. 每条批注 quote 逐字可查，哈希与版本一致；
3. 旧版/低 OCR 批注状态正确，`mapped_to_current=false`；
4. 每条 note 的 claim 有原文，links 有 basis，索引引用真实 id；
5. operations 覆盖失败/跳过/重复/复核；
6. output_contract 每个键都有 `output/results/<key>.json` 或映射。
