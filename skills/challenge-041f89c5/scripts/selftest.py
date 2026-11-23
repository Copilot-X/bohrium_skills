#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Positive + boundary tests for the research topic-selection skill."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
_STAGE = None


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def staged_scripts():
    """Return a scripts directory that also contains the shared engine.

    In the installed submission package run_case.py sits next to run_skill.py.
    In the authoring tree it lives in period4/shared/scripts, so a temporary
    copy is made without hard-coding an absolute path.
    """
    global _STAGE
    if _STAGE is not None:
        return _STAGE
    if (HERE / "run_case.py").is_file():
        _STAGE = HERE
        return _STAGE
    period4 = HERE.parents[2]
    shared = period4 / "shared" / "scripts"
    if not shared.is_dir():
        _STAGE = HERE
        return _STAGE
    stage_root = Path(tempfile.mkdtemp(prefix="research-selftest-stage-"))
    stage_skill = stage_root / SKILL.name
    shutil.copytree(SKILL, stage_skill)
    for name in ("run_case.py", "skillcore.py", "parsers.py", "fam_tracks.py"):
        source = shared / name
        if source.is_file():
            shutil.copy2(source, stage_skill / "scripts" / name)
    _STAGE = stage_skill / "scripts"
    return _STAGE


def run_skill(input_dir, work, output):
    scripts = staged_scripts()
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(scripts / "run_skill.py"),
         "--input", str(input_dir), "--work", str(work), "--output", str(output)],
        capture_output=True, text=True, cwd=str(scripts.parent))


def get_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def positive(root):
    input_dir, work, output = root / "input", root / "work", root / "output"
    write(input_dir / "case.json", json.dumps({
        "case_id": "SELFTEST-RESEARCH-POS",
        "output_contract": {"研究产物": "report", "证据矩阵": "matrix", "不确定性": "limits"},
    }, ensure_ascii=False))
    write(input_dir / "task.md", "# Case\nAssess the proposed topic against the provided records.\n")
    write(input_dir / "records.csv",
          "title,doi,year,authors,venue,abstract,status\n"
          "Graph neural networks for molecular property prediction,10.1000/gnn.2023,2023,Li et al.,J Chem,"
          "Graph neural networks improve molecular property prediction on benchmark datasets,published\n"
          "Transformers for molecular property prediction,10.1000/tr.2024,2024,Wang et al.,Nat Mach,"
          "Transformer models for molecular property prediction with pretraining,retracted\n"
          "Small-data molecular benchmarks,10.1000/bench.2022,2022,Chen et al.,Data Sci,"
          "Benchmark of molecular property datasets under small-data regimes,published\n")
    write(input_dir / "proposal.md", """# Proposal
Research question: Can a graph transformer improve molecular property prediction under small-data regimes?
Population: public molecular property datasets with fewer than 1,000 labeled compounds.
Variables: model architecture and pretraining strategy; outcome is RMSE on held-out scaffolds.
Baseline: the graph neural network of Li et al. (2023).
Expected conclusion: the proposed model lowers RMSE by at least 10% under the same split.
Falsification: the improvement disappears or RMSE is higher when the split is fixed.
Novelty claim: we propose a new pretraining objective for small-data molecular graphs.
Data availability: three public datasets are provided in the supplementary material.
Compute: one 24 GB GPU is available.
Timeline: twelve weeks including ablation.
Ethics: no human or animal data are used.
""")
    proc = run_skill(input_dir, work, output)
    problems = []
    if proc.returncode != 0:
        problems.append("positive exit=%d stderr=%s" % (proc.returncode, proc.stderr[-800:]))
    result_path = output / "result.json"
    if not result_path.is_file():
        return ["positive: result.json missing"], {}
    result = get_json(result_path)
    if result.get("case_id") != "SELFTEST-RESEARCH-POS":
        problems.append("positive: case_id mismatch")
    if result.get("status") not in ("completed", "partial"):
        problems.append("positive: unexpected status %r" % result.get("status"))
    for name in ("research-report.md", "evidence-matrix.json", "references.json"):
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            problems.append("positive: %s missing/empty" % name)
    refs = get_json(output / "references.json").get("references") or []
    if len(refs) < 3:
        problems.append("positive: parsed fewer than 3 references")
    for record in refs:
        if record.get("doi") and not str(record["doi"]).startswith("10."):
            problems.append("positive: invalid DOI %r" % record.get("doi"))
    matrix = get_json(output / "evidence-matrix.json").get("matrix") or []
    if len(matrix) != len(refs):
        problems.append("positive: matrix/reference count mismatch")
    retracted = [r for r in refs if r.get("status") == "retracted"]
    if not retracted:
        problems.append("positive: retracted record was not flagged")
    claims = get_json(output / "evidence-matrix.json").get("claims") or []
    if not claims:
        problems.append("positive: no novelty claim extracted")
    for claim in claims:
        if claim.get("verdict") not in ("possibly_covered", "weakly_related",
                                        "no_overlap_in_provided_corpus"):
            problems.append("positive: invalid verdict %r" % claim.get("verdict"))
    coverage = result.get("contract_coverage") or []
    if not coverage or not all(row.get("present") for row in coverage):
        problems.append("positive: contract coverage incomplete: %r" % coverage)
    audit = get_json(work / "domain-status.json")
    if audit.get("source_count", 0) < 1:
        problems.append("positive: source_count too small")
    if audit.get("records_extracted", 0) < 3:
        problems.append("positive: records_extracted too small")
    checks = audit.get("checks") or {}
    if checks.get("blocking"):
        problems.append("positive: blocking domain checks %r" % checks.get("problems"))
    return problems, {"status": result.get("status"), "coverage": coverage,
                      "records": len(refs), "checks": checks}


def boundary(root):
    input_dir, work, output = root / "input-empty", root / "work-empty", root / "output-empty"
    write(input_dir / "case.json", json.dumps({
        "case_id": "SELFTEST-RESEARCH-EMPTY",
        "output_contract": {"研究产物": "report", "证据矩阵": "matrix"},
    }, ensure_ascii=False))
    proc = run_skill(input_dir, work, output)
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
    for name in ("research-report.md", "evidence-matrix.json", "references.json"):
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            problems.append("boundary: declared output %s missing/empty" % name)
    refs = get_json(output / "references.json").get("references")
    if refs:
        problems.append("boundary: fabricated references from empty input")
    audit = get_json(work / "domain-status.json")
    if audit.get("source_count") != 0:
        problems.append("boundary: expected zero sources")
    return problems, {"status": result.get("status"), "audit": audit}


def main():
    root = Path(tempfile.mkdtemp(prefix="research-selftest-"))
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
        if _STAGE is not None and _STAGE != HERE:
            try:
                shutil.rmtree(_STAGE.parents[2], ignore_errors=True)
            except IndexError:
                pass


if __name__ == "__main__":
    sys.exit(main())
