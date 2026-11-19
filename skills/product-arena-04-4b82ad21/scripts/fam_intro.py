#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Writing 08 — introduction and related work with claim-citation binding."""
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


def _bib(ctx):
    entries = S.bib_entries_from_docs(ctx.docs)
    by_key = {}
    for entry in entries:
        by_key[str(entry.get("key"))] = entry
    return entries, by_key


def _bib_evidence(ctx, entry):
    """Return a verbatim bibliography record for a cited key.

    The full text of the cited paper is not part of /input in this task, so the
    honest evidence is the bibliographic record itself.  Never leave quote
    empty while claiming a source.
    """
    key = str(entry.get("key") or "")
    fields = entry.get("fields") or {}
    raw = str(entry.get("raw") or "").strip()
    title = str(fields.get("title") or "").strip()
    for doc in ctx.docs:
        text = S.doc_text(doc)
        rel = doc.get("rel_path") or doc.get("path") or ""
        if raw and raw in text:
            return {"path": rel, "locator": key, "quote": raw,
                    "supports": "bibliographic-record"}
        if title and title in text:
            return {"path": rel, "locator": key, "quote": title,
                    "supports": "bibliographic-record"}
    return {"path": "bibliography", "locator": key,
            "quote": raw or title, "supports": "bibliographic-record"}


def _segments(ctx):
    data = S.load_json(ctx.work / "evidence.json", {})
    segments = data.get("segments") if isinstance(data, dict) else []
    if segments:
        return [s for s in segments if s.get("quote")]
    out = []
    for doc in ctx.docs:
        text = S.doc_text(doc)
        if not text.strip():
            continue
        for segment in S.segment_blocks(text, source_id=doc.get("source_id", "SRC01"),
                                       path=doc.get("rel_path", "")):
            out.append({"path": doc.get("rel_path"), "locator": segment["locator"],
                        "quote": S.excerpt(segment["text"], 240),
                        "text": segment["text"]})
    return out


def _best_segment(statement, segments):
    statement = str(statement or "")
    words = set(re.findall(r"[A-Za-z]{4,}|[一-鿿]{2,6}", statement))
    numbers = set(re.findall(r"\d+(?:\.\d+)?%?", statement))
    best, best_score = None, 0
    for segment in segments:
        text = str(segment.get("text") or segment.get("quote") or "")
        score = len(words & set(re.findall(r"[A-Za-z]{4,}|[一-鿿]{2,6}", text)))
        score += 2 * len(numbers & set(re.findall(r"\d+(?:\.\d+)?%?", text)))
        if score > best_score:
            best, best_score = segment, score
    return best


def _segment_evidence(segment, supports="contribution-link"):
    if not segment:
        return None
    return {"path": segment.get("path"), "locator": segment.get("locator"),
            "quote": S.excerpt(str(segment.get("text") or segment.get("quote") or ""), 240),
            "supports": supports}


def _contribution_evidence(contribution, segments):
    raw = contribution.get("evidence") or []
    out = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and str(item.get("quote") or "").strip():
                out.append(item)
            elif isinstance(item, str) and item.strip():
                # A plan string is a pointer, not a source.  Resolve the
                # explicit "path Pxxx: hint" form first, then fall back to a
                # keyword/number match.  The delivered quote is always the
                # manuscript segment, never the plan string.
                pointer = re.search(
                    r"([A-Za-z0-9_.-]+\.(?:md|txt|json|tex|pdf))\s+([A-Za-z0-9_.:-]+)",
                    item)
                segment = None
                if pointer:
                    path, locator = pointer.group(1), pointer.group(2)
                    for candidate in segments:
                        if (str(candidate.get("path") or "").endswith(path)
                                and locator in str(candidate.get("locator") or "")):
                            segment = candidate
                            break
                if segment is None:
                    segment = _best_segment(item, segments)
                evidence = _segment_evidence(segment)
                if evidence:
                    out.append(evidence)
                else:
                    out.append({"path": "writing-plan", "locator": contribution.get("id", ""),
                                "quote": item.strip(), "supports": "contribution-plan"})
    if out:
        return out
    segment = _best_segment(contribution.get("statement"), segments)
    evidence = _segment_evidence(segment)
    return [evidence] if evidence else []


def _number_locator(ctx, raw):
    for doc in ctx.docs:
        text = S.doc_text(doc)
        pos = text.find(raw)
        if pos >= 0:
            line = text.count("\n", 0, pos) + 1
            return "%s:L%d" % (doc.get("rel_path") or doc.get("path") or "material", line)
    return "materials"


def prepare(ctx):
    doc = _primary_doc(ctx)
    if doc is None:
        ctx.result.change("prepare", "input", "no readable manuscript")
        ctx.result.uncertainty("manuscript", "no readable source text",
                               "introduction cannot be grounded", "provide materials")
        return ctx.result.finalize("input_error")
    text = S.doc_text(doc)
    segments = S.segment_blocks(text, source_id=doc.get("source_id", "SRC01"),
                                path=doc.get("rel_path", ""))
    entries, by_key = _bib(ctx)
    evidence = []
    for segment in segments:
        evidence.append({"segment_id": segment["id"], "path": doc.get("rel_path"),
                         "locator": segment["locator"], "quote": S.excerpt(segment["text"], 240),
                         "text": segment["text"][:800], "kind": segment.get("kind")})
    citations = S.citation_tokens(text)
    candidates = []
    for entry in entries:
        fields = entry.get("fields") or {}
        candidates.append({
            "key": entry.get("key"),
            "title": fields.get("title", ""),
            "year": fields.get("year", ""),
            "authors": fields.get("author", ""),
            "doi": fields.get("doi", ""),
        })
    S.write_json(ctx.work / "evidence.json", {
        "case_id": ctx.case_id, "source_path": doc.get("rel_path"),
        "segments": evidence, "citation_tokens": citations,
        "bibliography": candidates,
    })
    S.write_json(ctx.work / "writing-plan-template.json", {
        "case_id": ctx.case_id,
        "background": {"claims": [], "sources": []},
        "related_work_groups": [{"group": "group name", "dimension": "problem|method|data|metric",
                                 "works": [{"citation_key": "key", "summary": "",
                                            "limitation": ""}],
                                 "synthesis": ""}],
        "gap": {"statement": "", "derived_from": [], "evidence": []},
        "contributions": [{"id": "C1", "statement": "",
                           "linked_sections": [], "evidence": []}],
        "citation_coverage": [{"key": "key", "used_in": "Introduction|Related Work",
                               "claim": ""}],
    })
    outline = ["# Writing outline (source-grounded, not a draft)", "",
               "## Introduction", "- Background: problem, why now, constraints",
               "- Related work: grouped by comparison dimension, not paper-by-paper",
               "- Gap: derived from limitations of cited work",
               "- Contributions: each linked to a method/result/figure", "",
               "## Related Work", "- 2–4 groups; compare works on the same dimension",
               "- State each work's conclusion and limitation accurately", ""]
    S.write_text(ctx.work / "outline.md", "\n".join(outline))
    S.write_json(ctx.work / "bibliography.json", {"entries": candidates})
    ctx.result.change("prepare", doc.get("rel_path", ""),
                      "%d segments and %d bibliography entries" % (len(segments), len(entries)))
    return ctx.provisional(reason="materials and bibliography indexed; write introduction.md, related-work.md and writing-plan.json")


def _read_sections(ctx):
    intro = S.read_text(ctx.output / "introduction.md")
    related = S.read_text(ctx.output / "related-work.md")
    if not related:
        combined = S.read_text(ctx.output / "sections.md")
        if combined:
            match = re.search(r"(?im)^#{1,6}\s*(?:related work|相关工作)\s*$", combined)
            if match:
                # Use the heading positions to split the combined file.
                headings = list(re.finditer(r"(?im)^#{1,6}\s+(.+)$", combined))
                for index, heading in enumerate(headings):
                    title = heading.group(1).strip().lower()
                    end = headings[index + 1].start() if index + 1 < len(headings) else len(combined)
                    body = combined[heading.end():end].strip()
                    if "introduction" in title or "引言" in title:
                        intro = intro or body
                    elif "related" in title or "相关" in title:
                        related = related or body
            elif not intro:
                intro = combined
    return intro, related


def finalize(ctx):
    intro, related = _read_sections(ctx)
    if not intro.strip() and not related.strip():
        ctx.result.change("finalize", "output", "no introduction.md or related-work.md")
        ctx.result.uncertainty("deliverable", "model-authored sections are missing",
                               "cannot deliver sections", "write the two sections")
        return ctx.result.finalize("partial")
    plan = S.load_json(ctx.work / "writing-plan.json", {})
    if not isinstance(plan, dict):
        plan = {}
    entries, by_key = _bib(ctx)
    # Collapse duplicate bibliography records (same DOI/title/year) before
    # assigning numeric citation positions.
    seen_dedupe, unique_entries = set(), []
    for entry in entries:
        key = S.dedupe_key(entry)
        if key in seen_dedupe:
            continue
        seen_dedupe.add(key)
        unique_entries.append(entry)
    entries = unique_entries
    by_key = {str(e.get("key")): e for e in entries}
    source_text = "\n\n".join(S.doc_text(d) for d in ctx.docs)
    text = (intro + "\n\n" + related).strip()
    citation_tokens = S.citation_tokens(text)
    used_keys = set()
    for token in citation_tokens:
        used_keys.update(token.get("keys") or [])
    known_keys = set(by_key)
    unknown_keys = sorted(k for k in used_keys if known_keys and k not in known_keys)
    numeric_refs = sorted({n for token in citation_tokens
                           for n in token.get("numbers", [])})
    # Numeric citations point at bibliography positions when the
    # bibliography has no explicit [n] labels.
    number_to_key = {index: str(entry.get("key"))
                     for index, entry in enumerate(entries, 1)}
    for number in numeric_refs:
        if number in number_to_key:
            used_keys.add(number_to_key[number])
    bib_numbers = set()
    for entry in entries:
        raw = entry.get("raw", "")
        match = re.search(r"\[(\d+)\]", raw)
        if match:
            bib_numbers.add(int(match.group(1)))
    if not bib_numbers and entries:
        bib_numbers = set(range(1, len(entries) + 1))
    missing_numeric = sorted(n for n in numeric_refs if bib_numbers and n not in bib_numbers)
    invented = S.number_presence_missing(text, source_text)
    numbers = {"ok": not invented, "invented": invented}

    contributions = plan.get("contributions") or []
    if not contributions:
        # Extract explicit contribution bullets when the plan is absent.
        section = re.search(r"(?is)(?:contributions?|本文贡献|主要贡献)(.*?)(?:\n#{1,6}\s|\Z)", text)
        if section:
            for index, line in enumerate(re.findall(r"^\s*[-*+]\s+(.+)$", section.group(1), re.M), 1):
                contributions.append({"id": "C%d" % index, "statement": line.strip(),
                                      "linked_sections": [], "evidence": []})
    weak_contributions = [c for c in contributions
                          if not c.get("linked_sections") and not c.get("evidence")]
    groups = plan.get("related_work_groups") or []
    group_issues = []
    for group in groups:
        if len(group.get("works") or []) < 2:
            group_issues.append("group '%s' has fewer than 2 works" % group.get("group", ""))
    gap = plan.get("gap") or {}
    gap_issues = []
    if not gap.get("statement"):
        gap_issues.append("gap statement missing")
    if not gap.get("derived_from"):
        gap_issues.append("gap is not tied to cited work")
    cited_keys = used_keys
    if gap.get("derived_from"):
        not_cited = [k for k in gap["derived_from"] if k not in cited_keys]
        if not_cited:
            gap_issues.append("gap sources not cited in the sections: %s" % ", ".join(not_cited))

    audit = {
        "case_id": ctx.case_id,
        "citation_tokens": citation_tokens,
        "used_keys": sorted(used_keys),
        "known_keys": sorted(known_keys),
        "unknown_keys": unknown_keys,
        "numeric_refs": numeric_refs,
        "missing_numeric": missing_numeric,
        "numbers": numbers,
        "groups": len(groups),
        "group_issues": group_issues,
        "contributions": len(contributions),
        "weak_contributions": [c.get("id") for c in weak_contributions],
        "gap_issues": gap_issues,
    }
    S.write_json(ctx.output / "citation-audit.json", audit)
    S.write_json(ctx.output / "contributions.json", {
        "case_id": ctx.case_id, "contributions": contributions,
        "weak": [c.get("id") for c in weak_contributions],
    })
    S.write_json(ctx.output / "evidence-map.json", {
        "case_id": ctx.case_id,
        "plan": plan,
        "citation_coverage": plan.get("citation_coverage") or [],
        "sources": [{"key": k, "entry": by_key.get(k)} for k in sorted(used_keys) if k in by_key],
    })

    S.write_text(ctx.output / "introduction.md", intro.strip() + "\n" if intro.strip() else "")
    S.write_text(ctx.output / "related-work.md", related.strip() + "\n" if related.strip() else "")
    combined = "# Introduction\n\n%s\n\n# Related Work\n\n%s\n" % (
        intro.strip(), related.strip())
    S.write_text(ctx.output / "sections.md", combined)
    docx_path = ctx.output / "sections.docx"
    try:
        S.write_docx(combined, docx_path, title="Introduction and Related Work")
        ok, why = S.docx_reopen_check(docx_path)
        if not ok:
            ctx.result.uncertainty("docx_reopen", why, "DOCX may not open",
                                   "use sections.md")
    except Exception as exc:  # noqa: BLE001
        ctx.result.uncertainty("docx_export", str(exc), "DOCX unavailable", "use sections.md")
    preview = ctx.output / "sections-preview.pdf"
    pdf, pdf_err = S.write_pdf_preview(combined, preview, title="Introduction and Related Work")
    if pdf_err:
        ctx.result.uncertainty("pdf_preview", pdf_err, "preview unavailable", "inspect sections.md")

    all_groups_single = bool(groups) and all(
        len(group.get("works") or []) < 2 for group in groups)
    critical = (bool(unknown_keys) or bool(missing_numeric) or not numbers["ok"]
                or bool(gap_issues) or bool(weak_contributions)
                or all_groups_single
                or not intro.strip() or not related.strip())
    status = "completed" if not critical else "partial"
    if unknown_keys:
        ctx.result.uncertainty("unknown_citations", ", ".join(unknown_keys[:20]),
                               "cited keys are not in the bibliography", "fix keys or add entries")
    if missing_numeric:
        ctx.result.uncertainty("missing_reference_numbers", missing_numeric,
                               "numeric citations have no matching entries", "repair the reference list")
    if not numbers["ok"]:
        ctx.result.uncertainty("number_mismatch", json.dumps(numbers, ensure_ascii=False),
                               "numbers in sections do not match the materials", "restore source values")
    for issue in group_issues + gap_issues:
        ctx.result.uncertainty("argument", issue, "related-work structure needs work", "revise the plan")
    if weak_contributions:
        ctx.result.uncertainty("contributions", ", ".join(str(c.get("id")) for c in weak_contributions),
                               "contribution lacks linked evidence", "add method/result/figure links")

    preserved = [{"kind": "number", "source": t["raw"], "target": t["raw"],
                  "locator": _number_locator(ctx, t["raw"]), "value": t["raw"], "unit": ""}
                 for t in S.number_tokens(text)[:100]]
    segments = _segments(ctx)
    evidence_map = []
    bib_evidence_used = False
    for item in plan.get("citation_coverage") or []:
        key = str(item.get("key", ""))
        entry = by_key.get(key)
        if entry:
            source = _bib_evidence(ctx, entry)
            bib_evidence_used = True
        else:
            source = {"path": "bibliography", "locator": key, "quote": "",
                      "supports": "missing-bibliographic-record"}
        evidence_map.append({"claim_id": key,
                             "claim": item.get("claim", ""),
                             "source": {"path": source["path"],
                                        "locator": source["locator"]},
                             "quote": source.get("quote", ""),
                             "supports": item.get("used_in") or source.get("supports", "")})
    for contribution in contributions:
        items = _contribution_evidence(contribution, segments)
        if not items:
            ctx.result.uncertainty(
                "contribution_evidence", str(contribution.get("id") or ""),
                "no manuscript segment could be bound to this contribution",
                "add an explicit evidence quote to writing-plan.json")
        for item in items:
            evidence_map.append({"claim_id": contribution.get("id", ""),
                                 "claim": contribution.get("statement", ""),
                                 "source": {"path": item.get("path", ""),
                                            "locator": item.get("locator", "")},
                                 "quote": item.get("quote", ""),
                                 "supports": item.get("supports", "contribution")})
    if bib_evidence_used:
        ctx.result.uncertainty(
            "citation_fulltext", "cited works are represented by bibliographic records",
            "the input supplies bibliography metadata, not the full text of each cited paper",
            "bind claims to the cited paper's full text when it is provided")

    texts = {doc.get("rel_path") or doc.get("path"): S.doc_text(doc) for doc in ctx.docs}
    evidence_problems = []
    for item in evidence_map:
        quote = str(item.get("quote") or "").strip()
        source = item.get("source") or {}
        path = str(source.get("path") or "")
        if not quote:
            evidence_problems.append("%s: empty quote" % item.get("claim_id"))
            continue
        text = texts.get(path)
        if text is None:
            text = next((value for key, value in texts.items()
                         if key and path and str(key).endswith(path)), None)
        if text is None:
            evidence_problems.append("%s: source path not found: %s" % (
                item.get("claim_id"), path))
            continue
        ok, reason = S.quote_supported(text, quote)
        if not ok:
            evidence_problems.append("%s: %s" % (item.get("claim_id"), reason))
    if evidence_problems:
        status = "partial"
        ctx.result.uncertainty("evidence_map", "; ".join(evidence_problems[:12]),
                               "some claim-to-source quotes are empty or not verbatim",
                               "fix the source path/locator and copy the exact sentence")

    ctx.result.add_artifact(ctx.output / "introduction.md", "section", "md", "Introduction")
    ctx.result.add_artifact(ctx.output / "related-work.md", "section", "md", "Related Work")
    ctx.result.add_artifact(ctx.output / "sections.md", "deliverable", "md",
                            "combined editable sections")
    ctx.result.add_artifact(ctx.output / "citation-audit.json", "audit", "json",
                            "citation keys, numeric refs, numbers, groups and gap checks")
    ctx.result.add_artifact(ctx.output / "evidence-map.json", "evidence", "json",
                            "claim-to-source and citation coverage")
    ctx.result.add_artifact(ctx.output / "contributions.json", "contributions", "json",
                            "contribution-to-evidence links")
    if docx_path.is_file():
        ctx.result.add_artifact(docx_path, "editable_docx", "docx", "editable Word version")
    if pdf and pdf.is_file():
        ctx.result.add_artifact(pdf, "preview_pdf", "pdf",
                                "text-content PDF preview, not a Word layout render")
    ctx.result.change("write", "introduction+related-work",
                      "sections drafted and checked against bibliography")
    ctx.result.change("audit", "citations", "citation keys, numbers, gap and contributions checked")
    return ctx.result.finalize(
        status,
        deliverable=ctx.output / "sections.md",
        result={"summary": "Introduction and Related Work drafted; %d citations used; %d groups; %d contributions." % (
                    len(used_keys), len(groups), len(contributions)),
                "word_count": S.word_count(text), "audit": audit},
        claims=[{"claim_id": c.get("id"), "text": c.get("statement", ""),
                 "evidence": c.get("linked_sections") or c.get("evidence")}
                for c in contributions],
        preserved_facts=preserved, evidence_map=evidence_map,
    )
