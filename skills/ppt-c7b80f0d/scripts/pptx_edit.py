#!/usr/bin/env python3
"""Inspect and edit an existing .pptx without python-pptx (stdlib only).

Editing works at the XML-string level: only the <a:t> runs you name are
rewritten, every other byte of the package is copied through untouched. That is
what "source-preserving edit" means for a deck — master, layouts, theme,
images, animations and unknown extensions all survive.

Subcommands
    inspect  <deck.pptx>                     slide size, masters, layouts, fonts, colours
    list     <deck.pptx>                     per slide: text runs, images, notes
    set-text <deck.pptx> --slide N --index K --text "..."  [--out new.pptx]
    replace  <deck.pptx> --from OLD --to NEW [--slide N] [--all] [--out new.pptx]
    set-notes <deck.pptx> --slide N --text "..."  [--append] [--out new.pptx]
    set-image <deck.pptx> --part ppt/media/image1.png --src new.png [--out new.pptx]
    verify   <deck.pptx>

All commands accept --json.
"""

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, unescape

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

T_RE = re.compile(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.S)
SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
LAYOUT_RE = re.compile(r"^ppt/slideLayouts/slideLayout(\d+)\.xml$")
NOTES_RE = re.compile(r"^ppt/notesSlides/notesSlide(\d+)\.xml$")


# --------------------------------------------------------------------------
# package helpers
# --------------------------------------------------------------------------
def read_parts(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def write_parts(parts, out_path, order=None):
    names = order or list(parts)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            if name in parts:
                zf.writestr(name, parts[name])
        for name in parts:
            if name not in names:
                zf.writestr(name, parts[name])


def text_of(xml_bytes):
    raw = xml_bytes.decode("utf-8", "replace")
    return [unescape(m) for m in T_RE.findall(raw)]


def rels_target(parts, part_name, rel_type_suffix):
    """Find the relationship target of a given type for a part."""
    base = part_name.rsplit("/", 1)
    rels_name = f"{base[0]}/_rels/{base[1]}.rels"
    if rels_name not in parts:
        return None
    raw = parts[rels_name].decode("utf-8", "replace")
    for m in re.finditer(r"<Relationship\b([^>]*)/>", raw):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if attrs.get("Type", "").endswith(rel_type_suffix):
            target = attrs.get("Target", "")
            return _resolve_target(base[0], target)
    return None


def ordered_slides(parts):
    """Return slide part names in presentation order."""
    pres = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    ids = re.findall(r'<p:sldId\b[^>]*r:id="([^"]+)"', pres)
    rels_name = "ppt/_rels/presentation.xml.rels"
    order = []
    if rels_name in parts and ids:
        raw = parts[rels_name].decode("utf-8", "replace")
        mapping = {}
        for m in re.finditer(r"<Relationship\b([^>]*)/>", raw):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            mapping[attrs.get("Id")] = attrs.get("Target", "")
        for rid in ids:
            target = mapping.get(rid, "")
            target = target.lstrip("/")
            if not target.startswith("ppt/"):
                target = "ppt/" + target
            if target in parts:
                order.append(target)
    if not order:
        numeric = sorted((int(SLIDE_RE.match(n).group(1)), n)
                         for n in parts if SLIDE_RE.match(n))
        order = [n for _, n in numeric]
    return order


# --------------------------------------------------------------------------
# inspect / list
# --------------------------------------------------------------------------
def inspect(path):
    parts = read_parts(path)
    out = {"path": str(path), "ok": True}
    pres = parts.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    m = re.search(r'<p:sldSz\b([^>]*)/>', pres)
    if m:
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        out["slide_size"] = {"cx": int(attrs.get("cx", 0)), "cy": int(attrs.get("cy", 0)),
                             "type": attrs.get("type", "")}
        if out["slide_size"]["cx"] and out["slide_size"]["cy"]:
            ratio = out["slide_size"]["cx"] / out["slide_size"]["cy"]
            out["aspect_ratio"] = "16:9" if abs(ratio - 16 / 9) < 0.02 else (
                "4:3" if abs(ratio - 4 / 3) < 0.02 else f"{ratio:.3f}")
    out["masters"] = sorted(n for n in parts if re.match(r"^ppt/slideMasters/slideMaster\d+\.xml$", n))
    layouts = []
    for name in sorted(parts, key=lambda x: int(LAYOUT_RE.match(x).group(1))
                       if LAYOUT_RE.match(x) else 0):
        if not LAYOUT_RE.match(name):
            continue
        raw = parts[name].decode("utf-8", "replace")
        nm = re.search(r'<p:cSld\b[^>]*name="([^"]*)"', raw)
        phs = re.findall(r'<p:ph\b([^>]*)/>', raw)
        types = []
        for ph in phs:
            t = re.search(r'type="([^"]*)"', ph)
            types.append(t.group(1) if t else "body")
        layouts.append({"part": name, "name": nm.group(1) if nm else "",
                        "placeholders": types})
    out["layouts"] = layouts
    out["slides"] = ordered_slides(parts)
    out["slide_count"] = len(out["slides"])

    fonts, colors = set(), set()
    for name in parts:
        if re.match(r"^ppt/theme/theme\d+\.xml$", name):
            raw = parts[name].decode("utf-8", "replace")
            fonts.update(re.findall(r'<a:latin\b[^>]*typeface="([^"]+)"', raw))
            fonts.update(re.findall(r'<a:ea\b[^>]*typeface="([^"]+)"', raw))
            colors.update(re.findall(r'<a:srgbClr\b[^>]*val="([0-9A-Fa-f]{6})"', raw))
    out["theme_fonts"] = sorted(f for f in fonts if f)
    out["theme_colors"] = sorted({c.upper() for c in colors})
    out["media"] = sorted(n for n in parts if n.startswith("ppt/media/"))
    out["notes_parts"] = sorted(n for n in parts if NOTES_RE.match(n))
    return out


def list_deck(path):
    parts = read_parts(path)
    slides = ordered_slides(parts)
    result = []
    for i, name in enumerate(slides, 1):
        texts = text_of(parts[name])
        notes_name = rels_target(parts, name, "/notesSlide")
        notes = text_of(parts[notes_name]) if notes_name and notes_name in parts else []
        imgs = re.findall(r'Target="([^"]+\.(?:png|jpe?g|gif|svg|emf|wmf))"',
                          parts.get(f"{name.rsplit('/', 1)[0]}/_rels/{name.rsplit('/', 1)[1]}.rels",
                                    b"").decode("utf-8", "replace"), re.I)
        result.append({"slide": i, "part": name,
                       "texts": [{"index": k, "text": t} for k, t in enumerate(texts)],
                       "images": imgs, "notes": " ".join(notes).strip()})
    return {"path": str(path), "slide_count": len(slides), "slides": result}


# --------------------------------------------------------------------------
# mutations
# --------------------------------------------------------------------------
def _replace_nth(xml, index, new_text):
    matches = list(T_RE.finditer(xml))
    if index < 0 or index >= len(matches):
        return None, len(matches)
    m = matches[index]
    escaped = escape(new_text)
    return xml[:m.start(1)] + escaped + xml[m.end(1):], len(matches)


def set_text(path, slide_no, index, text, out_path=None):
    parts = read_parts(path)
    slides = ordered_slides(parts)
    if not 1 <= slide_no <= len(slides):
        return {"ok": False, "error": f"slide {slide_no} out of range 1..{len(slides)}"}
    part = slides[slide_no - 1]
    xml = parts[part].decode("utf-8")
    new_xml, count = _replace_nth(xml, index, text)
    if new_xml is None:
        return {"ok": False, "error": f"text index {index} out of range 0..{count - 1}"}
    parts[part] = new_xml.encode("utf-8")
    out = Path(out_path or path)
    write_parts(parts, out)
    return {"ok": True, "out": str(out), "slide": slide_no, "index": index,
            "before": text_of(xml.encode("utf-8"))[index], "after": text}


def replace_text(path, old, new, slide_no=None, replace_all=False, out_path=None):
    parts = read_parts(path)
    slides = ordered_slides(parts)
    targets = [slides[slide_no - 1]] if slide_no else slides
    hits, changes = 0, []
    for part in targets:
        xml = parts[part].decode("utf-8")
        counter = 0
        out_xml, pos = [], 0
        for m in T_RE.finditer(xml):
            content = unescape(m.group(1))
            if old in content:
                new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
                out_xml.append(xml[pos:m.start(1)])
                out_xml.append(escape(new_content))
                pos = m.end(1)
                counter += 1
                hits += 1
        out_xml.append(xml[pos:])
        if counter:
            parts[part] = "".join(out_xml).encode("utf-8")
            changes.append({"part": part, "runs_changed": counter})
            if slide_no and not replace_all:
                break
    if not hits:
        return {"ok": False, "error": f"'{old}' not found",
                "slides_searched": len(targets)}
    out = Path(out_path or path)
    write_parts(parts, out)
    return {"ok": True, "out": str(out), "replacements": hits, "changes": changes}


def set_notes(path, slide_no, text, append=False, out_path=None):
    parts = read_parts(path)
    slides = ordered_slides(parts)
    if not 1 <= slide_no <= len(slides):
        return {"ok": False, "error": f"slide {slide_no} out of range 1..{len(slides)}"}
    part = slides[slide_no - 1]
    notes_part = rels_target(parts, part, "/notesSlide")
    if not notes_part or notes_part not in parts:
        return {"ok": False, "error": "slide has no notes part; rebuild the deck with "
                                      "make_pptx.py --spec if notes must be added",
                "slide": slide_no}
    xml = parts[notes_part].decode("utf-8")
    runs = list(T_RE.finditer(xml))
    if not runs:
        return {"ok": False, "error": "notes part has no text run to replace"}
    if append:
        new_text = unescape(runs[0].group(1)) + "\n" + text
    else:
        new_text = text
    # first run carries the whole note; remaining runs are blanked so stale
    # paragraphs cannot survive underneath the new text
    pieces, pos = [], 0
    for k, m in enumerate(runs):
        pieces.append(xml[pos:m.start(1)])
        pieces.append(escape(new_text) if k == 0 else "")
        pos = m.end(1)
    pieces.append(xml[pos:])
    parts[notes_part] = "".join(pieces).encode("utf-8")
    out = Path(out_path or path)
    write_parts(parts, out)
    return {"ok": True, "out": str(out), "slide": slide_no, "notes_part": notes_part}


def set_image(path, part_name, src, out_path=None):
    if part_name not in read_parts(path):
        return {"ok": False, "error": f"{part_name} not in package"}
    parts = read_parts(path)
    parts[part_name] = Path(src).read_bytes()
    out = Path(out_path or path)
    write_parts(parts, out)
    return {"ok": True, "out": str(out), "part": part_name, "bytes": len(parts[part_name])}


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------
def _resolve_target(folder, target):
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


def verify(path):
    import xml.etree.ElementTree as ET
    problems = []
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                problems.append(f"corrupt member: {bad}")
            names = zf.namelist()
            if "ppt/presentation.xml" not in names:
                problems.append("missing ppt/presentation.xml")
            for name in names:
                if name.endswith(".xml") or name.endswith(".rels"):
                    try:
                        ET.fromstring(zf.read(name))
                    except ET.ParseError as exc:
                        problems.append(f"{name}: {exc}")
    except zipfile.BadZipFile as exc:
        return {"ok": False, "problems": [f"not a zip: {exc}"]}
    parts = read_parts(path)
    for name in sorted(parts):
        if not name.endswith(".rels"):
            continue
        if name == "_rels/.rels":
            folder = ""
        elif "/_rels/" in name:
            folder = name.split("/_rels/")[0]
        else:
            continue
        raw = parts[name].decode("utf-8", "replace")
        for m in re.finditer(r"<Relationship\b([^>]*)/>", raw):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            if attrs.get("TargetMode", "").lower() == "external":
                continue
            target = attrs.get("Target", "")
            resolved = _resolve_target(folder, target)
            if resolved and resolved not in parts:
                problems.append(f"{name}: dangling relationship target {target}")
    slides = ordered_slides(parts)
    return {"ok": not problems, "problems": problems, "slide_count": len(slides),
            "slides": slides}


# --------------------------------------------------------------------------
# template adaptation
# --------------------------------------------------------------------------
CT = "application/vnd.openxmlformats-officedocument"


def _content_type_for(name):
    if name.endswith(".rels"):
        return "application/vnd.openxmlformats-package.relationships+xml"
    fixed = {
        "ppt/presentation.xml": CT + ".presentationml.presentation.main+xml",
        "ppt/presProps.xml": CT + ".presentationml.presProps+xml",
        "ppt/viewProps.xml": CT + ".presentationml.viewProps+xml",
        "ppt/tableStyles.xml": CT + ".presentationml.tableStyles+xml",
        "docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
        "docProps/app.xml": CT + ".extended-properties+xml",
    }
    if name in fixed:
        return fixed[name]
    patterns = [
        (r"ppt/slideMasters/slideMaster\d+\.xml$", CT + ".presentationml.slideMaster+xml"),
        (r"ppt/slideLayouts/slideLayout\d+\.xml$", CT + ".presentationml.slideLayout+xml"),
        (r"ppt/slides/slide\d+\.xml$", CT + ".presentationml.slide+xml"),
        (r"ppt/notesMasters/notesMaster\d+\.xml$", CT + ".presentationml.notesMaster+xml"),
        (r"ppt/notesSlides/notesSlide\d+\.xml$", CT + ".presentationml.notesSlide+xml"),
        (r"ppt/theme/theme\d+\.xml$", CT + ".theme+xml"),
    ]
    for pat, ct in patterns:
        if re.match(pat, name):
            return ct
    if name.endswith(".xml"):
        return "application/xml"
    return None


def rebuild_content_types(parts):
    defaults = {
        "rels": "application/vnd.openxmlformats-package.relationships+xml",
        "xml": "application/xml",
        "png": "image/png",
        "jpeg": "image/jpeg",
        "jpg": "image/jpeg",
        "gif": "image/gif",
        "svg": "image/svg+xml",
        "emf": "image/x-emf",
        "wmf": "image/x-wmf",
    }
    used = {n.rsplit(".", 1)[-1].lower() for n in parts if "." in n}
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">']
    for ext, ct in defaults.items():
        if ext in used:
            lines.append(f'<Default Extension="{ext}" ContentType="{ct}"/>')
    for name in sorted(parts):
        if name == "[Content_Types].xml":
            continue
        ct = _content_type_for(name)
        if ct:
            lines.append(f'<Override PartName="/{name}" ContentType="{ct}"/>')
    lines.append("</Types>")
    return "".join(lines).encode("utf-8")


def _layout_parts(tmpl):
    rels_name = "ppt/slideMasters/_rels/slideMaster1.xml.rels"
    parts = []
    if rels_name in tmpl:
        raw = tmpl[rels_name].decode("utf-8", "replace")
        for m in re.finditer(r"<Relationship\b([^>]*)/>", raw):
            attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            if attrs.get("Type", "").endswith("/slideLayout"):
                target = attrs.get("Target", "").lstrip("/")
                if target.startswith("../"):
                    target = target[3:]
                target = target if target.startswith("ppt/") else f"ppt/{target}"
                parts.append(target.replace("\\", "/"))
    if not parts:
        parts = sorted(n for n in tmpl if re.match(r"ppt/slideLayouts/slideLayout\d+\.xml$", n))
    return parts


def apply_template(deck_path, template_path, out_path=None, layout_name=None):
    deck = read_parts(deck_path)
    original = dict(deck)
    tmpl = read_parts(template_path)

    masters = sorted(n for n in tmpl if re.match(r"ppt/slideMasters/slideMaster\d+\.xml$", n))
    themes = sorted(n for n in tmpl if re.match(r"ppt/theme/theme\d+\.xml$", n))
    theme_only = (len(masters) != 1
                  or masters[0] != "ppt/slideMasters/slideMaster1.xml"
                  or not themes)

    if theme_only:
        if not themes:
            return {"ok": False, "error": "template has no theme part"}
        deck["ppt/theme/theme1.xml"] = tmpl[themes[0]]
        deck["[Content_Types].xml"] = rebuild_content_types(deck)
        out = Path(out_path or deck_path)
        write_parts(deck, out)
        res = verify(out)
        if not res["ok"]:
            write_parts(original, out)
            return {"ok": False, "error": "theme adoption broke the package; original kept",
                    "problems": res["problems"]}
        return {"ok": True, "out": str(out), "fidelity": "theme-only",
                "reason": "template has several masters/themes; only theme1 was adopted"}

    prefixes = ("ppt/slideMasters/", "ppt/slideLayouts/", "ppt/theme/", "ppt/media/")
    rename = {}
    for name, raw in tmpl.items():
        if not name.startswith(prefixes):
            continue
        target = name
        if name.startswith("ppt/media/") and name in deck and deck[name] != raw:
            target = "ppt/media/tpl_" + name.split("/")[-1]
            rename[name.split("/")[-1]] = target.split("/")[-1]
        deck[target] = raw
    if rename:
        for name in list(deck):
            if name.endswith(".rels") and name.startswith(("ppt/slideMasters/", "ppt/slideLayouts/")):
                raw = deck[name].decode("utf-8", "replace")
                for old, new in rename.items():
                    raw = raw.replace(f"../media/{old}\"", f"../media/{new}\"")
                deck[name] = raw.encode("utf-8")

    pres = deck["ppt/presentation.xml"].decode("utf-8", "replace")
    pres = re.sub(
        r"<p:sldMasterIdLst>.*?</p:sldMasterIdLst>",
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rIdMaster"/></p:sldMasterIdLst>',
        pres, flags=re.S)
    tpres = tmpl.get("ppt/presentation.xml", b"").decode("utf-8", "replace")
    sz = re.search(r"<p:sldSz\b[^>]*/>", tpres)
    if sz:
        pres = re.sub(r"<p:sldSz\b[^>]*/>", sz.group(0), pres)
    deck["ppt/presentation.xml"] = pres.encode("utf-8")

    prels_name = "ppt/_rels/presentation.xml.rels"
    prels = deck.get(prels_name, b"").decode("utf-8", "replace")
    if "slideMasters/slideMaster1.xml" not in prels:
        prels = prels.replace(
            "</Relationships>",
            f'<Relationship Id="rIdMaster" Type="{P_NS}/slideMaster" '
            f'Target="slideMasters/slideMaster1.xml"/></Relationships>')
        deck[prels_name] = prels.encode("utf-8")

    layouts = _layout_parts(tmpl)
    layout_file = None
    if layout_name:
        for lp in layouts:
            raw = deck.get(lp, b"").decode("utf-8", "replace")
            nm = re.search(r'<p:cSld\b[^>]*name="([^"]*)"', raw)
            if nm and nm.group(1).strip().lower() == layout_name.strip().lower():
                layout_file = lp.split("/")[-1]
                break
    if not layout_file:
        for lp in layouts:
            raw = deck.get(lp, b"").decode("utf-8", "replace")
            if 'type="body"' in raw or 'type="obj"' in raw:
                layout_file = lp.split("/")[-1]
                break
    if not layout_file and layouts:
        layout_file = layouts[min(1, len(layouts) - 1)].split("/")[-1]
    if not layout_file:
        return {"ok": False, "error": "template has no slide layout"}

    for slide in ordered_slides(deck):
        folder, fname = slide.rsplit("/", 1)
        rels = f"{folder}/_rels/{fname}.rels"
        if rels not in deck:
            continue
        raw = deck[rels].decode("utf-8", "replace")
        raw = re.sub(r'Target="\.\./slideLayouts/slideLayout\d+\.xml"',
                     f'Target="../slideLayouts/{layout_file}"', raw)
        deck[rels] = raw.encode("utf-8")

    deck["[Content_Types].xml"] = rebuild_content_types(deck)
    out = Path(out_path or deck_path)
    write_parts(deck, out)
    res = verify(out)
    if not res["ok"]:
        write_parts(original, out)
        return {"ok": False,
                "error": "template application produced an invalid package; original kept",
                "problems": res["problems"]}
    info = inspect(out)
    return {"ok": True, "out": str(out), "fidelity": "master+layouts+theme",
            "layout": layout_file, "layout_count": len(layouts),
            "theme_fonts": info.get("theme_fonts", []),
            "theme_colors": info.get("theme_colors", []),
            "slide_size": info.get("slide_size")}


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="inspect / edit .pptx with stdlib only")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inspect"); p.add_argument("path"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("list"); p.add_argument("path"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("verify"); p.add_argument("path"); p.add_argument("--json", action="store_true")

    p = sub.add_parser("set-text")
    p.add_argument("path"); p.add_argument("--slide", type=int, required=True)
    p.add_argument("--index", type=int, required=True); p.add_argument("--text", required=True)
    p.add_argument("--out"); p.add_argument("--json", action="store_true")

    p = sub.add_parser("replace")
    p.add_argument("path"); p.add_argument("--from", dest="old", required=True)
    p.add_argument("--to", dest="new", required=True); p.add_argument("--slide", type=int)
    p.add_argument("--all", action="store_true"); p.add_argument("--out")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("set-notes")
    p.add_argument("path"); p.add_argument("--slide", type=int, required=True)
    p.add_argument("--text", required=True); p.add_argument("--append", action="store_true")
    p.add_argument("--out"); p.add_argument("--json", action="store_true")

    p = sub.add_parser("set-image")
    p.add_argument("path"); p.add_argument("--part", required=True)
    p.add_argument("--src", required=True); p.add_argument("--out")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("apply-template")
    p.add_argument("path"); p.add_argument("--template", required=True)
    p.add_argument("--layout"); p.add_argument("--out")
    p.add_argument("--json", action="store_true")

    args = ap.parse_args()
    try:
        if args.cmd == "inspect":
            res = inspect(args.path)
        elif args.cmd == "list":
            res = list_deck(args.path)
        elif args.cmd == "verify":
            res = verify(args.path)
        elif args.cmd == "set-text":
            res = set_text(args.path, args.slide, args.index, args.text, args.out)
        elif args.cmd == "replace":
            res = replace_text(args.path, args.old, args.new, args.slide, args.all, args.out)
        elif args.cmd == "set-notes":
            res = set_notes(args.path, args.slide, args.text, args.append, args.out)
        elif args.cmd == "apply-template":
            res = apply_template(args.path, args.template, args.out, args.layout)
        else:
            res = set_image(args.path, args.part, args.src, args.out)
    except (OSError, KeyError) as exc:
        res = {"ok": False, "error": str(exc)}

    if getattr(args, "json", False):
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
