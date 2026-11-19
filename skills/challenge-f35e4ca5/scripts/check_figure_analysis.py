#!/usr/bin/env python3
"""Audit a scientific-figure analysis result (optional, never a delivery gate).

Usage:
    python3 check_figure_analysis.py --result /app/output/result.json \
        --input /input/case.json --json
    python3 check_figure_analysis.py --self-test --json

Checks
  * case_id exists and matches the input when available
  * top-level reading aliases: status/result/claims/uncertainties
  * every figure declares figure_mode and is handled by the matching rules
  * quantitative_chart: x/y axes, series, statistics, observations
  * visual_figure: no axes/series points, no plot table, no quantitative claim
  * multi_panel: panel_modes covers every panel
  * question_figure_map: every input question has a figure_id + panel locator
  * panel_coverage: every known figure/panel is covered (or explains why not)
  * unreadable[] entries carry a valid error_class
  * answers: one per input question, non-empty answer, evidence on every claim
  * estimated readings carry read_error; error_bar/n/significance vocabulary

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable input.
The skill works without this script; failures are reported, not fatal.
"""
import argparse
import json
import sys
from pathlib import Path

ERROR_BARS = {"SD", "SEM", "CI", "IQR", "min-max", "none", "not_provided",
              "not_applicable", "unknown"}
KINDS = {"reported", "derived", "inferred", "visual"}
STATUSES = {"answered", "partial", "insufficient_evidence"}
TOP_STATUSES = STATUSES | {"input_error"}
CONTENT_KINDS = {"text", "visual", "mixed"}
EXTRACTION_MODES = {"deterministic", "visual_description", "hybrid"}
FIGURE_MODES = {"quantitative_chart", "visual_figure", "multi_panel"}
PANEL_MODES = {"quantitative_chart", "visual_figure"}
ERROR_CLASSES = {"decode_failed", "missing_file", "unsupported_format",
                 "no_pixels", "not_provided", "other"}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def check_axis(axis, name, problems, warnings, require_unit=True):
    if not isinstance(axis, dict):
        problems.append("axis %s is not an object" % name)
        return
    if not axis.get("label"):
        problems.append("axis %s missing label" % name)
    if require_unit and not axis.get("unit") and not axis.get("unit_missing"):
        warnings.append("axis %s missing unit (set unit_missing=true if unknown)" % name)
    scale = str(axis.get("scale") or "").lower()
    if scale not in ("linear", "log", "log10", "symlog", "categorical", "ordinal"):
        problems.append("axis %s has invalid scale: %s" % (name, axis.get("scale")))
    rng = axis.get("range")
    if rng is not None and not (isinstance(rng, list) and len(rng) == 2):
        problems.append("axis %s range must be [min,max]" % name)


def check_statistics(stats, problems, warnings, allow_not_applicable=True):
    if not isinstance(stats, dict):
        problems.append("statistics is not an object")
        return
    error = stats.get("error_bar")
    if error is not None and str(error) not in ERROR_BARS:
        warnings.append("unknown error_bar value: %s" % error)
    if str(error) == "not_applicable" and not allow_not_applicable:
        warnings.append("error_bar not_applicable on a quantitative chart")
    n = stats.get("n")
    if n is not None and (not isinstance(n, int) or n < 0):
        problems.append("statistics.n must be a non-negative integer or null")
    for i, item in enumerate(stats.get("significance") or []):
        if not isinstance(item, dict):
            problems.append("significance[%d] is not an object" % i)
            continue
        if not item.get("mark"):
            warnings.append("significance[%d] missing mark" % i)
        if not item.get("comparison"):
            warnings.append("significance[%d] missing comparison" % i)


def check_evidence(ev, where, locator_ids, problems, warnings):
    if isinstance(ev, list):
        if not ev:
            warnings.append("%s has empty evidence list" % where)
        for i, item in enumerate(ev):
            check_evidence(item, "%s[%d]" % (where, i), locator_ids,
                           problems, warnings)
        return
    if not isinstance(ev, dict):
        problems.append("%s evidence is not an object" % where)
        return
    fid = ev.get("figure_id")
    if not fid:
        problems.append("%s evidence missing figure_id" % where)
    elif locator_ids and str(fid) not in locator_ids:
        problems.append("%s evidence points at unknown figure %s" % (where, fid))
    if not ev.get("panel"):
        warnings.append("%s evidence missing panel" % where)
    if not ev.get("quote"):
        warnings.append("%s evidence missing quote" % where)


def observation_panel(obs):
    ev = obs.get("evidence")
    if isinstance(ev, list) and ev:
        ev = ev[0]
    if isinstance(ev, dict):
        return ev.get("panel")
    return None


def check_observations(fig, fid, mode, locator_ids, problems, warnings,
                       panel_modes=None):
    observations = fig.get("observations") or []
    if not isinstance(observations, list):
        problems.append("figure %s observations must be a list" % fid)
        return
    if not observations:
        warnings.append("figure %s has no observations[]" % fid)
    for j, obs in enumerate(observations):
        where = "figure %s observation[%d]" % (fid, j)
        if not isinstance(obs, dict):
            problems.append("%s is not an object" % where)
            continue
        if not obs.get("text"):
            problems.append("%s missing text" % where)
        kind = obs.get("kind")
        if kind not in KINDS:
            warnings.append("%s kind unusual: %s" % (where, kind))
        if obs.get("estimated") and not obs.get("read_error"):
            problems.append("%s estimated without read_error" % where)
        obs_mode = mode
        if panel_modes:
            panel = str(observation_panel(obs))
            obs_mode = panel_modes.get(panel)
        if obs_mode == "visual_figure":
            if obs.get("quantitative") is True:
                problems.append("%s is quantitative on a visual panel" % where)
            if kind not in ("visual", "reported"):
                warnings.append("%s kind should be visual/reported on a visual panel"
                                % where)
            if obs.get("estimated") is True:
                problems.append("%s uses estimated numeric reading on a visual panel"
                                % where)
        check_evidence(obs.get("evidence"), where, locator_ids, problems, warnings)


def check_quantitative(fig, fid, locator_ids, problems, warnings):
    axes = fig.get("axes") or {}
    if not isinstance(axes, dict):
        problems.append("figure %s axes is not an object" % fid)
    else:
        for name in ("x", "y"):
            if name not in axes:
                problems.append("figure %s missing %s axis" % (fid, name))
            else:
                check_axis(axes[name], name, problems, warnings)
    series = fig.get("series") or []
    if not isinstance(series, list) or not series:
        warnings.append("figure %s has no series[]" % fid)
    else:
        for i, item in enumerate(series):
            if not isinstance(item, dict):
                problems.append("figure %s series[%d] is not an object" % (fid, i))
    check_statistics(fig.get("statistics") or {}, problems, warnings)
    check_observations(fig, fid, "quantitative_chart", locator_ids,
                       problems, warnings)


def check_visual(fig, fid, locator_ids, problems, warnings):
    axes = fig.get("axes")
    if isinstance(axes, dict) and (axes.get("x") or axes.get("y")):
        warnings.append("visual figure %s declares axes; remove numeric axes" % fid)
    series = fig.get("series") or []
    if isinstance(series, list):
        for i, item in enumerate(series):
            if isinstance(item, dict) and item.get("points"):
                problems.append(
                    "visual figure %s series[%d] has numeric points (fabricated reading)"
                    % (fid, i))
    stats = fig.get("statistics") or {}
    if isinstance(stats, dict):
        check_statistics(stats, problems, warnings, allow_not_applicable=True)
        if stats.get("error_bar") not in (None, "not_applicable", "not_provided"):
            warnings.append("visual figure %s has error_bar=%s"
                            % (fid, stats.get("error_bar")))
    check_observations(fig, fid, "visual_figure", locator_ids, problems, warnings)


def check_multi_panel(fig, fid, locator_ids, problems, warnings):
    panels = fig.get("panels") or []
    if not isinstance(panels, list) or not panels:
        problems.append("multi_panel figure %s needs a non-empty panels[]" % fid)
        panels = []
    modes = fig.get("panel_modes")
    if not isinstance(modes, dict):
        problems.append("multi_panel figure %s missing panel_modes{}" % fid)
    else:
        for panel in panels:
            mode = modes.get(str(panel))
            if mode not in PANEL_MODES:
                problems.append("panel_modes[%s] missing/invalid on figure %s"
                                % (panel, fid))
        for key, mode in modes.items():
            if str(key) not in {str(p) for p in panels}:
                warnings.append("panel_modes has unknown panel %s on %s" % (key, fid))
            if mode not in PANEL_MODES:
                warnings.append("panel_modes[%s] unknown mode %s" % (key, mode))
    check_observations(fig, fid, "multi_panel", locator_ids, problems, warnings,
                       panel_modes=modes if isinstance(modes, dict) else None)


def check_figures(result, input_data, problems, warnings, stats, known_ids=None):
    figures = result.get("figures") or []
    if not isinstance(figures, list):
        problems.append("figures must be a list")
        figures = []
    figure_ids = set()
    evidence_ids = set(known_ids or ())
    figure_modes = {}
    panel_sets = {}
    panel_modes_lookup = {}
    for i, fig in enumerate(figures):
        if not isinstance(fig, dict):
            problems.append("figure[%d] is not an object" % i)
            continue
        fid = fig.get("figure_id")
        if not fid:
            problems.append("figure[%d] missing figure_id" % i)
            continue
        fid = str(fid)
        if fid in figure_ids:
            problems.append("duplicate figure_id: %s" % fid)
        figure_ids.add(fid)
        evidence_ids.add(fid)
        if not fig.get("caption"):
            warnings.append("figure %s missing caption" % fid)
        panels = fig.get("panels") or []
        if not isinstance(panels, list):
            problems.append("figure %s panels must be a list" % fid)
            panels = []
        panel_sets[fid] = [str(p) for p in panels]
        modes = fig.get("panel_modes")
        if isinstance(modes, dict):
            for panel, panel_mode in modes.items():
                panel_modes_lookup[(fid, str(panel))] = panel_mode
        content_kind = fig.get("content_kind")
        if content_kind and content_kind not in CONTENT_KINDS:
            warnings.append("figure %s has unknown content_kind %s" % (fid, content_kind))
        mode = fig.get("extraction_mode")
        if mode and mode not in EXTRACTION_MODES:
            warnings.append("figure %s has unknown extraction_mode %s" % (fid, mode))
        fig_mode = fig.get("figure_mode")
        figure_modes[fid] = fig_mode
        if fig_mode not in FIGURE_MODES:
            problems.append("figure %s missing/invalid figure_mode: %s" % (fid, fig_mode))
            continue
        if fig_mode == "quantitative_chart":
            check_quantitative(fig, fid, evidence_ids, problems, warnings)
        elif fig_mode == "visual_figure":
            check_visual(fig, fid, evidence_ids, problems, warnings)
        else:
            check_multi_panel(fig, fid, evidence_ids, problems, warnings)
    stats["figures"] = len(figures)
    stats["figure_ids"] = sorted(figure_ids)
    return figure_ids, figure_modes, panel_sets, panel_modes_lookup


def check_unreadable(result, input_data, problems, warnings):
    items = result.get("unreadable")
    if items is None:
        items = []
    if not isinstance(items, list):
        problems.append("unreadable must be a list")
        return set()
    unreadable_ids = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append("unreadable[%d] is not an object" % i)
            continue
        fid = item.get("figure_id") or item.get("path")
        if not fid:
            problems.append("unreadable[%d] missing figure_id/path" % i)
        else:
            unreadable_ids.add(str(fid))
        cls = item.get("error_class")
        if cls not in ERROR_CLASSES:
            problems.append("unreadable[%d] invalid error_class: %s" % (i, cls))
    return unreadable_ids


def check_question_map(result, input_data, locator_ids, panel_sets,
                       problems, warnings):
    qmap = result.get("question_figure_map")
    questions = (input_data or {}).get("questions") or []
    question_ids = [str(q.get("question_id") or q.get("id"))
                    for q in questions
                    if isinstance(q, dict) and (q.get("question_id") or q.get("id"))]
    if not isinstance(qmap, list):
        problems.append("missing question_figure_map[]")
        qmap = []
    seen = set()
    for i, item in enumerate(qmap):
        if not isinstance(item, dict):
            problems.append("question_figure_map[%d] is not an object" % i)
            continue
        qid = item.get("question_id")
        fid = item.get("figure_id")
        panel = item.get("panel")
        if not qid:
            problems.append("question_figure_map[%d] missing question_id" % i)
        else:
            seen.add(str(qid))
        if not fid:
            problems.append("question_figure_map[%d] missing figure_id" % i)
        elif locator_ids and str(fid) not in locator_ids:
            problems.append("question_figure_map[%d] unknown figure_id %s" % (i, fid))
        if not panel:
            problems.append("question_figure_map[%d] missing panel" % i)
        elif str(fid) in panel_sets and panel_sets[str(fid)] and \
                str(panel) not in panel_sets[str(fid)]:
            warnings.append("question_figure_map[%d] panel %s not in figure %s panels"
                            % (i, panel, fid))
    missing = sorted(set(question_ids) - seen)
    if missing:
        problems.append("questions missing from question_figure_map: %s"
                        % ", ".join(missing))
    return seen


def check_panel_coverage(result, figure_ids, panel_sets, problems, warnings):
    coverage = result.get("panel_coverage")
    if not isinstance(coverage, list):
        problems.append("missing panel_coverage[]")
        coverage = []
    covered = set()
    for i, item in enumerate(coverage):
        if not isinstance(item, dict):
            problems.append("panel_coverage[%d] is not an object" % i)
            continue
        fid, panel = item.get("figure_id"), item.get("panel")
        if not fid or not panel:
            problems.append("panel_coverage[%d] missing figure_id/panel" % i)
            continue
        covered.add((str(fid), str(panel)))
        if not isinstance(item.get("covered"), bool):
            problems.append("panel_coverage[%d].covered must be boolean" % i)
        elif item.get("covered") is False and not (item.get("reason")
                                                   or item.get("notes")):
            warnings.append("panel_coverage[%d] uncovered without reason" % i)
        if str(fid) not in figure_ids:
            warnings.append("panel_coverage[%d] unknown figure_id %s" % (i, fid))
        elif panel_sets.get(str(fid)) and str(panel) not in panel_sets[str(fid)]:
            warnings.append("panel_coverage[%d] unknown panel %s on %s"
                            % (i, panel, fid))
    for fid in sorted(figure_ids):
        for panel in panel_sets.get(fid) or []:
            if (fid, panel) not in covered:
                problems.append("panel not covered: %s/%s" % (fid, panel))


def check_plot_tables(result, figure_ids, figure_modes, panel_modes_lookup,
                      problems, warnings):
    tables = result.get("plot_tables")
    if tables is None:
        return
    if not isinstance(tables, list):
        problems.append("plot_tables must be a list")
        return
    for i, table in enumerate(tables):
        if not isinstance(table, dict):
            problems.append("plot_tables[%d] is not an object" % i)
            continue
        fid = str(table.get("figure_id") or "")
        if fid and figure_ids and fid not in figure_ids:
            problems.append("plot_tables[%d] unknown figure_id %s" % (i, fid))
        panel = table.get("panel")
        if figure_modes.get(fid) == "visual_figure":
            problems.append("plot_tables[%d] targets a visual_figure %s" % (i, fid))
        if panel_modes_lookup.get((fid, str(panel))) == "visual_figure":
            problems.append("plot_tables[%d] targets a visual panel %s/%s"
                            % (i, fid, panel))
        if not panel:
            warnings.append("plot_tables[%d] missing panel" % i)
        axes = table.get("axes")
        if isinstance(axes, dict):
            for name in ("x", "y"):
                if name in axes:
                    check_axis(axes[name], "plot_tables[%d].%s" % (i, name),
                               problems, warnings, require_unit=False)
        for j, series in enumerate(table.get("series") or []):
            if not isinstance(series, dict):
                problems.append("plot_tables[%d].series[%d] is not an object" % (i, j))
                continue
            for k, point in enumerate(series.get("points") or []):
                if not isinstance(point, dict):
                    problems.append("plot_tables[%d].series[%d].points[%d] invalid"
                                    % (i, j, k))
                    continue
                if point.get("estimated") and not point.get("read_error"):
                    problems.append(
                        "plot_tables[%d].series[%d].points[%d] estimated without read_error"
                        % (i, j, k))
        check_statistics(table.get("statistics") or {}, problems, warnings)


def check_tables(result, input_data, problems, warnings):
    tables = result.get("tables")
    if tables is None:
        return
    if not isinstance(tables, list):
        problems.append("tables must be a list")
        return
    ids = set()
    for i, table in enumerate(tables):
        if not isinstance(table, dict):
            problems.append("tables[%d] is not an object" % i)
            continue
        tid = table.get("table_id")
        if not tid:
            problems.append("tables[%d] missing table_id" % i)
        else:
            ids.add(str(tid))
        if not isinstance(table.get("columns"), list):
            warnings.append("tables[%d] columns should be a list" % i)
        if not isinstance(table.get("rows"), list):
            warnings.append("tables[%d] rows should be a list" % i)
        if table.get("verbatim") is not True:
            warnings.append("tables[%d] should declare verbatim=true" % i)
    required = (input_data or {}).get("required_table_ids") or []
    missing = sorted(set(map(str, required)) - ids)
    if missing:
        problems.append("required tables not covered: %s" % ", ".join(missing))


def check_answers(result, locator_ids, question_ids, problems, warnings):
    answers = result.get("answers") or []
    if not isinstance(answers, list):
        problems.append("answers must be a list")
        answers = []
    answered = set()
    for i, ans in enumerate(answers):
        if not isinstance(ans, dict):
            problems.append("answer[%d] is not an object" % i)
            continue
        qid = ans.get("question_id")
        if not qid:
            problems.append("answer[%d] missing question_id" % i)
        else:
            answered.add(str(qid))
        if not str(ans.get("answer") or "").strip():
            problems.append("answer[%d] has empty answer text" % i)
        status = ans.get("status")
        if status not in STATUSES:
            problems.append("answer[%d] invalid status: %s" % (i, status))
        claims = ans.get("claims") or []
        if status == "answered" and not claims:
            warnings.append("answer[%d] answered without claims" % i)
        for j, claim in enumerate(claims):
            if not isinstance(claim, dict):
                problems.append("answer[%d] claim[%d] is not an object" % (i, j))
                continue
            if not claim.get("text"):
                problems.append("answer[%d] claim[%d] missing text" % (i, j))
            if claim.get("kind") not in KINDS:
                warnings.append("answer[%d] claim[%d] kind unusual: %s" %
                                (i, j, claim.get("kind")))
            check_evidence(claim.get("evidence"),
                           "answer[%d] claim[%d]" % (i, j),
                           locator_ids, problems, warnings)
    missing = sorted(set(question_ids) - answered)
    if missing:
        problems.append("input questions not answered: %s" % ", ".join(missing))
    return answers


def validate(result, input_data):
    problems, warnings, stats = [], [], {}
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["result root is not an object"],
                "warnings": [], "stats": {}}
    case_id = result.get("case_id")
    if not case_id:
        problems.append("result missing case_id")
    if input_data and input_data.get("case_id") and case_id and \
            input_data["case_id"] != case_id:
        problems.append("case_id does not match input")

    status = result.get("status")
    if status not in TOP_STATUSES:
        problems.append("top-level status missing/invalid: %s" % status)
    for key in ("result", "claims", "uncertainties"):
        if key not in result:
            problems.append("missing official alias: %s" % key)
    if "claims" in result and not isinstance(result.get("claims"), list):
        problems.append("official claims must be a list")
    if "uncertainties" in result and not isinstance(result.get("uncertainties"), list):
        problems.append("official uncertainties must be a list")
    for key in ("support_files_used", "change_log", "uncertainty"):
        if key not in result:
            warnings.append("missing %s" % key)

    pre_figure_ids = {str(f.get("figure_id")) for f in (result.get("figures") or [])
                      if isinstance(f, dict) and f.get("figure_id")}
    figure_ids, figure_modes, panel_sets, panel_modes_lookup = check_figures(
        result, input_data, problems, warnings, stats, known_ids=pre_figure_ids)
    unreadable_ids = check_unreadable(result, input_data, problems, warnings)
    locator_ids = set(figure_ids) | set(unreadable_ids)
    for table in result.get("tables") or []:
        if isinstance(table, dict) and table.get("table_id"):
            locator_ids.add(str(table["table_id"]))

    questions_seen = check_question_map(result, input_data, locator_ids,
                                        panel_sets, problems, warnings)
    check_panel_coverage(result, figure_ids | unreadable_ids, panel_sets,
                         problems, warnings)
    check_plot_tables(result, figure_ids, figure_modes, panel_modes_lookup,
                      problems, warnings)
    check_tables(result, input_data, problems, warnings)
    input_questions = (input_data or {}).get("questions") or []
    input_qids = {str(q.get("question_id") or q.get("id"))
                  for q in input_questions
                  if isinstance(q, dict) and (q.get("question_id") or q.get("id"))}
    check_answers(result, locator_ids, input_qids or questions_seen,
                  problems, warnings)

    input_figures = (input_data or {}).get("figures") or []
    covered = set(figure_ids) | set(unreadable_ids)
    missing_fig = sorted({str(f.get("figure_id")) for f in input_figures
                          if isinstance(f, dict) and f.get("figure_id")}
                         - covered)
    if missing_fig:
        problems.append("input figures not covered: %s" % ", ".join(missing_fig))
    stats["answers"] = len(result.get("answers") or [])
    stats["unreadable"] = sorted(unreadable_ids)
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "stats": stats}


def self_test():
    result = {
        "case_id": "fig-self-test",
        "figures": [
            {
                "figure_id": "Fig.1", "caption": "Line chart", "panels": ["a"],
                "figure_mode": "quantitative_chart",
                "panel_modes": {"a": "quantitative_chart"},
                "content_kind": "text", "extraction_mode": "deterministic",
                "axes": {
                    "x": {"label": "T", "unit": "C", "scale": "linear", "range": [0, 10]},
                    "y": {"label": "Y", "unit": "%", "scale": "log", "range": [1, 100]},
                },
                "series": [{"name": "A", "points": [{"x": 5, "y": 10,
                                                     "estimated": True,
                                                     "read_error": "0.5"}]}],
                "statistics": {"error_bar": "SD", "n": 3, "significance": []},
                "observations": [{
                    "text": "value 5", "kind": "derived", "quantitative": True,
                    "estimated": True, "read_error": "0.5",
                    "evidence": {"figure_id": "Fig.1", "panel": "a", "quote": "5"},
                }],
            },
            {
                "figure_id": "Fig.2", "caption": "Micrograph", "panels": ["a"],
                "figure_mode": "visual_figure",
                "panel_modes": {"a": "visual_figure"},
                "content_kind": "visual", "extraction_mode": "visual_description",
                "axes": None, "series": [],
                "statistics": {"error_bar": "not_applicable", "n": None,
                               "significance": []},
                "observations": [{
                    "text": "needle-like crystals; scale bar 100 um",
                    "kind": "visual", "quantitative": False, "estimated": False,
                    "evidence": {"figure_id": "Fig.2", "panel": "a",
                                 "quote": "scale bar 100 um"},
                }],
            },
        ],
        "question_figure_map": [
            {"question_id": "q1", "figure_id": "Fig.1", "panel": "a"},
            {"question_id": "q2", "figure_id": "Fig.2", "panel": "a"},
        ],
        "panel_coverage": [
            {"figure_id": "Fig.1", "panel": "a", "figure_mode": "quantitative_chart",
             "covered": True, "evidence": "plot_tables:Fig.1/a"},
            {"figure_id": "Fig.2", "panel": "a", "figure_mode": "visual_figure",
             "covered": True, "evidence": "observations:Fig.2/a"},
        ],
        "unreadable": [],
        "plot_tables": [{
            "figure_id": "Fig.1", "panel": "a", "chart_type": "line",
            "axes": {"x": {"label": "T", "unit": "C", "scale": "linear"},
                     "y": {"label": "Y", "unit": "%", "scale": "log"}},
            "series": [{"name": "A", "points": [{"x": 5, "y": 10,
                                                 "estimated": True,
                                                 "read_error": "0.5"}]}],
            "statistics": {"error_bar": "SD", "n": 3, "significance": []},
        }],
        "answers": [
            {"question_id": "q1", "answer": "10", "status": "answered",
             "claims": [{"claim_id": "c1", "text": "10", "kind": "derived",
                         "evidence": [{"figure_id": "Fig.1", "panel": "a",
                                       "quote": "5"}]}],
             "unanswered_parts": []},
            {"question_id": "q2", "answer": "needle-like crystals", "status": "answered",
             "claims": [{"claim_id": "c2", "text": "needle-like", "kind": "visual",
                         "evidence": [{"figure_id": "Fig.2", "panel": "a",
                                       "quote": "visual"}]}],
             "unanswered_parts": []},
        ],
        "tables": [],
        "support_files_used": ["scripts/check_figure_analysis.py"],
        "uncertainty": [],
        "change_log": [],
        "status": "answered",
        "result": {"summary": "q1 10; q2 needle-like crystals"},
        "claims": [{"claim_id": "c1", "kind": "derived",
                    "evidence": "Fig.1a", "source_id": "self-test",
                    "location": "Fig.1a"}],
        "uncertainties": [],
    }
    input_data = {
        "case_id": "fig-self-test",
        "figures": [{"figure_id": "Fig.1"}, {"figure_id": "Fig.2"}],
        "questions": [{"question_id": "q1"}, {"question_id": "q2"}],
    }
    return validate(result, input_data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None)
    ap.add_argument("--input", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        report = self_test()
        print(json.dumps({"ok": report["ok"], "self_test": True,
                          "problems": report["problems"],
                          "warnings": report["warnings"]}, ensure_ascii=False,
                         indent=2))
        return 0 if report["ok"] else 1
    if not args.result:
        ap.error("--result is required unless --self-test is used")
    try:
        result = load_json(args.result)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "problems": ["cannot read result: %s" % exc]},
                         ensure_ascii=False))
        return 2
    input_data = None
    if args.input:
        try:
            input_data = load_json(args.input)
        except (OSError, json.JSONDecodeError):
            input_data = None
    report = validate(result, input_data)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
