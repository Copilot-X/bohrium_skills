#!/usr/bin/env python3
"""Validate Results/Discussion output and its claim table.

Usage:
    python3 check_results_discussion.py --result /app/output/result.json \
        --manuscript /app/output/results-discussion.md --root /app/output --json
    python3 check_results_discussion.py --self-test --json

Checks
  * results have ids/value/unit/condition/n/error/source/figure
  * significance is only claimed with a test and p-value
  * interpretations have based_on ids and a valid strength
  * limitations are specific (limitation + impact)
  * sections results/discussion exist and reference known result ids
  * artifacts resolve; no placeholder text

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import json
import re
import sys
from pathlib import Path

STRENGTHS = {"supported", "suggested", "speculative"}
KINDS = {"reported", "derived", "estimated"}
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|待补充|待填写)\b", re.I)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def validate(result, manuscript_text, root):
    problems, warnings, checks = [], [], {}
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["result root is not an object"],
                "warnings": [], "checks": {}}
    for key in ("case_id", "sections", "results", "interpretations",
                "limitations", "artifacts"):
        if key not in result:
            problems.append("result.json missing %s" % key)
    results = result.get("results") or []
    result_ids = set()
    result_numbers = set()
    for i, item in enumerate(results):
        if not isinstance(item, dict):
            problems.append("result[%d] is not an object" % i)
            continue
        rid = item.get("result_id")
        if not rid:
            problems.append("result[%d] missing result_id" % i)
            continue
        if rid in result_ids:
            problems.append("duplicate result_id: %s" % rid)
        result_ids.add(str(rid))
        for key in ("statement", "value", "unit", "condition", "source"):
            if item.get(key) in (None, ""):
                problems.append("result %s missing %s" % (rid, key))
        if item.get("n") is None:
            warnings.append("result %s has no n" % rid)
        error = item.get("error") or {}
        if item.get("value") not in (None, "") and not error:
            warnings.append("result %s has no error type/value" % rid)
        if error and not error.get("type"):
            problems.append("result %s error has no type" % rid)
        significance = item.get("significance") or {}
        if significance:
            if not significance.get("test") or significance.get("p") in (None, ""):
                problems.append("result %s significance lacks test/p" % rid)
        if item.get("kind") and item["kind"] not in KINDS:
            warnings.append("result %s kind unusual: %s" % (rid, item["kind"]))
        if item.get("value") not in (None, ""):
            result_numbers.add(re.sub(r"\s+", "", str(item["value"])))
    checks["results"] = len(results)

    interpretations = result.get("interpretations") or []
    for i, item in enumerate(interpretations):
        if not isinstance(item, dict):
            problems.append("interpretation[%d] is not an object" % i)
            continue
        iid = item.get("interpretation_id") or "I%d" % (i + 1)
        if not item.get("text"):
            problems.append("interpretation %s missing text" % iid)
        if item.get("strength") not in STRENGTHS:
            problems.append("interpretation %s invalid strength: %s" %
                            (iid, item.get("strength")))
        based_on = item.get("based_on") or []
        if not based_on:
            problems.append("interpretation %s has no based_on" % iid)
        for rid in based_on:
            if result_ids and str(rid) not in result_ids:
                problems.append("interpretation %s references unknown result %s" %
                                (iid, rid))
    checks["interpretations"] = len(interpretations)

    limitations = result.get("limitations") or []
    if not limitations:
        problems.append("limitations is empty")
    for i, item in enumerate(limitations):
        if isinstance(item, str):
            warnings.append("limitation[%d] is a bare string; add impact" % i)
            continue
        if not isinstance(item, dict):
            problems.append("limitation[%d] is not an object" % i)
            continue
        if not item.get("limitation"):
            problems.append("limitation[%d] missing limitation" % i)
        if not item.get("impact"):
            warnings.append("limitation[%d] missing impact" % i)
    checks["limitations"] = len(limitations)

    sections = result.get("sections") or []
    section_ids = {s.get("id") for s in sections if isinstance(s, dict)}
    for required in ("results", "discussion"):
        if required not in section_ids:
            problems.append("sections missing %s" % required)
    for i, item in enumerate(sections):
        if isinstance(item, dict):
            for rid in item.get("results") or []:
                if result_ids and str(rid) not in result_ids:
                    problems.append("section %s references unknown result %s" %
                                    (item.get("id"), rid))

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
        roles[role] = candidate
    for required in ("editable", "evidence"):
        if required not in roles:
            problems.append("artifacts missing role=%s" % required)
    checks["artifact_roles"] = sorted(roles)
    if manuscript_text:
        if PLACEHOLDER.search(manuscript_text):
            problems.append("manuscript contains placeholder text")
        if "results" in section_ids and "discussion" in section_ids:
            for word in ("导致", "证明", "causes", "proves"):
                if word in manuscript_text:
                    warnings.append("manuscript contains strong causal wording: %s" % word)
    return {"ok": not problems, "problems": problems, "warnings": warnings, "checks": checks}


def self_test():
    return {
        "case_id": "rd-self-test",
        "sections": [{"id": "results", "results": ["R01"], "figures": ["Fig.2a"]},
                     {"id": "discussion", "answers": "Q1", "citations": ["ref3"]}],
        "results": [{"result_id": "R01", "statement": "92.1% conversion",
                     "value": "92.1", "unit": "%", "condition": "350 C",
                     "n": 3, "error": {"type": "SD", "value": "1.8"},
                     "significance": {"test": "Tukey", "p": "0.003"},
                     "source": {"path": "m.csv", "locator": "rows 1-3"},
                     "figure": "Fig.2a", "kind": "reported"}],
        "interpretations": [{"interpretation_id": "I01",
                             "text": "vacancies may stabilize intermediate",
                             "based_on": ["R01"], "strength": "suggested",
                             "citations": ["ref3"]}],
        "limitations": [{"limitation": "only 300-400 C", "impact": "cannot extrapolate"}],
        "artifacts": [{"role": "editable", "path": "results-discussion.md"},
                      {"role": "evidence", "path": "claim-table.json"}],
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
            (root / "results-discussion.md").write_text("Results and Discussion.",
                                                        encoding="utf-8")
            (root / "claim-table.json").write_text("[]", encoding="utf-8")
            report = validate(self_test(), "Results and Discussion.", root)
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
