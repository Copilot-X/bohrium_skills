#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create small translation batches from work/segments.json.

The model fills only ``target`` in each batch JSON instead of rewriting the
whole translation file every turn.  This keeps a long paper inside the 64-request
/ 900-second budget and avoids repeated file rewrites.
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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--size", type=int, default=12)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    work, out = Path(args.work), Path(args.output)
    segments_doc = read_json(work / "segments.json", {})
    segments = segments_doc.get("segments") or []
    if not segments:
        print(json.dumps({"ok": False, "reason": "no segments; run prepare first"}))
        return 0
    glossary_doc = read_json(work / "glossary.json", {})
    glossary = (glossary_doc.get("terms")
                if isinstance(glossary_doc, dict) else glossary_doc) or []
    candidates_doc = read_json(work / "glossary-candidates.json", {})
    candidates = (candidates_doc.get("terms")
                  if isinstance(candidates_doc, dict) else candidates_doc) or []
    if not glossary:
        glossary = candidates
    batch_dir = work / "translation-batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    for old in batch_dir.glob("batch-*.json"):
        old.unlink()
    count = 0
    for start in range(0, len(segments), max(1, args.size)):
        chunk = segments[start:start + args.size]
        batch = {
            "case_id": segments_doc.get("case_id") or "UNKNOWN",
            "batch_id": "B%03d" % (start // max(1, args.size) + 1),
            "glossary": glossary[:40],
            "instructions": "Translate each source into the target language. "
                            "Keep numbers, units, citations, figure/table ids, "
                            "negation and modality. Fill target for every segment. "
                            "Do not merge or reorder segments.",
            "segments": [{
                "segment_id": s.get("segment_id"),
                "source": s.get("source") or s.get("text") or "",
                "target": "",
            } for s in chunk],
        }
        write_json(batch_dir / ("batch-%03d.json" % (start // max(1, args.size) + 1)), batch)
        count += 1
    summary = {"ok": True, "segments": len(segments), "batches": count,
               "batch_size": args.size, "dir": str(batch_dir)}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
