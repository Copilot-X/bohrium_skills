#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entry point: /input -> editable SVG + PDF/PNG + result.json.

Pipeline (all steps are deterministic assembly, not answer generation):

    inventory -> load/normalize spec -> render SVG -> export PDF/PNG
    -> audit coverage/sources/exports -> result_graphics.build_result

The agent's job is to read the real materials and write `/app/work/spec.json`
following `templates/spec.schema.json` (or put the spec in case.json under
`figure_spec`).  This script never extracts a fallback answer from the paper.

Exit codes:
    0 = completed, partial or input_error; result.json was written either way
    2 = nothing could be started / result.json could not be written

Usage:
    python3 run_case.py --input /input --output /app/output --work /app/work
    python3 run_case.py --input ./examples --work /tmp/work --output /tmp/out
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import traceback
from pathlib import Path

import audit as audit_mod
import export as export_mod
import result_graphics
import svg_gen

TASK_FAMILY = "apparatus-route"
WORKORDER = "apparatus_and_technical_route"
TEXT_EXT = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml"}


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def digest(path, limit=50 * 1024 * 1024):
    size = path.stat().st_size
    if size > limit:
        return ""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def inventory(input_dir):
    root = Path(input_dir)
    rows = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(part.startswith(".") for part in path.parts):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            rows.append({
                "path": path.relative_to(root).as_posix(),
                "kind": "text" if path.suffix.lower() in TEXT_EXT else
                        ("image" if path.suffix.lower() in
                         {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg"}
                         else "document"),
                "bytes": size,
                "sha256": digest(path),
            })
    return {"ok": root.is_dir(), "root": str(root), "files": rows,
            "total": len(rows)}


def load_case(input_dir):
    path = Path(input_dir) / "case.json"
    if not path.is_file():
        return {}, ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        case = json.loads(raw)
        return (case if isinstance(case, dict) else {}), ""
    except (OSError, json.JSONDecodeError) as exc:
        return {}, "case.json unreadable: %s" % exc


def pick_spec(case, work, explicit):
    if explicit:
        path = Path(explicit)
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8")), str(path), ""
            except (OSError, json.JSONDecodeError) as exc:
                return None, str(path), "spec unreadable: %s" % exc
        return None, str(path), "spec not found: %s" % path
    candidate = Path(work) / "spec.json"
    if candidate.is_file():
        try:
            return (json.loads(candidate.read_text(encoding="utf-8")),
                    str(candidate), "")
        except (OSError, json.JSONDecodeError) as exc:
            return None, str(candidate), "spec unreadable: %s" % exc
    for key in ("figure_spec", "spec", "diagram_spec"):
        if isinstance(case.get(key), dict):
            return case[key], "case.json:%s" % key, ""
    if any(case.get(key) for key in ("nodes", "components", "stages", "panels")):
        return case, "case.json", ""
    return None, "", "no spec: write /app/work/spec.json following templates/spec.schema.json"


def source_items(norm):
    for element in norm.get("elements") or []:
        yield element.get("source")
    for conn in norm.get("connections") or []:
        yield conn.get("source")
    for cond in norm.get("conditions") or []:
        yield cond.get("source")
    for dec in norm.get("decisions") or []:
        yield dec.get("criterion_source")


def align_sources(norm, input_dir, relative_paths):
    """Resolve basename-only sources to the real relative input path."""
    root = Path(input_dir)
    for source in source_items(norm):
        if not isinstance(source, dict):
            continue
        path = str(source.get("path") or "")
        if not path:
            continue
        if Path(path).is_absolute() and Path(path).is_file():
            continue
        if (root / path).is_file():
            continue
        matches = [rel for rel in relative_paths
                   if rel == path or rel.endswith("/" + path) or
                   Path(rel).name == Path(path).name]
        if len(matches) == 1:
            source["path"] = matches[0]


def default_material(input_dir, inventory_report, case):
    declared = case.get("source_materials") or case.get("materials") or []
    if isinstance(declared, str):
        declared = [declared]
    for item in declared:
        if isinstance(item, dict):
            item = item.get("path")
        if item and (Path(input_dir) / str(item)).is_file():
            return str(item)
    texts = [row["path"] for row in inventory_report.get("files", [])
             if row.get("kind") == "text" and
             Path(row["path"]).name not in ("case.json", "task.md")]
    return texts[0] if len(texts) == 1 else ""


def safe_base(figure_id):
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", str(figure_id or "figure")).strip("._")
    return base or "figure"


def build_input_error(case_id, message, exceptions, output, work, inventory_report,
                      spec_source=""):
    norm = {"figure_id": "input-error", "mode": "apparatus", "width_mm": 0,
            "height_mm": 0, "panels": [], "elements": [], "connections": [],
            "conditions": [], "decisions": []}
    result = result_graphics.build_result(
        case_id, norm, {}, "input_error", None, None, None, 300,
        exceptions=exceptions or [{"code": "input_error", "detail": message,
                                   "impact": "no figure was produced"}],
        uncertainty=[{"item": "input", "reason": message}],
        inputs=[{"path": row["path"], "bytes": row.get("bytes"),
                 "sha256": row.get("sha256")}
                for row in inventory_report.get("files", [])],
        spec_source=spec_source)
    write_json(Path(work) / "inventory.json", inventory_report)
    return write_json(Path(output) / "result.json", result), result


def main():
    ap = argparse.ArgumentParser(description="Apparatus/route figure pipeline")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--spec", default=None)
    ap.add_argument("--mode", default=None,
                    choices=[None, "apparatus", "route", "both"])
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    input_dir = Path(args.input)
    output = Path(args.output)
    work = Path(args.work)
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    (output / "figures").mkdir(parents=True, exist_ok=True)
    (output / "preview").mkdir(parents=True, exist_ok=True)

    inv = inventory(input_dir)
    write_json(work / "inventory.json", inv)

    case, case_error = load_case(input_dir)
    case_id = str(case.get("case_id") or case.get("id") or
                  (input_dir.name if input_dir.name else "case"))
    if case_error:
        path, result = build_input_error(
            case_id, case_error,
            [{"code": "input_unreadable", "detail": case_error,
              "impact": "case metadata unavailable"}],
            output, work, inv)
        print(json.dumps({"ok": False, "status": result["status"],
                          "result": str(path)}, ensure_ascii=False))
        return 0

    raw_spec, spec_source, spec_error = pick_spec(case, work, args.spec)
    if spec_error:
        path, result = build_input_error(
            case_id, spec_error,
            [{"code": "spec_missing" if "no spec" in spec_error else "spec_unreadable",
              "detail": spec_error,
              "impact": "the drawing cannot start without a source-bound spec",
              "fallback": "write work/spec.json and rerun"}],
            output, work, inv)
        print(json.dumps({"ok": False, "status": result["status"],
                          "result": str(path)}, ensure_ascii=False))
        return 0

    relative_paths = [row["path"] for row in inv.get("files", [])]
    try:
        norm, warnings = svg_gen.normalize_spec(raw_spec, args.mode)
    except (svg_gen.SpecError, ValueError) as exc:
        path, result = build_input_error(
            case_id, str(exc),
            [{"code": "spec_invalid", "detail": str(exc),
              "impact": "spec cannot be rendered"}],
            output, work, inv, spec_source)
        print(json.dumps({"ok": False, "status": result["status"],
                          "result": str(path)}, ensure_ascii=False))
        return 0

    align_sources(norm, input_dir, relative_paths)
    default_path = default_material(input_dir, inv, case)
    # A locator-only source ("§2.1") is acceptable only when the input has a
    # single obvious material; bind it explicitly instead of leaving it vague.
    for source in source_items(norm):
        if isinstance(source, dict) and source.get("locator") and not source.get("path"):
            if default_path and not source.get("path"):
                source["path"] = default_path
            else:
                source["path"] = ""
    norm["source_materials"] = [default_path] if default_path else []
    write_json(work / "normalized-spec.json", norm)

    figure_id = norm.get("figure_id") or "figure"
    base = safe_base(figure_id)
    svg_path = output / "figures" / (base + ".svg")
    pdf_path = output / "figures" / (base + ".pdf")
    png_path = output / "preview" / (base + ".png")
    for stale in (svg_path, pdf_path, png_path):
        if stale.is_file():
            try:
                stale.unlink()
            except OSError:
                pass

    render_meta = {}
    render_exception = None
    try:
        render_meta = svg_gen.build_svg(norm, svg_path)
        write_json(work / "render.json", render_meta)
    except Exception as exc:  # noqa: BLE001 - report, never crash the case
        render_exception = "%s: %s" % (type(exc).__name__, exc)
        write_json(work / "render.json", {"ok": False, "error": render_exception,
                                          "trace": traceback.format_exc()[-2000:]})

    export_report = {}
    if svg_path.is_file():
        try:
            export_report = export_mod.export_all(
                svg_path, output / "figures", basename=base,
                formats=("pdf", "png"), dpi=args.dpi,
                out_pdf=pdf_path, out_png=png_path)
        except Exception as exc:  # noqa: BLE001
            export_report = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                             "outputs": {}, "exceptions": [
                                 {"code": "export_failed", "detail": str(exc)}]}
        write_json(work / "export.json", export_report)

    audit_report = {}
    try:
        audit_report = audit_mod.audit(
            norm, svg_path if svg_path.is_file() else None,
            pdf_path if pdf_path.is_file() else None,
            png_path if png_path.is_file() else None,
            input_root=str(input_dir), spec_path=str(work / "normalized-spec.json"),
            default_source_path=default_path, dpi=args.dpi)
    except Exception as exc:  # noqa: BLE001
        audit_report = {"ok": False, "coverage": 0.0, "checks": {},
                        "errors": [{"code": "AUDIT_FAILED",
                                    "detail": "%s: %s" % (type(exc).__name__, exc)}],
                        "warnings": [], "compliance_checks": []}
    write_json(work / "coverage.json", audit_report)

    svg_rel = "figures/%s.svg" % base if svg_path.is_file() else None
    pdf_rel = "figures/%s.pdf" % base if pdf_path.is_file() else None
    png_rel = "preview/%s.png" % base if png_path.is_file() else None

    spec_warnings = [str(w) for w in (norm.get("warnings") or [])]
    # Benign normalization notes (canvas widened, duplicate id renamed) are
    # uncertainty, not exceptions: a completed result should have no blockers.
    exceptions = []
    if render_exception:
        exceptions.append({"code": "render_failed", "detail": render_exception,
                           "impact": "no SVG was produced"})
    for item in export_report.get("exceptions") or []:
        exceptions.append({"code": item.get("code") or "export_failed",
                           "detail": item.get("detail") or "",
                           "impact": "requested export not produced"})
    for err in norm.get("errors") or []:
        exceptions.append({"code": "spec_semantic", "detail": str(err),
                           "impact": "result is partial until the spec is fixed"})

    pdf_ok = bool((audit_report.get("exports") or {}).get("pdf", {}).get("signature"))
    png_ok = bool((audit_report.get("exports") or {}).get("png", {}).get("signature"))
    completed = bool(
        svg_rel and audit_report.get("ok") and
        float(audit_report.get("coverage") or 0) >= 1.0 and
        pdf_ok and png_ok and not norm.get("errors") and not render_exception)
    status = "completed" if completed else ("partial" if (svg_rel or pdf_rel or png_rel)
                                            else "input_error")
    if status == "input_error":
        exceptions.append({"code": "no_artifact",
                           "detail": "render/export produced no deliverable",
                           "impact": "only the input inventory is reported"})

    result = result_graphics.build_result(
        case_id, norm, audit_report, status, svg_rel, pdf_rel, png_rel,
        dpi=args.dpi, exceptions=exceptions,
        uncertainty=[{"item": warning, "reason": "spec normalization warning"}
                     for warning in spec_warnings],
        inputs=[{"path": row["path"], "bytes": row.get("bytes"),
                 "sha256": row.get("sha256")}
                for row in inv.get("files", [])],
        render_meta=render_meta, spec_source=spec_source)

    result_path = write_json(output / "result.json", result)
    summary = {
        "ok": status == "completed",
        "status": status,
        "case_id": case_id,
        "result": str(result_path),
        "svg": str(svg_path) if svg_path.is_file() else None,
        "pdf": str(pdf_path) if pdf_path.is_file() else None,
        "png": str(png_path) if png_path.is_file() else None,
        "coverage": audit_report.get("coverage"),
        "errors": len(audit_report.get("errors") or []),
        "warnings": len(audit_report.get("warnings") or []),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - last resort: never exit 2 silently
        traceback.print_exc()
        sys.exit(2)
