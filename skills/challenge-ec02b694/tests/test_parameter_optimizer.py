#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for scripts/parameter_optimizer.py.

All fixtures are synthetic and created in temporary directories; no real or
organizer-provided case data is embedded here.

Run from the skill root (or the repository root with the tests path):
    python -X utf8 -m unittest discover -s tests -p "test_*.py" -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "parameter_optimizer.py"

NORMAL_CSV = """temperature_C,time_min,loading_wt,yield_pct,impurity_pct,cost_cny
40,5,0.5,48,0.8,300
50,5,0.5,56,1.0,330
60,5,0.5,63,1.3,360
70,5,0.5,69,1.5,400
80,5,0.5,85,2.0,450
50,15,0.5,60,1.1,380
60,15,0.5,66,1.5,420
"""

CONSTRAINTS_MD = """# Synthetic constraints (not a real case)

Reactor R1 maximum temperature is 85 C; gasket material is rated to 70 C.
Impurity must stay at or below 1.6%.
Cost cap is 700 CNY per run; at most 10 validation runs are funded.
"""

GOAL_MD = """# Synthetic goal (not a real case)

Maximize yield (%) while keeping impurity at or below 1.6% and cost at or below 700 CNY per run.
"""


class OptimizerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="p4-opt-test-")
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_input(self, name, files):
        input_dir = self.root / name / "input"
        output_dir = self.root / name / "output"
        input_dir.mkdir(parents=True)
        for filename, text in files.items():
            target = input_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return input_dir, output_dir

    def run_script(self, input_dir, output_dir, *extra):
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT),
             "--input", str(input_dir), "--output", str(output_dir)] + list(extra),
            capture_output=True, text=True, timeout=120)
        payload = {}
        json_path = output_dir / "parameter-analysis.json"
        if json_path.is_file():
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        return proc, payload

    def test_normal_path_computes_recommendation_with_evidence(self):
        input_dir, output_dir = self.write_input("normal", {
            "case.json": json.dumps({
                "case_id": "SYN-NORMAL",
                "output_contract": {
                    "feasible_domain": "bounds",
                    "recommendations": "values",
                    "tradeoffs": "responses",
                    "validation_plan": "runs",
                    "constraints": "limits",
                },
            }, ensure_ascii=False),
            "history.csv": NORMAL_CSV,
            "constraints.md": CONSTRAINTS_MD,
            "optimization-goal.md": GOAL_MD,
        })
        proc, payload = self.run_script(input_dir, output_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload.get("status"), "ok", payload.get("not_computable"))
        self.assertTrue((output_dir / "parameter-analysis.md").is_file())

        params = [row["column"] for row in payload["detection"]["parameters"]]
        self.assertEqual(params[:3], ["temperature_C", "time_min", "loading_wt"])
        self.assertEqual(payload["detection"]["objective"]["column"], "yield_pct")
        self.assertGreaterEqual(payload["sensitivity"]["computable_pairs"], 6)

        items = payload["recommendations"]["parameter_items"]
        self.assertTrue(items)
        temperature = next(item for item in items
                           if item["name"] == "temperature_C")
        self.assertIsNotNone(temperature["recommended_value"])
        self.assertLessEqual(temperature["recommended_interval"][1], 70.0)
        self.assertTrue(temperature["support_rows"])
        for row in temperature["support_rows"]:
            self.assertEqual(row["path"], "history.csv")
            self.assertTrue(row["locator"])
            self.assertTrue(row["quote"])

        conflicts = payload["constraints"]["conflicts"]
        self.assertTrue(conflicts)
        self.assertEqual(conflicts[0]["binding"], 70.0)
        anomaly_types = {item["type"] for item in payload["anomalies"]}
        self.assertIn("infeasible_best_row", anomaly_types)
        self.assertTrue(payload["validation_plan"]["runs"])
        self.assertTrue(payload["evidence"])

    def test_empty_input_writes_valid_not_computable_json(self):
        input_dir, output_dir = self.write_input("empty", {
            "case.json": json.dumps({"case_id": "SYN-EMPTY"}, ensure_ascii=False),
        })
        proc, payload = self.run_script(input_dir, output_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload.get("status"), "input_error")
        self.assertTrue(payload.get("not_computable"))
        self.assertEqual(payload.get("recommendations", {}).get("parameter_items", []), [])
        self.assertTrue((output_dir / "parameter-analysis.json").is_file())
        self.assertTrue((output_dir / "parameter-analysis.md").is_file())

    def test_missing_explicit_parameter_column_is_not_computable(self):
        input_dir, output_dir = self.write_input("missing", {
            "case.json": json.dumps({"case_id": "SYN-MISSING"}, ensure_ascii=False),
            "history.csv": NORMAL_CSV,
            "constraints.md": CONSTRAINTS_MD,
        })
        proc, payload = self.run_script(
            input_dir, output_dir, "--parameters", "definitely_missing_column")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload.get("status"), "not_computable")
        self.assertIn("definitely_missing_column",
                      payload["detection"]["missing_columns"])
        self.assertEqual(payload.get("recommendations", {}).get("parameter_items", []), [])
        self.assertTrue((output_dir / "parameter-analysis.md").is_file())

    def test_json_rows_and_no_literal_quote_noted(self):
        input_dir, output_dir = self.write_input("json", {
            "case.json": json.dumps({
                "case_id": "SYN-JSON",
                "parameters": ["temperature_C", "time_min"],
                "responses": ["yield_pct"],
                "objective": {"column": "yield_pct", "direction": "max"},
                "constraints": [{"column": "temperature_C", "op": "<=",
                                 "value": 75, "source": "case.json"}],
            }, ensure_ascii=False),
            "runs.json": json.dumps({"history": [
                {"temperature_C": 45, "time_min": 5, "yield_pct": 50},
                {"temperature_C": 55, "time_min": 5, "yield_pct": 58},
                {"temperature_C": 65, "time_min": 15, "yield_pct": 66},
                {"temperature_C": 78, "time_min": 5, "yield_pct": 80},
            ]}, ensure_ascii=False),
        })
        proc, payload = self.run_script(input_dir, output_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(payload.get("status"), ("ok", "partial"))
        self.assertEqual(payload["detection"]["objective"]["column"], "yield_pct")
        self.assertEqual(payload["dataset"]["row_count"], 4)
        self.assertTrue(payload["recommendations"]["parameter_items"])
        row_evidence = [item for item in payload["evidence"]
                        if item["path"] == "runs.json"]
        self.assertTrue(row_evidence)
        self.assertTrue(any(item.get("quote_note") for item in row_evidence))
        for item in row_evidence:
            self.assertTrue(item["locator"].startswith("$"))

    def test_candidate_plans_are_parsed_but_marked_low_confidence(self):
        input_dir, output_dir = self.write_input("plans", {
            "case.json": json.dumps({
                "case_id": "SYN-PLANS",
                "constraints": [{"column": "temperature_C", "op": "<=",
                                 "value": 75}],
            }, ensure_ascii=False),
            "plans.json": json.dumps({"plans": [
                {"plan_id": "P1",
                 "parameters": {"temperature_C": {"value": 60, "unit": "C"},
                                "time_min": {"value": 10, "unit": "min"}},
                 "predicted_yield_pct": 70, "predicted_impurity_pct": 1.2,
                 "cost_cny": 400},
                {"plan_id": "P2",
                 "parameters": {"temperature_C": {"value": 72, "unit": "C"},
                                "time_min": {"value": 10, "unit": "min"}},
                 "predicted_yield_pct": 77, "predicted_impurity_pct": 1.9,
                 "cost_cny": 460},
                {"plan_id": "P3",
                 "parameters": {"temperature_C": {"value": 80, "unit": "C"},
                                "time_min": {"value": 10, "unit": "min"}},
                 "predicted_yield_pct": 82, "predicted_impurity_pct": 2.5,
                 "cost_cny": 500},
            ]}, ensure_ascii=False),
        })
        proc, payload = self.run_script(input_dir, output_dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload["dataset"]["record_type"], "candidate_plans")
        params = [row["column"] for row in payload["detection"]["parameters"]]
        self.assertIn("temperature_C", params)
        self.assertIn("time_min", params)
        items = payload["recommendations"]["parameter_items"]
        self.assertTrue(items)
        self.assertTrue(all(item["confidence"] == "low" for item in items))
        types = {item["type"] for item in payload["anomalies"]}
        self.assertIn("candidate_plan_only", types)


if __name__ == "__main__":
    unittest.main()
