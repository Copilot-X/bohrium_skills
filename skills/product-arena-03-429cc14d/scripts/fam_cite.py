#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Writing 04 — reference and citation management with numbered synchronization."""
from __future__ import annotations

import json
import re
from pathlib import Path

import skillcore as S

REF_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:References|Bibliography|参考文献)\s*$", re.I | re.M)
REF_ENTRY = re.compile(r"^\s*\[(\d+)\]\s+(.+?)(?=^\s*\[\d+\]\s+|\Z)", re.M | re.S)


def _manuscript(ctx):
    best = None
    for doc in ctx.docs:
        if doc.get("kind") in ("bib", "ris", "json"):
            continue
        text = S.doc_text(doc)
        if text.strip() and (best is None or len(text) > len(S.doc_text(best))):
            best = doc
    return best


def _entries(ctx):
    return S.bib_entries_from_docs(ctx.docs)


def _style(ctx):
    case = ctx.case or {}
    value = str(case.get("citation_style") or case.get("style") or "").lower()
    task = ctx.task.lower()
    if "gb/t" in value or "gb/t" in task or "7714" in task:
        return "gb"
    if "ieee" in value or "ieee" in task:
        return "ieee"
    if "vancouver" in value or "vancouver" in task:
        return "vancouver"
    if "apa" in value or "apa" in task:
        return "apa"
    return "gb"


def _parse_reference_entries(text):
    heading = REF_HEADING.search(text)
    if not heading:
        return [], None
    section = text[heading.end():]
    entries = []
    for match in REF_ENTRY.finditer(section):
        entries.append({"number": int(match.group(1)),
                        "text": re.sub(r"\s+", " ", match.group(2)).strip()})
    return entries, heading


def prepare(ctx):
    doc = _manuscript(ctx)
    if doc is None:
        ctx.result.change("prepare", "input", "no readable manuscript")
        ctx.result.uncertainty("manuscript", "no readable source text",
                               "citation management cannot start", "provide the manuscript")
        return ctx.result.finalize("input_error")
    text = S.doc_text(doc)
    segments = S.segment_blocks(text, source_id=doc.get("source_id", "SRC01"),
                                path=doc.get("rel_path", ""))
    in_references = False
    for segment in segments:
        if REF_HEADING.match(segment["text"].strip()):
            in_references = True
        elif in_references:
            segment["kind"] = "reference"
        sentences = S.split_sentences(segment["text"])
        for sentence in sentences:
            sentence["sentence_id"] = "%s-%s" % (segment["id"], sentence["id"])
            sentence["locator"] = "%s %s" % (segment["locator"], sentence["id"])
        segment["sentences"] = sentences
    entries = _entries(ctx)
    by_key = {str(e.get("key")): e for e in entries}
    existing_refs, heading = _parse_reference_entries(text)
    # Dedupe candidates.
    groups = {}
    for entry in entries:
        groups.setdefault(S.dedupe_key(entry), []).append(entry)
    candidates = []
    for key, group in groups.items():
        if len(group) > 1:
            candidates.append({"keep": group[0].get("key"),
                               "merge": [x.get("key") for x in group[1:]],
                               "basis": key, "decision": "merge"})
    citation_points = []
    for segment in segments:
        for sentence in segment.get("sentences", []):
            tokens = S.citation_tokens(sentence["text"])
            citation_points.append({
                "paragraph_id": segment["id"],
                "sentence_id": sentence["sentence_id"],
                "locator": sentence["locator"],
                "text": sentence["text"],
                "has_citation": bool(tokens),
                "existing_citations": [t["raw"] for t in tokens],
                "likely_claim": bool(re.search(
                    r"\b(?:show|demonstrate|suggest|report|find|increase|decrease|"
                    r"improve|reduce|significant|associated|correlat|表明|显示|发现|"
                    r"提高|降低|显著|相关)\b", sentence["text"], re.I)),
            })
    S.write_json(ctx.work / "manuscript.json", {
        "case_id": ctx.case_id, "source_path": doc.get("rel_path"),
        "style": _style(ctx), "segments": segments,
        "existing_reference_entries": existing_refs,
    })
    S.write_json(ctx.work / "bibliography.json", {
        "case_id": ctx.case_id, "style": _style(ctx),
        "entries": [{"key": e.get("key"), "type": e.get("type"),
                     "fields": e.get("fields"), "dedupe_key": S.dedupe_key(e)}
                    for e in entries],
    })
    S.write_json(ctx.work / "citation-points.json", {
        "case_id": ctx.case_id, "points": citation_points,
        "existing_reference_entries": existing_refs,
    })
    S.write_json(ctx.work / "dedupe-candidates.json", {
        "case_id": ctx.case_id, "candidates": candidates,
    })
    S.write_json(ctx.work / "citation-plan-template.json", {
        "case_id": ctx.case_id, "style": _style(ctx),
        "insertions": [{"id": "C001", "paragraph_id": "P001",
                        "sentence_id": "P001-S001", "anchor_text": "",
                        "ref_keys": ["key"], "position": "sentence_end",
                        "reason": "which claim this source supports"}],
        "dedupe": candidates,
        "metadata_fixes": [{"key": "", "field": "", "before": "", "after": "",
                            "source": ""}],
        "unresolved": [],
    })
    S.write_json(ctx.work / "bibliography-keys.json", {
        "keys": sorted(by_key), "count": len(by_key),
    })
    ctx.result.change("prepare", doc.get("rel_path", ""),
                      "%d segments, %d bibliography entries, %d dedupe candidates" % (
                          len(segments), len(entries), len(candidates)))
    return ctx.provisional(reason="manuscript and bibliography indexed; write citation-plan.json")


def _find_sentence(segments, sentence_id=None, paragraph_id=None, anchor_text=""):
    for segment in segments:
        if paragraph_id and segment["id"] != paragraph_id:
            continue
        for sentence in segment.get("sentences", []):
            if sentence_id and sentence.get("sentence_id") != sentence_id:
                continue
            if anchor_text and anchor_text not in sentence["text"]:
                continue
            return segment, sentence
    return None, None


def _insert_into_sentence(sentence_text, marker, anchor_text="", position="sentence_end"):
    if position == "after_anchor" and anchor_text and anchor_text in sentence_text:
        index = sentence_text.find(anchor_text) + len(anchor_text)
        return sentence_text[:index] + " " + marker + sentence_text[index:]
    # Insert before terminal punctuation at the end.
    match = re.search(r"([.!?。！？])([\"'”’)]*)\s*$", sentence_text)
    if match:
        return sentence_text[:match.start()] + " " + marker + sentence_text[match.start():]
    return sentence_text.rstrip() + " " + marker


def _rebuild_segments(segments, replacements):
    """Apply sentence replacements and return the edited segment texts."""
    edited = []
    for segment in segments:
        text = segment["text"]
        for old, new in replacements.get(segment["id"], []):
            if old in text:
                text = text.replace(old, new, 1)
        edited.append({"segment_id": segment["id"], "text": text,
                       "kind": segment.get("kind")})
    return edited


def _render(edited):
    lines = []
    for segment in edited:
        text = segment["text"].strip()
        if not text:
            continue
        if segment.get("kind") == "reference":
            continue
        if re.match(r"^(?:#+\s*)?(?:References|Bibliography|参考文献)\s*$", text, re.I):
            continue
        if segment.get("kind") == "heading" and not text.startswith("#"):
            lines.append("## " + text)
        else:
            lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _format_inline(entry, style, number=None):
    fields = entry.get("fields") or {}
    authors = S._author_list(fields)
    year = fields.get("year") or fields.get("PY") or ""
    if style in ("apa", "author-year"):
        if authors:
            last = S._split_author(authors[0])[0]
            if len(authors) > 1:
                return "(%s et al., %s)" % (last, year)
            return "(%s, %s)" % (last, year)
        return "(%s)" % year
    return "[%s]" % (number if number is not None else "?")


def _apply_metadata_fixes(entries, fixes):
    by_key = {str(e.get("key")): e for e in entries}
    applied = []
    for fix in fixes or []:
        key, field = str(fix.get("key")), str(fix.get("field"))
        entry = by_key.get(key)
        if entry is None or not field:
            applied.append({"fix": fix, "status": "blocked", "reason": "entry/field missing"})
            continue
        before = (entry.get("fields") or {}).get(field)
        if str(before) != str(fix.get("before")):
            applied.append({"fix": fix, "status": "blocked",
                            "reason": "before does not match: %r" % before})
            continue
        entry.setdefault("fields", {})[field] = fix.get("after")
        applied.append({"fix": fix, "status": "applied"})
    return applied


def finalize(ctx):
    manuscript = S.load_json(ctx.work / "manuscript.json", {})
    segments = manuscript.get("segments") or []
    if not segments:
        ctx.result.change("finalize", "input", "manuscript.json missing")
        ctx.result.uncertainty("manuscript", "no prepared manuscript",
                               "citation management cannot continue", "run prepare first")
        return ctx.result.finalize("input_error")
    plan = S.load_json(ctx.work / "citation-plan.json", {})
    if not isinstance(plan, dict):
        plan = {}
    insertions = plan.get("insertions") or []
    entries = _entries(ctx)
    by_key = {str(e.get("key")): e for e in entries}
    # Apply dedupe first.
    dedupe = plan.get("dedupe") or []
    dedupe_report = []
    key_alias = {}
    for item in dedupe:
        keep = str(item.get("keep") or "")
        merge = [str(x) for x in (item.get("merge") or [])]
        if keep not in by_key:
            dedupe_report.append({"item": item, "status": "blocked", "reason": "keep key missing"})
            continue
        if item.get("decision") == "keep_separate":
            dedupe_report.append({"item": item, "status": "kept_separate"})
            continue
        for key in merge:
            if key in by_key:
                key_alias[key] = keep
                dedupe_report.append({"keep": keep, "merged": key,
                                      "basis": item.get("basis", ""), "status": "merged"})
        by_key[keep].setdefault("merged_keys", []).extend(merge)
    merged_keys = set(key_alias)
    by_key = {key: value for key, value in by_key.items() if key not in merged_keys}
    fixes = _apply_metadata_fixes(list(by_key.values()), plan.get("metadata_fixes") or [])
    style = str(plan.get("style") or manuscript.get("style") or "gb").lower()
    if style.startswith("gb"):
        style = "gb"
    # Insert placeholders into sentences.
    replacements = {}
    applied, blocked = [], []
    for insertion in insertions:
        keys = [key_alias.get(str(k), str(k)) for k in (insertion.get("ref_keys") or [])]
        missing = [k for k in keys if k not in by_key]
        anchor = str(insertion.get("anchor_text") or "")
        segment, sentence = _find_sentence(
            segments, insertion.get("sentence_id"), insertion.get("paragraph_id"), anchor)
        if missing or segment is None or sentence is None:
            row = dict(insertion)
            row["status"] = "blocked"
            row["reason"] = ("missing keys: %s" % missing if missing else
                             "sentence/anchor not found")
            blocked.append(row)
            continue
        marker = "[[%s]]" % ",".join(keys)
        new_sentence = _insert_into_sentence(
            sentence["text"], marker, anchor, insertion.get("position", "sentence_end"))
        replacements.setdefault(segment["id"], []).append((sentence["text"], new_sentence))
        row = dict(insertion)
        row["status"] = "applied"
        row["keys"] = keys
        row["marker"] = marker
        applied.append(row)
    edited = _rebuild_segments(segments, replacements)
    text = _render(edited)
    # Number new placeholders after existing in-text citations. The
    # reference list was already removed from the rendered body, so its
    # numbers cannot shift the new sequence.
    existing_numbers = [int(x) for x in re.findall(r"\[(\d+)\]", text)]
    next_number = max(existing_numbers, default=0) + 1
    key_numbers = {}
    for match in re.finditer(r"\[\[([^\]]+)\]\]", text):
        for key in [x.strip() for x in match.group(1).split(",") if x.strip()]:
            if key not in key_numbers:
                key_numbers[key] = next_number
                next_number += 1
    # Every remaining bibliography entry gets a unique number, including
    # uncited entries, so the list stays internally consistent.
    for key in sorted(by_key):
        if key not in key_numbers:
            key_numbers[key] = next_number
            next_number += 1
    if style in ("apa", "author-year"):
        def replace_marker(match):
            keys = [x.strip() for x in match.group(1).split(",") if x.strip()]
            return "; ".join(_format_inline(by_key[k], style) for k in keys if k in by_key)
    else:
        def replace_marker(match):
            keys = [x.strip() for x in match.group(1).split(",") if x.strip()]
            nums = sorted(key_numbers.get(k, 0) for k in keys if k in key_numbers)
            return "[%s]" % S.format_number_list(nums) if nums else ""
    text = re.sub(r"\[\[([^\]]+)\]\]", replace_marker, text)
    if style in ("apa", "author-year"):
        ordered = sorted(by_key.values(), key=lambda e: (
            (S._author_list(e.get("fields") or {}) or ["zzz"])[0].lower(),
            str((e.get("fields") or {}).get("year", ""))))
        bibliography = [S.format_bib_entry(entry, "apa") for entry in ordered]
    else:
        ordered = sorted(by_key.values(), key=lambda e: key_numbers.get(str(e.get("key")), 10 ** 9))
        bibliography = [S.format_bib_entry(entry, style,
                                           key_numbers.get(str(entry.get("key"))))
                       for entry in ordered]
    refs_md = "# References\n\n" + "\n\n".join(
        "%s" % item if style in ("apa", "author-year") else item for item in bibliography) + "\n"
    full = text.rstrip() + "\n\n" + refs_md
    S.write_text(ctx.output / "manuscript-cited.md", full)
    S.write_json(ctx.output / "references.json", {
        "case_id": ctx.case_id, "style": style,
        "entries": [{"key": e.get("key"), "number": key_numbers.get(str(e.get("key"))),
                     "formatted": S.format_bib_entry(e, style,
                                                     key_numbers.get(str(e.get("key")))),
                     "fields": e.get("fields")} for e in ordered],
        "metadata_fixes": fixes,
    })
    S.write_text(ctx.output / "references-formatted.md", refs_md)
    # Write a deduplicated BibTeX file from the entries actually used.
    if ordered:
        S.write_text(ctx.output / "references.bib",
                     "\n\n".join(S.bibtex_entry(entry) for entry in ordered) + "\n")
    else:
        bib_texts = [S.doc_text(d) for d in ctx.docs if d.get("kind") == "bib"]
        if bib_texts:
            S.write_text(ctx.output / "references.bib", "\n\n".join(bib_texts))
    # Audit.
    final_citations = S.citation_tokens(full)
    final_numbers = sorted({n for t in final_citations for n in t.get("numbers", [])})
    entry_numbers = sorted({int(x) for x in re.findall(r"^\s*\[(\d+)\]", refs_md, re.M)})
    missing_numbers = [n for n in final_numbers if entry_numbers and n not in entry_numbers]
    duplicate_keys = [k for k, count in
                      __import__("collections").Counter(e.get("key") for e in entries).items()
                      if count > 1]
    unknown_tokens = [t["raw"] for t in final_citations
                      if t["kind"] == "bracket" and entry_numbers and
                      any(n not in entry_numbers for n in t.get("numbers", []))]
    audit = {
        "case_id": ctx.case_id, "style": style,
        "insertions_total": len(insertions), "applied": len(applied),
        "blocked": blocked, "dedupe": dedupe_report, "metadata_fixes": fixes,
        "final_numbers": final_numbers, "entry_numbers": entry_numbers,
        "missing_numbers": missing_numbers, "unknown_citation_tokens": unknown_tokens,
        "duplicate_keys": duplicate_keys,
        "citation_count": len(final_citations),
        "reference_count": len(ordered),
    }
    S.write_json(ctx.output / "citation-audit.json", audit)
    S.write_json(ctx.output / "citation-plan-applied.json", {
        "case_id": ctx.case_id, "applied": applied, "blocked": blocked,
        "key_numbers": key_numbers, "key_alias": key_alias,
    })
    docx_path = ctx.output / "manuscript-cited.docx"
    try:
        S.write_docx(full, docx_path, title="Cited manuscript")
        ok, why = S.docx_reopen_check(docx_path)
        if ok:
            ctx.result.add_artifact(docx_path, "editable_docx", "docx",
                                    "editable Word manuscript")
        else:
            ctx.result.uncertainty("docx_reopen", why, "DOCX may not open",
                                   "use manuscript-cited.md")
    except Exception as exc:  # noqa: BLE001
        ctx.result.uncertainty("docx_export", str(exc), "DOCX unavailable",
                               "use manuscript-cited.md")

    critical = (bool(blocked) or bool(missing_numbers) or bool(unknown_tokens)
                or bool(duplicate_keys) or not insertions)
    status = "completed" if not critical else "partial"
    if blocked:
        ctx.result.uncertainty("blocked_insertions", "%d insertions blocked" % len(blocked),
                               "some claims remain uncited", "fix anchors or keys")
    if missing_numbers or unknown_tokens:
        ctx.result.uncertainty("citation_sync", json.dumps(
            {"missing_numbers": missing_numbers, "unknown": unknown_tokens}, ensure_ascii=False),
                               "in-text citations and the reference list are not synchronized",
                               "renumber or repair entries")
    if duplicate_keys:
        ctx.result.uncertainty("duplicate_entries", duplicate_keys,
                               "bibliography has duplicate keys", "merge duplicates")
    if not insertions:
        ctx.result.uncertainty("insertions", "citation-plan.json has no insertions",
                               "no new citations were added", "write the citation plan")

    preserved = [{"kind": "number", "source": t["raw"], "target": t["raw"],
                  "locator": manuscript.get("source_path"), "value": t["raw"], "unit": ""}
                 for t in S.number_tokens(full)[:150]]
    evidence = []
    for row in applied:
        evidence.append({"claim_id": row.get("id"), "claim": row.get("reason", ""),
                         "source": {"path": manuscript.get("source_path"),
                                    "locator": row.get("sentence_id", "")},
                         "quote": row.get("anchor_text", ""), "supports": "citation"})
    ctx.result.add_artifact(ctx.output / "manuscript-cited.md", "deliverable", "md",
                            "manuscript with synchronized citations")
    ctx.result.add_artifact(ctx.output / "references.json", "reference_list", "json",
                            "formatted reference entries")
    ctx.result.add_artifact(ctx.output / "references-formatted.md", "reference_list", "md",
                            "human-readable reference list")
    if (ctx.output / "references.bib").is_file():
        ctx.result.add_artifact(ctx.output / "references.bib", "bibliography", "bib",
                                "original BibTeX entries")
    ctx.result.add_artifact(ctx.output / "citation-audit.json", "audit", "json",
                            "citation/reference synchronization audit")
    ctx.result.add_artifact(ctx.output / "citation-plan-applied.json", "change_record", "json",
                            "applied insertions and numbering")
    ctx.result.change("cite", manuscript.get("source_path"),
                      "%d insertions applied, %d blocked" % (len(applied), len(blocked)))
    ctx.result.change("renumber", "references", "numbering and reference list rebuilt")
    return ctx.result.finalize(
        status,
        deliverable=ctx.output / "manuscript-cited.md",
        result={"summary": "Applied %d citation insertions; %d references; style %s." % (
                    len(applied), len(ordered), style),
                "audit": audit},
        claims=[{"claim_id": row.get("id"), "text": row.get("reason", ""),
                 "evidence": row.get("sentence_id")} for row in applied],
        preserved_facts=preserved, evidence_map=evidence,
    )
