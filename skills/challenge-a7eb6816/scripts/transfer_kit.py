#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task-specific stdlib helpers for the research-transfer writing pipeline.

Imported as ``transfer_kit`` by every script in ``scripts/``.  No third-party
packages, no network.  It only provides text/JSON/number utilities; it does not
write or rewrite the manuscript.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".tex", ".rst", ".json", ".jsonl", ".csv",
    ".tsv", ".yaml", ".yml", ".html", ".htm", ".xml", ".bib", ".ris",
}
DOCX_EXTS = {".docx"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".bmp", ".gif"}
SKIP_DIRS = {"__pycache__", ".git", ".svn", ".hg", "node_modules", ".cache"}

# Unit spellings are normalised before comparing draft numbers with sources.
UNIT_ALIASES = {
    "℃": "°c", "摄氏度": "°c", "摄氏度": "°c", "degree c": "°c", "°c": "°c",
    "小时": "h", "钟头": "h", "分钟": "min", "秒": "s", "天": "d", "日": "d",
    "年": "a", "周": "wk", "个月": "mo", "月": "mo",
    "毫升": "ml", "升": "l", "微升": "ul", "毫克": "mg", "克": "g",
    "千克": "kg", "公斤": "kg", "微克": "ug", "纳克": "ng",
    "纳米": "nm", "微米": "um", "毫米": "mm", "厘米": "cm", "千米": "km",
    "毫摩尔": "mm", "微摩尔": "um", "摩尔": "m", "当量": "eq",
    "小时-1": "h-1", "转/分": "rpm", "转每分钟": "rpm",
    "伏": "v", "毫伏": "mv", "安": "a", "毫安": "ma", "瓦": "w",
    "千瓦": "kw", "兆瓦": "mw", "赫兹": "hz", "千赫兹": "khz",
    "兆赫兹": "mhz", "吉赫兹": "ghz", "分贝": "db",
    "百分比": "%", "百分": "%", "倍": "×",
}

SENTENCE_END = re.compile(r"(?<=[。！？!?；;])\s*|(?<=[.])\s+(?=[A-Z0-9一-鿿])")
CJK = re.compile(r"[㐀-鿿]")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path, limit=None, errors="replace"):
    """Read a text file, trying UTF-8 first and GB18030 as a fallback."""
    p = Path(path)
    raw = p.read_bytes()
    if limit is not None:
        raw = raw[:limit]
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors=errors)


def read_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, ValueError, TypeError):
        return default


def write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rel_posix(path, base):
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def normalize_space(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def nfkc(text):
    return unicodedata.normalize("NFKC", str(text or ""))


def normalize_unit(unit):
    key = nfkc(unit).strip().lower().replace(" ", "")
    return UNIT_ALIASES.get(key, key)


def normalize_number(value):
    text = nfkc(str(value)).replace(",", "").replace(" ", "")
    try:
        num = float(text)
    except ValueError:
        return text
    if num.is_integer():
        return str(int(num))
    return ("%.6f" % num).rstrip("0").rstrip(".")


def number_key(value, unit):
    return "%s|%s" % (normalize_number(value), normalize_unit(unit))


def split_sentences(text):
    text = normalize_space(text)
    if not text:
        return []
    parts = [p.strip() for p in SENTENCE_END.split(text) if p.strip()]
    return parts or [text]


def guess_language(text):
    sample = str(text or "")[:20000]
    cjk = len(CJK.findall(sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    if cjk >= 20 and cjk >= latin * 0.15:
        return "zh"
    if latin >= 20:
        return "en"
    return "unknown"


def excerpt(text, limit=180):
    text = normalize_space(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def unique(seq):
    seen = set()
    out = []
    for item in seq:
        key = normalize_space(item).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def keywords(text, limit=24):
    """Return coarse matching keys: CJK bigrams and Latin words."""
    text = nfkc(text).lower()
    out = []
    for word in WORD.findall(text):
        if word not in {"the", "and", "with", "from", "that", "this", "were",
                        "was", "for", "are", "has", "have", "not", "but"}:
            out.append(word)
    cjk = CJK.findall(text)
    for i in range(len(cjk) - 1):
        out.append(cjk[i] + cjk[i + 1])
    return unique(out)[:limit]


def markdown_blocks(text):
    """Yield ``{kind, level, text, line}`` for headings/paragraphs/lists.

    Fenced code blocks are returned as ``code`` blocks.  Line numbers are
    1-based and refer to the source text.
    """
    lines = str(text or "").splitlines()
    blocks = []
    buffer = []
    start = 0

    def flush(end_line):
        nonlocal buffer, start
        if buffer:
            joined = " ".join(x.strip() for x in buffer).strip()
            if joined:
                blocks.append({"kind": "paragraph", "level": 0,
                               "text": joined, "line": start})
        buffer = []

    in_fence = False
    fence_start = 0
    fence = []
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                blocks.append({"kind": "code", "level": 0,
                               "text": "\n".join(fence), "line": fence_start})
                fence = []
                in_fence = False
            else:
                flush(idx - 1)
                in_fence = True
                fence_start = idx + 1
            continue
        if in_fence:
            fence.append(line)
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush(idx - 1)
            blocks.append({"kind": "heading", "level": len(m.group(1)),
                           "text": m.group(2).strip(), "line": idx})
            continue
        if not stripped:
            flush(idx - 1)
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", stripped):
            flush(idx - 1)
            blocks.append({"kind": "list", "level": 0, "text": stripped,
                           "line": idx})
            continue
        if stripped.startswith(">"):
            flush(idx - 1)
            blocks.append({"kind": "quote", "level": 0,
                           "text": stripped.lstrip("> ").strip(), "line": idx})
            continue
        if re.match(r"^\|.*\|\s*$", stripped):
            flush(idx - 1)
            blocks.append({"kind": "table_row", "level": 0,
                           "text": stripped, "line": idx})
            continue
        if not buffer:
            start = idx
        buffer.append(line)
    flush(len(lines))
    return blocks


def paragraph_units(text, locator_prefix="L", page_style=False):
    """Split extracted text into locatable units.

    ``page_style`` treats form-feed characters as page breaks (PDF text).
    Otherwise markdown blocks are used and locators point at line spans.
    """
    text = str(text or "")
    if not text.strip():
        return []
    units = []
    if page_style:
        pages = text.split("\f")
        for page_no, page in enumerate(pages, 1):
            for block in markdown_blocks(page):
                if block["kind"] in ("heading", "code"):
                    continue
                units.append({"locator": "p.%d" % page_no,
                              "text": block["text"], "kind": block["kind"]})
        return units
    for block in markdown_blocks(text):
        if block["kind"] in ("heading", "code"):
            continue
        end = block["line"] + max(1, block["text"].count("\n"))
        locator = "%s%d-%s%d" % (locator_prefix, block["line"],
                                 locator_prefix, end)
        units.append({"locator": locator, "text": block["text"],
                      "kind": block["kind"]})
    return units
