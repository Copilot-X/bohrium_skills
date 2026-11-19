---
name: reference-citation-manager
description: 参考文献与引用管理：从真实稿件与文献库出发，把引用插到支撑具体论断的句子，去重并合并同 DOI/同题条目，按 GB/T 7714、APA、IEEE、Vancouver 重排编号与文末列表，逐条核验元数据与正文—条目一致性，交付可编辑稿件、同步文献表与审计。Reference and citation management with claim-level insertion, dedupe, renumbering, style conversion and metadata audits. Use for 引用管理、参考文献、BibTeX、GB/T 7714、APA、编号更新、文献去重.
---

# 参考文献与引用管理（Reference & Citation Manager）

## 用途与触发

task.md 要求“插入引用/补引用”“更新编号”“文献去重”“BibTeX/RIS 转
GB/T 7714 或 APA”“逐句核对正文与文献”“参考文献一致性检查”时使用。
不适用：没有稿件或没有文献库的开放式写作；需要联网检索真实文献且输入
没有提供条目时，只能做有限核验并如实标注。

核心原则：**挂得对、编得顺、收得净、对得上**。
模型判断“哪句话需要哪条文献”；脚本负责解析文献库、应用插入、去重、
重排编号、格式化与一致性审计。

## 输入

- `/input/case.json`：`case_id`、`citation_style`/`style`、`output_contract`。
- `/input/task.md`：目标规范、是否要求逐句核验、是否允许删改条目。
- 稿件：`.md/.txt/.tex/.docx`；文献库：`.bib/.ris/.json/.csv`。
- 可选：`work/citation-plan.json`、已有参考文献表。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run |
| `scripts/skillcore.py` | BibTeX/RIS 解析、去重键、GB/T 7714/APA/IEEE/Vancouver 格式化、引用 token |
| `scripts/parsers.py` | 稿件与文献库提取 |
| `scripts/fam_cite.py` | 引用点、插入、去重、编号重排、审计 |
| `task.json` | family、模块、profile、输出契约 |
| `templates/citation-plan.schema.json` | `work/citation-plan.json` 字段 |
| `templates/result-fields.md` | 结果字段与状态说明 |
| `references/citation-rules.md` | 引用位置、编号、去重、格式与核验规则 |
| `examples/manuscript.md` | synthetic 稿件 |
| `examples/refs.bib` | synthetic 文献库 |
| `examples/citation-plan.json` | synthetic 插入/去重计划 |

## 工作流（先建引用点，再插引用）

### 第 1 步：prepare（约 15% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口产出：

- `work/manuscript.json`：段落/句子 `Pxxx-Sxxx`、现有引用、参考文献表；
- `work/bibliography.json`：按 `key` 索引的文献条目与 `dedupe_key`；
- `work/citation-points.json`：每个句子的 `has_citation`、`likely_claim`、
  `locator`；
- `work/dedupe-candidates.json`：同 DOI / 同题同年的候选合并组；
- `work/citation-plan-template.json`：待填计划；
- `output/result.json`：`status=partial` 的 provisional 结果。

### 第 2 步：写 `/app/work/citation-plan.json`（约 55% 预算）

```json
{
  "case_id": "与输入一致",
  "style": "gb|apa|ieee|vancouver",
  "insertions": [
    {"id": "C001", "paragraph_id": "P005", "sentence_id": "P005-S002",
     "anchor_text": "逐字来自该句的论断片段",
     "ref_keys": ["smith2020"],
     "position": "sentence_end|after_anchor",
     "reason": "这条文献具体支撑哪个论断"}
  ],
  "dedupe": [
    {"keep": "smith2020", "merge": ["smith2020preprint"],
     "basis": "same DOI 10.1234/...", "decision": "merge|keep_separate"}
  ],
  "metadata_fixes": [
    {"key": "smith2020", "field": "year", "before": "2020", "after": "2021",
     "source": "doi:10.1234/..."}
  ],
  "unresolved": []
}
```

插入规则（详见 `references/citation-rules.md`）：

1. **挂得对**：引用放在支撑该论断的句子内，优先 `after_anchor`；
   不放在整段末尾或图表注旁边。
2. **一句多引**：`ref_keys` 给多条，按同一位置插入；
   **多句一引**：每句分别写一条 insertion。
3. **只引真实条目**：`ref_keys` 必须存在于 `work/bibliography.json`；
   找不到就写入 `unresolved`，不要编 key。
4. **去重可解释**：优先 DOI；其次规范化标题 + 年份；
   预印本/正式版、数据库重复导出用 `merge` 并写 `basis`。
5. **元数据不臆造**：`metadata_fixes` 必须有 `source`（DOI/原文/数据库），
   否则不改。
6. **编号同步**：数字样式由入口按正文首次出现重排；作者—年份样式不编号。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 应用 `dedupe`，把 merged key 映射到 keep key，并从文献表移除重复条目；
2. 按 `anchor_text` 在指定句子插入 `[[key]]` 占位符；
3. 数字样式：按正文首次出现分配 `[n]`，已有引用保留并顺延；
   作者—年份样式：写成 `(Author, Year)`；
4. 重建文末参考文献表（GB/T 7714 / APA / IEEE / Vancouver）；
5. 生成 `output/manuscript-cited.md`、`references.json`、
   `references-formatted.md`、可选 `references.bib`、`manuscript-cited.docx`；
6. 生成 `output/citation-audit.json` 与 `citation-plan-applied.json`；
7. 写 `output/result.json`（官方 writing 四字段由入口生成）。

`status` 取 `completed|partial|input_error`。有 blocked 插入、编号不连续、
未知引用、重复条目时用 `partial`。

## 输出契约

`result.json` 必须含：

- `deliverable`：`manuscript-cited.md` 的真实路径；
- `preserved_facts[]`：数字/引用 token，带 `source`/`locator`；
- `evidence_map[]`：每条 insertion 的 claim、锚点、来源；
- `change_log[]`：prepare/插入/去重/重编号/审计；
- `artifacts[]`：cited manuscript、references、audit、change record；
- `result`：`audit`（插入/阻塞/去重/编号/未知 token）；
- `case_id`、`status`、`output_contract`、`contract_coverage`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 插入位置 | 每条引用在支撑论断的句子内，锚点逐字存在 |
| G2 | 文献真实 | 每个 ref key 在文献库中存在，无编造条目 |
| G3 | 编号同步 | 正文 `[n]` 与文末列表一一对应，连续且唯一 |
| G4 | 去重 | 同 DOI/同题条目合并，有 `basis`；不同文献不误合并 |
| G5 | 格式 | 目标样式字段齐全、标点一致 |
| G6 | 元数据 | 未提供来源的字段不改；修改有记录 |
| G7 | 契约 | result.json 可解析，artifact 路径真实 |

## 异常处理

| 情况 | 处理 |
|---|---|
| 文献库为空 | 不插入引用；`unresolved` 写缺口，`status=partial` |
| DOI 解析失败 | 用规范化标题+年份；仍失败则 `keep_separate` 并说明 |
| 条目歧义/同名作者 | 不自动合并；写 `keep_separate` + `basis` |
| 编号冲突 | 按正文首次出现重排；保留原条目文本 |
| 稿件没有参考文献节 | 入口重建；`change_log` 记录 |
| `before`/锚点找不到 | 该条 blocked，稿件原文不变 |
| 目标样式未知 | 默认 GB/T 7714；写入 `uncertainty` |
| 文件损坏/编码错误 | `input_error` 或 partial；不伪造条目 |

## 最小示例（synthetic，不是正式答案）

```json
{"id": "C001", "paragraph_id": "P005", "sentence_id": "P005-S002",
 "anchor_text": "can activate CO2 through surface defects",
 "ref_keys": ["lee2022"], "position": "after_anchor",
 "reason": "该句关于表面缺陷活化 CO2，由 lee2022 的 operando 研究支撑"}
```

去重：

```json
{"keep": "smith2020", "merge": ["smith2020preprint"],
 "basis": "same DOI 10.1234/jcat.2020.001", "decision": "merge"}
```

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + 本任务模块；支持 GB/T 7714、APA 7、
  IEEE、Vancouver；数字与作者—年份两种引用形态。
- 不联网检索；DOI/元数据核验基于输入文献库与条目内已有字段。
- 去重键是启发式，最终合并需人工复核；不同文献不自动合并。
- 示例是 synthetic 接口样例，不是正式案例答案。
