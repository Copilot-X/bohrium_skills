#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Positive, idempotency and boundary tests for latest-tracking."""
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
        "case_id": "SELFTEST-TRACK-POS",
        "subscription": {"id": "SUB-SELFTEST",
                         "query": "graph neural network molecular property prediction"},
        "window": {"start": "2024-01-01", "end": "2024-02-01", "timezone": "UTC"},
        "snapshot_before": {"keys": ["doi:10.1000/pub"]},
        "output_contract": {"检索结果": "increments", "证据记录": "evidence",
                            "状态说明": "limits"},
    }, ensure_ascii=False))
    write(input_dir / "task.md", "# Subscription\nTrack new work for the query in the window.\n")
    write(input_dir / "records.csv",
          "title,doi,arxiv,date,authors,venue,status,version\n"
          "Old molecular graph work,10.1000/old,,2023-12-01,Old et al.,J Old,published,v1\n"
          "Graph neural network for molecular property prediction,10.1000/new,,2024-01-15,Li et al.,J Chem,published,v1\n"
          "Graph neural network for molecular property prediction,10.1000/pub,arXiv:2401.00001,2024-01-20,Li et al.,J Chem,published,v2\n"
          "Graph neural network for molecular property prediction,,arXiv:2401.00001,2024-01-18,Li et al.,arXiv,preprint,v1\n"
          "Retracted molecular graph benchmark,10.1000/ret,,2024-01-10,Wang et al.,J Bad,retracted,v1\n"
          "Future molecular graph work,10.1000/future,,2024-03-01,Zhao et al.,J Future,published,v1\n")
    proc = run_skill(input_dir, work, output)
    problems = []
    if proc.returncode != 0:
        problems.append("positive exit=%d stderr=%s" % (proc.returncode, proc.stderr[-800:]))
    result_path = output / "result.json"
    if not result_path.is_file():
        return ["positive: result.json missing"], {}, {}
    result = get_json(result_path)
    data = get_json(output / "search-results.json")
    if result.get("status") not in ("completed", "answered", "partial"):
        problems.append("positive: unexpected status %r" % result.get("status"))
    for name in ("search-results.json", "candidates.csv", "search-log.json", "evidence.json"):
        path = output / name
        if not path.is_file() or path.stat().st_size == 0:
            problems.append("positive: %s missing/empty" % name)
    window = data.get("window") or {}
    if (window.get("start"), window.get("end"), window.get("timezone")) != (
            "2024-01-01", "2024-02-01", "UTC"):
        problems.append("positive: window drifted: %r" % window)
    increments = data.get("increments") or []
    categories = sorted(inc.get("category") for inc in increments)
    if categories != ["new", "retraction", "version_update"]:
        problems.append("positive: categories=%r" % categories)
    keys = [inc.get("dedupe_key") for inc in increments]
    if len(keys) != len(set(keys)):
        problems.append("positive: duplicate dedupe keys in increments")
    for inc in increments:
        for field in ("record_id", "dedupe_key", "category", "evidence", "alert_id"):
            if not inc.get(field):
                problems.append("positive: increment lacks %s" % field)
    alerts = data.get("alert_log") or []
    if len(alerts) != 3 or any(a.get("status") != "queued_for_digest" for a in alerts):
        problems.append("positive: alert_log unexpected: %r" % alerts)
    suppressed = data.get("suppressed") or []
    if not any(item.get("reason") == "outside_window" for item in suppressed):
        problems.append("positive: out-of-window record was not suppressed")
    if not data.get("replay_key"):
        problems.append("positive: replay_key missing")
    state = get_json(work / "tracking-state.json")
    if state.get("replay_key") != data.get("replay_key"):
        problems.append("positive: replay key/state mismatch")
    coverage = result.get("contract_coverage") or []
    if not coverage or not all(row.get("present") for row in coverage):
        problems.append("positive: contract coverage incomplete: %r" % coverage)
    return problems, {"status": result.get("status"), "categories": categories,
                      "alerts": len(alerts), "suppressed": len(suppressed)}, state


def idempotency(root, input_dir, work, output, first_state):
    proc = run_skill(input_dir, work, output)
    problems = []
    if proc.returncode != 0:
        problems.append("rerun exit=%d" % proc.returncode)
    data = get_json(output / "search-results.json")
    alerts = data.get("alert_log") or []
    if any(a.get("status") == "queued_for_digest" for a in alerts):
        problems.append("rerun: alert was queued twice")
    if not any(s.get("reason") == "already_prepared_idempotent"
               for s in data.get("suppressed") or []):
        problems.append("rerun: no idempotent suppression recorded")
    state = get_json(work / "tracking-state.json")
    ids = state.get("prepared_alert_ids") or []
    if len(ids) != len(set(ids)):
        problems.append("rerun: duplicate prepared_alert_ids in state")
    return problems, {"alerts": [(a.get("alert_id"), a.get("status")) for a in alerts],
                      "runs": state.get("runs")}


def boundary(root):
    input_dir, work, output = root / "input-empty", root / "work-empty", root / "output-empty"
    write(input_dir / "case.json", json.dumps({
        "case_id": "SELFTEST-TRACK-EMPTY",
        "output_contract": {"检索结果": "increments", "证据记录": "evidence"},
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
    if data.get("increments"):
        problems.append("boundary: fabricated increments from empty input")
    if data.get("candidates"):
        problems.append("boundary: fabricated candidates from empty input")
    csv_text = (output / "candidates.csv").read_text(encoding="utf-8")
    if "10." in csv_text or "arXiv" in csv_text:
        problems.append("boundary: fabricated identifier in candidates.csv")
    audit = get_json(work / "domain-status.json")
    if audit.get("source_count") != 0:
        problems.append("boundary: expected zero sources")
    return problems, {"status": result.get("status"), "audit": audit}


def main():
    root = Path(tempfile.mkdtemp(prefix="track-selftest-"))
    try:
        pos_problems, pos_info, state = positive(root)
        idem_problems, idem_info = ([], {})
        if not pos_problems:
            idem_problems, idem_info = idempotency(
                root, root / "input", root / "work", root / "output", state)
        bnd_problems, bnd_info = boundary(root)
        report = {
            "skill": SKILL.name,
            "positive": {"ok": not pos_problems, "problems": pos_problems, **pos_info},
            "idempotency": {"ok": not idem_problems, "problems": idem_problems, **idem_info},
            "boundary": {"ok": not bnd_problems, "problems": bnd_problems, **bnd_info},
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        ok = not (pos_problems or idem_problems or bnd_problems)
        print("SELFTEST OK" if ok else "SELFTEST FAILED")
        return 0 if ok else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
