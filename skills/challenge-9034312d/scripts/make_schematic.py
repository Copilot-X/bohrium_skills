#!/usr/bin/env python3
"""Deterministic schematic / pathway / apparatus / route diagram builder (stdlib only).

Nodes carry an explicit source; edges carry a semantic kind so activation and
inhibition are never confused. Output is editable SVG: every node and edge is
its own named group, all text stays <text>.

Usage:
    python3 make_schematic.py --spec schematic.json --out fig1.svg
    python3 make_schematic.py --check fig1.svg --json

Spec sketch:
{
  "figure_id": "Fig.1", "width_mm": 180, "height_mm": 90,
  "direction": "LR", "layout": "layered",
  "groups": [{"id": "g1", "label": "Cytosol", "nodes": ["n1","n2"]}],
  "nodes": [{"id": "n1", "label": "Catalyst A", "sublabel": "20 wt%",
             "shape": "rounded", "rank": 0, "order": 0,
             "fill": "#0072B2", "text_color": "#ffffff",
             "source": "materials/method.md §2.1"}],
  "edges": [{"id": "e1", "from": "n1", "to": "n2", "kind": "activate",
             "label": "+", "route": "orthogonal", "source": "p.2"}],
  "legend": [{"kind": "activate", "label": "activation"}]
}
"""

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

MM_TO_PX = 96.0 / 25.4
PT_TO_PX = 96.0 / 72.0

DEFAULT_FILL = "#ffffff"
DEFAULT_STROKE = "#333333"
ACCENT = "#0072B2"
EDGE_STYLE = {
    "activate":    {"color": "#0072B2", "marker": "arrow", "dash": None, "width": 1.4},
    "inhibit":     {"color": "#D55E00", "marker": "bar", "dash": None, "width": 1.4},
    "flow":        {"color": "#333333", "marker": "arrow", "dash": None, "width": 2.0},
    "translocate": {"color": "#333333", "marker": "arrow", "dash": "5 3", "width": 1.4},
    "bind":        {"color": "#666666", "marker": "none", "dash": None, "width": 1.2},
    "inferred":    {"color": "#666666", "marker": "arrow", "dash": "5 3", "width": 1.2},
    "default":     {"color": "#333333", "marker": "arrow", "dash": None, "width": 1.3},
}


def text_width(s, size_px):
    w = 0.0
    for ch in s:
        if ord(ch) > 0x2E80:
            w += size_px
        elif ch in "il.,:;'|!()[]":
            w += size_px * 0.34
        elif ch in "mwMW@%":
            w += size_px * 0.95
        else:
            w += size_px * 0.56
    return w


def wrap_label(label, max_width, size_px):
    """Greedy wrap that also breaks inside CJK runs."""
    tokens, buf = [], ""
    for ch in label:
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
        cand = cur + tok
        if text_width(cand, size_px) <= max_width or not cur:
            cur = cand
        else:
            lines.append(cur.rstrip())
            cur = "" if tok == " " else tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


class Diagram:
    def __init__(self, spec):
        self.spec = spec
        self.fig_id = spec.get("figure_id", "figure")
        self.width_mm = float(spec.get("width_mm", 180))
        self.height_mm = float(spec.get("height_mm", 90))
        self.W = self.width_mm * MM_TO_PX
        self.H = self.height_mm * MM_TO_PX
        self.ff = spec.get("font_family", "Liberation Sans")
        self.fs = float(spec.get("font_size_pt", 8)) * PT_TO_PX
        self.direction = str(spec.get("direction", "LR")).upper()
        self.layout = spec.get("layout", "layered")
        self.margin = float(spec.get("margin_px", max(10.0, self.fs * 1.6)))
        self.nodes = [dict(n) for n in spec.get("nodes", [])]
        self.edges = [dict(e) for e in spec.get("edges", [])]
        self.groups = spec.get("groups", []) or []
        self.by_id = {n["id"]: n for n in self.nodes}
        self.title = spec.get("title")
        self.footnote = spec.get("footnote")
        self.out = []

    # ------------------------------------------------------------------
    def measure(self):
        for n in self.nodes:
            shape = n.get("shape", "rect")
            label = str(n.get("label", n["id"]))
            sub = n.get("sublabel")
            inner = float(n.get("font_scale", 1.0)) * self.fs
            max_w = float(n.get("max_text_px", 0)) or min(self.W * 0.28, 150.0)
            lines = wrap_label(label, max_w, inner)
            sub_lines = wrap_label(str(sub), max_w, inner * 0.86) if sub else []
            n["_lines"] = lines
            n["_sub_lines"] = sub_lines
            if n.get("w"):
                n["_w"] = float(n["w"])
            else:
                tw = max([text_width(x, inner) for x in lines] +
                         [text_width(x, inner * 0.86) for x in sub_lines] + [0])
                pad_x = inner * 1.1
                n["_w"] = max(30.0, tw + 2 * pad_x)
            if n.get("h"):
                n["_h"] = float(n["h"])
            else:
                line_h = inner * 1.24
                n["_h"] = max(18.0, len(lines) * line_h + len(sub_lines) * line_h * 0.9 + inner * 1.1)
            if shape == "circle":
                d = max(n["_w"], n["_h"])
                n["_w"] = n["_h"] = d

    # ------------------------------------------------------------------
    def assign_ranks(self):
        if self.layout == "free":
            units = self.spec.get("free_units", "px")
            scale = MM_TO_PX if units == "mm" else 1.0
            for n in self.nodes:
                n["_cx"] = float(n.get("x", self.W / 2)) * scale
                n["_cy"] = float(n.get("y", self.H / 2)) * scale
            return

        ids = [n["id"] for n in self.nodes]
        has_rank = any("rank" in n for n in self.nodes)
        if not has_rank:
            rank = {i: 0 for i in ids}
            for _ in range(len(ids)):
                changed = False
                for e in self.edges:
                    a, b = e.get("from"), e.get("to")
                    if a not in rank or b not in rank:
                        continue
                    if rank[b] < rank[a] + 1 and rank[a] + 1 < len(ids):
                        rank[b] = rank[a] + 1
                        changed = True
                if not changed:
                    break
            for n in self.nodes:
                n["rank"] = rank.get(n["id"], 0)

        ranks = sorted({int(n.get("rank", 0)) for n in self.nodes})
        for n in self.nodes:
            n["rank"] = int(n.get("rank", 0))
        order_counter = {}
        for n in self.nodes:
            r = n["rank"]
            if "order" not in n:
                n["order"] = order_counter.get(r, 0)
                order_counter[r] = n["order"] + 1
            else:
                n["order"] = int(n["order"])
                order_counter[r] = max(order_counter.get(r, 0), n["order"] + 1)
        self.ranks = ranks

    def place_layered(self):
        ranks = self.ranks
        n_rank = len(ranks)
        top = self.margin + (self.fs * 2.0 if self.title else 0)
        bottom = self.H - self.margin - (self.fs * 1.8 if self.footnote else 0)
        cols = {}
        for n in self.nodes:
            cols.setdefault(n["rank"], []).append(n)

        if self.direction == "LR":
            widths = [max([n["_w"] for n in cols[r]] + [1]) for r in ranks]
            total_w = sum(widths)
            gap = (self.W - 2 * self.margin - total_w) / max(1, n_rank - 1) if n_rank > 1 else 0
            gap = max(gap, self.fs * 2.6)
            xs, cursor = {}, self.margin
            for r, w in zip(ranks, widths):
                xs[r] = cursor + w / 2
                cursor += w + gap
            avail_h = max(1.0, bottom - top - max([n["_h"] for n in self.nodes] + [1]))
            for r in ranks:
                items = sorted(cols[r], key=lambda n: n["order"])
                m = len(items)
                step = avail_h / max(1, m) if m > 1 else 0
                for k, n in enumerate(items):
                    n["_cx"] = xs[r]
                    n["_cy"] = (top + bottom) / 2 if m == 1 else top + step * (k + 0.5)
        else:
            heights = [max([n["_h"] for n in cols[r]] + [1]) for r in ranks]
            total_h = sum(heights)
            gap = (bottom - top - total_h) / max(1, n_rank - 1) if n_rank > 1 else 0
            gap = max(gap, self.fs * 2.2)
            ys, cursor = {}, top
            for r, h in zip(ranks, heights):
                ys[r] = cursor + h / 2
                cursor += h + gap
            for r in ranks:
                items = sorted(cols[r], key=lambda n: n["order"])
                m = len(items)
                step = (self.W - 2 * self.margin) / max(1, m)
                for k, n in enumerate(items):
                    n["_cx"] = self.margin + step * (k + 0.5)
                    n["_cy"] = ys[r]

    def place_grid(self):
        n = len(self.nodes)
        cols = int(self.spec.get("layout_cols", 0)) or max(1, math.ceil(math.sqrt(n)))
        rows = math.ceil(n / cols)
        top = self.margin + (self.fs * 2.0 if self.title else 0)
        bottom = self.H - self.margin - (self.fs * 1.8 if self.footnote else 0)
        cell_w = (self.W - 2 * self.margin) / cols
        cell_h = (bottom - top) / rows
        for i, node in enumerate(self.nodes):
            r, c = divmod(i, cols)
            node["_cx"] = self.margin + cell_w * (c + 0.5)
            node["_cy"] = top + cell_h * (r + 0.5)

    # ------------------------------------------------------------------
    def svg_defs(self):
        defs = ['<defs>']
        for name, color in (("arrow", "#333333"), ("arrow-blue", "#0072B2"),
                            ("arrow-inhibit", "#D55E00"), ("arrow-gray", "#666666")):
            if name == "arrow-inhibit":
                defs.append(
                    f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" '
                    f'markerHeight="5" orient="auto">'
                    f'<path d="M5,1 L5,9" stroke="{color}" stroke-width="2" fill="none"/></marker>')
            else:
                defs.append(
                    f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
                    f'markerHeight="6" orient="auto">'
                    f'<path d="M1,1 L9,5 L1,9 z" fill="{color}"/></marker>')
        defs.append('</defs>')
        return "".join(defs)

    # ------------------------------------------------------------------
    def node_anchor(self, node, tx, ty):
        cx, cy = node["_cx"], node["_cy"]
        dx, dy = tx - cx, ty - cy
        if dx == 0 and dy == 0:
            return cx, cy
        shape = node.get("shape", "rect")
        if shape == "ellipse":
            a, b = node["_w"] / 2, node["_h"] / 2
            t = 1.0 / math.sqrt((dx * dx) / (a * a) + (dy * dy) / (b * b))
            return cx + dx * t, cy + dy * t
        a, b = node["_w"] / 2, node["_h"] / 2
        tx_ = a / abs(dx) if dx else float("inf")
        ty_ = b / abs(dy) if dy else float("inf")
        t = min(tx_, ty_)
        return cx + dx * t, cy + dy * t

    def draw_node(self, n):
        cx, cy = n["_cx"], n["_cy"]
        w, h = n["_w"], n["_h"]
        x, y = cx - w / 2, cy - h / 2
        fill = n.get("fill", DEFAULT_FILL)
        stroke = n.get("stroke", DEFAULT_STROKE)
        sw = float(n.get("stroke_width", 1.1))
        dash = ' stroke-dasharray="5 3"' if n.get("dashed") else ""
        shape = n.get("shape", "rect")
        self.out.append(f'<g id="{escape(str(n["id"]))}" data-shape="{escape(shape)}" '
                        f'data-source="{escape(str(n.get("source", "")))}">')
        if shape == "ellipse":
            self.out.append(
                f'<ellipse cx="{cx:.2f}" cy="{cy:.2f}" rx="{w / 2:.2f}" ry="{h / 2:.2f}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        elif shape == "circle":
            self.out.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{w / 2:.2f}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        elif shape == "hexagon":
            pts = " ".join(f"{cx + (w / 2) * math.cos(math.radians(a)):.2f},{cy + (h / 2) * math.sin(math.radians(a)):.2f}"
                           for a in (0, 60, 120, 180, 240, 300))
            self.out.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" '
                            f'stroke-width="{sw}"{dash}/>')
        elif shape == "diamond":
            pts = f"{cx:.2f},{y:.2f} {x + w:.2f},{cy:.2f} {cx:.2f},{y + h:.2f} {x:.2f},{cy:.2f}"
            self.out.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" '
                            f'stroke-width="{sw}"{dash}/>')
        else:
            r = min(w, h) * 0.18 if shape == "rounded" else 0
            self.out.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
                f'rx="{r:.2f}" ry="{r:.2f}" fill="{fill}" stroke="{stroke}" '
                f'stroke-width="{sw}"{dash}/>')

        inner = float(n.get("font_scale", 1.0)) * self.fs
        color = n.get("text_color", "#222222")
        line_h = inner * 1.24
        total = len(n["_lines"]) * line_h + len(n["_sub_lines"]) * line_h * 0.9
        ty = cy - total / 2 + inner * 0.86
        for line in n["_lines"]:
            self.out.append(
                f'<text x="{cx:.2f}" y="{ty:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{inner:.2f}" text-anchor="middle" fill="{color}" '
                f'font-weight="{"bold" if n.get("bold") else "normal"}">{escape(line)}</text>')
            ty += line_h
        for line in n["_sub_lines"]:
            ty += line_h * 0.06
            self.out.append(
                f'<text x="{cx:.2f}" y="{ty:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{inner * 0.86:.2f}" text-anchor="middle" fill="{color}" '
                f'fill-opacity="0.88">{escape(line)}</text>')
            ty += line_h * 0.9
        self.out.append('</g>')

    # ------------------------------------------------------------------
    def edge_geometry(self, e):
        a = self.by_id.get(e.get("from"))
        b = self.by_id.get(e.get("to"))
        if not a or not b:
            return None
        x1, y1 = self.node_anchor(a, b["_cx"], b["_cy"])
        x2, y2 = self.node_anchor(b, a["_cx"], a["_cy"])
        route = e.get("route", "straight")
        pts = [(x1, y1)]
        if route == "orthogonal":
            if self.direction == "LR" and abs(x2 - x1) >= abs(y2 - y1):
                mid = (x1 + x2) / 2
                pts += [(mid, y1), (mid, y2), (x2, y2)]
            elif self.direction == "TB" and abs(y2 - y1) > abs(x2 - x1):
                mid = (y1 + y2) / 2
                pts += [(x1, mid), (x2, mid), (x2, y2)]
            else:
                mid = (x1 + x2) / 2
                pts += [(mid, y1), (mid, y2), (x2, y2)]
        elif route == "curve":
            mx = (x1 + x2) / 2
            return f"M{x1:.2f},{y1:.2f} C{mx:.2f},{y1:.2f} {mx:.2f},{y2:.2f} {x2:.2f},{y2:.2f}", \
                   ((x1 + x2) / 2, (y1 + y2) / 2)
        return "M" + " L".join(f"{px:.2f},{py:.2f}" for px, py in pts), \
               (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    def draw_edge(self, e):
        geo = self.edge_geometry(e)
        if not geo:
            return
        d, (lx, ly) = geo
        kind = e.get("kind", "default")
        style = dict(EDGE_STYLE.get(kind, EDGE_STYLE["default"]))
        if e.get("color"):
            style["color"] = e["color"]
        if e.get("dashed"):
            style["dash"] = "5 3"
        marker = style["marker"]
        marker_id = {"arrow": "arrow", "bar": "arrow-inhibit", "none": None}.get(marker)
        if kind == "activate":
            marker_id = "arrow-blue"
        if kind == "inferred":
            marker_id = "arrow-gray"
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
        mk = f' marker-end="url(#{marker_id})"' if marker_id else ""
        self.out.append(f'<g id="{escape(str(e.get("id", "edge")))}" data-kind="{escape(kind)}" '
                        f'data-source="{escape(str(e.get("source", "")))}">')
        self.out.append(f'<path d="{d}" fill="none" stroke="{style["color"]}" '
                        f'stroke-width="{style["width"]}" stroke-linejoin="round"{dash}{mk}/>')
        label = e.get("label")
        if label:
            size = self.fs * 0.92
            tw = text_width(str(label), size)
            self.out.append(
                f'<rect x="{lx - tw / 2 - 2:.2f}" y="{ly - size * 0.78:.2f}" '
                f'width="{tw + 4:.2f}" height="{size * 1.15:.2f}" fill="#ffffff" '
                f'fill-opacity="0.92" stroke="none"/>')
            self.out.append(
                f'<text x="{lx:.2f}" y="{ly + size * 0.12:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{size:.2f}" text-anchor="middle" fill="{style["color"]}">'
                f'{escape(str(label))}</text>')
        self.out.append('</g>')

    # ------------------------------------------------------------------
    def draw_groups(self):
        for g in self.groups:
            members = [self.by_id[i] for i in g.get("nodes", []) if i in self.by_id]
            if not members:
                continue
            pad = float(g.get("pad_px", self.fs * 1.4))
            x0 = min(n["_cx"] - n["_w"] / 2 for n in members) - pad
            x1 = max(n["_cx"] + n["_w"] / 2 for n in members) + pad
            y0 = min(n["_cy"] - n["_h"] / 2 for n in members) - pad - (self.fs * 1.2 if g.get("label") else 0)
            y1 = max(n["_cy"] + n["_h"] / 2 for n in members) + pad * 0.8
            fill = g.get("fill", "#f7f7f7")
            stroke = g.get("stroke", "#bbbbbb")
            dash = ' stroke-dasharray="6 4"' if g.get("dashed", True) else ""
            self.out.append(f'<g id="{escape(str(g.get("id", "group")))}" data-role="compartment">')
            self.out.append(
                f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{x1 - x0:.2f}" height="{y1 - y0:.2f}" '
                f'rx="{self.fs * 0.5:.2f}" fill="{fill}" fill-opacity="0.7" stroke="{stroke}" '
                f'stroke-width="0.9"{dash}/>')
            if g.get("label"):
                self.out.append(
                    f'<text x="{x0 + self.fs * 0.6:.2f}" y="{y0 + self.fs * 1.05:.2f}" '
                    f'font-family="{escape(self.ff)}" font-size="{self.fs * 0.92:.2f}" '
                    f'fill="#555555" font-style="italic">{escape(str(g["label"]))}</text>')
            self.out.append('</g>')

    def draw_legend(self):
        items = self.spec.get("legend") or []
        if not items:
            return
        fs = self.fs * 0.92
        y = self.H - self.margin + fs * 0.2
        x = self.margin
        self.out.append('<g id="legend" data-role="legend">')
        for item in items:
            kind = item.get("kind", "default")
            style = EDGE_STYLE.get(kind, EDGE_STYLE["default"])
            dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] else ""
            marker_id = {"arrow": "arrow", "bar": "arrow-inhibit", "none": None}.get(style["marker"])
            mk = f' marker-end="url(#{marker_id})"' if marker_id else ""
            self.out.append(f'<path d="M{x:.2f},{y:.2f} L{x + fs * 2.2:.2f},{y:.2f}" fill="none" '
                            f'stroke="{style["color"]}" stroke-width="{style["width"]}"{dash}{mk}/>')
            self.out.append(
                f'<text x="{x + fs * 2.7:.2f}" y="{y + fs * 0.34:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{fs:.2f}" fill="#333333">{escape(str(item.get("label", kind)))}</text>')
            x += fs * 2.7 + text_width(str(item.get("label", kind)), fs) + fs * 2.0
        self.out.append('</g>')

    def draw_header(self):
        if self.title:
            self.out.append(
                f'<text x="{self.margin:.2f}" y="{self.margin + self.fs * 1.2:.2f}" '
                f'font-family="{escape(self.ff)}" font-size="{self.fs * 1.1:.2f}" '
                f'font-weight="bold" fill="#222222">{escape(str(self.title))}</text>')

    def draw_footnote(self):
        notes = []
        if self.footnote:
            notes.append(str(self.footnote))
        dashed = [n for n in self.nodes if n.get("dashed")]
        if dashed:
            notes.append("虚线框为推断/示意元素，材料未直接给出")
        if not notes:
            return
        text = "  ".join(notes)
        self.out.append(
            f'<text x="{self.margin:.2f}" y="{self.H - self.margin * 0.55:.2f}" '
            f'font-family="{escape(self.ff)}" font-size="{self.fs * 0.82:.2f}" '
            f'fill="#666666">{escape(text)}</text>')

    # ------------------------------------------------------------------
    def render(self):
        self.measure()
        self.assign_ranks()
        if self.layout == "free":
            pass
        elif self.layout == "grid":
            self.place_grid()
        else:
            self.place_layered()

        self.draw_groups()
        for e in self.edges:
            self.draw_edge(e)
        for n in self.nodes:
            self.draw_node(n)
        self.draw_header()
        self.draw_legend()
        self.draw_footnote()

        svg = (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width_mm:.1f}mm" '
            f'height="{self.height_mm:.1f}mm" viewBox="0 0 {self.W:.2f} {self.H:.2f}">\n'
            f'<title>{escape(str(self.fig_id))}</title>\n'
            f'<rect x="0" y="0" width="{self.W:.2f}" height="{self.H:.2f}" fill="#ffffff"/>\n'
            + self.svg_defs() + "\n" + "".join(self.out) + "\n</svg>\n"
        )
        return svg


def build(spec, out_path):
    svg = Diagram(spec).render()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")
    return {
        "ok": True,
        "path": str(out),
        "bytes": len(svg.encode("utf-8")),
        "nodes": svg.count('data-shape='),
        "edges": svg.count('data-kind='),
        "text_elements": svg.count("<text"),
        "named_groups": svg.count("<g id="),
        "width_mm": float(spec.get("width_mm", 180)),
        "height_mm": float(spec.get("height_mm", 90)),
    }


def check(path):
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "missing"}
    raw = p.read_text(encoding="utf-8")
    out = {"ok": True, "path": str(p), "text_elements": raw.count("<text"),
           "named_groups": raw.count("<g id=")}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return {"ok": False, "error": f"xml: {exc}"}
    if not root.tag.endswith("svg"):
        out["ok"] = False
        out["error"] = "root is not <svg>"
    if "viewBox" not in root.attrib:
        out["ok"] = False
        out["error"] = "missing viewBox"
    if out["text_elements"] == 0:
        out["ok"] = False
        out["error"] = "no <text> elements"
    return out


def main():
    ap = argparse.ArgumentParser(description="JSON spec -> editable SVG schematic")
    ap.add_argument("--spec")
    ap.add_argument("--out")
    ap.add_argument("--check")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.check:
        res = check(args.check)
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json else res)
        sys.exit(0 if res.get("ok") else 1)

    if not args.spec or not args.out:
        ap.error("--spec and --out are required")
    with open(args.spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    res = build(spec, args.out)
    print(json.dumps(res, ensure_ascii=False, indent=2) if args.json else res)


if __name__ == "__main__":
    main()
