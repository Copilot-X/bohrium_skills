---
name: literature-data-cleaner
description: 文献数据清洗与标准化：先做字段画像，再按可追溯规则统一单位、命名、去重、缺失与异常值处理，保留原始值并输出清洗日志和质量报告。Profile, clean and standardize literature-derived tabular data with a traceable cleaning log and quality report under /app/output. Use for 数据清洗、单位统一、去重、缺失值处理、质量报告。
---

# 文献数据清洗与标准化（Literature Data Cleaner）

## 用途与触发

task.md 要求「清洗」「标准化」「去重」「统一单位」「质量报告」时使用。核心：**每条清洗规则可追溯、原始值不丢失**。

## 支持文件（必须使用）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/data-cleaning-playbook.md` | 画像、定规则、处理缺失/异常前 | 先读；按规则表与失败模式执行 |
| `templates/cleaning-rules.json` | 写清洗规则 | 复制后按实际字段/单位/主键修改；规则顺序即执行顺序 |
| `scripts/clean_table.py` | 执行清洗 | `python3 {SKILL_DIR}/scripts/clean_table.py --input /input/data.csv --rules /app/work/cleaning-rules.json --out-dir /app/output/data --result /app/output/result.json --json` |
| `scripts/check_cleaning.py` | 交付前 | `python3 {SKILL_DIR}/scripts/check_cleaning.py --result /app/output/result.json --root /app/output --json` |

`{SKILL_DIR}` 通常为 `/skills/weekly-skill`。清洗器只依赖标准库；会保留
`<field>__raw` 原始列、逐规则记录 `rows_affected` 与示例，并重算清洗前后质量指标。
不要为了指标好看而改 `quality_report`；应修规则或重跑。

## 执行步骤

1. **画像**：每字段统计类型、缺失率、唯一值数、异常值、单位分布、样例。
2. **定规则**：规则 id + 描述 + 作用字段 + 预期影响。常见：
   - `R1` 单位统一（如 kJ/mol → J/mol）
   - `R2` 命名规范（大小写、空格、同义词）
   - `R3` 去重（完全重复 / 主键重复）
   - `R4` 缺失处理（保留 null / 标记 / 不插补）
   - `R5` 异常值（超出物理范围 → 标记，不删除）
3. **执行**：把规则写入 `/app/work/cleaning-rules.json`，运行
   ```bash
   python3 {SKILL_DIR}/scripts/clean_table.py \
     --input /input/data.csv --rules /app/work/cleaning-rules.json \
     --out-dir /app/output/data --result /app/output/result.json --json
   ```
   清洗器会自动保留 `<field>__raw`、记录每规则 `rows_affected` 与 before/after 示例。
4. **质量报告**：清洗前后对比（缺失率、重复率、单位一致性）；由脚本重算，不手填。
5. **输出产物**：`cleaned.csv` + `cleaning_log.json` + `quality_report.json` + `result.json`。
6. **交付校验**：运行 `check_cleaning.py`，检查规则/日志/CSV/质量报告四者一致、
   raw 列存在、无静默删行；有 problem 先修，修不了标 `status=partial`。

## 输出契约

```json
{
  "case_id": "...",
  "schema": [{"name": "temperature", "dtype": "float", "unit": "°C",
              "missing_rate": 0.1, "unique": 12}],
  "rules": [{"rule_id": "R1", "description": "单位统一为 °C", "fields": ["temperature"]}],
  "cleaning_log": [
    {"rule_id": "R1", "rows_affected": 3,
     "examples": [{"row_id": "r2", "raw_value": "623 K", "clean_value": 350.0}]}
  ],
  "quality_report": {
    "before": {"rows": 10, "missing_rate": 0.18, "duplicate_rate": 0.1},
    "after": {"rows": 9, "missing_rate": 0.11, "duplicate_rate": 0.0},
    "unit_consistency": {"temperature": true},
    "outliers_flagged": [{"row_id": "r7", "field": "temperature", "value": 5000,
                          "reason": "超出物理范围"}]
  },
  "artifacts": [{"id": "cleaned", "path": "data/cleaned.csv", "format": "csv"},
                {"id": "log", "path": "data/cleaning_log.json", "format": "json"},
                {"id": "quality", "path": "data/quality_report.json", "format": "json"}],
  "missing_policy": [{"field": "selectivity", "policy": "keep", "reason": "不插补"}],
  "validation_rules": [{"field": "temperature", "min": -100, "max": 1500,
                        "passed": 3, "failed": 0}],
  "sample_flow": {"before": 0, "after": 0, "excluded": [{"row_id": "r2", "reason": "duplicate"}]},
  "analysis_gate": {"ready": false, "reason": "需人工确认异常值标记"},
  "support_files_used": ["references/data-cleaning-playbook.md",
                         "scripts/clean_table.py", "scripts/check_cleaning.py"],
  "uncertainty": [],
  "change_log": [{"step": 1, "action": "...", "evidence": "..."}]
}
```

## 清洗规则

1. **不删除原始数据**：异常值标记而非剔除；剔除需在日志中说明并保留行号。
2. **不做无依据插补**：缺失默认保留 `null`；若 task.md 要求插补，写明方法与理由。
3. 单位换算写换算系数，保留原始字符串。
4. 去重按业务主键，不按整行哈希（除非确认完全重复）。
5. 每个规则必须能算出 `rows_affected`；为 0 也要记录。
6. 标准化后的字段名与 task.md 要求一致，不做额外重命名。

## 异常处理

| 情况 | 处理 |
|---|---|
| 表头缺失/错位 | 按列位置与内容推断，标 `header_inferred: true` |
| 编码错误 | 尝试 utf-8 / gbk，记录采用的编码 |
| 单位混用 | 统一到 SI 或 task.md 指定单位，逐行记录换算 |
| 数据量超过预算 | 抽样画像 + 全量规则执行，报告中说明 |

## 自检

- 每条规则都有 `rows_affected` 与示例（为 0 也记录）
- `cleaned.csv` 存在且可解析；行数与 `quality_report.after.rows` 一致
- 被修改字段有 `<field>__raw` 原始列
- 单位换算有 factor/offset；异常值只标记不静默删除
- `quality_report` 前后指标可由 CSV/日志复算
- `check_cleaning.py` 返回 `ok: true`

## 快速路径（预算内）

1. **0–2 分钟**：读 task.md/case.json，确认输入表、目标单位、主键与输出格式。
2. **2–7 分钟**：先做字段画像（类型/缺失/唯一值/范围/单位），落盘 `schema[]` 与 `result.json` 骨架。
3. **7–13 分钟**：按规则执行清洗，边执行边写 `cleaning_log`。
4. **最后 2 分钟**：复算质量指标，确认原始值未丢失。

## 方法增强（融合参考实践）

- **画像六项**：类型、缺失率、唯一值、范围/异常值、单位分布、样例；先画像再定规则。
- **规则表**：每条规则有 `rule_id / description / fields / expected_effect`；规则先声明后执行。
- **原始值保留**：`raw_value` 与 `clean_value` 双列；任何删除/替换都能回溯到原行。
- **可逆清洗**：异常值标记而非删除；确需剔除时保留行号与理由。
- **无依据不插补**：缺失默认保留 null；task.md 要求插补时写明方法与理由。
- **清洗日志**：每规则记录 `rows_affected` 与 2–3 个 before/after 例子；为 0 也要记录。

### 定向方法增强

- **来自 数据分析流水线**：按 LOAD→CLEAN→EXPLORE→ANALYZE→REPORT 顺序执行，不跳步；加载即画像（shape/dtypes/missing/dupes/head/describe），画像未完成不定规则。
- **来自 数据分析流水线**：缺失处理要有明确策略与阈值——缺失率 >50% 考虑删列，5%–50% 按分布用中位数/众数填补并记录理由，<5% 可删行；任何选择都写 `missing_policy`，不得默认全填。
- **来自 数据分析流水线**：异常值用 IQR 规则识别，优先 winsorize/标记而不是删除；重复按业务主键删；类型转换失败的行单列，不静默变 null。
- **来自 SPSS 数据清洗**：缺失可选中位数/众数/回归/多重插补/cold-deck/hot-deck，但必须写方法、假设与影响；分类变量插补不得凭空造类别。
- **来自 SPSS 数据清洗**：清洗后做验证规则——范围（年龄 0–120）、逻辑（结束时间≥开始时间）、唯一（主键不重复）；每条规则给通过/失败行数与样例。
- **来自 SPSS 数据清洗**：重编码要留痕（连续→分类的切点、反向计分公式）；质量报告含前后 N、逐变量缺失表、带时间戳的操作日志；分析前设 `analysis_gate`，清洗未验收不得进入分析。
- **两篇合取后的改造**：`result.json` 增加 `missing_policy[]`、`validation_rules[]`、`recode_log[]`、`sample_flow{before, after, excluded}`、`analysis_gate`；`cleaning_log[]` 每项加 `timestamp`。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 规则 | 每条规则有 id、字段、影响行数 | 补日志；无影响的也记 0 |
| G2 | 原始值 | `raw_value` 与 `clean_value` 都可查 | 恢复原始列 |
| G3 | 单位 | 统一到 task.md/SI 单位，换算系数有记录 | 未换算的行标 `unit_mixed` |
| G4 | 质量报告 | 前后缺失率/重复率/单位一致性可复算 | 重算并修正 |
| G5 | 产物 | `cleaned.csv` 可解析，行数与日志一致 | 修文件或更新日志 |
| G6 | 边界 | 无无依据插补、无静默删除 | 回滚并标 `uncertainty` |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 静默删除异常值 | 行数减少但日志无记录 | 恢复行，改标记；确需删除则写理由 |
| 单位混用 | 同一列出现 K/°C 或 mg/g | 统一换算，逐行记录系数 |
| 去重误删 | 按整行哈希删掉不同样本 | 改用业务主键，保留差异行 |
| 编码错误 | 中文乱码/表头错位 | 尝试 utf-8/gbk，记录采用编码 |
| 表头错位 | 列含义与值不匹配 | 按列位置+内容重建表头，标 `header_inferred` |
| 无依据插补 | 缺失值被均值/中位数填满 | 改回 null，或写明方法与理由 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`schema[]`、`rules[]`、`cleaning_log[]`、
      `quality_report`、`artifacts`、`support_files_used`
- [ ] `cleaned.csv`、`cleaning_log.json`、`quality_report.json` 真实存在且可解析
- [ ] 每条规则有 `rows_affected` 与示例
- [ ] 原始值与清洗值都可追溯（`__raw` 或日志 raw_value）
- [ ] 单位换算有系数与来源
- [ ] 质量报告前后指标可复算；`check_cleaning.py` 通过
- [ ] 无静默删除、无无依据插补

## 最小示例（示例）

10 行数据，R1 统一温度单位影响 3 行，R3 去重删除 1 行；清洗后缺失率 18%→11%。
