#!/usr/bin/env python3
"""Build a real, editable .docx from a JSON spec using only the standard library.

The evaluation image does not install third-party packages, so python-docx is
not available. This module writes the OOXML package directly (WordprocessingML),
which Word / LibreOffice / WPS can open and edit.

Spec format (all text is plain UTF-8; inline **bold** is NOT interpreted):

{
  "title": "optional document title",
  "blocks": [
    {"type": "heading", "level": 1, "text": "1 Introduction"},
    {"type": "paragraph", "text": "..."},
    {"type": "bullets", "items": ["...", "..."]},
    {"type": "numbers", "items": ["...", "..."]},
    {"type": "quote", "text": "..."},
    {"type": "table", "header": ["A", "B"], "rows": [["1", "2"]], "caption": "Table 1"},
    {"type": "figure", "path": "fig.png", "width_cm": 15.0, "caption": "Fig. 1"},
    {"type": "pagebreak"}
  ]
}

Usage:
    python3 make_docx.py --spec doc.json --out /app/output/manuscript.docx
    python3 make_docx.py --check /app/output/manuscript.docx
"""
import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
EMU_PER_CM = 360000
EMU_PER_INCH = 914400


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
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
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        seg = int.from_bytes(data[i + 2:i + 4], "big")
        i += 2 + seg
    return None


def image_size(path):
    data = Path(path).read_bytes()
    return png_size(data) or jpeg_size(data) or (800, 600)


def run(text, bold=False, italic=False, size=None, style=None):
    props = []
    if bold:
        props.append("<w:b/>")
    if italic:
        props.append("<w:i/>")
    if size:
        props.append(f'<w:sz w:val="{int(size * 2)}"/>')
    if style:
        props.append(f'<w:rStyle w:val="{style}"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    space = ' xml:space="preserve"' if text != text.strip() else ""
    return f'<w:r>{rpr}<w:t{space}>{escape(text)}</w:t></w:r>'


def para(content, style=None, align=None, spacing_after=None):
    ppr = []
    if style:
        ppr.append(f'<w:pStyle w:val="{style}"/>')
    if align:
        ppr.append(f'<w:jc w:val="{align}"/>')
    if spacing_after is not None:
        ppr.append(f'<w:spacing w:after="{spacing_after}" w:line="360" w:lineRule="auto"/>')
    ppr_xml = f"<w:pPr>{''.join(ppr)}</w:pPr>" if ppr else ""
    return f"<w:p>{ppr_xml}{content}</w:p>"


# --------------------------------------------------------------------------
# block writers
# --------------------------------------------------------------------------
class Document:
    def __init__(self):
        self.body = []
        self.images = []          # list of (filename, bytes, ext)
        self.rels = []            # (rId, type, target)
        self.bookmark = 1
        self.fig_no = 0

    def add_image_part(self, path):
        raw = Path(path).read_bytes()
        ext = Path(path).suffix.lower().lstrip(".") or "png"
        if ext == "jpg":
            ext = "jpeg"
        name = f"image{len(self.images) + 1}.{ext}"
        self.images.append((name, raw, ext))
        rid = f"rIdImg{len(self.images)}"
        self.rels.append((rid, R_NS + "/image", f"media/{name}"))
        return rid

    def heading(self, level, text):
        style = {1: "Heading1", 2: "Heading2", 3: "Heading3"}.get(int(level), "Heading3")
        self.body.append(para(run(text), style=style))

    def paragraph(self, text, style=None, align=None):
        self.body.append(para(run(text), style=style, align=align))

    def list_items(self, items, numbered=False):
        for item in items:
            self.body.append(para(run(("• " if not numbered else "") + str(item)),
                                  style="ListParagraph"))

    def quote(self, text):
        self.body.append(para(run(text, italic=True), style="Quote"))

    def table(self, header, rows, caption=None):
        cols = max([len(header)] + [len(r) for r in rows]) if header or rows else 1
        width = 9000 // max(cols, 1)
        grid = "".join(f'<w:gridCol w:w="{width}"/>' for _ in range(cols))
        out = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
               '<w:tblW w:w="0" w:type="auto"/></w:tblPr>',
               f"<w:tblGrid>{grid}</w:tblGrid>"]
        if header:
            cells = []
            for text in header:
                cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/></w:tcPr>'
                             f'{para(run(str(text), bold=True))}</w:tc>')
            out.append("<w:tr>" + "".join(cells) + "</w:tr>")
        for row in rows:
            cells = []
            for text in row:
                cells.append(f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/></w:tcPr>'
                             f'{para(run(str(text)))}</w:tc>')
            out.append("<w:tr>" + "".join(cells) + "</w:tr>")
        out.append("</w:tbl>")
        self.body.append("".join(out))
        if caption:
            self.body.append(para(run(str(caption)), style="Caption"))

    def figure(self, path, width_cm=15.0, caption=None):
        if not Path(path).exists():
            raise FileNotFoundError(path)
        self.fig_no += 1
        px_w, px_h = image_size(path)
        cx = int(float(width_cm) * EMU_PER_CM)
        cy = int(cx * px_h / max(px_w, 1))
        rid = self.add_image_part(path)
        drawing = (
            '<w:r><w:drawing>'
            f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/>'
            '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="{self.fig_no}" name="Figure {self.fig_no}"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic>'
            f'<a:graphicData uri="{PIC_NS}">'
            '<pic:pic>'
            f'<pic:nvPicPr><pic:cNvPr id="{self.fig_no}" name="{escape(Path(path).name)}"/>'
            '<pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
        )
        self.body.append(para(drawing, align="center"))
        if caption:
            self.body.append(para(run(str(caption)), style="Caption"))

    def pagebreak(self):
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    # -- package ---------------------------------------------------------
    def document_xml(self):
        namespaces = (f'xmlns:w="{W_NS}" xmlns:r="{R_NS}" xmlns:wp="{WP_NS}" '
                      f'xmlns:a="{A_NS}" xmlns:pic="{PIC_NS}"')
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document {namespaces}><w:body>'
                + "".join(self.body)
                + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                  '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
                  'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>'
                f'</w:body></w:document>')

    def styles_xml(self):
        def heading_style(idx, size, bold=True):
            return (f'<w:style w:type="paragraph" w:styleId="Heading{idx}">'
                    f'<w:name w:val="heading {idx}"/><w:basedOn w:val="Normal"/>'
                    f'<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/>'
                    f'<w:outlineLvl w:val="{idx - 1}"/></w:pPr>'
                    f'<w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/></w:rPr></w:style>')

        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:styles xmlns:w="{W_NS}">'
                '<w:docDefaults><w:rPrDefault><w:rPr>'
                '<w:rFonts w:ascii="Liberation Serif" w:hAnsi="Liberation Serif" w:eastAsia="SimSun"/>'
                '<w:sz w:val="22"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>'
                '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="360" w:lineRule="auto"/>'
                '</w:pPr></w:pPrDefault></w:docDefaults>'
                '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
                '<w:name w:val="Normal"/></w:style>'
                + heading_style(1, 32) + heading_style(2, 26) + heading_style(3, 24)
                + '<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/>'
                  '<w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr>'
                  '<w:rPr><w:i/><w:sz w:val="18"/></w:rPr></w:style>'
                  '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/>'
                  '<w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="567" w:right="567"/></w:pPr>'
                  '</w:style>'
                  '<w:style w:type="paragraph" w:styleId="ListParagraph">'
                  '<w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>'
                  '<w:pPr><w:ind w:left="720"/></w:pPr></w:style>'
                  '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>'
                  '<w:tblPr><w:tblBorders>'
                  '<w:top w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                  '<w:left w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                  '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                  '<w:right w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                  '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                  '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
                  '</w:tblBorders></w:tblPr></w:style>'
                '</w:styles>')

    def content_types(self):
        overrides = [
            ('/word/document.xml',
             'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'),
            ('/word/styles.xml',
             'application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml'),
            ('/docProps/core.xml', 'application/vnd.openxmlformats-package.core-properties+xml'),
            ('/docProps/app.xml',
             'application/vnd.openxmlformats-officedocument.extended-properties+xml'),
        ]
        for name, _, ext in self.images:
            overrides.append((f"/word/media/{name}", f"image/{'jpeg' if ext == 'jpeg' else ext}"))
        body = "".join(f'<Override PartName="{p}" ContentType="{c}"/>' for p, c in overrides)
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Default Extension="png" ContentType="image/png"/>'
                '<Default Extension="jpeg" ContentType="image/jpeg"/>'
                '<Default Extension="jpg" ContentType="image/jpeg"/>'
                f'{body}</Types>')

    def root_rels(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'<Relationship Id="rId1" Type="{R_NS}/officeDocument" Target="word/document.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                f'<Relationship Id="rId3" Type="{R_NS}/extended-properties" Target="docProps/app.xml"/>'
                '</Relationships>')

    def doc_rels(self):
        rels = [("rIdStyles", R_NS + "/styles", "styles.xml")]
        rels += self.rels
        body = "".join(
            f'<Relationship Id="{rid}" Type="{typ}" Target="{target}"/>'
            for rid, typ, target in rels)
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'{body}</Relationships>')

    def core_xml(self, title):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                f'<dc:title>{escape(title or "")}</dc:title>'
                f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
                f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
                '</cp:coreProperties>')

    def app_xml(self):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                '<Application>weekly-skill</Application></Properties>')

    def save(self, out_path):
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", self.content_types())
            zf.writestr("_rels/.rels", self.root_rels())
            zf.writestr("word/document.xml", self.document_xml())
            zf.writestr("word/styles.xml", self.styles_xml())
            zf.writestr("word/_rels/document.xml.rels", self.doc_rels())
            zf.writestr("docProps/core.xml", self.core_xml(getattr(self, "title", "")))
            zf.writestr("docProps/app.xml", self.app_xml())
            for name, raw, _ in self.images:
                zf.writestr(f"word/media/{name}", raw)
        return out


def build(spec, out_path):
    doc = Document()
    doc.title = spec.get("title", "")
    if spec.get("title"):
        doc.body.append(para(run(spec["title"], bold=True, size=18), align="center"))
    for block in spec.get("blocks", []):
        kind = block.get("type", "paragraph")
        if kind == "heading":
            doc.heading(block.get("level", 1), block.get("text", ""))
        elif kind == "paragraph":
            doc.paragraph(block.get("text", ""), align=block.get("align"))
        elif kind == "bullets":
            doc.list_items(block.get("items", []))
        elif kind == "numbers":
            doc.list_items(block.get("items", []), numbered=True)
        elif kind == "quote":
            doc.quote(block.get("text", ""))
        elif kind == "table":
            doc.table(block.get("header") or [], block.get("rows") or [],
                      block.get("caption"))
        elif kind == "figure":
            doc.figure(block["path"], block.get("width_cm", 15.0), block.get("caption"))
        elif kind == "pagebreak":
            doc.pagebreak()
        else:
            raise ValueError(f"unknown block type: {kind}")
    return doc.save(out_path)


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------
def check(path):
    import xml.etree.ElementTree as ET
    problems = []
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                problems.append(f"corrupt member: {bad}")
            names = set(zf.namelist())
            for required in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
                if required not in names:
                    problems.append(f"missing part: {required}")
            if "word/document.xml" in names:
                try:
                    root = ET.fromstring(zf.read("word/document.xml"))
                    paras = root.findall(f".//{{{W_NS}}}p")
                    problems.append(f"paragraphs={len(paras)}")
                except ET.ParseError as exc:
                    problems.append(f"document.xml not parseable: {exc}")
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
        fatal = [p for p in problems if not p.startswith("paragraphs=")]
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
