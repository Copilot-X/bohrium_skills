#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reading 14 — atomic assertions, minimal evidence and machine-readable nanopublications."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

import skillcore as S

RELATIONS = {
    "CAUSES", "ASSOCIATED_WITH", "CORRELATES_WITH", "INHIBITS", "ACTIVATES",
    "INCREASES", "DECREASES", "REGULATES", "BINDS", "EXPRESSED_IN",
    "LOCATED_IN", "IS_A", "PART_OF", "USES", "MEASURED_BY", "PREDICTS",
    "SUPPORTS", "CONTRADICTS", "COMPARES_WITH", "OUTPERFORMS", "DERIVED_FROM",
    "ENHANCES", "REDUCES", "MODULATES", "INTERACTS_WITH", "CO_OCCURS_WITH",
    "HAS_PART", "HAS_PROPERTY", "MEASURED_AS", "EXPRESSES", "ENCODES",
    "TREATS", "PREVENTS", "INDUCES", "REPRESSES",
}


def _primary_doc(ctx):
    best = None
    for doc in ctx.docs:
        text = S.doc_text(doc)
        if text.strip() and (best is None or len(text) > len(S.doc_text(best))):
            best = doc
    return best


def _paper_meta(ctx, doc):
    case = ctx.case or {}
    text = S.doc_text(doc)
    title = case.get("title") or case.get("paper_title") or ""
    doi = case.get("doi") or ""
    if not doi:
        match = S.DOI_RE.search(text)
        doi = match.group(0) if match else ""
    authors = case.get("authors") or []
    if isinstance(authors, str):
        authors = [x.strip() for x in re.split(r";|,", authors) if x.strip()]
    year = str(case.get("year") or "")
    if not year:
        match = re.search(r"\b(?:19|20)\d{2}\b", text)
        year = match.group(0) if match else ""
    return {"paper_id": ctx.case_id, "title": title, "authors": authors,
            "year": year, "doi": doi, "version": str(case.get("version") or "1"),
            "source_path": doc.get("rel_path")}


def prepare(ctx):
    doc = _primary_doc(ctx)
    if doc is None:
        ctx.result.change("prepare", "input", "no readable paper")
        ctx.result.uncertainty("paper", "no readable source text",
                               "assertions cannot be grounded", "provide the paper")
        return ctx.result.finalize("input_error")
    text = S.doc_text(doc)
    segments = S.segment_blocks(text, source_id=doc.get("source_id", "SRC01"),
                                path=doc.get("rel_path", ""))
    paragraphs = [{"paragraph_id": s["id"], "locator": s["locator"],
                   "text": s["text"], "page": s.get("page")} for s in segments]
    meta = _paper_meta(ctx, doc)
    S.write_json(ctx.work / "paragraphs.json", {
        "case_id": ctx.case_id, "source_path": doc.get("rel_path"),
        "source_id": doc.get("source_id"), "metadata": meta,
        "paragraphs": paragraphs,
    })
    S.write_json(ctx.work / "assertions-template.json", {
        "case_id": ctx.case_id, "source_metadata": meta,
        "assertions": [{
            "id": "A001", "assertion": "one falsifiable claim",
            "subject": {"label": "", "type": "material|method|finding|metric|organism|process"},
            "relation": "CAUSES",
            "object": {"label": "", "type": "material|method|finding|metric|organism|process"},
            "conditions": [], "evidence": {"path": doc.get("rel_path"),
                                           "locator": "P001", "quote": ""},
            "evidence_type": "direct|indirect|inferred",
            "confidence": "high|moderate|low", "limitations": []
        }],
        "unresolved": [],
        "relation_vocabulary": sorted(RELATIONS),
    })
    ctx.result.change("paragraphs", doc.get("rel_path", ""),
                      "%d paragraphs indexed" % len(paragraphs))
    return ctx.provisional(reason="paragraphs indexed; write assertions.json, then run finalize")


def _norm_relation(value):
    value = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "")).strip("_").upper()
    return value


def _quote_problem(paragraphs, evidence):
    if not isinstance(evidence, dict):
        return "evidence is not an object"
    path = evidence.get("path") or ""
    locator = str(evidence.get("locator") or "")
    quote = evidence.get("quote") or ""
    paragraph = None
    for item in paragraphs:
        if locator and locator in (item.get("locator") or "", item.get("paragraph_id") or ""):
            paragraph = item
            break
    if paragraph is None:
        # Fall back to a path-wide quote check.
        text = "\n\n".join(p.get("text", "") for p in paragraphs)
        ok, reason = S.quote_supported(text, quote)
        return "" if ok else reason
    ok, reason = S.quote_supported(paragraph.get("text", ""), quote)
    return "" if ok else reason


def finalize(ctx):
    payload = S.load_json(ctx.work / "assertions.json", {})
    assertions = payload.get("assertions") if isinstance(payload, dict) else None
    if not assertions:
        ctx.result.change("finalize", "output", "assertions.json missing or empty")
        ctx.result.uncertainty("assertions", "model-authored assertions.json is missing",
                               "no machine-readable assertions", "write assertions.json")
        return ctx.result.finalize("partial")
    paragraphs_payload = S.load_json(ctx.work / "paragraphs.json", {})
    paragraphs = paragraphs_payload.get("paragraphs") or []
    meta = payload.get("source_metadata") or paragraphs_payload.get("metadata") or {}
    problems, quality = [], []
    seen_ids = set()
    normalized = []
    for index, item in enumerate(assertions, 1):
        if not isinstance(item, dict):
            problems.append("assertion %d is not an object" % index)
            continue
        aid = str(item.get("id") or "A%03d" % index)
        if aid in seen_ids:
            problems.append("duplicate assertion id: %s" % aid)
        seen_ids.add(aid)
        assertion = str(item.get("assertion") or "").strip()
        subject = item.get("subject") or {}
        obj = item.get("object") or {}
        relation = _norm_relation(item.get("relation"))
        evidence = item.get("evidence") or {}
        atomic = S.atomicity_issues(assertion)
        if atomic:
            quality.append({"id": aid, "issues": atomic})
        if not assertion:
            problems.append("%s: empty assertion" % aid)
        if not isinstance(subject, dict) or not subject.get("label"):
            problems.append("%s: subject label missing" % aid)
        if not isinstance(obj, dict) or not obj.get("label"):
            problems.append("%s: object label missing" % aid)
        if relation not in RELATIONS:
            problems.append("%s: relation '%s' not in controlled vocabulary" % (aid, relation))
        quote_problem = _quote_problem(paragraphs, evidence)
        if quote_problem:
            problems.append("%s: %s" % (aid, quote_problem))
        row = dict(item)
        row["id"] = aid
        row["relation"] = relation
        row["atomicity_issues"] = atomic
        normalized.append(row)

    nanopubs = []
    for item in normalized:
        evidence = item.get("evidence") or {}
        np_id = "np:" + hashlib.sha256(
            (ctx.case_id + item["id"] + item.get("assertion", "")).encode("utf-8")
        ).hexdigest()[:24]
        nanopubs.append({
            "nanopub_id": np_id,
            "assertion": {
                "subject": item.get("subject"),
                "relation": item.get("relation"),
                "object": item.get("object"),
                "text": item.get("assertion"),
                "conditions": item.get("conditions") or [],
            },
            "provenance": {
                "source_id": meta.get("paper_id"),
                "title": meta.get("title"),
                "authors": meta.get("authors"),
                "year": meta.get("year"),
                "doi": meta.get("doi"),
                "version": meta.get("version"),
                "path": evidence.get("path"),
                "locator": evidence.get("locator"),
                "quote": evidence.get("quote"),
                "evidence_type": item.get("evidence_type", "direct"),
            },
            "publication_info": {
                "case_id": ctx.case_id,
                "assertion_id": item.get("id"),
                "confidence": item.get("confidence", "moderate"),
                "limitations": item.get("limitations") or [],
            },
        })
    S.write_json(ctx.output / "nanopublications.json", {
        "case_id": ctx.case_id, "source_metadata": meta,
        "nanopublications": nanopubs, "count": len(nanopubs),
    })
    S.write_json(ctx.output / "assertions.json", {
        "case_id": ctx.case_id, "source_metadata": meta,
        "assertions": normalized, "unresolved": payload.get("unresolved") or [],
    })
    with (ctx.output / "assertions.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "subject", "relation", "object", "assertion",
                         "evidence_path", "locator", "quote", "evidence_type", "confidence"])
        for item in normalized:
            ev = item.get("evidence") or {}
            writer.writerow([item.get("id"), (item.get("subject") or {}).get("label"),
                             item.get("relation"), (item.get("object") or {}).get("label"),
                             item.get("assertion"), ev.get("path"), ev.get("locator"),
                             ev.get("quote"), item.get("evidence_type"),
                             item.get("confidence")])
    lines = ["# Atomic assertions", ""]
    for item in normalized:
        ev = item.get("evidence") or {}
        lines.append("## %s" % item.get("id"))
        lines.append("")
        lines.append("**Assertion:** %s" % item.get("assertion"))
        lines.append("")
        lines.append("- Subject: %s (%s)" % ((item.get("subject") or {}).get("label"),
                                             (item.get("subject") or {}).get("type", "")))
        lines.append("- Relation: `%s`" % item.get("relation"))
        lines.append("- Object: %s (%s)" % ((item.get("object") or {}).get("label"),
                                            (item.get("object") or {}).get("type", "")))
        lines.append("- Evidence: `%s` — %s" % (ev.get("locator", ""),
                                                 S.excerpt(ev.get("quote", ""), 180)))
        lines.append("")
    S.write_text(ctx.output / "assertions.md", "\n".join(lines).strip() + "\n")
    report = {
        "case_id": ctx.case_id, "count": len(normalized),
        "problems": problems, "atomicity_warnings": quality,
        "relation_vocabulary": sorted(RELATIONS),
        "all_atomic": not quality,
    }
    S.write_json(ctx.output / "quality-report.json", report)

    status = "answered" if not problems and not quality else "partial"
    for problem in problems[:20]:
        ctx.result.uncertainty("assertion", problem, "assertion set is incomplete",
                               "fix the assertion or evidence")
    if quality:
        ctx.result.uncertainty("atomicity", "%d assertions have atomicity warnings" % len(quality),
                               "some assertions may contain multiple claims",
                               "split composite assertions")
    if not meta.get("doi") and not meta.get("title"):
        ctx.result.uncertainty("source_metadata", "DOI and title are both missing",
                               "provenance is weak", "add source metadata")

    preserved = []
    for item in normalized:
        ev = item.get("evidence") or {}
        for token in S.number_tokens(ev.get("quote", ""))[:20]:
            preserved.append({"kind": "number", "source": token["raw"],
                              "target": token["raw"], "locator": ev.get("locator"),
                              "value": token["raw"], "unit": ""})
    evidence_map = [{"claim_id": item.get("id"), "claim": item.get("assertion"),
                     "source": {"path": (item.get("evidence") or {}).get("path"),
                                "locator": (item.get("evidence") or {}).get("locator")},
                     "quote": (item.get("evidence") or {}).get("quote"),
                     "supports": item.get("evidence_type", "direct")}
                    for item in normalized]

    ctx.result.add_artifact(ctx.output / "nanopublications.json", "deliverable", "json",
                            "machine-readable nanopublications")
    ctx.result.add_artifact(ctx.output / "assertions.json", "assertions", "json",
                            "assertions with provenance")
    ctx.result.add_artifact(ctx.output / "assertions.csv", "assertions", "csv",
                            "spreadsheet-friendly assertions")
    ctx.result.add_artifact(ctx.output / "assertions.md", "readable", "md",
                            "human-readable assertions")
    ctx.result.add_artifact(ctx.output / "quality-report.json", "audit", "json",
                            "atomicity, relation and evidence checks")
    ctx.result.change("assert", paragraphs_payload.get("source_path"),
                      "%d assertions extracted" % len(normalized))
    ctx.result.change("audit", "assertions", "atomicity, relation vocabulary and quotes checked")
    return ctx.result.finalize(
        status,
        deliverable=ctx.output / "nanopublications.json",
        result={"summary": "%d atomic assertions with evidence and provenance." % len(normalized),
                "source_metadata": meta, "checks": report},
        claims=[{"claim_id": item.get("id"), "text": item.get("assertion"),
                 "evidence": item.get("evidence")} for item in normalized],
        preserved_facts=preserved, evidence_map=evidence_map,
    )
