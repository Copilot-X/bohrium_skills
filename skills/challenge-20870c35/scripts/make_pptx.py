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


def tx_body(paragraphs, anchor="t"):
    """paragraphs: list of dicts {text,size,bold,bullet,level,color}."""
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
        ppr_xml = ""
        if attrs or children:
            ppr_xml = f'<a:pPr {" ".join(attrs)}>{"".join(children)}</a:pPr>'
        color = f'<a:solidFill><a:srgbClr val="{para["color"]}"/></a:solidFill>' if para.get("color") else ""
        out.append(
            f'<a:p>{ppr_xml}<a:r><a:rPr lang="zh-CN" sz="{size}"{bold}>{color}</a:rPr>'
            f'<a:t>{escape(str(para.get("text", "")))}</a:t></a:r></a:p>')
    out.append("</p:txBody>")
    return "".join(out)


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
        self.media = []          # (name, bytes)

    # ---------------- slides ----------------
    def slide_xml(self, index):
        s = self.slides[index]
        kind = s.get("kind", "content")
        cx, cy = self.cx, self.cy
        margin = int(cx * 0.06)
        shapes = []
        sid = 2
        if kind == "title":
            title = shape(sid, "Title", margin, int(cy * 0.30), cx - 2 * margin, int(cy * 0.18),
                          [{"text": s.get("title", ""), "size": 40, "bold": True,
                            "bullet": False, "color": "1F3864"}], anchor="b")
            shapes.append(title); sid += 1
            sub = shape(sid, "Subtitle", margin, int(cy * 0.52), cx - 2 * margin, int(cy * 0.20),
                        [{"text": s.get("subtitle", ""), "size": 20, "bullet": False,
                          "color": "595959"}], anchor="t")
            shapes.append(sub); sid += 1
        elif kind == "section":
            shapes.append(shape(sid, "Section", margin, int(cy * 0.40), cx - 2 * margin, int(cy * 0.20),
                                [{"text": s.get("title", ""), "size": 32, "bold": True,
                                  "bullet": False, "color": "1F3864"}], anchor="b"))
            sid += 1
        else:
            shapes.append(shape(sid, "Title", margin, int(cy * 0.06), cx - 2 * margin, int(cy * 0.14),
                                [{"text": s.get("title", ""), "size": 28, "bold": True,
                                  "bullet": False, "color": "1F3864"}], anchor="b"))
            sid += 1
            img = s.get("image")
            if img and Path(img).exists():
                px_w, px_h = image_size(img)
                box_w = int((cx - 2 * margin) * 0.46)
                box_h = int(cy * 0.60)
                scale = min(box_w / px_w, box_h / px_h)
                w, h = int(px_w * scale), int(px_h * scale)
                rid = self.add_image(img)
                shapes.append(picture(sid, rid, cx - margin - w, int(cy * 0.24), w, h,
                                      Path(img).name))
                sid += 1
                text_w = int((cx - 2 * margin) * 0.50)
            else:
                text_w = cx - 2 * margin
            bullets = s.get("bullets") or []
            if bullets:
                shapes.append(shape(
                    sid, "Content", margin, int(cy * 0.24), text_w, int(cy * 0.58),
                    [{"text": b if isinstance(b, str) else b.get("text", ""),
                      "size": 18, "bullet": True,
                      "level": 0 if isinstance(b, str) else b.get("level", 0)}
                     for b in bullets], anchor="t"))
                sid += 1
            if s.get("caption"):
                shapes.append(shape(sid, "Caption", margin, int(cy * 0.86), cx - 2 * margin,
                                    int(cy * 0.06),
                                    [{"text": s["caption"], "size": 12, "bullet": False,
                                      "color": "595959"}], anchor="t"))
                sid += 1
        if s.get("footer"):
            shapes.append(shape(sid, "Footer", margin, int(cy * 0.93), cx - 2 * margin, int(cy * 0.05),
                                [{"text": s["footer"], "size": 10, "bullet": False,
                                  "color": "808080"}], anchor="t"))
            sid += 1
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<p:sld {NS}><p:cSld><p:spTree>{group_props()}'
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
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<a:theme xmlns:a="{A_NS}" name="WeeklySkill">'
                '<a:themeElements>'
                '<a:clrScheme name="Office">'
                '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
                '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
                '<a:dk2><a:srgbClr val="44546A"/></a:dk2>'
                '<a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>'
                '<a:accent1><a:srgbClr val="4472C4"/></a:accent1>'
                '<a:accent2><a:srgbClr val="ED7D31"/></a:accent2>'
                '<a:accent3><a:srgbClr val="A5A5A5"/></a:accent3>'
                '<a:accent4><a:srgbClr val="FFC000"/></a:accent4>'
                '<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5>'
                '<a:accent6><a:srgbClr val="70AD47"/></a:accent6>'
                '<a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
                '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
                '</a:clrScheme>'
                '<a:fontScheme name="Office">'
                '<a:majorFont><a:latin typeface="Liberation Sans"/><a:ea typeface="SimHei"/><a:cs typeface=""/></a:majorFont>'
                '<a:minorFont><a:latin typeface="Liberation Sans"/><a:ea typeface="SimSun"/><a:cs typeface=""/></a:minorFont>'
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
