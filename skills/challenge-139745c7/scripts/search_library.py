#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic file/full-text search over the auditable library index.

Scores index blocks against the supplied queries, applies explicit metadata
filters, returns ranked hits with path/version/locator/verbatim snippet, and
records a no-result explanation.  No semantic model is claimed.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

STOP = {"what", "which", "was", "were", "the", "of", "at", "in", "a", "an",
        "and", "is", "are", "for", "with", "to", "from", "by", "on", "that",
        "this", "find", "search"}


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


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--top-k", type=int, default=5)
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
    queries = case.get("queries") or case.get("questions") or []
    if isinstance(queries, dict):
        queries = [{"id": k, "text": v} for k, v in queries.items()]
    cleaned = []
    for i, item in enumerate(queries, 1):
        if isinstance(item, str):
            cleaned.append({"id": "Q%d" % i, "text": item})
        elif isinstance(item, dict):
            cleaned.append({"id": str(item.get("id") or "Q%d" % i),
                            "text": norm(item.get("text") or item.get("query") or "")})
    if not cleaned and case.get("question"):
        cleaned.append({"id": "Q1", "text": norm(case["question"])})
    if not cleaned:
        task = inp / "task.md"
        text = task.read_text(encoding="utf-8", errors="replace") if task.is_file() else ""
        for line in text.splitlines():
            if line.startswith("- "):
                cleaned.append({"id": "Q%d" % (len(cleaned) + 1),
                                "text": norm(line[2:])})
    filters = case.get("filters") or {}
    files = {file.get("path"): file for file in index.get("files") or []}
    hits, filter_execution = [], []
    for query in cleaned:
        q_tokens = tokens(query["text"])
        scored = []
        for path, file in files.items():
            if not file.get("readable", True):
                continue
            filter_ok = True
            haystack = " ".join([str(path), str(file.get("version_token") or ""),
                                 str(file.get("kind") or ""),
                                 " ".join(str(b.get("text") or "")
                                          for b in file.get("blocks") or [])
                                 ]).lower()
            for key, value in (filters or {}).items():
                if key in ("file_type", "type", "extensions"):
                    allowed = value if isinstance(value, list) else [value]
                    suffix = Path(str(path)).suffix.lower().lstrip(".")
                    if suffix not in [str(x).lower().lstrip(".") for x in allowed]:
                        filter_ok = False
                        break
                elif key in ("must", "must_not"):
                    continue
                elif str(value).lower() not in haystack:
                    filter_ok = False
                    break
            for term in (filters or {}).get("must") or []:
                if str(term).lower() not in haystack:
                    filter_ok = False
            for term in (filters or {}).get("must_not") or []:
                if str(term).lower() in haystack:
                    filter_ok = False
            if not filter_ok:
                continue
            for block in file.get("blocks") or []:
                text = norm(block.get("text") or "")
                overlap = len(q_tokens & tokens(text))
                if overlap:
                    scored.append((overlap, path, file, block, text))
        filter_execution.append({"query_id": query["id"], "filters": filters,
                                 "kept": len({row[1] for row in scored})})
        scored.sort(reverse=True, key=lambda row: (row[0], row[1]))
        seen = set()
        for rank, (score, path, file, block, text) in enumerate(scored, 1):
            key = (path, block.get("block_id"))
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > args.top_k:
                break
            snippet = block.get("first_sentence") or text
            hits.append({
                "id": "R%03d" % (len(hits) + 1),
                "rank": rank, "path": path,
                "version": file.get("version_token") or "",
                "locator": "%s L%s" % (block.get("block_id"), block.get("line")),
                "snippet": snippet, "score": score,
                "reason": "lexical overlap with query %s" % query["id"],
                "query_id": query["id"],
                "evidence": {"path": path,
                             "locator": "%s L%s" % (block.get("block_id"),
                                                    block.get("line")),
                             "quote": snippet},
            })
    items = [{
        "id": row["id"], "rank": row["rank"], "path": row["path"],
        "version": row["version"], "locator": row["locator"],
        "snippet": row["snippet"], "score": row["score"],
        "hash": "sha256:" + str((files.get(row["path"]) or {}).get("sha256") or ""),
        "evidence": row["evidence"],
    } for row in hits]
    plan = read_json(work / "library-plan.json", {})
    if not isinstance(plan, dict):
        plan = {}
    plan.update({
        "case_id": case.get("case_id") or "UNKNOWN",
        "mode": "file-search",
        "summary": "检索 %d 个查询，命中 %d 条；过滤器 %s。" % (
            len(cleaned), len(items), json.dumps(filters, ensure_ascii=False)),
        "queries": [{"id": q["id"], "query": q["text"]} for q in cleaned],
        "items": items,
        "filters": {**filters,
                    "must": (["project:%s" % filters["project"]]
                             if filters.get("project") else []),
                    "must_not": []},
        "filter_execution": filter_execution,
        "no_result": "" if items else "no block matched the queries under the filters",
        "unreadable": [file.get("path") for file in files.values()
                       if not file.get("readable", True)],
        "version_conflict": [],
        "evidence": [row["evidence"] for row in items],
        "operations": [{"id": "O%03d" % (i + 1), "action": "search",
                        "target": row["path"], "status": "ok"}
                       for i, row in enumerate(items)],
        "operation_evidence": {"queries": [q["text"] for q in cleaned],
                               "filters": filters,
                               "index": "work/library-index-build.json"},
        "failure_recovery": {"no_result": "" if items else "no block matched",
                             "unreadable": [file.get("path") for file in files.values()
                                            if not file.get("readable", True)],
                             "version_conflict": []},
        "uncertainties": [{"item": "lexical_search",
                           "reason": "hits use token overlap only",
                           "impact": "confirm the snippet and metadata filter",
                           "action": "read each hit and re-rank if needed"}],
        "generated_at": now_iso(), "generated_by": "search_library.py",
    })
    write_json(work / "library-plan.json", plan)
    write_json(out / "library-results.json", {
        "case_id": plan["case_id"], "summary": plan["summary"],
        "items": items,
        "filters": {**filters,
                    "must": (["project:%s" % filters["project"]]
                             if filters.get("project") else []),
                    "must_not": []},
        "filter_execution": filter_execution, "no_result": plan["no_result"],
        "evidence": plan["evidence"],
        "operation_evidence": plan["operation_evidence"],
        "failure_recovery": plan["failure_recovery"]})
    write_json(out / "library-index.json", {
        "case_id": plan["case_id"],
        "index": [{"path": file.get("path"),
                   "version": file.get("version_token") or "",
                   "sha256": file.get("sha256")}
                  for file in files.values()]})
    write_json(out / "operations.log.json", {
        "case_id": plan["case_id"], "operations": plan["operations"]})
    summary = {"ok": True, "queries": len(cleaned), "hits": len(items),
               "filters": filters}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
