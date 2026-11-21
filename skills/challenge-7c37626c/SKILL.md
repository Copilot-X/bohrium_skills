---
name: formula-math-reasoner
description: 公式解析与数学推理：把公式转写为 LaTeX，建立符号表，逐步复现推导并给出每步依据，做数值代入、量纲与合理性检查。Parse formulas into LaTeX with symbol definitions, step-by-step derivations, dimensional analysis and numeric checks into /app/output/result.json. Use for 公式解析、LaTeX 转写、推导复现、量纲检查、数学推理。
---

# 公式解析与数学推理（Formula & Math Reasoner）

## 用途与触发

输入含公式、推导、符号表，或 task.md 要求「解释公式」「推导」「代入计算」「量纲检查」时使用。

## 支持文件（必须使用）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/formula-reasoning-playbook.md` | 转写、建符号表、推导、量纲检查前 | 先读；按其中三查/字段/失败模式执行 |
| `references/00-protocol.md` | 第一次运行、确认目录/预算/工具 | 按其中路径与预算安排步骤 |
| `references/01-evidence.md` | 选 quote、定 locator、区分 reported/inferred | 每个公式与数值检查按该规则绑证据 |
| `references/02-reading.md` | 判断问题类型（事实/条件/计算/缺口） | 按类型选择回答与状态 |
| `references/08-quality-gates.md` | 交付前总检 | 与本文件质量闸门一起执行 |
| `templates/formula-analysis.json` | 结果骨架 | 复制结构，替换为真实公式与符号 |
| `templates/symbol-table.md` | 建符号表时 | 逐符号填含义/单位/范围/状态，未知符号显式登记 |
| `templates/result_envelope.json` | 官方 reading 信封 | 补齐 status/result/claims/uncertainties |
| `scripts/inspect_mathml.py` | 输入含 publisher/source XML 时**先跑** | `python3 {SKILL_DIR}/scripts/inspect_mathml.py --input /input --out /app/work/formula_inventory.json --json` |
| `scripts/check_formula_analysis.py` | 交付前 | `python3 {SKILL_DIR}/scripts/check_formula_analysis.py --result /app/output/result.json --input /input/case.json --json` |
| `scripts/emit_official.py` | 写盘后、校验前 | `python3 {SKILL_DIR}/scripts/emit_official.py --result /app/output/result.json --profile reading --main-field formulas --json` |
| `scripts/validate_result.py` | 通用契约校验 | `python3 {SKILL_DIR}/scripts/validate_result.py /app/output/result.json --input /input` |

`{SKILL_DIR}` 通常为 `/skills/weekly-skill`。校验器会检查 LaTeX 括号/环境平衡、
推导步骤字段、量纲字段，并用安全算术复算 `computation` 与 `result.value` 是否一致。
不要为了让校验通过而改结果数值；应先修公式或计算。

## 执行步骤

0. **盘点输入与公式来源**：读 `/input/case.json`、`/input/task.md`、
   `environment/resources.yaml`，列出实际文件。若存在 `artifacts/publisher-source.xml`
   或 `artifacts/source-formulas.xml`，先运行
   `inspect_mathml.py`，用 `formula_inventory.json` 中的 `xml_id`、MathML 结构、
   `tex_annotation` 和祖先 section id 作为转写依据。**XML 目录只是公式节点子集，
   不是线性化答案或推导**，不得把目录顺序当推导链。
1. **转写**：公式 → LaTeX；保留原编号 `eq_id`。XML 直接给出的公式标
   `transcription_mode: xml_mathml` 并在 `source.xml_id` 记录节点 id；
   从 PDF 文本抽的标 `pdf_text`；视觉识别标 `vlm` 并逐符号复核。
2. **定义符号**：每个符号给含义、单位、取值范围、已知/未知。
3. **解析结构**：标注公式类型（定义式/守恒式/经验式/近似式）。
4. **推导**：从已知式到目标式，每步一行，写明依据（代入/移项/近似/定理名）。
5. **数值代入**：代入值 + 单位 + 量纲检查；量纲不一致立即报错。把纯算术写进
   `numeric_check.computation`（如 `40/50`），并让 `result.value` 与之一致；
   `check_formula_analysis.py` 会安全复算并核对。
6. **合理性检查**：数量级、边界条件、极限行为；在 `plausibility` 写清依据。
7. **回答**：问题逐条对应到公式或推导步骤。
8. **官方契约**：运行 `emit_official.py --profile reading --main-field formulas`，
   补齐 `status/result/claims/uncertainties`；原有 `formulas/derivation/numeric_check`
   等字段不改名、不删除。
9. **交付校验**：运行 `check_formula_analysis.py`（LaTeX 平衡、符号、推导、量纲、
   数值复算、XML 来源、缺失分支）和 `validate_result.py`；有 problem 先修，修不了
   标 `status=partial` 并把限制写入 `uncertainty`。最后确认 `/app/output/result.json`
   是校验后的版本（`emit_official.py` 与校验器的写盘顺序不要颠倒）。

## 本版输入契约（C01–C03 / XML 与缺失分支）

本版三个固定案例的材料不同，**按实际文件处理，不按旧学科标签补写**：

1. **C01/C03（有 XML）**：`artifacts/publisher-source.xml` 是出版商原始 XML 的原样
   副本；`artifacts/source-formulas.xml` 是其中实际公式节点的派生子集。转写与符号表
   必须回到这些节点：每个公式写 `source.xml_id` / `source.artifact_path` /
   `source.paragraph_id` 中的至少一个，并保留 MathML 的分式、上下标与来源 ID。
   在 `formula_inventory` 记录 `xml_files`、`formula_nodes` 与实际读取的文件。
2. **C02（真实缺失）**：若原快照没有公式表示、逐条预测或可复算原始数据，
   **保留缺失分支**：能核对的明确指标与条件照常交付并给证据；需要缺失对象的推导或
   复算必须 `status=partial` 或 `status=insufficient_evidence`，并在
   `formula_absence` 写 `found:false`、`reason`、`search_scope`/`evidence`。
   **禁止反推公式、禁止把未知值填 0、禁止把目录当答案。**
3. 输入只有正文文字时，公式转写标 `transcription_mode: pdf_text`；公式被截断或
   模糊时标 `transcription_confidence: low` 并在 `uncertainty` 说明，不猜补。
4. 官方输出契约要求顶层 `case_id`、`status`、`result`、`claims`、`uncertainties`。
   写盘后运行 `emit_official.py` 补齐；`status` 取
   `answered|partial|insufficient_evidence|input_error`，与 `formula_absence` /
   `uncertainty` 一致，不得为了拿分把缺失写成成功。

## 输出契约

```json
{
  "case_id": "...",
  "status": "answered|partial|insufficient_evidence|input_error",
  "formula_inventory": {
    "xml_files": 2,
    "formula_nodes": 14,
    "files": ["artifacts/publisher-source.xml", "artifacts/source-formulas.xml"],
    "order_rule": "document order, not derivation order"
  },
  "formula_absence": {
    "found": false,
    "reason": "原快照没有公式表示或可复算原始数据",
    "search_scope": "p1-p12, tables 1-2, artifacts/",
    "evidence": {"path": "case.json", "locator": "artifacts", "quote": "..."}
  },
  "formulas": [
    {
      "eq_id": "Eq.3",
      "latex": "\\eta = \\frac{P_{out}}{P_{in}}",
      "type": "definition|conservation|empirical|approximation",
      "symbols": [{"symbol": "\\eta", "meaning": "效率", "unit": "1", "status": "defined"},
                  {"symbol": "P_{out}", "meaning": "输出功率", "unit": "W", "status": "defined"}],
      "source": {"paragraph_id": "p5", "xml_id": "m12",
                 "artifact_path": "artifacts/source-formulas.xml",
                 "quote": "..."},
      "assumptions": ["稳态"],
      "formula_ast": {"op": "frac", "children": ["P_out", "P_in"]},
      "transcription_mode": "deterministic|vlm",
      "transcription_confidence": "high|medium|low",
      "reading_order": {"page": 5, "column": 1, "index": 2}
    }
  ],
  "derivation": [
    {"step": 1, "from": "Eq.3", "to": "Eq.4", "operation": "代入 Eq.2",
     "justification": "能量守恒", "latex": "..."}
  ],
  "numeric_check": {
    "inputs": [{"symbol": "P_{out}", "value": 40, "unit": "W"},
               {"symbol": "P_{in}", "value": 50, "unit": "W"}],
    "computation": "40/50",
    "result": {"value": 0.8, "unit": "1"},
    "dimensional_check": "W/W = 1",
    "plausibility": "0.8 在 [0,1] 内，合理"
  },
  "render_target": "latex",
  "package_dependencies": ["amsmath"],
  "compile_boundary": "syntax_only|compiled",
  "glyph_substitution": [],
  "support_files_used": ["references/formula-reasoning-playbook.md",
                         "scripts/check_formula_analysis.py"],
  "uncertainty": [{"item": "符号 x 未定义", "reason": "原文未给出", "impact": "无法数值化"}],
  "uncertainties": [{"item": "符号 x 未定义", "reason": "原文未给出", "impact": "无法数值化"}],
  "claims": [
    {"claim_id": "C1", "kind": "reported|derived|inferred",
     "text": "结论或计算结论", "evidence": [{"path": "...", "locator": "...", "quote": "..."}]}
  ],
  "change_log": [{"step": 1, "action": "...", "evidence": "..."}],
  "official_contract": {"profile": "reading", "source": "organizer public contract adapter"}
}
```

`claims[]` 中每条数值结论的 `evidence.quote` 必须逐字来自实际材料；`derived` 结论
还要给 `computation`，`inferred` 结论给 `rationale` 与 `confidence`（0–1）。

## 规则

1. LaTeX 必须可编译；不确定的符号用 `\square` 占位并登记 `unknown_symbol`。
2. 推导不得跳步；每步单独一行。
3. 近似必须写适用条件（小角、稀溶液、稳态、忽略高阶项）。
4. 数值结果给有效数字与单位；量纲不符时 `status=failed` 并指出错误步骤。
5. 原文公式排版有歧义时，列出两种解析并标 `ambiguous: true`。

## 异常处理

| 情况 | 处理 |
|---|---|
| 公式为图片且无法识别 | OCR + LaTeX 重建，标 `ocr_confidence` |
| 符号表缺失 | 从上下文推断，标 `inferred_definition` |
| 推导所需中间式缺失 | 明确写「原文未给出」，不自行补全为原文结论 |

## 自检

- 每个公式有 `eq_id/latex/type/source/symbols`，LaTeX 括号/环境平衡
- 每个符号有含义、单位、状态；未知符号已登记
- 每步推导有 from/to/operation/justification
- 数值检查含单位、量纲检查、有效数字；`computation` 与 `result.value` 一致
- `check_formula_analysis.py` 返回 `ok: true`
- 近似条件、歧义、缺失步骤已显式记录

## 快速路径（预算内）

1. **0–2 分钟**：读 task.md/case.json，列出公式编号、符号表与待推导目标。
2. **2–7 分钟**：先转写 1–2 个核心公式并完成符号定义，落盘骨架。
3. **7–13 分钟**：补推导链、数值代入与量纲检查。
4. **最后 2 分钟**：检查每步 justification 与 LaTeX 合法性。

## 方法增强（融合参考实践）

- **公式转写三查**：查上下标、查希腊字母、查括号作用域；转写后回读原图/原文逐符号核对。
- **符号表先行**：每个符号给含义、单位、取值范围、已知/未知；未定义符号标 `unknown_symbol`，不猜。
- **推导链**：每步一行，写 `from → to + operation + justification`；禁止跳步或把结论当步骤。
- **量纲分析**：代入前先做量纲检查；结果单位与目标不符时立即定位错误步骤。
- **近似条件**：小角、稀溶液、稳态、忽略高阶项等必须写明适用条件，不隐式使用。
- **多解析并置**：排版歧义时列出两种读法，标 `ambiguous: true`，不擅自选一个。

### 定向方法增强

- **来自 PaddleOCR 文档解析**：公式从版面读入，先定阅读顺序——多栏排版按栏读，不把相邻栏的公式串成一行；跨页公式标 `continued`，上下标被行高截断时标 `transcription_confidence: low`。
- **来自 PaddleOCR 文档解析**：识别模式要显式声明——确定性管道优先用于符号与数字，视觉模型在复杂版面上更准但可能产生“看起来合理”的幻觉；`transcription_mode ∈ {deterministic, vlm}`，vlm 结果逐符号复核并记 `hallucination_risk`。
- **来自 PaddleOCR 文档解析**：公式图片无法识别时不要猜，标 `unreadable` + 区域定位；空白页/无公式页明确写“未检出”，不生成占位公式。
- **来自 Pandoc**：先把公式解析成 AST（运算符、操作数、上下标、作用域）再渲染 LaTeX，AST 与目标格式解耦；同一 AST 可输出 LaTeX/MathML/纯文本，避免“边看边写”造成括号错配。
- **来自 Pandoc**：声明渲染目标与依赖包（`amsmath`、`siunitx`、`mhchem` 等）；缺 `.sty/.cls` 时只承诺“语法合法”，不承诺可编译，写 `compile_boundary`。
- **来自 Pandoc**：转换中缺失字形/符号用替代记号并记录 `glyph_substitution[]`，不静默丢符号；元数据（公式编号、来源页）随转换保留。
- **两篇合取后的改造**：`formulas[]` 增加 `formula_ast`、`transcription_mode`、`transcription_confidence`、`reading_order{page, column, index}`；顶层增加 `render_target`、`package_dependencies[]`、`compile_boundary`、`glyph_substitution[]`。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G0 | 输入来源 | XML 案例已跑 `inspect_mathml.py`；无公式案例有 `formula_absence` 证据 | 补盘点；不得用常识补公式 |
| G0b | 状态诚实 | C02 类缺失为 `partial`/`insufficient_evidence`，且 `uncertainty` 说明范围 | 改状态或补真实证据，不填 0 |
| G1 | LaTeX | 每个公式可编译/语法合法，编号与原文一致 | 修语法；不确定符号用 `\square` 并登记 |
| G2 | 符号 | 每个符号有含义/单位/状态 | 补定义或标 `unknown_symbol` |
| G3 | 推导 | 每步有 from/to/operation/justification | 补依据；无法补则标 `not_derived` |
| G4 | 量纲 | 数值代入前后量纲一致 | 不一致时 `status=failed` 并指出步骤 |
| G5 | 数值 | 有效数字、单位、范围合理 | 重算并给误差范围 |
| G6 | 来源 | 每个公式有 paragraph/equation 定位 | 无来源的公式删除或标 inferred |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| OCR 上下标错误 | 指数/下标与原文不符 | 放大原图逐符号核对，标 `ocr_confidence` |
| 符号未定义 | 推导中出现无来源符号 | 补定义或标 `unknown_symbol` |
| 跳步 | 相邻两式之间无依据 | 拆成多步，补 operation/justification |
| 量纲不符 | 结果单位与目标不一致 | 回查代入步骤，修正换算 |
| 近似无 condition | 使用了小角/稳态却未说明 | 补适用条件，否则撤下近似 |
| 歧义公式强解 | 两种读法只取一种 | 并列两种解析，标 `ambiguous` |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`status`、`formulas[]`、`derivation[]`、
      `numeric_check`、`render_target`、`support_files_used`，以及官方
      `result/claims/uncertainties`
- [ ] 每个公式有 LaTeX、类型、符号表、来源；XML 公式有 `source.xml_id`
- [ ] 每步推导有 from/to/operation/justification
- [ ] 数值代入含单位、量纲检查、有效数字；`computation` 可复算
- [ ] 近似条件已写明；未知/歧义项已显式记录
- [ ] C02 类缺失有 `formula_absence{found:false, reason, search_scope}`，未反推/未填 0
- [ ] `inspect_mathml.py`（如适用）、`emit_official.py`、
      `check_formula_analysis.py` 与 `validate_result.py` 均通过

## 最小示例（示例）

Eq.3 效率定义 → 代入 40 W/50 W → 0.8，量纲 W/W=1，合理。
