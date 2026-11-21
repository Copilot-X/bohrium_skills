#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-finalize quality gate for knowledge-base QA.

Usage:
    python3 check_kb_answers.py --input /input --work /app/work \
        --output /app/output --json
    python3 check_kb_answers.py --self-test --json

Checks beyond the generic finalize:
  * every requested question is answered exactly once;
  * every answer sentence has at least one citation, and the citation quote
    actually contains the sentence's numbers and key entities (a verbatim but
    unrelated quote is rejected);
  * refused / insufficient_evidence answers carry missing_evidence and no
    answer sentence;
  * conflict answers cite at least two distinct sources and do not silently
    pick one;
  * duplicate copies and superseded versions are not presented as independent
    evidence;
  * every quote is verbatim in the cited input file;
  * contract-specific artifacts exist in output/results or output root.

Exit 0 = pass, 1 = problems, 2 = unreadable work file.
Stdlib only; reuses skillcore from the same scripts directory.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import skillcore as S  # noqa: E402

WS = re.compile(r"\s+")
NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]{2,}")
STOP = {
    "the", "and", "was", "were", "for", "with", "that", "this", "from",
    "have", "has", "had", "are", "but", "not", "per", "using", "used",
    "same", "also", "into", "than", "then", "their", "there", "these",
    "those", "which", "when", "were", "will", "would", "could", "should",
    "according", "reported", "reports", "study", "studies", "protocol",
}


def norm(value):
    return WS.sub(" ", str(value or "").replace(" ", " ")).strip().lower()


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return default


def fact_tokens(text):
    numbers = set(NUMBER.findall(str(text or "")))
    words = {w.lower() for w in LATIN.findall(str(text or ""))}
    words = {w for w in words if w not in STOP and len(w) >= 3}
    return numbers, words


def citation_supports(sentence, citations, problems, label):
    """Reject a verbatim quote that does not contain the sentence's facts."""
    numbers, words = fact_tokens(sentence)
    combined = norm(" ".join(str(c.get("quote") or "") for c in citations))
    missing_numbers = sorted(n for n in numbers if n not in combined)
    if missing_numbers:
        problems.append("%s: citation quote lacks numbers from the answer "
                        "sentence: %s" % (label, ", ".join(missing_numbers)))
    if words:
        present = {w for w in words if w in combined}
        if not present:
            problems.append("%s: citation quote shares no key entity with the "
                            "answer sentence (unrelated quote)" % label)
        elif len(present) < max(1, (len(words) + 1) // 2):
            # The quote may paraphrase; require at least half the entities.
            problems.append("%s: citation quote only weakly matches the answer "
                            "sentence (%d/%d key terms)" % (label, len(present),
                                                           len(words)))
    if not str(combined).strip():
        problems.append("%s: citations are empty" % label)


def requested_questions(case, task_text, work):
    ids = []
    for source in (work.get("questions"), case.get("questions") if isinstance(case, dict) else None):
        for row in source or []:
            if isinstance(row, dict):
                ids.append(str(row.get("id") or row.get("question_id") or row.get("question") or ""))
            elif isinstance(row, str):
                ids.append(row)
    if not ids:
        for answer in work.get("answers") or []:
            if isinstance(answer, dict):
                ids.append(str(answer.get("id") or answer.get("question_id") or ""))
    return [x for x in ids if x]


def check_items(work, problems, warnings, index):
    items = [x for x in work.get("items") or [] if isinstance(x, dict)]
    if not items:
        warnings.append("items is empty; the file/metadata layer is not documented")
    hashes = {}
    for item in items:
        iid = item.get("id") or item.get("path")
        if not (item.get("path") or item.get("id")):
            problems.append("item lacks path/id")
        if not item.get("evidence"):
            warnings.append("item %s lacks evidence" % iid)
        digest = item.get("hash") or item.get("sha256")
        if digest:
            hashes.setdefault(str(digest), []).append(iid)
        if item.get("status") in ("duplicate", "superseded") and not item.get("duplicate_of"):
            warnings.append("item %s is %s but has no duplicate_of/superseded_by"
                            % (iid, item.get("status")))
    for digest, ids in hashes.items():
        if len(ids) > 1:
            warnings.append("identical content hash shared by %s; mark copies "
                            "as duplicate_of" % ", ".join(ids))
    if index:
        for row in index.get("unreadable") or []:
            paths = {str(x.get("path") or "") for x in items}
            if row.get("path") not in paths:
                problems.append("unreadable input not recorded as an item: %s"
                                % row.get("path"))


def check_answers(work, docs, problems, warnings, checks, case, task_text):
    by_path = {d.get("rel_path"): S.doc_text(d) for d in docs}
    answers = [x for x in work.get("answers") or [] if isinstance(x, dict)]
    if not answers:
        problems.append("answers is empty")
    seen = set()
    for answer in answers:
        aid = answer.get("id") or answer.get("question_id") or "?"
        if aid in seen:
            problems.append("duplicate answer id: %s" % aid)
        seen.add(aid)
        if not str(answer.get("question") or "").strip():
            problems.append("answer %s lacks question" % aid)
        status = str(answer.get("status") or "")
        sentences = answer.get("sentences") or []
        if status in ("refused", "insufficient_evidence", "no_evidence"):
            if sentences:
                problems.append("refused answer %s still has answer sentences" % aid)
            if not (answer.get("missing_evidence") or answer.get("missing")):
                problems.append("refused answer %s lacks missing_evidence" % aid)
            continue
        if status == "conflict":
            if len(sentences) < 2:
                problems.append("conflict answer %s needs at least two cited "
                                "positions" % aid)
            paths = set()
            for sentence in sentences:
                for citation in sentence.get("citations") or []:
                    paths.add(str(citation.get("path") or ""))
            if len(paths) < 2:
                problems.append("conflict answer %s cites fewer than two "
                                "distinct sources" % aid)
            conflict = answer.get("conflict")
            if not conflict:
                problems.append("conflict answer %s lacks conflict record" % aid)
            else:
                sources = conflict.get("sources") if isinstance(conflict, dict) else None
                if isinstance(sources, list) and len(sources) < 2:
                    problems.append("conflict answer %s conflict.sources has "
                                    "fewer than two entries" % aid)
                if isinstance(conflict, dict) and not str(
                        conflict.get("resolution") or conflict.get("note") or "").strip():
                    warnings.append("conflict answer %s does not state whether "
                                    "the conflict is resolved" % aid)
        elif status not in ("answered", "partial"):
            problems.append("answer %s has unknown status %r" % (aid, status))
        if status in ("answered", "partial") and not sentences:
            problems.append("answer %s has no sentences" % aid)
        for index, sentence in enumerate(sentences, 1):
            if not isinstance(sentence, dict):
                problems.append("answer %s sentence[%d] is not an object" % (aid, index))
                continue
            text = str(sentence.get("text") or "").strip()
            citations = [c for c in sentence.get("citations") or []
                         if isinstance(c, dict)]
            label = "%s sentence[%d]" % (aid, index)
            if not text:
                problems.append("%s has empty text" % label)
            if not citations:
                problems.append("%s has no citations" % label)
                continue
            for citation in citations:
                path = str(citation.get("path") or "")
                quote = str(citation.get("quote") or "").strip()
                if not quote:
                    problems.append("%s citation has empty quote" % label)
                    continue
                if path not in by_path:
                    problems.append("%s citation path not found: %s" % (label, path))
                    continue
                ok, why = S.quote_supported(by_path[path], quote)
                if not ok:
                    problems.append("%s citation quote not verbatim: %s" % (label, why))
            citation_supports(text, citations, problems, label)
            if not sentence.get("support"):
                warnings.append("%s lacks support level (direct/partial/conflict)"
                                % label)
    requested = requested_questions(case, task_text, work)
    missing = sorted(set(requested) - seen)
    if missing and requested:
        problems.append("requested questions not answered: %s" % ", ".join(missing))
    checks["answers"] = len(answers)
    checks["sentences"] = sum(len(a.get("sentences") or []) for a in answers)


def check_versions(work, problems, warnings, index):
    if not index:
        return
    families = {}
    for group in index.get("version_groups") or []:
        for member in group.get("members") or []:
            families[member.get("path")] = group.get("family")
    answers = work.get("answers") or []
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        cited = set()
        for sentence in answer.get("sentences") or []:
            for citation in sentence.get("citations") or []:
                cited.add(str(citation.get("path") or ""))
        used_families = {}
        for path in cited:
            family = families.get(path)
            if family:
                used_families.setdefault(family, []).append(path)
        for family, paths in used_families.items():
            if len(paths) > 1 and str(answer.get("status")) != "conflict":
                problems.append(
                    "answer %s cites multiple versions of %s as if independent "
                    "evidence; state the version relation or use conflict status"
                    % (answer.get("id"), family))


def check_contract(work, output, case, problems):
    contract = case.get("output_contract") if isinstance(case, dict) else {}
    if not isinstance(contract, dict):
        return
    canonical = {"case_id", "status", "task_status", "result", "claims",
                 "uncertainties", "uncertainty", "evidence", "evidence_map",
                 "change_log", "deliverable", "artifacts", "output_contract",
                 "contract_coverage", "preserved_facts"}
    for key in contract:
        if key in canonical:
            continue
        candidates = [Path(output) / "results" / (key + ext) for ext in
                      (".json", ".csv", ".md")] + \
                     [Path(output) / (key + ext) for ext in (".json", ".csv", ".md")]
        if any(p.is_file() and p.stat().st_size > 0 for p in candidates):
            continue
        if key in work and work.get(key) not in (None, "", [], {}):
            continue
        problems.append("contract field %r has no artifact in output/results/"
                        " and no value in the work file" % key)


def run(input_dir, work_dir, output_dir):
    problems, warnings, checks = [], [], {}
    work = load(Path(work_dir) / "library-plan.json")
    if not isinstance(work, dict) or not work:
        return {"ok": False, "problems": ["work/library-plan.json is missing or invalid"],
                "warnings": [], "checks": {}}
    case = load(Path(input_dir) / "case.json", {}) or {}
    task_text = ""
    task_path = Path(input_dir) / "task.md"
    if task_path.is_file():
        task_text = task_path.read_text(encoding="utf-8", errors="replace")
    index = load(Path(work_dir) / "library-index-build.json")
    docs = []
    if Path(input_dir).is_dir():
        try:
            _, docs = S.load_materials(Path(input_dir), ocr=False,
                                       tmp_dir=Path(work_dir) / ".kb-check")
        except Exception as exc:  # noqa: BLE001
            warnings.append("could not reload materials: %s" % exc)
    check_items(work, problems, warnings, index)
    if docs:
        check_answers(work, docs, problems, warnings, checks, case, task_text)
    else:
        problems.append("no readable material; answers cannot be verified")
    check_versions(work, problems, warnings, index)
    check_contract(work, output_dir, case, problems)
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "checks": checks}


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp, work, out = root / "input", root / "work", root / "output"
        (inp / "kb").mkdir(parents=True)
        work.mkdir()
        (out / "results").mkdir(parents=True)
        (out / "results" / "operation_evidence.json").write_text("{}", encoding="utf-8")
        (inp / "kb" / "paper_a.md").write_text(
            "# Paper A\n\nThe running buffer was 20 mM Tris-HCl at pH 7.4.\n",
            encoding="utf-8")
        (inp / "kb" / "paper_b.md").write_text(
            "# Paper B (revised)\n\nIn the revised protocol the running buffer was 25 mM HEPES at pH 7.8.\n",
            encoding="utf-8")
        (inp / "kb" / "protocol.md").write_text(
            "# Protocol\n\nIncubation was performed at 37 C for 30 minutes.\n",
            encoding="utf-8")
        (inp / "case.json").write_text(json.dumps(
            {"case_id": "SELF", "questions": [{"id": "Q1"}, {"id": "Q2"},
                                              {"id": "Q3"}, {"id": "Q4"}],
             "output_contract": {"operation_evidence": "x"}}), encoding="utf-8")
        plan = {
            "case_id": "SELF", "mode": "kb-qa",
            "items": [{"id": p, "path": p, "kind": "text", "version": "v1",
                       "hash": "sha256:" + p, "status": "indexed",
                       "evidence": {"path": p, "locator": "L3",
                                    "quote": q}}
                      for p, q in [
                          ("kb/paper_a.md", "The running buffer was 20 mM Tris-HCl at pH 7.4."),
                          ("kb/paper_b.md", "In the revised protocol the running buffer was 25 mM HEPES at pH 7.8."),
                          ("kb/protocol.md", "Incubation was performed at 37 C for 30 minutes.")]],
            "answers": [
                {"id": "Q1", "question": "buffer pH in original protocol?",
                 "status": "answered",
                 "sentences": [{"text": "The original protocol used 20 mM Tris-HCl at pH 7.4.",
                                "citations": [{"path": "kb/paper_a.md", "locator": "L3",
                                               "quote": "The running buffer was 20 mM Tris-HCl at pH 7.4."}],
                                "support": "direct"}],
                 "missing_evidence": [], "conflict": []},
                {"id": "Q2", "question": "buffer pH in revised protocol?",
                 "status": "answered",
                 "sentences": [{"text": "The revised protocol used 25 mM HEPES at pH 7.8.",
                                "citations": [{"path": "kb/paper_b.md", "locator": "L3",
                                               "quote": "In the revised protocol the running buffer was 25 mM HEPES at pH 7.8."}],
                                "support": "direct"}],
                 "missing_evidence": [], "conflict": []},
                {"id": "Q3", "question": "OD600 for ZX-99?",
                 "status": "insufficient_evidence",
                 "sentences": [],
                 "missing_evidence": ["OD600 for ZX-99", "any mention of ZX-99"],
                 "conflict": []},
                {"id": "Q4", "question": "buffer used for the assay?",
                 "status": "conflict",
                 "sentences": [
                     {"text": "paper_a reports 20 mM Tris-HCl at pH 7.4.",
                      "citations": [{"path": "kb/paper_a.md", "locator": "L3",
                                     "quote": "The running buffer was 20 mM Tris-HCl at pH 7.4."}],
                      "support": "direct"},
                     {"text": "paper_b reports 25 mM HEPES at pH 7.8.",
                      "citations": [{"path": "kb/paper_b.md", "locator": "L3",
                                     "quote": "In the revised protocol the running buffer was 25 mM HEPES at pH 7.8."}],
                      "support": "direct"}],
                 "missing_evidence": [],
                 "conflict": {"sources": ["kb/paper_a.md", "kb/paper_b.md"],
                              "resolution": "not resolved; different protocol versions"}},
            ],
            "evidence": [{"path": "kb/paper_a.md", "locator": "L3",
                          "quote": "The running buffer was 20 mM Tris-HCl at pH 7.4."}],
            "uncertainties": [],
        }
        (work / "library-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        good = run(str(inp), str(work), str(out))
        # Unrelated citation must fail.
        plan["answers"][2] = {
            "id": "Q3", "question": "OD600 for ZX-99?", "status": "answered",
            "sentences": [{"text": "The OD600 was 1.2.",
                           "citations": [{"path": "kb/protocol.md", "locator": "L3",
                                          "quote": "Incubation was performed at 37 C for 30 minutes."}]}],
            "missing_evidence": [], "conflict": []}
        (work / "library-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        bad = run(str(inp), str(work), str(out))
    ok = (good["ok"] and not good["problems"]
          and not bad["ok"]
          and any("lacks numbers" in p for p in bad["problems"]))
    return {"ok": ok, "self_test": True, "good_ok": good["ok"],
            "good_problems": good["problems"][:6],
            "bad_problems": bad["problems"][:6]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        report = self_test()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    report = run(args.input, args.work, args.output)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("ok" if report["ok"] else "problems: %d" % len(report["problems"]))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
