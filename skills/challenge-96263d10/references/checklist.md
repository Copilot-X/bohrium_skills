# 格式适配检查清单（逐项可核对）

用途：对 `manuscript.docx` 与 `format-spec.json` 做人工复核，或为自动检查
无法判定的条目补 `manual_check`。每条都要写“产物中的可定位证据”，不能只写
“已按模板调整”。

## 1. 页面与分节

| 检查码 | 检查项 | 验证方式 |
|---|---|---|
| `PAGE-SIZE` | 纸张尺寸与方向 | 读 `word/document.xml` 的 `sectPr/pgSz`，比较 `w`/`h` 与 `orient`；注意横向时宽高互换 |
| `PAGE-MARGIN` | 上下左右、页眉页脚距、装订线 | 读 `sectPr/pgMar` 的 twips，换算 cm；逐边比较，容差约 0.05 cm |
| `PAGE-COLUMNS` | 栏数与栏间距 | 读 `sectPr/cols` 的 `num`/`space`；单栏时 `num=1` |
| `PAGE-SECTIONS` | 分节符数量 | 统计 `sectPr`；模板多节时确认页眉页脚与横向页未被丢弃 |
| `HEADER-FOOTER` | 页眉页脚引用 | 检查 `headerReference`/`footerReference` 与 `titlePg`；视觉位置用 Word/LibreOffice 复核 |

## 2. 正文与标题层级

| 检查码 | 检查项 | 验证方式 |
|---|---|---|
| `BODY-FONT` | 正文字体（西文/中文） | 检查 `docDefaults`、Normal 样式与正文 run 的 `rFonts`；字体缺失时确认已替换为 Liberation Serif 并记录 |
| `BODY-SIZE` | 正文字号 | 检查 `sz` 半磅值；模板 10.5 pt 对应 `sz=21` |
| `BODY-SPACING` | 行距与段前段后 | 检查 `spacing` 的 `line`/`lineRule`/`before`/`after`；多倍行距 `line=240×倍数` |
| `TITLE-STYLE` | 标题样式 | 确认首段使用模板 Title 或等价居中/加粗/字号设置 |
| `HEADING-1` … `HEADING-6` | 各级标题的样式绑定与层级 | 检查 `pStyle` 与 `outlineLvl`；目录/书签依赖 outline level，不能只改字号加粗 |
| `STRUCT-HEADINGS` | 章节结构完整 | 比较源稿与成品的标题数量/层级；缺失或误升级都记录 |
| `NUMBERING` | 章节编号 | 模板使用自动编号时检查 `numPr`；否则确认文本编号连续且未被交叉引用改坏 |

## 3. 图表与题注

| 检查码 | 检查项 | 验证方式 |
|---|---|---|
| `CAP-FIGURE` | 图题样式 | 检查图题段落的样式、字号、对齐与是否斜体 |
| `CAP-TABLE` | 表题样式 | 检查表题段落的样式、字号与对齐 |
| `CAP-POSITION` | 图题在图下、表题在表上（或模板相反规定） | 读文档顺序：题注段落与 `w:tbl`/图片段落的相对位置 |
| `CAP-NUMBER` | 题注编号连续 | 按 Figure/Table 分别检查编号从 1 连续递增；重复编号要告警 |
| `CAP-CROSSREF` | 正文交叉引用 | 搜索正文中的 Figure/Table/图/表编号，确认与题注重编号一致 |
| `TABLE-BORDERS` | 三线表或模板表格线 | 检查 `tblBorders` 的 top/bottom/insideH/insideV |
| `IMAGE-RETAINED` | 图片未被图片化或丢失 | 统计源稿与成品的 `a:blip` 数量；DOCX 源应在副本上套格式 |

## 4. 参考文献与声明

| 检查码 | 检查项 | 验证方式 |
|---|---|---|
| `REF-STYLE` | 参考文献样式（numeric/author-year/GB/T 7714/APA…） | 检查条目是否以 `[1]`、`1.` 或作者-年份开头；样式转换超出脚本能力时标 `manual_check` |
| `REF-INDENT` | 悬挂缩进与字号 | 检查参考文献段落的 `ind/hanging`、`sz`、行距 |
| `REF-HEADING` | 参考文献标题存在且未被当作正文 | 检查标题样式与位置 |
| `CITATION-FACTS` | 正文引用编号保留 | 比较源稿与成品的 `[n]` 集合与上下文 |
| `ABSTRACT` | 摘要要求 | 模板/指南要求摘要时，确认源稿已有摘要被保留；不得凭空补写 |
| `KEYWORDS` | 关键词要求 | 同上 |
| `DECLARATIONS` | 数据可用性、利益冲突、作者贡献、伦理、基金 | 只保留源稿已有内容；不适用时写理由，不伪造声明 |

## 5. 转换完整性

| 检查码 | 检查项 | 验证方式 |
|---|---|---|
| `FIDELITY-BODY` | 正文逐字保留 | 比较源稿与成品的词元多重集；题注重排造成顺序变化时看 `ordered_similarity` |
| `FIDELITY-TABLE` | 表格内容与行列数 | 比较 `w:tbl` 数量、行列数与单元格文本 |
| `FIDELITY-OBJECT` | 图片/公式/域/脚注/批注/修订 | 统计 `a:blip`、`m:oMath`、`instrText`、`footnoteReference`；应保留原对象而非转文字 |
| `FIDELITY-SECTION` | 分节、页码、目录 | 检查 `sectPr`、`PAGE` 域、TOC 域；无法自动刷新时标 `manual_check` |
| `DOCX-OPEN` | 文件可打开 | 用 `docio.py check` 回读 zip、`[Content_Types].xml`、`word/document.xml` 与段落数 |
| `ARTIFACT-EXISTS` | 结果中的路径真实存在 | 逐条检查 `result.json` 的 `deliverable`/`artifacts` 路径与大小 |

## 6. 官方字段与记录

| 检查码 | 检查项 | 验证方式 |
|---|---|---|
| `OFFICIAL-FIELDS` | 四个 writing 字段 | `result.json` 顶层存在 `deliverable`、`preserved_facts`、`evidence_map`、`change_log` 且非空 |
| `EVIDENCE-MAP` | 要求与证据一一对应 | 每条 `requirements[]` 都能在 `evidence_map` 找到同 ID，含 artifact/locator/check_code |
| `PRESERVED-FACTS` | 数字/引用/题注/章节/术语可回溯 | 每条有 source 与 output 定位；找不到写 `unverified` |
| `CHANGE-LOG` | 改动可复核 | 页面、字体、标题、题注、参考文献、字体替换、正文来源都有 before/after/reason |
| `UNMET-UNCERTAINTY` | 限制与人工项 | `unmet` 写无法满足项，`uncertainty` 写材料缺口、冲突、无法自动判断项 |

## 7. 复核顺序（建议）

1. `result.json` 的 `status`、`summary`、`unmet`、`uncertainty`；
2. `format-spec.json` 的 `meta.evidence` 与 `meta.conflicts`；
3. `docx-check.json` 的 page/margins/columns/fonts/sizes/captions/references；
4. 用 Word/LibreOffice 打开 `manuscript.docx`，看目录、页码、页眉页脚与浮动对象；
5. 对 `manual_check` 逐条补证据，不把“看起来像”写成 `pass`。
