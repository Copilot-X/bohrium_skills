#!/usr/bin/env python3
"""Adapt academic content to a real PowerPoint template (stdlib only).

Two modes:

1. Build from content spec (default):
       adapt_deck.py --template template.pptx --spec deck-spec.json \
                     --format-spec ppt-format-spec.json --out deck.pptx

   The template package is copied as-is (master, layouts, theme, media), the
   template's sample slides are removed, and one new slide per spec entry is
   created from a *real* layout. Text is written into layout placeholders, so
   theme fonts/colours and layout geometry stay inherited. Images are inserted
   with aspect ratio preserved. Speaker notes are written when the template has
   a notes master.

2. Retarget an existing deck:
       adapt_deck.py --deck starting.pptx --template template.pptx \
                     --layout-map layout-map.json --out deck.pptx

   Every slide keeps its content untouched; only its slideLayout relationship
   and the package-level master/layout/theme are replaced with the template's.
   The layout map is {"slides": [{"slide": 1, "role": "title",
   "layout": "Title Slide"}, ...]}.

Both modes write a machine-readable report (--report). Exit codes:
0 = adapted and package validates, 1 = adaptation failed, 2 = unreadable input.
"""
import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import xml.etree.ElementTree as ET

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"p": P_NS, "a": A_NS, "r": R_NS}
EMU_IN = 914400
EMU_PT = 12700

SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
LAYOUT_RE = re.compile(r"^ppt/slideLayouts/slideLayout(\d+)\.xml$")
NOTES_RE = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
MEDIA_RE = re.compile(r"^ppt/media/(.+)$")
REL_RE = re.compile(r"<Relationship\b([^>]*)/>")
T_RE = re.compile(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.S)

CONTROL_PH = {"dt", "ftr", "sldNum", "hdr", "sldImg"}
BODY_TYPES = {"body", "obj"}


# ---------------------------------------------------------------------------
# package helpers
# ---------------------------------------------------------------------------
def read_parts(path):
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def write_parts(parts, out_path, original_order=None):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    order = ["[Content_Types].xml"]
    for n in original_order or []:
        if n in parts and n not in order:
            order.append(n)
    for n in parts:
        if n not in order:
            order.append(n)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in order:
            zf.writestr(n, parts[n])


def parse_xml(raw, name):
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"{name} is not valid XML: {exc}") from exc


def rels_of(parts, part_name):
    folder, fname = part_name.rsplit("/", 1)
    rels_name = f"{folder}/_rels/{fname}.rels"
    raw = parts.get(rels_name, b"").decode("utf-8", "replace")
    out = []
    for m in REL_RE.finditer(raw):
        attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
        out.append(attrs)
    return rels_name, out


def resolve_target(folder, target):
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


def ordered_slides(parts):
    pres = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    ids = re.findall(r'<p:sldId\b[^>]*r:id="([^"]+)"', pres)
    _, rels = rels_of(parts, "ppt/presentation.xml")
    mapping = {r.get("Id"): r.get("Target", "") for r in rels}
    order = []
    for rid in ids:
        target = mapping.get(rid)
        if not target:
            continue
        part = resolve_target("ppt", target)
        if part in parts:
            order.append(part)
    if not order:
        nums = sorted((int(SLIDE_RE.match(n).group(1)), n) for n in parts if SLIDE_RE.match(n))
        order = [n for _, n in nums]
    return order


def presentation_rels(parts):
    _, rels = rels_of(parts, "ppt/presentation.xml")
    return rels


def slide_size(parts):
    raw = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    m = re.search(r"<p:sldSz\b([^>]*)/>", raw)
    if not m:
        return None
    attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
    return {"cx": int(attrs.get("cx", 0)), "cy": int(attrs.get("cy", 0)),
            "type": attrs.get("type", "")}


# ---------------------------------------------------------------------------
# theme / layout parsing
# ---------------------------------------------------------------------------
def parse_theme(parts):
    theme = {}
    for name in parts:
        if not re.match(r"^ppt/theme/theme\d+\.xml$", name):
            continue
        root = parse_xml(parts[name], name)
        el = root.find("a:themeElements", NS)
        if el is None:
            continue
        colors, fonts = {}, {}
        clr = el.find("a:clrScheme", NS)
        if clr is not None:
            for child in clr:
                tag = child.tag.rsplit("}", 1)[-1]
                srgb = child.find("a:srgbClr", NS)
                sysclr = child.find("a:sysClr", NS)
                if srgb is not None:
                    colors[tag] = srgb.get("val", "").upper()
                elif sysclr is not None:
                    colors[tag] = (sysclr.get("lastClr") or sysclr.get("val") or "").upper()
        fscheme = el.find("a:fontScheme", NS)
        if fscheme is not None:
            for which in ("majorFont", "minorFont"):
                node = fscheme.find(f"a:{which}", NS)
                if node is None:
                    continue
                fonts[which] = {}
                for face in ("latin", "ea", "cs"):
                    sub = node.find(f"a:{face}", NS)
                    if sub is not None and sub.get("typeface"):
                        fonts[which][face] = sub.get("typeface")
        theme[name] = {"colors": colors, "fonts": fonts,
                       "name": root.get("name", "")}
    return theme


def parse_layouts(parts):
    layouts = []
    for name in parts:
        m = LAYOUT_RE.match(name)
        if not m:
            continue
        root = parse_xml(parts[name], name)
        csld = root.find("p:cSld", NS)
        lname = csld.get("name", "") if csld is not None else ""
        phs = []
        for sp in root.findall(".//p:sp", NS):
            ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", NS)
            if ph is None:
                continue
            idx = ph.get("idx", "0")
            ptype = ph.get("type") or ("title" if idx == "0" else "obj")
            c_nv = sp.find("./p:nvSpPr/p:cNvPr", NS)
            xfrm = sp.find("./p:spPr/a:xfrm", NS)
            box = None
            if xfrm is not None:
                off = xfrm.find("a:off", NS)
                ext = xfrm.find("a:ext", NS)
                if off is not None and ext is not None:
                    box = {"x": int(off.get("x", 0)), "y": int(off.get("y", 0)),
                           "cx": int(ext.get("cx", 0)), "cy": int(ext.get("cy", 0))}
            phs.append({"idx": idx, "type": ptype,
                        "name": c_nv.get("name", "") if c_nv is not None else "",
                        "box": box})
        layouts.append({"part": name, "index": int(m.group(1)),
                        "name": lname, "type": root.get("type", ""),
                        "placeholders": phs})
    layouts.sort(key=lambda x: x["index"])
    return layouts


def ph_title(layout):
    for ph in layout["placeholders"]:
        if ph["type"] in ("title", "ctrTitle") or (ph["idx"] == "0" and ph["type"] not in CONTROL_PH):
            return ph
    return None


def ph_subtitle(layout):
    for ph in layout["placeholders"]:
        if ph["type"] == "subTitle":
            return ph
    return None


def ph_bodies(layout):
    return [ph for ph in layout["placeholders"]
            if ph["type"] in BODY_TYPES and ph["idx"] != "0"]


def ph_pictures(layout):
    return [ph for ph in layout["placeholders"] if ph["type"] == "pic"]


def layout_score(layout, role):
    name = layout["name"].lower()
    ltype = (layout["type"] or "").lower()
    title, bodies, pics = ph_title(layout), ph_bodies(layout), ph_pictures(layout)
    if role in ("title", "closing"):
        if ltype in ("title", "cover") or "title slide" in name or "cover" in name:
            return 100 + (20 if ph_subtitle(layout) else 0)
        return 25 if title else 0
    if role == "section":
        if ltype in ("sechead", "section") or "section" in name:
            return 100
        return 20 if title else 0
    if role == "two-column":
        return 100 + 10 * len(bodies) if title and len(bodies) >= 2 else 0
    if role == "figure":
        if title and pics:
            return 110
        return 60 if title and bodies else 0
    # content and unknown
    if title and bodies:
        score = 90
        if "content" in name or ltype in ("obj", "body"):
            score += 15
        if "section" in name or ltype in ("sechead", "section"):
            score -= 80
        return max(score, 0)
    return 0


def choose_layout(layouts, role, role_cfg):
    wanted = role_cfg.get("layout") if role_cfg else None
    candidates = list(role_cfg.get("fallback_layouts") or []) if role_cfg else []
    if wanted:
        candidates.insert(0, wanted)
    by_name = {l["name"].strip().lower(): l for l in layouts}
    for name in candidates:
        hit = by_name.get(str(name).strip().lower())
        if hit:
            return hit, None
    best, best_score = None, 0
    for layout in layouts:
        score = layout_score(layout, role)
        if score > best_score:
            best, best_score = layout, score
    if best and best_score > 0:
        fallback = None
        if wanted and best["name"].strip().lower() != str(wanted).strip().lower():
            fallback = f"layout '{wanted}' not found; used '{best['name']}'"
        return best, fallback
    return None, f"no layout can serve role '{role}'"


# ---------------------------------------------------------------------------
# XML builders
# ---------------------------------------------------------------------------
def xfrm_xml(box):
    if not box:
        return ""
    return (f'<a:xfrm><a:off x="{int(box["x"])}" y="{int(box["y"])}"/>'
            f'<a:ext cx="{int(box["cx"])}" cy="{int(box["cy"])}"/></a:xfrm>')


def paragraph_xml(paras, default_size, bold_default=False, bullet=False,
                  align=None):
    """paras: str | dict {text,size,bold,color,level,align}."""
    out = []
    for para in paras:
        if isinstance(para, str):
            para = {"text": para}
        text = str(para.get("text", ""))
        size = int(round(float(para.get("size", default_size)) * 100))
        bold = bool(para.get("bold", bold_default))
        level = int(para.get("level", 0) or 0)
        palign = para.get("align", align)
        ppr_attrs = [f'lvl="{level}"'] if level else []
        if palign:
            ppr_attrs.append(f'algn="{palign}"')
        ppr_children = [] if bullet else ["<a:buNone/>"]
        ppr = ""
        if ppr_attrs or ppr_children:
            ppr = f'<a:pPr {" ".join(ppr_attrs)}>{"".join(ppr_children)}</a:pPr>'
        color = para.get("color")
        fill = ""
        if color:
            if str(color).startswith("#"):
                color = str(color)[1:]
            if re.fullmatch(r"[0-9A-Fa-f]{6}", str(color)):
                fill = f'<a:solidFill><a:srgbClr val="{str(color).upper()}"/></a:solidFill>'
            else:
                fill = f'<a:solidFill><a:schemeClr val="{escape(str(color))}"/></a:solidFill>'
        bold_attr = ' b="1"' if bold else ""
        out.append(
            f'<a:p>{ppr}<a:r><a:rPr lang="zh-CN" sz="{size}"{bold_attr}>{fill}</a:rPr>'
            f'<a:t>{escape(text)}</a:t></a:r></a:p>')
    return "".join(out)


def placeholder_shape(sid, ph, paras, size, bullet=False, box=None,
                      bold=False, anchor=None):
    idx = ph.get("idx")
    ptype = ph.get("type")
    attrs = []
    if ptype in ("title", "ctrTitle", "subTitle", "pic"):
        attrs.append(f'type="{ptype}"')
        if ptype in ("subTitle", "pic"):
            attrs.append(f'idx="{idx}"')
    else:
        attrs.append(f'idx="{idx}"')
    ph_attrs = (" " + " ".join(attrs)) if attrs else ""
    name = escape(ph.get("name") or "Placeholder")
    sp_pr = f"<p:spPr>{xfrm_xml(box)}</p:spPr>" if box else "<p:spPr/>"
    body_pr = f'<a:bodyPr anchor="{anchor}"/>' if anchor else "<a:bodyPr/>"
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/>'
            f'<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            f'<p:nvPr><p:ph{ph_attrs}/></p:nvPr></p:nvSpPr>{sp_pr}'
            f'<p:txBody>{body_pr}<a:lstStyle/>'
            f'{paragraph_xml(paras, size, bold, bullet)}</p:txBody></p:sp>')


def free_text_shape(sid, name, box, paras, size, bold=False, anchor="t"):
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{escape(name)}"/>'
            f'<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr>{xfrm_xml(box)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            f'<p:txBody><a:bodyPr wrap="square" anchor="{anchor}"/><a:lstStyle/>'
            f'{paragraph_xml(paras, size, bold)}</p:txBody></p:sp>')


def picture_shape(sid, rid, box, name="Picture"):
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="{sid}" name="{escape(name)}"/>'
            f'<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            f'<p:spPr>{xfrm_xml(box)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')


def group_props():
    return ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


def png_size(data):
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return None


def jpeg_size(data):
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            return (int.from_bytes(data[i + 7:i + 9], "big"),
                    int.from_bytes(data[i + 5:i + 7], "big"))
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def image_size(data):
    return png_size(data) or jpeg_size(data)


def fit_box(src, box):
    """Scale (w,h) into box, preserving aspect ratio, centred."""
    if not src:
        return dict(box)
    sw, sh = src
    if sw <= 0 or sh <= 0:
        return dict(box)
    scale = min(box["cx"] / sw, box["cy"] / sh)
    w, h = int(sw * scale), int(sh * scale)
    return {"x": int(box["x"] + (box["cx"] - w) / 2),
            "y": int(box["y"] + (box["cy"] - h) / 2),
            "cx": w, "cy": h}


# ---------------------------------------------------------------------------
# notes / relationships
# ---------------------------------------------------------------------------
def notes_master_part(parts):
    for rel in presentation_rels(parts):
        if rel.get("Type", "").endswith("/notesMaster"):
            return resolve_target("ppt", rel.get("Target", ""))
    return None


def notes_xml(text):
    paras = [{"text": line, "size": 12} for line in str(text).split("\n")] or \
            [{"text": "", "size": 12}]
    root_ns = f'xmlns:p="{P_NS}" xmlns:a="{A_NS}" xmlns:r="{R_NS}"'
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:notes {root_ns}>'
            '<p:cSld><p:spTree>' + group_props() +
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="body" idx="3"/></p:nvPr></p:nvSpPr><p:spPr/>'
            f'<p:txBody><a:bodyPr/><a:lstStyle/>{paragraph_xml(paras, 12)}</p:txBody></p:sp>'
            '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>')


def rels_xml(entries):
    body = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{g}"/>'
                   for i, t, g in entries)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{body}</Relationships>')


# ---------------------------------------------------------------------------
# content types (kept in sync with shared/scripts/pptx_edit.py semantics)
# ---------------------------------------------------------------------------
CT = "application/vnd.openxmlformats-officedocument"


def content_type_for(name):
    fixed = {
        "ppt/presentation.xml": CT + ".presentationml.presentation.main+xml",
        "ppt/presProps.xml": CT + ".presentationml.presProps+xml",
        "ppt/viewProps.xml": CT + ".presentationml.viewProps+xml",
        "ppt/tableStyles.xml": CT + ".presentationml.tableStyles+xml",
        "docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
        "docProps/app.xml": CT + ".extended-properties+xml",
    }
    if name in fixed:
        return fixed[name]
    patterns = [
        (r"ppt/slideMasters/slideMaster\d+\.xml$", CT + ".presentationml.slideMaster+xml"),
        (r"ppt/slideLayouts/slideLayout\d+\.xml$", CT + ".presentationml.slideLayout+xml"),
        (r"ppt/slides/slide\d+\.xml$", CT + ".presentationml.slide+xml"),
        (r"ppt/notesMasters/notesMaster\d+\.xml$", CT + ".presentationml.notesMaster+xml"),
        (r"ppt/notesSlides/notesSlide\d+\.xml$", CT + ".presentationml.notesSlide+xml"),
        (r"ppt/theme/theme\d+\.xml$", CT + ".theme+xml"),
        (r"ppt/comments/comment\d+\.xml$", CT + ".presentationml.comments+xml"),
        (r"ppt/commentAuthors\.xml$", CT + ".presentationml.commentAuthors+xml"),
    ]
    for pat, ct in patterns:
        if re.match(pat, name):
            return ct
    if name.endswith(".xml"):
        return "application/xml"
    return None


def rebuild_content_types(parts):
    defaults = {
        "rels": "application/vnd.openxmlformats-package.relationships+xml",
        "xml": "application/xml", "png": "image/png", "jpeg": "image/jpeg",
        "jpg": "image/jpeg", "gif": "image/gif", "svg": "image/svg+xml",
        "emf": "image/x-emf", "wmf": "image/x-wmf",
        "mp4": "video/mp4", "m4a": "audio/mp4", "mp3": "audio/mpeg",
        "wav": "audio/wav", "bin": "application/vnd.openxmlformats-officedocument.oleObject",
    }
    used = {n.rsplit(".", 1)[-1].lower() for n in parts if "." in n}
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">']
    for ext, ct in defaults.items():
        if ext in used:
            lines.append(f'<Default Extension="{ext}" ContentType="{ct}"/>')
    for name in sorted(parts):
        if name == "[Content_Types].xml" or name.endswith(".rels") or name.endswith("/"):
            continue
        ct = content_type_for(name)
        if ct:
            lines.append(f'<Override PartName="/{name}" ContentType="{ct}"/>')
    lines.append("</Types>")
    return "".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# build mode
# ---------------------------------------------------------------------------
def remove_existing_slides(parts):
    removed = 0
    for name in list(parts):
        if SLIDE_RE.match(name) or NOTES_RE.match(name) or \
                name.startswith("ppt/slides/_rels/") or name.startswith("ppt/notesSlides/_rels/") or \
                name.startswith("ppt/comments/"):
            del parts[name]
            removed += 1
    # presentation relationships to slides/notes/comments
    rels_name = "ppt/_rels/presentation.xml.rels"
    raw = parts.get(rels_name, b"").decode("utf-8", "replace")
    def keep(m):
        attrs = m.group(1)
        t = re.search(r'Type="([^"]*)"', attrs)
        typ = t.group(1) if t else ""
        if typ.endswith(("/slide", "/notesSlide", "/comments", "/commentAuthors")):
            return ""
        return m.group(0)
    parts[rels_name] = REL_RE.sub(keep, raw).encode("utf-8")
    return removed


def add_media(parts, src, used_names):
    data = Path(src).read_bytes()
    ext = Path(src).suffix.lower().lstrip(".") or "png"
    if ext == "jpg":
        ext = "jpeg"
    n = 1
    while f"image{n}.{ext}" in used_names or f"ppt/media/image{n}.{ext}" in parts:
        n += 1
    name = f"image{n}.{ext}"
    used_names.add(name)
    parts[f"ppt/media/{name}"] = data
    return name, image_size(data)


def build_slide_xml(spec_slide, layout, role_cfg, parts, media_state,
                    sizes):
    role = spec_slide.get("role", "content")
    shapes, sid = [], 2
    used_boxes = []
    title_ph = ph_title(layout)
    subtitle_ph = ph_subtitle(layout)
    bodies = ph_bodies(layout)
    pics = ph_pictures(layout)
    title_size = float((role_cfg or {}).get("title_size", sizes.get("title", 32)))
    body_size = float((role_cfg or {}).get("body_size", sizes.get("body", 18)))
    caption_size = float(sizes.get("caption", 12))
    layout_type = (layout.get("type") or "").lower()
    is_caption_layout = (layout_type in ("pictx", "pic") or
                         "caption" in (layout.get("name") or "").lower())
    body_caption_size = caption_size if is_caption_layout else body_size
    slide_cx, slide_cy = media_state["slide_cx"], media_state["slide_cy"]
    margin = int(slide_cx * 0.055)

    if title_ph is not None and spec_slide.get("title") is not None:
        shapes.append(placeholder_shape(sid, title_ph, [{
            "text": spec_slide.get("title", ""), "size": title_size, "bold": True,
            "color": (role_cfg or {}).get("title_color", "tx1")}],
            title_size, bullet=False, bold=True))
        sid += 1

    if subtitle_ph is not None and spec_slide.get("subtitle") is not None:
        shapes.append(placeholder_shape(sid, subtitle_ph, [{
            "text": spec_slide.get("subtitle", ""), "size": body_size,
            "color": (role_cfg or {}).get("subtitle_color", "tx2")}],
            body_size, bullet=False, bold=False))
        sid += 1
    elif (subtitle_ph is None and spec_slide.get("subtitle") and
          role in ("title", "closing", "section") and bodies):
        # single-layout / title-only fallback: keep the subtitle in the real
        # body placeholder instead of silently dropping it
        shapes.append(placeholder_shape(
            sid, bodies[0],
            [{"text": spec_slide["subtitle"], "size": body_size,
              "color": "tx2"}], body_size, bullet=False, bold=False))
        sid += 1

    def add_image(box, image_path, caption=None):
        nonlocal sid
        media_name, px = add_media(parts, image_path, media_state["names"])
        rid = f"rIdImg{sid}"
        media_state["images"].append((rid, media_name))
        shapes.append(picture_shape(sid, rid, fit_box(px, box), Path(image_path).name))
        sid += 1
        if caption:
            cap_box = {"x": box["x"], "y": box["y"] + box["cy"] + int(0.04 * slide_cy),
                       "cx": box["cx"], "cy": int(slide_cy * 0.07)}
            shapes.append(free_text_shape(
                sid, "Caption", cap_box,
                [{"text": caption, "size": caption_size, "color": "tx2"}],
                caption_size))
            sid += 1

    if role in ("title", "closing"):
        pass
    elif role == "section":
        pass
    elif role == "figure" and pics:
        box = pics[0].get("box") or {
            "x": margin, "y": int(slide_cy * 0.20),
            "cx": slide_cx - 2 * margin, "cy": int(slide_cy * 0.62)}
        if spec_slide.get("image"):
            caption = spec_slide.get("caption")
            if caption and bodies:
                add_image(box, spec_slide["image"], None)
                shapes.append(placeholder_shape(
                    sid, bodies[0],
                    [{"text": caption, "size": body_caption_size, "color": "tx2"}],
                    body_caption_size, bullet=False))
                sid += 1
            else:
                add_image(box, spec_slide["image"], caption)
        elif bodies:
            shapes.append(placeholder_shape(
                sid, bodies[0], spec_slide.get("bullets") or [],
                body_size, bullet=True, bold=False))
            sid += 1
    elif role == "two-column":
        lefts = list(spec_slide.get("bullets") or [])
        right_bullets = list(spec_slide.get("bullets_right") or [])
        if bodies:
            paras = list(lefts)
            if len(bodies) < 2 and right_bullets:
                paras.append({"text": "对照：", "level": 0, "bold": True})
                paras.extend({"text": t, "level": 1} for t in right_bullets)
            shapes.append(placeholder_shape(sid, bodies[0], paras, body_size,
                                            bullet=True))
            sid += 1
        if len(bodies) > 1:
            right = right_bullets
            if spec_slide.get("image") and not right:
                box = bodies[1].get("box") or {
                    "x": int(slide_cx * 0.52), "y": int(slide_cy * 0.22),
                    "cx": int(slide_cx * 0.42), "cy": int(slide_cy * 0.62)}
                add_image(box, spec_slide["image"], spec_slide.get("caption"))
            else:
                shapes.append(placeholder_shape(sid, bodies[1], right, body_size,
                                                bullet=True))
                sid += 1
    else:  # content / unknown
        bullets = spec_slide.get("bullets") or []
        if spec_slide.get("image") and bodies:
            body_box = bodies[0].get("box") or {
                "x": margin, "y": int(slide_cy * 0.22),
                "cx": slide_cx - 2 * margin, "cy": int(slide_cy * 0.64)}
            gap = int(slide_cx * 0.02)
            left = {"x": body_box["x"], "y": body_box["y"],
                    "cx": int(body_box["cx"] * 0.54), "cy": body_box["cy"]}
            right = {"x": body_box["x"] + body_box["cx"] - int(body_box["cx"] * 0.40),
                     "y": body_box["y"], "cx": int(body_box["cx"] * 0.40),
                     "cy": body_box["cy"]}
            if left["x"] + left["cx"] + gap > right["x"]:
                left["cx"] = max(int(left["cx"] * 0.9), int(slide_cx * 0.30))
            caption = spec_slide.get("caption")
            if not bullets and caption:
                shapes.append(placeholder_shape(
                    sid, bodies[0],
                    [{"text": caption, "size": body_caption_size, "color": "tx2"}],
                    body_caption_size, bullet=False, box=left))
                sid += 1
                add_image(right, spec_slide["image"], None)
            else:
                shapes.append(placeholder_shape(sid, bodies[0], bullets, body_size,
                                                bullet=True, box=left))
                sid += 1
                add_image(right, spec_slide["image"], caption)
        elif bodies:
            shapes.append(placeholder_shape(sid, bodies[0], bullets, body_size,
                                            bullet=True))
            sid += 1
        elif bullets:
            box = {"x": margin, "y": int(slide_cy * 0.22),
                   "cx": slide_cx - 2 * margin, "cy": int(slide_cy * 0.64)}
            shapes.append(free_text_shape(sid, "Content", box, bullets, body_size))
            sid += 1
        if spec_slide.get("image") and not bodies:
            box = {"x": int(slide_cx * 0.30), "y": int(slide_cy * 0.42),
                   "cx": int(slide_cx * 0.62), "cy": int(slide_cy * 0.45)}
            add_image(box, spec_slide["image"], spec_slide.get("caption"))

    if spec_slide.get("footer"):
        box = {"x": margin, "y": int(slide_cy * 0.93),
               "cx": slide_cx - 2 * margin, "cy": int(slide_cy * 0.05)}
        shapes.append(free_text_shape(
            sid, "Footer", box,
            [{"text": spec_slide["footer"], "size": sizes.get("footer", 10),
              "color": "tx2"}], sizes.get("footer", 10)))
        sid += 1

    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sld xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
            f'<p:cSld><p:spTree>{group_props()}{"".join(shapes)}</p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'), sid


def sld_id_list(count, prefix="rIdSlide", start_id=256):
    return "".join(f'<p:sldId id="{start_id + i}" r:id="{prefix}{i + 1}"/>'
                   for i in range(count))


def next_rid(rels, prefix="rIdN"):
    used = {r.get("Id", "") for r in rels}
    n = 1
    while f"{prefix}{n}" in used:
        n += 1
    return f"{prefix}{n}"


def build_from_spec(template_path, spec, format_spec, out_path, report_path):
    parts = read_parts(template_path)
    original_order = list(parts)
    masters = [n for n in parts if re.match(r"^ppt/slideMasters/slideMaster\d+\.xml$", n)]
    themes = [n for n in parts if re.match(r"^ppt/theme/theme\d+\.xml$", n)]
    if not masters or not themes:
        return {"ok": False, "error": "template has no slide master or theme",
                "status": "partial"}, 2
    layouts = parse_layouts(parts)
    if not layouts:
        return {"ok": False, "error": "template has no slide layout"}, 2

    fmt_roles = (format_spec or {}).get("roles", {}) or {}
    typo = (format_spec or {}).get("typography", {}) or {}
    sizes = {
        "title": typo.get("title_size", 32),
        "body": typo.get("body_size", 18),
        "caption": typo.get("caption_size", 12),
        "footer": typo.get("footer_size", 10),
    }
    master_notes = notes_master_part(parts)
    slide_cx, slide_cy = (slide_size(parts) or {"cx": 12192000, "cy": 6858000})["cx"], \
                         (slide_size(parts) or {"cx": 12192000, "cy": 6858000})["cy"]
    remove_existing_slides(parts)
    # presentation rels after cleanup
    _, prels = rels_of(parts, "ppt/presentation.xml")
    new_prels = list(prels)
    prefix = "rIdSlide"
    while any(str(r.get("Id", "")).startswith(prefix) for r in prels):
        prefix += "X"
    media_state = {"names": set(), "images": [], "slide_cx": slide_cx,
                   "slide_cy": slide_cy}

    mapping, warnings, uncertainty = [], [], []
    slides = spec.get("slides", [])
    slide_parts = []
    for i, slide_spec in enumerate(slides, 1):
        role = slide_spec.get("role", "content")
        role_cfg = fmt_roles.get(role) or {}
        explicit = slide_spec.get("layout")
        layout, fallback = choose_layout(
            layouts, role,
            dict(role_cfg, layout=explicit) if explicit else role_cfg)
        if layout is None:
            return {"ok": False, "error": f"slide {i}: no layout for role {role}"}, 2
        if fallback:
            warnings.append(f"slide {i}: {fallback}")
            uncertainty.append({"slide": i, "issue": "layout_fallback",
                                "detail": fallback})
        if role == "two-column" and len(ph_bodies(layout)) < 2:
            detail = (f"two-column role uses '{layout['name']}' with "
                      f"{len(ph_bodies(layout))} body placeholder(s); right column "
                      "content was merged into the single body")
            warnings.append(f"slide {i}: {detail}")
            uncertainty.append({"slide": i, "issue": "column_collapse",
                                "detail": detail})
        if role == "figure" and not ph_pictures(layout):
            detail = (f"figure role uses '{layout['name']}' without a picture "
                      "placeholder; image was placed in the body area")
            warnings.append(f"slide {i}: {detail}")
            uncertainty.append({"slide": i, "issue": "picture_placeholder_missing",
                                "detail": detail})
        if (role in ("title", "closing") and slide_spec.get("subtitle") and
                ph_subtitle(layout) is None):
            detail = (f"layout '{layout['name']}' has no subtitle placeholder; "
                      "subtitle was written into the body placeholder")
            warnings.append(f"slide {i}: {detail}")
            uncertainty.append({"slide": i, "issue": "subtitle_in_body",
                                "detail": detail})
        media_state["images"] = []
        slide_xml, _ = build_slide_xml(slide_spec, layout, role_cfg, parts,
                                       media_state, sizes)
        slide_part = f"ppt/slides/slide{i}.xml"
        slide_parts.append(slide_part)
        parts[slide_part] = slide_xml.encode("utf-8")
        rel_entries = [("rId1", P_NS + "/slideLayout",
                        f"../slideLayouts/{layout['part'].split('/')[-1]}")]
        for rid, media_name in media_state["images"]:
            rel_entries.append((rid, R_NS + "/image", f"../media/{media_name}"))
        has_notes = bool(str(slide_spec.get("notes") or "").strip())
        if has_notes and master_notes:
            notes_part = f"ppt/notesSlides/notesSlide{i}.xml"
            parts[notes_part] = notes_xml(slide_spec["notes"]).encode("utf-8")
            parts[f"ppt/notesSlides/_rels/notesSlide{i}.xml.rels"] = rels_xml([
                ("rIdSlide", P_NS + "/slide", f"../slides/slide{i}.xml"),
                ("rIdNotesMaster", P_NS + "/notesMaster",
                 f"../notesMasters/{master_notes.split('/')[-1]}"),
            ]).encode("utf-8")
            rel_entries.append(("rIdNotes", P_NS + "/notesSlide",
                                f"../notesSlides/notesSlide{i}.xml"))
        elif has_notes:
            warnings.append(f"slide {i}: template has no notes master; notes omitted")
        parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = rels_xml(rel_entries).encode("utf-8")
        mapping.append({"slide": i, "role": role, "layout": layout["name"],
                        "layout_part": layout["part"],
                        "placeholders_used": [p["idx"] for p in layout["placeholders"]
                                              if p["type"] not in CONTROL_PH],
                        "fallback": fallback})
        rid = f"{prefix}{i}"
        new_prels.append({"Id": rid, "Type": P_NS + "/slide",
                          "Target": f"slides/slide{i}.xml"})
        parts[f"ppt/_rels/presentation.xml.rels"] = rels_xml(
            [(r.get("Id"), r.get("Type"), r.get("Target")) for r in new_prels]).encode("utf-8")

    # presentation.xml: replace slide id list
    pres = parts["ppt/presentation.xml"].decode("utf-8", "replace")
    new_list = f"<p:sldIdLst>{sld_id_list(len(slides), prefix)}</p:sldIdLst>"
    if "<p:sldIdLst>" in pres:
        pres = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>", new_list, pres, flags=re.S)
    elif "<p:sldIdLst/>" in pres:
        pres = pres.replace("<p:sldIdLst/>", new_list)
    else:
        pres = re.sub(r"(<p:sldSz\b[^>]*/>)", new_list + r"\1", pres)
    parts["ppt/presentation.xml"] = pres.encode("utf-8")
    if "docProps/app.xml" in parts:
        app = parts["docProps/app.xml"].decode("utf-8", "replace")
        app = re.sub(r"<Slides>\d+</Slides>", f"<Slides>{len(slides)}</Slides>", app)
        parts["docProps/app.xml"] = app.encode("utf-8")
    parts["[Content_Types].xml"] = rebuild_content_types(parts)

    ok, problems = package_ok(parts)
    if not ok:
        return {"ok": False, "error": "adapted package is invalid", "problems": problems}, 1
    write_parts(parts, out_path, original_order)
    report = {
        "ok": True, "mode": "build", "status": "completed",
        "template": str(template_path), "out": str(out_path),
        "fidelity": "master+layouts+theme",
        "slide_count": len(slides),
        "slide_size": {"cx": slide_cx, "cy": slide_cy},
        "layouts_available": [l["name"] for l in layouts],
        "layout_map": mapping,
        "font_substitution": (format_spec.get("fonts") or {}).get("substitutions") or [],
        "content": {"source": str(spec.get("source", "")),
                    "slides_with_notes": sum(1 for s in slides if s.get("notes"))},
        "checks": {"zip_valid": True, "xml_valid": True,
                   "relationships_resolved": True,
                   "sample_slides_removed": True},
        "warnings": warnings, "uncertainty": uncertainty,
        "change_log": [f"removed template sample slides",
                       f"created {len(slides)} slides from {len(set(m['layout'] for m in mapping))} layouts"],
    }
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    return report, 0


# ---------------------------------------------------------------------------
# retarget mode
# ---------------------------------------------------------------------------
def retarget_deck(deck_path, template_path, layout_map, out_path, report_path):
    deck = read_parts(deck_path)
    original_order = list(deck)
    tmpl = read_parts(template_path)
    masters = [n for n in tmpl if re.match(r"^ppt/slideMasters/slideMaster\d+\.xml$", n)]
    themes = [n for n in tmpl if re.match(r"^ppt/theme/theme\d+\.xml$", n)]
    if not masters or not themes:
        return {"ok": False, "error": "template has no slide master or theme"}, 2
    layouts = parse_layouts(tmpl)
    by_name = {l["name"].strip().lower(): l for l in layouts}
    slides = ordered_slides(deck)
    entries = (layout_map or {}).get("slides") or []
    if not entries:
        return {"ok": False, "error": "layout map has no 'slides' entries"}, 2
    if len(entries) != len(slides):
        return {"ok": False,
                "error": f"layout map has {len(entries)} entries for {len(slides)} slides"}, 2
    old_size = slide_size(deck)

    theme_only = len(masters) != 1 or masters[0] != "ppt/slideMasters/slideMaster1.xml"
    if theme_only:
        deck["ppt/theme/theme1.xml"] = tmpl[themes[0]]
        deck["[Content_Types].xml"] = rebuild_content_types(deck)
        ok, problems = package_ok(deck)
        if not ok:
            return {"ok": False, "error": "theme adoption broke package",
                    "problems": problems}, 1
        write_parts(deck, out_path, original_order)
        report = {"ok": True, "mode": "retarget", "status": "partial",
                  "fidelity": "theme-only", "out": str(out_path),
                  "slide_count": len(slides),
                  "layout_map": entries,
                  "warning": "template has several masters/themes; theme only",
                  "warnings": ["template has several masters/themes; theme only"],
                  "checks": {"zip_valid": True}}
        if report_path:
            Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
        return report, 0

    prefixes = ("ppt/slideMasters/", "ppt/slideLayouts/", "ppt/theme/", "ppt/media/")
    rename = {}
    for name, raw in tmpl.items():
        if not name.startswith(prefixes):
            continue
        target = name
        if name.startswith("ppt/media/") and name in deck and deck[name] != raw:
            target = "ppt/media/tpl_" + name.split("/")[-1]
            rename[name.split("/")[-1]] = target.split("/")[-1]
        deck[target] = raw
    if rename:
        for name in list(deck):
            if name.endswith(".rels") and name.startswith(("ppt/slideMasters/", "ppt/slideLayouts/")):
                raw = deck[name].decode("utf-8", "replace")
                for old, new in rename.items():
                    raw = raw.replace(f"../media/{old}\"", f"../media/{new}\"")
                deck[name] = raw.encode("utf-8")

    prels_name = "ppt/_rels/presentation.xml.rels"
    prels_raw = deck.get(prels_name, b"").decode("utf-8", "replace")
    _, prels_list = rels_of(deck, "ppt/presentation.xml")
    master_rid = None
    for rel in prels_list:
        if rel.get("Type", "").endswith("/slideMaster"):
            master_rid = rel.get("Id")
            break
    if not master_rid:
        used = {r.get("Id") for r in prels_list}
        master_rid = "rIdMaster"
        n = 1
        while master_rid in used:
            master_rid = f"rIdMaster{n}"
            n += 1
        prels_raw = prels_raw.replace(
            "</Relationships>",
            f'<Relationship Id="{master_rid}" Type="{P_NS}/slideMaster" '
            'Target="slideMasters/slideMaster1.xml"/></Relationships>')
        deck[prels_name] = prels_raw.encode("utf-8")
    pres = deck["ppt/presentation.xml"].decode("utf-8", "replace")
    pres = re.sub(
        r"<p:sldMasterIdLst>.*?</p:sldMasterIdLst>",
        f'<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="{master_rid}"/>'
        '</p:sldMasterIdLst>', pres, flags=re.S)
    tpres = tmpl.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    sz = re.search(r"<p:sldSz\b[^>]*/>", tpres)
    if sz:
        pres = re.sub(r"<p:sldSz\b[^>]*/>", sz.group(0), pres)
    deck["ppt/presentation.xml"] = pres.encode("utf-8")

    mapping, warnings = [], []
    deck_size, template_size = old_size, slide_size(tmpl)
    if deck_size and template_size and (deck_size["cx"], deck_size["cy"]) != (
            template_size["cx"], template_size["cy"]):
        warnings.append(
            f"deck slide size {deck_size['cx']}x{deck_size['cy']} differs from "
            f"template {template_size['cx']}x{template_size['cy']}; template size "
            "was adopted and existing element geometry was not rescaled")
    for i, (slide_part, entry) in enumerate(zip(slides, entries), 1):
        wanted = str(entry.get("layout", "")).strip().lower()
        layout = by_name.get(wanted)
        if layout is None:
            return {"ok": False,
                    "error": f"slide {i}: layout '{entry.get('layout')}' not in template"}, 2
        _, rels = rels_of(deck, slide_part)
        previous = None
        for rel in rels:
            if rel.get("Type", "").endswith("/slideLayout"):
                previous = rel.get("Target", "").split("/")[-1]
                break
        rels_name = f"{slide_part.rsplit('/', 1)[0]}/_rels/{slide_part.rsplit('/', 1)[1]}.rels"
        raw = deck.get(rels_name, b"").decode("utf-8", "replace")
        pat = r'(<Relationship\b[^>]*Type="[^"]*/slideLayout"[^>]*Target=")[^"]*(")'
        if re.search(pat, raw):
            raw = re.sub(pat, r"\g<1>../slideLayouts/" +
                         layout["part"].split("/")[-1] + r"\g<2>", raw)
        else:
            rid = next_rid(rels, "rIdLayout")
            raw = raw.replace("</Relationships>",
                              f'<Relationship Id="{rid}" Type="{P_NS}/slideLayout" '
                              f'Target="../slideLayouts/{layout["part"].split("/")[-1]}"/>'
                              '</Relationships>')
        deck[rels_name] = raw.encode("utf-8")
        mapping.append({"slide": i, "role": entry.get("role", ""),
                        "layout": layout["name"],
                        "previous_layout_part": previous,
                        "content_preserved": True})
    deck["[Content_Types].xml"] = rebuild_content_types(deck)
    ok, problems = package_ok(deck)
    if not ok:
        return {"ok": False, "error": "retargeted package is invalid",
                "problems": problems}, 1
    write_parts(deck, out_path, original_order)
    report = {"ok": True, "mode": "retarget", "status": "completed",
              "fidelity": "master+layouts+theme", "out": str(out_path),
              "slide_count": len(slides), "layout_map": mapping,
              "content": {"preserved": True, "slide_parts_untouched": len(slides)},
              "font_substitution": [], "warnings": warnings,
              "checks": {"zip_valid": True, "xml_valid": True,
                         "relationships_resolved": True}}
    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    return report, 0


# ---------------------------------------------------------------------------
# package validation
# ---------------------------------------------------------------------------
def package_ok(parts):
    problems = []
    if "ppt/presentation.xml" not in parts:
        problems.append("missing ppt/presentation.xml")
    if "[Content_Types].xml" not in parts:
        problems.append("missing [Content_Types].xml")
    for name in parts:
        if name.endswith(".xml") or name.endswith(".rels"):
            try:
                ET.fromstring(parts[name])
            except ET.ParseError as exc:
                problems.append(f"{name}: {exc}")
    for name in parts:
        if not name.endswith(".rels"):
            continue
        folder = name.rsplit("/_rels/", 1)[0] if "/_rels/" in name else ""
        base = name.rsplit("/", 1)[-1][:-5] if name.endswith(".rels") else ""
        raw = parts[name].decode("utf-8", "replace")
        for m in REL_RE.finditer(raw):
            attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
            if attrs.get("TargetMode") == "External":
                continue
            target = resolve_target(folder, attrs.get("Target", ""))
            if target and target not in parts:
                problems.append(f"{name}: dangling target {target}")
    return (not problems), problems


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="adapt content / an existing deck to a real PPTX template")
    ap.add_argument("--template", required=True, help="template .pptx/.potx")
    ap.add_argument("--spec", help="content deck spec (build mode)")
    ap.add_argument("--deck", help="existing deck (retarget mode)")
    ap.add_argument("--format-spec", help="format spec JSON (build mode)")
    ap.add_argument("--layout-map", help="layout map JSON (retarget mode)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        if args.deck:
            layout_map = {}
            if args.layout_map:
                layout_map = json.loads(Path(args.layout_map).read_text(encoding="utf-8"))
            report, code = retarget_deck(args.deck, args.template, layout_map,
                                         args.out, args.report)
        elif args.spec:
            spec_path = Path(args.spec)
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            for s in spec.get("slides", []):
                img = s.get("image")
                if img and not Path(img).is_absolute():
                    candidate = spec_path.parent / img
                    if candidate.exists():
                        s["image"] = str(candidate)
            fmt = {}
            if args.format_spec:
                fmt = json.loads(Path(args.format_spec).read_text(encoding="utf-8"))
            report, code = build_from_spec(args.template, spec, fmt, args.out,
                                           args.report)
        else:
            report, code = {"ok": False, "error": "one of --spec or --deck is required"}, 2
    except (OSError, zipfile.BadZipFile, ValueError, ET.ParseError) as exc:
        report, code = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
