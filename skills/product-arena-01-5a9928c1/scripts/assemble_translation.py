#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble batch translations into output/translation.md + bilingual.md.

Reads every work/translation-batches/batch-*.json in order, requires a target
for each segment, and writes both deliverables plus a glossary.  Missing targets
are reported honestly instead of being filled with the source or invented text.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    work, out = Path(args.work), Path(args.output)
    batch_dir = work / "translation-batches"
    files = sorted(batch_dir.glob("batch-*.json"))
    if not files:
        print(json.dumps({"ok": False, "reason": "no batch files"}))
        return 0
    rows = []
    missing = []
    glossary = []
    for path in files:
        data = read_json(path, {})
        if data.get("glossary") and not glossary:
            glossary = data["glossary"]
        for segment in data.get("segments") or []:
            sid = str(segment.get("segment_id") or "")
            source = str(segment.get("source") or "")
            target = str(segment.get("target") or "").strip()
            rows.append({"segment_id": sid, "source": source, "target": target})
            if not target:
                missing.append(sid)
    rows.sort(key=lambda row: row["segment_id"])
    translation = "\n\n".join(row["target"] for row in rows if row["target"])
    bilingual = "\n\n".join(
        "## %s\n\n- **Source:** %s\n- **Target:** %s" % (
            row["segment_id"], row["source"], row["target"])
        for row in rows)
    write_text(out / "translation.md", (translation or "").rstrip() + "\n")
    write_text(out / "bilingual.md", bilingual.rstrip() + "\n")
    if glossary:
        write_json(work / "glossary.json", {"terms": glossary})
    status = "complete" if not missing else "partial"
    summary = {"ok": True, "segments": len(rows), "translated": len(rows) - len(missing),
               "missing": len(missing), "status": status,
               "missing_ids": missing[:50]}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
