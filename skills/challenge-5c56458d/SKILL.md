---
name: analysis-database-query-skill
description: 科研数据库查询与分析：在只读快照上真实执行 SQL，导出结果表，核对行数、字段映射、已登记组的空结果与多表连接膨胀，并保留快照 SHA-256、SQL 哈希和执行日志。Executable read-only SQL runner (sqlite3) with snapshot hash, row/group/fan-out checks, field-mapping evidence, contract fields and result.json. Use for 科研数据库查询与分析、database-query、SQL 可复跑、字段映射、空结果、跨表分析.
---

# 科研数据库查询与分析（analysis track · database-query）

## 用途与触发

task.md 要求“查询设计、模式/字段映射、结果校验与空结果处理”，或 `/input/case.json` 给出 `analysis` / `database-query`
相关字段时使用。本题属于 **数据库查询** 类任务，核心交付是
**SQL、字段映射、结果表与行数校验**，不是一段泛泛建议。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代
同行评审/机构审查/临床决策。材料不足时只交付可确认部分，并把缺口写成
`uncertainty`，不要补造。

核心原则：**有输入、有依据、有产物、有边界**。脚本负责盘点材料、建立
工作区、校验证据/字段/产物并组装 `result.json`；模型负责判断、筛选、
分析或写作。脚本不会替你生成候选、结论或审查意见。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：本题的具体问题、范围、输出要求与证据范围。
- `/input` 下的材料：`.md/.txt/.json/.csv/.pdf/.docx/.tex/.yaml` 等。
  `scripts/parsers.py` 会提取文本、JSON 数据与 CSV 行；无文字层 PDF
  尝试 OCR，失败如实记录。
- 可选：已有工作文件、术语表、数据字典、候选列表、查询日志。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | Skill 入口：prepare / finalize / run / inspect |
| `scripts/db_query.py` | **本题核心执行器**：只读执行 `work/query.sql`，导出结果表，核对行数/组覆盖/连接膨胀/字段映射，回填 `work/analysis-plan.json` |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/analysis-plan.json` 的字段契约 |
| `references/analysis-rules.md` | 数据库查询 的可执行规则与失败模式 |
| `examples/synthetic-input.json` | synthetic work 片段示意（非正式案例） |

## 工作流

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，提取文本/JSON/CSV，写 `work/inventory.json` 与
   `work/materials.json`；
2. 生成 `work/analysis-plan.json` 骨架（已存在则不覆盖）；
3. 写 `output/README-partial.md` 与 provisional `result.json`。

先读 `work/materials.json` 与 `task.md`，确认材料边界和缺失项。

### 第 2 步：生成并真实执行查询（约 60% 预算）

**先看 schema，再写 SQL。** 运行检查器（只读，不产生副作用）：

```bash
python3 "$SKILL_DIR/scripts/db_query.py" --inspect \
  --input /input --work /app/work --output /app/output
```

如果提供的是 SQLite，它会打印表、列、类型与行数；如果只有 CSV，它会先把
CSV 载入一个私有 SQLite 文件（`work/csv-snapshot.sqlite`）并打印同样的 schema。
它还会显示 `work/csv-snapshot.sqlite` 的路径与源文件哈希。

然后：

1. 阅读 `query-request.md` / `task.md`、`schema.sql`、`data-dictionary.md`；
2. 把**最终要执行的 SQL** 写进 `work/query.sql`（单条 SELECT/WITH）；
3. 运行执行器：

```bash
python3 "$SKILL_DIR/scripts/db_query.py" \
  --input /input --work /app/work --output /app/output
```

执行器是本题的核心真实入口，它会：

- 以 `file:...?mode=ro` + `PRAGMA query_only=ON` 打开快照，记录路径、
  大小、SHA-256、SQLite 版本；
- 只执行 `work/query.sql`，拒绝写操作和多语句；把实际执行的 SQL
  原样写入 `output/query.sql` 并记录 `sql_sha256`；
- 把完整结果集写入 `output/result-table.csv`，把列名、行数、前 2000 行、
  数值列均值等写入 `output/results.json`；
- 自动核对：
  - `row_count_check`：实际行数与 `work` 中声明的期望行数是否一致；
  - `empty_result`：结果为空时显式说明；输出列与基表同名列的已登记组
    比对，缺失组逐个列出（例如 `C` 组）；
  - `join_checks`：多表查询时按共享键计算每键最大行数，标出一对多膨胀
    风险以及是否已被 `GROUP BY`/`DISTINCT`/聚合缓解；
  - `schema_mapping`：把 `data-dictionary.md` 的字段逐条映射到真实
    `table.column`，记录字典原文、行号、是否出现在查询/结果中；
- 生成 `output/execution.log` 与 `output/analysis-report.md`（报告中的
  每个数字都来自同一份 `results.json`，不手填）；
- 把 `query/sql_sha256/snapshot/schema_mapping/row_count_check/
  empty_result/permissions/results/evidence` 回填到
  `work/analysis-plan.json`，供 finalize 复核。

如果 SQL 报错，执行器不会伪造结果：它写入 `status=query_error`、
错误信息、可用表/列提示和非零退出码。按提示修正 SQL 后重跑即可。

如果需要补充解释，编辑生成的 `output/analysis-report.md` 与
`work/analysis-plan.json`，但不要改动已执行 SQL 或结果数字；需要改口径时
改 SQL 并重新执行。

工作文件的关键字段（`templates/work.schema.json` 给出结构）：

```json
{
  "case_id": "SYN-DB-001",
  "mode": "database-query",
  "summary": "执行结果摘要",
  "query": "SELECT ...",
  "sql_sha256": "…",
  "snapshot": {"path": "lab.sqlite", "sha256": "…", "bytes": 12288, "read_only": true},
  "schema_mapping": {"samples.group_name": {"dictionary_description": "…", "schema_table": "samples", "schema_column": "group_name"}},
  "row_count_check": {"expected": 3, "actual": 3, "ok": true, "source": "command_line"},
  "empty_result": {"is_empty": false, "missing_groups": ["C"], "statement": "…"},
  "permissions": {"status": "read_only", "write_allowed": false},
  "results": [{"id": "R001", "metric": "result_row_count", "value": 3, "evidence": {"path": "query-request.md", "locator": "L1", "quote": "For every group in samples, return:"}}],
  "evidence": [{"path": "data-dictionary.md", "locator": "L5", "quote": "measurements.value: numeric result; -999 is the missing code and must be excluded."}],
  "uncertainties": []
}
```

必填字段：`query`, `sql_sha256`, `snapshot`, `schema_mapping`,
`row_count_check`, `empty_result`, `permissions`, `results`, `evidence`。
缺任何一个，finalize 会给出 `partial`，不要用空对象占位。

同时必须存在的产物：

- `output/analysis-report.md`（主交付物）
- `output/query.sql`
- `output/result-table.csv`
- `output/results.json`
- `output/execution.log`

- 所有 `quote` 必须能在对应 `path` 的材料中逐字（允许空白折叠/省略号分段）
  找到；执行器只从原始材料/生成结果中摘取原文。
- 空结果和权限必须明确；不能把“查询没返回”写成“数据不存在”。
- 行数、组数、均值等数字以 `output/results.json` 为准，报告不得另写一套。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/analysis-plan.json`，检查必填字段 `query`, `sql_sha256`,
   `snapshot`, `schema_mapping`, `row_count_check`, `empty_result`,
   `permissions`, `results`, `evidence`；
2. 逐条核验 evidence 的 path/locator/quote；错误路径或找不到的引文会降级；
3. 核对 `work.query` 非空，并把 `query.sql`、结果表、`results.json`、
   `execution.log` 等声明产物登记为 artifact；
4. 检查 `output/analysis-report.md` 非空、声明的每个产物真实存在；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`；原始
   `output_contract` 键（`sql_text`/`result_table`/`field_mapping`/
   `empty_result_handling`/`snapshot_version`）由工作文件同名键覆盖，
   未覆盖的键会降级 `partial`。

执行器已经真实运行过 SQL；finalize 只是复核和封装，不会替你补造查询结果。

`status` 取 `completed|partial|input_error`。有缺字段、证据
不匹配、主产物缺失或执行失败时用 `partial`。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/analysis-report.md` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings`/`candidates`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote；
- `artifacts[]`：主产物与 `output/query.sql`, `output/result-table.csv`, `output/results.json`, `output/execution.log`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`。

若 `/input/case.json` 的 `output_contract` 含本题特有键（常见为
`sql_text`、`result_table`、`field_mapping`、`empty_result_handling`、
`snapshot_version`），必须：

1. 在 `result.json` 顶层保留原键名；
2. 对应 canonical 字段/产物已由执行器写入 `work/analysis-plan.json`：
   `sql_text`（SQL 原文）、`result_table`（`output/result-table.csv`）、
   `field_mapping`（字典→schema 映射）、`empty_result_handling`（空组
   声明）、`snapshot_version`（路径+SHA-256+SQLite 版本）；
3. 执行器还会按 `case.json` 的键名补充映射；finalize 检查
   `contract_coverage`，未覆盖的键会降级 `partial` 并写入
   `uncertainties`。不要只写 `status/result/claims` 而忽略原契约字段。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/analysis-report.md` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `query`, `sql_sha256`, `snapshot`, `schema_mapping`, `row_count_check`, `empty_result`, `permissions`, `results`, `evidence` 齐全且非空 |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查；locator 指向真实行 |
| G4 | 边界 | 未报告、冲突、失败、未检查项写入 `uncertainties` |
| G5 | 专项 | SQL 在快照上真实执行；`work.query` = `output/query.sql` = 实际执行 SQL；行数/组覆盖/连接膨胀/权限/空结果均有结构化字段 |
| G6 | 产物 | 声明的每个 artifact 路径真实存在 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 查询设计、模式/字段映射、结果校验与空结果处理；查询可复跑；结果与问题对应；权限/空结果明确 | `output/analysis-report.md` + evidence |
| 使用增益 | 可复跑的查询/步骤/规则/日志，以及同条件对照 | `output/query.sql`, `output/result-table.csv`, `output/results.json`, `output/execution.log` |
| 创新增益 | 本题专项方法（database-query）及其与基线方案的边界 | `references/analysis-rules.md` + `output/analysis-report.md` |
| 可靠体验 | 空结果、冲突、缺字段、权限/格式错误的识别与降级 | `uncertainties` + `output/result.json` |
| 生态贡献 | SKILL.md、schema、规则、synthetic 示例可被他人复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；不生成伪候选/结论/审查意见 |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要”；`status=partial` |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 候选/文献重复 | 用 DOI/标题+年份合并；保留合并依据 |
| 空结果/无命中 | 明确写“未检索到/未命中”，不把空白写成排除 |
| 数字/统计不一致 | 以数据文件为准；报告与结果文件对齐 |
| 代码执行失败 | 保留 `execution.log` 与失败原因；不伪造输出 |
| 权限/接口/格式错误 | 写 `uncertainty` 与已完成范围；建议人工接管 |

## 最小调用示例（synthetic，不是正式答案）

完整可运行的小例子在 `examples/synthetic-case/`：包含 `case.json`、
`query-request.md`、`data-dictionary.md` 和一个 CSV 快照。复现命令：

```bash
CASE="$SKILL_DIR/examples/synthetic-case/input"
WORK=/tmp/p4-db-work; OUT=/tmp/p4-db-out
rm -rf "$WORK" "$OUT"; mkdir -p "$WORK" "$OUT"
python3 "$SKILL_DIR/scripts/run_case.py" prepare --input "$CASE" --work "$WORK" --output "$OUT"
cat > "$WORK/query.sql" <<'SQL'
SELECT group_name,
       COUNT(*) AS n_rows
FROM measurements
WHERE value <> -999
GROUP BY group_name
ORDER BY group_name;
SQL
python3 "$SKILL_DIR/scripts/db_query.py" --input "$CASE" --work "$WORK" --output "$OUT"
python3 "$SKILL_DIR/scripts/run_case.py" finalize --input "$CASE" --work "$WORK" --output "$OUT"
```

示意输出（该 synthetic CSV 只有 2 行，数字仅用于说明接口，不是正式案例
答案）：

```json
{
  "status": "ok",
  "row_count": 1,
  "columns": ["group_name", "n_rows"],
  "missing_groups": ["B"],
  "row_count_ok": true,
  "artifacts": ["output/query.sql", "output/result-table.csv",
                "output/results.json", "output/execution.log",
                "output/analysis-report.md"],
  "work_file": "/tmp/p4-db-work/analysis-plan.json"
}
```

`examples/synthetic-input.json` 只是 work 字段片段示意（不是可执行答案），
`examples/synthetic-case/` 才是完整的输入→命令→产物链路。示例中的实体与
数值不得复制到正式案例。

## 版本与限制

- 版本 `1.1.0`：单入口 + `db_query.py` 真实执行器 + 共享引擎；模型写
  SQL 与解释，脚本负责执行、导出、行数/组覆盖/连接检查与回填。
- 支持提供的 SQLite 快照；若只有 CSV，会自动载入
  `work/csv-snapshot.sqlite`（NUMERIC 列按数值推断），再执行同一份 SQL。
- 只读：拒绝 INSERT/UPDATE/DELETE/DDL/PRAGMA 和多语句；不写入源库。
- 不联网：只使用 `/input` 提供的材料或导出，不伪造在线查询结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断；字段语义仍以数据字典为准。
- `examples/synthetic-case/` 是 synthetic 可运行示例，不是正式案例答案。
