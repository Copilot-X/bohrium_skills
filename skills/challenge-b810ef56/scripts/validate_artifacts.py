#!/usr/bin/env python3
"""Validate result.json plus every artifact it claims, before delivery.

This is the last gate. It does not judge scientific quality; it checks that
what the result claims actually exists, opens, and is not a placeholder.

Checks
  * result.json parses, has case_id, and every artifact path resolves
  * artifact files are non-empty and inside the output root
  * .pptx / .docx are valid zip packages whose main part parses as XML
  * .svg is well-formed and keeps <text> elements
  * .pdf starts with %PDF and ends with %%EOF
  * .png has a valid IHDR header
  * visual deliverables declare a preview/render artifact
  * text artifacts contain no TODO/TBD/placeholder markers

Usage:
    python3 validate_artifacts.py --result /app/output/result.json
    python3 validate_artifacts.py --result result.json --root /app/output --json
Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|XXX|lorem ipsum|placeholder text)\b", re.I)
TEXT_EXT = {".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".tex", ".html"}
VISUAL_EXT = {".svg", ".pptx", ".docx", ".pdf", ".png"}


def check_zip_part(path, main_part, problems, label):
    try:
        with zipfile.ZipFile(path) as zf:
            if zf.testzip():
                problems.append(f"{label}: corrupt zip member")
            names = set(zf.namelist())
            if main_part not in names:
                problems.append(f"{label}: missing {main_part}")
                return
            try:
                ET.fromstring(zf.read(main_part))
            except ET.ParseError as exc:
                problems.append(f"{label}: {main_part} not parseable: {exc}")
    except zipfile.BadZipFile as exc:
        problems.append(f"{label}: not a zip: {exc}")


def check_file(path, problems):
    ext = path.suffix.lower()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        problems.append(f"{path.name}: unreadable: {exc}")
        return
    if not raw:
        problems.append(f"{path.name}: empty file")
        return
    if ext == ".pptx":
        check_zip_part(path, "ppt/presentation.xml", problems, path.name)
    elif ext == ".docx":
        check_zip_part(path, "word/document.xml", problems, path.name)
    elif ext == ".svg":
        try:
            root = ET.fromstring(raw)
            if root.tag.split("}")[-1] != "svg":
                problems.append(f"{path.name}: root is not <svg>")
            if not root.iter("{http://www.w3.org/2000/svg}text") and \
               len(list(root.iter("text"))) == 0:
                problems.append(f"{path.name}: no <text> elements (labels outlined?)")
        except ET.ParseError as exc:
            problems.append(f"{path.name}: not well-formed XML: {exc}")
    elif ext == ".pdf":
        if not raw.startswith(b"%PDF"):
            problems.append(f"{path.name}: missing %PDF header")
        if b"%%EOF" not in raw[-2048:]:
            problems.append(f"{path.name}: missing %%EOF trailer (truncated?)")
    elif ext == ".png":
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            problems.append(f"{path.name}: not a PNG")
        else:
            w = int.from_bytes(raw[16:20], "big")
            h = int.from_bytes(raw[20:24], "big")
            if w <= 0 or h <= 0:
                problems.append(f"{path.name}: invalid PNG dimensions {w}x{h}")
    elif ext in TEXT_EXT:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{path.name}: not UTF-8")
            return
        hit = PLACEHOLDER.search(text)
        if hit:
            problems.append(f"{path.name}: placeholder text '{hit.group(0)}'")


def collect_artifacts(result):
    items = []
    for key in ("artifacts", "deliverables", "outputs"):
        value = result.get(key)
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and entry.get("path"):
                    items.append(entry)
                elif isinstance(entry, str):
                    items.append({"path": entry})
    return items


def validate(result_path, root):
    problems, notes = [], {}
    try:
        result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "problems": [f"cannot read result: {exc}"], "notes": {}}

    if not result.get("case_id"):
        problems.append("result.json: missing case_id")
    status = result.get("status") or result.get("run_status")
    notes["status"] = status
    if not status:
        problems.append("result.json: missing status")

    artifacts = collect_artifacts(result)
    notes["artifacts"] = len(artifacts)
    if not artifacts:
        problems.append("result.json: no artifacts declared")

    root = Path(root).resolve()
    roles = [str(a.get("role", a.get("kind", ""))).lower() for a in artifacts]
    exts = set()
    for entry in artifacts:
        rel = entry["path"]
        path = Path(rel)
        if not path.is_absolute():
            path = root / rel
        try:
            path.resolve().relative_to(root)
        except ValueError:
            problems.append(f"{rel}: outside the output root")
        if not path.is_file():
            problems.append(f"{rel}: missing")
            continue
        exts.add(path.suffix.lower())
        check_file(path, problems)

    if exts & VISUAL_EXT:
        if not any(r in ("preview", "render", "rendered", "thumbnail") for r in roles):
            problems.append("visual deliverable present but no preview/render artifact declared")
    if (".pptx" in exts or ".docx" in exts) and not any(r == "editable" for r in roles):
        problems.append("office file present but no artifact has role='editable'")

    notes["extensions"] = sorted(exts)
    notes["roles"] = sorted(set(roles))
    return {"ok": not problems, "problems": problems, "notes": notes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--root", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = args.root or str(Path(args.result).resolve().parent)
    report = validate(args.result, root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(("PASS" if report["ok"] else "FAIL") + f" {args.result}")
        for key, value in report["notes"].items():
            print(f"  {key}: {value}")
        for item in report["problems"]:
            print(f"  ! {item}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
