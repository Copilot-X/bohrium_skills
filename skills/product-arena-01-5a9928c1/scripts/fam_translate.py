#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reading 03 — source-bound academic translation with segment alignment.

The module keeps the model responsible for the actual translation.  It does
the deterministic work around it:

* choose the real source document instead of a glossary/template file;
* parse ``## Pxxx`` bilingual blocks with or without heading suffixes, and
  accumulate multi-line Source/Target values;
* align by explicit IDs first, then by exact block count; it never silently
  shifts paragraphs when the counts disagree;
* audit numbers, citations, figure/table/equation identifiers, negation and
  hedging in a direction-aware way;
* require a locked glossary before a run can be called ``answered``.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import skillcore as S

AUXILIARY_NAME_RE = re.compile(
    r"(glossary|terminolog|translation|bilingual|style|instruction|readme|"
    r"template|schema|metadata|license|manifest|reference|bibliograph|"
    r"refs|supplement|appendix)", re.I)
SKIP_PARTS = {"output", "work", ".ocr", "__pycache__", ".git", "build"}
SKIP_KINDS = {"bib", "ris"}

_LANG_ALIASES = {
    "zh": "zh", "zho": "zh", "chi": "zh", "chinese": "zh", "中文": "zh",
    "汉语": "zh", "简体中文": "zh", "zh-cn": "zh", "zh_cn": "zh",
    "en": "en", "eng": "en", "english": "en", "英文": "en", "英语": "en",
    "en-us": "en", "en_us": "en", "en-gb": "en",
}

_EN_HEDGE = re.compile(
    r"\b(?:may|might|could|suggest(?:s|ed)?|indicat(?:e|es|ed)|appear(?:s|ed)?|"
    r"likely|possibly|potentially|associated with|correlat(?:e|es|ed) with|"
    r"consistent with|tend(?:s|ed)? to|can|perhaps|probabl[ey]|"
    r"propos(?:e|es|ed)|estimat(?:e|es|ed))\b", re.I)
_EN_STRONG = re.compile(
    r"\b(?:prov(?:e|es|ed|en)|demonstrat(?:e|es|ed)|"
    r"establish(?:es|ed)?|confirm(?:s|ed)?|caus(?:e|es|ed)|driv(?:e|es|en)|"
    r"lead(?:s|ing)? to|must|always|never|clearly|definitively)\b", re.I)
_ZH_HEDGE_RE = re.compile(
    r"可能|或许|也许|提示|暗示|倾向于|相关|关联|推测|估计|大致|有望")
_ZH_STRONG_RE = re.compile(
    r"证明|证实|确定|导致|引起|必然|总是|从不|完全|毫无疑问")
_EN_NEG = re.compile(
    r"\b(?:not|no|none|never|without|neither|nor|cannot|can't|didn't|"
    r"doesn't|isn't|wasn't|weren't|fail(?:ed|s)?|absence|lack(?:s|ed)?)\b",
    re.I)
_ZH_NEG = re.compile(
    r"并非|不是|没有|未能|无法|不能|缺乏|缺失|未(?!来)|"
    r"不(?!同|仅|但|过|断|稳定|确定|足|少)|"
    r"无(?:显著|明显|法|关|效|需|论)")

_FIGURE_EN = re.compile(
    r"\b(Fig(?:ure)?|Tab(?:le)?|Eq(?:uation)?|Section|Sec\.?|Scheme)"
    r"\s*\.?\s*([A-Za-z]?\d+[A-Za-z]?(?:\s*\([a-z0-9]+\))?)", re.I)
_FIGURE_ZH = re.compile(
    r"(图|表|式)\s*([0-9一二三四五六七八九十]+[A-Za-z]?"
    r"(?:\s*\([a-z0-9]+\))?)")
_FIGURE_KIND = {
    "fig": "figure", "figure": "figure", "tab": "table", "table": "table",
    "eq": "equation", "equation": "equation", "section": "section",
    "sec": "section", "scheme": "scheme", "图": "figure", "表": "table",
    "式": "equation",
}
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
              "七": 7, "八": 8, "九": 9, "十": 10}

_HEADING_RE = re.compile(r"^#{2,4}\s+(P\d+)\b[^\n]*$", re.I)
_MARKER_RE = re.compile(
    r"^[-*]?\s*(?:>\s*)?\*\*\s*(Source|Target)\b[^*]*\*\*\s*[:：]?\s*(.*)$",
    re.I)


def _norm_lang(value):
    if value is None:
        return ""
    text = str(value).strip().lower()
    if text in _LANG_ALIASES:
        return _LANG_ALIASES[text]
    if "中" in text or "zh" in text:
        return "zh"
    if "英" in text or "english" in text:
        return "en"
    return text


def _cjk_ratio(text):
    cjk = S.cjk_count(text)
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return cjk / float(max(cjk + latin, 1))


def _direction(ctx, text):
    case = ctx.case if isinstance(ctx.case, dict) else {}
    explicit = (case.get("direction") or case.get("translation_direction")
                or case.get("language_direction") or "")
    if isinstance(explicit, dict):
        explicit = "%s->%s" % (explicit.get("source", ""),
                               explicit.get("target", ""))
    if explicit:
        match = re.search(r"([A-Za-z一-鿿_-]+)\s*(?:->|→|to|2)\s*"
                          r"([A-Za-z一-鿿_-]+)", str(explicit), re.I)
        if match:
            source, target = _norm_lang(match.group(1)), _norm_lang(match.group(2))
            if source and target and source != target:
                return "%s->%s" % (source, target), source, target

    source = _norm_lang(case.get("source_language") or case.get("source_lang")
                        or case.get("sourceLanguage"))
    target = _norm_lang(case.get("target_language") or case.get("target_lang")
                        or case.get("targetLanguage"))
    if source and target and source != target:
        return "%s->%s" % (source, target), source, target

    task = (ctx.task or "").lower()
    if re.search(r"中译英|汉译英|译成英|译為英|translate[^.\n]{0,80}into english|"
                 r"\bzh\s*(?:->|→|to|2)\s*en\b", task):
        return "zh->en", "zh", "en"
    if re.search(r"英译中|英译汉|译成中|translate[^.\n]{0,80}into chinese|"
                 r"\ben\s*(?:->|→|to|2)\s*zh\b", task):
        return "en->zh", "en", "zh"
    if target in ("zh", "en"):
        inferred = "zh" if _cjk_ratio(text) >= 0.15 else "en"
        if inferred != target:
            return "%s->%s" % (inferred, target), inferred, target
        source = "en" if target == "zh" else "zh"
        return "%s->%s" % (source, target), source, target
    if _cjk_ratio(text) >= 0.15:
        return "zh->en", "zh", "en"
    return "en->zh", "en", "zh"


def _largest_source_text(ctx):
    """Longest non-auxiliary document, used only to infer the direction."""
    best = ""
    for doc in ctx.docs:
        text = S.doc_text(doc)
        if not text.strip() or doc.get("kind") in SKIP_KINDS:
            continue
        rel = str(doc.get("rel_path") or "")
        name = Path(rel).name.lower()
        parts = set(Path(rel).parts)
        if AUXILIARY_NAME_RE.search(name) or (parts & SKIP_PARTS):
            continue
        if len(text) > len(best):
            best = text
    return best


def _source_doc(ctx, source_lang):
    best = None
    best_key = None
    for doc in ctx.docs:
        text = S.doc_text(doc)
        if not text.strip() or doc.get("kind") in SKIP_KINDS:
            continue
        rel = str(doc.get("rel_path") or "")
        name = Path(rel).name.lower()
        parts = set(Path(rel).parts)
        auxiliary = bool(AUXILIARY_NAME_RE.search(name)) or bool(parts & SKIP_PARTS)
        ratio = _cjk_ratio(text)
        if source_lang == "zh":
            mismatch = 0 if ratio >= 0.15 else 2
        elif source_lang == "en":
            mismatch = 0 if ratio < 0.30 else 2
        else:
            mismatch = 0
        kind_bonus = 0 if doc.get("kind") in ("pdf", "tex", "md", "text",
                                              "docx") else 1
        key = (1 if auxiliary else 0, mismatch, kind_bonus, -len(text), rel)
        if best_key is None or key < best_key:
            best_key, best = key, doc
    return best


def _segment_source(text, text_doc):
    segments = S.segment_blocks(text,
                                source_id=text_doc.get("source_id", "SRC01"),
                                path=text_doc.get("rel_path", ""))
    for segment in segments:
        sentences = S.split_sentences(segment["text"])
        for sentence in sentences:
            sentence["sentence_id"] = "%s-%s" % (segment["id"], sentence["id"])
            sentence["locator"] = "%s %s" % (segment["locator"], sentence["id"])
        segment["sentences"] = sentences
    return segments


def prepare(ctx):
    direction, source_lang, target_lang = _direction(
        ctx, _largest_source_text(ctx))
    text_doc = _source_doc(ctx, source_lang)
    if text_doc is None:
        ctx.result.change("prepare", "input", "no readable source material")
        ctx.result.uncertainty("source", "no readable material under /input",
                               "translation cannot start",
                               "provide the source file")
        return ctx.result.finalize("input_error")
    text = S.doc_text(text_doc)
    direction, source_lang, target_lang = _direction(ctx, text)
    # Re-select with the direction inferred from the actual source text.
    better = _source_doc(ctx, source_lang)
    if better is not None and better is not text_doc:
        text_doc = better
        text = S.doc_text(text_doc)
        direction, source_lang, target_lang = _direction(ctx, text)
    segments = _segment_source(text, text_doc)
    payload = {
        "case_id": ctx.case_id,
        "direction": direction,
        "source_language": source_lang,
        "target_language": target_lang,
        "source_path": text_doc.get("rel_path"),
        "source_kind": text_doc.get("kind"),
        "source_sha256": S.sha256_text(text),
        "source_chars": len(text),
        "segment_count": len(segments),
        "segments": segments,
        "glossary_candidates": S.term_candidates(text, 60),
        "instructions": [
            "Translate every segment in order; do not merge, split or reorder segments.",
            "Preserve numbers, units, citations, figure/table/equation references and formulas.",
            "Write /app/output/translation.md first, then /app/output/bilingual.md with the same P IDs.",
            "Lock terminology in /app/work/glossary.json and use the same target term everywhere.",
            "Record uncertain wording in /app/work/translation-notes.json instead of guessing silently.",
        ],
    }
    S.write_json(ctx.work / "segments.json", payload)
    S.write_json(ctx.work / "glossary-candidates.json",
                 payload["glossary_candidates"])
    S.write_json(ctx.work / "translation-brief.json", {
        "case_id": ctx.case_id, "direction": direction,
        "source_language": source_lang, "target_language": target_lang,
        "output_contract": ctx.contract,
        "source_path": text_doc.get("rel_path"),
        "source_kind": text_doc.get("kind"),
        "segment_count": len(segments),
    })
    S.write_json(ctx.work / "translation-notes-template.json", {
        "case_id": ctx.case_id,
        "flags": [{
            "segment_id": "P001",
            "issue": "ambiguous term or grammar",
            "source_span": "verbatim source fragment",
            "resolution": "chosen translation and why",
            "needs_human_review": True,
            "source": {"path": text_doc.get("rel_path"), "locator": "P001"},
        }],
    })
    lines = ["# Bilingual alignment", "",
             "> Fill every Target line. Keep the P IDs and the Source text unchanged.",
             ""]
    for segment in segments:
        lines.append("## %s" % segment["id"])
        lines.append("")
        lines.append("- **Source (%s):** %s" % (
            source_lang, segment["text"].replace("\n", " ")))
        lines.append("- **Target (%s):** " % target_lang)
        lines.append("")
    S.write_text(ctx.work / "bilingual-template.md", "\n".join(lines))
    # A source-only bilingual file is a safe partial deliverable.
    S.write_text(ctx.output / "bilingual-partial.md", "\n".join(lines))
    ctx.result.add_artifact(ctx.output / "bilingual-partial.md",
                            "partial_alignment", "md",
                            "source segments with empty target slots")
    ctx.result.change("segment", text_doc.get("rel_path", ""),
                      "%d segments prepared (%s)" % (len(segments), direction))
    if len(segments) == 0:
        ctx.result.uncertainty("segments", "source produced zero segments",
                               "nothing to translate",
                               "check the source encoding or provide plain text")
        return ctx.result.finalize("input_error")
    return ctx.provisional(
        reason="source segmented; write translation.md and bilingual.md, then "
               "run finalize")


def _clean_value(value):
    """Normalise horizontal space but keep line breaks (tables stay tables)."""
    lines = [re.sub(r"[ \t]+", " ", line).strip()
             for line in (value or "").replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _parse_bilingual(text):
    """Parse ``## Pxxx`` blocks with Source/Target markers.

    Accepts heading suffixes (``## P004 摘要``), both ``**Source (zh):**`` and
    ``**Source (zh)**:``, and multi-line values continued on later lines.
    """
    entries = {}
    order = []
    current = None
    field = None
    values = {}

    def flush():
        if current is None:
            return
        entries[current] = {
            "source": _clean_value(values.get("source", "")),
            "target": _clean_value(values.get("target", "")),
        }

    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            current = heading.group(1).upper()
            if current not in order:
                order.append(current)
            values = {}
            field = None
            continue
        if current is None:
            continue
        marker = _MARKER_RE.match(line)
        if marker:
            field = marker.group(1).lower()
            values[field] = marker.group(2).strip()
            continue
        if field and line and not line.startswith("#"):
            values[field] = (values.get(field, "") + " " + line).strip()
        elif line.startswith("#"):
            flush()
            current = None
            field = None
            values = {}
    flush()
    return entries


def _parse_heading_sections(text):
    """Parse a translation written as ``## Pxxx`` followed by its text."""
    entries = {}
    current = None
    buffer = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        heading = _HEADING_RE.match(line.strip())
        if heading:
            if current is not None:
                entries[current] = _clean_value("\n".join(buffer))
            current = heading.group(1).upper()
            buffer = []
            continue
        if current is not None:
            buffer.append(line)
    if current is not None:
        entries[current] = _clean_value("\n".join(buffer))
    return {key: {"source": "", "target": value}
            for key, value in entries.items() if value}


def _parse_translation_blocks(text):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text)
              if block.strip()]
    if blocks and re.match(r"^---\s*$", blocks[0]):
        # Drop YAML frontmatter.
        for index, block in enumerate(blocks[1:], 1):
            if re.match(r"^---\s*$", block):
                blocks = blocks[index + 1:]
                break
    return blocks


def _align(segments, translation_text, bilingual):
    """Return (aligned, method, issues).

    IDs always win.  Plain blocks are zipped only when the count matches
    exactly; a one-block title offset is accepted and recorded.  No other
    sequential approximation is performed, because misaligned paragraphs are
    worse than an honest ``partial`` result.
    """
    aligned = {}
    methods = []
    issues = []
    if bilingual:
        for segment in segments:
            entry = bilingual.get(segment["id"])
            if entry and entry.get("target"):
                aligned[segment["id"]] = entry["target"]
        if aligned:
            methods.append("bilingual:Pxxx")

    translation_entries = _parse_bilingual(translation_text)
    if translation_entries:
        added = 0
        for segment in segments:
            if segment["id"] in aligned:
                continue
            entry = translation_entries.get(segment["id"])
            if entry and entry.get("target"):
                aligned[segment["id"]] = entry["target"]
                added += 1
        if added:
            methods.append("translation:Pxxx")
    else:
        heading_entries = _parse_heading_sections(translation_text)
        added = 0
        for segment in segments:
            if segment["id"] in aligned:
                continue
            entry = heading_entries.get(segment["id"])
            if entry and entry.get("target"):
                aligned[segment["id"]] = entry["target"]
                added += 1
        if added:
            methods.append("translation:heading:Pxxx")

    missing = [s for s in segments if not aligned.get(s["id"])]
    if missing:
        blocks = _parse_translation_blocks(translation_text)
        variants = [(blocks, "translation:blocks")]
        if blocks and blocks[0].startswith("#") and len(blocks) - 1 == len(segments):
            variants.append((blocks[1:], "translation:blocks+title_offset"))
        matched = False
        for candidate, method in variants:
            if len(candidate) == len(segments):
                for segment, block in zip(segments, candidate):
                    aligned.setdefault(segment["id"], block)
                methods.append(method)
                matched = True
                break
        if not matched and blocks:
            issues.append("translation.md blocks=%d, segments=%d" % (
                len(blocks), len(segments)))
    return aligned, methods, issues


def _load_glossary(ctx):
    for name in ("glossary.json", "glossary-locked.json", "terms.json"):
        payload = S.load_json(ctx.work / name, None)
        if not payload:
            continue
        items = None
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            for key in ("terms", "glossary", "entries"):
                value = payload.get(key)
                if isinstance(value, list):
                    items = value
                    break
                if isinstance(value, dict):
                    items = [{"source": k, "target": v} for k, v in value.items()]
                    break
            if items is None:
                meta_keys = {"locked", "source", "source_language",
                             "target_language", "case_id", "note", "issues",
                             "direction"}
                items = [{"source": k, "target": v} for k, v in payload.items()
                         if k not in meta_keys and isinstance(v, (str, dict))]
        cleaned = []
        for item in items or []:
            if isinstance(item, str):
                continue
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or item.get("src") or item.get("zh")
                         or item.get("term") or item.get("source_term") or "")
            target = str(item.get("target") or item.get("tgt") or item.get("en")
                         or item.get("translation") or item.get("target_term")
                         or "")
            if isinstance(item.get("target"), dict):
                target = str(item["target"].get("term")
                             or item["target"].get("text") or "")
            aliases = item.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            if source or target:
                cleaned.append({
                    "source": source.strip(), "target": target.strip(),
                    "aliases": [str(a).strip() for a in aliases if str(a).strip()],
                    "note": str(item.get("note") or ""),
                })
        if cleaned:
            return cleaned, name
    return [], None


def _term_present(term, text):
    term = _clean_value(term)
    if not term:
        return False
    if re.search(r"[一-鿿]", term):
        return term in text
    pattern = re.escape(term).replace(r"\ ", r"\s+")
    return bool(re.search(r"(?<![A-Za-z])%s(?![A-Za-z])" % pattern, text, re.I))


def _term_issues(glossary, text):
    problems = []
    for entry in glossary or []:
        target = _clean_value(entry.get("target"))
        if not target:
            continue
        accepted = [target] + list(entry.get("aliases") or [])
        if not any(_term_present(candidate, text) for candidate in accepted):
            problems.append("glossary target not found: %s -> %s" % (
                entry.get("source"), target))
    return problems


def _citation_signature(token):
    kind = token.get("kind")
    if kind == "bracket":
        return "bracket:" + ",".join(str(n) for n in token.get("numbers") or [])
    if kind == "author-year":
        value = token.get("raw") or ""
        value = value.lower().replace("，", ",").replace("；", ";")
        value = re.sub(r"\s+", " ", value)
        value = re.sub(r"\s*([,;])\s*", r"\1", value)
        value = re.sub(r",?\s*((?:19|20)\d{2})", r",\1", value)
        return "author-year:" + value
    if kind == "tex":
        return "tex:" + ",".join(token.get("keys") or [])
    if kind == "pandoc":
        return "pandoc:" + ";".join(token.get("keys") or [])
    return "raw:" + (token.get("raw") or "")


def _citation_counter(text):
    return Counter(_citation_signature(token)
                   for token in S.citation_tokens(text))


def _check_citations(source, target):
    src = _citation_counter(source)
    tgt = _citation_counter(target)
    missing = [{"token": token, "missing_count": count}
               for token, count in (src - tgt).items()]
    extra = [{"token": token, "extra_count": count}
             for token, count in (tgt - src).items()]
    return {"ok": not missing, "missing": missing, "extra": extra}


def _negation_count(text):
    return len(_EN_NEG.findall(text or "")) + len(_ZH_NEG.findall(text or ""))


def _hedge_profile(text, language):
    if language == "zh":
        return {"hedging": len(_ZH_HEDGE_RE.findall(text or "")),
                "strong": len(_ZH_STRONG_RE.findall(text or "")),
                "language": "zh"}
    return {"hedging": len(_EN_HEDGE.findall(text or "")),
            "strong": len(_EN_STRONG.findall(text or "")),
            "language": "en"}


def _cn_number(value):
    value = value.strip()
    if value.isdigit():
        return value
    if value == "十":
        return "10"
    if len(value) == 1 and value in _CN_DIGITS:
        return str(_CN_DIGITS[value])
    if value.startswith("十") and len(value) == 2 and value[1] in _CN_DIGITS:
        return str(10 + _CN_DIGITS[value[1]])
    if len(value) == 2 and value[0] in _CN_DIGITS and value[1] == "十":
        return str(_CN_DIGITS[value[0]] * 10)
    if len(value) == 3 and value[0] in _CN_DIGITS and value[1] == "十" \
            and value[2] in _CN_DIGITS:
        return str(_CN_DIGITS[value[0]] * 10 + _CN_DIGITS[value[2]])
    return value


def _figure_signatures(text):
    signatures = []
    for match in _FIGURE_EN.finditer(text or ""):
        kind = _FIGURE_KIND.get(match.group(1).lower().rstrip("."))
        number = match.group(2).replace(" ", "")
        if kind:
            signatures.append("%s:%s" % (kind, number))
    for match in _FIGURE_ZH.finditer(text or ""):
        kind = _FIGURE_KIND.get(match.group(1))
        number = re.sub(r"\s+", "", match.group(2))
        number = re.sub(r"[一二三四五六七八九十]+",
                        lambda m: _cn_number(m.group(0)), number, count=1)
        if kind:
            signatures.append("%s:%s" % (kind, number))
    return signatures


def _check_figures(source, target):
    src = Counter(_figure_signatures(source))
    tgt = Counter(_figure_signatures(target))
    missing = [{"token": token, "missing_count": count}
               for token, count in (src - tgt).items()]
    extra = [{"token": token, "extra_count": count}
             for token, count in (tgt - src).items()]
    return {"ok": not missing, "missing": missing, "extra": extra}


def _locator_for(segments, needle, fallback):
    needle = _clean_value(needle)
    if needle:
        for segment in segments:
            if needle in segment.get("text", ""):
                return segment.get("locator") or fallback
    return fallback


def _preserved_facts(segments, source_text, target_text, glossary, source_path):
    preserved = []
    target_numbers = []
    for item in S.number_values(target_text):
        target_numbers.append((round(item["number"], 6), item["raw"]))
    for token in S.number_tokens(source_text):
        match = re.match(r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?",
                         token["raw"])
        if not match:
            continue
        try:
            value = round(float(match.group(0).replace(",", "")), 6)
        except ValueError:
            continue
        for index, (candidate, raw) in enumerate(target_numbers):
            if candidate != value:
                continue
            target_numbers.pop(index)
            preserved.append({
                "kind": "number", "source": token["raw"], "target": raw,
                "locator": _locator_for(segments, token["raw"], source_path),
                "value": token["raw"], "unit": "",
            })
            break

    target_cites = []
    for token in S.citation_tokens(target_text):
        target_cites.append((_citation_signature(token), token["raw"]))
    for token in S.citation_tokens(source_text):
        signature = _citation_signature(token)
        for index, (candidate, raw) in enumerate(target_cites):
            if candidate != signature:
                continue
            target_cites.pop(index)
            preserved.append({
                "kind": "citation", "source": token["raw"], "target": raw,
                "locator": _locator_for(segments, token["raw"], source_path),
                "value": signature, "unit": "",
            })
            break

    target_figures = Counter(_figure_signatures(target_text))
    for signature in _figure_signatures(source_text):
        if target_figures[signature] <= 0:
            continue
        target_figures[signature] -= 1
        preserved.append({
            "kind": "figure_ref", "source": signature, "target": signature,
            "locator": source_path, "value": signature, "unit": "",
        })

    for entry in glossary or []:
        target = _clean_value(entry.get("target"))
        if not target:
            continue
        preserved.append({
            "kind": "term", "source": entry.get("source"), "target": target,
            "locator": _locator_for(segments, entry.get("source"), source_path),
            "value": target, "unit": "",
        })
    return preserved[:300]


def finalize(ctx):
    payload = S.load_json(ctx.work / "segments.json", {})
    segments = payload.get("segments") or []
    direction = payload.get("direction") or "zh->en"
    source_lang = payload.get("source_language") or "zh"
    target_lang = payload.get("target_language") or "en"
    source_path = payload.get("source_path") or ""
    if not segments:
        ctx.result.change("finalize", "input", "segments.json missing or empty")
        ctx.result.uncertainty("segments", "no prepared segments",
                               "translation cannot be aligned",
                               "run prepare first")
        return ctx.result.finalize("input_error")

    translation_path = ctx.output / "translation.md"
    bilingual_path = ctx.output / "bilingual.md"
    translation = S.read_text(translation_path)
    bilingual_text = S.read_text(bilingual_path)
    if not translation.strip() and not bilingual_text.strip():
        ctx.result.change("finalize", "output",
                          "no translation.md or bilingual.md")
        ctx.result.uncertainty("deliverable",
                               "model-authored translation is missing",
                               "cannot deliver a translation",
                               "write translation.md and bilingual.md")
        return ctx.result.finalize("partial")

    bilingual = _parse_bilingual(bilingual_text)
    aligned, methods, alignment_issues = _align(segments, translation, bilingual)
    glossary, glossary_source = _load_glossary(ctx)
    locked = bool(glossary_source) and any(
        _clean_value(item.get("target")) for item in glossary)
    if not glossary:
        glossary = [{"source": item.get("term", ""), "target": "",
                     "aliases": [], "note": "candidate; target not locked"}
                    for item in S.load_json(
                        ctx.work / "glossary-candidates.json", [])[:20]]
    if not locked:
        glossary = [
            {"source": item.get("source") or item.get("term", ""),
             "target": item.get("target") or "",
             "aliases": item.get("aliases") or [],
             "note": item.get("note") or "candidate; target not locked"}
            for item in glossary
        ]

    bilingual_lines = [
        "# Bilingual alignment", "",
        "> Paragraph-level source/target alignment. Empty Target means untranslated.",
        ""]
    translated = 0
    entries = []
    for segment in segments:
        target = _clean_value(aligned.get(segment["id"], ""))
        status = "translated" if target else "untranslated"
        if target:
            translated += 1
        entries.append({
            "segment_id": segment["id"], "source": segment["text"],
            "target": target, "status": status,
            "locator": segment.get("locator"),
            "source_locator": segment.get("locator"),
            "line_start": segment.get("line_start"),
            "line_end": segment.get("line_end"),
        })
        bilingual_lines.append("## %s" % segment["id"])
        bilingual_lines.append("")
        bilingual_lines.append("- **Source (%s):** %s" % (
            source_lang, segment["text"].replace("\n", " ")))
        bilingual_lines.append("- **Target (%s):** %s" % (target_lang, target))
        bilingual_lines.append("")
    S.write_text(bilingual_path, "\n".join(bilingual_lines))

    full_translation = "\n\n".join(e["target"] for e in entries
                                   if e["target"]).strip()
    if not translation.strip() and full_translation:
        S.write_text(translation_path, "# Translation\n\n" + full_translation + "\n")
        translation = S.read_text(translation_path)

    term_issues = _term_issues(glossary, "\n\n".join(
        e["target"] for e in entries if e["target"]))
    for item in glossary:
        if _clean_value(item.get("target")):
            item["verified"] = not _term_issues([item], "\n\n".join(
                e["target"] for e in entries if e["target"]))
    S.write_json(ctx.output / "glossary.json", {
        "case_id": ctx.case_id, "source_language": source_lang,
        "target_language": target_lang, "locked": bool(locked),
        "source": glossary_source or "candidates", "terms": glossary[:100],
        "issues": term_issues,
    })

    notes = S.load_json(ctx.work / "translation-notes.json", {})
    flags = notes.get("flags") if isinstance(notes, dict) else []
    if flags:
        S.write_json(ctx.output / "translation-notes.json", {
            "case_id": ctx.case_id, "flags": flags,
        })

    source_all = "\n\n".join(s["text"] for s in segments)
    target_all = "\n\n".join(e["target"] for e in entries)
    numbers = S.check_numbers_preserved(source_all, target_all)
    citations = _check_citations(source_all, target_all)
    figures = _check_figures(source_all, target_all)
    source_neg = _negation_count(source_all)
    target_neg = _negation_count(target_all)
    neg_limit = max(1, int(round(0.35 * max(source_neg, target_neg))))
    negation_ok = abs(source_neg - target_neg) <= neg_limit
    source_hedge = _hedge_profile(source_all, source_lang)
    target_hedge = _hedge_profile(target_all, target_lang)
    # Strong claims must never increase; losing one hedge marker is tolerated
    # because the two languages do not have a one-to-one hedge lexicon.
    hedging_ok = (target_hedge["strong"] <= source_hedge["strong"]
                  and target_hedge["hedging"] >= source_hedge["hedging"] - 1)
    missing_segments = [e["segment_id"] for e in entries if not e["target"]]
    glossary_ok = bool(locked) and not term_issues and any(
        _clean_value(item.get("target")) for item in glossary)

    checks = {
        "segments_total": len(segments),
        "segments_translated": translated,
        "missing_segments": missing_segments,
        "numbers": numbers,
        "citations": citations,
        "figures": figures,
        "negation": {"source": source_neg, "target": target_neg,
                     "tolerance": neg_limit, "ok": negation_ok},
        "hedging": {"source": source_hedge, "target": target_hedge,
                    "ok": hedging_ok},
        "glossary": {"terms": sum(1 for item in glossary
                                  if _clean_value(item.get("target"))),
                     "locked": bool(locked), "issues": term_issues,
                     "source": glossary_source or "candidates",
                     "ok": glossary_ok},
        "alignment": {"method": methods, "issues": alignment_issues,
                      "ok": not alignment_issues},
        "alignment_issues": alignment_issues,
    }
    S.write_json(ctx.output / "translation-check.json", {
        "case_id": ctx.case_id, "direction": direction,
        "checks": checks, "entries": entries, "generated_at": S.now_iso(),
    })

    preserved = _preserved_facts(segments, source_all, target_all, glossary,
                                 source_path)
    evidence = []
    for entry in entries[:200]:
        if not entry["target"]:
            continue
        evidence.append({
            "claim_id": entry["segment_id"],
            "claim": S.excerpt(entry["target"], 120),
            "source": {"path": source_path, "locator": entry["locator"]},
            "quote": S.excerpt(entry["source"], 200),
            "paraphrase": True, "supports": "translation",
        })

    critical = (bool(missing_segments)
                or bool(numbers["missing"])
                or not citations["ok"]
                or not figures["ok"]
                or not glossary_ok
                or not negation_ok
                or not hedging_ok
                or bool(alignment_issues))
    status = "answered" if not critical and translated == len(segments) else "partial"
    if missing_segments:
        ctx.result.uncertainty("untranslated_segments",
                               ", ".join(missing_segments[:20]),
                               "%d segments remain" % len(missing_segments),
                               "translate them and rerun finalize")
    if numbers["missing"]:
        ctx.result.uncertainty("number_mismatch",
                               json.dumps(numbers["missing"][:20],
                                          ensure_ascii=False),
                               "numeric fidelity needs review",
                               "compare source and target")
    if citations["missing"]:
        ctx.result.uncertainty("citation_mismatch",
                               json.dumps(citations["missing"][:20],
                                          ensure_ascii=False),
                               "citation markers were dropped or altered",
                               "restore the original citation markers")
    if figures["missing"]:
        ctx.result.uncertainty("figure_reference_mismatch",
                               json.dumps(figures["missing"][:20],
                                          ensure_ascii=False),
                               "figure/table/equation identifiers drifted",
                               "restore the original identifiers")
    if term_issues:
        ctx.result.uncertainty("glossary_consistency",
                               "; ".join(term_issues[:10]),
                               "term targets are not all present",
                               "apply the locked glossary")
    if not locked:
        ctx.result.uncertainty("glossary_not_locked",
                               "work/glossary.json is missing or has no targets",
                               "terminology consistency is not verifiable",
                               "write a glossary with source->target terms")
    if not negation_ok:
        ctx.result.uncertainty("negation_mismatch",
                               "source=%d target=%d tolerance=%d" % (
                                   source_neg, target_neg, neg_limit),
                               "negation scope may have changed",
                               "compare the affected sentences")
    if not hedging_ok:
        ctx.result.uncertainty("hedging_mismatch",
                               "source=%s target=%s" % (
                                   json.dumps(source_hedge, ensure_ascii=False),
                                   json.dumps(target_hedge, ensure_ascii=False)),
                               "claim strength may have been upgraded",
                               "restore may/suggest-level wording")
    if alignment_issues:
        ctx.result.uncertainty("alignment", "; ".join(alignment_issues),
                               "paragraph alignment is approximate",
                               "use bilingual.md P IDs")
    if flags:
        ctx.result.uncertainty("translation_flags",
                               "%d model-recorded flags" % len(flags),
                               "some wording needs human review",
                               "see output/translation-notes.json")

    ctx.result.add_artifact(translation_path, "deliverable", "md",
                            "complete translation")
    ctx.result.add_artifact(bilingual_path, "alignment", "md",
                            "paragraph-level bilingual table")
    ctx.result.add_artifact(ctx.output / "glossary.json", "glossary", "json",
                            "locked terminology")
    ctx.result.add_artifact(ctx.output / "translation-check.json", "audit", "json",
                            "numbers, citations, figures, negation, hedging "
                            "and glossary checks")
    if flags:
        ctx.result.add_artifact(ctx.output / "translation-notes.json", "notes",
                                "json", "model-recorded translation flags")
    ctx.result.change("translate", source_path,
                      "%d/%d segments translated" % (translated, len(segments)))
    ctx.result.change("audit", "translation",
                      "numbers/citations/figures/terms/negation/hedging checked")
    preservation = {
        "numbers_match": not numbers["missing"],
        "citation_numbers_match": citations["ok"],
        "figure_references_match": figures["ok"],
        "negation_match": negation_ok,
        "modality_match": hedging_ok,
        "glossary_locked": bool(locked),
    }
    return ctx.result.finalize(
        status,
        deliverable=translation_path,
        result={
            "summary": "Translated %d/%d segments (%s); glossary %d terms "
                       "(locked=%s); numbers=%s citations=%s figures=%s "
                       "negation=%s hedging=%s." % (
                           translated, len(segments), direction,
                           checks["glossary"]["terms"], bool(locked),
                           not numbers["missing"], citations["ok"],
                           figures["ok"], negation_ok, hedging_ok),
            "direction": direction,
            "source_path": source_path,
            "segments": len(segments),
            "translated": translated,
            "untranslated": missing_segments,
            "checks": checks,
            "preservation": preservation,
            "translation": "translation.md",
            "bilingual": "bilingual.md",
            "glossary": "glossary.json",
        },
        claims=[{"claim_id": e["segment_id"],
                 "text": S.excerpt(e["target"], 140),
                 "evidence": e["segment_id"]}
                for e in entries[:200] if e["target"]],
        preserved_facts=preserved, evidence_map=evidence,
    )
