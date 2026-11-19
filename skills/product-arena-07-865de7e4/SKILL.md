---
name: latex-source-compiler
description: LaTeX 写作与编译：在真实 LaTeX 工程上完成模板适配、公式/表格/引用修复与多遍编译，交付可继续编辑的完整源码树、真实编译报告、逐条改动记录和错误分析；缺少 TeX 引擎时如实返回 compile_unavailable 与 partial，不伪造 PDF。LaTeX source editing, template adaptation, math/table/citation repair and reproducible multi-pass compilation with an honest build record. Use for LaTeX 编译、模板适配、公式、表格、引用、编译错误、可重复构建、latexmk、pdflatex、xelatex.
---

# LaTeX 写作与编译（LaTeX Source & Compiler）

## 用途与触发

task.md 要求“修好这个 LaTeX 工程”“让稿子编译通过”“迁入期刊/会议模板”
“修公式/表格/交叉引用/参考文献”“定位编译错误并说明轮次与引擎”
“给出可重复构建记录”时使用。也适用于 `/input/case.json` 给出
`template` / `engine` / `citation_style` 等约束的 LaTeX 写作案例。

不适用：只给 PDF 没有源码且要求还原整份源码；要求联网下载缺失宏包或
模板类文件；输入根本不是 LaTeX 工程。

核心原则：**编得通、改得对、交源码、可复现、不撒谎**。
脚本负责工程盘点、静态检查、真实编译、逐条应用编辑、diff 与报告；
模型负责判断改什么、怎样最小改动、如何保住公式/表格/引用与原文意图。
没有 TeX 引擎时脚本返回 `compile_unavailable`，这是诚实结果，**不要伪造 PDF**。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：目标模板、引擎、引用样式、必填项、允许的改动范围。
- LaTeX 工程：`.tex` 主文件与子文件、`.bib`、`.cls/.sty/.bst`、
  `sections/`、图片等；相对路径按原工程保持。
- 可选：模板说明、期望 PDF、以及已有的 `work/latex-edits.json`。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 文本 IO、inventory、静态审计、result.json 信封 |
| `scripts/parsers.py` | `.tex/.bib/.md/.txt` 等材料提取 |
| `scripts/fam_latex.py` | 工程复制、主文件识别、静态检查、编译、编辑应用与 diff |
| `task.json` | family=latex-writing、module=fam_latex、profile=writing |
| `templates/latex-edits.schema.json` | `work/latex-edits.json` 字段说明 |
| `templates/compile-report.schema.json` | `output/compile-report.json` 字段说明 |
| `references/latex-build-rules.md` | 模板适配、公式、表格、引用、编译错误、可重复构建规则 |
| `examples/project/main.tex` | synthetic 小工程主文件（非正式案例） |
| `examples/project/refs.bib` | synthetic 文献库（非正式案例） |
| `examples/latex-edits.json` | synthetic 编辑计划（非正式答案） |

## 工作流（prepare → 模型编辑 → finalize）

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，把文本类工程文件（`.tex/.latex/.bib/.cls/.sty/.bst/
   .cfg/.def`、可抽取的 `.pdf` 与 `.md/.txt`）复制到 `work/project/`；
   **图片（`.png/.jpg/.jpeg/.eps/.svg`）等二进制资源不会被复制**，
   需要时手工从 `/input` 补到原相对路径。同时保留一份
   `work/project-baseline/` 作为 diff 基线；
2. 选主文件：第一个含 `\documentclass` 的 `.tex`（按路径排序），
   写入 `work/latex-project.json` 的 `main`；
3. 写 `work/latex-project.json`：`case_id/main/files/static/engine/pdf`；
   `static` 含 `documentclass/begin_document/end_document/balanced_braces/
   citations/labels/refs/bib_keys/missing_refs/missing_citations/ok`；
4. 真实尝试编译一次，写 `work/compile-baseline.json`（`available/ok/
   engine/error/passes/pdf/errors/warnings`）与 `work/error-list.json`；
5. 写 `work/latex-edits-template.json`（编辑计划骨架）；
6. 写一份 `status=partial` 的 provisional `result.json`。

运行时若存在 `latexmk`，执行
`latexmk -pdf -interaction=nonstopmode -halt-on-error -file-line-error`；
否则依次使用可用的 `pdflatex` / `xelatex` / `lualatex` 连跑 2 遍。
日志按行解析：`!` 开头或 `Undefined control sequence` 计为错误；
`LaTeX Warning:`、`Package ... Warning:`、`Overfull/Underfull \hbox`、
`undefined` 计为警告。
若 `available=false`，说明环境没有 TeX 引擎：保留源码与静态分析，
在 result 中写 `compile_unavailable`，不要生成占位 PDF。

如果主文件选错（工程有多个 `\documentclass`），把
`work/latex-project.json` 的 `main` 改成正确相对路径再编辑；该文件是
工作文件，finalize 以它为准。若主文件在子目录中，编译命令只使用
文件名（cwd 为工程根），此时把主文件复制/移动到工程根（如
`main.tex`），并把其中的 `\input/\include/\bibliography/
\includegraphics` 相对路径改成相对工程根，再把 `main` 指向这个根级副本。

### 第 2 步：模型编辑（约 60% 预算）

两种方式，任选其一；**推荐写 `latex-edits.json`**，因为每条
before/after 都会进入 `change-record.json`。

**方式 A：写 `/app/work/latex-edits.json`（推荐）**

```json
{
  "case_id": "与输入一致",
  "main": "main.tex",
  "edits": [
    {
      "id": "L001",
      "file": "main.tex",
      "before": "逐字来自 work/project/main.tex 的唯一片段",
      "after": "修改后的片段",
      "reason": "修复什么错误 / 满足哪条模板要求",
      "error_id": "compile:Undefined control sequence"
    }
  ],
  "template_requirements": ["期刊模板必填项或样式约束，供人工复核"]
}
```

规则（详见 `references/latex-build-rules.md`）：

1. **锚点唯一**：`before` 必须在目标文件中**恰好出现一次**；
   出现 0 次或多次会被 `blocked`，该条不应用、原文不变。
2. **最小改动**：只改出错或不合规的片段；不重写整段，不改
   数字、单位、符号、引用 key、`\label` 语义。
3. **模板适配**：保持 `\documentclass` 选项与模板要求一致；
   宏包按需加载，`hyperref` 等尽量靠后；不重复加载同一宏包。
4. **公式**：多行对齐用 `align/gather`；每条编号公式有唯一 `\label`；
   变量斜体、单位与函数名用 `\mathrm`/`\text`；不把 `\ref` 改成硬编码编号。
5. **表格**：三线表用 `booktabs`；列数、`&` 数、`\\` 行数一致；
   宽表优先调列宽/换行/`longtable`，不要把 `\resizebox` 当默认补丁。
6. **引用**：`\cite/\citep/\citet` 的 key 必须存在于 `.bib`；
   `\ref` 的目标必须存在 `\label`；修复后仍需多遍编译才收敛。
7. **`template_requirements` 不会被 finalize 自动应用**，它只是
   自检与复核清单；真正生效的是 `edits[]` 和你在工程里的直接修改。

**方式 B：直接编辑 `/app/work/project/` 下的源码**

可以直接改文件、加 `.bib` 条目、补 `\label`。finalize 会把工程与
`project-baseline/` 做 diff 并写入 `change-record.json`；没有逐条
before/after，所以可核验性弱于方式 A。不要同时用两种方式改同一处：
若已直接改成 after，再提交同一条 edit 会因 `before` 不存在而 blocked。

注意：prepare 只复制可提取的文本类材料，图片等二进制资源若未出现在
`work/project/`，需从 `/input` 按原相对路径手工复制进 `work/project/`。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/latex-edits.json`，按 `edits[]` 顺序应用
   `before → after`，记录 `line`；无法应用的写入 `blocked` 并给出原因；
2. 对 `work/project/` 与 `work/project-baseline/` 逐文件比较
   （文本给 unified diff，二进制给 sha256）；
3. 重跑静态检查（`missing_refs` / `missing_citations` / 括号配平等）；
4. 真实重新编译，解析错误与警告；仅当产物以 `%PDF-` 开头才复制为
   `output/main.pdf`；
5. 复制完整源码树到 `output/project/`，删除 `.aux/.log/.out/.toc/
   .bbl/.blg/.fls/.fdb_latexmk` 等中间文件；
6. 写 `output/compile-report.json`、`output/change-record.json`、
   `output/error-analysis.md`，最后写 `output/result.json`（writing
   官方四字段由入口生成）。

`status` 取 `completed|partial|input_error`：只有静态检查通过、
无日志错误、无 blocked、无缺失引用/文献、编译 `ok` 且产出 PDF 时才是
`completed`；没有引擎或编译失败一律 `partial` 并写 uncertainty。

## 输出契约

```text
/app/output/
  project/              # 完整可编辑源码树（首选交付物）
  main.pdf              # 仅当真实编译产出合法 PDF 时存在
  compile-report.json   # 编译、静态检查、改动汇总
  change-record.json    # 应用的编辑、blocked、逐文件 diff/sha
  error-analysis.md     # 引擎、轮次、错误、警告、缺失引用
  result.json           # 官方结果信封
```

`compile-report.json` 顶层字段：

```json
{
  "case_id": "...", "main": "main.tex",
  "available": true, "ok": false, "engine": "pdflatex",
  "error": "exit 1", "passes": 1,
  "errors": ["! Undefined control sequence."],
  "warnings": ["LaTeX Warning: Reference `tab:x' on page 1 undefined"],
  "static": {"missing_refs": ["tab:x"], "missing_citations": [], "ok": true},
  "edits_applied": 1, "edits_blocked": 0,
  "file_changes": [{"file": "main.tex", "changed": true, "diff": "..."}],
  "pdf": null
}
```

`change-record.json`：`{case_id, applied[], blocked[], file_changes[]}`，
`applied[]` 含 `edit/file/line`，`blocked[]` 含 `edit/reason`。

`result.json` 必须含：

- `case_id`、`status`、`task_status`、`profile`；
- `deliverable`：`{path: "project", format: "directory", exists: true}`；
- `preserved_facts[]`：本模块当前为空数组，事实保持由最小编辑与 diff 证据体现；
- `evidence_map[]`：每个改动文件一条，`claim="source edited"`，
  `source.path` 为工程内相对路径，`quote` 为 diff 片段；
- `change_log[]`：prepare / edit / compile 等真实步骤；
- `artifacts[]`：`project/`、`compile-report.json`、`change-record.json`、
  `error-analysis.md`、可选 `main.pdf`，每条 `exists` 必须真实；
- `result`：`summary` 与完整 `report`；`claims` 两条（compile/static）；
- `uncertainties[]`：`compile_unavailable`、`compile_failed`、
  `blocked_edits`、`missing_refs`、`missing_citations` 等；
- `output_contract` 原样保留，`contract_coverage` 逐字段说明。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 工程完整 | `output/project/` 存在；主文件与子文件/`.bib` 可继续编辑；无 aux/log 中间产物 |
| G2 | 改动可控 | 每条 applied 都有唯一 `before`、真实 `line` 与 diff；blocked 逐条给出原因 |
| G3 | 静态一致 | `documentclass/begin/end` 齐全、花括号配平、`missing_refs=[]`、`missing_citations=[]` |
| G4 | 编译真实 | `available/ok/engine/passes` 来自真实进程；`main.pdf` 有 `%PDF-` 头；无引擎时不得有 PDF |
| G5 | 错误可定位 | 有引擎时错误带文件名/行号（`-file-line-error`）；级联错误以第一条根因为准 |
| G6 | 可重复 | 报告记录引擎、轮次、命令形态；源码改动有基线 sha256，他人可复跑 |
| G7 | 契约 | `result.json` 可解析；artifact 路径存在；无 PDF 时不得标 `completed` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 没有 TeX 引擎 | 保留源码、静态分析与错误分析；`status=partial` + `compile_unavailable`；不伪造 PDF |
| 输入无 `.tex` | prepare 直接 `input_error`；不生成空工程 |
| 编译失败 | 先修第一条错误；`error-analysis.md` 记录错误/警告；`status=partial` |
| 缺 `.cls/.sty` | 优先使用 `/input` 提供的文件；没有则记录 `compile_failed`，不联网下载 |
| 缺图片/二进制资源 | 从 `/input` 复制到 `work/project/` 的原相对路径，再 finalize |
| `before` 出现 0 次或多次 | 该条 blocked，原文不变；重取唯一锚点（带上下文）后重跑 |
| 未解析引用/文献 | 补 `\label` 或 `.bib` 条目；没有可信来源就保留并在 uncertainty 标注，不编造 |
| Overfull hbox | 调列宽/换行/`longtable`；不用 `\resizebox` 掩盖溢出 |
| 主文件在子目录 | 复制到工程根并把 `work/latex-project.json` 的 `main` 指向副本 |
| 编译超时/死循环 | 记录 timeout；检查递归 `\input`、死循环宏包；保留部分结果 |
| 时间不足 | 先保证源码与 `result.json` 落盘，`status=partial`，说明未修项 |

## 最小示例（synthetic，不是正式答案）

`synthetic` 工程片段（见 `examples/project/main.tex`）：

```latex
The sample reached 90\% selectivity (Table~\ref{tab:demo}).
...
\caption{Synthetic performance table.}
```

`work/latex-edits.json` 中的单条 edit（放进 `edits[]`）：

```json
{"id": "L001", "file": "main.tex",
 "before": "\\caption{Synthetic performance table.}",
 "after": "\\caption{Synthetic performance table.}\n\\label{tab:demo}",
 "reason": "Table~\\ref{tab:demo} 未解析；在 caption 后补唯一 label",
 "error_id": "static:missing_ref:tab:demo"}
```

`finalize` 后应能核对：`change-record.json` 的 `applied` 含 `L001`
与行号；`compile-report.json` 的 `static.missing_refs` 不再含
`tab:demo`；无引擎环境下 `available=false`、`pdf=null`、
`status=partial`；`main.pdf` 不存在而不是空文件。

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_latex` 模块；prepare 写
  provisional result，finalize 生成真实编译报告与改动记录。
- 编译依赖运行时是否安装 TeX Live/`latexmk`；本地无引擎时只能做
  静态检查与源码交付，`status` 必然为 `partial`。
- 静态检查是正则/计数启发式：`balanced_braces` 只看主文件原始括号，
  `missing_refs` 只覆盖 `\ref/\autoref/\eqref/\pageref`，
  `missing_citations` 只覆盖 `\cite*`；`\include`/`\input` 的路径存在性
  与宏包可用性需人工或真实编译确认。
- `file_changes` 只遍历仍存在的文件：删除的文件不会出现在 diff 中，
  需要删除时在 `reason`/`uncertainty` 里明确说明。
- 只支持离线文本工程；不联网安装宏包、不调用外部编译服务。
- 示例是 synthetic 接口样例，不是正式案例答案；不要复制其中实体或来源。
