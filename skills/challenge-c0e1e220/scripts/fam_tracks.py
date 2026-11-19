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
        if not isinstance(template, dict):
            template = _generic_template(ctx)
        template = dict(template)
        template["case_id"] = ctx.case_id
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
        if "quote" in value and isinstance(value.get("quote"), str):
            source = value.get("source") if isinstance(value.get("source"), dict) else {}
            path = (value.get("path") or source.get("path")
                    or value.get("doc") or value.get("file") or "")
            locator = (value.get("locator") or source.get("locator")
                       or value.get("paragraph") or value.get("page") or "")
            out.append({"quote": value.get("quote", ""),
                        "path": path or "", "locator": locator or ""})
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
    # Intermediate and final artifacts are legitimate evidence sources for
    # multi-step/analysis tasks.  Index them under work/ and output/.
    for base, prefix in ((ctx.work, "work"), (ctx.output, "output")):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            suffix = path.suffix.lower()
            if suffix not in (".md", ".txt", ".json", ".csv", ".log",
                              ".py", ".tex", ".yaml", ".yml", ".sql"):
                continue
            text = S.read_text(path)
            rel = path.relative_to(base).as_posix()
            by_path["%s/%s" % (prefix, rel)] = text
            by_path.setdefault(rel, text)
    problems = []
    for index, item in enumerate(evidence, 1):
        quote = str(item.get("quote") or "").strip()
        if not quote:
            problems.append("evidence %d: empty quote" % index)
            continue
        if not str(item.get("locator") or "").strip():
            problems.append("evidence %d: empty locator" % index)
        path = str(item.get("path") or "")
        text = by_path.get(path)
        if text is None:
            # A missing path is a warning, not a fabricated quote.
            problems.append("evidence %d: source path not found: %s" % (index, path))
            continue
        ok, reason = S.quote_supported(text, quote)
        if not ok:
            problems.append("evidence %d: %s" % (index, reason))
    return problems


def _quote_claim_map(value, out, parent_id="", parent_text=""):
    """Map each verbatim quote back to the candidate/finding that owns it."""
    if isinstance(value, dict):
        cid = str(value.get("id") or parent_id or "")
        text = (value.get("statement") or value.get("text")
                or value.get("title") or value.get("label") or parent_text)
        if cid and "evidence" in value:
            found = []
            _walk_evidence(value.get("evidence"), found)
            for item in found:
                out.setdefault(item.get("quote", ""), (cid, str(text or "")))
        for item in value.values():
            _quote_claim_map(item, out, cid, str(text or ""))
    elif isinstance(value, list):
        for item in value:
            _quote_claim_map(item, out, parent_id, parent_text)


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
    for name in ("figures", "tables", "results", "library", "archive",
                 "quarantine", "notes", "annotations"):
        folder = ctx.output / name
        if folder.is_dir():
            files.extend(p for p in sorted(folder.rglob("*")) if p.is_file())
    # Task-specific output_contract keys often map to same-named files in the
    # output root (e.g. cleaned_table.json).  Register them unless they are the
    # envelope itself or the provisional README.
    for path in sorted(ctx.output.glob("*")):
        if path.is_file() and path.name not in ("result.json", "README-partial.md"):
            files.append(path)
    # De-duplicate while preserving order.
    seen, out = set(), []
    for path in files:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _track_checks(track, work):
    """Task-specific structural checks that the generic envelope cannot see."""
    problems, warnings = [], []
    if track == "search":
        for index, query in enumerate(work.get("queries") or [], 1):
            if not isinstance(query, dict):
                problems.append("query %d is not an object" % index)
                continue
            if not (query.get("query") or query.get("query_text")):
                problems.append("query %d lacks query text" % index)
            if not query.get("source"):
                problems.append("query %d lacks source" % index)
            if not query.get("purpose"):
                warnings.append("query %d lacks purpose" % index)
            if "round" not in query:
                warnings.append("query %d lacks round" % index)
        for index, candidate in enumerate(work.get("candidates") or [], 1):
            if not isinstance(candidate, dict):
                problems.append("candidate %d is not an object" % index)
                continue
            if not candidate.get("title"):
                problems.append("candidate %d lacks title" % index)
            if not candidate.get("reason"):
                problems.append("candidate %d lacks relevance reason" % index)
            if not candidate.get("evidence"):
                problems.append("candidate %d lacks evidence" % index)
            if not candidate.get("year") or not (candidate.get("doi")
                                                 or candidate.get("venue")):
                warnings.append("candidate %d has incomplete metadata" % index)
            if "rank" not in candidate:
                warnings.append("candidate %d lacks rank" % index)
            if "score" not in candidate:
                warnings.append("candidate %d lacks score" % index)
        keys = [str(c.get("dedupe_key")) for c in (work.get("candidates") or [])
                if isinstance(c, dict) and c.get("dedupe_key")]
        if len(keys) != len(set(keys)):
            problems.append("duplicate dedupe_key candidates were not merged")
        if not work.get("metrics"):
            warnings.append("search metrics (k/Precision@k/nDCG@k/Recall@k) are missing")
        if not work.get("screening") and not (work.get("seeds") or work.get("subscription")):
            warnings.append("screening include/exclude/uncertain layers are missing")
        for edge in work.get("edges") or []:
            if isinstance(edge, dict):
                for field in ("from", "to", "direction", "source"):
                    if not edge.get(field):
                        problems.append("edge %s lacks %s" % (
                            edge.get("id", "?"), field))
        for path in work.get("paths") or []:
            if isinstance(path, dict) and not (path.get("nodes") and path.get("edges")):
                problems.append("path %s is not a complete hop chain" % path.get("id", "?"))
        if "subscription" in work:
            if not work.get("window") or not work.get("watermark"):
                problems.append("incremental tracking lacks window/watermark")
            if not work.get("increments"):
                problems.append("incremental tracking has no increments")
        if "coverage" in work:
            coverage = work.get("coverage") if isinstance(work.get("coverage"), dict) else {}
            if "numerator" in coverage or "denominator" in coverage:
                if coverage.get("numerator") is None or coverage.get("denominator") in (None, 0):
                    problems.append("coverage numerator/denominator is missing or zero")
        if "prisma_counts" in work:
            counts = work.get("prisma_counts") if isinstance(work.get("prisma_counts"), dict) else {}
            identified = counts.get("identified")
            duplicates = counts.get("duplicates")
            screened = counts.get("screened")
            excluded = counts.get("excluded")
            included = counts.get("included")
            if None not in (identified, duplicates, screened, excluded, included):
                if identified - duplicates != screened or screened - excluded != included:
                    problems.append("PRISMA counts do not close")
        for appraisal in work.get("appraisals") or []:
            if isinstance(appraisal, dict) and not (appraisal.get("dimension")
                                                    and appraisal.get("rating")
                                                    and appraisal.get("evidence")):
                problems.append("appraisal %s lacks dimension/rating/evidence" % (
                    appraisal.get("record_id", "?")))
        for patent in work.get("patents") or []:
            if isinstance(patent, dict) and not (patent.get("patent_number")
                                                 and patent.get("legal_status")):
                problems.append("patent %s lacks number/legal status" % (
                    patent.get("id", "?")))
    elif track == "library":
        for index, item in enumerate(work.get("items") or [], 1):
            if not isinstance(item, dict):
                problems.append("item %d is not an object" % index)
                continue
            if not (item.get("path") or item.get("id")):
                problems.append("item %d lacks path/id" % index)
            if not item.get("evidence"):
                problems.append("item %d lacks evidence" % index)
            if not (item.get("hash") or item.get("sha256")):
                warnings.append("item %d lacks content hash" % index)
        for index, operation in enumerate(work.get("operations") or [], 1):
            if isinstance(operation, dict) and not operation.get("status"):
                warnings.append("operation %d lacks status" % index)
        if "version_chain" in work:
            for item in work.get("items") or []:
                if isinstance(item, dict):
                    if not item.get("sha256"):
                        problems.append("library item %s lacks sha256" % item.get("id", "?"))
                    if not item.get("status"):
                        problems.append("library item %s lacks status" % item.get("id", "?"))
        if "filters" in work:
            if not work.get("filter_execution"):
                problems.append("filters are declared but filter_execution is empty")
            if not work.get("filters", {}).get("must") and not work.get("filters", {}).get("must_not"):
                problems.append("filters must/must_not are both empty")
        if "taxonomy" in work:
            if not work.get("taxonomy"):
                problems.append("classification taxonomy is empty")
            for item in work.get("items") or []:
                if isinstance(item, dict) and not item.get("category"):
                    problems.append("classified item %s lacks category" % item.get("id", "?"))
        for answer in work.get("answers") or []:
            if not isinstance(answer, dict):
                continue
            status = str(answer.get("status") or "")
            sentences = answer.get("sentences") or []
            if status in ("refused", "insufficient_evidence"):
                if not answer.get("missing_evidence"):
                    problems.append("refused answer %s lacks missing_evidence" % answer.get("id", "?"))
            elif not sentences:
                problems.append("answer %s has no sentences" % answer.get("id", "?"))
            for sentence in sentences:
                if isinstance(sentence, dict) and not sentence.get("citations"):
                    problems.append("answer sentence lacks citations: %s" % answer.get("id", "?"))
        for annotation in work.get("annotations") or []:
            if isinstance(annotation, dict) and not (annotation.get("doc")
                                                     or annotation.get("path")):
                problems.append("annotation %s lacks doc/path" % annotation.get("id", "?"))
            if isinstance(annotation, dict) and not annotation.get("anchor_status"):
                problems.append("annotation %s lacks anchor_status" % annotation.get("id", "?"))
            if isinstance(annotation, dict) and not annotation.get("doc_sha256"):
                warnings.append("annotation %s lacks doc_sha256" % annotation.get("id", "?"))
    elif track == "research":
        for index, finding in enumerate(work.get("findings") or [], 1):
            if not isinstance(finding, dict):
                problems.append("finding %d is not an object" % index)
                continue
            if not finding.get("statement"):
                problems.append("finding %d lacks statement" % index)
            if not finding.get("evidence"):
                problems.append("finding %d lacks evidence" % index)
            if not finding.get("sources"):
                warnings.append("finding %d lacks source list" % index)
        if not work.get("gap_analysis") and not work.get("gaps"):
            warnings.append("gap_analysis/gaps is empty")
        for sub in work.get("subquestions") or []:
            if isinstance(sub, dict) and not sub.get("status"):
                problems.append("subquestion %s lacks coverage status" % sub.get("id", "?"))
        for log in work.get("screening_log") or []:
            if isinstance(log, dict) and not (log.get("decision") and log.get("reason")):
                problems.append("screening log entry lacks decision/reason")
        if "protocol" in work and "screening" in work:
            s = work.get("screening") or {}
            if None not in (s.get("identified"), s.get("duplicates"),
                            s.get("screened"), s.get("excluded"), s.get("included")):
                if s["identified"] - s["duplicates"] != s["screened"] \
                        or s["screened"] - s["excluded"] != s["included"]:
                    problems.append("systematic-review screening counts do not close")
        for theme in work.get("themes") or []:
            if isinstance(theme, dict) and len(theme.get("records") or []) < 2:
                problems.append("theme %s has fewer than 2 records" % theme.get("id", "?"))
        if work.get("themes"):
            for finding in work.get("findings") or []:
                if isinstance(finding, dict) and not (finding.get("support")
                                                      and finding.get("strength")):
                    problems.append("review-generation finding lacks support/strength")
        for claim in work.get("novelty_claims") or []:
            if isinstance(claim, dict) and not claim.get("scope"):
                problems.append("novelty claim lacks scope")
        if "feasibility" in work:
            feasibility = work.get("feasibility") or {}
            if not feasibility.get("premises"):
                problems.append("feasibility lacks premises")
    elif track == "review":
        for index, finding in enumerate(work.get("findings") or [], 1):
            if not isinstance(finding, dict):
                problems.append("finding %d is not an object" % index)
                continue
            if not (finding.get("location") or finding.get("locator")):
                problems.append("finding %d lacks location/locator" % index)
            if not finding.get("severity"):
                problems.append("finding %d lacks severity" % index)
            if not finding.get("evidence"):
                problems.append("finding %d lacks evidence" % index)
            if not (finding.get("original_quote") or finding.get("quote")):
                problems.append("finding %d lacks original quote" % index)
            if not (finding.get("type") or finding.get("issue_type")):
                warnings.append("finding %d lacks issue type" % index)
            if not (finding.get("fix") or finding.get("recommendation")):
                warnings.append("finding %d lacks fix/recommendation" % index)
        if not work.get("checks") and not work.get("facets_checked") \
                and not work.get("compliance_checklist") and not work.get("claim_source_matrix"):
            warnings.append("checklist/facets_checked is empty")
        for row in work.get("claim_source_matrix") or []:
            if not isinstance(row, dict):
                continue
            if not (row.get("claim") and row.get("source")):
                problems.append("claim-source row lacks claim/source")
            if row.get("verdict") not in ("full", "partial", "none", "unverifiable"):
                problems.append("claim-source verdict must be full/partial/none/unverifiable")
        for row in work.get("source_status") or []:
            if isinstance(row, dict):
                if row.get("status") not in ("published", "preprint", "retracted", "unknown"):
                    problems.append("source status must be published/preprint/retracted/unknown")
                if row.get("status") == "unknown" and not row.get("needs_recheck"):
                    warnings.append("unknown source status should set needs_recheck")
        for row in work.get("claim_inventory") or []:
            if isinstance(row, dict) and not (row.get("speaker") and row.get("support")):
                warnings.append("claim inventory row lacks speaker/support")
        for row in work.get("compliance_checklist") or []:
            if isinstance(row, dict) and not (row.get("rule_id") and row.get("status")):
                problems.append("compliance check lacks rule_id/status")
        if "decision_rules" in work and not work.get("decision_rules"):
            warnings.append("decision_rules is empty")
        if "reporting_checklist" in work and not work.get("reporting_checklist"):
            warnings.append("reporting_checklist is empty")
        if "guideline_source" in work:
            guide = work.get("guideline_source") or {}
            if not (guide.get("name") and guide.get("version")):
                problems.append("guideline_source lacks name/version")
        if work.get("review_status") == "not_checked" and not work.get("not_checked"):
            problems.append("review_status=not_checked but not_checked list is empty")
    elif track == "analysis":
        analysis = work.get("analysis") if isinstance(work.get("analysis"), dict) else {}
        if not analysis.get("method"):
            problems.append("analysis.method is missing")
        if not work.get("results"):
            problems.append("results are empty")
        for index, row in enumerate(work.get("results") or [], 1):
            if isinstance(row, dict) and not (row.get("metric") or row.get("name")):
                warnings.append("result %d lacks metric/name" % index)
            if isinstance(row, dict) and not row.get("evidence"):
                problems.append("result %d lacks evidence" % index)
        if "cleaned_table" in work or "cleaning_rules" in work:
            closure = work.get("closure") if isinstance(work.get("closure"), dict) else {}
            if closure and closure.get("raw_rows") is not None:
                if closure.get("raw_rows", 0) - closure.get("dropped", 0) != closure.get("cleaned_rows"):
                    problems.append("cleaning row counts do not close")
            if not work.get("original_snapshot"):
                warnings.append("cleaning original_snapshot is missing")
        for fig in work.get("figures") or []:
            if isinstance(fig, dict) and not (fig.get("data_source") and fig.get("units")):
                problems.append("figure %s lacks data_source/units" % fig.get("id", "?"))
        if "query" in work and not str(work.get("query") or "").strip():
            problems.append("database query is empty")
        if "code" in work and not work.get("code"):
            problems.append("code path is empty")
    elif track == "experiment":
        for index, step in enumerate(work.get("steps") or [], 1):
            if isinstance(step, dict) and not step.get("action"):
                problems.append("step %d lacks action" % index)
        for index, row in enumerate(work.get("parameters") or [], 1):
            if isinstance(row, dict) and row.get("value") in (None, ""):
                warnings.append("parameter %d lacks value" % index)
        if not work.get("risks"):
            warnings.append("risk assessment is empty")
        if "feasibility" in work:
            feasibility = work.get("feasibility") or {}
            for key in ("equipment", "materials", "time", "budget",
                        "safety", "ethics"):
                if key not in feasibility:
                    warnings.append("feasibility lacks %s" % key)
        for row in work.get("recommendations") or []:
            if isinstance(row, dict) and not row.get("basis"):
                problems.append("parameter recommendation lacks basis")
        space = {str(row.get("name")): row for row in (work.get("parameter_space") or [])
                 if isinstance(row, dict) and row.get("name")}
        for row in work.get("recommendations") or []:
            if not isinstance(row, dict):
                continue
            for name, value in (row.get("parameters") or {}).items():
                bounds = (space.get(str(name)) or {}).get("range")
                if not (isinstance(bounds, list) and len(bounds) == 2):
                    continue
                try:
                    numeric = float(value)
                    low, high = float(bounds[0]), float(bounds[1])
                except (TypeError, ValueError):
                    continue
                if numeric < low or numeric > high:
                    problems.append(
                        "recommendation outside feasible range: %s=%s [%s, %s]" % (
                            name, value, low, high))
        if "options" in work:
            if not work.get("dimensions"):
                problems.append("option comparison lacks dimensions")
            for option in work.get("options") or []:
                if isinstance(option, dict) and not option.get("scores"):
                    problems.append("option %s lacks scores" % option.get("id", "?"))
            scoring = work.get("scoring") if isinstance(work.get("scoring"), dict) else {}
            weights = scoring.get("weights") if isinstance(scoring.get("weights"), dict) else {}
            if weights:
                try:
                    total = sum(float(x) for x in weights.values())
                    if abs(total - 1.0) > 1e-6:
                        problems.append("comparison weights do not sum to 1: %s" % total)
                except (TypeError, ValueError):
                    problems.append("comparison weights are not numeric")
        for observation in work.get("observations") or []:
            if isinstance(observation, dict) and not observation.get("evidence"):
                problems.append("observation %s lacks evidence" % observation.get("id", "?"))
        for interpretation in work.get("interpretations") or []:
            if isinstance(interpretation, dict) and not interpretation.get("type"):
                warnings.append("interpretation lacks observation/inference type")
    return problems, warnings


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
    empty = [field for field in required if field in work and not work.get(field)]
    problems = ["missing required field: %s" % field for field in missing]
    problems.extend("empty required field: %s" % field for field in empty)
    work_case = work.get("case_id")
    if work_case and str(work_case) != ctx.case_id:
        problems.append("work case_id mismatch: %r != %r" % (work_case, ctx.case_id))
    track_problems, track_warnings = _track_checks(_config(ctx).get("family"), work)
    problems.extend(track_problems)
    for warning in track_warnings:
        ctx.result.uncertainty("track_check", warning,
                               "task-specific field is incomplete",
                               "fill the field described in SKILL.md and rerun finalize")
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

    declared = _output_files(ctx)
    for name in declared:
        path = _resolve_output(ctx, name)
        if not path.exists():
            problems.append("declared output missing: %s" % name)
        elif path.is_file() and path.stat().st_size == 0:
            problems.append("declared output is empty: %s" % name)
    if deliverable_path.exists() and deliverable_path.is_file() \
            and deliverable_path.stat().st_size == 0:
        problems.append("deliverable is empty: %s" % deliverable_name)
    code_out = ctx.output / "analysis-code.py"
    code_work = ctx.work / "analysis.py"
    if _config(ctx).get("require_code") and code_out.is_file() and code_work.is_file():
        import hashlib
        if hashlib.sha256(code_out.read_bytes()).hexdigest() !=                 hashlib.sha256(code_work.read_bytes()).hexdigest():
            problems.append("output/analysis-code.py does not match the executed work/analysis.py")
    if _config(ctx).get("run_python") and not (ctx.work / "analysis.py").is_file():
        if _config(ctx).get("require_code"):
            problems.append("work/analysis.py is required but missing")
        else:
            ctx.result.uncertainty(
                "analysis_code", "work/analysis.py is missing",
                "the analysis result was not produced by an executable script",
                "write work/analysis.py and rerun finalize")
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
    merged_uncertainties = list(ctx.result.data.get("uncertainties") or [])
    for item in work.get("uncertainties") or []:
        if isinstance(item, dict):
            merged_uncertainties.append(item)
        else:
            merged_uncertainties.append({
                "item": str(item), "reason": "reported in the work file",
                "impact": "see the work file", "action": "review the source",
            })
    quote_claims = {}
    _quote_claim_map(work, quote_claims)
    # Surface the task-specific required fields at the top level of result.json
    # as well as inside the work file.  This keeps the original
    # output_contract keys (e.g. queries/candidates/items/results) inspectable
    # without inventing content.
    for field in required:
        if field in work and field not in ("evidence",):
            ctx.result.data[field] = work.get(field)
    payload = ctx.result.finalize(
        status,
        deliverable=deliverable_path if deliverable_path.exists() else None,
        result={"summary": summary, "mode": _config(ctx).get("mode"),
                "required_fields": required, "missing_fields": missing,
                "evidence_count": len(evidence), "execution": execution,
                "artifacts": [str(p.relative_to(ctx.output)) for p in artifacts
                              if p.is_relative_to(ctx.output)]},
        claims=claims,
        preserved_facts=preserved,
        uncertainties=merged_uncertainties,
        evidence_map=[{"claim_id": quote_claims.get(e.get("quote", ""), ("evidence-%d" % i, ""))[0],
                       "claim": quote_claims.get(e.get("quote", ""), ("", ""))[1],
                       "source": {"path": e.get("path"), "locator": e.get("locator")},
                       "quote": e.get("quote"), "supports": "source"}
                      for i, e in enumerate(evidence, 1)],
    )
    # Contract coverage is evaluated after finalize has populated the
    # canonical fields.  A missing original output_contract key is a real
    # partial result, not a silent success.
    uncovered = [row.get("field") for row in payload.get("contract_coverage", [])
                 if not row.get("present")]
    if uncovered:
        message = "output_contract fields not covered: %s" % ", ".join(
            str(x) for x in uncovered)
        payload["status"] = "partial"
        payload["task_status"] = "partial"
        payload.setdefault("uncertainties", []).append({
            "item": "output_contract",
            "reason": message,
            "impact": "the original output_contract is not fully covered",
            "action": "write same-named artifacts or map the contract keys",
        })
        payload["uncertainty"] = payload["uncertainties"]
        S.write_json(ctx.output / "result.json", payload)
    return payload
