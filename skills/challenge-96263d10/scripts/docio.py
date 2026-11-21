#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DOCX I/O and structure inventory for the format-adapter skill.

Responsibilities (stdlib only, no third-party packages):

* read a DOCX package: sections, styles, theme fonts, paragraphs, tables,
  images, equations, fields, footnotes, captions and reference entries;
* read Markdown/LaTeX as *verbatim* blocks (no summarising, no extracted
  "baseline prose" -- the model owns the body text);
* write a DOCX from blocks by using the supplied template as the package
  skeleton (so styles, headers/footers and sectPr survive), or restyle an
  existing DOCX in place while keeping its body objects;
* read back a finished DOCX and report measured page/style/object facts.

The module never invents sentences. Text in the output is either the source
text unchanged or text supplied by the caller (model-authored).
"""
import argparse
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

for _prefix, _uri in (("w", W_NS), ("r", R_NS), ("wp", WP_NS),
                      ("a", A_NS), ("pic", PIC_NS), ("m", M_NS)):
    ET.register_namespace(_prefix, _uri)

TWIPS_CM = 2.54 / 1440.0
EMU_CM = 360000
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}")
BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
CAPTION_RE = re.compile(
    r"^(figure|fig\.?|table|图|表)\s*([0-9]+[A-Za-z]?)\s*"
    r"(?:[.:：、]\s*|\s+)?(.*)$", re.IGNORECASE)
NUM_CITE_RE = re.compile(r"\[([0-9]+(?:\s*[,\-–]\s*[0-9]+)*)\]")
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)*)\s?(%|°C|cm|mm|pt|mL|µL|mg|g|h|min|s)?")
TEX_HEAD_RE = re.compile(
    r"\\(part|chapter|section|subsection|subsubsection|paragraph)\*?\s*\{([^{}]*)\}")
TEX_CAPTION_RE = re.compile(r"\\caption(?:\[[^\]]*\])?\{([^{}]*)\}")
TEX_CITE_RE = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])?\{([^}]+)\}")
TEX_ENV_RE = re.compile(r"\\begin\{(figure\*?|table\*?)\}(.*?)\\end\{\1\}",
                        re.DOTALL)
TEX_EQ_RE = re.compile(
    r"\\begin\{(equation\*?|align\*?|eqnarray\*?|gather\*?|multline\*?)\}")
TEX_GRAPHIC_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{[^}]+\}")
DECL_ALIASES = {
    "data_availability": ("data availability", "availability of data", "数据可用性"),
    "conflict_of_interest": ("conflict of interest", "competing interests", "利益冲突"),
    "author_contributions": ("author contribution", "作者贡献"),
    "ethics": ("ethics approval", "ethical approval", "ethics statement", "伦理"),
    "funding": ("funding", "financial support", "基金", "资助"),
}
RPR_ORDER = [
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike",
    "dstrike", "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid",
    "vanish", "webHidden", "color", "spacing", "w", "kern", "position", "sz",
    "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign",
    "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
]
PPR_ORDER = [
    "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr",
    "widowControl", "numPr", "suppressLineNumbers", "pBdr", "shd", "tabs",
    "suppressAutoHyphens", "kinsoku", "wordWrap", "overflowPunct",
    "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
    "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents",
    "suppressOverlap", "jc", "textDirection", "textAlignment",
    "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr",
    "pPrChange",
]
RPR_INDEX = {name: i for i, name in enumerate(RPR_ORDER)}
PPR_INDEX = {name: i for i, name in enumerate(PPR_ORDER)}


def w(tag):
    return f"{{{W_NS}}}{tag}"


def a(tag):
    return f"{{{A_NS}}}{tag}"


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def read_text(path):
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def clean_space(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def strip_inline(text):
    text = str(text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    return clean_space(text)


def word_count(text):
    latin = re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", str(text))
    cjk = re.findall(r"[\u4e00-\u9fff]", str(text))
    return len(latin) + len(cjk)


def expand_citations(group):
    numbers = []
    for part in str(group).split(","):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[\-–]\s*(\d+)$", part)
        if m:
            low, high = int(m.group(1)), int(m.group(2))
            if low <= high and high - low <= 200:
                numbers.extend(range(low, high + 1))
        elif part.isdigit():
            numbers.append(int(part))
    return numbers


def caption_kind(text):
    match = CAPTION_RE.match(clean_space(text))
    if not match:
        return None, None
    label = match.group(1).lower()
    kind = "figure" if label.startswith(("figure", "fig", "图")) else "table"
    return kind, match.group(2)


def element_text(element):
    return clean_space("".join(t.text or "" for t in element.iter(w("t"))))


def get_int(element, name, default=None):
    if element is None:
        return default
    value = element.get(w(name))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def set_w_attr(element, name, value):
    if element is not None and value is not None:
        element.set(w(name), str(value))


def reorder_properties(root):
    for tag, index in (("pPr", PPR_INDEX), ("rPr", RPR_INDEX)):
        for parent in root.iter(w(tag)):
            children = list(parent)
            children.sort(key=lambda el: index.get(local_name(el.tag), 999))
            parent[:] = children


def twips_to_cm(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value * TWIPS_CM, 3)


def half_points_to_pt(value):
    try:
        return round(float(value) / 2.0, 2)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# OOXML style and section parsing
# ---------------------------------------------------------------------------
def _parse_rpr(rpr, out):
    if rpr is None:
        return out
    fonts = rpr.find(w("rFonts"))
    if fonts is not None:
        for key in ("ascii", "hAnsi", "eastAsia", "cs"):
            value = fonts.get(w(key))
            if value:
                out[f"font_{key}"] = value
        for key, theme_key in (("asciiTheme", "theme_major_minor_ascii"),
                               ("eastAsiaTheme", "theme_ea"),
                               ("hAnsiTheme", "theme_hansi"),
                               ("cstheme", "theme_cs")):
            value = fonts.get(w(key))
            if value:
                out[theme_key] = value
    size = rpr.find(w("sz"))
    if size is not None:
        out["size_pt"] = half_points_to_pt(size.get(w("val")))
    size_cs = rpr.find(w("szCs"))
    if size_cs is not None and "size_pt" not in out:
        out["size_pt"] = half_points_to_pt(size_cs.get(w("val")))
    for key in ("b", "i", "caps", "smallCaps", "strike", "vanish"):
        node = rpr.find(w(key))
        if node is not None:
            value = node.get(w("val"))
            out[key] = not (value in ("0", "false", "off"))
    color = rpr.find(w("color"))
    if color is not None and color.get(w("val")):
        out["color"] = color.get(w("val"))
    underline = rpr.find(w("u"))
    if underline is not None:
        out["underline"] = underline.get(w("val")) or "single"
    vert = rpr.find(w("vertAlign"))
    if vert is not None:
        out["vert_align"] = vert.get(w("val"))
    return out


def _parse_ppr(ppr, out):
    if ppr is None:
        return out
    style = ppr.find(w("pStyle"))
    if style is not None:
        out["pstyle"] = style.get(w("val"))
    outline = ppr.find(w("outlineLvl"))
    if outline is not None:
        level = get_int(outline, "val")
        if level is not None:
            out["outline_level"] = level + 1
    jc = ppr.find(w("jc"))
    if jc is not None:
        out["align"] = jc.get(w("val"))
    spacing = ppr.find(w("spacing"))
    if spacing is not None:
        before = get_int(spacing, "before")
        after = get_int(spacing, "after")
        line = get_int(spacing, "line")
        rule = spacing.get(w("lineRule")) or "auto"
        if before is not None:
            out["space_before_pt"] = round(before / 20.0, 2)
        if after is not None:
            out["space_after_pt"] = round(after / 20.0, 2)
        if line is not None:
            if rule == "auto":
                out["line_spacing"] = round(line / 240.0, 3)
                out["line_rule"] = "multiple"
            else:
                out["line_spacing_pt"] = round(line / 20.0, 2)
                out["line_rule"] = rule
    ind = ppr.find(w("ind"))
    if ind is not None:
        for key, out_key in (("left", "left_indent_cm"), ("right", "right_indent_cm"),
                             ("firstLine", "first_line_indent_cm"),
                             ("hanging", "hanging_indent_cm")):
            value = get_int(ind, key)
            if value is not None:
                out[out_key] = twips_to_cm(value)
    for key in ("keepNext", "keepLines", "pageBreakBefore"):
        node = ppr.find(w(key))
        if node is not None:
            value = node.get(w("val"))
            out[key] = not (value in ("0", "false", "off"))
    num = ppr.find(w("numPr"))
    if num is not None:
        num_id = num.find(w("numId"))
        ilvl = num.find(w("ilvl"))
        if num_id is not None:
            out["num_id"] = num_id.get(w("val"))
        if ilvl is not None:
            out["num_level"] = ilvl.get(w("val"))
    return out


def parse_styles(zf):
    result = {"doc_defaults": {}, "styles": {}, "by_name": {},
              "default_style_id": None, "warnings": []}
    try:
        root = ET.fromstring(zf.read("word/styles.xml"))
    except (KeyError, ET.ParseError) as exc:
        result["warnings"].append(f"styles.xml unavailable: {type(exc).__name__}")
        return result
    defaults = root.find(w("docDefaults"))
    if defaults is not None:
        rpr = defaults.find(f"{w('rPrDefault')}/{w('rPr')}")
        _parse_rpr(rpr, result["doc_defaults"])
        ppr = defaults.find(f"{w('pPrDefault')}/{w('pPr')}")
        _parse_ppr(ppr, result["doc_defaults"])
    for style in root.findall(w("style")):
        sid = style.get(w("styleId")) or ""
        name_el = style.find(w("name"))
        name = name_el.get(w("val")) if name_el is not None else sid
        based = style.find(w("basedOn"))
        record = {
            "style_id": sid, "name": name,
            "type": style.get(w("type")) or "paragraph",
            "based_on": based.get(w("val")) if based is not None else None,
            "default": style.get(w("default")) in ("1", "true"),
            "rpr": _parse_rpr(style.find(w("rPr")), {}),
            "ppr": _parse_ppr(style.find(w("pPr")), {}),
        }
        result["styles"][sid] = record
        result["by_name"][name.lower()] = sid
        if record["default"] and record["type"] == "paragraph":
            result["default_style_id"] = sid
    # Resolve inherited run/paragraph properties.
    for sid, record in result["styles"].items():
        merged = dict(result["doc_defaults"])
        chain, cursor, seen = [], sid, set()
        while cursor and cursor in result["styles"] and cursor not in seen:
            seen.add(cursor)
            chain.append(cursor)
            cursor = result["styles"][cursor].get("based_on")
        for ancestor in reversed(chain):
            item = result["styles"][ancestor]
            merged.update(item.get("rpr") or {})
            merged.update(item.get("ppr") or {})
        record["effective"] = merged
    return result


def parse_theme(zf):
    theme = {"major_latin": None, "minor_latin": None,
             "major_ea": None, "minor_ea": None,
             "major_cs": None, "minor_cs": None, "source": None}
    try:
        root = ET.fromstring(zf.read("word/theme/theme1.xml"))
    except (KeyError, ET.ParseError):
        return theme
    scheme = root.find(f".//{{{A_NS}}}fontScheme")
    if scheme is None:
        return theme
    theme["source"] = "word/theme/theme1.xml"
    for family in ("majorFont", "minorFont"):
        node = scheme.find(f"{{{A_NS}}}{family}")
        if node is None:
            continue
        prefix = "major" if family == "majorFont" else "minor"
        for tag, suffix in (("latin", "latin"), ("ea", "ea"), ("cs", "cs")):
            item = node.find(f"{{{A_NS}}}{tag}")
            if item is not None:
                theme[f"{prefix}_{suffix}"] = item.get("typeface") or None
        for script in node.findall(f"{{{A_NS}}}font"):
            if script.get("script") in ("Hans", "Hant") and \
                    not theme[f"{prefix}_ea"]:
                theme[f"{prefix}_ea"] = script.get("typeface")
    return theme


def _sect_record(sect, index):
    record = {"index": index, "page": {}, "margins": {}, "columns": {},
              "headers": [], "footers": [], "title_page": False,
              "doc_grid": {}, "raw": {}}
    page_size = sect.find(w("pgSz"))
    if page_size is not None:
        width = get_int(page_size, "w")
        height = get_int(page_size, "h")
        orient = page_size.get(w("orient")) or (
            "landscape" if width and height and width > height else "portrait")
        record["page"] = {"width_twips": width, "height_twips": height,
                          "orientation": orient,
                          "width_cm": twips_to_cm(width) if width else None,
                          "height_cm": twips_to_cm(height) if height else None}
    margins = sect.find(w("pgMar"))
    if margins is not None:
        for key in ("top", "right", "bottom", "left", "header", "footer", "gutter"):
            value = get_int(margins, key)
            if value is not None:
                record["margins"][key] = twips_to_cm(value)
                record["raw"][key] = value
    cols = sect.find(w("cols"))
    if cols is not None:
        num = get_int(cols, "num", 1) or 1
        space = get_int(cols, "space")
        record["columns"] = {"count": num,
                             "space_cm": twips_to_cm(space) if space is not None else None}
    for tag, key in (("headerReference", "headers"), ("footerReference", "footers")):
        for ref in sect.findall(w(tag)):
            record[key].append({"type": ref.get(w("type")),
                                "rid": ref.get(f"{{{R_NS}}}id")})
    title_page = sect.find(w("titlePg"))
    record["title_page"] = title_page is not None
    grid = sect.find(w("docGrid"))
    if grid is not None:
        record["doc_grid"] = {"type": grid.get(w("type")),
                              "line_pitch": get_int(grid, "linePitch")}
    return record


def parse_sections(root):
    sections = []
    for sect in root.iter(w("sectPr")):
        sections.append(_sect_record(sect, len(sections) + 1))
    return sections


def _para_record(paragraph, styles):
    ppr = paragraph.find(w("pPr"))
    parsed_ppr = _parse_ppr(ppr, {})
    sid = parsed_ppr.get("pstyle") or ""
    style = styles["styles"].get(sid) or {}
    effective = style.get("effective") or {}
    level = parsed_ppr.get("outline_level") or effective.get("outline_level")
    name = (style.get("name") or sid or "").lower()
    if level is None:
        match = re.match(r"^(?:heading|标题)\s*([1-9])", name)
        if match:
            level = int(match.group(1))
    text = element_text(paragraph)
    run_font = run_size = None
    for run in paragraph.iter(w("r")):
        rpr = run.find(w("rPr"))
        if rpr is None:
            continue
        fonts = rpr.find(w("rFonts"))
        if fonts is not None and not run_font:
            run_font = fonts.get(w("ascii")) or fonts.get(w("hAnsi")) or \
                fonts.get(w("eastAsia"))
        if not run_size:
            size = rpr.find(w("sz"))
            if size is not None:
                run_size = half_points_to_pt(size.get(w("val")))
        if run_font and run_size:
            break
    images = [node.get(f"{{{R_NS}}}embed")
              for node in paragraph.iter(f"{{{A_NS}}}blip")]
    equations = len(list(paragraph.iter(f"{{{M_NS}}}oMath")))
    fields = [node.text.strip() for node in paragraph.iter(w("instrText"))
              if node.text]
    fields += [node.get(w("instr")).strip() for node in paragraph.iter(w("fldSimple"))
               if node.get(w("instr"))]
    footnotes = len(list(paragraph.iter(w("footnoteReference")))) + \
        len(list(paragraph.iter(w("endnoteReference"))))
    kind, number = caption_kind(text)
    return {
        "index": 0, "text": text, "style_id": sid,
        "style_name": style.get("name") or sid, "level": level,
        "align": parsed_ppr.get("align") or effective.get("align"),
        "size_pt": effective.get("size_pt"),
        "font": effective.get("font_ascii") or effective.get("font_hAnsi"),
        "run_font": run_font, "run_size_pt": run_size,
        "font_east_asia": effective.get("font_eastAsia"),
        "line_spacing": effective.get("line_spacing"),
        "indent": {"left_cm": effective.get("left_indent_cm"),
                   "first_line_cm": effective.get("first_line_indent_cm"),
                   "hanging_cm": effective.get("hanging_indent_cm")},
        "images": [item for item in images if item],
        "equations": equations, "fields": fields, "footnotes": footnotes,
        "caption_kind": kind, "caption_number": number,
    }


def docx_read(path):
    """Read a DOCX and return a measured structure inventory."""
    path = Path(path)
    result = {"ok": False, "path": str(path), "error": None, "parts": [],
              "sections": [], "paragraphs": [], "tables": [], "captions": [],
              "references": {"heading_index": None, "entries": []},
              "citations": [], "numbers": [], "styles_used": {},
              "fonts_used": [], "sizes_used": [], "counts": {},
              "warnings": [], "theme": {}, "styles": {},
              "doc_defaults": {}}
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                result["error"] = f"corrupt member: {bad}"
                return result
            names = set(zf.namelist())
            result["parts"] = sorted(names)
            if "word/document.xml" not in names:
                result["error"] = "missing word/document.xml"
                return result
            root = ET.fromstring(zf.read("word/document.xml"))
            styles = parse_styles(zf)
            theme = parse_theme(zf)
    except (zipfile.BadZipFile, OSError, ET.ParseError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["theme"] = theme
    result["styles"] = styles["styles"]
    result["doc_defaults"] = styles["doc_defaults"]
    result["warnings"].extend(styles["warnings"])
    result["sections"] = parse_sections(root)
    body = root.find(w("body"))
    children = list(body) if body is not None else []
    paragraph_index = 0
    table_index = 0
    table_records = []
    for position, child in enumerate(children):
        if child.tag == w("tbl"):
            table_index += 1
            rows = []
            for tr in child.findall(w("tr")):
                cells = [clean_space(" ".join(element_text(p)
                                              for p in tc.findall(w("p"))))
                         for tc in tr.findall(w("tc"))]
                rows.append(cells)
            record = {"index": table_index, "position": position,
                      "rows": rows, "caption": None, "caption_position": None}
            # Nearest caption before/after the table becomes its caption.
            for delta, side in ((-1, "above"), (1, "below")):
                target = position + delta
                if 0 <= target < len(children) and children[target].tag == w("p"):
                    text = element_text(children[target])
                    kind, _ = caption_kind(text)
                    if kind == "table":
                        record["caption"] = text
                        record["caption_position"] = side
                        break
            table_records.append(record)
            continue
        if child.tag != w("p"):
            continue
        paragraph_index += 1
        record = _para_record(child, styles)
        record["index"] = paragraph_index
        record["position"] = position
        result["paragraphs"].append(record)
        if record["style_id"]:
            result["styles_used"][record["style_id"]] = \
                result["styles_used"].get(record["style_id"], 0) + 1
        for font_key in ("font", "font_east_asia", "run_font"):
            value = record.get(font_key)
            if value and value not in result["fonts_used"]:
                result["fonts_used"].append(value)
        for size_key in ("size_pt", "run_size_pt"):
            value = record.get(size_key)
            if value is not None and value not in result["sizes_used"]:
                result["sizes_used"].append(value)
        if record["caption_kind"]:
            result["captions"].append({
                "kind": record["caption_kind"],
                "number": record["caption_number"],
                "text": record["text"], "paragraph": record["index"],
                "style_id": record["style_id"],
            })
        for match in NUM_CITE_RE.finditer(record["text"]):
            result["citations"].extend(expand_citations(match.group(1)))
        for match in NUMBER_RE.finditer(record["text"]):
            result["numbers"].append({
                "value": match.group(1), "unit": match.group(2) or "",
                "paragraph": record["index"],
                "context": record["text"][:140]})
    result["tables"] = table_records
    ordered_text = [(paragraph.get("position", 0), 0, paragraph["text"])
                    for paragraph in result["paragraphs"] if paragraph["text"]]
    for table in table_records:
        cells = [cell for row in table.get("rows") or [] for cell in row if cell]
        if cells:
            ordered_text.append((table.get("position", 0), 1, " ".join(cells)))
    ordered_text.sort(key=lambda item: (item[0], item[1]))
    result["full_text"] = "\n".join(item[2] for item in ordered_text)
    result["citations"] = sorted(set(result["citations"]))
    # Reference section: heading plus following non-empty paragraphs.
    reference_heading = re.compile(
        r"^(references?|reference list|bibliography|works cited|参考文献|引用文献)\s*[:：]?$",
        re.IGNORECASE)
    heading_index = None
    for record in result["paragraphs"]:
        if reference_heading.match(record["text"].strip()):
            heading_index = record["index"]
            break
    if heading_index is not None:
        result["references"]["heading_index"] = heading_index
        for record in result["paragraphs"]:
            if record["index"] <= heading_index:
                continue
            if record["level"] and record["index"] > heading_index + 1:
                break
            if record["text"]:
                result["references"]["entries"].append({
                    "paragraph": record["index"], "text": record["text"],
                    "style_id": record["style_id"],
                    "indent": record.get("indent") or {},
                    "font": record.get("run_font") or record.get("font"),
                    "size_pt": record.get("run_size_pt") or record.get("size_pt")})
    result["counts"] = {
        "sections": sum(1 for p in result["paragraphs"] if p["level"]),
        "section_breaks": len(result["sections"]),
        "paragraphs": len(result["paragraphs"]),
        "tables": len(table_records),
        "figures": sum(1 for p in result["paragraphs"] if p["images"]),
        "equations": sum(p["equations"] for p in result["paragraphs"]),
        "fields": sum(len(p["fields"]) for p in result["paragraphs"]),
        "footnotes": sum(p["footnotes"] for p in result["paragraphs"]),
        "captions": len(result["captions"]),
        "references": len(result["references"]["entries"]),
        "citations": len(result["citations"]),
        "numbers": len(result["numbers"]),
        "words": sum(word_count(p["text"]) for p in result["paragraphs"]),
    }
    result["ok"] = True
    return result


# ---------------------------------------------------------------------------
# Markdown / LaTeX -> verbatim blocks
# ---------------------------------------------------------------------------
def _split_row(line):
    body = line.strip().strip("|")
    return [cell.strip() for cell in body.split("|")]


def _is_block_start(lines, index):
    line = lines[index].strip()
    if not line:
        return True
    if FENCE_RE.match(line) or HEADING_RE.match(line):
        return True
    if line.startswith(("$$", ">", "<!--")):
        return True
    if BULLET_RE.match(line) or NUMBERED_RE.match(line):
        return True
    if TABLE_ROW_RE.match(line) and index + 1 < len(lines) and \
            TABLE_SEP_RE.match(lines[index + 1].strip()):
        return True
    return False


def parse_markdown(text, base_dir=None):
    """Parse Markdown into blocks while keeping every sentence verbatim."""
    lines = str(text).splitlines()
    base = Path(base_dir) if base_dir else None
    warnings, changes, items = [], [], []
    index, total = 0, len(lines)
    if total and lines[0].strip() == "---":
        for probe in range(1, min(total, 120)):
            if lines[probe].strip() in ("---", "..."):
                index = probe + 1
                break
    while index < total:
        raw = lines[index]
        line = raw.strip()
        if not line:
            index += 1
            continue
        if FENCE_RE.match(line):
            index += 1
            while index < total and not FENCE_RE.match(lines[index].strip()):
                if lines[index].strip():
                    items.append(("paragraph", strip_inline(lines[index]), index + 1))
                index += 1
            index += 1
            warnings.append("fenced code content kept as plain paragraphs")
            continue
        if line.startswith("<!--") and line.endswith("-->"):
            if "pagebreak" in line.lower():
                items.append(("pagebreak", "", index + 1))
            index += 1
            continue
        if line.startswith("$$"):
            buffer = [line]
            if line.count("$$") < 2:
                index += 1
                while index < total and "$$" not in lines[index]:
                    buffer.append(lines[index])
                    index += 1
                if index < total:
                    buffer.append(lines[index])
            index += 1
            content = clean_space(" ".join(part.strip().strip("$") for part in buffer))
            items.append(("equation", content, index))
            warnings.append("display equation kept as text; native OMML is not "
                            "created when rebuilding from Markdown")
            continue
        heading = HEADING_RE.match(line)
        if heading:
            items.append(("heading", (len(heading.group(1)),
                                      strip_inline(heading.group(2))), index + 1))
            index += 1
            continue
        if TABLE_ROW_RE.match(line) and index + 1 < total and \
                TABLE_SEP_RE.match(lines[index + 1].strip()):
            header = _split_row(line)
            rows = []
            index += 2
            while index < total and TABLE_ROW_RE.match(lines[index].strip()):
                rows.append(_split_row(lines[index].strip()))
                index += 1
            caption = None
            caption_kind_value = None
            probe = index
            while probe < total and not lines[probe].strip():
                probe += 1
            if probe < total:
                kind, _ = caption_kind(strip_inline(lines[probe]))
                if kind == "table":
                    caption = strip_inline(lines[probe])
                    caption_kind_value = kind
                    index = probe + 1
            items.append(("table", {"header": header, "rows": rows,
                                    "caption": caption}, index))
            continue
        bullet = BULLET_RE.match(line)
        if bullet:
            buffer = []
            while index < total:
                match = BULLET_RE.match(lines[index].strip())
                if not match:
                    break
                buffer.append(strip_inline(match.group(1)))
                index += 1
            items.append(("bullets", buffer, index))
            continue
        numbered = NUMBERED_RE.match(line)
        if numbered:
            buffer = []
            while index < total:
                match = NUMBERED_RE.match(lines[index].strip())
                if not match:
                    break
                buffer.append(strip_inline(match.group(1)))
                index += 1
            items.append(("numbers", buffer, index))
            continue
        if line.startswith(">"):
            buffer = []
            while index < total and lines[index].strip().startswith(">"):
                buffer.append(strip_inline(lines[index].strip()[1:]))
                index += 1
            items.append(("quote", " ".join(buffer), index))
            continue
        image = IMAGE_RE.match(line)
        if image:
            alt, rel = image.group(1), image.group(2)
            target = (base / rel) if base else Path(rel)
            caption = None
            probe = index + 1
            while probe < total and not lines[probe].strip():
                probe += 1
            if probe < total:
                kind, _ = caption_kind(strip_inline(lines[probe]))
                if kind == "figure":
                    caption = strip_inline(lines[probe])
                    index = probe + 1
            if target.is_file():
                items.append(("figure", {"path": str(target),
                                         "caption": caption}, index + 1))
            else:
                items.append(("paragraph",
                              f"[image: {alt or rel}]" +
                              (f" {caption}" if caption else ""), index + 1))
                warnings.append(f"image not found, kept as text: {rel}")
            index = max(index, probe) if probe <= index else index + 1
            continue
        buffer = [line]
        index += 1
        while index < total and lines[index].strip() and \
                not _is_block_start(lines, index):
            buffer.append(lines[index].strip())
            index += 1
        items.append(("paragraph", strip_inline(" ".join(buffer)), index))

    title = None
    if items and items[0][0] == "heading" and items[0][1][0] == 1:
        later = [item for item in items[1:]
                 if item[0] == "heading" and item[1][0] >= 2]
        if later:
            title = items[0][1][1]
            items = items[1:]
    blocks = []
    stats = {"headings": 0, "paragraphs": 0, "tables": 0, "figures": 0,
             "equations": 0, "captions": 0, "words": word_count(text)}
    for kind, payload, line_no in items:
        if kind == "heading":
            level, heading_text = payload
            mapped = level - 1 if title else level
            mapped = max(1, min(6, mapped))
            if mapped != level - (1 if title else 0):
                warnings.append(f"heading level {level} clamped at line {line_no}")
            blocks.append({"type": "heading", "level": mapped,
                           "text": heading_text})
            stats["headings"] += 1
        elif kind == "paragraph":
            if payload:
                block = {"type": "paragraph", "text": payload}
                cap_kind, _ = caption_kind(payload)
                if cap_kind:
                    block["role"] = "caption"
                    block["caption_kind"] = cap_kind
                    stats["captions"] += 1
                blocks.append(block)
                stats["paragraphs"] += 1
        elif kind in ("bullets", "numbers"):
            if payload:
                blocks.append({"type": kind, "items": payload})
                stats["paragraphs"] += 1
        elif kind == "quote":
            blocks.append({"type": "quote", "text": payload})
            stats["paragraphs"] += 1
        elif kind == "table":
            blocks.append({"type": "table", **payload})
            stats["tables"] += 1
        elif kind == "figure":
            blocks.append({"type": "figure", **payload})
            stats["figures"] += 1
        elif kind == "equation":
            blocks.append({"type": "paragraph", "text": f"Equation: {payload}",
                           "role": "equation"})
            stats["equations"] += 1
        elif kind == "pagebreak":
            blocks.append({"type": "pagebreak"})
    if re.search(r"(\*\*|__|`|~~)", str(text)):
        changes.append({"section": "document",
                        "before": "Markdown emphasis markers",
                        "after": "plain text runs; emphasis is carried by DOCX runs",
                        "reason": "markers are formatting, not content"})
    return {"title": title, "blocks": blocks, "warnings": warnings,
            "text_changes": changes, "stats": stats}


def tex_to_blocks(text):
    warnings = []
    clean = re.sub(r"(?<!\\)%.*$", "", str(text), flags=re.MULTILINE)
    title = ""
    match = re.search(r"\\title\s*\{([^{}]*)\}", clean, re.DOTALL)
    if match:
        title = strip_inline(re.sub(r"\\[a-zA-Z]+\s*", "", match.group(1)))
        clean = clean[:match.start()] + clean[match.end():]
    abstract = ""
    match = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", clean, re.DOTALL)
    if match:
        abstract = strip_inline(re.sub(r"\\[a-zA-Z]+\s*", "", match.group(1)))
        clean = clean[:match.start()] + "\n## Abstract\n\n" + abstract + "\n" + \
            clean[match.end():]
    figure_no = table_no = 0
    def env_repl(match):
        nonlocal figure_no, table_no
        kind = "figure" if match.group(1).startswith("figure") else "table"
        if kind == "figure":
            figure_no += 1
            number = figure_no
        else:
            table_no += 1
            number = table_no
        caption = TEX_CAPTION_RE.search(match.group(2))
        caption_text = strip_inline(re.sub(r"\\[a-zA-Z]+\s*", "", caption.group(1))) \
            if caption else ""
        label = "Figure" if kind == "figure" else "Table"
        warnings.append(f"LaTeX {kind} environment flattened to caption text")
        return f"\n{label} {number}. {caption_text}\n"
    clean = TEX_ENV_RE.sub(env_repl, clean)
    level_map = {"part": 1, "chapter": 1, "section": 1, "subsection": 2,
                 "subsubsection": 3, "paragraph": 4}
    def head_repl(match):
        level = level_map.get(match.group(1), 1)
        heading_text = strip_inline(re.sub(r"\\[a-zA-Z]+\s*", "", match.group(2)))
        return "\n" + "#" * min(level + 1, 6) + " " + heading_text + "\n"
    clean = TEX_HEAD_RE.sub(head_repl, clean)
    def cite_repl(match):
        keys = [key.strip() for key in match.group(1).split(",") if key.strip()]
        warnings.append(f"LaTeX citation keys kept as text: {', '.join(keys)}")
        return "[" + "; ".join(keys) + "]"
    clean = TEX_CITE_RE.sub(cite_repl, clean)
    clean = re.sub(r"\\begin\{document\}|\\end\{document\}", "", clean)
    clean = re.sub(r"\\(?:label|ref|autoref|cref|Cref|eqref)\s*\{([^{}]*)\}",
                   r"\1", clean)
    clean = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?", "", clean)
    clean = clean.replace("{", "").replace("}", "").replace("~", " ")
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    if TEX_EQ_RE.search(str(text)) or re.search(r"\\\[", str(text)):
        warnings.append("LaTeX equations were counted but not converted to OMML")
    if TEX_GRAPHIC_RE.search(str(text)):
        warnings.append("LaTeX graphics paths were not embedded automatically")
    markdown = (f"# {title}\n\n" if title else "") + clean.strip() + "\n"
    parsed = parse_markdown(markdown)
    parsed["warnings"] = warnings + parsed["warnings"]
    return parsed


def docx_to_blocks(path):
    """Turn a DOCX into blocks for reference; images stay as placeholders."""
    inventory = docx_read(path)
    if not inventory["ok"]:
        raise ValueError(inventory.get("error") or "cannot read docx")
    blocks = []
    warnings = []
    for record in inventory["paragraphs"]:
        if record["images"]:
            blocks.append({"type": "paragraph",
                           "text": f"[embedded image, {len(record['images'])} part(s); "
                                   f"an existing DOCX source is restyled in place]"})
            warnings.append("embedded images are preserved only when the DOCX is "
                            "restyled in place")
        if record["equations"]:
            blocks.append({"type": "paragraph", "text": "[OMML equation]"})
            warnings.append("OMML equations are preserved only when the DOCX is "
                            "restyled in place")
        if not record["text"]:
            continue
        if record["level"]:
            blocks.append({"type": "heading", "level": min(record["level"], 6),
                           "text": record["text"]})
        elif record["caption_kind"]:
            blocks.append({"type": "paragraph", "text": record["text"],
                           "role": "caption",
                           "caption_kind": record["caption_kind"]})
        else:
            blocks.append({"type": "paragraph", "text": record["text"]})
    for table in inventory["tables"]:
        caption = table.get("caption")
        if caption:
            blocks.append({"type": "paragraph", "text": caption,
                           "role": "caption", "caption_kind": "table"})
        rows = table["rows"] or []
        if rows:
            blocks.append({"type": "table", "header": rows[0],
                           "rows": rows[1:], "caption": None})
    return {"title": None, "blocks": blocks, "warnings": warnings,
            "text_changes": [], "stats": inventory["counts"]}


def read_source_blocks(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_to_blocks(path)
    if suffix == ".tex":
        return tex_to_blocks(read_text(path))
    return parse_markdown(read_text(path), path.parent)


# ---------------------------------------------------------------------------
# Minimal package skeleton (used when no template is available)
# ---------------------------------------------------------------------------
def _xml_bytes(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _new_rels():
    root = ET.Element(f"{{{PKG_REL_NS}}}Relationships")
    return root


def _add_rel(root, rid, rel_type, target):
    rel = ET.SubElement(root, f"{{{PKG_REL_NS}}}Relationship")
    rel.set("Id", rid)
    rel.set("Type", rel_type)
    rel.set("Target", target)
    return rel


def _new_content_types():
    root = ET.Element(f"{{{CT_NS}}}Types")
    for extension, content_type in (
            ("rels", "application/vnd.openxmlformats-package.relationships+xml"),
            ("xml", "application/xml"), ("png", "image/png"),
            ("jpg", "image/jpeg"), ("jpeg", "image/jpeg"),
            ("gif", "image/gif")):
        item = ET.SubElement(root, f"{{{CT_NS}}}Default")
        item.set("Extension", extension)
        item.set("ContentType", content_type)
    for part, content_type in (
            ("/word/document.xml",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
            ("/word/styles.xml",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"),
            ("/word/theme/theme1.xml",
             "application/vnd.openxmlformats-officedocument.theme+xml"),
            ("/docProps/core.xml",
             "application/vnd.openxmlformats-package.core-properties+xml"),
            ("/docProps/app.xml",
             "application/vnd.openxmlformats-officedocument.extended-properties+xml")):
        item = ET.SubElement(root, f"{{{CT_NS}}}Override")
        item.set("PartName", part)
        item.set("ContentType", content_type)
    return root


def theme_xml(spec):
    root = ET.Element(f"{{{A_NS}}}theme", {"name": "Office Theme"})
    elements = ET.SubElement(root, f"{{{A_NS}}}themeElements")
    colors = ET.SubElement(elements, f"{{{A_NS}}}clrScheme", {"name": "Office"})
    for name, value in (("dk1", "000000"), ("lt1", "FFFFFF"),
                        ("dk2", "44546A"), ("lt2", "E7E6E6"),
                        ("accent1", "4472C4"), ("accent2", "ED7D31"),
                        ("accent3", "A5A5A5"), ("accent4", "FFC000"),
                        ("accent5", "5B9BD5"), ("accent6", "70AD47"),
                        ("hlink", "0563C1"), ("folHlink", "954F72")):
        node = ET.SubElement(colors, f"{{{A_NS}}}{name}")
        ET.SubElement(node, f"{{{A_NS}}}srgbClr", {"val": value})
    fonts = ET.SubElement(elements, f"{{{A_NS}}}fontScheme", {"name": "Office"})
    body_font = spec.get("fonts", {}).get("body_ascii") or "Liberation Serif"
    heading_font = spec.get("fonts", {}).get("heading_ascii") or body_font
    east_asia = spec.get("fonts", {}).get("body_east_asia") or body_font
    major = ET.SubElement(fonts, f"{{{A_NS}}}majorFont")
    ET.SubElement(major, f"{{{A_NS}}}latin", {"typeface": heading_font})
    ET.SubElement(major, f"{{{A_NS}}}ea", {"typeface": east_asia})
    ET.SubElement(major, f"{{{A_NS}}}cs", {"typeface": ""})
    minor = ET.SubElement(fonts, f"{{{A_NS}}}minorFont")
    ET.SubElement(minor, f"{{{A_NS}}}latin", {"typeface": body_font})
    ET.SubElement(minor, f"{{{A_NS}}}ea", {"typeface": east_asia})
    ET.SubElement(minor, f"{{{A_NS}}}cs", {"typeface": ""})
    fmt = ET.SubElement(elements, f"{{{A_NS}}}fmtScheme", {"name": "Office"})
    for list_name, color in (("fillStyleLst", "4472C4"),
                             ("lnStyleLst", "000000"),
                             ("effectStyleLst", "000000"),
                             ("bgFillStyleLst", "FFFFFF")):
        node = ET.SubElement(fmt, f"{{{A_NS}}}{list_name}")
        for _ in range(3):
            if list_name == "lnStyleLst":
                line = ET.SubElement(node, f"{{{A_NS}}}ln", {"w": "6350", "cap": "flat",
                                                             "cmpd": "sng", "algn": "ctr"})
                fill = ET.SubElement(line, f"{{{A_NS}}}solidFill")
                ET.SubElement(fill, f"{{{A_NS}}}srgbClr", {"val": color})
            elif list_name == "effectStyleLst":
                effect = ET.SubElement(node, f"{{{A_NS}}}effectStyle")
                ET.SubElement(effect, f"{{{A_NS}}}effectLst")
            else:
                fill = ET.SubElement(node, f"{{{A_NS}}}solidFill")
                ET.SubElement(fill, f"{{{A_NS}}}srgbClr", {"val": color})
    return root


def _rpr_xml(parent, role, spec_fonts, spec):
    rpr = ET.SubElement(parent, w("rPr"))
    fonts = ET.SubElement(rpr, w("rFonts"))
    font = role.get("font") or spec_fonts.get("body_ascii") or "Liberation Serif"
    east_asia = role.get("font_east_asia") or spec_fonts.get("body_east_asia") or font
    fonts.set(w("ascii"), font)
    fonts.set(w("hAnsi"), font)
    fonts.set(w("cs"), font)
    fonts.set(w("eastAsia"), east_asia)
    if role.get("size_pt"):
        for tag in ("sz", "szCs"):
            node = ET.SubElement(rpr, w(tag))
            node.set(w("val"), str(int(round(float(role["size_pt"]) * 2))))
    if role.get("bold") is not None:
        node = ET.SubElement(rpr, w("b"))
        if not role["bold"]:
            node.set(w("val"), "0")
    if role.get("italic"):
        ET.SubElement(rpr, w("i"))
    if role.get("color"):
        ET.SubElement(rpr, w("color"), {"{%s}val" % W_NS: role["color"]})
    reorder_properties(rpr)
    return rpr


def _ppr_xml(parent, role, spec):
    ppr = ET.SubElement(parent, w("pPr"))
    if role.get("style_id") and local_name(parent.tag) != "style":
        ET.SubElement(ppr, w("pStyle"), {w("val"): role["style_id"]})
    if role.get("keep_next") or role.get("role") in ("heading", "title"):
        ET.SubElement(ppr, w("keepNext"))
    if role.get("page_break_before"):
        ET.SubElement(ppr, w("pageBreakBefore"))
    spacing = ET.SubElement(ppr, w("spacing"))
    if role.get("space_before_pt") is not None:
        spacing.set(w("before"), str(int(round(float(role["space_before_pt"]) * 20))))
    if role.get("space_after_pt") is not None:
        spacing.set(w("after"), str(int(round(float(role["space_after_pt"]) * 20))))
    if role.get("line_spacing") is not None:
        spacing.set(w("line"), str(int(round(float(role["line_spacing"]) * 240))))
        spacing.set(w("lineRule"), "auto")
    elif role.get("line_spacing_pt") is not None:
        spacing.set(w("line"), str(int(round(float(role["line_spacing_pt"]) * 20))))
        spacing.set(w("lineRule"), role.get("line_rule") or "exact")
    if any(role.get(key) is not None for key in
           ("left_indent_cm", "first_line_indent_cm", "hanging_indent_cm",
            "right_indent_cm")):
        ind = ET.SubElement(ppr, w("ind"))
        for key, attr in (("left_indent_cm", "left"), ("right_indent_cm", "right"),
                          ("first_line_indent_cm", "firstLine"),
                          ("hanging_indent_cm", "hanging")):
            if role.get(key) is not None:
                ind.set(w(attr), str(int(round(float(role[key]) / TWIPS_CM))))
    if role.get("align"):
        ET.SubElement(ppr, w("jc"), {w("val"): role["align"]})
    if role.get("outline_level") is not None:
        ET.SubElement(ppr, w("outlineLvl"),
                      {w("val"): str(max(0, int(role["outline_level"]) - 1))})
    reorder_properties(ppr)
    return ppr


def styles_xml(spec):
    root = ET.Element(w("styles"))
    defaults = ET.SubElement(root, w("docDefaults"))
    rpr_default = ET.SubElement(defaults, w("rPrDefault"))
    _rpr_xml(rpr_default, {"font": spec.get("fonts", {}).get("body_ascii"),
                           "font_east_asia": spec.get("fonts", {}).get("body_east_asia"),
                           "size_pt": spec.get("fonts", {}).get("body_size_pt")},
             spec.get("fonts", {}), spec)
    ppr_default = ET.SubElement(defaults, w("pPrDefault"))
    _ppr_xml(ppr_default, {"line_spacing": spec.get("fonts", {}).get("body_line_spacing")},
             spec)
    is_default = {"style_id": "Normal", "name": "Normal", "type": "paragraph",
                  "default": True}
    for role_name, role in _iter_roles(spec):
        style_id = role.get("style_id") or _default_style_id(role_name)
        style = ET.SubElement(root, w("style"))
        for key, value in (("type", "paragraph"), ("styleId", style_id)):
            style.set(w(key), value)
        if role_name == "body":
            style.set(w("default"), "1")
        name = ET.SubElement(style, w("name"), {w("val"): role.get("style_name")
                                                 or _default_style_name(role_name)})
        if role_name != "body":
            ET.SubElement(style, w("basedOn"), {w("val"): "Normal"})
        if role_name != "body":
            ET.SubElement(style, w("qFormat"))
        _ppr_xml(style, role, spec)
        _rpr_xml(style, role, spec.get("fonts", {}), spec)
    reorder_properties(root)
    return root


def _default_style_id(role_name):
    if role_name == "title":
        return "Title"
    if role_name.startswith("heading_"):
        return "Heading" + role_name.split("_")[1]
    if role_name == "body":
        return "Normal"
    if role_name == "caption_figure":
        return "Caption"
    if role_name == "caption_table":
        return "TableCaption"
    if role_name == "reference":
        return "Bibliography"
    if role_name == "abstract":
        return "Abstract"
    if role_name == "keywords":
        return "Keywords"
    return role_name.title().replace("_", "")


def _default_style_name(role_name):
    names = {"title": "Title", "body": "Normal", "caption_figure": "Caption",
             "caption_table": "Table Caption", "reference": "Bibliography",
             "abstract": "Abstract", "keywords": "Keywords"}
    if role_name.startswith("heading_"):
        return "heading " + role_name.split("_")[1]
    return names.get(role_name, role_name)


def _iter_roles(spec):
    for role_name in ("body", "title", "heading_1", "heading_2", "heading_3",
                      "heading_4", "heading_5", "heading_6", "caption_figure",
                      "caption_table", "reference", "abstract", "keywords"):
        role = (spec.get("styles") or {}).get(role_name)
        if role:
            yield role_name, role


# ---------------------------------------------------------------------------
# DOCX construction
# ---------------------------------------------------------------------------
SECT_ORDER = ["footnotePr", "endnotePr", "type", "pgSz", "pgMar", "paperSrc",
              "pgBorders", "lnNumType", "pgNumType", "cols", "formProt",
              "vAlign", "noEndnote", "titlePg", "textDirection", "bidi",
              "rtlGutter", "docGrid", "printerSettings", "sectPrChange"]


def _copy_element(element):
    return ET.fromstring(ET.tostring(element, encoding="utf-8"))


def _insert_sect_child(sect, child):
    name = local_name(child.tag)
    index = SECT_ORDER.index(name) if name in SECT_ORDER else len(SECT_ORDER)
    position = len(sect)
    for probe, existing in enumerate(list(sect)):
        existing_name = local_name(existing.tag)
        existing_index = SECT_ORDER.index(existing_name) \
            if existing_name in SECT_ORDER else len(SECT_ORDER)
        if existing_index > index:
            position = probe
            break
    sect.insert(position, child)


def apply_page_to_sect(sect, spec):
    page = spec.get("page") or {}
    margins = page.get("margins_cm") or {}
    columns = page.get("columns") or {}
    pg_size = sect.find(w("pgSz"))
    if pg_size is None:
        pg_size = ET.Element(w("pgSz"))
        _insert_sect_child(sect, pg_size)
    width = page.get("width_twips")
    height = page.get("height_twips")
    if not width and page.get("width_cm"):
        width = int(round(float(page["width_cm"]) / TWIPS_CM))
    if not height and page.get("height_cm"):
        height = int(round(float(page["height_cm"]) / TWIPS_CM))
    set_w_attr(pg_size, "w", width)
    set_w_attr(pg_size, "h", height)
    if page.get("orientation") == "landscape":
        pg_size.set(w("orient"), "landscape")
    else:
        pg_size.attrib.pop(w("orient"), None)
    pg_mar = sect.find(w("pgMar"))
    if pg_mar is None:
        pg_mar = ET.Element(w("pgMar"))
        _insert_sect_child(sect, pg_mar)
    for key, attr in (("top", "top"), ("right", "right"), ("bottom", "bottom"),
                      ("left", "left"), ("header", "header"), ("footer", "footer"),
                      ("gutter", "gutter")):
        value = margins.get(key)
        if value is not None:
            pg_mar.set(w(attr), str(int(round(float(value) / TWIPS_CM))))
    cols = sect.find(w("cols"))
    if cols is None and columns.get("count"):
        cols = ET.Element(w("cols"))
        _insert_sect_child(sect, cols)
    if cols is not None:
        if columns.get("count"):
            cols.set(w("num"), str(int(columns["count"])))
        if columns.get("space_cm") is not None:
            cols.set(w("space"), str(int(round(float(columns["space_cm"]) / TWIPS_CM))))
    return sect


def _new_sect(spec):
    sect = ET.Element(w("sectPr"))
    apply_page_to_sect(sect, spec)
    return sect


def _new_document(spec):
    document = ET.Element(w("document"))
    body = ET.SubElement(document, w("body"))
    body.append(_new_sect(spec))
    return document, body


def _append_text_run(paragraph, text, role, spec):
    run = ET.SubElement(paragraph, w("r"))
    _rpr_xml(run, role, spec.get("fonts", {}), spec)
    node = ET.SubElement(run, w("t"))
    node.text = text
    node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _paragraph_element(text, role, spec):
    paragraph = ET.Element(w("p"))
    _ppr_xml(paragraph, role, spec)
    _append_text_run(paragraph, text, role, spec)
    return paragraph


def _lookup_role(spec, name):
    role = (spec.get("styles") or {}).get(name)
    if role:
        return dict(role)
    fallback = {"style_id": _default_style_id(name),
                "style_name": _default_style_name(name), "font": None,
                "size_pt": spec.get("fonts", {}).get("body_size_pt"),
                "align": None, "keep_next": False}
    if name.startswith("heading_"):
        fallback.update({"bold": True, "outline_level": int(name.split("_")[1])})
    if name.startswith("caption"):
        fallback.update({"size_pt": spec.get("fonts", {}).get("caption_size_pt"),
                         "italic": False, "align": "left"})
    if name == "reference":
        fallback.update({"hanging_indent_cm":
                         (spec.get("references") or {}).get("hanging_indent_cm"),
                         "size_pt": (spec.get("references") or {}).get("size_pt")})
    return fallback


def _image_dimensions(data, suffix):
    try:
        if suffix == ".png" and data[:8] == b"\x89PNG\r\n\x1a\n":
            import struct
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if suffix in (".jpg", ".jpeg") and data[:2] == b"\xff\xd8":
            index = 2
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height = int.from_bytes(data[index + 5:index + 7], "big")
                    width = int.from_bytes(data[index + 7:index + 9], "big")
                    return max(width, 1), max(height, 1)
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    index += 2
                    continue
                length = int.from_bytes(data[index + 2:index + 4], "big")
                index += 2 + length
        if suffix == ".gif" and data[:3] == b"GIF":
            return (int.from_bytes(data[6:8], "little"),
                    int.from_bytes(data[8:10], "little"))
    except (ValueError, IndexError):
        pass
    return 800, 600


def _add_image(parts, rels_root, image_path, index, spec):
    path = Path(image_path)
    suffix = path.suffix.lower()
    data = path.read_bytes()
    member = f"media/image{index}{suffix}"
    parts[f"word/{member}"] = data
    rid = f"rIdImage{index}"
    _add_rel(rels_root, rid, R_NS + "/image", member)
    width_px, height_px = _image_dimensions(data, suffix)
    page = spec.get("page") or {}
    margins = page.get("margins_cm") or {}
    text_width = (page.get("width_cm") or 21.0) - \
        (margins.get("left") or 2.54) - (margins.get("right") or 2.54)
    max_width = max(6.0, min(16.0, text_width))
    width_cm = min(max_width, width_px / 96.0 * 2.54)
    height_cm = width_cm * height_px / float(width_px or 1)
    return rid, int(width_cm * EMU_CM), int(height_cm * EMU_CM)


def _figure_paragraph(rid, cx, cy, index, caption_role, spec):
    paragraph = ET.Element(w("p"))
    role = dict(caption_role)
    role["align"] = "center"
    role["style_id"] = None
    role["keep_next"] = True
    _ppr_xml(paragraph, role, spec)
    run = ET.SubElement(paragraph, w("r"))
    _rpr_xml(run, {"font": None, "size_pt": None}, spec.get("fonts", {}), spec)
    drawing = ET.SubElement(run, w("drawing"))
    inline = ET.SubElement(drawing, f"{{{WP_NS}}}inline",
                           {"distT": "0", "distB": "0", "distL": "0", "distR": "0"})
    ET.SubElement(inline, f"{{{WP_NS}}}extent", {"cx": str(cx), "cy": str(cy)})
    ET.SubElement(inline, f"{{{WP_NS}}}docPr",
                  {"id": str(index), "name": f"Picture {index}"})
    graphic = ET.SubElement(inline, f"{{{A_NS}}}graphic")
    data = ET.SubElement(graphic, f"{{{A_NS}}}graphicData",
                         {"uri": "http://schemas.openxmlformats.org/drawingml/2006/picture"})
    picture = ET.SubElement(data, f"{{{PIC_NS}}}pic")
    nv = ET.SubElement(picture, f"{{{PIC_NS}}}nvPicPr")
    ET.SubElement(nv, f"{{{PIC_NS}}}cNvPr", {"id": str(index),
                                             "name": f"Picture {index}"})
    ET.SubElement(nv, f"{{{PIC_NS}}}cNvPicPr")
    fill = ET.SubElement(picture, f"{{{PIC_NS}}}blipFill")
    ET.SubElement(fill, f"{{{A_NS}}}blip", {f"{{{R_NS}}}embed": rid})
    stretch = ET.SubElement(fill, f"{{{A_NS}}}stretch")
    ET.SubElement(stretch, f"{{{A_NS}}}fillRect")
    shape = ET.SubElement(picture, f"{{{PIC_NS}}}spPr")
    transform = ET.SubElement(shape, f"{{{A_NS}}}xfrm")
    ET.SubElement(transform, f"{{{A_NS}}}off", {"x": "0", "y": "0"})
    ET.SubElement(transform, f"{{{A_NS}}}ext", {"cx": str(cx), "cy": str(cy)})
    geometry = ET.SubElement(shape, f"{{{A_NS}}}prstGeom", {"prst": "rect"})
    ET.SubElement(geometry, f"{{{A_NS}}}avLst")
    return paragraph


def _table_element(block, spec):
    rows = []
    if block.get("header"):
        rows.append(block["header"])
    rows.extend(block.get("rows") or [])
    if not rows:
        return None
    table = ET.Element(w("tbl"))
    table_props = ET.SubElement(table, w("tblPr"))
    width = ET.SubElement(table_props, w("tblW"))
    width.set(w("type"), "auto")
    width.set(w("w"), "0")
    borders = ET.SubElement(table_props, w("tblBorders"))
    three_line = (spec.get("table") or {}).get("style") == "three-line"
    for edge, size in (("top", 8), ("bottom", 8), ("left", 0), ("right", 0),
                       ("insideH", 0 if three_line else 4),
                       ("insideV", 0 if three_line else 4)):
        border = ET.SubElement(borders, w(edge))
        border.set(w("val"), "single" if size else "nil")
        if size:
            border.set(w("sz"), str(size))
            border.set(w("space"), "0")
            border.set(w("color"), "000000")
    grid = ET.SubElement(table, w("tblGrid"))
    for _ in range(max(len(row) for row in rows)):
        ET.SubElement(grid, w("gridCol"))
    body_role = _lookup_role(spec, "body")
    body_role.update({"first_line_indent_cm": None, "hanging_indent_cm": None,
                      "line_spacing": 1.0, "space_before_pt": 0,
                      "space_after_pt": 0})
    for row_index, row in enumerate(rows):
        tr = ET.SubElement(table, w("tr"))
        for cell_text in row:
            tc = ET.SubElement(tr, w("tc"))
            tc_props = ET.SubElement(tc, w("tcPr"))
            cell_width = ET.SubElement(tc_props, w("tcW"))
            cell_width.set(w("type"), "auto")
            cell_width.set(w("w"), "0")
            if three_line and row_index == 0:
                cell_borders = ET.SubElement(tc_props, w("tcBorders"))
                bottom = ET.SubElement(cell_borders, w("bottom"))
                bottom.set(w("val"), "single")
                bottom.set(w("sz"), "6")
                bottom.set(w("space"), "0")
                bottom.set(w("color"), "000000")
            role = dict(body_role)
            if row_index == 0:
                role["bold"] = True
            tc.append(_paragraph_element(clean_space(cell_text), role, spec))
    return table


def renumber_captions(blocks, spec):
    """Assign sequential caption numbers and remap in-text cross references."""
    captions = {"figure": [], "table": []}
    for block in blocks:
        kind = None
        if block.get("type") == "paragraph":
            kind = block.get("caption_kind")
            if not kind:
                kind, _ = caption_kind(block.get("text", ""))
                if kind:
                    block["caption_kind"] = kind
                    block["role"] = "caption"
        elif block.get("type") in ("figure", "table") and block.get("caption"):
            kind, _ = caption_kind(block["caption"])
        if kind:
            captions[kind].append(block)
    mapping = {"figure": {}, "table": {}}
    warnings = []
    labels = (spec.get("captions") or {}).get("labels") or {}
    for kind, items in captions.items():
        old_numbers = []
        for item in items:
            text = item.get("caption") or item.get("text") or ""
            _, old = caption_kind(text)
            old_numbers.append(old)
        if len(set(old_numbers)) != len(old_numbers):
            warnings.append(f"{kind} captions have duplicate numbers; renumbering "
                            f"is skipped to avoid breaking cross references")
            continue
        for number, item in enumerate(items, 1):
            old = old_numbers[number - 1]
            text = item.get("caption") or item.get("text") or ""
            kind_actual, _ = caption_kind(text)
            label = labels.get(kind_actual) or (
                "Figure" if kind_actual == "figure" else "Table")
            new_text = re.sub(
                r"^(figure|fig\.?|table|图|表)\s*[0-9]+[A-Za-z]?",
                f"{label} {number}", text, count=1, flags=re.IGNORECASE)
            if item.get("caption") is not None:
                item["caption"] = new_text
            else:
                item["text"] = new_text
            if old:
                mapping[kind][old] = str(number)
    if mapping["figure"] or mapping["table"]:
        for block in blocks:
            if block.get("role") == "caption":
                continue
            text = block.get("text")
            if not text:
                continue
            updated = text
            for kind, table in mapping.items():
                if not table:
                    continue
                prefix = r"fig(?:ure)?\.?" if kind == "figure" else "table"
                for old, new in table.items():
                    if old == new:
                        continue
                    updated = re.sub(
                        rf"\b({prefix})\s*{re.escape(old)}\b",
                        lambda m: f"{m.group(1)} {new}", updated,
                        flags=re.IGNORECASE)
            block["text"] = updated
    return mapping, warnings


def build_docx(blocks, spec, out_path, template=None, title=None):
    """Build (or rebuild) a DOCX using the template package as skeleton."""
    warnings = []
    mapping, renumber_warnings = renumber_captions(blocks, spec)
    warnings.extend(renumber_warnings)
    parts = {}
    if template and Path(template).is_file():
        with zipfile.ZipFile(template) as zf:
            parts = {name: zf.read(name) for name in zf.namelist()}
        if "word/document.xml" not in parts:
            warnings.append("template has no word/document.xml; using a new package")
            parts = {}
    if not parts:
        parts = minimal_parts(spec)
    _ensure_package_parts(parts, spec)
    styles_root = ET.fromstring(parts["word/styles.xml"])
    _update_styles_root(styles_root, spec)
    parts["word/styles.xml"] = _xml_bytes(styles_root)
    _update_theme_fonts(parts, spec)
    document = ET.fromstring(parts["word/document.xml"])
    body = document.find(w("body"))
    if body is None:
        body = ET.SubElement(document, w("body"))
    sections = [sect for sect in document.iter(w("sectPr"))]
    if len(sections) > 1:
        warnings.append(f"template has {len(sections)} sections; the final sectPr "
                        f"is kept and earlier section breaks are not reproduced")
    sect = _copy_element(sections[-1]) if sections else _new_sect(spec)
    apply_page_to_sect(sect, spec)
    rels_root = _document_rels(parts)
    new_body = ET.Element(w("body"))
    if title:
        new_body.append(_paragraph_element(clean_space(title),
                                           _lookup_role(spec, "title"), spec))
    caption_counts = {"figure": 0, "table": 0}
    reference_mode = False
    abstract_mode = False
    keywords_mode = False
    first_content = True
    image_index = 0
    figure_role = _lookup_role(spec, "caption_figure")
    table_role = _lookup_role(spec, "caption_table")
    caption_position = spec.get("captions") or {}
    body_role = _lookup_role(spec, "body")
    for block in blocks:
        kind = block.get("type")
        if kind == "pagebreak":
            paragraph = _paragraph_element("", body_role, spec)
            run = paragraph.find(w("r"))
            if run is not None:
                ET.SubElement(run, w("br"), {w("type"): "page"})
            new_body.append(paragraph)
            continue
        if kind == "heading":
            text = block.get("text", "")
            level = int(block.get("level") or 1)
            role = _lookup_role(spec, f"heading_{min(level, 6)}")
            new_body.append(_paragraph_element(text, role, spec))
            reference_mode = bool(re.match(
                r"^(references?|bibliography|works cited|参考文献)\s*[:：]?$",
                clean_space(text), re.IGNORECASE))
            abstract_mode = bool(re.match(r"^(abstract|摘要)\s*[:：]?$",
                                          clean_space(text), re.IGNORECASE))
            keywords_mode = bool(re.match(r"^(keywords?|关键词)\s*[:：]?$",
                                          clean_space(text), re.IGNORECASE))
            if text and title is None and first_content:
                title = text
            first_content = False
            continue
        if kind == "figure":
            image_index += 1
            image_path = block.get("path")
            if image_path and Path(image_path).is_file():
                rid, cx, cy = _add_image(parts, rels_root, image_path,
                                         image_index, spec)
                new_body.append(_figure_paragraph(rid, cx, cy, image_index,
                                                  figure_role, spec))
            else:
                new_body.append(_paragraph_element(
                    f"[image unavailable: {image_path or 'no path'}]",
                    body_role, spec))
            if block.get("caption"):
                caption_counts["figure"] += 1
                new_body.append(_paragraph_element(block["caption"],
                                                   figure_role, spec))
            first_content = False
            continue
        if kind == "table":
            table = _table_element(block, spec)
            caption_text = block.get("caption")
            above = (caption_position.get("table_position") or "above") == "above"
            if caption_text and above:
                new_body.append(_paragraph_element(caption_text, table_role, spec))
            if table is not None:
                new_body.append(table)
            if caption_text and not above:
                new_body.append(_paragraph_element(caption_text, table_role, spec))
            if caption_text:
                caption_counts["table"] += 1
            first_content = False
            continue
        if kind in ("bullets", "numbers"):
            role = dict(body_role)
            role.update({"first_line_indent_cm": None, "left_indent_cm": 0.74})
            for offset, item in enumerate(block.get("items") or [], 1):
                prefix = "- " if kind == "bullets" else f"{offset}. "
                new_body.append(_paragraph_element(prefix + item, role, spec))
            first_content = False
            continue
        if kind == "quote":
            role = dict(body_role)
            role.update({"left_indent_cm": 1.0, "right_indent_cm": 1.0})
            new_body.append(_paragraph_element(block.get("text", ""), role, spec))
            first_content = False
            continue
        text = block.get("text", "")
        if not text:
            continue
        if block.get("role") == "caption" or block.get("caption_kind"):
            cap_kind = block.get("caption_kind") or caption_kind(text)[0] or "figure"
            role = figure_role if cap_kind == "figure" else table_role
            new_body.append(_paragraph_element(text, role, spec))
            caption_counts[cap_kind] += 1
        elif reference_mode:
            new_body.append(_paragraph_element(text, _lookup_role(spec, "reference"), spec))
        elif abstract_mode:
            new_body.append(_paragraph_element(text, _lookup_role(spec, "abstract"), spec))
        elif keywords_mode:
            new_body.append(_paragraph_element(text, _lookup_role(spec, "keywords"), spec))
        else:
            new_body.append(_paragraph_element(text, body_role, spec))
        first_content = False
    new_body.append(sect)
    for child in list(body):
        body.remove(child)
    for child in list(new_body):
        body.append(child)
    _strip_problem_attributes(document)
    parts["word/document.xml"] = _xml_bytes(document)
    _write_zip(out_path, parts)
    return {"ok": True, "path": str(out_path), "template": str(template) if template else None,
            "blocks": len(blocks), "captions": caption_counts,
            "renumbered": mapping, "warnings": warnings}


def _document_rels(parts):
    name = "word/_rels/document.xml.rels"
    if name in parts:
        try:
            return ET.fromstring(parts[name])
        except ET.ParseError:
            pass
    root = _new_rels()
    parts[name] = _xml_bytes(root)
    return root


def _ensure_package_parts(parts, spec):
    if "[Content_Types].xml" not in parts:
        parts["[Content_Types].xml"] = _xml_bytes(_new_content_types())
    else:
        try:
            ct = ET.fromstring(parts["[Content_Types].xml"])
            existing = {item.get("PartName") for item in ct.findall(f"{{{CT_NS}}}Override")}
            for part_name, content_type in (
                    ("/word/styles.xml",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"),
                    ("/word/theme/theme1.xml",
                     "application/vnd.openxmlformats-officedocument.theme+xml")):
                if part_name not in existing:
                    item = ET.SubElement(ct, f"{{{CT_NS}}}Override")
                    item.set("PartName", part_name)
                    item.set("ContentType", content_type)
            parts["[Content_Types].xml"] = _xml_bytes(ct)
        except ET.ParseError:
            parts["[Content_Types].xml"] = _xml_bytes(_new_content_types())
    if "_rels/.rels" not in parts:
        rels = _new_rels()
        _add_rel(rels, "rId1", R_NS + "/officeDocument", "word/document.xml")
        parts["_rels/.rels"] = _xml_bytes(rels)
    rels_root = _document_rels(parts)
    targets = {rel.get("Target") for rel in rels_root}
    next_id = 1
    existing_ids = {rel.get("Id") for rel in rels_root}
    for part_name, rel_type, target in (
            ("word/styles.xml", R_NS + "/styles", "styles.xml"),
            ("word/theme/theme1.xml", R_NS + "/theme", "theme/theme1.xml")):
        if part_name not in parts:
            parts[part_name] = _xml_bytes(
                styles_xml(spec) if part_name.endswith("styles.xml")
                else theme_xml(spec))
        if target not in targets:
            while f"rId{next_id}" in existing_ids:
                next_id += 1
            rid = f"rId{next_id}"
            _add_rel(rels_root, rid, rel_type, target)
            existing_ids.add(rid)
    parts["word/_rels/document.xml.rels"] = _xml_bytes(rels_root)
    if "word/document.xml" not in parts:
        document, _ = _new_document(spec)
        parts["word/document.xml"] = _xml_bytes(document)


def minimal_parts(spec):
    document, _ = _new_document(spec)
    rels = _new_rels()
    _add_rel(rels, "rId1", R_NS + "/styles", "styles.xml")
    _add_rel(rels, "rId2", R_NS + "/theme", "theme/theme1.xml")
    package_rels = _new_rels()
    _add_rel(package_rels, "rId1", R_NS + "/officeDocument", "word/document.xml")
    _add_rel(package_rels, "rId2",
             "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
             "docProps/core.xml")
    _add_rel(package_rels, "rId3", R_NS + "/extended-properties", "docProps/app.xml")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:creator>format-adapter</dc:creator>'
        '<cp:lastModifiedBy>format-adapter</cp:lastModifiedBy>'
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-01-01T00:00:00Z</dcterms:created>'
        '</cp:coreProperties>'
    ).encode("utf-8")
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Application>format-adapter</Application></Properties>'
    ).encode("utf-8")
    return {
        "[Content_Types].xml": _xml_bytes(_new_content_types()),
        "_rels/.rels": _xml_bytes(package_rels),
        "word/document.xml": _xml_bytes(document),
        "word/styles.xml": _xml_bytes(styles_xml(spec)),
        "word/theme/theme1.xml": _xml_bytes(theme_xml(spec)),
        "word/_rels/document.xml.rels": _xml_bytes(rels),
        "docProps/core.xml": core,
        "docProps/app.xml": app,
    }


def _strip_problem_attributes(root):
    for element in root.iter():
        for name in list(element.attrib):
            if local_name(name) == "Ignorable":
                del element.attrib[name]


def _write_zip(path, parts):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    order = ["[Content_Types].xml", "_rels/.rels"]
    names = order + [name for name in parts if name not in order]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            if name in parts:
                zf.writestr(name, parts[name])


# ---------------------------------------------------------------------------
# Restyle an existing DOCX while keeping its body objects
# ---------------------------------------------------------------------------
def _find_style_element(root, style_id, name):
    for style in root.findall(w("style")):
        if style.get(w("styleId")) == style_id:
            return style
    target = (name or "").lower()
    for style in root.findall(w("style")):
        name_el = style.find(w("name"))
        if name_el is not None and (name_el.get(w("val")) or "").lower() == target:
            return style
    return None


def _create_style_element(root, style_id, name):
    style = ET.SubElement(root, w("style"))
    style.set(w("type"), "paragraph")
    style.set(w("styleId"), style_id)
    ET.SubElement(style, w("name"), {w("val"): name or style_id})
    ET.SubElement(style, w("basedOn"), {w("val"): "Normal"})
    return style


def _replace_style_properties(style, role, spec):
    old_ppr = style.find(w("pPr"))
    preserved_ppr = []
    if old_ppr is not None:
        for tag in ("numPr", "tabs", "sectPr", "widowControl"):
            node = old_ppr.find(w(tag))
            if node is not None:
                preserved_ppr.append(_copy_element(node))
        style.remove(old_ppr)
    old_rpr = style.find(w("rPr"))
    preserved_rpr = []
    if old_rpr is not None:
        for tag in ("rStyle", "lang", "eastAsianLayout"):
            node = old_rpr.find(w(tag))
            if node is not None:
                preserved_rpr.append(_copy_element(node))
        style.remove(old_rpr)
    _ppr_xml(style, role, spec)
    _rpr_xml(style, role, spec.get("fonts", {}), spec)
    ppr = style.find(w("pPr"))
    rpr = style.find(w("rPr"))
    for node in preserved_ppr:
        ppr.append(node)
    for node in preserved_rpr:
        rpr.append(node)


def _update_styles_root(root, spec):
    role_ids = {}
    defaults = root.find(w("docDefaults"))
    if defaults is None:
        defaults = ET.Element(w("docDefaults"))
        root.insert(0, defaults)
    rpr_default = defaults.find(w("rPrDefault"))
    if rpr_default is None:
        rpr_default = ET.SubElement(defaults, w("rPrDefault"))
    old = rpr_default.find(w("rPr"))
    if old is not None:
        rpr_default.remove(old)
    _rpr_xml(rpr_default, {
        "font": (spec.get("fonts") or {}).get("body_ascii"),
        "font_east_asia": (spec.get("fonts") or {}).get("body_east_asia"),
        "size_pt": (spec.get("fonts") or {}).get("body_size_pt")},
        spec.get("fonts", {}), spec)
    ppr_default = defaults.find(w("pPrDefault"))
    if ppr_default is not None:
        old = ppr_default.find(w("pPr"))
        if old is not None:
            ppr_default.remove(old)
        _ppr_xml(ppr_default, {
            "line_spacing": (spec.get("fonts") or {}).get("body_line_spacing")}, spec)
    for role_name, role in _iter_roles(spec):
        style_id = role.get("style_id") or _default_style_id(role_name)
        style_name = role.get("style_name") or _default_style_name(role_name)
        style = _find_style_element(root, style_id, style_name)
        if style is None:
            style = _create_style_element(root, style_id, style_name)
        _replace_style_properties(style, role, spec)
        role_ids[role_name] = style_id
    normal = _find_style_element(root, "Normal", "Normal")
    if normal is None:
        normal = _create_style_element(root, "Normal", "Normal")
        normal.set(w("default"), "1")
    reorder_properties(root)
    return role_ids


def _set_paragraph_style(paragraph, style_id):
    ppr = paragraph.find(w("pPr"))
    if ppr is None:
        ppr = ET.Element(w("pPr"))
        paragraph.insert(0, ppr)
    existing = ppr.find(w("pStyle"))
    if existing is None:
        existing = ET.Element(w("pStyle"))
        ppr.insert(0, existing)
    existing.set(w("val"), style_id)


def _set_spacing(ppr, role):
    spacing = ppr.find(w("spacing"))
    if spacing is None:
        spacing = ET.Element(w("spacing"))
        ppr.append(spacing)
    if role.get("space_before_pt") is not None:
        spacing.set(w("before"), str(int(round(float(role["space_before_pt"]) * 20))))
    if role.get("space_after_pt") is not None:
        spacing.set(w("after"), str(int(round(float(role["space_after_pt"]) * 20))))
    if role.get("line_spacing") is not None:
        spacing.set(w("line"), str(int(round(float(role["line_spacing"]) * 240))))
        spacing.set(w("lineRule"), "auto")
    elif role.get("line_spacing_pt") is not None:
        spacing.set(w("line"), str(int(round(float(role["line_spacing_pt"]) * 20))))
        spacing.set(w("lineRule"), role.get("line_rule") or "exact")


def _set_indent(ppr, role):
    if not any(role.get(key) is not None for key in
               ("left_indent_cm", "right_indent_cm", "first_line_indent_cm",
                "hanging_indent_cm")):
        return
    ind = ppr.find(w("ind"))
    if ind is None:
        ind = ET.Element(w("ind"))
        ppr.append(ind)
    for key, attr in (("left_indent_cm", "left"), ("right_indent_cm", "right"),
                      ("first_line_indent_cm", "firstLine"),
                      ("hanging_indent_cm", "hanging")):
        value = role.get(key)
        if value is not None:
            ind.set(w(attr), str(int(round(float(value) / TWIPS_CM))))
    if role.get("hanging_indent_cm") is not None:
        ind.attrib.pop(w("firstLine"), None)
    if role.get("first_line_indent_cm") is not None:
        ind.attrib.pop(w("hanging"), None)


def _format_runs(paragraph, role):
    for run in paragraph.iter(w("r")):
        rpr = run.find(w("rPr"))
        if rpr is None:
            rpr = ET.Element(w("rPr"))
            run.insert(0, rpr)
        fonts = rpr.find(w("rFonts"))
        if fonts is None:
            fonts = ET.Element(w("rFonts"))
            rpr.insert(0, fonts)
        if role.get("font"):
            for attr in ("ascii", "hAnsi", "cs"):
                fonts.set(w(attr), role["font"])
        if role.get("font_east_asia"):
            fonts.set(w("eastAsia"), role["font_east_asia"])
        if role.get("size_pt"):
            for tag in ("sz", "szCs"):
                node = rpr.find(w(tag))
                if node is None:
                    node = ET.SubElement(rpr, w(tag))
                node.set(w("val"), str(int(round(float(role["size_pt"]) * 2))))
        if role.get("bold") is not None:
            bold = rpr.find(w("b"))
            if role["bold"] and bold is None:
                ET.SubElement(rpr, w("b"))
            elif not role["bold"] and bold is not None:
                rpr.remove(bold)
        if role.get("italic") is not None:
            italic = rpr.find(w("i"))
            if role["italic"] and italic is None:
                ET.SubElement(rpr, w("i"))
            elif not role["italic"] and italic is not None:
                rpr.remove(italic)
        reorder_properties(rpr)


def _apply_role_to_paragraph(paragraph, role, spec):
    ppr = paragraph.find(w("pPr"))
    if ppr is None:
        ppr = ET.Element(w("pPr"))
        paragraph.insert(0, ppr)
    if role.get("style_id"):
        _set_paragraph_style(paragraph, role["style_id"])
    _set_spacing(ppr, role)
    _set_indent(ppr, role)
    if role.get("align"):
        jc = ppr.find(w("jc"))
        if jc is None:
            jc = ET.SubElement(ppr, w("jc"))
        jc.set(w("val"), role["align"])
    if role.get("outline_level") is not None:
        outline = ppr.find(w("outlineLvl"))
        if outline is None:
            outline = ET.SubElement(ppr, w("outlineLvl"))
        outline.set(w("val"), str(max(0, int(role["outline_level"]) - 1)))
    if role.get("keep_next"):
        if ppr.find(w("keepNext")) is None:
            ET.SubElement(ppr, w("keepNext"))
    _format_runs(paragraph, role)
    reorder_properties(ppr)


def _heading_level_from_text(text):
    stripped = clean_space(text)
    if not stripped or len(stripped) > 120:
        return None
    if re.match(r"^(abstract|摘要|keywords?|关键词|references?|bibliography|"
                r"参考文献|acknowledg|致谢|appendix|附录)\b", stripped, re.IGNORECASE):
        return 1
    match = re.match(r"^(\d+(?:\.\d+){0,4})[\s.、]+[^\d\s]", stripped)
    if match:
        return min(6, match.group(1).count(".") + 1)
    if re.match(r"^第[一二三四五六七八九十0-9]+[章节部分]", stripped):
        return 1
    if re.match(r"^[一二三四五六七八九十]+[、.．]", stripped) and len(stripped) <= 60:
        return 1
    return None


def _replace_in_text_nodes(paragraph, pattern, replacement):
    count = 0
    for node in paragraph.iter(w("t")):
        if not node.text:
            continue
        updated, number = re.subn(pattern, replacement, node.text,
                                  flags=re.IGNORECASE)
        if number:
            node.text = updated
            count += number
    return count


def _renumber_docx_captions(body_paragraphs):
    captions = {"figure": [], "table": []}
    for paragraph in body_paragraphs:
        text = element_text(paragraph)
        kind, number = caption_kind(text)
        if kind and number:
            captions[kind].append((paragraph, text, number))
    mapping = {"figure": {}, "table": {}}
    warnings = []
    for kind, items in captions.items():
        old_numbers = [item[2] for item in items]
        if len(set(old_numbers)) != len(old_numbers):
            warnings.append(f"{kind} caption numbers are duplicated; "
                            f"renumbering skipped to protect cross references")
            continue
        for new_number, (paragraph, text, old_number) in enumerate(items, 1):
            replacement = f"{'Figure' if kind == 'figure' else 'Table'} {new_number}"
            _replace_in_text_nodes(
                paragraph, r"^(figure|fig\.?|table|图|表)\s*[0-9]+[A-Za-z]?",
                replacement)
            mapping[kind][old_number] = str(new_number)
    crossrefs = 0
    if mapping["figure"] or mapping["table"]:
        caption_ids = {id(item[0]) for items in captions.values() for item in items}
        for paragraph in body_paragraphs:
            if id(paragraph) in caption_ids:
                continue
            for kind, table in mapping.items():
                if not table:
                    continue
                prefix = r"fig(?:ure)?\.?" if kind == "figure" else "table"
                for old, new in table.items():
                    if old == new:
                        continue
                    crossrefs += _replace_in_text_nodes(
                        paragraph, rf"\b({prefix})\s*{re.escape(old)}\b",
                        lambda match: f"{match.group(1)} {new}")
    return mapping, crossrefs, warnings


def _child_index(parent, child):
    for index, item in enumerate(parent):
        if item is child:
            return index
    return -1


def _reposition_captions(body, spec):
    """Move table/figure captions to the template's required side."""
    captions = spec.get("captions") or {}
    wanted = {"table": captions.get("table_position") or "above",
              "figure": captions.get("figure_position") or "below"}
    moved = []
    for _ in range(len(list(body)) + 2):
        changed = False
        for child in list(body):
            if child.tag == w("tbl"):
                index = _child_index(body, child)
                if index < 0:
                    continue
                previous = body[index - 1] if index > 0 and \
                    body[index - 1].tag == w("p") else None
                following = body[index + 1] if index + 1 < len(body) and \
                    body[index + 1].tag == w("p") else None
                previous_kind = caption_kind(element_text(previous))[0] \
                    if previous is not None else None
                following_kind = caption_kind(element_text(following))[0] \
                    if following is not None else None
                if wanted["table"] == "above" and following_kind == "table":
                    body.remove(following)
                    body.insert(index, following)
                    moved.append({"kind": "table", "position": "above"})
                    changed = True
                elif wanted["table"] == "below" and previous_kind == "table":
                    body.remove(previous)
                    body.insert(index, previous)
                    moved.append({"kind": "table", "position": "below"})
                    changed = True
            elif child.tag == w("p") and child.find(f".//{{{A_NS}}}blip") is not None:
                index = _child_index(body, child)
                if index < 0:
                    continue
                previous = body[index - 1] if index > 0 and \
                    body[index - 1].tag == w("p") else None
                following = body[index + 1] if index + 1 < len(body) and \
                    body[index + 1].tag == w("p") else None
                previous_kind = caption_kind(element_text(previous))[0] \
                    if previous is not None else None
                following_kind = caption_kind(element_text(following))[0] \
                    if following is not None else None
                if wanted["figure"] == "below" and previous_kind == "figure":
                    body.remove(previous)
                    body.insert(index, previous)
                    moved.append({"kind": "figure", "position": "below"})
                    changed = True
                elif wanted["figure"] == "above" and following_kind == "figure":
                    body.remove(following)
                    body.insert(index, following)
                    moved.append({"kind": "figure", "position": "above"})
                    changed = True
        if not changed:
            break
    return moved


def _update_theme_fonts(parts, spec):
    if "word/theme/theme1.xml" not in parts:
        return
    try:
        root = ET.fromstring(parts["word/theme/theme1.xml"])
    except ET.ParseError:
        return
    scheme = root.find(f".//{{{A_NS}}}fontScheme")
    if scheme is None:
        return
    font_map = {
        "majorFont": ((spec.get("fonts") or {}).get("heading_ascii") or
                      (spec.get("fonts") or {}).get("body_ascii")),
        "minorFont": (spec.get("fonts") or {}).get("body_ascii"),
    }
    ea_font = (spec.get("fonts") or {}).get("body_east_asia")
    for family, latin_font in font_map.items():
        node = scheme.find(f"{{{A_NS}}}{family}")
        if node is None:
            continue
        if latin_font:
            latin = node.find(f"{{{A_NS}}}latin")
            if latin is None:
                latin = ET.SubElement(node, f"{{{A_NS}}}latin")
            latin.set("typeface", latin_font)
        if ea_font:
            ea = node.find(f"{{{A_NS}}}ea")
            if ea is None:
                ea = ET.SubElement(node, f"{{{A_NS}}}ea")
            ea.set("typeface", ea_font)
    parts["word/theme/theme1.xml"] = _xml_bytes(root)


def apply_docx(source_docx, spec, out_path):
    """Restyle an existing DOCX; body text, tables, images and fields survive."""
    source_docx = Path(source_docx)
    with zipfile.ZipFile(source_docx) as zf:
        parts = {name: zf.read(name) for name in zf.namelist()}
    _ensure_package_parts(parts, spec)
    styles_root = ET.fromstring(parts["word/styles.xml"])
    role_ids = _update_styles_root(styles_root, spec)
    parts["word/styles.xml"] = _xml_bytes(styles_root)
    document = ET.fromstring(parts["word/document.xml"])
    body = document.find(w("body"))
    if body is None:
        document, body = _new_document(spec)
    for sect in document.iter(w("sectPr")):
        apply_page_to_sect(sect, spec)
    body_paragraphs = []
    table_paragraphs = set()
    for table in body.iter(w("tbl")):
        for paragraph in table.iter(w("p")):
            table_paragraphs.add(id(paragraph))
    for paragraph in body.iter(w("p")):
        if id(paragraph) not in table_paragraphs:
            body_paragraphs.append(paragraph)
    reference_heading = re.compile(
        r"^(references?|reference list|bibliography|works cited|参考文献)\s*[:：]?$",
        re.IGNORECASE)
    abstract_heading = re.compile(r"^(abstract|摘要)\s*[:：]?$", re.IGNORECASE)
    keywords_heading = re.compile(r"^(keywords?|关键词)\s*[:：]?$", re.IGNORECASE)
    reference_mode = abstract_mode = keywords_mode = False
    applied = {"title": 0, "headings": 0, "body": 0, "captions": 0,
               "references": 0, "abstract": 0, "keywords": 0}
    first_content = True
    for paragraph in body_paragraphs:
        text = element_text(paragraph)
        if not text:
            continue
        sid = ""
        ppr = paragraph.find(w("pPr"))
        if ppr is not None and ppr.find(w("pStyle")) is not None:
            sid = ppr.find(w("pStyle")).get(w("val")) or ""
        style = (styles_root.findall(w("style")))
        level = None
        for style_el in style:
            if style_el.get(w("styleId")) == sid:
                parsed = _parse_ppr(style_el.find(w("pPr")), {})
                level = parsed.get("outline_level")
                break
        if level is None:
            level = _heading_level_from_text(text)
        cap_kind, _ = caption_kind(text)
        if reference_heading.match(text.strip()):
            role = _lookup_role(spec, "heading_1")
            _apply_role_to_paragraph(paragraph, role, spec)
            reference_mode, abstract_mode, keywords_mode = True, False, False
            applied["headings"] += 1
        elif abstract_heading.match(text.strip()):
            role = _lookup_role(spec, "abstract")
            _apply_role_to_paragraph(paragraph, role, spec)
            abstract_mode, reference_mode, keywords_mode = True, False, False
            applied["abstract"] += 1
        elif keywords_heading.match(text.strip()):
            role = _lookup_role(spec, "keywords")
            _apply_role_to_paragraph(paragraph, role, spec)
            keywords_mode, reference_mode, abstract_mode = True, False, False
            applied["keywords"] += 1
        elif cap_kind:
            role = _lookup_role(spec, "caption_figure" if cap_kind == "figure"
                                else "caption_table")
            _apply_role_to_paragraph(paragraph, role, spec)
            applied["captions"] += 1
        elif level:
            role = _lookup_role(spec, f"heading_{min(int(level), 6)}")
            _apply_role_to_paragraph(paragraph, role, spec)
            reference_mode = abstract_mode = keywords_mode = False
            applied["headings"] += 1
        elif first_content and len(text) <= 300:
            role = _lookup_role(spec, "title")
            _apply_role_to_paragraph(paragraph, role, spec)
            applied["title"] += 1
        elif reference_mode:
            _apply_role_to_paragraph(paragraph, _lookup_role(spec, "reference"), spec)
            applied["references"] += 1
        elif abstract_mode:
            _apply_role_to_paragraph(paragraph, _lookup_role(spec, "abstract"), spec)
            applied["abstract"] += 1
        elif keywords_mode:
            _apply_role_to_paragraph(paragraph, _lookup_role(spec, "keywords"), spec)
            applied["keywords"] += 1
        else:
            _apply_role_to_paragraph(paragraph, _lookup_role(spec, "body"), spec)
            applied["body"] += 1
        first_content = False
    repositioned = _reposition_captions(body, spec)
    mapping, crossrefs, warnings = _renumber_docx_captions(body_paragraphs)
    _strip_problem_attributes(document)
    parts["word/document.xml"] = _xml_bytes(document)
    _update_theme_fonts(parts, spec)
    _write_zip(out_path, parts)
    return {"ok": True, "path": str(out_path), "source": str(source_docx),
            "applied": applied, "role_styles": role_ids,
            "renumbered": mapping, "crossrefs": crossrefs,
            "repositioned": repositioned, "warnings": warnings}


# ---------------------------------------------------------------------------
# Read-back check
# ---------------------------------------------------------------------------
def page_name(width, height):
    if not width or not height:
        return None
    known = {"A4": (11906, 16838), "A3": (16838, 23811), "A5": (8391, 11906),
             "B5": (9979, 14175), "Letter": (12240, 15840),
             "Legal": (12240, 20160)}
    for name, (known_w, known_h) in known.items():
        if abs(width - known_w) <= 30 and abs(height - known_h) <= 30:
            return name
        if abs(width - known_h) <= 30 and abs(height - known_w) <= 30:
            return name
    return f"{round(width / 1440.0, 2)}x{round(height / 1440.0, 2)} in"


def reference_style_guess(entries):
    texts = [entry.get("text", "") for entry in entries if entry.get("text")]
    if not texts:
        return "unknown"
    numeric_bracket = sum(1 for text in texts if re.match(r"^\s*\[\d+\]", text))
    numeric_dot = sum(1 for text in texts if re.match(r"^\s*\d+[.)]\s", text))
    author_year = sum(1 for text in texts
                      if re.search(r"\((?:19|20)\d{2}[a-z]?\)", text))
    gbt = sum(1 for text in texts if re.search(r"\[(?:J|M|D|C|P|R|S|N|EB/OL)\]", text))
    total = len(texts)
    if gbt >= max(1, total // 2):
        return "gb-t-7714"
    if numeric_bracket >= max(1, total // 2):
        return "numeric-bracket"
    if numeric_dot >= max(1, total // 2):
        return "numeric"
    if author_year >= max(1, total // 2):
        return "author-year"
    return "template"


def check_docx(path):
    path = Path(path)
    inventory = docx_read(path)
    report = {
        "ok": False, "path": str(path), "error": inventory.get("error"),
        "bytes": path.stat().st_size if path.is_file() else 0,
        "parts": len(inventory.get("parts") or []),
        "paragraphs": inventory.get("counts", {}).get("paragraphs", 0),
        "sections": inventory.get("sections") or [],
        "page": {}, "margins_cm": {}, "columns": {},
        "styles_used": inventory.get("styles_used") or {},
        "fonts_used": inventory.get("fonts_used") or [],
        "sizes_used": inventory.get("sizes_used") or [],
        "counts": inventory.get("counts") or {},
        "captions": inventory.get("captions") or [],
        "references": inventory.get("references") or {},
        "reference_style": reference_style_guess(
            (inventory.get("references") or {}).get("entries") or []),
        "body_text": inventory.get("full_text") or "".join(
            p["text"] for p in inventory.get("paragraphs") or []),
        "paragraph_text": "".join(p["text"] for p in inventory.get("paragraphs") or []),
        "warnings": inventory.get("warnings") or [],
    }
    if inventory.get("sections"):
        section = inventory["sections"][-1]
        report["page"] = dict(section.get("page") or {})
        report["margins_cm"] = dict(section.get("margins") or {})
        report["columns"] = dict(section.get("columns") or {})
        report["page"]["name"] = page_name(report["page"].get("width_twips"),
                                           report["page"].get("height_twips"))
    # Caption positions: table captions use the table record; figure captions
    # are inferred from the nearest image paragraph.
    paragraphs = inventory.get("paragraphs") or []
    figure_caption_positions = {}
    for index, record in enumerate(paragraphs):
        if record.get("caption_kind") != "figure":
            continue
        previous = paragraphs[index - 1] if index > 0 else None
        following = paragraphs[index + 1] if index + 1 < len(paragraphs) else None
        if previous and previous.get("images"):
            figure_caption_positions[record["index"]] = "below"
        elif following and following.get("images"):
            figure_caption_positions[record["index"]] = "above"
    for caption in report["captions"]:
        if caption["kind"] == "figure":
            caption["position"] = figure_caption_positions.get(caption["paragraph"])
    table_positions = {}
    for table in inventory.get("tables") or []:
        if table.get("caption"):
            table_positions[table["caption"]] = table.get("caption_position")
    for caption in report["captions"]:
        if caption["kind"] == "table":
            caption["position"] = table_positions.get(caption["text"])
    report["caption_counts"] = {
        "figure": sum(1 for item in report["captions"] if item["kind"] == "figure"),
        "table": sum(1 for item in report["captions"] if item["kind"] == "table"),
    }
    report["caption_numbers"] = {
        kind: [item["number"] for item in report["captions"] if item["kind"] == kind]
        for kind in ("figure", "table")
    }
    report["ok"] = inventory.get("ok", False) and report["paragraphs"] > 0
    if not report["ok"] and not report["error"]:
        report["error"] = "document has zero paragraphs"
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_json(path):
    return json.loads(read_text(path))


def main():
    parser = argparse.ArgumentParser(description="DOCX read/write/inventory")
    sub = parser.add_subparsers(dest="command", required=True)

    read_cmd = sub.add_parser("read", help="inventory a DOCX")
    read_cmd.add_argument("--docx", required=True)
    read_cmd.add_argument("--out", default=None)

    blocks_cmd = sub.add_parser("blocks", help="read a source into verbatim blocks")
    blocks_cmd.add_argument("--source", required=True)
    blocks_cmd.add_argument("--out", required=True)

    build_cmd = sub.add_parser("build", help="build a DOCX from blocks")
    build_cmd.add_argument("--blocks", required=True)
    build_cmd.add_argument("--spec", required=True)
    build_cmd.add_argument("--out", required=True)
    build_cmd.add_argument("--template", default=None)

    apply_cmd = sub.add_parser("apply", help="restyle an existing DOCX in place")
    apply_cmd.add_argument("--docx", required=True)
    apply_cmd.add_argument("--spec", required=True)
    apply_cmd.add_argument("--out", required=True)

    check_cmd = sub.add_parser("check", help="read back a finished DOCX")
    check_cmd.add_argument("--docx", required=True)

    args = parser.parse_args()
    try:
        if args.command == "read":
            payload = docx_read(args.docx)
            if args.out:
                write_json(args.out, payload)
        elif args.command == "blocks":
            payload = read_source_blocks(args.source)
            write_json(args.out, payload)
        elif args.command == "build":
            blocks = _load_json(args.blocks)
            spec = _load_json(args.spec)
            if isinstance(blocks, dict):
                blocks = blocks.get("blocks") or []
            payload = build_docx(blocks, spec, args.out, args.template)
        elif args.command == "apply":
            spec = _load_json(args.spec)
            payload = apply_docx(args.docx, spec, args.out)
        else:
            payload = check_docx(args.docx)
    except Exception as exc:  # noqa: BLE001
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())




