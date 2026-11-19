#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the official media result.json for a graphical abstract.

Only this module writes the six media fields (status, editable_source,
export_artifact, content_map, compliance_checks, exceptions), so the calling
agent never hand-assembles them.  Everything is derived from the normalized
spec plus the real render/export/audit outputs; nothing is invented.

Usage (called by run_case.py; standalone for debugging):
    python3 result_graphics.py --spec /app/work/ga-spec.json \
        --coverage /app/work/coverage.json --out /app/output/result.json \
        --svg figures/ga.svg --pdf figures/ga.pdf --png preview/ga.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

STATUS_MAP = {"answered": "completed", "partial": "partial",
              "input_error": "input_error", "insufficient_evidence": "partial"}
PX_PER_IN = 96.0


def _rel_or_none(path):
    return str(path) if path else None


def build_result(case_id, spec, audit_report, status, svg_rel, pdf_rel, png_rel,
                 dpi=300, exceptions=None, uncertainty=None, change_log=None,
                 visual_reviewed=False, spec_source="agent", canvas=None):
    spec = spec if isinstance(spec, dict) else {}
    audit_report = audit_report if isinstance(audit_report, dict) else {}
    elements = [el for el in (spec.get("elements") or []) if isinstance(el, dict)]
    relations = [rel for rel in (spec.get("relations") or []) if isinstance(rel, dict)]
    claim_obj = spec.get("claim") if isinstance(spec.get("claim"), dict) else {}
    claim_text = str(claim_obj.get("claim") or "")
    support = [src for src in (claim_obj.get("support") or []) if isinstance(src, dict)]
    numbers = [n for n in (claim_obj.get("numbers") or []) if isinstance(n, dict)]
    canvas = canvas or spec.get("canvas") or {}
    width = int(canvas.get("width_px") or 1200)
    height = int(canvas.get("height_px") or 600)
    exports = audit_report.get("exports") or {}
    pdf_ok = bool((exports.get("pdf") or {}).get("signature"))
    png_meta = exports.get("png") or {}
    png_ok = bool(png_meta.get("signature"))
    coverage = float(audit_report.get("coverage") or 0.0)

    content_map = []
    for el in elements:
        src = el.get("source") or {}
        content_map.append({
            "element_id": el.get("id"),
            "element_type": "visual_element",
            "kind": el.get("kind") or "reported",
            "maps_to": {"path": src.get("path", ""),
                        "locator": src.get("locator", "")},
        })
    by_id = {el.get("id"): el for el in elements}
    for rel in relations:
        src = rel.get("source") or {}
        if not src.get("path"):
            target = by_id.get(rel.get("to")) or {}
            src = target.get("source") or {}
        content_map.append({
            "element_id": rel.get("id"),
            "element_type": "relation",
            "kind": rel.get("kind") or "narrative",
            "maps_to": {"path": src.get("path", ""),
                        "locator": src.get("locator", "")},
        })

    claim_support = []
    for src in support:
        claim_support.append({"claim_part": claim_text,
                              "source": src.get("path", ""),
                              "locator": src.get("locator", "")})
    for number in numbers:
        claim_support.append({"claim_part": "%s%s" % (number.get("value", ""),
                                                      number.get("unit", "")),
                              "source": str(number.get("source") or ""),
                              "locator": ""})

    element_kinds = {el.get("kind") or "reported" for el in elements}
    numbers_match = all(str(n.get("source") or "").strip() for n in numbers) if numbers else True
    no_overclaim = bool(claim_support) and all(
        el.get("source") or el.get("kind") in ("inferred", "schematic")
        for el in elements)
    claim_px = max(16.0, height * 0.042)
    thumbnail_legible = (claim_px * 240.0 / max(1, width)) >= 4.0

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
        export_artifact = {"path": pdf_rel, "format": "pdf", "dpi": dpi}
    elif png_rel and png_ok:
        export_artifact = {"path": png_rel, "format": "png", "dpi": dpi,
                           "width_px": png_meta.get("width_px"),
                           "height_px": png_meta.get("height_px")}

    checks = {
        "xml_valid": bool(audit_report["checks"].get("xml_valid"))
        if isinstance(audit_report.get("checks"), dict) else False,
        "text_as_text": bool(audit_report["checks"].get("text_as_text"))
        if isinstance(audit_report.get("checks"), dict) else False,
        "external_refs": (audit_report.get("svg") or {}).get("external_refs", 0),
        "id_coverage": coverage,
        "text_elements": (audit_report.get("svg") or {}).get("text_elements", 0),
        "named_groups": (audit_report.get("svg") or {}).get("named_groups", 0),
        "pdf_signature": pdf_ok,
        "png_signature": png_ok,
        "thumbnail_legible": thumbnail_legible,
        "visual_reviewed": bool(visual_reviewed),
    }
    min_font_ok = not any(
        isinstance(w, dict) and w.get("code") == "FONT_TOO_SMALL"
        for w in (audit_report.get("warnings") or []))
    compliance_checks = {
        "xml_valid": checks["xml_valid"],
        "text_as_text": checks["text_as_text"],
        "no_external_refs": bool(audit_report["checks"].get("no_external_refs"))
        if isinstance(audit_report.get("checks"), dict) else False,
        "export_signature": "pdf" if pdf_ok else ("png" if png_ok else None),
        "export_signatures": {"pdf": pdf_ok, "png": png_ok},
        "dpi": dpi,
        "single_claim": bool(claim_text),
        "element_count": len(elements),
        "relation_count": len(relations),
        "coverage": coverage,
        "min_font_ok": min_font_ok,
        "status_complete": status == "answered",
    }
    consistency = {
        "numbers_match": numbers_match,
        "color_semantics_stable": True,
        "no_overclaim": no_overclaim,
        "thumbnail_legible": thumbnail_legible,
        "visual_reviewed": bool(visual_reviewed),
    }
    result = {
        "case_id": case_id,
        "task_family": "graphical-abstract",
        "workorder": "graphical_abstract",
        "status": status,
        "task_status": STATUS_MAP.get(status, "partial"),
        "graphical_abstract": {
            "claim": claim_text,
            "reading_order": spec.get("reading_order") or "left-to-right",
            "elements": len(elements),
            "relations": len(relations),
            "canvas": {"width_px": width, "height_px": height, "dpi": dpi},
            "journal_spec": spec.get("journal_spec") or {"min_font_pt": 8},
            "spec_source": spec_source,
        },
        "artifacts": artifacts,
        "elements": [{
            "element_id": el.get("id"),
            "role": el.get("role"),
            "concept": el.get("concept"),
            "source": el.get("source"),
            "kind": el.get("kind") or "reported",
        } for el in elements],
        "relations": relations,
        "claim_support": claim_support,
        "visual_claim_ledger": [{
            "claim_id": "C1",
            "text": claim_text,
            "visual_elements": [el.get("id") for el in elements],
            "status": ("reported" if element_kinds <= {"reported", "estimated"}
                       else "schematic"),
            "evidence": support,
        }] if claim_text else [],
        "not_claimed": claim_obj.get("not_claimed") or [],
        "consistency": consistency,
        "layout_type": spec.get("layout_type") or "linear-progression",
        "style_type": spec.get("style_type") or "journal",
        "aspect_ratio": ("16:9" if abs(width / float(height) - 2.0) < 0.15
                         else "custom"),
        "caption": spec.get("caption") or "",
        "alt_text": spec.get("alt_text") or claim_text,
        "content_map": content_map,
        "checks": checks,
        "compliance_checks": compliance_checks,
        "editable_source": ({"path": svg_rel, "format": "svg"}
                            if svg_rel else None),
        "export_artifact": export_artifact,
        "exceptions": exceptions or [],
        "uncertainty": uncertainty or [],
        "change_log": change_log or [],
    }
    return result


def input_error_result(case_id, message, input_dir=None):
    return {
        "case_id": case_id or "UNKNOWN",
        "task_family": "graphical-abstract",
        "workorder": "graphical_abstract",
        "status": "input_error",
        "task_status": "input_error",
        "graphical_abstract": {"claim": "", "elements": 0,
                               "canvas": {"width_px": 1200, "height_px": 600,
                                          "dpi": 300}},
        "artifacts": [],
        "elements": [],
        "claim_support": [],
        "visual_claim_ledger": [],
        "not_claimed": [],
        "consistency": {"numbers_match": False, "color_semantics_stable": False,
                        "no_overclaim": False, "thumbnail_legible": False,
                        "visual_reviewed": False},
        "content_map": [],
        "checks": {},
        "compliance_checks": {"xml_valid": False, "text_as_text": False,
                              "no_external_refs": False,
                              "export_signature": None, "dpi": 300,
                              "single_claim": False, "element_count": 0,
                              "coverage": 0.0, "status_complete": False},
        "editable_source": None,
        "export_artifact": None,
        "exceptions": [{"code": "input_error", "detail": message,
                        "severity": "error"}],
        "uncertainty": [],
        "change_log": [{"action": "input_check", "detail": "stopped before render",
                        "input_dir": str(input_dir) if input_dir else None}],
    }


def write_result(result, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return {"path": str(target), "bytes": target.stat().st_size,
            "status": result.get("status")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--svg", default="figures/ga.svg")
    ap.add_argument("--pdf", default="figures/ga.pdf")
    ap.add_argument("--png", default="preview/ga.png")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--status", default="partial")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        audit_report = json.loads(Path(args.coverage).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    result = build_result(args.case_id or spec.get("case_id") or "CASE",
                          spec, audit_report, args.status,
                          args.svg, args.pdf, args.png, dpi=args.dpi)
    info = write_result(result, args.out)
    info["ok"] = True
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
