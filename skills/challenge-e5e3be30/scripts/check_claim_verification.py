#!/usr/bin/env python3
"""Validate a claim-verification result.json against the input bundle.

Usage:
    python3 check_claim_verification.py --result /app/output/result.json \
        --input /input --json
    python3 check_claim_verification.py --result /app/output/result.json \
        --input /input --source /app/work/extracted_text --json --out report.json
    python3 check_claim_verification.py --self-test --json

Checks
  * every input claim_id is covered by at least one verdict
  * verdicts use only supported / partially_supported / contradicted /
    insufficient_evidence
  * assertion_id is unique; claim_id points to an input claim
  * each evidence item has a locator and a verbatim quote from the input
  * quote belongs to the stated paragraph when paragraph text is available
  * evidence lists are consistent with the verdict
    - supported: >=1 supporting, zero counter
    - contradicted: >=1 counter
    - partially_supported: >=1 supporting plus limitations or counter evidence
    - insufficient_evidence: search_scope/reason; no fabricated numbers
  * numbers in a supported assertion appear in its supporting quotes unless a
    numeric_check is supplied
  * rewrite numbers must come from evidence quotes
  * summary counts match verdicts; high_risk ids exist
  * checked_range / unchecked / uncertainty / change_log / support_files_used

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import copy
import json
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

VERDICTS = ("supported", "partially_supported", "contradicted",
            "insufficient_evidence")
LEGACY_VERDICTS = {"not_found": "insufficient_evidence",
                   "ambiguous": "insufficient_evidence"}
CLAIM_TYPES = {"fact", "comparison", "causal", "numeric", "range",
               "negation", "significance", "attribution", "other"}
STATUSES = {"completed", "partial", "insufficient_evidence", "input_error"}
EVIDENCE_LEVELS = {"primary", "review", "secondary", "unknown"}
PLACEHOLDER = re.compile(
    r"(TODO|TBD|FIXME|<placeholder>|REPLACE_WITH|待补充|待填写|xxx)",
    re.IGNORECASE)
NUMBER = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?")
NUMBER_UNIT = re.compile(
    r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*"
    r"(%|°C|℃|K|mg|kg|g|µg|μg|ug|ng|mL|ml|L|µL|μL|uL|h|hr|hours?|min|s|"
    r"M|mM|µM|μM|uM|nM|bar|kPa|MPa|Pa|ppm|nm|µm|μm|um|mm|cm|m|Da|kDa|eV|"
    r"V|mA|W|kW|Hz|kHz|MHz|GHz|fold|x)?",
    re.IGNORECASE)
WS = re.compile(r"\s+")
COMPARATOR = re.compile(
    r"(高于|低于|超过|少于|大于|小于|相比|较之|优于|劣于|"
    r"more than|less than|higher than|lower than|greater than|fewer than|"
    r"compared (?:with|to)|versus|\bvs\.?\b)", re.IGNORECASE)
NEGATION = re.compile(
    r"(未|未见|未发现|无显著|没有|并非|不能|不支持|不再|不会|不显著|"
    r"\bnot\b|\bno\b|\bnever\b|\bwithout\b|did not|does not|failed to|"
    r"no significant)", re.IGNORECASE)
SIGNIFICANCE = re.compile(
    r"(显著|显著性|p\s*[<=>]\s*0?\.\d+|P\s*value|confidence interval|"
    r"95%\s*CI|\bsignificant(?:ly)?\b|\bsignificance\b)", re.IGNORECASE)

CLAIM_LIST_KEYS = ("claims", "claims_to_verify", "claimsToVerify",
                   "assertions", "questions")
PARAGRAPH_LIST_KEYS = ("paragraphs", "paragraph", "passages", "sentences")
CLAIM_ID_KEYS = ("claim_id", "claimId", "id", "question_id", "questionId",
                 "assertion_id")
CLAIM_TEXT_KEYS = ("text", "claim", "statement", "question", "content")
PID_KEYS = ("paragraph_id", "paragraphId", "pid", "id")
PTEXT_KEYS = ("text", "content", "paragraph", "sentence")
DOC_ID_KEYS = ("doc_id", "docId", "document_id", "documentId", "source_id",
               "sourceId", "paper_id", "paperId")
TEXT_SUFFIXES = {".md", ".txt", ".csv", ".tsv", ".tex", ".html", ".htm",
                 ".xml", ".yaml", ".yml"}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    return WS.sub(" ", text).strip().casefold()


def numeric_values(text):
    return {m.group(0).replace(",", ".") for m in NUMBER.finditer(str(text or ""))}


def substantive_numbers(text):
    """Numbers that matter for rewrite safety (>=2 digits, decimal or unit)."""
    out = set()
    for match in NUMBER_UNIT.finditer(str(text or "")):
        value, unit = match.group(1), match.group(2)
        digits = re.sub(r"[.,]", "", value)
        if len(digits) >= 2 or unit or "." in value or "," in value:
            out.add(value.replace(",", "."))
    return out


def first_value(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def collect_claims(data):
    claims, seen = [], set()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in CLAIM_LIST_KEYS and isinstance(item, list):
                    for entry in item:
                        if not isinstance(entry, dict):
                            continue
                        cid = first_value(entry, CLAIM_ID_KEYS)
                        text = first_value(entry, CLAIM_TEXT_KEYS)
                        if cid is not None and text is not None:
                            cid = str(cid)
                            if cid not in seen:
                                seen.add(cid)
                                claims.append({"claim_id": cid,
                                               "text": str(text)})
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(data)
    return claims


def collect_paragraphs(data):
    paragraphs, doc_paragraphs, paragraph_ids = {}, {}, set()
    doc_ids = set()
    ambiguous = set()

    def add(pid, text, doc_id):
        pid, text = str(pid), str(text)
        paragraph_ids.add(pid)
        if doc_id:
            doc_id = str(doc_id)
            doc_ids.add(doc_id)
            doc_paragraphs["%s::%s" % (doc_id, pid)] = text
        if pid in paragraphs and normalize(paragraphs[pid]) != normalize(text):
            ambiguous.add(pid)
        elif pid not in paragraphs:
            paragraphs[pid] = text

    def visit(value, inherited_doc=None):
        if isinstance(value, dict):
            doc_id = first_value(value, DOC_ID_KEYS) or inherited_doc
            for key, item in value.items():
                if key in PARAGRAPH_LIST_KEYS and isinstance(item, list):
                    for entry in item:
                        if not isinstance(entry, dict):
                            continue
                        pid = first_value(entry, PID_KEYS)
                        text = first_value(entry, PTEXT_KEYS)
                        entry_doc = first_value(entry, DOC_ID_KEYS) or doc_id
                        if pid is not None and text is not None:
                            add(pid, text, entry_doc)
                        visit(entry, entry_doc)
                else:
                    visit(item, doc_id)
        elif isinstance(value, list):
            for item in value:
                visit(item, inherited_doc)

    visit(data)
    for pid in ambiguous:
        paragraphs.pop(pid, None)
    return {"paragraphs": paragraphs,
            "doc_paragraphs": doc_paragraphs,
            "paragraph_ids": paragraph_ids,
            "doc_ids": doc_ids,
            "ambiguous": ambiguous}


def collect_source_strings(value):
    """Strings that belong to the source material, skipping claim lists."""
    chunks = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in CLAIM_LIST_KEYS:
                continue
            chunks.extend(collect_source_strings(item))
    elif isinstance(value, list):
        for item in value:
            chunks.extend(collect_source_strings(item))
    elif isinstance(value, str):
        chunks.append(value)
    return chunks


def collect_input(input_path, extra_sources=None):
    ctx = {"claims": [], "paragraphs": {}, "doc_paragraphs": {},
           "paragraph_ids": set(), "doc_ids": set(), "ambiguous": set(),
           "texts": [], "case_id": None, "input_found": False,
           "json_files": []}
    path = Path(input_path) if input_path else None
    json_payloads = []

    if path and path.exists():
        ctx["input_found"] = True
        if path.is_dir():
            files = sorted(p for p in path.rglob("*") if p.is_file())
            files.sort(key=lambda p: (0 if "claim" in p.name.lower()
                                      else 1 if "case" in p.name.lower()
                                      else 2))
        else:
            files = [path]
        for file in files:
            if file.stat().st_size > 5_000_000:
                continue
            if file.name.lower() in ("result.json", "output.json"):
                continue
            if file.suffix.lower() == ".json":
                try:
                    payload = load_json(file)
                except (OSError, json.JSONDecodeError):
                    continue
                json_payloads.append(payload)
                ctx["json_files"].append(str(file))
            elif file.suffix.lower() in TEXT_SUFFIXES:
                try:
                    ctx["texts"].append(file.read_text(encoding="utf-8",
                                                       errors="replace"))
                except OSError:
                    pass

    for payload in json_payloads:
        for claim in collect_claims(payload):
            if claim["claim_id"] not in {c["claim_id"] for c in ctx["claims"]}:
                ctx["claims"].append(claim)
        paragraph_info = collect_paragraphs(payload)
        for pid, text in paragraph_info["paragraphs"].items():
            if pid not in ctx["paragraphs"]:
                ctx["paragraphs"][pid] = text
        ctx["doc_paragraphs"].update(paragraph_info["doc_paragraphs"])
        ctx["paragraph_ids"].update(paragraph_info["paragraph_ids"])
        ctx["doc_ids"].update(paragraph_info["doc_ids"])
        ctx["ambiguous"].update(paragraph_info["ambiguous"])
        ctx["texts"].extend(collect_source_strings(payload))
        if ctx["case_id"] is None and isinstance(payload, dict):
            ctx["case_id"] = payload.get("case_id") or payload.get("caseId")

    # Extra extracted-text/corpus paths supplied by the agent.
    for source in extra_sources or []:
        source = Path(source)
        if not source.exists():
            continue
        files = [source] if source.is_file() else sorted(source.rglob("*"))
        for file in files:
            if not file.is_file() or file.stat().st_size > 5_000_000:
                continue
            if file.suffix.lower() == ".json":
                try:
                    payload = load_json(file)
                except (OSError, json.JSONDecodeError):
                    continue
                paragraph_info = collect_paragraphs(payload)
                for pid, text in paragraph_info["paragraphs"].items():
                    if pid not in ctx["paragraphs"]:
                        ctx["paragraphs"][pid] = text
                ctx["doc_paragraphs"].update(paragraph_info["doc_paragraphs"])
                ctx["paragraph_ids"].update(paragraph_info["paragraph_ids"])
                ctx["doc_ids"].update(paragraph_info["doc_ids"])
                ctx["ambiguous"].update(paragraph_info["ambiguous"])
                ctx["texts"].extend(collect_source_strings(payload))
            elif file.suffix.lower() in TEXT_SUFFIXES:
                try:
                    ctx["texts"].append(file.read_text(encoding="utf-8",
                                                       errors="replace"))
                except OSError:
                    pass

    ctx["corpus"] = normalize("\n".join(ctx["texts"]))
    return ctx


def paragraph_text(ctx, pid, doc_id=None):
    """Return (found, text_or_None). text None means id exists but text is unclear."""
    pid = str(pid)
    if doc_id:
        key = "%s::%s" % (doc_id, pid)
        if key in ctx["doc_paragraphs"]:
            return True, ctx["doc_paragraphs"][key]
    if pid in ctx["paragraphs"]:
        return True, ctx["paragraphs"][pid]
    if pid in ctx["paragraph_ids"]:
        return True, None
    return False, None


def check_evidence(items, label, kind, ctx, problems, warnings, checks):
    if items is None:
        return []
    if not isinstance(items, list):
        problems.append("%s %s is not a list" % (label, kind))
        return []
    cleaned = []
    for index, evidence in enumerate(items):
        where = "%s %s[%d]" % (label, kind, index)
        if not isinstance(evidence, dict):
            problems.append("%s is not an object" % where)
            continue
        quote = str(evidence.get("quote") or "").strip()
        pid = evidence.get("paragraph_id")
        if pid is None and evidence.get("paragraph_ids"):
            pids = evidence["paragraph_ids"]
            if isinstance(pids, list) and len(pids) == 1:
                pid = pids[0]
        locator = (evidence.get("locator") or evidence.get("page")
                   or evidence.get("section_id") or evidence.get("table_id")
                   or evidence.get("figure_id"))
        if not quote:
            problems.append("%s missing quote" % where)
        else:
            checks["quotes"] += 1
            normalized_quote = normalize(quote)
            if len(normalized_quote) < 6:
                problems.append("%s quote too short" % where)
            if ctx["corpus"] and normalized_quote not in ctx["corpus"]:
                problems.append("%s quote not verbatim in input: %r"
                                % (where, quote[:70]))
            elif not ctx["corpus"]:
                warnings.append("%s quote check skipped: no readable source text"
                                % where)
            if pid is not None:
                found, text = paragraph_text(ctx, pid, evidence.get("doc_id"))
                if not found:
                    problems.append("%s unknown paragraph_id %s" % (where, pid))
                elif text is not None and normalized_quote not in normalize(text):
                    problems.append("%s quote not found in stated paragraph %s"
                                    % (where, pid))
                elif text is None:
                    warnings.append("%s paragraph %s text is ambiguous; used corpus "
                                    "fallback" % (where, pid))
            elif not locator:
                problems.append("%s missing locator (paragraph_id/page/section_id)"
                                % where)
        doc_id = evidence.get("doc_id")
        if doc_id and ctx["doc_ids"] and str(doc_id) not in ctx["doc_ids"]:
            problems.append("%s unknown doc_id %s" % (where, doc_id))
        elif (pid is not None and doc_id and ctx["doc_paragraphs"]
              and str(doc_id) in ctx["doc_ids"]
              and "%s::%s" % (doc_id, pid) not in ctx["doc_paragraphs"]):
            problems.append("%s paragraph %s is not under doc_id %s"
                            % (where, pid, doc_id))
        level = evidence.get("evidence_level")
        if level not in EVIDENCE_LEVELS:
            warnings.append("%s evidence_level missing/unknown" % where)
        if not isinstance(evidence.get("secondary_source", False), bool):
            warnings.append("%s secondary_source should be boolean" % where)
        cleaned.append({"quote": quote,
                        "paragraph_id": pid,
                        "doc_id": doc_id,
                        "secondary_source": bool(
                            evidence.get("secondary_source", False)),
                        "evidence_level": level})
    return cleaned


def check_result(result, ctx, problems, warnings, checks):
    if not isinstance(result, dict):
        problems.append("result.json root is not an object")
        return
    for key in ("case_id", "status", "verdicts", "summary", "checked_range",
                "uncertainty", "change_log"):
        if key not in result:
            problems.append("result.json missing %s" % key)

    if ctx.get("case_id") and result.get("case_id") not in (None, ""):
        if str(result.get("case_id")) != str(ctx["case_id"]):
            problems.append("result.case_id does not match input case_id")
    if result.get("status") not in STATUSES:
        problems.append("invalid status: %r (use %s)"
                        % (result.get("status"), "/".join(sorted(STATUSES))))

    verdicts = result.get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        problems.append("result has no verdicts[]")
        return

    input_claim_ids = {c["claim_id"] for c in ctx["claims"]}
    covered = set()
    seen_assertions = set()
    counts = {verdict: 0 for verdict in VERDICTS}

    for index, verdict in enumerate(verdicts):
        label = "verdicts[%d]" % index
        if not isinstance(verdict, dict):
            problems.append("%s is not an object" % label)
            continue
        assertion_id = verdict.get("assertion_id")
        if not assertion_id:
            problems.append("%s missing assertion_id" % label)
        else:
            assertion_id = str(assertion_id)
            label = "verdict %s" % assertion_id
            if assertion_id in seen_assertions:
                problems.append("duplicate assertion_id: %s" % assertion_id)
            seen_assertions.add(assertion_id)

        claim_id = verdict.get("claim_id")
        if not claim_id:
            problems.append("%s missing claim_id" % label)
        else:
            claim_id = str(claim_id)
            covered.add(claim_id)
            if input_claim_ids and claim_id not in input_claim_ids:
                problems.append("%s unknown claim_id %s (not in input)"
                                % (label, claim_id))

        source_claim = str(verdict.get("source_claim") or "")
        assertion = str(verdict.get("assertion") or "")
        if not assertion:
            problems.append("%s missing assertion text" % label)
        if not source_claim:
            warnings.append("%s missing source_claim" % label)

        verdict_name = verdict.get("verdict")
        if verdict_name not in VERDICTS:
            if verdict_name in LEGACY_VERDICTS:
                problems.append("%s legacy verdict %r; use %r"
                                % (label, verdict_name,
                                   LEGACY_VERDICTS[verdict_name]))
            else:
                problems.append("%s invalid verdict %r; use one of %s"
                                % (label, verdict_name, "/".join(VERDICTS)))
            verdict_name = None
        else:
            counts[verdict_name] += 1

        confidence = verdict.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            problems.append("%s confidence must be a number in [0,1]" % label)
        elif not 0 <= confidence <= 1:
            problems.append("%s confidence out of [0,1]" % label)
        elif verdict_name in ("supported", "contradicted") and confidence < 0.6:
            warnings.append("%s %s with confidence < 0.6" % (label, verdict_name))

        if not str(verdict.get("reason") or "").strip():
            problems.append("%s missing reason" % label)

        claim_type = verdict.get("claim_type")
        if claim_type not in CLAIM_TYPES:
            warnings.append("%s claim_type missing/unknown: %r"
                            % (label, claim_type))

        supporting = check_evidence(verdict.get("supporting_evidence"), label,
                                    "supporting_evidence", ctx, problems,
                                    warnings, checks)
        counter = check_evidence(verdict.get("counter_evidence"), label,
                                 "counter_evidence", ctx, problems, warnings,
                                 checks)
        limitations = verdict.get("limitations") or []
        if not isinstance(limitations, list):
            problems.append("%s limitations must be a list" % label)
            limitations = []

        if verdict_name == "supported":
            if not supporting:
                problems.append("%s verdict=supported but no supporting_evidence"
                                % label)
            if counter:
                problems.append("%s verdict=supported but counter_evidence is "
                                "non-empty; use partially_supported/contradicted"
                                % label)
        elif verdict_name == "contradicted":
            if not counter:
                problems.append("%s verdict=contradicted but no counter_evidence"
                                % label)
        elif verdict_name == "partially_supported":
            if not supporting:
                problems.append("%s verdict=partially_supported but no "
                                "supporting_evidence" % label)
            if not counter and not limitations:
                problems.append("%s verdict=partially_supported needs "
                                "counter_evidence or limitations" % label)
        elif verdict_name == "insufficient_evidence":
            scope = str(verdict.get("search_scope") or "").strip()
            if not scope:
                problems.append("%s verdict=insufficient_evidence requires "
                                "search_scope" % label)
            if counter and not limitations:
                warnings.append("%s insufficient_evidence has counter_evidence; "
                                "explain the conflict in limitations" % label)

        all_quotes = supporting + counter
        all_quote_text = " ".join(item["quote"] for item in all_quotes)
        all_quote_numbers = numeric_values(all_quote_text)

        numeric_check = verdict.get("numeric_check")
        if numeric_check is not None and not isinstance(numeric_check, dict):
            problems.append("%s numeric_check must be an object" % label)
            numeric_check = None

        assertion_numbers = numeric_values(assertion)
        if verdict_name == "supported" and assertion_numbers:
            support_numbers = numeric_values(
                " ".join(item["quote"] for item in supporting))
            missing_numbers = assertion_numbers - support_numbers
            if missing_numbers and not (numeric_check
                                        and numeric_check.get("claimed")
                                        and numeric_check.get("observed")):
                problems.append("%s assertion numbers %s are not in supporting "
                                "quotes and numeric_check is missing"
                                % (label, sorted(missing_numbers)))
        elif verdict_name == "partially_supported" and assertion_numbers:
            support_numbers = numeric_values(
                " ".join(item["quote"] for item in supporting + counter))
            missing_numbers = assertion_numbers - support_numbers
            if missing_numbers and not numeric_check:
                warnings.append("%s assertion numbers %s are not in evidence; "
                                "document them in numeric_check"
                                % (label, sorted(missing_numbers)))

        rewrite = verdict.get("rewrite")
        rewrite_text = str(rewrite or "").strip()
        if verdict_name in ("contradicted", "insufficient_evidence"):
            if not rewrite_text:
                problems.append("%s verdict=%s requires rewrite"
                                % (label, verdict_name))
        if rewrite_text:
            # Restating the claimed value ("material does not report 200 h")
            # is allowed; a number absent from evidence AND from the original
            # claim is new information.
            allowed_numbers = (all_quote_numbers
                               | substantive_numbers(assertion)
                               | substantive_numbers(source_claim))
            if numeric_check:
                allowed_numbers |= substantive_numbers(
                    json.dumps(numeric_check, ensure_ascii=False))
            extra_numbers = substantive_numbers(rewrite_text) - allowed_numbers
            if extra_numbers:
                problems.append("%s rewrite introduces numbers not in evidence "
                                "or original claim: %s"
                                % (label, sorted(extra_numbers)))

        # Preserve comparator / negation / significance / conditions.
        if COMPARATOR.search(assertion) and not verdict.get("comparator"):
            warnings.append("%s assertion has a comparator but comparator field "
                            "is empty" % label)
        if NEGATION.search(assertion) and "negation" not in verdict:
            warnings.append("%s assertion has negation but negation field is "
                            "missing" % label)
        if SIGNIFICANCE.search(assertion) and not verdict.get("significance"):
            warnings.append("%s assertion mentions significance but significance "
                            "field is empty" % label)
        if verdict_name in ("supported", "partially_supported") \
                and "conditions" not in verdict:
            warnings.append("%s missing conditions field" % label)

        if any(item["secondary_source"] for item in supporting) \
                and confidence is not None and isinstance(confidence, (int, float)) \
                and confidence > 0.8:
            warnings.append("%s high confidence on secondary source" % label)
        if verdict.get("retraction_flag") and verdict_name == "supported":
            warnings.append("%s supported by a retracted source" % label)

        if verdict.get("match_quality") not in (
                None, "exact", "near_exact", "partial", "mismatch",
                "not_found", "ambiguous"):
            warnings.append("%s invalid match_quality: %r"
                            % (label, verdict.get("match_quality")))
        if verdict.get("severity") not in (
                None, "critical", "major", "minor", "none"):
            warnings.append("%s invalid severity: %r"
                            % (label, verdict.get("severity")))
        if verdict.get("verification_route") not in (
                None, "local_text", "manual", "metadata"):
            warnings.append("%s invalid verification_route: %r"
                            % (label, verdict.get("verification_route")))
        for flag in ("retraction_flag", "preprint_flag"):
            if flag in verdict and not isinstance(verdict[flag], bool):
                warnings.append("%s %s should be boolean" % (label, flag))

    missing_claims = input_claim_ids - covered
    if missing_claims:
        problems.append("input claims not covered by any verdict: %s"
                        % ", ".join(sorted(missing_claims)))
    checks["input_claims"] = len(input_claim_ids)
    checks["covered_claims"] = len(input_claim_ids & covered)
    checks["verdicts"] = len(verdicts)
    checks["assertions"] = len(seen_assertions)

    # summary
    summary = result.get("summary")
    if not isinstance(summary, dict):
        problems.append("summary is not an object")
    else:
        if summary.get("total") != len(verdicts):
            problems.append("summary.total=%r but verdicts=%d"
                            % (summary.get("total"), len(verdicts)))
        for verdict_name in VERDICTS:
            if summary.get(verdict_name) != counts[verdict_name]:
                problems.append("summary.%s=%r but counted %d"
                                % (verdict_name, summary.get(verdict_name),
                                   counts[verdict_name]))
        high_risk = summary.get("high_risk") or []
        if not isinstance(high_risk, list):
            problems.append("summary.high_risk must be a list")
        else:
            unknown = {str(item) for item in high_risk} - seen_assertions
            if unknown:
                problems.append("summary.high_risk has unknown assertion_id: %s"
                                % ", ".join(sorted(unknown)))

    checked_range = result.get("checked_range")
    if not str(checked_range or "").strip():
        warnings.append("checked_range is empty; state the exhaustive range")
    unchecked = result.get("unchecked")
    if unchecked is not None:
        if not isinstance(unchecked, list):
            problems.append("unchecked must be a list")
        else:
            for index, item in enumerate(unchecked):
                if not isinstance(item, dict) or not str(
                        item.get("reason") or "").strip():
                    problems.append("unchecked[%d] missing reason" % index)

    uncertainty = result.get("uncertainty")
    if not isinstance(uncertainty, list):
        problems.append("uncertainty must be a list")
    else:
        for index, item in enumerate(uncertainty):
            if not isinstance(item, dict) or not item.get("reason"):
                warnings.append("uncertainty[%d] missing reason" % index)

    change_log = result.get("change_log")
    if not isinstance(change_log, list) or not change_log:
        problems.append("change_log must be a non-empty list")
    else:
        for index, item in enumerate(change_log):
            if not isinstance(item, dict) or not item.get("action"):
                warnings.append("change_log[%d] missing action" % index)

    support_files = result.get("support_files_used")
    if not isinstance(support_files, list) or not support_files:
        warnings.append("support_files_used missing/empty")
    else:
        joined = " ".join(str(item) for item in support_files)
        for required in ("claim-verification-playbook.md",
                         "check_claim_verification.py"):
            if required not in joined:
                warnings.append("support_files_used does not mention %s"
                                % required)

    for value in walk(result):
        if isinstance(value, str) and PLACEHOLDER.search(value):
            problems.append("placeholder text found: %r" % value[:80])


def self_test_fixture():
    input_data = {
        "case_id": "claim-check-self-test",
        "material_version": "v1",
        "documents": [{
            "doc_id": "d1",
            "paragraphs": [
                {"paragraph_id": "p1",
                 "text": "At 350 C the conversion was 92%."},
                {"paragraph_id": "p2",
                 "text": "At 250 C the conversion was below 50%."},
                {"paragraph_id": "p3",
                 "text": "The authors suggest a possible role of oxygen "
                         "vacancies, but the mechanism was not confirmed."},
                {"paragraph_id": "p4",
                 "text": "The catalyst was stable for 50 h."},
            ],
        }],
        "claims": [
            {"claim_id": "c1", "text": "The conversion was 92% at 350 C."},
            {"claim_id": "c2", "text": "The conversion was above 90% at 250 C."},
            {"claim_id": "c3", "text": "Oxygen vacancies cause the activity."},
            {"claim_id": "c4", "text": "The catalyst kept 80% activity after 200 h."},
        ],
    }
    result = {
        "case_id": "claim-check-self-test",
        "status": "completed",
        "task_family": "reading",
        "material_version": "v1",
        "verdicts": [
            {
                "assertion_id": "a1", "claim_id": "c1",
                "source_claim": "The conversion was 92% at 350 C.",
                "assertion": "The conversion was 92% at 350 C.",
                "claim_type": "numeric", "comparator": "", "negation": False,
                "significance": "", "conditions": ["350 C"],
                "verdict": "supported", "confidence": 0.93,
                "supporting_evidence": [{"doc_id": "d1", "paragraph_id": "p1",
                                         "quote": "At 350 C the conversion was 92%.",
                                         "evidence_level": "primary",
                                         "secondary_source": False}],
                "counter_evidence": [], "limitations": [],
                "reason": "Direct match.", "search_scope": "p1-p4",
                "numeric_check": {"claimed": "92%", "observed": "92%",
                                  "conversion": "none", "unit": "%"},
                "rewrite": "At 350 C the conversion was 92%.",
                "match_quality": "exact", "severity": "none",
                "verification_route": "local_text",
                "retraction_flag": False, "preprint_flag": False,
            },
            {
                "assertion_id": "a2", "claim_id": "c2",
                "source_claim": "The conversion was above 90% at 250 C.",
                "assertion": "The conversion was above 90% at 250 C.",
                "claim_type": "comparison", "comparator": "250 C vs 90%",
                "negation": False, "significance": "", "conditions": ["250 C"],
                "verdict": "contradicted", "confidence": 0.9,
                "supporting_evidence": [],
                "counter_evidence": [{"doc_id": "d1", "paragraph_id": "p2",
                                      "quote": "At 250 C the conversion was below 50%.",
                                      "evidence_level": "primary",
                                      "secondary_source": False}],
                "limitations": [], "reason": "Source states below 50%.",
                "search_scope": "p1-p4",
                "numeric_check": {"claimed": ">90%", "observed": "<50%",
                                  "conversion": "none", "unit": "%"},
                "rewrite": "At 250 C the conversion was below 50%.",
                "match_quality": "mismatch", "severity": "major",
                "verification_route": "local_text",
                "retraction_flag": False, "preprint_flag": False,
            },
            {
                "assertion_id": "a3", "claim_id": "c3",
                "source_claim": "Oxygen vacancies cause the activity.",
                "assertion": "Oxygen vacancies cause the activity.",
                "claim_type": "causal", "comparator": "", "negation": False,
                "significance": "", "conditions": [],
                "verdict": "partially_supported", "confidence": 0.55,
                "supporting_evidence": [{"doc_id": "d1", "paragraph_id": "p3",
                                         "quote": "The authors suggest a possible "
                                                  "role of oxygen vacancies, but the "
                                                  "mechanism was not confirmed.",
                                         "evidence_level": "primary",
                                         "secondary_source": False}],
                "counter_evidence": [],
                "limitations": ["Only a possible role is suggested."],
                "reason": "Association suggested, causation not established.",
                "search_scope": "p1-p4",
                "numeric_check": {"claimed": "", "observed": "",
                                  "conversion": "", "unit": ""},
                "rewrite": "The authors suggest a possible role of oxygen "
                           "vacancies, but the mechanism was not confirmed.",
                "match_quality": "partial", "severity": "major",
                "verification_route": "local_text",
                "retraction_flag": False, "preprint_flag": False,
            },
            {
                "assertion_id": "a4", "claim_id": "c4",
                "source_claim": "The catalyst kept 80% activity after 200 h.",
                "assertion": "The catalyst kept 80% activity after 200 h.",
                "claim_type": "numeric", "comparator": "", "negation": False,
                "significance": "", "conditions": ["200 h"],
                "verdict": "insufficient_evidence", "confidence": 0.8,
                "supporting_evidence": [], "counter_evidence": [],
                "limitations": ["Only 50 h stability was reported."],
                "reason": "No 200 h data in the material.",
                "search_scope": "Searched p1-p4 for 200 h, 80%, stability.",
                "numeric_check": {"claimed": "80% after 200 h",
                                  "observed": "not reported",
                                  "conversion": "n/a", "unit": "%"},
                "rewrite": "The material does not report 200 h stability.",
                "match_quality": "not_found", "severity": "minor",
                "verification_route": "local_text",
                "retraction_flag": False, "preprint_flag": False,
            },
        ],
        "summary": {"total": 4, "supported": 1, "partially_supported": 1,
                    "contradicted": 1, "insufficient_evidence": 1,
                    "high_risk": ["a2", "a3"]},
        "checked_range": "c1-c4 all covered.",
        "unchecked": [],
        "bibliography_issues": [],
        "uncertainty": [{"item": "mechanism", "reason": "not confirmed",
                         "impact": "cannot state causation"}],
        "change_log": [{"step": 1, "action": "checked all claims",
                        "evidence": "p1-p4"}],
        "support_files_used": ["references/claim-verification-playbook.md",
                               "templates/verification-report.json",
                               "scripts/check_claim_verification.py"],
    }
    return input_data, result


def run_self_test():
    input_data, result = self_test_fixture()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        (root / "claims-to-verify.json").write_text(
            json.dumps(input_data, ensure_ascii=False), encoding="utf-8")
        ctx = collect_input(root)
        base_problems, base_warnings, checks = [], [], {
            "quotes": 0, "input_claims": 0, "covered_claims": 0,
            "verdicts": 0, "assertions": 0}
        check_result(result, ctx, base_problems, base_warnings, checks)

    mutations = []

    def mutate(name, expected, function):
        candidate = copy.deepcopy(result)
        function(candidate)
        problems, warnings, check_counts = [], [], {
            "quotes": 0, "input_claims": 0, "covered_claims": 0,
            "verdicts": 0, "assertions": 0}
        check_result(candidate, ctx, problems, warnings, check_counts)
        detected = any(expected.lower() in problem.lower()
                       for problem in problems)
        mutations.append({"name": name, "expected": expected,
                          "detected": detected,
                          "problems": problems[:5]})

    mutate("invalid_verdict", "invalid verdict",
           lambda r: r["verdicts"][0].__setitem__("verdict", "mostly_true"))
    mutate("legacy_verdict", "legacy verdict",
           lambda r: r["verdicts"][1].__setitem__("verdict", "not_found"))
    mutate("missing_coverage", "not covered",
           lambda r: r["verdicts"].pop(3))
    mutate("non_verbatim_quote", "not verbatim",
           lambda r: r["verdicts"][0]["supporting_evidence"][0].__setitem__(
               "quote", "Conversion was 99% at 300 C."))
    mutate("supported_with_counter", "counter_evidence",
           lambda r: r["verdicts"][0].__setitem__("counter_evidence", [
               {"doc_id": "d1", "paragraph_id": "p2",
                "quote": "At 250 C the conversion was below 50%.",
                "evidence_level": "primary", "secondary_source": False}]))
    mutate("summary_mismatch", "summary.supported",
           lambda r: r["summary"].__setitem__("supported", 3))
    mutate("duplicate_assertion", "duplicate assertion_id",
           lambda r: r["verdicts"][1].__setitem__("assertion_id", "a1"))
    mutate("rewrite_number", "rewrite introduces numbers",
           lambda r: r["verdicts"][3].__setitem__(
               "rewrite", "The material does not report 80% at 300 h."))

    report = {
        "ok": not base_problems and all(item["detected"] for item in mutations),
        "self_test": True,
        "valid_result_ok": not base_problems,
        "valid_result_problems": base_problems,
        "valid_result_warnings": base_warnings,
        "mutations": mutations,
        "checks": checks,
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", default=None)
    parser.add_argument("--input", default=None)
    parser.add_argument("--source", action="append", default=[],
                        help="extra extracted text/corpus path")
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        report = run_self_test()
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0 if report["ok"] else 1

    if not args.result:
        parser.error("--result is required unless --self-test is used")
    try:
        result = load_json(args.result)
    except (OSError, json.JSONDecodeError) as exc:
        report = {"ok": False, "problems": ["cannot read result: %s" % exc],
                  "warnings": []}
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 2

    ctx = collect_input(args.input, args.source) if args.input else {
        "claims": [], "paragraphs": {}, "doc_paragraphs": {},
        "paragraph_ids": set(), "doc_ids": set(), "ambiguous": set(),
        "texts": [], "corpus": "", "case_id": None, "input_found": False,
        "json_files": []}
    problems, warnings, checks = [], [], {
        "quotes": 0, "input_claims": 0, "covered_claims": 0,
        "verdicts": 0, "assertions": 0}
    if args.input and not ctx["input_found"]:
        warnings.append("input path not found; coverage/quote checks limited")
    check_result(result, ctx, problems, warnings, checks)
    report = {"ok": not problems, "problems": problems, "warnings": warnings,
              "checks": checks,
              "input": {"path": args.input,
                        "json_files": ctx["json_files"],
                        "claims": len(ctx["claims"]),
                        "paragraphs": len(ctx["paragraph_ids"]),
                        "corpus_chars": len(ctx["corpus"])}}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    if args.json:
        print(text)
    else:
        print(("PASS" if report["ok"] else "FAIL")
              + ": %d problems, %d warnings" % (len(problems), len(warnings)))
        for problem in problems:
            print("  - " + problem)
        for warning in warnings:
            print("  ! " + warning)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
