#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export the markdown deliverable as an editable DOCX (stdlib OOXML).

Usage:
    python3 docx_out.py --draft /app/output/article.md \
        --out /app/output/article.docx [--preview] [--json]

No python-docx, no network.  The generated file contains a real
``word/document.xml`` with headings, paragraphs, bullets, tables and inline
bold/italic, so Word/LibreOffice can open and edit it.  ``--preview`` tries a
local LibreOffice conversion to PDF and records the result honestly.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # package mode (python -m scripts.docx_out)
    from . import transfer_kit as kit
except ImportError:  # direct mode (python scripts/docx_out.py)
    kit = __import__("transfer_kit")

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="SimSun" w:cs="Calibri"/>
<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/>
</w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/><w:color w:val="1F3864"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:rPr><w:color w:val="595959"/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="280" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="32"/><w:color w:val="2E74B5"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="28"/><w:color w:val="2E74B5"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="24"/><w:color w:val="1F3864"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading4"><w:name w:val="heading 4"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:outlineLvl w:val="3"/></w:pPr><w:rPr><w:b/><w:i/><w:sz w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:ind w:left="420"/><w:spacing w:after="60"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:ind w:left="420" w:right="420"/></w:pPr><w:rPr><w:i/><w:color w:val="595959"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/></w:pPr>
<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="18"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:qFormat/>
<w:tblPr><w:tblBorders>
<w:top w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>
<w:left w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>
<w:bottom w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>
<w:right w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>
<w:insideH w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>
<w:insideV w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>
</w:tblBorders></w:tblPr></w:style>
</w:styles>"""

DOC_HEAD = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<w:body>"""

DOC_TAIL = """<w:sectPr>
<w:pgSz w:w="11906" w:h="16838"/>
<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>
</w:sectPr></w:body></w:document>"""

INLINE_RE = re.compile(
    r"(\*\*.+?\*\*|__.+?__|\*[^*\n]+?\*|_[^_\n]+?_|`[^`]+`|\[[^\]]+\]\([^)]+\))")


def xml_text(text):
    return escape(str(text), {'"': "&quot;"})


def inline_runs(text):
    runs = []
    pos = 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            runs.append({"text": text[pos:m.start()]})
        token = m.group(0)
        if token.startswith("**") or token.startswith("__"):
            runs.append({"text": token[2:-2], "bold": True})
        elif token.startswith("`"):
            runs.append({"text": token[1:-1], "code": True})
        elif token.startswith("["):
            label = re.match(r"\[([^\]]+)\]", token)
            runs.append({"text": label.group(1) if label else token})
        else:
            runs.append({"text": token[1:-1], "italic": True})
        pos = m.end()
    if pos < len(text):
        runs.append({"text": text[pos:]})
    return [r for r in runs if r.get("text")]


def run_xml(run):
    props = []
    if run.get("bold"):
        props.append("<w:b/>")
    if run.get("italic"):
        props.append("<w:i/>")
    if run.get("code"):
        props.append('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>')
    rpr = "<w:rPr>%s</w:rPr>" % "".join(props) if props else ""
    return ('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>'
            % (rpr, xml_text(run["text"])))


def paragraph_xml(text, style="Normal", bullet=False, prefix=""):
    if bullet:
        prefix = "• "
    runs = inline_runs(text)
    body = "".join(run_xml(r) for r in runs)
    if prefix:
        body = ('<w:r><w:t xml:space="preserve">%s</w:t></w:r>' % xml_text(prefix)) + body
    ppr = ('<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style) if style else ""
    return "<w:p>%s%s</w:p>" % (ppr, body)


def table_xml(rows):
    if not rows:
        return ""
    cols = max(len(r) for r in rows)
    width = 9000 // max(1, cols)
    out = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
           '<w:tblW w:w="0" w:type="auto"/></w:tblPr><w:tblGrid>']
    out.extend('<w:gridCol w:w="%d"/>' % width for _ in range(cols))
    out.append("</w:tblGrid>")
    for r_idx, row in enumerate(rows):
        cells = list(row) + [""] * (cols - len(row))
        out.append("<w:tr>")
        for cell in cells:
            out.append('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr>' % width)
            style = "Normal"
            runs = inline_runs(cell)
            if r_idx == 0:
                runs = [dict(r, bold=True) for r in runs]
            body = "".join(run_xml(r) for r in runs)
            out.append('<w:p><w:pPr><w:pStyle w:val="%s"/></w:pPr>%s</w:p>'
                       % (style, body))
            out.append("</w:tc>")
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def _split_table_row(line):
    line = line.strip().strip("|")
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line)]


def _is_separator(line):
    cells = _split_table_row(line)
    return bool(cells) and all(re.match(r"^:?-{2,}:?$", c.strip()) for c in cells)


def markdown_to_document_xml(markdown_text):
    blocks = kit.markdown_blocks(markdown_text)
    parts = []
    first_h1 = True
    table_rows = []
    in_code = False
    code_lines = []

    def flush_table():
        nonlocal table_rows
        if table_rows:
            rows = [r for r in table_rows if not _is_separator("|" + "|".join(r) + "|")]
            if rows:
                parts.append(table_xml(rows))
        table_rows = []

    def flush_code():
        nonlocal code_lines
        if code_lines:
            text = "\n".join(code_lines)
            runs = []
            for i, line in enumerate(text.split("\n")):
                if i:
                    runs.append("<w:r><w:br/></w:r>")
                runs.append('<w:r><w:t xml:space="preserve">%s</w:t></w:r>'
                            % xml_text(line))
            parts.append('<w:p><w:pPr><w:pStyle w:val="Code"/></w:pPr>%s</w:p>'
                         % "".join(runs))
        code_lines = []

    for block in blocks:
        if block["kind"] != "table_row":
            flush_table()
        if block["kind"] == "code":
            code_lines = block["text"].split("\n")
            flush_code()
            continue
        if block["kind"] == "heading":
            level = min(4, block["level"])
            style = "Title" if (level == 1 and first_h1) else "Heading%d" % level
            if level == 1:
                first_h1 = False
            parts.append(paragraph_xml(block["text"], style))
        elif block["kind"] == "list":
            stripped = re.sub(r"^([-*+])\s+", "", block["text"])
            parts.append(paragraph_xml(stripped, "ListParagraph", bullet=True))
        elif block["kind"] == "quote":
            parts.append(paragraph_xml(block["text"], "Quote"))
        elif block["kind"] == "table_row":
            table_rows.append(_split_table_row(block["text"]))
        else:
            parts.append(paragraph_xml(block["text"], "Normal"))
    flush_table()
    return DOC_HEAD + "".join(parts) + DOC_TAIL


def _core_xml(title, case_id):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>%s</dc:title><dc:creator>research-translation-writer</dc:creator>
<cp:lastModifiedBy>research-translation-writer</cp:lastModifiedBy>
<cp:subject>%s</cp:subject>
<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>
<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>
</cp:coreProperties>""" % (xml_text(title), xml_text(case_id), stamp, stamp)


def _app_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>research-transfer-kit</Application><AppVersion>1.0</AppVersion>
</Properties>"""


def convert(markdown_text, out_path, title=None, case_id="unknown", preview=False):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document = markdown_to_document_xml(markdown_text)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("word/_rels/document.xml.rels", DOC_RELS)
        zf.writestr("word/document.xml", document)
        zf.writestr("word/styles.xml", STYLES)
        zf.writestr("docProps/core.xml", _core_xml(title or out_path.stem, case_id))
        zf.writestr("docProps/app.xml", _app_xml())
    meta = {
        "path": str(out_path),
        "bytes": out_path.stat().st_size,
        "sha256": kit.sha256_file(out_path),
        "valid": verify_docx(out_path),
        "preview": None,
    }
    if preview:
        meta["preview"] = try_render_pdf(out_path)
    return meta


def verify_docx(path):
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml",
                        "word/styles.xml"}
            if not required.issubset(names):
                return False
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
            return "<w:body>" in xml and "</w:document>" in xml
    except (OSError, zipfile.BadZipFile, KeyError):
        return False


def _find_soffice():
    env = os.environ.get("SOFFICE_BIN")
    if env and Path(env).is_file():
        return env
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                      "/usr/bin/soffice", "/usr/local/bin/soffice"):
        if Path(candidate).is_file():
            return candidate
    return None


def try_render_pdf(docx_path, timeout=90):
    binary = _find_soffice()
    if not binary:
        return {"ok": False, "reason": "soffice_not_found",
                "note": "DOCX is editable but no local renderer was available"}
    out_dir = Path(docx_path).parent
    try:
        with tempfile.TemporaryDirectory(prefix="lo-home-") as home:
            env = dict(os.environ)
            env["HOME"] = home
            proc = subprocess.run(
                [binary, "--headless", "--convert-to", "pdf", "--outdir",
                 str(out_dir), str(docx_path)],
                capture_output=True, timeout=timeout, env=env)
        pdf = out_dir / (Path(docx_path).stem + ".pdf")
        if proc.returncode == 0 and pdf.is_file():
            return {"ok": True, "path": str(pdf), "bytes": pdf.stat().st_size}
        return {"ok": False, "reason": "conversion_failed",
                "detail": proc.stderr.decode("utf-8", errors="replace")[-400:]}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "reason": "conversion_error", "detail": str(exc)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--draft", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--case-id", default="unknown")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    draft_path = Path(args.draft)
    if not draft_path.is_file():
        print("draft not found: %s" % draft_path, file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else draft_path.with_suffix(".docx")
    meta = convert(kit.read_text(draft_path), out,
                   title=args.title, case_id=args.case_id, preview=args.preview)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0 if meta["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
