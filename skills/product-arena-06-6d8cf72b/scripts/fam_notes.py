#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reading 10 — section-aligned structured notes and reusable paper card."""
from __future__ import annotations

import json
import re
from pathlib import Path

import skillcore as S


def _primary_doc(ctx):
    best = None
    for doc in ctx.docs:
        text = S.doc_text(doc)
        if text.strip() and (best is None or len(text) > len(S.doc_text(best))):
            best = doc
    return best


def prepare(ctx):
    doc = _primary_doc(ctx)
    if doc is None:
        ctx.result.change("prepare", "input", "no readable paper")
        ctx.result.uncertainty("paper", "no readable source text",
                               "notes cannot be grounded", "provide the paper")
        return ctx.result.finalize("input_error")
    text = S.doc_text(doc)
    segments = S.segment_blocks(text, source_id=doc.get("source_id", "SRC01"),
                                path=doc.get("rel_path", ""))
    # Section grouping from headings; PDF text may have plain headings.
    blocks = []
    for segment in segments:
        if segment.get("kind") == "heading":
            blocks.append({"text": segment["text"], "kind": "heading",
                           "line_start": segment.get("line_start"),
                           "page": segment.get("page")})
        else:
            blocks.append({"text": segment["text"], "kind": "body",
                           "line_start": segment.get("line_start"),
                           "page": segment.get("page")})
    sections = []
    current = {"id": "S0", "heading": "Front matter", "level": 0, "segments": []}
    sections.append(current)
    counter = 0
    for segment, block in zip(segments, blocks):
        if block["kind"] == "heading":
            counter += 1
            current = {"id": "S%d" % counter, "heading": segment["text"],
                       "level": 1, "segments": [segment]}
            sections.append(current)
        else:
            current["segments"].append(segment)
    outline = {
        "case_id": ctx.case_id,
        "source_path": doc.get("rel_path"),
        "source_chars": len(text),
        "sections": [{"id": s["id"], "heading": s["heading"],
                      "segments": [{"id": x["id"], "locator": x["locator"],
                                    "text": S.excerpt(x["text"], 500)}
                                   for x in s["segments"]]}
                     for s in sections if s["segments"] or s["heading"] != "Front matter"],
        "all_segments": segments,
    }
    S.write_json(ctx.work / "outline.json", outline)
    S.write_json(ctx.work / "notes-template.json", {
        "case_id": ctx.case_id,
        "paper_card": {
            "research_question": "", "data": "", "methods": "",
            "findings": [], "limitations": [], "conditions": [],
            "doi": "", "year": "", "authors": [],
        },
        "sections": [{"id": s["id"], "heading": s["heading"],
                      "purpose": "", "methods": "", "results": "",
                      "conclusions": "", "anchors": [
                          {"path": doc.get("rel_path"), "locator": "",
                           "quote": ""}]}
                     for s in sections if s["segments"] or s["heading"] != "Front matter"],
        "knowledge_points": [{"id": "K1", "type": "finding",
                              "statement": "", "evidence": [
                                  {"path": doc.get("rel_path"), "locator": "",
                                   "quote": ""}], "tags": []}],
        "open_questions": [],
    })
    S.write_json(ctx.work / "source.json", {
        "case_id": ctx.case_id, "path": doc.get("rel_path"),
        "source_id": doc.get("source_id"), "chars": len(text),
    })
    ctx.result.change("outline", doc.get("rel_path", ""),
                      "%d sections, %d segments" % (
                          len(outline["sections"]), len(segments)))
    return ctx.provisional(reason="paper outlined; write notes.json, then run finalize")


def _anchor_problems(anchors, docs, require_quote=True):
    problems = []
    by_path = {(d.get("rel_path") or d.get("path")): d for d in docs}
    for index, anchor in enumerate(anchors or [], 1):
        if not isinstance(anchor, dict):
            problems.append("anchor %d is not an object" % index)
            continue
        path = anchor.get("path") or ""
        doc = by_path.get(path)
        if doc is None:
            problems.append("anchor %d: path not found %s" % (index, path))
            continue
        quote = anchor.get("quote") or ""
        if require_quote:
            ok, reason = S.quote_supported(S.doc_text(doc), quote)
            if not ok:
                problems.append("anchor %d: %s" % (index, reason))
    return problems


def finalize(ctx):
    notes = S.load_json(ctx.work / "notes.json", {})
    if not isinstance(notes, dict) or not (notes.get("sections") or notes.get("knowledge_points")):
        ctx.result.change("finalize", "output", "notes.json missing or empty")
        ctx.result.uncertainty("notes", "model-authored notes.json is missing",
                               "no structured notes", "write notes.json")
        return ctx.result.finalize("partial")
    outline = S.load_json(ctx.work / "outline.json", {})
    all_segments = outline.get("all_segments") or []
    source_text = "\n\n".join(S.doc_text(d) for d in ctx.docs)
    card = notes.get("paper_card") or {}
    sections = notes.get("sections") or []
    points = notes.get("knowledge_points") or []
    problems = []
    for key in ("research_question", "data", "methods", "findings", "limitations"):
        if not card.get(key):
            problems.append("paper_card.%s is missing" % key)
    for section in sections:
        if not section.get("anchors"):
            problems.append("section %s has no anchors" % section.get("id", "?"))
        problems.extend(_anchor_problems(section.get("anchors"), ctx.docs))
    for point in points:
        evidence = point.get("evidence") or []
        if not evidence:
            problems.append("knowledge point %s has no evidence" % point.get("id", "?"))
        problems.extend(_anchor_problems(evidence, ctx.docs))

    lines = ["# Structured notes: %s" % (card.get("title") or ctx.case_id), ""]
    lines += ["## Paper card", ""]
    for key in ("research_question", "data", "methods"):
        lines.append("- **%s**: %s" % (key.replace("_", " ").title(), card.get(key, "")))
    for key in ("findings", "limitations", "conditions"):
        values = card.get(key) or []
        if values:
            lines.append("- **%s**: %s" % (key.title(), "; ".join(map(str, values))))
    lines += ["", "## Section notes", ""]
    for section in sections:
        lines.append("### %s" % section.get("heading", section.get("id", "")))
        for key in ("purpose", "methods", "results", "conclusions"):
            if section.get(key):
                lines.append("- **%s**: %s" % (key.title(), section[key]))
        for anchor in section.get("anchors") or []:
            lines.append("  - Evidence: `%s` — %s" % (
                anchor.get("locator", ""), S.excerpt(anchor.get("quote", ""), 120)))
        lines.append("")
    lines += ["## Knowledge points", ""]
    for point in points:
        lines.append("- **%s** (%s): %s" % (point.get("id", ""), point.get("type", ""),
                                            point.get("statement", "")))
        for anchor in point.get("evidence") or []:
            lines.append("  - `%s`: %s" % (anchor.get("locator", ""),
                                           S.excerpt(anchor.get("quote", ""), 120)))
    if notes.get("open_questions"):
        lines += ["", "## Open questions", ""]
        for question in notes["open_questions"]:
            lines.append("- %s" % question)
    notes_md = "\n".join(lines).strip() + "\n"
    S.write_text(ctx.output / "notes.md", notes_md)
    S.write_json(ctx.output / "paper-card.json", {
        "case_id": ctx.case_id, "paper_card": card,
        "source": outline.get("source_path"),
        "section_count": len(sections), "knowledge_points": len(points),
    })
    S.write_json(ctx.output / "knowledge-points.json", {
        "case_id": ctx.case_id, "points": points,
    })
    index = []
    for section in sections:
        index.append({"kind": "section", "id": section.get("id"),
                      "heading": section.get("heading"), "text": " ".join(
                          str(section.get(k, "")) for k in
                          ("purpose", "methods", "results", "conclusions"))})
    for point in points:
        index.append({"kind": "knowledge_point", "id": point.get("id"),
                      "type": point.get("type"), "text": point.get("statement", ""),
                      "tags": point.get("tags") or []})
    S.write_json(ctx.output / "notes-index.json", {
        "case_id": ctx.case_id, "entries": index,
    })

    invented = S.number_presence_missing(notes_md, source_text)
    numbers = {"ok": not invented, "invented": invented}
    if invented:
        problems.append("notes contain numbers that are absent from the paper")
    status = "answered" if not problems else "partial"
    for problem in problems[:20]:
        ctx.result.uncertainty("notes", problem, "note quality is incomplete",
                               "add the missing field or evidence anchor")
    if not numbers["ok"]:
        ctx.result.uncertainty("number_mismatch", json.dumps(numbers, ensure_ascii=False),
                               "numbers are not traceable to the paper", "restore exact values")

    preserved = [{"kind": "number", "source": t["raw"], "target": t["raw"],
                  "locator": outline.get("source_path"), "value": t["raw"], "unit": ""}
                 for t in S.number_tokens(source_text)[:150]]
    evidence_map = []
    for section in sections:
        for anchor in section.get("anchors") or []:
            evidence_map.append({"claim_id": section.get("id", ""),
                                 "claim": section.get("heading", ""),
                                 "source": {"path": anchor.get("path", ""),
                                            "locator": anchor.get("locator", "")},
                                 "quote": anchor.get("quote", ""),
                                 "supports": "section-note"})
    for point in points:
        for anchor in point.get("evidence") or []:
            evidence_map.append({"claim_id": point.get("id", ""),
                                 "claim": point.get("statement", ""),
                                 "source": {"path": anchor.get("path", ""),
                                            "locator": anchor.get("locator", "")},
                                 "quote": anchor.get("quote", ""),
                                 "supports": "knowledge-point"})

    ctx.result.add_artifact(ctx.output / "notes.md", "deliverable", "md",
                            "structured, section-aligned notes")
    ctx.result.add_artifact(ctx.output / "paper-card.json", "paper_card", "json",
                            "reusable paper card")
    ctx.result.add_artifact(ctx.output / "knowledge-points.json", "knowledge_points", "json",
                            "searchable knowledge points")
    ctx.result.add_artifact(ctx.output / "notes-index.json", "index", "json",
                            "flat searchable index")
    docx_path = ctx.output / "notes.docx"
    try:
        S.write_docx(notes_md, docx_path, title="Structured notes")
        ok, why = S.docx_reopen_check(docx_path)
        if ok:
            ctx.result.add_artifact(docx_path, "editable_docx", "docx",
                                    "editable Word notes")
        else:
            ctx.result.uncertainty("docx_reopen", why, "DOCX may not open", "use notes.md")
    except Exception as exc:  # noqa: BLE001
        ctx.result.uncertainty("docx_export", str(exc), "DOCX unavailable", "use notes.md")

    ctx.result.change("notes", outline.get("source_path"),
                      "%d sections and %d knowledge points" % (len(sections), len(points)))
    ctx.result.change("audit", "anchors", "section and knowledge-point anchors checked")
    return ctx.result.finalize(
        status,
        deliverable=ctx.output / "notes.md",
        result={"summary": "Structured notes with %d sections and %d knowledge points." % (
                    len(sections), len(points)),
                "paper_card": card, "checks": {"problems": problems, "numbers": numbers}},
        claims=[{"claim_id": p.get("id"), "text": p.get("statement", ""),
                 "evidence": p.get("evidence")} for p in points],
        preserved_facts=preserved, evidence_map=evidence_map,
    )
