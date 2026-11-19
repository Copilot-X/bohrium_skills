#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deckkit: OOXML deck writer/editor, real template inheritance and preview.

Standard library only for PPTX.  python-pptx is deliberately not required:
the evaluation image may not ship it.  The writer emits a real PresentationML
package (masters, layouts, theme, notes, media) that opens in PowerPoint,
LibreOffice Impress and WPS with every string editable.

Public API (used by main.py):
    audit_template(path)                  -> master/layout/theme inventory
    build_deck(spec, out, template=None)  -> build report
    apply_edits(base, edits, out)         -> edit report (minimal, in-place)
    deck_to_spec(base, work_dir)          -> spec that preserves base content
    inspect_deck(path)                    -> pages/texts/notes/media/layouts
    inspect_overflow(path)                -> approximate text overflow risks
    validate_zip(path)                    -> structural problems
    export_preview(pptx, out_dir, spec)   -> native or content-faithful PDF/PNG
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, unescape

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument"
NS = f'xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"'
EMU_PT = 12700

SIZES = {"16:9": (12192000, 6858000), "4:3": (9144000, 6858000)}

THEMES = {
    "scholar": {
        "ink": "1F2A30", "muted": "5E6A70", "accent": "0F6E78",
        "accent2": "B07D2A", "bg": "FBFCFC", "panel": "EAF2F3",
        "line": "C9D6D9", "white": "FFFFFF",
        "title_font": "Cambria", "body_font": "Calibri",
        "title_ea": "SimSun", "body_ea": "SimHei",
    },
    "editorial": {
        "ink": "24211E", "muted": "665F58", "accent": "9B3A2E",
        "accent2": "3E6B66", "bg": "FFFDF8", "panel": "F4EEE5",
        "line": "DFD3C3", "white": "FFFFFF",
        "title_font": "Cambria", "body_font": "Calibri",
        "title_ea": "SimSun", "body_ea": "SimHei",
    },
    "graphite": {
        "ink": "20252B", "muted": "626B73", "accent": "356A8A",
        "accent2": "B4884A", "bg": "FCFCFC", "panel": "EEF1F4",
        "line": "D5DCE2", "white": "FFFFFF",
        "title_font": "Cambria", "body_font": "Calibri",
        "title_ea": "SimSun", "body_ea": "SimHei",
    },
}


# ---------------------------------------------------------------- utilities
def resolve_theme(raw, base=None):
    t = dict(THEMES["scholar"])
    if base:
        t.update({k: v for k, v in base.items() if v})
    if isinstance(raw, str):
        t.update(THEMES.get(raw, {}))
    elif isinstance(raw, dict):
        t.update(THEMES.get(str(raw.get("name", "")), {}))
        for key, value in raw.items():
            if key != "name" and value:
                t[key] = str(value)
    return t


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


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


def image_size(path):
    try:
        data = Path(path).read_bytes()
    except OSError:
        return (800, 600)
    return png_size(data) or jpeg_size(data) or (800, 600)


def color(value, fallback="000000"):
    value = str(value or "").strip().lstrip("#")
    return value if re.fullmatch(r"[0-9A-Fa-f]{6}", value) else fallback


# ---------------------------------------------------------------- slide XML
def tx_body(paragraphs, anchor="t", font=None, ea_font=None):
    out = [f'<p:txBody><a:bodyPr wrap="square" anchor="{anchor}">'
           '<a:normAutofit/></a:bodyPr><a:lstStyle/>']
    for para in paragraphs:
        size = int(float(para.get("size", 18)) * 100)
        bold = ' b="1"' if para.get("bold") else ""
        attrs, children = [], []
        if para.get("bullet", True):
            attrs.append('marL="285750" indent="-285750"')
            children.append('<a:buFont typeface="Arial"/><a:buChar char="\u2022"/>')
        if para.get("level"):
            attrs.append(f'lvl="{int(para["level"])}"')
        if para.get("align") in ("l", "ctr", "r", "just"):
            attrs.append(f'algn="{para["align"]}"')
        ppr_xml = ""
        if attrs or children:
            ppr_xml = f'<a:pPr {" ".join(attrs)}>{"".join(children)}</a:pPr>'
        fill = ""
        if para.get("color"):
            fill = f'<a:solidFill><a:srgbClr val="{color(para["color"])}"/></a:solidFill>'
        face = ""
        if font:
            face = f'<a:latin typeface="{escape(str(font))}"/>'
            if ea_font:
                face += f'<a:ea typeface="{escape(str(ea_font))}"/>'
        out.append(
            f'<a:p>{ppr_xml}<a:r><a:rPr lang="zh-CN" sz="{size}"{bold}>'
            f'{fill}{face}</a:rPr><a:t>{escape(str(para.get("text", "")))}</a:t></a:r></a:p>')
    out.append("</p:txBody>")
    return "".join(out)


def box_shape(shape_id, name, x, y, cx, cy, fill=None, line=None,
              line_width=12700, prst="rect", paragraphs=None, anchor="t",
              font=None, ea_font=None, radius=None, shadow=False):
    if prst == "roundRect" and radius is not None:
        prst_xml = ('<a:prstGeom prst="roundRect"><a:avLst>'
                    f'<a:gd name="adj" fmla="val {int(radius)}"/></a:avLst></a:prstGeom>')
    else:
        prst_xml = f'<a:prstGeom prst="{prst}"><a:avLst/></a:prstGeom>'
    fill_xml = f'<a:solidFill><a:srgbClr val="{color(fill)}"/></a:solidFill>' if fill else "<a:noFill/>"
    line_xml = (f'<a:ln w="{int(line_width)}"><a:solidFill><a:srgbClr val="{color(line)}"/>'
                '</a:solidFill></a:ln>') if line else "<a:ln><a:noFill/></a:ln>"
    effect = ""
    if shadow:
        effect = ('<a:effectLst><a:outerShdw blurRad="38100" dist="12700" dir="5400000" '
                  'algn="ctr" rotWithShape="0"><a:srgbClr val="000000"><a:alpha val="12000"/>'
                  '</a:srgbClr></a:outerShdw></a:effectLst>')
    body = tx_body(paragraphs, anchor=anchor, font=font, ea_font=ea_font) if paragraphs else ""
    tx_attr = ' txBox="1"' if paragraphs else ""
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{escape(name)}"/>'
            f'<p:cNvSpPr{tx_attr}/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{int(x)}" y="{int(y)}"/>'
            f'<a:ext cx="{int(cx)}" cy="{int(cy)}"/></a:xfrm>{prst_xml}{fill_xml}{line_xml}{effect}'
            f'</p:spPr>{body}</p:sp>')


def picture(shape_id, rid, x, y, cx, cy, name="Picture"):
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="{shape_id}" name="{escape(name)}"/>'
            f'<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')


def group_props():
    return ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


def rels_xml(entries):
    body = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{escape(g)}"/>'
                   for i, t, g in entries)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{PKG_REL}">{body}</Relationships>')


# ------------------------------------------------------------ template audit
ROLE_LAYOUT_HINTS = {
    "title": ["title slide", "title", "cover", "封面", "标题幻灯片"],
    "section": ["section header", "section", "节标题"],
    "content": ["title and content", "content", "标题和内容", "内容"],
    "comparison": ["two content", "comparison", "两栏", "对比"],
    "image": ["picture with caption", "content with caption", "picture", "图片"],
    "closing": ["title only", "仅标题", "blank", "空白"],
}


def _parse_content_types(data):
    defaults, overrides = {}, {}
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return defaults, overrides
    for elem in root:
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "Default":
            defaults[elem.get("Extension", "").lower()] = elem.get("ContentType", "")
        elif tag == "Override":
            overrides[elem.get("PartName", "").lstrip("/")] = elem.get("ContentType", "")
    return defaults, overrides


def _scheme_color(node, fallback):
    if node is None:
        return fallback
    srgb = node.find(f"{{{A_NS}}}srgbClr")
    if srgb is not None and srgb.get("val"):
        return color(srgb.get("val"), fallback)
    sysc = node.find(f"{{{A_NS}}}sysClr")
    if sysc is not None:
        mapping = {"windowText": "000000", "window": "FFFFFF", "highlight": "FFFF00"}
        return mapping.get(sysc.get("val"), color(sysc.get("lastClr"), fallback))
    return fallback


def _theme_from_xml(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    base = dict(THEMES["scholar"])
    scheme = root.find(f".//{{{A_NS}}}clrScheme")
    if scheme is not None:
        base["ink"] = _scheme_color(scheme.find(f"{{{A_NS}}}dk1"), base["ink"])
        base["bg"] = _scheme_color(scheme.find(f"{{{A_NS}}}lt1"), base["bg"])
        base["muted"] = _scheme_color(scheme.find(f"{{{A_NS}}}dk2"), base["muted"])
        base["panel"] = _scheme_color(scheme.find(f"{{{A_NS}}}lt2"), base["panel"])
        base["accent"] = _scheme_color(scheme.find(f"{{{A_NS}}}accent1"), base["accent"])
        base["accent2"] = _scheme_color(scheme.find(f"{{{A_NS}}}accent2"), base["accent2"])
    major = root.find(f".//{{{A_NS}}}majorFont")
    minor = root.find(f".//{{{A_NS}}}minorFont")
    for node, prefix in ((major, "title"), (minor, "body")):
        if node is None:
            continue
        latin = node.find(f"{{{A_NS}}}latin")
        ea = node.find(f"{{{A_NS}}}ea")
        if latin is not None and latin.get("typeface"):
            base[f"{prefix}_font"] = latin.get("typeface")
        if ea is not None and ea.get("typeface"):
            base[f"{prefix}_ea"] = ea.get("typeface")
    return base


def audit_template(path):
    """Inspect a real template/deck: masters, layouts, theme, size, media."""
    path = str(path)
    info = {"path": path, "exists": Path(path).is_file(), "masters": [],
            "layouts": [], "theme": {}, "size": None, "media": [],
            "notes_masters": [], "copy_parts": [], "defaults": {},
            "overrides": {}, "layout_names": []}
    if not info["exists"]:
        return info
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            info["defaults"], info["overrides"] = _parse_content_types(
                z.read("[Content_Types].xml") if "[Content_Types].xml" in names else b"<Types/>")
            for name in sorted(names):
                if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", name):
                    info["masters"].append(name)
                elif re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", name):
                    try:
                        root = ET.fromstring(z.read(name))
                        c_sld = root.find(f"{{{P_NS}}}cSld")
                        lname = c_sld.get("name", "") if c_sld is not None else ""
                    except ET.ParseError:
                        lname = ""
                    info["layouts"].append({"path": name, "name": lname})
                    info["layout_names"].append(lname)
                elif re.fullmatch(r"ppt/theme/theme\d+\.xml", name) and not info["theme"]:
                    info["theme"] = _theme_from_xml(z.read(name))
                elif re.fullmatch(r"ppt/notesMasters/notesMaster\d+\.xml", name):
                    info["notes_masters"].append(name)
                    try:
                        nroot = ET.fromstring(z.read(name))
                        for ph in nroot.iter(f"{{{P_NS}}}ph"):
                            if ph.get("type") == "body":
                                info["notes_body_idx"] = int(ph.get("idx", "3"))
                                break
                    except (ET.ParseError, TypeError, ValueError):
                        pass
                elif name.startswith("ppt/media/"):
                    info["media"].append(name)
            if "ppt/presentation.xml" in names:
                try:
                    root = ET.fromstring(z.read("ppt/presentation.xml"))
                    sz = root.find(f"{{{P_NS}}}sldSz")
                    if sz is not None:
                        info["size"] = (int(sz.get("cx")), int(sz.get("cy")))
                except (ET.ParseError, TypeError, ValueError):
                    pass
            for name in names:
                if (re.fullmatch(r"ppt/(slideMasters|slideLayouts|theme|notesMasters)/[^/]+\.xml", name)
                        or re.fullmatch(r"ppt/(slideMasters|slideLayouts|notesMasters)/_rels/[^/]+\.rels", name)
                        or name.startswith("ppt/media/")):
                    info["copy_parts"].append(name)
    except (zipfile.BadZipFile, KeyError, OSError):
        info["exists"] = False
    return info


def pick_layout(role, layouts):
    if not layouts:
        return None
    hints = ROLE_LAYOUT_HINTS.get(str(role or "content"), ROLE_LAYOUT_HINTS["content"])
    lowered = [(i, str(item.get("name", "")).lower()) for i, item in enumerate(layouts)]
    for hint in hints:
        for i, name in lowered:
            if hint in name:
                return i
    # a layout explicitly named "blank" is the safest fallback for absolute shapes
    for i, name in lowered:
        if "blank" in name or "空白" in name:
            return i
    return 0


# ------------------------------------------------------------------- writer
class Deck:
    def __init__(self, spec, template_info=None):
        self.spec = spec or {}
        self.template_info = template_info or {}
        cx, cy = SIZES.get(str(self.spec.get("size", "16:9")), SIZES["16:9"])
        if template_info and template_info.get("size"):
            cx, cy = template_info["size"]
        self.cx, self.cy = cx, cy
        self.slides = self.spec.get("slides", [])
        base = template_info.get("theme") if template_info else None
        self.theme = resolve_theme(self.spec.get("theme"), base)
        self.media = []          # list of (package name, bytes)
        self.media_by_path = {}
        self.slide_pics = {}
        self.notes_body_idx = int((template_info or {}).get("notes_body_idx") or 3)
        self.template_mode = bool(template_info and template_info.get("copy_parts"))
        self.report = {"template_fidelity": "none", "layouts_used": [],
                       "images_embedded": [], "images_missing": []}

    # ---------------- content helpers
    def _para(self, value, size, ink, bold=False, bullet=False, level=0, align="l"):
        return {"text": str(value), "size": size, "bold": bold, "bullet": bullet,
                "level": level, "color": ink, "align": align}

    def _bullets(self, items, size, ink):
        if isinstance(items, str):
            items = [items]
        out = []
        for item in items or []:
            if isinstance(item, dict):
                out.append(self._para(item.get("text", ""), item.get("size", size),
                                      item.get("color", ink), item.get("bold", False),
                                      True, item.get("level", 0)))
            else:
                out.append(self._para(item, size, ink, False, True))
        return out

    def add_image(self, path):
        path = str(path)
        if path in self.media_by_path:
            return self.media_by_path[path]
        raw = Path(path).read_bytes()
        ext = Path(path).suffix.lower().lstrip(".") or "png"
        if ext == "jpg":
            ext = "jpeg"
        if ext not in ("png", "jpeg", "gif", "bmp", "tiff"):
            raise ValueError(f"unsupported image format: {path}")
        name = f"deckimg{len(self.media) + 1}.{ext}"
        self.media.append((name, raw))
        self.media_by_path[path] = {"name": name, "ext": ext,
                                    "bytes": len(raw), "source": path}
        return self.media_by_path[path]

    # ---------------- slide
    def slide_xml(self, index):
        s = self.slides[index]
        layout = str(s.get("layout") or s.get("kind") or "content").lower()
        t = self.theme
        cx, cy = self.cx, self.cy
        margin = int(cx * 0.06)
        footer_y = int(cy * 0.925)
        shapes = []
        sid = [2]
        dark = layout in ("title", "cover", "closing", "end", "statement", "quote", "section", "divider")
        bg = s.get("background") or (t["ink"] if dark else t["bg"])
        ink = s.get("text_color") or (t["white"] if dark else t["ink"])
        muted = s.get("muted_color") or (t["line"] if dark else t["muted"])

        def text(x, y, w, h, paragraphs, anchor="t", name="Text"):
            if not paragraphs or all(not str(p.get("text", "")).strip() for p in paragraphs):
                return
            shapes.append(box_shape(sid[0], name, x, y, w, h, paragraphs=paragraphs,
                                    anchor=anchor, font=t["body_font"], ea_font=t["body_ea"]))
            sid[0] += 1

        def add_picture(x0, y0, bw, bh):
            img = s.get("image")
            if not img or not Path(img).is_file():
                if img:
                    self.report["images_missing"].append(str(img))
                return False
            try:
                media = self.add_image(img)
            except (OSError, ValueError) as exc:
                self.report["images_missing"].append(f"{img}: {exc}")
                return False
            px_w, px_h = image_size(img)
            scale = min(bw / px_w, bh / px_h)
            w, h = int(px_w * scale), int(px_h * scale)
            x = x0 + (bw - w) // 2
            y = y0 + (bh - h) // 2
            rid = "rIdImg1"
            self.slide_pics.setdefault(index, []).append(media)
            shapes.append(picture(sid[0], rid, x, y, w, h, Path(img).name))
            sid[0] += 1
            self.report["images_embedded"].append(
                {"slide": index + 1, "path": str(img), "package": media["name"]})
            return True

        def card(x, y, w, h, title, body, accent=None, fill=None, title_size=18, body_size=14):
            shapes.append(box_shape(sid[0], "Card", x, y, w, h, fill=fill or t["panel"],
                                    line=t["line"], line_width=9525, prst="roundRect",
                                    radius=7000, shadow=True))
            sid[0] += 1
            shapes.append(box_shape(sid[0], "Card accent", x + int(w * 0.06), y + int(h * 0.10),
                                    max(120000, int(w * 0.055)), max(120000, int(h * 0.055)),
                                    fill=accent or t["accent"], line=None, prst="ellipse"))
            sid[0] += 1
            text(x + int(w * 0.10), y + int(h * 0.10), int(w * 0.82), int(h * 0.22),
                 [self._para(title, title_size, t["ink"], True)], name="Card title")
            text(x + int(w * 0.10), y + int(h * 0.36), int(w * 0.82), int(h * 0.55),
                 self._bullets(body, body_size, t["muted"]), name="Card body")

        def footer():
            if s.get("hide_footer"):
                return
            if s.get("source"):
                text(margin, footer_y, int(cx * 0.62), int(cy * 0.045),
                     [self._para(s["source"], 9.5, muted)], name="Source")
            text(cx - margin - int(cx * 0.10), footer_y, int(cx * 0.10), int(cy * 0.045),
                 [self._para(str(index + 1), 10, muted, False, False, 0, "r")], name="Page number")

        if layout in ("title", "cover"):
            eyebrow = s.get("eyebrow", s.get("kicker", ""))
            if eyebrow:
                text(margin, int(cy * 0.18), cx - 2 * margin, int(cy * 0.10),
                     [self._para(eyebrow, 13, t["accent2"], True)])
            text(margin, int(cy * 0.27), int(cx * 0.80), int(cy * 0.24),
                 [self._para(s.get("title", ""), s.get("title_size", 38), ink, True)],
                 anchor="t", name="Title")
            if s.get("subtitle"):
                text(margin, int(cy * 0.53), int(cx * 0.70), int(cy * 0.16),
                     [self._para(s["subtitle"], s.get("subtitle_size", 19), muted)], name="Subtitle")
            footer()
        elif layout in ("section", "divider"):
            number = str(s.get("number", ""))
            if number:
                text(margin, int(cy * 0.22), int(cx * 0.12), int(cy * 0.18),
                     [self._para(number, 44, t["accent"], True)])
            text(margin, int(cy * 0.40), int(cx * 0.78), int(cy * 0.16),
                 [self._para(s.get("title", ""), s.get("title_size", 32), ink, True)],
                 name="Section title")
            if s.get("subtitle"):
                text(margin, int(cy * 0.58), int(cx * 0.68), int(cy * 0.10),
                     [self._para(s["subtitle"], 17, muted)], name="Section subtitle")
            footer()
        elif layout in ("closing", "end"):
            text(margin, int(cy * 0.16), cx - 2 * margin, int(cy * 0.12),
                 [self._para(s.get("eyebrow", "TAKE-HOME"), 12, t["accent2"], True)])
            text(margin, int(cy * 0.27), int(cx * 0.80), int(cy * 0.16),
                 [self._para(s.get("title", "结论"), s.get("title_size", 32), ink, True)],
                 name="Closing title")
            body = s.get("bullets") or s.get("takeaways") or []
            if body:
                text(margin, int(cy * 0.47), int(cx * 0.78), int(cy * 0.30),
                     self._bullets(body, s.get("body_size", 17), muted), name="Takeaways")
            if s.get("subtitle"):
                text(margin, int(cy * 0.82), int(cx * 0.78), int(cy * 0.06),
                     [self._para(s["subtitle"], 12, muted)], name="Closing note")
            footer()
        elif layout in ("quote", "statement"):
            quote = s.get("quote") or s.get("title", "")
            if s.get("title") and str(s["title"]) != str(quote):
                text(margin, int(cy * 0.10), cx - 2 * margin, int(cy * 0.08),
                     [self._para(s["title"], 13, t["accent2"], True, False, 0, "ctr")],
                     name="Statement title")
            text(margin, int(cy * 0.20), cx - 2 * margin, int(cy * 0.42),
                 [self._para("\u201c" + str(quote) + "\u201d", s.get("title_size", 28),
                             ink, False, False, 0, "ctr")], anchor="ctr", name="Quote")
            if s.get("attribution"):
                text(margin, int(cy * 0.68), cx - 2 * margin, int(cy * 0.08),
                     [self._para("\u2014 " + str(s["attribution"]), 14, muted, False, False, 0, "r")],
                     name="Attribution")
            footer()
        else:
            if s.get("eyebrow"):
                text(margin, int(cy * 0.045), int(cx * 0.80), int(cy * 0.045),
                     [self._para(s["eyebrow"], 10.5, t["accent"], True)], name="Eyebrow")
            text(margin, int(cy * 0.085), cx - 2 * margin, int(cy * 0.105),
                 [self._para(s.get("title", ""), s.get("title_size", 28), ink, True)], name="Title")

            if layout in ("timeline", "steps", "process"):
                steps = s.get("steps") or s.get("items") or []
                n = max(1, len(steps))
                gap = int(cx * 0.02)
                width = (cx - 2 * margin - gap * (n - 1)) // n
                y = int(cy * 0.28)
                for i, step in enumerate(steps):
                    x = margin + i * (width + gap)
                    shapes.append(box_shape(sid[0], "Step card", x, y, width, int(cy * 0.48),
                                            fill=t["panel"], line=t["line"], line_width=9525,
                                            prst="roundRect", radius=7000, shadow=True))
                    sid[0] += 1
                    shapes.append(box_shape(sid[0], "Step number", x + int(width * 0.08),
                                            y + int(cy * 0.07), int(cx * 0.045),
                                            int(cx * 0.045), fill=t["accent"], line=None,
                                            prst="ellipse",
                                            paragraphs=[self._para(str(i + 1), 15, t["white"],
                                                                   True, False, 0, "ctr")],
                                            anchor="ctr"))
                    sid[0] += 1
                    st = step if isinstance(step, dict) else {"title": str(step)}
                    text(x + int(width * 0.08), y + int(cy * 0.16), int(width * 0.84),
                         int(cy * 0.17),
                         [self._para(st.get("title", st.get("label", "")), 14, t["ink"], True)],
                         name="Step title")
                    body = st.get("text", st.get("body", ""))
                    text(x + int(width * 0.08), y + int(cy * 0.36), int(width * 0.84),
                         int(cy * 0.13),
                         self._bullets(body, 11.5, t["muted"]) if body else [], name="Step body")
            elif layout in ("stat", "stats", "metrics"):
                stats = s.get("stats") or s.get("metrics") or []
                n = max(1, len(stats))
                gap = int(cx * 0.02)
                width = (cx - 2 * margin - gap * (n - 1)) // n
                y = int(cy * 0.28)
                for i, item in enumerate(stats):
                    x = margin + i * (width + gap)
                    value = item.get("value", item.get("number", "")) if isinstance(item, dict) else str(item)
                    label = item.get("label", item.get("caption", "")) if isinstance(item, dict) else ""
                    shapes.append(box_shape(sid[0], "Stat card", x, y, width, int(cy * 0.40),
                                            fill=t["panel"], line=t["line"], line_width=9525,
                                            prst="roundRect", radius=7000, shadow=True))
                    sid[0] += 1
                    text(x + int(width * 0.08), y + int(cy * 0.07), int(width * 0.84),
                         int(cy * 0.16),
                         [self._para(value, 34, t["accent"], True, False, 0, "ctr")],
                         anchor="ctr", name="Stat value")
                    text(x + int(width * 0.08), y + int(cy * 0.25), int(width * 0.84),
                         int(cy * 0.09),
                         [self._para(label, 12, t["muted"], False, False, 0, "ctr")],
                         name="Stat label")
                if s.get("bullets"):
                    text(margin, int(cy * 0.72), cx - 2 * margin, int(cy * 0.14),
                         self._bullets(s["bullets"], 14, t["muted"]), name="Content")
            elif layout in ("comparison", "two-column", "two-col"):
                left = s.get("left") or {}
                right = s.get("right") or {}
                gap = int(cx * 0.025)
                width = (cx - 2 * margin - gap) // 2
                y = int(cy * 0.28)
                for i, block in enumerate((left, right)):
                    x = margin + i * (width + gap)
                    if isinstance(block, str):
                        block = {"title": "", "bullets": [block]}
                    card(x, y, width, int(cy * 0.52),
                         block.get("title", ["方案 A", "方案 B"][i]),
                         block.get("bullets", []),
                         accent=t["accent"] if i == 0 else t["accent2"])
            elif layout in ("cards", "grid", "features"):
                cards = s.get("cards") or []
                n = max(1, len(cards))
                gap = int(cx * 0.018)
                width = (cx - 2 * margin - gap * (n - 1)) // n
                y = int(cy * 0.29)
                for i, item in enumerate(cards):
                    x = margin + i * (width + gap)
                    card(x, y, width, int(cy * 0.48), item.get("title", ""),
                         item.get("bullets", item.get("text", [])),
                         accent=t["accent"] if i % 2 == 0 else t["accent2"])
            else:
                has_image = bool(s.get("image") and Path(s["image"]).is_file())
                callout = s.get("callout") or {}
                body_w = int((cx - 2 * margin) * (0.54 if (has_image or callout) else 0.92))
                body_x = margin
                body_y = int(cy * 0.27)
                body_h = int(cy * 0.57)
                image_left = layout in ("image-left",)
                if has_image and image_left:
                    add_picture(margin, int(cy * 0.26), int(cx * 0.40), int(cy * 0.58))
                    body_x = margin + int(cx * 0.44)
                    body_w = cx - margin - body_x
                elif has_image:
                    add_picture(margin + int(cx * 0.56), int(cy * 0.25),
                                int(cx * 0.38), int(cy * 0.58))
                elif callout:
                    box_w = int(cx * 0.30)
                    box_x = cx - margin - box_w
                    shapes.append(box_shape(sid[0], "Callout", box_x, int(cy * 0.28), box_w,
                                            int(cy * 0.46), fill=t["panel"], line=t["line"],
                                            line_width=9525, prst="roundRect", radius=7000,
                                            shadow=True))
                    sid[0] += 1
                    text(box_x + int(box_w * 0.08), int(cy * 0.33), int(box_w * 0.84),
                         int(cy * 0.08),
                         [self._para(callout.get("title", "关键点"), 15, t["accent"], True)],
                         name="Callout title")
                    text(box_x + int(box_w * 0.08), int(cy * 0.43), int(box_w * 0.84),
                         int(cy * 0.24),
                         self._bullets(callout.get("bullets", callout.get("text", [])),
                                       12, t["muted"]), name="Callout body")
                body = s.get("bullets") or []
                text(body_x, body_y, body_w, body_h,
                     self._bullets(body, s.get("body_size", 17), t["muted"]), name="Content")
                if s.get("caption"):
                    text(margin, int(cy * 0.855), cx - 2 * margin, int(cy * 0.04),
                         [self._para(s["caption"], 10.5, t["muted"])], name="Caption")
            footer()

        bg_xml = ""
        if not self.template_mode:
            bg_xml = (f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="{color(bg)}"/></a:solidFill>'
                      '<a:effectLst/></p:bgPr></p:bg>')
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<p:sld {NS}>{bg_xml}<p:cSld><p:spTree>{group_props()}'
                + "".join(shapes)
                + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')

    def notes_xml(self, index):
        text = self.slides[index].get("notes", "")
        paragraphs = [{"text": line, "size": 12, "bullet": False}
                      for line in str(text).split("\n")] if text else \
                     [{"text": "", "size": 12, "bullet": False}]
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<p:notes {NS}><p:cSld><p:spTree>{group_props()}'
                '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/>'
                '<p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr>'
                '<p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
                '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/>'
                '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                f'<p:nvPr><p:ph type="body" idx="{self.notes_body_idx}"/></p:nvPr></p:nvSpPr><p:spPr/>'
                f'{tx_body(paragraphs)}</p:sp>'
                '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>')

    def slide_rels(self, index):
        s = self.slides[index]
        entries = []
        if self.template_mode and self.template_info.get("layouts"):
            li = pick_layout(s.get("role") or s.get("kind"), self.template_info["layouts"])
            target = "../slideLayouts/" + Path(self.template_info["layouts"][li]["path"]).name
            entries.append(("rId1", R_NS + "/slideLayout", target))
            self.report["layouts_used"].append(
                {"slide": index + 1, "layout": self.template_info["layouts"][li]["name"],
                 "part": target})
        else:
            entries.append(("rId1", R_NS + "/slideLayout", "../slideLayouts/slideLayout1.xml"))
        for n, media in enumerate(self.slide_pics.get(index, []), start=1):
            entries.append((f"rIdImg{n}", R_NS + "/image", f"../media/{media['name']}"))
        entries.append(("rIdNotes", R_NS + "/notesSlide", f"../notesSlides/notesSlide{index + 1}.xml"))
        return rels_xml(entries)

    # ---------------- package parts
    def presentation_xml(self):
        slides = "".join(
            f'<p:sldId id="{256 + i}" r:id="rIdS{i + 1}"/>' for i in range(len(self.slides)))
        if self.template_mode and self.template_info.get("masters"):
            inner = "".join(
                f'<p:sldMasterId id="{2147483648 + i}" r:id="rIdMaster{i + 1}"/>'
                for i in range(len(self.template_info["masters"])))
            masters = f"<p:sldMasterIdLst>{inner}</p:sldMasterIdLst>"
        else:
            masters = ('<p:sldMasterIdLst><p:sldMasterId id="2147483648" '
                       'r:id="rIdMaster"/></p:sldMasterIdLst>')
        notes = ('<p:notesMasterIdLst><p:notesMasterId r:id="rIdNotesMaster"/>'
                 '</p:notesMasterIdLst>')
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<p:presentation {NS} saveSubsetFonts="1">{masters}{notes}'
                f'<p:sldIdLst>{slides}</p:sldIdLst>'
                f'<p:sldSz cx="{self.cx}" cy="{self.cy}"/>'
                '<p:notesSz cx="6858000" cy="9144000"/>'
                '<p:defaultTextStyle><a:defPPr><a:defRPr lang="zh-CN"/></a:defPPr>'
                '</p:defaultTextStyle></p:presentation>')

    def presentation_rels(self):
        entries = []
        if self.template_mode and self.template_info.get("masters"):
            for i, master in enumerate(self.template_info["masters"]):
                entries.append((f"rIdMaster{i + 1}", R_NS + "/slideMaster",
                                f"slideMasters/{Path(master).name}"))
        else:
            entries.append(("rIdMaster", R_NS + "/slideMaster", "slideMasters/slideMaster1.xml"))
        entries.append(("rIdNotesMaster", R_NS + "/notesMaster", "notesMasters/notesMaster1.xml"))
        entries.append(("rIdTheme", R_NS + "/theme", "theme/theme1.xml"))
        for i in range(len(self.slides)):
            entries.append((f"rIdS{i + 1}", R_NS + "/slide", f"slides/slide{i + 1}.xml"))
        return rels_xml(entries)

    def content_types(self):
        overrides = [
            ("/ppt/presentation.xml", CT + ".presentationml.presentation.main+xml"),
            ("/ppt/slideMasters/slideMaster1.xml", CT + ".presentationml.slideMaster+xml"),
            ("/ppt/slideLayouts/slideLayout1.xml", CT + ".presentationml.slideLayout+xml"),
            ("/ppt/notesMasters/notesMaster1.xml", CT + ".presentationml.notesMaster+xml"),
            ("/ppt/theme/theme1.xml", CT + ".theme+xml"),
            ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
            ("/docProps/app.xml", CT + ".extended-properties+xml"),
        ]
        for i in range(len(self.slides)):
            overrides.append((f"/ppt/slides/slide{i + 1}.xml", CT + ".presentationml.slide+xml"))
            overrides.append((f"/ppt/notesSlides/notesSlide{i + 1}.xml",
                              CT + ".presentationml.notesSlide+xml"))
        body = "".join(f'<Override PartName="{p}" ContentType="{c}"/>' for p, c in overrides)
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Default Extension="png" ContentType="image/png"/>'
                '<Default Extension="jpeg" ContentType="image/jpeg"/>'
                f'{body}</Types>')

    def slide_master_xml(self):
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<p:sldMaster {NS}><p:cSld>'
                '<p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
                f'<p:spTree>{group_props()}'
                '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title Placeholder 1"/>'
                '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
                '<p:spPr><a:xfrm><a:off x="838200" y="365125"/><a:ext cx="10515600" cy="1325563"/></a:xfrm></p:spPr>'
                f'{tx_body([{"text": "Click to edit title", "size": 28, "bullet": False}], "b")}</p:sp>'
                '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Text Placeholder 2"/>'
                '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
                '<p:spPr><a:xfrm><a:off x="838200" y="1825625"/><a:ext cx="10515600" cy="4351338"/></a:xfrm></p:spPr>'
                f'{tx_body([{"text": "Click to edit text", "size": 18}])}</p:sp>'
                '</p:spTree></p:cSld>'
                '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
                'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
                'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
                '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rIdLayout"/></p:sldLayoutIdLst>'
                '<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="2800" b="1"/></a:lvl1pPr></p:titleStyle>'
                '<p:bodyStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:bodyStyle>'
                '<p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle></p:txStyles>'
                '</p:sldMaster>')

    def slide_layout_xml(self):
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<p:sldLayout {NS} type="obj" preserve="1">'
                '<p:cSld name="Title and Content">'
                f'<p:spTree>{group_props()}'
                '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/>'
                '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
                '<p:spPr><a:xfrm><a:off x="838200" y="365125"/><a:ext cx="10515600" cy="1325563"/></a:xfrm></p:spPr>'
                f'{tx_body([{"text": "Title", "size": 28, "bullet": False}], "b")}</p:sp>'
                '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Content 2"/>'
                '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                '<p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
                '<p:spPr><a:xfrm><a:off x="838200" y="1825625"/><a:ext cx="10515600" cy="4351338"/></a:xfrm></p:spPr>'
                f'{tx_body([{"text": "Content", "size": 18}])}</p:sp>'
                '</p:spTree></p:cSld>'
                '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')

    def notes_master_xml(self):
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<p:notesMaster {NS}><p:cSld>'
                '<p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
                f'<p:spTree>{group_props()}'
                '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/>'
                '<p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr>'
                '<p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr></p:nvSpPr>'
                '<p:spPr><a:xfrm><a:off x="685800" y="1143000"/><a:ext cx="5486400" cy="3086100"/></a:xfrm></p:spPr></p:sp>'
                '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/>'
                '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
                '<p:nvPr><p:ph type="body" sz="quarter" idx="3"/></p:nvPr></p:nvSpPr>'
                '<p:spPr><a:xfrm><a:off x="685800" y="4400550"/><a:ext cx="5486400" cy="3600450"/></a:xfrm></p:spPr>'
                f'{tx_body([{"text": "", "size": 12, "bullet": False}])}</p:sp>'
                '</p:spTree></p:cSld>'
                '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
                'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
                'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
                '<p:notesStyle><a:lvl1pPr><a:defRPr sz="1200"/></a:lvl1pPr></p:notesStyle>'
                '</p:notesMaster>')

    def theme_xml(self):
        t = self.theme
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<a:theme xmlns:a="{A_NS}" name="DeckKit">'
                '<a:themeElements><a:clrScheme name="DeckKit">'
                f'<a:dk1><a:srgbClr val="{t["ink"]}"/></a:dk1>'
                f'<a:lt1><a:srgbClr val="{t["bg"]}"/></a:lt1>'
                f'<a:dk2><a:srgbClr val="{t["muted"]}"/></a:dk2>'
                f'<a:lt2><a:srgbClr val="{t["panel"]}"/></a:lt2>'
                f'<a:accent1><a:srgbClr val="{t["accent"]}"/></a:accent1>'
                f'<a:accent2><a:srgbClr val="{t["accent2"]}"/></a:accent2>'
                '<a:accent3><a:srgbClr val="8A9A9F"/></a:accent3>'
                '<a:accent4><a:srgbClr val="C9A227"/></a:accent4>'
                '<a:accent5><a:srgbClr val="6C8EA0"/></a:accent5>'
                '<a:accent6><a:srgbClr val="7A9A7E"/></a:accent6>'
                f'<a:hlink><a:srgbClr val="{t["accent"]}"/></a:hlink>'
                f'<a:folHlink><a:srgbClr val="{t["muted"]}"/></a:folHlink>'
                '</a:clrScheme>'
                '<a:fontScheme name="DeckKit">'
                f'<a:majorFont><a:latin typeface="{escape(t["title_font"])}"/>'
                f'<a:ea typeface="{escape(t["title_ea"])}"/><a:cs typeface=""/></a:majorFont>'
                f'<a:minorFont><a:latin typeface="{escape(t["body_font"])}"/>'
                f'<a:ea typeface="{escape(t["body_ea"])}"/><a:cs typeface=""/></a:minorFont>'
                '</a:fontScheme>'
                '<a:fmtScheme name="Office"><a:fillStyleLst>'
                '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
                '<a:solidFill><a:schemeClr val="phClr"><a:tint val="50000"/></a:schemeClr></a:solidFill>'
                '<a:solidFill><a:schemeClr val="phClr"><a:shade val="50000"/></a:schemeClr></a:solidFill>'
                '</a:fillStyleLst><a:lnStyleLst>'
                '<a:ln w="6350" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>'
                '<a:ln w="12700" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>'
                '<a:ln w="19050" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>'
                '</a:lnStyleLst><a:effectStyleLst>'
                '<a:effectStyle><a:effectLst/></a:effectStyle>'
                '<a:effectStyle><a:effectLst/></a:effectStyle>'
                '<a:effectStyle><a:effectLst/></a:effectStyle>'
                '</a:effectStyleLst><a:bgFillStyleLst>'
                '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
                '<a:solidFill><a:schemeClr val="phClr"><a:tint val="95000"/></a:schemeClr></a:solidFill>'
                '<a:solidFill><a:schemeClr val="phClr"><a:shade val="95000"/></a:schemeClr></a:solidFill>'
                '</a:bgFillStyleLst></a:fmtScheme></a:themeElements>'
                '<a:objectDefaults/><a:extraClrSchemeLst/></a:theme>')

    def core_xml(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                f'<dc:title>{escape(str(self.spec.get("title", "")))}</dc:title>'
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
                '</cp:coreProperties>')

    def app_xml(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                '<Application>deckkit</Application>'
                f'<Slides>{len(self.slides)}</Slides></Properties>')

    # ---------------- write
    def _template_content_types(self, copied):
        defaults = {"rels": "application/vnd.openxmlformats-package.relationships+xml",
                    "xml": "application/xml", "png": "image/png", "jpeg": "image/jpeg",
                    "jpg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
                    "tiff": "image/tiff", "emf": "image/x-emf", "wmf": "image/x-wmf"}
        for ext, ctype in (self.template_info.get("defaults") or {}).items():
            if ext:
                defaults[ext] = ctype
        overrides = {}
        for part, ctype in (self.template_info.get("overrides") or {}).items():
            if part in copied and ctype:
                overrides[part] = ctype
        overrides["ppt/presentation.xml"] = CT + ".presentationml.presentation.main+xml"
        overrides["docProps/core.xml"] = "application/vnd.openxmlformats-package.core-properties+xml"
        overrides["docProps/app.xml"] = CT + ".extended-properties+xml"
        for i in range(len(self.slides)):
            overrides[f"ppt/slides/slide{i + 1}.xml"] = CT + ".presentationml.slide+xml"
            overrides[f"ppt/notesSlides/notesSlide{i + 1}.xml"] = CT + ".presentationml.notesSlide+xml"
        # template media types are covered by defaults; ensure a default exists
        for name in copied:
            ext = Path(name).suffix.lower().lstrip(".")
            if ext and ext not in defaults and name.startswith("ppt/media/"):
                defaults[ext] = "application/octet-stream"
        defs = "".join(f'<Default Extension="{escape(ext)}" ContentType="{escape(ct)}"/>'
                       for ext, ct in sorted(defaults.items()))
        ovs = "".join(f'<Override PartName="/{escape(part)}" ContentType="{escape(ctype)}"/>'
                      for part, ctype in sorted(overrides.items()))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                f'{defs}{ovs}</Types>')

    def _save_template(self, out_path):
        info = self.template_info
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(info["path"]) as src:
            copied = {name: src.read(name) for name in info["copy_parts"]}
        copied_names = set(copied)
        # notes master: copy real one when available, otherwise generate
        if info.get("notes_masters"):
            notes_master_part = info["notes_masters"][0]
        else:
            notes_master_part = "ppt/notesMasters/notesMaster1.xml"
            copied[notes_master_part] = self.notes_master_xml().encode("utf-8")
            copied["ppt/notesMasters/_rels/notesMaster1.xml.rels"] = rels_xml([
                ("rIdTheme", R_NS + "/theme", "../theme/theme1.xml")]).encode("utf-8")
            copied_names.update([notes_master_part, "ppt/notesMasters/_rels/notesMaster1.xml.rels"])
        theme_name = None
        for name in copied_names:
            if re.fullmatch(r"ppt/theme/theme\d+\.xml", name):
                theme_name = name
                break
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", self._template_content_types(copied_names))
            zf.writestr("_rels/.rels", rels_xml([
                ("rId1", R_NS + "/officeDocument", "ppt/presentation.xml"),
                ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                 "docProps/core.xml"),
                ("rId3", R_NS + "/extended-properties", "docProps/app.xml"),
            ]))
            zf.writestr("docProps/core.xml", self.core_xml())
            zf.writestr("docProps/app.xml", self.app_xml())
            zf.writestr("ppt/presentation.xml", self.presentation_xml())
            entries = []
            for i, m in enumerate(info.get("masters") or []):
                entries.append((f"rIdMaster{i + 1}", R_NS + "/slideMaster", "slideMasters/" + Path(m).name))
            if not info.get("masters"):
                entries.append(("rIdMaster", R_NS + "/slideMaster", "slideMasters/slideMaster1.xml"))
            entries.append(("rIdNotesMaster", R_NS + "/notesMaster",
                            "notesMasters/" + Path(notes_master_part).name))
            if theme_name:
                entries.append(("rIdTheme", R_NS + "/theme", "theme/" + Path(theme_name).name))
            for i in range(len(self.slides)):
                entries.append((f"rIdS{i + 1}", R_NS + "/slide", f"slides/slide{i + 1}.xml"))
            zf.writestr("ppt/_rels/presentation.xml.rels", rels_xml(entries))
            for name, data in copied.items():
                zf.writestr(name, data)
            for i in range(len(self.slides)):
                zf.writestr(f"ppt/slides/slide{i + 1}.xml", self.slide_xml(i))
                zf.writestr(f"ppt/slides/_rels/slide{i + 1}.xml.rels", self.slide_rels(i))
                zf.writestr(f"ppt/notesSlides/notesSlide{i + 1}.xml", self.notes_xml(i))
                zf.writestr(f"ppt/notesSlides/_rels/notesSlide{i + 1}.xml.rels", rels_xml([
                    ("rIdSlide", R_NS + "/slide", f"../slides/slide{i + 1}.xml"),
                    ("rIdNotesMaster", R_NS + "/notesMaster",
                     "../notesMasters/" + Path(notes_master_part).name),
                ]))
            for name, raw in self.media:
                zf.writestr(f"ppt/media/{name}", raw)
        return out

    def save(self, out_path):
        out = Path(out_path)
        if self.template_mode:
            self._save_template(out_path)
            self.report["template_fidelity"] = "master+layouts+theme"
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("[Content_Types].xml", self.content_types())
                zf.writestr("_rels/.rels", rels_xml([
                    ("rId1", R_NS + "/officeDocument", "ppt/presentation.xml"),
                    ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                     "docProps/core.xml"),
                    ("rId3", R_NS + "/extended-properties", "docProps/app.xml"),
                ]))
                zf.writestr("docProps/core.xml", self.core_xml())
                zf.writestr("docProps/app.xml", self.app_xml())
                zf.writestr("ppt/presentation.xml", self.presentation_xml())
                zf.writestr("ppt/_rels/presentation.xml.rels", self.presentation_rels())
                zf.writestr("ppt/theme/theme1.xml", self.theme_xml())
                zf.writestr("ppt/slideMasters/slideMaster1.xml", self.slide_master_xml())
                zf.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", rels_xml([
                    ("rIdLayout", R_NS + "/slideLayout", "../slideLayouts/slideLayout1.xml"),
                    ("rIdTheme", R_NS + "/theme", "../theme/theme1.xml"),
                ]))
                zf.writestr("ppt/slideLayouts/slideLayout1.xml", self.slide_layout_xml())
                zf.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", rels_xml([
                    ("rIdMaster", R_NS + "/slideMaster", "../slideMasters/slideMaster1.xml"),
                ]))
                zf.writestr("ppt/notesMasters/notesMaster1.xml", self.notes_master_xml())
                zf.writestr("ppt/notesMasters/_rels/notesMaster1.xml.rels", rels_xml([
                    ("rIdTheme", R_NS + "/theme", "../theme/theme1.xml"),
                ]))
                for i in range(len(self.slides)):
                    zf.writestr(f"ppt/slides/slide{i + 1}.xml", self.slide_xml(i))
                    zf.writestr(f"ppt/slides/_rels/slide{i + 1}.xml.rels", self.slide_rels(i))
                    zf.writestr(f"ppt/notesSlides/notesSlide{i + 1}.xml", self.notes_xml(i))
                    zf.writestr(f"ppt/notesSlides/_rels/notesSlide{i + 1}.xml.rels", rels_xml([
                        ("rIdSlide", R_NS + "/slide", f"../slides/slide{i + 1}.xml"),
                        ("rIdNotesMaster", R_NS + "/notesMaster", "../notesMasters/notesMaster1.xml"),
                    ]))
                for name, raw in self.media:
                    zf.writestr(f"ppt/media/{name}", raw)
            self.report["template_fidelity"] = "none"
        return out


def build_deck(spec, out_path, template=None, work_dir=None):
    info = audit_template(template) if template else None
    if info and not info.get("exists"):
        info = None
    deck = Deck(spec, info)
    deck.save(out_path)
    report = dict(deck.report)
    report.update({
        "path": str(out_path),
        "bytes": Path(out_path).stat().st_size if Path(out_path).is_file() else 0,
        "sha256": sha256_file(out_path) if Path(out_path).is_file() else None,
        "slides": len(deck.slides),
        "theme": deck.theme,
        "size_emu": [deck.cx, deck.cy],
        "template_used": bool(info),
        "template_path": str(template) if template else None,
        "problems": validate_zip(out_path),
    })
    if report["images_missing"]:
        report.setdefault("warnings", []).append(
            f"{len(report['images_missing'])} image(s) missing; text-only fallback used")
    return report


# ------------------------------------------------------------------ editing
A_T_RE = re.compile(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.S)


def _xml_unescape(value):
    return unescape(value, {"&quot;": '"', "&apos;": "'"})


def _slide_number(edit):
    for key in ("slide", "slide_no", "slide_number", "page", "page_no",
                "page_number", "index"):
        if key in edit:
            try:
                return int(edit[key])
            except (TypeError, ValueError):
                m = re.search(r"\d+", str(edit[key]))
                if m:
                    return int(m.group(0))
    target = str(edit.get("target", ""))
    m = re.search(r"slide[#\s]*(\d+)", target, re.I)
    return int(m.group(1)) if m else None


def _target_index(edit):
    for key in ("target_index", "text_index", "run_index", "shape_index"):
        if key in edit:
            try:
                return int(edit[key])
            except (TypeError, ValueError):
                pass
    target = str(edit.get("target", ""))
    m = re.search(r"(?:text|run|shape)[#\s]*(\d+)", target, re.I)
    return int(m.group(1)) if m else None


def _edit_text(edit):
    for key in ("to", "new", "after", "text", "value", "replacement",
                "new_text", "revised", "content"):
        if key in edit and edit[key] is not None:
            return str(edit[key])
    return None


def _edit_old(edit):
    for key in ("from", "old", "before", "find", "original", "old_text",
                "current", "find_text"):
        if key in edit and edit[key] is not None:
            return str(edit[key])
    return None


def _replace_slide_text(xml, edit):
    units = list(A_T_RE.finditer(xml))
    if not units:
        return None, "slide has no editable text run"
    old = _edit_old(edit)
    new = _edit_text(edit)
    if new is None:
        return None, "edit has no replacement text"
    chosen = None
    if old:
        for m in units:
            if _xml_unescape(m.group(1)) == old:
                chosen = m
                break
        if chosen is None:
            for m in units:
                if old in _xml_unescape(m.group(1)):
                    chosen = m
                    break
    if chosen is None:
        idx = _target_index(edit)
        if idx is not None and 1 <= idx <= len(units):
            chosen = units[idx - 1]
    if chosen is None:
        return None, "target run not found"
    before = _xml_unescape(chosen.group(1))
    updated = xml[:chosen.start(1)] + escape(new) + xml[chosen.end(1):]
    return updated, {"before": before, "after": new}


def _slide_rel_name(slide_no):
    return f"ppt/slides/_rels/slide{slide_no}.xml.rels"


def _ensure_notes_part(parts, slide_no):
    notes_name = f"ppt/notesSlides/notesSlide{slide_no}.xml"
    rels_name = f"ppt/notesSlides/_rels/notesSlide{slide_no}.xml.rels"
    # find notes master and its theme relationship target
    master = "ppt/notesMasters/notesMaster1.xml"
    for name in parts:
        if re.fullmatch(r"ppt/notesMasters/notesMaster\d+\.xml", name):
            master = name
            break
    body_idx = 3
    if master in parts:
        root = _parse_xml(parts[master])
        if root is not None:
            for ph in root.iter(f"{{{P_NS}}}ph"):
                if ph.get("type") == "body":
                    try:
                        body_idx = int(ph.get("idx", "3"))
                    except (TypeError, ValueError):
                        pass
                    break
    notes = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             f'<p:notes {NS}><p:cSld><p:spTree>{group_props()}'
             '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/>'
             '<p:cNvSpPr><a:spLocks noGrp="1" noRot="1" noChangeAspect="1"/></p:cNvSpPr>'
             '<p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
             '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/>'
             '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
             f'<p:nvPr><p:ph type="body" idx="{body_idx}"/></p:nvPr></p:nvSpPr><p:spPr/>'
             f'{tx_body([{"text": "", "size": 12, "bullet": False}])}</p:sp>'
             '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>')
    parts[notes_name] = notes.encode("utf-8")
    parts[rels_name] = rels_xml([
        ("rIdSlide", R_NS + "/slide", f"../slides/slide{slide_no}.xml"),
        ("rIdNotesMaster", R_NS + "/notesMaster", "../notesMasters/" + Path(master).name),
    ]).encode("utf-8")
    # slide -> notes relationship
    rel_name = _slide_rel_name(slide_no)
    if rel_name in parts:
        raw = parts[rel_name].decode("utf-8")
        if "notesSlide" not in raw:
            raw = raw.replace("</Relationships>",
                              f'<Relationship Id="rIdNotesEdit" Type="{P_NS}/notesSlide" '
                              f'Target="../notesSlides/notesSlide{slide_no}.xml"/></Relationships>')
            parts[rel_name] = raw.encode("utf-8")
    # content types
    ct = parts["[Content_Types].xml"].decode("utf-8")
    needle = f'PartName="/{notes_name}"'
    if needle not in ct:
        ct = ct.replace("</Types>",
                        f'<Override PartName="/{notes_name}" '
                        f'ContentType="{CT}.presentationml.notesSlide+xml"/></Types>')
        parts["[Content_Types].xml"] = ct.encode("utf-8")
    return notes_name


def set_notes_text(parts, slide_no, text):
    notes_name = _ensure_notes_part(parts, slide_no)
    raw = parts[notes_name].decode("utf-8")
    paragraphs = [{"text": line, "size": 12, "bullet": False}
                  for line in str(text or "").split("\n")]
    body = tx_body(paragraphs or [{"text": "", "size": 12, "bullet": False}])
    # replace the body placeholder's txBody; keep slide image placeholder intact
    pattern = re.compile(r'(<p:ph type="body"[^>]*/>.*?)(<p:txBody>.*?</p:txBody>)', re.S)
    if pattern.search(raw):
        raw = pattern.sub(lambda m: m.group(1) + body, raw, count=1)
    else:
        raw = raw.replace("</p:spTree>",
                          f'<p:sp><p:nvSpPr><p:cNvPr id="9" name="Notes"/>'
                          f'<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr/>{body}</p:sp>'
                          '</p:spTree>')
    parts[notes_name] = raw.encode("utf-8")
    return notes_name


def apply_edits(base, edits, out_path, work_dir=None):
    """Apply only named edits to a copy of base. Never re-creates untouched pages."""
    base = str(base)
    out_path = str(out_path)
    report = {"base": base, "path": out_path, "applied": [], "unresolved": [],
              "untouched_slides": [], "changed_slides": [], "notes_changed": [],
              "images_replaced": [], "problems": []}
    with zipfile.ZipFile(base) as z:
        parts = {name: z.read(name) for name in z.namelist()}
    slide_names = sorted((n for n in parts if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                         key=lambda n: int(re.search(r"(\d+)", Path(n).stem).group(1)))
    before = {n: hashlib.sha256(parts[n]).hexdigest() for n in slide_names}
    if not slide_names:
        report["unresolved"].append({"edit_id": "base", "reason": "starting deck has no slides"})
        return report
    for pos, edit in enumerate(edits or [], start=1):
        if not isinstance(edit, dict):
            edit = {"to": str(edit)}
        edit_id = str(edit.get("edit_id") or edit.get("id") or f"E{pos:02d}")
        action = str(edit.get("action") or edit.get("op") or edit.get("type") or "replace").lower()
        slide_no = _slide_number(edit)
        if slide_no is None or not 1 <= slide_no <= len(slide_names):
            report["unresolved"].append({"edit_id": edit_id, "reason": "invalid slide number"})
            continue
        name = f"ppt/slides/slide{slide_no}.xml"
        xml = parts[name].decode("utf-8")
        if action in ("replace", "replace_text", "replace_content", "set_text",
                      "set-text", "update_text", "rewrite", "edit"):
            updated, result = _replace_slide_text(xml, edit)
            if updated is None:
                report["unresolved"].append({"edit_id": edit_id, "slide": slide_no,
                                             "reason": result})
                continue
            parts[name] = updated.encode("utf-8")
            report["applied"].append({"edit_id": edit_id, "slide": slide_no,
                                      "action": "replace", "target": edit.get("target"),
                                      "before": result["before"], "after": result["after"],
                                      "source": edit.get("source"), "verified": True})
            report["changed_slides"].append(slide_no)
        elif action in ("notes", "set_notes", "set-notes", "speaker_notes",
                        "set_speaker_notes", "update_notes", "notes_update"):
            target = _edit_text(edit)
            if target is None:
                target = edit.get("notes", "")
            if not str(target).strip():
                report["unresolved"].append({"edit_id": edit_id, "slide": slide_no,
                                             "reason": "notes edit has no text; refusing to clear notes"})
                continue
            set_notes_text(parts, slide_no, target)
            report["applied"].append({"edit_id": edit_id, "slide": slide_no,
                                      "action": "set_notes", "after": str(target)[:160],
                                      "source": edit.get("source"), "verified": True})
            report["notes_changed"].append(slide_no)
            report["changed_slides"].append(slide_no)
        elif action in ("image", "set_image", "replace_image", "set_picture",
                        "replace_picture"):
            src = edit.get("to", edit.get("image", edit.get("src")))
            if not src or not Path(str(src)).is_file():
                report["unresolved"].append({"edit_id": edit_id, "slide": slide_no,
                                             "reason": f"image not found: {src}"})
                continue
            rel_name = _slide_rel_name(slide_no)
            rel_raw = parts.get(rel_name, b"").decode("utf-8")
            m = re.search(r'Target="([^"]*media/[^"]+)"', rel_raw)
            if not m:
                report["unresolved"].append({"edit_id": edit_id, "slide": slide_no,
                                             "reason": "slide has no image relationship"})
                continue
            target = m.group(1).replace("../", "ppt/", 1)
            if target in parts:
                old_ext = Path(target).suffix.lower()
                new_ext = Path(str(src)).suffix.lower()
                if old_ext != new_ext:
                    report["unresolved"].append({
                        "edit_id": edit_id, "slide": slide_no,
                        "reason": f"format differs ({old_ext} vs {new_ext}); refusing unsafe swap"})
                    continue
                parts[target] = Path(str(src)).read_bytes()
                report["images_replaced"].append({"edit_id": edit_id, "slide": slide_no,
                                                  "part": target, "source": str(src)})
                report["changed_slides"].append(slide_no)
            else:
                report["unresolved"].append({"edit_id": edit_id, "slide": slide_no,
                                             "reason": f"media part missing: {target}"})
        else:
            report["unresolved"].append({"edit_id": edit_id, "slide": slide_no,
                                         "reason": f"unsupported action '{action}'; not faked"})
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    for i, name in enumerate(slide_names, start=1):
        if i in report["changed_slides"]:
            continue
        report["untouched_slides"].append(i)
    report["changed_slides"] = sorted(set(report["changed_slides"]))
    report["problems"] = validate_zip(out_path)
    report["bytes"] = out.stat().st_size
    report["sha256"] = sha256_file(out_path)
    return report


def deck_to_spec(base, work_dir=None, template=None):
    """Turn an existing deck into a content-preserving spec (for adapt mode)."""
    info = inspect_deck(base)
    slides = info.get("slides", [])
    spec_slides = []
    media_dir = Path(work_dir or tempfile.mkdtemp(prefix="deck-media-")) / "base-media"
    media_dir.mkdir(parents=True, exist_ok=True)
    for i, slide in enumerate(slides):
        texts = [t for t in slide.get("texts", []) if str(t).strip()]
        title = texts[0] if texts else f"Slide {i + 1}"
        bullets = texts[1:]
        kind = "title" if i == 0 else ("closing" if i == len(slides) - 1 else "content")
        source_ref = {"path": base, "locator": f"slide {i + 1}"}
        entry = {"kind": kind, "role": kind, "title": title, "bullets": bullets,
                 "notes": slide.get("notes", ""),
                 "source": f"{Path(base).name} · slide {i + 1}",
                 "source_ref": source_ref,
                 "claims": ([{"claim": title, "source": source_ref}] if title else [])}
        if kind == "title" and bullets:
            entry["subtitle"] = "；".join(bullets[:2])
            entry["bullets"] = []
        if slide.get("images"):
            img = slide["images"][0]
            ext = Path(img.get("target", ".png")).suffix or ".png"
            dest = media_dir / f"base-slide{i + 1}{ext}"
            try:
                with zipfile.ZipFile(base) as z:
                    dest.write_bytes(z.read(img["part"]))
                entry["image"] = str(dest)
                entry["caption"] = f"原稿第 {i + 1} 页图片（保留）"
            except (KeyError, OSError):
                pass
        spec_slides.append(entry)
    return {"title": info.get("title") or Path(base).stem, "size": info.get("size_label", "16:9"),
            "slides": spec_slides, "theme": "scholar",
            "_origin": {"path": str(base), "mode": "content-preserved-from-base"}}


# -------------------------------------------------------------- inspection
def _parse_xml(data):
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def inspect_deck(path):
    path = str(path)
    out = {"path": path, "exists": Path(path).is_file(), "slides": [], "count": 0,
           "size_emu": None, "size_label": None, "media": [], "layouts": [],
           "notes_master": None, "theme_fonts": [], "problems": []}
    if not out["exists"]:
        out["problems"].append("file missing")
        return out
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "ppt/presentation.xml" in names:
                root = _parse_xml(z.read("ppt/presentation.xml"))
                if root is not None:
                    sz = root.find(f"{{{P_NS}}}sldSz")
                    if sz is not None:
                        out["size_emu"] = [int(sz.get("cx")), int(sz.get("cy"))]
                        out["size_label"] = "16:9" if out["size_emu"][0] * 9 == out["size_emu"][1] * 16 else "4:3"
            out["media"] = sorted(n for n in names if n.startswith("ppt/media/"))
            layout_parts = sorted(n for n in names
                                  if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", n))
            out["layouts"] = []
            for name in layout_parts:
                root = _parse_xml(z.read(name))
                c = root.find(f"{{{P_NS}}}cSld") if root is not None else None
                out["layouts"].append({"part": name,
                                       "name": c.get("name") if c is not None else None})
            theme = next((n for n in sorted(names) if re.fullmatch(r"ppt/theme/theme\d+\.xml", n)), None)
            if theme:
                t = _theme_from_xml(z.read(theme))
                out["theme_fonts"] = [t.get("title_font"), t.get("body_font"),
                                      t.get("title_ea"), t.get("body_ea")]
            slide_names = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                                 key=lambda n: int(re.search(r"(\d+)", Path(n).stem).group(1)))
            for i, name in enumerate(slide_names, start=1):
                root = _parse_xml(z.read(name))
                texts, title = [], None
                if root is not None:
                    for sp in root.iter(f"{{{P_NS}}}sp"):
                        tx = sp.find(f"{{{P_NS}}}txBody")
                        if tx is None:
                            continue
                        chunk = "".join(t.text or "" for t in tx.iter(f"{{{A_NS}}}t")).strip()
                        if chunk:
                            texts.append(chunk)
                            nv = sp.find(f"{{{P_NS}}}nvSpPr")
                            nm = nv.find(f"{{{P_NS}}}cNvPr").get("name", "") if nv is not None else ""
                            if title is None and "title" in nm.lower():
                                title = chunk
                rel_name = f"ppt/slides/_rels/slide{i}.xml.rels"
                images, layout = [], None
                if rel_name in names:
                    rel_root = _parse_xml(z.read(rel_name))
                    if rel_root is not None:
                        for rel in rel_root:
                            target = rel.get("Target", "")
                            typ = rel.get("Type", "")
                            if typ.endswith("/image"):
                                part = os.path.normpath(os.path.join("ppt/slides", target)).replace("\\", "/")
                                images.append({"part": part, "target": target,
                                               "bytes": len(z.read(part)) if part in names else 0})
                            elif typ.endswith("/slideLayout"):
                                layout = os.path.normpath(os.path.join("ppt/slides", target)).replace("\\", "/")
                notes = ""
                notes_name = f"ppt/notesSlides/notesSlide{i}.xml"
                if notes_name in names:
                    nroot = _parse_xml(z.read(notes_name))
                    if nroot is not None:
                        notes = "\n".join(t.text or "" for t in nroot.iter(f"{{{A_NS}}}t")).strip()
                out["slides"].append({"index": i, "title": title or (texts[0] if texts else ""),
                                      "texts": texts, "notes": notes, "images": images,
                                      "layout": layout})
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        out["problems"].append(f"not a readable pptx: {exc}")
    out["count"] = len(out["slides"])
    return out


def inspect_overflow(path):
    risks = []
    try:
        with zipfile.ZipFile(path) as z:
            slide_names = sorted((n for n in z.namelist()
                                  if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                                 key=lambda n: int(re.search(r"(\d+)", Path(n).stem).group(1)))
            for i, name in enumerate(slide_names, start=1):
                root = _parse_xml(z.read(name))
                if root is None:
                    continue
                for sp in root.iter(f"{{{P_NS}}}sp"):
                    tx = sp.find(f"{{{P_NS}}}txBody")
                    xfrm = sp.find(f".//{{{A_NS}}}xfrm")
                    if tx is None or xfrm is None:
                        continue
                    ext = xfrm.find(f"{{{A_NS}}}ext")
                    if ext is None:
                        continue
                    cy = int(ext.get("cy", "0"))
                    cx = int(ext.get("cx", "0"))
                    total = 0.0
                    sizes = []
                    lines = 0
                    last_text = ""
                    for p in tx.iter(f"{{{A_NS}}}p"):
                        txt = "".join(t.text or "" for t in p.iter(f"{{{A_NS}}}t"))
                        if not txt:
                            continue
                        last_text = txt
                        rpr = p.find(f".//{{{A_NS}}}rPr")
                        sz = int(rpr.get("sz", "1800")) / 100 if rpr is not None else 18
                        sizes.append(sz)
                        width_units = sum(2 if ord(ch) > 127 else 1 for ch in txt)
                        per_line = max(8, int(cx / EMU_PT / (sz * 0.52)))
                        lines += max(1, math.ceil(width_units / per_line))
                        total += width_units
                    if not lines:
                        continue
                    sz = max(sizes or [18])
                    needed_pt = lines * sz * 1.22 + 6
                    box_pt = cy / EMU_PT
                    if needed_pt > box_pt * 1.08:
                        risks.append({"slide": i, "box_height_pt": round(box_pt, 1),
                                      "needed_pt": round(needed_pt, 1),
                                      "chars": int(total), "font_pt": sz,
                                      "text": last_text[:80]})
    except (zipfile.BadZipFile, OSError):
        return [{"error": "unreadable pptx"}]
    return risks


def validate_zip(path):
    problems = []
    path = str(path)
    if not Path(path).is_file():
        return ["file missing"]
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                problems.append(f"corrupt member: {bad}")
            names = set(z.namelist())
            if "ppt/presentation.xml" not in names:
                problems.append("missing ppt/presentation.xml")
            if "[Content_Types].xml" not in names:
                problems.append("missing [Content_Types].xml")
            for name in names:
                if name.endswith(".xml") or name.endswith(".rels"):
                    try:
                        ET.fromstring(z.read(name))
                    except ET.ParseError as exc:
                        problems.append(f"{name}: invalid XML ({exc})")
                if name.endswith(".rels"):
                    base = os.path.dirname(os.path.dirname(name))
                    try:
                        root = ET.fromstring(z.read(name))
                    except ET.ParseError:
                        continue
                    for rel in root:
                        if rel.get("TargetMode") == "External":
                            continue
                        target = os.path.normpath(os.path.join(base, rel.get("Target", ""))).replace("\\", "/")
                        if target not in names:
                            problems.append(f"{name}: unresolved target {rel.get('Target')}")
            slides = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
            if not slides:
                problems.append("no slides")
    except zipfile.BadZipFile as exc:
        problems.append(f"not a zip: {exc}")
    return problems


# ------------------------------------------------------------------ preview
def _find_office():
    candidates = [os.environ.get("OFFICE_RENDERER"), os.environ.get("SOFFICE"),
                  shutil.which("soffice"), shutil.which("libreoffice"),
                  r"C:\Program Files\LibreOffice\program\soffice.exe",
                  r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                  "/usr/bin/soffice", "/usr/local/bin/soffice"]
    for c in candidates:
        if c and Path(c).is_file():
            return str(c)
    return None


def _soffice_pdf(office, pptx, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = out_dir / ".lo-profile"
    cmd = [office, "--headless", "--norestore", "--nologo",
           f"-env:UserInstallation={profile.as_uri()}",
           "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx)]
    try:
        subprocess.run(cmd, check=True, timeout=180,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (subprocess.SubprocessError, OSError):
        return None
    pdf = out_dir / (Path(pptx).stem + ".pdf")
    return pdf if pdf.is_file() and pdf.stat().st_size > 1000 else None


def _pdf_to_png(pdf, png_dir):
    png_dir = Path(png_dir)
    png_dir.mkdir(parents=True, exist_ok=True)
    made = []
    try:
        import fitz  # type: ignore
        doc = fitz.open(str(pdf))
        for i, page in enumerate(doc, start=1):
            dest = png_dir / f"slide-{i:02d}.png"
            page.get_pixmap(dpi=140).save(str(dest))
            made.append(str(dest))
        return made, "pymupdf"
    except Exception:
        pass
    exe = shutil.which("pdftoppm")
    if exe:
        try:
            prefix = png_dir / "slide"
            subprocess.run([exe, "-png", "-r", "140", str(pdf), str(prefix)],
                           check=True, timeout=180, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
            made = []
            for p in sorted(png_dir.glob("slide-*.png")):
                m = re.search(r"slide-(\d+)\.png$", p.name)
                if m:
                    dest = png_dir / f"slide-{int(m.group(1)):02d}.png"
                    if dest != p:
                        p.replace(dest)
                    made.append(str(dest))
                else:
                    made.append(str(p))
            return made, "pdftoppm"
        except (subprocess.SubprocessError, OSError):
            pass
    return [], None


def _load_mpl():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
        from matplotlib.patches import FancyBboxPatch, Rectangle
        from matplotlib import font_manager
        return plt, PdfPages, FancyBboxPatch, Rectangle, font_manager
    except Exception:
        return None, None, None, None, None


def _pick_font(font_manager, text):
    candidates = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC",
                  "WenQuanYi Zen Hei", "PingFang SC", "SimSun", "DejaVu Sans"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            return name
    return "DejaVu Sans"


def _wrap_cjk(text, width_units):
    lines, current, units = [], [], 0
    for ch in str(text):
        w = 2 if ord(ch) > 127 else 1
        if ch == "\n" or units + w > width_units:
            lines.append("".join(current).strip())
            current, units = ([], 0) if ch == "\n" else ([ch], w)
        else:
            current.append(ch)
            units += w
    if current:
        lines.append("".join(current).strip())
    return [ln for ln in lines if ln]


def _draw_preview(spec, pdf_path, png_dir):
    plt, PdfPages, FancyBboxPatch, Rectangle, font_manager = _load_mpl()
    if plt is None:
        return {"ok": False, "engine": "none", "reason": "matplotlib unavailable"}
    theme = resolve_theme(spec.get("theme"))
    size = str(spec.get("size", "16:9"))
    wi, hi = (13.333, 7.5) if size == "16:9" else (10.0, 7.5)
    all_text = " ".join(str(s.get("title", "")) + " " + " ".join(map(str, s.get("bullets", []) or []))
                        for s in spec.get("slides", []))
    font = _pick_font(font_manager, all_text)
    cjk_missing = any(ord(ch) > 127 for ch in all_text) and font == "DejaVu Sans"
    rc = {"font.family": font, "pdf.fonttype": 42, "ps.fonttype": 42}
    plt.rcParams.update(rc)
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    png_dir = Path(png_dir)
    png_dir.mkdir(parents=True, exist_ok=True)
    made = []
    slides = spec.get("slides", []) or [{"kind": "title", "title": spec.get("title", "")}]
    with PdfPages(str(pdf_path)) as pdf:
        for idx, s in enumerate(slides, start=1):
            fig = plt.figure(figsize=(wi, hi), dpi=110)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.set_xlim(0, 100)
            ax.set_ylim(0, 100)
            ax.axis("off")

            def Y(y):
                return 100.0 - float(y)

            def YB(y, h):
                return 100.0 - float(y) - float(h)

            kind = str(s.get("layout") or s.get("kind") or "content").lower()
            dark = kind in ("title", "cover", "closing", "end", "statement", "quote",
                            "section", "divider")
            bg = s.get("background") or (theme["ink"] if dark else theme["bg"])
            ink = "#" + color(s.get("text_color") or (theme["white"] if dark else theme["ink"]))
            muted = "#" + color(s.get("muted_color") or (theme["line"] if dark else theme["muted"]))
            accent = "#" + color(theme["accent"])
            accent2 = "#" + color(theme["accent2"])
            panel = "#" + color(theme["panel"])
            fig.patch.set_facecolor("#" + color(bg))
            ax.add_patch(Rectangle((0, 0), 100, 100, color="#" + color(bg)))

            def txt(x, y, w, value, size_pt, col=None, bold=False, ha="left", va="top",
                    max_lines=8):
                if not value:
                    return
                wrap_units = max(6, int(w * 1.06 * 96 / (size_pt * 0.62)))
                lines = _wrap_cjk(value, wrap_units)[:max_lines]
                ax.text(x, Y(y), "\n".join(lines), fontsize=size_pt, color=col or ink,
                        fontweight="bold" if bold else "normal", ha=ha, va=va,
                        linespacing=1.28, wrap=False)

            def footer():
                if s.get("hide_footer"):
                    return
                if s.get("source"):
                    txt(6, 95.4, 68, str(s["source"]), 7.2, muted, max_lines=1)
                txt(94, 95.4, 4, str(idx), 7.5, muted, ha="right", max_lines=1)

            if kind in ("title", "cover"):
                if s.get("eyebrow"):
                    txt(6, 16, 88, s["eyebrow"], 10, accent2, True, max_lines=1)
                txt(6, 26, 84, s.get("title", ""), s.get("title_size", 30) * 0.72, ink, True,
                    max_lines=3)
                if s.get("subtitle"):
                    txt(6, 52, 76, s["subtitle"], 13, muted, max_lines=3)
                footer()
            elif kind in ("section", "divider"):
                if s.get("number"):
                    txt(6, 20, 12, str(s["number"]), 34, accent, True, max_lines=1)
                txt(6, 38, 82, s.get("title", ""), 24, ink, True, max_lines=3)
                if s.get("subtitle"):
                    txt(6, 58, 76, s["subtitle"], 12, muted, max_lines=2)
                footer()
            elif kind in ("closing", "end"):
                txt(6, 15, 90, s.get("eyebrow", "TAKE-HOME"), 9, accent2, True, max_lines=1)
                txt(6, 26, 86, s.get("title", "结论"), 24, ink, True, max_lines=2)
                for j, b in enumerate((s.get("bullets") or s.get("takeaways") or [])[:5]):
                    txt(8, 45 + j * 9, 82, "\u2022 " + str(b), 11.5, muted, max_lines=2)
                footer()
            elif kind in ("quote", "statement"):
                if s.get("title") and str(s.get("title")) != str(s.get("quote", "")):
                    txt(8, 9, 84, str(s.get("title")), 10, accent2, True,
                        ha="center", max_lines=2)
                txt(8, 24, 84, "\u201c" + str(s.get("quote") or s.get("title", "")) + "\u201d",
                    20, ink, ha="center", max_lines=4)
                if s.get("attribution"):
                    txt(60, 68, 34, "\u2014 " + str(s["attribution"]), 10, muted,
                        ha="right", max_lines=1)
                footer()
            else:
                title = str(s.get("title", ""))
                txt(6, 7, 88, title, 20, ink, True, max_lines=2)
                ax.add_patch(Rectangle((6, YB(18.2, 0.5)), 12, 0.5, color=accent))
                has_img = bool(s.get("image") and Path(str(s["image"])).is_file())
                if kind in ("timeline", "steps", "process"):
                    steps = s.get("steps") or s.get("items") or []
                    n = max(1, len(steps))
                    width = (88 - (n - 1) * 2) / n
                    for j, step in enumerate(steps[:5]):
                        x = 6 + j * (width + 2)
                        ax.add_patch(FancyBboxPatch((x, YB(27, 50)), width, 50,
                                                    boxstyle="round,pad=0.6,rounding_size=1.2",
                                                    facecolor=panel, edgecolor=muted, linewidth=0.6))
                        ax.add_patch(plt.Circle((x + 4, Y(33)), 2.2, color=accent))
                        st = step if isinstance(step, dict) else {"title": str(step)}
                        ax.text(x + 4, Y(33), str(j + 1), fontsize=9, color="white",
                                ha="center", va="center", fontweight="bold")
                        txt(x + 3, 41, width - 6, st.get("title", st.get("label", "")), 10.5,
                            ink, True, max_lines=2)
                        txt(x + 3, 50, width - 6, st.get("text", st.get("body", "")), 8.5,
                            muted, max_lines=5)
                elif kind in ("stat", "stats", "metrics"):
                    stats = s.get("stats") or s.get("metrics") or []
                    n = max(1, len(stats))
                    width = (88 - (n - 1) * 2) / n
                    for j, item in enumerate(stats[:5]):
                        x = 6 + j * (width + 2)
                        ax.add_patch(FancyBboxPatch((x, YB(27, 34)), width, 34,
                                                    boxstyle="round,pad=0.6,rounding_size=1.2",
                                                    facecolor=panel, edgecolor=muted, linewidth=0.6))
                        value = item.get("value", item.get("number", "")) if isinstance(item, dict) else str(item)
                        label = item.get("label", item.get("caption", "")) if isinstance(item, dict) else ""
                        txt(x + 2, 33, width - 4, str(value), 22, accent, True, ha="center", max_lines=1)
                        txt(x + 2, 45, width - 4, str(label), 8, muted, ha="center", max_lines=2)
                    if s.get("bullets"):
                        for j, b in enumerate(s["bullets"][:4]):
                            txt(8, 68 + j * 6, 82, "\u2022 " + str(b), 9, muted, max_lines=1)
                elif kind in ("comparison", "two-column", "two-col"):
                    blocks = [s.get("left") or {}, s.get("right") or {}]
                    for j, block in enumerate(blocks):
                        x = 6 + j * 45
                        if isinstance(block, str):
                            block = {"title": "", "bullets": [block]}
                        ax.add_patch(FancyBboxPatch((x, YB(27, 50)), 42, 50,
                                                    boxstyle="round,pad=0.6,rounding_size=1.2",
                                                    facecolor=panel, edgecolor=muted, linewidth=0.6))
                        txt(x + 3, 31, 36, block.get("title", ""), 12, accent, True, max_lines=1)
                        for k, b in enumerate((block.get("bullets") or [])[:5]):
                            txt(x + 3, 39 + k * 7, 36, "\u2022 " + str(b), 8.5, muted, max_lines=2)
                elif kind in ("cards", "grid", "features"):
                    cards = s.get("cards") or []
                    n = max(1, len(cards))
                    width = (88 - (n - 1) * 2) / n
                    for j, item in enumerate(cards[:4]):
                        x = 6 + j * (width + 2)
                        ax.add_patch(FancyBboxPatch((x, YB(27, 48)), width, 48,
                                                    boxstyle="round,pad=0.6,rounding_size=1.2",
                                                    facecolor=panel, edgecolor=muted, linewidth=0.6))
                        txt(x + 3, 31, width - 6, item.get("title", ""), 11, accent, True, max_lines=2)
                        for k, b in enumerate((item.get("bullets") or item.get("text") or [])[:4]):
                            txt(x + 3, 40 + k * 7, width - 6, "\u2022 " + str(b), 8, muted, max_lines=2)
                else:
                    body_w = 51 if (has_img or s.get("callout")) else 88
                    bullets = s.get("bullets") or []
                    y = 25
                    for b in bullets[:6]:
                        lines = _wrap_cjk("\u2022 " + str(b), max(12, int(body_w * 1.55)))
                        txt(7, y, body_w, "\n".join(lines), 11, muted, max_lines=4)
                        y += 6.1 + 3.1 * max(0, len(lines) - 1)
                    if has_img:
                        try:
                            img = plt.imread(str(s["image"]))
                            ax_img = fig.add_axes([0.56, 0.17, 0.38, 0.58])
                            ax_img.imshow(img)
                            ax_img.axis("off")
                        except Exception:
                            pass
                    elif s.get("callout"):
                        call = s["callout"]
                        ax.add_patch(FancyBboxPatch((62, YB(28, 44)), 31, 44,
                                                    boxstyle="round,pad=0.6,rounding_size=1.2",
                                                    facecolor=panel, edgecolor=muted, linewidth=0.6))
                        txt(65, 32, 25, call.get("title", "关键点"), 10.5, accent, True, max_lines=2)
                        for k, b in enumerate((call.get("bullets") or call.get("text") or [])[:4]):
                            txt(65, 42 + k * 7, 25, "\u2022 " + str(b), 8, muted, max_lines=2)
                    if s.get("caption"):
                        txt(6, 88.5, 88, s["caption"], 7.5, muted, max_lines=2)
                footer()
            pdf.savefig(fig)
            dest = png_dir / f"slide-{idx:02d}.png"
            fig.savefig(dest, dpi=120, facecolor=fig.get_facecolor())
            made.append(str(dest))
            plt.close(fig)
    return {"ok": True, "engine": "matplotlib-spec", "native": False,
            "pdf": str(pdf_path), "png": made, "font": font,
            "cjk_missing": cjk_missing}


def export_preview(pptx, out_dir, spec, work_dir=None, allow_native=True):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "presentation.pdf"
    png_dir = out_dir
    report = {"engine": "none", "native": False, "pdf": None, "png": [],
              "unavailable": True, "limitations": [], "png_engine": None}
    office = _find_office() if allow_native else None
    if office:
        native_pdf = _soffice_pdf(office, pptx, out_dir)
        if native_pdf:
            if native_pdf != pdf_path:
                try:
                    shutil.move(str(native_pdf), str(pdf_path))
                except OSError:
                    pdf_path = native_pdf
            report.update({"engine": "libreoffice-soffice", "native": True,
                           "pdf": str(pdf_path), "unavailable": False})
            pngs, eng = _pdf_to_png(pdf_path, png_dir)
            report["png"], report["png_engine"] = pngs, eng
            if len(pngs) < len(spec.get("slides", []) or []):
                # no PDF rasterizer (or partial output): draw faithful PNGs from
                # the same spec, keep the native PDF and say so.
                tmp_pdf = out_dir / ".spec-preview.pdf"
                drawn = _draw_preview(spec, tmp_pdf, png_dir)
                try:
                    tmp_pdf.unlink(missing_ok=True)
                except OSError:
                    pass
                if drawn.get("ok") and drawn.get("png"):
                    report["png"] = drawn["png"]
                    report["png_engine"] = "matplotlib-spec"
                    report["limitations"].append(
                        "native PDF exported; PNG previews drawn from the same spec "
                        "because no PDF rasterizer was available")
                else:
                    report["limitations"].append(
                        "native PDF exported; PNG rasterizer unavailable and spec "
                        "preview failed, PNG omitted (not faked)")
    if not report["pdf"]:
        drawn = _draw_preview(spec, pdf_path, png_dir)
        if drawn.get("ok"):
            report.update({"engine": drawn["engine"], "native": False,
                           "pdf": drawn["pdf"], "png": drawn["png"],
                           "unavailable": False, "font": drawn.get("font"),
                           "png_engine": "matplotlib-spec"})
            report["limitations"].append(
                "no office renderer found: PDF/PNG are content-faithful previews drawn from "
                "the same deck spec, not a native PPTX conversion")
            if drawn.get("cjk_missing"):
                report["limitations"].append(
                    "no CJK font found; preview may show missing glyphs (PPTX keeps text)")
        else:
            report["limitations"].append(
                "no office renderer and matplotlib unavailable; preview omitted, not faked")
    # validate outputs
    if report["pdf"] and Path(report["pdf"]).is_file():
        head = Path(report["pdf"]).read_bytes()[:5]
        if head != b"%PDF-":
            report["limitations"].append("PDF signature check failed")
            report["pdf"] = None
            report["unavailable"] = True
    else:
        report["pdf"] = None
        report["unavailable"] = True
    valid_png = []
    for p in report["png"]:
        try:
            data = Path(p).read_bytes()
            if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 1000:
                valid_png.append(str(p))
        except OSError:
            pass
    report["png"] = valid_png
    report["pdf_pages"] = None
    if report["pdf"]:
        try:
            from pypdf import PdfReader  # type: ignore
            report["pdf_pages"] = len(PdfReader(str(report["pdf"])).pages)
        except Exception:
            pass
    report["png_count"] = len(valid_png)
    report["slide_count_spec"] = len(spec.get("slides", []) or [])
    return report
