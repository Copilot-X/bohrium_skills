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
    readable = any(S.doc_text(d).strip() or d.get("data") for d in ctx.docs)
    has_materials = any(row.get("role") == "material" for row in (ctx.inv or []))
    if not readable and not has_materials:
        ctx.result.change("prepare", "input", "no material files")
        ctx.result.uncertainty("materials", "no material files were supplied",
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
    if not readable:
        ctx.result.uncertainty(
            "materials", "material files exist but no text/JSON/table could be decoded",
            "the skill must work from page/image inspection or honest partial output",
            "record unreadable regions and avoid inventing content")
        return ctx.provisional(
            reason="material files exist but no text could be decoded; "
                   "continue with page/image inspection and mark unreadable regions")
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


# ---------------------------------------------------------------------------
# classification quality gate (mode == "classification" only)
# ---------------------------------------------------------------------------

CLASSIFY_DEFAULT_THRESHOLD = 0.7


def _classify_as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _classify_group_ids(group):
    if isinstance(group, dict):
        for key in ("items", "ids", "members", "item_ids", "files", "paths"):
            if isinstance(group.get(key), list):
                return [str(value) for value in group[key]]
        # Common duplicate/version group shapes: kept+archived, current+superseded.
        ids = []
        for key in ("kept", "current", "primary"):
            value = group.get(key)
            if isinstance(value, str) and value.strip():
                ids.append(value.strip())
        for key in ("archived", "superseded", "duplicates", "versions", "others"):
            value = group.get(key)
            if isinstance(value, list):
                ids.extend(str(item) for item in value if str(item).strip())
        return ids
    if isinstance(group, list):
        return [str(value) for value in group]
    return []


CLASSIFY_SUPPORT_NAMES = {
    "case.json", "task.md", "task-note.md", "taxonomy.md", "manifest.md",
    "readme.md", "instructions.md",
}


def _classify_input_paths(ctx):
    """Files that need a classification decision.

    Task metadata, a taxonomy definition and a corpus manifest are inputs to
    the workflow, not items to be filed.  Excluding them keeps both synthetic
    fixtures and official cases from being forced to classify their own rules.
    """
    paths = []
    for row in ctx.inv or []:
        if row.get("role") != "material" or not row.get("path"):
            continue
        path = str(row.get("path") or "")
        if Path(path).name.lower() in CLASSIFY_SUPPORT_NAMES:
            continue
        paths.append(path)
    return sorted(paths)


def _classify_output_json(ctx, name):
    path = ctx.output / name
    return S.load_json(path, {}) if path.is_file() else None


def _classification_audit(ctx, work):
    """Checks required by the classification workorder; never chooses labels."""
    problems, warnings, checks = [], [], {}
    if not isinstance(work, dict) or not work:
        return {"ok": False,
                "problems": ["work/library-plan.json is missing or empty"],
                "warnings": [], "checks": {"plan_present": False}}
    taxonomy = work.get("taxonomy") if isinstance(work.get("taxonomy"), dict) else {}
    categories = [str(value).strip() for value in
                  _classify_as_list(taxonomy.get("categories")) if str(value).strip()]
    criteria = taxonomy.get("criteria") if isinstance(taxonomy.get("criteria"), dict) else {}
    threshold = taxonomy.get("threshold", CLASSIFY_DEFAULT_THRESHOLD)
    try:
        threshold = float(threshold)
        if not 0.0 <= threshold <= 1.0:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("taxonomy.threshold must be a number in [0, 1]")
        threshold = CLASSIFY_DEFAULT_THRESHOLD
    if not categories:
        problems.append("taxonomy.categories is empty")
    for category in categories:
        if not str(criteria.get(category) or "").strip():
            problems.append("taxonomy.criteria is missing for category: %s" % category)

    input_paths = _classify_input_paths(ctx)
    by_path, by_id = {}, {}
    items = work.get("items") if isinstance(work.get("items"), list) else []
    if not items:
        problems.append("items is missing or empty")
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            problems.append("item %d is not an object" % index)
            continue
        iid = str(item.get("id") or item.get("item_id") or
                  item.get("file_id") or "I%03d" % index)
        path = str(item.get("path") or item.get("file") or "")
        category = str(item.get("category") or "")
        if iid in by_id:
            problems.append("duplicate item id: %s" % iid)
        by_id[iid] = item
        if not path:
            problems.append("item %s lacks path" % iid)
        elif path in by_path:
            problems.append("input file classified more than once: %s" % path)
        by_path[path] = item
        if not category:
            problems.append("item %s lacks category" % iid)
        elif category not in categories:
            problems.append("item %s category is not in taxonomy: %s" % (iid, category))
        if not str(item.get("rationale") or item.get("reason") or "").strip():
            problems.append("item %s lacks rationale" % iid)
        if not (item.get("hash") or item.get("sha256")):
            problems.append("item %s lacks content hash" % iid)
        if not item.get("evidence"):
            problems.append("item %s lacks evidence" % iid)
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            problems.append("item %s confidence is missing or not numeric" % iid)
            confidence = 0.0
        if not 0.0 <= confidence <= 1.0:
            problems.append("item %s confidence out of [0, 1]" % iid)
        if "review" not in item:
            warnings.append("item %s lacks an explicit review flag" % iid)

    missing = sorted(set(input_paths) - set(by_path))
    unknown = sorted(set(by_path) - set(input_paths))
    for path in missing:
        problems.append("input file not classified: %s" % path)
    for path in unknown:
        problems.append("classified path is not an input file: %s" % path)
    no_result = bool(work.get("no_result"))
    if not input_paths and not no_result:
        problems.append("input has no material files; set no_result=true and explain")
    if input_paths and no_result:
        problems.append("no_result=true but %d input files exist" % len(input_paths))

    queue = work.get("review_queue")
    if queue is None:
        problems.append("review_queue is missing")
        queue = []
    if not isinstance(queue, list):
        problems.append("review_queue is not a list")
        queue = []
    queue_ids = set()
    for entry in queue:
        if isinstance(entry, str):
            qid = entry
        elif isinstance(entry, dict):
            qid = str(entry.get("item_id") or entry.get("id") or
                      entry.get("file_id") or entry.get("path") or "")
        else:
            qid = ""
        if not qid:
            problems.append("review_queue entry has no item id")
            continue
        queue_ids.add(qid)
        if qid not in by_id and qid not in by_path:
            problems.append("review_queue references an unknown item: %s" % qid)
    required = set()
    for iid, item in by_id.items():
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        if item.get("review") or confidence < threshold:
            required.add(iid)
    for iid in sorted(required - queue_ids):
        problems.append("low-confidence/boundary item is not in review_queue: %s" % iid)
    for iid, item in by_id.items():
        in_queue = iid in queue_ids or item.get("path") in queue_ids
        if bool(item.get("review")) != in_queue:
            problems.append("item %s review flag and review_queue disagree" % iid)

    review_keys = set()
    for iid, item in by_id.items():
        if item.get("review"):
            review_keys.update({iid, str(item.get("path") or ""),
                                str(item.get("new_name") or "")})
    operations = work.get("operations")
    if operations is None:
        operations = []
    if not isinstance(operations, list):
        problems.append("operations is not a list")
        operations = []
    for index, operation in enumerate(operations, 1):
        if not isinstance(operation, dict):
            problems.append("operation %d is not an object" % index)
            continue
        action = str(operation.get("action") or operation.get("type") or "")
        if not action:
            problems.append("operation %d lacks action" % index)
        if action.lower() in ("move", "rename", "classify_move", "relocate",
                              "copy", "classify_copy"):
            from_path = str(operation.get("from") or operation.get("source") or "")
            to_path = str(operation.get("to") or operation.get("target") or "")
            if not from_path or not to_path:
                problems.append("operation %d move/rename lacks from/to" % index)
            if not operation.get("rollback"):
                problems.append("operation %d move/rename lacks rollback" % index)
            if {from_path, to_path, Path(from_path).name,
                    Path(to_path).name} & review_keys:
                problems.append("operation %d moves/renames a review-queue item" % index)

    for index, group in enumerate(work.get("duplicate_groups") or [], 1):
        ids = _classify_group_ids(group)
        if len(ids) < 2:
            problems.append("duplicate group %d has fewer than 2 items" % index)
        for iid in ids:
            if iid not in by_id:
                problems.append("duplicate group %d references unknown item: %s" %
                                (index, iid))
        hashes = {str(by_id[iid].get("hash") or "").replace("sha256:", "")
                  for iid in ids if iid in by_id}
        hashes.discard("")
        if len(hashes) > 1:
            problems.append("duplicate group %d items do not share a hash" % index)
    for index, group in enumerate(work.get("version_groups") or [], 1):
        ids = _classify_group_ids(group)
        if len(ids) < 2:
            problems.append("version group %d has fewer than 2 items" % index)
        for iid in ids:
            if iid not in by_id:
                problems.append("version group %d references unknown item: %s" %
                                (index, iid))

    results = _classify_output_json(ctx, "library-results.json")
    index_doc = _classify_output_json(ctx, "library-index.json")
    operations_log = _classify_output_json(ctx, "operations.log.json")
    if results is None:
        warnings.append("output/library-results.json is missing (declared output)")
    else:
        output_paths = {str(item.get("path") or "")
                        for item in results.get("items") or []
                        if isinstance(item, dict)}
        if output_paths != set(by_path):
            problems.append("library-results.json items do not match the plan paths")
        output_review = {str(row.get("item_id") or row.get("id") or
                             row.get("file_id") or row.get("path") or "")
                         for row in results.get("review_queue") or []
                         if isinstance(row, dict)}
        if required - output_review:
            problems.append("library-results.json review_queue misses: %s" %
                            ", ".join(sorted(required - output_review)))
        counts = results.get("counts") or {}
        if counts.get("input_files") not in (None, len(input_paths)):
            problems.append("library-results.json input_files count is inconsistent")
    if index_doc is not None:
        index_paths = {str(item.get("path") or "") for item in
                       index_doc.get("items") or [] if isinstance(item, dict)}
        if index_paths and index_paths != set(by_path):
            problems.append("library-index.json items do not match the plan paths")
    if operations_log is not None:
        log_rows = operations_log.get("operations") or []
        if not log_rows:
            warnings.append("operations.log.json has no operations")
        if any(isinstance(row, dict) and row.get("action") == "classify_copy"
               and not row.get("rollback") for row in log_rows):
            problems.append("materialized operation lacks a rollback record")
    if results is not None and results.get("materialized"):
        if not (ctx.output / "library").is_dir():
            problems.append("results claim materialized=true but output/library/ is missing")

    checks.update({
        "taxonomy_categories": len(categories),
        "threshold": threshold,
        "input_files": len(input_paths),
        "classified": len(set(by_path) & set(input_paths)),
        "unclassified": len(missing),
        "review_queue": len(queue_ids),
        "duplicate_groups": len(work.get("duplicate_groups") or []),
        "version_groups": len(work.get("version_groups") or []),
    })
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "checks": checks}


def _classification_contract_value(key, work, results, index_doc, operations_log):
    norm = "".join(ch for ch in str(key).lower()
                   if ch.isalnum() or "一" <= ch <= "鿿")
    if "review" in norm or "复核" in norm:
        return work.get("review_queue") or (results or {}).get("review_queue")
    if "operation" in norm or "操作" in norm or norm.endswith("log"):
        return operations_log or work.get("operations")
    if "index" in norm or "索引" in norm:
        return index_doc
    if "taxonomy" in norm or "分类体系" in norm or "类别" in norm:
        return work.get("taxonomy")
    if "classification" in norm or "library" in norm or "分类结果" in norm \
            or "result" in norm or "结果" in norm:
        return results or (work.get("items") and {"items": work.get("items")})
    if "item" in norm or "条目" in norm or "文件" in norm:
        return work.get("items")
    if "evidence" in norm or "证据" in norm:
        return work.get("evidence")
    if "uncertain" in norm or "不确定" in norm:
        return work.get("uncertainties")
    return None


def _apply_classification_contract(payload, ctx, work, audit):
    results = _classify_output_json(ctx, "library-results.json")
    index_doc = _classify_output_json(ctx, "library-index.json")
    operations_log = _classify_output_json(ctx, "operations.log.json")
    contract = ctx.contract if isinstance(ctx.contract, dict) else {}
    coverage = []
    for key in contract:
        present = key in payload and payload[key] not in (None, "", [], {})
        if not present:
            value = _classification_contract_value(
                key, work, results, index_doc, operations_log)
            if value not in (None, "", [], {}):
                payload[key] = value
                present = True
        coverage.append({"field": key, "present": present})
    if contract:
        payload["contract_coverage"] = coverage
    uncovered = [row["field"] for row in coverage if not row["present"]]
    if not uncovered:
        uncertainties = [row for row in payload.get("uncertainties") or []
                         if not (isinstance(row, dict) and
                                 row.get("item") == "output_contract")]
        payload["uncertainties"] = uncertainties
        payload["uncertainty"] = uncertainties
        validation_problems = [row for row in uncertainties
                               if isinstance(row, dict) and
                               row.get("item") == "validation"]
        deliverable = payload.get("deliverable") or {}
        if (payload.get("status") == "partial" and audit.get("ok") and
                not validation_problems and deliverable.get("exists")):
            success = _config(ctx).get("status_success") or "answered"
            payload["status"] = success
            payload["task_status"] = success
    elif payload.get("status") != "input_error":
        payload["status"] = "partial"
        payload["task_status"] = "partial"
        uncertainties = list(payload.get("uncertainties") or [])
        uncertainties.append({
            "item": "output_contract",
            "reason": "fields not covered: %s" % ", ".join(uncovered),
            "impact": "the original output contract is incomplete",
            "action": "write same-named artifacts or map the keys",
        })
        payload["uncertainties"] = uncertainties
        payload["uncertainty"] = uncertainties
    return payload


def _finalize_classification(ctx, work, payload):
    audit = _classification_audit(ctx, work)
    audit["checked_at"] = S.now_iso()
    S.write_json(ctx.work / "classification-audit.json", audit)
    payload["classification_audit"] = audit
    if audit["problems"]:
        uncertainties = list(payload.get("uncertainties") or [])
        for problem in audit["problems"][:40]:
            uncertainties.append({
                "item": "classification_gate",
                "reason": problem,
                "impact": "the classification result is incomplete or inconsistent",
                "action": "fix work/library-plan.json or the declared outputs, "
                          "rerun classify_library.py, then finalize again",
            })
        payload["uncertainties"] = uncertainties
        payload["uncertainty"] = uncertainties
        if payload.get("status") != "input_error":
            payload["status"] = "partial"
            payload["task_status"] = "partial"
    payload = _apply_classification_contract(payload, ctx, work, audit)
    S.write_json(ctx.output / "result.json", payload)
    return payload


# ---------------------------------------------------------------------------
# result-interpretation quality gate (mode == "result-interpretation")
# ---------------------------------------------------------------------------

INTERPRET_TYPES = {"observation", "inference", "causal", "causal_hypothesis",
                   "association", "hypothesis"}
INTERPRET_STRENGTHS = {"strong", "moderate", "suggestive", "speculative",
                       "weak", "high", "medium", "low"}
CAUSAL_RE = re.compile(
    r"(导致|引起|造成|归因于|驱动|证明了|说明是|决定了|使得|"
    r"\bcaus(?:e|es|ed|ing)\b|\bdriv(?:e|es|en|ing)\b|"
    r"\blead(?:s|ing)?\s+to\b|\bresult(?:s|ed|ing)?\s+in\b|"
    r"\bproves?\b|\battributable\s+to\b|\bdue\s+to\b)", re.IGNORECASE)
NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _numbers_in(value, out):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in ("run_id", "id", "name", "unit", "path",
                                    "metric", "statement", "label"):
                continue
            _numbers_in(item, out)
    elif isinstance(value, list):
        for item in value:
            _numbers_in(item, out)
    elif isinstance(value, (int, float)):
        out.add(round(float(value), 6))
    elif isinstance(value, str):
        for token in NUMBER_RE.findall(value):
            try:
                out.add(round(float(token), 6))
            except ValueError:
                pass


def _number_present(value, work, ctx):
    if isinstance(value, (int, float)):
        target = round(float(value), 6)
    else:
        match = NUMBER_RE.search(str(value or ""))
        if not match:
            return False
        try:
            target = round(float(match.group()), 6)
        except ValueError:
            return False
    found = set()
    _numbers_in(work.get("observations"), found)
    _numbers_in(work.get("results"), found)
    _numbers_in(work.get("data_refs"), found)
    if target in found:
        return True
    text_numbers = set()
    for doc in ctx.docs or []:
        for token in NUMBER_RE.findall(S.doc_text(doc)):
            try:
                text_numbers.add(round(float(token), 6))
            except ValueError:
                pass
    return target in text_numbers


def _result_interpretation_audit(ctx, work):
    problems, warnings, checks = [], [], {}
    if not isinstance(work, dict) or not work:
        return {"ok": False,
                "problems": ["work/experiment-plan.json is missing or empty"],
                "warnings": [], "checks": {"plan_present": False}}
    observations = work.get("observations")
    interpretations = work.get("interpretations")
    if not isinstance(observations, list) or not observations:
        if not work.get("no_result"):
            problems.append("observations is missing or empty")
        observations = []
    if not isinstance(interpretations, list) or not interpretations:
        problems.append("interpretations is missing or empty")
        interpretations = []
    obs_by_id = {}
    for index, observation in enumerate(observations, 1):
        if not isinstance(observation, dict):
            problems.append("observation %d is not an object" % index)
            continue
        oid = str(observation.get("id") or observation.get("observation_id") or
                  "O%03d" % index)
        obs_by_id[oid] = observation
        if not str(observation.get("statement") or "").strip():
            problems.append("observation %s lacks statement" % oid)
        if not observation.get("evidence"):
            problems.append("observation %s lacks evidence" % oid)
        statement = str(observation.get("statement") or "")
        if NUMBER_RE.search(statement) and observation.get("value") in (None, ""):
            warnings.append("observation %s mentions a number but has no value field" % oid)
        if observation.get("value") is not None and not str(
                observation.get("unit") or "").strip():
            warnings.append("observation %s value has no unit" % oid)

    has_causal = False
    for index, interpretation in enumerate(interpretations, 1):
        if not isinstance(interpretation, dict):
            problems.append("interpretation %d is not an object" % index)
            continue
        iid = str(interpretation.get("id") or "I%03d" % index)
        statement = str(interpretation.get("statement") or
                        interpretation.get("text") or "")
        kind = str(interpretation.get("type") or "").lower()
        if not statement:
            problems.append("interpretation %s lacks statement" % iid)
        if kind not in INTERPRET_TYPES:
            problems.append("interpretation %s has invalid type: %s" % (iid, kind))
        if not interpretation.get("evidence"):
            problems.append("interpretation %s lacks evidence" % iid)
        strength = str(interpretation.get("strength") or "").lower()
        causal_text = bool(CAUSAL_RE.search(statement))
        if causal_text or kind in ("causal", "causal_hypothesis"):
            has_causal = True
            if kind not in ("causal", "causal_hypothesis"):
                problems.append("interpretation %s uses causal wording but type=%s"
                                % (iid, kind or "missing"))
            if strength not in INTERPRET_STRENGTHS:
                problems.append("interpretation %s causal claim lacks a valid strength" % iid)
            support = interpretation.get("support")
            exclusion = interpretation.get("exclusion")
            if not support:
                problems.append("interpretation %s causal claim lacks support" % iid)
            if not exclusion:
                problems.append("interpretation %s causal claim lacks exclusion/limits" % iid)
            if not interpretation.get("confounders") and not interpretation.get("alternative"):
                warnings.append("interpretation %s causal claim has no confounder list" % iid)
        elif kind == "observation" and causal_text:
            problems.append("interpretation %s is typed observation but contains causal wording"
                            % iid)

    alternatives = work.get("alternative_explanations") or []
    if has_causal:
        if not isinstance(alternatives, list) or not alternatives:
            problems.append("causal claims require alternative_explanations")
        else:
            for index, alternative in enumerate(alternatives, 1):
                if not isinstance(alternative, dict):
                    problems.append("alternative explanation %d is not an object" % index)
                    continue
                if not str(alternative.get("explanation") or
                           alternative.get("statement") or "").strip():
                    problems.append("alternative explanation %d lacks text" % index)
                status = str(alternative.get("status") or "").lower()
                if status not in ("excluded", "not_excluded", "untested", "supported"):
                    problems.append("alternative explanation %d has invalid status: %s"
                                    % (index, status or "missing"))

    trends = work.get("trends")
    if not isinstance(trends, list) or not trends:
        if not work.get("no_result"):
            problems.append("trends is missing or empty")
        trends = []
    else:
        for index, trend in enumerate(trends, 1):
            if not isinstance(trend, dict):
                problems.append("trend %d is not an object" % index)
                continue
            tid = str(trend.get("id") or "T%03d" % index)
            if not (trend.get("metric") or trend.get("name")):
                problems.append("trend %s lacks metric" % tid)
            direction = str(trend.get("direction") or "").lower()
            if direction not in ("up", "down", "flat", "mixed", "increase",
                                 "decrease", "上升", "下降", "持平", "混合"):
                problems.append("trend %s has invalid direction: %s"
                                % (tid, direction or "missing"))
            if not trend.get("evidence"):
                problems.append("trend %s lacks evidence" % tid)

    anomalies = work.get("anomalies")
    if anomalies is None:
        anomalies = []
    if not isinstance(anomalies, list):
        problems.append("anomalies is not a list")
        anomalies = []
    for index, anomaly in enumerate(anomalies, 1):
        if not isinstance(anomaly, dict):
            problems.append("anomaly %d is not an object" % index)
            continue
        aid = str(anomaly.get("id") or "A%03d" % index)
        oid = str(anomaly.get("observation_id") or anomaly.get("observation") or "")
        if not oid or oid not in obs_by_id:
            problems.append("anomaly %s does not reference an existing observation" % aid)
        if not (anomaly.get("threshold") or anomaly.get("criterion") or
                anomaly.get("rule")):
            problems.append("anomaly %s lacks a threshold/criterion" % aid)
        if anomaly.get("value") is not None and not _number_present(
                anomaly.get("value"), work, ctx):
            problems.append("anomaly %s value is not found in the data/observations" % aid)
        run_id = anomaly.get("run_id") or anomaly.get("sample_id") or ""
        if run_id:
            haystack = json.dumps({"observations": observations,
                                   "results": work.get("results") or [],
                                   "data_refs": work.get("data_refs") or []},
                                  ensure_ascii=False) + "\n" + S.all_text(ctx.docs)
            if str(run_id) not in haystack:
                problems.append("anomaly %s run_id/sample_id is not in the data" % aid)
        if not (anomaly.get("evidence") or anomaly.get("rationale") or oid):
            problems.append("anomaly %s lacks evidence/rationale" % aid)
        if not str(anomaly.get("action") or "").strip():
            warnings.append("anomaly %s lacks an action" % aid)

    recheck = work.get("recheck_steps") or work.get("rechecks") or []
    if not isinstance(recheck, list) or not recheck:
        problems.append("recheck_steps is missing or empty")
        recheck = []
    for index, step in enumerate(recheck, 1):
        if not isinstance(step, dict):
            problems.append("recheck step %d is not an object" % index)
            continue
        sid = str(step.get("id") or "R%03d" % index)
        if not str(step.get("action") or step.get("step") or "").strip():
            problems.append("recheck step %s lacks action" % sid)
        linked = str(step.get("observation_id") or step.get("anomaly_id") or
                     step.get("interpretation_id") or "")
        if linked and linked not in obs_by_id and \
                not any(str(a.get("id") or "") == linked for a in anomalies) and \
                not any(str(i.get("id") or "") == linked for i in interpretations):
            problems.append("recheck step %s references an unknown item: %s" % (sid, linked))
        if not (linked or step.get("evidence")):
            problems.append("recheck step %s lacks observation/evidence link" % sid)
        if not str(step.get("expected") or step.get("expected_outcome") or "").strip():
            warnings.append("recheck step %s lacks an expected outcome" % sid)

    follow = work.get("next_experiments") or work.get("follow_up") or []
    if not isinstance(follow, list) or not follow:
        problems.append("next_experiments is missing or empty")
        follow = []
    for index, experiment in enumerate(follow, 1):
        if not isinstance(experiment, dict):
            problems.append("follow-up %d is not an object" % index)
            continue
        eid = str(experiment.get("id") or "E%03d" % index)
        if not str(experiment.get("experiment") or experiment.get("action") or
                   "").strip():
            problems.append("follow-up %s lacks experiment/action" % eid)
        if not str(experiment.get("purpose") or "").strip():
            problems.append("follow-up %s lacks purpose" % eid)
        linked = str(experiment.get("observation_id") or
                     experiment.get("anomaly_id") or
                     experiment.get("interpretation_id") or "")
        if linked and linked not in obs_by_id and \
                not any(str(a.get("id") or "") == linked for a in anomalies) and \
                not any(str(i.get("id") or "") == linked for i in interpretations):
            problems.append("follow-up %s references an unknown item: %s" % (eid, linked))
        if not (linked or experiment.get("evidence")):
            problems.append("follow-up %s lacks observation/evidence link" % eid)
        if not str(experiment.get("expected_outcome") or
                   experiment.get("decision_rule") or "").strip():
            warnings.append("follow-up %s lacks expected outcome/decision rule" % eid)

    report_path = ctx.output / "experiment-report.md"
    if report_path.is_file():
        report = S.read_text(report_path)
        sections = {
            "trend": ("趋势", "trend"),
            "anomaly": ("异常", "anomal"),
            "cause": ("原因", "causal", "cause"),
            "recheck": ("复核", "recheck", "verification"),
            "follow_up": ("后续", "follow-up", "follow_up", "next experiment"),
        }
        for name, words in sections.items():
            if not any(word.lower() in report.lower() for word in words):
                problems.append("experiment-report.md lacks the %s section" % name)
        if observations and not any(oid in report for oid in obs_by_id):
            problems.append("experiment-report.md does not cite any observation id")
    else:
        warnings.append("experiment-report.md is missing (declared deliverable)")

    # Declared JSON outputs must agree with the work file.
    interpretation_path = ctx.output / "interpretation.json"
    anomalies_path = ctx.output / "anomalies.json"
    follow_path = ctx.output / "follow-up.json"
    interpretation_doc = (S.load_json(interpretation_path)
                          if interpretation_path.is_file() else None)
    anomalies_doc = (S.load_json(anomalies_path)
                     if anomalies_path.is_file() else None)
    follow_doc = S.load_json(follow_path) if follow_path.is_file() else None
    if interpretation_doc is not None:
        out = interpretation_doc.get("interpretations") if isinstance(
            interpretation_doc, dict) else interpretation_doc
        out_ids = {str(row.get("id") or "") for row in out or []
                   if isinstance(row, dict)}
        plan_ids = {str(row.get("id") or "I%03d" % index)
                    for index, row in enumerate(interpretations, 1)
                    if isinstance(row, dict)}
        if out_ids != plan_ids:
            problems.append("interpretation.json does not match the plan interpretations")
    if anomalies_doc is not None:
        out = anomalies_doc.get("anomalies") if isinstance(anomalies_doc, dict) \
            else anomalies_doc
        out_ids = {str(row.get("id") or "") for row in out or []
                   if isinstance(row, dict)}
        plan_ids = {str(row.get("id") or "A%03d" % index)
                    for index, row in enumerate(anomalies, 1)
                    if isinstance(row, dict)}
        if out_ids != plan_ids:
            problems.append("anomalies.json does not match the plan anomalies")
    if follow_doc is not None:
        out = follow_doc.get("next_experiments") if isinstance(follow_doc, dict) \
            else follow_doc
        if not isinstance(out, list):
            out = follow_doc.get("follow_up") if isinstance(follow_doc, dict) else out
        out_ids = {str(row.get("id") or "") for row in out or []
                   if isinstance(row, dict)}
        plan_ids = {str(row.get("id") or "E%03d" % index)
                    for index, row in enumerate(follow, 1)
                    if isinstance(row, dict)}
        if out_ids != plan_ids:
            problems.append("follow-up.json does not match the plan next_experiments")

    checks.update({
        "observations": len(observations),
        "interpretations": len(interpretations),
        "causal_claims": sum(1 for row in interpretations
                             if isinstance(row, dict) and
                             (str(row.get("type") or "").lower() in
                              ("causal", "causal_hypothesis") or
                              CAUSAL_RE.search(str(row.get("statement") or "")))),
        "trends": len(trends),
        "anomalies": len(anomalies),
        "recheck_steps": len(recheck),
        "next_experiments": len(follow),
    })
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "checks": checks}


def _interpretation_contract_value(key, work, results, index_doc, operations_log):
    norm = "".join(ch for ch in str(key).lower()
                   if ch.isalnum() or "一" <= ch <= "鿿")
    if "interpret" in norm or "解释" in norm or "解读" in norm:
        return work.get("interpretations")
    if "anomal" in norm or "异常" in norm:
        return work.get("anomalies")
    if "follow" in norm or "next" in norm or "后续" in norm:
        return work.get("next_experiments") or work.get("follow_up")
    if "trend" in norm or "趋势" in norm:
        return work.get("trends")
    if "recheck" in norm or "复核" in norm or "核查" in norm:
        return work.get("recheck_steps") or work.get("rechecks")
    if "observation" in norm or "观测" in norm:
        return work.get("observations")
    if "cause" in norm or "原因" in norm:
        return work.get("interpretations")
    if "report" in norm or "报告" in norm:
        return "experiment-report.md"
    if "evidence" in norm or "证据" in norm:
        return work.get("evidence")
    if "uncertain" in norm or "不确定" in norm:
        return work.get("uncertainties")
    return None


def _finalize_result_interpretation(ctx, work, payload):
    audit = _result_interpretation_audit(ctx, work)
    audit["checked_at"] = S.now_iso()
    S.write_json(ctx.work / "interpretation-audit.json", audit)
    payload["interpretation_audit"] = audit
    if audit["problems"]:
        uncertainties = list(payload.get("uncertainties") or [])
        for problem in audit["problems"][:40]:
            uncertainties.append({
                "item": "interpretation_gate",
                "reason": problem,
                "impact": "the result interpretation is incomplete or overclaims",
                "action": "fix work/experiment-plan.json and the declared outputs, "
                          "then rerun finalize",
            })
        payload["uncertainties"] = uncertainties
        payload["uncertainty"] = uncertainties
        if payload.get("status") != "input_error":
            payload["status"] = "partial"
            payload["task_status"] = "partial"
    contract = ctx.contract if isinstance(ctx.contract, dict) else {}
    coverage = []
    for key in contract:
        present = key in payload and payload[key] not in (None, "", [], {})
        if not present:
            value = _interpretation_contract_value(key, work, None, None, None)
            if value not in (None, "", [], {}):
                payload[key] = value
                present = True
        coverage.append({"field": key, "present": present})
    if contract:
        payload["contract_coverage"] = coverage
    uncovered = [row["field"] for row in coverage if not row["present"]]
    if not uncovered:
        uncertainties = [row for row in payload.get("uncertainties") or []
                         if not (isinstance(row, dict) and
                                 row.get("item") == "output_contract")]
        payload["uncertainties"] = uncertainties
        payload["uncertainty"] = uncertainties
        validation_problems = [row for row in uncertainties
                               if isinstance(row, dict) and
                               row.get("item") == "validation"]
        deliverable = payload.get("deliverable") or {}
        if (payload.get("status") == "partial" and audit.get("ok") and
                not validation_problems and deliverable.get("exists")):
            success = _config(ctx).get("status_success") or "completed"
            payload["status"] = success
            payload["task_status"] = success
    elif payload.get("status") != "input_error":
        payload["status"] = "partial"
        payload["task_status"] = "partial"
        uncertainties = list(payload.get("uncertainties") or [])
        uncertainties.append({
            "item": "output_contract",
            "reason": "fields not covered: %s" % ", ".join(uncovered),
            "impact": "the original output contract is incomplete",
            "action": "write same-named artifacts or map the keys",
        })
        payload["uncertainties"] = uncertainties
        payload["uncertainty"] = uncertainties
    S.write_json(ctx.output / "result.json", payload)
    return payload


def _materialize_declared_outputs(ctx, work, deliverable_path, work_name):
    """Derive declared artifacts from real work-file content when the model
    omitted a file.  This never invents new facts: it only re-serializes
    values the model already wrote, and records the derivation in change_log.
    """
    changed = []
    mode = str(_config(ctx).get("mode") or "")
    for name in _output_files(ctx):
        path = _resolve_output(ctx, name)
        if path.exists() and (path.is_dir() or path.stat().st_size > 0):
            continue
        if path.name == "result.json":
            continue
        stem = path.stem
        keys = [stem, stem.replace("-", "_"), stem.replace("_", "-")]
        value = None
        for key in keys:
            if key in work and work.get(key) not in (None, "", [], {}):
                value = work.get(key)
                break
        if value is None:
            mode_defaults = {
                "review": ["findings", "claim_source_matrix", "checks"],
                "search": ["queries", "candidates", "items"],
                "library": ["items", "taxonomy", "review_queue"],
                "analysis": ["results", "analysis", "steps"],
                "experiment": ["parameters", "recommendations", "risks"],
                "writing": ["claims", "evidence", "preserved_facts"],
                "reading": ["items", "claims", "evidence"],
            }
            for key in mode_defaults.get(mode, []):
                if work.get(key) not in (None, "", [], {}):
                    value = work.get(key)
                    break
        suffix = path.suffix.lower()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if suffix == ".json":
                if value is None:
                    value = {"case_id": ctx.case_id, "available_fields":
                             [k for k in ("summary", "findings", "items",
                                          "results", "evidence", "uncertainties")
                              if work.get(k) not in (None, "", [], {})],
                             "note": "derived from the work file; no new facts"}
                payload = value if isinstance(value, (dict, list)) else {stem: value}
                if isinstance(payload, dict):
                    payload = dict(payload)
                    payload.setdefault("case_id", ctx.case_id)
                    payload.setdefault("derived_from", work_name)
                S.write_json(path, payload)
            elif suffix in (".md", ".txt"):
                summary = work.get("summary") or work.get("answer") or ""
                lines = ["# %s" % (_config(ctx).get("title") or ctx.case_id), "",
                         "> 由 work 文件内容生成；未新增材料外事实。", "",
                         "## 摘要", "", str(summary) or "(work summary is empty)", ""]
                for label, key in (("发现", "findings"), ("候选项", "candidates"),
                                   ("条目", "items"), ("结果", "results"),
                                   ("证据", "evidence"),
                                   ("不确定项", "uncertainties")):
                    rows = work.get(key)
                    if not isinstance(rows, list) or not rows:
                        continue
                    lines += ["## %s" % label, ""]
                    for row in rows[:100]:
                        if isinstance(row, dict):
                            text = (row.get("statement") or row.get("text")
                                    or row.get("title") or row.get("label")
                                    or row.get("reason") or json.dumps(
                                        row, ensure_ascii=False)[:500])
                            lines.append("- %s" % text)
                        else:
                            lines.append("- %s" % row)
                    lines.append("")
                S.write_text(path, "\n".join(lines).rstrip() + "\n")
            elif suffix == ".csv":
                rows = value if isinstance(value, list) else work.get("rows")
                if not isinstance(rows, list) or not rows:
                    continue
                if all(isinstance(row, dict) for row in rows):
                    import csv
                    fields = list(dict.fromkeys(
                        key for row in rows for key in row.keys()))
                    with path.open("w", encoding="utf-8", newline="") as fh:
                        writer = csv.DictWriter(fh, fieldnames=fields)
                        writer.writeheader()
                        writer.writerows(rows)
                else:
                    import csv
                    with path.open("w", encoding="utf-8", newline="") as fh:
                        writer = csv.writer(fh)
                        writer.writerows(rows)
            else:
                continue
            changed.append(name)
            ctx.result.change("materialize", "output",
                              "derived %s from %s" % (name, work_name))
        except (OSError, TypeError, ValueError) as exc:
            ctx.result.uncertainty(
                "materialize", "could not derive %s: %s" % (name, exc),
                "the declared artifact remains missing",
                "write the artifact explicitly and rerun finalize")
    # A missing Markdown deliverable can be built from the same work content.
    if deliverable_path and not deliverable_path.exists() \
            and deliverable_path.suffix.lower() in (".md", ".txt"):
        try:
            summary = work.get("summary") or work.get("answer") or ""
            lines = ["# %s" % (_config(ctx).get("title") or ctx.case_id), "",
                     "> 由 work 文件内容生成；未新增材料外事实。", "",
                     "## 摘要", "", str(summary) or "(work summary is empty)", ""]
            for label, key in (("发现", "findings"), ("候选项", "candidates"),
                               ("条目", "items"), ("结果", "results"),
                               ("证据", "evidence"),
                               ("不确定项", "uncertainties")):
                rows = work.get(key)
                if not isinstance(rows, list) or not rows:
                    continue
                lines += ["## %s" % label, ""]
                for row in rows[:100]:
                    if isinstance(row, dict):
                        text = (row.get("statement") or row.get("text")
                                or row.get("title") or row.get("label")
                                or row.get("reason") or json.dumps(
                                    row, ensure_ascii=False)[:500])
                        lines.append("- %s" % text)
                    else:
                        lines.append("- %s" % row)
                lines.append("")
            deliverable_path.parent.mkdir(parents=True, exist_ok=True)
            S.write_text(deliverable_path, "\n".join(lines).rstrip() + "\n")
            changed.append(deliverable_path.name)
            ctx.result.change("materialize", "deliverable",
                              "derived %s from %s" % (
                                  deliverable_path.name, work_name))
        except (OSError, TypeError, ValueError):
            pass
    return changed


def _fallback_work(ctx):
    """Build an honest, quote-only draft when the model wrote no work file.

    It does not answer the task.  It preserves readable material excerpts,
    labels them as extracted evidence, and gives finalize enough structure to
    emit a real partial deliverable instead of an empty envelope.
    """
    evidence, findings = [], []
    for doc in ctx.docs:
        text = S.doc_text(doc)
        path = doc.get("rel_path") or doc.get("path") or ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines[:5], 1):
            if len(line) > 400:
                line = line[:400]
            item = {"path": path, "locator": "L%d" % index, "quote": line}
            evidence.append(item)
            findings.append({
                "id": "E%03d" % (len(findings) + 1),
                "statement": line,
                "type": "extracted_quote",
                "evidence": item,
            })
    if not evidence:
        return {}
    config = _config(ctx)
    work = {
        "case_id": ctx.case_id,
        "mode": config.get("mode") or config.get("family"),
        "summary": "材料已读取，但模型未完成题面要求的实质性回答；"
                   "以下仅为逐字摘录，不能替代正式结论。",
        "findings": findings,
        "evidence": evidence,
        "uncertainties": [{
            "item": "model_work_missing",
            "reason": "no model-authored work file was found",
            "impact": "the task-specific answer is not available",
            "action": "write the work file described in SKILL.md and rerun finalize",
        }],
    }
    for field in config.get("required_fields") or []:
        if field in work:
            continue
        work[field] = [] if field not in ("analysis", "taxonomy",
                                          "reporting_checklist") else {}
    return work


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
        work = _fallback_work(ctx)
        if not work:
            ctx.result.change("finalize", "output", "no work file and no deliverable")
            ctx.result.uncertainty("deliverable", "the model-authored work file is missing",
                                   "no substantive answer", "write %s" % work_name)
            payload = ctx.result.finalize("partial")
            if _config(ctx).get("mode") == "classification":
                payload = _finalize_classification(ctx, work, payload)
            elif _config(ctx).get("mode") == "result-interpretation":
                payload = _finalize_result_interpretation(ctx, work, payload)
            return payload
        ctx.result.change("finalize", "work",
                          "fallback excerpt draft: no model work file")
        ctx.result.uncertainty(
            "model_work_missing",
            "the model-authored work file was missing; only verbatim excerpts were kept",
            "the deliverable is a partial draft, not a task answer",
            "write %s and rerun finalize" % work_name)

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

    _materialize_declared_outputs(ctx, work, deliverable_path, work_name)

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
    # Preserve original output_contract keys when the work file already uses the
    # same key (e.g. step_plan, intermediate_products, final_results).  This is
    # a copy, not a new claim.
    contract = ctx.contract if isinstance(ctx.contract, dict) else {}
    contract_aliases = {
        "step_plan": "steps",
        "intermediate_products": "intermediate_results",
        "final_results": "results",
        "review_report": "findings",
        "search_results": "candidates",
        "extraction_results": "results",
    }
    for key in contract:
        value = work.get(key)
        if value in (None, "", [], {}) and key in contract_aliases:
            value = work.get(contract_aliases[key])
        if value not in (None, "", [], {}) and key not in ctx.result.data:
            ctx.result.data[key] = value
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
    if _config(ctx).get("mode") == "classification":
        payload = _finalize_classification(ctx, work, payload)
    elif _config(ctx).get("mode") == "result-interpretation":
        payload = _finalize_result_interpretation(ctx, work, payload)
    return payload
