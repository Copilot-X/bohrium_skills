#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract a content deck-spec from a PPTX and render a real PDF preview.

This is the fallback for edit / template-adaptation cases where the starting
deck's geometry is not known ahead of time.  It reads the OOXML package with
the standard library, preserves slide order and text, extracts images, and
hands the resulting spec to deck_pdf.py.  It does not use python-pptx and it
does not claim to reproduce every template ornament pixel-for-pixel.

Usage:
    python3 pptx_to_preview.py --pptx /app/output/presentation.pptx \
        --out /app/output/preview/presentation.pdf \
        --png-dir /app/output/preview --json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"p": P_NS, "a": A_NS, "r": R_NS}
REL_RE = re.compile(r"<Relationship\b([^>]*)/>")
T_RE = re.compile(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.S)
SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
SZ_RE = re.compile(r"<p:sldSz\b([^>]*)/>")


def emit(data, code=0):
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return code


def parts_of(path):
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def resolve(folder, target):
    if target.startswith("/"):
        return target.lstrip("/")
    stack = [p for p in folder.split("/") if p]
    for piece in target.replace("\\", "/").split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if stack:
                stack.pop()
        else:
            stack.append(piece)
    return "/".join(stack)


def rels(parts, part):
    folder, name = part.rsplit("/", 1)
    raw = parts.get(f"{folder}/_rels/{name}.rels", b"").decode("utf-8", "replace")
    out = {}
    for m in REL_RE.finditer(raw):
        attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
        if attrs.get("Id"):
            out[attrs["Id"]] = attrs
    return out


def ordered_slides(parts):
    pres = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    ids = re.findall(r'<p:sldId\b[^>]*r:id="([^"]+)"', pres)
    rel = rels(parts, "ppt/presentation.xml")
    out = []
    for rid in ids:
        target = rel.get(rid, {}).get("Target", "")
        if target:
            part = resolve("ppt", target)
            if part in parts:
                out.append(part)
    if not out:
        out = [n for _, n in sorted((int(SLIDE_RE.match(n).group(1)), n)
                                    for n in parts if SLIDE_RE.match(n))]
    return out


def slide_size(parts):
    raw = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    m = SZ_RE.search(raw)
    if not m:
        return "16:9"
    attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
    cx = int(attrs.get("cx", 12192000))
    cy = int(attrs.get("cy", 6858000))
    return "4:3" if abs(cx / cy - 4 / 3) < 0.03 else "16:9"


def text_shapes(raw):
    """Return [(text, font_size, is_title, is_bullet)] in document order."""
    # A light-weight XML parse keeps order and placeholder type without
    # rebuilding the whole DrawingML model.
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out = []
    for sp in root.iter():
        if sp.tag.rsplit("}", 1)[-1] != "sp":
            continue
        paragraphs = []
        for para in sp.iter():
            if para.tag.rsplit("}", 1)[-1] != "p":
                continue
            text = "".join("".join(t.itertext()) for t in para.iter()
                           if t.tag.rsplit("}", 1)[-1] == "t")
            if text.strip():
                paragraphs.append(text.strip())
        text = "\n".join(paragraphs)
        if not text.strip():
            continue
        title = False
        for ph in sp.iter():
            if ph.tag.rsplit("}", 1)[-1] == "ph":
                typ = ph.get("type", "")
                if typ in ("title", "ctrTitle"):
                    title = True
        sizes = []
        for rpr in sp.iter():
            if rpr.tag.rsplit("}", 1)[-1] == "rPr" and rpr.get("sz"):
                try:
                    sizes.append(int(rpr.get("sz")) / 100.0)
                except ValueError:
                    pass
        size = max(sizes) if sizes else 18.0
        out.append((text.strip(), size, title))
    return out


def extract_images(parts, slide_part, media_dir):
    out = []
    rel = rels(parts, slide_part)
    for rid, attrs in rel.items():
        if not attrs.get("Type", "").endswith("/image"):
            continue
        target = attrs.get("Target", "")
        if not target:
            continue
        part = resolve(slide_part.rsplit("/", 1)[0], target)
        if part not in parts:
            continue
        media_dir.mkdir(parents=True, exist_ok=True)
        dst = media_dir / Path(part).name
        if not dst.exists():
            dst.write_bytes(parts[part])
        out.append(str(dst))
    return out


def build_spec(pptx, media_dir=None):
    parts = parts_of(pptx)
    slides = []
    for idx, part in enumerate(ordered_slides(parts), 1):
        raw = parts[part]
        shapes = text_shapes(raw)
        title = ""
        rest = []
        for text, size, is_title in shapes:
            if is_title and not title:
                title = text
        if not title:
            # largest font is the title; ties break by document order
            best = max(shapes, key=lambda x: x[1]) if shapes else ("", 18, False)
            title = best[0]
        for text, size, is_title in shapes:
            if text != title:
                for line in text.split("\n"):
                    clean = line.strip()
                    if clean and not (clean.isdigit() and len(clean) <= 3):
                        rest.append(clean)
        images = extract_images(parts, part, media_dir) if media_dir else []
        if idx == 1:
            slides.append({"layout": "title", "title": title,
                           "subtitle": rest[0] if rest else "",
                           "source": "", "notes": ""})
        else:
            slides.append({"layout": "content", "title": title,
                           "bullets": rest, "image": images[0] if images else "",
                           "source": "", "notes": ""})
    return {
        "title": slides[0]["title"] if slides else "",
        "size": slide_size(parts),
        "theme": "scholar",
        "slides": slides,
    }


def main():
    ap = argparse.ArgumentParser(description="PPTX -> deck-spec + PDF preview")
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--png-dir", default=None)
    ap.add_argument("--spec-out", default=None)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    pptx = Path(args.pptx)
    if not pptx.is_file():
        return emit({"ok": False, "error": "PPTX not found: %s" % pptx}, 2)
    media_dir = (Path(args.png_dir) / "media") if args.png_dir else (Path(args.out).parent / "media")
    try:
        spec = build_spec(pptx, media_dir)
    except (OSError, zipfile.BadZipFile) as exc:
        return emit({"ok": False, "error": "cannot read PPTX: %s" % exc}, 2)
    spec_path = Path(args.spec_out) if args.spec_out else Path(args.out).with_suffix(".spec.json")
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    if importlib.util.find_spec("deck_pdf") is None:
        # When this file is run directly, Python puts its own directory on
        # sys.path; this branch only triggers if the module was copied alone.
        candidate = Path(__file__).with_name("deck_pdf.py")
        if candidate.is_file():
            import importlib.util as iu
            mod_spec = iu.spec_from_file_location("deck_pdf", candidate)
            mod = iu.module_from_spec(mod_spec)
            mod_spec.loader.exec_module(mod)
        else:
            return emit({"ok": False, "error": "deck_pdf.py not found"}, 1)
    else:
        import importlib
        mod = importlib.import_module("deck_pdf")
    ok, detail = mod.render(spec, args.out, args.png_dir, args.dpi)
    if ok:
        return emit({"ok": True, "pptx": str(pptx), "spec": str(spec_path), **detail})
    return emit({"ok": False, "pptx": str(pptx), "spec": str(spec_path),
                 "error": detail,
                 "exceptions": [{"code": "render_unavailable", "detail": str(detail)}]}, 1)


if __name__ == "__main__":
    raise SystemExit(main())
