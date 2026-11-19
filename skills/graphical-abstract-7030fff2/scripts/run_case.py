#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entry point for the Graphical Abstract skill.

Flow:
    inspect /input -> resolve a GA spec (agent-written or derived from case
    fields/paper) -> svg_gen.py (editable SVG) -> export.py (real PDF/PNG) ->
    audit.py (XML/text/external refs/id coverage/export headers) ->
    result_graphics.py (official media result.json).

The agent should normally write /app/work/ga-spec.json first (schema in
templates/ga-spec.schema.json).  When it is absent, the pipeline derives a
minimal spec from real case fields/paper text, records that in uncertainty and
still produces a complete, honest result.

Exit codes:
    0 = result.json written (answered, partial or input_error alike)
    2 = could not start (sibling scripts missing or no writable output dir)

Usage:
    python3 run_case.py --input /input --out /app/output --work /app/work --json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAX_HASH_BYTES = 16 * 1024 * 1024
ROLE_ORDER = ("problem", "approach", "result", "conclusion", "implication")
ROLE_CUES = {
    "problem": ("problem", "challenge", "limitation", "bottleneck", "issue",
                "question", "poor", "low ", "问题", "挑战", "瓶颈", "局限", "下降"),
    "approach": ("method", "approach", "strategy", "using", "via", "synthes",
                 "design", "we ", "herein", "方法", "策略", "通过", "构建", "设计"),
    "result": ("result", "show", "increase", "improve", "achiev", "reach",
               "retain", "selectivity", "activity", "yield", "结果", "提高",
               "实现", "保持", "达到", "表明"),
    "conclusion": ("conclusion", "therefore", "suggest", "conclude",
                   "demonstrat", "结论", "因此", "综上"),
}
SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n{2,}")
NUMBER_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(%|°C|℃|h|min|nm|µm|μm|um|mM|µM|μM|fold|×|kV|V|mA)")


def load_sibling(name):
    for candidate in (HERE / name, HERE / (name + ".md")):
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location(
                "_ga_" + name.replace(".py", ""), candidate)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError("missing sibling script: %s" % name)


def digest(path):
    h = hashlib.sha256()
    limit = MAX_HASH_BYTES
    try:
        with path.open("rb") as fh:
            while limit > 0:
                block = fh.read(min(65536, limit))
                if not block:
                    break
                h.update(block)
                limit -= len(block)
    except OSError:
        return None
    return h.hexdigest()


def inventory(input_dir):
    rows = []
    if not input_dir.is_dir():
        return {"ok": False, "root": str(input_dir), "files": [], "total": 0}
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(input_dir).parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        rows.append({"relative": path.relative_to(input_dir).as_posix(),
                     "bytes": size, "sha256": digest(path)})
    return {"ok": True, "root": str(input_dir), "files": rows, "total": len(rows)}


def text_from_pdf(path):
    exe = shutil.which("pdftotext")
    if not exe:
        return ""
    try:
        proc = subprocess.run([exe, "-layout", str(path), "-"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def resolve_path(value, input_dir):
    if not value:
        return None
    raw = Path(str(value))
    for candidate in (raw, input_dir / raw, input_dir / raw.name):
        if candidate.is_file():
            return candidate
    return None


def as_rel(path, input_dir):
    try:
        return path.relative_to(input_dir).as_posix()
    except (ValueError, AttributeError):
        return str(path)


def resolve_paper(case, input_dir):
    """Return (paper_text, relative_path_or_None); reads real files only."""
    values = []
    paper = case.get("paper")
    if isinstance(paper, dict):
        text = paper.get("text") or paper.get("content")
        if text:
            return str(text), str(paper.get("path") or "case.json")
        values.append(paper.get("path"))
    elif isinstance(paper, str):
        values.append(paper)
    for key in ("paper_text", "manuscript", "source_text", "abstract_text"):
        if isinstance(case.get(key), str) and case[key].strip():
            return case[key], str(case.get("paper") or "case.json")
    for item in (case.get("materials") or case.get("sources") or []):
        if isinstance(item, dict):
            if item.get("text"):
                return str(item["text"]), str(item.get("path") or "case.json")
            values.append(item.get("path") or item.get("file"))
        elif isinstance(item, str):
            values.append(item)
    for value in values:
        path = resolve_path(value, input_dir)
        if not path:
            continue
        rel = as_rel(path, input_dir)
        if path.suffix.lower() == ".pdf":
            text = text_from_pdf(path)
            if text.strip():
                return text, rel
        else:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if text.strip():
                return text, rel
    preferred = ("paper.txt", "paper.md", "manuscript.txt", "manuscript.md",
                 "materials.txt", "source.txt")
    candidates = []
    for name in preferred:
        candidate = input_dir / name
        if candidate.is_file():
            candidates.append(candidate)
    if not candidates:
        candidates = [p for p in sorted(input_dir.rglob("*"))
                      if p.is_file() and p.suffix.lower() in (".txt", ".md")
                      and p.name.lower() not in ("task.md", "readme.md")]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            return text, as_rel(path, input_dir)
    return "", None


def sentences(text):
    return [s.strip() for s in SENTENCE_SPLIT.split(text or "") if len(s.strip()) > 25]


def first_string(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("claim", "text", "summary", "value", "finding", "sentence"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
    return None


def derive_spec(case, input_dir):
    """Derive a source-bound spec when the agent did not write ga-spec.json."""
    notes, uncertainty = [], []
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    paper_text, paper_rel = resolve_paper(case, input_dir)
    paper_sentences = sentences(paper_text)

    claim, claim_source_key, claim_support = "", None, []
    for key in ("claim", "take_home_message", "key_finding", "conclusion",
                "one_sentence", "summary", "abstract", "tldr"):
        found = first_string(case.get(key))
        if found:
            claim, claim_source_key = found, key
            value = case.get(key)
            if isinstance(value, dict):
                for item in value.get("support") or []:
                    if isinstance(item, str):
                        claim_support.append({"path": item, "locator": key})
                    elif isinstance(item, dict) and item.get("path"):
                        claim_support.append({"path": str(item["path"]),
                                              "locator": str(item.get("locator") or key)})
            break
    if not claim and paper_sentences:
        scored = [s for s in paper_sentences[:12] if NUMBER_RE.search(s)]
        claim = (scored[0] if scored else paper_sentences[0])[:260]
        claim_source_key = "paper"
    if len(claim) > 260:
        notes.append({"code": "claim_truncated",
                      "detail": "claim longer than 260 chars; kept the first 260",
                      "severity": "warning"})
        claim = claim[:257].rstrip() + "..."
    if claim:
        if not claim_support:
            claim_support = [{"path": paper_rel or "case.json",
                              "locator": "paper first claim sentence"
                              if claim_source_key == "paper" else claim_source_key}]
    else:
        notes.append({"code": "claim_missing",
                      "detail": "case.json/paper.txt expose no single-sentence claim",
                      "severity": "error"})
        uncertainty.append("claim could not be derived; agent should write "
                           "/app/work/ga-spec.json")

    elements = []
    raw_elements = case.get("elements") or case.get("key_elements")
    if isinstance(raw_elements, list) and raw_elements:
        for i, item in enumerate(raw_elements[:5]):
            if isinstance(item, str):
                item = {"concept": item}
            if not isinstance(item, dict):
                continue
            concept = str(item.get("concept") or item.get("label") or
                          item.get("title") or item.get("text") or "").strip()
            if not concept:
                continue
            source = item.get("source") or item.get("maps_to") or {}
            if isinstance(source, str):
                source = {"path": source, "locator": ""}
            if not isinstance(source, dict):
                source = {}
            elements.append({
                "id": str(item.get("id") or "G%d" % (i + 1)),
                "role": str(item.get("role") or ROLE_ORDER[i % len(ROLE_ORDER)]),
                "concept": concept,
                "kind": str(item.get("kind") or "reported"),
                "source": {"path": str(source.get("path") or ""),
                           "locator": str(source.get("locator") or "")},
            })
    if not elements and isinstance(case.get("key_figures"), list):
        for i, fig in enumerate(case["key_figures"][:5]):
            if isinstance(fig, str):
                fig = {"caption": fig}
            if not isinstance(fig, dict):
                continue
            concept = str(fig.get("caption") or fig.get("label") or
                          fig.get("title") or fig.get("description") or "").strip()
            if not concept:
                continue
            elements.append({
                "id": "G%d" % (i + 1),
                "role": ROLE_ORDER[i % len(ROLE_ORDER)],
                "concept": concept,
                "kind": "reported",
                "source": {"path": str(fig.get("path") or fig.get("file") or ""),
                           "locator": str(fig.get("panel") or fig.get("id") or "")},
            })
    if not elements and paper_sentences:
        used = set()
        for role in ("problem", "approach", "result"):
            cues = ROLE_CUES[role]
            for i, sentence in enumerate(paper_sentences[:20]):
                if i in used:
                    continue
                if any(cue in sentence.lower() for cue in cues):
                    used.add(i)
                    elements.append({
                        "id": "G%d" % (len(elements) + 1), "role": role,
                        "concept": sentence[:150], "kind": "reported",
                        "source": {"path": paper_rel or "case.json",
                                   "locator": "paper sentence %d" % (i + 1)}})
                    break
        for i, sentence in enumerate(paper_sentences[:20]):
            if len(elements) >= 3:
                break
            if i not in used:
                used.add(i)
                role = ("problem", "approach", "result")[len(elements) % 3]
                elements.append({
                    "id": "G%d" % (len(elements) + 1), "role": role,
                    "concept": sentence[:150], "kind": "reported",
                    "source": {"path": paper_rel or "case.json",
                               "locator": "paper sentence %d" % (i + 1)}})
    if not elements:
        notes.append({"code": "elements_missing",
                      "detail": "no verifiable element material in input",
                      "severity": "error"})
        uncertainty.append("No elements could be derived from real materials; "
                           "renderer placeholders must not be read as findings.")
        for i, role in enumerate(("problem", "approach", "result")):
            elements.append({"id": "G%d" % (i + 1), "role": role,
                             "concept": role.capitalize(), "kind": "schematic",
                             "source": None})
    if len(elements) < 3:
        notes.append({"code": "elements_insufficient",
                      "detail": "only %d source-bound elements; 3-5 expected"
                      % len(elements), "severity": "error"})

    journal = case.get("journal") if isinstance(case.get("journal"), dict) else {}
    journal = journal or (case.get("journal_spec")
                          if isinstance(case.get("journal_spec"), dict) else {})
    canvas_in = case.get("canvas") if isinstance(case.get("canvas"), dict) else {}
    width = journal.get("width_px") or canvas_in.get("width_px") or 1200
    height = journal.get("height_px") or canvas_in.get("height_px") or 600
    dpi = journal.get("dpi") or canvas_in.get("dpi") or 300
    try:
        width, height, dpi = int(width), int(height), int(dpi)
    except (TypeError, ValueError):
        width, height, dpi = 1200, 600, 300
        notes.append({"code": "canvas_defaulted",
                      "detail": "journal size not numeric; using 1200x600 @300dpi",
                      "severity": "warning"})
    if not journal:
        uncertainty.append("journal size not provided; defaulted to 1200x600 px @300 dpi")
    try:
        min_font_pt = float(journal.get("min_font_pt") or 8)
    except (TypeError, ValueError):
        min_font_pt = 8.0

    numbers = []
    for value, unit in NUMBER_RE.findall(claim):
        numbers.append({"value": value, "unit": unit,
                        "source": paper_rel or "case.json"})
    spec = {
        "case_id": case_id,
        "claim": {"claim": claim, "support": claim_support,
                  "not_claimed": [str(x) for x in (case.get("not_claimed") or [])],
                  "numbers": numbers},
        "elements": elements,
        "canvas": {"width_px": width, "height_px": height, "dpi": dpi},
        "journal_spec": {"min_font_pt": min_font_pt,
                         "source": "case.json" if journal else "default"},
        "reading_order": case.get("reading_order") or "left-to-right",
        "layout_type": case.get("layout_type") or "linear-progression",
        "style_type": case.get("style_type") or "journal",
        "caption": case.get("caption") or "",
        "alt_text": case.get("alt_text") or claim,
        "source": paper_rel or "case.json",
    }
    uncertainty.append("spec derived from real input fields/paper text "
                       "(no agent-authored ga-spec.json)")
    return spec, notes, uncertainty, paper_rel


def load_case(input_dir):
    if not input_dir.is_dir():
        return {}, {"code": "input_missing",
                    "detail": "input directory not found: %s" % input_dir}
    case_path = input_dir / "case.json"
    if not case_path.is_file():
        return {}, {"code": "case_missing",
                    "detail": "case.json not found in %s" % input_dir}
    try:
        case = json.loads(case_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, {"code": "case_invalid", "detail": str(exc)}
    if not isinstance(case, dict):
        return {}, {"code": "case_invalid", "detail": "case.json root is not an object"}
    return case, None


def canonicalize_sources(spec, input_dir, exceptions, change_log):
    """Rewrite real source paths to input-relative form; flag missing ones."""
    missing = []

    def fix(source):
        if not isinstance(source, dict):
            return
        path = str(source.get("path") or "").strip()
        if not path:
            return
        resolved = resolve_path(path, input_dir)
        if resolved:
            source["path"] = as_rel(resolved, input_dir)
        else:
            missing.append(path)

    for source in (spec.get("claim") or {}).get("support") or []:
        fix(source)
    for element in spec.get("elements") or []:
        fix(element.get("source"))
    for relation in spec.get("relations") or []:
        fix(relation.get("source"))
    if missing:
        unique = sorted(set(missing))
        exceptions.append({
            "code": "source_not_found",
            "detail": "paths not found under input: " + ", ".join(unique[:8]),
            "severity": "error"})
    else:
        change_log.append({"action": "sources_checked",
                           "detail": "all source paths resolve under input"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--spec", default=None,
                    help="explicit spec path (default: <work>/ga-spec.json)")
    ap.add_argument("--dpi", type=int, default=None)
    ap.add_argument("--no-export", action="store_true",
                    help="diagnostic: render SVG only, write a partial result")
    ap.add_argument("--visual-reviewed", action="store_true",
                    help="set only after actually opening the exported preview")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    input_dir = Path(args.input)
    outdir = Path(args.out)
    workdir = Path(args.work)
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        workdir.mkdir(parents=True, exist_ok=True)
        svg_gen = load_sibling("svg_gen.py")
        export_mod = load_sibling("export.py")
        audit_mod = load_sibling("audit.py")
        result_mod = load_sibling("result_graphics.py")
    except Exception as exc:  # noqa: BLE001 - cannot start at all
        print(json.dumps({"ok": False, "error": "startup failed: %s" % exc},
                         ensure_ascii=False, indent=2))
        return 2

    result_path = outdir / "result.json"
    input_report = inventory(input_dir)
    (workdir / "inventory.json").write_text(
        json.dumps(input_report, ensure_ascii=False, indent=2), encoding="utf-8")

    case, case_error = load_case(input_dir)
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    if case_error:
        result = result_mod.input_error_result(case_id, case_error["detail"])
        result_mod.write_result(result, result_path)
        report = {"ok": True, "status": "input_error", "case_id": case_id,
                  "result": str(result_path), "error": case_error}
        (workdir / "run-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    exceptions, uncertainty, change_log = [], [], []
    explicit = Path(args.spec) if args.spec else None
    spec_candidates = ([explicit] if explicit else []) + [workdir / "ga-spec.json"]
    raw_spec, spec_source, spec_path, spec_error = None, "derived", None, None
    for candidate in spec_candidates:
        if candidate and candidate.is_file():
            try:
                raw_spec = json.loads(candidate.read_text(encoding="utf-8",
                                                          errors="replace"))
                spec_source, spec_path = "agent", str(candidate)
                break
            except (OSError, json.JSONDecodeError) as exc:
                spec_error = "%s: %s" % (candidate, exc)
    if spec_error:
        exceptions.append({"code": "spec_parse_error", "detail": spec_error,
                           "severity": "warning"})
        change_log.append({"action": "spec_fallback",
                           "detail": "unreadable spec ignored: %s" % spec_error})
    if raw_spec is None:
        raw_spec, notes, uncertainty, paper_rel = derive_spec(case, input_dir)
        exceptions.extend(notes)
        if paper_rel:
            change_log.append({"action": "source_read",
                               "detail": "derived material from %s" % paper_rel})
    else:
        change_log.append({"action": "spec_read", "detail": spec_path})

    spec, spec_notes = svg_gen.normalize_spec(raw_spec)
    spec["case_id"] = case_id
    for note in spec_notes:
        entry = {"code": note["code"], "detail": note["detail"],
                 "severity": ("warning" if note["code"] in (
                     "elements_truncated", "id_repaired", "role_defaulted",
                     "canvas_defaulted", "relation_skipped") else "error")}
        exceptions.append(entry)
    canonicalize_sources(spec, input_dir, exceptions, change_log)
    (workdir / "ga-spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    change_log.append({"action": "spec_normalized",
                       "detail": "%d elements, %d relations"
                       % (len(spec["elements"]), len(spec["relations"]))})

    dpi = int(args.dpi or spec["canvas"].get("dpi") or 300)
    svg_abs = outdir / "figures" / "ga.svg"
    pdf_abs = outdir / "figures" / "ga.pdf"
    png_abs = outdir / "preview" / "ga.png"
    for stale in (pdf_abs, png_abs):
        if stale.is_file():
            try:
                stale.unlink()
            except OSError:
                pass
    render_info, render_error = {}, None
    if len(spec["elements"]) >= 1 and spec["claim"]["claim"]:
        try:
            render_info = svg_gen.build(spec, svg_abs)
            change_log.append({"action": "render",
                               "detail": "%d elements, %d relations, %s"
                               % (len(spec["elements"]), len(spec["relations"]),
                                  svg_abs.name)})
        except Exception as exc:  # noqa: BLE001
            render_error = "%s: %s" % (type(exc).__name__, exc)
            exceptions.append({"code": "render_failed", "detail": render_error,
                               "severity": "error"})
    else:
        render_error = "spec lacks a claim or elements"
        exceptions.append({"code": "render_skipped", "detail": render_error,
                           "severity": "error"})

    export_info = {"ok": False, "outputs": {}, "attempts": [], "tools": {},
                   "exceptions": []}
    if svg_abs.is_file() and not args.no_export:
        export_info = export_mod.export_svg(svg_abs, pdf_abs, png_abs, dpi)
        if export_info.get("ok"):
            change_log.append({"action": "export",
                               "detail": "pdf=%s png=%s"
                               % (pdf_abs.is_file(), png_abs.is_file())})
        else:
            for item in export_info.get("exceptions") or []:
                exceptions.append({"code": item.get("code", "export_error"),
                                   "detail": item.get("detail", ""),
                                   "severity": "error"})
    elif args.no_export:
        export_info["exceptions"] = [{"code": "export_skipped",
                                      "detail": "--no-export diagnostic"}]
        exceptions.append({"code": "export_skipped",
                           "detail": "diagnostic run without export",
                           "severity": "warning"})
    elif not svg_abs.is_file():
        exceptions.append({"code": "export_skipped",
                           "detail": "no SVG to export", "severity": "error"})

    audit_report = audit_mod.audit(
        spec,
        str(svg_abs) if svg_abs.is_file() else None,
        str(pdf_abs) if pdf_abs.is_file() else None,
        str(png_abs) if png_abs.is_file() else None,
        min_font_pt=spec["journal_spec"].get("min_font_pt", 8.0), dpi=dpi)
    (workdir / "coverage.json").write_text(
        json.dumps(audit_report, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in audit_report.get("errors") or []:
        exceptions.append({"code": item.get("code", "audit_error"),
                           "detail": item.get("detail") or
                           json.dumps(item, ensure_ascii=False),
                           "severity": "error"})

    error_codes = {e["code"] for e in exceptions if e.get("severity") == "error"}
    exports_ok = bool(pdf_abs.is_file() and png_abs.is_file() and
                      audit_report.get("exports", {}).get("pdf", {}).get("signature")
                      and audit_report.get("exports", {}).get("png", {}).get("signature"))
    if render_error or not exports_ok or not audit_report.get("ok") or \
            error_codes or len(spec["elements"]) < 3 or not spec["claim"]["claim"]:
        status = "partial"
    else:
        status = "answered"
    if status == "partial" and not exceptions:
        exceptions.append({"code": "partial_completion",
                           "detail": "quality gate not fully satisfied",
                           "severity": "warning"})

    visual_reviewed = bool(args.visual_reviewed or spec.get("visual_reviewed"))
    result = result_mod.build_result(
        case_id, spec, audit_report, status,
        svg_rel="figures/ga.svg" if svg_abs.is_file() else None,
        pdf_rel="figures/ga.pdf" if pdf_abs.is_file() else None,
        png_rel="preview/ga.png" if png_abs.is_file() else None,
        dpi=dpi, exceptions=exceptions, uncertainty=uncertainty,
        change_log=change_log, visual_reviewed=visual_reviewed,
        spec_source=spec_source)
    result_mod.write_result(result, result_path)

    report = {
        "ok": True,
        "case_id": case_id,
        "status": status,
        "result": str(result_path),
        "spec_source": spec_source,
        "input_files": input_report.get("total", 0),
        "elements": len(spec["elements"]),
        "relations": len(spec["relations"]),
        "render": {"svg": svg_abs.is_file(),
                   "bytes": render_info.get("bytes")},
        "exports": {"pdf": pdf_abs.is_file(), "png": png_abs.is_file()},
        "audit": {"ok": audit_report.get("ok"),
                  "coverage": audit_report.get("coverage"),
                  "errors": [e.get("code") for e in audit_report.get("errors") or []]},
        "exceptions": [e.get("code") for e in exceptions],
    }
    (workdir / "run-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - last-resort trace for the operator
        traceback.print_exc()
        raise SystemExit(2)
