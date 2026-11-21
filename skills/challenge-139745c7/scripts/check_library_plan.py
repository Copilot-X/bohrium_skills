#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-finalize quality gate for file-search / library plans.

Usage:
    python3 check_library_plan.py --input /input --work /app/work \
        --output /app/output --json
    python3 check_library_plan.py --self-test --json

It checks what the generic finalize cannot:
  * every accepted hit satisfies every hard `filters.must` condition;
  * every `must_not` match is excluded (no rank, excluded_by recorded);
  * filter_execution kept/excluded counts agree with items;
  * no_result is explicit when zero hits remain, and false when hits exist;
  * duplicate copies / superseded versions are not ranked as independent hits;
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
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import skillcore as S  # noqa: E402

WS = re.compile(r"\s+")
QUAL = re.compile(
    r"(?i)^\s*([A-Za-z_][\w.-]*|\w[\w.-]*)\s*(?:=|:|：)\s*(.+?)\s*$")


def norm(value):
    return WS.sub(" ", str(value or "").replace(" ", " ")).strip().lower()


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return default


def flatten(value, out=None):
    if out is None:
        out = []
    if isinstance(value, dict):
        for item in value.values():
            flatten(item, out)
    elif isinstance(value, list):
        for item in value:
            flatten(item, out)
    elif value is not None:
        out.append(str(value))
    return out


def item_haystack(item):
    parts = []
    for key in ("path", "file", "version", "locator", "snippet", "title",
                "name", "id", "summary", "score_reason", "filter_evidence"):
        parts.extend(flatten(item.get(key)))
    parts.extend(flatten(item.get("metadata")))
    parts.extend(flatten(item.get("aliases")))
    parts.extend(flatten(item.get("filter_matches")))
    return norm(" \n ".join(parts))


def filter_matches(item, condition):
    """Return (matched, explicit_evidence) for one hard filter condition."""
    text = item_haystack(item)
    if isinstance(condition, dict):
        key = str(condition.get("field") or condition.get("key") or "")
        value = str(condition.get("value") or condition.get("equals") or "")
        phrase = value or json.dumps(condition, ensure_ascii=False)
        explicit = item.get("filter_matches")
        if isinstance(explicit, dict) and (condition in explicit.values()
                                           or phrase in explicit):
            return True, True
        if key and value:
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if norm(metadata.get(key)) == norm(value):
                return True, True
            if (norm(value) and norm(value) in text
                    and (not key or norm(key) in text)):
                return True, False
            return False, False
        return norm(phrase) in text, False
    phrase = str(condition or "").strip()
    if not phrase:
        return True, True
    explicit = item.get("filter_matches")
    if isinstance(explicit, dict) and phrase in explicit:
        return True, True
    if isinstance(explicit, list) and any(phrase in str(x) for x in explicit):
        return True, True
    qual = QUAL.match(phrase)
    if qual:
        key, value = qual.group(1), qual.group(2)
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if norm(metadata.get(key)) == norm(value):
            return True, True
        if norm(value) and norm(value) in text:
            return True, False
        return False, False
    return norm(phrase) in text, False


def accepted(item):
    rank = item.get("rank")
    return rank not in (None, "", 0, "0") and not item.get("excluded_by")


def excluded(item):
    return bool(item.get("excluded_by")) or item.get("rank") in (None, "", 0, "0")


def check_filters(work, problems, warnings, checks):
    items = [x for x in work.get("items") or [] if isinstance(x, dict)]
    filters = work.get("filters") if isinstance(work.get("filters"), dict) else {}
    must = filters.get("must") or []
    must_not = filters.get("must_not") or []
    if not isinstance(must, list):
        must = [must]
    if not isinstance(must_not, list):
        must_not = [must_not]
    must = [x for x in must if str(x).strip()]
    must_not = [x for x in must_not if str(x).strip()]
    if not must and not must_not:
        problems.append("filters.must and filters.must_not are both empty")
    report = {"must": [], "must_not": []}
    for condition in must:
        matched = []
        for item in items:
            ok, explicit = filter_matches(item, condition)
            if not ok and accepted(item):
                problems.append(
                    "accepted item %s does not satisfy hard filter %r"
                    % (item.get("id") or item.get("path"), condition))
            if ok:
                matched.append(item.get("id") or item.get("path"))
                if not explicit:
                    warnings.append(
                        "item %s matches hard filter %r only by free text; "
                        "add filter_matches with evidence"
                        % (item.get("id") or item.get("path"), condition))
        report["must"].append({"filter": condition,
                               "matched": matched,
                               "kept": sum(1 for x in items if accepted(x)),
                               "excluded": sum(1 for x in items if excluded(x))})
    for condition in must_not:
        matched = []
        for item in items:
            ok, _ = filter_matches(item, condition)
            if ok:
                matched.append(item.get("id") or item.get("path"))
                if accepted(item):
                    problems.append(
                        "item %s matches must_not filter %r but is still ranked"
                        % (item.get("id") or item.get("path"), condition))
                elif not item.get("excluded_by"):
                    problems.append(
                        "item %s is unranked for must_not %r without excluded_by"
                        % (item.get("id") or item.get("path"), condition))
        report["must_not"].append({"filter": condition,
                                   "matched": matched,
                                   "excluded": sum(1 for x in items if excluded(x))})
    if filters and not work.get("filter_execution"):
        problems.append("filters are declared but filter_execution is empty")
    for index, row in enumerate(work.get("filter_execution") or [], 1):
        if not isinstance(row, dict):
            problems.append("filter_execution[%d] is not an object" % index)
            continue
        name = str(row.get("filter") or "")
        if not name:
            problems.append("filter_execution[%d] lacks filter" % index)
        kept, excl = row.get("kept"), row.get("excluded")
        if not isinstance(kept, int) or not isinstance(excl, int):
            problems.append("filter_execution[%d] kept/excluded must be integers" % index)
            continue
        if kept + excl != len(items):
            problems.append("filter_execution[%d] kept+excluded=%d but items=%d"
                            % (index, kept + excl, len(items)))
        expected_kept = sum(1 for item in items if accepted(item))
        if kept != expected_kept:
            problems.append("filter_execution[%d] kept=%d but accepted items=%d"
                            % (index, kept, expected_kept))
    report["total_items"] = len(items)
    report["accepted"] = sum(1 for item in items if accepted(item))
    checks["filters"] = report
    return report


def check_items(work, problems, warnings, checks, index):
    items = [x for x in work.get("items") or [] if isinstance(x, dict)]
    if not items:
        problems.append("items is empty")
    ranks = {}
    for item in items:
        iid = item.get("id") or item.get("path")
        if not (item.get("path") or item.get("id")):
            problems.append("item lacks path/id")
        if not item.get("evidence"):
            problems.append("item %s lacks evidence" % iid)
        if accepted(item):
            rank = item.get("rank")
            try:
                rank = int(rank)
            except (TypeError, ValueError):
                problems.append("item %s has non-integer rank %r" % (iid, item.get("rank")))
                rank = None
            if rank is not None:
                if rank < 1:
                    problems.append("item %s rank must be >= 1" % iid)
                if rank in ranks:
                    problems.append("duplicate rank %d: %s and %s" % (rank, ranks[rank], iid))
                ranks[rank] = iid
            if not (item.get("snippet") or item.get("locator")):
                warnings.append("item %s lacks snippet/locator" % iid)
            if not item.get("score_reason"):
                warnings.append("item %s lacks score_reason" % iid)
        if item.get("excluded_by") and (item.get("rank") not in (None, "", 0, "0")):
            problems.append("excluded item %s still has rank %r"
                            % (iid, item.get("rank")))
    # duplicate copies / superseded versions must not be independent ranked hits
    if index:
        by_path = {f["path"]: f for f in index.get("files") or []}
        families = {}
        for group in index.get("version_groups") or []:
            for member in group.get("members") or []:
                families[member.get("path")] = {
                    "family": group.get("family"),
                    "version_token": member.get("version_token")}
        ranked = [item for item in items if accepted(item)]
        seen_family = {}
        for item in ranked:
            info = families.get(item.get("path")) or {}
            family = info.get("family") or str(item.get("version_family") or "")
            if not family:
                continue
            if family in seen_family:
                problems.append(
                    "two versions of the same document are ranked independently: "
                    "%s and %s; keep one as current and record the other in "
                    "version_conflict/superseded_by"
                    % (seen_family[family], item.get("id") or item.get("path")))
            seen_family[family] = item.get("id") or item.get("path")
    checks["ranks"] = sorted(ranks)
    return items


def check_no_result(work, problems, checks):
    items = [x for x in work.get("items") or [] if isinstance(x, dict)]
    kept = [x for x in items if accepted(x)]
    no_result = work.get("no_result")
    explicit = False
    reason = ""
    if isinstance(no_result, dict):
        explicit = bool(no_result.get("is_no_result") or no_result.get("no_result")
                        or no_result.get("hits") == 0
                        or no_result.get("status") in ("no_result", "empty"))
        reason = str(no_result.get("reason") or no_result.get("explanation") or "")
    elif isinstance(no_result, bool):
        explicit = no_result
        reason = str(work.get("no_result_reason") or "")
    elif isinstance(no_result, str):
        explicit = norm(no_result) in ("true", "yes", "1", "no_result", "none")
        reason = no_result
    if kept and explicit:
        problems.append("no_result is true but %d ranked items exist" % len(kept))
    if not kept and not explicit:
        problems.append("no ranked items but no_result is not explicitly set")
    if not kept and explicit and not reason:
        problems.append("no_result is set without a reason/explanation")
    checks["no_result"] = {"explicit": explicit, "kept": len(kept), "reason": reason}


def check_unreadable_and_versions(work, problems, warnings, checks, index):
    index = index or {}
    work_unreadable = {str(x.get("path") or x.get("file") or "")
                       for x in work.get("unreadable") or [] if isinstance(x, dict)}
    for row in index.get("unreadable") or []:
        if row.get("path") and row["path"] not in work_unreadable:
            problems.append("unreadable input file is not recorded: %s" % row["path"])
    if work.get("unreadable") and not isinstance(work.get("unreadable"), list):
        problems.append("unreadable must be a list")
    if index.get("version_groups") and not work.get("version_conflict"):
        warnings.append("version groups exist but version_conflict is empty; "
                        "state how the current version was chosen")


def check_evidence(work, problems, warnings, checks, docs):
    by_path = {d.get("rel_path"): S.doc_text(d) for d in docs}
    for key in ("evidence",):
        for index, item in enumerate(work.get(key) or [], 1):
            if not isinstance(item, dict):
                problems.append("%s[%d] is not an object" % (key, index))
                continue
            path = str(item.get("path") or "")
            quote = str(item.get("quote") or "").strip()
            if not quote:
                problems.append("%s[%d] has empty quote" % (key, index))
                continue
            if path not in by_path:
                problems.append("%s[%d] path not found: %s" % (key, index, path))
                continue
            ok, why = S.quote_supported(by_path[path], quote)
            if not ok:
                problems.append("%s[%d] quote not verbatim: %s" % (key, index, why))
    # item-level evidence
    for item in work.get("items") or []:
        if not isinstance(item, dict):
            continue
        ev = item.get("evidence")
        items = ev if isinstance(ev, list) else [ev] if isinstance(ev, dict) else []
        for index, one in enumerate(items, 1):
            if not isinstance(one, dict):
                problems.append("item %s evidence[%d] is not an object"
                                % (item.get("id") or item.get("path"), index))
                continue
            path = str(one.get("path") or item.get("path") or "")
            quote = str(one.get("quote") or "").strip()
            if not quote:
                problems.append("item %s evidence[%d] has empty quote"
                                % (item.get("id") or item.get("path"), index))
            elif path not in by_path:
                problems.append("item %s evidence path not found: %s"
                                % (item.get("id") or item.get("path"), path))
            else:
                ok, why = S.quote_supported(by_path[path], quote)
                if not ok:
                    problems.append("item %s evidence quote not verbatim: %s"
                                    % (item.get("id") or item.get("path"), why))
    checks["evidence_checked"] = len(work.get("evidence") or [])


def check_contract(work, output, case, problems, warnings):
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
        candidates = [
            Path(output) / "results" / (key + ".json"),
            Path(output) / (key + ".json"),
            Path(output) / "results" / (key + ".csv"),
            Path(output) / (key + ".csv"),
            Path(output) / "results" / (key + ".md"),
            Path(output) / (key + ".md"),
        ]
        if any(p.is_file() and p.stat().st_size > 0 for p in candidates):
            continue
        if key in work and work.get(key) not in (None, "", [], {}):
            continue
        problems.append("contract field %r has no same-named artifact in "
                        "output/results/ and no value in the work file" % key)


def run(input_dir, work_dir, output_dir):
    problems, warnings, checks = [], [], {}
    work_path = Path(work_dir) / "library-plan.json"
    work = load(work_path)
    if not isinstance(work, dict) or not work:
        return {"ok": False, "problems": ["work/library-plan.json is missing or invalid"],
                "warnings": [], "checks": {}}
    case = load(Path(input_dir) / "case.json", {}) or {}
    index = None
    index_path = Path(work_dir) / "library-index-build.json"
    if index_path.is_file():
        index = load(index_path)
    elif Path(input_dir).is_dir():
        try:
            sys.path.insert(0, str(HERE))
            import build_library_index as B
            index = B.build(input_dir)
        except Exception as exc:  # noqa: BLE001
            warnings.append("could not rebuild library index: %s" % exc)
    items = check_items(work, problems, warnings, checks, index)
    check_filters(work, problems, warnings, checks)
    check_no_result(work, problems, checks)
    check_unreadable_and_versions(work, problems, warnings, checks, index)
    if Path(input_dir).is_dir():
        try:
            _, docs = S.load_materials(Path(input_dir), ocr=False,
                                       tmp_dir=Path(work_dir) / ".check-ocr")
        except Exception as exc:  # noqa: BLE001
            docs = []
            warnings.append("could not reload materials for quote check: %s" % exc)
        if docs:
            check_evidence(work, problems, warnings, checks, docs)
        else:
            warnings.append("quote verbatim check skipped: no readable material")
    check_contract(work, output_dir, case, problems, warnings)
    checks["items"] = len(items)
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "checks": checks, "work_file": str(work_path)}


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp = root / "input"
        work = root / "work"
        out = root / "output"
        inp.mkdir()
        work.mkdir()
        (out / "results").mkdir(parents=True)
        (inp / "projectA_batch3_v2.md").write_text(
            "# Buffer\n\nThe running buffer was changed to 25 mM HEPES, pH 7.8.\n",
            encoding="utf-8")
        (inp / "projectB_batch1.md").write_text(
            "# Buffer\n\nThe running buffer was 30 mM phosphate, pH 8.0.\n",
            encoding="utf-8")
        (inp / "case.json").write_text(json.dumps(
            {"case_id": "SELF", "output_contract": {"operation_evidence": "x"}}),
            encoding="utf-8")
        (out / "results" / "operation_evidence.json").write_text("{}", encoding="utf-8")
        plan = {
            "case_id": "SELF", "mode": "file-search",
            "items": [
                {"id": "R1", "rank": 1, "path": "projectA_batch3_v2.md",
                 "version": "v2", "locator": "L3", "snippet": "25 mM HEPES",
                 "score_reason": "matches question",
                 "excluded_by": [],
                 "filter_matches": {"project=PA": {"path": "projectA_batch3_v2.md",
                                                   "locator": "L1",
                                                   "quote": "# Buffer"}},
                 "evidence": {"path": "projectA_batch3_v2.md", "locator": "L3",
                              "quote": "The running buffer was changed to 25 mM HEPES, pH 7.8."}},
                {"id": "R2", "rank": None, "path": "projectB_batch1.md",
                 "locator": "L3", "snippet": "30 mM phosphate",
                 "excluded_by": ["project=PA"], "score_reason": "hard filter",
                 "evidence": {"path": "projectB_batch1.md", "locator": "L3",
                              "quote": "The running buffer was 30 mM phosphate, pH 8.0."}},
            ],
            "filters": {"must": ["project=PA"], "must_not": ["project=PB"],
                        "applied_to": "all records"},
            "filter_execution": [
                {"filter": "project=PA", "kept": 1, "excluded": 1},
                {"filter": "project=PB", "kept": 1, "excluded": 1}],
            "no_result": False,
            "evidence": [{"path": "projectA_batch3_v2.md", "locator": "L3",
                          "quote": "The running buffer was changed to 25 mM HEPES, pH 7.8."}],
            "unreadable": [], "version_conflict": [],
        }
        (work / "library-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        good = run(str(inp), str(work), str(out))
        # A relaxed filter must fail: project B is ranked under must=project=PA.
        plan["items"][1]["rank"] = 1
        plan["items"][1]["excluded_by"] = []
        plan["items"][0]["rank"] = 2
        (work / "library-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        bad = run(str(inp), str(work), str(out))
        # no_result branch must be explicit.
        plan["items"] = []
        plan["no_result"] = {}
        (work / "library-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        no_result = run(str(inp), str(work), str(out))
    ok = (good["ok"] and not good["problems"]
          and not bad["ok"] and any("does not satisfy hard filter" in p
                                    for p in bad["problems"])
          and not no_result["ok"]
          and any("no_result is not explicitly set" in p
                  for p in no_result["problems"]))
    return {"ok": ok, "self_test": True,
            "good_ok": good["ok"], "bad_problems": bad["problems"][:4],
            "no_result_problems": no_result["problems"][:4]}


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
