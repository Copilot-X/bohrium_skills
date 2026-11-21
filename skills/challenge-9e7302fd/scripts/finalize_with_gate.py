#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Finalize wrapper: run the review quality gate, then run_case.py finalize.

Why this wrapper exists
-----------------------
``fam_tracks`` is a shared module and cannot know about this skill's
methods/statistics coverage rules.  This wrapper keeps the enforcement local:
it always runs ``review_quality_gate.py`` first and then calls the standard
``run_case.py finalize``.  If the gate reports problems, the wrapper downgrades
``status=completed`` to ``status=partial`` and appends ``review_gate``
uncertainties to ``output/result.json``.  It never edits the review content.

Usage:
    python3 "$SKILL_DIR/scripts/finalize_with_gate.py" \
      --input /input --work /app/work --output /app/output
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _run(command):
    proc = subprocess.run(command, capture_output=True, text=True)
    text = (proc.stdout or "") + (proc.stderr or "")
    if text:
        print(text.rstrip())
    return proc


def _load_gate(output):
    path = Path(output) / "review-gate.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--skill-dir", default=str(HERE.parent))
    args = parser.parse_args(argv)

    gate_script = HERE / "review_quality_gate.py"
    gate_proc = _run([sys.executable, str(gate_script),
                      "--work", args.work, "--output", args.output,
                      "--input", args.input, "--json"]) \
        if gate_script.is_file() else None
    gate = _load_gate(args.output)

    finalize = _run([sys.executable, str(HERE / "run_case.py"), "finalize",
                     "--input", args.input, "--work", args.work,
                     "--output", args.output,
                     "--skill-dir", args.skill_dir])
    result_path = Path(args.output) / "result.json"
    if not result_path.is_file():
        return 2
    try:
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return 2

    issues = []
    if gate_proc is None:
        issues.append("review_quality_gate.py is missing from the skill package")
    elif gate_proc.returncode != 0:
        if gate:
            issues.extend(str(item) for item in (gate.get("problems") or []))
        else:
            issues.append("review gate exited %d without a readable "
                          "output/review-gate.json" % gate_proc.returncode)
    if gate is None and not issues:
        issues.append("output/review-gate.json is missing")
    elif gate is not None and gate.get("ok") is False and not issues:
        issues.extend(str(item) for item in (gate.get("problems") or []))

    result["review_gate"] = gate if gate is not None else {
        "ok": False, "problems": issues}
    if issues:
        if result.get("status") in ("completed", "answered"):
            result["status"] = "partial"
            result["task_status"] = "partial"
        uncertainties = list(result.get("uncertainties") or [])
        existing = {str(item.get("reason")) for item in uncertainties
                    if isinstance(item, dict)}
        for issue in issues[:25]:
            if issue in existing:
                continue
            uncertainties.append({
                "item": "review_gate",
                "reason": issue,
                "impact": "the methods/statistics review gate did not pass",
                "action": "fix the work file or report, rerun the gate, then "
                          "rerun finalize",
            })
            existing.add(issue)
        result["uncertainties"] = uncertainties
        result["uncertainty"] = uncertainties
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    print(json.dumps({
        "command": "finalize_with_gate",
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "gate_ok": (gate or {}).get("ok"),
        "gate_problems": len(issues),
        "deliverable": result.get("deliverable"),
    }, ensure_ascii=False, indent=2))
    return 0 if finalize.returncode == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
