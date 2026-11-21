#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Writing 13 — LaTeX source editing, compilation and reproducible build record.

The module is deliberately conservative:

* a PDF is reported only when a real engine process exited 0 and the file
  starts with ``%PDF-``;
* a stale PDF copied from the input project is moved aside before compiling,
  so it can never be mistaken for a build artifact;
* a missing TeX engine is an honest ``compile_unavailable`` partial result,
  never a fabricated PDF;
* the engine is chosen from the project (ctex/xeCJK/fontspec => XeLaTeX,
  luatexja/directlua => LuaLaTeX, otherwise pdfLaTeX), not blindly from the
  first executable on PATH;
* every compile attempt has a total time budget so the evaluator cannot hang.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import skillcore as S

TEXT_EXTS = {".tex", ".latex", ".bib", ".cls", ".sty", ".bst", ".cfg", ".def"}
BINARY_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg"}
TEX_EXTS = TEXT_EXTS | BINARY_EXTS
SKIP_PARTS = {"output", "work", ".ocr", "__pycache__", ".git", "build",
              "node_modules", ".pytest_cache"}
SKIP_NAMES = {"case.json", "task.md", "environment/resources.yaml",
              "resources.yaml"}
TRANSIENT_SUFFIXES = {".aux", ".log", ".out", ".toc", ".bbl", ".blg", ".fls",
                      ".fdb_latexmk", ".nav", ".snm", ".vrb", ".bcf", ".run.xml"}
GRAPHICS_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")

# A single case gets at most 180 s of real compilation.  The official budget is
# 900 s for the whole run, so this leaves room for the model and for the two
# finalize passes.
COMPILE_TOTAL_BUDGET = 180.0
PER_PASS_TIMEOUT = 75
PASSES_PER_ENGINE = 2
ENGINE_ORDER = ("pdflatex", "xelatex", "lualatex")


def _which(name):
    return shutil.which(name)


def _norm_rel(rel):
    return str(rel).replace("\\", "/")


def _copy_project(ctx, target):
    target = Path(target)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    copied = []
    for row in ctx.inv:
        rel = _norm_rel(row.get("path") or "")
        if not rel:
            continue
        path = Path(rel)
        if path.name in SKIP_NAMES:
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        suffix = path.suffix.lower()
        if suffix in TRANSIENT_SUFFIXES or path.name.endswith(".synctex.gz"):
            continue
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


def _main_candidates(project):
    """Rank .tex files that contain ``\\documentclass``.

    Root-level conventional names win; otherwise the shallowest path wins.
    The ranking is deterministic so repeated runs pick the same main file.
    """
    ranked = []
    for path in sorted(Path(project).rglob("*.tex")):
        text = S.read_text(path)
        if "\\documentclass" not in text:
            continue
        rel = path.relative_to(project)
        depth = len(rel.parts) - 1
        name = path.name.lower()
        priority = 0 if name in ("main.tex", "paper.tex", "manuscript.tex",
                                 "article.tex", "root.tex") else 1
        ranked.append((priority, depth, rel.as_posix(), path))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return ranked


def _main_tex(project):
    ranked = _main_candidates(project)
    if ranked:
        return ranked[0][3]
    candidates = sorted(Path(project).rglob("*.tex"))
    return candidates[0] if candidates else None


def _read_project_tex(project):
    pieces = []
    for path in sorted(Path(project).rglob("*.tex")):
        text = S.read_text(path)
        if text:
            pieces.append(text)
    return "\n\n".join(pieces)


def _engine_hint(ctx, project, main, main_text):
    """Choose the most plausible engine from the actual project.

    ``case.json`` wins when it states an engine explicitly.  Otherwise the
    document class/packages decide: ctex/xeCJK/fontspec/polyglossia need
    XeLaTeX, luatexja/directlua need LuaLaTeX, and CJK text without an explicit
    package should not be sent to pdfLaTeX.
    """
    explicit = ""
    for key in ("engine", "tex_engine", "latex_engine", "compiler"):
        value = ctx.case.get(key) if isinstance(ctx.case, dict) else None
        if isinstance(value, str) and value.strip():
            explicit = value.strip().lower()
            break
    explicit_match = re.search(r"(latexmk|pdflatex|xelatex|lualatex)", explicit)
    if explicit_match:
        name = explicit_match.group(1)
        if name == "latexmk":
            name = "pdflatex"
        return {"preferred": name, "reason": "case.json engine=%s" % explicit,
                "source": "case"}

    project_text = _read_project_tex(project)
    combined = (main_text or "") + "\n" + project_text
    lowered = combined.lower()
    docclass = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^{}]+)\}", combined)
    class_name = (docclass.group(1).strip().lower() if docclass else "")

    if class_name in ("ctexart", "ctexbook", "ctexrep", "ctexbeamer") or \
            re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\b(?:ctex|xeCJK)\b[^}]*\}",
                      combined) or "\\setmainfont" in combined or \
            re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*\b(?:fontspec|unicode-math|polyglossia)\b[^}]*\}",
                      combined):
        return {"preferred": "xelatex",
                "reason": "project uses ctex/xeCJK/fontspec/polyglossia",
                "source": "project"}
    if "luatexja" in lowered or "\\directlua" in lowered or \
            re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*luacode", combined):
        return {"preferred": "lualatex",
                "reason": "project uses luatexja/luacode/directlua",
                "source": "project"}
    if S.cjk_count(combined) >= 20 and "CJKutf8" not in combined:
        return {"preferred": "xelatex",
                "reason": "CJK source; XeLaTeX avoids pdfLaTeX encoding failures",
                "source": "heuristic"}
    return {"preferred": "pdflatex", "reason": "default pdfLaTeX project",
            "source": "default"}


def _clean_transients(project):
    for path in list(Path(project).rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in TRANSIENT_SUFFIXES or path.name.endswith(".synctex.gz"):
            try:
                path.unlink()
            except OSError:
                pass


def _pdf_header_ok(path):
    try:
        with Path(path).open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def _latexmk_flag(preferred):
    if preferred == "xelatex":
        return "-xelatex"
    if preferred == "lualatex":
        return "-lualatex"
    return "-pdf"


def _command_for(tool, preferred, main_arg):
    common = ["-interaction=nonstopmode", "-halt-on-error", "-file-line-error",
              "-no-shell-escape"]
    if tool == "latexmk":
        return ["latexmk", _latexmk_flag(preferred)] + common + \
               ["-maxrepeat=4", main_arg]
    return [tool] + common + [main_arg]


def _parse_passes(log):
    matches = [int(x) for x in re.findall(r"Run number\s+(\d+)", log or "")]
    return max(matches) + 1 if matches else 1


def _compile(project, main, preferred="pdflatex"):
    """Run a real TeX process, or report honestly that none is available.

    At most two plans are tried and the whole operation is bounded by
    ``COMPILE_TOTAL_BUDGET`` seconds.  A stale ``main.pdf`` is removed first so
    an input PDF can never masquerade as a fresh build.
    """
    project = Path(project)
    main = Path(main)
    try:
        main_arg = str(main.relative_to(project))
    except ValueError:
        main_arg = main.name
    pdf_path = main.with_suffix(".pdf")

    available_tools = [name for name in ("latexmk",) + ENGINE_ORDER if _which(name)]
    if not available_tools:
        return {"available": False, "ok": False, "engine": None, "tool": None,
                "error": "no TeX engine found (latexmk/pdflatex/xelatex/lualatex)",
                "log": "", "full_log": "", "pdf": None, "passes": 0,
                "command": None, "elapsed_s": 0.0,
                "preferred": preferred, "plans": []}

    # A stale PDF must never be mistaken for a fresh build.  Only remove it
    # once we know an engine is actually available.
    if pdf_path.exists():
        try:
            pdf_path.unlink()
        except OSError:
            pass

    direct = preferred if preferred in available_tools else (
        next((name for name in ENGINE_ORDER if name in available_tools), None))
    plans = []
    if "latexmk" in available_tools:
        plans.append(("latexmk", preferred))
    if direct and ("latexmk", preferred) not in plans:
        plans.append((direct, direct))
    plans = plans[:2]

    started = time.monotonic()
    logs = []
    last = None
    for tool, backend in plans:
        last = {"available": True, "ok": False, "engine": backend, "tool": tool,
                "error": "", "log": "", "full_log": "", "pdf": None,
                "passes": 0, "command": _command_for(tool, backend, main_arg),
                "elapsed_s": 0.0, "preferred": preferred,
                "plans": [{"tool": t, "engine": e} for t, e in plans]}
        command = last["command"]
        repeated = 1 if tool == "latexmk" else PASSES_PER_ENGINE
        for pass_index in range(repeated):
            remaining = COMPILE_TOTAL_BUDGET - (time.monotonic() - started)
            if remaining <= 5:
                last["error"] = "compile budget exhausted after %.1fs" % (
                    time.monotonic() - started)
                break
            timeout = max(5, min(PER_PASS_TIMEOUT, int(remaining)))
            pass_started = time.monotonic()
            try:
                proc = subprocess.run(command, cwd=str(project),
                                      capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                logs.append("TIMEOUT after %ss" % timeout)
                last["error"] = "timeout after %ss" % timeout
                last["passes"] = pass_index + 1
                break
            except OSError as exc:
                logs.append("OSError: %s" % exc)
                last["error"] = str(exc)
                break
            log = (proc.stdout + proc.stderr).decode("utf-8", "replace")
            logs.append(log)
            last["passes"] = (_parse_passes(log) if tool == "latexmk"
                              else pass_index + 1)
            last["elapsed_s"] = round(time.monotonic() - started, 2)
            header_ok = pdf_path.is_file() and _pdf_header_ok(pdf_path)
            last["ok"] = proc.returncode == 0 and header_ok
            last["error"] = ("" if last["ok"] else (
                "exit %d" % proc.returncode if proc.returncode != 0
                else "exit 0 but no valid PDF"))
            last["pdf"] = str(pdf_path) if last["ok"] else None
            if proc.returncode != 0:
                break
        last["full_log"] = "\n".join(logs)[-60000:]
        last["log"] = last["full_log"][-20000:]
        last["elapsed_s"] = round(time.monotonic() - started, 2)
        if last["ok"]:
            break
        # A LaTeX error is a real document problem; retrying another engine
        # only helps when the executable/format itself was unavailable.
        lowered = (last.get("full_log") or "").lower()
        recoverable = any(marker in lowered for marker in (
            "command not found", "not found", "cannot find", "can't find",
            "no such file", "unknown option", "format"))
        if not recoverable:
            break
    if last is None:
        return {"available": True, "ok": False, "engine": preferred, "tool": None,
                "error": "no compile attempt", "log": "", "full_log": "",
                "pdf": None, "passes": 0, "command": None,
                "elapsed_s": round(time.monotonic() - started, 2),
                "preferred": preferred, "plans": []}
    last["full_log"] = "\n".join(logs)[-60000:]
    last["log"] = last["full_log"][-20000:]
    return last


def _log_issues(log):
    errors, warnings = [], []
    file_line = re.compile(r"^[^\s:][^:]*\.(?:tex|latex|sty|cls|bib|bst|def|cfg)"
                           r":\d+:\s*")
    for line in (log or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("!") or "Undefined control sequence" in stripped \
                or stripped.startswith("Emergency stop") \
                or stripped.startswith("Fatal error") \
                or file_line.match(stripped):
            errors.append(stripped[:300])
        elif re.search(r"LaTeX Warning:|Package .* Warning:|Overfull \\hbox|"
                       r"Underfull \\hbox|undefined", stripped, re.I):
            warnings.append(stripped[:300])
    return list(dict.fromkeys(errors))[:100], list(dict.fromkeys(warnings))[:200]


def _resolve_tex_input(project, main, ref):
    ref = (ref or "").strip()
    if not ref or "\\" in ref:
        return None
    candidates = []
    for base in (Path(main).parent, Path(project)):
        path = base / ref
        candidates.append(path)
        if path.suffix == "":
            candidates.append(path.with_suffix(".tex"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _resolve_graphic(project, main, ref):
    ref = (ref or "").strip()
    if not ref or "://" in ref:
        return None
    candidates = []
    for base in (Path(main).parent, Path(project)):
        path = base / ref
        candidates.append(path)
        if path.suffix == "":
            for suffix in GRAPHICS_EXTS:
                candidates.append(path.with_suffix(suffix))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _resolve_bib(project, main, ref):
    ref = (ref or "").strip()
    if not ref:
        return None
    candidates = []
    for base in (Path(main).parent, Path(project)):
        path = base / ref
        candidates.append(path)
        if path.suffix == "":
            candidates.append(path.with_suffix(".bib"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _kpsewhich(names):
    """Return the subset of names found by kpsewhich, or None if unavailable."""
    tool = _which("kpsewhich")
    if not tool or not names:
        return None
    try:
        proc = subprocess.run([tool] + list(names), capture_output=True,
                              timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 and not proc.stdout.strip():
        return None
    found = set()
    output = proc.stdout.decode("utf-8", "replace")
    for name in names:
        stem = Path(name).stem
        if re.search(r"(?:^|[\\/])%s\.[a-z]+$" % re.escape(stem), output,
                     re.M | re.I):
            found.add(name)
    return found


def _static_checks(project, main):
    text = S.read_text(main)
    tex_paths = sorted(Path(project).rglob("*.tex"))
    project_text = "\n\n".join(S.read_text(p) for p in tex_paths)
    bib_text = "\n\n".join(S.read_text(p) for p in Path(project).rglob("*.bib"))
    results = {
        "documentclass": bool(re.search(r"\\documentclass(?:\[[^\]]*\])?\{", text)),
        "begin_document": "\\begin{document}" in text,
        "end_document": "\\end{document}" in text,
        "balanced_braces": text.count("{") == text.count("}"),
        "citations": sorted(set(re.findall(
            r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])?\{([^{}]+)\}", project_text))),
        "labels": sorted(set(re.findall(r"\\label\{([^{}]+)\}", project_text))),
        "refs": sorted(set(re.findall(
            r"\\(?:ref|autoref|eqref|pageref)\{([^{}]+)\}", project_text))),
        "bib_keys": sorted(set(re.findall(r"@\w+\s*\{\s*([^,\s]+)", bib_text))),
    }
    results["missing_refs"] = [x for x in results["refs"]
                               if x not in results["labels"]]
    results["missing_citations"] = [x for x in results["citations"]
                                    if x not in results["bib_keys"]]

    inputs = []
    for match in re.finditer(r"\\(?:input|include|subfile)\{([^{}]+)\}",
                             project_text):
        for ref in match.group(1).split(","):
            ref = ref.strip()
            if ref and ref not in inputs:
                inputs.append(ref)
    results["inputs"] = inputs
    results["missing_inputs"] = [ref for ref in inputs
                                 if _resolve_tex_input(project, main, ref) is None]

    graphics = []
    for match in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}",
                             project_text):
        ref = match.group(1).strip()
        if ref and ref not in graphics:
            graphics.append(ref)
    results["graphics"] = graphics
    results["missing_graphics"] = [ref for ref in graphics
                                   if _resolve_graphic(project, main, ref) is None]

    bib_refs = []
    for pattern in (r"\\bibliography\{([^{}]+)\}",
                    r"\\addbibresource(?:\[[^\]]*\])?\{([^{}]+)\}"):
        for match in re.finditer(pattern, project_text):
            for ref in match.group(1).split(","):
                ref = ref.strip()
                if ref and ref not in bib_refs:
                    bib_refs.append(ref)
    results["bib_files"] = bib_refs
    results["missing_bib"] = [ref for ref in bib_refs
                              if _resolve_bib(project, main, ref) is None]

    docclass = re.search(r"\\documentclass(?:\[[^\]]*\])?\{([^{}]+)\}", text)
    results["documentclass_name"] = docclass.group(1).strip() if docclass else ""
    class_file = results["documentclass_name"] + ".cls" \
        if results["documentclass_name"] else ""
    class_local = bool(class_file) and any(
        p.name.lower() == class_file.lower() for p in Path(project).rglob("*.cls"))
    if class_local:
        results["class_available"] = True
        results["class_source"] = "project"
    elif class_file:
        found = _kpsewhich([class_file])
        if found is None:
            results["class_available"] = None
            results["class_source"] = "unknown"
        else:
            results["class_available"] = class_file in found
            results["class_source"] = "texmf" if class_file in found else "missing"
    else:
        results["class_available"] = None
        results["class_source"] = "unknown"

    packages = []
    for match in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^{}]+)\}",
                             project_text):
        for name in match.group(1).split(","):
            name = name.strip()
            if name and name not in packages:
                packages.append(name)
    results["packages"] = packages
    local_packages = {p.stem.lower() for p in Path(project).rglob("*.sty")}
    probe = [name + ".sty" for name in packages
             if name.lower() not in local_packages]
    found = _kpsewhich(probe) if probe else set()
    if found is None:
        results["missing_packages"] = []
        results["package_check"] = "unavailable"
    else:
        results["missing_packages"] = [name + ".sty" for name in packages
                                       if name.lower() not in local_packages
                                       and (name + ".sty") not in found]
        results["package_check"] = "kpsewhich"

    results["ok"] = (results["documentclass"] and results["begin_document"]
                     and results["end_document"] and results["balanced_braces"])
    results["references_ok"] = (not results["missing_refs"]
                                and not results["missing_citations"])
    results["assets_ok"] = not (results["missing_inputs"]
                                or results["missing_graphics"]
                                or results["missing_bib"])
    return results


def _write_plan(ctx, project, main, plan, static):
    payload = {
        "case_id": ctx.case_id,
        "main": str(Path(main).relative_to(project)),
        "preferred_engine": plan.get("preferred"),
        "reason": plan.get("reason"),
        "source": plan.get("source"),
        "engine_order": [name for name in ("latexmk",) + ENGINE_ORDER
                         if _which(name)],
        "total_budget_s": COMPILE_TOTAL_BUDGET,
        "per_pass_timeout_s": PER_PASS_TIMEOUT,
        "commands": {
            "latexmk": "%s %s -interaction=nonstopmode -halt-on-error "
                       "-file-line-error -no-shell-escape -maxrepeat=4 <main>"
                       % ("latexmk", _latexmk_flag(plan.get("preferred", "pdflatex"))),
            "direct": "<engine> -interaction=nonstopmode -halt-on-error "
                      "-file-line-error -no-shell-escape <main>",
        },
        "static_assets": {
            "missing_inputs": static.get("missing_inputs"),
            "missing_graphics": static.get("missing_graphics"),
            "missing_bib": static.get("missing_bib"),
            "missing_packages": static.get("missing_packages"),
            "class_available": static.get("class_available"),
        },
    }
    S.write_json(ctx.work / "latex-compile-plan.json", payload)
    return payload


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
    ranked = _main_candidates(project)
    main = ranked[0][3] if ranked else _main_tex(project)
    if main is None:
        ctx.result.change("prepare", "input", "no main .tex")
        ctx.result.uncertainty("latex", "no .tex file found",
                               "cannot compile", "provide main.tex")
        return ctx.result.finalize("input_error")

    # A PDF shipped with the input is a reference, not a build product.
    reference_pdf = None
    stale_pdf = Path(main).with_suffix(".pdf")
    if stale_pdf.is_file():
        reference_pdf = ctx.work / "reference.pdf"
        try:
            shutil.move(str(stale_pdf), str(reference_pdf))
        except OSError:
            reference_pdf = None
        for tree in (project, baseline):
            try:
                (Path(tree) / stale_pdf.relative_to(project)).unlink()
            except (OSError, ValueError):
                pass
    _clean_transients(project)
    _clean_transients(baseline)

    main_text = S.read_text(main)
    plan = _engine_hint(ctx, project, main, main_text)
    static = _static_checks(project, main)
    compile_result = _compile(project, main, preferred=plan.get("preferred",
                                                                 "pdflatex"))
    errors, warnings = _log_issues(compile_result.get("full_log")
                                   or compile_result.get("log") or "")
    S.write_json(ctx.work / "latex-project.json", {
        "case_id": ctx.case_id,
        "main": str(Path(main).relative_to(project)),
        "main_candidates": [item[2] for item in ranked],
        "files": copied,
        "static": static,
        "engine": compile_result.get("engine"),
        "engine_preferred": plan.get("preferred"),
        "engine_reason": plan.get("reason"),
        "pdf": compile_result.get("pdf"),
        "reference_pdf": S.relpath(reference_pdf, ctx.work) if reference_pdf else None,
    })
    S.write_json(ctx.work / "compile-baseline.json", {
        "case_id": ctx.case_id,
        "available": compile_result.get("available"),
        "ok": compile_result.get("ok"),
        "engine": compile_result.get("engine"),
        "engine_preferred": plan.get("preferred"),
        "tool": compile_result.get("tool"),
        "error": compile_result.get("error"),
        "passes": compile_result.get("passes"),
        "pdf": compile_result.get("pdf"),
        "errors": errors,
        "warnings": warnings[:60],
        "command": compile_result.get("command"),
        "elapsed_s": compile_result.get("elapsed_s"),
        "budget_s": COMPILE_TOTAL_BUDGET,
    })
    S.write_json(ctx.work / "error-list.json", {
        "case_id": ctx.case_id,
        "errors": errors,
        "warnings": warnings,
        "static": static,
    })
    S.write_json(ctx.work / "latex-edits-template.json", {
        "case_id": ctx.case_id,
        "main": str(Path(main).relative_to(project)),
        "edits": [{"id": "L001", "file": str(Path(main).relative_to(project)),
                   "before": "verbatim source text", "after": "fixed source text",
                   "reason": "error or template requirement", "error_id": ""}],
        "template_requirements": [],
    })
    _write_plan(ctx, project, main, plan, static)
    ctx.result.change("inventory", "latex", "%d project files; main=%s" % (
        len(copied), Path(main).relative_to(project)))
    if ranked and len(ranked) > 1:
        ctx.result.uncertainty(
            "main_candidates", ", ".join(item[2] for item in ranked),
            "multiple files contain \\documentclass",
            "confirm work/latex-project.json main before finalize")
    if reference_pdf:
        ctx.result.change("inventory", "reference.pdf",
                          "input PDF moved aside; it is not a build product")
    ctx.result.change("compile", "baseline", "preferred=%s engine=%s ok=%s" % (
        plan.get("preferred"), compile_result.get("engine"),
        compile_result.get("ok")))
    return ctx.provisional(
        reason="LaTeX project copied; edit work/project or write "
               "latex-edits.json, then run finalize")


def _apply_edits(project, edits):
    applied, blocked = [], []
    for edit in edits or []:
        if not isinstance(edit, dict):
            blocked.append({"edit": edit,
                            "reason": "edit is not a JSON object"})
            continue
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
        count = text.count(before)
        if count != 1:
            blocked.append({"edit": edit,
                            "reason": "before occurs %d times" % count})
            continue
        index = text.find(before)
        line = text[:index].count("\n") + 1
        S.write_text(path, text.replace(before, after, 1))
        applied.append({"edit": edit, "file": rel, "line": line,
                        "sha256_before": S.sha256_text(text),
                        "sha256_after": S.sha256_text(
                            text.replace(before, after, 1))})
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
        if suffix in BINARY_EXTS:
            changes.append({"file": rel, "changed": True, "diff": "",
                            "binary": True, "sha256_before": sha_before,
                            "sha256_after": sha_after})
            continue
        new_text = S.read_text(path)
        old_text = S.read_text(old_path)
        diff = list(difflib.unified_diff(old_text.splitlines(),
                                         new_text.splitlines(),
                                         fromfile=rel, tofile=rel,
                                         lineterm=""))
        changes.append({"file": rel, "changed": True,
                        "diff": "\n".join(diff[:400]),
                        "sha256_before": sha_before,
                        "sha256_after": sha_after})
    return changes


def _preserved_from_edits(applied, uncertainties):
    preserved = []
    for item in applied:
        edit = item.get("edit") or {}
        before = str(edit.get("before") or "")
        after = str(edit.get("after") or "")
        before_numbers = {token["value"]: token
                          for token in S.number_tokens(before)}
        after_numbers = {token["value"]: token for token in S.number_tokens(after)}
        for value in sorted(set(before_numbers) & set(after_numbers)):
            preserved.append({
                "kind": "number", "source": before_numbers[value]["raw"],
                "target": after_numbers[value]["raw"],
                "locator": "%s line %s" % (item.get("file"), item.get("line")),
                "value": value, "unit": "",
            })
        removed = sorted(set(before_numbers) - set(after_numbers))
        if removed:
            uncertainties.append({
                "item": "fact_removed", "reason": ", ".join(removed),
                "impact": "a number present before the edit is absent after it",
                "action": "confirm this is an intended correction, not data loss",
            })
        before_cites = {token["raw"]: token for token in S.citation_tokens(before)}
        after_cites = {token["raw"]: token for token in S.citation_tokens(after)}
        for raw in sorted(set(before_cites) & set(after_cites)):
            preserved.append({
                "kind": "citation", "source": raw, "target": raw,
                "locator": "%s line %s" % (item.get("file"), item.get("line")),
                "value": raw, "unit": "",
            })
    return preserved[:300]


def _gate_checks(report, out_project, pdf_path, static, applied, blocked,
                 project, main_rel):
    transient = []
    for path in Path(out_project).rglob("*"):
        if path.is_file() and (path.suffix.lower() in TRANSIENT_SUFFIXES
                               or path.name.endswith(".synctex.gz")):
            transient.append(path.name)
    checks = [
        {"id": "G1", "check": "project tree exists and is editable",
         "ok": out_project.is_dir() and (out_project / main_rel).is_file()
               and not transient,
         "evidence": {"path": "project", "main": main_rel,
                      "transient_files": sorted(set(transient))}},
        {"id": "G2", "check": "edits are controlled and traceable",
         "ok": not blocked,
         "evidence": {"applied": len(applied), "blocked": len(blocked)}},
        {"id": "G3", "check": "static structure and references are consistent",
         "ok": bool(static.get("ok")) and bool(static.get("references_ok"))
               and bool(static.get("assets_ok"))
               and static.get("class_available") is not False,
         "evidence": {"missing_refs": static.get("missing_refs"),
                      "missing_citations": static.get("missing_citations"),
                      "missing_inputs": static.get("missing_inputs"),
                      "missing_graphics": static.get("missing_graphics"),
                      "missing_bib": static.get("missing_bib"),
                      "missing_packages": static.get("missing_packages"),
                      "class_available": static.get("class_available")}},
        {"id": "G4", "check": "compile result is real, never fabricated",
         "ok": bool(report.get("available")) and bool(report.get("ok"))
               and bool(pdf_path) and _pdf_header_ok(pdf_path),
         "evidence": {"available": report.get("available"),
                      "ok": report.get("ok"),
                      "engine": report.get("engine"),
                      "pdf": report.get("pdf")}},
        {"id": "G5", "check": "errors are locatable to file and line",
         "ok": (not report.get("errors")) or any(
             re.search(r"\.(?:tex|sty|cls|bib):\d+", error)
             for error in (report.get("errors") or []))
               or not report.get("available"),
         "evidence": {"errors": (report.get("errors") or [])[:10]}},
        {"id": "G6", "check": "build is reproducible",
         "ok": bool(report.get("engine") or not report.get("available"))
               and report.get("command") is not None
               and report.get("passes") is not None,
         "evidence": {"engine": report.get("engine"),
                      "passes": report.get("passes"),
                      "command": report.get("command"),
                      "budget_s": report.get("compile_budget_s")}},
        {"id": "G7", "check": "output contract paths are real",
         "ok": out_project.is_dir() and all(
             Path(out_project / item["file"]).is_file()
             for item in (report.get("file_changes") or [])
             if item.get("file")),
         "evidence": {"files": len(report.get("file_changes") or [])}},
    ]
    return checks


def finalize(ctx):
    project = ctx.work / "project"
    baseline = ctx.work / "project-baseline"
    meta = S.load_json(ctx.work / "latex-project.json", {})
    if not project.is_dir() or not meta.get("main"):
        ctx.result.change("finalize", "latex", "project workspace missing")
        ctx.result.uncertainty("latex", "no prepared LaTeX project",
                               "cannot finalize", "run prepare first")
        return ctx.result.finalize("input_error")
    edits_path = ctx.work / "latex-edits.json"
    edits_error = None
    if edits_path.is_file():
        try:
            edits_payload = json.loads(S.read_text(edits_path))
        except json.JSONDecodeError:
            edits_error = "work/latex-edits.json is not valid JSON"
            edits_payload = {}
        if not isinstance(edits_payload, dict):
            edits_error = "work/latex-edits.json is not a JSON object"
            edits_payload = {}
        edits = edits_payload.get("edits")
        if edits is not None and not isinstance(edits, list):
            edits_error = "work/latex-edits.json 'edits' is not a list"
            edits = []
    else:
        edits_payload, edits = {}, []
    applied, blocked = _apply_edits(project, edits or [])
    if edits_error:
        blocked.append({"edit": {"id": "latex-edits.json"},
                        "reason": edits_error})
    changes = _file_changes(baseline, project)
    main = project / meta["main"]
    if not main.is_file():
        candidates = _main_candidates(project)
        if candidates:
            main = candidates[0][3]
            meta["main"] = main.relative_to(project).as_posix()
        else:
            ctx.result.change("finalize", "latex", "main source disappeared")
            ctx.result.uncertainty("latex", "main file not found",
                                   "cannot compile", "restore the main .tex")
            return ctx.result.finalize("input_error")

    plan = S.load_json(ctx.work / "latex-compile-plan.json", {})
    preferred = plan.get("preferred_engine") or meta.get("engine_preferred") \
        or "pdflatex"
    _clean_transients(project)
    static = _static_checks(project, main)
    compile_result = _compile(project, main, preferred=preferred)
    errors, warnings = _log_issues(compile_result.get("full_log")
                                   or compile_result.get("log") or "")
    pdf_path = None
    pdf_sha256 = None
    pdf_bytes = 0
    if compile_result.get("ok") and compile_result.get("pdf"):
        source_pdf = Path(compile_result["pdf"])
        if source_pdf.is_file() and _pdf_header_ok(source_pdf):
            pdf_path = ctx.output / "main.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_pdf, pdf_path)
            pdf_sha256 = S.sha256_path(pdf_path)
            pdf_bytes = pdf_path.stat().st_size
        else:
            pdf_path = None
    out_project = ctx.output / "project"
    if out_project.exists():
        shutil.rmtree(out_project)
    shutil.copytree(project, out_project)
    for junk in list(out_project.rglob("*")):
        if junk.is_file() and (junk.suffix.lower() in TRANSIENT_SUFFIXES
                               or junk.name.endswith(".synctex.gz")):
            try:
                junk.unlink()
            except OSError:
                pass

    uncertainties = []
    exceptions = []

    def add_exception(code, detail, impact, fallback):
        item = {"code": code, "detail": detail, "impact": impact,
                "fallback": fallback}
        exceptions.append(item)
        uncertainties.append({"item": code, "reason": detail,
                              "impact": impact, "action": fallback})

    if not compile_result.get("available"):
        add_exception("compile_unavailable",
                      compile_result.get("error", ""),
                      "no PDF could be produced",
                      "run in an image with TeX Live; the editable source tree "
                      "and static audit remain valid")
    elif not compile_result.get("ok"):
        add_exception("compile_failed", compile_result.get("error", ""),
                      "source does not compile",
                      "fix the first error in error-analysis.md and rerun")
    if blocked:
        add_exception("blocked_edits",
                      "%d edits could not be applied" % len(blocked),
                      "requested fixes are absent", "fix before anchors")
    for key, code, impact, fallback in (
            ("missing_refs", "missing_refs", "unresolved cross-references",
             "add labels or fix refs"),
            ("missing_citations", "missing_citations",
             "citations have no bibliography entries",
             "add real bib entries or report the gap"),
            ("missing_inputs", "missing_inputs", "included files are absent",
             "copy the missing files from the input project"),
            ("missing_graphics", "missing_graphics", "graphics are absent",
             "copy the missing figures from the input project"),
            ("missing_bib", "missing_bib", "bibliography files are absent",
             "copy the .bib file from the input project"),
            ("missing_packages", "missing_packages",
             "packages are not installed in this TeX distribution",
             "install the package or use an available equivalent")):
        value = static.get(key) or []
        if value:
            add_exception(code, json.dumps(value, ensure_ascii=False),
                          impact, fallback)
    if static.get("class_available") is False:
        add_exception("missing_class",
                      static.get("documentclass_name", "") + ".cls",
                      "the document class is unavailable",
                      "copy the class file from the input project; do not "
                      "fabricate or download one")
    if compile_result.get("ok") and not pdf_path:
        add_exception("invalid_pdf", "engine exited 0 but no %PDF- file",
                      "no PDF artifact can be trusted",
                      "inspect the compile log and rerun")

    preserved = _preserved_from_edits(applied, uncertainties)
    report = {
        "case_id": ctx.case_id, "main": meta["main"],
        "available": compile_result.get("available"),
        "ok": compile_result.get("ok"),
        "engine": compile_result.get("engine"),
        "engine_preferred": preferred,
        "engine_reason": plan.get("reason") or meta.get("engine_reason"),
        "tool": compile_result.get("tool"),
        "error": compile_result.get("error"),
        "passes": compile_result.get("passes"),
        "errors": errors, "warnings": warnings, "static": static,
        "edits_applied": len(applied), "edits_blocked": len(blocked),
        "file_changes": changes,
        "pdf": "main.pdf" if pdf_path else None,
        "pdf_sha256": pdf_sha256, "pdf_bytes": pdf_bytes,
        "command": compile_result.get("command"),
        "elapsed_s": compile_result.get("elapsed_s"),
        "compile_budget_s": COMPILE_TOTAL_BUDGET,
        "reference_pdf": meta.get("reference_pdf"),
        "exceptions": exceptions,
    }
    S.write_json(ctx.output / "compile-report.json", report)
    S.write_json(ctx.output / "change-record.json", {
        "case_id": ctx.case_id, "applied": applied, "blocked": blocked,
        "file_changes": changes,
    })
    error_lines = ["# Compile and static analysis", "",
                   "- Main: %s" % meta["main"],
                   "- Preferred engine: %s (%s)" % (
                       preferred, plan.get("reason") or meta.get("engine_reason")),
                   "- Engine used: %s" % (compile_result.get("engine") or "unavailable"),
                   "- Tool: %s" % (compile_result.get("tool") or "unavailable"),
                   "- Compile ok: %s" % compile_result.get("ok"),
                   "- Passes: %s" % compile_result.get("passes"),
                   "- Elapsed: %ss of %ss budget" % (
                       compile_result.get("elapsed_s"), COMPILE_TOTAL_BUDGET),
                   "- Command: %s" % (compile_result.get("command") or "not run"),
                   "- Static checks ok: %s" % static.get("ok"), "",
                   "## Missing assets", ""]
    for key in ("missing_inputs", "missing_graphics", "missing_bib",
                "missing_packages"):
        values = static.get(key) or []
        error_lines.append("- %s: %s" % (key, ", ".join(map(str, values)) or "none"))
    error_lines += ["- class_available: %s" % static.get("class_available"), ""]
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
                or not static.get("references_ok")
                or not static.get("assets_ok")
                or static.get("class_available") is False
                or not compile_result.get("ok") or not pdf_path)
    status = "completed" if not critical else "partial"

    evidence = []
    for change in changes:
        evidence.append({"claim_id": change["file"], "claim": "source edited",
                         "source": {"path": change["file"], "locator": "diff"},
                         "quote": change.get("diff", "")[:500],
                         "supports": "change"})
    content_map = []
    for change in changes:
        content_map.append({
            "id": change["file"], "kind": "source_change",
            "label": change["file"],
            "source": {"path": change["file"], "locator": "diff"},
            "rendered": Path(out_project / change["file"]).is_file(),
            "media": "tex", "diff": change.get("diff", "")[:2000],
        })
    compliance = _gate_checks(report, out_project, pdf_path, static, applied,
                              blocked, project, meta["main"])
    editable_source = {
        "path": S.relpath(out_project, ctx.output), "format": "directory",
        "main": meta["main"],
        "paths": sorted(p.relative_to(out_project).as_posix()
                        for p in out_project.rglob("*") if p.is_file())[:200],
    }
    export_artifact = {
        "path": S.relpath(pdf_path, ctx.output) if pdf_path else None,
        "format": "pdf", "valid_header": bool(pdf_path),
        "bytes": pdf_bytes, "sha256": pdf_sha256, "png": None,
    }

    ctx.result.add_artifact(out_project, "editable_source", "directory",
                            "complete LaTeX source tree")
    ctx.result.add_artifact(ctx.output / "compile-report.json", "audit", "json",
                            "compile, static checks and changes")
    ctx.result.add_artifact(ctx.output / "change-record.json", "change_record",
                            "json", "applied edits and diffs")
    ctx.result.add_artifact(ctx.output / "error-analysis.md", "readable", "md",
                            "compile errors, warnings and missing references")
    if pdf_path:
        ctx.result.add_artifact(pdf_path, "export_artifact", "pdf",
                                "compiled PDF")
    ctx.result.change("edit", "latex", "%d edits applied, %d blocked" % (
        len(applied), len(blocked)))
    ctx.result.change("compile", "latex", "preferred=%s engine=%s ok=%s" % (
        preferred, compile_result.get("engine"), compile_result.get("ok")))
    return ctx.result.finalize(
        status,
        deliverable=out_project,
        result={
            "summary": "LaTeX project %s; preferred=%s; engine=%s; compile ok=%s; "
                       "%d errors, %d warnings; %d/%d edits applied." % (
                           "compiled" if pdf_path else "not compiled",
                           preferred, compile_result.get("engine"),
                           compile_result.get("ok"), len(errors), len(warnings),
                           len(applied), len(applied) + len(blocked)),
            "main": meta["main"],
            "engine": compile_result.get("engine"),
            "engine_preferred": preferred,
            "compile_ok": compile_result.get("ok"),
            "pdf": "main.pdf" if pdf_path else None,
            "static": static,
            "report": report,
            "preservation": {
                "static_references_ok": static.get("references_ok"),
                "assets_ok": static.get("assets_ok"),
                "class_available": static.get("class_available"),
                "preserved_facts": len(preserved),
            },
        },
        claims=[
            {"claim_id": "compile", "text": "compile ok=%s (engine=%s)" % (
                compile_result.get("ok"), compile_result.get("engine"))},
            {"claim_id": "static", "text": "static checks ok=%s; missing_refs=%d; "
                                           "missing_citations=%d" % (
                static.get("ok"), len(static.get("missing_refs") or []),
                len(static.get("missing_citations") or []))},
        ],
        preserved_facts=preserved,
        evidence_map=evidence,
        extra={
            "editable_source": editable_source,
            "export_artifact": export_artifact,
            "content_map": content_map,
            "compliance_checks": compliance,
            "exceptions": exceptions,
            "uncertainties": uncertainties,
            "uncertainty": uncertainties,
        },
    )
