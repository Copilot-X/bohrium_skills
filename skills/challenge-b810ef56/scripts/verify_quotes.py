#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify every evidence quote against the source text sidecar.

Verification order per quote:

1. exact substring of the extracted source text;
2. substring after collapsing whitespace (multi-line paragraphs);
3. ellipsis-separated segments (``...`` / ``…``) each found verbatim.

Only whitespace is normalised; characters, punctuation, order and spelling
must match.  A quote that cannot be found is reported as an error, never
silently accepted.  Missing/unreadable source text is a warning because the
quote may still be correct but cannot be machine-verified.

Usage:
    python3 verify_quotes.py --work /app/work --input /input --output /app/output
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


def _resolve_source_text(source, work_dir, input_dir):
    for key, root in (("text_path", work_dir), ("path", input_dir)):
        value = source.get(key)
        if not value:
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = Path(root) / value
        try:
            if candidate.is_file():
                return candidate, evidence_kit.read_text(candidate)
        except OSError:
            continue
    return None, None


def _segments(quote):
    return [part.strip() for part in re.split(r"\s*(?:\.\.\.|…)\s*", quote)
            if part.strip()]


def check_quote(quote, source_text):
    if not quote or not str(quote).strip():
        return False, "empty", "quote is empty"
    if source_text is None:
        return None, "unverified", "source text unavailable"
    quote = evidence_kit.normalize_space(quote)
    if quote in source_text:
        return True, "exact", "exact substring"
    if quote in evidence_kit.normalize_space(source_text):
        return True, "whitespace_normalized", "found after whitespace normalisation"
    segments = _segments(quote)
    if len(segments) > 1:
        haystack = evidence_kit.normalize_space(source_text)
        missing = [segment for segment in segments
                   if evidence_kit.normalize_space(segment) not in haystack]
        if not missing:
            return True, "ellipsis_segments", "%d segments found" % len(segments)
        return False, "ellipsis_segments_missing", "%d/%d segments missing" % (
            len(missing), len(segments))
    return False, "not_found", "quote is not a verbatim substring"


def build(ledger, sources_data, work_dir, input_dir):
    work_dir = Path(work_dir)
    input_dir = Path(input_dir)
    source_by_id = {item.get("source_id"): item
                    for item in sources_data.get("sources") or []}
    errors, warnings, rows = [], [], []
    for claim in ledger.get("claims") or []:
        for index, evidence in enumerate(claim.get("evidence") or []):
            source = source_by_id.get(evidence.get("source_id")) or {}
            resolved, source_text = _resolve_source_text(
                source, work_dir, input_dir)
            quote = evidence.get("quote") or ""
            ok, method, detail = check_quote(quote, source_text)
            row = {
                "claim_id": claim.get("claim_id"),
                "evidence_index": index,
                "source_id": evidence.get("source_id"),
                "locator": evidence.get("locator"),
                "quote": evidence_kit.excerpt(quote, 240),
                "source_text_path": str(resolved) if resolved else None,
                "ok": ok,
                "method": method,
                "detail": detail,
            }
            rows.append(row)
            if ok is None:
                warnings.append({
                    "code": "quote_unverified",
                    "message": detail,
                    "claim_id": claim.get("claim_id"),
                    "source_id": evidence.get("source_id"),
                })
            elif not ok:
                errors.append({
                    "code": "quote_not_verbatim",
                    "message": detail,
                    "claim_id": claim.get("claim_id"),
                    "source_id": evidence.get("source_id"),
                    "quote": evidence_kit.excerpt(quote, 240),
                })
            elif len(evidence_kit.compact(quote)) < 6:
                warnings.append({
                    "code": "quote_too_short",
                    "message": "quote is too short to support a claim",
                    "claim_id": claim.get("claim_id"),
                })
    return {
        "schema": "quote-audit/v1",
        "case_id": ledger.get("case_id") or sources_data.get("case_id"),
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "quotes": rows,
        "stats": {
            "checked": len(rows),
            "found": sum(1 for row in rows if row["ok"] is True),
            "not_found": sum(1 for row in rows if row["ok"] is False),
            "unverified": sum(1 for row in rows if row["ok"] is None),
            "exact": sum(1 for row in rows if row["method"] == "exact"),
            "normalized": sum(1 for row in rows
                              if row["method"] in {"whitespace_normalized",
                                                   "ellipsis_segments"}),
        },
        "generated_at": evidence_kit.now_iso(),
    }


def write(work_dir, output_dir, report):
    evidence_kit.write_json(Path(work_dir) / "quote-audit.json", report)
    evidence_kit.write_json(Path(output_dir) / "quote-audit.json", report)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    ledger_path = Path(args.ledger) if args.ledger else work_dir / "claims.json"
    ledger = evidence_kit.read_json(ledger_path) or {}
    sources_data = evidence_kit.read_json(work_dir / "sources.json") or {}
    report = build(ledger, sources_data, work_dir, args.input)
    write(work_dir, output_dir, report)
    print(json.dumps({key: report[key] for key in
                      ("ok", "errors", "warnings", "stats")},
                     ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
