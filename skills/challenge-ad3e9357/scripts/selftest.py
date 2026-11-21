#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Positive + boundary tests for the patent/technical-literature skill."""
from __future__ import annotations

import csv
import io
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


def csv_text(rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def run_skill(input_dir, work, output):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(HERE / "run_skill.py"),
         "--input", str(input_dir), "--work", str(work), "--output", str(output)],
        capture_output=True, text=True, cwd=str(SKILL))


def get_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def positive(root):
    input_dir, work, output = root / "input", root / "work", root / "output"
    write(input_dir / "case.json", json.dumps({
        "case_id": "SELFTEST-PATENT-POS",
        "topic": "solid electrolyte lithium battery",
        "output_contract": {"检索结果": "patents", "证据记录": "evidence",
                            "状态说明": "limits"},
    }, ensure_ascii=False))
    write(input_dir / "task.md",
          "# Patent search\nFind the target patent, family members and technical literature.\n")
    write(input_dir / "patents.csv", csv_text([
        ["patent_number", "application_number", "priority_number", "title", "applicant",
         "inventors", "filing_date", "priority_date", "publication_date", "grant_date",
         "legal_status", "status_date", "family_id", "ipc", "abstract", "claims", "access"],
        ["US 11,123,456 B2", "US 16/123,456", "US 62/111,222",
         "Solid electrolyte for lithium battery", "Example Corp", "Li et al.",
         "2019-01-10", "2018-01-10", "2021-06-01", "2021-06-01", "granted",
         "2021-06-01", "F1", "H01M 10/0562",
         "Solid electrolyte composition with improved conductivity",
         "A solid electrolyte comprising a ceramic phase", "public"],
        ["EP 3 456 789 A1", "EP 19 123 456", "US 62/111,222",
         "Solid electrolyte for lithium battery", "Example Corp", "Li et al.",
         "2019-07-10", "2018-01-10", "2020-01-15", "", "pending", "2020-01-15",
         "F1", "H01M 10/0562", "Solid electrolyte composition",
         "pending claim set", "licensed"],
        ["WO 2020/123456 A1", "WO 2020/123456", "EP 18123456",
         "Solid electrolyte battery", "Other AG", "Wang et al.",
         "2020-06-01", "2018-12-01", "2020-12-17", "", "", "", "", "H01M 10/0562",
         "", "", "public"],
    ]))
    write(input_dir / "observations.csv", csv_text([
        ["patent_number", "legal_status", "status_date", "source_note"],
        ["US 11,123,456 B2", "expired", "2025-01-01",
         "second observation for conflict test"],
    ]))
    write(input_dir / "literature.md",
          "# Technical literature\n"
          "Solid electrolyte interphase review, DOI 10.1000/sei.2020, 2020, provided corpus.\n")
    write(input_dir / "access.md",
          "USPTO facsimile is public access. Commercial database extracts are restricted.\n")
    proc = run_skill(input_dir, work, output)
    problems = []
    if proc.returncode != 0:
        problems.append("positive exit=%d stderr=%s" % (proc.returncode, proc.stderr[-800:]))
    result_path = output / "result.json"
    if not result_path.is_file():
        return ["positive: result.json missing"], {}
    result = get_json(result_path)
    data = get_json(output / "search-results.json")
    if result.get("status") not in ("completed", "answered", "partial"):
        problems.append("positive: unexpected status %r" % result.get("status"))
    for name in ("search-results.json", "candidates.csv", "search-log.json", "evidence.json"):
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            problems.append("positive: %s missing/empty" % name)
    patents = data.get("patents") or []
    if len(patents) != 3:
        problems.append("positive: expected 3 patents, got %d" % len(patents))
    numbers = {p.get("patent_number") for p in patents}
    for expected in ("US11123456B2", "EP3456789A1", "WO2020123456A1"):
        if expected not in numbers:
            problems.append("positive: missing normalized number %s" % expected)
    us = next((p for p in patents if p.get("patent_number") == "US11123456B2"), {})
    if us.get("legal_status") != "conflict":
        problems.append("positive: conflicting statuses were flattened to %r"
                        % us.get("legal_status"))
    if len(us.get("status_observations") or []) < 2:
        problems.append("positive: status observations were not retained")
    families = data.get("families") or []
    if len(families) != 2:
        problems.append("positive: expected 2 families, got %d" % len(families))
    merged_family = next((f for f in families
                          if {"US11123456B2", "EP3456789A1"}
                          <= set(f.get("member_numbers") or [])), {})
    if len(merged_family.get("members") or []) != 2:
        problems.append("positive: explicit family/priority did not merge both members")
    wo_family = next((f for f in families
                      if "WO2020123456A1" in (f.get("member_numbers") or [])), {})
    if len(wo_family.get("members") or []) != 1:
        problems.append("positive: WO patent was over-merged into another family")
    if set(wo_family.get("member_numbers") or []) & {"US11123456B2", "EP3456789A1"}:
        problems.append("positive: WO family leaked members from the F1 family")
    wo = next((p for p in patents if p.get("patent_number") == "WO2020123456A1"), {})
    if wo.get("legal_status") != "unknown" or not wo.get("needs_verification"):
        problems.append("positive: missing legal status was not kept as unknown")
    literature = data.get("technical_literature") or []
    if not any(item.get("doi") == "10.1000/sei.2020" for item in literature):
        problems.append("positive: technical literature DOI was not extracted")
    classification = data.get("classification") or []
    if not any(row.get("code") == "H01M 10/0562" and row.get("valid")
               for row in classification):
        problems.append("positive: valid IPC was not normalized")
    coverage = result.get("contract_coverage") or []
    if not coverage or not all(row.get("present") for row in coverage):
        problems.append("positive: contract coverage incomplete: %r" % coverage)
    audit = get_json(work / "domain-status.json")
    checks = audit.get("checks") or {}
    if checks.get("blocking"):
        problems.append("positive: blocking domain checks %r" % checks.get("problems"))
    return problems, {"status": result.get("status"), "patents": len(patents),
                      "families": len(families), "checks": checks}


def boundary(root):
    input_dir, work, output = root / "input-empty", root / "work-empty", root / "output-empty"
    write(input_dir / "case.json", json.dumps({
        "case_id": "SELFTEST-PATENT-EMPTY",
        "output_contract": {"检索结果": "patents", "证据记录": "evidence"},
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
    for name in ("search-results.json", "candidates.csv", "search-log.json", "evidence.json"):
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            problems.append("boundary: declared output %s missing/empty" % name)
    data = get_json(output / "search-results.json")
    if data.get("patents") or data.get("families"):
        problems.append("boundary: fabricated patents from empty input")
    csv_text = (output / "candidates.csv").read_text(encoding="utf-8")
    if "US" in csv_text or "EP" in csv_text or "WO" in csv_text:
        problems.append("boundary: fabricated patent number in candidates.csv")
    audit = get_json(work / "domain-status.json")
    if audit.get("source_count") != 0:
        problems.append("boundary: expected zero sources")
    return problems, {"status": result.get("status"), "audit": audit}


def main():
    root = Path(tempfile.mkdtemp(prefix="patent-selftest-"))
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
