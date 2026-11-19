#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entry point for the academic-manuscript writing skill.

The model writes the manuscript.  This entry point only reads the real input,
builds the contract + locatable evidence ledger, installs the model draft, runs
the checks and emits ``result.json`` with the official fields
``deliverable / preserved_facts / evidence_map / change_log``.

Commands
--------
prepare  : inventory -> contract -> sources -> evidence ledger -> outline
           -> work/writing-brief.md; installs a clearly-labelled PARTIAL
           scaffold only when manuscript.md does not exist yet.
assemble : read the model-written manuscript.md (or --draft PATH), bind
           sentences/facts/citations, run task_specific checks, rewrite
           result.json and the evidence artifacts.
run      : prepare + assemble in one call (fallback / smoke test).  A scaffold
           is always reported as ``partial``, never ``completed``.

Exit codes
----------
0 : always, including ``partial`` and ``input_error`` (result.json is written).
2 : only when the program cannot start at all (missing engine files or an
    unwritable output directory).

Usage
-----
    python3 main.py prepare  --input /input --work /app/work --output /app/output
    python3 main.py assemble --input /input --work /app/work --output /app/output \
                             --draft /app/work/draft.md
    python3 main.py run      --input /input --work /app/work --output /app/output

Stdlib only, no network.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIRED = ("writing_lib", "task_specific")


def _bootstrap_wrapped_scripts():
    """Direct execution from tracks/ where scripts are stored as *.py.md."""
    if all((HERE / (name + ".py")).is_file() for name in REQUIRED):
        return None
    mirror = Path(tempfile.mkdtemp(prefix="manuscript-kit-scripts-"))
    copied = 0
    for source in sorted(HERE.glob("*.py.md")):
        shutil.copy2(source, mirror / source.name[:-3])
        copied += 1
    if copied:
        sys.path.insert(0, str(mirror))
        return mirror
    return None


_BOOTSTRAP_DIR = _bootstrap_wrapped_scripts()
try:  # package mode
    from . import writing_lib as lib
except ImportError:  # direct mode
    try:
        import writing_lib as lib
    except ImportError as exc:  # engine really missing: cannot start
        print("cannot start: %s" % exc, file=sys.stderr)
        sys.exit(2)


def _read(path, default=""):
    return lib.read_text(path, default)


def _summary(command, result):
    return {
        "ok": result.get("status") != "input_error",
        "command": command,
        "status": result.get("status"),
        "case_id": result.get("case_id"),
        "deliverable": (result.get("deliverable") or {}).get("path"),
        "words": (result.get("manuscript") or {}).get("word_count"),
        "sections": len((result.get("manuscript") or {}).get("sections") or []),
        "facts": len(result.get("preserved_facts") or []),
        "evidence_map": len(result.get("evidence_map") or []),
        "uncertainty": len(result.get("uncertainty") or []),
        "audit_ok": (result.get("audit_summary") or {}).get("ok"),
        "problems": (result.get("audit_summary") or {}).get("problems", [])[:6],
    }


def _input_error(input_dir, work_dir, output_dir, reason):
    output_dir, work_dir = Path(output_dir), Path(work_dir)
    result = {
        "case_id": "unknown-case",
        "task_family": "manuscript",
        "status": "input_error",
        "deliverable": None,
        "preserved_facts": [],
        "evidence_map": [],
        "change_log": [{"step": 1, "action": "stop before writing",
                        "reason": reason, "evidence": str(input_dir)}],
        "artifacts": [],
        "uncertainty": [{"item": "input", "reason": reason,
                         "impact": "no manuscript was produced"}],
        "generated_by": "scripts/main.py",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    lib.write_json(output_dir / "result.json", result)
    return result


def _context(input_dir, work_dir, output_dir):
    """Read input, build work products; always return (ctx, error)."""
    input_dir, work_dir, output_dir = (Path(input_dir), Path(work_dir),
                                       Path(output_dir))
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    case = lib.read_json(input_dir / "case.json", {}) or {}
    task_text = _read(input_dir / "task.md")
    if not case and not task_text:
        return None, "neither case.json nor task.md exists in %s" % input_dir
    inv, texts = lib.inventory(input_dir)
    contract = lib.build_contract(case, task_text)
    sources = lib.extract_sources(texts, input_dir, work_dir)
    facts = lib.extract_facts(sources["sources"])
    outline = lib.plan_outline(contract, facts, sources["references"])
    lib.write_json(work_dir / "inventory.json", inv)
    lib.write_json(work_dir / "contract.json", contract)
    lib.write_json(work_dir / "sources.json", sources)
    lib.write_json(work_dir / "references.json",
                   {"references": sources["references"]})
    lib.write_json(work_dir / "evidence-ledger.json", {"facts": facts})
    lib.write_json(work_dir / "outline.json", outline)
    lib.write_text(work_dir / "writing-brief.md",
                   lib.writing_brief(contract, inv, sources, facts, outline))
    return {"case": case, "task_text": task_text, "inv": inv,
            "contract": contract, "sources": sources, "facts": facts,
            "outline": outline, "input_dir": input_dir,
            "work_dir": work_dir, "output_dir": output_dir}, None


def _load_or_context(input_dir, work_dir, output_dir):
    """Reuse persisted work products when available (cheap assemble)."""
    work_dir = Path(work_dir)
    required = ("contract.json", "sources.json", "evidence-ledger.json",
                "outline.json", "inventory.json")
    if all((work_dir / name).is_file() for name in required):
        contract = lib.read_json(work_dir / "contract.json", {}) or {}
        sources = lib.read_json(work_dir / "sources.json", {}) or {}
        facts = (lib.read_json(work_dir / "evidence-ledger.json", {}) or {}).get(
            "facts", [])
        outline = lib.read_json(work_dir / "outline.json", {}) or {}
        inv = lib.read_json(work_dir / "inventory.json", {}) or {}
        input_dir, output_dir = Path(input_dir), Path(output_dir)
        case = lib.read_json(input_dir / "case.json", {}) or {}
        task_text = _read(input_dir / "task.md")
        if not (work_dir / "writing-brief.md").is_file():
            lib.write_text(work_dir / "writing-brief.md",
                           lib.writing_brief(contract, inv, sources, facts,
                                             outline))
        return {"case": case, "task_text": task_text, "inv": inv,
                "contract": contract, "sources": sources, "facts": facts,
                "outline": outline, "input_dir": input_dir,
                "work_dir": work_dir, "output_dir": output_dir}, None
    return _context(input_dir, work_dir, output_dir)


def _ensure_manuscript(ctx, draft_path=None):
    """Return (text, scaffold, note).  A scaffold is always PARTIAL."""
    target = ctx["output_dir"] / "manuscript.md"
    note = None
    if draft_path:
        draft = Path(draft_path)
        if draft.is_file():
            text = _read(draft)
            if draft.resolve() != target.resolve():
                lib.write_text(target, text)
            return text, lib.SCAFFOLD_MARKER in text, None
        note = "draft not found: %s" % draft_path
    if target.is_file():
        text = _read(target)
        return text, lib.SCAFFOLD_MARKER in text, note
    text = lib.compose_scaffold(ctx["contract"], ctx["outline"],
                                ctx["facts"], ctx["sources"]["references"])
    lib.write_text(target, text)
    return text, True, note


def prepare(args):
    ctx, error = _context(args.input, args.work, args.output)
    if error:
        result = _input_error(args.input, args.work, args.output, error)
        print(json.dumps(_summary("prepare", result), ensure_ascii=False, indent=2))
        return 0
    text, scaffold, _ = _ensure_manuscript(ctx)
    result = lib.build_result(ctx["case"], ctx["contract"], ctx["inv"],
                              ctx["sources"], ctx["facts"], text,
                              work_dir=ctx["work_dir"],
                              output_dir=ctx["output_dir"],
                              mode="prepare", scaffold=scaffold)
    print(json.dumps(_summary("prepare", result), ensure_ascii=False, indent=2))
    return 0


def assemble(args):
    ctx, error = _load_or_context(args.input, args.work, args.output)
    if error:
        result = _input_error(args.input, args.work, args.output, error)
        print(json.dumps(_summary("assemble", result), ensure_ascii=False,
                         indent=2))
        return 0
    text, scaffold, note = _ensure_manuscript(ctx, args.draft)
    result = lib.build_result(ctx["case"], ctx["contract"], ctx["inv"],
                              ctx["sources"], ctx["facts"], text,
                              work_dir=ctx["work_dir"],
                              output_dir=ctx["output_dir"],
                              mode="assemble", scaffold=scaffold,
                              extra_notes=[note] if note else None)
    print(json.dumps(_summary("assemble", result), ensure_ascii=False, indent=2))
    return 0


def run(args):
    ctx, error = _context(args.input, args.work, args.output)
    if error:
        result = _input_error(args.input, args.work, args.output, error)
        print(json.dumps(_summary("run", result), ensure_ascii=False, indent=2))
        return 0
    text, scaffold, note = _ensure_manuscript(ctx, args.draft)
    result = lib.build_result(ctx["case"], ctx["contract"], ctx["inv"],
                              ctx["sources"], ctx["facts"], text,
                              work_dir=ctx["work_dir"],
                              output_dir=ctx["output_dir"],
                              mode="run", scaffold=scaffold,
                              extra_notes=[note] if note else None)
    print(json.dumps(_summary("run", result), ensure_ascii=False, indent=2))
    return 0


def _emergency(args, exc):
    """Write an honest partial result on unexpected failure; exit 0."""
    try:
        output = Path(getattr(args, "output", "/app/output"))
        work = Path(getattr(args, "work", "/app/work"))
        output.mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        result = {
            "case_id": "unknown-case",
            "task_family": "manuscript",
            "status": "partial",
            "deliverable": None,
            "preserved_facts": [],
            "evidence_map": [],
            "change_log": [{"step": 1, "action": "entry point failed",
                            "reason": "%s: %s" % (type(exc).__name__, exc),
                            "evidence": "see uncertainty"}],
            "artifacts": [],
            "uncertainty": [{"item": "runtime",
                             "reason": "%s: %s" % (type(exc).__name__, exc),
                             "impact": "no reliable output was produced"}],
            "generated_by": "scripts/main.py (emergency)",
        }
        lib.write_json(output / "result.json", result)
        print(json.dumps({"ok": False, "status": "partial",
                          "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 0
    except OSError:
        return 2


def _parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--input", default=argparse.SUPPRESS)
    common.add_argument("--work", default=argparse.SUPPRESS)
    common.add_argument("--output", default=argparse.SUPPRESS)
    common.add_argument("--draft", default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--draft", default=None)
    sub = ap.add_subparsers(dest="command")
    for name in ("prepare", "assemble", "run", "write", "finalize"):
        sub.add_parser(name, parents=[common])
    return ap


def main(argv=None):
    args = _parser().parse_args(argv)
    command = args.command or "run"
    handler = {"prepare": prepare, "assemble": assemble, "run": run,
               "write": assemble, "finalize": assemble}.get(command, run)
    try:
        return handler(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - entry point must stay alive
        return _emergency(args, exc)


if __name__ == "__main__":
    sys.exit(main())
