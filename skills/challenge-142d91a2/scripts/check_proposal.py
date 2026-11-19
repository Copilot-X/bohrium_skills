#!/usr/bin/env python3
"""Validate a research proposal / thesis proposal result.

Usage:
    python3 check_proposal.py --result /app/output/result.json \
        --brief /app/output/proposal.md --root /app/output --json
    python3 check_proposal.py --self-test --json

Checks
  * question has PICO/scope and falsifiable hypotheses
  * objectives have indicators/criteria; design modules match objectives
  * design has materials/groups/measurements/statistics/sample-size basis
  * plan has milestones/deliverables and at least one critical path
  * risks have trigger/impact/Plan B; ethics status is explicit
  * fact_ledger separates confirmed/user_stated/to_supplement/unusable
  * one-minute retelling and red-team answers are present
  * artifacts resolve; brief has no placeholders

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import json
import re
import sys
from pathlib import Path

FACT_STATUS = {"confirmed", "user_stated", "to_supplement", "unusable"}
ETHICS_STATUS = {"not_applicable", "to_be_obtained", "provided"}
IMPACTS = {"high", "medium", "low"}
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|待补充|待填写)\b", re.I)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def validate(result, brief_text, root):
    problems, warnings, checks = [], [], {}
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["result root is not an object"],
                "warnings": [], "checks": {}}
    for key in ("case_id", "question", "objectives", "design", "plan", "risks",
                "ethics", "fact_ledger", "one_minute_retelling", "red_team_answers",
                "artifacts"):
        if key not in result:
            problems.append("result.json missing %s" % key)

    question = result.get("question") or {}
    if not isinstance(question, dict):
        problems.append("question is not an object")
    else:
        if not question.get("question"):
            problems.append("question.question missing")
        pico = question.get("pico") or {}
        for key in ("population", "intervention", "comparison", "outcome"):
            if not pico.get(key):
                warnings.append("question.pico missing %s" % key)
        scope = question.get("scope") or {}
        if not scope.get("included"):
            warnings.append("question.scope.included missing")
        hypotheses = question.get("hypotheses") or []
        if not hypotheses:
            problems.append("no hypotheses[]")
        for i, hyp in enumerate(hypotheses):
            if not isinstance(hyp, dict):
                problems.append("hypothesis[%d] is not an object" % i)
                continue
            if not hyp.get("statement"):
                problems.append("hypothesis[%d] missing statement" % i)
            if not hyp.get("falsifiable_by"):
                problems.append("hypothesis[%d] missing falsifiable_by" % i)
    checks["hypotheses"] = len(question.get("hypotheses") or []) if isinstance(question, dict) else 0

    objectives = result.get("objectives") or []
    objective_ids = set()
    modules = set()
    for i, item in enumerate(objectives):
        if not isinstance(item, dict):
            problems.append("objective[%d] is not an object" % i)
            continue
        oid = item.get("objective_id")
        if not oid:
            problems.append("objective[%d] missing objective_id" % i)
        else:
            objective_ids.add(str(oid))
        for key in ("text", "module", "indicator", "criterion"):
            if not item.get(key):
                problems.append("objective %s missing %s" % (oid, key))
        if item.get("module"):
            modules.add(str(item["module"]))
    checks["objectives"] = len(objectives)

    design = result.get("design") or []
    design_modules = set()
    for i, item in enumerate(design):
        if not isinstance(item, dict):
            problems.append("design[%d] is not an object" % i)
            continue
        module = item.get("module")
        if not module:
            problems.append("design[%d] missing module" % i)
        else:
            design_modules.add(str(module))
        for key in ("materials", "groups", "measurements", "statistics",
                    "criterion", "sample_size_basis"):
            if not item.get(key):
                problems.append("design %s missing %s" % (module, key))
    missing_design = sorted(modules - design_modules)
    if missing_design:
        problems.append("objectives without design module: %s" % ", ".join(missing_design))
    checks["design_modules"] = len(design_modules)

    plan = result.get("plan") or []
    if not plan:
        problems.append("plan[] is empty")
    critical = 0
    for i, item in enumerate(plan):
        if not isinstance(item, dict):
            problems.append("plan[%d] is not an object" % i)
            continue
        for key in ("phase", "period", "tasks", "milestone", "deliverable"):
            if not item.get(key):
                problems.append("plan[%d] missing %s" % (i, key))
        if item.get("critical_path"):
            critical += 1
    if plan and critical == 0:
        warnings.append("plan has no critical_path item")
    checks["plan_phases"] = len(plan)

    risks = result.get("risks") or []
    for i, item in enumerate(risks):
        if not isinstance(item, dict):
            problems.append("risk[%d] is not an object" % i)
            continue
        for key in ("risk", "trigger", "plan_b"):
            if not item.get(key):
                problems.append("risk[%d] missing %s" % (i, key))
        if item.get("impact") not in IMPACTS:
            warnings.append("risk[%d] impact unusual: %s" % (i, item.get("impact")))
    if not risks:
        warnings.append("no risks listed")
    checks["risks"] = len(risks)

    ethics = result.get("ethics") or {}
    if not isinstance(ethics, dict):
        problems.append("ethics is not an object")
    else:
        if ethics.get("status") not in ETHICS_STATUS:
            problems.append("ethics.status invalid: %s" % ethics.get("status"))
        if ethics.get("required") and not ethics.get("detail"):
            problems.append("ethics.required=true but detail is empty")

    ledger = result.get("fact_ledger") or []
    for i, item in enumerate(ledger):
        if not isinstance(item, dict):
            problems.append("fact_ledger[%d] is not an object" % i)
            continue
        status = item.get("status")
        if status not in FACT_STATUS:
            problems.append("fact_ledger[%d] invalid status: %s" % (i, status))
        if status == "confirmed" and not item.get("source"):
            problems.append("fact_ledger[%d] confirmed without source" % i)
    checks["fact_ledger"] = len(ledger)

    retelling = result.get("one_minute_retelling") or []
    if len(retelling) < 5:
        warnings.append("one_minute_retelling has fewer than 5 points")
    red_team = result.get("red_team_answers") or []
    if len(red_team) < 4:
        warnings.append("red_team_answers has fewer than 4 entries")
    for i, item in enumerate(red_team):
        if isinstance(item, dict) and (not item.get("question") or not item.get("answer")):
            problems.append("red_team_answers[%d] missing question/answer" % i)

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
    for required in ("editable", "plan"):
        if required not in roles:
            problems.append("artifacts missing role=%s" % required)
    checks["artifact_roles"] = sorted(roles)
    if brief_text and PLACEHOLDER.search(brief_text):
        problems.append("proposal.md contains placeholder text")
    return {"ok": not problems, "problems": problems, "warnings": warnings, "checks": checks}


def self_test():
    return {
        "case_id": "proposal-self-test",
        "question": {"question": "X affects Z?", "pico": {
            "population": "samples", "intervention": "X", "comparison": "baseline",
            "outcome": "Z"}, "scope": {"included": "A", "excluded": ["B"]},
            "hypotheses": [{"id": "H1", "statement": "X affects Z",
                            "falsifiable_by": "no effect in test"}]},
        "objectives": [{"objective_id": "O1", "text": "test X", "module": "M1",
                        "indicator": "Z", "criterion": "p<0.05"}],
        "design": [{"module": "M1", "materials": "samples", "groups": "X vs control",
                    "measurements": ["Z"], "statistics": "t-test", "criterion": "p<0.05",
                    "sample_size_basis": "power 0.8"}],
        "plan": [{"phase": "Q1", "period": "M1-M3", "tasks": ["test"],
                  "milestone": "data", "deliverable": "dataset", "critical_path": True}],
        "risks": [{"risk": "low yield", "trigger": "yield <50%", "impact": "high",
                   "plan_b": "repeat with protocol B"}],
        "ethics": {"required": False, "status": "not_applicable", "detail": "no human data"},
        "fact_ledger": [{"fact_id": "F1", "statement": "X is available",
                         "status": "confirmed", "source": "materials/m.md"}],
        "one_minute_retelling": ["q", "gap", "route", "value", "outcome"],
        "red_team_answers": [{"question": "why", "answer": "because"},
                             {"question": "new", "answer": "method"},
                             {"question": "doable", "answer": "yes"},
                             {"question": "fallback", "answer": "plan B"}],
        "artifacts": [{"role": "editable", "path": "proposal.md"},
                      {"role": "plan", "path": "plan.json"}],
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
            (root / "proposal.md").write_text("Proposal body.", encoding="utf-8")
            (root / "plan.json").write_text("{}", encoding="utf-8")
            report = validate(self_test(), "Proposal body.", root)
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
