#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Writing 13 — LaTeX source editing, compilation and reproducible build record."""
from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
from pathlib import Path

import skillcore as S

TEX_EXTS = {".tex", ".latex", ".bib", ".cls", ".sty", ".bst", ".cfg", ".def",
            ".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"}


def _which(name):
    return shutil.which(name)


def _copy_project(ctx, target):
    target = Path(target)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    copied = []
    for row in ctx.inv:
        rel = row.get("path") or ""
        if not rel or rel in ("case.json", "task.md"):
            continue
        suffix = Path(rel).suffix.lower()
        if suffix not in TEX_EXTS and suffix not in (".md", ".txt"):
            continue
        src = ctx.input / rel
        if not src.is_file():
            continue
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(rel)
    return copied


def _main_tex(project):
    candidates = sorted(Path(project).rglob("*.tex"))
    for path in candidates:
        text = S.read_text(path)
        if "\\documentclass" in text:
            return path
    return candidates[0] if candidates else None


def _compile(project, main):
    """Try common TeX engines; return a real result, never a fabricated one."""
    logs = []
    commands = []
    try:
        main_arg = str(Path(main).relative_to(project))
    except ValueError:
        main_arg = Path(main).name
    if _which("latexmk"):
        commands.append(["latexmk", "-pdf", "-interaction=nonstopmode",
                         "-halt-on-error", "-file-line-error", main_arg])
    for engine in ("pdflatex", "xelatex", "lualatex"):
        if _which(engine):
            commands.append([engine, "-interaction=nonstopmode", "-halt-on-error",
                             "-file-line-error", main_arg])
            break
    if not commands:
        return {"available": False, "ok": False, "engine": None,
                "error": "no TeX engine found (latexmk/pdflatex/xelatex/lualatex)",
                "log": "", "pdf": None, "passes": 0}
    last = None
    for command in commands:
        for pass_index in range(2):
            try:
                proc = subprocess.run(command, cwd=str(project), capture_output=True,
                                      timeout=180)
            except (OSError, subprocess.TimeoutExpired) as exc:
                last = {"available": True, "ok": False, "engine": command[0],
                        "error": str(exc), "log": "", "pdf": None,
                        "passes": pass_index}
                break
            log = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            logs.append(log)
            pdf = main.with_suffix(".pdf")
            last = {"available": True, "ok": proc.returncode == 0 and pdf.is_file(),
                    "engine": command[0], "error": "" if proc.returncode == 0 else
                    "exit %d" % proc.returncode, "log": log[-20000:],
                    "pdf": str(pdf) if pdf.is_file() else None,
                    "passes": pass_index + 1}
            if proc.returncode != 0:
                break
        if last and last.get("ok"):
            break
    if last:
        last["full_log"] = "\n".join(logs)[-40000:]
    return last or {"available": False, "ok": False, "engine": None,
                    "error": "no compile attempt", "log": "", "pdf": None, "passes": 0}


def _log_issues(log):
    errors, warnings = [], []
    for line in (log or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("!") or "Undefined control sequence" in stripped:
            errors.append(stripped[:300])
        elif re.search(r"LaTeX Warning:|Package .* Warning:|Overfull \\hbox|"
                       r"Underfull \\hbox|undefined", stripped, re.I):
            warnings.append(stripped[:300])
    # De-duplicate while preserving order.
    return list(dict.fromkeys(errors))[:100], list(dict.fromkeys(warnings))[:200]


def _static_checks(project, main):
    text = S.read_text(main)
    project_text = "\n\n".join(S.read_text(p) for p in Path(project).rglob("*.tex"))
    bib_text = "\n\n".join(S.read_text(p) for p in Path(project).rglob("*.bib"))
    results = {
        "documentclass": bool(re.search(r"\\documentclass(?:\[[^\]]*\])?\{", text)),
        "begin_document": "\\begin{document}" in text,
        "end_document": "\\end{document}" in text,
        "balanced_braces": text.count("{") == text.count("}"),
        "citations": sorted(set(re.findall(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])?\{([^{}]+)\}", project_text))),
        "labels": sorted(set(re.findall(r"\\label\{([^{}]+)\}", project_text))),
        "refs": sorted(set(re.findall(r"\\(?:ref|autoref|eqref|pageref)\{([^{}]+)\}", project_text))),
        "bib_keys": sorted(set(re.findall(r"@\w+\s*\{\s*([^,\s]+)", bib_text))),
    }
    results["missing_refs"] = [x for x in results["refs"] if x not in results["labels"]]
    results["missing_citations"] = [x for x in results["citations"] if x not in results["bib_keys"]]
    results["ok"] = (results["documentclass"] and results["begin_document"]
                     and results["end_document"] and results["balanced_braces"])
    return results


def prepare(ctx):
    tex_docs = [d for d in ctx.docs if d.get("kind") == "tex"]
    if not tex_docs:
        ctx.result.change("prepare", "input", "no .tex source found")
        ctx.result.uncertainty("latex", "no readable LaTeX source",
                               "cannot compile or edit", "provide the project")
        return ctx.result.finalize("input_error")
    project = ctx.work / "project"
    baseline = ctx.work / "project-baseline"
    copied = _copy_project(ctx, project)
    _copy_project(ctx, baseline)
    main = _main_tex(project)
    if main is None:
        ctx.result.change("prepare", "input", "no main .tex")
        ctx.result.uncertainty("latex", "no .tex file found",
                               "cannot compile", "provide main.tex")
        return ctx.result.finalize("input_error")
    static = _static_checks(project, main)
    compile_result = _compile(project, main)
    errors, warnings = _log_issues(compile_result.get("full_log") or compile_result.get("log") or "")
    S.write_json(ctx.work / "latex-project.json", {
        "case_id": ctx.case_id, "main": str(main.relative_to(project)),
        "files": copied, "static": static,
        "engine": compile_result.get("engine"),
        "pdf": compile_result.get("pdf"),
    })
    S.write_json(ctx.work / "compile-baseline.json", {
        "case_id": ctx.case_id, "available": compile_result.get("available"),
        "ok": compile_result.get("ok"), "engine": compile_result.get("engine"),
        "error": compile_result.get("error"), "passes": compile_result.get("passes"),
        "pdf": compile_result.get("pdf"), "errors": errors, "warnings": warnings[:60],
    })
    S.write_json(ctx.work / "error-list.json", {
        "case_id": ctx.case_id, "errors": errors, "warnings": warnings,
        "static": static,
    })
    S.write_json(ctx.work / "latex-edits-template.json", {
        "case_id": ctx.case_id, "main": str(main.relative_to(project)),
        "edits": [{"id": "L001", "file": str(main.relative_to(project)),
                   "before": "verbatim source text", "after": "fixed source text",
                   "reason": "error or template requirement", "error_id": ""}],
        "template_requirements": [],
    })
    ctx.result.change("inventory", "latex", "%d project files; main=%s" % (
        len(copied), main.relative_to(project)))
    ctx.result.change("compile", "baseline", "engine=%s ok=%s" % (
        compile_result.get("engine"), compile_result.get("ok")))
    return ctx.provisional(reason="LaTeX project copied; edit work/project or write latex-edits.json, then run finalize")


def _apply_edits(project, edits):
    applied, blocked = [], []
    for edit in edits:
        rel = str(edit.get("file") or "")
        path = Path(project) / rel
        if not path.is_file():
            blocked.append({"edit": edit, "reason": "file not found: %s" % rel})
            continue
        text = S.read_text(path)
        before = str(edit.get("before") or "")
        after = str(edit.get("after") or "")
        if not before:
            blocked.append({"edit": edit, "reason": "empty before"})
            continue
        if text.count(before) != 1:
            blocked.append({"edit": edit, "reason": "before occurs %d times" % text.count(before)})
            continue
        index = text.find(before)
        line = text[:index].count("\n") + 1
        S.write_text(path, text.replace(before, after, 1))
        applied.append({"edit": edit, "file": rel, "line": line})
    return applied, blocked


def _file_changes(baseline, project):
    changes = []
    for path in sorted(Path(project).rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(project).as_posix()
        old_path = Path(baseline) / rel
        suffix = path.suffix.lower()
        sha_before = S.sha256_path(old_path) if old_path.is_file() else ""
        sha_after = S.sha256_path(path)
        if old_path.is_file() and sha_before == sha_after:
            continue
        if suffix in (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg"):
            changes.append({"file": rel, "changed": True, "diff": "",
                            "binary": True, "sha256_before": sha_before,
                            "sha256_after": sha_after})
            continue
        new_text = S.read_text(path)
        old_text = S.read_text(old_path)
        diff = list(difflib.unified_diff(old_text.splitlines(), new_text.splitlines(),
                                         fromfile=rel, tofile=rel, lineterm=""))
        changes.append({"file": rel, "changed": True,
                        "diff": "\n".join(diff[:400]),
                        "sha256_before": sha_before,
                        "sha256_after": sha_after})
    return changes


def finalize(ctx):
    project = ctx.work / "project"
    baseline = ctx.work / "project-baseline"
    meta = S.load_json(ctx.work / "latex-project.json", {})
    if not project.is_dir() or not meta.get("main"):
        ctx.result.change("finalize", "latex", "project workspace missing")
        ctx.result.uncertainty("latex", "no prepared LaTeX project",
                               "cannot finalize", "run prepare first")
        return ctx.result.finalize("input_error")
    edits_payload = S.load_json(ctx.work / "latex-edits.json", {})
    edits = edits_payload.get("edits") if isinstance(edits_payload, dict) else []
    applied, blocked = _apply_edits(project, edits or [])
    changes = _file_changes(baseline, project)
    main = project / meta["main"]
    static = _static_checks(project, main)
    compile_result = _compile(project, main)
    errors, warnings = _log_issues(compile_result.get("full_log") or compile_result.get("log") or "")
    pdf_path = None
    if compile_result.get("pdf"):
        source_pdf = Path(compile_result["pdf"])
        if source_pdf.is_file() and source_pdf.read_bytes()[:5] == b"%PDF-":
            pdf_path = ctx.output / "main.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_pdf, pdf_path)
    out_project = ctx.output / "project"
    if out_project.exists():
        shutil.rmtree(out_project)
    shutil.copytree(project, out_project)
    # Exclude transient build files and the generated PDF from the source bundle.
    for junk in list(out_project.rglob("*.aux")) + list(out_project.rglob("*.log")) + \
            list(out_project.rglob("*.out")) + list(out_project.rglob("*.toc")) + \
            list(out_project.rglob("*.bbl")) + list(out_project.rglob("*.blg")) + \
            list(out_project.rglob("*.fls")) + list(out_project.rglob("*.fdb_latexmk")):
        try:
            junk.unlink()
        except OSError:
            pass
    report = {
        "case_id": ctx.case_id, "main": meta["main"],
        "available": compile_result.get("available"),
        "ok": compile_result.get("ok"), "engine": compile_result.get("engine"),
        "error": compile_result.get("error"), "passes": compile_result.get("passes"),
        "errors": errors, "warnings": warnings, "static": static,
        "edits_applied": len(applied), "edits_blocked": len(blocked),
        "file_changes": changes, "pdf": "main.pdf" if pdf_path else None,
    }
    S.write_json(ctx.output / "compile-report.json", report)
    S.write_json(ctx.output / "change-record.json", {
        "case_id": ctx.case_id, "applied": applied, "blocked": blocked,
        "file_changes": changes,
    })
    error_lines = ["# Compile and static analysis", "",
                   "- Engine: %s" % (compile_result.get("engine") or "unavailable"),
                   "- Compile ok: %s" % compile_result.get("ok"),
                   "- Passes: %s" % compile_result.get("passes"),
                   "- Static checks ok: %s" % static.get("ok"), ""]
    if errors:
        error_lines += ["## Errors", ""] + ["- %s" % e for e in errors[:50]] + [""]
    if warnings:
        error_lines += ["## Warnings", ""] + ["- %s" % w for w in warnings[:80]] + [""]
    if static.get("missing_refs"):
        error_lines += ["## Missing references", ""] + [
            "- %s" % x for x in static["missing_refs"][:50]] + [""]
    if static.get("missing_citations"):
        error_lines += ["## Missing citations", ""] + [
            "- %s" % x for x in static["missing_citations"][:50]] + [""]
    S.write_text(ctx.output / "error-analysis.md", "\n".join(error_lines))

    critical = (not static.get("ok") or bool(errors) or bool(blocked)
                or static.get("missing_refs") or static.get("missing_citations")
                or not compile_result.get("ok"))
    status = "completed" if not critical and pdf_path else "partial"
    if not compile_result.get("available"):
        ctx.result.uncertainty("compile_unavailable", compile_result.get("error", ""),
                               "no PDF could be produced", "run in an image with TeX Live")
    if compile_result.get("available") and not compile_result.get("ok"):
        ctx.result.uncertainty("compile_failed", compile_result.get("error", ""),
                               "source does not compile", "fix the errors in error-analysis.md")
    if blocked:
        ctx.result.uncertainty("blocked_edits", "%d edits could not be applied" % len(blocked),
                               "requested fixes are absent", "fix before anchors")
    if static.get("missing_refs"):
        ctx.result.uncertainty("missing_refs", static["missing_refs"],
                               "unresolved cross-references", "add labels or fix refs")
    if static.get("missing_citations"):
        ctx.result.uncertainty("missing_citations", static["missing_citations"],
                               "citations have no bibliography entries", "add bib entries")

    evidence = []
    for change in changes:
        evidence.append({"claim_id": change["file"], "claim": "source edited",
                         "source": {"path": change["file"], "locator": "diff"},
                         "quote": change.get("diff", "")[:500], "supports": "change"})
    ctx.result.add_artifact(out_project, "editable_source", "directory",
                            "complete LaTeX source tree")
    ctx.result.add_artifact(ctx.output / "compile-report.json", "audit", "json",
                            "compile, static checks and changes")
    ctx.result.add_artifact(ctx.output / "change-record.json", "change_record", "json",
                            "applied edits and diffs")
    ctx.result.add_artifact(ctx.output / "error-analysis.md", "readable", "md",
                            "compile errors, warnings and missing references")
    if pdf_path:
        ctx.result.add_artifact(pdf_path, "export_artifact", "pdf",
                                "compiled PDF")
    ctx.result.change("edit", "latex", "%d edits applied, %d blocked" % (
        len(applied), len(blocked)))
    ctx.result.change("compile", "latex", "engine=%s ok=%s" % (
        compile_result.get("engine"), compile_result.get("ok")))
    return ctx.result.finalize(
        status,
        deliverable=out_project,
        result={"summary": "LaTeX project %s; compile ok=%s; %d errors, %d warnings." % (
                    "compiled" if pdf_path else "not compiled",
                    compile_result.get("ok"), len(errors), len(warnings)),
                "report": report},
        claims=[{"claim_id": "compile", "text": "compile ok=%s" % compile_result.get("ok")},
                {"claim_id": "static", "text": "static checks ok=%s" % static.get("ok")}],
        preserved_facts=[],
        evidence_map=evidence,
    )
