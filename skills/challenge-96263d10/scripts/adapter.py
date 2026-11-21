#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Template recognition + result assembly for the format-adapter skill.

This module is deliberately standalone: it opens the supplied DOCX template
and reads word/styles.xml, word/document.xml (all sectPr nodes),
word/theme/theme1.xml and word/numbering.xml. A style-guide text is treated as
an *additional* source, never as a replacement for the template.

It also provides input triage, spec merging, requirement checking and the
official writing result fields (deliverable / preserved_facts / evidence_map /
change_log).
"""
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
TWIPS_CM = 2.54 / 1440.0
CAPTION_RE = re.compile(
    r"^(figure|fig\.?|table|图|表)\s*([0-9]+[A-Za-z]?)\s*"
    r"(?:[.:：、]\s*|\s+)?(.*)$", re.IGNORECASE)
REFERENCE_HEADING_RE = re.compile(
    r"^(references?|reference list|bibliography|works cited|参考文献|引用文献)\s*[:：]?$",
    re.IGNORECASE)
ABSTRACT_RE = re.compile(r"^(abstract|摘要)\s*[:：]?$", re.IGNORECASE)
KEYWORDS_RE = re.compile(r"^(keywords?|关键词)\s*[:：]?$", re.IGNORECASE)
TEXT_SUFFIXES = {".md", ".txt", ".tex", ".latex", ".rst", ".org"}
GUIDE_HINTS = ("style-guide", "style_guide", "guideline", "instruction",
               "author-guide", "format", "checklist", "投稿", "格式", "要求",
               "规范", "指南", "说明")
TEMPLATE_HINTS = ("template", "模板", "样式", "word-template", "journal-template")
MANUSCRIPT_HINTS = ("manuscript", "paper", "article", "draft", "submission",
                    "稿件", "论文", "正文", "原稿", "文章")
FONT_KNOWN = {"liberation serif", "liberation sans", "dejavu serif",
              "dejavu sans", "carlito", "caladea", "noto serif", "noto sans",
              "symbol", "serif", "sans-serif", "monospace"}
FONT_WINDOWS = {"times new roman", "arial", "calibri", "cambria", "georgia",
                "simsun", "宋体", "simhei", "黑体", "microsoft yahei", "微软雅黑",
                "kaiti", "楷体", "fangsong", "仿宋", "consolas"}
_FC_FONTS = None


def w(tag):
    return f"{{{W_NS}}}{tag}"


def sha256_file(path, limit=8 * 1024 * 1024):
    path = Path(path)
    if not path.is_file():
        return None
    if path.stat().st_size > limit:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def read_text(path):
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text, fallback="format-case"):
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "")).strip("-")
    return value or fallback


def clean_space(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def run_process(args, cwd=None, timeout=240):
    try:
        process = subprocess.run(
            [str(item) for item in args], cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout)
        return {"ok": process.returncode == 0, "exit_code": process.returncode,
                "stdout": process.stdout[-20000:],
                "stderr": process.stderr[-12000:],
                "command": [str(item) for item in args]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "exit_code": None, "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "command": [str(item) for item in args]}


# ---------------------------------------------------------------------------
# Input triage
# ---------------------------------------------------------------------------
def _docx_stats(path):
    stats = {"words": 0, "paragraphs": 0, "styles": 0, "sections": 0,
             "media": 0, "error": None}
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            stats["media"] = sum(1 for name in names if name.startswith("word/media/"))
            if "word/document.xml" in names:
                root = ET.fromstring(zf.read("word/document.xml"))
                texts = [node.text for node in root.iter(w("t")) if node.text]
                joined = "".join(texts)
                stats["words"] = len(re.findall(
                    r"[A-Za-z0-9]+", joined)) + len(re.findall(r"[一-鿿]", joined))
                stats["paragraphs"] = len(list(root.iter(w("p"))))
                stats["sections"] = len(list(root.iter(w("sectPr"))))
            if "word/styles.xml" in names:
                styles_root = ET.fromstring(zf.read("word/styles.xml"))
                stats["styles"] = len(list(styles_root.findall(w("style"))))
    except (zipfile.BadZipFile, OSError, ET.ParseError) as exc:
        stats["error"] = f"{type(exc).__name__}: {exc}"
    return stats


def scan_input(input_dir):
    root = Path(input_dir)
    report = {"root": str(root), "exists": root.is_dir(), "files": [],
              "templates": [], "manuscripts": [], "guides": [],
              "case": None, "task": None, "bibliography": [],
              "warnings": [], "template": None, "manuscript": None,
              "guide": None}
    if not root.is_dir():
        report["warnings"].append(f"input directory not found: {root}")
        return report
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts or path.suffix.lower() in (".pyc", ".pyo"):
            continue
        suffix = path.suffix.lower()
        name = path.name.lower()
        entry = {"path": str(path), "rel": rel, "name": path.name,
                 "suffix": suffix, "size": path.stat().st_size,
                 "sha256": sha256_file(path), "role": "asset"}
        report["files"].append(entry)
        if name in ("case.json",) or (suffix == ".json" and not report["case"]):
            data = load_json(path)
            if isinstance(data, dict) and ("case_id" in data or "output_contract" in data):
                entry["role"] = "case"
                report["case"] = str(path)
                continue
        if name == "task.md":
            entry["role"] = "task"
            report["task"] = str(path)
            continue
        if suffix in (".bib", ".ris", ".enw"):
            entry["role"] = "bibliography"
            report["bibliography"].append(str(path))
            continue
        is_guide_name = any(hint in name for hint in GUIDE_HINTS)
        is_template_name = any(hint in name for hint in TEMPLATE_HINTS)
        is_manuscript_name = any(hint in name for hint in MANUSCRIPT_HINTS)
        stats = _docx_stats(path) if suffix == ".docx" else None
        if suffix == ".docx":
            template_score = 0
            manuscript_score = 0
            if is_template_name:
                template_score += 6
            if is_guide_name:
                template_score += 2
            if stats and stats["styles"] >= 8 and stats["words"] < 2000:
                template_score += 2
            if is_manuscript_name:
                manuscript_score += 6
            if stats and stats["words"] >= 400:
                manuscript_score += 2
            if stats and stats["words"] > 2000:
                manuscript_score += 2
            entry["stats"] = stats
            if template_score >= manuscript_score and template_score > 0:
                entry["role"] = "template_candidate"
                entry["score"] = template_score
                report["templates"].append(dict(entry))
            elif manuscript_score > 0:
                entry["role"] = "manuscript_candidate"
                entry["score"] = manuscript_score
                report["manuscripts"].append(dict(entry))
            else:
                entry["role"] = "docx_candidate"
                report["manuscripts"].append(dict(entry))
        elif suffix in TEXT_SUFFIXES:
            if is_guide_name:
                entry["role"] = "guide_candidate"
                report["guides"].append(dict(entry))
            elif is_manuscript_name or not is_guide_name:
                entry["role"] = "manuscript_candidate"
                entry["score"] = 4 if is_manuscript_name else 1
                report["manuscripts"].append(dict(entry))
        elif suffix == ".pdf":
            if is_guide_name:
                entry["role"] = "guide_candidate"
                report["guides"].append(dict(entry))
            else:
                entry["role"] = "pdf_candidate"
                report["manuscripts"].append(dict(entry))
    report["templates"].sort(key=lambda item: (-item.get("score", 0),
                                               item.get("size", 0)))
    report["manuscripts"].sort(key=lambda item: (-item.get("score", 0),
                                                 -item.get("size", 0)))
    report["guides"].sort(key=lambda item: (-item.get("score", 0),
                                            item.get("size", 0)))
    if report["templates"]:
        report["template"] = report["templates"][0]["path"]
    if report["manuscripts"]:
        report["manuscript"] = report["manuscripts"][0]["path"]
    if report["guides"]:
        report["guide"] = report["guides"][0]["path"]
        if report["guide"] == report["manuscript"]:
            report["guide"] = None
    if report["template"] and report["template"] == report["manuscript"]:
        report["manuscript"] = None
    if not report["template"] and report["manuscript"]:
        # A single DOCX that contains real prose is the manuscript, not a
        # template, even when no filename hint is present.
        candidate = Path(report["manuscript"])
        if candidate.suffix.lower() == ".docx":
            stats = _docx_stats(candidate)
            if stats["words"] < 300 and stats["styles"] >= 8:
                report["template"] = report["manuscript"]
                report["manuscript"] = None
    if not report["manuscript"]:
        for entry in report["files"]:
            if entry["suffix"] in TEXT_SUFFIXES and entry["role"] == "asset":
                report["manuscript"] = entry["path"]
                break
    if not report["manuscript"]:
        report["warnings"].append("no manuscript candidate found under the input directory")
    if not report["template"]:
        report["warnings"].append("no template.docx candidate found; the guide or "
                                  "built-in defaults will be used and the result may "
                                  "be partial")
    return report


def load_case(input_dir):
    root = Path(input_dir)
    out = {"path": None, "raw": {}, "case_id": None, "output_contract": {},
           "declared_fields": [], "journal": None, "template_id": None,
           "target": None, "task": None}
    candidates = []
    preferred = root / "case.json"
    if preferred.is_file():
        candidates.append(preferred)
    candidates += [p for p in sorted(root.glob("*.json"))
                   if p.is_file() and p != preferred][:8]
    for path in candidates:
        data = load_json(path)
        if not isinstance(data, dict):
            continue
        if path != preferred and "case_id" not in data and "output_contract" not in data:
            continue
        out["path"] = str(path)
        out["raw"] = data
        out["case_id"] = data.get("case_id") or data.get("id")
        contract = data.get("output_contract") or {}
        if isinstance(contract, list):
            contract = {str(key): None for key in contract}
        if not isinstance(contract, dict):
            contract = {}
        out["output_contract"] = contract
        out["declared_fields"] = sorted(str(key) for key in contract)
        out["journal"] = data.get("journal") or data.get("venue")
        out["template_id"] = data.get("template_id") or data.get("template")
        out["target"] = data.get("target")
        out["task"] = data.get("task") or data.get("instruction")
        break
    return out


def ensure_text_file(path, work_dir, label="guide"):
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        return str(path), None
    for candidate in (path.with_suffix(".txt"), path.with_name(path.name + ".txt")):
        if candidate.is_file() and candidate.stat().st_size > 0:
            return str(candidate), None
    tool = shutil.which("pdftotext")
    if not tool:
        return None, f"{label}: PDF needs text extraction but pdftotext is unavailable"
    target = Path(work_dir) / f"{slug(path.stem)}-{label}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    result = run_process([tool, str(path), str(target)], timeout=120)
    if result["ok"] and target.is_file() and target.stat().st_size > 0:
        return str(target), None
    return None, f"{label}: pdftotext failed: {result['stderr'][:300]}"


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
def available_fonts():
    global _FC_FONTS
    if _FC_FONTS is not None:
        return _FC_FONTS
    fonts = set(FONT_KNOWN)
    if os.name == "nt":
        fonts.update(FONT_WINDOWS)
    tool = shutil.which("fc-list")
    if tool:
        result = run_process([tool, ":", "family"], timeout=30)
        if result["ok"]:
            for line in result["stdout"].splitlines():
                for family in line.split(","):
                    value = family.strip().lower()
                    if value:
                        fonts.add(value)
    _FC_FONTS = fonts
    return fonts


def font_available(name):
    value = clean_space(name).lower()
    if not value:
        return True
    return value in available_fonts()


def substitute_font(name, fallback="Liberation Serif"):
    if not name:
        return fallback, None
    if font_available(name):
        return name, None
    if name.strip().lower() == fallback.lower():
        return fallback, None
    return fallback, {"requested": name, "used": fallback,
                      "reason": "requested font is not installed; organizer "
                                "revision 3 supplies Liberation Serif"}


# ---------------------------------------------------------------------------
# Template extraction (styles.xml, document.xml sectPr, theme1.xml)
# ---------------------------------------------------------------------------
def _parse_rpr(element):
    out = {}
    if element is None:
        return out
    fonts = element.find(w("rFonts"))
    if fonts is not None:
        for key in ("ascii", "hAnsi", "eastAsia", "cs"):
            value = fonts.get(w(key))
            if value:
                out[f"font_{key}"] = value
        for key in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
            value = fonts.get(w(key))
            if value:
                out[key] = value
    size = element.find(w("sz"))
    if size is not None:
        try:
            out["size_pt"] = round(int(size.get(w("val"))) / 2.0, 2)
        except (TypeError, ValueError):
            pass
    for key in ("b", "i"):
        node = element.find(w(key))
        if node is not None:
            value = node.get(w("val"))
            out[key] = not (value in ("0", "false", "off"))
    color = element.find(w("color"))
    if color is not None and color.get(w("val")):
        out["color"] = color.get(w("val"))
    return out


def _int_attr(element, name, default=None):
    try:
        return int(element.get(w(name)))
    except (TypeError, ValueError, AttributeError):
        return default


def _parse_ppr(element):
    out = {}
    if element is None:
        return out
    style = element.find(w("pStyle"))
    if style is not None:
        out["pstyle"] = style.get(w("val"))
    outline = element.find(w("outlineLvl"))
    if outline is not None:
        level = _int_attr(outline, "val")
        if level is not None:
            out["outline_level"] = level + 1
    jc = element.find(w("jc"))
    if jc is not None:
        out["align"] = jc.get(w("val"))
    spacing = element.find(w("spacing"))
    if spacing is not None:
        before = _int_attr(spacing, "before")
        after = _int_attr(spacing, "after")
        line = _int_attr(spacing, "line")
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
    ind = element.find(w("ind"))
    if ind is not None:
        for key, out_key in (("left", "left_indent_cm"), ("right", "right_indent_cm"),
                             ("firstLine", "first_line_indent_cm"),
                             ("hanging", "hanging_indent_cm")):
            value = _int_attr(ind, key)
            if value is not None:
                out[out_key] = round(value * TWIPS_CM, 3)
    for key in ("keepNext", "keepLines", "pageBreakBefore"):
        node = element.find(w(key))
        if node is not None:
            value = node.get(w("val"))
            out[key] = not (value in ("0", "false", "off"))
    num = element.find(w("numPr"))
    if num is not None:
        num_id = num.find(w("numId"))
        ilvl = num.find(w("ilvl"))
        if num_id is not None:
            out["num_id"] = num_id.get(w("val"))
        if ilvl is not None:
            out["num_level"] = ilvl.get(w("val"))
    return out


def _parse_styles(zf):
    styles = {"doc_defaults": {}, "records": {}, "by_name": {},
              "default_style_id": None}
    try:
        root = ET.fromstring(zf.read("word/styles.xml"))
    except (KeyError, ET.ParseError):
        return styles
    defaults = root.find(w("docDefaults"))
    if defaults is not None:
        styles["doc_defaults"].update(
            _parse_rpr(defaults.find(f"{w('rPrDefault')}/{w('rPr')}")))
        styles["doc_defaults"].update(
            _parse_ppr(defaults.find(f"{w('pPrDefault')}/{w('pPr')}")))
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
            "rpr": _parse_rpr(style.find(w("rPr"))),
            "ppr": _parse_ppr(style.find(w("pPr"))),
        }
        styles["records"][sid] = record
        styles["by_name"][name.lower()] = sid
        if record["default"] and record["type"] == "paragraph":
            styles["default_style_id"] = sid
    for sid, record in styles["records"].items():
        merged = dict(styles["doc_defaults"])
        chain, cursor, seen = [], sid, set()
        while cursor and cursor in styles["records"] and cursor not in seen:
            seen.add(cursor)
            chain.append(cursor)
            cursor = styles["records"][cursor].get("based_on")
        for ancestor in reversed(chain):
            merged.update(styles["records"][ancestor]["rpr"])
            merged.update(styles["records"][ancestor]["ppr"])
        record["effective"] = merged
    return styles


def _parse_theme(zf):
    theme = {"major_latin": None, "minor_latin": None, "major_ea": None,
             "minor_ea": None, "major_cs": None, "minor_cs": None}
    try:
        root = ET.fromstring(zf.read("word/theme/theme1.xml"))
    except (KeyError, ET.ParseError):
        return theme
    scheme = root.find(f".//{{{A_NS}}}fontScheme")
    if scheme is None:
        return theme
    for family, prefix in (("majorFont", "major"), ("minorFont", "minor")):
        node = scheme.find(f"{{{A_NS}}}{family}")
        if node is None:
            continue
        for tag in ("latin", "ea", "cs"):
            item = node.find(f"{{{A_NS}}}{tag}")
            if item is not None and item.get("typeface"):
                theme[f"{prefix}_{tag}"] = item.get("typeface")
        if not theme[f"{prefix}_ea"]:
            for script in node.findall(f"{{{A_NS}}}font"):
                if script.get("script") in ("Hans", "Hant") and script.get("typeface"):
                    theme[f"{prefix}_ea"] = script.get("typeface")
                    break
    return theme


def _resolve_fonts(raw, theme, defaults):
    font = raw.get("font_ascii") or raw.get("font_hAnsi") or \
        defaults.get("font_ascii") or defaults.get("font_hAnsi")
    east = raw.get("font_eastAsia") or defaults.get("font_eastAsia")
    theme_ascii = raw.get("asciiTheme") or raw.get("hAnsiTheme")
    theme_ea = raw.get("eastAsiaTheme")
    if theme_ascii:
        font = theme.get("major_latin") if "major" in theme_ascii.lower() \
            else theme.get("minor_latin")
    if theme_ea:
        east = theme.get("major_ea") if "major" in theme_ea.lower() \
            else theme.get("minor_ea")
    if not font:
        font = theme.get("minor_latin") or theme.get("major_latin")
    if not east:
        east = theme.get("minor_ea") or theme.get("major_ea") or font
    return font, east


def _style_config(record, theme, defaults, role_name=None, level=None):
    effective = record.get("effective") or {}
    font, east = _resolve_fonts(effective, theme, defaults)
    size = effective.get("size_pt")
    config = {
        "style_id": record.get("style_id"),
        "style_name": record.get("name"),
        "font": font, "font_east_asia": east, "size_pt": size,
        "bold": effective.get("b"), "italic": effective.get("i"),
        "align": effective.get("align"),
        "space_before_pt": effective.get("space_before_pt"),
        "space_after_pt": effective.get("space_after_pt"),
        "line_spacing": effective.get("line_spacing"),
        "line_spacing_pt": effective.get("line_spacing_pt"),
        "line_rule": effective.get("line_rule"),
        "first_line_indent_cm": effective.get("first_line_indent_cm"),
        "left_indent_cm": effective.get("left_indent_cm"),
        "right_indent_cm": effective.get("right_indent_cm"),
        "hanging_indent_cm": effective.get("hanging_indent_cm"),
        "keep_next": effective.get("keepNext"),
        "outline_level": effective.get("outline_level"),
        "numbering": {"num_id": effective.get("num_id"),
                      "level": effective.get("num_level")},
    }
    if level is not None:
        config["outline_level"] = level
        config["keep_next"] = True
        config["bold"] = True if config.get("bold") is None else config["bold"]
    return config


def _style_role(style_id, name, effective):
    text = f"{style_id} {name}".lower()
    compact = re.sub(r"[\s_\-]+", "", text)
    if re.search(r"(^|\s)(title|论文题目|文章标题)(\s|$)", name.lower()) and \
            not re.search(r"heading|标题\s*[0-9一二三四五六七八九]", name.lower()):
        return "title"
    if re.search(r"(caption|题注|图注|表注|图题|表题)", text):
        if re.search(r"(table|表)", text):
            return "caption_table"
        if re.search(r"(figure|fig|图)", text):
            return "caption_figure"
        return "caption"
    if re.search(r"(bibliography|references?|参考文献|文献条目|引用文献)", text):
        return "reference"
    if re.search(r"(abstract|摘要)", text):
        return "abstract"
    if re.search(r"(keywords?|关键词)", text):
        return "keywords"
    match = re.search(r"(?:heading|标题)\s*([1-9])", name.lower())
    if match:
        return f"heading_{int(match.group(1))}"
    match = re.search(r"^h([1-6])$", compact)
    if match:
        return f"heading_{int(match.group(1))}"
    level = effective.get("outline_level")
    if level and 1 <= int(level) <= 9:
        return f"heading_{int(level)}"
    if re.search(r"(^|\s)(normal|body text|body|正文|文本)(\s|$)", name.lower()) or \
            style_id in ("Normal", "BodyText"):
        return "body"
    return None


def _sample_caption_labels(paragraphs):
    labels = {}
    separators = {}
    for text in paragraphs:
        match = CAPTION_RE.match(clean_space(text))
        if not match:
            continue
        label = match.group(1)
        kind = "figure" if label.lower().startswith(("figure", "fig", "图")) else "table"
        labels.setdefault(kind, label)
        remainder = text[len(label):].lstrip()
        number_match = re.match(r"[0-9]+[A-Za-z]?\s*([.:：、]?)", remainder)
        if number_match and number_match.group(1):
            separators.setdefault(kind, number_match.group(1))
    return labels, separators


def _body_children(document):
    body = document.find(w("body"))
    return list(body) if body is not None else []


def _infer_caption_positions(document):
    children = _body_children(document)
    positions = {"figure": None, "table": None}
    for index, child in enumerate(children):
        if child.tag == w("tbl"):
            for delta, side in ((-1, "above"), (1, "below")):
                target = index + delta
                if 0 <= target < len(children) and children[target].tag == w("p"):
                    text = clean_space("".join(t.text or "" for t in children[target].iter(w("t"))))
                    match = CAPTION_RE.match(text)
                    if match and match.group(1).lower().startswith(("table", "表")):
                        positions["table"] = side
                        break
        elif child.tag == w("p"):
            has_image = child.find(f".//{{{A_NS}}}blip") is not None
            if not has_image:
                continue
            for delta, side in ((-1, "above"), (1, "below")):
                target = index + delta
                if 0 <= target < len(children) and children[target].tag == w("p"):
                    text = clean_space("".join(t.text or "" for t in children[target].iter(w("t"))))
                    match = CAPTION_RE.match(text)
                    if match and match.group(1).lower().startswith(("figure", "fig", "图")):
                        positions["figure"] = side
                        break
    return positions


def _reference_style_from_entries(entries):
    if not entries:
        return "unknown"
    numeric_bracket = sum(1 for text in entries if re.match(r"^\s*\[\d+\]", text))
    numeric_dot = sum(1 for text in entries if re.match(r"^\s*\d+[.)]\s", text))
    author_year = sum(1 for text in entries
                      if re.search(r"\((?:19|20)\d{2}[a-z]?\)", text))
    gbt = sum(1 for text in entries if re.search(r"\[(?:J|M|D|C|P|R|S|N|EB/OL)\]", text))
    total = len(entries)
    if gbt >= max(1, total // 2):
        return "gb-t-7714"
    if numeric_bracket >= max(1, total // 2):
        return "numeric-bracket"
    if numeric_dot >= max(1, total // 2):
        return "numeric"
    if author_year >= max(1, total // 2):
        return "author-year"
    return "template"


def _page_name(width, height):
    known = {"A4": (11906, 16838), "A3": (16838, 23811), "A5": (8391, 11906),
             "B5": (9979, 14175), "Letter": (12240, 15840),
             "Legal": (12240, 20160)}
    for name, (known_w, known_h) in known.items():
        if abs(width - known_w) <= 30 and abs(height - known_h) <= 30:
            return name
        if abs(width - known_h) <= 30 and abs(height - known_w) <= 30:
            return name
    return "custom"


def default_spec():
    body_font = "Liberation Serif"
    roles = {
        "body": {"style_id": "Normal", "style_name": "Normal",
                 "font": body_font, "font_east_asia": body_font, "size_pt": 10.5,
                 "align": "both", "line_spacing": 1.15,
                 "first_line_indent_cm": 0.74},
    }
    heading_sizes = {1: 16.0, 2: 14.0, 3: 12.0, 4: 11.0, 5: 10.5, 6: 10.5}
    for level, size in heading_sizes.items():
        roles[f"heading_{level}"] = {
            "style_id": f"Heading{level}", "style_name": f"heading {level}",
            "font": body_font, "font_east_asia": body_font, "size_pt": size,
            "bold": True, "align": "left", "keep_next": True,
            "outline_level": level, "space_before_pt": 12.0 if level <= 2 else 6.0,
            "space_after_pt": 6.0,
        }
    roles.update({
        "title": {"style_id": "Title", "style_name": "Title",
                  "font": body_font, "font_east_asia": body_font,
                  "size_pt": 18.0, "bold": True, "align": "center",
                  "space_after_pt": 12.0, "keep_next": True},
        "caption_figure": {"style_id": "Caption", "style_name": "Caption",
                           "font": body_font, "font_east_asia": body_font,
                           "size_pt": 9.0, "align": "left"},
        "caption_table": {"style_id": "TableCaption",
                          "style_name": "Table Caption",
                          "font": body_font, "font_east_asia": body_font,
                          "size_pt": 9.0, "align": "left"},
        "reference": {"style_id": "Bibliography", "style_name": "Bibliography",
                      "font": body_font, "font_east_asia": body_font,
                      "size_pt": 9.0, "align": "left", "hanging_indent_cm": 0.75,
                      "line_spacing": 1.0, "space_after_pt": 3.0},
        "abstract": {"style_id": "Abstract", "style_name": "Abstract",
                     "font": body_font, "font_east_asia": body_font,
                     "size_pt": 9.5, "align": "both", "line_spacing": 1.15},
        "keywords": {"style_id": "Keywords", "style_name": "Keywords",
                     "font": body_font, "font_east_asia": body_font,
                     "size_pt": 9.5, "align": "left"},
    })
    return {
        "meta": {"generated_by": "adapter.py", "created_at": utc_now(),
                 "template": None, "template_sha256": None,
                 "guide": None, "source": "built-in defaults",
                 "evidence": [], "conflicts": [], "unrecognized": [],
                 "font_substitutions": []},
        "page": {"size": "A4", "width_twips": 11906, "height_twips": 16838,
                 "width_cm": 21.0, "height_cm": 29.7, "orientation": "portrait",
                 "margins_cm": {"top": 2.54, "right": 2.54, "bottom": 2.54,
                                "left": 2.54, "header": 1.25, "footer": 1.25,
                                "gutter": 0.0},
                 "columns": {"count": 1, "space_cm": 0.75}},
        "fonts": {"body_ascii": body_font, "body_east_asia": body_font,
                  "heading_ascii": body_font, "body_size_pt": 10.5,
                  "body_line_spacing": 1.15, "caption_size_pt": 9.0,
                  "theme_major_latin": None, "theme_minor_latin": None,
                  "theme_major_ea": None, "theme_minor_ea": None},
        "styles": roles,
        "captions": {"figure_position": "below", "table_position": "above",
                     "labels": {"figure": "Figure", "table": "Table"},
                     "separator": ". "},
        "references": {"style": "unknown", "hanging_indent_cm": 0.75,
                       "size_pt": 9.0},
        "table": {"style": "three-line"},
        "numbering": {"mode": "none"},
        "header_footer": {"headers": 0, "footers": 0, "title_page": False},
        "requirements_flags": {"abstract": False, "keywords": False},
    }


def extract_template_spec(template_path):
    """Read the actual DOCX template and return a measured format spec."""
    spec = default_spec()
    attempts = []
    path = Path(template_path)
    spec["meta"]["template"] = path.name
    spec["meta"]["template_sha256"] = sha256_file(path)
    spec["meta"]["source"] = str(path)
    try:
        with zipfile.ZipFile(path) as zf:
            styles = _parse_styles(zf)
            theme = _parse_theme(zf)
            document = ET.fromstring(zf.read("word/document.xml"))
            names = set(zf.namelist())
            has_numbering = "word/numbering.xml" in names
    except (zipfile.BadZipFile, OSError, KeyError, ET.ParseError) as exc:
        spec["meta"]["warnings"] = [
            f"template could not be read ({type(exc).__name__}: {exc}); "
            f"built-in defaults are used"]
        spec["meta"]["template_readable"] = False
        return spec

    evidence = []
    def add_evidence(field, value, locator):
        evidence.append({"field": field, "value": value,
                         "source": path.name, "locator": locator})

    # ---- styles and roles -------------------------------------------------
    def records_for(role):
        found = []
        for record in styles["records"].values():
            if record.get("type") != "paragraph":
                continue
            detected = _style_role(record["style_id"], record["name"],
                                   record.get("effective") or {})
            if detected == role:
                found.append(record)
        return found

    role_records = {}
    for role in ("title", "body", "reference", "abstract", "keywords"):
        records = records_for(role)
        if records:
            role_records[role] = records[0]
    for level in range(1, 7):
        records = records_for(f"heading_{level}")
        if records:
            records.sort(key=lambda item: (
                0 if re.search(r"(?:heading|标题)\s*" + str(level),
                               item["name"].lower()) else 1,
                item["style_id"]))
            role_records[f"heading_{level}"] = records[0]
    for role in ("caption", "caption_figure", "caption_table"):
        records = records_for(role)
        if records:
            role_records[role] = records[0]

    def apply_record(role_name, record, level=None):
        config = _style_config(record, theme, styles["doc_defaults"],
                               role_name, level)
        spec["styles"][role_name] = config
        add_evidence(f"styles.{role_name}", {
            "style_id": record["style_id"], "style_name": record["name"],
            "size_pt": config.get("size_pt"), "font": config.get("font")},
            f"word/styles.xml styleId={record['style_id']} name={record['name']}")

    body_record = role_records.get("body")
    if body_record is None and styles.get("default_style_id"):
        body_record = styles["records"].get(styles["default_style_id"])
    if body_record is None and styles["records"]:
        body_record = next(iter(styles["records"].values()))
    if body_record is not None:
        apply_record("body", body_record)
        body_config = spec["styles"]["body"]
        spec["fonts"]["body_ascii"] = body_config.get("font") or \
            styles["doc_defaults"].get("font_ascii")
        spec["fonts"]["body_east_asia"] = body_config.get("font_east_asia") or \
            styles["doc_defaults"].get("font_eastAsia")
        spec["fonts"]["body_size_pt"] = body_config.get("size_pt") or 10.5
        if body_config.get("line_spacing"):
            spec["fonts"]["body_line_spacing"] = body_config["line_spacing"]
        add_evidence("fonts.body", {
            "ascii": spec["fonts"]["body_ascii"],
            "east_asia": spec["fonts"]["body_east_asia"],
            "size_pt": spec["fonts"]["body_size_pt"]},
            f"word/styles.xml docDefaults + styleId={body_record['style_id']}")

    if "title" in role_records:
        apply_record("title", role_records["title"])
    else:
        title = dict(spec["styles"]["body"])
        title.update({"style_id": "Title", "style_name": "Title",
                      "size_pt": (spec["fonts"]["body_size_pt"] or 10.5) + 6,
                      "bold": True, "align": "center", "keep_next": True,
                      "first_line_indent_cm": None})
        spec["styles"]["title"] = title
        add_evidence("styles.title", {"style_id": "Title", "synthetic": True},
                     "word/styles.xml: no title style; derived from body +6pt")

    for level in range(1, 7):
        role_name = f"heading_{level}"
        if role_name in role_records:
            apply_record(role_name, role_records[role_name], level)
        else:
            role = dict(spec["styles"]["body"])
            role.update({
                "style_id": f"Heading{level}", "style_name": f"heading {level}",
                "size_pt": max((spec["fonts"]["body_size_pt"] or 10.5) + 6 - level,
                               10.5),
                "bold": True, "align": "left", "keep_next": True,
                "outline_level": level, "first_line_indent_cm": None})
            spec["styles"][role_name] = role
            add_evidence(f"styles.{role_name}",
                         {"style_id": f"Heading{level}", "synthetic": True},
                         f"word/styles.xml: no heading {level} style; derived from body")

    caption_figure = role_records.get("caption_figure") or role_records.get("caption")
    caption_table = role_records.get("caption_table") or role_records.get("caption")
    for name, record in (("caption_figure", caption_figure),
                         ("caption_table", caption_table)):
        if record is not None:
            apply_record(name, record)
        else:
            role = dict(spec["styles"]["body"])
            role.update({"style_id": "Caption" if name == "caption_figure"
                         else "TableCaption",
                         "style_name": "Caption" if name == "caption_figure"
                         else "Table Caption",
                         "size_pt": max((spec["fonts"]["body_size_pt"] or 10.5) - 1.5, 8),
                         "align": "left", "first_line_indent_cm": None,
                         "italic": False})
            spec["styles"][name] = role
            add_evidence(f"styles.{name}",
                         {"style_id": role["style_id"], "synthetic": True},
                         "word/styles.xml: no caption style; derived from body")

    if "reference" in role_records:
        apply_record("reference", role_records["reference"])
        ref_config = spec["styles"]["reference"]
        if ref_config.get("hanging_indent_cm") is None:
            ref_config["hanging_indent_cm"] = 0.75
    else:
        role = dict(spec["styles"]["body"])
        role.update({"style_id": "Bibliography", "style_name": "Bibliography",
                     "size_pt": max((spec["fonts"]["body_size_pt"] or 10.5) - 1.5, 8),
                     "align": "left", "hanging_indent_cm": 0.75,
                     "first_line_indent_cm": None, "line_spacing": 1.0})
        spec["styles"]["reference"] = role
        add_evidence("styles.reference",
                     {"style_id": "Bibliography", "synthetic": True},
                     "word/styles.xml: no bibliography style; derived from body")
    for role_name in ("abstract", "keywords"):
        if role_name in role_records:
            apply_record(role_name, role_records[role_name])

    spec["references"]["hanging_indent_cm"] = \
        spec["styles"]["reference"].get("hanging_indent_cm") or 0.75
    spec["references"]["size_pt"] = spec["styles"]["reference"].get("size_pt")

    # ---- page / sectPr ----------------------------------------------------
    sections = []
    body = document.find(w("body"))
    children = list(body) if body is not None else []
    for sect in document.iter(w("sectPr")):
        sections.append(sect)
    sect = sections[-1] if sections else None
    if sect is not None:
        pg_size = sect.find(w("pgSz"))
        width = _int_attr(pg_size, "w") if pg_size is not None else None
        height = _int_attr(pg_size, "h") if pg_size is not None else None
        if width and height:
            spec["page"].update({
                "width_twips": width, "height_twips": height,
                "width_cm": round(width * TWIPS_CM, 2),
                "height_cm": round(height * TWIPS_CM, 2),
                "size": _page_name(width, height),
                "orientation": "landscape" if width > height else "portrait"})
            add_evidence("page.size", {
                "size": spec["page"]["size"], "width_twips": width,
                "height_twips": height},
                f"word/document.xml sectPr/pgSz w={width} h={height}")
        pg_mar = sect.find(w("pgMar"))
        if pg_mar is not None:
            margins = dict(spec["page"]["margins_cm"])
            for key in ("top", "right", "bottom", "left", "header", "footer", "gutter"):
                value = _int_attr(pg_mar, key)
                if value is not None:
                    margins[key] = round(value * TWIPS_CM, 3)
            spec["page"]["margins_cm"] = margins
            add_evidence("page.margins", margins,
                         "word/document.xml sectPr/pgMar " +
                         " ".join(f"{key}={value}" for key, value in margins.items()))
        cols = sect.find(w("cols"))
        if cols is not None:
            count = _int_attr(cols, "num", 1) or 1
            space = _int_attr(cols, "space")
            spec["page"]["columns"] = {
                "count": count,
                "space_cm": round(space * TWIPS_CM, 3) if space is not None else None}
            add_evidence("page.columns", spec["page"]["columns"],
                         f"word/document.xml sectPr/cols num={count}")
        headers = len(list(sect.findall(w("headerReference"))))
        footers = len(list(sect.findall(w("footerReference"))))
        spec["header_footer"] = {
            "headers": headers, "footers": footers,
            "title_page": sect.find(w("titlePg")) is not None}
        add_evidence("header_footer", spec["header_footer"],
                     f"word/document.xml sectPr headerReference={headers} "
                     f"footerReference={footers}")
    spec["page"]["section_count"] = len(sections)
    add_evidence("page.section_count", len(sections), "word/document.xml sectPr count")

    # ---- theme ------------------------------------------------------------
    spec["fonts"]["theme_major_latin"] = theme.get("major_latin")
    spec["fonts"]["theme_minor_latin"] = theme.get("minor_latin")
    spec["fonts"]["theme_major_ea"] = theme.get("major_ea")
    spec["fonts"]["theme_minor_ea"] = theme.get("minor_ea")
    if theme.get("major_latin") or theme.get("minor_latin"):
        add_evidence("fonts.theme", {"major": theme.get("major_latin"),
                                     "minor": theme.get("minor_latin"),
                                     "major_ea": theme.get("major_ea"),
                                     "minor_ea": theme.get("minor_ea")},
                     "word/theme/theme1.xml fontScheme")

    # ---- captions, references, numbering, tables --------------------------
    paragraph_texts = [clean_space("".join(t.text or "" for t in p.iter(w("t"))))
                       for p in document.iter(w("p"))]
    labels, separators = _sample_caption_labels(paragraph_texts)
    positions = _infer_caption_positions(document)
    spec["captions"]["labels"].update(labels)
    spec["captions"]["figure_position"] = positions["figure"] or "below"
    spec["captions"]["table_position"] = positions["table"] or "above"
    if separators:
        first = next(iter(separators.values()))
        spec["captions"]["separator"] = f"{first} "
    add_evidence("captions", {
        "labels": spec["captions"]["labels"],
        "figure_position": spec["captions"]["figure_position"],
        "table_position": spec["captions"]["table_position"]},
        "word/document.xml caption paragraphs relative to tables/images")

    reference_entries = []
    reference_heading_index = None
    for index, text in enumerate(paragraph_texts):
        if REFERENCE_HEADING_RE.match(text):
            reference_heading_index = index
            break
    if reference_heading_index is not None:
        for text in paragraph_texts[reference_heading_index + 1:]:
            if text and not CAPTION_RE.match(text):
                reference_entries.append(text)
            if len(reference_entries) >= 20:
                break
    style_guess = _reference_style_from_entries(reference_entries)
    spec["references"]["style"] = style_guess
    add_evidence("references.style", style_guess,
                 f"word/document.xml reference entries after heading "
                 f"(sample={len(reference_entries)})")
    if reference_entries:
        add_evidence("references.entries", reference_entries[:5],
                     f"word/document.xml paragraphs after reference heading")

    heading_texts = []
    heading_sizes = []
    for p in document.iter(w("p")):
        text = clean_space("".join(t.text or "" for t in p.iter(w("t"))))
        if not text:
            continue
        ppr = p.find(w("pPr"))
        sid = ""
        if ppr is not None and ppr.find(w("pStyle")) is not None:
            sid = ppr.find(w("pStyle")).get(w("val")) or ""
        record = styles["records"].get(sid)
        effective = (record or {}).get("effective") or {}
        level = effective.get("outline_level")
        if level or re.match(r"^(?:heading|标题)\s*[1-9]", (record or {}).get("name", "").lower()):
            heading_texts.append(text)
        if sid and sid in {record["style_id"] for record in role_records.values()}:
            if effective.get("size_pt"):
                heading_sizes.append(effective["size_pt"])
    numbering_mode = "none"
    if any(re.match(r"^\d+(?:\.\d+)*[.\s、]", text) for text in heading_texts):
        numbering_mode = "decimal"
    elif any((styles["records"].get(record["style_id"], {}).get("effective") or {}).get("num_id")
             for record in role_records.values() if record["style_id"].startswith("Heading")):
        numbering_mode = "style-numbering"
    spec["numbering"] = {"mode": numbering_mode, "samples": heading_texts[:8],
                         "numbering_xml": has_numbering}
    add_evidence("numbering", spec["numbering"],
                 "word/document.xml heading samples + word/numbering.xml presence")

    table_style = "three-line"
    for table in document.iter(w("tbl")):
        borders = table.find(f"{w('tblPr')}/{w('tblBorders')}")
        if borders is not None:
            inside_h = borders.find(w("insideH"))
            if inside_h is not None and inside_h.get(w("val")) not in ("nil", "none"):
                table_style = "grid"
                break
    spec["table"] = {"style": table_style}
    add_evidence("table.style", table_style,
                 "word/document.xml tblBorders insideH")

    spec["requirements_flags"] = {
        "abstract": any(ABSTRACT_RE.match(text) for text in paragraph_texts),
        "keywords": any(KEYWORDS_RE.match(text) for text in paragraph_texts),
    }
    spec["meta"]["evidence"] = evidence
    spec["meta"]["warnings"] = attempts
    spec["meta"]["template_readable"] = True
    return spec


# ---------------------------------------------------------------------------
# Style-guide text (secondary source)
# ---------------------------------------------------------------------------
CN_SIZES = {"初号": 42.0, "小初": 36.0, "一号": 26.0, "小一": 24.0,
            "二号": 22.0, "小二": 18.0, "三号": 16.0, "小三": 15.0,
            "四号": 14.0, "小四": 12.0, "五号": 10.5, "小五": 9.0,
            "六号": 7.5, "小六": 6.5}
FONT_NAMES = ["Liberation Serif", "Liberation Sans", "Times New Roman",
              "Arial", "Calibri", "Cambria", "Georgia", "Noto Serif",
              "Noto Sans", "DejaVu Serif", "DejaVu Sans", "宋体", "黑体",
              "Microsoft YaHei", "微软雅黑", "楷体", "仿宋", "思源宋体",
              "思源黑体"]


def _to_cm(value, unit):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    unit = (unit or "cm").lower()
    if unit in ("cm", "厘米"):
        return round(number, 3)
    if unit in ("mm", "毫米"):
        return round(number / 10.0, 3)
    if unit in ("in", "英寸"):
        return round(number * 2.54, 3)
    if unit in ("pt", "磅"):
        return round(number * 2.54 / 72.0, 3)
    return round(number, 3)


def parse_guide_text(text, guide_name="guide"):
    """Extract explicit format values and their line-level evidence."""
    result = {"page": {}, "fonts": {}, "styles": {}, "captions": {},
              "references": {}, "requirements_flags": {},
              "evidence": [], "unrecognized": [], "recognized": 0}
    lines = str(text).splitlines()
    matched_lines = set()

    def add(field, value, line_no, line):
        result["evidence"].append({
            "field": field, "value": value, "source": guide_name,
            "locator": f"line {line_no}", "text": clean_space(line)[:180]})
        matched_lines.add(line_no)
        result["recognized"] += 1

    page_sizes = {"A4": (11906, 16838, 21.0, 29.7),
                  "A3": (16838, 23811, 29.7, 42.0),
                  "A5": (8391, 11906, 14.8, 21.0),
                  "B5": (9979, 14175, 17.6, 25.0),
                  "LETTER": (12240, 15840, 21.59, 27.94),
                  "LEGAL": (12240, 20160, 21.59, 35.56)}
    for line_no, line in enumerate(lines, 1):
        match = re.search(r"\b(A4|A3|A5|B5|Letter|Legal)\b", line, re.IGNORECASE)
        if match and "page" not in result["page"]:
            key = match.group(1).upper()
            width, height, width_cm, height_cm = page_sizes[key]
            result["page"].update({
                "size": match.group(1).title(), "width_twips": width,
                "height_twips": height, "width_cm": width_cm,
                "height_cm": height_cm})
            add("page.size", result["page"]["size"], line_no, line)
        if re.search(r"landscape|横向", line, re.IGNORECASE):
            result["page"]["orientation"] = "landscape"
            add("page.orientation", "landscape", line_no, line)
        elif re.search(r"portrait|纵向", line, re.IGNORECASE):
            result["page"]["orientation"] = "portrait"
            add("page.orientation", "portrait", line_no, line)

        columns = None
        if re.search(r"双栏|两栏|two[\s-]*column|2[\s-]*column", line, re.IGNORECASE):
            columns = 2
        elif re.search(r"单栏|一栏|single[\s-]*column|one[\s-]*column", line, re.IGNORECASE):
            columns = 1
        else:
            match = re.search(r"(?:栏数|columns?)\s*[:：=]?\s*([0-9]+)", line,
                              re.IGNORECASE)
            if match:
                columns = int(match.group(1))
        if columns:
            result["page"].setdefault("columns", {})["count"] = columns
            add("page.columns", columns, line_no, line)
        column_space = re.search(
            r"(?:column\s*spacing|栏间距|列间距)[^0-9\n]{0,12}"
            r"([0-9]+(?:\.[0-9]+)?)\s*(cm|mm|in|pt|厘米|毫米|英寸|磅)",
            line, re.IGNORECASE)
        if column_space:
            result["page"].setdefault("columns", {})["space_cm"] = _to_cm(
                column_space.group(1), column_space.group(2))
            add("page.columns.space_cm", result["page"]["columns"]["space_cm"],
                line_no, line)

        margin_hit = False
        margin_context = re.search(r"页边距|margin|边距|上下|左右", line,
                                   re.IGNORECASE) or re.match(
            r"^\s*(?:上|下|左|右|top|bottom|left|right)\s*[:：=]", line,
            re.IGNORECASE)
        if margin_context:
            for label, key in (("上", "top"), ("top", "top"), ("下", "bottom"),
                               ("bottom", "bottom"), ("左", "left"),
                               ("left", "left"), ("右", "right"),
                               ("right", "right"), ("装订线", "gutter"),
                               ("gutter", "gutter")):
                pattern = rf"(?<![A-Za-z]){label}(?![A-Za-z])[^0-9\n]{{0,12}}" \
                          rf"([0-9]+(?:\.[0-9]+)?)\s*(cm|mm|in|pt|厘米|毫米|英寸|磅)"
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    value = _to_cm(match.group(1), match.group(2))
                    if value is not None:
                        result["page"].setdefault("margins_cm", {})[key] = value
                        margin_hit = True
        if re.search(r"上下|top\s*(?:and|&)\s*bottom", line, re.IGNORECASE):
            match = re.search(r"(?:上下|top\s*(?:and|&)\s*bottom)[^0-9]{0,12}"
                              r"([0-9]+(?:\.[0-9]+)?)\s*(cm|mm|in|pt|厘米|毫米|英寸|磅)",
                              line, re.IGNORECASE)
            if match:
                value = _to_cm(match.group(1), match.group(2))
                if value is not None:
                    result["page"].setdefault("margins_cm", {})["top"] = value
                    result["page"]["margins_cm"]["bottom"] = value
                    margin_hit = True
        if re.search(r"左右|left\s*(?:and|&)\s*right", line, re.IGNORECASE):
            match = re.search(r"(?:左右|left\s*(?:and|&)\s*right)[^0-9]{0,12}"
                              r"([0-9]+(?:\.[0-9]+)?)\s*(cm|mm|in|pt|厘米|毫米|英寸|磅)",
                              line, re.IGNORECASE)
            if match:
                value = _to_cm(match.group(1), match.group(2))
                if value is not None:
                    result["page"].setdefault("margins_cm", {})["left"] = value
                    result["page"]["margins_cm"]["right"] = value
                    margin_hit = True
        if margin_hit:
            add("page.margins_cm", result["page"].get("margins_cm"), line_no, line)

        if re.search(r"字体|font", line, re.IGNORECASE):
            for font in FONT_NAMES:
                if re.search(re.escape(font), line, re.IGNORECASE):
                    result["fonts"]["body_ascii"] = font
                    if font in ("宋体", "黑体", "楷体", "仿宋", "微软雅黑"):
                        result["fonts"]["body_east_asia"] = font
                    add("fonts.body_ascii", font, line_no, line)
                    break
        size = None
        for label, value in CN_SIZES.items():
            if label in line and re.search(r"字号|font\s*size|正文|body", line,
                                           re.IGNORECASE):
                size = value
                break
        if size is None:
            match = re.search(r"(?:字号|font\s*size|body\s*size)[^0-9\n]{0,12}"
                              r"([0-9]+(?:\.[0-9]+)?)\s*(?:pt|磅)", line,
                              re.IGNORECASE)
            if match:
                size = float(match.group(1))
        if size is None and re.search(r"字号|font|body|正文", line, re.IGNORECASE):
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:pt|磅)", line,
                              re.IGNORECASE)
            if match:
                size = float(match.group(1))
        if size is not None:
            result["fonts"]["body_size_pt"] = size
            add("fonts.body_size_pt", size, line_no, line)

        line_spacing = None
        match = re.search(r"(?:行距|line\s*spacing)[^0-9\n]{0,12}"
                          r"([0-9]+(?:\.[0-9]+)?)\s*(?:倍|lines?)", line,
                          re.IGNORECASE)
        if match:
            line_spacing = float(match.group(1))
        elif re.search(r"1\.5\s*倍|1\.5\s*lines?", line, re.IGNORECASE):
            line_spacing = 1.5
        elif re.search(r"双倍行距|double\s+spacing", line, re.IGNORECASE):
            line_spacing = 2.0
        elif re.search(r"单倍行距|single\s+spacing", line, re.IGNORECASE):
            line_spacing = 1.0
        if line_spacing is None and re.search(r"行距|line\s*spacing", line,
                                              re.IGNORECASE):
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)", line)
            if match:
                number = float(match.group(1))
                if number <= 4:
                    line_spacing = number
        if line_spacing is not None:
            result["fonts"]["body_line_spacing"] = line_spacing
            add("fonts.body_line_spacing", line_spacing, line_no, line)

        for level in (1, 2, 3, 4):
            pattern = rf"(?:{'一二三四'[level - 1]}级标题|标题\s*{level}|"
            pattern += rf"heading\s*{level}|h{level})[^0-9\n]{{0,30}}"
            pattern += r"([0-9]+(?:\.[0-9]+)?)\s*(?:pt|磅)"
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                result["styles"].setdefault(f"heading_{level}", {})["size_pt"] = \
                    float(match.group(1))
                add(f"styles.heading_{level}.size_pt", float(match.group(1)),
                    line_no, line)

        if re.search(r"(图注|图题|figure\s*caption|figure\s*note)", line,
                     re.IGNORECASE):
            if re.search(r"下方|之下|below|beneath", line, re.IGNORECASE):
                result["captions"]["figure_position"] = "below"
                add("captions.figure_position", "below", line_no, line)
            elif re.search(r"上方|之上|above", line, re.IGNORECASE):
                result["captions"]["figure_position"] = "above"
                add("captions.figure_position", "above", line_no, line)
        if re.search(r"(表注|表题|table\s*caption|table\s*note)", line,
                     re.IGNORECASE):
            if re.search(r"上方|之上|above", line, re.IGNORECASE):
                result["captions"]["table_position"] = "above"
                add("captions.table_position", "above", line_no, line)
            elif re.search(r"下方|之下|below|beneath", line, re.IGNORECASE):
                result["captions"]["table_position"] = "below"
                add("captions.table_position", "below", line_no, line)

        style_map = {"APA": "APA", "MLA": "MLA", "GB/T 7714": "gb-t-7714",
                     "GB/T7714": "gb-t-7714", "IEEE": "IEEE", "Vancouver": "Vancouver",
                     "Chicago": "Chicago", "Harvard": "Harvard", "AMA": "AMA"}
        for label, key in style_map.items():
            if label.lower() in line.lower() and re.search(
                    r"参考文献|references?|citation", line, re.IGNORECASE):
                result["references"]["style"] = key
                add("references.style", key, line_no, line)
                break

        if re.search(r"摘要|abstract", line, re.IGNORECASE) and \
                re.search(r"必需|必须|required|required", line, re.IGNORECASE):
            result["requirements_flags"]["abstract"] = True
            add("requirements_flags.abstract", True, line_no, line)
        if re.search(r"关键词|keywords?", line, re.IGNORECASE) and \
                re.search(r"必需|必须|required", line, re.IGNORECASE):
            result["requirements_flags"]["keywords"] = True
            add("requirements_flags.keywords", True, line_no, line)

    format_terms = re.compile(
        r"字体|字号|行距|页边距|margin|font|spacing|标题|heading|参考文献|"
        r"references?|图注|表注|caption|双栏|栏数|column", re.IGNORECASE)
    for line_no, line in enumerate(lines, 1):
        if line_no in matched_lines:
            continue
        if format_terms.search(line):
            result["unrecognized"].append({
                "line": line_no, "text": clean_space(line)[:220]})
    result["unrecognized"] = result["unrecognized"][:40]
    return result


# ---------------------------------------------------------------------------
# Merge guide + template + case hints
# ---------------------------------------------------------------------------
def merge_spec(template_spec, guide, case):
    spec = copy.deepcopy(template_spec)
    conflicts = []
    template_source = (spec.get("meta") or {}).get("source")

    def conflict(field, old, new, source):
        if old is not None and new is not None and old != new:
            conflicts.append({"field": field, "template": old, "guide": new,
                              "resolution": "guide value applied",
                              "source": source})

    if guide.get("page"):
        for key, value in guide["page"].items():
            if key == "columns":
                old = (spec.get("page") or {}).get("columns") or {}
                conflict("page.columns", old.get("count"), value.get("count"),
                         "style guide")
                old.update(value)
            else:
                old = (spec.get("page") or {}).get(key)
                conflict(f"page.{key}", old, value, "style guide")
                spec.setdefault("page", {})[key] = value
        if guide["page"].get("size"):
            key = guide["page"]["size"].upper()
            known = {"A4": (11906, 16838, 21.0, 29.7),
                     "A3": (16838, 23811, 29.7, 42.0),
                     "A5": (8391, 11906, 14.8, 21.0),
                     "B5": (9979, 14175, 17.6, 25.0),
                     "LETTER": (12240, 15840, 21.59, 27.94),
                     "LEGAL": (12240, 20160, 21.59, 35.56)}
            if key in known:
                width, height, width_cm, height_cm = known[key]
                spec["page"].update({"size": guide["page"]["size"],
                                     "width_twips": width, "height_twips": height,
                                     "width_cm": width_cm, "height_cm": height_cm})
    if guide.get("fonts"):
        fonts = guide["fonts"]
        for key in ("body_ascii", "body_east_asia", "body_size_pt",
                    "body_line_spacing"):
            if fonts.get(key) is not None:
                old = (spec.get("fonts") or {}).get(key)
                if key in ("body_ascii", "body_east_asia"):
                    old = (spec.get("styles", {}).get("body") or {}).get(
                        "font" if key == "body_ascii" else "font_east_asia")
                conflict(f"fonts.{key}", old, fonts[key], "style guide")
                spec.setdefault("fonts", {})[key] = fonts[key]
        old_body_font = (spec.get("styles", {}).get("body") or {}).get("font")
        old_east = (spec.get("styles", {}).get("body") or {}).get("font_east_asia")
        if fonts.get("body_ascii"):
            for role in (spec.get("styles") or {}).values():
                if role.get("font") in (None, old_body_font):
                    role["font"] = fonts["body_ascii"]
        if fonts.get("body_east_asia"):
            for role in (spec.get("styles") or {}).values():
                if role.get("font_east_asia") in (None, old_east):
                    role["font_east_asia"] = fonts["body_east_asia"]
        if fonts.get("body_size_pt") is not None:
            spec["styles"].setdefault("body", {})["size_pt"] = \
                fonts["body_size_pt"]
            spec["fonts"]["caption_size_pt"] = max(fonts["body_size_pt"] - 1.5, 8.0)
        if fonts.get("body_line_spacing") is not None:
            spec["styles"].setdefault("body", {})["line_spacing"] = \
                fonts["body_line_spacing"]
    for level, values in (guide.get("styles") or {}).items():
        role = spec.get("styles", {}).setdefault(level, {})
        for key, value in values.items():
            conflict(f"styles.{level}.{key}", role.get(key), value, "style guide")
            role[key] = value
    for key, value in (guide.get("captions") or {}).items():
        old = (spec.get("captions") or {}).get(key)
        conflict(f"captions.{key}", old, value, "style guide")
        spec.setdefault("captions", {})[key] = value
    for key, value in (guide.get("references") or {}).items():
        old = (spec.get("references") or {}).get(key)
        conflict(f"references.{key}", old, value, "style guide")
        spec.setdefault("references", {})[key] = value
    for key, value in (guide.get("requirements_flags") or {}).items():
        spec.setdefault("requirements_flags", {})[key] = value
    spec["meta"]["conflicts"] = (spec["meta"].get("conflicts") or []) + conflicts
    spec["meta"]["guide"] = guide.get("guide_name")
    spec["meta"]["guide_evidence"] = guide.get("evidence") or []
    spec["meta"]["unrecognized"] = (spec["meta"].get("unrecognized") or []) + \
        (guide.get("unrecognized") or [])
    if guide.get("evidence"):
        spec["meta"]["source"] = f"{template_source} + {guide.get('guide_name', 'guide')}"
    target = case.get("journal") or case.get("target") or case.get("template_id")
    spec["meta"]["target"] = target
    spec["meta"]["journal"] = case.get("journal")
    spec["meta"]["template_id"] = case.get("template_id")
    spec["meta"]["case_id"] = case.get("case_id")
    return spec


def apply_font_revision(spec, fallback="Liberation Serif"):
    substitutions = []
    roles = spec.get("styles") or {}
    for role_name, role in roles.items():
        for key, field_name in (("font", "ascii"), ("font_east_asia", "east_asia")):
            requested = role.get(key)
            if not requested:
                if key == "font":
                    role[key] = fallback
                continue
            used, substitution = substitute_font(requested, fallback)
            if substitution:
                substitution["scope"] = f"styles.{role_name}.{key}"
                substitutions.append(substitution)
            role[key] = used
    body = roles.get("body") or {}
    spec.setdefault("fonts", {})["body_ascii"] = body.get("font") or fallback
    spec["fonts"]["body_east_asia"] = body.get("font_east_asia") or \
        spec["fonts"]["body_ascii"]
    for key in ("theme_major_latin", "theme_minor_latin", "theme_major_ea",
                "theme_minor_ea"):
        requested = spec["fonts"].get(key)
        if requested:
            used, substitution = substitute_font(requested, fallback)
            if substitution:
                substitution["scope"] = f"fonts.{key}"
                substitutions.append(substitution)
            spec["fonts"][key] = used
    heading_font = (roles.get("heading_1") or {}).get("font")
    if heading_font:
        spec["fonts"]["heading_ascii"] = heading_font
    spec["meta"]["font_substitutions"] = substitutions
    return substitutions


# ---------------------------------------------------------------------------
# Requirement catalogue
# ---------------------------------------------------------------------------
def _format_number(value):
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        return ", ".join(f"{key}={_format_number(item)}"
                         for key, item in value.items())
    return str(value)


def _evidence_locator(spec, field):
    all_evidence = (spec.get("meta") or {}).get("evidence") or []
    all_evidence += (spec.get("meta") or {}).get("guide_evidence") or []
    for evidence in all_evidence:
        candidate = str(evidence.get("field") or "")
        if candidate == field or field.startswith(candidate + ".") or \
                candidate.startswith(field + "."):
            return (f"{evidence.get('source', 'spec')}: "
                    f"{evidence.get('locator', '?')}")
    return "built-in default (unverified)"


def requirement_catalog(spec):
    requirements = []

    def add(area, field, expected, check_code, method="auto", note=None):
        requirements.append({
            "id": f"R{len(requirements) + 1:02d}", "area": area, "field": field,
            "expected": expected, "check_code": check_code, "method": method,
            "source": _evidence_locator(spec, field), "note": note})

    page = spec.get("page") or {}
    add("page", "page.size",
        f"{page.get('size')} ({page.get('width_twips')}x{page.get('height_twips')} twips)",
        "PAGE-SIZE")
    add("page", "page.margins_cm", _format_number(page.get("margins_cm")),
        "PAGE-MARGIN")
    add("page", "page.columns", _format_number(page.get("columns")),
        "PAGE-COLUMNS")
    fonts = spec.get("fonts") or {}
    add("fonts", "fonts.body_ascii", fonts.get("body_ascii"), "BODY-FONT")
    add("fonts", "fonts.body_size_pt", fonts.get("body_size_pt"), "BODY-SIZE")
    add("fonts", "fonts.body_line_spacing", fonts.get("body_line_spacing"),
        "BODY-SPACING")
    styles = spec.get("styles") or {}
    if styles.get("title"):
        add("typography", "styles.title",
            _format_number({key: styles["title"].get(key) for key in
                            ("style_id", "size_pt", "bold", "align")}), "TITLE-STYLE")
    for level in range(1, 7):
        role = styles.get(f"heading_{level}")
        if not role:
            continue
        add("structure", f"styles.heading_{level}",
            _format_number({key: role.get(key) for key in
                            ("style_id", "size_pt", "bold", "outline_level")}),
            f"HEADING-{level}")
    for name, code in (("caption_figure", "CAP-FIGURE"),
                       ("caption_table", "CAP-TABLE")):
        role = styles.get(name)
        if role:
            add("captions", f"styles.{name}",
                _format_number({key: role.get(key) for key in
                                ("style_id", "size_pt", "align")}), code)
    captions = spec.get("captions") or {}
    add("captions", "captions.positions",
        _format_number({"figure": captions.get("figure_position"),
                        "table": captions.get("table_position")}), "CAP-POSITION")
    add("captions", "captions.labels", _format_number(captions.get("labels")),
        "CAP-NUMBER")
    references = spec.get("references") or {}
    add("references", "references.style", references.get("style"), "REF-STYLE")
    add("references", "references.hanging_indent_cm",
        references.get("hanging_indent_cm"), "REF-INDENT")
    flags = spec.get("requirements_flags") or {}
    add("structure", "structure.headings",
        "headings preserved and mapped to template levels", "STRUCT-HEADINGS")
    add("fidelity", "fidelity.body", "source text preserved verbatim",
        "FIDELITY-BODY")
    if flags.get("abstract") or styles.get("abstract"):
        add("structure", "requirements_flags.abstract", True, "ABSTRACT")
    if flags.get("keywords") or styles.get("keywords"):
        add("structure", "requirements_flags.keywords", True, "KEYWORDS")
    header_footer = spec.get("header_footer") or {}
    if header_footer.get("headers") or header_footer.get("footers"):
        add("export", "header_footer",
            _format_number(header_footer), "HEADER-FOOTER", method="manual",
            note="headers/footers are preserved from the template package; "
                 "visual placement needs a renderer/Word check")
    return requirements


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)*)\s?(%|°C|cm|mm|pt|mL|µL|mg|g|h|min|s)?")
NUM_CITE_RE = re.compile(r"\[([0-9]+(?:\s*[,\-–]\s*[0-9]+)*)\]")
TERM_RE = re.compile(r"\b[A-Z][A-Za-z0-9\-]{2,}\b")
TERM_STOP = {"The", "This", "That", "These", "Those", "Figure", "Table",
             "Abstract", "Keywords", "References", "Introduction", "Methods",
             "Results", "Discussion", "Conclusion", "Appendix", "Author",
             "Data", "Study", "Here", "However", "Therefore", "Using", "Our",
             "We", "In", "Of", "And", "For", "With", "From", "All", "Not"}


def _norm_text(text):
    return clean_space(str(text)).lower()


def _numbers_in(text):
    return [match.group(0) for match in NUMBER_RE.finditer(str(text))]


def units_from_blocks(blocks, source_name, title=None):
    """Build source content units from verbatim blocks (no rewriting)."""
    paragraphs, headings, captions, numbers, citations = [], [], [], [], []
    index = 0
    if title:
        index = 1
        paragraphs.append({"index": index, "text": clean_space(title)})
    for block in blocks or []:
        kind = block.get("type")
        text = ""
        if kind in ("paragraph", "heading", "quote"):
            text = block.get("text") or ""
        elif kind in ("bullets", "numbers"):
            text = " ".join(block.get("items") or [])
        elif kind == "table":
            text = " ".join(" ".join(row) for row in
                            ([block.get("header") or []] + (block.get("rows") or [])))
            if block.get("caption"):
                captions.append({"kind": "table", "text": block["caption"],
                                 "locator": f"table {len(captions) + 1} caption"})
        elif kind == "figure":
            text = block.get("caption") or "[figure]"
            if block.get("caption"):
                captions.append({"kind": "figure", "text": block["caption"],
                                 "locator": f"figure {len(captions) + 1} caption"})
        if not text:
            continue
        index += 1
        paragraphs.append({"index": index, "text": text})
        if kind == "heading":
            headings.append({"level": block.get("level") or 1, "text": text,
                             "locator": f"paragraph {index}"})
        cap_kind = block.get("caption_kind") or CAPTION_RE.match(text)
        if block.get("role") == "caption" or block.get("caption_kind"):
            match = CAPTION_RE.match(text)
            if match:
                label = match.group(1).lower()
                cap_kind = "figure" if label.startswith(("figure", "fig", "图")) \
                    else "table"
                captions.append({"kind": cap_kind, "text": text,
                                 "locator": f"paragraph {index}"})
        for match in NUM_CITE_RE.finditer(text):
            citations.append({"value": match.group(0), "locator": f"paragraph {index}"})
        for match in NUMBER_RE.finditer(text):
            numbers.append({"value": match.group(1), "unit": match.group(2) or "",
                            "locator": f"paragraph {index}",
                            "context": text[:140]})
    terms = []
    seen_terms = set()
    for paragraph in paragraphs:
        for match in TERM_RE.finditer(paragraph["text"]):
            term = match.group(0)
            if term in TERM_STOP or len(term) < 3 or term.lower() in seen_terms:
                continue
            seen_terms.add(term.lower())
            terms.append({"value": term, "locator": f"paragraph {paragraph['index']}"})
    heading_levels = {}
    for heading in headings:
        level = int(heading.get("level") or 1)
        heading_levels[str(level)] = heading_levels.get(str(level), 0) + 1
    return {
        "source": source_name, "title": title,
        "text": "\n".join(paragraph["text"] for paragraph in paragraphs),
        "paragraphs": paragraphs, "headings": headings, "captions": captions,
        "citations": citations, "numbers": numbers, "terms": terms,
        "counts": {"sections": len(headings), "paragraphs": len(paragraphs),
                   "captions": len(captions),
                   "citations": len({item.get("value") for item in citations}),
                   "numbers": len(numbers), "tables": sum(
                       1 for block in blocks or [] if block.get("type") == "table"),
                   "figures": sum(1 for block in blocks or []
                                  if block.get("type") == "figure"),
                   "heading_levels": heading_levels,
                   "words": sum(len(re.findall(r"\S+", p["text"])) for p in paragraphs)},
    }


def units_from_docx_inventory(inventory, source_name):
    paragraphs = inventory.get("paragraphs") or []
    headings = [{"level": p.get("level"), "text": p.get("text"),
                 "locator": f"paragraph {p.get('index')}"}
                for p in paragraphs if p.get("level")]
    captions = [{"kind": item.get("kind"), "text": item.get("text"),
                 "locator": f"paragraph {item.get('paragraph')}"}
                for item in inventory.get("captions") or []]
    citations = [{"value": f"[{number}]", "locator": "docx citation"}
                 for number in inventory.get("citations") or []]
    numbers = [{"value": item.get("value"), "unit": item.get("unit") or "",
                "locator": f"paragraph {item.get('paragraph')}"}
               for item in (inventory.get("numbers") or [])[:120]]
    terms = []
    seen = set()
    for paragraph in paragraphs:
        for match in TERM_RE.finditer(paragraph.get("text") or ""):
            term = match.group(0)
            if term in TERM_STOP or term.lower() in seen:
                continue
            seen.add(term.lower())
            terms.append({"value": term,
                          "locator": f"paragraph {paragraph.get('index')}"})
    return {"source": source_name, "title": None,
            "text": inventory.get("full_text") or
                    "\n".join(paragraph.get("text") or "" for paragraph in paragraphs),
            "paragraphs": paragraphs,
            "headings": headings, "captions": captions, "citations": citations,
            "numbers": numbers, "terms": terms,
            "counts": dict(inventory.get("counts") or {})}


def build_preserved_facts(source_units, output_inventory,
                          source_name, output_name, limit=90):
    output_paragraphs = output_inventory.get("paragraphs") or []
    output_text = "\n".join(p.get("text") or "" for p in output_paragraphs)
    output_norm = _norm_text(output_text)
    facts = []

    def locate(value):
        target = _norm_text(value)
        if not target:
            return None
        for paragraph in output_paragraphs:
            if target in _norm_text(paragraph.get("text") or ""):
                return f"paragraph {paragraph.get('index')}"
        return None

    def add(kind, value, source_locator, extra=None):
        if len(facts) >= limit:
            return
        fact = {
            "fact_id": f"PF{len(facts) + 1:03d}", "kind": kind, "value": value,
            "source": {"path": source_name, "locator": source_locator},
            "output": {"path": output_name, "locator": locate(value)},
        }
        if extra:
            fact.update(extra)
        fact["status"] = "preserved" if fact["output"]["locator"] else "unverified"
        facts.append(fact)

    for item in (source_units.get("numbers") or [])[:45]:
        value = f"{item.get('value')}{(' ' + item.get('unit')) if item.get('unit') else ''}".strip()
        add("number", value, item.get("locator") or "source line")
    for item in (source_units.get("citations") or [])[:20]:
        add("citation", item.get("value"), item.get("locator") or "source line")
    for item in (source_units.get("captions") or [])[:20]:
        add("caption", item.get("text"), item.get("locator") or "source caption",
            {"caption_kind": item.get("kind")})
    for item in (source_units.get("headings") or [])[:15]:
        add("section", item.get("text"), item.get("locator") or "source heading")
    for item in (source_units.get("terms") or [])[:20]:
        add("term", item.get("value"), item.get("locator") or "source text")
    return facts


def _close(first, second, tolerance):
    try:
        return abs(float(first) - float(second)) <= tolerance
    except (TypeError, ValueError):
        return False


def verify_requirements(requirements, spec, output_check, output_inventory,
                        source_units, fidelity):
    page = spec.get("page") or {}
    expected_margins = page.get("margins_cm") or {}
    actual_margins = output_check.get("margins_cm") or {}
    actual_page = output_check.get("page") or {}
    actual_columns = output_check.get("columns") or {}
    styles_used = output_check.get("styles_used") or {}
    fonts_used = [str(item).lower() for item in output_check.get("fonts_used") or []]
    sizes_used = output_check.get("sizes_used") or []
    caption_counts = output_check.get("caption_counts") or {}
    caption_numbers = output_check.get("caption_numbers") or {}
    references = output_check.get("references") or {}
    reference_entries = references.get("entries") or []
    source_counts = source_units.get("counts") or {}
    body_style = (spec.get("styles") or {}).get("body") or {}
    body_style_id = body_style.get("style_id") or "Normal"
    output_styles = output_inventory.get("styles") or {}
    normal_effective = (output_styles.get(body_style_id) or {}).get("effective") or {}
    output_levels = {}
    for record in output_inventory.get("paragraphs") or []:
        level = record.get("level")
        if level:
            output_levels[int(level)] = output_levels.get(int(level), 0) + 1
    results = []
    for requirement in requirements:
        field = requirement.get("field")
        status, actual, evidence = "manual_check", None, "not automatically checked"
        if field == "page.size":
            actual = {"name": actual_page.get("name"),
                      "width_twips": actual_page.get("width_twips"),
                      "height_twips": actual_page.get("height_twips")}
            ok = page.get("width_twips") and actual_page.get("width_twips") and \
                _close(page.get("width_twips"), actual_page.get("width_twips"), 40) and \
                _close(page.get("height_twips"), actual_page.get("height_twips"), 40)
            status = "pass" if ok else "missing"
            evidence = f"word/document.xml pgSz={actual}"
        elif field == "page.margins_cm":
            actual = actual_margins
            keys = [key for key, value in expected_margins.items()
                    if value is not None and key != "gutter"]
            ok = bool(keys) and all(_close(expected_margins[key], actual_margins.get(key), 0.06)
                                    for key in keys)
            status = "pass" if ok else "missing"
            evidence = f"word/document.xml pgMar={actual}"
        elif field == "page.columns":
            actual = actual_columns
            expected = (page.get("columns") or {}).get("count") or 1
            ok = int(actual.get("count") or 1) == int(expected)
            status = "pass" if ok else "missing"
            evidence = f"word/document.xml cols num={actual.get('count')}"
        elif field == "fonts.body_ascii":
            expected = str((spec.get("fonts") or {}).get("body_ascii") or "").lower()
            actual = output_check.get("fonts_used")
            ok = not expected or expected in fonts_used
            status = "pass" if ok else "manual_check"
            evidence = f"run/style fonts used={actual[:12]}"
        elif field == "fonts.body_size_pt":
            expected = (spec.get("fonts") or {}).get("body_size_pt")
            actual = sizes_used
            ok = any(_close(expected, value, 0.6) for value in sizes_used)
            status = "pass" if ok else "manual_check"
            evidence = f"font sizes used={actual[:12]}"
        elif field == "fonts.body_line_spacing":
            expected = (spec.get("styles", {}).get("body") or {}).get("line_spacing")
            actual = normal_effective.get("line_spacing")
            ok = expected is None or _close(expected, actual, 0.05)
            status = "pass" if ok else "missing"
            evidence = f"style {body_style_id} line spacing={actual}"
        elif field == "styles.title":
            target = (spec.get("styles", {}).get("title") or {}).get("style_id")
            actual = target if target in styles_used else None
            if source_counts.get("sections", 0) == 0 and not actual:
                status, evidence = "not_applicable", "no title paragraph in source"
            else:
                status = "pass" if actual else "manual_check"
                evidence = f"title style used={target} occurrences={styles_used.get(target, 0)}"
        elif field.startswith("styles.heading_"):
            level = int(field.rsplit("_", 1)[1])
            target = (spec.get("styles", {}).get(field) or {}).get("style_id")
            actual = styles_used.get(target, 0)
            levels = source_counts.get("heading_levels")
            if levels is None:
                levels = {}
                for item in source_units.get("headings") or []:
                    key = str(int(item.get("level") or 1))
                    levels[key] = levels.get(key, 0) + 1
            source_level = int(levels.get(str(level), 0))
            if not source_level:
                status = "not_applicable"
                evidence = f"source has no level-{level} headings"
            else:
                status = "pass" if actual or output_levels.get(level) else "missing"
                evidence = (f"style {target} occurrences={actual}; "
                            f"source level {level}={source_level}; "
                            f"outline level {level} paragraphs={output_levels.get(level, 0)}")
        elif field.startswith("styles.caption_"):
            kind = "figure" if field.endswith("figure") else "table"
            source_count = sum(1 for item in source_units.get("captions") or []
                               if item.get("kind") == kind)
            actual = caption_counts.get(kind, 0)
            if source_count == 0 and actual == 0:
                status, evidence = "not_applicable", f"no {kind} captions in source"
            else:
                status = "pass" if actual >= source_count and actual > 0 else "missing"
                evidence = f"{kind} captions source={source_count} output={actual}"
        elif field == "captions.positions":
            positions = [item.get("position") for item in output_check.get("captions") or []]
            known = [item for item in positions if item]
            expected = {"figure": (spec.get("captions") or {}).get("figure_position"),
                        "table": (spec.get("captions") or {}).get("table_position")}
            ok = True
            for item in output_check.get("captions") or []:
                if item.get("position") and expected.get(item.get("kind")):
                    if item["position"] != expected[item["kind"]]:
                        ok = False
            if not known:
                status, evidence = "manual_check", "caption positions need a visual check"
            else:
                status = "pass" if ok else "missing"
                evidence = f"caption positions={known}; expected={expected}"
        elif field == "captions.labels":
            expected_labels = (spec.get("captions") or {}).get("labels") or {}
            ok = True
            for kind, numbers in caption_numbers.items():
                if numbers and expected_labels.get(kind):
                    label = expected_labels[kind].lower()
                    if kind == "figure" and label not in ("figure", "fig.", "fig", "图"):
                        ok = False
                    if kind == "table" and label not in ("table", "表"):
                        ok = False
            status = "pass" if ok else "manual_check"
            evidence = f"caption labels={expected_labels}; numbers={caption_numbers}"
        elif field == "references.style":
            actual = output_check.get("reference_style")
            expected = (spec.get("references") or {}).get("style")
            if not reference_entries:
                status = "not_applicable" if not expected else "missing"
                evidence = "no reference entries detected in output"
            elif expected in (None, "unknown", "template"):
                status, evidence = "pass", f"reference entries={len(reference_entries)}"
            else:
                status = "pass" if actual == expected else "manual_check"
                evidence = f"detected reference style={actual}; expected={expected}"
        elif field == "references.hanging_indent_cm":
            expected = (spec.get("references") or {}).get("hanging_indent_cm")
            if not reference_entries:
                status, evidence = "not_applicable", "no reference entries"
            else:
                hints = [entry.get("indent", {}).get("hanging_cm") or
                         entry.get("indent", {}).get("left_cm")
                         for entry in reference_entries]
                ok = any(_close(expected, value, 0.12) for value in hints if value is not None)
                status = "pass" if ok else "manual_check"
                evidence = f"reference indents={hints[:6]}; expected hanging={expected}"
        elif field == "structure.headings":
            source_headings = source_counts.get("sections", 0)
            output_headings = sum(output_levels.values())
            if not source_headings:
                status, evidence = "not_applicable", "source has no headings"
            else:
                status = "pass" if output_headings >= source_headings else "missing"
                evidence = f"headings source={source_headings} output={output_headings}"
        elif field == "fidelity.body":
            similarity = (fidelity or {}).get("similarity")
            status = "pass" if (similarity or 0) >= 0.98 else "missing"
            actual = similarity
            evidence = (f"text similarity={similarity}; "
                        f"source_units={fidelity.get('source_units')}; "
                        f"output_units={fidelity.get('output_units')}")
        elif field == "requirements_flags.abstract":
            source_has = any(re.search(r"abstract|摘要", paragraph.get("text") or "",
                                       re.IGNORECASE)
                             for paragraph in source_units.get("paragraphs") or [])
            found = bool(re.search(r"abstract|摘要", output_check.get("body_text") or "",
                                   re.IGNORECASE))
            if not source_has and not found:
                status = "not_applicable"
                evidence = "source has no abstract; no text was invented"
            else:
                status = "pass" if found else "missing"
                evidence = "abstract heading/text found in output" if found else \
                    "abstract heading/text not found"
            actual = found
        elif field == "requirements_flags.keywords":
            source_has = any(re.search(r"keywords?|关键词", paragraph.get("text") or "",
                                       re.IGNORECASE)
                             for paragraph in source_units.get("paragraphs") or [])
            found = bool(re.search(r"keywords?|关键词", output_check.get("body_text") or "",
                                   re.IGNORECASE))
            if not source_has and not found:
                status = "not_applicable"
                evidence = "source has no keywords; no text was invented"
            else:
                status = "pass" if found else "missing"
                evidence = "keywords found in output" if found else "keywords not found"
            actual = found
        elif field == "header_footer":
            status = "manual_check"
            actual = spec.get("header_footer")
            evidence = "headers/footers live in the template package; verify visually"
        results.append({
            **requirement, "status": status, "actual": actual,
            "evidence": evidence, "artifact": "manuscript.docx"})
    return results


def summarize(requirements):
    summary = {"total": len(requirements), "pass": 0, "fixed": 0, "missing": 0,
               "not_applicable": 0, "manual_check": 0}
    for requirement in requirements:
        status = requirement.get("status") or "manual_check"
        summary[status] = summary.get(status, 0) + 1
    return summary


def fidelity_record(source_text, output_text, source_units, output_inventory,
                    source_path, output_path):
    import difflib
    source_norm = clean_space(source_text)[:200000]
    output_norm = clean_space(output_text)[:200000]
    ordered_ratio = 1.0
    if source_norm or output_norm:
        ordered_ratio = round(difflib.SequenceMatcher(
            None, source_norm, output_norm).ratio(), 4)
    # Caption repositioning legitimately changes order, so content fidelity is
    # measured on the token multiset; the ordered ratio is reported separately.
    source_tokens = re.findall(r"\S+", source_norm)
    output_tokens = re.findall(r"\S+", output_norm)
    ratio = 1.0
    if source_tokens or output_tokens:
        ratio = round(difflib.SequenceMatcher(
            None, sorted(source_tokens), sorted(output_tokens)).ratio(), 4)
    source_counts = source_units.get("counts") or {}
    output_counts = output_inventory.get("counts") or {}
    units = {}
    for name in ("sections", "tables", "figures", "citations", "equations"):
        source_value = source_counts.get(name, 0) or 0
        output_value = output_counts.get(name, 0) or 0
        units[name] = {"source": source_value, "output": output_value,
                       "status": "preserved" if output_value >= source_value
                       else "reduced"}
    return {
        "source": source_path, "output": output_path,
        "similarity": ratio, "ordered_similarity": ordered_ratio,
        "source_units": len(source_norm), "output_units": len(output_norm),
        "source_words": source_counts.get("words"),
        "output_words": output_counts.get("words"),
        "units": units,
        "preserved": ratio >= 0.98,
    }


def build_evidence_map(requirements, output_name="manuscript.docx"):
    evidence_map = []
    for requirement in requirements:
        evidence_map.append({
            "requirement_id": requirement.get("id"),
            "area": requirement.get("area"),
            "method": requirement.get("method") or "auto",
            "artifact": output_name,
            "locator": "word/document.xml + word/styles.xml",
            "source": requirement.get("source"),
            "check_code": requirement.get("check_code"),
            "evidence": requirement.get("evidence"),
            "status": requirement.get("status"),
        })
    return evidence_map


def build_change_log(spec, template_name, source_name, output_name,
                     font_substitutions, body_override):
    changes = []
    page = spec.get("page") or {}
    changes.append({
        "section": "document", "type": "page",
        "before": f"source/template package ({source_name or 'unknown'})",
        "after": (f"page={page.get('size')} {page.get('width_cm')}x{page.get('height_cm')} cm; "
                  f"margins={_format_number(page.get('margins_cm'))}; "
                  f"columns={(page.get('columns') or {}).get('count')}"),
        "reason": "target template sectPr",
        "artifact": output_name})
    fonts = spec.get("fonts") or {}
    body = (spec.get("styles") or {}).get("body") or {}
    changes.append({
        "section": "body", "type": "font",
        "before": "source direct formatting / template defaults",
        "after": (f"font={fonts.get('body_ascii')}; size={fonts.get('body_size_pt')} pt; "
                  f"line spacing={fonts.get('body_line_spacing')}"),
        "reason": "template docDefaults + Normal style + organizer font revision",
        "artifact": output_name})
    for role_name, role in (spec.get("styles") or {}).items():
        if not role_name.startswith("heading_") and role_name not in (
                "title", "caption_figure", "caption_table", "reference"):
            continue
        changes.append({
            "section": role_name, "type": "style",
            "before": "source style or direct formatting",
            "after": (f"styleId={role.get('style_id')}; size={role.get('size_pt')} pt; "
                      f"bold={role.get('bold')}; font={role.get('font')}"),
            "reason": f"mapped to template {role_name.replace('_', ' ')} style",
            "artifact": output_name})
    references = spec.get("references") or {}
    changes.append({
        "section": "references", "type": "style",
        "before": "source reference formatting",
        "after": (f"style={references.get('style')}; "
                  f"hanging indent={references.get('hanging_indent_cm')} cm; "
                  f"size={references.get('size_pt')} pt"),
        "reason": "template reference style + guide",
        "artifact": output_name})
    captions = spec.get("captions") or {}
    changes.append({
        "section": "captions", "type": "style",
        "before": "source caption formatting",
        "after": (f"labels={_format_number(captions.get('labels'))}; "
                  f"figure={captions.get('figure_position')}; "
                  f"table={captions.get('table_position')}; sequential numbering"),
        "reason": "template caption styles + position conventions",
        "artifact": output_name})
    for substitution in font_substitutions or []:
        changes.append({
            "section": substitution.get("scope") or "fonts", "type": "font_substitution",
            "before": substitution.get("requested"),
            "after": substitution.get("used"),
            "reason": substitution.get("reason"),
            "artifact": output_name})
    changes.append({
        "section": "body", "type": "text_source",
        "before": source_name or "no source",
        "after": ("model-supplied body (adapted text)" if body_override
                  else "source body preserved verbatim; formatting only"),
        "reason": "avoid extractive baseline; body text is never synthesised by scripts",
        "artifact": output_name})
    return changes


def build_result(case, scan, spec, spec_path, source_path, deliverable,
                 artifacts, requirements, summary, fidelity,
                 font_substitutions, change_log, preserved_facts, evidence_map,
                 uncertainty, unmet, source_units, output_check, body_mode,
                 commands):
    status = "completed"
    blocking = []
    if not source_path:
        blocking.append("no manuscript source found")
    if not scan.get("template"):
        blocking.append("no DOCX template found")
    elif (spec.get("meta") or {}).get("template_readable") is False:
        blocking.append("template DOCX could not be read")
    if summary.get("missing"):
        blocking.append(f"{summary['missing']} requirement(s) missing")
    if not (output_check or {}).get("ok"):
        blocking.append("deliverable DOCX failed read-back")
    if (fidelity or {}).get("similarity") is not None and \
            (fidelity or {}).get("similarity") < 0.90:
        blocking.append("body text similarity below 0.90")
    if blocking:
        status = "partial" if source_path else "input_error"
    target = {
        "journal": (case.get("journal") or (spec.get("meta") or {}).get("journal")),
        "template_id": (case.get("template_id") or
                        (spec.get("meta") or {}).get("template_id")),
        "style": (spec.get("references") or {}).get("style"),
        "language": (spec.get("meta") or {}).get("language"),
        "output_format": (deliverable or {}).get("format"),
    }
    result_payload = {
        "case_id": case.get("case_id") or slug(Path(source_path or "case").stem),
        "task_family": "format-adaptation",
        "status": status,
        "generated_at": utc_now(),
        "target": target,
        "deliverable": deliverable,
        "preserved_facts": preserved_facts,
        "evidence_map": evidence_map,
        "change_log": change_log,
        "artifacts": [item for item in artifacts if item],
        "requirements": requirements,
        "summary": summary,
        "fidelity": fidelity,
        "format_spec": {
            "path": "format-spec.json" if spec_path else None,
            "source": (spec.get("meta") or {}).get("source"),
            "template": (spec.get("meta") or {}).get("template"),
            "template_sha256": (spec.get("meta") or {}).get("template_sha256"),
            "guide": (spec.get("meta") or {}).get("guide"),
            "recognized_requirements": len(requirements),
            "conflicts": (spec.get("meta") or {}).get("conflicts") or [],
            "unrecognized": (spec.get("meta") or {}).get("unrecognized") or [],
        },
        "output_contract": case.get("output_contract") or {},
        "official_contract": {
            "profile": "writing",
            "declared_fields": case.get("declared_fields") or [],
            "fields_present": [key for key in
                               ("deliverable", "preserved_facts", "evidence_map",
                                "change_log") if key in
                               ("deliverable", "preserved_facts", "evidence_map",
                                "change_log")],
        },
        "font_substitution": font_substitutions,
        "source_inventory": {
            "files": scan.get("files") or [],
            "manuscript": source_path,
            "template": scan.get("template"),
            "guide": scan.get("guide"),
            "counts": (source_units or {}).get("counts") or {},
        },
        "docx_check": output_check,
        "body_mode": body_mode,
        "unmet": unmet,
        "uncertainty": uncertainty,
        "commands": commands,
    }
    for field in case.get("declared_fields") or []:
        if field not in result_payload:
            result_payload[field] = None
            result_payload["uncertainty"].append({
                "kind": "contract_field_unavailable",
                "detail": f"output_contract field '{field}' was not produced by "
                          f"this format-adaptation run",
                "impact": "the field is null instead of fabricated"})
    return result_payload


def render_report_md(result):
    lines = [
        "# 格式适配报告（Format Adaptation Report）", "",
        f"- case_id: `{result.get('case_id')}`",
        f"- status: `{result.get('status')}`",
        f"- template: `{(result.get('format_spec') or {}).get('template')}`",
        f"- deliverable: `{(result.get('deliverable') or {}).get('path')}`",
        f"- body mode: `{result.get('body_mode')}`", "",
        "## 实测格式", "",
        "| 项目 | 结果 |", "|---|---|",
    ]
    docx_check = result.get("docx_check") or {}
    page = docx_check.get("page") or {}
    margins = docx_check.get("margins_cm") or {}
    lines.append(f"| 页面 | {page.get('name')} "
                 f"({page.get('width_twips')}x{page.get('height_twips')} twips) |")
    lines.append(f"| 页边距 | {_format_number(margins)} |")
    lines.append(f"| 栏数 | {(docx_check.get('columns') or {}).get('count')} |")
    lines.append(f"| 段落 | {docx_check.get('paragraphs')} |")
    lines.append(f"| 题注 | {_format_number(docx_check.get('caption_counts'))} |")
    lines.append(f"| 参考文献条目 | {(docx_check.get('references') or {}).get('entries') and len(docx_check['references']['entries']) or 0} |")
    lines += ["", "## 要求逐项结果", "",
              "| ID | 区域 | 要求 | 状态 | 证据 |", "|---|---|---|---|---|"]
    for requirement in result.get("requirements") or []:
        lines.append(
            f"| {requirement.get('id')} | {requirement.get('area')} | "
            f"{requirement.get('field')} | {requirement.get('status')} | "
            f"{str(requirement.get('evidence') or '').replace('|', '/')[:160]} |")
    summary = result.get("summary") or {}
    lines += ["", "## 汇总", "", f"```json\n{json.dumps(summary, ensure_ascii=False)}\n```", ""]
    fidelity = result.get("fidelity") or {}
    lines += [f"- 文本保真相似度: `{fidelity.get('similarity')}`",
              f"- 未满足项: `{len(result.get('unmet') or [])}`",
              f"- 不确定项: `{len(result.get('uncertainty') or [])}`", ""]
    return "\n".join(lines).rstrip() + "\n"





# ---------------------------------------------------------------------------
# CLI (standalone spec extraction)
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Template recognition and spec merge")
    parser.add_argument("--template", default=None)
    parser.add_argument("--guide", default=None)
    parser.add_argument("--source", default=None)
    parser.add_argument("--out-spec", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    spec = extract_template_spec(args.template) if args.template else default_spec()
    if args.guide:
        guide_text = read_text(args.guide)
        guide = parse_guide_text(guide_text, Path(args.guide).name)
        guide["guide_name"] = Path(args.guide).name
        spec = merge_spec(spec, guide, {"case_id": None})
    else:
        spec = merge_spec(spec, {"guide_name": None}, {"case_id": None})
    substitutions = apply_font_revision(spec)
    if args.source:
        spec["meta"]["source_manuscript"] = args.source
    if args.out_spec:
        write_json(args.out_spec, spec)
    payload = {
        "ok": True, "template": args.template, "guide": args.guide,
        "page": spec.get("page"), "fonts": spec.get("fonts"),
        "requirements": len(requirement_catalog(spec)),
        "conflicts": (spec.get("meta") or {}).get("conflicts") or [],
        "font_substitutions": substitutions,
        "out_spec": args.out_spec,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
