#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Writing 02 — minimal-edit academic language polishing with change records."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import skillcore as S


def _source_doc(ctx):
    best = None
    for doc in ctx.docs:
        text = S.doc_text(doc)
        if text.strip() and (best is None or len(text) > len(S.doc_text(best))):
            best = doc
    return best


def _genre(ctx):
    case = ctx.case or {}
    return str(case.get("genre") or case.get("target_genre")
               or case.get("style") or "journal article")


def prepare(ctx):
    doc = _source_doc(ctx)
    if doc is None:
        ctx.result.change("prepare", "input", "no readable manuscript")
        ctx.result.uncertainty("manuscript", "no readable source text",
                               "polishing cannot start", "provide the manuscript")
        return ctx.result.finalize("input_error")
    text = S.doc_text(doc)
    segments = S.segment_blocks(text, source_id=doc.get("source_id", "SRC01"),
                                path=doc.get("rel_path", ""))
    for segment in segments:
        sentences = S.split_sentences(segment["text"])
        for sentence in sentences:
            sentence["sentence_id"] = "%s-%s" % (segment["id"], sentence["id"])
            sentence["locator"] = "%s %s" % (segment["locator"], sentence["id"])
        segment["sentences"] = sentences
    glossary_candidates = S.term_candidates(text, 60)
    payload = {
        "case_id": ctx.case_id,
        "source_path": doc.get("rel_path"),
        "genre": _genre(ctx),
        "segments": segments,
        "glossary_candidates": glossary_candidates,
        "instructions": [
            "Use minimal edits: keep the author's information, order and hedging strength.",
            "Write /app/work/edits.json with before/after/reason/type for every change.",
            "Before must be a verbatim substring of the named segment or sentence.",
            "Preserve numbers, units, citations and figure/table references exactly.",
        ],
    }
    S.write_json(ctx.work / "segments.json", payload)
    S.write_json(ctx.work / "glossary-candidates.json", glossary_candidates)
    template = {"case_id": ctx.case_id, "genre": payload["genre"], "edits": [
        {"id": "E001", "segment_id": "P001", "sentence_id": "P001-S001",
         "before": "verbatim source phrase", "after": "revised phrase",
         "type": "grammar|term|register|clarity|cohesion|typography",
         "reason": "why this edit is needed", "preserve": ["number", "citation", "hedge"]}
    ]}
    S.write_json(ctx.work / "edits-template.json", template)
    S.write_text(ctx.output / "polished-partial.md", text)
    ctx.result.add_artifact(ctx.output / "polished-partial.md", "partial_draft", "md",
                            "unpolished source preserved for inspection")
    ctx.result.change("segment", doc.get("rel_path", ""), "%d segments prepared" % len(segments))
    return ctx.provisional(reason="manuscript segmented; write edits.json and run finalize")


def _load_glossary(ctx):
    payload = S.load_json(ctx.work / "glossary.json", None)
    if isinstance(payload, dict):
        items = payload.get("terms") or payload.get("glossary") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    out = []
    for item in items:
        if isinstance(item, dict):
            source = str(item.get("source") or item.get("from") or "")
            target = str(item.get("target") or item.get("to") or "")
            if source or target:
                out.append({"source": source, "target": target,
                            "note": item.get("note", "")})
    return out


def _apply_edit(segments, edit):
    segment_id = str(edit.get("segment_id") or "")
    segment = next((s for s in segments if s["id"] == segment_id), None)
    if segment is None:
        return False, "segment not found: %s" % segment_id
    before = str(edit.get("before") or "")
    after = str(edit.get("after") or "")
    if not before:
        return False, "empty before"
    target_text = segment["text"]
    sentence_id = str(edit.get("sentence_id") or "")
    if sentence_id:
        sentence = next((x for x in segment.get("sentences", [])
                         if x.get("sentence_id") == sentence_id), None)
        if sentence is None:
            return False, "sentence not found: %s" % sentence_id
        target_text = sentence["text"]
    if target_text.count(before) != 1:
        # Try whitespace-normalised match and replace the matched span.
        norm_target = re.sub(r"\s+", " ", target_text)
        norm_before = re.sub(r"\s+", " ", before)
        if norm_target.count(norm_before) != 1:
            return False, "before occurs %d times" % target_text.count(before)
        updated_sentence = norm_target.replace(norm_before, after, 1)
        if sentence_id:
            segment["text"] = segment["text"].replace(target_text, updated_sentence, 1)
            for item in segment.get("sentences", []):
                if item.get("sentence_id") == sentence_id:
                    item["text"] = updated_sentence
        else:
            segment["text"] = updated_sentence
        return True, "ok (whitespace-normalised)"
    updated = target_text.replace(before, after, 1)
    if sentence_id:
        segment["text"] = segment["text"].replace(target_text, updated, 1)
        for item in segment.get("sentences", []):
            if item.get("sentence_id") == sentence_id:
                item["text"] = updated
    else:
        segment["text"] = updated
    return True, "ok"


def _render(segments):
    lines = []
    for segment in segments:
        text = segment["text"].strip()
        if not text:
            continue
        if segment.get("kind") == "heading" and not text.startswith("#"):
            lines.append("## " + text)
        else:
            lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def finalize(ctx):
    payload = S.load_json(ctx.work / "segments.json", {})
    segments = payload.get("segments") or []
    if not segments:
        ctx.result.change("finalize", "input", "segments.json missing")
        ctx.result.uncertainty("segments", "no prepared manuscript",
                               "polishing cannot continue", "run prepare first")
        return ctx.result.finalize("input_error")
    source_text = "\n\n".join(s["text"] for s in segments)
    edits_payload = S.load_json(ctx.work / "edits.json", {})
    if isinstance(edits_payload, dict):
        edits = edits_payload.get("edits") or []
    else:
        edits = edits_payload if isinstance(edits_payload, list) else []
    polished_path = ctx.output / "polished.md"
    applied, blocked = [], []
    if edits:
        for edit in edits:
            ok, reason = _apply_edit(segments, edit)
            row = dict(edit)
            row["status"] = "applied" if ok else "blocked"
            row["detail"] = reason
            if ok:
                applied.append(row)
            else:
                blocked.append(row)
        polished = _render(segments)
        S.write_text(polished_path, polished)
    elif polished_path.is_file() and S.read_text(polished_path).strip():
        polished = S.read_text(polished_path)
        diff = S.sentence_diff(source_text, polished)
        for index, item in enumerate(diff, 1):
            applied.append({"id": "D%03d" % index, "type": "rewrite",
                            "before": item["before"], "after": item["after"],
                            "status": "applied", "detail": "derived from polished.md"})
    else:
        ctx.result.change("finalize", "output", "no edits.json and no polished.md")
        ctx.result.uncertainty("deliverable", "model-authored polished text is missing",
                               "cannot deliver a polished manuscript",
                               "write edits.json or polished.md")
        return ctx.result.finalize("partial")

    glossary = _load_glossary(ctx)
    if not glossary:
        glossary = [{"source": x.get("term", ""), "target": "",
                     "note": "candidate; target not locked"}
                    for x in S.load_json(ctx.work / "glossary-candidates.json", [])[:20]]
    S.write_json(ctx.output / "glossary.json", {
        "case_id": ctx.case_id, "genre": payload.get("genre"),
        "terms": glossary,
    })

    numbers = S.check_numbers_preserved(source_text, polished)
    citations = S.check_citations_preserved(source_text, polished)
    source_hedge = S.hedge_counts(source_text)
    target_hedge = S.hedge_counts(polished)
    source_neg = S.negation_count(source_text)
    target_neg = S.negation_count(polished)
    term_issues = S.check_terms(glossary, polished) if glossary else []
    change_table = []
    for row in applied:
        change_table.append({
            "id": row.get("id") or "E%03d" % (len(change_table) + 1),
            "segment_id": row.get("segment_id", ""),
            "sentence_id": row.get("sentence_id", ""),
            "type": row.get("type", "edit"),
            "before": row.get("before", ""),
            "after": row.get("after", ""),
            "reason": row.get("reason", ""),
            "status": row.get("status", "applied"),
            "locator": row.get("locator", row.get("segment_id", "")),
        })
    S.write_json(ctx.output / "edit-table.json", {
        "case_id": ctx.case_id, "applied": change_table,
        "blocked": blocked, "count": len(change_table),
    })
    with (ctx.output / "edit-table.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "segment_id", "sentence_id", "type", "before",
                         "after", "reason", "status"])
        for row in change_table:
            writer.writerow([row["id"], row["segment_id"], row["sentence_id"],
                             row["type"], row["before"], row["after"],
                             row["reason"], row["status"]])

    report = {
        "case_id": ctx.case_id,
        "source_path": payload.get("source_path"),
        "genre": payload.get("genre"),
        "edits_total": len(edits),
        "applied": len(change_table),
        "blocked": len(blocked),
        "numbers": numbers,
        "citations": citations,
        "negation": {"source": source_neg, "target": target_neg,
                     "ok": source_neg == target_neg},
        "hedging": {"source": source_hedge, "target": target_hedge,
                    "ok": source_hedge["level"] == target_hedge["level"]},
        "glossary_issues": term_issues,
    }
    S.write_json(ctx.output / "preservation-report.json", report)

    docx_path = ctx.output / "polished.docx"
    try:
        S.write_docx(polished, docx_path, title="Polished manuscript")
        ok, why = S.docx_reopen_check(docx_path)
        if not ok:
            ctx.result.uncertainty("docx_reopen", why, "DOCX may not open",
                                   "keep the Markdown deliverable")
    except Exception as exc:  # noqa: BLE001
        ctx.result.uncertainty("docx_export", str(exc), "DOCX unavailable",
                               "use polished.md")
    preview_path = ctx.output / "polished-preview.pdf"
    preview, preview_err = S.write_pdf_preview(polished, preview_path,
                                               title="Polished manuscript preview")
    if preview_err:
        ctx.result.uncertainty("pdf_preview", preview_err, "preview unavailable",
                               "inspect polished.md")

    preserved = []
    for token in S.number_tokens(source_text)[:200]:
        preserved.append({"kind": "number", "source": token["raw"],
                          "target": token["raw"], "locator": payload.get("source_path"),
                          "value": token["raw"], "unit": ""})
    for token in S.citation_tokens(source_text)[:100]:
        preserved.append({"kind": "citation", "source": token["raw"],
                          "target": token["raw"], "locator": payload.get("source_path"),
                          "value": token["raw"], "unit": ""})
    evidence = []
    for row in change_table[:200]:
        evidence.append({"claim_id": row["id"], "claim": S.excerpt(row["after"], 120),
                         "source": {"path": payload.get("source_path"),
                                    "locator": row.get("locator") or row.get("segment_id")},
                         "quote": S.excerpt(row["before"], 200),
                         "supports": "edit", "paraphrase": True})

    critical = (bool(blocked) or bool(numbers["missing"]) or not citations["ok"]
                or not report["hedging"]["ok"] or bool(term_issues))
    status = "completed" if not critical and change_table else "partial"
    if blocked:
        ctx.result.uncertainty("blocked_edits", "%d edits could not be applied" % len(blocked),
                               "some requested changes are absent", "fix before anchors")
    if bool(numbers["missing"]):
        ctx.result.uncertainty("number_mismatch", json.dumps(numbers, ensure_ascii=False),
                               "numbers changed during editing", "restore source values")
    if not report["hedging"]["ok"]:
        ctx.result.uncertainty("hedging_shift", json.dumps(report["hedging"]),
                               "claim strength may have changed", "review modal verbs")
    if term_issues:
        ctx.result.uncertainty("glossary_consistency", "; ".join(term_issues[:10]),
                               "term targets are not all present", "apply the glossary")

    ctx.result.add_artifact(polished_path, "deliverable", "md", "polished manuscript")
    ctx.result.add_artifact(ctx.output / "edit-table.json", "change_record", "json",
                            "sentence-level edits with reasons")
    ctx.result.add_artifact(ctx.output / "edit-table.csv", "change_record", "csv",
                            "spreadsheet-friendly edit table")
    ctx.result.add_artifact(ctx.output / "glossary.json", "glossary", "json",
                            "terminology used in the edit")
    ctx.result.add_artifact(ctx.output / "preservation-report.json", "audit", "json",
                            "numbers, citations, negation, hedging and terms")
    if docx_path.is_file():
        ctx.result.add_artifact(docx_path, "editable_docx", "docx",
                                "editable Word version")
    if preview and preview.is_file():
        ctx.result.add_artifact(preview, "preview_pdf", "pdf",
                                "text-content PDF preview, not a DOCX layout render")
    ctx.result.change("polish", payload.get("source_path"),
                      "%d edits applied, %d blocked" % (len(change_table), len(blocked)))
    ctx.result.change("audit", "preservation", "numbers/citations/hedging/terms checked")
    return ctx.result.finalize(
        status,
        deliverable=polished_path,
        result={"summary": "Applied %d edits; %d blocked; preservation %s." % (
                    len(change_table), len(blocked), "ok" if not critical else "needs review"),
                "genre": payload.get("genre"), "edits_applied": len(change_table),
                "edits_blocked": len(blocked), "checks": report},
        claims=[{"claim_id": row["id"], "text": S.excerpt(row["after"], 140),
                 "evidence": row.get("segment_id", "")} for row in change_table[:200]],
        preserved_facts=preserved, evidence_map=evidence,
    )
