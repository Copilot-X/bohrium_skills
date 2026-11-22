# UCI 三数据集 profile 与检查口径

本题的固定数据形态是 Concrete（1030 行）、Iris（150 行，bezdek 源变体）
与 Wine（178 行）的 CSV 快照和真实 SQLite 数据库。`scripts/uci_profile.py`
不依赖文件名标签，而是用列签名、表名与取值类型联合识别。

## 1. 规范字段

### Concrete（9 列，抗压强度为目标）

| 规范字段 | 常见原始列名 | 单位 | 说明 |
|---|---|---|---|
| `cement` | Cement (component 1) | kg/m3 | 水泥用量 |
| `blast_furnace_slag` | Blast Furnace Slag (component 2) | kg/m3 | 高炉矿渣 |
| `fly_ash` | Fly Ash (component 3) | kg/m3 | 粉煤灰 |
| `water` | Water (component 4) | kg/m3 | 用水量 |
| `superplasticizer` | Superplasticizer (component 5) | kg/m3 | 减水剂 |
| `coarse_aggregate` | Coarse Aggregate (component 6) | kg/m3 | 粗骨料 |
| `fine_aggregate` | Fine Aggregate (component 7) | kg/m3 | 细骨料 |
| `age` | Age (day) | day | 养护龄期 |
| `compressive_strength` | Concrete compressive strength | MPa | 抗压强度 |

### Iris（5 列）

| 规范字段 | 常见原始列名 | 单位 |
|---|---|---|
| `sepal_length` | sepal length in cm / SepalLengthCm | cm |
| `sepal_width` | sepal width in cm | cm |
| `petal_length` | petal length in cm | cm |
| `petal_width` | petal width in cm | cm |
| `species` | class / Species / variety | 类别标签 |

无表头文件若为 5 列且文件名含 iris，按
`sepal_length, sepal_width, petal_length, petal_width, species` 解释。

### Wine（14 列，首列通常为类别）

| 规范字段 | 常见原始列名 |
|---|---|
| `wine_class` | class / cultivar / wine_class |
| `alcohol` | Alcohol |
| `malic_acid` | Malic acid |
| `ash` | Ash |
| `alcalinity_of_ash` | Alcalinity of ash |
| `magnesium` | Magnesium |
| `total_phenols` | Total phenols |
| `flavanoids` | Flavanoids |
| `nonflavanoid_phenols` | Nonflavanoid phenols |
| `proanthocyanins` | Proanthocyanins |
| `color_intensity` | Color intensity |
| `hue` | Hue |
| `od280_od315` | OD280/OD315 of diluted wines |
| `proline` | Proline |

原始 Wine 数据的多数指标未标注单位；报告不会替数据补一个未经证实的单位。

## 2. 识别规则

1. 规范化列名：转小写、`µ/μ` 统一、括号与标点折叠为下划线。
2. 用别名模式匹配规范字段；`class/species/cultivar` 先记为 `class_label`，
   再按数据集映射为 `species` 或 `wine_class`。
3. 命中率 ≥0.6，或文件名/表名提示且命中率 ≥0.3 时判为该数据集；否则归为
   generic。
4. 无表头判定：首行全部可转数值，或文件名为已知数据集且首行大多为数值
   （Iris 首行为 4 个数值 + 类别标签）。
5. 派生快照的表名使用 `concrete/iris/wine`；多份同数据集表加 `_2` 后缀。

## 3. 查询口径

- 默认主表：题目文字明确点名时用点名数据集；否则选行数最大的已识别表。
- Iris：`GROUP BY species`，输出 n、各测量列均值/最小/最大。
- Wine：`GROUP BY wine_class`，输出 n、酒精/苹果酸/类黄酮/颜色强度/脯氨酸均值。
- Concrete：按龄期段分组，输出 n、强度均值/极值、水泥与用水均值。
- generic：优先按 2–12 个取值的类别列分组；没有则整体聚合。
- 期望行数 = 基准表的组数（Concrete 为龄期段数）；实际行数与之一致才
  令 `row_count_check.ok=true`。
- 结果为空时 `empty_result.is_empty=true`；基准组未返回时列入
  `missing_groups`，不把空组写成“不存在”。
- 跨表：只检查共享列名。每键最大行数 >1 标记一对多膨胀风险；没有共享键时
  明确写“跨表连接不适用”。

## 4. 质量检查口径

| 检查 | 口径 | 处置 |
|---|---|---|
| 缺失 | 空串、NA/N/A/NaN/null/None/?/-/--/missing 等 | 保留为缺失；统计每列缺失率 |
| 完全重复 | 全部列取值相同 | 计数并保留；不静默去重 |
| 重复键 | 列名形如 id/`*_id`/sample/record/index | 计重复键数 |
| 异常值 | Tukey 1.5*IQR 与 \|z\|>3 | 两套口径分别计数；仅提示 |
| 范围筛查 | 保守领域边界（浓度非负、龄期 1–3650 天、强度 0–200 MPa 等） | 越界只提示复核 |
| 疑似哨兵 | -999/999/9999/-9999 的重复出现 | 提示确认，不自动替换 |
| 常量列 | 非缺失值只有一个取值 | 报告，不删除 |

## 5. 复跑与证据

- 快照哈希：源 SQLite 或派生快照的 SHA-256；派生快照另列每个源文件哈希。
- SQL 哈希：实际执行 SQL 的 SHA-256；报告中的 SQL 与 `output/query.sql` 一致。
- 代码：`work/analysis.py` 与 `output/analysis-code.py` 字节一致；
  `work/uci-run.json` 固定本次快照路径与表/列映射。
- 数字：报告只引用 `output/results.json` 与 `output/result-table.csv` 中的值。
- 字典：输入字典行优先；没有对应行时写入 `work/uci-dictionary.md` 并标注
  “未在提供的数据字典中描述”，不冒充输入事实。

## 6. 已知限制

- 列签名识别对列名高度改写、换语言或换单位不敏感，可能归为 generic。
- 保守范围边界只用于提示，不能替代领域专家判断。
- 跨表检查基于共享列名，不能推断未命名的外键关系。
- 没有共享键时会话中只有一个表时，跨表分析不适用，报告会明确说明。
- 无文字层/加密的二进制快照无法解析时，写 `input_error` 或跳过并说明。

## 7. 代码生成与执行口径（code-generation）

- `scripts/uci_core.py` 被原样复制为 `work/analysis.py` 与
  `output/analysis-code.py`；两份代码字节一致，finalize 校验 SHA-256。
- 生成代码只依赖 Python 标准库（sqlite3/csv/statistics/math/json）；
  numpy 只在 try/except 中用于可选交叉校验，缺失时不影响结果。
- 运行配置写入 `work/uci-run.json`：模式、快照绝对路径、表/列映射、
  分组列与目标列；生成代码只读该配置与派生快照。
- 运行日志写入 `output/execution.log`：命令、cwd、Python 版本、退出码、
  耗时、stdout/stderr；finalize 会再次执行 `work/analysis.py`。
- 验证：
  - 行数：SQL `COUNT(*)` 与 Python 行数逐表对照；
  - 均值：SQL `AVG(col)` 与 Python 均值逐数值列对照（相对容差 1e-6）；
  - 自检：空值列表、缺列、非数值、零方差、样本不足、只读 SQL 守卫；
  - 校验结果写 `results.json.validation`；任何 fail 都进入 uncertainties。
- 环境：`output/environment.json` 记录 Python 实现/版本、平台、SQLite 版本、
  可选 numpy 版本、确定性标志与命令；不声明未运行的环境。
- 错误策略：缺数据/缺列/非数值/零方差/空结果都写出 status 与原因；
  不静默补值；不可恢复的 I/O 错误才使用非零退出码。
- 结果：`results.json`、`results.csv`、`statistics-report.json`、
  `cleaned-table.csv`、`quality-report.json`、`cleaning-rules.json`、
  `figures/` 与 `analysis-report.md` 的数字来自同一次执行。
