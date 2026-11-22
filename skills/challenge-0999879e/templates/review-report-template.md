# 论文质量审查报告

> 本模板是结构骨架，不是结论模板。所有问题、引文与判断必须来自本次输入；
> 没有发现的检查面写“已查、通过、依据”，不得编造问题。

## 1. 审查元信息

- case_id：
- 稿件文件/版本（哈希或版本号，若有）：
- 审查范围（章节/页/图表）：
- 可用材料：
- 材料缺口：
- 规则扫描：`work/rule-review.json`（候选 N 条；采纳 X；改写/降级 Y；驳回 Z）

## 2. 方法与六面覆盖

| facet | 状态 | 查了什么 | 证据 locator |
|---|---|---|---|
| fact_consistency | checked / partial / not_applicable / not_checked | | |
| citation_support | | | |
| method_design | | | |
| statistical_reporting | | | |
| argument_chain | | | |
| academic_compliance | | | |

状态为 `partial`、`not_checked` 或 `not_applicable` 时必须写原因。

## 3. 结论摘要

- 总体判断：accept / minor_revision / major_revision / reject / unverifiable
- 主要理由（最多 5 条）：
- 最需要作者处理的问题（ID）：

## 4. 问题清单（按严重度）

| ID | facet | 位置 | 严重度 | 类型 | 问题 | 修改建议 |
|---|---|---|---|---|---|---|
| Q01 | | | critical / major / minor / info | error / insufficient_evidence / norm_violation / suggestion | | |

### Q01 · 问题标题

- 位置：
- 原文（逐字）：
- 影响：
- 类型与严重度理由：
- 修改建议（改哪句、改成什么、补什么证据）：
- 误报检查：
- 关联证据：

（每条 finding 重复本节；ID 必须与 findings.json 一致。）

## 5. 引用-主张对照（claim-source matrix）

| claim_id | 本文论断 | 论断位置 | 被引文献 | 文献实际主张 | 文献位置 | 判定 | 说明 |
|---|---|---|---|---|---|---|---|
| CS01 | | | | | | full / partial / none / unverifiable | |

## 6. 错误 / 证据不足 / 建议分栏

### 6.1 事实或逻辑错误（error）

### 6.2 证据不足或未报告（insufficient_evidence）

### 6.3 规范问题（norm_violation）

### 6.4 改进建议（suggestion）

## 7. 待复核与未检查

| 项目 | 状态 | 原因 | 影响 | 需要谁补什么 |
|---|---|---|---|---|

## 8. 可复核性与限制

- locator 约定（段落/行/页/章节）：
- 规则扫描与候选处置（采纳/改写/驳回及理由）：
- 运行与工具限制：
- 未覆盖材料及影响：
- 本报告不替代机构审查/同行评审的声明：
