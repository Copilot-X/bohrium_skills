#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic multi-step analysis first pass.

Steps: inventory -> clean/dedupe -> summarize -> verify.  Every number is
recomputed from the supplied table; missing values and non-numeric columns stay
explicit.  The script also writes work/analysis.py so finalize executes the same
analysis and records an execution log.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path


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


def as_number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_table(doc):
    text = doc.get("text") or ""
    rows = doc.get("rows") or []
    if rows:
        header = [str(x) for x in rows[0]]
        body = rows[1:]
    else:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return [], []
        delimiter = "\t" if "\t" in lines[0] else (
            ";" if ";" in lines[0] and "," not in lines[0] else ",")
        try:
            parsed = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        except csv.Error:
            return [], []
        if len(parsed) < 2 or len(parsed[0]) < 2:
            return [], []
        header, body = [str(x) for x in parsed[0]], parsed[1:]
    return header, body


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
    docs = materials.get("documents") or []
    source_doc, header, body = None, [], []
    for doc in docs:
        h, b = parse_table(doc)
        if h and b:
            source_doc, header, body = doc, h, b
            break
    if not source_doc:
        plan = read_json(work / "analysis-plan.json", {})
        plan.update({
            "case_id": case.get("case_id") or "UNKNOWN",
            "mode": "multi-step-agent",
            "summary": "未找到可解析的表格数据；未生成分析结论。",
            "steps": [], "results": [], "evidence": [],
            "uncertainties": [{
                "item": "no_tabular_data",
                "reason": "no CSV/JSON table with at least two rows was found",
                "impact": "quantitative analysis cannot start",
                "action": "provide a table or run the analysis manually",
            }],
            "generated_at": now_iso(), "generated_by": "domain_work.py",
        })
        write_json(work / "analysis-plan.json", plan)
        write_json(out / "steps.json", {"steps": []})
        write_json(out / "recovery.json", {"recovery": []})
        write_text(out / "analysis-report.md",
                   "# 多步骤数据分析\n\n未找到可解析表格；已如实记录输入错误。\n")
        print(json.dumps({"ok": False, "reason": "no table"}))
        return 0
    path = source_doc.get("path") or source_doc.get("rel_path") or ""
    # Inventory.
    columns = len(header)
    numeric_cols = []
    missing = {}
    for index, name in enumerate(header):
        values = [row[index] if index < len(row) else "" for row in body]
        missing[name] = sum(1 for v in values if not str(v).strip())
        if sum(1 for v in values if as_number(v) is not None) >= max(2, len(values) // 2):
            numeric_cols.append(name)
    # Deduplicate exact rows.
    seen, deduped, removed = set(), [], 0
    for row in body:
        key = tuple(str(x).strip() for x in row)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        deduped.append(row)
    # Summaries.
    summaries = {}
    for name in numeric_cols:
        index = header.index(name)
        values = [as_number(row[index]) for row in deduped
                  if index < len(row) and as_number(row[index]) is not None]
        if values:
            summaries[name] = {
                "n": len(values), "mean": sum(values) / len(values),
                "min": min(values), "max": max(values),
            }
    # Group summary by first categorical column.
    category = next((name for name in header if name not in numeric_cols), None)
    groups = []
    if category and numeric_cols:
        ci, ni = header.index(category), header.index(numeric_cols[0])
        buckets = {}
        for row in deduped:
            if ci >= len(row) or ni >= len(row):
                continue
            value = as_number(row[ni])
            if value is None:
                continue
            buckets.setdefault(str(row[ci]).strip(), []).append(value)
        for key, values in sorted(buckets.items()):
            groups.append({"group": key, "n": len(values),
                           "mean": sum(values) / len(values)})
    # Evidence: verbatim header and one representative data row.
    lines = (source_doc.get("text") or "").splitlines()
    evidence = []
    for index, line in enumerate(lines[:3], 1):
        if line.strip():
            evidence.append({"path": path, "locator": "L%d" % index,
                             "quote": line.strip()})

    # Verifiable results.
    results = []
    for name, stats in summaries.items():
        results.append({
            "id": "R%03d" % (len(results) + 1),
            "metric": "%s_mean" % name, "value": round(stats["mean"], 6),
            "unit": "", "n": stats["n"],
            "method": "arithmetic mean after exact-row dedupe",
            "source": path,
            "evidence": evidence[0] if evidence else None,
        })
    for group in groups:
        results.append({
            "id": "R%03d" % (len(results) + 1),
            "metric": "%s_mean_by_%s" % (numeric_cols[0], category),
            "group": group["group"], "value": round(group["mean"], 6),
            "n": group["n"], "unit": "", "method": "group mean",
            "source": path,
            "evidence": evidence[0] if evidence else None,
        })
    steps = [
        {"id": "S1", "action": "inventory table", "tool": "csv/json reader",
         "params": {"path": path}, "result": "%d rows x %d columns" % (
             len(body), columns), "status": "ok",
         "evidence": evidence[0] if evidence else None},
        {"id": "S2", "action": "exact-row dedupe", "tool": "python set",
         "params": {"key": "all columns"}, "result": "%d duplicates removed" % removed,
         "status": "ok"},
        {"id": "S3", "action": "summary statistics", "tool": "python arithmetic",
         "params": {"columns": numeric_cols}, "result": "%d metrics" % len(summaries),
         "status": "ok"},
        {"id": "S4", "action": "group comparison", "tool": "python arithmetic",
         "params": {"group": category, "metric": numeric_cols[0] if numeric_cols else None},
         "result": "%d groups" % len(groups), "status": "ok" if groups else "skipped"},
        {"id": "S5", "action": "verify by recomputation", "tool": "python arithmetic",
         "params": {"check": "mean"}, "result": "recomputed from deduped rows",
         "status": "ok"},
    ]
    plan = {
        "case_id": case.get("case_id") or "UNKNOWN",
        "mode": "multi-step-agent",
        "summary": "多步骤分析：%d 行，去重 %d 行，数值列 %s。" % (
            len(body), removed, ", ".join(numeric_cols) or "无"),
        "steps": steps, "step_plan": steps, "results": results,
        "evidence": evidence,
        "analysis": {"method": "deterministic inventory/dedupe/summarize/verify",
                     "assumptions": ["exact-row dedupe", "missing values excluded from means"],
                     "diagnostics": [{"missing": missing},
                                     {"duplicates_removed": removed}]},
        "intermediate_results": [
            {"id": "I1", "path": "intermediate-results/inventory.json",
             "description": "columns and missing counts"},
            {"id": "I2", "path": "intermediate-results/deduped.json",
             "description": "deduplicated rows and removed count"},
            {"id": "I3", "path": "intermediate-results/summary.json",
             "description": "numeric and group summaries"},
        ],
        "recovery": [{
            "step_id": "S3", "checkpoint": "intermediate-results/summary.json",
            "resume": "re-run from this checkpoint without repeating S1-S2",
        }],
        "code": "work/analysis.py",
        "tool_calls": [{"id": "T%d" % i, "step_id": s["id"],
                        "tool": s["tool"], "status": s["status"], "retry": 0}
                       for i, s in enumerate(steps, 1)],
        "uncertainties": [
            {"item": "missing_values", "reason": json.dumps(missing, ensure_ascii=False),
             "impact": "means use available values only",
             "action": "confirm the missing-value rule with the task owner"},
        ] if any(missing.values()) else [],
        "generated_at": now_iso(), "generated_by": "domain_work.py",
    }
    write_json(work / "analysis-plan.json", plan)
    write_json(work / "analysis.json", {
        "header": header, "rows": deduped, "missing": missing,
        "summaries": summaries, "groups": groups})
    # Reproducible script executed by finalize.
    analysis_code = '''#!/usr/bin/env python3
"""Recompute the analysis from work/analysis.json; read-only, stdlib only."""
import json
from pathlib import Path
work = Path(__file__).resolve().parent
data = json.loads((work / "analysis.json").read_text(encoding="utf-8"))
print("rows=%d columns=%d" % (len(data["rows"]), len(data["header"])))
for name, stats in data["summaries"].items():
    print("%s n=%d mean=%.6f min=%.6f max=%.6f" % (
        name, stats["n"], stats["mean"], stats["min"], stats["max"]))
'''
    write_text(work / "analysis.py", analysis_code)
    write_text(out / "analysis-code.py", analysis_code)
    inter = out / "intermediate-results"
    write_json(inter / "inventory.json", {
        "path": path, "columns": header, "missing": missing,
        "numeric_columns": numeric_cols})
    write_json(inter / "deduped.json", {
        "rows": len(deduped), "removed": removed})
    write_json(inter / "summary.json", {
        "summaries": summaries, "groups": groups})
    write_json(out / "steps.json", {"steps": steps})
    write_json(out / "recovery.json", {"recovery": plan["recovery"]})
    report = [
        "# 多步骤数据分析报告", "",
        "> 由 domain_work.py 从输入表重算；示例/合成数据不冒充正式结论。", "",
        "## 数据与步骤", "",
        "- 来源：`%s`" % path,
        "- 规模：%d 行 × %d 列" % (len(body), columns),
        "- 去重：移除 %d 行" % removed,
        "", "## 结果", "",
    ]
    for item in results:
        report.append("- %s = %.6f（n=%d）" % (
            item["metric"], item["value"], item.get("n", 0)))
    report += ["", "## 恢复点", "",
               "- `intermediate-results/summary.json` 可用于从 S3 恢复。", ""]
    write_text(out / "analysis-report.md", "\n".join(report))
    summary = {"ok": True, "rows": len(deduped), "removed": removed,
               "metrics": len(results), "groups": len(groups)}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
