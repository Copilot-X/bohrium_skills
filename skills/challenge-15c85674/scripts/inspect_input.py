#!/usr/bin/env python3
"""Inventory a case bundle and hint at its output contract.

Usage:
    python3 inspect_input.py /input [--out /app/work/contract_hints.json]

Stdlib only. Never modifies the input tree.
"""
import argparse
import json
import os
import sys
from pathlib import Path

TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv", ".tex", ".html", ".xml"}
CONTRACT_HINTS = ("contract", "schema", "interface", "example", "output", "task")


def walk(root):
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in sorted(filenames):
            p = Path(dirpath) / name
            try:
                size = p.stat().st_size
            except OSError:
                size = -1
            entries.append({"path": str(p), "rel": str(p.relative_to(root)),
                            "size": size, "ext": p.suffix.lower()})
    return entries


def json_shape(value, depth=0):
    if depth > 3:
        return "..."
    if isinstance(value, dict):
        return {k: json_shape(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [json_shape(value[0], depth + 1)] if value else []
    if isinstance(value, str):
        return f"str({len(value)})"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "num"
    if value is None:
        return "null"
    return type(value).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="/input")
    ap.add_argument("--out", default="/app/work/contract_hints.json")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(json.dumps({"error": f"input root missing: {root}"}, ensure_ascii=False))
        return 2

    entries = walk(root)
    hints = []
    for e in entries:
        low = e["rel"].lower()
        if e["ext"] in TEXT_EXT and any(h in low for h in CONTRACT_HINTS):
            hints.append(e["path"])

    shapes = {}
    for e in entries:
        if e["ext"] == ".json" and e["size"] <= 2_000_000:
            try:
                data = json.loads(Path(e["path"]).read_text(encoding="utf-8", errors="replace"))
                shapes[e["rel"]] = json_shape(data)
            except Exception as exc:  # noqa: BLE001
                shapes[e["rel"]] = f"<unparsed: {type(exc).__name__}>"

    report = {"root": str(root), "file_count": len(entries),
              "total_bytes": sum(max(0, e["size"]) for e in entries),
              "files": entries, "contract_candidates": hints, "json_shapes": shapes}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"file_count": report["file_count"],
                      "contract_candidates": hints[:30],
                      "out": str(out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
