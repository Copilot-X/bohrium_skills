#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task-specific stdlib helpers for evidence-driven writing.

This is the single shared library for challenge-b810ef56 (imported as
``evidence_kit`` by every other script).  It contains text decoding, sentence
splitting, citation/number parsing, bilingual concept matching, DOCX/PDF text
extraction and the reference-block helpers.

No third-party packages, no network.  Modules in scripts/ import this file
from the same directory.  main.py bootstraps a temporary mirror when the
files are stored as ``*.py.md`` on disk (local repository convention) and are
executed directly.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".tex", ".rst", ".csv", ".tsv", ".json",
    ".jsonl", ".yaml", ".yml", ".html", ".htm", ".xml", ".bib", ".ris",
}
DOCX_EXTS = {".docx"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".bmp", ".gif"}
SKIP_DIRS = {"__pycache__", ".git", ".svn", ".hg", "node_modules", ".cache"}
TASK_METADATA = {"case.json", "task.md", "output-contract.md", "resources.yaml"}

CITATION_RE = re.compile(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]")
REF_LINE_RE = re.compile(r"^\s*\[(\d+)\]")
REF_HEADING_RE = re.compile(
    r"^#{0,6}\s*(references|reference list|bibliography|参考文献|引用文献)"
    r"\s*[:：]?\s*$", re.I
)
SENT_SPLIT = re.compile(r"(?<=[。！？!?])\s*|(?<=[；;])\s*")
SUB_SPLIT = re.compile(r"(?<=[.])\s+(?=[A-Z0-9\"“(])")
NUM_RE = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?\s*%?")
CJK = re.compile(r"[㐀-鿿]")
CJK_RUN = re.compile(r"[㐀-鿿]+")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
COUNT_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s,;)\]}]+")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

STOPWORDS = {
    "the", "and", "with", "from", "that", "this", "these", "those", "were",
    "was", "for", "are", "has", "have", "had", "not", "but", "its", "their",
    "our", "under", "into", "than", "then", "also", "been", "being", "which",
    "while", "where", "when", "what", "who", "whom", "how", "all", "any",
    "can", "may", "might", "could", "does", "did", "done", "will", "would",
    "should", "there", "here", "such", "only", "more", "most", "less",
}

# Bilingual concept lexicon.  It lets a Chinese sentence find an English
# source unit (and vice versa) without translating or paraphrasing either
# side.  Matching only narrows candidates; the verbatim quote remains the
# evidence and is checked separately.
CONCEPTS = [
    ("experiment", ["实验", "试验", "测试", "测定"],
     ["experiment", "experiments", "experimental", "tested", "assay"]),
    ("condition", ["条件", "工况"],
     ["condition", "conditions"]),
    ("causality", ["因果", "导致", "引起", "造成"],
     ["causal", "causality", "cause", "causes", "caused"]),
    ("association", ["相关", "关联", "伴随", "一致"],
     ["associated", "association", "correlat", "consistent with"]),
    ("study", ["研究", "论文", "文献", "作者", "报告"],
     ["study", "studies", "paper", "reported", "reports"]),
    ("design", ["设计", "方法", "方案", "流程"],
     ["design", "method", "methods", "protocol"]),
    ("observational", ["观察", "观察性", "队列", "随访"],
     ["observational", "cohort", "follow-up"]),
    ("comparison", ["比较", "对比", "高于", "低于"],
     ["compar", "versus", "higher", "lower", "than"]),
    ("selectivity", ["选择性"], ["selectivity", "selective"]),
    ("catalyst", ["催化剂", "催化"], ["catalyst", "catalysts", "catalytic"]),
    ("fixed_bed", ["固定床"], ["fixed-bed", "fixed bed"]),
    ("reactor", ["反应器"], ["reactor"]),
    ("symptom", ["症状"], ["symptom", "symptoms"]),
    ("score", ["评分", "得分"], ["score", "scores", "score change"]),
    ("intervention", ["干预", "治疗"], ["intervention", "treatment"]),
    ("reduction", ["降低", "下降", "减少"], ["reduction", "reduced", "decrease"]),
    ("stability", ["稳定性", "稳定"], ["stability", "stable"]),
    ("long_term", ["长期"], ["long-term", "long term"]),
    ("adult", ["成人", "成年人"], ["adult", "adults"]),
    ("sample", ["样本", "受试者", "患者", "纳入", "名"],
     ["sample", "participants", "patients", "enrolled"]),
    ("limitation", ["局限", "限制", "不能", "未报告", "不足以"],
     ["limitation", "cannot", "can not", "did not", "does not", "not reported"]),
    ("significant", ["显著"], ["significant", "significantly"]),
    ("stability_test", ["循环", "寿命", "耐久"], ["cycle", "cycling", "durability"]),
]

HEDGE_PATTERNS = [
    r"可能", r"或许", r"也许", r"提示", r"推测", r"似乎", r"倾向于",
    r"尚不", r"尚不能", r"有待", r"不能排除", r"不能建立", r"相关而非因果",
    r"不确定", r"尚未", r"并未", r"无法确定",
    r"\bmay\b", r"\bmight\b", r"\bcould\b", r"suggest", r"indicat",
    r"\blikely\b", r"possibl", r"\bappears?\b", r"consistent with",
    r"associated with", r"correlat", r"cannot establish", r"remains? unclear",
    r"not reported", r"under these conditions",
]
LIMITATION_MARKERS = [
    "cannot", "can not", "does not", "did not", "not reported", "unclear",
    "不能", "未报告", "无法", "尚不", "局限", "限制",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_text(path, errors="replace"):
    p = Path(path)
    raw = p.read_bytes()
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


def rel_posix(path, base):
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def normalize_space(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def nfkc(text):
    return unicodedata.normalize("NFKC", str(text or ""))


def excerpt(text, limit=180):
    text = normalize_space(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def strip_markdown(line):
    line = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", line.strip())
    line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    return line.strip()


def split_sentences(paragraph):
    """Split a paragraph like the legacy auditor, including ``[n] Next``.

    A citation marker often sits between two English sentences
    (``... result.[1] The next sentence ...``).  The legacy splitter only
    looked for a period followed by an uppercase letter, so it glued those
    sentences (and their claims) together.  Insert a boundary after a closing
    citation bracket when a new sentence starts, then split as before.
    """
    text = str(paragraph or "")
    text = re.sub(r"(?<=\])(?=\s+[A-Z0-9“])", "\n", text)
    parts = re.split(r"\n|(?<=[。！？!?])\s*|(?<=[；;])\s*", text)
    result = []
    for part in parts:
        if not part.strip():
            continue
        subs = SUB_SPLIT.split(part)
        result.extend(s.strip() for s in subs if s.strip())
    return result


def compact(text):
    text = nfkc(text)
    text = CITATION_RE.sub("", text)
    text = re.sub(r"【[0-9][0-9,\-–\s]*】", "", text)
    text = re.sub(r"[`*_>#]", "", text)
    return re.sub(r"[\s　]+", "", text).lower()


def trailing_reference_start(lines):
    nonempty = [i for i, line in enumerate(lines) if line.strip()]
    if not nonempty:
        return None
    start = None
    seen_entry = False
    for position in range(len(nonempty) - 1, -1, -1):
        index = nonempty[position]
        raw = lines[index]
        stripped = raw.strip()
        if REF_HEADING_RE.match(stripped):
            start = index
            break
        if REF_LINE_RE.match(stripped):
            seen_entry = True
            start = index
            continue
        if raw[:1].isspace() or stripped.lower().startswith(
                ("doi:", "doi ", "http://", "https://")):
            start = index
            continue
        break
    if not seen_entry or start is None:
        return None
    first = next((i for i in nonempty if i >= start), None)
    if first is None or not REF_LINE_RE.match(lines[first].strip()):
        return None
    return start


def reference_region_start(lines):
    for index, line in enumerate(lines):
        if REF_HEADING_RE.match(line.strip()):
            return index
    return trailing_reference_start(lines)


def body_text(text):
    lines = str(text or "").splitlines()
    start = reference_region_start(lines)
    if start is not None:
        lines = lines[:start]
    return "\n".join(lines)


def body_sentences_with_context(text):
    """Return ``{text, section, line}`` for every body sentence."""
    lines = str(text or "").splitlines()
    start = reference_region_start(lines)
    region = lines if start is None else lines[:start]
    out = []
    section = ""
    for idx, raw in enumerate(region, 1):
        line = raw.strip()
        if line.startswith("#"):
            section = line.lstrip("#").strip()
            continue
        if not line or line.startswith("|") or line.startswith("!["):
            continue
        if line.startswith("<!--"):
            continue
        stripped = strip_markdown(line)
        if not stripped:
            continue
        for sentence in split_sentences(stripped):
            if len(compact(sentence)) >= 2:
                out.append({"text": sentence, "section": section, "line": idx})
    return out


def body_sentences(text):
    return [item["text"] for item in body_sentences_with_context(text)]


def reference_entries(text):
    refs = {}
    lines = str(text or "").splitlines()
    start = reference_region_start(lines)
    if start is None:
        return refs
    for line in lines[start:]:
        stripped = line.strip()
        if REF_HEADING_RE.match(stripped):
            continue
        match = REF_LINE_RE.match(stripped)
        if match:
            refs[int(match.group(1))] = stripped
    return refs


def citation_numbers(text):
    numbers = set()
    for match in CITATION_RE.finditer(str(text or "")):
        for part in re.split(r"[\s,]+", match.group(1)):
            if not part:
                continue
            span = re.match(r"^(\d+)[\-–](\d+)$", part)
            if span:
                begin, end = int(span.group(1)), int(span.group(2))
                if 0 <= end - begin <= 20:
                    numbers.update(range(begin, end + 1))
                else:
                    numbers.update({begin, end})
            elif part.isdigit():
                numbers.add(int(part))
    return numbers


def citation_order(text):
    """First-appearance order of citation numbers in the body."""
    order = []
    for match in CITATION_RE.finditer(body_text(text)):
        for part in re.split(r"[\s,]+", match.group(1)):
            if part.isdigit():
                number = int(part)
                if number not in order:
                    order.append(number)
            else:
                span = re.match(r"^(\d+)[\-–](\d+)$", part)
                if span:
                    for number in range(int(span.group(1)),
                                        int(span.group(2)) + 1):
                        if number not in order:
                            order.append(number)
    return order


def strip_noncontent_numbers(text):
    text = CITATION_RE.sub(" ", str(text or ""))
    text = re.sub(r"(?:图|表|公式|式|第)\s*\d+(?:\.\d+)*\s*(?:节|章|页)?", " ", text)
    text = re.sub(
        r"\b(?:Fig(?:ure)?|Table|Eq(?:uation)?|Section|Sec\.)\s*\.?\s*"
        r"\d+(?:\.\d+)*",
        " ", text, flags=re.I)
    return text


def normalize_number(token):
    token = nfkc(str(token)).strip().lower().replace(" ", "").replace(",", "")
    if token.endswith("."):
        token = token[:-1]
    return token


def numbers_in(text):
    found = set()
    for match in NUM_RE.finditer(strip_noncontent_numbers(text)):
        token = normalize_number(match.group(0))
        if token:
            found.add(token)
    return found


def number_value(token):
    try:
        return float(str(token).rstrip("%"))
    except (TypeError, ValueError):
        return None


def number_traceable(token, candidates):
    token = normalize_number(token)
    if token in candidates:
        return True
    value = number_value(token)
    if value is None:
        return False
    for candidate in candidates:
        other = number_value(candidate)
        if other is not None and abs(value - other) <= 1e-9 * max(
                1.0, abs(value), abs(other)):
            return True
    return False


def count_words(text):
    cjk = len(CJK.findall(str(text or "")))
    latin = len(COUNT_WORD.findall(CJK.sub(" ", str(text or ""))))
    return cjk + latin


def detect_language(text):
    sample = str(text or "")[:20000]
    cjk = len(CJK.findall(sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    if cjk >= 20 and cjk >= latin * 0.15:
        return "zh"
    if latin >= 20:
        return "en"
    return "unknown"


def guess_genre(task_text, case_data=None):
    low = str(task_text or "").lower()
    contract = (case_data or {}).get("output_contract") or {}
    hint = str(contract.get("genre") or "").lower()
    if hint in {"review", "abstract", "discussion", "letter"}:
        return hint
    checks = [
        ("abstract", ["摘要", "abstract"]),
        ("discussion", ["讨论", "discussion"]),
        ("letter", ["letter", "书信", "信件"]),
        ("review", ["综述", "review", "进展", "研究现状"]),
    ]
    for genre, keys in checks:
        if any(key in low for key in keys):
            return genre
    return "review"


def hedge_in(text):
    return any(re.search(pattern, str(text or ""), re.I)
               for pattern in HEDGE_PATTERNS)


def limitation_in(text):
    low = str(text or "").lower()
    return any(marker in low for marker in LIMITATION_MARKERS)


def content_terms(text):
    """Matching terms: Latin words, CJK bigrams and bilingual concepts."""
    low = nfkc(text).lower()
    terms = set()
    for word in WORD.findall(low):
        if word not in STOPWORDS:
            terms.add(word)
    for run in CJK_RUN.findall(low):
        for index in range(len(run) - 1):
            terms.add(run[index:index + 2])
    for concept, zh_terms, en_terms in CONCEPTS:
        found = any(term in low for term in zh_terms)
        if not found:
            for term in en_terms:
                pattern = r"(?<![a-z])" + re.escape(term) + r"(?![a-z])"
                if re.search(pattern, low):
                    found = True
                    break
        if found:
            terms.add("c:" + concept)
    return terms


def extract_conditions(text):
    text = str(text or "")
    patterns = [
        r"\d+(?:\.\d+)?\s*(?:°\s*C|℃|K|bar|mbar|MPa|kPa|atm|h|min|s|mg|kg|"
        r"mL|L|mol|mmol|μmol|umol|nm|μm|um)\b",
        r"\bn\s*=\s*\d+",
        r"\d+\s*(?:名|例)\s*(?:成人|成年人|患者|受试者|参与者)?",
        r"95\s*%\s*CI\s*[^）)]{0,24}",
    ]
    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            value = normalize_space(match.group(0))
            if value and value not in found:
                found.append(value)
    return "; ".join(found)


def first_value_unit(text):
    """Pick the outcome value: prefer a percentage, else the first number.

    ``92% selectivity at 350 °C`` must yield value 92, not 350.  Conditions
    live in ``condition``; the value is the measured outcome.
    """
    cleaned = strip_noncontent_numbers(text)
    matches = list(NUM_RE.finditer(cleaned))
    if not matches:
        return None, "", ""
    chosen = next((match for match in matches
                   if normalize_number(match.group(0)).endswith("%")), matches[0])
    raw = normalize_number(chosen.group(0))
    if raw.endswith("%"):
        return raw[:-1], "%", raw
    tail = cleaned[chosen.end():chosen.end() + 12]
    unit_match = re.match(r"\s*(°\s*C|℃|K|bar|mbar|MPa|kPa|atm|h|min|s|mg|kg|"
                          r"mL|L|mol|mmol|μmol|umol|nm|μm|um)\b", tail, re.I)
    unit = normalize_space(unit_match.group(1)) if unit_match else ""
    if not unit:
        unit = "dimensionless"
    return raw, unit, raw


def extract_docx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        return None, "docx_unreadable: %s" % exc
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        return None, "docx_xml_invalid: %s" % exc
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(ns + "p"):
        parts = [node.text or "" for node in paragraph.iter(ns + "t")]
        joined = "".join(parts).strip()
        if joined:
            paragraphs.append(joined)
    return "\n\n".join(paragraphs), ""


def extract_pdf(path):
    tool = shutil.which("pdftotext")
    if not tool:
        return None, "pdf_tool_unavailable: install pdftotext or use the runtime PDF text tool"
    output = Path(tempfile.mkdtemp(prefix="pdf-text-")) / "out.txt"
    try:
        proc = subprocess.run([tool, "-layout", str(path), str(output)],
                              capture_output=True, text=True, timeout=120)
        if proc.returncode == 0 and output.is_file():
            return read_text(output), ""
        return None, "pdf_extract_failed: %s" % (proc.stderr or proc.stdout)[-400:]
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "pdf_extract_failed: %s" % exc


def title_from_text(text, fallback=""):
    for line in str(text or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped and not stripped.startswith((">", "<!--")):
            return stripped[:240]
    return fallback


def parse_bibliography(text):
    entries = {}
    lines = str(text or "").splitlines()
    start = reference_region_start(lines)
    if start is None:
        return entries
    for line in lines[start:]:
        stripped = line.strip()
        match = REF_LINE_RE.match(stripped)
        if match:
            entries[int(match.group(1))] = stripped
    return entries


def doi_in(text):
    match = DOI_RE.search(str(text or ""))
    return match.group(0).rstrip(".") if match else None


def year_in(text):
    match = YEAR_RE.search(str(text or ""))
    return int(match.group(0)) if match else None


def paragraph_units(text):
    """Locatable source units with line ranges (PDF form-feeds = pages)."""
    text = str(text or "")
    if not text.strip():
        return []
    units = []
    if "\f" in text:
        for page_no, page in enumerate(text.split("\f"), 1):
            for block in _page_blocks(page):
                units.append({"locator": "p.%d, %s" % (page_no, block["locator"]),
                              "text": block["text"], "kind": block["kind"]})
        return units
    lines = text.splitlines()
    start_region = reference_region_start(lines)
    limit = len(lines) if start_region is None else start_region
    buffer = []
    start_line = 0
    kind = "paragraph"

    def flush(end_line):
        nonlocal buffer, start_line, kind
        if not buffer:
            return
        joined = " ".join(item.strip() for item in buffer).strip()
        buffer = []
        if not joined:
            return
        units.append({
            "locator": "L%d-L%d" % (start_line, max(start_line, end_line)),
            "text": joined,
            "kind": kind,
        })

    in_fence = False
    for index, raw in enumerate(lines[:limit], 1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            flush(index - 1)
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            flush(index - 1)
            continue
        if stripped.startswith("#") or stripped.startswith("<!--"):
            flush(index - 1)
            continue
        if stripped.startswith(">"):
            flush(index - 1)
            continue
        if re.match(r"^([-*+]|\d+[.)])\s+", stripped):
            flush(index - 1)
            units.append({"locator": "L%d" % index,
                          "text": re.sub(r"^([-*+]|\d+[.)])\s+", "", stripped),
                          "kind": "list"})
            continue
        if re.match(r"^\|.*\|\s*$", stripped):
            flush(index - 1)
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if any(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            units.append({"locator": "L%d" % index,
                          "text": " | ".join(cells), "kind": "table_row"})
            continue
        if not buffer:
            start_line = index
            kind = "paragraph"
        buffer.append(raw)
    flush(min(len(lines), limit))
    return units


def _page_blocks(page):
    blocks = []
    start = 0
    buffer = []
    for index, raw in enumerate(page.splitlines(), 1):
        if raw.strip():
            if not buffer:
                start = index
            buffer.append(raw.strip())
        elif buffer:
            blocks.append({"locator": "L%d-L%d" % (start, index - 1),
                           "text": " ".join(buffer), "kind": "paragraph"})
            buffer = []
    if buffer:
        blocks.append({"locator": "L%d-L%d" % (start, start + len(buffer) - 1),
                       "text": " ".join(buffer), "kind": "paragraph"})
    return blocks


def ensure_reference_block(text, sources, cited_numbers):
    """Append honest material-locator stubs for cited sources lacking entries.

    Never invents authors, journals or DOIs.  A stub names the source title
    and its input path; real bibliographic metadata must come from the
    material itself.
    """
    existing = reference_entries(text)
    missing = [source for source in sources
               if isinstance(source.get("citation_number"), int)
               and source["citation_number"] in cited_numbers
               and source["citation_number"] not in existing]
    if not missing:
        return text, []
    additions = []
    for source in sorted(missing, key=lambda item: item["citation_number"]):
        entry = source.get("reference_entry") or (
            "%s. Synthetic/material locator: %s."
            % (source.get("title") or "Untitled material", source.get("path")))
        additions.append("[%d] %s" % (source["citation_number"], entry))
    lines = str(text).rstrip().splitlines()
    if reference_region_start(lines) is None:
        lines.extend(["", "## References", ""])
        lines.extend(additions)
    else:
        lines.extend(["", *additions])
    return "\n".join(lines).rstrip() + "\n", additions


def remove_sentences(text, sentences):
    """Remove exact body sentences while keeping headings, refs and layout."""
    targets = {normalize_space(item) for item in sentences if item}
    if not targets:
        return text
    lines = str(text).splitlines()
    start = reference_region_start(lines)
    limit = len(lines) if start is None else start
    out = []
    for index, raw in enumerate(lines):
        if index >= limit or not raw.strip():
            out.append(raw)
            continue
        line = raw
        for target in targets:
            if target in line:
                line = line.replace(target, "")
        line = re.sub(r"\s+([，。；：！？,.;:!?])", r"\1", line)
        line = re.sub(r"[ \t]{2,}", " ", line).rstrip()
        if line.strip() and line.strip() not in {"-", "*", "+"}:
            out.append(line)
    kept = lines[limit:] if start is not None else []
    if kept:
        if out and out[-1].strip():
            out.append("")
        out.extend(kept)
    return "\n".join(out).rstrip() + "\n"
