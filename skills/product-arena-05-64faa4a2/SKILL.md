---
name: source-grounded-methods-writer
description: 方法与实验章节写作：从配置文件、训练日志、代码和数据集抽取步骤、参数、数据划分、对照与版本，生成可复做的方法与实验章节、带来源的参数表、复现清单和作者追问清单，并逐项核对正文数字。Source-grounded methods and experiments writing with parameter provenance, reproducibility checklist and number audits. Use for 方法章节、实验设置、参数表、数据集划分、复现性、限制条件、实验记录整理.
---

# 方法与实验章节写作（Source-Grounded Methods Writer）

## 用途与触发

task.md 要求“写方法与实验章节”“把实验记录整理成步骤”“生成参数表”
“补复现清单”“写数据集与划分”“说明对照条件与限制”，或
`/input/case.json` 给出 `methods` / `experiments` / `reproducibility`
等字段时使用。

不适用：没有配置/日志/代码/数据说明的方法章节；要求联网获取数据；
需要真实运行训练或评测。材料只有论文正文而无实验记录时，可写方法概述，
把缺失参数列为 gap，不要伪造参数；若数据集版本/划分或基线条件等关键
字段无法填写，保留空值使 `status=partial`。

核心原则：**写得全、写得准、做得着、说得住**。
脚本负责抽取候选参数、核对正文数字、校验计划字段、生成参数表/复现清单/
追问清单；模型负责把零散记录组织成可执行步骤，并判断哪些信息不足。
脚本不会代写章节，也不会用模板填充缺失值。

## 输入

- `/input/case.json`：`case_id`、`output_contract`；原样保留到 result.json。
- `/input/task.md`：学科、目标读者、期刊格式、必须覆盖的实验要素。
- 结构化材料：`.yaml/.yml/.toml/.json/.csv`（配置、数据表）。
- 文本材料：`.log/.py/.sh/.md/.txt/.tex/.docx/.pdf`（日志、代码、说明、稿件）。
- 可选：已有 `work/methods-plan.json`、作者补充说明、集群作业脚本。

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

## 支持文件

| 文件 | 作用 |
|---|---|
| `scripts/run_case.py` | **唯一入口**：prepare / finalize / run / inspect |
| `scripts/skillcore.py` | 数字 token、DOCX/PDF 导出、result.json 信封 |
| `scripts/parsers.py` | YAML/TOML/JSON/CSV/LOG/代码/PDF/DOCX 提取 |
| `scripts/fam_methods.py` | 本任务的事实抽取、计划校验、参数表与复现清单 |
| `task.json` | family=`methods-writing`、module=`fam_methods`、profile=`writing` |
| `templates/methods-plan.schema.json` | `work/methods-plan.json` 字段契约 |
| `templates/parameter-table.schema.json` | 参数表 JSON/CSV 字段契约 |
| `references/methods-rules.md` | 六类要素、来源定位、冲突处理与失败模式 |
| `examples/synthetic-config.yaml` | synthetic 配置（不是正式案例） |
| `examples/synthetic-methods-plan.json` | synthetic 计划（不是正式答案） |
| `examples/synthetic-methods.md` | synthetic 成稿（不是正式答案） |

## 工作流（先抽事实，再写章节，最后对齐）

### 第 1 步：prepare（约 15% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" prepare \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 盘点输入并写 `work/inventory.json`、`work/context.json`；
2. 解析 YAML/TOML/JSON 的嵌套键，按“参数/设置/数据集/对照/版本”抽取，
   同时从日志和代码中抓 `key=value`、`--flag default=...`、seed 线索，
   写入 `work/extracted-facts.json`：
   `facts.parameters/settings/datasets/baselines/versions`，每行含
   `name/value/source/locator/kind`；
3. 写 `work/parameter-table.csv`（候选参数，不是最终表）；
4. 写 `work/methods-plan-template.json`（待填骨架）；
5. 写 `work/methods-skeleton.md`（来源已填、正文待写）；
6. 写 `output/result.json`，`status=partial`，等待模型写成稿。

没有可读材料时入口写 `status=input_error` 并退出 0。不要依据题面标签
补造配置、日志或数据。

### 第 2 步：模型写两个文件（约 55% 预算）

1. `/app/output/methods.md`：完整方法与实验章节，**必须非空**；
   覆盖步骤、实验设置、参数、数据集与划分、对照、复现与限制；
2. `/app/work/methods-plan.json`：结构化计划与来源映射（见下）。

`methods-plan.json` 字段（与 `fam_methods.py` 实际读取一致）：

```json
{
  "case_id": "与输入一致",
  "steps": [
    {"id": "M1", "name": "步骤名", "input": "输入", "operation": "操作",
     "output": "输出", "decision": "判断条件",
     "source": {"path": "config.yaml", "locator": "dataset"}}
  ],
  "settings": {"hardware": "", "software": "", "environment": "",
               "seeds": "", "repeats": ""},
  "parameters": [
    {"name": "learning_rate", "value": "0.001", "unit": "",
     "scope": "training",
     "source": {"path": "config.yaml", "locator": "training.learning_rate"}}
  ],
  "datasets": [
    {"name": "CO2-H2 benchmark", "version": "v1.2", "size": "12000",
     "preprocessing": "", "split": "train:val:test = 8:1:1",
     "source": {"path": "config.yaml", "locator": "dataset"}}
  ],
  "baselines": [
    {"name": "CeO2", "version": "same batch", "conditions": "350 C, 1 bar",
     "metrics": "selectivity, conversion",
     "source": {"path": "data.csv", "locator": "data.csv"}}
  ],
  "reproducibility": [
    {"item": "environment", "value": "Python 3.12.1, PyTorch 2.1.0",
     "source": {"path": "config.yaml", "locator": "software"}},
    {"item": "command", "value": "python code.py --data data.csv --seed 42",
     "source": {"path": "code.py", "locator": "argparse"}},
    {"item": "seed", "value": "42",
     "source": {"path": "train.log", "locator": "seed"}},
    {"item": "data access", "value": "data.csv",
     "source": {"path": "data.csv", "locator": "data.csv"}}
  ],
  "limitations": [
    {"statement": "具体失效场景与适用范围",
     "failure_scenario": "在什么条件下结论不成立",
     "source": "data.csv"}
  ],
  "gaps": [
    {"item": "缺失项", "why": "为什么材料不足以判断",
     "question": "给作者的具体问题"}
  ]
}
```

硬规则（详见 `references/methods-rules.md`）：

1. **步骤可执行**：`steps` 至少 1 条，按执行顺序给 `M1/M2…` 编号；
   每步写清 input → operation → output → decision，不能把多步压成一句；
   每步 `source.path` 必须非空。
2. **参数有来源**：`parameters[].source.path` 必须非空，`locator` 指向
   配置键、日志行或代码行；单位与作用对象写入 `unit`/`scope`。
3. **数据集必填版本与划分**：`datasets[].version` 与 `split` 为空会被
   记为 `plan_problems`；版本、规模、预处理必须来自材料，不能猜。
4. **对照必填版本与条件**：`baselines[].version` 与 `conditions` 为空会被
   记为 `plan_problems`；材料没有基线时不要虚构，留空数组并在 `gaps`
   追问，同时在正文声明“未做基线比较”。
5. **复现四要素**：`reproducibility` 序列化后必须包含
   `environment`、`command`、`seed`、`data` 四个词（不区分大小写）；
   建议四个 `item` 就用这些词，值写具体版本/命令/种子/数据路径。
6. **限制要具体**：`limitations` 非空；每条 `statement` 至少 12 个字符，
   且**不能包含** `limitation`、`future work`、`not perfect`
   （含 `limitations` 也会被判定为套话）；`failure_scenario` 写失效场景。
7. **缺口要能追问**：`gaps` 非空；每项写 `item/why/question`，
   问题必须能由作者用一句话回答。
8. **正文数字只来自材料**：`methods.md` 中每个数值都必须能在输入材料
   文本中找到；不要计算派生值（如由 8:1:1 换算百分比），不要写
   材料没有的硬件、样本量或指标。**有序列表的 `1.`、`2.` 和标题编号
   同样计入数字**：材料中没有这些数字时，步骤与问题改用 `-` 无序列表。
9. **正文与表一致**：参数表由 plan 生成；正文里的参数值必须与
   `parameters[].value` 一致，否则 `untabulated_numbers` 会列出差异。

### 第 3 步：finalize（约 30% 预算）

```bash
python3 "$SKILL_DIR/scripts/run_case.py" finalize \
  --input /input --work /app/work --output /app/output
```

入口会：

1. 读取 `output/methods.md`；缺失时把 `work/methods-skeleton.md` 写成
   `output/methods-partial.md` 并返回 `status=partial`；
2. 校验 `methods-plan.json`：步骤/参数/数据集/对照/复现/限制/缺口；
3. 比对 `methods.md` 与全部输入材料的数值，得到 `numbers.ok/invented`；
   再与参数表比对，得到 `untabulated_numbers`（提示性，不单独降级）；
4. 写 `output/methods-report.json`（上述检查与计数）；
5. 写 `output/parameter-table.json`（`parameters` + `extracted`）与
   `output/parameter-table.csv`
   （`name,value,unit,scope,source_path,locator`）；
6. 写 `output/repro-checklist.md`（逐项带来源）与
   `output/gap-questions.md`（逐条追问）；
7. 导出 `output/methods.docx`（真实 OOXML，可重新打开）与
   `output/methods-preview.pdf`（文本内容预览，不冒充 Word 排版渲染）；
8. 写 `output/result.json`（writing 官方四字段由入口生成）。

`status` 取 `completed|partial|input_error`：正文缺失、正文含材料外数字、
或 `plan_problems` 非空时为 `partial`。DOCX/PDF 导出失败只记
`uncertainty`，不改变主稿可用性。

## 输出契约

| 产物 | 说明 |
|---|---|
| `methods.md` | 模型写成稿，`deliverable` 指向它 |
| `parameter-table.json` | plan 中的参数 + 引擎抽取的候选参数 |
| `parameter-table.csv` | 表格列：`name,value,unit,scope,source_path,locator` |
| `repro-checklist.md` | 环境/命令/种子/数据等复现项，逐项带来源 |
| `gap-questions.md` | 材料缺口与给作者的问题 |
| `methods-report.json` | `numbers/plan_problems/untabulated_numbers` 与计数 |
| `methods.docx` | 可编辑 Word；导出失败时不登记 artifact |
| `methods-preview.pdf` | 文本内容预览；失败时记 uncertainty |
| `methods-partial.md` | 仅当正文缺失时生成的骨架保底稿 |
| `result.json` | `case_id/status/deliverable/preserved_facts/evidence_map/change_log/artifacts/result/output_contract/contract_coverage` |

`result.deliverable.path` 必须是真实存在的 `methods.md`；
`evidence_map[]` 包含每个步骤与参数的 `source.path/locator`；
`preserved_facts[]` 记录输入材料中的数字 token。

## 质量闸门

| # | 检查 | 通过标准 |
|---|---|---|
| G1 | 六类要素 | 正文覆盖步骤、设置、参数、数据、对照、复现与限制 |
| G2 | 来源定位 | 每个 step/parameter/dataset/baseline 有 `source.path`，建议有 locator |
| G3 | 数字准确 | `numbers.ok=true`，无材料外数值，无自行换算值 |
| G4 | 参数对齐 | 正文参数值与 `parameter-table` 一致，差异均有解释 |
| G5 | 复现 | 四要素 environment/command/seed/data 齐全且给具体值 |
| G6 | 限制 | 非空、具体失效场景、不含 limitation/future work/not perfect |
| G7 | 缺口 | `gaps` 非空，每项有 why 与可回答的 question |
| G8 | 契约 | result.json 可解析，artifact 路径真实；DOCX 可重开，PDF 存在或已说明 |

G2 是编辑闸门：引擎只强制 `steps` 与 `parameters` 的 `source.path`，
数据集与对照的来源定位需要模型与作者自行保证。

`untabulated_numbers` 是提示性检查：版本号（如 `3.12.1`）、单位或
结果指标可能不在参数表中，只要它们来自材料且正文与表不矛盾，可在
交付说明中解释；不要为清空该列表而编造参数行。

## 异常处理

| 情况 | 处理 |
|---|---|
| 无任何可读材料 | `input_error`；不生成伪方法章节 |
| YAML/TOML 解析失败 | 退回逐行 `key=value` 抽取；模型直接读原文，缺字段进 gaps |
| 配置、日志、代码数值冲突 | 不取平均、不静默选一个；正文并列写明来源，`gaps` 追问确认 |
| 参数无来源 | 不写入 `parameters`；在 `gaps` 记录 |
| 数据集版本未知 | 不猜版本；版本留空以触发 `partial`，并在 `gaps` 追问 |
| 没有基线 | `baselines` 留空数组，正文明确声明未做对照，`gaps` 追问 |
| 正文出现材料外数字 | 删除或恢复为材料原值；不得保留推算值 |
| 计划字段缺失 | 按 `methods-report.json` 的 `plan_problems` 逐项补齐后重跑 |
| 复现命令不完整 | 只写材料能证明的部分；缺项进 gaps，不编造命令 |
| DOCX/PDF 导出失败 | 保留 md 主稿；uncertainty 记录原因 |
| 时间不足 | 先写 `methods.md` 与最小 plan（steps/parameters/repro/limitations/gaps） |

## 最小示例（synthetic，不是正式答案）

配置片段：`learning_rate: 0.001`、`seed: 42`，日志：
`test_selectivity=92`。

```json
{"id": "M2", "name": "Train model", "input": "training split",
 "operation": "100 epochs, batch 32, lr 0.001, AdamW",
 "output": "checkpoint", "decision": "early stopping patience 10",
 "source": {"path": "config.yaml", "locator": "training"}}
```

正文写法：`We trained for 100 epochs with batch size 32 and learning rate
0.001, using seed 42.` `finalize` 后应满足：`numbers.ok=true`、
`plan_problems=[]`、`parameter-table.csv` 含 `learning_rate=0.001`、
`repro-checklist.md` 含 environment/command/seed/data、`status=completed`。

## 版本与限制

- 版本 `1.0.0`：单入口 + 共享引擎 + `fam_methods` 模块；
  prepare 写 provisional result，官方 writing 四字段由入口从真实文件生成。
- 抽取是启发式：嵌套配置键、日志正则和代码 flag 可能漏项；
  最终参数以 `methods-plan.json` 为准，缺口用 `gaps` 交付作者。
- `numbers` 检查是数值集合比对，不做单位换算；派生值会被判为新材料。
- `settings` 字段用于写作与人工复核，引擎不做单独校验。
- YAML 解析优先 PyYAML，缺失时退回逐行 `key=value` 抽取；
  TOML 依赖 Python 3.11+ 的 `tomllib`。PDF 预览依赖 matplotlib，
  缺失时记 `pdf_preview` uncertainty，`methods.md` 与 DOCX 不受影响。
- 示例全部为 synthetic 接口样例，不是正式案例答案；不要复制其中的实体或数值。
