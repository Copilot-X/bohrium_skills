---
name: section-aligned-paper-notes
description: 文献总结与结构化笔记：先按论文原有标题切分章节，再逐节写目的/方法/结果/结论，补齐论文卡片（研究问题、数据、方法、主要发现、限制、条件）与带证据锚点的知识点，交付 notes.md、paper-card.json、knowledge-points.json、notes-index.json 与可编辑 notes.docx。Section-aligned structured reading notes with reusable paper card, knowledge points and verbatim source anchors. Use for 文献总结、结构化笔记、论文卡片、章节摘要、知识点提取、证据回链、可检索笔记、notes.json.
---

# 文献总结与结构化笔记（Section-Aligned Paper Notes）

## 用途与触发

当 task.md 要求“文档总结/结构化笔记/论文卡片/逐章摘要/知识点提取/
证据回链/可检索笔记”，或 `/input/case.json` 的 `output_contract` 出现
`notes`、`paper_card`、`knowledge_points`、`notes_index` 时使用。

适用：单篇论文、预印本、报告、学位论文章节；输入可以是 Markdown、TXT、
TeX、DOCX 或带文字层的 PDF。不适用：纯翻译（见翻译型 Skill）、
无原文的开放写作、只要求一句摘要的问答。

核心原则：**对得上、留得住、找得回、用得上**。脚本负责切段、章节骨架、
锚点/数字审计、DOCX 导出与 `result.json`；模型负责判断每节写什么、
哪些是知识点、哪段原文是证据。**脚本不会替你补写笔记**；
`work/notes.json` 必须由模型按本文件契约写出。

## 输入

- `/input/case.json`：`case_id`、可选 `title/doi/authors/year`、
  `output_contract`；字段原样保留，不要改写。
- `/input/task.md`：语言、目标读者、必须覆盖的章节/字段、输出格式。
- `/input` 下的论文：`.md/.txt/.tex/.docx/.pdf`。prepare 选择**可读文本最长**
  的文档作为主论文；PDF 由 `scripts/parsers.py` 尝试 PyMuPDF、
  `pdftotext`、`pypdf`，扫描页尝试 OCR，失败会记录 warning，不猜文字。
- 可选：补充材料、图表说明、作者术语表。它们只作参考；笔记锚点必须指向
  真实可读的主论文段落。

运行目录约定：

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 切块、章节分段、引文/数字审计、DOCX 导出、`result.json` 信封 |
| `scripts/parsers.py` | Markdown/TXT/TeX/DOCX/PDF 文本提取与 OCR 兜底 |
| `scripts/fam_notes.py` | 本任务的章节骨架、锚点校验、`notes.md` 与索引生成 |
| `task.json` | 指定 `module=fam_notes`、`profile=reading`、family |
| `templates/notes.schema.json` | `work/notes.json` 字段契约（模型必读） |
| `templates/paper-card.schema.json` | `paper_card` 字段字典与最小示例 |
| `references/notes-rules.md` | 章节对齐、锚点、数字、知识点与失败模式（执行规则） |
| `examples/synthetic-paper.md` | synthetic 论文，仅演示输入形态（非正式案例） |
| `examples/notes.example.json` | 与 synthetic 论文配套的 `notes.json` 示例（非正式答案） |

使用顺序：先读 `templates/notes.schema.json` 确定字段，再按
`references/notes-rules.md` 写内容；`examples/` 只用于核对接口形状，
**不得把示例实体、数字或结论当作正式案例答案**。

## 工作流（prepare → 模型写 notes.json → finalize）

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

在 period4 仓库内本地运行时，入口与 skill 目录分开指定：

```bash
python -X utf8 period4/shared/scripts/run_case.py prepare \
  --input <input> --work <work> --output <output> \
  --skill-dir period4/skills/product-arena-06-6d8cf72b
```

入口会写出：

- `work/inventory.json`、`work/context.json`：输入清单、case、契约；
- `work/outline.json`：按 Markdown/文本标题切出的章节 `S1…` 与段落
  `P001…`；`all_segments[]` 每项含 `id/locator/kind/text`；
- `work/notes-template.json`：与 `notes.json` 同形的空骨架
  （`paper_card`、逐节 `purpose/methods/results/conclusions/anchors`、
  `knowledge_points`、`open_questions`）；
- `work/source.json`：主论文路径、source_id、字符数；
- `output/result.json`：`status=partial` 的 provisional 信封。

若没有可读论文，prepare 直接写 `input_error` 并退出 0。此时不要编造论文；
入口会在 `result.json` 的 `uncertainty` 说明缺什么材料。

### 第 2 步：模型写 `/app/work/notes.json`（约 60% 预算）

1. 打开 `work/outline.json` 与 `work/notes-template.json`；
2. 以 `outline.sections` 为骨架，保留 `id`，**去掉 heading 前的 `#` 标记**
   （finalize 会自行加 `###`）；
3. 逐节填写 `purpose/methods/results/conclusions`，没有对应内容的字段留空串；
4. 为每节至少写 1 条 `anchors`，为每个知识点至少写 1 条 `evidence`；
5. 填写 `paper_card` 的必需字段，写入 `open_questions`（可选）。

完整骨架见 `templates/notes.schema.json`。最小形态：

```json
{
  "case_id": "与输入一致",
  "paper_card": {
    "title": "论文标题（可选，用于 notes.md 一级标题）",
    "research_question": "论文要回答的问题",
    "data": "样本/材料/数据来源",
    "methods": "方法与关键条件",
    "findings": ["带条件的发现 1", "发现 2"],
    "limitations": ["限制 1", "限制 2"],
    "conditions": ["温度/压力/进料比等前提"],
    "doi": "10.xxxx/xxxx",
    "year": "2024",
    "authors": ["A. Author", "B. Author"]
  },
  "sections": [
    {
      "id": "S2",
      "heading": "Abstract",
      "purpose": "本节目的",
      "methods": "本节方法或留空",
      "results": "本节结果",
      "conclusions": "本节结论",
      "anchors": [
        {"path": "paper.md", "locator": "paper.md P004",
         "quote": "逐字来自该段的原文片段"}
      ]
    }
  ],
  "knowledge_points": [
    {"id": "K1", "type": "finding", "statement": "一条可检索的命题",
     "evidence": [{"path": "paper.md", "locator": "paper.md P010",
                   "quote": "逐字原文片段"}],
     "tags": ["selectivity"]}
  ],
  "open_questions": ["论文未回答、值得后续验证的问题"]
}
```

硬规则（详见 `references/notes-rules.md`）：

- **章节对齐**：覆盖 Abstract/Introduction/Methods/Results/Discussion/
  Conclusion 等实际内容章节；不套用与论文无关的通用模板；不把不同章节
  合并成一段通读摘要。References 节可省略；若保留，必须给它一条锚点。
- **`paper_card` 必需字段**：`research_question`、`data`、`methods`、
  `findings`、`limitations` 必须非空（数组至少 1 项）。论文未报告时写
  “Not reported in the paper”并说明，而不是猜。
- **锚点是逐字证据**：`quote` 必须是 `path` 对应段落中的连续原文
  （允许空白/全半角差异；可用 `...` 连接两段各自逐字的片段）。
  `locator` 直接采用 `outline.all_segments[].locator`（形如
  `paper.md P010`）或 `P010`；`path` 用 `outline.source_path`。
- **每节至少有锚点**，每个知识点至少有证据；没有证据的推测写入
  `open_questions`，不要伪装成知识点。
- **数字不臆造**：笔记正文出现的每个数字都必须能在论文中找到；
  finalize 会用 `number_presence_missing` 检查并据此降级。
- **术语稳定**：同一概念全文一种写法；缩写首次出现保留原文展开；
  不把 `may/suggest` 改写成 `prove/show`。
- **不增不删事实**：可以压缩措辞，但不能补论文没有的样本量、年份、
  条件或结论；限制条件必须跟着结论走。
- **列表写法**：`findings`/`limitations` 的数组项不要以句号结尾，
  finalize 会用 `; ` 连接，句号会产生 `.;` 的排版瑕疵。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/notes.json`；缺失或 `sections`/`knowledge_points` 全空时，
   写 `status=partial` 并说明“notes.json missing or empty”，**不生成伪笔记**；
2. 校验 `paper_card` 必需字段；逐节检查锚点、逐知识点检查证据
   （path 必须存在，quote 必须可回查）；
3. 用真实出现的数字做 `number_presence_missing` 审计；
4. 生成 `output/notes.md`（Markdown 主笔记）；
5. 生成 `output/paper-card.json`、`output/knowledge-points.json`、
   `output/notes-index.json`（扁平可检索索引）；
6. 导出 `output/notes.docx` 并重新打开校验；失败只记 `uncertainty`，
   `notes.md` 仍可用；
7. 写 `output/result.json`，`deliverable` 指向 `notes.md`。

`status` 取 reading 枚举 `answered|partial|insufficient_evidence|input_error`：
没有任何 problem 时为 `answered`；缺字段、锚点不可回查或出现来源外数字时
为 `partial`，并在 `uncertainty[]` 给出可修复动作。

## 输出契约

`/app/output` 下必须存在：

| 文件 | 内容 |
|---|---|
| `notes.md` | 主交付物：Paper card + 逐节笔记 + 知识点 + open questions |
| `paper-card.json` | `{case_id, paper_card, source, section_count, knowledge_points}`；这里 `knowledge_points` 是**数量**（整数） |
| `knowledge-points.json` | `{case_id, points[]}`；`points` 为完整知识点数组 |
| `notes-index.json` | `{case_id, entries[]}`；section entry 含 `id/heading/text`，knowledge_point entry 含 `id/type/text/tags` |
| `notes.docx` | 可编辑 Word 版（导出失败时入口会在 uncertainty 说明） |
| `result.json` | 官方信封 |

`result.json` 关键字段：

```json
{
  "case_id": "与输入一致",
  "status": "answered",
  "task_family": "structured-notes",
  "profile": "reading",
  "deliverable": {"path": "notes.md", "format": "md", "exists": true},
  "result": {
    "summary": "Structured notes with 4 sections and 3 knowledge points.",
    "paper_card": {},
    "checks": {"problems": [], "numbers": {"ok": true, "invented": []}}
  },
  "claims": [{"claim_id": "K1", "text": "...", "evidence": []}],
  "evidence_map": [{"claim_id": "S2", "claim": "Abstract",
                    "source": {"path": "paper.md", "locator": "paper.md P004"},
                    "quote": "...", "supports": "section-note"}],
  "preserved_facts": [{"kind": "number", "source": "92%", "target": "92%",
                       "locator": "paper.md", "value": "92%", "unit": ""}],
  "uncertainty": [],
  "change_log": [{"step": 1, "action": "prepare", "scope": "input",
                  "reason": "paper outlined; write notes.json, then run finalize"}],
  "artifacts": [{"path": "notes.md", "role": "deliverable", "exists": true}],
  "output_contract": {},
  "contract_coverage": [],
  "artifact_paths_valid": true
}
```

`deliverable.path` 与 `artifacts[].path` 都是相对 `/app/output` 的真实路径；
`artifacts[].exists` 由入口按文件系统写成，不要手工伪造。
`preserved_facts` 由入口从论文中抽取数字 token（最多 150 个）生成，
用于证明数值没有被篡改；`evidence_map` 汇总每节锚点与每个知识点的证据。

## 质量闸门

| # | 检查 | 通过标准 | 由谁判定 |
|---|---|---|---|
| G1 | 章节对齐 | notes.json 的 section id/heading 与 outline 内容章节对应，顺序一致 | 模型自检（入口不补章节） |
| G2 | 卡片完整 | `research_question/data/methods/findings/limitations` 非空 | finalize 判 problem |
| G3 | 锚点回查 | 每节 ≥1 anchor；path 存在；quote 逐字可回查 | finalize 判 problem |
| G4 | 知识点证据 | 每个 point ≥1 evidence，且 quote 可回查 | finalize 判 problem |
| G5 | 数字可追溯 | `result.checks.numbers.ok=true`，无来源外数字 | finalize 判 problem |
| G6 | 输出可用 | notes.md 非空；三个 JSON 可解析；索引条目数与 sections+points 一致 | 入口生成 + 自检 |
| G7 | DOCX | 能重新打开；失败时 uncertainty 说明且 notes.md 可替代 | 入口启发式 |
| G8 | 契约 | result.json 可解析，status 属于 reading 枚举，artifact 路径真实 | 入口生成 |

注意：G1 是模型自检项。finalize 只遍历你写入的 section，**不会**根据
outline 自动补缺失章节；漏章节不会自动产生 problem，但会直接损失任务
完成度。提交前用 G1 自查。

## 异常处理

| 情况 | 处理 |
|---|---|
| 无文本层/无主论文 | prepare 写 `input_error`；不生成伪笔记，说明缺少的材料 |
| PDF 双栏错序/公式成图 | 以文本块为锚点，图表内容用 `open_questions` 或 limitation 标注；不猜数值 |
| `notes.json` 缺失/为空 | finalize 返回 `partial`，不生成任何交付物；按 schema 重写后重跑 |
| 锚点 quote 与原文不符 | 该条计入 problem → `partial`；从 `outline` 段落复制逐字片段 |
| 锚点 path 不存在 | 使用 `outline.source_path`；补充材料不能替代主论文锚点 |
| 笔记出现来源外数字 | 恢复原文数值/范围；换算值只允许等值展开并保留原文；否则删除该数字 |
| 论文未报告某必需字段 | 填 “Not reported in the paper”，并在 `open_questions` 或该字段内注明未报告；不要写猜测值 |
| DOCX 导出失败 | 保留 notes.md；`uncertainty` 会有 `docx_export`/`docx_reopen` 记录 |
| 时间不足 | 先写 paper card + 已读章节锚点并跑 finalize；诚实保留 `partial` |
| 入口脚本异常 | 读 `work/main-error.json`；已写 notes.json 不丢，修复后重跑 finalize |

## 最小示例（synthetic，不是正式答案）

synthetic 论文片段：

```markdown
## 2 Methods
Samples were annealed at 450 °C for 3 h.
## 3 Results
The doped sample reached 85% conversion after 2 h.
```

对应 `work/notes.json` 片段（S/P 编号以实际 `outline.json` 为准）：

```json
{
  "case_id": "SYNTH-NOTES-EXAMPLE",
  "paper_card": {
    "research_question": "Does doping raise conversion?",
    "data": "Two synthetic samples",
    "methods": "Annealing at 450 °C for 3 h",
    "findings": ["The doped sample reached 85% conversion after 2 h"],
    "limitations": ["Single synthetic batch"]
  },
  "sections": [
    {"id": "S1", "heading": "2 Methods", "methods": "Annealing at 450 °C for 3 h",
     "anchors": [{"path": "paper.md", "locator": "paper.md P002",
                  "quote": "Samples were annealed at 450 °C for 3 h."}]},
    {"id": "S2", "heading": "3 Results", "results": "The doped sample reached 85% conversion",
     "anchors": [{"path": "paper.md", "locator": "paper.md P004",
                  "quote": "The doped sample reached 85% conversion after 2 h."}]}
  ],
  "knowledge_points": [
    {"id": "K1", "type": "finding",
     "statement": "The doped sample reached 85% conversion after 2 h",
     "evidence": [{"path": "paper.md", "locator": "paper.md P004",
                   "quote": "The doped sample reached 85% conversion after 2 h."}],
     "tags": ["conversion"]}
  ]
}
```

把该片段补全为完整 `notes.json` 并跑 finalize 后，正常应得到
`status=answered`、`deliverable.path=notes.md`，且
`notes.md`、`paper-card.json`、`knowledge-points.json`、`notes-index.json`
存在、`checks.numbers.ok=true`；若 quote 或数字与原文不符，则应为
`partial`。该示例只演示接口，**不是正式案例答案**。

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_notes` 模块；prepare 写
  provisional result，官方字段由入口按真实文件生成。
- 章节切分基于 Markdown 标题与空行；纯文本/PDF 的“裸标题”可能被并入正文，
  此时仍以 `Pxxx` 为锚点，并在 `open_questions` 或 notes 正文中注明章节
  边界靠人工判断。
- 锚点只机器校验 `path` 与 `quote`；`locator` 的可信度依赖模型按 outline
  填写，复核时应同时核对 locator 与 quote。
- 数字审计是启发式集合比较（按数值，忽略格式/正负号差异），不能替代
  人工语义核对；公式编号、引用编号也可能被当作数字，需人工确认。
- 不联网、不检索外部元数据；DOI/年份只使用输入材料中真实出现的信息。
- 示例是 synthetic 接口样例，不是正式案例答案；不要复制其中实体或数字。
