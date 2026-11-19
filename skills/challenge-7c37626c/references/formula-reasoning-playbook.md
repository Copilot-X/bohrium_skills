# 公式解析与数学推理参考

配合 `templates/formula-analysis.json`、`templates/symbol-table.md`、
`scripts/check_formula_analysis.py` 使用。**task.md 与材料优先**；无法识别或原文未给的
符号/推导步骤要显式记录，不要补成“看起来合理”的结论。

## 1. 转写三查

1. 查上下标：指数、下标、上下标嵌套是否与原图一致。
2. 查希腊字母/符号：`\alpha` vs `a`、`\partial` vs `d`、`\sum` vs `\Sigma`。
3. 查括号作用域：`\frac{}{}`、`\sqrt{}`、`\left(...\right)` 是否闭合。

转写后逐符号回读原图/原文；无法确定的符号用 `\square` 占位并登记
`unknown_symbol`，不要猜。

## 2. 先建符号表

每个符号至少记：

| 字段 | 含义 |
|---|---|
| `symbol` | LaTeX 记号（如 `P_{out}`） |
| `meaning` | 中文/英文含义 |
| `unit` | SI 单位或 `1`（无量纲） |
| `range` | 取值范围/定义域（未知写 null） |
| `status` | `defined / inferred_definition / unknown_symbol` |
| `source` | 原文定位或“上下文推断” |

同一符号在不同公式中含义不同要拆成两条，不合并。

## 3. 公式结构

- `type`: `definition|conservation|empirical|approximation`。
- 记录 `assumptions[]`（稳态、理想气体、小角、忽略高阶项等）。
- 有编号的公式保留原 `eq_id`；跨页/跨栏标 `continued`。
- 排版歧义（如 `a/bc`、上下标位置）列出两种解析，标 `ambiguous: true`。

## 4. 推导链

每步一行：

```json
{"step": 1, "from": "Eq.3", "to": "Eq.4",
 "operation": "代入 Eq.2 并移项",
 "justification": "能量守恒",
 "latex": "..."}
```

规则：

- 不跳步；每一步都要有 `operation` 和 `justification`。
- 近似步骤必须写适用条件；没有条件就撤下近似。
- 原文缺失中间式时写 `not_provided`，不自行补成原文结论。
- `from/to` 指向已有 `eq_id` 或上一步编号；悬空引用要报告。

## 5. 数值与量纲

- 代入前先做量纲检查：`W/W = 1`、`mol/L × L = mol`。
- 结果给有效数字、单位、范围；不要给超过输入精度的位数。
- 量纲不符时 `status: "failed"` 并指出错误步骤，不要强行给数值。
- 合理性检查：数量级、边界条件（0、∞、极限）、单调性。
- `computation` 写成可复算的纯算术表达式（如 `40/50`），方便脚本核对。

## 6. LaTeX 语法边界

- 只承诺语法合法时写 `compile_boundary: "syntax_only"`；实际编译需要
  `amsmath`/`siunitx`/`mhchem` 等宏包时列入 `package_dependencies[]`。
- 缺失字形用替代记号并登记 `glyph_substitution[]`，不静默丢符号。
- `\left`/`\right`、`\begin{}`/`\end{}`、花括号必须成对。

## 7. 常见失败模式

| 信号 | 问题 | 修复 |
|---|---|---|
| 上下标与原文不符 | OCR/转写错误 | 放大原图逐符号核对 |
| 推导出现未定义符号 | 符号表缺失 | 补定义或标 unknown_symbol |
| 相邻两式无依据 | 跳步 | 拆步并补 operation/justification |
| 结果单位不对 | 量纲错误 | 回查代入/换算 |
| 使用小角/稳态未说明 | 近似无条件 | 补条件或撤下近似 |
| 歧义公式只取一种 | 强解 | 并列两种解析，标 ambiguous |
| 数值位数过多 | 虚假精度 | 按输入有效数字截断 |
| 公式无来源 | 无法追溯 | 补 paragraph/equation 定位 |

## 8. 交付前检查

- [ ] 每个公式有 `eq_id/latex/type/source/symbols`。
- [ ] 每个符号有含义、单位、状态；未知符号已登记。
- [ ] 每步推导有 from/to/operation/justification。
- [ ] 数值代入含单位、量纲检查、有效数字。
- [ ] `computation` 可被脚本复算，结果与 `result.value` 一致。
- [ ] 近似条件、歧义、缺失步骤已显式记录。
- [ ] `check_formula_analysis.py` 返回 `ok: true`。
