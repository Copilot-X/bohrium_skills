---
name: analysis-code-generation-skill
description: 科研代码生成与执行：自动识别 UCI Concrete(1030)/Iris(150)/Wine(178) 的 CSV 或 SQLite 快照，生成自包含、stdlib-only 的分析脚本并真实执行，产出清洗/统计结果、运行日志、环境清单与 SQL-vs-Python 交叉校验，保留代码哈希、退出码与错误处理策略。Executable UCI code-generation pipeline with validation and result.json. Use for 科研代码生成与执行、code-generation、analysis、可复跑代码、运行日志、错误处理.
---

# 科研代码生成与执行（analysis track · code-generation）

## 用途与触发

task.md 要求“可执行代码、输入输出、依赖、错误处理与运行日志”，或 `/input`
给出需要清洗/统计/查询的固定数据快照时使用。核心交付是**真正跑通的代码与
可复核结果**，不是代码片段或“看起来合理”的数字。

固定数据形态（题面末尾已写明）：Concrete 1030 行、Iris 150 行（bezdek 源
变体）、Wine 178 行；数据可能是 CSV、无表头 `.data`、JSON、XLSX 或真实
SQLite。

不适用：输入没有可读数据；要求在不提供依赖或数据的情况下猜测结果；
要求替代同行评审/临床决策。

核心原则：**代码真实执行、环境可复现、结果可对账、失败不掩盖**。

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | 统一入口：prepare / finalize / run / inspect |
| `scripts/uci_profile.py` | **本题主执行器**：识别数据 → 生成 `work/analysis.py` → 真实执行 → 验证 → 报告/工作文件 |
| `scripts/uci_core.py` | 自包含分析引擎；被原样复制为 `work/analysis.py` 与 `output/analysis-code.py` |
| `scripts/skillcore.py` | result.json 信封、证据/数字审计、产物登记 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段（`require_code=true`） |
| `templates/work.schema.json` | `work/analysis-plan.json` 的字段契约 |
| `references/analysis-rules.md` | code-generation 的通用规则与失败模式 |
| `references/uci-profile.md` | 三数据集字段、识别规则、代码与校验口径 |
| `examples/synthetic-input.json` | synthetic work 片段示意（非正式案例） |
| `examples/synthetic-uci/` | 可运行的 synthetic UCI 形状示例（含缺失值/重复行，非正式案例） |

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：要跑的清洗/统计/查询步骤与验收口径。
- `/input` 下的数据：`.sqlite/.sqlite3/.db`、`.csv/.tsv/.data`、`.json`、
  `.xlsx`；可选数据字典、既有代码、环境说明。
- 生成的代码只读 `/input`/快照，不写源数据；运行产物只写 `/app/work`
  与 `/app/output`。

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

先读 `work/materials.json`，确认数据形态、字段与依赖边界。

### 第 2 步：生成并执行分析代码（约 65% 预算）

```bash
python3 "$SKILL_DIR/scripts/uci_profile.py" \
  --input /input --work /app/work --output /app/output
```

执行器真实完成：

1. **数据识别**：SQLite 表结构或 CSV/TSV/JSON/XLSX 表头；按列签名识别
   Concrete/Iris/Wine，或归为 generic。无表头 Iris（5 列）与 Wine（14 列）
   按文件提示与列数解释。
2. **代码生成**：把 `scripts/uci_core.py` 原样复制为 `work/analysis.py`，
   并写 `work/uci-run.json` 固定快照路径、表/列映射与运行模式；
   `output/analysis-code.py` 与 `work/analysis.py` 字节一致。
3. **真实执行**：用当前 Python 解释器运行 `work/analysis.py`，记录命令、
   cwd、Python 版本、退出码、耗时与 stdout/stderr 到 `output/execution.log`。
4. **清洗与统计**：缺失/重复/异常值只标记不删除；描述统计 + 分组推断 +
   效应量 + 95% CI + Holm；详细口径见 `references/uci-profile.md`。
5. **结果验证（防“跑通但算错”）**：
   - 同一派生快照上，独立执行 SQL 的 `COUNT(*)`/`AVG(col)` 与 Python 计算
     对照，逐项 `pass/fail`；
   - 空值列表、缺列、非数值、零方差、样本不足、只读守卫的函数级自检；
   - 校验结果写入 `results.json.validation`，失败项计入 `uncertainties`。
6. **环境与依赖**：写 `output/environment.json`：Python 实现与版本、平台、
   SQLite 版本、numpy 是否可用（可选，仅校验用）、确定性说明、随机种子。
   `dependencies` 只声明 `python-stdlib`。
7. **产物**：`output/analysis-report.md`、`output/results.json`、
   `output/results.csv`、`output/statistics-report.json`、
   `output/environment.json`、`output/analysis-code.py`、
   `output/execution.log`，以及 `output/figures/`、`output/cleaned-table.csv`、
   `output/quality-report.json`、`output/cleaning-rules.json`。
8. **回填工作文件**：`code`、`results`、`evidence`、`analysis`、
   `dependencies`、`inputs`、`outputs`、`error_handling`、`execution`、
   `validation`、`environment`、`figures`。

运行后必须检查：

- `execution.returncode` 是否为 0；`output/execution.log` 是否有异常；
- `validation.status` 是否 pass、失败项是什么；
- 行数是否与公开快照一致（Concrete 1030、Iris 150、Wine 178）；
- `output/analysis-code.py` 与 `work/analysis.py` 是否字节一致；
- 统计结果的 n、效应量、CI、p 与校正 p 是否与报告一致；
- 错误处理说明是否覆盖缺列、类型不符、空结果、零方差、样本不足。

**复核与改写边界**：可以补报告解释与失败分析；不要手改结果数字。需要改
代码时改 `work/analysis.py`（并同步 `output/analysis-code.py`），或在
`work/uci-run.json` 改配置后重跑；finalize 会再次执行 `work/analysis.py`。

### 第 3 步：finalize（约 25% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 检查必填字段 `code`, `results`, `evidence`，以及 `analysis` 的方法字段；
2. 逐条核验 evidence 的 path/locator/quote（含字典、结果 JSON、日志）；
3. 真实执行 `work/analysis.py`，stdout/stderr 覆盖
   `output/execution.log`；失败只标 partial，不伪造输出；
4. 检查 `output/analysis-code.py` 与 `work/analysis.py` 的 SHA-256 一致
   （`require_code=true`）；
5. 登记 `output/analysis-report.md` 与 `output/analysis-code.py`、
   `output/results.json`、`output/execution.log`、`output/environment.json`；
6. 写 `output/result.json`，保留原 `output_contract` 键并检查覆盖。

`status` 取 `completed|partial|input_error`。缺代码、缺产物、执行失败或
证据不匹配时为 `partial`；没有可读数据时为 `input_error`。

## 输出契约

`result.json` 必须含 `case_id`、`status`、`task_status`、`deliverable`、
`result`、`claims`、`preserved_facts`、`evidence_map`、`artifacts`、
`uncertainties`、`change_log`、`output_contract`、`contract_coverage`。

本题常见合同字段与真实来源：

| 字段 | 来源 |
|---|---|
| `code` | `work/analysis.py`（实际执行入口） |
| `analysis_code` | `output/analysis-code.py`（与上者字节一致） |
| `results` | `output/results.json` 的指标值/单位/n/方法/效应量/CI，带 evidence |
| `evidence` | 字典行、快照哈希、结果 JSON 的逐字片段 |
| `execution_log` | `output/execution.log`（命令、退出码、stdout/stderr） |
| `execution` | returncode、命令、耗时、日志路径 |
| `environment` | `output/environment.json`（Python/SQLite/平台/可选 numpy） |
| `dependencies` | `["python-stdlib"]`（numpy 仅可选校验） |
| `inputs` | 源数据文件及其 SHA-256 清单 |
| `outputs` | 本次声明的产物路径 |
| `error_handling` | 缺数据、缺列、非数值、零方差、空结果、权限错误的处置 |
| `validation` | SQL-vs-Python 对照与函数级自检的 pass/fail |
| `cleaned_table` | `output/cleaned-table.csv`（保留全部行，标缺失/重复标记） |
| `quality_report` | `output/quality-report.json` |
| `rule_log` | `output/cleaning-rules.json` |
| `statistics_report` | `output/statistics-report.json` |
| `figures` | SVG 路径 + 同源 CSV + 轴单位 |

不要只手写 `status/result/claims` 而忽略原契约字段；未覆盖的键会降级并写入
`uncertainties`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/analysis-report.md` 存在、非空、数字与结果文件一致 |
| G2 | 必填字段 | `code`, `results`, `evidence` 齐全；`analysis.method` 非空 |
| G3 | 证据 | 每条关键判断/结果有 path/locator/quote；quote 逐字可查 |
| G4 | 边界 | 缺列/类型不符/空结果/零方差/小样本/失败写入 `uncertainties` |
| G5 | 专项 | 代码真实运行；结果文件生成；异常/限制说明 |
| G6 | 代码一致 | `work/analysis.py` 与 `output/analysis-code.py` SHA-256 相同 |
| G7 | 结果对账 | SQL 聚合与 Python 计算逐项对照；失败项为 0 或已解释 |
| G8 | 产物 | 声明的每个 artifact 路径真实存在且非空 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 可执行代码、输入输出、依赖、错误处理与运行日志；代码真实运行；结果文件生成；异常/限制说明 | `output/analysis-code.py` + `output/execution.log` + `output/results.json` |
| 使用增益 | 同一数据/环境下的一次跑通与交叉校验，减少调试与复核成本 | `output/environment.json` + `output/validation` + `work/analysis.py` |
| 创新增益 | 列签名识别、自包含代码生成、SQL-vs-Python 校验、错误降级 | `references/uci-profile.md` + 报告的执行/校验节 |
| 可靠体验 | 缺列、类型不符、空结果、零方差、权限错误的识别与处置 | `error_handling` + `uncertainties` |
| 生态贡献 | 可复用代码、环境、数据字典、校验方法、synthetic 示例 | `SKILL.md` + `references/` + `templates/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 没有可读数据 | `input_error`；写合法 result.json、空结果说明与代码占位，不编造 |
| 缺列/列名不认识 | 归为 generic 或跳过该变量；记录缺失字段与影响 |
| 类型不符/非数值混入 | 不转成 0；按缺失处理，报告转换失败计数 |
| 空结果/空组 | 显式写 `is_empty`/空组；不写成“无数据” |
| 零方差/样本不足 | 检验标记为无定义；不伪造 p 值或效应量 |
| 依赖缺失 | 标准库路径始终可用；numpy 仅可选，缺失时降级并记录 |
| 路径/编码/超时 | 固定 UTF-8 与工作目录；超时与 I/O 错误写日志，不伪装成功 |
| 代码执行失败 | 保留 execution.log 与 stderr；finalize 标 partial |
| 代码与产物不一致 | 重新生成 `output/analysis-code.py`；哈希不一致会标 partial |

## 最小调用示例（synthetic，不是正式答案）

```bash
CASE="$SKILL_DIR/examples/synthetic-uci/input"
WORK=/tmp/p4-code-work; OUT=/tmp/p4-code-out
rm -rf "$WORK" "$OUT"; mkdir -p "$WORK" "$OUT"
python3 "$SKILL_DIR/scripts/run_case.py" prepare --input "$CASE" --work "$WORK" --output "$OUT"
python3 "$SKILL_DIR/scripts/uci_profile.py" --input "$CASE" --work "$WORK" --output "$OUT"
python3 "$SKILL_DIR/scripts/run_case.py" finalize --input "$CASE" --work "$WORK" --output "$OUT"
```

示意输出（仅说明接口，数值不是正式答案）：

```json
{
  "status": "completed",
  "case_id": "SYNTH-CODE-CASE",
  "tables": {"wine": 178},
  "results": 120,
  "validation": {"status": "pass", "failed": 0},
  "execution": {"returncode": 0, "log": "output/execution.log"},
  "artifacts": ["output/analysis-report.md", "output/analysis-code.py",
                "output/results.json", "output/execution.log",
                "output/environment.json"]
}
```

## 版本与限制

- 版本 `2.0.0`：新增 `uci_profile.py` + `uci_core.py`，生成并执行自包含
  stdlib-only 分析代码；`require_code=true` 时校验代码哈希。
- 生成代码绑定本次 `work/uci-run.json` 的快照路径；路径变化需重跑
  `uci_profile.py`。
- 正态性用 Jarque-Bera 筛查，不等同于 Shapiro-Wilk；小样本结论标探索性。
- SQL-vs-Python 校验只覆盖行数与数值列均值；其他统计量由同一代码路径计算，
  不声称是外部金标准。
- 环境清单记录实际运行环境；不声称在未运行的环境上通过。
- `examples/` 全部是 synthetic 接口样例，不是正式案例答案。
