#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic multi-document comparison first pass.

Extracts one evidence sentence per document per dimension (question, data,
method, metric, conclusion, limitation), marks not_reported explicitly, and
builds agreements/divergences/gaps from those cells.  It never adds a fact that
is absent from every supplied document.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

DIMENSIONS = {
    "question": r"aim|objective|question|investigat|study|we ask|hypothes",
    "data": r"dataset|sample|participants|patients|cohort|n\s*=|corpus|materials",
    "method": r"method|approach|model|algorithm|we used|procedure|protocol",
    "metric": r"metric|accuracy|f1|sensitivity|specificity|selectivity|yield|outcome|measure",
    "conclusion": r"conclusion|we found|results show|demonstrat|suggest|indicate",
    "limitation": r"limitation|future work|caveat|not measured|remains? unknown|small sample",
}


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


def sentences(text):
    return [x.strip() for x in re.split(r"(?<=[。！？.!?])\s+|\n+", str(text or ""))
            if 20 <= len(x.strip()) <= 500]


def title_of(text, fallback):
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return norm(line.lstrip("#"))
        if 20 <= len(line) <= 200:
            return norm(line)
    return fallback


def best_cell(text, pattern):
    best = None
    for index, sentence in enumerate(sentences(text), 1):
        score = len(re.findall(pattern, sentence, re.I))
        if score and (best is None or score > best[0]):
            best = (score, index, sentence)
    if best:
        return {"value": best[2], "status": "reported",
                "locator": {"paragraph_id": "S%d" % best[1]},
                "quote": best[2]}
    return {"value": None, "status": "not_reported",
            "locator": {"paragraph_id": None}, "quote": None}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(inp / "case.json", {})
    docs = []
    for path in sorted(inp.rglob("*")):
        if not path.is_file() or path.name in ("case.json", "task.md"):
            continue
        if "environment" in path.parts:
            continue
        if path.suffix.lower() not in (".md", ".txt", ".tex"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text.strip()) < 80:
            continue
        rel = path.relative_to(inp).as_posix()
        docs.append({"doc_id": "D%03d" % (len(docs) + 1), "path": rel,
                     "title": title_of(text, Path(rel).stem), "text": text})
    if not docs:
        write_json(out / "result.json", {
            "case_id": case.get("case_id") or "UNKNOWN",
            "status": "input_error", "documents": [], "comparison_table": [],
            "synthesis": {"agreements": [], "divergences": [], "gaps": []},
            "conflicts": [], "uncertainty": ["no readable documents"],
            "change_log": []})
        print(json.dumps({"ok": False, "reason": "no readable documents"}))
        return 0
    table = []
    evidence = []
    for dimension, pattern in DIMENSIONS.items():
        cells = []
        for doc in docs:
            cell = best_cell(doc["text"], pattern)
            cell["doc_id"] = doc["doc_id"]
            cells.append(cell)
            if cell["quote"]:
                evidence.append({"path": doc["path"],
                                 "locator": cell["locator"]["paragraph_id"],
                                 "quote": cell["quote"]})
        table.append({"dimension": dimension, "cells": cells})
    agreements, divergences, gaps = [], [], []
    for row in table:
        reported = [c for c in row["cells"] if c["status"] == "reported"]
        if len(reported) >= 2:
            agreements.append({
                "text": "%s dimension is reported by %d documents; compare the exact definitions before treating them as equivalent" % (
                    row["dimension"], len(reported)),
                "support": [{"doc_id": c["doc_id"],
                             "paragraph_id": c["locator"]["paragraph_id"]}
                            for c in reported]})
        elif reported:
            gaps.append({"text": "%s is reported by only one document" % row["dimension"],
                         "affected_dimension": row["dimension"]})
        else:
            gaps.append({"text": "no document reports %s" % row["dimension"],
                         "affected_dimension": row["dimension"]})
    # Divergence when two metric/conclusion cells contain different numeric values.
    for dimension in ("metric", "conclusion"):
        row = next((r for r in table if r["dimension"] == dimension), None)
        if not row:
            continue
        values = []
        for cell in row["cells"]:
            if cell["status"] == "reported":
                nums = re.findall(r"[-+]?\d+(?:\.\d+)?\s*%?", cell["quote"])
                if nums:
                    values.append((cell["doc_id"], nums[:3], cell["quote"]))
        if len(values) >= 2 and len({tuple(v[1]) for v in values}) > 1:
            divergences.append({
                "text": "%s values differ across documents; check metric definition, unit and condition" % dimension,
                "sides": [{"doc_id": d, "claim": quote} for d, _, quote in values],
                "resolution": "unresolved",
            })
    synthesis = {"agreements": agreements, "divergences": divergences,
                 "gaps": gaps}
    conflicts = divergences
    payload = {
        "case_id": case.get("case_id") or "UNKNOWN",
        "status": "answered" if any(c["status"] == "reported"
                                    for row in table for c in row["cells"]) else "partial",
        "documents": [{"doc_id": d["doc_id"], "title": d["title"],
                       "type": "research", "path": d["path"]} for d in docs],
        "comparison_table": table, "synthesis": synthesis,
        "conflicts": conflicts, "evidence": evidence[:200],
        "uncertainty": [{"item": "deterministic_sentence_match",
                         "reason": "cells use the best keyword-matching sentence",
                         "impact": "read each quote and align metric definitions",
                         "action": "confirm or replace every cell"}],
        "change_log": [{"step": 1, "action": "compare_documents.py draft",
                        "evidence": "supplied documents"}],
        "generated_at": now_iso(),
    }
    write_json(work / "comparison-draft.json", payload)
    write_json(out / "result.json", payload)
    write_json(out / "comparison-table.json", {
        "case_id": payload["case_id"], "documents": payload["documents"],
        "comparison_table": table})
    report = ["# 多文档比较与整合", "",
              "> 由 compare_documents.py 从逐字句生成；每个单元格需人工确认口径。", ""]
    for row in table:
        report.append("## %s" % row["dimension"])
        for cell in row["cells"]:
            if cell["status"] == "reported":
                report.append("- **%s**：%s（%s）" % (
                    cell["doc_id"], cell["quote"],
                    cell["locator"]["paragraph_id"]))
            else:
                report.append("- **%s**：not_reported" % cell["doc_id"])
        report.append("")
    report += ["## 一致结论", ""]
    report += [("- %s" % x["text"]) for x in agreements] or ["- 无"]
    report += ["", "## 分歧", ""]
    report += [("- %s" % x["text"]) for x in divergences] or ["- 无"]
    report += ["", "## 证据缺口", ""]
    report += [("- %s" % x["text"]) for x in gaps] or ["- 无"]
    write_text(out / "synthesis.md", "\n".join(report) + "\n")
    summary = {"ok": True, "documents": len(docs), "dimensions": len(table),
               "agreements": len(agreements), "divergences": len(divergences),
               "gaps": len(gaps)}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
