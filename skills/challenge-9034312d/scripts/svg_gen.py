#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize an apparatus/route spec and render it as one editable SVG.

The spec is authored by the agent from the real input materials (or supplied
by the case).  This module only assembles what the spec states:

* components/connections -> apparatus nodes/edges;
* stages/decisions -> route nodes/edges (a decision becomes a diamond);
* conditions stay callouts tied to their unit;
* a "both" spec renders two stacked panels into a single SVG.

Every node, edge, condition and panel keeps a named <g> group and all visible
text stays <text>.  Rendering itself is delegated to make_schematic.py.

Usage:
    python3 svg_gen.py --spec spec.json --out figures/fig4.svg --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import make_schematic

MM_TO_PX = 96.0 / 25.4
VALID_MODES = ("apparatus", "route", "both")
GAP_MM = 4.0


class SpecError(ValueError):
    pass


def _source(value):
    if isinstance(value, dict):
        out = {}
        for key in ("path", "locator", "quote", "page", "section"):
            if value.get(key) not in (None, ""):
                out[key] = value[key]
        return out
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if "/" in text or re.search(
                r"\.(md|txt|pdf|json|csv|tsv|docx?|pptx?|png|jpe?g|svg|xml)$",
                text, re.I):
            return {"path": text}
        return {"locator": text}
    return {}


def _label(item, fallback=""):
    return str(item.get("label") or item.get("name") or
               item.get("question") or fallback)


def _node_from_component(comp, index):
    return {
        "id": str(comp.get("id") or "A%d" % (index + 1)),
        "label": _label(comp, "component %d" % (index + 1)),
        "sublabel": comp.get("sublabel"),
        "shape": comp.get("shape") or comp.get("symbol") or "rounded",
        "source": _source(comp.get("source")),
        "rank": comp.get("rank"),
        "order": comp.get("order"),
        "fill": comp.get("fill"),
    }


def _node_from_stage(stage, index):
    node = {
        "id": str(stage.get("id") or "S%d" % (index + 1)),
        "label": _label(stage, "stage %d" % (index + 1)),
        "sublabel": stage.get("sublabel"),
        "shape": stage.get("shape") or "rounded",
        "source": _source(stage.get("source")),
        "rank": stage.get("rank"),
        "order": stage.get("order") if stage.get("order") is not None else index,
        "fill": stage.get("fill"),
    }
    if stage.get("type") in ("terminal", "milestone"):
        node["shape"] = stage.get("shape") or "ellipse"
    return {k: v for k, v in node.items() if v is not None}


def _clean_node(node):
    out = dict(node)
    for key in ("rank", "order", "fill", "stroke", "sublabel", "dashed"):
        if out.get(key) is None:
            out.pop(key, None)
    out["id"] = str(out.get("id"))
    out["label"] = _label(out, out["id"])
    out["source"] = _source(out.get("source"))
    return out


def _edge_from_connection(conn, index):
    direction = str(conn.get("direction") or "forward").lower()
    kind = conn.get("kind") or ("inferred" if direction == "undetermined" else "flow")
    return {
        "id": str(conn.get("id") or "K%d" % (index + 1)),
        "from": str(conn.get("from")),
        "to": str(conn.get("to")),
        "kind": kind,
        "label": conn.get("label") or conn.get("medium") or "",
        "medium": conn.get("medium") or conn.get("label") or "",
        "direction": direction,
        "from_port": conn.get("from_port"),
        "to_port": conn.get("to_port"),
        "source": _source(conn.get("source")),
        "route": conn.get("route") or "orthogonal",
        "dashed": bool(conn.get("dashed")) or direction == "undetermined",
        "bidirectional": direction == "undetermined",
        "feedback": bool(conn.get("feedback")),
    }


def _clean_edge(edge):
    out = dict(edge)
    for key in ("from_port", "to_port"):
        if out.get(key) in (None, ""):
            out.pop(key, None)
    out["id"] = str(out.get("id"))
    out["from"] = str(out.get("from"))
    out["to"] = str(out.get("to"))
    out["source"] = _source(out.get("source"))
    if out.get("label") is None:
        out.pop("label", None)
    return out


def _panel_from_raw(raw, default_mode):
    panel = dict(raw)
    mode = str(panel.get("mode") or default_mode or "apparatus").lower()
    if mode == "both":
        mode = "apparatus"
    if not panel.get("mode") and not panel.get("components") and \
            (panel.get("stages") or panel.get("decisions")):
        mode = "route"
    panel["mode"] = mode
    panel["id"] = str(panel.get("id") or mode)
    nodes = [dict(n) for n in panel.get("nodes", []) if isinstance(n, dict)]
    by_id = {str(n.get("id")): n for n in nodes}

    for index, comp in enumerate(panel.get("components", []) or []):
        if not isinstance(comp, dict):
            continue
        node = _node_from_component(comp, index)
        if node["id"] not in by_id:
            nodes.append(node)
            by_id[node["id"]] = node

    for index, stage in enumerate(panel.get("stages", []) or []):
        if not isinstance(stage, dict):
            continue
        node = _node_from_stage(stage, index)
        if node["id"] not in by_id:
            nodes.append(node)
            by_id[node["id"]] = node

    edges = [dict(e) for e in panel.get("edges", []) if isinstance(e, dict)]
    edge_ids = {str(e.get("id")) for e in edges}
    pairs = {(str(e.get("from")), str(e.get("to"))) for e in edges}
    for index, conn in enumerate(panel.get("connections", []) or []):
        if not isinstance(conn, dict):
            continue
        edge = _edge_from_connection(conn, index)
        if edge["id"] not in edge_ids:
            edges.append(edge)
            edge_ids.add(edge["id"])
            pairs.add((edge["from"], edge["to"]))

    decisions = [dict(d) for d in panel.get("decisions", []) or []
                 if isinstance(d, dict)]
    decision_ids = {str(d.get("id")) for d in decisions}
    # A route decision must be visible: add its diamond node and yes/no
    # branches only when the spec has not already declared them.
    for index, dec in enumerate(decisions):
        did = str(dec.get("id") or "D%d" % (index + 1))
        dec["id"] = did
        if did not in by_id:
            by_id[did] = _clean_node({
                "id": did,
                "label": _label(dec, did),
                "sublabel": dec.get("criterion") or dec.get("criterion_label"),
                "shape": "diamond",
                "source": _source(dec.get("criterion_source") or dec.get("source")),
            })
            nodes.append(by_id[did])
        src = _source(dec.get("criterion_source") or dec.get("source"))
        after = dec.get("after")
        if after and str(after) in by_id and (str(after), did) not in pairs:
            eid = "%s-in" % did
            if eid in edge_ids:
                eid = "%s-in-%d" % (did, index)
            edges.append({
                "id": eid, "from": str(after), "to": did, "kind": "flow",
                "label": "", "medium": "sequence", "direction": "forward",
                "source": src, "route": "orthogonal",
            })
            edge_ids.add(eid)
            pairs.add((str(after), did))
        for branch in ("yes", "no"):
            target = dec.get(branch)
            if not target:
                continue
            target = str(target)
            if (did, target) in pairs:
                continue
            bid = "%s-%s" % (did, branch)
            if bid in edge_ids:
                bid = "%s-%s-%d" % (did, branch, index)
            edge = {
                "id": bid, "from": did, "to": target, "kind": branch,
                "label": branch, "medium": "decision", "direction": "forward",
                "source": src, "route": "orthogonal",
                "feedback": bool(dec.get("feedback_%s" % branch)),
            }
            if dec.get("feedback_%s" % branch):
                edge["dashed"] = True
            edges.append(edge)
            edge_ids.add(bid)
            pairs.add((did, target))

    if panel["mode"] == "route" and len(nodes) > 1 and not decisions:
        # Without decisions the stated stage order is itself source-bearing:
        # connect consecutive stages so the route is a sequence rather than
        # loose boxes.  With decisions, branches define the order instead;
        # adding a direct stage-to-stage edge would bypass the decision.
        stage_ids = [str(n["id"]) for n in nodes
                     if str(n["id"]) not in decision_ids]
        stage_edges = [e for e in edges
                       if str(e.get("from")) in stage_ids and
                       str(e.get("to")) in stage_ids]
        if not stage_edges and len(stage_ids) > 1:
            ordered = sorted([n for n in nodes if str(n["id"]) in stage_ids],
                             key=lambda n: (n.get("order", 0), n["id"]))
            for index in range(len(ordered) - 1):
                a, b = ordered[index], ordered[index + 1]
                eid = "E%d" % (index + 1)
                while eid in edge_ids:
                    eid += "b"
                edges.append({
                    "id": eid, "from": a["id"], "to": b["id"],
                    "kind": "flow", "label": "", "medium": "sequence",
                    "direction": "forward", "route": "orthogonal",
                    "source": _source(b.get("source")),
                })
                edge_ids.add(eid)

    panel["nodes"] = [_clean_node(n) for n in nodes if n.get("id")]
    panel["edges"] = [_clean_edge(e) for e in edges
                      if e.get("from") and e.get("to")]
    panel["conditions"] = [dict(c) for c in panel.get("conditions", []) or []
                           if isinstance(c, dict)]
    panel["decisions"] = decisions
    return panel


def _dedupe_panel(panel, seen, warnings):
    mapping = {}
    node_ids = {str(n["id"]) for n in panel["nodes"]}

    def unique(old):
        old = str(old)
        if old not in seen:
            seen.add(old)
            return old
        base = "%s_%s" % (panel["id"], old)
        new, index = base, 2
        while new in seen:
            new = "%s_%d" % (base, index)
            index += 1
        seen.add(new)
        mapping[old] = new
        warnings.append("duplicate id %s renamed to %s" % (old, new))
        return new

    for node in panel["nodes"]:
        node["id"] = unique(node["id"])
    for edge in panel["edges"]:
        edge["id"] = unique(edge["id"])
    for cond in panel["conditions"]:
        if cond.get("id"):
            cond["id"] = unique(cond["id"])
    for dec in panel["decisions"]:
        did = str(dec.get("id") or "")
        if not did:
            continue
        if did in node_ids:
            # The decision node and its metadata are one object; keep one id.
            dec["id"] = mapping.get(did, did)
        else:
            dec["id"] = unique(did)

    if mapping:
        for edge in panel["edges"]:
            edge["from"] = mapping.get(edge["from"], edge["from"])
            edge["to"] = mapping.get(edge["to"], edge["to"])
        for cond in panel["conditions"]:
            cond["location"] = mapping.get(str(cond.get("location")), cond.get("location"))
        for dec in panel["decisions"]:
            for key in ("after", "yes", "no"):
                if dec.get(key) is not None:
                    dec[key] = mapping.get(str(dec[key]), dec[key])


def normalize_spec(raw, mode_override=None):
    """Return (normalized, warnings).  Structural failures raise SpecError."""
    if not isinstance(raw, dict):
        raise SpecError("spec must be a JSON object")
    mode = str(mode_override or raw.get("mode") or "apparatus").lower()
    if mode not in VALID_MODES:
        raise SpecError("mode must be one of %s" % (VALID_MODES,))

    panels_raw = raw.get("panels")
    if isinstance(panels_raw, list) and panels_raw:
        panels = [_panel_from_raw(p, mode) for p in panels_raw if isinstance(p, dict)]
    else:
        panels = [_panel_from_raw(raw, mode if mode != "both" else "apparatus")]
        if mode == "both":
            raise SpecError("mode=both requires a non-empty panels array")
    panels = [p for p in panels if p["nodes"]]
    if not panels:
        raise SpecError("spec has no nodes/components/stages to draw")
    if mode == "both" and len(panels) < 2:
        raise SpecError("mode=both requires two panels")
    if len(panels) > 4:
        raise SpecError("at most 4 panels are supported")

    warnings = []
    requested_pt = float(raw.get("font_size_pt") or 8.0)
    font_size_pt = requested_pt
    if requested_pt < 8.0:
        # Small annotations (ports, footnotes) are drawn at ~0.88x the base
        # size; 8 pt keeps every visible label at or above the 7 pt floor.
        font_size_pt = 8.0
        warnings.append("font_size_pt %.2f raised to 8.0 for the 7 pt floor"
                        % requested_pt)
    seen = set()
    for panel in panels:
        _dedupe_panel(panel, seen, warnings)

    width_mm = float(raw.get("width_mm") or 180.0)
    height_mm = float(raw.get("height_mm") or
                      (110.0 if len(panels) == 1 else 110.0 * len(panels)))
    # A long linear apparatus needs room for readable unit labels; widen the
    # canvas instead of shrinking boxes until the text overflows.
    suggested = width_mm
    widest_panel = None
    for panel in panels:
        if (panel["mode"] == "apparatus" and
                (panel.get("layout") or "layered") == "layered" and
                str(panel.get("direction") or "LR").upper() == "LR"):
            needed = 16.0 + 28.0 * len(panel["nodes"])
            if needed > suggested:
                suggested = needed
                widest_panel = panel
    if suggested > width_mm:
        width_mm = min(320.0, suggested)
        warnings.append("width_mm raised to %.1f for %d apparatus units"
                        % (width_mm, len(widest_panel["nodes"])))
    min_panel_mm = 45.0
    if len(panels) > 1:
        needed = min_panel_mm * len(panels) + GAP_MM * (len(panels) - 1)
        if height_mm < needed:
            warnings.append("height_mm %.1f raised to %.1f for readable panels"
                            % (height_mm, needed))
            height_mm = needed

    elements, connections, conditions, decisions = [], [], [], []
    for panel in panels:
        decision_ids = {str(d.get("id")) for d in panel["decisions"]}
        for node in panel["nodes"]:
            if str(node["id"]) in decision_ids:
                continue
            ntype = node.get("type")
            if not ntype:
                if panel["mode"] == "route":
                    ntype = ("terminal" if str(node.get("shape")) == "ellipse"
                             else "stage")
                else:
                    ntype = "component"
            elements.append({
                "id": str(node["id"]), "label": node["label"], "type": ntype,
                "sublabel": node.get("sublabel"), "panel": panel["id"],
                "source": node.get("source") or {},
            })
        for edge in panel["edges"]:
            connections.append({
                "id": str(edge["id"]), "from": str(edge["from"]),
                "to": str(edge["to"]),
                "medium": edge.get("medium") or edge.get("label") or "",
                "direction": str(edge.get("direction") or "forward"),
                "kind": edge.get("kind") or "flow",
                "label": edge.get("label") or "",
                "from_port": edge.get("from_port"),
                "to_port": edge.get("to_port"),
                "panel": panel["id"],
                "source": edge.get("source") or {},
            })
        for cond in panel["conditions"]:
            conditions.append({
                "id": str(cond.get("id") or "P%d" % (len(conditions) + 1)),
                "location": str(cond.get("location") or ""),
                "params": cond.get("params") if cond.get("params") is not None
                          else cond.get("value") or cond.get("label") or "",
                "panel": panel["id"],
                "source": _source(cond.get("source")),
            })
        for dec in panel["decisions"]:
            decisions.append({
                "id": str(dec.get("id") or "D%d" % (len(decisions) + 1)),
                "after": str(dec.get("after") or ""),
                "question": _label(dec, ""),
                "yes": str(dec.get("yes") or ""),
                "no": str(dec.get("no") or ""),
                "criterion": dec.get("criterion") or "",
                "criterion_source": _source(dec.get("criterion_source")),
                "panel": panel["id"],
            })

    normalized = {
        "figure_id": str(raw.get("figure_id") or raw.get("id") or "figure"),
        "mode": mode,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "font_family": raw.get("font_family") or "Liberation Sans",
        "font_size_pt": font_size_pt,
        "title": raw.get("title") or "",
        "panels": panels,
        "elements": elements,
        "connections": connections,
        "conditions": conditions,
        "decisions": decisions,
        "warnings": warnings,
        "errors": [],
    }
    normalized["errors"] = validate_normalized(normalized)
    return normalized, warnings


def validate_normalized(norm):
    errors = []
    node_ids = {e["id"] for e in norm["elements"]}
    for panel in norm["panels"]:
        ids = {str(n["id"]) for n in panel["nodes"]}
        for node in panel["nodes"]:
            if not str(node.get("label") or "").strip():
                errors.append("node %s has no label" % node["id"])
        for edge in panel["edges"]:
            if edge["from"] not in ids or edge["to"] not in ids:
                errors.append("edge %s references missing endpoint (%s -> %s)"
                              % (edge["id"], edge["from"], edge["to"]))
        for cond in panel["conditions"]:
            if str(cond.get("location")) not in ids:
                errors.append("condition %s references missing location %s"
                              % (cond.get("id"), cond.get("location")))
        for dec in panel["decisions"]:
            if dec.get("after") and str(dec["after"]) not in ids:
                errors.append("decision %s 'after' target %s missing"
                              % (dec.get("id"), dec.get("after")))
            for branch in ("yes", "no"):
                if dec.get(branch) and str(dec[branch]) not in ids:
                    errors.append("decision %s %s target %s missing"
                                  % (dec.get("id"), branch, dec.get(branch)))
    if any(not e["source"] for e in norm["elements"]):
        errors.append("one or more elements have no source")
    if any(not c["source"] for c in norm["connections"]):
        errors.append("one or more connections have no source")
    if any(not c["source"] for c in norm["conditions"]):
        errors.append("one or more conditions have no source")
    # keep node_ids used for future extension / debugging
    _ = node_ids
    return errors


def _render_panel(panel, norm, width_mm, height_mm, transparent):
    sub = {
        "figure_id": "%s %s" % (norm["figure_id"], panel.get("title") or panel["id"]),
        "title": panel.get("title") or "",
        "width_mm": width_mm,
        "height_mm": height_mm,
        "font_family": norm["font_family"],
        "font_size_pt": norm["font_size_pt"],
        "direction": panel.get("direction") or
                     ("TB" if panel["mode"] == "route" else "LR"),
        "layout": panel.get("layout") or "layered",
        "nodes": panel["nodes"],
        "edges": panel["edges"],
        "groups": panel.get("groups") or [],
        "legend": panel.get("legend") or [],
        "conditions": panel.get("conditions") or [],
        "footnote": panel.get("footnote") or "",
        "transparent": transparent,
    }
    return make_schematic.Diagram(sub).render()


def _body_of(svg):
    body = svg.split("</defs>", 1)[1].rsplit("</svg>", 1)[0]
    return re.sub(r"<title>.*?</title>\s*", "", body, count=1, flags=re.S)


def _defs_of(svg):
    return svg.split("<defs>", 1)[1].split("</defs>", 1)[0]


def build_svg(norm, out_path):
    width_mm = float(norm["width_mm"])
    height_mm = float(norm["height_mm"])
    panels = norm["panels"]
    W = width_mm * MM_TO_PX
    H = height_mm * MM_TO_PX

    if len(panels) == 1:
        svg = _render_panel(panels[0], norm, width_mm, height_mm, False)
    else:
        gap_px = GAP_MM * MM_TO_PX
        available_mm = height_mm - GAP_MM * (len(panels) - 1)
        # Routes are tall vertical lanes; apparatus rows are shallow.  Split
        # the canvas accordingly instead of leaving a half-empty panel.
        weights = [2.0 if panel["mode"] == "route" else 1.0 for panel in panels]
        total_weight = sum(weights)
        panel_heights = [available_mm * w / total_weight for w in weights]
        rendered = []
        for panel, panel_h_mm in zip(panels, panel_heights):
            rendered.append(_render_panel(panel, norm, width_mm, panel_h_mm, True))
        defs = _defs_of(rendered[0])
        body = []
        offset = 0.0
        for index, (panel, svg_text) in enumerate(zip(panels, rendered)):
            pid = re.sub(r"[^A-Za-z0-9_.-]+", "-", panel["id"])
            body.append(
                '<g id="panel-%s" data-role="panel" transform="translate(0,%.2f)">%s</g>'
                % (pid, offset, _body_of(svg_text)))
            offset += panel_heights[index] * MM_TO_PX + gap_px
        svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="%.1fmm" height="%.1fmm" '
            'viewBox="0 0 %.2f %.2f">\n<title>%s</title>\n'
            '<rect x="0" y="0" width="%.2f" height="%.2f" fill="#ffffff"/>\n'
            '<defs>%s</defs>\n%s\n</svg>\n'
            % (width_mm, height_mm, W, H, escape(norm["figure_id"]), W, H,
               defs, "\n".join(body)))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    return {
        "ok": True,
        "path": str(out),
        "bytes": len(svg.encode("utf-8")),
        "figure_id": norm["figure_id"],
        "mode": norm["mode"],
        "panels": len(panels),
        "nodes": len(norm["elements"]) + len(norm["decisions"]),
        "edges": len(norm["connections"]),
        "text_elements": svg.count("<text"),
        "named_groups": len(re.findall(r"<g\b[^>]*\bid=", svg)),
        "width_mm": width_mm,
        "height_mm": height_mm,
    }


def main():
    ap = argparse.ArgumentParser(description="Spec -> editable SVG (apparatus/route)")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        raw = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        norm, warnings = normalize_spec(raw, args.mode)
    except (OSError, json.JSONDecodeError, SpecError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    result = build_svg(norm, args.out)
    result["warnings"] = warnings
    result["spec_errors"] = norm.get("errors") or []
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
