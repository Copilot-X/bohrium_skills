#!/usr/bin/env python3
"""Convert a source-bound pathway entity table into a figure spec, an editable
SVG and an honest result.json draft (media/official aliases included).

Usage:
    python3 pathway_to_spec.py --pathway /app/work/pathway.json \
        --out-spec /app/work/figure-spec.json \
        --out-svg /app/output/figures/fig3.svg \
        --result-out /app/output/result.json \
        --root /app/output --reviewed --json

The converter never invents entities or edges: it only renders what the table
contains.  It never renders PDF/PNG and never requires them.  Optional
--preview/--pdf values are only recorded when those files already exist and are
valid; otherwise the result draft stays honest with export_artifact=null.

Rendering uses shared make_schematic.py when available.  If that helper is
missing or fails, a built-in stdlib renderer produces the same editable SVG
contract (named <g id> per compartment/node/edge, all text as <text>), so a
missing helper can never leave the run without a deliverable.

--reviewed asserts that a human/agent has checked every activation/inhibition
arrow against its quoted source.  Without it the draft is status=partial.

Stdlib only. Exit 0 = generated (warnings may be present in the JSON report),
2 = invalid input or render failure.  Invalid input with --result-out also
writes a minimal status=input_error result (no figure, no fabricated content).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

EFFECTS = {
    "activate", "inhibit", "translocate", "bind", "modify",
    "association", "unknown_link",
}
KINDS = {"reported", "inferred"}
EVIDENCE_STRENGTH = {"strong", "moderate", "weak", "speculative"}

EFFECT_KIND = {
    "activate": "activate",
    "inhibit": "inhibit",
    "translocate": "translocate",
    "bind": "bind",
    "modify": "default",
    "association": "bind",
    "unknown_link": "inferred",
}
EFFECT_LABEL = {
    "activate": "+",
    "inhibit": "-",
    "translocate": "translocate",
    "bind": "bind",
    "modify": "modify",
    "association": "assoc",
    "unknown_link": "?",
}
LEGEND_LABEL = {
    "activate": "activation",
    "inhibit": "inhibition",
    "translocate": "translocation",
    "bind": "binding",
    "modify": "modification",
    "association": "association (no direction)",
    "unknown_link": "unknown link",
}

TYPE_SHAPE = {
    "ligand": "circle",
    "receptor": "rounded",
    "kinase": "ellipse",
    "phosphatase": "ellipse",
    "gtpase": "ellipse",
    "tf": "hexagon",
    "complex": "rect",
    "metabolite": "diamond",
    "gene": "rect",
    "rna": "rect",
    "channel": "rounded",
    "cell": "rounded",
    "protein": "rounded",
    "other": "rect",
}
TYPE_FILL = {
    "ligand": "#e6f2fa",
    "receptor": "#e3edf7",
    "kinase": "#fde9d9",
    "phosphatase": "#fbe3e0",
    "gtpase": "#fde9d9",
    "tf": "#ece4f3",
    "complex": "#e4f2ec",
    "metabolite": "#f7f0cf",
    "gene": "#efefef",
    "rna": "#eef4e8",
    "channel": "#e3edf7",
    "cell": "#fceef2",
    "protein": "#eef1f4",
    "other": "#f2f2f2",
}
TYPE_ALIASES = {
    "配体": "ligand", "受体": "receptor", "激酶": "kinase",
    "磷酸酶": "phosphatase", "gtp酶": "gtpase", "小g蛋白": "gtpase",
    "转录因子": "tf", "复合物": "complex", "复合体": "complex",
    "代谢物": "metabolite", "基因": "gene", "通道": "channel",
    "细胞": "cell", "蛋白": "protein", "蛋白质": "protein",
    "其他": "other", "transcription_factor": "tf", "transcription factor": "tf",
    "enzyme": "protein", "small_molecule": "ligand", "ion": "metabolite",
}
MOVE_WORDS = ("translocat", "nuclear import", "nuclear export", "shuttle",
              "traffic", "transport", "import", "export", "secretion",
              "endocyt", "exocyt")

MM_TO_PX = 96.0 / 25.4
PT_TO_PX = 96.0 / 72.0


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def source_parts(value):
    if isinstance(value, dict):
        return (str(value.get("path") or "").strip(),
                str(value.get("locator") or "").strip(),
                str(value.get("quote") or "").strip())
    text = str(value or "").strip()
    return text, "", ""


def source_text(value):
    path, locator, _quote = source_parts(value)
    if not path and not locator:
        return "not_provided"
    return " ".join(x for x in (path, locator) if x).strip()


def source_dict(value):
    path, locator, quote = source_parts(value)
    if not path and not locator and not quote:
        return {}
    item = {}
    if path:
        item["path"] = path
    if locator:
        item["locator"] = locator
    if quote:
        item["quote"] = quote
    return item


def source_ok(value):
    path, locator, _quote = source_parts(value)
    return bool(path or locator) and not (
        (path or locator).lower() in ("not_provided", "unknown", "n/a"))


def is_inferred_edge(edge):
    return (str(edge.get("kind") or "reported") == "inferred" or
            str(edge.get("effect") or "") == "unknown_link")


def is_reported_edge(edge):
    return not is_inferred_edge(edge)


def warn(warnings, message):
    if message not in warnings:
        warnings.append(message)


def normalize_type(value):
    text = str(value or "").strip()
    low = text.lower()
    if low in TYPE_SHAPE:
        return low
    return TYPE_ALIASES.get(low, TYPE_ALIASES.get(text, "other"))


def validate_table(data):
    errors, warnings = [], []
    if not isinstance(data, dict):
        return ["pathway root is not an object"], warnings
    compartments = data.get("compartments")
    molecules = data.get("molecules")
    interactions = data.get("interactions")
    if not isinstance(compartments, list) or not compartments:
        errors.append("compartments[] must be a non-empty list")
    if not isinstance(molecules, list) or not molecules:
        errors.append("molecules[] must be a non-empty list")
    if not isinstance(interactions, list) or not interactions:
        errors.append("interactions[] must be a non-empty list")
    if errors:
        return errors, warnings

    comp_ids = set()
    for index, comp in enumerate(compartments):
        if not isinstance(comp, dict):
            errors.append("compartment[%d] is not an object" % index)
            continue
        cid = str(comp.get("id") or "").strip()
        if not cid:
            errors.append("compartment[%d] missing id" % index)
            continue
        if cid in comp_ids:
            errors.append("duplicate compartment id: %s" % cid)
        comp_ids.add(cid)
        if not comp.get("label"):
            warn(warnings, "compartment %s missing label" % cid)
        if not source_ok(comp.get("source")):
            warn(warnings, "compartment %s has no usable source" % cid)

    mol_ids = set()
    for index, mol in enumerate(molecules):
        if not isinstance(mol, dict):
            errors.append("molecule[%d] is not an object" % index)
            continue
        mid = str(mol.get("id") or "").strip()
        if not mid:
            errors.append("molecule[%d] missing id" % index)
            continue
        if mid in mol_ids:
            errors.append("duplicate molecule id: %s" % mid)
        mol_ids.add(mid)
        if not mol.get("label"):
            errors.append("molecule %s missing label" % mid)
        if not mol.get("type"):
            warn(warnings, "molecule %s missing type" % mid)
        compartment = str(mol.get("compartment") or "").strip()
        if compartment not in comp_ids:
            errors.append("molecule %s references unknown compartment: %s" %
                          (mid, compartment))
        if not source_ok(mol.get("source")):
            warn(warnings, "molecule %s has no usable source" % mid)
        strength = mol.get("evidence_strength")
        if strength and str(strength) not in EVIDENCE_STRENGTH:
            warn(warnings, "molecule %s has unknown evidence_strength: %s" %
                 (mid, strength))

    int_ids = set()
    for index, edge in enumerate(interactions):
        if not isinstance(edge, dict):
            errors.append("interaction[%d] is not an object" % index)
            continue
        iid = str(edge.get("id") or "").strip()
        if not iid:
            errors.append("interaction[%d] missing id" % index)
            continue
        if iid in int_ids:
            errors.append("duplicate interaction id: %s" % iid)
        int_ids.add(iid)
        src = str(edge.get("from") or "").strip()
        dst = str(edge.get("to") or "").strip()
        if src not in mol_ids:
            errors.append("interaction %s has unknown from: %s" % (iid, src))
        if dst not in mol_ids:
            errors.append("interaction %s has unknown to: %s" % (iid, dst))
        effect = str(edge.get("effect") or "").strip()
        if effect not in EFFECTS:
            errors.append("interaction %s has unknown effect: %s" % (iid, effect))
        kind = str(edge.get("kind") or "reported").strip()
        if kind not in KINDS:
            errors.append("interaction %s has unknown kind: %s" % (iid, kind))
        if not source_ok(edge.get("source")):
            if kind == "reported":
                warn(warnings, "reported interaction %s has no usable source" % iid)
            else:
                warn(warnings, "inferred interaction %s has no usable source" % iid)
        strength = edge.get("evidence_strength")
        if strength and str(strength) not in EVIDENCE_STRENGTH:
            warn(warnings, "interaction %s has unknown evidence_strength: %s" %
                 (iid, strength))
    return errors, warnings


def compartment_order(data):
    order = {}
    for index, comp in enumerate(data.get("compartments") or []):
        cid = str(comp.get("id"))
        try:
            order[cid] = int(comp.get("order", index))
        except (TypeError, ValueError):
            order[cid] = index
    return order


def assign_ranks(molecules, interactions, comp_order, warnings):
    all_ranked = all("rank" in mol for mol in molecules)
    ranks = {}
    if all_ranked:
        for mol in molecules:
            try:
                ranks[str(mol["id"])] = max(0, int(mol["rank"]))
            except (TypeError, ValueError):
                ranks[str(mol["id"])] = 0
    else:
        missing = [str(m["id"]) for m in molecules if "rank" not in m]
        if missing:
            warn(warnings, "rank missing on %d molecule(s); derived from compartments "
                           "and interaction order" % len(missing))
        for mol in molecules:
            cid = str(mol.get("compartment") or "")
            ranks[str(mol["id"])] = comp_order.get(cid, 0)
        flow = [e for e in interactions
                if str(e.get("effect")) not in ("bind", "association")]
        for _ in range(len(molecules) + 1):
            changed = False
            for edge in flow:
                src, dst = str(edge.get("from")), str(edge.get("to"))
                if src in ranks and dst in ranks and ranks[dst] < ranks[src] + 1:
                    ranks[dst] = ranks[src] + 1
                    changed = True
            if not changed:
                break
    counters = {}
    ordered = {}
    for mol in molecules:
        mid = str(mol["id"])
        rank = ranks[mid]
        if "order" in mol:
            try:
                order = int(mol["order"])
            except (TypeError, ValueError):
                order = counters.get(rank, 0)
        else:
            order = counters.get(rank, 0)
        counters[rank] = max(counters.get(rank, 0), order + 1)
        ordered[mid] = order
    return ranks, ordered


def build_nodes(data, ranks, orders, warnings):
    nodes = []
    italic_ids = []
    gene_italic = str((data.get("naming") or {}).get("gene_style") or "").lower() == "italic"
    for mol in data["molecules"]:
        mid = str(mol["id"])
        mtype = normalize_type(mol.get("type"))
        if str(mol.get("type") or "").strip().lower() not in TYPE_SHAPE:
            warn(warnings, "molecule %s type %r normalized to %s" %
                 (mid, mol.get("type"), mtype))
        node = {
            "id": mid,
            "label": str(mol.get("label") or mid),
            "shape": mol.get("shape") or TYPE_SHAPE.get(mtype, "rect"),
            "fill": mol.get("fill") or TYPE_FILL.get(mtype, "#f2f2f2"),
            "stroke": mol.get("stroke") or "#444444",
            "stroke_width": float(mol.get("stroke_width", 1.1)),
            "rank": ranks[mid],
            "order": orders[mid],
            "max_text_px": float(mol.get("max_text_px", 84)),
            "source": source_text(mol.get("source")),
            "molecule_type": mtype,
        }
        if mol.get("sublabel"):
            node["sublabel"] = str(mol["sublabel"])
        if mol.get("dashed"):
            node["dashed"] = True
        nodes.append(node)
        display = mol.get("display") or {}
        if display.get("italic") is True or (
                gene_italic and str(mol.get("entity_kind") or "") == "gene"):
            italic_ids.append(mid)
    return nodes, italic_ids


def build_groups(data):
    comp_order = compartment_order(data)
    groups = []
    for comp in sorted(data["compartments"], key=lambda c: comp_order.get(str(c.get("id")), 0)):
        cid = str(comp.get("id"))
        members = [str(m["id"]) for m in data["molecules"]
                   if str(m.get("compartment")) == cid]
        if not members:
            continue
        groups.append({
            "id": cid,
            "label": str(comp.get("label") or cid),
            "nodes": members,
            "fill": comp.get("fill") or "#f7f7f7",
            "stroke": comp.get("stroke") or "#bbbbbb",
            "dashed": bool(comp.get("dashed", True)),
            "pad_px": float(comp.get("pad_px", 10)),
        })
    return groups


def edge_label(edge):
    if "label" in edge and str(edge.get("label") or "").strip():
        return str(edge["label"])
    return EFFECT_LABEL.get(str(edge.get("effect")), "")


def build_edges(data, ranks, warnings):
    edges = []
    by_id = {str(m["id"]): m for m in data["molecules"]}
    for edge in data["interactions"]:
        iid = str(edge["id"])
        effect = str(edge["effect"])
        inferred = is_inferred_edge(edge)
        src, dst = str(edge["from"]), str(edge["to"])
        if effect == "translocate":
            src_comp = str(by_id[src].get("compartment"))
            dst_comp = str(by_id[dst].get("compartment"))
            if src_comp == dst_comp:
                warn(warnings, "translocation %s does not cross compartments" % iid)
        mechanism = str(edge.get("mechanism") or "")
        if effect in ("activate", "inhibit", "modify"):
            src_comp = str(by_id[src].get("compartment"))
            dst_comp = str(by_id[dst].get("compartment"))
            if src_comp != dst_comp:
                if any(word in mechanism.lower() for word in MOVE_WORDS):
                    warn(warnings, "interaction %s crosses compartments and mentions "
                                   "transport; consider effect=translocate" % iid)
                else:
                    warn(warnings, "interaction %s crosses compartments as signaling; "
                                   "confirm the molecule itself does not move" % iid)
        if "route" in edge:
            route = str(edge["route"])
        elif ranks[src] == ranks[dst]:
            route = "straight"
        elif ranks[dst] < ranks[src]:
            route = "curve"
        else:
            route = "orthogonal"
        item = {
            "id": iid,
            "from": src,
            "to": dst,
            "kind": "inferred" if inferred else EFFECT_KIND[effect],
            "label": edge_label(edge),
            "route": route,
            "source": source_text(edge.get("source")),
        }
        if inferred:
            item["dashed"] = True
            item["inferred"] = True
        edges.append(item)
    return edges


def build_legend(data, edges):
    present = []
    seen = set()
    for edge in data["interactions"]:
        if is_inferred_edge(edge):
            continue
        effect = str(edge["effect"])
        if effect not in seen:
            seen.add(effect)
            present.append(effect)
    labels = {}
    for item in ((data.get("legend") or {}).get("edge_semantics") or []):
        if isinstance(item, dict) and item.get("effect"):
            labels[str(item["effect"])] = str(item.get("label") or LEGEND_LABEL.get(
                str(item["effect"]), str(item["effect"])))
    legend = []
    for effect in present:
        legend.append({"kind": EFFECT_KIND[effect],
                       "label": labels.get(effect, LEGEND_LABEL.get(effect, effect))})
    if any(is_inferred_edge(e) for e in data["interactions"]):
        legend.append({"kind": "inferred", "label": "inferred / uncertain"})
    return legend


def build_spec(data, warnings):
    comp_order = compartment_order(data)
    ranks, orders = assign_ranks(data["molecules"], data["interactions"],
                                 comp_order, warnings)
    nodes, italic_ids = build_nodes(data, ranks, orders, warnings)
    groups = build_groups(data)
    edges = build_edges(data, ranks, warnings)
    legend = build_legend(data, edges)
    canvas = data.get("canvas") or {}
    spec = {
        "figure_id": data.get("figure_id") or "Fig.pathway",
        "title": data.get("title") or "",
        "width_mm": float(canvas.get("width_mm") or data.get("width_mm") or 180),
        "height_mm": float(canvas.get("height_mm") or data.get("height_mm") or 120),
        "direction": str(canvas.get("direction") or data.get("direction") or "LR"),
        "layout": str(canvas.get("layout") or data.get("layout") or "layered"),
        "font_family": str(canvas.get("font_family") or data.get("font_family")
                           or "Liberation Sans"),
        "font_size_pt": float(canvas.get("font_size_pt") or data.get("font_size_pt") or 8),
        "groups": groups,
        "nodes": nodes,
        "edges": edges,
        "legend": legend,
        "footnote": data.get("footnote") or (
            "All nodes and edges are transcribed from the case materials; "
            "dashed edges mark inferred or translocated relations."),
    }
    return spec, italic_ids


def find_make_schematic(script_dir):
    names = ("make_schematic.py", "make_schematic.py.md")
    candidates = []
    for name in names:
        candidates.append(script_dir / name)
    env = os.environ.get("SKILL_DIR")
    if env:
        for name in names:
            candidates.append(Path(env) / "scripts" / name)
    for parent in script_dir.parents:
        for name in names:
            candidates.append(parent / "scripts" / name)
            candidates.append(parent / "shared" / "scripts" / name)
    for path in candidates:
        if path.is_file():
            return path
    return None


def apply_gene_italic(svg_path, italic_ids):
    if not italic_ids:
        return []
    path = Path(svg_path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    changed = []
    for nid in italic_ids:
        pattern = re.compile(r'(<g id="%s"[^>]*>)(.*?)(</g>)' % re.escape(nid), re.S)

        def repl(match):
            body = match.group(2)
            if "font-style=" in body:
                return match.group(0)
            body = body.replace("<text ", '<text font-style="italic" ')
            return match.group(1) + body + match.group(3)

        updated = pattern.sub(repl, raw, count=1)
        if updated != raw:
            changed.append(nid)
            raw = updated
    path.write_text(raw, encoding="utf-8")
    return changed


# ---------------------------------------------------------------------------
# Built-in fallback renderer (stdlib only).  Same editable contract as
# make_schematic.py: one named <g> per compartment/node/edge, text stays text.
# ---------------------------------------------------------------------------
def text_width(text, size_px):
    total = 0.0
    for ch in str(text):
        if ord(ch) > 0x2E80:
            total += size_px
        elif ch in "il.,:;'|!()[]":
            total += size_px * 0.34
        elif ch in "mwMW@%":
            total += size_px * 0.95
        else:
            total += size_px * 0.56
    return total


def wrap_label(label, max_width, size_px):
    tokens, buf = [], ""
    for ch in str(label):
        if ord(ch) > 0x2E80:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ch == " ":
            if buf:
                tokens.append(buf)
            tokens.append(" ")
            buf = ""
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    lines, cur = [], ""
    for tok in tokens:
        candidate = cur + tok
        if text_width(candidate, size_px) <= max_width or not cur:
            cur = candidate
        else:
            lines.append(cur.rstrip())
            cur = "" if tok == " " else tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def _attr(value):
    return escape(str(value), {'"': "&quot;"})


def _edge_style(kind, dashed):
    styles = {
        "activate": ("#0072B2", "arrow-blue", 1.5, None),
        "inhibit": ("#D55E00", "arrow-inhibit", 1.5, None),
        "translocate": ("#333333", "arrow", 1.4, "5 3"),
        "bind": ("#666666", None, 1.1, None),
        "inferred": ("#666666", "arrow-gray", 1.2, "5 3"),
        "default": ("#333333", "arrow", 1.3, None),
    }
    color, marker, width, dash = styles.get(kind, styles["default"])
    if dashed and not dash:
        dash = "5 3"
    return color, marker, width, dash


def _anchor(node, tx, ty):
    cx, cy = node["_cx"], node["_cy"]
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    half_w, half_h = node["_w"] / 2.0, node["_h"] / 2.0
    if node.get("shape") == "ellipse":
        denom = ((dx * dx) / (half_w * half_w) +
                 (dy * dy) / (half_h * half_h)) ** 0.5
        t = 1.0 / denom if denom else 0.0
        return cx + dx * t, cy + dy * t
    tx_ = half_w / abs(dx) if dx else float("inf")
    ty_ = half_h / abs(dy) if dy else float("inf")
    t = min(tx_, ty_)
    return cx + dx * t, cy + dy * t


def _edge_path(edge, by_id, direction):
    src = by_id.get(str(edge.get("from")))
    dst = by_id.get(str(edge.get("to")))
    if not src or not dst:
        return None, None
    x1, y1 = _anchor(src, dst["_cx"], dst["_cy"])
    x2, y2 = _anchor(dst, src["_cx"], src["_cy"])
    route = str(edge.get("route") or "straight")
    if route == "curve":
        mx = (x1 + x2) / 2.0
        path = "M%.2f,%.2f C%.2f,%.2f %.2f,%.2f %.2f,%.2f" % (
            x1, y1, mx, y1, mx, y2, x2, y2)
        return path, ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    if route == "orthogonal":
        if direction == "TB" and abs(y2 - y1) > abs(x2 - x1):
            mid = (y1 + y2) / 2.0
            points = [(x1, y1), (x1, mid), (x2, mid), (x2, y2)]
        else:
            mid = (x1 + x2) / 2.0
            points = [(x1, y1), (mid, y1), (mid, y2), (x2, y2)]
    else:
        points = [(x1, y1), (x2, y2)]
    path = "M" + " L".join("%.2f,%.2f" % (px, py) for px, py in points)
    return path, (sum(p[0] for p in points) / len(points),
                  sum(p[1] for p in points) / len(points))


def _shape_markup(node, italic):
    cx, cy = node["_cx"], node["_cy"]
    w, h = node["_w"], node["_h"]
    x, y = cx - w / 2.0, cy - h / 2.0
    fill = _attr(node.get("fill") or "#ffffff")
    stroke = _attr(node.get("stroke") or "#444444")
    sw = float(node.get("stroke_width", 1.1))
    dash = ' stroke-dasharray="5 3"' if node.get("dashed") else ""
    shape = node.get("shape") or "rect"
    if shape == "circle":
        return ('<circle cx="%.2f" cy="%.2f" r="%.2f" fill="%s" stroke="%s" '
                'stroke-width="%.2f"%s/>' % (cx, cy, w / 2.0, fill, stroke, sw, dash))
    if shape == "ellipse":
        return ('<ellipse cx="%.2f" cy="%.2f" rx="%.2f" ry="%.2f" fill="%s" '
                'stroke="%s" stroke-width="%.2f"%s/>' %
                (cx, cy, w / 2.0, h / 2.0, fill, stroke, sw, dash))
    if shape == "hexagon":
        import math
        points = " ".join("%.2f,%.2f" % (
            cx + (w / 2.0) * math.cos(math.radians(angle)),
            cy + (h / 2.0) * math.sin(math.radians(angle)))
            for angle in (0, 60, 120, 180, 240, 300))
        return ('<polygon points="%s" fill="%s" stroke="%s" stroke-width="%.2f"%s/>' %
                (points, fill, stroke, sw, dash))
    if shape == "diamond":
        points = "%.2f,%.2f %.2f,%.2f %.2f,%.2f %.2f,%.2f" % (
            cx, y, x + w, cy, cx, y + h, x, cy)
        return ('<polygon points="%s" fill="%s" stroke="%s" stroke-width="%.2f"%s/>' %
                (points, fill, stroke, sw, dash))
    radius = min(w, h) * 0.18 if shape == "rounded" else 0.0
    return ('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="%.2f" ry="%.2f" '
            'fill="%s" stroke="%s" stroke-width="%.2f"%s/>' %
            (x, y, w, h, radius, radius, fill, stroke, sw, dash))


def builtin_render(spec, italic_ids, font_family=None):
    width_mm = float(spec.get("width_mm", 180))
    height_mm = float(spec.get("height_mm", 120))
    width = width_mm * MM_TO_PX
    height = height_mm * MM_TO_PX
    font_family = font_family or spec.get("font_family") or "Liberation Sans"
    font_size = float(spec.get("font_size_pt", 8)) * PT_TO_PX
    direction = str(spec.get("direction", "LR")).upper()
    margin = max(12.0, font_size * 1.5)
    title = str(spec.get("title") or "")
    top = margin + (font_size * 2.0 if title else 0.0)
    legend = spec.get("legend") or []
    footnote = str(spec.get("footnote") or "")
    bottom = margin + (font_size * 2.3 if legend else 0.0) + (
        font_size * 1.8 if footnote else 0.0)
    usable_w = max(60.0, width - 2 * margin)
    usable_h = max(60.0, height - top - bottom)

    nodes = [dict(n) for n in spec.get("nodes") or []]
    edges = [dict(e) for e in spec.get("edges") or []]
    groups = spec.get("groups") or []
    by_id = {str(n["id"]): n for n in nodes}

    ranks = sorted({int(n.get("rank", 0)) for n in nodes})
    rank_index = {rank: index for index, rank in enumerate(ranks)}
    columns = max(1, len(ranks))
    col_w = usable_w / columns
    by_rank = {}
    for node in nodes:
        by_rank.setdefault(int(node.get("rank", 0)), []).append(node)
    for rank, members in by_rank.items():
        members.sort(key=lambda n: (int(n.get("order", 0)), str(n.get("id"))))
        rows = max(1, len(members))
        row_h = usable_h / rows
        for index, node in enumerate(members):
            node["_cx"] = margin + col_w * (rank_index[rank] + 0.5)
            node["_cy"] = top + row_h * (index + 0.5)
            label = str(node.get("label") or "")
            max_w = max(40.0, min(col_w - 12.0, float(node.get("max_text_px", 84))))
            lines = wrap_label(label, max_w, font_size)
            node["_lines"] = lines[:2]
            sublabel = str(node.get("sublabel") or "").strip()
            node["_sub_lines"] = (wrap_label(sublabel, max_w, font_size * 0.86)[:1]
                                  if sublabel else [])
            node["_w"] = min(max(46.0, max(text_width(line, font_size)
                                           for line in node["_lines"]) + 18.0), col_w - 8.0)
            node["_h"] = min(44.0 if node["_sub_lines"] else 32.0,
                             max(26.0, row_h - 10.0))

    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.1fmm" height="%.1fmm" '
             'viewBox="0 0 %.2f %.2f">' % (width_mm, height_mm, width, height)]
    parts.append('<defs>')
    for marker_id, color, inhibit in (("arrow", "#333333", False),
                                      ("arrow-blue", "#0072B2", False),
                                      ("arrow-gray", "#666666", False),
                                      ("arrow-inhibit", "#D55E00", True)):
        if inhibit:
            parts.append('<marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" '
                         'markerWidth="5" markerHeight="5" orient="auto">'
                         '<path d="M5,1 L5,9" stroke="%s" stroke-width="2" '
                         'fill="none"/></marker>' % (marker_id, color))
        else:
            parts.append('<marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" '
                         'markerWidth="6" markerHeight="6" orient="auto">'
                         '<path d="M1,1 L9,5 L1,9 z" fill="%s"/></marker>' %
                         (marker_id, color))
    parts.append('</defs>')
    parts.append('<rect x="0" y="0" width="%.2f" height="%.2f" fill="#ffffff"/>' %
                 (width, height))

    # Compartments first (z-order: background -> groups -> edges -> nodes).
    for group in groups:
        members = [by_id[str(mid)] for mid in group.get("nodes") or []
                   if str(mid) in by_id]
        if not members:
            continue
        pad = float(group.get("pad_px", font_size * 1.2))
        x0 = min(n["_cx"] - n["_w"] / 2.0 for n in members) - pad
        x1 = max(n["_cx"] + n["_w"] / 2.0 for n in members) + pad
        y0 = min(n["_cy"] - n["_h"] / 2.0 for n in members) - pad - (
            font_size * 1.15 if group.get("label") else 0.0)
        y1 = max(n["_cy"] + n["_h"] / 2.0 for n in members) + pad * 0.8
        dash = ' stroke-dasharray="6 4"' if group.get("dashed", True) else ""
        parts.append('<g id="%s" data-role="compartment">' % _attr(group.get("id")))
        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="%.2f" '
                     'fill="%s" fill-opacity="0.55" stroke="%s" stroke-width="0.9"%s/>' %
                     (x0, y0, x1 - x0, y1 - y0, font_size * 0.5,
                      _attr(group.get("fill") or "#f7f7f7"),
                      _attr(group.get("stroke") or "#bbbbbb"), dash))
        if group.get("label"):
            parts.append('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f" '
                         'fill="#555555" font-style="italic">%s</text>' %
                         (x0 + font_size * 0.55, y0 + font_size * 1.0,
                          _attr(font_family), font_size * 0.92,
                          escape(str(group["label"]))))
        parts.append('</g>')

    # Edges.
    for edge in edges:
        path, label_pos = _edge_path(edge, by_id, direction)
        if not path:
            continue
        kind = str(edge.get("kind") or "default")
        color, marker, line_width, dash = _edge_style(kind, bool(edge.get("dashed")))
        dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
        marker_attr = ' marker-end="url(#%s)"' % marker if marker else ""
        source = str(edge.get("source") or "not_provided")
        parts.append('<g id="%s" data-kind="%s" data-source="%s">' %
                     (_attr(edge.get("id")), _attr(kind), _attr(source)))
        parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" '
                     'stroke-linejoin="round"%s%s/>' %
                     (path, color, line_width, dash_attr, marker_attr))
        label = str(edge.get("label") or "")
        if label and label_pos:
            size = font_size * 0.92
            tw = text_width(label, size)
            lx, ly = label_pos
            parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                         'fill="#ffffff" fill-opacity="0.92" stroke="none"/>' %
                         (lx - tw / 2.0 - 2.0, ly - size * 0.78,
                          tw + 4.0, size * 1.15))
            parts.append('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f" '
                         'text-anchor="middle" fill="%s">%s</text>' %
                         (lx, ly + size * 0.12, _attr(font_family), size,
                          color, escape(label)))
        parts.append('</g>')

    # Nodes.
    for node in nodes:
        nid = str(node.get("id"))
        parts.append('<g id="%s" data-shape="%s" data-source="%s">' %
                     (_attr(nid), _attr(node.get("shape") or "rect"),
                      _attr(node.get("source") or "not_provided")))
        parts.append(_shape_markup(node, nid in italic_ids))
        inner = font_size * 0.96
        line_h = inner * 1.24
        total = len(node["_lines"]) * line_h + len(node["_sub_lines"]) * line_h * 0.9
        ty = node["_cy"] - total / 2.0 + inner * 0.86
        italic = ' font-style="italic"' if nid in italic_ids else ""
        for line in node["_lines"]:
            parts.append('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f" '
                         'text-anchor="middle" fill="#222222"%s>%s</text>' %
                         (node["_cx"], ty, _attr(font_family), inner, italic,
                          escape(line)))
            ty += line_h
        for line in node["_sub_lines"]:
            ty += line_h * 0.06
            parts.append('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f" '
                         'text-anchor="middle" fill="#444444" fill-opacity="0.88">%s</text>' %
                         (node["_cx"], ty, _attr(font_family), inner * 0.86, escape(line)))
            ty += line_h * 0.9
        parts.append('</g>')

    if title:
        parts.append('<g id="header" data-role="header"><text x="%.2f" y="%.2f" '
                     'font-family="%s" font-size="%.2f" font-weight="bold" '
                     'fill="#222222">%s</text></g>' %
                     (margin, margin + font_size * 1.25, _attr(font_family),
                      font_size * 1.1, escape(title)))

    if legend:
        size = font_size * 0.9
        y = height - margin - (font_size * 1.8 if footnote else 0.0)
        x = margin
        parts.append('<g id="legend" data-role="legend">')
        for item in legend:
            kind = str(item.get("kind") or "default")
            color, marker, line_width, dash = _edge_style(kind, False)
            dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
            marker_attr = ' marker-end="url(#%s)"' % marker if marker else ""
            parts.append('<path d="M%.2f,%.2f L%.2f,%.2f" fill="none" stroke="%s" '
                         'stroke-width="%.2f"%s%s/>' %
                         (x, y, x + size * 2.2, y, color, line_width,
                          dash_attr, marker_attr))
            label = str(item.get("label") or kind)
            parts.append('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f" '
                         'fill="#333333">%s</text>' %
                         (x + size * 2.7, y + size * 0.34, _attr(font_family),
                          size, escape(label)))
            x += size * 2.7 + text_width(label, size) + size * 2.0
            if x > width - margin - size * 4:
                x = margin
                y += size * 1.4
        parts.append('</g>')

    if footnote:
        parts.append('<g id="footnote" data-role="footnote"><text x="%.2f" y="%.2f" '
                     'font-family="%s" font-size="%.2f" fill="#666666">%s</text></g>' %
                     (margin, height - margin * 0.7, _attr(font_family),
                      font_size * 0.82, escape(footnote)))

    parts.append('</svg>')
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
def svg_stats(path):
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": "missing"}
    raw = p.read_text(encoding="utf-8", errors="replace")
    stats = {
        "ok": True,
        "path": str(p),
        "bytes": len(raw.encode("utf-8")),
        "text_elements": raw.count("<text"),
        "named_groups": raw.count("<g id="),
        "data_shapes": raw.count("data-shape="),
        "data_kinds": raw.count("data-kind="),
        "external_refs": (raw.lower().count('href="http') +
                          raw.lower().count("href='http")),
    }
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        stats["ok"] = False
        stats["error"] = "xml: %s" % exc
        return stats
    stats["viewbox"] = root.get("viewBox")
    group_ids = set()
    for elem in root.iter():
        if elem.tag.split("}")[-1] == "g" and elem.get("id"):
            group_ids.add(elem.get("id"))
    stats["group_ids"] = sorted(group_ids)
    return stats


def png_size(path):
    p = Path(path)
    if not p.is_file():
        return None
    raw = p.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) < 24:
        return None
    return [int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")]


def pdf_ok(path):
    p = Path(path)
    return p.is_file() and p.read_bytes()[:4] == b"%PDF"


def result_path(path, root):
    if not path:
        return ""
    if not root:
        return str(path)
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def build_content_map(data):
    items = []
    for comp in data.get("compartments") or []:
        items.append({
            "element_id": str(comp.get("id")),
            "element_type": "compartment",
            "label": str(comp.get("label") or ""),
            "kind": "context",
            "maps_to": source_dict(comp.get("source")),
        })
    for mol in data.get("molecules") or []:
        items.append({
            "element_id": str(mol.get("id")),
            "element_type": "molecule",
            "label": str(mol.get("label") or ""),
            "kind": "reported" if source_ok(mol.get("source")) else "unsourced",
            "maps_to": source_dict(mol.get("source")),
        })
    for edge in data.get("interactions") or []:
        items.append({
            "element_id": str(edge.get("id")),
            "element_type": "interaction",
            "label": "%s -> %s (%s)" % (edge.get("from"), edge.get("to"),
                                        edge.get("effect")),
            "kind": "inferred" if is_inferred_edge(edge) else "reported",
            "maps_to": source_dict(edge.get("source")),
        })
    return items


def build_compliance_checks(data, spec, stats, reported_sourced, all_sourced,
                            elements_sourced, status, export_artifact):
    group_ids = set(stats.get("group_ids") or [])
    molecule_ids = {str(m.get("id")) for m in data.get("molecules") or []}
    interaction_ids = {str(e.get("id")) for e in data.get("interactions") or []}
    legend_kinds = {str(item.get("kind")) for item in spec.get("legend") or []}
    edge_kinds = {str(edge.get("kind")) for edge in spec.get("edges") or []}
    inferred_dashed = all(
        bool(edge.get("dashed")) for edge in spec.get("edges") or []
        if edge.get("kind") == "inferred")
    return {
        "xml_valid": bool(stats.get("ok")),
        "text_as_text": stats.get("text_elements", 0) > 0,
        "no_external_refs": stats.get("external_refs", 0) == 0,
        "viewbox_present": bool(stats.get("viewbox")),
        "named_groups": stats.get("named_groups", 0),
        "node_groups": "%d/%d" % (len(molecule_ids & group_ids), len(molecule_ids)),
        "edge_groups": "%d/%d" % (len(interaction_ids & group_ids), len(interaction_ids)),
        "all_reported_edges_sourced": bool(reported_sourced),
        "all_interactions_sourced": bool(all_sourced),
        "all_elements_sourced": bool(elements_sourced),
        "legend_covers_edge_kinds": edge_kinds.issubset(legend_kinds),
        "inferred_edges_dashed": inferred_dashed,
        "data_encoding_declared": isinstance(data.get("data_encoding"), list),
        "export_artifact_present": bool(export_artifact),
        "status": status,
        "status_vocabulary": "media",
    }


def current_status(counts, reported_sourced, all_sourced, elements_sourced,
                   export_artifact, reviewed, stats):
    if not stats.get("ok"):
        return "input_error"
    if counts["reported"] == 0 and counts["inferred"] > 0 and not all_sourced:
        return "insufficient_evidence"
    if not reported_sourced or not all_sourced or not elements_sourced:
        return "partial"
    if not export_artifact:
        return "partial"
    if not reviewed:
        return "partial"
    return "answered"


def build_result(data, spec, svg_path, args, warnings, renderer, italic_done):
    counts = {
        "compartments": len(data["compartments"]),
        "molecules": len(data["molecules"]),
        "interactions": len(data["interactions"]),
        "reported": sum(1 for e in data["interactions"] if is_reported_edge(e)),
        "inferred": sum(1 for e in data["interactions"] if is_inferred_edge(e)),
        "inhibitions": sum(1 for e in data["interactions"]
                           if str(e.get("effect")) == "inhibit"),
        "translocations": sum(1 for e in data["interactions"]
                              if str(e.get("effect")) == "translocate"),
        "binds": sum(1 for e in data["interactions"]
                     if str(e.get("effect")) in ("bind", "association")),
        "modifications": sum(1 for e in data["interactions"]
                             if str(e.get("effect")) == "modify"),
    }
    reported_sourced = all(source_ok(e.get("source")) for e in data["interactions"]
                           if is_reported_edge(e))
    all_sourced = all(source_ok(e.get("source")) for e in data["interactions"])
    compartment_sourced = all(source_ok(c.get("source"))
                              for c in data["compartments"])
    molecule_sourced = all(source_ok(m.get("source")) for m in data["molecules"])
    elements_sourced = (compartment_sourced and molecule_sourced and all_sourced)
    disclaimers = list(data.get("disclaimers") or [])
    for edge in data["interactions"]:
        if not is_inferred_edge(edge):
            continue
        iid = str(edge["id"])
        if not any(iid in str(x) for x in disclaimers):
            disclaimers.append(
                "%s (%s to %s, %s) is inferred: the source records an association "
                "or an incomplete link, not a direct causal test." %
                (iid, edge.get("from"), edge.get("to"), edge.get("effect")))

    stats = svg_stats(svg_path)
    preview_size = png_size(args.preview) if args.preview else None
    pdf_valid = pdf_ok(args.pdf) if args.pdf else False

    artifacts = [{"role": "editable", "path": result_path(svg_path, args.root),
                  "format": "svg"}]
    export_artifact = None

    if args.preview:
        if preview_size:
            preview_artifact = {
                "role": "preview",
                "path": result_path(args.preview, args.root),
                "format": "png",
                "width_px": preview_size[0],
                "height_px": preview_size[1],
            }
            if args.dpi:
                preview_artifact["dpi"] = int(args.dpi)
            width_in = float(spec["width_mm"]) / 25.4
            if width_in > 0:
                preview_artifact["dpi_effective"] = round(preview_size[0] / width_in, 1)
            artifacts.append(preview_artifact)
            export_artifact = dict(preview_artifact)
            export_artifact["role"] = "export"
        else:
            warn(warnings, "preview is missing or not a valid PNG: %s" % args.preview)
    if args.pdf:
        if pdf_valid:
            pdf_artifact = {"role": "export",
                            "path": result_path(args.pdf, args.root),
                            "format": "pdf"}
            artifacts.append(pdf_artifact)
            export_artifact = dict(pdf_artifact)
        else:
            warn(warnings, "PDF export is missing or has no %PDF header: %s" % args.pdf)

    status = current_status(counts, reported_sourced, all_sourced, elements_sourced,
                            export_artifact, args.reviewed, stats)
    task_status = {"answered": "completed", "partial": "partial",
                   "insufficient_evidence": "partial",
                   "input_error": "input_error"}.get(status, "partial")
    exceptions = []
    if not elements_sourced:
        missing = [str(c.get("id")) for c in data["compartments"]
                   if not source_ok(c.get("source"))]
        missing += [str(m.get("id")) for m in data["molecules"]
                    if not source_ok(m.get("source"))]
        missing += [str(e.get("id")) for e in data["interactions"]
                    if not source_ok(e.get("source"))]
        exceptions.append({"code": "missing_source",
                           "detail": "elements without a usable source: %s" %
                                     ", ".join(missing)})
    if not export_artifact:
        exceptions.append({"code": "export_pending",
                           "detail": "no real PDF/PNG was produced yet; run "
                                     "export_pathway.py or keep status=partial"})

    compliance = build_compliance_checks(data, spec, stats, reported_sourced,
                                         all_sourced, elements_sourced, status,
                                         export_artifact)
    content_map = build_content_map(data)
    uncertainty = list(data.get("uncertainty") or [])
    for item in warnings:
        uncertainty.append("converter warning: %s" % item)

    result = {
        "case_id": str(data.get("case_id") or "unknown"),
        "task_family": "pathway",
        "workorder": "biological_mechanism_pathway",
        "status": status,
        "task_status": task_status,
        "figure": {
            "figure_id": spec["figure_id"],
            "compartments": counts["compartments"],
            "molecules": counts["molecules"],
            "interactions": counts["interactions"],
            "reported": counts["reported"],
            "inferred": counts["inferred"],
            "canvas_mm": [spec["width_mm"], spec["height_mm"]],
            "layout_type": spec["direction"],
        },
        "artifacts": artifacts,
        "pathway": {
            "compartments": data["compartments"],
            "molecules": data["molecules"],
            "interactions": data["interactions"],
        },
        "semantic_check": {
            "arrows_reported": counts["reported"],
            "arrows_inferred": counts["inferred"],
            "inhibitions_total": counts["inhibitions"],
            "inhibitions_verified": counts["inhibitions"] if args.reviewed else 0,
            "removed_unsupported": list(data.get("removed_unsupported") or []),
            "all_effects_sourced": reported_sourced,
            "all_elements_sourced": elements_sourced,
            "reviewed": bool(args.reviewed),
            "counts": counts,
        },
        "legend": (data.get("legend") or {}),
        "data_encoding": list(data.get("data_encoding") or []),
        "omitted": list(data.get("omitted") or []),
        "conflicts": list(data.get("conflicts") or []),
        "schematic_disclaimers": disclaimers,
        "checks": {
            "xml_valid": bool(stats.get("ok")),
            "text_as_text": stats.get("text_elements", 0) > 0,
            "external_refs": stats.get("external_refs", 0),
            "named_groups": stats.get("named_groups", 0),
            "node_count": counts["molecules"],
            "edge_count": counts["interactions"],
            "preview_present": bool(preview_size),
            "export_present": bool(export_artifact),
            "escape_checked": bool(stats.get("ok")),
            "renderer": renderer,
            "italic_nodes": list(italic_done or []),
            "z_order": ["background", "groups", "edges", "nodes", "header",
                        "legend", "footnote"],
            "official_fields": True,
        },
        "support_files_used": ["scripts/pathway_to_spec.py"] + (
            ["scripts/make_schematic.py"] if renderer == "make_schematic" else []),
        "uncertainty": uncertainty,
        "change_log": [{
            "action": "created",
            "tool": "pathway_to_spec.py",
            "renderer": renderer,
            "input": str(args.pathway),
            "output": [str(args.out_spec), str(svg_path)],
        }],
        "editable_source": {"path": result_path(svg_path, args.root), "format": "svg"},
        "export_artifact": export_artifact,
        "content_map": content_map,
        "compliance_checks": compliance,
        "exceptions": exceptions,
    }
    return result, counts, disclaimers


def write_input_error_result(args, case_id, errors):
    if not args.result_out:
        return
    result = {
        "case_id": case_id or "unknown",
        "task_family": "pathway",
        "workorder": "biological_mechanism_pathway",
        "status": "input_error",
        "task_status": "input_error",
        "figure": {},
        "artifacts": [],
        "pathway": {"compartments": [], "molecules": [], "interactions": []},
        "semantic_check": {
            "arrows_reported": 0, "arrows_inferred": 0,
            "inhibitions_total": 0, "inhibitions_verified": 0,
            "removed_unsupported": [], "all_effects_sourced": False,
            "reviewed": False, "counts": {},
        },
        "legend": {},
        "data_encoding": [],
        "omitted": [],
        "conflicts": [],
        "schematic_disclaimers": [],
        "checks": {"xml_valid": False, "official_fields": True},
        "support_files_used": ["scripts/pathway_to_spec.py"],
        "uncertainty": list(errors),
        "change_log": [{"action": "rejected", "tool": "pathway_to_spec.py",
                        "detail": "; ".join(errors)}],
        "editable_source": None,
        "export_artifact": None,
        "content_map": [],
        "compliance_checks": {"xml_valid": False, "status": "input_error",
                              "status_vocabulary": "media"},
        "exceptions": [{"code": "input_error", "detail": "; ".join(errors)}],
    }
    out = Path(args.result_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Pathway entity table -> editable SVG")
    ap.add_argument("--pathway", required=True)
    ap.add_argument("--out-spec", required=True)
    ap.add_argument("--out-svg", default=None)
    ap.add_argument("--result-out", default=None)
    ap.add_argument("--preview", default=None,
                    help="existing PNG preview to record; never rendered here")
    ap.add_argument("--pdf", default=None,
                    help="existing PDF export to record; never rendered here")
    ap.add_argument("--root", default=None,
                    help="output root; artifact paths are written relative to it")
    ap.add_argument("--dpi", type=int, default=None,
                    help="declared export resolution recorded in the preview artifact")
    ap.add_argument("--renderer", choices=("auto", "make_schematic", "builtin"),
                    default="auto")
    ap.add_argument("--reviewed", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pathway_path = Path(args.pathway)
    if not pathway_path.is_file():
        errors = ["missing pathway file: %s" % pathway_path]
        write_input_error_result(args, "unknown", errors)
        print(json.dumps({"ok": False, "status": "input_error", "errors": errors},
                         ensure_ascii=False))
        return 2
    try:
        data = load_json(pathway_path)
    except (OSError, json.JSONDecodeError) as exc:
        errors = ["cannot parse pathway: %s" % exc]
        write_input_error_result(args, "unknown", errors)
        print(json.dumps({"ok": False, "status": "input_error", "errors": errors},
                         ensure_ascii=False))
        return 2
    if not isinstance(data, dict):
        errors = ["pathway root is not an object"]
        write_input_error_result(args, "unknown", errors)
        print(json.dumps({"ok": False, "status": "input_error", "errors": errors},
                         ensure_ascii=False))
        return 2

    errors, warnings = validate_table(data)
    if errors:
        write_input_error_result(args, str(data.get("case_id") or "unknown"), errors)
        print(json.dumps({"ok": False, "status": "input_error", "errors": errors,
                          "warnings": warnings}, ensure_ascii=False, indent=2))
        return 2

    spec, italic_ids = build_spec(data, warnings)
    out_spec = Path(args.out_spec)
    out_spec.parent.mkdir(parents=True, exist_ok=True)
    out_spec.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "ok": True,
        "status": "generated",
        "pathway": str(pathway_path),
        "spec": str(out_spec),
        "counts": {
            "compartments": len(data.get("compartments") or []),
            "molecules": len(spec["nodes"]),
            "interactions": len(spec["edges"]),
        },
        "warnings": warnings,
        "errors": [],
    }

    if args.out_svg:
        out_svg = Path(args.out_svg)
        out_svg.parent.mkdir(parents=True, exist_ok=True)
        renderer = "builtin"
        italic_done = []
        helper = find_make_schematic(Path(__file__).resolve().parent)
        if args.renderer in ("auto", "make_schematic") and helper is not None:
            proc = subprocess.run(
                [sys.executable, str(helper), "--spec", str(out_spec),
                 "--out", str(out_svg), "--json"],
                capture_output=True, text=True, encoding="utf-8")
            if proc.returncode == 0:
                stats = svg_stats(out_svg)
                if (stats.get("ok") and stats.get("text_elements", 0) > 0 and
                        stats.get("data_shapes", 0) > 0 and
                        stats.get("data_kinds", 0) > 0):
                    renderer = "make_schematic"
                else:
                    warn(warnings, "make_schematic produced an unusable SVG "
                                   "(parse/text/group check failed); falling back "
                                   "to the built-in renderer")
            else:
                warn(warnings, "make_schematic failed (%s); falling back to the "
                               "built-in renderer" %
                               (proc.stderr or proc.stdout or "unknown error").strip()[:200])
        elif args.renderer == "make_schematic":
            warn(warnings, "make_schematic.py not found; using the built-in renderer")
        if renderer == "make_schematic":
            italic_done = apply_gene_italic(out_svg, italic_ids)
        else:
            try:
                svg_text = builtin_render(spec, set(italic_ids))
                out_svg.write_text(svg_text, encoding="utf-8")
                italic_done = list(italic_ids)
            except Exception as exc:  # never leak a traceback
                print(json.dumps({"ok": False, "status": "render_error",
                                  "errors": ["built-in renderer failed: %s: %s" %
                                             (type(exc).__name__, exc)],
                                  "warnings": warnings}, ensure_ascii=False, indent=2))
                return 2
        stats = svg_stats(out_svg)
        report["svg"] = stats
        report["renderer"] = renderer
        report["postprocess"] = {"italic_nodes": italic_done}
        if not stats.get("ok"):
            print(json.dumps({"ok": False, "status": "render_error",
                              "errors": ["generated SVG is not parseable: %s" %
                                         stats.get("error")],
                              "warnings": warnings}, ensure_ascii=False, indent=2))
            return 2

        if args.result_out:
            result, counts, disclaimers = build_result(
                data, spec, out_svg, args, warnings, renderer, italic_done)
            out_result = Path(args.result_out)
            out_result.parent.mkdir(parents=True, exist_ok=True)
            out_result.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
            report["result"] = str(out_result)
            report["result_status"] = result["status"]
            report["task_status"] = result["task_status"]
            report["semantic_counts"] = counts
            report["disclaimers"] = disclaimers
            report["exceptions"] = result["exceptions"]

    report["warnings"] = warnings
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never leak a traceback to the harness
        print(json.dumps({"ok": False, "status": "internal_error",
                          "errors": ["%s: %s" % (type(exc).__name__, exc)]},
                         ensure_ascii=False, indent=2))
        sys.exit(2)
