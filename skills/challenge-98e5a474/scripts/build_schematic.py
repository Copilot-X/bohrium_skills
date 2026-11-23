#!/usr/bin/env python3
"""Build a self-contained, editable SVG schematic from an entity/relation table.

Usage:
    python3 build_schematic.py --entities /app/work/entities.json \
        --out /app/output/figures/fig1.svg --json
    python3 build_schematic.py --entities table.json --spec spec.json \
        --out fig1.svg --json

Input is one JSON object with `entities` and `relations` plus optional layout
keys (`canvas_mm`, `direction`, `layout`, `title`, `footnote`, `groups`,
`legend`, `font_family`, `font_size_pt`).  `nodes`/`edges` are accepted as
aliases for `entities`/`relations`.

Every node becomes <g id="E1"> with its shape and <text> labels; every
relation becomes <g id="R1"> with a path, a marker and an optional label.
All labels stay <text>; the SVG references no external file.  This script is
an accelerator: a malformed table returns a structured JSON error instead of a
traceback, and the caller may always hand-write the SVG instead.

Exit codes: 0 = SVG written (warnings possible), 2 = invalid input.
Stdlib only, Python 3.12+.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

MM_TO_PX = 96.0 / 25.4
PT_TO_PX = 96.0 / 72.0

DEFAULT_FONT = "Liberation Sans"
ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")

SEMANTICS = {"flow", "activate", "inhibit", "convert", "contain",
             "bind", "translocate", "inferred", "default"}
SHAPES = {"rect", "rounded", "ellipse", "circle", "hexagon", "diamond"}
TYPE_SHAPE = {"process": "rounded", "state": "rounded", "decision": "diamond",
              "signal": "hexagon", "component": "rect", "compartment": "rect"}

EDGE_STYLE = {
    "activate":    {"color": "#0072B2", "marker": "arrow-blue", "dash": None, "width": 1.5},
    "inhibit":     {"color": "#D55E00", "marker": "bar", "dash": None, "width": 1.5},
    "flow":        {"color": "#333333", "marker": "arrow", "dash": None, "width": 2.0},
    "convert":     {"color": "#333333", "marker": "arrow", "dash": None, "width": 1.6},
    "contain":     {"color": "#666666", "marker": "none", "dash": None, "width": 1.1},
    "bind":        {"color": "#666666", "marker": "none", "dash": None, "width": 1.2},
    "translocate": {"color": "#333333", "marker": "arrow", "dash": "5 3", "width": 1.4},
    "inferred":    {"color": "#666666", "marker": "arrow-gray", "dash": "5 3", "width": 1.2},
    "default":     {"color": "#333333", "marker": "arrow", "dash": None, "width": 1.3},
}


class InputError(Exception):
    """Structured, user-readable input failure (never a traceback)."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []

    def as_dict(self):
        return {"ok": False, "error_code": self.code,
                "message": self.message, "details": self.details}


def read_json(path, label):
    p = Path(path)
    if not p.is_file():
        raise InputError("input_missing", "%s file not found: %s" % (label, p),
                         ["pass a real path or write the SVG by hand"])
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise InputError("input_unreadable", "cannot read %s: %s" % (label, exc))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError("invalid_json", "%s is not valid JSON: %s" % (label, exc),
                         ["line %s column %s" % (exc.lineno, exc.colno)])
    if not isinstance(data, dict):
        raise InputError("root_not_object", "%s root must be a JSON object" % label)
    return data


# ---------------------------------------------------------------------------
# text metrics (deliberately simple, deterministic)
def char_width(ch, size):
    if ord(ch) > 0x2E80:
        return size
    if ch in "il.,:;'|!()[]":
        return size * 0.34
    if ch in "mwMW@%":
        return size * 0.95
    return size * 0.56


def text_width(text, size):
    return sum(char_width(ch, size) for ch in str(text))


def wrap_label(label, max_width, size):
    """Greedy wrap that also breaks between CJK characters."""
    paragraphs = str(label).split("\n")
    lines = []
    for para in paragraphs:
        tokens, buf = [], ""
        for ch in para:
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
        cur = ""
        for tok in tokens:
            cand = cur + tok
            if text_width(cand, size) <= max_width or not cur:
                cur = cand
            else:
                lines.append(cur.rstrip())
                cur = "" if tok == " " else tok
        if cur.strip():
            lines.append(cur.rstrip())
        elif not tokens:
            lines.append("")
    return lines or [""]


# ---------------------------------------------------------------------------
def normalize(data):
    warnings = []
    errors = []

    entities = data.get("entities")
    if entities is None:
        entities = data.get("nodes")
    relations = data.get("relations")
    if relations is None:
        relations = data.get("edges")
    if entities is None:
        raise InputError("empty_entities",
                         "input has no `entities` (or `nodes`) array",
                         ["an editable schematic needs at least one entity"])
    if not isinstance(entities, list):
        raise InputError("invalid_entities", "`entities` must be a list")
    if not entities:
        raise InputError("empty_entities", "`entities` is empty",
                         ["extract entities from the source materials first"])
    if relations is None:
        relations = []
    if not isinstance(relations, list):
        raise InputError("invalid_relations", "`relations` must be a list")

    seen_ids = set()
    clean_entities = []
    for index, item in enumerate(entities):
        if not isinstance(item, dict):
            errors.append("entity[%d] is not an object" % index)
            continue
        eid = str(item.get("id") or "").strip()
        if not eid:
            errors.append("entity[%d] has no id" % index)
            continue
        if not ID_RE.match(eid):
            errors.append("entity id %r is not XML-safe (use letters, digits, _ . -)" % eid)
            continue
        if eid in seen_ids:
            errors.append("duplicate entity id: %s" % eid)
            continue
        seen_ids.add(eid)
        node = dict(item)
        node["id"] = eid
        label = item.get("label")
        if label is None or str(label).strip() == "":
            warnings.append("entity %s has no label; using its id" % eid)
            node["label"] = eid
        else:
            node["label"] = str(label)
        shape = str(item.get("shape") or TYPE_SHAPE.get(str(item.get("type")), "rect"))
        if shape not in SHAPES:
            warnings.append("entity %s has unknown shape %r; using rect" % (eid, shape))
            shape = "rect"
        node["shape"] = shape
        for key, cast in (("rank", int), ("order", int), ("w", float), ("h", float),
                          ("x", float), ("y", float), ("font_scale", float)):
            if key not in node:
                continue
            try:
                node[key] = cast(node[key])
            except (TypeError, ValueError):
                node.pop(key, None)
                warnings.append("entity %s has a non-numeric %s; ignored" % (eid, key))
        if not item.get("type"):
            warnings.append("entity %s has no type" % eid)
        if not _source_text(item.get("source")):
            warnings.append("entity %s has no usable source" % eid)
        clean_entities.append(node)

    if errors:
        raise InputError("invalid_entities", "entity table has structural problems", errors)

    by_id = {n["id"]: n for n in clean_entities}
    seen_rel = set()
    clean_relations = []
    for index, item in enumerate(relations):
        if not isinstance(item, dict):
            errors.append("relation[%d] is not an object" % index)
            continue
        rid = str(item.get("id") or "R%d" % (index + 1)).strip()
        if not ID_RE.match(rid):
            errors.append("relation id %r is not XML-safe" % rid)
            continue
        if rid in seen_rel or rid in seen_ids:
            errors.append("duplicate relation id: %s" % rid)
            continue
        src = str(item.get("from") or "").strip()
        dst = str(item.get("to") or "").strip()
        if not src or not dst:
            errors.append("relation %s needs both `from` and `to`" % rid)
            continue
        if src not in by_id:
            errors.append("relation %s has unknown `from`: %s" % (rid, src))
            continue
        if dst not in by_id:
            errors.append("relation %s has unknown `to`: %s" % (rid, dst))
            continue
        seen_rel.add(rid)
        rel = dict(item)
        rel["id"] = rid
        semantic = str(item.get("semantic") or item.get("kind") or "default").lower()
        if semantic not in SEMANTICS:
            warnings.append("relation %s has unknown semantic %r; using default" % (rid, semantic))
            semantic = "default"
        rel["semantic"] = semantic
        if not _source_text(item.get("source")):
            warnings.append("relation %s has no usable source" % rid)
        clean_relations.append(rel)

    if errors:
        raise InputError("invalid_relations", "relation table has structural problems", errors)

    canvas = data.get("canvas_mm")
    try:
        if isinstance(canvas, (list, tuple)) and len(canvas) == 2:
            width_mm, height_mm = float(canvas[0]), float(canvas[1])
        else:
            width_mm = float(data.get("width_mm", 180))
            height_mm = float(data.get("height_mm", 90))
    except (TypeError, ValueError):
        raise InputError("invalid_canvas", "canvas_mm/width_mm/height_mm must be numbers")
    if not (10 <= width_mm <= 1000 and 10 <= height_mm <= 1000):
        raise InputError("invalid_canvas",
                         "canvas must be within 10-1000 mm per side",
                         ["got %s x %s mm" % (width_mm, height_mm)])

    layout = str(data.get("layout") or "layered").lower()
    if layout not in {"layered", "grid", "free"}:
        warnings.append("unknown layout %r; using layered" % layout)
        layout = "layered"
    direction = str(data.get("direction") or "LR").upper()
    if direction not in {"LR", "TB"}:
        warnings.append("unknown direction %r; using LR" % direction)
        direction = "LR"
    try:
        font_pt = float(data.get("font_size_pt", 8))
    except (TypeError, ValueError):
        font_pt = 8.0
        warnings.append("font_size_pt is not a number; using 8 pt")
    if not 5 <= font_pt <= 24:
        warnings.append("font_size_pt %.1f outside 5-24; clamped" % font_pt)
        font_pt = min(24.0, max(5.0, font_pt))

    try:
        layout_cols = int(data.get("layout_cols", 0) or 0)
    except (TypeError, ValueError):
        layout_cols = 0
        warnings.append("layout_cols is not an integer; using auto columns")

    try:
        margin_px = float(data.get("margin_px", 0) or 0)
    except (TypeError, ValueError):
        margin_px = 0.0
        warnings.append("margin_px is not a number; using the default margin")

    groups = data.get("groups") or []
    if not isinstance(groups, list):
        warnings.append("`groups` is not a list; ignored")
        groups = []
    legend = data.get("legend") or []
    if not isinstance(legend, list):
        warnings.append("`legend` is not a list; ignored")
        legend = []

    return {
        "figure_id": str(data.get("figure_id") or "figure"),
        "title": data.get("title"),
        "footnote": data.get("footnote"),
        "width_mm": width_mm, "height_mm": height_mm,
        "layout": layout, "direction": direction,
        "free_units": str(data.get("free_units") or "px"),
        "layout_cols": layout_cols,
        "font_family": str(data.get("font_family") or DEFAULT_FONT),
        "font_pt": font_pt, "margin_px": float(data.get("margin_px", 0) or 0),
        "entities": clean_entities, "relations": clean_relations,
        "groups": [g for g in groups if isinstance(g, dict)],
        "legend": [g for g in legend if isinstance(g, (dict, str))],
        "warnings": warnings,
    }


def _source_text(value):
    if isinstance(value, dict):
        value = value.get("path") or value.get("locator") or value.get("page")
    text = str(value or "").strip()
    return bool(text) and text.lower() not in {"unknown", "n/a", "not_provided", "none"}


def _source_attr(value):
    """Flatten a source dict into a readable data-source attribute value."""
    if isinstance(value, dict):
        parts = [str(v) for v in value.values() if v not in (None, "")]
        return " ".join(parts)
    return str(value or "")


# ---------------------------------------------------------------------------
class Diagram:
    def __init__(self, spec):
        self.spec = spec
        self.ff = spec["font_family"]
        self.fs = spec["font_pt"] * PT_TO_PX
        self.W = spec["width_mm"] * MM_TO_PX
        self.H = spec["height_mm"] * MM_TO_PX
        self.margin = spec["margin_px"] or max(10.0, self.fs * 1.6)
        self.nodes = spec["entities"]
        self.edges = spec["relations"]
        self.groups = spec["groups"]
        self.legend = spec["legend"]
        self.by_id = {n["id"]: n for n in self.nodes}
        self.out = []
        self.text_count = 0

    # -- layout ------------------------------------------------------------
    def measure(self):
        for node in self.nodes:
            inner = float(node.get("font_scale", 1.0)) * self.fs
            max_w = float(node.get("max_text_px", 0) or 0) or min(self.W * 0.28, 150.0)
            node["_lines"] = wrap_label(node["label"], max_w, inner)
            sub = node.get("sublabel")
            node["_sub"] = wrap_label(str(sub), max_w, inner * 0.86) if sub else []
            if node.get("w"):
                node["_w"] = float(node["w"])
            else:
                widths = [text_width(line, inner) for line in node["_lines"]]
                widths += [text_width(line, inner * 0.86) for line in node["_sub"]]
                node["_w"] = max(30.0, max(widths or [0]) + inner * 2.2)
            if node.get("h"):
                node["_h"] = float(node["h"])
            else:
                line_h = inner * 1.24
                node["_h"] = max(18.0, len(node["_lines"]) * line_h
                                 + len(node["_sub"]) * line_h * 0.9 + inner * 1.1)
            if node["shape"] == "circle":
                side = max(node["_w"], node["_h"])
                node["_w"] = node["_h"] = side

    def assign_ranks(self):
        if self.spec["layout"] == "free":
            scale = MM_TO_PX if self.spec.get("free_units") == "mm" else 1.0
            for node in self.nodes:
                node["_cx"] = float(node.get("x", self.W / 2)) * scale
                node["_cy"] = float(node.get("y", self.H / 2)) * scale
            return

        ids = [n["id"] for n in self.nodes]
        if not any("rank" in n for n in self.nodes):
            rank = {eid: 0 for eid in ids}
            for _ in range(len(ids) + 1):
                changed = False
                for rel in self.edges:
                    a, b = rel["from"], rel["to"]
                    if rank[b] < rank[a] + 1 and rank[a] + 1 < len(ids):
                        rank[b] = rank[a] + 1
                        changed = True
                if not changed:
                    break
            for node in self.nodes:
                node["rank"] = rank.get(node["id"], 0)
        for node in self.nodes:
            node["rank"] = int(node.get("rank", 0))
        counters = {}
        for node in self.nodes:
            r = node["rank"]
            if "order" in node:
                node["order"] = int(node["order"])
                counters[r] = max(counters.get(r, 0), node["order"] + 1)
            else:
                node["order"] = counters.get(r, 0)
                counters[r] = node["order"] + 1
        self.ranks = sorted({n["rank"] for n in self.nodes})
        self.cols = {}
        for node in self.nodes:
            self.cols.setdefault(node["rank"], []).append(node)

    def usable_box(self):
        top = self.margin + (self.fs * 2.0 if self.spec["title"] else 0)
        bottom = self.H - self.margin
        if self.legend:
            bottom -= self.fs * 2.0
        if self.spec["footnote"] or any(r.get("kind") == "inferred" or
                                        r.get("semantic") == "inferred" for r in self.edges) \
                or any(n.get("kind") == "inferred" or n.get("dashed") for n in self.nodes):
            bottom -= self.fs * 1.6
        return top, max(top + 1.0, bottom)

    def place(self):
        if self.spec["layout"] == "free":
            return
        top, bottom = self.usable_box()
        if self.spec["layout"] == "grid":
            cols = int(self.spec.get("layout_cols", 0) or max(1, math.ceil(math.sqrt(len(self.nodes)))))
            rows = math.ceil(len(self.nodes) / cols)
            cw = (self.W - 2 * self.margin) / cols
            ch = (bottom - top) / rows
            for index, node in enumerate(self.nodes):
                row, col = divmod(index, cols)
                node["_cx"] = self.margin + cw * (col + 0.5)
                node["_cy"] = top + ch * (row + 0.5)
            return

        if self.spec["direction"] == "LR":
            widths = [max(n["_w"] for n in self.cols[r]) for r in self.ranks]
            gap = (self.W - 2 * self.margin - sum(widths)) / max(1, len(self.ranks) - 1)
            gap = max(gap, self.fs * 2.6)
            x, xs = self.margin, {}
            for r, width in zip(self.ranks, widths):
                xs[r] = x + width / 2
                x += width + gap
            for r in self.ranks:
                items = sorted(self.cols[r], key=lambda n: n["order"])
                step = (bottom - top) / len(items) if len(items) > 1 else 0
                for k, node in enumerate(items):
                    node["_cx"] = xs[r]
                    node["_cy"] = (top + bottom) / 2 if len(items) == 1 else top + step * (k + 0.5)
        else:
            heights = [max(n["_h"] for n in self.cols[r]) for r in self.ranks]
            gap = (bottom - top - sum(heights)) / max(1, len(self.ranks) - 1)
            gap = max(gap, self.fs * 2.2)
            y, ys = top, {}
            for r, height in zip(self.ranks, heights):
                ys[r] = y + height / 2
                y += height + gap
            for r in self.ranks:
                items = sorted(self.cols[r], key=lambda n: n["order"])
                step = (self.W - 2 * self.margin) / len(items) if len(items) > 1 else 0
                for k, node in enumerate(items):
                    node["_cx"] = (self.W / 2 if len(items) == 1
                                   else self.margin + step * (k + 0.5))
                    node["_cy"] = ys[r]

    # -- drawing -----------------------------------------------------------
    def emit(self, markup):
        self.out.append(markup)

    def text(self, x, y, value, size, color="#222222", anchor="middle",
             weight="normal", italic=False):
        style = ' font-style="italic"' if italic else ""
        self.emit('<text x="%.2f" y="%.2f" font-family=%s font-size="%.2f" '
                  'text-anchor="%s" fill="%s" font-weight="%s"%s>%s</text>'
                  % (x, y, quoteattr(self.ff), size, anchor, color, weight, style,
                     escape(str(value))))
        self.text_count += 1

    def svg_defs(self):
        self.emit('<defs>')
        for name, color in (("arrow", "#333333"), ("arrow-blue", "#0072B2"),
                            ("arrow-gray", "#666666")):
            self.emit('<marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" '
                      'markerWidth="6" markerHeight="6" orient="auto">'
                      '<path d="M1,1 L9,5 L1,9 z" fill="%s"/></marker>' % (name, color))
        self.emit('<marker id="bar" viewBox="0 0 10 10" refX="5" refY="5" '
                  'markerWidth="6" markerHeight="6" orient="auto">'
                  '<path d="M5,1 L5,9" stroke="#D55E00" stroke-width="2" fill="none"/>'
                  '</marker>')
        self.emit('</defs>')

    def draw_groups(self):
        for group in self.groups:
            members = [self.by_id[i] for i in
                       (group.get("nodes") or group.get("entities") or []) if i in self.by_id]
            if not members:
                continue
            pad = float(group.get("pad_px", self.fs * 1.4))
            x0 = min(n["_cx"] - n["_w"] / 2 for n in members) - pad
            x1 = max(n["_cx"] + n["_w"] / 2 for n in members) + pad
            y0 = min(n["_cy"] - n["_h"] / 2 for n in members) - pad
            if group.get("label"):
                y0 -= self.fs * 1.2
            y1 = max(n["_cy"] + n["_h"] / 2 for n in members) + pad * 0.8
            gid = str(group.get("id") or "group")
            self.emit('<g id=%s data-role="compartment">' % quoteattr(gid))
            self.emit('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="%.2f" '
                      'fill="%s" fill-opacity="0.65" stroke="%s" stroke-width="0.9" '
                      'stroke-dasharray="6 4"/>'
                      % (x0, y0, x1 - x0, y1 - y0, self.fs * 0.5,
                         group.get("fill", "#f7f7f7"), group.get("stroke", "#bbbbbb")))
            if group.get("label"):
                self.text(x0 + self.fs * 0.6, y0 + self.fs * 1.05, group["label"],
                          self.fs * 0.92, color="#555555", anchor="start", italic=True)
            self.emit('</g>')

    def anchor(self, node, tx, ty):
        cx, cy = node["_cx"], node["_cy"]
        dx, dy = tx - cx, ty - cy
        if dx == 0 and dy == 0:
            return cx, cy
        a, b = node["_w"] / 2, node["_h"] / 2
        if node["shape"] in ("ellipse", "circle"):
            t = 1.0 / math.sqrt((dx * dx) / (a * a) + (dy * dy) / (b * b))
            return cx + dx * t, cy + dy * t
        tx_ = a / abs(dx) if dx else float("inf")
        ty_ = b / abs(dy) if dy else float("inf")
        t = min(tx_, ty_)
        return cx + dx * t, cy + dy * t

    def edge_path(self, rel):
        a, b = self.by_id[rel["from"]], self.by_id[rel["to"]]
        x1, y1 = self.anchor(a, b["_cx"], b["_cy"])
        x2, y2 = self.anchor(b, a["_cx"], a["_cy"])
        route = str(rel.get("route") or "straight").lower()
        if route == "curve":
            mx = (x1 + x2) / 2
            return ("M%.2f,%.2f C%.2f,%.2f %.2f,%.2f %.2f,%.2f"
                    % (x1, y1, mx, y1, mx, y2, x2, y2)), ((x1 + x2) / 2, (y1 + y2) / 2)
        if route == "orthogonal":
            if abs(x2 - x1) >= abs(y2 - y1):
                mid = (x1 + x2) / 2
                points = [(x1, y1), (mid, y1), (mid, y2), (x2, y2)]
            else:
                mid = (y1 + y2) / 2
                points = [(x1, y1), (x1, mid), (x2, mid), (x2, y2)]
            return ("M" + " L".join("%.2f,%.2f" % p for p in points),
                    ((x1 + x2) / 2, (y1 + y2) / 2))
        return ("M%.2f,%.2f L%.2f,%.2f" % (x1, y1, x2, y2),
                ((x1 + x2) / 2, (y1 + y2) / 2))

    def draw_edge(self, rel):
        semantic = rel["semantic"]
        kind = "inferred" if rel.get("kind") == "inferred" else semantic
        style = dict(EDGE_STYLE.get(kind, EDGE_STYLE["default"]))
        if rel.get("color"):
            style["color"] = str(rel["color"])
        if rel.get("dashed"):
            style["dash"] = "5 3"
        direction = str(rel.get("direction") or "forward").lower()
        marker = style["marker"]
        if direction in ("none", "undirected", "undetermined"):
            marker = "none"
        dash = ' stroke-dasharray="%s"' % style["dash"] if style["dash"] else ""
        marker_attr = ' marker-end="url(#%s)"' % marker if marker != "none" else ""
        d, (lx, ly) = self.edge_path(rel)
        self.emit('<g id=%s data-kind=%s data-source=%s>'
                  % (quoteattr(rel["id"]), quoteattr(kind),
                     quoteattr(str(rel.get("source", "")))))
        self.emit('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" '
                  'stroke-linejoin="round"%s%s/>'
                  % (d, style["color"], style["width"], dash, marker_attr))
        label = rel.get("label")
        if label not in (None, ""):
            size = self.fs * 0.92
            width = text_width(label, size)
            self.emit('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                      'fill="#ffffff" fill-opacity="0.92" stroke="none"/>'
                      % (lx - width / 2 - 2, ly - size * 0.78, width + 4, size * 1.15))
            self.text(lx, ly + size * 0.12, label, size, color=style["color"])
        self.emit('</g>')

    def draw_node(self, node):
        cx, cy = node["_cx"], node["_cy"]
        w, h = node["_w"], node["_h"]
        x, y = cx - w / 2, cy - h / 2
        fill = str(node.get("fill", "#ffffff"))
        stroke = str(node.get("stroke", "#333333"))
        dash = ' stroke-dasharray="5 3"' if (node.get("dashed") or node.get("kind") == "inferred") else ""
        self.emit('<g id=%s data-shape=%s data-source=%s>'
                  % (quoteattr(node["id"]), quoteattr(node["shape"]),
                     quoteattr(str(node.get("source", "")))))
        shape = node["shape"]
        if shape == "ellipse":
            self.emit('<ellipse cx="%.2f" cy="%.2f" rx="%.2f" ry="%.2f" fill="%s" '
                      'stroke="%s" stroke-width="1.1"%s/>'
                      % (cx, cy, w / 2, h / 2, fill, stroke, dash))
        elif shape == "circle":
            self.emit('<circle cx="%.2f" cy="%.2f" r="%.2f" fill="%s" stroke="%s" '
                      'stroke-width="1.1"%s/>' % (cx, cy, w / 2, fill, stroke, dash))
        elif shape in ("hexagon", "diamond"):
            if shape == "hexagon":
                points = [(cx + (w / 2) * math.cos(math.radians(a)),
                           cy + (h / 2) * math.sin(math.radians(a)))
                          for a in (0, 60, 120, 180, 240, 300)]
            else:
                points = [(cx, y), (x + w, cy), (cx, y + h), (x, cy)]
            self.emit('<polygon points="%s" fill="%s" stroke="%s" stroke-width="1.1"%s/>'
                      % (" ".join("%.2f,%.2f" % p for p in points), fill, stroke, dash))
        else:
            radius = min(w, h) * 0.18 if shape == "rounded" else 0
            self.emit('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="%.2f" '
                      'ry="%.2f" fill="%s" stroke="%s" stroke-width="1.1"%s/>'
                      % (x, y, w, h, radius, radius, fill, stroke, dash))

        inner = float(node.get("font_scale", 1.0)) * self.fs
        color = str(node.get("text_color", "#222222"))
        line_h = inner * 1.24
        total = len(node["_lines"]) * line_h + len(node["_sub"]) * line_h * 0.9
        ty = cy - total / 2 + inner * 0.86
        weight = "bold" if node.get("bold") else "normal"
        for line in node["_lines"]:
            self.text(cx, ty, line, inner, color=color, weight=weight,
                      italic=bool(node.get("italic")))
            ty += line_h
        for line in node["_sub"]:
            ty += line_h * 0.06
            self.text(cx, ty, line, inner * 0.86, color=color)
            ty += line_h * 0.9
        self.emit('</g>')

    def draw_header(self):
        if self.spec["title"]:
            self.text(self.margin, self.margin + self.fs * 1.2, self.spec["title"],
                      self.fs * 1.1, color="#222222", anchor="start", weight="bold")

    def draw_legend(self):
        if not self.legend:
            return
        size = self.fs * 0.92
        y = self.H - self.margin - self.fs * 0.9
        x = self.margin
        self.emit('<g id="legend" data-role="legend">')
        self.emit('<text x="%.2f" y="%.2f" font-family=%s font-size="%.2f" '
                  'fill="#555555">Legend</text>'
                  % (x, y - size * 1.35, quoteattr(self.ff), size * 0.9))
        for item in self.legend:
            if isinstance(item, str):
                kind, label = item, item
            else:
                kind = str(item.get("kind") or item.get("semantic") or "default")
                label = str(item.get("label") or kind)
            style = EDGE_STYLE.get(kind, EDGE_STYLE["default"])
            dash = ' stroke-dasharray="%s"' % style["dash"] if style["dash"] else ""
            marker = style["marker"]
            marker_attr = ' marker-end="url(#%s)"' % marker if marker != "none" else ""
            self.emit('<path d="M%.2f,%.2f L%.2f,%.2f" fill="none" stroke="%s" '
                      'stroke-width="%.2f"%s%s data-kind=%s/>'
                      % (x, y, x + size * 2.2, y, style["color"],
                         style["width"], dash, marker_attr, quoteattr(kind)))
            self.text(x + size * 2.7, y + size * 0.34, label, size, anchor="start")
            x += size * 2.7 + text_width(label, size) + size * 2.0
        self.emit('</g>')

    def draw_footnote(self):
        notes = []
        if self.spec["footnote"]:
            notes.append(str(self.spec["footnote"]))
        inferred = any(r.get("kind") == "inferred" or r.get("semantic") == "inferred"
                       for r in self.edges)
        if inferred:
            notes.append("灰色虚线为推断或示意关系，材料未直接给出。")
        if not notes:
            return
        self.text(self.margin, self.H - self.margin * 0.55, "  ".join(notes),
                  self.fs * 0.82, color="#666666", anchor="start")

    def geometry_report(self):
        boxes = [(n["id"], n["_cx"] - n["_w"] / 2, n["_cy"] - n["_h"] / 2,
                  n["_cx"] + n["_w"] / 2, n["_cy"] + n["_h"] / 2) for n in self.nodes]
        overlaps = []
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if (min(a[3], b[3]) - max(a[1], b[1]) > 1 and
                        min(a[4], b[4]) - max(a[2], b[2]) > 1):
                    overlaps.append([a[0], b[0]])
        outside = [box[0] for box in boxes
                   if box[1] < 0 or box[2] < 0 or box[3] > self.W or box[4] > self.H]
        return {"node_overlaps": overlaps, "out_of_canvas": outside}

    def render(self):
        self.measure()
        self.assign_ranks()
        self.place()
        self.svg_defs()
        self.emit('<g id="background"><rect x="0" y="0" width="%.2f" height="%.2f" '
                  'fill="#ffffff"/></g>' % (self.W, self.H))
        self.draw_groups()
        for rel in self.edges:
            self.draw_edge(rel)
        for node in self.nodes:
            self.draw_node(node)
        self.draw_header()
        self.draw_legend()
        self.draw_footnote()
        geometry = self.geometry_report()
        header = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                  '<svg xmlns="http://www.w3.org/2000/svg" width="%.1fmm" '
                  'height="%.1fmm" viewBox="0 0 %.2f %.2f">\n'
                  '<title>%s</title>\n'
                  '<desc>Editable schematic. Every node/edge is a named group; '
                  'sources are stored in data-source attributes.</desc>\n'
                  % (self.spec["width_mm"], self.spec["height_mm"], self.W, self.H,
                     escape(self.spec["figure_id"])))
        return header + "\n".join(self.out) + "\n</svg>\n", geometry


def build(spec, out_path):
    diagram = Diagram(spec)
    svg, geometry = diagram.render()
    out = Path(out_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(svg, encoding="utf-8")
    except OSError as exc:
        raise InputError("output_unwritable", "cannot write SVG: %s" % exc)
    warnings = list(spec["warnings"])
    if geometry["node_overlaps"]:
        warnings.append("overlapping nodes: %s" % geometry["node_overlaps"])
    if geometry["out_of_canvas"]:
        warnings.append("nodes outside canvas: %s" % geometry["out_of_canvas"])
    return {
        "ok": True,
        "path": str(out),
        "figure_id": spec["figure_id"],
        "width_mm": spec["width_mm"],
        "height_mm": spec["height_mm"],
        "nodes": len(diagram.nodes),
        "edges": len(diagram.edges),
        "compartments": len(diagram.groups),
        "text_elements": diagram.text_count,
        "named_groups": svg.count("<g id="),
        "bytes": len(svg.encode("utf-8")),
        "warnings": warnings,
        "geometry": geometry,
    }


def main():
    parser = argparse.ArgumentParser(description="JSON entity table -> editable SVG")
    parser.add_argument("--entities", required=True)
    parser.add_argument("--spec", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        table = read_json(args.entities, "entities")
        if args.spec:
            spec_data = read_json(args.spec, "spec")
            merged = dict(table)
            merged.update(spec_data)
            if "nodes" in spec_data and "entities" not in spec_data:
                merged["entities"] = spec_data["nodes"]
            if "edges" in spec_data and "relations" not in spec_data:
                merged["relations"] = spec_data["edges"]
            table = merged
        report = build(normalize(table), args.out)
    except InputError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:  # last-resort guard: never leak a traceback
        payload = {"ok": False, "error_code": "internal_error",
                   "message": "%s: %s" % (type(exc).__name__, exc),
                   "details": ["hand-write the SVG and record audit_unavailable"]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
          "%s <- %s (%d nodes, %d edges)" % (report["path"], args.entities,
                                             report["nodes"], report["edges"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
