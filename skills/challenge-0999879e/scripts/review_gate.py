#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-finalize gate for the paper quality-review skill.

The gate is deliberately deterministic: it checks *traceability and coverage*,
not whether the model's judgement is scientifically right.  It writes
``output/self-check.json`` and exits 1 when the work file or report has
fixable gaps, so the agent can iterate before ``run_case.py finalize``.

Checks:
  * six canonical review facets have a status and a reason when incomplete;
  * every finding has locator/original quote/severity/type/fix/false-positive
    check and verbatim evidence that can be found in the input material;
  * citation_support findings have claim-source matrix rows with a legal
    verdict;
  * review_status and not_checked are internally consistent;
  * the report contains the required sections and every finding id;
  * every output_contract key has a work-field or a same-named artifact.

Never fabricates content.  A crash is reported as ``status=error`` and still
writes a valid self-check file and exits 0, so the pipeline can continue.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

FACETS = [
    "fact_consistency",
    "citation_support",
    "method_design",
    "statistical_reporting",
    "argument_chain",
    "academic_compliance",
]
FACET_ALIASES = {
    "factual": "fact_consistency",
    "fact": "fact_consistency",
    "facts": "fact_consistency",
    "fact_check": "fact_consistency",
    "citation": "citation_support",
    "citations": "citation_support",
    "reference_support": "citation_support",
    "methods": "method_design",
    "method": "method_design",
    "methodology": "method_design",
    "statistics": "statistical_reporting",
    "statistical": "statistical_reporting",
    "stats": "statistical_reporting",
    "argument": "argument_chain",
    "logic": "argument_chain",
    "academic_norms": "academic_compliance",
    "compliance": "academic_compliance",
    "norms": "academic_compliance",
}
SEVERITIES = {"critical", "major", "minor", "info"}
TYPE_ALIASES = {
    "error": "error",
    "fact_error": "error",
    "bug": "error",
    "insufficient_evidence": "insufficient_evidence",
    "evidence_gap": "insufficient_evidence",
    "insufficient": "insufficient_evidence",
    "norm_violation": "norm_violation",
    "norm": "norm_violation",
    "compliance": "norm_violation",
    "suggestion": "suggestion",
    "recommendation": "suggestion",
    "advisory": "suggestion",
}
STATUS_ALIASES = {
    "confirmed": "confirmed",
    "open": "confirmed",
    "needs_recheck": "needs_recheck",
    "unverified": "needs_recheck",
    "resolved": "resolved",
}
REVIEW_STATUSES = {"pass", "issues", "needs_recheck", "not_checked"}
FACET_STATUSES = {"checked", "partial", "not_applicable", "not_checked"}
VERDICTS = {"full", "partial", "none", "unverifiable"}


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return default


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


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_space(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def quote_found(source, quote):
    """Verbatim match with whitespace folding and ellipsis-split support."""
    source = norm_space(source)
    quote = norm_space(quote)
    if not quote:
        return False, "empty quote"
    if quote in source:
        return True, "exact"
    parts = [p for p in re.split(r"…|\.\.\.", quote) if norm_space(p)]
    if len(parts) > 1:
        start = 0
        for part in parts:
            part = norm_space(part)
            if len(part) < 3:
                continue
            pos = source.find(part, start)
            if pos < 0:
                return False, "ellipsis part not found: %s" % part[:40]
            start = pos + len(part)
        return True, "ellipsis"
    return False, "quote not found verbatim"


class Sources:
    def __init__(self, input_dir, work_dir, output_dir):
        self.texts = {}
        self.raw = {}
        for base in (Path(input_dir), Path(work_dir), Path(output_dir)):
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file() or path.stat().st_size > 4_000_000:
                    continue
                suffix = path.suffix.lower()
                if suffix not in (".md", ".txt", ".json", ".csv", ".tex", ".yaml",
                                  ".yml", ".bib", ".ris", ".log", ".py", ".html"):
                    continue
                try:
                    text = path.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                rel = path.relative_to(base).as_posix()
                self.texts.setdefault(rel, text)
                self.texts.setdefault(path.name, text)
                self.raw[rel] = text
        materials = load_json(Path(work_dir) / "materials.json", {})
        for doc in materials.get("documents") or []:
            text = str(doc.get("text") or "")
            if not text:
                continue
            for key in (doc.get("path"), Path(str(doc.get("path") or "")).name):
                if key:
                    self.texts.setdefault(str(key), text)

    def find_text(self, path):
        path = str(path or "")
        if not path:
            return None
        if path in self.texts:
            return self.texts[path]
        norm = path.replace("\\", "/")
        if norm in self.texts:
            return self.texts[norm]
        name = Path(norm).name
        if name in self.texts:
            return self.texts[name]
        for key, text in self.texts.items():
            if key.endswith(norm) or norm.endswith(key):
                return text
        return None


def flatten_evidence(value, out):
    if isinstance(value, dict):
        if isinstance(value.get("quote"), str) and value.get("quote").strip():
            out.append({
                "path": value.get("path") or value.get("doc") or value.get("file") or "",
                "locator": value.get("locator") or value.get("paragraph")
                or value.get("page") or "",
                "quote": value.get("quote", ""),
            })
        for item in value.values():
            flatten_evidence(item, out)
    elif isinstance(value, list):
        for item in value:
            flatten_evidence(item, out)


def canonical_facet(value):
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in FACETS:
        return key
    return FACET_ALIASES.get(key, key)


def canonical_severity(value):
    key = str(value or "").strip().lower()
    aliases = {"high": "major", "medium": "minor", "low": "info",
               "blocker": "critical", "suggestion": "info"}
    key = aliases.get(key, key)
    return key


def canonical_type(value):
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return TYPE_ALIASES.get(key, key)


def canonical_status(value):
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return STATUS_ALIASES.get(key, key)


def locator_parts(finding):
    loc = finding.get("locator")
    if isinstance(loc, dict):
        path = loc.get("path") or loc.get("doc") or loc.get("file") or ""
        where = loc.get("locator") or loc.get("paragraph") or loc.get("page") \
            or loc.get("section") or ""
        return str(path), str(where)
    if isinstance(loc, str):
        # Accept "manuscript.md P2" or just "P2".
        match = re.match(r"([A-Za-z0-9_.\-/]+\.(?:md|txt|pdf|docx|tex|json|csv))\s+(.+)", loc)
        if match:
            return match.group(1), match.group(2)
        return "", loc
    return "", ""


def check_findings(findings, sources):
    problems, warnings = [], []
    seen = set()
    for index, finding in enumerate(findings, 1):
        if not isinstance(finding, dict):
            problems.append("finding %d is not an object" % index)
            continue
        label = str(finding.get("id") or "finding %d" % index)
        fid = str(finding.get("id") or "").strip()
        if not fid:
            problems.append("finding %d lacks id" % index)
        elif fid in seen:
            problems.append("%s: duplicate finding id" % fid)
        seen.add(fid)
        facet = canonical_facet(finding.get("facet"))
        if facet not in FACETS:
            problems.append("%s: unknown facet %r" % (label, finding.get("facet")))
        path, where = locator_parts(finding)
        if not (path or where):
            problems.append("%s: missing locator" % label)
        quote = str(finding.get("original_quote") or finding.get("quote") or "").strip()
        if len(quote) < 4:
            problems.append("%s: missing/short original_quote" % label)
        severity = canonical_severity(finding.get("severity"))
        if severity not in SEVERITIES:
            problems.append("%s: invalid severity %r" % (label, finding.get("severity")))
        ftype = canonical_type(finding.get("type") or finding.get("issue_type"))
        if ftype not in {"error", "insufficient_evidence", "norm_violation", "suggestion"}:
            problems.append("%s: invalid type %r" % (label, finding.get("type")))
        status = canonical_status(finding.get("status") or "confirmed")
        if status not in {"confirmed", "needs_recheck", "resolved"}:
            problems.append("%s: invalid status %r" % (label, finding.get("status")))
        fix = str(finding.get("fix") or finding.get("recommendation") or "").strip()
        if len(fix) < 8:
            problems.append("%s: missing actionable fix/recommendation" % label)
        fp = str(finding.get("false_positive_check") or "").strip()
        if len(fp) < 5:
            problems.append("%s: missing false_positive_check" % label)
        evidence = []
        flatten_evidence(finding.get("evidence"), evidence)
        if not evidence:
            problems.append("%s: no evidence with path+quote" % label)
        if quote:
            source = sources.find_text(path) if path else None
            if source is not None:
                ok, why = quote_found(source, quote)
                if not ok:
                    problems.append("%s: original_quote not verbatim in %s (%s)"
                                    % (label, path, why))
            else:
                # Try any source only when the locator carries no path.
                if not path:
                    found = any(quote_found(text, quote)[0] for text in sources.texts.values())
                    if not found:
                        problems.append("%s: original_quote not found in any input" % label)
        for ev_index, item in enumerate(evidence, 1):
            ev_path = str(item.get("path") or "").strip()
            ev_quote = str(item.get("quote") or "").strip()
            if not ev_path:
                problems.append("%s: evidence %d lacks path" % (label, ev_index))
                continue
            source = sources.find_text(ev_path)
            if source is None:
                problems.append("%s: evidence %d path not found: %s"
                                % (label, ev_index, ev_path))
                continue
            if not ev_quote:
                problems.append("%s: evidence %d has empty quote" % (label, ev_index))
                continue
            ok, why = quote_found(source, ev_quote)
            if not ok:
                problems.append("%s: evidence %d quote not verbatim (%s)"
                                % (label, ev_index, why))
    return problems, warnings


def check_facets(work):
    problems, warnings = [], []
    facets = work.get("facets_checked")
    if not isinstance(facets, list) or not facets:
        return ["facets_checked is missing or empty"], warnings
    seen = {}
    for index, row in enumerate(facets, 1):
        if not isinstance(row, dict):
            problems.append("facets_checked[%d] is not an object" % index)
            continue
        facet = canonical_facet(row.get("facet"))
        if facet not in FACETS:
            problems.append("facets_checked[%d]: unknown facet %r"
                            % (index, row.get("facet")))
            continue
        if facet in seen:
            warnings.append("facet %s listed more than once" % facet)
        seen[facet] = row
        status = str(row.get("status") or "").strip().lower()
        if status not in FACET_STATUSES:
            problems.append("%s: invalid facet status %r" % (facet, row.get("status")))
        if status in {"partial", "not_checked", "not_applicable"}:
            reason = str(row.get("reason") or "").strip()
            if not reason and not row.get("items"):
                problems.append("%s: %s without reason/items" % (facet, status))
    missing = [f for f in FACETS if f not in seen]
    if missing:
        problems.append("facets_checked missing: %s" % ", ".join(missing))
    return problems, warnings


def check_claim_matrix(work):
    problems, warnings = [], []
    matrix = work.get("claim_source_matrix")
    findings = work.get("findings") or []
    citation_findings = [f for f in findings if isinstance(f, dict)
                         and canonical_facet(f.get("facet")) == "citation_support"]
    if citation_findings and not matrix:
        problems.append("citation_support findings exist but claim_source_matrix is empty")
    if matrix is None:
        matrix = []
    if not isinstance(matrix, list):
        return ["claim_source_matrix is not a list"], warnings
    for index, row in enumerate(matrix, 1):
        if not isinstance(row, dict):
            problems.append("claim_source_matrix[%d] is not an object" % index)
            continue
        for field in ("claim_id", "claim", "claim_locator", "source",
                      "source_statement", "source_locator"):
            if not str(row.get(field) or "").strip():
                problems.append("claim_source_matrix[%d] lacks %s" % (index, field))
        verdict = str(row.get("verdict") or "").strip().lower()
        if verdict not in VERDICTS:
            problems.append("claim_source_matrix[%d]: invalid verdict %r"
                            % (index, row.get("verdict")))
    return problems, warnings


def check_status(work):
    problems = []
    status = str(work.get("review_status") or "").strip().lower()
    if status not in REVIEW_STATUSES:
        problems.append("review_status must be one of %s, got %r"
                        % ("/".join(sorted(REVIEW_STATUSES)), work.get("review_status")))
    findings = [f for f in (work.get("findings") or []) if isinstance(f, dict)]
    confirmed = [f for f in findings
                 if canonical_status(f.get("status") or "confirmed") == "confirmed"]
    recheck = [f for f in findings
               if canonical_status(f.get("status")) == "needs_recheck"]
    not_checked = work.get("not_checked") or []
    if status == "issues" and not confirmed:
        problems.append("review_status=issues but there is no confirmed finding")
    if status == "pass" and confirmed:
        problems.append("review_status=pass but confirmed findings exist")
    if status == "needs_recheck" and not recheck and not (work.get("uncertainties") or []):
        problems.append("review_status=needs_recheck but no needs_recheck item/uncertainty")
    if status == "not_checked" and not not_checked:
        problems.append("review_status=not_checked but not_checked list is empty")
    return problems


REPORT_PATTERNS = {
    "scope/meta": r"(审查元信息|审查范围|scope|metadata|case_id)",
    "coverage/method": r"(覆盖|方法与|六面|coverage|facet|method)",
    "findings": r"(问题清单|逐条问题|findings|issues)",
    "citation matrix": r"(引用.{0,4}主张|claim[- ]source|claim_source|引用对照|引用支持)",
    "not checked/uncertainty": r"(待复核|未检查|不确定性|not_checked|uncertaint)",
    "limitations": r"(限制|边界|limitation|boundary)",
}


def check_report(report_path, work):
    problems, warnings = [], []
    text = read_text(report_path)
    if not text.strip():
        return ["review report is missing or empty"], warnings
    if len(text) < 400:
        warnings.append("review report is shorter than 400 characters")
    for name, pattern in REPORT_PATTERNS.items():
        if not re.search(pattern, text, re.I):
            problems.append("report lacks section: %s" % name)
    for finding in work.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        fid = str(finding.get("id") or "")
        if fid and fid not in text:
            problems.append("report does not mention finding id %s" % fid)
        quote = norm_space(finding.get("original_quote") or finding.get("quote") or "")
        if quote:
            head = quote[:24]
            if head and head not in norm_space(text):
                warnings.append("report may not quote finding %s verbatim" % fid)
    return problems, warnings


CANONICAL_CONTRACT_ALIASES = {
    "review_report": "deliverable",
    "review-report": "deliverable",
    "findings": "findings",
    "evidence_map": "evidence_map",
    "evidence-map": "evidence_map",
    "status": "review_status",
    "review_status": "review_status",
}


def check_contract(case, work, output):
    problems, warnings = [], []
    contract = case.get("output_contract")
    if not isinstance(contract, dict):
        return problems, warnings
    work_keys = set(work)
    canonical_files = {
        "review_report": [Path(output) / "review-report.md"],
        "review-report": [Path(output) / "review-report.md"],
        "findings": [Path(output) / "findings.json"],
        "evidence_map": [Path(output) / "evidence-map.json"],
        "evidence-map": [Path(output) / "evidence-map.json"],
    }
    for key in contract:
        candidates = [Path(output) / "results" / (key + ".json"),
                      Path(output) / "results" / key,
                      Path(output) / (key + ".json"),
                      Path(output) / key]
        present = key in work_keys or any(p.exists() for p in candidates)
        alias = CANONICAL_CONTRACT_ALIASES.get(key)
        if not present and alias == "deliverable":
            present = (Path(output) / "review-report.md").is_file()
        if not present and alias == "evidence_map":
            present = bool(work.get("claim_source_matrix")) or any(
                p.exists() for p in canonical_files.get(key, []))
        if not present and alias == "review_status":
            present = bool(work.get("review_status"))
        if not present and alias == "findings":
            present = any(p.exists() for p in canonical_files.get(key, []))
        if not present:
            warnings.append("output_contract key %r has no work field or artifact yet" % key)
    return problems, warnings


def evaluate(input_dir, work_dir, output_dir):
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    output_dir = Path(output_dir)
    case = load_json(input_dir / "case.json", {})
    work = load_json(work_dir / "review-findings.json", {})
    if not isinstance(work, dict) or not work:
        return {
            "case_id": case.get("case_id") or "CASE",
            "status": "fail",
            "problems": ["work/review-findings.json is missing or empty"],
            "warnings": [],
            "coverage": {},
        }
    sources = Sources(input_dir, work_dir, output_dir)
    problems, warnings = [], []
    case_id = str(case.get("case_id") or "")
    if case_id and str(work.get("case_id") or "") not in (case_id, ""):
        problems.append("work case_id %r != input case_id %r"
                        % (work.get("case_id"), case_id))
    for field in ("findings", "facets_checked", "review_status", "evidence"):
        if field not in work or not work.get(field):
            problems.append("required field missing/empty: %s" % field)
    findings = [f for f in (work.get("findings") or []) if isinstance(f, dict)]
    if not findings and str(work.get("review_status")) == "pass":
        warnings.append("no findings and review_status=pass: ensure the pass is evidence-backed")
    f_problems, f_warnings = check_findings(work.get("findings") or [], sources)
    problems.extend(f_problems)
    warnings.extend(f_warnings)
    facet_problems, facet_warnings = check_facets(work)
    problems.extend(facet_problems)
    warnings.extend(facet_warnings)
    matrix_problems, matrix_warnings = check_claim_matrix(work)
    problems.extend(matrix_problems)
    warnings.extend(matrix_warnings)
    problems.extend(check_status(work))
    report_problems, report_warnings = check_report(output_dir / "review-report.md", work)
    problems.extend(report_problems)
    warnings.extend(report_warnings)
    contract_problems, contract_warnings = check_contract(case, work, output_dir)
    problems.extend(contract_problems)
    warnings.extend(contract_warnings)
    # Evidence coverage: every finding should be represented in the global evidence list.
    global_quotes = set()
    flat = []
    flatten_evidence(work.get("evidence"), flat)
    for item in flat:
        global_quotes.add(norm_space(item.get("quote")))
    for finding in findings:
        quote = norm_space(finding.get("original_quote") or finding.get("quote"))
        if quote and quote not in global_quotes:
            warnings.append("finding %s original_quote is not in top-level evidence[]"
                            % finding.get("id"))
    # Rule scan traceability: the scan is optional for robustness, but when it
    # is absent or unreadable the model must not imply that rule coverage ran.
    rule_payload = load_json(work_dir / "rule-review.json", {})
    rule_candidates = 0
    if not (work_dir / "rule-review.json").is_file():
        warnings.append("work/rule-review.json missing: run quality_review.py "
                        "and verify its candidates, or state why the scan was skipped")
    elif not isinstance(rule_payload, dict) or not rule_payload:
        warnings.append("work/rule-review.json is unreadable")
    else:
        rule_candidates = len(rule_payload.get("findings") or [])
        if rule_payload.get("generated_by") != "scripts/quality_review.py":
            warnings.append("work/rule-review.json was not produced by the packaged "
                            "quality_review.py")
        if rule_payload.get("engine_status") in ("error", "input_error"):
            warnings.append("rule scan status is %r: candidates are incomplete"
                            % rule_payload.get("engine_status"))
    meta = work.get("rule_review")
    if isinstance(meta, dict) and meta.get("merged"):
        auto = [f for f in findings if isinstance(f, dict) and f.get("rule_id")]
        confirmed_auto = [f for f in auto if canonical_status(
            f.get("status") or "confirmed") == "confirmed"]
        if confirmed_auto:
            warnings.append("%d rule-derived findings are marked confirmed; "
                            "ensure each was checked against the manuscript"
                            % len(confirmed_auto))
    coverage = {
        "facets": len(work.get("facets_checked") or []),
        "findings": len(findings),
        "confirmed": sum(1 for f in findings
                         if canonical_status(f.get("status") or "confirmed") == "confirmed"),
        "needs_recheck": sum(1 for f in findings
                             if canonical_status(f.get("status")) == "needs_recheck"),
        "evidence": len(flat),
        "claim_source_rows": len(work.get("claim_source_matrix") or []),
        "rule_candidates": rule_candidates,
        "report_bytes": (output_dir / "review-report.md").stat().st_size
        if (output_dir / "review-report.md").is_file() else 0,
    }
    return {
        "case_id": case_id or str(work.get("case_id") or "CASE"),
        "status": "pass" if not problems else "fail",
        "problems": problems,
        "warnings": warnings,
        "coverage": coverage,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="quality-review pre-finalize gate")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--apply", action="store_true",
                        help="downgrade an existing result.json to partial when problems remain")
    args = parser.parse_args(argv)
    output = Path(args.output)
    try:
        payload = evaluate(args.input, args.work, args.output)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "case_id": "CASE",
            "status": "error",
            "problems": ["gate crashed: %s" % exc],
            "warnings": [],
            "traceback": traceback.format_exc()[-4000:],
            "coverage": {},
        }
    payload["generated_at"] = now_iso()
    write_json(output / "self-check.json", payload)
    applied = None
    if args.apply and payload.get("problems"):
        result_path = output / "result.json"
        existing = load_json(result_path, None)
        if isinstance(existing, dict):
            status = existing.get("status")
            if status not in ("input_error", "partial"):
                existing["status"] = "partial"
                existing["task_status"] = "partial"
                uncertainties = existing.get("uncertainties")
                if not isinstance(uncertainties, list):
                    uncertainties = []
                message = "pre-finalize gate problems remain: %s" % "; ".join(
                    str(x) for x in (payload.get("problems") or [])[:8])
                if not any(isinstance(item, dict) and item.get("item") == "self_check"
                           for item in uncertainties):
                    uncertainties.append({
                        "item": "self_check",
                        "reason": message,
                        "impact": "the delivered result was downgraded to partial",
                        "action": "fix the work file/report and rerun finalize",
                    })
                existing["uncertainties"] = uncertainties
                existing["uncertainty"] = uncertainties
                write_json(result_path, existing)
                applied = {"status": "partial", "result": str(result_path)}
    summary = {
        "status": payload.get("status"),
        "problems": len(payload.get("problems") or []),
        "warnings": len(payload.get("warnings") or []),
        "coverage": payload.get("coverage"),
        "self_check": str(output / "self-check.json"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if payload.get("problems"):
        for problem in payload["problems"][:30]:
            print("PROBLEM: %s" % problem, file=sys.stderr)
    if payload.get("status") == "fail":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
