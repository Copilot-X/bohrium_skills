# 统计分析与结果解读：执行规则

## 1. 任务口径

- 目标：统计方法选择、假设检查、效应量/区间与结果解读。
- 主产物：`output/analysis-report.md`。
- 必填字段：`analysis`, `results`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

方法与数据匹配；前提检查；报告数字与输出一致；不越界因果。

## 4. 产物

- 主产物必须非空、可编辑、可复核；
- 每个声明 artifact 必须真实存在；
- 分析类任务保留原始快照、规则/代码、日志与结果文件；
- 失败/跳过/权限不足要记录，不把未完成写成完成。

## 5. 失败模式

| 失败 | 后果 | 处置 |
|---|---|---|
| 以结果定义分母 | 指标虚高 | 先冻结参考集/输入，再计算 |
| 把未找到当不相关 | 遗漏被掩盖 | 单列未命中与原因 |
| 只给结论不给证据 | 无法复核 | 补 path/locator/quote |
| 合并不同对象 | 事实错误 | 保留 `keep_separate` 与依据 |
| 伪造在线结果 | 学术不端风险 | 只用输入材料，写缺口 |
| 静默改数据 | 结果不可复现 | 保留原始快照与变更日志 |

## 6. 本题指标与字段

- 清洗：原始快照 + `cleaned_table` + 规则日志，清洗前后计数闭合。
- 统计：方法、假设、效应量/区间、样本量与多重比较；前提不满足时降级说明。
- 图表：轴/单位/图例/数据来源齐全；导出 SVG/PNG 与数据文件一致。
- 代码/查询：真实执行，保留 `execution.log` 或查询语句；失败不伪造结果。
- 方法与数据匹配；前提检查；效应量/区间；报告数字与结果文件一致；不越界因果。

## 7. 本题执行器协议（uci_profile.py）

- 识别 Concrete/Iris/Wine 或 generic 表；建立派生快照与数据字典映射。
- 逐列缺失/重复/异常值/范围检查，只标记不删除。
- 描述统计 + 经典/Welch ANOVA + 成对 Welch + Holm + eta²/omega² +
  Hedges g + 95% CI；Concrete 另做与抗压强度的相关。
- Jarque-Bera（筛查）与 Brown-Forsythe 前提检查；不满足时降级说明。
- 生成 `work/analysis.py`（与 `output/analysis-code.py` 一致）并真实执行。
- 输出 `results.json`、`results.csv`、`statistics-report.json`、
  `environment.json`、`cleaned-table.csv`、`quality-report.json`、
  `cleaning-rules.json`、`figures/` 与 `analysis-report.md`。
- 所有数字来自同一次执行；报告不得另写一套数字。
