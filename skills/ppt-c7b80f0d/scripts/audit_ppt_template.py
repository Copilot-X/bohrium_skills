#!/usr/bin/env python3
"""Audit a PowerPoint template before adapting content to it (stdlib only).

Reports the facts that a faithful adaptation depends on: slide size, masters,
real layout names, placeholder idx/type/geometry, theme colour scheme, theme
font scheme, explicit fonts used by layouts, sample slides that must be deleted,
media and notes-master presence.

Optionally maps the audited layouts to content roles and emits a starter format
spec (--emit-spec) that can be copied and edited before running adapt_deck.py.

Usage:
    audit_ppt_template.py --template template.pptx --out template-audit.json --json
    audit_ppt_template.py --template template.pptx --emit-spec ppt-format-spec.json
    audit_ppt_template.py --template template.pptx --available-fonts fonts.txt

--available-fonts accepts either a file (one font per line / comma separated)
or an inline comma-separated list, e.g. "$(font-discovery --list)".
Exit codes: 0 = audited, 2 = unreadable/invalid template.
"""
import argparse
import json
import re
import statistics
import sys
import zipfile
from pathlib import Path

import xml.etree.ElementTree as ET

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = {"p": P_NS, "a": A_NS}
EMU_IN = 914400
EMU_PT = 12700
LAYOUT_RE = re.compile(r"^ppt/slideLayouts/slideLayout(\d+)\.xml$")
CONTROL_PH = {"dt", "ftr", "sldNum", "hdr", "sldImg"}
REL_RE = re.compile(r"<Relationship\b([^>]*)/>")
T_RE = re.compile(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.S)

MONO_HINTS = ("mono", "consolas", "courier", "menlo", "code")
SERIF_HINTS = ("serif", "times", "song", "ming", "garamond", "georgia",
               "cambria", "simsun", "stsong", "book")
CJK_HINTS = ("cjk", "hei", "song", "ming", "kai", "yahei", "simsun", "simhei",
             "noto sans sc", "source han", "pingfang")
COMMON_SANS = ["Liberation Sans", "DejaVu Sans", "Arial", "Calibri", "Helvetica"]
COMMON_SERIF = ["Liberation Serif", "DejaVu Serif", "Times New Roman", "Georgia"]
COMMON_CJK_SANS = ["Noto Sans CJK SC", "Source Han Sans SC", "SimHei",
                   "Microsoft YaHei", "WenQuanYi Zen Hei"]
COMMON_CJK_SERIF = ["Noto Serif CJK SC", "Source Han Serif SC", "SimSun",
                    "STSong", "Songti SC"]
COMMON_MONO = ["DejaVu Sans Mono", "Liberation Mono", "Courier New"]


def read_parts(path):
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def parse_xml(raw, name):
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"{name} is not valid XML: {exc}") from exc


def resolve_target(folder, target):
    if target.startswith("/"):
        return target.lstrip("/")
    stack = [p for p in folder.split("/") if p]
    for piece in target.replace("\\", "/").split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if stack:
                stack.pop()
        else:
            stack.append(piece)
    return "/".join(stack)


def rels_of(parts, part_name):
    folder, fname = part_name.rsplit("/", 1)
    raw = parts.get(f"{folder}/_rels/{fname}.rels", b"").decode("utf-8", "replace")
    out = []
    for m in REL_RE.finditer(raw):
        out.append(dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1))))
    return out


def ordered_slides(parts):
    pres = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    ids = re.findall(r'<p:sldId\b[^>]*r:id="([^"]+)"', pres)
    mapping = {r.get("Id"): r.get("Target", "")
               for r in rels_of(parts, "ppt/presentation.xml")}
    out = []
    for rid in ids:
        target = mapping.get(rid)
        if not target:
            continue
        part = resolve_target("ppt", target)
        if part in parts:
            out.append(part)
    if not out:
        nums = sorted((int(re.search(r"slide(\d+)", n).group(1)), n)
                      for n in parts if re.match(r"^ppt/slides/slide\d+\.xml$", n))
        out = [n for _, n in nums]
    return out


def slide_size(parts):
    raw = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    m = re.search(r"<p:sldSz\b([^>]*)/>", raw)
    if not m:
        return None
    attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
    cx, cy = int(attrs.get("cx", 0)), int(attrs.get("cy", 0))
    ratio = (cx / cy) if cy else 0
    label = "16:9" if abs(ratio - 16 / 9) < 0.02 else \
            "4:3" if abs(ratio - 4 / 3) < 0.02 else f"{ratio:.3f}"
    return {"cx": cx, "cy": cy, "type": attrs.get("type", ""),
            "inches": {"cx": round(cx / EMU_IN, 3), "cy": round(cy / EMU_IN, 3)},
            "aspect_ratio": label}


def parse_theme(parts):
    for name in sorted(n for n in parts if re.match(r"^ppt/theme/theme\d+\.xml$", n)):
        root = parse_xml(parts[name], name)
        el = root.find("a:themeElements", NS)
        if el is None:
            continue
        colors, fonts = {}, {}
        clr = el.find("a:clrScheme", NS)
        if clr is not None:
            for child in clr:
                tag = child.tag.rsplit("}", 1)[-1]
                srgb = child.find("a:srgbClr", NS)
                sysclr = child.find("a:sysClr", NS)
                if srgb is not None:
                    colors[tag] = srgb.get("val", "").upper()
                elif sysclr is not None:
                    colors[tag] = (sysclr.get("lastClr") or sysclr.get("val") or "").upper()
        fscheme = el.find("a:fontScheme", NS)
        if fscheme is not None:
            for which in ("majorFont", "minorFont"):
                node = fscheme.find(f"a:{which}", NS)
                if node is None:
                    continue
                fonts[which] = {}
                for face in ("latin", "ea", "cs"):
                    sub = node.find(f"a:{face}", NS)
                    if sub is not None and sub.get("typeface"):
                        fonts[which][face] = sub.get("typeface")
        return {"part": name, "name": root.get("name", ""),
                "colors": colors, "fonts": fonts}
    return {}


def box_of(sp):
    xfrm = sp.find("./p:spPr/a:xfrm", NS)
    if xfrm is None:
        return None
    off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
    if off is None or ext is None:
        return None
    box = {"x": int(off.get("x", 0)), "y": int(off.get("y", 0)),
           "cx": int(ext.get("cx", 0)), "cy": int(ext.get("cy", 0))}
    box["inches"] = {k: round(box[k] / EMU_IN, 3) for k in ("x", "y", "cx", "cy")}
    return box


def sizes_in(xml_text):
    return [int(s) / 100 for s in re.findall(r'\bsz="(\d+)"', xml_text)]


def parse_layouts(parts):
    layouts, warnings = [], []
    for name in parts:
        m = LAYOUT_RE.match(name)
        if not m:
            continue
        raw = parts[name].decode("utf-8", "replace")
        root = parse_xml(parts[name], name)
        csld = root.find("p:cSld", NS)
        lname = csld.get("name", "") if csld is not None else ""
        phs = []
        for sp in root.findall(".//p:sp", NS):
            ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", NS)
            if ph is None:
                continue
            idx = ph.get("idx", "0")
            ptype = ph.get("type") or ("title" if idx == "0" else "obj")
            c_nv = sp.find("./p:nvSpPr/p:cNvPr", NS)
            sizes = sizes_in(ET.tostring(sp, encoding="unicode"))
            phs.append({"idx": idx, "type": ptype,
                        "name": c_nv.get("name", "") if c_nv is not None else "",
                        "box": box_of(sp),
                        "text_sizes_pt": sorted(set(sizes))})
        body = [p for p in phs if p["type"] in ("body", "obj") and p["idx"] != "0"]
        title = [p for p in phs if p["type"] in ("title", "ctrTitle") or
                 (p["idx"] == "0" and p["type"] not in CONTROL_PH)]
        pic = [p for p in phs if p["type"] == "pic"]
        area = None
        boxes = [p["box"] for p in body + pic if p["box"]]
        if boxes:
            x0 = min(b["x"] for b in boxes)
            y0 = min(b["y"] for b in boxes)
            x1 = max(b["x"] + b["cx"] for b in boxes)
            y1 = max(b["y"] + b["cy"] for b in boxes)
            area = {"x": x0, "y": y0, "cx": x1 - x0, "cy": y1 - y0,
                    "inches": {k: round(v / EMU_IN, 3)
                               for k, v in (("x", x0), ("y", y0),
                                            ("cx", x1 - x0), ("cy", y1 - y0))}}
        layout = {"index": int(m.group(1)), "part": name, "name": lname,
                  "type": root.get("type", ""), "placeholders": phs,
                  "has_title": bool(title), "has_body": bool(body),
                  "has_picture": bool(pic), "body_count": len(body),
                  "content_area": area}
        layouts.append(layout)
        if not lname:
            warnings.append(f"{name} has no layout name")
    layouts.sort(key=lambda x: x["index"])
    return layouts, warnings


def guess_roles(layouts):
    out = {}
    used = set()
    def pick(role, predicate):
        for layout in layouts:
            key = layout["part"]
            if key in used:
                continue
            if predicate(layout):
                out[role] = layout["name"]
                used.add(key)
                return
        if layouts:
            out[role] = layouts[min(len(layouts) - 1, 0)]["name"]
    pick("title", lambda l: (l["type"] or "").lower() in ("title", "cover") or
         "title slide" in l["name"].lower() or "cover" in l["name"].lower())
    pick("section", lambda l: (l["type"] or "").lower() in ("sechead", "section") or
         "section" in l["name"].lower())
    # Content must prefer a real content/object body layout.  A section header
    # also exposes a body placeholder, but using it for every content slide
    # silently throws away the template's content geometry.
    pick("content", lambda l: l["has_title"] and l["has_body"] and
         "section" not in str(l.get("name", "")).lower() and
         ((l.get("type") or "").lower() in ("obj", "body") or
          "content" in str(l.get("name", "")).lower()))
    pick("two-column", lambda l: l["has_title"] and l["body_count"] >= 2 and
         "section" not in str(l.get("name", "")).lower())
    pick("figure", lambda l: l["has_title"] and l["has_picture"])
    used.clear()
    for role, name in list(out.items()):
        out[role] = name
    out.setdefault("closing", out.get("title", layouts[0]["name"] if layouts else ""))
    return out


def classify_font(name):
    low = name.lower()
    is_cjk = any(ord(ch) > 0x2E80 for ch in name) or any(h in low for h in CJK_HINTS)
    if any(h in low for h in MONO_HINTS):
        family = "mono"
    elif any(h in low for h in SERIF_HINTS):
        family = "serif"
    else:
        family = "sans"
    return family, is_cjk


def parse_fonts(raw):
    if not raw:
        return []
    path = Path(raw)
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else raw
    fonts = []
    for line in text.splitlines():
        for item in re.split(r"[,;\t]", line):
            item = item.strip().strip('"').strip("'")
            if item and not item.startswith("#"):
                fonts.append(item)
    return fonts


def font_substitutions(theme, available):
    if not available:
        return []
    have = {f.strip().lower(): f.strip() for f in available}
    used = []
    for which in ("majorFont", "minorFont"):
        for face, name in (theme.get("fonts", {}).get(which) or {}).items():
            if name and name.lower() not in have and name not in [u["original"] for u in used]:
                used.append({"original": name, "face": f"{which}.{face}",
                             "substitute": "", "reason": "",
                             "family": classify_font(name)[0]})
    for item in used:
        family, is_cjk = classify_font(item["original"])
        pools = (COMMON_CJK_SERIF if is_cjk and family == "serif" else
                 COMMON_CJK_SANS if is_cjk else
                 COMMON_MONO if family == "mono" else
                 COMMON_SERIF if family == "serif" else COMMON_SANS)
        choice = ""
        for cand in pools:
            if cand.lower() in have:
                choice = have[cand.lower()]
                break
        if not choice:
            for cand in available:
                fam, cjk = classify_font(cand)
                if fam == family and cjk == is_cjk:
                    choice = cand
                    break
        item["substitute"] = choice
        item["reason"] = ("same script/family fallback; original font absent from "
                          "--available-fonts" if choice else
                          "no same-family candidate in --available-fonts")
    return used


def extract_font_usage(parts, theme):
    latin, ea = {}, {}
    for name, raw in parts.items():
        if not (re.match(r"^ppt/(slideMasters|slideLayouts|slides)/", name)
                or re.match(r"^ppt/theme/theme\d+\.xml$", name)):
            continue
        text = raw.decode("utf-8", "replace")
        for face, tag in (("latin", "latin"), ("ea", "ea")):
            for m in re.finditer(rf'<a:{tag}\b[^>]*typeface="([^"]+)"', text):
                val = m.group(1)
                if not val:
                    continue
                bucket = latin if face == "latin" else ea
                bucket[val] = bucket.get(val, 0) + 1
    return {"latin": dict(sorted(latin.items(), key=lambda kv: -kv[1])),
            "ea": dict(sorted(ea.items(), key=lambda kv: -kv[1])),
            "theme": theme.get("fonts", {})}


def sample_slides(parts):
    out = []
    for i, part in enumerate(ordered_slides(parts), 1):
        texts = [t for t in T_RE.findall(parts[part].decode("utf-8", "replace"))]
        notes = ""
        for rel in rels_of(parts, part):
            if rel.get("Type", "").endswith("/notesSlide"):
                target = resolve_target("ppt/slides", rel.get("Target", ""))
                if target in parts:
                    notes = " ".join(T_RE.findall(
                        parts[target].decode("utf-8", "replace"))).strip()
        out.append({"index": i, "part": part,
                    "texts": [t for t in texts if t.strip()][:8],
                    "notes": notes[:200]})
    return out


def suggested_sizes(layouts, theme):
    title_sizes, body_sizes = [], []
    for layout in layouts:
        for ph in layout["placeholders"]:
            target = None
            if ph["type"] in ("title", "ctrTitle"):
                target = title_sizes
            elif ph["type"] in ("body", "obj"):
                target = body_sizes
            if target is not None and ph["text_sizes_pt"]:
                target.extend(ph["text_sizes_pt"])
    def med(values, default):
        return float(statistics.median(values)) if values else float(default)
    return {"title_size": med(title_sizes, 32),
            "body_size": med(body_sizes, 18),
            "caption_size": 12.0, "footer_size": 10.0}


def emit_spec(audit):
    theme = audit.get("theme", {})
    fonts = theme.get("fonts", {})
    allowed = sorted({name for which in ("majorFont", "minorFont")
                      for name in (fonts.get(which) or {}).values() if name})
    sub = audit.get("font_substitution") or []
    roles = audit.get("suggested_roles", {})
    sizes = audit.get("suggested_sizes", {})
    layout_list = audit.get("layouts", [])
    single_layout = len(layout_list) == 1
    has_body = any(l.get("has_body") for l in layout_list)
    layout_by_name = {str(l.get("name", "")).strip().lower(): l
                      for l in layout_list}
    role_specs = {}
    for role, name in roles.items():
        title_size = sizes.get("title_size", 32)
        body_size = sizes.get("body_size", 18)
        layout = layout_by_name.get(str(name).strip().lower())
        if layout:
            for ph in layout.get("placeholders", []):
                pts = ph.get("text_sizes_pt") or []
                if not pts:
                    continue
                if ph.get("type") in ("title", "ctrTitle"):
                    title_size = max(pts)
                elif ph.get("type") in ("body", "obj"):
                    body_size = max(pts)
        role_specs[role] = {"layout": name, "title_size": title_size,
                            "body_size": body_size,
                            "notes_required": role in ("title", "content",
                                                       "figure", "closing")}
    return {
        "meta": {"spec_version": "1.0",
                 "generated_by": "audit_ppt_template.py",
                 "template": audit.get("template", ""),
                 "aspect_ratio": audit.get("slide_size", {}).get("aspect_ratio", "")},
        "template": {"required": True,
                     "fidelity": "master+layouts+theme",
                     "slide_size": audit.get("slide_size", {})},
        "roles": role_specs,
        "typography": {
            "title_size": sizes.get("title_size", 32),
            "body_size": sizes.get("body_size", 18),
            "caption_size": 12, "footer_size": 10,
            "min_body_pt": 14, "min_caption_pt": 10, "min_footer_pt": 9,
            "max_title_lines": 2, "max_bullets": 6,
        },
        "fonts": {"theme": fonts, "allowed": allowed,
                  "unavailable": [s["original"] for s in sub if s.get("substitute")],
                  "substitutions": sub},
        "colors": {"theme": theme.get("colors", {}),
                   "allowed": sorted(set((theme.get("colors") or {}).values())),
                   "exceptions": []},
        "content": {"preserve_from_baseline": True, "min_retention": 0.85,
                    "numbers_must_match": True, "must_contain": [],
                    "notes_required": True},
        "images": {"aspect_tolerance": 0.02, "allow_crop": False},
        "validation": {"require_placeholders": True,
                       "require_title_placeholder": True,
                       "require_body_placeholder": has_body and not single_layout,
                       "allow_free_text": ["Caption", "Footer", "Source"],
                       "must_use_roles": (["content"] if single_layout else
                                          ["title", "content"]),
                       "overflow": "warn", "strict_fonts": False,
                       "strict_colors": False},
    }


def audit_template(path, available_fonts):
    parts = read_parts(path)
    if "ppt/presentation.xml" not in parts:
        raise ValueError("missing ppt/presentation.xml")
    size = slide_size(parts)
    theme = parse_theme(parts)
    layouts, warnings = parse_layouts(parts)
    masters = sorted(n for n in parts if re.match(r"^ppt/slideMasters/slideMaster\d+\.xml$", n))
    themes = sorted(n for n in parts if re.match(r"^ppt/theme/theme\d+\.xml$", n))
    if not masters:
        warnings.append("template has no slide master")
    if not themes:
        warnings.append("template has no theme; fidelity is impossible")
    if not layouts:
        warnings.append("template has no slide layout")
    if not any(l["has_title"] for l in layouts):
        warnings.append("no layout exposes a title placeholder")
    if not any(l["has_body"] for l in layouts):
        warnings.append("no layout exposes a body/object placeholder")
    notes_master = None
    for rel in rels_of(parts, "ppt/presentation.xml"):
        if rel.get("Type", "").endswith("/notesMaster"):
            notes_master = resolve_target("ppt", rel.get("Target", ""))
    samples = sample_slides(parts)
    if samples:
        warnings.append(f"template contains {len(samples)} sample slide(s); "
                        "adapt_deck.py removes them before filling content")
    subs = font_substitutions(theme, available_fonts)
    if not available_fonts:
        warnings.append("font availability not checked; pass --available-fonts "
                        "(e.g. font-discovery --list) to get substitutions")
    return {
        "ok": True, "template": str(path),
        "package": {"files": len(parts), "bytes": sum(len(v) for v in parts.values())},
        "slide_size": size,
        "masters": masters, "themes": themes,
        "layouts": layouts,
        "theme": theme,
        "font_usage": extract_font_usage(parts, theme),
        "sample_slides": samples,
        "notes_master": notes_master,
        "media": sorted(n for n in parts if n.startswith("ppt/media/")),
        "suggested_roles": guess_roles(layouts),
        "suggested_sizes": suggested_sizes(layouts, theme),
        "font_substitution": subs,
        "available_fonts_checked": bool(available_fonts),
        "warnings": warnings,
    }


def main():
    ap = argparse.ArgumentParser(description="audit a PPTX template with stdlib only")
    ap.add_argument("--template", required=True)
    ap.add_argument("--out")
    ap.add_argument("--emit-spec")
    ap.add_argument("--available-fonts",
                    help="file path or comma-separated font names")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        fonts = parse_fonts(args.available_fonts)
        report = audit_template(args.template, fonts)
    except (OSError, zipfile.BadZipFile, ValueError, ET.ParseError) as exc:
        report = {"ok": False, "template": args.template,
                  "error": f"{type(exc).__name__}: {exc}"}
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    if args.emit_spec:
        Path(args.emit_spec).parent.mkdir(parents=True, exist_ok=True)
        Path(args.emit_spec).write_text(
            json.dumps(emit_spec(report), ensure_ascii=False, indent=2),
            encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        size = report.get("slide_size") or {}
        print(f"template: {args.template}")
        print(f"slide size: {size.get('inches')} ({size.get('aspect_ratio')})")
        print(f"masters={len(report['masters'])} layouts={len(report['layouts'])} "
              f"theme={report['theme'].get('name', '')}")
        for layout in report["layouts"]:
            phs = ", ".join(f"{p['idx']}:{p['type']}" for p in layout["placeholders"])
            print(f"  [{layout['index']}] {layout['name']} ({layout['type']}): {phs}")
        print(f"theme fonts: {report['theme'].get('fonts')}")
        print(f"theme colors: {report['theme'].get('colors')}")
        print(f"sample slides: {len(report['sample_slides'])}")
        for w in report["warnings"]:
            print(f"WARNING: {w}")
        for s in report["font_substitution"]:
            print(f"FONT: {s['original']} -> {s['substitute']} ({s['reason']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
