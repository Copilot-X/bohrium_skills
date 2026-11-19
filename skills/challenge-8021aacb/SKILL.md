---
name: structure-logic-reviewer
description: 论文结构与逻辑审稿与改稿：读取真实稿件，建立章节—段落索引，按 D1 研究问题与贡献、D2 章节结构、D3 论证链、D4 段落关系、D5 重复冗余、D6 结论边界六维给出有原文定位、逐字引文、证据和可执行 edit 的审稿；默认 review-only，任务明确要求改稿时才生成 revised.md。Structure and logic review/revise across six grounded dimensions. Use for 结构评审、逻辑审查、论文诊断、审稿意见、修改清单、投稿前结构检查、改稿。
---

# 论文结构与逻辑优化（Structure & Logic Reviewer）v0.7

## 1. 用途与硬约束

读一篇真实稿件，交付**可定位、可执行、可复核**的六维结构审稿；任务明确要求改稿时，再生成 `revised.md`。

触发语：优化结构、梳理逻辑、检查论证链、论文诊断、审稿意见、修改清单、投稿前结构检查、结构评审、revise/edit the manuscript。

- 只评估稿件实际存在的问题；**不预设缺陷、不提供标准答案**。
- 稿件干净就写 `verdict:"ok"` + basis，`findings:[]`；不为了凑数造 finding。
- 每条判断必须指回章节/段落/逐字原文，并给出可执行 edit。
- 材料不足写 `not_assessed`；不凭领域标签补造内容。
- 默认**只读审稿**；只有 task.md 或 `output_contract` 明确要求修改稿件/返回修订稿时才输出 `revised.md`。
- 自动改稿不改数字、单位、条件与引用键；无法保证时降级为人工编辑并记录原因。

## 2. 输入与输出

输入：`/input/task.md`（案例要求）、`/input/case.json`（`case_id`、稿件路径/文件列表、`output_contract`）、稿件（`.md/.markdown/.txt/.tex/.latex/.docx/.pdf`），可选期刊要求与上一轮意见。

输出目录 `/app/output`：

- `structure-review.json`：主审稿，含 `dimensions`、`findings`、`patterns`、`priority_order`、`not_assessed`、`uncertainty`、`change_log`、`preserved_facts`、`evidence_map`。
- `result.json`：任务字段 + 官方四字段 `deliverable / preserved_facts / evidence_map / change_log`，路径均真实存在。
- 仅 revise：`revised.md`（可由模型直接写，或由 `apply:true` 的安全编辑生成）。

PDF 先用运行时 `pdf-text-tool`（或 `pdftotext`/`mutool`）抽文本；无文本层时只评可见文字，并在 `uncertainty` 标记 `quotes_unverified`。

## 3. 安装与唯一入口

```bash
export SKILL_DIR="${SKILL_DIR:-/skills/weekly-skill}"
if [ ! -d "$SKILL_DIR/scripts" ]; then
  for d in /skills/weekly-skill /app/skills/weekly-skill "$(pwd)"; do
    [ -d "$d/scripts" ] && export SKILL_DIR="$d" && break
  done
fi
mkdir -p /app/work /app/output
```

所有命令使用 `python3 "$SKILL_DIR/scripts/main.py" ...`。脚本仅用 Python 标准库，不依赖网络。

```bash
# 完整流程：inventory → workorder → diagnose → (apply) → emit
python3 "$SKILL_DIR/scripts/main.py" run --input /input --work /app/work --output /app/output
# 强制只读 / 只诊断 / 应用编辑 / 投影 result
python3 "$SKILL_DIR/scripts/main.py" review --input /input --work /app/work --output /app/output
python3 "$SKILL_DIR/scripts/main.py" diagnose --input /input --work /app/work --output /app/output
python3 "$SKILL_DIR/scripts/main.py" apply --work /app/work --output /app/output --review /app/output/structure-review.json
python3 "$SKILL_DIR/scripts/main.py" emit --work /app/work --output /app/output --review /app/output/structure-review.json
```

`--mode auto|review|revise`；`--manuscript PATH` 显式指定稿件。无子命令时默认 `run`。退出码：0 已写出交付物（**含诚实的 partial / input_error**）；只有完全无法启动（如输出目录不可写）才 exit 2。

## 4. 执行顺序（模型负责判断）

1. **inventory**（约 10%）：`scripts/inventory.py` 扫描输入、记录大小/哈希、抽取文本；按 `case.json` 路径提示优先选稿件。找不到可读稿件就写 `input_error`，不编造。
2. **workorder**（约 10%）：`scripts/workorder.py` 读 task.md 与 `output_contract`，判定 `review_only`（默认）或 `revise`，确定 `case_id`、体裁、期望章节、官方字段与保护约束；依据写入 `mode_basis`。
3. **diagnose**（约 30%）：`scripts/diagnose.py` 调用 `scripts/structure_kit.py` 建章节—段落索引，跑 D1–D6，写 `<work>/structure-map.json` 与 `<work>/diagnosis.json`。这些只是**有原文锚点的候选**，不是最终判断。
4. **模型复核与写作**（约 35%）：模型必须自己通读稿件与 `diagnosis.json`：
   - 逐条核对 `location.quote` 是否逐字来自原文；虚构或错位的 finding 删除；
   - 把 `problem` / `suggestion` 改写成自己的审稿判断，保留原文定位与可执行 edit；
   - 补上规则漏掉的真实问题，或在 `verdict/ok` 的维度写明依据；
   - 不把风格偏好写成结构错误；不为了“显得完整”保留假 finding；
   - 把定稿写回 `<work>/diagnosis.json`（或 `--review` 指定的审稿 JSON）。
5. **apply / emit**（约 15%）：review-only 时 `scripts/actions.py` 只把 `apply` 置为 false 并记录“未改动原稿”；revise 时只应用 `apply:true` 且通过保护检查的 edit。`scripts/emit.py` 写 `structure-review.json` 和 `result.json`。

`main.py run` 可快速得到可用基线；追求质量时用“diagnose → 模型复核 → emit”，不要把模板化基线当成最终审稿。

## 5. 六维（交付必填）

| ID | 维度 | 核心检查 | 可观察锚点 |
|---|---|---|---|
| D1 | 研究问题与贡献主张 | 问题是否提出并贯穿；贡献是否与实际工作一致 | 问题句/贡献句 vs 方法设计 |
| D2 | 章节结构与叙事顺序 | 缺章、错序、空节、篇幅与功能错位 | 章节功能序列、词数占比 |
| D3 | 论证链与论据对应 | 讨论是否首次报告新结果；结论是否有结果支撑 | 结果提示词 + 结果部分缺失的数值 |
| D4 | 段落关系与衔接 | 小节首段无承接转折；超长多主题段 | 首句转折词、句数/词数 |
| D5 | 重复与冗余 | 跨章节逐字重复、同章重复 | 规范化后 ≥12 字符的相同句 |
| D6 | 结论边界与表述尺度 | 相关写成因果、全称夸大、限制缺失 | 因果动词 + 关联设计；全称量词；限制词 |

`A1`（图表—正文引用）与 `A2`（术语与指代）可选，写入时必须在 `dimensions` 中声明。严重度：`blocker`（不修则结论不成立）、`major`（实质缺口可补）、`minor`（衔接/重复/措辞）；不要全部标 blocker。

## 6. finding 与 edit 契约

```json
{
  "finding_id": "F-01", "dimension": "D3", "severity": "major", "priority": "P2",
  "location": {"chapter": "4", "chapter_title": "4 讨论", "section": "4.1",
               "paragraph_id": "p4.1.1", "quote": "原文逐字片段"},
  "problem": "讨论首次报告结果部分没有的新数值发现",
  "evidence": [{"kind": "text", "locator": "p4.1.1", "quote": "原文逐字片段"},
               {"kind": "omission", "locator": "结果章节 3；未出现 β=-0.18"}],
  "suggestion": "把该句移入结果 3.2 并补对应分析，或删除该新增结果",
  "edit": {"type": "move", "before": "原文逐字片段", "target": "p4.2.1"},
  "apply": false
}
```

- `location.quote` 必须逐字来自稿件；缺失章节/图表可用 `paragraph_id=document/chapter/section` + `evidence.kind=omission/structure`。
- `edit.type` 词表：`move / reorder / merge / split / delete / insert / add_transition / rewrite / narrow / relabel`。
- `delete` 只需 `before`；`move/merge/split/reorder` 用 `target`；内容类动作用 `after`。
- `evidence[]` 至少一条，含 `kind + locator`；文本/数字类带逐字 `quote`。
- 同类问题 ≥2 条时合并为 `patterns`；`priority_order` 覆盖全部 id（P1→P3）。
- 各维 `findings` 计数必须等于实际数；`verdict=ok` 的维度不能带 finding。
- `status`：六维已评且无缺口为 `completed`；有 `not_assessed`/材料缺口为 `partial`；无稿件为 `input_error`。

## 7. 双模式与保护

**review-only（默认）**：不写 `revised.md`；`apply` 全为 `false`；`change_log` 至少一条“未改动原稿；仅提交审稿与编辑方案”；主交付物是 `structure-review.json`。

**revise（明确要求改稿）**：只处理 `apply:true` 的 finding；模型可直接写 `revised.md`，但必须逐条写 `change_log`（含 diff/原因）。自动应用支持 `rewrite / narrow / relabel / insert / add_transition / delete / move / merge / split`；`reorder` 默认人工执行并写明原因。保护检查：数字/统计量/单位/年份/引用键集合保持不变；改写不得丢条件词；`delete` 若含数字/引用则拒绝；新增文字不得引入数字或引用。全部应用后做全局 token 比对，不一致则整批回滚；没有安全可应用项时不伪造 `revised.md`，在 `limitations` 说明。

## 8. result.json 官方字段

- `case_id`：与 `/input/case.json` 一致；
- `deliverable`：review-only → `{"path":"structure-review.json","format":"json","role":"review"}`；revise → `{"path":"revised.md","format":"markdown","role":"revised"}`；
- `preserved_facts`：稿件中未被改动的数值/单位/条件/引用定位（`fact_id/value/unit/locator/quote`）；
- `evidence_map`：finding/claim → 来源定位与逐字引文（`claim_id/claim/source/locator/quote`）；
- `change_log`：实际改动或“未改动”记录。

同时保留 `status / mode / manuscript / dimensions / findings / patterns / priority_order / artifacts / not_assessed / uncertainty / coverage / limitations`；原始 `output_contract` 原样放入 `output_contract`，`contract_fields` 列出要求字段。

## 9. 支持文件

| 文件 | 何时用 | 用法 |
|---|---|---|
| `scripts/main.py` | 唯一入口 | `run/review/diagnose/apply/emit/inventory/workorder` |
| `scripts/structure_kit.py` | 共享工具 | 文本/JSON/哈希、句子切分、数字与引用 token、章节—段落索引 |
| `scripts/inventory.py` | 扫描输入、发现稿件、抽文本 | `--input /input --work /app/work` |
| `scripts/workorder.py` | 判定 review-only / revise | `--input /input --work /app/work` |
| `scripts/diagnose.py` | D1–D6 诊断，生成候选审稿 | `--work /app/work` |
| `scripts/actions.py` | 应用 `apply:true`，生成 `revised.md` | `--work /app/work --output /app/output` |
| `scripts/emit.py` | 写 `structure-review.json` + `result.json` | `--work /app/work --output /app/output` |
| `references/issue_taxonomy.md` | 六维问题分类、锚点、严重度、非问题清单 | 写/改 finding 前查阅 |
| `references/edit_action_spec.md` | edit 类型、target、自动应用保护、change_log 格式 | 写/改 edit 或排查 apply 失败时查阅 |

脚本失败时仍可按本文件手工完成 review/result，并在 `uncertainty` 写 `tool_unavailable`；不要为了通过检查删除真实 finding。

## 10. 最小示例（示例，不是正式答案）

输入：一篇含“讨论新增结果、小节首段无承接、跨章节重复句、结论因果越界”的合成稿件，`case.json` 只要求审稿。

```bash
python3 "$SKILL_DIR/scripts/main.py" run --input /input --work /app/work --output /app/output
```

示意输出（字段片段，非正式案例结果）：

```json
{"case_id": "example-case", "status": "completed", "mode": "review_only",
 "deliverable": {"path": "structure-review.json", "format": "json", "role": "review"},
 "preserved_facts": [{"fact_id": "PF-01", "value": "1.7", "unit": "分",
                      "locator": "p3.2.1", "quote": "……下降 1.7 分……"}],
 "evidence_map": [{"claim_id": "F-01", "claim": "讨论首次报告新数值",
                   "source": "manuscript.md", "locator": "4/4.1/p4.1.1",
                   "quote": "……（β = -0.18，p = 0.04）。"}],
 "change_log": [{"finding_id": "none", "action": "未改动原稿；仅提交审稿与编辑方案",
                 "diff": "无（review-only）", "applied": false}]}
```

## 11. 异常处理

| 情况 | 处理 |
|---|---|
| 无稿件/路径不可读 | `status:"input_error"`，六维 `not_assessed`，`findings:[]`，result 仍写出四字段，exit 0 |
| 只有部分章节/节选 | 只评已有部分；缺失功能维度 `not_assessed`，`status:"partial"` |
| PDF 无文本层 | 先转文本；失败时只评可见文字，引文标 `S/M`，`quotes_unverified` 写入 uncertainty |
| docx 标题未套 Heading | 手工补章节树；在 uncertainty 说明边界可能不完整 |
| 章节编号混乱 | 以 `structure-map.json` 的 `paragraph_id` 为唯一定位，不按行号 |
| 稿件被截断 | 只评已有内容；D6 等边界维度标 `partial/not_assessed` |
| task.md 与 case.json 冲突 | 以案例 output_contract/task.md 为准，冲突写入 uncertainty |
| 契约要求改稿但无安全编辑 | 不伪造 revised.md；逐条记录拒绝原因并给人工编辑方案 |
| 脚本/依赖失败 | 继续手工交付；写 `tool_unavailable`，按质量闸门核对 |
| 时间不足 | 先保 `structure-review.json` + `result.json`；revised 可后补 |

## 12. 质量闸门与版本

必须：两个 JSON 都存在且可 `json.load`；`case_id` 与输入一致；D1–D6 都有 `verdict + basis`；每条 finding 有 `location{chapter,paragraph_id,quote}`、逐字引文、结构化 `evidence`、具体 `suggestion`、可执行 `edit`；`priority_order` 覆盖全部 id 且各维计数一致；数字/条件/引用未编造；review-only 时 `apply:false` 且无 `revised.md`；result 含官方四字段且 artifact 路径真实；revise 时 `change_log` 逐条有 diff/原因且 token 保护通过。

建议：同类 finding 合并 `patterns`；blocker 写 `devils_advocate`；低置信项写 `uncertainty`；findings 必须由模型逐条核对后才算定稿。

版本 `0.7.0`（2026-11-17）：单入口最小包；章节索引合并进 `structure_kit.py`；入口对 partial/input_error 也写 `result.json` 并 exit 0；默认 review-only。限制：启发式诊断只覆盖可观察结构信号，不判断学术对错；PDF 依赖文本层；docx 依赖 Heading 样式；自动改稿只做确定性文本操作，语义取舍需作者复核。维护：先改两个 references，再同步 `diagnose.py` / `actions.py` / 本文件；脚本保持 stdlib-only，路径统一通过 `"$SKILL_DIR"` 解析。
