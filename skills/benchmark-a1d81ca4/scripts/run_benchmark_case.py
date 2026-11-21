#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end runner for the literature-reading benchmark skill.

One entry point, four commands:

    python3 run_benchmark_case.py prepare  --input /input --work /app/work --output /app/output
    # the model writes /app/work/predictions.jsonl
    python3 run_benchmark_case.py finalize --input /input --work /app/work --output /app/output
    python3 run_benchmark_case.py run      --input /input --work /app/work --output /app/output
    python3 run_benchmark_case.py self-test --json

What the runner does deterministically:

* locates the organizer ``benchmark-workorder.json`` and the paragraph/table
  materials, and refuses to invent items when the workorder is missing;
* copies the workorder items into a machine-readable gold set *verbatim*
  (question, gold points, gold evidence, type, difficulty, capability);
* verifies every gold-evidence quote against the supplied material text and
  records ``verified`` / ``unverified`` instead of assuming a PDF-pixel gold;
* scores model-authored predictions with the packaged ``score_benchmark.py``
  and records per-item 0/1/2 rubric scores, failure types, coverage and
  partial credit;
* re-runs the scorer with the same seed and records whether the output is
  deterministic;
* assembles ``/app/output/result.json`` with the official reading aliases,
  original ``output_contract`` keys, a trace index and honest uncertainty.

The runner never writes an official DSH score.  A local run is explicitly
marked ``official_dsh_score: false`` and its limitations are listed.

Stdlib only.  Exit codes: 0 = completed, 1 = input/contract problems,
2 = the runner itself could not read its own inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

RUNNER_VERSION = "1.0.0"
SKIP_INPUT_NAMES = {"case.json", "task.md"}
SKIP_INPUT_PARTS = {"environment", ".ocr", "__pycache__"}
TEXT_SUFFIXES = {".md", ".txt", ".tex", ".latex", ".json", ".jsonl", ".csv",
                 ".tsv", ".yaml", ".yml", ".toml", ".xml", ".html", ".rst",
                 ".org", ".py", ".log", ".bib", ".ris"}
WORKORDER_NAMES = ("benchmark-workorder.json", "benchmark_workorder.json",
                   "workorder.json")
METRIC_DEFS = [
    ("answer_accuracy", "exact_match / total"),
    ("answer_f1", "macro mean of point F1"),
    ("evidence_precision", "matched_pred_evidence / pred_evidence"),
    ("evidence_recall", "matched_gold_evidence / gold_evidence"),
    ("abstention_correctness", "correct_abstention / total"),
    ("citation_groundedness", "grounded_answers / answered"),
    ("coverage", "items_with_prediction / total"),
    ("hallucination_rate", "1 - mean(answer_precision) over answered items"),
]
ALIAS_MAP = {
    "benchmark_report": "result",
    "benchmark_result": "result",
    "evaluation_report": "result",
    "report": "result",
    "protocol": "protocol",
    "task_set": "protocol",
    "dataset": "dataset_snapshot",
    "dataset_snapshot": "dataset_snapshot",
    "metrics": "metrics",
    "scores": "metrics",
    "scoring": "metrics",
    "per_item_scores": "per_item_scores",
    "item_scores": "per_item_scores",
    "failure_analysis": "failure_analysis",
    "failures": "failure_analysis",
    "version": "version",
    "version_comparison": "version",
    "raw_outputs": "raw_outputs",
    "traces": "raw_outputs",
    "baselines": "baselines",
    "limitations": "limitations",
    "rubric": "rubric",
    "evidence": "evidence_map",
    "evidence_map": "evidence_map",
    "uncertainties": "uncertainties",
    "uncertainty": "uncertainties",
    "change_log": "change_log",
    "changelog": "change_log",
    "artifacts": "artifacts",
}


# ---------------------------------------------------------------------------
# small IO helpers
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


def sha256_bytes(data):
    return hashlib.sha256(data or b"").hexdigest()


def sha256_path(path):
    h = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def rel(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def norm_space(value):
    return " ".join(str(value or "").replace("\r", "\n").split())


def quote_supported(source_text, quote):
    """Whitespace-folded verbatim check; ellipsis-separated segments allowed."""
    source = norm_space(source_text)
    quote = norm_space(quote)
    if not quote:
        return False
    if quote in source:
        return True
    pieces = [p.strip() for p in re.split(r"(?:\.{3,}|…+)", quote) if p.strip()]
    return bool(pieces) and all(piece in source for piece in pieces)


def first(mapping, keys, default=None):
    if not isinstance(mapping, dict):
        return default
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    return [value]


# ---------------------------------------------------------------------------
# input discovery
# ---------------------------------------------------------------------------

def iter_input_files(input_dir):
    root = Path(input_dir)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = set(path.relative_to(root).parts)
        if parts & SKIP_INPUT_PARTS:
            continue
        if path.name in SKIP_INPUT_NAMES:
            continue
        yield path


def find_workorder(input_dir):
    root = Path(input_dir)
    for name in WORKORDER_NAMES:
        path = root / name
        if path.is_file():
            return path
    candidates = [p for p in root.rglob("*.json")
                  if "workorder" in p.name.lower()]
    return sorted(candidates)[0] if candidates else None


def collect_materials(input_dir, workorder_path=None):
    root = Path(input_dir)
    corpus = []
    for path in iter_input_files(root):
        rp = rel(path, root)
        if workorder_path is not None and path == workorder_path:
            continue
        if "workorder" in path.name.lower():
            continue
        suffix = path.suffix.lower()
        row = {"path": rp, "absolute": str(path), "kind": suffix.lstrip(".") or "file",
               "bytes": path.stat().st_size, "sha256": sha256_path(path),
               "readable": suffix in TEXT_SUFFIXES, "text": "", "inline": False}
        if suffix in TEXT_SUFFIXES:
            row["text"] = read_text(path)
        else:
            row["warnings"] = ["no text layer extracted by stdlib runner; "
                               "evidence quotes must come from supplied text"]
        corpus.append(row)
    return corpus


def add_inline_materials(workorder, corpus, work_dir):
    """Support workorders that embed paragraph/table text."""
    if not isinstance(workorder, dict):
        return
    inline = workorder.get("materials")
    rows = []
    if isinstance(inline, dict):
        rows = [{"path": key, "text": value} for key, value in inline.items()]
    elif isinstance(inline, list):
        rows = inline
    for index, row in enumerate(rows, 1):
        if isinstance(row, str):
            name, text = "material-%02d.md" % index, row
        elif isinstance(row, dict):
            name = str(first(row, ("path", "name", "id", "file")) or
                       "material-%02d.md" % index)
            text = first(row, ("text", "content", "body"), "")
        else:
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        name = name.replace("\\", "/").lstrip("/")
        if name.startswith("../") or "/../" in name:
            name = "material-%02d.md" % index
        target = Path(work_dir) / "inline-materials" / name
        write_text(target, text)
        corpus.append({"path": name, "absolute": str(target), "kind": "md",
                       "bytes": target.stat().st_size,
                       "sha256": sha256_path(target), "readable": True,
                       "text": text, "inline": True})


# ---------------------------------------------------------------------------
# workorder normalization
# ---------------------------------------------------------------------------

ITEM_LIST_KEYS = ("items", "tasks", "questions", "benchmark_items", "cases",
                  "cards", "workorder_items", "questions_and_answers")
QUESTION_KEYS = ("question", "prompt", "query", "task", "question_text",
                 "item_text", "statement")


def looks_like_item(value):
    return isinstance(value, dict) and any(value.get(k) for k in QUESTION_KEYS)


def extract_item_list(workorder, depth=0):
    if depth > 6:
        return []
    if isinstance(workorder, list):
        rows = [row for row in workorder if looks_like_item(row)]
        return rows
    if not isinstance(workorder, dict):
        return []
    for key in ITEM_LIST_KEYS:
        value = workorder.get(key)
        if isinstance(value, list):
            rows = [row for row in value if looks_like_item(row)]
            if rows:
                return rows
    for value in workorder.values():
        rows = extract_item_list(value, depth + 1)
        if rows:
            return rows
    return []


def normalize_evidence(value):
    rows = []
    for item in as_list(value):
        if isinstance(item, str):
            rows.append({"locator": item, "quote": "", "ref": item, "verified": False})
            continue
        if not isinstance(item, dict):
            continue
        row = dict(item)
        locator = first(row, ("locator", "paragraph_id", "para_id", "pid",
                              "page", "pageno", "page_number", "section_id",
                              "section", "table_id", "figure_id"))
        row["locator"] = str(locator or "")
        row["quote"] = str(first(row, ("quote", "text", "span", "sentence",
                                       "excerpt"), "") or "")
        row.setdefault("verified", False)
        rows.append(row)
    return rows


def normalize_points(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if isinstance(value, dict):
        value = list(value.values())
    points = []
    for point in as_list(value):
        if isinstance(point, (dict, list)):
            points.append(json.dumps(point, ensure_ascii=False, sort_keys=True))
        else:
            text = str(point).strip()
            if text:
                points.append(text)
    return points


def normalize_item(raw, index):
    item = dict(raw)
    iid = first(raw, ("item_id", "id", "qid", "q_id", "key", "uid"))
    iid = str(iid or "i%02d" % index)
    question = str(first(raw, QUESTION_KEYS, "") or "")
    gold_points = normalize_points(first(
        raw, ("gold_points", "answer_points", "reference_points", "points",
              "answers", "reference_answer", "gold_answer", "answer_key",
              "answer")))
    evidence = normalize_evidence(first(
        raw, ("gold_evidence", "evidence", "source_spans", "spans",
              "provenance", "support", "sources")))
    kind = str(first(raw, ("type", "task_type", "question_type"), "unknown") or "unknown")
    if kind in ("unknown", "") and (raw.get("abstain") or
                                    str(raw.get("capability", "")).lower() == "abstain"):
        kind = "abstain"
    normalized = {
        "item_id": iid,
        "question": question,
        "gold_points": gold_points,
        "gold_evidence": evidence,
        "type": kind,
        "difficulty": str(first(raw, ("difficulty", "level"), "unknown") or "unknown"),
        "capability": str(first(raw, ("capability", "skill", "dimension"),
                                "unknown") or "unknown"),
        "source": str(first(raw, ("source", "path", "material", "doc"),
                            "") or ""),
        "scoring_rule": str(first(raw, ("scoring_rule", "rubric", "criteria"),
                                  "") or ""),
    }
    for key, value in raw.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def normalize_workorder(workorder):
    raw_items = extract_item_list(workorder)
    items = [normalize_item(raw, i) for i, raw in enumerate(raw_items, 1)]
    seen = set()
    for item in items:
        base = item["item_id"]
        suffix = 2
        while item["item_id"] in seen:
            item["item_id"] = "%s-%d" % (base, suffix)
            suffix += 1
        seen.add(item["item_id"])
    return items


def verify_item_evidence(items, corpus):
    for item in items:
        for evidence in item.get("gold_evidence") or []:
            quote = evidence.get("quote") or ""
            if not quote:
                evidence["verified"] = False
                evidence["verify_status"] = "locator_only"
                continue
            target = str(first(evidence, ("path", "source", "doc", "file"),
                               item.get("source") or "") or "")
            matches = [row for row in corpus
                       if row.get("readable") and quote_supported(row.get("text"), quote)]
            if target:
                targeted = [row for row in matches if row.get("path") == target]
                matches = targeted or matches
            if matches:
                evidence["verified"] = True
                evidence["verify_status"] = "quote_found"
                evidence["matched_path"] = matches[0]["path"]
            else:
                evidence["verified"] = False
                evidence["verify_status"] = "quote_not_found"
    return items


# ---------------------------------------------------------------------------
# scorer / validator loading
# ---------------------------------------------------------------------------

def sibling(name):
    here = Path(__file__).resolve().parent
    for candidate in (here / name, here / (name + ".md")):
        if candidate.is_file():
            return candidate
    return None


def load_module(name, filename):
    path = sibling(filename)
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# predictions and scoring
# ---------------------------------------------------------------------------

def load_rows(path):
    text = read_text(path).strip()
    if not text:
        return []
    if text.startswith("["):
        data = load_json(path, [])
        return data if isinstance(data, list) else []
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_parse_error": line[:160]})
    return rows


def verify_prediction_evidence(predictions, corpus):
    """Verify model-authored evidence quotes against the same material text."""
    for row in predictions:
        statuses = []
        for evidence in row.get("evidence") or []:
            quote = evidence.get("quote") or ""
            if not quote:
                evidence["verified"] = False
                evidence["verify_status"] = "locator_only"
                statuses.append(True)  # locator-only is allowed, not a mismatch
                continue
            target = str(first(evidence, ("path", "source", "doc", "file"), "") or "")
            matches = [item for item in corpus
                       if item.get("readable") and
                       quote_supported(item.get("text"), quote)]
            if target:
                matches = [item for item in matches if item.get("path") == target] or matches
            if matches:
                evidence["verified"] = True
                evidence["verify_status"] = "quote_found"
                evidence["matched_path"] = matches[0]["path"]
                statuses.append(True)
            else:
                evidence["verified"] = False
                evidence["verify_status"] = "quote_not_found"
                statuses.append(False)
        row["evidence_verified"] = all(statuses) if statuses else None
    return predictions


def load_predictions(work_dir):
    work = Path(work_dir)
    candidates = [work / "predictions.jsonl", work / "pred.jsonl",
                  work / "predictions.json", work / "pred.json"]
    for path in candidates:
        if not path.is_file():
            continue
        raw = load_rows(path)
        if isinstance(raw, dict):
            raw = [dict(value, item_id=key) if isinstance(value, dict)
                   else {"item_id": key, "answer_points": [str(value)]}
                   for key, value in raw.items()]
        rows = []
        errors = []
        for index, row in enumerate(raw, 1):
            if not isinstance(row, dict) or "_parse_error" in row:
                errors.append("prediction line %d is not a JSON object" % index)
                continue
            iid = str(first(row, ("item_id", "id", "qid", "q_id"), "") or "")
            if not iid:
                errors.append("prediction line %d has no item_id" % index)
                continue
            answer_points = normalize_points(first(
                row, ("answer_points", "points", "answers", "answer")))
            evidence = normalize_evidence(first(
                row, ("evidence", "citations", "sources", "spans")))
            rows.append({
                "item_id": iid,
                "answer_points": answer_points,
                "evidence": evidence,
                "abstain": bool(row.get("abstain")),
                "reason": str(first(row, ("reason", "rationale", "note"), "") or ""),
                "raw": row,
            })
        return rows, errors, str(path)
    return [], ["no predictions file (expected work/predictions.jsonl)"], ""


def gold_rows(items):
    rows = []
    for item in items:
        rows.append({
            "item_id": item["item_id"],
            "question": item["question"],
            "gold_points": item["gold_points"],
            "gold_evidence": item["gold_evidence"],
            "type": item["type"],
            "difficulty": item["difficulty"],
            "capability": item["capability"],
            "source": item["source"],
            "scoring_rule": item["scoring_rule"],
            "abstain": item["type"].lower() == "abstain",
        })
    return rows


def score_once(gold, predictions, run_id, seed, bootstrap, synthetic=False):
    module = load_module("score_benchmark", "score_benchmark.py")
    if module is None:
        return None, "score_benchmark.py not found next to the runner"
    pred_rows = [{"item_id": row["item_id"],
                  "answer_points": row["answer_points"],
                  "evidence": row["evidence"],
                  "abstain": row["abstain"]} for row in predictions]
    try:
        report = module.score(gold, pred_rows, bootstrap_reps=bootstrap, seed=seed,
                              run_id=run_id, synthetic=synthetic)
    except Exception as exc:  # noqa: BLE001
        return None, "scorer failed: %s: %s" % (type(exc).__name__, exc)
    return report, ""


def rubric_for(item, row):
    """0/1/2 rubric per item, derived only from scorer facts."""
    iid = item["item_id"]
    gold_abstain = bool(row.get("gold_abstain"))
    pred_abstain = bool(row.get("pred_abstain"))
    answered = bool(row.get("answered"))
    answer_exact = float(row.get("answer_exact") or 0)
    answer_f1 = float(row.get("answer_f1") or 0)
    answer_precision = float(row.get("answer_precision") or 0)
    evidence_recall = float(row.get("evidence_recall") or 0)
    evidence_precision = float(row.get("evidence_precision") or 0)
    has_pred_evidence = evidence_precision > 0 or evidence_recall > 0
    if not answered:
        return {"score_0_2": 0, "answer_score": 0, "evidence_score": 0,
                "status": "missing", "failure_type": "missing_prediction",
                "detail": "no prediction row for this item"}
    if gold_abstain:
        if pred_abstain:
            score = 2 if has_pred_evidence else 1
            return {"score_0_2": score,
                    "answer_score": 2,
                    "evidence_score": 2 if has_pred_evidence else 1,
                    "status": "ok",
                    "failure_type": None if score == 2 else "missing_evidence",
                    "detail": "correct abstention" if score == 2 else
                              "abstained without a locatable reason"}
        return {"score_0_2": 0, "answer_score": 0, "evidence_score": 0,
                "status": "wrong", "failure_type": "abstain_error",
                "detail": "gold requires an abstention but the prediction answered"}
    if pred_abstain:
        return {"score_0_2": 0, "answer_score": 0, "evidence_score": 0,
                "status": "wrong", "failure_type": "abstain_error",
                "detail": "prediction abstained on an answerable item"}
    if answer_exact >= 1.0 and evidence_recall >= 0.999:
        return {"score_0_2": 2, "answer_score": 2,
                "evidence_score": 2 if evidence_precision >= 0.5 else 1,
                "status": "ok", "failure_type": None,
                "detail": "exact answer with locatable evidence"}
    if answer_exact >= 1.0:
        return {"score_0_2": 1, "answer_score": 2, "evidence_score": 0,
                "status": "partial", "failure_type": "missing_evidence",
                "detail": "answer correct but gold evidence is not cited"}
    if answer_f1 > 0:
        if evidence_recall < 0.999 and not has_pred_evidence:
            failure, detail = "missing_evidence", "partial answer without evidence"
        elif answer_precision < 0.5:
            failure, detail = "overclaim", "extra or unsupported points"
        else:
            failure, detail = "wrong_value", "only some gold points matched"
        return {"score_0_2": 1, "answer_score": 1,
                "evidence_score": 2 if evidence_recall >= 0.999 else
                                 (1 if has_pred_evidence else 0),
                "status": "partial", "failure_type": failure, "detail": detail}
    return {"score_0_2": 0, "answer_score": 0,
            "evidence_score": 2 if evidence_recall >= 0.999 else
                             (1 if has_pred_evidence else 0),
            "status": "wrong", "failure_type": "wrong_value",
            "detail": "no gold point matched"}


def build_per_item(items, report, predictions):
    pred_by_id = {row["item_id"]: row for row in predictions}
    rows = []
    for index, item in enumerate(items):
        score_row = report["items"][index] if index < len(report["items"]) else {}
        rubric = rubric_for(item, score_row)
        pred = pred_by_id.get(item["item_id"])
        pred_verified = (pred or {}).get("evidence_verified")
        pred_evidence = (pred or {}).get("evidence") or []
        mismatch = (pred is not None and any(
            evidence.get("quote") and not evidence.get("verified")
            for evidence in pred_evidence))
        if mismatch and rubric["score_0_2"] > 0:
            rubric = dict(rubric)
            rubric["score_0_2"] = min(rubric["score_0_2"], 1)
            rubric["evidence_score"] = min(rubric.get("evidence_score", 0), 1)
            rubric["status"] = "partial"
            rubric["failure_type"] = "evidence_mismatch"
            rubric["detail"] = ("prediction cites a quote that is not in the "
                                "supplied material text")
        rows.append({
            "item_id": item["item_id"],
            "question": item["question"],
            "type": item["type"],
            "difficulty": item["difficulty"],
            "capability": item["capability"],
            "gold_points": item["gold_points"],
            "predicted_points": (pred or {}).get("answer_points") or [],
            "predicted_abstain": bool((pred or {}).get("abstain")),
            "answer_exact": score_row.get("answer_exact"),
            "answer_f1": score_row.get("answer_f1"),
            "answer_precision": score_row.get("answer_precision"),
            "evidence_precision": score_row.get("evidence_precision"),
            "evidence_recall": score_row.get("evidence_recall"),
            "abstention_ok": score_row.get("abstention_ok"),
            "citation_groundedness": score_row.get("citation_groundedness"),
            "prediction_evidence_verified": pred_verified,
            **rubric,
        })
    return rows


def build_failure_analysis(per_item):
    by_type = {}
    by_capability = {}
    examples = []
    for row in per_item:
        failure = row.get("failure_type")
        if not failure:
            continue
        by_type[failure] = by_type.get(failure, 0) + 1
        capability = row.get("capability") or "unknown"
        by_capability[capability] = by_capability.get(capability, 0) + 1
        if len(examples) < 12:
            examples.append({"item_id": row["item_id"],
                             "capability": capability,
                             "failure_type": failure,
                             "detail": row.get("detail", "")})
    scores = [row["score_0_2"] for row in per_item]
    recommendations = []
    if by_type.get("missing_evidence"):
        recommendations.append("strengthen paragraph/table quote checks; require "
                               "locator + verbatim quote for every answer point")
    if by_type.get("wrong_value"):
        recommendations.append("add targeted items for the failed capability and "
                               "re-score only the affected item IDs")
    if by_type.get("abstain_error"):
        recommendations.append("add an adversarial abstain item and score "
                               "abstention separately from answer accuracy")
    if by_type.get("overclaim"):
        recommendations.append("penalize unsupported extra points with precision, "
                               "not only exact match")
    if by_type.get("missing_prediction"):
        recommendations.append("run the runner with a prediction row for every "
                               "workorder item; missing items count in coverage")
    if not recommendations:
        recommendations.append("no failure in this run; keep the 0/1/2 rubric and "
                               "n<30 confidence intervals for the next version")
    return {
        "total_items": len(per_item),
        "failed_items": sum(1 for row in per_item if row.get("failure_type")),
        "by_type": by_type,
        "by_capability": by_capability,
        "examples": examples,
        "recommendations": recommendations,
        "partial_credit": {
            "items_with_partial_credit": sum(1 for value in scores if value == 1),
            "items_full_credit": sum(1 for value in scores if value == 2),
            "items_zero": sum(1 for value in scores if value == 0),
            "mean_score_0_2": round(sum(scores) / len(scores), 6) if scores else None,
        },
    }


# ---------------------------------------------------------------------------
# result envelope
# ---------------------------------------------------------------------------

def contract_keys(case):
    contract = (case or {}).get("output_contract")
    if isinstance(contract, list):
        return [str(key) for key in contract]
    if isinstance(contract, dict):
        return [str(key) for key in contract]
    return []


def contract_alias(key):
    norm = re.sub(r"[^0-9a-z一-鿿]+", "_", key.lower()).strip("_")
    if norm in ALIAS_MAP:
        return ALIAS_MAP[norm]
    tokens = {
        "protocol": "protocol", "题卡": "protocol", "题集": "protocol",
        "metric": "metrics", "指标": "metrics", "score": "per_item_scores",
        "评分": "per_item_scores", "failure": "failure_analysis",
        "失败": "failure_analysis", "version": "version", "版本": "version",
        "result": "result", "结果": "result", "report": "result",
        "报告": "result", "baseline": "baselines", "基线": "baselines",
        "artifact": "artifacts", "产物": "artifacts",
        "trace": "raw_outputs", "raw": "raw_outputs", "原始": "raw_outputs",
        "evidence": "evidence_map", "证据": "evidence_map",
        "uncertain": "uncertainties", "不确定": "uncertainties",
        "change": "change_log", "变更": "change_log",
        "dataset": "dataset_snapshot", "rubric": "rubric",
        "limitation": "limitations", "限制": "limitations",
    }
    for token, target in tokens.items():
        if token in norm:
            return target
    return None


def apply_contract(result, case, artifacts):
    coverage = []
    for key in contract_keys(case):
        present = key in result and result[key] not in (None, "", [], {})
        if not present:
            alias = contract_alias(key)
            if alias and alias in result and result[alias] not in (None, "", [], {}):
                result[key] = result[alias]
                present = True
            else:
                stem = re.sub(r"[^0-9a-z一-鿿]+", "_", key.lower()).strip("_")
                for artifact in artifacts:
                    artifact_stem = re.sub(
                        r"[^0-9a-z一-鿿]+", "_",
                        Path(str(artifact.get("path"))).stem.lower()).strip("_")
                    if stem and (stem == artifact_stem or stem in artifact_stem
                                 or artifact_stem in stem):
                        result[key] = artifact.get("path")
                        present = True
                        break
        coverage.append({"field": key, "present": present})
    result["output_contract"] = (case or {}).get("output_contract") or {}
    result["contract_coverage"] = coverage
    return coverage


def evaluate_validation(protocol, metrics, result, output_dir):
    module = load_module("validate_benchmark", "validate_benchmark.py")
    if module is None:
        return {"ok": None, "problems": [], "warnings": [],
                "detail": "validate_benchmark.py not found; audit_unavailable"}
    problems, warnings, checks = [], [], {}
    try:
        module.check_protocol(protocol, str(output_dir), problems, warnings, checks)
        module.check_metrics(metrics, protocol, problems, warnings, checks)
        module.check_result(result, protocol, problems, warnings, checks)
    except Exception as exc:  # noqa: BLE001
        return {"ok": None, "problems": [], "warnings": [],
                "detail": "validator failed: %s: %s" % (type(exc).__name__, exc)}
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "checks": checks}


def provisional_result(case_id, workorder_path, status, reason, case=None):
    result = {
        "case_id": case_id,
        "status": status,
        "task_status": status,
        "profile": "reading",
        "result": {"summary": reason, "mode": "benchmark"},
        "claims": [],
        "uncertainties": [{"item": "deliverable_pending", "reason": reason,
                           "impact": "result is provisional",
                           "action": "write work/predictions.jsonl and run finalize"}],
        "uncertainty": [],
        "workorder": {"path": workorder_path, "items": [], "source_conditioned": bool(workorder_path)},
        "metrics": [],
        "per_item_scores": [],
        "failure_analysis": {},
        "output_contract": (case or {}).get("output_contract") or {},
        "change_log": [],
        "artifacts": [],
        "official_dsh_score": False,
        "runner_version": RUNNER_VERSION,
    }
    result["uncertainty"] = result["uncertainties"]
    return result


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_prepare(args):
    input_dir, work, output = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    case = load_json(input_dir / "case.json", {})
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    workorder_path = find_workorder(input_dir)
    workorder = load_json(workorder_path, {}) if workorder_path else {}
    items = normalize_workorder(workorder) if workorder_path else []
    corpus = collect_materials(input_dir, workorder_path)
    add_inline_materials(workorder, corpus, work)
    verify_item_evidence(items, corpus)
    if workorder_path:
        write_json(work / "workorder.json", {
            "source_path": rel(workorder_path, input_dir),
            "sha256": sha256_path(workorder_path),
            "raw": workorder,
            "normalized_items": items,
        })
    write_json(work / "materials.json", {
        "case_id": case_id,
        "materials": [{k: v for k, v in row.items() if k != "text"}
                      | {"chars": len(row.get("text") or ""),
                         "excerpt": (row.get("text") or "")[:400]}
                      for row in corpus],
    })
    write_json(work / "gold.jsonl.manifest", {"items": gold_rows(items)})
    write_text(work / "gold.jsonl",
               "\n".join(json.dumps(row, ensure_ascii=False) for row in gold_rows(items)) + "\n")
    template = "\n".join(json.dumps({
        "item_id": item["item_id"],
        "answer_points": [],
        "evidence": [],
        "abstain": item["type"].lower() == "abstain",
        "reason": "",
    }, ensure_ascii=False) for item in items)
    write_text(work / "predictions.template.jsonl", template + "\n")
    write_text(work / "README-next.md", (
        "# Benchmark runner\n\n"
        "- Workorder: `%s` (%d items)\n"
        "- Gold written to `work/gold.jsonl` verbatim from the workorder.\n"
        "- Fill `work/predictions.jsonl` (copy `predictions.template.jsonl`) with\n"
        "  `item_id`, `answer_points`, `evidence` (locator + verbatim quote) and\n"
        "  `abstain`; then run `run_benchmark_case.py finalize`.\n"
        "- Do not add items that are absent from the workorder.\n"
    ) % (rel(workorder_path, input_dir) if workorder_path else "MISSING",
         len(items)))
    status = "partial" if workorder_path else "input_error"
    result = provisional_result(case_id, rel(workorder_path, input_dir)
                                if workorder_path else "",
                                status,
                                "inventory/gold written; waiting for predictions"
                                if workorder_path else
                                "benchmark-workorder.json is missing; no items invented",
                                case=case)
    if workorder_path:
        apply_contract(result, case, [])
    write_json(output / "result.json", result)
    print(json.dumps({"command": "prepare", "case_id": case_id,
                      "workorder": rel(workorder_path, input_dir) if workorder_path else None,
                      "items": len(items), "materials": len(corpus),
                      "status": status, "work": str(work), "output": str(output)},
                     ensure_ascii=False, indent=2))
    return 0 if workorder_path else 1


def build_protocol(case_id, items, workorder_path, input_dir):
    return {
        "schema_version": 1,
        "case_id": case_id,
        "task": "literature reading QA benchmark",
        "source_conditioned": True,
        "workorder": {
            "path": rel(workorder_path, input_dir) if workorder_path else "",
            "sha256": sha256_path(workorder_path) if workorder_path else "",
        },
        "items": [{
            "item_id": item["item_id"],
            "question": item["question"],
            "gold_points": item["gold_points"],
            "gold_evidence": item["gold_evidence"],
            "type": item["type"],
            "difficulty": item["difficulty"],
            "capability": item["capability"],
            "source": item["source"],
            "scoring_rule": item["scoring_rule"],
        } for item in items],
        "capabilities": sorted({item["capability"] for item in items
                                if item["capability"]}),
        "types": sorted({item["type"] for item in items if item["type"]}),
        "metrics": [{"name": name, "formula": formula, "range": [0, 1]}
                    for name, formula in METRIC_DEFS],
        "scoring_tool": {
            "path": "scripts/score_benchmark.py",
            "usage": "python3 scripts/score_benchmark.py --gold work/gold.jsonl "
                     "--pred work/predictions.jsonl --out work/metrics.json --json",
        },
        "baselines": [
            {"name": "abstain_all", "description": "所有题都拒答"},
            {"name": "empty_answer", "description": "所有题都返回空答案"},
        ],
        "rubric": {
            "scale": "0/1/2",
            "definition": {
                "2": "full answer + locatable evidence (or correct abstention with reason)",
                "1": "partial answer or correct answer without evidence",
                "0": "wrong, missing or wrong abstention",
            },
        },
        "limitations": [
            "n equals the workorder size (four items in the organizer draft); "
            "confidence intervals are wide and must not be extrapolated.",
            "This is a local scorer run on model-authored predictions, not an "
            "official DSH score.",
            "Gold evidence is checked against supplied paragraph/table text, not "
            "PDF pixels; unverified quotes are marked instead of assumed.",
        ],
    }


def cmd_finalize(args):
    input_dir, work, output = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    case = load_json(input_dir / "case.json", {})
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    workorder_path = find_workorder(input_dir)
    if workorder_path is None:
        result = provisional_result(case_id, "", "input_error",
                                    "benchmark-workorder.json is missing; "
                                    "no items or metrics were fabricated",
                                    case=case)
        result["uncertainties"].append({
            "item": "workorder", "reason": "missing benchmark workorder",
            "impact": "cannot build a source-conditioned benchmark",
            "action": "provide benchmark-workorder.json under /input"})
        write_json(output / "result.json", result)
        print(json.dumps({"command": "finalize", "case_id": case_id,
                          "status": "input_error", "items": 0}, ensure_ascii=False,
                         indent=2))
        return 1
    workorder = load_json(workorder_path, {})
    items = normalize_workorder(workorder)
    corpus = collect_materials(input_dir, workorder_path)
    add_inline_materials(workorder, corpus, work)
    verify_item_evidence(items, corpus)
    gold = gold_rows(items)
    protocol = build_protocol(case_id, items, workorder_path, input_dir)
    predictions, pred_errors, pred_path = load_predictions(work)
    verify_prediction_evidence(predictions, corpus)

    bootstrap = int(os.environ.get("BENCHMARK_BOOTSTRAP", "1000") or 1000)
    seed = int(os.environ.get("BENCHMARK_SEED", "42") or 42)
    run_id = "run-001"
    report, score_error = score_once(gold, predictions, run_id, seed, bootstrap)
    if report is None:
        result = provisional_result(case_id, rel(workorder_path, input_dir),
                                    "partial", score_error, case=case)
        result["metrics"] = []
        result["uncertainties"].append({
            "item": "scoring", "reason": score_error,
            "impact": "no metric values are reported",
            "action": "restore scripts/score_benchmark.py and rerun finalize"})
        write_json(output / "result.json", result)
        print(json.dumps({"command": "finalize", "case_id": case_id,
                          "status": "partial", "error": score_error},
                         ensure_ascii=False, indent=2))
        return 1

    # Determinism check: same gold/prediction rows and same seed.
    repeat, repeat_error = score_once(gold, predictions, run_id, seed, bootstrap)
    deterministic = bool(repeat is not None and
                         json.dumps(repeat.get("metrics"), sort_keys=True) ==
                         json.dumps(report.get("metrics"), sort_keys=True) and
                         json.dumps(repeat.get("items"), sort_keys=True) ==
                         json.dumps(report.get("items"), sort_keys=True))

    per_item = build_per_item(items, report, predictions)
    failure = build_failure_analysis(per_item)
    metrics = report.get("metrics") or []
    by_name = {row.get("name"): row for row in metrics}
    results_row = {
        "run_id": run_id,
        "n": len(gold),
        "metrics": {name: (by_name.get(name) or {}).get("value") for name in
                    [definition[0] for definition in METRIC_DEFS]},
        "ci95": {name: (by_name.get(name) or {}).get("ci95") for name in
                 [definition[0] for definition in METRIC_DEFS]},
        "repeats": 1,
        "deterministic": deterministic,
        "synthetic": False,
        "official_dsh_score": False,
        "score_source": "local score_benchmark.py run on model-authored predictions",
    }
    evidence_map = []
    for item in items:
        for evidence in item.get("gold_evidence") or []:
            evidence_map.append({
                "item_id": item["item_id"],
                "source": evidence.get("matched_path") or item.get("source") or "",
                "locator": evidence.get("locator") or "",
                "quote": evidence.get("quote") or "",
                "verified": bool(evidence.get("verified")),
                "verify_status": evidence.get("verify_status") or "unknown",
            })
    unverified = [row for row in evidence_map
                  if row["quote"] and not row["verified"]]
    missing_pred = int((report.get("counts") or {}).get("missing_pred") or 0)
    scorer_errors = list(pred_errors) + list(report.get("errors") or [])
    # Copy the scorer/validator before validation so scoring_tool.path resolves
    # under output/scripts/ and a reviewer can rerun the exact tools.
    scripts_dir = output / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in ("score_benchmark.py", "validate_benchmark.py"):
        source = sibling(name)
        if source is not None:
            shutil.copy2(source, scripts_dir / name)
    validation = evaluate_validation(protocol, report, {
        "case_id": case_id, "status": "partial", "protocol": protocol,
        "metrics": metrics, "baselines": report.get("baselines") or [],
        "results": [results_row],
        "limitations": protocol["limitations"], "uncertainty": [],
        "change_log": [],
    }, output)

    uncertainties = []
    if pred_errors:
        uncertainties.append({"item": "predictions",
                              "reason": "; ".join(pred_errors),
                              "impact": "some prediction rows were not scored",
                              "action": "fix work/predictions.jsonl"})
    if missing_pred:
        uncertainties.append({"item": "coverage",
                              "reason": "%d gold items have no prediction" % missing_pred,
                              "impact": "coverage metric is below 1; per-item rows show 0",
                              "action": "add a prediction row for every workorder item"})
    for row in unverified[:20]:
        uncertainties.append({
            "item": "gold_evidence:%s" % row["item_id"],
            "reason": "quote not found in supplied material text",
            "impact": "gold evidence cannot be independently verified",
            "action": "re-extract the verbatim quote or mark the item not_provided"})
    for problem in validation.get("problems") or []:
        uncertainties.append({"item": "validation", "reason": problem,
                              "impact": "contract gate did not pass",
                              "action": "fix the protocol/result and rerun finalize"})
    for warning in (validation.get("warnings") or [])[:20]:
        uncertainties.append({"item": "validation_warning", "reason": warning,
                              "impact": "non-blocking contract warning",
                              "action": "review before the next dataset version"})
    for name in by_name:
        metric = by_name[name]
        if metric.get("value") is None:
            uncertainties.append({
                "item": "metric:%s" % name,
                "reason": "scorer returned null (n=0 or no answered items)",
                "impact": "metric not interpretable for this run",
                "action": "collect predictions before citing the metric"})
    if not deterministic:
        uncertainties.append({
            "item": "determinism", "reason": repeat_error or
            "repeat run produced different metrics/items",
            "impact": "scores cannot be reproduced with the same seed",
            "action": "inspect scorer randomness and rerun"})

    unverified_pred = [row for row in per_item
                       if row.get("failure_type") == "evidence_mismatch"]
    for row in unverified_pred[:20]:
        uncertainties.append({
            "item": "prediction_evidence:%s" % row["item_id"],
            "reason": "the prediction cites a quote that is not in the material",
            "impact": "the evidence score was capped at 1/2",
            "action": "replace the quote with a verbatim material sentence"})
    coverage_ok = len(predictions) >= len(gold) and missing_pred == 0
    metrics_ok = all(metric.get("value") is not None or name == "hallucination_rate"
                     for name, metric in by_name.items())
    evidence_ok = not unverified
    pred_evidence_ok = not unverified_pred
    validation_ok = validation.get("ok") is not False
    status = "answered" if (coverage_ok and metrics_ok and evidence_ok and
                            pred_evidence_ok and validation_ok and
                            deterministic and not scorer_errors) else "partial"

    claims = []
    for item in items:
        claims.append({
            "claim_id": item["item_id"],
            "text": item["question"],
            "kind": "reported",
            "evidence": [{"source": evidence.get("matched_path") or
                          evidence.get("path") or item.get("source") or "",
                          "locator": evidence.get("locator") or "",
                          "quote": evidence.get("quote") or ""}
                         for evidence in item.get("gold_evidence") or []],
        })
    source_hashes = [{"path": row["path"], "sha256": row["sha256"],
                      "bytes": row["bytes"], "kind": row["kind"],
                      "inline": row.get("inline", False)} for row in corpus]
    workorder_sha = sha256_path(workorder_path)
    version_file = load_json(work / "version.json", {})
    version = {
        "version_id": version_file.get("version_id") or "v1",
        "workorder_sha256": workorder_sha,
        "changed_items": version_file.get("changed_items") or [],
        "comparable_previous_scores": bool(
            version_file.get("comparable_previous_scores", False)),
        "reason": version_file.get("reason") or
                  "题卡、材料或评分规则变化后，历史总分不可直接比较；逐题分数需按 item_id 对齐。",
    }
    raw_outputs = {
        "workorder": rel(workorder_path, input_dir),
        "gold": "work/gold.jsonl",
        "predictions": rel(Path(pred_path), work) if pred_path else "",
        "metrics": "work/metrics.json",
        "protocol": "work/benchmark-protocol.json",
        "per_item_scores": "work/per-item-scores.json",
        "failure_analysis": "work/failure-analysis.json",
    }
    # Persist process artifacts under work/ and a review copy under output/.
    write_text(work / "gold.jsonl",
               "\n".join(json.dumps(row, ensure_ascii=False) for row in gold) + "\n")
    write_json(work / "benchmark-protocol.json", protocol)
    write_json(work / "metrics.json", report)
    write_json(work / "per-item-scores.json", per_item)
    write_json(work / "failure-analysis.json", failure)
    write_json(work / "version.json", version)
    write_json(work / "evidence-map.json", evidence_map)
    output_work = output / "work"
    output_work.mkdir(parents=True, exist_ok=True)
    for name in ("gold.jsonl", "benchmark-protocol.json", "metrics.json",
                 "per-item-scores.json", "failure-analysis.json",
                 "evidence-map.json", "version.json"):
        shutil.copy2(work / name, output_work / name)
    if pred_path:
        shutil.copy2(pred_path, output_work / Path(pred_path).name)
    scripts_dir = output / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in ("score_benchmark.py", "validate_benchmark.py"):
        source = sibling(name)
        if source is not None:
            shutil.copy2(source, scripts_dir / name)
    artifacts = []
    for path, role in (
            (output / "work" / "benchmark-protocol.json", "protocol"),
            (output / "work" / "metrics.json", "metrics"),
            (output / "work" / "per-item-scores.json", "per_item_scores"),
            (output / "work" / "failure-analysis.json", "failure_analysis"),
            (output / "work" / "gold.jsonl", "gold"),
            (output / "work" / "evidence-map.json", "evidence_map"),
            (output / "work" / "version.json", "version"),
            (output / "scripts" / "score_benchmark.py", "scoring_tool")):
        if path.is_file():
            artifacts.append({"role": role, "path": rel(path, output),
                              "format": path.suffix.lstrip(".") or "file",
                              "bytes": path.stat().st_size, "exists": True})

    summary = ("按 workorder 构建 %d 题 benchmark，真实运行 score_benchmark.py："
               "coverage=%s，answer_accuracy=%s，rubric 均分=%s。本地运行，"
               "不是官方 DSH 分数。") % (
        len(items),
        (by_name.get("coverage") or {}).get("value"),
        (by_name.get("answer_accuracy") or {}).get("value"),
        failure["partial_credit"]["mean_score_0_2"])
    result = {
        "case_id": case_id,
        "status": status,
        "task_status": status,
        "profile": "reading",
        "result": {
            "summary": summary,
            "mode": "benchmark",
            "run_scope": "local_skill_run",
            "official_dsh_score": False,
            "items": len(items),
            "predictions": len(predictions),
            "coverage": (by_name.get("coverage") or {}).get("value"),
            "scorer_errors": scorer_errors,
            "deterministic": deterministic,
        },
        "claims": claims,
        "uncertainties": uncertainties,
        "uncertainty": uncertainties,
        "workorder": {
            "path": rel(workorder_path, input_dir),
            "sha256": workorder_sha,
            "items": [item["item_id"] for item in items],
            "item_count": len(items),
            "source_conditioned": True,
        },
        "dataset_snapshot": {
            "version_id": version["version_id"],
            "workorder_sha256": workorder_sha,
            "source_hashes": source_hashes,
            "items": len(items),
        },
        "protocol": protocol,
        "baselines": report.get("baselines") or [],
        "results": [results_row],
        "metrics": metrics,
        "per_item_scores": per_item,
        "failure_analysis": failure,
        "rubric": {
            "scale": "0/1/2",
            "total": sum(row["score_0_2"] for row in per_item),
            "max": 2 * len(per_item),
            "mean": failure["partial_credit"]["mean_score_0_2"],
            "partial_credit_items": failure["partial_credit"]["items_with_partial_credit"],
        },
        "evidence_map": evidence_map,
        "trace_index": [{
            "item_id": row["item_id"],
            "gold_row": "work/gold.jsonl#%d" % (index + 1),
            "prediction_row": row["item_id"] if row["item_id"] in
                              {pred["item_id"] for pred in predictions} else None,
            "scorer_item_index": index,
            "run_id": run_id,
            "metrics": "work/metrics.json",
            "score_0_2": row["score_0_2"],
        } for index, row in enumerate(per_item)],
        "determinism": {
            "repeat_runs": 2,
            "same_seed": seed,
            "identical_output": deterministic,
            "detail": repeat_error or ("repeat run matched metrics and per-item rows"
                                       if deterministic else
                                       "repeat run changed the output"),
        },
        "version": version,
        "raw_outputs": raw_outputs,
        "artifacts": artifacts,
        "limitations": protocol["limitations"],
        "validation": validation,
        "change_log": [
            {"step": 1, "action": "读取 workorder 并逐字复制题卡",
             "evidence": "work/workorder.json"},
            {"step": 2, "action": "核验 gold evidence 是否能在材料中逐字找到",
             "evidence": "work/evidence-map.json"},
            {"step": 3, "action": "真实运行 score_benchmark.py",
             "evidence": "work/metrics.json"},
            {"step": 4, "action": "生成 0/1/2 逐题分数、失败分析与版本快照",
             "evidence": "work/per-item-scores.json"},
        ],
        "official_dsh_score": False,
        "runner_version": RUNNER_VERSION,
    }
    coverage = apply_contract(result, case, artifacts)
    uncovered = [row["field"] for row in coverage if not row["present"]]
    if uncovered:
        result["uncertainties"].append({
            "item": "output_contract",
            "reason": "fields not covered: %s" % ", ".join(uncovered),
            "impact": "the original output contract is incomplete",
            "action": "write same-named artifacts or map the keys"})
        result["uncertainty"] = result["uncertainties"]
        if result["status"] == "answered":
            result["status"] = "partial"
            result["task_status"] = "partial"
    write_json(output / "result.json", result)
    write_json(output / "metrics.json", report)
    write_json(output / "per-item-scores.json", per_item)
    write_json(output / "failure-analysis.json", failure)
    print(json.dumps({"command": "finalize", "case_id": case_id,
                      "status": status, "items": len(items),
                      "predictions": len(predictions),
                      "coverage": (by_name.get("coverage") or {}).get("value"),
                      "answer_accuracy": (by_name.get("answer_accuracy") or {}).get("value"),
                      "rubric_mean": failure["partial_credit"]["mean_score_0_2"],
                      "deterministic": deterministic,
                      "validation_ok": validation.get("ok"),
                      "uncovered_contract": uncovered,
                      "result": str(output / "result.json")},
                     ensure_ascii=False, indent=2))
    return 0 if status == "answered" else 1


def cmd_run(args):
    code = cmd_prepare(args)
    if code != 0:
        return code
    return cmd_finalize(args)


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

SELF_TEST_MATERIAL = """# Synthetic catalyst paper

At 350 °C the methanol selectivity reached 91% over the Cu/ZnO catalyst.
The same catalyst converted 42% of CO2 at 350 °C.
The Cu/ZnO catalyst showed a higher methanol selectivity than the Pd/SiO2 catalyst.
The Pd/SiO2 catalyst was tested at 300 °C and reached 55% selectivity.
No error bars are reported for the stability test; significance cannot be claimed.
"""

SELF_TEST_WORKORDER = {
    "case_id": "SYN-BENCH",
    "items": [
        {"item_id": "i01", "question": "What is the methanol selectivity at 350 °C?",
         "gold_points": ["91%"],
         "gold_evidence": [{"paragraph_id": "p1",
                            "quote": "At 350 °C the methanol selectivity reached 91%"}],
         "type": "fact", "difficulty": "easy", "capability": "extract",
         "source": "materials/paper.md"},
        {"item_id": "i02", "question": "What CO2 conversion is reported at 350 °C?",
         "gold_points": ["42%"],
         "gold_evidence": [{"paragraph_id": "p2",
                            "quote": "converted 42% of CO2 at 350 °C"}],
         "type": "fact", "difficulty": "medium", "capability": "locate",
         "source": "materials/paper.md"},
        {"item_id": "i03", "question": "Which catalyst has the higher methanol selectivity?",
         "gold_points": ["Cu/ZnO"],
         "gold_evidence": [{"paragraph_id": "p3",
                            "quote": "Cu/ZnO catalyst showed a higher methanol selectivity than the Pd/SiO2 catalyst"}],
         "type": "compare", "difficulty": "hard", "capability": "compare",
         "source": "materials/paper.md"},
        {"item_id": "i04", "question": "Can the stability improvement be called significant?",
         "gold_points": [],
         "gold_evidence": [{"paragraph_id": "p5",
                            "quote": "No error bars are reported for the stability test"}],
         "type": "abstain", "difficulty": "medium", "capability": "abstain",
         "source": "materials/paper.md"},
    ],
}

SELF_TEST_PREDICTIONS = [
    {"item_id": "i01", "answer_points": ["91%"],
     "evidence": [{"paragraph_id": "p1",
                   "quote": "At 350 °C the methanol selectivity reached 91%"}]},
    {"item_id": "i02", "answer_points": ["42%"],
     "evidence": [{"paragraph_id": "p2", "quote": "converted 42% of CO2 at 350 °C"}]},
    {"item_id": "i03", "answer_points": ["Cu/ZnO"],
     "evidence": [{"paragraph_id": "p3",
                   "quote": "Cu/ZnO catalyst showed a higher methanol selectivity than the Pd/SiO2 catalyst"}]},
    {"item_id": "i04", "abstain": True, "answer_points": [],
     "evidence": [{"paragraph_id": "p5",
                   "quote": "No error bars are reported for the stability test"}],
     "reason": "材料没有报告误差信息，不能判断显著性。"},
]


def run_fixture(root, predictions):
    input_dir = root / "input"
    work = root / "work"
    output = root / "output"
    (input_dir / "materials").mkdir(parents=True)
    write_text(input_dir / "case.json", json.dumps({
        "case_id": "SYN-BENCH",
        "output_contract": {
            "benchmark_report": "summary",
            "protocol": "items and metrics",
            "metrics": "metric values",
            "per_item_scores": "0/1/2 scores",
            "failure_analysis": "failure types",
            "version": "snapshot",
        },
    }, ensure_ascii=False))
    write_text(input_dir / "task.md", "# Synthetic benchmark case\n")
    write_text(input_dir / "benchmark-workorder.json",
               json.dumps(SELF_TEST_WORKORDER, ensure_ascii=False))
    write_text(input_dir / "materials" / "paper.md", SELF_TEST_MATERIAL)
    args = argparse.Namespace(input=str(input_dir), work=str(work), output=str(output))
    code_prepare = cmd_prepare(args)
    if predictions is not None:
        write_text(work / "predictions.jsonl",
                   "\n".join(json.dumps(row, ensure_ascii=False)
                             for row in predictions) + "\n")
    code_finalize = cmd_finalize(args)
    return code_prepare, code_finalize, output, work


def self_test():
    checks = []
    with tempfile.TemporaryDirectory(prefix="benchmark-selftest-") as td:
        root = Path(td) / "positive"
        code_prepare, code_finalize, output, work = run_fixture(
            root, SELF_TEST_PREDICTIONS)
        result = load_json(output / "result.json", {})
        checks.append({"name": "prepare_exit", "ok": code_prepare == 0})
        checks.append({"name": "finalize_exit", "ok": code_finalize == 0})
        checks.append({"name": "status_answered",
                       "ok": result.get("status") == "answered"})
        checks.append({"name": "four_per_item_scores",
                       "ok": len(result.get("per_item_scores") or []) == 4})
        checks.append({"name": "four_gold_rows",
                       "ok": len((load_json(work / "gold.jsonl.manifest", {})
                                  or {}).get("items") or []) == 4})
        checks.append({"name": "metrics_are_real",
                       "ok": all((row.get("value") is not None or
                                  row.get("name") == "hallucination_rate")
                                 for row in result.get("metrics") or [])})
        checks.append({"name": "deterministic",
                       "ok": bool((result.get("determinism") or {}).get(
                           "identical_output"))})
        checks.append({"name": "contract_covered",
                       "ok": all(row.get("present") for row in
                                 result.get("contract_coverage") or [])})
        checks.append({"name": "artifacts_exist",
                       "ok": all((output / "work" / name).is_file() for name in (
                           "metrics.json", "per-item-scores.json",
                           "failure-analysis.json", "benchmark-protocol.json"))})
        checks.append({"name": "self_test_perfect_rubric",
                       "ok": (result.get("rubric") or {}).get("mean") == 2.0})

        boundary = Path(td) / "boundary"
        code_prepare_b, code_finalize_b, output_b, _ = run_fixture(
            boundary, SELF_TEST_PREDICTIONS[:3])
        result_b = load_json(output_b / "result.json", {})
        checks.append({"name": "boundary_prepare_ok", "ok": code_prepare_b == 0})
        checks.append({"name": "boundary_partial",
                       "ok": result_b.get("status") == "partial"})
        checks.append({"name": "boundary_coverage_zeroish",
                       "ok": (result_b.get("result") or {}).get("coverage") == 0.75})
        checks.append({"name": "boundary_missing_prediction_flag",
                       "ok": any(row.get("failure_type") == "missing_prediction"
                                 for row in result_b.get("per_item_scores") or [])})

        no_workorder = Path(td) / "no-workorder"
        input_nw = no_workorder / "input"
        input_nw.mkdir(parents=True)
        write_text(input_nw / "case.json", json.dumps({"case_id": "NO-WO"}))
        write_text(input_nw / "task.md", "# no workorder\n")
        args_nw = argparse.Namespace(input=str(input_nw),
                                     work=str(no_workorder / "work"),
                                     output=str(no_workorder / "output"))
        code_nw = cmd_finalize(args_nw)
        result_nw = load_json(no_workorder / "output" / "result.json", {})
        checks.append({"name": "missing_workorder_input_error",
                       "ok": code_nw == 1 and result_nw.get("status") == "input_error"})
        checks.append({"name": "missing_workorder_no_fabricated_items",
                       "ok": not (result_nw.get("workorder") or {}).get("items")})
    ok = all(row["ok"] for row in checks)
    print(json.dumps({"ok": ok, "self_test": True, "runner_version": RUNNER_VERSION,
                      "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Literature benchmark runner")
    parser.add_argument("command", nargs="?",
                        choices=("prepare", "finalize", "run", "self-test"),
                        default="run")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "self-test":
        return self_test()
    if args.command == "prepare":
        return cmd_prepare(args)
    if args.command == "finalize":
        return cmd_finalize(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
