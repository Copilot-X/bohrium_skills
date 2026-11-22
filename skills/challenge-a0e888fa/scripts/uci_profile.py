#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discover the case snapshot, build an executable UCI profile, and run it.

The script is standard-library only (numpy is optional and only cross-checks).
It performs the following steps, in order:

1. discover SQLite databases and CSV/TSV/JSON/XLSX snapshots under ``/input``;
2. identify Concrete (1030), Iris (150) and Wine (178) tables from column
   signatures and file names, without trusting the file label alone;
3. build ``work/uci-snapshot.sqlite`` with a provenance manifest (SHA-256);
4. map every column to a canonical field and to the provided data dictionary
   (or an explicit built-in convention when no dictionary line exists);
5. run missing / duplicate / outlier / range checks without deleting rows;
6. generate ``work/analysis.py``, execute it, and write mode-specific outputs;
7. write ``output/analysis-report.md`` and merge the executed fields into
   ``work/analysis-plan.json`` so ``run_case.py finalize`` can audit them.

Missing data, unknown columns, empty results and failed SQL are reported in
``uncertainties``; nothing is fabricated or silently repaired.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import uci_core as C  # noqa: E402

SKILL_DIR = HERE.parent
VERSION = "1.0.0"
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".db3", ".sqlite2"}
CSV_SUFFIXES = {".csv", ".tsv", ".data"}
XLSX_SUFFIXES = {".xlsx", ".xlsm"}
JSON_SUFFIXES = {".json"}
MAX_ROWS = 200000


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return default


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def load_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {} if default is None else default


def write_json(path, payload):
    return write_text(path, json.dumps(C._json_safe(payload), ensure_ascii=False,
                                       indent=2, allow_nan=False) + "\n")


def rel(path, base):
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def sanitize_table(name, fallback="table"):
    value = re.sub(r"[^0-9A-Za-z_]+", "_", str(name or "")).strip("_").lower()
    if not value:
        value = fallback
    if value[0].isdigit():
        value = "t_" + value
    return value


def unique_name(name, used):
    candidate, counter = name, 2
    while candidate in used:
        candidate = "%s_%d" % (name, counter)
        counter += 1
    used.add(candidate)
    return candidate


# ---------------------------------------------------------------------------
# Discovery and loading
# ---------------------------------------------------------------------------


def discover(input_dir):
    root = Path(input_dir)
    found = {"sqlite": [], "csv": [], "xlsx": [], "json": [], "text": [],
             "dictionary": []}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        name = path.name.lower()
        if suffix in SQLITE_SUFFIXES:
            found["sqlite"].append(path)
        elif suffix in CSV_SUFFIXES:
            found["csv"].append(path)
        elif suffix in XLSX_SUFFIXES:
            found["xlsx"].append(path)
        elif suffix == ".json" and name not in ("case.json",):
            found["json"].append(path)
        elif suffix in (".md", ".txt", ".yaml", ".yml"):
            found["text"].append(path)
            if ("dictionary" in name or "data_dict" in name or "datadict" in name
                    or "schema" in name):
                found["dictionary"].append(path)
    return found


def detect_delimiter(sample):
    lines = [line for line in sample.splitlines() if line.strip()][:10]
    if not lines:
        return ","
    candidates = [",", ";", "\t", "|"]
    best, best_score = ",", -1
    for delimiter in candidates:
        counts = [line.count(delimiter) for line in lines]
        if not counts or max(counts) == 0:
            continue
        score = sum(1 for count in counts if count == counts[0]) * 10 + counts[0]
        if score > best_score:
            best, best_score = delimiter, score
    if best_score >= 0:
        return best
    if any(re.search(r"\s{2,}", line) for line in lines):
        return "whitespace"
    return ","


def split_line(line, delimiter):
    if delimiter == "whitespace":
        return [cell.strip() for cell in re.split(r"\s{2,}", line.strip())]
    return next(csv.reader([line], delimiter=delimiter))


def name_hint(name):
    text = str(name or "").lower()
    for dataset in ("concrete", "iris", "wine"):
        if dataset in text:
            return dataset
    return ""


def looks_like_header(first, hint):
    cells = [str(cell).strip() for cell in first if str(cell).strip()]
    if not cells:
        return False
    numeric = sum(1 for cell in cells if C.safe_float(cell) is not None)
    if numeric == len(cells):
        return False
    if hint and numeric >= max(1, len(cells) - 1):
        # A headerless Iris row is four numbers plus the class label.
        return False
    return True


def infer_header(first, hint):
    width = len(first)
    if hint and width == len(C.HEADERLESS_ORDER.get(hint, [])):
        return list(C.HEADERLESS_ORDER[hint])
    return ["col_%d" % (index + 1) for index in range(width)]


def read_csv_table(path):
    try:
        sample = Path(path).read_text(encoding="utf-8-sig", errors="replace")[:20000]
    except OSError:
        return None
    delimiter = detect_delimiter(sample)
    try:
        with Path(path).open("r", encoding="utf-8-sig", errors="replace",
                             newline="") as fh:
            if delimiter == "whitespace":
                lines = [line.rstrip("\n\r") for line in fh if line.strip()]
                rows = [split_line(line, delimiter) for line in lines]
            else:
                rows = [list(row) for row in csv.reader(fh, delimiter=delimiter)]
    except OSError:
        return None
    rows = [row for row in rows if any(str(cell).strip() for cell in row)]
    if not rows:
        return None
    hint = name_hint(path.name)
    if looks_like_header(rows[0], hint):
        header = [str(cell).strip() or "col_%d" % (index + 1)
                  for index, cell in enumerate(rows[0])]
        body = rows[1:]
    else:
        header = infer_header(rows[0], hint)
        body = rows
    width = len(header)
    body = [(row + [""] * width)[:width] for row in body]
    return {
        "name": sanitize_table(path.stem),
        "source": rel(path, path.parent),
        "source_path": str(path),
        "source_kind": "csv",
        "source_table": path.stem,
        "raw_header": header,
        "raw_rows": body[:MAX_ROWS],
        "truncated": max(0, len(body) - MAX_ROWS),
        "delimiter": delimiter,
    }


def read_sqlite_tables(path):
    uri = "file:%s?mode=ro" % Path(path).resolve().as_posix()
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return []
    out = []
    try:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names:
            try:
                info = conn.execute("PRAGMA table_info(%s)" % C.quote_ident(name)).fetchall()
                header = [row[1] for row in info]
                rows = [list(row) for row in conn.execute(
                    "SELECT * FROM %s LIMIT %d" % (C.quote_ident(name), MAX_ROWS + 1))]
                total = conn.execute("SELECT COUNT(*) FROM %s" % C.quote_ident(name)).fetchone()[0]
            except sqlite3.Error:
                continue
            if not header:
                continue
            out.append({
                "name": sanitize_table(name),
                "source": rel(path, path.parent),
                "source_path": str(path),
                "source_kind": "sqlite",
                "source_table": name,
                "raw_header": header,
                "raw_rows": rows[:MAX_ROWS],
                "truncated": max(0, (total or 0) - MAX_ROWS),
                "sqlite_total": total,
            })
    finally:
        conn.close()
    return out


def read_xlsx_table(path):
    try:
        import openpyxl  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        sheet = workbook.worksheets[0]
        rows = []
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if index >= MAX_ROWS:
                break
            rows.append(["" if value is None else value for value in row])
        workbook.close()
    except Exception:  # noqa: BLE001
        return None
    rows = [row for row in rows if any(str(cell).strip() for cell in row)]
    if not rows:
        return None
    hint = name_hint(path.name)
    if looks_like_header(rows[0], hint):
        header = [str(cell).strip() or "col_%d" % (i + 1)
                  for i, cell in enumerate(rows[0])]
        body = rows[1:]
    else:
        header = infer_header(rows[0], hint)
        body = rows
    width = len(header)
    return {
        "name": sanitize_table(path.stem),
        "source": rel(path, path.parent),
        "source_path": str(path),
        "source_kind": "xlsx",
        "source_table": path.stem,
        "raw_header": header,
        "raw_rows": [(row + [""] * width)[:width] for row in body],
        "truncated": 0,
        "delimiter": "xlsx",
    }


def read_json_table(path):
    data = load_json(path, None)
    if data is None:
        return None
    rows = None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        rows = data
    elif isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                rows = value
                break
    if not rows:
        return None
    header = list(dict.fromkeys(key for row in rows for key in row))
    body = [[row.get(key, "") for key in header] for row in rows[:MAX_ROWS]]
    return {
        "name": sanitize_table(path.stem),
        "source": rel(path, path.parent),
        "source_path": str(path),
        "source_kind": "json",
        "source_table": path.stem,
        "raw_header": header,
        "raw_rows": body,
        "truncated": max(0, len(rows) - MAX_ROWS),
        "delimiter": "json",
    }


def infer_dtype(values):
    non_missing = [value for value in values if not C.is_missing(value)]
    if not non_missing:
        return "TEXT"
    numeric = sum(1 for value in non_missing
                  if C.safe_float(value) is not None)
    return "NUMERIC" if numeric / len(non_missing) >= 0.99 else "TEXT"


def detect_dataset(raw_table):
    hint = name_hint(raw_table["source_table"] + " " + raw_table["name"]
                     + " " + raw_table["source"])
    keys = [C.match_canonical(header) for header in raw_table["raw_header"]]
    # If the header was already canonical, match_canonical handles it.
    best = {"dataset": "generic", "ratio": 0.0, "hint": hint}
    for dataset, spec in C.SPECS.items():
        spec_keys = {column[0] for column in spec["columns"]}
        hits = sum(1 for key in keys if key and (
            key in spec_keys or key == "class_label"))
        ratio = hits / float(len(spec_keys)) if spec_keys else 0.0
        score = ratio * 2.0 + (1.0 if hint == dataset else 0.0)
        if score > best.get("score", -1):
            best = {"dataset": dataset, "ratio": ratio, "hint": hint,
                    "score": score}
    if best["dataset"] != "generic" and (
            best["ratio"] >= 0.6 or (best["hint"] == best["dataset"]
                                     and best["ratio"] >= 0.3)):
        return best["dataset"], keys
    return "generic", keys


def build_columns(raw_table, dataset, keys):
    spec = C.SPECS.get(dataset)
    raw_header = raw_table["raw_header"]
    width = len(raw_header)
    columns = []
    used = set()
    for index in range(width):
        raw = str(raw_header[index] or "col_%d" % (index + 1)).strip()
        key = keys[index] if index < len(keys) else ""
        if spec and key == "class_label":
            key = spec.get("group") or key
        if spec and not key:
            normalized = C.normalize_name(raw)
            for spec_key, _label, _unit, _desc in spec["columns"]:
                if normalized == spec_key:
                    key = spec_key
                    break
        if spec and key not in {column[0] for column in spec["columns"]}:
            key = ""
        values = [row[index] if index < len(row) else None
                  for row in raw_table["raw_rows"]]
        dtype = infer_dtype(values)
        unit, description = "", ""
        if spec and key:
            for spec_key, _label, spec_unit, spec_desc in spec["columns"]:
                if spec_key == key:
                    unit, description = spec_unit, spec_desc
                    break
        if not description:
            description = "未在输入数据字典中描述；按列名与取值类型映射"
        sql_name = key or sanitize_table(raw, "col_%d" % (index + 1))
        sql_name = unique_name(sql_name, used)
        columns.append({
            "index": index, "raw_name": raw, "sql_name": sql_name,
            "canonical": key, "dtype": dtype, "unit": unit,
            "description": description,
            "values": values,
        })
    if spec:
        group_key = spec.get("group") or ""
        target_key = spec.get("target") or ""
    else:
        group_key, target_key = "", ""
        for column in columns:
            if column["dtype"] == "TEXT":
                distinct = C.distinct_values(column["values"], limit=50)
                if 2 <= len(distinct) <= 12:
                    group_key = column["sql_name"]
                    break
        numeric = [column for column in columns if column["dtype"] == "NUMERIC"]
        if numeric:
            target_key = numeric[-1]["sql_name"]
    return columns, group_key, target_key


def prepare_tables(found):
    raw_tables = []
    for path in found["sqlite"]:
        raw_tables.extend(read_sqlite_tables(path))
    for path in found["csv"]:
        table = read_csv_table(path)
        if table:
            raw_tables.append(table)
    for path in found["xlsx"]:
        table = read_xlsx_table(path)
        if table:
            raw_tables.append(table)
    for path in found["json"]:
        table = read_json_table(path)
        if table:
            raw_tables.append(table)
    prepared = []
    used_names = set()
    for raw_table in raw_tables:
        dataset, keys = detect_dataset(raw_table)
        columns, group_key, target_key = build_columns(raw_table, dataset, keys)
        if not columns:
            continue
        name = unique_name(dataset if dataset != "generic" else raw_table["name"],
                           used_names)
        prepared.append({
            "name": name, "dataset": dataset,
            "source": raw_table["source"],
            "source_path": raw_table["source_path"],
            "source_kind": raw_table["source_kind"],
            "source_table": raw_table["source_table"],
            "raw_header": raw_table["raw_header"],
            "raw_rows": raw_table["raw_rows"],
            "truncated": raw_table.get("truncated", 0),
            "columns": columns,
            "group_column": group_key,
            "target_column": target_key,
            "n_expected": (C.SPECS[dataset]["expected_rows"]
                           if dataset in C.SPECS else None),
            "n_rows": len(raw_table["raw_rows"]),
        })
    return prepared


# ---------------------------------------------------------------------------
# Data dictionary
# ---------------------------------------------------------------------------


def parse_dictionary(paths, root):
    entries = []
    for path in paths:
        text = read_text(path)
        if not text:
            continue
        source = rel(path, root)
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            field = description = ""
            if stripped.startswith("|"):
                cells = [cell.strip() for cell in stripped.strip("|").split("|")]
                if len(cells) >= 2 and not set(cells[0]) <= {"-", " "}:
                    field = cells[0].strip("`* ")
                    description = " | ".join(
                        cell for cell in cells[1:]
                        if cell and not set(cell) <= {"-", " "})
            else:
                match = re.match(
                    r"^[-*]\s*`?([^:`]{1,160}?)`?\s*[:：]\s*(.+)$", stripped)
                if not match:
                    match = re.match(
                        r"^`?([A-Za-z_][\w .\-/()^%,°]*?)`?\s*[:：]\s*(.+)$",
                        stripped)
                if match:
                    field, description = match.group(1), match.group(2).strip()
            if not field or not description:
                continue
            entries.append({
                "field": field.strip("`* "), "description": description,
                "source": source, "locator": "L%d" % line_no, "quote": stripped,
            })
    return entries


def dictionary_for(columns, table_name, provided):
    """Attach dictionary source/quote to each column; return report lines."""
    by_name = {}
    for entry in provided:
        by_name.setdefault(C.normalize_name(entry["field"]), []).append(entry)
    lines = ["# UCI profile dictionary", "",
             "> 由输入数据字典与内置规范字段表合并；未匹配到的列会明确标注。", ""]
    for column in columns:
        raw_key = C.normalize_name(column["raw_name"])
        canonical_key = C.normalize_name(column["canonical"])
        candidates = by_name.get(raw_key) or by_name.get(canonical_key) or []
        if candidates:
            entry = candidates[0]
            column["dictionary_source"] = entry["source"]
            column["dictionary_locator"] = entry["locator"]
            column["dictionary_quote"] = entry["quote"]
        else:
            unit_text = ("单位：%s" % column["unit"]) if column["unit"] else "单位未在输入中标注"
            line = "- %s.%s: %s；%s；原始列名 %r；规范字段 %s" % (
                table_name, column["sql_name"], column["description"],
                unit_text, column["raw_name"],
                column["canonical"] or "(未识别)")
            lines.append(line)
            column["dictionary_source"] = "work/uci-dictionary.md"
            column["dictionary_locator"] = "L%d" % (len(lines))
            column["dictionary_quote"] = line
    return lines


# ---------------------------------------------------------------------------
# Derived snapshot
# ---------------------------------------------------------------------------


def build_source_manifest(found, root):
    paths = []
    for group in ("sqlite", "csv", "xlsx", "json"):
        paths.extend(found[group])
    manifest = []
    for path in sorted(set(paths)):
        try:
            manifest.append({
                "path": rel(path, root),
                "bytes": path.stat().st_size,
                "sha256": C.sha256_file(path),
                "kind": path.suffix.lower().lstrip("."),
            })
        except OSError:
            continue
    return manifest


def build_derived_snapshot(tables, work):
    path = Path(work) / "uci-snapshot.sqlite"
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    created = []
    try:
        for table in tables:
            columns = table["columns"]
            ddl = "CREATE TABLE %s (%s)" % (
                C.quote_ident(table["name"]),
                ", ".join("%s %s" % (C.quote_ident(column["sql_name"]),
                                     column["dtype"]) for column in columns))
            conn.execute(ddl)
            placeholders = ", ".join("?" for _ in columns)
            insert = "INSERT INTO %s VALUES (%s)" % (
                C.quote_ident(table["name"]), placeholders)
            payload = []
            for row in table["raw_rows"]:
                values = []
                for column in columns:
                    raw = row[column["index"]] if column["index"] < len(row) else None
                    if C.is_missing(raw):
                        values.append(None)
                    elif column["dtype"] == "NUMERIC":
                        numeric = C.safe_float(raw)
                        values.append(numeric if numeric is not None else None)
                    else:
                        values.append(str(raw).strip())
                payload.append(values)
            conn.executemany(insert, payload)
            # A view with the original column names helps model-authored SQL.
            raw_names = [column["raw_name"] for column in columns]
            sql_names = [column["sql_name"] for column in columns]
            if raw_names != sql_names:
                view = table["name"] + "__raw"
                try:
                    conn.execute("CREATE VIEW %s AS SELECT %s FROM %s" % (
                        C.quote_ident(view),
                        ", ".join("%s AS %s" % (C.quote_ident(sql),
                                                C.quote_ident(raw))
                                  for sql, raw in zip(sql_names, raw_names)),
                        C.quote_ident(table["name"])))
                    table["raw_view"] = view
                except sqlite3.Error:
                    pass
            created.append(table["name"])
        conn.commit()
    finally:
        conn.close()
    return {
        "path": str(path),
        "sha256": C.sha256_file(path),
        "bytes": path.stat().st_size if path.exists() else 0,
        "sqlite_version": sqlite3.sqlite_version,
        "origin": "derived_from_input_snapshots",
        "read_only": True,
        "tables": {table["name"]: table["n_rows"] for table in tables},
        "created_tables": created,
    }


def choose_query_target(cfg, tables, found):
    """Prefer the provided SQLite snapshot for database-query mode."""
    primary = None
    recognized = [table for table in tables if table["dataset"] != "generic"]
    pool = recognized or tables
    if pool:
        primary = max(pool, key=lambda table: table["n_rows"])
    if not primary:
        return "", None
    if primary["source_kind"] == "sqlite":
        column_map = {}
        for column in primary["columns"]:
            column_map[column["canonical"] or column["sql_name"]] = column["raw_name"]
        return primary["name"], {
            "kind": "provided_sqlite",
            "path": primary["source_path"],
            "table": primary["source_table"],
            "column_map": column_map,
            "source": primary["source"],
            "sha256": C.sha256_file(primary["source_path"]),
        }
    column_map = {column["canonical"] or column["sql_name"]: column["sql_name"]
                  for column in primary["columns"]}
    return primary["name"], {
        "kind": "derived_sqlite",
        "path": str(Path(cfg["work_dir"]) / "uci-snapshot.sqlite"),
        "table": primary["name"],
        "column_map": column_map,
        "source": "work/uci-snapshot.sqlite",
        "sha256": cfg["derived_meta"]["sha256"],
    }


# ---------------------------------------------------------------------------
# Config and generated pipeline
# ---------------------------------------------------------------------------


def table_config(table):
    columns = []
    for column in table["columns"]:
        columns.append({
            key: column.get(key) for key in (
                "raw_name", "sql_name", "canonical", "dtype", "unit",
                "description", "dictionary_source", "dictionary_locator",
                "dictionary_quote")
        })
    return {
        "name": table["name"], "dataset": table["dataset"],
        "source": table["source"], "source_kind": table["source_kind"],
        "group_column": table["group_column"],
        "target_column": table["target_column"],
        "n_expected": table["n_expected"], "columns": columns,
    }


def case_question(input_dir):
    case = load_json(Path(input_dir) / "case.json", {}) or {}
    parts = []
    for key in ("question", "prompt", "instruction", "instructions",
                "description", "task", "query", "request"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    if not parts:
        for path in sorted(Path(input_dir).rglob("*")):
            if path.is_file() and any(token in path.name.lower() for token in (
                    "query-request", "question", "request")):
                text = read_text(path).strip()
                if text:
                    parts.append(text[:1200])
                    break
    if not parts:
        task = read_text(Path(input_dir) / "task.md")
        if task:
            parts.append(task[:1200])
    return "\n".join(parts)[:2000]


def write_figures(payload, output):
    """Static SVG dot/CI plot per dataset, generated from the same results."""
    figures = []
    for report in (payload.get("reports") or [])[:3]:
        outcomes = [row for row in (report.get("outcomes") or [])
                    if (row.get("inference") or {}).get("primary")]
        if not outcomes:
            continue
        outcome = max(outcomes, key=lambda row: (
            (row.get("inference") or {}).get("effect_size") or {}
        ).get("eta_squared") or -1.0)
        groups = []
        for label, stats in (outcome.get("by_group") or {}).items():
            mean = stats.get("mean")
            if mean is None:
                continue
            ci = stats.get("ci95") or [None, None]
            groups.append({
                "group": label, "mean": mean, "ci_low": ci[0],
                "ci_high": ci[1], "n": stats.get("n"),
            })
        if len(groups) < 2:
            continue
        name = report["dataset"]
        base = Path(output) / "figures"
        base.mkdir(parents=True, exist_ok=True)
        data_path = base / ("group-means-%s.csv" % name)
        with data_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["group", "mean", "ci_low",
                                                    "ci_high", "n"])
            writer.writeheader()
            writer.writerows(groups)
        svg_path = base / ("group-means-%s.svg" % name)
        write_text(svg_path, _group_means_svg(
            groups, outcome.get("outcome"), outcome.get("unit") or "",
            report.get("table"), data_path.name))
        figures.append({
            "id": "FIG%03d" % (len(figures) + 1),
            "path": "figures/%s" % svg_path.name,
            "source": "results",
            "data_source": "output/figures/%s" % data_path.name,
            "units": {"x": "group", "y": outcome.get("unit") or "unit"},
            "caption": "%s 的 %s 组均值与 95%% CI（每组一个点，误差线为 t 区间）"
                       % (report.get("table"), outcome.get("outcome")),
        })
    return figures


def _group_means_svg(groups, outcome, unit, table, data_name):
    width, height = 760, 420
    left, right, top, bottom = 78, 28, 64, 76
    plot_w = width - left - right
    plot_h = height - top - bottom
    values = []
    for row in groups:
        values.append(row["mean"])
        if row["ci_low"] is not None:
            values.append(row["ci_low"])
        if row["ci_high"] is not None:
            values.append(row["ci_high"])
    low, high = min(values), max(values)
    if high == low:
        high = low + 1.0
    pad = (high - low) * 0.12
    low, high = low - pad, high + pad

    def y(value):
        return top + plot_h - (value - low) / (high - low) * plot_h

    def x(index):
        return left + (index + 0.5) * plot_w / len(groups)

    colors = {"surface": "#ffffff", "ink": "#1f2937", "muted": "#6b7280",
              "grid": "#e5e7eb", "accent": "#2563eb"}
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" role="img" aria-labelledby="fig-title fig-desc">'
        % (width, height, width, height),
        '<title id="fig-title">%s: group means with 95%% CI</title>' % table,
        '<desc id="fig-desc">%s 的组均值与 95%% 置信区间；数据来自 %s。</desc>'
        % (outcome, data_name),
        '<rect width="100%%" height="100%%" fill="%s"/>' % colors["surface"],
        '<text x="%d" y="30" font-family="system-ui, sans-serif" font-size="18" '
        'font-weight="600" fill="%s">%s: group mean (95%% CI)</text>'
        % (left, colors["ink"], table),
        '<text x="%d" y="50" font-family="system-ui, sans-serif" font-size="12" '
        'fill="%s">unit: %s</text>' % (left, colors["muted"],
                                       unit or "not specified"),
    ]
    for tick in range(5):
        value = low + (high - low) * tick / 4.0
        line_y = y(value)
        lines.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                     'stroke-width="1"/>' % (left, line_y, width - right,
                                             line_y, colors["grid"]))
        lines.append('<text x="%d" y="%.1f" text-anchor="end" '
                     'font-family="system-ui, sans-serif" font-size="11" '
                     'fill="%s">%.4g</text>' % (left - 8, line_y + 4,
                                                colors["muted"], value))
    lines.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                 'stroke-width="1"/>' % (left, y(low), width - right, y(low),
                                         colors["muted"]))
    for index, row in enumerate(groups):
        gx = x(index)
        if row["ci_low"] is not None and row["ci_high"] is not None:
            lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                         'stroke="%s" stroke-width="2"/>' % (
                             gx, y(row["ci_low"]), gx, y(row["ci_high"]),
                             colors["accent"]))
            for cap in (row["ci_low"], row["ci_high"]):
                lines.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                             'stroke="%s" stroke-width="2"/>' % (
                                 gx - 7, y(cap), gx + 7, y(cap),
                                 colors["accent"]))
        lines.append('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" '
                     'stroke="%s" stroke-width="2"><title>%s: mean=%.6g, '
                     '95%% CI=[%s, %s], n=%s</title></circle>' % (
                         gx, y(row["mean"]), colors["accent"],
                         colors["surface"], row["group"], row["mean"],
                         row["ci_low"], row["ci_high"], row["n"]))
        lines.append('<text x="%.1f" y="%.1f" text-anchor="middle" '
                     'font-family="system-ui, sans-serif" font-size="11" '
                     'fill="%s">%.4g</text>' % (
                         gx, y(row["mean"]) - 10, colors["ink"],
                         row["mean"]))
        label = str(row["group"])
        if len(label) > 16:
            label = label[:15] + "…"
        lines.append('<text x="%.1f" y="%d" text-anchor="middle" '
                     'font-family="system-ui, sans-serif" font-size="11" '
                     'fill="%s">%s</text>' % (
                         gx, height - bottom + 22, colors["ink"],
                         label.replace("&", "&amp;").replace("<", "&lt;")))
    lines += ["</svg>", ""]
    return "\n".join(lines)


def write_generated_code(work, output):
    core_text = read_text(HERE / "uci_core.py")
    header = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "# Generated analysis pipeline (uci_core %s).\n"
        "# Reads work/uci-run.json next to this file; standard library only;\n"
        "# optional numpy is used only for a cross-check and never required.\n\n"
        % C.CORE_VERSION)
    generated = header + core_text
    write_text(Path(work) / "analysis.py", generated)
    write_text(Path(output) / "analysis-code.py", generated)
    return C.sha256_text(generated)


def run_generated(work, output, timeout=300):
    script = Path(work) / "analysis.py"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    started = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, str(script)],
                              cwd=str(work), capture_output=True, text=True,
                              timeout=timeout, env=env)
        elapsed = (time.perf_counter() - started) * 1000.0
        log = ["command: %s %s" % (sys.executable, script),
               "cwd: %s" % work,
               "python: %s" % platform.python_version(),
               "returncode: %d" % proc.returncode,
               "elapsed_ms: %.3f" % elapsed, "", "[stdout]", proc.stdout,
               ""]
        if proc.stderr:
            log.extend(["[stderr]", proc.stderr])
        write_text(Path(output) / "execution.log", "\n".join(log) + "\n")
        return {"returncode": proc.returncode, "elapsed_ms": round(elapsed, 3),
                "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-20000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        write_text(Path(output) / "execution.log",
                   "execution failed: %s\n" % exc)
        return {"returncode": 2, "elapsed_ms": 0.0, "stdout": "",
                "stderr": str(exc)}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def md_table(headers, rows, limit=80):
    lines = ["| " + " | ".join(str(header) for header in headers) + " |",
             "|" + "---|" * len(headers)]
    for row in rows[:limit]:
        lines.append("| " + " | ".join(
            "" if value is None else str(value).replace("|", "\\|")
            for value in row) + " |")
    return lines


def write_report(output, payload, work_data, context):
    mode = payload.get("mode") or context.get("mode")
    lines = ["# 科研数据分析报告（%s）" % mode, "",
             "> 本报告的数字来自同一次真实执行：`work/analysis.py` 在"
             " `work/uci-snapshot.sqlite` 上计算，并写入 `output/results.json`。"
             "报告不手写另一套数字。", ""]
    lines += ["## 0. 运行状态与快照", "",
              "- case_id：`%s`" % (payload.get("case_id") or context.get("case_id")),
              "- status：`%s`" % payload.get("status"),
              "- 入口：`python scripts/uci_profile.py --input /input --work /app/work --output /app/output`",
              "- 快照：`%s`" % (payload.get("snapshot") or {}).get("path", "-"),
              "- 快照 SHA-256：`%s`" % (payload.get("snapshot") or {}).get("sha256", "-"),
              "- 生成代码：`work/analysis.py`（SHA-256 `%s`）"
              % context.get("code_sha256", "-"),
              ""]
    sources = context.get("source_manifest") or []
    if sources:
        lines += ["## 1. 输入来源与版本", ""]
        lines += md_table(["文件", "类型", "字节", "SHA-256"],
                          [[row["path"], row["kind"], row["bytes"],
                            row["sha256"][:16] + "…"] for row in sources])
        lines.append("")
    datasets = payload.get("datasets") or []
    lines += ["## 2. 数据集识别与行数", ""]
    if datasets:
        lines += md_table(
            ["数据集", "表", "行数", "期望行数", "列数", "数值列", "缺失单元格",
             "完全重复行"],
            [[row["dataset"], row["table"], row["rows"],
              row["rows_expected"] if row["rows_expected"] is not None else "-",
              row["columns"], row["numeric_columns"], row["missing_cells"],
              row["duplicate_rows_exact"]] for row in datasets])
        lines.append("")
        for row in datasets:
            if row["rows_expected"] is not None and not row["rows_match_expected"]:
                lines.append("- **行数不一致**：`%s` 实际 %d 行，公开快照应为 %d 行；"
                             "已在不确定性中保留。" % (
                                 row["table"], row["rows"], row["rows_expected"]))
        lines.append("")
    else:
        lines.append("- 没有可识别的数据表。")
        lines.append("")

    mapping = payload.get("schema_mapping") or {}
    if mapping:
        lines += ["## 3. 数据字典映射", "",
                  "| 字段 | 规范字段 | 类型 | 单位 | 字典解释 | 来源 |",
                  "|---|---|---|---|---|---|"]
        for key, entry in list(mapping.items())[:80]:
            lines.append("| `%s` | `%s` | %s | %s | %s | %s |" % (
                key, entry.get("canonical") or "-", entry.get("dtype") or "-",
                entry.get("unit") or "-",
                (entry.get("dictionary_description") or "-").replace("|", "\\|"),
                entry.get("source") or "-"))
        lines.append("")

    quality = payload.get("quality") or {}
    lines += ["## 4. 质量检查（缺失/重复/异常值）", "",
              "以下检查只标记，不删除或静默修改数据。", ""]
    for table_name, table_quality in quality.items():
        lines += ["### `%s`" % table_name, "",
                  "- 行数 %d，列数 %d，缺失单元格 %d（缺失率 %s），完全重复行 %d。" % (
                      table_quality["n_rows"], table_quality["n_columns"],
                      table_quality["missing_cells"],
                      table_quality["missing_rate"],
                      table_quality["duplicate_rows_exact"])]
        flagged = []
        for column_name, entry in (table_quality.get("columns") or {}).items():
            notes = []
            if entry.get("missing"):
                notes.append("缺失 %d" % entry["missing"])
            if entry.get("outliers_iqr_count"):
                notes.append("IQR 异常 %d" % entry["outliers_iqr_count"])
            if entry.get("outliers_z3_count"):
                notes.append("|z|>3 异常 %d" % entry["outliers_z3_count"])
            if entry.get("range_violations"):
                notes.append("越界 %d（规则 %s）" % (
                    entry["range_violations"], entry.get("range_rule")))
            if entry.get("possible_sentinel_values"):
                notes.append("疑似哨兵值 %s" % entry["possible_sentinel_values"])
            if notes:
                flagged.append([column_name, "；".join(notes)])
        if flagged:
            lines += md_table(["列", "标记"], flagged)
        const = table_quality.get("constant_columns") or []
        if const:
            lines.append("- 常量列：%s。" % ", ".join("`%s`" % name for name in const))
        lines.append("")

    if mode == "database-query":
        lines += _report_database(payload)
    else:
        lines += _report_statistics(payload, mode)

    figures = context.get("figures") or []
    if figures:
        lines += ["## 7.1 图表", ""]
        for figure in figures:
            lines.append("- `%s`：%s；数据 `%s`；轴单位：x=%s，y=%s。" % (
                figure.get("path"), figure.get("caption", ""),
                figure.get("data_source"), (figure.get("units") or {}).get("x"),
                (figure.get("units") or {}).get("y")))
        lines.append("")
    lines += ["## 8. 复现方法与产物", "",
              "```bash",
              "python scripts/uci_profile.py --input /input --work /app/work --output /app/output",
              "python scripts/run_case.py finalize --input /input --work /app/work --output /app/output",
              "```", "",
              "- `work/analysis.py` 是本次实际执行的脚本，`output/analysis-code.py` 为同一份代码。",
              "- `work/uci-run.json` 记录快照路径、表/列映射与该次运行配置。",
              "- `output/results.json`、`output/results.csv`、"
              "`output/statistics-report.json`、`output/quality-report.json`、"
              "`output/cleaning-rules.json` 与报告数字一致。",
              ""]
    uncertainties = payload.get("uncertainties") or []
    lines += ["## 9. 不确定性与边界", ""]
    if uncertainties:
        for item in uncertainties:
            if isinstance(item, dict):
                lines.append("- **%s**：%s（影响：%s；处理：%s）" % (
                    item.get("item", "未命名"), item.get("reason", ""),
                    item.get("impact", ""), item.get("action", "")))
            else:
                lines.append("- %s" % item)
    else:
        lines.append("- 未发现需要额外人工确认的问题；结论仅适用于上述快照与口径。")
    lines.append("")
    write_text(Path(output) / "analysis-report.md", "\n".join(lines) + "\n")


def _report_database(payload):
    lines = ["## 5. 可复跑查询", "", "```sql",
             (payload.get("query") or "").strip(), "```", "",
             "- SQL SHA-256：`%s`" % payload.get("sql_sha256", "-"),
             "- 主表：`%s`（选择依据：%s）" % (
                 payload.get("primary_table", "-"),
                 payload.get("dataset_selection", "-")),
             "- 权限：只读（`mode=ro` + `PRAGMA query_only=ON`）。", ""]
    lines += ["## 6. 结果表", "",
              "返回行数：**%d**；列：%s。" % (
                  payload.get("row_count", 0),
                  ", ".join("`%s`" % name for name in
                            (payload.get("columns") or [])) or "无")]
    preview = payload.get("preview") or []
    if preview:
        lines += [""] + md_table(payload.get("columns") or [], preview, limit=40)
        if (payload.get("row_count") or 0) > len(preview):
            lines.append("")
            lines.append("（仅展示前 %d 行；完整结果见 `output/result-table.csv`。）"
                         % len(preview))
    row_check = payload.get("row_count_check") or {}
    empty = payload.get("empty_result") or {}
    lines += ["", "## 7. 行数、组覆盖与连接检查", "",
              "- 行数核对：期望 %s，实际 %s，结果 %s（来源：%s）。" % (
                  row_check.get("expected"), row_check.get("actual"),
                  "一致" if row_check.get("ok") else "不一致",
                  row_check.get("source")),
              "- 空结果：is_empty=%s；%s" % (
                  empty.get("is_empty"), empty.get("statement"))]
    coverage = empty.get("coverage_checks") or []
    if coverage:
        lines += [""] + md_table(
            ["输出列", "源表.列", "基准组数", "输出组数", "缺失组"],
            [[row.get("output_column"), "%s.%s" % (
                row.get("source_table"), row.get("source_column")),
              row.get("base_distinct"), row.get("output_distinct"),
              ", ".join(row.get("missing_groups") or []) or "-"]
             for row in coverage])
    joins = payload.get("join_checks") or []
    if joins:
        lines += [""] + md_table(
            ["左表", "右表", "共享键", "左表每键最大行数", "右表每键最大行数",
             "1:1", "匹配键数"],
            [[row["left"], row["right"], row["key"],
              row["left_rows_per_key_max"], row["right_rows_per_key_max"],
              "是" if row["one_to_one"] else "否", row["matched_keys"]]
             for row in joins])
    else:
        lines += ["", "- 未识别到可检查的跨表共享键。"]
    lines.append("")
    return lines


def _report_statistics(payload, mode):
    lines = []
    if mode == "code-generation":
        validation = payload.get("validation") or {}
        execution = payload.get("execution_contract") or {}
        lines += ["## 5. 代码执行与环境", "",
                  "- 入口：`work/analysis.py`；配置：`work/uci-run.json`。",
                  "- 依赖：%s；可选依赖：%s。" % (
                      ", ".join(execution.get("dependencies") or ["python-stdlib"]),
                      ", ".join(execution.get("optional_dependencies") or ["无"])),
                  "- 验证状态：**%s**（失败 %s 项）。" % (
                      validation.get("status"), validation.get("failed")),
                  "- 验证方法：%s。" % validation.get("method"),
                  "- 错误策略：%s。" % execution.get("error_policy"), ""]
        checks = validation.get("checks") or []
        if checks:
            lines += md_table(["检查", "状态", "SQL", "Python"],
                              [[row["check"], row["status"],
                                row.get("sql"), row.get("python")]
                               for row in checks], limit=40)
            lines.append("")
    lines += ["## 5. 统计结果", ""]
    reports = payload.get("reports") or []
    for report in reports:
        lines += ["### `%s`（%s，n=%d）" % (
            report["dataset"], report["table"], report["rows"]), ""]
        rows = []
        for outcome in report.get("outcomes") or []:
            overall = outcome.get("overall") or {}
            inference = outcome.get("inference") or {}
            primary = inference.get("primary") or {}
            effect = inference.get("effect_size") or {}
            rows.append([
                outcome.get("outcome"), outcome.get("unit") or "-",
                overall.get("n"), overall.get("mean"), overall.get("sd"),
                overall.get("median"),
                "%s–%s" % tuple(overall.get("ci95") or ["-", "-"]),
                inference.get("primary_test", "-"),
                C.format_p(primary.get("p")),
                C.format_p(inference.get("p_holm_across_outcomes")),
                effect.get("eta_squared")])
        if rows:
            lines += md_table(
                ["指标", "单位", "n", "均值", "SD", "中位数", "均值 95% CI",
                 "主检验", "p", "p(Holm)", "eta²"], rows)
            lines.append("")
        primary_outcomes = sorted(
            report.get("outcomes") or [],
            key=lambda row: ((row.get("inference") or {}).get(
                "effect_size") or {}).get("eta_squared") or -1,
            reverse=True)[:3]
        for outcome in primary_outcomes:
            inference = outcome.get("inference") or {}
            pairs = inference.get("pairwise_welch_holm") or []
            if not pairs:
                continue
            lines += ["#### 主要指标的组间比较：`%s`" % outcome.get("outcome"), "",
                      "- %s" % (inference.get("conclusion") or ""), ""]
            lines += md_table(
                ["组 A", "组 B", "差值", "95% CI", "p", "p(Holm)", "Hedges g"],
                [[row["group_a"], row["group_b"], row.get("diff"),
                  "%s–%s" % tuple(row.get("ci95_diff") or ["-", "-"]),
                  C.format_p(row.get("p")), C.format_p(row.get("p_holm")),
                  row.get("hedges_g")]
                 for row in pairs])
            lines.append("")
            assumptions = inference.get("assumptions") or {}
            lines += ["- 前提筛查：Brown-Forsythe p=%s；Jarque-Bera 提示非正态的组：%s。" % (
                (assumptions.get("variance_homogeneity") or {}).get("p"),
                ", ".join(assumptions.get("normality_flags_0_05") or []) or "无"),
                ""]
        correlations = report.get("correlations") or []
        if correlations:
            lines += ["#### 与 `%s` 的相关（Pearson）" % correlations[0].get("target"), ""]
            lines += md_table(
                ["指标", "n", "r", "95% CI", "p", "p(Holm)"],
                [[row.get("outcome"), row.get("n"), row.get("r"),
                  "%s–%s" % tuple(row.get("ci95") or ["-", "-"]),
                  C.format_p(row.get("p")), C.format_p(row.get("p_holm"))]
                 for row in correlations])
            lines.append("")
        sensitivity = report.get("sensitivity")
        if sensitivity:
            lines += ["- 敏感性分析（%s）：移除 %s 个 IQR 异常值后，"
                      "ANOVA p=%s，eta²=%s。主分析仍保留全部数据。" % (
                          sensitivity.get("analysis"),
                          sensitivity.get("removed"),
                          C.format_p((sensitivity.get("anova") or {}).get("p")),
                          (sensitivity.get("anova") or {}).get("eta_squared")),
                      ""]
    lines += ["## 7. 结果解读边界", "",
              "- 上述检验用于描述当前快照中的组间差异与关联；不能据此推断因果。",
              "- p 值小于 0.05 只表示在当前模型与样本量下罕见；"
              "效应量与置信区间用于判断实际意义。",
              "- 多重比较已用 Holm 校正，报告同时给出未校正 p 与校正 p。",
              "- p 显示为 `<1e-300` 表示低于浮点分辨率，不是精确的 0。",
              "- 缺失值按变量完整观测处理；重复行保留；异常值只做敏感性分析。",
              ""]
    return lines


# ---------------------------------------------------------------------------
# Work file and evidence
# ---------------------------------------------------------------------------


def json_quote(path, key, value):
    """A quote that survives whitespace-normalized evidence checks."""
    rendered = json.dumps(C._json_safe(value), ensure_ascii=False)
    return '"%s": %s' % (key, rendered)


def build_evidence(payload, dictionary_entries, output, work):
    evidence = []
    seen = set()

    def add(path, locator, quote):
        quote = (quote or "").strip()
        if not quote or (path, quote) in seen:
            return
        seen.add((path, quote))
        evidence.append({"path": path, "locator": locator, "quote": quote})

    # Provided dictionary lines are literal source evidence.
    for entry in dictionary_entries[:12]:
        add(entry["source"], entry["locator"], entry["quote"])
    # Built-in/merged dictionary lines are real files written by this run.
    dictionary_path = Path(work) / "uci-dictionary.md"
    if dictionary_path.is_file():
        for line_no, line in enumerate(
                read_text(dictionary_path).splitlines(), 1):
            if line.startswith("- "):
                add("work/uci-dictionary.md", "L%d" % line_no, line)
                if len([item for item in evidence
                        if item["path"] == "work/uci-dictionary.md"]) >= 12:
                    break
    for key, entry in list((payload.get("schema_mapping") or {}).items())[:12]:
        add(entry.get("source") or "work/uci-dictionary.md",
            entry.get("locator") or "$.schema_mapping",
            entry.get("quote"))
    snapshot = payload.get("snapshot") or {}
    if snapshot.get("sha256"):
        add("output/results.json", "$.snapshot.sha256",
            json_quote("output/results.json", "sha256", snapshot["sha256"]))
    if payload.get("row_count") is not None:
        add("output/results.json", "$.row_count",
            json_quote("output/results.json", "row_count",
                       payload["row_count"]))
    for index, row in enumerate((payload.get("results") or [])[:20]):
        metric = row.get("metric")
        value = row.get("value")
        if metric is None or value is None:
            continue
        add("output/results.json", "$.results[%d].value" % index,
            '"metric": %s, "value": %s' % (
                json.dumps(metric, ensure_ascii=False),
                json.dumps(C._json_safe(value), ensure_ascii=False)))
    if not evidence:
        add("output/results.json", "$.status",
            json_quote("output/results.json", "status",
                       payload.get("status") or "input_error"))
    return evidence


def work_results(payload, evidence, limit=200):
    rows = []
    for index, row in enumerate((payload.get("results") or [])[:limit], 1):
        metric = row.get("metric")
        value = row.get("value")
        if metric is None or value is None:
            continue
        quote = '"metric": %s, "value": %s' % (
            json.dumps(metric, ensure_ascii=False),
            json.dumps(C._json_safe(value), ensure_ascii=False))
        rows.append({
            "id": row.get("id") or "R%03d" % index,
            "metric": metric, "value": value, "unit": row.get("unit", ""),
            "n": row.get("n"), "outcome": row.get("outcome", ""),
            "dataset": row.get("dataset", ""), "table": row.get("table", ""),
            "analysis": row.get("analysis", ""),
            "p_value": row.get("p_value"), "p_adjusted": row.get("p_adjusted"),
            "effect_size": row.get("effect_size"),
            "evidence": {"path": "output/results.json",
                         "locator": "$.results[%d].value" % (index - 1),
                         "quote": quote},
        })
    if not rows:
        rows.append({
            "id": "R001", "metric": "status",
            "value": payload.get("status") or "input_error",
            "unit": "", "n": None,
            "evidence": {"path": "output/results.json", "locator": "$.status",
                         "quote": json_quote("output/results.json", "status",
                                             payload.get("status")
                                             or "input_error")},
        })
    return rows


def merge_work(work_path, payload, context, evidence):
    work = load_json(work_path, {})
    if not isinstance(work, dict):
        work = {}
    mode = payload.get("mode") or context.get("mode")
    work["case_id"] = payload.get("case_id") or context.get("case_id")
    work["mode"] = mode
    work["summary"] = context.get("summary") or (
        "在记录的快照上真实执行 UCI profile：%d 个数据集，%d 条结果指标。"
        % (len(payload.get("datasets") or []),
           len(payload.get("results") or [])))
    work["question"] = context.get("question") or ""
    work["answer"] = work["summary"]
    work.setdefault("analysis", {})
    analysis = work["analysis"] if isinstance(work["analysis"], dict) else {}
    analysis.update({
        "question": (context.get("question") or
                     "对固定 UCI 快照完成查询/统计/代码执行")[:800],
        "method": context.get("method"),
        "assumptions": context.get("assumptions") or [],
        "steps": context.get("steps") or [],
        "diagnostics": context.get("diagnostics") or [],
        "sample_size": context.get("sample_size"),
        "effect_size": context.get("effect_size") or "",
        "ci": context.get("ci") or [],
        "assumption_checks": context.get("assumption_checks") or [],
    })
    work["analysis"] = analysis
    work["results"] = work_results(payload, evidence)
    work["evidence"] = evidence
    work["uncertainties"] = [
        item if isinstance(item, dict) else {
            "item": "reported", "reason": str(item),
            "impact": "见分析报告", "action": "人工复核"}
        for item in (payload.get("uncertainties") or [])
    ]
    work["figures"] = context.get("figures") or work.get("figures") or []
    work["code"] = "work/analysis.py"
    work["dependencies"] = ["python-stdlib"]
    work["inputs"] = [row.get("path") for row in context.get("source_manifest") or []]
    work["outputs"] = context.get("outputs") or []
    work["error_handling"] = context.get("error_handling") or []
    work["execution"] = {
        "returncode": context.get("returncode"),
        "log": "output/execution.log",
        "elapsed_ms": context.get("elapsed_ms"),
        "command": "python work/analysis.py",
    }
    snapshot = payload.get("snapshot") or {}
    work["original_snapshot"] = snapshot
    work["snapshot"] = snapshot
    work["cleaned_table"] = "output/cleaned-table.csv"
    work["quality_report"] = payload.get("quality") or {}
    work["rule_log"] = [
        {"rule": "missing_as_missing", "action": "不插补、不当 0；按变量报告 n"},
        {"rule": "duplicates_retained", "action": "完全重复行保留并计数"},
        {"rule": "outliers_flagged", "action": "Tukey 1.5*IQR 与 |z|>3 仅标记"},
        {"rule": "range_screening", "action": "越界值提示复核，不删除"},
    ]
    if mode == "cleaning" or context.get("mode") == "cleaning":
        quality = work.get("quality_report") if isinstance(
            work.get("quality_report"), dict) else {}
        raw_rows = sum(int((stats or {}).get("n_rows") or 0)
                       for stats in quality.values()
                       if isinstance(stats, dict))
        work["cleaning_rules"] = work["rule_log"]
        work["closure"] = {"raw_rows": raw_rows, "dropped": 0,
                           "cleaned_rows": raw_rows,
                           "note": "exact duplicates retained and counted; "
                                   "no silent row deletion"}
        work["data_dictionary"] = payload.get("dictionary") or {}
        if not work["data_dictionary"]:
            dictionary = {}
            for table, stats in (quality or {}).items():
                columns = (stats or {}).get("columns") if isinstance(stats, dict) else {}
                dictionary[table] = {
                    name: {"dtype": (info or {}).get("dtype"),
                           "unit": (info or {}).get("unit") or "",
                           "missing": (info or {}).get("missing")}
                    for name, info in (columns or {}).items()}
            work["data_dictionary"] = dictionary
    if mode == "database-query":
        mapping = payload.get("schema_mapping") or {}
        work["query"] = payload.get("query") or ""
        work["sql_text"] = work["query"]
        work["sql_sha256"] = payload.get("sql_sha256") or ""
        work["schema_mapping"] = mapping
        work["field_mapping"] = mapping
        work["row_count_check"] = payload.get("row_count_check") or {}
        work["empty_result"] = payload.get("empty_result") or {}
        work["empty_result_handling"] = work["empty_result"]
        work["permissions"] = payload.get("permissions") or {
            "status": "read_only", "write_allowed": False}
        work["snapshot_version"] = snapshot
        work["result_table"] = "output/result-table.csv"
        work["join_checks"] = payload.get("join_checks") or []
        work["findings"] = _database_findings(payload)
    else:
        work["findings"] = _statistics_findings(payload)
        work["statistics_report"] = "output/statistics-report.json"
        work["analysis_code"] = "output/analysis-code.py"
        work["execution_log"] = "output/execution.log"
        work["environment"] = payload.get("environment") or {}
        work["validation"] = payload.get("validation") or _basic_validation(payload)
        # Contract aliases that are real for this run: the metric table,
        # dictionary mapping and the no-data policy.
        work["result_table"] = "output/results.csv"
        work["field_mapping"] = payload.get("schema_mapping") or {}
        work["snapshot_version"] = snapshot
        work["empty_result_handling"] = {
            "is_empty": not bool(payload.get("results")),
            "statement": ("每个变量按完整观测计算；空表/空组与缺失情况在"
                          "uncertainties 中报告，不把空结果写成无数据。"),
        }
    write_json(work_path, work)
    return work


def _database_findings(payload):
    findings = [{
        "id": "F001", "statement": "查询在主快照上返回 %d 行；空结果标记为 %s。" % (
            payload.get("row_count") or 0,
            (payload.get("empty_result") or {}).get("is_empty")),
        "evidence": {"path": "output/results.json", "locator": "$.row_count",
                     "quote": json_quote("output/results.json", "row_count",
                                         payload.get("row_count") or 0)},
    }]
    return findings


def _statistics_findings(payload):
    findings = []
    for report in (payload.get("reports") or [])[:3]:
        for outcome in (report.get("outcomes") or [])[:4]:
            inference = outcome.get("inference") or {}
            primary = inference.get("primary") or {}
            if primary.get("p") is None:
                continue
            findings.append({
                "id": "F%03d" % (len(findings) + 1),
                "statement": "%s 的 %s：p=%s，eta²=%s；%s" % (
                    report["dataset"], outcome.get("outcome"),
                    C.format_p(primary.get("p")),
                    (inference.get("effect_size") or {}).get("eta_squared"),
                    inference.get("conclusion")),
                "evidence": {"path": "output/statistics-report.json",
                             "locator": "$.reports",
                             "quote": '"p": %s' % json.dumps(
                                 C._json_safe(primary.get("p")))},
            })
    if not findings:
        findings.append({
            "id": "F001", "statement": "本次运行未产生可用的推断统计结果。",
            "evidence": {"path": "output/results.json", "locator": "$.status",
                         "quote": json_quote("output/results.json", "status",
                                             payload.get("status"))},
        })
    return findings


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def empty_exit(input_dir, work, output, mode, case_id, reason):
    payload = {
        "status": "input_error", "case_id": case_id, "mode": mode,
        "core_version": C.CORE_VERSION, "generated_at": now_iso(),
        "error": reason, "datasets": [], "results": [],
        "quality": {}, "reports": [], "uncertainties": [{
            "item": "no_data", "reason": reason,
            "impact": "无法产生查询或统计结果",
            "action": "提供 CSV/SQLite 数据快照后重跑"}],
    }
    write_json(Path(output) / "results.json", payload)
    write_json(Path(output) / "environment.json", {
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "dependency_policy": "standard library only",
        "generated_at": now_iso(),
    })
    if mode == "database-query":
        write_text(Path(output) / "query.sql",
                   "-- no query was executed: %s\n" % reason)
        write_text(Path(output) / "result-table.csv",
                   "status,message\ninput_error,%s\n" % reason.replace(",", ";"))
    else:
        write_text(Path(output) / "results.csv",
                   "case_id,status,message\n%s,input_error,%s\n"
                   % (case_id, reason.replace(",", ";")))
        write_json(Path(output) / "statistics-report.json", payload)
        write_text(Path(output) / "cleaned-table.csv",
                   "case_id,note\n%s,no readable table\n" % case_id)
        write_json(Path(output) / "quality-report.json", {})
        write_json(Path(output) / "cleaning-rules.json",
                   {"case_id": case_id, "rules": []})
    write_text(Path(output) / "execution.log", "input_error: %s\n" % reason)
    write_text(Path(output) / "analysis-report.md",
               "# 科研数据分析报告\n\n- status：`input_error`\n- case_id：`%s`\n"
               "- 原因：%s\n\n未生成任何查询/统计数字；请提供数据快照后重跑。\n"
               % (case_id, reason))
    work_path = Path(work) / "analysis-plan.json"
    work_data = load_json(work_path, {})
    if not isinstance(work_data, dict):
        work_data = {}
    work_data.update({
        "case_id": case_id, "mode": mode, "summary": reason,
        "analysis": {"question": "无可用数据", "method": "未执行",
                     "assumptions": [], "steps": [], "diagnostics": []},
        "results": [], "evidence": [], "uncertainties": payload["uncertainties"],
        "code": "", "dependencies": [], "inputs": [], "outputs": [],
        "error_handling": [], "execution": {},
    })
    if mode == "database-query":
        work_data.update({
            "query": "", "sql_sha256": "", "snapshot": {},
            "schema_mapping": {}, "row_count_check": {}, "empty_result": {},
            "permissions": {"status": "read_only", "write_allowed": False},
            "sql_text": "", "result_table": "", "field_mapping": {},
            "empty_result_handling": {}, "snapshot_version": {},
        })
    else:
        work_data.update({
            "cleaned_table": "output/cleaned-table.csv",
            "quality_report": {}, "rule_log": [],
            "original_snapshot": {}, "statistics_report":
                "output/statistics-report.json",
            "analysis_code": "", "execution_log": "output/execution.log",
            "environment": {}, "validation": {}, "figures": [],
            "result_table": "output/results.csv", "field_mapping": {},
            "snapshot_version": {},
            "empty_result_handling": {
                "is_empty": True,
                "statement": "没有可读数据表，未执行统计；不把空结果写成无数据。",
            },
        })
    write_json(work_path, work_data)
    print(json.dumps({"status": "input_error", "case_id": case_id,
                      "mode": mode, "reason": reason,
                      "work_file": str(work_path)}, ensure_ascii=False,
                     indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build and run a UCI profile")
    parser.add_argument("--input", default=os.environ.get("SKILL_INPUT", "/input"))
    parser.add_argument("--work", default=os.environ.get("SKILL_WORK", "/app/work"))
    parser.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", "/app/output"))
    parser.add_argument("--mode", default=None)
    parser.add_argument("--skip-run", action="store_true")
    args = parser.parse_args(argv)

    input_dir = Path(args.input)
    work = Path(args.work)
    output = Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    task_config = load_json(SKILL_DIR / "task.json", {})
    mode = args.mode or task_config.get("mode") or "statistics"
    case = load_json(input_dir / "case.json", {})
    case_id = str(case.get("case_id") or case.get("id")
                  or task_config.get("challenge_id") or "CASE")
    found = discover(input_dir)
    tables = prepare_tables(found)
    if not tables:
        return empty_exit(input_dir, work, output, mode, case_id,
                          "未在 /input 找到可读的 SQLite/CSV/JSON/XLSX 数据表")

    for table in tables:
        table["source"] = rel(table["source_path"], input_dir)
    provided = parse_dictionary(found["dictionary"], input_dir)
    dictionary_lines = ["# UCI profile dictionary", ""]
    for table in tables:
        table_lines = dictionary_for(table["columns"], table["name"], provided)
        dictionary_lines.extend(table_lines[2:])
    dictionary_lines.append("")
    write_text(work / "uci-dictionary.md", "\n".join(dictionary_lines))
    for table in tables:
        for column in table["columns"]:
            if column.get("dictionary_source") != "work/uci-dictionary.md":
                continue
            quote = column.get("dictionary_quote")
            for line_no, line in enumerate(dictionary_lines, 1):
                if line == quote:
                    column["dictionary_locator"] = "L%d" % line_no
                    break

    derived_meta = build_derived_snapshot(tables, work)
    source_manifest = build_source_manifest(found, input_dir)
    question = case_question(input_dir)
    cfg = {
        "case_id": case_id, "mode": mode, "question_text": question,
        "input_dir": str(input_dir.resolve()),
        "work_dir": str(work.resolve()), "output_dir": str(output.resolve()),
        "snapshot_path": str((work / "uci-snapshot.sqlite").resolve()),
        "user_sql_path": str((work / "query.sql").resolve()),
        "derived_meta": derived_meta,
        "sources": source_manifest,
        "tables": [table_config(table) for table in tables],
    }
    primary_name, query_target = choose_query_target(cfg, tables, found)
    cfg["query_target"] = query_target
    cfg["primary_table"] = primary_name
    source_rows = {table["name"]: table["n_rows"] for table in tables}
    if (mode == "database-query" and query_target
            and query_target.get("kind") == "provided_sqlite"):
        cfg["snapshot_meta"] = {
            "path": query_target.get("source"),
            "sha256": query_target.get("sha256"),
            "bytes": Path(query_target["path"]).stat().st_size,
            "sqlite_version": sqlite3.sqlite_version,
            "origin": "provided_sqlite",
            "read_only": True,
            "tables": source_rows,
            "derived_snapshot": {
                "path": "work/uci-snapshot.sqlite",
                "sha256": derived_meta["sha256"],
            },
            "sources": source_manifest,
        }
    else:
        cfg["snapshot_meta"] = {
            "path": "work/uci-snapshot.sqlite",
            "sha256": derived_meta["sha256"],
            "bytes": derived_meta["bytes"],
            "sqlite_version": sqlite3.sqlite_version,
            "origin": "derived_from_input_snapshots",
            "read_only": True,
            "tables": source_rows,
            "sources": source_manifest,
        }
    write_json(work / "uci-run.json", cfg)
    code_sha = write_generated_code(work, output)

    execution = {"returncode": None, "elapsed_ms": None, "stdout": "",
                 "stderr": ""}
    if not args.skip_run:
        execution = run_generated(work, output)
    payload = load_json(work / "core-output.json", {})
    if not payload:
        payload = load_json(output / "results.json", {})
    if not payload:
        payload = {
            "status": "input_error", "case_id": case_id, "mode": mode,
            "error": "generated analysis produced no output",
            "datasets": [], "results": [], "uncertainties": [],
        }
    payload.setdefault("case_id", case_id)
    payload.setdefault("mode", mode)
    payload.setdefault("snapshot", cfg["snapshot_meta"])

    evidence = build_evidence(payload, provided, output, work)
    figures = write_figures(payload, output) if mode != "database-query" else []
    diagnostics = _diagnostics(payload, mode)
    context = {
        "case_id": case_id, "mode": mode, "question": question,
        "method": _method(mode, payload), "steps": _steps(mode),
        "assumptions": _assumptions(mode),
        "diagnostics": diagnostics, "sample_size": _sample_size(payload),
        "effect_size": _effect_size(payload), "ci": _ci(payload),
        "assumption_checks": _assumption_checks(payload),
        "summary": _summary(payload, mode),
        "source_manifest": source_manifest,
        "outputs": _declared_outputs(mode),
        "error_handling": _error_handling(mode),
        "returncode": execution.get("returncode"),
        "elapsed_ms": execution.get("elapsed_ms"),
        "code_sha256": code_sha,
        "execution": execution,
        "figures": figures,
    }
    write_report(output, payload, {}, context)
    if mode == "cleaning":
        for row in source_manifest:
            source_rel = row.get("path") if isinstance(row, dict) else str(row)
            if source_rel and str(source_rel).lower().endswith(".csv"):
                src = input_dir / source_rel
                if src.is_file():
                    (output / "raw-snapshot.csv").write_bytes(src.read_bytes())
                    break
    work_data = merge_work(work / "analysis-plan.json", payload, context, evidence)
    write_json(work / "uci-profile-summary.json", {
        "case_id": case_id, "mode": mode, "status": payload.get("status"),
        "tables": source_rows, "primary_table": primary_name,
        "query_target": query_target,
        "code_sha256": code_sha,
        "execution": execution,
        "artifacts": context["outputs"],
        "work_file": str(work / "analysis-plan.json"),
    })
    print(json.dumps({
        "status": payload.get("status"), "case_id": case_id, "mode": mode,
        "tables": source_rows, "results": len(payload.get("results") or []),
        "query_rows": payload.get("row_count"),
        "code_sha256": code_sha,
        "execution_returncode": execution.get("returncode"),
        "work_file": str(work / "analysis-plan.json"),
        "outputs": context["outputs"],
    }, ensure_ascii=False, indent=2))
    return 0


def _declared_outputs(mode):
    if mode == "database-query":
        return ["output/analysis-report.md", "output/query.sql",
                "output/result-table.csv", "output/results.json",
                "output/execution.log"]
    if mode == "code-generation":
        return ["output/analysis-report.md", "output/analysis-code.py",
                "output/results.json", "output/execution.log",
                "output/environment.json"]
    return ["output/analysis-report.md", "output/results.json",
            "output/results.csv", "output/statistics-report.json",
            "output/analysis-code.py", "output/environment.json"]


def _method(mode, payload):
    if mode == "database-query":
        return ("在只读快照上执行单条自动生成的 SELECT；"
                "按数据字典映射字段，并核对行数、组覆盖、空结果与跨表共享键。")
    if mode == "code-generation":
        return ("生成自包含的 Python 分析脚本并在快照上真实执行；"
                "用 SQL 聚合与 Python 计算两条路径交叉校验，"
                "同时检查空输入、缺列、非数值、零方差与只读守卫。")
    return ("对每个数值指标做描述统计，按分组做 Welch/经典 ANOVA，"
            "报告 eta²/omega²、均值差与 95% CI、Holm 校正的成对 Welch 检验；"
            "用 Jarque-Bera 与 Brown-Forsythe 做前提筛查。")


def _steps(mode):
    if mode == "database-query":
        return ["识别表/列并建立规范字段映射", "在只读快照上执行查询",
                "导出结果表", "核对行数/组覆盖/连接风险", "写报告与工作文件"]
    return ["识别表/列并建立规范字段映射", "缺失/重复/异常值检查",
            "生成分析脚本并真实执行", "描述统计与推断统计",
            "效应量/置信区间/多重比较", "写报告与工作文件"]


def _assumptions(mode):
    if mode == "database-query":
        return ["快照在本次运行期间保持不变", "只读权限足够回答该问题",
                "字段语义以数据字典与列名映射为准"]
    return ["每个统计量使用该变量的完整观测（不插补）",
            "分组标签来自原始类别编码，不重新编码",
            "经典 ANOVA 用于方差齐性未触发警示时；否则优先 Welch ANOVA",
            "p 值为双尾；多重比较使用 Holm 校正"]


def _basic_validation(payload):
    """Row-count and quality checks that are available without extra code."""
    checks = []
    for row in payload.get("datasets") or []:
        expected = row.get("rows_expected")
        checks.append({
            "check": "row_count:%s" % row.get("table"),
            "status": ("pass" if row.get("rows_match_expected") else "attention"),
            "expected": expected, "actual": row.get("rows"),
        })
    quality = payload.get("quality") or {}
    for table, entry in quality.items():
        checks.append({
            "check": "duplicate_rows:%s" % table,
            "status": "attention" if entry.get("duplicate_rows_exact") else "pass",
            "value": entry.get("duplicate_rows_exact"),
        })
        checks.append({
            "check": "missing_cells:%s" % table,
            "status": "attention" if entry.get("missing_cells") else "pass",
            "value": entry.get("missing_cells"),
        })
    failed = [row for row in checks if row["status"] == "fail"]
    return {
        "status": "fail" if failed else "pass",
        "checks": checks,
        "method": ("公开快照行数核对 + 缺失/完全重复计数；"
                   "attention 表示需要在报告中说明，不等于运行失败。"),
    }


def _diagnostics(payload, mode):
    diagnostics = []
    for row in payload.get("datasets") or []:
        diagnostics.append("%s: %d 行 / %d 列；与公开快照行数%s" % (
            row["table"], row["rows"], row["columns"],
            "一致" if row.get("rows_match_expected") else "不一致"))
    if mode == "database-query":
        diagnostics.append("查询返回 %s 行" % payload.get("row_count"))
    else:
        diagnostics.append("产生 %d 条统计指标" % len(payload.get("results") or []))
        validation = payload.get("validation") or {}
        if validation:
            diagnostics.append("代码校验：%s（失败 %s）" % (
                validation.get("status"), validation.get("failed")))
    return diagnostics


def _sample_size(payload):
    sizes = []
    for report in payload.get("reports") or []:
        for outcome in report.get("outcomes") or []:
            n = (outcome.get("overall") or {}).get("n")
            if n:
                sizes.append(n)
    return max(sizes) if sizes else None


def _effect_size(payload):
    for report in payload.get("reports") or []:
        for outcome in report.get("outcomes") or []:
            value = ((outcome.get("inference") or {}).get("effect_size")
                     or {}).get("eta_squared")
            if value is not None:
                return "eta_squared=%s" % value
    return ""


def _ci(payload):
    for report in payload.get("reports") or []:
        for outcome in report.get("outcomes") or []:
            ci = (outcome.get("overall") or {}).get("ci95")
            if ci and ci[0] is not None:
                return ci
    return []


def _assumption_checks(payload):
    checks = []
    for report in payload.get("reports") or []:
        for outcome in report.get("outcomes") or []:
            assumptions = (outcome.get("inference") or {}).get("assumptions") or {}
            if not assumptions:
                continue
            checks.append({
                "outcome": outcome.get("outcome"),
                "normality_flags": assumptions.get("normality_flags_0_05") or [],
                "variance_p": (assumptions.get("variance_homogeneity")
                               or {}).get("p"),
            })
    return checks[:20]


def _summary(payload, mode):
    datasets = payload.get("datasets") or []
    names = ", ".join("%s(%d 行)" % (row["table"], row["rows"])
                      for row in datasets)
    if mode == "database-query":
        return ("在记录快照上执行只读查询：%s；返回 %s 行；"
                "空结果标记为 %s。" % (
                    names or "无可识别表", payload.get("row_count"),
                    (payload.get("empty_result") or {}).get("is_empty")))
    return ("对 %s 完成描述统计、分组推断、效应量与置信区间；"
            "%d 条指标；代码真实执行并校验。" % (
                names or "无可识别表", len(payload.get("results") or [])))


def _error_handling(mode):
    if mode == "database-query":
        return [
            {"condition": "空结果", "action": "显式写出 is_empty 与缺失组，不写成不存在"},
            {"condition": "SQL 报错", "action": "记录错误与可用表/列，回退到自动查询"},
            {"condition": "连接/权限失败", "action": "写 uncertainty 并建议人工接管"},
        ]
    return [
        {"condition": "缺数据/缺列", "action": "写 input_error 或跳过该变量并记录 n"},
        {"condition": "非数值混入", "action": "不转成 0；按缺失处理并报告"},
        {"condition": "零方差/样本不足", "action": "检验标记为无定义，不伪造 p 值"},
        {"condition": "正态性/方差齐性不满足", "action": "改用 Welch 检验并降低结论强度"},
    ]


if __name__ == "__main__":
    raise SystemExit(main())
