#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generic handler for Period 4 community tracks.

The 37 search/library/research/review/analysis/experiment challenges are
open-ended Skill tasks.  This module supplies the deterministic half:

* inventory and structured-material extraction (JSON/CSV/text);
* a model-authored work file for the substantive answer;
* evidence, required-field, artifact and optional Python-execution checks;
* the official reading and writing result fields.

The model writes the domain content described in each SKILL.md; this module
never invents candidates, findings, reviews or analysis results.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import skillcore as S


def _config(ctx):
    return ctx.config or {}


def _work_name(ctx):
    raw = str(_config(ctx).get("work_file") or "work/answer.json")
    return Path(raw).name


def _output_files(ctx):
    files = _config(ctx).get("output_files") or []
    return [str(x) for x in files]


def _generic_template(ctx):
    return {
        "case_id": ctx.case_id,
        "mode": _config(ctx).get("mode") or _config(ctx).get("family"),
        "summary": "",
        "answer": "",
        "findings": [],
        "candidates": [],
        "queries": [],
        "decisions": [],
        "evidence": [],
        "uncertainties": [],
        "artifacts": [],
    }


def _material_payload(ctx):
    docs = []
    for doc in ctx.docs:
        item = {
            "source_id": doc.get("source_id"),
            "path": doc.get("rel_path"),
            "kind": doc.get("kind"),
            "text": S.doc_text(doc)[:200000],
            "chars": len(S.doc_text(doc)),
            "warnings": doc.get("warnings") or [],
        }
        if isinstance(doc.get("data"), (dict, list)):
            item["json"] = doc["data"]
        if doc.get("rows"):
            item["rows"] = doc["rows"][:2000]
        if doc.get("entries"):
            item["entries"] = doc["entries"]
        docs.append(item)
    return docs


def prepare(ctx):
    if not any(S.doc_text(d).strip() or d.get("data") for d in ctx.docs):
        ctx.result.change("prepare", "input", "no readable material")
        ctx.result.uncertainty("materials", "no readable text, JSON or table",
                               "the task cannot start", "provide the case material")
        return ctx.result.finalize("input_error")
    materials = _material_payload(ctx)
    S.write_json(ctx.work / "materials.json", {
        "case_id": ctx.case_id,
        "family": _config(ctx).get("family"),
        "mode": _config(ctx).get("mode"),
        "task_excerpt": S.excerpt(ctx.task, 6000),
        "documents": materials,
        "inventory": ctx.inv,
    })
    work_name = _work_name(ctx)
    work_path = ctx.work / work_name
    if not work_path.exists():
        template = _config(ctx).get("work_template") or _generic_template(ctx)
        S.write_json(work_path, template)
    output_files = _output_files(ctx)
    readme = [
        "# %s" % (_config(ctx).get("title") or ctx.case_id),
        "",
        "This is a provisional workspace. The model must write:",
        "",
        "- `%s`" % work_name,
    ]
    for name in output_files:
        readme.append("- `%s`" % name)
    readme += ["", "## Input inventory"]
    for row in ctx.inv:
        if row.get("role") == "material":
            readme.append("- `%s` (%s, %d bytes)" % (
                row.get("path"), row.get("kind"), row.get("bytes", 0)))
    S.write_text(ctx.output / "README-partial.md", "\n".join(readme) + "\n")
    ctx.result.add_artifact(ctx.output / "README-partial.md", "partial_draft", "md",
                            "inventory and pending work files")
    ctx.result.change("prepare", "materials",
                      "%d material files extracted" % len(materials))
    return ctx.provisional(reason="materials extracted; write the work file and outputs, then run finalize")


def _walk_evidence(value, out):
    if isinstance(value, dict):
        quote = value.get("quote")
        source = value.get("source") if isinstance(value.get("source"), dict) else {}
        path = value.get("path") or source.get("path") or ""
        locator = value.get("locator") or source.get("locator") or ""
        if isinstance(quote, str) and quote.strip():
            out.append({"quote": quote, "path": path or "", "locator": locator or ""})
        for item in value.values():
            _walk_evidence(item, out)
    elif isinstance(value, list):
        for item in value:
            _walk_evidence(item, out)


def _check_evidence(evidence, ctx):
    by_path = {}
    for doc in ctx.docs:
        by_path[doc.get("rel_path")] = S.doc_text(doc)
        by_path[doc.get("path")] = S.doc_text(doc)
    problems = []
    for index, item in enumerate(evidence, 1):
        path = str(item.get("path") or "")
        text = by_path.get(path)
        if text is None:
            # A missing path is a warning, not a fabricated quote.
            problems.append("evidence %d: source path not found: %s" % (index, path))
            continue
        ok, reason = S.quote_supported(text, item.get("quote", ""))
        if not ok:
            problems.append("evidence %d: %s" % (index, reason))
    return problems


def _run_python(ctx):
    script = ctx.work / "analysis.py"
    if not script.is_file():
        return None
    log_path = ctx.output / "execution.log"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run([sys.executable, str(script)],
                              cwd=str(ctx.work), capture_output=True,
                              text=True, timeout=300, env=env)
        log = proc.stdout + ("\n[stderr]\n" + proc.stderr if proc.stderr else "")
        S.write_text(log_path, log[-100000:])
        return {"returncode": proc.returncode, "log": str(log_path),
                "stdout": proc.stdout[-5000:], "stderr": proc.stderr[-5000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        S.write_text(log_path, "execution failed: %s\n" % exc)
        return {"returncode": 2, "log": str(log_path), "stdout": "", "stderr": str(exc)}


def _resolve_output(ctx, name):
    path = Path(str(name or ""))
    if path.is_absolute():
        return path
    parts = path.parts
    if parts and parts[0] == "output":
        return ctx.output.joinpath(*parts[1:])
    return ctx.output / path


def _collect_outputs(ctx):
    files = []
    for name in _output_files(ctx):
        path = _resolve_output(ctx, name)
        if path.exists():
            files.append(path)
    # Figures and tables produced by the model or analysis script.
    for folder in (ctx.output / "figures", ctx.output / "tables", ctx.output / "results"):
        if folder.is_dir():
            files.extend(p for p in sorted(folder.rglob("*")) if p.is_file())
    # De-duplicate while preserving order.
    seen, out = set(), []
    for path in files:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def finalize(ctx):
    work_name = _work_name(ctx)
    work_path = ctx.work / work_name
    work = S.load_json(work_path, {})
    deliverable_name = _config(ctx).get("deliverable") or ""
    deliverable_path = _resolve_output(ctx, deliverable_name)
    if not isinstance(work, dict) or not work:
        # Allow a JSON deliverable as the work product.
        if deliverable_path.is_file() and deliverable_path.suffix.lower() == ".json":
            work = S.load_json(deliverable_path, {})
        elif deliverable_path.is_file():
            work = {"summary": S.read_text(deliverable_path)[:2000]}
    if not work and not deliverable_path.is_file():
        ctx.result.change("finalize", "output", "no work file and no deliverable")
        ctx.result.uncertainty("deliverable", "the model-authored work file is missing",
                               "no substantive answer", "write %s" % work_name)
        return ctx.result.finalize("partial")

    required = _config(ctx).get("required_fields") or []
    missing = [field for field in required if field not in work and not (
        deliverable_path.is_file() and field in S.load_json(deliverable_path, {}))]
    problems = ["missing required field: %s" % field for field in missing]
    evidence = []
    _walk_evidence(work, evidence)
    problems.extend(_check_evidence(evidence, ctx))

    execution = _run_python(ctx)
    if execution is not None:
        ctx.result.add_artifact(execution["log"], "execution_log", "log",
                                "stdout/stderr from analysis.py")
        if execution["returncode"] != 0:
            problems.append("analysis.py exited %d" % execution["returncode"])
            ctx.result.uncertainty("analysis_execution", execution["stderr"],
                                   "generated analysis did not complete",
                                   "fix analysis.py and rerun finalize")

    artifacts = _collect_outputs(ctx)
    existing = {item.get("path") for item in ctx.result.data.get("artifacts", [])}
    for path in artifacts:
        rel = S.relpath(path, ctx.output)
        if rel in existing:
            continue
        role = "deliverable" if path == deliverable_path else "artifact"
        ctx.result.add_artifact(path, role,
                                path.suffix.lstrip(".") or "file",
                                "model-produced artifact")
        existing.add(rel)
    if deliverable_name and not deliverable_path.exists():
        problems.append("deliverable missing: %s" % deliverable_name)

    summary = work.get("summary") or work.get("answer") or ""
    findings = work.get("findings") or work.get("claims") or work.get("candidates") or []
    claims = []
    for index, item in enumerate(findings[:200], 1):
        if isinstance(item, dict):
            claims.append({"claim_id": item.get("id") or "F%03d" % index,
                           "text": item.get("text") or item.get("statement")
                           or item.get("title") or item.get("label") or "",
                           "evidence": item.get("evidence") or item.get("source")})
        else:
            claims.append({"claim_id": "F%03d" % index, "text": str(item)})
    if not claims:
        claims = [{"claim_id": "summary", "text": summary}] if summary else []
    preserved = []
    for item in evidence[:100]:
        for token in S.number_tokens(item.get("quote", ""))[:5]:
            preserved.append({"kind": "number", "source": token["raw"],
                              "target": token["raw"], "locator": item.get("locator"),
                              "value": token["raw"], "unit": ""})
    success = _config(ctx).get("status_success") or (
        "completed" if _config(ctx).get("profile") in ("writing", "both") else "answered")
    critical = bool(problems) or not deliverable_path.exists()
    status = success if not critical else "partial"
    for problem in problems[:25]:
        ctx.result.uncertainty("validation", problem,
                               "the result is incomplete or not fully traceable",
                               "fix the work file or evidence and rerun finalize")
    ctx.result.change("finalize", "output", "work/output assembled and audited")
    return ctx.result.finalize(
        status,
        deliverable=deliverable_path if deliverable_path.exists() else None,
        result={"summary": summary, "mode": _config(ctx).get("mode"),
                "required_fields": required, "missing_fields": missing,
                "evidence_count": len(evidence), "execution": execution,
                "artifacts": [str(p.relative_to(ctx.output)) for p in artifacts
                              if p.is_relative_to(ctx.output)]},
        claims=claims,
        preserved_facts=preserved,
        evidence_map=[{"claim_id": "evidence-%d" % i, "claim": "",
                       "source": {"path": e.get("path"), "locator": e.get("locator")},
                       "quote": e.get("quote"), "supports": "source"}
                      for i, e in enumerate(evidence, 1)],
    )
