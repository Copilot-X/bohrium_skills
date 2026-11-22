#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Index a finite file library with hashes, metadata and version groups.

Builds a real inventory from the supplied files: path, bytes, sha256, title,
authors/year/DOI when present, alias/version hints from names, duplicate groups
and version chains.  It does not move or rename the user's files; operations
are recorded as index/mark actions with an explicit rollback field.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


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


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def title_of(doc):
    text = doc.get("text") or doc.get("excerpt") or ""
    for line in text.splitlines():
        line = norm(line)
        if line.startswith("#"):
            return norm(line.lstrip("#"))
        if 20 <= len(line) <= 200 and not DOI_RE.search(line):
            return line
    return Path(doc.get("path") or doc.get("rel_path") or "untitled").stem


def alias_hints(path):
    name = Path(path).stem
    hints = []
    if re.search(r"preprint|arxiv|biorxiv|medrxiv", name, re.I):
        hints.append("preprint")
    if re.search(r"supp|supplement", name, re.I):
        hints.append("supplement")
    if re.search(r"copy|\(\d+\)|duplicate|final|revised|v\d+", name, re.I):
        hints.append("copy_or_version")
    return hints


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
    materials = read_json(work / "materials.json", {})
    docs = materials.get("documents") or materials.get("materials") or []
    items, operations, skipped, evidence = [], [], [], []
    by_hash, by_doi, by_title = {}, {}, {}
    for index, doc in enumerate(docs, 1):
        path = doc.get("path") or doc.get("rel_path") or ""
        text = doc.get("text") or doc.get("excerpt") or ""
        if not path:
            skipped.append({"path": "", "reason": "missing path"})
            continue
        full = inp / path
        if not full.is_file():
            skipped.append({"path": path, "reason": "file not readable"})
            continue
        data = full.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        title = title_of(doc)
        doi_match = DOI_RE.search(text)
        doi = doi_match.group(0).lower() if doi_match else ""
        year_match = YEAR_RE.search(text)
        year = year_match.group(0) if year_match else ""
        item = {
            "id": "I%03d" % index, "path": path, "bytes": len(data),
            "sha256": digest, "source": path, "permissions": "read_only",
            "status": "indexed", "target": "library/%s/%s" % (
                year or "unknown", re.sub(r"[^\w.-]+", "-", title)[:80]),
            "title": title, "doi": doi, "year": year,
            "aliases": alias_hints(path),
            "evidence": {"path": path, "locator": "L1",
                         "quote": next((norm(x) for x in text.splitlines()
                                        if norm(x)), title)},
        }
        evidence.append(item["evidence"])
        items.append(item)
        by_hash.setdefault(digest, []).append(item["id"])
        if doi:
            by_doi.setdefault(doi, []).append(item["id"])
        by_title.setdefault(re.sub(r"[^a-z0-9]+", " ", title.lower()).strip(),
                            []).append(item["id"])
        operations.append({
            "id": "O%03d" % (len(operations) + 1),
            "item_id": item["id"], "action": "index",
            "from": path, "to": item["target"],
            "rollback": "remove index entry; source file was not modified",
            "status": "ok",
        })
    duplicate_groups = [{"basis": "sha256", "key": digest, "items": ids}
                        for digest, ids in by_hash.items() if len(ids) > 1]
    duplicate_groups += [{"basis": "doi", "key": doi, "items": ids}
                         for doi, ids in by_doi.items() if len(ids) > 1]
    version_chain = []
    for title, ids in by_title.items():
        if len(ids) > 1:
            version_chain.append({
                "title_key": title, "items": ids,
                "basis": "same normalized title; inspect aliases/year/DOI",
            })
    ev_lines = ["# Library evidence index", ""]
    for item in items:
        ev_lines.append("%s | %s | %s | %s" % (
            item["id"], item["path"], item["bytes"], item["sha256"]))
        item["evidence"] = {"path": "work/library-evidence.md",
                            "locator": item["id"],
                            "quote": "%s | %s | %s | %s" % (
                                item["id"], item["path"], item["bytes"],
                                item["sha256"])}
    write_text_md = Path(work) / "library-evidence.md"
    write_text_md.write_text("\n".join(ev_lines) + "\n", encoding="utf-8")
    plan = {
        "case_id": case.get("case_id") or "UNKNOWN",
        "mode": "file-management",
        "summary": "索引 %d 个文件；重复组 %d；版本链 %d；跳过 %d。" % (
            len(items), len(duplicate_groups), len(version_chain), len(skipped)),
        "items": items, "operations": operations,
        "duplicate_groups": duplicate_groups, "version_chain": version_chain,
        "skipped": skipped, "evidence": [item["evidence"] for item in items],
        "operation_evidence": operations,
        "failure_recovery": {"skipped": skipped,
                             "rollback": "index entries can be removed without "
                                         "touching the source files"},
        "uncertainties": ([{"item": "version_chain",
                            "reason": "version groups use normalized titles",
                            "impact": "year/DOI/alias must be checked manually",
                            "action": "inspect each group before merging"}]
                          if version_chain else []),
        "generated_at": now_iso(), "generated_by": "library_inventory.py",
    }
    write_json(work / "library-plan.json", plan)
    write_json(out / "library-results.json", {
        "case_id": plan["case_id"], "summary": plan["summary"],
        "items": items, "duplicate_groups": duplicate_groups,
        "version_chain": version_chain, "skipped": skipped,
        "evidence": [item["evidence"] for item in items],
        "operation_evidence": operations,
        "failure_recovery": plan["failure_recovery"]})
    write_json(out / "library-index.json", {
        "case_id": plan["case_id"],
        "index": [{"id": item["id"], "path": item["path"],
                   "title": item["title"], "doi": item["doi"],
                   "year": item["year"], "sha256": item["sha256"]}
                  for item in items]})
    write_json(out / "operations.log.json", {
        "case_id": plan["case_id"], "operations": operations})
    summary = {"ok": True, "items": len(items),
               "duplicates": len(duplicate_groups),
               "versions": len(version_chain), "skipped": len(skipped)}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
