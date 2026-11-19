#!/usr/bin/env python3
"""Validate a benchmark protocol, score report and result.json.

Usage:
    python3 validate_benchmark.py --protocol /app/work/benchmark-protocol.json \
        --metrics /app/work/metrics.json --result /app/output/result.json \
        --root /app/output --json
    python3 validate_benchmark.py --self-test --json

Checks
  * items have unique ids, questions, gold points (or explicit abstain), material evidence
  * capabilities/types/difficulties are declared and covered
  * metrics have formulas, ranges and unique names
  * scoring tool path resolves; baselines exist; limitations exist
  * result.json declares case_id/protocol/metrics/baselines/results/limitations
  * metric values are in range or explicitly not_run; n<30 warns without CI
  * score report errors, counts and metric names agree with the protocol

This is a contract gate, not a scientific-quality score. Stdlib only.
Exit 0 = pass, 1 = problems, 2 = unreadable input.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

REQUIRED_RESULT = ("case_id", "protocol", "metrics", "baselines", "results",
                   "limitations", "uncertainty", "change_log")
REQUIRED_METRICS = ("answer_accuracy", "evidence_recall", "abstention_correctness")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def check_protocol(protocol, root, problems, warnings, checks):
    items = protocol.get("items") or []
    if not isinstance(items, list) or not items:
        problems.append("protocol has no items[]")
        return
    ids = set()
    capabilities = set()
    types = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append("item[%d] is not an object" % i)
            continue
        iid = item.get("item_id")
        if not iid:
            problems.append("item[%d] missing item_id" % i)
            continue
        if iid in ids:
            problems.append("duplicate item_id: %s" % iid)
        ids.add(str(iid))
        if not item.get("question"):
            problems.append("item %s missing question" % iid)
        kind = str(item.get("type") or "")
        if not kind:
            problems.append("item %s missing type" % iid)
        types.add(kind)
        capability = item.get("capability")
        if capability:
            capabilities.add(str(capability))
        else:
            warnings.append("item %s missing capability" % iid)
        if not item.get("difficulty"):
            warnings.append("item %s missing difficulty" % iid)
        gold = item.get("gold_points") or []
        evidence = item.get("gold_evidence") or item.get("evidence") or []
        if not gold and kind != "abstain":
            problems.append("item %s has no gold_points (only abstain may be empty)" % iid)
        if not evidence:
            problems.append("item %s has no gold_evidence" % iid)
        else:
            for j, ev in enumerate(evidence):
                if isinstance(ev, dict):
                    if not any(ev.get(k) for k in
                               ("paragraph_id", "table_id", "figure_id", "page", "locator")):
                        problems.append("item %s evidence[%d] has no locator" % (iid, j))
                elif not str(ev).strip():
                    problems.append("item %s evidence[%d] is empty" % (iid, j))
        if not item.get("source") and not evidence:
            warnings.append("item %s has no source" % iid)
    checks["items"] = len(items)
    checks["capabilities"] = sorted(capabilities)
    checks["types"] = sorted(types)
    if len(capabilities) < 3:
        warnings.append("fewer than 3 capability dimensions")
    if "abstain" not in types:
        warnings.append("no abstain/adversarial item")
    if not any(str(item.get("difficulty")) == "hard" for item in items
               if isinstance(item, dict)):
        warnings.append("no hard item")

    metrics = protocol.get("metrics") or []
    if not isinstance(metrics, list) or not metrics:
        problems.append("protocol has no metrics[]")
        metrics = []
    names = set()
    for i, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            problems.append("metric[%d] is not an object" % i)
            continue
        name = metric.get("name")
        if not name:
            problems.append("metric[%d] missing name" % i)
            continue
        if name in names:
            problems.append("duplicate metric name: %s" % name)
        names.add(name)
        if not metric.get("formula"):
            problems.append("metric %s missing formula" % name)
        rng = metric.get("range")
        if not (isinstance(rng, list) and len(rng) == 2):
            problems.append("metric %s missing/invalid range" % name)
    for name in REQUIRED_METRICS:
        if name not in names:
            warnings.append("recommended metric missing: %s" % name)
    checks["metrics"] = sorted(names)

    tool = protocol.get("scoring_tool") or {}
    tool_path = tool.get("path")
    if not tool_path:
        problems.append("protocol missing scoring_tool.path")
    else:
        root = Path(root) if root else Path.cwd()
        candidates = [Path(tool_path), root / tool_path, root.parent / tool_path]
        if not any(p.is_file() or Path(str(p) + ".md").is_file() for p in candidates):
            problems.append("scoring_tool.path does not resolve: %s" % tool_path)
        if not tool.get("usage"):
            warnings.append("scoring_tool.usage is empty")
    baselines = protocol.get("baselines") or []
    if not baselines:
        problems.append("protocol has no baselines[]")
    if not protocol.get("limitations"):
        warnings.append("protocol has no limitations[]")
    checks["baselines"] = len(baselines)


def check_metrics(report, protocol, problems, warnings, checks):
    if not isinstance(report, dict):
        problems.append("metrics report root is not an object")
        return
    metrics = report.get("metrics") or []
    protocol_names = {m.get("name") for m in (protocol.get("metrics") or [])
                      if isinstance(m, dict)}
    if not metrics:
        problems.append("metrics report has no metrics[]")
        return
    names = set()
    for i, metric in enumerate(metrics):
        if not isinstance(metric, dict):
            problems.append("report metric[%d] is not an object" % i)
            continue
        name = metric.get("name")
        if not name:
            problems.append("report metric[%d] missing name" % i)
            continue
        names.add(name)
        if protocol_names and name not in protocol_names:
            warnings.append("report metric not declared in protocol: %s" % name)
        value = metric.get("value")
        status = metric.get("status")
        if value is None:
            if status not in ("not_run", "insufficient_n", None):
                warnings.append("metric %s has null value but status=%s" % (name, status))
        elif not isinstance(value, (int, float)) or not (0 <= value <= 1):
            problems.append("metric %s value out of [0,1]: %s" % (name, value))
        n = metric.get("n")
        if isinstance(n, int) and 0 < n < 30 and not metric.get("ci95"):
            warnings.append("metric %s has n<30 without ci95" % name)
    errors = report.get("errors") or []
    for error in errors:
        problems.append("score report error: %s" % error)
    counts = report.get("counts") or {}
    if counts.get("missing_pred"):
        warnings.append("%d gold items have no prediction" % counts["missing_pred"])
    checks["report_metrics"] = sorted(names)


def check_result(result, protocol, problems, warnings, checks):
    if not isinstance(result, dict):
        problems.append("result.json root is not an object")
        return
    for key in REQUIRED_RESULT:
        if key not in result:
            problems.append("result.json missing %s" % key)
    if result.get("case_id") != protocol.get("case_id"):
        warnings.append("result.case_id differs from protocol.case_id")
    metrics = result.get("metrics") or []
    names = {m.get("name") for m in metrics if isinstance(m, dict)}
    for name in REQUIRED_METRICS:
        if name not in names:
            warnings.append("result metrics missing %s" % name)
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        value = metric.get("value")
        if value is not None and (not isinstance(value, (int, float)) or not 0 <= value <= 1):
            problems.append("result metric %s out of range: %s" %
                            (metric.get("name"), value))
    checks["result_metrics"] = sorted(names)
    checks["result_status"] = result.get("status")


def self_test(problems, warnings, checks):
    protocol = {
        "case_id": "self-test", "items": [
            {"item_id": "i1", "question": "q", "gold_points": ["91"],
             "gold_evidence": [{"paragraph_id": "p1"}], "type": "fact",
             "difficulty": "hard", "capability": "extract", "source": "m"},
            {"item_id": "i2", "question": "q", "gold_points": [],
             "gold_evidence": [{"paragraph_id": "p2"}], "type": "abstain",
             "difficulty": "medium", "capability": "abstain", "source": "m"},
            {"item_id": "i3", "question": "q", "gold_points": ["x"],
             "gold_evidence": [{"paragraph_id": "p3"}], "type": "compare",
             "difficulty": "easy", "capability": "compare", "source": "m"},
        ],
        "metrics": [{"name": n, "formula": "x", "range": [0, 1]} for n in REQUIRED_METRICS],
        "scoring_tool": {"path": "score_benchmark.py", "usage": "python3 score_benchmark.py"},
        "baselines": [{"name": "abstain_all"}], "limitations": ["small"],
    }
    report = {"metrics": [{"name": n, "formula": "x", "range": [0, 1], "value": 1.0,
                           "n": 3, "ci95": [0, 1], "status": "ok"}
                          for n in REQUIRED_METRICS],
              "errors": [], "counts": {"gold": 3, "pred": 3, "missing_pred": 0}}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "score_benchmark.py").write_text("# test\n", encoding="utf-8")
        check_protocol(protocol, root, problems, warnings, checks)
        check_metrics(report, protocol, problems, warnings, checks)
        result = {"case_id": "self-test", "status": "completed",
                  "protocol": protocol, "metrics": report["metrics"],
                  "baselines": protocol["baselines"], "results": [],
                  "limitations": ["small"], "uncertainty": [], "change_log": []}
        check_result(result, protocol, problems, warnings, checks)
    checks["self_test"] = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", default=None)
    ap.add_argument("--metrics", default=None)
    ap.add_argument("--result", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    problems, warnings, checks = [], [], {}
    if args.self_test:
        self_test(problems, warnings, checks)
    else:
        if not args.protocol:
            ap.error("--protocol is required unless --self-test is used")
        try:
            protocol = load_json(args.protocol)
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "problems": ["cannot read protocol: %s" % exc]},
                             ensure_ascii=False))
            return 2
        check_protocol(protocol, args.root, problems, warnings, checks)
        if args.metrics:
            try:
                report = load_json(args.metrics)
            except (OSError, json.JSONDecodeError) as exc:
                problems.append("cannot read metrics: %s" % exc)
                report = {}
            check_metrics(report, protocol, problems, warnings, checks)
        if args.result:
            try:
                result = load_json(args.result)
            except (OSError, json.JSONDecodeError) as exc:
                problems.append("cannot read result: %s" % exc)
                result = {}
            check_result(result, protocol, problems, warnings, checks)

    output = {"ok": not problems, "problems": problems, "warnings": warnings,
              "checks": checks}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
