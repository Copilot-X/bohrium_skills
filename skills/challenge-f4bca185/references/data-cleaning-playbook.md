# 文献数据清洗参考

配合 `templates/cleaning-rules.json`、`scripts/clean_table.py`、
`scripts/check_cleaning.py` 使用。**task.md 与 case.json 优先**；原始值不可丢，
任何删除/替换都要在日志里能回溯到行。

## 1. 先画像再定规则

每个字段统计：

- 类型（numeric / text / date / mixed）
- 缺失数与缺失率
- 唯一值数
- 数值范围/异常值
- 单位分布与样例
- 主键候选（唯一、非空）

画像未完成不要开始填缺失或删行。

## 2. 规则表

每条规则先声明后执行：

```json
{"rule_id":"R1","type":"unit_convert","field":"temperature",
 "from_unit":"K","to_unit":"°C","factor":1,"offset":-273.15}
```

支持的类型：

| type | 作用 | 关键字段 |
|---|---|---|
| `trim` | 去首尾空白 | `fields` |
| `normalize_name` | 小写/下划线/同义词 | `fields` |
| `rename` | 字段重命名 | `mapping` |
| `unit_convert` | 单位换算 | `field/from_unit/to_unit/factor/offset` |
| `dedupe` | 按业务主键去重 | `keys/keep` |
| `missing` | 缺失保留/删除/填补 | `field/policy` |
| `range` | 物理范围检查 | `field/min/max/action` |
| `outlier` | IQR/固定阈值标记 | `field/method/action` |

规则执行顺序即数组顺序；`dedupe` 通常在标准化之后。

## 3. 原始值保留

- 每个被修改字段保留 `<field>__raw` 列（原始字符串）。
- `cleaning_log[].examples[]` 至少给 2 个 `row_id/raw_value/clean_value`。
- 删除行时记录 `row_id` 与理由；不得静默删除。
- 异常值默认标记（`flags` 列），不删除；确需剔除要单独规则并写理由。

## 4. 缺失与异常

- 缺失默认保留 `null`；不无依据插补。
- 若 task.md 要求插补：写 `policy`（median/mode）、方法、影响行数；分类变量不得造新类别。
- 范围检查用物理/业务范围；IQR 只标记，不自动 winsorize（除非 task.md 要求）。
- 类型转换失败的行单独标记，不静默变 null。

## 5. 质量报告

清洗前后至少给：

- `rows`、`missing_rate`、`duplicate_rate`；
- `unit_consistency`（每个做过单位换算的字段）；
- `outliers_flagged`（row_id/field/value/reason）；
- `rules_applied` 与 `rows_affected`。

指标要能从 `cleaned.csv` 与日志复算，不能手写一个“提升百分比”。

## 6. 常见失败模式

| 信号 | 问题 | 修复 |
|---|---|---|
| 行数减少但日志无记录 | 静默删除 | 恢复行或补删除日志 |
| 同列 K/°C 混用 | 单位未统一 | 逐行换算并保留 raw |
| 按整行哈希去重 | 误删不同样本 | 改业务主键 |
| 缺失被均值填满 | 无依据插补 | 改回 null 或写方法 |
| 异常值直接删 | 丢失证据 | 改为标记 |
| 质量指标无法复算 | 报告编造 | 从 cleaned.csv 重算 |

## 7. 交付前检查

- [ ] 每条规则有 `rows_affected` 与示例。
- [ ] `cleaned.csv` 可解析，行数与日志一致。
- [ ] 每个修改字段有 `__raw` 或日志 raw_value。
- [ ] 单位换算有 factor/offset。
- [ ] 质量报告前后指标可复算。
- [ ] 无静默删除/无依据插补。
- [ ] `check_cleaning.py` 返回 `ok: true`。
