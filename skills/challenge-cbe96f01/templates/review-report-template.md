# 引用与事实核验报告

> 本模板是结构骨架，不是结论模板。所有判定、引文、数值与来源状态必须来自
> 本次输入；没有发现问题的检查面写“已查、依据、为何通过”，不得编造缺陷。

## 1. 审查元信息

- case_id：
- 稿件文件/版本（哈希或版本号，若有）：
- 审查范围（章节/页/图表）：
- 全文规模（章节/段落/句子/字数）：
- 可用材料与缺口：
- 预检：`work/citation-check.json`（候选 N 条；采纳 X；改写/降级 Y；驳回 Z）

## 2. 方法与覆盖

| facet | 状态 | 查了什么 | 证据 locator |
|---|---|---|---|
| fact_consistency | checked / partial / not_applicable / not_checked | | |
| citation_support | | | |
| method_design | | | |
| statistical_reporting | | | |
| academic_compliance | | | |
| argument_chain | | | |

| 引用子面 | 状态 | 说明 |
|---|---|---|
| citation_resolvable | | |
| quote_verbatim | | |
| numeric_consistency | | |
| source_status | | |
| design_scope_match | | |
| uncited_results | | |
| reference_metadata | | |
| section_coverage | | |

状态为 `partial`、`not_checked` 或 `not_applicable` 时必须写原因。

## 3. claim-source 矩阵

| claim_id | 本文论断 | 章节 | 被引来源 | 来源实际主张 | 判定 | 数值对照 | 说明 |
|---|---|---|---|---|---|---|---|
| C01 | | | | | full / partial / none / unverifiable | manuscript / source | |

## 4. 来源状态

| source | status | version_note | needs_recheck | 依据 |
|---|---|---|---|---|
| | published / preprint / retracted / unknown | | | |

## 5. 问题清单（按严重度）

| ID | type | subtype | 位置 | 严重度 | 问题 | 修改建议 |
|---|---|---|---|---|---|---|
| F01 | missing_reference / uncited_claim / citation_mismatch / quote_mismatch | | | critical / major / minor / info | | |

### F01 · 问题标题

- 位置：
- 原文（逐字）：
- 来源原文/通知（逐字）：
- 判定与依据：
- 类型与严重度理由：
- 修改建议（改哪句、补什么来源/数值）：
- 误报检查：
- 关联证据：

（每条 finding 重复本节；ID 必须与 `findings.json` 一致。）

## 6. 错误 / 证据不足 / 建议分栏

### 6.1 可确认的事实或引用错误

### 6.2 证据不足或无法判定

### 6.3 规范问题

### 6.4 改进建议

## 7. 待复核与未检查

| 项目 | 状态 | 原因 | 影响 | 需要谁补什么 |
|---|---|---|---|---|

## 8. 可复核性与限制

- locator 约定（段落/行/页/图表/数据集条目）：
- 预检候选处置（采纳/改写/驳回及理由）：
- 运行与工具限制（不联网、仅输入材料）：
- 未覆盖材料及影响：
- 本报告不替代机构审查/同行评审的声明：
