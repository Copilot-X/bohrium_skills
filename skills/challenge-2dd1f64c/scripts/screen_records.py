#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic screening/ranking first pass for a finite record set.

Reads structured records from JSON/CSV when present, otherwise treats each
document as one record.  Labels are explicitly rule-based (keep/uncertain/
exclude), evidence is a verbatim source sentence, and duplicates are grouped by
DOI or normalized title.  Metrics without a reference set are marked
not_computable, never invented.
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
STOP = {"what", "which", "is", "the", "of", "at", "in", "a", "an", "and",
        "does", "do", "how", "why", "for", "with", "to", "from", "by",
        "on", "that", "this", "are", "was", "were", "be", "as"}


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
    return [part.strip() for part in re.split(r"(?<=[。！？.!?])\s+|\n+",
                                              norm(text))
            if len(part.strip()) >= 10]


def parse_records(materials):
    records, docs = [], []
    for doc in materials.get("documents") or materials.get("materials") or []:
        path = doc.get("path") or doc.get("rel_path") or ""
        text = doc.get("text") or doc.get("excerpt") or ""
        data = doc.get("json")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            for item in data:
                records.append({"path": path, "item": item, "text": text})
            continue
        header, rows = [], []
        if text:
            lines = [x for x in text.splitlines() if x.strip()]
            if len(lines) >= 2:
                delimiter = "\t" if "\t" in lines[0] else (
                    ";" if ";" in lines[0] and "," not in lines[0] else ",")
                try:
                    parsed = list(csv.reader(io.StringIO(text), delimiter=delimiter))
                except csv.Error:
                    parsed = []
                if len(parsed) >= 2 and len(parsed[0]) >= 2:
                    header, rows = [str(x).strip() for x in parsed[0]], parsed[1:]
        if header and rows:
            for row in rows:
                item = dict(zip(header, row))
                records.append({"path": path, "item": item, "text": text})
        else:
            docs.append({"path": path, "text": text})
    if not records:
        for doc in docs:
            records.append({"path": doc["path"], "item": {
                "title": (sentences(doc["text"]) or [doc["path"]])[0][:160],
                "abstract": doc["text"][:2000]}, "text": doc["text"]})
    return records


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--keep-threshold", type=float, default=0.35)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(inp / "case.json", {})
    materials = read_json(work / "materials.json", {})
    question = norm(case.get("question") or case.get("research_question") or "")
    if not question:
        task = inp / "task.md"
        if task.is_file():
            for line in task.read_text(encoding="utf-8",
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
    records = parse_records(materials)
    q_tokens = tokens(question + " " + " ".join(include_terms))
    candidates, evidence = [], []
    seen = {}
    for index, record in enumerate(records, 1):
        item = record["item"]
        title = norm(item.get("title") or item.get("name") or "")
        abstract = norm(item.get("abstract") or item.get("summary") or
                        item.get("text") or record["text"])
        doi = (item.get("doi") or item.get("DOI") or
               (DOI_RE.search(abstract).group(0) if DOI_RE.search(abstract) else ""))
        text = title + " " + abstract
        score = len(q_tokens & tokens(text)) / max(1, len(q_tokens))
        lowered = text.lower()
        excluded = [term for term in exclude_terms
                    if term.lower() in lowered]
        included = [term for term in include_terms
                    if term.lower() in lowered]
        if excluded:
            label = "exclude"
        elif score >= args.keep_threshold or included:
            label = "keep"
        elif score > 0:
            label = "uncertain"
        else:
            label = "exclude"
        quote_source = abstract or title
        best = None
        for sentence in sentences(quote_source)[:20]:
            overlap = len(q_tokens & tokens(sentence))
            if best is None or overlap > best[0]:
                best = (overlap, sentence)
        quote = best[1] if best else (
            sentences(quote_source) or [quote_source])[0]
        ev = {"path": record["path"], "locator": "S1", "quote": quote}
        evidence.append(ev)
        key = norm(doi.lower()) if doi else norm(title.lower())
        duplicate_of = seen.get(key)
        if key and not duplicate_of:
            seen[key] = "C%03d" % index
        candidates.append({
            "id": "C%03d" % index,
            "title": title or "(untitled)",
            "year": item.get("year") or "",
            "doi": doi,
            "label": label,
            "score": round(score, 4),
            "confidence": "high" if label == "keep" and score >= 0.5 else (
                "medium" if label != "exclude" else "low"),
            "reason": "rule-based lexical overlap; %s" % (
                "exclude criterion matched" if excluded else
                "include criterion matched" if included else
                "no criterion matched"),
            "duplicate_of": duplicate_of,
            "escalated_to_human": label == "uncertain",
            "evidence": ev,
        })
    candidates.sort(key=lambda row: (-row["score"], row["title"].lower()))
    for rank, row in enumerate(candidates, 1):
        row["rank"] = rank
        row["tie_break"] = "title"
    keep = [row["id"] for row in candidates if row["label"] == "keep"]
    ranked_keep = keep[:args.k]
    screening = {
        "include": keep,
        "exclude": [row["id"] for row in candidates if row["label"] == "exclude"],
        "uncertain": [row["id"] for row in candidates if row["label"] == "uncertain"],
    }
    plan = read_json(work / "search-plan.json", {})
    if not isinstance(plan, dict):
        plan = {}
    plan.update({
        "case_id": case.get("case_id") or "UNKNOWN",
        "mode": "screening-ranking", "question": question,
        "summary": "规则化筛选：%d 条记录，keep %d / uncertain %d / exclude %d；前 %d 条为 ranked_keep。" % (
            len(candidates), len(screening["include"]),
            len(screening["uncertain"]), len(screening["exclude"]),
            len(ranked_keep)),
        "queries": plan.get("queries") or [{
            "id": "Q1", "round": 1, "query": question,
            "source": "provided corpus", "purpose": "screening",
            "stop_condition": "all records screened"}],
        "candidates": candidates, "screening": screening,
        "ranked_keep": ranked_keep,
        "decision_rules": [
            {"rule": "exclude if any explicit exclusion criterion appears in title/abstract",
             "action": "exclude"},
            {"rule": "keep if lexical overlap with question >= %.2f or include criterion matches" % args.keep_threshold,
             "action": "keep"},
            {"rule": "uncertain if 0 < overlap < threshold; escalate to human",
             "action": "uncertain"},
        ],
        "metrics": {"k": args.k, "precision_at_k": None,
                    "ndcg_at_k": None, "recall_at_k": None,
                    "not_computable": "no frozen reference set in the input",
                    "definition": "metrics require a gold relevance set"},
        "evidence": evidence[:500],
        "uncertainties": [{"item": "rule_based_labels",
                           "reason": "labels use lexical overlap and explicit criteria only",
                           "impact": "uncertain and near-threshold records need human review",
                           "action": "read the cited sentence and confirm each keep/exclude"}],
        "generated_at": now_iso(), "generated_by": "screen_records.py",
    })
    write_json(work / "search-plan.json", plan)
    write_json(out / "search-results.json", {
        "case_id": plan["case_id"], "question": question,
        "summary": plan["summary"], "candidates": candidates,
        "screening": screening, "ranked_keep": ranked_keep,
        "metrics": plan["metrics"], "evidence": evidence[:500],
        "decision_rules": plan["decision_rules"]})
    write_json(out / "search-log.json", {
        "case_id": plan["case_id"], "queries": plan["queries"],
        "screening": screening, "generated_at": now_iso()})
    write_json(out / "evidence.json", {
        "case_id": plan["case_id"], "evidence": evidence[:500]})
    with (out / "candidates.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "title", "year", "doi", "label", "rank", "score",
                         "confidence", "duplicate_of", "reason"])
        for row in candidates:
            writer.writerow([row["id"], row["title"], row["year"], row["doi"],
                             row["label"], row["rank"], row["score"],
                             row["confidence"], row["duplicate_of"] or "",
                             row["reason"]])
    summary = {"ok": bool(candidates), "records": len(candidates),
               "keep": len(screening["include"]),
               "uncertain": len(screening["uncertain"]),
               "exclude": len(screening["exclude"]),
               "ranked_keep": len(ranked_keep)}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
