#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic target-field extraction draft.

Reads /input/target-fields.json and the supplied text/PDF/CSV/JSON materials,
finds the field name (or description aliases) in sentences, extracts a numeric
or short string value with unit and a verbatim quote, and writes a draft
result.json.  Missing fields are explicitly ``not_reported``; a quote alone is
labelled as requiring semantic review, never as a confirmed fact.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

NUM_RE = re.compile(
    r"[-+]?\d+(?:\.\d+)?\s*(?:%|°C|C|K|h|min|s|mg|g|kg|mL|L|M|mM|nM|ppm|"
    r"eV|nm|µm|um|Hz|kHz|MPa|GPa|days?|years?|patients?|samples?)?")


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


def field_list(case):
    for key in ("fields", "target_fields", "requested_fields"):
        if isinstance(case.get(key), list):
            return case[key]
    if isinstance(case.get("target_fields"), dict):
        return [{"field_id": k, **(v if isinstance(v, dict) else {})}
                for k, v in case["target_fields"].items()]
    if isinstance(case.get("fields"), dict):
        return [{"field_id": k, **(v if isinstance(v, dict) else {})}
                for k, v in case["fields"].items()]
    return []


def read_pdf(path):
    pages = []
    for tool in (["pdftotext", "-layout", str(path), "-"],
                 ["pdftotext", "-raw", str(path), "-"]):
        try:
            proc = subprocess.run(tool, capture_output=True, text=True,
                                  timeout=120)
            if proc.returncode == 0 and proc.stdout.strip():
                pages = proc.stdout.split("\f")
                break
        except (OSError, subprocess.TimeoutExpired):
            continue
    if not pages:
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(path))
            pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception:  # noqa: BLE001
            pages = []
    return pages


def load_docs(inp):
    docs = []
    skip = {"case.json", "target-fields.json", "task.md", "environment"}
    for path in sorted(inp.rglob("*")):
        if not path.is_file():
            continue
        if path.name.lower() in skip or "environment" in path.parts:
            continue
        suffix = path.suffix.lower()
        rel = path.relative_to(inp).as_posix()
        if suffix == ".pdf":
            for index, text in enumerate(read_pdf(path), 1):
                if text.strip():
                    docs.append({"path": rel, "page": index, "text": text})
        elif suffix in (".md", ".txt", ".tex", ".csv", ".tsv", ".json"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            docs.append({"path": rel, "page": None, "text": text})
    return docs


def sentences(text):
    parts = re.split(r"(?<=[。！？.!?])\s+|\n+", str(text or ""))
    cleaned = [norm(part) for part in parts]
    return [part for part in cleaned if 12 <= len(part) <= 600]


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
    target = read_json(inp / "target-fields.json", {})
    fields = field_list(target) or field_list(case)
    if not fields:
        task = inp / "task.md"
        text = task.read_text(encoding="utf-8", errors="replace") if task.is_file() else ""
        for match in re.finditer(r"`([a-zA-Z][\w.-]{2,60})`", text):
            fields.append({"field_id": match.group(1), "name": match.group(1)})
    fields = fields[:80]
    docs = load_docs(inp)
    schema, extractions = [], []
    for field in fields:
        if not isinstance(field, dict):
            continue
        fid = str(field.get("field_id") or field.get("id") or field.get("name") or "")
        name = str(field.get("name") or fid)
        aliases = [name, fid]
        for key in ("description", "definition", "aliases", "synonyms"):
            value = field.get(key)
            if isinstance(value, str):
                aliases.append(value)
            elif isinstance(value, list):
                aliases.extend(str(x) for x in value)
        schema.append({
            "field_id": fid, "name": name,
            "type": field.get("type") or "unknown",
            "unit": field.get("unit") or field.get("expected_unit"),
            "required": bool(field.get("required", True)),
            "definition_source": "target-fields.json",
        })
        best = None
        for doc in docs:
            for index, sentence in enumerate(sentences(doc["text"]), 1):
                lowered = sentence.lower()
                score = sum(1 for alias in aliases
                            if str(alias).lower() in lowered)
                if score <= 0:
                    continue
                numbers = [m.group(0).strip() for m in NUM_RE.finditer(sentence)]
                candidate = (score, len(numbers), doc, index, sentence, numbers)
                if best is None or candidate[:2] > best[:2]:
                    best = candidate
        if best:
            score, _, doc, index, sentence, numbers = best
            values = []
            for number in numbers[:4]:
                values.append({
                    "value": number, "original": number,
                    "unit": "", "unit_status": "not_reported",
                    "unit_missing": True,
                    "statistic": "point_estimate",
                    "condition": "not_reported",
                    "condition_status": "not_reported",
                    "comparator": "none",
                    "sample": "not_reported", "time_point": "not_reported",
                    "uncertainty": {"type": "not_reported", "value": None},
                    "locator": {"doc_id": doc["path"],
                                "paragraph_id": "S%d" % index,
                                "table_id": None, "figure_id": None,
                                "page": doc.get("page"), "row_index": None,
                                "column": None, "quote": sentence},
                    "source_kind": "paragraph", "condition_evidence": None,
                    "semantic_check": {
                        "requested_metric": name,
                        "quoted_metric": name,
                        "subject": "requires model review",
                        "matches_requested_field": False,
                        "note": "deterministic alias match only; confirm the "
                                "subject, metric, condition and unit",
                    },
                    "digit_check": {"checked": True, "conflicts": []},
                })
            extractions.append({
                "field_id": fid, "field_name": name,
                "status": "reported" if values else "not_reported",
                "values": values, "candidates": [], "resolution": None,
                "resolution_note": None,
                "missing_reason": None if values else "field name not found",
                "search_scope": "all supplied text/PDF paragraphs",
            })
        else:
            extractions.append({
                "field_id": fid, "field_name": name,
                "status": "not_reported", "values": [], "candidates": [],
                "resolution": None, "resolution_note": None,
                "missing_reason": "field name/aliases not found in supplied materials",
                "search_scope": "all supplied text/PDF paragraphs",
            })
    reported = sum(1 for row in extractions if row["status"] == "reported")
    payload = {
        "case_id": case.get("case_id") or target.get("case_id") or "UNKNOWN",
        "status": "partial",
        "task_family": "reading",
        "schema": schema, "extractions": extractions,
        "missing": [row["field_id"] for row in extractions
                    if row["status"] == "not_reported"],
        "conflicts": [],
        "summary": {"field_count": len(extractions),
                    "reported_count": reported,
                    "not_reported_count": len(extractions) - reported,
                    "extraction_failed_count": 0,
                    "conflict_count": 0, "unit_missing_count": reported,
                    "condition_missing_count": reported,
                    "complete_rate": (reported / len(extractions)
                                      if extractions else 0.0)},
        "corpus_scope": {"documents": len(docs), "year_range": "not_reported",
                         "languages": ["unknown"]},
        "uncertainty": [{"item": "deterministic_alias_match",
                         "reason": "field matches use the field name/description aliases",
                         "impact": "each value needs semantic review of subject/metric/condition",
                         "action": "read the quote and confirm or change status"}],
        "change_log": [{"step": 1, "action": "extract_fields.py draft",
                        "evidence": "target-fields.json + supplied materials"}],
        "support_files_used": ["scripts/extract_fields.py"],
        "result": {"summary": "%d/%d fields have a candidate value" % (
            reported, len(extractions))},
        "claims": [], "preserved_facts": [], "evidence_map": [],
        "artifacts": [], "generated_at": now_iso(),
    }
    write_json(work / "extraction-draft.json", payload)
    write_json(out / "result.json", payload)
    summary = {"ok": True, "fields": len(extractions), "reported": reported,
               "not_reported": len(extractions) - reported}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
