#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic retrieval-and-answer first pass for a finite knowledge base.

Uses the auditable block index from build_library_index.py.  Each answer
sentence carries at least one verbatim citation; no-overlap questions are
refused; two distinct sources with different numeric values are marked as a
conflict instead of silently choosing one.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

STOP = {"what", "which", "was", "were", "the", "of", "at", "in", "a", "an",
        "and", "is", "are", "for", "with", "to", "from", "by", "on", "that",
        "this", "used", "use"}


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


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def tokens(text):
    return {w.lower() for w in re.findall(
        r"[A-Za-z][A-Za-z0-9/+-]{2,}|[一-鿿]{2,8}", str(text or ""))
        if w.lower() not in STOP}


def numbers(text):
    return re.findall(r"[-+]?\d+(?:\.\d+)?\s*%?", str(text or ""))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(inp / "case.json", {})
    index = read_json(work / "library-index-build.json", {})
    if not index:
        print(json.dumps({"ok": False, "reason": "run build_library_index.py first"}))
        return 0
    questions = case.get("questions") or case.get("qa") or []
    if isinstance(questions, dict):
        questions = [{"id": k, "text": v} for k, v in questions.items()]
    cleaned = []
    for index_q, item in enumerate(questions, 1):
        if isinstance(item, str):
            cleaned.append({"id": "Q%d" % index_q, "text": item})
        elif isinstance(item, dict):
            cleaned.append({"id": str(item.get("id") or item.get("question_id")
                                      or "Q%d" % index_q),
                            "text": norm(item.get("text") or item.get("question")
                                         or "")})
    if not cleaned:
        task = inp / "task.md"
        text = task.read_text(encoding="utf-8", errors="replace") if task.is_file() else ""
        for index_q, line in enumerate(text.splitlines(), 1):
            line = norm(line)
            if line.endswith("?") and len(line) > 10:
                cleaned.append({"id": "Q%d" % len(cleaned) + 1, "text": line})
    blocks = []
    for file in index.get("files") or []:
        if not file.get("readable", True):
            continue
        for block in file.get("blocks") or []:
            blocks.append({
                "path": file.get("path"),
                "source_id": file.get("source_id"),
                "version": file.get("version_token") or "",
                "block_id": block.get("block_id"),
                "line": block.get("line"),
                "page": block.get("page"),
                "text": block.get("text") or "",
                "first_sentence": block.get("first_sentence") or block.get("text") or "",
                "tokens": tokens(block.get("first_sentence") or block.get("text") or ""),
            })
    answers, retrieval = [], []
    for question in cleaned:
        q_tokens = tokens(question["text"])
        scored = []
        for block in blocks:
            overlap = len(q_tokens & block["tokens"])
            if overlap:
                scored.append((overlap, block))
        scored.sort(reverse=True, key=lambda row: row[0])
        if not scored:
            answers.append({
                "id": question["id"], "question": question["text"],
                "status": "refused", "sentences": [],
                "missing_evidence": ["no supplied block has lexical overlap "
                                     "with the question"],
            })
            continue
        top = scored[0][0]
        chosen = [block for score, block in scored if score == top][:4]
        distinct_paths = {block["path"] for block in chosen}
        chosen_numbers = [tuple(numbers(block["first_sentence"])) for block in chosen]
        conflict = (len(distinct_paths) >= 2 and len(set(chosen_numbers)) >= 2)
        sentences = []
        citations = []
        for block in chosen[:2 if conflict else 1]:
            sentence = block["first_sentence"]
            citation = {"path": block["path"],
                        "locator": "%s L%s" % (block["block_id"], block["line"]),
                        "quote": sentence,
                        "source_id": block["source_id"],
                        "version": block["version"]}
            sentences.append({"text": sentence, "citations": [citation]})
            citations.append(citation)
            retrieval.append({"question_id": question["id"],
                              "path": block["path"],
                              "locator": citation["locator"],
                              "quote": sentence,
                              "reason": "highest lexical overlap with the question"})
        answer = {"id": question["id"], "question": question["text"],
                  "status": "conflict" if conflict else "answered",
                  "sentences": sentences}
        if conflict:
            answer["conflict"] = {
                "sources": sorted(distinct_paths),
                "note": "two supplied sources report different values/versions; "
                        "both are shown, not silently merged",
            }
        answers.append(answer)
    items = [{
        "id": file.get("source_id") or "I%03d" % (i + 1),
        "path": file.get("path"), "kind": file.get("kind"),
        "metadata": {"title": (file.get("sections") or [{}])[0].get("title", "")
                     if file.get("sections") else ""},
        "version": file.get("version_token") or "",
        "hash": "sha256:" + str(file.get("sha256") or ""),
        "status": "indexed" if file.get("readable", True) else "unreadable",
        "evidence": {"path": file.get("path"), "locator": "L1",
                     "quote": (file.get("blocks") or [{}])[0].get("first_sentence", "")
                     if file.get("blocks") else ""},
    } for i, file in enumerate(index.get("files") or [])]
    plan = read_json(work / "library-plan.json", {})
    if not isinstance(plan, dict):
        plan = {}
    plan.update({
        "case_id": case.get("case_id") or "UNKNOWN",
        "mode": "kb-qa",
        "summary": "回答 %d 个问题：%d answered / %d conflict / %d refused。" % (
            len(answers),
            sum(1 for a in answers if a["status"] == "answered"),
            sum(1 for a in answers if a["status"] == "conflict"),
            sum(1 for a in answers if a["status"] == "refused")),
        "questions": [{"id": q["id"], "text": q["text"]} for q in cleaned],
        "items": items, "answers": answers, "retrieval": retrieval,
        "evidence": [c for a in answers for s in a.get("sentences") or []
                     for c in s.get("citations") or []],
        "operations": [{"id": "O%03d" % (i + 1), "action": "retrieve",
                        "target": row["path"], "status": "ok"}
                       for i, row in enumerate(retrieval)],
        "operation_evidence": retrieval,
        "failure_recovery": {
            "refused": [a["id"] for a in answers if a["status"] == "refused"],
            "conflicts": [a["id"] for a in answers if a["status"] == "conflict"],
            "policy": "no-evidence questions are refused; conflicting sources are shown side by side",
        },
        "uncertainties": [{"item": "lexical_retrieval",
                           "reason": "answers use the highest-overlap block",
                           "impact": "confirm each sentence and version",
                           "action": "read the cited block and resolve conflicts"}],
        "generated_at": now_iso(), "generated_by": "answer_questions.py",
    })
    write_json(work / "library-plan.json", plan)
    write_json(out / "library-results.json", {
        "case_id": plan["case_id"], "summary": plan["summary"],
        "answers": answers, "items": items, "retrieval": retrieval,
        "evidence": plan["evidence"],
        "operation_evidence": retrieval,
        "failure_recovery": plan["failure_recovery"]})
    write_json(out / "library-index.json", {
        "case_id": plan["case_id"],
        "index": [{"id": item["id"], "path": item["path"],
                   "version": item["version"], "hash": item["hash"]}
                  for item in items]})
    write_json(out / "operations.log.json", {
        "case_id": plan["case_id"], "operations": plan["operations"]})
    summary = {"ok": True, "questions": len(answers),
               "answered": sum(1 for a in answers if a["status"] == "answered"),
               "conflict": sum(1 for a in answers if a["status"] == "conflict"),
               "refused": sum(1 for a in answers if a["status"] == "refused")}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
