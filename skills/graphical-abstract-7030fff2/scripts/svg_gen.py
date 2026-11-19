#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize a GA spec and render it to an editable SVG.

This is the content-to-geometry boundary: run_case.py passes a spec (written
by the agent from the source paper, or derived from case.json), svg_gen
validates ids/canvas/relations, and ga_render.py performs the deterministic
drawing.  All visible text stays <text>; every panel and narrative arrow is a
named <g id="G*"> / <g id="E*">.

Usage:
    python3 svg_gen.py --spec /app/work/ga-spec.json \
        --out /app/output/figures/ga.svg --json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

ROLES = ("problem", "approach", "method", "result", "implication", "conclusion")
KINDS = ("reported", "schematic", "inferred", "estimated")
MAX_ELEMENTS = 5
SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")
HERE = Path(__file__).resolve().parent


def load_sibling(name):
    """Load a sibling module on disk or in the .md-wrapped build form."""
    for candidate in (HERE / name, HERE / (name + ".md")):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "_ga_" + name.replace(".py", ""), candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError("missing renderer: %s" % name)


def normalize_source(value):
    if isinstance(value, str) and value.strip():
        return {"path": value.strip(), "locator": ""}
    if isinstance(value, dict):
        path = value.get("path") or value.get("file") or value.get("source")
        locator = value.get("locator") or value.get("section") or value.get("page") or ""
        if path:
            return {"path": str(path), "locator": str(locator)}
    return None


def normalize_spec(raw):
    """Return (spec, notes); notes record honest repairs, never silent fixes."""
    raw = raw if isinstance(raw, dict) else {}
    notes = []

    def note(code, detail):
        notes.append({"code": code, "detail": detail})

    claim_obj = raw.get("claim")
    if isinstance(claim_obj, str):
        claim_obj = {"claim": claim_obj}
    elif not isinstance(claim_obj, dict):
        claim_obj = {}
    claim_text = str(claim_obj.get("claim") or raw.get("title") or "").strip()
    support = []
    raw_support = claim_obj.get("support")
    if not isinstance(raw_support, list):
        if raw_support:
            note("support_repaired", "claim.support is not a list; ignored")
        raw_support = []
    for item in raw_support:
        src = normalize_source(item)
        if src:
            support.append(src)
    numbers = []
    raw_numbers = claim_obj.get("numbers")
    if not isinstance(raw_numbers, list):
        raw_numbers = []
    for item in raw_numbers:
        if isinstance(item, dict) and str(item.get("value") or "").strip():
            numbers.append({"value": str(item.get("value")),
                            "unit": str(item.get("unit") or ""),
                            "source": str(item.get("source") or item.get("locator") or "")})
    raw_not_claimed = claim_obj.get("not_claimed")
    if isinstance(raw_not_claimed, str):
        raw_not_claimed = [raw_not_claimed]
    elif not isinstance(raw_not_claimed, list):
        raw_not_claimed = []
    not_claimed = [str(x).strip() for x in raw_not_claimed if str(x).strip()]

    elements = []
    raw_elements = raw.get("elements") if isinstance(raw.get("elements"), list) else []
    if len(raw_elements) > MAX_ELEMENTS:
        note("elements_truncated", "kept first %d of %d elements"
             % (MAX_ELEMENTS, len(raw_elements)))
    for i, item in enumerate(raw_elements[:MAX_ELEMENTS]):
        if isinstance(item, str):
            item = {"concept": item}
        if not isinstance(item, dict):
            note("element_skipped", "element %d is not an object" % (i + 1))
            continue
        concept = " ".join(str(item.get("concept") or item.get("label") or
                                 item.get("title") or "").split())
        if not concept:
            note("element_skipped", "element %d has no concept/label" % (i + 1))
            continue
        eid = str(item.get("id") or "G%d" % (i + 1)).strip()
        if not SAFE_ID.match(eid):
            note("id_repaired", "element id %r -> G%d" % (eid, i + 1))
            eid = "G%d" % (i + 1)
        role = str(item.get("role") or "default").strip().lower()
        if role not in ROLES:
            note("role_defaulted", "element %s role %r -> default" % (eid, role))
            role = "default"
        kind = str(item.get("kind") or item.get("status") or "reported").lower()
        if kind not in KINDS:
            kind = "reported"
        source = normalize_source(item.get("source") or item.get("maps_to") or
                                  item.get("evidence"))
        if source is None and kind != "inferred":
            note("missing_source", "element %s has no source path" % eid)
        elements.append({"id": eid, "role": role, "concept": concept,
                         "kind": kind, "source": source})
    seen = set()
    for el in elements:
        base, n = el["id"], 2
        while el["id"] in seen:
            el["id"] = "%s_%d" % (base, n)
            n += 1
        seen.add(el["id"])

    element_ids = {el["id"] for el in elements}
    relations = []
    raw_relations = raw.get("relations")
    if isinstance(raw_relations, list) and raw_relations:
        for i, item in enumerate(raw_relations[:12]):
            if not isinstance(item, dict):
                continue
            rid = str(item.get("id") or "E%d" % (i + 1)).strip()
            if not SAFE_ID.match(rid):
                rid = "E%d" % (i + 1)
            src = str(item.get("from") or item.get("source") or "").strip()
            dst = str(item.get("to") or item.get("target") or "").strip()
            if src not in element_ids or dst not in element_ids:
                note("relation_skipped", "relation %s endpoints %r -> %r not found"
                     % (rid, src, dst))
                continue
            relations.append({"id": rid, "from": src, "to": dst,
                              "label": str(item.get("label") or "")[:40],
                              "kind": str(item.get("kind") or "narrative")})
    else:
        for i in range(len(elements) - 1):
            relations.append({"id": "E%d" % (i + 1),
                              "from": elements[i]["id"],
                              "to": elements[i + 1]["id"],
                              "label": "", "kind": "narrative"})

    canvas = raw.get("canvas") if isinstance(raw.get("canvas"), dict) else {}
    width = canvas.get("width_px") or raw.get("width_px") or 1200
    height = canvas.get("height_px") or raw.get("height_px") or 600
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError):
        note("canvas_defaulted", "invalid canvas size; using 1200x600")
        width, height = 1200, 600
    journal = raw.get("journal_spec") if isinstance(raw.get("journal_spec"), dict) else {}
    try:
        min_font_pt = float(journal.get("min_font_pt") or 8)
    except (TypeError, ValueError):
        min_font_pt = 8.0

    spec = {
        "case_id": str(raw.get("case_id") or "CASE"),
        "claim": {"claim": claim_text, "support": support,
                  "not_claimed": not_claimed, "numbers": numbers},
        "elements": elements,
        "relations": relations,
        "canvas": {"width_px": max(800, width), "height_px": max(400, height),
                   "dpi": int(canvas.get("dpi") or raw.get("dpi") or 300)},
        "journal_spec": {"min_font_pt": min_font_pt,
                         "source": str(journal.get("source") or "task.md")},
        "reading_order": str(raw.get("reading_order") or "left-to-right"),
        "layout_type": str(raw.get("layout_type") or "linear-progression"),
        "style_type": str(raw.get("style_type") or "journal"),
        "caption": str(raw.get("caption") or ""),
        "alt_text": str(raw.get("alt_text") or claim_text),
        "visual_reviewed": raw.get("visual_reviewed") is True,
        "source": str(raw.get("source") or journal.get("source") or ""),
    }
    if not claim_text:
        note("claim_missing", "spec has no single-sentence claim")
    elif not support:
        note("claim_unsourced", "claim has no source path/locator")
    if len(elements) < 3:
        note("elements_insufficient", "%d elements; a graphical abstract needs 3-5"
             % len(elements))
    return spec, notes


def build(raw_spec, out_path):
    spec, notes = normalize_spec(raw_spec)
    engine = load_sibling("ga_render.py")
    info = engine.build(spec, out_path)
    info["spec"] = spec
    info["notes"] = notes
    info["claim"] = spec["claim"]["claim"]
    info["element_ids"] = [el["id"] for el in spec["elements"]]
    info["relation_ids"] = [rel["id"] for rel in spec["relations"]]
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        raw = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    try:
        info = build(raw, args.out)
    except Exception as exc:  # noqa: BLE001 - renderer failure is reported, not hidden
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False, indent=2))
        return 2
    info.pop("spec", None)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
