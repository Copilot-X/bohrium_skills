---
name: analysis-data-cleaning-skill
description: 科研数据清洗与处理：按数据字典与清洗工单真实执行缺失/删失/异常/单位换算/近似重复去重，输出可逆规则日志、字段级质量报告、原始快照 SHA-256 与计数闭合的清洗表。Deterministic reversible cleaning pipeline with missing-code vs zero separation, below_lod censoring, logged unit factors, keep-first dedupe, retained+flagged outliers and raw-dropped=cleaned closure. Use for 科研数据清洗、data-cleaning、缺失值、异常值、重复记录、单位转换、质量报告.
---

# 科研数据清洗与处理（analysis track · data-cleaning）

## 用途与触发

task.md 要求“数据字典、缺失/异常/重复处理、清洗规则与快照”，或 `/input/case.json` 给出 `analysis` / `data-cleaning`
相关字段时使用。本题属于 **数据清洗** 类任务，核心交付是
**清洗表、质量报告、规则日志与原始快照**，不是一段泛泛建议。

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
| `scripts/clean_data.py` | **本题核心清洗器**：解析字典/工单，真实清洗 CSV，输出快照、清洗表、质量报告、规则日志并回填 work 文件 |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/analysis-plan.json` 的字段契约 |
| `references/analysis-rules.md` | 数据清洗 的可执行规则与失败模式 |
| `examples/synthetic-input.json` | synthetic 输入示意（非正式案例） |

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

### 第 2 步：真实执行清洗（约 60% 预算）

先读 `work/materials.json` 里的 `measurements.csv`、`data-dictionary.md` 和
`cleaning-workorder.md`。清洗器会解析字典与工单中的规则（缺失码、below_lod、
有效范围、单位等式、去重键），然后**真实改写数据**：

```bash
python3 "$SKILL_DIR/scripts/clean_data.py"   --input /input --work /app/work --output /app/output --install-code
```

`--install-code` 会把清洗器复制为 `work/analysis.py` 与
`output/cleaning-code.py`；finalize 会再次真实执行 `work/analysis.py` 并把
stdout/stderr 写入 `output/execution.log`。

清洗器固定执行以下步骤，并把每一步写成可复核字段：

1. **原始快照**：先逐字节复制输入 CSV 到 `output/raw-snapshot.csv`，计算
   输入 SHA-256 与快照 SHA-256，`identical` 必须是 `true`；
2. **缺失 vs 真值 0**：`-999`、空白、`NA/n/a/null/NaN` 等按缺失处理；
   **不填补**；`status=below_lod` 标记为删失（`_censored=true`），不当作
   数值 0 参与统计；
3. **单位归一**：按字典/工单等式换算到 mg/L（如 `1 ug/mL equals 1 mg/L`），
   在 `_conversion_factor` 与 `rule-log.json` 记录系数；未知单位不猜，
   置空并记 `unit_unknown`；
4. **近似重复**：按工单键（如 `sample_id + assay_date + replicate`）在
   去空格/大小写归一后识别重复，**保留第一行**，每个被丢弃行写入
   `output/quality-report.json` 的 `duplicates` 与 `dropped` 计数；
5. **异常值**：按有效范围（如 0.0–500.0 mg/L）判定，**保留并标记**
   `_outlier=true`，不静默盖帽；同时在报告中列出源行、原值和归一值；
6. **计数闭合**：`raw_rows - dropped = cleaned_rows` 必须成立，否则
   `results.json` 与报告中显式写出 `closed=false` 和差异，不掩盖；
7. **字段级质量报告**：每个字段/问题给出 `field/issue/count/action`；
8. **数字一致性**：`analysis-report.md` 由 `results.json` 同次生成，
   报告中的行数、缺失数、below_lod、异常值、换算数都来自同一份字典。

清洗表 `output/cleaned-table.csv` 保留原始列，并追加以下可核对列：
`_source_row`、`_sample_id_canonical`、`_date_iso`、
`_concentration_mg_l`、`_unit_canonical`、`_conversion_factor`、
`_missing`、`_missing_reason`、`_censored`、`_outlier`。原始值不被覆盖，
下游可用 `_concentration_mg_l` 做统计。

需要针对具体案例调整列名/键/范围时，修改
`work/cleaning-config.json`（可选）或在 `scripts/clean_data.py` 顶部的
推断规则中调整；不要手改 `cleaned-table.csv` 的数字，改规则后重跑。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/analysis-plan.json`，检查必填字段 `cleaned_table`,
   `cleaning_rules`, `quality_report`, `rule_log`, `original_snapshot`,
   `closure`, `data_dictionary`, `evidence`；`closure` 必须满足
   `raw_rows - dropped = cleaned_rows`；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/analysis-report.md` 与 `output/cleaned-table.csv`,
   `output/quality-report.json`, `output/rule-log.json`,
   `output/raw-snapshot.csv` 的真实文件；
4. 若存在 `work/analysis.py`（清洗器 `--install-code` 会写入），会再次
   真实执行并把 stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`；契约键
   `cleaned_table/quality_report/rule_log/original_snapshot/uncertainties`
   由工作文件同名键覆盖。

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
- `artifacts[]`：主产物与 `output/cleaned-table.csv`, `output/quality-report.json`, `output/rule-log.json`, `output/raw-snapshot.csv`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`。

若 `/input/case.json` 的 `output_contract` 含本题特有键（例如
`cleaned_table`、`quality_report`、`rule_log`、`original_snapshot`），必须：

1. 在 `result.json` 顶层保留原键名；
2. 优先写同名产物 `output/<field>.json/.csv/.md`，或把对应 canonical
   字段/artifact 路径填入该键；
3. finalize 会检查 `contract_coverage`，未覆盖的键会降级 `partial` 并写入
   `uncertainties`；不要只写 `status/result/claims` 而忽略原契约字段。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/analysis-report.md` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `cleaned_table`, `cleaning_rules`, `quality_report`, `rule_log`, `original_snapshot`, `closure`, `data_dictionary`, `evidence` 齐全且非空 |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查；locator 指向真实行 |
| G4 | 边界 | 未知单位、非数值、below_lod、超范围值均显式记录，未解决项写入 `uncertainties` |
| G5 | 专项 | 原值未覆盖；原始快照哈希与输入一致；`raw_rows - dropped = cleaned_rows`；规则/换算系数可逆可复跑；报告数字与 `results.json` 同源 |
| G6 | 产物 | 声明的每个 artifact 路径真实存在 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 数据字典、缺失/异常/重复处理、清洗规则与快照；规则可复跑；原始快照保留；清洗前后计数闭合；异常不静默 | `output/analysis-report.md` + evidence |
| 使用增益 | 可复跑的查询/步骤/规则/日志，以及同条件对照 | `output/cleaned-table.csv`, `output/quality-report.json`, `output/rule-log.json`, `output/raw-snapshot.csv` |
| 创新增益 | 本题专项方法（data-cleaning）及其与基线方案的边界 | `references/analysis-rules.md` + `output/analysis-report.md` |
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

完整可运行示例在 `examples/synthetic-case/`（8 行合成表，含 -999、空白、
below_lod、ug/mL、超范围值和近似重复）。复现命令：

```bash
CASE="$SKILL_DIR/examples/synthetic-case/input"
WORK=/tmp/p4-clean-work; OUT=/tmp/p4-clean-out
rm -rf "$WORK" "$OUT"; mkdir -p "$WORK" "$OUT"
python3 "$SKILL_DIR/scripts/run_case.py" prepare --input "$CASE" --work "$WORK" --output "$OUT"
python3 "$SKILL_DIR/scripts/clean_data.py" --input "$CASE" --work "$WORK" --output "$OUT" --install-code
python3 "$SKILL_DIR/scripts/run_case.py" finalize --input "$CASE" --work "$WORK" --output "$OUT"
```

示意输出（synthetic 数字仅用于说明接口，不是正式案例答案）：

```json
{
  "status": "ok",
  "counts": {"raw_rows": 8, "dropped": 1, "cleaned_rows": 7,
             "missing": 2, "below_lod": 1, "outlier": 1,
             "converted": 2, "invalid": 0, "unit_unknown": 0},
  "closure": {"raw_rows": 8, "dropped": 1, "cleaned_rows": 7, "closed": true},
  "snapshot_sha256": "<64 hex chars>",
  "artifacts": ["output/analysis-report.md", "output/cleaned-table.csv",
                "output/quality-report.json", "output/rule-log.json",
                "output/raw-snapshot.csv"]
}
```

`examples/synthetic-input.json` 只是 work 字段片段示意（不是可执行答案）；
`examples/synthetic-case/` 才是完整输入→命令→产物链路。示例中的实体与数值
不得复制到正式案例。

## 版本与限制

- 版本 `1.1.0`：单入口 + `clean_data.py` 真实清洗器 + 共享引擎；模型负责
  确认字段语义与边界，脚本负责规则执行、计数闭合、快照与报告一致性。
- 清洗器不联网、不填补、不覆盖原始值；未知单位/非数值不会被猜测。
- 规则从 `data-dictionary.md` / `cleaning-workorder.md` 解析；若案例措辞
  超出解析范围，先看 `work/cleaning-config.json` 或在脚本顶部补齐映射，
  再重跑，不要手改输出数字。
- 证据校验是逐字匹配启发式，不能替代语义判断。
- `examples/synthetic-case/` 是 synthetic 可运行示例，不是正式案例答案。
