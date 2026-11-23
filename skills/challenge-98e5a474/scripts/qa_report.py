#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit a schematic's real exports before they are handed in.

Checks are deliberately factual: PNG signature/dimensions/pHYs DPI, PDF
signature/font embedding/page boxes, SVG text/group/viewBox structure, and
whether the declared relation semantics and legend entries agree.  A missing
artifact is reported as ``null`` plus a warning; nothing is invented.

Usage:
    python3 qa_report.py --svg figures/fig1.svg --png preview/fig1.png \
        --pdf figures/fig1.pdf --entities work/entities.json \
        --out output/qa-report.json --json

Exit 0 = report written (warnings allowed), 2 = unreadable input.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG_NS = "{http://www.w3.org/2000/svg}"


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {} if default is None else default


def png_info(path):
    info = {"path": str(path), "exists": Path(path).is_file(), "ok": False,
            "width": None, "height": None, "dpi": None, "color_type": None}
    if not info["exists"]:
        return info
    raw = Path(path).read_bytes()
    info["bytes"] = len(raw)
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        info["error"] = "not a PNG signature"
        return info
    info["ok"] = True
    pos = 8
    while pos + 8 <= len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + length]
        if kind == b"IHDR" and len(body) >= 13:
            width, height, bit_depth, color_type = struct.unpack(">IIBB", body[:10])
            info.update(width=width, height=height, bit_depth=bit_depth,
                        color_type=color_type)
        elif kind == b"pHYs" and len(body) >= 9:
            x, y, unit = struct.unpack(">IIB", body[:9])
            if unit == 1:
                info["dpi"] = round((x + y) / 2.0 * 0.0254, 1)
                info["dpi_xy"] = [round(x * 0.0254, 1), round(y * 0.0254, 1)]
        elif kind == b"IEND":
            break
        pos += 12 + length
    return info


def pdf_info(path):
    info = {"path": str(path), "exists": Path(path).is_file(), "ok": False,
            "pages": None, "fonts_embedded": None, "has_media_box": None}
    if not info["exists"]:
        return info
    raw = Path(path).read_bytes()
    info["bytes"] = len(raw)
    if not raw.startswith(b"%PDF"):
        info["error"] = "not a PDF signature"
        return info
    info["ok"] = True
    text = raw.decode("latin-1", "replace")
    info["pages"] = len(re.findall(r"/Type\s*/Page\b", text))
    embedded = len(re.findall(r"/FontFile[23]?\b", text))
    info["font_files"] = embedded
    info["fonts_embedded"] = embedded > 0
    info["has_media_box"] = bool(re.search(r"/MediaBox\s*\[", text))
    return info


def svg_info(path):
    info = {"path": str(path), "exists": Path(path).is_file(), "ok": False,
            "texts": 0, "groups": 0, "viewBox": None, "fonts": [],
            "legend_items": [], "scripts": 0, "external_refs": 0}
    if not info["exists"]:
        return info
    try:
        root = ET.fromstring(Path(path).read_text(encoding="utf-8", errors="replace"))
    except ET.ParseError as exc:
        info["error"] = "XML parse error: %s" % exc
        return info
    info["ok"] = True
    if root.tag != SVG_NS + "svg":
        info["error"] = "root is not <svg>"
    info["viewBox"] = root.attrib.get("viewBox")
    info["texts"] = sum(1 for el in root.iter() if el.tag == SVG_NS + "text")
    groups = [el for el in root.iter() if el.tag == SVG_NS + "g"]
    info["groups"] = sum(1 for el in groups if el.attrib.get("id"))
    info["scripts"] = sum(1 for el in root.iter() if el.tag == SVG_NS + "script")
    info["external_refs"] = len(re.findall(
        r'(?:href|xlink:href)="(?!data:|#)[^"]+"',
        Path(path).read_text(encoding="utf-8", errors="replace")))
    fonts = set()
    for el in root.iter():
        family = el.attrib.get("font-family")
        if family:
            fonts.add(family)
    info["fonts"] = sorted(fonts)
    for g in groups:
        if g.attrib.get("id") == "legend" or g.attrib.get("data-role") == "legend":
            for child in list(g):
                label = child.attrib.get("data-label") or child.attrib.get("data-kind")
                if label:
                    info["legend_items"].append(label)
    return info


def semantics_of(entities):
    sem = set()
    for rel in (entities.get("relations") or entities.get("edges") or []):
        if isinstance(rel, dict):
            sem.add(str(rel.get("semantic") or rel.get("kind") or "default"))
    return sem


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg")
    ap.add_argument("--png")
    ap.add_argument("--pdf")
    ap.add_argument("--entities")
    ap.add_argument("--out")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    warnings, errors = [], []
    svg = svg_info(args.svg) if args.svg else None
    png = png_info(args.png) if args.png else None
    pdf = pdf_info(args.pdf) if args.pdf else None
    entities = read_json(args.entities, {}) if args.entities else {}

    if svg:
        if not svg["exists"]:
            errors.append("svg_missing")
        elif not svg["ok"]:
            errors.append("svg_unreadable")
        else:
            if not svg["viewBox"]:
                errors.append("svg_no_viewbox")
            if not svg["texts"]:
                errors.append("svg_no_text")
            if not svg["groups"]:
                warnings.append("svg_no_named_groups")
            if svg["scripts"]:
                errors.append("svg_has_script")
            if svg["external_refs"]:
                errors.append("svg_external_reference")
    if png:
        if not png["exists"]:
            warnings.append("png_missing")
        elif not png["ok"]:
            errors.append("png_invalid")
        elif not png.get("width") or not png.get("height"):
            errors.append("png_no_ihdr")
        if png.get("ok") and png.get("dpi") is None:
            warnings.append("png_no_phys_dpi")
    if pdf:
        if not pdf["exists"]:
            warnings.append("pdf_missing")
        elif not pdf["ok"]:
            errors.append("pdf_invalid")
        elif not pdf.get("pages"):
            warnings.append("pdf_no_page_objects")
        if pdf.get("ok") and pdf.get("fonts_embedded") is False:
            warnings.append("pdf_fonts_not_embedded")
    if entities:
        sem = semantics_of(entities)
        legend = set((svg or {}).get("legend_items") or [])
        missing = sorted(sem - legend) if legend else sorted(sem)
        if missing:
            warnings.append("legend_missing_semantics:" + ",".join(missing))

    status = "complete"
    if errors or (png and not png.get("exists")) or (pdf and not pdf.get("exists")):
        status = "partial"
    report = {
        "ok": not errors,
        "status": status,
        "svg": svg,
        "png": png,
        "pdf": pdf,
        "checks": {
            "svg_editable_text": bool(svg and svg.get("texts")),
            "svg_named_groups": bool(svg and svg.get("groups")),
            "png_signature": bool(png and png.get("ok")),
            "png_dpi_at_least_300": bool(png and (png.get("dpi") or 0) >= 300),
            "pdf_signature": bool(pdf and pdf.get("ok")),
            "pdf_fonts_embedded": bool(pdf and pdf.get("fonts_embedded")),
            "legend_covers_semantics": "legend_missing_semantics" not in " ".join(warnings),
        },
        "warnings": warnings,
        "errors": errors,
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
