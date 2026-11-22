#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit a rendered pathway figure: SVG structure, text, references, semantic
line styles, compartment/molecule/edge coverage, export headers and pixels.

The audit is read-only and returns a JSON-serialisable report.  It never treats
an inferred edge as an error when the edge is dashed and carries a disclaimer;
it does treat a reported edge without source, an unmapped element, a wrong
edge style or a fake export as errors.

Usage:
    python3 audit.py --ledger /app/work/pathway.json \
        --spec /app/work/figure-spec.json --svg /app/output/figures/fig3.svg \
        --pdf /app/output/figures/fig3.pdf --png /app/output/preview/fig3.png \
        --out /app/work/coverage.json --json
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pathway_svg import (  # noqa: E402
    normalize_ledger, load_json, source_ok,
)

EXPECTED_EFFECTS = {
    "activate", "inhibit", "bind", "translocate", "modify",
    "association", "unknown_link", "inferred",
}
ARROW_MARKERS = {"arrow-blue", "arrow-dark", "arrow-purple", "arrow-gray",
                 "arrow"}
INHIBIT_MARKERS = {"inhibit-orange", "inhibit-gray", "arrow-inhibit"}


def local(tag):
    return tag.rsplit("}", 1)[-1]


def marker_name(value):
    match = re.search(r"url\(#([^)]+)\)", str(value or ""))
    return match.group(1) if match else ""


def svg_profile(svg_path):
    result = {"exists": False, "xml_valid": False, "external_refs": 0,
              "script": False, "image": False, "text_elements": 0,
              "texts": [], "groups": {}, "legend_effects": set(),
              "viewbox": None, "bytes": 0, "error": ""}
    path = Path(svg_path)
    if not path.is_file():
        result["error"] = "missing"
        return result
    result["exists"] = True
    raw = path.read_text(encoding="utf-8", errors="replace")
    result["bytes"] = len(raw.encode("utf-8"))
    result["external_refs"] = len(re.findall(
        r'(?:href|src)\s*=\s*["\']\s*(?:https?:|file:|//)', raw, re.I))
    result["script"] = "<script" in raw.lower()
    result["image"] = "<image" in raw.lower()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        result["error"] = "xml: %s" % exc
        return result
    if local(root.tag) != "svg":
        result["error"] = "root is not <svg>"
        return result
    result["xml_valid"] = True
    result["viewbox"] = root.get("viewBox")
    for elem in root.iter():
        tag = local(elem.tag)
        if tag == "text":
            result["text_elements"] += 1
            text = "".join(elem.itertext()).strip()
            if text:
                result["texts"].append(text)
        elif tag == "g" and elem.get("id"):
            gid = elem.get("id")
            info = {
                "role": elem.get("data-role") or "",
                "shape": elem.get("data-shape") or "",
                "entity_kind": elem.get("data-entity-kind") or "",
                "effect": elem.get("data-effect") or "",
                "kind": elem.get("data-kind") or "",
                "source": elem.get("data-source") or "",
                "dash": [],
                "markers": [],
                "strokes": [],
            }
            for child in elem.iter():
                if local(child.tag) == "path":
                    if child.get("stroke-dasharray"):
                        info["dash"].append(child.get("stroke-dasharray"))
                    if child.get("marker-end"):
                        info["markers"].append(marker_name(child.get("marker-end")))
                    if child.get("stroke"):
                        info["strokes"].append(child.get("stroke").lower())
            result["groups"][gid] = info
            if info["role"] == "legend-item":
                result["legend_effects"].add(info["effect"])
        elif tag == "g" and elem.get("data-role") == "legend-item":
            effect = elem.get("data-effect") or ""
            if effect:
                result["legend_effects"].add(effect)
    result["legend_effects"] = sorted(result["legend_effects"])
    return result


def png_profile(png_path):
    """Inspect PNG header and pixels.  Uses Pillow when available, else a small
    stdlib decoder for non-interlaced 8-bit RGB/RGBA/gray PNGs."""
    path = Path(png_path)
    result = {"exists": False, "valid": False, "width_px": 0, "height_px": 0,
              "bytes": 0, "non_white_fraction": 0.0, "content_bbox": None,
              "distinct_colors": 0, "pixel_reviewed": False, "error": ""}
    if not path.is_file():
        result["error"] = "missing"
        return result
    raw = path.read_bytes()
    result["exists"] = True
    result["bytes"] = len(raw)
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) < 24:
        result["error"] = "PNG signature missing"
        return result
    result["valid"] = True
    result["width_px"] = int.from_bytes(raw[16:20], "big")
    result["height_px"] = int.from_bytes(raw[20:24], "big")
    try:
        pixels = _load_pixels(path)
    except Exception as exc:  # noqa: BLE001 - pixel review is best effort
        result["error"] = "pixel decode: %s: %s" % (type(exc).__name__, exc)
        return result
    if not pixels:
        return result
    width, height, channels, data = pixels
    step = max(1, min(width, height) // 220)
    non_white = 0
    total = 0
    colors = set()
    x_min, y_min = width, height
    x_max = y_max = -1
    for y in range(0, height, step):
        row = y * width * channels
        for x in range(0, width, step):
            off = row + x * channels
            if channels == 1:
                r = g = b = data[off]
            else:
                r, g, b = data[off], data[off + 1], data[off + 2]
            total += 1
            if r < 246 or g < 246 or b < 246:
                non_white += 1
                x_min, x_max = min(x_min, x), max(x_max, x)
                y_min, y_max = min(y_min, y), max(y_max, y)
            if len(colors) < 4096:
                colors.add((r // 8, g // 8, b // 8))
    result["non_white_fraction"] = round(non_white / max(1, total), 4)
    result["distinct_colors"] = len(colors)
    if x_max >= 0:
        result["content_bbox"] = [x_min, y_min, x_max, y_max]
    margin = max(2, int(min(width, height) * 0.002))
    bbox = result["content_bbox"]
    result["pixel_reviewed"] = bool(
        bbox and 0.005 <= result["non_white_fraction"] <= 0.98 and
        result["distinct_colors"] >= 4 and
        bbox[0] >= margin and bbox[1] >= margin and
        bbox[2] <= width - margin and bbox[3] <= height - margin)
    return result


def _load_pixels(path):
    try:
        from PIL import Image  # type: ignore
        img = Image.open(path).convert("RGB")
        return img.width, img.height, 3, img.tobytes()
    except Exception:
        pass
    raw = Path(path).read_bytes()
    pos = 8
    width = height = bit_depth = color_type = interlace = 0
    idat = []
    while pos + 8 <= len(raw):
        length = int.from_bytes(raw[pos:pos + 4], "big")
        kind = raw[pos + 4:pos + 8]
        payload = raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filt, interlace = \
                struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            idat.append(payload)
        elif kind == b"IEND":
            break
    if bit_depth != 8 or interlace != 0 or color_type not in (0, 2, 4, 6):
        raise ValueError("unsupported PNG format")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    data = zlib.decompress(b"".join(idat))
    stride = width * channels
    rows = []
    prev = bytearray(stride)
    off = 0
    for _y in range(height):
        ftype = data[off]
        off += 1
        row = bytearray(data[off:off + stride])
        off += stride
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if ftype == 1:
                row[i] = (row[i] + a) & 0xFF
            elif ftype == 2:
                row[i] = (row[i] + b) & 0xFF
            elif ftype == 3:
                row[i] = (row[i] + ((a + b) // 2)) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[i] = (row[i] + pr) & 0xFF
        rows.append(bytes(row))
        prev = row
    if color_type in (4, 6):
        rgb = bytearray()
        for row in rows:
            for i in range(0, len(row), channels):
                rgb.append(row[i])
                rgb.append(row[i + 1])
                rgb.append(row[i + 2])
        return width, height, 3, bytes(rgb)
    return width, height, 1, b"".join(rows)


def pdf_profile(pdf_path):
    path = Path(pdf_path)
    result = {"exists": False, "valid": False, "bytes": 0}
    if not path.is_file():
        return result
    head = path.read_bytes()[:8]
    result["exists"] = True
    result["bytes"] = path.stat().st_size
    result["valid"] = head.startswith(b"%PDF")
    return result


def audit_figure(ledger, spec=None, svg_path=None, pdf_path=None, png_path=None,
                 result=None):
    spec = spec or {}
    result = result if isinstance(result, dict) else {}
    profile = svg_profile(svg_path) if svg_path else svg_profile("")
    png = png_profile(png_path) if png_path else {}
    pdf = pdf_profile(pdf_path) if pdf_path else {}
    comps = ledger.get("compartments") or []
    mols = ledger.get("molecules") or []
    edges = ledger.get("interactions") or []
    spec_edges = {str(e.get("id")): e for e in spec.get("edges") or []}
    groups = profile["groups"]
    expected = ([("compartment", str(c.get("id"))) for c in comps] +
                [("molecule", str(m.get("id"))) for m in mols] +
                [("interaction", str(e.get("id"))) for e in edges])
    mapped = [(kind, eid) for kind, eid in expected if eid in groups]
    coverage = round(len(mapped) / max(1, len(expected)), 4)
    source_ok_count = (sum(1 for c in comps if source_ok(c.get("source"))) +
                       sum(1 for m in mols if source_ok(m.get("source"))) +
                       sum(1 for e in edges if source_ok(e.get("source"))))
    source_coverage = round(source_ok_count / max(1, len(expected)), 4)
    reported = [e for e in edges if str(e.get("kind")) != "inferred" and
                str(e.get("effect")) != "inferred"]
    reported_sourced = sum(1 for e in reported if source_ok(e.get("source")))
    reported_source_coverage = round(
        reported_sourced / max(1, len(reported)), 4) if reported else 1.0

    text_blob = re.sub(r"\s+", "", "".join(profile.get("texts") or []))
    text_present = {}
    for mol in mols:
        label = re.sub(r"\s+", "", str(mol.get("label") or ""))
        text_present[str(mol.get("id"))] = bool(label and label in text_blob)
    for comp in comps:
        label = re.sub(r"\s+", "", str(comp.get("label") or ""))
        text_present[str(comp.get("id"))] = bool(label and label in text_blob)
    missing_text = [eid for eid, present in text_present.items() if not present]

    expected_comp_ids = {str(c.get("id")) for c in comps}
    comp_groups = {gid for gid, info in groups.items()
                   if info.get("role") == "compartment"}
    expected_mol_ids = {str(m.get("id")) for m in mols}
    mol_groups = {gid for gid, info in groups.items() if info.get("shape")}
    expected_edge_ids = {str(e.get("id")) for e in edges}
    edge_groups = {gid for gid in expected_edge_ids if gid in groups}

    style_errors, style_checked = [], 0
    inferred_dashed = True
    inferred_gray = True
    for edge in edges:
        eid = str(edge.get("id"))
        info = groups.get(eid)
        if not info:
            continue
        style_checked += 1
        effect = str(edge.get("effect") or "")
        kind = str(edge.get("kind") or "reported")
        if kind == "inferred" or effect == "inferred":
            base = str(edge.get("base_effect") or "activate")
            has_dash = bool(info.get("dash"))
            markers = set(info.get("markers") or [])
            if not has_dash:
                style_errors.append("%s: inferred edge is not dashed" % eid)
                inferred_dashed = False
            expected_markers = INHIBIT_MARKERS if base == "inhibit" else ARROW_MARKERS
            if not (markers & expected_markers):
                style_errors.append("%s: inferred edge has no polarity marker" % eid)
            gray = False
            for stroke in info.get("strokes") or []:
                if stroke in ("#7a7a7a", "#777777", "#666666", "gray", "grey"):
                    gray = True
                elif re.match(r"^#[0-9a-f]{6}$", stroke):
                    r, g, b = (int(stroke[i:i + 2], 16) for i in (1, 3, 5))
                    if max(r, g, b) - min(r, g, b) <= 24 and max(r, g, b) <= 180:
                        gray = True
            if not gray:
                style_errors.append("%s: inferred edge is not gray" % eid)
                inferred_gray = False
            continue
        dash = bool(info.get("dash"))
        markers = set(info.get("markers") or [])
        if effect == "activate":
            if dash or not (markers & ARROW_MARKERS):
                style_errors.append("%s: activation needs a solid arrow" % eid)
        elif effect == "inhibit":
            if dash or not (markers & INHIBIT_MARKERS):
                style_errors.append("%s: inhibition needs a solid T-bar" % eid)
        elif effect == "bind":
            if markers or dash:
                style_errors.append("%s: binding needs a plain thin line" % eid)
        elif effect == "translocate":
            if not dash or not (markers & ARROW_MARKERS):
                style_errors.append("%s: translocation needs a dashed arrow" % eid)
        elif effect == "modify":
            if dash or not (markers & ARROW_MARKERS):
                style_errors.append("%s: modification needs a solid arrow" % eid)
        elif effect == "association":
            if markers:
                style_errors.append("%s: association must not have an arrowhead" % eid)
        elif effect == "unknown_link":
            if not dash or not (markers & ARROW_MARKERS):
                style_errors.append("%s: unknown_link needs a dashed arrow" % eid)

    expected_effects = {str(spec_edges[eid].get("effect")) for eid in expected_edge_ids
                        if eid in spec_edges}
    legend_effects = set(profile.get("legend_effects") or [])
    legend_complete = expected_effects.issubset(legend_effects)
    if not legend_complete:
        legend_complete = not expected_effects
    layout = spec.get("layout") or {}
    text_fit = bool(layout.get("text_fit", True))
    label_overlap_free = bool(layout.get("label_overlap_free", True))

    errors = []
    if not profile.get("xml_valid"):
        errors.append({"code": "SVG_INVALID",
                       "detail": profile.get("error") or "missing SVG"})
    if profile.get("script"):
        errors.append({"code": "SVG_HAS_SCRIPT"})
    if profile.get("external_refs"):
        errors.append({"code": "SVG_EXTERNAL_REFS",
                       "count": profile["external_refs"]})
    if profile.get("image"):
        errors.append({"code": "SVG_HAS_RASTER_IMAGE"})
    if comp_groups != expected_comp_ids:
        errors.append({"code": "COMPARTMENT_GROUPS",
                       "missing": sorted(expected_comp_ids - comp_groups),
                       "extra": sorted(comp_groups - expected_comp_ids)})
    if not expected_mol_ids.issubset(mol_groups):
        errors.append({"code": "MOLECULE_GROUPS",
                       "missing": sorted(expected_mol_ids - mol_groups)})
    if edge_groups != expected_edge_ids:
        errors.append({"code": "EDGE_GROUPS",
                       "missing": sorted(expected_edge_ids - edge_groups)})
    if coverage < 1.0:
        errors.append({"code": "UNMAPPED_IDS", "coverage": coverage})
    if reported and reported_sourced < len(reported):
        errors.append({"code": "MISSING_SOURCE",
                       "unsourced": [str(e.get("id")) for e in reported
                                     if not source_ok(e.get("source"))]})
    if style_errors:
        errors.append({"code": "EDGE_SEMANTICS", "items": style_errors})
    if not legend_complete:
        errors.append({"code": "LEGEND_INCOMPLETE",
                       "expected": sorted(expected_effects),
                       "legend": sorted(legend_effects)})
    if missing_text:
        errors.append({"code": "TEXT_MISSING", "ids": missing_text})
    if not text_fit:
        errors.append({"code": "TEXT_OVERFLOW"})
    if not label_overlap_free:
        errors.append({"code": "LABEL_OVERLAP"})
    if not pdf.get("valid") and not png.get("valid"):
        errors.append({"code": "EXPORT_INVALID"})
    if not png.get("pixel_reviewed"):
        errors.append({"code": "PIXEL_REVIEW_FAILED",
                       "detail": png.get("error") or
                       "preview is blank, clipped or too uniform"})

    compliance = {
        "xml_valid": bool(profile.get("xml_valid")),
        "text_as_text": profile.get("text_elements", 0) > 0,
        "no_external_refs": not profile.get("external_refs") and
                            not profile.get("script"),
        "viewbox_present": bool(profile.get("viewbox")),
        "named_groups": len(groups),
        "compartment_groups": "%d/%d" % (len(comp_groups & expected_comp_ids),
                                         len(expected_comp_ids)),
        "molecule_groups": "%d/%d" % (len(mol_groups & expected_mol_ids),
                                      len(expected_mol_ids)),
        "edge_groups": "%d/%d" % (len(edge_groups), len(expected_edge_ids)),
        "node_groups": "%d/%d" % (len(mol_groups & expected_mol_ids),
                                  len(expected_mol_ids)),
        "all_reported_edges_sourced": reported_source_coverage == 1.0,
        "reported_edges_sourced": "%d/%d" % (reported_sourced, len(reported)),
        "all_elements_sourced": source_coverage == 1.0,
        "source_coverage": source_coverage,
        "element_coverage": coverage,
        "legend_covers_edge_kinds": bool(legend_complete),
        "edge_semantics_checked": style_checked,
        "edge_semantics_ok": not style_errors,
        "inferred_edges_dashed": bool(inferred_dashed),
        "inferred_edges_gray": bool(inferred_gray),
        "text_fit": text_fit,
        "arrow_clearance": label_overlap_free,
        "text_labels_present": not missing_text,
        "export_artifact_present": bool(pdf.get("valid") or png.get("valid")),
        "pdf_header_valid": bool(pdf.get("valid")),
        "png_header_valid": bool(png.get("valid")),
        "pixel_reviewed": bool(png.get("pixel_reviewed")),
        "preview_size_px": [png.get("width_px", 0), png.get("height_px", 0)],
    }
    return {
        "ok": not errors,
        "errors": errors,
        "coverage": {
            "total": len(expected),
            "mapped": len(mapped),
            "element_coverage": coverage,
            "source_coverage": source_coverage,
            "reported_source_coverage": reported_source_coverage,
        },
        "compliance_checks": compliance,
        "svg": {k: (sorted(v) if isinstance(v, set) else v)
                for k, v in profile.items() if k not in ("texts", "groups")},
        "png": png,
        "pdf": pdf,
        "missing_text": missing_text,
        "style_errors": style_errors,
        "warnings": [],
    }


def main():
    ap = argparse.ArgumentParser(description="Audit pathway figure and exports")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--spec", default=None)
    ap.add_argument("--svg", required=True)
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--png", default=None)
    ap.add_argument("--result", default=None)
    ap.add_argument("--out", default="/app/work/coverage.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    warnings = []
    try:
        ledger = normalize_ledger(load_json(args.ledger), warnings)
        spec = load_json(args.spec) if args.spec and Path(args.spec).is_file() else {}
        result = load_json(args.result) if args.result and Path(args.result).is_file() else {}
        report = audit_figure(ledger, spec, args.svg, args.pdf, args.png, result)
        report["warnings"] = warnings
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    except Exception as exc:  # noqa: BLE001
        report = {"ok": False, "errors": [{"code": "AUDIT_CRASH",
                                           "detail": "%s: %s" %
                                                     (type(exc).__name__, exc)}]}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
