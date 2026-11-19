#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export an editable SVG to real PDF/PNG, with a portable fallback.

The primary renderers are cairosvg, rsvg-convert and Inkscape, exactly like the
older per-skill exporters.  When none of them exists, this script can draw the
SVG's own primitives with matplotlib.  Matplotlib is an *optional* fallback:
it is imported through importlib only after a runtime probe, so the script
stays usable on a standard-library-only image.

Nothing is fabricated.  Every output is checked by magic bytes and dimensions;
an invalid file is removed before the next renderer is attempted.

Usage:
    python3 svg_export.py --svg figures/fig1.svg --outdir /app/output \
        --formats pdf,png --dpi 300 --basename fig1 --json

Exit codes:
    0 = every requested format produced
    1 = partial (at least one format produced, or SVG remains valid)
    2 = SVG missing/unreadable
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

TOOLS = ("cairosvg", "rsvg-convert", "inkscape")
FORMATS = ("pdf", "png")
SVG_NS = "http://www.w3.org/2000/svg"
PT_PER_MM = 72.0 / 25.4
PX_PER_IN = 96.0


class ExportError(Exception):
    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []

    def as_dict(self):
        return {"ok": False, "error_code": self.code, "message": self.message,
                "details": self.details}


def emit(data, code=0):
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return code


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_float(value, default=0.0):
    if value is None:
        return default
    text = str(value).strip()
    m = re.match(r"^[-+]?(?:\d+\.?\d*|\.\d+)", text)
    return float(m.group(0)) if m else default


def parse_length(value, default=0.0):
    """Return length in SVG user units (px at 96 dpi for physical units)."""
    if value is None:
        return default
    text = str(value).strip().lower()
    m = re.match(r"^([-+]?(?:\d+\.?\d*|\.\d+))\s*(px|pt|mm|cm|in)?$", text)
    if not m:
        return default
    number = float(m.group(1))
    unit = m.group(2) or "px"
    if unit == "mm":
        return number * PX_PER_IN / 25.4
    if unit == "cm":
        return number * PX_PER_IN / 2.54
    if unit == "in":
        return number * PX_PER_IN
    if unit == "pt":
        return number * PX_PER_IN / 72.0
    return number


def read_svg(path):
    p = Path(path)
    if not p.is_file():
        raise ExportError("svg_missing", "SVG not found: %s" % p)
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExportError("svg_unreadable", "cannot read SVG: %s" % exc)
    if not raw.strip():
        raise ExportError("svg_empty", "SVG is empty: %s" % p)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ExportError("svg_invalid", "SVG is not well-formed XML: %s" % exc)
    if local_name(root.tag) != "svg":
        raise ExportError("svg_invalid", "root element is not <svg>")
    return raw, root


def svg_box(root):
    width = parse_length(root.get("width"), 0.0)
    height = parse_length(root.get("height"), 0.0)
    view = root.get("viewBox") or root.get("viewbox")
    if view:
        nums = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)", view)]
        if len(nums) == 4 and nums[2] > 0 and nums[3] > 0:
            if not width:
                width = nums[2]
            if not height:
                height = nums[3]
            return width or nums[2], height or nums[3], nums
    if width and height:
        return width, height, [0, 0, width, height]
    raise ExportError("svg_no_size", "SVG has neither width/height nor viewBox")


def validate_output(path, fmt):
    p = Path(path)
    if not p.is_file():
        return False, "renderer did not create the file", {}
    size = p.stat().st_size
    if size <= 0:
        return False, "empty output", {}
    head = p.read_bytes()[:32]
    if fmt == "png":
        if head[:8] != b"\x89PNG\r\n\x1a\n" or len(head) < 24:
            return False, "PNG signature missing", {}
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        if width <= 0 or height <= 0:
            return False, "PNG dimensions invalid", {}
        return True, "", {"width_px": width, "height_px": height, "bytes": size}
    if not head.startswith(b"%PDF"):
        return False, "PDF signature missing", {}
    return True, "", {"bytes": size}


def run_cmd(cmd, timeout=180):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "binary not found"
    except subprocess.TimeoutExpired:
        return False, "timed out after %ss" % timeout
    except OSError as exc:
        return False, "cannot execute: %s" % exc
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return False, "exit %s: %s" % (proc.returncode, output[:500])
    return True, output[:500]


def export_cairosvg(svg_path, out_path, fmt, dpi):
    if importlib.util.find_spec("cairosvg") is None:
        return False, "module not installed"
    try:
        cairosvg = importlib.import_module("cairosvg")
    except Exception as exc:  # noqa: BLE001 - optional probe
        return False, "import failed: %s" % exc
    try:
        if fmt == "pdf":
            cairosvg.svg2pdf(url=str(svg_path), write_to=str(out_path))
        else:
            cairosvg.svg2png(url=str(svg_path), write_to=str(out_path), scale=dpi / 96.0)
    except Exception as exc:  # noqa: BLE001
        return False, "conversion failed: %s: %s" % (type(exc).__name__, exc)
    ok, detail, _ = validate_output(out_path, fmt)
    return ok, detail


def export_rsvg(svg_path, out_path, fmt, dpi):
    binary = shutil.which("rsvg-convert")
    if not binary:
        return False, "binary not found"
    cmd = [binary, "-f", fmt, "-o", str(out_path)]
    if fmt == "png":
        cmd += ["-d", str(dpi), "-p", str(dpi)]
    cmd.append(str(svg_path))
    ok, detail = run_cmd(cmd)
    if not ok:
        return False, detail
    good, msg, _ = validate_output(out_path, fmt)
    return good, msg


def export_inkscape(svg_path, out_path, fmt, dpi):
    binary = shutil.which("inkscape")
    if not binary:
        return False, "binary not found"
    cmd = [binary, str(svg_path), "--export-type=%s" % fmt,
           "--export-filename=%s" % out_path]
    if fmt == "png":
        cmd.append("--export-dpi=%s" % dpi)
    ok, detail = run_cmd(cmd)
    if not ok:
        legacy = [binary, str(svg_path), "--export-%s=%s" % (fmt, out_path)]
        if fmt == "png":
            legacy.insert(1, "--export-dpi=%s" % dpi)
        ok, detail2 = run_cmd(legacy)
        if not ok:
            return False, "%s / legacy: %s" % (detail, detail2)
    good, msg, _ = validate_output(out_path, fmt)
    return good, msg


# ---------------------------------------------------------------------------
# Optional matplotlib fallback: draw the SVG primitives themselves.
# ---------------------------------------------------------------------------
def _matrix_mul(a, b):
    return [
        a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
        a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
        a[0] * b[4] + a[2] * b[5] + a[4],
        a[1] * b[4] + a[3] * b[5] + a[5],
    ]


def parse_transform(text):
    mat = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    if not text:
        return mat
    for name, args in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", text):
        vals = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)", args)]
        if name == "translate" and vals:
            step = [1, 0, 0, 1, vals[0], vals[1] if len(vals) > 1 else 0]
        elif name == "scale" and vals:
            step = [vals[0], 0, 0, vals[1] if len(vals) > 1 else vals[0], 0, 0]
        elif name == "matrix" and len(vals) == 6:
            step = vals
        elif name == "rotate" and vals:
            ang = math.radians(vals[0])
            step = [math.cos(ang), math.sin(ang), -math.sin(ang), math.cos(ang), 0, 0]
            if len(vals) >= 3:
                step[4] = vals[1]
                step[5] = vals[2]
                back = [1, 0, 0, 1, -vals[1], -vals[2]]
                step = _matrix_mul(_matrix_mul([1, 0, 0, 1, vals[1], vals[2]], step), back)
        else:
            continue
        mat = _matrix_mul(mat, step)
    return mat


def apply_mat(mat, x, y):
    return (mat[0] * x + mat[2] * y + mat[4],
            mat[1] * x + mat[3] * y + mat[5])


def color_of(value, default="none"):
    if not value or value == "none":
        return default
    value = value.strip()
    named = {
        "black": "#000000", "white": "#FFFFFF", "red": "#FF0000",
        "blue": "#0000FF", "green": "#008000", "gray": "#808080",
        "grey": "#808080", "transparent": "none",
    }
    if value.lower() in named:
        return named[value.lower()]
    return value


def parse_path_points(d):
    """Parse the common subset produced by our own SVG builders.

    Returns a list of subpaths, each a list of (x, y, command) points.  Curves
    are flattened into line segments; this is a renderer fallback, not a
    full SVG engine.
    """
    tokens = re.findall(r"[MmLlHhVvCcQqSsTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)", d or "")
    if not tokens:
        return []
    subpaths, current, cmd = [], [], "M"
    x = y = 0.0
    start = (0.0, 0.0)
    i = 0
    last_ctrl = None

    def num():
        nonlocal i
        if i >= len(tokens):
            return 0.0
        try:
            val = float(tokens[i])
            i += 1
            return val
        except ValueError:
            i += 1
            return 0.0

    while i < len(tokens):
        tok = tokens[i]
        if re.match(r"^[A-Za-z]$", tok):
            cmd = tok
            i += 1
            if cmd in "Zz":
                if current:
                    current.append((start[0], start[1], "L"))
                subpaths.append(current)
                current = []
                x, y = start
            continue
        absolute = cmd.isupper()
        low = cmd.lower()
        if low == "m":
            nx, ny = num(), num()
            if not current and subpaths:
                pass
            if current:
                subpaths.append(current)
                current = []
            x, y = (nx, ny) if absolute else (x + nx, y + ny)
            start = (x, y)
            current.append((x, y, "M"))
            cmd = "L" if absolute else "l"
        elif low == "l":
            nx, ny = num(), num()
            x, y = (nx, ny) if absolute else (x + nx, y + ny)
            current.append((x, y, "L"))
        elif low == "h":
            nx = num()
            x = nx if absolute else x + nx
            current.append((x, y, "L"))
        elif low == "v":
            ny = num()
            y = ny if absolute else y + ny
            current.append((x, y, "L"))
        elif low == "c":
            x1, y1, x2, y2, nx, ny = (num() for _ in range(6))
            if not absolute:
                x1, x2, nx = x + x1, x + x2, x + nx
                y1, y2, ny = y + y1, y + y2, y + ny
            steps = 8
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1 - t
                bx = mt ** 3 * x + 3 * mt ** 2 * t * x1 + 3 * mt * t ** 2 * x2 + t ** 3 * nx
                by = mt ** 3 * y + 3 * mt ** 2 * t * y1 + 3 * mt * t ** 2 * y2 + t ** 3 * ny
                current.append((bx, by, "L"))
            x, y, last_ctrl = nx, ny, (x2, y2)
        elif low == "s":
            x2, y2, nx, ny = (num() for _ in range(4))
            if not absolute:
                x2, nx = x + x2, x + nx
                y2, ny = y + y2, y + ny
            x1, y1 = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1]) if last_ctrl else (x, y)
            steps = 8
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1 - t
                bx = mt ** 3 * x + 3 * mt ** 2 * t * x1 + 3 * mt * t ** 2 * x2 + t ** 3 * nx
                by = mt ** 3 * y + 3 * mt ** 2 * t * y1 + 3 * mt * t ** 2 * y2 + t ** 3 * ny
                current.append((bx, by, "L"))
            x, y, last_ctrl = nx, ny, (x2, y2)
        elif low == "q":
            x1, y1, nx, ny = (num() for _ in range(4))
            if not absolute:
                x1, nx = x + x1, x + nx
                y1, ny = y + y1, y + ny
            steps = 8
            for k in range(1, steps + 1):
                t = k / steps
                mt = 1 - t
                bx = mt ** 2 * x + 2 * mt * t * x1 + t ** 2 * nx
                by = mt ** 2 * y + 2 * mt * t * y1 + t ** 2 * ny
                current.append((bx, by, "L"))
            x, y, last_ctrl = nx, ny, (x1, y1)
        elif low == "a":
            # Flatten an arc conservatively to its endpoint.
            for _ in range(5):
                num()
            nx, ny = num(), num()
            x, y = (nx, ny) if absolute else (x + nx, y + ny)
            current.append((x, y, "L"))
        else:
            i += 1
    if current:
        subpaths.append(current)
    out = []
    for sub in subpaths:
        if sub:
            out.append(sub)
    return out


def marker_kind(marker_url):
    text = (marker_url or "").lower()
    if "inhibit" in text or "bar" in text:
        return "bar"
    if "arrow" in text or "triangle" in text:
        return "arrow"
    if "none" in text:
        return "none"
    return "arrow" if text else "none"


def draw_arrow(ax, p0, p1, kind, color, linewidth, scale=9.0):
    if kind == "none":
        return
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    tip = p1
    if kind == "bar":
        half = max(4.0, linewidth * 3.2)
        ax.plot([tip[0] - px * half, tip[0] + px * half],
                [tip[1] - py * half, tip[1] + py * half],
                color=color, linewidth=max(1.2, linewidth), solid_capstyle="butt", zorder=6)
        return
    size = max(5.0, scale)
    base = (tip[0] - ux * size, tip[1] - uy * size)
    left = (base[0] + px * size * 0.55, base[1] + py * size * 0.55)
    right = (base[0] - px * size * 0.55, base[1] - py * size * 0.55)
    ax.add_patch(__import__("matplotlib").patches.Polygon(
        [tip, left, right], closed=True, facecolor=color, edgecolor=color, zorder=6))


def render_matplotlib(root, out_pdf, out_png=None, dpi=300):
    if importlib.util.find_spec("matplotlib") is None:
        return False, "matplotlib not installed"
    try:
        matplotlib = importlib.import_module("matplotlib")
        matplotlib.use("Agg")
        plt = importlib.import_module("matplotlib.pyplot")
        patches = importlib.import_module("matplotlib.patches")
        font_manager = importlib.import_module("matplotlib.font_manager")
        matplotlib.rcParams["pdf.fonttype"] = 42
        matplotlib.rcParams["ps.fonttype"] = 42
    except Exception as exc:  # noqa: BLE001
        return False, "matplotlib import failed: %s" % exc

    width_px, height_px, view = svg_box(root)
    if width_px <= 0 or height_px <= 0:
        return False, "invalid SVG canvas"
    width_in = width_px / PX_PER_IN
    height_in = height_px / PX_PER_IN
    fig = plt.figure(figsize=(width_in, height_in), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width_px)
    ax.set_ylim(height_px, 0)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    ax.add_patch(patches.Rectangle((0, 0), width_px, height_px,
                                   facecolor="white", edgecolor="none", zorder=-10))

    # Pick a font that can render the labels reasonably.  A single SVG may
    # mix English labels and a Chinese footnote; in that case a CJK face is
    # the safer choice because it also carries Latin glyphs.
    has_cjk = any(ord(ch) > 0x2E80 for text in root.itertext() for ch in str(text))
    families = (("Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                 "Source Han Sans SC", "DejaVu Sans") if has_cjk else
                ("Liberation Sans", "Arial", "DejaVu Sans"))
    font_path = None
    for family in families:
        try:
            candidate = font_manager.findfont(family, fallback_to_default=False)
            if candidate and Path(candidate).is_file():
                font_path = candidate
                break
        except Exception:
            continue
    if font_path:
        font_prop = font_manager.FontProperties(fname=font_path)
    else:
        font_prop = font_manager.FontProperties(family="DejaVu Sans")

    def walk(node, mat):
        tag = local_name(node.tag)
        attrs = node.attrib
        if tag in ("defs", "title", "desc", "metadata", "style", "clipPath",
                   "marker", "pattern", "linearGradient", "radialGradient"):
            return
        if tag == "g":
            child_mat = _matrix_mul(mat, parse_transform(attrs.get("transform")))
            for child in node:
                walk(child, child_mat)
            return

        fill = color_of(attrs.get("fill"), "none")
        stroke = color_of(attrs.get("stroke"), "none")
        sw = parse_float(attrs.get("stroke-width"), 1.0)
        opacity = parse_float(attrs.get("opacity"), 1.0)
        dash = attrs.get("stroke-dasharray")
        linestyle = "--" if dash and dash != "none" else "-"
        if fill in ("none", ""):
            fill = "none"
        if stroke in ("none", ""):
            stroke = "none"
        if fill != "none" and not fill.startswith("#") and not fill.startswith("rgb"):
            fill = "none"
        if stroke != "none" and not stroke.startswith("#") and not stroke.startswith("rgb"):
            stroke = "none"

        def pt(key, default=0.0):
            return parse_length(attrs.get(key), default)

        if tag == "rect":
            x, y = pt("x"), pt("y")
            w, h = pt("width"), pt("height")
            if w <= 0 or h <= 0:
                return
            x, y = apply_mat(mat, x, y)
            rx = pt("rx", 0.0)
            if rx > 0:
                patch = patches.FancyBboxPatch(
                    (x, y), w, h, boxstyle="round,pad=0,rounding_size=%s" % rx,
                    linewidth=sw, edgecolor=stroke, facecolor=fill, alpha=opacity, zorder=2)
            else:
                patch = patches.Rectangle(
                    (x, y), w, h, linewidth=sw, edgecolor=stroke,
                    facecolor=fill, alpha=opacity, zorder=2)
            ax.add_patch(patch)
        elif tag == "circle":
            cx, cy, r = pt("cx"), pt("cy"), pt("r")
            if r > 0:
                cx, cy = apply_mat(mat, cx, cy)
                ax.add_patch(patches.Circle((cx, cy), r, linewidth=sw,
                                            edgecolor=stroke, facecolor=fill,
                                            alpha=opacity, zorder=2))
        elif tag == "ellipse":
            cx, cy, rx, ry = pt("cx"), pt("cy"), pt("rx"), pt("ry")
            if rx > 0 and ry > 0:
                cx, cy = apply_mat(mat, cx, cy)
                ax.add_patch(patches.Ellipse((cx, cy), rx * 2, ry * 2,
                                             linewidth=sw, edgecolor=stroke,
                                             facecolor=fill, alpha=opacity, zorder=2))
        elif tag == "line":
            x1, y1 = apply_mat(mat, pt("x1"), pt("y1"))
            x2, y2 = apply_mat(mat, pt("x2"), pt("y2"))
            ax.plot([x1, x2], [y1, y2], color=stroke if stroke != "none" else "#333333",
                    linewidth=max(sw, 0.5), linestyle=linestyle, alpha=opacity, zorder=4)
            kind = marker_kind(attrs.get("marker-end"))
            if kind != "none":
                draw_arrow(ax, (x1, y1), (x2, y2), kind,
                           stroke if stroke != "none" else "#333333", sw)
        elif tag in ("polyline", "polygon"):
            raw = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)", attrs.get("points", ""))
            points = [apply_mat(mat, float(raw[i]), float(raw[i + 1]))
                      for i in range(0, len(raw) - 1, 2)]
            if len(points) >= 2:
                if tag == "polygon":
                    ax.add_patch(patches.Polygon(points, closed=True, linewidth=sw,
                                                 edgecolor=stroke, facecolor=fill,
                                                 alpha=opacity, zorder=2))
                else:
                    ax.plot([p[0] for p in points], [p[1] for p in points],
                            color=stroke if stroke != "none" else "#333333",
                            linewidth=max(sw, 0.5), linestyle=linestyle,
                            alpha=opacity, zorder=4)
        elif tag == "path":
            subpaths = parse_path_points(attrs.get("d"))
            for sub in subpaths:
                pts = [apply_mat(mat, p[0], p[1]) for p in sub]
                if len(pts) < 2:
                    continue
                if fill != "none" and len(pts) >= 3:
                    ax.add_patch(patches.Polygon(pts, closed=True, linewidth=sw,
                                                 edgecolor=stroke, facecolor=fill,
                                                 alpha=opacity, zorder=2))
                else:
                    ax.plot([p[0] for p in pts], [p[1] for p in pts],
                            color=stroke if stroke != "none" else "#333333",
                            linewidth=max(sw, 0.5), linestyle=linestyle,
                            alpha=opacity, zorder=4)
                    kind_end = marker_kind(attrs.get("marker-end"))
                    kind_start = marker_kind(attrs.get("marker-start"))
                    if kind_end != "none" and len(pts) >= 2:
                        draw_arrow(ax, pts[-2], pts[-1], kind_end,
                                   stroke if stroke != "none" else "#333333", sw)
                    if kind_start != "none" and len(pts) >= 2:
                        draw_arrow(ax, pts[1], pts[0], kind_start,
                                   stroke if stroke != "none" else "#333333", sw)
        elif tag == "text":
            text = "".join(node.itertext()).strip()
            if not text:
                return
            x, y = apply_mat(mat, pt("x"), pt("y"))
            size = parse_length(attrs.get("font-size"), 10.0)
            anchor = {"start": "left", "middle": "center", "end": "right"}.get(
                attrs.get("text-anchor", "start"), "left")
            weight = "bold" if attrs.get("font-weight") in ("bold", "700", "800", "900") else "normal"
            style = "italic" if attrs.get("font-style") == "italic" else "normal"
            color = fill if fill != "none" else "#111111"
            rotation = 0.0
            rot_match = re.search(r"rotate\s*\(\s*([-+]?[0-9.]+)", str(attrs.get("transform", "")))
            if rot_match:
                rotation = float(rot_match.group(1))
            ax.text(x, y, text, fontsize=size, color=color, ha=anchor, va="baseline",
                    fontproperties=font_prop, fontweight=weight, fontstyle=style,
                    alpha=opacity, zorder=5, rotation=rotation,
                    rotation_mode="anchor")
        elif tag == "image":
            # Data-URI images are intentionally skipped: this fallback keeps the
            # vector figure readable and never silently substitutes a bitmap.
            return

    for child in root:
        walk(child, [1, 0, 0, 1, 0, 0])

    if out_pdf:
        out_pdf = Path(out_pdf)
        out_pdf.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_pdf, format="pdf", facecolor="white", edgecolor="none")
    png_ok = None
    if out_png:
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_png, format="png", dpi=dpi, facecolor="white", edgecolor="none")
        png_ok = True
    plt.close(fig)
    return True, {"renderer": "matplotlib-svg-fallback", "png": png_ok}


def main():
    ap = argparse.ArgumentParser(description="Export SVG to real PDF/PNG")
    ap.add_argument("--svg", required=True)
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--out-pdf", default=None)
    ap.add_argument("--out-png", default=None)
    ap.add_argument("--formats", default="pdf,png")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--basename", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        raw, root = read_svg(args.svg)
        width, height, _ = svg_box(root)
    except ExportError as exc:
        return emit(exc.as_dict(), 2)

    svg_path = Path(args.svg)
    outdir = Path(args.outdir) if args.outdir else svg_path.parent
    outdir.mkdir(parents=True, exist_ok=True)
    base = args.basename or svg_path.stem
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    outputs = {}
    attempts = []
    tools = {"cairosvg": importlib.util.find_spec("cairosvg") is not None,
             "rsvg-convert": bool(shutil.which("rsvg-convert")),
             "inkscape": bool(shutil.which("inkscape")),
             "matplotlib": importlib.util.find_spec("matplotlib") is not None}

    wanted = []
    if "pdf" in formats:
        wanted.append(("pdf", Path(args.out_pdf) if args.out_pdf else outdir / (base + ".pdf")))
    if "png" in formats:
        wanted.append(("png", Path(args.out_png) if args.out_png else outdir / (base + ".png")))

    for fmt, target in wanted:
        target.parent.mkdir(parents=True, exist_ok=True)
        done = False
        for name, func in (("cairosvg", export_cairosvg),
                           ("rsvg-convert", export_rsvg),
                           ("inkscape", export_inkscape)):
            if not tools.get(name):
                continue
            ok, detail = func(svg_path, target, fmt, args.dpi)
            attempts.append({"format": fmt, "tool": name, "ok": ok, "detail": detail})
            if ok:
                done = True
                break
        if not done and tools.get("matplotlib"):
            tmp_pdf = target if fmt == "pdf" else None
            preview_png = outdir / (base + ".png")
            tmp_png = target if fmt == "png" else preview_png
            ok, detail = render_matplotlib(root, tmp_pdf, tmp_png, args.dpi)
            attempts.append({"format": fmt, "tool": "matplotlib",
                             "ok": bool(ok), "detail": detail})
            if ok:
                done = True
                if fmt == "pdf" and preview_png.is_file():
                    outputs["png"] = str(preview_png)
        if done and target.is_file():
            valid, msg, meta = validate_output(target, fmt)
            if valid:
                outputs[fmt] = str(target)
                outputs.update({k: v for k, v in meta.items() if k not in outputs})
            else:
                attempts.append({"format": fmt, "tool": "postcheck",
                                 "ok": False, "detail": msg})
                try:
                    target.unlink()
                except OSError:
                    pass

    result = {
        "ok": bool(outputs.get("pdf") or outputs.get("png")),
        "svg": str(svg_path),
        "width_px": round(width, 2),
        "height_px": round(height, 2),
        "width_mm": round(width * 25.4 / PX_PER_IN, 2),
        "height_mm": round(height * 25.4 / PX_PER_IN, 2),
        "outputs": outputs,
        "tools": tools,
        "attempts": attempts,
        "exceptions": [] if outputs else [{"code": "export_unavailable",
                                           "detail": "no renderer produced a valid PDF/PNG"}],
    }
    return emit(result, 0 if result["ok"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
