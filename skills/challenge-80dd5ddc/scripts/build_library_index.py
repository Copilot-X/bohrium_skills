#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a deterministic, locatable index for a research file library.

Usage:
    python3 build_library_index.py --input /input --out /app/work/library-index-build.json --json
    python3 build_library_index.py --input /input --ocr --out ... --json

Output (model-facing, not a final answer):
  * every readable file with sha256, kind, bytes and unreadable warnings;
  * section headings and paragraph blocks with line/page locators, so a hit
    can be reported as "file + version + page/section + line + first sentence";
  * exact-duplicate groups (same sha256 or same normalized full text);
  * conservative version groups from filenames (v1/v2/revised/final/修订/终版)
    plus administrative notes (supersedes/replaces/obsolete/作废);
  * alias mentions (alias/aka/又名/别名/曾用名) with their source locator.

The script never decides relevance, filters or ranking.  It only makes the
library and its version/alias structure auditable before semantic search.

Exit codes: 0 index written, 2 input missing.
Stdlib only (reuses skillcore/parsers from the same scripts directory).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import skillcore as S  # noqa: E402

VERSION_SUFFIX = re.compile(
    r"(?i)(?:[\s._\-]+(?:v(?:er(?:sion)?)?[\s._\-]*\d+(?:\.\d+)*"
    r"|rev(?:ision)?[\s._\-]*\d*|revised|draft|final|copy"
    r"|副本|修订版?|终版|定稿|改版|旧版|新版)[\s._\-]*)+$")
ADMIN_NOTE = re.compile(
    r"(?i)(supersed\w*|replac\w*|obsolete|deprecat\w*|withdrawn|"
    r"作废|替代|取代|已废弃|不再使用|版本说明|version\s+note)")
ALIAS_NOTE = re.compile(
    r"(?i)\b(?:alias(?:es)?|aka|a\.k\.a\.|also\s+known\s+as|"
    r"previous\s+name|old\s+name)\b\s*[:：=\-]?\s*(.+)$|"
    r"(?:别名|又名|曾用名|旧名|项目别名)\s*[:：=\-]?\s*(.+)$")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
WS = re.compile(r"\s+")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def norm_text(text):
    return WS.sub(" ", (text or "").replace(" ", " ")).strip().lower()


def stem_key(name):
    stem = Path(name).stem
    previous = None
    while previous != stem:
        previous = stem
        stem = VERSION_SUFFIX.sub("", stem)
    return stem.strip(" ._-") or Path(name).stem


def version_token(name):
    lowered = Path(name).stem.lower()
    match = re.search(
        r"(?i)(v(?:er(?:sion)?)?[\s._\-]*\d+(?:\.\d+)*"
        r"|rev(?:ision)?[\s._\-]*\d*|revised|draft|final|copy"
        r"|副本|修订版?|终版|定稿|改版|旧版|新版)", lowered)
    return match.group(1) if match else ""


def blocks_for(doc):
    blocks = []
    for index, block in enumerate(doc.get("blocks") or []):
        if not isinstance(block, dict):
            continue
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        line = block.get("line") or block.get("line_start") or block.get("start")
        page = block.get("page")
        blocks.append({
            "block_id": "B%03d" % (index + 1),
            "page": page,
            "line": line,
            "line_end": block.get("line_end") or block.get("end"),
            "kind": block.get("kind") or "",
            "text": text[:4000],
            "first_sentence": sent[0].get("text", text[:240])
            if (sent := S.split_sentences(text)) else text[:240],
        })
        if len(blocks) >= 800:
            break
    return blocks


def markdown_sections(text):
    sections = []
    current = {"title": "", "line_start": 1, "line_end": None}
    for number, line in enumerate((text or "").splitlines(), 1):
        match = HEADING.match(line)
        if match:
            if current["title"] or number > 1:
                current["line_end"] = number - 1
                if current["title"]:
                    sections.append(dict(current))
            current = {"title": match.group(2).strip(), "level": len(match.group(1)),
                       "line_start": number, "line_end": None}
    current["line_end"] = len((text or "").splitlines())
    if current["title"]:
        sections.append(dict(current))
    return sections


def notes_from_doc(text, rel_path):
    notes = []
    for number, line in enumerate((text or "").splitlines(), 1):
        if not line.strip() or len(line) > 800:
            continue
        if ADMIN_NOTE.search(line):
            notes.append({"kind": "administrative_version_note",
                          "path": rel_path, "locator": "L%d" % number,
                          "quote": line.strip()})
        alias = ALIAS_NOTE.search(line)
        if alias:
            value = (alias.group(1) or alias.group(2) or "").strip(" .;；")
            if value:
                notes.append({"kind": "alias", "alias": value,
                              "path": rel_path, "locator": "L%d" % number,
                              "quote": line.strip()})
        if len(notes) >= 200:
            break
    return notes


def build(input_dir, ocr=False):
    root = Path(input_dir)
    if not root.is_dir():
        raise FileNotFoundError("input not found: %s" % root)
    inv, docs = S.load_materials(root, ocr=ocr, tmp_dir=root / ".ocr-index")
    files = []
    duplicate_hashes, duplicate_texts = {}, {}
    version_groups = {}
    notes = []
    text_by_path = {}
    for doc in docs:
        rel = doc.get("rel_path") or ""
        text = S.doc_text(doc)
        text_by_path[rel] = text
        raw_bytes = b""
        try:
            raw_bytes = (root / rel).read_bytes()
        except OSError:
            pass
        digest = sha256_bytes(raw_bytes) if raw_bytes else ""
        duplicate_hashes.setdefault(digest, []).append(rel) if digest else None
        normalized = norm_text(text)
        if len(normalized) >= 120:
            duplicate_texts.setdefault(normalized, []).append(rel)
        version_groups.setdefault(stem_key(rel), []).append({
            "path": rel, "version_token": version_token(rel),
            "sha256": digest,
        })
        notes.extend(notes_from_doc(text, rel))
        files.append({
            "path": rel,
            "source_id": doc.get("source_id"),
            "kind": doc.get("kind"),
            "bytes": doc.get("bytes"),
            "sha256": digest,
            "chars": len(text),
            "readable": bool(text.strip()),
            "warnings": doc.get("warnings") or [],
            "version_token": version_token(rel),
            "version_family": stem_key(rel),
            "sections": markdown_sections(text),
            "blocks": blocks_for(doc),
        })
    # Files the text extractor does not read (scans/images/legacy office) are
    # still part of the library and must be reported, not silently dropped.
    indexed = {doc.get("rel_path") for doc in docs}
    for row in inv:
        if row.get("role") != "material" or row.get("path") in indexed:
            continue
        reason = ("image file; no text layer unless the OCR tool is run"
                  if row.get("kind") == "image"
                  else "format requires an external converter; not read")
        files.append({
            "path": row.get("path"),
            "source_id": None,
            "kind": row.get("kind"),
            "bytes": row.get("bytes"),
            "sha256": row.get("sha256") or "",
            "chars": 0,
            "readable": False,
            "warnings": [reason],
            "version_token": version_token(row.get("path") or ""),
            "version_family": stem_key(row.get("path") or ""),
            "sections": [],
            "blocks": [],
        })
    exact_duplicates = [
        {"basis": "sha256", "sha256": digest, "paths": paths}
        for digest, paths in duplicate_hashes.items() if len(paths) > 1]
    text_duplicates = [
        {"basis": "normalized_text", "paths": paths}
        for paths in duplicate_texts.values() if len(paths) > 1]
    versions = [
        {"family": family, "members": members}
        for family, members in version_groups.items() if len(members) > 1]
    unreadable = [
        {"path": item["path"], "warnings": item["warnings"],
         "reason": "no extractable text; scanned page / OCR not run or failed"}
        for item in files if not item["readable"]]
    return {
        "input_dir": str(root),
        "files": files,
        "counts": {
            "files": len(files),
            "readable": sum(1 for item in files if item["readable"]),
            "unreadable": len(unreadable),
            "sections": sum(len(item["sections"]) for item in files),
            "blocks": sum(len(item["blocks"]) for item in files),
            "exact_duplicate_groups": len(exact_duplicates),
            "text_duplicate_groups": len(text_duplicates),
            "version_groups": len(versions),
        },
        "duplicate_groups": exact_duplicates + text_duplicates,
        "version_groups": versions,
        "notes": notes,
        "unreadable": unreadable,
        "how_to_cite": ("Use path + version + page (PDF) or section/line "
                        "(text) + the first sentence as locator, plus a "
                        "verbatim quote from the block."),
    }


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "projectA_batch3_v1.md").write_text(
            "# Buffer\n\n[paragraph p1] The running buffer was 20 mM Tris-HCl, pH 7.4.\n",
            encoding="utf-8")
        (root / "projectA_batch3_v2.md").write_text(
            "# Buffer\n\n[paragraph p1] The running buffer was changed to 25 mM HEPES, pH 7.8.\n"
            "Version note: v2 supersedes v1.\nAlias: PA-batch3\n",
            encoding="utf-8")
        (root / "projectA_batch3_v1_copy.md").write_bytes(
            (root / "projectA_batch3_v1.md").read_bytes())
        payload = build(root)
    ok = (payload["counts"]["files"] == 3
          and payload["counts"]["exact_duplicate_groups"] >= 1
          and payload["counts"]["version_groups"] >= 1
          and any(n["kind"] == "alias" for n in payload["notes"])
          and any(n["kind"] == "administrative_version_note"
                  for n in payload["notes"]))
    return {"ok": ok, "self_test": True, "counts": payload["counts"],
            "notes": payload["notes"][:4]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--ocr", action="store_true")
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
        payload = build(args.input, ocr=args.ocr)
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
        print(json.dumps(payload["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
