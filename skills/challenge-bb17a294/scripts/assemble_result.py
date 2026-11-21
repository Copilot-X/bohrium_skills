#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble result.json from segments.json + bilingual.md (optional accelerator).

The translation itself is written first (translation.md + bilingual.md).  This
script copies targets from the bilingual file into ``result.segments[]`` so the
agent does not have to emit the whole translation a second time, and it fills
the task fields plus the official writing aliases.

It deliberately leaves the *judgement* fields empty/conservative:

- ``preservation`` defaults to all false; set true only after manual checks;
- ``back_translation_check``, ``preserved_facts`` and ``evidence_map`` are [];
- ``translation_check`` records nulls until an optional audit runs.

Missing bilingual sections produce empty targets, ``status=partial`` and exit 1;
the file is still written so the agent can repair and re-run.

Usage:
    python3 assemble_result.py --case-id CASE-1 --direction 'zh->en' \
        --segments /app/work/segments.json \
        --bilingual /app/output/bilingual.md \
        --glossary /app/output/glossary.json \
        --artifact-root /app/output \
        --out /app/output/result.json --status completed

Exit codes: 0 ok, 1 written with alignment gaps, 2 unreadable/invalid input.
Stdlib only.
"""

import argparse
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SECTION_RE = re.compile(r"^#{2,4}\s+(S\d+)\b.*$", re.MULTILINE)
TARGET_RE = re.compile(
    r"^\s*[-*]\s*\*\*\s*Target\s*(?:\([^)]*\))?\s*:?\s*\*\*\s*(.*)$", re.I
)
SOURCE_RE = re.compile(
    r"^\s*[-*]\s*\*\*\s*Source\s*(?:\([^)]*\))?\s*:?\s*\*\*\s*(.*)$", re.I
)


def read_text(path):
    return Path(path).read_text(encoding="utf-8-sig")


def load_json(path):
    return json.loads(read_text(path))


def one_line(text):
    return re.sub(r"\s+", " ", text).strip()


def parse_bilingual(text):
    """Return ({segment_id: target}, duplicate_ids) from ``## Sxx`` sections."""
    matches = list(SECTION_RE.finditer(text))
    targets = {}
    duplicates = []
    for i, match in enumerate(matches):
        sid = match.group(1)
        if sid in targets:
            duplicates.append(sid)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end():end]
        lines = body.splitlines()
        value = []
        collecting = False
        for line in lines:
            target_match = TARGET_RE.match(line)
            if target_match:
                value = [target_match.group(1).strip()]
                collecting = True
                continue
            source_match = SOURCE_RE.match(line)
            if source_match:
                collecting = False
                continue
            if collecting:
                if not line.strip():
                    continue
                # continuation line; stop at a new bullet/heading
                if re.match(r"^\s*(?:[-*]\s+|\*\*|#{1,6}\s)", line):
                    collecting = False
                    continue
                value.append(line.strip())
        targets[sid] = one_line(" ".join(value))
    return targets, duplicates


def artifact_entries(root):
    roles = [
        ("translation", "translation.md"),
        ("editable", "bilingual.md"),
        ("glossary", "glossary.json"),
        ("notes", "translation-notes.json"),
        ("report", "translation-report.md"),
    ]
    entries = []
    for role, name in roles:
        path = root / name
        if path.is_file() and path.stat().st_size > 0:
            fmt = "json" if name.endswith(".json") else "markdown"
            entries.append({"role": role, "path": name, "format": fmt})
    return entries


NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?:\s*[–~\-]\s*[-+]?\d+(?:[.,]\d+)?)?"
    r"\s*(?:%|°C|℃|eV|meV|nm|µm|μm|mm|cm|m|km|s|ms|min|h|hr|K|MPa|GPa|"
    r"kPa|Pa|mM|µM|μM|nM|mg|g|kg|mL|µL|μL|L|Da|kDa|ppm|mol|wt%|at%|"
    r"bar|atm|Torr|rpm)?", re.I)
CITE_RE = re.compile(
    r"\[[0-9,\s–\-]+\]|\([A-Z][^()]{0,80}?(?:19|20)\d{2}[a-z]?\)|"
    r"(?:Fig(?:ure)?|Table|Eq(?:uation)?)\.?\s*\d+[A-Za-z]?|"
    r"(?:图|表|式)\s*\d+[A-Za-z]?", re.I)
FORMULA_RE = re.compile(r"\$[^$]{2,}\$|\\\([^)]{2,}\\\)|\\begin\{[a-z]+\*?\}")
NEG_CUES = ("not ", "no ", "never", "without", "n't", "未", "不", "无", "没有", "并非")
MOD_CUES = ("may", "might", "could", "suggest", "likely", "possibly",
            "可能", "或许", "提示", "倾向于", "有望")


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _canon(kind, value):
    text = _norm(value)
    if kind == "citation":
        text = (text.replace("图", "fig").replace("表", "table")
                .replace("式", "eq").replace("figure", "fig")
                .replace("equation", "eq"))
        return re.sub(r"[^a-z0-9\[\]()]", "", text)
    if kind == "number":
        return re.sub(r"\s+", "", text).replace(",", "")
    return text


def _features(text):
    text = str(text or "")
    return {
        "number": sorted(set(_canon("number", x) for x in NUMBER_RE.findall(text))),
        "citation": sorted(set(_canon("citation", x) for x in CITE_RE.findall(text))),
        "formula": sorted(set(_canon("formula", x) for x in FORMULA_RE.findall(text))),
    }


def _term_present(term, text):
    low = str(text or "").lower()
    base = str(term or "").lower()
    variants = {base, base + "s", base + "es"}
    if base.endswith("y"):
        variants.add(base[:-1] + "ies")
    return any(v and v in low for v in variants)


def _cue_count(text, cues):
    low = str(text or "").lower()
    return sum(low.count(_norm(c)) for c in cues)


def collect_preservation(segments, locked_terms):
    facts, evidence, mismatches = [], [], []
    number_mismatch = citation_mismatch = formula_mismatch = False
    negation_mismatch = modality_mismatch = False
    for seg in segments:
        sid = seg.get("segment_id")
        source = seg.get("source") or ""
        target = seg.get("target") or ""
        sf, tf = _features(source), _features(target)
        for kind, flag_name in (("number", "numbers"), ("citation", "citations"),
                                ("formula", "formulas")):
            for item in sf[kind]:
                if item in tf[kind]:
                    facts.append({"kind": kind, "source": item, "target": item,
                                  "locator": sid})
                else:
                    mismatches.append("%s %s not found in target: %s" % (sid, kind, item))
                    if kind == "number":
                        number_mismatch = True
                    elif kind == "citation":
                        citation_mismatch = True
                    else:
                        formula_mismatch = True
        for term in locked_terms:
            src_term = term.get("source") or ""
            tgt_term = term.get("target") or ""
            if src_term and src_term in source:
                if tgt_term and _term_present(tgt_term, target):
                    facts.append({"kind": "term", "source": src_term,
                                  "target": tgt_term, "locator": sid})
                else:
                    mismatches.append("%s locked term not found in target: %s"
                                      % (sid, src_term))
        if _cue_count(source, NEG_CUES) != _cue_count(target, NEG_CUES):
            negation_mismatch = True
            mismatches.append("%s negation cue count mismatch" % sid)
        if _cue_count(source, MOD_CUES) != _cue_count(target, MOD_CUES):
            modality_mismatch = True
            mismatches.append("%s modality cue count mismatch" % sid)
        if target:
            evidence.append({
                "claim_id": sid,
                "claim": one_line(target)[:240],
                "source": sid,
                "locator": "%s source" % sid,
                "quote": one_line(source)[:300],
            })
    preservation = {
        "numbers_match": not number_mismatch,
        "units_match": not number_mismatch,
        "formulas_match": not formula_mismatch,
        "citation_numbers_match": not citation_mismatch,
        "figure_refs_match": not citation_mismatch,
        "negation_match": not negation_mismatch,
        "modality_match": not modality_mismatch,
    }
    return facts, evidence, preservation, mismatches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--direction", required=True, choices=["zh->en", "en->zh"])
    parser.add_argument("--segments", required=True, help="segments.json")
    parser.add_argument("--bilingual", required=True, help="bilingual.md")
    parser.add_argument("--glossary", default=None, help="glossary.json (optional)")
    parser.add_argument("--artifact-root", default=None,
                        help="directory holding translation.md etc.")
    parser.add_argument("--out", required=True, help="result.json to write")
    parser.add_argument("--status", choices=["completed", "partial"], default="completed")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    def fatal(message):
        print(json.dumps({"tool": "assemble_result", "status": "error",
                          "error": message}, ensure_ascii=False))
        return 2

    try:
        seg_doc = load_json(args.segments)
    except (OSError, ValueError) as exc:
        return fatal("cannot read segments: %s" % exc)
    if not isinstance(seg_doc, dict) or not isinstance(seg_doc.get("segments"), list):
        return fatal("segments.json must contain a 'segments' list")
    source_segments = seg_doc["segments"]

    try:
        bilingual_text = read_text(args.bilingual)
    except OSError as exc:
        return fatal("cannot read bilingual.md: %s" % exc)
    targets, duplicates = parse_bilingual(bilingual_text)

    glossary = {}
    if args.glossary:
        try:
            glossary = load_json(args.glossary)
        except (OSError, ValueError) as exc:
            return fatal("cannot read glossary: %s" % exc)
        if not isinstance(glossary, dict):
            return fatal("glossary.json must be an object")

    terms = [t for t in glossary.get("terms", [])
             if isinstance(t, dict) and isinstance(t.get("source"), str)
             and isinstance(t.get("target"), str)]
    locked_terms = [t for t in terms if t.get("locked", True)]

    segments = []
    missing = []
    for item in source_segments:
        if not isinstance(item, dict) or not isinstance(item.get("source"), str):
            return fatal("invalid source segment in segments.json")
        sid = item.get("segment_id")
        target = targets.get(sid, "")
        if not target:
            missing.append(sid)
        used = [t["source"] for t in locked_terms if t["source"] in item["source"]]
        segments.append({
            "segment_id": sid,
            "source": item["source"],
            "target": target,
            "terms": used,
            "added": [],
            "omitted": [],
            "flags": [],
            "confidence": "high" if target else "low",
        })

    known = {s["segment_id"] for s in source_segments if isinstance(s, dict)}
    extra = sorted(sid for sid in targets if sid not in known)

    status = args.status
    untranslated = []
    uncertainty = []
    if missing or extra or duplicates:
        status = "partial"
        for sid in missing:
            untranslated.append({"segment_id": sid,
                                 "reason": "target missing from bilingual.md"})
        for sid in duplicates:
            uncertainty.append("bilingual.md contains duplicate section: %s" % sid)
        if extra:
            uncertainty.append("bilingual.md contains unknown sections: %s"
                               % ", ".join(extra))
        uncertainty.append("assemble_result.py found alignment gaps; repair "
                           "bilingual.md and re-run or fill targets manually")

    root = Path(args.artifact_root) if args.artifact_root else Path(args.out).resolve().parent
    artifacts = artifact_entries(root)
    facts, evidence, preservation, mismatches = collect_preservation(
        segments, locked_terms)
    if mismatches:
        uncertainty.extend("preservation_mismatch: %s" % item
                           for item in mismatches[:40])
        uncertainty.append("preservation booleans are computed from source/target "
                           "segment text; review the mismatch list before submitting")

    result = {
        "case_id": args.case_id,
        "task_family": "translation",
        "mode": "translation",
        "status": status,
        "direction": args.direction,
        "artifacts": artifacts,
        "register": glossary.get("register", {}),
        "segments": segments,
        "glossary": [{"source": t["source"], "target": t["target"], "locked": True}
                     for t in locked_terms],
        "preservation": preservation,
        "back_translation_check": [],
        "untranslated": untranslated,
        "uncertainty": uncertainty,
        "change_log": [
            {"action": "assembled", "scope": "segments from bilingual.md",
             "reason": "assemble_result.py; preservation facts/evidence computed "
                       "from source and target segments; manual review still required",
             "human_review": bool(mismatches)}
        ],
        "translation_check": {
            "path": "translation-check.json" if (root / "translation-check.json").is_file() else None,
            "exit_code": None, "errors": None, "warnings": None,
            "note": "optional audit; fill after running check_translation.py",
        },
        "deliverable": {"path": "translation.md", "format": "markdown"},
        "preserved_facts": facts,
        "evidence_map": evidence,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {
        "tool": "assemble_result",
        "status": status,
        "out": str(out),
        "segments": len(segments),
        "missing_targets": missing,
        "duplicate_sections": sorted(set(duplicates)),
        "extra_sections": extra,
        "locked_terms": len(locked_terms),
        "preserved_facts": len(facts),
        "evidence_map": len(evidence),
        "preservation_mismatches": len(mismatches),
        "note": "preservation/facts/evidence are computed from source/target segments; review mismatches",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("wrote %s (%d segments, %d missing targets)" % (out, len(segments), len(missing)))
    return 1 if (missing or extra or duplicates) else 0


if __name__ == "__main__":
    sys.exit(main())
