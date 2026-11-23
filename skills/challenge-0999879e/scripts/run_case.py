#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entry point for every Period 4 skill.

Usage:
    python3 run_case.py prepare  --input /input --work /app/work --output /app/output
    # model writes the requested work/output file
    python3 run_case.py finalize --input /input --work /app/work --output /app/output
    python3 run_case.py run      --input /input --work /app/work --output /app/output

The entry point always writes an honest ``result.json`` for ``partial`` and
``input_error`` as well; only a completely unusable installation returns 2.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import skillcore as S  # noqa: E402

VERSION = "1.0.0"


def load_config(skill_dir):
    path = Path(skill_dir) / "task.json"
    if path.is_file():
        return S.load_json(path, {})
    return {}


def resolve_module(config):
    module_name = config.get("module")
    if not module_name:
        family = (config.get("family") or "").replace("-", "_")
        module_name = "fam_" + family if family else ""
    if not module_name:
        raise SystemExit("task.json must define 'module' or 'family'")
    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit("cannot import %s: %s" % (module_name, exc))


def make_context(args, config):
    skill_dir = Path(args.skill_dir) if args.skill_dir else HERE.parent
    return S.Context(args.input, args.work, args.output, skill_dir, config)


def safe_error_result(ctx, exc):
    try:
        ctx.ensure()
        ctx.result.change("error", "entry", "%s: %s" % (type(exc).__name__, exc),
                          traceback.format_exc()[-1200:])
        ctx.result.uncertainty("entry_failure", str(exc),
                               "no substantive result was produced",
                               "inspect work/main-error.json and rerun")
        S.write_json(ctx.work / "main-error.json", {
            "error": str(exc), "type": type(exc).__name__,
            "traceback": traceback.format_exc(),
        })
        return ctx.result.finalize("input_error")
    except Exception:  # noqa: BLE001
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Period 4 skill entry point")
    parser.add_argument("command", choices=("prepare", "finalize", "run", "inspect", "version"))
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--skill-dir", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    skill_dir = Path(args.skill_dir) if args.skill_dir else HERE.parent
    config = load_config(skill_dir)

    if args.command == "version":
        print(json.dumps({"version": VERSION, "skill_dir": str(skill_dir),
                          "config": config}, ensure_ascii=False, indent=2))
        return 0

    try:
        module = resolve_module(config)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    ctx = make_context(args, config)
    try:
        if args.command == "inspect":
            ctx.load(ocr=bool(config.get("ocr", True)))
            ctx.write_context()
            S.print_json({"case_id": ctx.case_id, "files": len(ctx.inv),
                          "documents": [{"path": d.get("rel_path"),
                                         "kind": d.get("kind"),
                                         "chars": len(S.doc_text(d))}
                                        for d in ctx.docs]})
            return 0

        if args.command == "prepare":
            ctx.load(ocr=bool(config.get("ocr", True)))
            ctx.write_context()
            payload = module.prepare(ctx)
            if payload is None:
                payload = ctx.provisional()
            S.print_json({"command": "prepare", "case_id": ctx.case_id,
                          "status": payload.get("status"),
                          "work": str(ctx.work), "output": str(ctx.output)})
            return 0

        if args.command == "finalize":
            ctx.load(ocr=bool(config.get("ocr", True)))
            ctx.write_context()
            payload = module.finalize(ctx)
            S.print_json({"command": "finalize", "case_id": ctx.case_id,
                          "status": payload.get("status"),
                          "deliverable": payload.get("deliverable"),
                          "artifacts": [a.get("path") for a in payload.get("artifacts", [])]})
            return 0

        if args.command == "run":
            ctx.load(ocr=bool(config.get("ocr", True)))
            ctx.write_context()
            module.prepare(ctx)
            payload = module.finalize(ctx)
            S.print_json({"command": "run", "case_id": ctx.case_id,
                          "status": payload.get("status"),
                          "deliverable": payload.get("deliverable"),
                          "artifacts": [a.get("path") for a in payload.get("artifacts", [])]})
            return 0
    except Exception as exc:  # noqa: BLE001
        payload = safe_error_result(ctx, exc)
        print(json.dumps({"status": "input_error", "error": str(exc),
                          "result_written": payload is not None},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 0 if payload is not None else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
