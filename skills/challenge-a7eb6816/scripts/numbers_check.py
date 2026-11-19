#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check numbers, units, conditions and restricted content in the draft.

Usage:
    python3 numbers_check.py --work /app/work --draft /app/output/article.md [--json]

``ok=false`` only for hard errors: a number+unit absent from the material, a
unit mismatch, a leaked restricted statement, or placeholder text.  Missing
key facts and condition placement are warnings so that a complete draft is
never blocked by a heuristic.  Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # package mode (python -m scripts.numbers_check)
    from . import transfer_kit as kit
    from . import evidence as evidence_mod
except ImportError:  # direct mode (python scripts/numbers_check.py)
    kit = __import__("transfer_kit")
    evidence_mod = __import__("evidence")

COUNT_UNITS = {"n", "次", "例", "人", "只", "个", "批次", "重复", "sample", "samples"}
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|Lorem ipsum)\b|待补充|待填写|此处插入", re.I)
HYPE = re.compile(r"颠覆性|革命性|填补国内空白|国际领先|世界首创|guaranteed|"
                  r"revolutionary|world[- ]first", re.I)
FIG_RE = re.compile(r"(?:图|Figure|Fig\.?)\s*(\d+)", re.I)
TAB_RE = re.compile(r"(?:表|Table)\s*(\d+)", re.I)


def _keys_from_quantities(quantities):
    return {kit.number_key(q["value"], q.get("unit") or "") for q in quantities}


def _ranges(evidence):
    out = []
    for entry in evidence.get("evidence") or []:
        groups = {}
        for q in entry.get("quantities") or []:
            if q.get("comparison") == "range":
                groups.setdefault(q.get("raw"), []).append(q)
        for qs in groups.values():
            if len(qs) >= 2:
                try:
                    lo, hi = sorted(float(q["value"]) for q in qs)
                except ValueError:
                    continue
                out.append((lo, hi, kit.normalize_unit(qs[0].get("unit") or "")))
    return out


def _condition_tokens(entry):
    tokens = set()
    for cond in entry.get("conditions") or []:
        for q in evidence_mod.extract_quantities(cond):
            tokens.add(kit.number_key(q["value"], q.get("unit") or ""))
        for scale in ("实验室", "小试", "中试", "工业", "现场", "临床",
                      "体外", "体内", "模拟", "示范"):
            if scale in cond:
                tokens.add(scale)
    return tokens


def _paragraphs(draft_text):
    blocks = [b["text"] for b in kit.markdown_blocks(draft_text)
              if b["kind"] in ("paragraph", "list", "quote", "table_row")]
    return blocks or [draft_text]


def _paragraph_index(paragraphs, token_raw):
    for idx, para in enumerate(paragraphs):
        if token_raw in para:
            return idx
    return None


def _count_words(text, language):
    if language == "zh":
        return len(re.findall(r"[一-鿿]", text))
    return len(re.findall(r"\b[A-Za-z0-9][A-Za-z0-9'\-]*\b", text))


def check(draft_text, evidence, workorder=None, source_text=""):
    workorder = workorder or {}
    draft_text = draft_text or ""
    errors, warnings = [], []
    if not draft_text.strip():
        errors.append({"code": "empty_draft", "detail": "draft is empty"})
        return _report(False, errors, warnings, draft_text, evidence, workorder)

    source_quantities = []
    for entry in evidence.get("evidence") or []:
        source_quantities.extend(entry.get("quantities") or [])
    if source_text:
        source_quantities.extend(evidence_mod.extract_quantities(source_text))
    source_keys = _keys_from_quantities(source_quantities)
    source_values = {q["value"] for q in source_quantities}
    value_units = {}
    for q in source_quantities:
        value_units.setdefault(q["value"], set()).add(q.get("normalized_unit") or "")
    ranges = _ranges(evidence)

    draft_quantities = evidence_mod.extract_quantities(draft_text)
    preserved_keys = set()
    for q in draft_quantities:
        key = kit.number_key(q["value"], q.get("unit") or "")
        if key in source_keys:
            preserved_keys.add(key)
            continue
        unit = q.get("normalized_unit") or ""
        value = q["value"]
        if value in source_values:
            if unit in COUNT_UNITS and (value_units.get(value, set()) & COUNT_UNITS):
                warnings.append({
                    "code": "count_unit_paraphrase",
                    "token": q["raw"],
                    "detail": "count value present in source with a different count unit",
                })
                preserved_keys.add(key)
                continue
            try:
                num = float(value)
            except ValueError:
                num = None
            if num is not None and any(lo <= num <= hi and unit == r_unit
                                       for lo, hi, r_unit in ranges):
                warnings.append({
                    "code": "value_inside_reported_range",
                    "token": q["raw"],
                    "detail": "value lies inside a source range but is not stated verbatim; "
                              "use approximate wording or quote the range",
                })
                preserved_keys.add(key)
                continue
            errors.append({
                "code": "unit_mismatch",
                "token": q["raw"],
                "detail": "value exists in source but with different unit(s): %s"
                          % ", ".join(sorted(value_units.get(value, set()))),
            })
            continue
        if unit == "a" and value.isdigit() and 1900 <= int(value) <= 2100:
            warnings.append({
                "code": "unsourced_year",
                "token": q["raw"],
                "detail": "year not found in material; verify before publishing",
            })
            continue
        errors.append({
            "code": "number_not_in_source",
            "token": q["raw"],
            "detail": "number+unit not found in the material; remove it or mark it "
                      "as an explicit calculation/assumption",
        })

    # Key-fact coverage: omission is a warning, not a hard failure.
    key_ids = set(evidence.get("key_facts") or [])
    key_entries = [e for e in evidence.get("evidence") or []
                   if e.get("evidence_id") in key_ids]
    missing = []
    paragraphs = _paragraphs(draft_text)
    for entry in key_entries:
        entry_keys = _keys_from_quantities(entry.get("quantities") or [])
        if entry_keys and not (entry_keys & preserved_keys):
            missing.append({"evidence_id": entry.get("evidence_id"),
                            "statement": kit.excerpt(entry.get("statement"), 140),
                            "numbers": [q["raw"] for q in entry.get("quantities") or []]})
            continue
        # Condition proximity: the condition should appear in the same or an
        # adjacent paragraph, otherwise a lab number may read as universal.
        cond_tokens = _condition_tokens(entry)
        if not cond_tokens:
            continue
        anchor = None
        for q in entry.get("quantities") or []:
            anchor = _paragraph_index(paragraphs, q["raw"])
            if anchor is not None:
                break
        if anchor is None:
            continue
        window = "\n".join(paragraphs[max(0, anchor - 1): anchor + 2])
        window_keys = _keys_from_quantities(evidence_mod.extract_quantities(window))
        if not (cond_tokens & window_keys) and not any(
                scale in window for scale in cond_tokens if not any(ch.isdigit() for ch in scale)):
            warnings.append({
                "code": "condition_not_nearby",
                "evidence_id": entry.get("evidence_id"),
                "detail": "key number appears without its condition nearby: %s"
                          % "；".join(entry.get("conditions") or [])[:160],
            })

    if missing:
        warnings.append({
            "code": "missing_key_facts",
            "detail": "%d key quantitative facts are not preserved" % len(missing),
            "items": missing[:8],
        })

    # Placeholders, hype and restricted leakage.
    placeholders = PLACEHOLDER.findall(draft_text)
    if placeholders:
        errors.append({"code": "placeholder_text",
                       "detail": "draft still contains placeholder text",
                       "items": kit.unique([p if isinstance(p, str) else "TODO"
                                               for p in placeholders])[:8]})
    hype = HYPE.findall(draft_text)
    if hype:
        warnings.append({"code": "unverifiable_hype",
                         "detail": "unverifiable wording found",
                         "items": kit.unique(hype)[:8]})

    normalized_draft = kit.normalize_space(draft_text).replace(" ", "").lower()
    for item in evidence.get("restricted") or []:
        statement = str(item.get("statement") or "")
        probe = kit.normalize_space(statement).replace(" ", "").lower()
        if len(probe) >= 12 and probe in normalized_draft:
            errors.append({"code": "restricted_leak",
                           "detail": "restricted statement appears in the draft",
                           "evidence_id": item.get("evidence_id")})

    # Figure/table numbering must not invent new objects.
    source_figs = set()
    source_tabs = set()
    for cit in evidence.get("citations") or []:
        raw = str(cit.get("raw") or "")
        m = FIG_RE.search(raw)
        if m:
            source_figs.add(m.group(1))
        m = TAB_RE.search(raw)
        if m:
            source_tabs.add(m.group(1))
    for m in FIG_RE.finditer(draft_text):
        if source_figs and m.group(1) not in source_figs:
            warnings.append({"code": "figure_number_not_in_source",
                             "detail": m.group(0)})
    for m in TAB_RE.finditer(draft_text):
        if source_tabs and m.group(1) not in source_tabs:
            warnings.append({"code": "table_number_not_in_source",
                             "detail": m.group(0)})

    # Length limit from the work order.
    limit = (workorder or {}).get("length_limit") or {}
    length_info = {"limit": limit, "measured": None, "status": "not_checked"}
    if limit.get("kind") == "characters":
        measured = len(draft_text)
        length_info.update({"measured": measured, "unit": "characters"})
        if limit.get("value"):
            if measured > limit["value"] * 1.2:
                warnings.append({"code": "over_length",
                                 "detail": "%d chars > limit %d" % (measured, limit["value"])})
            elif measured < limit["value"] * 0.55:
                warnings.append({"code": "under_length",
                                 "detail": "%d chars < 55%% of limit %d"
                                           % (measured, limit["value"])})
            length_info["status"] = "checked"
    elif limit.get("kind") == "words":
        measured = _count_words(draft_text, (workorder or {}).get("language", "zh"))
        length_info.update({"measured": measured, "unit": "words"})
        if limit.get("value"):
            if measured > limit["value"] * 1.2:
                warnings.append({"code": "over_length",
                                 "detail": "%d words > limit %d" % (measured, limit["value"])})
            elif measured < limit["value"] * 0.55:
                warnings.append({"code": "under_length",
                                 "detail": "%d words < 55%% of limit %d"
                                           % (measured, limit["value"])})
            length_info["status"] = "checked"
    elif limit.get("kind") == "pages":
        warnings.append({"code": "page_limit_not_measured",
                         "detail": "page limit is checked in the DOCX preview, not in markdown"})

    return _report(not errors, errors, warnings, draft_text, evidence, workorder,
                   length_info=length_info, missing=missing,
                   source_numbers=len(source_keys), preserved=len(preserved_keys))


def _report(ok, errors, warnings, draft_text, evidence, workorder,
            length_info=None, missing=None, source_numbers=0, preserved=0):
    language = (workorder or {}).get("language", "zh")
    return {
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "draft": {
            "chars": len(draft_text or ""),
            "words": _count_words(draft_text or "", language),
            "paragraphs": len(_paragraphs(draft_text or "")),
            "sha256": kit.sha256_text(draft_text or ""),
        },
        "coverage": {
            "key_facts": len((evidence or {}).get("key_facts") or []),
            "source_number_keys": source_numbers,
            "preserved_number_keys": preserved,
            "missing": missing or [],
        },
        "length": length_info or {},
        "generated_at": kit.now_iso(),
    }


def write(work_dir, report):
    path = Path(work_dir) / "numbers-check.json"
    kit.write_json(path, report)
    return path


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
    draft_path = Path(args.draft) if args.draft else Path("/app/output") / workorder.get(
        "deliverable_name", "article.md")
    draft_text = kit.read_text(draft_path) if draft_path.is_file() else ""
    source_text = ""
    inventory = kit.read_json(work / "inventory.json", default={}) or {}
    for f in inventory.get("files", []):
        if f.get("role") == "task_metadata":
            continue
        if f.get("readable") and f.get("text_path"):
            try:
                source_text += "\n" + kit.read_text(work / f["text_path"])
            except OSError:
                pass
    report = check(draft_text, evidence, workorder, source_text)
    path = write(work, report)
    print(json.dumps({"ok": report["ok"], "errors": report["errors"],
                      "warnings": report["warnings"], "coverage": report["coverage"],
                      "numbers_check": str(path)},
                     ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
