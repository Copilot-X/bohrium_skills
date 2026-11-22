#!/usr/bin/env python3
"""Validate a result.json produced by the Research Deliverable Engine.

Usage:
    python3 validate_result.py /app/output/result.json \
        [--contract /app/work/contract_map.json] \
        [--input /input] [--check-quotes] [--allow-missing-artifacts]

Exit codes: 0 ok, 1 validation problems, 2 cannot read result.
Stdlib only.
"""
import argparse
import json
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"(TODO|TBD|FIXME|<placeholder>|待补充|待填写|xxx)", re.IGNORECASE)
ALLOWED_STATUS = {"completed", "partial", "insufficient_evidence", "input_error", "failed",
                  "answered", "ok", "success", "done"}
REQUIRED_TOP = ["case_id", "status"]


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot parse {path}: {exc}")
        return None


def walk_strings(value, trail="$"):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from walk_strings(v, f"{trail}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from walk_strings(v, f"{trail}[{i}]")
    elif isinstance(value, str):
        yield trail, value


def collect_evidence(value, trail="$"):
    """Yield (trail, evidence_dict) for dicts that look like evidence objects."""
    if isinstance(value, dict):
        if ("quote" in value) and any(k in value for k in
                                      ("paragraph_id", "page", "section_id", "table_id", "locator")):
            yield trail, value
        for k, v in value.items():
            yield from collect_evidence(v, f"{trail}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from collect_evidence(v, f"{trail}[{i}]")


def load_input_text(input_root):
    chunks = []
    root = Path(input_root)
    if not root.exists():
        return ""
    for p in root.rglob("*"):
        if not p.is_file() or p.stat().st_size > 5_000_000:
            continue
        if p.suffix.lower() in {".json", ".md", ".txt", ".csv", ".tsv", ".yaml", ".yml", ".tex", ".html"}:
            try:
                chunks.append(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                pass
    return "\n".join(chunks)


def normalize(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("result")
    ap.add_argument("--contract", default=None)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--check-quotes", action="store_true")
    ap.add_argument("--allow-missing-artifacts", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    result = load(args.result)
    if result is None:
        return 2

    problems, warnings = [], []

    for key in REQUIRED_TOP:
        if key not in result:
            problems.append(f"missing top-level key: {key}")

    status = result.get("status")
    if isinstance(status, str) and status not in ALLOWED_STATUS:
        warnings.append(f"unusual status: {status}")

    for trail, value in walk_strings(result):
        if PLACEHOLDER.search(value):
            problems.append(f"placeholder text at {trail}: {value[:60]!r}")

    # contract fields
    if args.contract:
        contract = load(args.contract)
        if contract:
            for field in contract.get("required_fields", []):
                if field not in result:
                    problems.append(f"contract field missing: {field}")

    # evidence quotes
    evidence_items = list(collect_evidence(result))
    if not evidence_items and result.get("task_family") == "reading":
        warnings.append("no evidence objects found in a reading result")

    if args.check_quotes and evidence_items:
        corpus = normalize(load_input_text(args.input))
        if not corpus:
            warnings.append("no readable text under input; skipped quote check")
        else:
            for trail, ev in evidence_items:
                quote = normalize(str(ev.get("quote", "")))
                if len(quote) < 6:
                    problems.append(f"quote too short at {trail}")
                elif quote not in corpus:
                    warnings.append(f"quote not found verbatim at {trail}: {quote[:70]!r}")

    # artifacts
    artifacts = result.get("artifacts") or []
    if isinstance(artifacts, list):
        for i, art in enumerate(artifacts):
            if not isinstance(art, dict):
                continue
            path = art.get("path")
            if not path:
                problems.append(f"artifact[{i}] has no path")
                continue
            candidates = [Path(path), Path("/app/output") / path]
            if not any(p.exists() for p in candidates):
                msg = f"artifact missing on disk: {path}"
                (warnings if args.allow_missing_artifacts else problems).append(msg)
            elif all(p.stat().st_size == 0 for p in candidates if p.exists()):
                problems.append(f"artifact is empty: {path}")

    report = {"ok": not problems, "problems": problems, "warnings": warnings,
              "evidence_count": len(evidence_items),
              "top_level_keys": sorted(result.keys())}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
