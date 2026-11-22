#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a deterministic mind map (JSON + editable SVG) from supplied text.

Root = task/question; branches = document headings or top keyword clusters;
leaves = evidence sentences.  The SVG is hand-written and editable.  A PNG
preview is produced only when a renderer is available; otherwise the limit is
reported honestly.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


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
    return re.sub(r"\s+", " ", str(text or "")).strip()


def headings(text):
    out = []
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^#{1,4}\s+\S", line):
            out.append(re.sub(r"^#{1,4}\s+", "", line)[:80])
    return out


def sentences(text, limit=60):
    parts = re.split(r"(?<=[。！？.!?])\s+|\n+", norm(text))
    return [p.strip() for p in parts if 20 <= len(p.strip()) <= 300][:limit]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--max-branches", type=int, default=6)
    ap.add_argument("--max-leaves", type=int, default=4)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(inp / "case.json", {})
    materials = read_json(work / "materials.json", {})
    docs = materials.get("documents") or materials.get("materials") or []
    question = norm(case.get("question") or case.get("topic") or "")
    if not question and (inp / "task.md").is_file():
        for line in (inp / "task.md").read_text(encoding="utf-8",
                                                errors="replace").splitlines():
            if line.startswith("#"):
                question = norm(line.lstrip("#"))
                break
    question = question or "研究主题"
    branches = []
    for doc in docs[:3]:
        text = doc.get("text") or doc.get("excerpt") or ""
        path = doc.get("path") or doc.get("rel_path") or ""
        hs = headings(text)
        if not hs:
            hs = ["核心内容"]
        for heading in hs[:args.max_branches]:
            leaves = [s for s in sentences(text) if heading.lower() not in s.lower()][:args.max_leaves]
            if leaves:
                branches.append({"label": heading, "path": path, "leaves": leaves})
    branches = branches[:args.max_branches]
    if not branches:
        branches = [{"label": "材料不足", "path": "",
                     "leaves": ["未找到可展开的标题或证据句"]}]
    nodes = [{"id": "root", "label": question, "level": 0,
              "evidence": None}]
    edges = []
    for bi, branch in enumerate(branches, 1):
        bid = "b%d" % bi
        nodes.append({"id": bid, "label": branch["label"], "level": 1,
                      "evidence": {"path": branch["path"], "locator": "heading",
                                   "quote": branch["label"]}})
        edges.append({"from": "root", "to": bid})
        for li, leaf in enumerate(branch["leaves"], 1):
            nid = "%s_l%d" % (bid, li)
            nodes.append({"id": nid, "label": leaf, "level": 2,
                          "evidence": {"path": branch["path"], "locator": "sentence",
                                       "quote": leaf}})
            edges.append({"from": bid, "to": nid})
    width, height = 1400, 900
    cx, cy = width / 2, height / 2
    positions = {"root": (cx, cy)}
    n = len(branches)
    for bi, branch in enumerate(branches, 1):
        angle = 2 * math.pi * (bi - 1) / max(1, n) - math.pi / 2
        bx = cx + 300 * math.cos(angle)
        by = cy + 240 * math.sin(angle)
        positions["b%d" % bi] = (bx, by)
        for li in range(1, len(branch["leaves"]) + 1):
            lx = bx + 210 * math.cos(angle)
            ly = by + 65 * (li - (len(branch["leaves"]) + 1) / 2)
            positions["%s_l%d" % ("b%d" % bi, li)] = (lx, ly)
    def esc(text):
        return (str(text).replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (width, height, width, height),
             '<rect width="100%" height="100%" fill="#fbfcfe"/>']
    for edge in edges:
        x1, y1 = positions[edge["from"]]
        x2, y2 = positions[edge["to"]]
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#9aa7b8" stroke-width="1.6"/>' % (x1, y1, x2, y2))
    for node in nodes:
        x, y = positions[node["id"]]
        if node["level"] == 0:
            fill, radius, font = "#1f3a5f", 18, 18
        elif node["level"] == 1:
            fill, radius, font = "#2f6f8f", 13, 14
        else:
            fill, radius, font = "#5b8db8", 9, 11
        parts.append('<circle cx="%.1f" cy="%.1f" r="%d" fill="%s"/>' % (x, y, radius, fill))
        label = str(node["label"])
        if node["level"] == 2:
            label = label[:34] + "…" if len(label) > 34 else label
        parts.append('<text x="%.1f" y="%.1f" font-family="Arial, sans-serif" font-size="%d" fill="#1b2430">%s</text>' % (x + radius + 6, y + 4, font, esc(label)))
    parts.append("</svg>")
    svg = "\n".join(parts)
    write_json(out / "mindmap.json", {
        "case_id": case.get("case_id") or "UNKNOWN",
        "topic": question, "nodes": nodes, "edges": edges,
        "layout": "radial", "node_count": len(nodes),
        "generated_at": now_iso(), "generated_by": "build_mindmap.py"})
    write_text(out / "mindmap.svg", svg)
    preview = None
    try:
        import cairosvg  # type: ignore
        (out / "preview").mkdir(parents=True, exist_ok=True)
        cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                         write_to=str(out / "preview" / "mindmap.png"),
                         output_width=1400, output_height=900)
        preview = "preview/mindmap.png"
    except Exception:  # noqa: BLE001
        preview = None
    summary = {"ok": True, "nodes": len(nodes), "edges": len(edges),
               "branches": len(branches), "preview": preview}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
