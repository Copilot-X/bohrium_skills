#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check a Chinese<->English academic translation against its source and contract.

Checks (group ids are used in the JSON report):
  G0 contract      required result.json fields, status, direction, case id
  G1 alignment     segment ids/count/order/source text; bilingual.md alignment
  G2 numbers       numeric values and measurement units per segment
  G3 citations     numeric/author-year citations and figure/table/equation refs
  G4 terms         locked glossary, forbidden variants, do-not-translate,
                   abbreviation first use, unresolved candidates
  G5 strength      negation and modal/claim-strength preservation
  G6 preservation  formulas, URLs/DOIs, inline code, placeholders, HTML tags
  G7 completeness  empty targets, added/omitted declarations, length ratio
  G8 backtranslation  coverage and deviation records
  G9 bilingual     segment ids and target text in bilingual.md
  G10 artifacts    artifact files exist, required outputs are present

The number/citation/strength checks are screening rules, not a substitute for a
human reviewer.  They deliberately err on the side of a warning/error with an
explicit evidence excerpt.

Usage:
    python3 check_translation.py \
        --source /input/source.md \
        --result /app/output/result.json \
        --glossary /app/output/glossary.json \
        --bilingual /app/output/bilingual.md \
        --candidates /app/work/glossary-candidates.json \
        --artifact-root /app/output \
        --report /app/output/translation-check.json --json

Exit codes: 0 no errors, 1 findings include errors, 2 unreadable/empty input.
The report is an *audit*, not the deliverable: in the v0.5 workflow a
non-zero exit never blocks delivery; record it and verify the flagged items
manually (see SKILL.md, step 4).
Stdlib only.
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

VERSION = "1.1.0"

PRESERVATION_KEYS = [
    "numbers_match", "units_match", "formulas_match",
    "citation_numbers_match", "figure_refs_match",
    "negation_match", "modality_match",
]

GROUPS = OrderedDict([
    ("contract", "G0 contract"),
    ("alignment", "G1 alignment"),
    ("numbers", "G2 numbers/units"),
    ("citations", "G3 citations/refs"),
    ("terms", "G4 terminology"),
    ("strength", "G5 negation/modality"),
    ("preservation", "G6 formulas/urls/code"),
    ("completeness", "G7 completeness"),
    ("backtranslation", "G8 back-translation"),
    ("bilingual", "G9 bilingual"),
    ("artifacts", "G10 artifacts"),
])

REQUIRED_RESULT_KEYS = [
    "case_id", "task_family", "status", "direction", "artifacts", "segments",
    "glossary", "preservation", "back_translation_check", "untranslated",
    "uncertainty", "change_log",
]

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+\S")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+)")
TABLE_RE = re.compile(r"^\s*\|")
MATH_START_RE = re.compile(
    r"^\s*(?:\$\$|\\\[|\\begin\{(?:equation|align|eqnarray|gather|multline)\*?\})"
)
MATH_ENV_RE = re.compile(r"\\begin\{([^}]+)\}")

SECTION_RE = re.compile(r"^#{2,4}\s+(S\d+)\b.*$", re.MULTILINE)

FORMULA_PATTERNS = [
    re.compile(r"\$\$.+?\$\$", re.S),
    re.compile(r"(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)", re.S),
    re.compile(r"\\\[.+?\\\]", re.S),
    re.compile(r"\\\(.+?\\\)", re.S),
    re.compile(
        r"\\begin\{(equation|align|eqnarray|gather|multline)\*?\}.+?\\end\{\1\*?\}",
        re.S,
    ),
]

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s<>\"']+", re.I)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
CODE_RE = re.compile(r"`[^`\n]+`")
PLACEHOLDER_RE = re.compile(
    r"\{[A-Za-z_][A-Za-z0-9_.\-]*\}|\{\{[^{}]+\}\}|%\d*\$?[sd]"
)
HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")

NUM_CITE_RE = re.compile(
    r"[\[［]\s*(\d{1,3}(?:\s*[,，–\-—]\s*\d{1,3})*)\s*[\]］]"
)
AUTHOR_YEAR_PAIR_RE = re.compile(
    r"([A-Z][A-Za-z'’\-]+)"
    r"(?:\s*(?:et\s+al\.?|等|and|&)\s*(?:[A-Z][A-Za-z'’\-]+)?)?"
    r"[，,]\s*(\d{4}[a-z]?)"
)
GROUPED_CITE_RE = re.compile(r"\(([^()]*\d{4}[a-z]?[^()]*)\)")
NARRATIVE_CITE_RE = re.compile(
    r"([A-Z][A-Za-z'’\-]+)\s+(?:et\s+al\.?|等)\s*\((\d{4}[a-z]?)\)"
)
# A bare "Name (2020)" narrative citation.  Capitalised words that are
# structural labels are excluded below so "Figure (2020)" is not a citation.
NARRATIVE_PLAIN_RE = re.compile(
    r"([A-Z][A-Za-z'’\-]+)\s+\((\d{4}[a-z]?)\)"
)
NON_AUTHOR_WORDS = {
    "figure", "fig", "table", "section", "sec", "equation", "eq", "reference",
    "references", "supplementary", "appendix", "data", "methods", "results",
    "discussion", "conclusion", "chapter", "scheme", "note",
}

FIG_RE = re.compile(
    r"(?:Fig(?:ure)?s?\.?|(?<![地意试蓝构])图)\s*"
    r"(S?[0-9]+[A-Za-z]?(?:\s*[–\-—~]\s*S?[0-9]+[A-Za-z]?)?)",
    re.I,
)
TAB_RE = re.compile(
    r"(?:Tables?|(?<![达示明征面述决演代])表)\s*"
    r"(S?[0-9]+[A-Za-z]?(?:\s*[–\-—~]\s*S?[0-9]+[A-Za-z]?)?)",
    re.I,
)
EQ_RE = re.compile(
    r"(?:Eq(?:uation)?s?\.?|公式|(?<![方形模正格样])式)\s*"
    r"[\(（]?\s*(S?[0-9]+[A-Za-z]?)\s*[\)）]?",
    re.I,
)
SEC_RE = re.compile(
    r"(?:Sec(?:tion)?\.?|(?<![调环季节目约奏细情关字])节)\s*"
    r"([0-9]+(?:\.[0-9]+)*)"
    r"|第\s*([0-9]+(?:\.[0-9]+)*)\s*节",
    re.I,
)

SCALE_VALUES = {
    "万": Decimal(10) ** 4,
    "亿": Decimal(10) ** 8,
    "thousand": Decimal(1000),
    "million": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9,
}
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
    r"(?P<scale>\s*(?:万|亿|thousand|million|billion))?"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)

# Physical units only.  Chinese measure words (个/次/项/...) are deliberately
# excluded because they need not survive verbatim in the target language.
UNIT_ALIASES = {
    "%": "%", "‰": "‰", "°C": "°C", "℃": "°C",
    "kg": "kg", "mg": "mg", "μg": "ug", "µg": "ug", "ug": "ug", "ng": "ng", "g": "g",
    "mol": "mol", "mmol": "mmol", "μmol": "umol", "µmol": "umol", "umol": "umol",
    "M": "M", "mM": "mM", "μM": "uM", "µM": "uM", "uM": "uM", "nM": "nM",
    "nm": "nm", "μm": "um", "µm": "um", "um": "um", "mm": "mm", "cm": "cm",
    "m": "m", "km": "km",
    "mL": "mL", "ml": "mL", "μL": "uL", "µL": "uL", "uL": "uL", "ul": "uL",
    "L": "L", "l": "L",
    "Pa": "Pa", "kPa": "kPa", "MPa": "MPa", "GPa": "GPa",
    "eV": "eV", "keV": "keV", "MeV": "MeV",
    "V": "V", "mV": "mV", "kV": "kV", "A": "A", "mA": "mA", "Ω": "ohm", "ohm": "ohm",
    "Hz": "Hz", "kHz": "kHz", "MHz": "MHz", "GHz": "GHz",
    "W": "W", "kW": "kW", "MW": "MW", "J": "J", "kJ": "kJ", "MJ": "MJ",
    "cal": "cal", "kcal": "kcal",
    "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
    "min": "min", "mins": "min", "minute": "min", "minutes": "min",
    "s": "s", "sec": "s", "second": "s", "seconds": "s",
    "ms": "ms", "ns": "ns", "ps": "ps",
    "K": "K", "bar": "bar", "atm": "atm", "Torr": "Torr", "rpm": "rpm",
    "Da": "Da", "kDa": "kDa", "ppm": "ppm", "ppb": "ppb",
    "wt%": "wt%", "at%": "at%", "vol%": "vol%",
    "years": "year", "year": "year", "days": "day", "day": "day",
    "weeks": "week", "week": "week",
}
_UNIT_KEYS = sorted(UNIT_ALIASES, key=len, reverse=True)
UNIT_RE = re.compile(r"\s*(" + "|".join(re.escape(u) for u in _UNIT_KEYS) + r")(?![A-Za-z0-9])")

MONTH_NUMBERS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5,
    "June": 6, "July": 7, "August": 8, "September": 9, "October": 10,
    "November": 11, "December": 12, "Sept": 9, "Jan": 1, "Feb": 2,
    "Mar": 3, "Apr": 4, "Jun": 6, "Jul": 7, "Aug": 8, "Sep": 9,
    "Oct": 10, "Nov": 11, "Dec": 12,
}
# Case-sensitive on purpose: lowercase "may" is a modal verb, not a month.
# "May" is handled separately and only when a digit follows ("May 2020"),
# because sentence-initial "May be ..." is a modal, not a date.
MONTH_RE = re.compile(
    r"\b(January|February|March|April|June|July|August|September|"
    r"October|November|December|Sept|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|"
    r"Nov|Dec)\.?(?![A-Za-z])"
)
MAY_DATE_RE = re.compile(r"\bMay\.?(?=\s+\d)")


def _join_cn_date(match):
    parts = [match.group(1), str(int(match.group(2)))]
    if match.group(3):
        parts.append(str(int(match.group(3))))
    return "-".join(parts)


def normalize_measure_text(text):
    """Normalise equivalent surface forms before numeric comparison.

    This removes formatting-only differences that are not translation defects:
    full-width digits, dates written as "2020 年 3 月" vs "March 2020",
    "24 小时" vs "24 h", "per cent" vs "%", and thin-space thousands
    separators.  Values themselves are never changed.
    """
    text = re.sub(r"[０-９]", lambda m: str(ord(m.group(0)) - ord("０")), text)
    text = (text.replace("％", "%").replace("．", ".").replace("，", ",")
                .replace("－", "-").replace("～", "~"))
    text = unicodedata.normalize("NFC", text)

    # Chinese dates -> numeric month/day so both languages produce the same
    # number set.  A bare "2020 年" keeps only the year.
    text = re.sub(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*[日号])?",
        _join_cn_date, text,
    )
    text = re.sub(r"(\d{4})\s*年", r"\1", text)
    text = MAY_DATE_RE.sub("5", text)
    text = MONTH_RE.sub(lambda m: str(MONTH_NUMBERS[m.group(1)]), text)
    text = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", text, flags=re.I)
    # Decades: "the 2020s" and "2020 年代" carry the same number.
    text = re.sub(r"\b(\d{4})s\b", r"\1", text)

    # Chinese temporal units -> canonical English unit symbols.
    text = re.sub(r"(?<=\d)\s*(?:小时|钟头)", " h", text)
    text = re.sub(r"(?<=\d)\s*分钟", " min", text)
    text = re.sub(r"(?<=\d)\s*毫秒", " ms", text)
    text = re.sub(r"(?<=\d)\s*(?:秒钟|秒)", " s", text)
    text = re.sub(r"(?<=\d)\s*天(?=$|[^一-鿿]|[内后前中左右的里])", " day", text)
    text = re.sub(r"(?<=\d)\s*周(?=$|[^一-鿿]|[内后前中左右的里])", " week", text)
    text = re.sub(r"(?<=\d)\s*年(?=$|[^一-鿿]|[内后前中左右的里间])", " year", text)

    text = re.sub(r"\bper\s*cent\b|\bpercent\b", "%", text, flags=re.I)
    text = re.sub(r"(?<=\d)[   ](?=\d{3}(?!\d))", ",", text)
    return text

NEG_CN = re.compile(
    r"不显著|不明显|不能|不可|未能|未见|未发现|未检测到|未观察到|未出现|"
    r"未达到|未满足|未发生|没有|无法|并非|并未|不含|不存在|无显著|无明显|"
    r"缺乏|缺失|不应|不再|尚未|无需|不等于|不支持|不构成|并不"
)
NEG_EN = re.compile(
    r"(?<![A-Za-z])(?:not|none|never|without|cannot|neither|nor|"
    r"fail(?:s|ed)?|absent|absence|lack(?:s|ed)?|unable|"
    r"no\s+(?:significant|difference|obvious|clear|evidence)|no\s+longer)(?![A-Za-z])",
    re.I,
)
NOT_ONLY_RE = re.compile(r"not\s+only|不仅|不但", re.I)

MODALITY = {
    "possibility": re.compile(
        r"(?<![A-Za-z])(?:may|might|could|possibly|perhaps)(?![A-Za-z])|可能|或许|也许|或将",
        re.I,
    ),
    "indication": re.compile(
        r"(?<![A-Za-z])(?:suggest(?:s|ed)?|indicat(?:e|es|ed)|appear(?:s|ed)?|"
        r"seem(?:s|ed)?|likely|probably)(?![A-Za-z])|提示|推测|似乎",
        re.I,
    ),
    "demonstration": re.compile(
        r"(?<![A-Za-z])(?:show(?:s|n|ed)?|demonstrat(?:e|es|ed)|find(?:s|ings?)?|"
        r"found|observ(?:e|es|ed))(?![A-Za-z])|显示|表明|发现|观察到|表现出",
        re.I,
    ),
    "proof": re.compile(
        r"(?<![A-Za-z])(?:prove(?:s|n|d)?|confirm(?:s|ed)?|establish(?:es|ed)?)"
        r"(?![A-Za-z])|证明|证实|确认",
        re.I,
    ),
    "obligation_high": re.compile(
        r"(?<![A-Za-z])(?:must|required|requires?|necessary)(?![A-Za-z])|必须|需要|必需|须|(?<!无)需",
        re.I,
    ),
    "obligation_mid": re.compile(
        r"(?<![A-Za-z])(?:should|ought)(?![A-Za-z])|应当|应该|理应|应予|"
        r"(?<![对响适相供反效])(?![用急])应(?=[在当于按采])",
        re.I,
    ),
}

CONTRACTION_RE = re.compile(
    r"(?<![A-Za-z])(?:[A-Za-z]+n't|(?:it|that|there|what|who|he|she|we|they)'s|"
    r"(?:I|you|we|they)'re|(?:I|you|we|they)'ve|(?:I|you|he|she|we|they)'ll|"
    r"(?:I|you|he|she|we|they)'d)(?![A-Za-z])",
    re.I,
)

PLACEHOLDER_TEXT_RE = re.compile(
    r"(TODO|TBD|FIXME|<placeholder>|待翻译|待补充|待填写)", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# text utilities
# ---------------------------------------------------------------------------

def read_text(path):
    return Path(path).read_text(encoding="utf-8-sig")


def load_json(path):
    return json.loads(read_text(path))


def norm_space(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def one_line(text):
    return re.sub(r"\s+", " ", text).strip()


def cjk_count(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def word_count(text):
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", text))


def excerpt(text, limit=90):
    text = one_line(text)
    return text if len(text) <= limit else text[:limit] + "…"


def dec_str(value):
    if value == value.to_integral_value():
        return format(value, "f").split(".")[0]
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# ---------------------------------------------------------------------------
# segmentation (kept in sync with segment_source.py)
# ---------------------------------------------------------------------------

def split_blocks(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
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
            blocks.append({
                "lines": list(buf),
                "line_start": (buf_start if buf_start is not None else 0) + 1,
                "line_end": end_idx,
            })
        buf = []
        buf_start = None

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            flush(i)
            i += 1
            continue
        if HEADING_RE.match(line):
            flush(i)
            blocks.append({"lines": [line.rstrip()], "line_start": i + 1, "line_end": i + 1})
            i += 1
            continue
        fence = FENCE_RE.match(line)
        if fence:
            flush(i)
            marker = fence.group(1)
            start = i
            buf = [line.rstrip("\n")]
            i += 1
            closing = re.compile(
                r"^\s*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$"
            )
            while i < n:
                buf.append(lines[i].rstrip("\n"))
                if closing.match(lines[i]):
                    i += 1
                    break
                i += 1
            blocks.append({"lines": list(buf), "line_start": start + 1, "line_end": i})
            buf = []
            buf_start = None
            continue
        if MATH_START_RE.match(line) and not buf:
            flush(i)
            start = i
            stripped = line.strip()
            closing = None
            if stripped.startswith("$$"):
                closing = "$$"
            elif stripped.startswith("\\["):
                closing = "\\]"
            else:
                env = MATH_ENV_RE.match(stripped)
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
            blocks.append({"lines": list(buf), "line_start": start + 1, "line_end": i})
            buf = []
            buf_start = None
            continue
        if not buf:
            buf_start = i
        buf.append(line.rstrip("\n"))
        i += 1
    flush(n)
    return blocks


def block_type(lines):
    first = lines[0].strip()
    if HEADING_RE.match(lines[0]):
        return "heading"
    if FENCE_RE.match(lines[0]):
        return "code"
    if first.startswith("$$") or first.startswith("\\[") or first.startswith("\\begin{"):
        return "formula"
    if TABLE_RE.match(lines[0]):
        return "table"
    if LIST_RE.match(lines[0]):
        return "list"
    return "paragraph"


def build_segments(text):
    segments = []
    for block in split_blocks(text):
        body = "\n".join(block["lines"]).strip("\n")
        if not body.strip():
            continue
        segments.append({
            "segment_id": "S%02d" % (len(segments) + 1),
            "type": block_type(block["lines"]),
            "line_start": block["line_start"],
            "line_end": block["line_end"],
            "source": body,
        })
    return segments


# ---------------------------------------------------------------------------
# span extraction / masking
# ---------------------------------------------------------------------------

def merge_spans(spans):
    spans = sorted(spans, key=lambda x: (x[0], x[1]))
    merged = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(s, e) for s, e in merged]


def mask_spans(text, spans):
    if not spans:
        return text
    chars = list(text)
    for start, end in merge_spans([(s, e) for s, e, _ in spans] if spans and len(spans[0]) == 3 else spans):
        for i in range(start, min(end, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def extract_formula_spans(text):
    spans = []
    for pattern in FORMULA_PATTERNS:
        for match in pattern.finditer(text):
            value = re.sub(r"\\bm(?![A-Za-z])", r"\\boldsymbol", match.group(0))
            spans.append((match.start(), match.end(), norm_space(value)))
    return spans


def extract_url_spans(text):
    spans = []
    for pattern in (URL_RE, DOI_RE, EMAIL_RE):
        for match in pattern.finditer(text):
            value = match.group(0).rstrip(".,;:!?")
            spans.append((match.start(), match.start() + len(value), value))
    return spans


def extract_code_spans(text):
    return [(m.start(), m.end(), m.group(0)) for m in CODE_RE.finditer(text)]


def extract_placeholder_spans(text):
    return [(m.start(), m.end(), m.group(0)) for m in PLACEHOLDER_RE.finditer(text)]


def extract_html_spans(text):
    return [(m.start(), m.end(), m.group(0)) for m in HTML_TAG_RE.finditer(text)]


def extract_numeric_cite_spans(text):
    spans = []
    for match in NUM_CITE_RE.finditer(text):
        inner = re.sub(r"\s+", "", match.group(1))
        inner = re.sub(r"[–—−]", "-", inner).replace("，", ",")
        spans.append((match.start(), match.end(), "num:[" + inner + "]"))
    return spans


def build_masked(text):
    """Return (masked_text, dict of extracted token counters)."""
    formula_spans = extract_formula_spans(text)
    masked = mask_spans(text, formula_spans)
    url_spans = extract_url_spans(masked)
    masked = mask_spans(masked, url_spans)
    code_spans = extract_code_spans(masked)
    masked = mask_spans(masked, code_spans)
    placeholder_spans = extract_placeholder_spans(masked)
    masked = mask_spans(masked, placeholder_spans)
    html_spans = extract_html_spans(masked)
    masked = mask_spans(masked, html_spans)
    cite_spans = extract_numeric_cite_spans(masked)
    masked = mask_spans(masked, cite_spans)

    tokens = {
        "formula": Counter(s[2] for s in formula_spans),
        "url": Counter(s[2] for s in url_spans),
        "code": Counter(s[2] for s in code_spans),
        "placeholder": Counter(s[2] for s in placeholder_spans),
        "html": Counter(s[2] for s in html_spans),
        "numeric_cite": Counter(s[2] for s in cite_spans),
    }
    return masked, tokens


# ---------------------------------------------------------------------------
# numbers / citations / references
# ---------------------------------------------------------------------------

def extract_numbers(text):
    text = normalize_measure_text(text)
    items = []
    for match in NUMBER_RE.finditer(text):
        raw = match.group("num")
        scale = (match.group("scale") or "").strip().lower()
        try:
            value = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            continue
        if scale in SCALE_VALUES:
            value *= SCALE_VALUES[scale]
        items.append({
            "value": dec_str(value),
            "unit": None,
            "start": match.start(),
            "end": match.end(),
            "raw": match.group(0),
        })
    for item in items:
        tail = text[item["end"]:item["end"] + 14]
        unit_match = UNIT_RE.match(tail)
        if unit_match:
            item["unit"] = UNIT_ALIASES.get(unit_match.group(1), unit_match.group(1))
    for i in range(len(items) - 1):
        if items[i]["unit"] is None:
            gap = text[items[i]["end"]:items[i + 1]["start"]]
            if re.fullmatch(r"\s*(?:[–\-—−~]|to|and|至|到|和|与)\s*", gap, re.I):
                items[i]["unit"] = items[i + 1]["unit"]
    return items


def extract_author_year(text):
    cites = Counter()
    # Full-width parentheses are the normal Chinese form for author-year
    # citations; comparing them with the ASCII form is a formatting change,
    # not a translation defect.
    text = text.replace("（", "(").replace("）", ")")

    def add(name, year):
        if name.lower() in NON_AUTHOR_WORDS or name in MONTH_NUMBERS:
            return
        cites["ay:%s:%s" % (name.lower(), year.lower())] += 1

    # One parenthesis may hold several citations:
    # (Smith et al., 2020; Zhang 等，2021).
    for group in GROUPED_CITE_RE.finditer(text):
        for match in AUTHOR_YEAR_PAIR_RE.finditer(group.group(1)):
            add(match.group(1), match.group(2))
    for match in NARRATIVE_CITE_RE.finditer(text):
        add(match.group(1), match.group(2))
    for match in NARRATIVE_PLAIN_RE.finditer(text):
        # Avoid double counting "Smith et al. (2020)": the plain pattern only
        # matches a single capitalised word immediately before the year.
        if NARRATIVE_CITE_RE.search(text[match.start():match.end() + 8]):
            continue
        add(match.group(1), match.group(2))
    return cites


def extract_refs(text):
    refs = Counter()

    def add(kind, value):
        value = re.sub(r"\s+", "", value)
        value = re.sub(r"[–—−~]", "-", value)
        refs[kind + ":" + value.lower()] += 1

    for match in FIG_RE.finditer(text):
        add("fig", match.group(1))
    for match in TAB_RE.finditer(text):
        add("tab", match.group(1))
    for match in EQ_RE.finditer(text):
        add("eq", match.group(1))
    for match in SEC_RE.finditer(text):
        add("sec", match.group(1) or match.group(2))
    return refs


# ---------------------------------------------------------------------------
# terminology / strength
# ---------------------------------------------------------------------------

def term_pattern(term, case_sensitive):
    if re.search(r"[A-Za-z]", term):
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", flags
        )
    return None


def term_occurs(term, text, case_sensitive=False):
    if not term:
        return False
    pattern = term_pattern(term, case_sensitive)
    if pattern is not None:
        return pattern.search(text) is not None
    return term in text


def first_index(term, text, case_sensitive=False):
    if not term:
        return None
    if not case_sensitive:
        idx = text.lower().find(term.lower())
    else:
        idx = text.find(term)
    return idx if idx >= 0 else None


def is_acronym(term):
    return bool(re.fullmatch(r"[A-Z][A-Z0-9\-]{1,9}", term or ""))


def negation_count(text):
    text = NOT_ONLY_RE.sub(" ", text)
    return len(NEG_CN.findall(text)) + len(NEG_EN.findall(text))


# "Figure 2a shows ..." / "表 1 汇总了 ..." are presentation verbs, not
# claim-strength cues.  Strip those clauses before the modality screen so a
# literal "图 2a 给出 → Figure 2a shows" is not scored as an escalation.
PRESENTATIONAL_RE = re.compile(
    r"(?:Fig(?:ure)?s?\.?|Tables?|Scheme|图|表)\s*S?\d+[A-Za-z]?"
    r"[^.;。；\n]{0,60}?"
    r"(?:show(?:s|ed|n|ing)?|present(?:s|ed)?|summari[sz](?:e|es|ed)|"
    r"display(?:s|ed)?|illustrat(?:e|es|ed)|列出|给出|显示|展示|汇总|总结|所示)"
    r"|(?:as\s+)?(?:show(?:n|s|ed)?|present(?:ed)?|illustrat(?:ed|es)?|"
    r"summari[sz]ed)\s+in\s+(?:Fig(?:ure)?s?\.?|Tables?|Scheme)\s*S?\d+[A-Za-z]?",
    re.I,
)


def modality_profile(text):
    text = PRESENTATIONAL_RE.sub(" ", text)
    return {name: len(pattern.findall(text)) for name, pattern in MODALITY.items()}


def strength_level(profile):
    if profile["proof"]:
        return 4
    if profile["demonstration"]:
        return 3
    if profile["indication"]:
        return 2
    if profile["possibility"]:
        return 1
    return 0


# ---------------------------------------------------------------------------
# checker
# ---------------------------------------------------------------------------

class Checker:
    def __init__(self):
        self.findings = []
        self._seen = set()

    def add(self, level, code, group, message, segment_id=None, source=None, target=None):
        key = (level, code, group, segment_id, message)
        if key in self._seen:
            return
        self._seen.add(key)
        self.findings.append({
            "level": level,
            "code": code,
            "group": group,
            "segment_id": segment_id,
            "message": message,
            "source_excerpt": excerpt(source) if source else "",
            "target_excerpt": excerpt(target) if target else "",
        })

    def error(self, code, group, message, **kwargs):
        self.add("error", code, group, message, **kwargs)

    def warning(self, code, group, message, **kwargs):
        self.add("warning", code, group, message, **kwargs)

    def group_status(self, group):
        levels = {f["level"] for f in self.findings if f["group"] == group}
        if "error" in levels:
            return "fail"
        if "warning" in levels:
            return "warn"
        return "pass"

    def count(self, level=None, group=None, prefix=None):
        total = 0
        for f in self.findings:
            if level and f["level"] != level:
                continue
            if group and f["group"] != group:
                continue
            if prefix and not f["code"].startswith(prefix):
                continue
            total += 1
        return total


def compare_counter(checker, code, group, src, tgt, label, seg_id, source, target):
    for value, count in sorted((src - tgt).items()):
        checker.error(
            code + ".missing", group,
            "%s missing from target: %s (x%d)" % (label, value, count),
            segment_id=seg_id, source=source, target=target,
        )
    for value, count in sorted((tgt - src).items()):
        checker.error(
            code + ".extra", group,
            "%s added in target: %s (x%d)" % (label, value, count),
            segment_id=seg_id, source=source, target=target,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=None, help="raw source document")
    parser.add_argument("--segments", default=None, help="precomputed segments.json")
    parser.add_argument("--result", required=True, help="result.json")
    parser.add_argument("--glossary", default=None, help="locked glossary.json")
    parser.add_argument("--bilingual", default=None, help="bilingual.md")
    parser.add_argument("--candidates", default=None, help="glossary-candidates.json")
    parser.add_argument("--artifact-root", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--emit-bilingual", dest="emit_bilingual", default=None,
                        help="write bilingual.md from result segments before checking")
    parser.add_argument("--direction", choices=["zh->en", "en->zh"], default=None)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()

    def fatal(message):
        payload = {"tool": "check_translation", "version": VERSION,
                   "status": "error", "error": message}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    # ---- load inputs -----------------------------------------------------
    if not args.source and not args.segments:
        return fatal("one of --source or --segments is required")

    source_segments = None
    if args.segments:
        try:
            seg_doc = load_json(args.segments)
        except (OSError, ValueError) as exc:
            return fatal("cannot read segments: %s" % exc)
        if not isinstance(seg_doc, dict) or not isinstance(seg_doc.get("segments"), list):
            return fatal("segments file must contain a 'segments' list")
        source_segments = seg_doc["segments"]
        source_label = args.segments
    else:
        try:
            source_text = read_text(args.source)
        except OSError as exc:
            return fatal("cannot read source: %s" % exc)
        if not source_text.strip():
            return fatal("source is empty")
        source_segments = build_segments(source_text)
        source_label = args.source

    if not source_segments:
        return fatal("no source segments found")

    clean_segments = []
    for idx, seg in enumerate(source_segments):
        if not isinstance(seg, dict) or not isinstance(seg.get("source"), str):
            return fatal("source segment %d is invalid" % idx)
        sid = seg.get("segment_id") or "S%02d" % (idx + 1)
        clean_segments.append({
            "segment_id": sid,
            "type": seg.get("type", "paragraph"),
            "source": seg["source"],
        })
    source_segments = clean_segments
    source_ids = [s["segment_id"] for s in source_segments]

    try:
        result = load_json(args.result)
    except (OSError, ValueError) as exc:
        return fatal("cannot read result.json: %s" % exc)
    if not isinstance(result, dict):
        return fatal("result.json must be an object")

    glossary = None
    if args.glossary:
        try:
            glossary = load_json(args.glossary)
        except (OSError, ValueError) as exc:
            return fatal("cannot read glossary: %s" % exc)
        if not isinstance(glossary, dict):
            return fatal("glossary.json must be an object")
    else:
        glossary = {}

    candidates = None
    if args.candidates:
        try:
            cand_doc = load_json(args.candidates)
            candidates = cand_doc.get("candidates") if isinstance(cand_doc, dict) else None
        except (OSError, ValueError) as exc:
            return fatal("cannot read candidates: %s" % exc)

    direction = args.direction or result.get("direction")
    if direction not in ("zh->en", "en->zh"):
        direction = "zh->en"
    source_lang, target_lang = ("zh", "en") if direction == "zh->en" else ("en", "zh")
    mode = result.get("mode", "translation")

    checker = Checker()

    # ---- G0 contract -----------------------------------------------------
    for key in REQUIRED_RESULT_KEYS:
        if key not in result:
            checker.error("result.missing_key", "contract",
                          "result.json is missing required key: %s" % key)
    for key in ("artifacts", "segments", "glossary", "back_translation_check",
                "untranslated", "uncertainty", "change_log"):
        if key in result and not isinstance(result[key], list):
            checker.error("result.type", "contract",
                          "result.%s must be a list" % key)

    case_id = result.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        checker.error("result.case_id", "contract", "case_id must be a non-empty string")
    elif args.case_id and case_id != args.case_id:
        checker.error("result.case_id", "contract",
                      "case_id %r does not match --case-id %r" % (case_id, args.case_id))

    task_family = result.get("task_family")
    if not isinstance(task_family, str) or not task_family:
        checker.error("result.task_family", "contract", "task_family is required")
    elif task_family not in ("translation", "academic-translation"):
        checker.warning("result.task_family", "contract",
                        "unexpected task_family: %s" % task_family)

    status = result.get("status")
    if status not in ("completed", "partial", "input_error"):
        checker.error("result.status", "contract",
                      "status must be 'completed', 'partial' or 'input_error', got %r"
                      % status)
    if mode not in ("translation", "polish"):
        checker.warning("result.mode", "contract",
                        "unexpected mode %r; expected translation|polish" % mode)

    result_direction = result.get("direction")
    if result_direction not in ("zh->en", "en->zh"):
        checker.error("result.direction", "contract",
                      "direction must be 'zh->en' or 'en->zh'")
    elif args.direction and result_direction != args.direction:
        checker.error("result.direction", "contract",
                      "direction %s does not match --direction %s"
                      % (result_direction, args.direction))

    res_segments = result.get("segments") if isinstance(result.get("segments"), list) else []
    res_by_id = {}
    res_ids = []
    for idx, seg in enumerate(res_segments):
        if not isinstance(seg, dict):
            checker.error("segment.type", "alignment",
                          "result.segments[%d] is not an object" % idx)
            continue
        sid = seg.get("segment_id")
        if not isinstance(sid, str) or not sid:
            checker.error("segment.id", "alignment",
                          "result.segments[%d] has no segment_id" % idx)
            continue
        if sid in res_by_id:
            checker.error("segment.duplicate", "alignment",
                          "duplicate result segment id: %s" % sid)
        res_by_id[sid] = seg
        res_ids.append(sid)
        for key in ("source", "target"):
            if not isinstance(seg.get(key), str):
                checker.error("segment.field", "alignment",
                              "%s is missing string field %r" % (sid, key))
        for key in ("terms", "added", "omitted", "flags"):
            if key in seg and not isinstance(seg[key], list):
                checker.error("segment.field", "alignment",
                              "%s field %r must be a list" % (sid, key))
        confidence = seg.get("confidence")
        if confidence not in ("high", "medium", "low"):
            checker.warning("segment.confidence", "alignment",
                            "%s has non-standard confidence %r" % (sid, confidence))

    # ---- G1 alignment ----------------------------------------------------
    if len(res_segments) != len(source_segments):
        checker.error("segment.count", "alignment",
                      "source has %d segments, result has %d"
                      % (len(source_segments), len(res_segments)))
    for sid in source_ids:
        if sid not in res_by_id:
            checker.error("segment.missing", "alignment",
                          "result is missing source segment %s" % sid)
    for sid in res_ids:
        if sid not in source_ids:
            checker.error("segment.extra", "alignment",
                          "result has unknown segment %s" % sid)
    if not res_ids == source_ids and set(res_ids) == set(source_ids):
        checker.error("segment.order", "alignment",
                      "result segment order differs from source order")

    for item in source_segments:
        sid = item["segment_id"]
        seg = res_by_id.get(sid)
        if not seg:
            continue
        if norm_space(seg.get("source", "")) != norm_space(item["source"]):
            checker.error("segment.source_mismatch", "alignment",
                          "%s source text differs from the source document" % sid,
                          segment_id=sid, source=item["source"],
                          target=seg.get("source", ""))

    # ---- untranslated map ------------------------------------------------
    untranslated_ids = set()
    for item in result.get("untranslated", []) or []:
        if not isinstance(item, dict):
            checker.warning("untranslated.type", "contract",
                            "untranslated entry is not an object")
            continue
        sid = item.get("segment_id")
        if sid not in source_ids:
            checker.error("untranslated.unknown", "completeness",
                          "untranslated references unknown segment %r" % sid)
            continue
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            checker.warning("untranslated.reason", "completeness",
                            "%s untranslated entry has no reason" % sid)
        untranslated_ids.add(sid)

    if status == "completed" and untranslated_ids:
        checker.error("result.completed_untranslated", "contract",
                      "status=completed but untranslated is not empty")
    if (status in ("partial", "input_error") and not untranslated_ids
            and not (result.get("uncertainty") or [])):
        checker.error("result.partial_unexplained", "contract",
                      "status=%s requires untranslated or uncertainty records" % status)

    # ---- per-segment fidelity checks ------------------------------------
    numbers_ok = True
    units_ok = True
    formulas_ok = True
    citations_ok = True
    refs_ok = True
    negation_ok = True
    modality_ok = True

    terms = glossary.get("terms") if isinstance(glossary.get("terms"), list) else []
    terms_by_source = {}
    for term in terms:
        if not isinstance(term, dict):
            checker.error("glossary.schema", "terms", "glossary term is not an object")
            continue
        src_term = term.get("source")
        tgt_term = term.get("target")
        if not isinstance(src_term, str) or not src_term.strip():
            checker.error("glossary.schema", "terms", "glossary term has no source")
            continue
        if not isinstance(tgt_term, str) or not tgt_term.strip():
            checker.error("glossary.schema", "terms",
                          "glossary term %r has no target" % src_term)
            continue
        if src_term in terms_by_source:
            checker.error("glossary.duplicate", "terms",
                          "duplicate glossary source: %s" % src_term)
        terms_by_source[src_term] = term
        if "locked" in term and not isinstance(term["locked"], bool):
            checker.warning("glossary.schema", "terms",
                            "term %r has non-boolean locked flag" % src_term)
        for key in ("aliases", "forbidden"):
            if key in term and not isinstance(term[key], list):
                checker.warning("glossary.schema", "terms",
                                "term %r field %r must be a list" % (src_term, key))

    if not terms:
        checker.warning("glossary.empty", "terms",
                        "glossary.json contains no terms; terminology was not locked")

    register = glossary.get("register") if isinstance(glossary.get("register"), dict) else None
    if register is None:
        checker.warning("register.missing", "terms",
                        "glossary.register is missing; genre/formality were not recorded")
    else:
        for key in ("genre", "formality"):
            if not register.get(key):
                checker.warning("register.field", "terms",
                                "glossary.register.%s is empty" % key)
        avoid = register.get("avoid")
        if isinstance(avoid, list):
            for phrase in avoid:
                if isinstance(phrase, str) and phrase:
                    for item in source_segments:
                        seg = res_by_id.get(item["segment_id"], {})
                        target = seg.get("target", "") if isinstance(seg, dict) else ""
                        if term_occurs(phrase, target, False):
                            checker.warning("register.avoid", "terms",
                                            "target uses register.avoid phrase %r" % phrase,
                                            segment_id=item["segment_id"], target=target)
        if register.get("formality") == "academic" and target_lang == "en":
            for item in source_segments:
                seg = res_by_id.get(item["segment_id"], {})
                target = seg.get("target", "") if isinstance(seg, dict) else ""
                if CONTRACTION_RE.search(target):
                    checker.warning("register.contraction", "terms",
                                    "academic target contains a contraction",
                                    segment_id=item["segment_id"], target=target)

    for item in source_segments:
        sid = item["segment_id"]
        if sid not in res_by_id:
            # Alignment already reports the missing segment; do not turn one
            # structural defect into a cascade of per-segment errors.
            continue
        seg = res_by_id.get(sid, {})
        source = item["source"]
        target = seg.get("target", "") if isinstance(seg, dict) else ""
        if sid in untranslated_ids:
            continue

        masked_src, src_tokens = build_masked(source)
        masked_tgt, tgt_tokens = build_masked(target)

        # formulas / urls / code / placeholders / html
        for token_key, code_prefix, label in (
            ("formula", "formula", "formula"),
            ("url", "preserve.url", "URL/DOI/email"),
            ("code", "preserve.code", "inline code"),
            ("placeholder", "preserve.placeholder", "placeholder"),
            ("html", "preserve.html", "HTML tag"),
        ):
            compare_counter(checker, code_prefix, "preservation",
                            src_tokens[token_key], tgt_tokens[token_key],
                            label, sid, source, target)
        if "\\bm" in target and "\\bm" not in source:
            checker.warning("formula.bm", "preservation",
                            "target uses \\bm; replace with \\boldsymbol",
                            segment_id=sid, target=target)

        # numbers and units
        src_numbers = extract_numbers(masked_src)
        tgt_numbers = extract_numbers(masked_tgt)
        compare_counter(
            checker, "number", "numbers",
            Counter(n["value"] for n in src_numbers),
            Counter(n["value"] for n in tgt_numbers),
            "numeric value", sid, source, target,
        )
        compare_counter(
            checker, "unit", "numbers",
            Counter(n["unit"] for n in src_numbers if n["unit"]),
            Counter(n["unit"] for n in tgt_numbers if n["unit"]),
            "unit", sid, source, target,
        )

        # citations and cross references
        compare_counter(
            checker, "citation", "citations",
            src_tokens["numeric_cite"], tgt_tokens["numeric_cite"],
            "numeric citation", sid, source, target,
        )
        compare_counter(
            checker, "citation.author_year", "citations",
            extract_author_year(masked_src), extract_author_year(masked_tgt),
            "author-year citation", sid, source, target,
        )
        compare_counter(
            checker, "ref", "citations",
            extract_refs(masked_src), extract_refs(masked_tgt),
            "figure/table/equation/section reference", sid, source, target,
        )

        # locked terminology
        for src_term, term in terms_by_source.items():
            if not term_occurs(src_term, source, term.get("case_sensitive", is_acronym(src_term))):
                continue
            locked = term.get("locked", True)
            level = "error" if locked else "warning"
            case_sensitive = term.get("case_sensitive", is_acronym(src_term))
            variants = [term.get("target")] + list(term.get("aliases") or [])
            found = any(
                isinstance(v, str) and term_occurs(
                    v, target, case_sensitive or is_acronym(v)
                )
                for v in variants
            )
            if not found:
                checker.add(
                    level, "term.missing", "terms",
                    "locked term %r has no approved target form in %s" % (src_term, sid)
                    if locked else
                    "tentative term %r has no target form in %s" % (src_term, sid),
                    segment_id=sid, source=source, target=target,
                )
            for bad in term.get("forbidden") or []:
                if isinstance(bad, str) and term_occurs(bad, target, case_sensitive):
                    checker.add(
                        level, "term.forbidden", "terms",
                        "target uses forbidden variant %r for %r" % (bad, src_term),
                        segment_id=sid, source=source, target=target,
                    )

        # do-not-translate tokens
        for item_dnt in glossary.get("do_not_translate", []) or []:
            text = item_dnt.get("text") if isinstance(item_dnt, dict) else item_dnt
            if not isinstance(text, str) or not text:
                continue
            if term_occurs(text, source, True) and not term_occurs(text, target, True):
                checker.error(
                    "dnt.missing", "terms",
                    "do-not-translate token %r disappeared from target" % text,
                    segment_id=sid, source=source, target=target,
                )

        # negation and modality
        src_neg = negation_count(masked_src)
        tgt_neg = negation_count(masked_tgt)
        if src_neg and not tgt_neg:
            checker.error("negation.missing", "strength",
                          "source is negative but target has no negation cue",
                          segment_id=sid, source=source, target=target)
        elif tgt_neg and not src_neg:
            checker.error("negation.added", "strength",
                          "target adds a negation cue absent from source",
                          segment_id=sid, source=source, target=target)
        elif src_neg and tgt_neg and src_neg != tgt_neg:
            checker.warning("negation.count", "strength",
                            "negation cue count differs (%d -> %d)" % (src_neg, tgt_neg),
                            segment_id=sid, source=source, target=target)

        src_profile = modality_profile(masked_src)
        tgt_profile = modality_profile(masked_tgt)
        src_level = strength_level(src_profile)
        tgt_level = strength_level(tgt_profile)
        if tgt_level > src_level + 1:
            checker.error("modality.upgrade", "strength",
                          "claim strength escalated (%d -> %d)" % (src_level, tgt_level),
                          segment_id=sid, source=source, target=target)
        elif tgt_level > src_level and src_level > 0:
            checker.warning("modality.upgrade", "strength",
                            "claim strength increased (%d -> %d)" % (src_level, tgt_level),
                            segment_id=sid, source=source, target=target)
        elif tgt_level < src_level and tgt_level > 0:
            checker.warning("modality.downgrade", "strength",
                            "claim strength decreased (%d -> %d)" % (src_level, tgt_level),
                            segment_id=sid, source=source, target=target)
        elif src_level > 0 and tgt_level == 0:
            checker.warning("modality.dropped", "strength",
                            "claim-strength cue dropped", segment_id=sid,
                            source=source, target=target)
        elif src_level == 0 and tgt_level > 0:
            checker.warning("modality.new", "strength",
                            "target adds a claim-strength cue", segment_id=sid,
                            source=source, target=target)

        if src_profile["obligation_high"] and not tgt_profile["obligation_high"]:
            checker.warning("modality.obligation", "strength",
                            "high-obligation cue (must/required/必须) was not preserved",
                            segment_id=sid, source=source, target=target)
        elif not src_profile["obligation_high"] and tgt_profile["obligation_high"]:
            checker.warning("modality.obligation", "strength",
                            "target adds a high-obligation cue", segment_id=sid,
                            source=source, target=target)
        elif (src_profile["obligation_mid"] and tgt_profile["obligation_high"]
              and not src_profile["obligation_high"]):
            checker.warning("modality.obligation", "strength",
                            "obligation escalated (should -> must)", segment_id=sid,
                            source=source, target=target)

        # completeness
        if not target.strip():
            checker.error("segment.empty_target", "completeness",
                          "%s has an empty target and is not in untranslated" % sid,
                          segment_id=sid, source=source)
        else:
            src_norm = norm_space(source)
            tgt_norm = norm_space(target)
            if (item.get("type") == "paragraph" and len(src_norm) >= 20
                    and src_norm == tgt_norm):
                checker.warning("segment.target_copied", "completeness",
                                "%s target is identical to the source" % sid,
                                segment_id=sid, source=source, target=target)
            if item.get("type") in ("paragraph", "list", "table"):
                if direction == "zh->en":
                    zh = cjk_count(source)
                    en = word_count(target)
                    if zh >= 10 and (en / max(zh, 1) < 0.15 or en / max(zh, 1) > 2.5):
                        checker.warning("length.ratio", "completeness",
                                        "target/source length ratio looks implausible (%.2f words/char)"
                                        % (en / max(zh, 1)),
                                        segment_id=sid, source=source, target=target)
                else:
                    en = word_count(source)
                    zh = cjk_count(target)
                    if en >= 8 and (zh / max(en, 1) < 0.4 or zh / max(en, 1) > 5.0):
                        checker.warning("length.ratio", "completeness",
                                        "target/source length ratio looks implausible (%.2f chars/word)"
                                        % (zh / max(en, 1)),
                                        segment_id=sid, source=source, target=target)
        for field, code in (("added", "addition.declared"), ("omitted", "omission.declared")):
            values = seg.get(field) if isinstance(seg, dict) else None
            if isinstance(values, list) and values:
                note = seg.get("note") if isinstance(seg, dict) else None
                flags = seg.get("flags") if isinstance(seg, dict) else None
                if not (isinstance(note, str) and note.strip()) and not (isinstance(flags, list) and flags):
                    checker.warning(code, "completeness",
                                    "%s lists %s but gives no note/flags"
                                    % (sid, field), segment_id=sid,
                                    source=source, target=target)

    # ---- global locked-term coverage ------------------------------------
    full_target = "\n".join(
        res_by_id[sid].get("target", "") for sid in res_ids
        if isinstance(res_by_id.get(sid), dict)
    )
    for src_term, term in terms_by_source.items():
        if not term.get("locked", True):
            continue
        if not any(term_occurs(src_term, s["source"], term.get("case_sensitive", is_acronym(src_term)))
                   for s in source_segments):
            continue
        case_sensitive = term.get("case_sensitive", is_acronym(src_term))
        variants = [term.get("target")] + list(term.get("aliases") or [])
        if not any(isinstance(v, str) and term_occurs(v, full_target, case_sensitive or is_acronym(v))
                   for v in variants):
            checker.error("term.missing_global", "terms",
                          "locked term %r never appears in the target document" % src_term)

    # ---- glossary consistency between file and result --------------------
    file_map = {
        src: term.get("target") for src, term in terms_by_source.items()
        if term.get("locked", True)
    }
    res_glossary = result.get("glossary") if isinstance(result.get("glossary"), list) else []
    res_map = {}
    for item in res_glossary:
        if not isinstance(item, dict):
            checker.warning("result.glossary_type", "terms",
                            "result.glossary entry is not an object")
            continue
        src_term = item.get("source")
        tgt_term = item.get("target")
        if not isinstance(src_term, str) or not isinstance(tgt_term, str):
            checker.warning("result.glossary_type", "terms",
                            "result.glossary entry lacks source/target")
            continue
        if src_term in res_map and res_map[src_term] != tgt_term:
            checker.error("result.glossary_mismatch", "terms",
                          "result.glossary has two targets for %r" % src_term)
        res_map[src_term] = tgt_term
        if src_term not in file_map:
            checker.warning("result.glossary_extra", "terms",
                            "result.glossary term %r is not locked in glossary.json" % src_term)
        elif file_map[src_term] != tgt_term:
            checker.error("result.glossary_mismatch", "terms",
                          "result.glossary target for %r differs from glossary.json"
                          % src_term)
    for src_term in file_map:
        if src_term not in res_map:
            checker.error("result.glossary_missing", "terms",
                          "locked term %r is missing from result.glossary" % src_term)

    # ---- abbreviation first use -----------------------------------------
    abbreviations = glossary.get("abbreviations")
    if isinstance(abbreviations, list):
        for abbr in abbreviations:
            if not isinstance(abbr, dict):
                continue
            short = abbr.get("short")
            full = abbr.get("full_target")
            if not isinstance(short, str) or not isinstance(full, str):
                checker.warning("glossary.schema", "terms",
                                "abbreviation needs short and full_target")
                continue
            if abbr.get("expand_on_first_use", True) is False:
                continue
            pos_short = first_index(short, full_target, True)
            pos_full = first_index(full, full_target, False)
            if pos_short is None:
                continue
            if pos_full is None:
                checker.warning("abbr.not_expanded", "terms",
                                "abbreviation %r is never expanded in target" % short)
            elif pos_short < pos_full:
                checker.warning("abbr.use_before_expansion", "terms",
                                "abbreviation %r is used before its full form" % short)

    # ---- unresolved glossary candidates ---------------------------------
    if isinstance(candidates, list):
        glossary_strings = []
        for src_term, term in terms_by_source.items():
            candidate_strings = [src_term, term.get("target")] + list(term.get("aliases") or [])
            for value in candidate_strings:
                if isinstance(value, str) and len(value) >= 2:
                    glossary_strings.append(value.casefold())
        warned = 0
        for cand in candidates:
            if warned >= 20 or not isinstance(cand, dict):
                break
            term_text = cand.get("term")
            confidence = cand.get("confidence")
            if not isinstance(term_text, str) or confidence not in ("high", "medium"):
                continue
            term_key = term_text.casefold()
            covered = any(term_key in s or s in term_key for s in glossary_strings)
            if covered:
                continue
            if any(term_occurs(term_text, s["source"], True) for s in source_segments):
                checker.warning("term.unlocked_candidate", "terms",
                                "candidate term %r appears in the source but is not locked"
                                % term_text)
                warned += 1

    # ---- result artifact checks -----------------------------------------
    root = Path(args.artifact_root) if args.artifact_root else Path(args.result).resolve().parent
    artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
    artifact_names = set()
    for idx, art in enumerate(artifacts):
        if not isinstance(art, dict):
            checker.error("artifact.type", "artifacts",
                          "artifacts[%d] is not an object" % idx)
            continue
        path = art.get("path")
        if not isinstance(path, str) or not path:
            checker.error("artifact.path", "artifacts",
                          "artifacts[%d] has no path" % idx)
            continue
        artifact_names.add(Path(path).name)
        full = Path(path)
        if not full.is_absolute():
            full = root / full
        if not full.exists():
            checker.error("artifact.missing", "artifacts",
                          "artifact does not exist: %s" % full)
        elif full.is_file() and full.stat().st_size == 0:
            checker.error("artifact.empty", "artifacts",
                          "artifact is empty: %s" % full)

    if status == "input_error":
        # No translation was attempted; nothing below is a delivery failure.
        required_artifacts = set()
        recommended_artifacts = set()
    elif mode == "translation":
        # v0.5 deliverable-first contract: the translated text and the
        # segment-aligned bilingual file are the only hard requirements.
        required_artifacts = {"translation.md", "bilingual.md"}
        recommended_artifacts = {
            "glossary.json", "translation-notes.json", "translation-report.md",
        }
    else:
        required_artifacts = {"revision.md"}
        recommended_artifacts = {"translation-notes.json", "translation-report.md"}
    for name in sorted(required_artifacts):
        if name not in artifact_names:
            checker.error("artifact.required", "artifacts",
                          "required artifact is not declared in result.artifacts: %s" % name)
    for name in sorted(recommended_artifacts):
        if name not in artifact_names:
            checker.warning("artifact.recommended", "artifacts",
                            "recommended artifact not declared: %s" % name)

    def artifact_path(name):
        for art in artifacts:
            if isinstance(art, dict) and Path(str(art.get("path", ""))).name == name:
                path = Path(art["path"])
                return path if path.is_absolute() else root / path
        return None

    translation_path = artifact_path("translation.md")
    if translation_path and translation_path.exists():
        try:
            translation_text = read_text(translation_path)
            if PLACEHOLDER_TEXT_RE.search(translation_text):
                checker.warning("artifact.placeholder", "artifacts",
                                "translation.md still contains placeholder text")
        except OSError as exc:
            checker.warning("artifact.unreadable", "artifacts",
                            "cannot read translation.md: %s" % exc)

    notes_path = artifact_path("translation-notes.json")
    if notes_path and notes_path.exists():
        try:
            notes = load_json(notes_path)
            if not isinstance(notes, dict):
                checker.warning("artifact.notes_type", "artifacts",
                                "translation-notes.json is not an object")
        except (OSError, ValueError) as exc:
            checker.error("artifact.notes_json", "artifacts",
                          "translation-notes.json is not valid JSON: %s" % exc)

    # ---- back translation ------------------------------------------------
    bt = result.get("back_translation_check") if isinstance(result.get("back_translation_check"), list) else []
    valid_bt = []
    for item in bt:
        if not isinstance(item, dict):
            checker.warning("backtranslation.type", "backtranslation",
                            "back_translation_check entry is not an object")
            continue
        sid = item.get("segment_id")
        if sid not in source_ids:
            checker.error("backtranslation.segment", "backtranslation",
                          "back translation references unknown segment %r" % sid)
            continue
        back = item.get("back")
        deviation = item.get("deviation")
        if not isinstance(back, str) or not back.strip():
            checker.warning("backtranslation.back", "backtranslation",
                            "%s back translation is empty" % sid)
            continue
        if deviation not in ("none", "minor", "major"):
            checker.warning("backtranslation.deviation", "backtranslation",
                            "%s deviation must be none|minor|major" % sid)
            continue
        valid_bt.append(item)
        if deviation == "major":
            checker.add(
                "error" if status == "completed" else "warning",
                "backtranslation.major", "backtranslation",
                "%s back translation still has a major deviation" % sid,
                segment_id=sid,
            )
    # v0.5: back translation is an optional spot check (2-3 high-risk
    # segments), not a delivery gate.  Missing samples are a warning; a
    # reported ``major`` deviation still blocks status=completed.
    recommended_bt = 3 if len(source_segments) >= 6 else (1 if len(source_segments) >= 2 else 0)
    if status != "input_error" and len(valid_bt) < recommended_bt:
        checker.warning("backtranslation.missing", "backtranslation",
                        "optional spot check not performed: %d of %d recommended "
                        "segments back-translated" % (len(valid_bt), recommended_bt))
    elif len(source_segments) >= 3 and len(valid_bt) < min(3, len(source_segments)):
        checker.warning("backtranslation.coverage", "backtranslation",
                        "only %d back-translation checks; 3 are recommended"
                        % len(valid_bt))

    # ---- bilingual.md ----------------------------------------------------
    bilingual_path = args.bilingual
    if args.emit_bilingual:
        lines = ["# Bilingual Alignment", "", "<!-- generated by check_translation.py -->", ""]
        for sid in res_ids:
            seg = res_by_id.get(sid, {})
            lines.append("## %s" % sid)
            lines.append("")
            lines.append("- **Source (%s):** %s" % (source_lang, one_line(seg.get("source", ""))))
            lines.append("- **Target (%s):** %s" % (target_lang, one_line(seg.get("target", ""))))
            lines.append("")
        emit_path = Path(args.emit_bilingual)
        emit_path.parent.mkdir(parents=True, exist_ok=True)
        emit_path.write_text("\n".join(lines), encoding="utf-8")
        if not bilingual_path:
            bilingual_path = str(emit_path)

    if bilingual_path:
        try:
            bilingual_text = read_text(bilingual_path)
        except OSError as exc:
            checker.error("bilingual.missing", "bilingual",
                          "cannot read bilingual.json/md: %s" % exc)
            bilingual_text = ""
        if bilingual_text:
            sections = []
            matches = list(SECTION_RE.finditer(bilingual_text))
            for i, match in enumerate(matches):
                end = matches[i + 1].start() if i + 1 < len(matches) else len(bilingual_text)
                sections.append((match.group(1), bilingual_text[match.end():end]))
            section_ids = [sid for sid, _ in sections]
            if len(set(section_ids)) != len(section_ids):
                checker.error("bilingual.duplicate", "bilingual",
                              "bilingual.md contains duplicate segment headings")
            for sid in res_ids:
                if sid not in section_ids:
                    checker.error("bilingual.missing_id", "bilingual",
                                  "bilingual.md is missing segment %s" % sid)
            for sid in section_ids:
                if sid not in res_ids:
                    checker.error("bilingual.extra_id", "bilingual",
                                  "bilingual.md has unknown segment %s" % sid)
            if len(section_ids) == len(res_ids) and section_ids != res_ids:
                checker.error("bilingual.order", "bilingual",
                              "bilingual.md segment order differs from result.json")
            for sid, section in sections:
                seg = res_by_id.get(sid, {})
                target = seg.get("target", "") if isinstance(seg, dict) else ""
                if not target.strip():
                    continue
                needle = norm_space(target)[:40]
                if needle and needle not in norm_space(section):
                    checker.error("bilingual.target_mismatch", "bilingual",
                                  "%s target text is not present under its heading" % sid,
                                  segment_id=sid, target=target)

    # ---- preservation claims --------------------------------------------
    computed = {
        "numbers_match": checker.count("error", prefix="number.") == 0,
        "units_match": checker.count("error", prefix="unit.") == 0,
        "formulas_match": checker.count("error", prefix="formula.") == 0,
        "citation_numbers_match": checker.count("error", prefix="citation.") == 0,
        "figure_refs_match": checker.count("error", prefix="ref.") == 0,
        "negation_match": checker.count("error", prefix="negation.") == 0,
        # Warnings are advisory cue-level observations; only a detected
        # error contradicts a preservation=true claim.
        "modality_match": checker.count("error", prefix="modality.") == 0,
    }
    claimed = result.get("preservation")
    if not isinstance(claimed, dict):
        checker.error("result.preservation", "contract",
                      "preservation must be an object")
        claimed = {}
    for key in PRESERVATION_KEYS:
        if key not in claimed:
            checker.error("result.preservation_missing", "contract",
                          "preservation.%s is missing" % key)
            continue
        if not isinstance(claimed[key], bool):
            checker.error("result.preservation_type", "contract",
                          "preservation.%s must be a boolean" % key)
            continue
        if computed[key] is False and claimed[key] is True:
            checker.error("result.preservation_claim", "contract",
                          "result claims preservation.%s=true but the check failed" % key)
        elif computed[key] is True and claimed[key] is False:
            checker.warning("result.preservation_underclaim", "contract",
                            "result claims preservation.%s=false but no finding was detected" % key)

    # ---- report ----------------------------------------------------------
    errors = checker.count("error")
    warnings = checker.count("warning")
    exit_code = 1 if errors else 0
    checks = []
    for group, label in GROUPS.items():
        checks.append({
            "id": label.split()[0],
            "name": label.split(" ", 1)[1],
            "group": group,
            "status": checker.group_status(group),
            "errors": checker.count("error", group=group),
            "warnings": checker.count("warning", group=group),
        })

    report = {
        "tool": "check_translation",
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "fail" if errors else ("warn" if warnings else "pass"),
        "case_id": result.get("case_id"),
        "direction": direction,
        "source": source_label,
        "result": str(args.result),
        "glossary": str(args.glossary) if args.glossary else None,
        "bilingual": str(bilingual_path) if bilingual_path else None,
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "segments_source": len(source_segments),
            "segments_result": len(res_segments),
            "locked_terms": len(file_map),
            "checks": len(checks),
            "exit_code": exit_code,
        },
        "computed_preservation": computed,
        "claimed_preservation": claimed,
        "checks": checks,
        "findings": checker.findings,
        "limitations": [
            "Number/unit comparison ignores numbers inside formulas, URLs, code and [n] citations.",
            "Negation and modality are cue-based screens; idioms and scope need human review.",
            "Target-text matching in bilingual.md compares the first 40 normalized characters.",
            "A pass means no detected defect, not a guarantee of translation quality.",
        ],
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text, encoding="utf-8")

    if args.json:
        print(text)
    else:
        print("check_translation %s" % VERSION)
        print("source: %s" % source_label)
        print("result: %s" % args.result)
        print("direction: %s | segments: %d source / %d result"
              % (direction, len(source_segments), len(res_segments)))
        for check in checks:
            print("%-6s %-22s %-4s %d error / %d warning"
                  % (check["id"], check["name"], check["status"].upper(),
                     check["errors"], check["warnings"]))
        print("summary: %d error(s), %d warning(s) -> %s"
              % (errors, warnings, "FAIL" if exit_code else "PASS"))
        if args.report:
            print("report: %s" % args.report)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
