#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entry point for the genre-first research translation skill.

Usage:
    python3 main.py prepare  --input /input --work /app/work --output /app/output
    # ... the model writes the complete draft to /app/output/<genre>.md ...
    python3 main.py finalize --work /app/work --output /app/output
    python3 main.py run      --input /input --work /app/work --output /app/output \
        --draft /app/output/article.md

``prepare`` inventories the real files, normalises the work order and builds the
locatable evidence ledger.  It never writes an extractive substitute article:
the complete manuscript is written by the model in the requested genre.
``finalize`` re-checks citations and numbers, exports DOCX, and writes
result.json with the official four fields.  A missing draft or incomplete input
still produces an honest partial result.json and exits 0; only an unusable
workspace exits 2.  Stdlib only, no network.
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
REQUIRED = ("transfer_kit", "inventory", "workorder", "evidence",
            "citation_map", "numbers_check", "docx_out", "result_writing")


def _bootstrap_local_wrapped_scripts():
    """Allow running directly from tracks/ where files are stored as *.py.md."""
    if all((HERE / (name + ".py")).is_file() for name in REQUIRED):
        return None
    mirror = Path(tempfile.mkdtemp(prefix="transfer-kit-scripts-"))
    copied = 0
    for src in HERE.glob("*.py.md"):
        shutil.copy2(src, mirror / src.name[:-3])
        copied += 1
    if copied:
        sys.path.insert(0, str(mirror))
        return mirror
    return None


_BOOTSTRAP_DIR = _bootstrap_local_wrapped_scripts()
try:  # package mode (python -m scripts.main)
    from . import transfer_kit as kit
    from . import inventory
    from . import workorder as workorder_mod
    from . import evidence as evidence_mod
    from . import citation_map as citation_mod
    from . import numbers_check
    from . import docx_out
    from . import result_writing
except ImportError:  # direct mode (python scripts/main.py)
    kit = __import__("transfer_kit")
    inventory = __import__("inventory")
    workorder_mod = __import__("workorder")
    evidence_mod = __import__("evidence")
    citation_mod = __import__("citation_map")
    numbers_check = __import__("numbers_check")
    docx_out = __import__("docx_out")
    result_writing = __import__("result_writing")


def _source_text(work_dir, inventory_data):
    parts = []
    for f in inventory_data.get("files", []):
        if f.get("role") == "task_metadata":
            continue
        if f.get("readable") and f.get("text_path"):
            try:
                parts.append(kit.read_text(Path(work_dir) / f["text_path"]))
            except OSError:
                continue
    return "\n".join(parts)


def _load(path, default=None):
    return kit.read_json(path, default=default)


def _draft_target(output_dir, workorder, explicit=None):
    if explicit:
        return Path(explicit)
    name = (workorder or {}).get("deliverable_name") or "article.md"
    return Path(output_dir) / name


def _check_draft(work_dir, output_dir, workorder, evidence, inventory, draft_path):
    draft_text = kit.read_text(draft_path) if Path(draft_path).is_file() else ""
    source_text = _source_text(work_dir, inventory)
    cm = citation_mod.build(workorder, evidence, draft_text)
    citation_mod.write(work_dir, cm)
    nc = numbers_check.check(draft_text, evidence, workorder, source_text)
    numbers_check.write(work_dir, nc)
    return draft_text, source_text, cm, nc


def prepare(args):
    input_dir = Path(args.input)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = kit.now_iso()

    inv = inventory.scan(input_dir, work_dir)
    wo = workorder_mod.build(input_dir, work_dir)
    ev = evidence_mod.extract(inv, work_dir, wo)

    draft_path = _draft_target(output_dir, wo, getattr(args, "draft", None))
    draft_text, _, cm, nc = _check_draft(work_dir, output_dir, wo, ev, inv, draft_path)

    summary = {
        "stage": "prepare",
        "case_id": wo["case_id"],
        "genre": wo["genre"],
        "genre_confidence": wo["genre_confidence"],
        "audience": wo["audience"],
        "deliverable_name": wo["deliverable_name"],
        "draft_path": str(draft_path),
        "draft_exists": draft_path.is_file() and bool(draft_text.strip()),
        "extractive_fallback": False,
        "inventory": {"files": inv.get("file_count"),
                      "readable": inv.get("readable_texts"),
                      "status": inv.get("status")},
        "evidence": ev.get("stats"),
        "citation_map": cm.get("stats"),
        "numbers_ok": nc.get("ok"),
        "numbers_errors": len(nc.get("errors") or []),
        "numbers_warnings": len(nc.get("warnings") or []),
        "workorder": str(work_dir / "workorder.json"),
        "evidence_ledger": str(work_dir / "evidence.json"),
        "started_at": started,
        "finished_at": kit.now_iso(),
        "next": "The model must now write the complete %s draft to %s."
                % (wo["genre"], draft_path),
    }
    kit.write_json(work_dir / "prepare-summary.json", summary)
    return summary


def finalize(args):
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = kit.now_iso()

    wo = _load(work_dir / "workorder.json")
    ev = _load(work_dir / "evidence.json")
    inv = _load(work_dir / "inventory.json", {}) or {}
    prepare_note = None
    if wo is None or ev is None:
        prepare_note = prepare(args)
        wo = _load(work_dir / "workorder.json") or {}
        ev = _load(work_dir / "evidence.json") or {}
        inv = _load(work_dir / "inventory.json", {}) or {}

    explicit = getattr(args, "draft", None)
    draft_path = _draft_target(output_dir, wo, explicit)
    target = Path(output_dir) / (wo.get("deliverable_name") or "article.md")
    if explicit and draft_path.is_file() and draft_path.resolve() != target.resolve():
        try:
            shutil.copy2(draft_path, target)
            draft_path = target
        except OSError:
            pass
    elif not draft_path.is_file() and target.is_file():
        draft_path = target

    draft_text, _, cm, nc = _check_draft(work_dir, output_dir, wo, ev, inv, draft_path)

    docx_meta = None
    if draft_path.is_file() and draft_text.strip() and not args.no_docx:
        docx_path = draft_path.with_suffix(".docx")
        docx_title = result_writing._draft_title(
            draft_text, wo.get("source_title") or "研究成果转化稿")
        docx_meta = docx_out.convert(draft_text, docx_path,
                                     title=docx_title,
                                     case_id=wo.get("case_id", "unknown"),
                                     preview=not getattr(args, "no_preview", False))
        kit.write_json(work_dir / "docx-meta.json", docx_meta)

    writing_mode = "agent_draft" if draft_text.strip() else "missing_draft"
    run_info = {
        "pipeline": ["inventory", "workorder", "evidence", "citation_map",
                     "numbers_check", "docx_out", "result_writing"],
        "draft_source": str(draft_path),
        "writing_mode": writing_mode,
        "prepare": prepare_note,
        "started_at": started,
        "finished_at": kit.now_iso(),
        "budget_note": "stdlib only; no network; checker failures degrade to uncertainty",
    }
    result, result_path = result_writing.build(
        work_dir, output_dir, draft_path, docx_meta=docx_meta,
        run_info=run_info, writing_mode=writing_mode)
    problems = result_writing.validate_paths(result, output_dir)

    summary = {
        "stage": "finalize",
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "genre": result.get("genre"),
        "deliverable": (result.get("deliverable") or {}).get("path"),
        "docx": (docx_meta or {}).get("path"),
        "docx_valid": (docx_meta or {}).get("valid"),
        "docx_preview": ((docx_meta or {}).get("preview") or {}).get("ok"),
        "numbers_ok": nc.get("ok"),
        "number_errors": nc.get("errors") or [],
        "number_warnings": len(nc.get("warnings") or []),
        "citation_map": cm.get("stats"),
        "preserved_facts": len(result.get("preserved_facts") or []),
        "evidence_map": len(result.get("evidence_map") or []),
        "change_log": len(result.get("change_log") or []),
        "problems": problems,
        "result": str(result_path),
        "started_at": started,
        "finished_at": kit.now_iso(),
    }
    kit.write_json(work_dir / "run-report.json", summary)
    return summary, problems


def _emergency_result(args, exc):
    """Write an honest partial result.json even when the pipeline crashed."""
    output_dir = Path(getattr(args, "output", "/app/output"))
    work_dir = Path(getattr(args, "work", "/app/work"))
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        case_id = "unknown-case"
        case_path = Path(getattr(args, "input", "/input")) / "case.json"
        try:
            case = json.loads(case_path.read_text(encoding="utf-8"))
            if isinstance(case, dict) and case.get("case_id"):
                case_id = str(case["case_id"])
        except (OSError, ValueError, TypeError):
            pass
        payload = {
            "case_id": case_id,
            "status": "partial",
            "deliverable": {"path": None, "format": "markdown"},
            "preserved_facts": [],
            "evidence_map": [],
            "change_log": [],
            "uncertainty": [{
                "item": "pipeline_error",
                "detail": "%s: %s" % (type(exc).__name__, exc),
                "action": "rerun prepare/finalize or complete the draft manually",
            }],
            "limitations": ["入口异常，未生成完整台账；没有伪造正文或数据。"],
            "run": {"traceback": traceback.format_exc()[-2000:]},
            "generated_at": kit.now_iso() if "kit" in globals() else "",
        }
        (output_dir / "result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        (work_dir / "run-report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        return 0
    except OSError:
        print("fatal: cannot write workspace: %s" % exc, file=sys.stderr)
        return 2


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("prepare", "finalize", "run"):
        p = sub.add_parser(name)
        p.add_argument("--input", default="/input")
        p.add_argument("--work", default="/app/work")
        p.add_argument("--output", default="/app/output")
        p.add_argument("--draft", default=None)
        p.add_argument("--json", action="store_true")
        if name in ("finalize", "run"):
            p.add_argument("--no-docx", action="store_true")
            p.add_argument("--preview", action="store_true",
                           help="kept for compatibility; preview is attempted by default")
            p.add_argument("--no-preview", action="store_true",
                           help="skip the LibreOffice DOCX->PDF preview attempt")
    return ap


def main():
    try:
        args = build_parser().parse_args()
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        if args.command == "prepare":
            summary = prepare(args)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.command == "finalize":
            summary, _ = finalize(args)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        prepared = prepare(args)
        summary, _ = finalize(args)
        summary["prepare"] = prepared
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # partial results still deliver a result.json
        code = _emergency_result(args, exc)
        print(json.dumps({"status": "partial", "error": str(exc),
                          "exit": code}, ensure_ascii=False, indent=2))
        return code


if __name__ == "__main__":
    sys.exit(main())
