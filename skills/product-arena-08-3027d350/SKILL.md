---
name: layout-faithful-pdf-parser
description: 文献文档解析：把真实 PDF 解析成可复核的结构化文档，修正双栏阅读顺序，拆分正文/题注/表格/公式，保留页码与区块 locator，表格行列与公式原文不失真，扫描页 OCR 失败如实标注。Layout-faithful PDF parsing with reading-order repair, table reconstruction, formula preservation, page/block anchors and honest OCR limits. Use for PDF 解析、双栏阅读顺序、表格解析、章节结构、公式与特殊字符、扫描件 OCR、页码定位.
---

# 文献文档解析（Layout-Faithful PDF Parser）

## 用途与触发

task.md 要求“解析 PDF”“提取正文/章节/表格/公式/参考文献”“修正双栏阅读
顺序”“保留页码定位”“处理扫描件/OCR”，或 `/input` 提供 `.pdf` 时使用。
本任务按结构元素识别图表与公式，不要求理解其科学含义。

成功标准不是输出一段“看起来像全文”的文字，而是：**提得全、排得对、
拆得清、核得回**。脚本负责版面提取、初稿结构、表格/公式候选、导出与审计；
模型负责对照真实页面修正初稿，尤其是双栏顺序、伪表格、区块拆分与 OCR 边界。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：版式特点、目标结构、是否要求表格/公式/参考文献。
- `/input` 下的 PDF：真实 PDF，没有预抽取答案层。入口依次尝试
  PyMuPDF、`pdftotext -raw`、`pdftotext -layout`、pypdf；无文字层的页面
  尝试 `pdftoppm` + `tesseract`，失败会在 `warnings`/`ocr_error` 记录。
- 可选：同目录的 `.md/.txt` 仅作辅助核对，不得替代 PDF 的页面结构。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 文件盘点、段落切分、result.json 信封 |
| `scripts/parsers.py` | PDF 文本/版面/页块提取、双栏排序、DOCX/TeX 提取 |
| `scripts/fam_parse.py` | 初稿结构、章节/表格/公式/图表/参考文献识别、导出与校验 |
| `task.json` | family、module、profile 与输出契约 |
| `templates/document.schema.json` | `work/document.json` 的字段契约 |
| `templates/parse-report.schema.json` | `report` 覆盖统计字段 |
| `references/parse-rules.md` | 阅读顺序、区块定位、表格、公式、OCR 的可执行规则 |
| `examples/synthetic-page.txt` | synthetic 双栏页面示意（非正式案例） |
| `examples/document.example.json` | 对应 synthetic 解析结果（非正式答案） |

## 工作流（先读版面，再改初稿，最后导出审计）

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，写 `work/inventory.json`、`work/context.json`；
2. 选主 PDF，按页提取 `text`、`layout_text`、`blocks[]`（含 `page`、
   `line_start`、`line_end`、`bbox`、`kind`）；
3. 对每页执行双栏排序 `order_blocks()`，再生成初稿：
   - `work/document-draft.json`：`{"document": {...}, "report": {...}}`；
   - `work/document-template.json`：字段骨架；
   - `work/document-outline.md`：章节层级预览；
4. 写 `status=partial` 的 provisional `result.json`。

**初稿是启发式结果，必须复核**。常见缺陷：

| 初稿现象 | 原因 | 处理 |
|---|---|---|
| 双栏正文被识别成 `tables` | 文本里有两列、列间多空格 | 删除伪表，按正文段落还原 |
| 一个 block 同时含表题、表体、公式、图题 | PDF 把相邻对象放在同一文本块 | 拆成 `tables`/`equations`/`figures` 与正文 |
| 段落按换行切得太碎 | 原始文本没有空行 | 按句意与缩进合并同段，保留 locator |
| 图题只匹配到正文里的 “Fig. 1 shows ...” | 正则只看行首 | 用真实图题（“Fig. 1. ...”）替换 |
| 页眉/页脚混入正文 | 版面块分类误差 | 移到 `headers_footers`，正文删除 |
| `warnings` 含 OCR/解析失败 | 扫描页或工具缺失 | 保留失败页码，不补造文字 |

改稿前先把逐页原文落到工作目录，便于逐段对照：

```bash
python3 - <<'PY'
import glob, json, os, sys
sys.path.insert(0, os.path.join(os.environ["SKILL_DIR"], "scripts"))
import parsers
pdf = sorted(glob.glob("/input/*.pdf"))[0]
doc = parsers.extract_pdf(pdf)
pages = [{"page": p.get("page"), "text": p.get("text", ""),
          "layout_text": p.get("layout_text", ""),
          "readable": p.get("readable"), "ocr": p.get("ocr", False),
          "ocr_error": p.get("ocr_error", "")}
         for p in doc.get("pages", [])]
with open("/app/work/pages.json", "w", encoding="utf-8") as fh:
    json.dump(pages, fh, ensure_ascii=False, indent=2)
print("wrote /app/work/pages.json", len(pages))
PY
```

`pages.json` 的 `text` 用于核对正文顺序，`layout_text` 用于核对列位与表格；
两者不一致时以版面为准重排，并在 `warnings` 说明人工修正。

### 第 2 步：模型写 `/app/work/document.json`（约 60% 预算）

以 `document-draft.json` 为底稿复制并修正，保持顶层结构：

```json
{
  "case_id": "与输入一致",
  "document": {
    "source_path": "paper.pdf",
    "title": "逐字标题",
    "authors": ["作者一", "作者二"],
    "abstract": "摘要原文",
    "page_count": 2,
    "readable_pages": 2,
    "ocr_pages": [],
    "empty_pages": [],
    "sections": [
      {"id": "S001", "heading": "1 Introduction", "level": 1,
       "paragraphs": [
         {"id": "P001", "text": "逐字正文（可含换行合并后的句子）",
          "page": 1, "block_id": 9, "bbox": [72.0, 210.0, 300.0, 420.0],
          "locator": "p1 b9", "kind": "body"}
       ]}
    ],
    "tables": [
      {"id": "T001", "page": 2,
       "caption": "Table 1. Catalytic performance at 350 C and 1 bar.",
       "columns": ["Catalyst", "Conversion (%)", "Selectivity (%)"],
       "rows": [["CeO2", "12", "78"], ["CeO2-x", "18", "92"]],
       "source_locator": "p2 b1", "detection": "manual-review"}
    ],
    "figures": [
      {"id": "F001", "label": "Fig. 1",
       "caption": "Fig. 1. Methanol selectivity versus reaction temperature ...",
       "page": 2, "locator": "p2 b1"}
    ],
    "equations": [
      {"id": "E001", "page": 2, "raw_text": "CO2 + 3 H2 -> CH3OH + H2O (1)",
       "latex": null, "number": "1", "locator": "p2 b1"}
    ],
    "references": [
      {"id": "R001", "raw": "[1] 作者. 题名. 期刊. 年;卷:页. doi:...",
       "doi": "10.xxxx/...", "year": "2020", "title": "", "authors": []}
    ],
    "headers_footers": [],
    "engine": "pdftotext-raw",
    "warnings": []
  },
  "report": {
    "case_id": "与输入一致", "source_path": "paper.pdf",
    "page_count": 2, "readable_pages": 2,
    "ocr_pages": [], "empty_pages": [],
    "sections": 6, "paragraphs": 17,
    "tables": 1, "figures": 1, "equations": 1, "references": 2,
    "warnings": []
  }
}
```

硬规则（详见 `references/parse-rules.md`）：

1. **文字逐字**：正文、题注、脚注、参考文献按原文提取；合并被换行切断的
   句子时只处理空白与连字符，不改词、不补句、不做同义替换。
2. **阅读顺序**：双栏页按“左栏自上而下 → 右栏自上而下”，跨栏/通栏标题
   与图题放在其版面位置；不按 y 坐标逐行交错读取。
3. **定位到页与区块**：每个段落写 `page` + `block_id` + `locator`
   （`p<page> b<block>`）；表格/图/公式写 `source_locator`/`locator`。
   不把自建序号当原 PDF 页，不写不存在的页码。
4. **结构拆分**：标题、作者、摘要、章节层级、正文、表格、图题、公式、
   参考文献、页眉页脚分别落到对应字段；同一文本块里的多种对象必须拆开。
5. **表格行列**：`columns` 是表头，`rows` 是数据行；单元格按列对齐，
   空单元格保留 `""`；合并单元格用重复值或空格并在 `caption`/`detection`
   说明；不要用双栏正文冒充表格。
6. **公式原文**：`raw_text` 逐字保留 PDF 抽出的线性化写法（如
   `CO2 + 3 H2 -> CH3OH + H2O (1)`）；`number` 取公式编号；
   无法可靠还原 LaTeX 时 `latex: null`，不得凭科学常识重写公式。
7. **扫描页与 OCR**：OCR 成功的页码进 `ocr_pages`；失败的页保留在
   `empty_pages`，把工具错误写入 `warnings`，正文留空，绝不猜补。
8. **不编造**：图片像素、补充附件、底层实验数据不在本包内；未报告项写
   `warnings`/`report`，不要从标题或常识补数据。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 优先读取 `work/document.json`；不存在时回退 `work/document-draft.json`；
2. 校验：`sections` 非空、`title` 非空、段落有 `text` 与 `locator`、
   表格有 `columns`/`rows`；`readable_pages < page_count` 时记为不可读页问题；
   若 `task.md` 明确要求公式/表格/参考文献而对应数组为空，也记为问题
   （入口不会代你补造，缺失就保持缺失）；
3. 写 `output/document.json`、`output/document.md`；
4. 每张表写 `output/tables/<id>.json` 与 `output/tables/<id>.md`；
5. 写 `output/parse-report.json`（取模型提供的 `report`）、
   `output/README.md`（范围与限制摘要）、`output/characterization.json`
   （文档级统计）、`output/arm_manifest.json`（产物 sha256 清单）；
6. 写 `output/result.json`。

README/characterization/manifest 都由入口根据真实解析结果生成，
模型不需要手写，也不能用它们替代 `document.json`。

`status` 取 `answered|partial|insufficient_evidence|input_error`：
全部校验通过 → `answered`；有不可读页、空段落、缺 locator、空表 →
`partial`，逐条写入 `uncertainties`；没有可读文档 → `input_error`。

## 输出契约

最终产物（全部位于 `/app/output`）：

| 文件 | 内容 |
|---|---|
| `document.json` | `{"case_id", "document", "report"}` 完整结构化文档 |
| `document.md` | 标题、作者、摘要、章节正文、表格、公式、图题、参考文献 |
| `tables/<id>.json` | 每张表的 `id/page/caption/columns/rows/source_locator` |
| `tables/<id>.md` | 每张表的 Markdown 表格 |
| `parse-report.json` | 页数、可读页、OCR 页、空页、章节/段落/表/图/公式/文献计数与 warnings |
| `README.md` | 解析摘要、页/结构计数与限制说明 |
| `characterization.json` | 文档级统计：标题、作者、页数、章节标题、各类计数、engine |
| `arm_manifest.json` | 主要产物的路径、字节数与 sha256 清单 |
| `result.json` | 状态、结果摘要、claims、uncertainties 与 artifact 路径 |

`result.json` 关键字段（由入口生成，不要手工伪造）：

```json
{
  "case_id": "与输入一致",
  "status": "answered",
  "result": {
    "summary": "Parsed 2 pages: 6 sections, 1 tables, 1 equations, 2 references.",
    "title": "逐字标题",
    "report": {"page_count": 2, "readable_pages": 2, "tables": 1},
    "problems": []
  },
  "claims": [
    {"claim_id": "structure", "text": "Document has 6 sections and 17 paragraphs"},
    {"claim_id": "tables", "text": "1 tables, 1 figures, 1 equations, 2 references"}
  ],
  "uncertainties": [],
  "deliverable": {"path": "document.json", "format": "json", "exists": true},
  "preserved_facts": [{"kind": "page", "source": "1", "target": "document.json",
                       "locator": "p1", "value": "1", "unit": "page"}],
  "evidence_map": [{"claim_id": "P001", "claim": "...",
                    "source": {"path": "paper.pdf", "locator": "p1 b9"},
                    "quote": "...", "supports": "verbatim"}],
  "change_log": [{"step": 1, "action": "parse", "reason": "..."}],
  "artifacts": [{"path": "document.json", "role": "deliverable", "exists": true}],
  "output_contract": {},
  "contract_coverage": [],
  "artifact_paths_valid": true
}
```

`deliverable.path` 固定为 `document.json`；`artifacts[]` 中每个路径必须真实存在。
必须保留 `report`：若 `document.json` 只有 `document`，`parse-report.json`
会变成空对象，审计信息丢失。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 文字保真 | 正文/题注/参考逐字一致；换行合并只改空白；无页眉页脚混入 |
| G2 | 阅读顺序 | 双栏按左栏→右栏；无跨栏拼句；跨栏/通栏元素在正确位置 |
| G3 | 区块定位 | 每段有 `page`+`block_id`+`locator`；表/图/公式有 locator；无编造页码 |
| G4 | 结构完整 | 标题、作者、摘要、章节层级、正文、图表题注、公式、参考文献、页眉页脚齐全 |
| G5 | 表格结构 | 表头/数据行对齐；单元格归属正确；伪表格已删除；续表有说明 |
| G6 | 公式保真 | `raw_text` 与 PDF 一致；编号正确；`latex` 不可靠时为 `null` |
| G7 | OCR 边界 | `readable_pages/ocr_pages/empty_pages` 与真实页一致；失败页不补造 |
| G8 | 结果信封 | `result.json` 可解析，status 合理，artifact 路径真实，`output_contract` 保留 |

## 异常处理

| 情况 | 处理 |
|---|---|
| 没有可读 PDF/文本 | prepare 返回 `input_error`；不生成伪文档 |
| PDF 加密/损坏 | 记录 warning；能读几页就解析几页，`status=partial` |
| 无文字层扫描页 | 尝试 OCR；成功页进 `ocr_pages` 并标注识别风险；失败页进 `empty_pages`+`warnings` |
| 双栏串栏/错行 | 用 `layout_text` 与 bbox 重新判定左/右栏；必要时手工排序并在 `kind`/`warnings` 说明 |
| 两栏正文被误判为表格 | 删除该 `tables[]` 条目，还原为段落 |
| 表题/表体/公式/图题同块 | 拆成独立对象，定位到同一 `block`，不要复制到多处 |
| 跨页续表 | 两张表分别保留 `page`，在 `caption`/`detection` 标 `continued`；不静默合并 |
| 合并单元格/多级表头 | 用重复值或空串保持列对齐，并在 `detection` 说明规则 |
| 公式是图片/无法还原 | `raw_text` 留可读占位或空串，`latex: null`，在 `warnings` 说明 |
| 题目要求公式/表格/文献但原文确实没有 | 保留空数组并在 `warnings` 说明；finalize 会按 task 校验标 `partial`，不得补造 |
| 参考文献条目跨行 | 同一条目内合并为一整条 `raw`，保留原始编号与 DOI |
| 时间不足 | 先保存已核对页的 `document.json` 并 finalize；`status=partial` |
| 入口异常 | 读 `work/main-error.json`；保留已写 `document.json`，修复后重跑 finalize |

## 最小示例（synthetic，不是正式答案）

synthetic 双栏页面（`examples/synthetic-page.txt`）左栏正文：

```text
The coated sample delivered 140 mAh g-1 at 1 C.
The uncoated sample delivered 110 mAh g-1 under the same conditions.
```

右栏：

```text
Table 2. Capacity at 1 C.
Sample        Capacity (mAh g-1)
Coated        140
Uncoated      110
E = E0 - (RT/nF) ln Q (2)
```

正确解析片段（只演示字段，不是正式答案）：

```json
{"sections": [{"id": "S001", "heading": "3 Results", "level": 1,
  "paragraphs": [
    {"id": "P001", "text": "The coated sample delivered 140 mAh g-1 at 1 C. The uncoated sample delivered 110 mAh g-1 under the same conditions.",
     "page": 1, "block_id": 3, "locator": "p1 b3", "kind": "body"}]}],
 "tables": [{"id": "T001", "page": 1, "caption": "Table 2. Capacity at 1 C.",
   "columns": ["Sample", "Capacity (mAh g-1)"],
   "rows": [["Coated", "140"], ["Uncoated", "110"]],
   "source_locator": "p1 b4", "detection": "manual-review"}],
 "equations": [{"id": "E001", "page": 1,
   "raw_text": "E = E0 - (RT/nF) ln Q (2)", "latex": null,
   "number": "2", "locator": "p1 b5"}]}
```

注意：`E0` 在 PDF 文本层可能是线性化写法；`raw_text` 必须与抽取结果一致，
不要凭公式常识补成上标结构。`finalize` 后应能核对
`document.json`、`document.md`、`tables/T001.json`、`tables/T001.md`、
`parse-report.json`、`README.md`、`characterization.json`、
`arm_manifest.json` 均存在，且 `result.json.status` 与 `problems` 一致。

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_parse.py`；prepare 生成初稿，
  finalize 校验并导出 Markdown/表格/审计。
- 解析依赖运行时可用工具（PyMuPDF/pdftotext/pypdf/tesseract）；
  某工具缺失会写入 `warnings`，不影响已可提取的部分。
- 公式仅保留文本与编号，不保证还原为 LaTeX；图表不要求理解科学含义。
- 本 skill 不补造图片像素、补充附件或未报告数据；扫描页失败如实标注。
- 示例是 synthetic 接口样例，不是正式案例答案。
