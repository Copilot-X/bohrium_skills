#!/usr/bin/env python3
"""Validate a reproduction result.json (and optional reproduction-plan.json).

Usage:
    python3 check_reproduction.py --result /app/output/result.json \
        --plan /app/work/reproduction-plan.json \
        --root /app/output --root /app/work --root /app --json

What it enforces
  * method_spec has the required fields, valid statuses and evidence locators
  * every missing field has a gap with a non-empty impact
  * reproduction_level is consistent with data/code availability and gaps
  * executed runs have a real script, log, exit code and command
  * not-run items never carry a reproduced value
  * abs_error / rel_error are recomputed, tolerance is pre-declared,
    and synthetic demos are never compared with paper values
  * reproduced values can be traced in the referenced log
  * verdict / supports_paper do not exceed the evidence
  * leakage_check and code_audit are structurally valid
  * optional plan has the seven elements, materials, environment,
    parameter provenance, steps, tolerance policy and verdict rules

The script never executes the referenced experiment. It only reads files and
parses Python sources for static reproducibility warnings.

Exit codes: 0 no errors, 1 validation errors, 2 unreadable input.
Stdlib only.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

TOOL_VERSION = "1.0"

FIELD_STATUS = {"reported", "partially_reported", "missing", "unknown",
                "not_applicable"}
LEVELS = {"full", "partial", "not_reproducible"}
SUPPORTS = {"yes", "partial", "no", "undetermined"}
VERDICTS = {"reproducible", "reproducible_with_notes", "blocked",
            "do_not_promote"}
LEAK_STATUS = {"not_checked", "no_leak_found", "suspected", "leak_detected"}
SEVERITIES = {"critical", "major", "minor", "nit"}
MATERIAL_STATUS = {"available", "missing", "restricted", "unknown"}
PARAM_ORIGIN = {"paper", "appendix", "code_default", "code_inferred",
                "assumed", "unknown"}
STEP_STATUS = {"planned", "done", "blocked", "partial"}
RUN_STATUS = {"success", "partial", "failed", "not_run", "blocked",
              "incomparable"}
TOP_STATUS = {"completed", "partial", "insufficient_evidence", "failed",
              "input_error", "success", "cannot_determine", "refused",
              "blocked"}
METHOD_FIELDS = ("dataset", "preprocessing", "model", "hyperparameters",
                 "metrics", "seed")
SEVEN_ELEMENTS = ("task", "data", "split", "model", "metric", "threshold",
                  "claim")
LOCATOR_KEYS = {
    "paragraph_id", "page", "pages", "section", "section_id", "table",
    "table_id", "figure", "figure_id", "panel", "row", "row_id", "column",
    "dataset_id", "repo_path", "path", "commit", "url", "doi", "locator",
    "quote", "line", "field", "slide_no", "shape_id",
}
SEED_CALLS = {
    "random.seed", "np.random.seed", "numpy.random.seed",
    "torch.manual_seed", "torch.cuda.manual_seed_all", "tf.random.set_seed",
    "set_seed", "set_random_seed",
}
ABS_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")
PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|XXX|lorem ipsum)\b", re.I)


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.checks = {}

    def error(self, code, message, path=None):
        item = {"code": code, "message": message}
        if path is not None:
            item["path"] = str(path)
        self.errors.append(item)

    def warn(self, code, message, path=None):
        item = {"code": code, "message": message}
        if path is not None:
            item["path"] = str(path)
        self.warnings.append(item)

    def ok(self):
        return not self.errors


def is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def close_enough(left, right):
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= 1e-9 * scale


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_roots(result_path, extras):
    roots = []
    if result_path:
        resolved = Path(result_path).resolve()
        roots.extend([resolved.parent, resolved.parent.parent])
    roots.append(Path.cwd().resolve())
    for extra in extras or []:
        roots.append(Path(extra).resolve())
    seen = set()
    unique = []
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def resolve_artifact(value, roots):
    if is_empty(value) or not isinstance(value, str):
        return None
    raw = Path(value)
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        stripped = value[2:] if value.startswith("./") else value
        for root in roots:
            candidates.append(root / stripped)
        candidates.append(Path.cwd() / stripped)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def validate_source(source, label, report, required=False):
    if source is None or source == "" or source == {} or source == []:
        if required:
            report.warn("EVIDENCE_SOURCE_MISSING",
                        "%s is reported but has no source/evidence locator" % label)
        return False
    if isinstance(source, str):
        if not source.strip():
            if required:
                report.warn("EVIDENCE_SOURCE_MISSING",
                            "%s has an empty source string" % label)
            return False
        return True
    if isinstance(source, list):
        if not source:
            if required:
                report.warn("EVIDENCE_SOURCE_MISSING",
                            "%s has an empty source list" % label)
            return False
        for index, entry in enumerate(source):
            validate_source(entry, "%s.source[%d]" % (label, index), report,
                            required=required)
        return True
    if isinstance(source, dict):
        locators = set(source) & LOCATOR_KEYS
        if not locators:
            if required:
                report.warn(
                    "EVIDENCE_LOCATOR_MISSING",
                    "%s.source has no recognizable locator key (paragraph_id, "
                    "page, table_id, figure_id, repo_path, url, ...)" % label)
            return False
        quote = source.get("quote")
        if isinstance(quote, str) and 0 < len(quote.strip()) < 6:
            report.warn("EVIDENCE_QUOTE_SHORT",
                        "%s.source.quote is shorter than 6 characters" % label)
        return True
    if required:
        report.warn("EVIDENCE_SOURCE_INVALID",
                    "%s.source must be an object, list or string" % label)
    return False


def check_method_spec(spec, report):
    statuses = {}
    if not isinstance(spec, dict):
        report.error("METHOD_SPEC_INVALID", "method_spec must be an object")
        return statuses
    for field in METHOD_FIELDS:
        label = "method_spec.%s" % field
        if field not in spec:
            report.error("METHOD_FIELD_MISSING", "%s is required" % label)
            statuses[field] = None
            continue
        item = spec.get(field)
        if not isinstance(item, dict):
            report.error("METHOD_FIELD_INVALID", "%s must be an object" % label)
            statuses[field] = None
            continue
        status = item.get("status")
        if status not in FIELD_STATUS:
            report.error(
                "METHOD_STATUS_INVALID",
                "%s.status must be one of %s (got %r)"
                % (label, sorted(FIELD_STATUS), status))
        value = item.get("value")
        if status in ("reported", "partially_reported") and is_empty(value):
            report.error("METHOD_VALUE_MISSING",
                         "%s.status=%s but value is empty" % (label, status))
        if status in ("missing", "unknown") and not is_empty(value):
            report.warn("STATUS_VALUE_CONFLICT",
                        "%s.status=%s but value is not empty" % (label, status))
        if status == "reported":
            validate_source(item.get("source") or item.get("evidence"), label,
                            report, required=True)
        elif status == "partially_reported":
            validate_source(item.get("source") or item.get("evidence"), label,
                            report, required=True)
        statuses[field] = status
    for flag in ("code_available", "data_available"):
        if flag not in spec:
            report.error("AVAILABILITY_MISSING",
                         "method_spec.%s is required" % flag)
        elif not isinstance(spec.get(flag), bool):
            report.error("AVAILABILITY_TYPE",
                         "method_spec.%s must be true/false" % flag)
    return statuses


def check_gaps(gaps, statuses, report):
    covered = set()
    if not isinstance(gaps, list):
        report.error("GAPS_INVALID", "gaps must be a list")
        return covered
    for index, gap in enumerate(gaps):
        label = "gaps[%d]" % index
        if not isinstance(gap, dict):
            report.error("GAP_INVALID", "%s must be an object" % label)
            continue
        field = gap.get("field")
        if is_empty(field):
            report.error("GAP_FIELD_MISSING", "%s.field is required" % label)
        else:
            text = str(field)
            covered.add(text)
            covered.add(text.split(".")[-1])
        if is_empty(gap.get("impact")):
            report.error("GAP_IMPACT_MISSING",
                         "%s.impact is required (use 'unknown' if unclear)"
                         % label)
        status = gap.get("status")
        if status is not None and status not in FIELD_STATUS:
            report.warn("GAP_STATUS_INVALID",
                        "%s.status=%r is not a recognized status"
                        % (label, status))
    for field, status in statuses.items():
        if status == "missing":
            if field not in covered:
                report.error(
                    "GAP_FOR_MISSING_FIELD",
                    "missing field %r has no gaps[] entry with an impact"
                    % field)
        elif status in ("unknown", "partially_reported"):
            if field not in covered:
                report.warn(
                    "GAP_RECOMMENDED",
                    "field %r is %s; add a gaps[] entry with impact"
                    % (field, status))
    return covered


def check_level(level, statuses, spec, report):
    if level not in LEVELS:
        report.error("LEVEL_INVALID",
                     "reproduction_level must be one of %s (got %r)"
                     % (sorted(LEVELS), level))
        return
    data_available = spec.get("data_available")
    code_available = spec.get("code_available")
    incomplete = [field for field, status in statuses.items()
                  if status not in ("reported", "not_applicable")]
    if data_available is False and level != "not_reproducible":
        report.error(
            "LEVEL_DATA_UNAVAILABLE",
            "data_available=false requires reproduction_level="
            "'not_reproducible' (got %r)" % level)
    if code_available is False and level == "full":
        report.error("LEVEL_CODE_UNAVAILABLE",
                     "code_available=false cannot support reproduction_level="
                     "'full'")
    if level == "full" and incomplete:
        report.error(
            "LEVEL_INCOMPLETE",
            "reproduction_level='full' but these fields are not reported: %s"
            % ", ".join(sorted(incomplete)))
    if (level == "not_reproducible" and data_available is True
            and code_available is True and not incomplete):
        report.warn(
            "LEVEL_SUSPICIOUS",
            "level=not_reproducible although data, code and all method fields "
            "are available; state the blocking reason in gaps/conclusion")


def value_candidates(value):
    if isinstance(value, str):
        return [value]
    candidates = {str(value), repr(value)}
    for fmt in ("{:.12g}", "{:.8g}", "{:.6g}", "{:.6f}", "{:.4f}",
                "{:.6e}", "{:.4e}"):
        try:
            candidates.add(fmt.format(value))
        except (TypeError, ValueError):
            pass
    return [item for item in candidates if item]


def value_in_text(value, text):
    return any(candidate in text for candidate in value_candidates(value))


def dotted_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def audit_python_script(path, label, report, stochastic, audited):
    key = str(path).lower()
    if key in audited:
        return
    audited.add(key)
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.warn("SCRIPT_UNREADABLE",
                    "%s cannot be read for static audit: %s" % (label, exc),
                    path)
        return
    if PLACEHOLDER_RE.search(source):
        report.warn("SCRIPT_PLACEHOLDER",
                    "%s contains TODO/TBD/FIXME text" % label, path)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        report.warn("SCRIPT_PARSE_FAILED",
                    "%s cannot be parsed: %s" % (label, exc), path)
        return
    has_seed = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = dotted_name(node.func)
            if name in SEED_CALLS:
                has_seed = True
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            report.warn("SCRIPT_BARE_EXCEPT",
                        "%s has a bare except at line %s"
                        % (label, getattr(node, "lineno", "?")), path)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if (len(text) > 2 and ABS_PATH_RE.match(text)
                    and not text.startswith(("http://", "https://", "//"))):
                report.warn(
                    "SCRIPT_HARDCODED_PATH",
                    "%s has a hardcoded absolute path at line %s: %s"
                    % (label, getattr(node, "lineno", "?"), text[:80]), path)
    if not has_seed:
        report.warn(
            "SCRIPT_NO_SEED",
            "%s has no explicit seed call (random.seed/set_seed/manual_seed); "
            "declare a seed policy even if the method is deterministic"
            % label, path)
    if stochastic:
        report.checks.setdefault("stochastic_scripts", []).append(str(path))


def check_reproduction_items(items, level, report, roots, spec):
    stats = {"items": 0, "executed": 0, "compared": 0, "within": 0,
             "out_of_tolerance": 0, "log_traced": 0, "not_run": 0}
    if not isinstance(items, list):
        report.error("REPRODUCTION_INVALID", "reproduction must be a list")
        return stats
    if not items and level in ("full", "partial"):
        report.error(
            "REPRODUCTION_EMPTY",
            "reproduction_level=%r requires at least one reproduction item"
            % level)
    audited = set()
    for index, item in enumerate(items):
        label = "reproduction[%d]" % index
        stats["items"] += 1
        if not isinstance(item, dict):
            report.error("REPRODUCTION_ITEM_INVALID",
                         "%s must be an object" % label)
            continue
        if is_empty(item.get("item")):
            report.error("REPRODUCTION_ITEM_NAME_MISSING",
                         "%s.item is required" % label)
        run = item.get("run")
        if not isinstance(run, dict):
            report.error("RUN_INVALID", "%s.run must be an object" % label)
            continue
        executed = run.get("executed")
        if not isinstance(executed, bool):
            report.error("RUN_EXECUTED_INVALID",
                         "%s.run.executed must be true/false" % label)
            continue
        stochastic = run.get("stochastic")
        if stochastic is not None and not isinstance(stochastic, bool):
            report.warn("RUN_STOCHASTIC_INVALID",
                        "%s.run.stochastic should be true/false" % label)
            stochastic = bool(stochastic)
        reported_value = item.get("reported_value")
        reproduced_value = item.get("reproduced_value")
        if reported_value is not None and not is_num(reported_value):
            report.warn("REPORTED_VALUE_NOT_NUMERIC",
                        "%s.reported_value is not a number; comparison is "
                        "skipped" % label)
            reported_value = None
        if reproduced_value is not None and not is_num(reproduced_value):
            report.warn("REPRODUCED_VALUE_NOT_NUMERIC",
                        "%s.reproduced_value is not a number; comparison is "
                        "skipped" % label)
            reproduced_value = None
        script_path = None
        log_path = None
        exit_code = run.get("exit_code")
        if executed:
            stats["executed"] += 1
            if is_empty(run.get("script")):
                report.error("RUN_SCRIPT_MISSING", "%s.run.script is required"
                             % label)
            else:
                script_path = resolve_artifact(run.get("script"), roots)
                if script_path is None:
                    report.error(
                        "RUN_SCRIPT_NOT_FOUND",
                        "%s.run.script does not exist: %s (searched roots: %s)"
                        % (label, run.get("script"),
                           ", ".join(str(root) for root in roots)))
            if is_empty(run.get("log")):
                report.error("RUN_LOG_MISSING", "%s.run.log is required"
                             % label)
            else:
                log_path = resolve_artifact(run.get("log"), roots)
                if log_path is None:
                    report.error(
                        "RUN_LOG_NOT_FOUND",
                        "%s.run.log does not exist: %s (no evidence for the "
                        "reproduced value)" % (label, run.get("log")))
                elif log_path.stat().st_size == 0:
                    report.error("RUN_LOG_EMPTY",
                                 "%s.run.log is empty: %s" % (label, log_path))
            if is_empty(run.get("command")):
                report.warn("RUN_COMMAND_MISSING",
                            "%s.run.command is missing; the run is not "
                            "independently repeatable" % label)
            if exit_code is None:
                report.error("RUN_EXIT_CODE_MISSING",
                             "%s.run.exit_code is required" % label)
            elif not isinstance(exit_code, int) or isinstance(exit_code, bool):
                report.error("RUN_EXIT_CODE_INVALID",
                             "%s.run.exit_code must be an integer" % label)
            elif exit_code != 0:
                if reproduced_value is not None:
                    report.error(
                        "RUN_FAILED_WITH_VALUE",
                        "%s.run.exit_code=%d but reproduced_value is not null"
                        % (label, exit_code))
                report.warn("RUN_FAILED",
                            "%s exited with code %d; record the failure, not a "
                            "successful value" % (label, exit_code))
            elif reproduced_value is None and item.get("output_text") is None:
                report.warn("RUN_OK_WITHOUT_VALUE",
                            "%s exited 0 but has no reproduced_value" % label)
            if stochastic:
                if run.get("seed") is None:
                    report.error("RUN_SEED_MISSING",
                                 "%s is stochastic but run.seed is null"
                                 % label)
                repeats = run.get("repeats")
                if not is_num(repeats) or repeats < 2:
                    report.warn(
                        "RUN_REPEATS_MISSING",
                        "%s is stochastic; run.repeats>=2 and mean/std are "
                        "expected" % label)
            if script_path is not None and script_path.suffix.lower() == ".py":
                audit_python_script(script_path, "%s.run.script" % label,
                                    report, bool(stochastic), audited)
        else:
            stats["not_run"] += 1
            if reproduced_value is not None:
                report.error(
                    "NOT_RUN_WITH_VALUE",
                    "%s.run.executed=false but reproduced_value=%r; set it to "
                    "null" % (label, reproduced_value))
            reason = run.get("not_run_reason") or item.get("not_run_reason")
            if is_empty(reason):
                report.error("NOT_RUN_REASON_MISSING",
                             "%s.run.executed=false requires not_run_reason"
                             % label)
            status = item.get("status")
            if status is not None and status not in RUN_STATUS:
                report.warn("RUN_STATUS_INVALID",
                            "%s.status=%r is not a recognized run status"
                            % (label, status))

        if reproduced_value is not None and log_path is not None:
            try:
                text = log_path.read_text(encoding="utf-8",
                                          errors="replace")[:2_000_000]
            except OSError:
                text = ""
            if text and value_in_text(reproduced_value, text):
                stats["log_traced"] += 1
            elif text:
                report.warn(
                    "VALUE_NOT_IN_LOG",
                    "%s.reproduced_value=%r was not found in %s; keep only "
                    "values traceable to the log"
                    % (label, reproduced_value, log_path))

        synthetic = bool(item.get("synthetic_demo") or
                         run.get("synthetic_demo"))
        if reported_value is not None and reproduced_value is not None:
            if synthetic:
                report.error(
                    "SYNTHETIC_COMPARISON",
                    "%s uses synthetic data but compares with reported_value=%r"
                    % (label, reported_value))
            expected_abs = abs(reproduced_value - reported_value)
            expected_rel = (expected_abs / abs(reported_value)
                            if reported_value != 0 else None)
            abs_error = item.get("abs_error")
            rel_error = item.get("rel_error")
            if abs_error is None:
                report.warn("ABS_ERROR_MISSING",
                            "%s.abs_error is missing (expected %.6g)"
                            % (label, expected_abs))
            elif not is_num(abs_error):
                report.error("ABS_ERROR_INVALID",
                             "%s.abs_error must be a number" % label)
            elif not close_enough(abs_error, expected_abs):
                report.error(
                    "ABS_ERROR_INCONSISTENT",
                    "%s.abs_error=%r but |reproduced-reported|=%.12g"
                    % (label, abs_error, expected_abs))
            if expected_rel is None:
                if rel_error not in (None, 0):
                    report.warn(
                        "REL_ERROR_UNDEFINED",
                        "%s.reported_value=0; rel_error must be null" % label)
            elif rel_error is None:
                report.warn("REL_ERROR_MISSING",
                            "%s.rel_error is missing (expected %.6g)"
                            % (label, expected_rel))
            elif not is_num(rel_error):
                report.error("REL_ERROR_INVALID",
                             "%s.rel_error must be a number" % label)
            elif not close_enough(rel_error, expected_rel):
                report.error(
                    "REL_ERROR_INCONSISTENT",
                    "%s.rel_error=%r but |reproduced-reported|/|reported|="
                    "%.12g" % (label, rel_error, expected_rel))
            tolerance = item.get("tolerance")
            abs_tol = rel_tol = None
            if isinstance(tolerance, dict):
                abs_tol = tolerance.get("abs", tolerance.get("abs_tol"))
                rel_tol = tolerance.get("rel", tolerance.get("rel_tol"))
            if abs_tol is None and rel_tol is None:
                report.warn(
                    "TOLERANCE_MISSING",
                    "%s has a reported value but no pre-declared tolerance "
                    "{abs, rel}" % label)
            else:
                within = True
                if abs_tol is not None:
                    if not is_num(abs_tol) or abs_tol < 0:
                        report.error("TOLERANCE_INVALID",
                                     "%s.tolerance.abs must be >=0" % label)
                        within = False
                    else:
                        within = within and expected_abs <= abs_tol
                if rel_tol is not None:
                    if not is_num(rel_tol) or rel_tol < 0:
                        report.error("TOLERANCE_INVALID",
                                     "%s.tolerance.rel must be >=0" % label)
                        within = False
                    elif expected_rel is not None:
                        within = within and expected_rel <= rel_tol
                declared = item.get("within_tolerance")
                if declared is not None and bool(declared) != within:
                    report.error(
                        "WITHIN_TOLERANCE_INCONSISTENT",
                        "%s.within_tolerance=%r but recomputed value is %r"
                        % (label, declared, within))
                if within:
                    stats["within"] += 1
                else:
                    stats["out_of_tolerance"] += 1
            stats["compared"] += 1
        elif reproduced_value is not None:
            if item.get("within_tolerance") is not None:
                report.error(
                    "NO_BASELINE_COMPARISON",
                    "%s has no reported_value but claims within_tolerance=%r"
                    % (label, item.get("within_tolerance")))
            if not synthetic and item.get("comparison_status") not in (
                    "not_comparable", "incomparable", "synthetic_demo"):
                report.warn(
                    "NO_BASELINE",
                    "%s has a reproduced value but no reported baseline; mark "
                    "comparison_status=incomparable or synthetic_demo=true"
                    % label)
        elif reported_value is not None:
            if item.get("within_tolerance") is not None:
                report.error(
                    "NO_REPRODUCED_COMPARISON",
                    "%s has no reproduced_value but claims within_tolerance=%r"
                    % (label, item.get("within_tolerance")))
    return stats


def check_leakage(result, report):
    leakage = result.get("leakage_check")
    if leakage is None:
        report.warn(
            "LEAKAGE_NOT_CHECKED",
            "leakage_check is missing; record no_leak_found / suspected / "
            "leak_detected with evidence")
        return None
    if not isinstance(leakage, dict):
        report.error("LEAKAGE_INVALID", "leakage_check must be an object")
        return None
    status = leakage.get("status") or leakage.get("verdict")
    if status not in LEAK_STATUS:
        report.warn("LEAKAGE_STATUS_INVALID",
                    "leakage_check.status=%r is not recognized" % status)
    findings = leakage.get("findings")
    if findings is not None:
        if not isinstance(findings, list):
            report.error("LEAKAGE_FINDINGS_INVALID",
                         "leakage_check.findings must be a list")
        else:
            for index, finding in enumerate(findings):
                if not isinstance(finding, dict):
                    report.error("LEAKAGE_FINDING_INVALID",
                                 "leakage_check.findings[%d] must be an object"
                                 % index)
                    continue
                if is_empty(finding.get("type")) and is_empty(
                        finding.get("finding")):
                    report.warn(
                        "LEAKAGE_FINDING_UNTYPED",
                        "leakage_check.findings[%d] has no type/finding"
                        % index)
                if is_empty(finding.get("evidence")):
                    report.warn(
                        "LEAKAGE_FINDING_NO_EVIDENCE",
                        "leakage_check.findings[%d] has no evidence" % index)
    return status


def check_code_audit(result, spec, report):
    audit = result.get("code_audit")
    if audit is None or audit == []:
        if spec.get("code_available") is True:
            report.warn(
                "CODE_AUDIT_MISSING",
                "code_available=true but code_audit[] is empty; record "
                "hardcoded paths, seeds, exceptions and version locks")
        return
    if not isinstance(audit, list):
        report.error("CODE_AUDIT_INVALID", "code_audit must be a list")
        return
    for index, entry in enumerate(audit):
        label = "code_audit[%d]" % index
        if not isinstance(entry, dict):
            report.error("CODE_AUDIT_ENTRY_INVALID",
                         "%s must be an object" % label)
            continue
        location = entry.get("file") or entry.get("location")
        if is_empty(location):
            report.error("CODE_AUDIT_LOCATION_MISSING",
                         "%s.file/location is required" % label)
        severity = entry.get("severity")
        if severity not in SEVERITIES:
            report.warn("CODE_AUDIT_SEVERITY_INVALID",
                        "%s.severity must be one of %s"
                        % (label, sorted(SEVERITIES)))
        if is_empty(entry.get("finding")) and is_empty(entry.get("note")):
            report.error("CODE_AUDIT_FINDING_MISSING",
                         "%s.finding/note is required" % label)


def check_env(result, executed_count, report):
    env = result.get("env_spec")
    if not isinstance(env, dict):
        if executed_count:
            report.warn(
                "ENV_SPEC_MISSING",
                "at least one run was executed but env_spec is missing; record "
                "runtime, dependencies, hardware and seed policy")
        return
    if is_empty(env.get("runtime")) and is_empty(env.get("python")):
        report.warn("ENV_RUNTIME_MISSING",
                    "env_spec.runtime/python is missing")
    if "dependencies" not in env:
        report.warn("ENV_DEPENDENCIES_MISSING",
                    "env_spec.dependencies is missing (use [] for stdlib only)")
    if is_empty(env.get("hardware")):
        report.warn("ENV_HARDWARE_MISSING", "env_spec.hardware is missing")
    if is_empty(env.get("seed_policy")) and is_empty(env.get("seed")):
        report.warn("ENV_SEED_POLICY_MISSING",
                    "env_spec.seed_policy is missing")


def check_conclusion(conclusion, level, stats, report):
    if not isinstance(conclusion, dict):
        report.error("CONCLUSION_INVALID", "conclusion must be an object")
        return None
    supports = conclusion.get("supports_paper")
    if supports not in SUPPORTS:
        report.error("CONCLUSION_SUPPORT_INVALID",
                     "conclusion.supports_paper must be one of %s (got %r)"
                     % (sorted(SUPPORTS), supports))
    if is_empty(conclusion.get("reason")):
        report.error("CONCLUSION_REASON_MISSING",
                     "conclusion.reason is required")
    if level == "not_reproducible" and supports not in ("undetermined", "no"):
        report.error(
            "CONCLUSION_LEVEL_CONFLICT",
            "reproduction_level=not_reproducible cannot support the paper "
            "(supports_paper=%r)" % supports)
    if supports == "yes":
        if level != "full":
            report.warn(
                "CONCLUSION_OVERCLAIM",
                "supports_paper=yes with reproduction_level=%r; use 'partial' "
                "unless all evidence supports the full claim" % level)
        if stats.get("compared", 0) == 0:
            report.error("CONCLUSION_WITHOUT_RUN",
                         "supports_paper=yes but no reported-vs-reproduced "
                         "comparison was executed")
        if stats.get("out_of_tolerance", 0) > 0:
            report.error(
                "CONCLUSION_CONTRADICTS_TOLERANCE",
                "supports_paper=yes but %d comparison(s) exceed the declared "
                "tolerance" % stats["out_of_tolerance"])
    if supports == "no" and stats.get("compared", 0) == 0:
        report.warn(
            "CONCLUSION_WITHOUT_RUN",
            "supports_paper=no without an executed comparison; use "
            "'undetermined' for material-only audits")
    return supports


def check_verdict(result, level, leakage_status, report):
    verdict = result.get("verdict")
    if verdict is None:
        report.warn("VERDICT_MISSING",
                    "verdict is missing; use reproducible / "
                    "reproducible_with_notes / blocked / do_not_promote")
        return None
    if verdict not in VERDICTS:
        report.error("VERDICT_INVALID",
                     "verdict must be one of %s (got %r)"
                     % (sorted(VERDICTS), verdict))
        return verdict
    if level == "not_reproducible" and verdict in ("reproducible",
                                                   "reproducible_with_notes"):
        report.error("VERDICT_LEVEL_CONFLICT",
                     "reproduction_level=not_reproducible but verdict=%r"
                     % verdict)
    if level == "partial" and verdict == "reproducible":
        report.error("VERDICT_LEVEL_CONFLICT",
                     "reproduction_level=partial cannot have verdict="
                     "'reproducible'")
    if leakage_status == "leak_detected" and verdict != "do_not_promote":
        report.error("LEAKAGE_VERDICT",
                     "leakage_check=leak_detected requires verdict="
                     "'do_not_promote'")
    if leakage_status == "suspected" and verdict == "reproducible":
        report.error("LEAKAGE_VERDICT",
                     "leakage_check=suspected cannot have verdict="
                     "'reproducible'")
    return verdict


def check_sources(result, report):
    sources = result.get("sources")
    if sources is None:
        report.warn(
            "SOURCES_MISSING",
            "sources[] is missing; key conclusions need verbatim evidence")
        return
    if not isinstance(sources, list):
        report.error("SOURCES_INVALID", "sources must be a list")
        return
    if not sources:
        report.warn("SOURCES_EMPTY", "sources[] is empty")
        return
    for index, source in enumerate(sources):
        label = "sources[%d]" % index
        if not isinstance(source, dict):
            report.error("SOURCE_INVALID", "%s must be an object" % label)
            continue
        if is_empty(source.get("locator")) and not (
                set(source) & LOCATOR_KEYS):
            report.error("SOURCE_LOCATOR_MISSING",
                         "%s.locator/paragraph_id/page/... is required"
                         % label)
        if is_empty(source.get("doc_id")):
            report.warn("SOURCE_DOC_ID_MISSING",
                        "%s.doc_id is missing" % label)
        quote = source.get("quote")
        if is_empty(quote):
            report.warn("SOURCE_QUOTE_MISSING",
                        "%s.quote is missing; evidence should be verbatim"
                        % label)
        elif isinstance(quote, str) and len(quote.strip()) < 6:
            report.warn("SOURCE_QUOTE_SHORT",
                        "%s.quote is shorter than 6 characters" % label)


def check_change_log(result, executed_count, report):
    change_log = result.get("change_log")
    if not isinstance(change_log, list):
        report.error("CHANGE_LOG_INVALID", "change_log must be a list")
        return
    if executed_count and not change_log:
        report.warn("CHANGE_LOG_MISSING",
                    "runs were executed but change_log is empty")
    for index, entry in enumerate(change_log):
        label = "change_log[%d]" % index
        if not isinstance(entry, dict):
            report.error("CHANGE_LOG_ENTRY_INVALID",
                         "%s must be an object" % label)
            continue
        if entry.get("step") is None and is_empty(entry.get("action")):
            report.error("CHANGE_LOG_ACTION_MISSING",
                         "%s needs step or action" % label)
        if is_empty(entry.get("evidence")) and is_empty(entry.get("result")):
            report.warn("CHANGE_LOG_EVIDENCE_MISSING",
                        "%s has no evidence/result path" % label)


def check_status_and_uncertainty(result, level, report):
    status = result.get("status")
    if status is None:
        report.warn("STATUS_MISSING",
                    "status is missing (completed/partial/"
                    "insufficient_evidence/failed/input_error)")
    elif status not in TOP_STATUS:
        report.warn("STATUS_INVALID", "unusual status: %r" % status)
    if status in ("completed", "success") and level == "not_reproducible":
        report.warn("STATUS_OVERCLAIM",
                    "status=%r but reproduction_level=not_reproducible"
                    % status)
    if status in ("partial", "insufficient_evidence", "failed", "input_error",
                  "cannot_determine", "refused", "blocked"):
        if is_empty(result.get("status_reason")):
            report.warn(
                "STATUS_REASON_MISSING",
                "status=%r requires status_reason so the reader knows why it "
                "is partial/blocked" % status)
    if "task_family" not in result:
        report.warn("TASK_FAMILY_MISSING",
                    "task_family is missing; use 'reading' for this task")
    uncertainty = result.get("uncertainty")
    if uncertainty is None:
        report.warn("UNCERTAINTY_MISSING",
                    "uncertainty[] is missing; record unresolved items")
    elif not isinstance(uncertainty, list):
        report.error("UNCERTAINTY_INVALID", "uncertainty must be a list")


def check_result(result, report, roots):
    if not isinstance(result, dict):
        report.error("RESULT_INVALID", "result.json root must be an object")
        return
    if is_empty(result.get("case_id")):
        report.error("CASE_ID_MISSING", "case_id is required")
    spec = result.get("method_spec")
    statuses = check_method_spec(spec, report)
    if isinstance(spec, dict):
        check_gaps(result.get("gaps"), statuses, report)
        level = result.get("reproduction_level")
        check_level(level, statuses, spec, report)
    else:
        level = result.get("reproduction_level")
        if level not in LEVELS:
            report.error("LEVEL_INVALID",
                         "reproduction_level is missing or invalid")
    stats = check_reproduction_items(result.get("reproduction"), level,
                                     report, roots,
                                     spec if isinstance(spec, dict) else {})
    leakage_status = check_leakage(result, report)
    check_code_audit(result, spec if isinstance(spec, dict) else {}, report)
    check_env(result, stats["executed"], report)
    supports = check_conclusion(result.get("conclusion"), level, stats, report)
    verdict = check_verdict(result, level, leakage_status, report)
    check_sources(result, report)
    check_change_log(result, stats["executed"], report)
    check_status_and_uncertainty(result, level, report)
    report.checks.update({
        "case_id": result.get("case_id"),
        "reproduction_level": level,
        "method_status": statuses,
        "verdict": verdict,
        "supports_paper": supports,
        "leakage_status": leakage_status,
    })
    report.checks.update(stats)


def check_plan(plan, report, result=None):
    if not isinstance(plan, dict):
        report.error("PLAN_INVALID", "plan root must be an object")
        return
    required = ("schema", "case_id", "paper", "objective", "seven_elements",
                "materials", "environment", "parameter_recovery",
                "reproduction_steps", "tolerance_policy", "gaps",
                "verdict_rules")
    for key in required:
        if key not in plan:
            report.error("PLAN_FIELD_MISSING", "plan.%s is required" % key)
    if plan.get("schema") not in (None, "reproduction-plan/v1"):
        report.warn("PLAN_SCHEMA", "unexpected plan.schema: %r"
                    % plan.get("schema"))
    if is_empty(plan.get("case_id")):
        report.error("PLAN_CASE_ID_MISSING", "plan.case_id is required")
    paper = plan.get("paper")
    if not isinstance(paper, dict):
        report.error("PLAN_PAPER_INVALID", "plan.paper must be an object")
    else:
        if is_empty(paper.get("title")) and is_empty(paper.get("doi_or_url")):
            report.warn("PLAN_PAPER_EMPTY",
                        "plan.paper needs title or doi_or_url")
    if is_empty(plan.get("objective")):
        report.error("PLAN_OBJECTIVE_MISSING", "plan.objective is required")
    seven = plan.get("seven_elements")
    if not isinstance(seven, dict):
        report.error("PLAN_SEVEN_INVALID",
                     "plan.seven_elements must be an object")
    else:
        for element in SEVEN_ELEMENTS:
            label = "seven_elements.%s" % element
            item = seven.get(element)
            if not isinstance(item, dict):
                report.error("PLAN_ELEMENT_MISSING", "%s is required" % label)
                continue
            status = item.get("status")
            if status not in FIELD_STATUS:
                report.error("PLAN_ELEMENT_STATUS_INVALID",
                             "%s.status must be one of %s"
                             % (label, sorted(FIELD_STATUS)))
            if status == "reported" and is_empty(item.get("value")):
                report.error("PLAN_ELEMENT_VALUE_MISSING",
                             "%s.status=reported but value is empty" % label)
            if status == "reported":
                validate_source(item.get("source"), label, report,
                                required=True)
    materials = plan.get("materials")
    if not isinstance(materials, list):
        report.error("PLAN_MATERIALS_INVALID",
                     "plan.materials must be a list")
    elif not materials:
        report.error("PLAN_MATERIALS_EMPTY",
                     "plan.materials must list code/data/environment items")
    else:
        for index, material in enumerate(materials):
            label = "materials[%d]" % index
            if not isinstance(material, dict):
                report.error("PLAN_MATERIAL_INVALID",
                             "%s must be an object" % label)
                continue
            if is_empty(material.get("item")):
                report.error("PLAN_MATERIAL_ITEM_MISSING",
                             "%s.item is required" % label)
            if is_empty(material.get("kind")):
                report.warn("PLAN_MATERIAL_KIND_MISSING",
                            "%s.kind is missing" % label)
            status = material.get("status")
            if status not in MATERIAL_STATUS:
                report.error("PLAN_MATERIAL_STATUS_INVALID",
                             "%s.status must be one of %s"
                             % (label, sorted(MATERIAL_STATUS)))
            if status == "available":
                if is_empty(material.get("location")):
                    report.error("PLAN_MATERIAL_LOCATION_MISSING",
                                 "%s is available but location is empty"
                                 % label)
                if is_empty(material.get("version_or_commit")):
                    report.warn(
                        "PLAN_MATERIAL_VERSION_MISSING",
                        "%s is available but version_or_commit is empty"
                        % label)
            if status in ("missing", "restricted") and is_empty(
                    material.get("note")):
                report.warn(
                    "PLAN_MATERIAL_NOTE_MISSING",
                    "%s is %s; add a note with the blocking reason"
                    % (label, status))
    environment = plan.get("environment")
    if not isinstance(environment, dict):
        report.error("PLAN_ENV_INVALID",
                     "plan.environment must be an object")
    else:
        for key in ("runtime", "dependencies", "hardware", "seed_policy"):
            if key not in environment:
                report.warn("PLAN_ENV_FIELD_MISSING",
                            "plan.environment.%s is missing" % key)
    parameters = plan.get("parameter_recovery")
    if not isinstance(parameters, list):
        report.error("PLAN_PARAMS_INVALID",
                     "plan.parameter_recovery must be a list")
    else:
        for index, param in enumerate(parameters):
            label = "parameter_recovery[%d]" % index
            if not isinstance(param, dict):
                report.error("PLAN_PARAM_INVALID",
                             "%s must be an object" % label)
                continue
            if is_empty(param.get("name")):
                report.error("PLAN_PARAM_NAME_MISSING",
                             "%s.name is required" % label)
            origin = param.get("origin")
            if origin not in PARAM_ORIGIN:
                report.error("PLAN_PARAM_ORIGIN_INVALID",
                             "%s.origin must be one of %s"
                             % (label, sorted(PARAM_ORIGIN)))
            if origin in ("paper", "appendix", "code_default",
                          "code_inferred"):
                validate_source(param.get("source"), label, report,
                                required=True)
            if origin == "assumed" and is_empty(param.get("impact_if_wrong")):
                report.warn(
                    "PLAN_ASSUMED_IMPACT_MISSING",
                    "%s.origin=assumed needs impact_if_wrong" % label)
    steps = plan.get("reproduction_steps")
    if not isinstance(steps, list):
        report.error("PLAN_STEPS_INVALID",
                     "plan.reproduction_steps must be a list")
    elif not steps:
        report.error("PLAN_STEPS_EMPTY",
                     "plan.reproduction_steps must have at least one step")
    else:
        for index, step in enumerate(steps):
            label = "reproduction_steps[%d]" % index
            if not isinstance(step, dict):
                report.error("PLAN_STEP_INVALID",
                             "%s must be an object" % label)
                continue
            if step.get("step") is None:
                report.error("PLAN_STEP_NUMBER_MISSING",
                             "%s.step is required" % label)
            if is_empty(step.get("action")):
                report.error("PLAN_STEP_ACTION_MISSING",
                             "%s.action is required" % label)
            if is_empty(step.get("command")):
                report.warn("PLAN_STEP_COMMAND_MISSING",
                            "%s.command is missing" % label)
            if is_empty(step.get("expected_output")):
                report.warn("PLAN_STEP_OUTPUT_MISSING",
                            "%s.expected_output is missing" % label)
            status = step.get("status")
            if status not in STEP_STATUS:
                report.error("PLAN_STEP_STATUS_INVALID",
                             "%s.status must be one of %s"
                             % (label, sorted(STEP_STATUS)))
    tolerances = plan.get("tolerance_policy")
    if not isinstance(tolerances, list):
        report.error("PLAN_TOLERANCE_INVALID",
                     "plan.tolerance_policy must be a list")
    elif not tolerances:
        report.warn("PLAN_TOLERANCE_EMPTY",
                    "plan.tolerance_policy is empty; declare tolerances "
                    "before running")
    else:
        for index, policy in enumerate(tolerances):
            label = "tolerance_policy[%d]" % index
            if not isinstance(policy, dict):
                report.error("PLAN_TOLERANCE_ENTRY_INVALID",
                             "%s must be an object" % label)
                continue
            if is_empty(policy.get("metric")):
                report.error("PLAN_TOLERANCE_METRIC_MISSING",
                             "%s.metric is required" % label)
            abs_tol = policy.get("abs_tol")
            rel_tol = policy.get("rel_tol")
            for name, value in (("abs_tol", abs_tol), ("rel_tol", rel_tol)):
                if value is not None and (not is_num(value) or value < 0):
                    report.error("PLAN_TOLERANCE_VALUE_INVALID",
                                 "%s.%s must be a number >=0"
                                 % (label, name))
            reported = policy.get("reported_value")
            if reported is not None and not is_num(reported):
                report.warn("PLAN_REPORTED_VALUE_NOT_NUMERIC",
                            "%s.reported_value is not numeric" % label)
            if reported is not None and abs_tol is None and rel_tol is None:
                report.error(
                    "PLAN_TOLERANCE_MISSING",
                    "%s has a reported value but no abs_tol/rel_tol" % label)
            elif reported is None and abs_tol is None and rel_tol is None:
                report.warn(
                    "PLAN_TOLERANCE_PENDING",
                    "%s still has null reported_value and tolerances" % label)
    gaps = plan.get("gaps")
    if not isinstance(gaps, list):
        report.error("PLAN_GAPS_INVALID", "plan.gaps must be a list")
    else:
        for index, gap in enumerate(gaps):
            label = "plan.gaps[%d]" % index
            if not isinstance(gap, dict):
                report.error("PLAN_GAP_INVALID",
                             "%s must be an object" % label)
                continue
            if is_empty(gap.get("field")):
                report.error("PLAN_GAP_FIELD_MISSING",
                             "%s.field is required" % label)
            if is_empty(gap.get("impact")):
                report.error("PLAN_GAP_IMPACT_MISSING",
                             "%s.impact is required" % label)
    rules = plan.get("verdict_rules")
    if not isinstance(rules, dict):
        report.error("PLAN_VERDICT_RULES_INVALID",
                     "plan.verdict_rules must be an object")
    else:
        if rules.get("max_level_if_data_restricted") not in LEVELS:
            report.warn("PLAN_VERDICT_RULE_INVALID",
                        "verdict_rules.max_level_if_data_restricted must be "
                        "one of %s" % sorted(LEVELS))
        if rules.get("max_level_if_code_missing") not in LEVELS:
            report.warn("PLAN_VERDICT_RULE_INVALID",
                        "verdict_rules.max_level_if_code_missing must be one "
                        "of %s" % sorted(LEVELS))
        if not isinstance(rules.get("blocked_if"), list):
            report.warn("PLAN_VERDICT_RULE_INVALID",
                        "verdict_rules.blocked_if must be a list")
    if result is not None:
        if plan.get("case_id") and result.get("case_id") and \
                plan.get("case_id") != result.get("case_id"):
            report.error("CASE_ID_MISMATCH",
                         "plan.case_id=%r != result.case_id=%r"
                         % (plan.get("case_id"), result.get("case_id")))
        result_spec = result.get("method_spec")
        if isinstance(seven, dict) and isinstance(result_spec, dict):
            for element in METHOD_FIELDS:
                plan_item = seven.get(element)
                result_item = result_spec.get(element)
                if isinstance(plan_item, dict) and isinstance(result_item,
                                                             dict):
                    if plan_item.get("status") != result_item.get("status"):
                        report.warn(
                            "PLAN_RESULT_STATUS_MISMATCH",
                            "seven_elements.%s.status=%r but "
                            "method_spec.%s.status=%r"
                            % (element, plan_item.get("status"), element,
                               result_item.get("status")))
        policies = {}
        if isinstance(tolerances, list):
            for policy in tolerances:
                if isinstance(policy, dict) and not is_empty(
                        policy.get("metric")):
                    policies[str(policy.get("metric"))] = policy
        items = result.get("reproduction")
        if isinstance(items, list):
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                if not (is_num(item.get("reported_value"))
                        and is_num(item.get("reproduced_value"))):
                    continue
                metric = str(item.get("item"))
                policy = policies.get(metric)
                if policy is None:
                    report.warn(
                        "PLAN_TOLERANCE_FOR_METRIC_MISSING",
                        "reproduction[%d] compares %r but plan."
                        "tolerance_policy has no entry for it" % (index, metric))
                    continue
                tolerance = item.get("tolerance")
                if isinstance(tolerance, dict):
                    for result_key, plan_key in (("abs", "abs_tol"),
                                                 ("rel", "rel_tol")):
                        result_value = tolerance.get(result_key)
                        plan_value = policy.get(plan_key)
                        if (is_num(result_value) and is_num(plan_value)
                                and not close_enough(result_value,
                                                     plan_value)):
                            report.warn(
                                "TOLERANCE_MISMATCH",
                                "reproduction[%d].tolerance.%s=%r but plan "
                                "tolerance_policy.%s=%r"
                                % (index, result_key, result_value, plan_key,
                                   plan_value))
        steps = plan.get("reproduction_steps")
        executed = any(
            isinstance(item, dict) and isinstance(item.get("run"), dict)
            and item["run"].get("executed") is True
            for item in (items or []))
        step_done = any(
            isinstance(step, dict) and step.get("status") == "done"
            for step in (steps or []))
        if executed and not step_done:
            report.warn(
                "PLAN_STEP_NOT_DONE",
                "result has an executed run but plan.reproduction_steps has no "
                "status=done step")
        if step_done and not executed:
            report.warn(
                "PLAN_STEP_DONE_WITHOUT_RUN",
                "plan marks a step done but result has no executed run")
    report.checks["plan_case_id"] = plan.get("case_id")


def emit(report, args, exit_code):
    summary = {
        "case_id": report.checks.get("case_id") or
        report.checks.get("plan_case_id"),
        "reproduction_level": report.checks.get("reproduction_level"),
        "method_status": report.checks.get("method_status"),
        "items": report.checks.get("items"),
        "executed": report.checks.get("executed"),
        "compared": report.checks.get("compared"),
        "within_tolerance": report.checks.get("within"),
        "out_of_tolerance": report.checks.get("out_of_tolerance"),
        "log_traced": report.checks.get("log_traced"),
        "not_run": report.checks.get("not_run"),
        "verdict": report.checks.get("verdict"),
        "supports_paper": report.checks.get("supports_paper"),
        "leakage_status": report.checks.get("leakage_status"),
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
    }
    payload = {
        "ok": report.ok(),
        "tool": "check_reproduction",
        "version": TOOL_VERSION,
        "result": str(args.result) if args.result else None,
        "plan": str(args.plan) if args.plan else None,
        "errors": report.errors,
        "warnings": report.warnings,
        "summary": summary,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in report.errors:
            print("ERROR %s: %s" % (item["code"], item["message"]))
        for item in report.warnings:
            print("WARN  %s: %s" % (item["code"], item["message"]))
        print("ok=%s errors=%d warnings=%d level=%s verdict=%s"
              % (payload["ok"], len(report.errors), len(report.warnings),
                 summary["reproduction_level"], summary["verdict"]))
    return exit_code


def main():
    parser = argparse.ArgumentParser(
        description="Validate a reproduction result.json and optional plan.")
    parser.add_argument("--result", default=None,
                        help="path to /app/output/result.json")
    parser.add_argument("--plan", default=None,
                        help="path to /app/work/reproduction-plan.json")
    parser.add_argument("--root", action="append", default=[],
                        help="extra root for resolving relative script/log "
                             "paths; repeatable")
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as errors")
    parser.add_argument("--json", action="store_true",
                        help="print one JSON report")
    args = parser.parse_args()
    if not args.result and not args.plan:
        parser.error("at least one of --result or --plan is required")

    report = Report()
    roots = build_roots(args.result, args.root)
    result = None
    plan = None

    if args.plan:
        try:
            plan = load_json(args.plan)
        except (OSError, json.JSONDecodeError) as exc:
            report.error("PLAN_UNREADABLE",
                         "cannot read plan %s: %s" % (args.plan, exc))
            return emit(report, args, 2)
    if args.result:
        try:
            result = load_json(args.result)
        except (OSError, json.JSONDecodeError) as exc:
            report.error("RESULT_UNREADABLE",
                         "cannot read result %s: %s" % (args.result, exc))
            return emit(report, args, 2)

    if plan is not None:
        check_plan(plan, report, result)
    if result is not None:
        check_result(result, report, roots)

    if args.strict and report.warnings:
        for warning in report.warnings:
            promoted = dict(warning)
            promoted["code"] = "STRICT_" + promoted["code"]
            promoted["message"] = "strict mode: " + promoted["message"]
            report.errors.append(promoted)
        report.warnings = []

    return emit(report, args, 0 if report.ok() else 1)


if __name__ == "__main__":
    sys.exit(main())
