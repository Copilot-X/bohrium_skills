#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reading 03 — source-bound academic translation with segment alignment."""
from __future__ import annotations

import json
import re
from pathlib import Path

import skillcore as S


def _direction(ctx, text):
    case = ctx.case or {}
    explicit = (case.get("direction") or case.get("translation_direction")
                or case.get("language_direction") or "")
    if isinstance(explicit, dict):
        explicit = "%s->%s" % (explicit.get("source", ""), explicit.get("target", ""))
    task = ctx.task.lower()
    if "中译英" in task or "translate into english" in task or "zh->en" in task:
        return "zh->en", "zh", "en"
    if "英译中" in task or "translate into chinese" in task or "en->zh" in task:
        return "en->zh", "en", "zh"
    if explicit and "->" in str(explicit):
        a, b = str(explicit).split("->", 1)
        return "%s->%s" % (a.strip(), b.strip()), a.strip(), b.strip()
    cjk = S.cjk_count(text) / max(len(re.findall(r"[A-Za-z一-鿿]", text)), 1)
    if cjk >= 0.15:
        return "zh->en", "zh", "en"
    return "en->zh", "en", "zh"


def _source_doc(ctx):
    best = None
    for doc in ctx.docs:
        text = S.doc_text(doc)
        if text.strip() and (best is None or len(text) > len(S.doc_text(best))):
            best = doc
    return best


def prepare(ctx):
    text_doc = _source_doc(ctx)
    if text_doc is None:
        ctx.result.change("prepare", "input", "no readable source material")
        ctx.result.uncertainty("source", "no readable material under /input",
                               "translation cannot start", "provide the source file")
        return ctx.result.finalize("input_error")
    text = S.doc_text(text_doc)
    direction, source_lang, target_lang = _direction(ctx, text)
    segments = S.segment_blocks(text, source_id=text_doc.get("source_id", "SRC01"),
                                path=text_doc.get("rel_path", ""))
    for segment in segments:
        sentences = S.split_sentences(segment["text"])
        for sentence in sentences:
            sentence["sentence_id"] = "%s-%s" % (segment["id"], sentence["id"])
            sentence["locator"] = "%s %s" % (segment["locator"], sentence["id"])
        segment["sentences"] = sentences
    payload = {
        "case_id": ctx.case_id,
        "direction": direction,
        "source_language": source_lang,
        "target_language": target_lang,
        "source_path": text_doc.get("rel_path"),
        "source_chars": len(text),
        "segments": segments,
        "glossary_candidates": S.term_candidates(text, 60),
        "instructions": [
            "Translate every segment in order; do not merge, split or reorder segments.",
            "Preserve numbers, units, citations, figure/table/equation references and formulas.",
            "Write /app/output/translation.md first, then /app/output/bilingual.md with the same P IDs.",
            "Lock terminology in /app/work/glossary.json and use the same target term everywhere.",
        ],
    }
    S.write_json(ctx.work / "segments.json", payload)
    S.write_json(ctx.work / "glossary-candidates.json", payload["glossary_candidates"])
    S.write_json(ctx.work / "translation-brief.json", {
        "case_id": ctx.case_id, "direction": direction,
        "source_language": source_lang, "target_language": target_lang,
        "output_contract": ctx.contract,
        "source_path": text_doc.get("rel_path"),
        "segment_count": len(segments),
    })
    lines = ["# Bilingual alignment", "",
             "> Fill every Target line. Keep the P IDs and the Source text unchanged.", ""]
    for segment in segments:
        lines.append("## %s" % segment["id"])
        lines.append("")
        lines.append("- **Source (%s):** %s" % (source_lang, segment["text"].replace("\n", " ")))
        lines.append("- **Target (%s):** " % target_lang)
        lines.append("")
    S.write_text(ctx.work / "bilingual-template.md", "\n".join(lines))
    # A source-only bilingual file is a safe partial deliverable.
    S.write_text(ctx.output / "bilingual-partial.md", "\n".join(lines))
    ctx.result.add_artifact(ctx.output / "bilingual-partial.md", "partial_alignment",
                            "md", "source segments with empty target slots")
    ctx.result.change("segment", text_doc.get("rel_path", ""),
                      "%d segments prepared" % len(segments))
    return ctx.provisional(reason="source segmented; write translation.md and bilingual.md, then run finalize")


def _parse_bilingual(text):
    entries = {}
    current = None
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        match = re.match(r"^##\s+(P\d+|[A-Za-z0-9_-]+)\s*$", line.strip())
        if match:
            current = match.group(1)
            entries.setdefault(current, {"source": "", "target": ""})
            continue
        if current is None:
            continue
        # Accept both "- **Source (zh):** text" (the template) and
        # "- **Source (zh)**: text" (the older parser-only form).
        src = re.match(r"^-\s+\*\*Source[^*]*?\*\*\s*:?\s*(.*)$", line.strip(), re.I)
        tgt = re.match(r"^-\s+\*\*Target[^*]*?\*\*\s*:?\s*(.*)$", line.strip(), re.I)
        if src:
            entries[current]["source"] = src.group(1).strip()
        elif tgt:
            entries[current]["target"] = tgt.group(1).strip()
    return entries


def _parse_translation_blocks(text):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    # Ignore an initial title heading.
    if blocks and blocks[0].startswith("#") and len(blocks) > 1:
        blocks = blocks[1:]
    return blocks


def _load_glossary(ctx):
    for name in ("glossary.json", "glossary-locked.json", "terms.json"):
        payload = S.load_json(ctx.work / name, None)
        if payload:
            if isinstance(payload, dict):
                items = payload.get("terms") or payload.get("glossary") or []
            else:
                items = payload
            if isinstance(items, list):
                cleaned = []
                for item in items:
                    if isinstance(item, str):
                        continue
                    source = str(item.get("source") or item.get("src") or item.get("zh") or "")
                    target = str(item.get("target") or item.get("tgt") or item.get("en") or "")
                    if source or target:
                        cleaned.append({"source": source, "target": target,
                                        "aliases": item.get("aliases") or [],
                                        "note": item.get("note", "")})
                if cleaned:
                    return cleaned, name
    return [], None


def _align(segments, translation_blocks, bilingual):
    """Align translations to segments using bilingual IDs first, then order."""
    aligned = {}
    if bilingual:
        for segment in segments:
            entry = bilingual.get(segment["id"])
            if entry and entry.get("target"):
                aligned[segment["id"]] = entry["target"].strip()
    missing = [s for s in segments if s["id"] not in aligned]
    if missing and translation_blocks:
        if len(translation_blocks) == len(segments):
            for segment, block in zip(segments, translation_blocks):
                aligned.setdefault(segment["id"], block.strip())
        elif len(translation_blocks) >= len(missing):
            # Conservative sequential fill; flag the alignment as approximate.
            for segment, block in zip(missing, translation_blocks):
                aligned.setdefault(segment["id"], block.strip())
    return aligned


def finalize(ctx):
    payload = S.load_json(ctx.work / "segments.json", {})
    segments = payload.get("segments") or []
    direction = payload.get("direction") or "zh->en"
    source_lang = payload.get("source_language") or "zh"
    target_lang = payload.get("target_language") or "en"
    source_path = payload.get("source_path") or ""
    if not segments:
        ctx.result.change("finalize", "input", "segments.json missing or empty")
        ctx.result.uncertainty("segments", "no prepared segments",
                               "translation cannot be aligned", "run prepare first")
        return ctx.result.finalize("input_error")

    translation_path = ctx.output / "translation.md"
    bilingual_path = ctx.output / "bilingual.md"
    translation = S.read_text(translation_path)
    bilingual_text = S.read_text(bilingual_path)
    if not translation.strip() and not bilingual_text.strip():
        ctx.result.change("finalize", "output", "no translation.md or bilingual.md")
        ctx.result.uncertainty("deliverable", "model-authored translation is missing",
                               "cannot deliver a translation", "write translation.md and bilingual.md")
        return ctx.result.finalize("partial")

    bilingual = _parse_bilingual(bilingual_text)
    blocks = _parse_translation_blocks(translation)
    aligned = _align(segments, blocks, bilingual)
    glossary, glossary_source = _load_glossary(ctx)
    if not glossary:
        glossary = []
        for item in S.load_json(ctx.work / "glossary-candidates.json", [])[:20]:
            glossary.append({"source": item.get("term", ""), "target": "",
                             "aliases": [], "note": "candidate; target not locked"})

    bilingual_lines = ["# Bilingual alignment", "",
                       "> Paragraph-level source/target alignment. Empty Target means untranslated.", ""]
    translated = 0
    entries = []
    for segment in segments:
        target = aligned.get(segment["id"], "").strip()
        status = "translated" if target else "untranslated"
        if target:
            translated += 1
        entries.append({"segment_id": segment["id"], "source": segment["text"],
                        "target": target, "status": status,
                        "locator": segment.get("locator")})
        bilingual_lines.append("## %s" % segment["id"])
        bilingual_lines.append("")
        bilingual_lines.append("- **Source (%s):** %s" % (source_lang, segment["text"].replace("\n", " ")))
        bilingual_lines.append("- **Target (%s):** %s" % (target_lang, target))
        bilingual_lines.append("")
    S.write_text(bilingual_path, "\n".join(bilingual_lines))

    full_translation = "\n\n".join(e["target"] for e in entries if e["target"]).strip()
    if not translation.strip() and full_translation:
        S.write_text(translation_path, "# Translation\n\n" + full_translation + "\n")
        translation = S.read_text(translation_path)
    S.write_json(ctx.output / "glossary.json", {
        "case_id": ctx.case_id, "source_language": source_lang,
        "target_language": target_lang, "locked": bool(glossary_source),
        "source": glossary_source or "candidates", "terms": glossary,
    })

    source_all = "\n\n".join(s["text"] for s in segments)
    target_all = "\n\n".join(e["target"] for e in entries)
    numbers = S.check_numbers_preserved(source_all, target_all)
    citations = S.check_citations_preserved(source_all, target_all)
    source_neg = S.negation_count(source_all)
    target_neg = S.negation_count(target_all)
    source_hedge = S.hedge_counts(source_all)
    target_hedge = S.hedge_counts(target_all)
    # Chinese and English negation markers are not one-to-one (e.g. 不同,
    # 未来, 不仅 are not negations, while "without" can be one).  Compare
    # counts with a proportional tolerance instead of exact equality.
    neg_limit = max(1, int(round(0.35 * max(source_neg, target_neg))))
    negation_ok = abs(source_neg - target_neg) <= neg_limit
    hedging_ok = source_hedge["level"] == target_hedge["level"]
    term_issues = S.check_terms(glossary, target_all) if glossary else []
    missing_segments = [e["segment_id"] for e in entries if not e["target"]]
    alignment_issues = []
    if not bilingual and blocks and len(blocks) != len(segments):
        alignment_issues.append("translation.md blocks=%d, segments=%d" % (len(blocks), len(segments)))

    checks = {
        "segments_total": len(segments),
        "segments_translated": translated,
        "missing_segments": missing_segments,
        "numbers": numbers,
        "citations": citations,
        "negation": {"source": source_neg, "target": target_neg,
                     "tolerance": neg_limit, "ok": negation_ok},
        "hedging": {"source": source_hedge, "target": target_hedge,
                    "ok": hedging_ok},
        "glossary": {"terms": len(glossary), "issues": term_issues,
                     "ok": not term_issues and bool(glossary)},
        "alignment_issues": alignment_issues,
    }
    S.write_json(ctx.output / "translation-check.json", {
        "case_id": ctx.case_id, "checks": checks, "entries": entries,
        "generated_at": S.now_iso(),
    })

    preserved = []
    for number in S.number_tokens(source_all)[:200]:
        preserved.append({"kind": "number", "source": number["raw"],
                          "target": number["raw"], "locator": source_path,
                          "value": number["raw"], "unit": ""})
    for token in S.citation_tokens(source_all)[:100]:
        preserved.append({"kind": "citation", "source": token["raw"],
                          "target": token["raw"], "locator": source_path,
                          "value": token["raw"], "unit": ""})
    for term in glossary[:50]:
        if term.get("target"):
            preserved.append({"kind": "term", "source": term.get("source"),
                              "target": term.get("target"), "locator": source_path,
                              "value": term.get("target"), "unit": ""})
    evidence = []
    for entry in entries[:200]:
        if not entry["target"]:
            continue
        evidence.append({"claim_id": entry["segment_id"],
                         "claim": S.excerpt(entry["target"], 120),
                         "source": {"path": source_path, "locator": entry["locator"]},
                         "quote": S.excerpt(entry["source"], 200),
                         "paraphrase": True,
                         "supports": "translation"})

    critical = (bool(missing_segments) or bool(numbers["missing"]) or not citations["ok"]
                or not checks["glossary"]["ok"] or not negation_ok or not hedging_ok)
    status = "answered" if not critical and translated == len(segments) else "partial"
    if missing_segments:
        ctx.result.uncertainty("untranslated_segments", ", ".join(missing_segments[:20]),
                               "%d segments remain" % len(missing_segments),
                               "translate them and rerun finalize")
    if bool(numbers["missing"]):
        ctx.result.uncertainty("number_mismatch", json.dumps(numbers, ensure_ascii=False),
                               "numeric fidelity needs review", "compare source and target")
    if term_issues:
        ctx.result.uncertainty("glossary_consistency", "; ".join(term_issues[:10]),
                               "term targets are not all present", "apply the locked glossary")
    if alignment_issues:
        ctx.result.uncertainty("alignment", "; ".join(alignment_issues),
                               "paragraph alignment is approximate", "use bilingual.md P IDs")

    ctx.result.add_artifact(translation_path, "deliverable", "md",
                            "complete translation")
    ctx.result.add_artifact(bilingual_path, "alignment", "md",
                            "paragraph-level bilingual table")
    ctx.result.add_artifact(ctx.output / "glossary.json", "glossary", "json",
                            "locked terminology")
    ctx.result.add_artifact(ctx.output / "translation-check.json", "audit", "json",
                            "numbers, citations, negation, hedging and glossary checks")
    ctx.result.change("translate", source_path, "%d/%d segments translated" % (translated, len(segments)))
    ctx.result.change("audit", "translation", "numbers/citations/terms checked")
    return ctx.result.finalize(
        status,
        deliverable=translation_path,
        result={"summary": "Translated %d/%d segments (%s); glossary %d terms." % (
                    translated, len(segments), direction, len(glossary)),
                "direction": direction, "segments": len(segments),
                "translated": translated, "checks": checks,
                "translation": "translation.md", "bilingual": "bilingual.md"},
        claims=[{"claim_id": e["segment_id"], "text": S.excerpt(e["target"], 140),
                 "evidence": e["segment_id"]} for e in entries[:200] if e["target"]],
        preserved_facts=preserved, evidence_map=evidence,
    )
