#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entry point for the six-dimension structure/logic review.

Usage:
    python3 main.py --input /input --work /app/work --output /app/output
    python3 main.py run --input /input --work /app/work --output /app/output
    python3 main.py review --input /input --work /app/work --output /app/output
    python3 main.py diagnose --input /input --work /app/work --output /app/output

    # When the model has curated <work>/diagnosis.json (or an output review):
    python3 main.py apply --work /app/work --output /app/output \
        --review /app/output/structure-review.json
    python3 main.py emit --work /app/work --output /app/output \
        --review /app/output/structure-review.json

``run`` inventories files, normalises the contract, diagnoses D1-D6, applies
edits only when the contract requests revision, and writes
``structure-review.json`` + ``result.json``. ``--mode review`` forces
review-only; ``--mode revise`` forces the apply path.

Reliability contract: every normal path (including ``partial`` and
``input_error``) writes ``result.json`` and exits 0. Exit 2 is reserved for a
completely unusable invocation (e.g. the output directory cannot be created).
Stdlib only, no network.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
REQUIRED = ("structure_kit", "inventory", "workorder", "diagnose", "actions",
            "emit")
DIMENSIONS = (
    ("D1", "研究问题与贡献主张"),
    ("D2", "章节结构与叙事顺序"),
    ("D3", "论证链与论据对应"),
    ("D4", "段落关系与衔接"),
    ("D5", "重复与冗余"),
    ("D6", "结论边界与表述尺度"),
)


def _bootstrap_wrapped():
    """Materialise *.py.md track copies into a temp dir for direct runs."""
    if all((HERE / (name + ".py")).is_file() for name in REQUIRED):
        return None
    mirror = Path(tempfile.mkdtemp(prefix="structure-review-scripts-"))
    copied = 0
    for source in HERE.glob("*.py.md"):
        shutil.copy2(source, mirror / source.name[:-3])
        copied += 1
    for source in HERE.glob("*.py"):
        target = mirror / source.name
        if not target.exists():
            shutil.copy2(source, target)
    if copied:
        sys.path.insert(0, str(mirror))
        return mirror
    return None


_MIRROR = _bootstrap_wrapped()
try:  # package mode (python -m scripts.main)
    from . import structure_kit as kit
    from . import inventory as inventory_mod
    from . import workorder as workorder_mod
    from . import diagnose as diagnose_mod
    from . import actions as actions_mod
    from . import emit as emit_mod
except ImportError:  # direct mode (python scripts/main.py)
    import structure_kit as kit
    import inventory as inventory_mod
    import workorder as workorder_mod
    import diagnose as diagnose_mod
    import actions as actions_mod
    import emit as emit_mod


def _load(path, default=None):
    return kit.read_json(path, default=default)


def _case_id(input_dir):
    data = _load(Path(input_dir) / "case.json", default={}) or {}
    return str(data.get("case_id") or "unknown-case")


def emergency_result(output_dir, input_dir, reason, status="partial"):
    """Write an honest, structurally valid result when the pipeline fails."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    case_id = _case_id(input_dir)
    dimensions = [{
        "id": dim, "name": name, "verdict": "not_assessed",
        "findings": 0, "basis": reason,
    } for dim, name in DIMENSIONS]
    review = {
        "schema": "structure-review/v0.7",
        "skill_version": kit.VERSION,
        "case_id": case_id,
        "task_family": "structure-review",
        "status": status,
        "mode": "review_only",
        "manuscript": {},
        "dimensions": dimensions,
        "findings": [],
        "patterns": [],
        "priority_order": [],
        "artifacts": [{"role": "review", "path": "structure-review.json",
                       "format": "json"}],
        "not_assessed": [{"dimension": dim, "reason": reason}
                         for dim, _ in DIMENSIONS],
        "uncertainty": [{
            "item": "pipeline_failure",
            "reason": reason,
            "impact": "no grounded structure finding was produced",
            "next_check": "inspect the manuscript and rerun main.py",
        }],
        "change_log": [{
            "finding_id": "none",
            "action": "未改动原稿；诊断流程未完成",
            "diff": "无",
            "applied": False,
        }],
        "preserved_facts": [],
        "evidence_map": [],
        "limitations": [reason],
    }
    kit.write_json(output_dir / "structure-review.json", review)
    result = {
        "schema": "structure-result/v0.7",
        "skill_version": kit.VERSION,
        "case_id": case_id,
        "task_family": "structure-review",
        "status": status,
        "mode": "review_only",
        "manuscript": {},
        "dimensions": dimensions,
        "findings": [],
        "patterns": [],
        "priority_order": [],
        "artifacts": review["artifacts"],
        "not_assessed": review["not_assessed"],
        "uncertainty": review["uncertainty"],
        "change_log": review["change_log"],
        "coverage": {
            "dimensions_total": len(DIMENSIONS),
            "dimensions_assessed": 0,
            "findings_total": 0,
            "by_severity": {"blocker": 0, "major": 0, "minor": 0},
            "by_dimension": {dim: 0 for dim, _ in DIMENSIONS},
            "apply_requested": 0,
            "apply_logged": 0,
            "before_after_complete": 0,
            "quotes_checked": 0,
            "quotes_found": 0,
            "quotes_unverified": 0,
            "revised_written": False,
        },
        "deliverable": {"path": "structure-review.json", "format": "json",
                        "role": "review"},
        "preserved_facts": [],
        "evidence_map": [],
        "limitations": [reason],
        "output_contract": {},
        "contract_fields": [],
        "revision_requested": False,
    }
    kit.write_json(output_dir / "result.json", result)
    return result


def _manuscript_text(work_dir, inventory):
    manuscript = (inventory or {}).get("manuscript") or {}
    text_path = manuscript.get("text_path") or "manuscript.txt"
    path = Path(work_dir) / text_path
    if path.is_file():
        return kit.read_text_file(path)
    fallback = Path(work_dir) / "manuscript.txt"
    if fallback.is_file():
        return kit.read_text_file(fallback)
    return ""


def _remove_stale_revised(output_dir):
    revised = Path(output_dir) / "revised.md"
    if revised.is_file():
        revised.unlink()


def _emit(review, workorder, work_dir, output_dir, revised_text):
    output_dir = Path(output_dir)
    if revised_text is None:
        _remove_stale_revised(output_dir)
    review_path = Path(work_dir) / "diagnosis-applied.json"
    kit.write_json(review_path, review)
    normalized, result = emit_mod.build(
        review, workorder, output_dir, review_path=review_path,
        revised_path=output_dir / "revised.md")
    return normalized, result


def run_pipeline(args, force_mode=None):
    input_dir = Path(args.input)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    override = None
    if force_mode:
        override = force_mode
    elif args.mode and args.mode != "auto":
        override = args.mode

    inventory = inventory_mod.scan(input_dir, work_dir, args.manuscript)
    workorder = workorder_mod.build(input_dir, work_dir, inventory,
                                    mode_override=override)
    if force_mode == "review" and workorder.get("mode") != "review_only":
        workorder["mode"] = "review_only"
        workorder["revision_requested"] = False
        workorder["mode_basis"] = list(workorder.get("mode_basis") or []) + [
            "command=review forces review-only"]
    review = diagnose_mod.run(work_dir, mode_override=workorder.get("mode"))

    manuscript_text = _manuscript_text(work_dir, inventory)
    map_data = _load(work_dir / "structure-map.json", default={}) or {}
    if review.get("status") == "input_error" or not manuscript_text:
        revised_text = None
        if not review.get("change_log"):
            review["change_log"] = [{
                "finding_id": "none",
                "action": "未改动原稿；输入不可读或缺少稿件",
                "diff": "无",
                "applied": False,
            }]
    else:
        review, revised_text, _ = actions_mod.process(
            review, workorder, manuscript_text, map_data, output_dir, work_dir)

    normalized, result = _emit(review, workorder, work_dir, output_dir,
                               revised_text)
    summary = {
        "command": "run",
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "mode": result.get("mode"),
        "revision_requested": workorder.get("revision_requested"),
        "findings": len(result.get("findings") or []),
        "dimensions": {item.get("id"): item.get("verdict")
                       for item in result.get("dimensions") or []},
        "deliverable": result.get("deliverable"),
        "preserved_facts": len(result.get("preserved_facts") or []),
        "evidence_map": len(result.get("evidence_map") or []),
        "change_log": len(result.get("change_log") or []),
        "review": str(output_dir / "structure-review.json"),
        "result": str(output_dir / "result.json"),
        "workorder": str(work_dir / "workorder.json"),
        "structure_map": str(work_dir / "structure-map.json"),
    }
    return summary, 0


def apply_command(args):
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    workorder = _load(work_dir / "workorder.json", default={}) or {}
    inventory = _load(work_dir / "inventory.json", default={}) or {}
    map_data = _load(work_dir / "structure-map.json", default={}) or {}
    if args.mode and args.mode != "auto":
        workorder["mode"] = "revise" if args.mode == "revise" else "review_only"
        workorder["revision_requested"] = workorder["mode"] == "revise"
        workorder["mode_basis"] = list(workorder.get("mode_basis") or []) + [
            "apply command mode=%s" % args.mode]
    review_path = Path(args.review) if args.review else (
        output_dir / "structure-review.json")
    if not review_path.is_file():
        review_path = work_dir / "diagnosis.json"
    review = _load(review_path)
    if not review:
        emergency_result(output_dir, args.input,
                         "审稿 JSON 不可用：%s" % review_path)
        return {"command": "apply", "status": "partial",
                "result": str(output_dir / "result.json")}, 0
    if not workorder:
        workorder = {
            "case_id": review.get("case_id") or "unknown-case",
            "mode": "review_only",
            "contract": {},
            "contract_fields": [],
            "revision_requested": False,
        }
    manuscript_text = _manuscript_text(work_dir, inventory)
    review, revised_text, report = actions_mod.process(
        review, workorder, manuscript_text, map_data, output_dir, work_dir)
    _, result = _emit(review, workorder, work_dir, output_dir, revised_text)
    summary = {
        "command": "apply",
        "mode": report.get("mode"),
        "requested": report.get("requested"),
        "applied": report.get("applied"),
        "skipped": report.get("skipped"),
        "revised_written": report.get("revised_written"),
        "deliverable": result.get("deliverable"),
        "result": str(output_dir / "result.json"),
    }
    return summary, 0


def emit_command(args):
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    workorder = _load(work_dir / "workorder.json", default={}) or {}
    review_path = Path(args.review) if args.review else (
        work_dir / "diagnosis-applied.json")
    if not review_path.is_file():
        review_path = work_dir / "diagnosis.json"
    review = _load(review_path)
    if not review:
        emergency_result(output_dir, args.input,
                         "审稿 JSON 不可用：%s" % review_path)
        return {"command": "emit", "status": "partial",
                "result": str(output_dir / "result.json")}, 0
    revised_text = None
    revised_path = Path(args.revised) if args.revised else (
        output_dir / "revised.md")
    if revised_path.is_file():
        revised_text = kit.read_text_file(revised_path)
    normalized, result = _emit(review, workorder, work_dir, output_dir,
                               revised_text)
    summary = {
        "command": "emit",
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "deliverable": result.get("deliverable"),
        "result": str(output_dir / "result.json"),
    }
    return summary, 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=False)
    for name in ("run", "review", "diagnose", "inventory", "workorder",
                 "apply", "emit"):
        part = sub.add_parser(name)
        part.add_argument("--input", default="/input")
        part.add_argument("--work", default="/app/work")
        part.add_argument("--output", default="/app/output")
        part.add_argument("--manuscript", default=None)
        part.add_argument("--mode", choices=["auto", "review", "revise"],
                          default="auto")
        part.add_argument("--review", default=None)
        part.add_argument("--revised", default=None)
        part.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"run", "review", "diagnose", "inventory", "workorder",
                "apply", "emit"}
    if not argv or (argv[0].startswith("-") and argv[0] not in commands):
        argv = ["run"] + argv
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inventory":
            result = inventory_mod.scan(args.input, args.work,
                                        args.manuscript)
            if result["status"] != "ok":
                emergency_result(
                    args.output, args.input,
                    "inventory status=%s" % result["status"],
                    status="input_error")
            summary = {
                "command": "inventory",
                "status": result["status"],
                "files": result["file_count"],
                "readable": result["readable_texts"],
                "manuscript": (result.get("manuscript") or {}).get("path"),
            }
            code = 0
        elif args.command == "workorder":
            inventory = _load(Path(args.work) / "inventory.json")
            if not inventory:
                inventory = inventory_mod.scan(args.input, args.work,
                                               args.manuscript)
            override = None if args.mode == "auto" else args.mode
            order = workorder_mod.build(args.input, args.work, inventory,
                                        mode_override=override)
            summary = {
                "command": "workorder",
                "case_id": order["case_id"],
                "mode": order["mode"],
                "genre": order["genre"],
                "mode_basis": order["mode_basis"],
            }
            code = 0
        elif args.command == "diagnose":
            inventory = _load(Path(args.work) / "inventory.json")
            if not inventory:
                inventory = inventory_mod.scan(args.input, args.work,
                                               args.manuscript)
            order = _load(Path(args.work) / "workorder.json")
            if not order:
                override = None if args.mode == "auto" else args.mode
                order = workorder_mod.build(args.input, args.work, inventory,
                                            mode_override=override)
            override = None if args.mode == "auto" else args.mode
            review = diagnose_mod.run(args.work, mode_override=override)
            # Keep the entry self-contained: even the diagnose stage leaves a
            # valid result.json for partial/input_error, overwritten later by
            # apply/emit if the model curates the review.
            _, result = _emit(review, order, args.work, args.output, None)
            summary = {
                "command": "diagnose",
                "status": review["status"],
                "findings": len(review.get("findings") or []),
                "dimensions": {item["id"]: item["verdict"]
                               for item in review.get("dimensions") or []},
                "result": str(Path(args.output) / "result.json"),
            }
            code = 0
        elif args.command == "apply":
            summary, code = apply_command(args)
        elif args.command == "emit":
            summary, code = emit_command(args)
        elif args.command == "review":
            summary, code = run_pipeline(args, force_mode="review")
        else:  # run
            summary, code = run_pipeline(args)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        reason = "流程异常：%s: %s" % (type(exc).__name__, exc)
        try:
            emergency_result(args.output, args.input, reason, status="partial")
        except Exception:  # noqa: BLE001
            print("ERROR: cannot write result.json under %s" % args.output,
                  file=sys.stderr)
            return 2
        summary = {
            "command": args.command,
            "status": "partial",
            "error": reason,
            "result": str(Path(args.output) / "result.json"),
        }
        code = 0
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
