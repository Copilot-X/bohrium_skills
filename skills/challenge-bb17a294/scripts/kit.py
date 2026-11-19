#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared engine for the academic translation / bilingual writing skill.

Capabilities (all stdlib, no network):

- conservative segmentation (headings, fenced code, display math, tables,
  list blocks and blank-line paragraphs stay intact);
- glossary candidates (acronyms, explicit bilingual pairs, repeated n-grams);
- bilingual.md parsing and emission;
- segment-level fidelity audit: numbers, units, formulas, citations,
  figure/table/equation/section references, URLs/DOIs/code/placeholders,
  negation and claim-strength (modality), locked terminology, alignment,
  completeness and register;
- ``preserved_facts`` and ``evidence_map`` generation for result.json;
- result.json assembly with the official writing aliases
  ``deliverable`` / ``preserved_facts`` / ``evidence_map`` / ``change_log``.

The audit is a screening aid, never a delivery gate.  It reports findings and
the computed ``preservation`` flags; the caller (main.py) writes the real
translation first and records remaining findings as uncertainty.

Version: 1.0.0
"""

import json
import re
import unicodedata
from collections import Counter, OrderedDict
from decimal import Decimal, InvalidOperation
from pathlib import Path

VERSION = "1.0.0"

PRESERVATION_KEYS = [
    "numbers_match", "units_match", "formulas_match",
    "citation_numbers_match", "figure_refs_match",
    "negation_match", "modality_match",
]

GROUP_LABELS = OrderedDict([
    ("alignment", "G1 alignment"),
    ("numbers", "G2 numbers/units"),
    ("citations", "G3 citations/refs"),
    ("terms", "G4 terminology"),
    ("strength", "G5 negation/modality"),
    ("preservation", "G6 formulas/urls/code"),
    ("completeness", "G7 completeness"),
    ("bilingual", "G8 bilingual"),
])

# ---------------------------------------------------------------------------
# small IO / text utilities
# ---------------------------------------------------------------------------

def read_text(path):
    return Path(path).read_text(encoding="utf-8-sig")


def load_json(path):
    return json.loads(read_text(path))


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def norm_space(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "")).strip()


def one_line(text):
    return re.sub(r"\s+", " ", (text or "").strip())


def excerpt(text, limit=120):
    text = one_line(text)
    return text if len(text) <= limit else text[:limit] + "…"


def cjk_count(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def word_count(text):
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-]*", text or ""))


def cjk_ratio(text):
    text = text or ""
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", text))
    return cjk_count(text) / max(letters, 1)


# ---------------------------------------------------------------------------
# segmentation (headings / code / math / tables / lists stay intact)
# ---------------------------------------------------------------------------

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+\S")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+)")
TABLE_RE = re.compile(r"^\s*\|")
MATH_START_RE = re.compile(
    r"^\s*(?:\$\$|\\\[|\\begin\{(?:equation|align|eqnarray|gather|multline)\*?\})"
)
MATH_ENV_RE = re.compile(r"\\begin\{([^}]+)\}")


def split_blocks(text):
    """Return blocks with 1-based line ranges. Blank lines separate paragraphs."""
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
            blocks.append({
                "lines": list(buf),
                "line_start": (buf_start if buf_start is not None else 0) + 1,
                "line_end": end_idx,
            })
        buf = []
        buf_start = None

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            flush(i)
            i += 1
            continue
        if HEADING_RE.match(line):
            flush(i)
            blocks.append({"lines": [line.rstrip()],
                           "line_start": i + 1, "line_end": i + 1})
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
                r"^\s*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$")
            while i < n:
                buf.append(lines[i].rstrip("\n"))
                if closing.match(lines[i]):
                    i += 1
                    break
                i += 1
            blocks.append({"lines": list(buf), "line_start": start + 1,
                           "line_end": i})
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
            one_line_math = closing == "$$" and stripped.count("$$") >= 2
            buf = [line.rstrip("\n")]
            i += 1
            while closing and not one_line_math and i < n:
                buf.append(lines[i].rstrip("\n"))
                if closing in lines[i]:
                    i += 1
                    break
                i += 1
            blocks.append({"lines": list(buf), "line_start": start + 1,
                           "line_end": i})
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
    if (first.startswith("$$") or first.startswith("\\[")
            or first.startswith("\\begin{")):
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
# glossary candidates and glossary normalisation
# ---------------------------------------------------------------------------

STOP_ACRONYMS = {
    "A", "I", "AN", "AND", "AS", "AT", "BE", "BY", "DO", "ET", "FOR", "FROM",
    "FIG", "IN", "IS", "IT", "NO", "NOT", "OF", "ON", "OR", "THE", "TO", "VS",
    "WE", "ALL", "ANY", "ARE", "HAS", "HAVE", "THIS", "THAT", "THESE",
    "THOSE", "WAS", "WERE", "WITH", "PDF", "DOI", "URL", "HTTP", "HTTPS",
    "HTML", "JSON", "API", "SI", "MD", "II", "III", "IV", "VI", "VII",
    "VIII", "IX", "XI", "XII", "OK",
}

CJK_STOP = {
    "本文", "我们", "研究", "结果", "方法", "实验", "样品", "材料", "表面",
    "可以", "通过", "以及", "其中", "由于", "因此", "然而", "但是", "并且",
    "采用", "所有", "具有", "进行", "表明", "显示", "分析", "计算", "观察",
    "发现", "增加", "降低", "提高", "影响", "作用", "条件", "温度", "时间",
    "浓度", "结构", "性能", "一个", "一种", "不同", "相关", "主要", "显著",
    "这些", "那些", "同时", "分别", "进一步", "目前", "已经", "能够", "需要",
    "可能", "约为", "之间", "以上", "以下", "之后", "之前", "活性", "表征",
    "出现", "对应", "过程", "变化", "趋势", "合成", "演示", "数据", "文件",
}

PAIR_PREFIXES = [
    "本文采用", "本研究采用", "我们采用", "本文使用", "本研究使用",
    "采用", "使用", "利用", "基于", "通过", "本文", "本研究", "我们",
    "上述", "其", "该", "本", "此", "这", "那", "各", "所",
    "可影响", "会影响", "影响", "调控", "提高", "降低", "增强", "抑制",
    "促进", "决定", "取决于", "可用于", "用于",
    "是", "为", "的", "与", "和", "及", "对", "在", "由", "将", "把",
]

LATIN_STOP = {
    "the", "and", "with", "from", "that", "this", "these", "those", "were",
    "was", "are", "have", "has", "been", "using", "used", "result", "results",
    "sample", "samples", "figure", "table", "study", "studies", "however",
    "therefore", "which", "their", "there", "where", "while", "after",
    "before", "between", "during", "shows", "show", "observed", "compared",
    "respectively", "significant", "significantly", "increase", "increased",
    "decrease", "decreased", "higher", "lower", "also", "than", "into",
    "such", "synthetic", "data", "note", "only",
}

CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
PAIR_RE = re.compile(
    r"([\u4e00-\u9fff]{2,12})\s*[（(]\s*([A-Za-z][A-Za-z0-9 \-/]{1,60}?)\s*[)）]")
ACRONYM_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,7}(?![A-Za-z0-9])")
LATIN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z\-]{5,}(?![A-Za-z0-9])")


def trim_pair_term(term):
    changed = True
    while changed:
        changed = False
        for prefix in PAIR_PREFIXES:
            if term.startswith(prefix) and len(term) - len(prefix) >= 2:
                term = term[len(prefix):]
                changed = True
                break
    return term


def _add_candidate(store, term, kind, segment_id, confidence, suggested=None):
    term = (term or "").strip()
    if not term:
        return
    key = term.casefold()
    rec = store.get(key)
    if rec is None:
        rec = {"term": term, "kind": kind, "count": 0,
               "segment_ids": [], "confidence": confidence}
        if suggested:
            rec["suggested_target"] = suggested.strip()
        store[key] = rec
    rec["count"] += 1
    if segment_id not in rec["segment_ids"]:
        rec["segment_ids"].append(segment_id)
    rank = {"high": 3, "medium": 2, "low": 1}
    if rank.get(confidence, 0) > rank.get(rec.get("confidence"), 0):
        rec["confidence"] = confidence


def candidate_terms(segments, min_count=2, max_candidates=40):
    """Suggest terms; suggestions are never locked without a target."""
    store = OrderedDict()
    for seg in segments:
        for match in ACRONYM_RE.finditer(seg["source"]):
            token = match.group(0)
            if token in STOP_ACRONYMS or token.isdigit():
                continue
            _add_candidate(store, token, "acronym", seg["segment_id"], "high")
    for seg in segments:
        for match in PAIR_RE.finditer(seg["source"]):
            term = trim_pair_term(match.group(1))
            if len(term) < 2:
                continue
            _add_candidate(store, term, "bilingual_pair", seg["segment_id"],
                           "high", suggested=match.group(2))
    counter = Counter()
    seg_ids = {}
    for seg in segments:
        for run in CJK_RUN_RE.findall(seg["source"]):
            for size in (2, 3, 4):
                for j in range(len(run) - size + 1):
                    gram = run[j:j + size]
                    if gram in CJK_STOP:
                        continue
                    if len(gram) == 2 and any(
                            ch in "的了和与在为中该其此等及或" for ch in gram):
                        continue
                    counter[gram] += 1
                    seg_ids.setdefault(gram, set()).add(seg["segment_id"])
    ordered = sorted(counter.items(), key=lambda kv: (-len(kv[0]), -kv[1], kv[0]))
    selected = []
    for gram, count in ordered:
        if count < min_count:
            continue
        if any(gram in longer and count == other for longer, other in selected):
            continue
        selected.append((gram, count))
    for gram, count in selected[:25]:
        confidence = "medium" if count >= 3 else "low"
        for sid in sorted(seg_ids[gram]):
            _add_candidate(store, gram, "cjk_ngram", sid, confidence)
        store[gram.casefold()]["count"] = count
    latin = Counter()
    latin_case = {}
    latin_segments = {}
    for seg in segments:
        for match in LATIN_RE.finditer(seg["source"]):
            token = match.group(0)
            key = token.casefold()
            if key in LATIN_STOP:
                continue
            latin[key] += 1
            latin_case.setdefault(key, token)
            latin_segments.setdefault(key, set()).add(seg["segment_id"])
    for key, count in latin.items():
        if count < 3:
            continue
        for sid in sorted(latin_segments[key]):
            _add_candidate(store, latin_case[key], "latin_term", sid, "medium")
        store[key]["count"] = count
    candidates = sorted(
        store.values(),
        key=lambda c: ({"high": 0, "medium": 1, "low": 2}.get(
            c.get("confidence"), 3), -c["count"], c["term"]))
    return candidates[:max_candidates]


def default_register():
    return {
        "genre": "academic",
        "formality": "academic",
        "person": "third-person",
        "tense": "present-for-claims; past-for-methods",
        "english_variant": "en-US",
        "avoid": ["don't", "can't", "won't"],
    }


def normalize_glossary(doc):
    """Return a glossary dict with validated terms and a register block."""
    doc = doc if isinstance(doc, dict) else {}
    terms = []
    seen = set()
    for item in doc.get("terms", []) or []:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        target = item.get("target")
        if not isinstance(source, str) or not source.strip():
            continue
        source = source.strip()
        if source in seen:
            continue
        seen.add(source)
        terms.append({
            "source": source,
            "target": target.strip() if isinstance(target, str) else "",
            "locked": bool(item.get("locked", True)),
            "aliases": [a for a in item.get("aliases", []) or []
                        if isinstance(a, str) and a.strip()],
            "forbidden": [a for a in item.get("forbidden", []) or []
                          if isinstance(a, str) and a.strip()],
            "case_sensitive": bool(item.get("case_sensitive", False)),
        })
    register = doc.get("register")
    register = dict(register) if isinstance(register, dict) else default_register()
    for key, value in default_register().items():
        register.setdefault(key, value)
    out = {
        "schema_version": doc.get("schema_version", "1.0"),
        "case_id": doc.get("case_id"),
        "direction": doc.get("direction"),
        "register": register,
        "terms": terms,
        "do_not_translate": doc.get("do_not_translate", []) or [],
        "abbreviations": doc.get("abbreviations", []) or [],
        "conflicts": doc.get("conflicts", []) or [],
    }
    return out


def _target_variants(target):
    variants = [target]
    if re.fullmatch(r"[A-Za-z][A-Za-z \-]*", target):
        if target.endswith("y"):
            variants.append(target[:-1] + "ies")
        else:
            variants.append(target + "s")
    return variants


def derive_glossary(candidates, segments, targets):
    """Build a conservative glossary from candidates plus aligned targets.

    Only explicit bilingual pairs are locked, and only when the suggested
    target is actually present in some translated segment.  Everything else
    stays tentative (locked=false) with an empty target.
    """
    joined_targets = "\n".join(targets.get(s["segment_id"], "")
                               for s in segments)
    joined_source = "\n".join(s["source"] for s in segments)
    terms = []
    seen = set()
    for cand in candidates or []:
        term = cand.get("term")
        if not isinstance(term, str) or not term or term.casefold() in seen:
            continue
        if term not in joined_source:
            continue
        seen.add(term.casefold())
        if cand.get("kind") == "bilingual_pair" and cand.get("suggested_target"):
            target = cand["suggested_target"].strip()
            present = term_occurs(target, joined_targets, False)
            terms.append({
                "source": term,
                "target": target,
                "locked": bool(present),
                "aliases": _target_variants(target) if present else [],
                "forbidden": [],
                "case_sensitive": False,
            })
        # Other candidates stay in glossary-candidates.json for the agent to
        # review; adding them here without a target only creates audit noise.
    return {
        "schema_version": "1.0",
        "register": default_register(),
        "terms": terms,
        "do_not_translate": [],
        "abbreviations": [],
        "conflicts": [],
        "source": "derived_from_candidates",
    }


def is_acronym(term):
    return bool(re.fullmatch(r"[A-Z][A-Z0-9\-]{1,9}", term or ""))


def term_pattern(term, case_sensitive=False):
    if re.search(r"[A-Za-z]", term):
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", flags)
    return None


def term_occurs(term, text, case_sensitive=False):
    if not term or not text:
        return False
    pattern = term_pattern(term, case_sensitive)
    if pattern is not None:
        return pattern.search(text) is not None
    return term in text


def locked_terms(glossary):
    return [t for t in (glossary or {}).get("terms", [])
            if isinstance(t, dict) and t.get("target")
            and t.get("locked", True)]


# ---------------------------------------------------------------------------
# bilingual.md
# ---------------------------------------------------------------------------

SECTION_RE = re.compile(r"^#{2,4}\s+(S\d+)\b.*$", re.MULTILINE)
TARGET_RE = re.compile(
    r"^\s*[-*]\s*\*\*\s*Target\s*(?:\([^)]*\))?\s*:?\s*\*\*\s*(.*)$", re.I)
SOURCE_RE = re.compile(
    r"^\s*[-*]\s*\*\*\s*Source\s*(?:\([^)]*\))?\s*:?\s*\*\*\s*(.*)$", re.I)


def parse_bilingual(text):
    """Return {"targets": {sid: target}, "sources": {...}, "order": [...]}. """
    matches = list(SECTION_RE.finditer(text or ""))
    targets, sources, order, duplicates = {}, {}, [], []
    for i, match in enumerate(matches):
        sid = match.group(1)
        if sid in targets:
            duplicates.append(sid)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end():end]
        target, source = [], []
        collecting = None
        for line in body.splitlines():
            tm = TARGET_RE.match(line)
            if tm:
                target = [tm.group(1).strip()]
                collecting = "target"
                continue
            sm = SOURCE_RE.match(line)
            if sm:
                source = [sm.group(1).strip()]
                collecting = "source"
                continue
            if collecting:
                if not line.strip():
                    continue
                if re.match(r"^\s*(?:[-*]\s+|\*\*|#{1,6}\s)", line):
                    collecting = None
                    continue
                (target if collecting == "target" else source).append(line.strip())
        targets[sid] = one_line(" ".join(target))
        sources[sid] = one_line(" ".join(source))
        order.append(sid)
    return {"targets": targets, "sources": sources, "order": order,
            "duplicates": duplicates}


def emit_bilingual(segments, targets, source_lang="zh", target_lang="en",
                   generated=False):
    lines = ["# Bilingual Alignment", ""]
    if generated:
        lines.append("<!-- generated from aligned targets; verify before delivery -->")
        lines.append("")
    for seg in segments:
        sid = seg["segment_id"]
        lines.append("## %s" % sid)
        lines.append("")
        lines.append("- **Source (%s):** %s"
                     % (source_lang, one_line(seg.get("source", ""))))
        lines.append("- **Target (%s):** %s"
                     % (target_lang, one_line(targets.get(sid, ""))))
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# span masking: formulas / URLs / code / placeholders / HTML / [n] citations
# ---------------------------------------------------------------------------

FORMULA_PATTERNS = [
    re.compile(r"\$\$.+?\$\$", re.S),
    re.compile(r"(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)", re.S),
    re.compile(r"\\\[.+?\\\]", re.S),
    re.compile(r"\\\(.+?\\\)", re.S),
    re.compile(
        r"\\begin\{(equation|align|eqnarray|gather|multline)\*?\}.+?"
        r"\\end\{\1\*?\}", re.S),
]
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s<>\"']+", re.I)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
CODE_RE = re.compile(r"`[^`\n]+`")
PLACEHOLDER_RE = re.compile(
    r"\{[A-Za-z_][A-Za-z0-9_.\-]*\}|\{\{[^{}]+\}\}|%\d*\$?[sd]")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")
NUM_CITE_RE = re.compile(
    r"[\[［]\s*(\d{1,3}(?:\s*[,，–\-—]\s*\d{1,3})*)\s*[\]］]")

AUTHOR_YEAR_PAIR_RE = re.compile(
    r"([A-Z][A-Za-z'’\-]+)"
    r"(?:\s*(?:et\s+al\.?|等|and|&)\s*(?:[A-Z][A-Za-z'’\-]+)?)?"
    r"[，,]\s*(\d{4}[a-z]?)")
GROUPED_CITE_RE = re.compile(r"\(([^()]*\d{4}[a-z]?[^()]*)\)")
NARRATIVE_CITE_RE = re.compile(
    r"([A-Z][A-Za-z'’\-]+)\s+(?:et\s+al\.?|等)\s*\((\d{4}[a-z]?)\)")
NARRATIVE_PLAIN_RE = re.compile(r"([A-Z][A-Za-z'’\-]+)\s+\((\d{4}[a-z]?)\)")
NON_AUTHOR_WORDS = {
    "figure", "fig", "table", "section", "sec", "equation", "eq", "reference",
    "references", "supplementary", "appendix", "data", "methods", "results",
    "discussion", "conclusion", "chapter", "scheme", "note", "sample",
}

FIG_RE = re.compile(
    r"(?:Fig(?:ure)?s?\.?|(?<![地意试蓝构])图)\s*"
    r"(S?[0-9]+[A-Za-z]?(?:\s*[–\-—~]\s*S?[0-9]+[A-Za-z]?)?)", re.I)
TAB_RE = re.compile(
    r"(?:Tables?|(?<![达示明征面述决演代])表)\s*"
    r"(S?[0-9]+[A-Za-z]?(?:\s*[–\-—~]\s*S?[0-9]+[A-Za-z]?)?)", re.I)
EQ_RE = re.compile(
    r"(?:Eq(?:uation)?s?\.?|公式|(?<![方形模正格样])式)\s*"
    r"[\(（]?\s*(S?[0-9]+[A-Za-z]?)\s*[\)）]?", re.I)
SEC_RE = re.compile(
    r"(?:Sec(?:tion)?\.?|(?<![调环季节目约奏细情关字])节)\s*"
    r"([0-9]+(?:\.[0-9]+)*)"
    r"|第\s*([0-9]+(?:\.[0-9]+)*)\s*节", re.I)


def merge_spans(spans):
    spans = sorted((s, e) for s, e, *_ in spans)
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
    for start, end in merge_spans(spans):
        for i in range(start, min(end, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _spans(pattern, text, transform=None):
    out = []
    for match in pattern.finditer(text):
        value = match.group(0)
        if transform:
            value = transform(value)
        out.append((match.start(), match.end(), value))
    return out


def build_masked(text):
    """Mask protected tokens and return (masked_text, token counters)."""
    formula = _spans(FORMULA_PATTERNS[0], text)
    for pattern in FORMULA_PATTERNS[1:]:
        formula += _spans(pattern, text)
    formula = [(s, e, norm_space(re.sub(r"\\bm(?![A-Za-z])", r"\\boldsymbol", v)))
               for s, e, v in formula]
    masked = mask_spans(text, formula)
    url = []
    for pattern in (URL_RE, DOI_RE, EMAIL_RE):
        url += _spans(pattern, masked, lambda v: v.rstrip(".,;:!?"))
    masked = mask_spans(masked, url)
    code = _spans(CODE_RE, masked)
    masked = mask_spans(masked, code)
    placeholder = _spans(PLACEHOLDER_RE, masked)
    masked = mask_spans(masked, placeholder)
    html = _spans(HTML_TAG_RE, masked)
    masked = mask_spans(masked, html)
    cite = []
    for match in NUM_CITE_RE.finditer(masked):
        inner = re.sub(r"\s+", "", match.group(1))
        inner = re.sub(r"[–—−]", "-", inner).replace("，", ",")
        cite.append((match.start(), match.end(), "num:[" + inner + "]"))
    masked = mask_spans(masked, cite)
    tokens = {
        "formula": Counter(s[2] for s in formula),
        "url": Counter(s[2] for s in url),
        "code": Counter(s[2] for s in code),
        "placeholder": Counter(s[2] for s in placeholder),
        "html": Counter(s[2] for s in html),
        "numeric_cite": Counter(s[2] for s in cite),
    }
    return masked, tokens


# ---------------------------------------------------------------------------
# numbers and units
# ---------------------------------------------------------------------------

SCALE_VALUES = {
    "万": Decimal(10) ** 4, "亿": Decimal(10) ** 8,
    "thousand": Decimal(1000), "million": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9,
}
NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
    r"(?P<scale>\s*(?:万|亿|thousand|million|billion))?"
    r"(?![A-Za-z])", re.IGNORECASE)

UNIT_ALIASES = {
    "%": "%", "‰": "‰", "°C": "°C", "℃": "°C",
    "kg": "kg", "mg": "mg", "μg": "ug", "µg": "ug", "ug": "ug",
    "ng": "ng", "g": "g", "mol": "mol", "mmol": "mmol",
    "μmol": "umol", "µmol": "umol", "umol": "umol",
    "M": "M", "mM": "mM", "μM": "uM", "µM": "uM", "uM": "uM", "nM": "nM",
    "nm": "nm", "μm": "um", "µm": "um", "um": "um", "mm": "mm",
    "cm": "cm", "m": "m", "km": "km",
    "mL": "mL", "ml": "mL", "μL": "uL", "µL": "uL", "uL": "uL", "ul": "uL",
    "L": "L", "l": "L",
    "Pa": "Pa", "kPa": "kPa", "MPa": "MPa", "GPa": "GPa",
    "eV": "eV", "keV": "keV", "MeV": "MeV",
    "V": "V", "mV": "mV", "kV": "kV", "A": "A", "mA": "mA",
    "Ω": "ohm", "ohm": "ohm",
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
    "m2/g": "m2/g", "m²/g": "m2/g", "m3/g": "m3/g", "m³/g": "m3/g",
    "cm2/g": "cm2/g", "cm²/g": "cm2/g", "cm3/g": "cm3/g", "mg/L": "mg/L",
    "g/L": "g/L", "mol/L": "mol/L", "mmol/L": "mmol/L",
    "mL/min": "mL/min", "L/min": "L/min", "mL/h": "mL/h",
    "J/mol": "J/mol", "kJ/mol": "kJ/mol", "eV/atom": "eV/atom",
    "mAh/g": "mAh/g", "Wh/kg": "Wh/kg", "W/m2": "W/m2", "kW/m2": "kW/m2",
}
_UNIT_KEYS = sorted(UNIT_ALIASES, key=len, reverse=True)
UNIT_RE = re.compile(
    r"\s*(" + "|".join(re.escape(u) for u in _UNIT_KEYS) + r")(?![A-Za-z0-9])")

MONTH_NUMBERS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5,
    "June": 6, "July": 7, "August": 8, "September": 9, "October": 10,
    "November": 11, "December": 12, "Sept": 9, "Jan": 1, "Feb": 2,
    "Mar": 3, "Apr": 4, "Jun": 6, "Jul": 7, "Aug": 8, "Sep": 9,
    "Oct": 10, "Nov": 11, "Dec": 12,
}
MONTH_RE = re.compile(
    r"\b(January|February|March|April|June|July|August|September|"
    r"October|November|December|Sept|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|"
    r"Nov|Dec)\.?(?![A-Za-z])")
MAY_DATE_RE = re.compile(r"\bMay\.?(?=\s+\d)")


def _join_cn_date(match):
    parts = [match.group(1), str(int(match.group(2)))]
    if match.group(3):
        parts.append(str(int(match.group(3))))
    return "-".join(parts)


def normalize_measure_text(text):
    """Normalise formatting-only differences before numeric comparison."""
    text = re.sub(r"[０-９]", lambda m: str(ord(m.group(0)) - ord("０")), text)
    text = (text.replace("％", "%").replace("．", ".").replace("，", ",")
                .replace("－", "-").replace("～", "~"))
    text = unicodedata.normalize("NFC", text)
    text = re.sub(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*[日号])?",
        _join_cn_date, text)
    text = re.sub(r"(\d{4})\s*年", r"\1", text)
    text = MAY_DATE_RE.sub("5", text)
    text = MONTH_RE.sub(lambda m: str(MONTH_NUMBERS[m.group(1)]), text)
    text = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", text, flags=re.I)
    text = re.sub(r"\b(\d{4})s\b", r"\1", text)
    text = re.sub(r"(?<=\d)\s*(?:小时|钟头)", " h", text)
    text = re.sub(r"(?<=\d)\s*分钟", " min", text)
    text = re.sub(r"(?<=\d)\s*毫秒", " ms", text)
    text = re.sub(r"(?<=\d)\s*(?:秒钟|秒)", " s", text)
    text = re.sub(r"(?<=\d)\s*天(?=$|[^一-鿿]|[内后前中左右的里])", " day", text)
    text = re.sub(r"(?<=\d)\s*周(?=$|[^一-鿿]|[内后前中左右的里])", " week", text)
    text = re.sub(r"(?<=\d)\s*年(?=$|[^一-鿿]|[内后前中左右的里间])", " year", text)
    text = re.sub(r"\bper\s*cent\b|\bpercent\b", "%", text, flags=re.I)
    text = re.sub(r"(?<=\d)[  ](?=\d{3}(?!\d))", ",", text)
    return text


def dec_str(value):
    if value == value.to_integral_value():
        return format(value, "f").split(".")[0]
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def extract_numbers(text):
    text = normalize_measure_text(text or "")
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
        items.append({"value": dec_str(value), "unit": None,
                      "start": match.start(), "end": match.end(),
                      "raw": match.group(0).strip()})
    for item in items:
        tail = text[item["end"]:item["end"] + 16]
        unit_match = UNIT_RE.match(tail)
        if unit_match:
            item["unit"] = UNIT_ALIASES.get(unit_match.group(1),
                                            unit_match.group(1))
    for i in range(len(items) - 1):
        if items[i]["unit"] is None:
            gap = text[items[i]["end"]:items[i + 1]["start"]]
            if re.fullmatch(r"\s*(?:[–\-—−~]|to|and|至|到|和|与)\s*", gap, re.I):
                items[i]["unit"] = items[i + 1]["unit"]
    for item in items:
        item["precision"] = len(item["raw"].split(".")[1]) \
            if "." in item["raw"] else 0
    return items


# ---------------------------------------------------------------------------
# citations / cross references
# ---------------------------------------------------------------------------

def extract_author_year(text):
    cites = Counter()
    text = (text or "").replace("（", "(").replace("）", ")")

    def add(name, year):
        if name.lower() in NON_AUTHOR_WORDS or name in MONTH_NUMBERS:
            return
        cites["ay:%s:%s" % (name.lower(), year.lower())] += 1

    for group in GROUPED_CITE_RE.finditer(text):
        for match in AUTHOR_YEAR_PAIR_RE.finditer(group.group(1)):
            add(match.group(1), match.group(2))
    for match in NARRATIVE_CITE_RE.finditer(text):
        add(match.group(1), match.group(2))
    for match in NARRATIVE_PLAIN_RE.finditer(text):
        if NARRATIVE_CITE_RE.search(text[match.start():match.end() + 8]):
            continue
        add(match.group(1), match.group(2))
    return cites


def extract_refs(text):
    """Language-neutral ref counters: Fig. 2a and 图 2a both become fig:2a."""
    refs = Counter()

    def add(kind, value):
        value = re.sub(r"\s+", "", value or "")
        value = re.sub(r"[–—−~]", "-", value)
        refs[kind + ":" + value.lower()] += 1

    for match in FIG_RE.finditer(text or ""):
        add("fig", match.group(1))
    for match in TAB_RE.finditer(text or ""):
        add("tab", match.group(1))
    for match in EQ_RE.finditer(text or ""):
        add("eq", match.group(1))
    for match in SEC_RE.finditer(text or ""):
        add("sec", match.group(1) or match.group(2))
    return refs


def numeric_cites(text):
    masked, tokens = build_masked(text or "")
    out = []
    for match in NUM_CITE_RE.finditer(masked):
        inner = re.sub(r"\s+", "", match.group(1))
        inner = re.sub(r"[–—−]", "-", inner).replace("，", ",")
        out.append("[" + inner + "]")
    return out, masked, tokens


# ---------------------------------------------------------------------------
# negation / modality (claim strength)
# ---------------------------------------------------------------------------

NEG_CN = re.compile(
    r"不显著|不明显|不能|不可|未能|未见|未发现|未检测到|未观察到|未出现|"
    r"未达到|未满足|未发生|没有|无法|并非|并未|不含|不存在|无显著|"
    r"无明显|缺乏|缺失|不应|不再|尚未|无需|不等于|不支持|不构成|并不|"
    r"不足以|不是|不对应|未掺杂|未处理|未修饰")
NEG_EN = re.compile(
    r"(?<![A-Za-z])(?:not|none|never|without|cannot|neither|nor|"
    r"fail(?:s|ed)?|absent|absence|lack(?:s|ed)?|unable|insufficient|"
    r"inadequate|undoped|untreated|"
    r"no\s+(?:significant|difference|obvious|clear|evidence)|no\s+longer)"
    r"(?![A-Za-z])", re.I)
NOT_ONLY_RE = re.compile(r"not\s+only|不仅|不但", re.I)

MODALITY = {
    "possibility": re.compile(
        r"(?<![A-Za-z])(?:may|might|could|possibly|perhaps)(?![A-Za-z])|"
        r"可能|或许|也许|或将", re.I),
    "indication": re.compile(
        r"(?<![A-Za-z])(?:suggest(?:s|ed)?|indicat(?:e|es|ed)|appear(?:s|ed)?|"
        r"seem(?:s|ed)?|likely|probably)(?![A-Za-z])|提示|推测|似乎", re.I),
    "demonstration": re.compile(
        r"(?<![A-Za-z])(?:show(?:s|n|ed)?|demonstrat(?:e|es|ed)|find(?:s|ings?)?|"
        r"found|observ(?:e|es|ed))(?![A-Za-z])|显示|表明|发现|观察到|表现出", re.I),
    "proof": re.compile(
        r"(?<![A-Za-z])(?:prove(?:s|n|d)?|confirm(?:s|ed)?|establish(?:es|ed)?)"
        r"(?![A-Za-z])|证明|证实|确认", re.I),
    "obligation_high": re.compile(
        r"(?<![A-Za-z])(?:must|required|requires?|necessary)(?![A-Za-z])|"
        r"必须|需要|必需|须|(?<!无)需", re.I),
    "obligation_mid": re.compile(
        r"(?<![A-Za-z])(?:should|ought)(?![A-Za-z])|应当|应该|理应|应予", re.I),
}

CONTRACTION_RE = re.compile(
    r"(?<![A-Za-z])(?:[A-Za-z]+n't|(?:it|that|there|what|who|he|she|we|they)'s|"
    r"(?:I|you|we|they)'re|(?:I|you|we|they)'ve|"
    r"(?:I|you|he|she|we|they)'ll|(?:I|you|he|she|we|they)'d)(?![A-Za-z])",
    re.I)

# "Figure 2a shows ..." is presentational, not claim strength.
PRESENTATIONAL_RE = re.compile(
    r"(?:Fig(?:ure)?s?\.?|Tables?|Scheme|图|表)\s*S?\d+[A-Za-z]?"
    r"[^.;。；\n]{0,60}?"
    r"(?:show(?:s|ed|n|ing)?|present(?:s|ed)?|summari[sz](?:e|es|ed)|"
    r"display(?:s|ed)?|illustrat(?:e|es|ed)|列出|给出|显示|展示|汇总|总结|所示)"
    r"|(?:as\s+)?(?:show(?:n|s|ed)?|present(?:ed)?|illustrat(?:ed|es)?|"
    r"summari[sz]ed)\s+in\s+(?:Fig(?:ure)?s?\.?|Tables?|Scheme)\s*S?\d+[A-Za-z]?",
    re.I)


def negation_cues(text):
    text = NOT_ONLY_RE.sub(" ", text or "")
    return NEG_CN.findall(text) + NEG_EN.findall(text)


def modality_profile(text):
    text = PRESENTATIONAL_RE.sub(" ", text or "")
    return {name: [m.group(0) for m in pattern.finditer(text)]
            for name, pattern in MODALITY.items()}


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
# audit
# ---------------------------------------------------------------------------

class Findings:
    def __init__(self):
        self.items = []
        self._seen = set()

    def add(self, level, code, group, message, segment_id=None,
            source=None, target=None):
        key = (level, code, group, segment_id, message)
        if key in self._seen:
            return
        self._seen.add(key)
        self.items.append({
            "level": level, "code": code, "group": group,
            "segment_id": segment_id, "message": message,
            "source_excerpt": excerpt(source) if source else "",
            "target_excerpt": excerpt(target) if target else "",
        })

    def error(self, code, group, message, **kwargs):
        self.add("error", code, group, message, **kwargs)

    def warning(self, code, group, message, **kwargs):
        self.add("warning", code, group, message, **kwargs)

    def count(self, level=None, group=None, prefix=None):
        total = 0
        for item in self.items:
            if level and item["level"] != level:
                continue
            if group and item["group"] != group:
                continue
            if prefix and not item["code"].startswith(prefix):
                continue
            total += 1
        return total


def _compare(findings, code, group, src, tgt, label, seg_id, source, target):
    for value, count in sorted((src - tgt).items()):
        findings.error(code + ".missing", group,
                       "%s missing from target: %s (x%d)" % (label, value, count),
                       segment_id=seg_id, source=source, target=target)
    for value, count in sorted((tgt - src).items()):
        findings.error(code + ".extra", group,
                       "%s added in target: %s (x%d)" % (label, value, count),
                       segment_id=seg_id, source=source, target=target)


def audit_document(segments, targets, glossary=None, direction="zh->en",
                   bilingual=None, translation_text=None):
    """Audit aligned targets; return a JSON-able report (never raises on data)."""
    glossary = normalize_glossary(glossary)
    source_ids = [s["segment_id"] for s in segments]
    target_ids = list(targets.keys())
    findings = Findings()
    target_lang = "en" if direction == "zh->en" else "zh"

    for sid in source_ids:
        if sid not in targets:
            findings.error("segment.missing", "alignment",
                           "result is missing source segment %s" % sid)
    for sid in target_ids:
        if sid not in source_ids:
            findings.error("segment.extra", "alignment",
                           "result has unknown segment %s" % sid)
    if not target_ids == source_ids and set(target_ids) == set(source_ids):
        findings.error("segment.order", "alignment",
                       "segment order differs from source order")

    terms = glossary["terms"]
    register = glossary.get("register") or {}
    if not terms:
        findings.warning("glossary.empty", "terms",
                         "glossary contains no terms; terminology was not locked")
    for term in terms:
        if term.get("locked") and not term.get("target"):
            findings.warning("glossary.no_target", "terms",
                             "locked term %r has no target" % term["source"])
    if register.get("formality") == "academic" and target_lang == "en":
        for sid, target in targets.items():
            if CONTRACTION_RE.search(target or ""):
                findings.warning("register.contraction", "terms",
                                 "academic target contains a contraction",
                                 segment_id=sid, target=target)

    for seg in segments:
        sid = seg["segment_id"]
        source = seg.get("source", "")
        if sid not in targets:
            continue
        target = targets.get(sid, "") or ""
        if not target.strip():
            findings.error("segment.empty_target", "completeness",
                           "%s has an empty target" % sid,
                           segment_id=sid, source=source)
            continue

        masked_src, src_tokens = build_masked(source)
        masked_tgt, tgt_tokens = build_masked(target)

        for token_key, code, label in (
                ("formula", "formula", "formula"),
                ("url", "preserve.url", "URL/DOI/email"),
                ("code", "preserve.code", "inline code"),
                ("placeholder", "preserve.placeholder", "placeholder"),
                ("html", "preserve.html", "HTML tag")):
            _compare(findings, code, "preservation",
                     src_tokens[token_key], tgt_tokens[token_key],
                     label, sid, source, target)
        if "\\bm" in target and "\\bm" not in source:
            findings.warning("formula.bm", "preservation",
                             "target uses \\bm; replace with \\boldsymbol",
                             segment_id=sid, target=target)

        src_numbers = extract_numbers(masked_src)
        tgt_numbers = extract_numbers(masked_tgt)
        _compare(findings, "number", "numbers",
                 Counter(n["value"] for n in src_numbers),
                 Counter(n["value"] for n in tgt_numbers),
                 "numeric value", sid, source, target)
        _compare(findings, "unit", "numbers",
                 Counter(n["unit"] for n in src_numbers if n["unit"]),
                 Counter(n["unit"] for n in tgt_numbers if n["unit"]),
                 "unit", sid, source, target)

        tgt_by_value = {}
        for item in tgt_numbers:
            tgt_by_value.setdefault(item["value"], []).append(item)
        for item in src_numbers:
            matches = tgt_by_value.get(item["value"]) or []
            if matches and item["precision"]:
                match = matches[0]
                if match["precision"] < item["precision"]:
                    findings.warning(
                        "number.precision", "numbers",
                        "numeric value %s dropped significant digits" % item["raw"],
                        segment_id=sid, source=source, target=target)

        _compare(findings, "citation", "citations",
                 src_tokens["numeric_cite"], tgt_tokens["numeric_cite"],
                 "numeric citation", sid, source, target)
        _compare(findings, "citation.author_year", "citations",
                 extract_author_year(masked_src),
                 extract_author_year(masked_tgt),
                 "author-year citation", sid, source, target)
        _compare(findings, "ref", "citations",
                 extract_refs(masked_src), extract_refs(masked_tgt),
                 "figure/table/equation/section reference",
                 sid, source, target)

        for term in terms:
            source_term = term["source"]
            if not term.get("target"):
                # Tentative candidates without a target are advisory only and
                # produce no per-segment noise; review them before locking.
                continue
            if not term_occurs(source_term, source,
                               term.get("case_sensitive", is_acronym(source_term))):
                continue
            level = "error" if term.get("locked", True) else "warning"
            case_sensitive = term.get("case_sensitive", is_acronym(source_term))
            variants = [term["target"]] + term.get("aliases", [])
            found = any(term_occurs(v, target,
                                    case_sensitive or is_acronym(v))
                        for v in variants if v)
            if not found:
                findings.add(
                    level, "term.missing", "terms",
                    "locked term %r has no approved target form in %s"
                    % (source_term, sid) if term.get("locked", True)
                    else "tentative term %r has no target form in %s"
                    % (source_term, sid),
                    segment_id=sid, source=source, target=target)
            for bad in term.get("forbidden", []):
                if term_occurs(bad, target, case_sensitive):
                    findings.add(level, "term.forbidden", "terms",
                                 "target uses forbidden variant %r for %r"
                                 % (bad, source_term),
                                 segment_id=sid, source=source, target=target)

        for item in glossary.get("do_not_translate", []) or []:
            text = item.get("text") if isinstance(item, dict) else item
            if not isinstance(text, str) or not text:
                continue
            if term_occurs(text, source, True) and not term_occurs(text, target, True):
                findings.error("dnt.missing", "terms",
                               "do-not-translate token %r disappeared from target"
                               % text, segment_id=sid, source=source, target=target)

        src_neg = len(negation_cues(masked_src))
        tgt_neg = len(negation_cues(masked_tgt))
        if src_neg and not tgt_neg:
            findings.error("negation.missing", "strength",
                           "source is negative but target has no negation cue",
                           segment_id=sid, source=source, target=target)
        elif tgt_neg and not src_neg:
            findings.error("negation.added", "strength",
                           "target adds a negation cue absent from source",
                           segment_id=sid, source=source, target=target)
        elif src_neg and tgt_neg and src_neg != tgt_neg:
            findings.warning("negation.count", "strength",
                             "negation cue count differs (%d -> %d)"
                             % (src_neg, tgt_neg),
                             segment_id=sid, source=source, target=target)

        src_profile = modality_profile(masked_src)
        tgt_profile = modality_profile(masked_tgt)
        src_level = strength_level(src_profile)
        tgt_level = strength_level(tgt_profile)
        if tgt_level > src_level + 1:
            findings.error("modality.upgrade", "strength",
                           "claim strength escalated (%d -> %d)"
                           % (src_level, tgt_level),
                           segment_id=sid, source=source, target=target)
        elif tgt_level > src_level and src_level > 0:
            findings.warning("modality.upgrade", "strength",
                             "claim strength increased (%d -> %d)"
                             % (src_level, tgt_level),
                             segment_id=sid, source=source, target=target)
        elif tgt_level < src_level and tgt_level > 0:
            findings.warning("modality.downgrade", "strength",
                             "claim strength decreased (%d -> %d)"
                             % (src_level, tgt_level),
                             segment_id=sid, source=source, target=target)
        elif src_level > 0 and tgt_level == 0:
            findings.warning("modality.dropped", "strength",
                             "claim-strength cue dropped",
                             segment_id=sid, source=source, target=target)
        elif src_level == 0 and tgt_level > 0:
            findings.warning("modality.new", "strength",
                             "target adds a claim-strength cue",
                             segment_id=sid, source=source, target=target)
        if src_profile["obligation_high"] and not tgt_profile["obligation_high"]:
            findings.warning("modality.obligation", "strength",
                             "high-obligation cue was not preserved",
                             segment_id=sid, source=source, target=target)
        elif not src_profile["obligation_high"] and tgt_profile["obligation_high"]:
            findings.warning("modality.obligation", "strength",
                             "target adds a high-obligation cue",
                             segment_id=sid, source=source, target=target)

        if norm_space(source) == norm_space(target) and len(norm_space(source)) >= 20 \
                and seg.get("type") == "paragraph":
            findings.warning("segment.target_copied", "completeness",
                             "%s target is identical to the source" % sid,
                             segment_id=sid, source=source, target=target)
        if seg.get("type") in ("paragraph", "list", "table"):
            if direction == "zh->en":
                zh, en = cjk_count(source), word_count(target)
                ratio = en / max(zh, 1)
                if zh >= 10 and (ratio < 0.15 or ratio > 2.5):
                    findings.warning("length.ratio", "completeness",
                                     "target/source length ratio looks implausible (%.2f words/char)"
                                     % ratio, segment_id=sid, source=source,
                                     target=target)
            else:
                en, zh = word_count(source), cjk_count(target)
                ratio = zh / max(en, 1)
                if en >= 8 and (ratio < 0.4 or ratio > 5.0):
                    findings.warning("length.ratio", "completeness",
                                     "target/source length ratio looks implausible (%.2f chars/word)"
                                     % ratio, segment_id=sid, source=source,
                                     target=target)

    full_target = "\n".join(targets.get(sid, "") for sid in source_ids)
    for term in terms:
        if not term.get("locked", True) or not term.get("target"):
            continue
        case_sensitive = term.get("case_sensitive", is_acronym(term["source"]))
        if not any(term_occurs(term["source"], s["source"], case_sensitive)
                   for s in segments):
            continue
        variants = [term["target"]] + term.get("aliases", [])
        if not any(term_occurs(v, full_target, case_sensitive or is_acronym(v))
                   for v in variants if v):
            findings.error("term.missing_global", "terms",
                           "locked term %r never appears in the target document"
                           % term["source"])

    if bilingual:
        order = bilingual.get("order", [])
        if len(set(order)) != len(order):
            findings.error("bilingual.duplicate", "bilingual",
                           "bilingual.md contains duplicate segment headings")
        for sid in source_ids:
            if sid not in order:
                findings.error("bilingual.missing_id", "bilingual",
                               "bilingual.md is missing segment %s" % sid)
        for sid in order:
            if sid not in source_ids:
                findings.error("bilingual.extra_id", "bilingual",
                               "bilingual.md has unknown segment %s" % sid)
        if len(order) == len(source_ids) and order != source_ids:
            findings.error("bilingual.order", "bilingual",
                           "bilingual.md segment order differs from segments.json")
        for seg in segments:
            sid = seg["segment_id"]
            target = targets.get(sid, "")
            if not target.strip():
                continue
            needle = norm_space(target)[:40]
            section = bilingual.get("targets", {}).get(sid, "")
            if needle and needle not in norm_space(section):
                findings.error("bilingual.target_mismatch", "bilingual",
                               "%s target text is not present under its heading" % sid,
                               segment_id=sid, target=target)

    if translation_text:
        normalized = norm_space(translation_text)
        missing = 0
        for seg in segments:
            target = targets.get(seg["segment_id"], "")
            if not target.strip():
                continue
            needle = norm_space(target)[:24]
            if needle and needle not in normalized:
                missing += 1
                if missing <= 10:
                    findings.warning(
                        "translation.target_not_found", "completeness",
                        "%s target text was not found in translation.md"
                        % seg["segment_id"], segment_id=seg["segment_id"],
                        target=target)

    coverage_complete = all((targets.get(sid) or "").strip()
                            for sid in source_ids)
    computed = {
        "numbers_match": coverage_complete and findings.count(
            "error", prefix="number.") == 0,
        "units_match": coverage_complete and findings.count(
            "error", prefix="unit.") == 0,
        "formulas_match": coverage_complete and findings.count(
            "error", prefix="formula.") == 0,
        "citation_numbers_match": coverage_complete and findings.count(
            "error", prefix="citation.") == 0,
        "figure_refs_match": coverage_complete and findings.count(
            "error", prefix="ref.") == 0,
        "negation_match": coverage_complete and findings.count(
            "error", prefix="negation.") == 0,
        "modality_match": coverage_complete and findings.count(
            "error", prefix="modality.") == 0,
    }
    checks = []
    for group, label in GROUP_LABELS.items():
        errors = findings.count("error", group=group)
        warnings = findings.count("warning", group=group)
        status = "fail" if errors else ("warn" if warnings else "pass")
        checks.append({"group": group, "name": label, "status": status,
                       "errors": errors, "warnings": warnings})
    errors = findings.count("error")
    warnings = findings.count("warning")
    return {
        "tool": "translation-kit",
        "version": VERSION,
        "status": "fail" if errors else ("warn" if warnings else "pass"),
        "direction": direction,
        "summary": {
            "errors": errors, "warnings": warnings,
            "segments_source": len(segments),
            "segments_result": len(target_ids),
            "locked_terms": len(locked_terms(glossary)),
            "exit_code": 1 if errors else 0,
        },
        "computed_preservation": computed,
        "checks": checks,
        "findings": findings.items,
        "limitations": [
            "Number/unit comparison ignores numbers inside formulas, URLs, code and [n] citations.",
            "Negation and modality are cue-based screens; idioms and scope need human review.",
            "A pass means no detected defect, not a guarantee of translation quality.",
            "The audit is advisory and must never block delivery or delete real translation text.",
        ],
    }


# ---------------------------------------------------------------------------
# preserved_facts / evidence_map (numbers and claims back to their locator)
# ---------------------------------------------------------------------------

def ref_records(text):
    """Language-neutral reference records: Fig. 2a and 图 2a share fig:2a."""
    out = []
    for kind, pattern in (("fig", FIG_RE), ("tab", TAB_RE), ("eq", EQ_RE)):
        for match in pattern.finditer(text or ""):
            number = re.sub(r"\s+", "", match.group(1))
            number = re.sub(r"[–—−~]", "-", number).lower()
            out.append({"kind": kind, "key": kind + ":" + number,
                        "display": one_line(match.group(0))})
    for match in SEC_RE.finditer(text or ""):
        number = (match.group(1) or match.group(2) or "").lower()
        out.append({"kind": "sec", "key": "sec:" + number,
                    "display": one_line(match.group(0))})
    return out


def _cite_records(text):
    records = []
    for match in NUM_CITE_RE.finditer(text or ""):
        inner = re.sub(r"\s+", "", match.group(1))
        inner = re.sub(r"[–—−]", "-", inner).replace("，", ",")
        records.append({"key": "[" + inner + "]", "display": match.group(0)})
    return records


def _term_target_for(term, text):
    case_sensitive = term.get("case_sensitive", is_acronym(term["source"]))
    for variant in [term.get("target")] + term.get("aliases", []):
        if variant and term_occurs(variant, text,
                                   case_sensitive or is_acronym(variant)):
            return variant
    return None


def extract_preserved_facts(segments, targets, glossary):
    """Return concrete facts that survived translation, each with a locator."""
    glossary = normalize_glossary(glossary)
    facts = []
    max_facts = 300

    def add(kind, source, target, locator, **extra):
        if len(facts) >= max_facts:
            return
        item = {"kind": kind, "source": source, "target": target,
                "locator": locator}
        item.update({k: v for k, v in extra.items() if v not in (None, "", [])})
        facts.append(item)

    for seg in segments:
        sid = seg["segment_id"]
        source = seg.get("source", "")
        target = targets.get(sid, "") or ""
        if not target.strip():
            continue
        masked_src, src_tokens = build_masked(source)
        masked_tgt, tgt_tokens = build_masked(target)

        for term in glossary["terms"]:
            if not term.get("target"):
                continue
            case_sensitive = term.get("case_sensitive", is_acronym(term["source"]))
            if not term_occurs(term["source"], source, case_sensitive):
                continue
            used = _term_target_for(term, target)
            add("term", term["source"], used or term["target"], sid,
                locked=bool(term.get("locked")))

        src_numbers = extract_numbers(masked_src)
        tgt_numbers = extract_numbers(masked_tgt)
        pools = {}
        for item in tgt_numbers:
            pools.setdefault(item["value"], []).append(item)
        for item in src_numbers:
            matches = pools.get(item["value"]) or []
            match = matches.pop(0) if matches else None
            add("number", item["raw"],
                match["raw"] if match else None, sid)
            if item["unit"]:
                add("unit", item["unit"],
                    match["unit"] if match and match["unit"] else None, sid)

        # Citations are masked out of ``masked_*`` so use the raw pair texts.
        src_cites = _cite_records(source)
        tgt_cites = _cite_records(target)
        tgt_pool = {}
        for item in tgt_cites:
            tgt_pool.setdefault(item["key"], []).append(item)
        for item in src_cites:
            matches = tgt_pool.get(item["key"]) or []
            match = matches.pop(0) if matches else None
            add("citation", item["key"],
                match["display"] if match else None, sid)

        src_ay = extract_author_year(masked_src)
        tgt_ay = extract_author_year(masked_tgt)
        for key in sorted(src_ay):
            add("citation_author_year", key,
                key if key in tgt_ay else None, sid)

        src_refs = ref_records(masked_src)
        tgt_refs = ref_records(masked_tgt)
        tgt_ref_pool = {}
        for item in tgt_refs:
            tgt_ref_pool.setdefault(item["key"], []).append(item)
        for item in src_refs:
            matches = tgt_ref_pool.get(item["key"]) or []
            match = matches.pop(0) if matches else None
            add("reference", item["display"],
                match["display"] if match else None, sid, ref_kind=item["kind"])

        for key in sorted(src_tokens["formula"]):
            add("formula", key,
                key if key in tgt_tokens["formula"] else None, sid)
        for key in sorted(src_tokens["url"]):
            add("url", key,
                key if key in tgt_tokens["url"] else None, sid)
        for key in sorted(src_tokens["code"]):
            add("code", key,
                key if key in tgt_tokens["code"] else None, sid)

        neg_src = negation_cues(masked_src)
        neg_tgt = negation_cues(masked_tgt)
        if neg_src:
            add("negation", " / ".join(dict.fromkeys(neg_src)) or "negation",
                " / ".join(dict.fromkeys(neg_tgt)) if neg_tgt else None, sid)
        src_profile = modality_profile(masked_src)
        tgt_profile = modality_profile(masked_tgt)
        src_level = strength_level(src_profile)
        if src_level:
            add("modality", "level %d" % src_level,
                "level %d" % strength_level(tgt_profile), sid,
                source_cues=list(dict.fromkeys(
                    cue for cues in src_profile.values() for cue in cues)),
                target_cues=list(dict.fromkeys(
                    cue for cues in tgt_profile.values() for cue in cues)))
    return facts


def split_sentences(text):
    pieces = re.split(
        r"(?<=[。！？!?；;])\s*|(?<=\.)\s+(?=[A-Z(\[])|[ \t]*\n+",
        text or "")
    return [one_line(piece) for piece in pieces if piece and piece.strip()]


def _sentence_score(text):
    score = 0
    if re.search(r"\d", text):
        score += 3
    if NUM_CITE_RE.search(text) or re.search(
            r"\(\s*[A-Z][^()]{0,60}\d{4}[a-z]?\s*\)", text):
        score += 2
    if strength_level(modality_profile(text)):
        score += 2
    if negation_cues(text):
        score += 1
    if len(text) >= 16:
        score += 1
    return score


def build_evidence_map(segments, targets, max_claims=60):
    """Map the strongest target claims back to their source sentence."""
    claims = []
    for seg in segments:
        sid = seg["segment_id"]
        target = targets.get(sid, "") or ""
        if not target.strip():
            continue
        src_sentences = split_sentences(seg.get("source", ""))
        tgt_sentences = split_sentences(target)
        scored = []
        for index, sentence in enumerate(src_sentences):
            score = _sentence_score(sentence)
            if score < 2:
                continue
            tgt_sentence = tgt_sentences[index] if index < len(tgt_sentences) else ""
            scored.append((score, index, sentence, tgt_sentence))
        scored.sort(key=lambda row: (-row[0], row[1]))
        for score, index, source_sentence, target_sentence in scored[:2]:
            claims.append({
                "claim_id": "C%02d" % (len(claims) + 1),
                "claim": excerpt(target_sentence or source_sentence, 300),
                "source": sid,
                "locator": "%s sentence %d" % (sid, index + 1),
                "quote": excerpt(source_sentence, 500),
                "paraphrase": excerpt(target_sentence, 500) if target_sentence else "",
            })
            if len(claims) >= max_claims:
                return claims
    if claims:
        return claims
    # A complete translation with no numeric/modality cue still gets at least
    # one real, locatable claim; never fabricate a quote.
    for seg in segments:
        target = targets.get(seg["segment_id"], "") or ""
        if not target.strip():
            continue
        source_sentences = split_sentences(seg.get("source", ""))
        target_sentences = split_sentences(target)
        source_sentence = source_sentences[0] if source_sentences else seg["source"]
        target_sentence = target_sentences[0] if target_sentences else target
        claims.append({
            "claim_id": "C01",
            "claim": excerpt(target_sentence, 300),
            "source": seg["segment_id"],
            "locator": "%s sentence 1" % seg["segment_id"],
            "quote": excerpt(source_sentence, 500),
            "paraphrase": excerpt(target_sentence, 500),
        })
        break
    return claims


# ---------------------------------------------------------------------------
# artifacts / result assembly
# ---------------------------------------------------------------------------

ARTIFACT_ROLES = [
    ("translation", "translation.md"),
    ("editable", "bilingual.md"),
    ("glossary", "glossary.json"),
    ("notes", "translation-notes.json"),
    ("report", "translation-report.md"),
    ("audit", "translation-check.json"),
]


def collect_artifacts(root):
    root = Path(root)
    artifacts = []
    for role, name in ARTIFACT_ROLES:
        path = root / name
        if path.is_file() and path.stat().st_size > 0:
            artifacts.append({
                "role": role,
                "path": name,
                "format": "json" if name.endswith(".json") else "markdown",
            })
    return artifacts


def _unique_strings(values):
    out = []
    for value in values or []:
        if isinstance(value, str) and value.strip() and value not in out:
            out.append(value.strip())
    return out


def build_result(case_id, direction, segments, targets, glossary, artifacts,
                 status="completed", untranslated=None, uncertainty=None,
                 change_log=None, audit_report=None, translation_text="",
                 deliverable_path="translation.md", case=None,
                 back_translation_check=None):
    """Assemble result.json with task fields and the four official aliases."""
    glossary = normalize_glossary(glossary)
    targets = targets or {}
    untranslated = [item for item in (untranslated or [])
                    if isinstance(item, dict)]
    uncertainty = _unique_strings(uncertainty)
    change_log = [item for item in (change_log or [])
                  if isinstance(item, dict)]

    terms = glossary["terms"]
    segment_entries = []
    for seg in segments:
        sid = seg["segment_id"]
        target = targets.get(sid, "") or ""
        used = []
        for term in terms:
            if not term.get("target"):
                continue
            case_sensitive = term.get("case_sensitive",
                                      is_acronym(term["source"]))
            if term_occurs(term["source"], seg.get("source", ""), case_sensitive):
                used.append(term["source"])
        flags = []
        if not target.strip():
            flags.append("untranslated")
        segment_entries.append({
            "segment_id": sid,
            "type": seg.get("type", "paragraph"),
            "source": seg.get("source", ""),
            "target": target,
            "terms": used,
            "added": [],
            "omitted": [],
            "flags": flags,
            "confidence": "high" if target.strip() else "low",
        })

    missing_ids = [seg["segment_id"] for seg in segments
                   if not (targets.get(seg["segment_id"]) or "").strip()]
    known_untranslated = {item.get("segment_id") for item in untranslated}
    for sid in missing_ids:
        if sid not in known_untranslated:
            untranslated.append({
                "segment_id": sid,
                "reason": "no target text was found in translation.md/bilingual.md",
            })
    if status == "completed" and (untranslated or missing_ids):
        status = "partial"
    if status == "completed" and not any(
            (targets.get(seg["segment_id"]) or "").strip() for seg in segments):
        status = "partial"
        if not uncertainty:
            uncertainty.append("no translated segments were available")
    if status in ("partial", "input_error") and not untranslated and not uncertainty:
        uncertainty.append(
            "status_%s: no detailed limitation was recorded; see "
            "work/finalize-summary.json" % status)

    computed = (audit_report or {}).get("computed_preservation") or {}
    preservation = {key: bool(computed.get(key, False))
                    for key in PRESERVATION_KEYS}
    audit_summary = (audit_report or {}).get("summary") or {}
    if audit_report:
        errors = int(audit_summary.get("errors") or 0)
        warnings = int(audit_summary.get("warnings") or 0)
        if errors and status == "completed":
            note = ("audit found %d error(s); see translation-check.json "
                    "(heuristic audit, delivery not blocked)" % errors)
            if note not in uncertainty:
                uncertainty.append(note)
        translation_check = {
            "path": "translation-check.json",
            "exit_code": 1 if errors else 0,
            "errors": errors,
            "warnings": warnings,
            "note": "optional audit; a non-zero exit never blocks delivery",
        }
    else:
        translation_check = {
            "path": None, "exit_code": None, "errors": None,
            "warnings": None,
            "note": "audit_unavailable: record the reason in uncertainty",
        }

    facts = extract_preserved_facts(segments, targets, glossary)
    evidence = build_evidence_map(segments, targets)
    if not change_log:
        change_log.append({
            "action": "finalize",
            "scope": "result.json",
            "reason": "assembled from translation.md/bilingual.md",
            "human_review": False,
        })
    change_log.append({
        "action": "assemble",
        "scope": "official fields",
        "reason": ("deliverable/preserved_facts/evidence_map/change_log generated "
                   "from real files by scripts/main.py finalize"),
        "human_review": False,
    })

    deliverable = {
        "path": deliverable_path or None,
        "format": "markdown",
        "role": "target-language translation",
        "segments": len(segments),
    }
    result = {
        "case_id": case_id,
        "task_family": "translation",
        "mode": "translation",
        "status": status,
        "direction": direction,
        "source_language": direction.split("->")[0],
        "target_language": direction.split("->")[-1],
        "artifacts": artifacts,
        "register": glossary.get("register") or default_register(),
        "segments": segment_entries,
        "glossary": [
            {"source": term["source"], "target": term["target"],
             "locked": bool(term.get("locked"))}
            for term in terms if term.get("target")
        ],
        "preservation": preservation,
        "back_translation_check": back_translation_check or [],
        "untranslated": untranslated,
        "uncertainty": uncertainty,
        "change_log": change_log,
        "translation_check": translation_check,
        "deliverable": deliverable,
        "preserved_facts": facts,
        "evidence_map": evidence,
    }

    case = case if isinstance(case, dict) else {}
    contract = case.get("output_contract")
    if isinstance(contract, dict):
        for key, value in contract.items():
            if key not in result:
                result[key] = value
    for key in ("source", "source_language", "target_language"):
        if key in case and key not in result:
            result[key] = case[key]
    return result


def validate_result_paths(result, root):
    """Return a list of path/field problems (empty means structurally sound)."""
    root = Path(root)
    problems = []
    deliverable = result.get("deliverable")
    if not isinstance(deliverable, dict):
        problems.append("deliverable is not an object")
    else:
        rel = deliverable.get("path")
        if not isinstance(rel, str) or not rel.strip():
            problems.append("deliverable.path is empty")
        elif not (root / rel).is_file():
            problems.append("deliverable.path does not exist: %s" % rel)
    for index, artifact in enumerate(result.get("artifacts") or []):
        if not isinstance(artifact, dict) or not artifact.get("path"):
            problems.append("artifacts[%d] has no path" % index)
            continue
        path = root / str(artifact["path"])
        if not path.is_file():
            problems.append("artifact does not exist: %s" % artifact["path"])
        elif path.stat().st_size == 0:
            problems.append("artifact is empty: %s" % artifact["path"])
    for index, fact in enumerate(result.get("preserved_facts") or []):
        if not isinstance(fact, dict):
            problems.append("preserved_facts[%d] is not an object" % index)
            continue
        for key in ("kind", "source", "locator"):
            if not fact.get(key):
                problems.append("preserved_facts[%d] lacks %s" % (index, key))
    for index, claim in enumerate(result.get("evidence_map") or []):
        if not isinstance(claim, dict):
            problems.append("evidence_map[%d] is not an object" % index)
            continue
        for key in ("claim_id", "claim", "source", "locator"):
            if not claim.get(key):
                problems.append("evidence_map[%d] lacks %s" % (index, key))
        if not (claim.get("quote") or claim.get("paraphrase")):
            problems.append("evidence_map[%d] lacks quote/paraphrase" % index)
    if not result.get("change_log"):
        problems.append("change_log is empty")
    return problems

