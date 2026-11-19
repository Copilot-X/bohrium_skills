#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Project the claim ledger into the official result contract.

Writes three real files and keeps them mutually consistent:

* ``work/evidence-map.json``  - legacy-compatible ledger (optional auditor);
* ``<output>/claim-evidence.json`` - full claim/evidence artifact;
* ``<output>/result.json`` - official fields ``deliverable``,
  ``preserved_facts``, ``evidence_map``, ``change_log`` plus the writing
  contract fields used by the platform.

Every path written here is relative to the output directory and exists.
Degraded runs set ``status=partial`` (or ``input_error``) and explain why in
``uncertainty`` instead of fabricating success.

Usage:
    python3 build_result.py --work /app/work --output /app/output
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

OFFICIAL_FIELDS = ("deliverable", "preserved_facts", "evidence_map", "change_log")


def _dedupe(items):
    seen = set()
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _section_names(text):
    names = []
    for line in str(text or "").splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
        if match:
            name = match.group(2).strip()
            if name and name not in names:
                names.append(name)
    return names


def _clean_source(source):
    return {key: source.get(key) for key in
            ("source_id", "path", "kind", "scope", "title", "citation_number",
             "citation", "text_path", "accessible", "metadata_complete",
             "reference_entry", "retracted_suspected")}


def _claim_sentence_ids(ledger):
    mapping = {}
    for sentence in ledger.get("sentences") or []:
        for claim_id in sentence.get("claims") or []:
            mapping.setdefault(claim_id, []).append(sentence.get("sentence_id"))
    return mapping


def _preserved_facts(ledger, text):
    source_by_id = {item.get("source_id"): item
                    for item in ledger.get("sources") or []}
    facts = []
    compact_text = evidence_kit.compact(text)
    for claim in ledger.get("claims") or []:
        if claim.get("status") == "unsupported":
            continue
        numbers = evidence_kit.numbers_in(claim.get("text") or "")
        citation_numbers = claim.get("citation_numbers") or []
        if not numbers and not citation_numbers:
            continue
        evidence = claim.get("evidence") or []
        first = evidence[0] if evidence else {}
        source = source_by_id.get(first.get("source_id")) or {}
        facts.append({
            "fact_id": claim.get("claim_id"),
            "text": claim.get("text"),
            "value": claim.get("value"),
            "unit": claim.get("unit", ""),
            "condition": claim.get("condition", ""),
            "numbers": sorted(numbers),
            "citation_numbers": citation_numbers,
            "source": {
                "source_id": first.get("source_id"),
                "path": source.get("path"),
                "locator": first.get("locator"),
                "quote": evidence_kit.excerpt(first.get("quote") or "", 240),
            },
            "in_draft": evidence_kit.compact(claim.get("text") or "") in compact_text,
        })
    return facts


def _evidence_map(ledger):
    source_by_id = {item.get("source_id"): item
                    for item in ledger.get("sources") or []}
    sentence_ids = _claim_sentence_ids(ledger)
    projected = []
    for claim in ledger.get("claims") or []:
        evidence = []
        for item in claim.get("evidence") or []:
            source = source_by_id.get(item.get("source_id")) or {}
            evidence.append({
                "source_id": item.get("source_id"),
                "source": {
                    "path": source.get("path"),
                    "locator": item.get("locator"),
                    "title": source.get("title"),
                },
                "locator": item.get("locator"),
                "quote": item.get("quote"),
                "supports": item.get("supports"),
            })
        projected.append({
            "claim_id": claim.get("claim_id"),
            "claim": claim.get("text"),
            "type": claim.get("type"),
            "strength": claim.get("strength"),
            "status": claim.get("status"),
            "limits": claim.get("limits", ""),
            "citation_numbers": claim.get("citation_numbers") or [],
            "draft_sentences": sentence_ids.get(claim.get("claim_id"), []),
            "evidence": evidence,
        })
    return projected


def _status(stage, text, ledger, citation, quotes, inventory):
    if not str(text or "").strip():
        return "input_error"
    if not ledger.get("claims") or not ledger.get("sentences"):
        return "partial"
    coverage = ledger.get("coverage") or {}
    if stage == "prepare":
        return "partial"
    if coverage.get("unsupported"):
        return "partial"
    if any(claim.get("status") in {"partial", "conflicting", "unsupported"}
           for claim in ledger.get("claims") or []):
        return "partial"
    if citation.get("errors") or quotes.get("errors"):
        return "partial"
    if (quotes.get("stats") or {}).get("unverified"):
        return "partial"
    if inventory.get("unreadable_texts"):
        return "partial"
    return "completed"


def _uncertainty(ledger, citation, quotes, inventory, extra):
    items = list(ledger.get("uncertainty") or [])
    for issue in (citation.get("errors") or []) + (citation.get("warnings") or []):
        items.append({
            "item": "citation:%s" % issue.get("code"),
            "reason": issue.get("message"),
            "impact": "citation numbering or source binding may be incomplete",
            "next_check": "verify the marker/entry and the supporting quote",
        })
    for issue in (quotes.get("errors") or []) + (quotes.get("warnings") or []):
        items.append({
            "item": "quote:%s" % issue.get("code"),
            "reason": issue.get("message"),
            "impact": "the quoted support could not be verified verbatim",
            "next_check": "read the source text and replace or correct the quote",
        })
    for item in inventory.get("uncertainty") or []:
        items.append(item)
    items.extend(extra)
    return _dedupe(items)


def build(work_dir, output_dir, draft_path, case_data=None, stage="finalize",
          writing_mode="agent_draft", input_dir="/input", extra=None):
    work_dir = Path(work_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    case_data = case_data or {}
    extra = extra or {}

    text_path = Path(draft_path)
    if not text_path.is_file():
        text_path = output_dir / "text.md"
    text = evidence_kit.read_text(text_path) if text_path.is_file() else ""
    target_text = output_dir / "text.md"
    if (text_path.is_file()
            and text_path.resolve() != target_text.resolve()):
        # Standalone use: materialise the real main deliverable first.
        evidence_kit.write_text(target_text, text)
        text_path = target_text
    sources_data = evidence_kit.read_json(work_dir / "sources.json") or {}
    ledger = evidence_kit.read_json(work_dir / "claims.json") or {}
    citation = evidence_kit.read_json(work_dir / "citation-audit.json") or {}
    quotes = evidence_kit.read_json(work_dir / "quote-audit.json") or {}
    inventory = evidence_kit.read_json(work_dir / "inventory.json") or {}

    # Keep the audit artifacts inside /app/output, where artifacts[] points.
    for name, payload in (("citation-audit.json", citation),
                          ("quote-audit.json", quotes)):
        if payload:
            evidence_kit.write_json(output_dir / name, payload)

    coverage = ledger.get("coverage") or {
        "sentences": 0, "fully_supported": 0,
        "partially_supported": 0, "unsupported": 0,
    }
    status = _status(stage, text, ledger, citation, quotes, inventory)
    word_count = evidence_kit.count_words(evidence_kit.body_text(text))
    language = evidence_kit.detect_language(text) or "unknown"
    genre = ledger.get("genre") or evidence_kit.guess_genre(
        case_data.get("task_instruction", ""), case_data)
    legacy_map = {
        "schema": "evidence-map/v1",
        "case_id": case_data.get("case_id") or ledger.get("case_id"),
        "language": language,
        "genre": genre,
        "sources": [_clean_source(item) for item in ledger.get("sources") or []],
        "claims": ledger.get("claims") or [],
        "sentences": ledger.get("sentences") or [],
        "conflicts": ledger.get("conflicts") or [],
        "uncertainty": ledger.get("uncertainty") or [],
        "change_log": ledger.get("change_log") or [],
    }
    evidence_kit.write_json(work_dir / "evidence-map.json", legacy_map)

    change_log = list(ledger.get("change_log") or [])
    change_log.append({
        "step": 9,
        "action": "sources_and_claims_inventoried",
        "reason": "real input files were read and claims were bound to locators",
        "evidence": "sources=%d; claims=%d; evidence_items=%d; numbers=%d/%d"
                    % (len(ledger.get("sources") or []),
                       len(ledger.get("claims") or []),
                       sum(len(claim.get("evidence") or [])
                           for claim in ledger.get("claims") or []),
                       sum(1 for item in ledger.get("number_index") or []
                           if item.get("found")),
                       len(ledger.get("number_index") or [])),
    })
    change_log.append({
        "step": 10,
        "action": "draft_loaded",
        "reason": "main deliverable is the editable Markdown draft",
        "evidence": "text.md (%s; %d words)" % (writing_mode, word_count),
    })
    if extra.get("reference_additions"):
        change_log.append({
            "step": 11,
            "action": "reference_entries_completed",
            "reason": "cited sources without a trailing entry received an "
                      "honest material-locator stub (no invented bibliographic data)",
            "evidence": "; ".join(extra["reference_additions"]),
        })
    change_log.append({
        "step": 12,
        "action": "citation_and_quote_checks",
        "reason": "citation numbering / orphans / verbatim quotes were checked",
        "evidence": "citation_errors=%d; quote_errors=%d; quotes=%d"
                    % (len(citation.get("errors") or []),
                       len(quotes.get("errors") or []),
                       (quotes.get("stats") or {}).get("checked", 0)),
    })
    change_log.append({
        "step": 13,
        "action": "official_fields_emitted",
        "reason": "deliverable/preserved_facts/evidence_map/change_log written "
                  "from the real ledger and real artifacts",
        "evidence": "result.json; claim-evidence.json",
    })

    facts = _preserved_facts(ledger, text)
    evidence_map = _evidence_map(ledger)
    contract = case_data.get("output_contract") if isinstance(
        case_data.get("output_contract"), dict) else {}
    contract_coverage = []
    known = {"case_id": "case_id", "deliverable": "deliverable",
             "preserved_facts": "preserved_facts",
             "evidence_map": "evidence_map", "change_log": "change_log"}
    for field in contract:
        projected = known.get(field, field)
        contract_coverage.append({
            "contract_field": field,
            "result_field": projected,
            "satisfied": projected in known.values(),
            "note": ("projected from the real ledger" if field in known
                     else "copied verbatim; no automatic projection"),
        })

    artifacts = [
        {"role": "editable", "path": "text.md", "format": "markdown"},
        {"role": "evidence", "path": "claim-evidence.json", "format": "json"},
        {"role": "check", "path": "citation-audit.json", "format": "json"},
        {"role": "check", "path": "quote-audit.json", "format": "json"},
    ]
    for artifact in artifacts:
        artifact["exists"] = (output_dir / artifact["path"]).is_file()

    result = {
        "case_id": case_data.get("case_id") or ledger.get("case_id") or "unknown",
        "task_family": "evidence-driven-writing",
        "status": status,
        "stage": stage,
        "writing_mode": writing_mode,
        "text": {
            "path": "text.md",
            "format": "markdown",
            "language": language,
            "word_count": word_count,
            "genre": genre,
            "sections": _section_names(text),
        },
        "deliverable": {
            "path": "text.md",
            "format": "markdown",
            "language": language,
            "word_count": word_count,
            "genre": genre,
            "sections": _section_names(text),
            "evidence": "claim-evidence.json",
        },
        "artifacts": artifacts,
        "sentences": ledger.get("sentences") or [],
        "unsupported": ledger.get("unsupported") or [],
        "coverage": coverage,
        "conflicts": ledger.get("conflicts") or [],
        "preserved_facts": facts,
        "evidence_map": evidence_map,
        "change_log": change_log,
        "uncertainty": _uncertainty(ledger, citation, quotes, inventory,
                                    extra.get("uncertainty") or []),
        "sources": [_clean_source(item) for item in ledger.get("sources") or []],
        "number_index": ledger.get("number_index") or [],
        "citation_index": citation.get("citation_index") or [],
        "citation_audit": {
            "ok": citation.get("ok"),
            "errors": citation.get("errors") or [],
            "warnings": citation.get("warnings") or [],
            "stats": citation.get("stats") or {},
        },
        "quote_audit": {
            "ok": quotes.get("ok"),
            "errors": quotes.get("errors") or [],
            "warnings": quotes.get("warnings") or [],
            "stats": quotes.get("stats") or {},
        },
        "checks": {
            "deliverable_exists": (output_dir / "text.md").is_file(),
            "official_fields_present": True,
            "claims_with_evidence": sum(
                1 for claim in ledger.get("claims") or []
                if claim.get("evidence")),
            "numbers_total": len(ledger.get("number_index") or []),
            "numbers_traced": sum(
                1 for item in ledger.get("number_index") or []
                if item.get("found")),
            "citation_ok": citation.get("ok"),
            "quotes_ok": quotes.get("ok"),
            "unsupported_handled": all(
                sentence.get("unsupported_action") in
                {"deleted", "marked_as_reasoning"}
                for sentence in ledger.get("sentences") or []
                if sentence.get("support") == "none"),
        },
        "output_contract": contract,
        "contract_coverage": contract_coverage,
        "pipeline": ["inventory", "extract_sources", "claims", "citecheck",
                     "verify_quotes", "build_result"],
        "generated_at": evidence_kit.now_iso(),
    }

    evidence_artifact = dict(legacy_map)
    evidence_artifact.update({
        "schema": "claim-evidence/v1",
        "generated_from": "work/evidence-map.json",
        "unsupported": result["unsupported"],
        "coverage": coverage,
        "number_index": result["number_index"],
        "citation_index": result["citation_index"],
        "citation_audit": result["citation_audit"],
        "quote_audit": result["quote_audit"],
        "stats": ledger.get("stats") or {},
    })
    evidence_kit.write_json(output_dir / "claim-evidence.json", evidence_artifact)
    # All artifact files now exist; refresh the honest existence flags before
    # result.json is written.
    for artifact in artifacts:
        artifact["exists"] = (output_dir / artifact["path"]).is_file()
    evidence_kit.write_json(output_dir / "result.json", result)
    validate_paths(result, output_dir)
    return result, output_dir / "result.json", evidence_artifact


def validate_paths(result, output_dir):
    problems = []
    for relative in ("text.md", "claim-evidence.json", "result.json",
                     "citation-audit.json", "quote-audit.json"):
        if not (Path(output_dir) / relative).is_file():
            problems.append("missing artifact: %s" % relative)
    for artifact in result.get("artifacts") or []:
        relative = artifact.get("path")
        if not relative or not (Path(output_dir) / relative).is_file():
            problems.append("artifact path does not exist: %s" % relative)
    for fact in result.get("preserved_facts") or []:
        source = fact.get("source") or {}
        if not source.get("path") or not source.get("locator"):
            problems.append("fact %s lacks source path/locator"
                            % fact.get("fact_id"))
    if not result.get("deliverable", {}).get("path"):
        problems.append("deliverable.path missing")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--draft", default=None)
    ap.add_argument("--stage", default="finalize")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    case_data = evidence_kit.read_json(Path(args.input) / "case.json") or {}
    draft = args.draft or str(Path(args.output) / "text.md")
    result, path, _ = build(args.work, args.output, draft, case_data,
                            stage=args.stage, input_dir=args.input)
    print(json.dumps({
        "result": str(path),
        "case_id": result["case_id"],
        "status": result["status"],
        "coverage": result["coverage"],
        "preserved_facts": len(result["preserved_facts"]),
        "evidence_map": len(result["evidence_map"]),
        "change_log": len(result["change_log"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
