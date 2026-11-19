# 论文写作契约（体裁、结构、证据、引用、边界）

本文件是 `scripts/main.py` 判定“写什么、写到哪、什么不能写”的规则说明。
执行时 **`/input/task.md` > `/input/case.json` 的 constraints/output_contract >
本文件默认值**；材料没有的事实、数字、引用、声明一律不补。

## 1. 体裁与结构

| 体裁 | 识别信号 | 默认章节（按序） | 核心约束 |
|---|---|---|---|
| research-article | 默认；有方法/结果 | abstract, introduction, methods, results, discussion, conclusion, references | 六段式齐全；方法与结果必须可分 |
| review | 综述、systematic review | abstract, introduction, main_body, discussion, conclusion, references | 主体按主题组织，不虚构方法/结果 |
| letter | 快报、letter、correspondence | abstract, introduction, methods, results, discussion, references | 篇幅不超过预算 1.5 倍 |
| case-report | 病例报告 | abstract, introduction, case_presentation, discussion, conclusion, references | 病例资料与讨论分开 |
| abstract | 只写摘要 | abstract | 不生成其他章节 |

`task.md` 明确列出的章节与顺序优先。缺材料的内容章节标 `not_supported`，
并在正文写出“材料未提供”，不得用通用知识填满。声明字段（数据可得性、利益冲突、
作者贡献、伦理、基金）缺材料时写 `not_provided_in_materials`，由作者确认。

## 2. 输入与契约提取

`main.py prepare` 读取：

- `/input/task.md`：体裁、受众、语言、篇幅、章节、引用格式、必含声明；
- `/input/case.json`：`case_id`、`task_instruction`、`constraints`、`output_contract`；
- `/input/materials/**`：实验记录、表格、图注、草稿、参考文献（PDF 需运行时工具）；
- `environment/resources.yaml`：可用工具（不假设未声明的软件）。

产物：`work/contract.json`（`genre/language/sections/required_sections/
word_budget/citation_style/required_declarations/output_contract`）、
`work/evidence-ledger.json`、`work/sources.json`、`work/outline.json` 与
`work/writing-brief.md`。无法满足的项进 `unmet`，不要用流畅文字掩盖。

## 3. 证据等级与台账

每个数值/关键句在 `source_map` 中占一条：

```json
{"fact_id":"F001","statement":"...","value":"92%","unit":"%",
 "condition":"350 °C, 1 bar, 12 h, n=3","kind":"reported",
 "verification_status":"verified|estimated|unverified",
 "source":{"path":"materials/experiment-log.md","locator":"lines 4-5"}}
```

- `verified`：材料直接报告；`estimated`：图读数或带“约/approx/~”的近似值；
  `unverified`：材料自身存疑。
- `estimated/unverified` 不得进入摘要与结论的确定性表述，只能出现在方法或限制中。
- 图没有原始数据时只描述图面可见内容并标 `estimated`，不反推数据表。
- 术语、缩写、单位、样本量、统计显著性、图注编号与来源逐字一致。
- 材料冲突（同一指标两个值）进 `conflicts[]`，并列保留、不静默裁决。

## 4. 章节功能与写法

| 章节 | 必须回答 | 禁止 |
|---|---|---|
| Abstract | 问题、方法、关键结果、有界结论 | 正文/source_map 之外的新数字 |
| Introduction | 材料范围内的背景、缺口、本文动作 | 教科书式长背景、编造“首次” |
| Methods | 可复现的样品、仪器、参数、统计 | 结果与机制解释 |
| Results | 现象 → 数值 → 条件 → 对照 | 无检验写“显著”、把相关写因果 |
| Discussion | 结果含义、与材料内文献的关系、局限 | 新数据、样本外外推 |
| Conclusion | 回答任务问题、贡献、边界 | 重复摘要、领域级断言 |
| References | 与正文编号一一对应 | 编造作者/年份/期刊/DOI |

先写 Methods/Results（有据可依），再写 Intro/Discussion/Abstract；摘要数字必须是
正文/source_map 的子集；精度统一（92.1% 不写成 92%）。

## 5. 引用规则

- 编号从 `[1]` 起连续；正文每个编号必须有文末条目，文末条目必须被正文引用。
- 每条引用必须支撑它所在的那句话；禁止装饰性引用与“引而不证”。
- 作者、年份、标题、期刊、卷期、DOI 与材料逐字一致；缺 DOI 写
  `doi:not_available`，不生成假 DOI。
- 引用风格由 task.md 决定（GB/T 7714、APA、IEEE、Vancouver 等）；风格未知时保留
  材料原格式并在 `uncertainty` 说明。
- 材料只给题名/作者时，只写可得字段；不补卷期页码。

## 6. 结论边界

- 相关性、趋势、单点观测不写成因果；机制未验证时用
  “may/suggest/提示/可能”，并在讨论中给限制。
- 无统计检验不写“显著（significant）”；有检验时写检验名、统计量与 p 值。
- 摘要与结论不得出现估计值；不得把单篇/单材料结果写成领域共识。
- 材料没有的对照、样本量、时间窗、应用场景不补写。
- 结论句必须能回到 `source_map` 的具体 fact；不能回到来源的判断改写为“推测”。

## 7. 正文、装配与留痕

- **正文由模型写**：`prepare` 只产出契约、台账、`work/writing-brief.md` 与一个
  明确标注 `PARTIAL SCAFFOLD` 的骨架；骨架永远只能以 `partial` 状态交付。
- 模型把完整正文写入 `/app/output/manuscript.md` 或 `/app/work/draft.md`，
  再运行 `main.py assemble --draft ...`。入口负责句子→fact→citation 绑定、
  结构/引用/数字/边界检查，并生成官方四字段。
- 主产物是 `manuscript.md`（需要 Word 时再导出 docx）。章节层级、图注编号、引用
  编号保留，作者可直接继续修改。
- `change_log` 记录每步动作、原因与证据；`uncertainty` 记录材料缺口、冲突、
  未完成检查与人工确认项。
- `status` 取值：`completed`（模型稿、结构完整、检查无 problem）、`partial`
  （有可编辑稿但存在缺口或仍是骨架）、`insufficient_input`（无可读材料）、
  `input_error`（无 case/task）。

## 8. 交付前自检

- [ ] 契约章节齐全；缺材料章节标 `not_supported`。
- [ ] 每个数值能在 `source_map` 找到 `path + locator`。
- [ ] 摘要数字是正文/source_map 子集；四处数值一致。
- [ ] 引用编号连续、条目存在、每条支撑所在句。
- [ ] 估计值未进入摘要/结论；因果措辞有证据或限定。
- [ ] 声明字段不虚构；材料缺口进 `uncertainty`。
- [ ] `manuscript.md` 非空、可编辑、不是 `PARTIAL SCAFFOLD`；
      `result.json` 四字段路径真实。
