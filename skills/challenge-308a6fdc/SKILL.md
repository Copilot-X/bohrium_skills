---
name: academic-manuscript-writer
description: 学术论文写作：读取 /input 的真实材料，先由模型写出可编辑 manuscript.md，再由唯一入口 scripts/main.py 生成官方四字段 deliverable/preserved_facts/evidence_map/change_log。每个数字绑定 source path+locator，引用编号与条目双向核对，材料缺口写入 uncertainty。Use for 论文写作、摘要/章节起草、全文改稿、Results/Discussion 写作、manuscript drafting with traceable evidence.
---

# 学术论文写作（Academic Manuscript Writer）

## 触发

输入含实验记录、数据表、图注、参考文献、已有草稿或写作要求，且 task.md 要求产出
论文/章节/摘要/改稿时使用。典型触发词：写论文、起草正文、写摘要/引言/方法/结果/
讨论、把材料整理成稿件、按期刊结构改写、补全章节、每条论断要有出处。

不适用于：只做格式转换、只做语言润色/翻译、只做证据综述、材料缺失但仍要求编造数据。

核心原则：**正文由模型写，入口只做来源、契约、装配与检查。** 先落盘完整
`manuscript.md`，再运行 `main.py` 生成官方字段；骨架稿只能以 `partial` 交付。

## 输入与输出

输入（以实际读到的文件为准）：

- `/input/task.md`：体裁、受众、语言、篇幅、章节、引用格式、必含声明（最权威）；
- `/input/case.json`：`case_id`、`task_instruction`、`constraints`、`output_contract`；
- `/input/materials/**`：实验记录、CSV/TSV、图注、草稿、`.bib`/参考文献；
- `environment/resources.yaml`：可用工具；PDF 使用运行时 `pdftotext`/`pdf-text-tool`。

输出到 `/app/output`：

- `manuscript.md`：完整、可编辑、章节层级保留的主稿（主产物，由模型写）；
- `result.json`：官方四字段 + `case_id`/`status`/`contract`/`source_map`/
  `sentence_map`/`citations`/`conflicts`/`uncertainty`/`change_log`；
- `source-map.json`、`change-log.json`、`manuscript-plan.json`、
  `manuscript-audit.json`：证据、留痕、计划与检查结果。

中间产物在 `/app/work`：`inventory.json`、`contract.json`、`sources.json`、
`references.json`、`evidence-ledger.json`、`outline.json`、`writing-brief.md`。

## 支持文件（实际使用）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/manuscript-contract.md` | 写作前/判定章节与边界 | 体裁、结构、证据等级、引用、结论边界规则 |
| `references/limits.md` | 材料缺失/异常/降级 | 限制矩阵与重跑方式；交付前读 |
| `scripts/main.py` | **唯一执行入口** | `prepare` / `assemble` / `run`；不要直接调用其他脚本 |
| `scripts/writing_lib.py` | 引擎实现（被入口导入） | inventory/contract/sources/facts/plan/bind/result |
| `scripts/task_specific.py` | 论文体裁检查（被入口导入） | 章节/摘要/引用/数字/结论边界/声明字段检查 |
| `examples/example_input.json` | 想看接口示例 | synthetic case.json 形状；不是正式案例或答案 |

`{SKILL_DIR}` 通常为 `/skills/weekly-skill`。示例全部标注 synthetic，不得当作
官方测试输出提交。脚本为 stdlib、无网络依赖。

## 路径解析（先做）

```bash
export SKILL_DIR="$(dirname "$(find /skills -maxdepth 3 -name SKILL.md 2>/dev/null | head -n 1)")"
[ -f "$SKILL_DIR/SKILL.md" ] || export SKILL_DIR="/skills/weekly-skill"
mkdir -p /app/work /app/output
```

后续命令一律使用 `"$SKILL_DIR/scripts/main.py"`。

## 工作流（正文优先）

### 第 1 步：prepare（约 15% 预算）

```bash
python3 "$SKILL_DIR/scripts/main.py" prepare \
  --input /input --work /app/work --output /app/output
```

`main.py` 依次调用 `writing_lib.py`：

1. **inventory**：扫描 `/input`，记录路径、sha256、类型、可读文本；Markdown/TXT/
   JSON/CSV/TeX/BIB 用 stdlib，DOCX 读 `word/document.xml`，PDF 尝试
   `pdftotext`/`pdf-text-tool`，失败标 `readable=false` 并进 `uncertainty`。
2. **contract**：从 task.md/case.json 归一化 `genre/language/sections/
   word_budget/citation_style/required_declarations/output_contract`。
3. **sources**：按段落/CSV 行/BIB entry 切块，带 `path + locator`；参考文献编号。
4. **evidence ledger**：抽出数值句与条件，生成 `F001...` fact；近似值标
   `estimated`，其余 `verified`；每条带 `source.path/locator/quote`。
5. **outline + brief**：每个章节绑定 fact_ids 与 citation_numbers；无材料章节标
   `not_supported`，并写 `work/writing-brief.md` 给模型。

若 `manuscript.md` 尚不存在，入口写一个明确标注 `PARTIAL SCAFFOLD` 的骨架；
骨架永远以 `partial` 交付，不能冒充完成稿。

### 第 2 步：写完整正文（约 55% 预算，最重要）

按 `work/writing-brief.md` 与 `work/evidence-ledger.json`，用真实学术文风写出
`/app/output/manuscript.md`（或写到 `/app/work/draft.md`）：

- 覆盖 task.md 的每个章节/问题，满足体裁、语言、篇幅、受众；
- 每个数字、单位、条件、样本量、比较对象都来自 `evidence-ledger.json`；
  材料没有的数字不写，材料不足写“现有材料未报告……”；
- 引用 `[n]` 放在支撑该句的位置；文末条目只写材料给出的字段；
- 相关不写因果，单材料结果不写成领域共识，摘要不出现正文没有的数字；
- 声明字段缺材料写 `not_provided_in_materials`；
- 材料冲突并列保留，不取平均、不选边；
- **不要保留 `PARTIAL SCAFFOLD` 标记，不要提交骨架。**

### 第 3 步：assemble（约 25% 预算）

```bash
python3 "$SKILL_DIR/scripts/main.py" assemble \
  --input /input --work /app/work --output /app/output \
  --draft /app/work/draft.md
```

若正文已直接写在 `/app/output/manuscript.md`，可省略 `--draft`。入口读取主稿，
重建 `sentence_map`（句子 → fact_ids/citation_numbers），计算章节字数与状态、
摘要数字子集、引用编号/条目、未溯源数字、结论边界、声明字段，运行
`task_specific.py`，最后重写 `source-map.json`、`change-log.json`、
`manuscript-plan.json`、`manuscript-audit.json` 与 `result.json`。

### 第 4 步：run（时间紧或 smoke test）

```bash
python3 "$SKILL_DIR/scripts/main.py" run \
  --input /input --work /app/work --output /app/output
```

`run = prepare + assemble`。存在模型稿时保留模型稿；否则输出 `PARTIAL SCAFFOLD`，
`status` 如实标 `partial`/`insufficient_input`。任何时候 partial/input_error 都
会写 `result.json` 并 **exit 0**；只有完全无法启动才 exit 2。

## 官方输出契约

官方 `output_contract` 要求四个顶层字段，由 `main.py` 生成，**不手工拼**：

| 字段 | 内容 | 路径要求 |
|---|---|---|
| `deliverable` | 可编辑主稿 | `path=manuscript.md`，文件真实存在 |
| `preserved_facts` | 数字/条件/术语/来源 | 每条 `source.path + locator` |
| `evidence_map` | 关键句 → fact → 来源 | 句级 `fact_ids` + `quote` |
| `change_log` | 动作/原因/证据/未决项 | 由入口按实际步骤写入 |

`result.json` 同时保留 `case_id`、`task_family`、`status`、`contract`、
`manuscript`、`artifacts`、`source_map`、`sentence_map`、`citations`、
`conflicts`、`missing_materials`、`coverage`、`uncertainty`。`status` 取值：

- `completed`：模型稿、结构齐全、检查无 problem、主稿可编辑；
- `partial`：主稿存在但有缺章/缺材料/检查问题，或仍是骨架；
- `insufficient_input`：无可读材料；
- `input_error`：连 `case.json`/`task.md` 都没有，不生成正文。

若 `output_contract` 使用别的字段名，原字段原名保留，四字段别名同时存在。
**不要编造 DOI、基金号、伦理批号、作者贡献或样本量。**

## 写作规则（摘要）

完整规则见 `references/manuscript-contract.md`：

1. 摘要只从正文/`source_map` 取数；摘要数字必须是正文子集。
2. 结果段按「现象 → 数值 → 条件 → 对照」写；无检验不写“显著”。
3. 相关性不写因果；未验证机制用 may/suggest/提示/可能，并在讨论给限制。
4. 方法段可复现：样品、仪器、参数、统计；缺项写 `not_reported`。
5. 讨论每条主张回到结果或标为推测；结论只回答任务问题。
6. 引用编号连续、条目双向对应、每条支撑所在句。
7. 估计值（图读数/约/approx）不进摘要与结论的确定表述。
8. 术语/缩写首次出现给全称；数值与单位格式统一。

## 异常处理

| 情况 | 处理 |
|---|---|
| 只有图没有原始数据 | 只写图面可见内容并标 `estimated`，不反推数据表 |
| 图注与正文数值冲突 | 两值进 `conflicts[]`，正文并列/脚注，不静默裁决 |
| 指定章节无材料 | 该节 `status=not_supported`，正文写“材料未提供”，保留标题 |
| 参考文献信息不全 | 只写可得字段，缺 DOI 写 `not_available`，不编造 |
| 无任何参考文献 | 参考文献节明确为空，正文不生成 `[n]` |
| PDF 解析失败 | 该文件标 `readable=false`，其余材料继续，写入 `uncertainty` |
| 篇幅超限 | 先压 Intro/Discussion，保 Results/Limitations；不截断主稿 |
| 时间/预算不足 | 先写完整可编辑稿再 `assemble`；无法完成时 `run` 落盘骨架并标 `partial` |
| 检查器报错 | 主稿保留，异常写 `manuscript-audit.json` 与 `uncertainty` |

更多降级行为见 `references/limits.md`。

## 最小示例（synthetic，非正式答案）

`examples/example_input.json` 展示输入 `case.json` 的接口形状（`case_id`、
`task_instruction`、`constraints`、`output_contract`），不是测试答案。把同样的
字段与一份 `task.md`、若干 `materials/` 放进同一目录即可按上述命令运行。

## 交付清单

- [ ] `manuscript.md` 非空、可编辑、无 `PARTIAL SCAFFOLD`，章节与 task.md 一致；
- [ ] `result.json` 含官方四字段与 `case_id`，所有声明路径真实；
- [ ] 每个数值有 `source.path + locator`；摘要数字是正文子集；
- [ ] 引用编号连续、条目存在、每条支撑所在句；
- [ ] 估计值未进摘要/结论；因果措辞有证据或限定；
- [ ] 缺材料章节 `not_supported`，声明字段不虚构；
- [ ] `uncertainty` 与 `change_log` 反映实际缺口和动作。
