---
name: experiment-feasibility-skill
description: 实验方案可行性评估：设备/材料/时间/预算/安全/伦理可行性与风险。交付可行性审查、约束条件、风险与确认项，保留来源/定位与不确定性，并生成含官方 reading/writing 字段的 result.json。experiment skill with a source-bound work file, evidence checks and an honest result envelope. Use for 实验方案可行性评估、feasibility、experiment、证据绑定、结果复核.
---

# 实验方案可行性评估（experiment track · feasibility）

## 用途与触发

task.md 要求“设备/材料/时间/预算/安全/伦理可行性与风险”，或 `/input/case.json` 给出 `experiment` / `feasibility`
相关字段时使用。本题属于 **实验可行性评估** 类任务，核心交付是
**可行性审查、约束条件、风险与确认项**，不是一段泛泛建议。

不适用：输入没有可读材料、要求联网获取未提供的数据、或要求替代
同行评审/机构审查/临床决策。材料不足时只交付可确认部分，并把缺口写成
`uncertainty`，不要补造。

核心原则：**有输入、有依据、有产物、有边界**。脚本负责盘点材料、建立
工作区、做确定性的逐条核对、校验证据/字段/产物并组装 `result.json`；
模型负责领域判断、补证、改写结论与最终复核。脚本不会凭空补造设备可用、
审批状态、价格或文献；所有“已具备”都必须能在材料中找到逐字依据。

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
| `scripts/run_skill.py` | **推荐入口**：prepare → 确定性核对 → finalize → 契约补全；输入不完整也写合法 `result.json` |
| `scripts/domain_work.py` | 本模式确定性初稿：六类可行性矩阵、条件、风险、确认项、证据；可重复运行 |
| `scripts/run_case.py` | 分步入口：prepare / finalize / run / inspect |
| `scripts/selftest.py` | 正向 + 空输入边界自测，真实执行并断言产物/契约 |
| `scripts/skillcore.py` | 文本/证据/数字审计、result.json 信封、DOCX/PDF 辅助 |
| `scripts/parsers.py` | JSON/CSV/PDF/DOCX/TeX 材料提取 |
| `scripts/fam_tracks.py` | 本 track 的盘点、证据校验、可选 Python 执行与产物登记 |
| `task.json` | family/mode/profile、工作文件、产物与必填字段 |
| `templates/work.schema.json` | `work/experiment-plan.json` 的字段契约 |
| `references/experiment-rules.md` | 实验可行性评估 的可执行规则与失败模式 |
| `examples/synthetic-input.json` | synthetic 输入示意（非正式案例） |

## 工作流

### 第 0 步：一条命令拿到可复核初稿（推荐，先保证不空转）

```bash
python3 "$SKILL_DIR/scripts/run_skill.py" \
  --input /input --work /app/work --output /app/output
```

它会依次执行 prepare、`domain_work.py` 确定性核对、finalize，并检查：

1. `work/experiment-plan.json`、4 个声明产物和 `output/result.json` 都存在；
2. `output_contract` 的每个键在 `result.json` 或同名产物中有真实内容；
3. 输入为空/不可读时仍写合法 `result.json`（`status=input_error`）且退出码为 0；
4. 任何声明输出缺失时自动补写，再跑一次 finalize。

随后读 `output/experiment-report.md`、`work/materials.json` 与
`work/domain-status.json`，用剩余预算补充专家判断、纠正误判，再运行
`run_case.py finalize`。不要把确定性初稿当成最终结论。

### 第 1 步：prepare（约 10% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点 `/input`，提取文本/JSON/CSV，写 `work/inventory.json` 与
   `work/materials.json`；
2. 生成 `work/experiment-plan.json` 骨架（已存在则不覆盖）；
3. 写 `output/README-partial.md` 与 provisional `result.json`。

先读 `work/materials.json` 与 `task.md`，确认材料边界和缺失项。

### 第 2 步：写 `work/experiment-plan.json`（约 60% 预算）

按 `templates/work.schema.json` 写结构化工作文件。必填字段为
`feasibility`, `conditions`, `risks`, `confirmation_items`, `evidence`，
并为每条关键判断/步骤绑定 `evidence`。`blocking`/`conditional` 条目必须
在 `conditions` 或条目自身的 `condition` 中写出可执行条件（谁、做什么、
何时关闭），不能只写“建议进一步确认”。

```json
{
  "case_id": "SYNTH-EXPERIMENT",
  "mode": "feasibility",
  "summary": "Synthetic example only.",
  "hypothesis": "Synthetic hypothesis",
  "variables": {
    "independent": [
      "Synthetic variable"
    ],
    "dependent": [
      "Synthetic outcome"
    ],
    "controls": [
      "Synthetic control"
    ]
  },
  "steps": [
    {
      "id": "S1",
      "action": "Synthetic step",
      "parameters": {
        "temperature": "350 C"
      },
      "duration": "2 h",
      "materials": "Synthetic material"
    }
  ],
  "parameters": [
    {
      "name": "temperature",
      "value": "350",
      "unit": "C",
      "basis": "synthetic.md P1"
    }
  ],
  "risks": [
    {
      "risk": "synthetic risk",
      "impact": "medium",
      "mitigation": "synthetic mitigation"
    }
  ],
  "confirmation_items": [
    {
      "item": "Confirm equipment access",
      "category": "equipment",
      "why": "synthetic only",
      "action": "Get written confirmation from the equipment manager",
      "blocking": true
    }
  ],
  "conditions": [
    {
      "condition": "Equipment access confirmed in writing before day 1",
      "owner": "equipment manager",
      "acceptance": "booking record with date and model"
    }
  ],
  "evidence": [
    {
      "path": "synthetic.md",
      "locator": "P1",
      "quote": "synthetic source sentence"
    }
  ],
  "uncertainties": [],
  "feasibility": {
    "equipment": [
      {
        "item": "synthetic",
        "status": "to_confirm",
        "source": "synthetic.md"
      }
    ],
    "materials": [],
    "time": "to_confirm",
    "budget": "to_confirm",
    "safety": "to_confirm",
    "ethics": "to_confirm"
  }
}
```

同时写以下产物：

- `output/experiment-report.md`
- `output/feasibility-review.json`
- `output/risk-assessment.json`
- `output/confirmation-items.json`

- `output/experiment-report.md` 是主交付物，必须真实存在且非空。
- 所有 `quote` 必须能在对应 `path` 的材料中逐字（允许空白折叠/省略号分段）
  找到；找不到的写入 `uncertainties`，不要改写成“大意”。
- 未确认条件不写成可执行。
- 六类 `feasibility`（equipment/materials/time/budget/safety/ethics）每类
  必须有 `status`、`condition`；`status=available` 必须有材料证据，
  否则只能是 `to_confirm`/`unknown`。
- 时间只累加互不重复的显式步骤时长，并说明等待、检测周转、返工未计入；
  预算只累加材料中出现的原币金额，不做汇率换算。
- `risks[]` 每条必须有 `mitigation` 与 `fallback`；`confirmation_items[]`
  每条必须有 `category`、`why`、`action`、`blocking`。
- `output_contract` 中的每个键都会写成 `output/<key>.json`（真实内容或
  明确的 partial 指针），并在 `result.json.contract_coverage` 中逐键报告。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `work/experiment-plan.json`（或 JSON 主产物），检查必填字段与 `track` 专项结构；
2. 逐条核验 evidence 的 path/locator/quote；
3. 登记 `output/experiment-report.md` 与 `output/feasibility-review.json`, `output/risk-assessment.json`, `output/confirmation-items.json` 的真实文件；
4. 分析类任务若存在 `work/analysis.py`，会真实执行并把
   stdout/stderr 写入 `output/execution.log`；失败不伪造结果；
5. 写 `output/result.json`，含 `status/result/claims/uncertainties`
   与 `deliverable/preserved_facts/evidence_map/change_log`。

`status` 取 `completed|partial|input_error`。有缺字段、证据
不匹配、主产物缺失或执行失败时用 `partial`。`scripts/run_skill.py` 还会
读取 `work/domain-status.json`：无任何可读材料时强制 `input_error`；
专项检查有阻塞项时降级 `partial`；全部通过且契约覆盖完整时才允许成功态。

退出码约定：只要写出了可解析的 `result.json` 就返回 0；只有
`scripts/run_case.py` 本身缺失、无法启动时才返回 2。

## 输出契约

`result.json` 必须含：

- `case_id`、`status`、`task_status`；
- `deliverable`：`output/experiment-report.md` 的真实路径（存在时）；
- `result`：摘要、mode、必填字段、证据数、执行结果、产物列表；
- `claims[]`：`findings`/`candidates`/`summary` 的投影；
- `preserved_facts[]`：证据中的数字/单位（有则填）；
- `evidence_map[]`：每条证据的 path/locator/quote；
- `artifacts[]`：主产物与 `output/feasibility-review.json`, `output/risk-assessment.json`, `output/confirmation-items.json`；
- `uncertainties[]`、`change_log[]`、`output_contract`、`contract_coverage`；
- `run_skill`：各步骤返回码、fallback 是否触发、声明产物的实际大小；
- `domain-audit.json`：来源数、证据数、专项检查的 problems/warnings。

若 `/input/case.json` 的 `output_contract` 含本题特有键（例如
`实验方案`、`依据与风险`、`确认项` 或 `feasibility_review`），必须：

1. 在 `result.json` 顶层保留原键名；
2. 写同名产物 `output/<field>.json`（中文键直接作为文件名），内容必须是
   真实结构或明确的 partial 指针，不能只写一句“见报告”；
3. finalize 会检查 `contract_coverage`，未覆盖的键会降级 `partial` 并写入
   `uncertainties`；`run_skill.py` 会在最后再补一次映射并重算覆盖表。
   不要只写 `status/result/claims` 而忽略原契约字段。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 主产物 | `output/experiment-report.md` 存在、非空、与任务口径一致 |
| G2 | 必填字段 | `feasibility`, `conditions`, `risks`, `confirmation_items`, `evidence` 齐全 |
| G3 | 证据 | 每条关键判断有 path/locator/quote；quote 逐字可查 |
| G4 | 边界 | 未报告、冲突、失败、未检查项写入 `uncertainties` |
| G5 | 专项 | 六类可行性各自有 status/condition；条件可执行；风险有 mitigation+fallback；未确认不写成可执行 |
| G6 | 产物 | 声明的每个 artifact 路径真实存在且非空 |
| G7 | 契约 | result.json 可解析，case_id 与输入一致，contract_coverage 全覆盖 |
| G8 | 不空转 | 材料不可读时仍写合法 result.json（`input_error`）且退出码 0 |
| G9 | 可复跑 | `python3 scripts/selftest.py` 正反向均通过；同输入重跑矩阵一致 |

## 评分维度对照

| 评分维度 | 本题要交付的证据 | 主要产物 |
|---|---|---|
| 实际效果 | 设备/材料/时间/预算/安全/伦理可行性与风险；每项可行性有条件；风险有处置；不把未确认写成可执行 | `output/experiment-report.md` + evidence |
| 使用增益 | 可复跑的查询/步骤/规则/日志，以及同条件对照 | `output/feasibility-review.json`, `output/risk-assessment.json`, `output/confirmation-items.json` |
| 创新增益 | 本题专项方法（feasibility）及其与基线方案的边界 | `references/experiment-rules.md` + `output/experiment-report.md` |
| 可靠体验 | 空结果、冲突、缺字段、权限/格式错误的识别与降级 | `uncertainties` + `output/result.json` |
| 生态贡献 | SKILL.md、schema、规则、synthetic 示例可被他人复用 | `SKILL.md` + `templates/` + `references/` + `examples/` |

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料为空/不可读 | `input_error`；仍写报告/矩阵/风险/确认项与合法 result.json，但全部标“无可读材料”，不生成伪设备/审批/金额 |
| 只有摘要/部分材料 | 只交付可确认部分；正文标“基于摘要”；`status=partial` |
| 证据 quote 找不到 | 回到原文重摘；无法逐字时删除该证据或标 `uncertainty` |
| 设备状态/预订缺失 | 标 `to_confirm`，写入确认项；不写成“可用” |
| 伦理状态未定 | 标 `approval_pending`/`to_confirm`，作为阻塞条件；不写成“已批准” |
| 参数/时长/预算缺失 | 保持 `unknown`，在条件中说明需要谁补什么；不做默认假设 |
| 方案自相矛盾 | 两条原文都保留为证据，在 `uncertainties` 登记冲突并转人工裁决 |
| 数字/统计不一致 | 以数据文件为准；报告与结果文件对齐 |
| 脚本异常 | `run_skill.py` 仍会 finalize 并写 result.json；查看 `work/main-error.json` 与 `run_skill.steps` |
| 权限/接口/格式错误 | 写 `uncertainty` 与已完成范围；建议人工接管 |

## 最小示例（synthetic，不是正式答案）

```json
{
  "case_id": "SYNTH-EXPERIMENT",
  "mode": "feasibility",
  "summary": "Synthetic example only.",
  "hypothesis": "Synthetic hypothesis",
  "variables": {
    "independent": [
      "Synthetic variable"
    ],
    "dependent": [
      "Synthetic outcome"
    ],
    "controls": [
      "Synthetic control"
    ]
  },
  "steps": [
    {
      "id": "S1",
      "action": "Synthetic step",
      "parameters": {
        "temperature": "350 C"
      },
      "duration": "2 h",
      "materials": "Synthetic material"
    }
  ],
  "parameters": [
    {
      "name": "temperature",
      "value": "350",
      "unit": "C",
      "basis": "synthetic.md P1"
    }
  ],
  "risks": [
    {
      "risk": "synthetic risk",
      "impact": "medium",
      "mitigation": "synthetic mitigation"
    }
  ],
  "confirmation_items": [
    {
      "item": "Confirm equipment access",
      "category": "equipment",
      "why": "synthetic only",
      "action": "Get written confirmation from the equipment manager",
      "blocking": true
    }
  ],
  "conditions": [
    {
      "condition": "Equipment access confirmed in writing before day 1",
      "owner": "equipment manager",
      "acceptance": "booking record with date and model"
    }
  ],
  "evidence": [
    {
      "path": "synthetic.md",
      "locator": "P1",
      "quote": "synthetic source sentence"
    }
  ],
  "uncertainties": [],
  "feasibility": {
    "equipment": [
      {
        "item": "synthetic",
        "status": "to_confirm",
        "source": "synthetic.md"
      }
    ],
    "materials": [],
    "time": "to_confirm",
    "budget": "to_confirm",
    "safety": "to_confirm",
    "ethics": "to_confirm"
  }
}
```

示例只说明接口，不冒充正式案例结果；不要复制其中的实体或数值。

## 版本与限制

- 版本 `1.1.0`：新增 `run_skill.py` 一键兜底入口与 `domain_work.py`
  确定性可行性核对；无材料/缺产物时仍写合法 result.json 与全部声明输出；
  契约键按同名产物映射并逐键报告覆盖。
- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_tracks`；模型写内容，脚本做
  证据/字段/产物审计。
- 不联网：检索/追踪/数据库类任务只使用 `/input` 提供的材料或导出；
  不伪造在线查询结果。
- 证据校验是逐字匹配启发式，不能替代语义蕴含判断。
- 示例是 synthetic 接口样例，不是正式案例答案。
