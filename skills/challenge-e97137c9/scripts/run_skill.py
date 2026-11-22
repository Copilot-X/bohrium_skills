#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-command entry point with a deterministic fallback.

Contract:
* Always try to leave a parseable ``output/result.json`` and every declared
  output file, even when the input is incomplete or the model work file is
  missing/empty.
* Exit 0 whenever result.json was written and parsed; exit 2 only when the
  installation itself is unusable (shared scripts missing).

Usage:
    python3 scripts/run_skill.py --input /input --work /app/work --output /app/output
    python3 scripts/run_skill.py ... --refresh   # overwrite the generated work file
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
RUN_CASE = HERE / "run_case.py"
DOMAIN = HERE / "research_profile.py"
if not DOMAIN.is_file():
    DOMAIN = HERE / "domain_work.py"
AUDIT_WORK = HERE / "audit_work.py"
ALLOWED_EMPTY = {
    "uncertainties", "uncertainty", "preserved_facts", "change_log", "conflicts",
    "review_queue", "skipped", "excluded", "suppressed", "residual_misses",
    "unresolved", "gaps", "limitations", "duplicate_groups", "version_groups",
    "unreadable", "version_conflict", "missing_evidence", "alert_log",
}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def norm_key(key):
    return re.sub(r"[^0-9a-z一-鿿]+", "_", str(key or "").lower()).strip("_")


def tokens(text):
    return {x for x in norm_key(text).split("_") if len(x) > 2}


def call(script, argv, timeout=600):
    if not Path(script).is_file():
        return {"ok": False, "returncode": 2, "stdout": "", "stderr": "missing: %s" % script}
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run([sys.executable, "-X", "utf8", str(script)] + list(argv),
                              capture_output=True, text=True, timeout=timeout,
                              env=env, cwd=str(SKILL_DIR))
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-8000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "returncode": 2, "stdout": "",
                "stderr": "%s: %s" % (type(exc).__name__, exc)}


def is_template_work(work, config):
    task = config.get("work_file") or "work/answer.json"
    work_path = Path(str(task))
    if work_path.is_absolute():
        path = work_path
    else:
        # work_file is relative to the skill, but prepare writes its basename under work/.
        path = Path(work) / work_path.name
    payload = read_json(path, {})
    if not isinstance(payload, dict) or not payload:
        return True
    if payload.get("_generated_by"):
        return False
    for field in config.get("required_fields") or []:
        value = payload.get(field)
        if value not in (None, "", [], {}):
            return False
    return not str(payload.get("summary") or "").strip()


def resolve_output(output, name):
    path = Path(str(name or ""))
    if path.is_absolute():
        return path
    parts = path.parts
    if parts and parts[0] == "output":
        return Path(output).joinpath(*parts[1:])
    return Path(output) / path


def collect_declared(config, output):
    paths = []
    for name in config.get("output_files") or []:
        paths.append(resolve_output(output, name))
    deliverable = config.get("deliverable")
    if deliverable:
        path = resolve_output(output, deliverable)
        if path not in paths:
            paths.append(path)
    return paths


def present_value(result, key):
    if key not in result:
        return False
    value = result.get(key)
    if value is None or value == "":
        return False
    if isinstance(value, (list, dict)) and len(value) == 0:
        return key in ALLOWED_EMPTY
    return True


def match_artifact(key, artifacts, output):
    norm = norm_key(key)
    key_tokens = tokens(key)
    for item in artifacts or []:
        path = item.get("path") if isinstance(item, dict) else item
        if not path:
            continue
        stem = norm_key(Path(str(path)).stem)
        if stem == norm or stem in norm or norm in stem:
            full = resolve_output(output, path)
            if full.exists():
                return path
        if key_tokens & tokens(stem):
            full = resolve_output(output, path)
            if full.exists():
                return path
    return None


def patch_contract(result, case, output, work, config):
    contract = case.get("output_contract") or {}
    if isinstance(contract, list):
        contract = {str(key): None for key in contract}
    if not isinstance(contract, dict) or not contract:
        return result
    work_payload = read_json(Path(work) / Path(str(config.get("work_file") or "")).name, {})
    aliases = {
        "experiment_plan": ["plan", "hypothesis", "steps", "parameters"],
        "feasibility_review": ["feasibility"],
        "feasibility_assessment": ["feasibility", "bounded_scenarios"],
        "risk_assessment": ["risks"],
        "confirmation_items": ["confirmation_items"],
        "evidence_record": ["evidence", "evidence_map"],
        "status_note": ["uncertainties"],
        "novelty_assessment": ["novelty_claims"],
        "trend_analysis": ["year_counts", "topic_counts", "representatives"],
        "review_structure": ["review_structure"],
        "screening_protocol": ["protocol", "screening_criteria", "screening_log"],
        "search_protocol": ["protocol", "screening_criteria", "screening_log"],
        "source_registry": ["sources", "records"],
        "主题": ["themes"],
        "综述结构": ["review_structure"],
        "研究空白": ["gap_analysis", "gaps"],
        "问题定义": ["problem_definition"],
        "比较矩阵": ["comparison_matrix"],
        "创新点": ["novelty_claims"],
        "可行性": ["feasibility", "bounded_scenarios"],
        "有界方案": ["bounded_scenarios"],
        "筛选协议": ["protocol", "screening_criteria", "screening_log"],
        "来源清单": ["sources", "records"],
    }
    for key in contract:
        if present_value(result, key):
            continue
        value = None
        if isinstance(work_payload, dict) and present_value(work_payload, key):
            value = work_payload.get(key)
        if value is None:
            value = match_artifact(key, result.get("artifacts") or [], output)
        if value is None:
            norm = norm_key(key)
            for alias in aliases.get(norm, []):
                if present_value(result, alias):
                    value = result.get(alias)
                    break
        if value is None and isinstance(work_payload, dict):
            for alias in aliases.get(norm_key(key), []):
                if present_value(work_payload, alias):
                    value = work_payload.get(alias)
                    break
        if value is None:
            deliverable = result.get("deliverable")
            if isinstance(deliverable, dict) and deliverable.get("exists"):
                value = {"field": key, "status": "partial",
                         "note": "No dedicated field was produced; use the deliverable.",
                         "deliverable": deliverable.get("path")}
        if value is not None:
            result[key] = value
    coverage = []
    for key, spec in contract.items():
        coverage.append({"field": key, "present": present_value(result, key),
                         "spec": spec if isinstance(spec, (str, dict, list)) else str(spec)})
    result["contract_coverage"] = coverage
    return result


def domain_audit(work):
    return read_json(Path(work) / "domain-status.json", {})


def model_authored_since_seed(work, output, config):
    """Do not overwrite model-improved work/outputs on a later wrapper run."""
    status = Path(work) / "domain-status.json"
    if not status.is_file():
        return False
    threshold = status.stat().st_mtime
    work_file = Path(work) / Path(str(config.get("work_file") or "work/answer.json")).name
    candidates = [work_file] + collect_declared(config, output)
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_mtime > threshold + 0.5:
                return True
        except OSError:
            continue
    return False


def patch_status(result, case, audit, config, output):
    status = str(result.get("status") or "partial")
    task_success = str(config.get("status_success") or "completed")
    source_count = audit.get("source_count")
    blocking = int((audit.get("checks") or {}).get("blocking") or 0)
    if source_count == 0:
        result["status"] = "input_error"
        result["task_status"] = "input_error"
        result.setdefault("uncertainties", []).append({
            "item": "input_error", "reason": "No readable material was provided.",
            "impact": "No fact-level feasibility judgement can be made.",
            "action": "Provide the plan/equipment/budget/ethics material and rerun.",
        })
    elif blocking:
        if status == task_success:
            result["status"] = "partial"
            result["task_status"] = "partial"
        result.setdefault("uncertainties", []).append({
            "item": "domain_check", "reason": "%d domain integrity checks failed." % blocking,
            "impact": "The structured result is incomplete or not fully traceable.",
            "action": "Fix the work file and rerun finalize.",
        })
    elif status == "partial" and not result.get("validation_blockers"):
        blockers = [u for u in result.get("uncertainties") or []
                    if isinstance(u, dict)
                    and str(u.get("item")) in ("validation", "deliverable",
                                               "artifact missing", "analysis_execution")]
        coverage_ok = all(row.get("present") for row in result.get("contract_coverage") or [])
        outputs_ok = all(path.exists() for path in collect_declared(config, output))
        if not blockers and coverage_ok and outputs_ok:
            result["status"] = task_success
            result["task_status"] = task_success
    return result


def write_placeholders(config, output, case_id, reason):
    """Last-resort honest placeholders so declared paths never dangle."""
    written = []
    for path in collect_declared(config, output):
        if path.exists() and path.stat().st_size > 0:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix.lower()
        if suffix == ".csv":
            text = "id,title,status,note\n"
        elif suffix in (".md", ".txt"):
            text = ("# Placeholder result\n\n"
                    "No substantive result was produced. reason: %s\n\n"
                    "See result.json for the authoritative status and uncertainties.\n"
                    % reason)
        else:
            text = json.dumps({
                "case_id": case_id, "status": "partial",
                "note": "No substantive result was produced.",
                "reason": reason,
                "authoritative": "result.json",
            }, ensure_ascii=False, indent=2) + "\n"
        path.write_text(text, encoding="utf-8")
        written.append(str(path))
    return written


def validate_result(path):
    payload = read_json(path, None)
    if not isinstance(payload, dict):
        return False, "result.json is not a JSON object"
    if not payload.get("case_id"):
        return False, "result.json has no case_id"
    if not payload.get("status"):
        return False, "result.json has no status"
    return True, "ok"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--refresh", action="store_true",
                        help="regenerate the deterministic work content even if present")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    input_dir = Path(args.input)
    work = Path(args.work)
    output = Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    config = read_json(SKILL_DIR / "task.json", {})
    case = read_json(input_dir / "case.json", {})

    if not RUN_CASE.is_file():
        print(json.dumps({"status": "installation_error",
                          "error": "scripts/run_case.py is missing; install the full package"},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    results = {}
    results["prepare"] = call(RUN_CASE, ["prepare", "--input", str(input_dir),
                                         "--work", str(work), "--output", str(output),
                                         "--skill-dir", str(SKILL_DIR)])
    if DOMAIN.is_file():
        domain_args = ["--input", str(input_dir), "--work", str(work),
                       "--output", str(output)]
        if args.refresh:
            # Remove a generated work file so the seed pass overwrites it.
            task = config.get("work_file") or "work/answer.json"
            path = work / Path(str(task)).name
            existing = read_json(path, {})
            if not existing or existing.get("_generated_by"):
                try:
                    path.unlink()
                except OSError:
                    pass
        if model_authored_since_seed(work, output, config):
            domain_args.append("--fill-missing")
        results["domain"] = call(DOMAIN, domain_args)
    if AUDIT_WORK.is_file():
        results["audit_work"] = call(
            AUDIT_WORK, ["--input", str(input_dir), "--work", str(work),
                         "--output", str(output)])
    results["finalize"] = call(RUN_CASE, ["finalize", "--input", str(input_dir),
                                          "--work", str(work), "--output", str(output),
                                          "--skill-dir", str(SKILL_DIR)])

    missing = [str(path) for path in collect_declared(config, output) if not path.exists()]
    if missing:
        if DOMAIN.is_file():
            results["domain_fill"] = call(
                DOMAIN, ["--input", str(input_dir), "--work", str(work),
                         "--output", str(output), "--fill-missing"])
        results["finalize_fill"] = call(
            RUN_CASE, ["finalize", "--input", str(input_dir), "--work", str(work),
                       "--output", str(output), "--skill-dir", str(SKILL_DIR)])

    placeholders = write_placeholders(
        config, output, str(case.get("case_id") or case.get("id") or "CASE"),
        "The deterministic fallback did not produce this declared output; "
        "inspect work/domain-status.json and run_skill.steps.")
    result_path = output / "result.json"
    result = read_json(result_path, {})
    if not isinstance(result, dict):
        result = {}
    result = patch_contract(result, case, output, work, config)
    if placeholders:
        result.setdefault("uncertainties", []).append({
            "item": "declared_output_placeholder",
            "reason": "Placeholder written for: %s" % ", ".join(placeholders),
            "impact": "The path exists but contains no substantive analysis.",
            "action": "Inspect the domain fallback and rerun finalize.",
        })
        existing_paths = {item.get("path") for item in result.get("artifacts") or []
                          if isinstance(item, dict)}
        for placeholder in placeholders:
            rel = placeholder.replace(str(output) + os.sep, "").replace("\\", "/")
            if rel in existing_paths:
                continue
            result.setdefault("artifacts", []).append({
                "path": rel, "role": "placeholder", "format": Path(placeholder).suffix.lstrip("."),
                "bytes": Path(placeholder).stat().st_size, "kind": "file",
                "exists": True, "description": "honest placeholder; not substantive",
            })
    audit = domain_audit(work)
    result = patch_status(result, case, audit, config, output)
    if results.get("audit_work") and not results["audit_work"].get("ok"):
        if result.get("status") == (config.get("status_success") or "completed"):
            result["status"] = "partial"
            result["task_status"] = "partial"
        result.setdefault("uncertainties", []).append({
            "item": "quality_audit",
            "reason": "audit_work.py failed after the deterministic profile pass",
            "impact": "the structured trend result did not pass the independent recount gate",
            "action": "inspect output/quality-audit.json and correct the work file",
        })
    result.setdefault("output_contract", case.get("output_contract") or {})
    result.setdefault("contract_coverage", [])
    result["profile_summary"] = {
        "source_count": audit.get("source_count"),
        "record_count": audit.get("record_count"),
        "unique_record_count": audit.get("unique_record_count"),
        "evidence_matrix_rows": audit.get("evidence_matrix_rows"),
        "evidence_count": audit.get("evidence_count"),
        "screening_counts": audit.get("screening_counts"),
        "questions_total": audit.get("questions_total"),
        "questions_covered": audit.get("questions_covered"),
        "blocking_checks": audit.get("blocking_checks"),
        "mode": audit.get("mode"),
    }
    result["run_skill"] = {
        "engine": "run_skill.py",
        "generated_at": now_iso(),
        "steps": {name: {"returncode": item.get("returncode"),
                         "ok": item.get("ok")} for name, item in results.items()},
        "fallback": bool(results.get("domain")),
        "declared_outputs": [{"path": str(path), "exists": path.exists(),
                              "bytes": path.stat().st_size if path.exists() else 0}
                             for path in collect_declared(config, output)],
        "contract_present": all(row.get("present")
                                for row in result.get("contract_coverage") or []),
    }
    write_json(result_path, result)

    valid, reason = validate_result(result_path)
    summary = {
        "command": "run_skill",
        "case_id": result.get("case_id") or case.get("case_id"),
        "status": result.get("status"),
        "deliverable": (result.get("deliverable") or {}).get("path")
        if isinstance(result.get("deliverable"), dict) else result.get("deliverable"),
        "declared_missing": missing,
        "contract_coverage": result.get("contract_coverage"),
        "result_valid": valid,
        "result_reason": reason,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if valid else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "runner_error",
                          "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False), file=sys.stderr)
        # Even an unexpected runner failure should not fabricate a result.
        sys.exit(2)
