#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inventory real input files and extract locatable plain text.

``scan(input_dir, work_dir)`` walks ``/input``, records size/sha256/role,
extracts text from Markdown/TXT/JSON/CSV/DOCX (stdlib) and optionally PDF
when a ``pdftotext``-style runtime tool exists.  Extraction failures are
recorded honestly; content is never guessed from a filename or label.

Usage:
    python3 inventory.py --input /input --work /app/work
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from . import evidence_kit
except ImportError:
    import evidence_kit

MAX_TEXT_BYTES = 4 * 1024 * 1024


def _role(rel_path):
    name = Path(rel_path).name.lower()
    if name in evidence_kit.TASK_METADATA:
        return "task_metadata"
    if name.startswith("expected-") or "/expected/" in "/" + rel_path.replace("\\", "/"):
        return "excluded_expected"
    return "material"


def _safe_name(rel_path):
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", rel_path.replace("\\", "/"))
    return stem.strip("_") or "text"


def _extract(path, work_dir, rel_path):
    suffix = path.suffix.lower()
    if suffix in evidence_kit.TEXT_EXTS:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return None, "text_too_large"
        return evidence_kit.read_text(path), ""
    if suffix in evidence_kit.DOCX_EXTS:
        return evidence_kit.extract_docx(path)
    if suffix in evidence_kit.PDF_EXTS:
        return evidence_kit.extract_pdf(path)
    if suffix in evidence_kit.IMAGE_EXTS:
        return None, "binary_image: use the runtime OCR/vision tool; not parsed here"
    return None, "unsupported_binary_format: %s" % (suffix or "no suffix")


def scan(input_dir, work_dir):
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    texts_dir = work_dir / "texts"
    texts_dir.mkdir(parents=True, exist_ok=True)

    files = []
    uncertainty = []
    if not input_dir.is_dir():
        data = {
            "status": "input_missing",
            "input_dir": str(input_dir),
            "files": [],
            "file_count": 0,
            "readable_texts": 0,
            "unreadable_texts": 0,
            "material_count": 0,
            "uncertainty": [{
                "item": "input_missing",
                "reason": "input directory does not exist: %s" % input_dir,
                "impact": "cannot extract sources or write a supported draft",
            }],
            "generated_at": evidence_kit.now_iso(),
        }
        evidence_kit.write_json(work_dir / "inventory.json", data)
        return data

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        parts = set(part.lower() for part in path.parts)
        if parts & evidence_kit.SKIP_DIRS:
            continue
        rel = path.relative_to(input_dir).as_posix()
        role = _role(rel)
        record = {
            "path": rel,
            "absolute_path": str(path),
            "suffix": path.suffix.lower(),
            "bytes": path.stat().st_size,
            "sha256": evidence_kit.sha256_file(path),
            "role": role,
            "readable": False,
            "chars": 0,
            "text_path": None,
            "extraction_error": "",
        }
        if role == "excluded_expected":
            record["extraction_error"] = "excluded: expected-demo files are not inputs"
            files.append(record)
            continue
        text, error = _extract(path, work_dir, rel)
        if text is not None:
            record["readable"] = True
            record["chars"] = len(text)
            target = texts_dir / (_safe_name(rel) + ".txt")
            evidence_kit.write_text(target, text)
            record["text_path"] = "texts/" + target.name
        else:
            record["extraction_error"] = error
            uncertainty.append({
                "item": "source_unreadable:%s" % rel,
                "reason": error,
                "impact": "claims that depend on this file cannot be asserted",
                "next_check": "convert to text with the runtime PDF/OCR tool or read manually",
            })
        files.append(record)

    materials = [item for item in files if item["role"] == "material"]
    readable = [item for item in materials if item["readable"]]
    data = {
        "status": "ok" if readable else ("input_empty" if not materials else "no_readable_material"),
        "input_dir": str(input_dir),
        "files": files,
        "file_count": len(files),
        "material_count": len(materials),
        "readable_texts": len(readable),
        "unreadable_texts": len(materials) - len(readable),
        "uncertainty": uncertainty,
        "generated_at": evidence_kit.now_iso(),
    }
    evidence_kit.write_json(work_dir / "inventory.json", data)
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    data = scan(args.input, args.work)
    print(json.dumps({key: data.get(key) for key in
                      ("status", "file_count", "material_count",
                       "readable_texts", "unreadable_texts", "uncertainty")},
                     ensure_ascii=False, indent=2))
    return 0 if data.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
