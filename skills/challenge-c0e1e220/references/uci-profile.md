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

## 7. 统计执行口径（statistics / code-generation）

- 描述统计：n、缺失、均值、SD、SE、min、Q1、median、Q3、max、IQR、
  偏度、峰度、均值 95% t 区间。
- 分组：Iris 用 `species`，Wine 用 `wine_class`，Concrete 用龄期段
  `<=28 / 29-90 / 91-180 / >180 天`；generic 用 2–12 个取值的类别列。
- 推断：同一指标同时计算经典 one-way ANOVA 与 Welch ANOVA；
  Brown-Forsythe p<0.05 时主检验取 Welch ANOVA。
- 成对：Welch t，报告均值差与 95% CI、Cohen's d、Hedges g；每个指标内
  与指标族层面都做 Holm 校正。
- 效应量：one-way eta² 与 omega²；eta² 分档仅作解释辅助：
  <0.01 极小、<0.06 小、<0.14 中等、其余大。
- 前提：每组 Jarque-Bera（n>=8）筛查正态性；Brown-Forsythe 筛查方差齐性；
  组内 n<5 标记为探索性。
- 相关：Concrete 计算配方指标与抗压强度的 Pearson r、Fisher z 95% CI 与
  Holm；其余数据集默认报告各指标与目标指标的相关。
- 敏感性：对主指标剔除 Tukey 1.5*IQR 异常值后重算 ANOVA；主分析保留全部
  数据，两者并列报告。
- 缺失：按变量完整观测（complete case per variable），不插补、不当 0；
  每个统计量报告自己的 n。
- 重复：完全重复行保留并计数，不静默去重；在不确定性中提示。
- 代码：`work/analysis.py` 与 `output/analysis-code.py` 字节一致；
  只依赖标准库；numpy 仅用于可选交叉校验。
- 图：`output/figures/group-means-<dataset>.svg` 与同源 CSV；图为组均值
  点 + 95% CI 误差线，轴单位写入 `figures[].units`。
