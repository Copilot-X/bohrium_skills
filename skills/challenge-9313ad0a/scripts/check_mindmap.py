#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a mind-map outline, render spec, SVG and result.json.

Checks structure (single root / parents / cycles), node typing and sources,
edge endpoints and relation words, SVG editability (real <text>, viewBox,
no scripts or external references), text overflow, and the official
deliverable / preserved_facts / evidence_map / change_log fields.

Usage:
    python3 check_mindmap.py --material /app/work/material.json \
        --outline /app/work/mindmap.json --spec /app/work/mindmap-spec.json \
        --svg /app/output/mindmap.svg --result /app/output/result.json \
        --root /app/output --json
    python3 check_mindmap.py --outline /app/work/mindmap.json --self-test --json

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable input.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from importlib.machinery import SourceFileLoader
from pathlib import Path


def _load_kit():
    try:
        import mindmap_kit  # type: ignore
        return mindmap_kit
    except ImportError:
        path = Path(__file__).resolve().with_name("mindmap_kit.py")
        if not path.is_file():
            path = path.with_name("mindmap_kit.py.md")
        spec = importlib.util.spec_from_loader(
            "mindmap_kit", SourceFileLoader("mindmap_kit", str(path)))
        module = importlib.util.module_from_spec(spec)
        sys.modules["mindmap_kit"] = module
        spec.loader.exec_module(module)
        return module


kit = _load_kit()

SVG_NS = "http://www.w3.org/2000/svg"
EXTERNAL_MARKERS = ('href="http', "href='http", 'href="https', "href='https",
                    "file://", "url(", "@import", "xlink:href",
                    "<image", "<script", "<iframe")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def check_outline(outline, problems, warnings, checks, max_nodes=40):
    nodes = outline.get("nodes") or []
    if not isinstance(nodes, list) or not nodes:
        problems.append("outline has no nodes[]")
        return set()
    by_id = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            problems.append("node[%d] is not an object" % index)
            continue
        nid = str(node.get("node_id") or node.get("id") or "")
        if not nid:
            problems.append("node[%d] missing node_id" % index)
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", nid):
            warnings.append("node id has unusual characters: %s" % nid)
        if nid in by_id:
            problems.append("duplicate node_id: %s" % nid)
            continue
        by_id[nid] = node
    roots = [nid for nid, node in by_id.items() if not node.get("parent")]
    if len(roots) != 1:
        problems.append("expected exactly one root, found %d" % len(roots))
    for nid, node in by_id.items():
        parent = node.get("parent")
        if parent and str(parent) not in by_id:
            problems.append("node %s references missing parent %s" % (nid, parent))
        label = str(node.get("label") or "").strip()
        if not label:
            problems.append("node %s missing label" % nid)
        elif len(label) > 60:
            problems.append("node %s label longer than 60 chars" % nid)
        elif len(label) > 20 and not re.search(r"[A-Za-z]{12}", label):
            warnings.append("node %s label longer than 20 chars" % nid)
        node_type = str(node.get("type") or "")
        if node_type not in kit.NODE_TYPES:
            warnings.append("node %s has unknown type %s" % (nid, node_type))
        kind = str(node.get("kind") or "")
        if kind not in kit.NODE_KINDS:
            problems.append("node %s has invalid kind: %r" % (nid, kind))
        if not kit.source_ok(node.get("source")) and kind not in ("task", "inferred"):
            problems.append("node %s (%s) has no usable source and is not "
                            "task/inferred" % (nid, node_type))
        if kind == "inferred" and not str(node.get("detail") or "").strip():
            problems.append("inferred node %s has no explanation in detail" % nid)
        if kind == "task" and not kit.source_is_task(node.get("source")):
            warnings.append("node %s is kind=task but source is not task" % nid)
    # Parent cycles: walk to root with a bounded step count.
    for start in by_id:
        seen, current = set(), start
        steps = 0
        while current and current in by_id and steps <= len(by_id) + 1:
            if current in seen:
                problems.append("parent cycle involving node %s" % start)
                break
            seen.add(current)
            parent = by_id[current].get("parent")
            current = str(parent) if parent else ""
            steps += 1
    depths = {}
    for nid in by_id:
        depth, current, seen = 0, nid, set()
        while current and current in by_id and current not in seen:
            seen.add(current)
            parent = by_id[current].get("parent")
            current = str(parent) if parent else ""
            depth += 1
        depths[nid] = depth - 1 if depth else 0
    inferred = sum(1 for node in by_id.values() if node.get("kind") == "inferred")
    if by_id and inferred / len(by_id) > 0.2:
        warnings.append("inferred nodes exceed 20%% (%d/%d)" % (inferred, len(by_id)))
    if len(by_id) > max_nodes:
        warnings.append("node count %d exceeds budget %d" % (len(by_id), max_nodes))
    root_id = roots[0] if len(roots) == 1 else (nodes[0].get("node_id")
                                                if nodes else None)
    branch_count = sum(1 for nid, node in by_id.items()
                       if str(node.get("parent") or "") == str(root_id))
    if by_id and not (kit.MIN_BRANCHES <= branch_count <= kit.MAX_BRANCHES):
        warnings.append("first-level branches = %d; expected %d-%d"
                        % (branch_count, kit.MIN_BRANCHES, kit.MAX_BRANCHES))
    max_depth = max(depths.values()) if depths else 0
    if max_depth > 6:
        problems.append("tree depth %d exceeds hard limit 6" % max_depth)
    elif max_depth > kit.MAX_DEPTH_DEFAULT:
        warnings.append("tree depth %d exceeds recommended %d"
                        % (max_depth, kit.MAX_DEPTH_DEFAULT))
    sibling_labels = {}
    for nid, node in by_id.items():
        parent = str(node.get("parent") or "")
        key = (parent, kit.normalize_for_match(node.get("label") or ""))
        if key in sibling_labels:
            warnings.append("duplicate sibling labels: %s / %s"
                            % (sibling_labels[key], nid))
        sibling_labels[key] = nid
    cross = outline.get("cross_links") or []
    seen_links = set()
    for index, link in enumerate(cross):
        if not isinstance(link, dict):
            problems.append("cross_link[%d] is not an object" % index)
            continue
        src, dst = str(link.get("from") or ""), str(link.get("to") or "")
        if src not in by_id:
            problems.append("cross_link[%d] missing from: %s" % (index, src))
        if dst not in by_id:
            problems.append("cross_link[%d] missing to: %s" % (index, dst))
        if src and src == dst:
            problems.append("cross_link[%d] is a self loop" % index)
        relation = str(link.get("relation") or "")
        if relation and relation not in kit.RELATIONS:
            warnings.append("cross_link[%d] unknown relation %s" % (index, relation))
        if not relation:
            warnings.append("cross_link[%d] has no relation word" % index)
        if not kit.source_ok(link.get("source")) and link.get("kind") != "inferred":
            warnings.append("cross_link[%d] has no source and is not inferred" % index)
        key = (src, dst, relation)
        if key in seen_links:
            warnings.append("duplicate cross_link %s -> %s (%s)" % (src, dst, relation))
        seen_links.add(key)
    checks.update({"nodes": len(by_id), "root": root_id,
                   "branches": branch_count, "depth": max_depth,
                   "inferred": inferred, "cross_links": len(cross),
                   "max_nodes": max_nodes})
    return set(by_id)


def check_spec(spec, node_ids, problems, warnings, checks):
    if not isinstance(spec, dict):
        problems.append("spec root is not an object")
        return
    nodes = spec.get("nodes") or []
    edges = spec.get("edges") or []
    spec_ids = set()
    for index, node in enumerate(nodes):
        nid = str(node.get("id") or "")
        if not nid:
            problems.append("spec node[%d] missing id" % index)
            continue
        spec_ids.add(nid)
        if not node.get("label"):
            problems.append("spec node %s missing label" % nid)
        if not kit.source_ok(node.get("source")) and node.get("kind") != "inferred":
            warnings.append("spec node %s has no usable source" % nid)
        if node.get("rank", 0) > 6:
            problems.append("spec node %s rank > 6" % nid)
        for key in ("x", "y", "width", "height"):
            if not isinstance(node.get(key), (int, float)):
                problems.append("spec node %s missing numeric %s" % (nid, key))
        if node.get("width", 0) <= 0 or node.get("height", 0) <= 0:
            problems.append("spec node %s has non-positive box" % nid)
    missing = sorted(node_ids - spec_ids)
    extra = sorted(spec_ids - node_ids)
    if missing:
        problems.append("outline nodes missing from spec: %s" % ", ".join(missing[:8]))
    if extra:
        warnings.append("spec has extra nodes: %s" % ", ".join(extra[:8]))
    edge_ids, hierarchy = set(), 0
    for index, edge in enumerate(edges):
        eid = str(edge.get("id") or "e%d" % index)
        if eid in edge_ids:
            warnings.append("duplicate spec edge id: %s" % eid)
        edge_ids.add(eid)
        src, dst = str(edge.get("from") or ""), str(edge.get("to") or "")
        if src not in spec_ids or dst not in spec_ids:
            problems.append("spec edge[%d] missing endpoint: %s -> %s"
                            % (index, src, dst))
        if edge.get("kind") == "hierarchy":
            hierarchy += 1
        if not kit.source_ok(edge.get("source")) and not edge.get("inferred"):
            warnings.append("spec edge %s has no source and is not inferred" % eid)
        if not str(edge.get("d") or "").startswith("M"):
            problems.append("spec edge %s missing path geometry" % eid)
    if spec_ids and hierarchy < len(spec_ids) - 1:
        problems.append("spec has %d hierarchy edges for %d nodes"
                        % (hierarchy, len(spec_ids)))
    checks.update({"spec_nodes": len(nodes), "spec_edges": len(edges),
                   "spec_hierarchy_edges": hierarchy})


def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _estimate_text_width(text, size):
    return sum((0.55 if ord(char) < 128 else 1.0) * size for char in text)


def check_svg(path, spec, node_ids, problems, warnings, checks):
    p = Path(path)
    if not p.is_file():
        problems.append("missing SVG: %s" % p)
        return
    raw = p.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        problems.append("SVG is empty")
        return
    lowered = raw.lower()
    for marker in EXTERNAL_MARKERS:
        if marker in lowered:
            problems.append("SVG contains external/unsafe marker: %s" % marker)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        problems.append("SVG not well-formed: %s" % exc)
        return
    if _local(root.tag) != "svg":
        problems.append("SVG root is not <svg>")
    viewbox = root.attrib.get("viewBox", "")
    values = viewbox.replace(",", " ").split()
    if len(values) != 4:
        problems.append("SVG missing/invalid viewBox")
    else:
        try:
            if any(float(value) <= 0 for value in values[2:]):
                problems.append("SVG viewBox has non-positive size")
        except ValueError:
            problems.append("SVG viewBox is not numeric")
    elements = list(root.iter())
    texts = [element for element in elements if _local(element.tag) == "text"]
    if len(texts) < len(node_ids):
        problems.append("SVG has %d <text> elements for %d nodes"
                        % (len(texts), len(node_ids)))
    for element in elements:
        if _local(element.tag) in ("script", "image", "iframe"):
            problems.append("SVG contains <%s>" % _local(element.tag))
    node_groups = {}
    for element in elements:
        if _local(element.tag) != "g":
            continue
        nid = element.attrib.get("data-node-id")
        if nid:
            if nid in node_groups:
                problems.append("duplicate SVG node group: %s" % nid)
            node_groups[nid] = element
    svg_ids = set(node_groups)
    missing = sorted(node_ids - svg_ids)
    if missing:
        problems.append("SVG missing node groups: %s" % ", ".join(missing[:8]))
    if not node_ids:
        warnings.append("no outline node ids supplied for SVG coverage check")
    empty_text, small_font, overflow = 0, 0, 0
    for nid, group in node_groups.items():
        source = group.attrib.get("data-source", "").strip()
        kind = group.attrib.get("data-kind", "")
        if not source and kind != "inferred":
            problems.append("SVG node %s has empty data-source" % nid)
        if group.attrib.get("data-type") not in kit.NODE_TYPES:
            warnings.append("SVG node %s has unknown data-type" % nid)
        if not group.findall(".//{%s}title" % SVG_NS) and not group.findall("title"):
            warnings.append("SVG node %s lacks <title>" % nid)
        node_texts = [el for el in group.iter() if _local(el.tag) == "text"]
        if not node_texts:
            problems.append("SVG node %s has no <text>" % nid)
            continue
        content = "".join("".join(el.itertext()) for el in node_texts).strip()
        if not content:
            empty_text += 1
            problems.append("SVG node %s has empty text" % nid)
        rect = None
        for element in group.iter():
            if _local(element.tag) == "rect":
                rect = element
                break
        for element in node_texts:
            size = element.attrib.get("font-size")
            if size:
                try:
                    if float(size) < 10:
                        small_font += 1
                except ValueError:
                    pass
            spans = [span for span in element.iter() if _local(span.tag) == "tspan"]
            line_texts = ["".join(span.itertext()) for span in spans] or \
                         ["".join(element.itertext())]
            if rect is not None:
                try:
                    box_width = float(rect.attrib.get("width", "0"))
                except ValueError:
                    box_width = 0
                for line in line_texts:
                    if _estimate_text_width(line, 13) > box_width - 4:
                        overflow += 1
    if small_font:
        warnings.append("%d SVG text element(s) have font-size < 10" % small_font)
    if overflow:
        warnings.append("%d SVG text line(s) may overflow their node box" % overflow)
    edge_paths = [element for element in elements
                  if _local(element.tag) == "path"
                  and element.attrib.get("data-edge-id")]
    if spec and spec.get("edges"):
        spec_edges = {str(edge.get("id")) for edge in spec["edges"]}
        svg_edges = {element.attrib.get("data-edge-id") for element in edge_paths}
        if spec_edges - svg_edges:
            problems.append("SVG missing edge paths: %s"
                            % ", ".join(sorted(spec_edges - svg_edges)[:8]))
        for element in edge_paths:
            if not element.attrib.get("data-source", "").strip() \
                    and element.attrib.get("data-inferred") != "true":
                warnings.append("SVG edge %s has no data-source"
                                % element.attrib.get("data-edge-id"))
    checks.update({"svg_text_elements": len(texts),
                   "svg_node_groups": len(node_groups),
                   "svg_edge_paths": len(edge_paths),
                   "svg_bytes": len(raw.encode("utf-8"))})


def check_material(material, outline, problems, warnings, checks):
    if not isinstance(material, dict):
        return
    if material.get("case_id") and outline.get("case_id") \
            and material["case_id"] != outline.get("case_id"):
        problems.append("material/outline case_id mismatch")
    nodes = material.get("nodes") or []
    if nodes and len(nodes) != len(outline.get("nodes") or []):
        warnings.append("material node count differs from outline")
    facts = material.get("preserved_facts") or []
    for index, fact in enumerate(facts[:20]):
        if not kit.source_ok(fact.get("source")):
            warnings.append("preserved_fact[%d] lacks source" % index)
    evidence = material.get("evidence_map") or []
    for index, entry in enumerate(evidence[:40]):
        source = entry.get("source") or {}
        if not kit.source_ok(source):
            warnings.append("evidence_map[%d] lacks source" % index)
        if not (source.get("quote") or entry.get("quote")):
            warnings.append("evidence_map[%d] lacks quote" % index)
    checks.update({"material_facts": len(facts), "material_evidence": len(evidence)})


def check_result(result_path, root, node_ids, outline, problems, warnings, checks):
    if not result_path:
        warnings.append("no result.json given; skipped result checks")
        return
    p = Path(result_path)
    if not p.is_file():
        problems.append("missing result.json: %s" % p)
        return
    try:
        result = load_json(p)
    except (OSError, json.JSONDecodeError) as exc:
        problems.append("cannot parse result.json: %s" % exc)
        return
    required = ("case_id", "mindmap", "artifacts", "nodes", "cross_links",
                "coverage", "deliverable", "preserved_facts",
                "evidence_map", "change_log")
    for key in required:
        if key not in result:
            problems.append("result.json missing %s" % key)
    if outline.get("case_id") and result.get("case_id") != outline.get("case_id"):
        problems.append("result.case_id does not preserve input case_id")
    if result.get("status") not in ("completed", "partial"):
        warnings.append("result.status should be completed|partial")
    mindmap = result.get("mindmap") or {}
    if isinstance(mindmap, dict) and mindmap.get("nodes") not in (None, 0):
        if int(mindmap["nodes"]) != len(node_ids):
            problems.append("result.mindmap.nodes != outline node count")
    result_ids = {str(node.get("node_id")) for node in (result.get("nodes") or [])
                  if isinstance(node, dict)}
    if result_ids and result_ids != node_ids:
        missing = sorted(node_ids - result_ids)
        if missing:
            problems.append("result.nodes missing ids: %s" % ", ".join(missing[:8]))
    edges = result.get("edges") or []
    if node_ids and len(edges) < len(node_ids) - 1:
        warnings.append("result.edges has fewer than nodes-1 entries")
    base = Path(root) if root else p.parent
    roles = {}
    for index, item in enumerate(result.get("artifacts") or []):
        if not isinstance(item, dict) or not item.get("path"):
            problems.append("artifact[%d] missing path" % index)
            continue
        rel = str(item["path"])
        candidate = Path(rel)
        if not candidate.is_absolute():
            candidate = base / rel
        if not candidate.is_file():
            problems.append("artifact missing: %s" % rel)
            continue
        roles[str(item.get("role") or "").lower()] = candidate
    for role in ("editable", "structure"):
        if role not in roles:
            problems.append("result.artifacts missing role=%s" % role)
    deliverable = result.get("deliverable")
    if isinstance(deliverable, dict):
        rel = deliverable.get("path")
        if not rel:
            problems.append("result.deliverable missing path")
        else:
            candidate = Path(rel)
            if not candidate.is_absolute():
                candidate = base / rel
            if not candidate.is_file():
                problems.append("result.deliverable path missing: %s" % rel)
            if str(deliverable.get("format")).lower() not in ("svg", "json"):
                warnings.append("deliverable format is not svg/json")
    elif not isinstance(deliverable, str):
        problems.append("result.deliverable is not an object/string")
    facts = result.get("preserved_facts") or []
    if node_ids and len(node_ids) > 1 and not facts:
        problems.append("result.preserved_facts is empty")
    evidence = result.get("evidence_map") or []
    reported = [nid for nid in node_ids]
    if len(reported) > 1 and not evidence:
        problems.append("result.evidence_map is empty")
    for index, entry in enumerate(evidence[:60]):
        if not isinstance(entry, dict):
            problems.append("evidence_map[%d] is not an object" % index)
            continue
        if not (entry.get("claim") or entry.get("text")):
            warnings.append("evidence_map[%d] missing claim text" % index)
        if not kit.source_ok(entry.get("source")):
            problems.append("evidence_map[%d] missing usable source" % index)
        quote = (entry.get("source") or {}).get("quote") or entry.get("quote")
        if not quote:
            warnings.append("evidence_map[%d] missing verbatim quote" % index)
        claim_id = entry.get("claim_id")
        if claim_id and str(claim_id) not in node_ids:
            warnings.append("evidence_map[%d] unknown claim_id %s" % (index, claim_id))
    log = result.get("change_log") or []
    if not log:
        problems.append("result.change_log is empty")
    for index, entry in enumerate(log[:20]):
        if isinstance(entry, dict):
            if not entry.get("action"):
                warnings.append("change_log[%d] missing action" % index)
        elif not str(entry).strip():
            problems.append("change_log[%d] is empty" % index)
    if not isinstance(result.get("uncertainty"), list):
        warnings.append("result.uncertainty should be a list")
    checks.update({"result_status": result.get("status"),
                   "artifact_roles": sorted(roles),
                   "result_facts": len(facts),
                   "result_evidence": len(evidence),
                   "result_changes": len(log)})


def self_test(problems, warnings, checks, temp_dir):
    outline = {"case_id": "mindmap-self-test", "title": "Self test",
               "nodes": [
                   {"node_id": "n0", "parent": None, "label": "Root",
                    "type": "topic", "source": "task", "kind": "task"},
                   {"node_id": "b1", "parent": "n0", "label": "方法",
                    "type": "method", "source": "task", "kind": "task"},
                   {"node_id": "n1", "parent": "b1", "label": "固定床实验",
                    "type": "method", "kind": "reported",
                    "detail": "d", "source": {"path": "m.md", "locator": "p1"}},
               ], "cross_links": []}
    ids = check_outline(outline, problems, warnings, checks)
    spec = {"nodes": [
        {"id": "n0", "label": "Root", "x": 0, "y": 0, "width": 100,
         "height": 40, "rank": 0, "source": "task", "kind": "task"},
        {"id": "b1", "label": "方法", "x": 200, "y": 0, "width": 100,
         "height": 40, "rank": 1, "source": "task", "kind": "task"},
        {"id": "n1", "label": "固定床实验", "x": 400, "y": 0, "width": 100,
         "height": 40, "rank": 2, "source": "m.md p1", "kind": "reported"}],
        "edges": [
            {"id": "e-b1", "from": "n0", "to": "b1", "kind": "hierarchy",
             "source": "task", "d": "M 100 20 C 160 20, 180 20, 200 20"},
            {"id": "e-n1", "from": "b1", "to": "n1", "kind": "hierarchy",
             "source": "m.md p1", "d": "M 300 20 C 340 20, 360 20, 400 20"}]}
    check_spec(spec, ids, problems, warnings, checks)
    svg_path = Path(temp_dir) / "self-test.svg"
    svg_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 80">'
        '<path data-edge-id="e-b1" data-source="task" d="M100 20 L200 20"/>'
        '<path data-edge-id="e-n1" data-source="m.md" d="M300 20 L400 20"/>'
        '<g data-node-id="n0" data-type="topic" data-kind="task" data-source="task">'
        '<title>Root</title><rect x="0" y="0" width="100" height="40"/>'
        '<text x="4" y="20" font-size="13"><tspan>Root</tspan></text></g>'
        '<g data-node-id="b1" data-type="method" data-kind="task" data-source="task">'
        '<title>方法</title><rect x="200" y="0" width="100" height="40"/>'
        '<text x="204" y="20" font-size="13"><tspan>方法</tspan></text></g>'
        '<g data-node-id="n1" data-type="method" data-kind="reported" data-source="m.md p1">'
        '<title>固定床实验</title><rect x="400" y="0" width="100" height="40"/>'
        '<text x="404" y="20" font-size="13"><tspan>固定床实验</tspan></text></g>'
        '</svg>', encoding="utf-8")
    check_svg(svg_path, spec, ids, problems, warnings, checks)
    checks["self_test"] = True


def main():
    kit.configure_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--material", default=None)
    parser.add_argument("--outline", default=None)
    parser.add_argument("--spec", default=None)
    parser.add_argument("--svg", default=None)
    parser.add_argument("--result", default=None)
    parser.add_argument("--root", default=None)
    parser.add_argument("--max-nodes", type=int, default=40)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    problems, warnings, checks = [], [], {}
    if args.self_test:
        with tempfile.TemporaryDirectory(prefix="mindmap-self-") as temp_dir:
            self_test(problems, warnings, checks, temp_dir)
    else:
        if not args.outline:
            parser.error("--outline is required unless --self-test is used")
        try:
            outline = load_json(args.outline)
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "problems": [
                "cannot read outline: %s" % exc]}, ensure_ascii=False))
            return 2
        ids = check_outline(outline, problems, warnings, checks,
                            max_nodes=args.max_nodes)
        if args.material:
            try:
                check_material(load_json(args.material), outline, problems,
                               warnings, checks)
            except (OSError, json.JSONDecodeError) as exc:
                problems.append("cannot read material: %s" % exc)
        spec = None
        if args.spec:
            try:
                spec = load_json(args.spec)
                check_spec(spec, ids, problems, warnings, checks)
            except (OSError, json.JSONDecodeError) as exc:
                problems.append("cannot read spec: %s" % exc)
        if args.svg:
            check_svg(args.svg, spec, ids, problems, warnings, checks)
        if args.result:
            check_result(args.result, args.root, ids, outline, problems,
                         warnings, checks)
    if args.strict:
        problems.extend("strict: " + warning for warning in warnings)
        warnings = []
    report = {"ok": not problems, "problems": problems, "warnings": warnings,
              "checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
