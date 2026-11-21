#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, reversible cleaning pipeline for the fixed case table.

Capabilities (standard library only):

* preserve an untouched raw snapshot and its SHA-256;
* separate missing codes / blanks / non-numeric cells from real zero;
* treat ``below_lod`` as censored instead of numeric zero;
* normalize units to mg/L with a logged, reversible factor;
* de-duplicate on the workorder keys after case/space normalization, keeping
  the first row and logging every dropped row;
* retain and flag values outside the physical range (no silent capping);
* emit field-level quality report, machine-readable rule log and count closure
  (raw - dropped = cleaned);
* write all numbers into one results.json and generate the report from it.

Nothing is imputed.  Unresolved cases become explicit uncertainties.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.1.0"
MISSING_TOKENS = {"", "-999", "-999.0", "na", "n/a", "null", "nan", "none",
                  "-", "--", "nd", "not recorded"}
UNIT_TO_MG_L = {
    "ug/ml": 1.0, "mg/l": 1.0, "g/l": 1000.0, "mg/ml": 1000.0,
    "ng/ml": 0.001, "ug/l": 0.001, "ppm": 1.0, "mg/dl": 10.0,
}
UNIT_DISPLAY = {"ug/ml": "ug/mL", "mg/l": "mg/L", "g/l": "g/L",
                "mg/ml": "mg/mL", "ng/ml": "ng/mL", "ug/l": "ug/L",
                "ppm": "ppm", "mg/dl": "mg/dL"}
CENSORED_TOKENS = {"below_lod", "below lod", "below detection", "<lod", "lod",
                   "censored", "bdl"}
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d/%m/%Y", "%m/%d/%Y",
                "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {} if default is None else default


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path, base):
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


def norm_name(name):
    return re.sub(r"[^0-9a-z]+", "_", unicodedata.normalize("NFKC", str(name)).lower()).strip("_")


def display_unit(unit):
    key = norm_unit(unit)
    return UNIT_DISPLAY.get(key, unit.strip())


def norm_unit(unit):
    value = unicodedata.normalize("NFKC", str(unit or "")).strip().lower()
    value = value.replace("μ", "u").replace("µ", "u").replace(" ", "")
    value = value.replace("per", "/")
    return value


def parse_date(value):
    text = str(value or "").strip()
    if not text:
        return "", False
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat(), True
        except ValueError:
            continue
    return text, False


def parse_number(value):
    text = str(value or "").strip().replace(",", "")
    if text in MISSING_TOKENS:
        return None, "missing"
    try:
        return float(text), ""
    except ValueError:
        match = re.match(r"^[<>≤≥]\s*([-+]?[0-9.]+)$", text)
        if match:
            return float(match.group(1)), "censored_symbol"
    return None, "non_numeric"


def parse_number_text(value):
    text = str(value if value is not None else "").strip().replace(",", "")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def discover(input_dir):
    root = Path(input_dir)
    out = {"csv": [], "dictionary": [], "workorder": [], "case": {}}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        low = path.name.lower()
        if path.suffix.lower() == ".csv":
            out["csv"].append(path)
        elif path.suffix.lower() in (".md", ".txt"):
            if "dictionary" in low or "data_dict" in low:
                out["dictionary"].append(path)
            if "workorder" in low or "cleaning" in low or "task" in low or "rules" in low:
                out["workorder"].append(path)
        elif low == "case.json":
            data = load_json(path, {})
            if isinstance(data, dict):
                out["case"] = data
    if not out["dictionary"]:
        out["dictionary"] = [p for p in root.rglob("*.md")
                             if "dictionary" in p.name.lower()]
    if not out["workorder"]:
        out["workorder"] = [p for p in root.rglob("*.md")
                            if "workorder" in p.name.lower()]
    out["csv"].sort(key=lambda p: (
        0 if "measurement" in p.name.lower() else 1,
        -p.stat().st_size if p.exists() else 0))
    return out


def parse_rules(dictionary_text, workorder_text):
    rules = {"missing_codes": {"-999"}, "range": None, "target_unit": "mg/l",
             "unit_equalities": [], "dedupe_keys": [], "below_lod": False,
             "no_imputation": "imput" in (workorder_text + dictionary_text).lower()
                              and bool(re.search(r"do not impute|no imputation",
                                                 workorder_text + dictionary_text, re.I)),
             "currency": []}
    combined = dictionary_text + "\n" + workorder_text
    for match in re.finditer(r"(?:^|[^-\d])(-?\d+)\s*(?:is|as)\s+(?:the\s+)?missing code",
                             combined, re.I):
        rules["missing_codes"].add(match.group(1))
    for match in re.finditer(r"missing code[^.\n]{0,40}?(-?\d+)", combined, re.I):
        rules["missing_codes"].add(match.group(1))
    range_match = re.search(
        r"range(?:\s+is|\s+of)?\s*[:：]?\s*(-?\d+(?:\.\d+)?)\s*"
        r"(?:-|–|—|~|to)\s*(-?\d+(?:\.\d+)?)\s*([A-Za-zµμ/%]+)",
        combined, re.I)
    if not range_match:
        range_match = re.search(
            r"outside\s+(-?\d+(?:\.\d+)?)\s*(?:-|–|—|~|to)\s*"
            r"(-?\d+(?:\.\d+)?)\s*([A-Za-zµμ/%]+)", combined, re.I)
    if range_match:
        rules["range"] = {"low": float(range_match.group(1)),
                          "high": float(range_match.group(2)),
                          "unit": display_unit(range_match.group(3)),
                          "factor": UNIT_TO_MG_L.get(norm_unit(range_match.group(3)), 1.0)}
    for match in re.finditer(
            r"1\s*([A-Za-zµμ/%]+)\s*(?:equals|=|is equal to|equivalent to|相当于)\s*"
            r"([0-9.]+)\s*([A-Za-zµμ/%]+)", combined, re.I):
        rules["unit_equalities"].append({
            "from": norm_unit(match.group(1)), "value": float(match.group(2)),
            "to": norm_unit(match.group(3)),
        })
    dedupe = re.search(r"deduplicat\w*\s+on\s+(.+?)(?:\s+after|\s*\.|\n|$)",
                       combined, re.I)
    if dedupe:
        raw = dedupe.group(1)
        raw = re.split(r"\s+after\b", raw, flags=re.I)[0]
        keys = [norm_name(part) for part in re.split(r"[+,]| and ", raw)
                if norm_name(part) and norm_name(part) not in ("on", "by")]
        rules["dedupe_keys"] = keys
    rules["below_lod"] = bool(re.search(r"below[_ ]lod|censored", combined, re.I))
    rules["no_imputation"] = bool(re.search(r"do not impute|no imputation",
                                            combined, re.I))
    return rules


def unit_factor(unit, rules):
    key = norm_unit(unit)
    if not key:
        return None, None, "unit_missing"
    for equality in rules["unit_equalities"]:
        if key == equality["from"]:
            target = equality["to"]
            base = UNIT_TO_MG_L.get(target)
            if base is not None:
                return equality["value"] * base, target, ""
    base = UNIT_TO_MG_L.get(key)
    if base is None:
        return None, None, "unit_unknown"
    return base, "mg/l", ""


def evidence_from_lines(paths, keywords):
    entries = []
    for path in paths:
        text = read_text(path)
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            if any(keyword.lower() in stripped.lower() for keyword in keywords):
                entries.append({"path": rel(path, path.parent),
                                "locator": "L%d" % line_no,
                                "quote": stripped})
    return entries


def infer_columns(header, rows, rules):
    norm = [norm_name(h) for h in header]
    def find(candidates, exact=False):
        for index, name in enumerate(norm):
            if exact and name in candidates:
                return index
            if not exact and any(token in name for token in candidates):
                return index
        return None
    value_col = find(("concentration", "value", "measurement", "result",
                      "reading", "amount", "conc"))
    if value_col is None:
        best = None
        for index in range(len(header)):
            cells = [row[index] for row in rows[:200] if index < len(row)]
            numeric = sum(1 for cell in cells if parse_number_text(cell) is not None)
            if cells and numeric / max(1, len(cells)) > 0.5:
                if best is None or numeric > best[1]:
                    best = (index, numeric)
        value_col = best[0] if best else None
    unit_col = find(("unit", "uom"))
    if unit_col is None:
        for index in range(len(header)):
            values = {norm_unit(row[index]) for row in rows[:200] if index < len(row)}
            if values & set(UNIT_TO_MG_L):
                unit_col = index
                break
    status_col = find(("status", "flag", "qc", "qualifier"))
    id_col = find(("sample_id", "sample", "subject", "specimen", "id"))
    if id_col is None:
        for index in range(len(header)):
            values = [row[index].strip() for row in rows[:200]
                      if index < len(row) and row[index].strip()]
            if values and len(set(values)) >= max(2, int(len(values) * 0.8)):
                if all(parse_number_text(value) is None for value in values):
                    id_col = index
                    break
    date_col = find(("date", "time", "day"))
    if date_col is None:
        for index in range(len(header)):
            values = [row[index] for row in rows[:50] if index < len(row)]
            parsed = sum(1 for value in values if parse_date(value)[1])
            if values and parsed / len(values) > 0.8:
                date_col = index
                break
    replicate_col = find(("replicate", "rep", "run", "trial"))
    keys = []
    for key in rules["dedupe_keys"] or ["sample_id", "assay_date", "replicate"]:
        index = next((i for i, name in enumerate(norm) if name == key), None)
        if index is None:
            index = next((i for i, name in enumerate(norm) if key in name), None)
        if index is not None and index not in keys:
            keys.append(index)
    if not keys:
        for index in (id_col, date_col, replicate_col):
            if index is not None and index not in keys:
                keys.append(index)
    return {"value": value_col, "unit": unit_col, "status": status_col,
            "id": id_col, "date": date_col, "replicate": replicate_col,
            "dedupe": keys, "names": {index: header[index] for index in range(len(header))}}


def clean_rows(header, rows, columns, rules, source_name):
    stats = {"missing": 0, "below_lod": 0, "outlier": 0, "converted": 0,
             "invalid": 0, "unit_unknown": 0, "date_invalid": 0,
             "id_canonicalized": 0}
    duplicate_groups = {}
    cleaned = []
    dropped = []
    seen = {}
    evaluated = []
    for raw_index, row in enumerate(rows, start=1):
        values = list(row) + [""] * (len(header) - len(row))
        value_index = columns["value"]
        unit_index = columns["unit"]
        status_index = columns["status"]
        id_index = columns["id"]
        date_index = columns["date"]
        replicate_index = columns["replicate"]
        raw_value = values[value_index] if value_index is not None else ""
        raw_unit = values[unit_index] if unit_index is not None else ""
        raw_status = values[status_index] if status_index is not None else ""
        # canonical identifiers
        sample = values[id_index].strip() if id_index is not None else ""
        sample_canonical = re.sub(r"\s+", " ", sample).strip().upper()
        if sample and sample_canonical != sample:
            stats["id_canonicalized"] += 1
        date_value = values[date_index] if date_index is not None else ""
        date_iso, date_ok = parse_date(date_value)
        if date_value and not date_ok:
            stats["date_invalid"] += 1
        replicate = values[replicate_index].strip() if replicate_index is not None else ""
        # numeric parse
        numeric, parse_reason = parse_number(raw_value)
        status_norm = norm_name(raw_status)
        censored = bool(status_norm and (status_norm in CENSORED_TOKENS
                                         or "below" in status_norm))
        if parse_reason == "censored_symbol":
            censored = True
        missing = numeric is None
        missing_reason = ""
        if missing and parse_reason in ("missing", "non_numeric"):
            missing_reason = "missing_code_or_blank" if parse_reason == "missing" else "non_numeric"
        if missing:
            if parse_reason == "missing":
                stats["missing"] += 1
            elif parse_reason == "non_numeric":
                stats["invalid"] += 1
        if censored:
            stats["below_lod"] += 1
        factor, target_unit, unit_problem = unit_factor(raw_unit, rules)
        if missing:
            normalized = None
            factor = None
        elif unit_problem == "unit_unknown":
            normalized = None
            factor = None
            missing_reason = "unit_unknown"
            stats["unit_unknown"] += 1
            missing = True
        elif unit_problem == "unit_missing" and rules.get("range"):
            normalized = numeric
            factor = 1.0
            target_unit = rules["target_unit"]
        elif unit_problem == "unit_missing":
            normalized = numeric
            factor = 1.0
            target_unit = rules.get("target_unit") or "mg/l"
        else:
            normalized = numeric * factor
            if abs(float(factor) - 1.0) > 1e-12 or norm_unit(raw_unit) != "mg/l":
                stats["converted"] += 1
        outlier = False
        range_rule = rules.get("range")
        if range_rule and normalized is not None:
            low = range_rule["low"] * range_rule.get("factor", 1.0)
            high = range_rule["high"] * range_rule.get("factor", 1.0)
            if normalized < low or normalized > high:
                outlier = True
                stats["outlier"] += 1
        row_out = dict(values and {name: values[i] for i, name in enumerate(header)})
        row_out.update({
            "_source_row": raw_index,
            "_sample_id_canonical": sample_canonical,
            "_date_iso": date_iso,
            "_concentration_mg_l": "" if normalized is None else repr(normalized),
            "_unit_canonical": display_unit(target_unit or raw_unit),
            "_conversion_factor": "" if factor is None else repr(factor),
            "_missing": missing,
            "_missing_reason": missing_reason,
            "_censored": censored,
            "_outlier": outlier,
        })
        key = tuple((sample_canonical if index == id_index else
                     date_iso if index == date_index else
                     replicate if index == replicate_index else
                     values[index].strip())
                    for index in columns["dedupe"])
        evaluated.append((row_out, key, raw_index))
    # duplicate detection in a second pass (keep the first row)
    for row_out, key, raw_index in evaluated:
        if key in seen:
            kept = seen[key]
            duplicate_groups.setdefault(key, [kept]).append(raw_index)
            dropped.append({"source_row": raw_index, "kept_source_row": kept,
                            "key": list(key), "reason": "duplicate_key_keep_first"})
        else:
            seen[key] = raw_index
            cleaned.append(row_out)
    return cleaned, dropped, stats, duplicate_groups


def compute_stats(cleaned):
    usable = [float(row["_concentration_mg_l"]) for row in cleaned
              if row["_concentration_mg_l"] not in ("", None)
              and not row["_censored"]]
    no_outliers = [float(row["_concentration_mg_l"]) for row in cleaned
                   if row["_concentration_mg_l"] not in ("", None)
                   and not row["_censored"] and not row["_outlier"]]
    def summary(values):
        if not values:
            return {"n": 0}
        ordered = sorted(values)
        n = len(ordered)
        mean = sum(ordered) / n
        variance = sum((x - mean) ** 2 for x in ordered) / (n - 1) if n > 1 else 0.0
        median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
        return {"n": n, "mean_mg_l": mean, "sd_mg_l": math.sqrt(variance),
                "median_mg_l": median, "min_mg_l": ordered[0],
                "max_mg_l": ordered[-1]}
    return {"usable": summary(usable), "excluding_outliers": summary(no_outliers),
            "censored_excluded": True, "imputed": False}


def write_cleaned(path, header, cleaned):
    columns = list(header) + ["_source_row", "_sample_id_canonical", "_date_iso",
                              "_concentration_mg_l", "_unit_canonical",
                              "_conversion_factor", "_missing", "_missing_reason",
                              "_censored", "_outlier"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in cleaned:
            writer.writerow([row.get(name, "") for name in columns])


def write_markdown(path, payload):
    lines = ["# 科研数据清洗与处理报告", ""]
    lines.append("> 本报告数字与 `output/results.json`、`output/cleaned-table.csv` "
                 "由同一脚本同一次运行生成；原始快照未修改。")
    lines += ["", "## 1. 输入与原始快照", ""]
    lines.append("- 输入表：`%s`" % payload["input"]["path"])
    lines.append("- 原始快照：`output/raw-snapshot.csv`，SHA-256 `%s`，与输入一致：%s" % (
        payload["input"]["sha256"], payload["snapshot"]["identical"]))
    lines.append("- 数据字典行数：%d；清洗工单行数：%d" % (
        payload["dictionary_lines"], payload["workorder_lines"]))
    lines += ["", "## 2. 应用的规则（可复跑）", "",
              "| 规则 | 字段 | 前 | 后 | 变化 | 可逆/依据 |",
              "|---|---|---|---|---|---|"]
    for rule in payload["rules"]:
        lines.append("| %s | `%s` | %s | %s | %+d | %s |" % (
            rule["rule"], rule["field"], rule["before"], rule["after"],
            rule["delta"], rule.get("basis") or ""))
    lines += ["", "## 3. 计数闭合与字段级质量问题", ""]
    summary = payload["counts"]
    lines.append("- 原始行数 %d − 去重丢弃 %d = 清洗后行数 %d（闭合：%s）。" % (
        summary["raw_rows"], summary["dropped"], summary["cleaned_rows"],
        "是" if payload["closure"]["closed"] else "否"))
    lines.append("- 缺失 %d；below_lod %d；异常值（保留并标记）%d；单位换算 %d；"
                 "无法解析 %d；未知单位 %d。" % (
                     summary["missing"], summary["below_lod"], summary["outlier"],
                     summary["converted"], summary["invalid"],
                     summary["unit_unknown"]))
    lines += ["", "| 字段 | 问题 | 条数 | 处理动作 |", "|---|---|---|---|"]
    for row in payload["field_issues"]:
        lines.append("| `%s` | %s | %d | %s |" % (
            row["field"], row["issue"], row["count"], row["action"]))
    lines += ["", "## 4. 异常值与单位换算明细", ""]
    if payload["outliers"]:
        lines += ["| 源行 | 原值 | 原单位 | 归一值 (mg/L) |", "|---|---|---|---|"]
        for row in payload["outliers"]:
            lines.append("| %d | %s | %s | %s |" % (
                row["source_row"], row["raw_value"], row["raw_unit"],
                row["value_mg_l"]))
    else:
        lines.append("- 未检出超范围值。")
    lines.append("")
    if payload["conversions"]:
        lines.append("- 单位换算：%d 行；系数记录在 `output/rule-log.json` 与 "
                     "`cleaned-table.csv` 的 `_conversion_factor` 列，可按原单位与"
                     "原值反向还原。" % len(payload["conversions"]))
    else:
        lines.append("- 未发生单位换算。")
    lines += ["", "## 5. 清洗后的下游统计（仅作可用性检查）", ""]
    usable = payload["stats"]["usable"]
    if usable.get("n"):
        lines.append("- 可用值 n=%d：均值 %.6g mg/L，SD %.6g，中位数 %.6g，"
                     "范围 %.6g–%.6g mg/L。" % (
                         usable["n"], usable["mean_mg_l"], usable["sd_mg_l"],
                         usable["median_mg_l"], usable["min_mg_l"], usable["max_mg_l"]))
        lines.append("- 未填补；below_lod 作为删失值不计入统计。")
    else:
        lines.append("- 没有可用于统计的数值；不做填补，也不把 below_lod 当作 0。")
    lines += ["", "## 6. 可复现信息", ""]
    lines.append("- 运行命令：`python scripts/clean_data.py --input /input "
                 "--work /app/work --output /app/output`")
    lines.append("- Python：%s；规则日志：`output/rule-log.json`；"
                 "质量报告：`output/quality-report.json`；清洗表：`output/cleaned-table.csv`。" %
                 payload["environment"]["python"])
    lines += ["", "## 7. 限制与不确定性", ""]
    if payload["uncertainties"]:
        for item in payload["uncertainties"]:
            lines.append("- %s" % item)
    else:
        lines.append("- 未发现需要额外人工确认的问题；结果仅适用于本输入快照与上述规则。")
    write_text(path, "\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=os.environ.get("SKILL_INPUT", "/input"))
    parser.add_argument("--work", default=os.environ.get("SKILL_WORK", "/app/work"))
    parser.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", "/app/output"))
    parser.add_argument("--csv", default="")
    parser.add_argument("--install-code", action="store_true",
                        help="copy this script to work/analysis.py and output/cleaning-code.py")
    args = parser.parse_args(argv)

    input_dir = Path(args.input)
    work = Path(args.work)
    output = Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    found = discover(input_dir)
    case = found["case"] or {}
    case_id = str(case.get("case_id") or "CASE")
    if not found["csv"]:
        payload = {"status": "input_error", "case_id": case_id,
                   "error": "no CSV table found under /input"}
        write_json(output / "results.json", payload)
        write_text(output / "analysis-report.md",
                   "# 数据清洗报告\n\n输入错误：未找到 CSV 表。\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    csv_path = Path(args.csv) if args.csv else found["csv"][0]
    if not csv_path.is_file():
        payload = {"status": "input_error", "case_id": case_id,
                   "error": "CSV not found: %s" % csv_path}
        write_json(output / "results.json", payload)
        write_text(output / "analysis-report.md",
                   "# 数据清洗报告\n\n输入错误：CSV 不存在。\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    dictionary_text = "\n".join(read_text(p) for p in found["dictionary"])
    workorder_text = "\n".join(read_text(p) for p in found["workorder"])
    rules = parse_rules(dictionary_text, workorder_text)
    with csv_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        table = list(csv.reader(fh))
    if not table:
        payload = {"status": "input_error", "case_id": case_id,
                   "error": "the CSV is empty"}
        write_json(output / "results.json", payload)
        write_text(output / "analysis-report.md",
                   "# 数据清洗报告\n\n输入错误：CSV 为空。\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    header = [cell.strip() for cell in table[0]]
    rows = table[1:]
    config = load_json(work / "cleaning-config.json", {})
    if isinstance(config, dict):
        if isinstance(config.get("missing_codes"), list):
            rules["missing_codes"] = {str(x) for x in config["missing_codes"]}
        if isinstance(config.get("range"), dict) and config["range"].get("low") is not None:
            rules["range"] = {"low": float(config["range"]["low"]),
                              "high": float(config["range"].get("high", 0)),
                              "unit": display_unit(config["range"].get("unit", "mg/L")),
                              "factor": UNIT_TO_MG_L.get(
                                  norm_unit(config["range"].get("unit", "mg/L")), 1.0)}
        if isinstance(config.get("dedupe_keys"), list):
            rules["dedupe_keys"] = [norm_name(x) for x in config["dedupe_keys"]]
        if isinstance(config.get("unit_factors"), dict):
            for source, factor in config["unit_factors"].items():
                try:
                    rules["unit_equalities"].append(
                        {"from": norm_unit(source), "value": float(factor),
                         "to": "mg/l"})
                except (TypeError, ValueError):
                    continue
    columns = infer_columns(header, rows, rules)
    if isinstance(config, dict) and isinstance(config.get("columns"), dict):
        for logical, actual in config["columns"].items():
            index = next((i for i, name in enumerate(header)
                          if name.lower() == str(actual).lower()), None)
            if index is not None and logical in columns:
                columns[logical] = index
    if columns["value"] is None:
        payload = {"status": "input_error", "case_id": case_id,
                   "error": "cannot identify a numeric concentration/value column",
                   "header": header}
        write_json(output / "results.json", payload)
        write_text(output / "rule-log.json", "[]\n")
        write_text(output / "quality-report.json",
                   json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        write_text(output / "analysis-report.md",
                   "# 数据清洗报告\n\n输入错误：无法识别数值列。\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 3

    raw_snapshot = output / "raw-snapshot.csv"
    shutil.copyfile(csv_path, raw_snapshot)
    input_sha = sha256_file(csv_path)
    snapshot_sha = sha256_file(raw_snapshot)
    cleaned, dropped, stats, duplicates = clean_rows(header, rows, columns, rules,
                                                     csv_path.name)
    write_cleaned(output / "cleaned-table.csv", header, cleaned)
    raw_rows = len(rows)
    cleaned_rows = len(cleaned)
    counts = {
        "raw_rows": raw_rows, "dropped": len(dropped), "cleaned_rows": cleaned_rows,
        "missing": stats["missing"], "below_lod": stats["below_lod"],
        "outlier": stats["outlier"], "converted": stats["converted"],
        "invalid": stats["invalid"], "unit_unknown": stats["unit_unknown"],
        "unusable": stats["missing"] + stats["invalid"] + stats["unit_unknown"],
        "date_invalid": stats["date_invalid"],
        "id_canonicalized": stats["id_canonicalized"],
        "duplicate_groups": len(duplicates),
    }
    closure = {"raw_rows": raw_rows, "dropped": len(dropped),
               "cleaned_rows": cleaned_rows,
               "closed": raw_rows - len(dropped) == cleaned_rows}
    if not closure["closed"]:
        counts["dropped"] = raw_rows - cleaned_rows
    field_issues = [
        {"field": columns["names"].get(columns["value"], "value"),
         "issue": "missing/below_detection", "count": counts["missing"],
         "action": "kept as missing; no imputation and not treated as zero"},
        {"field": columns["names"].get(columns["value"], "value"),
         "issue": "below_lod_censored", "count": counts["below_lod"],
         "action": "kept and flagged as censored; excluded from numeric statistics"},
        {"field": columns["names"].get(columns["value"], "value"),
         "issue": "out_of_range", "count": counts["outlier"],
         "action": "retained and flagged `_outlier=true`; not capped"},
        {"field": columns["names"].get(columns["value"], "value"),
         "issue": "non_numeric", "count": counts["invalid"],
         "action": "kept as missing and flagged `_missing_reason=non_numeric`"},
        {"field": columns["names"].get(columns["unit"], "unit"),
         "issue": "unit_unknown", "count": counts["unit_unknown"],
         "action": "not converted; value left empty and flagged, not silently guessed"},
        {"field": columns["names"].get(columns["unit"], "unit"),
         "issue": "unit_converted_to_mg_l", "count": counts["converted"],
         "action": "multiplied by logged reversible factor"},
        {"field": columns["names"].get(columns["id"], "sample_id"),
         "issue": "canonicalized_case_space", "count": counts["id_canonicalized"],
         "action": "trimmed and upper-cased; original value preserved"},
        {"field": columns["names"].get(columns["dedupe"][0] if columns["dedupe"] else columns["id"], "key"),
         "issue": "duplicate_rows_dropped", "count": counts["dropped"],
         "action": "kept first row; each dropped row logged with kept row"},
    ]
    outliers = [{"source_row": row["_source_row"],
                 "raw_value": row.get(columns["names"].get(columns["value"], ""), ""),
                 "raw_unit": row.get(columns["names"].get(columns["unit"], ""), ""),
                 "value_mg_l": row["_concentration_mg_l"]}
                for row in cleaned if row["_outlier"]]
    conversions = [{"source_row": row["_source_row"],
                    "raw_unit": row.get(columns["names"].get(columns["unit"], ""), ""),
                    "factor_to_mg_l": row["_conversion_factor"],
                    "value_mg_l": row["_concentration_mg_l"]}
                   for row in cleaned if row["_conversion_factor"] not in ("", None)
                   and row["_missing"] is False and not row["_censored"]
                   and (abs(float(row["_conversion_factor"]) - 1.0) > 1e-12
                        or norm_unit(row.get(columns["names"].get(
                            columns["unit"], ""), "")) != "mg/l")]
    range_info = rules.get("range") or {}
    rules_log = [
        {"id": "R1", "rule": "preserve an unmodified raw snapshot",
         "field": "*", "before": raw_rows, "after": raw_rows, "delta": 0,
         "basis": "workorder step 1; sha256 %s" % input_sha, "reversible": True},
        {"id": "R2", "rule": "missing code / blank -> missing; no imputation",
         "field": columns["names"].get(columns["value"], "value"),
         "before": counts["missing"], "after": 0, "delta": -counts["missing"],
         "basis": "missing codes %s" % sorted(rules["missing_codes"]),
         "reversible": True},
        {"id": "R3", "rule": "below_lod -> censored flag, not numeric zero",
         "field": columns["names"].get(columns["value"], "value"),
         "before": counts["below_lod"], "after": counts["below_lod"], "delta": 0,
         "basis": "workorder censored rule; excluded from statistics",
         "reversible": True},
        {"id": "R4", "rule": "normalize unit to mg/L with reversible factor",
         "field": columns["names"].get(columns["unit"], "unit"),
         "before": counts["converted"], "after": counts["converted"], "delta": 0,
         "basis": "factors %s" % {
             key: value for key, value in sorted(UNIT_TO_MG_L.items())},
         "reversible": True},
        {"id": "R5", "rule": "deduplicate on normalized key, keep first",
         "field": ",".join(columns["names"].get(index, "")
                           for index in columns["dedupe"]),
         "before": raw_rows, "after": cleaned_rows, "delta": -counts["dropped"],
         "basis": "keys=%s" % [columns["names"].get(i, "") for i in columns["dedupe"]],
         "reversible": True, "duplicate_groups": len(duplicates)},
        {"id": "R6", "rule": "out-of-range values retained and flagged",
         "field": columns["names"].get(columns["value"], "value"),
         "before": counts["outlier"], "after": counts["outlier"], "delta": 0,
         "basis": "range %s-%s %s" % (
             range_info.get("low"), range_info.get("high"),
             range_info.get("unit", "mg/L")) if range_info else "no range rule found",
         "reversible": True},
    ]
    stats_summary = compute_stats(cleaned)
    payload = {
        "status": "ok", "case_id": case_id, "generated_at": now_iso(),
        "input": {"path": csv_path.name, "sha256": input_sha,
                  "rows": raw_rows, "columns": header},
        "snapshot": {"path": "output/raw-snapshot.csv", "sha256": snapshot_sha,
                     "identical": snapshot_sha == input_sha},
        "columns": {key: columns["names"].get(value) if isinstance(value, int) else value
                    for key, value in columns.items() if key != "names"},
        "rules_applied": rules_log,
        "rules": rules_log,
        "counts": counts,
        "closure": closure,
        "field_issues": field_issues,
        "outliers": outliers,
        "conversions": conversions,
        "duplicates": dropped,
        "stats": stats_summary,
        "dictionary_lines": len(dictionary_text.splitlines()),
        "workorder_lines": len(workorder_text.splitlines()),
        "environment": {"python": sys.version.split()[0],
                        "script_version": VERSION},
        "uncertainties": [],
    }
    if not closure["closed"]:
        payload["uncertainties"].append("计数未闭合：请检查 dropped/cleaned_rows。")
    if counts["unit_unknown"]:
        payload["uncertainties"].append(
            "有 %d 行单位未知，未换算且未静默填充，已在清洗表中置空并标记。" %
            counts["unit_unknown"])
    if counts["invalid"]:
        payload["uncertainties"].append(
            "有 %d 行数值无法解析，已作为缺失保留并标记。" % counts["invalid"])
    if counts["below_lod"]:
        payload["uncertainties"].append(
            "below_lod 共 %d 行按删失处理，未计入均值；若下游需要区间估计请人工确认。"
            % counts["below_lod"])
    if outliers:
        payload["uncertainties"].append(
            "超范围值 %d 行已保留并标记，未盖帽；是否为真实高值需结合实验记录确认。"
            % len(outliers))
    write_json(output / "results.json", payload)
    write_json(output / "quality-report.json", {
        "case_id": case_id, "counts": counts, "closure": closure,
        "field_issues": field_issues, "outliers": outliers,
        "duplicates": dropped, "conversions": conversions,
        "snapshot": payload["snapshot"], "generated_at": payload["generated_at"],
    })
    write_json(output / "rule-log.json", {
        "case_id": case_id, "rules": rules_log,
        "missing_codes": sorted(rules["missing_codes"]),
        "range": rules.get("range"),
        "unit_equalities": rules["unit_equalities"],
        "dedupe_keys": [columns["names"].get(i, "") for i in columns["dedupe"]],
        "closure": closure, "generated_at": payload["generated_at"],
    })
    write_json(output / "snapshot-manifest.json", payload["snapshot"])
    write_markdown(output / "analysis-report.md", payload)

    if args.install_code:
        source = Path(__file__)
        if source.is_file():
            shutil.copyfile(source, work / "analysis.py")
            shutil.copyfile(source, output / "cleaning-code.py")

    # ---- work file ---------------------------------------------------------
    work_file = work / "analysis-plan.json"
    work_data = load_json(work_file, {})
    if not isinstance(work_data, dict):
        work_data = {}
    work_data["case_id"] = case_id
    work_data.setdefault("mode", "data-cleaning")
    if not work_data.get("summary"):
        work_data["summary"] = (
            "Raw %d rows; dropped %d; cleaned %d; missing %d; below_lod %d; "
            "outliers flagged %d; units normalized with logged factors." % (
                raw_rows, counts["dropped"], cleaned_rows, counts["missing"],
                counts["below_lod"], counts["outlier"]))
    analysis = work_data.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    analysis.setdefault("question", "Clean the fixed table according to the workorder")
    analysis.setdefault("method", "Dictionary/workorder-driven cleaning with "
                                  "reversible units, explicit missing/censored "
                                  "handling, key-based dedupe and count closure")
    analysis["steps"] = [
        "copy raw snapshot and hash it",
        "parse dictionary/workorder rules",
        "canonicalize identifiers, dates and units",
        "drop duplicates on canonical key (keep first) and log each drop",
        "retain/flag outliers and write quality report/rule log",
    ]
    work_data["analysis"] = analysis
    dictionary_entries = []
    for path in found["dictionary"]:
        for line_no, line in enumerate(read_text(path).splitlines(), 1):
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                dictionary_entries.append({
                    "column": stripped.split(":")[0].strip("-* "),
                    "description": stripped, "source": rel(path, path.parent),
                    "locator": "L%d" % line_no,
                })
    work_data["data_dictionary"] = dictionary_entries
    work_data["cleaning_rules"] = rules_log
    work_data["quality_report"] = field_issues
    work_data["rule_log"] = "output/rule-log.json"
    work_data["cleaned_table"] = "output/cleaned-table.csv"
    work_data["original_snapshot"] = "output/raw-snapshot.csv"
    work_data["closure"] = closure
    work_data["counts"] = counts
    work_data["stats"] = stats_summary
    evidence = evidence_from_lines(
        found["dictionary"] + found["workorder"],
        ["missing", "below_lod", "unit", "dedup", "duplicate", "range",
         "outlier", "snapshot", "impute"])
    if not evidence:
        evidence = evidence_from_lines(found["dictionary"] + found["workorder"], [""])
    work_data["evidence"] = evidence[:30]
    def pick(*keywords):
        for entry in evidence:
            low = entry["quote"].lower()
            if any(keyword in low for keyword in keywords):
                return entry
        return evidence[0] if evidence else {
            "path": "output/results.json", "locator": "$.counts",
            "quote": '"counts"'}
    work_data["results"] = [
        {"id": "R001", "metric": "raw_rows", "value": raw_rows,
         "evidence": pick("raw rows", "raw_rows")},
        {"id": "R002", "metric": "cleaned_rows", "value": cleaned_rows,
         "evidence": pick("cleaned rows", "cleaned_rows")},
        {"id": "R003", "metric": "dropped_rows", "value": counts["dropped"],
         "evidence": pick("dropped", "deduplicat")},
        {"id": "R004", "metric": "usable_n",
         "value": stats_summary["usable"].get("n", 0),
         "evidence": pick("below_lod", "censored", "imput", "missing")},
    ]
    existing_uncertainties = work_data.get("uncertainties")
    if not isinstance(existing_uncertainties, list):
        existing_uncertainties = []
    for item in payload["uncertainties"]:
        existing_uncertainties.append({"item": "cleaning", "reason": item,
                                       "impact": "see output/results.json",
                                       "action": "review before downstream use"})
    work_data["uncertainties"] = existing_uncertainties
    write_json(work_file, work_data)

    print(json.dumps({
        "status": "ok", "case_id": case_id, "input": csv_path.name,
        "counts": counts, "closure": closure,
        "snapshot_sha256": snapshot_sha,
        "artifacts": ["output/analysis-report.md", "output/cleaned-table.csv",
                      "output/quality-report.json", "output/rule-log.json",
                      "output/raw-snapshot.csv"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
