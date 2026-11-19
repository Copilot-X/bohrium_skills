#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Document parsers used by the Period 4 skills.

Pure standard library plus optional runtime helpers:

* PDF: ``pdftotext -layout`` first, then ``pypdf``, then optional OCR through
  ``pdftoppm`` + ``tesseract``.  The parser never invents text for an
  unreadable page; the page is returned with ``readable: false``.
* DOCX: ``python-docx`` if installed, otherwise a small OOXML reader.
* TeX / BibTeX / RIS / CSV: deterministic structure extraction.

The functions are deliberately conservative: a missing dependency degrades to
a clearly reported limitation, not to guessed content.
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

VERSION = "1.0.0"

KNOWN_HEADING_RE = re.compile(
    r"^\s*(?:\d{1,2}(?:\.\d{1,2})*[.)]?\s+)?"
    r"(?:Abstract|Summary|Introduction|Background|Related Work|"
    r"Materials? and Methods?|Methods?|Experimental(?: Section)?|Results?|"
    r"Discussion|Conclusions?|References|Bibliography|Acknowledg(?:e)?ments?|"
    r"Supplementary|Appendix|摘要|引言|前言|相关工作|方法|材料与方法|实验|"
    r"结果|讨论|结论|参考文献|致谢|附录)\s*$",
    re.I,
)
NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?P<num>\d{1,2}(?:\.\d{1,2})*)[.)]?\s+"
    r"(?P<title>[A-Z一-鿿][^\n,;:]{1,80})$"
)
HEADING_RE = KNOWN_HEADING_RE
CAPTION_RE = re.compile(
    r"^\s*(?:Fig(?:ure)?\.?|Table|Scheme|图|表|附图|附表)\s*"
    r"[A-Za-z0-9一二三四五六七八九十]+\s*[.:：|]\s*",
    re.I,
)


def is_heading(text):
    value = (text or "").strip()
    if not value or len(value) > 140:
        return False
    if KNOWN_HEADING_RE.match(value):
        return True
    if re.match(r"^[A-Z][A-Z\s&/()-]{3,60}$", value):
        return True
    match = NUMBERED_HEADING_RE.match(value)
    if match:
        title = match.group("title").strip()
        if title.endswith("."):
            return False
        if re.match(r"^(?:C|°C|%|K|h|min|s|bar|MPa|kPa|mL|mg|g|kg|nm|mm|cm)\b",
                    title, re.I):
            return False
        if re.search(r"\b(?:for|with|and|of|the|compared|than)\b", title, re.I) \
                and len(title.split()) > 5:
            return False
        return len(title.split()) <= 12
    return False


EQUATION_RE = re.compile(
    r"(?:\\begin\{(?:equation|align|gather|multline|eqnarray)\*?\}|"
    r"\$\$|\\\[|\\(?:frac|sum|int|sqrt|alpha|beta|gamma|delta|lambda|mu|sigma)\b|"
    r"(?:->|→|⇌|[=≈≤≥±×÷∑∫√∞∂∇])[^。；;]{0,80}\(\s*\d+[a-z]?\s*\))",
    re.I,
)
MATH_SYMBOL_RE = re.compile(r"[=≈≤≥±×÷∑∫√∞∂∇→⇌αβγδεζηθλμνξπρστφχψωΓΔΘΛΞΠΣΦΨΩ]")
REF_HEADING_RE = re.compile(
    r"^\s*(?:References|Bibliography|Literature Cited|参考文献)\s*$", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _run(cmd, timeout=120):
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace") or proc.stdout.decode("utf-8", "replace")
        return None, err.strip()[:500]
    return proc.stdout.decode("utf-8", "replace"), ""


def _which(name):
    return shutil.which(name)


def _pdf_text_mode(path, mode):
    exe = _which("pdftotext")
    if not exe:
        return None, None, "pdftotext not found"
    out, err = _run([exe, mode, "-enc", "UTF-8", str(path), "-"])
    if out is None:
        return None, None, err
    pages = out.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages, "pdftotext-%s" % mode.lstrip("-"), ""


def text_from_pdf_layout(path):
    """Return (pages_text, engine, error) using pdftotext -layout."""
    return _pdf_text_mode(path, "-layout")


def text_from_pdf_raw(path):
    """Return reading-order text (content-stream order) using pdftotext -raw."""
    return _pdf_text_mode(path, "-raw")


def text_from_pdf_pypdf(path):
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return None, None, "pypdf unavailable: %s" % exc
    try:
        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return pages, "pypdf", ""
    except Exception as exc:  # noqa: BLE001
        return None, None, "pypdf failed: %s" % exc


def _ocr_page(path, page_number, tmp_dir, lang="eng+chi_sim"):
    pdftoppm = _which("pdftoppm")
    tesseract = _which("tesseract")
    if not pdftoppm or not tesseract:
        return None, "OCR tools unavailable (pdftoppm/tesseract)"
    prefix = Path(tmp_dir) / ("page-%05d" % page_number)
    out, err = _run([pdftoppm, "-r", "300", "-png", "-f", str(page_number),
                     "-l", str(page_number), str(path), str(prefix)], timeout=180)
    if out is None:
        return None, "pdftoppm failed: %s" % err
    images = sorted(Path(tmp_dir).glob("page-%05d-*.png" % page_number))
    if not images:
        return None, "pdftoppm produced no image"
    text, err = _run([tesseract, str(images[0]), "stdout", "-l", lang], timeout=180)
    if text is None:
        return None, "tesseract failed: %s" % err
    return text, ""


def _line_blocks(page_text, page_number, page_width=None, page_height=None):
    """Split text into paragraph-like blocks.

    ``pdftotext`` does not expose paragraph objects, so this uses conservative
    cues: blank lines, heading patterns, reference-entry starts, and a short
    preceding line ending in sentence punctuation.  The result is a draft that
    the model can review; it is never presented as a perfect layout parse.
    """
    lines = page_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = []
    buf = []
    start = 0
    lengths = [len(line.strip()) for line in lines if line.strip()]
    median = sorted(lengths)[len(lengths) // 2] if lengths else 0

    def flush(end_index):
        nonlocal buf, start
        if buf:
            text = "\n".join(buf).strip()
            if text:
                blocks.append({
                    "text": text,
                    "page": page_number,
                    "line_start": start + 1,
                    "line_end": end_index,
                    "bbox": None,
                    "column": None,
                    "kind": _classify_block(text, page_number, None, None),
                })
        buf = []

    for index, raw_line in enumerate(lines + [""]):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            flush(index)
            continue
        heading = bool(is_heading(stripped) or REF_HEADING_RE.match(stripped))
        ref_start = bool(re.match(r"^\s*(?:\[\d+\]|\d+[.)])\s+", stripped))
        if heading or ref_start:
            flush(index)
            start = index
            buf = [line]
            if heading:
                flush(index + 1)
            continue
        # Start a new paragraph when the previous line is a short paragraph end.
        if buf and len(buf) >= 1:
            previous = buf[-1].strip()
            short = median and len(previous) < median * 0.80
            terminal = bool(re.search(r"[.!?。！？:；;]['\"”’)]*$", previous))
            starts_new = bool(re.match(r"^(?:[A-Z一-鿿]|\d+[.)]\s|[-*+]\s)", stripped))
            if short and terminal and starts_new:
                flush(index)
        if not buf:
            start = index
        buf.append(line)
    flush(len(lines))
    # Approximate vertical positions only for header/footer classification.
    if page_height:
        total = max(len(lines), 1)
        for block in blocks:
            y0 = (block["line_start"] - 1) / total * page_height
            y1 = block["line_end"] / total * page_height
            block["bbox"] = [0, round(y0, 1), page_width or 0, round(y1, 1)]
            block["kind"] = _classify_block(block["text"], page_number,
                                            block["bbox"], page_height)
    return blocks


def _classify_block(text, page_number, bbox, page_height):
    stripped = text.strip()
    if not stripped:
        return "empty"
    if bbox and page_height:
        if bbox[1] < page_height * 0.055:
            return "header"
        if bbox[3] > page_height * 0.945:
            return "footer"
    if REF_HEADING_RE.match(stripped):
        return "heading"
    if is_heading(stripped):
        return "heading"
    if CAPTION_RE.match(stripped):
        return "caption"
    if EQUATION_RE.search(stripped) and (MATH_SYMBOL_RE.search(stripped) or "\\" in stripped):
        return "formula"
    if re.match(r"^\s*(?:\[\d+\]|\d+[.)])\s+", stripped) and DOI_RE.search(stripped):
        return "reference"
    return "body"


def order_blocks(blocks, page_width=None):
    """Best-effort two-column reading order when bbox information exists."""
    if not blocks:
        return blocks
    if all(not b.get("bbox") for b in blocks):
        return sorted(blocks, key=lambda b: (b.get("line_start", 0), b.get("line_end", 0)))
    width = page_width or max((b["bbox"][2] for b in blocks if b.get("bbox")), default=0)
    if not width:
        return blocks
    mid = width / 2.0
    full, left, right = [], [], []
    for b in blocks:
        box = b.get("bbox")
        if not box:
            full.append(b)
            continue
        x0, _, x1, _ = box
        if (x1 - x0) >= width * 0.72:
            full.append(b)
        elif x0 + (x1 - x0) / 2 < mid:
            left.append(b)
        else:
            right.append(b)
    # A page with no right-column block is single-column.
    if len(right) < 2:
        ordered = sorted(blocks, key=lambda b: (b.get("bbox") or [0, 0, 0, 0])[1])
        return ordered
    full.sort(key=lambda b: (b.get("bbox") or [0, 0, 0, 0])[1])
    left.sort(key=lambda b: (b.get("bbox") or [0, 0, 0, 0])[1])
    right.sort(key=lambda b: (b.get("bbox") or [0, 0, 0, 0])[1])
    # Full-width blocks above, between and below columns are placed by y.
    top = [b for b in full if (b.get("bbox") or [0, 0, 0, 0])[1] < min(
        [(x.get("bbox") or [0, 10 ** 9, 0, 0])[1] for x in left + right], default=0)]
    bottom = [b for b in full if b not in top]
    return top + left + right + bottom


def _merge_reference_blocks(blocks):
    """Join consecutive reference entries that were split across lines."""
    out = []
    in_refs = False
    for block in blocks:
        text = block.get("text", "")
        if REF_HEADING_RE.match(text.strip()):
            in_refs = True
            out.append(block)
            continue
        if in_refs and block.get("kind") not in ("heading", "footer", "header"):
            if out and out[-1].get("kind") == "reference" and not re.match(
                    r"^\s*(?:\[\d+\]|\d+[.)])\s+", text):
                out[-1]["text"] = out[-1]["text"].rstrip() + " " + text.strip()
                continue
            if re.match(r"^\s*(?:\[\d+\]|\d+[.)])\s+", text) or DOI_RE.search(text):
                block = dict(block)
                block["kind"] = "reference"
        out.append(block)
    return out


def _pdf_fitz(path):
    """Return page/block geometry with PyMuPDF; None when unavailable."""
    try:
        import fitz  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return None, "pymupdf unavailable: %s" % exc
    try:
        doc = fitz.open(str(path))
        pages = []
        for page in doc:
            page_dict = page.get_text("dict")
            width = float(page.rect.width)
            height = float(page.rect.height)
            blocks = []
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                lines = block.get("lines", [])
                text = "\n".join(
                    "".join(span.get("text", "") for span in line.get("spans", []))
                    for line in lines
                ).strip()
                if not text:
                    continue
                sizes = [span.get("size", 0) for line in lines
                         for span in line.get("spans", []) if span.get("size")]
                bbox = list(block.get("bbox") or [0, 0, 0, 0])
                blocks.append({
                    "text": text,
                    "page": page.number + 1,
                    "bbox": [round(x, 1) for x in bbox],
                    "font_size": round(max(sizes), 1) if sizes else None,
                    "line_start": None,
                    "line_end": None,
                    "column": None,
                    "kind": _classify_block(text, page.number + 1, bbox, height),
                })
            pages.append({
                "page": page.number + 1,
                "text": page.get_text("text") or "",
                "chars": len((page.get_text("text") or "").strip()),
                "readable": bool((page.get_text("text") or "").strip()),
                "ocr": False,
                "width": width,
                "height": height,
                "blocks": blocks,
            })
        return pages, ""
    except Exception as exc:  # noqa: BLE001
        return None, "pymupdf failed: %s" % exc


def extract_pdf(path, ocr=True, tmp_dir=None):
    path = Path(path)
    result = {
        "kind": "pdf",
        "path": str(path),
        "page_count": 0,
        "pages": [],
        "blocks": [],
        "tables": [],
        "text": "",
        "engine": None,
        "warnings": [],
    }
    fitz_pages, fitz_err = _pdf_fitz(path)
    if fitz_pages is not None:
        result["engine"] = "pymupdf"
        result["page_count"] = len(fitz_pages)
        result["pages"] = fitz_pages
        for page in fitz_pages:
            if not page.get("readable") and ocr:
                ocr_text, ocr_err = _ocr_page(path, page["page"], tmp_dir or path.parent)
                if ocr_text and ocr_text.strip():
                    page["text"] = ocr_text
                    page["chars"] = len(ocr_text.strip())
                    page["readable"] = True
                    page["ocr"] = True
                    page["blocks"] = _line_blocks(ocr_text, page["page"])
                else:
                    page["ocr_error"] = ocr_err
                    result["warnings"].append("page %d: %s" % (page["page"], ocr_err))
            result["blocks"].extend(page.get("blocks") or [])
        result["text"] = "\n\n".join(p["text"] for p in result["pages"] if p["text"]).strip()
        result["blocks"] = _merge_reference_blocks(result["blocks"])
        return result
    result["warnings"].append(fitz_err)
    pages_text, engine, err = text_from_pdf_raw(path)
    if pages_text is None or not any((p or "").strip() for p in pages_text):
        if pages_text is None:
            result["warnings"].append(err)
        pages_text, engine, err = text_from_pdf_layout(path)
    if pages_text is None or not any((p or "").strip() for p in pages_text):
        if pages_text is None:
            result["warnings"].append(err)
        pages_text, engine, err = text_from_pdf_pypdf(path)
    if pages_text is None:
        result["warnings"].append(err)
        pages_text = []
    # Keep a physical-layout view for table detection, separately from the
    # reading-order text used for paragraphs.
    layout_pages, _, _ = text_from_pdf_layout(path)
    result["engine"] = engine
    result["page_count"] = len(pages_text)
    for page_number, page_text in enumerate(pages_text, 1):
        readable = bool(page_text and page_text.strip())
        page = {
            "page": page_number,
            "text": page_text or "",
            "layout_text": (layout_pages[page_number - 1]
                            if layout_pages and page_number <= len(layout_pages) else ""),
            "chars": len((page_text or "").strip()),
            "readable": readable,
            "ocr": False,
            "blocks": [],
        }
        if not readable and ocr:
            ocr_text, ocr_err = _ocr_page(path, page_number, tmp_dir or path.parent)
            if ocr_text and ocr_text.strip():
                page["text"] = ocr_text
                page["chars"] = len(ocr_text.strip())
                page["readable"] = True
                page["ocr"] = True
                page["engine"] = "tesseract"
            else:
                page["ocr_error"] = ocr_err
                result["warnings"].append("page %d: %s" % (page_number, ocr_err))
        blocks = _line_blocks(page["text"], page_number)
        page["blocks"] = blocks
        result["blocks"].extend(blocks)
        result["pages"].append(page)
    result["text"] = "\n\n".join(p["text"] for p in result["pages"] if p["text"]).strip()
    result["blocks"] = _merge_reference_blocks(result["blocks"])
    return result


def extract_docx(path):
    path = Path(path)
    result = {"kind": "docx", "path": str(path), "blocks": [], "tables": [],
              "text": "", "warnings": []}
    try:
        from docx import Document  # type: ignore
    except Exception:
        return _extract_docx_xml(path, result)
    try:
        doc = Document(str(path))
        for para in doc.paragraphs:
            text = para.text or ""
            if not text.strip():
                continue
            style = (para.style.name if para.style else "") or ""
            kind = "heading" if style.lower().startswith(("heading", "title")) else "body"
            result["blocks"].append({"text": text, "style": style, "kind": kind,
                                     "page": None, "bbox": None, "column": None})
        for table in doc.tables:
            rows = []
            for row in table.rows:
                rows.append([cell.text.strip() for cell in row.cells])
            if rows:
                result["tables"].append({"rows": rows, "source": "docx-table"})
        result["text"] = "\n\n".join(b["text"] for b in result["blocks"])
        return result
    except Exception as exc:  # noqa: BLE001
        result["warnings"].append("python-docx failed: %s" % exc)
        return _extract_docx_xml(path, result)


def _extract_docx_xml(path, result):
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
        root = ET.fromstring(xml)
    except Exception as exc:  # noqa: BLE001
        result["warnings"].append("docx XML read failed: %s" % exc)
        return result
    body = root.find("w:body", ns)
    if body is None:
        return result
    for child in body:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(t.text or "" for t in child.iter(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
            if text.strip():
                style_el = child.find("w:pPr/w:pStyle", ns)
                style = style_el.get("{%s}val" % ns["w"]) if style_el is not None else ""
                kind = "heading" if (style or "").lower().startswith(("heading", "title")) else "body"
                result["blocks"].append({"text": text, "style": style, "kind": kind,
                                         "page": None, "bbox": None, "column": None})
        elif tag == "tbl":
            rows = []
            for tr in child.findall("w:tr", ns):
                row = []
                for tc in tr.findall("w:tc", ns):
                    row.append("".join(t.text or "" for t in tc.iter(
                        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")).strip())
                if any(row):
                    rows.append(row)
            if rows:
                result["tables"].append({"rows": rows, "source": "docx-table"})
    result["text"] = "\n\n".join(b["text"] for b in result["blocks"])
    return result


def detect_sections(blocks):
    """Turn a block stream into a section tree plus front matter."""
    sections = []
    current = {"id": "S0", "heading": "Front matter", "level": 0, "blocks": []}
    sections.append(current)
    counter = 0
    for block in blocks:
        text = (block.get("text") or "").strip()
        kind = block.get("kind")
        if kind == "heading" or is_heading(text):
            counter += 1
            level = 1
            match = re.match(r"^(\d+(?:\.\d+)*)", text)
            if match:
                level = min(4, match.group(1).count(".") + 1)
            elif text.lower().startswith(("abstract", "摘要")):
                level = 1
            current = {"id": "S%d" % counter, "heading": text, "level": level,
                       "blocks": [], "source_block_start": block.get("line_start")}
            sections.append(current)
        else:
            current["blocks"].append(block)
    return [s for s in sections if s["blocks"] or s["heading"] != "Front matter"]


def extract_tex(path):
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    result = {"kind": "tex", "path": str(path), "text": text,
              "sections": [], "citations": [], "refs": [], "labels": [],
              "equations": [], "tables": [], "figures": [], "warnings": []}
    heading_re = re.compile(
        r"\\(section|subsection|subsubsection|chapter|paragraph)\*?\{([^{}]*)\}")
    counter = 0
    for match in heading_re.finditer(text):
        counter += 1
        result["sections"].append({
            "id": "S%d" % counter,
            "level": {"chapter": 1, "section": 1, "subsection": 2,
                      "subsubsection": 3, "paragraph": 4}.get(match.group(1), 1),
            "heading": match.group(2).strip(),
            "start": match.start(),
        })
    result["citations"] = re.findall(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])?\{([^{}]+)\}", text)
    result["refs"] = re.findall(r"\\(?:ref|autoref|eqref|pageref)\{([^{}]+)\}", text)
    result["labels"] = re.findall(r"\\label\{([^{}]+)\}", text)
    for match in re.finditer(r"\\begin\{(equation|align|gather|multline|eqnarray)\*?\}(.*?)\\end\{\1\*?\}", text, re.S):
        result["equations"].append({"env": match.group(1), "body": match.group(2).strip(),
                                    "raw": match.group(0)})
    for match in re.finditer(r"\\begin\{(table|tabular)\*?\}(.*?)\\end\{\1\*?\}", text, re.S):
        result["tables"].append({"env": match.group(1), "body": match.group(2).strip(),
                                 "raw": match.group(0)})
    for match in re.finditer(r"\\begin\{figure\*?\}(.*?)\\end\{figure\*?\}", text, re.S):
        result["figures"].append({"body": match.group(1).strip(), "raw": match.group(0)})
    return result


def _read_braced(text, start):
    """Read a balanced {...} group beginning at or after ``start``."""
    i = text.find("{", start)
    if i < 0:
        return "", start
    depth = 0
    out = []
    for j in range(i, len(text)):
        ch = text[j]
        if ch == "{":
            depth += 1
            if depth == 1:
                continue
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return "".join(out), j + 1
        if depth >= 1:
            out.append(ch)
    return "".join(out), len(text)


def parse_bibtex(text):
    entries = []
    i = 0
    while True:
        at = text.find("@", i)
        if at < 0:
            break
        match = re.match(r"@(\w+)\s*\{", text[at:])
        if not match:
            i = at + 1
            continue
        entry_type = match.group(1).lower()
        body_start = at + match.end()
        # Find matching closing brace for the entry.
        depth = 1
        j = body_start
        while j < len(text) and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        body = text[body_start:j - 1]
        if entry_type in ("comment", "preamble", "string"):
            i = j
            continue
        key_part, _, fields_part = body.partition(",")
        key = key_part.strip()
        fields = {}
        pos = 0
        while pos < len(fields_part):
            eq = fields_part.find("=", pos)
            if eq < 0:
                break
            name = fields_part[pos:eq].strip().lower()
            value, new_pos = _read_braced(fields_part, eq)
            if not value:
                # bare value until comma
                comma = fields_part.find(",", eq)
                if comma < 0:
                    comma = len(fields_part)
                value = fields_part[eq + 1:comma].strip().strip('"')
                new_pos = comma
            fields[name] = re.sub(r"\s+", " ", value.replace("\n", " ")).strip()
            pos = new_pos
            comma = fields_part.find(",", pos)
            if comma < 0:
                break
            pos = comma + 1
        entries.append({"key": key, "type": entry_type, "fields": fields,
                        "raw": text[at:j]})
        i = j
    return entries


def parse_ris(text):
    records = []
    current = {}
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = re.match(r"^([A-Z][A-Z0-9])\s{1,2}-(.*)$", line)
        if not match:
            continue
        tag, value = match.group(1), match.group(2).strip()
        if tag == "TY":
            if current:
                records.append(current)
            current = {"TY": value}
        elif tag == "ER":
            if current:
                records.append(current)
            current = {}
        else:
            current.setdefault(tag, []).append(value)
    if current:
        records.append(current)
    return records


def _json_plain_text(value, depth=0):
    """Collect prose strings from a JSON material without metadata noise."""
    if depth > 8:
        return []
    if isinstance(value, str):
        return [value] if len(value.strip()) > 1 else []
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_json_plain_text(item, depth + 1))
        return out
    if not isinstance(value, dict):
        return []
    out = []
    prose_keys = ("text", "content", "paragraph", "sentence", "abstract",
                  "body", "caption", "quote", "summary", "description")
    for key in prose_keys:
        if isinstance(value.get(key), str) and len(value[key].strip()) > 1:
            out.append(value[key].strip())
    for key in ("paragraphs", "sections", "sentences", "materials", "papers",
                "documents", "items"):
        if key in value:
            out.extend(_json_plain_text(value[key], depth + 1))
    return out


def json_text(data):
    parts = _json_plain_text(data)
    if not parts:
        return ""
    # Keep paragraph boundaries; de-duplicate exact repeats.
    seen, out = set(), []
    for part in parts:
        if part not in seen:
            seen.add(part)
            out.append(part)
    return "\n\n".join(out)


def extract_csv(path):
    rows = []
    with Path(path).open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            rows.append([cell.strip() for cell in row])
    return {"kind": "csv", "path": str(path), "rows": rows,
            "text": "\n".join("\t".join(row) for row in rows)}


def extract_any(path, ocr=True, tmp_dir=None):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path, ocr=ocr, tmp_dir=tmp_dir)
    if suffix in (".docx", ".doc"):
        if suffix == ".doc":
            return {"kind": "doc", "path": str(path), "text": "", "blocks": [],
                    "warnings": ["legacy .doc requires an external converter"]}
        return extract_docx(path)
    if suffix in (".tex", ".latex"):
        return extract_tex(path)
    if suffix in (".bib",):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        entries = parse_bibtex(text)
        return {"kind": "bib", "path": str(path), "text": text,
                "entries": entries, "warnings": []}
    if suffix == ".ris":
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return {"kind": "ris", "path": str(path), "text": text,
                "records": parse_ris(text), "warnings": []}
    if suffix == ".csv":
        return extract_csv(path)
    if suffix in (".json",):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        plain = json_text(data) if data is not None else ""
        return {"kind": "json", "path": str(path),
                "text": plain or text, "raw_text": text,
                "data": data, "warnings": []}
    # Markdown, text, XML, YAML, TOML, code and unknown text formats.
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return {"kind": suffix.lstrip(".") or "text", "path": str(path),
            "text": text, "blocks": _line_blocks(text, None),
            "warnings": []}
