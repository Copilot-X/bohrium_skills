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
| `templates/formula-analysis.json` | 结果骨架 | 复制结构，替换为真实公式与符号 |
| `templates/symbol-table.md` | 建符号表时 | 逐符号填含义/单位/范围/状态，未知符号显式登记 |
| `scripts/check_formula_analysis.py` | 交付前 | `python3 {SKILL_DIR}/scripts/check_formula_analysis.py --result /app/output/result.json --input /input/case.json --json` |
| `scripts/validate_result.py` | 通用契约校验 | `python3 {SKILL_DIR}/scripts/validate_result.py /app/output/result.json --input /input` |

`{SKILL_DIR}` 通常为 `/skills/weekly-skill`。校验器会检查 LaTeX 括号/环境平衡、
推导步骤字段、量纲字段，并用安全算术复算 `computation` 与 `result.value` 是否一致。
不要为了让校验通过而改结果数值；应先修公式或计算。

## 执行步骤

1. **转写**：公式 → LaTeX；保留原编号 `eq_id`。
2. **定义符号**：每个符号给含义、单位、取值范围、已知/未知。
3. **解析结构**：标注公式类型（定义式/守恒式/经验式/近似式）。
4. **推导**：从已知式到目标式，每步一行，写明依据（代入/移项/近似/定理名）。
5. **数值代入**：代入值 + 单位 + 量纲检查；量纲不一致立即报错。把纯算术写进
   `numeric_check.computation`（如 `40/50`），并让 `result.value` 与之一致；
   `check_formula_analysis.py` 会安全复算并核对。
6. **合理性检查**：数量级、边界条件、极限行为；在 `plausibility` 写清依据。
7. **回答**：问题逐条对应到公式或推导步骤。
8. **交付校验**：运行 `check_formula_analysis.py`（LaTeX 平衡、符号、推导、量纲、
   数值复算）和 `validate_result.py`；有 problem 先修，修不了标 `status=partial`
   并把限制写入 `uncertainty`。

## 输出契约

```json
{
  "case_id": "...",
  "formulas": [
    {
      "eq_id": "Eq.3",
      "latex": "\\eta = \\frac{P_{out}}{P_{in}}",
      "type": "definition|conservation|empirical|approximation",
      "symbols": [{"symbol": "\\eta", "meaning": "效率", "unit": "1", "status": "defined"},
                  {"symbol": "P_{out}", "meaning": "输出功率", "unit": "W", "status": "defined"}],
      "source": {"paragraph_id": "p5", "quote": "..."},
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
  "change_log": [{"step": 1, "action": "...", "evidence": "..."}]
}
```

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

- [ ] `/app/output/result.json` 含 `case_id`、`formulas[]`、`derivation[]`、
      `numeric_check`、`render_target`、`support_files_used`
- [ ] 每个公式有 LaTeX、类型、符号表、来源
- [ ] 每步推导有 from/to/operation/justification
- [ ] 数值代入含单位、量纲检查、有效数字；`computation` 可复算
- [ ] 近似条件已写明；未知/歧义项已显式记录
- [ ] `check_formula_analysis.py` 与 `validate_result.py` 通过

## 最小示例（示例）

Eq.3 效率定义 → 代入 40 W/50 W → 0.8，量纲 W/W=1，合理。
