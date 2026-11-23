---
name: source-bound-academic-translator
description: 文献翻译：把输入论文按段落完整译成目标语言，锁定术语、保留数字/单位/引用/图表编号/公式与否定情态，交付纯译文、段级双语对照、术语表和保真审计，并生成含 reading 与 writing 官方字段的 result.json。Source-bound academic translation with paragraph alignment, glossary lock, fidelity checks and an honest result envelope. Use for 文献翻译、中英对照、术语一致性、长文分段翻译、译文核验.
---

# 文献翻译（Source-Bound Academic Translator）

## 用途与触发

task.md 要求“翻译摘要/全文/图注/回复信”“中英对照”“术语统一”
“长文分段翻译”“译文核验”，或 `/input/case.json` 给出
`direction` / `target_language` 时使用。支持中→英与英→中。

成功标准不是“大意通顺”，而是：**段落齐全、术语稳定、事实可回查**。
脚本负责切段、对齐、术语候选、保真审计和 result.json；模型负责写译文。
脚本不会生成抽取式保底译文。

## 输入

- `/input/case.json`：`case_id`、`direction`、`output_contract`；字段原样保留。
- `/input/task.md`：语言方向、文体、读者、输出形态、术语表、字数、英式/美式。
- `/input` 下的原文：`.pdf/.md/.txt/.tex/.docx/.json`。本题默认**关闭 OCR**：
  PDF 先走文字层/`pdftotext`，无可读文字时直接 `partial/input_error`，
  避免逐页 OCR 在 900 秒预算内超时。
  PDF 由 `scripts/parsers.py` 依次尝试 PyMuPDF、`pdftotext -raw`、`pypdf`；
  扫描页尝试 `pdftoppm` + `tesseract`，失败会如实记录，不猜文字。
- 可选：作者术语表、期刊风格、已有译文（润色模式）。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 切段、分句、数字/引用/情态/术语审计、result.json 信封 |
| `scripts/parsers.py` | PDF/DOCX/TeX/JSON/CSV 文本与结构提取 |
| `scripts/batch_translate.py` | **预算内批处理**：把 segments 切成 12 段/批，模型只填 target |
| `scripts/assemble_translation.py` | 汇总 batch JSON，生成 translation.md/bilingual.md/glossary |
| `scripts/fam_translate.py` | 本任务的分段、对齐、术语与保真检查 |
| `task.json` | 指定 family、模块、profile 与输出契约 |
| `templates/segments.schema.json` | `work/segments.json` 字段说明 |
| `templates/glossary.schema.json` | `work/glossary.json` 字段说明 |
| `references/translation-rules.md` | 数字、引用、否定、情态、术语与失败模式 |
| `examples/source-zh.md` | synthetic 中文原文（非正式案例） |
| `examples/translation-en.md` | synthetic 英文纯译文（非正式答案） |
| `examples/bilingual.md` | 与 `work/segments.json` 的 P001–P012 对齐的 synthetic 双语示例 |
| `examples/glossary.json` | synthetic 术语表（锁定术语与别名） |

## 工作流（译文优先）

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，提取原文并写出 `work/inventory.json`；
   选择源文时排除 `glossary/translation/bilingual/template/schema` 等
   辅助文件，优先与 `case.json` 方向一致、篇幅最长的正文；
2. 方向判定顺序：`case.json` 的 `direction/translation_direction/
   language_direction`（支持 `zh->en`、`zh2en`、`中文→英文`）>
   `source_language`/`target_language` > `task.md` 的“中译英/英译中” >
   源文 CJK 比例；
3. 按标题/段落/空行切分，生成 `work/segments.json`（`P001…`，
   每段含 `locator`、`line_start/line_end` 和 `sentences[]`）；
4. 生成 `work/glossary-candidates.json`、`work/translation-brief.json`
   与 `work/translation-notes-template.json`；
5. 生成 `work/bilingual-template.md`（Source 已填、Target 待填）；
6. 写一份 `status=partial` 的 provisional `result.json`。

如果原文不可读或切不出任何段落，入口写 `input_error` 并退出 0；
不要编造原文。

### 第 2 步：按批写译文（预算内，不要整篇重写）

长文最容易失败的原因是模型每轮重写整份译文，64 次请求/900 秒很快耗尽。
本题固定使用批处理：

```bash
python3 "$SKILL_DIR/scripts/batch_translate.py" --work /app/work --output /app/output --size 12
```

脚本会在 `work/translation-batches/` 生成 `batch-001.json`…，每个文件只有
12 个 segment，字段为 `segment_id/source/target`。模型**只填 target**：

1. 每次读 2–4 个 batch，按 segment 顺序翻译，保持数字/单位/引用/图表编号/
   否定与情态/术语一致；
2. 把填好的 JSON 原样写回，不合并、不重排、不删 segment；
3. 不要在每轮重写 `translation.md`/`bilingual.md`，这两个文件最后由脚本生成。

同时写 `/app/work/glossary.json`（5–20 条锁定术语）；没有把握的术语在
`target` 中保留原文并写入 `work/translation-notes.json` 的 `flags`。

填完所有 batch 后运行：

```bash
python3 "$SKILL_DIR/scripts/assemble_translation.py" --work /app/work --output /app/output
```

它会生成 `output/translation.md`、`output/bilingual.md` 和 glossary；若有空
target，会列出 `missing_ids`，必须补齐后再 finalize。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 对齐顺序：`bilingual.md` 的 `Pxxx` > `translation.md` 的 `Pxxx`
   （`## P001`、`## P001 摘要`、`### P001` 均可）> 与段数完全相等的
   空行块顺序；只有「块数 = 段数 + 1 且首块是文件标题」时才接受
   标题偏移，并把方法写进 `alignment.method`。块数不一致时不会静默
   错位填充，而是保留空 Target 并在 `alignment_issues` 说明；
2. 解析 Source/Target 时支持 `**Source (zh):**`、`**Source (zh)**:`、
   全角冒号与多行续写；表格式 Target 保留换行；
3. 重建 `output/bilingual.md`（Source/Target 成对，含 `Pxxx` locator）；
4. 生成 `output/glossary.json`（锁定术语 + 候选缺口 + `issues`）：
   `work/glossary.json` 支持 `{"terms":[...]}`、`{"glossary":[...]}`、
   `{"源词":"译词"}` 映射；没有带 target 的术语表时 `locked=false`，
   状态不能标 `answered`；
5. 生成 `output/translation-check.json`：段覆盖、数字、引用（方括号、
   作者—年份、TeX/Pandoc）、图/表/公式编号、否定、情态、术语与对齐；
6. 若写了 `work/translation-notes.json`，复制为
   `output/translation-notes.json` 并写入 uncertainty；
7. 生成 `output/result.json`，包含 reading 字段
   `status/result/claims/uncertainties` 与 writing 字段
   `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `answered|partial|insufficient_evidence|input_error`：
全部段落有 Target，且数字/引用/图表编号/否定/情态/术语锁定/对齐
全部通过 → `answered`；否则 `partial`，逐项写 uncertainty。

## 输出契约

```json
{
  "case_id": "与输入一致",
  "status": "answered",
  "direction": "zh->en",
  "segments": [{"segment_id": "P001", "source": "...", "target": "...",
                "status": "translated", "locator": "paper.md P001"}],
  "glossary": [{"source": "氧空位", "target": "oxygen vacancy"}],
  "preservation": {"numbers_match": true, "citation_numbers_match": true,
                    "negation_match": true, "modality_match": true},
  "untranslated": [],
  "deliverable": {"path": "translation.md", "format": "md"},
  "preserved_facts": [{"kind": "number", "source": "92", "target": "92",
                       "locator": "paper.md P013"}],
  "evidence_map": [{"claim_id": "P001", "source": {"path": "paper.md",
                     "locator": "paper.md P001"}, "quote": "..."}],
  "change_log": [{"step": 1, "action": "prepare", "reason": "..."}],
  "checks": {"segments_total": 20, "segments_translated": 20,
              "numbers": {"missing": [], "extra": []},
              "citations": {"missing": [], "extra": []},
              "figures": {"missing": [], "extra": []},
              "negation": {"ok": true}, "hedging": {"ok": true},
              "glossary": {"locked": true, "ok": true},
              "alignment": {"method": ["bilingual:Pxxx"], "issues": []}},
  "preservation": {"numbers_match": true, "citation_numbers_match": true,
                    "figure_references_match": true, "negation_match": true,
                    "modality_match": true, "glossary_locked": true},
  "untranslated": [],
  "result": {"summary": "Translated 20/20 segments"},
  "claims": [{"claim_id": "P001", "text": "..."}],
  "uncertainties": [],
  "artifacts": [{"path": "translation.md", "role": "deliverable"}],
  "output_contract": {}
}
```

`deliverable.path` 必须指向真实、非空的 `translation.md`；`artifacts[]`
每个路径必须存在。`preserved_facts` 覆盖**译文中真实保留**的数字、引用、
图表/公式编号和锁定术语（只列 target 确实出现的项，不把 source 原文
直接当 preserved）。`evidence_map` 每段至少一条可回查的 Source/Target 对。
`translation-check.json` 的 `entries[]` 保留每段 `locator` 与
`source_locator`，供逐段回看；`glossary.json` 的 `locked` 为 false 时
不得把 `status` 写成 `answered`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 段覆盖 | `missing_segments=[]`，顺序与原文一致 |
| G2 | 数字 | 每个数值、单位、范围、百分数在译文中保留；`numbers.missing=[]` |
| G3 | 引用/编号 | 方括号引用、作者—年份、TeX/Pandoc 引用签名一致；`citations.missing=[]` |
| G4 | 图表/公式编号 | `Fig./Table/Eq./图/表/式` 的 kind 与编号不漂移；`figures.missing=[]` |
| G5 | 否定/情态 | 否定计数在容差内；强断言不增加、模糊限制语不明显丢失 |
| G6 | 术语 | `glossary.locked=true`，每个锁定 target/alias 在译文中出现 |
| G7 | 对齐 | 每个 `Pxxx` 有 Source/Target；无空 Target；`alignment.issues=[]` |
| G8 | 契约 | `result.json` 可解析，官方字段与 artifact 路径真实 |

## 异常处理

| 情况 | 处理 |
|---|---|
| 原文为空/不可读 | `input_error`；不生成伪译文 |
| 输入同时有辅助文件（术语表/模板） | 源文选择会排除这类文件；仍应核对 `work/segments.json` 的 `source_path` |
| 扫描页无文字层 | 尝试 OCR；失败写 `ocr_unavailable` 和页码 |
| 跨页/跨栏段落被切碎 | 在 `bilingual.md` 中按语义合并到同一个 `Pxxx`，并在 `uncertainty` 说明 |
| 公式/表格为图片 | 在译文中保留占位符 `[Formula: p3]`，并在 `untranslated`/`uncertainty` 标注 |
| 术语表与原文冲突 | 以术语表为准，记录冲突；无法确定时保留原文并标注 |
| 没有写术语表或 target 为空 | `glossary.locked=false`，`status=partial` + `glossary_not_locked`；不要假装已锁定 |
| `bilingual.md` 块数/标题格式异常 | 优先按 `Pxxx`；块数不等时不静默错位，写 `alignment` issue 并保留空 Target |
| 译文漏数字/引用/图表编号 | 对应 `number_mismatch`/`citation_mismatch`/`figure_reference_mismatch`，`status=partial` |
| 时间不足 | 先写已译段落；未译段留空，`status=partial` |
| 入口脚本失败 | 读 `work/main-error.json`；保留已写译文，修复后重跑 finalize |

## 最小示例（synthetic，不是正式答案）

`synthetic source`：`氧空位是 CeO2 表面催化活性的关键缺陷。我们在 120 °C 下测试了 3 个样品[3]。`

```markdown
## P001
- **Source (zh):** 氧空位是 CeO2 表面催化活性的关键缺陷。我们在 120 °C 下测试了 3 个样品[3]。
- **Target (en):** Oxygen vacancies are the key defects governing the catalytic activity of CeO2 surfaces. We tested 3 samples at 120 °C [3].
```

同时写最小锁定术语表（`work/glossary.json`）：

```json
{"terms": [{"source": "氧空位", "target": "oxygen vacancy"}]}
```

`finalize` 后应能核对：`120 °C`、`3`、`[3]` 在 `preserved_facts`；
`glossary.locked=true`；`status=answered`；`deliverable.path=translation.md`。
如果只有译文、没有带 target 的术语表，`glossary.locked=false`，结果只能是
`partial` 并写 `glossary_not_locked`。

## 版本与限制

- 版本 `1.1.0`：单入口 + 共享引擎 + 本任务模块；新增源文选择、
  方向推断、标题后缀/多行解析、ID 优先且拒绝静默错位的对齐、
  术语表锁定校验、图表编号检查、方向感知的否定/情态检查，以及
  `translation-notes.json` 支持。
- 数字/图表/术语/否定/情态检查是启发式筛检，不能替代人工语义判断；
  报告中的 `checks` 是复核起点，不是自动评分。
- PDF/OCR 依赖运行时工具；超长文需分块并在最后复核段数与术语。
- 示例是 synthetic 接口样例，不是本题答案；不要复制其中的实体或来源。
