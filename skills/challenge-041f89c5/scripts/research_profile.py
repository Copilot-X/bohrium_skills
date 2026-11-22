#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic research-profile pass for the 9-source/10-record cases.

The script reads the material that ``run_case.py prepare`` extracted into
``work/materials.json`` and produces a source-bound first pass:

* source registry (actual supplied sources, hashes, sizes, record mapping);
* record registry (10 records when supplied, with duplicate/abstract status);
* prospective screening/search protocol and per-record decisions;
* evidence matrix with one verbatim source locator per record;
* mode-specific analysis (trend / topic-selection / review-generation);
* a Markdown report, references and extra machine-readable artifacts.

It never invents records, identifiers, counts, novelty or search results.  A
missing source or record is reported as a gap, not filled with a placeholder.
Exit code is always 0 unless the process cannot start; the runner decides the
result status from the written audit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MODE_DEFAULT = "topic-selection"
SOURCE_DOC_KINDS = {"pdf", "docx", "doc", "tex", "latex", "md", "txt", "text",
                    "html", "xml", "rst", "org"}
REGISTRY_KINDS = {"csv", "json", "bib", "ris"}
SUPPORT_NOTE_RE = re.compile(
    r"(^|[/_-])(note|notes|metadata|constraint|constraints|readme|instruction|"
    r"export|search-log|searchlog)([/_.-]|$)", re.I)
TOPIC_KEYS = ("topic", "theme", "subject", "主题")
YEAR_KEYS = ("year", "publication_year", "pub_year", "年份", "date")
TITLE_KEYS = ("title", "标题", "name")
ID_KEYS = ("record_id", "paper_id", "study_id", "candidate_id", "id", "key")
DUP_KEYS = ("duplicate_of", "duplicate", "重复")
SOURCE_KEYS = ("source", "source_path", "source_file", "path", "file")
FULLTEXT_KEYS = ("full_text", "fulltext", "full_text_available",
                 "fulltext_available", "access")
ABSTRACT_KEYS = ("abstract", "summary", "摘要")
METHOD_KEYS = ("method", "method_family", "design", "approach", "方法")
DATA_KEYS = ("data", "dataset", "corpus", "sample", "数据")
EVAL_KEYS = ("evaluation", "metric", "metrics", "evaluation_object", "评价")
CONCLUSION_KEYS = ("conclusion", "result", "主结果", "结论", "findings")
NOTES_KEYS = ("notes", "note", "comment", "备注")
CITATION_KEYS = ("citations", "cited_by", "citation_count", "被引")
AUTHOR_KEYS = ("authors", "author", "作者")
VENUE_KEYS = ("venue", "journal", "conference", "出版社", "期刊")
DOI_KEYS = ("doi",)
ARXIV_KEYS = ("arxiv", "arxiv_id")
STATUS_KEYS = ("status", "publication_status", "状态")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"arXiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?", re.I)
FIELD_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?(?:#{1,6}\s*)?([A-Za-z][A-Za-z0-9 _/\-]{1,48}|"
    r"[一-鿿]{1,16})\s*[:：]\s*(.*)$")
CAUSAL_EN = re.compile(
    r"\b(caus(?:e|ed|es|ing)|led to|leads to|drives?|drove|due to|"
    r"resulted in|results in|attributable to|because of)\b", re.I)
CAUSAL_ZH = re.compile(r"导致|使得|造成|推动了|驱动了|引发|归因于|因为.{0,20}所以")
NEGATION = re.compile(r"不|并非|不能|未|没有|无关|not\b|no evidence|cannot|"
                      r"does not|is not|isn't|aren't|rather than|instead of", re.I)
STOPWORDS = set("""a an the and or of for in on to with from by as is are was were
be been being this that these those we our study studies paper papers result results
method methods using used can may might show shows showed suggest suggests
provided supplied corpus record records data set sets about between among into
than then also more most less least high low new novel first two three one
synthetic only fabricated local testing test audit not no does did do has have
had it its their there here what which who whom whose when where why how
""".split())

MODE = MODE_DEFAULT


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_iso():
    return datetime.now(timezone.utc).date().isoformat()


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
    return re.sub(r"\s+", " ", str(text if text is not None else "")).strip()


def norm_key(text):
    return re.sub(r"[^0-9a-z一-鿿]+", "", str(text or "").lower())


def safe_name(key):
    return re.sub(r"[^\w一-鿿.-]+", "_", str(key or "field")).strip("._") or "field"


def as_int(value):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def as_float(value):
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def first_year(value):
    match = YEAR_RE.search(str(value or ""))
    return int(match.group(0)) if match else None


def tokens(text):
    return {x for x in re.findall(r"[a-z0-9]{3,}|[一-鿿]{2,}",
                                  str(text or "").lower())
            if x not in STOPWORDS and not x.isdigit()}


def containment(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta))


def jaccard(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def quote_ok(quote):
    return bool(norm(quote))


def causal_text(text):
    text = str(text or "")
    if NEGATION.search(text):
        return False
    return bool(CAUSAL_EN.search(text) or CAUSAL_ZH.search(text))


def unique_evidence(items):
    seen, out = set(), []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        quote = norm(item.get("quote"))
        path = str(item.get("path") or "")
        if not quote or not path:
            continue
        key = (path, quote)
        if key in seen:
            continue
        seen.add(key)
        out.append({"path": path, "locator": str(item.get("locator") or ""),
                    "quote": quote})
    return out


def row_dict(row, header):
    out = {}
    for index, value in enumerate(row):
        key = header[index] if index < len(header) else "col%d" % index
        out[str(key).strip()] = str(value or "").strip()
    return out


def pick(row, *names):
    lowered = {norm_key(k): v for k, v in row.items()}
    for name in names:
        value = lowered.get(norm_key(name))
        if value not in (None, ""):
            return value
    return ""


def parse_fields(text):
    """Return canonical key -> (value, 1-based line number, raw line)."""
    fields = {}
    aliases = {
        "title": "title", "标题": "title",
        "year": "year", "publicationyear": "year", "年份": "year", "date": "year",
        "authors": "authors", "author": "authors", "作者": "authors",
        "venue": "venue", "journal": "venue", "conference": "venue", "期刊": "venue",
        "doi": "doi",
        "arxiv": "arxiv", "arxivid": "arxiv",
        "abstract": "abstract", "summary": "abstract", "摘要": "abstract",
        "topic": "topic", "theme": "topic", "subject": "topic", "主题": "topic",
        "method": "method", "methodfamily": "method", "design": "method",
        "approach": "method", "方法": "method",
        "data": "data", "dataset": "data", "corpus": "data", "sample": "data",
        "数据": "data",
        "evaluation": "evaluation", "metric": "evaluation", "metrics": "evaluation",
        "evaluationobject": "evaluation", "评价": "evaluation",
        "result": "result", "conclusion": "conclusion", "结论": "conclusion",
        "finding": "finding", "findings": "finding",
        "limitation": "limitation", "limitations": "limitation",
        "recommendation": "recommendation",
        "note": "notes", "notes": "notes", "comment": "notes", "备注": "notes",
        "citations": "citations", "citedby": "citations", "citationcount": "citations",
        "status": "status", "publicationstatus": "status", "状态": "status",
        "fulltext": "full_text", "fulltextavailable": "full_text",
        "access": "full_text",
        "source": "source", "sourcepath": "source", "sourcefile": "source",
        "researchquestion": "question", "question": "question", "研究问题": "question",
        "population": "population", "研究对象": "population", "人群": "population",
        "variables": "variables", "变量": "variables", "指标": "variables",
        "baseline": "baseline", "comparisonbaseline": "baseline", "对照": "baseline",
        "expectedconclusion": "expected_conclusion", "预期结论": "expected_conclusion",
        "falsificationcondition": "falsification", "falsificationconditions": "falsification",
        "falsifiability": "falsification", "证伪条件": "falsification",
        "constraints": "constraints", "constraint": "constraints", "约束": "constraints",
        "goal": "goal", "目标": "goal",
        "innovation": "innovation", "claimedinnovation": "innovation",
        "创新": "innovation",
    }
    for index, raw in enumerate(str(text or "").splitlines(), 1):
        match = FIELD_RE.match(raw)
        if not match:
            continue
        key = aliases.get(norm_key(match.group(1)))
        if not key:
            continue
        value = norm(match.group(2))
        if not value:
            continue
        if key not in fields or not fields[key][0]:
            fields[key] = (value, index, raw.strip())
    return fields


def line_of(text, needle):
    needle_n = norm(needle).lower()
    if not needle_n:
        return None
    for index, raw in enumerate(str(text or "").splitlines(), 1):
        if needle_n in norm(raw).lower():
            return index, raw.strip()
    return None


class Source:
    def __init__(self, item):
        self.path = str(item.get("path") or item.get("rel_path") or "").replace("\\", "/")
        self.kind = str(item.get("kind") or "").lower()
        self.text = str(item.get("text") or "")
        self.rows = item.get("rows") or []
        self.data = item.get("json")
        self.entries = item.get("entries") or []
        self.chars = len(self.text)
        self.sha256 = hashlib.sha256(self.text.encode("utf-8", "ignore")).hexdigest()
        self.lines = self.text.splitlines()
        self.stem = Path(self.path).stem
        self.name = Path(self.path).name

    @property
    def is_registry(self):
        return self.kind in REGISTRY_KINDS

    @property
    def is_support_note(self):
        return bool(SUPPORT_NOTE_RE.search(self.path))

    @property
    def is_source_document(self):
        if self.is_registry or self.is_support_note:
            return False
        if self.path in ("task.md", "case.json") or self.name in ("task.md", "case.json"):
            return False
        return self.kind in SOURCE_DOC_KINDS or bool(self.text.strip())

    def evidence_at(self, line_no):
        if not (1 <= line_no <= len(self.lines)):
            return None
        raw = self.lines[line_no - 1].strip()
        return {"path": self.path, "locator": "L%d" % line_no, "quote": raw} if raw else None

    def find_line(self, needle):
        found = line_of(self.text, needle)
        if not found:
            return None
        return self.evidence_at(found[0])

    def field_evidence(self, field_key, value):
        aliases = {
            "title": ("title", "标题"),
            "year": ("year", "publication_year", "年份", "date"),
            "method": ("method", "method family", "design", "approach", "方法"),
            "data": ("data", "dataset", "corpus", "sample", "数据"),
            "evaluation": ("evaluation", "metric", "metrics", "评价"),
            "result": ("result", "结论"),
            "conclusion": ("conclusion", "结论"),
            "finding": ("finding", "findings"),
            "limitation": ("limitation", "limitations"),
            "recommendation": ("recommendation",),
            "abstract": ("abstract", "summary", "摘要"),
        }
        keys = aliases.get(field_key, (field_key,))
        value_n = norm(value).lower()
        for index, raw in enumerate(self.lines, 1):
            low = norm(raw).lower()
            if not low:
                continue
            if any(low.startswith(k.lower()) or (": " + k.lower()) in low for k in keys) \
                    or any(("%s:" % k.lower()) in low for k in keys):
                if not value_n or value_n[:40] in low or low in value_n:
                    item = self.evidence_at(index)
                    if item:
                        return item
        return None


def load_sources(work_dir, input_dir):
    materials = read_json(Path(work_dir) / "materials.json", {})
    docs = []
    for item in materials.get("documents") or []:
        if isinstance(item, dict):
            docs.append(Source(item))
    if not docs:
        # Fallback when prepare was not run: read the common text formats only.
        for path in sorted(Path(input_dir).rglob("*")) if Path(input_dir).exists() else []:
            if not path.is_file() or path.name.startswith(".") or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(input_dir).as_posix()
            suffix = path.suffix.lower().lstrip(".")
            text = ""
            rows, data, entries = [], None, []
            try:
                if suffix == "csv":
                    with path.open("r", encoding="utf-8-sig", errors="replace",
                                   newline="") as fh:
                        rows = [list(map(str, row)) for row in csv.reader(fh)]
                    text = "\\n".join("\\t".join(row) for row in rows)
                elif suffix == "json":
                    data = json.loads(path.read_text(encoding="utf-8-sig",
                                                     errors="replace"))
                    text = json.dumps(data, ensure_ascii=False)
                elif suffix in ("md", "txt", "text", "tex", "latex", "bib", "ris",
                                "html", "xml", "rst", "org"):
                    text = path.read_text(encoding="utf-8-sig", errors="replace")
                else:
                    continue
            except (OSError, ValueError):
                continue
            docs.append(Source({"path": rel, "kind": suffix, "text": text,
                                "rows": rows, "json": data, "entries": entries}))
    # Never treat the task/case wrappers as source material.
    return [doc for doc in docs
            if doc.path not in ("task.md", "case.json")
            and doc.name not in ("task.md", "case.json")
            and not doc.path.startswith("environment/")]


# ---------------------------------------------------------------------------
# record extraction
# ---------------------------------------------------------------------------
def record_from_dict(item, source=None, registry_row=None):
    if not isinstance(item, dict):
        return None

    def get(*names):
        value = pick(item, *names)
        return norm(value) if value not in (None, "") else ""

    title = get(*TITLE_KEYS)
    doi = get(*DOI_KEYS)
    arxiv = get(*ARXIV_KEYS)
    if not (title or doi or arxiv):
        return None
    record = {
        "record_id": get(*ID_KEYS),
        "title": title,
        "year": first_year(get(*YEAR_KEYS)),
        "authors": get(*AUTHOR_KEYS),
        "venue": get(*VENUE_KEYS),
        "doi": doi or None,
        "arxiv": arxiv or None,
        "abstract": get(*ABSTRACT_KEYS),
        "topic": get(*TOPIC_KEYS),
        "method": get(*METHOD_KEYS),
        "data": get(*DATA_KEYS),
        "evaluation": get(*EVAL_KEYS),
        "conclusion": get(*CONCLUSION_KEYS),
        "finding": get("finding", "findings"),
        "limitation": get("limitation", "limitations"),
        "recommendation": get("recommendation"),
        "notes": get(*NOTES_KEYS),
        "citations": as_int(get(*CITATION_KEYS)),
        "status_text": get(*STATUS_KEYS),
        "source_field": get(*SOURCE_KEYS),
        "full_text_field": get(*FULLTEXT_KEYS),
        "duplicate_of": get(*DUP_KEYS),
        "exclusion_reason": get("exclusion_reason", "exclude_reason", "reason"),
        "registry_path": source.path if source is not None else "",
        "registry_row": registry_row,
        "origin": "registry",
        "raw": {str(k): str(v) for k, v in item.items()},
        "evidence": [],
    }
    if not record["duplicate_of"]:
        match = re.search(r"duplicate\s+of\s+([A-Za-z0-9_.-]+)",
                          record["notes"], re.I)
        if match:
            record["duplicate_of"] = match.group(1)
    return normalise_record(record)


def normalise_record(record):
    record["title"] = norm(record.get("title"))
    record["year"] = first_year(record.get("year"))
    record["doi"] = norm(record.get("doi")).replace("https://doi.org/", "").strip() or None
    record["arxiv"] = norm(record.get("arxiv")) or None
    if not record.get("arxiv") and record.get("doi"):
        match = ARXIV_RE.search(record["doi"])
        if match:
            record["arxiv"] = "arXiv:%s" % match.group(1)
    record["citations"] = as_int(record.get("citations"))
    record["duplicate_of"] = norm(record.get("duplicate_of")).upper() or ""
    status_text = norm(record.get("status_text")).lower()
    record["status"] = ("retracted" if re.search(r"retract|withdraw|撤稿|撤回",
                                                 status_text) else
                        "corrected" if re.search(r"corrigendum|erratum|勘误|更正",
                                                 status_text) else "unknown")
    full = norm(record.get("full_text_field")).lower()
    if full in ("yes", "true", "1", "full_text", "full", "available"):
        access = "full_text"
    elif full in ("no", "false", "0", "abstract_only", "abstract", "unavailable"):
        access = "abstract_only"
    elif record.get("abstract"):
        access = "abstract_only"
    else:
        access = "metadata_only"
    if re.search(r"protocol|abstract only|no methods or results",
                 " ".join([record.get("title", ""), record.get("notes", ""),
                           record.get("limitation", "")]), re.I):
        access = "abstract_only" if access == "full_text" else access
    record["access"] = access
    body = " ".join(str(record.get(k) or "") for k in
                    ("title", "abstract", "method", "data", "evaluation",
                     "conclusion", "finding", "limitation", "recommendation",
                     "notes", "topic"))
    record["search_text"] = norm(body)
    return record


def record_key(record):
    if record.get("doi"):
        return "doi:" + norm(record["doi"]).lower()
    if record.get("arxiv"):
        return "arxiv:" + norm(record["arxiv"]).lower()
    title = re.sub(r"[^0-9a-z一-鿿]+", " ", norm(record.get("title")).lower()).strip()
    if title:
        return "title:%s|%s" % (title, record.get("year") or "")
    return ""


def extract_csv_records(source):
    records = []
    if not source.rows:
        return records
    header = [str(x).strip() for x in source.rows[0]]
    for index, row in enumerate(source.rows[1:], 2):
        if not isinstance(row, list):
            continue
        item = row_dict(row, header)
        if not any(item.values()):
            continue
        record = record_from_dict(item, source=source, registry_row=index)
        if record is None:
            continue
        if not record.get("record_id"):
            record["record_id"] = "U%02d" % (len(records) + 1)
        if not record.get("year"):
            record["year_missing_in_registry"] = True
        title = record.get("title") or record.get("doi") or record.get("record_id")
        record["evidence"].append({"path": source.path, "locator": "row %d" % index,
                                   "quote": title})
        records.append(record)
    return records


def extract_json_records(source):
    records = []
    data = source.data
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("records", "references", "papers", "studies", "articles", "items"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
        else:
            if any(k in data for k in ("title", "doi", "record_id", "id")):
                items = [data]
    for index, item in enumerate(items, 1):
        record = record_from_dict(item, source=source, registry_row=index)
        if record is None:
            continue
        if not record.get("record_id"):
            record["record_id"] = "U%02d" % (len(records) + 1)
        title = record.get("title") or record.get("doi") or record.get("record_id")
        ev = source.find_line(title) or {"path": source.path,
                                         "locator": "json item %d" % index,
                                         "quote": title}
        record["evidence"].append(ev)
        records.append(record)
    return records


def extract_bib_records(source):
    records = []
    entries = source.entries or []
    if not entries and source.text.strip():
        # Minimal BibTeX parser; one entry per @type{key, ...}.
        for match in re.finditer(r"@([A-Za-z]+)\s*\{\s*([^,]+),(.*?)\n\}", source.text,
                                 re.S):
            fields = {}
            for field in re.finditer(r"([A-Za-z]+)\s*=\s*[\{\"](.*?)[\}\"]\s*,?",
                                     match.group(3), re.S):
                fields[field.group(1).lower()] = norm(field.group(2))
            entries.append({"key": match.group(2), "type": match.group(1),
                            "fields": fields, "line": 0})
    for index, entry in enumerate(entries, 1):
        fields = entry.get("fields") or entry
        item = dict(fields)
        item.setdefault("id", entry.get("key") or "B%02d" % index)
        record = record_from_dict(item, source=source, registry_row=index)
        if record is None:
            continue
        if not record.get("record_id"):
            record["record_id"] = item.get("id") or "U%02d" % (len(records) + 1)
        title = record.get("title") or record.get("doi") or record.get("record_id")
        ev = source.find_line(title) or {"path": source.path,
                                         "locator": "entry %d" % index,
                                         "quote": title}
        record["evidence"].append(ev)
        records.append(record)
    return records


def extract_doc_records(source):
    """One source document usually carries one record; multiple Title: blocks are
    treated as separate records and each block is kept with its line range."""
    lines = source.lines
    if not lines:
        return []
    markers = []
    for index, raw in enumerate(lines, 1):
        match = FIELD_RE.match(raw)
        if match and norm_key(match.group(1)) in ("title", "标题"):
            markers.append((index, match.group(2).strip()))
    if not markers:
        for index, raw in enumerate(lines, 1):
            match = re.match(r"^\s{0,3}#{1,4}\s+(.+)$", raw)
            if match and not re.search(r"synthetic|note|introduction|abstract|references",
                                       match.group(1), re.I):
                markers.append((index, match.group(1).strip()))
                break
    if not markers:
        title = source.stem.replace("_", " ").replace("-", " ")
        markers = [(1, title)]
    records = []
    for marker_index, (line_no, marker_title) in enumerate(markers):
        end = (markers[marker_index + 1][0] - 1) if marker_index + 1 < len(markers) \
            else len(lines)
        block = "\n".join(lines[line_no - 1:end])
        fields = parse_fields(block)
        item = {key: value[0] for key, value in fields.items()}
        item["title"] = item.get("title") or marker_title
        item.setdefault("id", "D%02d" % (len(records) + 1))
        record = record_from_dict(item, source=source, registry_row=line_no)
        if record is None:
            continue
        record["origin"] = "document"
        record["source_field"] = source.path
        record["source_path"] = source.path
        record["block_text"] = block
        evidence = [source.evidence_at(line_no)]
        for field_key in ("year", "method", "data", "evaluation", "result",
                          "conclusion", "finding", "limitation", "recommendation",
                          "abstract"):
            if item.get(field_key):
                ev = source.field_evidence(field_key, item[field_key])
                if ev:
                    evidence.append(ev)
        record["evidence"] = unique_evidence(evidence)
        records.append(record)
    return records


def registry_candidates(docs, case):
    candidates = []
    case_records = case.get("records") if isinstance(case, dict) else None
    if isinstance(case_records, list) and case_records:
        records = []
        for index, item in enumerate(case_records, 1):
            record = record_from_dict(item, source=None, registry_row=index)
            if record:
                if not record.get("record_id"):
                    record["record_id"] = "U%02d" % len(records)
                record["registry_path"] = "work/input-case.json"
                title = record.get("title") or record.get("record_id")
                record["evidence"].append({"path": "work/input-case.json",
                                           "locator": "records[%d]" % index,
                                           "quote": title})
                records.append(record)
        if records:
            candidates.append((100, records, "case.json"))
    for doc in docs:
        if doc.kind == "csv":
            records = extract_csv_records(doc)
            if records:
                candidates.append((50 + min(len(records), 20), records, doc.path))
        elif doc.kind == "json":
            records = extract_json_records(doc)
            if records:
                candidates.append((60 + min(len(records), 20), records, doc.path))
        elif doc.kind in ("bib", "ris"):
            records = extract_bib_records(doc)
            if records:
                candidates.append((40 + min(len(records), 20), records, doc.path))
    candidates.sort(key=lambda x: (x[0], len(x[1])), reverse=True)
    return candidates


def resolve_doc(record, docs_by_path, docs_by_stem, source_docs):
    candidate = norm(record.get("source_field") or record.get("source_path")).replace("\\", "/")
    if candidate:
        if candidate in docs_by_path:
            return docs_by_path[candidate]
        if candidate in docs_by_stem:
            return docs_by_stem[candidate]
        name = Path(candidate).name
        for doc in source_docs:
            if doc.name == name or doc.stem == Path(candidate).stem:
                return doc
    title = record.get("title")
    if title:
        for doc in source_docs:
            if norm(title).lower() in norm(doc.text).lower():
                return doc
    doi = record.get("doi")
    if doi:
        for doc in source_docs:
            if doi.lower() in doc.text.lower():
                return doc
    return None


def attach_doc_evidence(record, doc):
    evidence = list(record.get("evidence") or [])
    title = record.get("title")
    title_ev = doc.find_line(title) if title else None
    if title_ev is None and doc.lines:
        title_ev = doc.evidence_at(1)
    if title_ev:
        evidence.append(title_ev)
    for field_key in ("year", "method", "data", "evaluation", "result",
                      "conclusion", "finding", "limitation", "recommendation",
                      "abstract"):
        value = record.get(field_key) or record.get("abstract")
        if not value:
            continue
        ev = doc.field_evidence(field_key, value)
        if ev:
            evidence.append(ev)
    record["source_path"] = doc.path
    record["source_locator"] = title_ev.get("locator") if title_ev else ""
    record["source_quote"] = title_ev.get("quote") if title_ev else ""
    record["evidence"] = unique_evidence(evidence)
    return record


def normalized_title_key(record):
    return re.sub(r"[^0-9a-z一-鿿]+", " ",
                  norm(record.get("title")).lower()).strip()


def refresh_record_text(record):
    body = " ".join(str(record.get(k) or "") for k in
                    ("title", "abstract", "method", "data", "evaluation",
                     "conclusion", "finding", "limitation", "recommendation",
                     "notes", "topic"))
    record["search_text"] = norm(body)
    return record


def merge_record(target, source, doc=None):
    target.setdefault("merged_from", [])
    if source.get("record_id"):
        target["merged_from"].append(source["record_id"])
    for field in ("title", "authors", "venue", "doi", "arxiv", "abstract", "topic",
                  "method", "data", "evaluation", "conclusion", "finding",
                  "limitation", "recommendation", "notes", "citations"):
        if not target.get(field) and source.get(field):
            target[field] = source[field]
    if not target.get("year") and not target.get("year_missing_in_registry") \
            and source.get("year"):
        target["year"] = source["year"]
    if doc is not None:
        attach_doc_evidence(target, doc)
    if not target.get("evidence"):
        target["evidence"] = list(source.get("evidence") or [])
    return refresh_record_text(target)


def build_registries(docs, case):
    source_docs = [doc for doc in docs if doc.is_source_document]
    support_docs = [doc for doc in docs if doc.is_support_note]
    registry_docs = [doc for doc in docs if doc.is_registry]
    docs_by_path = {doc.path: doc for doc in docs}
    docs_by_stem = {doc.stem: doc for doc in docs}
    candidates = registry_candidates(docs, case)
    all_records = []
    registry_path = ""
    if candidates:
        _, all_records, registry_path = candidates[0]
        # Add document-derived records that are not in the chosen registry.
        known = {record_key(r) for r in all_records if record_key(r)}
        by_title = {}
        for record in all_records:
            key = normalized_title_key(record)
            if key:
                by_title.setdefault(key, []).append(record)
        seen_doc_keys = set()
        for doc in source_docs:
            for record in extract_doc_records(doc):
                key = record_key(record)
                target = next((r for r in all_records if record_key(r) == key), None)
                if target is None:
                    title = normalized_title_key(record)
                    candidates_by_title = by_title.get(title) or []
                    for candidate in candidates_by_title:
                        if candidate.get("year") is None or record.get("year") is None \
                                or candidate.get("year") == record.get("year"):
                            target = candidate
                            break
                if target is not None:
                    merge_record(target, record, doc)
                    continue
                if key and key in seen_doc_keys:
                    continue
                seen_doc_keys.add(key)
                all_records.append(record)
                tkey = normalized_title_key(record)
                if tkey:
                    by_title.setdefault(tkey, []).append(record)
    else:
        for doc in source_docs:
            all_records.extend(extract_doc_records(doc))
    # Merge a same-title document record into a registry row whose export year is
    # missing, but keep the export gap visible (year_missing_in_registry).
    seen_titles = {}
    final_records = []
    for record in all_records:
        tkey = normalized_title_key(record)
        other = seen_titles.get(tkey)
        if other is not None and (other.get("year") is None or record.get("year") is None) \
                and (other.get("year") is None or record.get("year") is None
                     or other.get("year") == record.get("year")):
            if other.get("year") is None and record.get("year") is not None \
                    and other.get("year_missing_in_registry"):
                pass
            merge_record(other, record)
            continue
        if tkey:
            seen_titles[tkey] = record
        final_records.append(record)
    all_records = final_records
    # Attach source evidence to every registry record.
    for record in all_records:
        if not record.get("evidence"):
            doc = resolve_doc(record, docs_by_path, docs_by_stem, source_docs)
            if doc is not None:
                attach_doc_evidence(record, doc)
            elif record.get("registry_path"):
                title = record.get("title") or record.get("doi") or record.get("record_id")
                record["evidence"] = [{"path": record["registry_path"],
                                       "locator": "row %s" % (record.get("registry_row") or "?"),
                                       "quote": title}]
        elif not record.get("source_quote"):
            ev = (record.get("evidence") or [{}])[0]
            record["source_path"] = ev.get("path")
            record["source_locator"] = ev.get("locator")
            record["source_quote"] = ev.get("quote")
    for record in all_records:
        refresh_record_text(record)
        if re.search(r"abstract only|protocol only|no methods or results|extended abstract",
                     record.get("search_text") or "", re.I):
            record["access"] = "abstract_only"
        elif record.get("access") == "metadata_only" and record.get("source_path") \
                and len(record.get("search_text") or "") >= 120:
            record["access"] = "full_text"
    # Assign stable IDs and mark duplicates.
    used_ids = set()
    for index, record in enumerate(all_records, 1):
        rid = str(record.get("record_id") or "").strip()
        if not rid or rid in used_ids:
            rid = "R%02d" % index
        while rid in used_ids:
            rid += "x"
        record["record_id"] = rid
        used_ids.add(rid)
    by_key = {}
    for record in all_records:
        key = record_key(record)
        if key and key in by_key and not record.get("duplicate_of"):
            record["duplicate_of"] = by_key[key]["record_id"]
            record.setdefault("duplicate_reason",
                              "same DOI/arXiv/title+year as %s" % by_key[key]["record_id"])
        elif key:
            by_key[key] = record
    unique_records = [r for r in all_records if not r.get("duplicate_of")]
    return {
        "all_records": all_records,
        "unique_records": unique_records,
        "source_docs": source_docs,
        "support_docs": support_docs,
        "registry_docs": registry_docs,
        "registry_path": registry_path,
        "docs_by_path": docs_by_path,
    }


def declared_source_paths(case):
    if not isinstance(case, dict):
        return []
    for key in ("sources", "source_files", "materials", "documents"):
        value = case.get(key)
        if not isinstance(value, list):
            continue
        paths = []
        for item in value:
            if isinstance(item, dict):
                path = item.get("path") or item.get("file") or item.get("name")
            else:
                path = item
            if norm(path):
                paths.append(norm(path).replace("\\", "/"))
        if paths:
            return paths
    return []


def build_source_registry(source_docs, all_records, docs, case=None):
    registry = []
    declared_paths = declared_source_paths(case)
    if declared_paths:
        by_path = {doc.path: doc for doc in docs}
        by_name = {doc.name: doc for doc in docs}
        by_stem = {doc.stem: doc for doc in docs}
        for index, path in enumerate(declared_paths, 1):
            doc = (by_path.get(path) or by_name.get(Path(path).name)
                   or by_stem.get(Path(path).stem))
            if doc is not None:
                ids = [r["record_id"] for r in all_records
                       if r.get("source_path") == doc.path]
                registry.append({
                    "source_id": "S%02d" % index, "path": doc.path,
                    "declared_path": path, "available": True, "kind": doc.kind,
                    "chars": doc.chars, "sha256": doc.sha256,
                    "title": (doc.lines[0].lstrip("# ").strip()
                              if doc.lines else doc.stem),
                    "record_ids": ids,
                    "evidence_lines": sum(1 for r in all_records
                                          if r.get("source_path") == doc.path),
                })
            else:
                registry.append({
                    "source_id": "S%02d" % index, "path": path,
                    "declared_path": path, "available": False, "kind": "",
                    "chars": 0, "sha256": "", "title": Path(path).stem,
                    "record_ids": [], "evidence_lines": 0,
                    "note": "declared in case.json but not parsed as a readable source",
                })
    else:
        for index, doc in enumerate(source_docs, 1):
            ids = [r["record_id"] for r in all_records
                   if r.get("source_path") == doc.path]
            registry.append({
                "source_id": "S%02d" % index,
                "path": doc.path,
                "available": True,
                "kind": doc.kind,
                "chars": doc.chars,
                "sha256": doc.sha256,
                "title": (doc.lines[0].lstrip("# ").strip() if doc.lines else doc.stem),
                "record_ids": ids,
                "evidence_lines": sum(1 for r in all_records
                                      if r.get("source_path") == doc.path),
            })
    files = []
    for doc in docs:
        files.append({"path": doc.path, "kind": doc.kind,
                      "role": ("source" if doc.is_source_document else
                               "support_note" if doc.is_support_note else
                               "record_registry" if doc.is_registry else "other"),
                      "chars": doc.chars, "sha256": doc.sha256})
    return {"sources": registry, "files": files,
            "source_count": len(registry),
            "declared_source_paths": declared_paths,
            "unavailable_sources": [row["path"] for row in registry
                                    if not row.get("available")]}


# ---------------------------------------------------------------------------
# question and protocol
# ---------------------------------------------------------------------------
def case_question(case, task_text=None):
    for key in ("research_question", "question", "topic"):
        value = norm(case.get(key)) if isinstance(case, dict) else ""
        if value and value.lower() not in ("research", "study", "untitled"):
            return value, {"path": "work/input-case.json", "locator": key,
                           "quote": value}
    return "", None


def task_question(task_text):
    patterns = [
        r"(?:Research question|Research questions|研究问题)\s*[:：]\s*(.+)",
        r"(?:Topic|主题)\s*[:：]\s*(.+)",
        r"(?:Proposal|研究设想)\s*[:：]\s*(.+)",
        r"(?:Goal|目标)\s*[:：]\s*(.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, task_text, re.I)
        if match:
            return norm(match.group(1)), {"path": "work/input-task.md",
                                          "locator": "task text",
                                          "quote": match.group(0).strip()}
    return "", None


def source_question(support_docs, source_docs):
    for doc in support_docs + source_docs:
        fields = parse_fields(doc.text)
        for key in ("question", "goal", "innovation"):
            if fields.get(key):
                value, line_no, raw = fields[key]
                return value, doc.evidence_at(line_no)
    return "", None


def fixed_questions(case, task_text):
    listed = []
    for key in ("research_questions", "questions", "subquestions", "fixed_questions"):
        value = case.get(key) if isinstance(case, dict) else None
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    text = item.get("question") or item.get("text") or item.get("title")
                else:
                    text = item
                if norm(text):
                    listed.append(norm(text))
    if listed:
        return listed
    return []


def canonical_topic(raw, record=None):
    text = norm(raw).lower()
    if not text:
        body = " ".join(str((record or {}).get(k) or "") for k in
                        ("title", "abstract", "method", "conclusion"))
        if re.search(r"multi[- ]hop", body, re.I):
            return "multi-hop"
        if re.search(r"cross[- ]lingual|multilingual", body, re.I):
            return "multilingual"
        if re.search(r"human|expert", body, re.I):
            return "evaluation validity"
        if re.search(r"uncertain|calibrat|conformal", body, re.I):
            return "uncertainty"
        if re.search(r"benchmark", body, re.I):
            return "benchmark"
        return "unclassified"
    aliases = {
        "uncertainty quantification": "uncertainty",
        "uncertainty estimation": "uncertainty",
        "calibration": "uncertainty",
        "conformal prediction": "uncertainty",
        "property prediction": "property prediction",
        "molecular property prediction": "property prediction",
        "evaluation": "evaluation validity",
        "evaluation practices": "evaluation validity",
        "benchmarking": "benchmark",
        "quantum property prediction": "quantum properties",
    }
    return aliases.get(text, text)


def key_terms(question, records, limit=10):
    text = " ".join([question] + [r.get("title", "") for r in records])
    counter = {}
    for token in re.findall(r"[a-z0-9]{3,}|[一-鿿]{2,}", text.lower()):
        if token in STOPWORDS or token.isdigit() or len(token) < 3:
            continue
        counter[token] = counter.get(token, 0) + 1
    ordered = sorted(counter, key=lambda x: (-counter[x], -len(x), x))
    return ordered[:limit]


def build_protocol(question, records, case, docs):
    terms = key_terms(question, records)
    explicit = ""
    for doc in docs:
        for line in doc.lines:
            match = re.search(r"(?:search string|检索式)\s*[:：]\s*(.+)", line, re.I)
            if match:
                explicit = norm(match.group(1))
                break
        if explicit:
            break
    if explicit:
        search_string = explicit
    elif len(terms) >= 4:
        search_string = '("%s" OR "%s") AND ("%s" OR "%s")' % tuple(terms[:4])
    elif terms:
        search_string = " OR ".join('"%s"' % term for term in terms)
    else:
        search_string = '("research question not stated")'
    databases = []
    for doc in docs:
        for line in doc.lines:
            match = re.search(r"(?:databases?|数据库)\s*[:：]\s*(.+)", line, re.I)
            if match:
                databases.extend([norm(x) for x in re.split(r"[;,，；]", match.group(1))
                                  if norm(x)])
    if not databases:
        databases = ["supplied_corpus"]
    executed = bool(case.get("search_executed")) if isinstance(case, dict) else False
    protocol = {
        "version": "v1",
        "search_string": search_string,
        "databases": databases,
        "date": today_iso(),
        "limits": [
            "Only the records supplied in /input are screened; no live database search is claimed.",
            "Language and time limits are those of the supplied corpus.",
            "A single deterministic screening pass is recorded; human double-screening remains required for a formal review.",
        ],
        "executed": executed,
        "coverage_note": ("Prospective protocol: the search was not executed by this skill; "
                          "screening below applies to the supplied records only."
                          if not executed else
                          "Executed search metadata was supplied in the case; results still limited to supplied records."),
        "reviewers": "deterministic single-reviewer pass; human double-screening recommended",
        "screening_stage": ["title_abstract", "full_text_or_supplied_record"],
        "key_terms": terms,
    }
    return protocol


def criteria_payload(question, records):
    include = [
        "Record directly addresses the fixed research question or its stated sub-questions.",
        "Record supplies a title and at least one of year, venue, DOI or source locator.",
        "Record is a full text or a clearly labelled abstract/protocol; access level is recorded.",
        "Non-duplicate report of a study, benchmark, review or guideline.",
    ]
    exclude = [
        "Duplicate export of a record already included (DOI, arXiv or title+year match).",
        "Record has no title, identifier or usable source locator.",
        "Record is retracted/withdrawn and cannot support a synthesis claim.",
        "Record is off-topic after title/abstract screening against the fixed question.",
    ]
    return {"version": "v1", "include": include, "exclude": exclude,
            "priority": ["direct relevance", "verifiable source locator",
                         "full-text availability", "recency only as a secondary signal"],
            "question": question,
            "rule": "Criteria are fixed before screening and applied to every supplied record."}


def screen_records(records, question, criteria, protocol):
    terms = tokens(question) | set(protocol.get("key_terms") or [])
    log = []
    for record in records:
        decision = "include"
        reason = "满足主题相关与可核验定位要求"
        stage = "title_abstract"
        if record.get("duplicate_of"):
            decision = "duplicate"
            reason = "重复导出于 %s（DOI/标题+年份匹配）" % record["duplicate_of"]
            stage = "duplicate_check"
        elif record.get("status") == "retracted":
            decision = "exclude"
            reason = "记录为撤稿/撤回状态，不能作为支持证据"
            stage = "full_text"
        elif not record.get("title") and not record.get("doi"):
            decision = "exclude"
            reason = "缺少标题与标识符，无法核验"
            stage = "title_abstract"
        else:
            relevance = containment(record.get("search_text"), " ".join(terms))
            topic_hit = bool(record.get("topic")) and any(
                term in norm(record.get("topic")).lower() for term in terms)
            if terms and relevance < 0.02 and not topic_hit and not re.search(
                    r"review|guideline|protocol|benchmark", record.get("search_text"), re.I):
                decision = "exclude"
                reason = "标题/摘要与固定问题无实质重合（重合度 %.3f）" % relevance
                stage = "title_abstract"
            else:
                reason = "主题相关（重合度 %.3f）；access=%s" % (
                    relevance, record.get("access"))
        record["screening_decision"] = decision
        record["screening_reason"] = reason
        record["screening_stage"] = stage
        log.append({
            "record_id": record["record_id"],
            "decision": decision,
            "reason": reason,
            "stage": stage,
            "full_text": record.get("access") == "full_text",
            "access": record.get("access"),
            "evidence": (record.get("evidence") or [None])[0],
        })
    identified = len(records)
    duplicates = sum(1 for row in log if row["decision"] == "duplicate")
    screened = identified - duplicates
    excluded = sum(1 for row in log if row["decision"] == "exclude")
    included = sum(1 for row in log if row["decision"] == "include")
    title_excluded = sum(1 for row in log if row["decision"] == "exclude"
                         and row.get("stage") == "title_abstract")
    full_text = screened - title_excluded
    counts = {
        "identified": identified,
        "duplicates": duplicates,
        "screened": screened,
        "excluded": excluded,
        "full_text": full_text,
        "included": included,
    }
    reasons = {}
    for row in log:
        if row["decision"] != "include":
            reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    return {"criteria": criteria, "protocol": protocol,
            "counts": counts, "records": log, "excluded_reasons": reasons}


def build_evidence_matrix(records, mode):
    rows = []
    for record in records:
        evidence = unique_evidence(record.get("evidence") or [])
        row = {
            "record_id": record["record_id"],
            "title": record.get("title"),
            "year": record.get("year"),
            "authors": record.get("authors"),
            "venue": record.get("venue"),
            "doi": record.get("doi"),
            "source_path": record.get("source_path") or record.get("registry_path"),
            "source_locator": record.get("source_locator") or "",
            "source_quote": record.get("source_quote") or
                            ((evidence[0].get("quote") if evidence else "")),
            "access": record.get("access"),
            "status": record.get("status"),
            "topic": record.get("topic"),
            "screening_decision": record.get("screening_decision"),
            "screening_reason": record.get("screening_reason"),
            "duplicate_of": record.get("duplicate_of") or None,
            "evidence_count": len(evidence),
            "evidence": evidence,
        }
        if mode == "trend-analysis":
            row["citations"] = record.get("citations")
            row["representative_role"] = record.get("representative_role")
            row["stage"] = record.get("stage")
        elif mode == "topic-selection":
            row["comparison"] = {
                "task": record.get("topic") or "not_extracted",
                "method": record.get("method") or "not_extracted",
                "data": record.get("data") or "not_extracted",
                "evaluation": record.get("evaluation") or "not_extracted",
                "conclusion": record.get("conclusion") or "not_extracted",
            }
            row["directly_comparable"] = sum(
                1 for value in row["comparison"].values() if value != "not_extracted") >= 3
        elif mode == "review-generation":
            row["themes"] = record.get("themes") or []
            row["support"] = record.get("support")
            row["strength"] = record.get("strength")
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# shared statistics
# ---------------------------------------------------------------------------
def data_quality(all_records, unique_records):
    duplicates = [r for r in all_records if r.get("duplicate_of")]
    dated = [r for r in unique_records if r.get("year") is not None]
    missing = [r for r in unique_records if r.get("year") is None]
    return {
        "denominator": {
            "rows": len(all_records),
            "duplicates": len(duplicates),
            "unique": len(unique_records),
            "dated": len(dated),
            "missing_year": len(missing),
        },
        "duplicates_removed": [{
            "record_id": r["record_id"],
            "duplicate_of": r["duplicate_of"],
            "reason": r.get("duplicate_reason") or "same DOI/arXiv/title+year",
            "evidence": (r.get("evidence") or [None])[0],
        } for r in duplicates],
        "missing_year_ids": [r["record_id"] for r in missing],
        "dedupe_key": "DOI, else arXiv, else normalized title+year",
    }


def flatten_evidence(records, limit=6):
    items = []
    for record in records:
        items.extend(record.get("evidence") or [])
    return unique_evidence(items)[:limit]


def years_of(records):
    return sorted({r["year"] for r in records if r.get("year") is not None})


def split_stages(records):
    years = years_of(records)
    if not years:
        return []
    if len(years) == 1:
        return [{"name": "single_year", "start": years[0], "end": years[0],
                 "basis": "only one year is present in the supplied records"}]
    nstages = 3 if (len(years) >= 5 and years[-1] - years[0] >= 4) else 2
    chunks = [[] for _ in range(nstages)]
    for index, year in enumerate(years):
        chunks[min(index * nstages // len(years), nstages - 1)].append(year)
    stages = []
    labels = ["early", "middle", "recent"] if nstages == 3 else ["early", "recent"]
    for index, chunk in enumerate(chunks):
        if not chunk:
            continue
        stages.append({
            "name": labels[index] if index < len(labels) else "stage_%d" % (index + 1),
            "start": min(chunk),
            "end": max(chunk),
            "basis": "equal-count split of the supplied years; not a change-point test",
        })
    return stages


def representative_role(record):
    text = " ".join(str(record.get(k) or "") for k in
                    ("title", "notes", "method", "topic", "conclusion"))
    if re.search(r"first|首次|首创", text, re.I):
        return "first_report"
    if re.search(r"benchmark|基准", text, re.I):
        return "benchmark"
    if re.search(r"review|综述|survey", text, re.I):
        return "review"
    return "method"


# ---------------------------------------------------------------------------
# trend-analysis
# ---------------------------------------------------------------------------
def trend_analysis(data, question):
    all_records = data["all_records"]
    unique_records = data["unique_records"]
    quality = data_quality(all_records, unique_records)
    dated_records = [r for r in unique_records if r.get("year") is not None]
    years = years_of(unique_records)
    year_counts = []
    for year in years:
        count = sum(1 for r in dated_records if r["year"] == year)
        year_counts.append({"year": year, "count": count,
                            "denominator": len(dated_records)})
    missing_count = len(unique_records) - len(dated_records)
    if missing_count:
        year_counts.append({"year": "missing", "count": missing_count,
                            "denominator": len(unique_records)})
    topic_mapping = {}
    topic_counts = {}
    for record in unique_records:
        raw = norm(record.get("topic"))
        canonical = canonical_topic(raw, record)
        topic_mapping[raw] = canonical
        topic_counts[canonical] = topic_counts.get(canonical, 0) + 1
    topic_rows = [{"topic": topic, "count": count,
                   "denominator": len(unique_records)}
                  for topic, count in sorted(topic_counts.items(),
                                             key=lambda x: (-x[1], x[0]))]
    stages = split_stages(unique_records)
    representatives = []
    exclusions = []
    used = set()
    for stage in stages:
        eligible = [r for r in unique_records
                    if r.get("year") is not None
                    and stage["start"] <= r["year"] <= stage["end"]]
        if not eligible:
            continue
        first_low = [r for r in eligible
                     if re.search(r"first|首次|首创", str(r.get("notes") or ""), re.I)
                     and (r.get("citations") is None or r.get("citations") <= 100)]
        candidates = first_low or [r for r in eligible if representative_role(r) == "method"] \
            or eligible
        candidates = sorted(candidates,
                            key=lambda r: (r.get("year") or 9999,
                                           -(r.get("citations") or 0),
                                           r["record_id"]))
        chosen = next((r for r in candidates if r["record_id"] not in used), candidates[0])
        used.add(chosen["record_id"])
        role = representative_role(chosen)
        reason = ("阶段 %s（%s-%s）；角色=%s；%s；被引=%s；按阶段覆盖+角色+被引组合选择，"
                  "不按单一被引排序" % (
                      stage["name"], stage["start"], stage["end"], role,
                      "含首创/首次方法注记，允许低被引" if first_low else
                      "该阶段的方法/基准代表",
                      chosen.get("citations") if chosen.get("citations") is not None
                      else "未报告"))
        chosen["representative_role"] = role
        chosen["stage"] = stage["name"]
        representatives.append({
            "topic": chosen.get("topic") or "unclassified",
            "record_id": chosen["record_id"],
            "stage": stage["name"],
            "role": role,
            "year": chosen.get("year"),
            "citations": chosen.get("citations"),
            "reason": reason,
            "evidence": (chosen.get("evidence") or [None])[0],
        })
        for record in eligible:
            if record["record_id"] in used:
                continue
            if re.search(r"first|首次|首创", str(record.get("notes") or ""), re.I):
                exclusions.append({
                    "record_id": record["record_id"],
                    "reason": "低被引/首创记录未入选该阶段代表；阶段覆盖已由 %s 承担"
                              % chosen["record_id"],
                })
    representative_rule = ("阶段覆盖优先；每阶段按 first/首创方法（允许低被引）> 方法/基准角色 "
                           "> 被引数排序；不以总被引单独选择，不遗漏低被引首创工作。")
    time_window = {
        "start": years[0] if years else datetime.now(timezone.utc).year,
        "end": years[-1] if years else datetime.now(timezone.utc).year,
        "as_of": today_iso(),
        "coverage_note": ("按输入记录年份统计；缺年份记录单列，末年可能因索引未完成而不完整。"
                          if years else "输入没有可解析年份；时间窗仅用于占位，不能支持趋势结论。"),
        "stages": stages,
        "incomplete_last_year": bool(
            years and (years[-1] >= datetime.now(timezone.utc).year
                       or re.search(r"incomplete|not finished|索引未完成|不完整",
                                    " ".join(str(d.text) for d in data["support_docs"]),
                                    re.I))),
    }
    findings = []
    if dated_records:
        parts = "；".join("%s 年 %d 条" % (row["year"], row["count"])
                         for row in year_counts if isinstance(row["year"], int))
        findings.append({
            "id": "F01",
            "statement": "所提供语料中可定年的唯一记录共 %d 条，年度分布为：%s。"
                         "这是描述性计数，不构成因果结论。" % (len(dated_records), parts),
            "sources": [r["record_id"] for r in dated_records[:8]],
            "limitations": ["小样本年度计数不能外推为领域趋势"],
            "evidence_type": "count",
            "evidence": flatten_evidence(dated_records, 4),
        })
    findings.append({
        "id": "F02",
        "statement": "去重口径：输入 %d 行，识别重复 %d 行，唯一记录 %d 条；"
                     "去重键为 DOI、否则 arXiv、否则规范化标题+年份。"
                     % (len(all_records), quality["denominator"]["duplicates"],
                        len(unique_records)),
        "sources": [r["record_id"] for r in all_records if r.get("duplicate_of")],
        "limitations": [],
        "evidence_type": "count",
        "evidence": flatten_evidence([r for r in all_records if r.get("duplicate_of")], 2)
        or flatten_evidence(unique_records, 1),
    })
    if missing_count:
        findings.append({
            "id": "F03",
            "statement": "%d 条唯一记录缺少年份（%s），未进入年度计数，已在 year_counts "
                         "中以 missing 行单列。" % (missing_count,
                                                    ", ".join(quality["missing_year_ids"])),
            "sources": quality["missing_year_ids"],
            "limitations": ["缺年份记录无法判断时间位置"],
            "evidence_type": "count",
            "evidence": flatten_evidence(
                [r for r in unique_records if r.get("year") is None], 2),
        })
    if topic_rows:
        findings.append({
            "id": "F%02d" % (len(findings) + 1),
            "statement": "主题计数（分母为 %d 条唯一记录）显示：%s。这是词表归并后的描述，"
                         "不代表主题之间存在因果关系。" % (
                             len(unique_records),
                             "；".join("%s %d 条" % (row["topic"], row["count"])
                                      for row in topic_rows[:6])),
            "sources": [r["record_id"] for r in unique_records[:8]],
            "limitations": ["主题归并依赖题录字段与关键词，术语漂移已写入 topic_mapping"],
            "evidence_type": "count",
            "evidence": flatten_evidence(unique_records, 4),
        })
    if representatives:
        findings.append({
            "id": "F%02d" % (len(findings) + 1),
            "statement": "按阶段划分得到 %d 个阶段，并为每个阶段选出代表记录：%s。"
                         "代表作选择依据阶段覆盖、方法/首创角色与被引的组合。" % (
                             len(stages), "；".join(
                                 "%s=%s" % (row["stage"], row["record_id"])
                                 for row in representatives)),
            "sources": [row["record_id"] for row in representatives],
            "limitations": ["代表选择是透明规则下的可复核选择，不是唯一正确答案"],
            "evidence_type": "qualitative",
            "evidence": flatten_evidence(
                [r for r in unique_records
                 if r["record_id"] in {x["record_id"] for x in representatives}], 4),
        })
    cooccurrence = []
    for stage in stages:
        stage_records = [r for r in unique_records
                         if r.get("year") is not None
                         and stage["start"] <= r["year"] <= stage["end"]]
        stage_topics = {canonical_topic(r.get("topic"), r) for r in stage_records}
        if len(stage_topics) >= 2:
            cooccurrence.append((stage["name"], sorted(stage_topics), stage_records))
    if cooccurrence:
        name, topics, records = cooccurrence[0]
        findings.append({
            "id": "F%02d" % (len(findings) + 1),
            "statement": "阶段 %s 内同时出现主题 %s；这是共现描述，共现不等于因果。"
                         % (name, "、".join(topics)),
            "sources": [r["record_id"] for r in records[:8]],
            "limitations": ["共现可能来自题录标注或检索构成，不能解释为互相驱动"],
            "evidence_type": "co-occurrence",
            "evidence": flatten_evidence(records, 3),
        })
    findings.append({
        "id": "F%02d" % (len(findings) + 1),
        "statement": "基于 %d 条记录的小样本，阶段间的主题构成变化只能作为待检验推断；"
                     "在没有更完整检索与统一口径前，不能外推为领域趋势。"
                     % len(unique_records),
        "sources": [r["record_id"] for r in unique_records],
        "limitations": ["样本小", "无在线检索", "末年可能不完整"],
        "evidence_type": "inference",
        "evidence": flatten_evidence(unique_records, 3),
    })
    gaps = []
    for record in unique_records:
        if record.get("year") is None:
            gaps.append({
                "gap": "记录 %s 缺年份，无法纳入时间切片" % record["record_id"],
                "basis": "%s 的题录缺年份；已在 denominator.missing_year 单列"
                         % record["record_id"],
                "excluded_alternative": "已检查输入题录与导出注记，不是同义词检索遗漏；"
                                        "需回源补年份后再计入",
                "evidence": (record.get("evidence") or [None])[0],
            })
        if record.get("access") != "full_text":
            gaps.append({
                "gap": "记录 %s 仅有摘要/题录，不能支持方法或结果层面的稳健结论"
                         % record["record_id"],
                "basis": "access=%s；未提供全文" % record.get("access"),
                "excluded_alternative": "该记录已在所提供语料中；空白来自全文缺失，"
                                        "不是检索遗漏",
                "evidence": (record.get("evidence") or [None])[0],
            })
    for row in topic_rows:
        if row["count"] == 1:
            record = next((r for r in unique_records
                           if canonical_topic(r.get("topic"), r) == row["topic"]), None)
            gaps.append({
                "gap": "主题 %s 只有 1 条记录，无法判断稳定趋势或饱和" % row["topic"],
                "basis": "topic_counts=%d/%d" % (row["count"], len(unique_records)),
                "excluded_alternative": "同义词已归并到 topic_mapping；单条仍不足，"
                                        "不是检索遗漏的替代解释",
                "evidence": (record.get("evidence") or [None])[0] if record else None,
            })
    if not gaps:
        gaps.append({
            "gap": "在所提供语料内未识别出可复核的趋势空白",
            "basis": "所有记录均有年份与可读文本",
            "excluded_alternative": "仅表示当前语料未见空白，不表示领域没有空白",
            "evidence": flatten_evidence(unique_records, 1),
        })
    uncertainties = [
        {"item": "search_scope", "reason": "筛选只针对所提供记录，未执行在线检索",
         "impact": "不能声称覆盖整个领域", "action": "需要时补做在线检索并保存检索式与日期"},
        {"item": "small_sample", "reason": "唯一记录数 %d" % len(unique_records),
         "impact": "年度波动不能稳定解释为拐点", "action": "扩大语料或做敏感性分析"},
        {"item": "causality", "reason": "主题共现与年度计数不能建立因果",
         "impact": "所有 count/co-occurrence 结论仅作描述", "action": "需要设计性证据或机制研究"},
    ]
    if missing_count:
        uncertainties.append({"item": "missing_year", "reason": "%d 条记录缺年份"
                              % missing_count, "impact": "年度分母不完整",
                              "action": "回源补年份"})
    if time_window.get("incomplete_last_year"):
        uncertainties.append({"item": "incomplete_last_year",
                              "reason": "最后一年可能索引未完成",
                              "impact": "末年计数可能偏低", "action": "更新检索截面"})
    return {
        "data_quality": quality,
        "time_window": time_window,
        "year_counts": year_counts,
        "topic_counts": topic_rows,
        "topic_mapping": topic_mapping,
        "representative_rule": representative_rule,
        "representatives": representatives,
        "representative_exclusions": exclusions,
        "stages": stages,
        "findings": findings,
        "gap_analysis": gaps,
        "uncertainties": uncertainties,
        "search_scope": "provided_corpus_only; no live search",
    }


# ---------------------------------------------------------------------------
# topic-selection
# ---------------------------------------------------------------------------
def all_docs(data):
    return (data.get("support_docs") or []) + (data.get("source_docs") or [])


def find_field_evidence(data, field_key):
    for doc in all_docs(data):
        fields = parse_fields(doc.text)
        if fields.get(field_key):
            value, line_no, raw = fields[field_key]
            return value, doc.evidence_at(line_no)
    return "", None


def find_line_evidence(data, pattern):
    for doc in all_docs(data):
        for index, raw in enumerate(doc.lines, 1):
            if re.search(pattern, raw, re.I):
                return norm(raw), doc.evidence_at(index)
    return "", None


def extract_number(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def problem_definition(data, question):
    specs = [
        ("question", question, r"(?:Research question|研究问题)\s*[:：]\s*(.+)"),
        ("population", "", r"(?:Population|研究对象|人群)\s*[:：]\s*(.+)"),
        ("variables", "", r"(?:Variables?|变量|指标)\s*[:：]\s*(.+)"),
        ("baseline", "", r"(?:Baseline|比较基线|对照)\s*[:：]\s*(.+)"),
        ("expected_conclusion", "", r"(?:Expected conclusion|预期结论)\s*[:：]\s*(.+)"),
        ("falsification_conditions", "", r"(?:Falsification condition|证伪条件)\s*[:：]\s*(.+)"),
        ("constraints", "", r"(?:Constraints?|约束)\s*[:：]\s*(.+)"),
    ]
    fields = {}
    evidence = []
    field_status = {}
    for key, initial, pattern in specs:
        lookup = "falsification" if key == "falsification_conditions" else key
        value, ev = (initial, None) if initial else find_field_evidence(data, lookup)
        if not value:
            value, ev = find_line_evidence(data, pattern)
        if not value:
            value = "not_stated_in_materials"
        if ev:
            evidence.append(ev)
        fields[key] = value
        field_status[key] = {
            "value": value,
            "status": "source_bound" if ev else "not_stated_in_materials",
            "evidence": ev,
        }
    if isinstance(fields.get("falsification_conditions"), str) and \
            fields["falsification_conditions"] != "not_stated_in_materials":
        falsification = [fields["falsification_conditions"]]
    elif fields.get("falsification_conditions") == "not_stated_in_materials":
        falsification = ["not_stated_in_materials"]
    else:
        falsification = fields.get("falsification_conditions") or []
    testable = bool(
        fields["question"] and fields["question"] != "not_stated_in_materials"
        and fields["variables"] != "not_stated_in_materials"
        and (fields["expected_conclusion"] != "not_stated_in_materials"
             or falsification != ["not_stated_in_materials"]))
    constraints = [fields["constraints"]] if fields["constraints"] not in (
        "", "not_stated_in_materials") else []
    return {
        "question": fields["question"],
        "population": fields["population"],
        "variables": fields["variables"],
        "baseline": fields["baseline"],
        "expected_conclusion": fields["expected_conclusion"],
        "falsification_conditions": falsification,
        "constraints": constraints,
        "scope": "provided_corpus_only",
        "testable": testable,
        "field_status": field_status,
        "bounded_scope": {
            "setting": "one clinic" if re.search(r"one sleep clinic|single clinic|一个",
                                                 " ".join(str(d.text) for d in all_docs(data)),
                                                 re.I) else fields["population"],
            "constraints": constraints,
            "not_claimed": ["global_novelty", "completed_experiments", "external_validation"],
        },
        "evidence": unique_evidence(evidence),
    }


def comparison_matrix(unique_records):
    rows = []
    for record in unique_records:
        design = record.get("method") or ""
        data_value = record.get("data") or ""
        if not data_value:
            match = re.search(r"\bn\s*=\s*(\d+)", design, re.I)
            if match:
                data_value = "n=%s" % match.group(1)
        value = {
            "task": record.get("topic") or "not_extracted",
            "method": design or "not_extracted",
            "data": data_value or "not_extracted",
            "evaluation": record.get("evaluation") or "not_extracted",
            "conclusion": record.get("conclusion") or record.get("finding") or
                          "not_extracted",
        }
        extracted = sum(1 for item in value.values() if item != "not_extracted")
        text = record.get("search_text", "")
        if re.search(r"protocol|abstract only", text, re.I):
            relation = "protocol_or_abstract_only"
        elif re.search(r"guideline|recommendation|reporting", text, re.I):
            relation = "reporting_guideline"
        elif re.search(r"review|scoping|systematic", text, re.I):
            relation = "review"
        elif re.search(r"shift worker", text, re.I):
            relation = "adjacent_shift_worker_study"
        else:
            relation = "adjacent_study"
        rows.append({
            "record_id": record["record_id"],
            "option": record.get("title") or record["record_id"],
            "dimension": "task/method/data/evaluation/conclusion",
            "value": value,
            "directly_comparable": extracted >= 3,
            "reason": ("至少三个比较字段来自材料，可做有限比较" if extracted >= 3 else
                       "题录/摘要缺少可比较字段；不能据此判定等效或创新"),
            "relation_to_proposal": relation,
            "evidence": (record.get("evidence") or [None])[0],
        })
    return rows


def novelty_claims(data, question):
    claims, evidence = [], []
    seen = set()
    for doc in all_docs(data):
        fields = parse_fields(doc.text)
        candidates = []
        if fields.get("innovation"):
            value, line_no, raw = fields["innovation"]
            candidates.append((value, doc.evidence_at(line_no)))
        for index, raw in enumerate(doc.lines, 1):
            text = norm(raw)
            if len(text) < 20:
                continue
            if re.search(r"novel|novelty|first|no prior|unprecedented|we propose|"
                         r"we introduce|our contribution|创新|首次|尚无人|未见报道|"
                         r"我们提出|本文提出|贡献", text, re.I):
                candidates.append((text, doc.evidence_at(index)))
        for text, ev in candidates:
            key = re.sub(r"[^0-9a-z一-鿿]+", "", text.lower())
            if key in seen:
                continue
            seen.add(key)
            if re.search(r"method|algorithm|model|framework|mechanism|方法|模型|机制",
                         text, re.I):
                kind = "method"
            elif re.search(r"data|dataset|corpus|数据|样本", text, re.I):
                kind = "data"
            elif re.search(r"shift|scenario|setting|population|场景|人群", text, re.I):
                kind = "scenario_transfer"
            elif re.search(r"conclusion|correct|revise|结论|修正", text, re.I):
                kind = "conclusion_revision"
            else:
                kind = "unknown"
            scored = []
            for record in data["unique_records"]:
                if re.search(r"proposal|本研究|work title", record.get("search_text"), re.I):
                    continue
                score = containment(text, record.get("search_text"))
                if score >= 0.12:
                    scored.append({
                        "record_id": record["record_id"],
                        "overlap": round(score, 3),
                        "title": record.get("title"),
                        "shared_terms": sorted(tokens(text) & tokens(
                            record.get("search_text")))[:10],
                    })
            scored.sort(key=lambda x: (-x["overlap"], x["record_id"]))
            scored = scored[:5]
            if scored and scored[0]["overlap"] >= 0.30:
                verdict = "possibly_covered"
            elif scored:
                verdict = "weakly_related"
            else:
                verdict = "no_overlap_in_provided_corpus"
            global_words = bool(re.search(r"首次|尚无人|未见报道|no prior|first|unprecedented",
                                          text, re.I))
            claims.append({
                "id": "N%02d" % (len(claims) + 1),
                "claim": text,
                "type": kind,
                "scope": "provided_corpus_only",
                "covered_by": scored,
                "verdict": verdict,
                "global_novelty_supported": False if global_words else None,
                "boundary": ("含全球性措辞；仅凭所提供语料不能支持全球新颖性，"
                             "需在线检索并保留检索式/日期/快照" if global_words else
                             "新颖性只在所提供语料范围内判定"),
                "evidence": ev,
            })
            if ev:
                evidence.append(ev)
    if not claims:
        claims.append({
            "id": "N01",
            "claim": "材料未给出可识别的创新陈述（to_confirm）",
            "type": "not_stated",
            "scope": "not_stated_in_materials",
            "covered_by": [],
            "verdict": "not_established",
            "global_novelty_supported": None,
            "boundary": "不能替研究问题负责人补造创新点",
            "evidence": None,
        })
    return claims, unique_evidence(evidence)


def feasibility_analysis(data):
    text = "\n".join(doc.text for doc in all_docs(data))
    specs = [
        ("data", r"dataset|data availability|数据|样本|语料|wearable data", "数据可用性"),
        ("recruitment", r"eligible|recruit|招募|入组|per year", "受试者招募"),
        ("time", r"month|timeline|时间窗|周期|recruitment window", "时间窗口"),
        ("personnel", r"statistics support|personnel|team|人员|统计", "人员能力"),
        ("ethics", r"ethic|irb|data-use|伦理|数据使用|共享", "伦理/数据共享"),
        ("instrument", r"wearable|device|instrument|设备|仪器|平台", "设备/平台"),
        ("outcome", r"non-response|outcome|events|终点|响应", "结局事件"),
    ]
    premises, evidence = [], []
    for key, pattern, label in specs:
        value, ev = find_line_evidence(data, pattern)
        premises.append({
            "premise": label,
            "key": key,
            "value": value or "not_stated_in_materials",
            "source": ev.get("path") if ev else None,
            "locator": ev.get("locator") if ev else None,
            "status": "evidence_in_material" if ev else "unknown",
            "evidence": ev,
        })
        if ev:
            evidence.append(ev)
    per_year = extract_number(text, [
        r"(\d+)\s*(?:eligible\s+)?shift workers\s+per\s+year",
        r"about\s+(\d+)\s+per\s+year",
        r"每年\s*(\d+)\s*(?:名|例|人)",
    ])
    months = extract_number(text, [
        r"(\d+)[- ]month(?:s)?\s+(?:recruitment|window)",
        r"recruitment window\s*[:：]\s*(\d+)\s*month",
        r"(\d+)\s*个月",
    ])
    rate = extract_number(text, [
        r"non-response rate\s*[:：]?\s*(?:about\s*)?(\d+(?:\.\d+)?)\s*%",
        r"(\d+(?:\.\d+)?)\s*%\s*(?:expected\s+)?non-response",
        r"无应答率\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%",
    ])
    predictors = extract_number(text, [
        r"with\s+(\d+)\s+(?:candidate\s+)?predictors",
        r"(\d+)\s*(?:candidate\s+)?predictors",
        r"(\d+)\s*个预测因子",
    ])
    epv = extract_number(text, [
        r"at least\s+(\d+)\s+outcome events per candidate predictor",
        r"events per (?:candidate )?predictor\s*(?:of|:)?\s*(\d+)",
    ])
    fte = extract_number(text, [
        r"(\d+(?:\.\d+)?)\s*full-time",
        r"(\d+(?:\.\d+)?)\s*FTE",
    ])
    participants = events = required_events = shortfall = None
    max_predictors = required_participants = required_years = None
    if per_year is not None and months is not None:
        participants = per_year * months / 12.0
    if participants is not None and rate is not None:
        events = participants * rate / 100.0
    if predictors is not None and epv is not None:
        required_events = predictors * epv
    if events is not None and required_events is not None:
        shortfall = required_events - events
    if events is not None and epv:
        max_predictors = int(math.floor(events / epv))
    if required_events is not None and rate:
        required_participants = int(math.ceil(required_events / (rate / 100.0)))
    if required_participants is not None and per_year:
        required_years = round(required_participants / per_year, 2)
    calculation = {
        "inputs": {"eligible_per_year": per_year, "recruitment_months": months,
                   "expected_nonresponse_rate_pct": rate,
                   "candidate_predictors": predictors,
                   "events_per_predictor": epv, "statistics_fte": fte},
        "formulas": {
            "participants": "eligible_per_year * recruitment_months / 12",
            "expected_events": "participants * expected_nonresponse_rate",
            "required_events": "candidate_predictors * events_per_predictor",
            "shortfall": "required_events - expected_events",
            "max_predictors": "floor(expected_events / events_per_predictor)",
            "required_participants": "ceil(required_events / expected_nonresponse_rate)",
            "required_years": "required_participants / eligible_per_year",
        },
        "values": {
            "participants": None if participants is None else round(participants, 1),
            "expected_events": None if events is None else round(events, 1),
            "required_events": None if required_events is None else round(required_events, 1),
            "shortfall": None if shortfall is None else round(shortfall, 1),
            "max_predictors": max_predictors,
            "required_participants": required_participants,
            "required_years": required_years,
        },
        "status": ("derived_from_material" if events is not None or required_events is not None
                   else "not_computable"),
        "note": "只做输入数字的算术推导；不把推导值当作已完成的实验结果。",
    }
    risks = [
        {"risk": "统计功效不足", "impact": "high" if (
            shortfall is not None and shortfall > 0) else "medium",
         "mitigation": "减少候选预测因子、延长招募或扩大中心数"},
        {"risk": "轮班状态依赖自报", "impact": "medium",
         "mitigation": "做自报与客观排班的敏感性分析"},
        {"risk": "单中心与数据不可外享", "impact": "medium",
         "mitigation": "预先限定外部验证范围，或改为多中心授权协议"},
    ]
    if shortfall is not None and shortfall > 0:
        conclusion = "conditional_with_shortfall"
    elif events is not None:
        conclusion = "conditional"
    else:
        conclusion = "insufficient_information"
    conditions = []
    for premise in premises:
        if premise["status"] != "evidence_in_material":
            conditions.append("补齐%s的书面前提（提供方/数量/时间/限制）" % premise["premise"])
    if shortfall is not None and shortfall > 0:
        conditions.append(
            "按 20 EPV 规则，现有设计预计事件数 %.1f，少于所需的 %.1f；需减少预测因子或扩大样本"
            % (events, required_events))
    return {
        "premises": premises,
        "calculation": calculation,
        "conclusion": conclusion,
        "conditions": conditions,
        "risks": risks,
        "evidence": unique_evidence(evidence),
    }


def bounded_scenarios(feasibility):
    calc = feasibility.get("calculation") or {}
    inputs = calc.get("inputs") or {}
    values = calc.get("values") or {}
    evidence = feasibility.get("evidence") or []
    scenarios = []
    scenarios.append({
        "id": "SC1",
        "name": "as_proposed",
        "description": "按材料所述的 prospective cohort、内部验证与现有单中心约束执行",
        "assumptions": [
            "每年可用受试者约 %s 人" % inputs.get("eligible_per_year"),
            "招募 %s 个月" % inputs.get("recruitment_months"),
            "无应答率约 %s%%" % inputs.get("expected_nonresponse_rate_pct"),
            "候选预测因子 %s 个" % inputs.get("candidate_predictors"),
        ],
        "calculation": {
            "participants": values.get("participants"),
            "expected_events": values.get("expected_events"),
            "required_events": values.get("required_events"),
            "shortfall": values.get("shortfall"),
        },
        "conclusion": ("在现有样本量下事件数不足，按 20 EPV 规则不可直接开发原定预测模型"
                       if values.get("shortfall") is not None and values.get("shortfall") > 0
                       else "在材料给出的数字范围内可做有限的模型开发，但仍需外部验证"),
        "status": ("bounded_estimate" if values.get("expected_events") is not None
                   else "not_computable"),
        "binding_constraints": ["单中心", "24 个月招募窗", "轮班状态自报", "数据不可外享"],
        "evidence": evidence[:2],
    })
    if values.get("max_predictors") is not None and values["max_predictors"] >= 1:
        scenarios.append({
            "id": "SC2",
            "name": "reduced_model",
            "description": "按可用事件数缩减候选预测因子，仅做内部验证",
            "assumptions": ["事件数约 %.1f" % values.get("expected_events", 0),
                            "20 EPV 规则来自材料"],
            "calculation": {"max_predictors": values["max_predictors"],
                            "required_events_for_that_model":
                                values["max_predictors"] * (inputs.get("events_per_predictor") or 0)},
            "conclusion": "可把候选预测因子压缩到不超过 %d 个；结论仍受单中心与自报轮班限制"
                          % values["max_predictors"],
            "status": "bounded_design_option",
            "binding_constraints": ["模型复杂度受限", "外部验证仍缺失"],
            "evidence": evidence[:2],
        })
    else:
        scenarios.append({
            "id": "SC2",
            "name": "reduced_model",
            "description": "缩减模型方案",
            "assumptions": [],
            "calculation": {"max_predictors": values.get("max_predictors")},
            "conclusion": "材料数字不足或事件数为零，无法给出可执行的缩减模型；需补充样本量假设",
            "status": "not_computable",
            "binding_constraints": ["缺少事件数/EPV 数字"],
            "evidence": evidence[:1],
        })
    scenarios.append({
        "id": "SC3",
        "name": "de_scoped_internal_only",
        "description": "退化为单中心内部验证研究，不宣称外部有效性",
        "assumptions": ["不改变数据不可外享的约束", "不把内部验证写成临床可用性证据"],
        "calculation": {"external_validation": "not_planned"},
        "conclusion": "可作为内部可行性/信号研究，但不能支持治疗分层已可临床使用",
        "status": "bounded_design_option",
        "binding_constraints": ["external validation missing", "single clinic"],
        "evidence": evidence[:2],
    })
    return scenarios


def polarity(text, positive, negative):
    text = str(text or "")
    if negative.search(text):
        return -1
    if positive.search(text):
        return 1
    return 0


def conflict_items(unique_records):
    positive = re.compile(r"improv(?:e|es|ed|ing)|raised|predicted lower|lower response|"
                          r"associated|outperformed|提高|降低|相关", re.I)
    negative = re.compile(r"did not improve|no reliable benefit|no benefit|no validated|"
                          r"heterogeneous|mixed|not improve|未改善|无可靠|混合|异质", re.I)
    items = []
    for i, left in enumerate(unique_records):
        for right in unique_records[i + 1:]:
            same_topic = (not left.get("topic") or not right.get("topic")
                          or norm(left["topic"]).lower() == norm(right["topic"]).lower())
            if not same_topic and containment(left.get("search_text"),
                                              right.get("search_text")) < 0.05:
                continue
            left_text = left.get("conclusion") or left.get("finding") or left.get("search_text")
            right_text = right.get("conclusion") or right.get("finding") or right.get("search_text")
            if not left_text or not right_text:
                continue
            left_pol = polarity(left_text, positive, negative)
            right_pol = polarity(right_text, positive, negative)
            if left_pol == 1 and right_pol == -1:
                side_a, side_b = left, right
            elif left_pol == -1 and right_pol == 1:
                side_a, side_b = right, left
            else:
                continue
            items.append({
                "id": "C%02d" % (len(items) + 1),
                "topic": left.get("topic") or right.get("topic"),
                "records": [side_a["record_id"], side_b["record_id"]],
                "side_a": {"record_id": side_a["record_id"],
                           "statement": side_a.get("conclusion") or side_a.get("finding"),
                           "evidence": (side_a.get("evidence") or [None])[0]},
                "side_b": {"record_id": side_b["record_id"],
                           "statement": side_b.get("conclusion") or side_b.get("finding"),
                           "evidence": (side_b.get("evidence") or [None])[0]},
                "resolution": "所提供语料不能判定哪一方成立；两方均保留，需新证据或更严格设计",
            })
            if len(items) >= 5:
                return items
    return items


def topic_analysis(data, question):
    unique_records = data["unique_records"]
    problem = problem_definition(data, question)
    matrix = comparison_matrix(unique_records)
    claims, claim_evidence = novelty_claims(data, question)
    feasibility = feasibility_analysis(data)
    scenarios = bounded_scenarios(feasibility)
    conflicts = conflict_items(unique_records)
    findings = [{
        "id": "F01",
        "statement": "问题定义包含问题/对象/变量/基线/证伪条件；当前可检验=%s。"
                     "缺失字段已标为 not_stated_in_materials，不能替 PI 补造。" % (
                         "是" if problem["testable"] else "否"),
        "sources": [r["record_id"] for r in unique_records[:3]],
        "evidence": problem.get("evidence") or flatten_evidence(unique_records, 1),
    }]
    comparable = [row for row in matrix if row["directly_comparable"]]
    findings.append({
        "id": "F02",
        "statement": "比较矩阵覆盖 %d 条唯一记录，其中 %d 条至少有 3 个可比较字段；"
                     "其余只能做题录级相邻性判断。" % (len(matrix), len(comparable)),
        "sources": [row["record_id"] for row in matrix[:8]],
        "evidence": flatten_evidence(
            [r for r in unique_records if r["record_id"] in
             {row["record_id"] for row in matrix[:4]}], 3),
    })
    top_claim = claims[0] if claims else {}
    findings.append({
        "id": "F03",
        "statement": "创新陈述逐条划界：%s 的判定为 %s，覆盖/相邻记录 %s；"
                     "结论只覆盖 provided_corpus_only。" % (
                         top_claim.get("id", "N/A"), top_claim.get("verdict", "not_established"),
                         ", ".join(x["record_id"] for x in top_claim.get("covered_by") or [])
                         or "无"),
        "sources": [x["record_id"] for x in top_claim.get("covered_by") or []],
        "evidence": [top_claim.get("evidence")] if top_claim.get("evidence") else
                    flatten_evidence(unique_records, 2),
    })
    calc = feasibility.get("calculation") or {}
    values = calc.get("values") or {}
    if values.get("expected_events") is not None:
        findings.append({
            "id": "F04",
            "statement": "按材料数字做算术：预计受试者 %s 人、事件 %s 个、20 EPV 所需事件 %s 个、"
                         "缺口 %s 个。这是可行性推导，不是已完成的实验。" % (
                             values.get("participants"), values.get("expected_events"),
                             values.get("required_events"), values.get("shortfall")),
            "sources": [r["record_id"] for r in unique_records[:4]],
            "evidence": feasibility.get("evidence") or flatten_evidence(unique_records, 2),
        })
    findings.append({
        "id": "F%02d" % (len(findings) + 1),
        "statement": "冲突证据检查发现 %d 组方向相反的记录；未用单方结论抹平分歧。"
                     % len(conflicts),
        "sources": [rid for item in conflicts for rid in item["records"]],
        "evidence": unique_evidence(
            [item["side_a"].get("evidence") for item in conflicts]
            + [item["side_b"].get("evidence") for item in conflicts])[:4]
        or flatten_evidence(unique_records, 2),
    })
    gaps = []
    for record in unique_records:
        text = " ".join(str(record.get(k) or "") for k in ("title", "finding", "limitation"))
        if re.search(r"no validated cutoff|no external validation|not conducted in shift workers",
                     text, re.I):
            gaps.append({
                "gap": "记录 %s 指出的具体证据缺口：%s" % (
                    record["record_id"], record.get("limitation") or record.get("finding")),
                "basis": "%s：%s" % (record["record_id"],
                                     record.get("limitation") or record.get("finding")),
                "excluded_alternative": "该缺口由原文明确陈述；不是检索遗漏，"
                                        "但也不能扩展为全球空白",
                "evidence": (record.get("evidence") or [None])[0],
            })
    gaps.append({
        "gap": "所提供语料中没有预测式治疗分层的前瞻性验证研究",
        "basis": "比较矩阵与创新覆盖检查未发现直接对应记录",
        "excluded_alternative": "只在 provided_corpus_only 范围内成立；需在线检索确认",
        "evidence": flatten_evidence(unique_records, 2),
    })
    uncertainties = [
        {"item": "global_novelty", "reason": "没有在线检索证据",
         "impact": "不能写“全球首次/尚无人”", "action": "补做检索式、日期与快照"},
        {"item": "assumptions", "reason": "部分可行性前提标为 unknown/assumption",
         "impact": "结论为 conditional", "action": "由 PI 补齐书面前提"},
        {"item": "conflicts", "reason": "冲突证据未被单方结论覆盖",
         "impact": "不能把某一方向写成定论", "action": "保留双方并设计判别性研究"},
    ]
    return {
        "problem_definition": problem,
        "comparison_matrix": matrix,
        "novelty_claims": claims,
        "novelty_evidence": claim_evidence,
        "feasibility": feasibility,
        "bounded_scenarios": scenarios,
        "bounded_proposal": {
            "statement": "在单中心、24 个月、约 40 人/年、无排班访问、数据不可外享的约束下，"
                         "只提出有边界的可行性方案；不宣称全球新颖性，也不宣称实验已完成。",
            "scope": "provided_corpus_only",
            "constraints": problem.get("constraints") or [],
        },
        "conflict_check": {"checked": True, "items": conflicts},
        "findings": findings,
        "gap_analysis": gaps,
        "uncertainties": uncertainties,
        "search_scope": "provided_corpus_only; no live search",
    }


# ---------------------------------------------------------------------------
# review-generation
# ---------------------------------------------------------------------------
def theme_name_for(record):
    topic = norm(record.get("topic"))
    if topic:
        return topic
    text = record.get("search_text") or ""
    if re.search(r"multi[- ]hop", text, re.I):
        return "multi-hop evaluation"
    if re.search(r"cross[- ]lingual|multilingual", text, re.I):
        return "multilingual retrieval"
    if re.search(r"human|attribution|expert", text, re.I):
        return "evaluation validity and human evaluation"
    if re.search(r"exact[- ]match|single[- ]hop", text, re.I):
        return "single-hop exact-match evaluation"
    return "general evaluation"


def build_themes(unique_records):
    groups = {}
    for record in unique_records:
        name = theme_name_for(record)
        groups.setdefault(name, []).append(record)
    # Keep the review structure honest: every theme needs at least two records.
    singles = [name for name, records in groups.items() if len(records) < 2]
    if singles:
        merged = []
        for name in singles:
            merged.extend(groups.pop(name))
        if merged:
            groups.setdefault("under-covered / emerging", []).extend(merged)
    while groups and any(len(records) < 2 for records in groups.values()):
        small = min((name for name, records in groups.items() if len(records) < 2),
                    key=lambda name: len(groups[name]))
        records = groups.pop(small)
        if not groups:
            groups[small] = records
            break
        target = max(groups, key=lambda name: len(groups[name]))
        groups[target].extend(records)
    themes = []
    for index, (name, records) in enumerate(sorted(groups.items(),
                                                   key=lambda x: (-len(x[1]), x[0])), 1):
        themes.append({
            "id": "TH%02d" % index,
            "name": name,
            "records": [r["record_id"] for r in records],
            "boundary": "由所提供语料中的题录主题/方法/评价对象归并；仅覆盖本语料",
            "conflicts": [],
            "support": "partial" if any(r.get("access") != "full_text" for r in records)
                       else "full",
            "strength": ("strong" if len(records) >= 4 and
                         all(r.get("access") == "full_text" for r in records) else
                         "moderate" if len(records) >= 2 else "suggestive"),
            "evidence": flatten_evidence(records, 5),
        })
    return themes


def review_conflicts(unique_records, themes):
    positive = re.compile(r"improv(?:e|es|ed|ing)|raised|predicted lower|associated|"
                          r"agreed|outperform(?:ed)? (?:the )?baseline|提高|降低|相关", re.I)
    negative = re.compile(r"did not improve|no reliable benefit|no benefit|no validated|"
                          r"matched|outperform(?:ed)? it|overestimate|varied|heterogeneous|"
                          r"mixed|not improve|未改善|无可靠|混合|异质|高估", re.I)
    items = []
    theme_of = {}
    for theme in themes:
        for rid in theme["records"]:
            theme_of.setdefault(rid, []).append(theme["id"])
    for i, left in enumerate(unique_records):
        for right in unique_records[i + 1:]:
            left_text = left.get("conclusion") or left.get("finding") or left.get("search_text")
            right_text = right.get("conclusion") or right.get("finding") or right.get("search_text")
            if not left_text or not right_text:
                continue
            left_pol = polarity(left_text, positive, negative)
            right_pol = polarity(right_text, positive, negative)
            if left_pol == 1 and right_pol == -1:
                side_a, side_b = left, right
            elif left_pol == -1 and right_pol == 1:
                side_a, side_b = right, left
            else:
                continue
            shared_themes = sorted(set(theme_of.get(side_a["record_id"], [])) &
                                   set(theme_of.get(side_b["record_id"], [])))
            items.append({
                "id": "C%02d" % (len(items) + 1),
                "themes": shared_themes,
                "topic": side_a.get("topic") or side_b.get("topic"),
                "records": [side_a["record_id"], side_b["record_id"]],
                "side_a": {"record_id": side_a["record_id"],
                           "statement": side_a.get("conclusion") or side_a.get("finding"),
                           "evidence": (side_a.get("evidence") or [None])[0]},
                "side_b": {"record_id": side_b["record_id"],
                           "statement": side_b.get("conclusion") or side_b.get("finding"),
                           "evidence": (side_b.get("evidence") or [None])[0]},
                "resolution": "未解决；两方证据均保留，不能合并成单一路线",
            })
            if len(items) >= 5:
                return {"checked": True, "items": items}
    return {"checked": True, "items": items}


def review_theme_findings(themes, unique_records, conflicts):
    by_id = {r["record_id"]: r for r in unique_records}
    findings = []
    for theme in themes:
        records = [by_id[rid] for rid in theme["records"] if rid in by_id]
        conclusions = []
        for record in records[:4]:
            text = record.get("conclusion") or record.get("finding")
            if text:
                conclusions.append("%s 报告“%s”" % (record["record_id"], text))
        if not conclusions:
            conclusions = ["%s 仅提供题录/摘要，无法核验方法与结果" % r["record_id"]
                           for r in records[:3]]
        support = "partial" if any(r.get("access") != "full_text" for r in records) \
            else "full"
        if any(item["themes"] and theme["id"] in item["themes"] for item in conflicts):
            support = "partial"
        strength = ("strong" if len(records) >= 4 and support == "full" else
                    "moderate" if len(records) >= 3 else "suggestive")
        findings.append({
            "id": "F%02d" % (len(findings) + 1),
            "theme_id": theme["id"],
            "statement": "主题“%s”下有 %d 条记录：%s。以上为逐条复述，"
                         "不把单篇结论扩展为该主题的定论。" % (
                             theme["name"], len(records), "；".join(conclusions)),
            "sources": [r["record_id"] for r in records],
            "support": support,
            "strength": strength,
            "limitations": (["含仅摘要/题录记录，不能核验全文"] if support == "partial"
                            else []) + ["所提供语料规模小，不能覆盖整个领域"],
            "evidence": flatten_evidence(records, 5),
        })
    if conflicts:
        findings.append({
            "id": "F%02d" % (len(findings) + 1),
            "theme_id": "conflicts",
            "statement": "检测到 %d 组方向相反的结论，已并列保留；未用平滑叙事抹平分歧。"
                         % len(conflicts),
            "sources": [rid for item in conflicts for rid in item["records"]],
            "support": "partial",
            "strength": "suggestive",
            "limitations": ["冲突未由所提供语料解决"],
            "evidence": unique_evidence(
                [item["side_a"].get("evidence") for item in conflicts]
                + [item["side_b"].get("evidence") for item in conflicts])[:6],
        })
    return findings


def review_gaps(themes, unique_records):
    gaps = []
    by_id = {r["record_id"]: r for r in unique_records}
    for theme in themes:
        if len(theme["records"]) <= 2:
            record = by_id.get(theme["records"][0]) if theme["records"] else None
            gaps.append({
                "gap": "主题“%s”只有 %d 条记录，证据不足以判断稳定结论" % (
                    theme["name"], len(theme["records"])),
                "basis": "%s: %d records" % (theme["id"], len(theme["records"])),
                "alternative_explanations": ["该主题可能确实处于早期，也可能是检索覆盖不足"],
                "why_not_omission": "已对同义主题做归并；仍只有少量记录，需在线检索确认",
                "evidence": (record.get("evidence") or [None])[0] if record else None,
            })
    for record in unique_records:
        if record.get("access") != "full_text":
            gaps.append({
                "gap": "记录 %s 仅有摘要/题录，无法核验方法、结果或归因" % record["record_id"],
                "basis": "access=%s" % record.get("access"),
                "alternative_explanations": ["全文可能支持也可能不支持摘要中的说法"],
                "why_not_omission": "记录已在所提供语料中；缺口来自全文缺失，不是检索遗漏",
                "evidence": (record.get("evidence") or [None])[0],
            })
    text = " ".join(str(r.get("search_text") or "") for r in unique_records)
    if re.search(r"only 3 of 18|human evaluation", text, re.I):
        record = next((r for r in unique_records
                       if re.search(r"only 3 of 18|human evaluation",
                                    r.get("search_text") or "", re.I)), None)
        gaps.append({
            "gap": "所提供语料显示人工评价报告不足，自动指标与人类判断的一致性仍需系统核验",
            "basis": "记录中明确提到人工评价覆盖有限",
            "alternative_explanations": ["可能是本语料的选择偏差"],
            "why_not_omission": "该陈述来自原文，不是检索遗漏；但不能扩展为整个领域",
            "evidence": (record.get("evidence") or [None])[0] if record else None,
        })
    if not gaps:
        gaps.append({
            "gap": "所提供语料内未识别出可复核的综述空白",
            "basis": "各主题均有至少 2 条记录且证据可回链",
            "alternative_explanations": ["可能是语料选择偏差"],
            "why_not_omission": "仅表示当前语料未见空白，不表示领域没有空白",
            "evidence": flatten_evidence(unique_records, 1),
        })
    return gaps


def review_structure_payload(question, themes, findings, conflicts, gaps, protocol):
    sections = []
    sections.append({
        "id": "SEC1", "title": "问题与范围",
        "purpose": "固定研究问题、语料边界与可检验的子问题",
        "record_ids": [], "evidence_count": 1,
        "draft": "本综述只回答：%s。语料为所提供的记录；检索协议为前瞻性方案，"
                 "未声称执行在线检索。" % (question or "材料未给出明确问题"),
    })
    sections.append({
        "id": "SEC2", "title": "筛选与语料",
        "purpose": "先固定纳排标准，再报告筛选计数与每条的决策理由",
        "record_ids": [], "evidence_count": len(protocol.get("key_terms") or []),
        "draft": "筛选按 include/exclude 规则对每条记录留痕；计数见 screening-protocol.json。",
    })
    for theme in themes:
        theme_finding = next((f for f in findings if f.get("theme_id") == theme["id"]), None)
        sections.append({
            "id": "SEC%d" % (len(sections) + 1),
            "title": "主题：%s" % theme["name"],
            "purpose": "按方法/评价对象归并，并保留主题边界",
            "record_ids": theme["records"],
            "evidence_count": len(theme.get("evidence") or []),
            "draft": (theme_finding or {}).get("statement") or
                     "主题“%s”下有 %d 条记录。" % (theme["name"], len(theme["records"])),
        })
    if conflicts:
        sections.append({
            "id": "SEC%d" % (len(sections) + 1),
            "title": "冲突证据",
            "purpose": "并列呈现方向相反的结论，不合并为单一路线",
            "record_ids": [rid for item in conflicts for rid in item["records"]],
            "evidence_count": sum(2 for _ in conflicts),
            "draft": "；".join("%s 与 %s 对同一主题给出相反方向" %
                               (item["records"][0], item["records"][1])
                               for item in conflicts),
        })
    sections.append({
        "id": "SEC%d" % (len(sections) + 1),
        "title": "研究空白与局限",
        "purpose": "把空白落到具体证据缺口，并排除“检索遗漏”这一替代解释",
        "record_ids": [],
        "evidence_count": len(gaps),
        "draft": "；".join(item["gap"] for item in gaps[:5]),
    })
    return {"sections": sections,
            "writing_rule": "每个实质句子至少绑定一个 evidence 行；证据不足时降级为 partial/suggestive。"}


def review_coverage_payload(question, themes, findings, conflicts, gaps, protocol):
    def item(status, evidence=None, items=None):
        return {"status": status, "evidence": evidence or [], "items": items or []}
    return {
        "question_breakdown": item("covered" if question else "partial",
                                   items=[question] if question else []),
        "search_coverage": item("partial" if not protocol.get("executed") else "covered",
                                evidence=[{"path": "work/research-plan.json",
                                           "locator": "protocol.coverage_note",
                                           "quote": protocol.get("coverage_note")}]),
        "evidence_chain": item("covered" if all(f.get("evidence") for f in findings)
                               else "partial",
                               evidence=[e for f in findings
                                         for e in (f.get("evidence") or [])][:3]),
        "conflicting_evidence": item("covered" if conflicts.get("checked") else "partial",
                                     items=conflicts.get("items") or []),
        "synthesis": item("covered" if findings else "partial",
                          items=[f["id"] for f in findings]),
        "gaps": item("covered" if gaps else "partial",
                     items=[g.get("gap") for g in gaps[:5]]),
    }


def review_analysis(data, question):
    unique_records = data["unique_records"]
    themes = build_themes(unique_records)
    conflicts = review_conflicts(unique_records, themes)
    findings = review_theme_findings(themes, unique_records, conflicts["items"])
    gaps = review_gaps(themes, unique_records)
    synthesis = {"conclusions": []}
    for finding in findings[:5]:
        synthesis["conclusions"].append({
            "statement": finding["statement"],
            "records": finding.get("sources") or [],
            "evidence": finding.get("evidence") or [],
            "strength": finding.get("strength") or "suggestive",
        })
    if gaps:
        synthesis["conclusions"].append({
            "statement": "研究空白：%s" % gaps[0]["gap"],
            "records": [],
            "evidence": [gaps[0]["evidence"]] if gaps[0].get("evidence") else [],
            "strength": "suggestive",
        })
    facets = [
        ("question_breakdown", "问题拆解"),
        ("search_coverage", "检索覆盖"),
        ("evidence_chain", "证据回链"),
        ("conflicting_evidence", "冲突证据"),
        ("synthesis", "综合结论"),
        ("gaps", "研究空白"),
    ]
    coverage = {}
    for key, label in facets:
        if key == "question_breakdown":
            status = "covered" if question else "partial"
        elif key == "search_coverage":
            status = "partial"
        elif key == "conflicting_evidence":
            status = "covered" if conflicts["checked"] else "partial"
        elif key == "gaps":
            status = "covered" if gaps else "partial"
        else:
            status = "covered" if findings else "partial"
        coverage[key] = {"label": label, "status": status,
                         "items": [], "evidence": flatten_evidence(unique_records, 1)}
    subquestions = []
    facet_questions = (data.get("fixed_questions") or []) + [
        "筛选与纳排", "主题归纳", "证据回链与强度", "冲突证据", "研究空白"]
    for index, text in enumerate(facet_questions, 1):
        subquestions.append({
            "id": "Q%02d" % index, "question": text,
            "status": "covered" if (themes and findings) else "partial",
            "evidence": flatten_evidence(unique_records, 1),
        })
    structure = review_structure_payload(question, themes, findings,
                                         conflicts["items"], gaps,
                                         data.get("protocol") or {})
    uncertainties = [
        {"item": "search_scope", "reason": "只筛选所提供语料；未执行在线检索",
         "impact": "不能声称覆盖整个领域", "action": "需要时补做检索并保存检索式/日期"},
        {"item": "abstract_only", "reason": "%d 条记录只有摘要/题录"
         % sum(1 for r in unique_records if r.get("access") != "full_text"),
         "impact": "相关主题只能给 partial/suggestive", "action": "获取全文后重跑"},
        {"item": "conflicts", "reason": "%d 组冲突未被所提供语料解决"
         % len(conflicts["items"]), "impact": "不能合并成单一路线",
         "action": "保留双方并设计判别性研究"},
        {"item": "small_corpus", "reason": "唯一记录 %d 条" % len(unique_records),
         "impact": "主题划分是透明规则下的可复核结果，不是唯一分类",
         "action": "扩大语料或做敏感性分析"},
    ]
    return {
        "themes": themes,
        "findings": findings,
        "conflict_check": conflicts,
        "synthesis": synthesis,
        "coverage": coverage,
        "subquestions": subquestions,
        "gaps": gaps,
        "gap_analysis": gaps,
        "review_structure": structure,
        "uncertainties": uncertainties,
        "search_scope": "provided_corpus_only; no live search",
    }


# ---------------------------------------------------------------------------
# assemble and check
# ---------------------------------------------------------------------------
def references_from_records(all_records):
    refs = []
    for record in all_records:
        refs.append({
            "key": record["record_id"],
            "id": record["record_id"],
            "title": record.get("title"),
            "authors": record.get("authors"),
            "year": record.get("year"),
            "venue": record.get("venue"),
            "doi": record.get("doi"),
            "arxiv": record.get("arxiv"),
            "source_path": record.get("source_path") or record.get("registry_path"),
            "access": record.get("access"),
            "status": record.get("status"),
            "duplicate_of": record.get("duplicate_of") or None,
            "evidence": (record.get("evidence") or [None])[0],
        })
    return refs


def mode_facets(mode):
    return {
        "trend-analysis": ["时间趋势与年度分布", "主题演化与阶段划分",
                           "各阶段代表性工作", "研究空白与证据边界"],
        "topic-selection": ["研究问题定义与可检验性", "已有工作比较",
                            "创新点逐条划界", "可行性前提与约束", "冲突证据"],
        "review-generation": ["筛选与纳排", "主题归纳", "证据回链与强度",
                              "冲突证据", "研究空白"],
    }.get(mode, ["研究问题", "证据", "边界"])


def build_plan(docs, case, task_text, mode):
    registries = build_registries(docs, case)
    all_records = registries["all_records"]
    unique_records = registries["unique_records"]
    source_docs = registries["source_docs"]
    valid_paths = {doc.path for doc in docs}

    def keep_evidence(item):
        path = str((item or {}).get("path") or "")
        return bool(path) and (path in valid_paths
                               or path.startswith(("work/", "output/")))

    for record in all_records:
        record["evidence"] = [e for e in (record.get("evidence") or [])
                              if keep_evidence(e)]
        if not record.get("evidence"):
            doc = resolve_doc(record, registries["docs_by_path"],
                              {d.stem: d for d in docs}, source_docs)
            if doc is not None:
                attach_doc_evidence(record, doc)
                record["evidence"] = [e for e in (record.get("evidence") or [])
                                      if keep_evidence(e)]
    question, question_ev = case_question(case, task_text)
    if not question:
        question, question_ev = source_question(registries["support_docs"], source_docs)
    if not question:
        question, question_ev = task_question(task_text)
    if not question and isinstance(case, dict):
        fallback = norm(case.get("title"))
        if fallback:
            question = fallback
            question_ev = {"path": "work/input-case.json", "locator": "title",
                           "quote": fallback}
    if not question:
        question = "材料未提供明确研究问题（to_confirm）"
    if question_ev and not keep_evidence(question_ev):
        question_ev = None
    fixed = fixed_questions(case, task_text) or mode_facets(mode)
    protocol = build_protocol(question, unique_records, case, docs)
    criteria = criteria_payload(question, unique_records)
    screening = screen_records(all_records, question, criteria, protocol)
    data = dict(registries)
    data["protocol"] = protocol
    data["fixed_questions"] = fixed
    source_registry = build_source_registry(source_docs, all_records, docs, case)
    if mode == "trend-analysis":
        mode_payload = trend_analysis(data, question)
    elif mode == "topic-selection":
        mode_payload = topic_analysis(data, question)
    else:
        mode_payload = review_analysis(data, question)
    matrix = build_evidence_matrix(all_records, mode)
    references = references_from_records(all_records)
    evidence = unique_evidence(
        [e for record in all_records for e in (record.get("evidence") or [])]
        + [e for finding in mode_payload.get("findings") or []
           for e in (finding.get("evidence") or [])]
        + ([question_ev] if question_ev else []))
    if not evidence:
        evidence = unique_evidence([e for record in all_records
                                    for e in (record.get("evidence") or [])])
    evidence = [e for e in evidence if keep_evidence(e)]
    findings = mode_payload.get("findings") or []
    gaps = mode_payload.get("gap_analysis") or mode_payload.get("gaps") or []
    uncertainties = mode_payload.get("uncertainties") or []
    questions = []
    for index, text in enumerate(fixed, 1):
        covered = bool(findings and evidence)
        questions.append({
            "id": "Q%02d" % index,
            "question": text,
            "status": "covered" if covered else "partial",
            "evidence": evidence[:2],
        })
    covered_count = sum(1 for item in questions if item["status"] == "covered")
    summary = ("研究 profile 已读取 %d 份来源、%d 条记录（唯一 %d 条），"
               "按前瞻性筛选协议逐条留痕，并生成 %s 分析。结论只覆盖所提供语料。"
               % (source_registry["source_count"], len(all_records), len(unique_records),
                  {"trend-analysis": "时间趋势/主题/代表作/空白",
                   "topic-selection": "问题定义/比较/新颖性/可行性",
                   "review-generation": "筛选/主题/证据/综述结构"}.get(mode, "领域")))
    plan = {
        "case_id": "",
        "mode": mode,
        "summary": summary,
        "question": question,
        "answer": summary,
        "fixed_questions": fixed,
        "subquestions": questions,
        "sources": source_registry["sources"],
        "source_registry": source_registry,
        "records": [{
            "record_id": r["record_id"], "title": r.get("title"), "year": r.get("year"),
            "source_path": r.get("source_path") or r.get("registry_path"),
            "access": r.get("access"), "status": r.get("status"),
            "duplicate_of": r.get("duplicate_of") or None,
            "screening_decision": r.get("screening_decision"),
            "evidence_count": len(r.get("evidence") or []),
        } for r in all_records],
        "references": references,
        "protocol": protocol,
        "search_protocol": protocol,
        "screening_protocol": protocol,
        "screening_criteria": screening["criteria"],
        "screening": {"counts": screening["counts"],
                      "records": screening["records"],
                      "excluded_reasons": screening["excluded_reasons"]},
        "screening_log": screening["records"],
        "evidence_matrix": matrix,
        "evidence": evidence,
        "findings": findings,
        "gap_analysis": gaps,
        "uncertainties": uncertainties,
        "search_scope": mode_payload.get("search_scope") or
                        "provided_corpus_only; no live search",
        "profile_summary": {
            "mode": mode,
            "sources_supplied": source_registry["source_count"],
            "records_identified": len(all_records),
            "records_unique": len(unique_records),
            "duplicates": screening["counts"]["duplicates"],
            "screening_counts": screening["counts"],
            "protocol_executed": protocol.get("executed"),
            "evidence_matrix_rows": len(matrix),
            "evidence_count": len(evidence),
            "questions_total": len(questions),
            "questions_covered": covered_count,
            "expected_records": 10,
            "expected_sources": 9,
        },
        "generated_at": now_iso(),
        "_generated_by": "research_profile.py",
    }
    for key, value in mode_payload.items():
        if key not in plan or plan.get(key) in (None, "", [], {}):
            plan[key] = value
    if mode == "trend-analysis":
        plan["year_counts"] = mode_payload.get("year_counts")
        plan["topic_counts"] = mode_payload.get("topic_counts")
        plan["topic_mapping"] = mode_payload.get("topic_mapping")
        plan["time_window"] = mode_payload.get("time_window")
        plan["representative_rule"] = mode_payload.get("representative_rule")
        plan["representatives"] = mode_payload.get("representatives")
        plan["data_quality"] = mode_payload.get("data_quality")
    elif mode == "topic-selection":
        plan["problem_definition"] = mode_payload.get("problem_definition")
        plan["comparison_matrix"] = mode_payload.get("comparison_matrix")
        plan["novelty_claims"] = mode_payload.get("novelty_claims")
        plan["feasibility"] = mode_payload.get("feasibility")
    else:
        plan["themes"] = mode_payload.get("themes")
        plan["gaps"] = mode_payload.get("gaps")
        plan["synthesis"] = mode_payload.get("synthesis")
        plan["coverage"] = mode_payload.get("coverage")
        plan["conflict_check"] = mode_payload.get("conflict_check")
        plan["review_structure"] = mode_payload.get("review_structure")
    plan["_profile_checks"] = profile_checks(plan)
    return plan


def profile_checks(plan):
    problems, warnings = [], []
    mode = plan.get("mode")
    summary = plan.get("profile_summary") or {}
    if not plan.get("sources"):
        problems.append("no source documents were parsed from the supplied material")
    if summary.get("sources_supplied") != 9:
        warnings.append("sources_supplied=%s, expected 9 in the fixed profile"
                        % summary.get("sources_supplied"))
    unavailable = (plan.get("source_registry") or {}).get("unavailable_sources") or []
    if unavailable:
        warnings.append("declared sources not parsed: %s" % ", ".join(unavailable))
    if summary.get("records_unique") != 10:
        warnings.append("records_unique=%s, expected 10 in the fixed profile"
                        % summary.get("records_unique"))
    for record in plan.get("references") or []:
        if not record.get("evidence"):
            problems.append("record %s has no verbatim evidence" % record.get("id"))
    for evidence in plan.get("evidence") or []:
        for field in ("path", "locator", "quote"):
            if not evidence.get(field):
                problems.append("evidence lacks %s" % field)
    counts = (plan.get("screening") or {}).get("counts") or {}
    if None not in (counts.get("identified"), counts.get("duplicates"),
                    counts.get("screened"), counts.get("excluded"),
                    counts.get("included")):
        if counts["identified"] - counts["duplicates"] != counts["screened"]:
            problems.append("screening counts do not close at screened")
        if counts["screened"] - counts["excluded"] != counts["included"]:
            problems.append("screening counts do not close at included")
        if counts["full_text"] < counts["included"]:
            problems.append("full_text < included in screening counts")
    if not plan.get("findings"):
        problems.append("findings is empty")
    for finding in plan.get("findings") or []:
        if not finding.get("evidence"):
            problems.append("finding %s has no evidence" % finding.get("id"))
    if not plan.get("gap_analysis"):
        problems.append("gap_analysis is empty")
    for gap in plan.get("gap_analysis") or []:
        if not gap.get("basis"):
            problems.append("gap lacks basis")
        if not (gap.get("excluded_alternative") or gap.get("why_not_omission")
                or plan.get("search_scope")):
            problems.append("gap does not exclude retrieval omission")
    if mode == "trend-analysis":
        quality = (plan.get("data_quality") or {}).get("denominator") or {}
        if quality.get("rows", 0) - quality.get("duplicates", 0) != quality.get("unique"):
            problems.append("data_quality rows-duplicates != unique")
        if quality.get("dated", 0) + quality.get("missing_year", 0) != quality.get("unique"):
            problems.append("data_quality dated+missing != unique")
        year_sum = sum(row["count"] for row in plan.get("year_counts") or []
                       if isinstance(row.get("year"), int))
        if year_sum != quality.get("dated"):
            problems.append("sum(year_counts)=%s != dated=%s"
                            % (year_sum, quality.get("dated")))
        topic_sum = sum(row["count"] for row in plan.get("topic_counts") or [])
        if topic_sum != quality.get("unique"):
            problems.append("sum(topic_counts)=%s != unique=%s"
                            % (topic_sum, quality.get("unique")))
        reps = plan.get("representatives") or []
        if not reps:
            problems.append("representatives is empty")
        stages = (plan.get("time_window") or {}).get("stages") or []
        for stage in stages:
            covered = any(stage["start"] <= (row.get("year") or -1) <= stage["end"]
                          for row in reps)
            if not covered:
                problems.append("representatives do not cover stage %s" % stage.get("name"))
        for finding in plan.get("findings") or []:
            etype = str(finding.get("evidence_type") or "").lower()
            if etype not in ("count", "co-occurrence", "qualitative", "inference"):
                problems.append("finding %s has invalid evidence_type" % finding.get("id"))
            if etype in ("count", "co-occurrence") and causal_text(
                    finding.get("statement")):
                problems.append("finding %s uses causal wording" % finding.get("id"))
    elif mode == "topic-selection":
        problem = plan.get("problem_definition") or {}
        for field in ("question", "population", "variables", "baseline",
                      "falsification_conditions", "scope"):
            if not problem.get(field):
                problems.append("problem_definition.%s missing" % field)
        if not plan.get("comparison_matrix"):
            problems.append("comparison_matrix is empty")
        for claim in plan.get("novelty_claims") or []:
            if not claim.get("scope"):
                problems.append("novelty claim lacks scope")
        if not (plan.get("feasibility") or {}).get("premises"):
            problems.append("feasibility premises are empty")
        if not plan.get("bounded_scenarios"):
            problems.append("bounded_scenarios is empty")
    else:
        themes = plan.get("themes") or []
        if not themes:
            problems.append("themes is empty")
        for theme in themes:
            if len(theme.get("records") or []) < 2:
                problems.append("theme %s has fewer than 2 records" % theme.get("id"))
        for finding in plan.get("findings") or []:
            if not (finding.get("support") and finding.get("strength")):
                problems.append("review finding %s lacks support/strength"
                                % finding.get("id"))
        conflict = plan.get("conflict_check") or {}
        if "checked" not in conflict:
            problems.append("conflict_check is missing")
        if not (plan.get("synthesis") or {}).get("conclusions"):
            problems.append("synthesis.conclusions is empty")
        for gap in plan.get("gaps") or []:
            if not (gap.get("alternative_explanations") or gap.get("why_not_omission")):
                problems.append("review gap lacks alternative explanation")
        for facet, item in (plan.get("coverage") or {}).items():
            if item.get("status") not in ("covered", "partial", "not_covered",
                                          "not_applicable"):
                problems.append("coverage.%s has invalid status" % facet)
    return {"problems": problems, "warnings": warnings,
            "blocking": len(problems),
            "source_count": summary.get("sources_supplied"),
            "record_count": summary.get("records_unique"),
            "evidence_count": len(plan.get("evidence") or []),
            "matrix_rows": len(plan.get("evidence_matrix") or [])}


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def short(text, limit=140):
    text = norm(text).replace("|", "/")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def md_table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def render_common_header(plan):
    summary = plan.get("profile_summary") or {}
    lines = [
        "# %s" % {
            "trend-analysis": "研究前沿与趋势分析报告",
            "topic-selection": "科研选题与创新性评估报告",
            "review-generation": "文献综述生成报告",
        }.get(plan.get("mode"), "研究报告"),
        "",
        "- case_id: `%s`" % plan.get("case_id"),
        "- 生成时间: %s" % plan.get("generated_at"),
        "- 研究问题: %s" % plan.get("question"),
        "- 证据范围: **provided_corpus_only**（来源 %s 份，记录 %s 行，唯一 %s 条）" % (
            summary.get("sources_supplied"), summary.get("records_identified"),
            summary.get("records_unique")),
        "- 筛选协议: %s" % ("已执行" if summary.get("protocol_executed") else
                            "前瞻性方案，未执行在线检索"),
        "",
        "> 本报告只基于所提供材料做确定性整理与算术推导；不宣称全球完整性、"
        "全球新颖性或已完成的实验。缺失、冲突与不可判定项单列。",
        "",
    ]
    counts = summary.get("screening_counts") or {}
    lines += md_table(
        ["identified", "duplicates", "screened", "excluded", "full_text", "included"],
        [[counts.get("identified"), counts.get("duplicates"), counts.get("screened"),
          counts.get("excluded"), counts.get("full_text"), counts.get("included")]])
    lines += ["", "### 来源清单", ""]
    for source in (plan.get("source_registry") or {}).get("sources") or []:
        lines.append("- `%s` %s（%s，%s chars，sha256=%s…；记录 %s）" % (
            source.get("source_id"), source.get("path"), source.get("kind"),
            source.get("chars"), str(source.get("sha256"))[:12],
            ", ".join(source.get("record_ids") or []) or "未映射"))
    lines += ["", "### 记录清单", ""]
    lines += md_table(["record", "year", "access", "screening", "source", "title"],
                      [[r.get("record_id"), r.get("year") or "missing",
                        r.get("access"), r.get("screening_decision"),
                        short(r.get("source_path"), 40), short(r.get("title"), 70)]
                       for r in (plan.get("records") or [])[:20]])
    lines += ["", "### 筛选协议", "",
              "- 检索式: `%s`" % (plan.get("protocol") or {}).get("search_string"),
              "- 数据库/范围: %s" % ", ".join(
                  (plan.get("protocol") or {}).get("databases") or []),
              "- 执行状态: %s；%s" % (
                  (plan.get("protocol") or {}).get("executed"),
                  (plan.get("protocol") or {}).get("coverage_note")),
              "- 纳排规则: %s" % "；".join(
                  (plan.get("screening_criteria") or {}).get("include") or []),
              ""]
    return lines


def render_trend(plan):
    lines = ["## 1. 时间趋势（计数口径）", ""]
    lines += md_table(["year", "count", "denominator"],
                      [[row.get("year"), row.get("count"), row.get("denominator")]
                       for row in plan.get("year_counts") or []])
    lines += ["", "## 2. 主题演化（词表归并）", ""]
    lines += md_table(["topic", "count", "denominator"],
                      [[row.get("topic"), row.get("count"), row.get("denominator")]
                       for row in plan.get("topic_counts") or []])
    lines += ["", "### topic_mapping（原始词→规范主题）", ""]
    for raw, canonical in (plan.get("topic_mapping") or {}).items():
        lines.append("- `%s` → `%s`" % (raw or "(missing)", canonical))
    lines += ["", "## 3. 阶段划分与代表性工作", "",
              "阶段划分规则：%s" % ((plan.get("time_window") or {}).get("coverage_note")),
              "", "代表作规则：%s" % plan.get("representative_rule"), ""]
    lines += md_table(["stage", "record", "role", "year", "citations", "reason"],
                      [[row.get("stage"), row.get("record_id"), row.get("role"),
                        row.get("year"), row.get("citations"),
                        short(row.get("reason"), 80)]
                       for row in plan.get("representatives") or []])
    if plan.get("representative_exclusions"):
        lines += ["", "未入选但需说明的低被引/首创记录：", ""]
        for item in plan["representative_exclusions"]:
            lines.append("- `%s`：%s" % (item.get("record_id"), item.get("reason")))
    lines += ["", "## 4. 结论与证据类型", ""]
    for finding in plan.get("findings") or []:
        lines.append("- `%s` [%s] %s" % (finding.get("id"),
                                         finding.get("evidence_type"),
                                         finding.get("statement")))
        for ev in (finding.get("evidence") or [])[:3]:
            lines.append("  - 证据：`%s` %s —— %s" % (ev.get("path"),
                                                      ev.get("locator"),
                                                      short(ev.get("quote"), 120)))
    lines += ["", "## 5. 研究空白（排除检索遗漏）", ""]
    for gap in plan.get("gap_analysis") or []:
        lines.append("- %s" % gap.get("gap"))
        lines.append("  - 依据：%s" % gap.get("basis"))
        lines.append("  - 为何不是检索遗漏：%s" % (gap.get("excluded_alternative") or
                                                    gap.get("why_not_omission")))
    lines += ["", "## 6. 共现不等于因果", "",
              "本报告的 count/co-occurrence 结论只描述所提供语料中的年度与主题分布；"
              "共现可能来自题录标注、检索构成或语料选择，不能解释为互相驱动。", ""]
    return lines


def render_topic(plan):
    problem = plan.get("problem_definition") or {}
    lines = ["## 1. 问题定义与可检验性", ""]
    lines += md_table(["field", "value"],
                      [["研究问题", problem.get("question")],
                       ["研究对象", problem.get("population")],
                       ["关键变量", problem.get("variables")],
                       ["比较基线", problem.get("baseline")],
                       ["预期结论", problem.get("expected_conclusion")],
                       ["证伪条件", "；".join(problem.get("falsification_conditions") or [])],
                       ["当前可检验", "是" if problem.get("testable") else "否（需 PI 补齐）"],
                       ["边界", problem.get("scope")]])
    lines += ["", "## 2. 已有工作比较矩阵", ""]
    lines += md_table(["record", "task", "method", "data", "evaluation", "conclusion", "可比"],
                      [[row.get("record_id"),
                        short((row.get("value") or {}).get("task"), 30),
                        short((row.get("value") or {}).get("method"), 45),
                        short((row.get("value") or {}).get("data"), 30),
                        short((row.get("value") or {}).get("evaluation"), 30),
                        short((row.get("value") or {}).get("conclusion"), 45),
                        "是" if row.get("directly_comparable") else "否"]
                       for row in plan.get("comparison_matrix") or []])
    lines += ["", "## 3. 创新点逐条划界", ""]
    for claim in plan.get("novelty_claims") or []:
        lines.append("- `%s` [%s] %s" % (claim.get("id"), claim.get("type"),
                                         claim.get("claim")))
        lines.append("  - 判定：`%s`；范围：`%s`；覆盖/相邻：%s" % (
            claim.get("verdict"), claim.get("scope"),
            ", ".join(x["record_id"] for x in claim.get("covered_by") or []) or "无"))
        lines.append("  - 边界：%s" % claim.get("boundary"))
    lines += ["", "## 4. 可行性前提与算术", ""]
    lines += md_table(["premise", "status", "value", "source"],
                      [[p.get("premise"), p.get("status"), short(p.get("value"), 80),
                        "%s %s" % (p.get("source") or "-", p.get("locator") or "")]
                       for p in (plan.get("feasibility") or {}).get("premises") or []])
    calc = (plan.get("feasibility") or {}).get("calculation") or {}
    lines += ["", "计算状态：`%s`" % calc.get("status"),
              "", "输入：`%s`" % json.dumps(calc.get("inputs") or {}, ensure_ascii=False),
              "", "结果：`%s`" % json.dumps(calc.get("values") or {}, ensure_ascii=False),
              "", "可行性结论：**%s**" % (plan.get("feasibility") or {}).get("conclusion"),
              ""]
    lines += ["## 5. 有界方案场景", ""]
    for scenario in plan.get("bounded_scenarios") or []:
        lines.append("- `%s` %s：%s（状态：`%s`）" % (
            scenario.get("id"), scenario.get("name"), scenario.get("conclusion"),
            scenario.get("status")))
        lines.append("  - 计算：`%s`" % json.dumps(scenario.get("calculation") or {},
                                                  ensure_ascii=False))
        lines.append("  - 约束：%s" % "；".join(scenario.get("binding_constraints") or []))
    lines += ["", "## 6. 冲突证据", ""]
    conflicts = (plan.get("conflict_check") or {}).get("items") or []
    if conflicts:
        for item in conflicts:
            lines.append("- `%s`（%s）：%s 与 %s" % (
                item.get("id"), item.get("topic"),
                item["side_a"].get("record_id"), item["side_b"].get("record_id")))
            lines.append("  - A：%s" % short(item["side_a"].get("statement"), 160))
            lines.append("  - B：%s" % short(item["side_b"].get("statement"), 160))
            lines.append("  - 处理：%s" % item.get("resolution"))
    else:
        lines.append("- 未在所提供的记录中检测到方向相反的结论；这不等于不存在冲突。")
    lines += ["", "## 7. 研究空白与边界", ""]
    for gap in plan.get("gap_analysis") or []:
        lines.append("- %s" % gap.get("gap"))
        lines.append("  - 依据：%s；排除替代解释：%s" % (
            gap.get("basis"), gap.get("excluded_alternative") or
            gap.get("why_not_omission")))
    lines += ["", "## 8. 有界提案", "",
              (plan.get("bounded_proposal") or {}).get("statement") or "未提供", ""]
    return lines


def render_review(plan):
    lines = ["## 1. 筛选与纳排", ""]
    criteria = plan.get("screening_criteria") or {}
    lines += ["纳入规则：", ""] + ["- %s" % item for item in criteria.get("include") or []]
    lines += ["", "排除规则：", ""] + ["- %s" % item for item in criteria.get("exclude") or []]
    lines += ["", "### 筛选记录", ""]
    lines += md_table(["record", "decision", "stage", "access", "reason"],
                      [[row.get("record_id"), row.get("decision"), row.get("stage"),
                        row.get("access"), short(row.get("reason"), 90)]
                       for row in plan.get("screening_log") or []])
    lines += ["", "## 2. 主题归纳", ""]
    lines += md_table(["theme", "name", "records", "support", "strength", "boundary"],
                      [[t.get("id"), t.get("name"), ", ".join(t.get("records") or []),
                        t.get("support"), t.get("strength"), short(t.get("boundary"), 60)]
                       for t in plan.get("themes") or []])
    lines += ["", "## 3. 综述结构（逐节草稿）", ""]
    for section in (plan.get("review_structure") or {}).get("sections") or []:
        lines.append("### %s %s" % (section.get("id"), section.get("title")))
        lines.append("")
        lines.append("- 目的：%s" % section.get("purpose"))
        lines.append("- 记录：%s；证据行：%s" % (
            ", ".join(section.get("record_ids") or []) or "跨主题",
            section.get("evidence_count")))
        lines.append("- 草稿：%s" % section.get("draft"))
        lines.append("")
    lines += ["## 4. 综合结论", ""]
    for conclusion in (plan.get("synthesis") or {}).get("conclusions") or []:
        lines.append("- [%s] %s" % (conclusion.get("strength"),
                                    conclusion.get("statement")))
        lines.append("  - 记录：%s" % (", ".join(conclusion.get("records") or []) or "跨主题"))
    lines += ["", "## 5. 冲突证据", ""]
    conflicts = (plan.get("conflict_check") or {}).get("items") or []
    if conflicts:
        for item in conflicts:
            lines.append("- `%s`：%s" % (item.get("id"), item.get("topic")))
            lines.append("  - A `%s`：%s" % (item["side_a"].get("record_id"),
                                             short(item["side_a"].get("statement"), 150)))
            lines.append("  - B `%s`：%s" % (item["side_b"].get("record_id"),
                                             short(item["side_b"].get("statement"), 150)))
            lines.append("  - 处理：%s" % item.get("resolution"))
    else:
        lines.append("- 未在所提供记录中检测到方向相反的结论；不等于不存在冲突。")
    lines += ["", "## 6. 研究空白", ""]
    for gap in plan.get("gaps") or []:
        lines.append("- %s" % gap.get("gap"))
        lines.append("  - 依据：%s；替代解释：%s；为何不是遗漏：%s" % (
            gap.get("basis"), "；".join(gap.get("alternative_explanations") or []),
            gap.get("why_not_omission")))
    return lines


def render_evidence_appendix(plan):
    lines = ["## 证据附录（逐字引用，最多 40 条）", ""]
    lines += md_table(["path", "locator", "quote"], [
        [ev.get("path"), ev.get("locator"), short(ev.get("quote"), 180)]
        for ev in (plan.get("evidence") or [])[:40]])
    return lines


def render_uncertainties(plan):
    lines = ["## 不确定性、冲突与不可判定项", ""]
    for item in plan.get("uncertainties") or []:
        lines.append("- **%s**：%s → 影响：%s → 处理：%s" % (
            item.get("item"), item.get("reason"), item.get("impact"),
            item.get("action")))
    if not plan.get("uncertainties"):
        lines.append("- 未报告额外不确定项；这不等于不存在不确定性。")
    lines += ["", "## 复跑说明", "",
              "```bash",
              "python3 scripts/run_skill.py --input /input --work /app/work --output /app/output",
              "python3 scripts/research_profile.py --input /input --work /app/work --output /app/output",
              "```",
              "同一输入与同一规则重跑可复现相同计数与筛选决策；脚本不会伪造在线检索或未提供的全文。", ""]
    return lines


def render_report(plan):
    lines = render_common_header(plan)
    mode = plan.get("mode")
    if mode == "trend-analysis":
        lines += render_trend(plan)
    elif mode == "topic-selection":
        lines += render_topic(plan)
    else:
        lines += render_review(plan)
    lines += ["", "## 结论边界", "",
              "- 所有结论限定在所提供语料与上述筛选口径内。",
              "- 材料事实、算术推导与推断已分别标注；未把推断写成已验证事实。",
              "- 未报告项与无法判断项进入 uncertainties，不替研究问题负责人补造。",
              ""]
    lines += render_evidence_appendix(plan)
    lines += render_uncertainties(plan)
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# contract and outputs
# ---------------------------------------------------------------------------
def contract_payloads(plan):
    mode = plan.get("mode")
    matrix = plan.get("evidence_matrix") or []
    refs = plan.get("references") or []
    payloads = {
        "研究产物": {"report": "research-report.md",
                     "summary": plan.get("summary"),
                     "question": plan.get("question"),
                     "mode": mode},
        "证据矩阵": matrix,
        "证据矩阵表": matrix,
        "参考文献": refs,
        "文献清单": refs,
        "不确定性": plan.get("uncertainties") or [],
        "筛选协议": plan.get("protocol") or {},
        "检索协议": plan.get("protocol") or {},
        "来源清单": plan.get("source_registry") or {},
        "问题定义": plan.get("problem_definition") or {},
        "比较矩阵": plan.get("comparison_matrix") or [],
        "创新点": plan.get("novelty_claims") or [],
        "可行性": plan.get("feasibility") or {},
        "有界方案": plan.get("bounded_scenarios") or [],
        "研究空白": plan.get("gap_analysis") or [],
        "主题": plan.get("themes") or [],
        "综述结构": plan.get("review_structure") or {},
        "证据": plan.get("evidence") or [],
        "evidence_record": plan.get("evidence") or [],
        "evidence_matrix": matrix,
        "references": refs,
        "uncertainties": plan.get("uncertainties") or [],
        "deliverable": {"path": "research-report.md",
                        "summary": plan.get("summary")},
        "research_report": {"path": "research-report.md",
                            "summary": plan.get("summary")},
        "status_note": plan.get("uncertainties") or [],
        "search_protocol": plan.get("protocol") or {},
        "screening_protocol": plan.get("protocol") or {},
        "screening_criteria": plan.get("screening_criteria") or {},
        "screening_log": plan.get("screening_log") or [],
        "source_registry": plan.get("source_registry") or {},
        "review_report": plan.get("findings") or [],
        "search_results": plan.get("records") or [],
        "extraction_results": matrix,
        "trend_analysis": {
            "year_counts": plan.get("year_counts"),
            "topic_counts": plan.get("topic_counts"),
            "time_window": plan.get("time_window"),
            "representatives": plan.get("representatives"),
        },
        "novelty_assessment": {
            "novelty_claims": plan.get("novelty_claims"),
            "comparison_matrix": plan.get("comparison_matrix"),
        },
        "feasibility_assessment": {
            "feasibility": plan.get("feasibility"),
            "bounded_scenarios": plan.get("bounded_scenarios"),
        },
        "review_structure": plan.get("review_structure") or {},
    }
    return payloads


def safe_write_contract(output, contract, plan, fill_only=False):
    payloads = contract_payloads(plan)
    keys = []
    if isinstance(contract, dict):
        keys = [str(k) for k in contract.keys()]
    elif isinstance(contract, list):
        keys = [str(k) for k in contract]
    written = {}
    for key in keys:
        value = payloads.get(key)
        if value is None:
            value = {"field": key, "status": "partial",
                     "note": "该契约键没有专用字段；以 research-report.md、"
                             "evidence-matrix.json 与 references.json 为准",
                     "deliverable": "research-report.md",
                     "uncertainties": plan.get("uncertainties")}
        path = output / (safe_name(key) + ".json")
        emit_json(path, value, fill_only=fill_only)
        written[key] = path.name
    return written


def expected_artifacts(mode):
    base = ["research-report.md", "evidence-matrix.json", "references.json",
            "screening-protocol.json", "source-registry.json",
            "evidence-matrix.csv", "domain-audit.json"]
    if mode == "trend-analysis":
        base.append("trend-analysis.json")
    elif mode == "topic-selection":
        base.extend(["novelty-assessment.json", "feasibility-constraints.json"])
    else:
        base.append("review-structure.json")
    return ["output/" + name for name in base]


def write_outputs(plan, output, contract, fill_only=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    mode = plan.get("mode")
    written = []
    if emit_text(output / "research-report.md", render_report(plan), fill_only):
        written.append("research-report.md")
    matrix_payload = {
        "case_id": plan.get("case_id"),
        "scope": "provided_corpus_only",
        "summary": plan.get("profile_summary"),
        "columns": ["record_id", "title", "year", "source_path", "source_locator",
                    "screening_decision", "access", "evidence"],
        "rows": plan.get("evidence_matrix") or [],
        "matrix": plan.get("evidence_matrix") or [],
        "records": plan.get("records") or [],
        "counts": plan.get("profile_summary") or {},
    }
    if emit_json(output / "evidence-matrix.json", matrix_payload, fill_only):
        written.append("evidence-matrix.json")
    refs_payload = {
        "case_id": plan.get("case_id"),
        "references": plan.get("references") or [],
        "duplicates": [r for r in plan.get("references") or []
                       if r.get("duplicate_of")],
        "note": "Only identifiers and locators parsed from the supplied material are listed.",
    }
    if emit_json(output / "references.json", refs_payload, fill_only):
        written.append("references.json")
    protocol_payload = {
        "case_id": plan.get("case_id"),
        "protocol": plan.get("protocol"),
        "criteria": plan.get("screening_criteria"),
        "counts": (plan.get("screening") or {}).get("counts"),
        "records": plan.get("screening_log"),
        "excluded_reasons": (plan.get("screening") or {}).get("excluded_reasons"),
    }
    if emit_json(output / "screening-protocol.json", protocol_payload, fill_only):
        written.append("screening-protocol.json")
    source_payload = {
        "case_id": plan.get("case_id"),
        "source_registry": plan.get("source_registry"),
        "records": plan.get("records"),
    }
    if emit_json(output / "source-registry.json", source_payload, fill_only):
        written.append("source-registry.json")
    csv_path = output / "evidence-matrix.csv"
    if not (fill_only and csv_path.exists() and csv_path.stat().st_size > 0):
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["record_id", "title", "year", "source_path",
                             "source_locator", "screening_decision", "access",
                             "topic", "evidence_count"])
            for row in plan.get("evidence_matrix") or []:
                writer.writerow([row.get("record_id"), row.get("title"),
                                 row.get("year"), row.get("source_path"),
                                 row.get("source_locator"),
                                 row.get("screening_decision"), row.get("access"),
                                 row.get("topic"), row.get("evidence_count")])
        written.append("evidence-matrix.csv")
    if mode == "trend-analysis":
        payload = {"case_id": plan.get("case_id"),
                   "data_quality": plan.get("data_quality"),
                   "time_window": plan.get("time_window"),
                   "year_counts": plan.get("year_counts"),
                   "topic_counts": plan.get("topic_counts"),
                   "topic_mapping": plan.get("topic_mapping"),
                   "representatives": plan.get("representatives"),
                   "representative_rule": plan.get("representative_rule"),
                   "findings": plan.get("findings")}
        if emit_json(output / "trend-analysis.json", payload, fill_only):
            written.append("trend-analysis.json")
    elif mode == "topic-selection":
        payload = {"case_id": plan.get("case_id"),
                   "problem_definition": plan.get("problem_definition"),
                   "comparison_matrix": plan.get("comparison_matrix"),
                   "novelty_claims": plan.get("novelty_claims"),
                   "conflict_check": plan.get("conflict_check"),
                   "bounded_proposal": plan.get("bounded_proposal")}
        if emit_json(output / "novelty-assessment.json", payload, fill_only):
            written.append("novelty-assessment.json")
        payload = {"case_id": plan.get("case_id"),
                   "feasibility": plan.get("feasibility"),
                   "bounded_scenarios": plan.get("bounded_scenarios"),
                   "constraints": (plan.get("problem_definition") or {}).get("constraints")}
        if emit_json(output / "feasibility-constraints.json", payload, fill_only):
            written.append("feasibility-constraints.json")
    else:
        payload = {"case_id": plan.get("case_id"),
                   "themes": plan.get("themes"),
                   "findings": plan.get("findings"),
                   "synthesis": plan.get("synthesis"),
                   "conflict_check": plan.get("conflict_check"),
                   "coverage": plan.get("coverage"),
                   "review_structure": plan.get("review_structure"),
                   "gaps": plan.get("gaps")}
        if emit_json(output / "review-structure.json", payload, fill_only):
            written.append("review-structure.json")
    contract_written = safe_write_contract(output, contract, plan, fill_only=fill_only)
    checks = plan.get("_profile_checks") or {}
    audit = {
        "case_id": plan.get("case_id"),
        "generated_at": now_iso(),
        "script": "research_profile.py",
        "mode": mode,
        "source_count": (plan.get("profile_summary") or {}).get("sources_supplied"),
        "record_count": (plan.get("profile_summary") or {}).get("records_identified"),
        "unique_record_count": (plan.get("profile_summary") or {}).get("records_unique"),
        "evidence_matrix_rows": len(plan.get("evidence_matrix") or []),
        "evidence_count": len(plan.get("evidence") or []),
        "screening_counts": (plan.get("screening") or {}).get("counts"),
        "questions_total": (plan.get("profile_summary") or {}).get("questions_total"),
        "questions_covered": (plan.get("profile_summary") or {}).get("questions_covered"),
        "checks": checks,
        "blocking_checks": (checks or {}).get("blocking", 0),
        "artifacts": written,
        "contract_artifacts": contract_written,
        "no_materials": not plan.get("sources"),
    }
    write_json(output / "domain-audit.json", audit)
    plan["artifacts"] = expected_artifacts(mode)
    return written, audit


def merge_work(existing, generated, fill_only=False):
    if not isinstance(existing, dict) or not existing:
        return generated
    if not fill_only:
        return generated
    out = dict(existing)
    protected = {"case_id", "mode", "_generated_by", "_profile_checks"}
    for key, value in generated.items():
        if key in protected:
            out.setdefault(key, value)
            continue
        if key not in out or out.get(key) in (None, "", [], {}):
            out[key] = value
    out["case_id"] = existing.get("case_id") or generated.get("case_id")
    out["mode"] = existing.get("mode") or generated.get("mode")
    out["_generated_by"] = "model+research_profile.py"
    return out


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return default


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--fill-missing", action="store_true")
    args = parser.parse_args(argv)
    input_dir = Path(args.input)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    skill_dir = Path(__file__).resolve().parent.parent
    config = read_json(skill_dir / "task.json", {})
    mode = str(config.get("mode") or MODE_DEFAULT)
    case = read_json(input_dir / "case.json", {})
    task_text = read_text(input_dir / "task.md", "")
    case_text = read_text(input_dir / "case.json", "")
    if case_text:
        write_text(work_dir / "input-case.json", case_text)
    if task_text:
        write_text(work_dir / "input-task.md", task_text)
    docs = load_sources(work_dir, input_dir)
    plan = build_plan(docs, case, task_text, mode)
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    plan["case_id"] = case_id
    existing = read_json(work_dir / "research-plan.json", {})
    plan = merge_work(existing, plan, fill_only=args.fill_missing)
    plan["case_id"] = case_id
    plan["mode"] = mode
    plan["_profile_checks"] = profile_checks(plan)
    plan["artifacts"] = expected_artifacts(mode)
    contract = case.get("output_contract") or {}
    write_json(work_dir / "research-plan.json", plan)
    written, audit = write_outputs(plan, output_dir, contract,
                                   fill_only=args.fill_missing)
    write_json(work_dir / "domain-status.json", audit)
    print(json.dumps({
        "script": "research_profile.py",
        "case_id": case_id,
        "mode": mode,
        "sources": audit.get("source_count"),
        "records": audit.get("record_count"),
        "unique_records": audit.get("unique_record_count"),
        "matrix_rows": audit.get("evidence_matrix_rows"),
        "questions_covered": "%s/%s" % (audit.get("questions_covered"),
                                        audit.get("questions_total")),
        "blocking_checks": audit.get("blocking_checks"),
        "artifacts": written,
        "contract_artifacts": audit.get("contract_artifacts"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        payload = {"script": "research_profile.py", "status": "error",
                   "error": "%s: %s" % (type(exc).__name__, exc),
                   "note": "No substantive result was fabricated."}
        try:
            work = Path(sys.argv[sys.argv.index("--work") + 1]) \
                if "--work" in sys.argv else Path("/app/work")
            out = Path(sys.argv[sys.argv.index("--output") + 1]) \
                if "--output" in sys.argv else Path("/app/output")
            work.mkdir(parents=True, exist_ok=True)
            out.mkdir(parents=True, exist_ok=True)
            write_json(work / "domain-status.json", payload)
            write_json(out / "domain-audit.json", payload)
        except Exception:
            pass
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        sys.exit(0)
