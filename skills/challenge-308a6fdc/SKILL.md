---
name: academic-manuscript-writer
description: 学术论文写作：基于实验记录、图表与参考文献起草完整论文或章节，每个数值与论断都绑定来源，输出 manuscript.md/docx、source-map.json 与 change-log.json。Draft a complete academic manuscript with every number and claim bound to a source location into /app/output. Use for 论文写作、摘要/章节起草、全文改稿、Results/Discussion 之外的正文写作。
---

# 学术论文写作（Academic Manuscript Writer）

## 用途与触发

输入含实验记录、图表、参考文献、已有草稿或写作要求，且 task.md 要求产出论文/章节/摘要/改稿时使用。
不适用于只做格式转换（见 `96263d10`）或只做语言润色（见 `bb17a294`）。

## 输入

- `/input/task.md`：体裁、受众、语言、篇幅、章节清单、引用格式、必须声明项（**最权威**）
- `/input/case.json`：`case_id`、`paper_id`、`material_version`、`output_contract`
- 材料：`materials/**`（实验记录、数据表、图、图注、参考文献、已有草稿）
- `environment/resources.yaml`：可用工具

## 执行步骤

1. **契约提取**：先读 task.md 与 case.json，把所有硬约束写成 `/app/work/contract.json`
   （章节、字数、格式、语言、必含字段）。若存在 `output-contract*.json`，字段名照抄。
2. **材料索引**：扫描 `/input`，生成 `/app/work/material-index.json`
   （路径、类型、对应章节、缺失项、冲突项）。
3. **证据台账**：逐条记录可用事实到 `/app/work/evidence-ledger.json`，
   `kind ∈ {reported, derived, inferred}`，数值带单位与条件。
4. **大纲**：写 `/app/work/outline.json`，每个 section 绑定 claim id；
   材料不足的章节进 `unsupported_sections`，不硬写。
5. **分节起草**：一次一节，写完立刻更新 `/app/work/sentence-map.json`
   （句子 → fact_id）。先写 Methods/Results（有据可依），再写 Intro/Discussion。
6. **引用与声明**：每条引用绑定到具体论断；声明字段缺材料时写
   `not_provided_in_materials`，不虚构基金号、伦理批号、作者贡献。
7. **一致性检查**：摘要、正文、图表、结论四处数值互校；冲突写入 `conflicts[]`，
   不擅自裁决。生成 `/app/output/change-log.json`。
8. **落盘**：`/app/output/manuscript.md`；需要 Word 时用
   `scripts/make_docx.py --spec doc.json --out /app/output/manuscript.docx`，
   再用 `--check` 回读。最后写 `result.json`。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "manuscript",
  "status": "completed|partial|insufficient_input",
  "manuscript": {
    "path": "manuscript.md",
    "genre": "research-article|review|letter|abstract",
    "language": "en|zh",
    "word_count": 0,
    "sections": [{"id": "results-1", "title": "3.1 …", "words": 0,
                  "claims": ["F001"], "status": "written|partial|not_supported"}]
  },
  "artifacts": [
    {"role": "editable", "path": "manuscript.md", "format": "markdown"},
    {"role": "export", "path": "manuscript.docx", "format": "docx"},
    {"role": "evidence", "path": "source-map.json", "format": "json"},
    {"role": "log", "path": "change-log.json", "format": "json"}
  ],
  "source_map": [
    {"fact_id": "F001", "statement": "…", "value": "18.2", "unit": "pp",
     "condition": "350 °C, n=3", "kind": "reported",
     "source": {"path": "materials/results.csv", "locator": "row 4-6"},
     "used_in": ["results-1", "abstract"]}
  ],
  "conflicts": [{"topic": "样本量", "a": "正文 n=24", "b": "表1 n=26", "handling": "正文采用 n=24 并标注"}],
  "missing_materials": ["作者单位", "基金编号"],
  "uncertainty": [{"item": "…", "reason": "…", "impact": "…"}],
  "change_log": [{"step": 1, "action": "…", "evidence": "…"}]
}
```

若 `output_contract` 规定了别的字段名，**顶层按契约输出**，本契约内容放入
`extended` 字段，两者都不丢。

## 写作规则

1. 摘要只从 `source_map` 取数，不得出现正文没有的数字。
2. 结果段按「现象 → 数值 → 条件 → 对照」写；不写"显著"除非材料给了检验。
3. 相关性不写成因果；未验证机制写 `inferred` 并在讨论中限定。
4. 方法段必须能复现：试剂/仪器/参数/统计方法，缺项写 `not_reported`。
5. 讨论段每条主张要么指回结果，要么标为推测并给依据链。
6. 术语表 `/app/work/terms.json` 先建后用，缩写首次出现给全称。
7. 数字格式统一（`.` 小数、`–` 范围、数值与单位间空格）。

## 引用与声明

- 作者、年份、期刊、卷期、DOI 必须与材料逐字一致；查不到 DOI 写 `doi: "not_available"`。
- 不允许"装饰性引用"：每条引用必须支撑它所在的那句话。
- 数据可得性、利益冲突、作者贡献、伦理审批、基金：材料给什么写什么。

## 异常处理

| 情况 | 处理 |
|---|---|
| 材料只有图没有原始数据 | 读数并标 `estimated`，不伪造数据表 |
| 图注与正文数值冲突 | 两值都记入 `conflicts`，正文用正文值并脚注 |
| 指定章节无材料 | `status: "not_supported"`，写"材料未提供"，不编 |
| 参考文献信息不全 | 保留可得字段，缺项标 `not_available` |
| 篇幅超限 | 先压缩 Intro/Discussion，保 Results 与 Limitations |
| 运行时间不足 | 先落盘已完成章节，`status=partial`，写清缺口 |

## 自检

- [ ] `case_id` 与输入一致，契约字段齐全
- [ ] 每条数值能在 `source_map` 中找到来源
- [ ] 摘要/正文/图表数值一致
- [ ] 引用与材料逐字一致，无编造 DOI
- [ ] 缺失材料与冲突已显式标注
- [ ] 产物真实存在，`.docx` 通过 `--check`

## 快速路径（预算内）

1. **0–2 分钟**：读 task.md/case.json，把章节、字数、语言、引用格式写成 `contract.json`。
2. **2–7 分钟**：先建 `evidence-ledger` 与 `outline`，优先写 Methods/Results 中材料最全的一节，落盘骨架。
3. **7–13 分钟**：逐节起草并更新 `sentence-map`，最后写 Intro/Discussion 与摘要。
4. **最后 2 分钟**：跑数值/引用/契约三项闸门，不再开新章节。

## 方法增强（融合参考实践）

- **契约先行**：task.md 的章节、字数、格式、必含声明优先于任何写作习惯；存在 `output-contract*.json` 时字段名照抄。
- **证据台账**：每条可用事实记 `fact_id / value / unit / condition / source / kind`，写作时只从台账取数。
- **大纲绑定 claim**：每个 section 先绑定 claim id，再写句子；材料不足的章节标 `not_supported`，不硬写。
- **句子级来源映射**：写完一节立刻更新 `sentence-map`（句子 → fact_id），保证每个数字可回查。
- **反向大纲复核**：写完通读每段首句，检查“段落在全文中的功能”是否与大纲一致；偏题的段落移动或删除。
- **四处数值互校**：摘要、正文、图表、结论中的同一数值逐字一致；冲突进 `conflicts[]`，不静默裁决。
- **引用支撑句**：每条引用必须支撑它所在的那句话，禁止装饰性引用；查不到 DOI 写 `not_available`。

### 定向方法增强

- **来自 Academic Paper Writing Assistant**：先做数据核实触发器——材料中出现“（估）/ ~ / 约 / 需核实 / 我记得 / * 标注”时，先核实再写作；数据来源按“用户材料 > 官方公开数据 > 用户明确授权的模拟数据”降级，模拟数据必须标注。
- **来自 Academic Paper Writing Assistant**：写作前加载历史教训/项目约定，先判断论文类型（实证/问卷/案例/综述），再决定统计预检与章节重点。
- **来自 academic-writing**：引用只取同行评议论文、会议论文、学术专著、官方报告与学位论文；编号从 [1] 起连续；文末条目格式统一；图表数据必须有真实来源。
- **来自 academic-writing**：终稿四查——引用是否合法、编号是否连续、条目格式是否统一、语言是否一致。
- **两篇合取后的改造**：在证据台账增加 `verification_status ∈ {verified, estimated, unverified}`；`estimated/unverified` 的数值不得进入摘要与结论，只能出现在方法或限制中。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 契约 | 章节、字数、语言、声明字段齐全 | 补字段；无法满足写 `unmet` |
| G2 | 数值 | 每个数值能在 `source_map` 找到来源 | 删除或补来源，不猜 |
| G3 | 一致 | 摘要/正文/图表/结论数值一致 | 列 `conflicts[]` 并注明采用值 |
| G4 | 引用 | 每条引用与材料逐字一致，无编造 DOI | 修正或标 `not_available` |
| G5 | 边界 | 相关不写因果；未验证机制标 `inferred` | 降级措辞并补限定 |
| G6 | 产物 | `manuscript.md` 非空；docx 通过 `--check` | 修文件或 `status=partial` |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 摘要出现正文没有的数 | 摘要数值在 source_map 中无对应 | 删除或补正文与来源 |
| 相关性写成因果 | “导致/证明”无实验或机制证据 | 改为“相关/提示”，标 `inferred` |
| 章节无材料硬写 | section 的 claims 为空 | 标 `not_supported`，写材料缺口 |
| 装饰性引用 | 引用与所在句论断无关 | 删除引用或改挂正确论断 |
| 数值漂移 | 正文 92.1%、摘要 92% | 统一精度并逐处核对 |
| 篇幅超限 | 字数超出契约 | 先压 Intro/Discussion，保 Results/Limitations |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`manuscript`、`artifacts`、`source_map`、`change_log`
- [ ] `manuscript.md` 真实存在，章节与契约一致
- [ ] 每个数值有 `source_map` 定位
- [ ] 摘要/正文/图表数值一致
- [ ] 引用与材料逐字一致，无编造 DOI
- [ ] 缺失材料与冲突已显式标注
- [ ] `.docx`（如生成）通过回读校验

## 最小示例（示例，非正式答案）

输入：`materials/results.csv`（3 组、每组 n=3）、`Fig.1`、`task.md` 要求写 Results 一节。
输出：`manuscript.md` 的 `3.1` 节 180 词，含 3 个数值句；`source_map` 三条分别指向
`results.csv` 行号与 `Fig.1`；`status: "completed"`。
