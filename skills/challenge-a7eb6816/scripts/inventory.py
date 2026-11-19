#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inventory real input files and extract readable text.

Usage:
    python3 inventory.py --input /input --work /app/work [--json]

Writes ``<work>/inventory.json`` and ``<work>/texts/*.txt``.  PDF/DOCX are
read with stdlib only where possible; PDF text extraction tries the optional
runtime converters (pdftotext / mutool / pdf-text-tool) and records an honest
``readable=false`` note when none is available.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # package mode (python -m scripts.inventory)
    from . import transfer_kit as kit
except ImportError:  # direct mode (python scripts/inventory.py)
    kit = __import__("transfer_kit")

MAX_TEXT_CHARS = 400_000
PDF_TIMEOUT = 30
TASK_METADATA_NAMES = {
    "case.json", "task.md", "task_instruction.md", "example-input.json",
    "output-contract.md", "output_contract.md",
}


def file_role(rel_path):
    """Task instructions/contracts and expected outputs are not material."""
    name = Path(rel_path).name.lower()
    if name in TASK_METADATA_NAMES:
        return "task_metadata"
    if name.startswith(("expected-", "expected_", "reference-answer", "gold-")):
        return "task_metadata"
    return "material"


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:80] or "file"


def extract_docx(path):
    """Extract paragraph text from word/document.xml without python-docx."""
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("word/document.xml")
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        return None, "docx_unreadable: %s" % exc
    xml = raw.decode("utf-8", errors="replace")
    xml = re.sub(r"</w:(p|tr)>", "\n", xml)
    xml = re.sub(r"<w:(br|tab)/>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, None


def _run_pdf_tool(cmd, timeout=PDF_TIMEOUT):
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode("utf-8", errors="replace")
    return out if out.strip() else None


def extract_pdf(path):
    """Try common PDF text extractors; never fail the whole run."""
    candidates = []
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        candidates.append([pdftotext, "-layout", str(path), "-"])
    mutool = shutil.which("mutool")
    if mutool:
        candidates.append([mutool, "draw", "-F", "txt", "-o", "-", str(path)])
    pdf_tool = shutil.which("pdf-text-tool")
    if pdf_tool:
        candidates.append([pdf_tool, str(path)])
        candidates.append([pdf_tool, "--input", str(path)])
    for cmd in candidates:
        text = _run_pdf_tool(cmd)
        if text:
            text = text.replace("\r\n", "\n")
            if "\f" not in text and "pdf-text-tool" not in cmd[0]:
                text = re.sub(r"\n\s*\n", "\n\f", text, count=0)
            return text, None, cmd[0]
    return None, ("pdf_text_unavailable: install/use pdf-text-tool or "
                  "pdftotext at runtime; file listed but not quoted"), None


def classify_file(path):
    suffix = path.suffix.lower()
    if suffix in kit.DOCX_EXTS:
        return "docx"
    if suffix in kit.PDF_EXTS:
        return "pdf"
    if suffix in kit.IMAGE_EXTS:
        return "image"
    if suffix in kit.TEXT_EXTS:
        return "text"
    return "binary"


def scan(input_dir, work_dir):
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    texts_dir = work_dir / "texts"
    texts_dir.mkdir(parents=True, exist_ok=True)
    files = []
    uncertainty = []
    if not input_dir.exists():
        uncertainty.append({
            "item": "input_missing",
            "detail": str(input_dir),
            "action": "check the mounted input path; no material was read",
        })
        result = {"status": "input_missing", "input_dir": str(input_dir),
                  "files": [], "readable_texts": 0,
                  "unreadable_texts": 0, "uncertainty": uncertainty}
        kit.write_json(work_dir / "inventory.json", result)
        return result

    entries = []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if any(part in kit.SKIP_DIRS for part in p.parts):
            continue
        entries.append(p)

    for idx, path in enumerate(entries):
        rel = kit.rel_posix(path, input_dir)
        kind = classify_file(path)
        rec = {
            "path": rel,
            "abs_path": str(path.resolve()),
            "name": path.name,
            "ext": path.suffix.lower(),
            "kind": kind,
            "role": file_role(rel),
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
                text = kit.read_text(path, limit=MAX_TEXT_CHARS * 4)
                if len(text) > MAX_TEXT_CHARS:
                    text = text[:MAX_TEXT_CHARS]
                    note = "truncated_at_%d_chars" % MAX_TEXT_CHARS
            elif kind == "docx":
                text, note = extract_docx(path)
                rec["locator_style"] = "paragraph"
            elif kind == "pdf":
                text, note, used = extract_pdf(path)
                rec["locator_style"] = "page"
                if used:
                    note = "extracted_with=%s" % Path(used).name
            elif kind == "image":
                note = "image_only: no OCR in stdlib pipeline; use runtime OCR if needed"
            else:
                note = "binary_or_unknown_format: not quoted"
        except OSError as exc:
            note = "read_error: %s" % exc

        if text and text.strip():
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS]
                note = (note + "; " if note else "") + "truncated_at_%d_chars" % MAX_TEXT_CHARS
            dest = texts_dir / ("%04d_%s.txt" % (idx, _safe_name(path.stem)))
            kit.write_text(dest, text)
            rec.update({
                "readable": True,
                "text_path": dest.relative_to(work_dir).as_posix(),
                "chars": len(text),
                "note": note,
                "language": kit.guess_language(text),
            })
        else:
            rec["note"] = note or "no_extractable_text"
            uncertainty.append({
                "item": "unreadable_material",
                "path": rel,
                "detail": rec["note"],
                "action": "use the runtime PDF/OCR tool or ask for a text copy; do not invent content",
            })
        files.append(rec)

    readable = sum(1 for f in files if f["readable"])
    if not readable:
        uncertainty.append({
            "item": "no_readable_text",
            "detail": "%d files scanned but none yielded text" % len(files),
            "action": "deliver a partial draft that states the material gap",
        })
    result = {
        "status": "ok" if readable else "no_readable_text",
        "input_dir": str(input_dir.resolve()),
        "files": files,
        "file_count": len(files),
        "readable_texts": readable,
        "unreadable_texts": len(files) - readable,
        "uncertainty": uncertainty,
        "generated_at": kit.now_iso(),
    }
    kit.write_json(work_dir / "inventory.json", result)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = scan(args.input, args.work)
    summary = {k: result[k] for k in
               ("status", "file_count", "readable_texts", "unreadable_texts")}
    summary["inventory"] = str(Path(args.work) / "inventory.json")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["readable_texts"] else 1


if __name__ == "__main__":
    sys.exit(main())
