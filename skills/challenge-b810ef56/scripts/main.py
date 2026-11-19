#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entry point for evidence-driven writing (challenge-b810ef56).

Flow:

1. ``prepare``  : inventory ``/input`` -> ``work/sources.json`` + text sidecars;
2. the model writes the complete draft to ``<output>/text.md``;
3. ``finalize`` : bind claims, audit citations/quotes, write
   ``claim-evidence.json`` and ``result.json`` with the official fields
   ``deliverable`` / ``preserved_facts`` / ``evidence_map`` / ``change_log``.

``run`` is ``prepare`` + ``finalize``.  A missing draft is reported as
``status=partial``; unreadable input as ``status=input_error``.  Both write a
real ``result.json`` and exit 0.  Exit 2 happens only when the process cannot
start at all (bad CLI or unusable installation).

No extractive fallback draft is produced: the prose must be authored by the
model, then checked and projected by this pipeline.

Usage:
    python3 main.py prepare  --input /input --work /app/work --output /app/output
    #   ... write /app/output/text.md ...
    python3 main.py finalize --input /input --work /app/work \
        --output /app/output --draft /app/output/text.md
    python3 main.py run      --input /input --work /app/work --output /app/output
    python3 main.py          --input /input --work /app/work --output /app/output

Stdlib only, no network.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIRED = ("evidence_kit", "inventory", "extract_sources", "claims",
            "citecheck", "verify_quotes", "build_result")


def _bootstrap_local_wrapped_scripts():
    """Allow direct execution where scripts are stored as ``*.py.md``."""
    if all((HERE / (name + ".py")).is_file() for name in REQUIRED):
        return None
    mirror = Path(tempfile.mkdtemp(prefix="evidence-kit-scripts-"))
    copied = 0
    for source in HERE.glob("*.py.md"):
        shutil.copy2(source, mirror / source.name[:-3])
        copied += 1
    if copied:
        sys.path.insert(0, str(mirror))
        return mirror
    return None


_BOOTSTRAP_DIR = _bootstrap_local_wrapped_scripts()
try:  # package mode (python -m scripts.main)
    from . import build_result
    from . import citecheck
    from . import claims as claims_mod
    from . import evidence_kit
    from . import extract_sources
    from . import inventory
    from . import verify_quotes
except ImportError:  # direct mode (python scripts/main.py)
    build_result = __import__("build_result")
    citecheck = __import__("citecheck")
    claims_mod = __import__("claims")
    evidence_kit = __import__("evidence_kit")
    extract_sources = __import__("extract_sources")
    inventory = __import__("inventory")
    verify_quotes = __import__("verify_quotes")

INPUT_ERROR_STATES = {"input_missing", "input_empty", "no_readable_material"}
ZERO_COVERAGE = {"sentences": 0, "fully_supported": 0,
                 "partially_supported": 0, "unsupported": 0}


def _case(input_dir):
    return evidence_kit.read_json(Path(input_dir) / "case.json", {}) or {}


def _load(path):
    return evidence_kit.read_json(path, {}) or {}


def _contract(case_data):
    value = (case_data or {}).get("output_contract")
    return value if isinstance(value, dict) else {}


def _contract_coverage(case_data):
    known = {"deliverable", "preserved_facts", "evidence_map", "change_log"}
    rows = []
    for field in _contract(case_data):
        rows.append({
            "contract_field": field,
            "result_field": field,
            "satisfied": field in known,
            "note": ("projected from the real ledger" if field in known
                     else "copied verbatim; no automatic projection"),
        })
    return rows


def _base_result(case_data, status, uncertainty=None, change_log=None):
    """A contract-shaped result that never claims work that did not happen."""
    return {
        "case_id": (case_data or {}).get("case_id") or "unknown",
        "task_family": "evidence-driven-writing",
        "status": status,
        "text": None,
        "deliverable": None,
        "artifacts": [],
        "sentences": [],
        "unsupported": [],
        "coverage": dict(ZERO_COVERAGE),
        "preserved_facts": [],
        "evidence_map": [],
        "change_log": list(change_log or []),
        "uncertainty": list(uncertainty or []),
        "output_contract": _contract(case_data),
        "contract_coverage": _contract_coverage(case_data),
        "generated_at": evidence_kit.now_iso(),
    }


def _write_result(output_dir, result):
    path = Path(output_dir) / "result.json"
    evidence_kit.write_json(path, result)
    return path


def _input_error(output_dir, case_data, inventory_data):
    uncertainty = list(inventory_data.get("uncertainty") or []) or [{
        "item": "no_readable_material",
        "reason": "input inventory contains no readable material",
        "impact": "the writing task cannot be completed from sources",
        "next_check": "provide readable task.md/materials",
    }]
    result = _base_result(
        case_data, "input_error", uncertainty,
        change_log=[{
            "step": 1,
            "action": "input_error_stop",
            "reason": "no readable material; no draft, number or citation was fabricated",
            "evidence": "work/inventory.json",
        }])
    return _write_result(output_dir, result)


def _awaiting_draft(output_dir, work_dir, case_data, inventory_data,
                    sources_data, stage):
    result = _base_result(
        case_data, "partial",
        uncertainty=[{
            "item": "draft_missing",
            "reason": "readable materials were inventoried, but %s/text.md does "
                      "not exist yet" % output_dir,
            "impact": "no prose deliverable or claim-evidence projection exists",
            "next_check": "write the complete text.md, then run finalize or run",
        }],
        change_log=[{
            "step": 1,
            "action": "sources_inventoried",
            "reason": "real input files were read and locatable units extracted",
            "evidence": "sources=%d; units=%d; inventory_files=%d"
                        % (len(sources_data.get("sources") or []),
                           sources_data.get("units_total") or 0,
                           inventory_data.get("file_count") or 0),
        }])
    result.update({
        "stage": stage,
        "sources": sources_data.get("sources") or [],
        "checks": {"deliverable_exists": False,
                   "official_fields_present": True},
    })
    path = _write_result(output_dir, result)
    return {
        "stage": stage,
        "status": "partial",
        "reason": "draft_missing",
        "deliverable": None,
        "sources": len(sources_data.get("sources") or []),
        "result": str(path),
        "finished_at": evidence_kit.now_iso(),
    }


def _prepare_core(input_dir, work_dir, output_dir, draft=None):
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    output_dir = Path(output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    case_data = _case(input_dir)

    inventory_data = inventory.scan(input_dir, work_dir)
    if inventory_data.get("status") in INPUT_ERROR_STATES:
        return "input_error", case_data, inventory_data, None

    sources_data = extract_sources.build(inventory_data, work_dir, case_data)
    if not sources_data.get("sources"):
        return "input_error", case_data, inventory_data, sources_data

    if draft:
        source = Path(draft)
        target = output_dir / "text.md"
        if source.is_file() and source.resolve() != target.resolve():
            shutil.copy2(source, target)
    return "ok", case_data, inventory_data, sources_data


def _finalize_impl(input_dir, work_dir, output_dir, case_data, inventory_data,
                   sources_data):
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    output_dir = Path(output_dir)
    text_path = output_dir / "text.md"
    if not text_path.is_file():
        return _awaiting_draft(output_dir, work_dir, case_data, inventory_data,
                               sources_data, "finalize")

    text = evidence_kit.read_text(text_path)
    cited = evidence_kit.citation_numbers(evidence_kit.body_text(text))
    text_with_refs, additions = evidence_kit.ensure_reference_block(
        text, sources_data.get("sources") or [], cited)
    if additions:
        evidence_kit.write_text(text_path, text_with_refs)
        text = text_with_refs

    ledger, final_text, actions = claims_mod.build(
        text, sources_data, case_data, prune_unsupported=True)
    if final_text != text:
        evidence_kit.write_text(text_path, final_text)
    evidence_kit.write_json(work_dir / "claims.json", ledger)

    citation = citecheck.build(final_text, sources_data, ledger)
    citecheck.write(work_dir, output_dir, citation)
    quotes = verify_quotes.build(ledger, sources_data, work_dir, input_dir)
    verify_quotes.write(work_dir, output_dir, quotes)

    extra_uncertainty = []
    if actions.get("deleted"):
        extra_uncertainty.append({
            "item": "unsupported_numeric_sentences_deleted",
            "reason": "sentences with numbers but no verbatim source support "
                      "were removed from text.md",
            "impact": "the draft may be shorter than the requested length",
            "next_check": "add sourced evidence or rewrite those points",
        })
    if actions.get("marked_as_reasoning"):
        extra_uncertainty.append({
            "item": "reasoning_sentences_marked",
            "reason": "non-numeric unsupported sentences were retained only as "
                      "marked author reasoning",
            "impact": "they do not count as evidence-backed results",
            "next_check": "replace with sourced statements where possible",
        })
    result, result_path, _ = build_result.build(
        work_dir, output_dir, text_path, case_data, stage="finalize",
        writing_mode="agent_draft", input_dir=str(input_dir),
        extra={"reference_additions": additions,
               "uncertainty": extra_uncertainty})
    summary = {
        "stage": "finalize",
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "writing_mode": "agent_draft",
        "deliverable": (result.get("deliverable") or {}).get("path"),
        "word_count": (result.get("text") or {}).get("word_count"),
        "claims": ledger.get("stats"),
        "coverage": ledger.get("coverage"),
        "citation_ok": citation.get("ok"),
        "citation_errors": len(citation.get("errors") or []),
        "quotes_ok": quotes.get("ok"),
        "quote_errors": len(quotes.get("errors") or []),
        "reference_additions": additions,
        "actions": {key: len(value) for key, value in actions.items()},
        "preserved_facts": len(result.get("preserved_facts") or []),
        "evidence_map": len(result.get("evidence_map") or []),
        "change_log": len(result.get("change_log") or []),
        "artifacts": len(result.get("artifacts") or []),
        "result": str(result_path),
        "finished_at": evidence_kit.now_iso(),
    }
    evidence_kit.write_json(work_dir / "run-report.json", summary)
    return summary


def prepare(args):
    state, case_data, inventory_data, sources_data = _prepare_core(
        args.input, args.work, args.output, args.draft)
    output_dir = Path(args.output)
    if state == "input_error":
        result = _input_error(output_dir, case_data, inventory_data)
        summary = {"stage": "prepare", "status": "input_error",
                   "inventory": inventory_data.get("status"),
                   "result": str(result)}
        evidence_kit.write_json(Path(args.work) / "prepare-summary.json",
                                summary)
        return summary
    text_path = output_dir / "text.md"
    if text_path.is_file():
        summary = _finalize_impl(args.input, args.work, args.output, case_data,
                                 inventory_data, sources_data)
        summary["stage"] = "prepare+finalize"
    else:
        summary = _awaiting_draft(output_dir, args.work, case_data,
                                  inventory_data, sources_data, "prepare")
    summary["inventory"] = {
        "files": inventory_data.get("file_count"),
        "readable": inventory_data.get("readable_texts"),
        "materials": inventory_data.get("material_count"),
    }
    summary["sources"] = {
        "count": sources_data.get("source_count"),
        "units": sources_data.get("units_total"),
    }
    evidence_kit.write_json(Path(args.work) / "prepare-summary.json", summary)
    return summary


def finalize(args):
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    case_data = _case(args.input)
    inventory_data = _load(work_dir / "inventory.json")
    sources_data = _load(work_dir / "sources.json")

    if not sources_data.get("sources"):
        state, case_data, inventory_data, sources_data = _prepare_core(
            args.input, args.work, args.output, args.draft)
        if state == "input_error":
            result = _input_error(output_dir, case_data, inventory_data)
            return {"stage": "finalize", "status": "input_error",
                    "result": str(result)}
    elif args.draft:
        source = Path(args.draft)
        target = output_dir / "text.md"
        if source.is_file() and source.resolve() != target.resolve():
            shutil.copy2(source, target)

    return _finalize_impl(args.input, args.work, args.output, case_data,
                          inventory_data, sources_data)


def run(args):
    state, case_data, inventory_data, sources_data = _prepare_core(
        args.input, args.work, args.output, args.draft)
    output_dir = Path(args.output)
    if state == "input_error":
        result = _input_error(output_dir, case_data, inventory_data)
        return {"stage": "run", "status": "input_error",
                "result": str(result)}
    return _finalize_impl(args.input, args.work, args.output, case_data,
                          inventory_data, sources_data)


def _emergency_result(input_dir, output_dir, exc):
    """Last-resort partial result; only paths that really exist are listed."""
    case_data = _case(input_dir)
    output_dir = Path(output_dir)
    artifacts = []
    for relative, role, fmt in (
            ("text.md", "editable", "markdown"),
            ("claim-evidence.json", "evidence", "json"),
            ("citation-audit.json", "check", "json"),
            ("quote-audit.json", "check", "json")):
        if (output_dir / relative).is_file():
            artifacts.append({"role": role, "path": relative, "format": fmt,
                              "exists": True})
    result = _base_result(
        case_data, "partial",
        uncertainty=[{
            "item": "internal_error",
            "reason": "%s: %s" % (type(exc).__name__, exc),
            "impact": "the pipeline stopped; files already on disk are kept",
            "next_check": "inspect the trace tail and rerun finalize",
        }],
        change_log=[{
            "step": 1,
            "action": "pipeline_stopped",
            "reason": "an unexpected error interrupted the projection",
            "evidence": "result.json",
        }])
    result.update({
        "artifacts": artifacts,
        "error": "%s: %s" % (type(exc).__name__, exc),
        "traceback_tail": traceback.format_exc()[-1500:],
    })
    if (output_dir / "text.md").is_file():
        result["text"] = {"path": "text.md", "format": "markdown"}
        result["deliverable"] = {"path": "text.md", "format": "markdown"}
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="run",
                        choices=("prepare", "finalize", "run"))
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--draft", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse --help / invalid CLI
        return int(exc.code or 0)
    try:
        if args.command == "prepare":
            summary = prepare(args)
        elif args.command == "finalize":
            summary = finalize(args)
        else:
            summary = run(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # never fail the run if a result can be written
        try:
            output_dir = Path(args.output)
            output_dir.mkdir(parents=True, exist_ok=True)
            _write_result(output_dir, _emergency_result(args.input,
                                                        output_dir, exc))
            print(json.dumps({"status": "partial", "error": str(exc),
                              "result": str(output_dir / "result.json")},
                             ensure_ascii=False, indent=2))
            return 0
        except Exception:
            return 2


if __name__ == "__main__":
    sys.exit(main())
