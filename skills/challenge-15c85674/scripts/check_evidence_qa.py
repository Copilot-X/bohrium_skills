#!/usr/bin/env python3
"""Validate literature evidence-QA output against the input bundle.

Usage:
    python3 check_evidence_qa.py --result /app/output/result.json \
        --input /input --json
    python3 check_evidence_qa.py --self-test --json

Checks
  * every input question_id has an answer; duplicates are rejected
  * answered claims have paragraph_id + verbatim quote from the input
  * inferred claims have rationale; derived claims have formula
  * numbers in the answer appear in that answer's claims/evidence
  * partial/insufficient answers list unanswered parts
  * coverage / uncertainty / change_log are present
  * quotes are not translated or paraphrased (normalized verbatim check)

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable input.
"""
import argparse
import json
import re
import sys
from pathlib import Path

STATUSES = {"answered", "partial", "insufficient_evidence", "input_error"}
KINDS = {"reported", "derived", "inferred"}
NUMBER = re.compile(r"\d+(?:[.,]\d+)?\s*%?")
WS = re.compile(r"\s+")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def normalize(text):
    return WS.sub(" ", str(text or "")).strip().lower()


def collect_input(input_path):
    path = Path(input_path)
    data, texts, paragraph_ids = None, [], set()
    if not path.exists():
        return data, texts, paragraph_ids
    files = [path] if path.is_file() else sorted(path.rglob("*"))
    for file in files:
        if not file.is_file() or file.stat().st_size > 5_000_000:
            continue
        try:
            raw = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        texts.append(raw)
        if file.suffix.lower() == ".json":
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and data is None:
                data = parsed
            for item in _walk_dicts(parsed):
                for key in ("paragraph_id", "id", "pid"):
                    if item.get(key) not in (None, ""):
                        paragraph_ids.add(str(item[key]))
    return data, texts, paragraph_ids


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item)


def numbers_in(text):
    return {normalize(match) for match in NUMBER.findall(str(text or ""))}


def check_answers(result, input_data, corpus, paragraph_ids, problems, warnings, checks):
    answers = result.get("answers") or []
    if not isinstance(answers, list) or not answers:
        problems.append("result has no answers[]")
        return
    seen = set()
    question_ids = set()
    if input_data:
        for q in input_data.get("questions") or []:
            if isinstance(q, dict):
                qid = q.get("question_id") or q.get("id")
                if qid:
                    question_ids.add(str(qid))
    for i, answer in enumerate(answers):
        if not isinstance(answer, dict):
            problems.append("answer[%d] is not an object" % i)
            continue
        qid = answer.get("question_id")
        if not qid:
            problems.append("answer[%d] missing question_id" % i)
            continue
        if qid in seen:
            problems.append("duplicate question_id: %s" % qid)
        seen.add(str(qid))
        status = answer.get("status")
        if status not in STATUSES:
            problems.append("answer %s invalid status: %s" % (qid, status))
        claims = answer.get("claims") or []
        if status in ("answered", "partial") and not claims:
            problems.append("answer %s has status=%s but no claims" % (qid, status))
        if status in ("partial", "insufficient_evidence") and not answer.get("unanswered_parts"):
            problems.append("answer %s status=%s but no unanswered_parts" % (qid, status))
        answer_numbers = numbers_in(answer.get("answer"))
        claim_text = " ".join(str(c.get("text") or "") for c in claims if isinstance(c, dict))
        quotes = []
        for j, claim in enumerate(claims):
            if not isinstance(claim, dict):
                problems.append("answer %s claim[%d] is not an object" % (qid, j))
                continue
            if not claim.get("text"):
                problems.append("answer %s claim[%d] missing text" % (qid, j))
            kind = claim.get("kind")
            if kind not in KINDS:
                problems.append("answer %s claim[%d] invalid kind: %s" % (qid, j, kind))
            if kind == "inferred" and not claim.get("rationale"):
                problems.append("answer %s claim[%d] inferred without rationale" % (qid, j))
            if kind == "derived" and not claim.get("formula"):
                problems.append("answer %s claim[%d] derived without formula" % (qid, j))
            confidence = claim.get("confidence")
            if confidence is not None and (not isinstance(confidence, (int, float))
                                           or not 0 <= confidence <= 1):
                problems.append("answer %s claim[%d] confidence out of [0,1]" % (qid, j))
            evidence = claim.get("evidence") or []
            if not evidence:
                problems.append("answer %s claim[%d] has no evidence" % (qid, j))
            for k, ev in enumerate(evidence):
                if not isinstance(ev, dict):
                    problems.append("answer %s claim[%d] evidence[%d] is not an object" %
                                    (qid, j, k))
                    continue
                pid = ev.get("paragraph_id")
                quote = ev.get("quote")
                if not pid:
                    problems.append("answer %s claim[%d] evidence[%d] missing paragraph_id" %
                                    (qid, j, k))
                elif paragraph_ids and str(pid) not in paragraph_ids:
                    problems.append("answer %s claim[%d] evidence[%d] unknown paragraph %s" %
                                    (qid, j, k, pid))
                if not quote:
                    problems.append("answer %s claim[%d] evidence[%d] missing quote" %
                                    (qid, j, k))
                else:
                    quotes.append(str(quote))
                    if corpus and normalize(quote) not in corpus:
                        problems.append("answer %s claim[%d] quote not verbatim: %r" %
                                        (qid, j, quote[:60]))
        support = normalize(claim_text + " " + " ".join(quotes))
        for number in sorted(answer_numbers):
            if number not in support:
                warnings.append("answer %s number %s not found in claims/quotes" % (qid, number))
        if answer.get("conflict"):
            conflict = answer["conflict"]
            if not isinstance(conflict, dict):
                problems.append("answer %s conflict is not an object" % qid)
            elif conflict.get("detected") and not conflict.get("resolution"):
                warnings.append("answer %s conflict detected without resolution" % qid)
    missing = sorted(question_ids - seen)
    if missing:
        problems.append("questions not answered: %s" % ", ".join(missing))
    checks["answers"] = len(answers)
    checks["input_questions"] = len(question_ids)


def check_result(result, input_data, input_path, problems, warnings, checks):
    if not isinstance(result, dict):
        return
    for key in ("case_id", "answers", "coverage", "uncertainty", "change_log"):
        if key not in result:
            problems.append("result.json missing %s" % key)
    if input_data:
        for key in ("case_id", "paper_id", "material_version"):
            if key in input_data and result.get(key) != input_data.get(key):
                problems.append("result.%s does not match input" % key)
    coverage = result.get("coverage")
    if not isinstance(coverage, dict):
        problems.append("coverage is not an object")
    else:
        for key in ("documents_read", "sections_read", "sections_skipped", "notes"):
            if key not in coverage:
                warnings.append("coverage missing %s" % key)
    _, texts, paragraph_ids = collect_input(input_path) if input_path else (None, [], set())
    corpus = normalize("\n".join(texts))
    check_answers(result, input_data, corpus, paragraph_ids, problems, warnings, checks)


def self_test():
    result = {
        "case_id": "qa-self-test", "paper_id": "p1", "material_version": "v1",
        "answers": [{
            "question_id": "q1", "answer": "92% at 350 C", "conditions": ["350 C"],
            "status": "answered",
            "claims": [{"claim_id": "q1-c1", "text": "92% at 350 C", "kind": "reported",
                        "confidence": 0.9,
                        "evidence": [{"paragraph_id": "p1",
                                      "quote": "conversion reached 92% at 350 C"}]}],
            "unanswered_parts": [],
        }],
        "coverage": {"documents_read": ["p"], "sections_read": ["R"],
                     "sections_skipped": [], "notes": "ok"},
        "uncertainty": [], "change_log": [],
    }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None)
    ap.add_argument("--input", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "case.json").write_text(json.dumps({
                "case_id": "qa-self-test", "paper_id": "p1", "material_version": "v1",
                "paragraphs": [{"paragraph_id": "p1",
                                "text": "conversion reached 92% at 350 C"}],
                "questions": [{"question_id": "q1"}],
            }), encoding="utf-8")
            input_data, texts, pids = collect_input(root)
            problems, warnings, checks = [], [], {}
            check_result(self_test(), input_data, root, problems, warnings, checks)
        print(json.dumps({"ok": not problems, "self_test": True,
                          "problems": problems, "warnings": warnings},
                         ensure_ascii=False, indent=2))
        return 0 if not problems else 1
    if not args.result:
        ap.error("--result is required unless --self-test is used")
    try:
        result = load_json(args.result)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "problems": ["cannot read result: %s" % exc]},
                         ensure_ascii=False))
        return 2
    input_data = None
    if args.input:
        try:
            input_data, texts, pids = collect_input(args.input)
        except OSError:
            input_data = None
    else:
        texts, pids = [], set()
    problems, warnings, checks = [], [], {}
    check_result(result, input_data, args.input, problems, warnings, checks)
    report = {"ok": not problems, "problems": problems, "warnings": warnings,
              "checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
