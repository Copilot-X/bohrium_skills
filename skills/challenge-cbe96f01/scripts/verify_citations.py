#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic offline pre-check for citation and fact verification.

The script reads the case materials under ``--input`` (Markdown, plain text,
JSON, CSV, TeX, BibTeX, RIS; PDF/DOCX text is reused from
``work/materials.json`` after ``run_case.py prepare``), extracts

* in-text citations (``[1]`` / ``[A]`` / author-year / ``\\cite`` / ``[@key]``),
* reference-list entries (``[A] ...`` / numbered / author-year / BibTeX / RIS
  / JSON / CSV),
* retraction notices and preprint/version notes,

then matches citations to entries and writes *candidates* for four issue types:

``missing_reference``, ``uncited_claim``, ``citation_mismatch``,
``quote_mismatch``.

The script is deliberately deterministic and conservative.  It never invents a
reference, a quote or a source value, and it never decides that a citation
supports a claim on its own: every row is marked ``needs_review`` so the model
can perform the semantic check and reject false positives.

Outputs (written even for empty or broken input)::

    work/citation-check.json        full deterministic result
    work/review-candidates.json     merge-ready candidate arrays
    output/citation-check.json      copy for the result bundle
    output/citation-precheck.md     human-readable review sheet

Exit code is 0 for every input shape (empty input, missing reference list,
unreadable material).  A crash is recorded as ``status=error`` and still exits
0 when the error report can be written.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tempfile
import traceback
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.1.0"
MAX_BYTES = 5_000_000
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".rst", ".org", ".tex", ".latex",
    ".json", ".csv", ".bib", ".ris", ".yaml", ".yml", ".log",
}
SKIP_NAMES = {"case.json", "task.md"}
STATUS_VALUES = {"published", "preprint", "retracted", "unknown"}
ISSUE_TYPES = {
    "missing_reference", "uncited_claim", "citation_mismatch", "quote_mismatch",
}

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
URL_RE = re.compile(r"https?://[^\s)>\]]+")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
NUMBER_RE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)(?![\w])")
PAGE_RANGE_RE = re.compile(r"\b\d{1,4}\s*[-\u2013]\s*\d{1,4}\b")
VOLUME_ISSUE_RE = re.compile(
    r"\b\d{1,4}\s*\(\s*\d{1,3}\s*\)(?:\s*[,:]\s*\d{1,4}(?:\s*[-\u2013]\s*\d{1,4})?)?")
WORD_NUMBER_RE = re.compile(
    r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b"
    r"(?=[\s-]+(?:year|years|fold|participants|patients|adults|studies|"
    r"trials|months|weeks|days|percent|%))", re.I)
WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}

MD_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+\S")
REF_HEADING_RE = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?(?:references?|reference\s+list|bibliography|"
    r"literature\s+cited|works\s+cited|\u53c2\u8003\u6587\u732e)"
    r"\s*[:\uff1a]?\s*$", re.I)
SECTION_STOP_RE = re.compile(
    r"^(?:#{1,6}\s*)?(?:abstract|summary|introduction|background|"
    r"related\s+work|methods?|materials?\s+and\s+methods?|results?|"
    r"discussion|conclusions?|acknowledg(?:e)?ments?|appendix|"
    r"supplementary|author\s+contributions|funding|"
    r"conflict\s+of\s+interest|data\s+availability|"
    r"\u6458\u8981|\u5f15\u8a00|\u524d\u8a00|\u65b9\u6cd5|\u7ed3\u679c|"
    r"\u8ba8\u8bba|\u7ed3\u8bba|\u81f4\u8c22|\u9644\u5f55)\s*[:\uff1a]?\s*$",
    re.I)

ENTRY_MARK_RE = re.compile(
    r"^\s*(?:"
    r"\[(?P<label>[^\]]{1,40})\]"
    r"|(?P<num>\d{1,3})[.)]\s+"
    r"|[-*+]\s+"
    r"|\\bibitem(?:\[[^\]]*\])?\{(?P<key>[^}]+)\}"
    r")\s*(?P<rest>.*)$")

BRACKET_CITE_RE = re.compile(
    r"\[((?:[A-Za-z]|\d{1,3})(?:\s*[,;\u2013-]\s*(?:[A-Za-z]|\d{1,3}))*)\]")
TEX_CITE_RE = re.compile(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])?\{([^{}]+)\}")
PANDOC_CITE_RE = re.compile(r"\[(-?@[^\]\s]+(?:\s*;\s*-?@[^\]\s]+)*)\]")
PAREN_RE = re.compile(r"[\(\uff08]([^()\uff08\uff09]{2,260})[\)\uff09]")
YEAR_IN_TEXT_RE = re.compile(r"\b((?:19|20)\d{2})[a-z]?\b")
NARRATIVE_CITE_RE = re.compile(
    r"\b([A-Z][A-Za-z'\u2019\-]+)\s+"
    r"(?:et\s+al\.|and\s+[A-Z][A-Za-z'\u2019\-]+)?\s*"
    r"\(((?:19|20)\d{2})\)")
QUOTE_RE = re.compile(r"[\"\u201c]([^\"\u201d]{8,400})[\"\u201d]")

ABBREVIATIONS = (
    "e.g.", "i.e.", "et al.", "vs.", "fig.", "figs.", "eq.", "eqs.",
    "ref.", "refs.", "no.", "nos.", "dr.", "prof.", "approx.", "cf.",
    "sec.", "tab.", "vol.", "pp.", "p.", "resp.", "suppl.",
)
SENT_BOUNDARY_RE = re.compile(
    r"(?<=[.!?\u3002\uff01\uff1f])\s+(?=[\"\u201c\u2018'(\[]?[A-Z0-9\u4e00-\u9fff])"
    r"|(?<=[\u3002\uff01\uff1f\uff1b])(?=[\u4e00-\u9fffA-Z0-9])")

SUBSTANTIVE_CUE_RE = re.compile(
    r"\b(reports?|reported|states?|stated|argues?|argued|found|finds?|"
    r"shows?|shown|conclude[sd]?|notes?|observed?|suggests?|indicates?|"
    r"describes?|warns?|mentions?|cannot|unsupported|no\s+empirical)\b"
    r"|\u62a5\u544a|\u8868\u660e|\u6307\u51fa|\u8ba4\u4e3a|\u53d1\u73b0|"
    r"\u8bf4\u660e|\u7ed3\u8bba", re.I)
PLACEHOLDER_RE = re.compile(
    r"(not\s+included|not\s+supplied|not\s+provided|"
    r"not\s+in\s+the\s+(?:supplied\s+)?reference\s+list|unavailable|"
    r"missing|no\s+reference|\u672a\u63d0\u4f9b|\u672a\u5217\u5165|"
    r"\u7f3a\u5931|\u65e0\u5bf9\u5e94)", re.I)
OPINION_RE = re.compile(
    r"\b(opinion|commentary|editorial|perspective|letter\s+to\s+the\s+editor)\b"
    r"|\u8bc4\u8bba|\u89c2\u70b9|\u793e\u8bba", re.I)
RETRACTION_RE = re.compile(r"\bretract(?:ed|ion|s)?\b|\u64a4\u7a3f", re.I)
PREPRINT_RE = re.compile(r"\bpreprint\b|\u9884\u5370\u672c", re.I)
VERSION_RE = re.compile(
    r"\b(?:published\s+version|version|reported)\b|\u6b63\u5f0f\u7248|"
    r"\u7248\u672c|\u53d1\u8868", re.I)

ASSERTIVE_CUES = (
    "cause", "causes", "caused", "prove", "proves", "proved",
    "establish", "establishes", "established", "confirm", "confirms",
    "confirmed", "demonstrate", "demonstrates", "demonstrated",
    "reverse", "reverses", "reversed", "eliminate", "eliminates",
    "cure", "cures", "persist", "persists", "persisted", "persistence",
    "always", "never", "definitive", "definitively",
    "\u5bfc\u81f4", "\u5f15\u8d77", "\u8bc1\u660e", "\u8bc1\u5b9e",
    "\u786e\u5b9a", "\u5fc5\u7136", "\u603b\u662f", "\u9006\u8f6c",
)
HEDGE_CUES = (
    "may", "might", "could", "suggest", "suggests", "possibly",
    "likely", "associated", "association", "correlat", "consistent with",
    "\u53ef\u80fd", "\u63d0\u793a", "\u76f8\u5173", "\u5173\u8054",
)
LIMITATION_CUES = (
    "association", "correlat", "cannot be inferred", "cannot",
    "unsupported", "no empirical", "preliminary", "limited",
    "observational", "cross-sectional", "commentary", "opinion",
    "argues that", "not causal", "no causal",
    "\u76f8\u5173", "\u5173\u8054", "\u65e0\u6cd5\u63a8\u65ad",
    "\u4e0d\u652f\u6301", "\u521d\u6b65", "\u5c40\u9650",
)

CLAIM_VERB_RE = re.compile(
    r"\b(found|finds?|reported?|reports?|shows?|demonstrat\w*|"
    r"establish\w*|improv\w*|increas\w*|decreas\w*|reduc\w*|persist\w*|"
    r"caus\w*|proves?|confirm\w*|suggest\w*)\b|\u53d1\u73b0|"
    r"\u8868\u660e|\u663e\u793a|\u8bc1\u660e|\u8bc1\u5b9e|\u63d0\u9ad8|"
    r"\u964d\u4f4e|\u51cf\u5c11|\u589e\u52a0|\u5bfc\u81f4|\u6539\u5584|"
    r"\u6301\u7eed", re.I)
STAT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|\u2030|percent)|\u767e\u5206\u4e4b|\u6210|\u500d",
    re.I)
DISCLAIMER_RE = re.compile(
    r"(synthetic\s+only|fabricated|not\s+a\s+real\s+paper|for\s+audit\s+"
    r"testing|\u4ec5\u4f9b\u6d4b\u8bd5|\u793a\u4f8b|\u865a\u6784)", re.I)
OWN_RESULT_RE = re.compile(
    r"\b(?:we|our)\s+(?:found|observed|report|reported|show|showed|"
    r"measured|tested|randomi[sz]ed|assigned|analy[sz]ed|interviewed)\b|"
    r"\bin\s+this\s+study\b|\bparticipants?\s+(?:in\s+our|showed|reported|"
    r"were\s+(?:assigned|randomi[sz]ed))\b|"
    r"\u672c\u7814\u7a76|\u6211\u4eec\u53d1\u73b0|\u6211\u4eec\u7684\u7ed3\u679c|\u53c2\u4e0e\u8005", re.I)
EXTERNAL_OR_GENERAL_RE = re.compile(
    r"\b(?:all|everyone|universal|generaliz\w*|previous|prior|studies|"
    r"literature|reported\s+by|according\s+to)\b|"
    r"\u6240\u6709|\u666e\u904d|\u5df2\u6709\u7814\u7a76|\u6587\u732e|"
    r"\u636e.*\u62a5\u9053", re.I)

SECTION_HEADING_RE = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?(?:\d+(?:\.\d+)*[.)]?\s*)?"
    r"(?:abstract|summary|introduction|background|"
    r"related\s+work|methods?|methodology|materials?\s+and\s+methods?|"
    r"results?|findings?|results\s+and\s+discussion|discussion|"
    r"conclusions?|limitations?|references?|bibliography|reference\s+list|"
    r"works\s+cited|literature\s+cited|acknowledg(?:e)?ments?|appendix|"
    r"supplementary|摘要|引言|前言|背景|方法|材料与方法|结果|讨论|结论|"
    r"局限|参考文献)\s*[:：]?\s*$", re.I)
SECTION_ORDER = ["abstract", "introduction", "methods", "results",
                 "discussion", "conclusion", "limitations", "references"]
SOURCE_DESIGN_RE = re.compile(
    r"\b(?:cross[- ]sectional|observational|survey|cohort|case[- ]control|"
    r"qualitative|interview|commentary|opinion|editorial|protocol|"
    r"no\s+intervention|no\s+manipulation|not\s+tested|retracted)\b|"
    r"横断面|观察性|调查|队列|病例对照|定性|访谈|评论|社论|无干预|未测试",
    re.I)
CLAIM_INTERVENTION_RE = re.compile(
    r"\b(?:intervention|training|treatment|efficacy|caus(?:e|es|ed)|"
    r"improve[sd]?|reduce[sd]?|increase[sd]?|reverse[sd]?|"
    r"effect\s+of|leads?\s+to|persist(?:s|ed)?)\b|"
    r"干预|训练|治疗|疗效|导致|引起|改善|降低|提高|逆转|持续", re.I)
FACET_BY_SUBTYPE = {
    "numeric_mismatch": "fact_consistency",
    "year_mismatch": "citation_support",
    "retracted_source": "citation_support",
    "unresolved_label": "citation_support",
    "invalid_or_placeholder_entry": "citation_support",
    "overclaim_or_negation": "citation_support",
    "design_scope_mismatch": "citation_support",
    "quote_not_in_source": "citation_support",
    "no_inline_citation": "citation_support",
}

SUMMARY_FIELDS = ("title", "journal", "booktitle", "venue", "year",
                  "doi", "abstract", "note", "notes", "summary",
                  "source_text", "quote", "status")
TEXT_FIELDS = ("text", "content", "body", "manuscript", "claim",
               "sentence", "paragraph", "abstract", "quote", "statement")
REF_KEYS = {"references", "reference", "bibliography", "refs", "sources",
            "reference_list", "reference list"}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def load_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def norm_space(text):
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("\u00ad", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", value).strip()


def norm_match(text):
    return re.sub(r"[\s\u3000]+", "", norm_space(text).lower())


def quote_present(source, quote):
    """Whitespace/NFKC-tolerant verbatim check with ellipsis support."""
    source_norm = norm_match(source)
    quote_norm = norm_match(quote)
    if not quote_norm:
        return False
    if quote_norm in source_norm:
        return True
    parts = [p for p in re.split(r"\u2026|\.\.\.", quote) if norm_match(p)]
    if len(parts) > 1:
        return all(norm_match(p) in source_norm for p in parts)
    return False


def excerpt(text, limit=220):
    value = norm_space(text)
    return value if len(value) <= limit else value[:limit - 1] + "\u2026"


def line_of(text, position):
    return text.count("\n", 0, max(0, position)) + 1


def overlap(a, b):
    return not (a[1] <= b[0] or b[1] <= a[0])


def is_text_suffix(path):
    return Path(path).suffix.lower() in TEXT_SUFFIXES


def significant_tokens(text):
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "these",
        "those", "study", "trial", "report", "reports", "randomized",
        "randomised", "older", "adults", "among", "into", "using", "based",
    }
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{4,}", text or "")
            if w.lower() not in stop]


def numeric_values(text):
    """Numeric values used for claim/source consistency.

    DOIs, URLs, years, page ranges and volume(issue) metadata are removed so
    that bibliographic noise does not masquerade as source evidence.
    """
    cleaned = DOI_RE.sub(" ", text or "")
    cleaned = URL_RE.sub(" ", cleaned)
    cleaned = YEAR_RE.sub(" ", cleaned)
    cleaned = PAGE_RANGE_RE.sub(" ", cleaned)
    cleaned = VOLUME_ISSUE_RE.sub(" ", cleaned)
    values = []
    for match in NUMBER_RE.finditer(cleaned):
        raw = match.group(1).replace(",", ".")
        try:
            values.append(float(raw))
        except ValueError:
            continue
    for match in WORD_NUMBER_RE.finditer(cleaned):
        values.append(float(WORD_NUMBERS.get(match.group(1).lower(), 0)))
    return sorted({round(v, 6) for v in values})


def strip_citations(sentence, citations):
    out = sentence
    for citation in sorted(citations, key=lambda c: c["start"], reverse=True):
        out = out[:citation["start"]] + " " + out[citation["end"]:]
    return out


def dedupe_evidence(items):
    seen, out = set(), []
    for item in items:
        key = (item.get("path", ""), item.get("locator", ""),
               norm_space(item.get("quote", "")))
        if not all(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def evidence(path, locator, quote):
    return {"path": str(path or ""), "locator": str(locator or ""),
            "quote": str(quote or "")}


# ---------------------------------------------------------------------------
# sentence segmentation
# ---------------------------------------------------------------------------

def _protect_abbreviations(text):
    protected = text
    for abbr in sorted(ABBREVIATIONS, key=len, reverse=True):
        protected = re.sub(re.escape(abbr),
                           lambda m: m.group(0).replace(".", "\u2024"),
                           protected, flags=re.I)
    return protected


def split_sentences(line):
    """Return (sentence_text, start, end) spans for one physical line."""
    line = str(line or "").replace("\r", "")
    if not line.strip():
        return []
    protected = _protect_abbreviations(line)
    spans, start = [], 0
    for match in SENT_BOUNDARY_RE.finditer(protected):
        end = match.start()
        if end > start:
            spans.append((start, end))
        start = match.end()
    if start < len(protected):
        spans.append((start, len(protected)))
    out = []
    for a, b in spans:
        raw = line[a:b]
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        text = raw.strip()
        if text:
            out.append((text, a + left, a + right))
    return out


# ---------------------------------------------------------------------------
# document loading
# ---------------------------------------------------------------------------

def locate_reference_section(text):
    """Return (body_lines, ref_start_line, ref_lines) with absolute line nums."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ref_index = None
    level = None
    for index, line in enumerate(lines):
        if REF_HEADING_RE.match(line):
            ref_index = index
            heading = MD_HEADING_RE.match(line)
            level = len(heading.group(1)) if heading else None
            break
    if ref_index is None:
        return [(i + 1, line) for i, line in enumerate(lines)], None, []
    end = len(lines)
    if level is not None:
        for index in range(ref_index + 1, len(lines)):
            heading = MD_HEADING_RE.match(lines[index])
            if heading and len(heading.group(1)) <= level:
                end = index
                break
    else:
        for index in range(ref_index + 1, len(lines)):
            if SECTION_STOP_RE.match(lines[index].strip()):
                end = index
                break
    body = [(i + 1, lines[i]) for i in range(ref_index)]
    refs = [(i + 1, lines[i]) for i in range(ref_index + 1, end)]
    return body, ref_index + 1, refs


def canonical_section_name(line):
    text = norm_space(re.sub(r"^\s*#{1,6}\s*", "", str(line or "")))
    text = text.strip(" .:：")
    low = text.lower()
    if re.search(r"\babstract\b|\bsummary\b|摘要", low):
        return "abstract"
    if re.search(r"\bintroduction\b|\bbackground\b|related\s+work|引言|前言|背景", low):
        return "introduction"
    if re.search(r"\bmethods?\b|\bmethodology\b|materials?\s+and\s+methods?|"
                 r"方法|材料与方法", low):
        return "methods"
    if re.search(r"\bresults?\b|\bfindings?\b|结果", low):
        return "results"
    if re.search(r"\bdiscussion\b|讨论", low):
        return "discussion"
    if re.search(r"\bconclusions?\b|concluding remarks|结论|总结", low):
        return "conclusion"
    if re.search(r"\blimitations?\b|局限", low):
        return "limitations"
    if re.search(r"\breferences?\b|\bbibliography\b|reference\s+list|"
                 r"works\s+cited|literature\s+cited|参考文献", low):
        return "references"
    return ""


def segment_sections(text):
    """Return (sections, line_section) with 1-based inclusive line ranges."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    starts = []
    for index, line in enumerate(lines, 1):
        name = canonical_section_name(line) if SECTION_HEADING_RE.match(line) else ""
        if name:
            starts.append((index, name))
    sections = []
    for pos, (line_no, name) in enumerate(starts):
        end = starts[pos + 1][0] - 1 if pos + 1 < len(starts) else len(lines)
        sections.append({"name": name, "line_start": line_no,
                         "line_end": max(line_no, end)})
    line_section = {}
    for section in sections:
        for line_no in range(section["line_start"], section["line_end"] + 1):
            line_section.setdefault(line_no, section["name"])
    # Text before the first explicit heading is still part of the body.
    for line_no in range(1, (starts[0][0] if starts else len(lines) + 1)):
        line_section.setdefault(line_no, "body")
    return sections, line_section


def segment_paragraphs(text, path):
    """Paragraph index for claim/section coverage; locators stay line-based."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paragraphs, buf, start = [], [], None
    section_by_line = segment_sections(text)[1]
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            if buf:
                paragraphs.append({
                    "id": "PAR%d" % (len(paragraphs) + 1),
                    "path": path, "line_start": start,
                    "line_end": line_no - 1,
                    "section": section_by_line.get(start, "body"),
                    "text": " ".join(buf),
                    "locator": "%s:L%d" % (path, start),
                })
                buf, start = [], None
            continue
        if start is None:
            start = line_no
        buf.append(stripped)
    if buf:
        paragraphs.append({
            "id": "PAR%d" % (len(paragraphs) + 1),
            "path": path, "line_start": start, "line_end": len(lines),
            "section": section_by_line.get(start, "body"),
            "text": " ".join(buf), "locator": "%s:L%d" % (path, start),
        })
    return paragraphs


def section_for_locator(doc, locator):
    match = re.search(r":L(\d+)\b", str(locator or ""))
    if not match:
        return ""
    return (doc.get("line_section") or {}).get(int(match.group(1)), "")


def parse_reference_blocks(ref_lines, path):
    entries = []
    current = None
    for line_no, line in ref_lines:
        text = line.rstrip()
        if not text.strip():
            continue
        marker = ENTRY_MARK_RE.match(text)
        if marker:
            if current:
                entries.append(current)
            label = (marker.group("label") or marker.group("key")
                     or marker.group("num") or "")
            rest = (marker.group("rest") or "").strip()
            current = {
                "label": label.strip(),
                "label_basis": "explicit" if label else "position",
                "raw": rest or text.strip(),
                "line_start": line_no,
                "line_end": line_no,
                "path": path,
                "kind": "reference_entry",
            }
            continue
        if current is None:
            current = {
                "label": "",
                "label_basis": "position",
                "raw": text.strip(),
                "line_start": line_no,
                "line_end": line_no,
                "path": path,
                "kind": "reference_entry",
            }
            continue
        previous = current["raw"].rstrip()
        continuation = (bool(re.match(r"^\s+", text))
                        or bool(re.match(r"^[a-z,;:)\]]", text.strip()))
                        or not previous.endswith((".", "\u3002", "?", "!")))
        if continuation:
            current["raw"] = (previous + " " + text.strip()).strip()
            current["line_end"] = line_no
        else:
            entries.append(current)
            current = {
                "label": "",
                "label_basis": "position",
                "raw": text.strip(),
                "line_start": line_no,
                "line_end": line_no,
                "path": path,
                "kind": "reference_entry",
            }
    if current:
        entries.append(current)
    for index, entry in enumerate(entries):
        if not entry["label"]:
            entry["label"] = str(index + 1)
        enrich_reference(entry)
    return entries


def _extract_source_text(raw):
    cut = 0
    for pattern in (DOI_RE, VOLUME_ISSUE_RE, PAGE_RANGE_RE):
        for match in pattern.finditer(raw):
            cut = max(cut, match.end())
    tail = raw[cut:].lstrip(" .,;:|\u3002\uff0c")
    if not tail:
        sentences = re.split(r"(?<=[.!?\u3002])\s+", raw)
        tail = " ".join(sentences[2:]) if len(sentences) > 2 else ""
    tail = tail.strip()
    if not tail or not SUBSTANTIVE_CUE_RE.search(tail):
        return ""
    return tail


def enrich_reference(entry):
    raw = entry.get("raw", "")
    doi_match = DOI_RE.search(raw)
    entry["doi"] = doi_match.group(0).rstrip(".") if doi_match else ""
    paren_year = re.search(r"\((19|20)\d{2}[a-z]?\)", raw)
    year_match = paren_year or YEAR_RE.search(raw)
    entry["year"] = (re.search(r"(?:19|20)\d{2}", year_match.group(0)).group(0)
                     if year_match else "")
    author = ""
    match = re.match(r"^\s*(?:\[[^\]]+\]\s*)?([A-Z][A-Za-z'\u2019\-]+)", raw)
    if match and match.group(1).lower() not in {
            "post", "the", "an", "a", "this", "these", "in", "on", "for"}:
        author = match.group(1)
    if not author:
        cn = re.match(r"^\s*([\u4e00-\u9fff]{2,4})(?:\u7b49|\u4eba)?", raw)
        if cn:
            author = cn.group(1)
    entry["first_author"] = author
    entry["display_author"] = author
    if not entry["display_author"]:
        label_match = re.match(
            r"^\s*(.{2,50}?)\s*\((?:19|20)\d{2}\)", raw)
        if label_match:
            entry["display_author"] = label_match.group(1).strip(" ,.。")
    title_match = re.search(
        r"\((?:19|20)\d{2}[a-z]?\)[.\s:,\-]*(.+?)(?:\.\s|\s\|\s|\s\|\s|$)", raw)
    entry["title"] = title_match.group(1).strip() if title_match else ""
    entry["has_venue"] = bool(re.search(
        r"\b(?:journal|proceedings|conference|transactions|reviews|"
        r"letters|nature|science|cell|lancet|plos|frontiers|press)\b"
        r"|\u5b66\u62a5|\u671f\u520a|\u51fa\u7248\u793e", raw, re.I)
        or re.search(r"\b\d{1,4}\s*\(\s*\d{1,3}\s*\)", raw))
    entry["is_opinion"] = bool(OPINION_RE.search(raw))
    entry["placeholder"] = bool(PLACEHOLDER_RE.search(raw))
    entry["source_text"] = _extract_source_text(raw)
    entry["use_ok"] = bool(entry.get("year")
                           and (entry.get("has_venue") or entry.get("doi"))
                           and not entry["placeholder"])
    entry["aliases"] = [normalize_label(entry.get("label") or "")]
    entry["explicit_status"] = str(entry.get("explicit_status") or "").lower()
    entry["locator"] = ("%s:L%d" % (entry.get("path"), entry.get("line_start"))
                        if entry.get("line_start") else entry.get("path", ""))
    return entry


def normalize_label(label):
    value = str(label or "").strip()
    if value.isdigit():
        return str(int(value))
    return value.upper()


def author_surname(value):
    """Extract a first-author surname from common author-field shapes."""
    text = str(value or "").strip()
    text = re.sub(r"\s+et\s+al\.?", "", text, flags=re.I)
    text = text.split(";")[0].strip()
    if "," in text:
        text = text.split(",", 1)[0].strip()
    else:
        parts = text.split()
        text = parts[-1] if parts else ""
    text = re.sub(r"[^A-Za-z一-鿿'’\-]", "", text)
    return text[:40]


def parse_bibtex_entries(text, path):
    entries = []
    index = 0
    while True:
        at = text.find("@", index)
        if at < 0:
            break
        match = re.match(r"@(\w+)\s*\{", text[at:])
        if not match:
            index = at + 1
            continue
        entry_type = match.group(1).lower()
        body_start = at + match.end()
        depth, pos = 1, body_start
        while pos < len(text) and depth:
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
            pos += 1
        body = text[body_start:pos - 1]
        if entry_type in ("comment", "preamble", "string"):
            index = pos
            continue
        key, _, fields_part = body.partition(",")
        key = key.strip()
        fields, cursor = {}, 0
        while cursor < len(fields_part):
            eq = fields_part.find("=", cursor)
            if eq < 0:
                break
            name = fields_part[cursor:eq].strip().lower()
            value, next_pos = _read_braced(fields_part, eq)
            if not value:
                comma = fields_part.find(",", eq)
                if comma < 0:
                    comma = len(fields_part)
                value = fields_part[eq + 1:comma].strip().strip('"')
                next_pos = comma
            fields[name] = re.sub(r"\s+", " ", value.replace("\n", " ")).strip()
            cursor = next_pos
            comma = fields_part.find(",", cursor)
            if comma < 0:
                break
            cursor = comma + 1
        raw = text[at:pos]
        entry = {
            "label": key or str(len(entries) + 1),
            "label_basis": "bibtex_key" if key else "position",
            "key": key,
            "raw": raw,
            "line_start": line_of(text, at),
            "line_end": line_of(text, pos),
            "path": path,
            "kind": "bibtex",
        }
        author = fields.get("author") or fields.get("authors") or ""
        author = re.split(r"\s+and\s+|;\s*", author)[0].strip()
        entry["first_author"] = author_surname(author)
        entry["year"] = str(fields.get("year") or fields.get("date") or "")[:4]
        entry["doi"] = fields.get("doi") or ""
        entry["title"] = fields.get("title") or ""
        entry["has_venue"] = bool(fields.get("journal") or fields.get("booktitle")
                                  or fields.get("publisher") or entry["doi"])
        entry["is_opinion"] = bool(OPINION_RE.search(raw))
        entry["placeholder"] = bool(PLACEHOLDER_RE.search(raw))
        abstract = fields.get("abstract") or ""
        note = fields.get("note") or fields.get("notes") or ""
        entry["source_text"] = abstract or note
        entry["explicit_status"] = (
            "preprint" if entry_type in ("unpublished", "preprint")
            or fields.get("eprint") else "")
        entry["use_ok"] = bool(entry["year"] and (entry["has_venue"]
                                                  or entry["doi"])
                               and not entry["placeholder"])
        entry["aliases"] = [normalize_label(entry["label"])]
        entry["locator"] = "%s:L%d" % (path, entry["line_start"])
        entries.append(entry)
        index = pos
    return entries


def _read_braced(text, start):
    opening = text.find("{", start)
    if opening < 0:
        return "", start
    depth, out = 0, []
    for position in range(opening, len(text)):
        char = text[position]
        if char == "{":
            depth += 1
            if depth == 1:
                continue
        elif char == "}":
            depth -= 1
            if depth == 0:
                return "".join(out), position + 1
        if depth >= 1:
            out.append(char)
    return "".join(out), len(text)


def parse_ris_entries(text, path):
    entries, current, start_line = [], {}, None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line_no, line in enumerate(lines, 1):
        match = re.match(r"^([A-Z][A-Z0-9])\s{1,2}-(.*)$", line)
        if not match:
            continue
        tag, value = match.group(1), match.group(2).strip()
        if tag == "TY":
            if current:
                entries.append(_ris_entry(current, text, path, start_line))
            current, start_line = {"TY": value}, line_no
        elif tag == "ER":
            if current:
                entries.append(_ris_entry(current, text, path, start_line))
            current, start_line = {}, None
        elif current is not None:
            current.setdefault(tag, []).append(value)
    if current:
        entries.append(_ris_entry(current, text, path, start_line))
    for index, entry in enumerate(entries):
        if not entry["label"]:
            entry["label"] = str(index + 1)
        entry["aliases"] = [normalize_label(entry["label"])]
    return entries


def _ris_entry(record, text, path, start_line):
    def first(*tags):
        for tag in tags:
            value = record.get(tag)
            if value:
                return value[0]
        return ""

    authors = record.get("AU") or record.get("A1") or []
    author = author_surname(authors[0] if authors else "")
    raw_lines = []
    for tag, values in record.items():
        for value in values if isinstance(values, list) else [values]:
            raw_lines.append("%s  - %s" % (tag, value))
    raw = "\n".join(raw_lines)
    year = first("PY", "Y1", "DA")[:4]
    doi = first("DO")
    journal = first("JO", "T2", "JF", "JA")
    abstract = first("AB", "N2")
    note = first("N1")
    entry = {
        "label": first("ID"),
        "label_basis": "ris_id",
        "key": first("ID"),
        "raw": raw,
        "line_start": start_line or 1,
        "line_end": (start_line or 1) + len(raw_lines),
        "path": path,
        "kind": "ris",
        "first_author": re.sub(r"[^A-Za-z\u4e00-\u9fff'\u2019\-]", "",
                               author)[:40],
        "year": year,
        "doi": doi,
        "title": first("TI", "T1"),
        "has_venue": bool(journal or doi),
        "is_opinion": bool(OPINION_RE.search(raw)),
        "placeholder": bool(PLACEHOLDER_RE.search(raw)),
        "source_text": abstract or note,
        "explicit_status": "",
        "use_ok": bool(year and (journal or doi)),
        "aliases": [],
        "locator": "%s:L%d" % (path, start_line or 1),
    }
    return entry


def _walk_json(value, trail, path, refs, body, notices, depth=0):
    if depth > 8:
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk_json(item, "%s[%d]" % (trail, index), path, refs, body,
                       notices, depth + 1)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        lower = str(key).lower()
        child = "%s.%s" % (trail, key) if trail else str(key)
        if lower in REF_KEYS and isinstance(item, list):
            for index, ref_item in enumerate(item):
                if isinstance(ref_item, dict):
                    refs.append(_json_reference(ref_item, path,
                                                "%s[%d]" % (child, index),
                                                len(refs)))
                elif isinstance(ref_item, str) and ref_item.strip():
                    entry = {
                        "label": "", "label_basis": "position",
                        "raw": ref_item.strip(), "line_start": None,
                        "line_end": None, "path": path, "kind": "json",
                        "explicit_status": "",
                    }
                    enrich_reference(entry)
                    refs.append(entry)
            continue
        if lower in ("retraction", "retractions", "retraction_notice",
                     "retraction_notices"):
            notices.append({
                "kind": "retraction",
                "path": path,
                "locator": "%s#%s" % (path, child),
                "text": norm_space(json.dumps(item, ensure_ascii=False)
                                   if not isinstance(item, str) else item),
            })
            continue
        if lower in ("preprint", "version_note", "version_notes",
                     "preprint_note"):
            notices.append({
                "kind": "version",
                "path": path,
                "locator": "%s#%s" % (path, child),
                "text": norm_space(json.dumps(item, ensure_ascii=False)
                                   if not isinstance(item, str) else item),
            })
            continue
        if lower in TEXT_FIELDS and isinstance(item, str):
            for line_index, line in enumerate(item.split("\n"), 1):
                body.append((None, line,
                             "%s#%s:L%d" % (path, child, line_index)))
            continue
        if lower in ("claims", "sentences", "paragraphs", "items") \
                and isinstance(item, list):
            for index, claim in enumerate(item):
                if isinstance(claim, str):
                    body.append((None, claim,
                                 "%s#%s[%d]" % (path, child, index)))
                elif isinstance(claim, dict):
                    text = (claim.get("text") or claim.get("claim")
                            or claim.get("sentence") or "")
                    if text:
                        body.append((None, str(text),
                                     "%s#%s[%d]" % (path, child, index)))
                    _walk_json(claim, "%s[%d]" % (child, index), path, refs,
                               body, notices, depth + 1)
            continue
        _walk_json(item, child, path, refs, body, notices, depth + 1)


def _json_reference(item, path, locator_suffix, index):
    def first(*keys):
        for key in keys:
            value = item.get(key)
            if isinstance(value, list):
                value = value[0] if value else ""
            if value not in (None, ""):
                return str(value)
        return ""

    label = first("label", "id", "key", "ref", "doi") or str(index + 1)
    raw = json.dumps(item, ensure_ascii=False)
    entry = {
        "label": label,
        "label_basis": "json_field",
        "key": first("key", "id"),
        "raw": raw,
        "line_start": None,
        "line_end": None,
        "path": path,
        "kind": "json",
        "first_author": first("first_author", "author", "authors"),
        "year": first("year", "publication_year", "date")[:4],
        "doi": first("doi"),
        "title": first("title"),
        "has_venue": bool(first("journal", "venue", "booktitle", "publisher")
                          or first("doi")),
        "is_opinion": bool(OPINION_RE.search(raw)),
        "placeholder": bool(PLACEHOLDER_RE.search(raw)),
        "source_text": first("abstract", "source_text", "quote", "notes",
                             "note", "summary"),
        "explicit_status": first("status", "publication_status").lower(),
        "locator": "%s#%s" % (path, locator_suffix),
        "aliases": [],
    }
    entry["first_author"] = author_surname(entry["first_author"])
    entry["use_ok"] = bool(entry["year"]
                           and (entry["has_venue"] or entry["doi"])
                           and not entry["placeholder"])
    entry["aliases"] = [normalize_label(label)]
    return entry


def parse_json_document(text, path):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], [], []
    refs, body, notices = [], [], []
    _walk_json(data, "", path, refs, body, notices)
    return refs, body, notices


def parse_csv_document(text, path):
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return [], [], []
    header = [str(cell).strip() for cell in rows[0]]
    lower = [cell.lower() for cell in header]
    ref_columns = {"title", "ti", "author", "authors", "au", "year", "py",
                   "doi", "journal", "venue", "reference", "ref", "label",
                   "id", "key"}
    text_columns = {"text", "claim", "sentence", "quote", "statement",
                    "paragraph", "content"}
    refs, body = [], []
    if len(ref_columns & set(lower)) >= 2:
        for row_index, row in enumerate(rows[1:], 2):
            mapping = {}
            for column, value in zip(lower, row):
                mapping[column] = value.strip()
            if not any(mapping.values()):
                continue
            label = (mapping.get("label") or mapping.get("id")
                     or mapping.get("key") or mapping.get("ref") or "")
            raw = " | ".join(cell for cell in row if cell.strip())
            entry = {
                "label": label or str(len(refs) + 1),
                "label_basis": "csv_field" if label else "position",
                "key": mapping.get("key") or mapping.get("id") or "",
                "raw": raw,
                "line_start": row_index,
                "line_end": row_index,
                "path": path,
                "kind": "csv",
                "first_author": (mapping.get("author")
                                 or mapping.get("authors")
                                 or mapping.get("au") or ""),
                "year": (mapping.get("year") or mapping.get("py") or "")[:4],
                "doi": mapping.get("doi") or "",
                "title": mapping.get("title") or mapping.get("ti") or "",
                "has_venue": bool(mapping.get("journal")
                                  or mapping.get("venue") or mapping.get("doi")),
                "is_opinion": bool(OPINION_RE.search(raw)),
                "placeholder": bool(PLACEHOLDER_RE.search(raw)),
                "source_text": (mapping.get("abstract")
                                or mapping.get("source_text")
                                or mapping.get("quote")
                                or mapping.get("notes")
                                or mapping.get("summary") or ""),
                "explicit_status": (mapping.get("status") or "").lower(),
                "locator": "%s:row %d" % (path, row_index),
                "aliases": [],
            }
            entry["first_author"] = author_surname(entry["first_author"])
            entry["use_ok"] = bool(entry["year"]
                                   and (entry["has_venue"] or entry["doi"])
                                   and not entry["placeholder"])
            entry["aliases"] = [normalize_label(entry["label"])]
            refs.append(entry)
        return refs, [], []
    if set(text_columns) & set(lower):
        column = next(index for index, name in enumerate(lower)
                      if name in text_columns)
        for row_index, row in enumerate(rows[1:], 2):
            if column < len(row) and row[column].strip():
                body.append((row_index, row[column].strip(),
                             "%s:row %d" % (path, row_index)))
        return [], body, []
    for row_index, row in enumerate(rows, 1):
        line = " | ".join(cell.strip() for cell in row if cell.strip())
        if line:
            body.append((row_index, line, "%s:row %d" % (path, row_index)))
    return [], body, []


def load_material_text(work_dir, rel_path):
    """Reuse prepare's extracted text for PDF/DOCX/other binary materials."""
    materials = load_json(Path(work_dir) / "materials.json", {})
    for doc in materials.get("documents") or []:
        if str(doc.get("path") or "") == rel_path and doc.get("text"):
            return str(doc["text"])
    return ""


def load_documents(input_dir, work_dir, only_material=None):
    documents = []
    input_dir = Path(input_dir)
    if not input_dir.exists():
        return documents
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(input_dir).parts):
            continue
        rel = path.relative_to(input_dir).as_posix()
        if path.name.lower() in SKIP_NAMES:
            continue
        if only_material and rel != only_material:
            continue
        if path.stat().st_size > MAX_BYTES:
            documents.append({"path": rel, "kind": "skipped", "text": "",
                              "body": [], "refs": [], "notices": [],
                              "warnings": ["file larger than %d bytes" % MAX_BYTES]})
            continue
        suffix = path.suffix.lower()
        doc = {"path": rel, "kind": suffix.lstrip(".") or "text",
               "text": "", "body": [], "refs": [], "notices": [],
               "sections": [], "line_section": {}, "paragraphs": [],
               "warnings": [], "line_mode": True}
        if suffix == ".json":
            text = read_text(path)
            doc["text"] = text
            refs, body, notices = parse_json_document(text, rel)
            doc["refs"], doc["body"], doc["notices"] = refs, body, notices
            doc["line_mode"] = False
        elif suffix == ".csv":
            text = read_text(path)
            doc["text"] = text
            refs, body, _ = parse_csv_document(text, rel)
            doc["refs"], doc["body"] = refs, body
        elif suffix == ".bib":
            text = read_text(path)
            doc["text"] = text
            doc["refs"] = parse_bibtex_entries(text, rel)
        elif suffix == ".ris":
            text = read_text(path)
            doc["text"] = text
            doc["refs"] = parse_ris_entries(text, rel)
        elif is_text_suffix(path):
            text = read_text(path)
            doc["text"] = text
            body, _, ref_lines = locate_reference_section(text)
            doc["body"] = [(line_no, line, "%s:L%d" % (rel, line_no))
                           for line_no, line in body]
            doc["refs"] = parse_reference_blocks(ref_lines, rel)
            doc["sections"], doc["line_section"] = segment_sections(text)
            doc["paragraphs"] = segment_paragraphs(text, rel)
        else:
            text = load_material_text(work_dir, rel)
            if text:
                doc["text"] = text
                body, _, ref_lines = locate_reference_section(text)
                doc["body"] = [(line_no, line,
                                "%s:extracted L%d" % (rel, line_no))
                               for line_no, line in body]
                doc["refs"] = parse_reference_blocks(ref_lines, rel)
                doc["sections"], doc["line_section"] = segment_sections(text)
                doc["paragraphs"] = segment_paragraphs(text, rel)
                doc["warnings"].append(
                    "text reused from work/materials.json; locators refer to "
                    "extracted text, not original page coordinates")
            else:
                doc["warnings"].append(
                    "unsupported material format; run run_case.py prepare "
                    "first or provide Markdown/text/JSON/CSV")
        documents.append(doc)
    return documents


# ---------------------------------------------------------------------------
# citation extraction and reference matching
# ---------------------------------------------------------------------------

def parse_label_group(value):
    labels = []
    for part in re.split(r"\s*[,;]\s*", value or ""):
        part = part.strip()
        if not part:
            continue
        match = re.match(r"^([A-Za-z]|\d{1,3})\s*[-\u2013]\s*([A-Za-z]|\d{1,3})$",
                         part)
        if match:
            left, right = match.group(1), match.group(2)
            if left.isdigit() and right.isdigit() and 0 < int(right) - int(left) <= 100:
                labels.extend(str(v) for v in range(int(left), int(right) + 1))
                continue
            if left.isalpha() and right.isalpha():
                start, end = ord(left.upper()), ord(right.upper())
                if 0 < end - start <= 26:
                    labels.extend(chr(v) for v in range(start, end + 1))
                    continue
        labels.append(part)
    return [normalize_label(label) for label in labels if label]


def first_surname(text):
    value = re.sub(r"^(?:e\.g\.|i\.e\.|see|cf\.|reviewed\s+in)\s*,?\s*", "",
                   str(text or "").strip(), flags=re.I)
    match = re.match(r"([A-Z][A-Za-z'\u2019\-]+)", value)
    if match:
        return match.group(1)
    match = re.match(r"([\u4e00-\u9fff]{2,4})(?:\u7b49|\u4eba)?", value)
    return match.group(1) if match else ""


def parse_parenthetical_citation(inner):
    pairs = []
    for part in re.split(r"[;\uff1b]", inner or ""):
        year_match = YEAR_IN_TEXT_RE.search(part)
        if not year_match:
            continue
        head = part[:year_match.start()].strip(" ,\uff0c")
        if not head or re.search(r"\d", head):
            continue
        surname = first_surname(head)
        if surname:
            pairs.append((surname, year_match.group(1)))
    return pairs


def extract_citations(sentence):
    citations = []
    spans = []

    for match in PANDOC_CITE_RE.finditer(sentence):
        keys = [part.strip().lstrip("-@").strip()
                for part in match.group(1).split(";") if part.strip()]
        keys = [key for key in keys if key]
        if keys:
            citations.append({"raw": match.group(0), "kind": "pandoc",
                              "labels": [], "pairs": [], "keys": keys,
                              "start": match.start(), "end": match.end()})
            spans.append((match.start(), match.end()))

    for match in TEX_CITE_RE.finditer(sentence):
        keys = [key.strip() for key in match.group(1).split(",") if key.strip()]
        if keys:
            citations.append({"raw": match.group(0), "kind": "tex",
                              "labels": [], "pairs": [], "keys": keys,
                              "start": match.start(), "end": match.end()})
            spans.append((match.start(), match.end()))

    for match in BRACKET_CITE_RE.finditer(sentence):
        span = (match.start(), match.end())
        if any(overlap(span, used) for used in spans):
            continue
        before = sentence[match.start() - 1] if match.start() else ""
        after = sentence[match.end()] if match.end() < len(sentence) else ""
        content = match.group(1)
        if before == "!" or after in "([:":
            continue
        if content.strip().lower() in ("x", " ", "todo", "citation"):
            continue
        labels = parse_label_group(content)
        if not labels:
            continue
        citations.append({"raw": match.group(0), "kind": "label",
                          "labels": labels, "pairs": [], "keys": [],
                          "start": match.start(), "end": match.end()})
        spans.append(span)

    for match in PAREN_RE.finditer(sentence):
        span = (match.start(), match.end())
        if any(overlap(span, used) for used in spans):
            continue
        pairs = parse_parenthetical_citation(match.group(1))
        if pairs:
            citations.append({"raw": match.group(0), "kind": "author-year",
                              "labels": [], "pairs": pairs, "keys": [],
                              "start": match.start(), "end": match.end()})
            spans.append(span)

    for match in NARRATIVE_CITE_RE.finditer(sentence):
        span = (match.start(), match.end())
        if any(overlap(span, used) for used in spans):
            continue
        citations.append({"raw": match.group(0), "kind": "author-year",
                          "labels": [], "pairs": [(match.group(1),
                                                   match.group(2))],
                          "keys": [], "start": match.start(),
                          "end": match.end()})
        spans.append(span)

    citations.sort(key=lambda item: item["start"])
    return citations


def normalize_name(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", value.lower())


def build_indexes(refs):
    by_label, by_key, by_author = {}, {}, {}
    for ref in refs:
        for label in ref.get("aliases") or [ref.get("label", "")]:
            if label:
                by_label.setdefault(normalize_label(label), []).append(ref)
        if ref.get("key"):
            by_key.setdefault(str(ref["key"]).lower(), []).append(ref)
        if ref.get("first_author") and ref.get("year"):
            key = (normalize_name(ref["first_author"]), str(ref["year"]))
            by_author.setdefault(key, []).append(ref)
    return by_label, by_key, by_author


def resolve_citation(citation, indexes):
    by_label, by_key, by_author = indexes
    refs, missing, year_matches = [], [], []
    for label in citation.get("labels") or []:
        found = by_label.get(normalize_label(label)) or []
        if found:
            refs.extend(found)
        else:
            missing.append(label)
    for key in citation.get("keys") or []:
        found = by_key.get(str(key).lower()) or []
        if found:
            refs.extend(found)
        else:
            missing.append(key)
    for surname, year in citation.get("pairs") or []:
        found = by_author.get((normalize_name(surname), str(year))) or []
        if found:
            refs.extend(found)
            continue
        # Surname exists with another year: a citation year mismatch.
        candidates = [ref for ref in
                      (by_label.get(normalize_label(surname), [])
                       + [r for r in _all_refs(indexes)
                          if normalize_name(r.get("first_author")) ==
                          normalize_name(surname)])
                      if ref.get("year")]
        unique = []
        for ref in candidates:
            if ref not in unique:
                unique.append(ref)
        if unique:
            year_matches.extend(unique)
        else:
            missing.append("%s %s" % (surname, year))
    unique_refs = []
    for ref in refs:
        if ref not in unique_refs:
            unique_refs.append(ref)
    return unique_refs, missing, year_matches


def _all_refs(indexes):
    by_label, by_key, by_author = indexes
    out = []
    for bucket in list(by_label.values()) + list(by_key.values()) + \
            list(by_author.values()):
        for ref in bucket:
            if ref not in out:
                out.append(ref)
    return out


# ---------------------------------------------------------------------------
# notices, statuses and support assessment
# ---------------------------------------------------------------------------

RETRACTED_RE = re.compile(
    r"\bretracted\b|retraction\s+notice\s*[:\uff1a]|"
    r"\u5df2\u64a4\u7a3f|\u64a4\u7a3f(?:\u58f0\u660e|\u901a\u77e5)\s*[:\uff1a]", re.I)


def collect_notices(documents):
    notices = []
    for doc in documents:
        lines = list(_doc_lines(doc))
        version_lines = []
        for line_no, line, locator in lines:
            text = line.strip()
            if not text:
                continue
            if RETRACTED_RE.search(text):
                notices.append({"kind": "retraction", "path": doc["path"],
                                "locator": locator, "text": text})
            if PREPRINT_RE.search(text):
                version_lines.append((line_no, text, locator))
        # A version note usually spans several lines (preprint year + published
        # year + retraction scope), so match the whole note as one record.
        if version_lines and is_notice_document(doc):
            doc_text = " ".join(
                text for _no, text, _loc in lines
                if text.strip() and not text.strip().startswith("#")
                and not DISCLAIMER_RE.search(text))
            notices.append({"kind": "version", "path": doc["path"],
                            "locator": version_lines[0][2],
                            "text": doc_text})
        notices.extend(doc.get("notices") or [])
    return notices


def _doc_lines(doc):
    for item in doc.get("body") or []:
        if len(item) == 3:
            yield item[0], item[1], item[2]
        else:
            line_no, line = item
            locator = ("%s:L%d" % (doc["path"], line_no)
                       if line_no else "%s:extracted" % doc["path"])
            yield line_no, line, locator


def is_notice_document(doc):
    name = doc.get("path", "").lower()
    if re.search(r"(retract|preprint|version[-_]?note|\u64a4\u7a3f|\u9884\u5370)",
                 name):
        return True
    head = "\n".join(str(item[1]) for item in (doc.get("body") or [])[:6])
    return bool(RETRACTION_RE.search(head) or
                (PREPRINT_RE.search(head) and VERSION_RE.search(head)))


def notice_matches_ref(notice_text, ref):
    text = str(notice_text or "")
    if ref.get("doi") and ref["doi"].lower() in text.lower():
        return True
    author = ref.get("first_author")
    year = ref.get("year")
    if author and year and author.lower() in text.lower() and year in text:
        return True
    tokens = significant_tokens(ref.get("title") or "")
    if len(tokens) >= 3:
        hits = sum(1 for token in tokens if token in text.lower())
        if hits >= 3:
            return True
    return False


def assign_source_status(ref, notices):
    matched = [notice for notice in notices
               if notice_matches_ref(notice.get("text", ""), ref)]
    retractions = [n for n in matched if n["kind"] == "retraction"]
    versions = [n for n in matched if n["kind"] == "version"]
    explicit = str(ref.get("explicit_status") or "").lower()
    if explicit in STATUS_VALUES:
        status = explicit
    elif retractions:
        status = "retracted"
    elif versions and (PREPRINT_RE.search(" ".join(n["text"] for n in versions))):
        status = "published"
    elif ref.get("use_ok"):
        status = "published"
    else:
        status = "unknown"
    version_note = " ".join(n["text"] for n in versions).strip()
    if version_note:
        values = numeric_values(version_note)
        if values:
            version_note = "%s\uff08\u6d89\u53ca\u6570\u503c\uff1a%s\uff09" % (
                version_note, ", ".join(_fmt(v) for v in values))
    needs_recheck = (status in ("unknown", "preprint", "retracted")
                     or bool(version_note))
    evidence_item = None
    if retractions:
        first = retractions[0]
        evidence_item = evidence(first["path"], first["locator"],
                                 first["text"])
    elif versions:
        first = versions[0]
        evidence_item = evidence(first["path"], first["locator"],
                                 first["text"])
    else:
        evidence_item = reference_evidence(ref)
    source_name = _source_display(ref)
    return {
        "source": source_name,
        "source_id": ref.get("ref_id", ""),
        "label": ref.get("label", ""),
        "doi": ref.get("doi", ""),
        "status": status,
        "status_basis": _status_basis(status, bool(retractions), bool(versions),
                                      explicit),
        "version_note": version_note,
        "needs_recheck": needs_recheck,
        "verification_limit": (
            "offline: publication and retraction status verified only against "
            "the supplied materials; no online database lookup"),
        "evidence": [evidence_item] if evidence_item else [],
    }


def _status_basis(status, has_retraction, has_version, explicit):
    if explicit:
        return "\u6765\u6e90\u6750\u6599\u7684\u663e\u5f0f\u72b6\u6001\u5b57\u6bb5"
    if has_retraction:
        return "\u8f93\u5165\u6750\u6599\u4e2d\u7684\u64a4\u7a3f\u901a\u77e5"
    if has_version:
        return "\u8f93\u5165\u6750\u6599\u4e2d\u7684\u7248\u672c/\u9884\u5370\u672c\u8bf4\u660e"
    if status == "published":
        return "\u53c2\u8003\u6587\u732e\u6761\u76ee\u542b\u671f\u520a/\u4f53\u91cf/DOI \u4e66\u76ee\u4fe1\u606f"
    return "\u4ec5\u6709\u4e66\u76ee\u4fe1\u606f\u6216\u6761\u76ee\u4e0d\u5b8c\u6574"


def _source_display(ref):
    label = ref.get("label") or "?"
    author = ref.get("first_author") or ref.get("display_author") or ""
    year = ref.get("year") or ""
    if ref.get("placeholder"):
        return "[%s] placeholder/missing entry" % label
    if author and year:
        return "[%s] %s (%s)" % (label, author, year)
    return "[%s] %s" % (label, excerpt(ref.get("raw", ""), 80))


def reference_evidence(ref):
    raw = ref.get("source_text") or ref.get("raw") or ""
    quote = str(raw).split("\n")[0].strip()
    return evidence(ref.get("path", ""), ref.get("locator", ""), quote)


def _fmt(value):
    return ("%d" % value) if float(value).is_integer() else ("%g" % value)


def overclaim_signal(claim_text, source_text):
    claim = str(claim_text or "").lower()
    source = str(source_text or "").lower()
    assertive = any(re.search(r"\b%s\b" % re.escape(word), claim)
                    or word in claim
                    for word in ASSERTIVE_CUES if word.isascii())
    if not assertive:
        assertive = any(word in claim for word in ASSERTIVE_CUES
                        if not word.isascii())
    hedged = any((re.search(r"\b%s\b" % re.escape(word), claim)
                  if word.isascii() else word in claim)
                 for word in HEDGE_CUES)
    limited = any((re.search(r"\b%s\b" % re.escape(word), source)
                   if word.isascii() else word in source)
                  for word in LIMITATION_CUES)
    return bool(assertive and not hedged and limited)


def _quote_result(claim_text, ref):
    quotes = [match.group(1).strip() for match in QUOTE_RE.finditer(claim_text)]
    if not quotes:
        return None
    source_text = ref.get("source_text") if ref else ""
    if not source_text:
        return ("unverifiable", quotes[0])
    for quote in quotes:
        if not quote_present(source_text, quote):
            return ("mismatch", quote)
    return ("ok", quotes[0])


def assess_support(claim_text, ref, source_status, missing_labels,
                   year_matches):
    """Return (verdict, issue_type, subtype, reason, details)."""
    details = {}
    if not ref:
        return ("none", "missing_reference", "unresolved_label",
                "\u6587\u5185\u5f15\u7528\u65e0\u6cd5\u5b9a\u4f4d\u5230\u53c2\u8003"
                "\u6587\u732e\u8868\u6761\u76ee\uff08%s\uff09"
                % ", ".join(missing_labels), details)
    if ref.get("placeholder") or not ref.get("use_ok"):
        return ("none", "missing_reference", "invalid_or_placeholder_entry",
                "\u5339\u914d\u5230\u7684\u6761\u76ee\u4e0d\u662f\u53ef\u56de\u67e5"
                "\u7684\u53c2\u8003\u6587\u732e\uff08\u7f3a\u5e74\u4efd/\u51fa\u5904"
                "\u6216\u4e3a\u201c\u672a\u63d0\u4f9b\u201d\u5360\u4f4d\u8bf4\u660e\uff09",
                details)
    if year_matches:
        details["year_mismatch_refs"] = [
            {"source_id": item.get("ref_id", ""),
             "source_year": item.get("year", "")} for item in year_matches]
        return ("none", "citation_mismatch", "year_mismatch",
                "\u6587\u5185\u5e74\u4efd\u4e0e\u53c2\u8003\u6587\u732e\u6761\u76ee"
                "\u5e74\u4efd\u4e0d\u4e00\u81f4", details)
    status = source_status.get(ref.get("ref_id"), {})
    if status.get("status") == "retracted":
        return ("none", "citation_mismatch", "retracted_source",
                "\u6765\u6e90\u5df2\u88ab\u8f93\u5165\u6750\u6599\u4e2d\u7684"
                "\u64a4\u7a3f\u901a\u77e5\u6807\u8bb0\u4e3a\u64a4\u7a3f\uff0c"
                "\u4e0d\u80fd\u4f5c\u4e3a\u7ed3\u8bba\u4f9d\u636e", details)

    quote_state = _quote_result(claim_text, ref)
    if quote_state and quote_state[0] == "mismatch":
        details["quote"] = quote_state[1]
        return ("none", "quote_mismatch", "quote_not_in_source",
                "\u5f15\u53f7\u5185\u6587\u5b57\u65e0\u6cd5\u5728\u5339\u914d"
                "\u6765\u6e90\u7684\u53ef\u6838\u9a8c\u6587\u672c\u4e2d\u9010"
                "\u5b57\u627e\u5230", details)

    claim_numbers = numeric_values(_without_citation_text(claim_text))
    source_numbers = numeric_values(ref.get("source_text") or ref.get("raw") or "")
    missing_numbers = [value for value in claim_numbers
                       if value not in source_numbers]
    if claim_numbers and source_numbers:
        details["manuscript_values"] = claim_numbers
        details["source_values"] = source_numbers
        if missing_numbers:
            details["missing_numbers"] = missing_numbers
            verdict = "none" if len(missing_numbers) == len(claim_numbers) \
                else "partial"
            return (verdict, "citation_mismatch", "numeric_mismatch",
                    "\u6b63\u6587\u6570\u503c %s \u5728\u6765\u6e90\u6761\u76ee"
                    "\u4e2d\u627e\u4e0d\u5230\uff08\u6765\u6e90\u53ef\u6838"
                    "\u9a8c\u6570\u503c\uff1a%s\uff09"
                    % (", ".join(_fmt(v) for v in missing_numbers),
                       ", ".join(_fmt(v) for v in source_numbers)), details)
    elif claim_numbers and not source_numbers:
        details["manuscript_values"] = claim_numbers
        details["numeric_note"] = "\u6765\u6e90\u53ef\u6838\u9a8c\u6587\u672c"
        details["numeric_note"] += "\u4e2d\u6ca1\u6709\u53ef\u6bd4\u8f83\u7684\u6570\u503c"

    claim_intervention = bool(CLAIM_INTERVENTION_RE.search(
        _without_citation_text(claim_text)))
    source_text = ref.get("source_text") or ""
    source_design = SOURCE_DESIGN_RE.search(source_text + " " + (
        ref.get("raw") or ""))
    if claim_intervention and source_design:
        details["support_scope"] = "design_scope_mismatch"
        details["source_design"] = source_design.group(0)
        return ("none", "citation_mismatch", "design_scope_mismatch",
                "被引文献的设计/类型（%s）不能支持正文的干预或因果断言；"
                "引用存在但支持范围不匹配。" % source_design.group(0),
                details)

    if source_text and overclaim_signal(claim_text, source_text):
        details["support_scope"] = "source_limitation_vs_claim_certainty"
        verdict = "none" if ref.get("is_opinion") else "partial"
        return (verdict, "citation_mismatch", "overclaim_or_negation",
                "\u6765\u6e90\u4ec5\u7ed9\u51fa\u5173\u8054/\u5c40\u9650\u6027"
                "\u8868\u8ff0\uff0c\u6b63\u6587\u4f7f\u7528\u56e0\u679c\u6216"
                "\u7edd\u5bf9\u5316\u63aa\u8f9e\uff0c\u652f\u6301\u8303\u56f4"
                "\u4e0d\u5339\u914d", details)

    if not ref.get("source_text"):
        return ("unverifiable", None, "bibliography_only",
                "\u6761\u76ee\u4ec5\u6709\u4e66\u76ee\u4fe1\u606f\uff0c\u65e0"
                "\u6cd5\u5728\u79bb\u7ebf\u6761\u4ef6\u4e0b\u5224\u5b9a"
                "\u8bed\u4e49\u652f\u6301\uff1b\u9700\u8981\u4eba\u5de5"
                "\u56de\u67e5\u539f\u6587", details)
    return ("full", None, "script_consistent",
            "\u811a\u672c\u672a\u53d1\u73b0\u6570\u503c\u6216\u8bed\u4e49"
            "\u77db\u76fe\u4fe1\u53f7\uff1b\u4ecd\u9700\u4eba\u5de5\u786e"
            "\u8ba4\u8bed\u4e49\u652f\u6301", details)


def _without_citation_text(claim_text):
    cites = extract_citations(claim_text)
    return strip_citations(claim_text, cites)


# ---------------------------------------------------------------------------
# main analysis
# ---------------------------------------------------------------------------

def dedupe_references(documents):
    refs, by_key = [], {}
    for doc in documents:
        for ref in doc.get("refs") or []:
            key = ""
            if ref.get("doi"):
                key = "doi:" + ref["doi"].lower()
            elif ref.get("title"):
                key = "title:%s|%s" % (norm_match(ref["title"]),
                                       ref.get("year", ""))
            if key and key in by_key:
                existing = by_key[key]
                for alias in ref.get("aliases") or []:
                    if alias and alias not in existing["aliases"]:
                        existing["aliases"].append(alias)
                if not existing.get("source_text") and ref.get("source_text"):
                    existing["source_text"] = ref["source_text"]
                continue
            ref["ref_id"] = "R%03d" % (len(refs) + 1)
            refs.append(ref)
            if key:
                by_key[key] = ref
    return refs


ENUMERATOR_RE = re.compile(r"^(?:[A-Za-z]?\d{1,3})[.)]$")


def collect_claims(documents):
    """Yield (doc, sentence, locator, citations) for claim-scan documents."""
    for doc in documents:
        if is_notice_document(doc):
            continue
        doc_citations = 0
        rows = []
        for _line_no, line, base_locator in doc.get("body") or []:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if DOC_HEADING_RE.match(stripped) and len(stripped) < 140:
                continue
            sentences = split_sentences(stripped)
            # A line such as "P1. Chronic stress ..." splits into "P1." and the
            # claim.  Drop the bare enumerator so the locator stays line-based.
            if len(sentences) > 1 and ENUMERATOR_RE.match(sentences[0][0]):
                sentences = sentences[1:]
            for index, (text, _, _) in enumerate(sentences, 1):
                citations = extract_citations(text)
                if len(sentences) > 1:
                    locator = "%s#S%d" % (base_locator, index)
                else:
                    locator = base_locator
                rows.append((text, locator, citations))
                doc_citations += len(citations)
        manuscript_like = bool(re.search(
            r"(manuscript|paper|article|draft|main[-_]?text|\u8bba\u6587|"
            r"\u6b63\u6587)", doc.get("path", ""), re.I))
        if not (doc_citations or manuscript_like):
            continue
        for row in rows:
            yield doc, row[0], row[1], row[2]


DOC_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+\S|\d{1,2}(?:\.\d{1,2})*[.)]?\s+"
    r"(?:abstract|introduction|methods?|results?|discussion|conclusions?|"
    r"references?|background|related\s+work|\u6458\u8981|\u5f15\u8a00|"
    r"\u65b9\u6cd5|\u7ed3\u679c|\u8ba8\u8bba|\u7ed3\u8bba|\u53c2\u8003"
    r"\u6587\u732e)\s*$)", re.I)


def analyze(input_dir, work_dir, output_dir, only_material=None):
    case = load_json(Path(input_dir) / "case.json", {})
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    documents = load_documents(input_dir, work_dir, only_material)
    readable = [doc for doc in documents
                if str(doc.get("text") or "").strip()]
    refs = dedupe_references(documents)
    notices = collect_notices(documents)
    indexes = build_indexes(refs)
    status_rows = []
    status_by_id = {}
    for ref in refs:
        row = assign_source_status(ref, notices)
        status_rows.append(row)
        status_by_id[ref["ref_id"]] = row

    matrix, findings, uncited, all_evidence = [], [], [], []
    claim_counter = 0
    finding_counter = 0

    for doc, sentence, locator, citations in collect_claims(documents):
        claim_path = doc["path"]
        claim_section = section_for_locator(doc, locator)
        if citations:
            claim_counter += 1
            claim_id = "C%03d" % claim_counter
        for citation in citations:
            refs_found, missing, year_matches = resolve_citation(citation, indexes)
            refs_found = list(refs_found)
            if not refs_found and year_matches:
                # Only the surname matched: report the year mismatch instead
                # of silently treating the citation as unresolvable.
                refs_found = list(year_matches)
            if missing:
                refs_found.append(None)
            if not refs_found:
                refs_found = [None]
            for ref in refs_found:
                row_status = status_by_id.get(ref["ref_id"], {}) if ref else {}
                row_year_matches = [item for item in year_matches
                                    if item is ref]
                verdict, issue_type, subtype, reason, details = assess_support(
                    sentence, ref, status_by_id, missing, row_year_matches)
                claim_evidence = evidence(claim_path, locator, sentence)
                row_evidence = [claim_evidence]
                source_display = "MISSING %s" % citation["raw"]
                source_id = ""
                source_locator = ""
                source_statement = ""
                source_design = "unknown"
                if ref:
                    source_display = _source_display(ref)
                    source_id = ref["ref_id"]
                    source_locator = ref.get("locator", "")
                    source_statement = excerpt(
                        ref.get("source_text") or ref.get("raw") or "")
                    row_evidence.append(reference_evidence(ref))
                    if ref.get("source_text"):
                        source_design = ("opinion/commentary"
                                         if ref.get("is_opinion")
                                         else "source_excerpt")
                    elif ref.get("use_ok"):
                        source_design = "bibliography_only"
                    if row_status.get("status") == "retracted":
                        source_design = "retracted"
                    elif row_status.get("status") == "preprint":
                        source_design = "preprint"
                row_evidence = dedupe_evidence(row_evidence)
                all_evidence.extend(row_evidence)
                row = {
                    "claim_id": claim_id,
                    "claim": sentence,
                    "claim_locator": locator,
                    "claim_path": claim_path,
                    "claim_section": claim_section,
                    "citation": citation["raw"],
                    "citation_kind": citation["kind"],
                    "source": source_display,
                    "source_id": source_id,
                    "source_label": ref.get("label", "") if ref else "",
                    "source_locator": source_locator,
                    "source_statement": source_statement,
                    "source_design": source_design,
                    "manuscript_value": ", ".join(
                        _fmt(v) for v in details.get("manuscript_values") or []),
                    "source_value": ", ".join(
                        _fmt(v) for v in details.get("source_values") or []),
                    "verdict": verdict,
                    "type": issue_type or "",
                    "subtype": subtype,
                    "reason": reason,
                    "needs_review": True,
                    "evidence": row_evidence,
                }
                matrix.append(row)
                if issue_type:
                    finding_counter += 1
                    finding = make_finding(finding_counter, row, details)
                    findings.append(finding)
                    all_evidence.extend(finding["evidence"])

    # Uncited claim scan: only documents with citations or manuscript-like names.
    for doc, sentence, locator, citations in collect_claims(documents):
        if citations:
            continue
        if DISCLAIMER_RE.search(sentence):
            continue
        section = section_for_locator(doc, locator)
        if section in ("methods", "results"):
            # Own results/methods are supported by the paper's data, not by a
            # citation; do not report them as uncited external claims.
            continue
        if OWN_RESULT_RE.search(sentence) or re.search(
                r"\b(?:we\s+(?:investigated|examined|tested|hypothesi[sz]ed)|"
                r"this\s+study\s+(?:aims?|investigates?)|研究目的|本研究)\b|"
                r"hypothes|假设|是否", sentence, re.I):
            continue
        if not (STAT_RE.search(sentence)
                or EXTERNAL_OR_GENERAL_RE.search(sentence)):
            # A bare interpretation/inference is not an uncited external fact.
            continue
        if len(norm_space(sentence)) < 24:
            continue
        finding_counter += 1
        claim_evidence = evidence(doc["path"], locator, sentence)
        severity = "major" if STAT_RE.search(sentence) else "minor"
        finding = {
            "id": "F%03d" % finding_counter,
            "type": "uncited_claim",
            "subtype": "no_inline_citation",
            "facet": "citation_support",
            "severity": severity,
            "status": "candidate",
            "needs_review": True,
            "location": {"path": doc["path"], "locator": locator,
                         "section": section_for_locator(doc, locator)},
            "claim_section": section_for_locator(doc, locator),
            "check_item": "\u6bcf\u6761\u5173\u952e\u65ad\u8a00\u53ef\u56de\u67e5",
            "original_quote": sentence,
            "issue": ("\u8be5\u53e5\u5305\u542b\u53ef\u6838\u9a8c\u7684"
                      "\u6570\u503c\u6216\u7ed3\u679c\u6027\u8868\u8ff0\uff0c"
                      "\u4f46\u672a\u7ed9\u51fa\u4efb\u4f55\u6587\u5185"
                      "\u5f15\u7528\u3002"),
            "fix": ("\u8865\u5145\u53ef\u56de\u67e5\u7684\u6765\u6e90\u5e76"
                    "\u5728\u53e5\u672b\u6ce8\u660e\u5f15\u7528\uff1b\u82e5"
                    "\u4e0d\u80fd\u786e\u8ba4\u6765\u6e90\uff0c\u6539\u5199"
                    "\u4e3a\u9650\u5b9a\u6027\u8868\u8ff0\u6216\u5217\u5165"
                    "\u5f85\u590d\u6838\u3002"),
            "evidence": [claim_evidence],
            "confidence": 0.55,
            "false_positive_check": (
                "\u786e\u8ba4\u8be5\u53e5\u662f\u5426\u4ee5\u524d\u6587"
                "\u5f15\u7528\u4e3a\u4f9d\u636e\u3001\u662f\u5426\u4e3a"
                "\u65b9\u6cd5/\u9650\u5236\u8bf4\u660e\uff1b\u82e5\u662f"
                "\u5219\u4ece findings \u4e2d\u79fb\u9664\u3002"),
        }
        uncited.append(finding)
        findings.append(finding)
        all_evidence.extend(finding["evidence"])

    all_evidence = dedupe_evidence(all_evidence)
    checks = build_checks(documents, refs, status_rows, matrix, findings,
                          uncited, readable, notices)
    facets_checked, citation_facets = build_facets(
        documents, refs, status_rows, matrix, findings, uncited, notices,
        readable)
    fulltext = build_fulltext(documents, matrix)
    uncertainties = build_uncertainties(documents, refs, matrix, notices,
                                        readable)
    status = decide_status(readable, refs, matrix, findings)
    review_status = decide_review_status(status, findings, matrix)
    citations_count = sum(1 for _doc, _s, _l, cites in
                          collect_claims(documents) for _ in cites)
    payload = {
        "case_id": case_id,
        "mode": "citation-fact-check",
        "script": "verify_citations.py",
        "version": VERSION,
        "generated_at": now_iso(),
        "status": status,
        "review_status": review_status,
        "summary": summarize(status, refs, matrix, findings, uncited,
                             documents, notices),
        "inputs": [{
            "path": doc["path"],
            "kind": doc["kind"],
            "chars": len(str(doc.get("text") or "")),
            "body_lines": len(doc.get("body") or []),
            "reference_entries": len(doc.get("refs") or []),
            "warnings": doc.get("warnings") or [],
        } for doc in documents],
        "inventory": {
            "documents": len(documents),
            "readable_documents": len(readable),
            "reference_entries": len(refs),
            "citations": citations_count,
            "claim_source_rows": len(matrix),
            "findings": len(findings),
            "uncited_claims": len(uncited),
            "notices": len(notices),
        },
        "references": [reference_summary(ref) for ref in refs],
        "source_status": status_rows,
        "claim_source_matrix": matrix,
        "findings": findings,
        "uncited_claims": uncited,
        "facets_checked": facets_checked,
        "citation_facets": citation_facets,
        "fulltext": fulltext,
        "evidence": all_evidence,
        "uncertainties": uncertainties,
        "checks": checks,
        "review_protocol": review_protocol(),
        "warnings": [warning for doc in documents
                     for warning in (doc.get("warnings") or [])],
    }
    return payload


def _without_ptr(text):
    return text


def make_finding(counter, row, details):
    subtype = row.get("subtype") or ""
    severity = "major"
    confidence = 0.75
    if subtype == "retracted_source":
        severity = "critical"
        confidence = 0.95
    elif subtype in ("unresolved_label", "invalid_or_placeholder_entry"):
        severity = "major"
        confidence = 0.9
    elif subtype == "numeric_mismatch":
        severity = "major"
        confidence = 0.8
    elif subtype == "overclaim_or_negation":
        severity = "major"
        confidence = 0.65
    elif subtype == "quote_not_in_source":
        severity = "major"
        confidence = 0.7
    elif subtype == "year_mismatch":
        severity = "major"
        confidence = 0.85
    issue_text = row.get("reason", "")
    if details.get("missing_numbers"):
        issue_text += "\uff1b\u7f3a\u5931\u6570\u503c\uff1a%s" % ", ".join(
            _fmt(v) for v in details["missing_numbers"])
    fix = fix_for_subtype(subtype)
    return {
        "id": "F%03d" % counter,
        "type": row.get("type") or "citation_mismatch",
        "subtype": subtype,
        "facet": FACET_BY_SUBTYPE.get(subtype, "citation_support"),
        "severity": severity,
        "status": "candidate",
        "needs_review": True,
        "location": {"path": row.get("claim_path", ""),
                     "locator": row.get("claim_locator", ""),
                     "section": row.get("claim_section", "")},
        "check_item": check_item_for_type(row.get("type")),
        "original_quote": row.get("claim", ""),
        "issue": issue_text,
        "fix": fix,
        "claim_id": row.get("claim_id", ""),
        "claim_section": row.get("claim_section", ""),
        "source_id": row.get("source_id", ""),
        "source": row.get("source", ""),
        "evidence": row.get("evidence") or [],
        "confidence": confidence,
        "false_positive_check": (
            "\u56de\u5230\u6765\u6e90\u539f\u6587\u786e\u8ba4\u8be5\u6761"
            "\u7ed3\u8bba\uff0c\u5e76\u68c0\u67e5\u811a\u672c\u662f\u5426"
            "\u5ffd\u7565\u4e86\u6765\u6e90\u5176\u4ed6\u6bb5\u843d\u7684"
            "\u652f\u6301\u8bc1\u636e\uff1b\u82e5\u4e0d\u6210\u7acb\uff0c"
            "\u8bb0\u5165 rejected_candidates\u3002"),
    }


def check_item_for_type(issue_type):
    return {
        "missing_reference": "\u6bcf\u6761\u5f15\u7528\u53ef\u56de\u67e5",
        "uncited_claim": "\u6bcf\u6761\u5173\u952e\u65ad\u8a00\u53ef\u56de\u67e5",
        "citation_mismatch": "\u65ad\u8a00\u4e0e\u6765\u6e90\u7ed3\u8bba\u5339\u914d",
        "quote_mismatch": "\u5f15\u6587\u9010\u5b57\u53ef\u6838\u9a8c",
    }.get(issue_type, "\u5f15\u7528\u4e0e\u4e8b\u5b9e\u6838\u9a8c")


def fix_for_subtype(subtype):
    mapping = {
        "unresolved_label": (
            "\u8865\u5145\u8be5\u5f15\u7528\u5bf9\u5e94\u7684\u53c2\u8003"
            "\u6587\u732e\u6761\u76ee\uff0c\u6216\u5220\u9664\u65e0\u6cd5"
            "\u56de\u67e5\u7684\u5f15\u7528\u3002"),
        "invalid_or_placeholder_entry": (
            "\u628a\u5360\u4f4d\u8bf4\u660e\u66ff\u6362\u4e3a\u5b8c\u6574"
            "\u7684\u4e66\u76ee\u4fe1\u606f\uff1b\u82e5\u6765\u6e90\u4e0d"
            "\u5b58\u5728\uff0c\u5220\u9664\u8be5\u5f15\u7528\u5e76\u91cd"
            "\u5199\u7ed3\u8bba\u3002"),
        "retracted_source": (
            "\u66f4\u6362\u4e3a\u672a\u64a4\u7a3f\u7684\u540c\u7c7b"
            "\u8bc1\u636e\uff0c\u6216\u5728\u6b63\u6587\u4e2d\u660e\u786e"
            "\u6807\u6ce8\u8be5\u6765\u6e90\u5df2\u64a4\u7a3f\u53ca"
            "\u5176\u5f71\u54cd\u3002"),
        "numeric_mismatch": (
            "\u6838\u5bf9\u539f\u59cb\u6570\u636e\u540e\u66f4\u6b63"
            "\u6b63\u6587\u4e2d\u7684\u6570\u503c\u3001\u6837\u672c"
            "\u91cf\u6216\u767e\u5206\u6bd4\uff0c\u5e76\u6807\u6ce8"
            "\u6765\u6e90\u5bf9\u5e94\u6570\u503c\u3002"),
        "overclaim_or_negation": (
            "\u5c06\u56e0\u679c/\u7edd\u5bf9\u5316\u63aa\u8f9e\u964d"
            "\u7ea7\u4e3a\u6765\u6e90\u652f\u6301\u7684\u5173\u8054"
            "\u6216\u9650\u5b9a\u6027\u8868\u8ff0\uff0c\u6216\u8865"
            "\u5145\u80fd\u652f\u6301\u56e0\u679c\u7ed3\u8bba\u7684"
            "\u8bc1\u636e\u3002"),
        "quote_not_in_source": (
            "\u56de\u5230\u6765\u6e90\u9010\u5b57\u91cd\u6458\u5f15"
            "\u6587\uff1b\u82e5\u4e0d\u80fd\u9010\u5b57\u6838\u5b9e"
            "\uff0c\u5220\u9664\u5f15\u53f7\u6216\u6807\u6ce8\u4e3a"
            "\u8f6c\u8ff0\u3002"),
        "year_mismatch": (
            "\u6838\u5bf9\u5f15\u7528\u5e74\u4efd\u4e0e\u53c2\u8003"
            "\u6587\u732e\u5e74\u4efd\uff0c\u66f4\u6b63\u5e74\u4efd"
            "\u6216\u66f4\u6362\u5230\u6b63\u786e\u7684\u7248\u672c"
            "\u3002"),
    }
    return mapping.get(subtype, "\u56de\u5230\u539f\u6587\u6838\u5b9e"
                               "\u540e\u4fee\u6b63\u5f15\u7528\u3002")


def build_fulltext(documents, matrix):
    """Section/paragraph coverage for the actual full-text materials."""
    docs = []
    section_names = []
    claims_by_section, citations_by_section = {}, {}
    for doc in documents:
        sections = doc.get("sections") or []
        paragraphs = doc.get("paragraphs") or []
        sentences = 0
        for _line_no, line, _locator in doc.get("body") or []:
            sentences += len(split_sentences(line))
        for section in sections:
            name = section.get("name")
            if name and name not in section_names:
                section_names.append(name)
        docs.append({
            "path": doc.get("path"),
            "kind": doc.get("kind"),
            "chars": len(str(doc.get("text") or "")),
            "sections": [s.get("name") for s in sections],
            "section_ranges": sections,
            "paragraphs": len(paragraphs),
            "sentences": sentences,
            "citation_claims": sum(1 for row in matrix
                                   if row.get("claim_path") == doc.get("path")),
        })
    for row in matrix:
        section = row.get("claim_section") or "body"
        claims_by_section[section] = claims_by_section.get(section, 0) + 1
        citations_by_section[section] = citations_by_section.get(section, 0) \
            + (1 if row.get("citation") else 0)
    return {
        "documents": docs,
        "sections": section_names,
        "paragraphs": sum(item["paragraphs"] for item in docs),
        "sentences": sum(item["sentences"] for item in docs),
        "claims_by_section": claims_by_section,
        "citations_by_section": citations_by_section,
    }


def _locators(rows, key):
    out = []
    for row in rows or []:
        value = row.get(key)
        if isinstance(value, dict):
            value = value.get("locator")
        if value and value not in out:
            out.append(str(value))
    return out[:8]


def build_facets(documents, refs, status_rows, matrix, findings, uncited,
                 notices, readable):
    """Official six review facets plus concrete citation subchecks."""
    section_names = []
    for doc in documents:
        for section in doc.get("sections") or []:
            if section.get("name") and section["name"] not in section_names:
                section_names.append(section["name"])
    subtypes = {row.get("subtype") for row in findings}
    types = {row.get("type") for row in findings}
    numeric_claims = [row for row in matrix
                      if row.get("manuscript_value") or row.get("source_value")]
    quoted_claims = [row for row in matrix
                     if QUOTE_RE.search(str(row.get("claim") or ""))]
    unknown = [row for row in status_rows if row.get("status") == "unknown"]
    retracted = [row for row in status_rows if row.get("status") == "retracted"]
    matrix_locators = _locators([{"locator": row.get("claim_locator")}
                                 for row in matrix], "locator")
    source_locators = _locators([{"locator": row.get("source_locator")}
                                 for row in matrix], "locator")
    status_locators = []
    for row in status_rows:
        for ev in row.get("evidence") or []:
            if ev.get("locator"):
                status_locators.append(ev["locator"])
    status_locators = list(dict.fromkeys(status_locators))[:8]

    def row(facet, status, items, evidence, reason=None):
        out = {"facet": facet, "status": status,
               "items": [item for item in items if item],
               "evidence": evidence[:8]}
        if status in ("partial", "not_checked", "not_applicable"):
            out["reason"] = reason or "材料不足"
        return out

    structure = set(section_names)
    rows = [
        row("fact_consistency",
            "fail" if "numeric_mismatch" in subtypes else
            ("checked" if numeric_claims else "partial"),
            ["数字、百分比、样本量", "摘要/结果/来源数值",
             "数值单位与方向"],
            matrix_locators + source_locators,
            None if numeric_claims else "没有可比较的数值型 claim"),
        row("citation_support",
            "fail" if types & {"missing_reference", "citation_mismatch",
                               "quote_mismatch"} else
            ("checked" if refs and matrix else "not_checked"),
            ["引用可回查", "论断-来源语义匹配", "引用设计/范围",
             "引文逐字", "未引用断言", "来源状态"],
            matrix_locators + source_locators + status_locators,
            None if refs and matrix else "没有参考文献或引用矩阵"),
        row("method_design",
            "fail" if "design_scope_mismatch" in subtypes else
            ("checked" if any(row.get("source_design") in
                              ("source_excerpt", "opinion/commentary",
                               "preprint", "retracted")
                              for row in matrix) else "partial"),
            ["被引研究设计", "干预/观察性范围", "综述/评论/原始研究"],
            source_locators,
            None if matrix else "没有来源可核验文本"),
        row("statistical_reporting",
            "fail" if "numeric_mismatch" in subtypes else
            ("checked" if numeric_claims else "partial"),
            ["数值一致性", "样本量", "显著性表述"],
            matrix_locators + source_locators,
            None if numeric_claims else "没有可比较的统计数值"),
        row("academic_compliance",
            "fail" if retracted and any(
                row.get("type") == "citation_mismatch"
                and row.get("subtype") == "retracted_source" for row in findings)
            else ("checked" if refs else "not_checked"),
            ["撤稿/更正", "预印本/版本差异", "来源状态可核验"],
            status_locators,
            None if refs else "没有参考文献条目"),
        row("argument_chain",
            "checked" if uncited or matrix else "not_checked",
            ["未引用结果断言", "引用存在≠支持结论", "过度推断/否定"],
            _locators(findings, "location") + matrix_locators,
            None if uncited or matrix else "没有可核对的断言"),
    ]
    detailed = [
        {"facet": "citation_resolvable",
         "status": "fail" if "missing_reference" in types else
                   ("pass" if matrix else "not_checked"),
         "detail": "%d 条引用无法回查" % sum(
             1 for row in findings if row.get("type") == "missing_reference"),
         "evidence": matrix_locators},
        {"facet": "quote_verbatim",
         "status": "fail" if "quote_mismatch" in types else
                   ("pass" if quoted_claims else "not_checked"),
         "detail": "%d 条带引号 claim，%d 条引文逐字不匹配" % (
             len(quoted_claims),
             sum(1 for row in findings if row.get("type") == "quote_mismatch")),
         "evidence": matrix_locators},
        {"facet": "numeric_consistency",
         "status": "fail" if "numeric_mismatch" in subtypes else
                   ("pass" if numeric_claims else "not_checked"),
         "detail": "%d 条数值型 claim，%d 条数值不匹配" % (
             len(numeric_claims),
             sum(1 for row in findings
                 if row.get("subtype") == "numeric_mismatch")),
         "evidence": matrix_locators + source_locators},
        {"facet": "source_status",
         "status": "fail" if retracted else
                   ("partial" if unknown else
                    ("pass" if refs else "not_checked")),
         "detail": "%d 条来源；unknown %d；retracted %d；通知 %d" % (
             len(status_rows), len(unknown), len(retracted), len(notices)),
         "evidence": status_locators},
        {"facet": "design_scope_match",
         "status": "fail" if "design_scope_mismatch" in subtypes else
                   ("pass" if matrix else "not_checked"),
         "detail": "%d 条设计/范围不匹配" % sum(
             1 for row in findings
             if row.get("subtype") == "design_scope_mismatch"),
         "evidence": source_locators},
        {"facet": "uncited_results",
         "status": "fail" if uncited else
                   ("pass" if matrix else "not_checked"),
         "detail": "%d 条未引用结果性断言" % len(uncited),
         "evidence": _locators(uncited, "location")},
        {"facet": "reference_metadata",
         "status": "partial" if any(not ref.get("use_ok") for ref in refs) else
                   ("pass" if refs else "not_checked"),
         "detail": "%d/%d 条参考文献含可回查年份与出处/DOI" % (
             sum(1 for ref in refs if ref.get("use_ok")), len(refs)),
         "evidence": [ref.get("locator") for ref in refs[:8]]},
        {"facet": "section_coverage",
         "status": "checked" if {"abstract", "methods", "results"} <= structure
         else ("partial" if structure else "not_checked"),
         "detail": "识别章节：%s" % (", ".join(section_names) or "none"),
         "evidence": [section.get("line_start") for section in
                      (documents[0].get("sections") or [])[:8]]
         if documents else [],
         "reason": "" if {"abstract", "methods", "results"} <= structure
         else "缺少摘要/方法/结果段，全文覆盖只能部分完成"},
    ]
    return rows, detailed


def build_checks(documents, refs, status_rows, matrix, findings, uncited,
                 readable, notices):
    missing_ref = [row for row in findings
                   if row.get("type") == "missing_reference"]
    mismatch = [row for row in findings
                if row.get("type") == "citation_mismatch"]
    quote_issues = [row for row in findings
                    if row.get("type") == "quote_mismatch"]
    checks = []
    checks.append({
        "item": "\u6750\u6599\u53ef\u8bfb",
        "status": "pass" if readable else "fail",
        "detail": "\u8bfb\u53d6\u4e86 %d/%d \u4e2a\u6750\u6599\u6587\u4ef6"
                  % (len(readable), len(documents)),
    })
    checks.append({
        "item": "\u53c2\u8003\u6587\u732e\u8868\u5b58\u5728",
        "status": "pass" if refs else "fail",
        "detail": "\u63d0\u53d6\u5230 %d \u4e2a\u53c2\u8003\u6587\u732e"
                  "\u6761\u76ee" % len(refs),
    })
    checks.append({
        "item": "\u6bcf\u6761\u5f15\u7528\u53ef\u56de\u67e5",
        "status": "fail" if missing_ref else ("pass" if matrix else
                                              "not_checked"),
        "detail": "\u53d1\u73b0 %d \u6761\u65e0\u6cd5\u56de\u67e5\u7684"
                  "\u5f15\u7528" % len(missing_ref),
    })
    checks.append({
        "item": "\u65ad\u8a00\u4e0e\u6765\u6e90\u7ed3\u8bba\u5339\u914d",
        "status": "fail" if mismatch or quote_issues else
                  ("not_checked" if matrix else "not_checked"),
        "detail": ("\u811a\u672c\u7ed9\u51fa %d \u6761\u8bed\u4e49/"
                   "\u6570\u503c\u5019\u9009\u95ee\u9898\uff1b\u8bed\u4e49"
                   "\u652f\u6301\u4ecd\u9700\u4eba\u5de5\u590d\u6838"
                   % (len(mismatch) + len(quote_issues))),
    })
    unknown_sources = [row for row in status_rows
                       if row.get("status") == "unknown"]
    checks.append({
        "item": "\u6765\u6e90\u72b6\u6001\u4e0e\u64a4\u7a3f\u6838\u9a8c",
        "status": ("not_checked" if unknown_sources else
                   ("pass" if refs else "not_checked")),
        "detail": ("\u57fa\u4e8e\u8f93\u5165\u6750\u6599\u68c0\u67e5\u4e86 "
                   "%d \u6761\u64a4\u7a3f/\u7248\u672c\u8bf4\u660e\uff1b"
                   "%d \u6761\u6765\u6e90\u72b6\u6001\u4ecd\u4e3a unknown"
                   % (len(notices), len(unknown_sources))),
    })
    checks.append({
        "item": "\u6570\u503c\u4e00\u81f4\u6027",
        "status": "fail" if any(row.get("subtype") == "numeric_mismatch"
                                for row in findings) else
                  ("pass" if matrix else "not_checked"),
        "detail": "\u5bf9\u6bd4\u6b63\u6587\u4e0e\u6765\u6e90\u53ef\u6838"
                  "\u9a8c\u6587\u672c\u4e2d\u7684\u6570\u503c",
    })
    checks.append({
        "item": "\u672a\u5f15\u7528\u65ad\u8a00\u626b\u63cf",
        "status": "fail" if uncited else ("pass" if matrix else "not_checked"),
        "detail": "\u53d1\u73b0 %d \u6761\u7f3a\u5c11\u6587\u5185\u5f15"
                  "\u7528\u7684\u5019\u9009\u65ad\u8a00" % len(uncited),
    })
    section_names = []
    for doc in documents:
        for section in doc.get("sections") or []:
            if section.get("name") and section["name"] not in section_names:
                section_names.append(section["name"])
    checks.append({
        "item": "\u5168\u6587\u5206\u6bb5",
        "status": "pass" if section_names else "not_checked",
        "detail": "\u8bc6\u522b\u7ae0\u8282\uff1a%s" % (
            ", ".join(section_names) or "none"),
    })
    checks.append({
        "item": "\u5f15\u7528\u5b50\u9762\u8986\u76d6",
        "status": "pass" if matrix else "not_checked",
        "detail": "\u5f15\u7528\u53ef\u56de\u67e5/\u5f15\u6587\u9010\u5b57/"
                  "\u6570\u503c\u4e00\u81f4/\u6765\u6e90\u72b6\u6001/"
                  "\u8bbe\u8ba1\u8303\u56f4/\u672a\u5f15\u7528\u65ad\u8a00/"
                  "\u5143\u6570\u636e\u9010\u9879\u8bb0\u5f55",
    })
    return checks


def build_uncertainties(documents, refs, matrix, notices, readable):
    uncertainties = []
    if not readable:
        uncertainties.append({
            "item": "materials",
            "reason": "\u6ca1\u6709\u53ef\u8bfb\u6750\u6599",
            "impact": "\u65e0\u6cd5\u62bd\u53d6\u5f15\u7528\u6216\u53c2\u8003"
                      "\u6587\u732e",
            "action": "\u68c0\u67e5 /input \u662f\u5426\u5305\u542b md/txt/json/csv \u6216\u5148\u8fd0\u884c prepare",
        })
    if readable and not refs:
        uncertainties.append({
            "item": "reference_list",
            "reason": "\u672a\u5728\u8f93\u5165\u4e2d\u627e\u5230\u53c2\u8003"
                      "\u6587\u732e\u8868\u6216 .bib/.ris \u6761\u76ee",
            "impact": "\u6240\u6709\u6587\u5185\u5f15\u7528\u65e0\u6cd5\u56de"
                      "\u67e5\uff0c\u8bed\u4e49\u652f\u6301\u65e0\u6cd5\u5224\u5b9a",
            "action": "\u786e\u8ba4\u662f\u5426\u4e3a\u4ec5\u6458\u8981\u3001"
                      "\u626b\u63cf\u4ef6\u6216\u53c2\u8003\u6587\u732e\u8868"
                      "\u7f3a\u5931\uff1b\u5728\u62a5\u544a\u4e2d\u6807\u6ce8",
        })
    bibliography_only = [ref for ref in refs if ref.get("use_ok")
                         and not ref.get("source_text")]
    if bibliography_only:
        uncertainties.append({
            "item": "semantic_support",
            "reason": "%d \u4e2a\u6761\u76ee\u4ec5\u6709\u4e66\u76ee\u4fe1\u606f"
                      "\uff0c\u6ca1\u6709\u53ef\u6bd4\u5bf9\u7684\u6458\u8981"
                      "\u6216\u6765\u6e90\u6458\u5f55" % len(bibliography_only),
            "impact": "\u811a\u672c\u53ea\u80fd\u7ed9\u51fa unverifiable\uff0c"
                      "\u4e0d\u80fd\u5224\u5b9a\u8bed\u4e49\u652f\u6301",
            "action": "\u4eba\u5de5\u56de\u67e5\u539f\u6587\uff0c\u6216\u8865"
                      "\u5145\u53ef\u5408\u6cd5\u4f7f\u7528\u7684\u6458\u8981/"
                      "\u8bc1\u636e\u7247\u6bb5",
        })
    if any(row.get("verdict") == "unverifiable" for row in matrix):
        uncertainties.append({
            "item": "verdict_pending",
            "reason": "\u5b58\u5728\u6807\u8bb0\u4e3a unverifiable \u7684"
                      "\u5f15\u7528\u884c",
            "impact": "\u811a\u672c\u672a\u505a\u8bed\u4e49\u7ed3\u8bba",
            "action": "\u9010\u6761\u4eba\u5de5\u5224\u5b9a full/partial/none/"
                      "unverifiable\uff0c\u5e76\u66f4\u65b0\u77e9\u9635",
        })
    if notices:
        uncertainties.append({
            "item": "retraction_check",
            "reason": "\u64a4\u7a3f/\u7248\u672c\u72b6\u6001\u4ec5\u6839\u636e"
                      "\u8f93\u5165\u6750\u6599\u4e2d\u7684\u901a\u77e5\u5224\u5b9a",
            "impact": "\u672a\u63d0\u4f9b\u7684\u64a4\u7a3f\u6216\u7248\u672c"
                      "\u5dee\u5f02\u53ef\u80fd\u672a\u88ab\u53d1\u73b0",
            "action": "\u82e5\u9700\u7ebf\u4e0a\u6838\u9a8c\uff0c\u7531\u4eba"
                      "\u5de5\u5728\u6388\u6743\u73af\u5883\u8865\u67e5",
        })
    skipped = [doc["path"] for doc in documents if doc.get("warnings")]
    if skipped:
        uncertainties.append({
            "item": "unparsed_materials",
            "reason": "\u90e8\u5206\u6750\u6599\u672a\u80fd\u76f4\u63a5\u89e3"
                      "\u6790\uff1a%s" % ", ".join(skipped),
            "impact": "\u8fd9\u4e9b\u6750\u6599\u4e2d\u7684\u5f15\u7528\u53ef"
                      "\u80fd\u672a\u8ba1\u5165",
            "action": "\u5148\u8fd0\u884c run_case.py prepare \u4ee5\u590d\u7528"
                      "\u5df2\u63d0\u53d6\u6587\u672c\uff0c\u6216\u8f6c\u6362"
                      "\u4e3a\u53ef\u8bfb\u6587\u672c",
        })
    uncertainties.append({
        "item": "offline_scope",
        "reason": "\u811a\u672c\u4e0d\u8054\u7f51\uff0c\u4e0d\u67e5\u8be2"
                  "\u5916\u90e8\u6570\u636e\u5e93",
        "impact": "\u53c2\u8003\u6587\u732e\u7684\u771f\u5b9e\u6027\u3001"
                  "\u64a4\u7a3f\u72b6\u6001\u4e0e\u6700\u65b0\u7248\u672c"
                  "\u4ec5\u80fd\u5728\u8f93\u5165\u6750\u6599\u8303\u56f4"
                  "\u5185\u6838\u9a8c",
        "action": "\u5728\u62a5\u544a\u4e2d\u58f0\u660e\u8be5\u5c40\u9650\uff0c"
                  "\u4e0d\u8981\u7f16\u9020 DOI \u6216\u7ebf\u4e0a\u7ed3\u679c",
    })
    return uncertainties


def summarize(status, refs, matrix, findings, uncited, documents, notices):
    return ("\u79bb\u7ebf\u9884\u68c0\u5b8c\u6210\uff1a\u6750\u6599 %d \u4efd\uff0c"
            "\u53c2\u8003\u6587\u732e %d \u6761\uff0c\u5f15\u7528\u77e9\u9635 "
            "%d \u884c\uff0c\u5019\u9009\u95ee\u9898 %d \u6761\uff08\u5176"
            "\u4e2d\u672a\u5f15\u7528\u65ad\u8a00 %d \u6761\uff09\uff0c"
            "\u64a4\u7a3f/\u7248\u672c\u901a\u77e5 %d \u6761\uff1b\u72b6"
            "\u6001=%s\u3002" % (len(documents), len(refs), len(matrix),
                              len(findings), len(uncited), len(notices),
                              status))


def decide_status(readable, refs, matrix, findings):
    if not readable:
        return "no_materials"
    if matrix and not refs:
        return "missing_reference_list"
    if findings:
        return "ok_with_candidates"
    return "ok_no_issues"


def decide_review_status(status, findings, matrix):
    """Four-state review status expected by the output contract."""
    if status == "no_materials":
        return "not_checked"
    if findings:
        # Candidate findings are not confirmed yet.
        return "needs_recheck"
    if matrix:
        return "pass"
    return "not_checked"


def reference_summary(ref):
    return {
        "source_id": ref.get("ref_id", ""),
        "label": ref.get("label", ""),
        "key": ref.get("key", ""),
        "first_author": ref.get("first_author", ""),
        "year": ref.get("year", ""),
        "title": ref.get("title", ""),
        "doi": ref.get("doi", ""),
        "path": ref.get("path", ""),
        "locator": ref.get("locator", ""),
        "has_source_text": bool(ref.get("source_text")),
        "is_opinion": bool(ref.get("is_opinion")),
        "placeholder": bool(ref.get("placeholder")),
        "use_ok": bool(ref.get("use_ok")),
    }


def review_protocol():
    return {
        "candidate_only": True,
        "human_review_required": True,
        "steps": [
            "\u6253\u5f00\u6bcf\u6761\u5019\u9009\u7684 evidence\uff0c\u56de\u5230"
            "\u539f\u6587\u786e\u8ba4 quote \u9010\u5b57\u53ef\u67e5\u3002",
            "\u9605\u8bfb\u6765\u6e90\u53ef\u6838\u9a8c\u6587\u672c\uff0c"
            "\u81ea\u884c\u5224\u5b9a full/partial/none/unverifiable\uff0c"
            "\u4e0d\u76f4\u63a5\u91c7\u7528\u811a\u672c\u7ed3\u8bba\u3002",
            "\u68c0\u67e5\u5047\u9633\u6027\uff08\u6570\u503c\u5728\u6765\u6e90"
            "\u5176\u4ed6\u6bb5\u843d\u3001\u5f15\u7528\u4ec5\u4f5c\u80cc\u666f"
            "\u3001\u6765\u6e90\u4e3a\u7efc\u8ff0\u7b49\uff09\uff0c\u628a"
            "\u4e0d\u6210\u7acb\u9879\u5199\u5165 rejected_candidates\u3002",
            "\u8865\u5145\u811a\u672c\u672a\u8986\u76d6\u7684\u8bed\u4e49"
            "\u8d85\u8303\u56f4\u3001\u5077\u6362\u7ed3\u8bba\u3001\u5f15\u7528"
            "\u5e74\u4efd/\u7248\u672c\u9519\u914d\u3002",
            "\u786e\u8ba4\u540e\u624d\u8fdb\u5165 findings\uff1b\u4e0d\u786e"
            "\u5b9a\u9879\u8fdb\u5165 uncertainties\u3002",
        ],
    }


# ---------------------------------------------------------------------------
# report and candidate files
# ---------------------------------------------------------------------------

def _md_cell(value, limit=160):
    text = excerpt(value, limit).replace("|", "\\|")
    return text or "-"


def render_report(payload):
    lines = [
        "# \u5f15\u7528\u4e0e\u4e8b\u5b9e\u6838\u9a8c\uff1a\u786e\u5b9a\u6027"
        "\u9884\u68c0\u62a5\u544a",
        "",
        "- case_id: `%s`" % payload["case_id"],
        "- \u751f\u6210\u65f6\u95f4: %s" % payload["generated_at"],
        "- \u811a\u672c: `verify_citations.py` v%s" % payload["version"],
        "- \u72b6\u6001: `%s`" % payload["status"],
        "- review_status: `%s`" % payload.get("review_status", "not_checked"),
        "- \u8bf4\u660e: \u672c\u62a5\u544a\u53ea\u5305\u542b**\u786e\u5b9a"
        "\u6027\u5019\u9009**\uff0c\u4e0d\u662f\u6700\u7ec8\u5ba1\u67e5"
        "\u7ed3\u8bba\u3002\u6bcf\u6761\u5747\u9700\u6a21\u578b/\u4eba\u5de5"
        "\u56de\u5230\u539f\u6587\u590d\u6838\u3002",
        "",
        payload["summary"],
        "",
        "## 1. \u8f93\u5165\u6e05\u5355",
        "",
        "| path | kind | chars | refs | warnings |",
        "|---|---|---:|---:|---|",
    ]
    for item in payload["inputs"]:
        lines.append("| %s | %s | %d | %d | %s |" % (
            item["path"], item["kind"], item["chars"],
            item["reference_entries"],
            _md_cell("; ".join(item["warnings"]), 80) if item["warnings"] else "-"))
    fulltext = payload.get("fulltext") or {}
    lines += ["", "## 1b. \u5168\u6587\u5206\u6bb5", "",
              "\u7ae0\u8282: %s\uff1b\u6bb5\u843d %d\uff1b\u53e5\u5b50 %d\u3002" % (
                  ", ".join(fulltext.get("sections") or []) or "none",
                  fulltext.get("paragraphs", 0),
                  fulltext.get("sentences", 0)),
              "", "| path | sections | paragraphs | sentences | citation claims |",
              "|---|---|---:|---:|---:|"]
    for item in fulltext.get("documents") or []:
        lines.append("| %s | %s | %d | %d | %d |" % (
            _md_cell(item.get("path"), 80),
            ", ".join(item.get("sections") or []) or "-",
            item.get("paragraphs", 0), item.get("sentences", 0),
            item.get("citation_claims", 0)))
    lines += ["", "## 2. \u6765\u6e90\u72b6\u6001", "",
              "| source | status | version_note | needs_recheck | basis |",
              "|---|---|---|---|---|"]
    for row in payload["source_status"]:
        lines.append("| %s | %s | %s | %s | %s |" % (
            _md_cell(row["source"], 80), row["status"],
            _md_cell(row["version_note"], 100) or "-",
            "yes" if row["needs_recheck"] else "no",
            _md_cell(row["status_basis"], 80)))
    lines += ["", "## 2b. \u5ba1\u7a3f\u9762\u8986\u76d6", "",
              "| facet | status | items | reason |", "|---|---|---|---|"]
    for row in payload.get("facets_checked") or []:
        lines.append("| %s | %s | %s | %s |" % (
            row.get("facet"), row.get("status"),
            _md_cell(", ".join(row.get("items") or []), 100),
            _md_cell(row.get("reason"), 100)))
    lines += ["", "### \u5f15\u7528\u5b50\u9762", "",
              "| subfacet | status | detail |", "|---|---|---|"]
    for row in payload.get("citation_facets") or []:
        lines.append("| %s | %s | %s |" % (
            row.get("facet"), row.get("status"),
            _md_cell(row.get("detail"), 140)))
    lines += ["", "## 3. claim-source \u77e9\u9635\u5019\u9009", "",
              "| claim_id | claim | section | source | verdict | type | reason | locator |",
              "|---|---|---|---|---|---|---|---|"]
    for row in payload["claim_source_matrix"]:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["claim_id"], _md_cell(row["claim"], 110),
            row.get("claim_section") or "-",
            _md_cell(row["source"], 70), row["verdict"],
            row["type"] or "-", _md_cell(row["reason"], 110),
            row["claim_locator"]))
    lines += ["", "## 4. \u5019\u9009\u95ee\u9898\uff08\u9700\u4eba\u5de5"
              "\u590d\u6838\uff09", ""]
    if not payload["findings"]:
        lines.append("\u672a\u53d1\u73b0\u786e\u5b9a\u6027\u5019\u9009"
                     "\u95ee\u9898\u3002\u8fd9\u4e0d\u7b49\u4e8e\u8bba\u6587"
                     "\u65e0\u95ee\u9898\uff1b\u8bed\u4e49\u5c42\u9762\u4ecd"
                     "\u9700\u8981\u4eba\u5de5\u5ba1\u67e5\u3002")
    for finding in payload["findings"]:
        lines += [
            "",
            "### %s [%s / %s] severity=%s" % (
                finding["id"], finding["type"], finding["subtype"],
                finding["severity"]),
            "",
            "- location: `%s` `%s`" % (finding["location"]["path"],
                                       finding["location"]["locator"]),
            "- original_quote: %s" % _md_cell(finding["original_quote"], 240),
            "- issue: %s" % finding["issue"],
            "- fix: %s" % finding["fix"],
            "- needs_review: %s" % ("yes" if finding["needs_review"] else "no"),
            "- evidence:",
        ]
        for item in finding["evidence"]:
            lines.append("  - `%s` `%s`: %s" % (
                item["path"], item["locator"], _md_cell(item["quote"], 200)))
    lines += ["", "## 5. \u672a\u5f15\u7528\u65ad\u8a00\u5019\u9009", ""]
    if not payload["uncited_claims"]:
        lines.append("- \u672a\u53d1\u73b0\u5019\u9009\u3002")
    for finding in payload["uncited_claims"]:
        lines.append("- %s `%s`: %s" % (
            finding["id"], finding["location"]["locator"],
            _md_cell(finding["original_quote"], 200)))
    lines += ["", "## 6. \u4e0d\u786e\u5b9a\u6027\u4e0e\u5c40\u9650", ""]
    for item in payload["uncertainties"]:
        lines.append("- **%s**: %s\uff1b%s\uff1b\u5efa\u8bae\uff1a%s" % (
            item["item"], item["reason"], item["impact"], item["action"]))
    lines += ["", "## 7. \u4eba\u5de5\u590d\u6838\u6b65\u9aa4", ""]
    for index, step in enumerate(payload["review_protocol"]["steps"], 1):
        lines.append("%d. %s" % (index, step))
    lines += ["", "## 8. \u8d28\u91cf\u68c0\u67e5", "",
              "| item | status | detail |", "|---|---|---|"]
    for check in payload["checks"]:
        lines.append("| %s | %s | %s |" % (
            check["item"], check["status"], _md_cell(check["detail"], 120)))
    lines += ["", "---", "",
              "\u672c\u62a5\u544a\u7531\u786e\u5b9a\u6027\u811a\u672c\u751f"
              "\u6210\uff0c\u4e0d\u8054\u7f51\u3001\u4e0d\u7f16\u9020\u6587"
              "\u732e\u3002\u6240\u6709\u5019\u9009\u987b\u7ecf\u4eba\u5de5"
              "\u786e\u8ba4\u540e\u624d\u80fd\u5199\u5165\u6700\u7ec8"
              " findings\u3002", ""]
    return "\n".join(lines)


def candidate_payload(payload):
    return {
        "case_id": payload["case_id"],
        "mode": "citation-fact-check",
        "_note": ("\u786e\u5b9a\u6027\u5019\u9009\u6570\u636e\uff0c"
                  "\u4e0d\u662f\u6700\u7ec8\u7ed3\u8bba\u3002\u5408\u5e76"
                  "\u524d\u5fc5\u987b\u9010\u6761\u6838\u5bf9 quote \u5e76"
                  "\u505a\u8bed\u4e49\u5224\u5b9a\uff1b\u4e0d\u786e\u5b9a"
                  "\u9879\u5199\u5165 uncertainties\u3002"),
        "precheck": "output/citation-check.json",
        "review_status": payload.get("review_status", "not_checked"),
        "claim_source_matrix": payload["claim_source_matrix"],
        "source_status": payload["source_status"],
        "findings": payload["findings"],
        "facets_checked": payload.get("facets_checked") or [],
        "citation_facets": payload.get("citation_facets") or [],
        "fulltext": payload.get("fulltext") or {},
        "evidence": payload["evidence"],
        "uncertainties": payload["uncertainties"],
        "checks": payload["checks"],
    }


def merge_into_work(payload, work_path):
    """Merge deterministic candidates without overwriting model judgement."""
    work = load_json(work_path, {})
    if not isinstance(work, dict):
        work = {}
    work.setdefault("case_id", payload.get("case_id") or "CASE")
    work.setdefault("mode", "citation-fact-check")
    work.setdefault("summary", "")
    for field in ("findings", "claim_source_matrix", "source_status",
                  "facets_checked", "citation_facets", "checks", "evidence",
                  "uncertainties", "reviewed_candidates",
                  "rejected_candidates"):
        if not isinstance(work.get(field), list):
            work[field] = []
    used_ids = {str(item.get("id")) for item in work["findings"]
                if isinstance(item, dict)}
    existing_sig = {(item.get("type"), item.get("subtype"),
                     norm_space(item.get("original_quote"))[:160])
                    for item in work["findings"] if isinstance(item, dict)}
    next_id = 1
    merged = 0
    for row in payload.get("findings") or []:
        sig = (row.get("type"), row.get("subtype"),
               norm_space(row.get("original_quote"))[:160])
        if sig in existing_sig:
            continue
        while "A%02d" % next_id in used_ids:
            next_id += 1
        new_row = dict(row)
        new_row["id"] = "A%02d" % next_id
        new_row["rule_status"] = row.get("status")
        new_row["status"] = "needs_recheck"
        work["findings"].append(new_row)
        used_ids.add(new_row["id"])
        existing_sig.add(sig)
        merged += 1
        next_id += 1
    matrix_sig = {(norm_space(row.get("claim")), norm_space(row.get("source")))
                  for row in work["claim_source_matrix"] if isinstance(row, dict)}
    for row in payload.get("claim_source_matrix") or []:
        sig = (norm_space(row.get("claim")), norm_space(row.get("source")))
        if sig not in matrix_sig:
            work["claim_source_matrix"].append(dict(row))
            matrix_sig.add(sig)
    status_sig = {str(row.get("source")) for row in work["source_status"]
                  if isinstance(row, dict)}
    for row in payload.get("source_status") or []:
        if str(row.get("source")) not in status_sig:
            work["source_status"].append(dict(row))
            status_sig.add(str(row.get("source")))
    facet_sig = {row.get("facet") for row in work["facets_checked"]
                 if isinstance(row, dict)}
    for row in payload.get("facets_checked") or []:
        if row.get("facet") not in facet_sig:
            work["facets_checked"].append(dict(row))
            facet_sig.add(row.get("facet"))
    for row in payload.get("citation_facets") or []:
        if not any(isinstance(item, dict) and item.get("facet") == row.get("facet")
                   for item in work["citation_facets"]):
            work["citation_facets"].append(dict(row))
    for row in payload.get("checks") or []:
        if not any(isinstance(item, dict) and item.get("item") == row.get("item")
                   for item in work["checks"]):
            work["checks"].append(dict(row))
    existing_quotes = {norm_space(item.get("quote"))
                       for item in work["evidence"] if isinstance(item, dict)}
    for row in payload.get("evidence") or []:
        quote = norm_space(row.get("quote"))
        if quote and quote not in existing_quotes:
            work["evidence"].append(dict(row))
            existing_quotes.add(quote)
    for row in payload.get("uncertainties") or []:
        item = str(row.get("item") or "")
        if not any(isinstance(x, dict) and x.get("item") == item
                   for x in work["uncertainties"]):
            work["uncertainties"].append(dict(row))
    if not work.get("review_status") or work.get("review_status") not in (
            "issues", "pass", "not_checked"):
        work["review_status"] = payload.get("review_status") or "needs_recheck"
    if not work.get("summary"):
        work["summary"] = (
            "引用预检产生 %d 条候选（合并 %d 条，全部标 needs_recheck），"
            "claim-source 矩阵 %d 行；候选必须逐条人工复核。"
            % (len(payload.get("findings") or []), merged,
               len(payload.get("claim_source_matrix") or [])))
    work["precheck"] = "output/citation-check.json"
    work["rule_review"] = {
        "script": "scripts/verify_citations.py",
        "generated_at": payload.get("generated_at"),
        "status": payload.get("status"),
        "candidates": len(payload.get("findings") or []),
        "merged": merged,
        "precheck": "work/citation-check.json",
        "note": "merged rows use status=needs_recheck; confirm, edit or delete them",
    }
    write_json(work_path, work)
    return {"work": str(work_path), "merged": merged,
            "findings": len(work["findings"])}


def write_outputs(payload, work_dir, output_dir):
    work_dir, output_dir = Path(work_dir), Path(output_dir)
    write_json(work_dir / "citation-check.json", payload)
    write_json(work_dir / "review-candidates.json", candidate_payload(payload))
    write_json(output_dir / "citation-check.json", payload)
    write_text(output_dir / "citation-precheck.md", render_report(payload))


def error_payload(case_id, exc, trace):
    message = "%s: %s" % (type(exc).__name__, exc)
    return {
        "case_id": case_id or "CASE",
        "mode": "citation-fact-check",
        "script": "verify_citations.py",
        "version": VERSION,
        "generated_at": now_iso(),
        "status": "error",
        "review_status": "not_checked",
        "summary": "\u9884\u68c0\u811a\u672c\u6267\u884c\u5931\u8d25\uff1a%s"
                   % message,
        "inputs": [],
        "inventory": {},
        "references": [],
        "source_status": [],
        "claim_source_matrix": [],
        "findings": [],
        "uncited_claims": [],
        "facets_checked": [],
        "citation_facets": [],
        "fulltext": {"sections": [], "paragraphs": 0, "sentences": 0,
                     "documents": []},
        "evidence": [],
        "uncertainties": [{
            "item": "script_error",
            "reason": message,
            "impact": "\u672c\u6b21\u6ca1\u6709\u4ea7\u751f\u53ef\u7528\u7684"
                      "\u9884\u68c0\u7ed3\u679c",
            "action": "\u67e5\u770b traceback \u5e76\u4fee\u590d\u8f93\u5165"
                      "\u6216\u73af\u5883\uff1b\u4ecd\u53ef\u4eba\u5de5\u5b8c"
                      "\u6210\u5ba1\u67e5",
        }],
        "checks": [{"item": "\u9884\u68c0\u6267\u884c", "status": "fail",
                    "detail": message}],
        "review_protocol": review_protocol(),
        "warnings": [trace[-4000:]],
    }


# ---------------------------------------------------------------------------
# self test
# ---------------------------------------------------------------------------

SELFTEST_MANUSCRIPT = """# Synthetic selftest manuscript

Synthetic only; fabricated for testing.

## Abstract
S1. Chronic stress causes memory decline in older adults [A].

## 1 Introduction
S2. In a national survey, 62% of older adults reported memory problems [B].
S3. A randomized trial reported a 12% improvement in recall [C].

## 2 Methods
S4. The authors wrote "Memory training reverses decline" [A].

## 3 Results
S5. The benefit persisted for five years [D].

## 4 Discussion
S6. These findings establish that apps reverse decline [E].
S7. Memory apps improve recall in all older adults.

## References
[A] Lee et al. (2022). Stress and memory in older adults. Journal of Synthetic Aging, 14(2), 101-110. doi:10.9999/synth.2022.101. The study reports an association and states that causation cannot be inferred.
[B] Ortiz et al. (2021). Self-reported memory complaints. Synthetic Public Health, 9(1), 5-12. doi:10.9999/synth.2021.005. The abstract reports 26% of adults aged 65 and older reported memory problems.
[C] Chen et al. (2020). Memory-training app trial. Synthetic Trials, 3(4), 200-209. doi:10.9999/synth.2020.200. The trial reports a 12% improvement at 8 weeks; no five-year follow-up was conducted.
[D] Post hoc commentary (2019). Five-year persistence claims. Synthetic Commentary, 1(1), 1-3. The commentary argues that persistence claims are unsupported and reports no empirical follow-up.
[E] An opinion piece cited in the discussion; the item is not included in the supplied reference list.
"""

SELFTEST_RETRACTION = """# Synthetic selftest retraction notice

Synthetic only.

Retraction notice: Chen et al. (2020), Synthetic Trials 3(4), was retracted in 2023.
"""

SELFTEST_VERSION = """# Synthetic selftest version note

Synthetic only.

The 2019 preprint of Chen et al. reported a 5% improvement.
The 2020 published version reported a 12% improvement.
"""


def run_selftest():
    results = {}
    with tempfile.TemporaryDirectory(prefix="cite-selftest-") as tmp:
        root = Path(tmp)

        # 1. success path
        input_dir = root / "success-input"
        input_dir.mkdir()
        (input_dir / "manuscript.md").write_text(SELFTEST_MANUSCRIPT,
                                                 encoding="utf-8")
        (input_dir / "retraction-notice.md").write_text(SELFTEST_RETRACTION,
                                                        encoding="utf-8")
        (input_dir / "preprint-note.md").write_text(SELFTEST_VERSION,
                                                    encoding="utf-8")
        (input_dir / "case.json").write_text(json.dumps({
            "case_id": "SYNTH-SELFTEST",
            "synthetic": True,
        }, ensure_ascii=False), encoding="utf-8")
        work, output = root / "success-work", root / "success-output"
        payload = analyze(input_dir, work, output)
        write_outputs(payload, work, output)
        findings = payload["findings"]
        types = {finding["type"] for finding in findings}
        statuses = {row["source"]: row["status"]
                    for row in payload["source_status"]}
        checks = {
            "status_ok": payload["status"] == "ok_with_candidates",
            "references_found": len(payload["references"]) == 5,
            "missing_reference_e": any(
                "E" in (finding.get("source") or "")
                and finding["type"] == "missing_reference"
                for finding in findings),
            "numeric_mismatch_b": any(
                finding.get("subtype") == "numeric_mismatch"
                and "62" in finding.get("issue", "")
                for finding in findings),
            "retraction_detected": any(
                row["status"] == "retracted"
                for row in payload["source_status"]),
            "quote_mismatch_detected": "quote_mismatch" in types,
            "uncited_claim_detected": any(
                finding["type"] == "uncited_claim" for finding in findings),
            "all_quotes_verbatim": _selftest_quote_check(payload, input_dir),
            "fulltext_sections": {"abstract", "introduction", "methods",
                                  "results", "discussion", "references"} <=
                                 set((payload.get("fulltext") or {}).get("sections") or []),
            "facets_checked": len(payload.get("facets_checked") or []) == 6,
            "citation_facets": len(payload.get("citation_facets") or []) >= 8,
            "outputs_written": ((output / "citation-precheck.md").is_file()
                                and (output / "citation-check.json").is_file()
                                and (work / "review-candidates.json").is_file()),
        }

        # 2. empty input
        empty_input = root / "empty-input"
        empty_input.mkdir()
        empty = analyze(empty_input, root / "empty-work",
                        root / "empty-output")
        write_outputs(empty, root / "empty-work", root / "empty-output")
        empty_checks = {
            "empty_status": empty["status"] == "no_materials",
            "empty_valid_json": bool(load_json(
                root / "empty-output" / "citation-check.json", {})),
            "empty_uncertainty": bool(empty["uncertainties"]),
        }

        # 3. missing reference list
        missing_input = root / "missing-ref-input"
        missing_input.mkdir()
        (missing_input / "manuscript.md").write_text(
            "# Synthetic only\n\n"
            "S1. A trial reported a 12% improvement [1].\n"
            "S2. A survey reported 62% memory problems [2].\n",
            encoding="utf-8")
        missing = analyze(missing_input, root / "missing-work",
                          root / "missing-output")
        write_outputs(missing, root / "missing-work", root / "missing-output")
        missing_checks = {
            "missing_status": missing["status"] == "missing_reference_list",
            "missing_findings": sum(
                1 for finding in missing["findings"]
                if finding["type"] == "missing_reference") >= 2,
            "missing_uncertainty": any(
                item["item"] == "reference_list"
                for item in missing["uncertainties"]),
        }
        merge_path = root / "merge-work" / "review-findings.json"
        write_json(merge_path, {"case_id": "SYNTH-SELFTEST", "findings": [],
                                "claim_source_matrix": [], "source_status": [],
                                "facets_checked": [], "evidence": []})
        merged = merge_into_work(payload, merge_path)
        merged_work = load_json(merge_path, {})
        merge_checks = {
            "merged": merged.get("merged", 0) > 0,
            "all_needs_recheck": all(
                row.get("status") == "needs_recheck"
                for row in merged_work.get("findings") or []),
            "matrix_rows": bool(merged_work.get("claim_source_matrix")),
            "facets": len(merged_work.get("facets_checked") or []) == 6,
        }
        results = {"success": checks, "empty": empty_checks,
                   "missing_reference_list": missing_checks,
                   "merge": merge_checks}

    ok = all(value for group in results.values() for value in group.values())
    print(json.dumps({"selftest": "pass" if ok else "fail",
                      "cases": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def _selftest_quote_check(payload, input_dir):
    texts = {path.name: read_text(path)
             for path in Path(input_dir).glob("*") if path.is_file()}
    for finding in payload["findings"]:
        for item in finding.get("evidence") or []:
            source = texts.get(Path(item["path"]).name)
            if source is None or not quote_present(source, item["quote"]):
                return False
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Deterministic offline citation/fact pre-check")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--material", default=None,
                        help="only scan this relative material path for claims")
    parser.add_argument("--selftest", action="store_true",
                        help="run built-in success/empty/missing-reference tests")
    parser.add_argument("--merge", action="store_true",
                        help="merge candidates into work/review-findings.json "
                             "with status=needs_recheck")
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest()

    try:
        payload = analyze(args.input, args.work, args.output, args.material)
        write_outputs(payload, args.work, args.output)
        merge_result = None
        if args.merge and payload.get("status") != "no_materials":
            merge_result = merge_into_work(
                payload, Path(args.work) / "review-findings.json")
        print(json.dumps({
            "status": payload["status"],
            "case_id": payload["case_id"],
            "references": payload["inventory"].get("reference_entries", 0),
            "claim_source_rows": payload["inventory"].get(
                "claim_source_rows", 0),
            "findings": payload["inventory"].get("findings", 0),
            "sections": (payload.get("fulltext") or {}).get("sections", []),
            "merge": merge_result,
            "work": str(Path(args.work) / "citation-check.json"),
            "report": str(Path(args.output) / "citation-precheck.md"),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        trace = traceback.format_exc()
        case = load_json(Path(args.input) / "case.json", {})
        payload = error_payload(str(case.get("case_id") or ""), exc, trace)
        try:
            write_outputs(payload, args.work, args.output)
        except Exception:  # noqa: BLE001
            print(trace, file=sys.stderr)
            return 2
        print(json.dumps({"status": "error", "error": str(exc)},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
