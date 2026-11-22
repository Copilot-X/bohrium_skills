# 科研数据库查询与分析：执行规则

## 1. 任务口径

- 目标：查询设计、模式/字段映射、结果校验与空结果处理。
- 主产物：`output/analysis-report.md`。
- 必填字段：`query`, `results`, `evidence`。

## 2. 证据

1. 每条关键判断/候选/步骤必须有 `path + locator + quote`；
2. `quote` 必须逐字来自材料；允许空白折叠与省略号分段，不允许改写；
3. 找不到原文时写入 `uncertainties`，不要用常识补全；
4. 同一材料的不同版本/格式不构成独立证据；
5. 外部未提供的文献、DOI、数值、设备状态不得编造。

## 3. 专项检查

查询可复跑；结果与问题对应；权限/空结果明确。

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
- 查询可复跑；结果与问题对应；权限/空结果明确；字段映射可核对。

## 7. 本题执行器协议（uci_profile.py，首选）

- 直接读取真实数据：SQLite 表结构或 CSV/TSV/JSON/XLSX 表头；按列签名识别
  Concrete/Iris/Wine，不依赖文件名标签。
- 建立 `work/uci-snapshot.sqlite` 派生快照（含源文件 SHA-256 清单）；主表
  来自提供的 SQLite 时，在同一原库上以只读模式执行查询。
- 解析输入 `data-dictionary.md` 的逐条字段；缺失字段写入
  `work/uci-dictionary.md` 并标注“未在提供的数据字典中描述”。
- 逐列做缺失/重复/异常值/范围/疑似哨兵检查，只标记不删除。
- 自动生成并按数据集执行一条只读 SELECT；若 `work/query.sql` 已存在且合法，
  优先执行它；执行报错时记录错误并回退自动查询。
- 输出 `query.sql`、`result-table.csv`、`results.json`、`execution.log`、
  `analysis-report.md`，并回填 `query/sql_sha256/snapshot/schema_mapping/
  row_count_check/empty_result/permissions/results/evidence/sql_text/
  result_table/field_mapping/empty_result_handling/snapshot_version`。
- 生成 `work/analysis.py`（与 `output/analysis-code.py` 一致），finalize 会
  再次真实执行，保证结果可复跑。

## 8. 手写 SQL 兼容路径（db_query.py）

- 先 `--inspect` 读取真实 schema；SQLite 快照直接只读打开，纯 CSV 会载入
  `work/csv-snapshot.sqlite`（列类型按样本推断），表名取文件 stem 的小写
  蛇形形式。
- SQL 写在 `work/query.sql`，只允许单条 SELECT/WITH/EXPLAIN；拒绝
  INSERT/UPDATE/DELETE/DDL/PRAGMA/ATTACH 和多语句。
- 执行器输出并回填：
  - `query`、`sql_sha256`、`snapshot{path,sha256,bytes,sqlite_version,read_only}`
  - `schema_mapping`：字典行 → 真实 `table.column`，带原文 quote 与 `L<n>`
  - `row_count_check{expected,actual,ok,source}`
  - `empty_result{is_empty,missing_groups,statement}`：按输出列与基表同名列
    的已登记组比对，空组显式列出
  - `join_checks`：共享键每键最大行数与聚合缓解标志
  - `permissions`、`results`、`evidence`
- 报告/结果数字只来自 `output/results.json`；不得手填别的一套。
- SQL 报错时写 `status=query_error` + 可用表/列提示，修正后重跑；不伪造结果。
