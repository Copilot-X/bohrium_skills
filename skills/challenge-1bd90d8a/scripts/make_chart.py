#!/usr/bin/env python3
"""Deterministic chart builder: JSON spec -> editable SVG (stdlib only).

Supported panel types: bar (grouped), line, scatter.
Every series keeps its data source string so the caller can rebuild
element_source_map without guessing.

Usage:
    python3 make_chart.py --spec figure-spec.json --out fig3.svg
    python3 make_chart.py --check fig3.svg
    python3 make_chart.py --spec sp.json --out fig.svg --json
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

# Okabe-Ito colour-blind safe palette (plus black).
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7",
           "#56B4E9", "#D55E00", "#F0E442", "#000000"]
MARKERS = ["circle", "square", "triangle", "diamond", "cross", "star"]


# --------------------------------------------------------------------------
# numeric helpers
# --------------------------------------------------------------------------
def nice_num(x, do_round):
    if x <= 0:
        return 0.0
    exp = math.floor(math.log10(x))
    f = x / (10.0 ** exp)
    if do_round:
        if f < 1.5:
            nf = 1.0
        elif f < 3.0:
            nf = 2.0
        elif f < 7.0:
            nf = 5.0
        else:
            nf = 10.0
    else:
        if f <= 1.0:
            nf = 1.0
        elif f <= 2.0:
            nf = 2.0
        elif f <= 5.0:
            nf = 5.0
        else:
            nf = 10.0
    return nf * (10.0 ** exp)


def linear_ticks(lo, hi, target=5):
    if hi == lo:
        hi = lo + 1.0
    span = nice_num(hi - lo, False)
    step = nice_num(span / max(1, target - 1), True)
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step) * step
    ticks = []
    v = start
    while v <= end + step * 1e-9:
        if v >= lo - step * 1e-9 and v <= hi + step * 1e-9:
            ticks.append(v)
        v += step
    return ticks


def log_ticks(lo, hi):
    lo = max(lo, 1e-12)
    hi = max(hi, lo * 10)
    ticks = []
    e0, e1 = math.floor(math.log10(lo)), math.ceil(math.log10(hi))
    for e in range(int(e0), int(e1) + 1):
        for m in (1, 2, 5):
            v = m * (10.0 ** e)
            if lo * 0.999 <= v <= hi * 1.001:
                ticks.append(v)
    return ticks or [lo, hi]


def fmt_num(v):
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1000:
        s = f"{v:.0f}"
    elif a >= 10:
        s = f"{v:.2f}".rstrip("0").rstrip(".")
    elif a >= 1:
        s = f"{v:.2f}".rstrip("0").rstrip(".")
    elif a >= 0.01:
        s = f"{v:.3f}".rstrip("0").rstrip(".")
    else:
        s = f"{v:.1e}".replace("e-0", "e-").replace("e+0", "e")
    return s


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------
def text_width(s, size_px):
    w = 0.0
    for ch in s:
        if ord(ch) > 0x2E80:  # CJK is roughly square
            w += size_px * 1.0
        elif ch in "il.,:;'|!":
            w += size_px * 0.32
        elif ch in "mwMW@%":
            w += size_px * 0.95
        else:
            w += size_px * 0.56
    return w


def marker_svg(name, cx, cy, r, color, stroke_bg="#ffffff"):
    if name == "square":
        return (f'<rect x="{cx - r:.2f}" y="{cy - r:.2f}" width="{2 * r:.2f}" '
                f'height="{2 * r:.2f}" fill="{color}" stroke="{stroke_bg}" stroke-width="0.8"/>')
    if name == "triangle":
        pts = f"{cx:.2f},{cy - r * 1.15:.2f} {cx - r:.2f},{cy + r * 0.85:.2f} {cx + r:.2f},{cy + r * 0.85:.2f}"
        return (f'<polygon points="{pts}" fill="{color}" stroke="{stroke_bg}" stroke-width="0.8"/>')
    if name == "diamond":
        pts = f"{cx:.2f},{cy - r * 1.2:.2f} {cx + r * 1.2:.2f},{cy:.2f} {cx:.2f},{cy + r * 1.2:.2f} {cx - r * 1.2:.2f},{cy:.2f}"
        return (f'<polygon points="{pts}" fill="{color}" stroke="{stroke_bg}" stroke-width="0.8"/>')
    if name == "cross":
        return (f'<path d="M{cx - r:.2f},{cy - r:.2f} L{cx + r:.2f},{cy + r:.2f} '
                f'M{cx + r:.2f},{cy - r:.2f} L{cx - r:.2f},{cy + r:.2f}" '
                f'stroke="{color}" stroke-width="1.6" fill="none"/>')
    if name == "star":
        pts = []
        for k in range(10):
            ang = -math.pi / 2 + k * math.pi / 5
            rad = r * 1.25 if k % 2 == 0 else r * 0.55
            pts.append(f"{cx + rad * math.cos(ang):.2f},{cy + rad * math.sin(ang):.2f}")
        return (f'<polygon points="{" ".join(pts)}" fill="{color}" '
                f'stroke="{stroke_bg}" stroke-width="0.6"/>')
    return (f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{color}" '
            f'stroke="{stroke_bg}" stroke-width="0.8"/>')


# --------------------------------------------------------------------------
# panel renderer
# --------------------------------------------------------------------------
class Panel:
    def __init__(self, spec, box, font_family, font_size, palette):
        self.spec = spec
        self.x, self.y, self.w, self.h = box
        self.ff = font_family
        self.fs = font_size
        self.palette = palette
        self.out = []
        self.kind = spec.get("type", "bar")
        self.series = spec.get("series", [])
        self.pid = spec.get("id", "")

    # ---- public -----------------------------------------------------
    def render(self):
        pad_l, pad_r, pad_t, pad_b = self._pads()
        self.px0 = self.x + pad_l
        self.px1 = self.x + self.w - pad_r
        self.py0 = self.y + pad_t
        self.py1 = self.y + self.h - pad_b
        self._value_range()
        self._draw_frame()
        if self.kind == "bar":
            self._draw_bars()
        else:
            self._draw_xy()
        self._draw_axes()
        self._draw_title()
        self._draw_legend()
        return "".join(self.out)

    # ---- layout -----------------------------------------------------
    def _pads(self):
        fs = self.fs
        ylab = self.spec.get("y", {}).get("label", "")
        xlab = self.spec.get("x", {}).get("label", "")
        has_legend = len(self.series) > 1 or bool(self.spec.get("legend"))
        left = 14 + (text_width(ylab, fs) if ylab else 0)
        bottom = 14 + (fs * 1.6 if xlab else 0) + fs * 1.2
        top = fs * 2.2 + (fs * 1.5 if self.spec.get("title") else 0)
        if has_legend:
            top += fs * 1.3
        right = fs * 1.2
        return left, right, top, bottom

    def _value_range(self):
        ys = self.spec.get("y", {})
        vals = []
        for s in self.series:
            vals.extend([v for v in s.get("values", []) if isinstance(v, (int, float))])
            vals.extend([v for v in s.get("y", []) if isinstance(v, (int, float))])
            errs = s.get("errors") or s.get("error", {}).get("values") or []
            base = s.get("values") or s.get("y") or []
            for i, e in enumerate(errs):
                if isinstance(e, (int, float)) and i < len(base) and isinstance(base[i], (int, float)):
                    vals.append(base[i] + abs(e))
                    vals.append(base[i] - abs(e))
        lo = ys.get("min")
        hi = ys.get("max")
        auto_lo, auto_hi = lo is None, hi is None
        if auto_lo or auto_hi:
            vmin = min(vals) if vals else 0.0
            vmax = max(vals) if vals else 1.0
            if self.kind == "bar":
                d_lo = min(0.0, vmin)
                d_hi = max(0.0, vmax)
                span = (d_hi - d_lo) or 1.0
                d_lo = d_lo - span * 0.08 if d_lo < 0 else 0.0
                d_hi = d_hi + span * 0.08 if d_hi > 0 else 0.0
            else:
                span = vmax - vmin
                pad = span * 0.10 if span > 0 else (abs(vmax) * 0.10 or 1.0)
                d_lo, d_hi = vmin - pad, vmax + pad
                if vmin >= 0 and d_lo < 0:
                    d_lo = 0.0
            if auto_lo:
                lo = d_lo
            if auto_hi:
                hi = d_hi
        if hi <= lo:
            hi = lo + 1.0
        self.ylo, self.yhi = float(lo), float(hi)
        self.ylog = bool(ys.get("log"))
        if self.ylog:
            self.ylo, self.yhi = max(self.ylo, 1e-12), max(self.yhi, self.ylo * 10)
        self.ticks = log_ticks(self.ylo, self.yhi) if self.ylog else linear_ticks(self.ylo, self.yhi, 5)
        if self.ticks:
            self.ylo = min(self.ylo, self.ticks[0])
            self.yhi = max(self.yhi, self.ticks[-1])

    def _sy(self, v):
        if self.ylog:
            v = max(v, 1e-12)
            a, b = math.log10(max(self.ylo, 1e-12)), math.log10(self.yhi)
            t = (math.log10(v) - a) / (b - a) if b > a else 0.5
        else:
            t = (v - self.ylo) / (self.yhi - self.ylo) if self.yhi > self.ylo else 0.5
        return self.py1 - t * (self.py1 - self.py0)

    # ---- frame ------------------------------------------------------
    def _draw_frame(self):
        self.out.append(
            f'<rect x="{self.x:.2f}" y="{self.y:.2f}" width="{self.w:.2f}" height="{self.h:.2f}" '
            f'fill="#ffffff" stroke="none"/>')
        for t in self.ticks:
            yy = self._sy(t)
            self.out.append(
                f'<line x1="{self.px0:.2f}" y1="{yy:.2f}" x2="{self.px1:.2f}" y2="{yy:.2f}" '
                f'stroke="#e6e6e6" stroke-width="0.6"/>')
        self.out.append(
            f'<line x1="{self.px0:.2f}" y1="{self.py0:.2f}" x2="{self.px0:.2f}" y2="{self.py1:.2f}" '
            f'stroke="#333333" stroke-width="0.9"/>')
        self.out.append(
            f'<line x1="{self.px0:.2f}" y1="{self.py1:.2f}" x2="{self.px1:.2f}" y2="{self.py1:.2f}" '
            f'stroke="#333333" stroke-width="0.9"/>')

    def _draw_axes(self):
        fs = self.fs
        for t in self.ticks:
            yy = self._sy(t)
            label = fmt_num(t)
            self.out.append(
                f'<text x="{self.px0 - 4:.2f}" y="{yy + fs * 0.34:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{fs:.2f}" text-anchor="end" fill="#333333">{escape(label)}</text>')
            self.out.append(
                f'<line x1="{self.px0 - 2.5:.2f}" y1="{yy:.2f}" x2="{self.px0:.2f}" y2="{yy:.2f}" '
                f'stroke="#333333" stroke-width="0.8"/>')
        xs = self.spec.get("x", {})
        xlabel = xs.get("label", "")
        if xlabel:
            unit = xs.get("unit")
            text = f"{xlabel} ({unit})" if unit else xlabel
            cy = self.y + self.h - fs * 0.5
            self.out.append(
                f'<text x="{(self.px0 + self.px1) / 2:.2f}" y="{cy:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{fs:.2f}" text-anchor="middle" fill="#333333">{escape(text)}</text>')
        ys = self.spec.get("y", {})
        ylabel = ys.get("label", "")
        if ylabel:
            unit = ys.get("unit")
            text = f"{ylabel} ({unit})" if unit else ylabel
            cx = self.x + fs * 1.05
            cy = (self.py0 + self.py1) / 2
            self.out.append(
                f'<text x="{cx:.2f}" y="{cy:.2f}" font-family="{escape(self.ff)}" font-size="{fs:.2f}" '
                f'text-anchor="middle" fill="#333333" '
                f'transform="rotate(-90 {cx:.2f} {cy:.2f})">{escape(text)}</text>')
        if self.ylog:
            self.out.append(
                f'<text x="{self.px0 + 2:.2f}" y="{self.py0 - 2:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{fs * 0.9:.2f}" fill="#666666">log scale</text>')

    def _draw_title(self):
        title = self.spec.get("title")
        label_w = self.fs * 2.0 if self.pid else 0.0
        if self.pid:
            self.out.append(
                f'<text x="{self.x + 2:.2f}" y="{self.y + self.fs * 1.15:.2f}" '
                f'font-family="{escape(self.ff)}" font-size="{self.fs * 1.05:.2f}" '
                f'font-weight="bold" fill="#222222">({escape(self.pid)})</text>')
        if title:
            ty = self.y + self.fs * 1.15
            self.out.append(
                f'<text x="{self.x + 2 + label_w:.2f}" y="{ty:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{self.fs * 1.05:.2f}" font-weight="bold" fill="#222222">'
                f'{escape(title)}</text>')

    def _draw_legend(self):
        if len(self.series) <= 1 and not self.spec.get("legend"):
            return
        fs = self.fs
        items = [(s.get("name", f"S{i + 1}"), s.get("color") or self.palette[i % len(self.palette)])
                 for i, s in enumerate(self.series)]
        widths = [text_width(n, fs) + fs * 1.9 for n, _ in items]
        total = sum(widths)
        start = self.px1 - total
        yy = self.py0 - fs * 1.35
        for (name, color), wdt in zip(items, widths):
            self.out.append(
                f'<line x1="{start:.2f}" y1="{yy:.2f}" x2="{start + fs * 1.1:.2f}" y2="{yy:.2f}" '
                f'stroke="{color}" stroke-width="2.0"/>')
            self.out.append(
                f'<text x="{start + fs * 1.45:.2f}" y="{yy + fs * 0.34:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{fs * 0.92:.2f}" fill="#333333">{escape(name)}</text>')
            start += wdt

    # ---- bars -------------------------------------------------------
    def _category_count(self):
        cats = self.spec.get("x", {}).get("categories") or []
        if cats:
            return len(cats)
        return max([len(s.get("values", [])) for s in self.series] + [1])

    def _draw_bars(self):
        n = self._category_count()
        m = max(len(self.series), 1)
        slot = (self.px1 - self.px0) / max(n, 1)
        group = slot * 0.78
        bw = group / m
        base = self._sy(0.0)
        for si, s in enumerate(self.series):
            color = s.get("color") or self.palette[si % len(self.palette)]
            errs = s.get("errors")
            if errs is None and isinstance(s.get("error"), dict):
                errs = s["error"].get("values")
            for ci, val in enumerate(s.get("values", [])):
                if not isinstance(val, (int, float)):
                    continue
                cx = self.px0 + slot * (ci + 0.5) - group / 2 + bw * (si + 0.5)
                top = self._sy(val)
                y = min(top, base)
                h = abs(base - top)
                self.out.append(
                    f'<rect x="{cx - bw * 0.44:.2f}" y="{y:.2f}" width="{bw * 0.88:.2f}" '
                    f'height="{max(h, 0.3):.2f}" fill="{color}" stroke="#222222" '
                    f'stroke-width="0.6" fill-opacity="0.92"/>')
                if errs and ci < len(errs) and isinstance(errs[ci], (int, float)):
                    self._error_bar(cx, val, errs[ci], color)
        # category labels
        cats = self.spec.get("x", {}).get("categories") or [str(i + 1) for i in range(n)]
        fs = self.fs
        for ci, cat in enumerate(cats[:n]):
            cx = self.px0 + slot * (ci + 0.5)
            self.out.append(
                f'<text x="{cx:.2f}" y="{self.py1 + fs * 1.05:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{fs * 0.95:.2f}" text-anchor="middle" fill="#333333">'
                f'{escape(str(cat))}</text>')
        self._significance(slot, base)

    def _significance(self, slot, base):
        for s in self.series:
            for sig in s.get("significance", []) or []:
                a, b, label = sig.get("a"), sig.get("b"), sig.get("label", "*")
                if a is None or b is None:
                    continue
                vals = s.get("values", [])
                if a >= len(vals) or b >= len(vals):
                    continue
                ya, yb = self._sy(vals[a]), self._sy(vals[b])
                top = min(ya, yb) - self.fs * 0.9
                xa = self.px0 + slot * (a + 0.5)
                xb = self.px0 + slot * (b + 0.5)
                self.out.append(
                    f'<path d="M{xa:.2f},{top + 3:.2f} L{xa:.2f},{top:.2f} L{xb:.2f},{top:.2f} '
                    f'L{xb:.2f},{top + 3:.2f}" stroke="#333333" stroke-width="0.8" fill="none"/>')
                self.out.append(
                    f'<text x="{(xa + xb) / 2:.2f}" y="{top - 1.5:.2f}" font-family="{escape(self.ff)}" '
                    f'font-size="{self.fs:.2f}" text-anchor="middle" fill="#333333">'
                    f'{escape(str(label))}</text>')

    def _error_bar(self, cx, val, err, color):
        y1, y2 = self._sy(val + abs(err)), self._sy(val - abs(err))
        cap = max(2.0, self.fs * 0.3)
        self.out.append(
            f'<line x1="{cx:.2f}" y1="{y1:.2f}" x2="{cx:.2f}" y2="{y2:.2f}" '
            f'stroke="#222222" stroke-width="0.9"/>')
        for yy in (y1, y2):
            self.out.append(
                f'<line x1="{cx - cap:.2f}" y1="{yy:.2f}" x2="{cx + cap:.2f}" y2="{yy:.2f}" '
                f'stroke="#222222" stroke-width="0.9"/>')

    # ---- line / scatter --------------------------------------------
    def _draw_xy(self):
        xs = self.spec.get("x", {})
        shared_x = xs.get("values")
        auto_lo = min([v for s in self.series for v in (s.get("x") or shared_x or [])] or [0])
        auto_hi = max([v for s in self.series for v in (s.get("x") or shared_x or [])] or [1])
        lo = xs.get("min", auto_lo)
        hi = xs.get("max", auto_hi)
        if hi == lo:
            hi = lo + 1.0
        self.xlo, self.xhi = float(lo), float(hi)

        def sx(v):
            return self.px0 + (v - self.xlo) / (self.xhi - self.xlo) * (self.px1 - self.px0)

        xticks = log_ticks(self.xlo, self.xhi) if xs.get("log") else linear_ticks(self.xlo, self.xhi, 5)
        fs = self.fs
        for t in xticks:
            xx = sx(t)
            if xx < self.px0 - 0.5 or xx > self.px1 + 0.5:
                continue
            self.out.append(
                f'<line x1="{xx:.2f}" y1="{self.py1:.2f}" x2="{xx:.2f}" y2="{self.py1 + 2.5:.2f}" '
                f'stroke="#333333" stroke-width="0.8"/>')
            self.out.append(
                f'<text x="{xx:.2f}" y="{self.py1 + fs * 1.15:.2f}" font-family="{escape(self.ff)}" '
                f'font-size="{fs * 0.95:.2f}" text-anchor="middle" fill="#333333">'
                f'{escape(fmt_num(t))}</text>')

        for si, s in enumerate(self.series):
            color = s.get("color") or self.palette[si % len(self.palette)]
            xv = s.get("x") or shared_x or list(range(len(s.get("y", s.get("values", [])))))
            yv = s.get("y") or s.get("values", [])
            errs = s.get("errors")
            if errs is None and isinstance(s.get("error"), dict):
                errs = s["error"].get("values")
            mk = s.get("marker") or MARKERS[si % len(MARKERS)]
            pts = [(sx(x), self._sy(y)) for x, y in zip(xv, yv)
                   if isinstance(x, (int, float)) and isinstance(y, (int, float))]
            if self.kind == "line" and len(pts) > 1:
                dash = ' stroke-dasharray="5 3"' if s.get("dashed") else ""
                d = "M" + " L".join(f"{px:.2f},{py:.2f}" for px, py in pts)
                self.out.append(
                    f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{s.get("width", 1.6)}" '
                    f'stroke-linejoin="round" stroke-linecap="round"{dash}/>')
            for k, (px, py) in enumerate(pts):
                if errs and k < len(errs) and isinstance(errs[k], (int, float)):
                    self._error_bar(px, yv[k], errs[k], color)
                self.out.append(marker_svg(mk, px, py, max(2.2, self.fs * 0.34), color))

    # ---- optional note ---------------------------------------------
    def note(self):
        pass


# --------------------------------------------------------------------------
# document assembly
# --------------------------------------------------------------------------
def build(spec, out_path):
    width_mm = float(spec.get("width_mm", 180))
    height_mm = float(spec.get("height_mm", 120))
    W = width_mm * MM_TO_PX
    H = height_mm * MM_TO_PX
    ff = spec.get("font_family", "Liberation Sans")
    fs = float(spec.get("font_size_pt", 8)) * PT_TO_PX
    palette = spec.get("palette") or PALETTE
    panels = spec.get("panels", [])
    if not panels:
        raise SystemExit("spec has no panels")

    cols = int(spec.get("layout", {}).get("cols", 1 if len(panels) == 1 else 2))
    cols = max(1, min(cols, len(panels)))
    rows = math.ceil(len(panels) / cols)
    gap = fs * 0.9
    cell_w = (W - gap * (cols + 1)) / cols
    cell_h = (H - gap * (rows + 1)) / rows

    body = []
    for i, pspec in enumerate(panels):
        r, c = divmod(i, cols)
        box = (gap + c * (cell_w + gap), gap + r * (cell_h + gap), cell_w, cell_h)
        body.append(Panel(pspec, box, ff, fs, palette).render())

    svg = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm:.1f}mm" '
        f'height="{height_mm:.1f}mm" viewBox="0 0 {W:.2f} {H:.2f}">\n'
        f'<title>{escape(spec.get("figure_id", "figure"))}</title>\n'
        f'<rect x="0" y="0" width="{W:.2f}" height="{H:.2f}" fill="#ffffff"/>\n'
        + "".join(body) +
        f'\n</svg>\n'
    )

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8")

    summary = {
        "ok": True,
        "path": str(out),
        "panels": len(panels),
        "series": sum(len(p.get("series", [])) for p in panels),
        "text_elements": svg.count("<text"),
        "width_mm": width_mm,
        "height_mm": height_mm,
        "dpi_at_native": 96,
    }
    return summary


def check(path):
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "missing"}
    raw = p.read_text(encoding="utf-8")
    result = {"ok": True, "path": str(p), "bytes": len(raw.encode("utf-8"))}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return {"ok": False, "error": f"xml: {exc}"}
    if not root.tag.endswith("svg"):
        result["ok"] = False
        result["error"] = "root is not <svg>"
    if "viewBox" not in root.attrib:
        result["ok"] = False
        result["error"] = "missing viewBox"
    result["text_elements"] = raw.count("<text")
    result["groups"] = raw.count("<g ")
    result["external_refs"] = raw.count("http://") + raw.count("https://") - raw.count("http://www.w3.org")
    if result["text_elements"] == 0:
        result["ok"] = False
        result["error"] = "no <text> elements (labels may be outlined)"
    return result


def main():
    ap = argparse.ArgumentParser(description="JSON spec -> editable SVG chart")
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
    sys.exit(0)


if __name__ == "__main__":
    main()
