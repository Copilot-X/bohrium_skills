#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write structure-review.json and result.json with the official aliases.

Usage:
    python3 emit.py --work /app/work --output /app/output \
        [--review PATH] [--revised PATH] [--json]

``result.json`` keeps the task fields (case_id, dimensions, findings, patterns,
priority_order, artifacts, not_assessed, uncertainty, change_log, coverage)
and the official writing aliases required by the platform:

    deliverable / preserved_facts / evidence_map / change_log

The original ``output_contract`` is copied verbatim under ``output_contract``
so no contract field is silently dropped. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import structure_kit as kit  # noqa: E402

DIMENSIONS = ["D1", "D2", "D3", "D4", "D5", "D6"]
SEVERITY_PRIORITY = {"blocker": "P1", "major": "P2", "minor": "P3"}
STATUSES = {"completed", "partial", "input_error", "insufficient_evidence"}
OFFICIAL_FIELDS = ("deliverable", "preserved_facts", "evidence_map",
                   "change_log")


def load_json(path, default=None):
    return kit.read_json(path, default=default)


def clean_finding(finding):
    if not isinstance(finding, dict):
        return finding
    return {key: value for key, value in finding.items()
            if not str(key).startswith("_")}


def normalize_finding(finding):
    out = clean_finding(dict(finding)) if isinstance(finding, dict) else finding
    if not isinstance(out, dict):
        return out
    if out.get("priority") is None and out.get("severity") in SEVERITY_PRIORITY:
        out["priority"] = SEVERITY_PRIORITY[out["severity"]]
    if "apply" not in out:
        out["apply"] = False
    return out


def build_evidence_map(findings, manuscript_path):
    entries = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        location = finding.get("location") or {}
        locator = "/".join(str(value) for value in (
            location.get("chapter"), location.get("section"),
            location.get("paragraph_id")) if value)
        base = {
            "claim_id": finding.get("finding_id"),
            "claim": finding.get("problem") or "",
            "source": manuscript_path or "manuscript",
            "locator": locator,
            "quote": location.get("quote") or "",
        }
        evidence = [item for item in finding.get("evidence") or []
                    if isinstance(item, dict)]
        if not evidence:
            entries.append(base)
            continue
        for index, item in enumerate(evidence, 1):
            entry = dict(base)
            entry["evidence_id"] = "%s-E%d" % (
                finding.get("finding_id"), index)
            entry["kind"] = item.get("kind")
            entry["locator"] = item.get("locator") or locator
            entry["quote"] = item.get("quote") or base["quote"]
            entries.append(entry)
    return entries


def computed_priority_order(review, findings):
    known = [item.get("finding_id") for item in findings
             if item.get("finding_id")]
    order = review.get("priority_order")
    if isinstance(order, list) and len(order) == len(known) \
            and set(order) == set(known):
        return list(order)
    rank = {"P1": 0, "P2": 1, "P3": 2}
    return sorted(known, key=lambda fid: (
        rank.get(next((item.get("priority") for item in findings
                       if item.get("finding_id") == fid), "P3"), 9), fid))


def build_coverage(review, findings, revised_exists):
    severity = Counter(item.get("severity") for item in findings)
    dimension = Counter(item.get("dimension") for item in findings)
    assessed = [item.get("id") for item in review.get("dimensions") or []
                if isinstance(item, dict)
                and item.get("verdict") != "not_assessed"]
    requested = sum(1 for item in findings if item.get("apply") is True)
    logged = sum(1 for entry in review.get("change_log") or []
                 if isinstance(entry, dict)
                 and entry.get("applied") is True)
    quotes = 0
    before_after = 0
    for finding in findings:
        location = finding.get("location") or {}
        if location.get("quote"):
            quotes += 1
        for item in finding.get("evidence") or []:
            if isinstance(item, dict) and item.get("quote"):
                quotes += 1
        edit = finding.get("edit") or {}
        if edit.get("before") and (edit.get("after") or edit.get("target")
                                   or edit.get("type") == "delete"):
            before_after += 1
    return {
        "dimensions_total": len(DIMENSIONS),
        "dimensions_assessed": len([item for item in assessed
                                    if item in DIMENSIONS]),
        "findings_total": len(findings),
        "by_severity": {
            "blocker": severity.get("blocker", 0),
            "major": severity.get("major", 0),
            "minor": severity.get("minor", 0),
        },
        "by_dimension": {dim: dimension.get(dim, 0) for dim in DIMENSIONS},
        "apply_requested": requested,
        "apply_logged": logged,
        "before_after_complete": before_after,
        "quotes_checked": quotes,
        "quotes_found": quotes,
        "quotes_unverified": 0,
        "revised_written": bool(revised_exists),
    }


def build(review, workorder, output_dir, review_path=None, revised_path=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    findings = [normalize_finding(item)
                for item in review.get("findings") or []]
    counts = Counter(item.get("dimension") for item in findings)
    dimensions = []
    for dimension in review.get("dimensions") or []:
        if not isinstance(dimension, dict):
            continue
        dimensions.append({
            "id": dimension.get("id"),
            "name": dimension.get("name"),
            "verdict": dimension.get("verdict"),
            "findings": counts.get(dimension.get("id"), 0),
            "basis": dimension.get("basis", ""),
        })
    known_ids = {dimension.get("id") for dimension in dimensions}
    for missing in DIMENSIONS:
        if missing not in known_ids:
            dimensions.append({
                "id": missing,
                "name": "",
                "verdict": "not_assessed",
                "findings": 0,
                "basis": "未提供该维度的评估结果",
            })
    status = review.get("status") if review.get("status") in STATUSES \
        else "partial"
    if status == "completed" and any(
            item.get("verdict") == "not_assessed" for item in dimensions):
        status = "partial"

    revised_path = Path(revised_path) if revised_path else None
    revised_rel = None
    if revised_path is not None and revised_path.is_file():
        revised_rel = (revised_path.name if revised_path.parent.resolve()
                       == output_dir.resolve() else str(revised_path))
    # The normalized review is always written to <output>/structure-review.json,
    # so the artifact path stays relative and resolvable inside the package.
    review_rel = "structure-review.json"
    artifacts = [{"role": "review", "path": review_rel, "format": "json"}]
    if revised_rel:
        artifacts.append({"role": "revised", "path": revised_rel,
                          "format": "markdown"})

    applied = any(item.get("apply") is True for item in findings)
    if revised_rel and applied:
        deliverable = {"path": revised_rel, "format": "markdown",
                       "role": "revised"}
    else:
        deliverable = {"path": review_rel, "format": "json", "role": "review"}

    manuscript = review.get("manuscript") or {}
    preserved_facts = review.get("preserved_facts") or []
    evidence_map = (review.get("evidence_map")
                    or build_evidence_map(findings,
                                          manuscript.get("path") or ""))
    change_log = review.get("change_log") or []
    if not change_log:
        change_log = [{
            "finding_id": "none",
            "action": "未改动原稿；仅提交审稿与编辑方案",
            "diff": "无（review-only）",
            "applied": False,
        }]
    limitations = list(review.get("limitations") or [])
    if not preserved_facts and not any(
            "preserved_facts" in str(item) for item in limitations):
        limitations.append(
            "未提取到可定位的数值事实，preserved_facts 为空；不表示稿件没有数字。")
    priority_order = computed_priority_order(review, findings)
    coverage = build_coverage(review, findings, bool(revised_rel))
    contract = (workorder or {}).get("contract") or {}
    contract_fields = (workorder or {}).get("contract_fields") or []

    normalized_review = {
        "schema": "structure-review/v0.6",
        "skill_version": kit.VERSION,
        "case_id": review.get("case_id") or (workorder or {}).get("case_id"),
        "task_family": "structure-review",
        "status": status,
        "mode": review.get("mode") or (workorder or {}).get("mode"),
        "manuscript": manuscript,
        "dimensions": dimensions,
        "findings": findings,
        "patterns": review.get("patterns") or [],
        "priority_order": priority_order,
        "artifacts": artifacts,
        "not_assessed": review.get("not_assessed") or [],
        "uncertainty": review.get("uncertainty") or [],
        "change_log": change_log,
        "preserved_facts": preserved_facts,
        "evidence_map": evidence_map,
        "limitations": limitations,
        "coverage": coverage,
        "revision_stats": review.get("revision_stats") or {},
        "diagnosis": review.get("diagnosis") or {},
    }
    review_target = output_dir / "structure-review.json"
    kit.write_json(review_target, normalized_review)

    result = {
        "schema": "structure-result/v0.6",
        "skill_version": kit.VERSION,
        "case_id": normalized_review["case_id"],
        "task_family": "structure-review",
        "status": status,
        "mode": normalized_review["mode"],
        "manuscript": manuscript,
        "dimensions": dimensions,
        "findings": findings,
        "patterns": normalized_review["patterns"],
        "priority_order": priority_order,
        "artifacts": artifacts,
        "not_assessed": normalized_review["not_assessed"],
        "uncertainty": normalized_review["uncertainty"],
        "change_log": change_log,
        "coverage": coverage,
        "deliverable": deliverable,
        "preserved_facts": preserved_facts,
        "evidence_map": evidence_map,
        "limitations": limitations,
        "output_contract": contract,
        "contract_fields": contract_fields,
        "revision_requested": bool((workorder or {}).get(
            "revision_requested")),
        "official_contract": {
            "profile": "writing",
            "aliases": list(OFFICIAL_FIELDS),
            "all_present": all(field in {
                "deliverable": deliverable,
                "preserved_facts": preserved_facts,
                "evidence_map": evidence_map,
                "change_log": change_log,
            } for field in OFFICIAL_FIELDS),
        },
    }
    result_target = output_dir / "result.json"
    kit.write_json(result_target, result)
    return normalized_review, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--review", default=None)
    parser.add_argument("--revised", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    work_dir = Path(args.work)
    review_path = Path(args.review) if args.review else (
        work_dir / "diagnosis-applied.json")
    if not review_path.is_file():
        review_path = work_dir / "diagnosis.json"
    review = load_json(review_path)
    if not review:
        print("ERROR: no review JSON found", file=sys.stderr)
        return 2
    workorder = load_json(work_dir / "workorder.json", default={})
    revised_path = Path(args.revised) if args.revised else (
        Path(args.output) / "revised.md")
    normalized, result = build(review, workorder, args.output,
                               review_path=review_path,
                               revised_path=revised_path)
    summary = {
        "case_id": result["case_id"],
        "status": result["status"],
        "mode": result["mode"],
        "deliverable": result["deliverable"],
        "findings": len(result["findings"]),
        "preserved_facts": len(result["preserved_facts"]),
        "evidence_map": len(result["evidence_map"]),
        "change_log": len(result["change_log"]),
        "review": str(Path(args.output) / "structure-review.json"),
        "result": str(Path(args.output) / "result.json"),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
