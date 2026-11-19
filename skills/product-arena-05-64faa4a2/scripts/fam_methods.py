#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Writing 09 — methods and experiments from configs, logs, code and data."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import skillcore as S

PARAM_HINTS = re.compile(
    r"(?:lr|learning_rate|batch|epoch|seed|optimizer|weight_decay|dropout|"
    r"temperature|top_p|max_tokens|window|stride|threshold|alpha|beta|gamma|"
    r"hidden|layers|heads|dim|model|dataset|split|ratio|fold|repeat|"
    r"workers|device|precision|resolution|steps|iterations|patience|"
    r"momentum|scheduler|augmentation)", re.I)
KV_RE = re.compile(
    r"(?m)^\s*([A-Za-z_][A-Za-z0-9_.\-/]{1,80})\s*[:=]\s*"
    r"([^\s#;,'\"\]\}]+(?:\s+[A-Za-z%°μ/][^\s#;,'\"\]\}]*)?)")


def _flatten(value, prefix=""):
    rows = []
    if isinstance(value, dict):
        for key, item in value.items():
            rows.extend(_flatten(item, (prefix + "." + str(key)) if prefix else str(key)))
    elif isinstance(value, list):
        if all(not isinstance(x, (dict, list)) for x in value):
            rows.append((prefix, ", ".join(str(x) for x in value)))
        else:
            for index, item in enumerate(value):
                rows.extend(_flatten(item, "%s[%d]" % (prefix, index)))
    else:
        rows.append((prefix, value))
    return rows


def _parse_structured(doc):
    text = S.doc_text(doc)
    kind = doc.get("kind")
    data = doc.get("data")
    if kind == "json" and data is not None:
        return data
    if kind in ("yaml", "yml"):
        try:
            import yaml  # type: ignore
            return yaml.safe_load(text)
        except Exception:
            return None
    if kind == "toml":
        try:
            import tomllib
            return tomllib.loads(text)
        except Exception:
            return None
    return None


def _source_locator(doc, line=None):
    path = doc.get("rel_path") or doc.get("path") or ""
    return "%sL%s" % (path, line) if line else path


def _extract_facts(ctx):
    parameters, settings, datasets, baselines, versions = [], [], [], [], []
    for doc in ctx.docs:
        text = S.doc_text(doc)
        kind = doc.get("kind")
        path = doc.get("rel_path") or doc.get("path") or ""
        structured = _parse_structured(doc)
        if structured is not None:
            for key, value in _flatten(structured):
                if not key or value in (None, ""):
                    continue
                row = {"name": key, "value": value, "source": path,
                       "locator": path, "kind": "config"}
                if PARAM_HINTS.search(key):
                    parameters.append(row)
                else:
                    settings.append(row)
                if re.search(r"dataset|corpus|benchmark|split|fold", key, re.I):
                    datasets.append({"name": key, "value": value, "source": path,
                                     "locator": path})
                if re.search(r"baseline|control|compare", key, re.I):
                    baselines.append({"name": key, "value": value, "source": path,
                                      "locator": path})
                if re.search(r"version|commit|release|image|python|torch|tensorflow|cuda", key, re.I):
                    versions.append({"name": key, "value": value, "source": path,
                                     "locator": path})
        if kind in ("text", "log", "code", "md", "other") or text:
            for index, line in enumerate(text.splitlines(), 1):
                if len(line) > 300:
                    continue
                for match in KV_RE.finditer(line):
                    key, value = match.group(1), match.group(2).strip()
                    if len(key) > 1 and len(value) < 80:
                        row = {"name": key, "value": value, "source": path,
                               "locator": _source_locator(doc, index), "kind": kind}
                        if PARAM_HINTS.search(key):
                            parameters.append(row)
                        if re.search(r"dataset|corpus|benchmark|split|fold", key, re.I):
                            datasets.append({"name": key, "value": value, "source": path,
                                             "locator": _source_locator(doc, index)})
                        if re.search(r"version|commit|release|image|python|torch|tensorflow|cuda", key, re.I):
                            versions.append({"name": key, "value": value, "source": path,
                                             "locator": _source_locator(doc, index)})
                for match in re.finditer(r"(--[A-Za-z0-9_-]+)(?:\s+default\s*[=:]\s*)([^\s,;]+)", line):
                    parameters.append({"name": match.group(1), "value": match.group(2),
                                       "source": path, "locator": _source_locator(doc, index),
                                       "kind": "cli-default"})
                for match in re.finditer(r"(?:manual_seed|random_seed|seed)\s*[=:(]\s*(\d+)", line, re.I):
                    parameters.append({"name": "seed", "value": match.group(1),
                                       "source": path, "locator": _source_locator(doc, index),
                                       "kind": "seed"})
                if re.search(r"dataset|data set|corpus", line, re.I) and len(line) < 220:
                    datasets.append({"name": "dataset mention", "value": line.strip(),
                                     "source": path, "locator": _source_locator(doc, index)})
        if kind == "csv":
            rows = doc.get("rows") or []
            datasets.append({"name": path, "value": "rows=%d cols=%d" % (
                max(len(rows) - 1, 0), max((len(r) for r in rows), default=0)),
                "source": path, "locator": path})
        # De-duplicate and cap.
        def dedupe(items):
            seen, out = set(), []
            for item in items:
                key = (str(item.get("name")), str(item.get("value")), item.get("source"))
                if key not in seen:
                    seen.add(key)
                    out.append(item)
            return out[:250]
        parameters, settings = dedupe(parameters), dedupe(settings)
        datasets, baselines, versions = dedupe(datasets), dedupe(baselines), dedupe(versions)
    return {"parameters": parameters, "settings": settings,
            "datasets": datasets, "baselines": baselines, "versions": versions}


def prepare(ctx):
    if not any(S.doc_text(d).strip() for d in ctx.docs):
        ctx.result.change("prepare", "input", "no readable experimental material")
        ctx.result.uncertainty("materials", "no readable config/log/code/data",
                               "methods section cannot be grounded", "provide materials")
        return ctx.result.finalize("input_error")
    facts = _extract_facts(ctx)
    S.write_json(ctx.work / "extracted-facts.json", {
        "case_id": ctx.case_id, "facts": facts,
        "source_files": [{"path": d.get("rel_path"), "kind": d.get("kind"),
                          "chars": len(S.doc_text(d))} for d in ctx.docs],
    })
    with (ctx.work / "parameter-table.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["name", "value", "source", "locator", "kind"])
        for row in facts["parameters"]:
            writer.writerow([row.get("name"), row.get("value"), row.get("source"),
                             row.get("locator"), row.get("kind")])
    S.write_json(ctx.work / "methods-plan-template.json", {
        "case_id": ctx.case_id,
        "steps": [{"id": "M1", "name": "", "input": "", "operation": "",
                   "output": "", "decision": "",
                   "source": {"path": "", "locator": ""}}],
        "settings": {"hardware": "", "software": "", "environment": "",
                     "seeds": "", "repeats": ""},
        "parameters": [{"name": "", "value": "", "unit": "", "scope": "",
                        "source": {"path": "", "locator": ""}}],
        "datasets": [{"name": "", "version": "", "size": "", "preprocessing": "",
                      "split": "", "source": {"path": "", "locator": ""}}],
        "baselines": [{"name": "", "version": "", "conditions": "", "metrics": "",
                       "source": {"path": "", "locator": ""}}],
        "reproducibility": [{"item": "", "value": "", "source": {"path": "", "locator": ""}}],
        "limitations": [{"statement": "", "failure_scenario": "", "source": ""}],
        "gaps": [{"item": "", "why": "", "question": ""}],
    })
    skeleton = [
        "# Methods and Experiments (source-grounded skeleton — not a final section)",
        "",
        "> Replace this skeleton with a complete, reviewer-ready section in",
        "> `/app/output/methods.md`. Every number must come from the files below.",
        "",
        "## Methods",
        "### Step sequence",
    ]
    for index, row in enumerate(facts["parameters"][:20], 1):
        skeleton.append("%d. `%s = %s` (%s)" % (index, row.get("name"), row.get("value"),
                                                row.get("locator")))
    skeleton += ["", "### Experimental settings", "### Datasets and splits",
                 "### Baselines and controls", "### Reproducibility",
                 "### Limitations", "", "## Questions for the author"]
    S.write_text(ctx.work / "methods-skeleton.md", "\n".join(skeleton))
    ctx.result.change("extract", "materials",
                      "%d parameters, %d datasets, %d versions extracted" % (
                          len(facts["parameters"]), len(facts["datasets"]),
                          len(facts["versions"])))
    return ctx.provisional(reason="experimental facts extracted; write methods.md and methods-plan.json")


def _load_plan(ctx):
    payload = S.load_json(ctx.work / "methods-plan.json", {})
    return payload if isinstance(payload, dict) else {}


def _plan_problems(plan):
    problems = []
    steps = plan.get("steps") or []
    if not steps:
        problems.append("no ordered steps")
    for step in steps:
        if not step.get("source") or not (step.get("source") or {}).get("path"):
            problems.append("step %s has no source" % step.get("id", "?"))
    for row in plan.get("parameters") or []:
        if not row.get("source") or not (row.get("source") or {}).get("path"):
            problems.append("parameter %s has no source" % row.get("name", "?"))
    for dataset in plan.get("datasets") or []:
        if not dataset.get("version"):
            problems.append("dataset %s lacks version" % dataset.get("name", "?"))
        if not dataset.get("split"):
            problems.append("dataset %s lacks split" % dataset.get("name", "?"))
    for baseline in plan.get("baselines") or []:
        if not baseline.get("version"):
            problems.append("baseline %s lacks version" % baseline.get("name", "?"))
        if not baseline.get("conditions"):
            problems.append("baseline %s lacks comparison conditions" % baseline.get("name", "?"))
    repro = plan.get("reproducibility") or []
    repro_text = json.dumps(repro, ensure_ascii=False).lower()
    aliases = {
        "environment": ("environment", "环境"),
        "command": ("command", "命令", "运行"),
        "seed": ("seed", "种子", "随机"),
        "data": ("data", "数据"),
    }
    for needed, words in aliases.items():
        if not any(word in repro_text for word in words):
            problems.append("reproducibility lacks %s" % needed)
    limitations = plan.get("limitations") or []
    if not limitations:
        problems.append("no limitations")
    for item in limitations:
        statement = str(item.get("statement", ""))
        if len(statement) < 12 or re.search(
                r"limitation|future work|not perfect|局限|未来工作|不完美",
                statement, re.I):
            problems.append("limitation is generic: %s" % S.excerpt(statement, 60))
        if not item.get("failure_scenario"):
            problems.append("limitation lacks a concrete failure scenario")
    return problems


def finalize(ctx):
    methods_path = ctx.output / "methods.md"
    text = S.read_text(methods_path)
    if not text.strip():
        skeleton = S.read_text(ctx.work / "methods-skeleton.md")
        if skeleton.strip():
            S.write_text(ctx.output / "methods-partial.md", skeleton)
            ctx.result.add_artifact(ctx.output / "methods-partial.md", "partial_draft", "md",
                                    "source-grounded skeleton, not a finished section")
        ctx.result.change("finalize", "output", "methods.md missing")
        ctx.result.uncertainty("deliverable", "model-authored methods.md is missing",
                               "no finished section", "write methods.md and rerun finalize")
        return ctx.result.finalize("partial")
    plan = _load_plan(ctx)
    facts = S.load_json(ctx.work / "extracted-facts.json", {}).get("facts", {})
    source_text = "\n\n".join(S.doc_text(d) for d in ctx.docs)
    invented = S.number_presence_missing(text, source_text)
    numbers = {"ok": not invented, "invented": invented}
    plan_problems = _plan_problems(plan)
    # Cross-check the text against the plan parameter values.
    plan_values = {str(p.get("value")) for p in (plan.get("parameters") or [])
                   if p.get("value") not in (None, "")}
    text_numbers = {t["raw"] for t in S.number_tokens(text)}
    untabulated = sorted(v for v in text_numbers if v not in plan_values
                         and v not in {str(f.get("value")) for f in facts.get("parameters", [])})
    gaps = plan.get("gaps") or []
    if not gaps:
        plan_problems.append("no explicit gaps/questions for the author")
    report = {
        "case_id": ctx.case_id,
        "numbers": numbers,
        "plan_problems": plan_problems,
        "untabulated_numbers": untabulated,
        "steps": len(plan.get("steps") or []),
        "parameters": len(plan.get("parameters") or []),
        "datasets": len(plan.get("datasets") or []),
        "baselines": len(plan.get("baselines") or []),
        "gaps": len(gaps),
    }
    S.write_json(ctx.output / "methods-report.json", report)
    S.write_json(ctx.output / "parameter-table.json", {
        "case_id": ctx.case_id, "parameters": plan.get("parameters") or [],
        "extracted": facts.get("parameters", []),
    })
    with (ctx.output / "parameter-table.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["name", "value", "unit", "scope", "source_path", "locator"])
        for row in plan.get("parameters") or []:
            source = row.get("source") or {}
            writer.writerow([row.get("name"), row.get("value"), row.get("unit", ""),
                             row.get("scope", ""), source.get("path", ""),
                             source.get("locator", "")])
    repro_lines = ["# Reproducibility checklist", ""]
    for item in plan.get("reproducibility") or []:
        source = item.get("source") or {}
        repro_lines.append("- [ ] **%s**: %s (%s %s)" % (
            item.get("item", ""), item.get("value", ""),
            source.get("path", ""), source.get("locator", "")))
    if len(repro_lines) == 2:
        repro_lines.append("- [ ] No reproducibility items were supplied; see gaps below.")
    S.write_text(ctx.output / "repro-checklist.md", "\n".join(repro_lines) + "\n")
    gap_lines = ["# Gap questions for the author", ""]
    for index, item in enumerate(gaps, 1):
        gap_lines.append("%d. **%s** — %s. Ask: %s" % (
            index, item.get("item", ""), item.get("why", ""), item.get("question", "")))
    if len(gap_lines) == 2:
        gap_lines.append("1. No explicit gaps were recorded; review the materials and add them.")
    S.write_text(ctx.output / "gap-questions.md", "\n".join(gap_lines) + "\n")

    critical = (not numbers["ok"] or bool(plan_problems))
    status = "completed" if not critical else "partial"
    if not numbers["ok"]:
        ctx.result.uncertainty("number_mismatch", json.dumps(numbers, ensure_ascii=False),
                               "methods text contains numbers absent from the materials",
                               "restore exact values or add source")
    for problem in plan_problems:
        ctx.result.uncertainty("plan", problem, "methods section is not fully reproducible",
                               "fill the missing field")
    if untabulated:
        ctx.result.uncertainty("untabulated_numbers", ", ".join(untabulated[:20]),
                               "numbers in text are not in the parameter table",
                               "align text and table")

    preserved = [{"kind": "number", "source": t["raw"], "target": t["raw"],
                  "locator": "materials", "value": t["raw"], "unit": ""}
                 for t in S.number_tokens(source_text)[:150]]
    evidence = []
    for step in plan.get("steps") or []:
        source = step.get("source") or {}
        evidence.append({"claim_id": step.get("id", ""), "claim": step.get("name", ""),
                         "source": {"path": source.get("path", ""),
                                    "locator": source.get("locator", "")},
                         "quote": step.get("operation", ""), "supports": "step"})
    for row in plan.get("parameters") or []:
        source = row.get("source") or {}
        evidence.append({"claim_id": "param:%s" % row.get("name", ""),
                         "claim": "%s = %s" % (row.get("name"), row.get("value")),
                         "source": {"path": source.get("path", ""),
                                    "locator": source.get("locator", "")},
                         "quote": "", "supports": "parameter"})

    ctx.result.add_artifact(methods_path, "deliverable", "md", "methods and experiments section")
    ctx.result.add_artifact(ctx.output / "parameter-table.json", "parameter_table", "json",
                            "parameters with sources")
    ctx.result.add_artifact(ctx.output / "parameter-table.csv", "parameter_table", "csv",
                            "spreadsheet-friendly parameter table")
    ctx.result.add_artifact(ctx.output / "repro-checklist.md", "reproducibility", "md",
                            "reproduction checklist")
    ctx.result.add_artifact(ctx.output / "gap-questions.md", "gaps", "md",
                            "questions for the author")
    ctx.result.add_artifact(ctx.output / "methods-report.json", "audit", "json",
                            "number and plan checks")
    docx_path = ctx.output / "methods.docx"
    try:
        S.write_docx(text, docx_path, title="Methods and Experiments")
        ok, why = S.docx_reopen_check(docx_path)
        if not ok:
            ctx.result.uncertainty("docx_reopen", why, "DOCX may not open", "use methods.md")
        else:
            ctx.result.add_artifact(docx_path, "editable_docx", "docx",
                                    "editable Word version")
    except Exception as exc:  # noqa: BLE001
        ctx.result.uncertainty("docx_export", str(exc), "DOCX unavailable", "use methods.md")
    preview = ctx.output / "methods-preview.pdf"
    pdf, pdf_err = S.write_pdf_preview(text, preview, title="Methods and Experiments")
    if pdf and pdf.is_file():
        ctx.result.add_artifact(pdf, "preview_pdf", "pdf",
                                "text-content PDF preview, not a Word layout render")
    elif pdf_err:
        ctx.result.uncertainty("pdf_preview", pdf_err, "preview unavailable", "inspect methods.md")

    ctx.result.change("write", "methods", "section drafted from extracted facts")
    ctx.result.change("audit", "numbers+plan", "numbers and reproducibility fields checked")
    return ctx.result.finalize(
        status,
        deliverable=methods_path,
        result={"summary": "Methods section drafted; %d steps, %d parameters, %d datasets, %d baselines." % (
                    len(plan.get("steps") or []), len(plan.get("parameters") or []),
                    len(plan.get("datasets") or []), len(plan.get("baselines") or [])),
                "checks": report, "word_count": S.word_count(text)},
        claims=[{"claim_id": s.get("id"), "text": s.get("name", ""),
                 "evidence": s.get("source")} for s in (plan.get("steps") or [])],
        preserved_facts=preserved, evidence_map=evidence,
    )
