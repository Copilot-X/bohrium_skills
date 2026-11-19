#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inventory the real input files and extract a locatable manuscript text.

Usage:
    python3 inventory.py --input /input --work /app/work [--manuscript PATH] [--json]

Writes ``<work>/inventory.json``, ``<work>/texts/*.txt`` and, when a readable
manuscript is found, ``<work>/manuscript.txt`` plus a source copy used for
structure parsing. PDF text extraction only uses tools available at runtime
(pdftotext / mutool / pdf-text-tool); if none exists the file is recorded as
unreadable instead of inventing content. Stdlib only, no network.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import structure_kit as kit  # noqa: E402

MAX_TEXT_CHARS = 500_000
PDF_TIMEOUT = 30
CASE_PATH_KEYS = {
    "path", "file", "filename", "href", "manuscript", "manuscript_path",
    "paper", "draft", "document", "source",
}
NAME_HINTS = ("manuscript", "paper", "draft", "thesis", "article", "稿件",
              "正文", "论文", "论文正文")


def file_role(rel_path):
    name = Path(rel_path).name.lower()
    if name in kit.TASK_METADATA_NAMES:
        return "task_metadata"
    if name.startswith(("expected-", "expected_", "gold-", "reference-answer")):
        return "task_metadata"
    return "material"


def classify(path):
    suffix = path.suffix.lower()
    if suffix in kit.DOCX_EXTS:
        return "docx"
    if suffix in kit.PDF_EXTS:
        return "pdf"
    if suffix in kit.IMAGE_EXTS:
        return "image"
    if suffix in kit.TEXT_EXTS:
        return "text"
    if suffix == ".json":
        return "json"
    return "binary"


def extract_docx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("word/document.xml")
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        return None, "docx_unreadable: %s" % exc
    xml = raw.decode("utf-8", errors="replace")
    xml = re.sub(r"</w:(p|tr)>", "\n", xml)
    xml = re.sub(r"<w:(br|tab)/>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return None, "docx_has_no_extractable_text"
    return text, ""


def _run_pdf_tool(cmd):
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=PDF_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.decode("utf-8", errors="replace")
    return text if text.strip() else None


def extract_pdf(path):
    candidates = []
    for name, args in (
            ("pdftotext", ["-layout", str(path), "-"]),
            ("mutool", ["draw", "-F", "txt", "-o", "-", str(path)]),
            ("pdf-text-tool", [str(path)]),
            ("pdf-text-tool", ["--input", str(path)])):
        tool = shutil.which(name)
        if tool:
            candidates.append([tool] + args)
    for cmd in candidates:
        text = _run_pdf_tool(cmd)
        if text:
            text = text.replace("\r\n", "\n")
            return text, "extracted_with=%s" % Path(cmd[0]).name
    return None, ("pdf_text_unavailable: use the runtime pdf-text-tool or "
                  "pdftotext; the file is listed but not quoted")


def _path_strings(value, depth=0):
    if depth > 4:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in CASE_PATH_KEYS and isinstance(item, str):
                yield item
            else:
                yield from _path_strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _path_strings(item, depth + 1)


def _resolve(value, root):
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return None
    if text.startswith("/input/"):
        text = text[len("/input/"):]
    candidate = (Path(root) / text).resolve()
    try:
        candidate.relative_to(Path(root).resolve())
    except ValueError:
        return None
    if candidate.is_file() and candidate.suffix.lower() in kit.MANUSCRIPT_EXTS:
        return candidate
    return None


def _score(path):
    name = path.name.lower()
    score = 0
    if any(hint in name for hint in NAME_HINTS):
        score += 5
    if path.suffix.lower() in {".md", ".markdown", ".tex", ".docx"}:
        score += 2
    try:
        score += min(path.stat().st_size // 10000, 5)
    except OSError:
        pass
    return score


def discover_manuscript(input_dir):
    root = Path(input_dir)
    if not root.is_dir():
        return None, "input_missing"
    case_path = root / "case.json"
    case_data = kit.read_json(case_path, default=None)
    if isinstance(case_data, dict):
        candidates = []
        for key in ("manuscript", "manuscript_path", "paper", "draft",
                    "document", "source", "files", "materials", "inputs"):
            if key in case_data:
                for value in _path_strings(case_data[key]):
                    resolved = _resolve(value, root)
                    if resolved is not None and resolved not in candidates:
                        candidates.append(resolved)
        if candidates:
            candidates.sort(key=lambda p: (_score(p), p.stat().st_size),
                            reverse=True)
            return candidates[0], "case.json"
    candidates = []
    for path in root.rglob("*"):
        try:
            if not path.is_file() or path.stat().st_size < 50:
                continue
            rel = path.relative_to(root)
        except OSError:
            continue
        if any(part in kit.SKIP_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() not in kit.MANUSCRIPT_EXTS:
            continue
        if path.name.lower() in kit.TASK_METADATA_NAMES:
            continue
        if path.name.lower().startswith(("expected-", "expected_", "gold-")):
            continue
        candidates.append((_score(path), path.stat().st_size, str(path), path))
    if not candidates:
        return None, "no_manuscript_candidate"
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][3], "scan"


def scan(input_dir, work_dir, manuscript_path=None):
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    texts_dir = work_dir / "texts"
    texts_dir.mkdir(parents=True, exist_ok=True)
    uncertainty = []
    if manuscript_path:
        chosen = Path(manuscript_path)
        discovery = "explicit" if chosen.is_file() else "missing"
    else:
        chosen, discovery = discover_manuscript(input_dir)

    records = []
    if not input_dir.is_dir():
        uncertainty.append({
            "item": "input_missing",
            "reason": str(input_dir),
            "impact": "no material was read",
            "next_check": "check the mounted /input path",
        })
        result = {
            "schema": "inventory/v1",
            "status": "input_missing",
            "input_dir": str(input_dir),
            "files": [],
            "file_count": 0,
            "readable_texts": 0,
            "unreadable_texts": 0,
            "manuscript": None,
            "discovery": discovery,
            "uncertainty": uncertainty,
            "generated_at": kit.now_iso(),
        }
        kit.write_json(work_dir / "inventory.json", result)
        return result

    all_files = []
    for path in sorted(input_dir.rglob("*")):
        try:
            if not path.is_file():
                continue
            rel = path.relative_to(input_dir)
        except OSError:
            continue
        if any(part in kit.SKIP_DIRS for part in rel.parts):
            continue
        all_files.append(path)

    chosen_resolved = chosen.resolve() if chosen is not None else None
    manuscript_record = None
    for index, path in enumerate(all_files):
        rel = kit.rel_posix(path, input_dir)
        kind = classify(path)
        role = file_role(rel)
        if chosen_resolved is not None and path.resolve() == chosen_resolved:
            role = "manuscript"
        record = {
            "path": rel,
            "name": path.name,
            "ext": path.suffix.lower(),
            "kind": kind,
            "role": role,
            "size_bytes": path.stat().st_size,
            "sha256": kit.sha256_file(path),
            "readable": False,
            "text_path": None,
            "chars": 0,
            "locator_style": "line",
            "note": "",
        }
        text = None
        note = ""
        try:
            if kind == "text":
                text = kit.read_text_file(path)
            elif kind == "docx":
                text, note = extract_docx(path)
                record["locator_style"] = "paragraph"
            elif kind == "pdf":
                text, note = extract_pdf(path)
                record["locator_style"] = "page"
            elif kind == "image":
                note = "image_only: no OCR in this stdlib pipeline"
            elif kind == "json":
                note = "metadata/json: not treated as manuscript prose"
            else:
                note = "binary_or_unsupported: not quoted"
        except OSError as exc:
            note = "read_error: %s" % exc
        if text and text.strip():
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS]
                note = (note + "; " if note else "") + \
                    "truncated_at_%d_chars" % MAX_TEXT_CHARS
            text_path = texts_dir / ("%04d_%s.txt" % (
                index, kit.safe_slug(path.stem)))
            kit.write_text(text_path, text)
            record.update({
                "readable": True,
                "text_path": text_path.relative_to(work_dir).as_posix(),
                "chars": len(text),
                "language": kit.detect_language(text),
                "note": note,
            })
            if role == "manuscript":
                manuscript_record = record
        else:
            record["note"] = note or "no_extractable_text"
            if role == "manuscript":
                manuscript_record = record
            if role != "task_metadata":
                uncertainty.append({
                    "item": "unreadable_material",
                    "reason": "%s: %s" % (rel, record["note"]),
                    "impact": "content from this file cannot be quoted",
                    "next_check": "use the runtime PDF/OCR tool or ask for text",
                })
        records.append(record)

    manuscript = None
    if manuscript_record is None:
        uncertainty.append({
            "item": "no_manuscript",
            "reason": "no readable manuscript candidate under %s (%s)"
                      % (input_dir, discovery),
            "impact": "the review cannot be grounded; no finding is invented",
            "next_check": "pass --manuscript or check case.json paths",
        })
        status = "no_manuscript"
    elif not manuscript_record.get("readable"):
        uncertainty.append({
            "item": "manuscript_unreadable",
            "reason": manuscript_record.get("note", "unreadable"),
            "impact": "six dimensions cannot be grounded",
            "next_check": "convert the manuscript to text and rerun",
        })
        status = "manuscript_unreadable"
    else:
        text = kit.read_text_file(work_dir / manuscript_record["text_path"])
        kit.write_text(work_dir / "manuscript.txt", text)
        suffix = Path(manuscript_record["path"]).suffix.lower()
        map_name = "manuscript_source" + (suffix if suffix in kit.TEXT_EXTS
                                          else ".txt")
        kit.write_text(work_dir / map_name, text)
        manuscript = {
            "path": manuscript_record["path"],
            "format": {
                ".md": "markdown", ".markdown": "markdown",
                ".tex": "latex", ".latex": "latex", ".docx": "docx",
                ".pdf": "pdf",
            }.get(suffix, "text"),
            "language": manuscript_record.get("language", "unknown"),
            "chars": manuscript_record.get("chars", 0),
            "lines": text.count("\n") + 1,
            "text_path": manuscript_record["text_path"],
            "source_path": map_name,
            "source_abs_path": str((work_dir / map_name).resolve()),
            "original_path": str(Path(manuscript_record["path"])),
            "discovery": discovery,
            "truncated": "truncated_at_" in (manuscript_record.get("note") or ""),
        }
        status = "ok"
        if manuscript["truncated"]:
            uncertainty.append({
                "item": "manuscript_truncated",
                "reason": "text exceeded %d chars" % MAX_TEXT_CHARS,
                "impact": "later sections may be missing",
                "next_check": "review only the extracted prefix",
            })

    readables = sum(1 for rec in records if rec["readable"])
    result = {
        "schema": "inventory/v1",
        "status": status,
        "input_dir": str(input_dir.resolve()),
        "files": records,
        "file_count": len(records),
        "readable_texts": readables,
        "unreadable_texts": len(records) - readables,
        "manuscript": manuscript,
        "discovery": discovery,
        "uncertainty": uncertainty,
        "generated_at": kit.now_iso(),
    }
    kit.write_json(work_dir / "inventory.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--manuscript", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = scan(args.input, args.work, args.manuscript)
    summary = {
        "status": result["status"],
        "file_count": result["file_count"],
        "readable_texts": result["readable_texts"],
        "manuscript": (result.get("manuscript") or {}).get("path"),
        "inventory": str(Path(args.work) / "inventory.json"),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
