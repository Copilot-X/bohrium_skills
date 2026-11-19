#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Genre- and manuscript-specific checks for challenge-308a6fdc.

``audit(result, manuscript_text, contract)`` is called by
``writing_lib.build_result`` after the manuscript, sentence map and evidence
map exist.  It checks what generic ledger validators do not:

  * required section coverage and section status for the declared genre
  * abstract length and abstract-number subset of body/source values
  * citation numbering, entries and sentence-level citation binding
  * numeric sentences without a bound fact (fabrication/drift signal)
  * conclusion/abstract boundary: causal wording without verified evidence
  * required declaration fields present or explicitly marked missing
  * placeholders and genre-specific limits (letter/abstract/review)

Checks never fabricate evidence and never rewrite the manuscript.  Problems
lower the status to ``partial``; warnings do not block delivery.
"""
from __future__ import annotations

import re

CITE_RE = re.compile(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]")
PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|待补充|待填写|待核实)\b", re.I)
CAUSAL_RE = re.compile(
    r"\b(causes?\s+(?:the|a|an|this|that|these|those|an?\s+(?:increase|"
    r"decrease|change|reduction|loss))|caused by|leads? to|resulted in|"
    r"proves?|demonstrates? that|confirms? that|drives?)\b|"
    r"导致|证明了|证实了|使得|造成", re.I)
STAT_RE = re.compile(r"\b(p\s*[<=>]\s*0?\.\d+|95%\s*CI|confidence interval|"
                     r"ANOVA|t-test|significant)\b|显著性|置信区间|检验", re.I)
NUMBER_RE = re.compile(
    r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)\s*(?:%|wt%|vol%|°C|℃|h|hr|min|s|bar|kPa|MPa|Pa|"
    r"mL|µL|uL|L|mg|µg|ug|g|kg|K|nm|µm|um|mm|cm|m|eV|keV|ppm|M|mM|µM|uM)?")
CORE = {"research-article": ["abstract", "introduction", "methods", "results",
                             "discussion", "conclusion"],
        "review": ["abstract", "introduction", "main_body", "discussion",
                   "conclusion"],
        "letter": ["abstract", "introduction", "methods", "results", "discussion"],
        "case-report": ["abstract", "introduction", "case_presentation",
                        "discussion", "conclusion"],
        "abstract": ["abstract"]}
DECL_LABELS = {
    "data_availability": r"data availability|数据可得性|数据可用性",
    "conflict_of_interest": r"conflict of interest|competing interests|利益冲突",
    "author_contributions": r"author contributions|作者贡献",
    "ethics": r"ethics|伦理|IRB|知情同意",
    "funding": r"funding|grant|基金|资助",
}


def _tokens(text):
    return set(re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}|[一-鿿]{2,}",
                          str(text or "").lower()))


def _numbers(text):
    cleaned = CITE_RE.sub("", str(text or ""))
    values = set()
    for match in NUMBER_RE.finditer(cleaned):
        token = re.sub(r"\s+", "", match.group(0)).replace(",", "")
        if not token:
            continue
        if re.fullmatch(r"\d{1,2}", token):        # tiny counts, e.g. n = 3
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", token):  # bare publication years
            continue
        values.add(token)
    return values


def _sections(result):
    manuscript = result.get("manuscript") or {}
    rows = manuscript.get("sections") or []
    return {str(item.get("id")): item for item in rows
            if isinstance(item, dict) and item.get("id")}


def _section_text(manuscript_text, section):
    title = str(section.get("title") or "").strip()
    if not title:
        return ""
    pattern = re.compile(r"^#{1,6}\s+%s\s*$(.*?)(?=^#{1,6}\s|\Z)" %
                         re.escape(title), re.M | re.S)
    match = pattern.search(str(manuscript_text or ""))
    return match.group(1) if match else ""


def audit(result, manuscript_text, contract):
    checks = []
    problems = []
    warnings = []

    def add(check_id, ok, detail, severity="problem"):
        checks.append({"id": check_id, "ok": bool(ok), "detail": str(detail)[:600]})
        if not ok:
            if severity == "warning":
                warnings.append("%s: %s" % (check_id, detail))
            else:
                problems.append("%s: %s" % (check_id, detail))

    text = str(manuscript_text or "")
    genre = str(contract.get("genre") or "research-article")
    sections = _sections(result)
    source_map = result.get("source_map") or []
    sentence_map = result.get("sentence_map") or []
    references = (result.get("citations") or {}).get("entries") or []
    ref_numbers = {int(item.get("citation_number")) for item in references
                   if str(item.get("citation_number", "")).isdigit()}
    fact_status = {str(item.get("fact_id")): item.get("verification_status")
                   for item in source_map}

    # S1: required structure for the declared genre
    required = [s for s in (contract.get("sections") or []) if s != "references"]
    missing = [s for s in required if s not in sections]
    empty = [s for s in required if s in sections
             and int(sections[s].get("words") or 0) < 15]
    add("genre_required_sections", not missing,
        "missing required sections: %s" % ", ".join(missing))
    add("genre_section_substance", not empty,
        "sections under 15 words: %s" % ", ".join(empty), "warning")
    core = CORE.get(genre, CORE["research-article"])
    core_missing = [s for s in core if s not in sections]
    add("genre_core_sections", not core_missing,
        "genre %s missing core sections: %s" % (genre, ", ".join(core_missing)))
    if genre == "letter":
        budget = int(contract.get("word_budget") or 2500)
        add("letter_length", int((result.get("manuscript") or {}).get("word_count") or 0)
            <= budget * 1.5, "letter length exceeds 150% of budget")
    if genre == "review":
        body = sections.get("main_body") or {}
        add("review_thematic_body", int(body.get("words") or 0) >= 120,
            "review main_body is too short", "warning")

    # S2: abstract length and numeric subset
    abstract = sections.get("abstract") or {}
    abstract_text = _section_text(text, abstract)
    if abstract_text:
        add("abstract_length", len(_tokens(abstract_text)) <= 450,
            "abstract is longer than 450 tokens", "warning")
        source_values = set()
        for fact in source_map:
            source_values |= _numbers(fact.get("value"))
        body_values = set()
        for section in sections.values():
            if section.get("id") != "abstract":
                body_values |= _numbers(_section_text(text, section))
        unknown = sorted(_numbers(abstract_text) - source_values - body_values)
        add("abstract_numbers_traceable", not unknown,
            "abstract numbers absent from source_map and body: %s" %
            ", ".join(unknown[:10]))
    else:
        add("abstract_present", False, "no abstract section found")

    # S3: citations
    cited = set()
    for group in CITE_RE.findall(text):
        for part in re.split(r"[,，]", group):
            if part.strip().isdigit():
                cited.add(int(part.strip()))
    unknown_citations = sorted(cited - ref_numbers)
    add("citation_entries_exist", not unknown_citations,
        "citation numbers without a reference entry: %s" %
        ", ".join(map(str, unknown_citations)))
    if cited:
        gaps = sorted(set(range(1, max(cited) + 1)) - cited)
        add("citation_numbering_contiguous", not gaps,
            "citation numbering gaps: %s" % ", ".join(map(str, gaps[:10])),
            "warning")
    bound = {}
    for sentence in sentence_map:
        for number in sentence.get("citation_numbers") or []:
            bound[int(number)] = bound.get(int(number), 0) + 1
    add("citations_bound_to_sentences", all(n in bound for n in cited),
        "citations not bound in sentence_map: %s" %
        ", ".join(map(str, sorted(cited - set(bound)))), "warning")
    declared = {int(item.get("citation_number")) for item in references
                if str(item.get("citation_number", "")).isdigit()}
    unused = sorted(declared - cited)
    add("no_orphan_references", not unused,
        "reference entries never cited: %s" % ", ".join(map(str, unused)),
        "warning")
    missing_fields = []
    for entry in references:
        fields = entry.get("fields") or {}
        if fields:
            for key in ("author", "year", "title"):
                if not fields.get(key) or fields.get(key) == "not_available":
                    missing_fields.append("%s.%s" % (entry.get("citation_number"), key))
    add("reference_metadata_complete", not missing_fields,
        "reference entries missing fields: %s" % ", ".join(missing_fields[:8]),
        "warning")

    # S4: numeric sentences must be bound to a fact
    untraced = []
    for sentence in sentence_map:
        section_id = str(sentence.get("section"))
        if section_id in ("references", "declarations") or \
                section_id in DECL_LABELS:
            continue
        if _numbers(sentence.get("text")) and not sentence.get("fact_ids"):
            untraced.append("%s:%s" % (sentence.get("sentence_id"),
                                       str(sentence.get("text"))[:80]))
    add("numeric_sentences_traceable", not untraced,
        "numeric sentences without fact binding: %s" % "; ".join(untraced[:5]))

    # S5: conclusion/abstract boundary
    boundary_sections = [sections.get(k) for k in ("abstract", "conclusion",
                                                   "discussion")
                         if sections.get(k)]
    risky = []
    for section in boundary_sections:
        body = _section_text(text, section)
        for sentence in re.split(r"(?<=[。！？.!?])\s+", body):
            if not CAUSAL_RE.search(sentence):
                continue
            if re.search(r"not supported|no causal|不支持因果|不构成因果|"
                         r"cannot establish|does not establish", sentence, re.I):
                continue
            if not STAT_RE.search(body):
                risky.append(str(sentence)[:120])
    add("causal_language_bounded", not risky,
        "causal wording without verified statistics/boundary: %s" %
        "; ".join(risky[:4]), "warning")
    for sentence in sentence_map:
        if str(sentence.get("section")) not in ("abstract", "conclusion"):
            continue
        if not _numbers(sentence.get("text")) or not sentence.get("fact_ids"):
            continue
        bad = [fid for fid in sentence["fact_ids"]
               if fact_status.get(fid) == "estimated"]
        if bad:
            add("estimated_values_not_in_conclusion", False,
                "estimated facts used in abstract/conclusion: %s" % ", ".join(bad),
                "warning")

    # S6: declarations
    required_decls = contract.get("required_declarations") or []
    missing_decls = []
    for key in required_decls:
        pattern = DECL_LABELS.get(key, re.escape(key.replace("_", " ")))
        if not re.search(pattern, text, re.I):
            missing_decls.append(key)
    add("declarations_present", not missing_decls,
        "required declarations absent: %s" % ", ".join(missing_decls))
    for key in required_decls:
        pattern = DECL_LABELS.get(key, re.escape(key.replace("_", " ")))
        match = re.search(r"(?:" + pattern + r")[^\n]*", text, re.I)
        if match and "not_provided_in_materials" not in match.group(0) and \
                not re.search(r"\b(?:no|not|none|未|不适用)\b",
                              match.group(0), re.I):
            warnings.append("declaration_%s: value should be confirmed by the author"
                            % key)

    # S7: placeholders and scaffold state
    add("no_placeholders", not PLACEHOLDER_RE.search(text),
        "placeholder text found: %s" % ", ".join(
            sorted(set(m.group(0) for m in PLACEHOLDER_RE.finditer(text)))))
    add("not_a_partial_scaffold",
        "PARTIAL SCAFFOLD" not in text,
        "the deliverable is still the PARTIAL scaffold", "warning")

    # S8: official result shape sanity
    deliverable = result.get("deliverable") or {}
    add("deliverable_declared", bool(deliverable.get("path")),
        "result.deliverable.path is empty")
    add("evidence_map_present", isinstance(result.get("evidence_map"), list),
        "result.evidence_map is not a list")
    add("preserved_facts_present", isinstance(result.get("preserved_facts"), list),
        "result.preserved_facts is not a list")

    stats = {
        "genre": genre,
        "sections": len(sections),
        "required_sections": len(required),
        "missing_sections": missing,
        "words": (result.get("manuscript") or {}).get("word_count"),
        "facts": len(source_map),
        "sentences": len(sentence_map),
        "citations_used": sorted(cited),
        "citations_declared": sorted(ref_numbers),
        "untraced_numeric_sentences": len(untraced),
    }
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "checks": checks, "stats": stats}
