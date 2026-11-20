#!/usr/bin/env python3
"""Deterministic, traceable CSV cleaning for literature-derived tables.

Usage:
    python3 clean_table.py --input /input/data.csv --rules /app/work/cleaning-rules.json \
        --out-dir /app/output/data --result /app/output/result.json --json
    python3 clean_table.py --self-test --json

Guarantees
  * raw values are preserved in <field>__raw columns whenever a rule changes them
  * every rule logs rows_affected and up to 3 before/after examples
  * deletions are listed with row ids; outliers are flagged, not silently dropped
  * quality metrics are recomputed from the actual before/after rows

Stdlib only. Exit 0 = cleaned, 1 = cleaned with warnings, 2 = unreadable input.
"""
import argparse
import csv
import io
import json
import math
import re
import sys
import tempfile
from pathlib import Path

NUMBER = re.compile(r"[-+]?(?:\d+(?:[,.]\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def read_csv(path):
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("cannot decode CSV with utf-8/gbk")
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = list(reader.fieldnames or [])
    rows = [dict(row) for row in reader]
    return fieldnames, rows, encoding


def write_csv(path, fieldnames, rows):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def parse_number(value):
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    match = NUMBER.search(text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def format_number(value):
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return "%.6g" % value


def row_identifier(row, fieldnames, index):
    for key in ("row_id", "sample_id", "id"):
        if key in fieldnames and str(row.get(key, "")).strip():
            return str(row[key]).strip()
    return "r%d" % (index + 1)


def ensure_raw_column(fieldnames, rows, field, raw_seen):
    raw_name = field + "__raw"
    if field not in raw_seen:
        raw_seen.add(field)
        if raw_name not in fieldnames:
            fieldnames.append(raw_name)
    for row in rows:
        row.setdefault(raw_name, row.get(field, ""))
    return raw_name


def profile(fieldnames, rows):
    schema = []
    for field in fieldnames:
        if field.endswith("__raw"):
            continue
        values = [str(row.get(field, "")).strip() for row in rows]
        nonempty = [v for v in values if v != ""]
        numbers = [parse_number(v) for v in nonempty]
        numeric = [n for n in numbers if n is not None]
        dtype = "numeric" if numeric and len(numeric) == len(nonempty) else (
            "mixed" if numeric else "text")
        schema.append({
            "name": field, "dtype": dtype,
            "missing": len(values) - len(nonempty),
            "missing_rate": round((len(values) - len(nonempty)) / len(values), 6) if values else 0,
            "unique": len(set(nonempty)),
            "min": min(numeric) if numeric else None,
            "max": max(numeric) if numeric else None,
            "samples": nonempty[:3],
        })
    return schema


def missing_rate(rows, fieldnames):
    if not rows or not fieldnames:
        return 0.0
    missing = sum(1 for row in rows for field in fieldnames
                  if str(row.get(field, "")).strip() == "")
    return round(missing / (len(rows) * len(fieldnames)), 6)


def duplicate_rate(rows, keys):
    if not rows:
        return 0.0
    seen = set()
    dupes = 0
    for row in rows:
        key = tuple(str(row.get(k, "")).strip() for k in keys)
        if key in seen:
            dupes += 1
        seen.add(key)
    return round(dupes / len(rows), 6)


def apply_rules(fieldnames, rows, rules, primary_key, warnings):
    log = []
    flags = []
    raw_seen = set()
    total_before = len(rows)
    for rule in rules:
        if not isinstance(rule, dict):
            warnings.append("rule is not an object: %r" % (rule,))
            continue
        rid = str(rule.get("rule_id") or "R%d" % (len(log) + 1))
        kind = str(rule.get("type") or "")
        entry = {"rule_id": rid, "type": kind, "fields": [],
                 "rows_affected": 0, "examples": []}
        if kind == "trim":
            fields = [f for f in rule.get("fields") or [] if f in fieldnames]
            entry["fields"] = fields
            for index, row in enumerate(rows):
                for field in fields:
                    before = str(row.get(field, ""))
                    after = before.strip()
                    if after != before:
                        ensure_raw_column(fieldnames, rows, field, raw_seen)
                        row[field] = after
                        entry["rows_affected"] += 1
                        if len(entry["examples"]) < 3:
                            entry["examples"].append({"row_id": row_identifier(row, fieldnames, index),
                                                       "raw_value": before, "clean_value": after})
        elif kind == "normalize_name":
            fields = [f for f in rule.get("fields") or [] if f in fieldnames]
            entry["fields"] = fields
            for index, row in enumerate(rows):
                for field in fields:
                    before = str(row.get(field, ""))
                    after = re.sub(r"[\s\-]+", "_", before.strip().lower())
                    if after != before:
                        ensure_raw_column(fieldnames, rows, field, raw_seen)
                        row[field] = after
                        entry["rows_affected"] += 1
                        if len(entry["examples"]) < 3:
                            entry["examples"].append({"row_id": row_identifier(row, fieldnames, index),
                                                       "raw_value": before, "clean_value": after})
        elif kind == "rename":
            mapping = rule.get("mapping") or {}
            for old, new in list(mapping.items()):
                if old not in fieldnames:
                    continue
                fieldnames[fieldnames.index(old)] = new
                for row in rows:
                    row[new] = row.pop(old, "")
                entry["fields"].append("%s->%s" % (old, new))
        elif kind == "unit_convert":
            field = rule.get("field")
            if field not in fieldnames:
                warnings.append("unit_convert field missing: %s" % field)
                continue
            factor = float(rule.get("factor", 1) or 1)
            offset = float(rule.get("offset", 0) or 0)
            entry["fields"] = [field]
            entry["from_unit"] = rule.get("from_unit")
            entry["to_unit"] = rule.get("to_unit")
            entry["factor"] = factor
            entry["offset"] = offset
            ensure_raw_column(fieldnames, rows, field, raw_seen)
            for index, row in enumerate(rows):
                raw = str(row.get(field, ""))
                value = parse_number(raw)
                if value is None:
                    if raw.strip():
                        warnings.append("unit_convert could not parse row %s: %r" %
                                        (row_identifier(row, fieldnames, index), raw))
                    continue
                clean = format_number(value * factor + offset)
                row[field] = clean
                entry["rows_affected"] += 1
                if len(entry["examples"]) < 3:
                    entry["examples"].append({"row_id": row_identifier(row, fieldnames, index),
                                               "raw_value": raw, "clean_value": clean})
        elif kind == "dedupe":
            keys = [k for k in rule.get("keys") or [] if k in fieldnames]
            if not keys:
                warnings.append("dedupe has no valid keys; skipped")
                continue
            entry["fields"] = keys
            seen = set()
            kept = []
            for index, row in enumerate(rows):
                key = tuple(str(row.get(k, "")).strip() for k in keys)
                if key in seen:
                    entry["rows_affected"] += 1
                    if len(entry["examples"]) < 3:
                        entry["examples"].append({"row_id": row_identifier(row, fieldnames, index),
                                                   "raw_value": " | ".join(key),
                                                   "clean_value": "dropped duplicate"})
                else:
                    seen.add(key)
                    kept.append(row)
            rows[:] = kept
        elif kind == "missing":
            field = rule.get("field")
            if field not in fieldnames:
                warnings.append("missing field absent: %s" % field)
                continue
            entry["fields"] = [field]
            policy = str(rule.get("policy") or "keep")
            entry["policy"] = policy
            if policy == "drop":
                kept = []
                for index, row in enumerate(rows):
                    if str(row.get(field, "")).strip() == "":
                        entry["rows_affected"] += 1
                        if len(entry["examples"]) < 3:
                            entry["examples"].append({"row_id": row_identifier(row, fieldnames, index),
                                                       "raw_value": "", "clean_value": "dropped"})
                    else:
                        kept.append(row)
                rows[:] = kept
            elif policy in ("fill_median", "fill_mean", "fill_mode"):
                values = [parse_number(row.get(field)) for row in rows]
                values = [v for v in values if v is not None]
                if policy == "fill_mode":
                    counts = {}
                    for row in rows:
                        value = str(row.get(field, "")).strip()
                        if value:
                            counts[value] = counts.get(value, 0) + 1
                    fill = max(counts, key=counts.get) if counts else ""
                elif values:
                    if policy == "fill_mean":
                        fill = format_number(sum(values) / len(values))
                    else:
                        values.sort()
                        mid = len(values) // 2
                        fill = format_number(values[mid] if len(values) % 2 else
                                             (values[mid - 1] + values[mid]) / 2)
                else:
                    fill = ""
                ensure_raw_column(fieldnames, rows, field, raw_seen)
                for index, row in enumerate(rows):
                    if str(row.get(field, "")).strip() == "":
                        row[field] = fill
                        entry["rows_affected"] += 1
                        if len(entry["examples"]) < 3:
                            entry["examples"].append({"row_id": row_identifier(row, fieldnames, index),
                                                       "raw_value": "", "clean_value": fill})
            else:
                entry["rows_affected"] = 0
        elif kind == "range":
            field = rule.get("field")
            if field not in fieldnames:
                warnings.append("range field absent: %s" % field)
                continue
            low = rule.get("min")
            high = rule.get("max")
            entry["fields"] = [field]
            for index, row in enumerate(rows):
                value = parse_number(row.get(field))
                if value is None:
                    continue
                bad = (low is not None and value < float(low)) or \
                      (high is not None and value > float(high))
                if bad:
                    flag = "%s:%s=%s" % (rid, field, row.get(field))
                    flags.append({"row_id": row_identifier(row, fieldnames, index),
                                  "field": field, "value": row.get(field), "reason": flag})
                    entry["rows_affected"] += 1
                    if len(entry["examples"]) < 3:
                        entry["examples"].append({"row_id": row_identifier(row, fieldnames, index),
                                                   "raw_value": row.get(field),
                                                   "clean_value": "flagged"})
        elif kind == "outlier":
            field = rule.get("field")
            if field not in fieldnames:
                warnings.append("outlier field absent: %s" % field)
                continue
            entry["fields"] = [field]
            values = [(row_identifier(row, fieldnames, i), parse_number(row.get(field)))
                      for i, row in enumerate(rows)]
            numeric = sorted(v for _rid, v in values if v is not None)
            if len(numeric) >= 4:
                q1 = numeric[len(numeric) // 4]
                q3 = numeric[(3 * len(numeric)) // 4]
                iqr = q3 - q1
                low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                for rid_value, value in values:
                    if value is not None and (value < low or value > high):
                        flags.append({"row_id": rid_value, "field": field, "value": value,
                                      "reason": "%s IQR [%s, %s]" % (rid, format_number(low),
                                                                     format_number(high))})
                        entry["rows_affected"] += 1
                        if len(entry["examples"]) < 3:
                            entry["examples"].append({"row_id": rid_value, "raw_value": value,
                                                       "clean_value": "flagged"})
        else:
            warnings.append("unknown rule type: %s" % kind)
        log.append(entry)
    if flags:
        if "flags" not in fieldnames:
            fieldnames.append("flags")
        for index, row in enumerate(rows):
            rid = row_identifier(row, fieldnames, index)
            row["flags"] = "; ".join(f["reason"] for f in flags if f["row_id"] == rid)
    return log, flags, total_before


def quality_report(before_rows, after_rows, fieldnames, rules, flags):
    keys = []
    for rule in rules:
        if isinstance(rule, dict) and rule.get("type") == "dedupe":
            keys = [k for k in rule.get("keys") or [] if k in fieldnames]
            break
    metric_fields = [f for f in fieldnames
                     if not f.endswith("__raw") and f != "flags"]
    unit_fields = [rule.get("field") for rule in rules
                   if isinstance(rule, dict) and rule.get("type") == "unit_convert"
                   and rule.get("field")]
    return {
        "before": {"rows": len(before_rows),
                   "missing_rate": missing_rate(before_rows, metric_fields),
                   "duplicate_rate": duplicate_rate(before_rows, keys) if keys else 0.0},
        "after": {"rows": len(after_rows),
                  "missing_rate": missing_rate(after_rows, metric_fields),
                  "duplicate_rate": duplicate_rate(after_rows, keys) if keys else 0.0},
        "unit_consistency": {field: all(
            str(row.get(field, "")).strip() == "" or parse_number(row.get(field)) is not None
            for row in after_rows) for field in unit_fields},
        "outliers_flagged": flags,
        "rules_applied": [r.get("rule_id") for r in rules if isinstance(r, dict)],
    }


def run(input_path, rules_path, out_dir, result_path, case_id=None):
    fieldnames, rows, encoding = read_csv(input_path)
    config = load_json(rules_path)
    rules = config.get("rules") if isinstance(config, dict) else config
    if not isinstance(rules, list):
        raise ValueError("rules file must contain a rules[] list")
    primary_key = config.get("primary_key") if isinstance(config, dict) else None
    before_rows = [dict(row) for row in rows]
    warnings = []
    log, flags, total_before = apply_rules(fieldnames, rows, rules, primary_key, warnings)
    if not case_id:
        case_id = (config.get("case_id") if isinstance(config, dict) else None) or "unknown-case"
    if case_id == "unknown-case":
        warnings.append("case_id missing; wrote unknown-case")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cleaned = out / "cleaned.csv"
    write_csv(cleaned, fieldnames, rows)
    quality = quality_report(before_rows, rows, fieldnames, rules, flags)
    log_obj = {"case_id": case_id, "encoding": encoding, "primary_key": primary_key,
               "rules": log, "summary": {"rows_before": total_before,
                                          "rows_after": len(rows),
                                          "rules": len(log), "warnings": warnings}}
    (out / "cleaning_log.json").write_text(json.dumps(log_obj, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    (out / "quality_report.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    result = {
        "case_id": case_id,
        "schema": profile(fieldnames, rows),
        "rules": [{"rule_id": r.get("rule_id"), "type": r.get("type"),
                   "fields": r.get("fields"), "rows_affected": r.get("rows_affected")}
                  for r in log],
        "cleaning_log": log,
        "quality_report": quality,
        "artifacts": [
            {"id": "cleaned", "path": "cleaned.csv", "format": "csv"},
            {"id": "log", "path": "cleaning_log.json", "format": "json"},
            {"id": "quality", "path": "quality_report.json", "format": "json"},
        ],
        "warnings": warnings,
        "uncertainty": [],
        "change_log": [{"step": 1, "action": "profile -> rules -> clean", "evidence": str(cleaned)}],
    }
    if result_path:
        Path(result_path).parent.mkdir(parents=True, exist_ok=True)
        Path(result_path).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    return {
        "ok": not warnings,
        "case_id": case_id,
        "cleaned": str(cleaned),
        "rows_before": total_before,
        "rows_after": len(rows),
        "rules": len(log),
        "flags": len(flags),
        "warnings": warnings,
        "result": str(result_path) if result_path else None,
    }


def self_test():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "raw.csv").write_text(
            "sample_id,temperature,selectivity\n"
            " a ,300,80\n"
            "b,350,91\n"
            "b,350,91\n"
            "c,5000,120\n", encoding="utf-8")
        rules = {"case_id": "clean-self-test", "rules": [
            {"rule_id": "R1", "type": "trim", "fields": ["sample_id"]},
            {"rule_id": "R2", "type": "unit_convert", "field": "temperature",
             "from_unit": "K", "to_unit": "C", "factor": 1, "offset": -273.15},
            {"rule_id": "R3", "type": "range", "field": "selectivity",
             "min": 0, "max": 100, "action": "flag"},
            {"rule_id": "R4", "type": "dedupe", "keys": ["sample_id"], "keep": "first"},
        ]}
        (root / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
        report = run(root / "raw.csv", root / "rules.json", root / "out",
                     root / "out" / "result.json")
        assert report["ok"], report
        assert report["rows_after"] == 3, report
        assert report["flags"] == 1, report
        return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None)
    ap.add_argument("--rules", default=None)
    ap.add_argument("--out-dir", default="/app/output/data")
    ap.add_argument("--result", default=None)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        report = self_test()
        print(json.dumps({"ok": True, "self_test": True, "report": report},
                         ensure_ascii=False, indent=2))
        return 0
    if not args.input or not args.rules:
        ap.error("--input and --rules are required unless --self-test is used")
    try:
        report = run(args.input, args.rules, args.out_dir, args.result, args.case_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
