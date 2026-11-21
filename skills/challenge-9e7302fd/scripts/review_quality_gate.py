#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Executable quality gate for methods/statistics review work files.

Usage:
    python3 review_quality_gate.py --work /app/work --output /app/output
    python3 review_quality_gate.py --work /app/work --output /app/output --json

The script never writes review content.  It checks that the model-authored
work file covers the five required facets, that every finding has a real
locator and a verbatim quote, that the reporting checklist and decision rules
are present, and that the report has the required sections.  Problems are
written to ``work/review-gate.json`` and ``output/review-gate.json``.

Exit code: 0 = no problems (warnings may remain), 1 = problems found,
2 = the work file is missing/unreadable.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:
    import skillcore as S  # type: ignore
except Exception:  # noqa: BLE001
    S = None

FACETS = {
    "design": ("design", "research_design", "study_design", "研究设计"),
    "variables": ("variables", "variable", "variable_definition",
                  "variables_definition", "变量", "变量定义"),
    "statistics": ("statistics", "statistical", "statistical_methods",
                   "stats", "统计", "统计方法"),
    "sample_size": ("sample_size", "sample", "power", "sample_size_power",
                    "样本量", "检验效能", "样本量与检验效能"),
    "reporting": ("reporting", "results_reporting", "report", "结果报告",
                  "报告"),
}

REQUIRED_REPORT_KEYS = ("n", "effect", "ci", "exact_p", "df", "missing_data",
                        "multiplicity")
OPTIONAL_REPORT_KEYS = ("randomization", "blinding", "software", "itt")
REPORT_KEY_ALIASES = {
    "n": ("n", "sample_size", "sample_n"),
    "effect": ("effect", "effect_size", "estimate"),
    "ci": ("ci", "confidence_interval", "95_ci"),
    "exact_p": ("exact_p", "p_value", "p"),
    "df": ("df", "degrees_of_freedom", "dof"),
    "missing_data": ("missing_data", "missing", "attrition", "dropout"),
    "multiplicity": ("multiplicity", "multiple_comparison",
                     "multiple_comparisons", "correction"),
    "randomization": ("randomization", "randomisation", "allocation"),
    "blinding": ("blinding", "masking"),
    "software": ("software", "analysis_software", "package"),
    "itt": ("itt", "intention_to_treat", "intention_to_treat_analysis"),
}
CHECK_ALIASES = {
    "design": ("design", "random", "blind", "control", "allocation",
               "confound", "bias", "研究设计", "随机", "盲法", "对照"),
    "variables": ("variable", "measurement", "coding", "unit", "exposure",
                  "outcome", "covariate", "变量", "测量", "编码", "单位"),
    "statistics": ("normal", "assumption", "test", "paired", "multiple",
                   "multiplicity", "interaction", "repeat", "sensitivity",
                   "正态", "假设", "检验", "配对", "多重", "重复", "敏感性"),
    "sample_size": ("power", "sample", "effect_size", "attrition",
                    "dropout", "allocation_ratio", "样本量", "效能",
                    "效应量", "失访"),
    "reporting": ("p_value", "ci", "df", "exact_p", "table", "figure",
                  "effect", "inconsisten", "p值", "置信区间", "自由度",
                  "表格", "一致性"),
}
SEVERITIES = {"critical", "major", "moderate", "minor", "info",
              "recommendation", "high", "medium", "low"}
TYPES = {"error", "fact_error", "insufficient_evidence", "reporting_gap",
         "suggestion", "compliance", "methodological", "statistical",
         "design", "variable", "sample_size", "results_reporting",
         "review_issue"}
STATUSES = {"open", "resolved", "needs_recheck", "not_applicable", "closed"}
REPORT_SECTIONS = {
    "scope": ("范围", "材料", "scope", "materials"),
    "design": ("研究设计", "设计", "design"),
    "variables": ("变量", "variables"),
    "statistics": ("统计", "statistics", "statistical"),
    "sample_size": ("样本量", "检验效能", "sample size", "power"),
    "reporting": ("结果报告", "报告", "reporting"),
    "evidence": ("证据", "定位", "evidence", "locator"),
    "uncertainty": ("待复核", "不确定", "边界", "uncertain", "limitation"),
    "reproducibility": ("复现", "复跑", "reproduc", "repeat"),
}


def _norm(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s\-/]+", "_", text)
    text = re.sub(r"[^0-9a-z_一-鿿]", "", text)
    return text.strip("_")


def _facet_key(value):
    key = _norm(value)
    if not key:
        return ""
    for canonical, aliases in FACETS.items():
        if key in {_norm(alias) for alias in aliases}:
            return canonical
    for canonical, aliases in FACETS.items():
        if any(_norm(alias) in key for alias in aliases):
            return canonical
    return ""


def _get_key(mapping, aliases):
    if not isinstance(mapping, dict):
        return None, None
    normalized = {_norm(key): key for key in mapping}
    for alias in aliases:
        key = normalized.get(_norm(alias))
        if key is not None:
            return key, mapping.get(key)
    return None, None


def _status_text(value):
    if isinstance(value, dict):
        value = value.get("status") or value.get("value") or ""
    if isinstance(value, bool):
        return "reported" if value else "missing"
    text = _norm(value)
    return {
        "已检查": "checked", "完成": "checked", "未检查": "not_checked",
        "不适用": "not_applicable", "材料不足": "insufficient_material",
        "部分": "partial", "已报告": "reported", "缺失": "missing",
        "未报告": "missing", "不一致": "inconsistent",
    }.get(text, text)


def _materials(work, input_dir=None, documents=None):
    by_path = {}
    payload = documents or (work.get("_materials")
                            if isinstance(work, dict) else None)
    if not payload:
        payload = []
    for doc in payload:
        if not isinstance(doc, dict):
            continue
        path = str(doc.get("path") or "")
        text = str(doc.get("text") or "")
        if path:
            by_path[path] = text
    if input_dir and Path(input_dir).is_dir():
        for path in Path(input_dir).rglob("*"):
            if path.is_file() and path.stat().st_size <= 2_000_000 \
                    and path.suffix.lower() in (".md", ".txt", ".tex",
                                                ".json", ".csv", ".yaml",
                                                ".yml", ".html", ".xml"):
                try:
                    text = path.read_text(encoding="utf-8-sig",
                                          errors="replace")
                except OSError:
                    continue
                by_path.setdefault(path.relative_to(input_dir).as_posix(),
                                   text)
    return by_path


def _quote_ok(quote, source_text):
    quote = (quote or "").strip()
    if not quote:
        return False
    if S is not None:
        try:
            return S.quote_supported(source_text, quote)[0]
        except Exception:  # noqa: BLE001
            pass
    normalized_source = re.sub(r"\s+", " ", source_text or "").strip().lower()
    normalized_quote = re.sub(r"\s+", " ", quote).strip().lower()
    return normalized_quote in normalized_source


def _check_facets(work, problems, warnings):
    rows = work.get("facets_checked") or []
    if not isinstance(rows, list):
        problems.append("facets_checked is not a list")
        rows = []
    seen = {}
    for index, row in enumerate(rows, 1):
        if isinstance(row, str):
            key = _facet_key(row)
            status, items = "", []
        elif isinstance(row, dict):
            key = _facet_key(row.get("facet") or row.get("name")
                             or row.get("id") or row.get("label"))
            status = _status_text(row.get("status"))
            items = row.get("items") or row.get("checks") or []
        else:
            key, status, items = "", "", []
        if not key:
            warnings.append("facets_checked[%d] has an unknown facet name" % index)
            continue
        seen.setdefault(key, []).append(status)
        if not status:
            problems.append("facet %s has no status" % key)
        elif status in ("checked", "complete", "done", "reviewed"):
            if not items:
                problems.append("facet %s is checked but items[] is empty" % key)
        elif status in ("not_checked", "unchecked", "skipped"):
            warnings.append("facet %s is not_checked; record why" % key)
        elif status not in ("insufficient_material", "not_applicable",
                            "partial", "incomplete"):
            warnings.append("facet %s has an unusual status: %s" % (key, status))
    for key in FACETS:
        if key not in seen:
            problems.append("required facet missing from facets_checked: %s" % key)
    for key, statuses in seen.items():
        if len(statuses) > 1:
            warnings.append("facet %s is listed %d times" % (key, len(statuses)))
    return seen


def _check_checks(work, problems, warnings):
    rows = work.get("checks") or []
    if not isinstance(rows, list):
        problems.append("checks is not a list")
        return
    if not rows:
        problems.append("checks is empty; add per-item pass/fail/not_checked rows")
        return
    covered = {key: 0 for key in FACETS}
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            problems.append("checks[%d] is not an object" % index)
            continue
        item = _norm(row.get("item") or row.get("check") or row.get("name"))
        status = _status_text(row.get("status"))
        if not item:
            problems.append("checks[%d] lacks item/check" % index)
        if status not in ("pass", "fail", "not_checked", "not_applicable",
                          "insufficient_material"):
            problems.append("checks[%d] has invalid status: %s" % (index, status))
        if not str(row.get("detail") or row.get("evidence") or "").strip():
            warnings.append("checks[%d] lacks detail/evidence" % index)
        for facet, aliases in CHECK_ALIASES.items():
            if any(alias in item for alias in aliases):
                covered[facet] += 1
    for facet, count in covered.items():
        if count == 0:
            problems.append("no checks[] item covers facet: %s" % facet)


def _check_reporting(work, problems, warnings):
    checklist = work.get("reporting_checklist")
    if not isinstance(checklist, dict) or not checklist:
        problems.append("reporting_checklist is missing or empty")
        return
    allowed = {"reported", "missing", "not_applicable", "inconsistent",
               "not_checked", "partial", "na"}
    for key in REQUIRED_REPORT_KEYS + OPTIONAL_REPORT_KEYS:
        found_key, value = _get_key(checklist, REPORT_KEY_ALIASES[key])
        if found_key is None:
            if key in REQUIRED_REPORT_KEYS:
                problems.append("reporting_checklist lacks required key: %s" % key)
            else:
                warnings.append("reporting_checklist lacks optional key: %s" % key)
            continue
        status = _status_text(value)
        if status not in allowed:
            problems.append("reporting_checklist.%s has invalid status: %s"
                            % (found_key, status or "missing"))
        if status == "not_checked":
            warnings.append("reporting_checklist.%s is not_checked" % found_key)
        if status == "inconsistent":
            warnings.append("reporting_checklist.%s is inconsistent; add a finding"
                            % found_key)


def _check_decision_rules(work, problems):
    rows = work.get("decision_rules") or []
    if not isinstance(rows, list) or len(rows) < 4:
        problems.append("decision_rules needs at least 4 documented rules")
        return
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            problems.append("decision_rules[%d] is not an object" % index)
            continue
        if not str(row.get("rule") or row.get("name") or "").strip():
            problems.append("decision_rules[%d] lacks rule/name" % index)
        if not str(row.get("condition") or row.get("when") or "").strip():
            problems.append("decision_rules[%d] lacks condition" % index)
        if not str(row.get("decision") or row.get("action") or "").strip():
            problems.append("decision_rules[%d] lacks decision/action" % index)


def _check_findings(work, materials, problems, warnings):
    findings = work.get("findings") or []
    if not isinstance(findings, list):
        problems.append("findings is not a list")
        return
    if not findings:
        warnings.append("findings is empty; confirm the manuscript truly has no issue")
    for index, finding in enumerate(findings, 1):
        if not isinstance(finding, dict):
            problems.append("finding %d is not an object" % index)
            continue
        fid = str(finding.get("id") or "F%03d" % index)
        facet = _facet_key(finding.get("facet") or finding.get("dimension"))
        if not facet:
            problems.append("finding %s lacks a known facet" % fid)
        locator = finding.get("locator") or finding.get("location") or {}
        path = ""
        loc = ""
        if isinstance(locator, dict):
            path = str(locator.get("path") or "")
            loc = str(locator.get("locator") or locator.get("line") or "")
        else:
            loc = str(locator)
        if not path:
            problems.append("finding %s lacks locator.path" % fid)
        if not loc:
            problems.append("finding %s lacks locator.locator/line" % fid)
        quote = str(finding.get("original_quote") or finding.get("quote") or "")
        if not quote:
            problems.append("finding %s lacks original_quote" % fid)
        elif path and path in materials and not _quote_ok(quote, materials[path]):
            problems.append("finding %s quote is not verbatim in %s" % (fid, path))
        elif path and path not in materials:
            warnings.append("finding %s source path not found in materials: %s"
                            % (fid, path))
        severity = _norm(finding.get("severity"))
        severity = {"严重": "major", "主要": "major", "中等": "moderate",
                    "次要": "minor", "建议": "recommendation",
                    "信息": "info", "高": "high", "中": "medium",
                    "低": "low"}.get(severity, severity)
        if severity not in SEVERITIES:
            problems.append("finding %s has invalid severity: %s" % (fid, severity))
        issue_type = _norm(finding.get("type") or finding.get("issue_type"))
        if issue_type and issue_type not in TYPES:
            warnings.append("finding %s has unusual type: %s" % (fid, issue_type))
        status = _norm(finding.get("status"))
        if status and status not in STATUSES:
            warnings.append("finding %s has unusual status: %s" % (fid, status))
        if not str(finding.get("fix") or finding.get("recommendation") or "").strip():
            warnings.append("finding %s lacks fix/recommendation" % fid)
        evidence = finding.get("evidence")
        if not evidence:
            problems.append("finding %s lacks evidence" % fid)
        elif path and isinstance(evidence, dict):
            quote2 = str(evidence.get("quote") or "")
            if quote2 and path in materials and not _quote_ok(quote2, materials[path]):
                problems.append("finding %s evidence quote is not verbatim" % fid)
        if facet in ("statistics", "sample_size") and \
                not (finding.get("assumption_checked")
                     or finding.get("assumptions")):
            warnings.append("finding %s should list assumption_checked" % fid)
        if facet in ("statistics", "sample_size") and not (
                finding.get("decision_rule") or finding.get("rule")):
            warnings.append("finding %s should cite the decision rule used" % fid)


def _check_report(report_text, problems):
    if not report_text.strip():
        problems.append("output/review-report.md is missing or empty")
        return
    lowered = report_text.lower()
    for name, words in REPORT_SECTIONS.items():
        if not any(word.lower() in lowered for word in words):
            problems.append("review report lacks the %s section" % name)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work-file", default="work/review-findings.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    work_path = work_dir / Path(args.work_file).name
    if not work_path.is_file():
        payload = {"ok": False, "problems": ["work file not found: %s" % work_path],
                   "warnings": [], "coverage": {}}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    try:
        work = json.loads(work_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        payload = {"ok": False, "problems": ["work file unreadable: %s" % exc],
                   "warnings": [], "coverage": {}}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    problems, warnings = [], []
    documents = None
    materials_path = work_dir / "materials.json"
    if materials_path.is_file():
        try:
            materials_payload = json.loads(
                materials_path.read_text(encoding="utf-8-sig"))
            documents = materials_payload.get("documents")
        except (OSError, json.JSONDecodeError):
            documents = None
    materials = _materials(work, args.input, documents)
    facets = _check_facets(work, problems, warnings)
    _check_checks(work, problems, warnings)
    _check_reporting(work, problems, warnings)
    _check_decision_rules(work, problems)
    _check_findings(work, materials, problems, warnings)
    report = ""
    report_path = output_dir / "review-report.md"
    if report_path.is_file():
        report = report_path.read_text(encoding="utf-8-sig", errors="replace")
    _check_report(report, problems)
    payload = {
        "ok": not problems,
        "case_id": work.get("case_id"),
        "work_file": str(work_path),
        "problems": problems,
        "warnings": warnings,
        "coverage": {
            "facets": sorted(facets),
            "facets_required": sorted(FACETS),
            "findings": len(work.get("findings") or []),
            "checks": len(work.get("checks") or []),
            "decision_rules": len(work.get("decision_rules") or []),
            "reporting_keys": len(work.get("reporting_checklist") or {}),
            "evidence_sources": sorted(materials),
        },
    }
    for base in (work_dir, output_dir):
        try:
            base.mkdir(parents=True, exist_ok=True)
            (base / "review-gate.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        except OSError:
            pass
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
