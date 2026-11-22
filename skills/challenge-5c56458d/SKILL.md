---
name: analysis-database-query-skill
description: 科研数据库查询与分析：自动识别 UCI Concrete(1030)/Iris(150)/Wine(178) 的 CSV 或真实 SQLite 快照，按数据字典映射字段，在只读快照上生成并执行 SQL，导出结果表，核对行数、组覆盖、空结果与跨表共享键，保留快照 SHA-256、SQL 哈希和执行日志。Executable read-only UCI SQL profiler with dictionary mapping, quality checks and result.json. Use for 科研数据库查询与分析、database-query、SQL 可复跑、字段映射、空结果、跨表分析.
---

# 科研数据库查询与分析（analysis track · database-query）

## 用途与触发

task.md 要求“查询设计、模式/字段映射、结果校验与空结果处理”，或 `/input` 出现
SQLite/CSV 的科研数据快照时使用。本题的核心交付是**真实执行的 SQL、字段映射、
结果表与行数校验**，不是一段泛泛建议，也不是手填的数字表。

优先处理的固定数据形态（题面末尾已写明）：

- Concrete：1030 行，9 列（水泥、矿渣、粉煤灰、水、减水剂、粗/细骨料、龄期、
  抗压强度）；
- Iris：150 行，4 个测量列 + 类别（bezdek 源变体）；
- Wine：178 行，13 个理化指标 + 类别编码。

同一份 Skill 也适用于列名不同的同结构 CSV/TSV/JSON/XLSX 与真实 SQLite：
脚本按列签名和文件/表名识别数据集，不依赖题目文字里的标签。

不适用：输入没有任何可读表；要求联网查询未提供的数据；要求替代同行评审、
机构审查或临床决策。材料不足时只交付可确认部分，并写入 `uncertainties`。

核心原则：**先执行、再解释；数字只来自快照；缺失与失败不静默**。

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | 统一入口：prepare / finalize / run / inspect |
| `scripts/uci_profile.py` | **本题主执行器**：发现表 → 识别 UCI 数据集 → 建立数据字典映射 → 质量检查 → 生成并只读执行 SQL → 写结果表/报告/工作文件 |
| `scripts/uci_core.py` | 自包含分析引擎；原样生成 `work/analysis.py`，由 finalize 再次真实执行 |
| `scripts/db_query.py` | 旧的手写 SQL 执行器：当你已有一份明确的 `work/query.sql` 时使用；支持 `--inspect` 查看 schema |
| `scripts/skillcore.py` | result.json 信封、证据/数字审计、产物登记 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/analysis-plan.json` 的字段契约 |
| `references/analysis-rules.md` | database-query 的通用执行规则与失败模式 |
| `references/uci-profile.md` | UCI 三数据集的规范字段、识别规则、检查口径与限制 |
| `examples/synthetic-input.json` | synthetic work 片段示意（非正式案例） |
| `examples/synthetic-case/` | 可运行的 synthetic 输入→命令→产物链路（非正式案例） |
| `examples/synthetic-uci/` | 可运行的 synthetic UCI 形状示例（含缺失值/重复行，非正式案例） |

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：本题问题、范围与证据要求。
- `/input` 下的数据：`.sqlite/.sqlite3/.db`、`.csv/.tsv/.data`、`.json`、
  `.xlsx`；以及可选 `data-dictionary.md`、`schema.sql`、`query-request.md`。
- 脚本不信任文件名标签：先读表头/列签名，再判断是 Concrete、Iris、Wine
  还是 generic 表。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 工作流

### 第 1 步：prepare（约 5% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

产出 `work/inventory.json`、`work/materials.json`、`work/analysis-plan.json`
骨架与 provisional `result.json`。先看 `work/materials.json` 和图谱，确认
哪些是数据、哪些是数据字典。

### 第 2 步：运行 UCI profile（约 65% 预算）

```bash
python3 "$SKILL_DIR/scripts/uci_profile.py" \
  --input /input --work /app/work --output /app/output
```

执行器按顺序做以下真实操作：

1. **表发现与识别**：读取 SQLite 表结构或 CSV/TSV/JSON/XLSX 表头；按规范
   字段签名识别 Concrete/Iris/Wine，支持 UCI 原始长列名、下划线列名与无表头
   文件（Iris 5 列、Wine 14 列、Concrete 9 列）。
2. **建立派生快照**：把识别到的表写入 `work/uci-snapshot.sqlite`，记录每个
   源文件的 SHA-256、字节数与 SQLite 版本；若主表来自提供的 SQLite，
   查询直接在原库上以 `mode=ro` + `PRAGMA query_only=ON` 执行。
3. **数据字典映射**：优先解析输入中的 `data-dictionary.md` 的逐条字段；
   未覆盖的列使用内置规范字段表并在 `work/uci-dictionary.md` 中逐行写出，
   明确标注“未在提供的数据字典中描述”。映射包含规范字段、原始列名、
   类型、单位、解释、来源与行号。
4. **质量检查**：逐列缺失率、完全重复行、重复键、常量列、Tukey 1.5*IQR
   与 |z|>3 异常值、保守范围越界、疑似哨兵值（-999/9999 等）。全部只标记，
   **不删除、不插补**。
5. **查询生成与执行**：按数据集生成一条只读 SELECT：
   - Iris：按 `species` 分组，返回 n 与 4 个测量列的均值/最小/最大；
   - Wine：按 `wine_class` 分组，返回 n 与关键理化指标均值；
   - Concrete：按龄期段（<=28、29-90、91-180、>180 天）分组，返回 n 与
     抗压强度均值/极值；
   - generic：按低基数类别列或整体聚合。
   如果 `work/query.sql` 已有合法单条 SELECT，则优先执行它；若执行报错，
   把错误写入日志并回退到自动查询，不伪造结果。
6. **结果导出与校验**：写 `output/query.sql`、`output/result-table.csv`、
   `output/results.json`、`output/execution.log`；核对实际行数、期望组数、
   缺失组、空结果、跨表共享键的一对多膨胀风险。
7. **生成可复跑代码**：`work/analysis.py`（由 `uci_core.py` 原样生成）与
   `output/analysis-code.py` 内容一致；`work/uci-run.json` 记录本次快照与
   表/列映射。finalize 会再次执行 `work/analysis.py`，所以输出必须确定性。
8. **回填工作文件**：把 `query`、`sql_sha256`、`snapshot`、`schema_mapping`、
   `row_count_check`、`empty_result`、`permissions`、`results`、`evidence`
   以及 `sql_text`、`result_table`、`field_mapping`、`empty_result_handling`、
   `snapshot_version` 写入 `work/analysis-plan.json`。

运行后先读 `output/analysis-report.md` 和 `output/results.json`：

- 行数是否与公开快照一致（Concrete 1030、Iris 150、Wine 178）；
- 分组结果的组数是否与基准组数一致；
- `empty_result.is_empty` 是否为 false；`missing_groups` 是否为空；
- 数据字典映射中 `exists_in_schema=false` 的字段；
- 质量检查里的重复行/异常值是否需要在解释中说明。

**复核与改写边界**：可以补充报告中的解释、局限与跨表讨论；不要改动已执行
SQL 或结果数字。需要改口径时改 `work/query.sql` 后重跑 `uci_profile.py`，
或只重跑 `db_query.py`（手写 SQL 的兼容路径）。

### 第 3 步：finalize（约 25% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 检查必填字段 `query`, `sql_sha256`, `snapshot`, `schema_mapping`,
   `row_count_check`, `empty_result`, `permissions`, `results`, `evidence`
   以及合同字段 `sql_text`, `result_table`, `field_mapping`,
   `empty_result_handling`, `snapshot_version`；
2. 逐条核验 evidence 的 path/locator/quote（包括 `schema_mapping` 中的字典
   引文），找不到的降级并把原因写入 `uncertainties`；
3. 真实执行 `work/analysis.py` 并把 stdout/stderr 写入
   `output/execution.log`；失败不伪造结果；
4. 登记主产物与 `output/query.sql`、`output/result-table.csv`、
   `output/results.json`、`output/execution.log`；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties` 与
   `deliverable/preserved_facts/evidence_map/change_log`；原始
   `output_contract` 键保留并给出覆盖情况。

`status` 取 `completed|partial|input_error`。缺字段、证据不匹配、主产物
缺失或执行失败时为 `partial`；无任何可读表时为 `input_error`。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/analysis-report.md` 的真实路径；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`、`preserved_facts[]`、`evidence_map[]`、`artifacts[]`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`。

本题特有字段：

| 字段 | 来源 | 说明 |
|---|---|---|
| `query` / `sql_text` | `output/query.sql` | 实际执行且只读的单条 SQL |
| `sql_sha256` | 执行器 | 对实际 SQL 原文计算 |
| `snapshot` / `snapshot_version` | 执行器 | 路径、SHA-256、字节数、SQLite 版本、只读标志、来源清单 |
| `schema_mapping` / `field_mapping` | 字典 + 真实 schema | 每个字段的规范名、原始列、类型、单位、字典引文与是否出现在查询/结果中 |
| `row_count_check` | 执行器 | 期望组数、实际行数、是否一致、来源 |
| `empty_result` / `empty_result_handling` | 执行器 | 是否空结果、缺失组、覆盖检查与明确说明 |
| `permissions` | 执行器 | `read_only`、`write_allowed=false` 与说明 |
| `results` | 执行器 | 行数与返回列均值等，每条带 evidence |
| `result_table` | 执行器 | `output/result-table.csv` 的真实路径 |

不要只写 `status/result/claims` 而忽略原契约字段；未覆盖的键会被降级并写入
`uncertainties`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/analysis-report.md` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `query`, `sql_sha256`, `snapshot`, `schema_mapping`, `row_count_check`, `empty_result`, `permissions`, `results`, `evidence` 齐全且非空 |
| G3 | 证据 | 每条关键判断/结果/映射有 path/locator/quote；quote 可在对应文件逐字找到 |
| G4 | 边界 | 空结果、缺失组、行数不符、连接膨胀、未知字段写入 `uncertainties` |
| G5 | 专项 | SQL 在记录的快照上真实执行；`work.query` = `output/query.sql` = 实际执行 SQL；只读 |
| G6 | 数据 | 行数与公开快照一致或已解释；缺失/重复/异常值有计数；未静默删除 |
| G7 | 产物 | 声明的每个 artifact 路径真实存在且非空 |
| G8 | 契约 | result.json 可解析，case_id 与输入一致，合同字段覆盖 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 查询设计、模式/字段映射、结果校验与空结果处理；查询可复跑；结果与问题对应；权限/空结果明确 | `output/analysis-report.md` + `output/results.json` |
| 使用增益 | 可复跑 SQL、快照哈希、执行日志与同条件对照 | `output/query.sql`, `output/result-table.csv`, `output/execution.log`, `work/analysis.py` |
| 创新增益 | 列签名识别、字典接地、连接键/膨胀检查、SQL 报错回退 | `references/uci-profile.md` + 报告中的映射与检查表 |
| 可靠体验 | 空结果、行数不符、缺字段、连接/权限错误的识别与降级 | `uncertainties` + `output/result.json` |
| 生态贡献 | 数据字典、规范字段、检查口径、synthetic 示例可复用 | `SKILL.md` + `references/` + `templates/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 没有可读表 | `input_error`；写合法 result.json 与空结果说明，不编造查询 |
| 表头/列名不认识 | 归为 generic；按低基数类别列或整体聚合；在 mapping 中标“未识别” |
| 行数与公开快照不一致 | 保留实际行数，写 `row_count_mismatch`，不按期望值改数 |
| 查询为空 | 写 `empty_result.is_empty=true` 与 statement；不写成“数据不存在” |
| 已登记组未返回 | 逐个列入 `missing_groups`，报告中保留空组 |
| SQL 报错 | 写错误、可用表/列提示；回退自动查询；不伪造结果 |
| 字段在字典中找不到 | 用内置规范表并在 `work/uci-dictionary.md` 标“未在提供字典中描述” |
| 连接/权限失败 | 写 `uncertainty` 与已完成范围，建议人工接管 |
| 重复行/异常值 | 报告计数与敏感性提示，不静默删除或插补 |

## 最小调用示例（synthetic，不是正式答案）

`examples/synthetic-case/` 是带 `case.json`、`query-request.md`、
`data-dictionary.md` 与 CSV 的 synthetic 链路，用于验证接口，不是 UCI
正式案例答案。UCI 形态可直接用本机 synthetic 快照验证：

```bash
CASE="$SKILL_DIR/examples/synthetic-uci/input"
WORK=/tmp/p4-uci-work; OUT=/tmp/p4-uci-out
rm -rf "$WORK" "$OUT"; mkdir -p "$WORK" "$OUT"
python3 "$SKILL_DIR/scripts/run_case.py" prepare --input "$CASE" --work "$WORK" --output "$OUT"
python3 "$SKILL_DIR/scripts/uci_profile.py" --input "$CASE" --work "$WORK" --output "$OUT"
python3 "$SKILL_DIR/scripts/run_case.py" finalize --input "$CASE" --work "$WORK" --output "$OUT"
```

示意输出（仅说明接口；真实数字以运行结果为准）：

```json
{
  "status": "completed",
  "case_id": "SYNTH-UCI-CASE",
  "tables": {"iris": 150},
  "query_rows": 3,
  "row_count_check": {"expected": 3, "actual": 3, "ok": true},
  "artifacts": ["output/analysis-report.md", "output/query.sql",
                "output/result-table.csv", "output/results.json",
                "output/execution.log"]
}
```

## 版本与限制

- 版本 `2.0.0`：新增 `uci_profile.py` + `uci_core.py`，真实识别并查询
  UCI Concrete/Iris/Wine；保留 `db_query.py` 作为手写 SQL 兼容路径。
- 仅使用 `/input` 提供的材料；不联网、不补造数据、不写入源数据库。
- 列签名识别是启发式；列名高度改写或缺失时归为 generic，并在报告中说明。
- 范围筛查是保守边界，越界只提示复核，不等于数据错误。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断；字段语义仍以数据字典为准。
- `examples/` 全部是 synthetic 接口样例，不是正式案例答案。
