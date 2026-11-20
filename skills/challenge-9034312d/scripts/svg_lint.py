#!/usr/bin/env python3
"""Lint a scientific SVG before it is delivered: editable, self-contained, honest.

Checks
  1. well-formed XML and an <svg> root
  2. viewBox (or width+height) present, positive canvas
  3. no <script>, no event handlers, no external http(s)/file references
  4. text kept as <text> (not converted to outlines) — reports the count
  5. font-family values reported so missing fonts can be substituted knowingly
  6. <image> only allowed with data: URIs or paths that exist next to the SVG
  7. every <g id="..."> group counted, so layering is inspectable

Usage:
    python3 svg_lint.py figures/fig3.svg [--json] [--fonts Liberation Sans,...]
Exit code 0 = pass, 1 = problems, 2 = unreadable.
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG_NS = "{http://www.w3.org/2000/svg}"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
EXTERNAL = re.compile(r"^(https?:|file:|ftp:)", re.I)
EVENT_ATTR = re.compile(r"^on[a-z]+$", re.I)


def walk(elem, path=""):
    yield elem, path
    for child in list(elem):
        tag = child.tag.split("}")[-1]
        yield from walk(child, f"{path}/{tag}")


def lint(path, allowed_fonts=None):
    problems, notes = [], {}
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return {"ok": False, "problems": [f"not well-formed XML: {exc}"], "notes": {}}

    root = tree.getroot()
    if root.tag.split("}")[-1] != "svg":
        problems.append(f"root element is {root.tag}, expected svg")

    viewbox = root.get("viewBox")
    width, height = root.get("width"), root.get("height")
    if viewbox:
        try:
            nums = [float(x) for x in re.split(r"[,\s]+", viewbox.strip()) if x]
            if len(nums) != 4 or nums[2] <= 0 or nums[3] <= 0:
                problems.append(f"invalid viewBox: {viewbox}")
            else:
                notes["viewBox"] = viewbox
        except ValueError:
            problems.append(f"unparsable viewBox: {viewbox}")
    elif not (width and height):
        problems.append("neither viewBox nor width/height is set")

    text_count = 0
    groups = 0
    fonts = set()
    images = []
    for elem, where in walk(root):
        tag = elem.tag.split("}")[-1]
        if tag == "script":
            problems.append(f"<script> at {where}")
        if tag == "text":
            text_count += 1
        if tag == "g" and elem.get("id"):
            groups += 1
        if tag == "image":
            href = elem.get(XLINK_HREF) or elem.get("href") or ""
            images.append(href)
        for attr, value in elem.attrib.items():
            local = attr.split("}")[-1]
            if EVENT_ATTR.match(local):
                problems.append(f"event handler {local}= at {where}")
            if local in ("href", "src") and EXTERNAL.match(value or ""):
                problems.append(f"external reference at {where}: {value}")
            if local in ("font-family", "fontFamily"):
                for name in re.split(r"[,]", value or ""):
                    name = name.strip().strip("'\"")
                    if name:
                        fonts.add(name)

    if text_count == 0:
        problems.append("no <text> elements: labels appear to be converted to outlines")

    for href in images:
        if href.startswith("data:"):
            continue
        if EXTERNAL.match(href):
            problems.append(f"external image: {href}")
        elif not (Path(path).parent / href).exists():
            problems.append(f"missing image file: {href}")

    if allowed_fonts:
        for name in sorted(fonts):
            if name not in allowed_fonts and name.lower() not in {f.lower() for f in allowed_fonts}:
                problems.append(f"font not on the confirmed list: {name}")

    notes.update({"text_elements": text_count, "named_groups": groups,
                  "fonts": sorted(fonts), "images": len(images)})
    return {"ok": not problems, "problems": problems, "notes": notes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fonts", default="")
    args = ap.parse_args()

    allowed = [f for f in args.fonts.split(",") if f.strip()] or None
    try:
        result = lint(args.path, allowed)
    except OSError as exc:
        result = {"ok": False, "problems": [f"unreadable: {exc}"], "notes": {}}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(("PASS" if result["ok"] else "FAIL") + f" {args.path}")
        for key, value in result["notes"].items():
            print(f"  {key}: {value}")
        for item in result["problems"]:
            print(f"  ! {item}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
