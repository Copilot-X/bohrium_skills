#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared engine for the Period 4 skills.

The engine is intentionally conservative: deterministic extraction and audit
helpers, a result envelope that writes only paths that really exist, and small
Markdown/DOCX/PDF exporters.  It never invents source text, citations, numbers
or review results.

Version: 1.0.0
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import parsers

VERSION = "1.0.0"
TEXT_EXTS = {".md", ".txt", ".tex", ".latex", ".bib", ".ris", ".json",
             ".csv", ".yaml", ".yml", ".toml", ".xml", ".html", ".py",
             ".sh", ".js", ".ts", ".ini", ".rst", ".org", ".log"}
SKIP_NAMES = {"case.json", "task.md", "environment/resources.yaml"}

READING_STATUS = ("answered", "partial", "insufficient_evidence", "input_error")
WRITING_STATUS = ("completed", "partial", "input_error")
OFFICIAL_READING = ("status", "result", "claims", "uncertainties")
OFFICIAL_WRITING = ("deliverable", "preserved_facts", "evidence_map", "change_log")

# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return default


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def load_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def sha256_path(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def relpath(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def ensure_dirs(*paths):
    for path in paths:
        Path(path).mkdir(parents=True, exist_ok=True)


def excerpt(text, limit=160):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def word_count(text):
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", text or "")) + len(
        re.findall(r"[\u4e00-\u9fff]", text or ""))


def cjk_count(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def normalize_space(text):
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00ad", "").replace("\ufeff", "")
    return re.sub(r"[ \t\u3000]+", " ", text).strip()


def normalize_match(text):
    text = normalize_space(text).lower()
    text = re.sub(r"[\s\u3000]+", "", text)
    return text


# ---------------------------------------------------------------------------
# Inventory and source loading
# ---------------------------------------------------------------------------

def classify_file(path):
    suffix = Path(path).suffix.lower()
    name = Path(path).name.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in (".docx", ".doc"):
        return "docx"
    if suffix in (".tex", ".latex"):
        return "tex"
    if suffix == ".bib":
        return "bib"
    if suffix == ".ris":
        return "ris"
    if suffix == ".json":
        return "json"
    if suffix == ".csv":
        return "csv"
    if suffix in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".webp"):
        return "image"
    if suffix in (".yaml", ".yml"):
        return "yaml"
    if suffix == ".toml":
        return "toml"
    if suffix in (".py", ".sh", ".js", ".ts", ".r", ".m"):
        return "code"
    if suffix == ".log":
        return "log"
    if suffix in (".md", ".txt", ".rst", ".org"):
        return "text"
    if name in ("case.json", "task.md"):
        return "task"
    return "other"


def inventory(input_dir):
    input_dir = Path(input_dir)
    rows = []
    if not input_dir.exists():
        return rows
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.name.startswith("."):
            continue
        rel = path.relative_to(input_dir).as_posix()
        rows.append({
            "path": rel,
            "name": path.name,
            "ext": path.suffix.lower(),
            "kind": classify_file(path),
            "bytes": path.stat().st_size,
            "sha256": sha256_path(path),
            "role": "task" if rel in SKIP_NAMES else "material",
        })
    return rows


def primary_materials(inv, keywords=None):
    """Rank likely primary material files; never excludes other readable files."""
    keywords = keywords or []
    candidates = [r for r in inv if r["role"] == "material"
                  and r["kind"] not in ("image", "task")]
    def score(row):
        name = row["path"].lower()
        value = 0
        if row["kind"] == "pdf":
            value += 50
        if row["kind"] in ("docx", "tex", "text", "md"):
            value += 20
        for word in keywords:
            if word in name:
                value += 30
        if any(word in name for word in ("paper", "manuscript", "article",
                                         "material", "source", "primary")):
            value += 15
        if name.startswith(("case", "task", "readme")):
            value -= 10
        return (-value, row["path"])
    return sorted(candidates, key=score)


def load_materials(input_dir, ocr=True, tmp_dir=None):
    input_dir = Path(input_dir)
    inv = inventory(input_dir)
    docs = []
    for row in primary_materials(inv):
        path = input_dir / row["path"]
        try:
            doc = parsers.extract_any(path, ocr=ocr, tmp_dir=tmp_dir or input_dir / ".ocr")
        except Exception as exc:  # noqa: BLE001
            doc = {"kind": row["kind"], "path": str(path), "text": "",
                   "warnings": ["extract failed: %s" % exc]}
        doc["source_id"] = "SRC%02d" % (len(docs) + 1)
        doc["rel_path"] = row["path"]
        doc["bytes"] = row["bytes"]
        docs.append(doc)
    return inv, docs


def doc_text(doc):
    return doc.get("text") or ""


def all_text(docs):
    return "\n\n".join(doc_text(d) for d in docs if doc_text(d))


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

HEADING_MD_RE = re.compile(r"^ {0,3}(#{1,6})\s+\S")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+)")
TABLE_RE = re.compile(r"^\s*\|")
MATH_START_RE = re.compile(
    r"^\s*(?:\$\$|\\\[|\\begin\{(?:equation|align|eqnarray|gather|multline)\*?\})")


def split_blocks(text):
    """Split Markdown/text into blocks with 1-based line ranges."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    blocks = []
    buf = []
    buf_start = None

    def flush(end_idx):
        nonlocal buf, buf_start
        if not buf:
            return
        while buf and not buf[-1].strip():
            buf.pop()
        if buf:
            blocks.append({"lines": list(buf),
                           "line_start": (buf_start if buf_start is not None else 0) + 1,
                           "line_end": end_idx})
        buf = []
        buf_start = None

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            flush(i)
            i += 1
            continue
        if HEADING_MD_RE.match(line):
            flush(i)
            blocks.append({"lines": [line.rstrip()], "line_start": i + 1,
                           "line_end": i + 1, "kind": "heading"})
            i += 1
            continue
        if MATH_START_RE.match(line) and not buf:
            flush(i)
            start = i
            stripped = line.strip()
            closing = "$$" if stripped.startswith("$$") else (
                "\\]" if stripped.startswith("\\[") else None)
            env = re.match(r"\\begin\{([^}]+)\}", stripped)
            if env:
                closing = "\\end{" + env.group(1) + "}"
            one_line = closing == "$$" and stripped.count("$$") >= 2
            buf = [line.rstrip("\n")]
            i += 1
            while closing and not one_line and i < n:
                buf.append(lines[i].rstrip("\n"))
                if closing in lines[i]:
                    i += 1
                    break
                i += 1
            blocks.append({"lines": list(buf), "line_start": start + 1,
                           "line_end": i, "kind": "formula"})
            buf = []
            buf_start = None
            continue
        if not buf:
            buf_start = i
        buf.append(line.rstrip("\n"))
        i += 1
    flush(n)
    return blocks


def block_kind(lines):
    first = lines[0].strip() if lines else ""
    if HEADING_MD_RE.match(lines[0] if lines else ""):
        return "heading"
    if MATH_START_RE.match(lines[0] if lines else ""):
        return "formula"
    if TABLE_RE.match(lines[0] if lines else ""):
        return "table"
    if LIST_RE.match(lines[0] if lines else ""):
        return "list"
    return "body"


def segment_blocks(text, source_id="SRC01", path="", page=None):
    """Return paragraph-level segments with stable IDs and locators."""
    segments = []
    for index, block in enumerate(split_blocks(text), 1):
        body = "\n".join(block["lines"]).strip()
        if not body:
            continue
        kind = block.get("kind") or block_kind(block["lines"])
        segments.append({
            "id": "P%03d" % index,
            "source_id": source_id,
            "path": path,
            "page": page,
            "locator": "%s P%03d" % (path or source_id, index),
            "line_start": block["line_start"],
            "line_end": block["line_end"],
            "kind": kind,
            "text": body,
        })
    return segments


ABBREVIATIONS = {
    "e.g.", "i.e.", "et al.", "vs.", "fig.", "figs.", "eq.", "eqs.", "ref.",
    "refs.", "no.", "nos.", "dr.", "prof.", "mr.", "mrs.", "ms.", "approx.",
    "ca.", "cf.", "resp.", "suppl.", "tab.", "sec.", "vol.", "pp.", "p.",
}


def _protect_abbreviations(text):
    protected = text
    for abbr in sorted(ABBREVIATIONS, key=len, reverse=True):
        protected = re.sub(re.escape(abbr), abbr.replace(".", "\u2024"), protected,
                           flags=re.I)
    return protected


def split_sentences(text):
    """Sentence spans that keep citations, decimals and abbreviations intact."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    protected = _protect_abbreviations(text)
    # Boundary: terminal punctuation followed by whitespace and an uppercase
    # letter, digit, opening quote/bracket, or a CJK character.  Also treat a
    # closing citation bracket followed by a capital as a boundary.
    pattern = re.compile(
        r"(?<=[.!?。！？])\s+(?=(?:[\"“‘'(\[]|[A-Z0-9\u4e00-\u9fff]))"
        r"|(?<=\])\s+(?=[A-Z\u4e00-\u9fff])"
        r"|(?<=[。！？；])(?=[\u4e00-\u9fffA-Z0-9])"
    )
    pieces = []
    start = 0
    for match in pattern.finditer(protected):
        end = match.start()
        if end > start:
            pieces.append((start, end))
        start = match.end()
    if start < len(protected):
        pieces.append((start, len(protected)))
    sentences = []
    for index, (a, b) in enumerate(pieces, 1):
        raw = text[a:b].strip()
        if not raw:
            continue
        sentences.append({
            "id": "S%03d" % index,
            "text": raw,
            "start": a,
            "end": b,
            "char_start": a,
            "char_end": b,
        })
    return sentences


# ---------------------------------------------------------------------------
# Token extraction and preservation
# ---------------------------------------------------------------------------

NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[-+]?\d+(?:[.,]\d+)?(?:\s*[eE][-+]?\d+)?"
    r"(?:\s*(?:%|‰|℃|°C|K|Pa|kPa|MPa|GPa|bar|atm|mM|μM|uM|nM|mg|g|kg|μg|ug|"
    r"ng|mL|ml|L|μL|uL|nm|μm|um|mm|cm|m|km|s|ms|min|h|hr|day|d|Hz|kHz|MHz|"
    r"GHz|V|mV|A|mA|W|kW|J|kJ|cal|kcal|mol|mmol|μmol|umol|rpm|Da|kDa|bp|kb))?)"
    r"(?![A-Za-z0-9])"
)
CITATION_BRACKET_RE = re.compile(r"\[(\d+(?:\s*[,–\-]\s*\d+)*)\]")
CITATION_AUTHOR_YEAR_RE = re.compile(
    r"\((?:[A-Z][A-Za-z'’\-]+(?:\s+(?:et al\.|&|and)\s+[A-Z][A-Za-z'’\-]+)*"
    r",?\s*(?:19|20)\d{2}[a-z]?(?:;\s*)?)+\)")
CITATION_TEX_RE = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])?\{([^{}]+)\}")
CITATION_PANDOC_RE = re.compile(r"\[@([^\]]+)\]")
FIGURE_REF_RE = re.compile(
    r"\b(?:Fig(?:ure)?\.?|Table|Eq(?:uation)?\.?|Scheme|Section)\s*"
    r"[A-Za-z0-9一二三四五六七八九十]+(?:[a-z])?(?:\([a-z0-9]+\))?"
    r"|(?:图|表|式|第)\s*[A-Za-z0-9一二三四五六七八九十]+(?:[a-z])?",
    re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
URL_RE = re.compile(r"https?://[^\s)>\]]+")


def number_tokens(text):
    tokens = []
    for match in NUMBER_RE.finditer(text or ""):
        raw = match.group(0).strip()
        if not re.search(r"\d", raw):
            continue
        tokens.append({"raw": raw, "value": re.sub(r"\s+", "", raw),
                       "start": match.start(), "end": match.end()})
    return tokens


def number_values(text):
    """Numeric values only, normalised for set comparison."""
    values = []
    for token in number_tokens(text):
        raw = token["raw"]
        match = re.match(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?", raw)
        if not match:
            continue
        value = match.group(0).replace(",", "")
        try:
            number = float(value)
        except ValueError:
            continue
        values.append({"raw": raw, "number": number})
    return values


def number_presence_missing(text, source_text):
    """Distinct numbers in ``text`` that do not appear anywhere in the source."""
    source = {round(v["number"], 6) for v in number_values(source_text)}
    target = {round(v["number"], 6) for v in number_values(text)}
    return sorted(value for value in target if value not in source)


def numbers_missing(source_text, target_text, tolerance=0.0):
    source = Counter(round(v["number"], 6) for v in number_values(source_text))
    target = Counter(round(v["number"], 6) for v in number_values(target_text))
    missing = []
    for value, count in (source - target).items():
        missing.append({"number": value, "missing_count": count})
    extra = []
    for value, count in (target - source).items():
        extra.append({"number": value, "extra_count": count})
    return missing, extra


def citation_tokens(text):
    tokens = []
    for match in CITATION_BRACKET_RE.finditer(text or ""):
        tokens.append({"raw": match.group(0), "kind": "bracket",
                       "numbers": _parse_number_group(match.group(1)),
                       "start": match.start()})
    for match in CITATION_AUTHOR_YEAR_RE.finditer(text or ""):
        tokens.append({"raw": match.group(0), "kind": "author-year",
                       "numbers": [], "start": match.start()})
    for match in CITATION_TEX_RE.finditer(text or ""):
        tokens.append({"raw": match.group(0), "kind": "tex",
                       "keys": [x.strip() for x in match.group(1).split(",") if x.strip()],
                       "numbers": [], "start": match.start()})
    for match in CITATION_PANDOC_RE.finditer(text or ""):
        tokens.append({"raw": match.group(0), "kind": "pandoc",
                       "keys": [x.strip() for x in match.group(1).split(";") if x.strip()],
                       "numbers": [], "start": match.start()})
    return tokens


def _parse_number_group(value):
    numbers = []
    for part in re.split(r"\s*,\s*", value or ""):
        if not part:
            continue
        rng = re.match(r"^(\d+)\s*[–\-]\s*(\d+)$", part)
        if rng:
            a, b = int(rng.group(1)), int(rng.group(2))
            if 0 < b - a <= 200:
                numbers.extend(range(a, b + 1))
            else:
                numbers.extend([a, b])
        elif part.isdigit():
            numbers.append(int(part))
    return sorted(set(numbers))


def format_number_list(numbers):
    numbers = sorted(set(int(n) for n in numbers))
    if not numbers:
        return ""
    chunks = []
    i = 0
    while i < len(numbers):
        j = i
        while j + 1 < len(numbers) and numbers[j + 1] == numbers[j] + 1:
            j += 1
        if j - i >= 2:
            chunks.append("%d-%d" % (numbers[i], numbers[j]))
        else:
            chunks.extend(str(n) for n in numbers[i:j + 1])
        i = j + 1
    return ",".join(chunks)


def figure_refs(text):
    return [m.group(0) for m in FIGURE_REF_RE.finditer(text or "")]


def reference_tokens(text):
    return [m.group(0) for m in DOI_RE.finditer(text or "")] + [
        m.group(0) for m in URL_RE.finditer(text or "")]


HEDGE_UP = [
    "may", "might", "could", "suggest", "suggests", "suggested", "indicate",
    "indicates", "indicated", "appear", "appears", "likely", "possibly",
    "potentially", "associated with", "correlated with", "consistent with",
    "tend to", "can", "possible", "perhaps", "probable", "probably",
    "可能", "或许", "提示", "表明", "显示", "相关", "关联", "倾向于",
    "可以", "或许", "也许", "可能",
]
HEDGE_DOWN = [
    "prove", "proves", "proved", "demonstrate", "demonstrates", "show",
    "shows", "establish", "establishes", "confirm", "confirms", "cause",
    "causes", "caused", "drive", "drives", "lead to", "leads to", "must",
    "always", "never", "clearly", "definitively",
    "证明", "证实", "表明", "确定", "导致", "引起", "必然", "总是",
]
NEGATION_RE = re.compile(
    r"\b(?:not|no|none|never|without|neither|nor|cannot|can't|didn't|"
    r"doesn't|isn't|wasn't|weren't|fail(?:ed|s)?|absence|lack(?:s|ed)?)\b"
    r"|不|未|无|没有|并非|非|缺乏|无法|不能|未能", re.I)


def hedge_counts(text):
    low = (text or "").lower()
    up = sum(low.count(term.lower()) for term in HEDGE_UP)
    down = sum(low.count(term.lower()) for term in HEDGE_DOWN)
    return {"hedging": up, "strong": down, "level": 1 if up > down else (-1 if down > up else 0)}


def negation_count(text):
    return len(NEGATION_RE.findall(text or ""))


def acronyms(text):
    counts = Counter(re.findall(r"\b[A-Z][A-Z0-9]{1,9}\b", text or ""))
    return [{"term": term, "count": count} for term, count in counts.most_common(30)]


def term_candidates(text, limit=40):
    """Conservative candidates: acronyms and repeated capitalised phrases."""
    counts = Counter()
    for term, count in Counter(re.findall(
            r"\b[A-Z][A-Za-z0-9\-]+(?:\s+[A-Z][A-Za-z0-9\-]+){0,2}\b",
            text or "")).items():
        if len(term) >= 4 and count >= 2:
            counts[term] = count
    for item in acronyms(text):
        counts[item["term"]] = max(counts[item["term"]], item["count"])
    return [{"term": term, "count": count} for term, count in counts.most_common(limit)]


# ---------------------------------------------------------------------------
# Quote and claim checks
# ---------------------------------------------------------------------------

def quote_supported(source_text, quote):
    """Return (ok, reason).  Allows whitespace/NFKC differences and ellipses."""
    quote = (quote or "").strip()
    if not quote:
        return False, "empty quote"
    source_norm = normalize_match(source_text)
    parts = [p for p in re.split(r"(?:…|\.\.\.)", quote) if normalize_match(p)]
    if not parts:
        return False, "no quote content"
    for part in parts:
        if normalize_match(part) not in source_norm:
            return False, "part not found: %s" % excerpt(part, 80)
    return True, "ok"


def quote_locator(text, quote):
    """Return a line number range for a verbatim quote, or None."""
    norm_text = normalize_match(text)
    norm_quote = normalize_match(quote)
    if not norm_quote:
        return None
    index = norm_text.find(norm_quote)
    if index < 0:
        parts = [normalize_match(p) for p in re.split(r"(?:…|\.\.\.)", quote) if p.strip()]
        if not parts:
            return None
        index = norm_text.find(parts[0])
        if index < 0:
            return None
    # Approximate the original character position by searching line by line.
    target = norm_quote[:80]
    consumed = 0
    for line_no, line in enumerate((text or "").splitlines(), 1):
        consumed += len(normalize_match(line))
        if consumed >= index + len(target):
            return {"line": line_no, "line_end": line_no}
    return None


def atomicity_issues(assertion):
    issues = []
    text = (assertion or "").strip()
    if not text:
        issues.append("empty assertion")
    if len(text) > 420:
        issues.append("assertion longer than 420 characters")
    # Multiple independent clauses are a signal, not a hard rejection.
    clause_marks = len(re.findall(r";|\b(?:and|but|while|whereas|although)\b", text, re.I))
    if clause_marks >= 2:
        issues.append("multiple clauses; consider splitting")
    return issues


# ---------------------------------------------------------------------------
# Bibliography formatting
# ---------------------------------------------------------------------------

def _first(value, default=""):
    if isinstance(value, list):
        return value[0] if value else default
    return value or default


def _author_list(entry):
    authors = entry.get("author") or entry.get("authors") or entry.get("AU") or ""
    if isinstance(authors, list):
        authors = " and ".join(authors)
    authors = re.sub(r"\s+", " ", authors or "").strip()
    return [a.strip() for a in re.split(r"\s+and\s+|;\s*", authors) if a.strip()]


def _split_author(name):
    name = name.strip().strip("{}")
    if "," in name:
        last, first = [x.strip() for x in name.split(",", 1)]
    else:
        parts = name.split()
        last = parts[-1] if parts else name
        first = " ".join(parts[:-1])
    initials = "".join(p[0] + "." for p in re.split(r"[\s\-]+", first) if p)
    return last, initials, first


def format_authors(entry, style="apa"):
    authors = _author_list(entry)
    if not authors:
        return ""
    formatted = []
    if style in ("apa", "gb"):
        for i, name in enumerate(authors):
            last, initials, _ = _split_author(name)
            if style == "apa":
                formatted.append("%s, %s" % (last, initials))
            else:
                formatted.append("%s %s" % (last, initials))
        if style == "apa" and len(formatted) > 1:
            return ", ".join(formatted[:-1]) + ", & " + formatted[-1]
        return ", ".join(formatted)
    if style == "ieee":
        for name in authors:
            last, initials, _ = _split_author(name)
            formatted.append("%s %s" % (initials, last))
        if len(formatted) > 1:
            return ", ".join(formatted[:-1]) + ", and " + formatted[-1]
        return formatted[0]
    return ", ".join(authors)


def format_bib_entry(entry, style="apa", number=None):
    fields = entry.get("fields") or entry
    title = _first(fields.get("title") or fields.get("TI"))
    journal = _first(fields.get("journal") or fields.get("journaltitle")
                     or fields.get("booktitle") or fields.get("T2"))
    year = _first(fields.get("year") or fields.get("PY") or fields.get("date"))
    volume = _first(fields.get("volume") or fields.get("VL"))
    issue = _first(fields.get("number") or fields.get("issue") or fields.get("IS"))
    pages = _first(fields.get("pages") or fields.get("SP"))
    doi = _first(fields.get("doi") or fields.get("DO"))
    publisher = _first(fields.get("publisher") or fields.get("PB"))
    authors = format_authors(fields, "apa" if style == "apa" else "gb" if style == "gb" else "ieee")
    style = (style or "apa").lower()
    if style == "gb":
        text = ", ".join(p for p in (authors, title) if p)
        tail = ""
        if journal:
            tail += journal
        if year:
            tail += (", " if tail else "") + str(year)
        if volume:
            tail += ", " + volume + ("(%s)" % issue if issue else "")
        if pages:
            tail += ": " + pages
        if tail:
            text = (text + ", " if text else "") + tail
        if doi:
            text += ". DOI: %s" % doi
        if not text:
            text = entry.get("raw", "")
        return "[%s] %s" % (number if number is not None else "?", text)
    if style == "ieee":
        text = ""
        if authors:
            text += authors + ", "
        if title:
            text += '"%s," ' % title
        if journal:
            text += journal + ", "
        if volume:
            text += "vol. %s, " % volume
        if issue:
            text += "no. %s, " % issue
        if pages:
            text += "pp. %s, " % pages
        if year:
            text += str(year)
        text = text.rstrip(", ") + "."
        if doi:
            text += " doi: %s." % doi
        return "[%s] %s" % (number if number is not None else "?", text)
    if style == "vancouver":
        text = ""
        if authors:
            text += authors + ". "
        if title:
            text += title + ". "
        if journal:
            text += journal + ". "
        if year:
            text += str(year) + ";"
        if volume:
            text += volume
        if issue:
            text += "(%s)" % issue
        if pages:
            text += ":" + pages
        text = text.rstrip(";.") + "."
        if doi:
            text += " doi:%s." % doi
        return ("%s. %s" % (number, text) if number is not None else text)
    # APA 7
    text = ""
    if authors:
        text += authors + " "
    if year:
        text += "(%s). " % year
    if title:
        text += title.rstrip(".") + ". "
    if journal:
        text += journal
        if volume:
            text += ", %s" % volume
        if issue:
            text += "(%s)" % issue
        if pages:
            text += ", %s" % pages
        text += ". "
    elif publisher:
        text += publisher + ". "
    if doi:
        text += "https://doi.org/%s" % doi
    return text.strip() or entry.get("raw", "")


def bib_entries_from_docs(docs):
    entries = []
    for doc in docs:
        if doc.get("kind") == "bib":
            entries.extend(doc.get("entries") or [])
        elif doc.get("kind") == "ris":
            for record in doc.get("records") or []:
                fields = {}
                for key, value in record.items():
                    fields[key.lower()] = _first(value)
                entries.append({"key": _first(record.get("ID")) or "ris%d" % len(entries),
                                "type": _first(record.get("TY")).lower() or "article",
                                "fields": fields, "raw": json.dumps(record, ensure_ascii=False)})
        elif doc.get("kind") == "json" and isinstance(doc.get("data"), list):
            for item in doc["data"]:
                if isinstance(item, dict):
                    fields = {str(k).lower(): v for k, v in item.items()}
                    entries.append({"key": str(fields.get("key") or fields.get("id")
                                               or "ref%d" % len(entries)),
                                    "type": str(fields.get("type") or "article"),
                                    "fields": fields, "raw": json.dumps(item, ensure_ascii=False)})
    return entries


def bibtex_entry(entry):
    """Serialise one bibliography entry as a compact BibTeX record."""
    fields = entry.get("fields") or {}
    key = str(entry.get("key") or "ref")
    entry_type = str(entry.get("type") or "article").lower()
    order = ["author", "title", "journal", "booktitle", "year", "volume",
             "number", "pages", "doi", "publisher", "url"]
    lines = ["@%s{%s," % (entry_type, key)]
    for name in order:
        value = fields.get(name)
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            value = " and ".join(map(str, value))
        value = str(value).replace("\n", " ").strip()
        lines.append("  %s = {%s}," % (name, value))
    if len(lines) == 1:
        lines.append("  title = {%s}," % str(fields.get("title") or key))
    lines.append("}")
    return "\n".join(lines)


def dedupe_key(entry):
    fields = entry.get("fields") or {}
    doi = (fields.get("doi") or "").lower().strip()
    if doi:
        return "doi:" + re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    title = re.sub(r"[^a-z0-9]+", "", (fields.get("title") or "").lower())
    year = str(fields.get("year") or "")
    return "title:" + title[:80] + ":" + year


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------

CONTRACT_ALIAS_MAP = {
    # English semantic field names used by task.md output tables.
    "search_results": ["deliverable", "result", "claims"],
    "candidate_set": ["claims", "result", "deliverable"],
    "screening_results": ["claims", "result", "deliverable"],
    "evidence_record": ["evidence_map", "evidence", "sources"],
    "status_note": ["uncertainties", "status"],
    "review_report": ["deliverable", "claims"],
    "analysis_report": ["deliverable", "claims", "results"],
    "research_report": ["deliverable", "claims"],
    "library_results": ["deliverable", "claims", "items"],
    "experiment_report": ["deliverable", "claims", "steps"],
    "coverage": ["result"],
    "editable_source": ["deliverable", "result"],
    "export_artifact": ["deliverable", "result"],
    "content_map": ["evidence_map", "claims", "result"],
    "compliance_checks": ["result", "claims"],
    "exceptions": ["uncertainties"],
    # Chinese field names used in some task.md tables.
    "检索结果": ["deliverable", "result", "claims"],
    "证据记录": ["evidence_map", "evidence", "sources"],
    "状态说明": ["uncertainties", "status"],
    "审查报告": ["deliverable", "claims"],
    "分析报告": ["deliverable", "claims", "results"],
    "研究产物": ["deliverable", "claims"],
    "目标产物": ["deliverable"],
    "事实与来源映射": ["evidence_map", "preserved_facts"],
    "阅读解析结果": ["deliverable", "result", "claims"],
    "证据与边界": ["evidence_map", "uncertainties"],
}


def contract_key_norm(key):
    return re.sub(r"[^0-9a-z一-鿿]+", "_",
                  str(key or "").lower()).strip("_")


class ResultBuilder:
    def __init__(self, case_id, task_family, output_dir, work_dir,
                 case=None, task_text="", profile="reading"):
        self.output = Path(output_dir)
        self.work = Path(work_dir)
        self.case = case or {}
        self.task_text = task_text or ""
        self.profile = profile
        self.data = {
            "case_id": str(case_id or self.case.get("case_id") or "CASE"),
            "task_family": task_family,
            "status": "partial",
            "task_status": "partial",
            "profile": profile,
            "result": {},
            "claims": [],
            "uncertainties": [],
            "uncertainty": [],
            "sources": [],
            "evidence": [],
            "evidence_map": [],
            "preserved_facts": [],
            "change_log": [],
            "artifacts": [],
            "deliverable": None,
            "output_contract": self.case.get("output_contract") or {},
            "contract_coverage": [],
            "generated_at": now_iso(),
            "engine_version": VERSION,
        }

    def add_source(self, path, locator="", quote="", source_id="", kind="",
                   title=""):
        item = {"source_id": source_id or "SRC%02d" % (len(self.data["sources"]) + 1),
                "path": str(path), "locator": locator, "quote": quote,
                "kind": kind, "title": title}
        self.data["sources"].append(item)
        return item

    def add_artifact(self, path, role="deliverable", fmt=None, description="",
                     must_exist=True):
        path = Path(path)
        if not path.is_absolute() and not path.exists():
            path = self.output / path
        exists = path.exists()
        if path.is_dir():
            size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
            kind = "directory"
        else:
            size = path.stat().st_size if exists else 0
            kind = "file"
        rel = relpath(path, self.output)
        item = {"path": rel, "role": role,
                "format": fmt or ("directory" if kind == "directory"
                                  else path.suffix.lstrip(".")),
                "bytes": size, "kind": kind,
                "exists": exists, "description": description}
        if must_exist and not exists:
            self.uncertainty("artifact missing: %s" % rel, "file was not written",
                             "deliverable is incomplete", "rerun finalize")
        self.data["artifacts"].append(item)
        return item

    def add_evidence(self, claim_id, source, quote="", locator="", supports="direct",
                     paraphrase="", source_id=""):
        item = {"claim_id": claim_id, "source": source, "locator": locator,
                "quote": quote, "supports": supports,
                "paraphrase": paraphrase, "source_id": source_id}
        self.data["evidence"].append(item)
        self.data["evidence_map"].append(item)
        return item

    def add_preserved(self, kind, source, target, locator="", value="", unit=""):
        item = {"kind": kind, "source": source, "target": target,
                "locator": locator, "value": value, "unit": unit}
        self.data["preserved_facts"].append(item)
        return item

    def uncertainty(self, item, reason, impact="", action=""):
        row = {"item": item, "reason": reason, "impact": impact, "action": action}
        self.data["uncertainty"].append(row)
        self.data["uncertainties"].append(row)
        return row

    def change(self, action, scope="", reason="", evidence="", human_review=False):
        row = {"step": len(self.data["change_log"]) + 1, "action": action,
               "scope": scope, "reason": reason, "evidence": evidence,
               "human_review": human_review, "at": now_iso()}
        self.data["change_log"].append(row)
        return row

    def set(self, key, value):
        self.data[key] = value
        return value

    def _contract_coverage(self):
        contract = self.data.get("output_contract")
        if isinstance(contract, list):
            contract = {str(k): None for k in contract}
        if not isinstance(contract, dict):
            return []
        coverage = []
        for key, spec in contract.items():
            # A key whose value is an empty list/dict is still present and
            # meaningful for fields such as uncertainties/conflicts.  For a
            # primary result field, an empty container is not a covered value.
            value = self.data.get(key)
            present = key in self.data and value is not None and value != ""
            if present and isinstance(value, (list, dict)) and len(value) == 0:
                allowed_empty = {
                    "uncertainties", "uncertainty", "preserved_facts",
                    "change_log", "conflicts", "review_queue", "skipped",
                    "excluded", "suppressed", "residual_misses", "unresolved",
                    "gaps", "limitations", "duplicate_groups", "version_groups",
                    "unreadable", "version_conflict", "missing_evidence",
                }
                present = key in allowed_empty
            coverage.append({"field": key, "present": present,
                             "spec": spec if isinstance(spec, (str, dict, list)) else str(spec)})
        return coverage

    def _apply_contract_aliases(self):
        """Populate original output_contract keys from equivalent fields.

        A task.md output table may name a field ``search_results`` or
        ``evidence_record`` while the skill's canonical envelope uses
        ``deliverable`` / ``evidence_map``.  The original key is retained and
        aliased to the real content; no new scientific content is invented.
        """
        contract = self.data.get("output_contract")
        if not isinstance(contract, dict):
            return
        for key in contract:
            if key in self.data and self.data[key] is not None:
                value = self.data[key]
                # A pre-finalize pass may have written an empty placeholder
                # (e.g. {} from a not-yet-populated result).  Retry aliases
                # for empty containers after the canonical fields exist.
                if not (isinstance(value, (list, dict)) and len(value) == 0):
                    continue
            norm = contract_key_norm(key)
            for alias in CONTRACT_ALIAS_MAP.get(norm, []):
                if alias in self.data and self.data[alias] is not None:
                    self.data[key] = self.data[alias]
                    break
            if key in self.data and self.data[key] is not None:
                continue
            for artifact in self.data.get("artifacts") or []:
                path = artifact.get("path") if isinstance(artifact, dict) else artifact
                if not path:
                    continue
                stem = contract_key_norm(Path(str(path)).stem)
                fmt = contract_key_norm(
                    (artifact.get("format") if isinstance(artifact, dict) else "")
                    or Path(str(path)).suffix.lstrip("."))
                if stem and (stem == norm or stem in norm or norm in stem):
                    self.data[key] = path
                    break
                if fmt and (fmt == norm or (len(fmt) >= 3 and fmt in norm)):
                    self.data[key] = path
                    break
                key_tokens = {x for x in norm.split("_") if len(x) > 2}
                stem_tokens = {x for x in stem.split("_") if len(x) > 2}
                if key_tokens & stem_tokens:
                    self.data[key] = path
                    break

    def finalize(self, status, deliverable=None, result=None, claims=None,
                 uncertainties=None, preserved_facts=None, evidence_map=None,
                 extra=None):
        if self.profile == "both":
            allowed = tuple(dict.fromkeys(READING_STATUS + WRITING_STATUS))
        elif self.profile == "reading":
            allowed = READING_STATUS
        else:
            allowed = WRITING_STATUS
        if status not in allowed:
            status = "partial"
        self.data["status"] = status
        self.data["task_status"] = status
        if deliverable is not None:
            if isinstance(deliverable, (str, Path)):
                path = Path(deliverable)
                if not path.is_absolute() and not path.exists():
                    path = self.output / path
                is_dir = path.is_dir()
                self.data["deliverable"] = {
                    "path": relpath(path, self.output),
                    "format": "directory" if is_dir else (path.suffix.lstrip(".") or "json"),
                    "exists": path.exists(),
                    "bytes": (sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
                              if is_dir else (path.stat().st_size if path.exists() else 0)),
                }
            else:
                self.data["deliverable"] = deliverable
        if result is not None:
            self.data["result"] = result
        if claims is not None:
            self.data["claims"] = claims
        if uncertainties is not None:
            self.data["uncertainties"] = uncertainties
            self.data["uncertainty"] = uncertainties
        if preserved_facts is not None:
            self.data["preserved_facts"] = preserved_facts
        if evidence_map is not None:
            self.data["evidence_map"] = evidence_map
            self.data["evidence"] = evidence_map
        if extra:
            for key, value in extra.items():
                self.data[key] = value
        self.data["generated_at"] = now_iso()
        self._apply_contract_aliases()
        self.data["contract_coverage"] = self._contract_coverage()
        self.data["artifact_paths_valid"] = all(a.get("exists") for a in self.data["artifacts"])
        if status in ("completed", "answered"):
            if not self.data["deliverable"] or not self.data["deliverable"].get("exists", False):
                self.data["status"] = "partial"
                self.data["task_status"] = "partial"
                self.uncertainty("deliverable", "required deliverable does not exist",
                                 "cannot be marked completed", "write the deliverable")
            if not self.data["artifact_paths_valid"]:
                self.data["status"] = "partial"
                self.data["task_status"] = "partial"
        payload = self.data
        write_json(self.output / "result.json", payload)
        return payload


# ---------------------------------------------------------------------------
# DOCX and PDF preview exporters (stdlib + optional matplotlib)
# ---------------------------------------------------------------------------

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _esc(value):
    return xml_escape(str(value or ""), {'"': "&quot;"})


def _docx_run(text, bold=False, italic=False):
    if text == "":
        return ""
    props = ""
    if bold or italic:
        props = "<w:rPr>%s%s</w:rPr>" % ("<w:b/>" if bold else "",
                                         "<w:i/>" if italic else "")
    pieces = str(text).split("\n")
    body = []
    for index, piece in enumerate(pieces):
        if index:
            body.append("<w:br/>")
        body.append('<w:t xml:space="preserve">%s</w:t>' % _esc(piece))
    return "<w:r>%s%s</w:r>" % (props, "".join(body))


def _docx_para(text, style=None, bold=False, italic=False):
    props = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ""
    runs = _docx_run(text, bold=bold, italic=italic)
    if not runs:
        runs = '<w:r><w:t xml:space="preserve"> </w:t></w:r>'
    return "<w:p>%s%s</w:p>" % (props, runs)


def _docx_table(rows):
    if not rows:
        return ""
    columns = max(len(row) for row in rows)
    border = ('<w:tblBorders>'
              '<w:top w:val="single" w:sz="4" w:color="A6A6A6"/>'
              '<w:left w:val="single" w:sz="4" w:color="A6A6A6"/>'
              '<w:bottom w:val="single" w:sz="4" w:color="A6A6A6"/>'
              '<w:right w:val="single" w:sz="4" w:color="A6A6A6"/>'
              '<w:insideH w:val="single" w:sz="4" w:color="A6A6A6"/>'
              '<w:insideV w:val="single" w:sz="4" w:color="A6A6A6"/>'
              '</w:tblBorders>')
    grid = "".join('<w:gridCol w:w="%d"/>' % max(900, 9000 // columns)
                   for _ in range(columns))
    body = []
    for row_index, row in enumerate(rows):
        cells = []
        for column in range(columns):
            value = row[column] if column < len(row) else ""
            cells.append('<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>%s</w:tc>'
                         % _docx_para(value, bold=(row_index == 0)))
        body.append("<w:tr>%s</w:tr>" % "".join(cells))
    return ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>%s</w:tblPr>'
            '<w:tblGrid>%s</w:tblGrid>%s</w:tbl>' % (border, grid, "".join(body)))


def _docx_styles():
    def style(style_id, name, size, bold=False):
        return ('<w:style w:type="paragraph" w:styleId="%s"><w:name w:val="%s"/>'
                '<w:basedOn w:val="Normal"/><w:qFormat/>'
                '<w:pPr><w:spacing w:before="120" w:after="120" w:line="276" '
                'w:lineRule="auto"/></w:pPr>'
                '<w:rPr>%s<w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr></w:style>'
                % (style_id, name, "<w:b/>" if bold else "", size, size))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:styles xmlns:w="%s">'
            '<w:docDefaults><w:rPrDefault><w:rPr>'
            '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="SimSun"/>'
            '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault>'
            '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" '
            'w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
            '<w:name w:val="Normal"/><w:qFormat/></w:style>'
            '%s%s%s%s</w:styles>' % (
                W_NS, style("Title", "Title", 40, True),
                style("Heading1", "heading 1", 32, True),
                style("Heading2", "heading 2", 28, True),
                style("Heading3", "heading 3", 24, True)))


def write_docx(markdown_text, path, title="Document", author="Period 4 Skill"):
    """Write a real, editable DOCX from a conservative Markdown subset."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (markdown_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = min(len(heading.group(1)), 3)
            blocks.append(_docx_para(heading.group(2).strip(),
                                     style="Heading%d" % level, bold=True))
            i += 1
            continue
        if re.match(r"^\s*\|", line):
            rows = []
            while i < len(lines) and re.match(r"^\s*\|", lines[i]):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.match(r"^:?-{2,}:?$", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            blocks.append(_docx_table(rows))
            continue
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line):
            text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
            blocks.append(_docx_para("• " + text))
            i += 1
            continue
        buf = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,6})\s+|^\s*\|", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        blocks.append(_docx_para(" ".join(buf)))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="%s"><w:body>%s'
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
                '</w:sectPr></w:body></w:document>'
                % (W_NS, "".join(blocks)))
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document)
        zf.writestr("word/styles.xml", _docx_styles())
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
    return path


def docx_reopen_check(path):
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                return False, "bad member: %s" % bad
            xml = zf.read("word/document.xml")
        if b"<w:document" not in xml:
            return False, "document.xml lacks w:document"
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def write_pdf_preview(text, path, title="Preview"):
    """Write a real text PDF preview through matplotlib when available.

    This is a content preview, not a rendering of a DOCX layout.  Callers must
    label it accordingly in result.json.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.backends.backend_pdf import PdfPages
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        return None, "matplotlib unavailable: %s" % exc
    try:
        lines = []
        for raw in (text or "").splitlines():
            while len(raw) > 96:
                lines.append(raw[:96])
                raw = raw[96:]
            lines.append(raw)
        per_page = 46
        with PdfPages(str(path)) as pdf:
            for start in range(0, max(len(lines), 1), per_page):
                fig = plt.figure(figsize=(8.27, 11.69))
                fig.text(0.06, 0.95, title, fontsize=12, weight="bold", va="top")
                y = 0.91
                for line in lines[start:start + per_page]:
                    fig.text(0.06, y, line, fontsize=8.5, va="top")
                    y -= 0.019
                pdf.savefig(fig)
                plt.close(fig)
        return path, ""
    except Exception as exc:  # noqa: BLE001
        return None, "preview failed: %s" % exc


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_artifacts(result, output_dir):
    problems = []
    for item in result.get("artifacts", []):
        path = Path(output_dir) / item.get("path", "")
        if not path.exists():
            problems.append("missing artifact: %s" % item.get("path"))
        elif path.is_file() and path.stat().st_size == 0:
            problems.append("empty artifact: %s" % item.get("path"))
    return problems


def validate_evidence(evidence, docs, require_quote=True):
    """Check evidence locators and quotes against extracted documents."""
    by_path = {}
    for doc in docs:
        by_path[doc.get("rel_path") or doc.get("path")] = doc
        by_path[doc.get("path")] = doc
    problems = []
    for index, item in enumerate(evidence or [], 1):
        source = item.get("source") or {}
        if isinstance(source, str):
            source = {"path": source}
        path = source.get("path") or item.get("path") or ""
        doc = by_path.get(path)
        if doc is None:
            problems.append("evidence %d: source path not found: %s" % (index, path))
            continue
        quote = item.get("quote") or ""
        if require_quote:
            ok, reason = quote_supported(doc_text(doc), quote)
            if not ok:
                problems.append("evidence %d: %s" % (index, reason))
    return problems


def check_numbers_preserved(source, target, allow_new=None):
    missing, extra = numbers_missing(source, target)
    allow_new = allow_new or []
    extra = [x for x in extra if x["number"] not in allow_new]
    return {"ok": not missing and not extra, "missing": missing, "extra": extra}


def check_citations_preserved(source, target):
    src = Counter(t["raw"] for t in citation_tokens(source))
    tgt = Counter(t["raw"] for t in citation_tokens(target))
    missing = []
    for token, count in (src - tgt).items():
        missing.append({"token": token, "missing_count": count})
    return {"ok": not missing, "missing": missing}


def check_terms(glossary, text):
    problems = []
    for entry in glossary or []:
        target = entry.get("target") or entry.get("zh") or entry.get("en") or ""
        if not target:
            continue
        if target not in (text or ""):
            problems.append("glossary target not found: %s" % target)
    return problems


def find_anchor(text, anchor, context=80):
    if not anchor:
        return None
    index = (text or "").find(anchor)
    if index < 0:
        return None
    return {"start": index, "end": index + len(anchor),
            "before": (text or "")[max(0, index - context):index],
            "after": (text or "")[index + len(anchor):index + len(anchor) + context]}


def replace_once(text, before, after):
    count = (text or "").count(before)
    if count != 1:
        return None, "anchor occurs %d times" % count
    return text.replace(before, after, 1), "ok"


def sentence_diff(before, after):
    """Sentence-level diff used when the model supplies a full rewritten text."""
    a = [s["text"] for s in split_sentences(before)]
    b = [s["text"] for s in split_sentences(after)]
    matcher = difflib.SequenceMatcher(None, a, b)
    changes = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append({"op": tag,
                        "before": " ".join(a[i1:i2]),
                        "after": " ".join(b[j1:j2]),
                        "before_index": [i1, i2], "after_index": [j1, j2]})
    return changes


# ---------------------------------------------------------------------------
# CLI context
# ---------------------------------------------------------------------------

class Context:
    def __init__(self, input_dir, work_dir, output_dir, skill_dir, task_config):
        self.input = Path(input_dir)
        self.work = Path(work_dir)
        self.output = Path(output_dir)
        self.skill = Path(skill_dir)
        self.config = task_config or {}
        self.case = load_json(self.input / "case.json", {})
        self.task = read_text(self.input / "task.md")
        self.case_id = str(self.case.get("case_id") or self.case.get("id")
                           or self.config.get("case_id") or "CASE")
        self.contract = self.case.get("output_contract") or {}
        self.inv = []
        self.docs = []
        self.profile = self.config.get("profile", "reading")
        self.result = ResultBuilder(self.case_id, self.config.get("family", "general"),
                                    self.output, self.work, case=self.case,
                                    task_text=self.task, profile=self.profile)

    def ensure(self):
        ensure_dirs(self.work, self.output)

    def load(self, ocr=True):
        self.ensure()
        self.inv, self.docs = load_materials(self.input, ocr=ocr,
                                             tmp_dir=self.work / ".ocr")
        write_json(self.work / "inventory.json", {
            "input_dir": str(self.input),
            "case_id": self.case_id,
            "files": self.inv,
            "documents": [{"source_id": d.get("source_id"),
                           "path": d.get("rel_path"),
                           "kind": d.get("kind"),
                           "chars": len(doc_text(d)),
                           "warnings": d.get("warnings", [])}
                          for d in self.docs],
            "generated_at": now_iso(),
        })
        return self.inv, self.docs

    def text(self):
        return all_text(self.docs)

    def primary_text(self):
        for doc in self.docs:
            if doc_text(doc).strip():
                return doc_text(doc), doc
        return "", None

    def write_context(self, extra=None):
        payload = {
            "case_id": self.case_id,
            "family": self.config.get("family"),
            "profile": self.profile,
            "case": self.case,
            "task_excerpt": excerpt(self.task, 4000),
            "output_contract": self.contract,
            "files": self.inv,
            "documents": [{"source_id": d.get("source_id"),
                           "path": d.get("rel_path"),
                           "kind": d.get("kind"),
                           "chars": len(doc_text(d)),
                           "warnings": d.get("warnings", [])}
                          for d in self.docs],
        }
        if extra:
            payload.update(extra)
        write_json(self.work / "context.json", payload)
        return payload

    def provisional(self, status="partial", reason="prepare stage completed; waiting for model-authored content"):
        self.result.change("prepare", "input", "inventory and source extraction completed")
        self.result.uncertainty("deliverable_pending", reason,
                                "result is provisional", "write the requested work file and run finalize")
        return self.result.finalize(status)


def print_json(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
