#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic systematic-screening / PRISMA first pass.

Two independent rule profiles (strict and lenient) screen the same finite
record set; disagreements are routed to arbitration.  PRISMA counts close over
the records that were actually supplied; no live-database or global-recall
claim is made.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
STOP = {"the", "and", "for", "with", "from", "that", "this", "are", "was",
        "were", "has", "have", "into", "about", "which", "what", "how"}


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
    return re.sub(r"\s+", " ", str(text or "")).strip()


def tokens(text):
    return {w.lower() for w in re.findall(
        r"[A-Za-z][A-Za-z0-9/+-]{2,}|[一-鿿]{2,8}", str(text or ""))
        if w.lower() not in STOP}


def sentences(text):
    return [x.strip() for x in re.split(r"(?<=[。！？.!?])\s+|\n+", str(text or ""))
            if len(x.strip()) >= 10]


def records_from(materials):
    records = []
    for doc in materials.get("documents") or materials.get("materials") or []:
        path = doc.get("path") or doc.get("rel_path") or ""
        text = doc.get("text") or doc.get("excerpt") or ""
        data = doc.get("json")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            for item in data:
                records.append({"path": path, "item": item, "text": text})
            continue
        lines = [x for x in text.splitlines() if x.strip()]
        if len(lines) >= 2:
            delim = "\t" if "\t" in lines[0] else (
                ";" if ";" in lines[0] and "," not in lines[0] else ",")
            try:
                parsed = list(csv.reader(io.StringIO(text), delimiter=delim))
            except csv.Error:
                parsed = []
            if len(parsed) >= 2 and len(parsed[0]) >= 2:
                header = [str(x).strip() for x in parsed[0]]
                for row in parsed[1:]:
                    records.append({"path": path, "item": dict(zip(header, row)),
                                    "text": text})
                continue
        records.append({"path": path, "item": {"title": (sentences(text) or [path])[0][:160],
                                                "abstract": text[:2000]},
                        "text": text})
    return records


def evaluate(text, q_tokens, include_terms, exclude_terms, threshold):
    lowered = text.lower()
    score = len(q_tokens & tokens(text)) / max(1, len(q_tokens))
    excluded = [term for term in exclude_terms if term.lower() in lowered]
    included = [term for term in include_terms if term.lower() in lowered]
    if excluded:
        return "exclude", "exclusion_criterion", score
    if included or score >= threshold:
        return "include", "include_criterion" if included else "relevance_threshold", score
    if score > 0:
        return "uncertain", "below_threshold", score
    return "exclude", "no_relevance", score


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--strict", type=float, default=0.45)
    ap.add_argument("--lenient", type=float, default=0.28)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(inp / "case.json", {})
    materials = read_json(work / "materials.json", {})
    question = norm(case.get("question") or case.get("research_question") or "")
    if not question and (inp / "task.md").is_file():
        for line in (inp / "task.md").read_text(encoding="utf-8",
                                                errors="replace").splitlines():
            if line.startswith("#"):
                question = norm(line.lstrip("#"))
                break
    question = question or "未提供明确研究问题"
    include_terms = [norm(x) for x in (case.get("include_criteria") or
                                       case.get("inclusion_criteria") or [])
                     if norm(x)]
    exclude_terms = [norm(x) for x in (case.get("exclude_criteria") or
                                       case.get("exclusion_criteria") or [])
                     if norm(x)]
    records = records_from(materials)
    q_tokens = tokens(question + " " + " ".join(include_terms))
    seen, deduped, duplicates = {}, [], []
    for index, record in enumerate(records, 1):
        item = record["item"]
        title = norm(item.get("title") or item.get("name") or "")
        doi = norm(item.get("doi") or item.get("DOI") or "")
        if not doi:
            match = DOI_RE.search(record["text"])
            doi = match.group(0).lower() if match else ""
        key = doi.lower() if doi else re.sub(r"[^a-z0-9]+", " ",
                                             title.lower()).strip()
        rid = "R%03d" % index
        if key and key in seen:
            duplicates.append({"id": rid, "duplicate_of": seen[key], "key": key})
            continue
        if key:
            seen[key] = rid
        abstract = norm(item.get("abstract") or item.get("summary") or record["text"])
        text = title + " " + abstract
        strict_label, strict_reason, score = evaluate(
            text, q_tokens, include_terms, exclude_terms, args.strict)
        lenient_label, lenient_reason, _ = evaluate(
            text, q_tokens, include_terms, exclude_terms, args.lenient)
        final = strict_label if strict_label == lenient_label else "arbitration"
        quote_source = abstract or title
        best = max(sentences(quote_source)[:20],
                   key=lambda s: len(q_tokens & tokens(s)), default=quote_source)
        deduped.append({
            "id": rid, "path": record["path"], "title": title, "doi": doi,
            "score": round(score, 4),
            "reviewer_a": {"label": strict_label, "reason_code": strict_reason},
            "reviewer_b": {"label": lenient_label, "reason_code": lenient_reason},
            "final": final,
            "arbitration_required": final == "arbitration",
            "reason": "two rule profiles; disagreements need human arbitration",
            "evidence": {"path": record["path"], "locator": "S1",
                         "quote": best},
        })
    arbitration = [{"record_id": row["id"],
                    "reviewer_a": row["reviewer_a"],
                    "reviewer_b": row["reviewer_b"],
                    "question": "read the record and decide include/exclude"}
                   for row in deduped if row["arbitration_required"]]
    included = [row["id"] for row in deduped if row["final"] == "include"]
    excluded = [row["id"] for row in deduped if row["final"] == "exclude"]
    prisma = {
        "identified": len(records),
        "duplicates_removed": len(duplicates),
        "screened": len(deduped),
        "excluded_title_abstract": len(excluded),
        "included": len(included),
        "arbitration_pending": len(arbitration),
        "closure_ok": len(records) == len(duplicates) + len(deduped),
    }
    plan = read_json(work / "search-plan.json", {})
    if not isinstance(plan, dict):
        plan = {}
    plan.update({
        "case_id": case.get("case_id") or "UNKNOWN",
        "mode": "systematic-screening", "question": question,
        "summary": "系统筛选：识别 %d，去重 %d，筛选 %d，纳入 %d，待仲裁 %d。" % (
            prisma["identified"], prisma["duplicates_removed"],
            prisma["screened"], prisma["included"],
            prisma["arbitration_pending"]),
        "protocol": {"question": question, "include_criteria": include_terms,
                     "exclude_criteria": exclude_terms,
                     "reviewers": ["strict-rule", "lenient-rule"],
                     "arbitration": "human review of disagreements"},
        "records": [{"id": row["id"], "title": row["title"], "doi": row["doi"],
                     "path": row["path"]} for row in deduped],
        "screening": deduped, "arbitration": arbitration,
        "prisma_counts": prisma,
        "queries": plan.get("queries") or [{
            "id": "Q1", "round": 1, "query": question,
            "source": "provided corpus", "purpose": "screening",
            "stop_condition": "all supplied records screened"}],
        "evidence": [row["evidence"] for row in deduped],
        "uncertainties": [{"item": "rule_based_screening",
                           "reason": "two deterministic rule profiles are used",
                           "impact": "arbitration records are not human decisions",
                           "action": "a human must resolve every arbitration record"}],
        "generated_at": now_iso(), "generated_by": "systematic_screen.py",
    })
    write_json(work / "search-plan.json", plan)
    write_json(out / "search-results.json", {
        "case_id": plan["case_id"], "summary": plan["summary"],
        "protocol": plan["protocol"], "prisma_counts": prisma,
        "records": plan["records"], "screening": deduped,
        "arbitration": arbitration, "evidence": plan["evidence"]})
    write_json(out / "search-log.json", {
        "case_id": plan["case_id"], "queries": plan["queries"],
        "prisma_counts": prisma, "generated_at": now_iso()})
    write_json(out / "evidence.json", {
        "case_id": plan["case_id"], "evidence": plan["evidence"]})
    with (out / "candidates.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["record_id", "title", "doi", "reviewer_a", "reviewer_b",
                         "final", "reason_code", "score"])
        for row in deduped:
            writer.writerow([row["id"], row["title"], row["doi"],
                             row["reviewer_a"]["label"], row["reviewer_b"]["label"],
                             row["final"], row["reviewer_a"]["reason_code"],
                             row["score"]])
    summary = {"ok": True, "identified": prisma["identified"],
               "duplicates": prisma["duplicates_removed"],
               "screened": prisma["screened"], "included": prisma["included"],
               "arbitration": prisma["arbitration_pending"],
               "closure_ok": prisma["closure_ok"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
