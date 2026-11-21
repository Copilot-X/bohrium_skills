#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guarantee a parseable result.json after a failed/aborted finalize.

This is a *safety net*, not a second writer of scientific content.  It only
reports files that really exist and marks the result ``partial`` (or
``input_error`` when no readable input was found).  It never invents
sections, citations or scores.

Usage:
    python3 scripts/fallback_result.py --input /input --work /app/work --output /app/output
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return default


def load_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def valid_existing(path):
    data = load_json(path, None)
    if not isinstance(data, dict):
        return None
    if not data.get("case_id") or not data.get("status"):
        return None
    return data


def rel(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def build_result(input_dir, work_dir, output_dir):
    input_dir, work_dir, output_dir = Path(input_dir), Path(work_dir), Path(output_dir)
    case = load_json(input_dir / "case.json", {})
    task_text = read_text(input_dir / "task.md")
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    contract = case.get("output_contract") or {}
    intro = read_text(output_dir / "introduction.md")
    related = read_text(output_dir / "related-work.md")
    combined = read_text(output_dir / "sections.md")
    if not combined and (intro or related):
        combined = "# Introduction\n\n%s\n\n# Related Work\n\n%s\n" % (
            intro.strip(), related.strip())
        (output_dir / "sections.md").write_text(combined, encoding="utf-8")
    readable = bool(task_text.strip() or any(
        (input_dir / name).exists() for name in ("case.json",)))
    if combined.strip():
        readable = True
    status = "partial" if readable else "input_error"
    deliverable = None
    if (output_dir / "sections.md").is_file():
        path = output_dir / "sections.md"
        deliverable = {
            "path": rel(path, output_dir),
            "format": "md",
            "exists": True,
            "bytes": path.stat().st_size,
        }
    artifacts = []
    for name in ("introduction.md", "related-work.md", "sections.md",
                 "citation-audit.json", "contributions.json", "evidence-map.json",
                 "self-check.json", "sections.docx", "sections-preview.pdf"):
        path = output_dir / name
        if path.is_file():
            artifacts.append({
                "path": rel(path, output_dir),
                "role": "deliverable" if name == "sections.md" else "artifact",
                "format": path.suffix.lstrip("."),
                "bytes": path.stat().st_size,
                "kind": "file",
                "exists": True,
                "description": "fallback registration of an existing file",
            })
    evidence_map = []
    emap = load_json(output_dir / "evidence-map.json", {})
    if isinstance(emap, dict) and isinstance(emap.get("rows"), list):
        evidence_map = emap["rows"]
    elif isinstance(emap, list):
        evidence_map = emap
    plan = load_json(work_dir / "writing-plan.json", {})
    if not evidence_map and isinstance(plan, dict):
        for row in plan.get("citation_coverage") or []:
            if isinstance(row, dict):
                evidence_map.append({
                    "claim_id": row.get("claim_id") or row.get("key") or "",
                    "claim": row.get("claim") or "",
                    "source": {"path": "bibliography",
                               "locator": row.get("key") or ""},
                    "quote": "",
                    "supports": row.get("used_in") or "citation-coverage",
                })
    uncertainties = [{
        "item": "fallback_result",
        "reason": "run_case finalize did not leave a parseable result.json",
        "impact": "the result is partial; existing artifacts are listed but not re-audited",
        "action": "inspect the finalize error, repair inputs/sections, rerun finalize",
    }]
    if not combined.strip():
        uncertainties.append({
            "item": "deliverable",
            "reason": "no introduction/related-work/sections file was written",
            "impact": "no editable manuscript is delivered",
            "action": "write the two sections and rerun finalize",
        })
    result = {
        "case_id": case_id,
        "task_family": "introduction-writing",
        "status": status,
        "task_status": status,
        "profile": "writing",
        "result": {
            "summary": "Fallback result: %s characters of editable sections; no re-audit."
                       % len(combined),
            "fallback": True,
            "sections_chars": len(combined),
        },
        "claims": [],
        "uncertainties": uncertainties,
        "uncertainty": uncertainties,
        "sources": [],
        "evidence": evidence_map,
        "evidence_map": evidence_map,
        "preserved_facts": [],
        "change_log": [{
            "step": 1,
            "action": "fallback_result",
            "scope": "output/result.json",
            "reason": "finalize produced no parseable result.json",
            "evidence": "scripts/fallback_result.py",
            "human_review": False,
            "at": now_iso(),
        }],
        "artifacts": artifacts,
        "deliverable": deliverable,
        "output_contract": contract,
        "contract_coverage": [],
        "artifact_paths_valid": all(a.get("exists") for a in artifacts),
        "engine_version": "fallback-1.0.0",
        "generated_at": now_iso(),
    }
    if isinstance(contract, dict):
        for key in contract:
            value = result.get(key)
            present = key in result and value is not None and value != ""
            if not present:
                norm = re.sub(r"[^0-9a-z一-鿿]+", "_", str(key).lower()).strip("_")
                for artifact in artifacts:
                    stem = re.sub(r"[^0-9a-z一-鿿]+", "_",
                                  Path(artifact["path"]).stem.lower()).strip("_")
                    if stem and (stem == norm or stem in norm or norm in stem):
                        result[key] = artifact["path"]
                        present = True
                        break
            result["contract_coverage"].append({
                "field": key,
                "present": present,
                "spec": contract.get(key),
            })
    return result


def register_artifact(data, path, output_dir, role="artifact"):
    exists = path.is_file()
    rel_path = rel(path, output_dir)
    for item in data.get("artifacts") or []:
        if item.get("path") == rel_path:
            if exists:
                item["exists"] = True
                item["bytes"] = path.stat().st_size
            return item
    item = {
        "path": rel_path,
        "role": role,
        "format": path.suffix.lstrip(".") or "file",
        "bytes": path.stat().st_size if exists else 0,
        "kind": "file",
        "exists": exists,
        "description": "real file registered by result_guard",
    }
    data.setdefault("artifacts", []).append(item)
    return item


def postprocess(data, input_dir, output_dir):
    """Cover output_contract keys with real artifacts and recheck status.

    fam_intro does not scan arbitrary output files, so the guard registers
    only files that exist and maps a contract key to ``output/results/<key>.json``
    (or ``output/<key>.json``).  Missing keys are reported honestly and make
    the result ``partial``; nothing is fabricated.
    """
    output_dir = Path(output_dir)
    case = load_json(Path(input_dir) / "case.json", {})
    contract = case.get("output_contract") or data.get("output_contract") or {}
    if not isinstance(contract, dict):
        contract = {}
    results_dir = output_dir / "results"
    if results_dir.is_dir():
        for path in sorted(results_dir.rglob("*")):
            if path.is_file():
                register_artifact(data, path, output_dir)
    for key in contract:
        candidates = [results_dir / (key + ".json"), results_dir / key,
                      output_dir / (key + ".json"), output_dir / key]
        found = next((p for p in candidates if p.is_file()), None)
        if found is not None:
            data[key] = rel(found, output_dir)
            register_artifact(data, found, output_dir)
    coverage = []
    for key, spec in contract.items():
        value = data.get(key)
        present = key in data and value is not None and value != ""
        if not present:
            norm = re.sub(r"[^0-9a-z一-鿿]+", "_", str(key).lower()).strip("_")
            for artifact in data.get("artifacts") or []:
                stem = re.sub(r"[^0-9a-z一-鿿]+", "_",
                              Path(str(artifact.get("path"))).stem.lower()).strip("_")
                if stem and (stem == norm or stem in norm or norm in stem):
                    data[key] = artifact.get("path")
                    present = True
                    break
        coverage.append({"field": key, "present": present, "spec": spec})
    data["contract_coverage"] = coverage
    data["artifact_paths_valid"] = all(
        item.get("exists") for item in data.get("artifacts") or [])
    uncovered = [row["field"] for row in coverage if not row["present"]]
    if uncovered:
        data["status"] = "partial"
        data["task_status"] = "partial"
        uncertainties = data.setdefault("uncertainties", [])
        message = "output_contract fields not covered: %s" % ", ".join(uncovered)
        if not any(item.get("item") == "output_contract" for item in uncertainties
                   if isinstance(item, dict)):
            uncertainties.append({
                "item": "output_contract",
                "reason": message,
                "impact": "the original output_contract is not fully covered",
                "action": "write output/results/<field>.json or map the contract key",
            })
        data["uncertainty"] = uncertainties
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description="write/repair a safe result.json")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing valid result.json")
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    existing = valid_existing(output / "result.json")
    if existing is not None and not args.force:
        payload = postprocess(existing, args.input, args.output)
        write_json(output / "result.json", payload)
        print(json.dumps({
            "status": payload.get("status"),
            "result": str(output / "result.json"),
            "action": "postprocessed existing result.json (contract/artifacts)",
            "uncovered": [row["field"] for row in payload.get("contract_coverage", [])
                          if not row.get("present")],
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        payload = build_result(args.input, args.work, args.output)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "case_id": "CASE",
            "status": "input_error",
            "task_status": "input_error",
            "profile": "writing",
            "result": {"summary": "fallback builder failed: %s" % exc},
            "uncertainties": [{"item": "fallback_error", "reason": str(exc),
                               "impact": "no substantive result",
                               "action": "inspect the run log"}],
            "uncertainty": [{"item": "fallback_error", "reason": str(exc),
                             "impact": "no substantive result",
                             "action": "inspect the run log"}],
            "evidence_map": [],
            "evidence": [],
            "preserved_facts": [],
            "change_log": [],
            "artifacts": [],
            "deliverable": None,
            "output_contract": load_json(Path(args.input) / "case.json", {}).get("output_contract") or {},
            "contract_coverage": [],
            "generated_at": now_iso(),
        }
    payload = postprocess(payload, args.input, args.output)
    write_json(output / "result.json", payload)
    print(json.dumps({
        "status": payload.get("status"),
        "result": str(output / "result.json"),
        "deliverable": payload.get("deliverable"),
        "artifacts": [a.get("path") for a in payload.get("artifacts") or []],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
