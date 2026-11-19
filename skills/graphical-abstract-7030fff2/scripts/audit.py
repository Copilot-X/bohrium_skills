#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit a rendered graphical abstract before result.json is accepted.

Checks, in one deterministic pass:
  * spec element/relation ids are all present in the SVG as named <g> groups;
  * the SVG is well-formed XML with a viewBox;
  * visible text is real <text> (no <foreignObject>/<script>);
  * no http(s)/file external references or embedded scripts;
  * PDF/PNG file magic and PNG pixel dimensions match the canvas ratio;
  * every font-size stays above the journal minimum (warning only).

The JSON report is intentionally plain so run_case.py and result_graphics.py
can copy it into result.json without reinterpretation.

Usage:
    python3 audit.py --spec /app/work/ga-spec.json --svg /app/output/figures/ga.svg \
        --pdf /app/output/figures/ga.pdf --png /app/output/preview/ga.png \
        --out /app/work/coverage.json --json
"""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

EXTERNAL_RE = re.compile(r'(?:href|src)\s*=\s*["\']\s*(?:https?:|file:)', re.I)
URL_RE = re.compile(r"url\(\s*['\"]?\s*(?:https?:|file:)", re.I)
FONT_RE = re.compile(r"font-size\s*=\s*[\"']\s*([-+]?[0-9.]+)\s*(px|pt)?", re.I)
PT_PER_IN = 72.0
PX_PER_IN = 96.0


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def png_size(head):
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def audit(spec, svg_path=None, pdf_path=None, png_path=None,
          min_font_pt=8.0, dpi=300):
    errors, warnings = [], []
    elements = spec.get("elements") if isinstance(spec.get("elements"), list) else []
    relations = spec.get("relations") if isinstance(spec.get("relations"), list) else []
    element_ids = [str(el.get("id")) for el in elements if isinstance(el, dict)]
    relation_ids = [str(rel.get("id")) for rel in relations if isinstance(rel, dict)]
    expected_ids = element_ids + relation_ids

    svg_checks = {"exists": False}
    pdf_checks = {"exists": False}
    png_checks = {"exists": False}
    svg_ok = text_ok = external_ok = False
    font_min = None

    if svg_path:
        svg = Path(svg_path)
        svg_checks["exists"] = svg.is_file()
        if svg.is_file():
            raw = svg.read_text(encoding="utf-8", errors="replace")
            svg_checks["bytes"] = svg.stat().st_size
            svg_checks["text_elements"] = len(re.findall(r"<text\b", raw))
            svg_checks["named_groups"] = len(re.findall(r"<g\b[^>]*\bid\s*=", raw))
            svg_checks["external_refs"] = len(EXTERNAL_RE.findall(raw)) + len(URL_RE.findall(raw))
            svg_checks["has_script"] = "<script" in raw.lower()
            svg_checks["has_foreign_object"] = "<foreignobject" in raw.lower()
            svg_checks["has_image"] = "<image" in raw.lower()
            try:
                root = ET.fromstring(raw)
                svg_ok = local_name(root.tag) == "svg"
                svg_checks["xml_valid"] = svg_ok
                svg_checks["viewBox"] = bool(root.get("viewBox") or root.get("viewbox"))
                if not svg_checks["viewBox"]:
                    warnings.append({"code": "SVG_NO_VIEWBOX",
                                     "detail": "root svg has no viewBox"})
                ids, duplicates = [], []
                for node in root.iter():
                    value = node.get("id")
                    if not value:
                        continue
                    if value in ids:
                        duplicates.append(value)
                    ids.append(value)
                svg_checks["ids"] = ids
                svg_checks["duplicate_ids"] = sorted(set(duplicates))
                present = set(ids)
                missing = [rid for rid in expected_ids if rid not in present]
                svg_checks["missing_ids"] = missing
                svg_checks["unexpected_groups"] = sorted(
                    gid for node in root.iter() if local_name(node.tag) == "g"
                    for gid in [node.get("id")] if gid and gid not in set(expected_ids))
                nonempty_text = 0
                for node in root.iter():
                    if local_name(node.tag) == "text" and "".join(node.itertext()).strip():
                        nonempty_text += 1
                svg_checks["nonempty_text"] = nonempty_text
                text_ok = (nonempty_text > 0 and
                           not svg_checks["has_foreign_object"])
                if not text_ok:
                    errors.append({"code": "TEXT_NOT_EDITABLE",
                                   "detail": "no non-empty <text> or foreignObject used"})
                external_ok = (svg_checks["external_refs"] == 0 and
                               not svg_checks["has_script"])
                if not external_ok:
                    errors.append({"code": "SVG_NOT_SELF_CONTAINED",
                                   "detail": "external reference or script tag"})
                if not svg_ok:
                    errors.append({"code": "SVG_INVALID", "detail": "root is not <svg>"})
                elif not svg_checks["viewBox"]:
                    pass
                if missing:
                    errors.append({"code": "MISSING_IDS",
                                   "detail": "spec ids absent from SVG",
                                   "ids": missing})
                if duplicates:
                    errors.append({"code": "DUPLICATE_IDS",
                                   "detail": "duplicate SVG ids",
                                   "ids": sorted(set(duplicates))})
                sizes = []
                for m in FONT_RE.finditer(raw):
                    value = float(m.group(1))
                    if (m.group(2) or "px").lower() == "pt":
                        value = value * PX_PER_IN / PT_PER_IN
                    sizes.append(value)
                if sizes:
                    font_min = round(min(sizes), 2)
                    threshold = min_font_pt * PX_PER_IN / PT_PER_IN
                    svg_checks["min_font_px"] = font_min
                    svg_checks["min_font_required_px"] = round(threshold, 2)
                    if font_min + 0.5 < threshold:
                        warnings.append({"code": "FONT_TOO_SMALL",
                                         "detail": "min font %.1fpx < %.1fpx"
                                         % (font_min, threshold)})
            except ET.ParseError as exc:
                svg_checks["xml_valid"] = False
                errors.append({"code": "SVG_INVALID", "detail": str(exc)})
        else:
            errors.append({"code": "SVG_MISSING", "detail": str(svg)})

    for label, path, checks in (("pdf", pdf_path, pdf_checks),
                                ("png", png_path, png_checks)):
        if not path:
            continue
        p = Path(path)
        checks["exists"] = p.is_file()
        if not p.is_file():
            errors.append({"code": "EXPORT_MISSING", "format": label,
                           "detail": str(p)})
            continue
        head = p.read_bytes()[:32]
        checks["bytes"] = p.stat().st_size
        if label == "pdf":
            checks["signature"] = head.startswith(b"%PDF")
            if not checks["signature"]:
                errors.append({"code": "EXPORT_INVALID", "format": "pdf"})
        else:
            size = png_size(head)
            checks["signature"] = size is not None
            if size is None:
                errors.append({"code": "EXPORT_INVALID", "format": "png"})
            else:
                checks["width_px"], checks["height_px"] = size
                canvas = spec.get("canvas") or {}
                cw, ch = canvas.get("width_px"), canvas.get("height_px")
                if cw and ch:
                    expected = cw / float(ch)
                    actual = size[0] / float(size[1])
                    checks["aspect_ratio"] = round(actual, 4)
                    checks["aspect_ok"] = abs(actual - expected) < 0.02
                    if not checks["aspect_ok"]:
                        warnings.append({"code": "PNG_ASPECT_MISMATCH",
                                         "detail": "%.3f vs %.3f" % (actual, expected)})

    total = len(expected_ids)
    present_count = total - len(svg_checks.get("missing_ids") or [])
    coverage = round(present_count / total, 4) if total else 0.0
    if total and coverage < 1.0:
        pass  # MISSING_IDS already recorded
    ok = not errors
    return {
        "ok": ok,
        "coverage": coverage,
        "expected_ids": expected_ids,
        "element_ids": element_ids,
        "relation_ids": relation_ids,
        "elements": len(element_ids),
        "relations": len(relation_ids),
        "svg": svg_checks,
        "exports": {"pdf": pdf_checks, "png": png_checks},
        "checks": {
            "xml_valid": bool(svg_checks.get("xml_valid")),
            "text_as_text": text_ok,
            "no_external_refs": external_ok,
            "id_coverage": coverage,
            "duplicate_ids": svg_checks.get("duplicate_ids") or [],
            "font_min_px": font_min,
            "pdf_valid": bool(pdf_checks.get("signature")),
            "png_valid": bool(png_checks.get("signature")),
        },
        "errors": errors,
        "warnings": warnings,
        "min_font_pt": min_font_pt,
        "dpi": dpi,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--svg", default=None)
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--png", default=None)
    ap.add_argument("--out", default="/app/work/coverage.json")
    ap.add_argument("--min-font-pt", type=float, default=8.0)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    spec = load_json(args.spec)
    if not spec:
        report = {"ok": False, "coverage": 0.0,
                  "errors": [{"code": "SPEC_INVALID", "detail": args.spec}]}
    else:
        report = audit(spec, args.svg, args.pdf, args.png,
                       args.min_font_pt, args.dpi)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
