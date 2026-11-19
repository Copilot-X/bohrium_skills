#!/usr/bin/env python3
"""Validate a peer-review response letter and manuscript changes.

Usage:
    python3 check_response_letter.py --result /app/output/result.json \
        --manuscript /app/output/manuscript-revised.md --root /app/output --json
    python3 check_response_letter.py --self-test --json

Checks
  * every response has comment_id/reviewer/action/response
  * revised/clarified changes have before/after/diff, and after appears in the manuscript
  * computed responses declare method/result/data; deferred declare reason/plan/impact
  * disagreed responses declare a basis
  * reviewers' counts agree; deferred/disagreements lists agree
  * honesty flags are false unless supported by real new data
  * artifacts resolve; response and revised manuscript exist

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ACTIONS = {"revised", "clarified", "computed", "deferred", "disagreed"}
WS = re.compile(r"\s+")
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|待补充)\b", re.I)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def norm(text):
    return WS.sub(" ", str(text or "")).strip()


def validate(result, manuscript_text, root):
    problems, warnings, checks = [], [], {}
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["result root is not an object"],
                "warnings": [], "checks": {}}
    for key in ("case_id", "reviewers", "responses", "artifacts", "deferred",
                "disagreements", "honesty"):
        if key not in result:
            problems.append("result.json missing %s" % key)
    reviewers = result.get("reviewers") or []
    reviewer_counts = {}
    for i, item in enumerate(reviewers):
        if not isinstance(item, dict):
            problems.append("reviewer[%d] is not an object" % i)
            continue
        rid = item.get("id")
        if not rid:
            problems.append("reviewer[%d] missing id" % i)
            continue
        reviewer_counts[str(rid)] = item
    responses = result.get("responses") or []
    if not isinstance(responses, list) or not responses:
        problems.append("responses is empty")
        responses = []
    seen = set()
    addressed = {}
    deferred_ids = set()
    disagreement_ids = set()
    for i, item in enumerate(responses):
        if not isinstance(item, dict):
            problems.append("response[%d] is not an object" % i)
            continue
        cid = item.get("comment_id")
        if not cid:
            problems.append("response[%d] missing comment_id" % i)
            continue
        if cid in seen:
            problems.append("duplicate response for %s" % cid)
        seen.add(str(cid))
        rid = str(item.get("reviewer") or "")
        if rid and reviewer_counts and rid not in reviewer_counts:
            warnings.append("response %s references unknown reviewer %s" % (cid, rid))
        if not item.get("response"):
            problems.append("response %s has no response text" % cid)
        action = item.get("action")
        if action not in ACTIONS:
            problems.append("response %s invalid action: %s" % (cid, action))
            continue
        if action in ("revised", "clarified"):
            change = item.get("manuscript_change")
            if not isinstance(change, dict):
                problems.append("response %s action=%s has no manuscript_change" %
                                (cid, action))
            else:
                for key in ("chapter", "before", "after"):
                    if not change.get(key):
                        problems.append("response %s change missing %s" % (cid, key))
                if not change.get("diff"):
                    warnings.append("response %s change has no diff path" % cid)
                if manuscript_text and change.get("after"):
                    if norm(change["after"]) not in norm(manuscript_text):
                        problems.append("response %s 'after' not found in revised manuscript" % cid)
                if change.get("diff"):
                    base = Path(root) if root else Path.cwd()
                    candidates = [Path(change["diff"]), base / change["diff"]]
                    if not any(p.is_file() for p in candidates):
                        problems.append("response %s diff missing: %s" %
                                        (cid, change["diff"]))
            addressed[rid] = addressed.get(rid, 0) + 1
        elif action == "computed":
            analysis = item.get("new_analysis") or {}
            if not isinstance(analysis, dict) or analysis.get("performed") is not True:
                problems.append("response %s computed but new_analysis not performed" % cid)
            else:
                for key in ("method", "result", "data"):
                    if not analysis.get(key):
                        problems.append("response %s new_analysis missing %s" % (cid, key))
            addressed[rid] = addressed.get(rid, 0) + 1
        elif action == "deferred":
            if not item.get("deferred_reason") or not item.get("plan"):
                problems.append("response %s deferred but missing reason/plan" % cid)
            if not item.get("impact"):
                warnings.append("response %s deferred but missing impact" % cid)
            deferred_ids.add(str(cid))
        elif action == "disagreed":
            if not item.get("basis"):
                problems.append("response %s disagreed but missing basis" % cid)
            disagreement_ids.add(str(cid))
    checks["responses"] = len(responses)
    checks["comments"] = len(seen)

    for rid, item in reviewer_counts.items():
        comments = item.get("comments")
        if comments is not None and int(comments) != sum(
                1 for cid in seen if str(cid).startswith(rid + "-")):
            warnings.append("reviewer %s comments count may not match response ids" % rid)
    deferred = {str(x.get("comment_id")) for x in result.get("deferred") or []
                if isinstance(x, dict)}
    if deferred != deferred_ids:
        warnings.append("deferred[] does not match deferred responses")
    disagreements = {str(x.get("comment_id")) for x in result.get("disagreements") or []
                     if isinstance(x, dict)}
    if disagreements != disagreement_ids:
        warnings.append("disagreements[] does not match disagreed responses")

    honesty = result.get("honesty") or {}
    if not isinstance(honesty, dict):
        problems.append("honesty is not an object")
        honesty = {}
    if honesty.get("acceptance_claimed") is True:
        problems.append("honesty.acceptance_claimed must be false")
    if honesty.get("full_revision_claimed") is True and deferred_ids:
        problems.append("honesty.full_revision_claimed=true while deferrals exist")
    if honesty.get("new_experiments_claimed") is True:
        if not any(isinstance(r, dict) and r.get("new_analysis", {}).get("performed") is True
                   for r in responses):
            problems.append("new_experiments_claimed=true without performed new_analysis")
        else:
            warnings.append("new_experiments_claimed=true; verify data provenance")

    artifacts = result.get("artifacts") or []
    roles = {}
    base = Path(root) if root else Path.cwd()
    for i, item in enumerate(artifacts):
        if not isinstance(item, dict):
            problems.append("artifact[%d] is not an object" % i)
            continue
        role = str(item.get("role") or "").lower()
        rel = item.get("path")
        if not rel:
            problems.append("artifact[%d] missing path" % i)
            continue
        candidate = Path(rel)
        if not candidate.is_absolute():
            candidate = base / rel
        if not candidate.is_file():
            problems.append("artifact missing: %s" % rel)
            continue
        roles.setdefault(role, []).append(candidate)
    for required in ("editable", "evidence"):
        if required not in roles:
            problems.append("artifacts missing role=%s" % required)
    checks["artifact_roles"] = sorted(roles)
    if manuscript_text and PLACEHOLDER.search(manuscript_text):
        problems.append("revised manuscript contains placeholder text")
    return {"ok": not problems, "problems": problems, "warnings": warnings, "checks": checks}


def self_test():
    return {
        "case_id": "response-self-test",
        "reviewers": [{"id": "R1", "comments": 2, "addressed": 1, "deferred": 1}],
        "responses": [
            {"comment_id": "R1-C1", "reviewer": "R1", "comment": "fix wording",
             "response": "We have clarified this.", "action": "revised",
             "manuscript_change": {"chapter": "2.3", "paragraph_id": "p1",
                                   "before": "old wording", "after": "new wording",
                                   "diff": "diffs/R1-C1.diff"}},
            {"comment_id": "R1-C2", "reviewer": "R1", "comment": "new experiment",
             "response": "We did not perform new experiments.", "action": "deferred",
             "deferred_reason": "needs new data", "plan": "next revision",
             "impact": "scope limited"},
        ],
        "artifacts": [{"role": "editable", "path": "response-letter.md"},
                      {"role": "editable", "path": "manuscript-revised.md"},
                      {"role": "evidence", "path": "point-by-point.json"}],
        "deferred": [{"comment_id": "R1-C2", "reason": "needs new data", "plan": "next"}],
        "disagreements": [],
        "honesty": {"new_experiments_claimed": False, "acceptance_claimed": False,
                    "full_revision_claimed": False},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None)
    ap.add_argument("--manuscript", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "response-letter.md").write_text("letter", encoding="utf-8")
            (root / "manuscript-revised.md").write_text("new wording", encoding="utf-8")
            (root / "point-by-point.json").write_text("{}", encoding="utf-8")
            (root / "diffs").mkdir()
            (root / "diffs/R1-C1.diff").write_text("-old\n+new\n", encoding="utf-8")
            report = validate(self_test(), "new wording", root)
        print(json.dumps({"ok": report["ok"], "self_test": True,
                          "problems": report["problems"],
                          "warnings": report["warnings"]}, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if not args.result:
        ap.error("--result is required unless --self-test is used")
    try:
        result = load_json(args.result)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "problems": ["cannot read result: %s" % exc]},
                         ensure_ascii=False))
        return 2
    text = None
    if args.manuscript:
        try:
            text = Path(args.manuscript).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = None
    report = validate(result, text, args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
