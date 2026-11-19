# 多文档比较与整合 Skill（v0.6.0）

## 用途

围绕同一研究问题，把多篇论文/材料落到同一套字段上，交付逐格可对照、逐格可回查的 `comparison_table`，并在定量综合前对 `task / metric / units / material / pollutant / budget` 六项逐项判定可比性。不可比、未报告、单篇结论分别保留，不合并成“领域共识”。

## 输入 / 输出

- 输入：`/input/comparison-workorder.json`（第一权威）、`/input/case.json`、`/input/task.md`、`/input` 下全部 corpus 文件。
- 输出：`/app/output/result.json`，核心字段 `workorder`、`documents[]`、`dimensions[]`、`comparison_table[]`、`comparability_gate[]`、`gate_summary`、`multi_document`、`synthesis`（含 `agreements/divergences/gaps/noncomparable/nonreported/single_document_only`）；顶层另有官方别名 `status/result/claims/uncertainties`。

## 快速开始

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md | head -n 1)")"

# 1) 先读 workorder，抄下 source_document_ids / paragraph 规则 / 维度 / 目标材料与污染物 / 不可比规则
# 2) 先写 comparison_table 并落盘 /app/output/result.json
# 3) 再写 comparability_gate 与 synthesis
# 4) 可选审计（失败不阻塞交付）
python3 "$SKILL_DIR/scripts/check_synthesis.py" \
  --result /app/output/result.json \
  --workorder /input/comparison-workorder.json --json
```

校验器也可直接指向输入目录，自动查找 `comparison-workorder.json`：

```bash
python3 "$SKILL_DIR/scripts/check_synthesis.py" \
  --result /app/output/result.json --input /input --json
```

## 最小示例（示例，不是正式答案）

三篇合成文档：DOC-A 报告 methylene blue 降解 92%（%），DOC-B 报告 methylene blue 降解 0.81（fraction of initial absorbance），DOC-C 报告 rhodamine B 降解 88%（1.0 g/L）。

正确处理：

- `units = incomparable`（DOC-A/DOC-B 单位与基准不同，且无换算）；
- `pollutant = incomparable`（DOC-C 是 RhB，不是 MB；不同染料不得合并）；
- `budget = incomparable`（DOC-C 为 1.0 g/L）；
- `gate_summary.quantitative_synthesis = restricted` 或 `blocked`，不输出“三篇平均降解率”；
- 三篇都报告 anatase TiO2 → 1 条 ≥2 篇的 `agreements`；
- DOC-A 的 {001} 晶面结论只有 1 篇 → `single_document_only[]`；
- DOC-C 未报告 band gap → `nonreported[]`。

## 设计要点

- **workorder-first**：文档 id、段落规则、比较维度、目标材料/污染物、不可比规则全部逐字抄录；不按自己的命名重写。
- **exact provenance**：单元格统一 `{source_document_id, paragraph_id, dimension, value, unit, condition, status}`；不用 `doc_id` 代替 `source_document_id`。
- **先判可比性，再做定量**：六项 gate 全部完成后才允许均值/排序/倍数；`unknown` 按不可比处理。
- **不合并不同组**：不同数据集、染料/污染物、材料、单位、预算一律分开；不可比项进入 `noncomparable[]`。
- **单篇不是共识**：`agreements` 每条至少 2 篇；单篇支持的结论只进 `single_document_only[]`。
- **负向明确**：只有 1 篇文档时 `status=insufficient_evidence`、`multi_document.satisfied=false`，明确说明不满足多文档比较。
- checker 是可选审计器，不是交付闸门；失败时按质量闸门手工核对并在 `uncertainty` 记录。

## 支持文件

| 文件 | 用途 |
|---|---|
| `references/field-dictionary.md` | 顶层字段、workorder、七字段单元格、六项 gate、synthesis 数组、官方 claims 的字段字典 |
| `references/multi-doc-synthesis-playbook.md` | workorder 字段表、七字段矩阵、六项可比性门、综合规则、失败模式 |
| `templates/comparison-matrix.json` | `result.json` 骨架；复制后替换 `REPLACE_WITH_*` |
| `scripts/check_synthesis.py` | 可选结构审计；`--self-test` 自测 |
| `SKILL.md` | 完整流程、输出契约、质量闸门与异常处理 |

## 已知限制

- 真实 workorder 的键名/嵌套可能变化；本 Skill 用别名兼容，无法映射时保留原键名并记录 `uncertainty`。
- 不联网、不补造数据；PDF 无文本层时依赖 `pdf-text-tool`/`ocr-tool`，读不出则标 `unreadable`。
- 段落 id 必须来自 workorder/case 或原文锚点；没有官方映射时不得伪造官方 id。
- 平台可能把 Skill 装到题目专属目录，命令必须先解析 `$SKILL_DIR`。
- checker 只查结构与一致性，不判断科学正确性；通过 checker 不等于结论正确。

## 版本与维护

- 版本：`0.6.0`（workorder-first + 六项可比性门）。
- 修改模板/checker 时同步更新 `SKILL.md` 的字段表、命令和本文件版本号。
