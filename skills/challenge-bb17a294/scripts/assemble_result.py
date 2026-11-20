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
        "preservation": {
            "numbers_match": False,
            "units_match": False,
            "formulas_match": False,
            "citation_numbers_match": False,
            "figure_refs_match": False,
            "negation_match": False,
            "modality_match": False,
        },
        "back_translation_check": [],
        "untranslated": untranslated,
        "uncertainty": uncertainty,
        "change_log": [
            {"action": "assembled", "scope": "segments from bilingual.md",
             "reason": "assemble_result.py; preservation/evidence left for manual completion",
             "human_review": False}
        ],
        "translation_check": {
            "path": "translation-check.json" if (root / "translation-check.json").is_file() else None,
            "exit_code": None, "errors": None, "warnings": None,
            "note": "optional audit; fill after running check_translation.py",
        },
        "deliverable": {"path": "translation.md", "format": "markdown"},
        "preserved_facts": [],
        "evidence_map": [],
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
        "note": "preservation defaults to false; set true only after manual checks",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("wrote %s (%d segments, %d missing targets)" % (out, len(segments), len(missing)))
    return 1 if (missing or extra or duplicates) else 0


if __name__ == "__main__":
    sys.exit(main())
