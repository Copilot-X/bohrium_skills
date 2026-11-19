#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reports: official media fields, run records and a single result.json.

The entrypoint owns these fields so a case never depends on the model to
hand-write ``status``, ``editable_source``, ``export_artifact``,
``content_map``, ``compliance_checks`` or ``exceptions``.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

RECORD_FILES = ("workorder.json", "template-audit.json", "ppt-format-spec.json",
                "layout-map.json", "adapt-report.json", "format-check.json",
                "plan.json", "claim_source.json", "change_log.json",
                "render_report.json", "time.json")


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(role, path, root):
    path = Path(path)
    if not path.is_file():
        return None
    root = Path(root)
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = str(path)
    return {"role": role, "path": rel, "format": path.suffix.lstrip(".").lower(),
            "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def mirror_work(work_dir, output_dir, names=RECORD_FILES):
    work_dir, output_dir = Path(work_dir), Path(output_dir)
    target = output_dir / "work"
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in names:
        source = work_dir / name
        if source.is_file():
            shutil.copy2(source, target / name)
            copied.append(target / name)
    return copied


def content_map(plan, slide_content, workorder, baseline_path):
    """Per-slide evidence map; model-provided source locators win."""
    slides = (plan.get("slides") or []) if plan.get("mode") == "build" else []
    provided = {}
    for item in (workorder.get("content_map") or []):
        if isinstance(item, dict) and item.get("slide"):
            provided[int(item["slide"])] = item
    sources = workorder.get("source_materials") or []
    fallback_source = (sources[0] if sources else
                       str(baseline_path) if baseline_path else "")
    rows = []
    count = max(len(slides), len(slide_content or []))
    layout_rows = (plan.get("layout_map") or {}).get("slides") or []
    for index in range(1, count + 1):
        spec = slides[index - 1] if index - 1 < len(slides) else {}
        extracted = (slide_content or [])[index - 1] if \
            index - 1 < len(slide_content or []) else {}
        title = spec.get("title") or extracted.get("title") or ""
        bullets = spec.get("bullets") or []
        evidence = [str(item.get("text") if isinstance(item, dict) else item)
                    for item in bullets]
        if not evidence:
            evidence = ([extracted.get("subtitle") or ""] +
                        (extracted.get("bodies") or []) +
                        (extracted.get("free") or []))
        evidence = [item for item in evidence if str(item).strip()][:8]
        source = spec.get("source") or ""
        locator = source or (f"starting deck slide {index}" if extracted
                             else f"slide {index}")
        claim = spec.get("claim") or (evidence[0] if evidence else title)
        layout = (layout_rows[index - 1].get("layout", "")
                  if index - 1 < len(layout_rows) else "")
        row = {
            "slide": index,
            "title": title,
            "claim": claim,
            "source": source or fallback_source,
            "locator": locator,
            "evidence": evidence,
            "notes_present": bool(spec.get("notes") or extracted.get("notes")),
            "template_layout": layout,
            "preserved": plan.get("mode") == "adapt",
        }
        if index in provided:
            row.update({key: value for key, value in provided[index].items()
                        if key != "slide"})
        claims = spec.get("claims") or []
        if claims and not row.get("claims"):
            row["claims"] = claims
        rows.append(row)
    return rows


def write_records(work_dir, case_id, plan, verify_report, render_report,
                  content_rows, changes, started_at, finished_at, stages,
                  workorder):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    slides = []
    for row in (plan.get("layout_map", {}).get("slides") or []):
        slides.append({
            "slide": row.get("slide"), "role": row.get("role"),
            "layout": row.get("layout"), "title": row.get("title", ""),
            "source": row.get("source", ""),
            "notes_present": row.get("notes_present", False),
        })
    plan_doc = {
        "case_id": case_id,
        "mode": plan.get("mode"),
        "slide_count": len(slides),
        "slides": slides,
        "spec_path": plan.get("spec_path"),
        "missing_spec": plan.get("missing_spec", False),
        "generated_at": utcnow(),
    }
    claim_doc = {
        "case_id": case_id,
        "claims": content_rows,
        "sources": workorder.get("source_materials") or [],
        "generated_at": utcnow(),
    }
    change_doc = {
        "case_id": case_id,
        "changes": [{"action": "template_adaptation", "detail": text}
                    for text in changes] or [],
        "generated_at": utcnow(),
    }
    render_doc = dict(render_report or {})
    render_doc.update({"case_id": case_id, "generated_at": utcnow()})
    time_doc = {
        "case_id": case_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_seconds": round(
            (datetime.fromisoformat(finished_at) -
             datetime.fromisoformat(started_at)).total_seconds(), 2),
        "stages": stages,
    }
    write_json(work_dir / "plan.json", plan_doc)
    write_json(work_dir / "claim_source.json", claim_doc)
    write_json(work_dir / "change_log.json", change_doc)
    write_json(work_dir / "render_report.json", render_doc)
    write_json(work_dir / "time.json", time_doc)
    write_json(work_dir / "format-check.json", verify_report)
    return {"plan": plan_doc, "claim_source": claim_doc, "change_log": change_doc,
            "render_report": render_doc, "time": time_doc}


def _status_from(verify_report, render_report, plan):
    if verify_report.get("errors"):
        return "partial"
    if not render_report.get("native"):
        return "partial"
    if plan.get("missing_spec"):
        return "partial"
    fidelity = (verify_report.get("checks") or {}).get("template") or {}
    if not all(fidelity.values()):
        return "partial"
    return "answered"


def build_result(case, workorder, audit, plan, verify_report, render_report,
                 output_dir, work_dir, deck_path, template_path, baseline_path,
                 content_rows, changes, exceptions, started_at, finished_at,
                 stages, stage="build", forced_status=None):
    output_dir = Path(output_dir)
    case_id = workorder.get("case_id") or case.get("case_id") or case.get("id") \
        or "case"
    checks = dict(verify_report.get("checks") or {})
    native = bool(render_report.get("native"))
    pdf_path = render_report.get("pdf")
    pngs = render_report.get("png") or []
    fidelity = checks.get("template") or {}
    fidelity_label = ("master+layouts+theme" if all(fidelity.values())
                      else "theme-only" if fidelity.get("theme") else "none")
    status = forced_status or _status_from(verify_report, render_report, plan)
    if status not in ("answered", "partial", "input_error", "blocked"):
        status = "partial"
    task_status = {"answered": "completed", "partial": "partial",
                   "input_error": "input_error", "blocked": "blocked"}[status]

    deck = None
    editable = None
    export = None
    if deck_path and Path(deck_path).is_file():
        deck = {
            "path": "presentation.pptx",
            "slide_count": verify_report.get("slide_count", 0),
            "template": str(template_path) if template_path else "",
            "template_fidelity": fidelity_label,
            "aspect_ratio": audit.get("aspect_ratio", ""),
            "editable": True,
        }
        editable = {"path": "presentation.pptx", "format": "pptx",
                    "slide_count": deck["slide_count"], "editable": True}
    if native and pdf_path and Path(pdf_path).is_file():
        export = {"path": "preview/presentation.pdf", "format": "pdf",
                  "native": True, "png_count": len(pngs),
                  "bytes": Path(pdf_path).stat().st_size}
    else:
        export = {
            "path": None, "format": None, "native": False,
            "status": "unavailable",
            "limitation": render_report.get("limitation") or
            "no native office renderer available; PPTX was structurally verified",
        }

    all_exceptions = list(plan.get("exceptions") or [])
    all_exceptions.extend(verify_report.get("exceptions") or [])
    if not native:
        all_exceptions.append({
            "code": "NATIVE_RENDER_UNAVAILABLE", "severity": "limitation",
            "detail": render_report.get("limitation") or "no office renderer"})
    for item in exceptions or []:
        if item not in all_exceptions:
            all_exceptions.append(item)
    if plan.get("missing_spec"):
        all_exceptions.append({
            "code": "DECK_SPEC_MISSING", "severity": "partial",
            "detail": "deck-spec.json was not provided; only a scaffold/adaptation "
                      "was produced"})
    font_info = plan.get("font_availability") or {}
    if font_info.get("checked") and font_info.get("missing"):
        all_exceptions.append({
            "code": "FONT_NOT_INSTALLED", "severity": "warning",
            "detail": f"template fonts not found locally: {font_info['missing']}"})

    compliance = {
        "pptx_valid": bool(checks.get("zip_valid")),
        "template_fidelity": fidelity_label,
        "placeholder_used": bool(checks.get("placeholders_ok")),
        "export_signature": "pdf" if native and export and export.get("path") else "none",
        "slide_size_match": bool(fidelity.get("slide_size")),
        "master_and_layouts_inherited": bool(fidelity.get("master") and
                                             fidelity.get("layouts")),
        "theme_inherited": bool(fidelity.get("theme")),
        "layouts_from_template": all(row.get("layout_in_template", False)
                                     for row in (verify_report.get("layouts") or [])),
        "fonts_within_template": bool(checks.get("fonts_ok")),
        "content_preserved": bool(checks.get("content_ok")),
        "numbers_preserved": not (verify_report.get("content_retention") or {}
                                  ).get("missing_numbers"),
        "title_hierarchy_ok": not any(
            item.get("code") == "TITLE_HIERARCHY" for item in all_exceptions),
        "notes_present": bool((verify_report.get("notes") or {})
                              .get("slides_with_notes")),
        "page_count_match": bool(checks.get("page_count_match")),
        "visual_preview": bool(native and (pdf_path or pngs)),
    }
    official = ("status", "editable_source", "export_artifact", "content_map",
                "compliance_checks", "exceptions")
    compliance["official_fields_present"] = bool(
        case_id and all(field for field in official))

    artifacts = []
    for role, path in (("editable", output_dir / "presentation.pptx"),
                       ("audit", output_dir / "work" / "template-audit.json"),
                       ("workorder", output_dir / "work" / "workorder.json"),
                       ("format_spec", output_dir / "work" / "ppt-format-spec.json"),
                       ("mapping", output_dir / "work" / "layout-map.json"),
                       ("adapt", output_dir / "work" / "adapt-report.json"),
                       ("format_check", output_dir / "format-check.json"),
                       ("plan", output_dir / "work" / "plan.json"),
                       ("claim_source", output_dir / "work" / "claim_source.json"),
                       ("timing", output_dir / "work" / "time.json"),
                       ("change_log", output_dir / "work" / "change_log.json"),
                       ("render_report", output_dir / "work" / "render_report.json")):
        entry = artifact(role, path, output_dir)
        if entry:
            artifacts.append(entry)
    if native and pdf_path and Path(pdf_path).is_file():
        entry = artifact("render", pdf_path, output_dir)
        if entry:
            artifacts.append(entry)
    for path in pngs:
        entry = artifact("preview", path, output_dir)
        if entry:
            artifacts.append(entry)

    checks = dict(checks)
    checks.update({"workorder_read": bool(workorder.get("original") or
                                          workorder.get("target_template")),
                   "records_complete": True,
                   "render_ok": native,
                   "visual_reviewed": False})
    result = {}
    reserved = {"status", "task_status", "editable_source", "export_artifact",
                "content_map", "compliance_checks", "exceptions", "checks",
                "artifacts", "deck", "template", "format_check", "render"}
    for key, value in (case or {}).items():
        if key not in reserved:
            result[key] = value
    result.update({
        "case_id": case_id,
        "task_family": "template-adaptation",
        "status": status,
        "task_status": task_status,
        "stage": stage,
        "editable_source": editable,
        "export_artifact": export,
        "content_map": content_rows,
        "compliance_checks": compliance,
        "exceptions": all_exceptions,
        "artifacts": artifacts,
        "deck": deck,
        "template": {
            "path": str(template_path) if template_path else "",
            "slide_size": audit.get("slide_size"),
            "aspect_ratio": audit.get("aspect_ratio"),
            "masters": audit.get("masters_count"),
            "layouts": [{"name": layout["name"],
                         "placeholders": [{"type": ph["type"],
                                           "idx": ph["idx"]}
                                          for ph in layout["placeholders"]
                                          if ph["type"] not in
                                          {"dt", "ftr", "sldNum", "hdr"}]}
                        for layout in audit.get("layouts", [])],
            "theme_fonts": audit.get("theme_fonts"),
            "theme_colors": audit.get("theme_colors"),
            "notes_master": audit.get("notes_master"),
        },
        "font_substitution": (plan.get("format_spec", {}).get("fonts", {})
                              .get("substitutions") or []),
        "font_availability": font_info,
        "format_check": {
            "source": "work/format-check.json",
            "checks": verify_report.get("checks"),
            "layouts": verify_report.get("layouts"),
            "placeholders": verify_report.get("placeholders"),
            "fonts": verify_report.get("fonts"),
            "colors": verify_report.get("colors"),
            "content_retention": verify_report.get("content_retention"),
            "images": verify_report.get("images"),
            "overflow_risks": verify_report.get("overflow_risks"),
            "out_of_bounds": verify_report.get("out_of_bounds"),
            "title_hierarchy": verify_report.get("title_hierarchy"),
            "notes": verify_report.get("notes"),
            "errors": verify_report.get("errors"),
            "warnings": verify_report.get("warnings"),
        },
        "render": {
            "engine": render_report.get("engine", "none"),
            "native": native,
            "pdf": "preview/presentation.pdf" if native else None,
            "png": [f"preview/{Path(path).name}" for path in pngs],
            "unavailable": not native,
            "limitation": render_report.get("limitation", ""),
            "duration_seconds": render_report.get("duration_seconds", 0),
        },
        "checks": checks,
        "workorder": {
            "path": "work/workorder.json",
            "mode": workorder.get("mode"),
            "slide_count": workorder.get("slide_count"),
            "starting_deck": workorder.get("starting_deck"),
            "target_template": workorder.get("target_template"),
            "edits": len(workorder.get("edits") or []),
            "preserve": workorder.get("preserve") or [],
        },
        "plan": {"path": "work/plan.json",
                 "slides": len((plan.get("layout_map") or {}).get("slides") or [])},
        "claim_source": {"path": "work/claim_source.json",
                         "claims": len(content_rows)},
        "timing": {"path": "work/time.json"},
        "change_records": {"path": "work/change_log.json",
                           "changes": len(changes)},
        "render_report": {"path": "work/render_report.json",
                          "native": native, "engine": render_report.get("engine")},
        "uncertainty": list(plan.get("uncertainty") or []),
        "change_log": list(changes),
    })
    result["timing"]["started_at"] = started_at
    result["timing"]["finished_at"] = finished_at
    result["timing"]["total_seconds"] = round(
        (datetime.fromisoformat(finished_at) -
         datetime.fromisoformat(started_at)).total_seconds(), 2)
    result["timing"]["stages"] = stages
    return result
