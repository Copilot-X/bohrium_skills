#!/usr/bin/env python3
"""Audit an editable-schematic result, entity table and SVG (optional gate).

Usage:
    python3 check_schematic_result.py --result /app/output/result.json \
        --entities /app/work/entities.json --svg /app/output/figures/fig1.svg \
        --before /app/work/start.svg --workorder /app/work/workorder.json \
        --root /app/output --json
    python3 check_schematic_result.py --self-test --json

The check is advisory: exit 1 means "problems found", not "deliverable
blocked".  Missing auxiliary inputs are reported as problems with exit 1;
an unreadable/missing result.json exits 2.  Both print a JSON report and
never a traceback.

Checks
  * result has the task fields (case_id/figure/artifacts/element_source_map/
    checks) and the media aliases (status/editable_source/export_artifact/
    content_map/compliance_checks/exceptions)
  * entities/relations have unique ids, valid endpoints and sources
  * artifacts exist; PNG/PDF/HTML previews pass signature checks
  * SVG parses, keeps <text>, has a viewBox, named groups, no script/external
    href; every entity/relation id appears as a <g id>
  * edit.diff actions are valid; scope stays inside the workorder
  * result.checks booleans do not contradict the measured SVG

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SEMANTICS = {"flow", "activate", "inhibit", "convert", "contain", "bind",
             "translocate", "inferred", "default"}
ACTIONS = {"added", "modified", "removed", "unchanged"}
OFFICIAL_STATUS = {"answered", "partial", "insufficient_evidence", "input_error"}
LEGACY_STATUS = {"completed": "answered", "complete": "answered", "done": "answered",
                 "ok": "answered", "success": "answered", "failed": "input_error",
                 "insufficient_input": "input_error"}
SVG_NS = "{http://www.w3.org/2000/svg}"
EXTERNAL_ATTR = re.compile(r"""(?:xlink:)?href\s*=\s*["'](?:https?:|file:|ftp:)""", re.I)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def source_ok(value):
    if isinstance(value, dict):
        value = value.get("path") or value.get("locator") or value.get("page")
    text = str(value or "").strip()
    return bool(text) and text.lower() not in ("not_provided", "unknown", "n/a", "none")


def resolve(base, rel):
    candidate = Path(str(rel))
    return candidate if candidate.is_absolute() else Path(base) / candidate


def check_entities(data, problems, warnings, checks):
    ids, relation_ids = set(), set()
    if not isinstance(data, dict):
        problems.append("entities root is not an object")
        return ids, relation_ids
    entities = data.get("entities")
    relations = data.get("relations") or []
    if not isinstance(entities, list):
        problems.append("entities file has no `entities` array")
        entities = []
    if not entities:
        problems.append("entities is empty: nothing to map into the figure")
    if not isinstance(relations, list):
        problems.append("`relations` is not a list")
        relations = []
    for index, item in enumerate(entities):
        if not isinstance(item, dict):
            problems.append("entity[%d] is not an object" % index)
            continue
        eid = item.get("id")
        if not eid:
            problems.append("entity[%d] missing id" % index)
            continue
        eid = str(eid)
        if eid in ids:
            problems.append("duplicate entity id: %s" % eid)
        ids.add(eid)
        if not item.get("label"):
            problems.append("entity %s missing label" % eid)
        if not item.get("type"):
            warnings.append("entity %s missing type" % eid)
        if not source_ok(item.get("source")):
            warnings.append("entity %s has no usable source" % eid)
    for index, item in enumerate(relations):
        if not isinstance(item, dict):
            problems.append("relation[%d] is not an object" % index)
            continue
        rid = str(item.get("id") or "R%d" % (index + 1))
        if rid in relation_ids:
            problems.append("duplicate relation id: %s" % rid)
        relation_ids.add(rid)
        if str(item.get("from")) not in ids:
            problems.append("relation %s missing/unknown from endpoint" % rid)
        if str(item.get("to")) not in ids:
            problems.append("relation %s missing/unknown to endpoint" % rid)
        semantic = item.get("semantic")
        if semantic not in SEMANTICS:
            warnings.append("relation %s unknown semantic: %s" % (rid, semantic))
        if not item.get("label") and semantic not in ("default", "contain", "bind", None):
            warnings.append("relation %s has no label" % rid)
        if not source_ok(item.get("source")):
            warnings.append("relation %s has no usable source" % rid)
    checks["entities"] = len(entities)
    checks["relations"] = len(relations)
    return ids, relation_ids


def has_named_child_group(element):
    for child in element.iter():
        if child is element:
            continue
        if child.tag.split("}")[-1] == "g" and child.get("id"):
            return True
    return False


def svg_inventory(raw):
    root = ET.fromstring(raw)
    items = {}
    for element in root.iter():
        if element.tag.split("}")[-1] != "g":
            continue
        eid = element.get("id")
        if not eid or has_named_child_group(element):
            continue
        texts = []
        for node in element.iter():
            if node.tag.split("}")[-1] == "text" and node.text:
                texts.append(" ".join(node.text.split()))
        items[eid] = " | ".join(texts)
    return items


def check_svg(path, problems, warnings, checks):
    p = Path(path)
    if not p.is_file():
        problems.append("missing SVG: %s" % p)
        return {}, False
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        problems.append("cannot read SVG: %s" % exc)
        return {}, False
    if not raw.strip():
        problems.append("SVG is empty")
        return {}, False
    parsed = False
    items = {}
    checks["svg_text_elements"] = raw.count("<text")
    checks["svg_named_groups"] = 0
    checks["svg_external_refs"] = len(EXTERNAL_ATTR.findall(raw))
    checks["svg_has_script"] = "<script" in raw.lower()
    if checks["svg_has_script"]:
        problems.append("SVG contains <script>")
    if checks["svg_external_refs"]:
        problems.append("SVG references an external resource (%d href)"
                        % checks["svg_external_refs"])
    try:
        root = ET.fromstring(raw)
        parsed = True
    except ET.ParseError as exc:
        problems.append("SVG not well-formed: %s" % exc)
        return {}, False
    if root.tag.split("}")[-1] != "svg":
        problems.append("SVG root is not <svg>")
    if "viewBox" not in root.attrib:
        problems.append("SVG missing viewBox")
    else:
        try:
            numbers = [float(x) for x in re.split(r"[,\s]+", root.attrib["viewBox"].strip()) if x]
            if len(numbers) != 4 or numbers[2] <= 0 or numbers[3] <= 0:
                problems.append("SVG has an invalid viewBox: %s" % root.attrib["viewBox"])
        except ValueError:
            problems.append("SVG viewBox is not numeric")
    if checks["svg_text_elements"] == 0:
        problems.append("SVG has no <text> elements (labels may be outlines)")
    try:
        items = svg_inventory(raw)
    except ET.ParseError:
        items = {}
    checks["svg_named_groups"] = len(items)
    if len(items) == 0:
        problems.append("SVG has no named <g id> groups")
    checks["svg_fonts"] = sorted(set(re.findall(r'font-family="([^"]+)"', raw)))
    marker_ids = set(re.findall(r'<marker[^>]*\bid="([^"]+)"', raw))
    for ref in sorted(set(re.findall(r"url\(#([^)]+)\)", raw))):
        if ref not in marker_ids:
            problems.append("SVG references missing marker definition: %s" % ref)
    return items, parsed


def check_media_aliases(result, base, problems, warnings, checks):
    status = result.get("status")
    if status is None:
        problems.append("result.json missing status")
    elif not isinstance(status, str):
        problems.append("status must be a string, got %s" % type(status).__name__)
    elif status in LEGACY_STATUS:
        warnings.append("status %r is legacy; normalize to %r for the official contract"
                        % (status, LEGACY_STATUS[status]))
    elif status not in OFFICIAL_STATUS:
        problems.append("invalid status: %r (use answered|partial|insufficient_evidence|"
                        "input_error)" % (status,))
    for key in ("editable_source", "export_artifact", "content_map",
                "compliance_checks", "exceptions"):
        if key not in result:
            problems.append("result.json missing official alias %s" % key)

    editable = result.get("editable_source")
    if editable is not None:
        rel = editable.get("path") if isinstance(editable, dict) else editable
        if not rel:
            problems.append("editable_source has no path")
        elif not resolve(base, rel).is_file():
            problems.append("editable_source does not exist: %s" % rel)

    export = result.get("export_artifact")
    if export is None:
        if status == "answered":
            problems.append("status=answered but export_artifact is null")
    else:
        rel = export.get("path") if isinstance(export, dict) else export
        if not rel:
            problems.append("export_artifact has no path")
        elif not resolve(base, rel).is_file():
            problems.append("export_artifact does not exist: %s" % rel)
        else:
            fmt = str((export.get("format") if isinstance(export, dict) else "")
                      or Path(str(rel)).suffix.lstrip(".")).lower()
            if fmt in ("html", "htm"):
                problems.append("export_artifact must be a real PDF/PNG, not HTML: %s"
                                % rel)

    content_map = result.get("content_map")
    if content_map is not None:
        if not isinstance(content_map, list):
            problems.append("content_map must be a list")
        elif not content_map:
            problems.append("content_map is empty; every entity/relation needs a mapping")

    compliance = result.get("compliance_checks")
    if compliance is not None:
        if not isinstance(compliance, dict):
            problems.append("compliance_checks must be an object")
        elif not compliance:
            warnings.append("compliance_checks is empty")

    exceptions = result.get("exceptions")
    if exceptions is not None and not isinstance(exceptions, list):
        problems.append("exceptions must be a list")
    checks["media_status"] = status


def check_result(result, base, problems, warnings, checks):
    if not isinstance(result, dict):
        problems.append("result root is not an object")
        return
    for key in ("case_id", "figure", "artifacts", "element_source_map", "checks"):
        if key not in result:
            problems.append("result.json missing %s" % key)

    artifacts = result.get("artifacts") or []
    if not isinstance(artifacts, list):
        problems.append("artifacts must be a list")
        artifacts = []
    roles = {}
    for index, item in enumerate(artifacts):
        if not isinstance(item, dict):
            problems.append("artifact[%d] is not an object" % index)
            continue
        role = str(item.get("role") or "").lower()
        rel = item.get("path")
        if not rel:
            problems.append("artifact[%d] missing path" % index)
            continue
        candidate = resolve(base, rel)
        if not candidate.is_file():
            problems.append("artifact missing: %s" % rel)
            continue
        roles.setdefault(role, candidate)
        fmt = str(item.get("format") or candidate.suffix.lstrip(".")).lower()
        if role == "preview" and fmt == "png":
            try:
                head = candidate.read_bytes()[:24]
            except OSError as exc:
                problems.append("cannot read preview: %s" % exc)
                continue
            if head[:8] != b"\x89PNG\r\n\x1a\n":
                problems.append("preview is not a PNG: %s" % rel)
            else:
                checks["preview_size"] = [int.from_bytes(head[16:20], "big"),
                                          int.from_bytes(head[20:24], "big")]
        elif role == "preview" and fmt == "html":
            try:
                html = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                problems.append("cannot read HTML preview: %s" % exc)
                continue
            if "<svg" not in html:
                problems.append("HTML preview does not inline the SVG: %s" % rel)
            if re.search(r"""(?:src|href)\s*=\s*["']https?://""", html, re.I):
                problems.append("HTML preview is not self-contained: %s" % rel)
        elif role == "export":
            try:
                head = candidate.read_bytes()[:8]
            except OSError as exc:
                problems.append("cannot read export: %s" % exc)
                continue
            if fmt == "pdf" and not head.startswith(b"%PDF"):
                problems.append("export is not a PDF: %s" % rel)
            if fmt in ("png", "tif", "tiff") and not head.startswith(b"\x89PNG"):
                problems.append("export is not a PNG: %s" % rel)
    for required in ("editable",):
        if required not in roles:
            problems.append("result.artifacts missing role=%s" % required)
    if not ({"preview", "export", "html_preview"} & set(roles)):
        problems.append("result.artifacts needs a preview/export artifact "
                        "(or an HTML preview when no renderer is available)")
    checks["artifact_roles"] = sorted(roles)

    source_map = result.get("element_source_map") or []
    if not isinstance(source_map, list):
        problems.append("element_source_map must be a list")
        source_map = []
    if not source_map:
        problems.append("element_source_map is empty")
    for index, item in enumerate(source_map):
        if not isinstance(item, dict):
            problems.append("element_source_map[%d] is not an object" % index)
            continue
        if not item.get("element_id"):
            problems.append("element_source_map[%d] missing element_id" % index)
        if not source_ok(item.get("maps_to") or item.get("source")):
            warnings.append("element_source_map[%d] has no usable maps_to/source" % index)
    checks["source_map_items"] = len(source_map)

    edit = result.get("edit")
    if not isinstance(edit, dict):
        warnings.append("result.edit is missing or not an object")
    else:
        mode = edit.get("mode")
        if mode not in ("create", "edit", None):
            problems.append("edit.mode must be create or edit, got %r" % (mode,))
        if mode == "edit" and not edit.get("starting_figure"):
            warnings.append("edit.mode=edit but edit.starting_figure is empty")
        if mode == "edit" and not edit.get("diff"):
            warnings.append("edit.mode=edit but edit.diff is empty; verify manually")
        if edit.get("preserved") is not None and not isinstance(edit.get("preserved"), list):
            problems.append("edit.preserved must be a list")
        for index, item in enumerate(edit.get("diff") or []):
            if not isinstance(item, dict):
                problems.append("edit.diff[%d] is not an object" % index)
                continue
            if item.get("action") not in ACTIONS:
                problems.append("edit.diff[%d] invalid action: %s"
                                % (index, item.get("action")))
        checks["edit_mode"] = mode
    check_media_aliases(result, base, problems, warnings, checks)


def check_coverage(entity_ids, relation_ids, items, problems, warnings, checks):
    if not items:
        return
    ids = set(items)
    if entity_ids:
        missing = sorted(entity_ids - ids)
        if missing:
            warnings.append("entities without <g id> in the SVG: %s" % ", ".join(missing))
        if len(missing) == len(entity_ids):
            problems.append("no entity id appears as a <g id> in the SVG")
    if relation_ids:
        missing = sorted(relation_ids - ids)
        if missing:
            warnings.append("relations without <g id> in the SVG: %s" % ", ".join(missing))
    checks["entity_group_coverage"] = "%d/%d" % (len(entity_ids & ids), len(entity_ids))
    checks["relation_group_coverage"] = "%d/%d" % (len(relation_ids & ids), len(relation_ids))


def workorder_targets(workorder, warnings):
    targets, add, remove = set(), set(), set()
    if workorder is None:
        return targets, add, remove
    if isinstance(workorder, list):
        targets = {str(x) for x in workorder}
        warnings.append("workorder is a bare list; treating items as edit targets")
    elif isinstance(workorder, dict):
        known = {"targets", "modify", "add", "remove", "source", "note", "workorder"}
        for key in workorder:
            if key not in known:
                warnings.append("workorder has unknown key: %s" % key)
        for key in ("targets", "modify"):
            targets |= {str(x) for x in _as_list(workorder.get(key))}
        add = {str(x) for x in _as_list(workorder.get("add"))}
        remove = {str(x) for x in _as_list(workorder.get("remove"))}
        if not (targets or add or remove):
            warnings.append("workorder has no targets/add/remove; edit scope cannot be verified")
    else:
        warnings.append("workorder is neither a list nor an object; ignored")
    return targets, add, remove


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def check_edit_scope(before, after, workorder, problems, warnings, checks):
    if before is None or after is None:
        return
    targets, add_allowed, remove_allowed = workorder_targets(workorder, warnings)
    known = set(before) | set(after)
    for target in sorted((targets | add_allowed | remove_allowed) - known):
        warnings.append("workorder target not present in either SVG: %s" % target)
    removed = set(before) - set(after)
    added = set(after) - set(before)
    modified = {eid for eid in (set(before) & set(after)) if before[eid] != after[eid]}
    for eid in sorted(removed | added | modified):
        if eid in targets:
            continue
        if eid in added and eid in add_allowed:
            continue
        if eid in removed and eid in remove_allowed:
            continue
        problems.append("edit changed element outside workorder: %s" % eid)
    checks["edit_scope"] = {"added": sorted(added), "removed": sorted(removed),
                            "modified": sorted(modified), "enforced": bool(
                                targets or add_allowed or remove_allowed)}


def honesty_checks(result, measured, problems, warnings):
    claimed = result.get("checks")
    if not isinstance(claimed, dict):
        return
    if claimed.get("xml_valid") is True and not measured.get("svg_parsed"):
        problems.append("result.checks.xml_valid is true but the SVG did not parse")
    if claimed.get("text_as_text") is True and not measured.get("svg_text_elements"):
        problems.append("result.checks.text_as_text is true but the SVG has no <text>")
    for key, actual in (("named_groups", measured.get("svg_named_groups")),
                        ("external_refs", measured.get("svg_external_refs"))):
        if isinstance(claimed.get(key), int) and actual is not None \
                and claimed[key] != actual:
            warnings.append("result.checks.%s=%s but measured %s"
                            % (key, claimed[key], actual))


def self_test(problems, warnings, checks):
    entities = {"entities": [{"id": "E1", "label": "Catalyst A", "type": "component",
                              "source": "m.md §1"}],
                "relations": []}
    result = {"case_id": "schematic-self-test",
              "task_family": "schematic",
              "workorder": "editable_relationship_schematic",
              "status": "partial",
              "figure": {"nodes": 1, "edges": 0},
              "artifacts": [{"role": "editable", "path": "fig.svg", "format": "svg"},
                            {"role": "preview", "path": "fig.png", "format": "png"}],
              "element_source_map": [{"element_id": "E1", "maps_to": "m.md §1"}],
              "content_map": [{"element_id": "E1", "maps_to": "m.md §1"}],
              "checks": {"xml_valid": True, "text_as_text": True, "named_groups": 1},
              "compliance_checks": {"xml_valid": True, "text_as_text": True},
              "editable_source": {"path": "fig.svg", "format": "svg"},
              "export_artifact": None,
              "exceptions": [{"code": "export_unavailable", "detail": "self-test"}],
              "edit": {"mode": "create", "diff": [], "preserved": []}}
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "fig.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
            '<g id="E1"><rect x="10" y="10" width="40" height="20"/>'
            '<text x="30" y="24">A</text></g></svg>', encoding="utf-8")
        (root / "fig.png").write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 +
            (10).to_bytes(4, "big") + (10).to_bytes(4, "big") + b"\x00" * 16)
        entity_ids, relation_ids = check_entities(entities, problems, warnings, checks)
        check_result(result, root, problems, warnings, checks)
        items, parsed = check_svg(root / "fig.svg", problems, warnings, checks)
        check_coverage(entity_ids, relation_ids, items, problems, warnings, checks)
        honesty_checks(result, {"svg_parsed": parsed,
                                "svg_text_elements": checks.get("svg_text_elements"),
                                "svg_named_groups": checks.get("svg_named_groups"),
                                "svg_external_refs": checks.get("svg_external_refs")},
                       problems, warnings)
    checks["self_test"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", default=None)
    parser.add_argument("--entities", default=None)
    parser.add_argument("--svg", default=None)
    parser.add_argument("--before", default=None)
    parser.add_argument("--workorder", default=None)
    parser.add_argument("--root", default=None)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    problems, warnings, checks = [], [], {}
    if args.self_test:
        self_test(problems, warnings, checks)
    else:
        if not args.result:
            parser.error("--result is required unless --self-test is used")
        try:
            result = load_json(args.result)
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error_code": "result_unreadable",
                              "problems": ["cannot read result: %s" % exc]},
                             ensure_ascii=False))
            return 2
        base = Path(args.root) if args.root else Path(args.result).parent
        entity_ids, relation_ids = set(), set()
        if args.entities:
            if not Path(args.entities).is_file():
                problems.append("missing entities file: %s" % args.entities)
            else:
                try:
                    entity_ids, relation_ids = check_entities(
                        load_json(args.entities), problems, warnings, checks)
                except (OSError, json.JSONDecodeError) as exc:
                    problems.append("cannot read entities: %s" % exc)
        else:
            warnings.append("--entities not provided; entity coverage not checked")
        check_result(result, base, problems, warnings, checks)

        items, parsed = {}, False
        if args.svg:
            items, parsed = check_svg(args.svg, problems, warnings, checks)
        else:
            warnings.append("--svg not provided; SVG structure not checked")
        check_coverage(entity_ids, relation_ids, items, problems, warnings, checks)

        before = None
        if args.before:
            before_path = Path(args.before)
            if not before_path.is_file():
                problems.append("missing before SVG: %s" % args.before)
            else:
                try:
                    before = svg_inventory(
                        before_path.read_text(encoding="utf-8", errors="replace"))
                except (OSError, ET.ParseError) as exc:
                    problems.append("cannot read before SVG: %s" % exc)
        else:
            warnings.append("--before not provided; edit scope not compared")
        workorder = None
        workorder_given = False
        if args.workorder:
            workorder_given = True
            if not Path(args.workorder).is_file():
                problems.append("missing workorder file: %s" % args.workorder)
            else:
                try:
                    workorder = load_json(args.workorder)
                except (OSError, json.JSONDecodeError) as exc:
                    problems.append("cannot read workorder: %s" % exc)
        after = items if items else None
        if before is not None and after is not None:
            if not workorder_given:
                warnings.append("before SVG given without a workorder; scope cannot be "
                                "verified and diff will be reported only")
            check_edit_scope(before, after, workorder, problems, warnings, checks)
        elif before is not None or after is not None:
            if before is None or after is None:
                warnings.append("edit scope not compared: both --before and --svg "
                                "(with named groups) are required")

        honesty_checks(result, {"svg_parsed": parsed,
                                "svg_text_elements": checks.get("svg_text_elements"),
                                "svg_named_groups": checks.get("svg_named_groups"),
                                "svg_external_refs": checks.get("svg_external_refs")},
                       problems, warnings)

    report = {"ok": not problems, "problems": problems, "warnings": warnings,
              "checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
