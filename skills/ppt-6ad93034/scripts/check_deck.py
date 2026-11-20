#!/usr/bin/env python3
"""Validate an editable .pptx deck (and optionally its deck-spec / result.json).

The checker never opens PowerPoint and never rewrites the package.  It unpacks
the OOXML zip with the standard library and checks the things that actually
break academic decks:

  * package integrity: zip readable, every .xml/.rels parses, main part exists
  * structure: slide count, slide order through sldIdLst, slide -> layout -> master
  * content: every slide has a title, notes coverage, image references resolve
  * visual risk: shapes outside the canvas, estimated text overflow, overlapping
    text boxes, leftover template strings ("Click to edit title", TODO, ...)
  * deck-spec conformance: slide count, titles, bullets, images, notes, evidence
  * result.json contract: case_id/status/artifacts exist, editable + preview,
    page-level evidence map

Usage
    python3 check_deck.py deck.pptx
    python3 check_deck.py deck.pptx --spec deck-spec.json --expect-slides 8
    python3 check_deck.py deck.pptx --spec deck-spec.json --require-notes \
        --require-evidence --result result.json --root /app/output --json
    python3 check_deck.py deck.pptx --strict      # warnings also fail

Exit codes: 0 pass, 1 problems (or warnings with --strict), 2 unreadable input.
Stdlib only.
"""

import argparse
import json
import math
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EMU = 914400.0
CJK_RE = re.compile(r"[⺀-鿿豈-﫿＀-￯　-〿]")
JUNK_RE = re.compile(
    r"(click to edit|lorem ipsum|\bTODO\b|\bTBD\b|\bFIXME\b|\[insert|"
    r"待补充|待填写|待插入|占位文本|placeholder text)", re.IGNORECASE)
TITLE_PH = {"title", "ctrTitle"}
DECORATIVE_ROLES = {"title", "section", "outline", "closing", "divider", "thanks"}
ALLOWED_STATUS = {"completed", "partial", "insufficient_evidence", "input_error",
                  "failed", "answered", "ok", "success", "done"}


# --------------------------------------------------------------------------
# package helpers
# --------------------------------------------------------------------------
def read_parts(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def parse_xml(raw, label, errors):
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        errors.append("%s: XML parse error: %s" % (label, exc))
        return None


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
    """Resolve a relationship Target relative to the part's folder."""
    target = target.split("#")[0].replace("\\", "/")
    if target.startswith("ppt/"):
        return target
    if target.startswith("/"):
        pieces = []
        target = target.lstrip("/")
    else:
        pieces = [p for p in folder.split("/") if p]
    for piece in target.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if pieces:
                pieces.pop()
        else:
            pieces.append(piece)
    return "/".join(pieces)


def rels_for(parts, part_name):
    folder, fname = part_name.rsplit("/", 1)
    rels_name = "%s/_rels/%s.rels" % (folder, fname)
    raw = parts.get(rels_name)
    if raw is None:
        return {}, None
    root = ET.fromstring(raw)
    out = {}
    for rel in root:
        rid = rel.get("Id")
        out[rid] = {"type": rel.get("Type", ""), "target": rel.get("Target", ""),
                    "external": rel.get("TargetMode", "").lower() == "external"}
    return out, rels_name


def slide_order(parts, errors):
    pres_raw = parts.get("ppt/presentation.xml")
    if pres_raw is None:
        errors.append("missing ppt/presentation.xml")
        return []
    pres = parse_xml(pres_raw, "ppt/presentation.xml", errors)
    if pres is None:
        return []
    rels_name = "ppt/_rels/presentation.xml.rels"
    rels = {}
    if rels_name in parts:
        root = parse_xml(parts[rels_name], rels_name, errors)
        if root is not None:
            for rel in root:
                rels[rel.get("Id")] = rel.get("Target", "")
    order = []
    for sld in pres.iter():
        if local(sld.tag) != "sldId":
            continue
        rid = sld.get("{%s}id" % R_NS)
        target = rels.get(rid, "")
        resolved = resolve_target("ppt", target)
        if resolved and resolved in parts:
            order.append(resolved)
        elif target:
            errors.append("sldIdLst relationship %s -> %s does not exist" % (rid, target))
    if not order:
        numbered = []
        for name in parts:
            m = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            if m:
                numbered.append((int(m.group(1)), name))
        order = [name for _n, name in sorted(numbered)]
    return order


def slide_size(parts, errors):
    raw = parts.get("ppt/presentation.xml")
    if raw is None:
        return None
    m = re.search(rb"<p:sldSz\b([^>]*)/>", raw)
    if not m:
        errors.append("presentation.xml has no sldSz")
        return None
    attrs = dict(re.findall(rb'(\w+)="(\d+)"', m.group(1)))
    if b"cx" not in attrs or b"cy" not in attrs:
        errors.append("presentation.xml sldSz lacks cx/cy")
        return None
    return int(attrs[b"cx"]), int(attrs[b"cy"])


# --------------------------------------------------------------------------
# shape parsing
# --------------------------------------------------------------------------
def _xfrm_values(node):
    if node is None:
        return None
    off, ext = child(node, "off"), child(node, "ext")
    if off is None or ext is None:
        return None
    return (int(off.get("x", 0)), int(off.get("y", 0)),
            int(ext.get("cx", 0)), int(ext.get("cy", 0)))


def _rgba(node):
    if node is None:
        return None
    for item in node.iter():
        if local(item.tag) == "srgbClr":
            return "#" + item.get("val", "").upper()
    return None


def parse_paragraph(p):
    ppr = child(p, "pPr")
    level = int(ppr.get("lvl", "0")) if ppr is not None else 0
    align = ppr.get("algn", "l") if ppr is not None else "l"
    bullet = bool(ppr is not None and (child(ppr, "buChar") is not None
                                       or child(ppr, "buAutoNum") is not None))
    runs, text = [], []
    for item in p:
        if local(item.tag) == "r":
            rpr = child(item, "rPr")
            size = int(rpr.get("sz", "1800")) / 100.0 if rpr is not None else 18.0
            bold = bool(rpr is not None and rpr.get("b") in ("1", "true"))
            color = _rgba(rpr) if rpr is not None else None
            t = child(item, "t")
            piece = t.text if t is not None and t.text else ""
            if piece:
                runs.append({"text": piece, "size": size, "bold": bold, "color": color})
                text.append(piece)
        elif local(item.tag) == "br":
            text.append("\n")
    full = "".join(text)
    return {"text": full, "level": level, "align": align, "bullet": bullet, "runs": runs}


def shape_from_sp(sp, conv):
    nv = child(sp, "nvSpPr")
    cnv = child(nv, "cNvPr") if nv is not None else None
    ph = None
    nvpr = child(nv, "nvPr") if nv is not None else None
    if nvpr is not None:
        ph = child(nvpr, "ph")
    xfrm = _xfrm_values(child(child(sp, "spPr"), "xfrm"))
    if xfrm is not None and conv is not None:
        xfrm = conv(*xfrm)
    tx = child(sp, "txBody")
    paragraphs = [parse_paragraph(p) for p in children(tx, "p")] if tx is not None else []
    text = "\n".join(p["text"] for p in paragraphs if p["text"])
    return {"kind": "text", "name": cnv.get("name", "") if cnv is not None else "",
            "shape_id": cnv.get("id", "") if cnv is not None else "",
            "ph_type": ph.get("type", "body") if ph is not None else None,
            "ph_idx": ph.get("idx") if ph is not None else None,
            "x": xfrm[0] if xfrm else None, "y": xfrm[1] if xfrm else None,
            "cx": xfrm[2] if xfrm else None, "cy": xfrm[3] if xfrm else None,
            "paragraphs": paragraphs, "text": text, "image_rid": None}


def shape_from_pic(pic, conv):
    nv = child(pic, "nvPicPr")
    cnv = child(nv, "cNvPr") if nv is not None else None
    xfrm = _xfrm_values(child(child(pic, "spPr"), "xfrm"))
    if xfrm is not None and conv is not None:
        xfrm = conv(*xfrm)
    blip = None
    for item in pic.iter():
        if local(item.tag) == "blip":
            blip = item
            break
    rid = blip.get("{%s}embed" % R_NS) if blip is not None else None
    return {"kind": "picture", "name": cnv.get("name", "") if cnv is not None else "",
            "shape_id": cnv.get("id", "") if cnv is not None else "",
            "ph_type": None, "ph_idx": None,
            "x": xfrm[0] if xfrm else None, "y": xfrm[1] if xfrm else None,
            "cx": xfrm[2] if xfrm else None, "cy": xfrm[3] if xfrm else None,
            "paragraphs": [], "text": "", "image_rid": rid}


def shape_from_graphic(frame, conv):
    nv = child(frame, "nvGraphicFrame")
    cnv = child(nv, "cNvPr") if nv is not None else None
    xfrm = _xfrm_values(child(frame, "xfrm"))
    if xfrm is not None and conv is not None:
        xfrm = conv(*xfrm)
    texts = [t.text for t in frame.iter() if local(t.tag) == "t" and t.text]
    return {"kind": "graphic", "name": cnv.get("name", "") if cnv is not None else "",
            "shape_id": cnv.get("id", "") if cnv is not None else "",
            "ph_type": None, "ph_idx": None,
            "x": xfrm[0] if xfrm else None, "y": xfrm[1] if xfrm else None,
            "cx": xfrm[2] if xfrm else None, "cy": xfrm[3] if xfrm else None,
            "paragraphs": [], "text": " ".join(texts), "image_rid": None}


def _compose(outer, inner):
    if outer is None:
        return inner
    if inner is None:
        return outer

    def conv(x, y, cx, cy):
        return outer(*inner(x, y, cx, cy))
    return conv


def collect_shapes(tree, conv=None):
    shapes = []
    for node in list(tree):
        tag = local(node.tag)
        if tag == "sp":
            shapes.append(shape_from_sp(node, conv))
        elif tag == "pic":
            shapes.append(shape_from_pic(node, conv))
        elif tag == "graphicFrame":
            shapes.append(shape_from_graphic(node, conv))
        elif tag == "grpSp":
            grp_xfrm = child(child(node, "grpSpPr"), "xfrm")
            inner = None
            if grp_xfrm is not None:
                off = child(grp_xfrm, "off")
                ext = child(grp_xfrm, "ext")
                choff = child(grp_xfrm, "chOff")
                chext = child(grp_xfrm, "chExt")
                if None not in (off, ext, choff, chext):
                    gx, gy = int(off.get("x", 0)), int(off.get("y", 0))
                    gcx, gcy = int(ext.get("cx", 1)), int(ext.get("cy", 1))
                    chx, chy = int(choff.get("x", 0)), int(choff.get("y", 0))
                    chcx = int(chext.get("cx", 1)) or 1
                    chcy = int(chext.get("cy", 1)) or 1

                    def inner(x, y, cx, cy, _v=(gx, gy, gcx, gcy, chx, chy, chcx, chcy)):
                        gx, gy, gcx, gcy, chx, chy, chcx, chcy = _v
                        return (round(gx + (x - chx) * gcx / chcx),
                                round(gy + (y - chy) * gcy / chcy),
                                round(cx * gcx / chcx), round(cy * gcy / chcy))
            shapes.extend(collect_shapes(node, _compose(conv, inner)))
    return shapes


# --------------------------------------------------------------------------
# text metrics
# --------------------------------------------------------------------------
def em_width(text):
    total = 0.0
    for ch in text:
        if CJK_RE.match(ch):
            total += 1.0
        elif ch in "\"'":
            total += 1.0
        elif ch in " \t":
            total += 0.3
        else:
            total += 0.55
    return total


def text_overflow(shape):
    """Return (need_in, box_in, ratio) for the worst paragraph, or None."""
    if shape["kind"] != "text" or shape["cx"] is None or shape["cy"] is None:
        return None
    box_w = shape["cx"] / EMU - 0.20
    box_h = shape["cy"] / EMU - 0.08
    if box_w <= 0.05:
        return (0.0, box_w, 99.0)
    need_w = need_h = 0.0
    worst = None
    for para in shape["paragraphs"]:
        if not para["text"]:
            continue
        size = max([r["size"] for r in para["runs"]] or [18.0])
        width = em_width(para["text"]) * size / 72.0
        lines = max(1, math.ceil(width / box_w - 1e-9))
        need_w = max(need_w, width)
        need_h += lines * size * 1.22 / 72.0
    if need_h <= 0:
        return None
    ratio = need_h / box_h if box_h > 0 else 99.0
    return (round(need_h, 3), round(box_h, 3), round(ratio, 2))


def intersects(a, b):
    if None in (a["x"], a["y"], a["cx"], a["cy"], b["x"], b["y"], b["cx"], b["cy"]):
        return 0.0
    x1 = max(a["x"], b["x"])
    y1 = max(a["y"], b["y"])
    x2 = min(a["x"] + a["cx"], b["x"] + b["cx"])
    y2 = min(a["y"] + a["cy"], b["y"] + b["cy"])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    smaller = min(a["cx"] * a["cy"], b["cx"] * b["cy"]) or 1
    return inter / smaller


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
def check_package(parts, errors, warnings):
    xml_bad = []
    for name in parts:
        if name.endswith((".xml", ".rels")):
            try:
                ET.fromstring(parts[name])
            except ET.ParseError as exc:
                xml_bad.append("%s: %s" % (name, exc))
    if xml_bad:
        errors.extend(xml_bad[:6])
    if "ppt/presentation.xml" not in parts:
        errors.append("missing ppt/presentation.xml")
    if not any(re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", n) for n in parts):
        errors.append("no slide master in package")
    if not any(re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n) for n in parts):
        errors.append("no slide layout in package")


def title_of(shapes):
    for shape in shapes:
        if shape["ph_type"] in TITLE_PH and shape["text"].strip():
            return shape["text"].strip()
    for shape in shapes:
        if "title" in shape["name"].lower() and shape["text"].strip():
            return shape["text"].strip()
    for shape in shapes:
        if shape["kind"] == "text" and shape["text"].strip():
            return shape["text"].strip().splitlines()[0]
    return ""


def check_slide(parts, slide_name, slide_no, size, errors, warnings):
    raw = parts[slide_name]
    root = parse_xml(raw, slide_name, errors)
    row = {"slide": slide_no, "part": slide_name, "title": "", "text_runs": 0,
           "notes_chars": 0, "images": 0, "layout": "", "errors": 0,
           "warnings": 0, "overflow": []}
    if root is None:
        return row, []
    tree = child(child(root, "cSld"), "spTree")
    shapes = collect_shapes(tree) if tree is not None else []
    row["title"] = title_of(shapes)
    row["text_runs"] = sum(len(p["runs"]) for s in shapes for p in s["paragraphs"])
    if not row["title"]:
        errors.append("slide %d: no title text" % slide_no)
        row["errors"] += 1
    if (not any(s["text"].strip() for s in shapes)
            and not any(s["kind"] == "picture" for s in shapes)):
        errors.append("slide %d: empty slide (no text and no picture)" % slide_no)
        row["errors"] += 1

    rels, _rels_name = rels_for(parts, slide_name)
    layout = ""
    for rid, rel in rels.items():
        if rel["type"].endswith("/slideLayout") and not rel["external"]:
            layout = resolve_target(slide_name.rsplit("/", 1)[0], rel["target"])
            break
    row["layout"] = layout
    if not layout:
        errors.append("slide %d: no slideLayout relationship" % slide_no)
        row["errors"] += 1
    elif layout not in parts:
        errors.append("slide %d: layout %s missing from package" % (slide_no, layout))
        row["errors"] += 1

    # notes
    notes_part = None
    for rid, rel in rels.items():
        if rel["type"].endswith("/notesSlide") and not rel["external"]:
            notes_part = resolve_target(slide_name.rsplit("/", 1)[0], rel["target"])
            break
    if notes_part and notes_part in parts:
        nroot = parse_xml(parts[notes_part], notes_part, errors)
        if nroot is not None:
            texts = [t.text for t in nroot.iter() if local(t.tag) == "t" and t.text]
            row["notes_chars"] = len(" ".join(texts).strip())
    elif notes_part:
        errors.append("slide %d: notes part %s missing" % (slide_no, notes_part))
        row["errors"] += 1

    # images
    image_rels = {rid: rel for rid, rel in rels.items()
                  if rel["type"].endswith("/image") and not rel["external"]}
    for shape in shapes:
        if shape["kind"] != "picture":
            continue
        row["images"] += 1
        rid = shape["image_rid"]
        if not rid or rid not in image_rels:
            errors.append("slide %d: picture %r has no image relationship"
                          % (slide_no, shape["name"]))
            row["errors"] += 1
            continue
        target = resolve_target(slide_name.rsplit("/", 1)[0], image_rels[rid]["target"])
        if target not in parts:
            errors.append("slide %d: image target %s missing" % (slide_no, target))
            row["errors"] += 1

    # bounds / overflow / overlap
    slide_cx, slide_cy = size
    bleed = 228600  # 0.25 inch: full-bleed pictures may sit slightly outside
    text_shapes = []
    for shape in shapes:
        if None in (shape["x"], shape["y"], shape["cx"], shape["cy"]):
            continue
        outside = (shape["x"] < -bleed or shape["y"] < -bleed
                   or shape["x"] + shape["cx"] > slide_cx + bleed
                   or shape["y"] + shape["cy"] > slide_cy + bleed)
        if outside:
            name = shape["name"] or shape["shape_id"]
            if shape["kind"] == "text" and shape["text"].strip():
                errors.append("slide %d: text shape %r is outside the slide canvas"
                              % (slide_no, name))
                row["errors"] += 1
            else:
                warnings.append("slide %d: shape %r extends past the canvas (bleed?)"
                                % (slide_no, name))
                row["warnings"] += 1
        if shape["kind"] == "text" and shape["text"].strip():
            text_shapes.append(shape)
            over = text_overflow(shape)
            if over and over[2] > 1.0:
                warnings.append(
                    "slide %d: text overflow risk in %r (need %.2f in, box %.2f in)"
                    % (slide_no, shape["name"] or shape["shape_id"], over[0], over[1]))
                row["warnings"] += 1
                row["overflow"].append({"shape": shape["name"], "need_in": over[0],
                                        "box_in": over[1], "ratio": over[2]})
    for i, a in enumerate(text_shapes):
        for b in text_shapes[i + 1:]:
            if intersects(a, b) >= 0.20:
                warnings.append("slide %d: text boxes %r and %r overlap"
                                % (slide_no, a["name"], b["name"]))
                row["warnings"] += 1

    # placeholder junk
    all_text = "\n".join(shape["text"] for shape in shapes)
    for m in JUNK_RE.finditer(all_text):
        errors.append("slide %d: template/placeholder text %r" % (slide_no, m.group(0)))
        row["errors"] += 1

    return row, shapes


def check_placeholders(parts, slide_shapes, errors, warnings):
    layouts = [n for n in parts if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)]
    used = 0
    for slide_no, shapes in slide_shapes:
        for shape in shapes:
            if not shape["ph_type"]:
                continue
            used += 1
            layout = None
            for cand in layouts:
                root = None
                try:
                    root = ET.fromstring(parts[cand])
                except ET.ParseError:
                    continue
                for node in root.iter():
                    if local(node.tag) != "ph":
                        continue
                    if node.get("idx") == shape["ph_idx"] or node.get("type") == shape["ph_type"]:
                        layout = cand
                        break
                if layout:
                    break
            if layout is None:
                warnings.append("slide %d: placeholder %r (idx=%s) has no layout match"
                                % (slide_no, shape["ph_type"], shape["ph_idx"]))
    return used


def check_spec(spec, rows, deck_aspect, require_evidence, errors, warnings):
    slides = spec.get("slides") or []
    if not slides:
        errors.append("deck-spec has no slides")
        return {"slides": 0}
    if len(slides) != len(rows):
        errors.append("deck-spec slide count %d != deck slide count %d"
                      % (len(slides), len(rows)))
    expected_size = spec.get("size")
    if expected_size and deck_aspect and expected_size != deck_aspect:
        errors.append("deck-spec size %s != deck aspect %s" % (expected_size, deck_aspect))

    def norm(text):
        return re.sub(r"\s+", " ", str(text or "")).strip().casefold()

    for idx, item in enumerate(slides):
        if idx >= len(rows):
            break
        row = rows[idx]
        row_text = norm(row.get("_text", ""))
        have_title = norm(row.get("title", ""))
        want_title = norm(item.get("title"))
        if want_title and want_title not in have_title:
            errors.append("slide %d: title %r not found in deck (found %r)"
                          % (idx + 1, item.get("title"), row.get("title", "")[:60]))
        for bullet in item.get("bullets") or []:
            text = bullet if isinstance(bullet, str) else bullet.get("text", "")
            if text and norm(text) not in row_text:
                errors.append("slide %d: bullet %r not found in deck"
                              % (idx + 1, str(text)[:48]))
        if item.get("image") and row.get("images", 0) < 1:
            errors.append("slide %d: deck-spec expects an image but slide has none"
                          % (idx + 1))
        if item.get("notes") and row.get("notes_chars", 0) < 1:
            errors.append("slide %d: deck-spec has notes but slide notes are empty"
                          % (idx + 1))
        role = str(item.get("kind") or item.get("role") or "content").lower()
        if require_evidence and role not in DECORATIVE_ROLES:
            evidence = item.get("evidence") or []
            if not evidence:
                errors.append("slide %d: content slide has no evidence entry" % (idx + 1))
            for ev in evidence:
                if not isinstance(ev, dict):
                    errors.append("slide %d: evidence entry is not an object" % (idx + 1))
                    continue
                if not str(ev.get("claim", "")).strip():
                    errors.append("slide %d: evidence has no claim text" % (idx + 1))
                source = ev.get("source")
                locator = ev.get("locator")
                if isinstance(source, dict):
                    if not source.get("path") or not source.get("locator"):
                        errors.append("slide %d: evidence source needs path+locator"
                                      % (idx + 1))
                elif not str(source or "").strip() or not str(locator or "").strip():
                    errors.append("slide %d: evidence needs source+locator" % (idx + 1))
    return {"slides": len(slides), "checked": min(len(slides), len(rows))}


def check_result(result_path, deck_path, slide_count, require_evidence, errors, warnings,
                 root=None):
    try:
        result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append("result.json unreadable: %s" % exc)
        return {"ok": False}
    base = Path(root).resolve() if root else Path(result_path).resolve().parent
    if not str(result.get("case_id", "")).strip():
        errors.append("result.json: missing case_id")
    status = result.get("status")
    if not status:
        errors.append("result.json: missing status")
    elif status not in ALLOWED_STATUS:
        warnings.append("result.json: unusual status %r" % status)

    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("result.json: no artifacts declared")
        artifacts = []
    roles, pptx_artifacts = set(), []
    for i, art in enumerate(artifacts):
        if not isinstance(art, dict) or not art.get("path"):
            errors.append("result.json: artifact[%d] has no path" % i)
            continue
        role = str(art.get("role", art.get("kind", ""))).lower()
        roles.add(role)
        rel = Path(str(art["path"]))
        path = rel if rel.is_absolute() else (base / rel)
        try:
            path.resolve().relative_to(base)
        except ValueError:
            errors.append("result.json: artifact %s is outside the output root" % rel)
        if not path.is_file():
            errors.append("result.json: artifact missing on disk: %s" % rel)
        elif path.stat().st_size == 0:
            errors.append("result.json: artifact is empty: %s" % rel)
        if path.suffix.lower() == ".pptx":
            pptx_artifacts.append(path.resolve())
    if pptx_artifacts and "editable" not in roles:
        errors.append("result.json: pptx present but no artifact has role='editable'")
    if pptx_artifacts and not (roles & {"preview", "render", "rendered", "thumbnail"}):
        errors.append("result.json: pptx present but no preview/render artifact declared")
    if deck_path:
        deck_resolved = Path(deck_path).resolve()
        if pptx_artifacts and deck_resolved not in pptx_artifacts:
            warnings.append("result.json: editable artifact does not point at the checked deck")

    evidence = result.get("page_evidence")
    if evidence is None:
        evidence = result.get("slides") if isinstance(result.get("slides"), list) else None
    if require_evidence:
        if not evidence:
            errors.append("result.json: page_evidence is missing")
        else:
            seen = set()
            for i, entry in enumerate(evidence):
                if not isinstance(entry, dict):
                    errors.append("result.json: page_evidence[%d] is not an object" % i)
                    continue
                no = entry.get("slide", entry.get("no"))
                if not isinstance(no, int) or not 1 <= no <= slide_count:
                    errors.append("result.json: page_evidence[%d] has invalid slide number" % i)
                    continue
                seen.add(no)
                role = str(entry.get("kind") or entry.get("role") or "content").lower()
                claims = entry.get("claims") or []
                if role not in DECORATIVE_ROLES and not claims:
                    errors.append("result.json: slide %d has no claims" % no)
                for claim in claims:
                    if not isinstance(claim, dict) or not str(claim.get("claim", "")).strip():
                        errors.append("result.json: slide %d claim text missing" % no)
                        continue
                    source = claim.get("source")
                    locator = claim.get("locator")
                    if isinstance(source, dict):
                        if not source.get("path") or not source.get("locator"):
                            errors.append("result.json: slide %d source needs path+locator" % no)
                    elif not str(source or "").strip() or not str(locator or "").strip():
                        errors.append("result.json: slide %d claim needs source+locator" % no)
            missing = sorted(set(range(1, slide_count + 1)) - seen)
            if missing:
                errors.append("result.json: page_evidence missing slides %s" % missing[:8])
    return {"ok": True, "artifacts": len(artifacts), "roles": sorted(roles),
            "page_evidence": len(evidence or [])}


# --------------------------------------------------------------------------
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
    ap = argparse.ArgumentParser(description="Validate an editable .pptx deck")
    ap.add_argument("deck")
    ap.add_argument("--spec", help="deck-spec.json used to generate/compare")
    ap.add_argument("--result", help="result.json to validate")
    ap.add_argument("--root", help="output root for relative artifact paths")
    ap.add_argument("--expect-slides", type=int)
    ap.add_argument("--expect-aspect", help="16:9 or 4:3")
    ap.add_argument("--require-notes", action="store_true")
    ap.add_argument("--require-images", type=int, default=0)
    ap.add_argument("--require-evidence", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    deck_path = Path(args.deck)
    if not deck_path.is_file():
        print("ERROR: deck not found: %s" % deck_path, file=sys.stderr)
        return 2
    try:
        parts = read_parts(deck_path)
    except (OSError, zipfile.BadZipFile) as exc:
        print("ERROR: not a readable pptx: %s" % exc, file=sys.stderr)
        return 2

    errors, warnings = [], []
    check_package(parts, errors, warnings)
    size = slide_size(parts, errors)
    if size is None:
        size = (12192000, 6858000)
    aspect = "16:9" if abs(size[0] / size[1] - 16 / 9) < 0.02 else (
        "4:3" if abs(size[0] / size[1] - 4 / 3) < 0.02 else "%.3f" % (size[0] / size[1]))
    order = slide_order(parts, errors)
    if args.expect_slides is not None and len(order) != args.expect_slides:
        errors.append("slide count %d != expected %d" % (len(order), args.expect_slides))
    if args.expect_aspect and aspect != args.expect_aspect:
        errors.append("aspect ratio %s != expected %s" % (aspect, args.expect_aspect))

    rows, slide_shapes = [], []
    for i, name in enumerate(order, 1):
        row, shapes = check_slide(parts, name, i, size, errors, warnings)
        row["_text"] = "\n".join(s["text"] for s in shapes)
        rows.append(row)
        slide_shapes.append((i, shapes))

    placeholder_used = check_placeholders(parts, slide_shapes, errors, warnings)
    with_notes = sum(1 for row in rows if row["notes_chars"] > 0)
    if args.require_notes:
        for row in rows:
            if row["notes_chars"] <= 0:
                errors.append("slide %d: speaker notes are empty (--require-notes)"
                              % row["slide"])
    total_images = sum(row["images"] for row in rows)
    if args.require_images and total_images < args.require_images:
        errors.append("image count %d < required %d" % (total_images, args.require_images))

    spec_info = None
    if args.spec:
        try:
            spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print("ERROR: deck-spec unreadable: %s" % exc, file=sys.stderr)
            return 2
        spec_info = check_spec(spec, rows, aspect, args.require_evidence, errors, warnings)

    result_info = None
    if args.result:
        result_info = check_result(args.result, deck_path, len(order),
                                   args.require_evidence, errors, warnings, args.root)

    deliveries = [{"slide": row["slide"], "title": row["title"],
                   "notes_chars": row["notes_chars"], "images": row["images"],
                   "layout": row["layout"], "text_runs": row["text_runs"]}
                  for row in rows]
    report = {
        "ok": not errors and not (args.strict and warnings),
        "deck": {"path": str(deck_path), "slide_count": len(order),
                 "aspect_ratio": aspect, "size_emu": {"cx": size[0], "cy": size[1]},
                 "bytes": deck_path.stat().st_size},
        "stats": {"slides": len(order), "slides_with_notes": with_notes,
                  "images": total_images, "placeholders_used": placeholder_used,
                  "layouts": len([n for n in parts
                                  if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n)]),
                  "masters": len([n for n in parts
                                  if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", n)])},
        "slides": deliveries,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "package_readable": "ppt/presentation.xml" in parts,
            "slide_count_match": args.expect_slides is None or len(order) == args.expect_slides,
            "notes_complete": (not args.require_notes) or with_notes == len(order),
            "images_ok": not any("image" in item for item in errors),
            "spec_checked": args.spec is not None,
            "result_checked": args.result is not None,
        },
        "spec": spec_info,
        "result": result_info,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    if args.json:
        print(text)
    else:
        print("deck: %s (%d slides, %s)" % (deck_path, len(order), aspect))
        for row in deliveries:
            mark = "!" if row["notes_chars"] == 0 else " "
            print("  %s %2d  notes=%-4d img=%d  %s"
                  % (mark, row["slide"], row["notes_chars"], row["images"],
                     row["title"][:60]))
        for item in errors:
            print("  ERROR   " + item)
        for item in warnings:
            print("  WARNING " + item)
        print("RESULT: %s (%d errors, %d warnings)"
              % ("PASS" if report["ok"] else "FAIL", len(errors), len(warnings)))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
