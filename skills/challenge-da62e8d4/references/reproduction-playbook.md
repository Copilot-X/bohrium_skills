# 论文复现与方法验证 Playbook

本 playbook 服务于「参数/代码/指标验算与差异归因」子场景。目标不是写一篇复现综述，而是把论文、补充材料、仓库和候选数值方法整理成**可执行、可核对、可归因**的最小复现闭环。

核心原则：

1. **先定目标，再跑数。** 复现目标不明确时，跑得再多也无法判断在验证什么。
2. **能算就真算，不能算就明说。** 没有运行记录就不写复现值；没有材料就登记 `missing`，不用常识补全。
3. **每个数字有来源。** 论文值要能定位到段落、页码、表格或图；复现值要能定位到脚本、命令、退出码和日志。
4. **容差事前声明。** 比较前写清指标口径、数据切分、绝对/相对容差和统计方式，不能先看结果再定容差。
5. **结论不升档。** 证据只支持 partial 就写 partial；存在数据泄漏只能降档。

---

## 1. 锁定复现目标（七要素）

开始前必须回答七项；任何一项为空，先写进 gaps，不要直接开跑。

| 要素 | 要回答的问题 | 典型定位 |
|---|---|---|
| task | 论文要解决什么任务？输入输出是什么？ | 摘要、方法首段、任务定义表 |
| data | 哪个数据集/样本/实验对象？版本、切分、许可？ | 数据章节、附录、仓库 data 目录 |
| split | 训练/验证/测试如何划分？是否分层、去重？ | 实验设置、表格脚注、代码 split 函数 |
| model / method | 模型结构、算法或实验装置？关键模块？ | 方法图、公式、配置文件 |
| metric | 指标定义、计算方式、阈值、单位？ | 评价章节、表格表头、评测脚本 |
| claim | 要验证的原文结论或数值是哪一条？ | 摘要、结论、主表 |
| reproduction target | 本次最小可判定目标是什么？ | 自己写，必须可执行、可比较 |

写进 `templates/reproduction-plan.json` 的 `seven_elements`，每项包含 `value`、`status`、`source`。

状态枚举：

- `reported`：材料明确给出，能定位。
- `partially_reported`：只给了一部分，或只在代码/图中出现。
- `missing`：材料没有给出。
- `unknown`：材料可能有但当前无法读取/无法判断。
- `not_applicable`：确实不适用于本任务。

---

## 2. 材料定位清单（找得齐）

逐项登记，不允许默默跳过。`status` 使用 `available / missing / restricted / unknown`。

| 类别 | 必查内容 | 证据形式 |
|---|---|---|
| 代码 | 仓库 URL、commit/tag、目录、入口脚本、评测脚本 | `repo_path` + commit + 文件行 |
| 数据 | 数据集名、版本、哈希、下载方式、许可、切分文件 | `dataset_id` + DOI/URL + sha256 |
| 权重 | checkpoint 名称、版本、下载地址、哈希 | URL + sha256 |
| 环境 | Python/框架/驱动版本、锁文件、硬件 | 环境文件 + 运行日志 |
| 配置 | 论文超参、代码默认值、命令行覆盖项 | 正文/附录 + config 文件 + 行号 |
| 许可 | 代码许可、数据许可、再分发限制 | LICENSE / 数据卡 |
| 评测 | 指标实现、阈值、后处理、随机性 | 脚本 + 命令 |

缺失处理：

- `missing`：材料未提供。写进 gaps，给 `impact` 和 `next_check`。
- `restricted`：存在但需要申请/付费/受版权限制。结论通常不能是 `full`。
- `unknown`：尚未核实。不能当成 `available`。

---

## 3. 环境复原（跑得对）

环境快照至少包含：

```json
{
  "runtime": "Python 3.11.9",
  "dependencies": ["stdlib only", "package==1.2.3"],
  "hardware": "CPU / GPU 型号 / 显存 / 驱动",
  "os": "Linux x86_64",
  "seed_policy": "所有随机源固定 seed=7；重复 3 次",
  "determinism_flags": ["PYTHONHASHSEED=7"],
  "command": "python -I work/repro.py --data data/input.csv --seed 7"
}
```

检查点：

- 论文声明与仓库默认值冲突时，**以论文声明的实验设置为主**，把仓库默认值记为 `code_default` 并在差异中说明。
- 依赖只写实际用到的版本；不要用 `latest`。
- 随机过程必须固定 seed；多次运行报告均值、标准差和次数。
- 无 GPU / 超预算时，记录降级运行条件，结论标 `partial`，不得冒充原环境结果。
- 不安装来源不明的依赖；缺依赖时写 `not_run` + 缺失清单。

---

## 4. 参数复原（三类来源）

每个关键参数一行，禁止只写一个数值。

| 来源 `origin` | 含义 | 证据要求 |
|---|---|---|
| `paper` | 正文明确给出 | `paragraph_id` / `page` + 引文 |
| `appendix` | 补充材料明确给出 | 附录页码/表格 + 引文 |
| `code_default` | 代码默认值 | `repo_path` + 行号 |
| `code_inferred` | 从代码逻辑推断 | `repo_path` + 行号 + 推断说明 |
| `assumed` | 材料缺失，自行设定 | 写清理由、影响与敏感性 |
| `unknown` | 无法确定 | 不得用于关键结论 |

冲突处理顺序：

1. 正文 vs 附录：先找论文内的裁决依据（版本说明、脚注、实验编号）。
2. 论文 vs 代码默认值：以论文实验设置为准；代码默认值只在论文未说明时使用。
3. 多处数值不一致：两种配置都跑（若可），结果并列，标 `conflict: unresolved`。
4. 无法裁决：保留冲突，不静默选一个；在 gaps 中给影响。

参数表建议同时记录：`name`、`value`、`origin`、`source`、`conflict`、`impact_if_wrong`、`sensitivity_checked`。

---

## 5. 运行与证据（跑得对、可复核）

最小运行单元优先：能用一个脚本 + 一份小数据验证的，不要先跑全量。

运行记录必须包含：

- 脚本真实路径（提交包内或 `/app/work`）；
- 完整命令；
- 工作目录；
- 退出码；
- 日志路径；
- seed、重复次数；
- 依赖与硬件（可引用环境快照）。

硬规则：

1. `executed=false` 时 `reproduced_value` 必须为 `null`。
2. `executed=true` 但没有脚本、日志或退出码，视为未完成运行。
3. 退出码非 0 的运行不能产出“成功复现值”；只能写失败原因。
4. 合成数据演示必须标 `synthetic_demo: true`，且不得与论文数值做数值比较。
5. 超时/超预算时保留已完成部分，`status=partial`，写清缺口。

日志建议格式（便于机器核对）：

```text
run_id=repro-001
command=python -I work/repro.py --data data/input.csv --seed 7
seed=7
metric=accuracy
value=0.923077
exit_code=0
```

---

## 6. 结果比较（对得上）

比较前先写 `tolerance_policy`：

```json
{
  "metric": "accuracy",
  "reported_value": 0.9231,
  "abs_tol": 0.001,
  "rel_tol": 0.002,
  "statistical_method": "single deterministic run",
  "source": {"table_id": "t1", "row": 2, "column": "accuracy"}
}
```

口径检查：

- 指标定义是否一致（accuracy / F1 / AUROC / RMSE 等不可混用）。
- 数据切分是否一致（test split 是否同一版本、是否去重）。
- 阈值与后处理是否一致。
- 单位与百分比口径是否一致。
- 统计方式是否一致（单次 / 均值±标准差 / 置信区间 / 显著性检验）。

误差公式固定为：

- 绝对误差：`abs_error = |reproduced - reported|`
- 相对误差：`rel_error = abs_error / |reported|`（`reported=0` 时写 `null`）
- 是否在容差内：`abs_error <= abs_tol` 且（若有）`rel_error <= rel_tol`

候选方法对比：

- 案例给出两份候选数值方法时，先对齐输入、切分、指标和容差，再分别运行。
- 报告每份方法的复现值、误差、运行日志和选择依据；不要只报较优者。
- 两份方法结论冲突时，保留冲突并归因，不静默合并。

特殊情况：

- 原文只给图：读数标 `estimated: true`，同时给读图误差；容差必须更宽并说明。
- 单位不同：先换算再比较，写换算系数。
- 只有部分样例/子集：标 `subset: true`，不得与全量指标直接比较。
- 无法同口径：标 `comparison_status: "incomparable"`，不给“通过/不通过”结论。

---

## 7. 差异归因（讲得清）

把“对不上”拆成可核验条目，每条给证据和影响方向。

| 归因类别 | 典型证据 | 常见误判 |
|---|---|---|
| 环境差异 | 框架/驱动版本、硬件、精度设置 | 把版本差异说成随机波动 |
| 数据差异 | 数据版本、切分、预处理、去重 | 用不同 test split 直接比较 |
| 实现差异 | 损失函数、初始化、后处理、阈值 | 只看配置文件不看代码 |
| 论文描述不足 | 缺失超参、缺失 seed、算法细节省略 | 用常识补齐后当成原文设置 |
| 评测协议差异 | 指标实现、四舍五入、样本过滤 | 指标同名就认为同口径 |
| 随机性 | seed、重复次数、方差 | 没有 seed 却断言“方法不行” |

归因条目最少包含：`category`、`observation`、`evidence`、`direction`（使复现值偏高/偏低/未知）、`confidence`（0–1）、`next_check`。

无法判断时写 `unknown`，不要编造原因。

---

## 8. 复现等级与结论

### 8.1 三级复现等级

| 等级 | 判定条件 | 允许的结论 |
|---|---|---|
| `full` | 七要素齐全、代码与数据可用、关键超参可复原、运行可重复 | 可以比较并支持/部分支持/不支持原文结论 |
| `partial` | 关键要素基本齐全，但个别超参/环境/数据子集缺失，或只完成部分运行 | 只能给部分支持或 undetermined；写明缺口 |
| `not_reproducible` | 数据或方法不可得、代码不可用、材料不足以定位目标 | 只能 `undetermined` 或 `no`；不得声称复现成功 |

### 8.2 四档交付 verdict

| verdict | 含义 | 降档触发 |
|---|---|---|
| `reproducible` | 材料、环境、参数、运行、比较证据齐全 | 仅用于 full 且无高影响缺口 |
| `reproducible_with_notes` | 可复现但有关键注记 | partial、estimated 读数、环境降级 |
| `blocked` | 当前无法执行或无法比较 | 数据受限、代码缺失、依赖不可得 |
| `do_not_promote` | 存在泄漏、隐私、伦理或严重误导风险 | 泄漏确认、隐私问题、结论无证据 |

等级只能降不能升。`reproduction_level=partial` 时 verdict 不能是 `reproducible`；`not_reproducible` 时不能是 `reproducible` 或 `reproducible_with_notes`。

### 8.3 结论措辞

- `supports_paper: yes`：仅当关键指标在事前容差内，且有可复核运行。
- `supports_paper: partial`：方向一致但存在缺口、子集或环境差异。
- `supports_paper: no`：同口径比较明确超出容差，且归因支持差异真实存在。
- `supports_paper: undetermined`：材料不足、无法比较或未运行。

结论必须写 `reason`，并引用具体证据/运行项。

---

## 9. 证据分级

每个结论项标注证据等级：

| `evidence_class` | 含义 | 可否写成“已复现” |
|---|---|---|
| `source_inspection` | 只读了论文/代码，没有运行 | 否 |
| `completed_run` | 有脚本、命令、日志、退出码 | 是（在容差与范围内） |
| `unverified_claim` | 仅文档声称或他人转述 | 否 |

代码静态检查（缺 docstring、硬编码路径、无 seed、裸 `except`、未锁版本、魔法数字）记 `file:line` + severity，不等于运行证据。

---

## 10. 常见不可复现原因

| 原因 | 识别信号 | 处理 |
|---|---|---|
| 数据不可得/受限 | 只有数据集名，无下载或许可 | `restricted`，level `not_reproducible` |
| 代码不可得 | 论文无仓库或仓库为空 | 登记缺失，仅做材料审计 |
| 关键超参缺失 | 正文/附录/代码都找不到 | `missing` + 影响 + 敏感性说明 |
| 环境不可重建 | 依赖未锁版本、私有驱动 | 记录实际环境，结论降级 |
| 评测脚本缺失 | 指标无法复算 | 用论文定义重写并标 `implementation_inferred` |
| 数据泄漏 | 训练集含测试样本/重复样本 | `leakage_check=leak_detected`，verdict `do_not_promote` |
| 只挑有利 seed/样例 | 单次结果、无重复、样例挑选 | 多次运行 + 全量结果；否则降级 |
| 论文内部矛盾 | 正文与附录/表格数值不一致 | 保留冲突，两种配置并列 |
| 图读数误差 | 只有曲线无表格 | `estimated` + 读图误差 + 更宽容差 |
| 指标同名不同义 | 自定义 F1/accuracy 实现 | 对齐公式与后处理，否则 `incomparable` |

---

## 11. 验收

交付前运行：

```bash
python3 {SKILL_DIR}/scripts/check_reproduction.py \
  --result /app/output/result.json \
  --plan /app/work/reproduction-plan.json \
  --root /app/output --root /app/work --root /app \
  --json
```

通过标准：

- 退出码 0；没有 `errors`。
- 每个 `missing` 字段都有 gap 且 `impact` 非空。
- 每个 `executed=true` 的运行项都能找到脚本、日志、退出码、命令。
- 每个 `reproduced_value` 能在日志中找到对应数值。
- 每个 `reported_value` 与 `reproduced_value` 的误差计算一致，且容差在运行前声明。
- `reproduction_level`、`verdict`、`conclusion.supports_paper` 三者不矛盾。
- 数据/代码受限时没有假装运行；合成演示没有与论文数值比较。
- `sources` 中的关键引文可逐字回查。

---

## 12. 最小示例（示例，不是正式案例结果）

```bash
# 1) 填计划：复制模板后补七要素、材料、参数与容差
cp {SKILL_DIR}/templates/reproduction-plan.json /app/work/reproduction-plan.json

# 2) 运行最小实验并保留日志
python3 -I /app/work/repro.py --data /app/work/data.csv --seed 7 > /app/work/repro.log 2>&1
echo "exit_code=$?" >> /app/work/repro.log

# 3) 写 result.json 后自检
python3 -I {SKILL_DIR}/scripts/check_reproduction.py \
  --result /app/output/result.json --plan /app/work/reproduction-plan.json \
  --root /app/output --root /app/work --json
```

示例 result 片段（数值仅示意）：

```json
{
  "item": "accuracy",
  "reported_value": 0.92,
  "reproduced_value": 0.90,
  "abs_error": 0.02,
  "rel_error": 0.0217,
  "tolerance": {"abs": 0.03, "rel": 0.05},
  "within_tolerance": true,
  "run": {
    "executed": true,
    "script": "work/repro.py",
    "log": "work/repro.log",
    "command": "python3 -I work/repro.py --data data.csv --seed 7",
    "exit_code": 0,
    "seed": 7,
    "stochastic": false,
    "synthetic_demo": false
  }
}
```
