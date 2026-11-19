#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the official media result.json for an apparatus/route figure.

This is the only module that writes the six media fields:
`status`, `editable_source`, `export_artifact`, `content_map`,
`compliance_checks`, `exceptions`.  It also keeps the task's original v0.3
fields (`elements`, `connections`, `conditions`, `decisions`, `checks`).
Everything is derived from the normalized spec plus the real render/export/
audit outputs.

Usage (called by run_case.py; standalone for debugging):
    python3 result_graphics.py --spec work/normalized-spec.json \
        --audit work/coverage.json --out output/result.json \
        --case-id C1 --status completed \
        --svg figures/fig4.svg --pdf figures/fig4.pdf --png preview/fig4.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _source(value):
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if v not in (None, "")}
    if isinstance(value, str) and value.strip():
        return {"locator": value.strip()}
    return {}


def _artifact_paths(svg_rel, pdf_rel, png_rel):
    return [p for p in (svg_rel, pdf_rel, png_rel) if p]


def build_result(case_id, norm, audit_report, status="partial", svg_rel=None,
                 pdf_rel=None, png_rel=None, dpi=300, exceptions=None,
                 uncertainty=None, change_log=None, inputs=None,
                 render_meta=None, spec_source="agent",
                 workorder="apparatus_and_technical_route"):
    norm = norm if isinstance(norm, dict) else {}
    audit_report = audit_report if isinstance(audit_report, dict) else {}
    elements = [e for e in norm.get("elements") or [] if isinstance(e, dict)]
    connections = [c for c in norm.get("connections") or [] if isinstance(c, dict)]
    conditions = [c for c in norm.get("conditions") or [] if isinstance(c, dict)]
    decisions = [d for d in norm.get("decisions") or [] if isinstance(d, dict)]
    checks = audit_report.get("checks") or {}
    exports = audit_report.get("exports") or {}
    not_rendered = set(audit_report.get("not_rendered") or [])
    pdf_ok = bool((exports.get("pdf") or {}).get("signature"))
    png_meta = exports.get("png") or {}
    png_ok = bool(png_meta.get("signature"))

    def rendered(item_id):
        return str(item_id) not in not_rendered

    artifacts = []
    if svg_rel:
        artifacts.append({"role": "editable", "path": svg_rel, "format": "svg"})
    if pdf_rel and pdf_ok:
        artifacts.append({"role": "export", "path": pdf_rel, "format": "pdf"})
    if png_rel and png_ok:
        artifacts.append({"role": "preview", "path": png_rel, "format": "png",
                          "width_px": png_meta.get("width_px"),
                          "height_px": png_meta.get("height_px"), "dpi": dpi})

    export_artifact = None
    if pdf_rel and pdf_ok:
        export_artifact = {"path": pdf_rel, "format": "pdf",
                           "paths": _artifact_paths(None, pdf_rel, png_rel),
                           "dpi": dpi,
                           "png": ({"path": png_rel,
                                    "width_px": png_meta.get("width_px"),
                                    "height_px": png_meta.get("height_px")}
                                   if png_ok else None)}
    elif png_rel and png_ok:
        export_artifact = {"path": png_rel, "format": "png", "paths": [png_rel],
                           "dpi": dpi,
                           "width_px": png_meta.get("width_px"),
                           "height_px": png_meta.get("height_px")}

    content_map = []
    for element in elements:
        content_map.append({
            "id": str(element.get("id")),
            "element_id": str(element.get("id")),
            "kind": element.get("type") or "component",
            "label": element.get("label") or "",
            "panel": element.get("panel"),
            "source": _source(element.get("source")),
            "rendered": rendered(element.get("id")),
            "media": "svg",
        })
    for conn in connections:
        content_map.append({
            "id": str(conn.get("id")),
            "element_id": str(conn.get("id")),
            "kind": "connection",
            "label": conn.get("medium") or conn.get("label") or "",
            "panel": conn.get("panel"),
            "source": _source(conn.get("source")),
            "rendered": rendered(conn.get("id")),
            "media": "svg",
        })
    for cond in conditions:
        content_map.append({
            "id": str(cond.get("id")),
            "element_id": str(cond.get("id")),
            "kind": "condition",
            "label": "working condition",
            "panel": cond.get("panel"),
            "source": _source(cond.get("source")),
            "rendered": rendered(cond.get("id")),
            "media": "svg",
        })
    for dec in decisions:
        content_map.append({
            "id": str(dec.get("id")),
            "element_id": str(dec.get("id")),
            "kind": "decision",
            "label": dec.get("question") or "",
            "panel": dec.get("panel"),
            "source": _source(dec.get("criterion_source") or dec.get("source")),
            "rendered": rendered(dec.get("id")),
            "media": "svg",
        })

    inferred = audit_report.get("inferred") or []
    exceptions_out = list(exceptions or [])
    for err in audit_report.get("errors") or []:
        code = err.get("code") or "AUDIT_ERROR"
        if any(str(e.get("code")) == code for e in exceptions_out):
            continue
        exceptions_out.append({
            "code": code,
            "detail": err.get("detail") or err.get("ids") or err.get("items") or "",
            "impact": "result is partial; the listed check did not pass",
        })
    for item in inferred:
        exceptions_out.append({
            "code": "inferred_element",
            "detail": item,
            "impact": "element/edge is explicitly inferred, not directly reported",
        })
    if not pdf_ok or not png_ok:
        exceptions_out.append({
            "code": "export_incomplete",
            "detail": "pdf=%s png=%s" % (pdf_ok, png_ok),
            "impact": "only the produced artifacts are claimed",
        })

    uncertainty_out = list(uncertainty or [])
    for warning in audit_report.get("warnings") or []:
        uncertainty_out.append({"item": str(warning), "reason": "audit warning"})

    sources = []
    seen = set()
    for item in elements + connections + conditions:
        src = _source(item.get("source"))
        key = (src.get("path"), src.get("locator"))
        if any(key) and key not in seen:
            seen.add(key)
            sources.append({"doc_id": src.get("path") or "",
                            "locator": src.get("locator") or ""})
    for dec in decisions:
        src = _source(dec.get("criterion_source"))
        key = (src.get("path"), src.get("locator"))
        if any(key) and key not in seen:
            seen.add(key)
            sources.append({"doc_id": src.get("path") or "",
                            "locator": src.get("locator") or ""})

    log = list(change_log or [])
    log.extend([
        {"step": len(log) + 1, "action": "inventoried the real input files",
         "evidence": "work/inventory.json"},
        {"step": len(log) + 2,
         "action": "normalized the source-bound apparatus/route spec",
         "evidence": "work/normalized-spec.json"},
        {"step": len(log) + 3, "action": "rendered editable SVG and exported PDF/PNG",
         "evidence": ", ".join(_artifact_paths(svg_rel, pdf_rel, png_rel))},
        {"step": len(log) + 4, "action": "audited coverage, sources and export headers",
         "evidence": "work/coverage.json"},
    ])

    components = sum(1 for e in elements if e.get("type") == "component")
    stages = sum(1 for e in elements if e.get("type") in ("stage", "terminal"))
    figure = {
        "figure_id": norm.get("figure_id") or "figure",
        "mode": norm.get("mode") or "apparatus",
        "components": components,
        "stages": stages,
        "decisions": len(decisions),
        "canvas_mm": [norm.get("width_mm"), norm.get("height_mm")],
        "panels": len(norm.get("panels") or []),
        "render_backend": "local_svg",
        "layout_type": ("vertical" if norm.get("mode") == "route" else "horizontal"),
    }

    result = {
        "case_id": case_id,
        "task_family": "apparatus-route",
        "workorder": workorder,
        "status": status,
        "figure": figure,
        "artifacts": artifacts,
        "elements": [
            {"element_id": str(e.get("id")), "label": e.get("label") or "",
             "type": e.get("type") or "component", "panel": e.get("panel"),
             "source": _source(e.get("source"))}
            for e in elements
        ],
        "connections": [
            {"id": str(c.get("id")), "from": str(c.get("from")),
             "to": str(c.get("to")), "medium": c.get("medium") or "",
             "direction": c.get("direction") or "forward",
             "label": c.get("label") or "", "panel": c.get("panel"),
             "from_port": c.get("from_port"), "to_port": c.get("to_port"),
             "source": _source(c.get("source"))}
            for c in connections
        ],
        "conditions": [
            {"id": str(c.get("id")), "location": str(c.get("location")),
             "params": c.get("params"), "panel": c.get("panel"),
             "source": _source(c.get("source"))}
            for c in conditions
        ],
        "decisions": [
            {"id": str(d.get("id")), "after": str(d.get("after") or ""),
             "question": d.get("question") or "", "yes": str(d.get("yes") or ""),
             "no": str(d.get("no") or ""), "criterion": d.get("criterion") or "",
             "panel": d.get("panel"),
             "criterion_source": _source(d.get("criterion_source"))}
            for d in decisions
        ],
        "checks": {
            "xml_valid": bool(checks.get("xml_valid")),
            "text_as_text": bool(checks.get("text_as_text")),
            "external_refs": checks.get("external_refs", 0),
            "all_components_named": bool(checks.get("all_components_named")),
            "stages_covered": bool(checks.get("stages_covered")),
            "connections_covered": bool(checks.get("connections_covered")),
            "conditions_sourced": bool(checks.get("conditions_sourced")),
            "decisions_sourced": bool(checks.get("decisions_sourced")),
            "flow_direction_closed": bool(checks.get("flow_direction_closed")),
            "export_from_source": bool(checks.get("export_from_source")),
            "id_coverage": audit_report.get("coverage", 0.0),
            "text_elements": checks.get("text_elements", 0),
            "named_groups": checks.get("named_groups", 0),
        },
        "editable_source": {
            "path": svg_rel,
            "format": "svg",
            "paths": [svg_rel] if svg_rel else [],
            "panels": len(norm.get("panels") or []),
            "layers": ["panel groups", "named node/edge groups", "legend",
                       "condition callouts"],
            "spec_source": spec_source,
        },
        "export_artifact": export_artifact,
        "content_map": content_map,
        "compliance_checks": audit_report.get("compliance_checks") or [],
        "coverage": audit_report.get("coverage", 0.0),
        "exceptions": exceptions_out,
        "sources": sources,
        "inputs": inputs or [],
        "uncertainty": uncertainty_out,
        "change_log": log,
    }
    if render_meta:
        result["render"] = render_meta
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--audit", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--status", default="partial")
    ap.add_argument("--svg", default=None)
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--png", default=None)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    norm = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    audit_report = {}
    if args.audit:
        try:
            audit_report = json.loads(Path(args.audit).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            audit_report = {}
    result = build_result(args.case_id, norm, audit_report, args.status,
                          args.svg, args.pdf, args.png, args.dpi)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps({"ok": True, "out": args.out, "status": args.status},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
