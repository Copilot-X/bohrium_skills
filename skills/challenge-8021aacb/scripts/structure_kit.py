#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task-specific structure kit: text helpers + chapter/paragraph map builder.

No third-party packages, no network. The track stores scripts as ``*.py.md``
for the local encryption workaround; inside the submitted package they are
real ``*.py`` files. ``main.py`` bootstraps the wrapped layout for local runs.

The map builder is a locator, not a verdict: every signal must be confirmed
against the manuscript before it becomes a finding.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.7.0"
TOOL = "structure-review-engine"

TEXT_EXTS = {".md", ".markdown", ".txt", ".tex", ".latex"}
DOCX_EXTS = {".docx"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif",
              ".tiff", ".svg"}
MANUSCRIPT_EXTS = TEXT_EXTS | DOCX_EXTS | PDF_EXTS
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".idea", ".vscode",
             "output", "work", ".pytest_cache"}
TASK_METADATA_NAMES = {
    "case.json", "task.md", "output-contract.md", "output_contract.md",
    "schema.json",
}

CJK_RE = re.compile(r"[㐀-鿿]")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.])\s+(?=[A-Z0-9\"“(])")
CITE_RE = re.compile(
    r"\[\d+(?:\s*[,\-–]\s*\d+)*\]"
    r"|\((?:[A-Z][A-Za-z\-']+(?:\s+et\s+al\.?)?,\s*(?:19|20)\d{2}[a-z]?"
    r"(?:;\s*[^)]*)?)\)"
    r"|\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])?\{[^}]*\}"
    r"|\[@[A-Za-z0-9_:\-]+\]")
NUMBER_WITH_UNIT = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?\d+(?:[.,]\d+)?\s*"
    r"(?:%|‰|°C|°|h|min|s|ms|mg|g|kg|mL|L|mm|cm|mM|mmol|μmol|nmol|M|"
    r"Pa|kPa|MPa|bar|atm|Hz|kHz|MHz|V|mA|W|kW|dB|rpm|bp|kb|Da|kDa|"
    r"分|岁|人|名|次|周|月|年|天|小时|分钟|秒)")
BARE_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d{2,}(?:\.\d+)?(?![A-Za-z0-9_])")
DECIMAL = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d+\.\d+(?![A-Za-z0-9_])")
STAT_TOKEN = re.compile(
    r"(?:n|N|β|γ|ρ|r|R|OR|HR|RR)\s*=\s*[-+]?\d+(?:\.\d+)?"
    r"|p\s*[<>=]\s*0?\.\d+"
    r"|95\s*%\s*(?:CI|置信区间)\s*[:：]?\s*[-+]?\d+(?:\.\d+)?\s*"
    r"(?:至|to|~|-)\s*[-+]?\d+(?:\.\d+)?", re.I)
CONDITION_WORDS = (
    "随机", "对照", "双盲", "单中心", "多中心", "横断面", "纵向", "队列",
    "实验组", "对照组", "剂量", "浓度", "温度", "压力", "样本", "纳入标准",
    "排除标准", "randomized", "controlled", "blinded", "cross-sectional",
    "longitudinal", "cohort", "dose", "concentration", "temperature",
    "pressure", "sample",
)


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_text_file(path, limit=None):
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    if limit is not None and len(text) > limit:
        return text[:limit]
    return text


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def rel_posix(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def norm_text(text):
    text = str(text or "").replace("​", "").replace("﻿", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def norm_label(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = (text.replace("“", '"').replace("”", '"')
                .replace("‘", "'").replace("’", "'")
                .replace("–", "-").replace("—", "-").replace("−", "-"))
    text = re.sub(r"[`*_>#]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def compact(text):
    text = norm_label(text)
    return re.sub(r"[^\w㐀-鿿]+", "", text)


def split_sentences(text):
    text = norm_text(text)
    if not text:
        return []
    return [part.strip() for part in SENT_SPLIT.split(text) if part.strip()]


def first_sentence(text, limit=180):
    for sentence in split_sentences(text):
        if sentence:
            return sentence[:limit]
    return norm_text(text)[:limit]


def word_count(text):
    return len(CJK_RE.findall(text)) + len(WORD_RE.findall(text))


def detect_language(text):
    cjk = len(CJK_RE.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return "zh" if cjk >= max(1, latin // 3) else "en"


def quantity_tokens(text):
    """Meaningful numeric/statistical tokens used as fact anchors."""
    tokens = set()
    for pattern in (STAT_TOKEN, NUMBER_WITH_UNIT, DECIMAL, BARE_NUMBER):
        for match in pattern.finditer(str(text or "")):
            token = re.sub(r"\s+", "", match.group(0)).lower()
            tokens.add(token)
    return tokens


def citation_tokens(text):
    return {re.sub(r"\s+", "", match.group(0)).lower()
            for match in CITE_RE.finditer(str(text or ""))}


def guard_tokens(text):
    """Tokens that an automatic edit must not add, drop or alter."""
    return quantity_tokens(text) | citation_tokens(text)


def condition_tokens(text):
    lowered = norm_label(text)
    found = set()
    for word in CONDITION_WORDS:
        if word.lower() in lowered:
            found.add(word.lower())
    return found


def contains_any(text, words):
    lowered = norm_label(text)
    return any(str(word).lower() in lowered for word in words)


def normalize_quote_for_match(text):
    """Whitespace/punctuation-light form used for verbatim containment."""
    return re.sub(r"\s+", "", norm_label(text))


def quote_in(source, quote):
    if not quote:
        return False
    return normalize_quote_for_match(quote) in normalize_quote_for_match(source)


def safe_slug(name, limit=80):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name))[:limit] or "file"


def truncate(text, limit=240):
    text = norm_text(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ---------------------------------------------------------------------------
# Structure map: chapters, sections, paragraphs and observable signals
# ---------------------------------------------------------------------------

CJK = re.compile(r"[\u3400-\u9fff]")
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
SENTENCE_END = re.compile(r"(?<=[。！？!?])\s*|(?<=[.])\s+(?=[A-Z0-9\"“(])")
NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?\s*%?")
CITE_BRACKET = re.compile(r"\[\d+(?:\s*[,\-–]\s*\d+)*\]")
CITE_PAREN = re.compile(r"\((?:[A-Z][A-Za-z\-']+(?:\s+et\s+al\.?)?,\s*"
                        r"(?:19|20)\d{2}[a-z]?(?:;\s*[^)]*)?)\)")
CITE_TEX = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])?\{[^}]*\}")
FIG_REF = re.compile(
    r"(?:图|表|Figure|Fig\.?|Table)\s*([0-9]+(?:[.\-–][0-9]+)?)", re.I)
FIG_DEF = re.compile(
    r"^\s*(?:!\[[^\]]*\]\([^)]*\)|\\caption\{|(?:\*\*)?\s*"
    r"(?:图|表|Figure|Fig\.?|Table)\s*[0-9]+(?:[.\-–][0-9]+)?)",
    re.I)

MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
TEX_HEADING = re.compile(
    r"^\s*\\(chapter|section|subsection|subsubsection)\*?\s*\{([^{}]*)\}\s*$")
TEX_ABSTRACT_BEGIN = re.compile(r"^\s*\\begin\{abstract\}", re.I)
TEX_ABSTRACT_END = re.compile(r"^\s*\\end\{abstract\}", re.I)
TEX_STRUCTURAL = re.compile(
    r"^\s*\\(?:documentclass|usepackage|begin\{document\}|end\{document\}|"
    r"maketitle|title|author|date|bibliographystyle|bibliography\{|"
    r"tableofcontents|newpage|clearpage|pagestyle|thispagestyle|"
    r"setlength|renewcommand|newcommand|input|include)")
CN_CHAPTER = re.compile(
    r"^\s*第\s*([一二三四五六七八九十百零〇\d]+)\s*章\s*[、.．:：]?\s*(.*)$")
NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+){0,2})[.、．]?\s+([^\s].{0,100})$")
KNOWN_HEADING = re.compile(
    r"^\s*(abstract|摘要|introduction|引言|绪论|background|related\s+work|"
    r"materials?\s+and\s+methods?|methods?|方法|results?|结果|findings?|"
    r"discussion|讨论|conclusions?|结论|结语|references|bibliography|"
    r"参考文献|appendix|附录)\s*$", re.I)

FUNCTION_RULES = [
    ("abstract", re.compile(r"abstract|摘要", re.I)),
    ("introduction", re.compile(r"introduction|引言|绪论|background|研究背景", re.I)),
    ("literature", re.compile(r"related\s+work|literature|文献|综述|理论", re.I)),
    ("methods", re.compile(r"materials?\s+and\s+methods?|methods?|方法|"
                           r"实验|数据来源|研究设计|methodology", re.I)),
    ("results", re.compile(r"results?|findings?|结果|发现|分析结果", re.I)),
    ("discussion", re.compile(r"discussion|讨论", re.I)),
    ("conclusion", re.compile(r"conclusions?|结论|结语|总结", re.I)),
    ("references", re.compile(r"references|bibliography|参考文献", re.I)),
    ("appendix", re.compile(r"appendix|附录", re.I)),
]
FUNCTION_ORDER = {
    "abstract": 0, "introduction": 1, "literature": 2, "methods": 3,
    "results": 4, "discussion": 5, "conclusion": 6,
}
CORE_FUNCTIONS = ("introduction", "methods", "results", "discussion", "conclusion")
BODY_EXCLUDED = {"references", "appendix"}



def docx_blocks(path):
    """Return [(text, style)] for each paragraph, in document order."""
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    out = []
    for paragraph in root.findall(".//w:body/w:p", ns):
        texts = []
        for node in paragraph.findall(".//w:t", ns):
            if node.text:
                texts.append(node.text)
        text = norm_text("".join(texts))
        style = ""
        style_node = paragraph.find("./w:pPr/w:pStyle", ns)
        if style_node is not None:
            style = style_node.get(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val",
                "") or ""
        out.append((text, style))
    return out


def docx_heading_level(text, style):
    if not text:
        return None
    lowered = style.lower().replace(" ", "")
    if "heading" in lowered or "标题" in lowered:
        match = re.search(r"(\d+)", lowered)
        if match:
            return min(6, max(1, int(match.group(1))))
        return 1
    if lowered in {"title", "标题"}:
        return 1
    return None


def markdown_blocks(lines):
    blocks = []
    fence = False
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = not fence
            index += 1
            continue
        if fence:
            index += 1
            continue
        match = MD_HEADING.match(line)
        if match:
            blocks.append({"kind": "heading", "level": len(match.group(1)),
                           "title": norm_text(match.group(2)), "raw": line})
            index += 1
            continue
        # Setext headings: title line followed by === or ---.
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if stripped and re.match(r"^=+$", next_line):
            blocks.append({"kind": "heading", "level": 1,
                           "title": norm_text(stripped), "raw": line})
            index += 2
            continue
        if stripped and re.match(r"^-{3,}$", next_line) and len(stripped) <= 100:
            blocks.append({"kind": "heading", "level": 2,
                           "title": norm_text(stripped), "raw": line})
            index += 2
            continue
        blocks.append({"kind": "text", "text": norm_text(line)})
        index += 1
    return blocks


def tex_blocks(lines):
    blocks = []
    in_abstract = False
    for line in lines:
        stripped = line.strip()
        if TEX_STRUCTURAL.match(stripped):
            continue
        if TEX_ABSTRACT_BEGIN.match(stripped):
            blocks.append({"kind": "heading", "level": 1, "title": "Abstract",
                           "raw": stripped})
            in_abstract = True
            continue
        if TEX_ABSTRACT_END.match(stripped):
            in_abstract = False
            continue
        match = TEX_HEADING.match(stripped)
        if match:
            command, title = match.group(1), norm_text(match.group(2))
            level = {"chapter": 1, "section": 2, "subsection": 3,
                     "subsubsection": 4}[command]
            blocks.append({"kind": "heading", "level": level, "title": title,
                           "raw": stripped})
            continue
        if in_abstract and stripped:
            blocks.append({"kind": "text", "text": strip_tex(stripped)})
        else:
            blocks.append({"kind": "text", "text": strip_tex(stripped)})
    return blocks


def strip_tex(text):
    text = re.sub(r"\\(?:cite[a-zA-Z]*\*?(?:\[[^\]]*\])?|ref|label|"
                  r"footnote|url)\{[^{}]*\}", " ", text)
    text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", " ", text)
    text = text.replace("{", " ").replace("}", " ").replace("$", " ")
    return norm_text(text)


def plain_blocks(lines):
    blocks = []
    for line in lines:
        raw = line.rstrip()
        stripped = raw.strip()
        match = CN_CHAPTER.match(stripped)
        if match:
            blocks.append({"kind": "heading", "level": 1,
                           "title": norm_text(match.group(2) or match.group(1)),
                           "number": match.group(1), "raw": raw})
            continue
        match = NUMBERED.match(stripped)
        if match and not stripped.startswith(("-", "*", "+", "(")):
            number, title = match.group(1), norm_text(match.group(2))
            level = number.count(".") + 1
            if len(title) <= 100 and not re.search(r"[。！？!?；;]$", title):
                known = KNOWN_HEADING.match(title)
                if level >= 2 or known or title[:1].isupper() or CJK.match(title[:1] or ""):
                    blocks.append({"kind": "heading", "level": min(level, 6),
                                   "title": title, "number": number, "raw": raw})
                    continue
        match = KNOWN_HEADING.match(stripped)
        if match:
            blocks.append({"kind": "heading", "level": 1,
                           "title": norm_text(match.group(1)), "raw": raw})
            continue
        blocks.append({"kind": "text", "text": norm_text(raw)})
    return blocks


def coalesce_paragraphs(blocks):
    """Merge consecutive text blocks separated only by a single newline.

    Plain text and LaTeX commonly wrap one paragraph across lines. Blank lines
    are the authoritative paragraph boundary; markdown list/table lines are kept
    separate to preserve their identity.
    """
    out = []
    buffer = []
    for block in blocks:
        if block["kind"] == "heading":
            if buffer:
                out.append({"kind": "text", "text": norm_text(" ".join(buffer))})
                buffer = []
            out.append(block)
            continue
        text = block.get("text", "")
        if not text:
            if buffer:
                out.append({"kind": "text", "text": norm_text(" ".join(buffer))})
                buffer = []
            continue
        if re.match(r"^(?:[-*+]|\d+[.)])\s+|^\|", text):
            if buffer:
                out.append({"kind": "text", "text": norm_text(" ".join(buffer))})
                buffer = []
            out.append({"kind": "text", "text": text, "list_item": True})
            continue
        buffer.append(text)
    if buffer:
        out.append({"kind": "text", "text": norm_text(" ".join(buffer))})
    return out


def parse_blocks(path):
    suffix = path.suffix.lower()
    if suffix == ".docx":
        raw = docx_blocks(path)
        blocks = []
        for text, style in raw:
            level = docx_heading_level(text, style)
            if level:
                blocks.append({"kind": "heading", "level": level,
                               "title": text, "raw": text})
            elif text:
                blocks.append({"kind": "text", "text": text})
        return blocks, "docx"
    if suffix == ".pdf":
        raise ValueError(
            "PDF is not parsed by this stdlib tool; convert it to .txt with "
            "the platform pdf-text-tool first")
    if suffix not in {".md", ".markdown", ".txt", ".tex", ".latex"}:
        raise ValueError("unsupported manuscript format: %s" % suffix)
    text = read_text_file(path)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if suffix in {".tex", ".latex"}:
        return coalesce_paragraphs(tex_blocks(lines)), "latex"
    if suffix in {".md", ".markdown"}:
        return coalesce_paragraphs(markdown_blocks(lines)), "markdown"
    if any(MD_HEADING.match(line) for line in lines):
        return coalesce_paragraphs(markdown_blocks(lines)), "text"
    return coalesce_paragraphs(plain_blocks(lines)), "text"


def guess_function(title):
    for name, pattern in FUNCTION_RULES:
        if pattern.search(title or ""):
            return name
    return "unknown"


def heading_number(title, fallback):
    match = re.match(r"^\s*(\d+(?:\.\d+){0,2})(?:[.、．]|\s)", title or "")
    return match.group(1) if match else fallback


def paragraph_signals(text):
    refs = sorted(set(FIG_REF.findall(text)))
    defs = sorted(set(FIG_REF.findall(text))) if FIG_DEF.match(text) else []
    return {
        "numbers": len(NUM.findall(text)),
        "citations": (len(CITE_BRACKET.findall(text))
                      + len(CITE_PAREN.findall(text))
                      + len(CITE_TEX.findall(text))),
        "figure_refs": refs,
        "table_refs": defs,
    }


def new_chapter(index, title="Front matter", number="0", level=1):
    return {
        "chapter_id": "C%d" % index,
        "number": number,
        "title": title,
        "level": level,
        "function": guess_function(title),
        "word_count": 0,
        "proportion": 0.0,
        "paragraphs": [],
        "sections": [],
    }


def new_section(number, title, level=2):
    return {
        "section_id": number,
        "title": title,
        "level": level,
        "word_count": 0,
        "paragraphs": [],
        "subsections": [],
    }


def normalize_heading_levels(blocks):
    """If a document has no level-1 heading, promote its top heading level."""
    levels = [block["level"] for block in blocks
              if block.get("kind") == "heading"]
    if levels and 1 not in levels:
        shift = min(levels) - 1
        for block in blocks:
            if block.get("kind") == "heading":
                block["level"] = max(1, block["level"] - shift)
    return blocks


def build_map(path, discovery="explicit"):
    blocks, fmt = parse_blocks(path)
    blocks = normalize_heading_levels(blocks)
    chapters = []
    current_chapter = None
    current_section = None
    heading_count = 0
    chapter_heading_count = 0
    heading_levels = []
    duplicate_titles = []
    title_seen = Counter()
    paragraph_counter = Counter()
    section_counter = Counter()

    # Front matter before the first level-1 heading (title, abstract...).
    current_chapter = new_chapter(0)

    def attach_paragraph(text):
        nonlocal current_section
        if current_section is not None:
            key = current_section["section_id"]
            paragraph_counter[key] += 1
            pid = "p%s.%d" % (key, paragraph_counter[key])
            current_section["paragraphs"].append(
                make_paragraph(pid, text, current_chapter, current_section))
        else:
            key = current_chapter["number"]
            paragraph_counter[key] += 1
            pid = "p%s.%d" % (key, paragraph_counter[key])
            current_chapter["paragraphs"].append(
                make_paragraph(pid, text, current_chapter, None))

    for block in blocks:
        if block["kind"] == "heading":
            level = block["level"]
            heading_count += 1
            heading_levels.append(level)
            title = norm_text(block.get("title", "")) or "Untitled"
            title_seen[title.casefold()] += 1
            if title_seen[title.casefold()] == 2:
                duplicate_titles.append(title)
            if level == 1:
                chapter_heading_count += 1
                if current_chapter["paragraphs"] or current_chapter["sections"]:
                    chapters.append(current_chapter)
                    current_chapter = new_chapter(len(chapters))
                else:
                    current_chapter = new_chapter(0)
                current_chapter["title"] = title
                current_chapter["function"] = guess_function(title)
                current_chapter["number"] = block.get(
                    "number") or str(chapter_heading_count)
                current_section = None
            else:
                if current_chapter is None:
                    current_chapter = new_chapter(0)
                section_counter[current_chapter["number"]] += 1
                number = block.get("number") or "%s.%d" % (
                    current_chapter["number"],
                    section_counter[current_chapter["number"]])
                section = new_section(number, title, level)
                current_chapter["sections"].append(section)
                current_section = section
            continue
        text = block.get("text", "").strip()
        if not text:
            continue
        attach_paragraph(text)

    if current_chapter["paragraphs"] or current_chapter["sections"]:
        chapters.append(current_chapter)

    # Drop an empty implicit front-matter chapter, keep a real Abstract chapter.
    chapters = [chapter for chapter in chapters
                if chapter["paragraphs"] or chapter["sections"]]
    for index, chapter in enumerate(chapters, 1):
        chapter["chapter_id"] = "C%d" % index

    paragraph_index = {}
    total_body = 0
    empty_sections = []
    all_chapter_text = []
    for chapter in chapters:
        chapter_words = 0
        chapter_texts = [item.get("text") or item["first_sentence"]
                         for item in chapter["paragraphs"]]
        for paragraph in chapter["paragraphs"]:
            chapter_words += paragraph["word_count"]
            paragraph_index[paragraph["paragraph_id"]] = {
                "chapter": chapter["number"],
                "chapter_title": chapter["title"],
                "section": "",
                "section_title": "",
                "first_sentence": paragraph["first_sentence"],
                "word_count": paragraph["word_count"],
            }
        for section in chapter["sections"]:
            section_words = 0
            for paragraph in section["paragraphs"]:
                section_words += paragraph["word_count"]
                paragraph_index[paragraph["paragraph_id"]] = {
                    "chapter": chapter["number"],
                    "chapter_title": chapter["title"],
                    "section": section["section_id"],
                    "section_title": section["title"],
                    "first_sentence": paragraph["first_sentence"],
                    "word_count": paragraph["word_count"],
                }
            section["word_count"] = section_words
            if not section["paragraphs"]:
                empty_sections.append("%s %s" % (
                    section["section_id"], section["title"]))
            chapter_words += section_words
            chapter_texts.extend(
                item.get("text") or item["first_sentence"]
                for item in section["paragraphs"])
        all_chapter_text.extend(chapter_texts)
        chapter["word_count"] = chapter_words
        if chapter["function"] not in BODY_EXCLUDED:
            total_body += chapter_words

    for chapter in chapters:
        chapter["proportion"] = (
            round(chapter["word_count"] / total_body, 4)
            if total_body else 0.0)

    functions = [(chapter["function"], chapter["title"])
                 for chapter in chapters]
    detected = {name for name, _ in functions}
    missing = [name for name in CORE_FUNCTIONS if name not in detected]
    anomalies = []
    core_positions = [
        (FUNCTION_ORDER[name], chapter["title"])
        for name, _ in functions if name in FUNCTION_ORDER
    ]
    for left, right in zip(core_positions, core_positions[1:]):
        if right[0] < left[0]:
            anomalies.append("function order: %s appears before %s"
                             % (right[1], left[1]))

    level_jumps = []
    for left, right in zip(heading_levels, heading_levels[1:]):
        if right - left > 1:
            level_jumps.append("heading level jumps %d -> %d" % (left, right))

    all_text = "\n".join(all_chapter_text)
    refs = set(FIG_REF.findall(all_text))
    definitions = set()
    for paragraph_text in all_chapter_text:
        definitions.update(FIG_REF.findall(paragraph_text))
    # Definitions are only trustworthy when the paragraph starts like a caption.
    defined = set()
    for chapter in chapters:
        groups = [chapter["paragraphs"]]
        groups += [section["paragraphs"] for section in chapter["sections"]]
        for group in groups:
            for paragraph in group:
                if FIG_DEF.match(paragraph["first_sentence"]) or \
                        FIG_DEF.match(paragraph["raw_first_line"]):
                    defined.update(FIG_REF.findall(paragraph["raw_first_line"]))
    undefined_refs = sorted(refs - defined)
    unreferenced_defs = sorted(defined - refs)

    manuscript = {
        "path": str(path),
        "format": fmt,
        "language": detect_language(all_text),
        "title": chapters[0]["title"] if chapters else "",
        "word_count": sum(chapter["word_count"] for chapter in chapters),
        "body_word_count": total_body,
        "paragraph_count": len(paragraph_index),
        "heading_count": heading_count,
        "discovery": discovery,
    }

    signals = {
        "missing_common_functions": missing,
        "function_order_anomalies": anomalies,
        "empty_sections": empty_sections,
        "duplicate_headings": duplicate_titles,
        "heading_level_jumps": level_jumps,
        "figure_undefined_refs": undefined_refs,
        "figure_unreferenced_defs": unreferenced_defs,
    }
    uncertainty = []
    if fmt == "text":
        uncertainty.append({
            "item": "标题识别",
            "reason": "纯文本标题依赖编号/关键词启发式，可能把列表项误判为标题",
            "impact": "章节边界与段落编号需人工抽查",
            "next_check": "对照原文目录核对 chapters[].title 与 paragraph_index",
        })
    if fmt == "docx":
        uncertainty.append({
            "item": "标题样式",
            "reason": "docx 仅根据 pStyle 判定标题；手工加粗的标题不会被识别",
            "impact": "章节树可能缺少未套用 Heading 样式的标题",
            "next_check": "对照 Word 导航窗格核对章节数",
        })
    if discovery == "scan":
        uncertainty.append({
            "item": "稿件自动发现",
            "reason": "case.json 未给出可用路径，按文件名/后缀/大小扫描 /input",
            "impact": "若目录中有多个候选文件，可能选错稿件",
            "next_check": "核对 manuscript.path 是否为目标稿件；不对则用 --manuscript 重跑",
        })
    if fmt == "latex":
        uncertainty.append({
            "item": "LaTeX 清洗",
            "reason": "公式与宏命令被剥离，词数与首句可能受排版命令影响",
            "impact": "篇幅比例是近似值",
            "next_check": "关键章节人工核对词数",
        })
    if missing:
        uncertainty.append({
            "item": "常见章节缺失",
            "reason": "检测到的功能标签中缺少: %s" % ", ".join(missing),
            "impact": "可能是体裁差异（理论/人文/简报），不必然是结构缺陷",
            "next_check": "按体裁确认这些功能是否由其他章节承担",
        })

    return {
        "schema": "structure-map/v1",
        "generated_by": "%s/%s" % (TOOL, VERSION),
        "manuscript": manuscript,
        "chapters": chapters,
        "paragraph_index": paragraph_index,
        "signals": signals,
        "uncertainty": uncertainty,
    }


def make_paragraph(pid, text, chapter, section):
    sentences = split_sentences(text)
    signals = paragraph_signals(text)
    raw_first = text.split("\n")[0]
    return {
        "paragraph_id": pid,
        "text": text,
        "first_sentence": first_sentence(text),
        "raw_first_line": raw_first[:200],
        "word_count": word_count(text),
        "sentence_count": len(sentences),
        "signals": signals,
        "chapter_function": chapter.get("function", "unknown"),
        "section_title": section["title"] if section else "",
    }
