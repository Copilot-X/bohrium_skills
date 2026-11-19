# LaTeX 构建与修复规则

本文件是 `latex-source-compiler` 的可执行规则清单。模型按顺序执行，
每条规则都对应一个可检查的结果；不知道的事实不要补，不能编译不要假装编译。

## 1. 工程盘点（prepare 后立即做）

1. 打开 `work/context.json` 与 `work/inventory.json`，确认输入文件清单。
2. 打开 `work/latex-project.json`：
   - `main` 是否确实是应编译的主文件（含 `\documentclass`）；
   - `files` 是否覆盖主文件 `\input/\include` 引用的所有子文件；
   - `static.missing_refs` / `static.missing_citations` 是待修清单的起点。
3. 打开 `work/compile-baseline.json` 与 `work/error-list.json`：
   - `available=false` → 本环境没有 TeX 引擎，只做静态修复，最终 `partial`；
   - `errors[]` → 按“第一条根因优先”排序；
   - `warnings[]` → 区分未解析引用、版面溢出、字体/宏包警告。
4. 缺失文件处理：`\input{sections/x}`、`\bibliography{refs}`、
   `\includegraphics{fig/plot.png}`、`\documentclass{...}` 依赖的
   `.cls/.sty` 必须存在。prepare 不会复制图片与二进制资源，需要时
   从 `/input/<相同相对路径>` 复制到 `work/project/<相同相对路径>`。

## 2. 模板适配

| 检查 | 要求 | 常见错误 |
|---|---|---|
| `\documentclass` | 与 task.md/模板要求一致，选项（字号、栏数、纸张）不擅改 | 为了消错改成 `article`，丢失期刊类文件 |
| 导言区顺序 | 先 `\documentclass`，再宏包，再自定义命令，最后 `\begin{document}` | 宏包写在 `\begin{document}` 之后 |
| 宏包冲突 | 不重复加载；`hyperref` 一般最后加载；`xcolor` 先于 `tikz` | 为修一处加载多个功能重叠宏包 |
| 必填项 | 标题、作者、单位、摘要、关键词、基金、通讯作者等按模板补齐 | 缺 `\keywords`、缺 `\affil` 导致模板运行时报错 |
| 文件结构 | 主文件只保留骨架；章节拆到 `sections/` 时保持相对路径 | 移动文件后 `\input` 路径失效 |
| 编码/字体 | 中文工程用 `ctex`/`xeCJK` + XeLaTeX；不要混用 pdflatex 与中文字体 | 引擎与模板不匹配 |
| 类文件缺失 | 只使用 `/input` 提供的 `.cls/.sty`；没有就记录 `compile_failed` | 联网下载或伪造一个同名空类 |

## 3. 数学公式

1. **只修不重写**：保持符号、上下标、变量含义与原文一致；不确定的
   符号保留原样并写入 uncertainty。
2. **编号与引用**：
   - 需要编号的公式用 `equation/align/gather` 等非星号环境；
   - 每个被引用的公式必须有唯一 `\label{eq:...}`；
   - `\ref{eq:x}` 不改写成硬编码 `(3)`；编号交给 LaTeX。
3. **对齐**：多行公式用 `align`，每行一个 `&` 对齐点、一个 `\\`；
   不要用连续 `equation` 手工对齐。
4. **正斜体与单位**：
   - 变量斜体（`$p$`），函数名 `\sin/\log/\exp`；
   - 化学式/多字母标识 `\mathrm{CO2}` 或 `\text{}`；
   - 单位用 `\,` 与 `\mathrm{}`（如 `10\,\mathrm{MPa}`），或模板已提供的
     `siunitx`；不要混用 `siunitx` 与手写单位。
5. **常见错误**：
   - `Missing $ inserted` → 文本中的 `_`、`^`、`%`、`&`、`#` 未转义；
   - `Double subscript` → `x_i_j` 应写 `x_{i_j}`；
   - `\begin{equation}` 未闭合或嵌套 → 检查 `\end` 配对。

## 4. 表格与浮动体

1. **列数一致**：`tabular` 的列声明、每行 `&` 数、`\\` 行数三者匹配；
   合并单元格用 `\multicolumn{n}{...}{...}` 并同步列数。
2. **三线表**：`booktabs` 的 `\toprule/\midrule/\bottomrule`；
   不在三线表中混用 `\hline` 造成双线。
3. **宽表**：
   - 首选调整 `p{...}` 列宽、`\small`、表头换行；
   - 跨页用 `longtable`（并在导言区加载）；
   - `\resizebox{\textwidth}{!}{...}` 只能作为最后手段，且不得用于
     掩盖裁切；缩放过小需在 uncertainty 说明。
4. **浮动体位置**：`[htbp]` 优先，不强行 `[H]`（需 `float` 宏包）；
   每个 `table/figure` 必须有 `\caption`，被引用时必须有 `\label`，
   `\label` 放在 `\caption` 之后。
5. **数值与单位**：表内数字、有效数字、单位与正文一致；改格式不改值。

## 5. 引用与参考文献

1. **key 必须存在**：`\cite/\citep/\citet/\citealp` 的每个 key 都要在
   `work/project/**/*.bib` 中有条目；缺条目时优先补真实条目，
   没有可靠元数据就保留缺口感并在 uncertainty 标注，禁止编造 DOI/作者/年份。
2. **样式匹配**：`natbib` 的数字样式用 `plainnat/unsrtnat` 等，
   作者—年份用 `plainnat` + `\citep`；`biblatex` 工程不要混入
   `\bibliographystyle/\bibliography`。样式由模板决定，不擅自更换。
3. **多遍收敛**：引用/交叉引用至少需要 2 遍编译；`latexmk` 会自动
   处理 bibtex/biber。出现 `There were undefined references` 或
   `Citation ... undefined` 时，先确认 key/label，再重编译。
4. **编号与顺序**：不手动写 `[12]`；数字样式由 `.bst`/`biblatex` 生成。
5. **正文—条目一致**：作者、年份、标题、期刊、卷页、DOI 与 `.bib`
   保持原值；只修格式时不动字段内容。

## 6. 编译错误定位

1. 使用 `-interaction=nonstopmode -halt-on-error -file-line-error`：
   日志中的 `file:line: message` 是首选定位点。
2. **第一条错误优先**：后续错误常是级联；修完第一条就重编译，
   不要在未验证时批量改十几处。
3. 常见错误与处置：

| 日志 | 根因 | 处置 |
|---|---|---|
| `Undefined control sequence` | 命令拼错或宏包未加载 | 查命令所属宏包；确认拼写；按需加载 |
| `Missing $ inserted` | 数学符号出现在文本模式 | 加 `$...$` 或转义 `_ ^ % & #` |
| `Runaway argument` | 花括号/环境未闭合 | 从报错行向前找未闭合的 `{` 或 `\begin` |
| `File 'x.cls' not found` | 类/宏包缺失 | 用输入提供的文件；否则记录 `compile_failed` |
| `LaTeX Error: Environment x undefined` | 环境名拼错或宏包缺失 | 修正环境名或加载宏包 |
| `Too many }'s` | 多余右括号 | 配平括号，注意 `\{` 转义 |
| `Package ... Error` | 宏包选项冲突 | 读该宏包报错行；不要盲目删宏包 |
| `TeX capacity exceeded` | 递归 `\input` 或宏循环 | 检查包含关系与自定义宏 |

4. **警告分级**：
   - `Reference/Citation ... undefined`：必须修；
   - `Overfull \hbox`：影响版面时修，轻微溢出可记录；
   - `Font shape ... not available`：记录字体依赖，不伪造字体；
   - `Underfull \hbox`：通常可接受，除非模板要求严格。
5. 无引擎时：只能做静态检查，`compile-report.json` 的
   `available=false`、`pdf=null`；不得写“编译通过”。

## 7. 可重复构建记录

1. 报告必须记录：引擎（`latexmk/pdflatex/xelatex/lualatex`）、
   轮次（`passes`）、命令形态（由入口固定，见 `fam_latex._compile`）。
2. 依赖说明：模板类文件、宏包、字体、编码、`bib` 后端
   （bibtex/biber）以及需要的编译轮次。
3. 不要依赖绝对路径、本机缓存或交互输入；`\write18` 默认关闭。
4. 从干净状态可复跑：删除 `.aux/.bbl/.blg/.out/.toc/.fls/.fdb_latexmk`
   后重新编译，结果应一致；`output/project/` 已由入口清理这些中间文件。
5. 改动可追溯：`change-record.json` 的 `file_changes[].sha256_before/after`
   与 diff 是复核依据；不要手改报告数字。

## 8. 编辑纪律与失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 整段重写 | 原文意图与事实漂移 | 退回最小 before→after |
| 锚点不唯一 | 改错位置或 blocked | 带上下文重取唯一锚点 |
| 编造引用/元数据 | 学术不端风险 | 只用真实输入；缺证据写 uncertainty |
| 删除宏包“消错” | 功能/版面丢失 | 先定位命令所属宏包 |
| `\resizebox` 掩盖溢出 | 排版不合规 | 调列宽/换行/`longtable` |
| 无引擎却声称编译通过 | 结果不可信 | `available=false` + `partial` |
| 手改 `compile-report.json` | 审计失效 | 一律由 finalize 重新生成 |
| 同时直接改工程又提交同一条 edit | 二次应用 blocked | 两种方式只选一种改同一处 |

## 9. finalize 前自检清单

- [ ] `work/latex-edits.json` 每条 `before` 在目标文件中恰好出现一次。
- [ ] 未改数字、单位、引用 key、公式符号含义。
- [ ] 新增 `\label` 与 `\ref` 一一对应；新增 `\cite` 有真实 bib 条目。
- [ ] `work/project/` 中主文件与所有 `\input/\include` 目标存在。
- [ ] 图片/类文件已复制到工程内的正确相对路径。
- [ ] 预期结果：有引擎时 `compile-report.ok=true` 且有 `main.pdf`；
      无引擎时 `available=false`、`pdf=null`、`status=partial`。
