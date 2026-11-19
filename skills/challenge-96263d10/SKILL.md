---
name: journal-format-adapter
description: 学术格式与模板适配：读取真实 DOCX 模板的 styles.xml、sectPr、主题字体、标题/题注/参考文献样式，把稿件逐项适配为目标格式，输出可编辑 manuscript.docx、可复核 format-spec.json 与含官方 writing 字段的 result.json。Use for 模板排版、期刊格式、投稿清单、参考文献格式、DOCX 格式转换与模板识别。
---

# 学术格式与模板适配（Journal Format Adapter）

## 何时使用

输入包含“稿件 + 目标模板/投稿格式要求”，需要按模板排版、检查投稿格式或
把稿件从一种 DOCX 结构转换到另一种模板时使用。核心原则：**规格来自真实模板
文件，不靠文件名或经验猜测；正文由模型保留或改写，脚本只做读取、套用、装配
与回读检查。**

## 输入

| 输入 | 必需 | 说明 |
|---|---|---|
| 案例 JSON（含 `case_id`、`output_contract`） | 建议 | 缺失时从文件名推断 case_id |
| 稿件 | 必需 | DOCX / Markdown / TXT / LaTeX / PDF；PDF 先转文本 |
| `template.docx` | 强烈建议 | 真实模板；脚本直接读取其 OOXML 部件 |
| 格式指南（Text/Markdown/PDF） | 可选 | 作为模板的补充，不替代模板 |
| 图表、字体、参考文献库 | 可选 | 缺失时保留占位并写入 `unmet` |

文件角色由 `scripts/adapter.py` 按文件名、DOCX 词数与样式数量打分推断；
`--source` / `--template` / `--body` 可显式覆盖。

## 一键入口（唯一入口）

```bash
python3 {SKILL_DIR}/scripts/main.py \
  --input /input --output /app/output --work /app/work --json
```

`main.py` 内部顺序：输入分诊 → 读取模板 → 合并指南 → 生成/套用 DOCX →
回读实测 → 生成要求台账与官方字段。**不要分步调用其他脚本作为提交路径。**

模型可先按内容需要改写正文，把结果写到 `/app/work/adapted.md`，再运行：

```bash
python3 {SKILL_DIR}/scripts/main.py --input /input --output /app/output \
  --work /app/work --body /app/work/adapted.md --json
```

不传 `--body` 时，脚本把源稿正文**逐字保留**并只改格式；不会生成抽取式
“保底正文”。`--body` 也接受 DOCX，此时在该 DOCX 上套用模板样式。

## 模板识别（adapter.py 必须真实读取）

| OOXML 部件 | 提取内容 |
|---|---|
| `word/styles.xml` | docDefaults 的字体/字号/行距；Normal 正文；Title；Heading 1–6 及 `outlineLvl`；Caption/题注；Bibliography/参考文献；Abstract/Keywords；基于 `basedOn` 的继承 |
| `word/document.xml` | 所有 `sectPr` 的 `pgSz`（纸张/横向）、`pgMar`（页边距/页眉页脚距/装订线）、`cols`（栏数/栏间距）、`headerReference`/`footerReference`、`titlePg` |
| `word/theme/theme1.xml` | majorFont/minorFont 的 latin/ea/cs 主题字体 |
| `word/numbering.xml` | 标题编号存在性与编号层级线索 |
| 文档示例段落 | 题注相对于表/图的上下位置、题注标签（Figure/Table/图/表）、参考文献样式（numeric-bracket / numeric / author-year / GB/T 7714） |

每条规格都带 `meta.evidence`（字段←模板部件与定位）。`adapter.py` 还会把
指南中的显式值合并进来：指南值优先，冲突写入 `meta.conflicts`；模板未规定的
字段保持模板值。组织方字体修订：请求字体不可用时统一替换为提供的
Liberation Serif，并记录 `meta.font_substitutions`。

## 产物

| 路径 | 内容 |
|---|---|
| `/app/output/manuscript.docx` | 可编辑主稿；DOCX 源在副本上重排样式，正文/表格/图片/公式/域保留 |
| `/app/output/format-spec.json` | 结构化格式规格 + 逐项证据 + 冲突 + 未识别指南行 |
| `/app/output/docx-check.json` | 回读实测：页面、页边距、栏数、字体/字号、题注、参考文献、样式使用 |
| `/app/output/source-inventory.json` | 输入文件、稿件/模板/指南角色与内容单元计数 |
| `/app/output/result.json` | 官方 writing 字段 + 要求台账 + 保真 + 未满足项 |
| `/app/output/adaptation-report.md` | 人读摘要（逐项要求、实测值、限制） |

`result.json` 顶层必须包含 `case_id`、`deliverable`、`preserved_facts`、
`evidence_map`、`change_log`；并保留 `requirements`、`summary`、`fidelity`、
`unmet`、`uncertainty`、`source_inventory`、`docx_check`。

约定：

- `deliverable.path` 必须真实存在且非空；DOCX 失败时降级为可编辑
  Markdown，并在 `unmet` 写 `U-DOCX`。
- `preserved_facts` 覆盖数字、引用、题注、章节标题和术语；来源与产物定位
  来自真实段落，找不到写 `unverified`，不伪造。
- `evidence_map` 与 `requirements[]` 一一对应；`manual_check` 也写检查方式。
- `change_log` 记录页面、正文、标题、题注、参考文献、字体替换与正文来源。
- `output_contract` 声明但未产出的字段写 `null` 并进 `uncertainty`。

## 执行细节

1. **输入分诊**：记录每个文件后缀、大小、sha256、角色；区分模板（样式多、
   正文短、名称含 template/模板）与稿件（正文长、名称含 manuscript/paper/
   稿件）。只有一个 DOCX 且正文超过 300 词时视为稿件，不误当模板。
2. **规格提取**：`adapter.extract_template_spec` 读取上述 OOXML 部件；缺失
   Title/Heading/Caption/Bibliography 样式时按正文派生并标 `synthetic`。
3. **指南合并**：解析纸张、页边距、栏数/栏间距、字体、字号、行距、标题级
   字号、题注上下位置、参考文献样式、摘要/关键词要求；记录行号证据。
4. **生成主稿**：源稿是 DOCX 时 `docio.apply_docx` 复制源包、重写
   `styles.xml` 与所有 `sectPr`，按角色套用样式并保留正文对象；源稿是
   Markdown/TXT/LaTeX 时 `docio.build_docx` 以模板包为骨架重建正文。
5. **题注与交叉引用**：按文档顺序重新编号 Figure/Table/图/表；表格题注按
   模板要求移动到表上方或下方；正文中的同标签引用同步更新；重复编号时
   放弃重编号并写 warning。
6. **回读检查**：`docio.check_docx` 验证 zip、`document.xml`、段落数，并
   实测页面/页边距/栏数/字体/字号/题注/参考文献；`adapter.verify_requirements`
   逐条给出 `pass / fixed / missing / not_applicable / manual_check`。
7. **写结果**：`format-spec.json`、`docx-check.json`、`result.json`、
   `adaptation-report.md` 同源生成；所有写入结果的路径先验证存在。

## 退出码与失败处理

- `0`：已写出 `result.json`（`completed`、`partial` 或 `input_error` 都退 0）。
- `2`：只有输出目录本身无法创建时才使用。

| 情况 | 处理 |
|---|---|
| 没有模板 | 使用指南 + 内置 A4 默认规格；`status=partial`，写 `U-TEMPLATE` |
| 模板缺少标题/题注/参考文献样式 | 从正文派生并标 `synthetic`，进 `uncertainty` |
| 没有指南 | 只用模板值；无模板时标 `unverified`，不声称合规 |
| 指南是 PDF 且无文本 | 找同名 `.txt` 或 `pdftotext`；仍失败则记录并降级 |
| 指南行含格式词但未解析 | 保留在 `meta.unrecognized`，不静默丢弃 |
| 模板与指南冲突 | 指南值生效，冲突写入 `meta.conflicts` |
| 字体缺失 | 替换为 Liberation Serif，写 `font_substitutions` |
| 源稿是 DOCX | 在副本上套格式，保留表格、图片、公式、域、脚注与批注对象 |
| DOCX 生成失败 | 保留可编辑 Markdown 主稿，写 `U-DOCX`，`status=partial` |
| 公式/域/脚注/多节 | 盘点并保留原对象；无法自动重排的写入 `manual_check` |
| 无法生成 PDF/PNG 预览 | 不伪造预览；提示用 Word/LibreOffice 检查 |

## 支持文件

| 文件 | 何时用 |
|---|---|
| `scripts/main.py` | 唯一入口；一次完成分诊、提取、生成、回读、写结果 |
| `scripts/adapter.py` | 需要单独做模板提取、指南解析、规格合并或要求台账时；`main.py` 已自动使用 |
| `scripts/docio.py` | 需要单独读取 DOCX 结构、把源稿转 blocks、回读成品时；不生成抽取式正文 |
| `references/checklist.md` | 人工复核或补 `manual_check` 时逐条查 |
| `references/format-spec-schema.md` | 需要字段级约定、降级规则或交接时 |
| `references/example-trial.md` | 想快速理解一次完整调用时；synthetic 示例，非正式答案 |

调用 `adapter.py` / `docio.py` 的独立命令：

```bash
python3 {SKILL_DIR}/scripts/adapter.py --help
python3 {SKILL_DIR}/scripts/docio.py read --docx /app/output/manuscript.docx
python3 {SKILL_DIR}/scripts/docio.py check --docx /app/output/manuscript.docx
```

## 规则

1. 只做格式适配时不改数值、术语、结论与引用；确需改写必须由模型通过
   `--body` 提供并写入 `change_log`。
2. 模板未规定的事项保持原样，不擅自“优化”。
3. 无法满足的要求进 `unmet`，无法自动判断的进 `manual_check`，不静默跳过。
4. 每条规格能追溯到模板部件或指南行；推断值标 `synthetic`/`unverified`。
5. 不把“脚本已运行”当作通过证据；证据必须是产物中的可定位事实。
6. 不伪造预览、官方成绩或缺失的软件状态；示例均为 synthetic。
