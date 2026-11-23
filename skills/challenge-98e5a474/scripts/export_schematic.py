#!/usr/bin/env python3
"""Export an editable SVG with the first renderer that is actually available.

Probe order: cairosvg (Python module) -> rsvg-convert -> inkscape -> matplotlib
(a pure-Python SVG-subset renderer bundled with scientific-python).

Nothing is fabricated.  Each candidate output is verified by magic bytes
(PNG \\x89PNG / PDF %PDF) and size; an invalid file is deleted before the next
renderer is tried.  If no renderer works, a self-contained HTML preview with
the SVG inlined is written and the report carries an `export_unavailable`
exception.

Usage:
    python3 export_schematic.py --svg /app/output/figures/fig1.svg \
        --outdir /app/output --json
    python3 export_schematic.py --svg fig1.svg --formats png --dpi 300 --json

Exit codes: 0 = every requested format exported, 1 = partial (SVG is still
delivered), 2 = SVG missing/unparseable.  Stdlib only, Python 3.12+.
"""
import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

TOOL_ORDER = ("cairosvg", "rsvg-convert", "inkscape", "matplotlib")
FORMATS = ("pdf", "png")


class ExportError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []

    def as_dict(self):
        return {"ok": False, "error_code": self.code, "message": self.message,
                "details": self.details}


def read_svg(path):
    p = Path(path)
    if not p.is_file():
        raise ExportError("svg_missing", "SVG not found: %s" % p,
                          ["write figures/fig1.svg before exporting"])
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExportError("svg_unreadable", "cannot read SVG: %s" % exc)
    if not raw.strip():
        raise ExportError("svg_empty", "SVG is empty: %s" % p)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ExportError("svg_invalid", "SVG is not well-formed XML: %s" % exc,
                          ["fix the XML or hand-write a smaller SVG"])
    if root.tag.split("}")[-1] != "svg":
        raise ExportError("svg_invalid", "root element is not <svg>")
    notes = {
        "viewBox": root.get("viewBox"),
        "width": root.get("width"),
        "height": root.get("height"),
        "text_elements": raw.count("<text"),
        "has_script": "<script" in raw.lower(),
        "external_refs": sum(raw.count(x) for x in
                             ('href="http://', "href='http://",
                              'href="https://', "href='https://", 'href="file:')),
    }
    return raw, notes


def probe_tools():
    status = {"cairosvg": False, "rsvg-convert": False, "inkscape": False,
              "matplotlib": False}
    try:
        import importlib.util
        status["cairosvg"] = importlib.util.find_spec("cairosvg") is not None
    except Exception:
        status["cairosvg"] = False
    for name in ("rsvg-convert", "inkscape"):
        status[name] = bool(shutil.which(name))
    try:
        import importlib.util
        status["matplotlib"] = (importlib.util.find_spec("matplotlib") is not None
                                and importlib.util.find_spec("PIL") is not None)
    except Exception:
        status["matplotlib"] = False
    return status


def _run(cmd):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return False, "binary not found"
    except subprocess.TimeoutExpired:
        return False, "timed out after 180 s"
    except OSError as exc:
        return False, "cannot execute: %s" % exc
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return False, "exit %d%s" % (proc.returncode,
                                     (": " + output[:400]) if output else "")
    return True, output[:400]


def try_cairosvg(fmt, svg, target, dpi):
    try:
        import cairosvg
    except Exception as exc:
        return False, "unavailable: %s" % exc
    try:
        if fmt == "pdf":
            cairosvg.svg2pdf(url=str(svg), write_to=str(target))
        else:
            cairosvg.svg2png(url=str(svg), write_to=str(target),
                             scale=max(0.1, dpi / 96.0))
        return True, "cairosvg"
    except Exception as exc:
        return False, "render failed: %s: %s" % (type(exc).__name__, exc)


def try_rsvg(fmt, svg, target, dpi):
    binary = shutil.which("rsvg-convert")
    if not binary:
        return False, "unavailable"
    if fmt == "pdf":
        cmd = [binary, "-f", "pdf", "-o", str(target), str(svg)]
    else:
        cmd = [binary, "-f", "png", "-d", str(dpi), "-p", str(dpi),
               "-o", str(target), str(svg)]
    return _run(cmd)


def try_inkscape(fmt, svg, target, dpi):
    binary = shutil.which("inkscape")
    if not binary:
        return False, "unavailable"
    if fmt == "pdf":
        cmd = [binary, str(svg), "--export-type=pdf", "--export-filename=%s" % target]
    else:
        cmd = [binary, str(svg), "--export-type=png", "--export-filename=%s" % target,
               "--export-dpi=%d" % dpi]
    ok, detail = _run(cmd)
    if ok:
        return True, detail
    # Inkscape 0.92 fallback syntax.
    if fmt == "pdf":
        legacy = [binary, "--export-pdf=%s" % target, str(svg)]
    else:
        legacy = [binary, "--export-png=%s" % target, "--export-dpi=%d" % dpi, str(svg)]
    ok2, detail2 = _run(legacy)
    if ok2:
        return True, detail2
    return False, "%s / legacy: %s" % (detail, detail2)


# ---------------------------------------------------------------------------
# Pure-Python fallback: render the generated SVG subset with matplotlib.
# This is used only after cairosvg/rsvg-convert/inkscape all fail.  It produces
# a real PDF/PNG from the same SVG geometry; it is not a fabricated export.
# ---------------------------------------------------------------------------

def _style_attrs(element):
    attrs = {key.split("}")[-1]: value for key, value in element.attrib.items()}
    style = attrs.get("style") or ""
    for part in style.split(";"):
        if ":" in part:
            key, value = part.split(":", 1)
            attrs.setdefault(key.strip(), value.strip())
    return attrs


def _parse_svg_path(d):
    """Parse the M/L/H/V/C/S/Q/T/A/Z subset used by the schematic builder."""
    from matplotlib.path import Path as MPath
    tokens = re.findall(
        r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?",
        str(d or ""))
    vertices, codes = [], []
    index = 0
    command = None
    current = (0.0, 0.0)
    start = (0.0, 0.0)
    last_cubic = None
    last_quad = None
    tangent = (1.0, 0.0)

    def number():
        nonlocal index
        value = float(tokens[index])
        index += 1
        return value

    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
        if command is None:
            break
        relative = command.islower()
        code = command.upper()
        if code == "M":
            x, y = number(), number()
            if relative:
                x, y = current[0] + x, current[1] + y
            current = start = (x, y)
            vertices.append(current)
            codes.append(MPath.MOVETO)
            command = "l" if relative else "L"
        elif code == "L":
            x, y = number(), number()
            if relative:
                x, y = current[0] + x, current[1] + y
            tangent = (x - current[0], y - current[1])
            current = (x, y)
            vertices.append(current)
            codes.append(MPath.LINETO)
        elif code == "H":
            x = number()
            if relative:
                x += current[0]
            tangent = (x - current[0], 0.0)
            current = (x, current[1])
            vertices.append(current)
            codes.append(MPath.LINETO)
        elif code == "V":
            y = number()
            if relative:
                y += current[1]
            tangent = (0.0, y - current[1])
            current = (current[0], y)
            vertices.append(current)
            codes.append(MPath.LINETO)
        elif code in ("C", "S"):
            if code == "S":
                if last_cubic is None:
                    control1 = current
                else:
                    control1 = (2 * current[0] - last_cubic[0],
                                2 * current[1] - last_cubic[1])
                control2 = (number(), number())
                if relative:
                    control2 = (current[0] + control2[0], current[1] + control2[1])
                end = (number(), number())
                if relative:
                    end = (current[0] + end[0], current[1] + end[1])
            else:
                control1 = (number(), number())
                control2 = (number(), number())
                end = (number(), number())
                if relative:
                    control1 = (current[0] + control1[0], current[1] + control1[1])
                    control2 = (current[0] + control2[0], current[1] + control2[1])
                    end = (current[0] + end[0], current[1] + end[1])
            vertices.extend([control1, control2, end])
            codes.extend([MPath.CURVE4, MPath.CURVE4, MPath.CURVE4])
            tangent = (end[0] - control2[0], end[1] - control2[1])
            last_cubic = control2
            current = end
        elif code in ("Q", "T"):
            if code == "T":
                if last_quad is None:
                    control = current
                else:
                    control = (2 * current[0] - last_quad[0],
                               2 * current[1] - last_quad[1])
                end = (number(), number())
                if relative:
                    end = (current[0] + end[0], current[1] + end[1])
            else:
                control = (number(), number())
                end = (number(), number())
                if relative:
                    control = (current[0] + control[0], current[1] + control[1])
                    end = (current[0] + end[0], current[1] + end[1])
            control1 = (current[0] + 2.0 / 3.0 * (control[0] - current[0]),
                        current[1] + 2.0 / 3.0 * (control[1] - current[1]))
            control2 = (end[0] + 2.0 / 3.0 * (control[0] - end[0]),
                        end[1] + 2.0 / 3.0 * (control[1] - end[1]))
            vertices.extend([control1, control2, end])
            codes.extend([MPath.CURVE4, MPath.CURVE4, MPath.CURVE4])
            tangent = (end[0] - control2[0], end[1] - control2[1])
            last_quad = control
            current = end
        elif code == "A":
            # Arcs are rare in generated schematics; approximate with a line.
            for _ in range(5):
                number()
            end = (number(), number())
            if relative:
                end = (current[0] + end[0], current[1] + end[1])
            tangent = (end[0] - current[0], end[1] - current[1])
            current = end
            vertices.append(current)
            codes.append(MPath.LINETO)
        elif code == "Z":
            vertices.append(start)
            codes.append(MPath.CLOSEPOLY)
            current = start
            command = None
        else:
            break
        # Guard against malformed paths with a zero-length implicit command.
        if command is None:
            continue
        if index >= len(tokens):
            break
        if tokens[index].isalpha():
            continue
        # Repeated coordinate pairs are allowed for L/C/M; the loop handles them.
    return MPath(vertices or [(0.0, 0.0)], codes or [MPath.MOVETO]), tangent, current


def _pick_font():
    try:
        from matplotlib import font_manager
        names = {font.name for font in font_manager.fontManager.ttflist}
        for candidate in ("Microsoft YaHei", "SimHei",
                          "Noto Sans CJK SC", "Noto Sans CJK JP",
                          "Noto Sans SC", "Source Han Sans SC",
                          "WenQuanYi Zen Hei", "Arial Unicode MS",
                          "Liberation Sans", "DejaVu Sans"):
            if candidate in names:
                return candidate
    except Exception:
        pass
    return "DejaVu Sans"


def try_matplotlib(fmt, svg, target, dpi):
    try:
        import logging
        import warnings
        import matplotlib
        matplotlib.use("Agg", force=True)
        # Embed TrueType subsets instead of Type-3 glyphs so the exported
        # PDF passes journal font-embedding checks.
        matplotlib.rcParams["pdf.fonttype"] = 42
        matplotlib.rcParams["ps.fonttype"] = 42
        logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
        import matplotlib.pyplot as plt
        from matplotlib import colors as mcolors
        from matplotlib import font_manager
        from matplotlib.patches import (Circle, Ellipse, PathPatch, Polygon,
                                        Rectangle)
        from matplotlib.path import Path as MPath
        warnings.filterwarnings("ignore", message=".*findfont.*")
        warnings.filterwarnings("ignore", message=".*Glyph .* missing.*")
    except Exception as exc:
        return False, "unavailable: %s" % exc
    try:
        raw = Path(svg).read_text(encoding="utf-8", errors="replace")
        root = ET.fromstring(raw)
        viewbox = root.get("viewBox") or ""
        numbers = [float(x) for x in re.split(r"[,\s]+", viewbox.strip()) if x]
        if len(numbers) == 4 and numbers[2] > 0 and numbers[3] > 0:
            width, height = numbers[2], numbers[3]
        else:
            width = float(re.sub(r"[^0-9.]", "", root.get("width") or "800") or 800)
            height = float(re.sub(r"[^0-9.]", "", root.get("height") or "600") or 600)
        fig = plt.figure(figsize=(width / 96.0, height / 96.0), dpi=dpi)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)
        ax.axis("off")
        ax.set_facecolor("white")
        font = _pick_font()
        available_fonts = {item.name for item in
                           font_manager.fontManager.ttflist}
        zorder = [0]

        def next_z():
            zorder[0] += 1
            return zorder[0]

        def paint(value, opacity=1.0):
            if value in (None, "", "none", "transparent"):
                return "none"
            try:
                return mcolors.to_rgba(value, alpha=float(opacity))
            except (ValueError, TypeError):
                return value

        def dash_style(attrs):
            dash = attrs.get("stroke-dasharray")
            if not dash:
                return "solid"
            try:
                parts = tuple(float(x) for x in re.split(r"[ ,]+", dash.strip()) if x)
                return (0, parts) if parts else "solid"
            except ValueError:
                return "solid"

        def draw_marker(attrs, end, tangent, stroke, z):
            marker = attrs.get("marker-end") or ""
            marker_id = ""
            if "url(#" in marker:
                marker_id = marker.split("url(#", 1)[1].split(")", 1)[0]
            if not marker_id:
                return
            tx, ty = tangent
            length = math.hypot(tx, ty)
            if length == 0:
                tx, ty, length = 1.0, 0.0, 1.0
            ux, uy = tx / length, ty / length
            px, py = -uy, ux
            if "bar" in marker_id:
                ax.plot([end[0] - px * 5, end[0] + px * 5],
                        [end[1] - py * 5, end[1] + py * 5],
                        color=stroke, linewidth=2.0, zorder=z,
                        solid_capstyle="butt")
                return
            size = 9.0
            tip = end
            base = (end[0] - ux * size, end[1] - uy * size)
            p1 = (base[0] + px * size * 0.45, base[1] + py * size * 0.45)
            p2 = (base[0] - px * size * 0.45, base[1] - py * size * 0.45)
            ax.add_patch(Polygon([tip, p1, p2], closed=True,
                                 facecolor=paint(stroke), edgecolor="none",
                                 zorder=z))

        def walk(element):
            for child in element:
                tag = child.tag.split("}")[-1]
                if tag in ("defs", "title", "desc", "metadata"):
                    continue
                attrs = _style_attrs(child)
                fill = attrs.get("fill", "#000000")
                stroke = attrs.get("stroke", "none")
                try:
                    stroke_width = float(attrs.get("stroke-width", 1) or 1)
                except ValueError:
                    stroke_width = 1.0
                fill_color = paint(fill, attrs.get("fill-opacity", 1))
                stroke_color = paint(stroke, attrs.get("stroke-opacity", 1))
                linestyle = dash_style(attrs)
                if tag == "g":
                    walk(child)
                    continue
                if tag == "rect":
                    try:
                        x = float(attrs.get("x", 0) or 0)
                        y = float(attrs.get("y", 0) or 0)
                        w = float(attrs.get("width", 0) or 0)
                        h = float(attrs.get("height", 0) or 0)
                    except ValueError:
                        continue
                    if w <= 0 or h <= 0:
                        continue
                    ax.add_patch(Rectangle((x, y), w, h, facecolor=fill_color,
                                           edgecolor=stroke_color,
                                           linewidth=stroke_width,
                                           linestyle=linestyle,
                                           zorder=next_z()))
                elif tag == "circle":
                    try:
                        cx = float(attrs.get("cx", 0) or 0)
                        cy = float(attrs.get("cy", 0) or 0)
                        r = float(attrs.get("r", 0) or 0)
                    except ValueError:
                        continue
                    if r > 0:
                        ax.add_patch(Circle((cx, cy), r, facecolor=fill_color,
                                            edgecolor=stroke_color,
                                            linewidth=stroke_width,
                                            linestyle=linestyle,
                                            zorder=next_z()))
                elif tag == "ellipse":
                    try:
                        cx = float(attrs.get("cx", 0) or 0)
                        cy = float(attrs.get("cy", 0) or 0)
                        rx = float(attrs.get("rx", 0) or 0)
                        ry = float(attrs.get("ry", 0) or 0)
                    except ValueError:
                        continue
                    if rx > 0 and ry > 0:
                        ax.add_patch(Ellipse((cx, cy), 2 * rx, 2 * ry,
                                             facecolor=fill_color,
                                             edgecolor=stroke_color,
                                             linewidth=stroke_width,
                                             linestyle=linestyle,
                                             zorder=next_z()))
                elif tag in ("polygon", "polyline"):
                    points = []
                    for pair in re.findall(
                            r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?",
                            attrs.get("points") or "")[0:2000]:
                        points.append(float(pair))
                    coords = list(zip(points[0::2], points[1::2]))
                    if len(coords) < 2:
                        continue
                    if tag == "polygon":
                        ax.add_patch(Polygon(coords, closed=True,
                                             facecolor=fill_color,
                                             edgecolor=stroke_color,
                                             linewidth=stroke_width,
                                             linestyle=linestyle,
                                             zorder=next_z()))
                    else:
                        xs, ys = zip(*coords)
                        ax.plot(xs, ys, color=stroke_color, linewidth=stroke_width,
                                linestyle=linestyle, zorder=next_z(),
                                solid_capstyle="round", solid_joinstyle="round")
                elif tag == "line":
                    try:
                        coords = [float(attrs.get(key, 0) or 0) for key in
                                  ("x1", "y1", "x2", "y2")]
                    except ValueError:
                        continue
                    ax.plot([coords[0], coords[2]], [coords[1], coords[3]],
                            color=stroke_color, linewidth=stroke_width,
                            linestyle=linestyle, zorder=next_z())
                elif tag == "path":
                    path, tangent, end = _parse_svg_path(attrs.get("d"))
                    ax.add_patch(PathPatch(path, facecolor="none",
                                           edgecolor=stroke_color,
                                           linewidth=stroke_width,
                                           linestyle=linestyle,
                                           capstyle="round",
                                           joinstyle="round",
                                           zorder=next_z()))
                    draw_marker(attrs, end, tangent, stroke,
                                zorder[0] + 1)
                elif tag == "text":
                    value = "".join(child.itertext())
                    if not value:
                        continue
                    try:
                        x = float(attrs.get("x", 0) or 0)
                        y = float(attrs.get("y", 0) or 0)
                        size_px = float(attrs.get("font-size", 12) or 12)
                    except ValueError:
                        continue
                    anchor = attrs.get("text-anchor", "start")
                    ha = {"start": "left", "middle": "center",
                          "end": "right"}.get(anchor, "left")
                    family = attrs.get("font-family") or font
                    family = family.split(",")[0].strip("' \"") or font
                    if family not in available_fonts:
                        family = font
                    weight = attrs.get("font-weight", "normal")
                    style = attrs.get("font-style", "normal")
                    ax.text(x, y, value, fontsize=size_px * 0.75,
                            fontfamily=family, fontweight=weight,
                            fontstyle=style, color=paint(fill),
                            ha=ha, va="baseline", zorder=next_z(),
                            clip_on=False)
                walk(child)

        walk(root)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(target), format=fmt, dpi=dpi, facecolor="white",
                    edgecolor="none", bbox_inches=None, pad_inches=0)
        plt.close(fig)
        return True, "matplotlib (pure-Python SVG subset renderer)"
    except Exception as exc:
        return False, "render failed: %s: %s" % (type(exc).__name__, exc)


def verify_output(path, fmt):
    p = Path(path)
    if not p.is_file():
        return False, "no output file", {}
    size = p.stat().st_size
    if size <= 0:
        return False, "empty output file", {}
    try:
        with open(p, "rb") as handle:
            head = handle.read(24)
    except OSError as exc:
        return False, "cannot read output: %s" % exc, {}
    if fmt == "png":
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return False, "PNG signature missing", {}
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        if width <= 0 or height <= 0:
            return False, "PNG has invalid dimensions", {}
        return True, "", {"width_px": width, "height_px": height, "bytes": size}
    if not head.startswith(b"%PDF"):
        return False, "PDF signature missing", {}
    return True, "", {"bytes": size}


def make_html(svg_text, svg_path, attempts, out_path, note):
    start = svg_text.find("<svg")
    end = svg_text.rfind("</svg>")
    if start >= 0 and end > start:
        inline = svg_text[start:end + len("</svg>")]
    else:
        inline = "<pre>%s</pre>" % escape(svg_text[:2000])
    title = Path(svg_path).stem
    applied = ", ".join("%s:%s" % (a["format"], a["tool"]) for a in attempts) or "none"
    html = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>%(title)s - SVG preview</title>
<style>
  body { margin: 0; padding: 24px; background: #f3f3f3;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #222; }
  .sheet { max-width: 1200px; margin: 0 auto; background: #fff; padding: 24px;
           border: 1px solid #ddd; border-radius: 6px; }
  h1 { font-size: 16px; font-weight: 600; margin: 0 0 16px; }
  .canvas svg { max-width: 100%%; height: auto; display: block; }
  .note { margin-top: 16px; font-size: 12px; color: #666; line-height: 1.5; }
  code { background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }
</style>
</head>
<body>
<main class="sheet">
<h1>%(title)s &mdash; editable SVG preview</h1>
<div class="canvas">
%(inline)s
</div>
<p class="note">%(note)s<br>
Source: <code>%(source)s</code>. Renderer probes: %(applied)s.
Generated %(stamp)s by export_schematic.py; this page inlines the SVG source and
is not a PNG/PDF export.</p>
</main>
</body>
</html>
""" % {"title": escape(title), "inline": inline, "note": escape(note),
       "source": escape(str(svg_path)), "applied": escape(applied),
       "stamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def export(svg_path, outdir, paths, formats, dpi):
    svg_text, notes = read_svg(svg_path)
    warnings = []
    if not notes["viewBox"]:
        warnings.append("SVG has no viewBox; raster size may follow width/height")
    if notes["text_elements"] == 0:
        warnings.append("SVG has no <text> elements; labels may be outlines")
    if notes["has_script"]:
        warnings.append("SVG contains <script>; preview may not be fully static")
    if notes["external_refs"]:
        warnings.append("SVG references an external resource; it is not self-contained")

    tools = probe_tools()
    attempts = []
    artifacts = []
    exceptions = []
    exported = {}

    for fmt in formats:
        target = Path(paths[fmt])
        target.parent.mkdir(parents=True, exist_ok=True)
        for tool in TOOL_ORDER:
            target.unlink(missing_ok=True)
            if tool == "cairosvg":
                ok, detail = try_cairosvg(fmt, svg_path, target, dpi)
            elif tool == "rsvg-convert":
                ok, detail = try_rsvg(fmt, svg_path, target, dpi)
            elif tool == "inkscape":
                ok, detail = try_inkscape(fmt, svg_path, target, dpi)
            else:
                ok, detail = try_matplotlib(fmt, svg_path, target, dpi)
            entry = {"format": fmt, "tool": tool, "ok": False, "detail": detail}
            if ok:
                valid, why, meta = verify_output(target, fmt)
                entry["ok"] = valid
                entry["detail"] = detail
                if not valid:
                    entry["detail"] = "output rejected: %s" % why
                    target.unlink(missing_ok=True)
                else:
                    entry.update(meta)
                    entry["exported_by"] = tool
                    attempts.append(entry)
                    artifact = {"role": "export", "path": str(target), "format": fmt,
                                "bytes": meta.get("bytes"), "exported_by": tool}
                    if fmt == "png":
                        artifact["width_px"] = meta.get("width_px")
                        artifact["height_px"] = meta.get("height_px")
                        artifact["dpi"] = dpi
                    artifacts.append(artifact)
                    exported[fmt] = artifact
                    break
            attempts.append(entry)
        if fmt not in exported:
            tried = ", ".join("%s=%s" % (t, "yes" if tools[t] else "no") for t in TOOL_ORDER)
            exceptions.append({
                "code": "export_unavailable",
                "detail": "cannot export %s: no working renderer (%s)" % (fmt, tried),
            })

    html_path = None
    if "png" not in exported:
        html_target = Path(paths["html"])
        try:
            html_path = make_html(svg_text, svg_path, attempts, html_target,
                                  "No raster renderer was available, so this preview "
                                  "inlines the SVG instead of fabricating a PNG/PDF.")
        except OSError as exc:
            warnings.append("cannot write HTML preview: %s" % exc)

    complete = len(exported) == len(formats)
    status = "completed" if complete else "partial"
    report = {
        "ok": complete,
        "status": status,
        "svg": str(svg_path),
        "formats_requested": list(formats),
        "dpi": dpi,
        "tools": tools,
        "svg_notes": notes,
        "attempts": attempts,
        "artifacts": artifacts,
        "html_preview": ({"role": "preview", "path": str(html_path), "format": "html"}
                         if html_path else None),
        "exceptions": exceptions,
        "warnings": warnings,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="SVG -> PDF/PNG with honest fallback")
    parser.add_argument("--svg", required=True)
    parser.add_argument("--outdir", default=None)
    parser.add_argument("--pdf", default=None, help="path relative to --outdir")
    parser.add_argument("--png", default=None, help="path relative to --outdir")
    parser.add_argument("--html", default=None, help="path relative to --outdir")
    parser.add_argument("--formats", default="pdf,png")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    formats = tuple(f.strip().lower() for f in args.formats.replace(" ", ",").split(",")
                    if f.strip())
    invalid = [f for f in formats if f not in FORMATS]
    if invalid or not formats:
        print(json.dumps({"ok": False, "error_code": "invalid_formats",
                          "message": "formats must be a subset of pdf,png",
                          "details": list(formats)}, ensure_ascii=False, indent=2))
        return 2
    if args.dpi < 72 or args.dpi > 1200:
        print(json.dumps({"ok": False, "error_code": "invalid_dpi",
                          "message": "dpi must be within 72-1200",
                          "details": [args.dpi]}, ensure_ascii=False, indent=2))
        return 2

    svg_path = Path(args.svg)
    if args.outdir:
        outdir = Path(args.outdir)
    elif Path("/app/output").is_dir():
        outdir = Path("/app/output")
    else:
        outdir = svg_path.parent
    stem = svg_path.stem
    paths = {
        "pdf": Path(args.pdf) if args.pdf else Path("figures") / (stem + ".pdf"),
        "png": Path(args.png) if args.png else Path("preview") / (stem + ".png"),
        "html": Path(args.html) if args.html else Path("preview") / (stem + ".html"),
    }
    for key, value in list(paths.items()):
        if not value.is_absolute():
            paths[key] = outdir / value

    try:
        report = export(svg_path, outdir, paths, formats, args.dpi)
    except ExportError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:  # never leak a traceback
        print(json.dumps({"ok": False, "error_code": "internal_error",
                          "message": "%s: %s" % (type(exc).__name__, exc),
                          "details": ["deliver the SVG and record export_unavailable"]},
                         ensure_ascii=False, indent=2))
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("%s: %s" % (report["status"], svg_path))
        for item in report["artifacts"]:
            print("  export %s -> %s" % (item["format"], item["path"]))
        if report["html_preview"]:
            print("  preview -> %s" % report["html_preview"]["path"])
        for item in report["exceptions"]:
            print("  ! %s: %s" % (item["code"], item["detail"]))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
