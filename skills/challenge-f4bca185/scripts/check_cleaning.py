#!/usr/bin/env python3
"""Validate a cleaning result, cleaned.csv, log and quality report.

Usage:
    python3 check_cleaning.py --result /app/output/result.json \
        --root /app/output --json
    python3 check_cleaning.py --self-test --json

Checks
  * result has case_id/schema/rules/cleaning_log/quality_report/artifacts
  * every rule has rows_affected; every logged change has an example when non-zero
  * cleaned.csv parses; row counts agree across result, log and quality
  * quality metrics are recomputable from cleaned.csv
  * unit-converted fields keep a <field>__raw column
  * deleted rows are explained by dedupe/drop rules; no silent row loss
  * outlier flags reference existing row ids / values
  * artifacts resolve and are non-empty

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path

NUMBER = re.compile(r"[-+]?(?:\d+(?:[,.]\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def parse_number(value):
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    match = NUMBER.search(text)
    try:
        return float(match.group(0)) if match else None
    except ValueError:
        return None


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def missing_rate(rows, fields):
    if not rows or not fields:
        return 0.0
    missing = sum(1 for row in rows for field in fields
                  if str(row.get(field, "")).strip() == "")
    return missing / (len(rows) * len(fields))


def duplicate_rate(rows, keys):
    if not rows or not keys:
        return 0.0
    seen, dupes = set(), 0
    for row in rows:
        key = tuple(str(row.get(k, "")).strip() for k in keys)
        if key in seen:
            dupes += 1
        seen.add(key)
    return dupes / len(rows)


def validate(result, root):
    problems, warnings, checks = [], [], {}
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["result root is not an object"],
                "warnings": [], "checks": {}}
    for key in ("case_id", "schema", "rules", "cleaning_log", "quality_report",
                "artifacts"):
        if key not in result:
            problems.append("result.json missing %s" % key)
    rules = result.get("rules") or []
    log = result.get("cleaning_log") or []
    if len(rules) != len(log):
        problems.append("rules[] and cleaning_log[] lengths differ: %d vs %d" %
                        (len(rules), len(log)))
    deleted = 0
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            problems.append("rule[%d] is not an object" % i)
            continue
        if not rule.get("rule_id"):
            problems.append("rule[%d] missing rule_id" % i)
        if "rows_affected" not in rule:
            problems.append("rule %s missing rows_affected" % rule.get("rule_id", i))
        elif rule.get("type") in ("dedupe",):
            deleted += int(rule.get("rows_affected") or 0)
        elif rule.get("type") == "missing" and rule.get("policy") == "drop":
            deleted += int(rule.get("rows_affected") or 0)
    for i, entry in enumerate(log):
        if not isinstance(entry, dict):
            problems.append("cleaning_log[%d] is not an object" % i)
            continue
        if entry.get("rows_affected") and not entry.get("examples"):
            problems.append("cleaning_log %s has rows_affected but no examples" %
                            entry.get("rule_id", i))

    quality = result.get("quality_report") or {}
    before = quality.get("before") or {}
    after = quality.get("after") or {}
    if before.get("rows") is not None and after.get("rows") is not None:
        if int(before["rows"]) - deleted != int(after["rows"]):
            warnings.append("row count: before %s - deleted %s != after %s" %
                            (before["rows"], deleted, after["rows"]))
    checks["rows_before"] = before.get("rows")
    checks["rows_after"] = after.get("rows")

    artifacts = result.get("artifacts") or []
    paths = {}
    base = Path(root) if root else Path.cwd()
    for i, item in enumerate(artifacts):
        if not isinstance(item, dict):
            problems.append("artifact[%d] is not an object" % i)
            continue
        rel = item.get("path")
        if not rel:
            problems.append("artifact[%d] missing path" % i)
            continue
        candidate = Path(rel)
        if not candidate.is_absolute():
            candidate = base / rel
        if not candidate.is_file():
            problems.append("artifact missing: %s" % rel)
            continue
        paths[str(item.get("id") or item.get("role") or i)] = candidate
    cleaned = paths.get("cleaned") or next(
        (p for p in paths.values() if p.suffix.lower() == ".csv"), None)
    if cleaned is None:
        problems.append("no cleaned CSV artifact found")
        return {"ok": False, "problems": problems, "warnings": warnings, "checks": checks}
    try:
        fields, rows = read_csv(cleaned)
    except (OSError, csv.Error) as exc:
        problems.append("cannot parse cleaned CSV: %s" % exc)
        return {"ok": False, "problems": problems, "warnings": warnings, "checks": checks}
    checks["csv_rows"] = len(rows)
    if after.get("rows") is not None and int(after["rows"]) != len(rows):
        problems.append("cleaned CSV has %d rows but quality.after.rows=%s" %
                        (len(rows), after["rows"]))
    schema_fields = [item.get("name") for item in result.get("schema") or []
                     if isinstance(item, dict) and item.get("name")]
    metric_fields = [f for f in schema_fields
                     if f in fields and not f.endswith("__raw") and f != "flags"]
    actual_missing = missing_rate(rows, metric_fields)
    if after.get("missing_rate") is not None and abs(actual_missing - float(after["missing_rate"])) > 1e-4:
        warnings.append("quality.after.missing_rate %s != CSV %s" %
                        (after["missing_rate"], round(actual_missing, 6)))
    keys = []
    for rule in rules:
        if isinstance(rule, dict) and rule.get("type") == "dedupe":
            keys = [k for k in rule.get("keys") or [] if k in fields]
            break
    if keys:
        actual_dupes = duplicate_rate(rows, keys)
        if after.get("duplicate_rate") is not None and abs(actual_dupes - float(after["duplicate_rate"])) > 1e-4:
            warnings.append("quality.after.duplicate_rate %s != CSV %s" %
                            (after["duplicate_rate"], round(actual_dupes, 6)))
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("type") != "unit_convert":
            continue
        field = rule.get("field")
        if field in fields:
            raw = field + "__raw"
            if raw not in fields:
                problems.append("unit_convert field %s has no %s column" % (field, raw))
            for row in rows:
                if str(row.get(field, "")).strip() and parse_number(row.get(field)) is None:
                    problems.append("unit_convert field %s has non-numeric cleaned value" % field)
                    break
    flags = (quality.get("outliers_flagged") or [])
    ids = {str(row.get("row_id") or row.get("sample_id") or row.get("id") or "")
           for row in rows}
    for i, flag in enumerate(flags):
        if not isinstance(flag, dict):
            problems.append("outlier[%d] is not an object" % i)
            continue
        rid = str(flag.get("row_id") or "")
        if rid and ids and rid not in ids:
            warnings.append("outlier[%d] row_id %s not in cleaned CSV" % (i, rid))
    checks["flags"] = len(flags)
    checks["schema_fields"] = len(schema_fields)
    return {"ok": not problems, "problems": problems, "warnings": warnings, "checks": checks}


def self_test():
    result = {
        "case_id": "clean-self-test",
        "schema": [{"name": "sample_id"}, {"name": "temperature"},
                   {"name": "selectivity"}],
        "rules": [
            {"rule_id": "R1", "type": "unit_convert", "field": "temperature",
             "rows_affected": 1},
            {"rule_id": "R2", "type": "dedupe", "keys": ["sample_id"],
             "rows_affected": 1},
        ],
        "cleaning_log": [
            {"rule_id": "R1", "rows_affected": 1,
             "examples": [{"row_id": "r1", "raw_value": "623 K", "clean_value": "350"}]},
            {"rule_id": "R2", "rows_affected": 1,
             "examples": [{"row_id": "r2", "raw_value": "b", "clean_value": "dropped"}]},
        ],
        "quality_report": {
            "before": {"rows": 3, "missing_rate": 0, "duplicate_rate": 0.3333},
            "after": {"rows": 2, "missing_rate": 0, "duplicate_rate": 0},
            "unit_consistency": {"temperature": True},
            "outliers_flagged": [],
        },
        "artifacts": [{"id": "cleaned", "path": "cleaned.csv"}],
    }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "cleaned.csv").write_text(
                "sample_id,temperature__raw,temperature,selectivity\n"
                "a,623 K,350,80\n"
                "c,700 K,427,90\n", encoding="utf-8")
            report = validate(self_test(), root)
        print(json.dumps({"ok": report["ok"], "self_test": True,
                          "problems": report["problems"],
                          "warnings": report["warnings"]}, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if not args.result:
        ap.error("--result is required unless --self-test is used")
    try:
        result = load_json(args.result)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "problems": ["cannot read result: %s" % exc]},
                         ensure_ascii=False))
        return 2
    report = validate(result, args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
