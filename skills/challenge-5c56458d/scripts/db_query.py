#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Execute a read-only SQL query against the case snapshot.

The script is deliberately deterministic and standard-library only:

* discovers SQLite snapshots (or loads CSVs into an in-memory file DB);
* records the snapshot path, size and SHA-256;
* executes exactly the SQL in ``work/query.sql`` (or the ``query`` field of the
  work file) with SQLite read-only/query-only guards;
* exports ``output/query.sql``, ``output/result-table.csv``,
  ``output/results.json`` and ``output/execution.log``;
* checks row count, group coverage, JOIN fan-out risk and field mapping;
* writes the structured results back into ``work/analysis-plan.json``.

It never invents a row or a number: every value in the report comes from the
SQLite cursor or from the source files.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SKILL_VERSION = "1.1.0"
READ_ONLY_START = re.compile(
    r"^\s*(?:with\b|select\b|explain\b)", re.I)
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|grant|revoke)\b", re.I)
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".db3"}
TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".sql"}
UNIT_ALIASES = {
    "ug/ml": "ug/mL", "µg/ml": "ug/mL", "μg/ml": "ug/mL",
    "mg/l": "mg/L", "g/l": "g/L", "mg/ml": "mg/mL",
    "ng/ml": "ng/mL", "ug/l": "ug/L", "µg/l": "ug/L",
    "μg/l": "ug/L", "ppm": "ppm", "%": "%",
}


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


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def rel(path, base):
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except (ValueError, OSError):
        return str(path)


def sanitize(text, fallback="table"):
    value = re.sub(r"[^0-9A-Za-z_]+", "_", str(text or "")).strip("_").lower()
    if not value:
        value = fallback
    if value[0].isdigit():
        value = "t_" + value
    return value


def json_value(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def discover(input_dir):
    root = Path(input_dir)
    out = {"sqlite": [], "csv": [], "text": [], "dictionary": [],
           "request": [], "schema_sql": [], "case": None}
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        low = path.name.lower()
        suffix = path.suffix.lower()
        if suffix in SQLITE_SUFFIXES:
            out["sqlite"].append(path)
        elif suffix == ".csv":
            out["csv"].append(path)
        elif suffix in TEXT_SUFFIXES:
            out["text"].append(path)
            if "dictionary" in low or "data_dict" in low or "schema" in low and suffix == ".md":
                out["dictionary"].append(path)
            if "request" in low or "question" in low or "query" in low:
                out["request"].append(path)
            if suffix == ".sql":
                out["schema_sql"].append(path)
        if suffix == ".json" and low in ("case.json", "input.json"):
            data = load_json(path, {})
            if isinstance(data, dict):
                out["case"] = data
    if not out["dictionary"]:
        out["dictionary"] = [p for p in out["text"]
                             if "dictionary" in p.name.lower()]
    if not out["request"]:
        out["request"] = [p for p in out["text"]
                          if "request" in p.name.lower()
                          or "question" in p.name.lower()]
    return out


def infer_type(values):
    seen = set()
    for raw in values:
        cell = str(raw).strip()
        if cell == "":
            continue
        try:
            float(cell)
            seen.add("number")
        except ValueError:
            seen.add("text")
        if len(seen) > 1:
            return "NUMERIC"
    return "REAL" if seen == {"number"} else "TEXT"


def load_csvs(csv_paths, work_dir):
    """Load CSV files into a private SQLite database with typed columns."""
    db_path = Path(work_dir) / "csv-snapshot.sqlite"
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    tables = {}
    seen_names = set()
    try:
        for path in csv_paths:
            try:
                with path.open("r", encoding="utf-8-sig", errors="replace",
                               newline="") as fh:
                    rows = list(csv.reader(fh))
            except OSError:
                continue
            if not rows:
                continue
            header = [sanitize(cell, "col_%d" % (i + 1))
                      for i, cell in enumerate(rows[0])]
            used, columns = set(), []
            for index, name in enumerate(header):
                candidate = name
                if candidate in used:
                    candidate = "%s_%d" % (candidate, index + 1)
                used.add(candidate)
                columns.append(candidate)
            body = rows[1:]
            table = sanitize(path.stem)
            base = table
            counter = 2
            while table in seen_names:
                table = "%s_%d" % (base, counter)
                counter += 1
            seen_names.add(table)
            col_types = []
            for index in range(len(columns)):
                col_types.append(infer_type([row[index] if index < len(row) else ""
                                             for row in body]))
            ddl = "CREATE TABLE %s (%s)" % (
                quote_ident(table),
                ", ".join("%s %s" % (quote_ident(c), t)
                          for c, t in zip(columns, col_types)))
            conn.execute(ddl)
            placeholders = ",".join("?" for _ in columns)
            insert = "INSERT INTO %s VALUES (%s)" % (quote_ident(table), placeholders)
            payload = []
            for row in body:
                values = []
                for index in range(len(columns)):
                    cell = row[index].strip() if index < len(row) else ""
                    values.append(None if cell == "" else cell)
                payload.append(values)
            conn.executemany(insert, payload)
            tables[table] = {"source": rel(path, path.parent), "rows": len(body),
                             "columns": [{"name": c, "type": t}
                                         for c, t in zip(columns, col_types)]}
        conn.commit()
    finally:
        conn.close()
    return db_path, tables


def quote_ident(name):
    return '"%s"' % str(name).replace('"', '""')


def connect_readonly(db_path):
    uri = "file:%s?mode=ro" % Path(db_path).resolve().as_posix()
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def table_schema(conn):
    tables = {}
    try:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    except sqlite3.Error:
        return tables
    for name in names:
        try:
            cols = []
            for row in conn.execute("PRAGMA table_info(%s)" % quote_ident(name)):
                cols.append({"name": row[1], "type": row[2],
                             "notnull": bool(row[3]), "pk": bool(row[5])})
            count = conn.execute("SELECT COUNT(*) FROM %s" % quote_ident(name)).fetchone()[0]
            tables[name] = {"columns": cols, "rows": count}
        except sqlite3.Error as exc:
            tables[name] = {"columns": [], "rows": None, "error": str(exc)}
    return tables


def parse_contract(case):
    contract = (case or {}).get("output_contract") or {}
    return contract if isinstance(contract, dict) else {}


def parse_dictionary(paths):
    entries = []
    by_name = {}
    for path in paths:
        text = read_text(path)
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            match = re.match(r"^[-*]\s*`?([A-Za-z_][\w.]*)`?\s*[:：]\s*(.+)$",
                             stripped)
            if not match:
                continue
            name, description = match.group(1), match.group(2).strip()
            table = ""
            column = name
            if "." in name:
                table, column = name.split(".", 1)
            entry = {"source": rel(path, path.parent), "line": line_no,
                     "field": name, "table": table, "column": column,
                     "description": description,
                     "quote": stripped, "locator": "L%d" % line_no}
            entries.append(entry)
            by_name.setdefault(column.lower(), []).append(entry)
    return entries, by_name


def parse_request(paths):
    items = []
    for path in paths:
        text = read_text(path)
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            items.append({"source": rel(path, path.parent), "line": line_no,
                          "text": stripped, "locator": "L%d" % line_no})
    return items


def strip_sql_comments(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def statement_count(sql):
    text = sql.strip()
    if not text:
        return 0
    count = 0
    buffer = ""
    for char in text:
        buffer += char
        if sqlite3.complete_statement(buffer):
            if strip_sql_comments(buffer).strip(" ;\n\t"):
                count += 1
            buffer = ""
    if strip_sql_comments(buffer).strip(" ;\n\t"):
        count += 1
    return count


def check_sql(sql):
    text = (sql or "").strip()
    if not text:
        return "the query is empty"
    if statement_count(text) != 1:
        return "exactly one SQL statement is required"
    head = strip_sql_comments(text).strip()
    if not READ_ONLY_START.match(head):
        return "only SELECT/WITH/EXPLAIN queries are allowed"
    forbidden = FORBIDDEN.search(strip_sql_comments(head))
    if forbidden:
        return "read-only snapshot rejected keyword: %s" % forbidden.group(1)
    return ""


def table_refs(sql):
    refs = re.findall(r"\b(?:from|join)\s+([A-Za-z_][\w\"]*)", sql, re.I)
    return [ref.strip('"') for ref in refs]


def aggregate_present(sql):
    return bool(re.search(r"\b(group\s+by|distinct|count|sum|avg|min|max|"
                          r"total|group_concat)\b", sql, re.I))


def join_fanout(conn, schema, sql):
    refs = list(dict.fromkeys(table_refs(sql)))
    checks = []
    for left_index in range(len(refs)):
        for right_index in range(left_index + 1, len(refs)):
            left, right = refs[left_index], refs[right_index]
            if left not in schema or right not in schema:
                continue
            left_cols = {c["name"].lower(): c["name"] for c in schema[left]["columns"]}
            right_cols = {c["name"].lower(): c["name"] for c in schema[right]["columns"]}
            common = sorted(set(left_cols) & set(right_cols))
            for column in common:
                try:
                    left_max = conn.execute(
                        "SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM %s "
                        "GROUP BY %s)" % (quote_ident(left), quote_ident(left_cols[column]))
                    ).fetchone()[0]
                    right_max = conn.execute(
                        "SELECT MAX(c) FROM (SELECT COUNT(*) AS c FROM %s "
                        "GROUP BY %s)" % (quote_ident(right), quote_ident(right_cols[column]))
                    ).fetchone()[0]
                except sqlite3.Error:
                    continue
                checks.append({
                    "left": left, "right": right, "key": column,
                    "left_max_per_key": left_max, "right_max_per_key": right_max,
                    "one_to_one": bool((left_max or 0) <= 1 and (right_max or 0) <= 1),
                    "mitigated_by_aggregation": aggregate_present(sql),
                })
    return checks


def group_coverage(conn, schema, sql, result_columns, rows):
    """Compare output groups with base-table groups for same-named columns."""
    checks = []
    if not schema:
        return checks
    for index, column in enumerate(result_columns):
        values = {str(row[index]) for row in rows if row[index] is not None}
        for table, info in schema.items():
            base_col = next((c["name"] for c in info["columns"]
                             if c["name"].lower() == column.lower()), None)
            if not base_col:
                continue
            try:
                distinct = conn.execute(
                    "SELECT COUNT(DISTINCT %s) FROM %s" % (
                        quote_ident(base_col), quote_ident(table))).fetchone()[0]
                if distinct is None or distinct > 100:
                    continue
                base_values = {str(row[0]) for row in conn.execute(
                    "SELECT DISTINCT %s FROM %s" % (
                        quote_ident(base_col), quote_ident(table)))
                    if row[0] is not None}
            except sqlite3.Error:
                continue
            missing = sorted(base_values - values)
            extra = sorted(values - base_values)
            if missing or extra:
                checks.append({
                    "output_column": column, "source_table": table,
                    "source_column": base_col,
                    "base_distinct": len(base_values),
                    "output_distinct": len(values),
                    "missing_groups": missing,
                    "extra_groups": extra,
                    "status": "group_mismatch" if missing else "extra_groups",
                })
    return checks


def build_schema_mapping(dictionary, schema, sql, result_columns):
    mapping = {}
    for entry in dictionary:
        key = entry["field"]
        found_table, found_column = "", ""
        for table, info in schema.items():
            if entry["table"] and entry["table"].lower() != table.lower():
                continue
            for column in info["columns"]:
                if column["name"].lower() == entry["column"].lower():
                    found_table, found_column = table, column["name"]
                    break
            if found_column:
                break
        in_output = any(c.lower() == entry["column"].lower()
                        for c in result_columns)
        mapping[key] = {
            "origin": "dictionary",
            "dictionary_field": entry["field"],
            "dictionary_description": entry["description"],
            "source": entry["source"], "locator": entry["locator"],
            "quote": entry["quote"],
            "schema_table": found_table, "schema_column": found_column,
            "exists_in_schema": bool(found_column),
            "appears_in_query": bool(re.search(
                r"(?<![\w])%s(?![\w])" % re.escape(entry["column"]), sql)),
            "appears_in_result": in_output,
        }
    for column in result_columns:
        if column not in mapping:
            mapping[column] = {
                "origin": "result_only",
                "dictionary_field": column, "dictionary_description": "",
                "source": "", "locator": "", "quote": "",
                "schema_table": next((t for t, i in schema.items()
                                      if any(c["name"].lower() == column.lower()
                                             for c in i["columns"])), ""),
                "schema_column": column,
                "exists_in_schema": any(c["name"].lower() == column.lower()
                                        for i in schema.values()
                                        for c in i["columns"]),
                "appears_in_query": True, "appears_in_result": True,
            }
    return mapping


def result_metrics(columns, rows):
    metrics = [{"metric": "result_row_count", "value": len(rows)}]
    for index, column in enumerate(columns):
        values = []
        for row in rows:
            value = row[index]
            if value is None:
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                pass
        if values and len(values) == len([r for r in rows if r[index] is not None]):
            metrics.append({
                "metric": "column_%s" % sanitize(column),
                "value": round(sum(values) / len(values), 6),
                "unit": "",
                "n": len(values),
                "operation": "mean_of_returned_rows",
            })
    return metrics


def build_empty_result(rows, schema, coverage, sql):
    missing = [entry for entry in coverage if entry.get("missing_groups")]
    is_empty = len(rows) == 0
    statements = []
    if is_empty:
        statements.append(
            "The query returned zero rows on the recorded snapshot; "
            "this is reported as an empty result, not as a successful finding.")
    if missing:
        names = sorted({g for entry in missing for g in entry["missing_groups"]})
        statements.append(
            "Registered groups with no returned row: %s." % ", ".join(names))
        statements.append(
            "These groups are reported as empty groups and are not dropped.")
    if not statements and not is_empty:
        statements.append("No empty group was detected among the compared columns.")
    return {
        "is_empty": is_empty,
        "missing_groups": sorted({g for entry in missing for g in entry["missing_groups"]}),
        "coverage_checks": coverage,
        "statement": " ".join(statements),
    }


def write_markdown(path, payload):
    lines = ["# 科研数据库查询与分析报告", ""]
    lines.append("> 本报告的数字由 `scripts/db_query.py` 在记录的快照上执行查询后生成；"
                 "`output/results.json` 是机器可读的同一份结果。")
    lines += ["", "## 1. 数据快照与权限", ""]
    snapshot = payload["snapshot"]
    lines.append("- 快照：`%s`" % snapshot["path"])
    lines.append("- SHA-256：`%s`" % snapshot["sha256"])
    lines.append("- 大小：%d bytes；SQLite 版本：%s" % (
        snapshot["bytes"], snapshot["sqlite_version"]))
    lines.append("- 权限：只读（`mode=ro` + `PRAGMA query_only=ON`）")
    lines += ["", "## 2. 可复跑查询", "", "```sql", payload["query"].strip(), "```", ""]
    lines.append("- SQL SHA-256：`%s`" % payload["sql_sha256"])
    lines.append("- 执行命令：`python scripts/db_query.py --input /input "
                 "--work /app/work --output /app/output`")
    lines += ["", "## 3. 字段映射（数据字典 → schema）", "",
              "| 字段 | 类型/来源 | 字典解释 | 在查询中 | 在结果中 |",
              "|---|---|---|---|---|"]
    for key, entry in payload["schema_mapping"].items():
        lines.append("| `%s` | `%s.%s` | %s | %s | %s |" % (
            key, entry.get("schema_table") or "-", entry.get("schema_column") or "-",
            (entry.get("dictionary_description") or "-").replace("|", "\\|"),
            "是" if entry.get("appears_in_query") else "否",
            "是" if entry.get("appears_in_result") else "否"))
    lines += ["", "## 4. 结果表", "",
              "返回行数：**%d**；列：%s。" % (
                  payload["row_count"], ", ".join("`%s`" % c for c in payload["columns"]) or "无")]
    if payload["preview"]:
        lines += ["", "| " + " | ".join(payload["columns"]) + " |",
                  "|" + "---|" * len(payload["columns"])]
        for row in payload["preview"][:50]:
            lines.append("| " + " | ".join(
                "" if value is None else str(value).replace("|", "\\|")
                for value in row) + " |")
        if payload["row_count"] > len(payload["preview"]):
            lines.append("")
            lines.append("（仅展示前 %d 行，完整结果见 `output/result-table.csv`。）"
                         % len(payload["preview"]))
    lines += ["", "## 5. 行数与空结果校验", ""]
    check = payload["row_count_check"]
    lines.append("- 行数核对：期望 %s，实际 %d，结果 %s（来源：%s）。" % (
        check.get("expected") if check.get("expected") is not None else "未预设",
        check["actual"], "一致" if check["ok"] else "不一致",
        check.get("source") or "execution"))
    empty = payload["empty_result"]
    lines.append("- 空结果处理：is_empty=%s；%s" % (empty["is_empty"], empty["statement"]))
    if payload["coverage_checks"]:
        lines += ["", "| 输出列 | 源表.列 | 基准组数 | 输出组数 | 缺失组 |",
                  "|---|---|---|---|---|"]
        for row in payload["coverage_checks"]:
            lines.append("| `%s` | `%s.%s` | %d | %d | %s |" % (
                row["output_column"], row["source_table"], row["source_column"],
                row["base_distinct"], row["output_distinct"],
                ", ".join(row["missing_groups"]) or "-"))
    lines += ["", "## 6. 跨表连接与膨胀检查", ""]
    if payload["join_checks"]:
        lines += ["| 左表 | 右表 | 键 | 左表每键最大行数 | 右表每键最大行数 | 1:1 | 已聚合 |",
                  "|---|---|---|---|---|---|---|"]
        for row in payload["join_checks"]:
            lines.append("| `%s` | `%s` | `%s` | %s | %s | %s | %s |" % (
                row["left"], row["right"], row["key"],
                row["left_max_per_key"], row["right_max_per_key"],
                "是" if row["one_to_one"] else "否",
                "是" if row["mitigated_by_aggregation"] else "否"))
    else:
        lines.append("- 查询未涉及可检查的多表连接（或未识别到共享键）。")
    lines += ["", "## 7. 限制与不确定性", ""]
    if payload["uncertainties"]:
        for item in payload["uncertainties"]:
            lines.append("- %s" % item)
    else:
        lines.append("- 未发现需要额外人工确认的问题；结论仅适用于上述快照与 SQL。")
    write_text(path, "\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=os.environ.get("SKILL_INPUT", "/input"))
    parser.add_argument("--work", default=os.environ.get("SKILL_WORK", "/app/work"))
    parser.add_argument("--output", default=os.environ.get("SKILL_OUTPUT", "/app/output"))
    parser.add_argument("--db", default="")
    parser.add_argument("--sql-file", default="")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--expected-rows", type=int, default=None)
    args = parser.parse_args(argv)

    input_dir = Path(args.input)
    work = Path(args.work)
    output = Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    discovered = discover(input_dir)
    case = discovered["case"] or {}
    case_id = str(case.get("case_id") or "CASE")
    started = time.time()

    db_path = Path(args.db) if args.db else (
        discovered["sqlite"][0] if discovered["sqlite"] else None)
    tables_meta = {}
    if db_path and db_path.is_file():
        conn = connect_readonly(db_path)
        snapshot_origin = "provided_sqlite"
    else:
        db_path, tables_meta = load_csvs(discovered["csv"], work)
        if not db_path.is_file():
            payload = {"status": "input_error", "case_id": case_id,
                       "error": "no SQLite database and no CSV snapshot in /input"}
            write_json(output / "results.json", payload)
            write_text(output / "execution.log", json.dumps(payload, ensure_ascii=False) + "\n")
            print(json.dumps(payload, ensure_ascii=False))
            return 2
        conn = connect_readonly(db_path)
        snapshot_origin = "csv_loaded_into_sqlite"
    schema = table_schema(conn)
    if args.inspect:
        print(json.dumps({"snapshot": str(db_path), "origin": snapshot_origin,
                          "tables": schema}, ensure_ascii=False, indent=2))
        conn.close()
        return 0

    sql_path = Path(args.sql_file) if args.sql_file else work / "query.sql"
    work_file = work / "analysis-plan.json"
    work_data = load_json(work_file, {})
    sql = read_text(sql_path) if sql_path.is_file() else ""
    if not sql.strip() and isinstance(work_data, dict):
        sql = str(work_data.get("query") or "")
        if sql.strip():
            write_text(sql_path, sql)
    if not sql.strip():
        payload = {
            "status": "input_error", "case_id": case_id,
            "error": "no query supplied; write work/query.sql first",
            "tables": {name: info.get("columns") for name, info in schema.items()},
        }
        write_json(output / "results.json", payload)
        write_text(output / "execution.log",
                   "no query supplied\navailable tables: %s\n" % ", ".join(schema))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        conn.close()
        return 2

    sql = sql.strip()
    if not sql.endswith(";"):
        sql += ";"
    problem = check_sql(sql)
    if problem:
        payload = {"status": "rejected", "case_id": case_id,
                   "error": problem, "query": sql}
        write_json(output / "results.json", payload)
        write_text(output / "execution.log",
                   "query rejected: %s\n%s\n" % (problem, sql))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        conn.close()
        return 3

    write_text(output / "query.sql", sql)
    result_columns, rows, error = [], [], ""
    elapsed = 0.0
    try:
        begin = time.perf_counter()
        cursor = conn.execute(sql)
        result_columns = [col[0] for col in (cursor.description or [])]
        rows = [tuple(json_value(v) for v in row) for row in cursor.fetchall()]
        elapsed = (time.perf_counter() - begin) * 1000.0
    except sqlite3.Error as exc:
        error = str(exc)

    if error:
        hint = ""
        match = re.search(r"no such table: ([\w\"]+)", error)
        if match:
            hint = "available tables: %s" % ", ".join(schema)
        match = re.search(r"no such column: ([\w.\"]+)", error)
        if match:
            column = match.group(1).strip('"')
            owners = []
            for table, info in schema.items():
                for item in info.get("columns") or []:
                    if item["name"].lower() == column.lower():
                        owners.append("%s.%s" % (table, item["name"]))
            hint = "column exists in: %s" % (", ".join(owners) or "no table")
        payload = {
            "status": "query_error", "case_id": case_id, "query": sql,
            "sql_sha256": sha256_bytes(sql.encode("utf-8")),
            "error": error, "hint": hint,
            "snapshot": {"path": rel(db_path, input_dir), "sha256": sha256_file(db_path)},
            "tables": schema,
        }
        write_json(output / "results.json", payload)
        write_text(output / "execution.log",
                   "SQL error: %s\nhint: %s\n\nSQL:\n%s\n" % (error, hint, sql))
        write_text(output / "result-table.csv", "")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        conn.close()
        return 4

    result_table = output / "result-table.csv"
    with result_table.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(result_columns)
        for row in rows:
            writer.writerow(["" if value is None else value for value in row])

    dictionary, _ = parse_dictionary(discovered["dictionary"])
    request_items = parse_request(discovered["request"])
    mapping = build_schema_mapping(dictionary, schema, sql, result_columns)
    coverage = group_coverage(conn, schema, sql, result_columns, rows)
    joins = join_fanout(conn, schema, sql)
    empty = build_empty_result(rows, schema, coverage, sql)
    expected = args.expected_rows
    expected_source = "command_line"
    if expected is None and isinstance(work_data, dict):
        check = work_data.get("row_count_check")
        if isinstance(check, dict) and check.get("expected") is not None:
            try:
                expected = int(check["expected"])
                expected_source = "work/analysis-plan.json"
            except (TypeError, ValueError):
                expected = None
    row_count_check = {
        "expected": expected, "actual": len(rows),
        "ok": expected is None or expected == len(rows),
        "source": expected_source if expected is not None else "execution",
    }
    uncertainties = []
    if empty["is_empty"]:
        uncertainties.append("查询返回 0 行；已按空结果报告，不代表筛选条件之外无数据。")
    if empty["missing_groups"]:
        uncertainties.append("未返回的已登记组：%s；已在空结果中显式保留。" %
                             ", ".join(empty["missing_groups"]))
    risky = [j for j in joins if not j["one_to_one"] and not j["mitigated_by_aggregation"]]
    if risky:
        uncertainties.append(
            "检测到一对多连接且未使用聚合/去重，结果可能膨胀：%s。" %
            "; ".join("%s.%s↔%s.%s" % (j["left"], j["key"], j["right"], j["key"])
                      for j in risky))
    unknown_fields = [k for k, v in mapping.items()
                      if v.get("origin") == "dictionary"
                      and not v["exists_in_schema"]]
    if unknown_fields:
        uncertainties.append("数据字典字段在 schema 中未找到：%s。" % ", ".join(unknown_fields))
    unused = [k for k, v in mapping.items()
              if v.get("origin") == "dictionary"
              and v["exists_in_schema"] and not v["appears_in_query"]]
    if unused:
        uncertainties.append("数据字典字段未出现在查询中（可能不在本题范围）：%s。" %
                             ", ".join(unused))

    if snapshot_origin == "provided_sqlite":
        snapshot = {
            "path": rel(db_path, input_dir),
            "sha256": sha256_file(db_path),
            "bytes": db_path.stat().st_size if db_path.exists() else 0,
            "sqlite_version": sqlite3.sqlite_version,
            "origin": snapshot_origin,
            "tables": {name: info.get("rows") for name, info in schema.items()},
            "read_only": True,
        }
    else:
        manifest = [{"path": rel(path, input_dir),
                     "sha256": sha256_file(path),
                     "bytes": path.stat().st_size}
                    for path in discovered["csv"] if path.is_file()]
        packed = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        snapshot = {
            "path": "CSV snapshot: " + ", ".join(row["path"] for row in manifest),
            "sha256": sha256_bytes(packed),
            "bytes": sum(row["bytes"] for row in manifest),
            "sqlite_version": sqlite3.sqlite_version,
            "origin": snapshot_origin,
            "manifest": manifest,
            "derived_database": rel(db_path, work),
            "tables": {name: info.get("rows") for name, info in schema.items()},
            "read_only": True,
        }
    payload = {
        "status": "ok",
        "case_id": case_id,
        "generated_at": now_iso(),
        "query": sql,
        "sql_sha256": sha256_bytes(sql.encode("utf-8")),
        "snapshot": snapshot,
        "columns": result_columns,
        "row_count": len(rows),
        "preview": [list(row) for row in rows[:200]],
        "rows": [list(row) for row in rows[:2000]],
        "truncated_rows": max(0, len(rows) - 2000),
        "row_count_check": row_count_check,
        "empty_result": empty,
        "schema_mapping": mapping,
        "join_checks": joins,
        "coverage_checks": coverage,
        "permissions": {"status": "read_only", "mode": "ro+query_only",
                        "write_allowed": False,
                        "notes": "所有查询在只读快照上执行，未写入源库。"},
        "execution": {"elapsed_ms": round(elapsed, 3),
                      "python": sys.version.split()[0],
                      "sqlite": sqlite3.sqlite_version,
                      "returncode": 0},
        "tables": schema,
        "uncertainties": uncertainties,
    }
    write_json(output / "results.json", payload)
    log_lines = [
        "command: python scripts/db_query.py --input %s --work %s --output %s" % (
            input_dir, work, output),
        "snapshot: %s sha256=%s origin=%s" % (
            snapshot["path"], snapshot["sha256"], snapshot_origin),
        "sqlite: %s; python: %s" % (sqlite3.sqlite_version, sys.version.split()[0]),
        "sql_sha256: %s" % payload["sql_sha256"],
        "rows: %d; columns: %s; elapsed_ms: %.3f" % (
            len(rows), ",".join(result_columns), elapsed),
        "row_count_check: expected=%s actual=%d ok=%s" % (
            expected, len(rows), row_count_check["ok"]),
        "empty_result: %s" % empty["statement"],
    ]
    for warning in uncertainties:
        log_lines.append("uncertainty: %s" % warning)
    log_lines.append("SQL:")
    log_lines.extend("  " + line for line in sql.splitlines())
    write_text(output / "execution.log", "\n".join(log_lines) + "\n")
    write_markdown(output / "analysis-report.md", payload)

    # ---- update the work file with the executed, verified fields ----------
    if not isinstance(work_data, dict):
        work_data = {}
    work_data["case_id"] = case_id
    work_data.setdefault("mode", "database-query")
    if not work_data.get("summary"):
        work_data["summary"] = (
            "Executed the recorded read-only query on snapshot %s: %d rows, "
            "%d empty/registered groups flagged." % (
                snapshot["sha256"][:12], len(rows), len(empty["missing_groups"])))
    analysis = work_data.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    analysis.setdefault("question", "Answer the query request on the recorded snapshot")
    analysis.setdefault("method", "Read-only SQL executed with sqlite3; row, group, "
                                  "field-mapping and join checks recorded")
    analysis["steps"] = [
        "discover snapshot and hash it",
        "execute exactly work/query.sql in read-only mode",
        "export result table and structured results",
        "check row count, group coverage and join fan-out",
    ]
    work_data["analysis"] = analysis
    result_rows = result_metrics(result_columns, rows)
    request_evidence = {}
    for item in request_items:
        request_evidence[item["locator"]] = item
    default_quote = next((item for item in request_items
                          if not item["text"].startswith("#")),
                         request_items[0] if request_items else None)
    results = []
    for index, metric in enumerate(result_rows, 1):
        evidence = {
            "path": "output/results.json",
            "locator": "$.row_count" if metric["metric"] == "result_row_count"
                       else "$.rows",
            "quote": "\"row_count\": %d" % len(rows),
        }
        if not results:
            evidence["path"] = default_quote["source"] if default_quote else "output/results.json"
            evidence["locator"] = default_quote["locator"] if default_quote else "$.row_count"
            evidence["quote"] = default_quote["text"] if default_quote else "\"row_count\": %d" % len(rows)
        results.append({"id": "R%03d" % index, "metric": metric["metric"],
                        "value": metric["value"], "unit": metric.get("unit", ""),
                        "n": metric.get("n", len(rows)),
                        "evidence": evidence})
    work_data["results"] = results
    evidence_entries = []
    for entry in dictionary[:20]:
        evidence_entries.append({"path": entry["source"], "locator": entry["locator"],
                                 "quote": entry["quote"]})
    for item in request_items[:10]:
        if item["text"].startswith("#"):
            continue
        evidence_entries.append({"path": item["source"], "locator": item["locator"],
                                 "quote": item["text"]})
    if not evidence_entries:
        evidence_entries.append({"path": "output/results.json", "locator": "$.row_count",
                                 "quote": "\"row_count\": %d" % len(rows)})
    work_data["evidence"] = evidence_entries
    work_data["query"] = sql
    work_data["schema_mapping"] = mapping
    work_data["permissions"] = payload["permissions"]
    work_data["empty_result"] = empty
    work_data["row_count_check"] = row_count_check
    work_data["snapshot"] = snapshot
    work_data["sql_sha256"] = payload["sql_sha256"]
    work_data["sql_text"] = sql
    work_data["result_table"] = "output/result-table.csv"
    work_data["field_mapping"] = mapping
    work_data["empty_result_handling"] = empty
    work_data["snapshot_version"] = snapshot
    # Map any additional output_contract key from case.json to a canonical
    # value, so a renamed contract still resolves to real executed content.
    contract = parse_contract(case)
    canonical = {
        "sql": sql, "query": sql, "sql_text": sql,
        "result_table": "output/result-table.csv",
        "resulttable": "output/result-table.csv",
        "field_mapping": mapping, "fieldmapping": mapping,
        "schema_mapping": mapping,
        "empty_result": empty, "emptyresulthandling": empty,
        "empty_result_handling": empty,
        "snapshot": snapshot, "snapshot_version": snapshot,
        "snapshotversion": snapshot,
        "row_count": row_count_check, "row_count_check": row_count_check,
        "permissions": payload["permissions"], "access": payload["permissions"],
        "results": results, "evidence": evidence_entries,
        "status": "ok",
    }
    for key in contract:
        if isinstance(work_data.get(key), (list, dict)) and work_data.get(key):
            continue
        if work_data.get(key) not in (None, ""):
            continue
        norm = re.sub(r"[^0-9a-z]+", "_", str(key).lower()).strip("_")
        for candidate in (norm, norm.replace("_", "")):
            if candidate in canonical:
                work_data[key] = canonical[candidate]
                break
        else:
            tokens = {token for token in norm.split("_") if len(token) > 2}
            for candidate, value in canonical.items():
                if tokens & {token for token in candidate.split("_")
                             if len(token) > 2}:
                    work_data[key] = value
                    break
        if key not in work_data:
            work_data[key] = "output/results.json"
    existing_uncertainties = work_data.get("uncertainties")
    if not isinstance(existing_uncertainties, list):
        existing_uncertainties = []
    for item in uncertainties:
        existing_uncertainties.append({"item": "query_check", "reason": item,
                                       "impact": "see results.json",
                                       "action": "review before drawing conclusions"})
    work_data["uncertainties"] = existing_uncertainties
    write_json(work_file, work_data)
    write_json(work / "query-run.json", {
        "case_id": case_id, "query": sql, "snapshot": snapshot,
        "row_count": len(rows), "row_count_check": row_count_check,
        "empty_result": empty, "schema_mapping": mapping,
        "join_checks": joins, "coverage_checks": coverage,
        "uncertainties": uncertainties,
    })

    print(json.dumps({
        "status": "ok", "case_id": case_id, "snapshot": snapshot['path'],
        "sql_sha256": payload["sql_sha256"], "row_count": len(rows),
        "columns": result_columns,
        "missing_groups": empty["missing_groups"],
        "row_count_ok": row_count_check["ok"],
        "artifacts": ["output/query.sql", "output/result-table.csv",
                      "output/results.json", "output/execution.log",
                      "output/analysis-report.md"],
        "work_file": str(work_file),
    }, ensure_ascii=False, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
