#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Split draft sentences into atomic claims and bind them to source units.

The matcher is deliberately conservative and explainable:

* numbers in the claim must occur in a verbatim quote;
* a citation marker ``[n]`` restricts the preferred source to number ``n``;
* otherwise Latin words, CJK bigrams and a small bilingual concept lexicon
  provide the overlap signal.

It never paraphrases a quote and never invents a source.  A sentence that
cannot be bound is deleted when it carries numbers, or retained only as
``marked_as_reasoning`` with no claims, so the ledger never claims support
that does not exist.

Usage:
    python3 claims.py --work /app/work --output /app/output
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

ASSOC_RE = re.compile(r"相关|关联|伴随|associated|correlation|correlat", re.I)
COMPARE_RE = re.compile(r"高于|低于|优于|相比|比较|高于|higher|lower|than|versus|"
                        r"compared", re.I)
CAUSAL_RE = re.compile(r"导致|引起|造成|致使|\bcauses?\b|\bleads? to\b|"
                       r"\bresult(?:s|ed)? in\b", re.I)
METHOD_RE = re.compile(r"采用|方法|设计|队列|纳入|反应器|装置|"
                       r"\bmethod\b|\bdesign\b|\bcohort\b|\breactor\b", re.I)
CONTRAST_RE = re.compile(r"；|;|然而|但是|相比之下|相反|"
                         r"\bhowever\b|\bwhereas\b|\bin contrast\b", re.I)


def _unit_records(sources_data):
    records = []
    for source in sources_data.get("sources") or []:
        if not source.get("accessible"):
            continue
        for unit in source.get("units") or []:
            text = evidence_kit.normalize_space(unit.get("text"))
            if not text:
                continue
            records.append({
                "source_id": source["source_id"],
                "citation_number": source.get("citation_number"),
                "path": source.get("path"),
                "scope": source.get("scope"),
                "locator": unit.get("locator") or "material",
                "text": text,
                "numbers": evidence_kit.numbers_in(text),
                "tokens": evidence_kit.content_terms(text),
            })
    return records


def _split_claim_parts(sentence):
    """Conservative atomic split: semicolons and explicit contrasts only."""
    stripped = re.sub(r"\[[\d,\-–\s]+\]", "", sentence).strip()
    parts = [part.strip() for part in CONTRAST_RE.split(stripped) if part.strip()]
    if len(parts) <= 1:
        return [stripped]
    if all(len(evidence_kit.compact(part)) >= 10 for part in parts):
        return parts
    return [stripped]


def _quote_from_unit(unit, part_numbers, part_tokens):
    sentences = evidence_kit.split_sentences(unit["text"]) or [unit["text"]]
    best = None
    best_score = -1
    best_index = 0
    for index, sentence in enumerate(sentences, 1):
        numbers = evidence_kit.numbers_in(sentence)
        score = 3 * len(part_numbers & numbers) + len(
            part_tokens & evidence_kit.content_terms(sentence))
        if score > best_score:
            best_score = score
            best = sentence
            best_index = index
    quote = evidence_kit.normalize_space(best or unit["text"])
    locator = unit["locator"]
    if len(sentences) > 1:
        locator = "%s, sentence %d" % (locator, best_index)
    return quote, locator


def _candidate_score(part_numbers, part_tokens, unit, cited):
    overlap = part_tokens & unit["tokens"]
    num_hits = part_numbers & unit["numbers"]
    score = 3 * len(num_hits) + len(overlap) + (3 if cited else 0)
    if part_numbers and not num_hits:
        score -= 3
    if not part_numbers and not overlap:
        score -= 2
    return score, overlap, num_hits


def _acceptable(part_numbers, overlap, num_hits):
    if part_numbers:
        return bool(num_hits)
    return len(overlap) >= 2


def _match_part(part, citations, units, source_by_id):
    part_numbers = evidence_kit.numbers_in(part)
    part_tokens = evidence_kit.content_terms(part)
    candidates = []
    for unit in units:
        source = source_by_id.get(unit["source_id"]) or {}
        cited = source.get("citation_number") in citations
        score, overlap, num_hits = _candidate_score(
            part_numbers, part_tokens, unit, cited)
        candidates.append({
            "unit": unit, "source": source, "cited": cited, "score": score,
            "overlap": overlap, "num_hits": num_hits,
        })
    candidates.sort(key=lambda item: item["score"], reverse=True)

    cited_candidates = [item for item in candidates if item["cited"]]
    pool = cited_candidates or candidates
    if citations and not cited_candidates:
        pool = candidates
    chosen = []
    covered = set()
    citation_bound = True
    for candidate in pool:
        unit = candidate["unit"]
        source = candidate["source"]
        quote_preview, _ = _quote_from_unit(unit, part_numbers, part_tokens)
        quote_numbers = evidence_kit.numbers_in(quote_preview)
        novel = (candidate["num_hits"] | quote_numbers) - covered
        if not chosen:
            if not _acceptable(part_numbers, candidate["overlap"],
                               candidate["num_hits"]):
                continue
        else:
            if not novel or candidate["score"] <= 0:
                continue
        if citations and source.get("citation_number") not in citations:
            citation_bound = False
            continue
        chosen.append(candidate)
        covered.update(quote_numbers & part_numbers)
        if not part_numbers or covered >= part_numbers:
            break
    if not chosen and citations:
        # Cited source could not support the clause; keep an explicitly
        # partial binding to the best non-cited unit instead of dropping it.
        for candidate in candidates:
            if _acceptable(part_numbers, candidate["overlap"],
                           candidate["num_hits"]):
                chosen = [candidate]
                citation_bound = False
                preview, _ = _quote_from_unit(candidate["unit"],
                                              part_numbers, part_tokens)
                covered = evidence_kit.numbers_in(preview) & part_numbers
                break

    overall_complete = (not part_numbers) or part_numbers <= covered
    evidence = []
    seen = set()
    direct_count = 0
    for index, candidate in enumerate(chosen):
        unit = candidate["unit"]
        quote, locator = _quote_from_unit(unit, part_numbers, part_tokens)
        key = (unit["source_id"], quote)
        if key in seen:
            continue
        seen.add(key)
        quote_numbers = evidence_kit.numbers_in(quote)
        # A claim may need two quotes (e.g. one value per catalyst).  When the
        # union covers every number, each quote is a direct observation of part
        # of the same claim; otherwise the binding is only partial.
        contributes = bool(quote_numbers & part_numbers) or not part_numbers
        supports = "direct" if (overall_complete and contributes
                                and len(candidate["overlap"]) >= 1) else "partial"
        if supports == "direct":
            direct_count += 1
        evidence.append({
            "source_id": unit["source_id"],
            "locator": locator,
            "quote": quote,
            "supports": supports,
            "citation_number": candidate["source"].get("citation_number"),
            "scope": unit.get("scope"),
        })
    numbers_complete = (not part_numbers) or part_numbers <= covered
    status = "supported"
    if not evidence:
        status = "unsupported"
    elif not numbers_complete:
        status = "partial"
    elif citations and not citation_bound:
        status = "partial"
    elif part_numbers and direct_count == 0:
        status = "partial"
    elif not part_numbers and not any(
            item["supports"] == "direct" for item in evidence):
        status = "partial"
    return {
        "part": part,
        "evidence": evidence,
        "status": status,
        "citation_bound": citation_bound,
        "numbers_complete": numbers_complete,
        "numbers": sorted(part_numbers),
        "score": chosen[0]["score"] if chosen else 0,
    }


def _claim_type(text, negative_causal):
    if ASSOC_RE.search(text):
        return "association"
    if negative_causal:
        return "qualitative"
    if CAUSAL_RE.search(text):
        return "causal"
    if COMPARE_RE.search(text) and evidence_kit.numbers_in(text):
        return "comparative"
    if evidence_kit.numbers_in(text):
        return "quantitative"
    if METHOD_RE.search(text):
        return "methodological"
    return "qualitative"


def _strength(text, quote_text):
    if evidence_kit.hedge_in(text) and not evidence_kit.limitation_in(quote_text):
        return "inferred"
    return "reported"


def _limits(claim_type, strength, evidence, sources_by_id):
    notes = []
    if strength == "inferred":
        notes.append("推断性表述，不能作为因果或普适结论")
    if claim_type == "association":
        notes.append("相关性不能推出因果")
    scopes = {(sources_by_id.get(item["source_id"]) or {}).get("scope")
              for item in evidence}
    if scopes and scopes <= {"abstract", "metadata"}:
        notes.append("基于摘要，未核对全文")
    return "；".join(notes)


def _expected_support(claim_objects):
    if not claim_objects:
        return "none"
    if any(claim.get("status") == "unsupported" for claim in claim_objects):
        return "none"
    if any(claim.get("status") in {"partial", "conflicting"}
           for claim in claim_objects):
        return "partial"
    if any(claim.get("strength") == "inferred" for claim in claim_objects):
        return "partial"
    return "direct"


def _make_claim(part, match, source_by_id):
    negative_causal = bool(re.search(
        r"(不能|无法|未|难以|cannot|does not|did not).{0,12}"
        r"(因果|causal|causality|导致|cause)", part, re.I))
    claim_type = _claim_type(part, negative_causal)
    quote_blob = " ".join(item["quote"] for item in match["evidence"])
    strength = _strength(part, quote_blob) if match["evidence"] else "reported"
    value, unit, raw = evidence_kit.first_value_unit(part)
    condition = evidence_kit.extract_conditions(part) or evidence_kit.extract_conditions(
        quote_blob) or "not stated"
    citation_numbers = sorted({
        item["citation_number"] for item in match["evidence"]
        if isinstance(item.get("citation_number"), int)
    })
    claim = {
        "text": evidence_kit.normalize_space(part),
        "type": claim_type,
        "strength": strength,
        "status": match["status"],
        "evidence": [
            {key: item[key] for key in
             ("source_id", "locator", "quote", "supports")}
            for item in match["evidence"]
        ],
        "citation_numbers": citation_numbers,
        "match": {
            "method": "citation+number+keyword+concept",
            "score": match["score"],
            "citation_bound": match["citation_bound"],
            "numbers_complete": match["numbers_complete"],
        },
    }
    if claim_type in {"quantitative", "comparative"} and value is not None:
        claim["value"] = value
        claim["unit"] = unit
    claim["condition"] = condition
    limits = _limits(claim_type, strength, match["evidence"], source_by_id)
    if limits:
        claim["limits"] = limits
    return claim


def build(text, sources_data, case_data=None, prune_unsupported=True):
    """Return ``(ledger, final_text, actions)`` for a draft."""
    source_by_id = {item.get("source_id"): item
                    for item in sources_data.get("sources") or []}
    units = _unit_records(sources_data)
    sentences = evidence_kit.body_sentences_with_context(text)
    claims = []
    records = []
    actions = {"deleted": [], "marked_as_reasoning": []}
    uncertainty = []
    claim_counter = 0

    for index, sentence in enumerate(sentences, 1):
        sentence_id = "S%02d" % index
        sentence_text = sentence["text"]
        citations = evidence_kit.citation_numbers(sentence_text)
        parts = _split_claim_parts(sentence_text)
        matches = [_match_part(part, citations, units, source_by_id)
                   for part in parts]
        if len(matches) > 1 and any(
                item["status"] == "unsupported" for item in matches):
            merged = _match_part(re.sub(r"\[[\d,\-–\s]+\]", "",
                                        sentence_text), citations, units,
                                 source_by_id)
            parts = [re.sub(r"\[[\d,\-–\s]+\]", "", sentence_text).strip()]
            matches = [merged]

        claim_objects = []
        for part, match in zip(parts, matches):
            claim_counter += 1
            claim = _make_claim(part, match, source_by_id)
            claim["claim_id"] = "C%02d" % claim_counter
            claim["sentence_id"] = sentence_id
            claim["draft_locator"] = sentence.get("section") or ""
            claims.append(claim)
            claim_objects.append(claim)
            if not match["citation_bound"]:
                uncertainty.append({
                    "item": "citation_not_bound:%s" % claim["claim_id"],
                    "reason": "evidence comes from a source not cited by the sentence",
                    "impact": "the citation marker may point at the wrong source",
                    "next_check": "move [n] next to the supported clause or add the source",
                })

        support = _expected_support(claim_objects)
        record = {
            "sentence_id": sentence_id,
            "text": sentence_text,
            "claims": [claim["claim_id"] for claim in claim_objects],
            "support": support,
            "section": sentence.get("section") or "",
            "line": sentence.get("line"),
        }
        number_tokens = evidence_kit.numbers_in(sentence_text)
        if support == "none":
            action = "deleted" if (prune_unsupported and number_tokens) \
                else "marked_as_reasoning"
            record["unsupported_action"] = action
            if action == "deleted":
                actions["deleted"].append({
                    "sentence_id": sentence_id,
                    "text": sentence_text,
                    "numbers": sorted(number_tokens),
                    "reason": "no source could support this sentence; numeric "
                              "content is removed rather than asserted",
                })
            else:
                # Retained reasoning must not smuggle in claims.
                record["claims"] = []
                record["reasoning_marker"] = True
                record["note"] = ("retained as author reasoning; not a "
                                  "source-backed factual claim")
                actions["marked_as_reasoning"].append({
                    "sentence_id": sentence_id,
                    "text": sentence_text,
                    "reason": "no source match; kept only as marked reasoning",
                })
                if number_tokens:
                    uncertainty.append({
                        "item": "unbound_numbers_retained:%s" % sentence_id,
                        "reason": "sentence has numbers but no source evidence",
                        "impact": "audit will flag unbound numbers; delete or source them",
                        "next_check": "rewrite with a sourced value or remove",
                    })
        records.append(record)

    final_text = text
    if actions["deleted"]:
        final_text = evidence_kit.remove_sentences(
            text, [item["text"] for item in actions["deleted"]])
        for item in actions["deleted"]:
            item["removed"] = item["text"] not in final_text

    number_index = []
    traced = 0
    by_id = {claim["claim_id"]: claim for claim in claims}
    for record in records:
        for token in sorted(evidence_kit.numbers_in(record["text"])):
            found_claim = None
            found_evidence = None
            for claim_id in record["claims"]:
                claim = by_id.get(claim_id) or {}
                candidate = evidence_kit.numbers_in(claim.get("text") or "")
                if token in candidate:
                    found_claim = claim
                    break
                for evidence in claim.get("evidence") or []:
                    if token in evidence_kit.numbers_in(evidence.get("quote") or ""):
                        found_claim = claim
                        found_evidence = evidence
                        break
                if found_claim:
                    break
            if found_claim and not found_evidence:
                for evidence in found_claim.get("evidence") or []:
                    if token in evidence_kit.numbers_in(evidence.get("quote") or ""):
                        found_evidence = evidence
                        break
            if found_claim and found_evidence:
                traced += 1
            number_index.append({
                "token": token,
                "sentence_id": record["sentence_id"],
                "claim_id": (found_claim or {}).get("claim_id"),
                "source_id": (found_evidence or {}).get("source_id"),
                "locator": (found_evidence or {}).get("locator"),
                "quote": (found_evidence or {}).get("quote"),
                "found": bool(found_claim and found_evidence),
                "action": record.get("unsupported_action") or "asserted",
            })

    unsupported = [
        {"sentence_id": record["sentence_id"], "sentence": record["text"],
         "action": record.get("unsupported_action"),
         "claims": record.get("claims") if record.get("unsupported_action")
         == "deleted" else [],
         "reason": "no direct source support"}
        for record in records if record["support"] == "none"
    ]
    if any(item["status"] == "partial" for item in claims):
        uncertainty.append({
            "item": "partial_evidence_bindings",
            "reason": "some claims cover only part of the sentence or rely on a "
                      "non-cited source",
            "impact": "strength and citation placement need a human review",
            "next_check": "verify the quote contains every number and condition",
        })
    uncertainty.append({
        "item": "heuristic_binding",
        "reason": "claim-evidence links use citation, number and bilingual "
                  "keyword/concept overlap; they are auditable but not semantic proof",
        "impact": "a verbatim quote can still fail to entail a claim",
        "next_check": "read each quoted sentence against its claim; change or "
                      "delete unsupported wording",
    })

    coverage = {
        "sentences": len(records),
        "fully_supported": sum(1 for item in records if item["support"] == "direct"),
        "partially_supported": sum(1 for item in records
                                   if item["support"] == "partial"),
        "unsupported": sum(1 for item in records if item["support"] == "none"),
    }
    change_log = [{
        "step": 1,
        "action": "claims_built",
        "reason": "split draft sentences into atomic claims and bind verbatim quotes",
        "evidence": "claims=%d; sentences=%d; numbers=%d/%d traced"
                    % (len(claims), len(records), traced, len(number_index)),
    }]
    if actions["deleted"]:
        change_log.append({
            "step": 2,
            "action": "deleted_unsupported_numeric_sentences",
            "reason": "no source could support numeric content",
            "evidence": ", ".join(item["sentence_id"]
                                  for item in actions["deleted"]),
        })
    if actions["marked_as_reasoning"]:
        change_log.append({
            "step": 3,
            "action": "marked_as_reasoning",
            "reason": "kept non-numeric unsupported sentences only as author reasoning",
            "evidence": ", ".join(item["sentence_id"]
                                  for item in actions["marked_as_reasoning"]),
        })

    ledger = {
        "schema": "evidence-map/v1",
        "case_id": (case_data or {}).get("case_id") or sources_data.get("case_id"),
        "language": evidence_kit.detect_language(final_text),
        "genre": evidence_kit.guess_genre((case_data or {}).get("task_instruction", ""),
                                    case_data),
        "sources": [
            {key: source.get(key) for key in
             ("source_id", "path", "kind", "scope", "title", "citation_number",
              "citation", "text_path", "accessible", "metadata_complete",
              "reference_entry", "retracted_suspected")}
            for source in sources_data.get("sources") or []
        ],
        "claims": claims,
        "sentences": records,
        "conflicts": [],
        "unsupported": unsupported,
        "coverage": coverage,
        "number_index": number_index,
        "uncertainty": uncertainty + list(sources_data.get("uncertainty") or []),
        "change_log": change_log,
        "stats": {
            "claims": len(claims),
            "evidence_items": sum(len(claim["evidence"]) for claim in claims),
            "sentences": len(records),
            "direct": coverage["fully_supported"],
            "partial": coverage["partially_supported"],
            "none": coverage["unsupported"],
            "numbers_total": len(number_index),
            "numbers_traced": traced,
            "sources_used": len({evidence["source_id"]
                                 for claim in claims
                                 for evidence in claim["evidence"]}),
        },
        "actions": actions,
    }
    return ledger, final_text, actions


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--text", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    work_dir = Path(args.work)
    text_path = Path(args.text) if args.text else Path(args.output) / "text.md"
    sources_data = evidence_kit.read_json(work_dir / "sources.json") or {}
    case_data = evidence_kit.read_json(Path(args.input) / "case.json") or {}
    text = evidence_kit.read_text(text_path)
    ledger, final_text, actions = build(
        text, sources_data, case_data, prune_unsupported=True)
    evidence_kit.write_text(text_path, final_text)
    evidence_kit.write_json(work_dir / "claims.json", ledger)
    print(json.dumps({"stats": ledger["stats"], "actions": actions},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
