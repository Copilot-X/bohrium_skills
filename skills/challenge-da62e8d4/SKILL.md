---
name: paper-reproduction-verifier
description: 论文复现与方法验证：抽取方法要素与可复现性缺口，形成复现计划，在能运行的部分真实执行，比较报告值与复现值并给出误差、归因和结论边界，最后用内置脚本校验 result.json。Extract method specs, build a reproduction plan, identify gaps, actually run what can be run, compare reproduced vs reported results, and validate /app/output/result.json. Use for 论文复现、方法验证、可复现性审计、参数复原、复现报告、重跑实验。
---

# 论文复现与方法验证（Paper Reproduction Verifier）

## 用途与触发

task.md 要求「复现」「验证方法」「检查可复现性」「重跑实验」「参数/代码/指标验算」「差异归因」时使用。核心原则：**能算就真算，不能算就明说；每个数字都要能回到材料或日志。**

## 支持文件（必须使用）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/reproduction-playbook.md` | 开始复现前；方法拆解、材料定位、参数复原、容差与归因 | 先读第 1–6 节；按其表格填写计划，不要凭常识补缺失项 |
| `templates/reproduction-plan.json` | 任何运行之前 | `cp {SKILL_DIR}/templates/reproduction-plan.json /app/work/reproduction-plan.json`，再填七要素、materials、parameter_recovery、reproduction_steps、tolerance_policy；没有的项保留 `missing` 并写 gap |
| `scripts/check_reproduction.py` | 写完 `result.json` 后、交付前 | `python3 -I {SKILL_DIR}/scripts/check_reproduction.py --result /app/output/result.json --plan /app/work/reproduction-plan.json --root /app/output --root /app/work --root /app --json`；退出码 0 才交付，1 按 errors 修复，2 输入不可读 |
| `scripts/validate_result.py` | 通用输出契约与引文回查（随包提供） | `python3 -I {SKILL_DIR}/scripts/validate_result.py /app/output/result.json --input /input --check-quotes` |

`{SKILL_DIR}` 是 Skill 安装目录（评测环境通常是 `/skills/weekly-skill`）。脚本只依赖 Python 标准库。`check_reproduction.py` 不会执行实验脚本，只校验结果与计划的一致性；实际运行必须由你按计划执行并保存日志。把校验报告保存到 `/app/work/reproduction-check.json`，便于复核。

## 输入

- `/input/case.json`：固定案例材料（论文正文、段落/表格/图编号、候选数值方法、仓库或数据说明）
- `/input/task.md`：任务与验收标准，**以它为准**
- 可能的补充材料、代码片段、数据文件、日志
- 工作目录 `/app/work`；最终产物 `/app/output`

先读实际文件，不要依据标题或领域标签假设内容。

## 执行步骤

1. **识别输入与边界**：记录 `case_id`、材料清单、问题范围；区分「论文声称」「可运行材料」「无法获得项」。
2. **抽取方法要素**：按 playbook 第 1 节锁定 task / data / split / model / metric / threshold / claim；每项写 `value`、`status`、`source`（段落、页码、表格、图、仓库路径或 URL）。
3. **填复现计划**：复制 `templates/reproduction-plan.json` 到 `/app/work/reproduction-plan.json`；登记材料版本、环境、参数来源（paper / appendix / code_default / code_inferred / assumed）、运行步骤和**事前容差**。计划未写容差前不要开跑。
4. **找缺口并定级**：逐项标 `reported / partially_reported / missing / unknown`；每个 `missing` 写 `impact` 和 `next_check`；按 playbook 第 8 节判 `full / partial / not_reproducible`。
5. **实际执行**：优先最小可运行单元。若案例提供两份候选数值方法（两个实现或两组参数），逐个运行，使用同一数据切分与同一事前容差，分别记录脚本、日志和结果，不合并成单一数字。每次运行记录脚本路径、完整命令、工作目录、退出码、日志、seed 和重复次数。不能运行时写 `executed=false` + `not_run_reason`，`reproduced_value` 必须为 `null`。
6. **结果比较**：按计划中的容差比较 `reported_value` 与 `reproduced_value`，计算绝对/相对误差；原文只给图时标 `estimated` 并计入读数误差；不同口径标 `incomparable`。
7. **差异归因**：按 playbook 第 7 节把偏差拆成环境、数据、实现、论文描述不足、评测协议、随机性等条目，每条给证据与影响方向；无法判断写 `unknown`。
8. **生成并自检**：写 `/app/output/result.json`（下节契约），然后运行 `check_reproduction.py` 与 `validate_result.py`；修复所有 errors，确认 warnings 都有合理解释后才交付。

## 输出契约

```json
{
  "case_id": "...",
  "task_family": "reading",
  "status": "completed|partial|insufficient_evidence|failed|input_error",
  "status_reason": "部分完成/无法判断/失败时写明原因",
  "method_spec": {
    "dataset": {"value": "...", "status": "reported", "source": {"paragraph_id": "p3", "quote": "..."}},
    "preprocessing": {"value": "...", "status": "reported", "source": {"section_id": "methods"}},
    "model": {"value": "...", "status": "reported", "source": {"paragraph_id": "p5", "quote": "..."}},
    "hyperparameters": {"value": {"lr": 0.001}, "status": "partially_reported", "source": {"repo_path": "config.yaml", "line": 12}},
    "metrics": {"value": ["accuracy"], "status": "reported", "source": {"table_id": "t1", "row": 2, "column": "accuracy"}},
    "seed": {"value": null, "status": "missing", "source": {}},
    "code_available": false,
    "data_available": true
  },
  "reproduction_level": "full|partial|not_reproducible",
  "reproduction": [
    {
      "item": "accuracy",
      "reported_value": 0.92,
      "reproduced_value": 0.90,
      "abs_error": 0.02,
      "rel_error": 0.0217,
      "tolerance": {"abs": 0.03, "rel": 0.05},
      "within_tolerance": true,
      "estimated": false,
      "comparison_status": "comparable|incomparable|not_run",
      "run": {
        "executed": true,
        "script": "work/repro.py",
        "log": "work/repro.log",
        "command": "python3 -I work/repro.py --seed 0",
        "exit_code": 0,
        "seed": 0,
        "stochastic": false,
        "repeats": 1,
        "synthetic_demo": false
      }
    }
  ],
  "gaps": [
    {"field": "seed", "status": "missing", "impact": "结果波动不可控", "next_check": "向作者索取 seed 或做多种子重复"}
  ],
  "code_audit": [
    {"file": "src/train.py", "line": 42, "severity": "major", "finding": "硬编码绝对路径"}
  ],
  "leakage_check": {
    "status": "no_leak_found|suspected|leak_detected|not_checked",
    "findings": [],
    "evidence": {"dataset_id": "...", "quote": "..."}
  },
  "env_spec": {
    "runtime": "Python 3.11",
    "dependencies": ["..."],
    "hardware": "...",
    "seed_policy": "...",
    "determinism_flags": []
  },
  "conclusion": {"supports_paper": "yes|partial|no|undetermined", "reason": "..."},
  "verdict": "reproducible|reproducible_with_notes|blocked|do_not_promote",
  "sources": [
    {"doc_id": "paper.pdf", "locator": "paragraph_id=p3", "quote": "逐字引文"}
  ],
  "uncertainty": [{"item": "...", "reason": "...", "impact": "..."}],
  "change_log": [{"step": 1, "action": "...", "evidence": "..."}]
}
```

状态映射（与通用校验器兼容）：`completed`=成功，`partial`=部分完成，`insufficient_evidence`=无法判断，`failed`=运行失败，`input_error`=输入不合法或拒答。`verdict` 只能降档不能升档。

## 复现规则

1. `run.executed=false` 时，`reproduced_value` 必须为 `null`，不得填猜测值。
2. `executed=true` 必须有真实脚本、日志、退出码和命令；退出码非 0 的运行不能产出成功复现值。
3. 随机过程固定 seed；多次运行给均值、标准差和 `repeats`。
4. 数据不可得时，可在合成数据上演示流程，但必须标 `synthetic_demo: true`，不得与原文数值做数值比较。
5. 相对误差计算口径：`|reproduced-reported|/|reported|`；`reported=0` 时写 `null`。
6. 原文只给图时，读数标 `estimated: true` 并计入误差；容差必须事前声明。
7. 每个 `missing` 字段都要有 `gaps[]` 条目；影响无法判断时写 `unknown`。
8. 数据或代码不可得时 `reproduction_level` 不得为 `full`；数据不可得时只能是 `not_reproducible`。

## 方法增强

- **七要素锁定**：task / data / split / model / metric / threshold / claim；目标不明确前不跑数，避免“跑了很多但不知道在验证什么”。
- **证据分三层**：源码检查（看到什么）、已完成运行（跑过什么）、未验证声明（只是文档说）；每项标 `evidence_class`，未运行的声明不得写成已复现。
- **证据包**：code version、data version、seed policy、hardware/runtime、精确评测命令；缺任一项 verdict 最多 `reproducible_with_notes`。
- **红队检查**：泄漏（训练见测试）、过拟合（挑样例/挑 seed）、缺基线、标签不全、隐私；四档 verdict 为 `reproducible / reproducible_with_notes / blocked / do_not_promote`。
- **静态代码审计**：缺 docstring、硬编码绝对路径、无 seed、裸 `except`、未锁版本、魔法数字；逐项记 `file:line` 与 severity。
- **改动可追溯**：改动前备份原文件；路径参数化、seed 固定、日志与错误处理齐全、环境规格落盘；改后必须验证同输入同输出（`behavior_preserved`）。
- **参数三类来源**：论文明确给出（paper/appendix）、从代码推断（code_default/code_inferred）、自行设定（assumed）；冲突保留并在 `gaps` 说明，不静默选一个。
- **结果对得上**：比较前写清指标、切分、阈值、统计方式和容差；原文图读数标 `estimated`；口径不同标 `incomparable`。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 运行真实 | `executed=true` 时有脚本、日志、退出码、命令，且 `check_reproduction.py` 能找到文件 | 改 `executed=false`，删除编造数值 |
| G2 | 数值来源 | 每个 `reproduced_value` 能在日志中找到 | 无来源的数值删除 |
| G3 | 可比性 | 指标、数据、切分、条件与原文同口径 | 标 `incomparable`，不给通过结论 |
| G4 | 随机性 | seed、重复次数、均值/标准差齐全 | 补运行；不能补则标 `partial` |
| G5 | 缺口 | 每个 `missing` 有 gap 且 `impact` 非空 | 补影响或标 `unknown` |
| G6 | 结论 | `supports_paper` 与证据强度一致 | 降级为 `partial/undetermined` |
| G7 | 容差 | 运行前声明 `tolerance`，误差重算一致 | 补计划或改 `within_tolerance` |
| G8 | 泄漏/verdict | 泄漏确认时 verdict 为 `do_not_promote` | 降档并说明 |
| G9 | 支持文件 | plan 已填、两个校验脚本均真实运行 | 未运行则不算交付 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 声称跑过但无日志 | `executed=true` 但找不到脚本/输出 | 改 false，删除编造数值 |
| seed 缺失 | 结果波动无法解释 | 固定 seed 重跑，或标 `partial` |
| 指标口径不同 | 复现值与原文值不可比 | 统一指标或标 `incomparable` |
| 合成数据冒充实测 | 使用 synthetic 却与原文数值比较 | 标 `synthetic_demo: true`，不做数值对比 |
| 数据泄漏 | 训练集含测试信息 | 指出泄漏，verdict 降为 `do_not_promote` |
| 过度结论 | 一次运行支持“完全复现” | 改为 `partial`，写误差与限制 |
| 参数来源混淆 | 把代码默认值写成论文设置 | 标 `code_default`，说明冲突 |
| 只挑有利 seed/样例 | 单次结果、样例挑选 | 多次运行 + 全量结果；否则降级 |
| 计划与结果不一致 | plan 的要素状态与 result 不同 | 同步更新，保留变更记录 |

## 自检

交付前逐项确认：

- [ ] `/app/work/reproduction-plan.json` 已从模板复制并填写，不是原始占位符。
- [ ] `python3 -I {SKILL_DIR}/scripts/check_reproduction.py ... --json` 退出码为 0，报告保存到 `/app/work/reproduction-check.json`。
- [ ] `python3 -I {SKILL_DIR}/scripts/validate_result.py ... --check-quotes` 退出码为 0。
- [ ] 每个 reproduction 项有 `executed` 标志；运行项有脚本、日志、退出码、命令、seed。
- [ ] 未运行项没有编造数值，且 `not_run_reason` 非空。
- [ ] 每个 `reproduced_value` 可在日志中回查。
- [ ] 每个 `missing` 有 gap 与 impact；冲突项保留。
- [ ] `reproduction_level`、`verdict`、`supports_paper` 三者不矛盾。
- [ ] 数据/代码受限时没有假装运行；合成演示没有与论文数值比较。
- [ ] `sources[]` 的关键引文可逐字回查。

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`task_family`、`status`、`method_spec`、`reproduction_level`、`reproduction[]`、`gaps[]`、`conclusion`、`verdict`、`sources`、`uncertainty`、`change_log`
- [ ] `/app/work/reproduction-plan.json` 与 `/app/work/reproduction-check.json`
- [ ] 实际运行的脚本与日志在 `/app/work`（或结果中写明的真实路径）
- [ ] 每个运行项有 `executed`、脚本、日志、退出码、命令、seed
- [ ] 未运行项没有编造数值
- [ ] 误差口径、容差与可比性说明清楚
- [ ] 结论未超出证据；泄漏或隐私风险已降档
- [ ] 支持文件均被真实调用，而不是只打包

## 最小调用示例（示例，不是正式案例结果）

```bash
# 1) 读材料并填计划
cp {SKILL_DIR}/templates/reproduction-plan.json /app/work/reproduction-plan.json
# 编辑 /app/work/reproduction-plan.json：填七要素、materials、参数、步骤与容差

# 2) 运行最小实验（示例命令，按实际材料替换）
python3 -I /app/work/repro.py --data /app/work/data.csv --seed 7 > /app/work/repro.log 2>&1
echo "exit_code=$?" >> /app/work/repro.log

# 3) 写 /app/output/result.json 后自检
python3 -I {SKILL_DIR}/scripts/check_reproduction.py \
  --result /app/output/result.json \
  --plan /app/work/reproduction-plan.json \
  --root /app/output --root /app/work --root /app \
  --json
python3 -I {SKILL_DIR}/scripts/validate_result.py \
  /app/output/result.json --input /input --check-quotes
```

结构示例（占位符，不代表任何正式案例结果）：

```json
{
  "item": "accuracy",
  "reported_value": "<from-paper>",
  "reproduced_value": "<from-log>",
  "abs_error": "<computed>",
  "rel_error": "<computed>",
  "tolerance": {"abs": "<pre-declared>", "rel": "<pre-declared>"},
  "within_tolerance": "<computed>",
  "run": {"executed": true, "script": "work/repro.py", "log": "work/repro.log",
          "command": "<exact-command>", "exit_code": 0, "seed": 7}
}
```
