#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inventory formulas from publisher XML / MathML without inventing content.

Usage:
    python3 inspect_mathml.py --input /input --out /app/work/formula_inventory.json --json
    python3 inspect_mathml.py --self-test --json

What it does
  * walks the input tree and parses every *.xml file (publisher-source.xml,
    source-formulas.xml, JATS, etc.) with the standard library;
  * records every MathML <math> node with its XML id, ancestor ids, order,
    a compact linear form, the TeX annotation when the publisher supplied one,
    and the enclosing section/caption text if present;
  * reports files that exist but contain no formula node instead of assuming
    the missing formula;
  * never infers a formula from prose and never fills unknown values with 0.

The output is an index for the model.  Formula structure, symbol definitions,
derivations and numeric checks still have to be read and verified by hand.

Exit codes: 0 = inventory written, 2 = input directory missing/unreadable.
Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MATH_TAGS = {"math", "m:math", "{http://www.w3.org/1998/Math/MathML}math"}


def local(tag):
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def parse_file(path):
    raw = path.read_bytes()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return {"file": str(path), "parse_error": str(exc),
                "sha256": hashlib.sha256(raw).hexdigest(), "formula_nodes": []}
    nodes = []

    def walk(elem, ancestors):
        tag = local(elem.tag)
        has_ref = (elem.get("id") or elem.get("source-id")
                   or elem.get("source_id"))
        next_ancestors = ancestors + ([elem] if has_ref else [])
        if tag == "math":
            nodes.append(node_record(elem, ancestors))
        for child in list(elem):
            walk(child, next_ancestors)

    walk(root, [])
    return {"file": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "root_tag": local(root.tag), "formula_nodes": nodes}


def node_record(elem, ancestors):
    tex = ""
    for child in elem.iter():
        if local(child.tag) == "annotation" and \
                (child.get("encoding") or "").lower().find("tex") >= 0:
            tex = (child.text or "").strip()
            break
    ids = [a.get("id") for a in ancestors if a.get("id")]
    return {
        "xml_id": elem.get("id") or "",
        "ancestor_ids": ids,
        "source_ids": sorted({a.get("source-id") or a.get("source_id")
                              for a in ancestors
                              if a.get("source-id") or a.get("source_id")}),
        "tex_annotation": tex or None,
        "mathml": compact(elem),
        "linear_hint": linear(elem),
        "display": elem.get("display") or "inline",
    }


def compact(elem, depth=0, max_depth=40):
    """Compact, lossless-enough outline of MathML structure."""
    tag = local(elem.tag)
    if depth > max_depth:
        return {"tag": tag, "truncated": True}
    out = {"tag": tag}
    if elem.get("id"):
        out["id"] = elem.get("id")
    for key in ("stretchy", "fence", "separator", "largeop", "movablelimits"):
        if elem.get(key):
            out[key] = elem.get(key)
    children = [compact(child, depth + 1, max_depth) for child in list(elem)]
    if children:
        out["children"] = children
    text = (elem.text or "").strip()
    if text and tag not in ("math", "mrow"):
        out["text"] = text
    return out


def linear(elem, out=None):
    """Readable linear hint; explicitly a hint, not a verified transcription."""
    if out is None:
        out = []
    tag = local(elem.tag)
    text = (elem.text or "").strip()
    if text:
        out.append(text)
    for child in list(elem):
        ctag = local(child.tag)
        if ctag == "annotation":
            continue
        if ctag == "mfrac":
            kids = list(child)
            if len(kids) >= 2:
                out.append("(")
                linear(kids[0], out)
                out.append(")/(")
                linear(kids[1], out)
                out.append(")")
                continue
        if ctag == "msup" and len(list(child)) >= 2:
            kids = list(child)
            linear(kids[0], out)
            out.append("^(")
            linear(kids[1], out)
            out.append(")")
            continue
        if ctag == "msub" and len(list(child)) >= 2:
            kids = list(child)
            linear(kids[0], out)
            out.append("_(")
            linear(kids[1], out)
            out.append(")")
            continue
        if ctag == "msqrt":
            out.append("sqrt(")
            linear(child, out)
            out.append(")")
            continue
        linear(child, out)
        tail = (child.tail or "").strip()
        if tail:
            out.append(tail)
    return " ".join(x for x in out if x)


def find_xml_files(root):
    return sorted(p for p in Path(root).rglob("*")
                  if p.is_file() and p.suffix.lower() in (".xml", ".mathml"))


def inventory(input_dir):
    root = Path(input_dir)
    if not root.is_dir():
        raise FileNotFoundError("input directory not found: %s" % root)
    files = []
    total = 0
    for path in find_xml_files(root):
        rel = path.relative_to(root).as_posix()
        rec = parse_file(path)
        rec["relative_path"] = rel
        total += len(rec.get("formula_nodes") or [])
        files.append(rec)
    return {
        "input_dir": str(root),
        "xml_files": len(files),
        "formula_nodes": total,
        "found": total > 0,
        "files": files,
        "missing_branch": {
            "applies": total == 0,
            "instruction": ("No MathML formula node is present. If the task "
                            "needs formula transcription or numeric "
                            "reproduction, report status=partial or "
                            "insufficient_evidence with the searched scope; "
                            "do not reverse-engineer a formula from prose and "
                            "do not fill an unknown value with 0."),
        },
    }


def self_test():
    sample = (b'<article><sec id="s1"><p id="p1">text</p>'
              b'<math id="m1"><mrow><mfrac><mi>P</mi><mi>Q</mi></mfrac>'
              b'<annotation encoding="application/x-tex">P/Q</annotation>'
              b'</mrow></math></sec></article>')
    tmp = Path("._inspect_mathml_selftest.xml")
    tmp.write_bytes(sample)
    try:
        rec = parse_file(tmp)
        inv = {"xml_files": 1, "formula_nodes": len(rec["formula_nodes"]),
               "found": bool(rec["formula_nodes"]),
               "node": rec["formula_nodes"][0] if rec["formula_nodes"] else None}
        ok = (inv["formula_nodes"] == 1 and inv["node"]["xml_id"] == "m1"
              and inv["node"]["tex_annotation"] == "P/Q"
              and inv["node"]["ancestor_ids"] == ["s1"])
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            empty = inventory(tmpdir)
        inv["empty_tree_missing_branch"] = empty["missing_branch"]["applies"]
        ok = ok and inv["empty_tree_missing_branch"] is True
        return {"ok": ok, "self_test": True, "inventory": inv}
    finally:
        tmp.unlink(missing_ok=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        report = self_test()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if not args.input:
        ap.error("--input is required unless --self-test is used")
    try:
        payload = inventory(args.input)
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("%d XML files, %d MathML nodes -> %s" % (
            payload["xml_files"], payload["formula_nodes"], args.out or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
