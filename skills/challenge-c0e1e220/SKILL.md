---
name: analysis-statistics-skill
description: 统计分析与结果解读：自动识别 UCI Concrete(1030)/Iris(150)/Wine(178) 的 CSV 或 SQLite 快照，完成缺失/重复/异常值检查、描述统计、Welch/经典 ANOVA、效应量、95% 置信区间与 Holm 多重比较，生成真实可执行的 analysis.py、统计报告、结果表与 SVG 图。Executable UCI statistics profiler with assumption checks and result.json. Use for 统计分析与结果解读、statistics、analysis、效应量、置信区间、结果复核.
---

# 统计分析与结果解读（analysis track · statistics）

## 用途与触发

task.md 要求“统计方法选择、假设检查、效应量/区间与结果解读”，或 `/input`
出现带缺失/重复/异常值的科研数据表时使用。核心交付是**真实计算出的统计量、
效应量、区间与可复跑的代码**，不是泛化清单。

固定数据形态（题面末尾已写明）：Concrete 1030 行、Iris 150 行（bezdek 源
变体）、Wine 178 行，均可能以 CSV 或真实 SQLite 提供。

不适用：输入没有可读数据；要求替代同行评审/临床决策；要求因果推断而设计
不支持。材料不足时只交付可确认部分，并写入 `uncertainties`。

核心原则：**先清洗检查、再选方法、报效应量与区间、不越界因果**。

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | 统一入口：prepare / finalize / run / inspect |
| `scripts/uci_profile.py` | **本题主执行器**：识别数据集 → 质量检查 → 描述/推断统计 → 效应量/CI/多重比较 → 图 → 报告与工作文件 |
| `scripts/uci_core.py` | 自包含统计引擎；原样生成 `work/analysis.py`，finalize 再次真实执行 |
| `scripts/skillcore.py` | result.json 信封、证据/数字审计、产物登记 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/analysis-plan.json` 的字段契约 |
| `references/analysis-rules.md` | statistics 的通用执行规则与失败模式 |
| `references/uci-profile.md` | 三数据集规范字段、识别规则、统计口径与限制 |
| `examples/synthetic-input.json` | synthetic work 片段示意（非正式案例） |
| `examples/synthetic-uci/` | 可运行的 synthetic UCI 形状示例（含缺失值/重复行，非正式案例） |

## 输入

- `/input/case.json`：`case_id`、`output_contract`；字段原样保留。
- `/input/task.md`：统计问题、分组变量、结果变量与证据要求。
- `/input` 下的数据：`.sqlite/.sqlite3/.db`、`.csv/.tsv/.data`、`.json`、
  `.xlsx`；可选 `data-dictionary.md`、`codebook.md`。
- 脚本按列签名识别 Concrete/Iris/Wine；不认识的表归为 generic，并选择低基数
  类别列作分组、最后一个数值列作目标。

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

先读 `work/materials.json` 与 `task.md`，确认数据文件、数据字典与问题口径。

### 第 2 步：运行 UCI 统计（约 65% 预算）

```bash
python3 "$SKILL_DIR/scripts/uci_profile.py" \
  --input /input --work /app/work --output /app/output
```

执行器真实完成：

1. **识别与映射**：按列签名识别数据集；建立规范字段、原始列名、类型、
   单位与数据字典引文；未描述的列写入 `work/uci-dictionary.md` 并明确标注。
2. **质量检查**：逐列缺失率、完全重复行、重复键、常量列、Tukey 1.5*IQR
   与 |z|>3 异常值、保守范围越界、疑似哨兵值。只标记，不删除、不插补。
3. **描述统计**：n、缺失、均值、SD、SE、最小、Q1、中位数、Q3、最大、
   IQR、偏度、峰度、均值 95% CI；Iris/Wine 按类别，Concrete 按龄期段。
4. **推断统计**：
   - 2 组以上：经典 one-way ANOVA 与 Welch ANOVA 都计算；
     Brown-Forsythe p<0.05 时主检验用 Welch ANOVA；
   - 成对比较：Welch t 检验，均值差 95% CI、Cohen's d、Hedges g，
     并在每个指标内做 Holm 校正；
   - 指标族层面再对所有主检验 p 做 Holm 校正；
   - Concrete：每个配方指标与抗压强度的 Pearson r、Fisher z 区间与 Holm；
   - 样本不足、零方差、组内 n<5 时标记为无定义/探索性，不伪造 p 值。
5. **前提筛查**：每组 Jarque-Bera（明确标注是筛查，不是 Shapiro-Wilk）、
   Brown-Forsythe 方差齐性、样本量与非正态组标记。
6. **敏感性分析**：对主指标剔除 Tukey IQR 异常值后重算 ANOVA，与主分析并列；
   主分析保留全部数据。
7. **图表**：`output/figures/group-means-<dataset>.svg` 为组均值 + 95% CI
   点图，附带同源 CSV；轴/单位/数据来源写进 `figures[]`。
8. **代码与复跑**：生成 `work/analysis.py` 与 `output/analysis-code.py`
   （字节一致），写 `output/environment.json`、`output/results.csv`、
   `output/results.json`、`output/statistics-report.json`、
   `output/cleaned-table.csv`、`output/quality-report.json`、
   `output/cleaning-rules.json`。finalize 会再次执行 `work/analysis.py`。
9. **回填工作文件**：`analysis`（问题/方法/假设/前提检查/样本量/效应量/CI）、
   `results`、`evidence`、`code`、`statistics_report`、`cleaned_table`、
   `quality_report`、`rule_log`、`original_snapshot`、`figures`、
   `environment`、`validation`、`execution`。

运行后必须检查：

- 行数：Concrete 1030、Iris 150、Wine 178；不一致要解释并保留实际值；
- 每个指标 n 与缺失率；完全重复行是否会影响样本量；
- 主检验是经典 ANOVA 还是 Welch ANOVA，为什么；
- 效应量（eta²/omega²、Hedges g）与 95% CI 是否与结论方向一致；
- 多重比较用 Holm，报告未校正 p 与校正 p；
- 结论是否写成“关联/差异”，没有因果措辞。

**复核与改写边界**：可以补充结果解读、局限和领域背景；不要手改数字。
改口径时改 `work/uci-run.json` 或重跑 `uci_profile.py`；报告与
`output/results.json`/`statistics-report.json` 必须一致。

### 第 3 步：finalize（约 25% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 检查必填字段 `analysis`, `results`, `evidence`，以及工作文件中的
   `code`、`figures`、`validation`、`execution`；
2. 逐条核验 evidence 的 path/locator/quote（包括字典引文与图数据来源）；
3. 真实执行 `work/analysis.py`，stdout/stderr 写入 `output/execution.log`；
4. 登记 `output/analysis-report.md` 与 `output/results.json`、
   `output/results.csv`、`output/statistics-report.json`、
   `output/analysis-code.py`、`output/environment.json`、`output/figures/`；
5. 写 `output/result.json`，保留原 `output_contract` 键并检查覆盖。

`status` 取 `completed|partial|input_error`。缺字段、证据不匹配、主产物
缺失或执行失败时为 `partial`；无任何可读数据时为 `input_error`。

## 输出契约

`result.json` 必须含 `case_id`、`status`、`task_status`、`deliverable`、
`result`、`claims`、`preserved_facts`、`evidence_map`、`artifacts`、
`uncertainties`、`change_log`、`output_contract`、`contract_coverage`。

本题常见合同字段与真实来源：

| 字段 | 来源 |
|---|---|
| `analysis` | 工作文件：问题、方法、假设、前提检查、样本量、效应量、CI |
| `results` | 每个统计量的值/单位/n/方法/p/校正 p/效应量/CI，带 evidence |
| `evidence` | 数据字典行、快照哈希、结果 JSON 的逐字片段 |
| `cleaned_table` | `output/cleaned-table.csv`（保留全部行，标缺失数与重复标记） |
| `quality_report` | `output/quality-report.json`（缺失/重复/异常/范围） |
| `rule_log` | `output/cleaning-rules.json`（清洗规则与处置） |
| `original_snapshot` | 源文件清单 + SHA-256 + SQLite 版本 |
| `statistics_report` | `output/statistics-report.json` |
| `analysis_code` / `code` | `output/analysis-code.py` / `work/analysis.py` |
| `environment` | `output/environment.json`（Python/SQLite/平台/可选 numpy） |
| `figures` | SVG 路径 + 同源 CSV + 轴单位 |
| `validation` | 行数核对、重复/缺失计数与实际执行校验 |
| `execution` | returncode、命令、耗时、日志路径 |

不要只手写 `status/result/claims` 而忽略原契约字段；未覆盖的键会降级并写入
`uncertainties`。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/analysis-report.md` 存在、非空、数字与结果文件一致 |
| G2 | 必填字段 | `analysis`, `results`, `evidence` 齐全；`analysis.method` 非空 |
| G3 | 证据 | 每条关键判断/统计量有 path/locator/quote；quote 逐字可查 |
| G4 | 边界 | 缺失、重复、异常值、前提不满足、小样本写入 `uncertainties` |
| G5 | 方法 | 方法选择与设计匹配；前提检查有结果；不越界因果 |
| G6 | 效应量/区间 | 每个主要结论有效应量与 95% CI；多重比较有 Holm |
| G7 | 代码 | `work/analysis.py` 与 `output/analysis-code.py` 一致并真实运行 |
| G8 | 产物 | 声明的每个 artifact 路径真实存在且非空 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 方法选择、假设检查、效应量/区间与结果解读；方法与数据匹配；前提检查；报告数字与输出一致；不越界因果 | `output/analysis-report.md` + `output/statistics-report.json` |
| 使用增益 | 可复跑代码、环境、结果表与同条件对照 | `output/analysis-code.py`, `output/results.csv`, `output/environment.json`, `output/execution.log` |
| 创新增益 | 自动识别、按分布/设计选检验、效应量绑定结论、前提不满足降级 | `references/uci-profile.md` + 报告的前提/敏感性分析 |
| 可靠体验 | 小样本、零方差、缺失比例高、前提不满足的识别与降级 | `uncertainties` + `work/analysis.py` 的自检 |
| 生态贡献 | 数据字典、统计口径、可复用代码与 synthetic 示例 | `SKILL.md` + `references/` + `templates/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 没有可读数据 | `input_error`；写合法 result.json 和说明，不编造统计量 |
| 列名不认识 | 归为 generic；选类别列与目标列，并在报告中说明 |
| 缺失值 | 每个统计量用该变量完整观测；报告 n 与缺失率；不插补、不当 0 |
| 完全重复行 | 计数并保留；提示可能影响样本量，不静默去重 |
| 异常值 | 只标记；对主指标做剔除后的敏感性分析，主分析保留全部数据 |
| 样本不足/零方差 | 检验标记为无定义或探索性；不伪造 p 值 |
| 方差不齐 | 主检验改用 Welch ANOVA；经典 ANOVA 同时报告 |
| 正态性偏离 | Jarque-Bera 筛查标记；小样本时降低结论强度 |
| 多重比较 | 每个指标内与指标族层面都用 Holm；同时报告未校正与校正 p |
| 代码执行失败 | 保留 execution.log 和失败原因；不伪造输出 |
| 单位不明 | 明确写“原始数据未标注单位”，不擅自补单位 |

## 最小调用示例（synthetic，不是正式答案）

```bash
CASE="$SKILL_DIR/examples/synthetic-uci/input"
WORK=/tmp/p4-stat-work; OUT=/tmp/p4-stat-out
rm -rf "$WORK" "$OUT"; mkdir -p "$WORK" "$OUT"
python3 "$SKILL_DIR/scripts/run_case.py" prepare --input "$CASE" --work "$WORK" --output "$OUT"
python3 "$SKILL_DIR/scripts/uci_profile.py" --input "$CASE" --work "$WORK" --output "$OUT"
python3 "$SKILL_DIR/scripts/run_case.py" finalize --input "$CASE" --work "$WORK" --output "$OUT"
```

示意输出（仅说明接口，数值不是正式答案）：

```json
{
  "status": "completed",
  "case_id": "SYNTH-STAT-CASE",
  "tables": {"iris": 150},
  "results": 38,
  "figures": ["figures/group-means-iris.svg"],
  "artifacts": ["output/analysis-report.md", "output/results.json",
                "output/results.csv", "output/statistics-report.json",
                "output/analysis-code.py", "output/environment.json"]
}
```

## 版本与限制

- 版本 `2.0.0`：新增 `uci_profile.py` + `uci_core.py`，真实识别并统计
  UCI 三数据集；`analysis.py` 自包含、stdlib-only，numpy 仅可选校验。
- 正态性用 Jarque-Bera 筛查，不等同于 Shapiro-Wilk；小样本结论标探索性。
- 效应量：one-way eta²/omega²、Welch 成对 Hedges g；不报告未定义的区间。
- 范围筛查是保守边界；越界只提示复核。
- 断言/证据校验是逐字匹配启发式，不能替代语义蕴含判断。
- `examples/` 为 synthetic 接口样例，不是正式案例答案。
