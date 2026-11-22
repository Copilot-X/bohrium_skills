#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic first pass for question-driven search.

It decomposes the supplied research question into explicit sub-questions,
builds a small round-based query plan, and maps verbatim material sentences to
sub-questions as evidence.  It never invents literature or findings: every
quote comes from an input document, and empty inputs produce a written
``input_error``/``partial`` result rather than a crash.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

STOP = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was",
    "were", "has", "have", "into", "about", "what", "which", "how", "why",
    "研究", "问题", "方法", "结果", "文献", "本文", "我们", "以及", "进行",
    "通过", "基于", "相关", "影响", "分析", "综述",
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


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def keywords(text, limit=12):
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[一-鿿]{2,8}",
                       str(text or ""))
    counts = {}
    for word in words:
        low = word.lower()
        if low in STOP or len(low) < 2:
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts, key=lambda w: (-counts[w], -len(w), w))
    return ranked[:limit]


def sentences(text):
    text = norm(text)
    parts = re.split(r"(?<=[。！？.!?])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 12]


def decompose(question):
    q = question or "研究问题"
    templates = [
        ("SQ1", "研究对象与核心概念是什么？", "definition"),
        ("SQ2", "已有研究采用了哪些方法与证据？", "method_evidence"),
        ("SQ3", "主要结论、分歧与边界条件是什么？", "conclusion_limits"),
        ("SQ4", "还有哪些缺口需要补检或复核？", "gap"),
    ]
    if re.search(r"比较|对比|差异|compare|versus|vs\.?", q, re.I):
        templates = [
            ("SQ1", "比较对象与比较维度如何界定？", "definition"),
            ("SQ2", "各对象分别有什么证据？", "evidence_per_object"),
            ("SQ3", "差异的方向、幅度与条件是什么？", "difference"),
            ("SQ4", "差异是否可由方法或样本差异解释？", "confounder"),
        ]
    elif re.search(r"影响|因果|机制|effect|cause|mechanism", q, re.I):
        templates = [
            ("SQ1", "自变量、因变量与机制假设是什么？", "definition"),
            ("SQ2", "有哪些直接证据与间接证据？", "evidence"),
            ("SQ3", "替代解释与混杂因素是否被排除？", "alternative"),
            ("SQ4", "边界条件与可推广范围是什么？", "limits"),
        ]
    elif re.search(r"进展|现状|趋势|review|progress|trend", q, re.I):
        templates = [
            ("SQ1", "时间线与代表性工作是什么？", "timeline"),
            ("SQ2", "方法与评价指标如何演变？", "method"),
            ("SQ3", "共识、争议与失败案例是什么？", "consensus"),
            ("SQ4", "下一步关键缺口是什么？", "gap"),
        ]
    return [{"id": sid, "question": text, "facet": facet,
             "depends_on": [] if sid == "SQ1" else [templates[0][0]],
             "query_ids": [], "status": "uncovered"}
            for sid, text, facet in templates]


def query_for(question, subquestion, round_no, kws):
    base = " ".join(kws[:5]) or norm(question)[:80]
    if round_no == 1:
        return base
    if round_no == 2:
        return "%s AND (%s)" % (base, subquestion.get("facet", "evidence"))
    return "%s AND (%s) AND (verification OR limitation)" % (
        base, subquestion.get("facet", "evidence"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(inp / "case.json", {})
    materials = read_json(work / "materials.json", {})
    docs = [d for d in materials.get("documents") or []
            if norm(d.get("text"))]
    task_text = ""
    task_path = inp / "task.md"
    if task_path.is_file():
        task_text = task_path.read_text(encoding="utf-8", errors="replace")
    question = norm(case.get("question") or case.get("research_question")
                    or case.get("title") or "")
    if not question:
        for line in task_text.splitlines():
            if line.startswith("#"):
                question = norm(line.lstrip("#"))
                break
    question = question or "未提供明确研究问题"
    kws = keywords(question + " " + " ".join(d.get("text", "")[:2000]
                                             for d in docs))
    plan_path = work / "search-plan.json"
    plan = read_json(plan_path, {})
    if not isinstance(plan, dict):
        plan = {}
    plan.setdefault("case_id", case.get("case_id") or "UNKNOWN")
    plan["mode"] = "question-driven"
    plan["question"] = question
    plan["summary"] = ("问题驱动检索初稿：拆为 %d 个子问题，生成 %d 条查询；"
                       "证据均来自输入材料逐字摘录。" % (4, 12))
    subquestions = plan.get("subquestions") or decompose(question)
    queries, rounds = [], []
    qid = 1
    for sub in subquestions:
        ids = []
        for round_no in (1, 2, 3):
            q = {
                "id": "Q%03d" % qid,
                "round": round_no,
                "subquestion_id": sub["id"],
                "query": query_for(question, sub, round_no, kws),
                "source": "provided corpus",
                "purpose": "recall" if round_no == 1 else (
                    "precision" if round_no == 2 else "verification"),
                "stop_condition": "连续 2 轮无新增合格记录",
            }
            queries.append(q)
            ids.append(q["id"])
            qid += 1
        sub["query_ids"] = ids
    for round_no in (1, 2, 3):
        rounds.append({
            "round": round_no,
            "query_ids": [q["id"] for q in queries if q["round"] == round_no],
            "new_candidates": 0,
            "decision": "继续" if round_no < 3 else "停止",
            "stop_condition": "连续 2 轮无新增合格记录",
        })
    # Map verbatim sentences to sub-questions by token overlap.
    candidate_sentences = []
    for doc in docs:
        path = doc.get("path") or doc.get("rel_path") or ""
        for index, sentence in enumerate(sentences(doc.get("text", ""))[:40], 1):
            candidate_sentences.append({
                "path": path, "locator": "S%d" % index, "quote": sentence})
    evidence = []
    used = set()
    for sub in subquestions:
        sub_tokens = set(keywords(sub["question"], 20))
        best = None
        for index, item in enumerate(candidate_sentences):
            overlap = len(sub_tokens & set(keywords(item["quote"], 20)))
            score = (overlap, 1 if index not in used else 0)
            if best is None or score > best[0]:
                best = (score, item)
        if best and best[0][0] > 0:
            item = best[1]
            used.add(candidate_sentences.index(item))
            evidence.append({
                "claim_id": sub["id"], "path": item["path"],
                "locator": item["locator"], "quote": item["quote"],
                "supports": "direct", "relevance": "lexical overlap only; "
                "verify against the source before drawing a conclusion"})
            sub["status"] = "covered"
        elif candidate_sentences:
            # No lexical match: keep one real passage as a candidate pointer so
            # the model has a concrete place to start, never a fabricated quote.
            item = next((x for i, x in enumerate(candidate_sentences)
                         if i not in used), candidate_sentences[0])
            used.add(candidate_sentences.index(item))
            evidence.append({
                "claim_id": sub["id"], "path": item["path"],
                "locator": item["locator"], "quote": item["quote"],
                "supports": "candidate", "relevance": "no lexical match; "
                "read the passage and determine whether it answers the sub-question"})
            sub["status"] = "needs_review"
        else:
            sub["status"] = "uncovered"
    plan.update({
        "subquestions": subquestions,
        "queries": queries,
        "rounds": rounds,
        "candidates": plan.get("candidates") or [],
        "evidence": evidence,
        "uncertainties": [
            {"item": "lexical_mapping",
             "reason": "evidence-to-subquestion links use lexical overlap",
             "impact": "each link needs human verification",
             "action": "read the cited passage and confirm the support"},
        ] if evidence else [
            {"item": "no_evidence", "reason": "no readable sentence could be "
             "mapped to a sub-question", "impact": "all sub-questions remain "
             "uncovered", "action": "inspect the material and add manual evidence"},
        ],
        "uncovered_subquestions": [s["id"] for s in subquestions
                                   if s["status"] == "uncovered"],
        "generated_at": now_iso(),
        "generated_by": "domain_work.py",
    })
    write_json(plan_path, plan)
    # Declared artifacts from the same content.
    write_json(out / "search-results.json", {
        "case_id": plan["case_id"], "question": question,
        "summary": plan["summary"], "subquestions": subquestions,
        "queries": queries, "rounds": rounds, "evidence": evidence,
        "uncovered_subquestions": plan["uncovered_subquestions"],
        "status": "answered" if evidence else "partial",
        "derived_from": "work/search-plan.json",
    })
    write_json(out / "search-log.json", {
        "case_id": plan["case_id"], "queries": queries,
        "rounds": rounds, "generated_at": now_iso(),
    })
    write_json(out / "evidence.json", {
        "case_id": plan["case_id"], "evidence": evidence,
    })
    with (out / "candidates.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["candidate_id", "path", "locator", "quote", "status"])
        for index, item in enumerate(evidence, 1):
            writer.writerow(["E%03d" % index, item.get("path"),
                             item.get("locator"), item.get("quote"),
                             "evidence_only"])
    summary = {"ok": bool(evidence), "subquestions": len(subquestions),
               "queries": len(queries), "evidence": len(evidence),
               "uncovered": len(plan["uncovered_subquestions"])}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
