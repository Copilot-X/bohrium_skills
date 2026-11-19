#!/usr/bin/env python3
"""Build a real, editable .pptx (OOXML) with the standard library only.

python-pptx is not installed in the evaluation image, so this script writes the
PresentationML package directly. The output opens in PowerPoint / LibreOffice
Impress / WPS and keeps a slide master + layout, plain text boxes (so every
string stays editable) and per-slide speaker notes.

Spec format:

{
  "title": "deck title",
  "size": "16:9",                       # or "4:3", default 16:9
  "slides": [
    {"kind": "title", "title": "...", "subtitle": "...", "notes": "..."},
    {"kind": "content", "title": "结论句", "bullets": ["...", "..."],
     "image": "materials/fig2a.png", "caption": "Fig. 2a",
     "footer": "Zhang et al., Nature 2024", "notes": "讲稿 40 秒"},
    {"kind": "section", "title": "第二部分"},
    {"kind": "title-only", "title": "..."}
  ]
}

Usage:
    python3 make_pptx.py --spec deck.json --out /app/output/deck.pptx
    python3 make_pptx.py --check /app/output/deck.pptx
"""
import argparse
import json
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument"

NS = f'xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"'

# 16:9 = 12192000 x 6858000 EMU ; 4:3 = 9144000 x 6858000
SIZES = {"16:9": (12192000, 6858000), "4:3": (9144000, 6858000)}

# A calm academic palette.  Every value can be overridden in the deck spec:
# {"theme": {"name": "scholar", "accent": "0F6E78", ...}}
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


def resolve_theme(raw):
    base = dict(THEMES["scholar"])
    if isinstance(raw, str):
        base.update(THEMES.get(raw, {}))
    elif isinstance(raw, dict):
        base.update(THEMES.get(str(raw.get("name", "")), {}))
        for key, value in raw.items():
            if key != "name" and value:
                base[key] = str(value)
    return base


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
    data = Path(path).read_bytes()
    return png_size(data) or jpeg_size(data) or (800, 600)


def tx_body(paragraphs, anchor="t", font=None, ea_font=None):
    """paragraphs: list of dicts {text,size,bold,bullet,level,color,align}."""
    out = [f'<p:txBody><a:bodyPr wrap="square" anchor="{anchor}">'
           '<a:normAutofit/></a:bodyPr><a:lstStyle/>']
    for para in paragraphs:
        size = int(para.get("size", 18) * 100)
        bold = ' b="1"' if para.get("bold") else ""
        attrs, children = [], []
        if para.get("bullet", True):
            attrs.append('marL="285750" indent="-285750"')
            children.append('<a:buFont typeface="Arial"/><a:buChar char="•"/>')
        if para.get("level"):
            attrs.append(f'lvl="{int(para["level"])}"')
        if para.get("align") in ("l", "ctr", "r", "just"):
            attrs.append(f'algn="{para["align"]}"')
        ppr_xml = ""
        if attrs or children:
            ppr_xml = f'<a:pPr {" ".join(attrs)}>{"".join(children)}</a:pPr>'
        color = f'<a:solidFill><a:srgbClr val="{para["color"]}"/></a:solidFill>' if para.get("color") else ""
        face = ""
        if font:
            face = f'<a:latin typeface="{escape(str(font))}"/>'
            if ea_font:
                face += f'<a:ea typeface="{escape(str(ea_font))}"/>'
        out.append(
            f'<a:p>{ppr_xml}<a:r><a:rPr lang="zh-CN" sz="{size}"{bold}>{color}{face}</a:rPr>'
            f'<a:t>{escape(str(para.get("text", "")))}</a:t></a:r></a:p>')
    out.append("</p:txBody>")
    return "".join(out)


def box_shape(shape_id, name, x, y, cx, cy, fill=None, line=None,
              line_width=12700, prst="rect", paragraphs=None, anchor="t",
              font=None, ea_font=None, radius=None, shadow=False):
    """A filled/outlined shape, optionally carrying editable text."""
    if prst == "roundRect" and radius is not None:
        prst_xml = ('<a:prstGeom prst="roundRect"><a:avLst>'
                    f'<a:gd name="adj" fmla="val {int(radius)}"/>'
                    '</a:avLst></a:prstGeom>')
    else:
        prst_xml = f'<a:prstGeom prst="{prst}"><a:avLst/></a:prstGeom>'
    fill_xml = f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>' if fill else "<a:noFill/>"
    line_xml = (f'<a:ln w="{int(line_width)}"><a:solidFill><a:srgbClr val="{line}"/>'
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


def rule(shape_id, x, y, cx, color, thickness=12700):
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="Rule"/>'
            f'<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm>'
            f'<a:off x="{int(x)}" y="{int(y)}"/><a:ext cx="{int(cx)}" cy="{int(thickness)}"/>'
            f'</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:ln><a:noFill/></a:ln>'
            '</p:spPr></p:sp>')


def shape(shape_id, name, x, y, cx, cy, paragraphs, anchor="t"):
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{escape(name)}"/>'
            f'<p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
            f'{tx_body(paragraphs, anchor)}</p:sp>')


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


class Deck:
    def __init__(self, spec):
        self.spec = spec
        self.cx, self.cy = SIZES.get(spec.get("size", "16:9"), SIZES["16:9"])
        self.slides = spec.get("slides", [])
        self.theme = resolve_theme(spec.get("theme"))
        self.media = []          # (name, bytes)

    # ---------------- slides ----------------
    def slide_xml(self, index):
        s = self.slides[index]
        layout = str(s.get("layout") or s.get("kind") or "content").lower()
        t = self.theme
        cx, cy = self.cx, self.cy
        margin = int(cx * 0.06)
        top = int(cy * 0.065)
        footer_y = int(cy * 0.925)
        shapes = []
        sid = 2
        dark = layout in ("title", "closing", "statement", "quote")
        bg = s.get("background") or (t["ink"] if dark else t["bg"])
        ink = s.get("text_color") or (t["white"] if dark else t["ink"])
        muted = s.get("muted_color") or (t["line"] if dark else t["muted"])

        def text(x, y, w, h, paragraphs, anchor="t", name="Text"):
            nonlocal sid
            if not paragraphs or all(not str(p.get("text", "")).strip() for p in paragraphs):
                return
            shapes.append(box_shape(sid, name, x, y, w, h, paragraphs=paragraphs,
                                    anchor=anchor, font=t["body_font"],
                                    ea_font=t["body_ea"]))
            sid += 1

        def para(value, size, color=None, bold=False, bullet=False, level=0, align="l"):
            return {"text": str(value), "size": size, "bold": bold,
                    "bullet": bullet, "level": level, "color": color or ink,
                    "align": align}

        def bullets(items, size, color=None):
            if isinstance(items, str):
                items = [items]
            out = []
            for item in items or []:
                if isinstance(item, str):
                    out.append(para(item, size, color, bullet=True))
                else:
                    out.append(para(item.get("text", ""), item.get("size", size),
                                    item.get("color") or color,
                                    item.get("bold", False), True,
                                    item.get("level", 0), item.get("align", "l")))
            return out

        def add_image(*args):
            nonlocal sid
            img = s.get("image")
            if not img or not Path(img).exists():
                return False
            if len(args) == 4:
                x0, y0, bw, bh = args
            elif len(args) == 1 and isinstance(args[0], (tuple, list)) and len(args[0]) == 4:
                x0, y0, bw, bh = args[0]
            else:
                return False
            px_w, px_h = image_size(img)
            scale = min(bw / px_w, bh / px_h)
            w, h = int(px_w * scale), int(px_h * scale)
            x = x0 + (bw - w) // 2
            y = y0 + (bh - h) // 2
            rid = self.add_image(img)
            shapes.append(picture(sid, rid, x, y, w, h, Path(img).name))
            sid += 1
            return True

        def card(x, y, w, h, title, body, accent=None, fill=None, title_size=18, body_size=14):
            nonlocal sid
            shapes.append(box_shape(sid, "Card", x, y, w, h, fill=fill or t["panel"],
                                    line=t["line"], line_width=9525, prst="roundRect",
                                    radius=7000, shadow=True))
            sid += 1
            shapes.append(box_shape(sid, "Card accent", x + int(w * 0.06), y + int(h * 0.10),
                                    max(120000, int(w * 0.055)), max(120000, int(h * 0.055)),
                                    fill=accent or t["accent"], line=None, prst="ellipse"))
            sid += 1
            text(x + int(w * 0.10), y + int(h * 0.10), int(w * 0.82), int(h * 0.22),
                 [para(title, title_size, t["ink"], True, False)], name="Card title")
            text(x + int(w * 0.10), y + int(h * 0.36), int(w * 0.82), int(h * 0.55),
                 bullets(body, body_size, t["muted"]), name="Card body")

        def footer():
            nonlocal sid
            if s.get("hide_footer"):
                return
            if s.get("source"):
                text(margin, footer_y, int(cx * 0.60), int(cy * 0.045),
                     [para(s["source"], 9.5, muted, False, False)], name="Source")
            text(cx - margin - int(cx * 0.10), footer_y, int(cx * 0.10), int(cy * 0.045),
                 [para(str(index + 1), 10, muted, False, False, 0, "r")], name="Page number")

        if layout in ("title", "cover"):
            eyebrow = s.get("eyebrow", s.get("kicker", ""))
            if eyebrow:
                text(margin, int(cy * 0.18), cx - 2 * margin, int(cy * 0.10),
                     [para(eyebrow, 13, t["accent2"], True, False)])
            text(margin, int(cy * 0.27), int(cx * 0.78), int(cy * 0.22),
                 [para(s.get("title", ""), s.get("title_size", 38), ink, True, False)],
                 anchor="t", name="Title")
            if s.get("subtitle"):
                text(margin, int(cy * 0.52), int(cx * 0.68), int(cy * 0.14),
                     [para(s["subtitle"], s.get("subtitle_size", 19), muted, False, False)],
                     name="Subtitle")
            footer()
        elif layout in ("section", "divider"):
            number = str(s.get("number", ""))
            if number:
                text(margin, int(cy * 0.22), int(cx * 0.12), int(cy * 0.18),
                     [para(number, 44, t["accent"], True, False)])
            text(margin, int(cy * 0.40), int(cx * 0.78), int(cy * 0.16),
                 [para(s.get("title", ""), s.get("title_size", 32), ink, True, False)],
                 name="Section title")
            if s.get("subtitle"):
                text(margin, int(cy * 0.58), int(cx * 0.68), int(cy * 0.10),
                     [para(s["subtitle"], 17, muted, False, False)], name="Section subtitle")
            footer()
        elif layout in ("closing", "end"):
            text(margin, int(cy * 0.16), cx - 2 * margin, int(cy * 0.12),
                 [para(s.get("eyebrow", "TAKE-HOME"), 12, t["accent2"], True, False)])
            text(margin, int(cy * 0.27), int(cx * 0.80), int(cy * 0.16),
                 [para(s.get("title", "结论"), s.get("title_size", 32), ink, True, False)],
                 name="Closing title")
            body = s.get("bullets") or s.get("takeaways") or []
            if body:
                text(margin, int(cy * 0.47), int(cx * 0.76), int(cy * 0.30),
                     bullets(body, s.get("body_size", 17), muted), name="Takeaways")
            if s.get("subtitle"):
                text(margin, int(cy * 0.82), int(cx * 0.76), int(cy * 0.06),
                     [para(s["subtitle"], 12, muted, False, False)], name="Closing note")
            footer()
        elif layout in ("quote", "statement"):
            quote = s.get("quote") or s.get("title", "")
            text(margin, int(cy * 0.18), cx - 2 * margin, int(cy * 0.44),
                 [para("“" + str(quote) + "”", s.get("title_size", 28), ink, False, False, 0, "ctr")],
                 anchor="ctr", name="Quote")
            if s.get("attribution"):
                text(margin, int(cy * 0.68), cx - 2 * margin, int(cy * 0.08),
                     [para("— " + str(s["attribution"]), 14, muted, False, False, 0, "r")],
                     name="Attribution")
            footer()
        else:
            title_size = s.get("title_size", 28)
            if s.get("eyebrow"):
                text(margin, int(cy * 0.045), int(cx * 0.80), int(cy * 0.045),
                     [para(s["eyebrow"], 10.5, t["accent"], True, False)], name="Eyebrow")
            text(margin, int(cy * 0.085), cx - 2 * margin, int(cy * 0.105),
                 [para(s.get("title", ""), title_size, ink, True, False)], name="Title")

            if layout in ("timeline", "steps", "process"):
                steps = s.get("steps") or s.get("items") or []
                n = max(1, len(steps))
                gap = int(cx * 0.02)
                width = (cx - 2 * margin - gap * (n - 1)) // n
                y = int(cy * 0.28)
                for i, step in enumerate(steps):
                    x = margin + i * (width + gap)
                    shapes.append(box_shape(sid, "Step card", x, y, width, int(cy * 0.48),
                                            fill=t["panel"], line=t["line"], line_width=9525,
                                            prst="roundRect", radius=7000, shadow=True))
                    sid += 1
                    shapes.append(box_shape(sid, "Step number", x + int(width * 0.08),
                                            y + int(cy * 0.07), int(cx * 0.045),
                                            int(cx * 0.045), fill=t["accent"], line=None,
                                            prst="ellipse",
                                            paragraphs=[para(str(i + 1), 15, t["white"], True, False, 0, "ctr")],
                                            anchor="ctr"))
                    sid += 1
                    title = step.get("title", step.get("label", "")) if isinstance(step, dict) else str(step)
                    body = step.get("text", step.get("body", "")) if isinstance(step, dict) else ""
                    text(x + int(width * 0.08), y + int(cy * 0.18),
                         int(width * 0.84), int(cy * 0.10),
                         [para(title, 15, t["ink"], True, False)], name="Step title")
                    text(x + int(width * 0.08), y + int(cy * 0.31),
                         int(width * 0.84), int(cy * 0.14),
                         bullets(body, 12, t["muted"]) if body else [], name="Step body")
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
                    shapes.append(box_shape(sid, "Stat card", x, y, width, int(cy * 0.40),
                                            fill=t["panel"], line=t["line"], line_width=9525,
                                            prst="roundRect", radius=7000, shadow=True))
                    sid += 1
                    text(x + int(width * 0.08), y + int(cy * 0.07),
                         int(width * 0.84), int(cy * 0.16),
                         [para(value, 34, t["accent"], True, False, 0, "ctr")],
                         anchor="ctr", name="Stat value")
                    text(x + int(width * 0.08), y + int(cy * 0.25),
                         int(width * 0.84), int(cy * 0.09),
                         [para(label, 12, t["muted"], False, False, 0, "ctr")],
                         anchor="t", name="Stat label")
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
                    card(x, y, width, int(cy * 0.48),
                         item.get("title", ""), item.get("bullets", item.get("text", [])),
                         accent=t["accent"] if i % 2 == 0 else t["accent2"])
            else:
                image_on_right = layout not in ("image-left",)
                has_image = bool(s.get("image") and Path(s["image"]).exists())
                callout = s.get("callout") or {}
                body_w = int((cx - 2 * margin) * (0.54 if (has_image or callout) else 0.92))
                body_x = margin
                body_y = int(cy * 0.27)
                body_h = int(cy * 0.57)
                if layout == "image-left" and has_image:
                    add_image((margin, int(cy * 0.26), int(cx * 0.40), int(cy * 0.58)))
                    body_x = margin + int(cx * 0.44)
                    body_w = cx - margin - body_x
                elif has_image:
                    add_image((margin + int(cx * 0.56), int(cy * 0.25),
                               int(cx * 0.38), int(cy * 0.58)))
                elif callout:
                    box_w = int(cx * 0.30)
                    box_x = cx - margin - box_w
                    shapes.append(box_shape(sid, "Callout", box_x, int(cy * 0.28), box_w,
                                            int(cy * 0.46), fill=t["panel"], line=t["line"],
                                            line_width=9525, prst="roundRect", radius=7000,
                                            shadow=True))
                    sid += 1
                    text(box_x + int(box_w * 0.08), int(cy * 0.33), int(box_w * 0.84),
                         int(cy * 0.08),
                         [para(callout.get("title", "关键点"), 15, t["accent"], True, False)],
                         name="Callout title")
                    text(box_x + int(box_w * 0.08), int(cy * 0.43), int(box_w * 0.84),
                         int(cy * 0.24),
                         bullets(callout.get("bullets", callout.get("text", [])), 12, t["muted"]),
                         name="Callout body")
                body = s.get("bullets") or []
                text(body_x, body_y, body_w, body_h,
                     bullets(body, s.get("body_size", 17), t["muted"]), name="Content")
                if s.get("caption"):
                    text(margin, int(cy * 0.855), cx - 2 * margin, int(cy * 0.04),
                         [para(s["caption"], 10.5, t["muted"], False, False)], name="Caption")
            footer()

        bg_xml = (f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="{bg}"/></a:solidFill>'
                  '<a:effectLst/></p:bgPr></p:bg>')
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<p:sld {NS}>{bg_xml}<p:cSld><p:spTree>{group_props()}'
                + "".join(shapes)
                + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')

    def add_image(self, path):
        raw = Path(path).read_bytes()
        ext = Path(path).suffix.lower().lstrip(".") or "png"
        if ext == "jpg":
            ext = "jpeg"
        name = f"image{len(self.media) + 1}.{ext}"
        self.media.append((name, raw))
        # one picture per slide, so the relationship id is fixed
        return "rIdImg1"

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
                '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/>'
                f'{tx_body(paragraphs)}</p:sp>'
                '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>')

    def slide_rels(self, index):
        rels = [("rId1", P_NS + "/slideLayout", "../slideLayouts/slideLayout1.xml")]
        s = self.slides[index]
        if s.get("image") and Path(s["image"]).exists():
            # images are appended to self.media in slide order, one per slide
            global_ordinal = sum(1 for i in range(index)
                                 if self.slides[i].get("image")
                                 and Path(self.slides[i]["image"]).exists()) + 1
            ext = Path(s["image"]).suffix.lower().lstrip(".")
            if ext == "jpg":
                ext = "jpeg"
            rels.append(("rIdImg1", R_NS + "/image",
                         f"../media/image{global_ordinal}.{ext}"))
        rels.append(("rIdNotes", P_NS + "/notesSlide",
                     f"../notesSlides/notesSlide{index + 1}.xml"))
        return self._rels(rels)

    # ---------------- package parts ----------------
    def presentation_xml(self):
        slides = "".join(
            f'<p:sldId id="{256 + i}" r:id="rIdS{i + 1}"/>' for i in range(len(self.slides)))
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<p:presentation {NS} saveSubsetFonts="1">'
                '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rIdMaster"/></p:sldMasterIdLst>'
                '<p:notesMasterIdLst><p:notesMasterId r:id="rIdNotesMaster"/></p:notesMasterIdLst>'
                f'<p:sldIdLst>{slides}</p:sldIdLst>'
                f'<p:sldSz cx="{self.cx}" cy="{self.cy}"/>'
                '<p:notesSz cx="6858000" cy="9144000"/>'
                '<p:defaultTextStyle><a:defPPr><a:defRPr lang="zh-CN"/></a:defPPr></p:defaultTextStyle>'
                '</p:presentation>')

    def presentation_rels(self):
        rels = [
            ("rIdMaster", P_NS + "/slideMaster", "slideMasters/slideMaster1.xml"),
            ("rIdNotesMaster", P_NS + "/notesMaster", "notesMasters/notesMaster1.xml"),
            ("rIdTheme", A_NS + "/theme", "theme/theme1.xml"),
        ]
        for i in range(len(self.slides)):
            rels.append((f"rIdS{i + 1}", P_NS + "/slide", f"slides/slide{i + 1}.xml"))
        return self._rels(rels)

    @staticmethod
    def _rels(entries):
        body = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{g}"/>'
                       for i, t, g in entries)
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<Relationships xmlns="{PKG_REL}">{body}</Relationships>')

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
                f'<a:theme xmlns:a="{A_NS}" name="WeeklySkill">'
                '<a:themeElements>'
                '<a:clrScheme name="Weekly">'
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
                '<a:fontScheme name="Weekly">'
                f'<a:majorFont><a:latin typeface="{escape(t["title_font"])}"/>'
                f'<a:ea typeface="{escape(t["title_ea"])}"/><a:cs typeface=""/></a:majorFont>'
                f'<a:minorFont><a:latin typeface="{escape(t["body_font"])}"/>'
                f'<a:ea typeface="{escape(t["body_ea"])}"/><a:cs typeface=""/></a:minorFont>'
                '</a:fontScheme>'
                '<a:fmtScheme name="Office">'
                '<a:fillStyleLst>'
                '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
                '<a:solidFill><a:schemeClr val="phClr"><a:tint val="50000"/></a:schemeClr></a:solidFill>'
                '<a:solidFill><a:schemeClr val="phClr"><a:shade val="50000"/></a:schemeClr></a:solidFill>'
                '</a:fillStyleLst>'
                '<a:lnStyleLst>'
                '<a:ln w="6350" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>'
                '<a:ln w="12700" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>'
                '<a:ln w="19050" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>'
                '</a:lnStyleLst>'
                '<a:effectStyleLst>'
                '<a:effectStyle><a:effectLst/></a:effectStyle>'
                '<a:effectStyle><a:effectLst/></a:effectStyle>'
                '<a:effectStyle><a:effectLst/></a:effectStyle>'
                '</a:effectStyleLst>'
                '<a:bgFillStyleLst>'
                '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
                '<a:solidFill><a:schemeClr val="phClr"><a:tint val="95000"/></a:schemeClr></a:solidFill>'
                '<a:solidFill><a:schemeClr val="phClr"><a:shade val="95000"/></a:schemeClr></a:solidFill>'
                '</a:bgFillStyleLst>'
                '</a:fmtScheme></a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>')

    def core_xml(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                f'<dc:title>{escape(self.spec.get("title", ""))}</dc:title>'
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
                '</cp:coreProperties>')

    def app_xml(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                '<Application>weekly-skill</Application>'
                f'<Slides>{len(self.slides)}</Slides></Properties>')

    # ---------------- write ----------------
    def save(self, out_path):
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", self.content_types())
            zf.writestr("_rels/.rels", self._rels([
                ("rId1", R_NS + "/officeDocument", "ppt/presentation.xml"),
                ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties", "docProps/core.xml"),
                ("rId3", R_NS + "/extended-properties", "docProps/app.xml"),
            ]))
            zf.writestr("docProps/core.xml", self.core_xml())
            zf.writestr("docProps/app.xml", self.app_xml())
            zf.writestr("ppt/presentation.xml", self.presentation_xml())
            zf.writestr("ppt/_rels/presentation.xml.rels", self.presentation_rels())
            zf.writestr("ppt/theme/theme1.xml", self.theme_xml())
            zf.writestr("ppt/slideMasters/slideMaster1.xml", self.slide_master_xml())
            zf.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", self._rels([
                ("rIdLayout", P_NS + "/slideLayout", "../slideLayouts/slideLayout1.xml"),
                ("rIdTheme", A_NS + "/theme", "../theme/theme1.xml"),
            ]))
            zf.writestr("ppt/slideLayouts/slideLayout1.xml", self.slide_layout_xml())
            zf.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", self._rels([
                ("rIdMaster", P_NS + "/slideMaster", "../slideMasters/slideMaster1.xml"),
            ]))
            zf.writestr("ppt/notesMasters/notesMaster1.xml", self.notes_master_xml())
            zf.writestr("ppt/notesMasters/_rels/notesMaster1.xml.rels", self._rels([
                ("rIdTheme", A_NS + "/theme", "../theme/theme1.xml"),
            ]))
            for i in range(len(self.slides)):
                zf.writestr(f"ppt/slides/slide{i + 1}.xml", self.slide_xml(i))
                zf.writestr(f"ppt/slides/_rels/slide{i + 1}.xml.rels", self.slide_rels(i))
                zf.writestr(f"ppt/notesSlides/notesSlide{i + 1}.xml", self.notes_xml(i))
                zf.writestr(f"ppt/notesSlides/_rels/notesSlide{i + 1}.xml.rels", self._rels([
                    ("rIdSlide", P_NS + "/slide", f"../slides/slide{i + 1}.xml"),
                    ("rIdNotesMaster", P_NS + "/notesMaster", "../notesMasters/notesMaster1.xml"),
                ]))
            for name, raw in self.media:
                zf.writestr(f"ppt/media/{name}", raw)
        return out


def build(spec, out_path):
    return Deck(spec).save(out_path)


def check(path):
    import re
    import xml.etree.ElementTree as ET
    problems = []
    try:
        with zipfile.ZipFile(path) as zf:
            if zf.testzip():
                problems.append("corrupt member")
            names = zf.namelist()
            if "ppt/presentation.xml" not in names:
                problems.append("missing presentation.xml")
            slides = sorted(n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
            problems.append(f"slides={len(slides)}")
            for name in ["ppt/presentation.xml"] + slides:
                try:
                    ET.fromstring(zf.read(name))
                except ET.ParseError as exc:
                    problems.append(f"{name} not parseable: {exc}")
            # every slide must reference its layout
            for i in range(1, len(slides) + 1):
                rel = f"ppt/slides/_rels/slide{i}.xml.rels"
                if rel in names:
                    try:
                        ET.fromstring(zf.read(rel))
                    except ET.ParseError as exc:
                        problems.append(f"{rel} not parseable: {exc}")
    except zipfile.BadZipFile as exc:
        problems.append(f"not a zip: {exc}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec")
    ap.add_argument("--out")
    ap.add_argument("--check")
    args = ap.parse_args()
    if args.check:
        problems = check(args.check)
        for item in problems:
            print(item)
        fatal = [p for p in problems if not p.startswith("slides=")]
        return 1 if fatal else 0
    if not (args.spec and args.out):
        ap.error("--spec and --out are required unless --check is used")
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    out = build(spec, args.out)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    for item in check(out):
        print(item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
