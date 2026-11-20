#!/usr/bin/env python3
"""Turn a .pptx into a browser-renderable geometry preview (stdlib only).

This is NOT a PowerPoint/LibreOffice renderer.  It reads the actual OOXML in the
package (slide size, shape coordinates, text runs, font sizes, image bytes) and
lays every element out at its real position on an HTML canvas.  The result is a
faithful geometry/text preview that exposes overflow, overlap, clipped images
and empty regions when opened in a real browser (Chrome headless screenshot).
Font shapes and kerning are approximate; use `office-renderer` when it is
available if pixel-accurate output is required.

Usage
    python3 pptx_preview.py deck.pptx --out preview/deck.html
    python3 pptx_preview.py deck.pptx --out preview/slide-03.html --slide 3
    python3 pptx_preview.py deck.pptx --out preview/deck.html --width 1600 --notes
    python3 pptx_preview.py deck.pptx --out preview/deck.html --json

Exit codes: 0 ok, 2 unreadable deck.
"""

import argparse
import base64
import html
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EMU = 914400.0
DEFAULT_SIZE = (12192000, 6858000)


def read_parts(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def local(tag):
    return tag.rsplit("}", 1)[-1]


def child(node, name):
    if node is None:
        return None
    for item in node:
        if local(item.tag) == name:
            return item
    return None


def children(node, name):
    if node is None:
        return []
    return [item for item in node if local(item.tag) == name]


def resolve_target(folder, target):
    target = target.split("#")[0].replace("\\", "/")
    if target.startswith("ppt/"):
        return target
    pieces = [] if target.startswith("/") else [p for p in folder.split("/") if p]
    for piece in target.lstrip("/").split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if pieces:
                pieces.pop()
        else:
            pieces.append(piece)
    return "/".join(pieces)


def slide_order(parts):
    pres = parts.get("ppt/presentation.xml")
    rels_raw = parts.get("ppt/_rels/presentation.xml.rels")
    if pres is None:
        return []
    root = ET.fromstring(pres)
    rels = {}
    if rels_raw:
        for rel in ET.fromstring(rels_raw):
            rels[rel.get("Id")] = rel.get("Target", "")
    order = []
    for node in root.iter():
        if local(node.tag) != "sldId":
            continue
        rid = node.get("{%s}id" % R_NS)
        target = resolve_target("ppt", rels.get(rid, ""))
        if target in parts:
            order.append(target)
    if not order:
        numbered = []
        for name in parts:
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            if m:
                numbered.append((int(m.group(1)), name))
        order = [name for _n, name in sorted(numbered)]
    return order


def slide_size(parts):
    raw = parts.get("ppt/presentation.xml", b"")
    m = re.search(rb"<p:sldSz\b([^>]*)/>", raw)
    if not m:
        return DEFAULT_SIZE
    attrs = dict(re.findall(rb'(\w+)="(\d+)"', m.group(1)))
    if b"cx" in attrs and b"cy" in attrs:
        return int(attrs[b"cx"]), int(attrs[b"cy"])
    return DEFAULT_SIZE


def xfrm_values(node):
    if node is None:
        return None
    off, ext = child(node, "off"), child(node, "ext")
    if off is None or ext is None:
        return None
    return (int(off.get("x", 0)), int(off.get("y", 0)),
            int(ext.get("cx", 0)), int(ext.get("cy", 0)))


def compose(outer, inner):
    if outer is None:
        return inner
    if inner is None:
        return outer
    return lambda x, y, cx, cy: outer(*inner(x, y, cx, cy))


def solid_color(node):
    if node is None:
        return None
    for item in node.iter():
        if local(item.tag) == "srgbClr" and item.get("val"):
            return "#" + item.get("val").upper()
    return None


def parse_paragraph(p):
    ppr = child(p, "pPr")
    align = ppr.get("algn", "l") if ppr is not None else "l"
    bullet = bool(ppr is not None and (child(ppr, "buChar") is not None
                                       or child(ppr, "buAutoNum") is not None))
    pieces, size, bold, color = [], 18.0, False, None
    for item in p:
        if local(item.tag) == "r":
            rpr = child(item, "rPr")
            if rpr is not None:
                size = int(rpr.get("sz", "1800")) / 100.0
                bold = bold or rpr.get("b") in ("1", "true")
                color = solid_color(rpr) or color
            t = child(item, "t")
            if t is not None and t.text:
                pieces.append(t.text)
        elif local(item.tag) == "br":
            pieces.append("\n")
    return {"text": "".join(pieces), "align": align, "bullet": bullet,
            "size": size, "bold": bold, "color": color}


def parse_text(sp):
    tx = child(sp, "txBody")
    paras = [parse_paragraph(p) for p in children(tx, "p")] if tx is not None else []
    body = child(tx, "bodyPr") if tx is not None else None
    anchor = body.get("anchor", "t") if body is not None else "t"
    return paras, anchor


def parse_shape(node, conv):
    tag = local(node.tag)
    if tag == "grpSp":
        gx = child(child(node, "grpSpPr"), "xfrm")
        inner = None
        if gx is not None:
            off, ext = child(gx, "off"), child(gx, "ext")
            choff, chext = child(gx, "chOff"), child(gx, "chExt")
            if None not in (off, ext, choff, chext):
                gx0, gy0 = int(off.get("x", 0)), int(off.get("y", 0))
                gcx, gcy = int(ext.get("cx", 1)), int(ext.get("cy", 1))
                chx, chy = int(choff.get("x", 0)), int(choff.get("y", 0))
                chcx = int(chext.get("cx", 1)) or 1
                chcy = int(chext.get("cy", 1)) or 1

                def inner(x, y, cx, cy, _v=(gx0, gy0, gcx, gcy, chx, chy, chcx, chcy)):
                    gx0, gy0, gcx, gcy, chx, chy, chcx, chcy = _v
                    return (round(gx0 + (x - chx) * gcx / chcx),
                            round(gy0 + (y - chy) * gcy / chcy),
                            round(cx * gcx / chcx), round(cy * gcy / chcy))
        out = []
        for sub in node:
            out.extend(parse_shape(sub, compose(conv, inner)))
        return out

    if tag not in ("sp", "pic", "graphicFrame"):
        return []
    nv_tag = {"sp": "nvSpPr", "pic": "nvPicPr", "graphicFrame": "nvGraphicFrame"}[tag]
    nv = child(node, nv_tag)
    cnv = child(nv, "cNvPr") if nv is not None else None
    xfrm = xfrm_values(child(child(node, "spPr"), "xfrm"))
    if xfrm is None and tag == "graphicFrame":
        xfrm = xfrm_values(child(node, "xfrm"))
    if xfrm is not None and conv is not None:
        xfrm = conv(*xfrm)
    shape = {"kind": tag, "name": cnv.get("name", "") if cnv is not None else "",
             "x": xfrm[0] if xfrm else 0, "y": xfrm[1] if xfrm else 0,
             "cx": xfrm[2] if xfrm else 0, "cy": xfrm[3] if xfrm else 0,
             "paragraphs": [], "anchor": "t", "rid": None}
    if tag == "sp":
        shape["paragraphs"], shape["anchor"] = parse_text(node)
    elif tag == "pic":
        for item in node.iter():
            if local(item.tag) == "blip":
                shape["rid"] = item.get("{%s}embed" % R_NS)
                break
    elif tag == "graphicFrame":
        texts = [t.text for t in node.iter() if local(t.tag) == "t" and t.text]
        if texts:
            shape["paragraphs"] = [{"text": " ".join(texts), "align": "l",
                                    "bullet": False, "size": 14.0,
                                    "bold": False, "color": None}]
    return [shape]


def collect_shapes(root):
    tree = child(child(root, "cSld"), "spTree")
    if tree is None:
        return []
    out = []
    for node in tree:
        out.extend(parse_shape(node, None))
    return out


def rel_map(parts, slide_name):
    folder, fname = slide_name.rsplit("/", 1)
    rels_name = "%s/_rels/%s.rels" % (folder, fname)
    out = {}
    if rels_name in parts:
        for rel in ET.fromstring(parts[rels_name]):
            out[rel.get("Id")] = resolve_target(folder, rel.get("Target", ""))
    return out


def notes_text(parts, slide_name):
    rels = rel_map(parts, slide_name)
    folder, fname = slide_name.rsplit("/", 1)
    rels_name = "%s/_rels/%s.rels" % (folder, fname)
    if rels_name not in parts:
        return ""
    for rel in ET.fromstring(parts[rels_name]):
        if rel.get("Type", "").endswith("/notesSlide"):
            target = resolve_target(folder, rel.get("Target", ""))
            if target in parts:
                root = ET.fromstring(parts[target])
                texts = [t.text for t in root.iter() if local(t.tag) == "t" and t.text]
                return " ".join(texts).strip()
    return ""


def mime_for(path):
    ext = path.rsplit(".", 1)[-1].lower()
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "bmp": "image/bmp"}.get(ext, "application/octet-stream")


def image_data_uri(parts, target):
    raw = parts.get(target)
    if raw is None:
        return None
    return "data:%s;base64,%s" % (mime_for(target), base64.b64encode(raw).decode("ascii"))


def render_slide(parts, slide_name, width):
    root = ET.fromstring(parts[slide_name])
    slide_cx, slide_cy = slide_size(parts)
    scale = width / slide_cx
    height = int(round(slide_cy * scale))
    px_per_in = width / (slide_cx / EMU)
    shapes = collect_shapes(root)
    rels = rel_map(parts, slide_name)
    blocks, images = [], 0

    for shape in shapes:
        left = shape["x"] * scale
        top = shape["y"] * scale
        w = shape["cx"] * scale
        h = shape["cy"] * scale
        style = ("left:%.1fpx;top:%.1fpx;width:%.1fpx;height:%.1fpx"
                 % (left, top, max(w, 1), max(h, 1)))
        if shape["kind"] == "pic":
            uri = image_data_uri(parts, rels.get(shape["rid"], ""))
            if uri:
                images += 1
                blocks.append('<img class="shape pic" style="%s" src="%s" alt="">'
                              % (style, uri))
            else:
                blocks.append('<div class="shape missing" style="%s">missing image</div>'
                              % style)
            continue
        paras = [p for p in shape["paragraphs"] if p["text"]]
        if not paras:
            continue
        justify = {"t": "flex-start", "ctr": "center", "b": "flex-end"}.get(
            shape["anchor"], "flex-start")
        color = next((p["color"] for p in paras if p["color"]), "#222222")
        weight = "700" if any(p["bold"] for p in paras) else "400"
        pieces = []
        for para in paras:
            font_px = para["size"] * px_per_in / 72.0
            align = {"ctr": "center", "r": "right", "just": "justify"}.get(
                para["align"], "left")
            text = html.escape(para["text"]).replace("\n", "<br>")
            if para["bullet"]:
                text = "• " + text
                pieces.append('<div style="font-size:%.1fpx;text-align:%s;'
                              'padding-left:1.1em;text-indent:-1.1em;'
                              'line-height:1.22">%s</div>' % (font_px, align, text))
            else:
                pieces.append('<div style="font-size:%.1fpx;text-align:%s;'
                              'line-height:1.22">%s</div>' % (font_px, align, text))
        blocks.append('<div class="shape text" style="%s;color:%s;font-weight:%s;'
                      'display:flex;flex-direction:column;justify-content:%s">%s</div>'
                      % (style, color, weight, justify, "".join(pieces)))

    title = ""
    for shape in shapes:
        if shape["kind"] == "sp" and shape["paragraphs"]:
            title = next((p["text"] for p in shape["paragraphs"] if p["text"]), "")
            if title:
                break
    notes = notes_text(parts, slide_name)
    return {"html": '<section class="slide" data-slide="%s">%s</section>'
                    % (html.escape(slide_name), "".join(blocks)),
            "width": width, "height": height, "title": title,
            "notes": notes, "images": images,
            "shapes": len(shapes)}


def build_html(deck_path, parts, width, only_slide=None, with_notes=False):
    order = slide_order(parts)
    if only_slide is not None:
        if not 1 <= only_slide <= len(order):
            raise ValueError("slide %d out of range 1..%d" % (only_slide, len(order)))
        order = [order[only_slide - 1]]
    rendered = []
    for i, name in enumerate(order, 1):
        info = render_slide(parts, name, width)
        rendered.append(info)
    first = rendered[0] if rendered else {"height": int(width * 9 / 16)}
    slide_h = first["height"]
    body = []
    for info in rendered:
        body.append(info["html"])
        if with_notes and info["notes"]:
            body.append('<div class="notes" style="width:%dpx">讲稿：%s</div>'
                        % (width, html.escape(info["notes"])))
    margin = 0 if only_slide is not None else 24
    doc = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>%s</title>
<style>
  html,body{margin:0;padding:0;background:%s;}
  body{font-family:"Microsoft YaHei","Segoe UI",Arial,sans-serif;}
  .slide{position:relative;width:%dpx;height:%dpx;background:#fff;overflow:hidden;
         margin:%dpx auto;box-shadow:%s;}
  .shape{position:absolute;box-sizing:border-box;overflow:hidden;white-space:pre-wrap;
         word-break:break-word;}
  .text{padding:2px 4px;}
  .pic{object-fit:fill;}
  .missing{border:1px dashed #d00;color:#d00;font-size:14px;padding:6px;}
  .notes{background:#fff;color:#333;font-size:14px;line-height:1.55;
         margin:0 auto 24px;padding:12px 16px;box-shadow:0 1px 4px rgba(0,0,0,.12);}
</style></head><body>
%s
</body></html>""" % (html.escape(deck_path.name),
                     "#e9edf2" if only_slide is None else "#ffffff",
                     width, slide_h, margin,
                     "0 2px 12px rgba(0,0,0,.18)" if only_slide is None else "none",
                     "\n".join(body))
    return doc, rendered, slide_h


def _utf8_stdout():
    """Keep local Windows consoles from crashing on non-GBK glyphs."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except (AttributeError, ValueError):
                pass


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser(description="PPTX -> HTML geometry preview")
    ap.add_argument("deck")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1200)
    ap.add_argument("--slide", type=int)
    ap.add_argument("--notes", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    deck = Path(args.deck)
    if not deck.is_file():
        print("ERROR: deck not found: %s" % deck, file=sys.stderr)
        return 2
    try:
        parts = read_parts(deck)
    except (OSError, zipfile.BadZipFile) as exc:
        print("ERROR: not a readable pptx: %s" % exc, file=sys.stderr)
        return 2
    try:
        doc, rendered, slide_h = build_html(deck, parts, args.width,
                                            args.slide, args.notes)
    except ValueError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    report = {"ok": True, "out": str(out), "width": args.width, "height": slide_h,
              "slides": len(rendered), "slide": args.slide,
              "images_embedded": sum(r["images"] for r in rendered),
              "titles": [r["title"] for r in rendered],
              "note": "HTML geometry preview from OOXML; not office-renderer output"}
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
          "wrote %s (%d slides, %dx%d)" % (out, len(rendered), args.width, slide_h))
    return 0


if __name__ == "__main__":
    sys.exit(main())
