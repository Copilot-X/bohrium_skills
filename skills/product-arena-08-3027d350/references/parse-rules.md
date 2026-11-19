# PDF 文档解析规则（可执行版）

本文件是 `work/document.json` 的判定标准。先读页面，再改初稿；每个判断都要
能回指 `page` 与 PDF 文本块。

## 1. 选择解析器与阅读顺序

1. 入口优先 PyMuPDF（有 bbox）；不可用时依次尝试 `pdftotext -raw`、
   `pdftotext -layout`、pypdf。`work/context.json` / `document.engine`
   记录实际引擎。
2. 每页同时保留两种视图：
   - `text`：阅读顺序文本（用于正文段落）；
   - `layout_text`：保留列间空白的版面文本（用于表格与栏位判断）。
3. 双栏判定：
   - 左栏块 x 中心 < 页宽一半，右栏块 x 中心 > 一半；
   - 宽度 ≥ 页宽 72% 的块视为通栏（标题、摘要、大表、图题）；
   - 阅读顺序 = 顶部通栏 → 左栏自上而下 → 右栏自上而下 → 底部通栏。
4. 若引擎按 y 坐标逐行交错输出（左右栏句子交替），必须用 `layout_text`
   重排；重排后检查每段是否跨栏，跨栏即错。
5. 跨页段落：只有原文明确续接（无新标题、句子未完）时才合并为一个段落；
   `locator` 记录起始页/块，并在 `kind` 或 `warnings` 说明续接。

## 2. 文字保真

- 正文、表题、图题、脚注、参考文献一律逐字提取。
- 行末连字符：`trans-\nformer` → `transformer`；`well-\nknown` 保留连字符。
- 换行合并只处理空白：一个句子被 PDF 折行时合并为一行，不加/删词。
- 页眉、页脚、页码、期刊名移到 `headers_footers`，正文删除。
- 特殊字符（希腊字母、`±`、`°C`、上标/下标、`CO2` 等）保持抽取结果；
  不凭化学/数学常识改写。
- 重复段落只保留一次；若同一内容在正文与图注都出现，正文与题注各保留，
  不要互相替代。

## 3. 页与区块定位（核得回）

1. 每个段落必须有：
   - `page`：PDF 物理页码，从 1 开始；
   - `block_id`：该页的文本块序号（入口 `line_start` 或块顺序）；
   - `locator`：`p<page> b<block_id>`。
2. 表格：`page` + `source_locator`（如 `p2 b1`），并保留 `caption`。
3. 图：`page` + `locator`；公式：`page` + `locator`。
4. 不写不存在的页码；不把“第 1 页”当成 PDF 页码之外的编号。
5. 同一 block 含多种对象时（表题+表体+公式+图题），拆成多个对象，
   但 locator 可以都指向同一个 block，并在 `detection`/`kind` 说明。

## 4. 章节与结构

1. 标题取首页最大字号/最顶部正文块，逐字保留；作者行紧随标题。
2. 摘要从 `Abstract`/`摘要` 标题到 `Keywords`/正文首节之前。
3. 章节标题保留编号与层级：`1 Introduction`（level 1）、`2.1 ...`（level 2）。
4. 首页的标题/作者不要重复放进正文段落；如需保留，放在 Front matter
   并在导出时跳过（`document.md` 会跳过名为 Front matter 的节）。
5. 参考文献从 `References`/`Bibliography`/`参考文献` 开始；每条保留原始
   编号、作者、题名、期刊、年卷页、DOI；跨行条目合并为一条 `raw`。

## 5. 表格重建

1. 先用 `layout_text` 判断列边界：连续 2+ 空格或制表符分隔的列。
2. `columns` 是表头（含单位行）；`rows` 是数据行；每行列数必须与表头一致，
   缺列补 `""`。
3. 表题（`Table 1. ...`）放在 `caption`，不要当正文段落。
4. 合并单元格：
   - 纵向合并：后续行对应列重复首行值，或留 `""` 并在 `detection` 说明；
   - 横向合并：拆成多列或保留合并值，必须保持列数一致。
5. 多级表头：用两行表头合并成一个 `columns` 数组（如 `"Conversion (%)"`）。
6. 跨页续表：每页一张表，`caption`/`detection` 标 `continued`；
   不要静默拼成一张表，也不要把续表的重复表头当数据行。
7. **伪表格**：双栏正文在文本层会出现“两列 + 多空格”，若内容为连续
   句子而非单元格，删除该 `tables[]` 条目并还原为段落。
8. 表格是图片或无法读取：保留 `caption` 与 locator，`columns`/`rows`
   留空并在 `warnings` 说明；不要猜单元格。

## 6. 公式与特殊字符

1. 公式行判定：包含数学关系符（`= ≈ ≤ ≥ ± × ÷ ∑ ∫ √ ∞ ∂ ∇ ->` 等）且
   行尾有编号 `(n)`；编号写入 `number`。
2. `raw_text` 必须与 PDF 文本层一致，包括线性化的上下标（如 `E0`、
   `CO2`、`mAh g-1`）；不要擅自转成 Unicode 上标或 LaTeX。
3. `latex` 仅在能可靠转写时填写；否则 `latex: null`。
4. 公式为图片：`raw_text` 留空或保持占位，`number` 从题注/正文读取，
   `warnings` 记 `formula image on page N`。
5. 行内公式不单独建 `equations[]`；保留在所属段落 `text` 中。

## 7. 图与题注

1. 图题以 `Fig.`/`Figure`/`图` 开头且包含图号与说明句；
   `label` 取 `Fig. 1`/`图 2`，`caption` 取整行。
2. 正文里的 `Fig. 1 shows ...` 是引用句，不是图题；不要放进 `figures[]`。
3. 图片本身不可提取，只记录题注、页码与 locator；不描述图像内容。

## 8. 扫描页与 OCR

1. 页面无文字层时入口尝试 OCR；成功页：
   - `ocr: true`，进入 `ocr_pages`；
   - 文本可能含识别错误，报告中标注 `OCR used on pages [...]`。
2. OCR 失败：
   - 页面 `readable: false`，进入 `empty_pages`；
   - `ocr_error` 与工具错误进入 `warnings`；
   - 正文留空，**不得**根据标题、图表或常识补写。
3. `page_count = readable_pages + 失败页数` 必须自洽；
   `ocr_pages ⊆ readable_pages`。
4. 低分辨率/旋转页：在 `warnings` 记录，必要时在 `kind` 标 `ocr`。

## 9. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 左右栏逐行交错 | 句子拼接错误 | 用 layout/bbox 重排左栏→右栏 |
| 双栏正文当表格 | 结构错误 | 删除伪表，还原段落 |
| 表题当正文 | 结构缺失 | 移入 `tables[].caption` |
| 合并单元格错列 | 数据串行 | 补空串/重复值，保持列数 |
| 公式被“美化” | 原文失真 | 恢复 `raw_text`；`latex: null` |
| 页码编造 | 不可回查 | 只写真实物理页与 block |
| 扫描页补字 | 学术不端 | 留空 + `empty_pages` + `warnings` |
| 参考文献条目拆散 | 引用不完整 | 同条目跨行合并 `raw` |
| 缺 `report` | 审计为空 | 从 draft 保留并更新计数 |

## 10. 提交前自查

- [ ] 每页 `text` 与 `layout_text` 都检查过；双栏顺序无交错。
- [ ] 每个段落 `text` 逐字、`locator = p<page> b<block>`。
- [ ] 标题/作者/摘要/章节层级/参考文献齐全且不重复。
- [ ] 表格与表题一一对应；无双栏伪表；行列数一致。
- [ ] 公式 `raw_text` 与抽取原文一致，编号来自原文。
- [ ] 图题是真正的题注，不是正文引用句。
- [ ] `page_count`、`readable_pages`、`ocr_pages`、`empty_pages` 自洽。
- [ ] `report` 计数与实际数组长度一致。
- [ ] output 中 `document.json`、`document.md`、`tables/*`、
      `parse-report.json`、`result.json` 均存在。
