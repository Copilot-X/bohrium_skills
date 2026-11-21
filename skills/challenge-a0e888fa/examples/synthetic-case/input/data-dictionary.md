# Data dictionary (Synthetic only)

- sample_id: unique sample identifier. Canonical form is upper-case with no surrounding spaces.
- assay_date: ISO 8601 date (YYYY-MM-DD).
- concentration: numeric measurement. Valid physical range is 0.0-500.0 mg/L. The value -999 is a missing code; a blank cell means not recorded.
- unit: one of mg/L or ug/mL. 1 ug/mL equals 1 mg/L exactly.
- replicate: integer 1-3.
- status: measured, below_lod, or missing.
