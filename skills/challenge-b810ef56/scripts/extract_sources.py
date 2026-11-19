#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the locatable source ledger (``work/sources.json``).

Each readable material becomes one ``SRCxx`` record with an honest path,
kind, scope, title, citation number, extracted-text sidecar and line/page
units.  No author, journal, DOI or year is invented: missing metadata stays
missing and is recorded in ``uncertainty``.

The ledger is the only source of citation numbers.  The model must read
``work/sources.json`` and cite the assigned ``citation_number``; no fallback
draft is generated here.

Usage:
    python3 extract_sources.py --input /input --work /app/work
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from . import evidence_kit
except ImportError:
    import evidence_kit


def _find_hint(case_data, rel_path):
    hints = (case_data or {}).get("sources") or []
    rel = rel_path.replace("\\", "/")
    name = Path(rel).name.lower()
    for hint in hints:
        if not isinstance(hint, dict):
            continue
        hint_path = str(hint.get("path") or "").replace("\\", "/").lstrip("./")
        if hint_path and (hint_path == rel or Path(hint_path).name.lower() == name):
            return hint
    return None


def _guess_kind(title, path, text):
    blob = ("%s %s %s" % (title, path, text[:1200])).lower()
    if "preprint" in blob or "预印本" in blob:
        return "preprint"
    if "retracted" in blob or "撤稿" in blob:
        return "review"  # keep a legal kind; retraction is recorded separately
    if "guideline" in blob or "指南" in blob:
        return "guideline"
    if "review" in blob or "综述" in blob:
        return "review"
    if "abstract" in blob and len(text) < 1500:
        return "abstract"
    if "dataset" in blob or "数据表" in blob:
        return "dataset"
    if "note" in blob or "笔记" in blob:
        return "note"
    return "original_research"


def _reference_entry(hint):
    """Only an explicit hint may supply a bibliographic reference entry.

    A reference list *inside* a material belongs to that paper's own
    bibliography, not to the paper itself, so it must not be reused as the
    paper's citation record.
    """
    value = hint.get("reference_entry") if isinstance(hint, dict) else None
    return str(value).strip() if value else None


def build(inventory, work_dir, case_data=None):
    work_dir = Path(work_dir)
    sources_dir = work_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    case_data = case_data or {}

    materials = [item for item in inventory.get("files") or []
                 if item.get("role") == "material"]
    hints = [item for item in (case_data.get("sources") or [])
             if isinstance(item, dict)]
    hint_order = {}
    for position, hint in enumerate(hints):
        hint_path = str(hint.get("path") or "").replace("\\", "/").lstrip("./")
        if hint_path:
            hint_order[Path(hint_path).name.lower()] = position

    def order_key(item):
        name = Path(item.get("path") or "").name.lower()
        return (hint_order.get(name, len(hints)), item.get("path") or "")

    sources = []
    uncertainty = []
    readable = [item for item in materials if item.get("readable")]
    for item in sorted(readable, key=order_key):
        rel_path = item["path"]
        text = evidence_kit.read_text(Path(work_dir) / item["text_path"])
        hint = _find_hint(case_data, rel_path) or {}
        title = str(hint.get("title") or "").strip() or evidence_kit.title_from_text(
            text, Path(rel_path).stem)
        kind = str(hint.get("kind") or "").strip() or _guess_kind(title, rel_path,
                                                                   text)
        if kind not in {"original_research", "review", "guideline", "preprint",
                        "dataset", "abstract", "note", "unknown"}:
            kind = "unknown"
        scope = str(hint.get("scope") or "").strip()
        if scope not in {"full", "abstract", "metadata", "excerpt", "unknown"}:
            scope = "abstract" if (kind == "abstract" or len(text) < 900) else "full"
        citation_number = hint.get("citation_number")
        if not isinstance(citation_number, int) or isinstance(citation_number, bool) \
                or citation_number <= 0:
            citation_number = None
        # Paper-level metadata is taken from the title/abstract head, not from
        # the first DOI/year anywhere in the file: a reference list inside the
        # material belongs to its cited works, not to this paper.
        head = text[:2000]
        doi = evidence_kit.doi_in(head)
        year = evidence_kit.year_in(head)
        source = {
            "source_id": "",
            "path": rel_path,
            "kind": kind,
            "scope": scope,
            "title": title,
            "citation_number": citation_number,
            "citation": {
                "doi": doi,
                "year": year,
                "authors": hint.get("authors") or None,
                "venue": hint.get("venue") or None,
            },
            "text_path": item["text_path"],
            "accessible": bool(item.get("readable")),
            "retracted_suspected": bool(re.search(
                r"\bretracted\b|撤稿", text, re.I)),
            "metadata_complete": bool(doi or year or hint.get("authors")),
            "reference_entry": _reference_entry(hint),
            "bibliography": evidence_kit.parse_bibliography(text),
            "units": evidence_kit.paragraph_units(text),
        }
        if evidence_kit.limitation_in(title) and kind == "original_research":
            source["kind"] = kind
        if source["retracted_suspected"]:
            uncertainty.append({
                "item": "retraction_suspected:%s" % rel_path,
                "reason": "material contains retraction/撤稿 wording",
                "impact": "do not assert its findings as current evidence",
                "next_check": "verify the publication status before citing",
            })
        if not source["metadata_complete"]:
            uncertainty.append({
                "item": "metadata_incomplete:%s" % rel_path,
                "reason": "no DOI/year/authors found in the material",
                "impact": "reference entry is a material-locator stub, not a full citation",
                "next_check": "complete the bibliographic record from the original source",
            })
        if not source["units"]:
            uncertainty.append({
                "item": "no_locatable_units:%s" % rel_path,
                "reason": "text extracted but no paragraph/table units were found",
                "impact": "claims from this source cannot receive a locator",
                "next_check": "inspect the raw text or convert the file again",
            })
        sources.append(source)

    used_numbers = set()
    next_number = 1
    for source in sources:
        number = source.get("citation_number")
        if number is None or number in used_numbers:
            while next_number in used_numbers:
                next_number += 1
            number = next_number
        used_numbers.add(number)
        source["citation_number"] = number
        if source.get("reference_entry") is None:
            source["reference_entry"] = _material_reference_entry(source)
    sources.sort(key=lambda item: item["citation_number"])
    for index, source in enumerate(sources, 1):
        source["source_id"] = "SRC%02d" % index
        target = sources_dir / (source["source_id"] + ".txt")
        evidence_kit.write_text(target, evidence_kit.read_text(Path(work_dir) / source["text_path"]))

    data = {
        "schema": "sources/v1",
        "case_id": case_data.get("case_id"),
        "sources": sources,
        "source_count": len(sources),
        "units_total": sum(len(item["units"]) for item in sources),
        "unreadable_materials": [
            {"path": item.get("path"), "reason": item.get("extraction_error")}
            for item in materials if not item.get("readable")
        ],
        "uncertainty": uncertainty,
        "generated_at": evidence_kit.now_iso(),
    }
    evidence_kit.write_json(work_dir / "sources.json", data)
    return data


def _material_reference_entry(source):
    title = source.get("title") or "Untitled material"
    parts = [title]
    doi = (source.get("citation") or {}).get("doi")
    year = (source.get("citation") or {}).get("year")
    if doi:
        parts.append("DOI: %s" % doi)
    if year:
        parts.append(str(year))
    parts.append("material: %s" % source.get("path"))
    return ". ".join(parts) + "."


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inventory = evidence_kit.read_json(Path(args.work) / "inventory.json") or {}
    case_data = evidence_kit.read_json(Path(args.input) / "case.json") or {}
    data = build(inventory, args.work, case_data)
    print(json.dumps({key: data.get(key) for key in
                      ("source_count", "units_total", "uncertainty")},
                     ensure_ascii=False, indent=2))
    return 0 if data.get("source_count") else 1


if __name__ == "__main__":
    sys.exit(main())
