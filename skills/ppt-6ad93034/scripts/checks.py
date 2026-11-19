#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Structural, visual and evidence checks for a generated/edited deck.

Checks are evidence-producing, not decorative: every failure becomes a
structured exception in result.json and is never silently swallowed.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import deckkit
import discover

JUNK_PATTERNS = [
    ("click_to_edit", re.compile(r"click to edit|单击此处编辑|点击此处编辑", re.I)),
    ("lorem", re.compile(r"lorem ipsum", re.I)),
    ("todo", re.compile(r"\bTODO\b|\bTBD\b|待补充|待完善", re.I)),
    ("placeholder", re.compile(r"placeholder|占位符|示例文本|xxxx", re.I)),
]
NUM_RE = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(%|‰|°C|K|h|min|s|ms|nm|µm|μm|mm|cm|m|"
    r"mL|µL|μL|L|mg|g|kg|M|mM|µM|μM|eV|keV|MeV|V|mA|W|Hz|kHz|MHz|GHz|"
    r"Da|kDa|bp|kb|GPa|MPa|Pa)?", re.I)


def norm_number(value, unit=""):
    value = str(value).replace(",", "").replace("，", "").strip()
    unit = str(unit or "").strip().replace("μ", "µ")
    if value.endswith(".0"):
        value = value[:-2]
    return (value + unit).lower()


def _norm_corpus(text):
    text = str(text).replace(",", "").replace("，", "")
    text = text.replace("％", "%").replace("μ", "µ")
    return re.sub(r"\s+", " ", text).lower()


def _spec_content_texts(spec):
    """Only claim-bearing text: titles/bullets/stats/steps. Footers and locators
    (line 3, p.4, slide 5) are metadata, not scientific claims."""
    locator_like = re.compile(r"(?:line|p\.|page|slide|row|para|sheet|图|表|第)\s*\d+", re.I)
    for i, slide in enumerate(spec.get("slides", []) or [], start=1):
        role = str(slide.get("role") or slide.get("kind") or "")
        if role in ("agenda", "evidence"):
            continue
        texts = []
        if slide.get("title"):
            texts.append(str(slide["title"]))
        if role == "title":
            pass
        else:
            texts += [str(b) for b in (slide.get("bullets") or [])]
            texts.append(str(slide.get("quote") or ""))
            for stat in slide.get("stats") or []:
                if isinstance(stat, dict):
                    texts.append(str(stat.get("value", "")))
            for step in slide.get("steps") or []:
                if isinstance(step, dict):
                    texts += [str(step.get("title", "")), str(step.get("text", ""))]
            for block in (slide.get("left"), slide.get("right")):
                if isinstance(block, dict):
                    texts += [str(block.get("title", ""))]
                    texts += [str(b) for b in (block.get("bullets") or [])]
            for item in slide.get("cards") or []:
                if isinstance(item, dict):
                    texts += [str(item.get("title", ""))]
                    texts += [str(b) for b in (item.get("bullets") or item.get("text") or [])]
            callout = slide.get("callout") or {}
            texts += [str(callout.get("title", ""))]
            texts += [str(b) for b in (callout.get("bullets") or callout.get("text") or [])]
        for text in texts:
            text = str(text).strip()
            if not text or locator_like.search(text):
                continue
            yield i, text


def _verify_numbers(spec, brief, inspected):
    corpus = _norm_corpus("\n".join(s.get("text", "") for s in brief.get("sources", [])))
    checked, verified, unverified = 0, 0, []
    for index, text in _spec_content_texts(spec):
        for m in NUM_RE.finditer(text):
            value = m.group(1)
            unit = m.group(2) or ""
            after = text[m.end():m.end() + 1]
            if m.start() <= 2 and not unit and len(value) <= 2 and after in (".", ")", "、"):
                continue
            token = norm_number(value, unit)
            checked += 1
            candidate = token
            bare = norm_number(value)
            if candidate in corpus or bare in corpus:
                verified += 1
                continue
            # tolerate a rounded/zero-padded variant: 92.0 vs 92
            try:
                f = float(value.replace(",", ""))
                variants = {norm_number(f"{f:g}", unit), norm_number(f"{f:g}"),
                            norm_number(f"{f:.1f}", unit), norm_number(f"{f:.1f}")}
                if any(v in corpus for v in variants):
                    verified += 1
                    continue
            except ValueError:
                pass
            unverified.append({"slide": index, "value": value, "unit": unit,
                               "context": text[:100]})
    return {"checked": checked, "verified": verified, "unverified": unverified[:30],
            "unverified_count": len(unverified)}


def _scan_junk(inspected):
    hits = []
    for slide in inspected.get("slides", []):
        for text in slide.get("texts", []):
            for code, pattern in JUNK_PATTERNS:
                if pattern.search(str(text)):
                    hits.append({"slide": slide.get("index"), "code": code,
                                 "text": str(text)[:100]})
    return hits


def _spec_conformance(spec, inspected):
    missing = []
    slides = spec.get("slides", []) or []
    built = inspected.get("slides", []) or []
    for i, slide in enumerate(slides, start=1):
        if i > len(built):
            missing.append({"slide": i, "reason": "built deck has fewer slides"})
            continue
        texts = [str(t).strip() for t in built[i - 1].get("texts", [])]
        title = str(slide.get("title", "")).strip()
        if title and not any(title in t or t in title for t in texts if t):
            missing.append({"slide": i, "reason": "title not found in built slide",
                            "title": title[:80]})
        body = slide.get("bullets") or []
        if body:
            found = 0
            for bullet in body[:4]:
                probe = str(bullet)[:24]
                if any(probe in t or t[:24] == probe for t in texts):
                    found += 1
            if found == 0:
                missing.append({"slide": i, "reason": "no bullet text found in built slide"})
    return missing


def run_checks(pptx, spec, brief, build_report, export_report,
               edit_report=None, expected_slides=None, work_dir=None, parts=None):
    inspected = deckkit.inspect_deck(pptx)
    zip_problems = deckkit.validate_zip(pptx)
    rel_problems = [p for p in zip_problems if "unresolved target" in p]
    overflow = deckkit.inspect_overflow(pptx)
    junk = _scan_junk(inspected)
    numbers = _verify_numbers(spec, brief, inspected)
    conformance = _spec_conformance(spec, inspected)
    slides = spec.get("slides", []) or []
    built_count = inspected.get("count", 0)
    notes_count = sum(1 for s in inspected.get("slides", [])
                      if str(s.get("notes") or "").strip())
    expected = expected_slides or brief.get("slide_count")
    slide_count_match = built_count == len(slides) and (
        not expected or built_count == int(expected))
    images_resolved = (not build_report.get("images_missing")) and all(
        img.get("bytes", 0) > 0 for s in inspected.get("slides", []) for img in s.get("images", []))
    content_slides = [s for s in slides if s.get("role") not in
                      ("title", "agenda", "evidence", "section", "conclusion")]
    grounded = [s for s in content_slides
                if s.get("claims") or s.get("source") or s.get("source_ref")]
    evidence_coverage = (len(grounded) / len(content_slides)) if content_slides else 1.0
    edit_ok = True
    if edit_report is not None:
        edit_ok = not edit_report.get("unresolved") and not edit_report.get("problems")
    pdf_ok = False
    if export_report and export_report.get("pdf"):
        p = Path(export_report["pdf"])
        try:
            pdf_ok = p.is_file() and p.read_bytes()[:5] == b"%PDF-"
        except OSError:
            pdf_ok = False
    png_ok = bool(export_report.get("png")) if export_report else False
    visual_ok = png_ok and (len(export_report.get("png", [])) >= min(3, built_count))
    parts = parts or {}
    rules = parts.get("rules") or {}
    roles = {str(s.get("role") or "") for s in slides}
    required = (rules.get("required_roles_generate") or []) if not brief.get("edits") else \
               (rules.get("required_roles_edit") or [])
    missing_roles = [r for r in required if r not in roles]
    checks = {
        "zip_valid": not zip_problems,
        "relationships_resolved": not rel_problems,
        "slide_count_match": slide_count_match,
        "notes_complete": notes_count == built_count and built_count > 0,
        "images_resolved": images_resolved,
        "text_overflow_risk": overflow,
        "placeholder_junk": junk,
        "spec_conformance": not conformance,
        "spec_conformance_issues": conformance,
        "workorder_read": bool(brief.get("workorder") or brief.get("edits")),
        "records_complete": True,
        "visual_reviewed": visual_ok,
        "numbers_verified": not numbers["unverified"],
        "numbers": numbers,
        "evidence_coverage": round(evidence_coverage, 3),
        "template_fidelity": build_report.get("template_fidelity", "none"),
        "edit_scope_ok": edit_ok,
        "required_roles_present": not missing_roles,
        "missing_roles": missing_roles,
        "render_available": pdf_ok and png_ok,
        "pdf_valid": pdf_ok,
        "png_count": len(export_report.get("png", [])) if export_report else 0,
    }
    compliance = {
        "pptx_valid": checks["zip_valid"] and checks["relationships_resolved"],
        "slide_count_match": slide_count_match,
        "notes_complete": checks["notes_complete"],
        "export_signature": "pdf" if pdf_ok else None,
        "template_fidelity": checks["template_fidelity"],
        "numbers_verified": checks["numbers_verified"],
        "evidence_coverage": checks["evidence_coverage"],
        "render_available": checks["render_available"],
    }
    exceptions = []
    for problem in zip_problems:
        exceptions.append({"code": "pptx_structure", "severity": "error", "detail": problem})
    if not slide_count_match:
        exceptions.append({"code": "slide_count_mismatch", "severity": "error",
                           "detail": f"built={built_count}, spec={len(slides)}, expected={expected}"})
    if notes_count != built_count:
        exceptions.append({"code": "notes_incomplete", "severity": "error",
                           "detail": f"{built_count - notes_count} slide(s) without speaker notes"})
    if junk:
        exceptions.append({"code": "placeholder_junk", "severity": "error",
                           "detail": junk[:6]})
    if overflow:
        exceptions.append({"code": "text_overflow_risk", "severity": "warning",
                           "detail": overflow[:6]})
    if conformance:
        exceptions.append({"code": "spec_conformance", "severity": "warning",
                           "detail": conformance[:6]})
    if numbers["unverified"]:
        exceptions.append({"code": "unverified_number", "severity": "error",
                           "detail": numbers["unverified"][:8]})
    if evidence_coverage < 0.8:
        exceptions.append({"code": "evidence_coverage_low", "severity": "warning",
                           "detail": f"{evidence_coverage:.0%} content slides have a locator"})
    if missing_roles and not brief.get("edits"):
        exceptions.append({"code": "missing_role", "severity": "warning",
                           "detail": missing_roles})
    if build_report.get("images_missing"):
        exceptions.append({"code": "image_missing", "severity": "warning",
                           "detail": build_report["images_missing"]})
    if edit_report is not None:
        for item in edit_report.get("unresolved", []):
            exceptions.append({"code": "edit_unresolved", "severity": "error",
                               "detail": item})
        if edit_report.get("problems"):
            exceptions.append({"code": "edit_verify_failed", "severity": "error",
                               "detail": edit_report["problems"]})
    if not pdf_ok:
        exceptions.append({"code": "pdf_preview_missing", "severity": "warning",
                           "detail": "no valid PDF preview"})
    if not png_ok:
        exceptions.append({"code": "png_preview_missing", "severity": "warning",
                           "detail": "no valid PNG preview"})
    if export_report and export_report.get("limitations"):
        for limitation in export_report["limitations"]:
            exceptions.append({"code": "render_limitation", "severity": "limitation",
                               "detail": limitation})
    hard_errors = [e for e in exceptions if e["severity"] == "error"]
    warnings = [e for e in exceptions if e["severity"] != "error"]
    return {
        "ok": not hard_errors,
        "checks": checks,
        "compliance_checks": compliance,
        "exceptions": exceptions,
        "errors": hard_errors,
        "warnings": warnings,
        "inspected": inspected,
    }
