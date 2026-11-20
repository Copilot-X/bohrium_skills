#!/usr/bin/env python3
"""Index Markdown pipe tables and CSV/TSV tables from an input bundle.

Usage:
    python3 extract_tables.py --input /input --out /app/work/tables.json --json

Output is a candidate index, not extracted answers: every row keeps the raw
line, header path, line number and parsed numeric cells so a reviewer can
re-open the source. Markdown tables preserve the exact source block, which is
the preferred quote for table evidence.

Stdlib only. Exit 0 on success, 2 when the input root is missing.
"""
import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

TEXT_EXT = {".md", ".markdown", ".txt", ".csv", ".tsv"}
SEP_CELL = re.compile(r"^:?-{3,}:?$")
HEADER_UNIT = re.compile(r"^(.*?)\s*[\(\[（]([^\(\)\[\]（）]+)[\)\]）]\s*$")
NUMBER_WITH_UNIT = re.compile(
    r"^([-−+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(%|°C|℃|h\^?-?1|h−1|s\^?-?1|s−1)?$"
)
TABLE_CAPTION = re.compile(r"^\s*(?:Table|TABLE|表)\s*[0-9A-Za-z]")
TABLE_ID = re.compile(r"(?:Table|TABLE|表)\s*([0-9]+[A-Za-z]?)")
CELL_SPLIT = re.compile(r"(?<!\\)\|")


def split_row(line):
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    return [cell.strip().replace("\\|", "|") for cell in CELL_SPLIT.split(text)]


def is_separator(line):
    if "|" not in line:
        return False
    cells = [cell.replace(" ", "") for cell in split_row(line)]
    cells = [cell for cell in cells if cell]
    return bool(cells) and all(SEP_CELL.match(cell) for cell in cells)


def find_caption(lines, header_index):
    for index in (header_index - 1, header_index - 2):
        if index < 0:
            continue
        text = lines[index].strip()
        if TABLE_CAPTION.match(text) and not text.startswith("|"):
            return text.lstrip("#").strip()
    return ""


def table_id_from_caption(caption, fallback):
    match = TABLE_ID.search(caption or "")
    return "t%s" % match.group(1) if match else "t%d" % fallback


def enrich_header(headers):
    names, units = [], {}
    for header in headers:
        match = HEADER_UNIT.match(header)
        if match and match.group(1).strip():
            name = match.group(1).strip()
            unit = match.group(2).strip()
            names.append(name)
            units[name] = unit
        else:
            names.append(header)
    return names, units


def parse_number(raw):
    text = str(raw or "").strip()
    match = NUMBER_WITH_UNIT.match(text)
    if not match:
        return None
    value_text = match.group(1).replace(",", "").replace("−", "-")
    try:
        number = float(value_text)
    except ValueError:
        return None
    value = int(number) if number.is_integer() else number
    return value, match.group(2)


def numeric_cells(rows, column_units):
    cells = []
    for row in rows:
        for column, raw in row["cells"].items():
            parsed = parse_number(raw)
            if parsed is None:
                continue
            value, explicit_unit = parsed
            unit = explicit_unit or column_units.get(column)
            cells.append({
                "row_index": row["row_index"],
                "column": column,
                "raw": raw,
                "value": value,
                "unit": unit,
                "unit_source": "cell" if explicit_unit else (
                    "header" if unit else "not_reported"),
            })
    return cells


def make_table(source_path, fmt, start_line, caption, headers, rows,
               raw_block, fallback_id):
    names, units = enrich_header(headers)
    normalized_rows = []
    for index, row in enumerate(rows):
        cells = {}
        for position, cell in enumerate(row):
            if position < len(names):
                cells[names[position]] = cell
        normalized_rows.append({
            "row_index": index + 1,
            "line": None,
            "cells": cells,
            "raw": None,
        })
    return {
        "table_id": table_id_from_caption(caption, fallback_id),
        "source_path": source_path,
        "format": fmt,
        "start_line": start_line,
        "caption": caption,
        "header": names,
        "header_paths": [[name] for name in names],
        "column_units": units,
        "rows": normalized_rows,
        "raw_block": raw_block,
        "numeric_cells": numeric_cells(normalized_rows, units),
    }


def parse_markdown_tables(text, source_path, warnings):
    lines = text.splitlines()
    tables = []
    index = 0
    fallback = 1
    while index + 1 < len(lines):
        if "|" not in lines[index] or not is_separator(lines[index + 1]):
            index += 1
            continue
        header = split_row(lines[index])
        if not any(header):
            index += 1
            continue
        rows, raw_lines = [], [lines[index], lines[index + 1]]
        cursor = index + 2
        while cursor < len(lines) and lines[cursor].strip() and "|" in lines[cursor]:
            cells = split_row(lines[cursor])
            if len(cells) != len(header):
                warnings.append(
                    "%s:%d: row has %d cells, header has %d"
                    % (source_path, cursor + 1, len(cells), len(header)))
                cells = (cells + [""] * len(header))[:len(header)]
            rows.append({
                "row_index": len(rows) + 1,
                "line": cursor + 1,
                "cells": {header[i]: cells[i] for i in range(len(header))},
                "raw": lines[cursor].strip(),
            })
            raw_lines.append(lines[cursor])
            cursor += 1
        caption = find_caption(lines, index)
        table = make_table(source_path, "markdown", index + 1, caption,
                           header, [[row["cells"].get(name, "") for name in header]
                                    for row in rows],
                           "\n".join(raw_lines), fallback)
        for row, parsed in zip(table["rows"], rows):
            row["line"] = parsed["line"]
            row["raw"] = parsed["raw"]
        table["raw_block"] = "\n".join(raw_lines)
        tables.append(table)
        fallback += 1
        index = cursor
    return tables


def parse_delimited(text, source_path, delimiter, warnings):
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        records = list(reader)
    except csv.Error as exc:
        warnings.append("%s: csv parse failed: %s" % (source_path, exc))
        return []
    records = [row for row in records if any(str(cell).strip() for cell in row)]
    if len(records) < 2:
        return []
    header = [str(cell).strip() for cell in records[0]]
    rows, raw_lines = [], []
    for offset, record in enumerate(records[1:], start=1):
        if len(record) != len(header):
            warnings.append("%s: row %d has %d cells, header has %d"
                            % (source_path, offset, len(record), len(header)))
        cells = [str(cell).strip() for cell in record]
        cells = (cells + [""] * len(header))[:len(header)]
        rows.append(cells)
        raw_lines.append(delimiter.join(cells))
    table = make_table(source_path, "tsv" if delimiter == "\t" else "csv",
                       1, "", header, rows, "\n".join(raw_lines), 1)
    for index, row in enumerate(table["rows"]):
        row["line"] = index + 2
        row["raw"] = raw_lines[index]
    return [table]


def scan(root, max_bytes):
    report = {"tables": [], "count": 0, "files_scanned": 0,
              "warnings": [], "root": str(root)}
    root = Path(root)
    if not root.exists():
        report["error"] = "input root missing: %s" % root
        return report
    files = [root] if root.is_file() else sorted(root.rglob("*"))
    for path in files:
        if not path.is_file() or path.suffix.lower() not in TEXT_EXT:
            continue
        try:
            if path.stat().st_size > max_bytes:
                report["warnings"].append("%s: skipped (> %d bytes)"
                                          % (path, max_bytes))
                continue
            data = path.read_bytes()
        except OSError as exc:
            report["warnings"].append("%s: unreadable: %s" % (path, exc))
            continue
        if b"\x00" in data:
            report["warnings"].append("%s: binary content skipped" % path)
            continue
        text = data.decode("utf-8", errors="replace")
        report["files_scanned"] += 1
        rel = str(path.relative_to(root)) if root.is_dir() else path.name
        if path.suffix.lower() in (".md", ".markdown", ".txt"):
            report["tables"].extend(parse_markdown_tables(
                text, rel, report["warnings"]))
        elif path.suffix.lower() == ".csv":
            report["tables"].extend(parse_delimited(
                text, rel, ",", report["warnings"]))
        elif path.suffix.lower() == ".tsv":
            report["tables"].extend(parse_delimited(
                text, rel, "\t", report["warnings"]))
    # stable renumbering by source file and start line
    report["tables"].sort(key=lambda t: (t["source_path"], t["start_line"]))
    report["count"] = len(report["tables"])
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/input")
    parser.add_argument("--out", default="/app/work/tables.json")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--max-file-bytes", type=int, default=5_000_000)
    args = parser.parse_args()

    report = scan(args.input, args.max_file_bytes)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    if args.json:
        print(text)
    else:
        print(json.dumps({
            "count": report.get("count", 0),
            "files_scanned": report.get("files_scanned", 0),
            "warnings": len(report.get("warnings", [])),
            "out": args.out,
        }, ensure_ascii=False, indent=2))
    return 2 if report.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
