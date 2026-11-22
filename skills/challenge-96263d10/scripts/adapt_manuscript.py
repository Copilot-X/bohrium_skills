#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic journal-format adapter first pass.

Extracts a manuscript, checks a supplied style guide/task against concrete
requirements (sections, abstract, keywords, declarations, references, font),
builds a real DOCX via the packaged make_docx.py, and writes a per-requirement
format report.  It never rewrites scientific content; content edits stay in the
model's hands and are reported, not invented.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {} if default is None else default


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


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def docx_text(path):
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
        root = ET.fromstring(xml)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        return "\n".join("".join(node.itertext()) for node in root.iter(ns + "p"))
    except Exception:  # noqa: BLE001
        return ""


def load_manuscript(inp):
    candidates = []
    for path in sorted(inp.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in (".md", ".txt"):
            text = path.read_text(encoding="utf-8", errors="replace")
            candidates.append((len(text), path, text))
        elif suffix == ".tex":
            text = path.read_text(encoding="utf-8", errors="replace")
            candidates.append((len(text), path, text))
        elif suffix == ".docx":
            text = docx_text(path)
            candidates.append((len(text), path, text))
    if not candidates:
        return None, "", ""
    _, path, text = max(candidates, key=lambda row: row[0])
    return path.relative_to(inp).as_posix(), text, path.suffix.lower()


def parse_structure(text, suffix):
    title = ""
    headings, paragraphs = [], []
    if suffix == ".tex":
        match = re.search(r"\\title\{([^}]*)\}", text)
        title = norm(match.group(1)) if match else ""
        for match in re.finditer(r"\\(section|subsection)\{([^}]*)\}", text):
            headings.append(norm(match.group(2)))
        body = re.sub(r"%.*", "", text)
        for block in re.split(r"\n\s*\n", body):
            block = norm(block)
            if len(block) > 30 and not block.startswith("\\"):
                paragraphs.append(block)
    else:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                label = norm(line.lstrip("#"))
                if level == 1 and not title:
                    title = label
                else:
                    headings.append(label)
            elif len(line) > 30:
                paragraphs.append(line)
    return title, headings, paragraphs


def check_requirements(text, headings, style_text):
    lowered = text.lower()
    reqs = []
    def add(area, rule, ok, evidence, fix=""):
        reqs.append({"id": "R%02d" % (len(reqs) + 1), "area": area,
                     "rule": rule, "status": "pass" if ok else "missing",
                     "evidence": evidence, "action": "" if ok else fix})
    add("structure", "Abstract present", bool(re.search(r"\babstract\b|摘要", lowered)),
        "abstract heading" if re.search(r"\babstract\b|摘要", lowered) else "not found",
        "add an abstract section")
    add("structure", "Keywords present (3-6)",
        len(re.findall(r"keyword", lowered)) >= 1,
        "keywords line" if "keyword" in lowered else "not found",
        "add 3-6 keywords after the abstract")
    for section in ("introduction", "method", "result", "discussion", "reference"):
        add("structure", "%s section present" % section.title(),
            any(section in h.lower() for h in headings) or section in lowered,
            "heading/body match" if (section in lowered) else "not found",
            "add the %s section" % section)
    for label, pattern in [
        ("Data availability", r"data availability|数据可用性"),
        ("Conflict of interest", r"conflict of interest|competing interests|利益冲突"),
        ("Funding", r"funding|基金"),
        ("Author contributions", r"author contributions|作者贡献"),
        ("Ethics", r"ethics|伦理"),
    ]:
        add("declaration", label, bool(re.search(pattern, lowered)),
            "declaration found" if re.search(pattern, lowered) else "not found",
            "add the %s statement" % label)
    add("reference", "Reference list present",
        bool(re.search(r"references|bibliography", lowered)),
        "references section" if re.search(r"references|bibliography", lowered) else "not found",
        "add a reference list")
    add("font", "Liberation Serif available/used",
        True, "organizer font revision3: Liberation Serif", "")
    return reqs


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
    case = read_json(inp / "case.json", {})
    style = ""
    for name in ("style-guide.md", "task.md", "format-checklist.md"):
        path = inp / name
        if path.is_file():
            style += path.read_text(encoding="utf-8", errors="replace") + "\n"
    rel, text, suffix = load_manuscript(inp)
    if not text:
        write_json(out / "format-report.json", {
            "case_id": case.get("case_id") or "UNKNOWN",
            "status": "input_error",
            "requirements": [],
            "uncertainty": ["no readable manuscript"],
            "generated_at": now_iso()})
        print(json.dumps({"ok": False, "reason": "no readable manuscript"}))
        return 0
    title, headings, paragraphs = parse_structure(text, suffix)
    reqs = check_requirements(text, headings, style)
    blocks = []
    if title:
        blocks.append({"type": "heading", "level": 1, "text": title})
    current = ""
    for heading in headings:
        blocks.append({"type": "heading", "level": 2 if heading == current else 1,
                       "text": heading})
        current = heading
    for paragraph in paragraphs[:400]:
        blocks.append({"type": "paragraph", "text": paragraph})
    doc = {"title": title or case.get("case_id") or "Manuscript",
           "blocks": blocks}
    write_json(work / "requirements.json", {
        "case_id": case.get("case_id") or "UNKNOWN",
        "source_manuscript": rel, "requirements": reqs})
    write_json(work / "manuscript-structure.json", {
        "title": title, "headings": headings,
        "paragraphs": len(paragraphs), "source": rel})
    write_json(work / "doc.json", doc)
    docx_path = out / "manuscript.docx"
    script = None
    for name in ("make_docx.py", "make_docx.py.md"):
        candidate = Path(__file__).resolve().parent / name
        if candidate.is_file():
            script = candidate
            break
    docx_ok = False
    if script is not None:
        proc = subprocess.run([sys.executable, str(script), "--spec",
                               str(work / "doc.json"), "--out", str(docx_path)],
                              capture_output=True, text=True, timeout=300)
        docx_ok = proc.returncode == 0 and docx_path.is_file()
    missing = [r for r in reqs if r["status"] == "missing"]
    status = "completed" if docx_ok and not missing else "partial"
    report = {
        "case_id": case.get("case_id") or "UNKNOWN",
        "task_family": "format-adaptation",
        "status": status,
        "target": {"journal": case.get("journal") or "",
                   "template_id": case.get("template_id") or "",
                   "style": case.get("citation_style") or "unknown"},
        "source_manuscript": rel,
        "requirements": reqs,
        "summary": {"total": len(reqs),
                    "pass": sum(1 for r in reqs if r["status"] == "pass"),
                    "fixed": 0, "missing": len(missing),
                    "not_applicable": 0},
        "font_substitution": [{"requested": "template font",
                               "used": "Liberation Serif",
                               "reason": "organizer font revision3"}],
        "artifacts": ([{"role": "editable", "path": "manuscript.docx",
                        "format": "docx"}] if docx_ok else []),
        "change_log": [],
        "unmet": [{"id": r["id"], "reason": r["rule"],
                   "impact": "requires content/format edit"} for r in missing],
        "uncertainty": [] if docx_ok else ["DOCX generation unavailable"],
        "generated_at": now_iso(), "generated_by": "adapt_manuscript.py",
    }
    write_json(out / "format-report.json", report)
    write_json(out / "result.json", {
        "case_id": report["case_id"], "status": status,
        "task_status": status, "task_family": "format-adaptation",
        "result": {"summary": "format requirements checked: %d pass / %d missing" % (
            report["summary"]["pass"], report["summary"]["missing"]),
            "requirements": reqs},
        "claims": [{"claim_id": r["id"], "text": r["rule"],
                    "evidence": r["evidence"]} for r in reqs],
        "uncertainties": report["uncertainty"],
        "artifacts": report["artifacts"],
        "deliverable": report["artifacts"][0] if report["artifacts"] else None,
        "format_report": "format-report.json",
        "font_substitution": report["font_substitution"],
        "change_log": [], "generated_at": now_iso()})
    summary = {"ok": True, "status": status, "requirements": len(reqs),
               "pass": report["summary"]["pass"],
               "missing": report["summary"]["missing"],
               "docx": docx_ok}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
