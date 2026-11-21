#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-finalize gate for the reading-annotation / knowledge-sedimentation skill.

Deterministic checks: file hashes, annotation anchors, version/OCR status,
note support, link bases, search index coverage, operations and output
contract coverage.  It writes ``output/self-check.json`` and exits 1 when
``problems`` remain.  A crash is written as ``status=error`` and exits 0.

It never invents annotations or notes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ANCHOR_STATUSES = {
    "verified", "unverified_low_ocr", "version_mismatch",
    "missing_source", "needs_review", "duplicate",
}
ANNOTATION_TYPES = {
    "highlight", "underline", "margin", "handwriting", "sticky",
    "comment", "other",
}
BASES = {"same_quote", "same_section", "same_entity", "same_doi_version",
         "explicit_note"}
OP_STATUSES = {"ok", "skipped", "failed", "needs_review"}
TEXT_SUFFIXES = {".md", ".txt", ".json", ".csv", ".tex", ".yaml", ".yml",
                 ".bib", ".ris", ".log", ".py", ".html"}


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return default


def load_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_space(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def quote_found(source, quote):
    source = norm_space(source)
    quote = norm_space(quote)
    if not quote:
        return False, "empty quote"
    if quote in source:
        return True, "exact"
    parts = [p for p in re.split(r"…|\.\.\.", quote) if norm_space(p)]
    if len(parts) > 1:
        start = 0
        for part in parts:
            part = norm_space(part)
            if len(part) < 3:
                continue
            pos = source.find(part, start)
            if pos < 0:
                return False, "ellipsis part not found"
            start = pos + len(part)
        return True, "ellipsis"
    return False, "quote not found verbatim"


def sha256_file(path):
    try:
        h = hashlib.sha256()
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def norm_hash(value):
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[7:]
    return text


class Sources:
    def __init__(self, input_dir, work_dir):
        self.texts = {}
        self.binary = {}
        self.input_dir = Path(input_dir)
        base = Path(input_dir)
        if base.exists():
            for path in base.rglob("*"):
                if not path.is_file() or path.stat().st_size > 8_000_000:
                    continue
                rel = path.relative_to(base).as_posix()
                if path.suffix.lower() in TEXT_SUFFIXES:
                    text = read_text(path)
                    self.texts[rel] = text
                    self.texts[path.name] = text
                else:
                    self.binary[rel] = path
                    self.binary[path.name] = path
        materials = load_json(Path(work_dir) / "materials.json", {})
        for doc in materials.get("documents") or []:
            text = str(doc.get("text") or "")
            path = str(doc.get("path") or "")
            if text and path:
                self.texts.setdefault(path, text)
                self.texts.setdefault(Path(path).name, text)

    def text(self, path):
        path = str(path or "")
        if not path:
            return None
        if path in self.texts:
            return self.texts[path]
        norm = path.replace("\\", "/")
        if norm in self.texts:
            return self.texts[norm]
        name = Path(norm).name
        if name in self.texts:
            return self.texts[name]
        for key, text in self.texts.items():
            if key.endswith(norm) or norm.endswith(key):
                return text
        return None

    def file(self, path):
        path = str(path or "")
        if not path:
            return None
        if path in self.binary:
            return self.binary[path]
        norm = path.replace("\\", "/")
        if norm in self.binary:
            return self.binary[norm]
        name = Path(norm).name
        if name in self.binary:
            return self.binary[name]
        for key, value in self.binary.items():
            if key.endswith(norm) or norm.endswith(key):
                return value
        return None


def flatten_evidence(value, out):
    if isinstance(value, dict):
        if isinstance(value.get("quote"), str) and value.get("quote").strip():
            out.append({
                "path": value.get("path") or value.get("doc") or "",
                "locator": value.get("locator") or "",
                "quote": value.get("quote", ""),
            })
        for item in value.values():
            flatten_evidence(item, out)
    elif isinstance(value, list):
        for item in value:
            flatten_evidence(item, out)


def item_index(items):
    by_path = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in (item.get("path"), Path(str(item.get("path") or "")).name):
            if key:
                by_path[str(key)] = item
    return by_path


def check_items(items, sources):
    problems, warnings = [], []
    if not isinstance(items, list) or not items:
        warnings.append("items[] is empty; file inventory/version evidence is incomplete")
        return problems, warnings
    seen_hash = {}
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            problems.append("items[%d] is not an object" % index)
            continue
        path = str(item.get("path") or "")
        label = str(item.get("id") or path or "item %d" % index)
        if not path:
            problems.append("%s: missing path" % label)
            continue
        if item.get("status") not in ("indexed", "duplicate", "superseded",
                                      "unreadable", "permission_denied"):
            problems.append("%s: invalid status %r" % (label, item.get("status")))
        source_file = sources.file(path)
        if source_file is None:
            # Text-only files are also handled by Sources.texts.
            if sources.text(path) is None:
                problems.append("%s: input path not found: %s" % (label, path))
                continue
        real_hash = sha256_file(source_file) if source_file else None
        declared = norm_hash(item.get("hash") or item.get("sha256"))
        if declared:
            if real_hash and declared != real_hash:
                problems.append("%s: hash mismatch (declared %s, actual %s)"
                                % (label, declared[:12], real_hash[:12]))
            seen_hash.setdefault(declared, []).append(label)
        else:
            warnings.append("%s: no sha256 hash declared" % label)
        if item.get("status") == "duplicate" and not item.get("duplicate_of"):
            problems.append("%s: duplicate item lacks duplicate_of" % label)
        evidence = []
        flatten_evidence(item.get("evidence"), evidence)
        if not evidence:
            warnings.append("%s: item has no evidence" % label)
        for ev_index, ev in enumerate(evidence, 1):
            text = sources.text(ev.get("path"))
            if text is None:
                continue
            if not quote_found(text, ev.get("quote")):
                problems.append("%s: item evidence %d quote not verbatim" % (label, ev_index))
    for digest, labels in seen_hash.items():
        if len(labels) > 1:
            marked = [lab for lab in labels]
            warnings.append("identical hash shared by %s; ensure duplicate_of is set"
                            % ", ".join(marked))
    return problems, warnings


def check_annotations(annotations, items_by_path, sources):
    problems, warnings = [], []
    if not isinstance(annotations, list) or not annotations:
        problems.append("annotations is missing or empty")
        return problems, warnings
    ids = set()
    for index, ann in enumerate(annotations, 1):
        if not isinstance(ann, dict):
            problems.append("annotation %d is not an object" % index)
            continue
        aid = str(ann.get("id") or ann.get("annotation_id") or "")
        label = aid or "annotation %d" % index
        if not aid:
            problems.append("annotation %d lacks id/annotation_id" % index)
        elif aid in ids:
            problems.append("duplicate annotation id %s" % aid)
        ids.add(aid)
        doc = str(ann.get("doc") or ann.get("path") or "")
        if not doc:
            problems.append("%s: missing doc/path" % label)
        quote = str(ann.get("quote") or "").strip()
        if len(quote) < 2:
            problems.append("%s: missing quote" % label)
        if ann.get("type") not in ANNOTATION_TYPES:
            problems.append("%s: invalid type %r" % (label, ann.get("type")))
        if not str(ann.get("color") or "").strip():
            warnings.append("%s: color is empty" % label)
        status = str(ann.get("anchor_status") or "")
        if status not in ANCHOR_STATUSES:
            problems.append("%s: invalid anchor_status %r" % (label, ann.get("anchor_status")))
        item = items_by_path.get(doc) or items_by_path.get(Path(doc).name)
        current_version = str(item.get("version") or "") if item else ""
        current_hash = norm_hash(item.get("hash") or item.get("sha256")) if item else ""
        ann_version = str(ann.get("version") or "")
        ann_hash = norm_hash(ann.get("doc_sha256") or ann.get("hash"))
        if status == "verified":
            if not (ann.get("page") is not None
                    or ann.get("paragraph")
                    or (isinstance(ann.get("char_start"), int)
                        and isinstance(ann.get("char_end"), int))):
                problems.append("%s: verified annotation lacks page/paragraph/char range" % label)
            if not ann_hash:
                problems.append("%s: verified annotation lacks doc_sha256" % label)
            elif current_hash and ann_hash != current_hash:
                problems.append("%s: verified but doc_sha256 != current item hash" % label)
            if ann_version and current_version and ann_version != current_version:
                problems.append("%s: verified but version %r != current %r"
                                % (label, ann_version, current_version))
        mismatch = ((current_hash and ann_hash and ann_hash != current_hash)
                    or (current_version and ann_version and ann_version != current_version))
        if mismatch and status not in ("version_mismatch", "duplicate"):
            problems.append("%s: annotation belongs to another version; mark version_mismatch"
                            % label)
        if status == "version_mismatch" and ann.get("mapped_to_current") is not False:
            problems.append("%s: version_mismatch must set mapped_to_current=false" % label)
        confidence = ann.get("ocr_confidence")
        if isinstance(confidence, (int, float)) and confidence < 0.60:
            if status != "unverified_low_ocr":
                problems.append("%s: OCR confidence %.2f requires unverified_low_ocr"
                                % (label, confidence))
            if ann.get("mapped_to_current") is not False:
                problems.append("%s: low-OCR annotation must set mapped_to_current=false"
                                % label)
        if status == "unverified_low_ocr" and not isinstance(confidence, (int, float)):
            warnings.append("%s: unverified_low_ocr without ocr_confidence" % label)
        quote_path = str(ann.get("path") or doc)
        source_text = sources.text(quote_path)
        if quote and source_text is not None:
            ok, why = quote_found(source_text, quote)
            if status == "verified" and not ok:
                problems.append("%s: verified quote not verbatim in %s (%s)"
                                % (label, quote_path, why))
            elif not ok:
                warnings.append("%s: quote not found in extracted text of %s" % (label, quote_path))
            start, end = ann.get("char_start"), ann.get("char_end")
            anchor = ann.get("anchor") if isinstance(ann.get("anchor"), dict) else {}
            if not isinstance(start, int):
                start = anchor.get("char_start")
            if not isinstance(end, int):
                end = anchor.get("char_end")
            if isinstance(start, int) and isinstance(end, int):
                if end - start != len(quote):
                    warnings.append("%s: char range length %d != quote length %d"
                                    % (label, end - start, len(quote)))
                elif source_text[start:end] != quote:
                    warnings.append("%s: char range does not equal quote text" % label)
        evidence = []
        flatten_evidence(ann.get("evidence"), evidence)
        if not evidence:
            problems.append("%s: no evidence (annotation export/source)" % label)
        for ev_index, ev in enumerate(evidence, 1):
            text = sources.text(ev.get("path"))
            if text is None:
                continue
            if not quote_found(text, ev.get("quote")):
                problems.append("%s: evidence %d quote not verbatim" % (label, ev_index))
    return problems, warnings


def check_notes(notes, annotation_ids, sources):
    problems, warnings = [], []
    if not isinstance(notes, list) or not notes:
        warnings.append("notes[] is empty; knowledge sedimentation is incomplete")
        return problems, warnings
    note_ids = set()
    for index, note in enumerate(notes, 1):
        if not isinstance(note, dict):
            problems.append("note %d is not an object" % index)
            continue
        nid = str(note.get("id") or note.get("note_id") or "")
        label = nid or "note %d" % index
        if not nid:
            problems.append("note %d lacks id" % index)
        elif nid in note_ids:
            problems.append("duplicate note id %s" % nid)
        note_ids.add(nid)
        anns = note.get("annotation_ids") or note.get("source_annotations") or []
        if not anns:
            problems.append("%s: no annotation_ids" % label)
        for aid in anns:
            if str(aid) not in annotation_ids:
                problems.append("%s: unknown annotation id %r" % (label, aid))
        claims = note.get("claims") or []
        if not claims and not (note.get("conclusion") or note.get("text")):
            problems.append("%s: no claim/conclusion" % label)
        for claim_index, claim in enumerate(claims, 1):
            if not isinstance(claim, dict):
                problems.append("%s: claim %d is not an object" % (label, claim_index))
                continue
            text = str(claim.get("text") or "").strip()
            if not text:
                problems.append("%s: claim %d lacks text" % (label, claim_index))
            evidence = []
            flatten_evidence(claim.get("evidence"), evidence)
            if not evidence:
                problems.append("%s: claim %d has no evidence" % (label, claim_index))
            for ev_index, ev in enumerate(evidence, 1):
                source = sources.text(ev.get("path"))
                if source is None:
                    problems.append("%s: claim %d evidence path not found: %s"
                                    % (label, claim_index, ev.get("path")))
                elif not quote_found(source, ev.get("quote")):
                    problems.append("%s: claim %d evidence quote not verbatim"
                                    % (label, claim_index))
        evidence = []
        flatten_evidence(note.get("evidence"), evidence)
        for ev_index, ev in enumerate(evidence, 1):
            source = sources.text(ev.get("path"))
            if source is not None and not quote_found(source, ev.get("quote")):
                problems.append("%s: note evidence %d quote not verbatim" % (label, ev_index))
    return problems, warnings, note_ids


def check_links(links, annotation_ids, note_ids, annotations_by_id):
    problems, warnings = [], []
    if not isinstance(links, list):
        problems.append("links is not a list")
        return problems, warnings
    seen = set()
    for index, link in enumerate(links, 1):
        if not isinstance(link, dict):
            problems.append("links[%d] is not an object" % index)
            continue
        src = str(link.get("from") or "")
        dst = str(link.get("to") or "")
        label = "%s->%s" % (src or "?", dst or "?")
        if src not in annotation_ids and src not in note_ids:
            problems.append("%s: 'from' id not found" % label)
        if dst not in annotation_ids and dst not in note_ids:
            problems.append("%s: 'to' id not found" % label)
        basis = str(link.get("basis") or "")
        if basis not in BASES:
            problems.append("%s: invalid basis %r" % (label, link.get("basis")))
        pair = tuple(sorted((src, dst)))
        if pair in seen:
            problems.append("%s: duplicate link pair" % label)
        seen.add(pair)
        if link.get("confidence") == "low" and not link.get("needs_review"):
            warnings.append("%s: low-confidence link should set needs_review=true" % label)
        ann = annotations_by_id.get(src) or annotations_by_id.get(dst)
        if isinstance(ann, dict) and ann.get("anchor_status") in (
                "version_mismatch", "unverified_low_ocr", "missing_source"):
            if not link.get("needs_review"):
                problems.append("%s: link touches unverified/old annotation; set needs_review=true"
                                % label)
    return problems, warnings


def check_index(index_rows, annotation_ids, note_ids, annotations_by_id):
    problems, warnings = [], []
    if not isinstance(index_rows, list) or not index_rows:
        warnings.append("search_index is missing/empty; results are not searchable")
        return problems, warnings
    indexed_annotations = set()
    for index, row in enumerate(index_rows, 1):
        if not isinstance(row, dict):
            problems.append("search_index[%d] is not an object" % index)
            continue
        term = str(row.get("term") or "").strip()
        if not term:
            problems.append("search_index[%d] lacks term" % index)
        anns = row.get("annotation_ids") or []
        if not anns:
            problems.append("search_index[%d] lacks annotation_ids" % index)
        for aid in anns:
            if str(aid) not in annotation_ids:
                problems.append("search_index[%d]: unknown annotation id %r"
                                % (index, aid))
            else:
                indexed_annotations.add(str(aid))
                ann = annotations_by_id.get(str(aid))
                if isinstance(ann, dict) and ann.get("anchor_status") != "verified" \
                        and not row.get("needs_review"):
                    warnings.append("search_index[%d]: non-verified annotation %s "
                                    "should be marked needs_review" % (index, aid))
        for nid in row.get("note_ids") or []:
            if str(nid) not in note_ids:
                problems.append("search_index[%d]: unknown note id %r" % (index, nid))
    for aid, ann in annotations_by_id.items():
        if isinstance(ann, dict) and ann.get("anchor_status") == "verified" \
                and aid not in indexed_annotations:
            warnings.append("verified annotation %s is not in search_index" % aid)
    return problems, warnings


def check_operations(operations, uncertainties):
    problems, warnings = [], []
    if not isinstance(operations, list) or not operations:
        warnings.append("operations[] is empty; extraction/recovery is not auditable")
        return problems, warnings
    failed = set()
    for index, op in enumerate(operations, 1):
        if not isinstance(op, dict):
            problems.append("operations[%d] is not an object" % index)
            continue
        if not str(op.get("action") or "").strip():
            problems.append("operations[%d] lacks action" % index)
        if not str(op.get("target") or "").strip():
            problems.append("operations[%d] lacks target" % index)
        status = str(op.get("status") or "")
        if status not in OP_STATUSES:
            problems.append("operations[%d]: invalid status %r" % (index, op.get("status")))
        if not str(op.get("detail") or "").strip():
            warnings.append("operations[%d] lacks detail" % index)
        if status in ("failed", "skipped", "needs_review"):
            failed.add(str(op.get("target")))
    if failed:
        uncertainty_text = json.dumps(uncertainties, ensure_ascii=False)
        for target in failed:
            if target and target not in uncertainty_text:
                warnings.append("operation target %s is failed/skipped but not in uncertainties"
                                % target)
    return problems, warnings


def check_contract(case, work, output):
    problems, warnings = [], []
    contract = case.get("output_contract")
    if not isinstance(contract, dict):
        return problems, warnings
    for key in contract:
        candidates = [Path(output) / "results" / (key + ".json"),
                      Path(output) / "results" / key,
                      Path(output) / (key + ".json"),
                      Path(output) / key]
        present = key in work or any(p.exists() for p in candidates)
        if not present:
            problems.append("output_contract key %r has no work field or "
                            "output/results/%s.json" % (key, key))
    return problems, warnings


def evaluate(input_dir, work_dir, output_dir):
    input_dir, work_dir, output_dir = Path(input_dir), Path(work_dir), Path(output_dir)
    case = load_json(input_dir / "case.json", {})
    work = load_json(work_dir / "library-plan.json", {})
    if not isinstance(work, dict) or not work:
        return {
            "case_id": case.get("case_id") or "CASE",
            "status": "fail",
            "problems": ["work/library-plan.json is missing or empty"],
            "warnings": [],
            "coverage": {},
        }
    sources = Sources(input_dir, work_dir)
    problems, warnings = [], []
    case_id = str(case.get("case_id") or "")
    if case_id and str(work.get("case_id") or "") not in (case_id, ""):
        problems.append("work case_id %r != input case_id %r"
                        % (work.get("case_id"), case_id))
    for field in ("annotations", "evidence"):
        if field not in work or not work.get(field):
            problems.append("required field missing/empty: %s" % field)
    items = work.get("items") or []
    item_problems, item_warnings = check_items(items, sources)
    problems.extend(item_problems)
    warnings.extend(item_warnings)
    items_by_path = item_index(items)
    annotations = work.get("annotations") or []
    ann_problems, ann_warnings = check_annotations(annotations, items_by_path, sources)
    problems.extend(ann_problems)
    warnings.extend(ann_warnings)
    annotation_ids = {str(a.get("id") or a.get("annotation_id"))
                      for a in annotations if isinstance(a, dict)}
    annotation_ids.discard("")
    annotations_by_id = {str(a.get("id") or a.get("annotation_id")): a
                         for a in annotations if isinstance(a, dict)
                         and (a.get("id") or a.get("annotation_id"))}
    notes = work.get("notes") or []
    note_problems, note_warnings, note_ids = check_notes(notes, annotation_ids, sources)
    problems.extend(note_problems)
    warnings.extend(note_warnings)
    note_problems, note_warnings = check_links(
        work.get("links") or [], annotation_ids, note_ids, annotations_by_id)
    problems.extend(note_problems)
    warnings.extend(note_warnings)
    index_problems, index_warnings = check_index(
        work.get("search_index") or [], annotation_ids, note_ids, annotations_by_id)
    problems.extend(index_problems)
    warnings.extend(index_warnings)
    op_problems, op_warnings = check_operations(
        work.get("operations") or [], work.get("uncertainties") or [])
    problems.extend(op_problems)
    warnings.extend(op_warnings)
    contract_problems, contract_warnings = check_contract(case, work, output_dir)
    problems.extend(contract_problems)
    warnings.extend(contract_warnings)
    for name in ("library-results.json", "library-index.json", "operations.log.json"):
        path = output_dir / name
        if not path.is_file():
            problems.append("declared output missing: %s" % name)
        elif path.stat().st_size == 0:
            problems.append("declared output is empty: %s" % name)
    results = load_json(output_dir / "library-results.json", {})
    if isinstance(results, dict):
        for field in ("annotations", "notes", "links", "search_index"):
            if field not in results:
                warnings.append("library-results.json lacks field %s" % field)
    coverage = {
        "items": len(items),
        "annotations": len(annotations),
        "verified": sum(1 for a in annotations
                        if isinstance(a, dict) and a.get("anchor_status") == "verified"),
        "needs_review": sum(1 for a in annotations
                            if isinstance(a, dict)
                            and a.get("anchor_status") in (
                                "unverified_low_ocr", "version_mismatch",
                                "missing_source", "needs_review")),
        "notes": len(notes),
        "links": len(work.get("links") or []),
        "search_index": len(work.get("search_index") or []),
        "operations": len(work.get("operations") or []),
    }
    return {
        "case_id": case_id or str(work.get("case_id") or "CASE"),
        "status": "pass" if not problems else "fail",
        "problems": problems,
        "warnings": warnings,
        "coverage": coverage,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="annotation library pre-finalize gate")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--apply", action="store_true",
                        help="downgrade an existing result.json to partial when problems remain")
    args = parser.parse_args(argv)
    output = Path(args.output)
    try:
        payload = evaluate(args.input, args.work, args.output)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "case_id": "CASE",
            "status": "error",
            "problems": ["gate crashed: %s" % exc],
            "warnings": [],
            "traceback": traceback.format_exc()[-4000:],
            "coverage": {},
        }
    payload["generated_at"] = now_iso()
    write_json(output / "self-check.json", payload)
    applied = None
    if args.apply and payload.get("problems"):
        result_path = output / "result.json"
        existing = load_json(result_path, None)
        if isinstance(existing, dict):
            status = existing.get("status")
            if status not in ("input_error", "partial"):
                existing["status"] = "partial"
                existing["task_status"] = "partial"
                uncertainties = existing.get("uncertainties")
                if not isinstance(uncertainties, list):
                    uncertainties = []
                message = "pre-finalize gate problems remain: %s" % "; ".join(
                    str(x) for x in (payload.get("problems") or [])[:8])
                if not any(isinstance(item, dict) and item.get("item") == "self_check"
                           for item in uncertainties):
                    uncertainties.append({
                        "item": "self_check",
                        "reason": message,
                        "impact": "the delivered result was downgraded to partial",
                        "action": "fix the work file/report and rerun finalize",
                    })
                existing["uncertainties"] = uncertainties
                existing["uncertainty"] = uncertainties
                write_json(result_path, existing)
                applied = {"status": "partial", "result": str(result_path)}
    print(json.dumps({
        "status": payload.get("status"),
        "problems": len(payload.get("problems") or []),
        "warnings": len(payload.get("warnings") or []),
        "coverage": payload.get("coverage"),
        "self_check": str(output / "self-check.json"),
        "applied": applied,
    }, ensure_ascii=False, indent=2))
    for problem in (payload.get("problems") or [])[:30]:
        print("PROBLEM: %s" % problem, file=sys.stderr)
    return 1 if payload.get("status") == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
