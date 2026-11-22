#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic coverage calculation for a frozen reference set.

The reference set is read from the case/work/material data and is never
back-filled from the results.  Missing identifiers stay missing and are
reported as categories, not guessed.  With no structured reference set the
script emits an honest skeleton with ``coverage.not_computable``.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")


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


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def first_doi(text):
    match = DOI_RE.search(str(text or ""))
    return match.group(0).lower() if match else ""


def key_of(item):
    if not isinstance(item, dict):
        return norm(item)
    return (norm(item.get("doi") or item.get("DOI") or "")
            or norm(item.get("id") or item.get("record_id")
                    or item.get("reference_id") or "")
            or norm(item.get("title") or item.get("name") or ""))


def title_of(item):
    if not isinstance(item, dict):
        return norm(item)
    return norm(item.get("title") or item.get("name") or item.get("label") or "")


def as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "records", "candidates", "references",
                    "reference_set", "gold_set", "expected", "relevant"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def collect(case, materials):
    sources = [case]
    for doc in materials.get("documents") or []:
        if isinstance(doc.get("json"), (dict, list)):
            sources.append(doc["json"])
        if isinstance(doc.get("rows"), list) and doc["rows"]:
            header = [str(x) for x in doc["rows"][0]]
            rows = []
            for row in doc["rows"][1:]:
                rows.append(dict(zip(header, row)))
            sources.append(rows)
    references, candidates = [], []
    for source in sources:
        if isinstance(source, list):
            for item in source:
                if isinstance(item, dict) and any(
                        k in item for k in ("eligible", "hit", "included",
                                            "relevant", "gold")):
                    references.append(item)
                elif isinstance(item, dict):
                    candidates.append(item)
            continue
        if not isinstance(source, dict):
            continue
        for key in ("reference_set", "gold_set", "expected", "relevant",
                    "references", "eligible_references"):
            if isinstance(source.get(key), list):
                references.extend(source[key])
        for key in ("candidates", "records", "search_results", "results",
                    "items", "hits"):
            if isinstance(source.get(key), list):
                candidates.extend(source[key])
    return references, candidates


def evidence_for(materials, needle):
    needle = norm(needle)[:80]
    if not needle:
        return None
    for doc in materials.get("documents") or []:
        path = doc.get("path") or doc.get("rel_path") or ""
        for index, line in enumerate((doc.get("text") or "").splitlines(), 1):
            if needle and needle in norm(line):
                return {"path": path, "locator": "L%d" % index,
                        "quote": line.strip()}
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(inp / "case.json", {})
    materials = read_json(work / "materials.json", {})
    references, candidates = collect(case, materials)
    plan = read_json(work / "search-plan.json", {})
    if not isinstance(plan, dict):
        plan = {}
    references = plan.get("reference_set") or references
    candidates = plan.get("candidates") or candidates
    by_key = {}
    for item in candidates:
        if isinstance(item, dict):
            for key in (key_of(item), title_of(item)):
                if key:
                    by_key.setdefault(key, item)
    rows, misses = [], []
    hit = 0
    for item in references:
        key, title = key_of(item), title_of(item)
        match = by_key.get(key) or by_key.get(title)
        found = match is not None
        hit += 1 if found else 0
        evidence = evidence_for(materials, title or key)
        row = {
            "id": (item.get("id") or item.get("reference_id")
                   or item.get("record_id") or "R%03d" % (len(rows) + 1))
            if isinstance(item, dict) else "R%03d" % (len(rows) + 1),
            "eligible": bool(item.get("eligible", True))
            if isinstance(item, dict) else True,
            "hit": found,
            "matched_candidate": (match.get("id") or match.get("record_id")
                                  or title_of(match)) if match else None,
            "evidence": evidence,
        }
        rows.append(row)
        if not found:
            category = "query_gap"
            if isinstance(item, dict) and not (
                    item.get("doi") or item.get("title")):
                category = "metadata_gap"
            misses.append({
                "id": row["id"], "category": category,
                "cause": "not present in the retrieved candidate set",
                "query_or_field": "query coverage / candidate metadata",
                "evidence": evidence,
            })
    denominator = sum(1 for r in rows if r["eligible"])
    numerator = sum(1 for r in rows if r["eligible"] and r["hit"])
    if denominator:
        coverage = {
            "numerator": numerator, "denominator": denominator,
            "ratio": round(numerator / denominator, 4),
            "definition": "unique eligible reference items with a verified hit / unique eligible reference items",
        }
    else:
        coverage = {
            "numerator": 0, "denominator": 0, "ratio": None,
            "not_computable": "no structured reference set was supplied",
        }
    evidence = [r["evidence"] for r in rows if r.get("evidence")]
    plan.update({
        "case_id": case.get("case_id") or plan.get("case_id") or "UNKNOWN",
        "mode": "coverage-analysis",
        "summary": ("覆盖率初稿：参考集 %d 条，命中 %d 条，覆盖率 %s。" % (
            denominator, numerator,
            ("%.2f%%" % (100 * coverage["ratio"])) if coverage["ratio"] is not None
            else "not_computable")),
        "reference_set": rows,
        "coverage": coverage,
        "misses": misses,
        "supplement": {"query_id": None, "new_hits": [],
                       "marginal_gain": 0,
                       "note": "run a targeted supplement query and record new hits"},
        "residual_misses": [m["id"] for m in misses],
        "candidates": candidates,
        "evidence": evidence,
        "uncertainties": ([{"item": "coverage_not_computable",
                            "reason": coverage["not_computable"],
                            "impact": "no recall estimate can be reported",
                            "action": "supply a frozen reference set"}]
                          if not denominator else []),
        "generated_at": now_iso(), "generated_by": "domain_work.py",
    })
    write_json(work / "search-plan.json", plan)
    result = {
        "case_id": plan["case_id"], "summary": plan["summary"],
        "coverage": coverage, "reference_set": rows, "misses": misses,
        "supplement": plan["supplement"], "residual_misses": plan["residual_misses"],
        "evidence": evidence, "status": "answered" if denominator else "partial",
        "derived_from": "work/search-plan.json",
    }
    write_json(out / "search-results.json", result)
    write_json(out / "search-log.json", {
        "case_id": plan["case_id"], "queries": plan.get("queries") or [],
        "coverage": coverage, "generated_at": now_iso()})
    write_json(out / "evidence.json", {
        "case_id": plan["case_id"], "evidence": evidence})
    with (out / "candidates.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["reference_id", "eligible", "hit", "category"])
        for row in rows:
            category = next((m["category"] for m in misses
                             if m["id"] == row["id"]), "hit" if row["hit"] else "")
            writer.writerow([row["id"], row["eligible"], row["hit"], category])
    summary = {"ok": bool(denominator), "references": denominator,
               "hits": numerator, "misses": len(misses),
               "ratio": coverage["ratio"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
