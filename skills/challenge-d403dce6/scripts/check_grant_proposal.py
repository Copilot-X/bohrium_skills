#!/usr/bin/env python3
"""Validate a grant-proposal result and its consistency matrix.

Usage:
    python3 check_grant_proposal.py --result /app/output/result.json \
        --brief /app/output/proposal.md --root /app/output --json
    python3 check_grant_proposal.py --self-test --json

Checks
  * seven elements E1-E7 exist with statuses; E7 has indicators
  * objectives have tasks/methods/outcomes/indicator and appear linked in the matrix
  * consistency.breaks is empty for completed proposals
  * applicant_evidence separates provided/missing; gaps cover missing items
  * budget/timeline are internally consistent or explicitly to_be_confirmed
  * eligibility risks are not silently ignored
  * artifacts resolve; brief has no placeholders

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ELEMENT_IDS = {"E1", "E2", "E3", "E4", "E5", "E6", "E7"}
STATUSES = {"written", "partial", "missing"}
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|待补充|待填写)\b", re.I)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def validate(result, brief_text, root):
    problems, warnings, checks = [], [], {}
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["result root is not an object"],
                "warnings": [], "checks": {}}
    for key in ("case_id", "proposal", "artifacts", "elements", "objectives",
                "consistency", "applicant_evidence", "gaps"):
        if key not in result:
            problems.append("result.json missing %s" % key)
    elements = result.get("elements") or []
    seen = set()
    for i, item in enumerate(elements):
        if not isinstance(item, dict):
            problems.append("element[%d] is not an object" % i)
            continue
        eid = item.get("id")
        if eid not in ELEMENT_IDS:
            problems.append("element[%d] invalid id: %s" % (i, eid))
            continue
        if eid in seen:
            problems.append("duplicate element id: %s" % eid)
        seen.add(eid)
        if item.get("status") not in STATUSES:
            problems.append("element %s invalid status: %s" % (eid, item.get("status")))
        if eid == "E7" and not item.get("indicators"):
            warnings.append("E7 has no indicators")
    missing_elements = sorted(ELEMENT_IDS - seen)
    if missing_elements:
        problems.append("missing elements: %s" % ", ".join(missing_elements))
    checks["elements"] = len(elements)

    objectives = result.get("objectives") or []
    objective_ids = set()
    for i, item in enumerate(objectives):
        if not isinstance(item, dict):
            problems.append("objective[%d] is not an object" % i)
            continue
        oid = item.get("objective_id")
        if not oid:
            problems.append("objective[%d] missing objective_id" % i)
            continue
        objective_ids.add(str(oid))
        for key in ("text", "tasks", "methods", "outcomes", "indicator"):
            if not item.get(key):
                problems.append("objective %s missing %s" % (oid, key))
    checks["objectives"] = len(objectives)

    consistency = result.get("consistency") or {}
    if not isinstance(consistency, dict):
        problems.append("consistency is not an object")
        consistency = {}
    rows = consistency.get("rows") or consistency.get("matrix") or []
    linked = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append("consistency row[%d] is not an object" % i)
            continue
        oid = str(row.get("objective") or "")
        if oid and oid not in objective_ids:
            problems.append("consistency row references unknown objective %s" % oid)
        if row.get("linked") is True:
            linked.add(oid)
        for key in ("content", "method", "outcome"):
            if not row.get(key) and row.get("linked") is True:
                warnings.append("consistency row %s linked but missing %s" % (oid, key))
    unlinked = sorted(objective_ids - linked)
    if unlinked:
        problems.append("objectives not linked in matrix: %s" % ", ".join(unlinked))
    breaks = consistency.get("breaks") or []
    if breaks and result.get("status") == "completed":
        problems.append("completed proposal has consistency breaks: %d" % len(breaks))
    checks["objectives_linked"] = len(linked)

    evidence = result.get("applicant_evidence") or {}
    if not isinstance(evidence, dict):
        problems.append("applicant_evidence is not an object")
        evidence = {}
    missing_evidence = evidence.get("missing") or []
    if not isinstance(missing_evidence, list):
        warnings.append("applicant_evidence.missing is not a list")
    gaps = result.get("gaps") or []
    gap_items = {str(g.get("item")) for g in gaps if isinstance(g, dict)}
    for item in missing_evidence:
        if str(item) not in gap_items:
            warnings.append("missing applicant evidence not in gaps: %s" % item)
    checks["missing_evidence"] = len(missing_evidence)

    budget = result.get("budget") or {}
    if isinstance(budget, dict) and budget:
        status = budget.get("status")
        total = budget.get("total")
        if status not in ("to_be_confirmed", "provided"):
            warnings.append("budget.status unusual: %s" % status)
        if status == "provided" and (total is None or float(total) <= 0):
            problems.append("budget provided but total is not positive")
        for i, item in enumerate(budget.get("items") or []):
            if isinstance(item, dict) and not item.get("name"):
                warnings.append("budget item[%d] has no name" % i)
    timeline = result.get("timeline") or []
    if not timeline:
        warnings.append("timeline is empty")
    for i, item in enumerate(timeline):
        if isinstance(item, dict) and not item.get("milestone"):
            warnings.append("timeline[%d] missing milestone" % i)

    screen = result.get("eligibility_screen") or {}
    if isinstance(screen, dict) and screen.get("passed") is False:
        warnings.append("eligibility_screen.passed=false; proposal may be ineligible")
    artifacts = result.get("artifacts") or []
    roles = {}
    base = Path(root) if root else Path.cwd()
    for i, item in enumerate(artifacts):
        if not isinstance(item, dict):
            problems.append("artifact[%d] is not an object" % i)
            continue
        role = str(item.get("role") or "").lower()
        rel = item.get("path")
        if not rel:
            problems.append("artifact[%d] missing path" % i)
            continue
        candidate = Path(rel)
        if not candidate.is_absolute():
            candidate = base / rel
        if not candidate.is_file():
            problems.append("artifact missing: %s" % rel)
            continue
        roles[role] = candidate
    for required in ("editable", "compliance"):
        if required not in roles:
            problems.append("artifacts missing role=%s" % required)
    checks["artifact_roles"] = sorted(roles)
    if brief_text and PLACEHOLDER.search(brief_text):
        problems.append("proposal.md contains placeholder text")
    return {"ok": not problems, "problems": problems, "warnings": warnings, "checks": checks}


def self_test():
    return {
        "case_id": "grant-self-test",
        "proposal": {"path": "proposal.md"},
        "elements": [{"id": e, "status": "written"} for e in sorted(ELEMENT_IDS)],
        "objectives": [{"objective_id": "O1", "text": "test", "tasks": ["T1"],
                        "methods": ["M1"], "outcomes": ["D1"], "indicator": "I1"}],
        "consistency": {"rows": [{"objective": "O1", "content": ["T1"], "method": ["M1"],
                                  "outcome": ["D1"], "linked": True}], "breaks": []},
        "applicant_evidence": {"provided": ["materials/p.pdf"],
                               "missing": ["申请人简历"]},
        "gaps": [{"item": "申请人简历", "why_needed": "E6", "owner": "申请人"}],
        "budget": {"status": "to_be_confirmed", "total": None, "items": []},
        "timeline": [{"phase": "Q1", "milestone": "data", "deliverable": "dataset"}],
        "eligibility_screen": {"passed": None, "risks": []},
        "artifacts": [{"role": "editable", "path": "proposal.md"},
                      {"role": "compliance", "path": "compliance-matrix.json"}],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None)
    ap.add_argument("--brief", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "proposal.md").write_text("Grant body.", encoding="utf-8")
            (root / "compliance-matrix.json").write_text("{}", encoding="utf-8")
            report = validate(self_test(), "Grant body.", root)
        print(json.dumps({"ok": report["ok"], "self_test": True,
                          "problems": report["problems"],
                          "warnings": report["warnings"]}, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if not args.result:
        ap.error("--result is required unless --self-test is used")
    try:
        result = load_json(args.result)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "problems": ["cannot read result: %s" % exc]},
                         ensure_ascii=False))
        return 2
    brief_text = None
    if args.brief:
        try:
            brief_text = Path(args.brief).read_text(encoding="utf-8", errors="replace")
        except OSError:
            brief_text = None
    report = validate(result, brief_text, args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
