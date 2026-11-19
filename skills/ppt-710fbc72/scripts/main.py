#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entrypoint: paper/material + real PPTX template -> editable deck.

Usage:
    python3 scripts/main.py --input /input --work /app/work --output /app/output
    python3 scripts/main.py ... --stage plan        # audit + skeleton spec only
    python3 scripts/main.py ... --spec /app/work/deck-spec.json
    python3 scripts/main.py ... --no-render

Normal paths (answered / partial / input_error) always write
``<output>/result.json`` and exit 0.  Exit 2 is reserved for an unwritable
output directory.  The model writes scientific content in ``deck-spec.json``;
this entrypoint only plans, assembles into real template layouts, verifies and
reports.  It never substitutes fonts silently and never overwrites slide text.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIRED = ("deckkit", "spec", "verify", "reports")


def _bootstrap_wrapped():
    """Materialise *.py.md track copies into a temp dir for direct runs."""
    if all((HERE / (name + ".py")).is_file() for name in REQUIRED):
        return None
    mirror = Path(tempfile.mkdtemp(prefix="deck-adapt-scripts-"))
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
    from . import deckkit
    from . import spec
    from . import verify
    from . import reports
except ImportError:  # direct mode (python scripts/main.py)
    import deckkit
    import spec
    import verify
    import reports


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def _safe_case(discovery):
    return discovery.get("case") or {}


def _write_result(output_dir, result):
    try:
        write_json(Path(output_dir) / "result.json", result)
    except OSError:
        pass


def _failure_result(case, case_id, status, code, detail):
    now = utcnow()
    official = ("status", "editable_source", "export_artifact", "content_map",
                "compliance_checks", "exceptions")
    result = {}
    for key, value in (case or {}).items():
        if key not in official:
            result[key] = value
    result.update({
        "case_id": case_id or case.get("case_id") or case.get("id") or "case",
        "task_family": "template-adaptation",
        "status": status,
        "task_status": {"answered": "completed", "partial": "partial",
                        "input_error": "input_error"}.get(status, "partial"),
        "editable_source": None,
        "export_artifact": {"path": None, "format": None, "native": False,
                            "status": "unavailable",
                            "limitation": detail},
        "content_map": [],
        "compliance_checks": {
            "pptx_valid": False, "template_fidelity": "none",
            "placeholder_used": False, "export_signature": "none",
            "official_fields_present": True,
        },
        "exceptions": [{"code": code, "severity": status, "detail": detail}],
        "checks": {"zip_valid": False, "render_ok": False},
        "render": {"engine": "none", "native": False, "unavailable": True,
                   "limitation": detail},
        "artifacts": [],
        "generated_at": now,
    })
    return result


def _stage_plan(discovery, workorder, constraints, audit, plan, work_dir,
                output_dir):
    work_dir, output_dir = Path(work_dir), Path(output_dir)
    write_json(work_dir / "workorder.json", workorder)
    write_json(work_dir / "template-audit.json", audit)
    write_json(work_dir / "ppt-format-spec.json", plan["format_spec"])
    write_json(work_dir / "layout-map.json", plan["layout_map"])
    if plan["mode"] == "build" and plan.get("missing_spec"):
        skeleton = spec.skeleton_spec(constraints, workorder)
        write_json(work_dir / "deck-spec.skeleton.json", skeleton)
    return work_dir


def _build_deck(discovery, plan, workorder, constraints, output_dir):
    output_dir = Path(output_dir)
    deck_path = output_dir / "presentation.pptx"
    template_path = discovery.get("template")
    if not template_path:
        raise ValueError("no target template was found")
    if plan["mode"] == "adapt":
        deck_source = discovery.get("deck")
        if not deck_source:
            raise ValueError("adapt mode requires a starting deck")
        report = deckkit.retarget_deck(
            deck_source, template_path, plan["layout_map"],
            deck_path, edits=workorder.get("edits") or [])
        return deck_path, report, deck_source
    slides = plan.get("slides") or []
    if not slides:
        skeleton = spec.skeleton_spec(constraints, workorder)
        slides = spec.validate_deck_spec(skeleton, [output_dir])["slides"]
        plan["missing_spec"] = True
    deck_spec = {"title": plan.get("deck_title") or constraints.get("title"),
                 "slides": slides}
    report = deckkit.build_from_spec(
        template_path, deck_spec, plan["format_spec"], deck_path)
    return deck_path, report, None


def _slide_content(deck_path, plan):
    if plan["mode"] == "adapt" and deck_path:
        try:
            parts = deckkit.read_parts(deck_path)
            return [deckkit.extract_slide_content(parts, part)
                    for part in deckkit.ordered_slides(parts)]
        except (OSError, ValueError, KeyError):
            return []
    return []


def _empty_verify():
    return {
        "ok": False, "errors": [], "warnings": [], "exceptions": [],
        "checks": {"zip_valid": False, "xml_valid": False,
                   "relationships_resolved": False,
                   "template": {"master": False, "layouts": False,
                                "theme": False, "slide_size": False},
                   "page_count_match": False, "placeholders_ok": False,
                   "fonts_ok": False, "sizes_ok": False, "colors_ok": False,
                   "images_ok": False, "content_ok": False,
                   "overflow_ok": False, "notes_ok": False},
        "slide_count": 0, "layouts": [], "placeholders": [],
        "fonts": {"used": [], "theme": [], "outside_template": []},
        "colors": {"outside_theme": []},
        "content_retention": {"ratio": 1.0, "missing_numbers": [],
                              "must_contain": []},
        "images": [], "overflow_risks": [], "out_of_bounds": [],
        "title_hierarchy": {}, "notes": {"slides_with_notes": 0,
                                          "slide_count": 0},
        "fidelity": {}, "fidelity_detail": {}, "slide_size": None,
    }


def _empty_render(limitation):
    return {"engine": "none", "native": False, "unavailable": True,
            "pdf": None, "png": [], "limitation": limitation,
            "duration_seconds": 0}


def run(args):
    output_dir = Path(args.output)
    work_dir = Path(args.work)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"cannot create output/work directory: {exc}", file=sys.stderr)
        return 2

    started_at = utcnow()
    started = time.time()
    stages = {}
    discovery = {"case": {}, "warnings": []}
    case = {}
    case_id = "case"
    try:
        stage_start = time.time()
        discovery = spec.discover(args.input, work_dir, args.spec)
        discovery["work_dir"] = str(work_dir)
        case = _safe_case(discovery)
        workorder = spec.normalize_workorder(discovery)
        case_id = workorder["case_id"]
        constraints = spec.constraints_from(discovery, workorder)
        stages["discover_seconds"] = round(time.time() - stage_start, 2)
        write_json(work_dir / "workorder.json", workorder)

        template_path = discovery.get("template")
        deck_path_input = discovery.get("deck")
        if not template_path:
            detail = ("No target .pptx/.potx template was found. Expected "
                      "workorder.target_template, case.template, or a file whose "
                      "name contains 'template'.")
            result = _failure_result(case, case_id, "input_error",
                                     "NO_TARGET_TEMPLATE", detail)
            _write_result(output_dir, result)
            return 0

        stage_start = time.time()
        try:
            audit = deckkit.audit_template(template_path)
        except (OSError, ValueError, KeyError) as exc:
            detail = f"Target template is unreadable: {type(exc).__name__}: {exc}"
            result = _failure_result(case, case_id, "input_error",
                                     "TEMPLATE_UNUSABLE", detail)
            _write_result(output_dir, result)
            return 0
        stages["audit_seconds"] = round(time.time() - stage_start, 2)
        if not audit.get("ok"):
            detail = "Template audit failed: " + "; ".join(audit.get("errors") or [])
            result = _failure_result(case, case_id, "input_error",
                                     "TEMPLATE_UNUSABLE", detail)
            _write_result(output_dir, result)
            return 0

        stage_start = time.time()
        plan = spec.build_plan(audit, discovery, workorder, constraints)
        stages["plan_seconds"] = round(time.time() - stage_start, 2)
        write_json(work_dir / "template-audit.json", audit)
        write_json(work_dir / "ppt-format-spec.json", plan["format_spec"])
        write_json(work_dir / "layout-map.json", plan["layout_map"])

        if args.stage == "plan":
            _stage_plan(discovery, workorder, constraints, audit, plan, work_dir,
                        output_dir)
            empty_verify = _empty_verify()
            empty_render = _empty_render(
                "stage=plan: deck not built yet; run the same command without "
                "--stage plan after writing work/deck-spec.json")
            content_rows = reports.content_map(plan, [], workorder, None)
            reports.write_records(
                work_dir, case_id, plan, empty_verify, empty_render,
                content_rows, plan.get("warnings") or [], started_at, utcnow(),
                stages, workorder)
            reports.mirror_work(work_dir, output_dir)
            write_json(output_dir / "format-check.json", empty_verify)
            result = reports.build_result(
                case, workorder, audit, plan, empty_verify, empty_render,
                output_dir, work_dir, None, template_path, None, content_rows,
                plan.get("warnings") or [], plan.get("exceptions") or [],
                started_at, utcnow(), stages, stage="plan",
                forced_status="partial")
            _write_result(output_dir, result)
            print(json.dumps({"status": result["status"], "stage": "plan",
                              "mode": plan["mode"],
                              "layouts": len(audit.get("layouts") or []),
                              "skeleton": plan.get("missing_spec", False),
                              "result": str(output_dir / "result.json")},
                             ensure_ascii=False))
            return 0

        stage_start = time.time()
        deck_path, adapt_report, baseline_path = _build_deck(
            discovery, plan, workorder, constraints, output_dir)
        stages["build_seconds"] = round(time.time() - stage_start, 2)
        write_json(work_dir / "adapt-report.json", adapt_report)

        stage_start = time.time()
        verify_report = verify.run(
            deck_path, template_path, plan.get("format_spec"),
            baseline_path=baseline_path,
            plan=plan,
            strict_sizes=(plan.get("mode") == "build"),
            edits=workorder.get("edits") or [])
        stages["verify_seconds"] = round(time.time() - stage_start, 2)
        write_json(work_dir / "format-check.json", verify_report)
        write_json(output_dir / "format-check.json", verify_report)

        stage_start = time.time()
        if args.no_render:
            render_report = _empty_render(
                "render disabled by --no-render; PPTX only passed structural checks")
        else:
            render_report = deckkit.render_preview(
                deck_path, output_dir / "preview", timeout=args.render_timeout)
        stages["render_seconds"] = round(time.time() - stage_start, 2)

        content_rows = reports.content_map(
            plan, _slide_content(deck_path, plan), workorder, baseline_path)
        changes = list(adapt_report.get("change_log") or [])
        changes.extend(verify_report.get("warnings") or [])
        extra_exceptions = []
        for item in (adapt_report.get("edits") or {}).get("unmatched") or []:
            extra_exceptions.append({
                "code": "EDIT_NOT_APPLIED", "severity": "warning",
                "detail": item})
        for warning in discovery.get("warnings") or []:
            extra_exceptions.append({"code": "INPUT_WARNING", "severity": "info",
                                     "detail": warning})
        if not discovery.get("workorder_path"):
            extra_exceptions.append({
                "code": "WORKORDER_MISSING", "severity": "info",
                "detail": "no slide-workorder.json found; mode inferred from files"})
        reports.write_records(
            work_dir, case_id, plan, verify_report, render_report, content_rows,
            changes, started_at, utcnow(), stages, workorder)
        reports.mirror_work(work_dir, output_dir)
        result = reports.build_result(
            case, workorder, audit, plan, verify_report, render_report,
            output_dir, work_dir, deck_path, template_path, baseline_path,
            content_rows, changes, extra_exceptions, started_at, utcnow(),
            stages, stage="build")
        _write_result(output_dir, result)
        print(json.dumps({
            "status": result["status"], "task_status": result["task_status"],
            "mode": plan["mode"], "slides": verify_report.get("slide_count"),
            "fidelity": (result.get("compliance_checks") or {}).get(
                "template_fidelity"),
            "render_native": (result.get("render") or {}).get("native"),
            "exceptions": len(result.get("exceptions") or []),
            "result": str(output_dir / "result.json"),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 - entrypoint must always emit result
        detail = f"{type(exc).__name__}: {exc}"
        trace = traceback.format_exc(limit=4)
        status = "input_error" if not discovery.get("template") else "partial"
        result = _failure_result(case, case_id, status, "PIPELINE_ERROR", detail)
        result["exceptions"][0]["trace"] = trace
        result["checks"] = {"zip_valid": False, "pipeline_error": True}
        _write_result(output_dir, result)
        print(json.dumps({"status": status, "error": detail,
                          "result": str(output_dir / "result.json")},
                         ensure_ascii=False), file=sys.stderr)
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="Adapt an academic deck to a real PowerPoint template")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--spec", default=None,
                        help="model-written deck spec (default: <work>/deck-spec.json)")
    parser.add_argument("--stage", choices=("plan", "build"), default="build")
    parser.add_argument("--no-render", action="store_true",
                        help="skip native PDF/PNG preview; result becomes partial")
    parser.add_argument("--render-timeout", type=int, default=180)
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
