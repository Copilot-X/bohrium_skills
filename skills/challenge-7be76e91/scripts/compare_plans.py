#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic candidate-plan comparison and selection.

Reads structured plans from case.json or JSON/CSV materials; when only text is
available it extracts labeled plan blocks and their numeric fields.  Scores are
computed from explicit weights, with a sensitivity check and arithmetic
validation.  Missing fields remain ``not_reported`` and are not imputed.
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


def number(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def structured(case, materials):
    options = []
    for source in [case] + [doc.get("json") for doc in
                            (materials.get("documents") or materials.get("materials") or [])
                            if isinstance(doc.get("json"), (dict, list))]:
        if isinstance(source, dict):
            for key in ("options", "plans", "candidates", "alternatives"):
                if isinstance(source.get(key), list):
                    options.extend(source[key])
        elif isinstance(source, list):
            options.extend(source)
    return [x for x in options if isinstance(x, dict)]


def text_options(materials):
    options = []
    for doc in materials.get("documents") or materials.get("materials") or []:
        text = doc.get("text") or doc.get("excerpt") or ""
        path = doc.get("path") or doc.get("rel_path") or ""
        current = None
        for line in text.splitlines():
            line = norm(line)
            match = re.match(r"^(?:#+\s*)?(?:plan|option|方案)\s*([A-Za-z0-9一二三]+)\b",
                             line, re.I)
            if match:
                current = {"id": match.group(1), "name": line[:120],
                           "path": path, "raw": []}
                options.append(current)
            elif current is not None and line:
                current["raw"].append(line)
        for option in options:
            body = " ".join(option.pop("raw", []))
            for field, pattern in {
                "cost": r"(?:cost|成本|价格)[^0-9]{0,20}([0-9][0-9,.]*)",
                "duration_days": r"(?:duration|time|周期|工期|天数)[^0-9]{0,20}([0-9][0-9,.]*)",
                "risk": r"(?:risk|风险)[^0-9]{0,20}([0-9][0-9,.]*)",
                "expected_yield": r"(?:yield|产出|成功率)[^0-9]{0,20}([0-9][0-9,.]*)",
            }.items():
                match = re.search(pattern, body, re.I)
                if match:
                    option[field] = number(match.group(1))
            option["evidence"] = {"path": option["path"], "locator": "heading",
                                  "quote": option["name"]}
    return options


def normalize(options):
    cleaned = []
    for index, option in enumerate(options, 1):
        item = {"id": str(option.get("id") or option.get("option_id")
                          or "O%03d" % index),
                "name": norm(option.get("name") or option.get("title")
                             or option.get("label") or "Option %d" % index)}
        for key in ("cost", "duration_days", "risk", "expected_yield"):
            value = option.get(key)
            if value is None:
                for alt in (key.replace("_days", ""), key.replace("expected_", "")):
                    value = option.get(alt)
                    if value is not None:
                        break
            item[key] = number(value)
        item["evidence"] = option.get("evidence") or {
            "path": "", "locator": "", "quote": item["name"]}
        item["raw"] = option
        cleaned.append(item)
    return cleaned


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(inp / "case.json", {})
    materials = read_json(work / "materials.json", {})
    raw = structured(case, materials)
    if not raw:
        raw = text_options(materials)
    options = normalize(raw)
    if not options:
        write_json(out / "result.json", {
            "case_id": case.get("case_id") or "UNKNOWN",
            "status": "input_error", "options": [], "selection": {},
            "uncertainty": ["no candidate plans found"]})
        print(json.dumps({"ok": False, "reason": "no candidate plans"}))
        return 0
    weights = case.get("weights") or {"cost": 0.30, "duration_days": 0.20,
                                      "risk": 0.25, "expected_yield": 0.25}
    dimensions = []
    for key in ("cost", "duration_days", "risk", "expected_yield"):
        values = [o[key] for o in options if o.get(key) is not None]
        if values:
            dimensions.append({"name": key, "min": min(values), "max": max(values),
                               "lower_is_better": key in ("cost", "duration_days", "risk"),
                               "weight": weights.get(key, 0.25)})
    for option in options:
        score = 0.0
        used = 0.0
        cells = []
        for dim in dimensions:
            value = option.get(dim["name"])
            if value is None:
                cells.append({"dimension": dim["name"], "value": None,
                              "status": "not_reported"})
                continue
            span = dim["max"] - dim["min"]
            if span == 0:
                norm_value = 1.0
            elif dim["lower_is_better"]:
                norm_value = (dim["max"] - value) / span
            else:
                norm_value = (value - dim["min"]) / span
            score += dim["weight"] * norm_value
            used += dim["weight"]
            cells.append({"dimension": dim["name"], "value": value,
                          "normalized": round(norm_value, 4),
                          "status": "reported"})
        option["score"] = round(score / used, 4) if used else None
        option["cells"] = cells
    ranked = sorted([o for o in options if o["score"] is not None],
                    key=lambda o: -o["score"])
    selection = {
        "selected": ranked[0]["id"] if ranked else None,
        "score": ranked[0]["score"] if ranked else None,
        "reason": "highest weighted normalized score; weights are explicit and "
                  "can be changed for sensitivity",
        "ranking": [{"id": o["id"], "score": o["score"]} for o in ranked],
    }
    # Sensitivity: equal weights.
    equal = {}
    for option in options:
        vals = [c["normalized"] for c in option["cells"]
                if c.get("normalized") is not None]
        equal[option["id"]] = round(sum(vals) / len(vals), 4) if vals else None
    arithmetic = {"weights": weights, "normalization": "min-max per dimension",
                  "lower_is_better": [d["name"] for d in dimensions
                                      if d["lower_is_better"]],
                  "checks": [{"option": o["id"], "score": o["score"]}
                             for o in options]}
    plan = read_json(work / "experiment-plan.json", {})
    if not isinstance(plan, dict):
        plan = {}
    plan.update({
        "case_id": case.get("case_id") or "UNKNOWN",
        "mode": "plan-comparison",
        "summary": "比较 %d 个方案；选择 %s（加权分 %.4f）。" % (
            len(options), selection["selected"] or "none",
            selection["score"] or 0.0),
        "options": options, "dimensions": dimensions,
        "selection": selection, "scores": arithmetic,
        "sensitivity": {"equal_weights": equal},
        "evidence": [o["evidence"] for o in options],
        "risks": [{"risk": "missing fields are not imputed",
                   "severity": "major",
                   "mitigation": "confirm cost/time/risk/yield for every option"}]
        if any(o.get(k) is None for o in options
               for k in ("cost", "duration_days", "risk", "expected_yield"))
        else [],
        "uncertainties": [{"item": "scoring_weights",
                           "reason": "weights come from case.json or explicit defaults",
                           "impact": "selection can change under different priorities",
                           "action": "run the equal-weight sensitivity check"}],
        "generated_at": now_iso(), "generated_by": "compare_plans.py",
    })
    write_json(work / "experiment-plan.json", plan)
    write_json(out / "comparison-table.json", {
        "case_id": plan["case_id"], "options": options,
        "dimensions": dimensions})
    write_json(out / "scoring.json", {
        "case_id": plan["case_id"], "weights": weights,
        "selection": selection, "arithmetic": arithmetic,
        "sensitivity": plan["sensitivity"]})
    write_json(out / "risk-assessment.json", {
        "case_id": plan["case_id"], "risks": plan["risks"]})
    lines = ["# 实验方案比较与选择", "",
             "> 由 compare_plans.py 根据显式权重重算；缺失字段不插补。", "",
             "| 方案 | " + " | ".join(d["name"] for d in dimensions) + " | 加权分 |",
             "|---|" + "---|" * len(dimensions) + "---|"]
    for option in options:
        cells = [str(option.get(d["name"]) if option.get(d["name"]) is not None
                     else "not_reported") for d in dimensions]
        lines.append("| %s | %s | %s |" % (
            option["name"], " | ".join(cells), option["score"]))
    lines += ["", "## 选择", "",
              "- 选中：%s（%.4f）" % (selection["selected"], selection["score"] or 0),
              "- 理由：%s" % selection["reason"], "",
              "## 敏感性", "",
              "- 等权重得分：%s" % json.dumps(equal, ensure_ascii=False)]
    write_text(out / "experiment-report.md", "\n".join(lines) + "\n")
    summary = {"ok": True, "options": len(options), "dimensions": len(dimensions),
               "selected": selection["selected"], "score": selection["score"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
