#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entrypoint: discover -> plan -> build -> preview -> checks -> gold.

Contract:
  * always writes <output>/result.json, including partial/input_error runs;
  * exits 0 for every handled outcome; exits 2 only when the output directory
    itself cannot be created/written (the process cannot start).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build          # noqa: E402
import checks         # noqa: E402
import deckkit        # noqa: E402
import discover       # noqa: E402
import gold           # noqa: E402


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel(path, base):
    try:
        return pathlib_rel(path, base)
    except Exception:
        return str(path)


def pathlib_rel(path, base):
    from os.path import relpath
    if not path:
        return None
    try:
        return relpath(str(path), str(base)).replace("\\", "/")
    except ValueError:
        return str(path)


def dump_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    return str(path)


def load_raw_case(input_dir):
    case = discover.load_json(Path(input_dir) / "case.json")
    return case if isinstance(case, dict) else {}


def base_result(case, case_id):
    result = json.loads(json.dumps(case, ensure_ascii=False)) if isinstance(case, dict) else {}
    result.setdefault("case_id", case_id or "unknown")
    return result


def write_result(out_dir, result):
    return dump_json(Path(out_dir) / "result.json", result)


def run(args):
    t0 = time.time()
    input_dir = Path(args.input)
    out_dir = Path(args.output)
    work_dir = Path(args.work)
    for d in (out_dir, work_dir, out_dir / "preview", work_dir / "figures"):
        d.mkdir(parents=True, exist_ok=True)
    stages = {}

    def mark(name):
        stages[name] = round(time.time() - t0, 3)

    raw_case = load_raw_case(input_dir)
    brief = discover.normalize(
        input_dir, work_dir,
        spec_path=args.spec, template_arg=args.template,
        starting_arg=args.starting_deck, slide_count_arg=args.slides)
    mark("discover")
    case_id = (brief.get("case_id") or raw_case.get("case_id")
               or raw_case.get("id") or "unknown")
    result = base_result(raw_case, case_id)

    # Evidence pack is written before generation so a model can author a spec.
    pack = {
        "case_id": case_id,
        "mode": brief.get("mode"), "mode_reason": brief.get("mode_reason"),
        "task": brief.get("task"), "constraints": brief.get("constraints"),
        "slide_count": brief.get("slide_count"), "aspect": brief.get("aspect"),
        "language": brief.get("language"), "audience": brief.get("audience"),
        "duration_min": brief.get("duration_min"),
        "template": brief.get("template"), "starting_deck": brief.get("starting_deck"),
        "workorder": brief.get("workorder"), "edits": brief.get("edits"),
        "sources": [{"path": s["path"], "kind": s["kind"], "chars": s["chars"],
                     "sha256": s["sha256"],
                     "locators": [p for p, _ in s["pages"]][:80]}
                    for s in brief.get("sources", [])],
        "evidence": brief.get("evidence", [])[:300],
        "figures": brief.get("figures", []),
        "tables": brief.get("tables", []),
        "warnings": brief.get("warnings", []),
    }
    dump_json(work_dir / "evidence-pack.json", pack)
    mark("evidence_pack")

    # ---------- choose the deck spec (model-authored first) ----------
    authored_path = None
    for candidate in (args.spec, work_dir / "deck-spec.json",
                      Path(args.input) / "deck-spec.json",
                      Path(args.input) / "artifacts" / "deck-spec.json"):
        if candidate and Path(candidate).is_file():
            authored_path = str(candidate)
            break
    if brief.get("errors") and not authored_path:
        return input_error(out_dir, result, brief, stages, t0)
    parts = build.load_parts()
    spec, spec_source = None, None
    if authored_path:
        loaded = build.load_spec(authored_path)
        if loaded:
            spec, spec_source = loaded, "model-authored"
    if spec is None:
        spec, spec_source = (build.auto_plan(brief, work_dir, parts),
                             "source-grounded auto-plan")
    spec, spec_issues, spec_warnings, notes_added = build.normalize_spec(
        spec, brief, authored_path, parts=parts)
    mark("plan")
    if spec is None:
        return input_error(out_dir, result, brief, stages, t0,
                           extra="deck spec could not be normalized")
    dump_json(work_dir / "deck-spec.effective.json", spec)

    # ---------- build / edit / adapt ----------
    pptx = out_dir / "presentation.pptx"
    build_report, edit_report = {}, None
    mode = brief.get("mode") or "generate"
    base_deck = brief.get("starting_deck")
    template = brief.get("template")
    if mode == "edit" and not (base_deck and Path(base_deck).is_file()):
        brief.setdefault("warnings", []).append(
            "edit mode selected but no readable starting deck; generated a new deck instead")
        mode = "generate"
    if mode == "adapt" and not (template and Path(template).is_file()):
        brief.setdefault("warnings", []).append(
            "adapt mode selected but no readable template; generated a standalone deck")
        mode = "generate"
    effective_spec = spec
    if mode == "edit" and base_deck and Path(base_deck).is_file():
        edit_report = deckkit.apply_edits(base_deck, brief.get("edits") or [],
                                          pptx, work_dir)
        if edit_report.get("problems") or not Path(pptx).is_file():
            edit_report["unresolved"].append({
                "edit_id": "build", "reason": "edited deck failed structural validation"})
        build_report = {"template_fidelity": "starting-deck-preserved",
                        "images_missing": [], "slides": deckkit.inspect_deck(pptx).get("count", 0),
                        "path": str(pptx), "bytes": Path(pptx).stat().st_size if Path(pptx).is_file() else 0,
                        "sha256": deckkit.sha256_file(pptx) if Path(pptx).is_file() else None,
                        "layouts_used": [], "images_embedded": []}
        if spec_source != "model-authored":
            effective_spec = deckkit.deck_to_spec(pptx, work_dir, template)
            effective_spec = build.normalize_spec(
                effective_spec, brief, None, parts=parts)[0] or effective_spec
            effective_spec.setdefault("planning", {})["origin"] = "starting-deck-content"
            spec_source = "starting-deck-content"
        # attach edit sources to the page evidence of affected slides
        used = {int(e.get("slide")) for e in edit_report.get("applied", []) if e.get("slide")}
        for i, slide in enumerate(effective_spec.get("slides", []), start=1):
            slide["_index"] = i
            if i not in used:
                continue
            applied = next((e for e in edit_report["applied"]
                            if e.get("slide") == i and e.get("source")), None)
            if not applied:
                continue
            source = applied["source"]
            if applied.get("action") == "replace":
                slide["source_ref"] = source
                slide.setdefault("claims", []).append(
                    {"claim": applied.get("after", "") or slide.get("title", ""),
                     "source": source})
            else:
                # notes/image edits do not replace the page's content evidence
                slide["notes_source"] = source
    elif template and mode in ("adapt", "generate"):
        if spec_source != "model-authored" and base_deck and Path(base_deck).is_file():
            effective_spec = deckkit.deck_to_spec(base_deck, work_dir, template)
            effective_spec = build.normalize_spec(
                effective_spec, brief, None, parts=parts)[0] or effective_spec
            effective_spec.setdefault("planning", {})["origin"] = "content-preserved-from-base"
            spec_source = "content-preserved-from-base"
        build_report = deckkit.build_deck(effective_spec, pptx, template=template,
                                          work_dir=work_dir)
        if build_report.get("problems"):
            first_problems = build_report["problems"]
            fallback = deckkit.build_deck(effective_spec, pptx, template=None,
                                          work_dir=work_dir)
            fallback["template_fidelity"] = "none"
            fallback["template_fallback_reason"] = first_problems[:5]
            build_report = fallback
    else:
        build_report = deckkit.build_deck(effective_spec, pptx, template=None,
                                          work_dir=work_dir)
        if mode == "adapt" and not template:
            build_report.setdefault("warnings", []).append(
                "adapt requested but no readable template; generated standalone deck")
    mark("build")

    # ---------- preview ----------
    export_report = deckkit.export_preview(
        pptx, out_dir / "preview", effective_spec,
        work_dir=work_dir, allow_native=not args.no_native)
    mark("export")

    # ---------- checks / gold ----------
    expected = (brief.get("slide_count")
                or (effective_spec.get("planning") or {}).get("requested_slides"))
    if mode == "edit":
        expected = len(effective_spec.get("slides", []))
    check_report = checks.run_checks(
        pptx, effective_spec, brief, build_report, export_report,
        edit_report=edit_report, expected_slides=expected, work_dir=work_dir,
        parts=parts)
    mark("checks")
    inspected = check_report.get("inspected") or {}

    content_map = []
    for i, slide in enumerate(effective_spec.get("slides", []), start=1):
        claims = slide.get("claims") or []
        if not claims and slide.get("source_ref"):
            claims = [{"claim": slide.get("title", ""), "source": slide["source_ref"]}]
        for claim in claims:
            src = (claim or {}).get("source") or {}
            if src.get("path") and src.get("locator"):
                content_map.append({"slide": i, "role": slide.get("role"),
                                    "claim": str(claim.get("claim", ""))[:300],
                                    "maps_to": {"path": src["path"], "locator": src["locator"]}})
        if slide.get("figure_source"):
            src = slide["figure_source"]
            content_map.append({"slide": i, "role": slide.get("role"),
                                "claim": f"figure: {Path(str(slide.get('image', ''))).name}",
                                "maps_to": {"path": src.get("path"), "locator": src.get("locator")}})
    gold_doc, diff_doc = gold.build_gold(effective_spec, brief, inspected, content_map)
    check_report.setdefault("checks", {})["gold_pass_rate"] = diff_doc["summary"]["pass_rate"]
    check_report.setdefault("compliance_checks", {})["gold_pass_rate"] = \
        diff_doc["summary"]["pass_rate"]
    if diff_doc["summary"]["failed"]:
        check_report.setdefault("exceptions", []).append({
            "code": "gold_diff_failed", "severity": "warning",
            "detail": f"{diff_doc['summary']['failed']} page(s) differ from the page contract"})
    mark("gold")

    # ---------- records ----------
    page_evidence = []
    for i, slide in enumerate(effective_spec.get("slides", []), start=1):
        page_evidence.append({
            "slide": i, "kind": slide.get("role") or slide.get("kind"),
            "title": str(slide.get("title", "")),
            "claims": slide.get("claims") or [],
            "source": slide.get("source_ref"),
            "figure": slide.get("figure_source") or None,
            "notes": bool(str(slide.get("notes") or "").strip()),
        })
    plan_doc = {
        "case_id": case_id, "mode": mode, "content_origin": spec_source,
        "requested_slides": brief.get("slide_count"),
        "built_slides": len(effective_spec.get("slides", [])),
        "outline": [{"slide": i, "role": s.get("role"), "kind": s.get("kind"),
                     "title": s.get("title"), "source": s.get("source")}
                    for i, s in enumerate(effective_spec.get("slides", []), start=1)],
        "deck_spec": effective_spec,
        "constraints": brief.get("constraints"),
        "spec_issues": spec_issues, "spec_warnings": spec_warnings,
        "notes_filled_from_slide": notes_added,
    }
    claim_by_slide = []
    for i, slide in enumerate(effective_spec.get("slides", []), start=1):
        claim_by_slide.append({"slide": i, "role": slide.get("role"),
                               "title": slide.get("title"),
                               "claims": slide.get("claims") or [],
                               "figure_source": slide.get("figure_source")})
    change_log = [{"at": now(), "action": "discover", "detail": f"mode={mode}"},
                  {"at": now(), "action": "plan",
                   "detail": f"{spec_source}; {len(effective_spec.get('slides', []))} slides"}]
    if edit_report is not None:
        for applied in edit_report.get("applied", []):
            change_log.append({"at": now(), "action": "edit", "detail": applied})
        for unresolved in edit_report.get("unresolved", []):
            change_log.append({"at": now(), "action": "edit_unresolved", "detail": unresolved})
    else:
        change_log.append({"at": now(), "action": "build",
                           "detail": f"template_fidelity={build_report.get('template_fidelity')}"})
    change_log.append({"at": now(), "action": "export",
                       "detail": f"engine={export_report.get('engine')}, "
                                 f"native={export_report.get('native')}"})
    change_log.append({"at": now(), "action": "checks",
                       "detail": f"ok={check_report.get('ok')}, "
                                 f"exceptions={len(check_report.get('exceptions', []))}"})
    timing = {"total_seconds": round(time.time() - t0, 3), "stages": stages,
              "budget_seconds": 900, "model_requests": 0}
    render_report = {**export_report, "pptx": str(pptx),
                     "pdf_pages_match": (export_report.get("pdf_pages") in
                                         (None, len(effective_spec.get("slides", []))))}

    record_paths = {
        "plan": dump_json(out_dir / "plan.json", plan_doc),
        "claim_source": dump_json(out_dir / "claim_source.json", claim_by_slide),
        "timing": dump_json(out_dir / "time.json", timing),
        "change_log": dump_json(out_dir / "change_log.json", change_log),
        "render_report": dump_json(out_dir / "render_report.json", render_report),
        "checks": dump_json(out_dir / "checks.json", check_report),
        "gold": dump_json(out_dir / "gold.json", gold_doc),
        "page_diff": dump_json(out_dir / "page-diff.json", diff_doc),
        "evidence_pack": dump_json(work_dir / "evidence-pack.json", pack),
        "deck_spec": dump_json(out_dir / "deck-spec.json", effective_spec),
    }
    mark("records")

    # ---------- status ----------
    hard_errors = check_report.get("errors", [])
    unresolved_edits = (edit_report or {}).get("unresolved", [])
    pptx_ok = Path(pptx).is_file() and not build_report.get("problems")
    render_ok = export_report.get("pdf") and export_report.get("png")
    if not pptx_ok:
        status, task_status = "partial", "partial"
        status_reason = "editable PPTX missing or structurally invalid"
    elif not render_ok or unresolved_edits or hard_errors:
        status, task_status = "partial", "partial"
        reasons = []
        if not render_ok:
            reasons.append("preview unavailable")
        if unresolved_edits:
            reasons.append(f"{len(unresolved_edits)} unresolved edit(s)")
        if hard_errors:
            reasons.append(f"{len(hard_errors)} hard check(s) failed")
        status_reason = "; ".join(reasons)
    else:
        status, task_status = "answered", "completed"
        status_reason = "editable deck, real previews and page-level evidence produced"

    artifacts = [
        {"role": "editable", "path": rel(pptx, out_dir), "format": "pptx"},
        {"role": "plan", "path": rel(record_paths["plan"], out_dir), "format": "json"},
        {"role": "claim_source", "path": rel(record_paths["claim_source"], out_dir), "format": "json"},
        {"role": "timing", "path": rel(record_paths["timing"], out_dir), "format": "json"},
        {"role": "change_log", "path": rel(record_paths["change_log"], out_dir), "format": "json"},
        {"role": "render_report", "path": rel(record_paths["render_report"], out_dir), "format": "json"},
        {"role": "checks", "path": rel(record_paths["checks"], out_dir), "format": "json"},
        {"role": "gold", "path": rel(record_paths["gold"], out_dir), "format": "json"},
        {"role": "page_diff", "path": rel(record_paths["page_diff"], out_dir), "format": "json"},
    ]
    if export_report.get("pdf"):
        artifacts.append({"role": "render", "path": rel(export_report["pdf"], out_dir), "format": "pdf"})
    for png in export_report.get("png", []):
        artifacts.append({"role": "preview", "path": rel(png, out_dir), "format": "png"})

    preserved = {
        "master": build_report.get("template_fidelity") in
                  ("master+layouts+theme", "starting-deck-preserved"),
        "layouts": len(inspected.get("layouts", [])),
        "theme_fonts": inspected.get("theme_fonts", []),
        "images": len(inspected.get("media", [])),
        "untouched_slides": (edit_report or {}).get("untouched_slides", []),
        "content_preserved_from_base": bool(
            (effective_spec.get("planning") or {}).get("origin") == "content-preserved-from-base"),
    }
    deck_info = {
        "path": "presentation.pptx", "format": "pptx",
        "slide_count": inspected.get("count", 0),
        "aspect_ratio": inspected.get("size_label") or effective_spec.get("size", "16:9"),
        "starting_deck": base_deck,
        "slides_added": 0 if not edit_report else 0,
        "slides_removed": 0,
        "slides_reordered": False,
        "template_fidelity": build_report.get("template_fidelity", "none"),
        "layout_map": build_report.get("layouts_used", []),
        "speaker_notes_slides": sum(1 for s in inspected.get("slides", [])
                                    if str(s.get("notes") or "").strip()),
    }
    uncertainty = list(dict.fromkeys(
        (brief.get("warnings") or [])
        + (spec_warnings or [])
        + ((effective_spec.get("planning") or {}).get("warnings") or [])
    ))
    limitations = list(dict.fromkeys(export_report.get("limitations") or []))
    exceptions = list(check_report.get("exceptions", []))
    if unresolved_edits:
        exceptions.extend({"code": "edit_unresolved", "severity": "error", "detail": e}
                          for e in unresolved_edits)
    if spec_source == "source-grounded auto-plan":
        exceptions.append({
            "code": "auto_plan_origin", "severity": "limitation",
            "detail": "deck spec generated by the source-grounded planner; "
                      "a model-authored work/deck-spec.json gives finer narrative control"})
    if brief.get("mode") == "edit" and base_deck and not brief.get("edits"):
        exceptions.append({"code": "no_resolvable_edits", "severity": "limitation",
                           "detail": "edit mode selected but workorder has no edit items"})

    result.update({
        "status": status,
        "task_status": task_status,
        "status_reason": status_reason,
        "workflow_mode": mode,
        "task_family": f"deck-{mode}",
        "case_id": case_id,
        "input_summary": {
            "task_md": brief.get("task_file"), "case_json": brief.get("case_file"),
            "materials": len(brief.get("materials", [])),
            "sources": len(brief.get("sources", [])),
            "figures": len(brief.get("figures", [])),
            "tables": len(brief.get("tables", [])),
            "evidence_items": len(brief.get("evidence", [])),
        },
        "deck": deck_info,
        "artifacts": artifacts,
        "workorder": {
            "path": brief.get("workorder_path"),
            "mode": (brief.get("workorder") or {}).get("mode"),
            "slide_count": brief.get("slide_count"),
            "starting_deck": base_deck, "target_template": template,
            "edit_items": len(brief.get("edits") or []),
        },
        "plan": {"path": rel(record_paths["plan"], out_dir),
                 "slides": len(effective_spec.get("slides", [])),
                 "content_origin": spec_source},
        "claim_source": {"path": rel(record_paths["claim_source"], out_dir),
                         "slides": len(claim_by_slide)},
        "timing": {"path": rel(record_paths["timing"], out_dir),
                   "total_seconds": timing["total_seconds"], "stages": stages},
        "change_records": {"path": rel(record_paths["change_log"], out_dir),
                           "changes": len(change_log)},
        "render_report": {"path": rel(record_paths["render_report"], out_dir),
                          "native": bool(export_report.get("native")),
                          "engine": export_report.get("engine")},
        "page_evidence": page_evidence,
        "edits": (edit_report or {}).get("applied", []),
        "unresolved_edits": (edit_report or {}).get("unresolved", []),
        "preserved": preserved,
        "render": export_report,
        "checks": check_report.get("checks", {}),
        "gold": {"path": rel(record_paths["gold"], out_dir),
                 "page_diff": rel(record_paths["page_diff"], out_dir),
                 "summary": diff_doc["summary"]},
        "editable_source": {
            "path": "presentation.pptx", "format": "pptx",
            "sha256": build_report.get("sha256"),
            "bytes": build_report.get("bytes", 0),
            "slide_count": inspected.get("count", 0),
            "editable": True,
        } if Path(pptx).is_file() else None,
        "export_artifact": {
            "path": rel(export_report["pdf"], out_dir), "format": "pdf",
            "pages": export_report.get("pdf_pages"),
            "engine": export_report.get("engine"),
            "native": bool(export_report.get("native")),
        } if export_report.get("pdf") else None,
        "content_map": content_map,
        "compliance_checks": check_report.get("compliance_checks", {}),
        "exceptions": exceptions,
        "uncertainty": uncertainty,
        "limitations": limitations,
        "sources": [{"path": s["path"], "kind": s["kind"], "chars": s["chars"],
                     "sha256": s["sha256"]} for s in brief.get("sources", [])],
        "template": {
            "path": template,
            "fidelity": build_report.get("template_fidelity", "none"),
            "layouts_used": build_report.get("layouts_used", []),
            "fallback_reason": build_report.get("template_fallback_reason"),
        },
        "spec": {"path": rel(record_paths["deck_spec"], out_dir),
                 "origin": spec_source, "issues": spec_issues,
                 "warnings": spec_warnings},
        "change_log": change_log,
        "notes": {
            "budget_seconds": 900,
            "native_renderer": export_report.get("engine"),
            "preview_is_native": bool(export_report.get("native")),
            "evidence_pack": rel(record_paths["evidence_pack"], out_dir),
        },
    })
    # Preserve any additional input-contract fields not overwritten above.
    for key, value in raw_case.items():
        result.setdefault(key, value)
    if brief.get("output_contract"):
        result.setdefault("output_contract", brief["output_contract"])
    write_result(out_dir, result)
    mark("result")
    result["timing"]["stages"] = stages
    write_result(out_dir, result)
    return 0, result


def input_error(out_dir, result, brief, stages, t0, extra=None):
    reason = extra or "; ".join(brief.get("errors") or ["no usable input"])
    result.update({
        "status": "input_error",
        "task_status": "input_error",
        "status_reason": reason,
        "workflow_mode": brief.get("mode"),
        "task_family": f"deck-{brief.get('mode') or 'generate'}",
        "case_id": result.get("case_id") or brief.get("case_id") or "unknown",
        "deck": None,
        "artifacts": [],
        "page_evidence": [],
        "edits": [],
        "preserved": {},
        "render": {"engine": "none", "native": False, "pdf": None, "png": [],
                   "unavailable": True,
                   "limitations": ["input_error: no deck was generated"]},
        "checks": {},
        "editable_source": None,
        "export_artifact": None,
        "content_map": [],
        "compliance_checks": {"pptx_valid": False, "slide_count_match": False,
                              "notes_complete": False, "export_signature": None,
                              "template_fidelity": "none", "render_available": False},
        "exceptions": [{"code": "input_error", "severity": "error", "detail": reason}],
        "uncertainty": brief.get("warnings", []),
        "limitations": ["No presentation was produced because required input was missing."],
        "sources": [],
        "input_summary": {
            "task_md": brief.get("task_file"), "case_json": brief.get("case_file"),
            "materials": len(brief.get("materials", [])),
            "sources": len(brief.get("sources", [])),
            "evidence_items": len(brief.get("evidence", [])),
        },
        "timing": {"total_seconds": round(time.time() - t0, 3), "stages": stages},
        "change_log": [{"at": now(), "action": "input_error", "detail": reason}],
        "notes": {"budget_seconds": 900, "preview_is_native": False},
    })
    dump_json(Path(out_dir) / "change_log.json", result["change_log"])
    dump_json(Path(out_dir) / "time.json", result["timing"])
    write_result(out_dir, result)
    return 0, result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input", help="input directory (case.json/task.md)")
    ap.add_argument("--output", default="/app/output", help="output directory")
    ap.add_argument("--work", default="/app/work", help="working directory")
    ap.add_argument("--spec", default=None, help="model-authored deck-spec.json")
    ap.add_argument("--template", default=None, help="target template .pptx")
    ap.add_argument("--starting-deck", dest="starting_deck", default=None,
                    help="starting deck for edit/adapt")
    ap.add_argument("--slides", type=int, default=None, help="requested slide count")
    ap.add_argument("--no-native", action="store_true",
                    help="skip soffice even when installed")
    ap.add_argument("--json", action="store_true", help="print result JSON")
    args = ap.parse_args(argv)
    try:
        Path(args.output).mkdir(parents=True, exist_ok=True)
        Path(args.work).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"cannot start: {exc}", file=sys.stderr)
        return 2
    try:
        code, result = run(args)
    except Exception as exc:  # last-resort: still emit an honest partial result
        raw_case = load_raw_case(args.input)
        result = base_result(raw_case, raw_case.get("case_id") or raw_case.get("id"))
        result.update({
            "status": "partial", "task_status": "partial",
            "status_reason": f"unhandled error: {type(exc).__name__}: {exc}",
            "deck": None, "artifacts": [], "editable_source": None,
            "export_artifact": None, "content_map": [],
            "compliance_checks": {"pptx_valid": False, "slide_count_match": False,
                                  "notes_complete": False, "export_signature": None,
                                  "template_fidelity": "none", "render_available": False},
            "exceptions": [{"code": "internal_error", "severity": "error",
                            "detail": f"{type(exc).__name__}: {exc}",
                            "trace": traceback.format_exc()[-2000:]}],
            "timing": {"total_seconds": 0, "stages": {}},
            "change_log": [{"at": now(), "action": "internal_error",
                            "detail": str(exc)}],
        })
        try:
            write_result(args.output, result)
        except Exception as write_exc:
            print(f"cannot write result.json: {write_exc}", file=sys.stderr)
            return 2
        code = 0
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"{result.get('status')} · {result.get('status_reason')}")
        print(f"slides={((result.get('deck') or {}).get('slide_count'))} "
              f"render={(result.get('render') or {}).get('engine')} "
              f"result={Path(args.output) / 'result.json'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
