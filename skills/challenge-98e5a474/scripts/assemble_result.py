#!/usr/bin/env python3
"""Assemble a contract-complete result.json for the editable-schematic skill.

Usage:
    python3 assemble_result.py --entities /app/work/entities.json \
        --svg /app/output/figures/fig1.svg --root /app/output \
        --export /app/work/export.json --out /app/output/result.json \
        --case-id case-001 --json
    python3 assemble_result.py --before /app/work/start.svg \
        --workorder /app/work/workorder.json ...   # edit mode
    python3 assemble_result.py --self-test --json

The assembler is deterministic: it reads the real entity table, the real SVG
and the real export report, then writes every required task field and media
alias (status/editable_source/export_artifact/content_map/compliance_checks/
exceptions).  It never invents entities, sources or export files: missing
artifacts are reported as null/partial with an exception code.

Stdlib only. Exit 0 = result written (status may be partial), 1 = written but
contract gaps, 2 = unreadable/missing core input.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

OFFICIAL_STATUS = {"answered", "partial", "insufficient_evidence", "input_error"}
EXTERNAL_ATTR = re.compile(
    r"""(?:xlink:)?href\s*=\s*["'](?:https?:|file:|ftp:)""", re.I)


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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def rel(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def sibling(name):
    here = Path(__file__).resolve().parent
    for candidate in (here / name, here / (name + ".md")):
        if candidate.is_file():
            return candidate
    return None


def load_module(name, filename):
    path = sibling(filename)
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def source_of(value):
    if isinstance(value, dict):
        return {"path": value.get("path") or "",
                "locator": value.get("locator") or value.get("page") or ""}
    text = str(value or "")
    return {"path": text, "locator": ""} if text else {}


def entity_ids(data):
    return [str(item.get("id")) for item in as_list(data.get("entities"))
            if isinstance(item, dict) and item.get("id")]


def relation_ids(data):
    return [str(item.get("id") or "R%d" % (index + 1))
            for index, item in enumerate(as_list(data.get("relations")))
            if isinstance(item, dict)]


def svg_checks(svg_path):
    raw = read_text(svg_path)
    result = {
        "exists": bool(raw.strip()),
        "xml_valid": False,
        "text_as_text": raw.count("<text") > 0,
        "text_elements": raw.count("<text"),
        "external_refs": len(EXTERNAL_ATTR.findall(raw)),
        "has_script": "<script" in raw.lower(),
        "named_groups": 0,
        "group_ids": [],
        "viewbox_check": False,
        "marker_check": True,
        "fonts": sorted(set(re.findall(r'font-family="([^"]+)"', raw))),
    }
    if not raw.strip():
        return result
    try:
        root = ET.fromstring(raw)
        result["xml_valid"] = root.tag.split("}")[-1] == "svg"
    except ET.ParseError:
        return result
    viewbox = root.get("viewBox")
    if viewbox:
        try:
            values = [float(x) for x in re.split(r"[,\s]+", viewbox.strip()) if x]
            result["viewbox_check"] = (len(values) == 4 and values[2] > 0
                                       and values[3] > 0)
        except ValueError:
            result["viewbox_check"] = False
    groups = []
    for element in root.iter():
        if element.tag.split("}")[-1] != "g":
            continue
        eid = element.get("id")
        if not eid:
            continue
        groups.append(eid)
        # Only leaf groups count as element groups.
        if any(child.tag.split("}")[-1] == "g" and child.get("id")
               for child in element.iter() if child is not element):
            groups.pop()
    result["group_ids"] = sorted(set(groups))
    result["named_groups"] = len(result["group_ids"])
    markers = set(re.findall(r'<marker[^>]*\bid="([^"]+)"', raw))
    refs = set(re.findall(r"url\(#([^)]+)\)", raw))
    result["marker_check"] = refs.issubset(markers)
    return result


def find_export(root, svg_path, export_report):
    """Return (pdf_artifact, png_artifact, html_artifact, exceptions)."""
    root = Path(root)
    pdf = png = html = None
    exceptions = []
    report = export_report if isinstance(export_report, dict) else {}
    for item in report.get("artifacts") or []:
        if not isinstance(item, dict):
            continue
        raw = Path(str(item.get("path") or ""))
        candidates = [raw] if raw.is_absolute() else [root / raw, raw]
        path = next((candidate for candidate in candidates
                     if candidate.is_file()), None)
        if path is None:
            continue
        fmt = str(item.get("format") or path.suffix.lstrip(".")).lower()
        try:
            artifact_path = rel(path, root)
        except (ValueError, OSError):
            artifact_path = path.as_posix()
        row = {"role": "export", "path": artifact_path, "format": fmt,
               "bytes": path.stat().st_size}
        if fmt == "pdf":
            if path.read_bytes()[:4] == b"%PDF":
                row["exported_by"] = item.get("exported_by") or "unknown"
                pdf = row
        elif fmt in ("png", "tif", "tiff"):
            head = path.read_bytes()[:24]
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                row["width_px"] = int.from_bytes(head[16:20], "big")
                row["height_px"] = int.from_bytes(head[20:24], "big")
                row["dpi"] = item.get("dpi") or report.get("dpi")
                row["exported_by"] = item.get("exported_by") or "unknown"
                png = row
    if pdf is None:
        for candidate in sorted((root / "figures").glob("*.pdf")):
            if candidate.read_bytes()[:4] == b"%PDF":
                pdf = {"role": "export", "path": rel(candidate, root),
                       "format": "pdf", "bytes": candidate.stat().st_size,
                       "exported_by": "filesystem"}
                break
    if png is None:
        for candidate in sorted((root / "preview").glob("*.png")):
            head = candidate.read_bytes()[:24]
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                png = {"role": "preview", "path": rel(candidate, root),
                       "format": "png", "bytes": candidate.stat().st_size,
                       "width_px": int.from_bytes(head[16:20], "big"),
                       "height_px": int.from_bytes(head[20:24], "big"),
                       "dpi": report.get("dpi"),
                       "exported_by": "filesystem"}
                break
    for candidate in sorted((root / "preview").glob("*.html")):
        html = {"role": "preview", "path": rel(candidate, root),
                "format": "html", "bytes": candidate.stat().st_size}
        break
    for item in report.get("exceptions") or []:
        if isinstance(item, dict) and item.get("code"):
            exceptions.append(item)
    if pdf is None and png is None and not any(
            item.get("code") == "export_unavailable" for item in exceptions):
        exceptions.append({
            "code": "export_unavailable",
            "detail": "no real PDF/PNG export was found; deliver the SVG and "
                      "an HTML preview, and keep export_artifact null",
        })
    return pdf, png, html, exceptions


def contract_map(data, svg_checks):
    elements = []
    for item in as_list(data.get("entities")):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        elements.append({
            "element_id": str(item["id"]),
            "maps_to": source_of(item.get("source")),
            "kind": "inferred" if item.get("kind") == "inferred" else "reported",
            "label": str(item.get("label") or ""),
        })
    for index, item in enumerate(as_list(data.get("relations")), 1):
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id") or "R%d" % index)
        elements.append({
            "element_id": rid,
            "maps_to": source_of(item.get("source")),
            "kind": "inferred" if (item.get("kind") == "inferred" or
                                   item.get("semantic") == "inferred") else "reported",
            "label": str(item.get("label") or ""),
        })
    return elements


def edit_block(before_path, workorder_path, svg_path):
    if not before_path:
        return {"mode": "create", "diff": [], "preserved": [], "removed": []}
    module = load_module("compare_svg", "compare_svg.py")
    if module is None:
        return {"mode": "edit", "starting_figure": str(before_path),
                "diff": [], "preserved": [], "removed": [],
                "note": "compare_svg.py unavailable; manual review required"}
    try:
        before = module.inventory(before_path)
        after = module.inventory(svg_path)
        workorder = load_json(workorder_path, {}) if workorder_path else None
        report = module.compare(before, after, workorder)
    except Exception as exc:  # noqa: BLE001
        return {"mode": "edit", "starting_figure": str(before_path),
                "diff": [], "preserved": [], "removed": [],
                "note": "comparison failed: %s: %s" % (type(exc).__name__, exc)}
    diff = []
    for eid in report.get("added") or []:
        diff.append({"element_id": eid, "action": "added", "after": after[eid]["text"]})
    for eid in report.get("modified") or []:
        diff.append({"element_id": eid, "action": "modified",
                     "before": before[eid]["text"], "after": after[eid]["text"]})
    for eid in report.get("removed") or []:
        diff.append({"element_id": eid, "action": "removed",
                     "before": before[eid]["text"]})
    return {"mode": "edit", "starting_figure": str(before_path),
            "diff": diff, "preserved": report.get("preserved") or [],
            "removed": report.get("removed") or [],
            "violations": report.get("violations") or [],
            "scope_enforced": bool(report.get("scope_enforced"))}


def assemble(entities_path, svg_path, root, export_path=None, out_path=None,
             case_id=None, before_path=None, workorder_path=None,
             dpi=300):
    data = load_json(entities_path, None)
    if not isinstance(data, dict):
        raise SystemExit("entities file is unreadable: %s" % entities_path)
    if not Path(svg_path).is_file():
        raise SystemExit("SVG is missing: %s" % svg_path)
    base = Path(root)
    export_report = load_json(export_path, {}) if export_path else {}
    measured = svg_checks(svg_path)
    pdf, png, html, exceptions = find_export(base, svg_path, export_report)
    if not measured["xml_valid"]:
        exceptions.append({"code": "svg_parse_error",
                           "detail": "the editable SVG is not well-formed"})
    if measured["external_refs"]:
        exceptions.append({"code": "external_ref",
                           "detail": "the SVG references external resources"})
    entities = as_list(data.get("entities"))
    relations = as_list(data.get("relations"))
    eids = entity_ids(data)
    rids = relation_ids(data)
    group_ids = set(measured["group_ids"])
    missing_entities = sorted(set(eids) - group_ids)
    missing_relations = sorted(set(rids) - group_ids)
    element_map = contract_map(data, measured)
    inferred = [row["element_id"] for row in element_map
                if row["kind"] == "inferred"]
    edit = edit_block(before_path, workorder_path, svg_path)
    edit_problems = list(edit.get("violations") or [])
    if edit.get("mode") == "edit" and not edit.get("diff") and not before_path:
        edit_problems.append("edit mode has no diff")
    answered = bool(measured["xml_valid"] and measured["exists"] and
                    measured["viewbox_check"] and measured["text_as_text"] and
                    measured["named_groups"] and not measured["external_refs"] and
                    not measured["has_script"] and (pdf or png) and
                    not missing_entities and not edit_problems and
                    not any(item.get("code") in ("svg_parse_error", "external_ref")
                            for item in exceptions))
    status = "answered" if answered else "partial"
    if not entities:
        status = "input_error"
    task_status = "completed" if status == "answered" else status
    artifacts = [{"role": "editable", "path": rel(svg_path, base),
                  "format": "svg", "bytes": Path(svg_path).stat().st_size}]
    if pdf:
        artifacts.append(pdf)
    if png:
        artifacts.append(png)
    if html:
        artifacts.append(html)
    checks = {
        "xml_valid": measured["xml_valid"],
        "text_as_text": measured["text_as_text"],
        "external_refs": measured["external_refs"],
        "named_groups": measured["named_groups"],
        "fonts": measured["fonts"],
        "viewbox_check": measured["viewbox_check"],
        "marker_check": measured["marker_check"],
        "export_from_source": bool(pdf or png),
        "entity_group_coverage": "%d/%d" % (len(set(eids) & group_ids), len(eids)),
        "relation_group_coverage": "%d/%d" % (len(set(rids) & group_ids), len(rids)),
        "missing_groups": {"entities": missing_entities, "relations": missing_relations},
    }
    compliance = {
        "xml_valid": measured["xml_valid"],
        "text_as_text": measured["text_as_text"],
        "no_external_refs": measured["external_refs"] == 0,
        "export_signature": (pdf or {}).get("format") or (png or {}).get("format") or None,
        "dpi": (png or {}).get("dpi") or dpi,
        "dpi_source": (png or {}).get("exported_by") or "not_exported",
    }
    uncertainties = []
    for exception in exceptions:
        uncertainties.append({"item": exception.get("code"),
                              "reason": exception.get("detail", ""),
                              "impact": "the export or contract is incomplete",
                              "action": "deliver the editable SVG and record the gap"})
    if missing_entities:
        uncertainties.append({"item": "entity_coverage",
                              "reason": "entities without <g id>: %s" %
                                        ", ".join(missing_entities),
                              "impact": "element_source_map cannot be verified",
                              "action": "add named groups for every entity"})
    case_value = str(case_id or data.get("case_id") or "CASE")
    result = {
        "case_id": case_value,
        "task_family": "schematic",
        "workorder": str(data.get("workorder") or
                         "editable_relationship_schematic"),
        "status": status,
        "task_status": task_status,
        "figure": {
            "figure_id": str(data.get("figure_id") or Path(svg_path).stem),
            "canvas_mm": data.get("canvas_mm") or [180, 90],
            "nodes": len(entities),
            "edges": len(relations),
            "compartments": len(as_list(data.get("groups"))),
            "inferred": len(inferred),
        },
        "artifacts": artifacts,
        "element_source_map": element_map,
        "edit": edit,
        "checks": checks,
        "schematic_disclaimers": [
            "%s 为推断关系，材料未直接给出方向或因果。" % eid
            for eid in inferred
        ],
        "uncertainty": uncertainties,
        "uncertainties": uncertainties,
        "change_log": [
            {"action": "build", "detail": "editable SVG rendered from the entity table"},
            {"action": "export", "detail": "PDF=%s PNG=%s" % (bool(pdf), bool(png))},
            {"action": "assemble", "detail": "result.json assembled by assemble_result.py"},
        ],
        "support_files_used": ["scripts/build_schematic.py",
                               "scripts/export_schematic.py",
                               "scripts/assemble_result.py"],
        "editable_source": {"path": rel(svg_path, base), "format": "svg"},
        "export_artifact": ({"path": pdf["path"], "format": "pdf"}
                            if pdf else
                            ({"path": png["path"], "format": "png"} if png else None)),
        "content_map": element_map,
        "compliance_checks": compliance,
        "exceptions": exceptions,
        "generated_at": now_iso(),
    }
    gaps = []
    if status != "answered":
        gaps.append("status=%s" % status)
    if not (pdf or png):
        gaps.append("no real PDF/PNG export")
    if missing_entities:
        gaps.append("entity groups missing")
    if edit_problems:
        gaps.append("edit violations: %s" % ", ".join(edit_problems))
    result["contract_gaps"] = gaps
    if out_path:
        write_json(out_path, result)
    return result, gaps


def self_test():
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    build_module = load_module("build_schematic", "build_schematic.py")
    export_module = load_module("export_schematic", "export_schematic.py")
    if build_module is None or export_module is None:
        print(json.dumps({"ok": False, "self_test": True,
                          "error": "build_schematic.py/export_schematic.py missing"}))
        return 2
    with tempfile.TemporaryDirectory(prefix="assemble-selftest-") as td:
        root = Path(td)
        table = {
            "figure_id": "fig1", "canvas_mm": [120, 60],
            "entities": [
                {"id": "E1", "label": "A", "type": "component",
                 "source": {"path": "materials/m.md", "locator": "p1"}},
                {"id": "E2", "label": "B", "type": "process",
                 "source": {"path": "materials/m.md", "locator": "p2"}},
            ],
            "relations": [
                {"id": "R1", "from": "E1", "to": "E2", "semantic": "activate",
                 "label": "activates",
                 "source": {"path": "materials/m.md", "locator": "p2"}},
            ],
            "legend": [{"kind": "activate", "label": "activation"}],
        }
        entities_path = root / "entities.json"
        write_json(entities_path, table)
        svg_path = root / "output" / "figures" / "fig1.svg"
        spec = build_module.normalize(dict(table))
        build_module.build(spec, svg_path)
        report = export_module.export(
            svg_path, root / "output",
            {"pdf": root / "output" / "figures" / "fig1.pdf",
             "png": root / "output" / "preview" / "fig1.png",
             "html": root / "output" / "preview" / "fig1.html"},
            ("pdf", "png"), 300)
        export_path = root / "export.json"
        write_json(export_path, report)
        result, gaps = assemble(entities_path, svg_path, root / "output",
                                export_path, root / "output" / "result.json",
                                case_id="SYNTH-ASSEMBLE")
        has_export = any(
            item.get("role") == "export" and item.get("format") in ("pdf", "png")
            for item in report.get("artifacts") or [])
        check("result_written", (root / "output" / "result.json").is_file())
        check("status_matches_renderer",
              result.get("status") == ("answered" if has_export else "partial"),
              "%s has_export=%s" % (result.get("status"), has_export))
        check("artifacts_exist", all(
            (root / "output" / item["path"]).is_file()
            for item in result.get("artifacts") or []))
        check("source_map_covers_elements",
              {row["element_id"] for row in result.get("element_source_map") or []}
              == {"E1", "E2", "R1"})
        check("export_metadata_consistent",
              bool(result.get("export_artifact")) == has_export)
        check("gaps_match_renderer", (not gaps) if has_export else bool(gaps),
              str(gaps))

        # Boundary: remove exports (not the editable SVG) -> partial/null export.
        for folder, pattern in (("figures", "*.pdf"), ("preview", "*.png"),
                                ("preview", "*.html"), ("preview", "*.tif")):
            for path in (root / "output" / folder).glob(pattern):
                path.unlink()
        result_b, gaps_b = assemble(entities_path, svg_path, root / "output",
                                    None, root / "output" / "result.json",
                                    case_id="SYNTH-ASSEMBLE")
        check("missing_export_partial", result_b.get("status") == "partial")
        check("missing_export_null", result_b.get("export_artifact") is None)
        check("missing_export_exception", any(
            item.get("code") == "export_unavailable"
            for item in result_b.get("exceptions") or []))

        # Boundary: entities missing -> input_error.
        empty_entities = root / "empty.json"
        write_json(empty_entities, {"entities": [], "relations": []})
        result_c, _ = assemble(empty_entities, svg_path, root / "output",
                               None, None, case_id="EMPTY")
        check("empty_entities_input_error", result_c.get("status") == "input_error")

    ok = all(row["ok"] for row in checks)
    print(json.dumps({"ok": ok, "self_test": True, "checks": checks},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Assemble schematic result.json")
    parser.add_argument("--entities", default=None)
    parser.add_argument("--svg", default=None)
    parser.add_argument("--root", default=None)
    parser.add_argument("--export", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--before", default=None)
    parser.add_argument("--workorder", default=None)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not (args.entities and args.svg):
        parser.error("--entities and --svg are required unless --self-test is used")
    root = args.root or str(Path(args.svg).parent.parent)
    out = args.out or str(Path(root) / "result.json")
    try:
        result, gaps = assemble(args.entities, args.svg, root, args.export, out,
                                args.case_id, args.before, args.workorder,
                                args.dpi)
    except SystemExit as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False,
                         indent=2))
        return 2
    report = {"ok": not gaps, "status": result.get("status"),
              "result": out, "gaps": gaps,
              "artifacts": [item.get("path") for item in result.get("artifacts") or []]}
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
          "%s -> %s (%s)" % (result.get("status"), out, "; ".join(gaps) or "ok"))
    return 0 if not gaps else 1


if __name__ == "__main__":
    sys.exit(main())
