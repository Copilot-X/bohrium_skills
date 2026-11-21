#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic pre-finalize quality gate for this Skill.

The script reads the actual /input material and the model-authored work file.
It recomputes counts where the input allows it and rejects hollow sub-fields
(e.g. ``representatives`` present but empty, ``quality_appraisal`` present but
without evidence).  It never writes scientific content and never patches the
work file.

Usage:
    python3 "$SKILL_DIR/scripts/audit_work.py" \
        --input /input --work /app/work --output /app/output

Exit codes: 0 = pass (warnings allowed), 1 = problems found, 2 = cannot run.
The JSON report is written to ``<output>/quality-audit.json``.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

TEXT_SUFFIXES = {".md", ".txt", ".json", ".csv", ".yaml", ".yml", ".tex",
                 ".bib", ".ris", ".xml", ".html"}
CAUSAL_EN = re.compile(
    r"\b(caus(?:e|ed|es|ing)|led to|leads to|drives?|drove|due to|"
    r"resulted in|results in|attributable to|because of)\b", re.I)
CAUSAL_ZH = re.compile(r"导致|使得|造成|推动了|驱动了|引发|归因于|因为.{0,20}所以")
NEGATION = re.compile(r"不|并非|不能|未|没有|无关|not\b|no evidence|cannot|"
                      r"does not|is not|isn't|aren't|rather than|instead of", re.I)
DECISIONS = {"include", "exclude", "duplicate"}
LABELS = {"keep", "maybe", "exclude"}
DIRECTIONS = {"forward", "backward", "undetermined"}


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------
def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default if default is not None else {}


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="ignore")
    except Exception:
        return ""


def norm(value):
    text = str(value if value is not None else "")
    text = text.replace(" ", " ").replace("　", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def norm_id(value):
    return re.sub(r"\s+", "", str(value if value is not None else "")).upper()


def norm_doi(value):
    return norm(value).replace("https://doi.org/", "").replace("doi:", "").strip()


def norm_title(value):
    text = norm(value)
    text = re.sub(r"^(synthetic\s+)?(preprint|protocol|replication)\s*[:.-]?\s*", "", text)
    return re.sub(r"[^0-9a-z一-鿿]+", " ", text).strip()


def as_int(value):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def as_float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def is_empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def csv_rows(path):
    text = read_text(path)
    if not text.strip():
        return [], []
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    try:
        reader = csv.DictReader(text.splitlines(), dialect=dialect)
        fields = [str(x).strip() for x in (reader.fieldnames or [])]
        rows = []
        for raw in reader:
            row = {}
            for key, val in raw.items():
                if key is None:
                    continue
                row[str(key).strip()] = (val or "").strip()
            if any(row.values()):
                rows.append(row)
        return fields, rows
    except Exception:
        return [], []


def field(row, *names):
    """Case/punctuation-insensitive column lookup."""
    lowered = {re.sub(r"[^0-9a-z]+", "", k.lower()): v for k, v in row.items()}
    for name in names:
        key = re.sub(r"[^0-9a-z]+", "", name.lower())
        if key in lowered:
            return lowered[key]
    return ""


class Inputs:
    def __init__(self, root):
        self.root = Path(root)
        self.files = {}
        self.csvs = []
        self.case = {}
        if not self.root.exists():
            return
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            rel = path.relative_to(self.root).as_posix()
            self.files[rel] = read_text(path)
            if path.suffix.lower() == ".csv":
                fields, rows = csv_rows(path)
                self.csvs.append({"path": rel, "fields": fields, "rows": rows,
                                  "text": "\n".join("\t".join(r.values()) for r in rows)})
        case_path = self.root / "case.json"
        if case_path.is_file():
            data = load_json(case_path, {})
            if isinstance(data, dict):
                self.case = data

    def source_text(self, rel, work=None, output=None):
        rel = str(rel or "").replace("\\", "/")
        if rel.startswith("input/"):
            rel = rel[6:]
        if rel in self.files:
            return self.files[rel]
        name = Path(rel).name
        for key, text in self.files.items():
            if key.endswith("/" + name) or key == name:
                return text
        for base, prefix in ((work, "work"), (output, "output")):
            if not base:
                continue
            path = Path(base) / rel
            if path.is_file():
                return read_text(path)
            for cand in Path(base).rglob(name):
                if cand.is_file() and cand.stat().st_size < 2_000_000:
                    return read_text(cand)
        return ""

    def all_text(self):
        return "\n".join(self.files.values())


class Report:
    def __init__(self):
        self.problems = []
        self.warnings = []
        self.recomputed = {}

    def problem(self, message):
        self.problems.append(str(message))

    def warn(self, message):
        self.warnings.append(str(message))

    def check(self, condition, message):
        if not condition:
            self.problem(message)
        return bool(condition)


# --------------------------------------------------------------------------
# evidence and metadata
# --------------------------------------------------------------------------
def walk_evidence(value, out):
    if isinstance(value, dict):
        if isinstance(value.get("quote"), str):
            source = value.get("source") if isinstance(value.get("source"), dict) else {}
            out.append({
                "quote": value.get("quote", ""),
                "path": value.get("path") or source.get("path") or value.get("doc") or "",
                "locator": value.get("locator") or source.get("locator") or value.get("page") or "",
            })
        for item in value.values():
            walk_evidence(item, out)
    elif isinstance(value, list):
        for item in value:
            walk_evidence(item, out)


def check_evidence(work, inputs, work_dir, output_dir, rep):
    found = []
    walk_evidence(work, found)
    if not found:
        rep.problem("no evidence objects with quote/path/locator found")
        return
    for index, item in enumerate(found, 1):
        quote = norm(item["quote"])
        if not quote:
            rep.problem("evidence %d: empty quote" % index)
            continue
        if is_empty(item["locator"]):
            rep.problem("evidence %d: empty locator" % index)
        text = norm(inputs.source_text(item["path"], work_dir, output_dir))
        if not text:
            rep.problem("evidence %d: source path not found: %s" % (index, item["path"]))
        elif quote not in text:
            rep.warn("evidence %d: quote not found verbatim in %s" % (index, item["path"]))


def check_required(work, config, rep):
    for name in config.get("required_fields") or []:
        if name not in work:
            rep.problem("missing required field: %s" % name)
        elif is_empty(work.get(name)):
            rep.problem("empty required field: %s" % name)


def check_case(work, inputs, rep):
    wanted = inputs.case.get("case_id")
    if wanted and str(work.get("case_id")) != str(wanted):
        rep.problem("work case_id %r does not match input case_id %r"
                    % (work.get("case_id"), wanted))
    if is_empty(work.get("summary")) and is_empty(work.get("question")):
        rep.problem("both summary and question are empty")


def causal_problem(text):
    if not text:
        return False
    text = str(text)
    if NEGATION.search(text):
        return False
    return bool(CAUSAL_EN.search(text) or CAUSAL_ZH.search(text))


# --------------------------------------------------------------------------
# trend-analysis
# --------------------------------------------------------------------------
def primary_table(inputs, id_names, year_name):
    scored = []
    for table in inputs.csvs:
        has_id = bool(table["rows"]) and any(
            field(row, *id_names) for row in table["rows"][:3])
        has_year = bool(table["rows"]) and any(
            field(row, year_name) for row in table["rows"][:3])
        if table["rows"] and has_year:
            scored.append((has_id, len(table["rows"]), table))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[0][2] if scored else None


def trend_rows(table):
    rows = []
    for raw in table["rows"]:
        pid = field(raw, "paper_id", "record_id", "id", "candidate_id")
        title = field(raw, "title")
        year = as_int(field(raw, "year", "publication_year"))
        duplicate = field(raw, "duplicate_of", "duplicate")
        notes = field(raw, "notes", "note", "comment")
        if not duplicate:
            match = re.search(r"duplicate\s+of\s+([A-Za-z0-9_.-]+)", notes, re.I)
            if match:
                duplicate = match.group(1)
        rows.append({"id": pid, "title": title, "year": year,
                     "topic": field(raw, "topic", "theme", "subject"),
                     "citations": as_int(field(raw, "citations", "cited_by", "citation_count")),
                     "notes": notes, "duplicate": duplicate, "raw": raw})
    duplicate_ids = {norm_id(r["duplicate"]) for r in rows if r["duplicate"]}
    unique = [r for r in rows if norm_id(r["id"]) not in duplicate_ids]
    return rows, unique, len(duplicate_ids)


def audit_trend(work, inputs, rep):
    table = primary_table(inputs, ("paper_id", "record_id", "id"), "year")
    if table is None:
        rep.warn("no input CSV with a year column; count recomputation skipped")
        check_trend_structure(work, rep, None)
        return
    rows, unique, dup_count = trend_rows(table)
    dated = [r for r in unique if r["year"] is not None]
    missing = [r for r in unique if r["year"] is None]
    expected = {"rows": len(rows), "duplicates": dup_count, "unique": len(unique),
                "dated": len(dated), "missing_year": len(missing)}
    rep.recomputed["denominator"] = expected

    denom = work.get("denominator")
    if not isinstance(denom, dict):
        denom = (work.get("data_quality") or {}).get("denominator") if isinstance(
            work.get("data_quality"), dict) else None
    if not isinstance(denom, dict) or not denom:
        rep.problem("denominator/data_quality.denominator is missing")
    else:
        for key, value in expected.items():
            got = as_int(denom.get(key))
            if got is None:
                rep.problem("denominator lacks integer %s" % key)
            elif got != value:
                rep.problem("denominator.%s=%s but input recomputes %s"
                            % (key, got, value))
    if dup_count:
        removed = (work.get("data_quality") or {}).get("duplicates_removed") \
            if isinstance(work.get("data_quality"), dict) else None
        if not removed:
            rep.problem("duplicates exist in input but data_quality.duplicates_removed is empty")

    # year counts
    year_counts = work.get("year_counts") or []
    if not isinstance(year_counts, list) or not year_counts:
        rep.problem("year_counts is empty")
    else:
        got = {}
        missing_row = None
        denominator_ok = True
        for item in year_counts:
            if not isinstance(item, dict):
                rep.problem("year_counts entry is not an object")
                continue
            year = item.get("year")
            count = as_int(item.get("count"))
            denominator = as_int(item.get("denominator"))
            if str(year).lower() in ("missing", "unknown", "null", "none", ""):
                missing_row = item
                continue
            year_int = as_int(year)
            if year_int is None:
                rep.problem("year_counts has non-numeric year: %r" % (year,))
                continue
            if count is None:
                rep.problem("year_counts[%s] has no integer count" % year_int)
                continue
            if denominator != len(dated):
                denominator_ok = False
            got[year_int] = got.get(year_int, 0) + count
        want = {}
        for row in dated:
            want[row["year"]] = want.get(row["year"], 0) + 1
        if sum(got.values()) != len(dated):
            rep.problem("sum(year_counts numeric)=%d but dated unique records=%d"
                        % (sum(got.values()), len(dated)))
        for year in sorted(set(got) | set(want)):
            if got.get(year, 0) != want.get(year, 0):
                rep.problem("year_counts[%s]=%s but input recomputes %s"
                            % (year, got.get(year, 0), want.get(year, 0)))
        if not denominator_ok:
            rep.problem("year_counts denominator must equal dated unique records (%d)"
                        % len(dated))
        if missing and (missing_row is None or as_int(missing_row.get("count")) != len(missing)):
            rep.problem("missing-year records exist (%d) but no matching 'missing' year_counts row"
                        % len(missing))
        rep.recomputed["year_counts"] = want

    # topic counts, with optional mapping
    topic_counts = work.get("topic_counts") or []
    mapping = work.get("topic_mapping") if isinstance(work.get("topic_mapping"), dict) else {}
    want_topics = {}
    for row in unique:
        topic = str(mapping.get(row["topic"], row["topic"]) or "").strip()
        if not topic:
            continue
        for part in re.split(r"[;|]", topic):
            part = part.strip()
            if part:
                want_topics[part] = want_topics.get(part, 0) + 1
    if want_topics:
        got_topics = {}
        topic_denominator_ok = True
        for item in topic_counts:
            if not isinstance(item, dict):
                rep.problem("topic_counts entry is not an object")
                continue
            topic = str(item.get("topic") or "").strip()
            count = as_int(item.get("count"))
            denominator = as_int(item.get("denominator"))
            if not topic or count is None:
                rep.problem("topic_counts entry lacks topic/count")
                continue
            got_topics[topic] = got_topics.get(topic, 0) + count
            if denominator != len(unique):
                topic_denominator_ok = False
        for topic in sorted(set(got_topics) | set(want_topics),
                            key=lambda x: x.lower()):
            if got_topics.get(topic, 0) != want_topics.get(topic, 0):
                rep.problem("topic_counts[%s]=%s but input recomputes %s"
                            % (topic, got_topics.get(topic, 0), want_topics.get(topic, 0)))
        if sum(got_topics.values()) != len(unique):
            rep.problem("sum(topic_counts)=%d but unique records=%d"
                        % (sum(got_topics.values()), len(unique)))
        if not topic_denominator_ok:
            rep.problem("topic_counts denominator must equal unique records (%d)" % len(unique))
        rep.recomputed["topic_counts"] = want_topics
    else:
        rep.warn("input CSV has no topic column; topic recomputation skipped")

    check_trend_structure(work, rep, unique)
    check_trend_representatives(work, unique, rep)
    check_trend_findings(work, rep)
    check_trend_gaps(work, rep)


def check_trend_structure(work, rep, unique):
    tw = work.get("time_window")
    if not isinstance(tw, dict) or not tw:
        rep.problem("time_window is not an object with start/end")
        return
    start, end = as_int(tw.get("start")), as_int(tw.get("end"))
    if start is None or end is None or start > end:
        rep.problem("time_window.start/end must be valid ordered years")
    if is_empty(tw.get("as_of")) and is_empty(tw.get("coverage_note")):
        rep.problem("time_window needs as_of or coverage_note (data cutoff/incomplete year)")
    rule = str(work.get("representative_rule") or "")
    if len(rule) < 20:
        rep.problem("representative_rule is a placeholder; state role/stage/citation criteria")
    elif re.search(r"^(rule|synthetic|todo|tbd)", rule.strip(), re.I):
        rep.problem("representative_rule is a placeholder")
    elif not re.search(r"first|method|stage|role|低被引|首创|阶段", rule, re.I):
        rep.warn("representative_rule does not mention stage/role/first-method criteria")
    if unique is not None:
        topics = {norm(r["topic"]) for r in unique if r["topic"]}
        mapping = work.get("topic_mapping")
        if len(topics) > 1 and not mapping:
            rep.warn("multiple raw topics but no topic_mapping (term merging rule)")


def check_trend_representatives(work, unique, rep):
    reps = work.get("representatives") or []
    if not isinstance(reps, list) or not reps:
        rep.problem("representatives is empty")
        return
    ids = {norm_id(r["id"]) for r in unique or []}
    seen = set()
    for index, item in enumerate(reps, 1):
        if not isinstance(item, dict):
            rep.problem("representative %d is not an object" % index)
            continue
        rid = norm_id(item.get("record_id") or item.get("id"))
        if not rid:
            rep.problem("representative %d lacks record_id" % index)
            continue
        if ids and rid not in ids:
            rep.problem("representative %d record_id %s is not in input" % (index, rid))
        if rid in seen:
            rep.problem("duplicate representative record_id: %s" % rid)
        seen.add(rid)
        role = str(item.get("role") or "")
        reason = str(item.get("reason") or "")
        if is_empty(role) or len(reason.strip()) < 8:
            rep.problem("representative %s lacks role/reason" % rid)
        if re.search(r"citation", reason, re.I) and not re.search(
                r"first|method|stage|首创|阶段|方法", reason, re.I):
            rep.warn("representative %s reason looks citation-only" % rid)
    if not unique:
        return
    # stage coverage
    stages = []
    tw = work.get("time_window") if isinstance(work.get("time_window"), dict) else {}
    raw_stages = work.get("stages") or tw.get("stages")
    if isinstance(raw_stages, list):
        for stage in raw_stages:
            if isinstance(stage, dict):
                stages.append((stage.get("name") or stage.get("label"),
                               as_int(stage.get("start")), as_int(stage.get("end"))))
            elif isinstance(stage, (list, tuple)) and len(stage) == 3:
                stages.append((stage[0], as_int(stage[1]), as_int(stage[2])))
    by_id = {norm_id(r["id"]): r for r in unique}
    if stages:
        for name, start, end in stages:
            covered = any(by_id.get(rid, {}).get("year") is not None
                          and start is not None and end is not None
                          and start <= by_id[rid]["year"] <= end for rid in seen)
            if not covered:
                rep.problem("representatives do not cover stage %s (%s-%s)"
                            % (name, start, end))
    else:
        years = sorted({r["year"] for r in unique if r["year"] is not None})
        if years:
            for edge_year in (years[0], years[-1]):
                if not any(by_id.get(rid, {}).get("year") == edge_year for rid in seen):
                    rep.warn("no representative for edge year %d" % edge_year)
    first_low = [r for r in unique if re.search(r"first|首次|首创", r["notes"], re.I)
                 and r["citations"] is not None and r["citations"] <= 100]
    for row in first_low:
        if norm_id(row["id"]) not in seen:
            rep.warn("low-citation first-method record %s is not a representative; "
                     "explain the exclusion in the report" % row["id"])


def check_trend_findings(work, rep):
    findings = work.get("findings") or []
    if not isinstance(findings, list) or not findings:
        rep.problem("findings is empty")
        return
    types = set()
    for index, item in enumerate(findings, 1):
        if not isinstance(item, dict):
            rep.problem("finding %d is not an object" % index)
            continue
        etype = str(item.get("evidence_type") or "").strip().lower()
        if etype not in ("count", "co-occurrence", "qualitative", "inference", "descriptive"):
            rep.problem("finding %s evidence_type must be count/co-occurrence/qualitative/"
                        "inference (got %r)" % (item.get("id", index), etype))
        types.add(etype)
        if causal_problem(item.get("statement")):
            rep.problem("finding %s uses causal wording; counts/co-occurrence cannot "
                        "establish causation" % item.get("id", index))
        if not item.get("evidence"):
            rep.problem("finding %s has no evidence" % item.get("id", index))
    if "count" not in types:
        rep.warn("no finding has evidence_type=count; trends may not be backed by counts")


def check_trend_gaps(work, rep):
    gaps = work.get("gap_analysis") or work.get("gaps") or []
    if not isinstance(gaps, list) or not gaps:
        rep.problem("gap_analysis is empty")
        return
    for index, item in enumerate(gaps, 1):
        if not isinstance(item, dict):
            rep.problem("gap %d is not an object" % index)
            continue
        if is_empty(item.get("gap")) or is_empty(item.get("basis")):
            rep.problem("gap %d lacks gap/basis" % index)
        if not (item.get("excluded_alternative") or item.get("why_not_omission")
                or item.get("search_scope") or work.get("search_scope")):
            rep.problem("gap %d does not exclude retrieval omission "
                        "(add excluded_alternative/why_not_omission)" % index)


# --------------------------------------------------------------------------
# systematic-review
# --------------------------------------------------------------------------
def find_records_table(inputs):
    best = None
    for table in inputs.csvs:
        if not table["rows"]:
            continue
        if any(field(row, "record_id", "study_id", "id") for row in table["rows"][:3]):
            if best is None or len(table["rows"]) > len(best["rows"]):
                best = table
    return best


def audit_review(work, inputs, rep):
    protocol = work.get("protocol") if isinstance(work.get("protocol"), dict) else {}
    if not protocol:
        rep.problem("protocol is not an object")
    else:
        for name in ("version", "search_string", "databases", "date", "limits"):
            if is_empty(protocol.get(name)):
                rep.problem("protocol lacks %s" % name)
        databases = protocol.get("databases")
        if not isinstance(databases, list) or not databases:
            rep.problem("protocol.databases must be a non-empty list")
        search_string = str(protocol.get("search_string") or "")
        if len(search_string.strip()) < 12:
            rep.problem("protocol.search_string is too short to be executable")
        elif not re.search(r"\b(and|or|not)\b|\(|\)", search_string, re.I):
            rep.problem("protocol.search_string has no boolean/field syntax")
        if protocol.get("fields") is not None and not isinstance(protocol.get("fields"), list):
            rep.problem("protocol.fields must be a list")
        try:
            date.fromisoformat(str(protocol.get("date")))
        except ValueError:
            rep.problem("protocol.date must be YYYY-MM-DD")
        if protocol.get("executed") is False and is_empty(protocol.get("coverage_note")):
            rep.problem("protocol.executed=false requires a coverage_note")
        if "executed" not in protocol:
            rep.warn("protocol.executed is not stated; state whether a live search was run")

    screening = work.get("screening") if isinstance(work.get("screening"), dict) else {}
    counts = {}
    for name in ("identified", "duplicates", "screened", "excluded", "full_text", "included"):
        value = as_int(screening.get(name))
        if value is None:
            rep.problem("screening.%s is missing or non-integer" % name)
        counts[name] = value
    if all(v is not None for v in counts.values()):
        if counts["identified"] - counts["duplicates"] != counts["screened"]:
            rep.problem("PRISMA: identified - duplicates != screened")
        if counts["screened"] - counts["excluded"] != counts["included"]:
            rep.problem("PRISMA: screened - excluded != included")
        if counts["full_text"] > counts["screened"]:
            rep.problem("PRISMA: full_text > screened")
        if counts["included"] > counts["full_text"]:
            rep.problem("PRISMA: included > full_text")
        rep.recomputed["screening_counts"] = counts

    records = screening.get("records") or []
    if not isinstance(records, list) or not records:
        rep.problem("screening.records is empty; per-record decisions are missing")
        records = []
    seen = {}
    for index, item in enumerate(records, 1):
        if not isinstance(item, dict):
            rep.problem("screening record %d is not an object" % index)
            continue
        rid = norm_id(item.get("record_id") or item.get("id"))
        if not rid:
            rep.problem("screening record %d lacks record_id" % index)
            continue
        seen.setdefault(rid, []).append(item)
        decision = str(item.get("decision") or "").lower()
        if decision not in DECISIONS:
            rep.problem("record %s decision must be include/exclude/duplicate" % rid)
        if decision != "include" and is_empty(item.get("reason")):
            rep.problem("record %s decision %s lacks a reason" % (rid, decision))
        if is_empty(item.get("stage")):
            rep.warn("record %s lacks screening stage" % rid)
        if decision == "include" and is_empty(item.get("evidence")):
            rep.problem("included record %s lacks evidence" % rid)
    for rid, items in seen.items():
        if len(items) > 1:
            rep.problem("record %s appears %d times; use one row per record" % (rid, len(items)))

    table = find_records_table(inputs)
    if table is not None:
        input_rows = table["rows"]
        expected = {}
        for raw in input_rows:
            rid = norm_id(field(raw, "record_id", "study_id", "id"))
            if not rid:
                continue
            duplicate = field(raw, "duplicate_of", "duplicate")
            reason = field(raw, "exclusion_reason", "exclude_reason", "reason")
            full_text = field(raw, "full_text", "fulltext_available", "full_text_available")
            if duplicate:
                expected[rid] = "duplicate"
            elif reason:
                expected[rid] = "exclude"
            elif str(full_text).lower() in ("no", "false", "0", "n", "unavailable"):
                expected[rid] = "exclude"
            else:
                expected[rid] = "include"
        missing_ids = [rid for rid in expected if rid not in seen]
        if missing_ids:
            rep.problem("screening.records misses input records: %s" % ", ".join(missing_ids))
        for rid, want in expected.items():
            got = seen.get(rid, [{}])[0].get("decision")
            if got and str(got).lower() != want:
                rep.problem("record %s decision=%s but input fields imply %s"
                            % (rid, got, want))
        expected_counts = {
            "identified": len(expected),
            "duplicates": sum(1 for v in expected.values() if v == "duplicate"),
            "included": sum(1 for v in expected.values() if v == "include"),
        }
        expected_counts["screened"] = (expected_counts["identified"]
                                       - expected_counts["duplicates"])
        expected_counts["excluded"] = (expected_counts["screened"]
                                       - expected_counts["included"])
        rep.recomputed["expected_screening"] = expected_counts
        for name, value in expected_counts.items():
            if counts.get(name) is not None and counts[name] != value:
                rep.problem("screening.%s=%s but input recomputes %s"
                            % (name, counts[name], value))

    quality = work.get("quality_appraisal") or []
    if not isinstance(quality, list) or not quality:
        rep.problem("quality_appraisal is empty")
        quality = []
    appraised = {}
    for index, item in enumerate(quality, 1):
        if not isinstance(item, dict):
            rep.problem("quality_appraisal %d is not an object" % index)
            continue
        rid = norm_id(item.get("record_id") or item.get("id"))
        if not rid:
            rep.problem("quality_appraisal %d lacks record_id" % index)
            continue
        appraised.setdefault(rid, []).append(item)
        if is_empty(item.get("tool")):
            rep.problem("quality_appraisal %s lacks tool" % rid)
        if not isinstance(item.get("domains"), list) or not item.get("domains"):
            rep.problem("quality_appraisal %s lacks domains list" % rid)
        judgement = str(item.get("judgement") or "").lower()
        if judgement not in ("low_risk", "some_concerns", "high_risk", "low", "moderate",
                             "high", "unclear"):
            rep.problem("quality_appraisal %s judgement must be low_risk/some_concerns/"
                        "high_risk (got %r)" % (rid, judgement))
        if is_empty(item.get("evidence")):
            rep.problem("quality_appraisal %s lacks per-record evidence" % rid)
        if is_empty(item.get("effect_on_conclusion")):
            rep.warn("quality_appraisal %s does not state its effect on the conclusion" % rid)
    included_ids = {rid for rid, items in seen.items()
                    if items and str(items[0].get("decision")).lower() == "include"}
    for rid in sorted(included_ids):
        if rid not in appraised:
            rep.problem("included record %s has no quality appraisal" % rid)
        elif len(appraised[rid]) > 1:
            rep.warn("record %s has multiple quality appraisal rows" % rid)
    for rid in appraised:
        if included_ids and rid not in included_ids:
            rep.warn("quality appraisal of non-included record %s" % rid)

    coverage = work.get("coverage") or work.get("coverage_facets")
    facets = ("question_breakdown", "search_coverage", "evidence_chain",
              "conflicting_evidence", "synthesis", "gaps")
    if not isinstance(coverage, dict) or not coverage:
        rep.problem("coverage (six-facet) is missing")
    else:
        for facet in facets:
            item = coverage.get(facet)
            if not isinstance(item, dict):
                rep.problem("coverage.%s is missing" % facet)
                continue
            status = str(item.get("status") or "").lower()
            if status not in ("covered", "partial", "not_covered", "not_applicable"):
                rep.problem("coverage.%s status must be covered/partial/not_covered" % facet)
            if status == "covered" and is_empty(item.get("evidence")) \
                    and is_empty(item.get("locator")) and is_empty(item.get("items")):
                rep.problem("coverage.%s is covered but has no evidence/items" % facet)

    subquestions = work.get("subquestions") or []
    if not isinstance(subquestions, list) or not subquestions:
        rep.warn("subquestions is empty; question decomposition is not inspectable")
    else:
        for index, item in enumerate(subquestions, 1):
            if isinstance(item, dict) and is_empty(item.get("status")):
                rep.problem("subquestion %d lacks status" % index)

    findings = work.get("findings") or []
    if not isinstance(findings, list) or not findings:
        rep.problem("findings is empty")
    synthesis = work.get("synthesis")
    if not isinstance(synthesis, dict) or is_empty(synthesis.get("conclusions")):
        rep.problem("synthesis.conclusions is missing; results are not integrated")
    else:
        for index, item in enumerate(synthesis.get("conclusions") or [], 1):
            if not isinstance(item, dict):
                rep.problem("synthesis conclusion %d is not an object" % index)
                continue
            if is_empty(item.get("statement")):
                rep.problem("synthesis conclusion %d lacks statement" % index)
            if is_empty(item.get("records")) and is_empty(item.get("evidence")):
                rep.problem("synthesis conclusion %d lacks record/evidence links" % index)
            if is_empty(item.get("strength")):
                rep.warn("synthesis conclusion %d lacks evidence strength" % index)
    conflict = work.get("conflict_check")
    if not isinstance(conflict, dict) or "checked" not in conflict:
        rep.problem("conflict_check is missing (checked + items)")
    elif conflict.get("checked") and not isinstance(conflict.get("items"), list):
        rep.problem("conflict_check.items must be a list (may be empty)")

    gaps = work.get("gap_analysis") or []
    if not isinstance(gaps, list) or not gaps:
        rep.problem("gap_analysis is empty")
    for index, item in enumerate(gaps, 1):
        if isinstance(item, dict):
            if is_empty(item.get("basis")):
                rep.problem("gap %d lacks basis" % index)
            if not (item.get("why_not_omission") or item.get("excluded_alternative")
                    or work.get("search_scope")):
                rep.problem("gap %d does not exclude retrieval omission" % index)


# --------------------------------------------------------------------------
# screening-ranking
# --------------------------------------------------------------------------
def candidate_inputs(inputs):
    for table in inputs.csvs:
        if not table["rows"]:
            continue
        if field(table["rows"][0], "candidate_id", "id") and field(
                table["rows"][0], "title"):
            return table
    return None


def audit_screening(work, inputs, rep):
    table = candidate_inputs(inputs)
    input_rows = {}
    if table is not None:
        for raw in table["rows"]:
            cid = norm_id(field(raw, "candidate_id", "id"))
            if cid:
                input_rows[cid] = raw
    else:
        rep.warn("no candidate CSV found; metadata recomputation skipped")

    candidates = work.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        rep.problem("candidates is empty")
        return
    by_id = {}
    for index, item in enumerate(candidates, 1):
        if not isinstance(item, dict):
            rep.problem("candidate %d is not an object" % index)
            continue
        cid = norm_id(item.get("id") or item.get("candidate_id"))
        if not cid:
            rep.problem("candidate %d lacks id" % index)
            continue
        if cid in by_id:
            rep.problem("duplicate candidate id %s" % cid)
        by_id[cid] = item
        label = str(item.get("label") or "").lower()
        if label not in LABELS:
            rep.problem("candidate %s label must be keep/maybe/exclude" % cid)
        if is_empty(item.get("reason")):
            rep.problem("candidate %s lacks reason" % cid)
        if is_empty(item.get("evidence")):
            rep.problem("candidate %s lacks evidence" % cid)
        if label in ("keep", "maybe") and not item.get("criteria_hits"):
            rep.problem("candidate %s is %s but has no criteria_hits" % (cid, label))
        if label == "exclude" and is_empty(item.get("exclusion_rule")) \
                and not item.get("criteria_hits"):
            rep.problem("candidate %s is excluded without exclusion_rule/criteria_hits" % cid)
        if input_rows:
            raw = input_rows.get(cid)
            if raw is None:
                rep.problem("candidate %s is not in the supplied candidate set" % cid)
                continue
            for key, names in (("title", ("title",)), ("doi", ("doi",)),
                               ("year", ("year",)), ("venue", ("venue",))):
                want = field(raw, *names)
                got = item.get(key)
                if is_empty(want):
                    continue
                if key == "doi":
                    match = norm_doi(got) == norm_doi(want)
                elif key == "year":
                    match = as_int(got) == as_int(want)
                else:
                    match = norm(got) == norm(want)
                if not match:
                    rep.problem("candidate %s %s=%r does not match input %r"
                                % (cid, key, got, want))
            if is_empty(field(raw, "abstract")) and is_empty(item.get("abstract")):
                if label == "exclude" and re.search(
                        r"abstract|missing|no metadata", str(item.get("reason")), re.I):
                    rep.problem("candidate %s excludes a record only for missing metadata; "
                                "escalate to human instead" % cid)
                if not item.get("escalated_to_human"):
                    rep.warn("candidate %s has no abstract; set escalated_to_human" % cid)
            if is_empty(field(raw, "year")) and is_empty(item.get("year")):
                if label == "exclude" and re.search(
                        r"year|metadata|missing", str(item.get("reason")), re.I):
                    rep.problem("candidate %s excludes a record only for missing year" % cid)
                if not item.get("escalated_to_human"):
                    rep.warn("candidate %s has no year; set escalated_to_human" % cid)

    if input_rows:
        missing = [cid for cid in input_rows if cid not in by_id]
        if missing:
            rep.problem("candidates miss input records: %s" % ", ".join(sorted(missing)))
        rep.recomputed["candidate_coverage"] = "%d/%d" % (
            len(input_rows) - len(missing), len(input_rows))

    screening = work.get("screening") if isinstance(work.get("screening"), dict) else {}
    if not screening:
        rep.problem("screening is not an object")
    layers = {name: {norm_id(x) for x in (screening.get(name) or [])}
              for name in ("keep", "maybe", "exclude")}
    for name, ids in layers.items():
        unknown = ids - set(by_id)
        if unknown:
            rep.problem("screening.%s lists unknown ids: %s"
                        % (name, ", ".join(sorted(unknown))))
    listed = set().union(*layers.values())
    for cid, item in by_id.items():
        label = str(item.get("label") or "").lower()
        if label in LABELS and cid not in layers.get(label, set()):
            rep.problem("candidate %s label=%s but is not in screening.%s"
                        % (cid, label, label))
        memberships = [name for name in LABELS if cid in layers.get(name, set())]
        if len(memberships) > 1:
            rep.problem("candidate %s appears in multiple screening layers: %s"
                        % (cid, memberships))
    if layers["keep"] & layers["maybe"] or layers["keep"] & layers["exclude"] \
            or layers["maybe"] & layers["exclude"]:
        rep.problem("screening layers overlap")
    for layer, ids in layers.items():
        if not isinstance(screening.get(layer), list):
            rep.problem("screening.%s must be a list" % layer)
        if not is_empty(screening.get(layer)) and not ids:
            rep.problem("screening.%s has no parseable ids" % layer)
    if not screening.get("criteria"):
        rep.problem("screening.criteria is empty; layer criteria are missing")

    rules = work.get("decision_rules") or []
    rule_ids = set()
    if isinstance(rules, list):
        for index, rule in enumerate(rules, 1):
            if not isinstance(rule, dict):
                rep.problem("decision_rule %d is not an object" % index)
                continue
            rid = str(rule.get("id") or rule.get("rule_id") or "")
            if not rid:
                rep.problem("decision_rule %d lacks id" % index)
            rule_ids.add(rid)
            if is_empty(rule.get("condition")):
                rep.problem("decision_rule %s lacks condition" % rid)
            if str(rule.get("decision") or "").lower() not in LABELS:
                rep.problem("decision_rule %s decision must be keep/maybe/exclude" % rid)
    else:
        rep.problem("decision_rules must be a list")
    if not rules:
        rep.warn("decision_rules is empty; label criteria are not machine-checkable")
    for cid, item in by_id.items():
        for hit in item.get("criteria_hits") or []:
            hit_id = str(hit.get("rule_id") if isinstance(hit, dict) else hit)
            if rule_ids and hit_id not in rule_ids:
                rep.problem("candidate %s criteria_hit %r is not a decision rule" % (cid, hit_id))

    keep = sorted([(as_int(i.get("rank")), cid, i) for cid, i in by_id.items()
                   if str(i.get("label")).lower() == "keep"], key=lambda x: (x[0] is None, x[0] or 0))
    if not keep:
        rep.warn("no keep candidates; ranked_keep/recall checks are vacuous")
    else:
        ranks = [rank for rank, _, _ in keep]
        if any(rank is None or rank < 1 for rank in ranks):
            rep.problem("all keep candidates need integer rank >= 1")
        else:
            if len(set(ranks)) != len(ranks):
                rep.problem("keep ranks contain duplicates")
            if sorted(ranks) != list(range(1, len(ranks) + 1)):
                rep.problem("keep ranks must be contiguous 1..%d" % len(ranks))
        scores = []
        for rank, cid, item in keep:
            score = as_float(item.get("score"))
            if score is None:
                rep.problem("keep candidate %s has no numeric score" % cid)
            scores.append((rank, cid, score, item))
        ordered = [x for x in sorted(scores, key=lambda x: x[0] or 10**9)]
        for prior, current in zip(ordered, ordered[1:]):
            if prior[2] is not None and current[2] is not None and current[2] > prior[2]:
                rep.problem("score increases with rank: %s (%s) > %s (%s)"
                            % (current[1], current[2], prior[1], prior[2]))
            if prior[2] is not None and current[2] is not None and abs(prior[2] - current[2]) < 1e-9:
                if is_empty(current[3].get("tie_break")):
                    rep.problem("tied scores need tie_break: %s/%s"
                                % (prior[1], current[1]))
                else:
                    key = str(current[3].get("tie_break") or "").lower()
                    if key in ("title", "candidate_id", "id", "year"):
                        a = norm(prior[3].get(key if key != "candidate_id" else "id"))
                        b = norm(current[3].get(key if key != "candidate_id" else "id"))
                        if a and b and a > b:
                            rep.problem("tie_break %s order is inconsistent at %s/%s"
                                        % (key, prior[1], current[1]))
    for cid, item in by_id.items():
        if str(item.get("label")).lower() in ("maybe", "exclude"):
            if item.get("rank") not in (None, "", 0):
                if as_int(item.get("rank")) is not None:
                    rep.problem("non-keep candidate %s should not have a numeric rank" % cid)

    metrics = work.get("metrics") if isinstance(work.get("metrics"), dict) else {}
    k = as_int(metrics.get("k"))
    if k is None or k < 1:
        rep.problem("metrics.k must be a positive integer")
        k = None
    ranked_keep = work.get("ranked_keep") or metrics.get("ranked_keep") or []
    if not isinstance(ranked_keep, list):
        rep.problem("ranked_keep must be a list")
        ranked_keep = []
    if k is not None:
        effective = min(k, len(by_id))
        if not ranked_keep:
            rep.problem("ranked_keep is missing; top-k cannot be checked against k=%d" % k)
        else:
            expected_ranked = [cid for _, cid, _ in keep][:effective]
            if [norm_id(x) for x in ranked_keep] != expected_ranked:
                rep.problem("ranked_keep does not equal top-%d keep ids by rank" % effective)
            if len(ranked_keep) < effective and is_empty(metrics.get("k_shortfall_note")) \
                    and len(keep) >= effective:
                rep.problem("ranked_keep has fewer than k entries without k_shortfall_note")
        relevant_top = len(set(norm_id(x) for x in ranked_keep[:effective]) & layers["keep"])
        precision = relevant_top / effective if effective else 0.0
        rep.recomputed["precision_at_k"] = round(precision, 4)
        got_precision = as_float(metrics.get("precision_at_k"))
        if got_precision is None:
            rep.problem("metrics.precision_at_k is missing")
        elif abs(got_precision - precision) > 0.02:
            rep.problem("metrics.precision_at_k=%s but recomputes %.4f"
                        % (got_precision, precision))
        if layers["keep"]:
            recall = relevant_top / len(layers["keep"])
            rep.recomputed["recall_at_k"] = round(recall, 4)
            got_recall = as_float(metrics.get("recall_at_k"))
            if got_recall is not None and abs(got_recall - recall) > 0.02:
                rep.problem("metrics.recall_at_k=%s but recomputes %.4f"
                            % (got_recall, recall))
    for name in ("ndcg_at_k", "metadata_accuracy"):
        value = as_float(metrics.get(name))
        if value is None:
            rep.problem("metrics.%s is missing" % name)
        elif not (0.0 <= value <= 1.0):
            rep.problem("metrics.%s must be in [0,1]" % name)
    if is_empty(metrics.get("definition")):
        rep.problem("metrics.definition is missing")

    # version / duplicate groups
    if input_rows:
        groups = {}
        for cid, raw in input_rows.items():
            title = norm_title(field(raw, "title"))
            if title:
                groups.setdefault(title, []).append((cid, raw))
        for title, group in groups.items():
            if len(group) < 2:
                continue
            members = {cid for cid, _ in group}
            merged = [cid for cid, item in by_id.items()
                      if members & {norm_id(x) for x in (item.get("merged_from") or [])}]
            separate = [cid for cid in members if cid in by_id
                        and not is_empty(by_id[cid].get("keep_separate_reason"))]
            if not merged and len(separate) < len(members):
                rep.problem("same-title group %r is neither merged nor marked "
                            "keep_separate_reason" % title)
            elif merged:
                for cid in merged:
                    if is_empty(by_id[cid].get("version_basis")):
                        rep.problem("merged candidate %s lacks version_basis" % cid)


# --------------------------------------------------------------------------
# citation-tracking
# --------------------------------------------------------------------------
def parse_records_md(text):
    records = []
    current = None
    for line in text.splitlines():
        match = re.match(r"^#{2,4}\s+([A-Za-z0-9_.-]+)\s*$", line.strip())
        if match:
            current = {"id": match.group(1)}
            records.append(current)
            continue
        match = re.match(r"^([A-Za-z][A-Za-z /_-]{1,30}):\s*(.*)$", line.strip())
        if match and current is not None:
            key = re.sub(r"[^a-z]", "", match.group(1).lower())
            current[key] = match.group(2).strip()
    return records


def input_citation_records(inputs):
    seed = None
    records = []
    for rel, text in inputs.files.items():
        if "seed" in Path(rel).stem.lower() and seed is None:
            parsed = parse_records_md(text)
            if parsed:
                seed = parsed[0]
            else:
                seed = {}
                for line in text.splitlines():
                    match = re.match(r"^([A-Za-z ]+):\s*(.*)$", line.strip())
                    if match:
                        seed[re.sub(r"[^a-z]", "", match.group(1).lower())] = match.group(2).strip()
        if "citation" in Path(rel).stem.lower() or "reference" in Path(rel).stem.lower():
            records.extend(parse_records_md(text))
    return seed or {}, records


def audit_citation(work, inputs, rep):
    seed, input_records = input_citation_records(inputs)
    input_by_id = {norm_id(r.get("id")): r for r in input_records if r.get("id")}
    input_dois = {norm_doi(r.get("doi")): r for r in input_records if r.get("doi")}
    if seed.get("doi"):
        input_dois[norm_doi(seed["doi"])] = seed

    seeds = work.get("seeds") or []
    if not isinstance(seeds, list) or not seeds:
        rep.problem("seeds is empty")
    else:
        matched = False
        for index, item in enumerate(seeds, 1):
            if not isinstance(item, dict):
                rep.problem("seed %d is not an object" % index)
                continue
            if is_empty(item.get("title")) or is_empty(item.get("evidence")):
                rep.problem("seed %d lacks title/evidence" % index)
            if seed:
                same = norm_doi(item.get("doi")) == norm_doi(seed.get("doi")) \
                    or norm_title(item.get("title")) == norm_title(seed.get("title"))
                if same:
                    matched = True
                    for key in ("title", "year", "doi"):
                        if not is_empty(seed.get(key)) and norm(item.get(key)) != norm(seed.get(key)):
                            rep.problem("seed %s does not match input seed" % key)
            if is_empty(item.get("id")):
                rep.warn("seed %d has no local id" % index)
        if seed and not matched:
            rep.problem("no work seed matches the input seed metadata")

    edges = work.get("edges") or []
    if not isinstance(edges, list) or not edges:
        rep.problem("edges is empty")
        return
    edge_by_id = {}
    endpoints = set()
    for index, edge in enumerate(edges, 1):
        if not isinstance(edge, dict):
            rep.problem("edge %d is not an object" % index)
            continue
        eid = str(edge.get("id") or "E%d" % index)
        if eid in edge_by_id:
            rep.problem("duplicate edge id %s" % eid)
        edge_by_id[eid] = edge
        for name in ("from", "to", "direction", "source"):
            if is_empty(edge.get(name)):
                rep.problem("edge %s lacks %s" % (eid, name))
        direction = str(edge.get("direction") or "").lower()
        if direction not in DIRECTIONS:
            rep.problem("edge %s direction must be forward/backward/undetermined" % eid)
        if direction == "undetermined":
            rep.problem("edge %s is undetermined; move it to direction_unknown instead" % eid)
        if norm_id(edge.get("from")) == norm_id(edge.get("to")):
            rep.problem("edge %s is a self-loop" % eid)
        if as_int(edge.get("hop")) is None or as_int(edge.get("hop")) < 1:
            rep.problem("edge %s lacks positive integer hop" % eid)
        if is_empty(edge.get("basis")):
            rep.warn("edge %s lacks a short basis note" % eid)
        source = edge.get("source")
        if isinstance(source, dict) and (is_empty(source.get("path"))
                                         or is_empty(source.get("locator"))):
            rep.problem("edge %s source lacks path/locator" % eid)
        endpoints.add(norm_id(edge.get("from")))
        endpoints.add(norm_id(edge.get("to")))
        # direction check against explicit note semantics, when present
        from_rec = input_by_id.get(norm_id(edge.get("from")))
        to_rec = input_by_id.get(norm_id(edge.get("to")))
        notes = " ".join(str(x.get("note", "")) for x in (from_rec, to_rec) if x)
        if notes:
            note = norm(notes)
            if "seed paper cites this work" in note and direction != "backward":
                rep.problem("edge %s should be backward: the seed cites this work" % eid)
            if re.search(r"cites the seed paper", note) and direction != "forward":
                rep.problem("edge %s should be forward: this work cites the seed" % eid)

    paths = work.get("paths") or []
    if not isinstance(paths, list) or not paths:
        rep.problem("paths is empty")
    directed_edges = 0
    for index, path in enumerate(paths, 1):
        if not isinstance(path, dict):
            rep.problem("path %d is not an object" % index)
            continue
        pid = path.get("id") or "P%d" % index
        nodes = [norm_id(x) for x in (path.get("nodes") or [])]
        edge_ids = [str(x) for x in (path.get("edges") or [])]
        if len(nodes) < 2 or not edge_ids:
            rep.problem("path %s is not a complete hop chain" % pid)
            continue
        if len(nodes) != len(edge_ids) + 1:
            rep.problem("path %s nodes/edges lengths disagree" % pid)
        for edge_id in edge_ids:
            edge = edge_by_id.get(edge_id)
            if edge is None:
                rep.problem("path %s references unknown edge %s" % (pid, edge_id))
        for a, b, edge_id in zip(nodes, nodes[1:], edge_ids):
            edge = edge_by_id.get(edge_id)
            if not isinstance(edge, dict):
                continue
            ef, et = norm_id(edge.get("from")), norm_id(edge.get("to"))
            if a == ef and b == et:
                directed_edges += 1
            elif a == et and b == ef:
                rep.problem("path %s traverses edge %s backwards; list nodes in "
                            "the edge's from->to order" % (pid, edge_id))
            else:
                rep.problem("path %s edge %s does not connect %s->%s"
                            % (pid, edge_id, a, b))
        direct = len(edge_ids) == 1
        if "direct" in path and bool(path.get("direct")) != direct:
            rep.problem("path %s direct flag disagrees with hop count" % pid)
        if is_empty(path.get("relation")):
            rep.warn("path %s lacks relation (cites/cited_by/mixed)" % pid)
    if directed_edges == 0:
        rep.problem("no path traverses a directed edge")

    groups = work.get("identity_groups") or []
    if not isinstance(groups, list) or not groups:
        rep.problem("identity_groups is empty")
        groups = []
    grouped = set()
    for index, group in enumerate(groups, 1):
        if not isinstance(group, dict):
            rep.problem("identity_group %d is not an object" % index)
            continue
        canonical = norm_id(group.get("canonical_id") or group.get("keep"))
        members = [norm_id(x) for x in (group.get("members") or group.get("merge") or [])]
        if canonical:
            members = [canonical] + [m for m in members if m != canonical]
        if len(members) < 1:
            rep.problem("identity_group %d has no members" % index)
        if is_empty(group.get("basis")):
            rep.problem("identity_group %s lacks basis" % (canonical or index))
        if not isinstance(group.get("merged"), bool):
            rep.problem("identity_group %s must state merged=true/false" % (canonical or index))
        for member in members:
            if member in grouped:
                rep.problem("record %s appears in multiple identity groups" % member)
            grouped.add(member)
            if input_by_id and member not in input_by_id \
                    and member not in {norm_id(s.get("id")) for s in seeds if isinstance(s, dict)}:
                rep.problem("identity_group member %s is not in input/seeds" % member)
        if group.get("merged") and len(members) < 2:
            rep.problem("identity_group %s is merged but has one member" % canonical)
    if input_records or seed:
        registry = list(input_records)
        if seed and seed.get("title"):
            registry.append(dict(seed, id="SEED"))
        titles = {}
        for rec in registry:
            title = norm_title(rec.get("title"))
            if title:
                titles.setdefault(title, []).append(rec)
        for title, recs in titles.items():
            if len(recs) < 2:
                continue
            ids = {norm_id(r.get("id")) for r in recs}
            group_members = []
            for group in groups:
                members = {norm_id(group.get("canonical_id") or group.get("keep"))}
                members.update(norm_id(x) for x in
                               (group.get("members") or group.get("merge") or []))
                if ids & members:
                    group_members.append(group)
            authors = {norm(r.get("authors")) for r in recs if r.get("authors")}
            if len(authors) == 1:
                if not group_members or not any(g.get("merged") for g in group_members):
                    rep.problem("same-title/same-author versions %s are not merged"
                                % ", ".join(sorted(ids)))
                for group in group_members:
                    if group.get("merged") and is_empty(group.get("version_basis")):
                        rep.problem("merged version group lacks version_basis")
            else:
                wrongly_merged = [g for g in group_members
                                  if g.get("merged") and len({
                                      norm_id(g.get("canonical_id") or g.get("keep")),
                                      *[norm_id(x) for x in (g.get("members") or [])],
                                      *ids}) > 1]
                if wrongly_merged:
                    rep.problem("same-title/different-author records %s were wrongly merged"
                                % ", ".join(sorted(ids)))
                if not any(g.get("keep_separate_reason") for g in group_members):
                    rep.warn("same-title/different-author records %s should document "
                             "keep_separate_reason" % ", ".join(sorted(ids)))

    related = work.get("related_works") or []
    if not isinstance(related, list) or not related:
        rep.problem("related_works is empty")
    related_ids = set()
    for index, item in enumerate(related, 1):
        if not isinstance(item, dict):
            rep.problem("related_work %d is not an object" % index)
            continue
        rid = norm_id(item.get("id"))
        if not rid:
            rep.problem("related_work %d lacks id" % index)
            continue
        related_ids.add(rid)
        relation = str(item.get("relation") or item.get("signal") or "").lower()
        if is_empty(relation):
            rep.problem("related_work %s lacks relation" % rid)
        elif relation in ("cites", "cited_by", "citation", "direct_citation"):
            rep.problem("related_work %s is a citation edge, not a co-citation/coupling signal" % rid)
        if is_empty(item.get("basis")):
            rep.problem("related_work %s lacks basis" % rid)
        if is_empty(item.get("evidence")):
            rep.problem("related_work %s lacks evidence" % rid)
        if rid in endpoints:
            rep.warn("related_work %s also appears in the citation edge list; "
                     "keep the two relations distinct" % rid)

    unknown = work.get("direction_unknown") or []
    unknown_ids = set()
    if isinstance(unknown, list):
        for index, item in enumerate(unknown, 1):
            if not isinstance(item, dict):
                rep.problem("direction_unknown %d is not an object" % index)
                continue
            ids = [norm_id(x) for x in (item.get("ids") or [item.get("id")]) if x]
            unknown_ids.update(ids)
            if is_empty(item.get("reason")):
                rep.problem("direction_unknown %d lacks reason" % index)
            if is_empty(item.get("source")) and is_empty(item.get("evidence")):
                rep.problem("direction_unknown %d lacks source/evidence" % index)
    elif isinstance(unknown, dict):
        if unknown.get("checked") is not True:
            rep.problem("direction_unknown.checked must be true")
        if is_empty(unknown.get("policy")):
            rep.problem("direction_unknown.policy is missing")
        for index, item in enumerate(unknown.get("items") or [], 1):
            if not isinstance(item, dict):
                rep.problem("direction_unknown.items[%d] is not an object" % index)
                continue
            ids = [norm_id(x) for x in (item.get("ids") or [item.get("id")]) if x]
            unknown_ids.update(ids)
            if is_empty(item.get("reason")):
                rep.problem("direction_unknown item %d lacks reason" % index)
            if is_empty(item.get("source")) and is_empty(item.get("evidence")):
                rep.problem("direction_unknown item %d lacks source/evidence" % index)
        for rid in unknown.get("ids") or []:
            unknown_ids.add(norm_id(rid))
    else:
        rep.problem("direction_unknown must be a list or object")
    unknown_note = []
    for rel, text in inputs.files.items():
        low = norm(text)
        if "direction is unknown" not in low and "does not state whether" not in low:
            continue
        for line in text.splitlines():
            line_low = norm(line)
            if ("direction is unknown" in line_low or "does not state whether" in line_low
                    or "entry" in line_low or "entries" in line_low
                    or "list contains" in line_low):
                unknown_note.append(line)
    if unknown_note:
        mentioned = {norm_id(x) for x in re.findall(r"\b([A-Za-z]\d+)\b", " ".join(unknown_note))}
        missing_unknown = mentioned - unknown_ids
        if not unknown_ids:
            rep.problem("input states that direction is unknown but direction_unknown is empty")
        elif missing_unknown:
            rep.problem("direction_unknown does not list %s"
                        % ", ".join(sorted(missing_unknown)))
    for rid in unknown_ids & endpoints:
        rep.problem("record %s is in direction_unknown but also has a directed edge" % rid)

    scope = work.get("tracking_scope") if isinstance(work.get("tracking_scope"), dict) else {}
    if not scope:
        rep.problem("tracking_scope is missing (levels/stop_condition/budget)")
    else:
        if as_int(scope.get("levels")) is None or as_int(scope.get("levels")) < 1:
            rep.problem("tracking_scope.levels must be a positive integer")
        if is_empty(scope.get("stop_condition")):
            rep.problem("tracking_scope.stop_condition is missing")
    directions = {str(e.get("direction")).lower() for e in edges if isinstance(e, dict)}
    if input_records:
        if any("cites the seed" in norm(r.get("note", "")) for r in input_records):
            if "forward" not in directions:
                rep.problem("input contains citing works but no forward edge")
        if any("seed paper cites this work" in norm(r.get("note", "")) for r in input_records):
            if "backward" not in directions:
                rep.problem("input contains cited references but no backward edge")

    # every supplied citation record must be accounted for
    if input_records:
        accounted = set(endpoints) | related_ids | unknown_ids
        for item in work.get("candidates") or []:
            if isinstance(item, dict):
                accounted.add(norm_id(item.get("id") or item.get("candidate_id")))
        for group in groups:
            accounted.add(norm_id(group.get("canonical_id") or group.get("keep")))
            accounted.update(norm_id(x) for x in (group.get("members") or group.get("merge") or []))
        missing = [rid for rid in input_by_id if rid not in accounted and input_by_id[rid].get("id")]
        if missing:
            rep.problem("input citation records not accounted for: %s"
                        % ", ".join(sorted(missing)))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def load_work(work_dir, config):
    name = Path(str(config.get("work_file") or "work/answer.json")).name
    path = Path(work_dir) / name
    if path.is_file():
        return path, load_json(path, {})
    for candidate in ("research-plan.json", "search-plan.json", "answer.json"):
        alt = Path(work_dir) / candidate
        if alt.is_file():
            return alt, load_json(alt, {})
    return path, {}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--skill-dir", default=None)
    args = parser.parse_args(argv)
    skill_dir = Path(args.skill_dir) if args.skill_dir else Path(__file__).resolve().parent.parent
    config = load_json(skill_dir / "task.json", {})
    mode = str(config.get("mode") or "")
    inputs = Inputs(args.input)
    work_path, work = load_work(args.work, config)
    rep = Report()
    if not inputs.files:
        rep.problem("no readable material under input: %s" % args.input)
    if not isinstance(work, dict) or not work:
        rep.problem("work file is missing or empty: %s" % work_path)
    else:
        check_case(work, inputs, rep)
        check_required(work, config, rep)
        check_evidence(work, inputs, args.work, args.output, rep)
        if mode == "trend-analysis":
            audit_trend(work, inputs, rep)
        elif mode == "systematic-review":
            audit_review(work, inputs, rep)
        elif mode == "screening-ranking":
            audit_screening(work, inputs, rep)
        elif mode == "citation-tracking":
            audit_citation(work, inputs, rep)
        else:
            rep.warn("no mode-specific audit for mode %r" % mode)

    payload = {
        "status": "pass" if not rep.problems else "fail",
        "mode": mode,
        "work_file": str(work_path),
        "problems": rep.problems,
        "warnings": rep.warnings,
        "recomputed": rep.recomputed,
        "rule": "fix every problem and rerun this audit before finalize",
    }
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "quality-audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if rep.problems:
        print("\nAUDIT FAILED: %d problem(s). Fix the work file; do not run finalize yet."
              % len(rep.problems), file=sys.stderr)
        return 1
    print("\nAUDIT PASS: %d warning(s). Proceed to finalize." % len(rep.warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
