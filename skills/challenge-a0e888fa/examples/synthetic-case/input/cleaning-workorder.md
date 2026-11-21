# Cleaning workorder (Synthetic only)

1. Preserve an unmodified snapshot of measurements.csv before any edit.
2. Treat -999 and blank concentration cells as missing. Do not impute.
3. Treat status=below_lod as a censored value, not as numeric zero.
4. Normalize every concentration to mg/L. The conversion must be reversible and logged.
5. Deduplicate on sample_id + assay_date + replicate after case/space normalization; keep the first row and log the dropped row.
6. Flag concentrations outside 0.0-500.0 mg/L as outliers; retain and flag them, do not cap silently.
7. Report raw rows, cleaned rows, missing, below_lod, outlier, converted and dropped counts; raw minus dropped must equal cleaned.
