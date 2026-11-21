# 研究前沿与趋势分析：执行规则

## 1. 任务口径

- 目标：时间趋势、主题演化、研究前沿与证据边界。
- 主产物：`output/research-report.md`。
- 必填字段：`time_window`, `year_counts`, `topic_counts`, `representative_rule`, `findings`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

趋势基于可核对的时间/数量；不把共现当因果；窗口明确。

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

- 结论：每条有 `statement/sources/limitations/evidence`；区分观测、推断与空白。
- 证据矩阵：按维度分组比较，保留每篇的结论、条件与局限；冲突并列。
- 系统综述：PRISMA 式检索/纳排/纳入计数闭合；每个排除有理由码。
- 趋势/选题：时间窗、检索范围与数量口径明确；不把共现当因果，不夸大创新性。
- 趋势/计数可复算；窗口与分母明确；代表作规则允许低被引首创工作。
- 每个趋势结论标 evidence_type(count/co-occurrence/qualitative)，禁止共现推因果。

## 7. 可复算算法（按顺序执行）

1. **读入行**：`papers.csv` 的每一行是一条导出记录；`paper_id` 为唯一标识。
2. **识别重复**：优先 `duplicate_of`；没有该列时读 `notes` 中的
   `duplicate of <ID>`。重复行不进入 unique 集，但保留在
   `data_quality.duplicates_removed`。
3. **计算分母**：
   - `rows = 输入行数`
   - `duplicates = 被标记为重复的行数`
   - `unique = rows - duplicates`
   - `dated = unique 中 year 可解析为整数的记录数`
   - `missing_year = unique - dated`
4. **年度计数**：对 dated 按 year 分组计数。`sum(count)=dated`；
   每个数字年份 `denominator=dated`。缺年份单独一行
   `{"year":"missing","count":missing_year,"denominator":unique}`。
5. **主题计数**：先按 `topic_mapping` 归并，再对 unique 计数。
   `sum(topic_counts.count)=unique`；每个 `denominator=unique`。
   多标签主题在 `topic_rule` 中写明“一篇可计入多个主题”，此时允许
   sum 大于 unique，但必须显式说明。
6. **代表作**：先按 `time_window.stages` 分阶段，再在每阶段内按
   “方法首创/基准/综述 + 被引”排序；被引只作同角色内的平局处理。
7. **空白**：先写下假设空白，再逐条排除“同义词未检索、数据库未覆盖、
   时间窗截断、语言限制”；排除不了的不算空白，写入 `uncertainties`。

## 8. 算例（synthetic，仅演示口径）

输入 6 行：P1(2020)、P2(2020)、P3(2021)、P4(2021)、P5(2022)、P6(缺年份)，
其中 P3 的 notes 写 `duplicate of P2`。

| 量 | 值 | 说明 |
|---|---|---|
| rows | 6 | 原始导出行 |
| duplicates | 1 | P3 |
| unique | 5 | P1,P2,P4,P5,P6 |
| dated | 4 | P1,P2,P4,P5 |
| missing_year | 1 | P6 |
| year_counts | 2020:2, 2021:1, 2022:1, missing:1 | 数字和=4=dated |
| topic_counts 分母 | 5 | unique |

若直接写 `year_counts={2022:1}` 或把分母写成 rows=6，预检会失败。
`sum(year_counts)=dated` 与 `rows-duplicates=unique` 是最低复算条件。

## 9. 因果措辞黑名单

`count` 与 `co-occurrence` 证据禁止出现：导致、使得、造成、推动、驱动、
引发、归因于、because of、due to、led to、drives、resulted in。
需要写机制时，改为：

- “在同一批文献中，A 与 B 同时出现（co-occurrence，不能说明因果）”；
- “原文作者提出 X 是可能机制（qualitative，单篇观点）”；
- “若需检验因果，需要干预/消融实验或时间序列识别策略（gap）”。

## 10. 预检脚本

`scripts/audit_work.py` 会读取输入 CSV 与 `work/research-plan.json`，
自动复算第 7 节的全部计数，并检查代表作阶段覆盖、`evidence_type`、
空白依据与逐字证据。退出码非 0 时先修工作文件，不要 finalize。
