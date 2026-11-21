# Methods and statistics review — synthetic example

> Synthetic example only. It is not a real review and must not be submitted as
> a formal case answer.

## 1. 范围与材料 (Scope and materials)

- Reviewed file: `synthetic-source.md` (synthetic only)
- Out of scope: raw data, analysis code and journal-specific reporting rules

## 2. 研究设计 (Research design)

- Allocation without randomization or blinding is reported in P004.
- Finding S001 (major, design): report the allocation method and bias risk.

## 3. 变量定义 (Variables)

- The outcome is binary but analyzed as continuous (S002, major, variable).
- Use a model that matches the outcome type or justify the approximation.

## 4. 统计方法 (Statistics)

- Independent-samples t tests are used for repeated measurements (S003, major, statistical).
- The independence assumption is violated; use a paired or mixed-effects analysis.
- Decision rule: paired vs unpaired.

## 5. 样本量与检验效能 (Sample size and power)

- No sample size or power calculation is reported (S004, moderate, insufficient_evidence).
- No power value is invented; the gap is recorded as insufficient evidence.

## 6. 结果报告 (Reporting)

- The 95% confidence interval and exact p value are not reported (S005, moderate, reporting_gap).
- n is inconsistent between Methods and the table; reconcile the denominator.

| Item | Status | Locator |
|---|---|---|
| n | inconsistent | P004 / Table 1 |
| effect | missing | P004 |
| ci | missing | P004 |
| exact_p | missing | P004 |
| df | missing | P004 |
| missing_data | missing | P004 |
| multiplicity | not_applicable | P004 |

## 7. 逐条发现 (Findings)

| ID | Facet | Locator | Severity | Type | Fix |
|---|---|---|---|---|---|
| S001 | design | synthetic-source.md P004 | major | design | Report allocation and blinding |
| S002 | variables | synthetic-source.md P004 | major | variable | Match model to binary outcome |
| S003 | statistics | synthetic-source.md P004 | major | statistical | Use paired/mixed model |
| S004 | sample_size | synthetic-source.md P004 | moderate | insufficient_evidence | Report power basis |
| S005 | reporting | synthetic-source.md P004 | moderate | reporting_gap | Add effect, CI, exact p, df |

## 8. 证据与定位 (Evidence and traceability)

- Every finding has `path + locator + original_quote` in
  `work/review-findings.json`; quotes are verbatim from `synthetic-source.md`.
- Quality gate output: `output/review-gate.json`.

## 9. 待复核与不确定 (Uncertainty and human review)

- Raw data and analysis code are unavailable; statistical claims cannot be
  recomputed.
- This review does not replace institutional review or statistical consultancy.

## 10. 复现边界 (Reproducibility)

- Materials: `synthetic-source.md` only.
- Tool: `scripts/review_quality_gate.py` for the deterministic gate.
- Limitation: no external literature or journal guideline was supplied.
