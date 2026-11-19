# 方法与实验章节执行规则

本文件是 `SKILL.md` 的可执行补充。所有检查都能对应到
`scripts/fam_methods.py` 与 `work/methods-plan.json` 的字段。

## 1. 材料读取顺序

1. 先读 `work/extracted-facts.json` 与 `work/parameter-table.csv`，
   获得候选参数、设置、数据集、对照与版本；
2. 再直接打开原始 `config.yaml`、`train.log`、`code.py`、`data.csv` 等，
   核对候选值，不要只信抽取结果；
3. 冲突时保留所有来源，不取平均、不静默选择；
4. 材料没有的信息不补造，进入 `gaps`。

## 2. 六类要素与正文位置

| 要素 | 正文必须写 | 计划字段 |
|---|---|---|
| 方法步骤 | 有序步骤，input→operation→output→decision | `steps[]` |
| 实验设置 | 硬件、软件/版本、环境、随机种子、重复次数 | `settings` + `reproducibility[]` |
| 参数 | 名称、取值、单位、作用对象、来源 | `parameters[]` |
| 数据集 | 名称、版本、规模、预处理、划分比例与方式 | `datasets[]` |
| 对照实验 | 基线名称/版本、同预算条件、指标与统计方式 | `baselines[]` |
| 复现与限制 | 代码版本、环境、命令、数据获取；具体失效场景 | `reproducibility[]` + `limitations[]` |

正文没有写的要素，即使计划里有，也视为未覆盖（G1 失败）。

## 3. 来源定位（source）

- `source.path` 必须是输入中的真实相对路径，例如 `config.yaml`、
  `train.log`、`code.py`、`data.csv`；只写 `"config"` 或 `"materials"`
  视为不合格。
- `source.locator` 指向配置键、日志行、代码行或表列，例如
  `training.learning_rate`、`train.log L3`、`code.py argparse`、
  `data.csv temperature_c`。
- 从日志抽出的 epoch/loss 等数值只能作为证据，不能当超参数写入
  `parameters`；超参数以配置或代码默认值为准。
- `best_epoch=37` 这类运行结果写进 `settings.repeats` 或限制说明，
  不要伪装成训练轮数。

## 4. 计划字段的硬性校验

`finalize` 会逐条检查，任一不满足即 `plan_problems` 非空、
`status=partial`：

1. `steps` 为空 → `no ordered steps`；
2. 任一步骤缺 `source.path` → `step <id> has no source`；
3. 任一参数缺 `source.path` → `parameter <name> has no source`；
4. 数据集缺 `version` 或 `split` → 逐条 problem；
5. 基线缺 `version` 或 `conditions` → 逐条 problem；
6. `reproducibility` 序列化文本不含 `environment`/`command`/`seed`/`data`
   任一 → 逐条 problem（大小写不敏感）；
7. `limitations` 为空 → `no limitations`；
8. 限制语句少于 12 字符，或含 `limitation`/`future work`/`not perfect`
   → `limitation is generic`；
9. `gaps` 为空 → `no explicit gaps/questions for the author`。

注意第 8 条的措辞陷阱：不要写
`A limitation of this study is ...`、`Future work should ...`、
`The setup is not perfect`。改写成具体场景：
`Vacancy concentration was not measured operando, so mechanism claims
outside the tested sample and 300-400 C range remain unsupported.`

## 5. 复现清单

`reproducibility[]` 建议固定四类 `item`：

| item | value 示例 | 来源 |
|---|---|---|
| `environment` | `Python 3.12.1, PyTorch 2.1.0, CUDA 12.1, NVIDIA A100, fp32` | `config.yaml`
| `command` | `python code.py --data data.csv --seed 42 --epochs 100` | `code.py`
| `seed` | `42` | `train.log` 或 `config.yaml`
| `data access` | `data.csv; dataset v1.2` | `config.yaml` / `data.csv`

- 命令只写材料能证明的参数；代码里没有的 flag 不加。
- 随机种子与重复次数分别写；`repeats` 在 `settings` 与正文都要出现。
- 数据获取方式若是内部路径，如实写路径，不编造 URL/DOI。

## 6. 数据集与对照

1. 版本、规模、划分比例、随机方式、预处理全部来自材料；
2. 划分字符串保持原样（如 `train:val:test = 8:1:1`），不要改写成
   `80/10/10` 或 `80%`，否则会产生材料外数字；
3. 基线必须写版本与对照条件（温度、压力、载量、空速、预算）；
4. 没有基线时：`baselines: []`，正文写“材料未提供基线，未做对照结论”，
   `gaps` 追问基线选择；
5. 不同材料在不同条件下比较时，不写“优于”结论，只写条件差异。

## 7. 数字与数值一致性

- `methods.md` 的每个数值都必须在输入材料中出现过；包括硬件型号中的
  数字、版本号、百分比、样本量。
- 不要写派生值：`8:1:1 → 80%`、`12000 × 0.8 = 9600`、`2×` 加速比。
- 不要在正文补材料没有的指标（如未记录的 F1、p 值、标准差）。
- 正文参数值必须等于 `parameters[].value`；单位写 `unit`，不要换算。
- 列表序号也是数字：`1.`、`2.`、`# 1 Introduction` 都会产生数字 token；
  材料中没有对应数字时，正文的步骤和问题用 `-` 无序列表。
- `methods-report.json` 的 `untabulated_numbers` 是提示项：版本号或
  结果指标可能天然不在参数表中；确认它们来自材料即可，不要为清空列表
  而编造参数行。
- 数值冲突时写成并列事实并标注来源，例如
  `config.yaml reports 100 epochs; train.log stopped at epoch 37 after
  early stopping (patience 10).`——37 与 10 都来自日志，合法。

## 8. 缺口与追问

`gaps[]` 的每一项必须可执行：

1. `item`：缺失的字段或决定，例如“test split usage”；
2. `why`：为什么现有材料不足以判断；
3. `question`：作者能一句话回答的具体问题，例如
   “Was the test split used for model selection?”。

禁止 `question` 写成“请补充更多细节”这类无法回答的句子。

## 9. 失败模式

| 失败 | 引擎信号 | 修复 |
|---|---|---|
| 步骤缺来源 | `step ... has no source` | 补真实 path/locator |
| 数据集版本/划分不明 | `dataset ... lacks version/split` | 留空并进 gaps，或补材料证据 |
| 基线条件不明 | `baseline ... lacks ...` | 补条件或删行并进 gaps |
| 复现缺四要素 | `reproducibility lacks ...` | 补 environment/command/seed/data |
| 限制是套话 | `limitation is generic` | 改写为具体失效场景 |
| 缺追问 | `no explicit gaps/...` | 至少写 1 条可回答问题 |
| 正文数字材料外 | `numbers.invented` | 删除或恢复原值 |
| 正文与表不一致 | `untabulated_numbers` | 对齐参数值或解释来源 |

## 10. 交付前自检

1. `methods-report.json`：`numbers.ok=true`、`plan_problems=[]`；
2. `parameter-table.csv` 每行都有 `source_path` 与 `locator`；
3. `repro-checklist.md` 覆盖 environment/command/seed/data；
4. `gap-questions.md` 至少 1 条，问题可直接回答；
5. `result.json`：`status=completed`（材料完整时），
   `deliverable.path="methods.md"` 且 `exists=true`；
6. `artifacts[]` 逐项存在；`methods.docx` 可重开；
   `methods-preview.pdf` 存在或已记录 uncertainty。
