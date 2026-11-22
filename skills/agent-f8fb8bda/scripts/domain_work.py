#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic deep-research first pass.

Builds sub-questions, a search plan and tool-call log, maps verbatim source
sentences to sub-questions as findings, extracts DOI/title references, and
writes the declared report/matrix/reference artifacts.  It labels lexical
matches as candidate evidence and leaves scientific judgement to the model.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")


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


def keywords(text, limit=16):
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[一-鿿]{2,8}",
                       str(text or ""))
    counts = {}
    for word in words:
        low = word.lower()
        if len(low) < 2:
            continue
        counts[word] = counts.get(word, 0) + 1
    return sorted(counts, key=lambda w: (-counts[w], -len(w), w))[:limit]


def sentences(text):
    parts = re.split(r"(?<=[。！？.!?])\s+|\n+", norm(text))
    return [p.strip() for p in parts if len(p.strip()) >= 20]


def decompose(question):
    base = [
        ("SQ1", "核心概念与研究边界是什么？"),
        ("SQ2", "现有证据与主要方法是什么？"),
        ("SQ3", "结论、冲突与限制条件是什么？"),
        ("SQ4", "关键证据缺口是什么？"),
    ]
    if re.search(r"比较|对比|差异|versus|compare", question, re.I):
        base = [
            ("SQ1", "比较对象与维度如何界定？"),
            ("SQ2", "各对象的证据分别是什么？"),
            ("SQ3", "差异的方向、幅度与条件是什么？"),
            ("SQ4", "哪些差异仍无法由现有证据解释？"),
        ]
    elif re.search(r"机制|因果|影响|mechanism|cause", question, re.I):
        base = [
            ("SQ1", "机制假设与变量是什么？"),
            ("SQ2", "直接与间接证据是什么？"),
            ("SQ3", "替代解释是否被排除？"),
            ("SQ4", "边界与可推广范围是什么？"),
        ]
    return [{"id": sid, "question": q, "depends_on": [] if sid == "SQ1" else ["SQ1"],
             "status": "uncovered", "finding_ids": []} for sid, q in base]


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
    docs = [d for d in materials.get("documents") or [] if norm(d.get("text"))]
    task = ""
    if (inp / "task.md").is_file():
        task = (inp / "task.md").read_text(encoding="utf-8", errors="replace")
    question = norm(case.get("question") or case.get("research_question") or "")
    if not question:
        for line in task.splitlines():
            if line.startswith("#"):
                question = norm(line.lstrip("#"))
                break
    question = question or "未提供明确研究问题"
    subquestions = decompose(question)
    all_sentences = []
    for doc in docs:
        path = doc.get("path") or doc.get("rel_path") or ""
        for index, sentence in enumerate(sentences(doc.get("text", ""))[:60], 1):
            all_sentences.append({"path": path, "locator": "S%d" % index,
                                  "quote": sentence})
    findings, used = [], set()
    for sub in subquestions:
        tokens = set(keywords(sub["question"], 20))
        scored = []
        for index, item in enumerate(all_sentences):
            if index in used:
                continue
            overlap = len(tokens & set(keywords(item["quote"], 20)))
            scored.append((overlap, index, item))
        scored.sort(reverse=True, key=lambda x: x[0])
        picks = [x for x in scored[:2]] if scored else []
        if not picks and all_sentences:
            picks = [(0, 0, all_sentences[0])]
        for overlap, index, item in picks:
            used.add(index)
            fid = "F%03d" % (len(findings) + 1)
            findings.append({
                "id": fid, "subquestion_id": sub["id"],
                "statement": item["quote"],
                "type": "extracted_quote" if overlap else "candidate_quote",
                "confidence": "lexical-match" if overlap else "unverified",
                "sources": [item["path"]],
                "limitations": [] if overlap else ["no lexical overlap; read the source before use"],
                "evidence": [item],
            })
            sub["finding_ids"].append(fid)
            sub["status"] = "covered" if overlap else "needs_review"
    references = []
    for doc in docs:
        path = doc.get("path") or doc.get("rel_path") or ""
        for doi in DOI_RE.findall(doc.get("text") or ""):
            doi = doi.lower()
            if any(r["doi"] == doi for r in references):
                continue
            references.append({"key": "ref%d" % (len(references) + 1),
                               "doi": doi, "source": path})
    search_plan = [
        {"id": "S1", "order": 1, "source": "provided corpus",
         "action": "inventory and keyword scan", "stop_condition": "all documents scanned"},
        {"id": "S2", "order": 2, "source": "provided corpus",
         "action": "extract passages for sub-questions", "stop_condition": "two passes without new evidence"},
        {"id": "S3", "order": 3, "source": "provided corpus",
         "action": "cross-check conflicts and gaps", "stop_condition": "each finding has a source or is marked uncertain"},
    ]
    tool_calls = [
        {"id": "T1", "tool": "inspect_input", "params": {"input": "/input"},
         "result": "%d readable documents" % len(docs), "status": "ok", "retry": 0},
        {"id": "T2", "tool": "extract_sentences",
         "params": {"max_per_doc": 60}, "result": "%d candidate passages" % len(all_sentences),
         "status": "ok", "retry": 0},
        {"id": "T3", "tool": "map_evidence",
         "params": {"subquestions": len(subquestions)},
         "result": "%d findings" % len(findings), "status": "ok", "retry": 0},
    ]
    gaps = [{"gap": "no verified evidence for %s" % s["id"], "basis": s["question"]}
            for s in subquestions if s["status"] == "uncovered"]
    evidence = [f["evidence"][0] for f in findings if f.get("evidence")]
    plan = {
        "case_id": case.get("case_id") or "UNKNOWN",
        "mode": "deep-research", "question": question,
        "summary": "深度研究初稿：%d 个子问题，%d 条逐字证据，%d 个缺口。" % (
            len(subquestions), len(evidence), len(gaps)),
        "subquestions": subquestions, "search_plan": search_plan,
        "tool_calls": tool_calls, "findings": findings,
        "references": references, "gap_analysis": gaps, "evidence": evidence,
        "uncertainties": ([{"item": "candidate_evidence",
                            "reason": "some passages have no lexical overlap with the sub-question",
                            "impact": "they are starting points, not conclusions",
                            "action": "read and confirm each passage"}]
                          if any(f["type"] == "candidate_quote" for f in findings) else []),
        "generated_at": now_iso(), "generated_by": "domain_work.py",
    }
    write_json(work / "research-plan.json", plan)
    write_json(out / "evidence-matrix.json", {
        "case_id": plan["case_id"],
        "rows": [{"subquestion_id": s["id"], "finding_ids": s["finding_ids"],
                  "status": s["status"]} for s in subquestions],
        "findings": findings,
    })
    write_json(out / "references.json", {
        "case_id": plan["case_id"], "references": references})
    lines = ["# 深度研究报告", "",
             "> 由 domain_work.py 生成逐字证据初稿；候选证据需人工复核。", "",
             "## 研究问题", "", question, "", "## 子问题与发现", ""]
    for sub in subquestions:
        lines += ["### %s %s" % (sub["id"], sub["question"]), ""]
        sub_findings = [f for f in findings if f["subquestion_id"] == sub["id"]]
        if not sub_findings:
            lines.append("- 未找到证据，列为缺口。")
        for finding in sub_findings:
            ev = (finding.get("evidence") or [{}])[0]
            lines.append("- [%s] %s（%s %s）" % (
                finding["id"], finding["statement"], ev.get("path"), ev.get("locator")))
        lines.append("")
    lines += ["## 证据缺口", ""]
    lines += ["- %s：%s" % (g["gap"], g["basis"]) for g in gaps] or ["- 无"]
    lines += ["", "## 引用", ""]
    lines += ["- %s（%s）" % (r["doi"], r.get("source", "")) for r in references] or ["- 未从材料中识别到 DOI"]
    write_text(out / "research-report.md", "\n".join(lines) + "\n")
    summary = {"ok": bool(findings), "subquestions": len(subquestions),
               "findings": len(findings), "references": len(references),
               "gaps": len(gaps)}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
