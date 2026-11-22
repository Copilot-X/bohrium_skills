#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real-file classifier for the 9-document library (classification mode).

    python3 library_classify.py --input /input --work /app/work \
        --output /app/output --apply --materialize --json

It scans the actual files under ``--input`` (not a label list), extracts
title/author/year/DOI/aliases from the file body and any manifest-like
``| path: ...`` rows, assigns every material file exactly one taxonomy
category with a per-item confidence, route low-confidence/cross-topic items to
``review_queue``, proposes canonical names, detects byte-identical duplicates
and version groups, and records reversible operations and rollback.

Writes ``work/library-plan.json`` (with ``--apply``), the evidence ledger
``work/library-evidence.md``, and the three declared outputs:
``output/library-results.json``, ``output/library-index.json``,
``output/operations.log.json``.  ``--materialize`` performs real, reversible
copies under ``output/library/`` and records before/after SHA-256.

Stdlib only; PDF/DOCX extraction degrades honestly to ``text_layer=false``.
It never invents authors, years, DOIs or categories: a missing field stays
empty and lowers confidence instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2.1.0"
DEFAULT_THRESHOLD = 0.7
SKIP_NAMES = {"case.json", "task.md", "task-note.md", "taxonomy.md",
              "manifest.md", "readme.md", "instructions.md"}
SKIP_PARTS = {"environment", "__pycache__", ".git", ".ocr", ".cache"}
TEXT_EXTS = {".md", ".txt", ".rst", ".org", ".tex", ".latex", ".bib", ".ris",
             ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml",
             ".xml", ".html", ".htm", ".log", ".py", ".sql", ".ini"}
DATA_EXTS = {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".json", ".jsonl",
             ".h5", ".hdf5", ".nc", ".mat", ".npy", ".npz"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

CATEGORIES = ["期刊论文", "预印本", "会议论文", "学位论文", "补充材料",
              "数据集", "扫描件", "清单与索引", "笔记与手稿", "待复核"]
CRITERIA = {
    "期刊论文": "正文/清单含期刊名、卷期或页码，或 type=journal article；有 DOI 时更强",
    "预印本": "正文/清单/文件名明确标注 preprint、arXiv、bioRxiv、medRxiv、SSRN、Research Square",
    "会议论文": "出现 conference、proceedings、symposium、workshop 或 type=conference",
    "学位论文": "出现 thesis、dissertation、学位论文 或 type=thesis",
    "补充材料": "出现 supplementary、supporting information、appendix、附录、SI 或 type=supplement",
    "数据集": "扩展名为 csv/tsv/xlsx/json/parquet/h5/nc/mat 等，或 type=dataset",
    "扫描件": "图像扩展名，或出现 scan/scanned/OCR/无文本层 标记",
    "清单与索引": "文本中包含两条及以上 `| path: ...` 元数据行，属于批次清单/索引",
    "笔记与手稿": "叙述性文本/笔记/手稿，无期刊、会议、预印本或数据集信号",
    "待复核": "文件不可读、无标题且无任何类型信号，或顶层与次顶层判据得分差 < 0.12",
}

TYPE_HINTS = {
    "期刊论文": ["journal article", "journal", "research article", "original article",
                 "期刊", "卷", "issue", "volume", "pages"],
    "预印本": ["preprint", "pre-print", "arxiv", "biorxiv", "medrxiv", "ssrn",
               "research square", "预印本"],
    "会议论文": ["conference", "proceedings", "symposium", "workshop", "会议"],
    "学位论文": ["thesis", "dissertation", "学位论文"],
    "补充材料": ["supplementary", "supplement", "supporting information",
                 "appendix", "附录", "补充材料"],
    "数据集": ["dataset", "data set", "数据", "raw data", "measurements"],
    "扫描件": ["scan", "scanned", "ocr", "no text layer", "扫描", "无文本层"],
    "清单与索引": ["manifest", "inventory", "index", "清单", "索引"],
    "笔记与手稿": ["note", "notes", "manuscript", "draft", "memo", "笔记", "手稿"],
}

TOPIC_GROUPS = {
    "冷链与稳定性": ["cold chain", "cold-chain", "cold storage", "stability",
                     "shelf life", "temperature", "lyophil", "冷藏", "冷链",
                     "稳定性", "温度"],
    "缓冲液与酶": ["buffer", "ph", "enzyme", "activity", "assay", "缓冲",
                   "酶", "活性", "测定"],
    "临床样本": ["clinical", "patient", "cohort", "specimen", "临床", "患者",
                 "队列", "样本"],
    "仪器与方法": ["instrument", "spectrometer", "sequencing", "microscopy",
                   "chromatography", "spectrometry", "仪器", "测序", "显微",
                   "色谱"],
    "数据与计算": ["dataset", "database", "benchmark", "model", "algorithm",
                   "machine learning", "数据", "算法", "模型", "基准"],
    "材料与化学": ["catalyst", "nanoparticle", "polymer", "synthesis",
                   "membrane", "催化", "纳米", "高分子", "合成", "膜"],
}


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


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def slug(text, limit=72):
    text = BAD_CHARS.sub("", norm(text))
    text = re.sub(r"[^\w一-鿿.-]+", "-", text, flags=re.UNICODE)
    text = re.sub(r"-{2,}", "-", text).strip("-_.")
    return text[:limit].strip("-_.") or "untitled"


def ascii_key(text):
    text = re.sub(r"[^A-Za-z0-9]+", " ", str(text or "").lower()).strip()
    return re.sub(r"\s+", " ", text)


def scan_files(input_dir):
    rows = []
    root = Path(input_dir)
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
        if rel in ("case.json", "task.md", "environment/resources.yaml"):
            continue
        rows.append(rel)
    return rows


def item_paths(input_dir, rows):
    """Mirror fam_tracks._classify_input_paths: support files are not items."""
    return [rel for rel in rows if Path(rel).name.lower() not in SKIP_NAMES]


def extract_docx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return norm(re.sub(r"<[^>]+>", " ", xml))
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""


def extract_pdf(path):
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = __import__(module_name)
            reader = module.PdfReader(str(path))
            pages = []
            for page in reader.pages[:40]:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:  # noqa: BLE001
                    pages.append("")
            text = "\n".join(pages)
            if norm(text):
                return text
        except Exception:  # noqa: BLE001
            continue
    # Stdlib fallback: pull the PDF Info dictionary title/author/year so a
    # scanned PDF still yields verifiable metadata without inventing text.
    try:
        raw = Path(path).read_bytes().decode("latin-1", "ignore")
        found = []
        for key in ("Title", "Author", "Subject", "Keywords", "CreationDate"):
            match = re.search(r"/%s\s*\((.{0,300}?)\)" % key, raw, re.S)
            if match:
                value = match.group(1).replace("\\(", "(").replace("\\)", ")")
                found.append("%s: %s" % (key, norm(value)))
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
    """Parse `KEY | path: ... | title: ...` rows from any text file."""
    by_path, sources = {}, []
    for rel in rows:
        full = Path(input_dir) / rel
        if Path(rel).suffix.lower() not in TEXT_EXTS:
            continue
        text = read_text(full)
        found = 0
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
            if not path_value:
                continue
            found += 1
            fields["_line"] = raw
            fields["_source"] = rel
            fields["_locator"] = "L%d" % line_no
            by_path.setdefault(path_value, fields)
        if found:
            sources.append({"path": rel, "rows": found})
    return by_path, sources


def body_meta(text):
    meta = {}
    patterns = {
        "title": r"^\s*title\s*:\s*(.+)$",
        "authors": r"^\s*(?:authors?|first_author)\s*:\s*(.+)$",
        "year": r"^\s*(?:year|publication year)\s*:\s*(.+)$",
        "doi": r"^\s*doi\s*:\s*(.+)$",
        "type": r"^\s*(?:type|document type)\s*:\s*(.+)$",
        "journal": r"^\s*(?:journal|venue|proceedings)\s*:\s*(.+)$",
        "version": r"^\s*version\s*:\s*(.+)$",
        "alias": r"^\s*(?:alias|aliases|also known as)\s*:\s*(.+)$",
        "note": r"^\s*(?:note|admin note|version note)\s*:\s*(.+)$",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            meta[key] = norm(match.group(1)).strip(" .;")
    return meta


def first_heading(text):
    for line in text.split("\n"):
        line = norm(line)
        if line.startswith("#"):
            value = norm(line.lstrip("#"))
            if value and not re.search(r"^(synthetic|example|sample)\b", value, re.I):
                return value
        if 20 <= len(line) <= 220 and not re.match(
                r"^(page|p\d+|doi|http|www)\b", line, re.I):
            return line
    return ""


def alias_hints(name, text, meta):
    haystack = " ".join([name, norm(text)[:4000], meta.get("note", ""),
                         meta.get("alias", ""), meta.get("type", "")]).lower()
    hints = []
    if re.search(r"preprint|arxiv|biorxiv|medrxiv|ssrn|research square|预印本", haystack):
        hints.append("preprint")
    if re.search(r"supplement|supporting information|appendix|附录|补充材料", haystack):
        hints.append("supplement")
    if re.search(r"conference|proceedings|symposium|workshop|会议", haystack):
        hints.append("conference")
    if re.search(r"thesis|dissertation|学位论文", haystack):
        hints.append("thesis")
    if re.search(r"scan|scanned|\bocr\b|无文本层|扫描", haystack):
        hints.append("scan")
    if re.search(r"copy|duplicate|重复|副本|\(\d+\)", haystack):
        hints.append("copy_or_duplicate")
    if re.search(r"(?:^|[_\-.])v\d+(?:$|[_\-.])|revis|revised|修订|accepted manuscript|postprint|final",
                 haystack):
        hints.append("version")
    return sorted(set(hints))


def detect_topics(text, title):
    haystack = (title + " " + norm(text)[:6000]).lower()
    hits = {}
    for group, words in TOPIC_GROUPS.items():
        found = [word for word in words if word in haystack]
        if found:
            hits[group] = found
    return hits


def score_categories(rel, text, meta, hints, topics):
    name = Path(rel).name.lower()
    suffix = Path(rel).suffix.lower()
    head = (meta.get("title", "") + " " + norm(text)[:5000]).lower()
    type_value = meta.get("type", "").lower()
    scores = {category: 0.30 for category in CATEGORIES}
    reasons = {category: [] for category in CATEGORIES}
    for category, words in TYPE_HINTS.items():
        for word in words:
            if word in type_value:
                scores[category] += 0.42
                reasons[category].append("type=%s" % word)
                break
        for word in words:
            if word in name:
                scores[category] += 0.18
                reasons[category].append("filename:%s" % word)
                break
        for word in words:
            if word in head:
                scores[category] += 0.12
                reasons[category].append("text:%s" % word)
                break
    if suffix in DATA_EXTS:
        scores["数据集"] += 0.38
        reasons["数据集"].append("extension:%s" % suffix)
    if suffix in IMAGE_EXTS:
        scores["扫描件"] += 0.42
        reasons["扫描件"].append("extension:%s" % suffix)
    if "preprint" in hints:
        scores["预印本"] += 0.30
        reasons["预印本"].append("alias:preprint")
    if "supplement" in hints:
        scores["补充材料"] += 0.30
        reasons["补充材料"].append("alias:supplement")
    if "conference" in hints:
        scores["会议论文"] += 0.30
        reasons["会议论文"].append("alias:conference")
    if "thesis" in hints:
        scores["学位论文"] += 0.30
        reasons["学位论文"].append("alias:thesis")
    if "scan" in hints:
        scores["扫描件"] += 0.30
        reasons["扫描件"].append("alias:scan")
    if meta.get("journal"):
        scores["期刊论文"] += 0.22
        reasons["期刊论文"].append("journal:%s" % meta["journal"][:60])
    if meta.get("doi"):
        scores["期刊论文"] += 0.12
        reasons["期刊论文"].append("doi")
    if topics:
        best = max(topics, key=lambda key: len(topics[key]))
        if best in ("数据与计算",):
            scores["数据集"] += 0.10
            reasons["数据集"].append("topic:%s" % best)
        elif best in ("冷链与稳定性", "缓冲液与酶", "临床样本", "仪器与方法", "材料与化学"):
            scores["期刊论文"] += 0.06
            scores["笔记与手稿"] += 0.04
    if len(text) > 200:
        scores["笔记与手稿"] += 0.10
        reasons["笔记与手稿"].append("narrative-text")
    if not text and not meta:
        scores["待复核"] += 0.55
        reasons["待复核"].append("no readable text or metadata")
    for category in scores:
        scores[category] = min(scores[category], 0.96)
    return scores, reasons


def choose_paths(item, groups):
    for group in groups.get("duplicate_groups", []):
        if item["id"] == group.get("kept"):
            item["status"] = "canonical"
        elif item["id"] in (group.get("archived") or []):
            item["status"] = "duplicate_archived"
            item["duplicate_of"] = group.get("kept", "")
    for group in groups.get("version_groups", []):
        if item["id"] in group.get("items", []) and item["id"] != group.get("preferred"):
            item["status"] = "superseded_archived"
            item["supersedes"] = group.get("preferred", "")


def canonical_name(item, suffix_marker=""):
    author = slug(item.get("authors") or "", 24)
    if not author or author == "untitled":
        author = "unknown-author"
    year = item.get("year") or "nd"
    title = slug(item.get("title") or Path(item["path"]).stem)
    suffix = Path(item["path"]).suffix or ".bin"
    name = "%s-%s-%s%s" % (author, year, title, suffix)
    if suffix_marker:
        stem, dot, ext = name.rpartition(".")
        name = stem + suffix_marker + dot + ext
    return name


def build(input_dir, work_dir, output_dir, threshold, apply_plan, materialize):
    input_dir, work, out = Path(input_dir), Path(work_dir), Path(output_dir)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(input_dir / "case.json", {})
    case_id = str(case.get("case_id") or "UNKNOWN")
    rows = scan_files(input_dir)
    targets = item_paths(input_dir, rows)
    manifest, manifest_sources = parse_manifest(input_dir, rows)

    items, evidence, uncertainties, skipped = [], [], [], []
    for index, rel in enumerate(targets, 1):
        full = input_dir / rel
        if not full.is_file():
            skipped.append({"path": rel, "reason": "file not readable"})
            continue
        data = full.read_bytes()
        digest = sha256_file(full)
        text = extract_text(full)
        suffix = full.suffix.lower()
        text_layer = bool(norm(text)) or suffix in TEXT_EXTS
        meta = dict(manifest.get(rel) or {})
        # Manifest keys are lowercase; body metadata only fills missing keys.
        for key, value in body_meta(text).items():
            meta.setdefault(key, value)
        title = norm(meta.get("title") or "")
        if not title or title.lower() in ("missing", "unknown", "none"):
            title = first_heading(text) or re.sub(r"[_-]+", " ", full.stem)
            title_source = "body" if first_heading(text) else "filename"
        else:
            title_source = "manifest" if rel in manifest else "body"
        authors = norm(meta.get("authors") or meta.get("first_author") or "")
        year_match = YEAR_RE.search(str(meta.get("year") or ""))
        year = year_match.group(0) if year_match else ""
        if not year:
            fallback = YEAR_RE.search(text[:6000])
            year = fallback.group(0) if fallback else ""
            year_source = "body" if year else ""
        else:
            year_source = "metadata"
        doi_match = DOI_RE.search(str(meta.get("doi") or "")) or DOI_RE.search(text[:10000])
        doi = doi_match.group(0).rstrip(".,;)").lower() if doi_match else ""
        hints = alias_hints(rel, text, meta)
        topics = detect_topics(text, title)
        scores, reasons = score_categories(rel, text, meta, hints, topics)
        ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
        category, score = ordered[0]
        runner_up, runner_score = ordered[1]
        cross_topic = len([group for group, words in topics.items() if len(words) >= 2]) >= 2
        if score - runner_score < 0.12 and category != "待复核":
            category = "待复核"
            reasons[category].append("top score margin %.2f < 0.12 vs %s" % (
                score - runner_score, runner_up))
            score = max(score, 0.5)
        confidence = score
        if title_source == "filename":
            confidence -= 0.10
        if not year:
            confidence -= 0.10
        if not doi and category == "期刊论文":
            confidence -= 0.08
        if cross_topic:
            confidence -= 0.12
        if not text_layer and category not in ("扫描件", "数据集"):
            confidence -= 0.10
        confidence = round(max(0.05, min(0.95, confidence)), 2)
        review_reasons = []
        if confidence < threshold:
            review_reasons.append("confidence %.2f < threshold %.2f" % (confidence, threshold))
        if cross_topic:
            review_reasons.append("cross-topic signals: %s" % ", ".join(
                group for group, words in topics.items() if len(words) >= 2))
        if category == "待复核":
            review_reasons.extend(reasons["待复核"] or ["ambiguous category signals"])
        if not text_layer and category not in ("扫描件", "数据集"):
            review_reasons.append("no text layer; content cannot be verified")
        review = bool(review_reasons)
        tag_list = []
        for group in sorted(topics, key=lambda key: -len(topics[key])):
            tag_list.append(group)
            tag_list.extend(topics[group][:2])
        tag_list = list(dict.fromkeys(tag_list))[:6] or ["待复核"]
        item_id = "I%03d" % (len(items) + 1)
        item = {
            "id": item_id,
            "path": rel, "file_id": item_id,
            "bytes": len(data), "sha256": digest, "hash": digest,
            "kind": suffix.lstrip(".") or "other",
            "title": title, "title_source": title_source,
            "authors": authors, "first_author": authors,
            "year": year, "year_source": year_source, "doi": doi,
            "journal": norm(meta.get("journal") or ""),
            "type": norm(meta.get("type") or ""),
            "aliases": hints,
            "category": category, "confidence": confidence,
            "tags": tag_list, "topics": sorted(topics),
            "cross_topic": cross_topic,
            "review": review, "status": "review" if review else "classified",
            "new_name": Path(rel).name if review else canonical_name({
                "authors": authors, "year": year, "title": title, "path": rel}),
            "proposed_name": canonical_name({"authors": authors, "year": year,
                                             "title": title, "path": rel}),
            "rationale": "; ".join(dict.fromkeys(
                reasons[category] or [reasons[runner_up][0] if reasons[runner_up] else "default narrative rule"]))[:400],
            "score_table": {key: round(value, 2) for key, value in ordered},
            "text_layer": text_layer,
            "review_reasons": review_reasons,
        }
        items.append(item)

    # manifest rows pointing to excluded support files are still evidenced
    by_id = {item["id"]: item for item in items}
    by_hash = {}
    for item in items:
        by_hash.setdefault(item["sha256"], []).append(item)
    duplicate_groups = []
    archived = set()
    for digest, members in by_hash.items():
        if len(members) < 2:
            continue
        kept = sorted(members, key=lambda row: (
            row["title_source"] == "filename", -row["bytes"], row["path"]))[0]
        rest = [row for row in members if row is not kept]
        for row in rest:
            row["status"] = "duplicate_archived"
            row["duplicate_of"] = kept["id"]
            row["review"] = False
            archived.add(row["id"])
        duplicate_groups.append({
            "group_id": "DUP%d" % (len(duplicate_groups) + 1),
            "basis": "sha256", "sha256": digest, "key": digest,
            "items": [row["id"] for row in members],
            "kept": kept["id"], "archived": [row["id"] for row in rest],
            "reason": "byte-identical copies; keep one canonical file and archive the rest",
        })
    # version groups: same normalized title / DOI / version-stem, different hashes
    version_buckets = {}
    for item in items:
        keys = []
        if item["title"] and item["title_source"] != "filename":
            keys.append("title:" + ascii_key(item["title"]))
        if item["doi"]:
            keys.append("doi:" + item["doi"])
        if "version" in item["aliases"]:
            keys.append("stem:" + ascii_key(re.sub(r"[_-]?v\d+.*$", "", Path(item["path"]).stem)))
        for key in keys:
            version_buckets.setdefault(key, []).append(item)
    seen_version_sets, version_groups = set(), []
    for key, members in version_buckets.items():
        unique = {row["id"]: row for row in members}
        if len(unique) < 2:
            continue
        ids = tuple(sorted(unique))
        if ids in seen_version_sets:
            continue
        hashes = {unique[iid]["sha256"] for iid in ids}
        if len(hashes) < 2:
            continue
        seen_version_sets.add(ids)
        ordered = sorted(unique.values(), key=lambda row: (
            "preprint" in row["aliases"], -(int(row["year"]) if row["year"] else 0),
            -row["bytes"], row["path"]))
        preferred = ordered[0]
        for row in ordered[1:]:
            if row["id"] not in archived:
                row["status"] = "superseded_archived"
                row["supersedes"] = preferred["id"]
        version_groups.append({
            "group_id": "VER%d" % (len(version_groups) + 1),
            "basis": "same normalized title/DOI with distinct hashes",
            "key": key, "items": list(ids), "versions": [
                {"item_id": row["id"], "version": next(
                    (hint for hint in row["aliases"] if hint in ("preprint", "version")),
                    "unlabelled"), "sha256": row["sha256"]}
                for row in ordered],
            "preferred": preferred["id"],
            "reason": "formal/latest version kept; other versions archived, never deleted",
        })
    # review flags must stay consistent with the queue; duplicates/versions are resolved
    for item in items:
        if item["id"] in archived or item.get("supersedes"):
            item["review"] = False
    review_queue = []
    for item in items:
        if item["review"]:
            review_queue.append({
                "item_id": item["id"], "id": item["id"], "path": item["path"],
                "reason": "; ".join(item["review_reasons"]),
                "suggested_action": "human confirms category/title/author before rename",
            })
    # evidence: real body/manifest quote when possible, inventory ledger otherwise
    ledger = ["# Library evidence ledger", "",
              "| item | path | bytes | sha256 | title | category | confidence |",
              "|---|---|---:|---|---|---|---:|"]
    ledger_rows = {}
    for item in items:
        row = "%s | %s | %d | %s | %s | %s | %.2f" % (
            item["id"], item["path"], item["bytes"], item["sha256"],
            item["title"], item["category"], item["confidence"])
        ledger.append("| " + " | ".join([
            item["id"], item["path"], str(item["bytes"]), item["sha256"],
            item["title"].replace("|", "/"), item["category"],
            "%.2f" % item["confidence"]]) + " |")
        ledger_rows[item["id"]] = row
    (work / "library-evidence.md").write_text("\n".join(ledger) + "\n", encoding="utf-8")
    for item in items:
        meta = manifest.get(item["path"])
        text = extract_text(input_dir / item["path"])
        quote, path_used, locator = "", "", ""
        if meta and meta.get("_line"):
            quote = meta["_line"]
            path_used = meta["_source"]
            locator = meta["_locator"]
        else:
            for line_no, line in enumerate(text.split("\n"), 1):
                candidate = norm(line)
                if 8 <= len(candidate) <= 240 and not candidate.startswith("|"):
                    quote = candidate
                    path_used = item["path"]
                    locator = "L%d" % line_no
                    break
        if not quote:
            quote = ledger_rows[item["id"]]
            path_used = "work/library-evidence.md"
            locator = item["id"]
        item["evidence"] = {"path": path_used, "locator": locator, "quote": quote}
        evidence.append(item["evidence"])

    operations = []
    for item in items:
        if item["review"]:
            operations.append({
                "id": "O%03d" % (len(operations) + 1),
                "action": "flag_review", "item_id": item["id"],
                "from": item["path"], "to": item["path"], "status": "needs_review",
                "detail": "low-confidence/ambiguous item kept in place: " +
                          "; ".join(item["review_reasons"]),
                "rollback": "clear the review flag; no file was changed",
            })
            continue
        if item["status"] == "duplicate_archived":
            target = "library/_duplicates/" + canonical_name(item, "-dup")
            action = "classify_copy"
            detail = "byte-identical duplicate of %s" % item.get("duplicate_of", "")
        elif item["status"] == "superseded_archived":
            target = "library/_superseded/" + canonical_name(item, "-old")
            action = "classify_copy"
            detail = "superseded version; preferred item %s" % item.get("supersedes", "")
        else:
            category_dir = slug(item["category"])
            target = "library/%s/%s" % (category_dir, item["new_name"])
            action = "classify_copy"
            detail = "category %s at confidence %.2f" % (item["category"], item["confidence"])
        operations.append({
            "id": "O%03d" % (len(operations) + 1), "action": action,
            "item_id": item["id"], "from": item["path"], "to": target,
            "sha_before": item["sha256"], "sha_after": item["sha256"],
            "status": "ok", "detail": detail,
            "rollback": "delete output/%s; source file was not modified" % target,
        })

    materialized_count = 0
    if materialize:
        for operation in operations:
            if operation["action"] != "classify_copy":
                continue
            source = input_dir / operation["from"]
            target = out / operation["to"]
            target.parent.mkdir(parents=True, exist_ok=True)
            final = target
            if final.exists():
                if sha256_file(final) == sha256_file(source):
                    operation["sha_after"] = sha256_file(final)
                    materialized_count += 1
                    continue
                counter = 2
                while True:
                    candidate = final.with_name("%s-%d%s" % (
                        final.stem, counter, final.suffix))
                    if not candidate.exists():
                        final = candidate
                        break
                    counter += 1
            shutil.copy2(source, final)
            operation["to"] = final.relative_to(out).as_posix()
            operation["sha_after"] = sha256_file(final)
            operation["rollback"] = "delete output/%s; source file was not modified" % operation["to"]
            materialized_count += 1

    input_snapshot = [{"path": rel, "bytes": (input_dir / rel).stat().st_size,
                       "sha256": sha256_file(input_dir / rel)} for rel in rows]
    coverage = {
        "input_files": len(targets), "indexed": len(items),
        "classified": len([row for row in items if not row["review"]]),
        "unclassified": [row["path"] for row in items if row["review"]],
        "unknown_paths": [], "skipped": skipped,
        "no_result": not items,
    }
    summary = "%d 个材料文件；分类 %d；复核 %d；重复组 %d；版本组 %d；跳过 %d。" % (
        len(items), coverage["classified"], len(review_queue),
        len(duplicate_groups), len(version_groups), len(skipped))
    if not items:
        summary = "输入目录没有可分类的材料文件；未生成伪分类（no_result）。"
    plan = {
        "case_id": case_id, "mode": "classification", "summary": summary,
        "question": str(case.get("question") or ""),
        "taxonomy": {"categories": CATEGORIES, "criteria": CRITERIA,
                     "threshold": threshold},
        "items": items, "review_queue": review_queue,
        "duplicate_groups": duplicate_groups, "version_groups": version_groups,
        "operations": operations, "evidence": evidence,
        "operation_evidence": {
            "operations": operations,
            "rollback_all": "reverse operations in reverse order; every operation "
                            "carries an explicit rollback field",
            "source_files_unchanged": True,
        },
        "failure_recovery": {
            "skipped": skipped,
            "review_queue": review_queue,
            "duplicate_groups": duplicate_groups,
            "version_groups": version_groups,
            "recovery": "review items stay in place; duplicates/old versions are "
                        "copied to library/_duplicates or library/_superseded and "
                        "can be deleted without touching the source files",
        },
        "input_snapshot": input_snapshot, "coverage": coverage,
        "manifest_sources": manifest_sources,
        "skipped": skipped, "uncertainties": uncertainties,
        "no_result": not items,
        "materialized": bool(materialize and materialized_count),
        "generated_at": now_iso(), "generated_by": "library_classify.py",
        "script_version": VERSION,
    }
    if not items:
        uncertainties.append({
            "item": "input_materials", "reason": "no readable material files",
            "impact": "no classification is claimed", "action": "check /input",
        })
        plan["uncertainties"] = uncertainties
    if apply_plan:
        write_json(work / "library-plan.json", plan)

    results = {
        "case_id": case_id, "mode": "classification", "summary": summary,
        "taxonomy": plan["taxonomy"], "items": items,
        "counts": {"input_files": len(targets), "classified": coverage["classified"],
                   "review": len(review_queue), "duplicates": len(duplicate_groups),
                   "versions": len(version_groups), "skipped": len(skipped)},
        "review_queue": review_queue, "duplicate_groups": duplicate_groups,
        "version_groups": version_groups, "operations": operations,
        "input_snapshot": input_snapshot, "coverage": coverage,
        "evidence": evidence, "skipped": skipped,
        "materialized": plan["materialized"], "no_result": plan["no_result"],
        "uncertainties": uncertainties,
        "generated_at": plan["generated_at"],
    }
    index = {
        "case_id": case_id,
        "items": [{"id": row["id"], "path": row["path"], "category": row["category"],
                   "tags": row["tags"], "new_name": row["new_name"],
                   "target": next((op["to"] for op in operations
                                   if op.get("item_id") == row["id"]
                                   and op["action"] == "classify_copy"), ""),
                   "hash": row["sha256"], "confidence": row["confidence"],
                   "review": row["review"], "status": row["status"]}
                  for row in items],
        "by_category": {},
        "review_queue": review_queue,
    }
    for row in items:
        index["by_category"].setdefault(row["category"], []).append(row["path"])
    operations_log = {
        "case_id": case_id, "operations": operations,
        "planned_operations": [op for op in operations
                               if op["action"] == "classify_copy"],
        "review_operations": [op for op in operations
                              if op["action"] == "flag_review"],
        "rollback_all": "reverse operations in reverse order; item rollback fields are explicit",
        "skipped": skipped, "generated_at": plan["generated_at"],
    }
    write_json(out / "library-results.json", results)
    write_json(out / "library-index.json", index)
    write_json(out / "operations.log.json", operations_log)
    return {
        "ok": True, "case_id": case_id, "items": len(items),
        "review": len(review_queue), "duplicates": len(duplicate_groups),
        "versions": len(version_groups), "materialized": materialized_count,
        "outputs": ["library-results.json", "library-index.json",
                    "operations.log.json"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Real-file library classifier")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--apply", action="store_true",
                        help="write work/library-plan.json")
    parser.add_argument("--materialize", action="store_true",
                        help="copy classified files into output/library/")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = build(args.input, args.work, args.output,
                        max(0.0, min(1.0, args.threshold)),
                        args.apply, args.materialize)
    except Exception as exc:  # noqa: BLE001
        # Honest failure envelope: still exit 0 and let finalize write partial.
        summary = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        try:
            write_json(Path(args.work) / "library-classify-error.json", summary)
        except OSError:
            pass
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
