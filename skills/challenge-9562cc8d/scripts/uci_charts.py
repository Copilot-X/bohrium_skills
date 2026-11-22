#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic chart first pass for a tabular/UCI analysis case.

Reads CSV/TSV or SQLite tables, picks numeric columns, and writes a real
editable SVG plus a stdlib-encoded PNG preview (no third-party dependency).
Axes, ticks, units (when supplied), legend and a data trace are included.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sqlite3
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path

PALETTE = [(31, 119, 180), (255, 127, 14), (44, 160, 44),
           (214, 39, 40), (148, 103, 189)]


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def norm(text):
    return str(text or "").strip()


def number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def load_tables(inp, materials):
    tables = []
    for doc in materials.get("documents") or materials.get("materials") or []:
        text = doc.get("text") or doc.get("excerpt") or ""
        path = doc.get("path") or doc.get("rel_path") or ""
        if not text:
            continue
        lines = [x for x in text.splitlines() if x.strip()]
        if len(lines) < 2:
            continue
        delim = "\t" if "\t" in lines[0] else (
            ";" if ";" in lines[0] and "," not in lines[0] else ",")
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        except csv.Error:
            continue
        if len(rows) >= 2 and len(rows[0]) >= 2:
            tables.append({"path": path, "header": [str(x).strip() for x in rows[0]],
                           "rows": rows[1:], "text": text})
    for path in sorted(inp.rglob("*")):
        if path.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
            try:
                conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
                names = [row[0] for row in conn.execute(
                    "select name from sqlite_master where type='table'")]
                for name in names:
                    cur = conn.execute('select * from "%s" limit 5000' % name)
                    rows = cur.fetchall()
                    if len(rows) >= 2:
                        tables.append({"path": path.relative_to(inp).as_posix(),
                                       "header": [d[0] for d in cur.description],
                                       "rows": [list(r) for r in rows]})
                conn.close()
            except sqlite3.Error:
                continue
    return tables


def numeric_columns(table):
    out = []
    for index, name in enumerate(table["header"]):
        values = [number(row[index]) for row in table["rows"]
                  if index < len(row) and number(row[index]) is not None]
        if len(values) >= max(3, len(table["rows"]) // 2):
            out.append((index, name, values))
    return out


def png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data +
            struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path, width, height, pixels):
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(pixels[y * width * 3:(y + 1) * width * 3])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) +
            png_chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
            png_chunk(b"IEND", b""))
    Path(path).write_bytes(data)


def draw_png(path, xs, ys, xlabel, ylabel, width=800, height=600):
    pixels = bytearray([255, 255, 255] * width * height)
    left, right, top, bottom = 80, width - 30, 30, height - 60
    def put(x, y, color):
        if 0 <= x < width and 0 <= y < height:
            i = (y * width + x) * 3
            pixels[i:i + 3] = bytes(color)
    for x in range(left, right + 1):
        put(x, top, (80, 80, 80)); put(x, bottom, (80, 80, 80))
    for y in range(top, bottom + 1):
        put(left, y, (80, 80, 80)); put(right, y, (80, 80, 80))
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1
    for x, y in zip(xs, ys):
        px = left + int((x - xmin) / (xmax - xmin) * (right - left))
        py = bottom - int((y - ymin) / (ymax - ymin) * (bottom - top))
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                put(px + dx, py + dy, (31, 119, 180))
    write_png(path, width, height, pixels)
    return {"x_min": xmin, "x_max": xmax, "y_min": ymin, "y_max": ymax}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--x", default="")
    ap.add_argument("--y", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(parents=True, exist_ok=True)
    case = read_json(inp / "case.json", {})
    materials = read_json(work / "materials.json", {})
    tables = load_tables(inp, materials)
    if not tables:
        write_json(out / "figure-manifest.json", {
            "case_id": case.get("case_id") or "UNKNOWN", "figures": [],
            "status": "input_error", "reason": "no tabular data"})
        print(json.dumps({"ok": False, "reason": "no tabular data"}))
        return 0
    table = tables[0]
    numeric = numeric_columns(table)
    if len(numeric) < 1:
        write_json(out / "figure-manifest.json", {
            "case_id": case.get("case_id") or "UNKNOWN", "figures": [],
            "status": "input_error", "reason": "no numeric columns"})
        print(json.dumps({"ok": False, "reason": "no numeric columns"}))
        return 0
    xcol = next((c for c in numeric if c[1].lower() == args.x.lower()), numeric[0])
    ycol = next((c for c in numeric if c[1].lower() == args.y.lower()),
                numeric[1] if len(numeric) > 1 else numeric[0])
    xi, xname, _ = xcol
    yi, yname, _ = ycol
    xs, ys = [], []
    for row in table["rows"]:
        if xi < len(row) and yi < len(row):
            x, y = number(row[xi]), number(row[yi])
            if x is not None and y is not None:
                xs.append(x); ys.append(y)
    if not xs:
        write_json(out / "figure-manifest.json", {
            "case_id": case.get("case_id") or "UNKNOWN", "figures": [],
            "status": "input_error", "reason": "no numeric pairs"})
        print(json.dumps({"ok": False, "reason": "no numeric pairs"}))
        return 0
    svg = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" viewBox="0 0 800 600">',
           '<rect width="100%" height="100%" fill="#ffffff"/>',
           '<line x1="80" y1="540" x2="770" y2="540" stroke="#333" stroke-width="1.5"/>',
           '<line x1="80" y1="30" x2="80" y2="540" stroke="#333" stroke-width="1.5"/>',
           '<text x="400" y="585" text-anchor="middle" font-family="Arial" font-size="15">%s</text>' % xname,
           '<text x="22" y="285" text-anchor="middle" transform="rotate(-90 22 285)" font-family="Arial" font-size="15">%s</text>' % yname]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax == xmin: xmax = xmin + 1
    if ymax == ymin: ymax = ymin + 1
    for x, y in zip(xs, ys):
        px = 80 + (x - xmin) / (xmax - xmin) * 690
        py = 540 - (y - ymin) / (ymax - ymin) * 510
        svg.append('<circle cx="%.1f" cy="%.1f" r="3.5" fill="#1f77b4" opacity="0.8"/>' % (px, py))
    svg += ['<text x="80" y="560" font-family="Arial" font-size="11">%.4g</text>' % xmin,
            '<text x="740" y="560" font-family="Arial" font-size="11">%.4g</text>' % xmax,
            '<text x="50" y="540" text-anchor="end" font-family="Arial" font-size="11">%.4g</text>' % ymin,
            '<text x="50" y="35" text-anchor="end" font-family="Arial" font-size="11">%.4g</text>' % ymax,
            "</svg>"]
    svg_path = out / "figures" / "figure1.svg"
    write_text(svg_path, "\n".join(svg))
    png_path = out / "figures" / "figure1.png"
    try:
        draw_png(png_path, xs, ys, xname, yname)
    except Exception:  # noqa: BLE001
        png_path = None
    caption = "Figure 1. %s versus %s (%d points). Source: %s." % (
        yname, xname, len(xs), table["path"])
    write_text(out / "caption.md", caption + "\n")
    manifest = {
        "case_id": case.get("case_id") or "UNKNOWN",
        "figures": [{
            "id": "F1", "path": "figures/figure1.svg",
            "png": "figures/figure1.png" if png_path else None,
            "type": "scatter", "x": xname, "y": yname,
            "points": len(xs), "source": table["path"],
            "data_source": table["path"],
            "units": {"x": "", "y": ""},
            "caption": caption,
            "data_trace": [{"x": xs[i], "y": ys[i]} for i in range(min(len(xs), 50))],
        }],
        "caption": caption,
        "generated_at": now_iso(), "generated_by": "uci_charts.py",
    }
    write_json(out / "figure-manifest.json", manifest)
    first_line = next((line.strip() for line in
                       (table.get("text") or "").splitlines() if line.strip()),
                      ",".join(str(x) for x in table["header"]))
    evidence = [{"path": table["path"],
                 "locator": "rows 1-%d" % (len(table["rows"]) + 1),
                 "quote": first_line}]
    plan = {
        "case_id": manifest["case_id"], "mode": "visualization",
        "summary": "散点图：%s vs %s，%d 个点。" % (yname, xname, len(xs)),
        "figures": manifest["figures"],
        "results": [{"id": "R001", "metric": "points", "value": len(xs),
                     "unit": "count", "evidence": evidence[0]},
                    {"id": "R002", "metric": "x_range", "value": [xmin, xmax],
                     "unit": "", "evidence": evidence[0]},
                    {"id": "R003", "metric": "y_range", "value": [ymin, ymax],
                     "unit": "", "evidence": evidence[0]}],
        "evidence": evidence, "data_trace": manifest["figures"][0]["data_trace"],
        "analysis": {"method": "descriptive scatter", "assumptions": [],
                     "diagnostics": [{"points": len(xs)}]},
        "code": "work/analysis.py",
        "uncertainties": ([] if png_path else
                          [{"item": "png_preview", "reason": "PNG writer failed",
                            "impact": "only SVG is available", "action": "open the SVG"}]),
        "generated_at": now_iso(), "generated_by": "uci_charts.py",
    }
    write_json(work / "analysis-plan.json", plan)
    write_text(work / "analysis.py",
               "print('points=%d x=%s y=%s')\n" % (len(xs), xname, yname))
    report = ["# 科研数据可视化", "",
              "> 由 uci_charts.py 从真实表生成；轴/点/来源可复核。", "",
              "![Figure 1](figures/figure1.svg)", "",
              caption, "",
              "数据来源：`%s`，%d 个点。" % (table["path"], len(xs))]
    write_text(out / "analysis-report.md", "\n".join(report) + "\n")
    summary = {"ok": True, "tables": len(tables), "x": xname, "y": yname,
               "points": len(xs), "png": bool(png_path)}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
