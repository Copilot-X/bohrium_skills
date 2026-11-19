#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an editable mind map (JSON outline + SVG) from extracted material.

Input may be ``material.json`` from extract_material.py or a hand-authored
outline (``nodes[]`` + ``cross_links[]``).  Outputs:

  * mindmap.json      editable structure (nodes, parent links, cross-links)
  * mindmap-spec.json normalized render spec (layout, positions, sources)
  * mindmap.svg       editable vector map: real <text>, per-node <g>,
                      no scripts, no external references

Usage:
    python3 build_mindmap.py --input /app/work/material.json \
        --out-outline /app/work/mindmap.json \
        --out-spec /app/work/mindmap-spec.json \
        --out-svg /app/output/mindmap.svg --layout tree --json
    python3 build_mindmap.py --outline /app/work/mindmap.json \
        --out-spec /app/work/spec.json --out-svg /app/output/mindmap.svg

Stdlib only. Exit 0 = built, 1 = built with warnings, 2 = unreadable input.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
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

FONT = "Arial, 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif"
FONT_SIZE = 13
LINE_H = 18
PAD_X = 12
PAD_Y = 9
MARGIN = 30
COL_GAP = 64
ROW_GAP = 14
COLORS = ("#E8F1FB", "#EAF5E8", "#FFF4E0", "#F3EAF8",
          "#E8F5F5", "#F5EDE8", "#EDEDED")
STROKES = ("#4A78A8", "#4F8A52", "#B8862D", "#7A5B9E",
           "#2F8585", "#A56B47", "#777777")
TYPE_SHAPE = {
    "topic": "rounded", "concept": "rounded", "question": "rounded",
    "method": "rounded", "evidence": "rounded", "result": "rounded",
    "claim": "rounded", "risk": "rounded", "gap": "rounded", "task": "rounded",
}


def normalize_outline(data, warnings):
    """Return an outline dict with stable ids, one root and source metadata."""
    if not isinstance(data, dict):
        return None, ["input is not a JSON object"]
    if isinstance(data.get("nodes"), list):
        outline = dict(data)
    else:
        outline = {"case_id": data.get("case_id") or "unknown-case",
                   "title": data.get("title") or "思维导图",
                   "nodes": data.get("nodes") or [],
                   "cross_links": data.get("cross_links") or [],
                   "coverage": data.get("coverage") or {},
                   "constraints": data.get("constraints") or {},
                   "structure_type": data.get("structure_type") or "left_right_tree"}
    raw_nodes = [node for node in (outline.get("nodes") or [])
                 if isinstance(node, dict)]
    if not raw_nodes:
        title = kit.clean_inline(outline.get("title") or "思维导图")[:30]
        raw_nodes = [{
            "node_id": "n0", "parent": None,
            "label": title or "材料不足",
            "type": "topic", "detail": "未提取到可读内容，仅保留任务根节点",
            "source": "task", "kind": "task", "importance": "core",
        }]
        warnings.append("no nodes in input; emitted a task-sourced root only")
    nodes, by_id = [], {}
    for index, node in enumerate(raw_nodes):
        nid = str(node.get("node_id") or node.get("id") or "n%d" % index)
        if nid in by_id:
            nid = "%s-%d" % (nid, index)
            warnings.append("duplicate node id renamed to %s" % nid)
        item = dict(node)
        item["node_id"] = nid
        item["label"] = kit.clean_inline(item.get("label") or nid)[:60]
        item["detail"] = kit.clean_inline(item.get("detail") or "")[:300]
        item["type"] = str(item.get("type") or
                          ("topic" if index == 0 else "concept"))
        source = kit.source_obj(item.get("source"))
        kind = str(item.get("kind") or "").strip()
        if not source.get("path") and not source.get("locator"):
            if kit.source_is_task(item.get("source")):
                source = kit.source_obj("task")
            else:
                kind = "inferred"
                source = {"path": "", "locator": "inferred: source not provided"}
                item["detail"] = item["detail"] or "来源缺失，已标注为推断"
                warnings.append("node %s has no source; marked inferred" % nid)
        if not kind:
            kind = "task" if kit.source_is_task(source) else "reported"
        item["source"] = source
        item["kind"] = kind if kind in kit.NODE_KINDS else "reported"
        item["importance"] = str(item.get("importance") or "supporting")
        nodes.append(item)
        by_id[nid] = item

    roots = [nid for nid, node in by_id.items() if not node.get("parent")]
    if not roots:
        roots = [nodes[0]["node_id"]]
        by_id[roots[0]]["parent"] = None
        warnings.append("no root found; promoted %s" % roots[0])
    root_id = roots[0]
    if len(roots) > 1:
        warnings.append("multiple roots; attached %d extra root(s) to %s"
                        % (len(roots) - 1, root_id))
        for extra in roots[1:]:
            by_id[extra]["parent"] = root_id
    # Re-parent nodes whose parent is missing.
    for node in nodes:
        parent = node.get("parent")
        if parent and str(parent) not in by_id:
            warnings.append("node %s parent %s missing; attached to root"
                            % (node["node_id"], parent))
            node["parent"] = root_id
    # Cycle guard: break by attaching offending node to root.
    for node in nodes:
        seen, current = set(), node["node_id"]
        while current and current in by_id:
            if current in seen:
                if node["parent"] != root_id:
                    node["parent"] = root_id
                    warnings.append("cycle broken at node %s" % node["node_id"])
                break
            seen.add(current)
            parent = by_id[current].get("parent")
            current = str(parent) if parent else ""
    outline["nodes"] = nodes
    outline["root_id"] = root_id
    links = []
    for index, link in enumerate(outline.get("cross_links") or []):
        if not isinstance(link, dict):
            warnings.append("cross_link[%d] is not an object" % index)
            continue
        src, dst = str(link.get("from") or ""), str(link.get("to") or "")
        if src not in by_id or dst not in by_id:
            warnings.append("cross_link[%d] endpoint missing: %s -> %s"
                            % (index, src, dst))
            continue
        if src == dst:
            warnings.append("cross_link[%d] is a self loop; skipped" % index)
            continue
        source = kit.source_obj(link.get("source"))
        kind = str(link.get("kind") or "")
        if not source.get("path") and not source.get("locator"):
            kind = "inferred"
            source = {"path": "", "locator": "inferred: relation source not provided"}
        links.append({"from": src, "to": dst,
                      "relation": str(link.get("relation") or "link"),
                      "kind": kind or "reported", "source": source})
    outline["cross_links"] = links
    if not outline.get("case_id"):
        outline["case_id"] = "unknown-case"
    if not outline.get("title"):
        outline["title"] = nodes[0]["label"] or "思维导图"
    return outline, warnings


def compute_depths(outline):
    by_id = {node["node_id"]: node for node in outline["nodes"]}
    root_id = outline["root_id"]
    depth, branch = {}, {}

    def walk(nid, level, branch_id):
        if nid in depth:
            return
        depth[nid] = level
        branch[nid] = branch_id
        for node in outline["nodes"]:
            if str(node.get("parent") or "") == nid:
                walk(node["node_id"], level + 1,
                     node["node_id"] if level == 0 else branch_id)

    walk(root_id, 0, root_id)
    for node in outline["nodes"]:
        nid = node["node_id"]
        if nid not in depth:
            parent = str(node.get("parent") or "")
            depth[nid] = depth.get(parent, 0) + 1
            branch[nid] = branch.get(parent, nid)
    return depth, branch


def text_width(text: str, size: float = FONT_SIZE) -> float:
    return sum((0.55 if ord(char) < 128 else 1.0) * size for char in text)


def node_box(node, depth):
    max_chars = 13 if depth == 0 else 15
    lines = kit.wrap_label(node.get("label") or node["node_id"], max_chars, 3)
    width = max(112.0, min(214.0, max(text_width(line) for line in lines) + 2 * PAD_X))
    height = max(36.0, PAD_Y * 2 + LINE_H * len(lines))
    node["_lines"] = lines
    node["_w"] = round(width, 1)
    node["_h"] = round(height, 1)
    node["_depth"] = depth
    return width, height


def layout_tree(outline):
    depth, branch = compute_depths(outline)
    by_id = {node["node_id"]: node for node in outline["nodes"]}
    children = {nid: [] for nid in by_id}
    roots = []
    for node in outline["nodes"]:
        parent = str(node.get("parent") or "")
        if parent and parent in children:
            children[parent].append(node["node_id"])
        else:
            roots.append(node["node_id"])
    root_id = outline["root_id"]
    for nid in by_id:
        node_box(by_id[nid], depth.get(nid, 0))
    max_by_depth = {}
    for nid, node in by_id.items():
        max_by_depth[node["_depth"]] = max(max_by_depth.get(node["_depth"], 0),
                                           node["_w"])
    columns, x = {}, MARGIN
    for level in sorted(max_by_depth):
        columns[level] = x
        x += max_by_depth[level] + COL_GAP
    y_cursor = {"value": MARGIN}

    def place(nid):
        node = by_id[nid]
        kids = children.get(nid, [])
        if kids:
            positions = [place(child) for child in kids]
            top = positions[0][0]
            bottom = positions[-1][1]
            node["_y"] = round((top + bottom) / 2 - node["_h"] / 2, 1)
        else:
            node["_y"] = y_cursor["value"]
            y_cursor["value"] += node["_h"] + ROW_GAP
        node["_x"] = round(columns[node["_depth"]], 1)
        return node["_y"], node["_y"] + node["_h"]

    place(root_id)
    for nid in by_id:
        if "_y" not in by_id[nid]:
            node = by_id[nid]
            node["_x"] = round(columns.get(node["_depth"], MARGIN), 1)
            node["_y"] = round(y_cursor["value"], 1)
            y_cursor["value"] += node["_h"] + ROW_GAP
    min_y = min(node["_y"] for node in by_id.values())
    for node in by_id.values():
        node["_y"] = round(node["_y"] - min_y + MARGIN, 1)
    width = max(node["_x"] + node["_w"] for node in by_id.values()) + MARGIN
    height = max(node["_y"] + node["_h"] for node in by_id.values()) + MARGIN + 48
    return {"layout": "tree", "width": round(width), "height": round(height),
            "depth": depth, "branch": branch, "children": children}


def layout_radial(outline):
    depth, branch = compute_depths(outline)
    by_id = {node["node_id"]: node for node in outline["nodes"]}
    children = {nid: [] for nid in by_id}
    for node in outline["nodes"]:
        parent = str(node.get("parent") or "")
        if parent in children:
            children[parent].append(node["node_id"])
    root_id = outline["root_id"]
    for nid, node in by_id.items():
        node_box(node, depth.get(nid, 0))
    leaves = [nid for nid in by_id if not children.get(nid)]
    order = {nid: index for index, nid in enumerate(
        [node["node_id"] for node in outline["nodes"]])}
    leaves.sort(key=lambda nid: order.get(nid, 0))
    angle = {}
    for index, nid in enumerate(leaves):
        angle[nid] = (2 * math.pi * index) / max(1, len(leaves)) - math.pi / 2

    def mean_angle(nid):
        kids = children.get(nid, [])
        if not kids:
            return angle.get(nid, 0.0)
        values = [mean_angle(child) for child in kids]
        return math.atan2(sum(math.sin(value) for value in values),
                          sum(math.cos(value) for value in values))

    max_depth = max(depth.values()) if depth else 0
    max_w = max(node["_w"] for node in by_id.values())
    max_h = max(node["_h"] for node in by_id.values())
    radius = {level: (0 if level == 0 else 150 + (level - 1) * 165)
              for level in range(max_depth + 1)}
    extent = radius[max_depth] + max(max_w, max_h) / 2 + 40
    center_x = center_y = extent + MARGIN
    for nid, node in by_id.items():
        level = node["_depth"]
        theta = mean_angle(nid) if level else 0.0
        node["_x"] = round(center_x + math.cos(theta) * radius[level] - node["_w"] / 2, 1)
        node["_y"] = round(center_y + math.sin(theta) * radius[level] - node["_h"] / 2, 1)
    width = height = round(extent * 2 + MARGIN * 2)
    return {"layout": "radial", "width": width, "height": height,
            "depth": depth, "branch": branch, "children": children,
            "center": (center_x, center_y)}


def edge_path(src, dst, layout):
    sx = src["_x"] + src["_w"]
    sy = src["_y"] + src["_h"] / 2
    tx = dst["_x"]
    ty = dst["_y"] + dst["_h"] / 2
    if layout == "radial":
        sx = src["_x"] + src["_w"] / 2
        sy = src["_y"] + src["_h"] / 2
        tx = dst["_x"] + dst["_w"] / 2
        ty = dst["_y"] + dst["_h"] / 2
    if tx >= sx:
        dx = max(36.0, (tx - sx) * 0.45)
        return "M %.1f %.1f C %.1f %.1f, %.1f %.1f, %.1f %.1f" % (
            sx, sy, sx + dx, sy, tx - dx, ty, tx, ty)
    dx = max(42.0, abs(tx - sx) * 0.35)
    return "M %.1f %.1f C %.1f %.1f, %.1f %.1f, %.1f %.1f" % (
        sx, sy, sx + dx, sy, tx - dx, ty, tx, ty)


def build_spec(outline, layout_name, warnings):
    depth, branch = compute_depths(outline)
    by_id = {node["node_id"]: node for node in outline["nodes"]}
    if layout_name == "radial":
        geometry = layout_radial(outline)
    else:
        geometry = layout_tree(outline)
    outline["structure_type"] = ("central_radial" if layout_name == "radial"
                                 else "left_right_tree")
    outline["layout"] = layout_name
    branch_index = {}
    for node in outline["nodes"]:
        key = branch.get(node["node_id"], node["node_id"])
        if key not in branch_index:
            branch_index[key] = len(branch_index)
    spec_nodes, svg_nodes = [], []
    for order, node in enumerate(outline["nodes"]):
        nid = node["node_id"]
        idx = branch_index.get(branch.get(nid, nid), 0) % len(COLORS)
        fill = "#FFFFFF" if depth.get(nid, 0) == 0 else COLORS[idx]
        stroke = "#333333" if depth.get(nid, 0) == 0 else STROKES[idx]
        source = kit.source_obj(node.get("source"))
        source_label = kit.source_text(source) or (
            "task" if node.get("kind") == "task" else "inferred")
        spec_node = {
            "id": nid, "label": node["label"], "sublabel": node.get("detail", "")[:120],
            "x": node["_x"], "y": node["_y"], "width": node["_w"],
            "height": node["_h"], "rank": depth.get(nid, 0),
            "order": order, "shape": TYPE_SHAPE.get(node.get("type"), "rounded"),
            "fill": fill, "stroke": stroke, "text_color": "#1F2328",
            "max_text_px": 190, "source": source_label,
            "node_type": node.get("type", "concept"),
            "kind": node.get("kind", "reported"),
            "importance": node.get("importance", "supporting"),
            "dashed": node.get("kind") == "inferred",
            "lines": node.get("_lines", [node["label"]]),
            "branch": branch.get(nid, nid),
        }
        spec_nodes.append(spec_node)
        svg_nodes.append(spec_node)

    edges = []
    for node in outline["nodes"]:
        parent = str(node.get("parent") or "")
        if parent and parent in by_id:
            edges.append({
                "id": "e-%s" % node["node_id"], "from": parent,
                "to": node["node_id"], "kind": "hierarchy",
                "relation": "has_child", "label": "",
                "source": kit.source_text(node.get("source")) or "inferred",
                "d": edge_path(by_id[parent], node, layout_name),
            })
    for index, link in enumerate(outline.get("cross_links") or []):
        source = kit.source_obj(link.get("source"))
        edges.append({
            "id": "x-%d" % (index + 1), "from": link["from"], "to": link["to"],
            "kind": "cross", "relation": link.get("relation") or "link",
            "label": link.get("relation") or "link",
            "source": kit.source_text(source) or "inferred",
            "inferred": link.get("kind") == "inferred" or not kit.source_ok(source),
            "d": edge_path(by_id[link["from"]], by_id[link["to"]], layout_name),
        })
    spec = {
        "figure_id": outline.get("case_id") or "mindmap",
        "title": outline.get("title") or "思维导图",
        "width": geometry["width"], "height": geometry["height"],
        "direction": "LR", "layout": layout_name,
        "font_family": FONT, "font_size_px": FONT_SIZE,
        "nodes": spec_nodes, "edges": edges,
        "legend": [{"kind": "hierarchy", "label": "实线：层级"},
                   {"kind": "cross", "label": "虚线：交叉关系/推断"}],
        "footnote": "每个节点带 data-source；完整证据见 result.json 的 evidence_map。",
    }
    return spec, geometry


def render_svg(spec, outline, warnings):
    width, height = spec["width"], spec["height"]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d"'
             ' width="%d" height="%d" role="img" aria-labelledby="mm-title mm-desc">'
             % (width, height, width, height),
             '  <title id="mm-title">%s</title>' % kit.xml_escape(spec["title"]),
             '  <desc id="mm-desc">Editable mind map generated from source material; '
             'node sources are recorded in data-source and result.json.</desc>',
             '  <metadata id="mindmap-meta">%s</metadata>'
             % kit.xml_escape(json.dumps({
                 "case_id": outline.get("case_id"),
                 "title": spec["title"], "layout": spec["layout"],
                 "nodes": len(spec["nodes"]), "edges": len(spec["edges"]),
                 "generator": "build_mindmap.py"} , ensure_ascii=False)),
             '  <rect data-shape="background" x="0" y="0" width="%d" height="%d"'
             ' fill="#FAFBFC"/>' % (width, height),
             '  <g id="edges" fill="none" stroke-linecap="round">']
    for edge in spec["edges"]:
        dash = ' stroke-dasharray="7 5"' if edge["kind"] == "cross" or edge.get("inferred") else ""
        stroke = "#8A94A0" if edge["kind"] == "hierarchy" else "#B06A6A"
        lines.append(
            '    <path id="%s" data-edge-id="%s" data-from="%s" data-to="%s"'
            ' data-kind="%s" data-relation="%s" data-source="%s"%s'
            ' d="%s" stroke="%s" stroke-width="1.6"/>'
            % (kit.xml_escape(edge["id"]), kit.xml_escape(edge["id"]),
               kit.xml_escape(edge["from"]), kit.xml_escape(edge["to"]),
               edge["kind"], kit.xml_escape(edge["relation"]),
               kit.xml_escape(edge["source"]),
               ' data-inferred="true"' if edge.get("inferred") else "", edge["d"],
               stroke))
    lines.append('  </g>')
    lines.append('  <g id="nodes">')
    for node in spec["nodes"]:
        source_label = node.get("source") or "inferred"
        dashed = ' stroke-dasharray="6 4"' if node.get("dashed") else ""
        lines.append(
            '    <g id="node-%s" data-node-id="%s" data-type="%s" data-kind="%s"'
            ' data-importance="%s" data-depth="%d" data-source="%s">'
            % (kit.xml_escape(node["id"]), kit.xml_escape(node["id"]),
               kit.xml_escape(node["node_type"]), kit.xml_escape(node["kind"]),
               kit.xml_escape(node.get("importance", "supporting")),
               node.get("rank", 0), kit.xml_escape(source_label)))
        lines.append('      <title>%s | source: %s</title>'
                     % (kit.xml_escape(node["label"]),
                        kit.xml_escape(source_label)))
        lines.append(
            '      <rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="8"'
            ' fill="%s" stroke="%s" stroke-width="%s"%s/>'
            % (node["x"], node["y"], node["width"], node["height"],
               node["fill"], node["stroke"],
               "2.2" if node.get("importance") == "core" else "1.3", dashed))
        text_y = node["y"] + PAD_Y + FONT_SIZE
        weight = ' font-weight="700"' if node.get("importance") == "core" else ""
        lines.append('      <text x="%.1f" y="%.1f" font-family="%s" font-size="%d"'
                     ' fill="%s"%s text-anchor="start">'
                     % (node["x"] + PAD_X, text_y, kit.xml_escape(FONT),
                        FONT_SIZE, node["text_color"], weight))
        for index, text in enumerate(node.get("lines") or [node["label"]]):
            dy = 0 if index == 0 else LINE_H
            lines.append('        <tspan x="%.1f" dy="%d">%s</tspan>'
                         % (node["x"] + PAD_X, dy, kit.xml_escape(text)))
        lines.append('      </text>')
        lines.append('    </g>')
    lines.append('  </g>')
    legend_y = height - 26
    lines.append('  <g id="legend" data-role="legend">')
    lines.append('    <text x="%d" y="%d" font-family="%s" font-size="11" fill="#5A626C">'
                 '实线=层级；虚线=交叉关系/推断；节点 title 与 data-source 记录来源'
                 '</text>' % (MARGIN, legend_y, kit.xml_escape(FONT)))
    lines.append('  </g>')
    lines.append('</svg>')
    return "\n".join(lines) + "\n"


def main():
    kit.configure_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=None)
    parser.add_argument("--outline", default=None)
    parser.add_argument("--out-outline", default=None)
    parser.add_argument("--out-spec", default=None)
    parser.add_argument("--out-svg", default=None)
    parser.add_argument("--layout", default="tree", choices=("tree", "radial"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.input and not args.outline:
        parser.error("one of --input or --outline is required")
    source_path = args.outline or args.input
    try:
        data = kit.load_json(source_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": "cannot read %s: %s"
                          % (source_path, exc)}, ensure_ascii=False))
        return 2

    warnings = []
    outline, warnings = normalize_outline(data, warnings)
    if outline is None:
        print(json.dumps({"ok": False, "error": "invalid outline",
                          "warnings": warnings}, ensure_ascii=False, indent=2))
        return 2
    layout = args.layout
    if str(outline.get("layout") or "") in ("radial",):
        layout = "radial"
    spec, geometry = build_spec(outline, layout, warnings)
    outline["render"] = {"width": spec["width"], "height": spec["height"],
                         "layout": spec["layout"],
                         "generator": "build_mindmap.py"}
    if args.out_outline:
        default_dir = Path(args.out_outline).parent
    elif args.out_spec:
        default_dir = Path(args.out_spec).parent
    else:
        default_dir = Path(args.out_svg).parent if args.out_svg else Path(".")
    out_outline = Path(args.out_outline) if args.out_outline else default_dir / "mindmap.json"
    out_spec = Path(args.out_spec) if args.out_spec else default_dir / "mindmap-spec.json"
    kit.dump_json(out_outline, outline)
    kit.dump_json(out_spec, spec)
    result = {"ok": True, "outline": str(out_outline), "spec": str(out_spec),
              "nodes": len(spec["nodes"]), "edges": len(spec["edges"]),
              "width": spec["width"], "height": spec["height"],
              "layout": spec["layout"], "warnings": warnings}
    if args.out_svg:
        svg_path = Path(args.out_svg)
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_text(render_svg(spec, outline, warnings), encoding="utf-8")
        result["svg"] = str(svg_path)
        result["bytes"] = kit.file_size(svg_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
