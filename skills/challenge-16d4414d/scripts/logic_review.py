#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full-text segmentation and deterministic argument-review candidates.

Task-specific precheck for the logic/argument review challenge.  It reads the
actual material under ``--input`` (Markdown, text, TeX, JSON, CSV, bib/ris;
PDF/DOCX text is reused from ``work/materials.json`` after
``run_case.py prepare``), segments the full scientific text into sections,
paragraphs and sentences, and extracts:

* a claim inventory (speaker, claim type, strength, support status);
* cross-section promise/delivery checks;
* typed candidate findings for explicit argument problems.

The script never decides that a paper is defective: every candidate carries a
verbatim quote, a locator, an impact statement, an actionable revision and a
false-positive check.  A candidate is ``confirmed`` only when two verbatim
passages contradict each other (for example "randomly assigned" vs "allocated
by alternating floors"); interpretive candidates are emitted as
``needs_recheck`` so the model can accept, rewrite or reject them.

Outputs (always written for empty/broken input, exit code 0)::

    work/logic-check.json         full deterministic result
    work/review-candidates.json   merge-ready candidate payload
    output/logic-check.json       copy for the result bundle
    output/logic-precheck.md      human-readable review sheet

Usage::

    python3 logic_review.py --input /input --work /app/work --output /app/output
    python3 logic_review.py --merge
    python3 logic_review.py --selftest
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:  # parsers.py is packaged next to this script; text-only input still works.
    import parsers  # type: ignore
except Exception:  # noqa: BLE001
    parsers = None

VERSION = "1.0.0"
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".org", ".tex", ".latex",
                 ".json", ".csv", ".yaml", ".yml", ".bib", ".ris"}
BINARY_SUFFIXES = {".pdf", ".docx", ".doc"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".webp",
                  ".eps", ".gif"}
SKIP_NAMES = {"case.json", "task.md", "readme.md"}
MAX_DOC_CHARS = 4_000_000
MAX_CLAIMS = 240

FACETS = ["fact_consistency", "citation_support", "method_design",
          "statistical_reporting", "argument_chain", "academic_compliance"]
LOGIC_FACETS = ["claim_extraction", "evidence_support", "reasoning_chain",
                "causal_inference", "cross_section_consistency",
                "severity_and_fix"]

MD_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$")
TEX_HEADING_RE = re.compile(r"^\s*\\(?:sub)*section\*?\s*\{([^}]*)\}")
NUMBER_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s*")
PLAIN_HEADING_RE = re.compile(
    r"^(?:abstract|summary|introduction|background|related work|"
    r"methods?|methodology|materials and methods|materials & methods|"
    r"experimental section|experimental procedures?|experimental methods?|"
    r"study design|data and methods|participants and methods|"
    r"results?|findings?|results and discussion|experimental results|"
    r"main results|discussion|conclusions?|concluding remarks|limitations?|"
    r"references?|bibliography|reference list|works cited|literature cited|"
    r"摘要|引言|前言|背景|方法|材料与方法|研究方法|实验部分|"
    r"结果|结果与讨论|讨论|结论|总结|局限|参考文献)$", re.I)
SECTION_STOP_RE = re.compile(
    r"^(?:abstract|summary|introduction|background|methods?|materials? and "
    r"methods?|results?|discussion|conclusions?|limitations?|references?|"
    r"bibliography|acknowledg(?:e)?ments?|appendix|supplementary)\s*[:：]?$",
    re.I)

SENT_BOUNDARY_RE = re.compile(
    r"(?<=[.!?。！？])\s+(?=[A-Z0-9一-鿿(\[])"
    r"|(?<=[。！？；])(?=[\u4e00-\u9fffA-Z0-9])")
ABBREVIATIONS = ("e.g.", "i.e.", "et al.", "vs.", "fig.", "figs.", "eq.",
                 "eqs.", "ref.", "refs.", "no.", "nos.", "dr.", "prof.",
                 "approx.", "cf.", "sec.", "tab.", "vol.", "pp.", "p.",
                 "resp.", "suppl.")

AIM_RE = re.compile(
    r"\b(?:we\s+(?:investigated|examined|tested|aimed|sought|hypothesi[sz]ed|"
    r"asked|set\s+out)|this\s+study\s+(?:aims?|investigates?|examines?|"
    r"tests?)|our\s+(?:aim|objective|hypothesis)|objective\s+was)\b|"
    r"研究目的|本研究|我们(?:研究|检验|假设|考察|调查)", re.I)
RESULT_CUE_RE = re.compile(
    r"\b(?:we\s+found|we\s+observed|results?\s+(?:show|showed|indicate|"
    r"indicated|reveal|revealed|suggest|suggested)|there\s+(?:was|were)|"
    r"participants?\s+(?:showed|reported|improved)|all\s+participants|"
    r"significantly?\s+(?:improved|increased|decreased|reduced|higher|lower)|"
    r"was\s+associated\s+with|correlated\s+with|predicted)\b|"
    r"结果(?:显示|表明)|发现|显著(?:提高|降低|增加|减少)|相关|改善", re.I)
STAT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|‰|percent)|p\s*[=<>]|"
                     r"r\s*=|\bbeta\b|β\s*=|95\s*%\s*CI|置信区间", re.I)
CAUSAL_RE = re.compile(
    r"\b(?:caus(?:e|es|ed|ing)|lead(?:s|ing)?\s+to|result(?:s|ed)?\s+in|"
    r"due\s+to|driv(?:e|es|ing)|gives?\s+rise\s+to|because\s+of)\b|"
    r"导致|引起|造成|使得|因为", re.I)
ASSOCIATION_RE = re.compile(
    r"\b(?:associated|association|correlat(?:e|ed|ion)|related|relationship|"
    r"predict(?:s|ed|or)|linked)\b|相关|关联|关系|预测", re.I)
MECHANISM_RE = re.compile(
    r"\b(?:mediat(?:e|es|ed|ion|ing)|mechanism|pathway|explains?|"
    r"explained\s+by|underlying\s+process)\b|中介|机制|通路|解释", re.I)
GENERAL_RE = re.compile(
    r"\b(?:generaliz(?:e|es|ed|able|ation)|all\s+(?:young\s+)?adults|"
    r"everyone|all\s+populations|universal(?:ly)?|broadly\s+applicable|"
    r"in\s+all\s+(?:cases|settings|groups))\b|普遍适用|所有(?:年轻)?成年人|"
    r"推广到|适用于所有|所有人", re.I)
PROOF_RE = re.compile(
    r"\b(?:prove[sd]?|establish(?:es|ed)?|demonstrat(?:e|es|ed)|"
    r"confirm(?:s|ed)?|definitive(?:ly)?|definitely|always|never)\b|"
    r"证明|证实|确定|必然|总是", re.I)
HEDGE_RE = re.compile(
    r"\b(?:may|might|could|suggest(?:s|ed|ive)?|possibly|likely|"
    r"potentially|appears?|seems?|association|associated|correlat\w*|"
    r"consistent\s+with|preliminary)\b|可能|或许|提示|似乎|相关|关联|初步", re.I)
ASSUMPTION_RE = re.compile(
    r"\b(?:we\s+assum(?:e|ed)|assum(?:e|ption|ing)|hypothetical(?:ly)?|"
    r"for\s+the\s+(?:purpose|sake)\s+of|if\s+we\s+assume|"
    r"suppose|supposing|thought\s+experiment)\b|假设|假定|设想|为了.*目的", re.I)
CITED_CUE_RE = re.compile(
    r"\b(?:previous\s+work|prior\s+work|earlier\s+work|"
    r"others?\s+have|studies?\s+have|researchers?\s+have|"
    r"\w+\s+et\s+al\.?|according\s+to|as\s+reported\s+by|"
    r"cited\s+in|reviewed\s+in)\b|已有研究|前人研究|据.*报道|引用", re.I)
DEFINITION_RE = re.compile(
    r"\b(?P<term>[A-Za-z][A-Za-z'\- ]{1,50}?)\s+(?:was|is|are|were)\s+"
    r"(?:defined|operationali[sz]ed|conceptualized)\s+as\s+(?P<definition>.+)",
    re.I)
MEASURED_BY_RE = re.compile(
    r"\b(?:measured|operationali[sz]ed|assessed|indexed|proxied)\s+by\s+"
    r"(?P<measure>.+?)(?:\.|;|$)", re.I)
LIMITATION_RE = re.compile(
    r"\b(?:cannot\s+(?:be\s+)?infer|no\s+causal|causal\s+inference\s+"
    r"(?:cannot|is\s+limited)|association\s+does\s+not\s+imply|"
    r"cross[- ]sectional\s+(?:design|data)|observational\s+design|"
    r"limitation(?:s)?\s+(?:of|include)|not\s+causal|"
    r"we\s+did\s+not\s+(?:measure|collect|conduct))\b|"
    r"无法推断|不能推断|相关不代表因果|横断面|观察性|局限", re.I)
DESIGN_OBS_RE = re.compile(
    r"\b(?:cross[- ]sectional|observational|survey|cohort|case[- ]control|"
    r"retrospective|secondary\s+(?:data|analysis)|archival|"
    r"correlational\s+design)\b|横断面|观察性|调查|队列|病例对照|回顾性", re.I)
DESIGN_RANDOM_RE = re.compile(
    r"\b(?:randomi[sz]ed|randomi[sz]ation|random\s+allocation|"
    r"randomly\s+(?:assigned|allocated))\b|随机(?:分组|分配|化)", re.I)
DESIGN_INTERVENTION_RE = re.compile(
    r"\b(?:intervention|training\s+(?:program|group)|treatment\s+group|"
    r"trial|waitlist|control\s+group|active\s+control|"
    r"pre[- ]?post)\b|干预|训练组|治疗组|试验|对照组|等待组", re.I)
ALTERNATION_RE = re.compile(
    r"\b(?:alternat(?:e|ed|ely|ion|ing)|every\s+other|sequential\s+"
    r"allocation|odd[- ]even|by\s+floor|by\s+week)\b|交替|轮流|奇偶|"
    r"按楼层|按周", re.I)
NO_MEDIATION_RE = re.compile(
    r"\b(?:did\s+not|didn't|no|not)\s+(?:conduct|perform|run|test|model)\w*"
    r"[^.\n]{0,60}\b(?:mediation|mediat(?:e|or)|path\s+analysis)\b|"
    r"no\s+mediation\s+analysis|mediation\s+(?:was\s+)?not\s+"
    r"(?:conducted|tested|performed)|未.{0,12}(?:中介|中介分析)|"
    r"没有.{0,12}中介", re.I)
NO_INTERVENTION_RE = re.compile(
    r"\bno\s+(?:intervention|manipulation|treatment)\s+was\s+"
    r"(?:tested|administered|delivered)|"
    r"(?:intervention|manipulation)\s+was\s+not\s+(?:tested|used)|"
    r"did\s+not\s+(?:test|administer|deliver|implement)[^.\n]{0,40}"
    r"(?:intervention|manipulation|training)|"
    r"未(?:实施|进行|测试).{0,12}(?:干预|训练)|无干预", re.I)
CIRCULAR_MEASURE_RE = re.compile(
    r"(?:defined|operationali[sz]ed|conceptualized)\s+as\s+([^.]+?)\s+"
    r"(?:and|;)\s*(?:was|is|were|are)?\s*(?:measured|assessed|indexed|"
    r"operationali[sz]ed)\s+by\s+([^.]+)", re.I)
CLAIM_VERB_RE = re.compile(
    r"\b(?:found|finds?|reported?|reports?|shows?|shown|demonstrat\w*|"
    r"establish\w*|improv\w*|increas\w*|decreas\w*|reduc\w*|persist\w*|"
    r"caus\w*|prove[sd]?|confirm\w*|suggest\w*|predict\w*|affect\w*)\b|"
    r"发现|表明|显示|证明|证实|提高|降低|减少|增加|导致|改善|持续|影响", re.I)
DISCLAIMER_RE = re.compile(
    r"(synthetic\s+only|fabricated|not\s+a\s+real\s+paper|for\s+audit\s+"
    r"testing|仅供测试|示例|虚构)", re.I)
REF_LABEL_RE = re.compile(r"\[(\d{1,3}|[A-Za-z])\]")
REF_ENTRY_RE = re.compile(
    r"^\s*(?:\[(?P<label>[^\]]{1,40})\]|(?P<num>\d{1,3})[.)])\s+"
    r"(?P<rest>.+)$")
AUTHOR_YEAR_CITE_RE = re.compile(
    r"\b([A-Z][A-Za-z'\-]+)(?:\s+et\s+al\.?)?\s*\((19|20)\d{2}\)")


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
    return re.sub(r"\s+", " ", str(text or "")).strip()


def quote_in(source, quote):
    return bool(norm_space(quote)) and norm_space(quote) in norm_space(source)


def tokens(text):
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]{3,}", text or "")]


def sentence_units(text):
    """Return exact sentence substrings so quotes remain verbatim."""
    flat = str(text or "").replace("\r", " ").replace("\n", " ")
    protected = flat
    for abbr in sorted(ABBREVIATIONS, key=len, reverse=True):
        protected = re.sub(re.escape(abbr),
                           lambda m: m.group(0).replace(".", "\u2024"),
                           protected, flags=re.I)
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
        raw = flat[a:b]
        piece = raw.strip()
        if piece:
            out.append(piece)
    return out or ([flat.strip()] if flat.strip() else [])


def canonical_section(heading):
    text = NUMBER_PREFIX_RE.sub("", norm_space(heading)).strip(" .:：")
    low = text.lower()
    names = []
    checks = [
        ("abstract", r"\babstract\b|\bsummary\b|摘要"),
        ("introduction", r"\bintroduction\b|\bbackground\b|\brelated\s+work\b|引言|前言|背景"),
        ("methods", r"\bmethods?\b|\bmethodology\b|materials and methods|"
                    r"materials & methods|experimental (?:section|procedures?|methods?)|"
                    r"study design|data and methods|participants and methods|方法|材料与方法|实验部分"),
        ("results", r"\bresults?\b|\bfindings?\b|experimental results|main results|结果"),
        ("discussion", r"\bdiscussion\b|讨论"),
        ("conclusion", r"\bconclusions?\b|concluding remarks|结论|总结"),
        ("limitations", r"\blimitations?\b|局限"),
        ("references", r"\breferences?\b|\bbibliography\b|reference list|"
                        r"works cited|literature cited|参考文献"),
    ]
    for name, pattern in checks:
        if re.search(pattern, low):
            names.append(name)
    return names


def plain_heading(line):
    text = NUMBER_PREFIX_RE.sub("", norm_space(line)).strip(" .:：")
    if not text or len(text) > 80:
        return None
    if PLAIN_HEADING_RE.match(text):
        return text
    return None


def parse_document(doc):
    """Segment one material into sections, paragraphs and sentences."""
    text = str(doc.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    paragraphs, section_names, captions = [], [], []
    current = ["body"]
    buf, start_line = [], None
    used_ids, unlabeled = set(), [0]
    explicit_mode = any(re.match(r"^\s*P\d+[.)]\s+", line) for line in lines)

    def new_id(preferred=None):
        if preferred and preferred not in used_ids:
            used_ids.add(preferred)
            return preferred
        if explicit_mode:
            unlabeled[0] += 1
            pid = "U%d" % unlabeled[0]
            while pid in used_ids:
                unlabeled[0] += 1
                pid = "U%d" % unlabeled[0]
            used_ids.add(pid)
            return pid
        index = len(used_ids) + 1
        while "P%d" % index in used_ids:
            index += 1
        pid = "P%d" % index
        used_ids.add(pid)
        return pid

    def flush(end_line):
        nonlocal buf, start_line
        raw = "\n".join(buf).strip()
        first_line = start_line or 0
        buf, start_line = [], None
        if not raw:
            return
        explicit = re.match(r"^P(\d+)[.)]\s+(.+)$", raw, re.S)
        pid = new_id("P%d" % int(explicit.group(1)) if explicit else None)
        content = explicit.group(2).strip() if explicit else raw
        kind = "caption" if re.match(
            r"^\s*(?:figure|fig\.?|table|图|表)\s*\d+", content, re.I) else "paragraph"
        para = {"id": pid, "path": doc.get("path") or "material",
                "section": current[0], "sections": list(current),
                "text": content, "kind": kind,
                "locator": ("%s (line %d)" % (pid, first_line)
                            if first_line else pid),
                "line_start": first_line, "line_end": end_line or 0}
        paragraphs.append(para)
        if kind == "caption":
            captions.append(para)

    for index, line in enumerate(lines, 1):
        heading = None
        match = MD_HEADING_RE.match(line)
        if match:
            heading = match.group(2)
        else:
            match = TEX_HEADING_RE.match(line)
            if match:
                heading = match.group(1)
            else:
                heading = plain_heading(line)
        if heading is not None:
            flush(index - 1)
            names = canonical_section(heading)
            if names:
                current = names
                for name in names:
                    if name not in section_names:
                        section_names.append(name)
            continue
        if start_line is not None and re.match(r"^\s*P\d+[.)]\s+", line):
            flush(index - 1)
        if not line.strip():
            flush(index - 1)
            continue
        if start_line is None:
            start_line = index
        buf.append(line.rstrip("\n"))
    flush(len(lines))
    for para in paragraphs:
        para["sentences"] = sentence_units(para["text"])
    return {"paragraphs": paragraphs, "section_names": section_names,
            "captions": captions, "chars": len(text), "lines": len(lines)}


def load_material_text(work_dir, rel_path):
    materials = load_json(Path(work_dir) / "materials.json", {})
    for doc in materials.get("documents") or []:
        if str(doc.get("path") or "") == rel_path and doc.get("text"):
            return str(doc["text"])
    return ""


def scan_input(input_dir, work_dir):
    docs, assets = [], []
    input_dir = Path(input_dir)
    if not input_dir.exists():
        return docs, assets
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(input_dir).as_posix()
        if (path.name.lower() in SKIP_NAMES or "__pycache__" in path.parts
                or path.name.startswith(".")):
            continue
        suffix = path.suffix.lower()
        if suffix in ASSET_SUFFIXES:
            assets.append(rel)
            continue
        if suffix in BINARY_SUFFIXES:
            text = ""
            if parsers is not None:
                try:
                    extracted = parsers.extract_any(path)
                    text = extracted.get("text") or ""
                except Exception:  # noqa: BLE001
                    text = ""
            if not text:
                text = load_material_text(work_dir, rel)
            docs.append({"path": rel, "kind": suffix.lstrip("."), "text": text,
                         "warnings": [] if text else
                         ["unreadable binary material; run run_case.py prepare"]})
            continue
        if suffix in TEXT_SUFFIXES:
            if path.stat().st_size > MAX_DOC_CHARS:
                docs.append({"path": rel, "kind": suffix.lstrip("."), "text": "",
                             "warnings": ["file larger than %d bytes" % MAX_DOC_CHARS]})
                continue
            docs.append({"path": rel, "kind": suffix.lstrip("."),
                         "text": read_text(path), "warnings": []})
    return docs, assets


def choose_primary(docs):
    ranked = []
    for doc in docs:
        text = str(doc.get("text") or "")
        if len(text.strip()) < 40 or doc.get("kind") in ("bib", "ris", "csv"):
            continue
        parsed = parse_document(doc)
        score = min(len(text), 200000) / 1000.0 + 25 * len(parsed["section_names"])
        name = (doc.get("path") or "").lower()
        if any(word in name for word in ("manuscript", "paper", "article",
                                         "source", "material", "primary", "main")):
            score += 30
        if doc.get("kind") in ("pdf", "docx", "tex", "md", "text"):
            score += 15
        ranked.append((score, doc))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1] if ranked and ranked[0][0] > 0 else None

def paras_in(parsed, name):
    return [p for p in parsed["paragraphs"]
            if name in (p.get("sections") or [p.get("section")])]


def sentence_items(paras):
    for para in paras:
        for sentence in para.get("sentences") or sentence_units(para.get("text")):
            yield para, sentence


def unit(para, sentence=None):
    quote = (sentence or para.get("text") or "").strip()
    return {"path": para.get("path") or "material",
            "locator": para.get("locator") or "locator", "quote": quote}


def first_match(paras, pattern):
    for para, sentence in sentence_items(paras):
        match = pattern.search(sentence)
        if match:
            return para, sentence, match
    return None, None, None


def full_text(parsed):
    return " ".join((p.get("text") or "") for p in parsed["paragraphs"])


def detect_design(parsed):
    """Return (state, para, sentence, kind) for the methods design."""
    methods = paras_in(parsed, "methods")
    scope = methods or parsed["paragraphs"]
    for para, sentence in sentence_items(scope):
        if DESIGN_OBS_RE.search(sentence):
            return "observational", para, sentence, "observational"
    for para, sentence in sentence_items(scope):
        if ALTERNATION_RE.search(sentence) and re.search(
                r"assign|allocat|group|floor|week|order|分配|分组|顺序", sentence, re.I):
            return "nonrandom", para, sentence, "alternation"
    for para, sentence in sentence_items(scope):
        if DESIGN_RANDOM_RE.search(sentence):
            return "randomized", para, sentence, "random"
    for para, sentence in sentence_items(scope):
        if DESIGN_INTERVENTION_RE.search(sentence):
            return "intervention", para, sentence, "intervention"
    return "unclear", None, None, ""


def detect_no_mediation(parsed):
    text = full_text(parsed)
    match = NO_MEDIATION_RE.search(text)
    if not match:
        return None
    for para, sentence in sentence_items(parsed["paragraphs"]):
        if match.group(0)[:40] in sentence or NO_MEDIATION_RE.search(sentence):
            return para, sentence
    return None


def detect_no_intervention(parsed):
    for para, sentence in sentence_items(parsed["paragraphs"]):
        if NO_INTERVENTION_RE.search(sentence):
            return para, sentence
    return None


def parse_references(parsed):
    """Map citation labels / author-year keys to reference entry text."""
    refs = {}
    for para in paras_in(parsed, "references"):
        for line in (para.get("text") or "").splitlines():
            text = line.strip()
            if not text:
                continue
            match = REF_ENTRY_RE.match(text)
            if match:
                label = (match.group("label") or match.group("num") or "").strip()
                refs[normalize_label(label)] = {
                    "label": label, "text": text, "para": para}
                continue
            author = AUTHOR_YEAR_CITE_RE.search(text)
            if author:
                refs[author.group(1).upper()] = {
                    "label": author.group(0), "text": text, "para": para}
    return refs


def normalize_label(label):
    value = str(label or "").strip()
    if value.isdigit():
        return str(int(value))
    return value.upper()


def citation_keys(sentence):
    keys = [normalize_label(x) for x in REF_LABEL_RE.findall(sentence)]
    keys += [m.group(1).upper() for m in AUTHOR_YEAR_CITE_RE.finditer(sentence)]
    return [key for key in keys if key]


def claim_speaker(sentence):
    low = sentence.lower()
    if ASSUMPTION_RE.search(sentence):
        return "assumption"
    if CITED_CUE_RE.search(sentence):
        return "cited"
    if REF_LABEL_RE.search(sentence) and not re.search(
            r"\b(?:we|our|this\s+study|these\s+findings)\b", low):
        if re.search(r"\b(?:report|reported|argue|argued|show|showed|found|"
                     r"describe|described|suggest|suggested)\b", low):
            return "cited"
    return "author"


def claim_kind(sentence, section):
    low = sentence.lower()
    if ASSUMPTION_RE.search(sentence):
        return "assumption"
    if DEFINITION_RE.search(sentence) or re.search(
            r"\b(?:defined|operationali[sz]ed|conceptualized)\s+as\b|定义为|操作化为",
            low):
        return "definition"
    if MECHANISM_RE.search(sentence):
        return "mediation"
    if GENERAL_RE.search(sentence):
        return "generalization"
    if CAUSAL_RE.search(sentence) and not HEDGE_RE.search(sentence):
        return "causal"
    if PROOF_RE.search(sentence):
        return "proof"
    if ASSOCIATION_RE.search(sentence):
        return "association"
    if AIM_RE.search(sentence) or re.search(r"hypothes|假设|研究问题", low):
        return "prediction"
    if STAT_RE.search(sentence) or RESULT_CUE_RE.search(sentence):
        return "result"
    if CLAIM_VERB_RE.search(sentence):
        return "substantive"
    return "background"


def claim_strength(sentence, kind):
    if ASSUMPTION_RE.search(sentence):
        return "assumption"
    if GENERAL_RE.search(sentence) or PROOF_RE.search(sentence):
        return "absolute" if not HEDGE_RE.search(sentence) else "suggestive"
    if kind == "causal":
        return "causal"
    if kind in ("association", "mediation"):
        return "associative"
    if HEDGE_RE.search(sentence):
        return "suggestive"
    return "descriptive"


def sample_sentence(parsed):
    for para, sentence in sentence_items(
            paras_in(parsed, "methods") + paras_in(parsed, "abstract")):
        if re.search(r"\b(?:participants?|students?|patients?|employees?|"
                     r"subjects?|volunteers?|mice|rats|samples?)\b|"
                     r"参与者|学生|患者|员工|受试者|样本", sentence, re.I) and \
                re.search(r"\d+|single|one\s+site|one\s+company|"
                          r"某|单中心|一家", sentence, re.I):
            return para, sentence
    return None, None


def assess_claim_support(claim, parsed, design, no_mediation, no_intervention,
                         refs):
    """Return (support, strength, basis, evidence_items, design_state)."""
    sentence = claim["claim"]
    section = claim["section"]
    kind = claim["type"]
    speaker = claim["speaker"]
    evidence = [claim["evidence"]]
    support = "unverifiable"
    basis = "没有找到可直接核对的本文证据；保持待复核。"
    design_state = design[0]

    if speaker == "assumption":
        return ("unverifiable", "assumption",
                "作者明确标注为假设，不作为事实主张；不能当作缺陷。",
                evidence, design_state)
    if speaker == "cited":
        keys = citation_keys(sentence)
        entry = next((refs[key] for key in keys if key in refs), None)
        if entry:
            evidence.append(unit(entry["para"], entry["text"]))
            if PROOF_RE.search(sentence) or kind in ("causal", "proof"):
                if re.search(r"qualitative|interview|commentary|opinion|"
                             r"association|correlat|no\s+intervention|"
                             r"not\s+support|unsupported|n\s*=\s*\d{1,3}\b|"
                             r"定性|访谈|评论|相关|无干预|不支持", entry["text"], re.I):
                    return ("none", "absolute",
                            "转述他人研究时使用了因果/证明措辞，但所引条目的设计或结论不支持该强度。",
                            evidence, design_state)
            return ("unverifiable", claim["strength"],
                    "引用观点只能在条目的可核验文本范围内判断；当前仅能确认条目存在。",
                    evidence, design_state)
        return ("unverifiable", claim["strength"],
                "引用观点没有可匹配的参考文献条目，无法判断来源是否支持。",
                evidence, design_state)

    if kind == "mediation":
        if no_mediation is not None:
            evidence.append(unit(no_mediation[0], no_mediation[1]))
            return ("none", "causal",
                    "正文提出中介/机制主张，但方法明确说明没有做中介分析。",
                    evidence, design_state)
        if re.search(r"mediat|mediation|中介", full_text(parsed), re.I):
            return ("partial", "associative",
                    "文中出现中介分析表述，仍需核对结果是否报告间接效应与置信区间。",
                    evidence, design_state)
        return ("none", "causal",
                "正文提出中介/机制主张，但方法或结果没有对应的中介分析。",
                evidence, design_state)

    if kind == "generalization":
        sample = sample_sentence(parsed)
        if sample[0] is not None:
            evidence.append(unit(sample[0], sample[1]))
            return ("none", "absolute",
                    "结论把结果外推到样本/场景之外，缺少抽样代表性或异质性依据。",
                    evidence, design_state)
        return ("unverifiable", "absolute",
                "结论使用普遍化措辞，但材料没有报告样本框或适用范围。",
                evidence, design_state)

    if kind in ("causal", "proof"):
        if design_state in ("observational", "nonrandom"):
            evidence.append(unit(design[1], design[2]))
            return ("none", "causal" if kind == "causal" else "absolute",
                    "使用因果/证明措辞，但方法段的设计只能支持关联或非随机比较。",
                    evidence, design_state)
        if design_state == "randomized":
            return ("partial", claim["strength"],
                    "方法为随机设计，但结论强度仍需结果段的效应量与不确定性支持。",
                    evidence, design_state)
        if design_state == "intervention":
            return ("partial", claim["strength"],
                    "方法描述了干预，但没有随机化与对照的信息；因果强度需要降级。",
                    evidence, design_state)
        return ("unverifiable", claim["strength"],
                "方法段没有可识别的设计信息，无法判断该强度是否成立。",
                evidence, design_state)

    if kind == "result":
        if design_state == "observational" and DESIGN_INTERVENTION_RE.search(sentence):
            evidence.append(unit(design[1], design[2]))
            return ("none", "causal",
                    "结果段描述干预/改善，但方法段是观察性/横断面设计，且未说明干预。",
                    evidence, design_state)
        if no_intervention is not None and DESIGN_INTERVENTION_RE.search(sentence):
            evidence.append(unit(no_intervention[0], no_intervention[1]))
            return ("none", "causal",
                    "结果段描述干预效果，但材料明确说明没有实施干预。",
                    evidence, design_state)
        if design_state in ("randomized", "intervention", "nonrandom"):
            return ("partial", "descriptive",
                    "结果句有方法段对应，但仍需核对效应量、方向与统计口径。",
                    evidence, design_state)
        if section == "abstract":
            return ("partial", "descriptive",
                    "摘要结果句需要在正文结果段找到对应数值后才能确认。",
                    evidence, design_state)
        return ("unverifiable", "descriptive",
                "结果句缺少可对应的方法或统计证据。", evidence, design_state)

    if kind == "definition":
        return ("unverifiable", "descriptive",
                "定义句需要与测量句对照后判断是否循环。", evidence, design_state)

    if kind == "association":
        if design_state in ("observational", "nonrandom", "randomized",
                            "intervention"):
            return ("partial", "associative",
                    "关联表述与方法段设计一致；仍需核对数值与调整变量。",
                    evidence, design_state)
        return ("unverifiable", "associative",
                "关联表述缺少方法设计信息。", evidence, design_state)

    if kind == "prediction":
        return ("unverifiable", "suggestive",
                "这是研究假设/目的，不应作为已被证据支持的事实主张。",
                evidence, design_state)

    if section == "results" and (STAT_RE.search(sentence)
                                 or RESULT_CUE_RE.search(sentence)):
        return ("partial", "descriptive",
                "结果性表述需要与具体统计量和分析集对应。",
                evidence, design_state)
    return ("unverifiable", "descriptive",
            "背景/过渡句，未形成可独立核对的主张。", evidence, design_state)


def build_claim_inventory(parsed, design, no_mediation, no_intervention, refs):
    claims = []
    scope = (paras_in(parsed, "abstract") + paras_in(parsed, "introduction")
             + paras_in(parsed, "results") + paras_in(parsed, "discussion")
             + paras_in(parsed, "conclusion"))
    seen = set()
    for para, sentence in sentence_items(scope):
        if DISCLAIMER_RE.search(sentence) or len(norm_space(sentence)) < 18:
            continue
        section = para.get("section") or "body"
        kind = claim_kind(sentence, section)
        if kind == "background" and not (CLAIM_VERB_RE.search(sentence)
                                         or STAT_RE.search(sentence)):
            continue
        if not (AIM_RE.search(sentence) or RESULT_CUE_RE.search(sentence)
                or CLAIM_VERB_RE.search(sentence) or STAT_RE.search(sentence)
                or kind in ("causal", "mediation", "generalization", "proof",
                            "definition", "association", "prediction",
                            "assumption")):
            continue
        key = (section, norm_space(sentence))
        if key in seen:
            continue
        seen.add(key)
        speaker = claim_speaker(sentence)
        strength = claim_strength(sentence, kind)
        claim = {
            "id": "C%03d" % (len(claims) + 1),
            "claim": sentence,
            "type": kind,
            "section": section,
            "speaker": speaker,
            "support": "unverifiable",
            "strength": strength,
            "locator": {"path": para.get("path"), "locator": para.get("locator")},
            "evidence": unit(para, sentence),
        }
        support, strength, basis, evidence, design_state = assess_claim_support(
            claim, parsed, design, no_mediation, no_intervention, refs)
        claim["support"] = support
        claim["strength"] = strength
        claim["support_basis"] = basis
        claim["design_state"] = design_state
        claim["evidence"] = evidence
        claims.append(claim)
        if len(claims) >= MAX_CLAIMS:
            break
    return claims

def claim_units(claim):
    value = claim.get("evidence") or []
    if isinstance(value, dict):
        return [value]
    return [item for item in value if isinstance(item, dict)]


def claim_anchor(claim):
    units = claim_units(claim)
    return units[0] if units else {}


def dedupe_units(items):
    seen, out = set(), []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (item.get("path"), item.get("locator"),
               norm_space(item.get("quote")))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def finding(rule_id, facet, anchor, evidence, severity, ftype, status,
            confidence, impact, fix, fp_check, claim=None, subfacet=""):
    evidence = dedupe_units(evidence)[:6]
    quote = anchor.get("quote") or ""
    if len(norm_space(quote)) < 8 and evidence:
        quote = evidence[0].get("quote") or quote
    row = {
        "id": "L%03d" % (finding.counter + 1),
        "rule_id": rule_id,
        "facet": facet,
        "subfacet": subfacet or rule_id,
        "locator": {"path": anchor.get("path"),
                    "locator": anchor.get("locator")},
        "original_quote": quote,
        "severity": severity,
        "type": ftype,
        "status": status,
        "confidence": confidence,
        "rule_status": status,
        "impact": impact,
        "fix": fix,
        "false_positive_check": fp_check,
        "evidence": evidence,
        "generated_by": "scripts/logic_review.py",
    }
    if claim:
        row["claim_id"] = claim.get("id", "")
        row["speaker"] = claim.get("speaker", "")
        row["support"] = claim.get("support", "")
    finding.counter += 1
    return row


finding.counter = 0


def claim_for_sentence(claims, sentence):
    flat = norm_space(sentence)
    for claim in claims:
        if norm_space(claim.get("claim")) == flat:
            return claim
    return None


def limitation_near(parsed, claim):
    """Check whether the author already states the limitation in-context."""
    para = None
    for candidate in parsed["paragraphs"]:
        if candidate.get("locator") == (claim.get("locator") or {}).get("locator"):
            para = candidate
            break
    if para is not None:
        if LIMITATION_RE.search(para.get("text") or ""):
            return True
        for other in parsed["paragraphs"]:
            if other.get("section") == para.get("section") and LIMITATION_RE.search(
                    other.get("text") or ""):
                return True
    return bool(LIMITATION_RE.search(full_text(parsed)))


def is_promise_claim(claim):
    """A hypothesis/aim is a promise, not evidence that the relation exists."""
    sentence = claim.get("claim") or ""
    if claim.get("section") not in ("abstract", "introduction"):
        return False
    return bool(AIM_RE.search(sentence) or re.search(
        r"(?:hypothesi[sz]|whether|aim|objective|we\s+set\s+out|"
        r"we\s+investigated|we\s+examined|we\s+tested)|"
        r"假设|是否|目的|研究问题", sentence, re.I))


def rule_mediation(claims, parsed):
    findings = []
    for claim in claims:
        if claim["type"] != "mediation" or claim["support"] != "none":
            continue
        if claim["speaker"] != "author" or is_promise_claim(claim):
            continue
        no_med = detect_no_mediation(parsed)
        evidence = claim_units(claim)
        status = "needs_recheck"
        confidence = "medium"
        impact = ("正文把中介/机制作为已成立的解释，但材料没有对应的中介分析；"
                  "读者无法核对间接效应、路径系数或置信区间。")
        if no_med is not None:
            evidence.append(unit(no_med[0], no_med[1]))
            status = "confirmed"
            confidence = "high"
            impact = ("正文提出中介/机制主张，但方法段逐字写明没有进行中介分析；"
                      "主张与可提供的证据直接矛盾。")
        findings.append(finding(
            "mediation_without_analysis", "argument_chain", claim_anchor(claim),
            evidence, "major", "error", status, confidence, impact,
            "把该句改为关联/探索性表述，或补充中介模型（路径系数、间接效应、"
            "bootstrap/置信区间、混杂控制）并在结果段报告；若未做分析，删除"
            "中介措辞。",
            "已核对方法段是否存在 mediation/mediate/中介分析；若材料只是节选、"
            "分析在补充材料中，应删除本候选或降为材料缺口。",
            claim, "mediation_without_analysis"))
    return findings


def rule_causal(claims, parsed):
    findings = []
    for claim in claims:
        if claim["type"] not in ("causal", "proof"):
            continue
        if claim["speaker"] != "author" or claim["support"] != "none":
            continue
        if claim["section"] not in ("discussion", "conclusion", "results",
                                    "abstract"):
            continue
        evidence = claim_units(claim)
        design_state = claim.get("design_state")
        if design_state in ("observational", "nonrandom"):
            design = detect_design(parsed)
            evidence.append(unit(design[1], design[2]))
            acknowledged = limitation_near(parsed, claim)
            status = "needs_recheck"
            severity = "major"
            impact = ("结论使用因果/证明措辞，但方法段为横断面/观察性或非随机"
                      "分配；当前设计不能排除替代解释。")
            if acknowledged:
                impact += "作者在邻近文本中已声明局限，需判断该句是否仍过度表述。"
            findings.append(finding(
                "causal_from_observational_design", "method_design",
                claim_anchor(claim), evidence, severity, "error", status,
                "medium", impact,
                "把因果句降级为关联/预测表述（如 associated with），或补充"
                "随机化、时间顺序、混杂控制与替代解释排除证据后再保留因果结论。",
                "已核对句内 hedge 与邻近局限声明；若作者明确标注为假设或"
                "解释性讨论，应降为 suggestion 或删除。",
                claim, "causal_inference"))
        elif PROOF_RE.search(claim["claim"]):
            findings.append(finding(
                "proof_language_outruns_evidence", "argument_chain",
                claim_anchor(claim), evidence, "major", "error",
                "needs_recheck", "medium",
                "结论使用 prove/establish/demonstrate 等证明性措辞，但证据只"
                "支持部分关联或描述性结果，措辞强度超出证据范围。",
                "将证明性措辞改为 supports/suggests/is consistent with；若坚持"
                "证明结论，补充能排除替代解释的证据。",
                "已核对句内 hedge、设计类型与结果段；若该句是在转述他人观点，"
                "应删除本候选并改按引用核验处理。",
                claim, "evidence_support"))
    return findings


def rule_generalization(claims, parsed):
    findings = []
    for claim in claims:
        if claim["type"] != "generalization" or claim["speaker"] != "author":
            continue
        if claim["support"] != "none" or is_promise_claim(claim):
            continue
        sample = sample_sentence(parsed)
        evidence = claim_units(claim)
        if sample[0] is not None:
            evidence.append(unit(sample[0], sample[1]))
        findings.append(finding(
            "generalization_overreach", "argument_chain", claim_anchor(claim),
            evidence, "major", "error", "needs_recheck", "medium",
            "结论把结果外推到样本/场景之外，但材料只报告了有限样本或单一场景，"
            "缺少代表性、异质性与边界条件证据。",
            "把结论限定在已抽样人群与情境；若要主张可推广，补充抽样框、"
            "站点/人群差异与效应异质性分析。",
            "已核对样本描述与结论范围；若局限段已明确声明推广边界且结论句"
            "本身有 hedge，可降为 suggestion。",
            claim, "evidence_support"))
    return findings


def rule_circular_definition(parsed):
    findings = []
    for para, sentence in sentence_items(parsed["paragraphs"]):
        match = CIRCULAR_MEASURE_RE.search(sentence) or DEFINITION_RE.search(sentence)
        if not match:
            continue
        definition = match.group(1).strip()
        measure_match = MEASURED_BY_RE.search(sentence)
        if not measure_match:
            continue
        measure = measure_match.group("measure").strip()
        left = set(tokens(definition))
        right = set(tokens(measure))
        overlap = sorted(left & right)
        generic = {"ability", "maintain", "level", "degree", "score", "scale",
                   "measure", "participants", "people", "performance", "task"}
        specific = [w for w in overlap if w not in generic]
        if len(overlap) < 1 or (len(specific) < 1 and len(overlap) < 2):
            continue
        status = "confirmed" if len(overlap) >= 2 else "needs_recheck"
        findings.append(finding(
            "circular_definition", "argument_chain", unit(para, sentence),
            [unit(para, sentence)], "major", "error", status,
            "high" if status == "confirmed" else "medium",
            "构念的定义与测量使用同一组词（%s），形成循环定义；该变量无法"
            "独立于结果被测量。" % ", ".join(overlap[:4]),
            "给出独立于结果的操作性定义（测量工具、评分规则、信度），并说明"
            "定义与测量为何不是同一件事；若确实使用同一指标，重写定义并"
            "讨论共同方法偏差。",
            "已提取定义句与测量句的词项重叠；若两个词只是领域通用词而"
            "测量工具另有独立来源，应删除本候选。",
            None, "reasoning_chain"))
    return findings


def rule_unsupported_result(claims, parsed, design, no_intervention):
    findings = []
    for claim in claims:
        if claim["type"] != "result" or claim["support"] != "none":
            continue
        sentence = claim["claim"]
        evidence = claim_units(claim)
        if no_intervention is not None:
            evidence.append(unit(no_intervention[0], no_intervention[1]))
        elif design[0] == "observational":
            evidence.append(unit(design[1], design[2]))
        findings.append(finding(
            "unsupported_result_claim", "method_design", claim_anchor(claim),
            evidence, "major", "error", "needs_recheck", "medium",
            "结果段报告干预/改善或全体改善，但方法段没有对应干预、对照或"
            "分组信息，结果无法由所述设计产生。",
            "在方法段补充干预内容、分组、对照与测量时点；若没有干预，改写"
            "结果句为观察性描述并删除干预/改善措辞。",
            "已核对方法段是否存在 intervention/training/control 与分组描述；"
            "若干预细节在补充材料中，应删除本候选或降为材料缺口。",
            claim, "evidence_support"))
    return findings


def rule_randomization(parsed):
    random_hit = None
    alt_hit = None
    for para, sentence in sentence_items(
            paras_in(parsed, "abstract") + paras_in(parsed, "methods")):
        if DESIGN_RANDOM_RE.search(sentence) and re.search(
                r"participant|student|patient|subject|assign|allocat|group|"
                r"参与者|学生|患者|分组|分配", sentence, re.I):
            random_hit = random_hit or (para, sentence)
        if ALTERNATION_RE.search(sentence) and re.search(
                r"assign|allocat|group|order|waitlist|control|floor|week|"
                r"分配|分组|顺序|对照|楼层|周", sentence, re.I):
            alt_hit = alt_hit or (para, sentence)
    if not random_hit or not alt_hit:
        return []
    return [finding(
        "randomization_inconsistency", "method_design",
        unit(random_hit[0], random_hit[1]),
        [unit(random_hit[0], random_hit[1]), unit(alt_hit[0], alt_hit[1])],
        "major", "error", "confirmed", "high",
        "摘要/方法称随机分配，但方法段写成交替/按楼层分配；两种分配机制"
        "不能同时成立，影响因果解释与偏倚评估。",
        "统一为实际分配方式：若为交替分配，删除 randomized 并说明分配隐藏；"
        "若确为随机分配，补充随机序列生成、分配隐藏与实施者。",
        "已核对两处都指向分组分配而非抽样；若 alternately 描述的是测量顺序"
        "而非分组，应降为措辞错误而非设计缺陷。",
        None, "method_result_consistency")]


def rule_cited_misattribution(claims, parsed, refs):
    findings = []
    for claim in claims:
        if claim["speaker"] != "cited" or claim["support"] != "none":
            continue
        keys = citation_keys(claim["claim"])
        entry = next((refs[key] for key in keys if key in refs), None)
        if entry is None:
            continue
        findings.append(finding(
            "cited_source_misattributed", "citation_support", claim_anchor(claim),
            claim_units(claim) + [unit(entry["para"], entry["text"])],
            "major", "error", "needs_recheck", "medium",
            "转述他人研究时使用了因果/证明措辞，但所引条目的设计或结论不支持"
            "该强度；这是引用支持问题，不是作者自己的实验结论。",
            "改写为条目实际支持的关联/定性表述，或改引与主张强度匹配的原始"
            "研究/系统综述；保留引用时只用于其真正支持的陈述。",
            "已核对引用条目文本；若条目只是节选、引用仅作背景铺垫，应删除"
            "本候选或降为材料缺口。",
            claim, "citation_support"))
    return findings


def rule_abstract_mismatch(parsed):
    abstract = paras_in(parsed, "abstract")
    results = paras_in(parsed, "results")
    if not abstract or not results:
        return []
    abs_sentence = None
    abs_para = None
    for para, sentence in sentence_items(abstract):
        if re.search(r"significant|improved|effective|reduced|increased|"
                     r"显著|改善|有效|提高|降低", sentence, re.I):
            abs_para, abs_sentence = para, sentence
            break
    if abs_sentence is None:
        return []
    result_sentence, result_para = None, None
    first_p = None
    for para, sentence in sentence_items(results):
        if STAT_RE.search(sentence):
            p_match = re.search(r"p\s*[=<>]\s*(0?\.\d+|\d+(?:\.\d+)?)", sentence, re.I)
            if p_match:
                try:
                    value = float(p_match.group(1))
                except ValueError:
                    value = None
                if first_p is None:
                    first_p = value
                if re.search(r"not\s+significant|no\s+significant|"
                             r"不显著|无显著", sentence, re.I):
                    result_para, result_sentence = para, sentence
                    break
                if value is not None and value >= 0.05:
                    result_para, result_sentence = para, sentence
                    break
    if result_sentence is None:
        return []
    if not re.search(r"significant|improved|effective|reduced|increased|"
                     r"显著|改善|有效|提高|降低", abs_sentence, re.I):
        return []
    return [finding(
        "abstract_result_direction_conflict", "fact_consistency",
        unit(abs_para, abs_sentence),
        [unit(abs_para, abs_sentence), unit(result_para, result_sentence)],
        "major", "error", "needs_recheck", "medium",
        "摘要给出显著/改善的结论，但结果段同一结果的首个或主要检验未达到"
        "显著阈值；两处口径需要统一。",
        "摘要只报告预先设定的主分析及其结果；若结果段的主分析不显著，摘要"
        "不得写显著改善，或明确标注为次要/校正后分析。",
        "已核对两处是否来自不同结局、不同分析集或不同检验；若是不同分析，"
        "仍须在摘要标明口径，不能并列为主结果。",
        None, "fact_consistency")]


def build_findings(parsed, claims, design, refs, no_mediation, no_intervention):
    findings = []
    findings.extend(rule_mediation(claims, parsed))
    findings.extend(rule_causal(claims, parsed))
    findings.extend(rule_generalization(claims, parsed))
    findings.extend(rule_circular_definition(parsed))
    findings.extend(rule_unsupported_result(claims, parsed, design,
                                            no_intervention))
    findings.extend(rule_randomization(parsed))
    findings.extend(rule_cited_misattribution(claims, parsed, refs))
    findings.extend(rule_abstract_mismatch(parsed))
    seen, out = set(), []
    for row in findings:
        key = (row["rule_id"], norm_space(row.get("original_quote"))[:160])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def cross_section_checks(parsed, claims, findings):
    checks = []
    abstract_text = " ".join(p.get("text") or "" for p in paras_in(parsed, "abstract"))
    intro_text = " ".join(p.get("text") or "" for p in paras_in(parsed, "introduction"))
    methods_text = " ".join(p.get("text") or "" for p in paras_in(parsed, "methods"))
    results_text = " ".join(p.get("text") or "" for p in paras_in(parsed, "results"))
    discussion_text = " ".join(p.get("text") or "" for p in paras_in(parsed, "discussion")
                               + paras_in(parsed, "conclusion"))
    promise_text = abstract_text + " " + intro_text
    promise_para, promise_sentence = None, None
    if MECHANISM_RE.search(promise_text):
        for para, sentence in sentence_items(paras_in(parsed, "abstract")
                                             + paras_in(parsed, "introduction")):
            if MECHANISM_RE.search(sentence):
                promise_para, promise_sentence = para, sentence
                break
        delivery_para, delivery_sentence = None, None
        if re.search(r"mediat|mediation|indirect\s+effect|中介", methods_text + " "
                     + results_text, re.I):
            for para, sentence in sentence_items(paras_in(parsed, "methods")
                                                 + paras_in(parsed, "results")):
                if re.search(r"mediat|mediation|indirect\s+effect|中介", sentence, re.I):
                    delivery_para, delivery_sentence = para, sentence
                    break
            consistency = "delivered"
        else:
            for para, sentence in sentence_items(parsed["paragraphs"]):
                if NO_MEDIATION_RE.search(sentence):
                    delivery_para, delivery_sentence = para, sentence
                    break
            consistency = "contradicted" if delivery_sentence else "not_delivered"
        checks.append({
            "promise": "分析中介/机制",
            "promise_locator": (promise_para or {}).get("locator", ""),
            "delivery": ("方法/结果报告中介分析" if consistency == "delivered"
                         else "方法明确未做中介分析" if consistency == "contradicted"
                         else "未找到中介分析"),
            "delivery_locator": (delivery_para or {}).get("locator", ""),
            "consistency": consistency,
            "note": "跨章节核对：引言承诺的机制分析是否在方法与结果中交付。",
            "evidence": dedupe_units([u for u in [
                unit(promise_para, promise_sentence) if promise_sentence else None,
                unit(delivery_para, delivery_sentence) if delivery_sentence else None,
            ] if u]),
        })
    # Intervention/randomization promise vs methods
    if re.search(r"randomi[sz]|trial|intervention|training|随机|干预|训练",
                 promise_text, re.I):
        for para, sentence in sentence_items(paras_in(parsed, "abstract")
                                             + paras_in(parsed, "introduction")):
            if re.search(r"randomi[sz]|trial|intervention|training|随机|干预|训练",
                         sentence, re.I):
                promise_para, promise_sentence = para, sentence
                break
        design = detect_design(parsed)
        if design[0] in ("observational", "nonrandom"):
            consistency = "contradicted"
        elif design[0] in ("randomized", "intervention"):
            consistency = "delivered" if re.search(
                r"randomi[sz]|trial|intervention|training|随机|干预|训练",
                methods_text, re.I) else "partial"
        else:
            consistency = "unverifiable"
        checks.append({
            "promise": "采用随机/干预设计检验效果",
            "promise_locator": (promise_para or {}).get("locator", ""),
            "delivery": ("方法段为 %s 设计" % design[0]) if design[0] != "unclear"
                        else "方法段未说明设计",
            "delivery_locator": (design[1] or {}).get("locator", "") if design[1] else "",
            "consistency": consistency,
            "note": "跨章节核对：摘要/引言的随机或干预承诺与方法段实际设计是否一致。",
            "evidence": dedupe_units([u for u in [
                unit(promise_para, promise_sentence) if promise_sentence else None,
                unit(design[1], design[2]) if design[1] else None,
            ] if u]),
        })
    # Abstract conclusion vs results significance
    abstract_hits = [(p, s) for p, s in sentence_items(paras_in(parsed, "abstract"))
                     if re.search(r"significant|improved|effective|reduced|"
                                  r"increased|显著|改善|有效|提高|降低", s, re.I)]
    result_hits = [(p, s) for p, s in sentence_items(paras_in(parsed, "results"))
                   if re.search(r"p\s*[=<>]|not\s+significant|不显著", s, re.I)]
    if abstract_hits and result_hits:
        consistency = "unverifiable"
        note = "摘要结论与结果段显著性口径需要逐句核对。"
        for para, sentence in result_hits:
            if re.search(r"not\s+significant|no\s+significant|不显著|无显著",
                         sentence, re.I):
                consistency = "contradicted"
                note = "结果段出现不显著表述，摘要仍使用显著/改善结论。"
                break
            p_match = re.search(r"p\s*[=<>]\s*(0?\.\d+|\d+(?:\.\d+)?)", sentence)
            if p_match:
                try:
                    value = float(p_match.group(1))
                except ValueError:
                    value = None
                if value is not None and value >= 0.05:
                    consistency = "partial"
                    note = "结果段首个 p 值未达 0.05；摘要需标明分析口径。"
                    break
                if value is not None and value < 0.05:
                    consistency = "delivered"
                    note = "结果段首个 p 值达到 0.05 阈值，仍需核对效应量与方向。"
                    break
        checks.append({
            "promise": "摘要结论与正文结果一致",
            "promise_locator": abstract_hits[0][0].get("locator", ""),
            "delivery": note,
            "delivery_locator": result_hits[0][0].get("locator", ""),
            "consistency": consistency,
            "note": "跨章节核对：摘要的显著/改善结论是否由结果段同一分析支持。",
            "evidence": dedupe_units([unit(abstract_hits[0][0], abstract_hits[0][1]),
                                      unit(result_hits[0][0], result_hits[0][1])]),
        })
    # Discussion claims vs methods design
    causal_claims = [c for c in claims if c["type"] in ("causal", "proof")
                     and c["section"] in ("discussion", "conclusion")]
    design = detect_design(parsed)
    if causal_claims and design[0] != "unclear":
        consistency = ("contradicted" if design[0] in ("observational", "nonrandom")
                       else "partial")
        checks.append({
            "promise": "讨论中的因果/证明结论",
            "promise_locator": causal_claims[0]["locator"]["locator"],
            "delivery": "方法段为 %s 设计" % design[0],
            "delivery_locator": (design[1] or {}).get("locator", "") if design[1] else "",
            "consistency": consistency,
            "note": "跨章节核对：讨论的推断强度是否超出方法设计。",
            "evidence": dedupe_units(claim_units(causal_claims[0]) + [
                                      unit(design[1], design[2]) if design[1] else None]),
        })
    return checks

def build_facets(parsed, claims, findings, checks, assets):
    have = set(parsed["section_names"])
    by_facet = {}
    for row in findings:
        by_facet.setdefault(row["facet"], []).append(row["rule_id"])
    evidence = {}
    for para in parsed["paragraphs"][:120]:
        evidence.setdefault(para.get("section"), []).append(para.get("locator"))

    def row(facet, status, items, reason=None):
        items = list(dict.fromkeys(str(item) for item in items if item))
        out = {"facet": facet, "status": status, "items": items,
               "evidence": [] if facet == "academic_compliance" else
               (evidence.get("results") or evidence.get("discussion")
                or evidence.get("methods") or
                evidence.get("introduction") or [])[:5]}
        if status != "checked":
            out["reason"] = reason or "材料不足"
        return out

    structure_ok = "abstract" in have and "results" in have
    rows = [
        row("fact_consistency",
            "checked" if structure_ok else "partial",
            ["摘要/结果数值方向", "样本量口径", "术语与定义一致性"] +
            by_facet.get("fact_consistency", []),
            None if structure_ok else "缺少摘要或结果段，只能部分核对"),
        row("citation_support",
            "checked" if "references" in have and any(
                c["speaker"] == "cited" for c in claims) else
            ("partial" if "references" in have else "not_checked"),
            ["转述观点与条目强度", "引用标签可回查", "引用设计匹配"] +
            by_facet.get("citation_support", []),
            None if "references" in have else "缺少参考文献段"),
        row("method_design",
            "checked" if "methods" in have else "not_checked",
            ["设计类型", "随机化/分配", "对照与混杂", "干预实施"] +
            by_facet.get("method_design", []),
            None if "methods" in have else "缺少方法段"),
        row("statistical_reporting",
            "checked" if "results" in have else "not_checked",
            ["显著性口径", "效应量与不确定性", "结果方向"] +
            by_facet.get("statistical_reporting", []),
            None if "results" in have else "缺少结果段"),
        row("argument_chain",
            "checked" if ("discussion" in have or "conclusion" in have)
            and "results" in have else "partial",
            ["主张清单", "证据支持", "承诺-交付", "因果/相关", "循环定义",
             "推广边界"] + by_facet.get("argument_chain", []),
            None if (("discussion" in have or "conclusion" in have)
                     and "results" in have) else "缺少讨论/结论或结果段"),
        row("academic_compliance", "not_applicable",
            ["本题聚焦逻辑与论证；规范面只在输入显式涉及伦理/声明时记录"],
            "任务边界不负责学术规范判定；不把未涉及项写成通过"),
    ]
    return rows


def build_logic_facets(parsed, claims, findings, checks):
    have = set(parsed["section_names"])
    findings_by_sub = {}
    for row in findings:
        findings_by_sub.setdefault(row.get("subfacet") or row["rule_id"], []).append(
            row["id"])
    claim_sections = sorted({c["section"] for c in claims})
    rows = [
        {"facet": "claim_extraction",
         "status": "checked" if claims else "not_checked",
         "items": ["作者主张 vs 引用观点 vs 假设", "主张类型", "强度",
                   "主张所在章节"],
         "evidence": [c["locator"]["locator"] for c in claims[:6]],
         "reason": "" if claims else "没有抽到可核对的主张"},
        {"facet": "evidence_support",
         "status": "checked" if claims and ("results" in have or "methods" in have)
         else "partial",
         "items": ["主张是否有本文证据", "证据是否足以支撑强度",
                   "未报告项"],
         "evidence": [c["locator"]["locator"] for c in claims
                      if c["support"] in ("none", "partial")][:6],
         "reason": "" if claims and ("results" in have or "methods" in have)
         else "缺少方法/结果段，无法核对支持关系"},
        {"facet": "reasoning_chain",
         "status": "checked" if ("methods" in have and "results" in have
                                 and ("discussion" in have or "conclusion" in have))
         else "partial",
         "items": ["前提-结论", "隐含假设", "替代解释", "循环论证"],
         "evidence": [row["locator"]["locator"] for row in findings
                      if row.get("subfacet") in
                      ("reasoning_chain", "mediation_without_analysis")][:6],
         "reason": "" if ("methods" in have and "results" in have
                          and ("discussion" in have or "conclusion" in have))
         else "缺少方法、结果或讨论段，推理链只能部分核对"},
        {"facet": "causal_inference",
         "status": "checked" if "methods" in have else "not_checked",
         "items": ["因果措辞 vs 设计", "时间顺序", "混杂与替代解释",
                   "相关性表述"],
         "evidence": [row["locator"]["locator"] for row in findings
                      if row.get("subfacet") == "causal_inference"][:6],
         "reason": "" if "methods" in have else "缺少方法段"},
        {"facet": "cross_section_consistency",
         "status": "checked" if len(have & {"abstract", "introduction",
                                            "methods", "results", "discussion",
                                            "conclusion"}) >= 3 else "partial",
         "items": ["承诺-交付", "方法-结果口径", "摘要-正文一致"],
         "evidence": [row.get("promise_locator") for row in checks
                      if row.get("promise_locator")][:6],
         "reason": "" if len(have & {"abstract", "introduction", "methods",
                                     "results", "discussion", "conclusion"}) >= 3
         else "章节不足，跨章节核对只能部分完成"},
        {"facet": "severity_and_fix",
         "status": "checked" if findings and all(
             row.get("severity") and row.get("fix") for row in findings)
         else ("checked" if not findings else "partial"),
         "items": ["严重度判据", "影响", "可执行修订", "误报检查"],
         "evidence": [row["id"] for row in findings[:6]],
         "reason": "" if not findings or all(
             row.get("severity") and row.get("fix") for row in findings)
         else "部分候选缺少严重度或修订建议"},
    ]
    return rows


def build_checks(parsed, claims, findings, checks, readable, refs):
    missing_ref = [row for row in findings if row["rule_id"] ==
                   "cited_source_misattributed"]
    return [
        {"item": "材料可读", "status": "pass" if readable else "fail",
         "detail": "读取了 %d 份材料" % len(readable)},
        {"item": "全文分段", "status": "pass" if parsed["paragraphs"] else "fail",
         "detail": "章节 %s；段落 %d；句子 %d" % (
             ", ".join(parsed["section_names"]) or "none",
             len(parsed["paragraphs"]),
             sum(len(p.get("sentences") or []) for p in parsed["paragraphs"]))},
        {"item": "主张清单", "status": "pass" if claims else "not_checked",
         "detail": "抽取 %d 条主张；作者 %d / 引用 %d / 假设 %d" % (
             len(claims),
             sum(1 for c in claims if c["speaker"] == "author"),
             sum(1 for c in claims if c["speaker"] == "cited"),
             sum(1 for c in claims if c["speaker"] == "assumption"))},
        {"item": "跨章节承诺-交付", "status": "pass" if checks else "not_checked",
         "detail": "生成 %d 条核对；not_delivered/contradicted %d 条" % (
             len(checks),
             sum(1 for row in checks if row["consistency"] in
                 ("not_delivered", "contradicted")))},
        {"item": "引用观点误报控制",
         "status": "pass" if not missing_ref else "fail",
         "detail": "发现 %d 条转述观点与条目强度不匹配；引用观点不自动算作者缺陷" %
                   len(missing_ref)},
        {"item": "类型与修订建议",
         "status": "pass" if all(row.get("type") and row.get("fix")
                                 for row in findings) else "not_checked",
         "detail": "每条候选含 type/severity/impact/fix/false_positive_check"},
    ]


def build_uncertainties(parsed, docs, readable, refs, findings, assets):
    out = []
    if not readable:
        out.append({"item": "materials", "reason": "没有可读材料",
                    "impact": "无法开始逻辑审查", "action": "提供 md/txt/tex/pdf/docx 或先运行 prepare"})
    missing_sections = [name for name in ("abstract", "methods", "results",
                                          "discussion", "conclusion")
                        if name not in parsed["section_names"]]
    if readable and missing_sections:
        out.append({"item": "section_coverage",
                    "reason": "缺少章节：%s" % ", ".join(missing_sections),
                    "impact": "相关 facet 只能部分核对，不能判为通过",
                    "action": "补充材料或人工核对缺失章节"})
    if readable and not refs:
        out.append({"item": "references", "reason": "未找到参考文献段/条目",
                    "impact": "引用观点与条目强度无法核对",
                    "action": "补充参考文献表或标注引用观点不可核验"})
    if any(row["status"] == "needs_recheck" for row in findings):
        out.append({"item": "candidate_review",
                    "reason": "存在 needs_recheck 候选",
                    "impact": "模型必须逐条回原文确认或驳回",
                    "action": "按 SKILL.md 人工复核并更新 work/review-findings.json"})
    if assets:
        out.append({"item": "figures",
                    "reason": "输入含图片资源：%s" % ", ".join(assets[:5]),
                    "impact": "图片内信息未在本脚本中做逻辑核对",
                    "action": "人工查看图片并把结论写入报告/uncertainties"})
    unparsed = [doc["path"] for doc in docs if doc.get("warnings")]
    if unparsed:
        out.append({"item": "unparsed_materials",
                    "reason": "部分材料未能直接解析：%s" % ", ".join(unparsed),
                    "impact": "其中的主张可能未计入", "action": "先运行 prepare 或转为可读文本"})
    out.append({"item": "offline_scope", "reason": "脚本不联网",
                "impact": "只核对输入材料；不判定外部文献真实性",
                "action": "在线核验与外部检索由人工另行完成并记录"})
    return out


def decide_status(readable, claims, findings):
    if not readable:
        return "no_materials"
    if findings:
        return "ok_with_candidates"
    return "ok_no_issues"


def review_status_suggestion(claims, findings, checks):
    if any(row["status"] == "confirmed" for row in findings):
        return "issues"
    if findings or any(row["consistency"] in ("contradicted", "not_delivered")
                       for row in checks):
        return "needs_recheck"
    if claims:
        return "pass"
    return "not_checked"


def analyze_documents(docs, assets, case_id, synthetic=False):
    primary = choose_primary(docs)
    if primary is None:
        return {
            "case_id": case_id, "generated_by": "scripts/logic_review.py",
            "generated_at": now_iso(), "synthetic": bool(synthetic),
            "status": "no_materials", "engine_status": "input_error",
            "review_status": "not_checked",
            "summary": "没有可读材料，未生成主张或问题；不把空输入当作通过。",
            "documents": [{"path": d.get("path"), "kind": d.get("kind"),
                           "chars": len(d.get("text") or "")} for d in docs],
            "fulltext": {"sections": [], "paragraphs": 0, "sentences": 0},
            "claim_inventory": [], "cross_section_checks": [],
            "findings": [], "facets_checked": [], "logic_facets": [],
            "checks": [{"item": "材料可读", "status": "fail", "detail": "无可读文本"}],
            "evidence": [], "uncertainties": [{
                "item": "materials", "reason": "no readable text material",
                "impact": "the review cannot start",
                "action": "provide a readable manuscript or extracted text"}],
            "stats": {"documents": len(docs), "claims": 0, "findings": 0},
            "limits": ["没有可读材料，未生成任何主张或结论。"],
        }
    parsed = parse_document(primary)
    refs = parse_references(parsed)
    design = detect_design(parsed)
    no_mediation = detect_no_mediation(parsed)
    no_intervention = detect_no_intervention(parsed)
    claims = build_claim_inventory(parsed, design, no_mediation,
                                   no_intervention, refs)
    finding.counter = 0
    findings = build_findings(parsed, claims, design, refs, no_mediation,
                              no_intervention)
    checks = cross_section_checks(parsed, claims, findings)
    facets = build_facets(parsed, claims, findings, checks, assets)
    logic_facets = build_logic_facets(parsed, claims, findings, checks)
    status = decide_status(bool(str(primary.get("text") or "").strip()),
                           claims, findings)
    review_status = review_status_suggestion(claims, findings, checks)
    evidence = []
    for claim in claims:
        evidence.extend(claim.get("evidence") or [])
    for row in findings:
        evidence.extend(row.get("evidence") or [])
    for row in checks:
        evidence.extend(row.get("evidence") or [])
    evidence = dedupe_units(evidence)
    uncertainties = build_uncertainties(parsed, docs,
                                        [d for d in docs if str(d.get("text") or "").strip()],
                                        refs, findings, assets)
    section_stats = {}
    for para in parsed["paragraphs"]:
        sec = para.get("section") or "body"
        item = section_stats.setdefault(sec, {"paragraphs": 0, "sentences": 0,
                                               "chars": 0, "locators": []})
        item["paragraphs"] += 1
        item["sentences"] += len(para.get("sentences") or [])
        item["chars"] += len(para.get("text") or "")
        if len(item["locators"]) < 5:
            item["locators"].append(para.get("locator"))
    return {
        "case_id": case_id,
        "mode": "logic-review",
        "generated_by": "scripts/logic_review.py",
        "generated_at": now_iso(),
        "synthetic": bool(synthetic),
        "status": status,
        "engine_status": ("partial" if any(
            name not in parsed["section_names"]
            for name in ("methods", "results")) else "ok"),
        "review_status": review_status,
        "summary": ("全文分段完成：%d 章、%d 段；抽取主张 %d 条"
                    "（作者 %d / 引用 %d / 假设 %d）；候选问题 %d 条；"
                    "跨章节核对 %d 条。" % (
                        len(parsed["section_names"]), len(parsed["paragraphs"]),
                        len(claims),
                        sum(1 for c in claims if c["speaker"] == "author"),
                        sum(1 for c in claims if c["speaker"] == "cited"),
                        sum(1 for c in claims if c["speaker"] == "assumption"),
                        len(findings), len(checks))),
        "documents": [{"path": d.get("path"), "kind": d.get("kind"),
                       "chars": len(d.get("text") or ""),
                       "sections": len(parse_document(d)["section_names"])
                       if str(d.get("text") or "").strip() else 0}
                      for d in docs],
        "fulltext": {
            "sections": parsed["section_names"],
            "section_stats": section_stats,
            "paragraphs": len(parsed["paragraphs"]),
            "sentences": sum(len(p.get("sentences") or [])
                             for p in parsed["paragraphs"]),
            "captions": len(parsed["captions"]),
            "chars": parsed["chars"],
        },
        "design": {"state": design[0],
                   "quote": design[2] if design[1] else "",
                   "locator": (design[1] or {}).get("locator", "") if design[1] else ""},
        "claim_inventory": claims,
        "cross_section_checks": checks,
        "findings": findings,
        "facets_checked": facets,
        "logic_facets": logic_facets,
        "checks": build_checks(parsed, claims, findings, checks,
                               [d for d in docs if str(d.get("text") or "").strip()],
                               refs),
        "evidence": evidence,
        "uncertainties": uncertainties,
        "stats": {
            "documents": len(docs), "sections": len(parsed["section_names"]),
            "paragraphs": len(parsed["paragraphs"]),
            "sentences": sum(len(p.get("sentences") or [])
                             for p in parsed["paragraphs"]),
            "claims": len(claims), "findings": len(findings),
            "confirmed": sum(1 for f in findings if f["status"] == "confirmed"),
            "needs_recheck": sum(1 for f in findings
                                 if f["status"] == "needs_recheck"),
            "cross_section_checks": len(checks),
            "evidence": len(evidence),
        },
        "review_protocol": {
            "candidate_only": True,
            "human_review_required": True,
            "steps": [
                "打开每条候选 evidence，确认 quote 逐字可查。",
                "核对主张的 speaker（author/cited/assumption），不要把引用观点或"
                "作者假设当作作者缺陷。",
                "逐条确认、改写或删除候选；确认的写 status=confirmed，"
                "不确定的写 needs_recheck 并进入 uncertainties。",
                "补充脚本未覆盖的语义跳跃、替代解释与跨段落矛盾。",
                "最终 findings 必须与 output/findings.json、"
                "output/evidence-map.json 和报告一致。",
            ],
        },
        "limits": [
            "规则只产生候选，不替代语义阅读；每条 finding 必须由模型结合上下文确认。",
            "逐字引文来自解析后的文本；扫描件/图表无文字层时无法抽取。",
            "不联网，不核对未提供的外部文献。",
        ],
    }

def load_documents(input_dir, work_dir):
    docs, assets = scan_input(input_dir, work_dir)
    # Reuse prepare's extracted text when a binary material could not be read.
    materials = load_json(Path(work_dir) / "materials.json", {})
    prepared = {}
    for doc in (materials.get("documents") or []):
        text = str(doc.get("text") or "")
        if text.strip():
            prepared[str(doc.get("path") or "material")] = text
    for doc in docs:
        if not str(doc.get("text") or "").strip() and doc.get("path") in prepared:
            doc["text"] = prepared[doc["path"]]
    return docs, assets


def analyze_paths(input_dir, work_dir):
    case = load_json(Path(input_dir) / "case.json", {})
    docs, assets = load_documents(input_dir, work_dir)
    work = load_json(Path(work_dir) / "review-findings.json", {})
    case_id = (case.get("case_id") if isinstance(case, dict) else None) \
        or (work.get("case_id") if isinstance(work, dict) else None) or "CASE"
    return analyze_documents(docs, assets, str(case_id),
                             bool(isinstance(case, dict) and case.get("synthetic")))


def _md_cell(value, limit=180):
    text = norm_space(value)
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return (text or "-").replace("|", "\\|")


def render_report(payload):
    lines = [
        "# 论文逻辑与论证审查：全文分段与确定性预检",
        "",
        "- case_id: `%s`" % payload.get("case_id"),
        "- 生成时间: %s" % payload.get("generated_at"),
        "- 脚本: `logic_review.py` v%s" % VERSION,
        "- 状态: `%s`；review_status: `%s`" % (
            payload.get("status"), payload.get("review_status")),
        "- 说明: 本报告只含**确定性候选**与全文分段索引，不是最终审稿结论；"
        "每条候选都必须由模型回到原文复核。",
        "",
        str(payload.get("summary") or ""),
        "",
        "## 1. 输入与全文分段",
        "",
        "| path | kind | chars | sections |",
        "|---|---|---:|---:|",
    ]
    for item in payload.get("documents") or []:
        lines.append("| %s | %s | %d | %d |" % (
            _md_cell(item.get("path"), 80), item.get("kind"),
            item.get("chars", 0), item.get("sections", 0)))
    full = payload.get("fulltext") or {}
    lines += ["", "章节: %s；段落 %d；句子 %d；图表题注 %d。" % (
        ", ".join(full.get("sections") or []) or "none",
        full.get("paragraphs", 0), full.get("sentences", 0),
        full.get("captions", 0)), ""]
    lines += ["| section | paragraphs | sentences | chars | sample locators |",
              "|---|---:|---:|---:|---|"]
    for name, row in (full.get("section_stats") or {}).items():
        lines.append("| %s | %d | %d | %d | %s |" % (
            name, row.get("paragraphs", 0), row.get("sentences", 0),
            row.get("chars", 0),
            _md_cell("; ".join(row.get("locators") or []), 100)))
    lines += ["", "## 2. 主张清单", "",
              "| id | section | speaker | type | strength | support | claim |",
              "|---|---|---|---|---|---|---|"]
    for claim in payload.get("claim_inventory") or []:
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            claim.get("id"), claim.get("section"), claim.get("speaker"),
            claim.get("type"), claim.get("strength"), claim.get("support"),
            _md_cell(claim.get("claim"), 150)))
    lines += ["", "## 3. 跨章节承诺-交付", "",
              "| promise | consistency | delivery | note |",
              "|---|---|---|---|"]
    for row in payload.get("cross_section_checks") or []:
        lines.append("| %s | %s | %s | %s |" % (
            _md_cell(row.get("promise"), 60), row.get("consistency"),
            _md_cell(row.get("delivery"), 90), _md_cell(row.get("note"), 120)))
    lines += ["", "## 4. 候选问题（需逐条复核）", ""]
    if not payload.get("findings"):
        lines.append("未发现确定性候选。这不等于论文无问题；语义层仍需人工通读。")
    for row in payload.get("findings") or []:
        lines += [
            "", "### %s [%s / %s] severity=%s status=%s" % (
                row.get("id"), row.get("facet"), row.get("rule_id"),
                row.get("severity"), row.get("status")),
            "",
            "- location: `%s` `%s`" % (
                (row.get("locator") or {}).get("path"),
                (row.get("locator") or {}).get("locator")),
            "- original_quote: %s" % _md_cell(row.get("original_quote"), 260),
            "- impact: %s" % row.get("impact"),
            "- fix: %s" % row.get("fix"),
            "- false_positive_check: %s" % row.get("false_positive_check"),
            "- evidence:",
        ]
        for item in row.get("evidence") or []:
            lines.append("  - `%s` `%s`: %s" % (
                item.get("path"), item.get("locator"),
                _md_cell(item.get("quote"), 220)))
    lines += ["", "## 5. 审稿面覆盖", "",
              "| facet | status | items | reason |", "|---|---|---|---|"]
    for row in payload.get("facets_checked") or []:
        lines.append("| %s | %s | %s | %s |" % (
            row.get("facet"), row.get("status"),
            _md_cell(", ".join(row.get("items") or []), 100),
            _md_cell(row.get("reason"), 100)))
    lines += ["", "### 逻辑专项面", "",
              "| subfacet | status | items | reason |", "|---|---|---|---|"]
    for row in payload.get("logic_facets") or []:
        lines.append("| %s | %s | %s | %s |" % (
            row.get("facet"), row.get("status"),
            _md_cell(", ".join(row.get("items") or []), 100),
            _md_cell(row.get("reason"), 100)))
    lines += ["", "## 6. 检查项", "",
              "| item | status | detail |", "|---|---|---|"]
    for row in payload.get("checks") or []:
        lines.append("| %s | %s | %s |" % (
            row.get("item"), row.get("status"), _md_cell(row.get("detail"), 140)))
    lines += ["", "## 7. 不确定性与限制", ""]
    for row in payload.get("uncertainties") or []:
        lines.append("- **%s**: %s；%s；建议：%s" % (
            row.get("item"), row.get("reason"), row.get("impact"),
            row.get("action")))
    lines += ["", "## 8. 人工复核步骤", ""]
    for index, step in enumerate((payload.get("review_protocol") or {}).get(
            "steps") or [], 1):
        lines.append("%d. %s" % (index, step))
    lines += ["", "---", "",
              "本报告由确定性脚本生成，不联网、不编造文献；所有候选须经人工确认。",
              ""]
    return "\n".join(lines)


def candidate_payload(payload):
    return {
        "case_id": payload.get("case_id"),
        "mode": "logic-review",
        "_note": "确定性候选，不是最终结论；合并前必须逐条核对 quote 与语义。",
        "precheck": "output/logic-check.json",
        "review_status": payload.get("review_status"),
        "claim_inventory": payload.get("claim_inventory") or [],
        "cross_section_checks": payload.get("cross_section_checks") or [],
        "findings": payload.get("findings") or [],
        "facets_checked": payload.get("facets_checked") or [],
        "logic_facets": payload.get("logic_facets") or [],
        "checks": payload.get("checks") or [],
        "evidence": payload.get("evidence") or [],
        "uncertainties": payload.get("uncertainties") or [],
        "stats": payload.get("stats") or {},
    }


def write_outputs(payload, work_dir, output_dir):
    work_dir, output_dir = Path(work_dir), Path(output_dir)
    write_json(work_dir / "logic-check.json", payload)
    write_json(work_dir / "review-candidates.json", candidate_payload(payload))
    write_json(output_dir / "logic-check.json", payload)
    write_text(output_dir / "logic-precheck.md", render_report(payload))


def merge_into_work(candidates, work_path):
    work = load_json(work_path, {})
    if not isinstance(work, dict):
        work = {}
    work.setdefault("case_id", candidates.get("case_id") or "CASE")
    work.setdefault("mode", "logic-review")
    work.setdefault("summary", "")
    for field in ("findings", "claim_inventory", "cross_section_checks",
                  "facets_checked", "evidence", "checks", "uncertainties"):
        if not isinstance(work.get(field), list):
            work[field] = []
    existing_quotes = {norm_space(item.get("quote"))
                       for item in work["evidence"] if isinstance(item, dict)}
    merged_findings = 0
    used_ids = {str(item.get("id")) for item in work["findings"]
                if isinstance(item, dict)}
    existing_sig = {(item.get("rule_id"), norm_space(item.get("original_quote"))[:160])
                    for item in work["findings"] if isinstance(item, dict)}
    next_id = 1
    for row in candidates.get("findings") or []:
        sig = (row.get("rule_id"), norm_space(row.get("original_quote"))[:160])
        if sig in existing_sig:
            continue
        while "A%02d" % next_id in used_ids:
            next_id += 1
        new_row = dict(row)
        new_row["id"] = "A%02d" % next_id
        new_row["rule_status"] = row.get("rule_status") or row.get("status")
        new_row["status"] = "needs_recheck"
        work["findings"].append(new_row)
        used_ids.add(new_row["id"])
        existing_sig.add(sig)
        merged_findings += 1
        next_id += 1
        for item in new_row.get("evidence") or []:
            quote = norm_space(item.get("quote"))
            if quote and quote not in existing_quotes:
                work["evidence"].append(item)
                existing_quotes.add(quote)
    # Merge claims without overwriting model-authored rows.
    claim_sigs = {norm_space(row.get("claim")) for row in work["claim_inventory"]
                  if isinstance(row, dict)}
    for row in candidates.get("claim_inventory") or []:
        if norm_space(row.get("claim")) in claim_sigs:
            continue
        work["claim_inventory"].append(dict(row))
        claim_sigs.add(norm_space(row.get("claim")))
    check_sigs = {(norm_space(row.get("promise")), row.get("consistency"))
                  for row in work["cross_section_checks"] if isinstance(row, dict)}
    for row in candidates.get("cross_section_checks") or []:
        sig = (norm_space(row.get("promise")), row.get("consistency"))
        if sig not in check_sigs:
            work["cross_section_checks"].append(dict(row))
            check_sigs.add(sig)
    facet_names = {row.get("facet") for row in work["facets_checked"]
                   if isinstance(row, dict)}
    for row in candidates.get("facets_checked") or []:
        if row.get("facet") not in facet_names:
            work["facets_checked"].append(dict(row))
            facet_names.add(row.get("facet"))
    for row in candidates.get("checks") or []:
        if not any(isinstance(item, dict) and item.get("item") == row.get("item")
                   for item in work["checks"]):
            work["checks"].append(dict(row))
    for row in candidates.get("evidence") or []:
        quote = norm_space(row.get("quote"))
        if quote and quote not in existing_quotes:
            work["evidence"].append(dict(row))
            existing_quotes.add(quote)
    for row in candidates.get("uncertainties") or []:
        item = str(row.get("item") or "")
        if not any(isinstance(x, dict) and x.get("item") == item
                   for x in work["uncertainties"]):
            work["uncertainties"].append(dict(row))
    statuses = [row.get("status") for row in work["findings"]
                if isinstance(row, dict)]
    if "confirmed" in statuses:
        work["review_status"] = "issues"
    elif work.get("review_status") not in ("issues",):
        work["review_status"] = "needs_recheck"
    if not work.get("summary"):
        work["summary"] = ("逻辑预检产生 %d 条候选（合并 %d 条，全部标 needs_recheck），"
                           "主张 %d 条，跨章节核对 %d 条；规则只做候选，不替代审稿判断。"
                           % (len(candidates.get("findings") or []),
                              merged_findings,
                              len(candidates.get("claim_inventory") or []),
                              len(candidates.get("cross_section_checks") or [])))
    work["rule_review"] = {
        "script": "scripts/logic_review.py",
        "generated_at": candidates.get("generated_at"),
        "status": candidates.get("status"),
        "candidates": len(candidates.get("findings") or []),
        "merged": merged_findings,
        "precheck": "work/logic-check.json",
        "note": "merged rows use status=needs_recheck; confirm, edit or delete them",
    }
    write_json(work_path, work)
    return {"work": str(work_path), "merged": merged_findings,
            "findings": len(work["findings"])}

SELFTEST_MANUSCRIPT = """# Synthetic only - full argument review test

Synthetic only. Fabricated for selftest; not a real paper.

## Abstract
P1. We investigated whether social support mediates the relationship between stress and burnout.
P2. We randomized 240 employees and found a significant reduction in burnout (p=0.03).

## 1 Introduction
P3. Previous work [1] proves that social support explains burnout.
P4. We hypothesized that stress increases burnout and that social support mediates this effect.
P4b. We assume that burnout is stable over the study period.

## 2 Methods
P5. In a cross-sectional survey of 240 employees, stress correlated with burnout (r=0.48).
P6. Social support also correlated with burnout (r=-0.31).
P7. We did not conduct a mediation analysis.
P8. "Resilience" was defined as the ability to maintain performance under stress and was measured by performance under stress.
P9. Participants were randomly assigned to departments; allocation was by alternating floors.

## 3 Results
P10. Stress was associated with burnout after adjustment for age and sex (beta=0.44, 95% CI 0.31 to 0.57).
P11. All participants showed improvement after the intervention.
P12. Social support was measured with a three-item scale; higher scores indicate more support.

## 4 Discussion
P13. Stress causes burnout.
P14. Because social support was correlated with burnout, improving support will reduce burnout.
P14b. Social support mediated the effect of stress on burnout.
P15. Our findings prove that organizational interventions should target social support.
P15b. These findings generalize to all young adults.

## References
P16. [1] A qualitative interview study reporting that employees described support as helpful (n=12).
"""

SELFTEST_MISSING = """# Synthetic only - partial manuscript

Synthetic only.

## Abstract
P1. We investigated whether stress causes burnout.

## References
P2. [1] A qualitative interview study.
"""


def _selftest_quote_check(payload, text):
    for item in payload.get("evidence") or []:
        if item.get("path") != "manuscript.md":
            continue
        if not quote_in(text, item.get("quote")):
            return False
    for row in payload.get("claim_inventory") or []:
        for item in row.get("evidence") or []:
            if item.get("path") == "manuscript.md" and not quote_in(
                    text, item.get("quote")):
                return False
    return True


def run_selftest():
    results = {}
    with tempfile.TemporaryDirectory(prefix="logic-selftest-") as tmp:
        root = Path(tmp)
        success_input = root / "success-input"
        success_input.mkdir()
        (success_input / "manuscript.md").write_text(SELFTEST_MANUSCRIPT,
                                                     encoding="utf-8")
        (success_input / "case.json").write_text(json.dumps({
            "case_id": "SYNTH-LOGIC", "synthetic": True}), encoding="utf-8")
        success = analyze_paths(success_input, root / "success-work")
        write_outputs(success, root / "success-work", root / "success-output")
        rules = {row.get("rule_id") for row in success.get("findings") or []}
        expected = {"mediation_without_analysis", "causal_from_observational_design",
                    "generalization_overreach", "circular_definition",
                    "unsupported_result_claim", "randomization_inconsistency",
                    "cited_source_misattributed"}
        results["success"] = {
            "status_ok": success.get("status") == "ok_with_candidates",
            "expected_rules": not (expected - rules),
            "claims": len(success.get("claim_inventory") or []) >= 8,
            "speakers": {"author", "cited", "assumption"} <= {
                row.get("speaker") for row in success.get("claim_inventory") or []},
            "facets": len(success.get("facets_checked") or []) == 6,
            "logic_facets": len(success.get("logic_facets") or []) == 6,
            "cross_section": len(success.get("cross_section_checks") or []) >= 2,
            "quotes_verbatim": _selftest_quote_check(success, SELFTEST_MANUSCRIPT),
            "outputs": ((root / "success-output" / "logic-precheck.md").is_file()
                        and (root / "success-output" / "logic-check.json").is_file()
                        and (root / "success-work" / "review-candidates.json").is_file()),
        }
        empty_input = root / "empty-input"
        empty_input.mkdir()
        empty = analyze_paths(empty_input, root / "empty-work")
        write_outputs(empty, root / "empty-work", root / "empty-output")
        results["empty"] = {
            "status": empty.get("status") == "no_materials",
            "no_findings": not empty.get("findings"),
            "valid_json": bool(load_json(root / "empty-output" / "logic-check.json", {})),
            "uncertainty": bool(empty.get("uncertainties")),
        }
        missing_input = root / "missing-input"
        missing_input.mkdir()
        (missing_input / "manuscript.md").write_text(SELFTEST_MISSING,
                                                     encoding="utf-8")
        missing = analyze_paths(missing_input, root / "missing-work")
        write_outputs(missing, root / "missing-work", root / "missing-output")
        facet_status = {row.get("facet"): row.get("status")
                        for row in missing.get("facets_checked") or []}
        results["missing_sections"] = {
            "partial": missing.get("engine_status") == "partial",
            "methods_not_checked": facet_status.get("method_design") == "not_checked",
            "results_not_checked": facet_status.get("statistical_reporting") == "not_checked",
            "claims": bool(missing.get("claim_inventory")),
            "quotes_verbatim": _selftest_quote_check(missing, SELFTEST_MISSING),
        }
        # Merge path: candidates must be imported as needs_recheck.
        work_path = root / "merge-work" / "review-findings.json"
        write_json(work_path, {"case_id": "SYNTH-LOGIC", "findings": [],
                               "claim_inventory": [], "cross_section_checks": [],
                               "facets_checked": [], "evidence": []})
        merged = merge_into_work(success, work_path)
        work = load_json(work_path, {})
        results["merge"] = {
            "merged": merged.get("merged", 0) > 0,
            "all_needs_recheck": all(row.get("status") == "needs_recheck"
                                     for row in work.get("findings") or []),
            "claim_inventory": bool(work.get("claim_inventory")),
            "cross_section": bool(work.get("cross_section_checks")),
        }
    ok = all(value for group in results.values() for value in group.values())
    print(json.dumps({"selftest": "pass" if ok else "fail",
                      "synthetic_only": True, "cases": results},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


def error_payload(case_id, exc, trace):
    return {
        "case_id": case_id or "CASE",
        "mode": "logic-review",
        "generated_by": "scripts/logic_review.py",
        "generated_at": now_iso(),
        "status": "error",
        "engine_status": "error",
        "review_status": "not_checked",
        "summary": "逻辑预检脚本执行失败：%s: %s" % (type(exc).__name__, exc),
        "documents": [], "fulltext": {"sections": [], "paragraphs": 0,
                                      "sentences": 0},
        "claim_inventory": [], "cross_section_checks": [], "findings": [],
        "facets_checked": [], "logic_facets": [],
        "checks": [{"item": "脚本执行", "status": "fail",
                    "detail": "%s: %s" % (type(exc).__name__, exc)}],
        "evidence": [],
        "uncertainties": [{"item": "script_error", "reason": str(exc),
                           "impact": "没有产生可用候选",
                           "action": "查看 traceback 并修复输入或环境；仍可人工审查"}],
        "stats": {"findings": 0},
        "limits": ["规则引擎异常，未生成候选；不得据此声称已检查。"],
        "traceback": trace[-4000:],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="full-text argument review precheck")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--merge", action="store_true",
                        help="merge candidates into work/review-findings.json "
                             "with status=needs_recheck")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return run_selftest()
    work_dir, output_dir = Path(args.work), Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        payload = analyze_paths(args.input, work_dir)
    except Exception as exc:  # noqa: BLE001
        trace = traceback.format_exc()
        case = load_json(Path(args.input) / "case.json", {})
        payload = error_payload(str(case.get("case_id") or ""), exc, trace)
        try:
            write_outputs(payload, work_dir, output_dir)
        except Exception:  # noqa: BLE001
            print(trace, file=sys.stderr)
            return 2
        print(json.dumps({"status": "error", "error": str(exc)},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 0
    write_outputs(payload, work_dir, output_dir)
    merge_result = None
    if args.merge and payload.get("status") != "no_materials":
        merge_result = merge_into_work(
            payload, work_dir / "review-findings.json")
    print(json.dumps({
        "script": "logic_review.py",
        "case_id": payload.get("case_id"),
        "status": payload.get("status"),
        "engine_status": payload.get("engine_status"),
        "sections": len((payload.get("fulltext") or {}).get("sections") or []),
        "paragraphs": (payload.get("fulltext") or {}).get("paragraphs", 0),
        "claims": len(payload.get("claim_inventory") or []),
        "findings": len(payload.get("findings") or []),
        "cross_section_checks": len(payload.get("cross_section_checks") or []),
        "merge": merge_result,
        "precheck": str(work_dir / "logic-check.json"),
        "report": str(output_dir / "logic-precheck.md"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())





