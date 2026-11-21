#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, source-bound patent/technical-literature first pass.

All patent numbers, dates, legal statuses and classification codes come from
the provided material.  Missing fields stay ``unknown``/``missing``; the script
never infers a legal status from a title and never merges families without an
explicit shared identifier.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"arXiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?", re.I)
IPC_RE = re.compile(r"^[A-H]\d{2}[A-Z]\s?\d{1,4}/\d{2,6}$")
DATE_RE = re.compile(r"\b((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
PATENT_RE = re.compile(r"\b([A-Z]{2})\s*([0-9][0-9,\-]{3,}[0-9])\s*([A-Z]\d?)?\b")
STATUS_MAP = [
    ("granted", re.compile(r"granted|issued|授权|已授权|有效|active", re.I)),
    ("expired", re.compile(r"expired|lapsed|期限届满|失效|终止", re.I)),
    ("withdrawn", re.compile(r"withdrawn|撤回", re.I)),
    ("rejected", re.compile(r"rejected|驳回", re.I)),
    ("pending", re.compile(r"pending|under examination|审中|审查中|公开", re.I)),
    ("unknown", re.compile(r"unknown|未确认|状态未知", re.I)),
]
ACCESS_MAP = [
    ("public", re.compile(r"public|open|公开", re.I)),
    ("licensed", re.compile(r"licensed|subscription|授权访问|已订阅", re.I)),
    ("restricted", re.compile(r"restricted|confidential|付费|受限|保密", re.I)),
]


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


def tokens(text):
    return {x for x in re.findall(r"[a-z0-9]{3,}|[一-鿿]{2,}",
                                  str(text or "").lower())}


def normalize_number(raw):
    """Normalize a publication/application number; keep the kind code."""
    text = norm(raw).upper()
    match = PATENT_RE.search(text)
    if match:
        country, digits, kind = match.groups()
        return "%s%s%s" % (country, re.sub(r"[^0-9]", "", digits), kind or "")
    compact = re.sub(r"[^A-Z0-9]", "", text)
    return compact


def number_parts(normalized):
    match = re.match(r"^([A-Z]{2})(\d+)([A-Z]\d?)?$", normalized or "")
    if not match:
        return {"country": None, "number": normalized, "kind": None}
    return {"country": match.group(1), "number": match.group(2), "kind": match.group(3)}


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
        return "%04d-%02d-01" % (int(match.group(1)), int(match.group(2))), "month"
    match = re.search(r"\b((?:19|20)\d{2})\b", text)
    if match:
        return "%s-01-01" % match.group(1), "year"
    return "", "missing"


def normalize_status(value):
    text = norm(value)
    for status, pattern in STATUS_MAP:
        if pattern.search(text):
            return status
    return "unknown"


def access_of(value):
    text = norm(value)
    for status, pattern in ACCESS_MAP:
        if pattern.search(text):
            return status
    return "unknown"


def normalize_ipc(value):
    text = norm(value).upper()
    text = re.sub(r"\s+", "", text)
    match = re.match(r"^([A-H]\d{2}[A-Z])(\d{1,4})/(\d{2,6})$", text)
    if not match:
        return ""
    return "%s %s/%s" % match.groups()


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
    """Parse CSV/TSV with the stdlib reader so quoted commas stay intact."""
    text = source.text or ""
    if not text.strip():
        return source.rows or []
    sample = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if "\t" in sample else (
        ";" if ";" in sample and "," not in sample else ",")
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        if rows and len(rows[0]) >= 2:
            return rows
    except csv.Error:
        pass
    return source.rows or []


def make_patent(raw, source, index):
    number_raw = norm(raw.get("patent_number") or raw.get("publication_number")
                      or raw.get("publication") or raw.get("number") or "")
    number = normalize_number(number_raw)
    parts = number_parts(number)
    status = normalize_status(raw.get("legal_status") or raw.get("status") or "")
    status_date, status_precision = parse_date(
        raw.get("status_date") or raw.get("legal_status_date") or "")
    filing, filing_precision = parse_date(raw.get("filing_date") or "")
    priority, priority_precision = parse_date(raw.get("priority_date") or "")
    publication, publication_precision = parse_date(
        raw.get("publication_date") or raw.get("date") or "")
    grant, grant_precision = parse_date(raw.get("grant_date") or "")
    access = access_of(raw.get("access") or raw.get("license") or raw.get("permission") or "")
    patent = {
        "id": "P%03d" % index,
        "patent_number": number,
        "patent_number_raw": number_raw,
        "country": parts["country"],
        "kind_code": parts["kind"],
        "application_number": normalize_number(raw.get("application_number")
                                              or raw.get("application") or ""),
        "priority_number": normalize_number(raw.get("priority_number")
                                           or raw.get("priority") or ""),
        "title": norm(raw.get("title") or ""),
        "applicant": norm(raw.get("applicant") or raw.get("assignee") or ""),
        "inventors": norm(raw.get("inventors") or raw.get("inventor") or ""),
        "filing_date": filing or None,
        "priority_date": priority or None,
        "publication_date": publication or None,
        "grant_date": grant or None,
        "date_precision": {"filing": filing_precision, "priority": priority_precision,
                           "publication": publication_precision, "grant": grant_precision},
        "legal_status": status,
        "status_date": status_date or None,
        "status_date_precision": status_precision,
        "family_id": norm(raw.get("family_id") or raw.get("simple_family")
                          or raw.get("family") or ""),
        "ipc": normalize_ipc(raw.get("ipc") or raw.get("ipc_class") or ""),
        "cpc": normalize_ipc(raw.get("cpc") or raw.get("cpc_class") or ""),
        "abstract": norm(raw.get("abstract") or ""),
        "claims": norm(raw.get("claims") or ""),
        "access": access,
        "source": source.path,
        "needs_verification": status == "unknown",
    }
    patent["evidence"] = source.evidence_for(number_raw, number,
                                             raw.get("title") or "")
    patent["status_observation"] = {
        "patent_id": patent["id"],
        "status": status,
        "date": status_date or None,
        "date_precision": status_precision,
        "source": source.path,
        "path": source.path,
        "locator": (patent.get("evidence") or {}).get("locator"),
        "quote": (patent.get("evidence") or {}).get("quote"),
        "needs_verification": status == "unknown",
    }
    return patent


def patents_from_json(value, source, start_index):
    items = []
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        for key in ("patents", "records", "items", "results", "families"):
            if isinstance(value.get(key), list):
                items = value[key]
                break
        else:
            if any(key in value for key in ("patent_number", "publication_number", "number")):
                items = [value]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        patent = make_patent(item, source, start_index + len(out))
        if patent["patent_number"] or patent["title"]:
            out.append(patent)
    return out


def patents_from_csv(source, start_index):
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
        "patent_number": col("patent_number", "publication_number", "publication",
                             "公开号", "专利号"),
        "application_number": col("application_number", "application", "申请号"),
        "priority_number": col("priority_number", "priority", "优先权号"),
        "title": col("title", "标题", "名称"),
        "applicant": col("applicant", "assignee", "申请人", "专利权人"),
        "inventors": col("inventors", "inventor", "发明人"),
        "filing_date": col("filing_date", "申请日"),
        "priority_date": col("priority_date", "优先权日"),
        "publication_date": col("publication_date", "date", "公开日"),
        "grant_date": col("grant_date", "授权日"),
        "legal_status": col("legal_status", "status", "法律状态"),
        "status_date": col("status_date", "状态日期"),
        "family_id": col("family_id", "family", "同族"),
        "ipc": col("ipc", "ipc_class"),
        "cpc": col("cpc", "cpc_class"),
        "abstract": col("abstract", "摘要"),
        "claims": col("claims", "权利要求"),
        "access": col("access", "license", "权限"),
    }
    out = []
    for row in rows[1:]:
        if not isinstance(row, list):
            continue
        raw = {}
        for key, index in mapping.items():
            if index is not None and index < len(row):
                raw[key] = norm(row[index])
        patent = make_patent(raw, source, start_index + len(out))
        if patent["patent_number"] or patent["title"]:
            out.append(patent)
    return out


def patents_from_text(source, start_index):
    out = []
    for index, line in enumerate(source.lines, 1):
        text = norm(line)
        match = PATENT_RE.search(text)
        if not match and not re.search(r"(?:patent|专利|公开号|申请号)\s*[:：]", text, re.I):
            continue
        raw = {"title": re.sub(r"^[#*\-\d.\s]+", "", text)}
        if match:
            raw["patent_number"] = match.group(0)
        status = normalize_status(text)
        if status != "unknown":
            raw["legal_status"] = status
        patent = make_patent(raw, source, start_index + len(out))
        out.append(patent)
    return out


def extract_patents(sources):
    out = []
    for source in sources:
        chunk = []
        if source.data is not None:
            chunk.extend(patents_from_json(source.data, source, len(out) + len(chunk) + 1))
        if source.rows:
            chunk.extend(patents_from_csv(source, len(out) + len(chunk) + 1))
        if not (source.data is not None or source.rows):
            chunk.extend(patents_from_text(source, len(out) + len(chunk) + 1))
        out.extend(chunk)
    # Deduplicate by normalized publication number; keep all observations.
    grouped, order = {}, []
    for patent in out:
        key = patent.get("patent_number") or norm(patent.get("title")).lower()
        if not key:
            continue
        observation = patent.pop("status_observation", None)
        if key in grouped:
            existing = grouped[key]
            # A sparse observation row may arrive before the full record:
            # fill missing fields from whichever record has them.
            for field, value in patent.items():
                if value in (None, "", [], {}) or field in ("id", "source"):
                    continue
                if existing.get(field) in (None, "", [], {}):
                    existing[field] = value
            if observation:
                existing["status_observations"].append(observation)
            continue
        patent["status_observations"] = [observation] if observation else []
        grouped[key] = patent
        order.append(key)
    result = [grouped[key] for key in order]
    for index, patent in enumerate(result, 1):
        patent["id"] = "P%03d" % index
    return result


def extract_literature(sources):
    out = []
    for source in sources:
        for index, line in enumerate(source.lines, 1):
            text = norm(line)
            doi = DOI_RE.search(text)
            arxiv = ARXIV_RE.search(text)
            if not (doi or arxiv):
                continue
            out.append({
                "id": "T%03d" % (len(out) + 1),
                "title": re.sub(r"^[#*\-\d.\s]+", "", text)[:300],
                "doi": doi.group(0) if doi else None,
                "arxiv": ("arXiv:%s" % arxiv.group(1)) if arxiv else None,
                "source": source.path,
                "evidence": {"path": source.path, "locator": "L%d" % index,
                             "quote": line.strip()},
            })
    return out


def build_families(patents):
    groups = {}
    for patent in patents:
        family_id = patent.get("family_id")
        priority = patent.get("priority_number")
        if family_id:
            key, basis_type, basis_value = "family:%s" % family_id, "family_id", family_id
        elif priority:
            key, basis_type, basis_value = "priority:%s" % priority, "priority_number", priority
        else:
            key = "separate:%s" % (patent.get("patent_number") or patent["id"])
            basis_type, basis_value = "none", ""
        groups.setdefault(key, {"basis_type": basis_type, "basis_value": basis_value,
                                "members": []})["members"].append(patent)
    families, patent_family = [], {}
    for index, (key, group) in enumerate(sorted(groups.items()), 1):
        family = {
            "family_id": "F%03d" % index,
            "basis_type": group["basis_type"],
            "basis_value": group["basis_value"] or None,
            "members": [p["id"] for p in group["members"]],
            "member_numbers": [p["patent_number"] for p in group["members"]],
            "scope": ("simple_family_from_explicit_identifier" if group["basis_type"] != "none"
                      else "singleton_no_family_evidence"),
            "confidence": "high" if group["basis_type"] != "none" else "low",
            "note": ("同族依据来自材料中的显式标识符" if group["basis_type"] != "none"
                     else "材料未给出 family_id/优先权号，按单件保留，不推测扩展族"),
        }
        families.append(family)
        for patent in group["members"]:
            patent_family[patent["id"]] = family["family_id"]
    return families, patent_family


def legal_status_table(patents):
    rows = []
    for patent in patents:
        observations = patent.get("status_observations") or []
        statuses = {obs.get("status") for obs in observations if obs.get("status")}
        known = statuses - {"unknown"}
        if len(known) > 1:
            patent["legal_status"] = "conflict"
            patent["needs_verification"] = True
        elif len(known) == 1:
            patent["legal_status"] = next(iter(known))
            patent["needs_verification"] = False
        else:
            patent["legal_status"] = "unknown"
            patent["needs_verification"] = True
        for obs in observations:
            rows.append({
                "patent_id": patent["id"],
                "status": obs.get("status"),
                "date": obs.get("date"),
                "date_precision": obs.get("date_precision"),
                "source": obs.get("source"),
                "path": obs.get("path") or obs.get("source"),
                "locator": obs.get("locator"),
                "quote": obs.get("quote"),
                "needs_verification": obs.get("needs_verification", False)
                                 or patent["legal_status"] == "conflict",
            })
        if not observations:
            rows.append({
                "patent_id": patent["id"], "status": "unknown", "date": None,
                "source": patent.get("source"), "path": patent.get("source"),
                "locator": None, "quote": None, "needs_verification": True,
            })
    return rows


def classification_table(patents):
    rows, missing = [], []
    for patent in patents:
        for scheme, code in (("IPC", patent.get("ipc")), ("CPC", patent.get("cpc"))):
            if code:
                rows.append({"patent_id": patent["id"], "code": code, "scheme": scheme,
                             "valid": bool(IPC_RE.match(code))})
            else:
                missing.append({"patent_id": patent["id"], "scheme": scheme,
                                "status": "missing"})
    return rows, missing


def build_queries(case, task_text, patents, literature):
    raw = case.get("queries") if isinstance(case.get("queries"), list) else []
    queries = []
    for index, query in enumerate(raw, 1):
        if isinstance(query, dict):
            queries.append({
                "id": str(query.get("id") or "Q%d" % index),
                "round": int(query.get("round") or 1),
                "query_type": query.get("query_type") or "topic",
                "query": norm(query.get("query") or query.get("query_text") or ""),
                "source": norm(query.get("source") or "provided corpus"),
                "purpose": norm(query.get("purpose") or "identify patents/literature"),
                "executed": False,
                "execution_note": "No live paid-database call; only the provided corpus was used.",
            })
    if not queries:
        numbers = [p["patent_number"] for p in patents if p.get("patent_number")]
        if numbers:
            queries.append({
                "id": "Q1", "round": 1, "query_type": "number_lookup",
                "query": " OR ".join(numbers[:20]),
                "source": "provided patent corpus",
                "purpose": "locate the target patents and their recorded family members",
                "executed": False,
                "execution_note": "Number lookup within the provided corpus only.",
            })
        topics = []
        for patent in patents[:3]:
            topics.extend(sorted(tokens(patent.get("title") or ""))[:8])
        if topics:
            queries.append({
                "id": "Q2", "round": 1, "query_type": "topic",
                "query": " ".join(dict.fromkeys(topics))[:300],
                "source": "provided patent corpus",
                "purpose": "topic recall and technical-literature discovery",
                "executed": False,
                "execution_note": "Topic query is a transparent token query over the corpus; "
                                  "IPC/CPC facets are listed separately.",
            })
    return queries


def build_project(case, task_text, sources):
    patents = extract_patents(sources)
    literature = extract_literature(sources)
    families, patent_family = build_families(patents)
    legal_status = legal_status_table(patents)
    classification, classification_missing = classification_table(patents)
    queries = build_queries(case, task_text, patents, literature)
    evidence = []
    seen = set()
    for item in patents + literature:
        ev = item.get("evidence")
        if ev and ev.get("quote"):
            key = (ev.get("path"), norm(ev.get("quote")))
            if key not in seen:
                seen.add(key)
                evidence.append(ev)
    topic = " ".join((case.get("topic") or case.get("query") or
                      (queries[1]["query"] if len(queries) > 1 else "")) or "")
    candidates = []
    for index, patent in enumerate(patents, 1):
        overlap = len(tokens(topic) & tokens("%s %s" % (patent.get("title") or "",
                                                        patent.get("abstract") or "")))
        candidates.append({
            "id": patent["id"],
            "record_type": "patent",
            "title": patent.get("title"),
            "patent_number": patent.get("patent_number"),
            "year": (patent.get("publication_date") or "")[:4] or None,
            "venue": "patent corpus",
            "dedupe_key": patent.get("patent_number"),
            "relevance": "core" if overlap >= 2 else "related" if overlap else "unknown",
            "rank": index,
            "score": overlap,
            "reason": ("Matched by normalized number from the provided corpus; "
                       "topic overlap tokens=%d" % overlap),
            "family_id": patent_family.get(patent["id"]),
            "legal_status": patent.get("legal_status"),
            "evidence": patent.get("evidence"),
        })
    for index, item in enumerate(literature, 1):
        year_match = re.search(r"\b((?:19|20)\d{2})\b", item.get("title") or "")
        candidates.append({
            "id": item["id"],
            "record_type": "technical_literature",
            "title": item.get("title"),
            "doi": item.get("doi"),
            "arxiv": item.get("arxiv"),
            "year": year_match.group(1) if year_match else None,
            "venue": "technical literature",
            "dedupe_key": ("doi:%s" % (item.get("doi") or "").lower()
                           if item.get("doi") else "arxiv:%s" % item.get("arxiv")),
            "relevance": "related" if topic and tokens(topic) & tokens(item.get("title") or "")
                         else "unknown",
            "rank": len(patents) + index,
            "score": len(tokens(topic) & tokens(item.get("title") or "")),
            "reason": "Technical literature found in the provided corpus; identifier is verbatim.",
            "evidence": item.get("evidence"),
        })
    access_boundaries = []
    for source in sources:
        access = "unknown"
        for line in source.lines:
            if re.search(r"license|access|permission|授权|许可|权限|保密", line, re.I):
                access = access_of(line)
                if access != "unknown":
                    break
        access_boundaries.append({
            "source": source.path,
            "access": access,
            "display": ("metadata_and_verbatim_quotes" if access in ("public", "licensed")
                        else "metadata_only" if access == "restricted" else
                        "unknown_requires_confirmation"),
            "note": ("Restricted sources: do not reproduce claims/full text."
                     if access == "restricted" else
                     "Access level was not stated; confirm before redistribution."),
        })
    uncertainties = []
    if not sources:
        uncertainties.append({"item": "materials", "reason": "未找到可读材料",
                              "impact": "无法检索专利或技术文献", "action": "提供专利记录/原文后重跑"})
    if not patents:
        uncertainties.append({"item": "patents", "reason": "材料中没有可解析的专利记录",
                              "impact": "专利号/同族/法律状态均无法给出",
                              "action": "提供公开号或专利著录项"})
    if classification_missing:
        uncertainties.append({"item": "classification",
                              "reason": "%d 个专利缺 IPC/CPC 分类号" % len(classification_missing),
                              "impact": "技术分类覆盖不完整",
                              "action": "回官方登记页补齐分类号，不用标题猜测"})
    if any(p.get("legal_status") == "unknown" for p in patents):
        uncertainties.append({"item": "legal_status",
                              "reason": "部分专利法律状态未确认",
                              "impact": "不能据此做自由实施或有效性判断",
                              "action": "逐件回官方登记核验状态与生效日"})
    if any(p.get("legal_status") == "conflict" for p in patents):
        uncertainties.append({"item": "legal_status_conflict",
                              "reason": "同一专利存在互相冲突的状态观察",
                              "impact": "不能合并为单一状态",
                              "action": "保留全部观察值并转专利律师人工裁决"})
    if any(p.get("access") == "restricted" for p in patents) or \
            any(row["access"] == "restricted" for row in access_boundaries):
        uncertainties.append({"item": "access_boundary",
                              "reason": "存在受限来源",
                              "impact": "不能展示权利要求/全文",
                              "action": "仅保留元数据与来源定位"})
    project = {
        "case_id": str(case.get("case_id") or case.get("id") or "CASE"),
        "mode": "patent-search",
        "summary": ("在提供的有限语料中定位 %d 件专利、%d 条技术文献；"
                    "同族/法律状态只按显式字段归并，未知状态保留为 unknown。" % (
                        len(patents), len(literature))),
        "question": norm(case.get("topic") or case.get("query") or ""),
        "queries": queries,
        "patents": patents,
        "families": families,
        "legal_status": legal_status,
        "classification": classification,
        "classification_missing": classification_missing,
        "technical_literature": literature,
        "candidates": candidates,
        "screening": {
            "include": [p["id"] for p in patents],
            "exclude": [],
            "uncertain": [p["id"] for p in patents if p.get("legal_status") == "unknown"],
        },
        "access_boundaries": access_boundaries,
        "metrics": {
            "patents": len(patents),
            "families": len(families),
            "technical_literature": len(literature),
            "legal_status_known": sum(1 for p in patents
                                      if p.get("legal_status") not in ("unknown", "conflict")),
            "legal_status_unknown": sum(1 for p in patents
                                        if p.get("legal_status") == "unknown"),
            "classification_present": len(classification),
            "classification_missing": len(classification_missing),
            "claim_scope_available": sum(1 for p in patents if p.get("claims")),
            "definition": "counts from the provided corpus only; no live database coverage claim",
        },
        "evidence": evidence,
        "uncertainties": uncertainties,
        "sources": [{"path": source.path, "chars": len(source.text),
                     "kind": source.kind} for source in sources],
        "generated_at": now_iso(),
        "_generated_by": "domain_work.py",
    }
    checks = domain_checks(project)
    project["_domain_checks"] = checks
    return project


def domain_checks(project):
    problems, warnings = [], []
    patents = project.get("patents") or []
    by_id = {p["id"]: p for p in patents}
    for patent in patents:
        for field in ("patent_number", "patent_number_raw", "legal_status",
                      "application_number", "title"):
            if field not in patent:
                problems.append("patent %s lacks %s" % (patent.get("id"), field))
        if not patent.get("patent_number"):
            problems.append("patent %s has no normalized number" % patent.get("id"))
        if normalize_number(patent.get("patent_number_raw") or "") != patent.get("patent_number"):
            problems.append("patent %s normalization is not reproducible" % patent.get("id"))
        if patent.get("legal_status") in ("granted", "expired", "withdrawn",
                                          "rejected", "pending"):
            if not patent.get("status_observations"):
                problems.append("patent %s has status without observation" % patent.get("id"))
            for obs in patent.get("status_observations") or []:
                if not obs.get("source"):
                    problems.append("patent %s status observation lacks source" % patent.get("id"))
        if patent.get("legal_status") == "unknown" and not patent.get("needs_verification"):
            problems.append("unknown status must set needs_verification=true")
    families = project.get("families") or []
    seen_members = {}
    for family in families:
        if not family.get("basis_type"):
            problems.append("family %s lacks basis_type" % family.get("family_id"))
        if family.get("basis_type") != "none" and not family.get("basis_value"):
            problems.append("family %s lacks basis_value" % family.get("family_id"))
        for member in family.get("members") or []:
            if member in seen_members:
                problems.append("patent %s appears in two families" % member)
            seen_members[member] = family.get("family_id")
            patent = by_id.get(member) or {}
            if family.get("basis_type") == "priority_number" \
                    and patent.get("priority_number") != family.get("basis_value"):
                problems.append("family %s member %s does not share the priority basis"
                                % (family.get("family_id"), member))
            if family.get("basis_type") == "family_id" \
                    and patent.get("family_id") != family.get("basis_value"):
                problems.append("family %s member %s does not share family_id"
                                % (family.get("family_id"), member))
    for patent in patents:
        if patent["id"] not in seen_members:
            problems.append("patent %s is not assigned to any family group" % patent["id"])
    status_rows = project.get("legal_status") or []
    for row in status_rows:
        if row.get("status") not in ("granted", "expired", "withdrawn", "rejected",
                                     "pending", "unknown"):
            problems.append("legal status row %s has invalid status %r"
                            % (row.get("patent_id"), row.get("status")))
        if row.get("status") != "unknown" and not (row.get("source") and row.get("date")):
            problems.append("known legal status %s lacks source/date" % row.get("patent_id"))
    for row in project.get("classification") or []:
        if not IPC_RE.match(row.get("code") or ""):
            problems.append("classification %r for %s is not a valid IPC/CPC code"
                            % (row.get("code"), row.get("patent_id")))
    for candidate in project.get("candidates") or []:
        for field in ("reason", "evidence"):
            if not candidate.get(field):
                problems.append("candidate %s lacks %s" % (candidate.get("id"), field))
    for row in project.get("access_boundaries") or []:
        if row.get("access") not in ("public", "licensed", "restricted", "unknown"):
            problems.append("invalid access level for %s" % row.get("source"))
    for evidence in project.get("evidence") or []:
        for field in ("path", "locator", "quote"):
            if not evidence.get(field):
                problems.append("evidence lacks %s" % field)
    if (project.get("metrics") or {}).get("legal_status_unknown", 0):
        warnings.append("some legal statuses are unknown and need official verification")
    return {"problems": problems, "warnings": warnings,
            "blocking": len(problems),
            "source_count": len(project.get("sources") or []),
            "evidence_count": len(project.get("evidence") or [])}


def render_report(project):
    metrics = project.get("metrics") or {}
    lines = [
        "# 专利与技术文献检索报告", "",
        "- case_id: `%s`" % project.get("case_id"),
        "- 生成时间: %s" % project.get("generated_at"),
        "- 语料范围：**provided_corpus_only**（%d 件专利，%d 条技术文献）" % (
            metrics.get("patents", 0), metrics.get("technical_literature", 0)),
        "", "> 检索只在组织方提供的语料内执行；未访问付费库，也不声称全球覆盖。",
        "> 法律状态未知时保留 unknown，不根据标题或二手页面猜测。",
        "", "## 1. 检索式", "",
    ]
    for query in project.get("queries") or []:
        lines.append("- `%s` [%s] `%s`（来源：%s；已执行：%s）" % (
            query.get("id"), query.get("query_type"), query.get("query"),
            query.get("source"), query.get("executed")))
    lines += ["", "## 2. 目标专利与同族", "",
              "| 专利 | 公开号（规范） | 原始号 | 申请人 | 优先权日 | 法律状态 | 同族 |",
              "|---|---|---|---|---|---|---|"]
    family_of = {}
    for family in project.get("families") or []:
        for member in family.get("members") or []:
            family_of[member] = family.get("family_id")
    for patent in project.get("patents") or []:
        lines.append("| %s | `%s` | %s | %s | %s | %s | %s |" % (
            patent.get("id"), patent.get("patent_number"),
            patent.get("patent_number_raw"), patent.get("applicant") or "—",
            patent.get("priority_date") or "—", patent.get("legal_status"),
            family_of.get(patent.get("id"), "—")))
    lines += ["", "### 同族归并依据", ""]
    for family in project.get("families") or []:
        lines.append("- `%s` basis=%s:%s members=%s scope=%s confidence=%s" % (
            family.get("family_id"), family.get("basis_type"),
            family.get("basis_value"), ", ".join(family.get("members") or []),
            family.get("scope"), family.get("confidence")))
    lines += ["", "## 3. 法律状态（逐条来源）", "",
              "| 专利 | 状态 | 日期 | 来源 | 定位 | 需核验 |", "|---|---|---|---|---|---|"]
    for row in project.get("legal_status") or []:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            row.get("patent_id"), row.get("status"), row.get("date") or "—",
            row.get("source") or "—", row.get("locator") or "—",
            "是" if row.get("needs_verification") else "否"))
    lines += ["", "## 4. 技术分类", ""]
    for row in project.get("classification") or []:
        lines.append("- %s %s `%s`（valid=%s）" % (
            row.get("patent_id"), row.get("scheme"), row.get("code"), row.get("valid")))
    if project.get("classification_missing"):
        lines.append("- 缺失分类：%s" % ", ".join(
            "%s/%s" % (row.get("patent_id"), row.get("scheme"))
            for row in project["classification_missing"]))
    lines += ["", "## 5. 技术文献", ""]
    for item in project.get("technical_literature") or []:
        lines.append("- %s DOI=%s arXiv=%s（证据：%s %s）" % (
            str(item.get("title"))[:100], item.get("doi") or "—",
            item.get("arxiv") or "—", (item.get("evidence") or {}).get("path"),
            (item.get("evidence") or {}).get("locator")))
    if not project.get("technical_literature"):
        lines.append("- 所提供语料中没有可解析的 DOI/arXiv 技术文献")
    lines += ["", "## 6. 授权与保密边界", ""]
    for row in project.get("access_boundaries") or []:
        lines.append("- `%s`：access=%s；display=%s；%s" % (
            row.get("source"), row.get("access"), row.get("display"), row.get("note")))
    lines += ["", "## 7. 证据与不确定性", ""]
    for ev in (project.get("evidence") or [])[:40]:
        lines.append("- `%s` %s：%s" % (ev.get("path"), ev.get("locator"), ev.get("quote")))
    if project.get("uncertainties"):
        lines += ["", "### 未确认 / 缺口"]
        for item in project["uncertainties"]:
            lines.append("- %s：%s → %s" % (item.get("item"), item.get("reason"),
                                            item.get("action")))
    lines += ["", "## 8. 复跑说明", "",
              "```bash",
              "python3 scripts/run_skill.py --input /input --work /app/work --output /app/output",
              "```",
              "号码规范化、同族依据、状态观察值与分类号均可从 `output/search-results.json` 复算。", ""]
    return "\n".join(lines)


def contract_payloads(project):
    return {
        "检索结果": {"patents": project.get("patents"),
                     "technical_literature": project.get("technical_literature"),
                     "candidates": project.get("candidates")},
        "专利清单": project.get("patents"),
        "同族": project.get("families"),
        "法律状态": project.get("legal_status"),
        "技术分类": project.get("classification"),
        "证据记录": project.get("evidence"),
        "状态说明": project.get("uncertainties"),
        "检索式": project.get("queries"),
        "candidates": project.get("candidates"),
        "patents": project.get("patents"),
        "families": project.get("families"),
        "legal_status": project.get("legal_status"),
        "classification": project.get("classification"),
        "technical_literature": project.get("technical_literature"),
        "evidence_record": project.get("evidence"),
        "status_note": project.get("uncertainties"),
    }


def write_contract_artifacts(output, contract, project, fill_only=False):
    payloads = contract_payloads(project)
    keys = [str(k) for k in (contract.keys() if isinstance(contract, dict)
                             else contract if isinstance(contract, list) else [])]
    written = {}
    for key in keys:
        value = payloads.get(key)
        if value is None:
            value = {"field": key, "status": "partial",
                     "note": "该契约键没有专用字段；以 search-results.json 与 result.json 为准",
                     "deliverable": "search-results.json",
                     "uncertainties": project.get("uncertainties")}
        path = output / (safe_name(key) + ".json")
        emit_json(path, value, fill_only=fill_only)
        written[key] = path.name
    return written


def merge_work(existing, generated, fill_only=False):
    if not isinstance(existing, dict) or not existing:
        return generated
    out = dict(existing)
    protected = {"case_id", "mode", "_generated_by", "_domain_checks"}
    for key, value in generated.items():
        if key in protected:
            out.setdefault(key, value)
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
    project = build_project(case, task_text, sources)
    work_path = work / "search-plan.json"
    project = merge_work(read_json(work_path, {}), project, fill_only=args.fill_missing)
    write_json(work_path, project)

    fill_only = bool(args.fill_missing)
    emit_text(output / "patent-search-report.md", render_report(project), fill_only)
    emit_json(output / "search-results.json", {
        "case_id": project.get("case_id"),
        "mode": "patent-search",
        "question": project.get("question"),
        "queries": project.get("queries"),
        "patents": project.get("patents"),
        "families": project.get("families"),
        "legal_status": project.get("legal_status"),
        "classification": project.get("classification"),
        "classification_missing": project.get("classification_missing"),
        "technical_literature": project.get("technical_literature"),
        "candidates": project.get("candidates"),
        "screening": project.get("screening"),
        "access_boundaries": project.get("access_boundaries"),
        "metrics": project.get("metrics"),
        "evidence": project.get("evidence"),
        "uncertainties": project.get("uncertainties"),
        "checks": project.get("_domain_checks"),
    }, fill_only)
    candidates_path = output / "candidates.csv"
    if not (fill_only and candidates_path.exists() and candidates_path.stat().st_size > 0):
        import csv
        import io
        header = ["id", "record_type", "title", "patent_number", "doi", "arxiv",
                  "relevance", "rank", "score", "reason", "evidence_path", "evidence_locator"]
        lines = [",".join(header)]
        for candidate in project.get("candidates") or []:
            buf = io.StringIO()
            writer = csv.writer(buf, lineterminator="")
            ev = candidate.get("evidence") or {}
            writer.writerow([
                candidate.get("id"), candidate.get("record_type"), candidate.get("title"),
                candidate.get("patent_number"), candidate.get("doi"),
                candidate.get("arxiv"), candidate.get("relevance"), candidate.get("rank"),
                candidate.get("score"), candidate.get("reason"),
                ev.get("path"), ev.get("locator"),
            ])
            lines.append(buf.getvalue())
        candidates_path.parent.mkdir(parents=True, exist_ok=True)
        candidates_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    emit_json(output / "search-log.json", {
        "case_id": project.get("case_id"),
        "queries": project.get("queries"),
        "sources": project.get("sources"),
        "normalization_rules": {
            "publication_number": "uppercase; remove spaces/commas/hyphens; keep country + digits + kind code",
            "status": "explicit field only; missing => unknown + needs_verification",
            "family": "only explicit family_id or shared priority_number; otherwise singleton",
            "classification": "IPC/CPC normalized to 'SECTIONnn CLASS nnnn/nn'",
        },
        "metrics": project.get("metrics"),
        "checks": project.get("_domain_checks"),
        "uncertainties": project.get("uncertainties"),
    }, fill_only)
    emit_json(output / "evidence.json", {
        "case_id": project.get("case_id"),
        "evidence": project.get("evidence"),
        "note": "All quotes are verbatim from the provided material.",
    }, fill_only)
    contract = case.get("output_contract") or {}
    written = write_contract_artifacts(output, contract, project, fill_only=fill_only)
    audit = {
        "case_id": project.get("case_id"), "generated_at": now_iso(),
        "script": "domain_work.py", "source_count": len(sources),
        "source_paths": [source.path for source in sources],
        "patents": len(project.get("patents") or []),
        "families": len(project.get("families") or []),
        "technical_literature": len(project.get("technical_literature") or []),
        "evidence_count": len(project.get("evidence") or []),
        "checks": project.get("_domain_checks"),
        "contract_artifacts": written,
        "no_materials": len(sources) == 0,
    }
    write_json(work / "domain-status.json", audit)
    write_json(output / "domain-audit.json", audit)
    print(json.dumps({"script": "domain_work.py", "case_id": project.get("case_id"),
                      "sources": len(sources),
                      "patents": len(project.get("patents") or []),
                      "families": len(project.get("families") or []),
                      "blocking_checks": (audit.get("checks") or {}).get("blocking", 0),
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
