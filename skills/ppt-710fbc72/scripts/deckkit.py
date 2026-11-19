#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deckkit: stdlib-only OOXML deck toolkit for real template adaptation.

Responsibilities:
  * read/write a .pptx package without python-pptx;
  * audit the real master / layouts / placeholders / theme fonts / slide size;
  * build a deck from a model-written deck spec into real layout placeholders;
  * retarget an existing deck onto a target template without dropping content;
  * apply workorder text edits; keep speaker notes and image aspect ratio;
  * optionally export a native PDF/PNG preview through LibreOffice.

The module is task-specific (not a generic common.py).  It never invents
scientific content and never changes fonts silently.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import xml.etree.ElementTree as ET

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument"
NS = {"p": P_NS, "a": A_NS, "r": R_NS}

SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
LAYOUT_RE = re.compile(r"^ppt/slideLayouts/slideLayout(\d+)\.xml$")
MASTER_RE = re.compile(r"^ppt/slideMasters/slideMaster(\d+)\.xml$")
NOTES_RE = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")
THEME_RE = re.compile(r"^ppt/theme/theme(\d+)\.xml$")
MEDIA_RE = re.compile(r"^ppt/media/(.+)$")

REL_RE = re.compile(r"<Relationship\b([^>]*)/>")
T_RE = re.compile(r"(<a:t(?:\s[^>]*)?>)(.*?)(</a:t>)", re.S)
P_RE = re.compile(r"<a:p(?:\s[^>]*)?>.*?</a:p>", re.S)
PH_RE = re.compile(r"<p:ph\b([^>]*)/>")
ATTR_RE = re.compile(r'([\w:]+)="([^"]*)"')

CONTROL_PH = {"dt", "ftr", "sldNum", "hdr", "sldImg"}
BODY_TYPES = {"body", "obj"}
TITLE_TYPES = {"title", "ctrTitle"}
EMU_PT = 12700


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
    for name in original_order or []:
        if name in parts and name not in order:
            order.append(name)
    for name in parts:
        if name not in order:
            order.append(name)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in order:
            zf.writestr(name, parts[name])


def parse_xml(raw, name="part"):
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"{name} is not valid XML: {exc}") from exc


def rels_of(parts, part_name):
    folder, fname = part_name.rsplit("/", 1)
    rels_name = f"{folder}/_rels/{fname}.rels"
    raw = parts.get(rels_name, b"").decode("utf-8", "replace")
    out = []
    for match in REL_RE.finditer(raw):
        out.append(dict(ATTR_RE.findall(match.group(1))))
    return rels_name, out


def resolve_target(folder, target):
    if not target:
        return ""
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


def xml_text(node):
    return "".join(t.text or "" for t in node.iter(f"{{{A_NS}}}t"))


def ordered_slides(parts):
    raw = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    ids = re.findall(r'<p:sldId\b[^>]*r:id="([^"]+)"', raw)
    _, rels = rels_of(parts, "ppt/presentation.xml")
    mapping = {r.get("Id"): r.get("Target", "") for r in rels}
    order = []
    for rid in ids:
        part = resolve_target("ppt", mapping.get(rid, ""))
        if part in parts:
            order.append(part)
    if not order:
        numbered = sorted((int(SLIDE_RE.match(n).group(1)), n)
                          for n in parts if SLIDE_RE.match(n))
        order = [name for _, name in numbered]
    return order


def slide_size(parts):
    raw = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    match = re.search(r"<p:sldSz\b([^>]*)/>", raw)
    if not match:
        return None
    attrs = dict(ATTR_RE.findall(match.group(1)))
    try:
        return {"cx": int(attrs.get("cx", 0)), "cy": int(attrs.get("cy", 0)),
                "type": attrs.get("type", "")}
    except ValueError:
        return None


def aspect_ratio(size):
    if not size or not size.get("cx") or not size.get("cy"):
        return ""
    ratio = size["cx"] / size["cy"]
    for label, value in (("16:9", 16 / 9), ("4:3", 4 / 3), ("16:10", 16 / 10)):
        if abs(ratio - value) < 0.02:
            return label
    return f"{ratio:.2f}:1"


# ---------------------------------------------------------------------------
# theme / layouts
# ---------------------------------------------------------------------------
def parse_theme(parts):
    themes = {}
    for name in sorted(parts):
        if not THEME_RE.match(name):
            continue
        root = parse_xml(parts[name], name)
        element = root.find("a:themeElements", NS)
        if element is None:
            continue
        colors, fonts = {}, {}
        scheme = element.find("a:clrScheme", NS)
        if scheme is not None:
            for child in scheme:
                tag = child.tag.rsplit("}", 1)[-1]
                srgb = child.find("a:srgbClr", NS)
                sysclr = child.find("a:sysClr", NS)
                if srgb is not None:
                    colors[tag] = srgb.get("val", "").upper()
                elif sysclr is not None:
                    colors[tag] = (sysclr.get("lastClr") or
                                   sysclr.get("val") or "").upper()
        font_scheme = element.find("a:fontScheme", NS)
        if font_scheme is not None:
            for which in ("majorFont", "minorFont"):
                node = font_scheme.find(f"a:{which}", NS)
                if node is None:
                    continue
                fonts[which] = {}
                for face in ("latin", "ea", "cs"):
                    sub = node.find(f"a:{face}", NS)
                    if sub is not None and sub.get("typeface"):
                        fonts[which][face] = sub.get("typeface")
        themes[name] = {"colors": colors, "fonts": fonts,
                        "name": root.get("name", "")}
    return themes


def master_theme_part(parts):
    for master in sorted(n for n in parts if MASTER_RE.match(n)):
        _, rels = rels_of(parts, master)
        for rel in rels:
            if rel.get("Type", "").endswith("/theme"):
                target = resolve_target(master.rsplit("/", 1)[0], rel.get("Target", ""))
                if target in parts:
                    return target
    for name in sorted(parts):
        if THEME_RE.match(name):
            return name
    return None


def parse_layouts(parts):
    layouts = []
    for name in sorted(parts, key=lambda n: (
            int(LAYOUT_RE.match(n).group(1)) if LAYOUT_RE.match(n) else 0, n)):
        match = LAYOUT_RE.match(name)
        if not match:
            continue
        root = parse_xml(parts[name], name)
        csld = root.find("p:cSld", NS)
        layout_name = csld.get("name", "") if csld is not None else ""
        placeholders = []
        for sp in root.findall(".//p:sp", NS):
            ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", NS)
            if ph is None:
                continue
            idx = ph.get("idx", "0")
            ptype = ph.get("type") or ("title" if idx == "0" else "obj")
            nv = sp.find("./p:nvSpPr/p:cNvPr", NS)
            box = shape_box(sp)
            placeholders.append({
                "idx": idx, "type": ptype,
                "name": nv.get("name", "") if nv is not None else "",
                "box": box})
        layouts.append({"part": name, "index": int(match.group(1)),
                        "name": layout_name, "type": root.get("type", ""),
                        "placeholders": placeholders})
    return layouts


def shape_box(node):
    xfrm = node.find("./p:spPr/a:xfrm", NS)
    if xfrm is None:
        xfrm = node.find("./a:xfrm", NS)
    if xfrm is None:
        return None
    off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
    if off is None or ext is None:
        return None
    try:
        return {"x": int(off.get("x", 0)), "y": int(off.get("y", 0)),
                "cx": int(ext.get("cx", 0)), "cy": int(ext.get("cy", 0))}
    except ValueError:
        return None


def ph_title(layout):
    for ph in layout.get("placeholders", []):
        if ph["type"] in TITLE_TYPES:
            return ph
    for ph in layout.get("placeholders", []):
        if ph["type"] not in CONTROL_PH and ph.get("idx") in ("0", None):
            return ph
    return None


def ph_subtitle(layout):
    for ph in layout.get("placeholders", []):
        if ph["type"] == "subTitle":
            return ph
    return None


def ph_bodies(layout):
    return [ph for ph in layout.get("placeholders", [])
            if ph["type"] in BODY_TYPES and ph["idx"] != "0"]


def ph_pictures(layout):
    return [ph for ph in layout.get("placeholders", []) if ph["type"] == "pic"]


def ph_footer(layout):
    for ph in layout.get("placeholders", []):
        if ph["type"] == "ftr":
            return ph
    return None


def layout_by_name(layouts, name):
    needle = str(name or "").strip().lower()
    if not needle:
        return None
    for layout in layouts:
        if layout["name"].strip().lower() == needle:
            return layout
    return None


def role_from_layout(layout):
    if not layout:
        return "content"
    name = (layout.get("name") or "").lower()
    ltype = (layout.get("type") or "").lower()
    if "section" in name or ltype in ("sechead", "section"):
        return "section"
    if "quote" in name.lower() or "quote" in ltype:
        return "quote"
    if "conclusion" in name:
        return "closing"
    if ph_pictures(layout) and len(ph_bodies(layout)) <= 1:
        return "figure"
    if len(ph_bodies(layout)) >= 2:
        return "two-column"
    if ph_bodies(layout):
        return "content"
    if ph_title(layout):
        return "title"
    return "content"


def layout_score(layout, role):
    name = (layout.get("name") or "").lower()
    ltype = (layout.get("type") or "").lower()
    title = ph_title(layout)
    subtitle = ph_subtitle(layout)
    bodies = ph_bodies(layout)
    pics = ph_pictures(layout)
    if role in ("title", "closing"):
        score = 0
        if ltype in ("title", "cover") or "title slide" in name or "cover" in name:
            score = 100
        elif name.startswith("title") and not bodies:
            score = 100
        elif title and subtitle:
            score = 60
        elif title:
            score = 20
        if subtitle:
            score += 20
        if pics:
            score -= 10
        if "quote" in name:
            score -= 60
        if role == "closing" and "conclusion" in name:
            score += 45
        return max(score, 0)
    if role == "section":
        if ltype in ("sechead", "section") or "section" in name:
            return 120
        if title and bodies and len(name) < 24:
            return 40
        return 0
    if role == "two-column":
        if title and len(bodies) >= 2:
            score = 100 + min(len(bodies), 4) * 5
            if "two content" in name:
                score += 25
            if "comparison" in name:
                score += 10
            return score
        return 0
    if role == "figure":
        if title and pics:
            score = 120
            if "picture with caption" in name:
                score += 12
            if "photo" in name:
                score += 8
            return score
        if title and bodies:
            return 50
        return 0
    if role == "quote":
        if "quote" in name or "quote" in ltype:
            return 130
        return 0
    # content / unknown
    if title and bodies:
        score = 90
        if "content" in name or ltype in ("obj", "body", "tx"):
            score += 20
        if "max" in name:
            score += 8
        if "section" in name or ltype in ("sechead", "section"):
            score -= 100
        for bad in ("agenda", "photo", "quote", "conclusion", "big number",
                    "statement", "comparison", "two content", "caption"):
            if bad in name:
                score -= 40
        return max(score, 0)
    if title:
        return 25
    return 0


def choose_layout(layouts, role, wanted=None, fallbacks=None):
    names = []
    if wanted:
        names.append(str(wanted))
    names.extend(str(x) for x in (fallbacks or []))
    for name in names:
        hit = layout_by_name(layouts, name)
        if hit is not None:
            return hit, None
    best, best_score = None, 0
    for layout in layouts:
        score = layout_score(layout, role)
        if score > best_score:
            best, best_score = layout, score
    if best is None:
        return None, f"no layout can serve role '{role}'"
    detail = None
    if wanted and best["name"].strip().lower() != str(wanted).strip().lower():
        detail = f"layout '{wanted}' not found; used '{best['name']}'"
    if detail is None and best_score < 60:
        detail = (f"template has no dedicated '{role}' layout; "
                  f"used '{best['name']}'")
    return best, detail


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------
def audit_template(path):
    path = Path(path)
    parts = read_parts(path)
    masters = sorted(n for n in parts if MASTER_RE.match(n))
    layouts = parse_layouts(parts)
    themes = parse_theme(parts)
    theme_part = master_theme_part(parts)
    size = slide_size(parts)
    notes_master = notes_master_part(parts)
    samples = ordered_slides(parts)
    errors, warnings = [], []
    if not masters:
        errors.append("template has no slide master")
    if not layouts:
        errors.append("template has no slide layout")
    if not themes:
        errors.append("template has no theme")
    if not size or not size.get("cx") or not size.get("cy"):
        errors.append("template has no readable slide size")
        size = None
    theme = themes.get(theme_part) or (themes[sorted(themes)[0]] if themes else {})
    fonts = sorted({face for group in (theme.get("fonts") or {}).values()
                    for face in group.values() if face})
    colors = (theme.get("colors") or {})
    body_areas = []
    for layout in layouts:
        bodies = ph_bodies(layout)
        area = sum((ph.get("box") or {}).get("cx", 0) *
                   (ph.get("box") or {}).get("cy", 0) for ph in bodies)
        body_areas.append(area)
        if not ph_title(layout) and layout["index"] != 48:
            warnings.append(f"layout '{layout['name']}' has no title placeholder")
    return {
        "ok": not errors, "path": str(path), "errors": errors,
        "warnings": warnings,
        "slide_size": size, "aspect_ratio": aspect_ratio(size),
        "masters": masters, "masters_count": len(masters),
        "layouts": layouts, "layout_count": len(layouts),
        "themes": {name: {"name": data.get("name"), "fonts": data.get("fonts"),
                          "colors": data.get("colors")}
                   for name, data in themes.items()},
        "theme_part": theme_part,
        "theme_fonts": fonts,
        "theme_colors": sorted(set(colors.values())),
        "notes_master": notes_master,
        "sample_slides": samples, "sample_slide_count": len(samples),
        "media_count": sum(1 for n in parts if MEDIA_RE.match(n)),
        "layout_body_areas": body_areas,
    }


def notes_master_part(parts):
    for rel in rels_of(parts, "ppt/presentation.xml")[1]:
        if rel.get("Type", "").endswith("/notesMaster"):
            target = resolve_target("ppt", rel.get("Target", ""))
            if target in parts:
                return target
    for name in sorted(parts):
        if name.startswith("ppt/notesMasters/notesMaster") and name.endswith(".xml"):
            return name
    return None


# ---------------------------------------------------------------------------
# XML builders
# ---------------------------------------------------------------------------
def xfrm_xml(box):
    if not box:
        return ""
    return (f'<a:xfrm><a:off x="{int(box["x"])}" y="{int(box["y"])}"/>'
            f'<a:ext cx="{int(box["cx"])}" cy="{int(box["cy"])}"/></a:xfrm>')


def paragraph_xml(paras, default_size, bold_default=False, bullet=False,
                  align=None, lang="zh-CN"):
    out = []
    for para in paras or []:
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
            color = str(color).lstrip("#")
            if re.fullmatch(r"[0-9A-Fa-f]{6}", color):
                fill = f'<a:solidFill><a:srgbClr val="{color.upper()}"/></a:solidFill>'
            else:
                fill = f'<a:solidFill><a:schemeClr val="{escape(str(color))}"/></a:solidFill>'
        bold_attr = ' b="1"' if bold else ""
        out.append(
            f'<a:p>{ppr}<a:r><a:rPr lang="{escape(lang)}" sz="{size}"{bold_attr}>'
            f'{fill}</a:rPr><a:t>{escape(text)}</a:t></a:r></a:p>')
    if not out:
        out.append('<a:p><a:endParaRPr lang="zh-CN"/></a:p>')
    return "".join(out)


def placeholder_shape(sid, ph, paras, size, bullet=False, box=None,
                      bold=False, anchor=None, lang="zh-CN"):
    idx = ph.get("idx")
    ptype = ph.get("type")
    attrs = []
    if ptype in ("title", "ctrTitle", "subTitle", "pic"):
        attrs.append(f'type="{ptype}"')
        if ptype in ("subTitle", "pic") and idx is not None:
            attrs.append(f'idx="{idx}"')
    elif idx is not None:
        attrs.append(f'idx="{idx}"')
    ph_attrs = (" " + " ".join(attrs)) if attrs else ""
    name = escape(ph.get("name") or "Placeholder")
    sp_pr = f"<p:spPr>{xfrm_xml(box)}</p:spPr>" if box else "<p:spPr/>"
    body_pr = f'<a:bodyPr anchor="{anchor}"/>' if anchor else "<a:bodyPr/>"
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/>'
            f'<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            f'<p:nvPr><p:ph{ph_attrs}/></p:nvPr></p:nvSpPr>{sp_pr}'
            f'<p:txBody>{body_pr}<a:lstStyle/>'
            f'{paragraph_xml(paras, size, bold, bullet, lang=lang)}</p:txBody></p:sp>')


def free_text_shape(sid, name, box, paras, size, bold=False, anchor="t",
                    lang="zh-CN"):
    sp_pr = (f"<p:spPr>{xfrm_xml(box)}<a:prstGeom prst=\"rect\">"
             "<a:avLst/></a:prstGeom></p:spPr>") if box else "<p:spPr/>"
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{escape(name)}"/>'
            f'<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>{sp_pr}'
            f'<p:txBody><a:bodyPr wrap="square" anchor="{anchor}"/><a:lstStyle/>'
            f'{paragraph_xml(paras, size, bold, lang=lang)}</p:txBody></p:sp>')


def picture_shape(sid, rid, box, name="Picture", ph=None):
    nv_pr = ""
    if ph is not None:
        nv_pr = f'<p:ph type="pic" idx="{ph.get("idx", "0")}"/>'
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="{sid}" name="{escape(name)}"/>'
            '<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>'
            f'<p:nvPr>{nv_pr}</p:nvPr>'
            f'</p:nvPicPr><p:blipFill><a:blip r:embed="{rid}"/>'
            '<a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            f'<p:spPr>{xfrm_xml(box)}<a:prstGeom prst="rect"><a:avLst/>'
            '</a:prstGeom></p:spPr></p:pic>')


def group_props():
    return ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
            '</p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/>'
            '<a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
            '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


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
    if not src or not box:
        return dict(box or {})
    sw, sh = src
    if sw <= 0 or sh <= 0:
        return dict(box)
    scale = min(box["cx"] / sw, box["cy"] / sh)
    width, height = int(sw * scale), int(sh * scale)
    return {"x": int(box["x"] + (box["cx"] - width) / 2),
            "y": int(box["y"] + (box["cy"] - height) / 2),
            "cx": width, "cy": height}


def notes_xml(text):
    lines = [line for line in str(text).split("\n")] or [""]
    paras = [{"text": line, "size": 12} for line in lines]
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:notes xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
            '<p:cSld><p:spTree>' + group_props() +
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/>'
            '</p:cNvSpPr><p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr>'
            '</p:nvSpPr><p:spPr/></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="body" idx="3"/></p:nvPr></p:nvSpPr><p:spPr/>'
            f'<p:txBody><a:bodyPr/><a:lstStyle/>{paragraph_xml(paras, 12)}'
            '</p:txBody></p:sp></p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>')


def rels_xml(entries):
    body = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{g}"/>'
                   for i, t, g in entries)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{PKG_NS}">{body}</Relationships>')


# ---------------------------------------------------------------------------
# content types
# ---------------------------------------------------------------------------
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
    for pattern, value in patterns:
        if re.match(pattern, name):
            return value
    if name.endswith(".xml"):
        return "application/xml"
    return None


DEFAULT_TYPES = {
    "rels": "application/vnd.openxmlformats-package.relationships+xml",
    "xml": "application/xml", "png": "image/png", "jpeg": "image/jpeg",
    "jpg": "image/jpeg", "gif": "image/gif", "svg": "image/svg+xml",
    "emf": "image/x-emf", "wmf": "image/x-wmf",
    "mp4": "video/mp4", "m4a": "audio/mp4", "mp3": "audio/mpeg",
    "wav": "audio/wav",
}


def ensure_content_types(parts):
    raw = parts.get("[Content_Types].xml")
    if not raw:
        lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">']
        used = {n.rsplit(".", 1)[-1].lower() for n in parts if "." in n}
        for ext, value in DEFAULT_TYPES.items():
            if ext in used:
                lines.append(f'<Default Extension="{ext}" ContentType="{value}"/>')
        for name in sorted(parts):
            if name == "[Content_Types].xml" or name.endswith(".rels"):
                continue
            value = content_type_for(name)
            if value:
                lines.append(f'<Override PartName="/{name}" ContentType="{value}"/>')
        lines.append("</Types>")
        parts["[Content_Types].xml"] = "".join(lines).encode("utf-8")
        return

    text = raw.decode("utf-8", "replace")

    def keep_override(match):
        part = match.group(1).lstrip("/")
        return match.group(0) if part in parts else ""

    text = re.sub(r'<Override\b[^>]*PartName="([^"]+)"[^>]*/>',
                  keep_override, text)
    for name in sorted(parts):
        if name == "[Content_Types].xml" or name.endswith(".rels"):
            continue
        value = content_type_for(name)
        if value and f'PartName="/{name}"' not in text:
            text = text.replace(
                "</Types>", f'<Override PartName="/{name}" ContentType="{value}"/></Types>')
    used = {n.rsplit(".", 1)[-1].lower() for n in parts if "." in n}
    for ext, value in DEFAULT_TYPES.items():
        if ext in used and f'Extension="{ext}"' not in text:
            text = text.replace(
                "</Types>", f'<Default Extension="{ext}" ContentType="{value}"/></Types>')
    # A .potx input must become an editable .pptx delivery.
    presentation_type = CT + ".presentationml.presentation.main+xml"
    if re.search(r'<Override\b[^>]*PartName="/ppt/presentation\.xml"[^>]*/>', text):
        text = re.sub(
            r'<Override\b[^>]*PartName="/ppt/presentation\.xml"[^>]*/>',
            f'<Override PartName="/ppt/presentation.xml" '
            f'ContentType="{presentation_type}"/>', text)
    parts["[Content_Types].xml"] = text.encode("utf-8")


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
        raw = parts[name].decode("utf-8", "replace")
        for match in REL_RE.finditer(raw):
            attrs = dict(ATTR_RE.findall(match.group(1)))
            if attrs.get("TargetMode") == "External":
                continue
            target = resolve_target(folder, attrs.get("Target", ""))
            if target and target not in parts:
                problems.append(f"{name}: dangling target {target}")
    return (not problems), problems


# ---------------------------------------------------------------------------
# deck spec -> new deck on a real template
# ---------------------------------------------------------------------------
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


def remove_existing_slides(parts):
    removed = 0
    for name in list(parts):
        if (SLIDE_RE.match(name) or NOTES_RE.match(name) or
                name.startswith("ppt/slides/_rels/") or
                name.startswith("ppt/notesSlides/_rels/") or
                name.startswith("ppt/comments/")):
            del parts[name]
            removed += 1
    rels_name = "ppt/_rels/presentation.xml.rels"
    raw = parts.get(rels_name, b"").decode("utf-8", "replace")
    if raw:
        def keep(match):
            attrs = match.group(1)
            typ = re.search(r'Type="([^"]*)"', attrs)
            value = typ.group(1) if typ else ""
            if value.endswith(("/slide", "/notesSlide", "/comments", "/commentAuthors")):
                return ""
            return match.group(0)
        parts[rels_name] = REL_RE.sub(keep, raw).encode("utf-8")
    return removed


def build_slide_xml(slide_spec, layout, role_cfg, parts, media_state, sizes,
                    lang="zh-CN"):
    role = slide_spec.get("role", "content")
    shapes, sid, used = [], 2, []
    title_ph = ph_title(layout)
    subtitle_ph = ph_subtitle(layout)
    bodies = ph_bodies(layout)
    pics = ph_pictures(layout)
    title_size = float((role_cfg or {}).get("title_size", sizes.get("title", 32)))
    body_size = float((role_cfg or {}).get("body_size", sizes.get("body", 18)))
    caption_size = float(sizes.get("caption", 12))
    footer_size = float(sizes.get("footer", 10))
    slide_cx = media_state["slide_cx"]
    slide_cy = media_state["slide_cy"]
    margin = int(slide_cx * 0.055)

    if title_ph is not None and slide_spec.get("title") is not None:
        shapes.append(placeholder_shape(
            sid, title_ph,
            [{"text": slide_spec.get("title", ""), "size": title_size,
              "bold": True, "color": "tx1"}],
            title_size, bullet=False, bold=True, lang=lang))
        used.append(title_ph["idx"])
        sid += 1

    def add_subtitle(ph=None):
        nonlocal sid
        text = slide_spec.get("subtitle")
        if text is None:
            return
        target = ph or subtitle_ph or (bodies[0] if bodies else None)
        if target is None:
            return
        shapes.append(placeholder_shape(
            sid, target, [{"text": text, "size": body_size, "color": "tx2"}],
            body_size, bullet=False, lang=lang))
        used.append(target["idx"])
        sid += 1

    def add_image(box, image_path, caption=None, ph=None):
        nonlocal sid
        media_name, pixels = add_media(parts, image_path, media_state["names"])
        rid = f"rIdImg{sid}"
        media_state["images"].append((rid, media_name))
        shapes.append(picture_shape(sid, rid, fit_box(pixels, box),
                                    Path(image_path).name, ph=ph))
        sid += 1
        if caption:
            cap_box = {"x": box["x"], "y": box["y"] + box["cy"] + int(0.03 * slide_cy),
                       "cx": box["cx"], "cy": int(slide_cy * 0.08)}
            shapes.append(free_text_shape(
                sid, "Caption", cap_box,
                [{"text": caption, "size": caption_size, "color": "tx2"}],
                caption_size, lang=lang))
            sid += 1

    def body_paras():
        bullets = slide_spec.get("bullets") or slide_spec.get("body") or []
        if isinstance(bullets, str):
            bullets = [line for line in bullets.split("\n") if line.strip()]
        out = []
        for item in bullets:
            if isinstance(item, dict):
                out.append(item)
            else:
                out.append({"text": str(item)})
        return out

    if role in ("title", "closing"):
        add_subtitle()
        if not slide_spec.get("subtitle") and body_paras() and bodies:
            shapes.append(placeholder_shape(
                sid, bodies[0], body_paras(), body_size, bullet=True, lang=lang))
            used.append(bodies[0]["idx"])
            sid += 1
    elif role == "section":
        add_subtitle()
        if bodies and body_paras():
            shapes.append(placeholder_shape(
                sid, bodies[0], body_paras(), body_size, bullet=True, lang=lang))
            used.append(bodies[0]["idx"])
            sid += 1
    elif role == "quote":
        if bodies:
            shapes.append(placeholder_shape(
                sid, bodies[0],
                [{"text": slide_spec.get("subtitle") or slide_spec.get("author")
                  or "", "size": body_size, "color": "tx2"}],
                body_size, bullet=False, lang=lang))
            used.append(bodies[0]["idx"])
            sid += 1
    elif role == "figure":
        box = (pics[0].get("box") if pics else None) or {
            "x": margin, "y": int(slide_cy * 0.20),
            "cx": slide_cx - 2 * margin, "cy": int(slide_cy * 0.62)}
        if slide_spec.get("image"):
            add_image(box, slide_spec["image"], None,
                      ph=pics[0] if pics else None)
            cap_size = caption_size if pics else body_size
            if slide_spec.get("caption") and bodies:
                shapes.append(placeholder_shape(
                    sid, bodies[0],
                    [{"text": slide_spec["caption"], "size": cap_size,
                      "color": "tx2"}], cap_size, bullet=False, lang=lang))
                used.append(bodies[0]["idx"])
                sid += 1
        elif bodies:
            shapes.append(placeholder_shape(
                sid, bodies[0], body_paras(), body_size, bullet=True, lang=lang))
            used.append(bodies[0]["idx"])
            sid += 1
    elif role == "two-column":
        left = body_paras()
        right = slide_spec.get("bullets_right") or []
        if isinstance(right, str):
            right = [line for line in right.split("\n") if line.strip()]
        if bodies:
            shapes.append(placeholder_shape(
                sid, bodies[0], left, body_size, bullet=True, lang=lang))
            used.append(bodies[0]["idx"])
            sid += 1
        if len(bodies) > 1:
            if slide_spec.get("image") and not right:
                box = bodies[1].get("box") or {
                    "x": int(slide_cx * 0.52), "y": int(slide_cy * 0.22),
                    "cx": int(slide_cx * 0.42), "cy": int(slide_cy * 0.62)}
                add_image(box, slide_spec["image"], slide_spec.get("caption"))
            else:
                shapes.append(placeholder_shape(
                    sid, bodies[1], right, body_size, bullet=True, lang=lang))
                used.append(bodies[1]["idx"])
                sid += 1
        elif right:
            shapes.append(free_text_shape(
                sid, "Right column",
                {"x": int(slide_cx * 0.52), "y": int(slide_cy * 0.24),
                 "cx": int(slide_cx * 0.42), "cy": int(slide_cy * 0.60)},
                right, body_size, lang=lang))
            sid += 1
    else:  # content
        paras = body_paras()
        if slide_spec.get("image") and bodies:
            body_box = bodies[0].get("box") or {
                "x": margin, "y": int(slide_cy * 0.22),
                "cx": slide_cx - 2 * margin, "cy": int(slide_cy * 0.64)}
            left = {"x": body_box["x"], "y": body_box["y"],
                    "cx": int(body_box["cx"] * 0.54), "cy": body_box["cy"]}
            right = {"x": body_box["x"] + body_box["cx"] - int(body_box["cx"] * 0.40),
                     "y": body_box["y"], "cx": int(body_box["cx"] * 0.40),
                     "cy": body_box["cy"]}
            shapes.append(placeholder_shape(
                sid, bodies[0], paras, body_size, bullet=True, box=left, lang=lang))
            used.append(bodies[0]["idx"])
            sid += 1
            add_image(right, slide_spec["image"], slide_spec.get("caption"))
        elif bodies:
            shapes.append(placeholder_shape(
                sid, bodies[0], paras, body_size, bullet=True, lang=lang))
            used.append(bodies[0]["idx"])
            sid += 1
        elif paras:
            shapes.append(free_text_shape(
                sid, "Content",
                {"x": margin, "y": int(slide_cy * 0.22),
                 "cx": slide_cx - 2 * margin, "cy": int(slide_cy * 0.68)},
                paras, body_size, lang=lang))
            sid += 1
        if slide_spec.get("image") and not bodies:
            add_image({"x": int(slide_cx * 0.28), "y": int(slide_cy * 0.40),
                       "cx": int(slide_cx * 0.64), "cy": int(slide_cy * 0.46)},
                      slide_spec["image"], slide_spec.get("caption"))

    footer_ph = ph_footer(layout)
    if slide_spec.get("footer"):
        if footer_ph is not None:
            shapes.append(placeholder_shape(
                sid, footer_ph,
                [{"text": slide_spec["footer"], "size": footer_size, "color": "tx2"}],
                footer_size, bullet=False, lang=lang))
            used.append(footer_ph["idx"])
        else:
            shapes.append(free_text_shape(
                sid, "Footer",
                {"x": margin, "y": int(slide_cy * 0.93),
                 "cx": slide_cx - 2 * margin, "cy": int(slide_cy * 0.05)},
                [{"text": slide_spec["footer"], "size": footer_size, "color": "tx2"}],
                footer_size, lang=lang))
        sid += 1

    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<p:sld xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">'
           f'<p:cSld><p:spTree>{group_props()}{"".join(shapes)}</p:spTree></p:cSld>'
           '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
    return xml, used


def build_from_spec(template_path, spec, format_spec, out_path):
    parts = read_parts(template_path)
    original_order = list(parts)
    masters = [n for n in parts if MASTER_RE.match(n)]
    themes = [n for n in parts if THEME_RE.match(n)]
    if not masters or not themes:
        raise ValueError("template has no slide master or theme")
    layouts = parse_layouts(parts)
    if not layouts:
        raise ValueError("template has no slide layout")
    fmt_roles = (format_spec or {}).get("roles", {}) or {}
    typo = (format_spec or {}).get("typography", {}) or {}
    lang = (format_spec or {}).get("language") or "zh-CN"
    sizes = {"title": typo.get("title_size", 32),
             "body": typo.get("body_size", 18),
             "caption": typo.get("caption_size", 12),
             "footer": typo.get("footer_size", 10)}
    size = slide_size(parts)
    slide_cx, slide_cy = size["cx"], size["cy"]
    master_notes = notes_master_part(parts)
    removed = remove_existing_slides(parts)
    _, presentation_rels = rels_of(parts, "ppt/presentation.xml")
    new_rels = list(presentation_rels)
    prefix = "rIdSlide"
    while any(str(r.get("Id", "")).startswith(prefix) for r in presentation_rels):
        prefix += "X"
    media_state = {"names": set(), "images": [], "slide_cx": slide_cx,
                   "slide_cy": slide_cy}

    mapping, warnings, uncertainty = [], [], []
    slides = spec.get("slides", [])
    for index, slide_spec in enumerate(slides, 1):
        role = slide_spec.get("role", "content")
        role_cfg = dict(fmt_roles.get(role) or {})
        explicit = slide_spec.get("layout")
        if explicit:
            role_cfg["layout"] = explicit
        layout, fallback = choose_layout(
            layouts, role, role_cfg.get("layout"), role_cfg.get("fallback_layouts"))
        if layout is None:
            raise ValueError(f"slide {index}: no layout for role '{role}'")
        if fallback:
            warnings.append(f"slide {index}: {fallback}")
            uncertainty.append({"slide": index, "issue": "layout_fallback",
                                "detail": fallback})
        if role == "two-column" and len(ph_bodies(layout)) < 2:
            detail = (f"two-column role uses '{layout['name']}' with "
                      f"{len(ph_bodies(layout))} body placeholder(s); right "
                      "column merged or placed as free text")
            warnings.append(f"slide {index}: {detail}")
            uncertainty.append({"slide": index, "issue": "column_collapse",
                                "detail": detail})
        if role == "figure" and not ph_pictures(layout):
            detail = (f"figure role uses '{layout['name']}' without a picture "
                      "placeholder; image placed in the body area")
            warnings.append(f"slide {index}: {detail}")
            uncertainty.append({"slide": index, "issue": "picture_placeholder_missing",
                                "detail": detail})
        if (role in ("title", "closing") and slide_spec.get("subtitle") and
                ph_subtitle(layout) is None):
            detail = (f"layout '{layout['name']}' has no subtitle placeholder; "
                      "subtitle written into the body placeholder")
            warnings.append(f"slide {index}: {detail}")
            uncertainty.append({"slide": index, "issue": "subtitle_in_body",
                                "detail": detail})
        media_state["images"] = []
        slide_xml, used = build_slide_xml(
            slide_spec, layout, role_cfg, parts, media_state, sizes, lang)
        slide_part = f"ppt/slides/slide{index}.xml"
        parts[slide_part] = slide_xml.encode("utf-8")
        rel_entries = [("rId1", P_NS + "/slideLayout",
                        f"../slideLayouts/{layout['part'].split('/')[-1]}")]
        for rid, media_name in media_state["images"]:
            rel_entries.append((rid, R_NS + "/image", f"../media/{media_name}"))
        notes = str(slide_spec.get("notes") or "").strip()
        if notes and master_notes:
            notes_part = f"ppt/notesSlides/notesSlide{index}.xml"
            parts[notes_part] = notes_xml(notes).encode("utf-8")
            parts[f"ppt/notesSlides/_rels/notesSlide{index}.xml.rels"] = rels_xml([
                ("rIdSlide", P_NS + "/slide", f"../slides/slide{index}.xml"),
                ("rIdNotesMaster", P_NS + "/notesMaster",
                 f"../notesMasters/{master_notes.split('/')[-1]}"),
            ]).encode("utf-8")
            rel_entries.append(("rIdNotes", P_NS + "/notesSlide",
                                f"../notesSlides/notesSlide{index}.xml"))
        elif notes:
            warnings.append(f"slide {index}: template has no notes master; notes omitted")
            uncertainty.append({"slide": index, "issue": "notes_master_missing",
                                "detail": "speaker notes could not be attached"})
        parts[f"ppt/slides/_rels/slide{index}.xml.rels"] = rels_xml(rel_entries).encode("utf-8")
        mapping.append({"slide": index, "role": role, "layout": layout["name"],
                        "layout_part": layout["part"], "placeholders_used": used,
                        "fallback": fallback})
        new_rels.append({"Id": f"{prefix}{index}", "Type": P_NS + "/slide",
                         "Target": f"slides/slide{index}.xml"})
        parts["ppt/_rels/presentation.xml.rels"] = rels_xml(
            [(r.get("Id"), r.get("Type"), r.get("Target")) for r in new_rels]
        ).encode("utf-8")

    presentation = parts["ppt/presentation.xml"].decode("utf-8", "replace")
    id_list = "".join(
        f'<p:sldId id="{256 + i}" r:id="{prefix}{i + 1}"/>'
        for i in range(len(slides)))
    new_list = f"<p:sldIdLst>{id_list}</p:sldIdLst>"
    if "<p:sldIdLst>" in presentation:
        presentation = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>", new_list,
                              presentation, flags=re.S)
    elif "<p:sldIdLst/>" in presentation:
        presentation = presentation.replace("<p:sldIdLst/>", new_list)
    else:
        presentation = re.sub(r"(<p:sldSz\b[^>]*/>)", new_list + r"\1", presentation)
    parts["ppt/presentation.xml"] = presentation.encode("utf-8")
    if "docProps/app.xml" in parts:
        app = parts["docProps/app.xml"].decode("utf-8", "replace")
        app = re.sub(r"<Slides>\d+</Slides>", f"<Slides>{len(slides)}</Slides>", app)
        parts["docProps/app.xml"] = app.encode("utf-8")
    ensure_content_types(parts)
    ok, problems = package_ok(parts)
    if not ok:
        raise ValueError("built package is invalid: " + "; ".join(problems[:6]))
    write_parts(parts, out_path, original_order)
    return {
        "ok": True, "mode": "build", "status": "completed",
        "template": str(template_path), "out": str(out_path),
        "fidelity": "master+layouts+theme",
        "slide_count": len(slides),
        "slide_size": {"cx": slide_cx, "cy": slide_cy},
        "layouts_available": [layout["name"] for layout in layouts],
        "layout_map": mapping, "warnings": warnings,
        "uncertainty": uncertainty,
        "checks": {"zip_valid": True, "xml_valid": True,
                   "relationships_resolved": True,
                   "sample_slides_removed": removed > 0,
                   "sample_slides_removed_count": removed},
        "change_log": [
            "removed template sample slides",
            f"created {len(slides)} slides using {len({m['layout'] for m in mapping})} real layouts",
        ],
    }


# ---------------------------------------------------------------------------
# existing deck -> target template (content preserved)
# ---------------------------------------------------------------------------
def slide_layout(parts, slide_part):
    for rel in rels_of(parts, slide_part)[1]:
        if rel.get("Type", "").endswith("/slideLayout"):
            target = resolve_target(slide_part.rsplit("/", 1)[0],
                                    rel.get("Target", ""))
            if target in parts:
                return target
    return None


def slide_notes_text(parts, slide_part):
    notes_part = None
    for rel in rels_of(parts, slide_part)[1]:
        if rel.get("Type", "").endswith("/notesSlide"):
            notes_part = resolve_target(slide_part.rsplit("/", 1)[0],
                                        rel.get("Target", ""))
            break
    if not notes_part or notes_part not in parts:
        return ""
    root = parse_xml(parts[notes_part], notes_part)
    chunks = []
    for sp in root.findall(".//p:sp", NS):
        ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is not None and (ph.get("type") or "") in ("sldNum", "sldImg"):
            continue
        text = xml_text(sp)
        if text.strip():
            chunks.append(text)
    return "\n".join(chunks)


def extract_slide_content(parts, slide_part):
    root = parse_xml(parts[slide_part], slide_part)
    title, subtitle, bodies, free = "", "", [], []
    for sp in root.findall(".//p:sp", NS):
        ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", NS)
        text = xml_text(sp).strip()
        if not text:
            continue
        ptype = ph.get("type") if ph is not None else None
        if ptype in TITLE_TYPES:
            title = text
        elif ptype == "subTitle":
            subtitle = text
        elif ptype in BODY_TYPES:
            bodies.append(text)
        elif ptype not in CONTROL_PH:
            free.append(text)
    for frame in root.findall(".//p:graphicFrame", NS):
        text = xml_text(frame).strip()
        if text:
            bodies.append(text)
    return {"title": title, "subtitle": subtitle, "bodies": bodies,
            "free": free, "notes": slide_notes_text(parts, slide_part)}


def _ph_attrs(raw):
    return dict(ATTR_RE.findall(raw or ""))


def align_placeholders(slide_xml, layout):
    """Make slide placeholders reference real slots of the target layout.

    Only ``p:ph`` attributes are changed; text, pictures, notes and formatting
    remain untouched.  Unmatched placeholders are turned into free shapes
    (their content is still preserved).
    """
    layout_phs = [ph for ph in layout.get("placeholders", [])
                  if ph["type"] not in CONTROL_PH]
    used_slots = set()
    changes, unmatched = [], []

    def pick(current):
        ptype = current.get("type") or ""
        idx = current.get("idx") or "0"
        if not ptype:
            # OOXML allows a slide placeholder to inherit its type from the
            # layout slot with the same idx; do not free such shapes.
            for slot in layout_phs:
                key = (slot["type"], slot["idx"])
                if key in used_slots:
                    continue
                if str(slot["idx"]) == str(idx):
                    used_slots.add(key)
                    return slot
        for slot in layout_phs:
            key = (slot["type"], slot["idx"])
            if key in used_slots:
                continue
            if slot["type"] == ptype and slot["idx"] == idx:
                used_slots.add(key)
                return slot
        family = None
        if ptype in TITLE_TYPES:
            family = TITLE_TYPES
        elif ptype == "subTitle":
            family = {"subTitle"}
        elif ptype in BODY_TYPES:
            family = BODY_TYPES
        elif ptype == "pic":
            family = {"pic"}
        if family:
            for slot in layout_phs:
                key = (slot["type"], slot["idx"])
                if key in used_slots or slot["type"] not in family:
                    continue
                used_slots.add(key)
                return slot
        return None

    def rewrite(match):
        current = _ph_attrs(match.group(1))
        slot = pick(current)
        if slot is None:
            unmatched.append(current)
            return ""
        attrs = []
        if slot["type"] in ("title", "ctrTitle", "subTitle", "pic"):
            attrs.append(f'type="{slot["type"]}"')
            if slot["type"] in ("subTitle", "pic") and slot.get("idx") is not None:
                attrs.append(f'idx="{slot["idx"]}"')
        elif slot.get("idx") is not None:
            attrs.append(f'idx="{slot["idx"]}"')
        new = (" " + " ".join(attrs)) if attrs else ""
        if new.strip() != (match.group(1) or "").strip():
            changes.append({"from": current, "to": slot})
        return f"<p:ph{new}/>"

    new_xml = PH_RE.sub(rewrite, slide_xml)
    return new_xml, changes, unmatched


def copy_template_structure(deck, template):
    """Replace deck masters/layouts/theme with the template's real parts."""
    for name in list(deck):
        if name.startswith(("ppt/slideMasters/", "ppt/slideLayouts/")):
            del deck[name]
    rename = {}
    for name, raw in template.items():
        if name.startswith(("ppt/slideMasters/", "ppt/slideLayouts/", "ppt/theme/")):
            deck[name] = raw
        elif name.startswith("ppt/notesMasters/") and name not in deck:
            deck[name] = raw
        elif name.startswith("ppt/media/"):
            target = name
            if name in deck and deck[name] != raw:
                base = name.rsplit("/", 1)[1]
                target = f"ppt/media/tpl_{base}"
                n = 1
                while target in deck:
                    target = f"ppt/media/tpl{n}_{base}"
                    n += 1
                rename[base] = target.rsplit("/", 1)[1]
            if target not in deck:
                deck[target] = raw
    if rename:
        for name in list(deck):
            if not name.startswith(("ppt/slideMasters/", "ppt/slideLayouts/",
                                    "ppt/notesMasters/", "ppt/theme/")):
                continue
            if not (name.endswith(".xml") or name.endswith(".rels")):
                continue
            raw = deck[name].decode("utf-8", "replace")
            for old, new in rename.items():
                raw = raw.replace(f"../media/{old}", f"../media/{new}")
                raw = raw.replace(f"/media/{old}", f"/media/{new}")
            deck[name] = raw.encode("utf-8")
    return rename


def set_presentation_master(deck, template):
    target_master = None
    for name in sorted(template):
        if MASTER_RE.match(name):
            target_master = name
            break
    if not target_master:
        raise ValueError("target template has no slide master")
    target_master = "ppt/slideMasters/" + target_master.rsplit("/", 1)[1]
    if target_master not in deck:
        raise ValueError("target master part was not copied")
    _, rels = rels_of(deck, "ppt/presentation.xml")
    master_rid = None
    kept = []
    for rel in rels:
        if rel.get("Type", "").endswith("/slideMaster"):
            master_rid = master_rid or rel.get("Id")
            continue
        kept.append(rel)
    if not master_rid:
        used = {r.get("Id") for r in rels}
        master_rid = "rIdMaster"
        n = 1
        while master_rid in used:
            master_rid = f"rIdMaster{n}"
            n += 1
    kept.append({"Id": master_rid, "Type": P_NS + "/slideMaster",
                 "Target": "slideMasters/" + target_master.rsplit("/", 1)[1]})
    deck["ppt/_rels/presentation.xml.rels"] = rels_xml(
        [(r.get("Id"), r.get("Type"), r.get("Target")) for r in kept]
    ).encode("utf-8")
    presentation = deck["ppt/presentation.xml"].decode("utf-8", "replace")
    master_list = (f'<p:sldMasterIdLst><p:sldMasterId id="2147483648" '
                   f'r:id="{master_rid}"/></p:sldMasterIdLst>')
    if "<p:sldMasterIdLst>" in presentation:
        presentation = re.sub(r"<p:sldMasterIdLst>.*?</p:sldMasterIdLst>",
                              master_list, presentation, flags=re.S)
    else:
        presentation = re.sub(r"(<p:sldIdLst>)", master_list + r"\1",
                              presentation)
    template_pres = template.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    size_tag = re.search(r"<p:sldSz\b[^>]*/>", template_pres)
    if size_tag:
        if re.search(r"<p:sldSz\b[^>]*/>", presentation):
            presentation = re.sub(r"<p:sldSz\b[^>]*/>", size_tag.group(0), presentation)
        else:
            presentation = re.sub(r"(<p:sldMasterIdLst>)", size_tag.group(0) + r"\1",
                                  presentation)
    deck["ppt/presentation.xml"] = presentation.encode("utf-8")
    return master_rid


def replace_text_in_slide(xml, old, new):
    """Replace text inside a:t; fall back to paragraph-level run merging."""
    if not old:
        return 0, xml
    old_esc, new_esc = escape(old), escape(new)
    count = 0

    def direct(match):
        nonlocal count
        text = match.group(2)
        if old_esc in text:
            count += text.count(old_esc)
            text = text.replace(old_esc, new_esc)
        return match.group(1) + text + match.group(3)

    xml = T_RE.sub(direct, xml)
    if count:
        return count, xml

    def paragraph(match):
        nonlocal count
        block = match.group(0)
        runs = list(T_RE.finditer(block))
        if not runs:
            return block
        joined = "".join(r.group(2) for r in runs)
        if old_esc not in joined:
            return block
        replaced = joined.replace(old_esc, new_esc)
        count += joined.count(old_esc)
        pieces, cursor = [], 0
        for i, run in enumerate(runs):
            start, end = run.start() - match.start(), run.end() - match.start()
            pieces.append(block[cursor:start])
            body = replaced if i == 0 else ""
            pieces.append(run.group(1) + body + run.group(3))
            cursor = end
        pieces.append(block[cursor:])
        return "".join(pieces)

    xml = P_RE.sub(paragraph, xml)
    return count, xml


def set_placeholder_text(parts, slide_part, kinds, text):
    root = parse_xml(parts[slide_part], slide_part)
    changed = False
    for sp in root.findall(".//p:sp", NS):
        ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is None or (ph.get("type") or "") not in kinds:
            continue
        tx = sp.find("p:txBody", NS)
        if tx is None:
            continue
        for child in list(tx):
            if child.tag.endswith("}p"):
                tx.remove(child)
        tx.append(ET.fromstring(
            f'<a:p xmlns:a="{A_NS}"><a:r><a:rPr lang="zh-CN" sz="3200"/>'
            f'<a:t>{escape(text)}</a:t></a:r></a:p>'))
        changed = True
    if changed:
        parts[slide_part] = ET.tostring(root, encoding="utf-8",
                                        xml_declaration=True)
    return changed


def set_notes_text(parts, slide_part, text):
    for rel in rels_of(parts, slide_part)[1]:
        if not rel.get("Type", "").endswith("/notesSlide"):
            continue
        notes_part = resolve_target(slide_part.rsplit("/", 1)[0],
                                    rel.get("Target", ""))
        if notes_part not in parts:
            continue
        root = parse_xml(parts[notes_part], notes_part)
        for sp in root.findall(".//p:sp", NS):
            ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", NS)
            if ph is None or (ph.get("type") or "") != "body":
                continue
            tx = sp.find("p:txBody", NS)
            if tx is None:
                continue
            for child in list(tx):
                if child.tag.endswith("}p"):
                    tx.remove(child)
            for line in str(text).split("\n"):
                tx.append(ET.fromstring(
                    f'<a:p xmlns:a="{A_NS}"><a:r><a:rPr lang="zh-CN" sz="1200"/>'
                    f'<a:t>{escape(line)}</a:t></a:r></a:p>'))
        parts[notes_part] = ET.tostring(root, encoding="utf-8",
                                        xml_declaration=True)
        return True
    return False


def apply_edits(parts, edits):
    applied, unmatched = [], []
    slides = ordered_slides(parts)
    deleted = set()
    for edit in edits or []:
        edit_id = edit.get("edit_id") or edit.get("id") or ""
        try:
            number = int(edit.get("slide") or edit.get("slide_number") or 0)
        except (TypeError, ValueError):
            number = 0
        if number < 1 or number > len(slides):
            unmatched.append({"edit_id": edit_id, "slide": number,
                              "reason": "slide out of range"})
            continue
        slide_part = slides[number - 1]
        action = str(edit.get("action") or edit.get("op") or "replace").lower()
        old = edit.get("from", edit.get("old", edit.get("search", "")))
        new = edit.get("to", edit.get("new", edit.get("replacement", "")))
        recorded = {"edit_id": edit_id, "slide": number, "action": action}
        if action in ("replace", "replace_text", "substitute"):
            xml = parts[slide_part].decode("utf-8", "replace")
            count, updated = replace_text_in_slide(xml, str(old), str(new))
            if count:
                parts[slide_part] = updated.encode("utf-8")
                applied.append(dict(recorded, replacements=count))
            else:
                unmatched.append(dict(recorded, reason="text not found", from_=old))
        elif action in ("set_title", "title"):
            changed = set_placeholder_text(parts, slide_part, TITLE_TYPES,
                                           str(new or edit.get("text", "")))
            (applied if changed else unmatched).append(
                dict(recorded, reason="" if changed else "title placeholder not found"))
        elif action in ("set_body", "set_text", "body"):
            changed = set_placeholder_text(parts, slide_part, BODY_TYPES,
                                           str(new or edit.get("text", "")))
            (applied if changed else unmatched).append(
                dict(recorded, reason="" if changed else "body placeholder not found"))
        elif action in ("set_notes", "notes"):
            changed = set_notes_text(parts, slide_part,
                                     str(new or edit.get("text", "")))
            (applied if changed else unmatched).append(
                dict(recorded, reason="" if changed else "notes part not found"))
        elif action in ("delete", "remove"):
            deleted.add(number)
            applied.append(dict(recorded, replacements=0))
        else:
            unmatched.append(dict(recorded, reason=f"unsupported action '{action}'"))
    if deleted:
        slides = ordered_slides(parts)
        keep = [i for i in range(1, len(slides) + 1) if i not in deleted]
        for name in list(parts):
            if SLIDE_RE.match(name) or NOTES_RE.match(name):
                number = int((SLIDE_RE.match(name) or NOTES_RE.match(name)).group(1))
                if number in deleted:
                    del parts[name]
                    rels = name.rsplit("/", 1)
                    rel_name = f"{rels[0]}/_rels/{rels[1]}.rels"
                    parts.pop(rel_name, None)
        presentation = parts["ppt/presentation.xml"].decode("utf-8", "replace")
        ids = re.findall(r'<p:sldId\b[^>]*/>', presentation)
        kept_tags = [tag for i, tag in enumerate(ids, 1) if i in keep]
        new_list = "<p:sldIdLst>" + "".join(kept_tags) + "</p:sldIdLst>"
        presentation = re.sub(r"<p:sldIdLst>.*?</p:sldIdLst>", new_list,
                              presentation, flags=re.S)
        parts["ppt/presentation.xml"] = presentation.encode("utf-8")
        rels_name = "ppt/_rels/presentation.xml.rels"
        raw = parts[rels_name].decode("utf-8", "replace")
        removed_rids = []
        for i, tag in enumerate(ids, 1):
            if i in deleted:
                rid = re.search(r'r:id="([^"]+)"', tag)
                if rid:
                    removed_rids.append(rid.group(1))

        def keep_rel(match):
            rid = re.search(r'Id="([^"]+)"', match.group(1))
            return "" if rid and rid.group(1) in removed_rids else match.group(0)

        parts[rels_name] = REL_RE.sub(keep_rel, raw).encode("utf-8")
    return applied, unmatched


def retarget_deck(deck_path, template_path, layout_map, out_path, edits=None):
    deck = read_parts(deck_path)
    original_order = list(deck)
    template = read_parts(template_path)
    audit = audit_template(template_path)
    if not audit["ok"]:
        raise ValueError("template audit failed: " + "; ".join(audit["errors"]))
    layouts = audit["layouts"]
    deck_size = slide_size(deck)
    template_size = audit["slide_size"]
    copy_template_structure(deck, template)
    set_presentation_master(deck, template)
    slides = ordered_slides(deck)
    entries = (layout_map or {}).get("slides") or []
    warnings, uncertainty, mapping = [], [], []
    if len(entries) != len(slides):
        warnings.append(f"layout map has {len(entries)} entries for "
                        f"{len(slides)} slides; inferred missing roles")
        uncertainty.append({"issue": "layout_map_count_mismatch",
                            "detail": f"{len(entries)} entries vs {len(slides)} slides"})
    if deck_size and template_size and (deck_size["cx"], deck_size["cy"]) != (
            template_size["cx"], template_size["cy"]):
        warnings.append(
            f"deck slide size {deck_size['cx']}x{deck_size['cy']} differs from "
            f"template {template_size['cx']}x{template_size['cy']}; template size "
            "adopted, existing element geometry was not rescaled")
        uncertainty.append({"issue": "slide_size_changed",
                            "detail": "existing geometry kept at original EMU values"})

    for index, slide_part in enumerate(slides, 1):
        entry = entries[index - 1] if index - 1 < len(entries) else {}
        role = entry.get("role") or "content"
        wanted = entry.get("layout")
        layout, fallback = choose_layout(layouts, role, wanted)
        if layout is None:
            raise ValueError(f"slide {index}: no layout for role '{role}'")
        if fallback:
            warnings.append(f"slide {index}: {fallback}")
            uncertainty.append({"slide": index, "issue": "layout_fallback",
                                "detail": fallback})
        rels_name, rels = rels_of(deck, slide_part)
        raw = deck.get(rels_name, b"").decode("utf-8", "replace")
        target = f"../slideLayouts/{layout['part'].split('/')[-1]}"
        pattern = r'(<Relationship\b[^>]*Type="[^"]*/slideLayout"[^>]*Target=")[^"]*(")'
        if re.search(pattern, raw):
            raw = re.sub(pattern, r"\g<1>" + target + r"\g<2>", raw)
        else:
            rid = "rIdLayout"
            used = {rel.get("Id") for rel in rels}
            n = 1
            while rid in used:
                rid = f"rIdLayout{n}"
                n += 1
            raw = raw.replace(
                "</Relationships>",
                f'<Relationship Id="{rid}" Type="{P_NS}/slideLayout" '
                f'Target="{target}"/></Relationships>')
        deck[rels_name] = raw.encode("utf-8")
        xml = deck[slide_part].decode("utf-8", "replace")
        xml, changes, unmatched_ph = align_placeholders(xml, layout)
        deck[slide_part] = xml.encode("utf-8")
        if unmatched_ph:
            warnings.append(f"slide {index}: {len(unmatched_ph)} placeholder(s) "
                            "not present in target layout were kept as free shapes")
            uncertainty.append({"slide": index, "issue": "placeholder_freed",
                                "detail": unmatched_ph})
        mapping.append({"slide": index, "role": role, "layout": layout["name"],
                        "layout_part": layout["part"],
                        "previous_layout_part": entry.get("previous_layout_part"),
                        "placeholder_changes": changes,
                        "content_preserved": True})

    applied, unmatched_edits = apply_edits(deck, edits)
    if unmatched_edits:
        warnings.append(f"{len(unmatched_edits)} workorder edit(s) could not be applied")
    ensure_content_types(deck)
    ok, problems = package_ok(deck)
    if not ok:
        raise ValueError("retargeted package is invalid: " + "; ".join(problems[:6]))
    write_parts(deck, out_path, original_order)
    return {
        "ok": True, "mode": "retarget", "status": "completed",
        "template": str(template_path), "deck": str(deck_path),
        "out": str(out_path), "fidelity": "master+layouts+theme",
        "slide_count": len(ordered_slides(deck)),
        "slide_size": template_size,
        "layout_map": mapping,
        "edits": {"applied": applied, "unmatched": unmatched_edits},
        "content": {"preserved": True,
                    "slide_parts_untouched": len(slides) - len(applied)},
        "warnings": warnings, "uncertainty": uncertainty,
        "checks": {"zip_valid": True, "xml_valid": True,
                   "relationships_resolved": True},
        "change_log": [
            "replaced deck master/layouts/theme with target template parts",
            f"retargeted {len(slides)} slides onto real template layouts",
        ] + ([f"applied {len(applied)} workorder edit(s)"] if applied else []),
    }


# ---------------------------------------------------------------------------
# native preview renderer (honest when unavailable)
# ---------------------------------------------------------------------------
def _libreoffice_path():
    env = os.environ.get("SOFFICE_BIN")
    candidates = [env] if env else []
    candidates.extend(shutil.which(name) for name in
                      ("soffice", "libreoffice", "soffice.bin"))
    candidates.extend([
        "/usr/bin/soffice", "/usr/local/bin/soffice",
        "/opt/libreoffice/program/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ])
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def find_renderer():
    env = os.environ.get("OFFICE_RENDERER_BIN")
    if env and Path(env).is_file():
        return ("office-renderer" if "office-render" in Path(env).name.lower()
                else "libreoffice", str(env))
    tool = shutil.which("office-renderer")
    if tool:
        return "office-renderer", tool
    office = _libreoffice_path()
    return ("libreoffice", office) if office else (None, None)


def find_office():
    return find_renderer()[1]


def _run(cmd, timeout, env=None):
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout,
                              env=env)
        return proc.returncode, proc.stdout.decode("utf-8", "replace"), \
            proc.stderr.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"


def pdf_to_png(pdf_path, out_dir, timeout=120):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / "slide"
    for old in out_dir.glob("slide*.png"):
        old.unlink()
    tool = shutil.which("pdftoppm")
    if tool:
        code, _, err = _run([tool, "-png", "-r", "120", str(pdf_path),
                             str(prefix)], timeout)
        if code == 0:
            for path in sorted(out_dir.glob("slide-*.png")):
                number = re.search(r"(\d+)", path.stem)
                if number:
                    path.rename(out_dir / f"slide-{int(number.group(1)):02d}.png")
            return sorted(out_dir.glob("slide-*.png")), "pdftoppm"
    tool = shutil.which("pdftocairo")
    if tool:
        code, _, err = _run([tool, "-png", "-r", "120", str(pdf_path),
                             str(prefix)], timeout)
        if code == 0:
            for path in sorted(out_dir.glob("slide-*.png")):
                number = re.search(r"(\d+)", path.stem)
                if number:
                    path.rename(out_dir / f"slide-{int(number.group(1)):02d}.png")
            return sorted(out_dir.glob("slide-*.png")), "pdftocairo"
    tool = shutil.which("mutool")
    if tool:
        pattern = str(out_dir / "slide-%02d.png")
        code, _, _ = _run([tool, "draw", "-o", pattern, "-r", "120",
                           str(pdf_path)], timeout)
        if code == 0:
            return sorted(out_dir.glob("slide-*.png")), "mutool"
    tool = shutil.which("gs")
    if tool:
        pattern = str(out_dir / "slide-%02d.png")
        code, _, _ = _run([tool, "-sDEVICE=png16m", "-r120", "-o", pattern,
                           str(pdf_path)], timeout)
        if code == 0:
            return sorted(out_dir.glob("slide-*.png")), "gs"
    try:
        import fitz  # optional PyMuPDF
    except ImportError:
        return [], None
    try:
        document = fitz.open(str(pdf_path))
        paths = []
        for index, page in enumerate(document, 1):
            path = out_dir / f"slide-{index:02d}.png"
            page.get_pixmap(dpi=120).save(str(path))
            paths.append(path)
        document.close()
        return paths, "pymupdf"
    except Exception:
        return [], None


def render_preview(pptx_path, preview_dir, timeout=180):
    engine, office = find_renderer()
    preview_dir = Path(preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)
    report = {"engine": "none", "native": False, "unavailable": True,
              "pdf": None, "png": [], "limitation": "", "duration_seconds": 0}
    if not office:
        report["limitation"] = (
            "本机未找到 office-renderer / LibreOffice / soffice，未生成原生 "
            "PDF/PNG 预览；PPTX 已通过 OOXML 结构校验。请安装 office renderer "
            "后重跑以生成真实预览。")
        return report
    workdir = Path(tempfile.mkdtemp(prefix="deck-render-"))
    env = dict(os.environ)
    env.setdefault("HOME", str(workdir))
    started = __import__("time").time()
    target = workdir / "presentation.pdf"
    attempts = []
    if engine == "office-renderer":
        commands = [
            [office, "--input", str(Path(pptx_path).resolve()),
             "--output", str(target)],
            [office, str(Path(pptx_path).resolve()), str(target)],
        ]
        for command in commands:
            code, stdout, stderr = _run(command, timeout, env)
            attempts.append({"command": command[0], "exit": code})
            if code == 0 and target.is_file():
                break
    else:
        code, stdout, stderr = _run(
            [office, "--headless", "--norestore", "--convert-to", "pdf",
             "--outdir", str(workdir), str(Path(pptx_path).resolve())],
            timeout, env)
        attempts.append({"command": office, "exit": code})
        produced = sorted(workdir.glob("*.pdf"))
        if produced:
            target = produced[0]
    if not target.is_file() and engine == "office-renderer":
        fallback = _libreoffice_path()
        if fallback:
            code, stdout, stderr = _run(
                [fallback, "--headless", "--norestore", "--convert-to", "pdf",
                 "--outdir", str(workdir), str(Path(pptx_path).resolve())],
                timeout, env)
            attempts.append({"command": fallback, "exit": code})
            produced = sorted(workdir.glob("*.pdf"))
            if produced:
                target = produced[0]
                engine = "libreoffice"
    if not target.is_file():
        report["engine"] = engine
        report["attempts"] = attempts
        report["limitation"] = (f"{engine} renderer failed: "
                                f"{(stderr or stdout)[:300]}")
        shutil.rmtree(workdir, ignore_errors=True)
        return report
    data = target.read_bytes()
    if not data.startswith(b"%PDF") or len(data) < 1000:
        report["engine"] = engine
        report["attempts"] = attempts
        report["limitation"] = f"{engine} produced an invalid PDF; not used"
        shutil.rmtree(workdir, ignore_errors=True)
        return report
    pdf_path = preview_dir / "presentation.pdf"
    pdf_path.write_bytes(data)
    pngs, png_engine = pdf_to_png(pdf_path, preview_dir)
    shutil.rmtree(workdir, ignore_errors=True)
    report.update({
        "engine": (engine + "+" + png_engine) if png_engine else engine,
        "native": True, "unavailable": False,
        "pdf": str(pdf_path), "png": [str(path) for path in pngs],
        "pdf_bytes": len(data), "png_count": len(pngs),
        "attempts": attempts,
        "duration_seconds": round(__import__("time").time() - started, 2),
        "limitation": "" if pngs else (
            "原生 PDF 已生成；本机缺少 PDF→PNG 转换器，未生成逐页 PNG。"),
    })
    return report


# ---------------------------------------------------------------------------
# CLI (diagnostic only; main.py is the single entrypoint)
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="deckkit diagnostics")
    parser.add_argument("--audit")
    parser.add_argument("--check")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.audit:
        audit = audit_template(args.audit)
        print(json.dumps(audit, ensure_ascii=False, indent=2))
        return 0 if audit["ok"] else 1
    if args.check:
        parts = read_parts(args.check)
        ok, problems = package_ok(parts)
        print(json.dumps({"ok": ok, "problems": problems,
                          "slides": len(ordered_slides(parts))},
                         ensure_ascii=False, indent=2))
        return 0 if ok else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
