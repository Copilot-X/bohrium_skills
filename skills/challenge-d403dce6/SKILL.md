---
name: grant-proposal-writer
description: 基金申请书写作：按背景、问题、目标、内容、方法、创新、可行性、成果七要素起草，与工作单逐项对齐，不虚构申请人资历、前期成果或机构政策。Draft a funding proposal whose seven elements stay consistent with the supplied workorder, without inventing credentials or agency policy. Use for 基金申请、项目申报书、标书、可行性论证。
---

# 基金申请书写作（Grant Proposal Writer）

## 用途与触发

task.md 要求写"基金申请书""项目申请书""标书"时使用。

## 组织方口径（硬约束）

- 本题给的是**三案例公开补充写作要求**与**七要素 / 计划一致性约束**；
- 它**不是现实资助机构的政策**，不要引用"国家自然科学基金委 2026 年指南"之类的虚构条款；
- 原论文与既有成果**归外部作者**，本申请人的真实证据**明确缺失**；
- 具体要求以运行时的 `case.json` 与 `organizer-workorder.json` 为准。

因此：**不得虚构申请人简历、前期成果、依托单位、经费预算依据**。
缺失项一律写 `not_provided_in_materials`，并在 `gaps` 里给出"需要申请人补充"的清单。

## 支持文件（必须使用）

| 文件 | 何时用 | 用法 |
|---|---|---|
| `references/grant-proposal-playbook.md` | 拆工作单、建七要素/一致性矩阵前 | 先读；按七要素与评审视角执行 |
| `templates/grant-plan.json` | 计划/一致性矩阵骨架 | 复制后填 requirements/elements/objectives/consistency/evidence/budget |
| `templates/grant-brief.md` | 写正文 | 按 E1–E7 + 预算/风险骨架写 |
| `scripts/check_grant_proposal.py` | 交付前 | `python3 {SKILL_DIR}/scripts/check_grant_proposal.py --result /app/output/result.json --brief /app/output/proposal.md --root /app/output --json` |

`{SKILL_DIR}` 通常为 `/skills/weekly-skill`。校验器检查七要素、目标四链、一致性断链、
缺失证据与 gaps、预算/周期；不要为了通过而虚构申请人信息或预算金额。

## 输入

- `/input/task.md`：资助类型、篇幅、章节模板、语言、评审关注点
- `/input/organizer-workorder.json`：七要素的具体要求与一致性约束
- `/input/case.json`：`case_id`、`applicant_evidence`（可能为空）、`output_contract`
- 材料：前期论文/数据、预实验、团队信息（有多少用多少）

## 七要素（固定骨架）

| 编号 | 要素 | 必须回答 | 常见失分 |
|---|---|---|---|
| E1 | 研究背景与科学问题 | 领域现状 → 具体空白 → 可检验的问题 | 背景太长、问题不具体 |
| E2 | 研究目标 | 1 个总目标 + 2–4 个可考核的分目标 | 目标与内容混写 |
| E3 | 研究内容 | 分课题/任务，彼此逻辑递进 | 内容与目标不对应 |
| E4 | 研究方法与技术路线 | 每个任务的方法、数据、可行性 | 只写方法名不写怎么用 |
| E5 | 创新点 | 相对已有工作的新增之处（有证据） | 把"首次"当创新 |
| E6 | 可行性分析 | 理论、方法、数据、团队、条件 | 空喊"团队实力强" |
| E7 | 预期成果与进度 | 产出形式、考核指标、时间节点 | 指标不可量化 |

**一致性约束**（评分重点）：E2 的每个分目标必须能在 E3 找到对应任务、
在 E4 找到方法、在 E7 找到产出；任一链条断开即 `consistency_breaks`。

## 执行步骤

1. **解析工单**：把 `organizer-workorder.json` 的每条要求转成 `requirements`
   （id、要素、要求、判定方式、来源）。
2. **建证据清单**：`/app/work/applicant-evidence.json`
   —— 只记录材料中**确实存在**的前期工作；没有的写 `missing`。
3. **搭一致性矩阵** `/app/work/consistency-matrix.json`：
   ```json
   {"rows": [
     {"objective": "O1", "content": ["T1"], "method": ["M1"],
      "outcome": ["D1"], "indicator": "…", "linked": true}]}
   ```
4. **按七要素起草**：先写 E2/E3/E4（逻辑主干），再写 E1/E5/E6/E7。
   每节开头一句话点题，避免"随着…的发展"式套话。
5. **量化**：目标、指标、进度都要可考核（数值、时间、交付物）。
   材料没给的指标写 `to_be_confirmed`，不虚构。
6. **一致性回扫**：逐行检查矩阵，断链写入 `consistency_breaks` 并修正文本。
7. **落盘**：`proposal.md`、`compliance-matrix.json`、`result.json`。
8. **交付校验**：运行 `check_grant_proposal.py`，检查七要素、目标四链、一致性断链、
   缺失证据与预算/周期；有 problem 先修，修不了标 `status=partial`。

## 官方输出契约（必须补齐）

官方 `example-input` 的 `output_contract` 要求顶层字段：`deliverable`、`preserved_facts`、`evidence_map`、`change_log`。
题目原有字段全部保留；交付前运行：

```bash
python3 {SKILL_DIR}/scripts/emit_official.py \
  --result /app/output/result.json --out /app/output/result.json \
  --profile writing --json
```

该适配器只补齐官方别名，不改变任务字段；`validate_result.py` 之后仍需运行。

## 输出契约

```json
{
  "case_id": "…",
  "task_family": "grant-proposal",
  "status": "completed|partial",
  "proposal": {
    "path": "proposal.md",
    "language": "zh|en",
    "word_count": 0,
    "scheme": "not_a_real_agency"
  },
  "artifacts": [
    {
      "role": "editable",
      "path": "proposal.md",
      "format": "markdown"
    },
    {
      "role": "compliance",
      "path": "compliance-matrix.json",
      "format": "json"
    }
  ],
  "elements": [
    {
      "id": "E1",
      "title": "研究背景与科学问题",
      "status": "written|partial|missing",
      "claims": [
        "C01",
        "C02"
      ]
    },
    {
      "id": "E7",
      "title": "预期成果与进度",
      "status": "written",
      "indicators": [
        {
          "name": "论文",
          "value": "2 篇",
          "source": "workorder#E7"
        }
      ]
    }
  ],
  "objectives": [
    {
      "objective_id": "O1",
      "text": "…",
      "tasks": [
        "T1"
      ],
      "methods": [
        "M1"
      ],
      "outcomes": [
        "D1"
      ],
      "indicator": "…"
    }
  ],
  "consistency": {
    "matrix_complete": true,
    "breaks": []
  },
  "applicant_evidence": {
    "provided": [
      "materials/prior_work.pdf"
    ],
    "missing": [
      "申请人简历",
      "依托单位信息",
      "经费预算依据"
    ]
  },
  "requirements": [
    {
      "id": "R1",
      "type": "Section",
      "text": "…",
      "source_location": "workorder#E2",
      "status": "covered|partial|missing"
    }
  ],
  "budget": {
    "total": null,
    "currency": "CNY",
    "items": [],
    "status": "to_be_confirmed"
  },
  "timeline": [
    {
      "phase": "Q1",
      "period": "M1-M3",
      "milestone": "…",
      "deliverable": "…"
    }
  ],
  "eligibility_screen": {
    "passed": null,
    "risks": []
  },
  "support_files_used": [
    "references/grant-proposal-playbook.md",
    "scripts/check_grant_proposal.py"
  ],
  "gaps": [
    {
      "item": "经费预算",
      "why_needed": "E7 要求",
      "owner": "申请人"
    }
  ],
  "uncertainty": [],
  "change_log": [],
  "deliverable": {
    "path": "…",
    "format": "markdown"
  },
  "preserved_facts": [],
  "evidence_map": []
}
```

## 写作规范

1. 背景 ≤ 全文 20%，最后一段必须落到"因此，本项目拟解决…"。
2. 目标用可考核动词（测定、构建、验证、比较），不用"深入研究""探索"。
3. 技术路线写"输入 → 步骤 → 判据 → 输出"，可配文字版流程图（`text` 图亦可）。
4. 创新点必须对比具体已有工作（引用真实文献），不写"填补空白"。
5. 可行性给证据：预实验数据、方法成熟度、数据可得性、团队分工。
6. 进度用"阶段—时间—里程碑—交付物"四列，粒度到季度。
7. 伦理与数据安全按材料写；材料没有就标 `not_provided`。

## 异常处理

| 情况 | 处理 |
|---|---|
| 工单与 task.md 冲突 | 以 task.md 为准，冲突写 `uncertainty` |
| 无申请人信息 | 用占位字段名而非虚构姓名，`gaps` 列出 |
| 预算无依据 | 给"待定"与计算口径，不编数字 |
| 前期成果不足 | E6 用方法与条件论证，明确"尚缺前期数据" |
| 篇幅超限 | 压缩 E1，保 E2–E4 与 E7 |

## 自检

- [ ] 七要素齐全，或缺失有说明
- [ ] 每个分目标在矩阵中四链齐全，`consistency.breaks` 为空或已解释
- [ ] 指标可量化、进度有里程碑
- [ ] 未虚构申请人、单位、预算、政策
- [ ] 创新点有对比对象
- [ ] `applicant_evidence.missing` 与 `gaps` 覆盖缺失项
- [ ] `check_grant_proposal.py` 返回 `ok: true`

## 快速路径（预算内）

1. **0–2 分钟**：读工作单与指南，建七要素合规矩阵。
2. **2–8 分钟**：先写目标/内容/方法（评审核心），落盘骨架。
3. **8–13 分钟**：补背景、创新、可行性、成果与预算。
4. **最后 2 分钟**：跑一致性矩阵与虚构检查。

## 方法增强（融合参考实践）

- **七要素矩阵**：E1 背景与意义、E2 科学问题、E3 目标、E4 研究内容、E5 方法与技术路线、E6 创新点、E7 可行性/基础/成果；逐项对应工作单要求。
- **问题驱动**：背景收敛到“一个可研究的缺口”，不写成综述；目标用可检验动词（阐明/建立/验证）。
- **目标-内容-方法对齐**：每个目标有对应内容，每个内容有对应方法与产出，禁止三张皮。
- **创新点克制**：写“相对已有工作的具体差异”，不写“国际首创”除非材料支持。
- **可行性有据**：前期数据、平台条件、团队分工只写材料提供的；缺失写 `not_provided`。
- **评审视角自检**：按“重要性/创新性/可行性/产出明确性”四项打分，弱项回改。
- **预算与周期一致**：经费科目、年度计划与研究内容匹配，不出现无内容支撑的预算。

### 定向方法增强

- **来自 Grant Proposal Drafter**：先把 RFP/指南拆成“要求登记册”，每行标类型 `Eligibility / Section / Limit / Attachment / Evaluation / Submission`，并记录原文位置与限额；不转述走样。
- **来自 Grant Proposal Drafter**：动笔前做资格筛查，任何不满足或未核实的资格项标 `Eligibility risk` 并上浮到报告顶部；不申请不符合资格的项目。
- **来自 Grant Proposal Drafter**：上下文按 12 项收集——问题与需求、目标人群与地理、目标、2–5 条 SMART 目标、活动与方法、时间线、评估计划、关键人员、可持续性、组织能力、合作方、预算摘要；每个回答映射回要求行。
- **来自 Grant Proposal Drafter**：合规矩阵状态只用 `Covered / Partial / Missing / Eligibility risk`；预算总额必须等于确认的申请金额；组织名称、EIN、既往成绩、人员资历不得编造。
- **来自 project-grant-proposal**：把任务写成“对象—动作—标准—交付物”；每项判断登记适用规则、证据、责任人、版本与整改状态；阶段 A 界定、阶段 B 处理、阶段 C 交付，原始材料与中间结果保留可追溯。
- **两篇合取后的改造**：`compliance_matrix` 增加 `requirement_type` 与 `source_location`；新增 `eligibility_screen` 与 `judgment_ledger[]` 两块。

## 质量闸门

| # | 检查 | 通过标准 | 不通过时 |
|---|---|---|---|
| G1 | 要素 | 七要素齐全且对应工作单 | 补缺项或标 `unmet` |
| G2 | 对齐 | 目标↔内容↔方法↔产出逐条对应 | 建矩阵修正断链 |
| G3 | 诚实 | 无虚构资历、成果、政策、预算 | 删除或标 not_provided |
| G4 | 问题 | 科学问题具体、可研究 | 收窄问题并给缺口依据 |
| G5 | 创新 | 差异具体、有对照 | 改为具体差异表述 |
| G6 | 一致 | 周期、预算、成果与内容匹配 | 调整至自洽 |

## 常见失败模式

| 失败模式 | 识别信号 | 修复 |
|---|---|---|
| 背景写成综述 | 大段领域介绍无缺口 | 收敛到 1 个可研究问题 |
| 目标空泛 | “提高性能”“深入研究” | 改可检验动词+指标 |
| 三张皮 | 目标/内容/方法各说各话 | 建对齐矩阵重写 |
| 虚构前期成果 | 出现材料没有的论文/专利 | 删除或标 not_provided |
| 创新点喊口号 | “国际领先”无对照 | 写具体差异与证据 |
| 预算无支撑 | 科目与研究内容无关 | 按内容重编预算 |

## 交付清单

- [ ] `/app/output/result.json` 含 `case_id`、`elements`、`objectives`、`consistency`、
      `applicant_evidence`、`requirements`、`budget`、`timeline`、`support_files_used`
- [ ] `proposal.md` 与 `compliance-matrix.json` 真实存在
- [ ] 七要素齐全
- [ ] 目标↔内容↔方法↔产出逐条对应
- [ ] 无虚构资历/成果/政策/预算
- [ ] 创新点为具体差异
- [ ] `check_grant_proposal.py` 通过；周期、预算、成果自洽

## 最小示例（示例）

输入：`organizer-workorder.json`（要求 3 个分目标、4 个任务、季度进度）。
输出：`proposal.md` 七节；`consistency-matrix` 3 行全部 `linked: true`；
`applicant_evidence.missing` 列出 3 项；`status: "partial"`（因申请人信息缺失）。
