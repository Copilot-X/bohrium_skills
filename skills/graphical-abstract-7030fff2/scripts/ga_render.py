#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic GA rendering engine used by svg_gen.py.

The renderer is intentionally different from a generic node/edge diagram:
it makes 3-5 visual panels with role labels, simple geometric icons, a
left-to-right narrative and a single claim banner.  Every panel and connector
is a named <g>; all text remains <text>.  It can also be run standalone for
diagnosis of an already-normalized spec.

Usage:
    python3 ga_render.py --plan /app/work/ga-spec.json \
        --out /app/output/figures/ga.svg --json
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from xml.sax.saxutils import escape

ROLE_COLORS = {
    "problem": ("#E8F1F5", "#0F6E78"),
    "approach": ("#F7EEDC", "#B07D2A"),
    "method": ("#F7EEDC", "#B07D2A"),
    "result": ("#E4F0E7", "#2F7D4A"),
    "implication": ("#EEE8F5", "#6B4C9A"),
    "conclusion": ("#E4F0E7", "#2F7D4A"),
    "default": ("#F1F4F5", "#4D5B60"),
}
INK = "#1F2A30"
MUTED = "#5E6A70"
WHITE = "#FFFFFF"


def text_width(value, size):
    w = 0.0
    for ch in str(value):
        if ord(ch) > 0x2E80:
            w += size
        elif ch in "il.,:;'|!()[]":
            w += size * 0.34
        elif ch in "mwMW@%":
            w += size * 0.95
        else:
            w += size * 0.56
    return w


def wrap(value, width, size):
    tokens, cur, out = [], "", []
    for ch in str(value):
        if ord(ch) > 0x2E80:
            if cur:
                tokens.append(cur)
                cur = ""
            tokens.append(ch)
        elif ch == " ":
            if cur:
                tokens.append(cur)
            tokens.append(" ")
            cur = ""
        else:
            cur += ch
    if cur:
        tokens.append(cur)
    for tok in tokens:
        cand = cur + tok
        if text_width(cand, size) <= width or not cur:
            cur = cand
        else:
            out.append(cur.rstrip())
            cur = "" if tok == " " else tok
    if cur.strip():
        out.append(cur.rstrip())
    return out or [""]


def panel_icon(role, cx, cy, size, color):
    """Small, self-contained geometric icon; no external assets."""
    parts = []
    if role in ("result", "conclusion", "implication"):
        # rising bars + upward arrow
        bars = [0.35, 0.55, 0.85]
        bw = size * 0.16
        gap = size * 0.14
        start = cx - (len(bars) * bw + (len(bars) - 1) * gap) / 2
        for i, h in enumerate(bars):
            x = start + i * (bw + gap)
            top = cy + size * 0.32 - size * 0.62 * h
            parts.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                         f'height="{size * 0.62 * h:.1f}" rx="{bw * 0.2:.1f}" '
                         f'fill="{color}" fill-opacity="0.88"/>')
        parts.append(f'<path d="M{cx - size * 0.38:.1f},{cy - size * 0.10:.1f} '
                     f'L{cx + size * 0.34:.1f},{cy - size * 0.44:.1f}" '
                     f'stroke="{color}" stroke-width="{max(2, size * 0.055):.1f}" '
                     f'fill="none" stroke-linecap="round"/>')
        parts.append(f'<path d="M{cx + size * 0.34:.1f},{cy - size * 0.44:.1f} '
                     f'l{-size * 0.18:.1f},{size * 0.04:.1f} '
                     f'l{size * 0.04:.1f},{size * 0.18:.1f}" fill="{color}"/>')
    elif role in ("approach", "method"):
        # three linked molecule dots
        pts = [(cx - size * 0.34, cy + size * 0.12),
               (cx, cy - size * 0.22),
               (cx + size * 0.34, cy + size * 0.12)]
        for i in range(len(pts) - 1):
            parts.append(f'<line x1="{pts[i][0]:.1f}" y1="{pts[i][1]:.1f}" '
                         f'x2="{pts[i+1][0]:.1f}" y2="{pts[i+1][1]:.1f}" '
                         f'stroke="{color}" stroke-width="{max(1.5, size * 0.035):.1f}"/>')
        for x, y in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{size * 0.13:.1f}" '
                         f'fill="{color}" fill-opacity="0.88"/>')
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy + size * 0.30:.1f}" '
                     f'r="{size * 0.08:.1f}" fill="{color}" fill-opacity="0.45"/>')
    else:
        # problem: descending trend + question mark
        pts = [(cx - size * 0.38, cy - size * 0.22),
               (cx - size * 0.12, cy - size * 0.02),
               (cx + size * 0.16, cy - size * 0.12),
               (cx + size * 0.38, cy + size * 0.26)]
        path = "M" + " L".join("%.1f,%.1f" % p for p in pts)
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" '
                     f'stroke-width="{max(2, size * 0.055):.1f}" '
                     f'stroke-linecap="round" stroke-linejoin="round"/>')
        parts.append(f'<circle cx="{cx + size * 0.38:.1f}" cy="{cy + size * 0.26:.1f}" '
                     f'r="{size * 0.075:.1f}" fill="{color}"/>')
        # Keep the question mark clear of the trend line: upper-left of the icon.
        parts.append(f'<text x="{cx - size * 0.30:.1f}" y="{cy - size * 0.30:.1f}" '
                     f'font-family="Arial, Liberation Sans, sans-serif" font-size="{size * 0.22:.1f}" '
                     f'font-weight="bold" fill="{color}" text-anchor="middle">?</text>')
    return parts


def build(plan, out_path):
    width = int((plan.get("canvas") or {}).get("width_px") or 1200)
    height = int((plan.get("canvas") or {}).get("height_px") or 600)
    width = max(800, width)
    height = max(400, height)
    margin = int(width * 0.045)
    claim_obj = plan.get("claim") or {}
    claim = claim_obj.get("claim") if isinstance(claim_obj, dict) else str(claim_obj or "")
    claim = claim or plan.get("title") or "研究结论"
    elements = plan.get("elements") or []
    if not elements:
        elements = [{"id": "G1", "role": "problem", "concept": "研究问题"},
                    {"id": "G2", "role": "approach", "concept": "方法"},
                    {"id": "G3", "role": "result", "concept": "核心结果"}]
    elements = elements[:5]
    n = len(elements)
    gap = int(width * 0.025)
    panel_w = (width - 2 * margin - gap * (n - 1)) // n
    panel_y = int(height * 0.25)
    panel_h = int(height * 0.48)
    header_h = int(height * 0.15)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<title>{escape(str(plan.get("case_id") or "Graphical Abstract"))}</title>',
        f'<desc>Single-claim graphical abstract with {n} visual elements.</desc>',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>',
    ]
    # Claim banner: one take-home message, not a title/abstract dump.
    parts.append(f'<rect x="{margin}" y="{int(height * 0.045)}" '
                 f'width="{width - 2 * margin}" height="{header_h}" rx="{header_h * 0.18}" '
                 f'fill="{INK}"/>')
    claim_size = max(16, int(height * 0.042))
    claim_lines = wrap(claim, width - 2 * margin - 36, claim_size)
    claim_y = int(height * 0.045) + header_h * 0.5 - (len(claim_lines) - 1) * claim_size * 0.55
    for i, line in enumerate(claim_lines[:2]):
        parts.append(f'<text x="{width / 2:.1f}" y="{claim_y + i * claim_size * 1.05:.1f}" '
                     f'font-family="Arial, Liberation Sans, sans-serif" font-size="{claim_size}" '
                     f'font-weight="bold" fill="{WHITE}" text-anchor="middle">{escape(line)}</text>')

    id_index = {}
    for i, el in enumerate(elements):
        if not isinstance(el, dict):
            el = {"concept": str(el)}
            elements[i] = el
        id_index[str(el.get("id") or "G%d" % (i + 1))] = i
        eid = str(el.get("id") or "G%d" % (i + 1))
        role = str(el.get("role") or "default").lower()
        fill, accent = ROLE_COLORS.get(role, ROLE_COLORS["default"])
        x = margin + i * (panel_w + gap)
        y = panel_y
        parts.append(f'<g id="{escape(eid)}" data-role="{escape(role)}">')
        parts.append(f'<rect x="{x}" y="{y}" width="{panel_w}" height="{panel_h}" '
                     f'rx="{panel_w * 0.055:.1f}" fill="{fill}" stroke="{accent}" '
                     f'stroke-opacity="0.35" stroke-width="1.2"/>')
        parts.append(f'<text x="{x + panel_w * 0.08:.1f}" y="{y + panel_h * 0.13:.1f}" '
                     f'font-family="Arial, Liberation Sans, sans-serif" font-size="{max(10, height * 0.023):.1f}" '
                     f'font-weight="bold" fill="{accent}" letter-spacing="1.2">'
                     f'{escape(role.upper())}</text>')
        # icon in a light disc
        cx = x + panel_w / 2
        cy = y + panel_h * 0.43
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{panel_h * 0.20:.1f}" '
                     f'fill="{WHITE}" fill-opacity="0.72"/>')
        icon_size = panel_h * 0.34
        parts.extend(panel_icon(role, cx, cy, icon_size, accent))
        concept = str(el.get("concept") or el.get("label") or el.get("title") or "")
        lines = wrap(concept, panel_w * 0.82, max(12, height * 0.028))
        ty = y + panel_h * 0.73
        for line in lines[:3]:
            parts.append(f'<text x="{cx:.1f}" y="{ty:.1f}" font-family="Arial, Liberation Sans, sans-serif" '
                         f'font-size="{max(12, height * 0.030):.1f}" fill="{INK}" '
                         f'text-anchor="middle">{escape(line)}</text>')
            ty += max(14, height * 0.036)
        parts.append('</g>')

    # Narrative relations: every connector is a named <g id="E*">.  Default is
    # one arrow between adjacent panels; a spec may pass explicit relations.
    relations = plan.get("relations")
    if not isinstance(relations, list) or not relations:
        relations = [{"id": "E%d" % (i + 1),
                      "from": str(elements[i].get("id") or "G%d" % (i + 1)),
                      "to": str(elements[i + 1].get("id") or "G%d" % (i + 2))}
                     for i in range(n - 1)]
    for k, rel in enumerate(relations[:12]):
        if not isinstance(rel, dict):
            continue
        rid = str(rel.get("id") or "E%d" % (k + 1))
        src_i = id_index.get(str(rel.get("from")))
        dst_i = id_index.get(str(rel.get("to")))
        if src_i is None or dst_i is None or src_i == dst_i:
            continue
        label = str(rel.get("label") or "")
        parts.append(f'<g id="{escape(rid)}" data-role="relation">')
        if dst_i == src_i + 1:
            ax1 = margin + src_i * (panel_w + gap) + panel_w + gap * 0.18
            ax2 = margin + src_i * (panel_w + gap) + panel_w + gap * 0.82
            ay = panel_y + panel_h * 0.5
            parts.append(f'<line x1="{ax1:.1f}" y1="{ay:.1f}" x2="{ax2:.1f}" y2="{ay:.1f}" '
                         f'stroke="{MUTED}" stroke-width="2" stroke-linecap="round"/>'
                         f'<path d="M{ax2:.1f},{ay:.1f} l{-7:.1f},{-4:.1f} l0,8 z" fill="{MUTED}"/>')
            if label:
                parts.append(f'<text x="{(ax1 + ax2) / 2:.1f}" '
                             f'y="{panel_y + panel_h + height * 0.030:.1f}" '
                             f'font-family="Arial, Liberation Sans, sans-serif" font-size="{max(9, height * 0.018):.1f}" '
                             f'fill="{MUTED}" text-anchor="middle">{escape(label)}</text>')
        else:
            x1 = margin + src_i * (panel_w + gap) + panel_w / 2
            x2 = margin + dst_i * (panel_w + gap) + panel_w / 2
            y0 = panel_y + panel_h
            ymid = y0 + max(18.0, (height - y0 - margin) * 0.42)
            parts.append(f'<path d="M{x1:.1f},{y0:.1f} L{x1:.1f},{ymid:.1f} '
                         f'L{x2:.1f},{ymid:.1f} L{x2:.1f},{y0:.1f}" fill="none" '
                         f'stroke="{MUTED}" stroke-width="2" stroke-linejoin="round"/>')
            parts.append(f'<path d="M{x2:.1f},{y0:.1f} l-4.5,-7.5 l9,0 z" fill="{MUTED}"/>')
            if label:
                parts.append(f'<text x="{(x1 + x2) / 2:.1f}" y="{ymid - 5:.1f}" '
                             f'font-family="Arial, Liberation Sans, sans-serif" font-size="{max(9, height * 0.018):.1f}" '
                             f'fill="{MUTED}" text-anchor="middle">{escape(label)}</text>')
        parts.append('</g>')
    # Footer source/scope line, only if explicitly supplied; never invent one.
    source = plan.get("source") or plan.get("journal_spec", {}).get("source")
    if source:
        parts.append(f'<text x="{margin}" y="{height - margin * 0.45:.1f}" '
                     f'font-family="Arial, Liberation Sans, sans-serif" font-size="{max(9, height * 0.018):.1f}" '
                     f'fill="{MUTED}">{escape(str(source))}</text>')
    parts.append('</svg>')
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(out), "elements": n,
            "width_px": width, "height_px": height,
            "bytes": out.stat().st_size, "text_elements": "\n".join(parts).count("<text")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    res = build(plan, args.out)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
