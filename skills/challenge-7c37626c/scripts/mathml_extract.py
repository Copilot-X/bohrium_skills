#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract MathML formulas from publisher/source XML to structured LaTeX.

The input may contain ``publisher-source.xml`` and ``source-formulas.xml``.
This script keeps element ids and a source locator, converts common MathML
constructs (mrow, mi, mn, mo, mfrac, msub/sup, msqrt, mfenced), and reports
missing-formula branches honestly.  It never invents a formula for a case that
does not contain one.
"""
from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

MATHML = "{http://www.w3.org/1998/Math/MathML}"


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def attr(element, *names):
    for name in names:
        for key, value in element.attrib.items():
            if local(key) == name:
                return value
    return ""


def text_of(element):
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def to_latex(element):
    tag = local(element.tag)
    children = list(element)
    if tag in ("math", "mrow", "mstyle", "mpadded", "semantics"):
        return "".join(to_latex(child) for child in children
                       if local(child.tag) != "annotation")
    if tag == "mi":
        name = text_of(element)
        if len(name) > 1 and name.isalpha():
            return "\\mathrm{%s}" % name
        return name
    if tag == "mn":
        return text_of(element)
    if tag == "mo":
        op = text_of(element)
        return {"−": "-", "×": "\\times", "·": "\\cdot", "≤": "\\le",
                "≥": "\\ge", "≠": "\\ne", "→": "\\to", "∞": "\\infty",
                "∑": "\\sum", "∫": "\\int", "∂": "\\partial"}.get(op, op)
    if tag == "mtext":
        return "\\text{%s}" % text_of(element)
    if tag == "mfrac" and len(children) >= 2:
        return "\\frac{%s}{%s}" % (to_latex(children[0]), to_latex(children[1]))
    if tag == "msqrt":
        return "\\sqrt{%s}" % "".join(to_latex(c) for c in children)
    if tag == "mroot" and len(children) >= 2:
        return "\\sqrt[%s]{%s}" % (to_latex(children[1]), to_latex(children[0]))
    if tag in ("msub", "msup", "msubsup") and len(children) >= 2:
        base = to_latex(children[0])
        sub = to_latex(children[1]) if tag in ("msub", "msubsup") else ""
        sup = to_latex(children[-1]) if tag in ("msup", "msubsup") else ""
        if sub:
            base += "_{%s}" % sub
        if sup:
            base += "^{%s}" % sup
        return base
    if tag in ("munder", "mover", "munderover") and len(children) >= 2:
        base = to_latex(children[0])
        if tag in ("munder", "munderover"):
            base += "_{%s}" % to_latex(children[1])
        if tag in ("mover", "munderover"):
            base += "^{%s}" % to_latex(children[-1])
        return base
    if tag == "mfenced":
        open_token = attr(element, "open") or "("
        close_token = attr(element, "close") or ")"
        return open_token + ",".join(to_latex(c) for c in children) + close_token
    if tag == "mtable":
        rows = []
        for row in children:
            if local(row.tag) == "mtr":
                rows.append(" & ".join(to_latex(c) for c in row
                                       if local(c.tag) == "mtd"))
        return "\\begin{matrix}%s\\end{matrix}" % " \\\\ ".join(rows)
    return "".join(to_latex(c) for c in children) if children else text_of(element)


def formula_id(element, index):
    for key in ("id", "xml:id", "formula-id", "source-id", "label"):
        value = attr(element, key)
        if value:
            return value
    return "EQ%03d" % index


def walk(element, path, out):
    tag = local(element.tag)
    if tag == "math":
        out.append((element, path))
        return
    for child in element:
        walk(child, path + "/" + local(child.tag), out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    formulas, sources = [], []
    for path in sorted(inp.rglob("*.xml")):
        rel = path.relative_to(inp).as_posix()
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError) as exc:
            sources.append({"path": rel, "status": "unreadable",
                            "error": str(exc)})
            continue
        found = []
        walk(tree.getroot(), local(tree.getroot().tag), found)
        sources.append({"path": rel, "status": "read",
                        "math_elements": len(found)})
        for index, (element, locator) in enumerate(found, 1):
            latex = to_latex(element)
            symbols = []
            for child in element.iter():
                if local(child.tag) == "mi":
                    name = text_of(child)
                    if name and name not in [s["symbol"] for s in symbols]:
                        symbols.append({"symbol": name, "meaning": "",
                                        "unit": "", "status": "inferred_definition"})
            formulas.append({
                "eq_id": formula_id(element, index),
                "latex": latex,
                "type": "definition",
                "symbols": symbols,
                "source": {"path": rel, "locator": locator,
                           "quote": ET.tostring(element, encoding="unicode")[:2000]},
                "transcription_mode": "deterministic",
                "transcription_confidence": "high",
                "reading_order": {"index": index},
            })
    payload = {
        "case_id": "UNKNOWN",
        "formulas": formulas,
        "sources": sources,
        "coverage": {
            "xml_files": len(sources),
            "math_elements": len(formulas),
            "missing_formula_branch": not bool(formulas),
        },
        "generated_at": now_iso(), "generated_by": "mathml_extract.py",
    }
    write_json(work / "mathml-formulas.json", payload)
    write_json(out / "source-formulas.json", payload)
    lines = ["# MathML extraction", "",
             "| source | status | math elements |", "|---|---|---:|"]
    for source in sources:
        lines.append("| %s | %s | %s |" % (
            source["path"], source["status"], source.get("math_elements", 0)))
    lines += ["", "## Formulas", ""]
    if formulas:
        for formula in formulas:
            lines.append("- **%s** `%s` (%s %s)" % (
                formula["eq_id"], formula["latex"], formula["source"]["path"],
                formula["source"]["locator"]))
    else:
        lines.append("- 未从提供的 XML 中检出 MathML 公式；该分支必须报告 "
                     "partial/insufficient_evidence，不得反推公式。")
    write_text(out / "source-formulas.md", "\n".join(lines) + "\n")
    summary = {"ok": bool(formulas), "xml_files": len(sources),
               "formulas": len(formulas),
               "missing_formula_branch": not bool(formulas)}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
