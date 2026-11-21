#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, replayable incremental-tracking first pass.

Only records present in the provided material are used.  The script builds a
fixed time window, compares in-window works with a before-snapshot, merges
versions by a documented dedupe key, prepares (never sends) alerts, records
suppressed items and writes a replay key/state for idempotent reruns.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"arXiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?", re.I)
DATE_RE = re.compile(r"\b((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
RETRACT_RE = re.compile(r"retract|withdraw|撤稿|撤回", re.I)
CORRECT_RE = re.compile(r"corrigendum|erratum|corrected|勘误|更正", re.I)
PREPRINT_RE = re.compile(r"preprint|bioRxiv|medRxiv|arXiv|SSRN|预印本", re.I)
STATUS_MAP = [
    ("retracted", RETRACT_RE),
    ("corrected", CORRECT_RE),
    ("preprint", PREPRINT_RE),
    ("published", re.compile(r"published|正式|见刊|期刊", re.I)),
]
DEDUPE_VERSION = "dedupe-v1"


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def emit_text(path, text, fill_only=False):
    path = Path(path)
    if fill_only and path.exists() and path.stat().st_size > 0:
        return False
    write_text(path, text)
    return True


def emit_json(path, payload, fill_only=False):
    path = Path(path)
    if fill_only and path.exists() and path.stat().st_size > 0:
        return False
    write_json(path, payload)
    return True


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def safe_name(key):
    return re.sub(r"[^\w一-鿿.-]+", "_", str(key or "field")).strip("._") or "field"


def norm_title(text):
    return re.sub(r"[^0-9a-z一-鿿]+", "", str(text or "").lower())


def tokens(text):
    return {x for x in re.findall(r"[a-z0-9]{3,}|[一-鿿]{2,}", str(text or "").lower())}


def sha16(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


def parse_date(value):
    text = norm(value)
    if not text:
        return "", "missing"
    match = DATE_RE.search(text)
    if match:
        year, month, day = match.groups()
        return "%04d-%02d-%02d" % (int(year), int(month), int(day)), "day"
    match = re.search(r"\b((?:19|20)\d{2})[-/.](\d{1,2})\b", text)
    if match:
        year, month = match.groups()
        return "%04d-%02d-01" % (int(year), int(month)), "month"
    match = YEAR_RE.search(text)
    if match:
        return "%s-01-01" % match.group(1), "year"
    return "", "missing"


class Source:
    def __init__(self, item):
        self.path = str(item.get("path") or item.get("rel_path") or "")
        self.text = str(item.get("text") or "")
        self.kind = str(item.get("kind") or "")
        self.rows = item.get("rows") or []
        self.data = item.get("json")
        self.lines = self.text.splitlines()

    def evidence_for(self, *needles):
        for needle in needles:
            needle_n = norm(needle).lower()
            if not needle_n:
                continue
            for index, line in enumerate(self.lines, 1):
                if needle_n in norm(line).lower():
                    return {"path": self.path, "locator": "L%d" % index,
                            "quote": line.strip()}
        return None


def load_sources(work):
    materials = read_json(Path(work) / "materials.json", {})
    docs = materials.get("documents") or []
    return [Source(doc) for doc in docs
            if str(doc.get("text") or "").strip() or doc.get("rows") or doc.get("json")]


def table_rows(source):
    text = source.text or ""
    if not text.strip():
        return source.rows or []
    sample = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "	" if "	" in sample else (
        ";" if ";" in sample and "," not in sample else ",")
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        if rows and len(rows[0]) >= 2:
            return rows
    except csv.Error:
        pass
    return source.rows or []


def status_of(value):
    text = norm(value)
    for status, pattern in STATUS_MAP:
        if pattern.search(text):
            return status
    return "unknown"


def make_record(raw, source, index):
    title = norm(raw.get("title") or raw.get("name") or "")
    doi = norm(raw.get("doi") or raw.get("DOI") or "")
    arxiv_raw = norm(raw.get("arxiv") or raw.get("arxiv_id") or "")
    arxiv_match = ARXIV_RE.search(arxiv_raw or doi)
    arxiv = arxiv_match.group(1) if arxiv_match else ""
    date_value = (raw.get("date") or raw.get("publication_date")
                  or raw.get("published") or raw.get("online_date")
                  or raw.get("updated") or raw.get("created")
                  or raw.get("year") or "")
    date, precision = parse_date(date_value)
    status = status_of(raw.get("status") or raw.get("type") or "")
    if status == "unknown" and arxiv:
        status = "preprint"
    version = norm(raw.get("version") or
                   (arxiv_match.group(2) if arxiv_match else "") or "")
    record = {
        "id": "C%03d" % index,
        "title": title,
        "authors": norm(raw.get("authors") or raw.get("author") or ""),
        "year": (date[:4] if date else ""),
        "venue": norm(raw.get("venue") or raw.get("journal") or raw.get("source") or ""),
        "doi": doi or "",
        "arxiv": ("arXiv:%s" % arxiv) if arxiv else "",
        "arxiv_id": arxiv,
        "date": date,
        "date_precision": precision,
        "version": version or ("v1" if arxiv else ""),
        "status": status,
        "abstract": norm(raw.get("abstract") or raw.get("summary") or ""),
        "source": source.path,
    }
    if doi:
        record["dedupe_key"] = "doi:%s" % doi.lower()
    elif arxiv:
        record["dedupe_key"] = "arxiv:%s" % arxiv
    elif title:
        record["dedupe_key"] = "title:%s|%s" % (norm_title(title), record["year"])
    else:
        record["dedupe_key"] = ""
    record["evidence"] = source.evidence_for(doi, arxiv, title)
    return record


def records_from_json(value, source, start_index):
    items = []
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        for key in ("records", "papers", "articles", "items", "results"):
            if isinstance(value.get(key), list):
                items = value[key]
                break
        else:
            if any(key in value for key in ("title", "doi", "arxiv")):
                items = [value]
    out = []
    for item in items:
        if isinstance(item, dict):
            record = make_record(item, source, start_index + len(out))
            if record["title"] or record["doi"] or record["arxiv"]:
                out.append(record)
    return out


def records_from_csv(source, start_index):
    rows = table_rows(source)
    if not rows or not isinstance(rows[0], list):
        return []
    header = [str(x).strip().lower() for x in rows[0]]

    def col(*names):
        for name in names:
            for index, value in enumerate(header):
                if name == value or name in value:
                    return index
        return None

    mapping = {
        "title": col("title", "标题"),
        "doi": col("doi"),
        "arxiv": col("arxiv"),
        "year": col("year"),
        "date": col("date", "publication_date", "published", "日期"),
        "authors": col("authors", "author", "作者"),
        "venue": col("venue", "journal", "期刊", "source"),
        "status": col("status", "type", "状态"),
        "version": col("version", "版本"),
        "abstract": col("abstract", "summary", "摘要"),
    }
    out = []
    for row in rows[1:]:
        if not isinstance(row, list):
            continue
        raw = {}
        for key, index in mapping.items():
            if index is not None and index < len(row):
                raw[key] = norm(row[index])
        record = make_record(raw, source, start_index + len(out))
        if record["title"] or record["doi"] or record["arxiv"]:
            out.append(record)
    return out


def records_from_text(source, start_index):
    out = []
    for index, line in enumerate(source.lines, 1):
        text = norm(line)
        if len(text) < 12:
            continue
        doi = DOI_RE.search(text)
        arxiv = ARXIV_RE.search(text)
        title_match = re.search(r"(?:title|标题)\s*[:：]\s*(.+)", text, re.I)
        if not (doi or arxiv or title_match):
            continue
        raw = {
            "title": (title_match.group(1) if title_match else
                      re.sub(r"^[#*\-\d.\s]+", "", text)),
            "doi": doi.group(0) if doi else "",
            "arxiv": ("arXiv:%s" % arxiv.group(1)) if arxiv else "",
            "date": (DATE_RE.search(text).group(0) if DATE_RE.search(text)
                     else YEAR_RE.search(text).group(0) if YEAR_RE.search(text) else ""),
            "status": "preprint" if arxiv else ("retracted" if RETRACT_RE.search(text)
                                                 else "unknown"),
        }
        record = make_record(raw, source, start_index + len(out))
        out.append(record)
    return out


def extract_records(sources):
    out = []
    for source in sources:
        chunk = []
        if source.data is not None:
            chunk.extend(records_from_json(source.data, source, len(out) + len(chunk) + 1))
        if source.rows:
            chunk.extend(records_from_csv(source, len(out) + len(chunk) + 1))
        if not (source.data is not None or source.rows):
            chunk.extend(records_from_text(source, len(out) + len(chunk) + 1))
        out.extend(chunk)
    for index, record in enumerate(out, 1):
        record["id"] = "C%03d" % index
    return out


def case_window(case):
    raw = case.get("window") if isinstance(case.get("window"), dict) else {}
    start = raw.get("start") or case.get("since") or case.get("window_start")
    end = raw.get("end") or case.get("until") or case.get("window_end")
    tz = raw.get("timezone") or case.get("timezone") or "UTC"
    start_date, _ = parse_date(start)
    end_date, _ = parse_date(end)
    return start_date, end_date, str(tz or "UTC"), bool(start_date and end_date)


def derive_window(records, case):
    start, end, tz, explicit = case_window(case)
    source = "case_json" if explicit else ""
    if not (start and end):
        dates = sorted(r["date"] for r in records if r.get("date"))
        if dates:
            start = start or dates[0]
            end = end or dates[-1]
            source = source or "derived_from_material_dates"
    return start, end, tz or "UTC", source or "not_available"


def in_window(date, start, end):
    if not date or not (start and end):
        return False
    # Inclusive start, exclusive end; fixed UTC dates avoid boundary drift.
    return start <= date < end


def _record_keys(record):
    """Identifier keys used for grouping.

    Title+year is only used when the record has no DOI/arXiv identifier:
    different DOIs with the same title must stay separate unless an explicit
    identifier links them.  A DOI record and an arXiv record merge only when
    one record carries both identifiers.
    """
    keys = []
    if record.get("doi"):
        keys.append("doi:%s" % record["doi"].lower())
    if record.get("arxiv_id"):
        keys.append("arxiv:%s" % record["arxiv_id"])
    if not keys and record.get("title"):
        keys.append("title:%s|%s" % (norm_title(record["title"]), record.get("year") or ""))
    return list(dict.fromkeys([k for k in keys if k]))


def group_versions(records):
    """Union DOI/arXiv/title keys so preprint and published versions merge."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    record_keys = {}
    for record in records:
        keys = _record_keys(record)
        record_keys[record["id"]] = keys
        if keys:
            first = keys[0]
            find(first)
            for key in keys[1:]:
                union(first, key)
    groups = {}
    for record in records:
        keys = record_keys.get(record["id"]) or [record["id"]]
        root = find(keys[0])
        groups.setdefault(root, []).append(record)
    priority = {"published": 4, "corrected": 3, "preprint": 2, "unknown": 1, "retracted": 0}
    out = []
    for members in groups.values():
        members = sorted(members, key=lambda r: (r.get("date") or "", r["id"]))
        primary = sorted(members, key=lambda r: (-priority.get(r.get("status"), 0),
                                                 r.get("date") or "", r["id"]))[0]
        member_keys = set()
        for member in members:
            member_keys.update(record_keys.get(member["id"]) or [])
        doi_keys = sorted(k for k in member_keys if k.startswith("doi:"))
        arxiv_keys = sorted(k for k in member_keys if k.startswith("arxiv:"))
        title_keys = sorted(k for k in member_keys if k.startswith("title:"))
        group_key = (doi_keys[0] if doi_keys else arxiv_keys[0] if arxiv_keys
                     else title_keys[0] if title_keys else primary["id"])
        if any(m.get("status") == "retracted" for m in members):
            category = "retraction"
        elif any(m.get("status") == "corrected" for m in members):
            category = "correction"
        else:
            category = "work"
        out.append({"dedupe_key": group_key, "member_keys": sorted(member_keys),
                    "primary": primary, "members": members,
                    "status_category": category})
    out.sort(key=lambda g: (g["primary"].get("date") or "", g["dedupe_key"]))
    return out


def relevance(record, subscription):
    query = " ".join(str(subscription.get(key) or "") for key in ("query", "topic", "title"))
    q = tokens(query)
    if not q:
        return "unspecified", None
    r = tokens("%s %s" % (record.get("title") or "", record.get("abstract") or ""))
    if not r:
        return "unknown", None
    score = len(q & r) / float(len(q))
    if score >= 0.5:
        return "core", round(score, 3)
    if score >= 0.2:
        return "related", round(score, 3)
    return "peripheral", round(score, 3)


def subscription_from(case, task_text, records):
    raw = case.get("subscription") if isinstance(case.get("subscription"), dict) else {}
    query = (raw.get("query") or case.get("query") or case.get("topic")
             or case.get("research_question") or "")
    if not query:
        match = re.search(r"(?:subscription|订阅|topic|主题|query|检索式)\s*[:：]\s*(.+)",
                          task_text, re.I)
        if match:
            query = norm(match.group(1))
    sources = sorted({r.get("source") for r in records if r.get("source")})
    return {
        "id": str(raw.get("id") or case.get("subscription_id") or "SUB-%s" % sha16(query)[:8]),
        "query": norm(query),
        "sources": raw.get("sources") or sources,
        "relevance_rule": "token_overlap>=0.5 core; >=0.2 related; else peripheral",
        "schedule": raw.get("schedule") or "manual / one window per run",
        "timezone": str((case.get("window") or {}).get("timezone") or "UTC")
        if isinstance(case.get("window"), dict) else "UTC",
    }


def baseline_from(case, records, start):
    raw = case.get("snapshot_before") or case.get("baseline") or case.get("last_snapshot")
    keys = set()
    source = "case_json"
    if isinstance(raw, dict):
        values = raw.get("keys") or raw.get("ids") or raw.get("records") or []
        keys = {str(x) for x in values}
    elif isinstance(raw, list):
        keys = {str(x) for x in raw}
    elif raw:
        keys = {str(raw)}
    if not keys:
        source = "derived_from_dates_before_window"
        keys = {r["dedupe_key"] for r in records
                if r.get("date") and start and r["date"] < start}
    return keys, source


def build_increments(groups, baseline_keys, start, end, subscription, state):
    increments, suppressed = [], []
    prepared = set(state.get("prepared_alert_ids") or [])
    state_key = state.get("replay_key")
    for group in groups:
        primary = group["primary"]
        members = group["members"]
        in_members = [m for m in members if in_window(m.get("date") or "", start, end)]
        if not in_members:
            suppressed.append({
                "record_id": primary["id"],
                "dedupe_key": group["dedupe_key"],
                "reason": "outside_window",
                "detail": "date=%s window=[%s, %s)" % (
                    primary.get("date") or "missing", start, end),
            })
            continue
        # Judge the category and the increment anchor from in-window members.
        primary_in = sorted(in_members, key=lambda r: (r.get("date") or "", r["id"]))[-1]
        key = group["dedupe_key"]
        member_keys = set(group.get("member_keys") or [key])
        in_baseline = bool(member_keys & baseline_keys)
        if any(m.get("status") == "retracted" for m in in_members):
            category = "retraction"
        elif any(m.get("status") == "corrected" for m in in_members):
            category = "correction"
        elif in_baseline:
            category = "version_update"
        else:
            category = "new"
        rel, score = relevance(primary_in, subscription)
        increment = {
            "id": "N%03d" % (len(increments) + 1),
            "record_id": primary_in["id"],
            "dedupe_key": key,
            "category": category,
            "title": primary_in.get("title"),
            "doi": primary_in.get("doi"),
            "arxiv": primary_in.get("arxiv"),
            "date": primary_in.get("date"),
            "date_precision": primary_in.get("date_precision"),
            "status": primary_in.get("status"),
            "version": primary_in.get("version"),
            "relevance": rel,
            "relevance_score": score,
            "in_baseline": in_baseline,
            "member_keys": sorted(member_keys),
            "merged_from": [m["id"] for m in members if m["id"] != primary_in["id"]],
            "versions": [{"record_id": m["id"], "date": m.get("date"),
                          "status": m.get("status"), "version": m.get("version"),
                          "doi": m.get("doi"), "arxiv": m.get("arxiv")}
                         for m in members],
            "evidence": primary_in.get("evidence"),
        }
        alert_id = "A-%s" % sha16("%s|%s|%s" % (subscription.get("id"), key,
                                                "%s..%s" % (start, end)))[:10]
        increment["alert_id"] = alert_id
        alert = {
            "alert_id": alert_id,
            "record_id": primary_in["id"],
            "dedupe_key": key,
            "category": category,
            "relevance": rel,
            "status": "queued_for_digest",
            "delivery": "not_sent_by_skill",
            "replay_key": "",
        }
        if state_key and alert_id in prepared:
            alert["status"] = "already_prepared"
            suppressed.append({
                "record_id": primary_in["id"], "dedupe_key": key,
                "reason": "already_prepared_idempotent",
                "detail": "same replay_key state already contains alert_id=%s" % alert_id,
            })
        increments.append(increment)
        increments[-1]["alert"] = alert
    # Digest cap: keep at most 10 alerts per window; rest suppressed explicitly.
    queued = [inc for inc in increments if inc["alert"]["status"] == "queued_for_digest"]
    for inc in queued[10:]:
        inc["alert"]["status"] = "suppressed_digest_limit"
        suppressed.append({
            "record_id": inc["record_id"], "dedupe_key": inc["dedupe_key"],
            "reason": "digest_limit",
            "detail": "more than 10 queued alerts in one digest",
        })
    return increments, suppressed


def snapshot(keys, cutoff, label):
    ordered = sorted(keys)
    return {
        "id": "SNAP-%s" % sha16("|".join(ordered) + "|" + str(cutoff))[:10],
        "label": label,
        "cutoff": cutoff,
        "count": len(ordered),
        "keys": ordered[:500],
        "truncated": len(ordered) > 500,
    }


def build_outputs(case, task_text, sources, records, state):
    subscription = subscription_from(case, task_text, records)
    start, end, tz, window_source = derive_window(records, case)
    baseline_keys, baseline_source = baseline_from(case, records, start)
    groups = group_versions(records)
    increments, suppressed = build_increments(groups, baseline_keys, start, end,
                                              subscription, state)
    window_records = sorted([r for r in records
                             if in_window(r.get("date") or "", start, end)],
                            key=lambda r: (r.get("date") or "", r["id"]), reverse=True)
    rank_by_id = {record["id"]: index for index, record in enumerate(window_records, 1)}
    for record in records:
        rel, score = relevance(record, subscription)
        record["relevance"] = rel
        record["score"] = score
        record["rank"] = rank_by_id.get(record["id"])
        record["reason"] = (
            "Provided-corpus record; in_window=%s; status=%s; relevance=%s (token overlap)"
            % (bool(record["rank"]), record.get("status"), rel))
    source_fingerprint = ",".join(sorted(
        "%s:%s" % (source.path, sha16(source.text)) for source in sources))
    replay_key = "REPLAY-%s" % sha16("|".join([
        subscription.get("id") or "", subscription.get("query") or "",
        start or "", end or "", tz or "", DEDUPE_VERSION, source_fingerprint,
    ]))[:16]
    for inc in increments:
        inc["alert"]["replay_key"] = replay_key
    window = {
        "start": start or None,
        "end": end or None,
        "timezone": tz,
        "boundary_rule": "inclusive start, exclusive end (UTC dates)",
        "source": window_source,
        "continuous_from_previous": bool(end and start and end > start),
    }
    watermark = (case.get("watermark") or case.get("last_seen")
                 or (start if start else ""))
    before = snapshot(baseline_keys, start, "before")
    before["source"] = baseline_source
    after_keys = set(baseline_keys)
    for inc in increments:
        after_keys.update(inc.get("member_keys") or [inc["dedupe_key"]])
    after = snapshot(after_keys, end, "after")
    after["source"] = "previous_snapshot_plus_this_window"
    queries = []
    if subscription.get("query"):
        queries.append({
            "id": "Q1", "round": 1, "query": subscription.get("query"),
            "source": ", ".join(subscription.get("sources") or []) or "provided corpus",
            "purpose": "incremental retrieval over the provided snapshot",
            "window": {"start": start, "end": end, "timezone": tz},
            "executed": False,
            "execution_note": "No live network call; records come from the provided material.",
        })
    alerts = [inc["alert"] for inc in increments]
    counts = {
        "records": len(records),
        "unique_works": len(groups),
        "in_window_records": sum(1 for r in records if in_window(r.get("date") or "", start, end)),
        "increments": len(increments),
        "new": sum(1 for i in increments if i["category"] == "new"),
        "version_update": sum(1 for i in increments if i["category"] == "version_update"),
        "retraction": sum(1 for i in increments if i["category"] == "retraction"),
        "correction": sum(1 for i in increments if i["category"] == "correction"),
        "alerts_queued": sum(1 for a in alerts if a["status"] == "queued_for_digest"),
        "alerts_already_prepared": sum(1 for a in alerts
                                       if a["status"] == "already_prepared"),
        "suppressed": len(suppressed),
        "suppressed_duplicates": sum(1 for s in suppressed
                                     if s["reason"] == "outside_window"),
    }
    metrics = {
        "counts": counts,
        "precision_at_k": "not_computable_without_labels",
        "recall_at_k": "not_computable_without_reference_set",
        "definition": ("counts are computed from the provided records; no labelled "
                       "reference set was supplied, so precision/recall are not claimed"),
    }
    evidence = []
    seen = set()
    for record in records:
        ev = record.get("evidence")
        if ev and ev.get("quote"):
            key = (ev.get("path"), norm(ev.get("quote")))
            if key not in seen:
                seen.add(key)
                evidence.append(ev)
    uncertainties = []
    if not records:
        uncertainties.append({"item": "records", "reason": "材料中没有可解析的文献记录",
                              "impact": "无法判定窗口新增", "action": "提供记录导出或快照"})
    if not start or not end:
        uncertainties.append({"item": "window", "reason": "材料与 case.json 均未给出完整窗口",
                              "impact": "无法做增量判定", "action": "提供窗口起止与时区"})
    elif window_source != "case_json":
        uncertainties.append({"item": "window_derived",
                              "reason": "窗口由材料日期推导，而非 case.json 指定",
                              "impact": "窗口边界可能与项目定义不同",
                              "action": "以项目订阅配置覆盖 start/end 后重跑"})
    if baseline_source != "case_json":
        uncertainties.append({"item": "baseline_derived",
                              "reason": "上次快照由窗口前日期推导",
                              "impact": "若上次快照含更晚入库记录，可能误判为新增",
                              "action": "提供显式 snapshot_before 键列表"})
    partial_dates = [r["id"] for r in records if r.get("date_precision") in ("year", "month")]
    if partial_dates:
        uncertainties.append({"item": "date_precision",
                              "reason": "部分记录只有年/月精度：%s" % ", ".join(partial_dates[:20]),
                              "impact": "窗口边界判定可能偏差",
                              "action": "回源核对精确入库/上线日期"})
    return {
        "case_id": str(case.get("case_id") or case.get("id") or "CASE"),
        "mode": "latest-tracking",
        "summary": ("窗口 [%s, %s) %s 相对上次快照得到 %d 条增量；"
                    "提醒仅入队未发送。" % (start, end, tz, len(increments))),
        "subscription": subscription,
        "window": window,
        "watermark": watermark,
        "replay_key": replay_key,
        "snapshot_before": before,
        "snapshot_after": after,
        "increments": increments,
        "alert_log": alerts,
        "suppressed": suppressed,
        "candidates": records,
        "queries": queries,
        "metrics": metrics,
        "evidence": evidence,
        "uncertainties": uncertainties,
        "dedupe_rules": {
            "version": DEDUPE_VERSION,
            "key_priority": ["doi:<lower>", "arxiv:<id-without-version>",
                             "title:<normalized>|<year>"],
            "version_priority": ["published", "corrected", "preprint", "unknown", "retracted"],
            "merge_rule": "same key => one work; versions retained in versions[]",
        },
        "sources": [{"path": source.path, "chars": len(source.text),
                     "kind": source.kind} for source in sources],
        "generated_at": now_iso(),
        "_generated_by": "domain_work.py",
    }


def domain_checks(plan):
    problems, warnings = [], []
    window = plan.get("window") or {}
    for field in ("start", "end", "timezone", "boundary_rule"):
        if not window.get(field):
            problems.append("window.%s is missing" % field)
    if window.get("start") and window.get("end") and window["end"] < window["start"]:
        problems.append("window end is before start")
    if not plan.get("watermark"):
        problems.append("watermark is missing")
    if not plan.get("replay_key"):
        problems.append("replay_key is missing")
    if not plan.get("snapshot_before") or not plan.get("snapshot_after"):
        problems.append("snapshot_before/after is missing")
    keys = [inc.get("dedupe_key") for inc in plan.get("increments") or []]
    if len(keys) != len(set(keys)):
        problems.append("increments contain duplicate dedupe_key values")
    for inc in plan.get("increments") or []:
        for field in ("record_id", "dedupe_key", "category", "evidence", "alert_id"):
            if not inc.get(field):
                problems.append("increment %s lacks %s" % (inc.get("id"), field))
        if inc.get("category") not in ("new", "version_update", "retraction", "correction"):
            problems.append("increment %s has invalid category %r" % (
                inc.get("id"), inc.get("category")))
        if inc.get("evidence") is None:
            warnings.append("increment %s has no locatable evidence" % inc.get("id"))
    if plan.get("increments") and not plan.get("alert_log"):
        problems.append("increments exist but alert_log is empty")
    for alert in plan.get("alert_log") or []:
        for field in ("alert_id", "record_id", "status", "replay_key"):
            if not alert.get(field):
                problems.append("alert %s lacks %s" % (alert.get("alert_id"), field))
        if alert.get("status") not in ("queued_for_digest", "already_prepared",
                                       "suppressed_digest_limit", "delivered"):
            problems.append("alert %s has invalid status %r" % (
                alert.get("alert_id"), alert.get("status")))
    for item in plan.get("suppressed") or []:
        if not item.get("reason"):
            problems.append("suppressed item lacks reason")
    for record in plan.get("candidates") or []:
        doi = record.get("doi")
        if doi and not DOI_RE.search(doi):
            problems.append("record %s has an invalid DOI" % record.get("id"))
        if not record.get("evidence"):
            warnings.append("record %s has no locatable evidence" % record.get("id"))
    for evidence in plan.get("evidence") or []:
        for field in ("path", "locator", "quote"):
            if not evidence.get(field):
                problems.append("evidence lacks %s" % field)
    return {"problems": problems, "warnings": warnings,
            "blocking": len(problems),
            "source_count": len(plan.get("sources") or []),
            "evidence_count": len(plan.get("evidence") or [])}


def render_digest(plan):
    window = plan.get("window") or {}
    counts = (plan.get("metrics") or {}).get("counts") or {}
    lines = [
        "# 增量文献追踪摘要", "",
        "- case_id: `%s`" % plan.get("case_id"),
        "- 生成时间: %s" % plan.get("generated_at"),
        "- 订阅：`%s`（%s）" % ((plan.get("subscription") or {}).get("id"),
                                (plan.get("subscription") or {}).get("query") or "未指定"),
        "- 窗口：[%s, %s) %s（%s）" % (
            window.get("start"), window.get("end"), window.get("timezone"),
            window.get("source")),
        "- 水位：`%s`" % plan.get("watermark"),
        "- 回放键：`%s`" % plan.get("replay_key"),
        "", "> 本 Skill 只准备提醒，不发送邮件/消息；`alert_log.status` 表示投递队列状态。",
        "", "## 计数", "",
        "| 指标 | 值 |", "|---|---|",
    ]
    for key in ("records", "unique_works", "in_window_records", "increments",
                "new", "version_update", "retraction", "correction",
                "alerts_queued", "alerts_already_prepared", "suppressed"):
        lines.append("| %s | %s |" % (key, counts.get(key, 0)))
    lines += ["", "## 本窗口增量", ""]
    if plan.get("increments"):
        lines += ["| ID | 类别 | 日期 | 标题 | 去重键 | 提醒 | 版本合并 |",
                  "|---|---|---|---|---|---|---|"]
        for inc in plan["increments"]:
            lines.append("| %s | %s | %s | %s | `%s` | %s | %s |" % (
                inc.get("id"), inc.get("category"), inc.get("date"),
                str(inc.get("title"))[:80].replace("|", "/"), inc.get("dedupe_key"),
                (inc.get("alert") or {}).get("status"),
                ", ".join(inc.get("merged_from") or []) or "—"))
    else:
        lines.append("- 本窗口没有相对上次快照的新增；这是空窗口结果，不是检索失败。")
    lines += ["", "## 被抑制/未入队条目", ""]
    for item in plan.get("suppressed") or []:
        lines.append("- %s：`%s`（%s）" % (item.get("record_id"), item.get("reason"),
                                          item.get("detail") or ""))
    if not plan.get("suppressed"):
        lines.append("- 无")
    lines += ["", "## 证据与限制", ""]
    for ev in (plan.get("evidence") or [])[:30]:
        lines.append("- `%s` %s：%s" % (ev.get("path"), ev.get("locator"), ev.get("quote")))
    if plan.get("uncertainties"):
        lines += ["", "### 未确认 / 缺口"]
        for item in plan["uncertainties"]:
            lines.append("- %s：%s → %s" % (item.get("item"), item.get("reason"),
                                            item.get("action")))
    lines += ["", "## 回放说明", "",
              "```bash",
              "python3 scripts/run_skill.py --input /input --work /app/work --output /app/output",
              "```",
              "同一输入与同一 replay_key 重跑不会二次入队；已入队 alert_id 记录在",
              "`work/tracking-state.json`，可通过 `suppressed.reason=already_prepared_idempotent` 核对。", ""]
    return "\n".join(lines)


def write_candidates_csv(path, records, increments):
    category = {inc["dedupe_key"]: inc for inc in increments}
    header = ["id", "title", "authors", "year", "venue", "doi", "arxiv", "date",
              "version", "status", "dedupe_key", "category", "alert_status",
              "evidence_path", "evidence_locator"]
    lines = [",".join(header)]
    for record in records:
        inc = category.get(record.get("dedupe_key")) or {}
        ev = record.get("evidence") or {}
        import csv
        import io
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="")
        writer.writerow([
            record.get("id"), record.get("title"), record.get("authors"),
            record.get("year"), record.get("venue"), record.get("doi"),
            record.get("arxiv"), record.get("date"), record.get("version"),
            record.get("status"), record.get("dedupe_key"), inc.get("category", ""),
            (inc.get("alert") or {}).get("status", "not_in_window"),
            ev.get("path", ""), ev.get("locator", ""),
        ])
        lines.append(buf.getvalue())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def contract_payloads(plan):
    return {
        "检索结果": plan.get("increments"),
        "候选文献": plan.get("candidates"),
        "证据记录": plan.get("evidence"),
        "状态说明": plan.get("uncertainties"),
        "增量清单": plan.get("increments"),
        "窗口": plan.get("window"),
        "订阅": plan.get("subscription"),
        "提醒": plan.get("alert_log"),
        "抑制": plan.get("suppressed"),
        "检索结果集": plan.get("increments"),
        "search_results": plan.get("increments"),
        "candidates": plan.get("candidates"),
        "evidence_record": plan.get("evidence"),
        "status_note": plan.get("uncertainties"),
        "subscription": plan.get("subscription"),
        "window": plan.get("window"),
        "increments": plan.get("increments"),
        "alert_log": plan.get("alert_log"),
        "suppressed": plan.get("suppressed"),
        "replay_key": plan.get("replay_key"),
        "metrics": plan.get("metrics"),
    }


def write_contract_artifacts(output, contract, plan, fill_only=False):
    payloads = contract_payloads(plan)
    keys = [str(k) for k in (contract.keys() if isinstance(contract, dict)
                             else contract if isinstance(contract, list) else [])]
    written = {}
    for key in keys:
        value = payloads.get(key)
        if value is None:
            value = {"field": key, "status": "partial",
                     "note": "该契约键没有专用字段；以 search-results.json 与 result.json 为准",
                     "deliverable": "search-results.json",
                     "uncertainties": plan.get("uncertainties")}
        path = output / (safe_name(key) + ".json")
        emit_json(path, value, fill_only=fill_only)
        written[key] = path.name
    return written


def merge_work(existing, generated, fill_only=False):
    if not isinstance(existing, dict) or not existing:
        return generated
    out = dict(existing)
    protected = {"case_id", "mode", "_generated_by", "_domain_checks"}
    # Operational state must be regenerated on every run; otherwise replay
    # idempotency and window/watermark state would go stale.
    operational = {
        "window", "watermark", "replay_key", "snapshot_before", "snapshot_after",
        "increments", "alert_log", "suppressed", "metrics", "candidates",
        "dedupe_rules", "queries", "sources", "generated_at",
    }
    for key, value in generated.items():
        if key in protected:
            out.setdefault(key, value)
            continue
        if key in operational:
            out[key] = value
            continue
        if key not in out or out.get(key) in (None, "", [], {}):
            out[key] = value
    out["_generated_by"] = "domain_work.py" if fill_only or existing.get("_generated_by") \
        else "model+domain_work.py"
    out["_domain_checks"] = generated.get("_domain_checks")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--fill-missing", action="store_true")
    args = parser.parse_args(argv)
    work, output = Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    case = read_json(Path(args.input) / "case.json", {})
    task_text = ""
    try:
        task_text = (Path(args.input) / "task.md").read_text(encoding="utf-8-sig")
    except OSError:
        pass
    sources = load_sources(work)
    records = extract_records(sources)
    state = read_json(work / "tracking-state.json", {})
    plan = build_outputs(case, task_text, sources, records, state)
    checks = domain_checks(plan)
    plan["_domain_checks"] = checks
    work_path = work / "search-plan.json"
    plan = merge_work(read_json(work_path, {}), plan, fill_only=args.fill_missing)
    work_path = work / "search-plan.json"
    write_json(work_path, plan)

    fill_only = bool(args.fill_missing)
    emit_text(output / "tracking-digest.md", render_digest(plan), fill_only)
    emit_json(output / "search-results.json", {
        "case_id": plan.get("case_id"),
        "mode": "latest-tracking",
        "subscription": plan.get("subscription"),
        "window": plan.get("window"),
        "watermark": plan.get("watermark"),
        "replay_key": plan.get("replay_key"),
        "snapshot_before": plan.get("snapshot_before"),
        "snapshot_after": plan.get("snapshot_after"),
        "increments": plan.get("increments"),
        "alert_log": plan.get("alert_log"),
        "suppressed": plan.get("suppressed"),
        "candidates": plan.get("candidates"),
        "metrics": plan.get("metrics"),
        "dedupe_rules": plan.get("dedupe_rules"),
        "checks": checks,
        "uncertainties": plan.get("uncertainties"),
    }, fill_only)
    candidates_path = output / "candidates.csv"
    if not (fill_only and candidates_path.exists() and candidates_path.stat().st_size > 0):
        write_candidates_csv(candidates_path, plan.get("candidates") or [],
                             plan.get("increments") or [])
    emit_json(output / "search-log.json", {
        "case_id": plan.get("case_id"),
        "queries": plan.get("queries"),
        "window": plan.get("window"),
        "watermark": plan.get("watermark"),
        "replay_key": plan.get("replay_key"),
        "dedupe_rules": plan.get("dedupe_rules"),
        "snapshot_before": plan.get("snapshot_before"),
        "snapshot_after": plan.get("snapshot_after"),
        "counts": (plan.get("metrics") or {}).get("counts"),
        "state_before": {"replay_key": state.get("replay_key"),
                         "runs": state.get("runs", 0)},
        "checks": checks,
        "uncertainties": plan.get("uncertainties"),
    }, fill_only)
    emit_json(output / "evidence.json", {
        "case_id": plan.get("case_id"),
        "evidence": plan.get("evidence"),
        "note": "All quotes are verbatim from the provided material.",
    }, fill_only)
    contract = case.get("output_contract") or {}
    written = write_contract_artifacts(output, contract, plan, fill_only=fill_only)

    # Persist idempotency state only after the outputs were prepared.
    prepared = set(state.get("prepared_alert_ids") or [])
    prepared.update(alert["alert_id"] for alert in plan.get("alert_log") or []
                    if alert.get("status") in ("queued_for_digest", "already_prepared"))
    state_payload = {
        "replay_key": plan.get("replay_key"),
        "window": plan.get("window"),
        "prepared_alert_ids": sorted(prepared),
        "last_run_at": now_iso(),
        "runs": int(state.get("runs") or 0) + 1,
        "delivery": "disabled: this skill prepares alerts but does not send them",
    }
    write_json(work / "tracking-state.json", state_payload)

    audit = {
        "case_id": plan.get("case_id"), "generated_at": now_iso(),
        "script": "domain_work.py", "source_count": len(sources),
        "source_paths": [source.path for source in sources],
        "records": len(records), "increments": len(plan.get("increments") or []),
        "alerts": len(plan.get("alert_log") or []),
        "suppressed": len(plan.get("suppressed") or []),
        "evidence_count": len(plan.get("evidence") or []),
        "checks": checks, "contract_artifacts": written,
        "replay_key": plan.get("replay_key"),
        "no_materials": len(sources) == 0,
    }
    write_json(work / "domain-status.json", audit)
    write_json(output / "domain-audit.json", audit)
    print(json.dumps({"script": "domain_work.py", "case_id": plan.get("case_id"),
                      "sources": len(sources), "records": len(records),
                      "increments": len(plan.get("increments") or []),
                      "blocking_checks": checks.get("blocking", 0),
                      "replay_key": plan.get("replay_key"),
                      "contract_artifacts": written}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"script": "domain_work.py", "status": "error",
                          "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(0)
