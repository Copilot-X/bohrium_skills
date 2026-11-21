#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate and materialize a library classification plan.

This is the deterministic half of the classification skill:

    python3 classify_library.py --input /input --work /app/work \
        --output /app/output --materialize --json
    python3 classify_library.py --check-only --input /input --work /app/work
    python3 classify_library.py --self-test --json

It does **not** choose categories.  The model writes ``work/library-plan.json``
from the actual file contents; this script then:

* inventories every input file and computes its sha256;
* checks the taxonomy (categories + per-category criteria + threshold);
* checks that every input file appears exactly once and every item has a
  category, rationale, hash and verbatim evidence;
* enforces the review queue: low-confidence/boundary items must be queued and
  must not be moved or renamed;
* validates duplicate and version groups;
* writes the three declared outputs and, with ``--materialize``, performs a
  real, reversible copy into ``output/library/<category>/<new_name>`` with
  before/after hashes and rollback records.

The script never invents categories, tags or evidence.  Invalid plans fail with
exit code 1 and a machine-readable audit; missing plans fail with exit code 2.

Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_THRESHOLD = 0.7
SKIP_NAMES = {"case.json", "task.md"}
SKIP_PARTS = {"environment", ".ocr", "__pycache__", ".git"}
TEXT_EXTENSIONS = {".md", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".yaml",
                   ".yml", ".toml", ".xml", ".html", ".tex", ".latex", ".rst",
                   ".org", ".bib", ".ris", ".log", ".py", ".sql"}
BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MOVING_ACTIONS = {"move", "rename", "classify_move", "relocate", "copy",
                  "classify_copy"}


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return default


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path, payload):
    return write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def sha256_path(path):
    h = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_space(value):
    return " ".join(str(value or "").replace("\r", "\n").split())


def norm_quote(value):
    """Fold case, whitespace and punctuation for a verbatim-style comparison."""
    value = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s　\W_]+", "", value, flags=re.UNICODE)


def quote_supported(source_text, quote):
    source = norm_quote(source_text)
    normalized = norm_quote(quote)
    if not normalized:
        return False
    if normalized in source:
        return True
    pieces = [norm_quote(piece) for piece in re.split(r"(?:\.{3,}|…+)", str(quote))
              if norm_quote(piece)]
    return bool(pieces) and all(piece in source for piece in pieces)


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def category_slug(value):
    slug = BAD_NAME_CHARS.sub("_", str(value or "")).strip(" ._")
    slug = re.sub(r"\s+", "_", slug)
    return slug or "uncategorized"


def safe_filename(value, fallback="item"):
    name = Path(str(value or "")).name
    name = BAD_NAME_CHARS.sub("_", name).strip(" ._")
    if not name or name in (".", ".."):
        name = fallback
    if name.startswith("."):
        name = "_" + name[1:]
    return name[:180] or fallback


# ---------------------------------------------------------------------------
# inventory and plan loading
# ---------------------------------------------------------------------------

def inventory(input_dir):
    root = Path(input_dir)
    rows = []
    if not root.is_dir():
        return rows
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = set(path.relative_to(root).parts)
        if parts & SKIP_PARTS:
            continue
        if path.name in SKIP_NAMES:
            continue
        suffix = path.suffix.lower()
        row = {
            "path": path.relative_to(root).as_posix(),
            "absolute": str(path),
            "bytes": path.stat().st_size,
            "kind": suffix.lstrip(".") or "file",
            "sha256": sha256_path(path),
            "readable": suffix in TEXT_EXTENSIONS,
            "text": "",
        }
        if row["readable"]:
            row["text"] = read_text(path)
        rows.append(row)
    return rows


def extra_evidence_sources(work_dir, output_dir):
    """Intermediate artifacts are valid evidence for unreadable inputs.

    The item still points at the real input path for classification; the
    evidence may cite a recorded inspection record under ``work/`` or
    ``output/`` (for example a metadata probe for a no-text-layer scan).
    """
    rows = []
    for base, prefix in ((Path(work_dir), "work"), (Path(output_dir), "output")):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            rel = "%s/%s" % (prefix, path.relative_to(base).as_posix())
            rows.append({"path": rel, "absolute": str(path),
                         "bytes": path.stat().st_size,
                         "kind": path.suffix.lstrip(".") or "file",
                         "sha256": sha256_path(path), "readable": True,
                         "text": read_text(path), "intermediate": True})
    return rows


def find_plan(work_dir):
    work = Path(work_dir)
    for name in ("library-plan.json", "classification-plan.json"):
        path = work / name
        if path.is_file():
            return path
    return None


def normalize_group_ids(group):
    if isinstance(group, dict):
        for key in ("items", "ids", "members", "item_ids", "files", "paths"):
            if isinstance(group.get(key), list):
                return [str(value) for value in group[key]]
        return []
    if isinstance(group, list):
        return [str(value) for value in group]
    return []


def group_label(group):
    if isinstance(group, dict):
        return str(group.get("id") or group.get("group_id") or
                   group.get("name") or "")
    return ""


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate_plan(plan, materials, materialize_requested=False,
                  evidence_sources=None):
    problems, warnings = [], []
    checks = {}

    if not isinstance(plan, dict) or not plan:
        return (["plan is missing or not a JSON object"],
                ["write work/library-plan.json before running the validator"],
                {"plan_present": False},
                {"taxonomy": {"categories": [], "criteria": {},
                              "threshold": DEFAULT_THRESHOLD},
                 "items": [], "missing": [], "unknown": [], "queue_ids": [],
                 "materialize_requested": materialize_requested})

    taxonomy = plan.get("taxonomy")
    if not isinstance(taxonomy, dict) or not taxonomy:
        problems.append("taxonomy is missing or empty")
        taxonomy = {}
    categories = [str(value).strip() for value in as_list(taxonomy.get("categories"))
                  if str(value).strip()]
    criteria = taxonomy.get("criteria") if isinstance(taxonomy.get("criteria"), dict) else {}
    if not categories:
        problems.append("taxonomy.categories is empty")
    if len(categories) != len(set(categories)):
        problems.append("taxonomy.categories contains duplicates")
    for category in categories:
        if not str(criteria.get(category) or "").strip():
            problems.append("taxonomy.criteria is missing for category: %s" % category)
    threshold = taxonomy.get("threshold", DEFAULT_THRESHOLD)
    try:
        threshold = float(threshold)
        if not 0.0 <= threshold <= 1.0:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("taxonomy.threshold must be a number in [0, 1]")
        threshold = DEFAULT_THRESHOLD

    by_path = {row["path"]: row for row in materials}
    sources_by_path = dict(by_path)
    for row in evidence_sources or []:
        sources_by_path.setdefault(row["path"], row)
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        problems.append("items is missing or empty")
        items = []
    seen_ids, seen_paths = set(), set()
    enriched = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            problems.append("item %d is not an object" % index)
            continue
        iid = str(item.get("id") or item.get("item_id") or "I%03d" % index)
        path = str(item.get("path") or item.get("file") or "")
        category = str(item.get("category") or "")
        if iid in seen_ids:
            problems.append("duplicate item id: %s" % iid)
        seen_ids.add(iid)
        if not path:
            problems.append("item %s lacks path" % iid)
        elif path in seen_paths:
            problems.append("input file classified more than once: %s" % path)
        seen_paths.add(path)
        source = by_path.get(path)
        if source is None:
            problems.append("item %s references an unknown input path: %s" % (iid, path))
        if not category:
            problems.append("item %s lacks category" % iid)
        elif category not in categories:
            problems.append("item %s category is not in taxonomy: %s" % (iid, category))

        tags = as_list(item.get("tags"))
        if not tags:
            warnings.append("item %s has no tags" % iid)
        if any(not str(tag).strip() for tag in tags):
            problems.append("item %s has an empty tag" % iid)

        confidence = item.get("confidence")
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            problems.append("item %s confidence is missing or not numeric" % iid)
            confidence = 0.0
        if not 0.0 <= confidence <= 1.0:
            problems.append("item %s confidence out of [0, 1]" % iid)

        rationale = str(item.get("rationale") or item.get("reason") or "").strip()
        if not rationale:
            problems.append("item %s lacks rationale" % iid)

        new_name = str(item.get("new_name") or item.get("filename") or "").strip()
        if not new_name:
            problems.append("item %s lacks new_name" % iid)
        else:
            if Path(new_name).name != new_name or "/" in new_name or "\\" in new_name:
                problems.append("item %s new_name must not contain a path separator" % iid)
            if new_name in (".", ".."):
                problems.append("item %s new_name is not a file name" % iid)
            original_suffix = Path(path).suffix.lower()
            if original_suffix and Path(new_name).suffix.lower() != original_suffix:
                warnings.append("item %s new_name changes the file extension" % iid)

        evidence = item.get("evidence")
        evidence_rows = evidence if isinstance(evidence, list) else [evidence]
        evidence_ok = False
        verified = []
        if not evidence:
            problems.append("item %s lacks evidence" % iid)
        for evidence_index, entry in enumerate(evidence_rows, 1):
            if not isinstance(entry, dict):
                problems.append("item %s evidence %d is not an object" % (iid, evidence_index))
                continue
            quote = str(entry.get("quote") or "").strip()
            locator = str(entry.get("locator") or entry.get("page") or
                          entry.get("paragraph") or "").strip()
            evidence_path = str(entry.get("path") or entry.get("source") or path or "")
            target = sources_by_path.get(evidence_path)
            if target is None and evidence_path:
                target = sources_by_path.get("work/" + evidence_path) or \
                         sources_by_path.get("output/" + evidence_path)
            if not quote or not locator:
                problems.append("item %s evidence %d lacks locator/quote" %
                                (iid, evidence_index))
                continue
            if target is None:
                problems.append("item %s evidence path not found: %s" %
                                (iid, evidence_path))
                continue
            if not target["readable"]:
                warnings.append("item %s evidence source has no text layer: %s; "
                                "manual review required" % (iid, evidence_path))
                verified.append(False)
                continue
            if quote_supported(target["text"], quote):
                evidence_ok = True
                verified.append(True)
            else:
                problems.append("item %s evidence quote not found verbatim in %s" %
                                (iid, evidence_path))
                verified.append(False)

        review = bool(item.get("review"))
        low_confidence = confidence < threshold
        review_reason = ""
        if low_confidence:
            review_reason = "confidence %.2f < threshold %.2f" % (confidence, threshold)
        elif source is not None and not source["readable"]:
            review_reason = "source has no extractable text layer"
        if review_reason and not review:
            problems.append("item %s must set review=true (%s)" % (iid, review_reason))
        if review and not review_reason:
            review_reason = "model marked boundary case for manual review"

        declared_hash = str(item.get("hash") or item.get("sha256") or "")
        computed_hash = source["sha256"] if source else ""
        if declared_hash:
            normalized = declared_hash.replace("sha256:", "").replace("sha256-", "")
            if computed_hash and normalized != computed_hash:
                problems.append("item %s declared hash does not match the input file" % iid)

        target_name = safe_filename(new_name or Path(path).name or iid,
                                    fallback="%s%s" % (iid, Path(path).suffix))
        enriched.append({
            **item,
            "id": iid,
            "path": path,
            "category": category,
            "tags": [str(tag) for tag in tags],
            "confidence": confidence,
            "review": review,
            "review_reason": review_reason,
            "hash": "sha256:" + computed_hash if computed_hash else declared_hash,
            "computed_sha256": computed_hash,
            "evidence_verified": evidence_ok and all(verified or [False]),
            "verification": verified,
            "target": "library/%s/%s" % (category_slug(category), target_name),
            "status": "needs_review" if review else "classified",
        })
    checks["items"] = len(enriched)

    materials_paths = set(by_path)
    classified_paths = {item["path"] for item in enriched if item.get("path")}
    missing = sorted(materials_paths - classified_paths)
    unknown = sorted(classified_paths - materials_paths)
    for path in missing:
        problems.append("input file not classified: %s" % path)
    for path in unknown:
        problems.append("classified path is not an input file: %s" % path)
    no_result = bool(plan.get("no_result"))
    if not materials:
        if not no_result:
            problems.append("input has no material files; set no_result=true and explain")
    elif no_result:
        problems.append("no_result=true but %d input files exist" % len(materials))

    # Review queue consistency.
    queue = plan.get("review_queue")
    if queue is None:
        problems.append("review_queue is missing")
        queue = []
    if not isinstance(queue, list):
        problems.append("review_queue is not a list")
        queue = []
    queue_ids = []
    for entry in queue:
        qid = ""
        if isinstance(entry, str):
            qid = entry
        elif isinstance(entry, dict):
            qid = str(entry.get("item_id") or entry.get("id") or
                      entry.get("path") or "")
            if not entry.get("reason") and not qid:
                problems.append("review_queue entry lacks item_id/reason")
        if not qid:
            problems.append("review_queue entry has no item id")
            continue
        if qid not in seen_ids and qid not in classified_paths:
            problems.append("review_queue references an unknown item: %s" % qid)
        queue_ids.append(qid)
    if len(queue_ids) != len(set(queue_ids)):
        problems.append("review_queue contains duplicate items")
    required_queue = {item["id"] for item in enriched
                      if item["review"] or item["confidence"] < threshold}
    for iid in sorted(required_queue - set(queue_ids)):
        problems.append("low-confidence/boundary item is not in review_queue: %s" % iid)
    for iid in sorted(set(queue_ids) - {item["id"] for item in enriched}):
        if iid not in classified_paths:
            problems.append("review_queue item is not in items: %s" % iid)
    for item in enriched:
        in_queue = item["id"] in queue_ids or item["path"] in queue_ids
        if item["review"] != in_queue:
            problems.append("item %s review flag and review_queue disagree" % item["id"])

    # Operations: moving/renaming a review item is unsafe.
    operations = plan.get("operations") or []
    if not isinstance(operations, list):
        problems.append("operations is not a list")
        operations = []
    review_keys = set()
    for item in enriched:
        if item["review"]:
            review_keys.update({item["id"], item["path"], Path(item["path"]).name,
                                Path(item["new_name"]).name
                                if item.get("new_name") else ""})
    for index, operation in enumerate(operations, 1):
        if not isinstance(operation, dict):
            problems.append("operation %d is not an object" % index)
            continue
        action = str(operation.get("action") or operation.get("type") or "")
        status = str(operation.get("status") or "")
        if not action:
            problems.append("operation %d lacks action" % index)
        if not status:
            warnings.append("operation %d lacks status" % index)
        if action.lower() in MOVING_ACTIONS:
            from_path = str(operation.get("from") or operation.get("source") or "")
            to_path = str(operation.get("to") or operation.get("target") or "")
            if not from_path or not to_path:
                problems.append("operation %d move/rename lacks from/to" % index)
            if not operation.get("rollback"):
                problems.append("operation %d move/rename lacks rollback" % index)
            touched = {from_path, to_path, Path(from_path).name, Path(to_path).name}
            if touched & review_keys:
                problems.append("operation %d moves/renames a review-queue item" % index)
    if not operations and materialize_requested and enriched:
        warnings.append("no plan operations; the validator will record the real "
                        "materialization operations it performs")

    # Duplicate and version groups.
    by_id = {item["id"]: item for item in enriched}
    duplicate_groups = plan.get("duplicate_groups") or []
    if not isinstance(duplicate_groups, list):
        problems.append("duplicate_groups is not a list")
        duplicate_groups = []
    for index, group in enumerate(duplicate_groups, 1):
        ids = normalize_group_ids(group)
        if len(ids) < 2:
            problems.append("duplicate group %d has fewer than 2 items" % index)
        for iid in ids:
            if iid not in by_id:
                problems.append("duplicate group %d references unknown item: %s" % (index, iid))
        hashes = {by_id[iid]["computed_sha256"] for iid in ids
                  if iid in by_id and by_id[iid]["computed_sha256"]}
        if len(hashes) > 1:
            problems.append("duplicate group %d items do not share a hash" % index)
        if len(ids) >= 2 and len(hashes) == 0:
            warnings.append("duplicate group %d has no computable hashes" % index)
    version_groups = plan.get("version_groups") or []
    if not isinstance(version_groups, list):
        problems.append("version_groups is not a list")
        version_groups = []
    for index, group in enumerate(version_groups, 1):
        ids = normalize_group_ids(group)
        if len(ids) < 2:
            problems.append("version group %d has fewer than 2 items" % index)
        for iid in ids:
            if iid not in by_id:
                problems.append("version group %d references unknown item: %s" % (index, iid))
        preferred = ""
        if isinstance(group, dict):
            preferred = str(group.get("preferred") or group.get("keep") or "")
            if preferred and preferred not in ids:
                problems.append("version group %d preferred item is not in the group" % index)
        labels = []
        if isinstance(group, dict):
            labels = [str(value) for value in as_list(group.get("versions") or [])]
        if labels and len(labels) != len(ids):
            warnings.append("version group %d version labels do not match item count" % index)
        hashes = {by_id[iid]["computed_sha256"] for iid in ids
                  if iid in by_id and by_id[iid]["computed_sha256"]}
        if len(ids) >= 2 and len(hashes) == 1:
            warnings.append("version group %d items are byte-identical; "
                            "record them as duplicates instead" % index)

    checks.update({
        "categories": len(categories),
        "input_files": len(materials),
        "classified": len(classified_paths & materials_paths),
        "unclassified": len(missing),
        "review_queue": len(set(queue_ids)),
        "duplicate_groups": len(duplicate_groups),
        "version_groups": len(version_groups),
        "threshold": threshold,
        "no_result": no_result,
    })
    return problems, warnings, checks, {
        "items": enriched,
        "taxonomy": {"categories": categories, "criteria": criteria,
                     "threshold": threshold},
        "missing": missing,
        "unknown": unknown,
        "queue_ids": queue_ids,
        "materialize_requested": materialize_requested,
    }


# ---------------------------------------------------------------------------
# outputs and materialization
# ---------------------------------------------------------------------------

def materialize(enriched, materials, output_dir):
    root = Path(output_dir) / "library"
    root.mkdir(parents=True, exist_ok=True)
    by_path = {row["path"]: row for row in materials}
    operations = []
    for index, item in enumerate(enriched, 1):
        source = by_path.get(item["path"])
        if source is None:
            continue
        target_rel = item["target"]
        target = Path(output_dir) / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        status = "ok"
        if target.exists():
            same = sha256_path(target) == source["sha256"]
            if same:
                status = "skipped_identical"
            else:
                stem, suffix = target.stem, target.suffix
                counter = 2
                while target.exists() and counter < 1000:
                    target = target.with_name("%s-%d%s" % (stem, counter, suffix))
                    counter += 1
                target_rel = target.relative_to(Path(output_dir)).as_posix()
                status = "renamed_collision"
        before = source["sha256"]
        if status != "skipped_identical":
            shutil.copy2(source["absolute"], target)
        after = sha256_path(target)
        if status != "skipped_identical" and before != after:
            status = "hash_mismatch"
        operations.append({
            "id": "OP%03d" % index,
            "action": "classify_copy",
            "status": status,
            "from": "input/" + item["path"],
            "to": target_rel,
            "category": item["category"],
            "sha_before": before,
            "sha_after": after,
            "rollback": "delete %s" % target_rel,
            "evidence": {
                "path": item["path"],
                "locator": (as_list(item.get("evidence"))[0].get("locator")
                            if as_list(item.get("evidence")) and
                            isinstance(as_list(item.get("evidence"))[0], dict)
                            else ""),
            },
            "review": item["review"],
        })
    return operations


def build_outputs(plan, materials, validation, output_dir, materialize_requested,
                  evidence_sources=None):
    case_id = str(plan.get("case_id") or "")
    taxonomy = validation["taxonomy"]
    items = validation["items"]
    queue = plan.get("review_queue") or []
    queue_rows = []
    by_id = {item["id"]: item for item in items}
    for entry in queue:
        qid = entry if isinstance(entry, str) else str(
            entry.get("item_id") or entry.get("id") or entry.get("path") or "")
        item = by_id.get(qid)
        row = dict(entry) if isinstance(entry, dict) else {"item_id": qid}
        if item:
            row.update({"item_id": item["id"], "path": item["path"],
                        "category": item["category"],
                        "confidence": item["confidence"],
                        "reason": row.get("reason") or item["review_reason"]})
        queue_rows.append(row)

    operations = [item for item in (plan.get("operations") or [])
                  if isinstance(item, dict)]
    materialized_operations = []
    if materialize_requested:
        materialized_operations = materialize(items, materials, output_dir)
        existing = {(row.get("action"), row.get("from"), row.get("to"))
                    for row in operations}
        for row in materialized_operations:
            if (row["action"], row["from"], row["to"]) not in existing:
                operations.append(row)
    else:
        materialized_operations = [{
            "id": "PLAN%03d" % index,
            "action": "planned_classify",
            "status": "not_executed",
            "from": "input/" + item["path"],
            "to": item["target"],
            "category": item["category"],
            "sha_before": item["computed_sha256"],
            "sha_after": "",
            "rollback": "no change was made",
        } for index, item in enumerate(items, 1)]

    counts = {
        "input_files": len(materials),
        "classified": sum(1 for item in items if item["path"]),
        "review_queue": len(queue_rows),
        "duplicate_groups": len(plan.get("duplicate_groups") or []),
        "version_groups": len(plan.get("version_groups") or []),
        "categories_used": len({item["category"] for item in items if item["category"]}),
    }
    evidence = []
    source_rows = list(materials) + list(evidence_sources or [])
    for item in items:
        for entry in as_list(item.get("evidence")):
            if isinstance(entry, dict) and entry.get("quote"):
                evidence_path = entry.get("path") or item["path"]
                source_text = next(
                    (row["text"] for row in source_rows
                     if row["path"] in (evidence_path, "work/" + evidence_path,
                                        "output/" + evidence_path)), "")
                evidence.append({
                    "item_id": item["id"],
                    "path": evidence_path,
                    "locator": entry.get("locator") or "",
                    "quote": entry["quote"],
                    "verified": quote_supported(source_text, entry["quote"]),
                })
    summary = str(plan.get("summary") or "").strip()
    if not summary:
        summary = ("已按 taxonomy 将 %d 个输入文件分类，%d 个进入复核队列。"
                   % (counts["classified"], counts["review_queue"]))
    results = {
        "case_id": case_id,
        "mode": "classification",
        "generated_at": now_iso(),
        "summary": summary,
        "taxonomy": {
            "categories": [
                {"name": category,
                 "criterion": taxonomy["criteria"].get(category, ""),
                 "count": sum(1 for item in items if item["category"] == category)}
                for category in taxonomy["categories"]
            ],
            "criteria": taxonomy["criteria"],
            "threshold": taxonomy["threshold"],
        },
        "items": items,
        "counts": counts,
        "coverage": {
            "input_files": [row["path"] for row in materials],
            "classified": sorted({item["path"] for item in items if item["path"]}),
            "unclassified": validation["missing"],
            "unknown_paths": validation["unknown"],
            "no_result": bool(plan.get("no_result")),
        },
        "review_queue": queue_rows,
        "duplicate_groups": plan.get("duplicate_groups") or [],
        "version_groups": plan.get("version_groups") or [],
        "operations": operations,
        "findings": plan.get("findings") or [],
        "evidence": evidence,
        "uncertainties": plan.get("uncertainties") or [],
        "input_snapshot": [{"path": row["path"], "sha256": row["sha256"],
                            "bytes": row["bytes"], "kind": row["kind"]}
                           for row in materials],
        "materialized": bool(materialize_requested),
    }
    index = {
        "case_id": case_id,
        "generated_at": now_iso(),
        "counts": counts,
        "categories": {category: [item["path"] for item in items
                                  if item["category"] == category]
                       for category in taxonomy["categories"]},
        "items": [{
            "path": item["path"],
            "category": item["category"],
            "new_name": item.get("new_name") or "",
            "target": item["target"],
            "tags": item.get("tags") or [],
            "confidence": item["confidence"],
            "hash": item["hash"],
            "review": item["review"],
            "evidence_locator": (as_list(item.get("evidence"))[0].get("locator")
                                 if as_list(item.get("evidence")) and
                                 isinstance(as_list(item.get("evidence"))[0], dict)
                                 else ""),
        } for item in items],
    }
    operations_log = {
        "case_id": case_id,
        "generated_at": now_iso(),
        "materialized": bool(materialize_requested),
        "operations": operations,
        "planned_operations": materialized_operations if not materialize_requested else [],
        "rollback_all": [row.get("rollback") for row in operations
                         if row.get("rollback")],
    }
    return results, index, operations_log


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------

def run(input_dir, work_dir, output_dir, materialize_requested=False,
        check_only=False):
    materials = inventory(input_dir)
    plan_path = find_plan(work_dir)
    if plan_path is None:
        audit = {"ok": False, "problems": ["work/library-plan.json is missing"],
                 "warnings": [], "checks": {"plan_present": False},
                 "generated_at": now_iso()}
        if not check_only:
            write_json(Path(output_dir) / "classification-audit.json", audit)
        print(json.dumps(audit, ensure_ascii=False, indent=2))
        return 2, audit
    plan = load_json(plan_path, {})
    evidence_sources = extra_evidence_sources(work_dir, output_dir)
    problems, warnings, checks, validation = validate_plan(
        plan, materials, materialize_requested, evidence_sources)
    audit = {
        "ok": not problems,
        "plan": Path(plan_path).as_posix(),
        "problems": problems,
        "warnings": warnings,
        "checks": checks,
        "outline": {
            "categories": validation["taxonomy"]["categories"],
            "threshold": validation["taxonomy"]["threshold"],
            "classified": checks.get("classified", 0),
            "unclassified": validation["missing"],
            "review_queue": validation["queue_ids"],
        },
        "generated_at": now_iso(),
    }
    if not check_only:
        write_json(Path(output_dir) / "classification-audit.json", audit)
    if problems:
        print(json.dumps(audit, ensure_ascii=False, indent=2))
        return 1, audit
    results, index, operations_log = build_outputs(
        plan, materials, validation, output_dir, materialize_requested,
        evidence_sources)
    if check_only:
        print(json.dumps(audit, ensure_ascii=False, indent=2))
        return 0, audit
    output = Path(output_dir)
    write_json(output / "library-results.json", results)
    write_json(output / "library-index.json", index)
    write_json(output / "operations.log.json", operations_log)
    summary = {"ok": True, "plan": Path(plan_path).as_posix(),
               "problems": [], "warnings": warnings, "checks": checks,
               "outputs": ["output/library-results.json",
                           "output/library-index.json",
                           "output/operations.log.json"],
               "materialized": bool(materialize_requested),
               "library_tree": "output/library" if materialize_requested else None,
               "generated_at": now_iso()}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0, summary


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

SELF_TEST_FILES = {
    "paper-a.md": (
        "# Deep learning for catalyst discovery\n\n"
        "We train a graph neural network on 12,000 catalyst structures.\n"
        "The model reaches a mean absolute error of 0.08 eV on the test set.\n"
    ),
    "paper-a-copy.md": (
        "# Deep learning for catalyst discovery\n\n"
        "We train a graph neural network on 12,000 catalyst structures.\n"
        "The model reaches a mean absolute error of 0.08 eV on the test set.\n"
    ),
    "paper-a-preprint.md": (
        "# Deep learning for catalyst discovery (preprint)\n\n"
        "This preprint reports a graph neural network trained on 9,000 structures.\n"
        "The mean absolute error is 0.11 eV before peer review.\n"
    ),
    "dataset.csv": (
        "material,band_gap_ev\nCu,0.0\nZnO,3.2\nTiO2,3.0\n"
    ),
    "supplementary.md": (
        "# Supplementary information\n\n"
        "Table S1 lists the hyperparameters used for training.\n"
    ),
    "ambiguous-notes.md": (
        "# Meeting notes\n\n"
        "Possible follow-up: compare with the 2024 baseline, details unclear.\n"
    ),
}


def _self_plan():
    return {
        "case_id": "SYNTH-CLASSIFY",
        "mode": "classification",
        "summary": "Synthetic self-test only.",
        "taxonomy": {
            "categories": ["journal-article", "preprint", "dataset", "supplementary"],
            "criteria": {
                "journal-article": "peer-reviewed article with a journal-style title",
                "preprint": "manuscript labelled preprint or before peer review",
                "dataset": "tabular data with a data schema",
                "supplementary": "supporting information or unclear material",
            },
            "threshold": 0.7,
        },
        "items": [
            {"id": "I001", "path": "paper-a.md", "category": "journal-article",
             "tags": ["catalyst", "graph-neural-network"], "confidence": 0.93,
             "new_name": "2024-gnn-catalyst-discovery.md", "review": False,
             "rationale": "peer-reviewed style article reporting a GNN model",
             "evidence": {"path": "paper-a.md", "locator": "P1",
                          "quote": "We train a graph neural network on 12,000 catalyst structures."}},
            {"id": "I006", "path": "paper-a-copy.md", "category": "journal-article",
             "tags": ["catalyst", "duplicate"], "confidence": 0.99,
             "new_name": "2024-gnn-catalyst-discovery-copy.md", "review": False,
             "rationale": "byte-identical duplicate download of the published article",
             "evidence": {"path": "paper-a-copy.md", "locator": "P1",
                          "quote": "We train a graph neural network on 12,000 catalyst structures."}},
            {"id": "I002", "path": "paper-a-preprint.md", "category": "preprint",
             "tags": ["catalyst", "preprint"], "confidence": 0.9,
             "new_name": "2024-gnn-catalyst-discovery-preprint.md", "review": False,
             "rationale": "explicitly labelled preprint with earlier numbers",
             "evidence": {"path": "paper-a-preprint.md", "locator": "P1",
                          "quote": "This preprint reports a graph neural network trained on 9,000 structures."}},
            {"id": "I003", "path": "dataset.csv", "category": "dataset",
             "tags": ["band-gap", "materials"], "confidence": 0.95,
             "new_name": "band-gap-materials.csv", "review": False,
             "rationale": "tabular materials data with a numeric schema",
             "evidence": {"path": "dataset.csv", "locator": "row 1",
                          "quote": "material,band_gap_ev"}},
            {"id": "I004", "path": "supplementary.md", "category": "supplementary",
             "tags": ["hyperparameters"], "confidence": 0.82,
             "new_name": "supplementary-hyperparameters.md", "review": False,
             "rationale": "supporting table with training hyperparameters",
             "evidence": {"path": "supplementary.md", "locator": "P1",
                          "quote": "Table S1 lists the hyperparameters used for training."}},
            {"id": "I005", "path": "ambiguous-notes.md", "category": "supplementary",
             "tags": ["notes", "unclear"], "confidence": 0.45,
             "new_name": "meeting-notes.md", "review": True,
             "rationale": "meeting note, not a research document; boundary case",
             "evidence": {"path": "ambiguous-notes.md", "locator": "P1",
                          "quote": "Possible follow-up: compare with the 2024 baseline, details unclear."}},
        ],
        "review_queue": [{"item_id": "I005", "reason": "confidence 0.45 < 0.70; ambiguous notes"}],
        "duplicate_groups": [{"id": "D1", "items": ["I001", "I006"],
                              "reason": "byte-identical duplicate download"}],
        "version_groups": [{"id": "V1", "items": ["I001", "I002"],
                            "versions": ["published", "preprint"], "preferred": "I001",
                            "reason": "keep the peer-reviewed version as preferred"}],
        "operations": [],
        "uncertainties": [],
    }


def self_test():
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    with tempfile.TemporaryDirectory(prefix="classify-selftest-") as td:
        root = Path(td)
        input_dir, work, output = root / "input", root / "work", root / "output"
        input_dir.mkdir(parents=True)
        work.mkdir(parents=True)
        for name, text in SELF_TEST_FILES.items():
            write_text(input_dir / name, text)
        write_text(input_dir / "case.json", json.dumps({"case_id": "SYNTH-CLASSIFY"}))
        write_text(work / "library-plan.json",
                   json.dumps(_self_plan(), ensure_ascii=False))
        code, summary = run(input_dir, work, output, materialize_requested=True)
        check("valid_plan_exit", code == 0, str(summary.get("problems")[:3]))
        check("outputs_written", all((output / name).is_file() for name in (
            "library-results.json", "library-index.json", "operations.log.json")))
        results = load_json(output / "library-results.json", {})
        check("all_inputs_classified",
              results.get("counts", {}).get("input_files") == len(SELF_TEST_FILES))
        check("review_queue_present",
              any(row.get("item_id") == "I005"
                  for row in results.get("review_queue") or []))
        check("materialized_tree",
              sum(1 for path in (output / "library").rglob("*") if path.is_file())
              == len(SELF_TEST_FILES))
        operations = load_json(output / "operations.log.json", {})
        check("operations_have_rollback",
              bool(operations.get("operations")) and
              all(row.get("rollback") for row in operations.get("operations") or []))
        check("operation_hashes_match",
              all(row.get("sha_before") == row.get("sha_after")
                  for row in operations.get("operations") or []))

        # Boundary: low-confidence item missing from the review queue.
        bad = _self_plan()
        bad["review_queue"] = []
        write_text(work / "library-plan.json", json.dumps(bad, ensure_ascii=False))
        code_bad, audit_bad = run(input_dir, work, output, check_only=True)
        check("missing_review_entry_rejected",
              code_bad == 1 and any("review_queue" in problem
                                    for problem in audit_bad.get("problems") or []))

        # Boundary: fabricated evidence quote.
        bad_quote = _self_plan()
        bad_quote["items"][0]["evidence"]["quote"] = "this sentence is not in the file"
        write_text(work / "library-plan.json",
                   json.dumps(bad_quote, ensure_ascii=False))
        code_quote, audit_quote = run(input_dir, work, output, check_only=True)
        check("fabricated_quote_rejected",
              code_quote == 1 and any("not found verbatim" in problem
                                      for problem in audit_quote.get("problems") or []))

        # Boundary: missing taxonomy.
        bad_tax = _self_plan()
        bad_tax["taxonomy"] = {}
        write_text(work / "library-plan.json", json.dumps(bad_tax, ensure_ascii=False))
        code_tax, audit_tax = run(input_dir, work, output, check_only=True)
        check("missing_taxonomy_rejected",
              code_tax == 1 and any("taxonomy" in problem
                                    for problem in audit_tax.get("problems") or []))

        # Boundary: missing plan.
        empty_work = root / "empty-work"
        empty_work.mkdir()
        code_none, audit_none = run(input_dir, empty_work, output, check_only=True)
        check("missing_plan_exit_2", code_none == 2)

    ok = all(row["ok"] for row in checks)
    print(json.dumps({"ok": ok, "self_test": True, "checks": checks},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate/materialize a library "
                                                 "classification plan")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--materialize", action="store_true",
                        help="copy files into output/library/<category>/<new_name>")
    parser.add_argument("--check-only", action="store_true",
                        help="validate without writing deliverables")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    code, _ = run(args.input, args.work, args.output,
                  materialize_requested=args.materialize,
                  check_only=args.check_only)
    return code


if __name__ == "__main__":
    sys.exit(main())
