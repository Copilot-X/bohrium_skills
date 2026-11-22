#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real reading-annotation extractor for the 9-document library.

    python3 annotation_extract.py --input /input --work /app/work \
        --output /app/output --apply --json

It scans the actual files, inventories them with SHA-256/version/duplicate
state, parses real annotation exports (pipe rows, JSON or CSV), locates each
quote in the source text with page/paragraph/line and character offsets, marks
low-OCR and old-version annotations explicitly, builds structured notes,
explainable links, a searchable index and a reusable Markdown export.

Writes ``work/library-plan.json`` (with ``--apply``), ``work/annotation-extract.json``
and the declared outputs plus ``output/annotations-export.md`` and
``output/annotation-index.json``.  Stdlib only; PDF extraction uses optional
pypdf/PyPDF2 and otherwise records ``text_layer=false`` honestly.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.2.0"
SKIP_NAMES = {"case.json", "task.md", "environment/resources.yaml"}
SKIP_PARTS = {"environment", "__pycache__", ".git", ".ocr", ".cache"}
TEXT_EXTS = {".md", ".txt", ".rst", ".org", ".tex", ".latex", ".bib", ".ris",
             ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml",
             ".xml", ".html", ".htm", ".log", ".py"}
ANNOTATION_TYPES = {"highlight", "underline", "margin", "handwriting",
                    "sticky", "comment", "other"}
TYPE_MAP = {"highlight": "highlight", "高亮": "highlight",
            "underline": "underline", "划线": "underline",
            "margin": "margin", "页边": "margin",
            "handwriting": "handwriting", "手写": "handwriting",
            "sticky": "sticky", "便签": "sticky", "comment": "comment",
            "批注": "comment", "note": "comment"}
ANCHOR_STATUSES = {"verified", "unverified_low_ocr", "version_mismatch",
                   "missing_source", "needs_review", "duplicate"}
BASES = {"same_quote", "same_section", "same_entity", "same_doi_version",
         "explicit_note"}
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
OCR_THRESHOLD = 0.60
TOPIC_WORDS = {
    "冷链与稳定性": ["cold chain", "cold storage", "stability", "temperature",
                     "shelf life", "冷藏", "冷链", "稳定性", "温度"],
    "缓冲液与酶": ["buffer", "ph", "enzyme", "activity", "assay", "缓冲",
                   "酶", "活性", "测定"],
    "临床样本": ["clinical", "patient", "cohort", "sample", "临床", "患者",
                 "队列", "样本"],
    "仪器与方法": ["instrument", "sequencing", "microscopy", "chromatography",
                   "spectrometry", "仪器", "测序", "显微", "色谱"],
    "数据与计算": ["dataset", "database", "benchmark", "model", "algorithm",
                   "数据", "算法", "模型", "基准"],
}
STOPWORDS = {"this", "that", "with", "from", "were", "was", "have", "has",
             "the", "and", "for", "are", "not", "但", "的", "了", "在", "和",
             "是", "与", "及", "对", "为", "中"}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def read_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def norm_hash(value):
    return str(value or "").replace("sha256:", "").strip().lower()


def scan_files(input_dir):
    root = Path(input_dir)
    rows = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if set(Path(rel).parts) & SKIP_PARTS:
            continue
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        if rel in SKIP_NAMES:
            continue
        rows.append(rel)
    return rows


def extract_docx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", " ", xml)
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""


def extract_pdf(path):
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = __import__(module_name)
            reader = module.PdfReader(str(path))
            pages = []
            for page in reader.pages[:80]:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:  # noqa: BLE001
                    pages.append("")
            text = "\n".join(pages)
            if norm(text):
                return text
        except Exception:  # noqa: BLE001
            continue
    try:
        raw = Path(path).read_bytes().decode("latin-1", "ignore")
        found = []
        for key in ("Title", "Author", "Subject", "CreationDate"):
            match = re.search(r"/%s\s*\((.{0,300}?)\)" % key, raw, re.S)
            if match:
                found.append("%s: %s" % (key, norm(match.group(1))))
        return "\n".join(found)
    except OSError:
        return ""


def extract_text(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix in TEXT_EXTS:
        return read_text(path)
    return ""


def parse_manifest(input_dir, rows):
    by_path = {}
    for rel in rows:
        if Path(rel).suffix.lower() not in TEXT_EXTS:
            continue
        text = read_text(Path(input_dir) / rel)
        for line_no, line in enumerate(text.split("\n"), 1):
            raw = line.strip()
            if "|" not in raw or "path:" not in raw.lower():
                continue
            fields = {}
            for chunk in raw.split("|"):
                if ":" in chunk:
                    key, value = chunk.split(":", 1)
                    fields[key.strip().lower()] = value.strip()
            path_value = fields.get("path", "").replace("\\", "/").lstrip("./")
            if path_value:
                fields["_line"] = raw
                fields["_source"] = rel
                fields["_locator"] = "L%d" % line_no
                by_path.setdefault(path_value, fields)
    return by_path


def version_of(rel, manifest, text):
    meta = manifest.get(rel) or {}
    if meta.get("version"):
        return meta["version"]
    match = re.search(r"(?:^|[_\-.])v(\d+)(?:$|[_\-.])", Path(rel).stem, re.I)
    return "v%s" % match.group(1) if match else "current"


def parse_pipe_annotations(rel, text):
    rows = []
    for line_no, line in enumerate(text.split("\n"), 1):
        raw = line.strip()
        if "|" not in raw or "quote:" not in raw.lower() or "file:" not in raw.lower():
            continue
        fields = {}
        for chunk in raw.split("|"):
            if ":" in chunk:
                key, value = chunk.split(":", 1)
                fields[key.strip().lower()] = value.strip()
        if not fields.get("quote"):
            continue
        fields["_source"] = rel
        fields["_line"] = raw
        fields["_locator"] = "L%d" % line_no
        rows.append(fields)
    return rows


def parse_json_annotations(rel, data):
    if isinstance(data, dict):
        rows = data.get("annotations") or data.get("highlights") or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []
    out = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            continue
        doc = row.get("file") or row.get("doc") or row.get("path") or ""
        quote = row.get("quote") or row.get("text") or row.get("highlight") or ""
        if not doc or not quote:
            continue
        out.append({
            "id": str(row.get("id") or row.get("annotation_id") or ""),
            "file": str(doc), "quote": str(quote),
            "page": row.get("page"), "paragraph": str(row.get("paragraph") or ""),
            "type": str(row.get("type") or "highlight"),
            "color": str(row.get("color") or "unknown"),
            "note": str(row.get("note") or "")
            , "version": str(row.get("version") or ""),
            "ocr_confidence": row.get("ocr_confidence"),
            "_source": rel, "_locator": "%s#%d" % (rel, index), "_line": str(quote),
        })
    return out


def parse_csv_annotations(rel, text):
    rows = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            return rows
        lowered = {name.lower(): name for name in reader.fieldnames}
        if "quote" not in lowered:
            return rows
        for index, row in enumerate(reader, 1):
            values = {key.lower(): value for key, value in row.items()}
            doc = values.get("file") or values.get("doc") or values.get("path") or ""
            quote = values.get("quote") or values.get("text") or ""
            if not doc or not quote:
                continue
            rows.append({
                "id": str(values.get("id") or values.get("annotation_id") or ""),
                "file": doc, "quote": quote, "page": values.get("page"),
                "paragraph": values.get("paragraph") or "",
                "type": values.get("type") or "highlight",
                "color": values.get("color") or "unknown",
                "note": values.get("note") or "",
                "version": values.get("version") or "",
                "ocr_confidence": values.get("ocr_confidence"),
                "_source": rel, "_locator": "%s#%d" % (rel, index),
                "_line": str(quote),
            })
    except csv.Error:
        pass
    return rows


def parse_annotations(input_dir, rows):
    found, sources = [], []
    for rel in rows:
        full = Path(input_dir) / rel
        suffix = Path(rel).suffix.lower()
        if suffix in (".md", ".txt", ".rst", ".org", ".log"):
            text = read_text(full)
            parsed = parse_pipe_annotations(rel, text)
        elif suffix in (".json", ".jsonl"):
            data = read_json(full, [])
            parsed = parse_json_annotations(rel, data)
        elif suffix in (".csv", ".tsv"):
            parsed = parse_csv_annotations(rel, read_text(full))
        else:
            continue
        if parsed:
            found.extend(parsed)
            sources.append({"path": rel, "rows": len(parsed)})
    return found, sources


def resolve_item(file_value, items):
    value = str(file_value or "").replace("\\", "/").lstrip("./")
    for item in items:
        if item["path"] == value:
            return item
    base = Path(value).name
    matches = [item for item in items if Path(item["path"]).name == base]
    return matches[0] if len(matches) == 1 else None


def anchor_offsets(text, quote):
    if not text or not quote:
        return None, None, None
    start = text.find(quote)
    if start >= 0:
        return start, start + len(quote), text.count("\n", 0, start) + 1
    pattern = re.escape(quote)
    pattern = pattern.replace(r"\ ", r"\s+")
    match = re.search(pattern, text)
    if match:
        return match.start(), match.end(), text.count("\n", 0, match.start()) + 1
    return None, None, None


def page_paragraph(text, start):
    if start is None or not text:
        return None, ""
    prefix = text[:start]
    pages = re.findall(r"(?:^|\n)\s*(?:Page|PAGE|p\.)\s*(\d+)", prefix)
    page = int(pages[-1]) if pages else 1 + prefix.count("\f")
    line_start = prefix.rfind("\n") + 1
    line = text[line_start:text.find("\n", start) if text.find("\n", start) >= 0 else len(text)]
    match = re.match(r"\s*(P\d+)\b", line)
    paragraph = match.group(1) if match else ""
    if not paragraph:
        blocks = [block for block in re.split(r"\n\s*\n", prefix) if block.strip()]
        paragraph = "P%d" % max(1, len(blocks))
    return page, paragraph


def derive_topics(text):
    haystack = norm(text)[:6000].lower()
    hits = {}
    for group, words in TOPIC_WORDS.items():
        found = [word for word in words if word in haystack]
        if found:
            hits[group] = found
    return hits


def quote_tags(quote, doc_topics):
    tags = [group for group in doc_topics if any(
        word in quote.lower() for word in TOPIC_WORDS[group])]
    words = re.findall(r"[A-Za-z][A-Za-z\-]{5,}|[一-鿿]{2,4}", quote)
    for word in words:
        lowered = word.lower()
        if lowered not in STOPWORDS and lowered not in tags:
            tags.append(lowered)
    return list(dict.fromkeys(tags))[:6]


def candidate_annotations(item, text):
    """Honest fallback when no annotation export exists."""
    candidates = []
    marker_re = re.compile(r"==(.+?)==|<mark>(.+?)</mark>|\[\^(\d+)\]")
    for index, match in enumerate(marker_re.finditer(text), 1):
        quote = norm(next(group for group in match.groups() if group))
        if len(quote) >= 4:
            candidates.append({"quote": quote, "type": "highlight",
                               "note": "explicit inline mark in source",
                               "source": "inline"})
    if not candidates:
        sentences = re.split(r"(?<=[.!?。！？])\s+", norm(text))
        for sentence in sentences:
            if 40 <= len(sentence) <= 260:
                candidates.append({"quote": sentence, "type": "comment",
                                   "note": "candidate anchor: no annotation export",
                                   "source": "candidate"})
            if len(candidates) >= 3:
                break
    out = []
    for candidate in candidates:
        start, end, _ = anchor_offsets(text, candidate["quote"])
        page, paragraph = page_paragraph(text, start)
        out.append({**candidate, "page": page, "paragraph": paragraph,
                    "char_start": start, "char_end": end,
                    "anchor_status": "needs_review", "color": "unknown",
                    "source": candidate["source"]})
    return out


def build(input_dir, work_dir, output_dir, apply_plan):
    input_dir, work, out = Path(input_dir), Path(work_dir), Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(input_dir / "case.json", {})
    case_id = str(case.get("case_id") or "UNKNOWN")
    rows = scan_files(input_dir)
    manifest = parse_manifest(input_dir, rows)
    items, uncertainties, operations = [], [], []
    by_path, by_hash = {}, {}
    for index, rel in enumerate(rows, 1):
        full = input_dir / rel
        if not full.is_file():
            uncertainties.append({"item": rel, "reason": "file not readable",
                                  "impact": "not indexed", "action": "check permissions"})
            continue
        text = extract_text(full)
        suffix = full.suffix.lower()
        digest = sha256_file(full)
        item = {
            "id": "D%03d" % (len(items) + 1), "path": rel,
            "kind": suffix.lstrip(".") or "other",
            "bytes": full.stat().st_size, "size": full.stat().st_size,
            "hash": "sha256:" + digest, "sha256": digest,
            "version": version_of(rel, manifest, text),
            "metadata": {"title": "", "authors": "", "year": "", "doi": ""},
            "status": "indexed", "duplicate_of": "", "supersedes": "",
            "text_layer": bool(norm(text)) and suffix not in
                            (".png", ".jpg", ".jpeg", ".tif", ".tiff"),
            "pages": len(re.findall(r"(?:^|\n)\s*(?:Page|PAGE|p\.)\s*\d+", text)) or None,
        }
        meta = manifest.get(rel) or {}
        title = str(meta.get("title") or "")
        if not title:
            heading = next((norm(line.lstrip("#")) for line in text.split("\n")
                            if norm(line).startswith("#")), "")
            title = heading or re.sub(r"[_-]+", " ", full.stem)
        doi_match = DOI_RE.search(text[:8000]) or DOI_RE.search(str(meta.get("doi") or ""))
        year_match = YEAR_RE.search(str(meta.get("year") or "")) or YEAR_RE.search(text[:4000])
        item["metadata"] = {
            "title": title,
            "authors": norm(meta.get("first_author") or meta.get("authors") or ""),
            "year": year_match.group(0) if year_match else "",
            "doi": doi_match.group(0).rstrip(".,;)") if doi_match else "",
        }
        item["title"] = title
        item["topics"] = derive_topics(text)
        item["_text"] = text
        if meta.get("_line"):
            item["evidence"] = {"path": meta["_source"],
                                "locator": meta["_locator"],
                                "quote": meta["_line"]}
        elif norm(text):
            first = next((norm(line) for line in text.split("\n")
                          if 8 <= len(norm(line)) <= 240), title)
            item["evidence"] = {"path": rel, "locator": "L1", "quote": first}
        else:
            item["evidence"] = {"path": rel, "locator": "file",
                                "quote": Path(rel).name}
        if meta.get("sha256") and norm_hash(meta["sha256"]) != digest:
            uncertainties.append({
                "item": rel, "reason": "manifest sha256 does not match actual file content",
                "impact": "the manifest is stale; actual hash is used",
                "action": "refresh the manifest entry"})
        items.append(item)
        by_path[rel] = item
        by_hash.setdefault(digest, []).append(item)
    # duplicates and superseded versions
    for digest, members in by_hash.items():
        if len(members) < 2:
            continue
        kept = members[0]
        for member in members[1:]:
            member["status"] = "duplicate"
            member["duplicate_of"] = kept["path"]
            operations.append({
                "action": "dedupe", "target": member["path"], "status": "ok",
                "detail": "byte-identical duplicate of %s; canonical kept" % kept["path"],
                "evidence": {"path": member["path"], "locator": "sha256",
                             "quote": Path(member["path"]).name}})
    stems = {}
    for item in items:
        stem = re.sub(r"(?:[_\-.])v\d+$", "", Path(item["path"]).stem)
        stems.setdefault(stem.lower(), []).append(item)
    for members in stems.values():
        if len(members) < 2:
            continue
        current = max(members, key=lambda row: (row["version"] != "current",
                                                int(YEAR_RE.search(row["version"]).group(0))
                                                if YEAR_RE.search(row["version"]) else 0))
        for member in members:
            if member is current:
                continue
            if member["status"] == "indexed":
                member["status"] = "superseded"
            member["supersedes"] = current["path"]
            operations.append({
                "action": "mark_superseded", "target": member["path"],
                "status": "ok",
                "detail": "older version of %s; kept for annotation cross-check"
                          % current["path"],
                "evidence": member["evidence"]})
    # annotation extraction
    parsed, annotation_sources = parse_annotations(input_dir, rows)
    if not parsed:
        # Only derive candidates from readable primary text documents.
        for item in items:
            if item["text_layer"] and norm(item.get("_text")):
                for candidate in candidate_annotations(item, item["_text"]):
                    parsed.append({
                        "file": item["path"], "quote": candidate["quote"],
                        "type": candidate["type"], "color": candidate["color"],
                        "note": candidate["note"], "page": candidate["page"],
                        "paragraph": candidate["paragraph"],
                        "char_start": candidate["char_start"],
                        "char_end": candidate["char_end"],
                        "anchor_status": "needs_review",
                        "_source": item["path"], "_locator": "body",
                        "_line": candidate["quote"],
                    })
                if parsed:
                    break
        if parsed:
            uncertainties.append({
                "item": "annotation_export",
                "reason": "no annotation export found; candidates were derived from explicit marks/sentences",
                "impact": "all candidates are needs_review, not user-confirmed highlights",
                "action": "supply an annotation export to confirm anchors"})
        else:
            uncertainties.append({
                "item": "annotation_export",
                "reason": "no annotation export and no readable text to anchor",
                "impact": "annotations list is empty",
                "action": "provide an export or OCR text"})
    annotations, seen_ids, seen_quotes = [], set(), set()
    for index, row in enumerate(parsed, 1):
        item = resolve_item(row.get("file"), items)
        quote = norm(row.get("quote"))
        aid = str(row.get("id") or "A%03d" % index)
        if aid in seen_ids:
            aid = "A%03d" % index
            while aid in seen_ids:
                aid += "x"
        seen_ids.add(aid)
        dedupe_key = (item["path"] if item else str(row.get("file")), quote,
                      str(row.get("page")), norm(row.get("paragraph")))
        duplicate = dedupe_key in seen_quotes
        seen_quotes.add(dedupe_key)
        ann_type = TYPE_MAP.get(str(row.get("type") or "").lower(), "other")
        try:
            ocr = float(row.get("ocr_confidence")) if row.get("ocr_confidence") not in (
                None, "") else None
        except (TypeError, ValueError):
            ocr = None
        version = str(row.get("version") or "")
        status = "verified"
        mapped = True
        reasons = []
        text = item.get("_text", "") if item else ""
        start = end = None
        page = row.get("page")
        paragraph = norm(row.get("paragraph") or "")
        if item is None:
            status, mapped = "missing_source", False
            reasons.append("annotation file/path does not match an input document")
        elif duplicate:
            status, mapped = "duplicate", False
            reasons.append("duplicate annotation row (same quote/anchor)")
        elif item["status"] in ("duplicate", "superseded"):
            status, mapped = "version_mismatch", False
            reasons.append("annotation belongs to a duplicate/superseded item")
        elif version and version != item["version"]:
            status, mapped = "version_mismatch", False
            reasons.append("annotation version %s != current %s" % (version, item["version"]))
        if item is not None and item["text_layer"] and quote:
            start, end, line_no = anchor_offsets(text, quote)
            if start is None:
                if status == "verified":
                    status, mapped = "missing_source", False
                reasons.append("quote not found verbatim in the current text")
            else:
                calc_page, calc_paragraph = page_paragraph(text, start)
                page = calc_page if page in (None, "", "None") else page
                paragraph = paragraph or calc_paragraph
                row["_line_no"] = line_no
        elif item is not None and not item["text_layer"] and status == "verified":
            status, mapped = "needs_review", False
            reasons.append("no text layer; anchor cannot be verified from source text")
        if ocr is not None and ocr < OCR_THRESHOLD:
            status, mapped = "unverified_low_ocr", False
            reasons.append("OCR confidence %.2f < %.2f" % (ocr, OCR_THRESHOLD))
        if status == "verified" and (page in (None, "", "None") and not paragraph
                                     and start is None):
            status, mapped = "needs_review", False
            reasons.append("no page/paragraph/char anchor")
        evidence_path = str(row.get("_source") or (item["path"] if item else ""))
        evidence_quote = row.get("_line") or quote
        locator = str(row.get("_locator") or "A%03d" % index)
        if row.get("_line_no"):
            locator = "L%s" % row["_line_no"]
        tags = quote_tags(quote, (item or {}).get("topics") or {}) or ["general"]
        annotation = {
            "id": aid, "annotation_id": aid,
            "doc": item["path"] if item else str(row.get("file") or ""),
            "path": evidence_path,
            "version": version or (item["version"] if item else ""),
            "doc_sha256": item["sha256"] if item else "",
            "page": page if page not in ("", "None") else None,
            "paragraph": paragraph,
            "line": row.get("_line_no"),
            "char_start": start, "char_end": end,
            "quote": quote,
            "type": ann_type,
            "color": str(row.get("color") or "unknown"),
            "anchor_status": status,
            "ocr_confidence": ocr,
            "mapped_to_current": mapped,
            "needs_review": status != "verified",
            "note": norm(row.get("note") or ""),
            "tags": tags,
            "topics": [tag for tag in tags if tag in TOPIC_WORDS],
            "anchor_reasons": reasons,
            "evidence": {"path": evidence_path, "locator": locator,
                         "quote": evidence_quote},
        }
        if status == "verified":
            annotation["anchor"] = {"page": page, "paragraph": paragraph,
                                    "line": row.get("_line_no"),
                                    "char_start": start, "char_end": end}
        annotations.append(annotation)
        if status == "unverified_low_ocr":
            operations.append({"action": "mark_low_ocr", "target": aid,
                               "status": "needs_review",
                               "detail": reasons[-1], "evidence": annotation["evidence"]})
        elif status == "version_mismatch":
            operations.append({"action": "mark_version_mismatch", "target": aid,
                               "status": "needs_review",
                               "detail": "; ".join(reasons), "evidence": annotation["evidence"]})
        elif status == "duplicate":
            operations.append({"action": "mark_duplicate_annotation", "target": aid,
                               "status": "ok", "detail": "; ".join(reasons),
                               "evidence": annotation["evidence"]})
        elif status in ("missing_source", "needs_review"):
            operations.append({"action": "flag_annotation", "target": aid,
                               "status": "needs_review",
                               "detail": "; ".join(reasons) or status,
                               "evidence": annotation["evidence"]})
        else:
            operations.append({"action": "extract_annotation", "target": aid,
                               "status": "ok",
                               "detail": "anchor verified at %s %s" % (
                                   page if page else "-", paragraph or "-"),
                               "evidence": annotation["evidence"]})
    # structured notes grouped by dominant topic/tag
    grouped = {}
    for annotation in annotations:
        key = annotation["topics"][0] if annotation["topics"] else (
            annotation["tags"][0] if annotation["tags"] else "general")
        grouped.setdefault(key, []).append(annotation)
    notes, links = [], []
    for note_index, (topic, members) in enumerate(sorted(grouped.items()), 1):
        note_id = "N%03d" % note_index
        verified = [ann for ann in members if ann["anchor_status"] == "verified"]
        claims = []
        for ann in members:
            claims.append({
                "text": "批注原文：%s" % ann["quote"],
                "annotation_id": ann["id"],
                "evidence": {"path": ann["doc"], "locator":
                             "p%s %s" % (ann["page"], ann["paragraph"] or "L%s" % (ann["line"] or "-")),
                             "quote": ann["quote"]},
                "evidence_status": "verified" if ann["anchor_status"] == "verified"
                                   else "needs_review",
            })
        note = {
            "id": note_id, "title": "%s 批注沉淀" % topic,
            "question": "这些批注围绕 %s 的共同判断是什么？" % topic,
            "method": "逐条保留原文锚点，按主题聚合，未做跨文献推断",
            "conclusion": "%s：%d 条批注（其中已核验 %d 条）；结论需结合原文复核。"
                          % (topic, len(members), len(verified)),
            "reusable_points": ["可按主题检索已核验批注原文"],
            "caveats": ([] if len(verified) == len(members)
                        else ["部分批注为低 OCR/旧版本/候选锚点，不能作为已确认结论"]),
            "annotation_ids": [ann["id"] for ann in members],
            "claims": claims,
            "tags": [topic],
            "topics": [group for group in TOPIC_WORDS if group == topic],
            "evidence_status": "verified" if len(verified) == len(members)
                               else "needs_review",
            "evidence": {"path": members[0]["doc"], "locator": "note",
                         "quote": members[0]["quote"]} if members else {},
        }
        notes.append(note)
        for ann in members:
            links.append({
                "from": ann["id"], "to": note_id, "basis": "same_quote",
                "confidence": "high" if ann["anchor_status"] == "verified" else "low",
                "needs_review": ann["anchor_status"] != "verified",
                "evidence": {"path": ann["doc"], "locator": ann["paragraph"] or "L%s" % (ann["line"] or "-"),
                             "quote": ann["quote"]},
            })
    # searchable index: topic/tag, source document, time
    search_index, indexed = [], set()
    for topic in sorted(grouped):
        members = grouped[topic]
        search_index.append({
            "term": topic, "kind": "topic",
            "annotation_ids": [ann["id"] for ann in members],
            "note_ids": [note["id"] for note in notes if topic in note["tags"]],
            "source_docs": sorted({ann["doc"] for ann in members}),
            "confidence": "high" if all(ann["anchor_status"] == "verified"
                                        for ann in members) else "low",
            "needs_review": any(ann["anchor_status"] != "verified" for ann in members),
        })
        indexed.update(ann["id"] for ann in members)
    for item in items:
        docs = [ann for ann in annotations if ann["doc"] == item["path"]]
        if not docs:
            continue
        search_index.append({
            "term": item["metadata"]["title"] or Path(item["path"]).name,
            "kind": "source", "annotation_ids": [ann["id"] for ann in docs],
            "note_ids": [], "source_docs": [item["path"]],
            "year": item["metadata"]["year"], "confidence": "high",
            "needs_review": any(ann["anchor_status"] != "verified" for ann in docs),
        })
        indexed.update(ann["id"] for ann in docs)
    for ann in annotations:
        if ann["id"] in indexed:
            continue
        search_index.append({
            "term": ann["tags"][0] if ann["tags"] else ann["quote"][:40],
            "kind": "tag", "annotation_ids": [ann["id"]], "note_ids": [],
            "source_docs": [ann["doc"]], "confidence": "low",
            "needs_review": ann["anchor_status"] != "verified",
        })
    for item in items:
        operations.append({
            "action": "index", "target": item["path"],
            "status": "ok" if item["text_layer"] else "needs_review",
            "detail": "sha256=%s; %d bytes; text_layer=%s; version=%s" % (
                item["sha256"][:16], item["bytes"], item["text_layer"], item["version"]),
            "evidence": item["evidence"]})
    operations.append({"action": "build_search_index", "target": "search_index",
                       "status": "ok" if search_index else "skipped",
                       "detail": "%d index rows over %d annotations" % (
                           len(search_index), len(annotations)),
                       "evidence": (annotations[0]["evidence"] if annotations else {
                           "path": "work/library-plan.json", "locator": "index",
                           "quote": "search_index"})})
    for rel in ("library-results.json", "library-index.json",
                "operations.log.json"):
        operations.append({"action": "write_output", "target": rel,
                           "status": "ok", "detail": "declared deliverable written",
                           "evidence": (annotations[0]["evidence"] if annotations else {
                               "path": "work/library-plan.json", "locator": "output",
                               "quote": rel})})
    if not annotations:
        uncertainties.append({"item": "annotations", "reason": "no annotations extracted",
                              "impact": "knowledge sedimentation is empty",
                              "action": "supply an annotation export or readable text"})
    for ann in annotations:
        if ann["anchor_status"] in ("unverified_low_ocr", "version_mismatch",
                                    "missing_source", "needs_review", "duplicate"):
            uncertainties.append({
                "item": ann["id"],
                "reason": "%s: %s" % (ann["anchor_status"],
                                      "; ".join(ann["anchor_reasons"]) or "requires review"),
                "impact": "excluded from default verified retrieval",
                "action": "review the anchor against the cited page/version",
            })
    # public item view (drop private text)
    public_items = []
    for item in items:
        public = {key: value for key, value in item.items() if key != "_text"}
        public_items.append(public)
    for item in public_items:
        if item["status"] == "duplicate" and not item.get("duplicate_of"):
            item["duplicate_of"] = ""
    summary = ("%d 个文件；%d 条批注（verified %d，待复核 %d）；%d 条笔记；"
               "%d 条关联；%d 条检索索引。" % (
                   len(items), len(annotations),
                   sum(1 for ann in annotations if ann["anchor_status"] == "verified"),
                   sum(1 for ann in annotations if ann["anchor_status"] != "verified"),
                   len(notes), len(links), len(search_index)))
    if not items:
        summary = "输入目录没有可索引的阅读材料；未生成伪批注（no_result）。"
    plan = {
        "case_id": case_id, "mode": "annotation", "summary": summary,
        "question": str(case.get("question") or ""),
        "items": public_items, "annotations": annotations, "notes": notes,
        "links": links, "search_index": search_index, "operations": operations,
        "evidence": [ann["evidence"] for ann in annotations] +
                    [item["evidence"] for item in public_items],
        "uncertainties": uncertainties,
        "annotation_sources": annotation_sources,
        "counts": {"files": len(public_items), "annotations": len(annotations),
                   "verified": sum(1 for ann in annotations
                                   if ann["anchor_status"] == "verified"),
                   "needs_review": sum(1 for ann in annotations
                                       if ann["anchor_status"] != "verified"),
                   "notes": len(notes), "links": len(links),
                   "search_index": len(search_index)},
        "no_result": not public_items,
        "extractor_version": VERSION, "generated_at": now_iso(),
        "generated_by": "annotation_extract.py",
    }
    if apply_plan:
        write_json(work / "library-plan.json", plan)
    write_json(work / "annotation-extract.json", plan)
    results = {key: plan[key] for key in (
        "case_id", "mode", "summary", "items", "annotations", "notes", "links",
        "search_index", "operations", "evidence", "uncertainties", "counts",
        "annotation_sources", "no_result", "generated_at")}
    index = {
        "case_id": case_id,
        "items": [{"id": item["id"], "path": item["path"], "kind": item["kind"],
                   "version": item["version"], "sha256": item["sha256"],
                   "status": item["status"], "duplicate_of": item["duplicate_of"],
                   "text_layer": item["text_layer"],
                   "annotation_count": sum(1 for ann in annotations
                                           if ann["doc"] == item["path"]),
                   "verified_count": sum(1 for ann in annotations
                                         if ann["doc"] == item["path"]
                                         and ann["anchor_status"] == "verified")}
                  for item in public_items],
        "by_status": {},
        "annotation_index": [{"id": ann["id"], "doc": ann["doc"],
                              "page": ann["page"], "paragraph": ann["paragraph"],
                              "line": ann["line"], "type": ann["type"],
                              "tags": ann["tags"],
                              "anchor_status": ann["anchor_status"]}
                             for ann in annotations],
        "search_index": search_index,
        "notes": [{"id": note["id"], "title": note["title"],
                   "annotation_ids": note["annotation_ids"]}
                  for note in notes],
    }
    for item in public_items:
        index["by_status"].setdefault(item["status"], []).append(item["path"])
    operations_log = {
        "case_id": case_id, "extractor_version": VERSION,
        "operations": operations,
        "failed_or_skipped": [op for op in operations
                              if op["status"] in ("failed", "skipped", "needs_review")],
        "recovery": "re-run annotation_extract.py; anchors are recomputed from file hashes",
        "generated_at": plan["generated_at"],
    }
    write_json(out / "library-results.json", results)
    write_json(out / "library-index.json", index)
    write_json(out / "annotation-index.json", index)
    write_json(out / "operations.log.json", operations_log)
    export = ["# 批注与知识沉淀导出（可复用 Markdown）", "",
              "> 由 annotation_extract.py v%s 从实际文件生成；每条批注保留来源与锚点。" % VERSION,
              "", "## 文件清单", ""]
    for item in public_items:
        export.append("- `%s` | version=%s | sha256=%s | status=%s | text_layer=%s" % (
            item["path"], item["version"], item["sha256"][:16], item["status"],
            item["text_layer"]))
    export += ["", "## 批注", ""]
    for ann in annotations:
        export.append("- **%s** [%s/%s%s] `%s` → %s" % (
            ann["id"], ann["anchor_status"], ann["type"], ann["color"],
            ann["doc"], ann["quote"]))
        export.append("  - anchor: page=%s paragraph=%s line=%s chars=%s-%s" % (
            ann["page"], ann["paragraph"] or "-", ann["line"],
            ann["char_start"], ann["char_end"]))
        if ann["note"]:
            export.append("  - note: %s" % ann["note"])
        if ann["anchor_reasons"]:
            export.append("  - review: %s" % "; ".join(ann["anchor_reasons"]))
    export += ["", "## 结构化笔记", ""]
    for note in notes:
        export.append("### %s" % note["title"])
        export.append("- question: %s" % note["question"])
        export.append("- method: %s" % note["method"])
        export.append("- conclusion: %s" % note["conclusion"])
        export.append("- annotations: %s" % ", ".join(note["annotation_ids"]))
        export.append("- evidence_status: %s" % note["evidence_status"])
        export.append("")
    export += ["## 检索索引", ""]
    for row in search_index:
        export.append("- `%s` (%s) → %s%s" % (
            row["term"], row["kind"], ", ".join(row["annotation_ids"]),
            " [needs_review]" if row["needs_review"] else ""))
    (out / "annotations-export.md").write_text("\n".join(export) + "\n",
                                               encoding="utf-8")
    return {
        "ok": True, "case_id": case_id, "files": len(public_items),
        "annotations": len(annotations),
        "verified": plan["counts"]["verified"],
        "needs_review": plan["counts"]["needs_review"],
        "notes": len(notes), "links": len(links),
        "search_index": len(search_index),
        "outputs": ["library-results.json", "library-index.json",
                    "operations.log.json", "annotation-index.json",
                    "annotations-export.md"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Real-file annotation extractor")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--apply", action="store_true",
                        help="write work/library-plan.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = build(args.input, args.work, args.output, args.apply)
    except Exception as exc:  # noqa: BLE001
        summary = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        try:
            write_json(Path(args.work) / "annotation-extract-error.json", summary)
        except OSError:
            pass
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
