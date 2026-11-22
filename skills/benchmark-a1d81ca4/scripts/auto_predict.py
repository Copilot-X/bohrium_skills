#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Material-only baseline predictions for the benchmark workorder.

Reads work/workorder.json and work/materials.json (written by prepare), ignores
gold_points/gold_evidence, and writes work/predictions.jsonl.  The model should
review and improve these predictions; this script only prevents an empty run.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?\s*(?:%|°C|C|K|h|min|s|mg|g|mL|L|M|mM|nM|ppm|eV|nm|µm|um|Hz|kHz|MPa|GPa)?")
STOP = {"what", "which", "is", "the", "of", "at", "in", "a", "an", "and",
        "does", "do", "how", "why", "reported", "show", "shows", "for",
        "with", "to", "from", "by", "on", "that", "this"}


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {} if default is None else default


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def tokens(text):
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9/+-]{2,}|[一-鿿]{2,8}",
                                           str(text or ""))
            if w.lower() not in STOP}


def sentences(text):
    return [part.strip() for part in re.split(r"(?<=[。！？.!?])\s+|\n+", norm(text))
            if len(part.strip()) >= 10]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    work, out = Path(args.work), Path(args.output)
    workorder = read_json(work / "workorder.json", {})
    materials = read_json(work / "materials.json", {})
    items = (workorder.get("normalized_items")
             or (workorder.get("raw") or {}).get("items")
             or workorder.get("items") or workorder.get("workorder") or [])
    if not items:
        print(json.dumps({"ok": False, "reason": "no workorder items"}))
        return 0
    corpus = []
    for doc in (materials.get("documents") or materials.get("materials") or []):
        path = doc.get("path") or doc.get("rel_path") or ""
        text = doc.get("text") or doc.get("excerpt") or ""
        for index, sentence in enumerate(sentences(text)[:200], 1):
            corpus.append({"path": path, "locator": "S%d" % index,
                           "quote": sentence, "tokens": tokens(sentence)})
    predictions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("item_id") or item.get("id") or "")
        question = norm(item.get("question") or "")
        q_tokens = tokens(question)
        scored = []
        for candidate in corpus:
            overlap = len(q_tokens & candidate["tokens"])
            # Prefer sentences that contain a number for numeric questions.
            numeric_bonus = 1 if re.search(r"\d", candidate["quote"]) else 0
            scored.append((overlap, numeric_bonus, candidate))
        scored.sort(reverse=True, key=lambda x: (x[0], x[1]))
        best = scored[0][2] if scored else None
        answer_points, evidence, abstain, reason = [], [], False, ""
        if best and scored[0][0] >= max(1, len(q_tokens) // 3):
            numbers = [m.group(0).strip() for m in NUM_RE.finditer(best["quote"])]
            if numbers:
                answer_points = list(dict.fromkeys(numbers))[:4]
            else:
                answer_points = [best["quote"]]
            evidence = [{"path": best["path"],
                         "paragraph_id": best["locator"].lower().replace("s", "p"),
                         "locator": best["locator"], "quote": best["quote"]}]
        else:
            abstain = True
            reason = "no material sentence has enough lexical overlap with the question"
        predictions.append({
            "item_id": qid, "answer_points": answer_points,
            "evidence": evidence, "abstain": abstain,
            "reason": reason or "material-only baseline; review before scoring",
        })
    target = work / "predictions.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        for row in predictions:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {"ok": True, "items": len(predictions),
               "answered": sum(1 for row in predictions if not row["abstain"]),
               "abstained": sum(1 for row in predictions if row["abstain"]),
               "path": str(target)}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
