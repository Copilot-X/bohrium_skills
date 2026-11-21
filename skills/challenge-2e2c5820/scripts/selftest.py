#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Positive + boundary tests for the experiment feasibility skill.

Run inside an installed/staged skill (scripts/run_case.py must exist):
    python3 scripts/selftest.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_case(input_dir, work, output):
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(HERE / "run_skill.py"),
         "--input", str(input_dir), "--work", str(work), "--output", str(output)],
        capture_output=True, text=True, cwd=str(SKILL))
    return proc


def get_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def positive(root):
    input_dir = root / "input"
    work = root / "work"
    output = root / "output"
    write(input_dir / "case.json", json.dumps({
        "case_id": "SELFTEST-EXP-POS",
        "output_contract": {
            "实验方案": "hypothesis, variables, steps, parameters",
            "依据与风险": "evidence, risks, mitigation",
            "确认项": "safety, ethics, equipment, expert confirmation",
        },
    }, ensure_ascii=False))
    write(input_dir / "task.md", "# Case\nReview feasibility using the plan, equipment list and approval form.\n")
    write(input_dir / "plan.md", """# Experimental plan
Hypothesis: Treatment X reduces marker Y by 20% compared with vehicle after 14 days.
Independent variable: dose of X (0, 10, 50 mg/kg). Dependent variable: plasma marker Y.
Control: vehicle-treated group. Replicate: n=8 per group. Randomization: block randomization.
Step S1: dissolve X in saline at 50 mg/kg and dose intraperitoneal daily for 14 days.
Step S2: collect blood at day 14 and measure Y by ELISA kit K-123, detection range 5-500 pg/mL.
Equipment: plate reader Model PR-200, required wavelength 450 nm.
Animals: 24 male C57BL/6 mice, 8 weeks old, from vendor V.
Ethics approval: protocol A-2026-001 submitted, decision pending.
Budget: ELISA kits 2 x 4000 CNY, animals 24 x 200 CNY.
""")
    write(input_dir / "equipment.csv",
          "item,model,status,booking\nplate reader,PR-200,available,booked day 15-16\n"
          "centrifuge,C-100,available,no booking\nELISA kit K-123,K-123,to_order,lead time 3 weeks\n")
    write(input_dir / "approvals.md",
          "# Approval status\nAnimal ethics protocol A-2026-001: submitted 2026-01-05, pending IACUC review.\n"
          "Biosafety: no recombinant DNA; BSL-1 not required.\n")
    proc = run_case(input_dir, work, output)
    problems = []
    if proc.returncode != 0:
        problems.append("positive exit=%d stderr=%s" % (proc.returncode, proc.stderr[-800:]))
    result_path = output / "result.json"
    if not result_path.is_file():
        return ["positive: result.json missing"], {}
    result = get_json(result_path)
    if result.get("case_id") != "SELFTEST-EXP-POS":
        problems.append("positive: case_id mismatch")
    if result.get("status") not in ("completed", "partial"):
        problems.append("positive: unexpected status %r" % result.get("status"))
    report = output / "experiment-report.md"
    if not report.is_file() or report.stat().st_size < 200:
        problems.append("positive: report missing/too small")
    for name in ("feasibility-review.json", "risk-assessment.json", "confirmation-items.json"):
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            problems.append("positive: %s missing/empty" % name)
    review = get_json(output / "feasibility-review.json")
    matrix = review.get("matrix") or {}
    for key in ("equipment", "materials", "time", "budget", "safety", "ethics"):
        if not isinstance(matrix.get(key), dict) or not matrix.get(key):
            problems.append("positive: feasibility matrix lacks %s" % key)
        elif not matrix[key].get("condition"):
            problems.append("positive: feasibility.%s has no condition" % key)
    risks = get_json(output / "risk-assessment.json").get("risks") or []
    for risk in risks:
        if not risk.get("mitigation") or not risk.get("fallback"):
            problems.append("positive: risk %s lacks mitigation/fallback" % risk.get("id"))
    coverage = result.get("contract_coverage") or []
    if not coverage or not all(row.get("present") for row in coverage):
        problems.append("positive: contract coverage incomplete: %r" % coverage)
    audit = get_json(work / "domain-status.json")
    if audit.get("source_count", 0) < 3:
        problems.append("positive: source_count too small")
    if audit.get("evidence_count", 0) < 3:
        problems.append("positive: evidence_count too small")
    ethics = matrix.get("ethics") or {}
    if ethics.get("status") == "approved":
        problems.append("positive: pending approval was promoted to approved")
    return problems, {"status": result.get("status"),
                      "coverage": coverage,
                      "audit": audit.get("checks")}


def boundary(root):
    input_dir = root / "input-empty"
    work = root / "work-empty"
    output = root / "output-empty"
    write(input_dir / "case.json", json.dumps({
        "case_id": "SELFTEST-EXP-EMPTY",
        "output_contract": {"实验方案": "plan", "依据与风险": "risk", "确认项": "confirm"},
    }, ensure_ascii=False))
    proc = run_case(input_dir, work, output)
    problems = []
    if proc.returncode != 0:
        problems.append("boundary exit=%d stderr=%s" % (proc.returncode, proc.stderr[-800:]))
    result_path = output / "result.json"
    if not result_path.is_file():
        return ["boundary: result.json missing"], {}
    result = get_json(result_path)
    if result.get("status") not in ("input_error", "partial"):
        problems.append("boundary: status must be input_error/partial, got %r"
                        % result.get("status"))
    for name in ("experiment-report.md", "feasibility-review.json",
                 "risk-assessment.json", "confirmation-items.json"):
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            problems.append("boundary: declared output %s missing/empty" % name)
    audit = get_json(work / "domain-status.json")
    if audit.get("source_count") != 0:
        problems.append("boundary: expected zero sources")
    matrix = get_json(output / "feasibility-review.json").get("matrix") or {}
    equipment = (matrix.get("equipment") or {}).get("items") or []
    if equipment:
        problems.append("boundary: fabricated equipment from empty input")
    return problems, {"status": result.get("status"), "audit": audit}


def main():
    root = Path(tempfile.mkdtemp(prefix="exp-selftest-"))
    try:
        pos_problems, pos_info = positive(root)
        bnd_problems, bnd_info = boundary(root)
        report = {
            "skill": SKILL.name,
            "positive": {"ok": not pos_problems, "problems": pos_problems, **pos_info},
            "boundary": {"ok": not bnd_problems, "problems": bnd_problems, **bnd_info},
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        ok = not pos_problems and not bnd_problems
        print("SELFTEST OK" if ok else "SELFTEST FAILED")
        return 0 if ok else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
