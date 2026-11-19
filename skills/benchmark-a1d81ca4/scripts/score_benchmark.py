#!/usr/bin/env python3
"""Deterministic scorer for a literature-reading benchmark.

Usage:
    python3 score_benchmark.py --gold gold.jsonl --pred pred.jsonl \
        --out metrics.json --bootstrap 1000 --seed 42 --json
    python3 score_benchmark.py --self-test --json

Input: JSONL or a JSON array. Each gold item:
    {"item_id","gold_points":[...],"gold_evidence":[...],"type","difficulty","capability"}
Each prediction item:
    {"item_id","answer_points":[...],"evidence":[...],"abstain":false}

Output: metrics[] with formula/range/value/n/ci95, per-item scores, baselines,
errors for missing/unknown/duplicate ids. Same input + seed => same output.

Stdlib only. Exit 0 = scored, 1 = input errors, 2 = unreadable input.
"""
import argparse
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PUNCT = re.compile(r"[^\w㐀-鿿]+", re.UNICODE)


def normalize(value):
    return " ".join(PUNCT.sub(" ", str(value or "").lower()).split())


def tokens(value):
    return set(normalize(value).split())


def point_match(gold, pred):
    a, b = normalize(gold), normalize(pred)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 3 and (a in b or b in a):
        return True
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6


def evidence_keys(item):
    keys = set()
    if isinstance(item, str):
        if normalize(item):
            keys.add("text:" + normalize(item))
        return keys
    if not isinstance(item, dict):
        return keys
    for name in ("paragraph_id", "para_id", "pid", "id"):
        if item.get(name) not in (None, ""):
            keys.add("p:" + normalize(item[name]))
    for name in ("table_id", "figure_id", "table", "figure"):
        if item.get(name) not in (None, ""):
            keys.add("t:" + normalize(item[name]))
    for name in ("page", "page_number", "pageno"):
        if item.get(name) not in (None, ""):
            keys.add("page:" + normalize(item[name]))
    for name in ("locator", "section_id", "section"):
        if item.get(name) not in (None, ""):
            keys.add("loc:" + normalize(item[name]))
    if not keys and item.get("quote"):
        keys.add("quote:" + normalize(item["quote"])[:120])
    return keys


def as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def load_jsonl(path):
    p = Path(path)
    if not p.is_file():
        return [], ["missing input: %s" % p]
    text = p.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return [], ["empty input: %s" % p]
    errors = []
    rows = []
    if text.startswith("["):
        try:
            data = json.loads(text)
            return (data if isinstance(data, list) else []), errors
        except json.JSONDecodeError as exc:
            return [], ["cannot parse JSON array: %s" % exc]
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            errors.append("line %d: %s" % (number, exc))
    return rows, errors


def answer_scores(gold_points, pred_points):
    gold = [str(x) for x in as_list(gold_points) if str(x).strip()]
    pred = [str(x) for x in as_list(pred_points) if str(x).strip()]
    matched_gold = sum(1 for item in gold if any(point_match(item, p) for p in pred))
    matched_pred = sum(1 for item in pred if any(point_match(item, g) for g in gold))
    recall = matched_gold / len(gold) if gold else (1.0 if not pred else 0.0)
    precision = matched_pred / len(pred) if pred else (1.0 if not gold else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    exact = 1.0 if matched_gold == len(gold) and matched_pred == len(pred) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "exact": exact,
            "gold_n": len(gold), "pred_n": len(pred)}


def evidence_scores(gold_evidence, pred_evidence):
    gold = set()
    for item in as_list(gold_evidence):
        gold |= evidence_keys(item)
    pred = set()
    for item in as_list(pred_evidence):
        pred |= evidence_keys(item)
    precision = len(gold & pred) / len(pred) if pred else (1.0 if not gold else 0.0)
    recall = len(gold & pred) / len(gold) if gold else (1.0 if not pred else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1,
            "gold_n": len(gold), "pred_n": len(pred)}


def wilson(k, n, z=1.96):
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, center - half), 6), round(min(1.0, center + half), 6)]


def bootstrap_ci(values, reps, seed):
    values = [float(v) for v in values if v is not None]
    if len(values) < 2 or reps <= 0:
        return None
    rng = random.Random(seed)
    means = []
    n = len(values)
    for _ in range(reps):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[max(0, int(0.025 * len(means)) - 1)]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    return [round(lo, 6), round(hi, 6)]


def make_metric(name, formula, values, n, ci=None, status="ok"):
    clean = [v for v in values if v is not None]
    value = round(sum(clean) / len(clean), 6) if clean else None
    return {"name": name, "formula": formula, "range": [0, 1],
            "value": value, "n": n, "ci95": ci, "status": status}


def score(gold_rows, pred_rows, bootstrap_reps=1000, seed=42, run_id="run-1",
          synthetic=False):
    errors = []
    gold_by_id = {}
    for index, row in enumerate(gold_rows):
        if not isinstance(row, dict):
            errors.append("gold[%d] is not an object" % index)
            continue
        iid = str(row.get("item_id") or row.get("id") or "")
        if not iid:
            errors.append("gold[%d] missing item_id" % index)
            continue
        if iid in gold_by_id:
            errors.append("duplicate gold item_id: %s" % iid)
            continue
        gold_by_id[iid] = row
    pred_by_id = {}
    for index, row in enumerate(pred_rows):
        if not isinstance(row, dict):
            errors.append("pred[%d] is not an object" % index)
            continue
        iid = str(row.get("item_id") or row.get("id") or "")
        if not iid:
            errors.append("pred[%d] missing item_id" % index)
            continue
        if iid in pred_by_id:
            errors.append("duplicate pred item_id: %s" % iid)
            continue
        pred_by_id[iid] = row
    for iid in sorted(set(pred_by_id) - set(gold_by_id)):
        errors.append("prediction for unknown item_id: %s" % iid)

    items = []
    for iid, gold in gold_by_id.items():
        pred = pred_by_id.get(iid)
        gold_points = gold.get("gold_points") or gold.get("points") or []
        pred_points = (pred or {}).get("answer_points") or (pred or {}).get("points") or []
        ans = answer_scores(gold_points, pred_points)
        ev = evidence_scores(gold.get("gold_evidence") or gold.get("evidence") or [],
                             (pred or {}).get("evidence") or [])
        gold_abstain = bool(gold.get("abstain")) or str(gold.get("type", "")).lower() == "abstain"
        pred_abstain = bool((pred or {}).get("abstain"))
        if pred is not None and not pred_abstain and not pred_points and not (pred or {}).get("answer"):
            pred_abstain = True
        abstain_ok = 1.0 if gold_abstain == pred_abstain else 0.0
        if pred_abstain:
            grounded = 1.0
        elif pred_points:
            grounded = 1.0 if (pred or {}).get("evidence") else 0.0
        else:
            grounded = 0.0
        items.append({
            "item_id": iid,
            "type": gold.get("type") or "unknown",
            "difficulty": gold.get("difficulty") or "unknown",
            "capability": gold.get("capability") or "unknown",
            "answered": pred is not None,
            "gold_abstain": gold_abstain,
            "pred_abstain": pred_abstain,
            "answer_precision": ans["precision"],
            "answer_recall": ans["recall"],
            "answer_f1": ans["f1"],
            "answer_exact": ans["exact"],
            "evidence_precision": ev["precision"],
            "evidence_recall": ev["recall"],
            "evidence_f1": ev["f1"],
            "abstention_ok": abstain_ok,
            "citation_groundedness": grounded,
        })

    n = len(items)
    metrics = [
        make_metric("answer_accuracy", "exact_match / total",
                    [item["answer_exact"] for item in items], n,
                    wilson(sum(item["answer_exact"] for item in items), n)),
        make_metric("answer_f1", "macro mean of point F1",
                    [item["answer_f1"] for item in items], n,
                    bootstrap_ci([item["answer_f1"] for item in items], bootstrap_reps, seed)),
        make_metric("evidence_precision", "matched_pred_evidence / pred_evidence",
                    [item["evidence_precision"] for item in items], n,
                    bootstrap_ci([item["evidence_precision"] for item in items],
                                 bootstrap_reps, seed)),
        make_metric("evidence_recall", "matched_gold_evidence / gold_evidence",
                    [item["evidence_recall"] for item in items], n,
                    bootstrap_ci([item["evidence_recall"] for item in items], bootstrap_reps, seed)),
        make_metric("abstention_correctness", "correct_abstention / total",
                    [item["abstention_ok"] for item in items], n,
                    wilson(sum(item["abstention_ok"] for item in items), n)),
        make_metric("citation_groundedness", "grounded_answers / answered",
                    [item["citation_groundedness"] for item in items], n,
                    bootstrap_ci([item["citation_groundedness"] for item in items],
                                 bootstrap_reps, seed)),
        make_metric("coverage", "items_with_prediction / total",
                    [1.0 if item["answered"] else 0.0 for item in items], n,
                    wilson(sum(1 for item in items if item["answered"]), n)),
    ]
    answered = [item for item in items if item["answered"] and not item["pred_abstain"]]
    hallucination = ([1.0 - item["answer_precision"] for item in answered]
                     if answered else [])
    metrics.append(make_metric("hallucination_rate", "1 - mean(answer_precision)",
                               hallucination, len(answered),
                               bootstrap_ci(hallucination, bootstrap_reps, seed)))

    by_type = {}
    for item in items:
        key = item["type"] or "unknown"
        row = by_type.setdefault(key, {"n": 0, "answer_f1": 0.0,
                                       "evidence_recall": 0.0,
                                       "abstention_ok": 0.0})
        row["n"] += 1
        row["answer_f1"] += item["answer_f1"]
        row["evidence_recall"] += item["evidence_recall"]
        row["abstention_ok"] += item["abstention_ok"]
    for row in by_type.values():
        for key in ("answer_f1", "evidence_recall", "abstention_ok"):
            row[key] = round(row[key] / row["n"], 6) if row["n"] else None

    # Rule-based baselines: all abstain and all empty.
    def baseline(kind):
        rows = []
        for iid, gold in gold_by_id.items():
            gold_points = gold.get("gold_points") or []
            if kind == "abstain_all":
                pred = {"item_id": iid, "abstain": True, "answer_points": [],
                        "evidence": []}
            else:
                pred = {"item_id": iid, "abstain": False, "answer_points": [],
                        "evidence": []}
            ans = answer_scores(gold_points, pred["answer_points"])
            gold_abstain = bool(gold.get("abstain")) or str(gold.get("type", "")).lower() == "abstain"
            pred_abstain = bool(pred.get("abstain")) or not pred["answer_points"]
            rows.append({"answer_exact": ans["exact"], "answer_f1": ans["f1"],
                         "abstention_ok": 1.0 if gold_abstain == pred_abstain else 0.0})
        return {"n": len(rows),
                "answer_accuracy": round(sum(r["answer_exact"] for r in rows) / len(rows), 6) if rows else None,
                "answer_f1": round(sum(r["answer_f1"] for r in rows) / len(rows), 6) if rows else None,
                "abstention_correctness": round(sum(r["abstention_ok"] for r in rows) / len(rows), 6) if rows else None}

    return {
        "run_id": run_id,
        "synthetic": bool(synthetic),
        "metrics": metrics,
        "baselines": [
            {"name": "abstain_all", "description": "所有题都拒答", "metrics": baseline("abstain_all")},
            {"name": "empty_answer", "description": "所有题都返回空答案", "metrics": baseline("empty")},
        ],
        "items": items,
        "errors": errors,
        "counts": {"gold": len(gold_by_id), "pred": len(pred_by_id),
                   "scored": n, "missing_pred": len(set(gold_by_id) - set(pred_by_id))},
        "by_type": by_type,
        "config": {"bootstrap": bootstrap_reps, "seed": seed},
    }


def self_test():
    gold = [
        {"item_id": "i1", "gold_points": ["91%"], "gold_evidence": [{"paragraph_id": "p6"}],
         "type": "fact"},
        {"item_id": "i2", "gold_points": [], "gold_evidence": [{"paragraph_id": "p8"}],
         "type": "abstain"},
    ]
    pred = [
        {"item_id": "i1", "answer_points": ["91"], "evidence": [{"paragraph_id": "p6"}]},
        {"item_id": "i2", "abstain": True, "answer_points": [], "evidence": []},
    ]
    report = score(gold, pred, bootstrap_reps=50, seed=7, run_id="self-test")
    assert report["metrics"][0]["value"] == 1.0, report["metrics"][0]
    assert report["metrics"][4]["value"] == 1.0, report["metrics"][4]
    assert report["counts"]["missing_pred"] == 0
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=None)
    ap.add_argument("--pred", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--run-id", default="run-1")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        report = self_test()
        print(json.dumps({"ok": True, "self_test": True, "metrics": report["metrics"]},
                         ensure_ascii=False, indent=2))
        return 0
    if not args.gold or not args.pred:
        ap.error("--gold and --pred are required unless --self-test is used")
    gold, gold_errors = load_jsonl(args.gold)
    pred, pred_errors = load_jsonl(args.pred)
    if gold_errors or not gold:
        print(json.dumps({"ok": False, "errors": gold_errors + ["gold has no items"]},
                         ensure_ascii=False, indent=2))
        return 2
    report = score(gold, pred, bootstrap_reps=args.bootstrap, seed=args.seed,
                   run_id=args.run_id, synthetic=args.synthetic)
    report["errors"] = gold_errors + pred_errors + report["errors"]
    report["ok"] = not report["errors"]
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
