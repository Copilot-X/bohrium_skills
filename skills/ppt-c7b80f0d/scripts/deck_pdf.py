#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a deck-spec.json to a real PDF and per-slide PNG previews.

This is a portable, spec-faithful renderer for the same JSON used by
make_pptx.py.  It is *not* a PowerPoint renderer and it does not claim to be:
the PDF is generated from the content spec with matplotlib, so text, numbers,
speaker-visible hierarchy and figures stay in sync with the editable PPTX.

matplotlib is an optional dependency, imported through importlib only after a
runtime probe.  If it is unavailable the script returns a structured error and
the skill keeps the editable PPTX as the primary deliverable.

Usage:
    python3 deck_pdf.py --spec deck.json --out preview/presentation.pdf \
        --png-dir preview --dpi 150 --json
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import math
import re
import sys
import textwrap
from pathlib import Path

THEMES = {
    "scholar": {"ink": "#1F2A30", "muted": "#5E6A70", "accent": "#0F6E78",
                "accent2": "#B07D2A", "bg": "#FBFCFC", "panel": "#EAF2F3",
                "line": "#C9D6D9", "white": "#FFFFFF"},
    "editorial": {"ink": "#24211E", "muted": "#665F58", "accent": "#9B3A2E",
                  "accent2": "#3E6B66", "bg": "#FFFDF8", "panel": "#F4EEE5",
                  "line": "#DFD3C3", "white": "#FFFFFF"},
    "graphite": {"ink": "#20252B", "muted": "#626B73", "accent": "#356A8A",
                 "accent2": "#B4884A", "bg": "#FCFCFC", "panel": "#EEF1F4",
                 "line": "#D5DCE2", "white": "#FFFFFF"},
}
_SUB = str.maketrans({
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
})
SIZES = {"16:9": (13.333, 7.5), "4:3": (10.0, 7.5)}


def emit(data, code=0):
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return code


def theme_of(raw):
    base = dict(THEMES["scholar"])
    if isinstance(raw, str):
        base.update(THEMES.get(raw, {}))
    elif isinstance(raw, dict):
        base.update(THEMES.get(str(raw.get("name", "")), {}))
        for k, v in raw.items():
            if k != "name" and v:
                key = str(v)
                if key.startswith("#"):
                    key = key[1:]
                base[k] = "#" + key.lstrip("#").upper() if k in (
                    "ink", "muted", "accent", "accent2", "bg", "panel", "line", "white") else v
    return base


def safe_text(value):
    return str(value or "").replace(" ", " ").translate(_SUB)


def wrap(value, width_chars):
    text = safe_text(value)
    if not text:
        return [""]
    # CJK text has no spaces; wrap by visual character count.  Prefer breaking
    # at whitespace when one exists, otherwise break between CJK characters.
    out = []
    for paragraph in text.split("\n"):
        if len(paragraph) <= width_chars:
            out.append(paragraph)
            continue
        if " " in paragraph:
            out.extend(textwrap.wrap(paragraph, width=max(6, width_chars),
                                     break_long_words=True, break_on_hyphens=False))
        else:
            for i in range(0, len(paragraph), max(6, width_chars)):
                out.append(paragraph[i:i + max(6, width_chars)])
    return out or [""]


def font_prop(font_manager, size, bold=False, italic=False, text=""):
    cjk = any(ord(ch) > 0x2E80 for ch in str(text))
    families = (("Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                 "Source Han Sans SC") if cjk else
                ("DejaVu Sans", "Arial", "Liberation Sans"))
    for family in families:
        try:
            path = font_manager.findfont(family, fallback_to_default=False)
            if path and Path(path).is_file():
                return font_manager.FontProperties(fname=path, size=size,
                                                   weight="bold" if bold else "normal",
                                                   style="italic" if italic else "normal")
        except Exception:
            continue
    return font_manager.FontProperties(family="DejaVu Sans", size=size,
                                       weight="bold" if bold else "normal",
                                       style="italic" if italic else "normal")


def render(spec, out_pdf, png_dir=None, dpi=150):
    if importlib.util.find_spec("matplotlib") is None:
        return False, "matplotlib not installed"
    try:
        matplotlib = importlib.import_module("matplotlib")
        matplotlib.use("Agg")
        plt = importlib.import_module("matplotlib.pyplot")
        patches = importlib.import_module("matplotlib.patches")
        font_manager = importlib.import_module("matplotlib.font_manager")
        pdf_pages = importlib.import_module("matplotlib.backends.backend_pdf").PdfPages
    except Exception as exc:  # noqa: BLE001
        return False, "matplotlib import failed: %s" % exc
    W, H = SIZES.get(spec.get("size", "16:9"), SIZES["16:9"])
    t = theme_of(spec.get("theme"))
    slides = spec.get("slides") or []
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    png_dir = Path(png_dir) if png_dir else None
    if png_dir:
        png_dir.mkdir(parents=True, exist_ok=True)
    return render_pages(slides, out_pdf, png_dir, dpi, plt, patches,
                        font_manager, pdf_pages, W, H, t)


def render_pages(slides, out_pdf, png_dir, dpi, plt, patches, font_manager,
                 pdf_pages, W, H, t):
    """One pass over the slides, writing PDF pages and optional PNGs."""
    pages = []
    with pdf_pages(str(out_pdf)) as pdf:
        for idx, s in enumerate(slides, 1):
            fig = plt.figure(figsize=(W, H), dpi=dpi)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.set_xlim(0, W)
            ax.set_ylim(H, 0)
            ax.set_aspect("equal")
            ax.axis("off")

            def font(size, bold=False, italic=False, text=""):
                return font_prop(font_manager, size, bold, italic, text)

            def rect(x, y, w, h, fill, line=None, radius=0.10, lw=0.8, alpha=1.0):
                if fill and not str(fill).startswith("#"):
                    fill = "#" + str(fill)
                if line and not str(line).startswith("#"):
                    line = "#" + str(line)
                if radius and radius > 0:
                    p = patches.FancyBboxPatch((x + radius, y + radius),
                                               max(0.01, w - 2 * radius),
                                               max(0.01, h - 2 * radius),
                                               boxstyle=f"round,pad={radius},rounding_size={radius}",
                                               linewidth=lw, edgecolor=line or "none",
                                               facecolor=fill or "none", alpha=alpha, zorder=2)
                else:
                    p = patches.Rectangle((x, y), w, h, linewidth=lw,
                                          edgecolor=line or "none", facecolor=fill or "none",
                                          alpha=alpha, zorder=2)
                ax.add_patch(p)

            def txt(x, y, w, h, value, size=16, color=None, bold=False, italic=False,
                    align="left", valign="top", line_spacing=1.22, bullet=False):
                color = color or t["ink"]
                if not str(color).startswith("#"):
                    color = "#" + str(color)
                cjk = any(ord(c) > 0x2E80 for c in safe_text(value))
                chars = max(8, int(w * 72 / max(6.0, size * (1.05 if cjk else 0.56))))
                lines = wrap(value, chars)
                line_h = size * line_spacing / 72.0
                yy = y
                if valign == "center":
                    yy = y + max(0, (h - len(lines) * line_h) / 2)
                xx = x + (w / 2 if align == "center" else (w if align == "right" else 0))
                for i, line in enumerate(lines):
                    label = ("• " if bullet and i == 0 else ("  " if bullet else "")) + line
                    ax.text(xx, yy, label, fontproperties=font(size, bold, italic, label),
                            color=color, ha=align, va="top", zorder=5)
                    yy += line_h
                return yy

            def bullets(x, y, w, h, items, size=16, color=None, gap=0.035):
                if isinstance(items, str):
                    items = [items]
                yy = y
                for item in items or []:
                    if isinstance(item, str):
                        text, sz, bold = item, size, False
                    else:
                        text = item.get("text", "")
                        sz = item.get("size", size)
                        bold = bool(item.get("bold"))
                    yy = txt(x, yy, w, max(0.1, h - (yy - y)), text, sz, color, bold, bullet=True)
                    yy += gap
                    if yy - y > h:
                        break
                return yy

            layout = str(s.get("layout") or s.get("kind") or "content").lower()
            dark = layout in ("title", "closing", "statement", "quote")
            bg = s.get("background") or (t["ink"] if dark else t["bg"])
            ink = s.get("text_color") or (t["white"] if dark else t["ink"])
            muted = s.get("muted_color") or (t["line"] if dark else t["muted"])
            ax.add_patch(patches.Rectangle((0, 0), W, H, facecolor=bg, edgecolor="none", zorder=0))
            m = 0.62

            def source(value):
                if value:
                    ax.text(0.55, H - 0.28, safe_text(value), fontproperties=font(8.5),
                            color=muted, ha="left", va="bottom", zorder=5)

            def page_num():
                ax.text(W - 0.55, H - 0.28, str(idx), fontproperties=font(9),
                        color=muted, ha="right", va="bottom", zorder=5)

            if layout in ("title", "cover"):
                txt(m, 1.20, W - 2 * m, 0.30, s.get("eyebrow", s.get("kicker", "")),
                    12, t["accent2"], True)
                txt(m, 1.75, W * 0.78, 1.30, s.get("title", ""), s.get("title_size", 34), ink, True)
                if s.get("subtitle"):
                    txt(m, 3.55, W * 0.70, 0.80, s["subtitle"], 17, muted)
                source(s.get("source"))
                page_num()
            elif layout in ("section", "divider"):
                if s.get("number"):
                    txt(m, 1.30, 1.0, 1.1, s["number"], 40, t["accent"], True)
                txt(m, 2.55, W - 2 * m, 0.75, s.get("title", ""), s.get("title_size", 30), ink, True)
                if s.get("subtitle"):
                    txt(m, 3.55, W * 0.70, 0.55, s["subtitle"], 16, muted)
                page_num()
            elif layout in ("closing", "end"):
                txt(m, 1.10, W - 2 * m, 0.30, s.get("eyebrow", "TAKE-HOME"), 11, t["accent2"], True)
                txt(m, 1.55, W * 0.80, 0.95, s.get("title", "结论"), s.get("title_size", 30), ink, True)
                bullets(m, 2.80, W * 0.76, 2.10, s.get("bullets") or s.get("takeaways") or [], 16, muted)
                if s.get("subtitle"):
                    txt(m, 6.35, W * 0.76, 0.30, s["subtitle"], 11, muted)
                source(s.get("source"))
                page_num()
            elif layout in ("quote", "statement"):
                txt(m, 1.15, W - 2 * m, 2.80,
                    "“" + safe_text(s.get("quote") or s.get("title", "")) + "”",
                    s.get("title_size", 26), ink, align="center", valign="center")
                if s.get("attribution"):
                    txt(m, 4.60, W - 2 * m, 0.40, "— " + safe_text(s["attribution"]),
                        13, muted, align="right")
                page_num()
            else:
                if s.get("eyebrow"):
                    txt(m, 0.35, W - 2 * m, 0.22, s["eyebrow"], 9.5, t["accent"], True)
                txt(m, 0.58, W - 2 * m, 0.72, s.get("title", ""), s.get("title_size", 25), ink, True)
                y = 1.55
                if layout in ("timeline", "steps", "process"):
                    steps = s.get("steps") or s.get("items") or []
                    n = max(1, len(steps))
                    gap = 0.18
                    cw = (W - 2 * m - gap * (n - 1)) / n
                    for i, step in enumerate(steps):
                        x = m + i * (cw + gap)
                        rect(x, y, cw, 3.35, t["panel"], t["line"], 0.12)
                        ax.add_patch(patches.Circle((x + 0.42, y + 0.52), 0.22,
                                                    facecolor=t["accent"], edgecolor="none", zorder=4))
                        ax.text(x + 0.42, y + 0.52, str(i + 1), fontproperties=font(13, True),
                                color=t["white"], ha="center", va="center", zorder=5)
                        title = step.get("title", step.get("label", "")) if isinstance(step, dict) else str(step)
                        body = step.get("text", step.get("body", "")) if isinstance(step, dict) else ""
                        txt(x + 0.25, y + 0.90, cw - 0.50, 0.70, title, 14, t["ink"], True)
                        bullets(x + 0.25, y + 1.65, cw - 0.50, 2.10, body, 11.5, t["muted"])
                elif layout in ("stat", "stats", "metrics"):
                    stats = s.get("stats") or s.get("metrics") or []
                    n = max(1, len(stats))
                    gap = 0.18
                    cw = (W - 2 * m - gap * (n - 1)) / n
                    for i, item in enumerate(stats):
                        x = m + i * (cw + gap)
                        rect(x, y + 0.30, cw, 2.95, t["panel"], t["line"], 0.12)
                        value = item.get("value", item.get("number", "")) if isinstance(item, dict) else str(item)
                        label = item.get("label", item.get("caption", "")) if isinstance(item, dict) else ""
                        txt(x + 0.20, y + 0.68, cw - 0.40, 1.20, value, 34, t["accent"], True,
                            align="center", valign="center")
                        txt(x + 0.20, y + 2.00, cw - 0.40, 0.45, label, 12, t["muted"], align="center")
                elif layout in ("comparison", "two-column", "two-col"):
                    left = s.get("left") or {}
                    right = s.get("right") or {}
                    gap = 0.20
                    cw = (W - 2 * m - gap) / 2
                    for i, block in enumerate((left, right)):
                        x = m + i * (cw + gap)
                        if isinstance(block, str):
                            block = {"title": "", "bullets": [block]}
                        rect(x, y, cw, 4.35, t["panel"], t["line"], 0.12)
                        ax.add_patch(patches.Circle((x + 0.35, y + 0.38), 0.10,
                                                    facecolor=t["accent"] if i == 0 else t["accent2"],
                                                    edgecolor="none", zorder=4))
                        txt(x + 0.55, y + 0.22, cw - 0.75, 0.40,
                            block.get("title", ["方案 A", "方案 B"][i]), 15, t["ink"], True)
                        bullets(x + 0.30, y + 0.85, cw - 0.60, 3.10, block.get("bullets", []), 13, t["muted"])
                elif layout in ("cards", "grid", "features"):
                    cards = s.get("cards") or []
                    n = max(1, len(cards))
                    gap = 0.16
                    cw = (W - 2 * m - gap * (n - 1)) / n
                    for i, item in enumerate(cards):
                        x = m + i * (cw + gap)
                        rect(x, y, cw, 4.10, t["panel"], t["line"], 0.12)
                        txt(x + 0.22, y + 0.25, cw - 0.44, 0.60, item.get("title", ""), 15, t["ink"], True)
                        bullets(x + 0.22, y + 1.00, cw - 0.44, 2.70,
                                item.get("bullets", item.get("text", [])), 12, t["muted"])
                else:
                    has_img = False
                    if s.get("image"):
                        try:
                            img = plt.imread(str(s["image"]))
                            ih, iw = img.shape[0], img.shape[1]
                            bw, bh = W * 0.36, 4.45
                            scale = min(bw / iw, bh / ih)
                            iw2, ih2 = iw * scale, ih * scale
                            ix = W * 0.57 + (bw - iw2) / 2
                            iy = y + (bh - ih2) / 2
                            ax.imshow(img, extent=(ix, ix + iw2, iy + ih2, iy),
                                      aspect="auto", zorder=3)
                            has_img = True
                        except Exception:
                            rect(W * 0.57, y, W * 0.36, 4.45, t["panel"], t["line"], 0.04)
                    callout = s.get("callout") or {}
                    if callout and not has_img:
                        bx, bw = W - m - W * 0.30, W * 0.30
                        rect(bx, y, bw, 3.55, t["panel"], t["line"], 0.12)
                        txt(bx + 0.22, y + 0.25, bw - 0.44, 0.35,
                            callout.get("title", "关键点"), 13, t["accent"], True)
                        bullets(bx + 0.22, y + 0.80, bw - 0.44, 2.40,
                                callout.get("bullets", callout.get("text", [])), 11.5, t["muted"])
                    body_w = W * 0.54 if (has_img or callout) else W - 2 * m
                    bullets(m, y, body_w, 4.65, s.get("bullets") or [],
                            s.get("body_size", 16), t["muted"])
                if s.get("caption"):
                    txt(m, 6.35, W - 2 * m, 0.30, s["caption"], 9.5, muted)
                source(s.get("source"))
                page_num()

            pdf.savefig(fig, facecolor=bg)
            if png_dir:
                png = png_dir / ("slide-%02d.png" % idx)
                fig.savefig(png, dpi=dpi, facecolor=bg, edgecolor="none")
                pages.append(str(png))
            plt.close(fig)
    return True, {"renderer": "matplotlib-from-spec", "pages": len(slides),
                  "pdf": str(out_pdf), "png": pages}


def main():
    ap = argparse.ArgumentParser(description="deck-spec.json -> PDF/PNG preview")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--png-dir", default=None)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return emit({"ok": False, "error": "cannot read spec: %s" % exc}, 2)
    try:
        ok, detail = render(spec, args.out, args.png_dir, args.dpi)
    except Exception as exc:  # noqa: BLE001 - keep harness alive
        return emit({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}, 1)
    if ok:
        return emit({"ok": True, **detail})
    return emit({"ok": False, "error": detail,
                 "exceptions": [{"code": "render_unavailable", "detail": str(detail)}]}, 1)


if __name__ == "__main__":
    raise SystemExit(main())
