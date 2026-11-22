#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic self-test for challenge-de9f0708 (compliance review).

Cases (all material is fabricated and marked synthetic):

1. normal material: complete declarations plus a data statement that is only
   "available upon request"; the checker must pass ethics and flag the data
   statement for recheck.
2. empty input: only case.json/task.md; the checker must write valid outputs
   with status input_error and invent no findings.
3. missing ethics/data declarations: human-participant material without an
   ethics or data statement; both facets must be ``issue`` and no approval
   number/DOI may be fabricated.

When ``run_case.py`` is available next to this script (staged package) or
``--entry`` is supplied (repository development tree), the test also runs the
real pipeline:

    prepare -> compliance_check --merge-work -> write outputs -> finalize

and checks that the success status is ``completed`` and that removing the
main deliverable downgrades finalize to ``partial`` without a crash.

Usage::

    python3 scripts/selftest.py
    python3 scripts/selftest.py --entry ../../../shared/scripts/run_case.py

Exit code 0 when every executed case passes, 1 otherwise. No report file is
written outside a temporary directory.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
CHECKER = HERE / "compliance_check.py"
PY = [sys.executable, "-X", "utf8"]


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path, payload):
    write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_checker(input_dir, work, output, *extra):
    return subprocess.run(
        PY + [str(CHECKER), "--input", str(input_dir), "--work", str(work),
              "--output", str(output)] + list(extra),
        capture_output=True, text=True)


def run_entry(entry, command, input_dir, work, output):
    return subprocess.run(
        PY + [str(entry), command, "--input", str(input_dir),
              "--work", str(work), "--output", str(output),
              "--skill-dir", str(SKILL)],
        capture_output=True, text=True)


def norm_key(text):
    return re.sub(r"\s+", "", str(text or "").lower())


def quote_ok(source, quote):
    return bool(norm_key(quote)) and norm_key(quote) in norm_key(source)


def matrix_index(matrix):
    return {row.get("check_id"): row for row in matrix.get("matrix") or []}


def check_evidence(matrix, input_dir):
    problems = []
    texts = {}
    for path in Path(input_dir).rglob("*"):
        if path.is_file() and path.suffix.lower() in (".md", ".txt", ".json"):
            try:
                texts[path.name] = path.read_text(encoding="utf-8")
            except OSError:
                continue
    for item in matrix.get("evidence_map") or []:
        source = texts.get(Path(str(item.get("path"))).name)
        if source is None:
            problems.append("evidence path not found: %s" % item.get("path"))
            continue
        if not item.get("locator"):
            problems.append("evidence lacks locator: %s" % item.get("path"))
        if not quote_ok(source, item.get("quote")):
            problems.append("evidence quote not verbatim: %s %s" % (
                item.get("path"), str(item.get("quote"))[:50]))
    return problems


def assert_no_fabrication(matrix):
    blob = json.dumps(matrix, ensure_ascii=False)
    problems = []
    if re.search(r"IRB[-\s]?\d{2,}", blob, re.I):
        problems.append("fabricated-looking IRB number in output")
    if re.search(r"\b10\.\d{4,9}/\S+", blob):
        problems.append("fabricated-looking DOI in output")
    return problems


NORMAL = """# Synthetic only - normal compliance material

Synthetic only; fabricated for self-testing, not a real paper.

## Methods
Synthetic participants (n=20) were recruited from a synthetic clinic.
Written informed consent was obtained from all participants.
The study was approved by the Synthetic Ethics Committee (IRB-2024-001).

## Results
Synthetic results only.

## Declarations
Ethics approval: Synthetic Ethics Committee, approval number IRB-2024-001.
Data availability: The synthetic dataset is available from the corresponding author upon reasonable request.
Conflict of interest: The authors declare no competing interests.
Author contributions: A.B. designed the study; C.D. collected and analyzed data; E.F. wrote the manuscript.
Funding: This work was supported by the Synthetic Research Foundation under grant SRF-2024-001.
Acknowledgments: We thank the synthetic laboratory staff.

## Figures
Figure 1 is reproduced from a synthetic prior publication with permission from the copyright holder.
"""

MISSING = """# Synthetic only - missing declarations

Synthetic only; fabricated for self-testing, not a real paper.

## Methods
We recruited 30 patients from a synthetic clinic and collected blood samples.
Written informed consent was obtained from all participants.

## Results
Synthetic results only.
"""


def setup_input(root, case_id, material=None):
    input_dir, work, output = root / "input", root / "work", root / "output"
    write_json(input_dir / "case.json", {
        "case_id": case_id,
        "synthetic": True,
        "output_contract": {
            "review_report": "output/review-report.md",
            "findings": "output/findings.json",
            "evidence_map": "output/evidence-map.json",
            "status": "pass | issues | needs_recheck | not_checked",
        },
    })
    write(input_dir / "task.md",
          "# Synthetic only\n\nCompliance review self-test. Synthetic only.\n")
    if material:
        write(input_dir / "manuscript.md", material)
    return input_dir, work, output


def case_normal(root):
    input_dir, work, output = setup_input(root, "SELFTEST-COMPLIANCE-NORMAL", NORMAL)
    proc = run_checker(input_dir, work, output, "--merge-work")
    problems = []
    if proc.returncode != 0:
        problems.append("exit=%d stderr=%s" % (proc.returncode, proc.stderr[-600:]))
    matrix_path = output / "compliance-matrix.json"
    if not matrix_path.is_file():
        return ["normal: compliance-matrix.json missing"], {}
    matrix = read_json(matrix_path)
    rows = matrix_index(matrix)
    expected = {
        "ethics_approval": "pass",
        "data_availability": "needs_recheck",
        "conflict_of_interest": "pass",
        "copyright_license": "pass",
    }
    for check_id, status in expected.items():
        row = rows.get(check_id) or {}
        if row.get("status") != status:
            problems.append("%s: expected %s, got %s" % (
                check_id, status, row.get("status")))
    if not any(f.get("check_id") == "data_availability"
               for f in matrix.get("findings") or []):
        problems.append("normal: missing data_availability finding")
    problems.extend(check_evidence(matrix, input_dir))
    for name in ("compliance-matrix.json", "compliance-findings.json",
                 "compliance-evidence-map.json", "compliance-check.md"):
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            problems.append("normal: output %s missing/empty" % name)
    work_file = work / "review-findings.json"
    if not work_file.is_file():
        problems.append("normal: --merge-work did not write review-findings.json")
    else:
        merged = read_json(work_file)
        if not merged.get("compliance_matrix"):
            problems.append("normal: merged work lacks compliance_matrix")
        if not merged.get("guideline_source", {}).get("name"):
            problems.append("normal: merged work lacks guideline_source.name")
    return problems, {"status": matrix.get("status"),
                      "counts": matrix.get("counts"),
                      "findings": len(matrix.get("findings") or [])}


def case_empty(root):
    input_dir, work, output = setup_input(root, "SELFTEST-COMPLIANCE-EMPTY", None)
    proc = run_checker(input_dir, work, output)
    problems = []
    if proc.returncode != 0:
        problems.append("empty exit=%d stderr=%s" % (proc.returncode, proc.stderr[-600:]))
    matrix_path = output / "compliance-matrix.json"
    if not matrix_path.is_file():
        return ["empty: compliance-matrix.json missing"], {}
    matrix = read_json(matrix_path)
    if matrix.get("status") != "input_error":
        problems.append("empty: expected input_error, got %r" % matrix.get("status"))
    if matrix.get("findings"):
        problems.append("empty: fabricated findings from empty input")
    if matrix.get("matrix"):
        problems.append("empty: fabricated matrix rows from empty input")
    for name in ("compliance-matrix.json", "compliance-findings.json",
                 "compliance-evidence-map.json", "compliance-check.md"):
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            problems.append("empty: output %s missing/empty" % name)
    return problems, {"status": matrix.get("status"),
                      "findings": len(matrix.get("findings") or [])}


def case_missing(root):
    input_dir, work, output = setup_input(root, "SELFTEST-COMPLIANCE-MISSING", MISSING)
    proc = run_checker(input_dir, work, output)
    problems = []
    if proc.returncode != 0:
        problems.append("missing exit=%d stderr=%s" % (proc.returncode, proc.stderr[-600:]))
    matrix_path = output / "compliance-matrix.json"
    if not matrix_path.is_file():
        return ["missing: compliance-matrix.json missing"], {}
    matrix = read_json(matrix_path)
    rows = matrix_index(matrix)
    for check_id in ("ethics_approval", "data_availability"):
        row = rows.get(check_id) or {}
        if row.get("status") != "issue":
            problems.append("%s: expected issue, got %s" % (
                check_id, row.get("status")))
    found = {f.get("check_id") for f in matrix.get("findings") or []}
    for check_id in ("ethics_approval", "data_availability"):
        if check_id not in found:
            problems.append("%s: no finding emitted" % check_id)
    problems.extend(check_evidence(matrix, input_dir))
    problems.extend(assert_no_fabrication(matrix))
    return problems, {"status": matrix.get("status"),
                      "missing_facets": sorted(found & {"ethics_approval",
                                                        "data_availability"})}


def pipeline(root, entry):
    """Real prepare -> checker -> outputs -> finalize path (when entry exists)."""
    problems, info = [], {}
    input_dir, work, output = setup_input(root / "pipe", "SELFTEST-COMPLIANCE-PIPE",
                                         NORMAL)
    pre = run_entry(entry, "prepare", input_dir, work, output)
    if pre.returncode != 0:
        return ["pipeline prepare exit=%d: %s" % (
            pre.returncode, (pre.stdout + pre.stderr)[-600:])], {}
    chk = run_checker(input_dir, work, output, "--merge-work")
    if chk.returncode != 0:
        problems.append("pipeline checker exit=%d" % chk.returncode)
    findings = read_json(output / "compliance-findings.json")
    write_json(output / "findings.json", findings)
    write_json(output / "evidence-map.json", read_json(
        output / "compliance-evidence-map.json"))
    ids = [f.get("id") for f in findings.get("findings") or []]
    report = ["# Synthetic only - compliance review report", "",
              "Synthetic only; pipeline self-test, not a real case.", "",
              "## Findings", ""]
    for fid in ids:
        report.append("- %s (synthetic evidence in compliance-matrix.json)" % fid)
    report += ["", "## Boundary", "",
               "Not a formal ethics or legal opinion; synthetic only.", ""]
    write(output / "review-report.md", "\n".join(report))
    fin = run_entry(entry, "finalize", input_dir, work, output)
    if fin.returncode != 0:
        problems.append("pipeline finalize exit=%d: %s" % (
            fin.returncode, (fin.stdout + fin.stderr)[-600:]))
    result_path = output / "result.json"
    if not result_path.is_file():
        problems.append("pipeline: result.json missing")
    else:
        result = read_json(result_path)
        info["success_status"] = result.get("status")
        if result.get("status") != "completed":
            problems.append("pipeline: expected completed, got %r"
                            % result.get("status"))
        artifacts = [item.get("path") for item in result.get("artifacts") or []
                     if isinstance(item, dict)]
        if not any(str(path).endswith("review-report.md") for path in artifacts):
            problems.append("pipeline: review-report.md not registered")
    # A missing declared artifact must not crash. Current shared code may
    # re-materialize it from real work content; otherwise it must downgrade to
    # partial and say so.
    report_path = output / "review-report.md"
    if report_path.exists():
        report_path.unlink()
    fin2 = run_entry(entry, "finalize", input_dir, work, output)
    if fin2.returncode != 0:
        problems.append("pipeline missing-artifact finalize exit=%d" % fin2.returncode)
    if result_path.is_file():
        result2 = read_json(result_path)
        info["missing_artifact_status"] = result2.get("status")
        if result2.get("status") not in ("completed", "partial", "input_error"):
            problems.append("pipeline: unexpected missing-artifact status %r"
                            % result2.get("status"))
        if result2.get("status") == "partial":
            if not result2.get("uncertainties"):
                problems.append("pipeline: partial result has no uncertainty")
        elif not report_path.is_file() or report_path.stat().st_size == 0:
            problems.append("pipeline: completed but review-report.md is still missing")
    # Missing required work fields must downgrade to partial and still exit 0.
    work_file = work / "review-findings.json"
    if work_file.is_file():
        broken = read_json(work_file)
        broken.pop("findings", None)
        broken.pop("compliance_matrix", None)
        write_json(work_file, broken)
        fin3 = run_entry(entry, "finalize", input_dir, work, output)
        if fin3.returncode != 0:
            problems.append("pipeline missing-field finalize exit=%d" % fin3.returncode)
        result3 = read_json(result_path) if result_path.is_file() else {}
        info["missing_field_status"] = result3.get("status")
        if result3.get("status") not in ("partial", "input_error"):
            problems.append("pipeline: missing required fields should be partial, got %r"
                            % result3.get("status"))
        if result3.get("status") == "partial" and not result3.get("uncertainties"):
            problems.append("pipeline: missing-field partial has no uncertainty")
    # Empty input still exits 0 and writes a legal result.
    empty_in, empty_work, empty_out = setup_input(
        root / "pipe-empty", "SELFTEST-COMPLIANCE-PIPE-EMPTY", None)
    pre2 = run_entry(entry, "prepare", empty_in, empty_work, empty_out)
    if pre2.returncode != 0:
        problems.append("pipeline empty prepare exit=%d" % pre2.returncode)
    run_checker(empty_in, empty_work, empty_out)
    fin3 = run_entry(entry, "finalize", empty_in, empty_work, empty_out)
    if fin3.returncode != 0:
        problems.append("pipeline empty finalize exit=%d" % fin3.returncode)
    empty_result = empty_out / "result.json"
    if not empty_result.is_file():
        problems.append("pipeline empty: result.json missing")
    else:
        data = read_json(empty_result)
        info["empty_status"] = data.get("status")
        if data.get("status") not in ("partial", "input_error"):
            problems.append("pipeline empty: unexpected status %r" % data.get("status"))
    return problems, info


def main(argv=None):
    parser = argparse.ArgumentParser(description="synthetic compliance self-test")
    parser.add_argument("--entry", default=None,
                        help="path to run_case.py (default: scripts/run_case.py)")
    args = parser.parse_args(argv)
    entry = Path(args.entry) if args.entry else HERE / "run_case.py"
    root = Path(tempfile.mkdtemp(prefix="compliance-selftest-"))
    try:
        results = {}
        for name, func in (("normal", case_normal), ("empty", case_empty),
                           ("missing_declarations", case_missing)):
            case_root = root / name
            problems, info = func(case_root)
            results[name] = {"ok": not problems, "problems": problems, **info}
        if entry.is_file():
            problems, info = pipeline(root, entry)
            results["pipeline"] = {"ok": not problems, "problems": problems, **info}
        else:
            results["pipeline"] = {"ok": True, "skipped":
                                   "run_case.py not present; pass --entry"}
        ok = all(item.get("ok") for item in results.values())
        print(json.dumps({"skill": SKILL.name, "cases": results,
                          "ok": ok}, ensure_ascii=False, indent=2))
        print("SELFTEST OK" if ok else "SELFTEST FAILED")
        return 0 if ok else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
