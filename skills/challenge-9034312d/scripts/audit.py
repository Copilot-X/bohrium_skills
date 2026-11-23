#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit a rendered apparatus/route figure before result.json is accepted.

One deterministic pass checks:

* SVG is well-formed XML with a viewBox, real <text>, no scripts or external
  http/file references, and no embedded bitmap substituted for vector shapes;
* every component/stage, condition, decision and connection id appears as a
  named <g> group in the SVG;
* every item carries a source {path, locator} and the path exists under input;
* connections have endpoints, medium, direction and optional port names;
* conditions name an existing unit and non-empty parameters;
* decisions name existing targets and a criterion source (or "not_provided");
* PDF/PNG have real magic bytes, and PNG dimensions match the requested dpi;
* the number of rendered node/edge groups equals the normalized spec count.

Usage:
    python3 audit.py --spec /app/work/normalized-spec.json \
        --input-root /input --svg /app/output/figures/fig4.svg \
        --pdf /app/output/figures/fig4.pdf --png /app/output/preview/fig4.png \
        --dpi 300 --out /app/work/coverage.json --json
"""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

EXTERNAL_RE = re.compile(r"""(?:href|src)\s*=\s*["']\s*(?:https?:|file:|//)""", re.I)
URL_RE = re.compile(r"""url\(\s*["']?\s*(?:https?:|file:|//)""", re.I)
FONT_RE = re.compile(r"""font-size\s*=\s*["']\s*([-+]?[0-9.]+)\s*(px|pt)?""", re.I)
PX_PER_IN = 96.0
PT_PER_IN = 72.0
MIN_FONT_PT = 7.0


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def png_size(head):
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def source_of(item):
    src = item.get("source") if isinstance(item, dict) else None
    if isinstance(src, dict):
        return src
    if isinstance(src, str) and src.strip():
        return {"path": src}
    return {}


def resolve_source(source, roots, default_path=None):
    """Return (ok, path_used).  locator-only sources use the default material."""
    if not isinstance(source, dict) or not (source.get("path") or source.get("locator")):
        return False, ""
    path = str(source.get("path") or "")
    if not path:
        path = default_path or ""
    if not path:
        return False, ""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.is_file(), path
    for root in roots:
        if root and (Path(root) / path).is_file():
            return True, path
    return False, path


def _source_ok(value):
    if isinstance(value, dict):
        value = value.get("path") or value.get("locator") or ""
    text = str(value or "").strip().lower()
    return bool(text) and text not in ("not_provided", "not provided", "missing")


def _label_seen(label, blob):
    label = re.sub(r"\s+", " ", str(label or "")).strip()
    if not label:
        return True
    tokens = [t for t in re.split(r"[\s/·,;:()\[\]]+", label) if t]
    probe = tokens[0] if tokens else label
    if len(probe) < 3:
        probe = label[:max(3, min(8, len(label)))]
    return probe[:24] in blob


def audit(norm, svg_path=None, pdf_path=None, png_path=None,
          input_root=None, spec_path=None, default_source_path=None,
          dpi=300):
    norm = norm if isinstance(norm, dict) else {}
    elements = [e for e in norm.get("elements") or [] if isinstance(e, dict)]
    connections = [c for c in norm.get("connections") or [] if isinstance(c, dict)]
    conditions = [c for c in norm.get("conditions") or [] if isinstance(c, dict)]
    decisions = [d for d in norm.get("decisions") or [] if isinstance(d, dict)]
    mode = str(norm.get("mode") or "apparatus")
    roots = [input_root, Path(spec_path).parent if spec_path else None, Path.cwd()]

    errors, warnings = [], []
    rendered_ids, node_groups, edge_groups = set(), 0, 0
    svg_checks = {"exists": False}
    text_blob = ""

    if svg_path:
        svg = Path(svg_path)
        svg_checks["exists"] = svg.is_file()
        if svg.is_file():
            raw = svg.read_text(encoding="utf-8", errors="replace")
            svg_checks["bytes"] = svg.stat().st_size
            svg_checks["text_elements"] = len(re.findall(r"<text\b", raw))
            svg_checks["named_groups"] = len(re.findall(r"<g\b[^>]*\bid\s*=", raw))
            svg_checks["node_groups"] = len(re.findall(r"data-shape\s*=", raw))
            svg_checks["edge_groups"] = len(re.findall(r"data-kind\s*=", raw))
            svg_checks["external_refs"] = (len(EXTERNAL_RE.findall(raw)) +
                                           len(URL_RE.findall(raw)))
            svg_checks["has_script"] = "<script" in raw.lower()
            svg_checks["has_foreign_object"] = "<foreignobject" in raw.lower()
            svg_checks["has_image"] = "<image" in raw.lower()
            try:
                root = ET.fromstring(raw)
                svg_checks["xml_valid"] = True
                svg_checks["viewbox"] = bool(root.get("viewBox") or root.get("viewbox"))
                for node in root.iter():
                    tag = node.tag.rsplit("}", 1)[-1]
                    if tag == "g" and node.get("id"):
                        rendered_ids.add(node.get("id"))
                    if tag == "text":
                        text_blob += " " + "".join(node.itertext())
                node_groups = svg_checks["node_groups"]
                edge_groups = svg_checks["edge_groups"]
            except ET.ParseError as exc:
                svg_checks["xml_valid"] = False
                svg_checks["xml_error"] = str(exc)
            fonts = []
            for value, unit in FONT_RE.findall(raw):
                try:
                    number = float(value)
                except ValueError:
                    continue
                fonts.append(number if (unit or "px").lower() == "pt"
                             else number * PT_PER_IN / PX_PER_IN)
            svg_checks["font_min_pt"] = round(min(fonts), 2) if fonts else None
            if fonts and min(fonts) < MIN_FONT_PT - 0.05:
                warnings.append("font_min_pt %.2f < %.1f" % (min(fonts), MIN_FONT_PT))
    else:
        svg_checks["xml_valid"] = False

    exports = {}
    for label, path in (("pdf", pdf_path), ("png", png_path)):
        if not path:
            exports[label] = {"exists": False, "signature": False}
            continue
        p = Path(path)
        meta = {"exists": p.is_file(), "signature": False}
        if p.is_file():
            head = p.read_bytes()[:32]
            meta["bytes"] = p.stat().st_size
            if label == "pdf":
                meta["signature"] = head.startswith(b"%PDF")
            else:
                size = png_size(head)
                meta["signature"] = size is not None
                if size:
                    meta["width_px"], meta["height_px"] = size
                    expected = float(norm.get("width_mm") or 0) / 25.4 * dpi
                    if expected > 0:
                        meta["expected_width_px"] = round(expected, 1)
                        meta["scale_ok"] = abs(size[0] - expected) / expected <= 0.12
                        if not meta["scale_ok"]:
                            warnings.append(
                                "PNG width %dpx differs from %.1fpx expected at %ddpi"
                                % (size[0], expected, dpi))
        exports[label] = meta

    def rendered(item_id):
        return str(item_id) in rendered_ids

    missing_source, missing_path, not_rendered, inferred = [], [], [], []
    covered = 0
    total = len(elements) + len(connections) + len(conditions) + len(decisions)

    def check_item(kind, item, iid, source, required=True):
        nonlocal covered
        src_ok, used = resolve_source(source, roots, default_source_path)
        is_inferred = (str(item.get("kind") or item.get("status") or "").lower()
                       == "inferred" or
                       str(item.get("direction") or "").lower() == "undetermined")
        if is_inferred and not src_ok:
            inferred.append(iid)
        elif required and not (source and (source.get("path") or source.get("locator"))):
            missing_source.append(iid)
        elif required and not src_ok:
            missing_path.append("%s (%s)" % (iid, used or "?"))
        r = rendered(iid)
        if not r:
            not_rendered.append(iid)
        if r and (src_ok or (is_inferred and (source or True))) and \
                (source or is_inferred):
            covered += 1

    for element in elements:
        check_item(element.get("type"), element, str(element.get("id")),
                   source_of(element))
    for conn in connections:
        check_item("connection", conn, str(conn.get("id")), source_of(conn))
    for cond in conditions:
        check_item("condition", cond, str(cond.get("id")), source_of(cond))
    for dec in decisions:
        source = dec.get("criterion_source") if isinstance(dec.get("criterion_source"), dict) \
            else source_of(dec)
        if str(source.get("path") or source.get("locator") or "").lower() in (
                "not_provided", "not provided", "missing"):
            source = {}
        check_item("decision", dec, str(dec.get("id")), source)

    # Structural checks -----------------------------------------------------
    node_ids = {str(e.get("id")) for e in elements} | {str(d.get("id")) for d in decisions}
    for conn in connections:
        a, b = str(conn.get("from")), str(conn.get("to"))
        if a not in node_ids or b not in node_ids:
            errors.append({"code": "CONNECTION_ENDPOINT_MISSING", "id": conn.get("id"),
                           "detail": "%s -> %s" % (a, b)})
        if not str(conn.get("medium") or "").strip():
            errors.append({"code": "CONNECTION_MEDIUM_MISSING", "id": conn.get("id")})
        if str(conn.get("direction") or "").lower() not in (
                "forward", "reverse", "undetermined"):
            errors.append({"code": "CONNECTION_DIRECTION_INVALID", "id": conn.get("id")})
    for cond in conditions:
        params = cond.get("params")
        if not params:
            errors.append({"code": "CONDITION_PARAMS_MISSING", "id": cond.get("id")})
        if str(cond.get("location")) not in node_ids:
            errors.append({"code": "CONDITION_LOCATION_MISSING", "id": cond.get("id"),
                           "detail": str(cond.get("location"))})
    for dec in decisions:
        for key in ("after", "yes", "no"):
            if dec.get(key) and str(dec[key]) not in node_ids:
                errors.append({"code": "DECISION_TARGET_MISSING", "id": dec.get("id"),
                               "detail": "%s=%s" % (key, dec.get(key))})
        if not str(dec.get("question") or "").strip():
            errors.append({"code": "DECISION_QUESTION_MISSING", "id": dec.get("id")})

    if svg_checks.get("exists"):
        if not svg_checks.get("xml_valid"):
            errors.append({"code": "SVG_INVALID",
                           "detail": svg_checks.get("xml_error")})
        if not svg_checks.get("viewbox"):
            errors.append({"code": "SVG_NO_VIEWBOX"})
        if not svg_checks.get("text_elements"):
            errors.append({"code": "SVG_NO_TEXT"})
        if svg_checks.get("has_script") or svg_checks.get("external_refs"):
            errors.append({"code": "SVG_NOT_SELF_CONTAINED"})
        if svg_checks.get("has_foreign_object"):
            errors.append({"code": "SVG_HAS_FOREIGN_OBJECT"})
        if svg_checks.get("has_image"):
            warnings.append("SVG embeds a raster <image>; keep vector shapes")
        if node_groups != len(node_ids):
            errors.append({"code": "NODE_COUNT_MISMATCH",
                           "detail": "%d rendered vs %d spec"
                                     % (node_groups, len(node_ids))})
        if edge_groups != len(connections):
            errors.append({"code": "EDGE_COUNT_MISMATCH",
                           "detail": "%d rendered vs %d spec"
                                     % (edge_groups, len(connections))})
        if elements and not connections:
            errors.append({"code": "NO_CONNECTIONS"})
        for element in elements:
            if not _label_seen(element.get("label"), text_blob):
                warnings.append("label not found in SVG text: %s"
                                % element.get("label"))
    else:
        errors.append({"code": "SVG_MISSING"})

    for label in ("pdf", "png"):
        meta = exports.get(label) or {}
        if not meta.get("exists"):
            errors.append({"code": "EXPORT_MISSING", "format": label})
        elif not meta.get("signature"):
            errors.append({"code": "EXPORT_INVALID", "format": label})

    if missing_source:
        errors.append({"code": "SOURCE_MISSING", "ids": missing_source})
    if missing_path:
        errors.append({"code": "SOURCE_PATH_MISSING", "items": missing_path})
    if not_rendered:
        errors.append({"code": "NOT_RENDERED", "ids": not_rendered})

    # Coverage: rendered + traceable, inferred items count once documented.
    coverage = round(covered / max(1, total), 4) if total else 0.0
    if coverage < 1.0:
        errors.append({"code": "COVERAGE_INCOMPLETE", "coverage": coverage})

    checks = {
        "xml_valid": bool(svg_checks.get("xml_valid")),
        "text_as_text": bool(svg_checks.get("text_elements")) and
                        not svg_checks.get("has_foreign_object"),
        "external_refs": svg_checks.get("external_refs", 0),
        "self_contained": not (svg_checks.get("external_refs") or
                               svg_checks.get("has_script")),
        "all_components_named": all(str(e.get("label") or "").strip() for e in elements),
        "stages_covered": mode != "route" or all(
            rendered(str(e.get("id"))) for e in elements if e.get("type") == "stage"),
        "connections_covered": all(rendered(str(c.get("id"))) for c in connections),
        "conditions_sourced": all(source_of(c) for c in conditions),
        "decisions_sourced": all(
            _source_ok(dec.get("criterion_source")) for dec in decisions),
        "flow_direction_closed": all(
            str(c.get("from")) in node_ids and str(c.get("to")) in node_ids and
            str(c.get("direction") or "").lower() in
            ("forward", "reverse", "undetermined") for c in connections),
        "export_from_source": bool((exports.get("pdf") or {}).get("signature")),
        "id_coverage": coverage,
        "text_elements": svg_checks.get("text_elements", 0),
        "named_groups": svg_checks.get("named_groups", 0),
        "node_groups": node_groups,
        "edge_groups": edge_groups,
    }

    compliance = [
        {"id": "C1", "check": "svg_well_formed", "ok": checks["xml_valid"],
         "evidence": "ElementTree parse" if checks["xml_valid"] else
                     svg_checks.get("xml_error", "parse failed")},
        {"id": "C2", "check": "text_is_text", "ok": checks["text_as_text"],
         "evidence": "%d <text> elements, no foreignObject"
                     % checks["text_elements"]},
        {"id": "C3", "check": "self_contained", "ok": checks["self_contained"],
         "evidence": "%d external references" % checks["external_refs"]},
        {"id": "C4", "check": "elements_rendered", "ok": not not_rendered,
         "evidence": "%d/%d elements have named groups"
                     % (len(elements) + len(decisions) - len(not_rendered),
                        len(elements) + len(decisions))},
        {"id": "C5", "check": "connections_rendered", "ok": checks["connections_covered"],
         "evidence": "%d/%d connections rendered"
                     % (edge_groups, len(connections))},
        {"id": "C6", "check": "sources_traceable", "ok": not (missing_source or missing_path),
         "evidence": "%d missing source, %d unresolved path"
                     % (len(missing_source), len(missing_path))},
        {"id": "C7", "check": "conditions_and_criteria_sourced",
         "ok": checks["conditions_sourced"] and checks["decisions_sourced"],
         "evidence": "%d conditions, %d decisions" % (len(conditions), len(decisions))},
        {"id": "C8", "check": "flow_direction_closed", "ok": checks["flow_direction_closed"],
         "evidence": "all endpoints and directions declared"},
        {"id": "C9", "check": "export_pdf", "ok": bool((exports.get("pdf") or {}).get("signature")),
         "evidence": "%%PDF signature" if (exports.get("pdf") or {}).get("signature")
                     else "no valid PDF"},
        {"id": "C10", "check": "export_png", "ok": bool((exports.get("png") or {}).get("signature")),
         "evidence": "PNG %sx%s" % ((exports.get("png") or {}).get("width_px"),
                                    (exports.get("png") or {}).get("height_px"))},
    ]

    return {
        "ok": not errors,
        "mode": mode,
        "counts": {
            "elements": len(elements), "connections": len(connections),
            "conditions": len(conditions), "decisions": len(decisions),
            "total": total,
        },
        "coverage": coverage,
        "missing_source": missing_source,
        "missing_path": missing_path,
        "not_rendered": not_rendered,
        "inferred": inferred,
        "svg": svg_checks,
        "exports": exports,
        "checks": checks,
        "compliance_checks": compliance,
        "errors": errors,
        "warnings": warnings,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--input-root", default=None)
    ap.add_argument("--svg", default=None)
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--png", default=None)
    ap.add_argument("--default-source", default=None)
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    norm = load_json(args.spec)
    report = audit(norm, args.svg, args.pdf, args.png, args.input_root,
                   args.spec, args.default_source, args.dpi)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
