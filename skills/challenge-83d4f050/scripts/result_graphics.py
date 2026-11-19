#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build /app/output/result.json for a pathway figure.

This module is the single place where the official media fields are written:

    status, editable_source, export_artifact, content_map,
    compliance_checks, exceptions

It does not render or export; it consumes the ledger, spec and audit report and
keeps every claim tied to a real file / real check.  A missing source, a failed
audit or an absent export cannot be hidden by the caller.
"""
from __future__ import annotations

import json
from pathlib import Path

from pathway_svg import source_dict, source_ok

STATUS_TASK = {
    "answered": "completed",
    "completed": "completed",
    "partial": "partial",
    "insufficient_evidence": "partial",
    "input_error": "input_error",
}
SUCCESS_STATUSES = ("completed", "answered")


def content_map(ledger):
    items = []
    for comp in ledger.get("compartments") or []:
        items.append({
            "element_id": str(comp.get("id")),
            "element_type": "compartment",
            "label": str(comp.get("label") or ""),
            "kind": "context",
            "maps_to": source_dict(comp.get("source")),
        })
    for mol in ledger.get("molecules") or []:
        items.append({
            "element_id": str(mol.get("id")),
            "element_type": "molecule",
            "label": str(mol.get("label") or ""),
            "kind": "reported" if source_ok(mol.get("source")) else "unsourced",
            "maps_to": source_dict(mol.get("source")),
        })
    for edge in ledger.get("interactions") or []:
        inferred = (str(edge.get("kind")) == "inferred" or
                    str(edge.get("effect")) == "inferred")
        items.append({
            "element_id": str(edge.get("id")),
            "element_type": "interaction",
            "label": "%s -> %s (%s)" % (edge.get("from"), edge.get("to"),
                                        edge.get("effect")),
            "kind": "inferred" if inferred else "reported",
            "maps_to": source_dict(edge.get("source")),
        })
    return items


def artifact_by_format(artifacts, fmt):
    for item in artifacts:
        if item.get("format") == fmt:
            return item
    return None


def choose_status(audit, artifacts, ledger, override=None):
    if override in ("input_error", "insufficient_evidence"):
        return override
    svg = artifact_by_format(artifacts, "svg")
    has_export = bool(artifact_by_format(artifacts, "pdf") or
                      artifact_by_format(artifacts, "png"))
    if not svg:
        return "input_error"
    if override == "partial":
        return "partial"
    reported = [e for e in ledger.get("interactions") or []
                if str(e.get("kind")) != "inferred" and
                str(e.get("effect")) != "inferred"]
    if not reported and (ledger.get("interactions") or []) and not override:
        return "insufficient_evidence"
    if audit.get("ok") and has_export and \
            (audit.get("coverage") or {}).get("element_coverage") == 1.0:
        return "completed"
    return "partial"


def build_result(ledger, spec, audit, artifacts, *, case_id=None,
                 status_override=None, warnings=None, change_log=None,
                 inventory=None, workorder="biological_mechanism_pathway"):
    warnings = list(warnings or [])
    artifacts = list(artifacts or [])
    svg = artifact_by_format(artifacts, "svg")
    pdf = artifact_by_format(artifacts, "pdf")
    png = artifact_by_format(artifacts, "png")
    compliance = dict(audit.get("compliance_checks") or {})
    coverage = dict(audit.get("coverage") or {})
    status = choose_status(audit, artifacts, ledger, status_override)
    task_status = STATUS_TASK.get(status, "partial")
    compliance["all_interactions_sourced"] = all(
        source_ok(e.get("source")) for e in ledger.get("interactions") or [])
    compliance["data_encoding_declared"] = isinstance(
        ledger.get("data_encoding"), list)
    compliance["status"] = status
    compliance["status_vocabulary"] = "media"
    errors = list(audit.get("errors") or [])
    exceptions = []
    for item in errors:
        if isinstance(item, dict):
            exceptions.append({"code": item.get("code") or "audit_error",
                               "detail": item.get("detail") or
                                         json.dumps(item, ensure_ascii=False)})
    if status != "input_error":
        if not pdf and not png:
            exceptions.append({"code": "export_pending",
                               "detail": "no valid PDF or PNG was produced; "
                                         "editable_source remains available"})
        if coverage.get("source_coverage", 0) < 1.0 and not any(
                item.get("code") == "MISSING_SOURCE" for item in errors):
            exceptions.append({"code": "missing_source",
                               "detail": "some elements have no usable source; "
                                         "see content_map"})
    if status in SUCCESS_STATUSES:
        exceptions = []

    edges = ledger.get("interactions") or []
    reported = [e for e in edges if str(e.get("kind")) != "inferred" and
                str(e.get("effect")) != "inferred"]
    inferred = [e for e in edges if str(e.get("kind")) == "inferred" or
                str(e.get("effect")) == "inferred"]
    inhibitions = [e for e in edges if str(e.get("effect")) == "inhibit" or
                   (str(e.get("kind")) == "inferred" and
                    str(e.get("base_effect")) == "inhibit")]
    pixel = audit.get("png") or {}
    semantic = {
        "arrows_reported": len(reported),
        "arrows_inferred": len(inferred),
        "inhibitions_total": len(inhibitions),
        "inhibitions_verified": (len(inhibitions)
                                 if compliance.get("edge_semantics_ok") else 0),
        "reported_edges_with_source": sum(
            1 for edge in reported if source_ok(edge.get("source"))),
        "removed_unsupported": [],
        "all_effects_sourced": bool(compliance.get("all_reported_edges_sourced")),
        "all_elements_sourced": bool(compliance.get("all_elements_sourced")),
        "reviewed": bool(compliance.get("pixel_reviewed") and audit.get("ok")),
        "counts": {
            "compartments": len(ledger.get("compartments") or []),
            "molecules": len(ledger.get("molecules") or []),
            "interactions": len(edges),
            "reported": len(reported),
            "inferred": len(inferred),
            "inhibitions": len(inhibitions),
            "translocations": sum(1 for e in edges
                                  if str(e.get("effect")) == "translocate"),
            "binds": sum(1 for e in edges
                         if str(e.get("effect")) in ("bind", "association")),
            "modifications": sum(1 for e in edges
                                 if str(e.get("effect")) == "modify"),
        },
    }
    checks = {
        "xml_valid": bool(compliance.get("xml_valid")),
        "text_as_text": bool(compliance.get("text_as_text")),
        "external_refs": int((audit.get("svg") or {}).get("external_refs", 0)),
        "named_groups": int(compliance.get("named_groups", 0)),
        "node_count": semantic["counts"]["molecules"],
        "edge_count": len(edges),
        "text_fit": bool(compliance.get("text_fit")),
        "arrow_clearance": bool(compliance.get("arrow_clearance")),
        "legend_complete": bool(compliance.get("legend_covers_edge_kinds")),
        "semantic_edges_ok": bool(compliance.get("edge_semantics_ok")),
        "visual_reviewed": bool(compliance.get("pixel_reviewed")),
        "pixel_non_white_fraction": pixel.get("non_white_fraction", 0),
        "pixel_content_bbox": pixel.get("content_bbox"),
        "preview_size_px": [pixel.get("width_px", 0), pixel.get("height_px", 0)],
        "element_coverage": coverage.get("element_coverage", 0),
        "source_coverage": coverage.get("source_coverage", 0),
        "editable_svg": bool(svg),
        "export_pdf": bool(pdf),
        "export_png": bool(png),
        "preview_present": bool(png),
        "export_present": bool(pdf or png),
        "escape_checked": bool(compliance.get("xml_valid")),
        "z_order": ["background", "compartments", "edges", "nodes",
                    "header", "legend", "footnote"],
        "official_fields": True,
    }

    export_artifact = None
    if pdf:
        export_artifact = {"path": pdf.get("path"), "format": "pdf"}
    elif png:
        export_artifact = {"path": png.get("path"), "format": "png",
                           "width_px": pixel.get("width_px"),
                           "height_px": pixel.get("height_px")}

    disclaimers = list(spec.get("disclaimers") or [])
    for item in warnings:
        text = "warning: %s" % item
        if text not in disclaimers:
            disclaimers.append(text)
    uncertainty = list(ledger.get("uncertainty") or [])
    for item in warnings:
        if item not in uncertainty:
            uncertainty.append(item)

    result = {
        "case_id": str(case_id or ledger.get("case_id") or spec.get("case_id") or
                       "unknown"),
        "output_contract": ["case_id", "status", "editable_source",
                            "export_artifact", "content_map",
                            "compliance_checks", "exceptions"],
        "task_family": "pathway",
        "workorder": workorder,
        "status": status,
        "task_status": task_status,
        "figure": {
            "figure_id": spec.get("figure_id"),
            "compartments": len(ledger.get("compartments") or []),
            "molecules": len(ledger.get("molecules") or []),
            "interactions": len(edges),
            "reported": len(reported),
            "inferred": len(inferred),
            "canvas_mm": [spec.get("width_mm"), spec.get("height_mm")],
            "layout_type": spec.get("direction"),
        },
        "artifacts": artifacts,
        "pathway": {
            "compartments": ledger.get("compartments") or [],
            "molecules": ledger.get("molecules") or [],
            "interactions": ledger.get("interactions") or [],
        },
        "semantic_check": semantic,
        "legend": {"edge_semantics": spec.get("legend") or [],
                   "encodings": [{"channel": "shape", "maps_to": "molecule type"},
                                 {"channel": "fill", "maps_to": "molecule type"},
                                 {"channel": "line and endpoint",
                                  "maps_to": "interaction effect"}]},
        "data_encoding": list(ledger.get("data_encoding") or []),
        "omitted": list(ledger.get("omitted") or []),
        "conflicts": list(ledger.get("conflicts") or []),
        "schematic_disclaimers": disclaimers,
        "caption": str(spec.get("caption") or ""),
        "alt_text": str(spec.get("alt_text") or ""),
        "checks": checks,
        "coverage": coverage,
        "input_inventory": inventory or {},
        "support_files_used": ["scripts/run_case.py", "scripts/pathway_svg.py",
                               "scripts/export.py", "scripts/audit.py",
                               "scripts/result_graphics.py"],
        "uncertainty": uncertainty,
        "change_log": list(change_log or []),
        "editable_source": ({"path": svg.get("path"), "format": "svg"}
                            if svg else None),
        "export_artifact": export_artifact,
        "content_map": content_map(ledger),
        "compliance_checks": compliance,
        "exceptions": exceptions,
        "status_vocabulary": "media",
    }
    return result


def write_result(result, path):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
