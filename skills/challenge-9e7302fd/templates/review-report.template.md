# Methods and statistics review (SYNTHETIC TEMPLATE ONLY)

> This template only shows the required structure. Replace every placeholder
> with a statement that is supported by a verbatim quote from the actual
> manuscript. Do not treat the placeholders as findings.

## 1. Scope and materials

- Manuscript/files reviewed: `<path>` (version/hash: `<sha256 or version>`)
- Review facets: design, variables, statistics, sample size/power, reporting
- Out of scope: `<not checked / unavailable material>`

## 2. Research design

- Design type and stated question: `<verbatim quote + locator>`
- Control/grouping/randomization/blinding: `<quote + locator>`
- Conclusion: `<checked / insufficient_material>` — `<reason>`

## 3. Variable definitions

- Exposure/outcome/covariates and coding: `<quote + locator>`
- Units, cut-points, missing-data handling: `<quote + locator>`
- Conclusion: `<checked / insufficient_material>` — `<reason>`

## 4. Statistical methods and assumptions

- Test/model vs data structure: `<quote + locator>`
- Paired vs unpaired, normality, equal variance, independence: `<quote + locator>`
- Multiplicity/repeated measures/interaction/sensitivity: `<quote + locator>`
- Decision rule used: `<rule id or text>`

## 5. Sample size and power

- Planned sample size and assumptions: `<quote + locator>`
- Effect-size source, alpha, power: `<quote + locator>`
- Achieved vs planned, attrition/exclusions: `<quote + locator>`
- Conclusion: `<checked / insufficient_material>` — `<reason>`

## 6. Results reporting

| Item | Status | Evidence |
|---|---|---|
| n (Methods/Results/Table consistent) | reported/missing/inconsistent | `<locator>` |
| Effect size | reported/missing | `<locator>` |
| Confidence interval | reported/missing | `<locator>` |
| Exact p value | reported/missing | `<locator>` |
| Test statistic/df | reported/missing | `<locator>` |
| Missing data/attrition | reported/missing | `<locator>` |
| Multiplicity correction | reported/missing/not_applicable | `<locator>` |

## 7. Findings

| ID | Facet | Locator | Original quote | Severity | Type | Fix |
|---|---|---|---|---|---|---|
| S001 | `<facet>` | `<path + paragraph/line/table>` | `<verbatim>` | `<critical/major/moderate/minor/info>` | `<error/insufficient_evidence/...>` | `<specific fix>` |

## 8. Evidence and traceability

- Every finding above must also appear in `work/review-findings.json`
  with `locator.path`, `locator.locator`, `original_quote` and `evidence`.
- Quote verification result: `work/review-gate.json`

## 9. Uncertainty and human review

- `<not reported / ambiguous / needs author clarification>`
- This review does not replace institutional review or statistical consultancy.

## 10. Reproducibility boundary

- Materials and versions used: `<list>`
- Commands/scripts: `scripts/review_quality_gate.py` (quality gate only)
- Known limitations: `<e.g. only PDF text layer, no raw data>`
