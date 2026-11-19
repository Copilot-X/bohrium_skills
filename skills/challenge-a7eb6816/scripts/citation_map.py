#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Map draft claims back to locatable source evidence.

Usage:
    python3 citation_map.py --work /app/work --draft /app/output/article.md [--json]

Writes ``<work>/citation-map.json``.  The map is deliberately conservative:
a claim is only marked ``mapped`` when it has a real path+locator in the
evidence ledger and its numbers/keywords actually occur in the draft.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # package mode (python -m scripts.citation_map)
    from . import transfer_kit as kit
    from . import evidence as evidence_mod
except ImportError:  # direct mode (python scripts/citation_map.py)
    kit = __import__("transfer_kit")
    evidence_mod = __import__("evidence")


def split_draft(draft_text):
    """Return sections with paragraph locators such as ``§2 ¶3``."""
    sections = [{"heading": "（开头）", "level": 0, "paragraphs": []}]
    for block in kit.markdown_blocks(draft_text):
        if block["kind"] == "heading":
            sections.append({"heading": block["text"], "level": block["level"],
                             "paragraphs": []})
        elif block["kind"] in ("paragraph", "list", "quote", "table_row"):
            sections[-1]["paragraphs"].append(block["text"])
    out = []
    for si, section in enumerate(sections, 1):
        if not section["paragraphs"] and si > 1:
            continue
        paras = []
        for pi, text in enumerate(section["paragraphs"], 1):
            paras.append({"locator": "§%d ¶%d" % (si, pi), "text": text})
        out.append({"section": section["heading"], "level": section["level"],
                    "locator": "§%d" % si, "paragraphs": paras,
                    "text": "\n".join(p["text"] for p in paras)})
    return out


def _claim_numbers(claim):
    keys = set()
    for q in claim.get("quantities") or []:
        keys.add(kit.number_key(q["value"], q.get("unit") or ""))
    return keys


def _paragraph_numbers(text):
    return {kit.number_key(q["value"], q.get("unit") or "")
            for q in evidence_mod.extract_quantities(text)}


def _match_score(claim, paragraph, claim_numbers, para_numbers):
    shared_numbers = claim_numbers & para_numbers
    score = 5 * len(shared_numbers)
    claim_kws = set(kit.keywords(claim.get("statement", ""), limit=30))
    para_kws = set(kit.keywords(paragraph, limit=60))
    shared_kws = claim_kws & para_kws
    score += min(6, len(shared_kws))
    return score, sorted(shared_numbers), sorted(shared_kws)


def build(workorder, evidence, draft_text):
    sections = split_draft(draft_text or "")
    paragraphs = [(s, p) for s in sections for p in s["paragraphs"]]
    claims = evidence.get("claims") or []
    claim_map = []
    evidence_map = []
    unmapped = []
    matched_claim_ids = set()

    for claim in claims:
        claim_numbers = _claim_numbers(claim)
        matches = []
        for section, para in paragraphs:
            score, shared_numbers, shared_kws = _match_score(
                claim, para["text"], claim_numbers, _paragraph_numbers(para["text"]))
            if shared_numbers or score >= 3:
                matches.append({
                    "locator": para["locator"],
                    "section": section["section"],
                    "score": score,
                    "shared_numbers": shared_numbers,
                    "shared_keywords": shared_kws[:6],
                })
        matches.sort(key=lambda m: -m["score"])
        source = claim.get("source") or {}
        source_ok = bool(source.get("path") and source.get("locator"))
        status = ("mapped" if (matches and source_ok)
                  else "not_in_draft" if not matches
                  else "source_missing")
        record = {
            "claim_id": claim.get("claim_id"),
            "claim": claim.get("statement"),
            "kind": claim.get("kind"),
            "tier": claim.get("tier"),
            "source": source,
            "quote": claim.get("quote"),
            "status": status,
            "draft_locators": [m["locator"] for m in matches[:3]],
            "match_score": matches[0]["score"] if matches else 0,
            "evidence_id": claim.get("evidence_id"),
            "conditions": claim.get("conditions") or [],
            "speculation": claim.get("tier") == "speculation",
        }
        claim_map.append(record)
        if status == "mapped":
            matched_claim_ids.add(claim.get("claim_id"))
            evidence_map.append({
                "claim_id": claim.get("claim_id"),
                "claim": claim.get("statement"),
                "source": source,
                "quote": claim.get("quote"),
                "draft_locator": matches[0]["locator"],
                "section": matches[0]["section"],
                "evidence_id": claim.get("evidence_id"),
                "tier": claim.get("tier"),
                "status": "mapped",
            })
        elif status == "not_in_draft":
            unmapped.append({
                "claim_id": claim.get("claim_id"),
                "claim": claim.get("statement"),
                "reason": "key claim from material does not appear in the draft",
                "source": source,
            })

    # Paragraphs with numbers but no supporting evidence are the main
    # fabrication risk.  Report them instead of silently trusting the draft.
    unsourced = []
    all_evidence_numbers = set()
    for entry in evidence.get("evidence") or []:
        for q in entry.get("quantities") or []:
            all_evidence_numbers.add(kit.number_key(q["value"], q.get("unit") or ""))
    for section, para in paragraphs:
        nums = _paragraph_numbers(para["text"])
        if not nums:
            continue
        unsupported = sorted(n for n in nums if n not in all_evidence_numbers)
        if unsupported:
            unsourced.append({
                "draft_locator": para["locator"],
                "section": section["section"],
                "excerpt": kit.excerpt(para["text"]),
                "unsupported_numbers": unsupported,
            })

    result = {
        "status": "ok" if evidence_map else "no_mapped_claims",
        "case_id": (workorder or {}).get("case_id"),
        "draft": {
            "sections": len(sections),
            "paragraphs": len(paragraphs),
            "chars": len(draft_text or ""),
        },
        "claim_map": claim_map,
        "evidence_map": evidence_map,
        "unmapped_key_claims": unmapped,
        "draft_claims_without_source": unsourced,
        "stats": {
            "claims": len(claims),
            "mapped": len(evidence_map),
            "not_in_draft": len(unmapped),
            "unsourced_paragraphs": len(unsourced),
        },
        "generated_at": kit.now_iso(),
    }
    return result


def write(work_dir, result):
    kit.write_json(Path(work_dir) / "citation-map.json", result)
    return Path(work_dir) / "citation-map.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--draft", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    work = Path(args.work)
    workorder = kit.read_json(work / "workorder.json", default={}) or {}
    evidence = kit.read_json(work / "evidence.json")
    if evidence is None:
        print("evidence.json missing; run evidence.py first", file=sys.stderr)
        return 2
    draft_path = Path(args.draft) if args.draft else None
    if draft_path is None:
        name = workorder.get("deliverable_name", "article.md")
        draft_path = Path("/app/output") / name
    draft_text = kit.read_text(draft_path) if draft_path.is_file() else ""
    result = build(workorder, evidence, draft_text)
    path = write(work, result)
    print(json.dumps({"status": result["status"], "stats": result["stats"],
                      "citation_map": str(path),
                      "draft": str(draft_path)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
