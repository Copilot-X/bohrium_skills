#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic extraction and rule-based candidate findings for paper review.

The script reads the case material under ``--input`` (or the already-extracted
``work/materials.json``), segments it into sections, paragraphs, sentences,
captions and numeric/statistical statements, then applies narrow, auditable
rules. Every emitted finding carries verbatim quotes and a locator, so the
reviewer model can accept, downgrade or reject it without inventing content.

Division of labour:
  * this script finds *candidate* problems and coverage gaps;
  * the model reads the paper, verifies each candidate in context, decides the
    final severity/type and writes the report. Merged candidates are always
    imported with ``status=needs_recheck``; the model must confirm them.

Usage:
    python3 quality_review.py --input /input --work /app/work --output /app/output
    python3 quality_review.py --input /input --work /app/work --output /app/output --merge
    python3 quality_review.py --selftest

Exit codes:
    0  analysis written (honest input_error/partial/ok status)
    1  only when --selftest assertions fail
    2  unusable command line
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
try:  # parsers.py is packaged next to this script; text-only inputs work without it.
    import parsers  # type: ignore
except Exception:  # noqa: BLE001
    parsers = None

FACETS = [
    "fact_consistency",
    "citation_support",
    "method_design",
    "statistical_reporting",
    "argument_chain",
    "academic_compliance",
]
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".org", ".tex", ".latex", ".html",
                 ".json", ".csv", ".yaml", ".yml", ".bib", ".ris"}
BINARY_SUFFIXES = {".pdf", ".docx", ".doc"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".webp",
                  ".eps", ".gif"}
SKIP_NAMES = {"case.json", "task.md", "readme.md"}
MAX_DOC_CHARS = 4_000_000

MD_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$")
TEX_HEADING_RE = re.compile(r"^\s*\\(?:sub)*section\*?\s*\{([^}]*)\}")
NUMBER_PREFIX_RE = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s*")
PLAIN_HEADING_RE = re.compile(
    r"^(?:abstract|introduction|background|methods?|methodology|"
    r"materials and methods|methods and materials|materials & methods|"
    r"experimental section|experimental procedures?|experimental methods?|"
    r"study design|data and methods|participants and methods|"
    r"results?|findings?|results and discussion|experimental results|"
    r"main results|discussion|conclusions?|concluding remarks|limitations|"
    r"references?|bibliography|reference list|works cited|literature cited|"
    r"摘要|引言|前言|背景|方法|材料与方法|研究方法|实验部分|"
    r"结果|结果与讨论|讨论|结论|总结|局限|参考文献)$", re.I)
CAPTION_RE = re.compile(
    r"^\s*(?:figure|fig\.?|table|图|表)\s*\d+[A-Za-z]?\s*[.:：]?\s+\S", re.I)
FIGURE_REF_RE = re.compile(r"\b(?:figure|fig\.?)\s*(\d+[A-Za-z]?)", re.I)
TABLE_REF_RE = re.compile(r"\b(?:table)\s*(\d+[A-Za-z]?)", re.I)
FIGURE_ABSENT_RE = re.compile(
    r"(?:figure|fig\.?|图)\s*\d+[^.\n]{0,80}?"
    r"(?:not\s+(?:provided|shown|included|available|supplied)|missing|"
    r"未提供|未显示|缺失|不可见)", re.I)
P_RE = re.compile(
    r"\bp\s*(?:value\s*)?(?:=|<|>|≤|≥|≈)\s*(0?\.\d+|\d+(?:\.\d+)?)", re.I)
N_RE = re.compile(r"\b[nN]\s*=\s*(\d+)")
WORD_N_RE = re.compile(
    r"(\d+)\s+(?:participants|students|patients|subjects|volunteers|mice|"
    r"rats|samples|records|cases)\b", re.I)
CJK_N_RE = re.compile(r"(\d+)\s*(?:名|位|例)\s*(?:参与者|学生|患者|受试者|样本)?")
EFFECT_RE = re.compile(
    r"(?:cohen'?s\s+)?\bd\s*=\s*-?\d|η\s*[²2]|eta\s*(?:squared)?|"
    r"partial\s+eta|odds\s+ratio|\bOR\s*=|\bRR\s*=|\bHR\s*=|β\s*=|beta\s*=|"
    r"\br\s*=\s*-?0?\.\d+", re.I)
CI_RE = re.compile(r"95\s*%\s*(?:CI|confidence\s+interval)|"
                   r"confidence\s+interval|置信区间|\bCI\b", re.I)
TEST_RE = re.compile(r"\bt\s*\(\s*\d+|\bF\s*\(\s*\d+|chi[- ]?square|"
                     r"χ\s*[²2]|\bU\s*=|\bz\s*=|\bW\s*=|\bt\s*=", re.I)
SIG_RE = re.compile(r"\bsignificant(?:ly)?\b|显著性|显著", re.I)
NEG_SIG_RE = re.compile(r"\b(?:not|non|no)\s+significant|不显著|无统计学", re.I)
RANDOM_RE = re.compile(r"randomi[sz](?:ed|ation|e)\b|随机", re.I)
ALTERNATION_RE = re.compile(r"\balternat(?:e|ed|ely|ion|ing)\b|"
                            r"every other|sequential allocation|交替|轮流|奇偶", re.I)
OBSERVATIONAL_RE = re.compile(
    r"cross[- ]sectional|observational|survey|cohort|case[- ]control|"
    r"横断面|观察性|调查", re.I)
INTERVENTION_RE = re.compile(
    r"intervention|training|treatment|efficacy|improve|reduce|increase|"
    r"effect of|cause|干预|训练|治疗|疗效|改善|降低|提高|效果|因果", re.I)
HUMAN_RE = re.compile(
    r"\bparticipants?\b|\bstudents?\b|\bpatients?\b|\bsubjects?\b|"
    r"volunteers|被试|参与者|学生|患者|受试者", re.I)
ETHICS_RE = re.compile(
    r"\bethic|\bIRB\b|institutional review|ethics committee|consent|"
    r"伦理|知情同意", re.I)
COI_RE = re.compile(r"conflict of interest|competing interests|利益冲突", re.I)
DATA_RE = re.compile(
    r"data availability|data are available|available from|数据可用性|"
    r"数据可从|数据获取", re.I)
SAMPLE_CALC_RE = re.compile(
    r"sample[- ]size (?:calculation|estimate)|power (?:analysis|calculation)|"
    r"a priori sample|样本量(?:计算|估算)|检验效能|把握度", re.I)
SAMPLE_NO_CALC_RE = re.compile(
    r"(?:did not|didn't|no)\s+(?:perform|conduct|use)?[^.\n]{0,40}"
    r"(?:sample[- ]size|power)|no a priori sample|"
    r"未[^。\n]{0,10}(?:样本量|效能)", re.I)
FOLLOWUP_RE = re.compile(
    r"follow[- ]up|longitudinal|durable|long[- ]term|随访|纵向|长期|持久", re.I)
CAUSAL_RE = re.compile(
    r"\bcaus(?:e|es|ed|ing)\b|\blead(?:s|ing)?\s+to\b|\bresults?\s+in\b|"
    r"导致|引起|造成|使得", re.I)
GENERALIZE_RE = re.compile(
    r"generaliz|all (?:young )?adults|everyone|all populations|"
    r"普遍适用|所有(?:年轻)?成年人|推广到|适用于所有", re.I)
IN_TEXT_NUM_RE = re.compile(r"\[(\d+(?:\s*[,;–\-]\s*\d+)*)\]")
AUTHOR_YEAR_RE = re.compile(
    r"\(([A-Z][A-Za-z'\-]+(?:\s+(?:et al\.?|and\s+[A-Z][A-Za-z'\-]+))?)"
    r"[,\s]+((?:19|20)\d{2})[a-z]?\)")
CITE_RE = re.compile(r"\\cite[a-zA-Z]*\{([^}]+)\}")
REF_NUM_RE = re.compile(r"^\s*\[(\d+)\]\s*(.+)$")
REF_PLAIN_RE = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")
BIBITEM_RE = re.compile(r"^\s*\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}\s*(.*)$")
YEAR_RE = re.compile(r"(?:19|20)\d{2}")
INCOMPLETE_META_RE = re.compile(
    r"incomplete|unknown|anonymous|missing (?:author|year)|不详|缺失|未注明", re.I)
INCOMPATIBLE_DESIGN_RE = re.compile(
    r"cross[- ]sectional|observational|survey|no intervention|protocol|"
    r"editorial|commentary|case report|animal|in vitro|"
    r"横断面|观察性|调查|未设干预|动物|体外", re.I)


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


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


def norm_space(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def quote_in(source, quote):
    return bool(norm_space(quote)) and norm_space(quote) in norm_space(source)


def canonical_sections(heading):
    """Map a heading to zero or more canonical review sections."""
    text = NUMBER_PREFIX_RE.sub("", norm_space(heading)).strip(" .:：")
    low = text.lower()
    names = []
    if re.search(r"\babstract\b|摘要", low):
        names.append("abstract")
    if re.search(r"\bintroduction\b|\bbackground\b|引言|前言|背景", low):
        names.append("introduction")
    if re.search(r"\bmethods?\b|\bmethodology\b|materials and methods|"
                 r"methods and materials|materials & methods|"
                 r"experimental (?:section|procedures?|methods?)|study design|"
                 r"data and methods|participants and methods|"
                 r"方法|材料与方法|实验部分", low):
        names.append("methods")
    if re.search(r"\bresults?\b|\bfindings?\b|experimental results|"
                 r"main results|结果", low):
        names.append("results")
    if re.search(r"\bdiscussion\b|讨论", low):
        names.append("discussion")
    if re.search(r"\bconclusions?\b|concluding remarks|结论|总结", low):
        names.append("conclusion")
    if re.search(r"\breferences?\b|\bbibliography\b|reference list|"
                 r"works cited|literature cited|参考文献", low):
        names.append("references")
    if re.search(r"\blimitations?\b|局限", low):
        names.append("limitations")
    return names


def plain_heading(line):
    text = NUMBER_PREFIX_RE.sub("", norm_space(line)).strip(" .:：")
    if not text or len(text) > 70:
        return None
    if PLAIN_HEADING_RE.match(text):
        return text
    return None


def sentence_units(text):
    """Yield (start, end, sentence) spans so quotes stay exact substrings."""
    units = []
    start = 0
    for match in re.finditer(r"[.!?。！？](?=\s|$)", text):
        end = match.end()
        piece = text[start:end].strip()
        if piece:
            units.append(piece)
        start = end
    tail = text[start:].strip()
    if tail:
        units.append(tail)
    return units or ([text.strip()] if text.strip() else [])


def detect_kind(lines):
    stripped = [line.strip() for line in lines if line.strip()]
    if not stripped:
        return "paragraph"
    if CAPTION_RE.match(stripped[0]):
        return "caption"
    if all(line.startswith("|") for line in stripped):
        return "table"
    return "paragraph"


def parse_document(doc):
    """Split one document into sections, paragraphs, captions and stats."""
    text = str(doc.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    paragraphs, section_names, captions = [], [], []
    current = ["body"]
    buf = []
    used_ids = set()
    explicit_mode = any(re.match(r"^\s*P\d+[.)]\s+", line) for line in lines)
    unlabeled = [0]

    def new_id(preferred=None):
        if preferred and preferred not in used_ids:
            used_ids.add(preferred)
            return preferred
        if explicit_mode:
            # Keep the author/paragraph labels used by the source; unlabeled
            # blocks must not renumber or collide with them.
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
        kind = detect_kind(content.split("\n"))
        where = ("%s (line %d)" % (pid, first_line) if first_line else pid)
        para = {"id": pid, "path": doc.get("path") or "material",
                "section": current[0], "sections": list(current),
                "text": content, "kind": kind, "locator": where,
                "line_start": first_line, "line_end": end_line or 0}
        paragraphs.append(para)
        if kind == "caption":
            captions.append(para)

    start_line = None
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
                named = plain_heading(line)
                if named:
                    heading = named
        if heading is not None:
            flush(index - 1)
            names = canonical_sections(heading)
            if names:
                current = names
                section_names.extend(names)
            continue
        # An explicit "P12." marker starts a new source paragraph even when
        # the author did not put a blank line between reference entries.
        if start_line is not None and re.match(r"^\s*P\d+[.)]\s+", line):
            flush(index - 1)
        if not line.strip():
            flush(index - 1)
            continue
        if start_line is None:
            start_line = index
        buf.append(line.rstrip("\n"))
    flush(len(lines))
    # Keep section_names ordered and unique.
    ordered = []
    for name in section_names:
        if name not in ordered:
            ordered.append(name)
    return {"paragraphs": paragraphs, "section_names": ordered,
            "captions": captions}


def paragraph_stats(para):
    text = para.get("text") or ""
    p_values = []
    for match in P_RE.finditer(text):
        raw = match.group(1)
        try:
            value = float(raw)
        except ValueError:
            continue
        p_values.append({"value": value, "raw": match.group(0),
                         "start": match.start(), "end": match.end()})
    n_values = [int(m.group(1)) for m in N_RE.finditer(text)]
    n_values += [int(m.group(1)) for m in WORD_N_RE.finditer(text)]
    n_values += [int(m.group(1)) for m in CJK_N_RE.finditer(text)]
    return {
        "p_values": p_values,
        "n_values": n_values,
        "has_effect": bool(EFFECT_RE.search(text)),
        "has_ci": bool(CI_RE.search(text)),
        "has_test_stat": bool(TEST_RE.search(text)),
        "has_significance": bool(SIG_RE.search(text)),
        "significance_negated": bool(NEG_SIG_RE.search(text)),
    }


def unit(para, sentence=None):
    quote = (sentence or para.get("text") or "").strip()
    return {"path": para.get("path") or "material",
            "locator": para.get("locator") or "locator",
            "quote": quote}


def finding(rule_id, facet, anchor, evidence, severity, ftype, status,
            confidence, impact, fix, fp_check, rule_status=None):
    evidence = dedupe_units(evidence)[:5]
    quote = anchor.get("quote") or ""
    if len(norm_space(quote)) < 8 and evidence:
        quote = evidence[0].get("quote") or quote
    return {
        "rule_id": rule_id,
        "facet": facet,
        "locator": {"path": anchor.get("path"), "locator": anchor.get("locator")},
        "original_quote": quote,
        "severity": severity,
        "type": ftype,
        "status": status,
        "confidence": confidence,
        "rule_status": rule_status or status,
        "impact": impact,
        "fix": fix,
        "false_positive_check": fp_check,
        "evidence": evidence,
        "generated_by": "scripts/quality_review.py",
    }


def dedupe_units(items):
    seen, out = set(), []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (item.get("path"), item.get("locator"), norm_space(item.get("quote")))
        if key in seen or not norm_space(item.get("quote")):
            continue
        seen.add(key)
        out.append(item)
    return out


def dedupe_findings(findings):
    seen, out = set(), []
    for item in findings:
        key = (item.get("rule_id"), norm_space(item.get("original_quote"))[:160])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def scan_input(input_dir, parse_binary=True):
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
        if suffix not in TEXT_SUFFIXES | BINARY_SUFFIXES:
            continue
        if path.stat().st_size > MAX_DOC_CHARS:
            continue
        if suffix in BINARY_SUFFIXES:
            if not parse_binary or parsers is None:
                continue
            try:
                doc = parsers.extract_any(path)
            except Exception as exc:  # noqa: BLE001
                docs.append({"path": rel, "kind": suffix.lstrip("."), "text": "",
                             "warnings": ["extract failed: %s" % exc]})
                continue
            text = doc.get("text") or ""
            if doc.get("kind") == "json" and doc.get("data") is not None:
                text = text or json.dumps(doc.get("data"), ensure_ascii=False)
            docs.append({"path": rel, "kind": doc.get("kind") or suffix.lstrip("."),
                         "text": text, "warnings": doc.get("warnings") or []})
            continue
        docs.append({"path": rel, "kind": suffix.lstrip("."),
                     "text": read_text(path), "warnings": []})
    return docs, assets


def load_documents(input_dir, work_dir):
    """Prefer prepare's materials.json; otherwise scan --input directly.

    When prepare already extracted PDF/DOCX text, do not parse those binaries a
    second time (OCR would double the runtime budget); only collect assets and
    text files that are missing from the prepared material list.
    """
    materials = load_json(Path(work_dir) / "materials.json", {})
    prepared = []
    for doc in (materials.get("documents") or []) if isinstance(materials, dict) else []:
        text = str(doc.get("text") or "")
        if text.strip():
            prepared.append({"path": doc.get("path") or "material",
                             "kind": doc.get("kind") or "text", "text": text,
                             "warnings": doc.get("warnings") or []})
    docs, assets = scan_input(input_dir, parse_binary=not prepared)
    merged = {doc["path"]: doc for doc in docs}
    for doc in prepared:
        merged.setdefault(doc["path"], doc)
    ordered = sorted(merged.values(),
                     key=lambda d: (d.get("path") or "").lower())
    return ordered, assets


def score_document(doc):
    text = doc.get("text") or ""
    if len(text.strip()) < 40 or doc.get("kind") in ("bib", "ris", "csv", "json"):
        return -1000
    score = min(len(text), 200000) / 1000.0
    parsed = parse_document(doc)
    score += 25 * len(parsed["section_names"])
    name = (doc.get("path") or "").lower()
    if any(word in name for word in ("manuscript", "paper", "article", "source",
                                     "material", "primary", "main")):
        score += 30
    if doc.get("kind") in ("pdf", "docx", "tex", "md", "text"):
        score += 15
    return score


def choose_primary(docs):
    ranked = sorted(docs, key=score_document, reverse=True)
    return ranked[0] if ranked and score_document(ranked[0]) > 0 else None


# === RULES ===


def paras_in(parsed, name):
    return [p for p in parsed["paragraphs"]
            if name in (p.get("sections") or [p.get("section")])]


def sentence_items(paras):
    for para in paras:
        for sentence in sentence_units(para.get("text") or ""):
            yield para, sentence


def first_p_sentence(paras):
    for para, sentence in sentence_items(paras):
        stats = paragraph_stats({"text": sentence})
        if stats["p_values"]:
            return para, sentence, stats["p_values"][0]
    return None


def significance_conflicts(parsed):
    """'significant' paired with p >= 0.05 in the same sentence."""
    out = []
    for para in parsed["paragraphs"]:
        for sentence in sentence_units(para.get("text") or ""):
            if not SIG_RE.search(sentence) or NEG_SIG_RE.search(sentence):
                continue
            stats = paragraph_stats({"text": sentence})
            for p_value in stats["p_values"]:
                if p_value["value"] >= 0.05:
                    out.append((para, sentence, p_value))
                    break
    return out


def rule_structure(parsed):
    gaps = []
    checks = {}
    required = [("abstract", "fact_consistency",
                 "摘要缺失，无法核对摘要与正文的一致性"),
                ("methods", "method_design",
                 "方法段缺失，无法核对对照、随机化、样本量与纳排标准"),
                ("results", "statistical_reporting",
                 "结果段缺失，无法核对效应量、置信区间与 p 值口径"),
                ("references", "citation_support",
                 "参考文献段缺失，无法建立引用-主张对照")]
    have = set(parsed["section_names"])
    for name, facet, reason in required:
        if name in have:
            continue
        gaps.append({
            "item": "%s section" % name,
            "facet": facet,
            "status": "not_checked",
            "reason": reason,
            "impact": "该检查面只能基于现有片段部分核对，不能判为通过",
            "action": "补充%s后重新运行 quality_review.py 与分面审查" % name,
        })
    checks["section_inventory"] = {
        "facet": "fact_consistency",
        "items": sorted(have),
        "detail": "识别到章节: %s" % (", ".join(parsed["section_names"]) or "none"),
    }
    return gaps, checks


def rule_p_values(parsed):
    findings = []
    abstract = paras_in(parsed, "abstract")
    results = paras_in(parsed, "results")
    abstract_first = first_p_sentence(abstract)
    result_ps = []
    for para in results:
        got = first_p_sentence([para])
        if got:
            result_ps.append(got)
    if abstract_first and result_ps:
        abs_para, abs_sentence, abs_p = abstract_first
        first_para, first_sentence, first_p = result_ps[0]
        if abs(abs_p["value"] - first_p["value"]) > 1e-9:
            evidence = [unit(abs_para, abs_sentence),
                        unit(first_para, first_sentence)]
            later_match = [row for row in result_ps[1:]
                           if abs(row[2]["value"] - abs_p["value"]) <= 1e-9]
            for para, sentence, _ in later_match[:2]:
                evidence.append(unit(para, sentence))
            # The rule cannot know whether the abstract reports a prespecified
            # primary analysis, a secondary outcome or a post-hoc exclusion.
            status = "needs_recheck"
            note = ("摘要报告 %s，结果段首个 p 值为 %s" %
                    (abs_p["raw"], first_p["raw"]))
            if later_match:
                note += "；后续分析出现与摘要相同的 p 值，需确认是否为主分析或事后排除分析"
            findings.append(finding(
                "p_value_mismatch", "fact_consistency",
                unit(abs_para, abs_sentence), evidence,
                "major", "error", status, "high",
                note + "；若两处不是同一分析，摘要必须标明口径，不能并列为主结果。",
                "在摘要中报告预先设定的主分析及其 p 值；若引用排除/校正后的分析，"
                "须在摘要中注明该分析并保持与结果段一致。",
                "已核对两处是否来自不同结局、不同检验或不同分析集；"
                "若是不同分析，仍须在摘要标明分析口径，不能并列为主结果。"))
    for para, sentence, p_value in significance_conflicts(parsed):
        findings.append(finding(
            "significance_threshold_conflict", "statistical_reporting",
            unit(para, sentence), [unit(para, sentence)],
            "major", "error", "confirmed", "high",
            "句子称显著但同一句给出的 %s 未达到 0.05 阈值。" % p_value["raw"],
            "改写为不显著，或报告正确的检验结果；若使用校正后的 p 值须注明。",
            "已排除 not/non-significant 的否定表述，并核对 p 值取自同一句。"))
    return findings


def rule_randomization(parsed):
    scope = paras_in(parsed, "abstract") + paras_in(parsed, "methods")
    random_units, alternation_units = [], []
    for para, sentence in sentence_items(scope):
        if RANDOM_RE.search(sentence) and re.search(
                r"participant|student|patient|subject|assign|allocat|group|"
                r"被试|参与者|学生|患者|分组|分配", sentence, re.I):
            random_units.append((para, sentence))
        if ALTERNATION_RE.search(sentence) and re.search(
                r"assign|allocat|group|order|waitlist|control|分配|分组|顺序|对照",
                sentence, re.I):
            alternation_units.append((para, sentence))
    if not random_units or not alternation_units:
        return []
    random_para, random_sentence = random_units[0]
    alt_para, alt_sentence = alternation_units[-1]
    return [finding(
        "randomization_conflict", "method_design",
        unit(random_para, random_sentence),
        [unit(random_para, random_sentence), unit(alt_para, alt_sentence)],
        "major", "error", "confirmed", "high",
        "摘要/方法称随机分配，但方法段的分配方式写成交替分配；两者不能同时成立。",
        "统一为实际分配方式：若为交替分配，删除 randomized 并说明分配隐藏；"
        "若确为随机分配，补充随机序列生成与分配隐藏方法。",
        "已核对 two phrases 都指向分组分配而非抽样；若 alternately 指其他流程，"
        "属于措辞错误而非设计缺陷，可降为 reporting error。")]


def rule_sample_size(parsed, full_text):
    findings, gaps = [], []
    explicit = None
    for para, sentence in sentence_items(parsed["paragraphs"]):
        if SAMPLE_NO_CALC_RE.search(sentence):
            explicit = (para, sentence)
            break
    if not explicit and SAMPLE_CALC_RE.search(full_text):
        return findings, gaps
    if explicit:
        para, sentence = explicit
        findings.append(finding(
            "sample_size_not_reported", "method_design",
            unit(para, sentence), [unit(para, sentence)],
            "major", "insufficient_evidence", "confirmed", "high",
            "未做先验样本量/检验效能计算，研究可能把握度不足，停止规则不明确。",
            "补充假定的效应量、α、把握度与所需样本量；若为探索性研究，明确声明"
            "并讨论精度限制。",
            "已检查全文是否在别处报告样本量计算；本句明确说明未做。"))
    else:
        gaps.append({
            "item": "sample size calculation",
            "facet": "method_design",
            "status": "not_checked",
            "reason": "材料中没有找到样本量计算/检验效能说明",
            "impact": "无法判断样本量依据；可能是报告位置缺失而非研究缺陷",
            "action": "补充方法段或补充材料中的样本量计算；模型需人工复核",
        })
    return findings, gaps


def rule_effect_reporting(parsed, full_text):
    findings = []
    results = paras_in(parsed, "results")
    if not results:
        # Missing results is a structure gap, not evidence that reporting is
        # incomplete: do not manufacture an effect-size finding from the abstract.
        return findings
    p_sentence = first_p_sentence(results)
    if not p_sentence:
        return findings
    all_stats = " ".join((p.get("text") or "") for p in parsed["paragraphs"])
    has_effect = bool(EFFECT_RE.search(full_text))
    has_ci = bool(CI_RE.search(full_text))
    has_test = bool(TEST_RE.search(all_stats))
    para, sentence, p_value = p_sentence
    if not has_effect:
        missing = ["效应量"]
        if not has_ci:
            missing.append("95% 置信区间")
        if not has_test:
            missing.append("检验统计量")
        findings.append(finding(
            "effect_size_missing", "statistical_reporting",
            unit(para, sentence), [unit(para, sentence)],
            "major", "insufficient_evidence", "needs_recheck", "medium",
            "结果只报告 p 值，缺少%s，读者无法判断效应大小与精度。" % "、".join(missing),
            "补报均值差/效应量及其 95% 置信区间、检验统计量与自由度，并说明分析集。",
            "已检索全文（含表格）的效应量、CI 与检验统计量标记；"
            "若目标期刊明确不要求 CI，仍应报告效应量与检验统计量。"))
    elif not has_ci:
        findings.append(finding(
            "confidence_interval_missing", "statistical_reporting",
            unit(para, sentence), [unit(para, sentence)],
            "minor", "suggestion", "needs_recheck", "medium",
            "报告了效应量但未见 95% 置信区间，精度信息不足。",
            "补报效应量的 95% 置信区间；若期刊格式不允许，在正文或表中说明。",
            "已检索全文 CI 标记；若表格另附 CI，应删除本候选。"))
    return findings


# === RULES2 ===


HEDGE_RE = re.compile(r"\bmay\b|\bmight\b|\bcould\b|\bsuggest|\bassociated\b|"
                      r"可能|提示|相关|关联", re.I)
LONGITUDINAL_RE = re.compile(r"follow[- ]up|longitudinal|随访|纵向", re.I)


def design_evidence(parsed):
    methods = paras_in(parsed, "methods")
    for para, sentence in sentence_items(methods):
        if ALTERNATION_RE.search(sentence) or OBSERVATIONAL_RE.search(sentence):
            return para, sentence, "nonrandom"
    for para, sentence in sentence_items(methods):
        if HUMAN_RE.search(sentence):
            return para, sentence, "unclear"
    return None, None, ""


def rule_argument(parsed):
    findings = []
    discussion = paras_in(parsed, "discussion") + paras_in(parsed, "conclusion")
    design_para, design_sentence, design_state = design_evidence(parsed)
    causal_hit = False
    for para, sentence in sentence_items(discussion):
        if not CAUSAL_RE.search(sentence) or HEDGE_RE.search(sentence):
            continue
        if re.search(r"\bcannot\b|\bcan not\b|\bno\b|无法|不能", sentence, re.I):
            continue
        evidence = [unit(para, sentence)]
        if design_para is not None:
            evidence.append(unit(design_para, design_sentence))
        status = "needs_recheck"
        findings.append(finding(
            "causal_overclaim", "argument_chain",
            unit(para, sentence), evidence,
            "major", "error" if design_state == "nonrandom" else "suggestion",
            status, "medium" if design_state == "nonrandom" else "low",
            "结论使用因果措辞，但设计段%s；因果解释超出可支持的推断范围。" %
            ("为交替分配/观察性设计" if design_state == "nonrandom"
             else "未说明随机化与混杂控制"),
            "把因果句改为关联或限定表述；若坚持因果结论，补充随机化、"
            "对照与混杂控制证据。",
            "已检查句内 hedge（may/suggest/associated）与否定表述；"
            "若作者在讨论中另有局限声明，可降为 suggestion。"))
        causal_hit = True
        break
    if not causal_hit:
        for para, sentence in sentence_items(discussion):
            if not re.search(r"\bdurable\b|\blong[- ]term\b|持久|长期", sentence, re.I):
                continue
            if LONGITUDINAL_RE.search(" ".join(
                    (p.get("text") or "") for p in
                    paras_in(parsed, "methods") + paras_in(parsed, "results"))):
                continue
            findings.append(finding(
                "durability_overclaim", "argument_chain",
                unit(para, sentence), [unit(para, sentence)],
                "major", "error", "needs_recheck", "medium",
                "结论声称效果持久/长期，但方法或结果段没有随访设计或长期测量。",
                "改为仅描述观察期内的结果；若要声称持久性，补充随访时间点与分析。",
                "已检查方法与结果段是否报告 follow-up/longitudinal；"
                "若随访数据在补充材料中，应删除本候选。"))
            break
    for para, sentence in sentence_items(discussion + paras_in(parsed, "abstract")):
        if not GENERALIZE_RE.search(sentence):
            continue
        sample = None
        for candidate, sample_sentence in sentence_items(
                paras_in(parsed, "methods") + paras_in(parsed, "abstract")):
            if HUMAN_RE.search(sample_sentence) and re.search(
                    r"\d+|students?|patients?|single|one site|学生|患者|单中心", sample_sentence, re.I):
                sample = (candidate, sample_sentence)
                break
        if not sample:
            continue
        evidence = [unit(para, sentence), unit(sample[0], sample[1])]
        findings.append(finding(
            "generalization_overreach", "argument_chain",
            unit(para, sentence), evidence,
            "major", "error", "needs_recheck", "medium",
            "结论把结果外推到超出样本范围的人群/情境，缺少抽样框架与异质性依据。",
            "把结论限定在已抽样人群与情境；若主张可推广，补充抽样代表性、"
            "站点/人群差异与效应异质性分析。",
            "已核对样本描述与结论范围；学科惯例允许在局限段讨论推广性，"
            "但不应作为无条件的结论陈述。"))
        break
    return findings


def parse_references(parsed):
    refs, entries = {}, []
    for para in paras_in(parsed, "references"):
        for line in (para.get("text") or "").splitlines():
            text = line.strip()
            if not text:
                continue
            bib = BIBITEM_RE.match(text)
            if bib:
                key = bib.group(1)
                content = bib.group(2).strip() or text
                row = {"number": None, "cite_key": key, "text": text,
                       "content": content, "para": para}
                refs.setdefault(key, row)
                entries.append(row)
                continue
            match = REF_NUM_RE.match(text) or REF_PLAIN_RE.match(text)
            if match:
                number = int(match.group(1))
                content = match.group(2).strip()
                row = {"number": number, "text": text, "content": content,
                       "para": para}
                refs.setdefault(number, row)
                entries.append(row)
            else:
                entries.append({"number": None, "text": text,
                                "content": text, "para": para})
    return refs, entries


def expand_citation_numbers(raw):
    numbers = []
    for part in re.split(r"[,;]", raw):
        part = part.strip()
        span = re.match(r"^(\d+)\s*[–\-]\s*(\d+)$", part)
        if span:
            low, high = int(span.group(1)), int(span.group(2))
            if 0 < high - low <= 20:
                numbers.extend(range(low, high + 1))
                continue
        if part.isdigit():
            numbers.append(int(part))
    return sorted(set(numbers))


def citation_occurrences(parsed):
    rows = []
    for para in parsed["paragraphs"]:
        if "references" in (para.get("sections") or []):
            continue
        text = para.get("text") or ""
        for match in IN_TEXT_NUM_RE.finditer(text):
            sentence = next((s for s in sentence_units(text)
                             if match.group(0) in s), text)
            for number in expand_citation_numbers(match.group(1)):
                rows.append({"para": para, "sentence": sentence,
                             "number": number, "raw": match.group(0)})
        for match in AUTHOR_YEAR_RE.finditer(text):
            sentence = next((s for s in sentence_units(text)
                             if match.group(0) in s), text)
            rows.append({"para": para, "sentence": sentence, "number": None,
                         "raw": match.group(0), "author_year": match.group(1)})
        for match in CITE_RE.finditer(text):
            sentence = next((s for s in sentence_units(text)
                             if match.group(0) in s), text)
            for key in (part.strip() for part in match.group(1).split(",")):
                if key:
                    rows.append({"para": para, "sentence": sentence,
                                 "number": None, "cite_key": key,
                                 "raw": match.group(0)})
    return rows


def rule_citations(parsed):
    findings, matrix, gaps = [], [], []
    refs, entries = parse_references(parsed)
    occurrences = citation_occurrences(parsed)
    matrix_index = 1
    for occurrence in occurrences:
        para = occurrence["para"]
        sentence = occurrence["sentence"]
        number = occurrence.get("number")
        cite_key = occurrence.get("cite_key")
        if number is None and not cite_key:
            matrix.append({
                "claim_id": "CS%02d" % matrix_index,
                "claim": sentence, "claim_locator": "%s %s" % (
                    para.get("path"), para.get("locator")),
                "source": occurrence.get("raw") or "author-year citation",
                "cited_source": occurrence.get("raw") or "author-year citation",
                "source_statement": "Reference entry is not identifiable from the supplied material.",
                "source_locator": "not supplied",
                "verdict": "unverifiable",
                "note": "只有正文中的作者-年份引用，材料未提供可匹配的文献条目。",
            })
            matrix_index += 1
            continue
        if number is not None:
            ref = refs.get(number)
            source_id = "[%d]" % number
        else:
            ref = refs.get(cite_key)
            source_id = "\\cite{%s}" % cite_key
        claim = sentence
        if ref is None:
            findings.append(finding(
                "citation_reference_missing", "citation_support",
                unit(para, sentence), [unit(para, sentence)],
                "minor", "insufficient_evidence", "needs_recheck", "medium",
                "正文引用 %s 在参考文献段找不到对应条目，无法核对支持关系。" % source_id,
                "补全参考文献条目（作者、年份、题名、来源、DOI）或修正引用编号。",
                "已检查参考文献段的所有编号/键条目；若材料是节选导致条目缺失，"
                "应在报告中标注为材料缺口而非作者错误。"))
            matrix.append({
                "claim_id": "CS%02d" % matrix_index, "claim": claim,
                "claim_locator": "%s %s" % (para.get("path"), para.get("locator")),
                "source": source_id, "cited_source": source_id,
                "source_statement": "No matching reference entry in the supplied material.",
                "source_locator": "references section",
                "verdict": "unverifiable",
                "note": "引用标识无对应条目。",
            })
            matrix_index += 1
            continue
        incompatible = INCOMPATIBLE_DESIGN_RE.search(ref["content"])
        claim_is_intervention = bool(INTERVENTION_RE.search(claim))
        verdict = "unverifiable"
        note = "仅凭文献条目无法判断其结论是否支持本文论断，需查看摘要/全文。"
        if claim_is_intervention and incompatible:
            verdict = "none"
            note = "被引文献的设计（%s）与本文的干预/因果论断不匹配。" % (
                incompatible.group(0))
            status = "needs_recheck"
            findings.append(finding(
                "citation_claim_mismatch", "citation_support",
                unit(para, sentence),
                [unit(para, sentence), unit(ref["para"], ref["text"])],
                "major", "error", status, "high",
                "被引文献的设计/结论不能支持所在论断，引用与主张错配。",
                "改写论断为被引文献实际支持的关联表述，或改引与论断设计匹配的"
                "原始研究/系统综述；保留该引用时只用于其真正支持的陈述。",
                "已核对被引文献条目的研究设计关键词；若条目只是节选或引用用于"
                "背景铺垫而非直接证据，应降级或删除该候选。"))
        matrix.append({
            "claim_id": "CS%02d" % matrix_index, "claim": claim,
            "claim_locator": "%s %s" % (para.get("path"), para.get("locator")),
            "source": source_id, "cited_source": source_id,
            "source_statement": ref["text"],
            "source_locator": "%s %s" % (ref["para"].get("path"),
                                         ref["para"].get("locator")),
            "verdict": verdict, "note": note,
        })
        matrix_index += 1
    seen, deduped = set(), []
    for row in matrix:
        key = (row["claim"], row["source"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    for entry in entries:
        if INCOMPLETE_META_RE.search(entry["text"]):
            para = entry["para"]
            findings.append(finding(
                "reference_metadata_incomplete", "citation_support",
                unit(para, entry["text"]), [unit(para, entry["text"])],
                "minor", "insufficient_evidence", "needs_recheck", "high",
                "参考文献条目元数据不完整（缺作者/年份等），无法核对来源。",
                "补全作者、年份、题名、期刊/会议、卷页与 DOI；无法补全时删除该引用。",
                "已检查条目文本的年份与显式缺失标记；若元数据在补充材料中，"
                "应把本条改为材料缺口。"))
    lacking_year = [e["text"] for e in entries if not YEAR_RE.search(e["text"])]
    if lacking_year:
        gaps.append({
            "item": "reference metadata",
            "facet": "citation_support",
            "status": "not_checked",
            "reason": "%d 条参考文献未见出版年份，无法逐条核对来源" % len(lacking_year),
            "impact": "引用元数据可核验性不足",
            "action": "补充完整参考文献导出；模型逐条复核缺年份条目。",
            "examples": lacking_year[:5],
        })
    return findings, deduped, gaps


def rule_figures(parsed, assets, full_text):
    findings, gaps = [], []
    explicit = set()
    for para, sentence in sentence_items(parsed["paragraphs"]):
        if FIGURE_ABSENT_RE.search(sentence):
            findings.append(finding(
                "figure_not_provided", "fact_consistency",
                unit(para, sentence), [unit(para, sentence)],
                "minor", "insufficient_evidence", "needs_recheck", "high",
                "正文引用的图未随材料提供，图内信息与相关论断无法核对。",
                "补充图文件或删除对该图的依赖；若图在补充材料中，注明位置并纳入复核。",
                "已检查输入中的图片资源与图注；若图片实际存在但解析器未读取，"
                "应删除本候选并改为人工核对。"))
            for number in FIGURE_REF_RE.findall(sentence):
                explicit.add(number)
    caption_numbers = set()
    for para in parsed["captions"]:
        first = (para.get("text") or "").split("\n")[0]
        match = re.match(r"^\s*(?:figure|fig\.?|table|图|表)\s*(\d+[A-Za-z]?)",
                         first, re.I)
        if match:
            caption_numbers.add(match.group(1))
    referenced = set(FIGURE_REF_RE.findall(full_text)) | set(
        TABLE_REF_RE.findall(full_text))
    missing = sorted(referenced - caption_numbers - explicit,
                     key=lambda x: (len(x), x))
    for number in missing[:6]:
        gaps.append({
            "item": "figure/table %s" % number,
            "facet": "fact_consistency",
            "status": "not_checked",
            "reason": "正文引用了图/表 %s，但材料中未见对应图注" % number,
            "impact": "图表与正文的一致性无法核对",
            "action": "补充图注/图文件；可用图片资源: %s" %
                      (", ".join(assets[:5]) if assets else "none"),
        })
    return findings, gaps


def rule_compliance(parsed, full_text):
    findings = []
    anchor = None
    for para, sentence in sentence_items(parsed["paragraphs"]):
        if HUMAN_RE.search(sentence) and re.search(
                r"\d+|students?|patients?|participants?|参与者|学生|患者", sentence, re.I):
            anchor = (para, sentence)
            break
    if anchor is None:
        return findings
    para, sentence = anchor
    ethics = bool(ETHICS_RE.search(full_text))
    data = bool(DATA_RE.search(full_text))
    coi = bool(COI_RE.search(full_text))
    if not ethics:
        extra = "" if data else "、数据可用性声明"
        findings.append(finding(
            "compliance_declarations_missing", "academic_compliance",
            unit(para, sentence), [unit(para, sentence)],
            "major", "norm_violation", "needs_recheck", "medium",
            "涉及人类参与者的研究未见伦理审批/知情同意声明%s，规范性信息缺失。" % extra,
            "补充伦理委员会名称与批件号、知情同意方式%s；若为豁免审查，"
            "写明豁免依据。" % ("" if data else "，以及数据可用性声明"),
            "已检索全文的 ethics/IRB/consent/data availability 关键词；"
            "声明可能位于投稿系统或补充文件，若存在应删除本候选。"))
    elif not data:
        findings.append(finding(
            "data_availability_missing", "academic_compliance",
            unit(para, sentence), [unit(para, sentence)],
            "minor", "norm_violation", "needs_recheck", "medium",
            "未见数据可用性声明，读者无法判断数据获取方式。",
            "补充数据可用性声明（仓库、受控访问条件或不可共享原因）。",
            "已检索全文 data availability/available from 等表述；"
            "若目标期刊允许在投稿系统单独提交，可降为 suggestion。"))
    if not coi:
        findings.append(finding(
            "coi_statement_missing", "academic_compliance",
            unit(para, sentence), [unit(para, sentence)],
            "info", "suggestion", "needs_recheck", "low",
            "未见利益冲突声明，属于常见投稿规范缺口但不影响研究结论。",
            "补充利益冲突声明；无冲突时明确写 none declared。",
            "已检索 conflict of interest/competing interests；"
            "若期刊不要求正文声明，可删除本候选。"))
    return findings


def rule_number_consistency(parsed):
    findings = []
    abstract, methods = paras_in(parsed, "abstract"), paras_in(parsed, "methods")
    abs_ns, abs_anchor = set(), None
    method_ns, method_anchor = set(), None
    for para in abstract:
        values = paragraph_stats(para)["n_values"]
        if values:
            abs_ns.update(values)
            abs_anchor = abs_anchor or (para, sentence_units(para["text"])[0])
    for para in methods:
        values = paragraph_stats(para)["n_values"]
        if values:
            method_ns.update(values)
            method_anchor = method_anchor or (para, sentence_units(para["text"])[0])
    if abs_ns and method_ns and abs_ns.isdisjoint(method_ns):
        para, sentence = abs_anchor
        evidence = [unit(para, sentence)]
        if method_anchor:
            evidence.append(unit(method_anchor[0], method_anchor[1]))
        findings.append(finding(
            "sample_size_inconsistency", "fact_consistency",
            unit(para, sentence), evidence,
            "major", "error", "needs_recheck", "medium",
            "摘要与方法段报告的样本量不一致（摘要 %s；方法 %s）。" %
            (sorted(abs_ns), sorted(method_ns)),
            "统一样本量口径，并分别说明入组数、随机化数与纳入分析数。",
            "已检查是否为入组/分析集口径差异；若是不同分析集，"
            "应在两处明确标注，不应视为错误。"))
    return findings


def build_coverage(parsed, findings, gaps, citation_count, assets):
    have = set(parsed["section_names"])
    facets = {name: [] for name in FACETS}
    for row in findings:
        facets[row["facet"]].append(row["rule_id"])
    evidence = {}
    for para in parsed["paragraphs"][:80]:
        evidence.setdefault(para["section"], para.get("locator"))

    def row(facet, status, items, reason=None):
        out = {"facet": facet, "status": status, "items": items,
               "evidence": [v for v in evidence.values() if v][:5]}
        if status != "checked":
            out["reason"] = reason or "材料不足"
        return out

    structure_ok = "abstract" in have and "results" in have
    rows = [
        row("fact_consistency", "checked" if structure_ok else "partial",
            ["abstract/results p values", "sample size", "figure references"],
            None if structure_ok else "缺少摘要或结果段，只能部分核对"),
        row("citation_support",
            "checked" if "references" in have and citation_count
            else ("partial" if "references" in have else "not_checked"),
            ["claim-source design match", "reference metadata"],
            None if ("references" in have and citation_count)
            else ("正文未找到可解析引用" if "references" in have
                  else "缺少参考文献段")),
        row("method_design", "checked" if "methods" in have else "not_checked",
            ["allocation", "sample size", "control"],
            None if "methods" in have else "缺少方法段"),
        row("statistical_reporting",
            "checked" if "results" in have else "not_checked",
            ["effect size", "confidence interval", "test statistic", "p value"],
            None if "results" in have else "缺少结果段"),
        row("argument_chain",
            "checked" if ("discussion" in have or "conclusion" in have)
            and "results" in have else "partial",
            ["causal language", "generalization", "durability"],
            None if (("discussion" in have or "conclusion" in have)
                     and "results" in have) else "缺少讨论/结论或结果段"),
        row("academic_compliance", "checked",
            ["ethics/consent", "data availability", "conflict of interest"], None),
    ]
    for item in rows:
        if item["facet"] in ("method_design", "statistical_reporting") \
                and item["status"] == "not_checked":
            for gap in gaps:
                if gap.get("facet") == item["facet"] and gap.get("status") == "not_checked":
                    item["reason"] = gap.get("reason")
                    break
    return rows


def review_status_suggestion(findings, gaps):
    if any(row.get("status") == "confirmed" for row in findings):
        return "issues"
    if findings or gaps:
        return "needs_recheck"
    return "pass"


def analyze_documents(docs, assets, case_id, synthetic=False):
    primary = choose_primary(docs)
    if primary is None:
        return {
            "case_id": case_id,
            "generated_by": "scripts/quality_review.py",
            "generated_at": now_iso(),
            "synthetic": bool(synthetic),
            "engine_status": "input_error",
            "review_status_suggestion": "not_checked",
            "documents": [{"path": d.get("path"), "kind": d.get("kind"),
                           "chars": len(d.get("text") or "")} for d in docs],
            "extraction": {"sections": {}, "paragraphs": [], "captions": []},
            "findings": [], "claim_source_matrix": [], "coverage": [],
            "structure_gaps": [], "not_checked": ["no readable material"],
            "uncertainties": [{
                "item": "materials", "reason": "no readable text material",
                "impact": "the review cannot start",
                "action": "provide a readable manuscript or extracted text",
            }],
            "stats": {"documents": len(docs), "findings": 0},
            "limits": ["没有可读材料，未生成任何问题或结论。"],
        }
    parsed = parse_document(primary)
    full_text = "\n\n".join((d.get("text") or "") for d in docs
                            if d.get("path") != "task.md")
    gaps, checks = rule_structure(parsed)
    findings = []
    findings.extend(rule_p_values(parsed))
    findings.extend(rule_randomization(parsed))
    sample_findings, sample_gaps = rule_sample_size(parsed, full_text)
    findings.extend(sample_findings)
    gaps.extend(sample_gaps)
    findings.extend(rule_effect_reporting(parsed, full_text))
    findings.extend(rule_argument(parsed))
    citation_findings, matrix, citation_gaps = rule_citations(parsed)
    findings.extend(citation_findings)
    gaps.extend(citation_gaps)
    figure_findings, figure_gaps = rule_figures(parsed, assets, full_text)
    findings.extend(figure_findings)
    gaps.extend(figure_gaps)
    findings.extend(rule_compliance(parsed, full_text))
    findings.extend(rule_number_consistency(parsed))
    findings = dedupe_findings(findings)
    if len(findings) > 40:
        findings = findings[:40]
        gaps.append({
            "item": "rule finding cap", "facet": "fact_consistency",
            "status": "not_checked",
            "reason": "规则候选超过 40 条，已截断",
            "impact": "可能存在未展示的候选",
            "action": "模型需人工通读并补充，不得把截断视为已覆盖",
        })
    citation_count = len(matrix)
    coverage = build_coverage(parsed, findings, gaps, citation_count, assets)
    not_checked = ["%s: %s" % (row["facet"], row.get("reason"))
                   for row in coverage
                   if row["status"] in ("not_checked", "partial") and row.get("reason")]
    uncertainties = list(gaps)
    if not docs:
        uncertainties.append({"item": "materials", "reason": "no input files",
                              "impact": "nothing to review",
                              "action": "provide material"})
    section_counts = {}
    for para in parsed["paragraphs"]:
        section_counts[para["section"]] = section_counts.get(para["section"], 0) + 1
    paragraphs = []
    for para in parsed["paragraphs"][:400]:
        paragraphs.append({
            "id": para["id"], "locator": para["locator"],
            "section": para["section"], "kind": para["kind"],
            "chars": len(para["text"]),
            "text": para["text"][:2000],
            "stats": paragraph_stats(para),
        })
    return {
        "case_id": case_id,
        "generated_by": "scripts/quality_review.py",
        "generated_at": now_iso(),
        "synthetic": bool(synthetic),
        "engine_status": ("partial" if ("methods" not in parsed["section_names"]
                                        or "results" not in parsed["section_names"])
                          else "ok"),
        "review_status_suggestion": review_status_suggestion(findings, gaps),
        "documents": [{"path": d.get("path"), "kind": d.get("kind"),
                       "chars": len(d.get("text") or "")} for d in docs],
        "extraction": {
            "sections": section_counts,
            "section_names": parsed["section_names"],
            "paragraphs": paragraphs,
            "captions": [{"locator": p["locator"], "text": p["text"][:1000]}
                         for p in parsed["captions"][:80]],
            "statistics": {
                "paragraphs_with_p_values": sum(
                    1 for p in parsed["paragraphs"] if paragraph_stats(p)["p_values"]),
                "paragraphs_with_effect_size": sum(
                    1 for p in parsed["paragraphs"] if paragraph_stats(p)["has_effect"]),
                "paragraphs_with_ci": sum(
                    1 for p in parsed["paragraphs"] if paragraph_stats(p)["has_ci"]),
                "citation_occurrences": citation_count,
            },
        },
        "findings": findings,
        "claim_source_matrix": matrix,
        "coverage": coverage,
        "structure_gaps": gaps,
        "not_checked": not_checked,
        "uncertainties": uncertainties,
        "stats": {
            "documents": len(docs), "paragraphs": len(parsed["paragraphs"]),
            "captions": len(parsed["captions"]), "findings": len(findings),
            "confirmed": sum(1 for f in findings if f["status"] == "confirmed"),
            "needs_recheck": sum(1 for f in findings
                                 if f["status"] == "needs_recheck"),
            "claim_source_rows": len(matrix),
            "assets": len(assets),
        },
        "limits": [
            "规则只产生候选，不替代语义阅读；每条 finding 必须由模型结合上下文确认。",
            "逐字引文来自解析后的文本；PDF 扫描件/图表无文字层时无法抽取。",
            "不联网，不核对未提供的文献全文或外部数据库。",
        ],
    }


# === MERGE ===


def merge_into_work(auto, work_path, min_confidence="medium"):
    """Merge candidates into the work file without overwriting model content."""
    work = load_json(work_path, {})
    if not isinstance(work, dict):
        work = {}
    work.setdefault("case_id", auto.get("case_id") or "CASE")
    work.setdefault("mode", "quality-review")
    work.setdefault("summary", "")
    work.setdefault("findings", [])
    work.setdefault("facets_checked", [])
    work.setdefault("claim_source_matrix", [])
    work.setdefault("evidence", [])
    work.setdefault("uncertainties", [])
    work.setdefault("not_checked", [])
    if not isinstance(work["findings"], list):
        work["findings"] = []
    if not isinstance(work["evidence"], list):
        work["evidence"] = []
    if not isinstance(work["facets_checked"], list):
        work["facets_checked"] = []
    if not isinstance(work["claim_source_matrix"], list):
        work["claim_source_matrix"] = []

    threshold = CONFIDENCE_RANK.get(min_confidence, 2)
    candidates = [row for row in auto.get("findings") or []
                  if CONFIDENCE_RANK.get(row.get("confidence"), 1) >= threshold]
    used_ids = {str(row.get("id")) for row in work["findings"]
                if isinstance(row, dict)}
    signatures = {(row.get("rule_id"), norm_space(row.get("original_quote"))[:160])
                  for row in work["findings"] if isinstance(row, dict)
                  and row.get("rule_id")}
    merged = []
    next_id = 1
    for row in candidates:
        signature = (row.get("rule_id"), norm_space(row.get("original_quote"))[:160])
        if signature in signatures:
            continue
        while "A%02d" % next_id in used_ids:
            next_id += 1
        new_row = dict(row)
        new_row["id"] = "A%02d" % next_id
        new_row["rule_status"] = row.get("rule_status") or row.get("status")
        # The script never confirms a review judgement; the model must do it.
        new_row["status"] = "needs_recheck"
        merged.append(new_row)
        used_ids.add(new_row["id"])
        signatures.add(signature)
        next_id += 1
    work["findings"].extend(merged)

    existing_quotes = {norm_space(item.get("quote"))
                       for item in work["evidence"] if isinstance(item, dict)}
    for row in merged:
        for item in row.get("evidence") or []:
            quote = norm_space(item.get("quote"))
            if quote and quote not in existing_quotes:
                work["evidence"].append(item)
                existing_quotes.add(quote)

    facet_rows = {}
    for row in work["facets_checked"]:
        if isinstance(row, dict) and row.get("facet"):
            facet_rows.setdefault(row.get("facet"), row)
    for row in auto.get("coverage") or []:
        facet = row.get("facet")
        if facet not in facet_rows:
            work["facets_checked"].append(dict(row))
            facet_rows[facet] = row
        else:
            target = facet_rows[facet]
            if not target.get("items"):
                target["items"] = list(row.get("items") or [])
            if target.get("status") in ("", None):
                target["status"] = row.get("status")
            if row.get("status") in ("partial", "not_checked", "not_applicable") \
                    and not target.get("reason"):
                target["reason"] = row.get("reason")

    matrix_keys = {(norm_space(row.get("claim")), norm_space(row.get("source")))
                   for row in work["claim_source_matrix"] if isinstance(row, dict)}
    for row in auto.get("claim_source_matrix") or []:
        key = (norm_space(row.get("claim")), norm_space(row.get("source")))
        if key not in matrix_keys:
            work["claim_source_matrix"].append(dict(row))
            matrix_keys.add(key)

    uncertainty_items = {str(item.get("item") or item)
                         for item in work["uncertainties"] if isinstance(item, dict)}
    for gap in auto.get("structure_gaps") or []:
        item = str(gap.get("item") or "")
        if item and item not in uncertainty_items:
            work["uncertainties"].append({
                "item": item,
                "reason": gap.get("reason") or "",
                "impact": gap.get("impact") or "",
                "action": gap.get("action") or "",
            })
            uncertainty_items.add(item)
    existing_not_checked = {str(item) for item in work["not_checked"]}
    for item in auto.get("not_checked") or []:
        if str(item) not in existing_not_checked:
            work["not_checked"].append(str(item))
            existing_not_checked.add(str(item))

    statuses = [row.get("status") for row in work["findings"]
                if isinstance(row, dict)]
    if "confirmed" in statuses:
        work["review_status"] = "issues"
    elif work.get("review_status") not in ("issues",):
        work["review_status"] = "needs_recheck"
    if not work.get("summary"):
        work["summary"] = (
            "规则扫描产生 %d 条候选问题（采纳 %d 条，全部标记为 needs_recheck，"
            "等待模型逐条核对）；规则只做抽取与候选，不替代审稿判断。"
            % (len(auto.get("findings") or []), len(merged)))
    work["coverage_summary"] = {
        "facets_total": len(FACETS),
        "facets_checked": sum(1 for row in work["facets_checked"]
                              if isinstance(row, dict)
                              and row.get("status") == "checked"),
        "findings": len(work["findings"]),
        "confirmed": sum(1 for row in work["findings"]
                         if isinstance(row, dict) and row.get("status") == "confirmed"),
        "needs_recheck": sum(1 for row in work["findings"]
                             if isinstance(row, dict)
                             and row.get("status") == "needs_recheck"),
        "evidence": len(work["evidence"]),
    }
    work["rule_review"] = {
        "script": "scripts/quality_review.py",
        "generated_at": auto.get("generated_at"),
        "engine_status": auto.get("engine_status"),
        "candidates": len(auto.get("findings") or []),
        "merged": len(merged),
        "min_confidence": min_confidence,
        "rule_file": "work/rule-review.json",
        "note": "merged rows use status=needs_recheck; confirm, edit or delete them",
    }
    write_json(work_path, work)
    return {"work": str(work_path), "merged": len(merged),
            "findings": len(work["findings"])}


def analyze_paths(input_dir, work_dir):
    case = load_json(Path(input_dir) / "case.json", {})
    docs, assets = load_documents(input_dir, work_dir)
    work = load_json(Path(work_dir) / "review-findings.json", {})
    case_id = (case.get("case_id") if isinstance(case, dict) else None) \
        or (work.get("case_id") if isinstance(work, dict) else None) or "CASE"
    return analyze_documents(docs, assets, str(case_id),
                             bool(isinstance(case, dict) and case.get("synthetic")))


# === SELFTEST ===


NORMAL_SYNTHETIC = """# SYNTHETIC manuscript (not a real paper)

## Abstract
P1. We randomized 40 students and found a significant improvement (p=0.04).

## 1 Introduction
P2. Prior work suggests mindfulness training improves attention [1].

## 2 Methods
P3. Participants were 40 students assigned alternately to training or waitlist.
P4. We did not perform an a priori sample-size calculation.

## 3 Results
P5. The training group improved by 9.1 points and the waitlist group by 2.2 points (p=0.08).
P6. Figure 1 (not provided in the input) shows the schedule.

## 4 Discussion
P7. Mindfulness training causes durable improvements in attention.
P8. The effect should generalize to all young adults.

## References
P9. [1] A cross-sectional survey of meditation and attention; no intervention was tested.
"""

MISSING_SECTIONS_SYNTHETIC = """# SYNTHETIC partial manuscript (not a real paper)

## Abstract
P1. We randomized 20 students and found an improvement (p=0.05).

## References
P2. [1] A survey of attention.
"""


def validate_finding(row, texts):
    problems = []
    for field in ("rule_id", "facet", "locator", "original_quote", "severity",
                  "type", "status", "impact", "fix", "false_positive_check",
                  "evidence"):
        if not row.get(field):
            problems.append("missing field %s" % field)
    if row.get("facet") not in FACETS:
        problems.append("bad facet %r" % row.get("facet"))
    if row.get("severity") not in ("critical", "major", "minor", "info"):
        problems.append("bad severity %r" % row.get("severity"))
    if row.get("type") not in ("error", "insufficient_evidence",
                               "norm_violation", "suggestion"):
        problems.append("bad type %r" % row.get("type"))
    if row.get("status") not in ("confirmed", "needs_recheck"):
        problems.append("bad status %r" % row.get("status"))
    locator = row.get("locator") or {}
    path = locator.get("path") if isinstance(locator, dict) else ""
    source = texts.get(path)
    if source is None:
        problems.append("locator path not in material: %r" % path)
    elif not quote_in(source, row.get("original_quote")):
        problems.append("original_quote not verbatim")
    for item in row.get("evidence") or []:
        source = texts.get(item.get("path"))
        if source is None or not quote_in(source, item.get("quote")):
            problems.append("evidence quote not verbatim")
    return problems


def run_selftest():
    results = []
    with tempfile.TemporaryDirectory(prefix="quality-review-selftest-") as tmp:
        base = Path(tmp)
        cases = [
            ("normal", NORMAL_SYNTHETIC,
             {"p_value_mismatch", "randomization_conflict",
              "sample_size_not_reported", "effect_size_missing",
              "citation_claim_mismatch", "figure_not_provided",
              "compliance_declarations_missing", "generalization_overreach",
              "causal_overclaim"}, "ok"),
            ("empty", "", set(), "input_error"),
            ("missing_sections", MISSING_SECTIONS_SYNTHETIC, set(), "partial"),
        ]
        for name, text, expected_rules, expected_status in cases:
            case_dir = base / name
            (case_dir / "work").mkdir(parents=True)
            (case_dir / "input").mkdir(parents=True)
            (case_dir / "input" / "case.json").write_text(json.dumps({
                "case_id": "SYNTH-%s" % name.upper(), "synthetic": True,
            }), encoding="utf-8")
            if text:
                (case_dir / "input" / "manuscript.md").write_text(
                    text, encoding="utf-8")
            auto = analyze_paths(case_dir / "input", case_dir / "work")
            problems = []
            if auto.get("engine_status") != expected_status:
                problems.append("engine_status %r != %r" % (
                    auto.get("engine_status"), expected_status))
            texts = {"manuscript.md": text} if text else {}
            for row in auto.get("findings") or []:
                problems.extend(validate_finding(row, texts))
            rules = {row.get("rule_id") for row in auto.get("findings") or []}
            missing = expected_rules - rules
            if missing:
                problems.append("expected rules missing: %s" % sorted(missing))
            if name == "empty" and (auto.get("findings") or []):
                problems.append("empty input produced findings")
            if name == "missing_sections":
                gap_items = {gap.get("item") for gap in auto.get("structure_gaps") or []}
                for wanted in ("methods section", "results section"):
                    if wanted not in gap_items:
                        problems.append("missing structure gap: %s" % wanted)
                coverage = {row.get("facet"): row.get("status")
                            for row in auto.get("coverage") or []}
                for facet in ("method_design", "statistical_reporting"):
                    if coverage.get(facet) != "not_checked":
                        problems.append("%s should be not_checked" % facet)
            results.append({
                "case": name, "status": "pass" if not problems else "fail",
                "engine_status": auto.get("engine_status"),
                "findings": len(auto.get("findings") or []),
                "rules": sorted(rules), "problems": problems,
            })
    passed = all(row["status"] == "pass" for row in results)
    payload = {"selftest": "pass" if passed else "fail",
               "synthetic_only": True, "cases": results}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if passed else 1


# === MAIN ===


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="rule-based candidate findings for paper quality review")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--out", default=None,
                        help="additional path for the analysis JSON")
    parser.add_argument("--merge", action="store_true",
                        help="merge candidates into work/review-findings.json "
                             "with status=needs_recheck")
    parser.add_argument("--min-confidence", default="medium",
                        choices=("low", "medium", "high"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return run_selftest()

    work_dir = Path(args.work)
    output_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        auto = analyze_paths(args.input, work_dir)
    except Exception as exc:  # noqa: BLE001 - never crash the pipeline
        auto = {
            "case_id": "CASE", "generated_by": "scripts/quality_review.py",
            "generated_at": now_iso(), "synthetic": False,
            "engine_status": "error", "review_status_suggestion": "not_checked",
            "documents": [], "extraction": {}, "findings": [],
            "claim_source_matrix": [], "coverage": [], "structure_gaps": [],
            "not_checked": ["rule engine crashed"],
            "uncertainties": [{
                "item": "rule_engine", "reason": "%s: %s" % (
                    type(exc).__name__, exc),
                "impact": "no rule candidates were produced",
                "action": "inspect the traceback in work/rule-review.json; "
                          "continue with manual review",
            }],
            "stats": {"findings": 0},
            "limits": ["规则引擎异常，未生成候选；不得据此声称已检查。"],
            "traceback": traceback.format_exc()[-4000:],
        }
    auto["status"] = auto.get("engine_status")
    rule_work = work_dir / "rule-review.json"
    write_json(rule_work, auto)
    write_json(output_dir / "rule-review.json", auto)
    extra = None
    if args.out:
        extra_path = Path(args.out)
        if not extra_path.is_absolute():
            extra_path = work_dir / extra_path
        if extra_path.resolve() not in (rule_work.resolve(),
                                        (output_dir / "rule-review.json").resolve()):
            write_json(extra_path, auto)
            extra = str(extra_path)
    merge_result = None
    if args.merge and auto.get("engine_status") != "input_error":
        merge_result = merge_into_work(
            auto, work_dir / "review-findings.json", args.min_confidence)
    summary = {
        "script": "quality_review.py",
        "case_id": auto.get("case_id"),
        "engine_status": auto.get("engine_status"),
        "findings": len(auto.get("findings") or []),
        "confirmed_candidates": sum(
            1 for row in auto.get("findings") or []
            if row.get("status") == "confirmed"),
        "needs_recheck": sum(
            1 for row in auto.get("findings") or []
            if row.get("status") == "needs_recheck"),
        "claim_source_rows": len(auto.get("claim_source_matrix") or []),
        "structure_gaps": len(auto.get("structure_gaps") or []),
        "rule_file": str(rule_work),
        "merge": merge_result,
        "extra_out": extra,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



