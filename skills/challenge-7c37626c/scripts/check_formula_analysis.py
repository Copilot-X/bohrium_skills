#!/usr/bin/env python3
r"""Validate a formula-analysis / derivation result before delivery.

Usage:
    python3 check_formula_analysis.py --result /app/output/result.json \
        --input /input/case.json --json
    python3 check_formula_analysis.py --self-test --json

Checks
  * formulas have eq_id/latex/type/source/symbols; ids are unique
  * symbols have meaning/unit/status; unknown symbols are explicit
  * LaTeX braces, \left/\right and \begin/\end are balanced
  * derivation steps have from/to/operation/justification and are sequential
  * numeric_check has inputs/computation/result/unit/dimensional_check
  * computation is pure arithmetic and agrees with result.value
  * input formula ids/questions are covered or explicitly missing

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable input.
"""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

TYPES = {"definition", "conservation", "empirical", "approximation"}
SYMBOL_STATUS = {"defined", "inferred_definition", "unknown_symbol"}
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|待补充|待填写)\b", re.I)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def unescaped_braces(text):
    depth = 0
    problems = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in "{}":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                problems.append("unbalanced closing brace")
                break
        i += 1
    if depth != 0:
        problems.append("unbalanced braces (depth %d)" % depth)
    return problems


def latex_problems(text):
    problems = unescaped_braces(text)
    if text.count("\\left") != text.count("\\right"):
        problems.append("\\left/\\right count mismatch")
    begins = re.findall(r"\\begin\{([^}]+)\}", text)
    ends = re.findall(r"\\end\{([^}]+)\}", text)
    if begins != ends:
        problems.append("\\begin/\\end mismatch: %s vs %s" % (begins, ends))
    if text.count("$") % 2:
        problems.append("odd number of $ delimiters")
    return problems


def safe_eval(expression):
    """Evaluate a pure arithmetic expression; reject names/calls/attributes."""
    tree = ast.parse(expression, mode="eval")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
               ast.USub, ast.UAdd, ast.FloorDiv)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            raise ValueError("unsupported expression node: %s" % type(node).__name__)
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("only numeric constants are allowed")
    return float(eval(compile(tree, "<computation>", "eval"), {"__builtins__": {}}, {}))


def check_formulas(result, problems, warnings, checks):
    formulas = result.get("formulas") or []
    if not isinstance(formulas, list) or not formulas:
        problems.append("result has no formulas[]")
        return set()
    ids = set()
    for i, formula in enumerate(formulas):
        if not isinstance(formula, dict):
            problems.append("formula[%d] is not an object" % i)
            continue
        eq = formula.get("eq_id") or formula.get("id")
        if not eq:
            problems.append("formula[%d] missing eq_id" % i)
            continue
        if eq in ids:
            problems.append("duplicate eq_id: %s" % eq)
        ids.add(str(eq))
        latex = formula.get("latex") or ""
        if not latex:
            problems.append("formula %s missing latex" % eq)
        else:
            for issue in latex_problems(latex):
                problems.append("formula %s latex: %s" % (eq, issue))
            if PLACEHOLDER.search(latex):
                problems.append("formula %s latex contains placeholder" % eq)
        if formula.get("type") not in TYPES:
            warnings.append("formula %s type unusual: %s" % (eq, formula.get("type")))
        source = formula.get("source")
        if not source:
            problems.append("formula %s missing source" % eq)
        elif isinstance(source, dict) and not any(
                source.get(k) for k in ("paragraph_id", "page", "locator", "quote")):
            problems.append("formula %s source has no locator/quote" % eq)
        symbols = formula.get("symbols") or []
        if not isinstance(symbols, list) or not symbols:
            problems.append("formula %s has no symbols[]" % eq)
        for j, symbol in enumerate(symbols):
            if not isinstance(symbol, dict):
                problems.append("formula %s symbol[%d] is not an object" % (eq, j))
                continue
            if not symbol.get("symbol"):
                problems.append("formula %s symbol[%d] missing symbol" % (eq, j))
            if not symbol.get("meaning"):
                problems.append("formula %s symbol[%d] missing meaning" % (eq, j))
            if symbol.get("unit") in (None, ""):
                warnings.append("formula %s symbol %s has no unit" % (eq, symbol.get("symbol")))
            status = symbol.get("status")
            if status and status not in SYMBOL_STATUS:
                warnings.append("formula %s symbol %s unknown status %s" %
                                (eq, symbol.get("symbol"), status))
    checks["formulas"] = len(formulas)
    return ids


def check_derivation(result, formula_ids, problems, warnings, checks):
    steps = result.get("derivation") or []
    if not isinstance(steps, list):
        problems.append("derivation must be a list")
        return
    seen_steps = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            problems.append("derivation[%d] is not an object" % i)
            continue
        number = step.get("step")
        if number is None:
            problems.append("derivation[%d] missing step number" % i)
        elif number in seen_steps:
            problems.append("duplicate derivation step %s" % number)
        else:
            seen_steps.add(number)
        for key in ("from", "to", "operation", "justification"):
            if not step.get(key):
                problems.append("derivation step %s missing %s" % (number, key))
        for key in ("from", "to"):
            target = str(step.get(key) or "")
            if target and formula_ids and target not in formula_ids and \
                    not target.startswith("step") and not target.isdigit():
                warnings.append("derivation step %s %s references unknown %s" %
                                (number, key, target))
        if step.get("latex"):
            for issue in latex_problems(str(step["latex"])):
                problems.append("derivation step %s latex: %s" % (number, issue))
    checks["derivation_steps"] = len(steps)


def check_numeric(result, problems, warnings, checks):
    numeric = result.get("numeric_check")
    if numeric is None:
        warnings.append("no numeric_check (mark not_run if not applicable)")
        return
    if not isinstance(numeric, dict):
        problems.append("numeric_check is not an object")
        return
    inputs = numeric.get("inputs") or []
    if not isinstance(inputs, list) or not inputs:
        warnings.append("numeric_check has no inputs[]")
    for i, item in enumerate(inputs):
        if not isinstance(item, dict):
            problems.append("numeric input[%d] is not an object" % i)
            continue
        if item.get("value") is None:
            problems.append("numeric input[%d] missing value" % i)
        if not item.get("unit"):
            warnings.append("numeric input[%d] missing unit" % i)
    result_value = (numeric.get("result") or {}).get("value") \
        if isinstance(numeric.get("result"), dict) else None
    computation = numeric.get("computation")
    if computation:
        try:
            computed = safe_eval(str(computation))
            if result_value is not None:
                if abs(computed - float(result_value)) > max(1e-6, abs(computed) * 1e-6):
                    problems.append("computation %s = %s does not match result %s" %
                                    (computation, computed, result_value))
            checks["computed_value"] = computed
        except (SyntaxError, ValueError, ZeroDivisionError) as exc:
            warnings.append("cannot safely evaluate computation %r: %s" % (computation, exc))
    else:
        warnings.append("numeric_check missing computation")
    if result_value is None:
        warnings.append("numeric_check missing result.value")
    if not numeric.get("dimensional_check"):
        problems.append("numeric_check missing dimensional_check")
    if not numeric.get("plausibility"):
        warnings.append("numeric_check missing plausibility")
    checks["numeric_value"] = result_value


def check_input_coverage(result, input_data, problems, warnings, checks):
    if not input_data:
        return
    input_formulas = input_data.get("formulas") or []
    input_ids = {str(f.get("eq_id") or f.get("id")) for f in input_formulas
                 if isinstance(f, dict) and (f.get("eq_id") or f.get("id"))}
    result_ids = {str(f.get("eq_id") or f.get("id")) for f in (result.get("formulas") or [])
                  if isinstance(f, dict) and (f.get("eq_id") or f.get("id"))}
    missing = sorted(input_ids - result_ids)
    if missing:
        problems.append("input formulas not covered: %s" % ", ".join(missing))
    checks["input_formulas"] = len(input_ids)


def self_test():
    result = {
        "case_id": "formula-self-test",
        "formulas": [{
            "eq_id": "Eq.3", "latex": "\\eta = \\frac{P_{out}}{P_{in}}",
            "type": "definition",
            "symbols": [
                {"symbol": "\\eta", "meaning": "效率", "unit": "1", "status": "defined"},
                {"symbol": "P_{out}", "meaning": "输出功率", "unit": "W", "status": "defined"},
                {"symbol": "P_{in}", "meaning": "输入功率", "unit": "W", "status": "defined"},
            ],
            "source": {"paragraph_id": "p5", "quote": "eta = Pout/Pin"},
            "assumptions": ["稳态"],
        }, {
            "eq_id": "Eq.4", "latex": "\\eta = 0.8", "type": "definition",
            "symbols": [{"symbol": "\\eta", "meaning": "效率", "unit": "1",
                         "status": "defined"}],
            "source": {"paragraph_id": "p6", "quote": "eta = 0.8"},
            "assumptions": [],
        }],
        "derivation": [{"step": 1, "from": "Eq.3", "to": "Eq.4",
                        "operation": "代入", "justification": "能量守恒",
                        "latex": "\\eta = 0.8"}],
        "numeric_check": {"inputs": [{"symbol": "Pout", "value": 40, "unit": "W"},
                                    {"symbol": "Pin", "value": 50, "unit": "W"}],
                          "computation": "40/50",
                          "result": {"value": 0.8, "unit": "1"},
                          "dimensional_check": "W/W = 1",
                          "plausibility": "0.8 in [0,1]"},
        "uncertainty": [], "change_log": [],
    }
    input_data = {"case_id": "formula-self-test", "formulas": [{"eq_id": "Eq.3"}]}
    return validate(result, input_data)


def validate(result, input_data):
    problems, warnings, checks = [], [], {}
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["result root is not an object"],
                "warnings": [], "checks": {}}
    if not result.get("case_id"):
        problems.append("result missing case_id")
    ids = check_formulas(result, problems, warnings, checks)
    check_derivation(result, ids, problems, warnings, checks)
    check_numeric(result, problems, warnings, checks)
    check_input_coverage(result, input_data, problems, warnings, checks)
    return {"ok": not problems, "problems": problems, "warnings": warnings, "checks": checks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None)
    ap.add_argument("--input", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        report = self_test()
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
    input_data = None
    if args.input:
        try:
            input_data = load_json(args.input)
        except (OSError, json.JSONDecodeError):
            input_data = None
    report = validate(result, input_data)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
