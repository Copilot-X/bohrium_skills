---
name: claim-bound-introduction-writer
description: 引言与相关工作写作：以真实稿件和文献库为证据底稿，按比较维度分组相关工作，从已引文献的局限推导研究空白，把贡献逐条绑定到章节与证据，并交付带引用审计、证据映射和 DOCX/PDF 的可编辑章节。Claim-bound introduction and related-work writing with grouped comparisons, gap derivation, contribution-evidence links and citation audits. Use for 引言写作、相关工作、研究空白、贡献表述、引用覆盖、论文开头.
---

# 引言与相关工作写作（Claim-Bound Introduction Writer）

## 用途与触发

task.md 要求“写引言”“整理相关工作”“从文献缺口写研究空白”
“凝练贡献”“检查引言引用覆盖”，或 `/input/case.json` 给出
`introduction` / `related_work` / `gap` / `contributions` 等字段时使用。

不适用：没有稿件或没有文献库的开放式综述；要求联网检索新文献且输入
未提供条目；要求整篇论文写作。输入缺文献库时仍可写基于稿件的章节，
但引用键无法核验，`status` 必须保持 `partial` 并写明原因。

核心原则：**立得住、分得清、说得实、对得上**。
脚本负责切段、索引文献、统计引用 token、核对数字/键/分组/空白/贡献并
导出 result.json；模型负责判断论证结构与每一句的引用归属。
脚本不会代写章节，也不会生成抽取式保底正文。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；原样保留到 result.json。
- `/input/task.md`：学科、文体、目标读者、期刊/引用格式、篇幅、语言。
- 稿件：`.md/.txt/.tex/.docx/.pdf`。PDF 由 `scripts/parsers.py` 依次尝试
  PyMuPDF、`pdftotext -raw`、`pypdf`，扫描页尝试 OCR；失败如实记录。
- 文献库：`.bib/.ris/.json`（JSON 为条目数组）。键名区分大小写。
- 可选：`work/writing-plan.json`（重跑时复用）、期刊模板、作者术语表。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 切段、引用 token、数字保真、DOCX/PDF 导出、result.json 信封 |
| `scripts/parsers.py` | PDF/DOCX/TeX/BibTeX/RIS/JSON/CSV 提取 |
| `scripts/fam_intro.py` | 本任务的证据索引、计划字段、引用与论证审计 |
| `task.json` | family=`introduction-writing`、module=`fam_intro`、profile=`writing` |
| `templates/writing-plan.schema.json` | `work/writing-plan.json` 字段契约 |
| `templates/result.schema.json` | `output/result.json` 官方字段与 artifact 契约 |
| `references/introduction-rules.md` | 背景、分组、空白、贡献、引用与失败模式规则 |
| `examples/synthetic-manuscript.md` | synthetic 稿件（不是正式案例） |
| `examples/synthetic-writing-plan.json` | synthetic 计划（不是正式答案） |
| `examples/synthetic-sections.md` | synthetic 成稿（不是正式答案） |

## 工作流（先建证据索引，再写章节，最后审计）

### 第 1 步：prepare（约 15% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点输入并写 `work/inventory.json`、`work/context.json`；
2. 选文本最长的主稿件，按标题/段落切分为 `P001…`，写入
   `work/evidence.json`：`segments[]`（`segment_id/path/locator/quote/text/kind`）、
   `citation_tokens[]`、`bibliography[]`（`key/title/year/authors/doi`）；
3. 写 `work/bibliography.json`（`entries[]`）、
   `work/writing-plan-template.json`、`work/outline.md`；
4. 写 `output/result.json`，`status=partial`，等待模型写章节。

主稿件不可读时入口写 `status=input_error` 并退出 0。不要凭题面标签补造
稿件内容。

### 第 2 步：模型写三个文件（约 55% 预算）

1. `/app/output/introduction.md`：完整引言，**必须非空**（首选）；
2. `/app/output/related-work.md`：完整相关工作，**必须非空**（首选）；
3. `/app/work/writing-plan.json`：论证计划与字段契约（见下）。

单文件兜底：题目只允许一个文件时，可只写 `/app/output/sections.md`，
但必须含一级标题 `# Introduction` 与 `# Related Work`（或中文
“引言”“相关工作”）。**切分只取标题到下一个标题之间的内容**：
Related Work 内一旦使用 `##` 子标题，后续段落会被截断，导致引用缺失并
使 `status=partial`。因此有分组子标题时必须用双文件；单文件模式要把
Related Work 写成连续段落。推荐双文件，且不要同时写两套内容。

`writing-plan.json` 字段（与 `fam_intro.py` 实际读取一致）：

```json
{
  "case_id": "与输入一致",
  "background": {
    "claims": ["背景论断，逐条对应稿件证据"],
    "sources": ["manuscript.md P003"]
  },
  "related_work_groups": [
    {
      "group": "路线或问题组名",
      "dimension": "problem|method|data|metric",
      "works": [
        {"citation_key": "smith2020", "summary": "该工作的结论与适用条件",
         "limitation": "该工作在同一维度上的局限"}
      ],
      "synthesis": "本组在同一维度上的取舍与共同局限"
    }
  ],
  "gap": {
    "statement": "由已引工作局限推出的空白",
    "derived_from": ["smith2020", "wang2021"],
    "evidence": ["manuscript.md P003", "smith2020 的局限"]
  },
  "contributions": [
    {"id": "C1", "statement": "可核对的贡献",
     "linked_sections": ["Introduction", "Results §2"],
     "evidence": ["manuscript.md P006：92% vs 78%"]}
  ],
  "citation_coverage": [
    {"key": "smith2020", "used_in": "Introduction|Related Work",
     "claim": "该引用具体支撑哪条论断"}
  ]
}
```

硬规则（详见 `references/introduction-rules.md`）：

1. **引用必须可绑定**：正文用 Pandoc 键 `[@smith2020]` 或 TeX
   `\cite{smith2020}`；同一处多篇写 `[@smith2020][@wang2021]` 或
   `\cite{smith2020,wang2021}`。**每个 `[@…]` 只放一个键**，
   不要写 `[@a; @b]`（会被解析成 `@b` 而误报未知键）。
2. **数字引用 `[n]`** 只按“去重后的文献库顺序”从 1 编号；`[1,2]` 合法。
   不要在正文手写会与文献库错位的编号。数字引用本身也计入正文数字，
   稿件里没有该数字时优先用 `[@key]`；文献键不要以数字开头。
3. **作者—年份不是可核验绑定**：`(Smith et al., 2020)` 会被识别为引用
   token，但不带 key，无法证明 `gap.derived_from` 被引用。目标期刊要求
   author–year 时，正文仍保留 `[@key]` 机器标记，或同时写
   `citation_coverage`，最终样式转换交由引用管理工具完成。
4. **背景**：第一段回答“研究对象—具体瓶颈—为什么现在做—约束”，
   禁止“近年来受到广泛关注”“具有重要意义”式空转；每条背景断言
   在 `background.sources` 给稿件 locator。
5. **相关工作分组**：2–4 组，每组至少 2 篇，用同一 `dimension` 比较；
   每篇写结论、适用条件和局限，禁止逐篇摘要拼接；每组必须有
   `synthesis`。少于 2 篇的组会被审计记为 `group_issues`。
6. **研究空白**：`statement` 非空，`derived_from` 至少 2 个真实键，
   且这些键必须在两个章节中出现（任意可绑定引用形式）；
   空白只能由已引工作的共同局限、未覆盖场景或冲突结论推出。
7. **贡献**：1–4 条；每条 `linked_sections` 或 `evidence` 至少一个非空，
   否则计为 `weak_contributions` 并使 `status=partial`；
   贡献不得超出稿件中的方法、图表与结果。
8. **不新增数字**：引言/相关工作里出现的任何数值（含年份、百分比、
   样本量）必须在主稿件文本中出现过；否则 `numbers.ok=false`。
   不要把文献年份直接写进正文（除非稿件里本来就有该数字）。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取两个章节；`related-work.md` 缺失时尝试从 `sections.md` 的
   `# Related Work` 标题处切分（单文件兜底，不推荐依赖）；
2. 对文献库按 DOI / 规范化标题+年份去重，建立数字引用到键的映射；
3. 统计引用 token、未知键、缺失编号、正文新增数字、分组、空白和贡献；
4. 写 `output/citation-audit.json`：`used_keys/known_keys/unknown_keys/
   numeric_refs/missing_numeric/numbers/groups/group_issues/
   contributions/weak_contributions/gap_issues`；
5. 写 `output/contributions.json`、`output/evidence-map.json`
   （含完整 plan、`citation_coverage`、已用文献条目）；
6. 重写 `output/introduction.md`、`output/related-work.md`，合并为
   `output/sections.md`；
7. 导出 `output/sections.docx`（真实 OOXML，可重新打开）与
   `output/sections-preview.pdf`（文本内容预览，不冒充 Word 排版渲染）；
8. 写 `output/result.json`（writing 官方四字段由入口生成）。

`status` 取 `completed|partial|input_error`：存在未知键、缺失编号、
新增数字、所有分组都少于 2 篇、gap 问题、弱贡献或任一章节为空时
只能是 `partial`。DOCX/PDF 导出失败只记 `uncertainty`，不改变主稿可用性。

## 输出契约

| 产物 | 说明 |
|---|---|
| `introduction.md` | 引言，非空 |
| `related-work.md` | 相关工作，非空 |
| `sections.md` | finalize 合并的可编辑主稿，`deliverable` 指向它 |
| `citation-audit.json` | 引用键/编号/数字/分组/空白/贡献审计 |
| `contributions.json` | 贡献列表与 `weak` 标记 |
| `evidence-map.json` | plan、citation_coverage 与已用文献条目 |
| `sections.docx` | 可编辑 Word；导出失败时不登记 artifact |
| `sections-preview.pdf` | 文本内容预览；生成失败时记 uncertainty |
| `result.json` | `case_id/status/deliverable/preserved_facts/evidence_map/change_log/artifacts/result/output_contract/contract_coverage` |

`result.deliverable.path` 必须是真实存在的 `sections.md`；
`artifacts[]` 中每个 `exists=true` 的路径都真实可打开。
`result.result.audit` 与 `citation-audit.json` 同源，不得手工改写。

`evidence_map[]` 的引用项绑定文献记录（`source.path` 指向 `refs.bib`
或含该条目的稿件 References，`quote` 为条目/标题原文）；贡献项绑定
稿件段落（`quote` 逐字来自 `path + locator`）。finalize 会逐条校验
`path + quote`，空 quote 或找不到原文时降级为 `partial` 并写入
`uncertainties`，不会用“大意”冒充证据。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 章节完整 | `introduction.md` 与 `related-work.md` 均非空，四要素齐全 |
| G2 | 引用键 | `unknown_keys=[]`，每个键都在 `bibliography.json` 中 |
| G3 | 编号 | `missing_numeric=[]`，数字引用与去重后条目一致 |
| G4 | 数字 | `numbers.ok=true`，正文数值都能在主稿件中找到 |
| G5 | 分组 | 2–4 组，每组 ≥2 篇，同组同一比较维度，有 synthesis |
| G6 | 空白 | `gap.statement` 非空，`derived_from` 全部在正文被引用 |
| G7 | 贡献 | 每条贡献有 `linked_sections` 或 `evidence`，无夸大 |
| G8 | 契约 | result.json 可解析，artifact 路径真实；DOCX 可重开，PDF 存在或已说明 |

G5 的“2–4 组”是编辑闸门：脚本对空组不报 critical，但空组或单篇组
不满足本题“分得清”的要求，必须补组后再交付。

## 异常处理

| 情况 | 处理 |
|---|---|
| 稿件为空/不可读 | `input_error`；不生成伪引言 |
| 文献库为空 | 不写 `[@key]`；引用只能来自稿件已有 `[n]`；`gap.derived_from` 留空会记 gap_issues，`status=partial` |
| 引用键不存在 | 换成真实键或删除该引用；不得编造条目 |
| 文献库有重复条目 | 交给入口按 DOI/标题+年份去重；数字编号按去重后顺序 |
| 目标期刊 author–year | 保留 `[@key]` 机器标记；样式转换不在本 Skill 范围 |
| 材料没有某项数值 | 不写该数值；需要时写入 plan 的说明，不要放进正文 |
| 主稿件只有摘要/片段 | 只写材料支持的背景；未覆盖部分不写进正文，候选缺口写入 plan 的可选 `unresolved` |
| 无法判断空白归属 | 不硬写空白；把候选矛盾写进 `citation_coverage` 的 claim 并标 `partial` |
| DOCX/PDF 导出失败 | 保留 md 主稿；uncertainty 记录原因，交付不受阻 |
| 时间不足 | 先写 `introduction.md`、`related-work.md` 与最小 plan，再补分组与覆盖 |

## 最小示例（synthetic，不是正式答案）

稿件片段：`In our tests, NiCo-200 reached 85% CH4 selectivity at 250 °C.`

```json
{"group": "Defect engineering", "dimension": "method",
 "works": [
   {"citation_key": "chen2019", "summary": "缺陷位点提高低温活性",
    "limitation": "未固定空速，无法分离温度效应"},
   {"citation_key": "patel2021", "summary": "载量调控改变选择性",
    "limitation": "只报告转化率，未报告选择性"}],
 "synthesis": "两条路线都缺少同一条件下的选择性对照。"}
```

正文写法：`... at 250 °C [@chen2019][@patel2021].`
`finalize` 后应满足：`used_keys=["chen2019","patel2021"]`、
`unknown_keys=[]`、`numbers.ok=true`、`status=completed`（当
gap/contributions 也齐全时）。

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_intro` 模块；
  prepare 写 provisional result，官方 writing 四字段由入口从真实文件生成。
- 引用审计基于输入文献库；不联网检索、不补全缺失元数据。
- `numbers` 检查是数值集合比对，不判断单位语义；正文数字必须来自稿件。
- 作者—年份引用只能弱核验；跨键的语义级“引用是否真的支持该句”仍需作者复核。
- PDF 预览依赖运行时 matplotlib；缺失时记 `pdf_preview` uncertainty，
  `sections.md` 与 DOCX 不受影响。
- 示例全部为 synthetic 接口样例，不是正式案例答案；不要复制其中的实体或结论。
