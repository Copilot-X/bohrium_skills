#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit citation numbers, reference entries and unsupported assertions.

Checks are local and mechanical, never network lookups:

* every body ``[n]`` has a declared source with ``citation_number = n``;
* every cited number has a trailing ``[n]`` reference entry;
* reference entries that are never cited are reported as orphans;
* first-appearance order is reported (useful when the target style requires it);
* a sentence's citation marker should point at a source actually bound to one
  of its claims; a bound source should also be cited in the sentence;
* claims/sentences with no support are counted, not silently accepted.

Usage:
    python3 citecheck.py --work /app/work --output /app/output
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from . import evidence_kit
except ImportError:
    import evidence_kit


def _issue(target, code, message, **extra):
    item = {"code": code, "message": message}
    item.update(extra)
    target.append(item)


def build(text, sources_data, ledger):
    body = evidence_kit.body_text(text)
    used = evidence_kit.citation_numbers(body)
    order = evidence_kit.citation_order(text)
    refs = evidence_kit.reference_entries(text)
    sources = sources_data.get("sources") or []
    source_by_number = {}
    errors, warnings = [], []

    for source in sources:
        number = source.get("citation_number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            continue
        if number in source_by_number:
            _issue(errors, "citation_number_duplicate",
                   "more than one source declares citation_number %d" % number,
                   number=number)
        else:
            source_by_number[number] = source

    for number in sorted(used):
        if number not in source_by_number:
            _issue(errors, "citation_number_unknown",
                   "body cites [%d] but no source declares that number" % number,
                   number=number)
    for number, source in sorted(source_by_number.items()):
        if number not in used:
            _issue(warnings, "citation_uncited_source",
                   "source declares [%d] but the body never cites it" % number,
                   source_id=source.get("source_id"))

    for number in sorted(used):
        if number not in refs:
            _issue(errors, "reference_entry_missing",
                   "citation [%d] has no trailing reference entry" % number,
                   number=number)
    for number in sorted(refs):
        if number not in used:
            _issue(warnings, "reference_entry_orphan",
                   "reference entry [%d] is never cited in the body" % number,
                   number=number)

    ascending = order == sorted(order)
    if not ascending:
        _issue(warnings, "citation_order_not_ascending",
               "citation numbers first appear in order %s" % order,
               order=order)

    sentence_rows = []
    bound_claims = {}
    for claim in ledger.get("claims") or []:
        for evidence in claim.get("evidence") or []:
            bound_claims.setdefault(evidence.get("source_id"), set()).add(
                claim.get("claim_id"))
    for sentence in ledger.get("sentences") or []:
        cited = sorted(evidence_kit.citation_numbers(sentence.get("text") or ""))
        if not cited:
            continue
        bound = set()
        for claim_id in sentence.get("claims") or []:
            claim = next((item for item in ledger.get("claims") or []
                          if item.get("claim_id") == claim_id), None)
            if not claim:
                continue
            for evidence in claim.get("evidence") or []:
                bound.add(evidence.get("source_id"))
        cited_ids = {source_by_number[number]["source_id"]
                     for number in cited if number in source_by_number}
        for number in cited:
            source = source_by_number.get(number)
            if not source:
                continue
            if source.get("accessible") is False:
                _issue(warnings, "citation_source_inaccessible",
                       "[%d] points at an inaccessible source" % number,
                       sentence_id=sentence.get("sentence_id"),
                       source_id=source.get("source_id"))
            if source.get("retracted_suspected"):
                _issue(warnings, "citation_source_retraction_suspected",
                       "[%d] source contains retraction wording" % number,
                       source_id=source.get("source_id"))
            if source.get("source_id") not in bound:
                _issue(warnings, "citation_source_not_bound",
                       "[%d] is cited but no claim of this sentence uses %s"
                       % (number, source.get("source_id")),
                       sentence_id=sentence.get("sentence_id"),
                       number=number)
        for source_id in sorted(bound - cited_ids):
            _issue(warnings, "citation_missing_for_bound_source",
                   "claim uses %s but the sentence does not cite it" % source_id,
                   sentence_id=sentence.get("sentence_id"),
                   source_id=source_id)
        sentence_rows.append({
            "sentence_id": sentence.get("sentence_id"),
            "cited_numbers": cited,
            "bound_source_ids": sorted(bound),
        })

    unsupported_claims = [claim.get("claim_id")
                          for claim in ledger.get("claims") or []
                          if claim.get("status") == "unsupported"]
    unsupported_sentences = [sentence.get("sentence_id")
                             for sentence in ledger.get("sentences") or []
                             if sentence.get("support") == "none"]
    if unsupported_claims:
        _issue(warnings, "unsupported_claims_present",
               "%d claim(s) have no evidence" % len(unsupported_claims),
               claims=unsupported_claims)
    if unsupported_sentences:
        _issue(warnings, "unsupported_sentences_present",
               "%d sentence(s) are unsupported and must be deleted or marked"
               % len(unsupported_sentences),
               sentences=unsupported_sentences)

    citation_index = []
    for number in sorted(used | set(source_by_number)):
        source = source_by_number.get(number) or {}
        sentence_ids = [row["sentence_id"] for row in sentence_rows
                        if number in row["cited_numbers"]]
        bound = any(
            source.get("source_id") in row["bound_source_ids"]
            for row in sentence_rows if number in row["cited_numbers"])
        citation_index.append({
            "number": number,
            "source_id": source.get("source_id"),
            "path": source.get("path"),
            "title": source.get("title"),
            "used_in_sentences": sentence_ids,
            "reference_entry": refs.get(number),
            "accessible": source.get("accessible"),
            "bound_to_claim": bound,
        })

    return {
        "schema": "citation-audit/v1",
        "case_id": sources_data.get("case_id"),
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "cited_numbers": sorted(used),
            "declared_numbers": sorted(source_by_number),
            "reference_entries": sorted(refs),
            "first_appearance_order": order,
            "order_ascending": ascending,
            "sentences_with_citations": len(sentence_rows),
            "unsupported_claims": unsupported_claims,
            "unsupported_sentences": unsupported_sentences,
        },
        "citation_index": citation_index,
        "sentence_citations": sentence_rows,
        "generated_at": evidence_kit.now_iso(),
    }


def write(work_dir, output_dir, report):
    evidence_kit.write_json(Path(work_dir) / "citation-audit.json", report)
    evidence_kit.write_json(Path(output_dir) / "citation-audit.json", report)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--text", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    text_path = Path(args.text) if args.text else output_dir / "text.md"
    sources_data = evidence_kit.read_json(work_dir / "sources.json") or {}
    ledger = evidence_kit.read_json(work_dir / "claims.json") or {}
    report = build(evidence_kit.read_text(text_path), sources_data, ledger)
    write(work_dir, output_dir, report)
    print(json.dumps({key: report[key] for key in
                      ("ok", "errors", "warnings", "stats")},
                     ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
