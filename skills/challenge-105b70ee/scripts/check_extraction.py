#!/usr/bin/env python3
"""Validate literature field-extraction output against /input.

Usage:
    python3 check_extraction.py --result /app/output/result.json \
        --input /input --json --out /app/work/extraction_check.json
    python3 check_extraction.py --self-test --json

Checks
  * every field_id in target-fields.json is covered exactly once
  * reported values bind value + original + unit + statistic + condition +
    comparator + locator/quote + semantic check
  * condition numbers are traceable to the value quote or condition_evidence
  * numbers in original/value agree with the supporting quote
  * quotes are verbatim in the input corpus
  * not_reported / extraction_failed have reasons and missing[] entries
  * source_conflict keeps >=2 candidates with sources and conflicts[] entries
  * summary counts and complete_rate match the actual extractions
  * unsupported statuses and silent unit assumptions are rejected

Exit codes: 0 pass, 1 problems, 2 result unreadable.
Stdlib only.
"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

STATUSES = {"reported", "not_reported", "source_conflict", "extraction_failed"}
TOP_STATUSES = {"completed", "partial", "insufficient_evidence", "input_error"}
MISSING_KINDS = {
    "not_reported", "unit_missing", "condition_missing",
    "extraction_failed", "unreadable", "source_unavailable",
}
UNIT_STATUSES = {"reported", "not_reported", "not_applicable", "converted"}
CONDITION_STATUSES = {"reported", "not_reported", "not_applicable"}
STATISTICS = {
    "point_estimate", "mean", "median", "min", "max", "range", "sum",
    "percentage", "ratio", "not_reported", "not_applicable",
}
SOURCE_KINDS = {
    "paragraph", "table", "figure_caption", "supplementary", "review",
    "abstract", "discussion",
}
TEXT_SUFFIXES = {
    ".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".tex",
    ".html", ".xml",
}
REQUIRED_TOP = [
    "case_id", "status", "task_family", "schema", "extractions",
    "missing", "conflicts", "summary", "uncertainty", "change_log",
]
NUM = re.compile(r"[-−+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
NUMERIC_STRING = re.compile(
    r"^\s*[-−+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"\s*(?:%|°C|℃|h\^?-?1|h−1|s\^?-?1|s−1)?\s*$"
)
NUMERIC_NAME = re.compile(
    r"(conversion|selectivity|yield|temperature|pressure|rate|value|metric|"
    r"percent|time|duration|size|mass|volume|energy|efficiency|purity|score|"
    r"count|number|含量|转化率|选择性|收率|温度|压力|速率|数值|指标|效率|纯度)",
    re.IGNORECASE,
)
PLACEHOLDER = re.compile(
    r"(REPLACE_WITH_|TODO|TBD|FIXME|<placeholder>|待填写|待补充|xxx)",
    re.IGNORECASE,
)
PARA_MARK = re.compile(r"\[(?:paragraph|para|p)\s+([A-Za-z0-9_.:-]+)\]", re.I)
TABLE_MARK = re.compile(r"\[table\s+([A-Za-z0-9_.:-]+)\]", re.I)
FIG_MARK = re.compile(r"\[figure\s+([A-Za-z0-9_.:-]+)\]", re.I)
WS = re.compile(r"\s+")


def normalize(text):
    return WS.sub(" ", str(text or "").replace(" ", " ")).strip().lower()


def norm_unit(unit):
    if unit is None:
        return ""
    text = str(unit).strip().lower().replace(" ", "")
    text = text.replace("℃", "°c").replace("°c", "°c")
    text = text.replace("degreec", "°c").replace("degreesc", "°c")
    text = text.replace("h−1", "h^-1").replace("h-1", "h^-1")
    text = text.replace("h⁻¹", "h^-1").replace("1/h", "h^-1")
    text = text.replace("s−1", "s^-1").replace("s-1", "s^-1")
    text = text.replace("s⁻¹", "s^-1").replace("1/s", "s^-1")
    return text


def canon_number(token):
    text = str(token).strip().replace("−", "-").replace("+", "")
    text = text.replace(",", "")
    try:
        return format(float(text), ".15g")
    except ValueError:
        return text


def numbers(text):
    return {canon_number(m.group(0)) for m in NUM.finditer(str(text or ""))}


def value_numbers(value):
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        return {format(float(value), ".15g")}
    if isinstance(value, str):
        return numbers(value)
    if isinstance(value, list):
        result = set()
        for item in value:
            result |= value_numbers(item)
        return result
    if isinstance(value, dict):
        result = set()
        for key in ("min", "max", "value", "low", "high"):
            if key in value:
                result |= value_numbers(value[key])
        return result
    return set()


def first_present(mapping, keys, default=None):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def load_target_fields(root):
    """Return {'fields': [...], 'case_id': str|None, 'path': str|None, 'error': str|None}."""
    root = Path(root)
    result = {"fields": [], "case_id": None, "path": None, "error": None}
    if not root.exists():
        result["error"] = "input root missing: %s" % root
        return result
    candidates = []
    if root.is_file():
        candidates = [root]
    else:
        for name in ("target-fields.json", "target_fields.json", "targetfields.json"):
            candidates.extend(sorted(root.rglob(name)))
    if not candidates:
        result["error"] = "target-fields.json not found under %s" % root
        return result
    path = candidates[0]
    result["path"] = str(path)
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = "cannot parse %s: %s" % (path, exc)
        return result
    if isinstance(data, list):
        raw_fields = data
        container = {}
    elif isinstance(data, dict):
        container = data
        raw_fields = first_present(
            data, ("fields", "target_fields", "requested_fields",
                   "field_definitions", "targets"), [])
        if isinstance(raw_fields, dict):
            raw_fields = [
                dict(value if isinstance(value, dict) else {"name": str(value)},
                     field_id=key)
                for key, value in raw_fields.items()
            ]
        if not raw_fields and first_present(
                data, ("field_id", "id", "name")):
            raw_fields = [data]
        if not raw_fields:
            meta = {"case_id", "caseid", "id", "description", "version",
                    "material_version", "title", "note", "notes"}
            mapping = {key: value for key, value in data.items()
                       if key.lower() not in meta}
            if mapping and all(isinstance(value, (dict, str))
                               for value in mapping.values()):
                raw_fields = [
                    dict(value if isinstance(value, dict) else {"name": str(value)},
                         field_id=key)
                    for key, value in mapping.items()
                ]
    else:
        result["error"] = "target-fields.json has unsupported shape"
        return result
    fields = []
    for index, raw in enumerate(raw_fields or []):
        if not isinstance(raw, dict):
            continue
        fid = first_present(raw, ("field_id", "id", "field", "name"))
        if not fid:
            continue
        unit = first_present(raw, ("unit", "expected_unit", "units"))
        if isinstance(unit, (list, tuple)):
            unit = unit[0] if unit else None
        if isinstance(unit, str) and unit.strip().lower() in ("", "-", "none",
                                                              "null", "na", "n/a"):
            unit = None
        fields.append({
            "field_id": str(fid),
            "name": str(first_present(raw, ("name", "label", "field"), fid)),
            "type": str(first_present(raw, ("type", "value_type", "dtype"),
                                      "unknown")),
            "unit": unit,
            "required": bool(raw.get("required", True)),
            "description": raw.get("description") or raw.get("definition") or "",
            "raw": raw,
            "index": index,
        })
    result["fields"] = fields
    if isinstance(container, dict):
        result["case_id"] = first_present(container, ("case_id", "caseId", "id"))
    if not result["case_id"]:
        case_path = None
        if root.is_dir():
            found = sorted(root.rglob("case.json"))
            case_path = found[0] if found else None
        if case_path:
            try:
                case_data = load_json(case_path)
                if isinstance(case_data, dict):
                    result["case_id"] = first_present(
                        case_data, ("case_id", "caseId", "id"))
            except (OSError, json.JSONDecodeError):
                pass
    return result


def collect_corpus(root):
    """Read text under input and collect paragraph/table/figure ids."""
    info = {"corpus": "", "paragraph_ids": set(), "table_ids": set(),
            "figure_ids": set(), "files": 0, "text_files": 0}
    root = Path(root)
    if not root.exists():
        return info
    files = [root] if root.is_file() else sorted(root.rglob("*"))
    chunks = []
    for path in files:
        if not path.is_file():
            continue
        # Never let the result under validation certify its own quotes.
        if path.name.lower() == "result.json":
            continue
        if "output" in {part.lower() for part in path.parts}:
            continue
        info["files"] += 1
        try:
            if path.stat().st_size > 5_000_000:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            info["text_files"] += 1
            chunks.append(text)
        info["paragraph_ids"] |= {m.group(1) for m in PARA_MARK.finditer(text)}
        info["table_ids"] |= {m.group(1) for m in TABLE_MARK.finditer(text)}
        info["figure_ids"] |= {m.group(1) for m in FIG_MARK.finditer(text)}
        if path.suffix.lower() == ".json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            for item in walk_dicts(parsed):
                for key, target in (("paragraph_id", "paragraph_ids"),
                                    ("pid", "paragraph_ids"),
                                    ("table_id", "table_ids"),
                                    ("figure_id", "figure_ids")):
                    value = item.get(key)
                    if value not in (None, ""):
                        info[target].add(str(value))
    info["corpus"] = normalize("\n".join(chunks))
    return info


def walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_dicts(item)


def walk_strings(value, trail="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(item, "%s.%s" % (trail, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_strings(item, "%s[%d]" % (trail, index))
    elif isinstance(value, str):
        yield trail, value


class Report:
    def __init__(self):
        self.problems = []
        self.warnings = []
        self.checks = {}
        self.quote_records = {}

    def problem(self, message):
        self.problems.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def quote(self, label, quote):
        text = str(quote or "")
        if not text.strip():
            self.problem("%s: missing quote" % label)
            return
        normalized = normalize(text)
        if len(normalized) < 6:
            self.problem("%s: quote too short" % label)
        if normalized not in self.quote_records:
            self.quote_records[normalized] = (label, text)


def check_locator(locator, label, corpus_info, report, need_quote=True):
    if not isinstance(locator, dict):
        report.problem("%s: locator is not an object" % label)
        return None
    ids = [locator.get(key) for key in
           ("paragraph_id", "table_id", "figure_id", "section_id",
            "supplementary_id", "page")]
    if not any(value not in (None, "") for value in ids):
        report.problem("%s: locator has no paragraph/table/figure/page id" % label)
    quote = locator.get("quote")
    if need_quote:
        report.quote(label, quote)
    if locator.get("paragraph_id") not in (None, ""):
        pid = str(locator["paragraph_id"])
        if corpus_info["paragraph_ids"] and pid not in corpus_info["paragraph_ids"]:
            report.warning("%s: paragraph_id %s not found in input markers"
                           % (label, pid))
    if locator.get("table_id") not in (None, ""):
        tid = str(locator["table_id"])
        if corpus_info["table_ids"] and tid not in corpus_info["table_ids"]:
            report.warning("%s: table_id %s not found in input markers"
                           % (label, tid))
    if locator.get("figure_id") not in (None, ""):
        fid = str(locator["figure_id"])
        if corpus_info["figure_ids"] and fid not in corpus_info["figure_ids"]:
            report.warning("%s: figure_id %s not found in input markers"
                           % (label, fid))
    return quote


def is_numeric_value(value, field):
    ftype = str((field or {}).get("type") or "").lower()
    if isinstance(value, dict) and ("value" in value):
        raw = value.get("value")
    else:
        raw = value
    numeric_raw = (
        isinstance(raw, (int, float)) and not isinstance(raw, bool)
    ) or (isinstance(raw, dict) and bool({"min", "max"} & set(raw)))
    original = value.get("original") if isinstance(value, dict) else None
    if ftype in ("boolean", "date", "enum"):
        return False
    if ftype == "string":
        return bool(field and field.get("unit") and (
            numeric_raw
            or (isinstance(original, str) and NUMERIC_STRING.match(original))
            or (isinstance(raw, str) and NUMERIC_STRING.match(raw))
        ))
    if ftype in ("number", "range", "integer", "float"):
        return True
    if isinstance(raw, bool):
        return False
    if numeric_raw:
        return True
    if field and field.get("unit"):
        return True
    if field and NUMERIC_NAME.search("%s %s" % (field.get("name", ""),
                                                field.get("description", ""))):
        return True
    for text in (original, raw):
        if isinstance(text, str) and NUMERIC_STRING.match(text):
            return True
    return False


def check_value(value, label, field, corpus_info, report,
                expect_condition=True):
    if not isinstance(value, dict):
        report.problem("%s: value entry is not an object" % label)
        return
    if "value" not in value or value.get("value") is None:
        report.problem("%s: value is null/missing" % label)
    original = value.get("original")
    if not isinstance(original, str) or not original.strip():
        report.problem("%s: original is empty" % label)
    numeric = is_numeric_value(value, field)
    expected_unit = field.get("unit") if field else None
    unit = value.get("unit")
    unit_status = value.get("unit_status")
    if unit_status not in UNIT_STATUSES:
        report.problem("%s: invalid/missing unit_status %r" % (label, unit_status))
    quote = check_locator(value.get("locator"), label, corpus_info, report)
    source_kind = value.get("source_kind")
    if source_kind not in SOURCE_KINDS:
        report.warning("%s: unusual source_kind %r" % (label, source_kind))
    semantic = value.get("semantic_check")
    if not isinstance(semantic, dict):
        report.problem("%s: missing semantic_check (literal quote is not enough)"
                       % label)
    elif semantic.get("matches_requested_field") is not True:
        report.problem("%s: reported value failed semantic_check; use "
                       "extraction_failed instead" % label)
    if not isinstance(value.get("digit_check"), dict):
        report.warning("%s: missing digit_check" % label)
    else:
        conflicts = value["digit_check"].get("conflicts")
        if conflicts:
            report.warning("%s: digit_check has %d unresolved digit conflicts"
                           % (label, len(conflicts)))

    if not numeric:
        return

    statistic = value.get("statistic")
    if statistic not in STATISTICS:
        report.problem("%s: invalid/missing statistic %r" % (label, statistic))
    comparator = value.get("comparator")
    if comparator is None or str(comparator).strip() == "":
        report.problem("%s: comparator missing; use 'none' when not applicable"
                       % label)
    condition = value.get("condition")
    condition_status = value.get("condition_status")
    if condition_status not in CONDITION_STATUSES:
        report.problem("%s: invalid/missing condition_status %r"
                       % (label, condition_status))
    condition_missing = False
    if expect_condition:
        if not isinstance(condition, str) or not condition.strip():
            if condition_status != "not_reported":
                report.problem("%s: numeric value has no condition and is not "
                               "marked condition_status=not_reported" % label)
            else:
                condition_missing = True
                report.warning("%s: condition explicitly not reported" % label)

    # unit binding
    if expected_unit not in (None, ""):
        if unit in (None, ""):
            flagged = (value.get("unit_missing") is True
                       or unit_status == "not_reported")
            if not flagged:
                report.problem("%s: expected unit %r but unit is empty without "
                               "unit_missing flag" % (label, expected_unit))
            else:
                report.warning("%s: unit explicitly not reported (expected %r)"
                               % (label, expected_unit))
        elif norm_unit(unit) != norm_unit(expected_unit):
            conversion = value.get("conversion")
            if isinstance(conversion, dict) and conversion:
                report.warning("%s: unit %r converted to %r; conversion recorded"
                               % (label, unit, expected_unit))
            else:
                report.problem("%s: unit %r does not match expected %r and no "
                               "conversion is recorded"
                               % (label, unit, expected_unit))
    else:
        if unit in (None, "") and unit_status == "reported":
            report.problem("%s: unit_status=reported but unit is empty" % label)

    # numeric consistency with quote
    quote_tokens = numbers(quote)
    original_tokens = numbers(original)
    if not original_tokens:
        report.warning("%s: numeric field original contains no parseable number"
                       % label)
    elif quote and original_tokens - quote_tokens:
        report.problem("%s: original numbers %s are not in the quote"
                       % (label, sorted(original_tokens - quote_tokens)))
    conversion = value.get("conversion")
    if not (isinstance(conversion, dict) and conversion):
        parsed_tokens = value_numbers(value.get("value"))
        unsupported = parsed_tokens - (original_tokens | quote_tokens)
        if unsupported:
            report.problem("%s: parsed value numbers %s are not supported by "
                           "original/quote" % (label, sorted(unsupported)))

    # condition numbers traceable to quote or condition_evidence
    condition_tokens = numbers(condition)
    if condition_tokens:
        allowed = set(quote_tokens)
        condition_evidence = value.get("condition_evidence")
        if isinstance(condition_evidence, dict):
            ev_quote = check_locator(condition_evidence,
                                     label + ".condition_evidence",
                                     corpus_info, report)
            allowed |= numbers(ev_quote)
        missing = condition_tokens - allowed
        if missing:
            report.problem("%s: condition numbers %s not traceable to value "
                           "quote or condition_evidence"
                           % (label, sorted(missing)))
    elif expect_condition and not condition_missing:
        report.warning("%s: condition has no numeric anchor; confirm it is a "
                       "qualitative condition" % label)


def check_extraction(extraction, field, corpus_info, report,
                     missing_by_field, conflicts_by_field):
    if not isinstance(extraction, dict):
        report.problem("extraction entry is not an object")
        return
    fid = extraction.get("field_id")
    label = "extraction[%s]" % (fid or "?")
    if not fid:
        report.problem("%s: missing field_id" % label)
        return
    status = extraction.get("status")
    if status not in STATUSES:
        report.problem("%s: invalid status %r" % (label, status))
        return
    if field and extraction.get("field_name") not in (None, "", field.get("name")):
        report.warning("%s: field_name %r differs from target name %r"
                       % (label, extraction.get("field_name"), field.get("name")))
    values = extraction.get("values")
    candidates = extraction.get("candidates")
    if values is None:
        values = []
    if not isinstance(values, list):
        report.problem("%s: values is not a list" % label)
        values = []
    if candidates is not None and not isinstance(candidates, list):
        report.problem("%s: candidates is not a list" % label)
        candidates = []

    if status == "reported":
        if not values:
            report.problem("%s: status=reported but values is empty" % label)
        for index, value in enumerate(values):
            check_value(value, "%s.values[%d]" % (label, index), field,
                        corpus_info, report)
    elif status in ("not_reported", "extraction_failed"):
        if values:
            report.problem("%s: status=%s must not carry values" % (label, status))
        reason = extraction.get("missing_reason")
        if not isinstance(reason, str) or not reason.strip():
            report.problem("%s: status=%s without missing_reason"
                           % (label, status))
        if not extraction.get("search_scope"):
            report.warning("%s: status=%s without search_scope" % (label, status))
        expected_kind = "not_reported" if status == "not_reported" else "extraction_failed"
        kinds = missing_by_field.get(fid, set())
        if not (kinds & {expected_kind, "unreadable", "source_unavailable"}):
            report.problem("%s: status=%s not registered in missing[] as %s"
                           % (label, status, expected_kind))
    elif status == "source_conflict":
        if not isinstance(candidates, list) or len(candidates) < 2:
            report.problem("%s: source_conflict needs >=2 candidates" % label)
        else:
            for index, candidate in enumerate(candidates):
                check_value(candidate, "%s.candidates[%d]" % (label, index),
                            field, corpus_info, report)
        if fid not in conflicts_by_field:
            report.problem("%s: source_conflict not registered in conflicts[]"
                           % label)
        if values:
            if not extraction.get("resolution") or not extraction.get("resolution_note"):
                report.problem("%s: source_conflict selects values without "
                               "resolution + resolution_note" % label)
            for index, value in enumerate(values):
                check_value(value, "%s.values[%d]" % (label, index), field,
                            corpus_info, report)
        if not extraction.get("search_scope"):
            report.warning("%s: source_conflict without search_scope" % label)

    # unit/condition missing registrations for reported values
    kinds = missing_by_field.get(fid, set())
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            continue
        if is_numeric_value(value, field):
            if (value.get("unit_missing") is True
                    or value.get("unit_status") == "not_reported"):
                if "unit_missing" not in kinds:
                    report.problem("%s.values[%d]: unit missing but no "
                                   "missing[] kind=unit_missing" % (label, index))
            if value.get("condition_status") == "not_reported":
                if "condition_missing" not in kinds:
                    report.problem("%s.values[%d]: condition missing but no "
                                   "missing[] kind=condition_missing"
                                   % (label, index))


def check_missing(missing, extractions_by_id, missing_by_field, report):
    if not isinstance(missing, list):
        report.problem("missing is not a list")
        return
    seen = set()
    for index, entry in enumerate(missing):
        label = "missing[%d]" % index
        if not isinstance(entry, dict):
            report.problem("%s: not an object" % label)
            continue
        fid = entry.get("field_id")
        kind = entry.get("kind")
        reason = entry.get("reason")
        if not fid:
            report.problem("%s: missing field_id" % label)
            continue
        if fid not in extractions_by_id:
            report.problem("%s: unknown field_id %s" % (label, fid))
        if kind not in MISSING_KINDS:
            report.problem("%s: invalid kind %r" % (label, kind))
            continue
        if not isinstance(reason, str) or not reason.strip():
            report.problem("%s: missing reason" % label)
        key = (fid, kind)
        if key in seen:
            report.problem("%s: duplicate (%s, %s)" % (label, fid, kind))
        seen.add(key)
        missing_by_field.setdefault(fid, set()).add(kind)
        extraction = extractions_by_id.get(fid)
        if not extraction:
            continue
        status = extraction.get("status")
        if kind == "not_reported" and status != "not_reported":
            report.problem("%s: kind=not_reported but extraction status is %s"
                           % (label, status))
        if kind in ("extraction_failed", "unreadable", "source_unavailable"):
            if status != "extraction_failed":
                report.problem("%s: kind=%s but extraction status is %s"
                               % (label, kind, status))
        if kind in ("unit_missing", "condition_missing") and status not in (
                "reported", "source_conflict"):
            report.problem("%s: kind=%s but extraction status is %s"
                           % (label, kind, status))


def check_conflicts(conflicts, extractions_by_id, conflicts_by_field, report,
                    corpus_info):
    if not isinstance(conflicts, list):
        report.problem("conflicts is not a list")
        return
    seen = set()
    for index, entry in enumerate(conflicts):
        label = "conflicts[%d]" % index
        if not isinstance(entry, dict):
            report.problem("%s: not an object" % label)
            continue
        fid = entry.get("field_id")
        if not fid:
            report.problem("%s: missing field_id" % label)
            continue
        if fid in seen:
            report.problem("%s: duplicate conflict for %s" % (label, fid))
        seen.add(fid)
        conflicts_by_field[fid] = entry
        extraction = extractions_by_id.get(fid)
        if extraction and extraction.get("status") != "source_conflict":
            report.problem("%s: conflicts entry for field with status %s"
                           % (label, extraction.get("status")))
        candidates = entry.get("candidates")
        if not isinstance(candidates, list) or len(candidates) < 2:
            report.problem("%s: needs >=2 candidates" % label)
        else:
            for cindex, candidate in enumerate(candidates):
                if not isinstance(candidate, dict):
                    report.problem("%s.candidates[%d]: not an object"
                                   % (label, cindex))
                    continue
                if not candidate.get("original"):
                    report.problem("%s.candidates[%d]: missing original"
                                   % (label, cindex))
                check_locator(candidate.get("locator"),
                              "%s.candidates[%d]" % (label, cindex),
                              corpus_info, report)
        if not isinstance(entry.get("note"), str) or not entry["note"].strip():
            report.problem("%s: missing note" % label)
    for fid, extraction in extractions_by_id.items():
        if extraction.get("status") == "source_conflict" and fid not in seen:
            report.problem("extraction[%s]: source_conflict missing from "
                           "conflicts[]" % fid)


def check_summary(summary, requested_ids, extractions_by_id,
                  missing_by_field, report):
    if not isinstance(summary, dict):
        report.problem("summary is not an object")
        return
    statuses = [e.get("status") for e in extractions_by_id.values()]
    unit_missing = {fid for fid, kinds in missing_by_field.items()
                    if "unit_missing" in kinds}
    condition_missing = {fid for fid, kinds in missing_by_field.items()
                         if "condition_missing" in kinds}
    clean_reported = {
        fid for fid, extraction in extractions_by_id.items()
        if extraction.get("status") == "reported"
        and fid not in unit_missing and fid not in condition_missing
    }
    expected = {
        "field_count": len(requested_ids),
        "reported_count": statuses.count("reported"),
        "not_reported_count": statuses.count("not_reported"),
        "extraction_failed_count": statuses.count("extraction_failed"),
        "conflict_count": statuses.count("source_conflict"),
        "unit_missing_count": len(unit_missing),
        "condition_missing_count": len(condition_missing),
        "complete_rate": (len(clean_reported) / len(requested_ids)
                          if requested_ids else 0.0),
    }
    for key in ("field_count", "reported_count", "not_reported_count",
                "extraction_failed_count", "conflict_count"):
        if key not in summary:
            report.problem("summary missing %s" % key)
        elif summary.get(key) != expected[key]:
            report.problem("summary.%s=%r but actual=%r"
                           % (key, summary.get(key), expected[key]))
    for key in ("unit_missing_count", "condition_missing_count"):
        if key not in summary:
            report.warning("summary missing %s" % key)
        elif summary.get(key) != expected[key]:
            report.problem("summary.%s=%r but actual=%r"
                           % (key, summary.get(key), expected[key]))
    if "complete_rate" not in summary:
        report.problem("summary missing complete_rate")
    else:
        try:
            rate = float(summary["complete_rate"])
            if abs(rate - expected["complete_rate"]) > 0.021:
                report.problem("summary.complete_rate=%r but actual=%.4f"
                               % (summary["complete_rate"],
                                  expected["complete_rate"]))
        except (TypeError, ValueError):
            report.problem("summary.complete_rate is not numeric")
    report.checks["summary_expected"] = expected


def check_all(result, bundle, corpus_info, input_given):
    report = Report()
    if not isinstance(result, dict):
        report.problem("result is not a JSON object")
        return report

    for key in REQUIRED_TOP:
        if key not in result:
            report.problem("result.json missing %s" % key)
    top_status = result.get("status")
    if top_status not in TOP_STATUSES:
        report.problem("result.status invalid: %r" % top_status)
    if result.get("task_family") not in (None, "", "reading"):
        report.warning("result.task_family is %r, expected 'reading'"
                       % result.get("task_family"))
    for trail, text in walk_strings(result):
        if PLACEHOLDER.search(text):
            report.problem("placeholder text at %s: %r"
                           % (trail, text[:60]))
    if bundle.get("case_id") and result.get("case_id") != bundle["case_id"]:
        report.problem("result.case_id %r does not match input %r"
                       % (result.get("case_id"), bundle["case_id"]))
    for key in ("uncertainty", "change_log"):
        if not isinstance(result.get(key), list):
            report.problem("%s is not a list" % key)
    if isinstance(result.get("change_log"), list) and not result["change_log"]:
        report.warning("change_log is empty")
    support = result.get("support_files_used")
    if not isinstance(support, list) or not support:
        report.warning("support_files_used is empty; list files actually used")
    else:
        for path in support:
            if not isinstance(path, str) or not path.strip():
                report.problem("support_files_used contains a non-path entry")

    schema = result.get("schema")
    schema_ids = set()
    schema_map = {}
    if not isinstance(schema, list):
        report.problem("schema is not a list")
        schema = []
    for index, item in enumerate(schema):
        if not isinstance(item, dict) or not item.get("field_id"):
            report.problem("schema[%d]: missing field_id" % index)
            continue
        fid = str(item["field_id"])
        if fid in schema_ids:
            report.problem("schema: duplicate field_id %s" % fid)
        schema_ids.add(fid)
        schema_map[fid] = item

    target_fields = bundle.get("fields") or []
    if bundle.get("error") and input_given:
        report.warning(bundle["error"])
    if target_fields:
        requested_ids = {field["field_id"] for field in target_fields}
        field_map = {field["field_id"]: field for field in target_fields}
        missing_schema = requested_ids - schema_ids
        if missing_schema:
            report.problem("schema missing target field_ids: %s"
                           % sorted(missing_schema))
        unknown_schema = schema_ids - requested_ids
        if unknown_schema:
            report.warning("schema has extra field_ids: %s" % sorted(unknown_schema))
        if result.get("case_id") and bundle.get("case_id"):
            pass
    else:
        requested_ids = schema_ids
        field_map = {fid: schema_map[fid] for fid in schema_ids}
        if input_given:
            report.warning("target-fields.json unavailable; coverage checked "
                           "against result.schema")

    extractions = result.get("extractions")
    extractions_by_id = {}
    if not isinstance(extractions, list) or not extractions:
        report.problem("extractions is empty or not a list")
        extractions = []
    for index, extraction in enumerate(extractions):
        if not isinstance(extraction, dict):
            report.problem("extractions[%d] is not an object" % index)
            continue
        fid = extraction.get("field_id")
        if not fid:
            report.problem("extractions[%d]: missing field_id" % index)
            continue
        if fid in extractions_by_id:
            report.problem("extractions: duplicate field_id %s" % fid)
        extractions_by_id[fid] = extraction
        extraction.setdefault("field_name", field_map.get(fid, {}).get("name"))
    missing_ids = requested_ids - set(extractions_by_id)
    if missing_ids:
        report.problem("field_ids not extracted: %s" % sorted(missing_ids))
    extra_ids = set(extractions_by_id) - requested_ids
    if extra_ids:
        report.warning("extra field_ids not requested: %s" % sorted(extra_ids))

    missing_by_field = {}
    check_missing(result.get("missing"), extractions_by_id,
                  missing_by_field, report)
    conflicts_by_field = {}
    check_conflicts(result.get("conflicts"), extractions_by_id,
                    conflicts_by_field, report, corpus_info)

    for fid, extraction in extractions_by_id.items():
        check_extraction(extraction, field_map.get(fid), corpus_info, report,
                         missing_by_field, conflicts_by_field)

    check_summary(result.get("summary"), requested_ids, extractions_by_id,
                  missing_by_field, report)

    gaps = {"not_reported", "extraction_failed"}
    status_counts = {}
    for extraction in extractions_by_id.values():
        status = extraction.get("status")
        status_counts[status] = status_counts.get(status, 0) + 1
    if top_status == "completed" and (
            status_counts.get("not_reported")
            or status_counts.get("extraction_failed")
            or status_counts.get("source_conflict")
            or any("unit_missing" in kinds or "condition_missing" in kinds
                   for kinds in missing_by_field.values())):
        report.warning("status=completed but explicit gaps/conflicts exist; "
                       "consider status=partial")

    if input_given and corpus_info["corpus"]:
        checked = 0
        for normalized, (label, original) in report.quote_records.items():
            checked += 1
            if normalized not in corpus_info["corpus"]:
                report.problem("%s: quote is not verbatim in input corpus: %r"
                               % (label, original[:70]))
        report.checks["quotes_checked"] = checked
    elif input_given:
        report.warning("input has no readable text; quote verbatim check skipped")
    else:
        report.warning("--input not provided; quote verbatim check skipped")

    report.checks.update({
        "requested_fields": len(requested_ids),
        "extractions": len(extractions_by_id),
        "missing_entries": len(result.get("missing") or [])
        if isinstance(result.get("missing"), list) else 0,
        "conflict_entries": len(result.get("conflicts") or [])
        if isinstance(result.get("conflicts"), list) else 0,
    })
    return report


def self_test():
    paper = (
        "# Synthetic\n\n"
        "[paragraph p2] The catalyst reached 92% conversion at 350 C.\n"
        "[table t1]\n| Catalyst | Conversion (%) |\n|---|---|\n"
        "| Ni/Al2O3 | 92 |\n"
    )
    target = {
        "case_id": "self-test",
        "fields": [{"field_id": "f1", "name": "conversion",
                    "type": "number", "unit": "%", "required": True}],
    }
    good = {
        "case_id": "self-test", "status": "completed", "task_family": "reading",
        "schema": [{"field_id": "f1", "name": "conversion", "type": "number",
                    "unit": "%", "required": True,
                    "definition_source": "target-fields.json"}],
        "extractions": [{
            "field_id": "f1", "field_name": "conversion", "status": "reported",
            "values": [{
                "value": 92, "original": "92%", "unit": "%",
                "unit_status": "reported", "unit_missing": False,
                "statistic": "point_estimate",
                "condition": "350 C", "condition_status": "reported",
                "comparator": "none",
                "locator": {"doc_id": "d1", "paragraph_id": "p2",
                            "quote": "The catalyst reached 92% conversion at 350 C."},
                "source_kind": "paragraph",
                "semantic_check": {"requested_metric": "conversion",
                                   "quoted_metric": "conversion",
                                   "subject": "catalyst",
                                   "matches_requested_field": True},
                "digit_check": {"checked": True, "conflicts": []},
            }],
            "candidates": [], "missing_reason": None,
            "search_scope": "p2, t1",
        }],
        "missing": [], "conflicts": [],
        "summary": {"field_count": 1, "reported_count": 1,
                    "not_reported_count": 0, "extraction_failed_count": 0,
                    "conflict_count": 0, "unit_missing_count": 0,
                    "condition_missing_count": 0, "complete_rate": 1.0},
        "uncertainty": [], "change_log": [{"step": 1, "action": "extract"}],
        "support_files_used": ["references/data-extraction-playbook.md"],
    }
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "target-fields.json").write_text(
            json.dumps(target), encoding="utf-8")
        (root / "paper.md").write_text(paper, encoding="utf-8")
        bundle = load_target_fields(root)
        corpus_info = collect_corpus(root)
        good_report = check_all(good, bundle, corpus_info, True)
        bad = json.loads(json.dumps(good))
        bad["extractions"][0]["values"][0]["unit"] = None
        bad["extractions"][0]["values"][0]["unit_status"] = "reported"
        bad["summary"]["reported_count"] = 99
        bad_report = check_all(bad, bundle, corpus_info, True)
    ok = bool(good_report.problems == [] and bad_report.problems)
    return {
        "ok": ok,
        "self_test": True,
        "good_problems": good_report.problems,
        "good_warnings": good_report.warnings,
        "bad_problem_count": len(bad_report.problems),
        "bad_problems": bad_report.problems,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", default=None)
    parser.add_argument("--input", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as failures")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        report = self_test()
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0 if report["ok"] else 1

    if not args.result:
        parser.error("--result is required unless --self-test is used")
    try:
        result = load_json(args.result)
    except (OSError, json.JSONDecodeError) as exc:
        payload = {"ok": False, "problems": ["cannot read result: %s" % exc],
                   "warnings": [], "checks": {}}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 2

    bundle = {"fields": [], "case_id": None, "error": None}
    corpus_info = {"corpus": "", "paragraph_ids": set(), "table_ids": set(),
                   "figure_ids": set(), "files": 0, "text_files": 0}
    input_given = bool(args.input)
    if args.input:
        bundle = load_target_fields(args.input)
        corpus_info = collect_corpus(args.input)
    report = check_all(result, bundle, corpus_info, input_given)
    payload = {
        "ok": not report.problems,
        "problems": report.problems,
        "warnings": report.warnings,
        "checks": report.checks,
        "input": {
            "path": args.input,
            "case_id": bundle.get("case_id"),
            "target_fields": len(bundle.get("fields") or []),
            "target_file": bundle.get("path"),
            "text_chars": len(corpus_info.get("corpus") or ""),
            "paragraph_ids": sorted(corpus_info.get("paragraph_ids") or []),
            "table_ids": sorted(corpus_info.get("table_ids") or []),
        },
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    if report.problems:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
