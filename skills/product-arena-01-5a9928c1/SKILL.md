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
- `/input` 下的原文：`.pdf/.md/.txt/.tex/.docx/.json`。
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
| `scripts/fam_translate.py` | 本任务的分段、对齐、术语与保真检查 |
| `task.json` | 指定 family、模块、profile 与输出契约 |
| `templates/segments.schema.json` | `work/segments.json` 字段说明 |
| `templates/glossary.schema.json` | `work/glossary.json` 字段说明 |
| `references/translation-rules.md` | 数字、引用、否定、情态、术语与失败模式 |
| `examples/source-zh.md` | synthetic 中文原文（非正式案例） |
| `examples/translation-en.md` | synthetic 英文译文（非正式答案） |
| `examples/glossary.json` | synthetic 术语表 |

## 工作流（译文优先）

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，提取原文并写出 `work/inventory.json`；
2. 按标题/段落/空行切分，生成 `work/segments.json`（`P001…`，
   每段含 `locator` 和 `sentences[]`）；
3. 生成 `work/glossary-candidates.json` 与 `work/translation-brief.json`；
4. 生成 `work/bilingual-template.md`（Source 已填、Target 待填）；
5. 写一份 `status=partial` 的 provisional `result.json`。

如果原文不可读，入口写 `input_error` 并退出 0；不要编造原文。

### 第 2 步：模型写译文（约 60% 预算）

打开 `work/segments.json`，按 `P001…` 顺序写两个文件：

1. `/app/output/translation.md`：完整纯译文，段落顺序与原文一致；
2. `/app/output/bilingual.md`：每个 `## Pxxx` 下写
   `- **Source (zh):** ...` 与 `- **Target (en):** ...`。

同时写 `/app/work/glossary.json`（5–20 条锁定术语）：

```json
{
  "terms": [
    {"source": "氧空位", "target": "oxygen vacancy",
     "aliases": ["oxygen vacancies"], "note": "全文统一"}
  ]
}
```

硬规则（详见 `references/translation-rules.md`）：

- **不增不删**：不归纳成摘要，不添加原文没有的解释、数据或结论；
- **数字与单位**：值、有效数字、范围、百分数、样本量、年份不变；
- **引用与编号**：`[12]`、`(Smith et al., 2020)`、`Fig. 2a`、`Table 1`、
  `Eq. (3)` 的位置与编号不漂移；
- **否定与情态**：`may < suggest < show < prove` 不升级；
  `associated with` 不改成 `causes`；
- **术语一致**：同一术语全文一种译法；锁定术语只用 `target`/`aliases`；
- **结构不变**：段落不合并、不拆分、不重排；标题层级、图注、表注、脚注留在原位；
- **不确定就标注**：给译法并括注原文，写入 `work/translation-notes.json`
  的 `flags`，不要静默猜。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 优先按 `bilingual.md` 的 `Pxxx` 对齐；没有时按 `translation.md` 的
   空行块顺序对齐，并在 `alignment_issues` 标明近似对齐；
2. 重建 `output/bilingual.md`（Source/Target 成对）；
3. 生成 `output/glossary.json`（锁定术语 + 候选缺口）；
4. 生成 `output/translation-check.json`：
   段覆盖、数字、引用、图表/公式编号、否定、情态、术语；
5. 生成 `output/result.json`，包含 reading 字段
   `status/result/claims/uncertainties` 与 writing 字段
   `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `answered|partial|insufficient_evidence|input_error`：
全部段落有 Target、数字/引用/术语检查通过 → `answered`；否则 `partial`。

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
  "result": {"summary": "Translated 20/20 segments"},
  "claims": [{"claim_id": "P001", "text": "..."}],
  "uncertainties": [],
  "artifacts": [{"path": "translation.md", "role": "deliverable"}],
  "output_contract": {}
}
```

`deliverable.path` 必须指向真实、非空的 `translation.md`；`artifacts[]`
每个路径必须存在。`preserved_facts` 覆盖真实出现的数字、引用、图表/公式编号
和锁定术语。`evidence_map` 每段至少一条可回查的 Source/Target 对。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 段覆盖 | `missing_segments=[]`，顺序与原文一致 |
| G2 | 数字 | 每个数值、单位、范围、百分数在译文中保留 |
| G3 | 引用/编号 | 引用、图/表/公式编号位置与编号不漂移 |
| G4 | 否定/情态 | 否定范围与情态强度不升级、不弱化 |
| G5 | 术语 | 锁定术语只有一种译法；标题/正文/图注/结论一致 |
| G6 | 对齐 | 每个 `Pxxx` 有 Source/Target；无空 Target |
| G7 | 契约 | `result.json` 可解析，官方字段与 artifact 路径真实 |

## 异常处理

| 情况 | 处理 |
|---|---|
| 原文为空/不可读 | `input_error`；不生成伪译文 |
| 扫描页无文字层 | 尝试 OCR；失败写 `ocr_unavailable` 和页码 |
| 跨页/跨栏段落被切碎 | 在 `bilingual.md` 中按语义合并到同一个 `Pxxx`，并在 `uncertainty` 说明 |
| 公式/表格为图片 | 在译文中保留占位符 `[Formula: p3]`，并在 `untranslated`/`uncertainty` 标注 |
| 术语表与原文冲突 | 以术语表为准，记录冲突；无法确定时保留原文并标注 |
| 时间不足 | 先写已译段落；未译段留空，`status=partial` |
| 入口脚本失败 | 读 `work/main-error.json`；保留已写译文，修复后重跑 finalize |

## 最小示例（synthetic，不是正式答案）

`synthetic source`：`氧空位是 CeO2 表面催化活性的关键缺陷。我们在 120 °C 下测试了 3 个样品[3]。`

```markdown
## P001
- **Source (zh):** 氧空位是 CeO2 表面催化活性的关键缺陷。我们在 120 °C 下测试了 3 个样品[3]。
- **Target (en):** Oxygen vacancies are the key defects governing the catalytic activity of CeO2 surfaces. We tested 3 samples at 120 °C [3].
```

`finalize` 后应能核对：`120 °C`、`3`、`[3]` 在 `preserved_facts`；
`status=answered`；`deliverable.path=translation.md`。

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + 本任务模块；prepare 写 provisional result；
  官方字段由入口从真实文件生成。
- 数字/否定/情态检查是启发式筛检，不能替代人工语义判断。
- PDF/OCR 依赖运行时工具；超长文需分块并在最后复核段数与术语。
- 示例是 synthetic 接口样例，不是本题答案；不要复制其中的实体或来源。
