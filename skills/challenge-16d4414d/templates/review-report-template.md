# 论文逻辑与论证审查报告

> 本模板是结构骨架，不是结论模板。所有主张、引文、判断与修订必须来自本次
> 输入；没有发现问题的检查面写“已查、依据、为何通过”，不得编造缺陷。

## 1. 审查元信息

- case_id：
- 稿件文件/版本（哈希或版本号，若有）：
- 审查范围（章节/页/图表）：
- 全文规模（章节/段落/句子/字数）：
- 可用材料与缺口：
- 逻辑预检：`work/logic-check.json`（候选 N 条；采纳 X；改写/降级 Y；驳回 Z）

## 2. 方法与全文覆盖

| 检查面 | 状态 | 查了什么 | 证据 locator |
|---|---|---|---|
| fact_consistency | checked / partial / not_applicable / not_checked | | |
| citation_support | | | |
| method_design | | | |
| statistical_reporting | | | |
| argument_chain | | | |
| academic_compliance | | | |

| 逻辑专项面 | 状态 | 查了什么 | 证据 locator |
|---|---|---|---|
| claim_extraction | | | |
| evidence_support | | | |
| reasoning_chain | | | |
| causal_inference | | | |
| cross_section_consistency | | | |
| severity_and_fix | | | |

状态为 `partial`、`not_checked` 或 `not_applicable` 时必须写原因。

## 3. 主张清单

| ID | 章节 | speaker | 类型 | 强度 | 支持状态 | 主张原文 |
|---|---|---|---|---|---|---|
| C01 | | author / cited / assumption | | | full / partial / none / unverifiable | |

## 4. 跨章节承诺-交付

| 承诺 | 承诺位置 | 交付/实际 | 交付位置 | consistency | 说明 |
|---|---|---|---|---|---|
| | | | | delivered / partial / not_delivered / contradicted / unverifiable | |

## 5. 结论摘要

- 总体判断：accept / minor_revision / major_revision / reject / unverifiable
- 主要理由（最多 5 条）：
- 最需要作者处理的 finding ID：

## 6. 问题清单（按严重度）

| ID | facet/subfacet | 位置 | 严重度 | 类型 | 问题 | 修改建议 |
|---|---|---|---|---|---|---|
| L01 | | | critical / major / minor / info | error / insufficient_evidence / norm_violation / suggestion | | |

### L01 · 问题标题

- 位置：
- 原文（逐字）：
- 影响：
- 类型与严重度理由：
- 修改建议（改哪句、改成什么、补什么证据）：
- 误报检查：
- 关联主张/证据：

（每条 finding 重复本节；ID 必须与 `findings.json` 一致。）

## 7. 错误 / 证据不足 / 建议分栏

### 7.1 事实或逻辑错误（error）

### 7.2 证据不足或未报告（insufficient_evidence）

### 7.3 规范问题（norm_violation）

### 7.4 改进建议（suggestion）

## 8. 待复核与未检查

| 项目 | 状态 | 原因 | 影响 | 需要谁补什么 |
|---|---|---|---|---|

## 9. 可复核性与限制

- locator 约定（段落/行/页/章节）：
- 预检候选处置（采纳/改写/驳回及理由）：
- 运行与工具限制：
- 未覆盖材料及影响：
- 本报告不替代机构审查/同行评审的声明：
