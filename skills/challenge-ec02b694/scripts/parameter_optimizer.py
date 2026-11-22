#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Constraint-aware parameter analysis for the experiment parameter-optimization skill.

The script reads real CSV/JSON material under ``--input``, identifies parameter
and response columns, applies explicit/derived hard constraints, computes
descriptive sensitivity and trend statistics, and proposes a reproducible
recommendation interval plus follow-up validation runs.  It never invents data:
every number comes from the input tables, and every recommendation points back
to supporting rows or constraint lines.

Outputs (under ``--output``):
    parameter-analysis.json   machine-readable analysis and evidence locators
    parameter-analysis.md     human-readable report

Exit contract: 0 whenever the analysis JSON was written (even when the data are
insufficient and the status is ``not_computable``); 2 only if the output file
could not be written at all.

Usage:
    python3 parameter_optimizer.py --input /input --output /app/output
    python3 parameter_optimizer.py --input /input --output /app/output \
        --parameters temperature_C,time_min --responses yield_pct,impurity_pct \
        --objective yield_pct:max
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"
MAX_DATA_BYTES = 5 * 1024 * 1024
MAX_EVIDENCE = 40
MAX_TEXT_MATERIALS = 60

NULLS = {"", "-", "--", "na", "n/a", "nan", "none", "null", "缺测", "无"}
KNOWN_UNITS = {
    "c", "°c", "℃", "k", "min", "mins", "h", "hr", "hrs", "s", "sec",
    "wt", "wt%", "pct", "%", "vol%", "mol%", "cny", "rmb", "usd", "eur",
    "mpa", "kpa", "bar", "atm", "rpm", "ml", "l", "ul", "µl", "μl",
    "mg", "g", "kg", "mol", "mmol", "m", "mm", "nm", "µm", "μm",
    "v", "mv", "a", "ma", "w", "kw", "hz", "khz", "sccm", "slm",
}
ID_HINTS = {"id", "run", "run_id", "sample", "sample_id", "batch", "lot",
            "index", "no", "编号", "序号", "批次"}
PARAM_HINTS = {
    "temperature", "temp", "time", "duration", "residence", "pressure",
    "concentration", "conc", "loading", "catalyst", "ph", "ratio", "flow",
    "rate", "rpm", "speed", "dosage", "dose", "amount", "voltage", "current",
    "power", "volume", "size", "humidity", "stirring", "heating", "molarity",
    "equivalents", "equiv", "temperature_c", "time_min", "loading_wt",
    "温度", "时间", "压力", "浓度", "负载", "载量", "流量", "转速", "搅拌",
    "电压", "电流", "功率", "体积", "粒径", "湿度", "配比", "当量", "滴速",
}
STRONG_PARAM = {"temperature", "temp", "time", "duration", "residence",
                "pressure", "loading", "catalyst", "stirring", "rpm",
                "温度", "时间", "压力", "负载", "转速", "搅拌"}
RESPONSE_HINTS = {
    "yield", "purity", "impurity", "conversion", "selectivity", "recovery",
    "score", "cost", "time", "rate", "ee", "titer", "output", "response",
    "result", "performance", "quality", "efficiency", "throughput", "loss",
    "产率", "收率", "纯度", "杂质", "转化率", "选择性", "回收率", "得分",
    "成本", "费用", "效率", "通量", "损耗",
}
STRONG_RESPONSE = {"yield", "purity", "impurity", "conversion", "selectivity",
                   "recovery", "score", "cost", "ee", "titer",
                   "产率", "收率", "纯度", "杂质", "转化率", "选择性", "成本"}
OBJECTIVE_HINTS = {"yield", "conversion", "selectivity", "score", "purity",
                   "recovery", "titer", "ee", "efficiency", "performance",
                   "产率", "收率", "转化率", "选择性", "纯度", "回收率", "得分",
                   "效率"}
COST_HINTS = {"cost", "price", "expense", "budget", "费用", "成本", "价格", "预算"}

SYNONYMS = {
    "temperature": ["temperature", "temp", "温度"],
    "time": ["residence", "time", "duration", "时间", "停留"],
    "loading": ["loading", "catalyst", "催化剂", "装载", "载量"],
    "impurity": ["impurity", "杂质"],
    "purity": ["purity", "纯度"],
    "yield": ["yield", "产率", "收率"],
    "conversion": ["conversion", "转化率"],
    "selectivity": ["selectivity", "选择性"],
    "recovery": ["recovery", "回收率"],
    "cost": ["cost", "price", "expense", "费用", "成本", "价格"],
    "pressure": ["pressure", "压力"],
    "ph": ["ph", "酸碱"],
    "flow": ["flow", "流量"],
    "rpm": ["rpm", "转速", "stirring", "搅拌"],
    "score": ["score", "得分", "评分"],
    "ee": ["ee", "enantiomeric", "对映"],
}

SKIP_JSON_KEYS = {
    "output_contract", "evidence", "uncertainties", "artifacts", "claims",
    "preserved_facts", "inventory", "documents", "parameter_space",
    "constraints", "hard_constraints", "metadata", "meta", "schema",
    "work_template", "example", "schema_properties", "mode_metrics",
    "change_log", "evidence_map", "result", "task", "query", "queries",
    "resources", "prices", "price_list", "bookings", "booking", "contracts",
    "contract", "quotations", "quotes", "budget", "cost_model", "rate_card",
    "vendors", "suppliers", "availability", "catalog",
}
FLATTEN_KEYS = {"parameters", "params", "settings", "inputs", "factors",
                "variables", "conditions", "config", "configuration"}
PLAN_ID_KEYS = {"plan", "plan_id", "candidate", "candidate_id", "version",
                "option", "option_id", "方案", "候选"}
CONTRACT_NAME_HINTS = {"price", "prices", "price_list", "quotation", "quote",
                       "quotes", "resource", "resources", "booking",
                       "bookings", "budget", "cost_model", "rate_card",
                       "vendor", "vendors", "supplier", "suppliers",
                       "contract", "contracts", "availability", "catalog",
                       "报价", "价格", "资源", "预约", "预算"}


def looks_like_contract_table(path):
    tokens = set(key_norm(Path(str(path)).stem).split("_"))
    return bool(tokens & CONTRACT_NAME_HINTS)


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------

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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                               allow_nan=False) + "\n", encoding="utf-8")
    return path


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def key_norm(text):
    return re.sub(r"[^0-9a-z一-鿿]+", "_", str(text or "").lower()).strip("_")


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def num_or_none(value):
    """Coerce a cell to float without guessing ambiguous multi-number fields."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if finite(value) else None
    text = str(value).strip()
    if not text or text.lower() in NULLS:
        return None
    cleaned = text.replace(",", "")
    matches = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", cleaned)
    if len(matches) != 1:
        return None
    try:
        number = float(matches[0])
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def fmt(value):
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if not finite(value):
            return "n/a"
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return ("%.4g" % value)
    return str(value)


def split_column(column):
    """Return (base_name, unit) for names such as ``yield_pct``/``T (C)``."""
    name = str(column or "").strip()
    match = re.match(r"^(?P<base>.+?)\s*[\(\[]\s*(?P<unit>[^\)\]]+?)\s*[\)\]]\s*$", name)
    if match:
        return match.group("base").strip(), match.group("unit").strip()
    match = re.match(r"^(?P<base>.+?)[_\- ](?P<unit>[A-Za-z%°℃/]+)$", name)
    if match and match.group("unit").lower() in KNOWN_UNITS:
        return match.group("base").strip(), match.group("unit").strip()
    return name, ""


def base_norm(column):
    return key_norm(split_column(column)[0])


def hint_match(column, hints):
    base = base_norm(column)
    full = key_norm(column)
    for hint in hints:
        hint_n = key_norm(hint)
        if not hint_n:
            continue
        if hint_n == base or hint_n == full:
            return True
        if len(hint_n) >= 3 and (hint_n in base or base in hint_n):
            return True
    return False


def units_compatible(left, right):
    if not left or not right:
        return True
    a, b = left.lower().replace(" ", ""), right.lower().replace(" ", "")
    temperature = {"c", "°c", "℃", "k"}
    fraction = {"%", "pct", "wt%", "wt", "vol%", "mol%"}
    if a in temperature and b in temperature:
        return True
    if a in fraction and b in fraction:
        return True
    return a == b


def column_unit(column):
    return split_column(column)[1]


def q(value):
    """Format a cell for an evidence quote (CSV text inside finalize uses tabs)."""
    if value is None:
        return ""
    if isinstance(value, float):
        return fmt(value)
    return str(value).strip()


def dedupe(items, key_fields):
    seen, out = set(), []
    for item in items:
        key = tuple(str(item.get(field) or "") for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# input scanning and table loading
# ---------------------------------------------------------------------------

def scan_files(input_dir):
    input_dir = Path(input_dir)
    rows = []
    if not input_dir.is_dir():
        return rows
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(input_dir).as_posix()
        suffix = path.suffix.lower()
        size = path.stat().st_size
        if suffix == ".csv":
            kind = "csv"
        elif suffix == ".json":
            kind = "json"
        elif suffix in (".md", ".txt", ".rst", ".tex", ".yaml", ".yml"):
            kind = "text"
        else:
            kind = "other"
        rows.append({"path": rel, "suffix": suffix, "kind": kind,
                     "bytes": size, "name": path.name})
    return rows


def load_csv_table(path, rel):
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return {"path": rel, "kind": "csv", "columns": [], "rows": [],
                "warnings": ["unreadable: %s" % exc]}
    if not text.strip():
        return {"path": rel, "kind": "csv", "columns": [], "rows": [],
                "warnings": ["empty file"]}
    lines = text.splitlines()
    sample = lines[0] if lines else ""
    if "\t" in sample:
        delimiter = "\t"
    elif ";" in sample and "," not in sample:
        delimiter = ";"
    else:
        delimiter = ","
    parsed = []
    warnings = []
    try:
        parsed = list(csv.reader(lines, delimiter=delimiter))
    except csv.Error as exc:
        return {"path": rel, "kind": "csv", "columns": [], "rows": [],
                "warnings": ["csv parse failed: %s" % exc]}
    if not parsed:
        return {"path": rel, "kind": "csv", "columns": [], "rows": [],
                "warnings": ["empty file"]}
    header = [norm(cell) for cell in parsed[0]]
    if len([cell for cell in header if cell]) < 2:
        return {"path": rel, "kind": "csv", "columns": [], "rows": [],
                "warnings": ["header has fewer than two named columns"]}
    columns = [cell or "column_%d" % (index + 1) for index, cell in enumerate(header)]
    rows = []
    for index, cells in enumerate(parsed[1:], 1):
        if not any(norm(cell) for cell in cells):
            continue
        padded = list(cells) + [""] * max(0, len(columns) - len(cells))
        raw = {col: padded[pos] if pos < len(padded) else ""
               for pos, col in enumerate(columns)}
        # finalize renders CSV material as tab-joined cells; quote that form
        # so the verbatim evidence check can find the row.
        quote = "\t".join(q(raw.get(col)) for col in columns)
        rows.append({"locator": "L%d" % (index + 1), "row": index,
                     "raw": raw, "quote": quote, "pointer": "",
                     "source": rel})
    return {"path": rel, "kind": "csv", "columns": columns, "rows": rows,
            "warnings": warnings,
            "record_type": ("contract_table" if looks_like_contract_table(rel)
                            else "measurements")}


def _flatten_json_row(item):
    """Flatten one level of parameter/settings objects and 'k=v; k=v' strings."""
    flat = {}
    for key, value in item.items():
        key_text = str(key)
        lowered = key_text.lower()
        if isinstance(value, dict) and lowered in FLATTEN_KEYS:
            for inner_key, inner_value in value.items():
                if isinstance(inner_value, dict):
                    number = num_or_none(inner_value.get("value"))
                    if number is not None:
                        flat[str(inner_key)] = number
                elif not isinstance(inner_value, list):
                    flat[str(inner_key)] = inner_value
        elif isinstance(value, str) and lowered in FLATTEN_KEYS:
            for name, number in re.findall(
                    r"([A-Za-z][A-Za-z0-9 _%-]{0,40}?)\s*[=:]\s*"
                    r"([-+]?\d+(?:\.\d+)?)", value):
                flat[name.strip()] = number
        elif not isinstance(value, (dict, list)):
            flat[key_text] = value
    return flat


def _json_rows(value, rel, pointer, out, depth=0):
    if depth > 7:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in SKIP_JSON_KEYS:
                continue
            _json_rows(item, rel, "%s.%s" % (pointer, key), out, depth + 1)
        return
    if not isinstance(value, list) or not value:
        return
    dicts = [item for item in value if isinstance(item, dict)]
    if dicts and len(dicts) == len(value):
        flat_rows = [_flatten_json_row(item) for item in dicts]
        columns = []
        for flat in flat_rows:
            for key in flat:
                if key not in columns:
                    columns.append(key)
        has_number = any(num_or_none(flat.get(col)) is not None
                         for flat in flat_rows for col in columns)
        if len(columns) >= 2 and has_number:
            rows = []
            for index, flat in enumerate(flat_rows):
                raw = {col: flat.get(col) for col in columns}
                rows.append({"locator": "%s[%d]" % (pointer or "$", index),
                             "row": index + 1, "raw": raw, "quote": "",
                             "pointer": "%s[%d]" % (pointer or "$", index),
                             "source": rel})
            if looks_like_contract_table(rel):
                record_type = "contract_table"
            elif any(key_norm(col) in PLAN_ID_KEYS for col in columns):
                record_type = "candidate_plans"
            else:
                record_type = "measurements"
            out.append({"path": rel, "kind": "json", "columns": columns,
                        "rows": rows, "warnings": [],
                        "record_type": record_type})
            return
    for index, item in enumerate(value[:200]):
        _json_rows(item, rel, "%s[%d]" % (pointer or "$", index), out, depth + 1)


def load_json_tables(path, rel):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [{"path": rel, "kind": "json", "columns": [], "rows": [],
                 "warnings": ["unreadable JSON: %s" % exc]}]
    out = []
    _json_rows(data, rel, "$", out)
    return out


# ---------------------------------------------------------------------------
# column classification
# ---------------------------------------------------------------------------

def resolve_column(name, columns):
    raw = str(name or "").strip()
    if not raw:
        return None
    for column in columns:
        if raw == column:
            return column
    target = key_norm(raw)
    for column in columns:
        if key_norm(column) == target:
            return column
    for column in columns:
        if base_norm(column) == target:
            return column
    matches = [column for column in columns
               if target and (target in key_norm(column) or key_norm(column) in target)]
    return matches[0] if len(matches) == 1 else None


def _names_from_value(value, keys):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                for key in keys:
                    if item.get(key) not in (None, ""):
                        out.append(str(item[key]))
                        break
        return out
    if isinstance(value, dict):
        out = []
        for key, item in value.items():
            if isinstance(item, dict):
                for inner in keys:
                    if item.get(inner) not in (None, ""):
                        out.append(str(item[inner]))
                        break
                else:
                    out.append(str(key))
            else:
                out.append(str(key))
        return out
    return []


def case_column_names(case, keys):
    for key in keys:
        if key in case:
            names = _names_from_value(case.get(key), ("column", "field", "name",
                                                      "parameter", "metric", "key"))
            if names:
                return names, "case.json:%s" % key
    return [], ""


def classify_columns(columns, case, args):
    all_params, all_responses = [], []
    sources = {}
    cli_params = [x.strip() for x in (args.parameters or "").split(",") if x.strip()]
    cli_responses = [x.strip() for x in (args.responses or "").split(",") if x.strip()]
    explicit_params = bool(cli_params)
    explicit_responses = bool(cli_responses)
    missing = []
    if cli_params:
        resolved, absent = [], []
        for name in cli_params:
            column = resolve_column(name, columns)
            (resolved if column else absent).append(column or name)
        all_params = resolved
        missing.extend(absent)
        sources.update({column: "cli:--parameters" for column in resolved})
    else:
        names, source = case_column_names(
            case, ("parameter_columns", "parameters", "factors",
                   "independent_variables", "inputs", "parameter_space"))
        for name in names:
            column = resolve_column(name, columns)
            if column and column not in all_params:
                all_params.append(column)
                sources[column] = source
        if names:
            missing.extend(name for name in names if not resolve_column(name, columns))
    if cli_responses:
        resolved, absent = [], []
        for name in cli_responses:
            column = resolve_column(name, columns)
            (resolved if column else absent).append(column or name)
        all_responses = resolved
        missing.extend(absent)
        sources.update({column: "cli:--responses" for column in resolved})
    else:
        names, source = case_column_names(
            case, ("response_columns", "responses", "metrics", "outputs",
                   "dependent_variables", "objectives", "target_columns"))
        for name in names:
            column = resolve_column(name, columns)
            if column and column not in all_responses:
                all_responses.append(column)
                sources[column] = source
        if names:
            missing.extend(name for name in names if not resolve_column(name, columns))

    used = set(all_params) | set(all_responses)
    index_like = [column for column in columns
                  if key_norm(column) in ID_HINTS or base_norm(column) in ID_HINTS]
    if not all_params and not explicit_params:
        for column in columns:
            if column in used or column in index_like:
                continue
            if hint_match(column, PARAM_HINTS):
                all_params.append(column)
                sources[column] = "heuristic:parameter-name"
                used.add(column)
    if not all_responses and not explicit_responses:
        for column in columns:
            if column in used or column in index_like:
                continue
            if hint_match(column, RESPONSE_HINTS) and column not in all_params:
                all_responses.append(column)
                sources[column] = "heuristic:response-name"
                used.add(column)
    # A column that matches both hint sets is a parameter only if it is a
    # strong parameter term and not a strong response term.
    for column in list(all_responses):
        if column in all_params and hint_match(column, RESPONSE_HINTS):
            if hint_match(column, STRONG_RESPONSE) and not hint_match(column, STRONG_PARAM):
                all_params.remove(column)
            else:
                all_responses.remove(column)
    unclassified = [column for column in columns
                    if column not in all_params and column not in all_responses
                    and column not in index_like]
    return {
        "parameters": all_params,
        "responses": all_responses,
        "index_like": index_like,
        "unclassified": unclassified,
        "sources": sources,
        "missing_columns": sorted(set(missing)),
        "heuristic": any(str(value).startswith("heuristic:")
                         for value in sources.values()),
    }


def parse_objective_value(raw):
    if isinstance(raw, dict):
        column = (raw.get("column") or raw.get("metric") or raw.get("name")
                  or raw.get("field") or raw.get("target"))
        direction = str(raw.get("direction") or raw.get("goal")
                        or raw.get("sense") or "max").lower()
        return str(column or ""), direction
    if isinstance(raw, str):
        text = raw.strip()
        if ":" in text:
            left, right = text.rsplit(":", 1)
            return left.strip(), right.strip().lower()
        lowered = text.lower()
        direction = "min" if re.search(r"\bmin|最小|降低|减少", lowered) else "max"
        return text, direction
    return "", "max"


def map_phrase_to_column(phrase, candidates):
    low = str(phrase or "").lower()
    if not low:
        return None
    scored = []
    for column in candidates:
        base = base_norm(column)
        words = SYNONYMS.get(base, [base])
        for word in words:
            if word and word.lower() in low:
                scored.append((len(word), column))
                break
        else:
            if base and (base in low or (len(low) >= 3 and low in base)):
                scored.append((len(base), column))
    if not scored:
        return None
    scored.sort(reverse=True)
    best_len = scored[0][0]
    best = [column for length, column in scored if length == best_len]
    return best[0] if len(best) == 1 else None


def find_objective(case, materials, responses, all_columns, args):
    if args.objective:
        column_name, direction = parse_objective_value(args.objective)
        column = resolve_column(column_name, all_columns)
        if column:
            return {"column": column, "direction": direction,
                    "source": "cli:--objective", "needs_review": False}
        return {"column": None, "direction": direction,
                "source": "cli:--objective",
                "missing_column": column_name, "needs_review": False}
    for key in ("objective", "target", "target_metric", "optimization_objective",
                "objective_column"):
        if key in case:
            column_name, direction = parse_objective_value(case.get(key))
            column = resolve_column(column_name, all_columns)
            if column:
                return {"column": column, "direction": direction,
                        "source": "case.json:%s" % key, "needs_review": False}
    max_re = re.compile(
        r"(?i)\bmaximi[sz]e\s+([A-Za-z][A-Za-z0-9 _-]{0,40})")
    min_re = re.compile(
        r"(?i)\bminimi[sz]e\s+([A-Za-z][A-Za-z0-9 _-]{0,40})")
    zh_max_re = re.compile(
        r"(?:最大化|提高|提升|增大)\s*([一-鿿A-Za-z0-9 _()%-]{1,30})")
    zh_min_re = re.compile(
        r"(?:最小化|降低|减少)\s*([一-鿿A-Za-z0-9 _()%-]{1,30})")
    text_candidates = []
    for material in materials:
        if material["path"].lower().endswith("task.md"):
            continue
        for line_no, line in enumerate(material["text"].splitlines(), 1):
            for regex, direction in ((max_re, "max"), (min_re, "min"),
                                     (zh_max_re, "max"), (zh_min_re, "min")):
                match = regex.search(line)
                if not match:
                    continue
                phrase = match.group(1).strip().split("(")[0].strip()
                phrase = re.split(r"\s+(?:while|with|subject|under|and|keeping|at|to)\b",
                                  phrase, maxsplit=1)[0]
                column = map_phrase_to_column(phrase, responses or all_columns)
                if column:
                    text_candidates.append({
                        "column": column, "direction": direction,
                        "source": "text", "path": material["path"],
                        "locator": "L%d" % line_no,
                        "quote": line.strip(), "needs_review": False,
                    })
                    if len(text_candidates) >= 10:
                        break
            if len(text_candidates) >= 10:
                break
        if len(text_candidates) >= 10:
            break
    if text_candidates:
        first = dict(text_candidates[0])
        distinct = {(item["column"], item["direction"])
                    for item in text_candidates}
        first["candidates"] = text_candidates[:5]
        if len(distinct) > 1:
            first["needs_review"] = True
            first["ambiguity"] = (
                "multiple maximize/minimize statements were found; "
                "rerun with --objective or state the priority explicitly")
        return first
    for column in responses:
        if hint_match(column, OBJECTIVE_HINTS):
            return {"column": column, "direction": "max",
                    "source": "default:first-objective-like-response",
                    "needs_review": True}
    if responses:
        return {"column": responses[0], "direction": "max",
                "source": "default:first-response", "needs_review": True}
    return {"column": None, "direction": "max", "source": "none",
            "needs_review": True}


# ---------------------------------------------------------------------------
# constraints
# ---------------------------------------------------------------------------

def normalize_op(raw):
    text = str(raw or "").strip().lower()
    aliases = {
        "<=": "max", "<": "max", "≤": "max", "max": "max", "at most": "max",
        "no more than": "max", "at or below": "max", "<=": "max",
        ">=": "min", ">": "min", "≥": "min", "min": "min", "at least": "min",
        "no less than": "min", "==": "eq", "=": "eq", "eq": "eq",
    }
    return aliases.get(text, "max")


def constraint_item(column, op, value, **extra):
    item = {"column": column, "op": normalize_op(op), "value": value}
    item.update(extra)
    item.setdefault("kind", "hard")
    item.setdefault("source", "unspecified")
    item.setdefault("needs_review", False)
    return item


def structured_constraints(case):
    items, notes = [], []
    raw = None
    for key in ("constraints", "hard_constraints", "parameter_domains", "domains"):
        if key in case:
            raw = case.get(key)
            break
    space = case.get("parameter_space")
    if isinstance(space, list):
        for entry in space:
            if not isinstance(entry, dict):
                continue
            column = entry.get("column") or entry.get("name") or entry.get("field")
            bounds = entry.get("range") or entry.get("bounds")
            if column is None or not (isinstance(bounds, list) and len(bounds) == 2):
                continue
            low, high = num_or_none(bounds[0]), num_or_none(bounds[1])
            for op, value in (("min", low), ("max", high)):
                if value is not None:
                    items.append(constraint_item(
                        str(column), op, value, kind="domain",
                        source="case.json:parameter_space",
                        needs_review=True))
    if raw is None:
        return items, notes
    if isinstance(raw, list):
        for index, entry in enumerate(raw):
            if isinstance(entry, dict):
                column = (entry.get("column") or entry.get("parameter")
                          or entry.get("metric") or entry.get("name")
                          or entry.get("field"))
                value = entry.get("value")
                op = entry.get("op") or entry.get("operator")
                range_value = None
                for key in ("range", "bounds", "domain", "interval"):
                    candidate = entry.get(key)
                    if isinstance(candidate, list) and len(candidate) == 2:
                        range_value = candidate
                        break
                if value is None and range_value is not None:
                    if column is None:
                        notes.append("domain entry %d has no column name" % (index + 1))
                        continue
                    low, high = num_or_none(range_value[0]), num_or_none(range_value[1])
                    if low is not None:
                        items.append(constraint_item(
                            str(column), "min", low, kind="domain",
                            source=str(entry.get("source") or "case.json:domain"),
                            locator=str(entry.get("locator") or ""),
                            quote=str(entry.get("quote") or "")))
                    if high is not None:
                        items.append(constraint_item(
                            str(column), "max", high, kind="domain",
                            source=str(entry.get("source") or "case.json:domain"),
                            locator=str(entry.get("locator") or ""),
                            quote=str(entry.get("quote") or "")))
                    if low is None and high is None:
                        notes.append("domain for %s has no numeric bounds" % column)
                    continue
                if value is None:
                    for key, op_name in (("max", "max"), ("maximum", "max"),
                                         ("upper", "max"), ("min", "min"),
                                         ("minimum", "min"), ("lower", "min")):
                        if entry.get(key) is not None:
                            value = entry.get(key)
                            op = op_name
                            break
                if column is not None and num_or_none(value) is not None:
                    items.append(constraint_item(
                        str(column), op, num_or_none(value),
                        kind=str(entry.get("kind") or "hard"),
                        source=str(entry.get("source") or "case.json:constraints"),
                        locator=str(entry.get("locator") or ""),
                        quote=str(entry.get("quote") or ""),
                        needs_review=bool(entry.get("needs_review", False))))
                else:
                    notes.append("constraint %d is not numeric/complete" % (index + 1))
            elif isinstance(entry, str):
                notes.append("string constraint left for text extraction: %s" % entry)
    elif isinstance(raw, dict):
        for column, spec in raw.items():
            if isinstance(spec, dict):
                range_value = None
                for key in ("range", "bounds", "domain", "interval"):
                    candidate = spec.get(key)
                    if isinstance(candidate, list) and len(candidate) == 2:
                        range_value = candidate
                        break
                if range_value is not None:
                    low, high = num_or_none(range_value[0]), num_or_none(range_value[1])
                    if low is not None:
                        items.append(constraint_item(
                            str(column), "min", low, kind="domain",
                            source=str(spec.get("source") or "case.json:domain")))
                    if high is not None:
                        items.append(constraint_item(
                            str(column), "max", high, kind="domain",
                            source=str(spec.get("source") or "case.json:domain")))
                    continue
                for key, op_name in (("max", "max"), ("maximum", "max"),
                                     ("upper", "max"), ("min", "min"),
                                     ("minimum", "min"), ("lower", "min")):
                    if spec.get(key) is not None and num_or_none(spec.get(key)) is not None:
                        items.append(constraint_item(
                            str(column), op_name, num_or_none(spec.get(key)),
                            kind=str(spec.get("kind") or "hard"),
                            source=str(spec.get("source") or "case.json:constraints"),
                            locator=str(spec.get("locator") or ""),
                            quote=str(spec.get("quote") or "")))
            elif isinstance(spec, list) and len(spec) == 2:
                low, high = num_or_none(spec[0]), num_or_none(spec[1])
                if low is not None:
                    items.append(constraint_item(
                        str(column), "min", low, kind="domain",
                        source="case.json:domain"))
                if high is not None:
                    items.append(constraint_item(
                        str(column), "max", high, kind="domain",
                        source="case.json:domain"))
            elif num_or_none(spec) is not None:
                notes.append("bare numeric constraint for %s is ambiguous; "
                             "expected {max/min: value}" % column)
    return items, notes


TEMP_MAX_RES = [
    (re.compile(
        r"(?i)(?:reactor|equipment|device|instrument|仪器|设备)[^.;\n]{0,80}?"
        r"(?:maximum|max\.?|最高|最大)[^.;\n]{0,30}?(?:temperature|温度)"
        r"[^.;\n]{0,20}?(?:is|of|为)?\s*([-+]?\d+(?:\.\d+)?)\s*"
        r"(?:°\s*C|℃|(?<![A-Za-z])C(?![A-Za-z]))"),
     "equipment temperature limit"),
    (re.compile(
        r"(?i)(?:gasket|seal|material|垫片|密封|材料)[^.;\n]{0,80}?"
        r"(?:rated\s+to|rated|耐温|耐受|最高)[^.;\n]{0,20}?"
        r"([-+]?\d+(?:\.\d+)?)\s*"
        r"(?:°\s*C|℃|(?<![A-Za-z])C(?![A-Za-z]))"),
     "material temperature limit"),
]
METRIC_MAX_RE = re.compile(
    r"(?i)\b([A-Za-z][A-Za-z0-9 _-]{0,40}?)\s+"
    r"(?:must|shall|should)\s+(?:stay|remain|be)?\s*"
    r"(?:at\s+or\s+below|below|under|at\s+most|no\s+more\s+than|<=|≤)\s*"
    r"([-+]?\d+(?:\.\d+)?)\s*(%|°\s*C|℃|CNY|RMB|USD|EUR)?")
METRIC_MIN_RE = re.compile(
    r"(?i)\b([A-Za-z][A-Za-z0-9 _-]{0,40}?)\s+"
    r"(?:must|shall|should)\s+(?:stay|remain|be)?\s*"
    r"(?:at\s+or\s+above|above|at\s+least|no\s+less\s+than|>=|≥)\s*"
    r"([-+]?\d+(?:\.\d+)?)\s*(%|°\s*C|℃|CNY|RMB|USD|EUR)?")
COST_CAP_RE = re.compile(
    r"(?i)\b(?:cost|budget|费用|成本)\s+(?:cap|limit|ceiling|上限)\s*"
    r"(?:is|of|为)?\s*([-+]?\d+(?:\.\d+)?)\s*(CNY|RMB|USD|EUR|元)?")
RUNS_RE = re.compile(
    r"(?i)(?:at\s+most|no\s+more\s+than)\s+(\d+)\s+(?:validation\s+)?runs?"
    r"|(?:最多|不超过)\s*(\d+)\s*(?:次|轮)")
ZH_TEMP_RE = re.compile(
    r"(?:最高温度|温度上限|耐温)[^。\n]{0,20}?([-+]?\d+(?:\.\d+)?)\s*(?:°C|℃)")
ZH_METRIC_MAX_RE = re.compile(
    r"(杂质|纯度|产率|收率|转化率|选择性|成本|费用)[^。\n]{0,12}?"
    r"(?:不超过|不高于|≤|低于|最多为)\s*([-+]?\d+(?:\.\d+)?)\s*(%|元|CNY|RMB)?")


def extract_text_constraints(materials):
    items = []
    for material in materials[:MAX_TEXT_MATERIALS]:
        rel = material["path"]
        if rel.lower().endswith("task.md"):
            continue
        for line_no, line in enumerate(material["text"].splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            found = []
            for regex, kind in TEMP_MAX_RES:
                for match in regex.finditer(stripped):
                    found.append(("temperature", "max", match.group(1),
                                  kind, match.group(0)))
            match = METRIC_MAX_RE.search(stripped)
            if match:
                found.append((match.group(1), "max", match.group(2),
                              "text_pattern", match.group(0)))
            match = METRIC_MIN_RE.search(stripped)
            if match:
                found.append((match.group(1), "min", match.group(2),
                              "text_pattern", match.group(0)))
            match = COST_CAP_RE.search(stripped)
            if match:
                found.append(("cost", "max", match.group(1),
                              "text_pattern", match.group(0)))
            match = ZH_TEMP_RE.search(stripped)
            if match:
                found.append(("temperature", "max", match.group(1),
                              "text_pattern", match.group(0)))
            match = ZH_METRIC_MAX_RE.search(stripped)
            if match:
                found.append((match.group(1), "max", match.group(2),
                              "text_pattern", match.group(0)))
            run_match = RUNS_RE.search(stripped)
            seen = set()
            for metric, op, value, kind, snippet in found:
                number = num_or_none(value)
                if number is None:
                    continue
                key = (key_norm(metric), op, number)
                if key in seen:
                    continue
                seen.add(key)
                items.append(constraint_item(
                    metric, op, number, kind=kind, source="text_pattern",
                    path=rel, locator="L%d" % line_no, quote=stripped,
                    snippet=snippet, needs_review=True))
            if run_match:
                value = num_or_none(run_match.group(1) or run_match.group(2))
                if value is not None:
                    items.append(constraint_item(
                        "validation_runs", "max", value, kind="budget",
                        source="text_pattern", path=rel,
                        locator="L%d" % line_no, quote=stripped,
                        snippet=run_match.group(0), needs_review=True))
    return dedupe(items, ("column", "op", "value", "path", "locator"))


def map_metric(metric, unit, columns):
    low = str(metric or "").lower()
    if not low:
        return None
    candidates = []
    for column in columns:
        base = base_norm(column)
        words = SYNONYMS.get(base, [base])
        if any(word and word.lower() in low for word in words):
            if units_compatible(unit, column_unit(column)):
                candidates.append(column)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates and unit:
        same = [column for column in columns
                if units_compatible(unit, column_unit(column))]
        if len(same) == 1:
            return same[0]
    return None


def apply_constraints(items, columns, param_cols, response_cols):
    applied, unmapped = [], []
    for index, item in enumerate(items, 1):
        item = dict(item)
        item["id"] = "C%02d" % index
        column = resolve_column(item.get("column"), columns)
        if column is None and item.get("column") == "validation_runs":
            item["mapping"] = "budget"
            applied.append(item)
            continue
        if column is None:
            column = map_metric(item.get("column"), item.get("unit"), columns)
        if column is None:
            item["mapping"] = "unmapped"
            unmapped.append(item)
            continue
        item["column"] = column
        item["unit"] = column_unit(column)
        if column in param_cols:
            item["mapping"] = "parameter_bound"
        elif column in response_cols or column in columns:
            item["mapping"] = "response_check"
        else:
            item["mapping"] = "row_check"
        applied.append(item)
    return applied, unmapped


def effective_bounds(applied, param_cols):
    bounds = {}
    conflicts = []
    for column in param_cols:
        lower, upper = None, None
        lower_sources, upper_sources = [], []
        for item in applied:
            if item.get("column") != column or item.get("mapping") != "parameter_bound":
                continue
            value = item.get("value")
            if not finite(value):
                continue
            entry = {"constraint_id": item.get("id"), "value": value,
                     "kind": item.get("kind"), "source": item.get("source"),
                     "path": item.get("path"), "locator": item.get("locator"),
                     "quote": item.get("quote")}
            if item.get("op") == "max":
                if upper is None or value < upper:
                    upper = value
                upper_sources.append(entry)
            elif item.get("op") == "min":
                if lower is None or value > lower:
                    lower = value
                lower_sources.append(entry)
        if upper_sources and len({row["value"] for row in upper_sources}) > 1:
            conflicts.append({
                "column": column, "kind": "upper_bound",
                "values": upper_sources,
                "binding": min(row["value"] for row in upper_sources),
                "message": "存在多个上限；采用最严格者，较宽松的上限不能作为可执行边界。",
            })
        if lower_sources and len({row["value"] for row in lower_sources}) > 1:
            conflicts.append({
                "column": column, "kind": "lower_bound",
                "values": lower_sources,
                "binding": max(row["value"] for row in lower_sources),
                "message": "存在多个下限；采用最严格者。",
            })
        status = "bounded" if (lower is not None or upper is not None) else "observed_only"
        if lower is not None and upper is not None and lower > upper:
            status = "conflict"
        bounds[column] = {"lower": lower, "upper": upper,
                          "status": status,
                          "lower_sources": lower_sources,
                          "upper_sources": upper_sources}
    return bounds, conflicts


# ---------------------------------------------------------------------------
# statistics (descriptive only)
# ---------------------------------------------------------------------------

def rank_values(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        average = (index + end) / 2.0 + 1.0
        for pos in range(index, end + 1):
            ranks[order[pos]] = average
        index = end + 1
    return ranks


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dx = [value - mean_x for value in xs]
    dy = [value - mean_y for value in ys]
    var_x = sum(value * value for value in dx)
    var_y = sum(value * value for value in dy)
    if var_x <= 0 or var_y <= 0:
        return None
    cov = sum(a * b for a, b in zip(dx, dy))
    return cov / math.sqrt(var_x * var_y)


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    return pearson(rank_values(xs), rank_values(ys))


def ols(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((value - mean_x) ** 2 for value in xs)
    if var_x <= 0:
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = cov / var_x
    intercept = mean_y - slope * mean_x
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r2 = 0.0 if ss_tot <= 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return {"slope": slope, "intercept": intercept, "r2": r2, "n": n}


def loocv_rmse(xs, ys):
    n = len(xs)
    if n < 4:
        return None
    errors = []
    for index in range(n):
        train_x = [value for pos, value in enumerate(xs) if pos != index]
        train_y = [value for pos, value in enumerate(ys) if pos != index]
        model = ols(train_x, train_y)
        if model is None:
            return None
        predicted = model["intercept"] + model["slope"] * xs[index]
        errors.append((ys[index] - predicted) ** 2)
    return math.sqrt(sum(errors) / n)


def strength(abs_r):
    if abs_r is None:
        return "not_computable"
    if abs_r >= 0.7:
        return "strong"
    if abs_r >= 0.4:
        return "moderate"
    if abs_r >= 0.2:
        return "weak"
    return "negligible"


def pair_stats(xs, ys):
    n = len(xs)
    distinct = len({round(value, 12) for value in xs})
    result = {"n": n, "distinct_x": distinct}
    if n < 3:
        result.update({"status": "not_computable",
                       "reason": "fewer than 3 complete pairs"})
        return result
    if distinct < 2:
        result.update({"status": "not_computable",
                       "reason": "parameter has a single observed level"})
        return result
    r = pearson(xs, ys)
    rho = spearman(xs, ys)
    model = ols(xs, ys)
    if model is None:
        result.update({"status": "not_computable",
                       "reason": "zero variance in x or y"})
        return result
    span = max(xs) - min(xs)
    result.update({
        "status": "computed",
        "pearson_r": r,
        "spearman_rho": rho,
        "slope_per_unit": model["slope"],
        "intercept": model["intercept"],
        "r2": model["r2"],
        "effect_over_observed_span": model["slope"] * span,
        "loocv_rmse": loocv_rmse(xs, ys),
        "direction": ("positive" if model["slope"] > 0 else
                      "negative" if model["slope"] < 0 else "flat"),
        "strength": strength(abs(rho) if rho is not None else abs(r) if r is not None else None),
        "method": "Pearson/Spearman/OLS descriptive statistics; not a causal estimate",
        "warnings": [],
    })
    if n < 5:
        result["warnings"].append("n<5: descriptive only; a few points can dominate")
    if r is not None and abs(r) >= 0.9 and n < 6:
        result["warnings"].append("very high |r| with small n; verify with new runs")
    levels = {}
    for x, y in zip(xs, ys):
        levels.setdefault(round(x, 12), []).append(y)
    means = [sum(values) / len(values) for _, values in sorted(levels.items())]
    diffs = [b - a for a, b in zip(means, means[1:])]
    sign_changes = sum(1 for a, b in zip(diffs, diffs[1:]) if a * b < 0)
    if sign_changes:
        result["warnings"].append("level means are not monotonic; check for an optimum or interaction")
    return result


def sensitivity_analysis(records, param_cols, response_cols, feasible_flags):
    pairs = []
    for parameter in param_cols:
        for response in response_cols:
            xs, ys, row_indexes = [], [], []
            fx, fy = [], []
            for index, record in enumerate(records):
                x = record["numbers"].get(parameter)
                y = record["numbers"].get(response)
                if x is None or y is None:
                    continue
                xs.append(x)
                ys.append(y)
                row_indexes.append(index)
                if feasible_flags[index]:
                    fx.append(x)
                    fy.append(y)
            entry = {"parameter": parameter, "response": response,
                     "parameter_unit": column_unit(parameter),
                     "response_unit": column_unit(response),
                     "support_rows": len(xs)}
            all_stats = pair_stats(xs, ys)
            entry.update(all_stats)
            if fx:
                feasible_stats = pair_stats(fx, fy)
                entry["feasible_subset"] = feasible_stats
            else:
                entry["feasible_subset"] = {
                    "n": 0, "status": "not_computable",
                    "reason": "no constraint-feasible rows with this pair"}
            basis = entry.get("feasible_subset") if entry["feasible_subset"].get(
                "status") == "computed" else entry
            entry["basis"] = "feasible_subset" if basis is not all_stats else "all_rows"
            entry["basis_n"] = basis.get("n")
            entry["basis_spearman_rho"] = basis.get("spearman_rho")
            entry["basis_pearson_r"] = basis.get("pearson_r")
            entry["basis_strength"] = basis.get("strength")
            entry["basis_direction"] = basis.get("direction")
            entry["basis_slope_per_unit"] = basis.get("slope_per_unit")
            entry["basis_r2"] = basis.get("r2")
            entry["basis_loocv_rmse"] = basis.get("loocv_rmse")
            entry["basis_effect_over_observed_span"] = basis.get(
                "effect_over_observed_span")
            pairs.append(entry)
    drivers = {}
    for response in response_cols:
        rows = [pair for pair in pairs if pair["response"] == response
                and pair.get("basis_spearman_rho") is not None]
        rows.sort(key=lambda pair: abs(pair["basis_spearman_rho"]), reverse=True)
        drivers[response] = [
            {"parameter": pair["parameter"],
             "spearman_rho": pair["basis_spearman_rho"],
             "pearson_r": pair["basis_pearson_r"],
             "direction": pair["basis_direction"],
             "strength": pair["basis_strength"],
             "basis": pair["basis"], "n": pair["basis_n"]}
            for pair in rows]
    return pairs, drivers


# ---------------------------------------------------------------------------
# records, feasibility, recommendations
# ---------------------------------------------------------------------------

def build_records(dataset, columns, param_cols, response_cols, objective_column):
    records, skipped = [], []
    for row in dataset["rows"]:
        numbers = {column: num_or_none(row["raw"].get(column)) for column in columns}
        params_complete = bool(param_cols) and all(
            numbers.get(column) is not None for column in param_cols)
        objective_complete = (objective_column is not None
                              and numbers.get(objective_column) is not None)
        required_responses = set(response_cols)
        responses_complete = all(numbers.get(column) is not None
                                 for column in required_responses)
        record = {
            "locator": row["locator"],
            "pointer": row.get("pointer", ""),
            "source": row.get("source", ""),
            "raw": row["raw"],
            "quote": row.get("quote", ""),
            "numbers": numbers,
            "params_complete": params_complete,
            "objective_complete": objective_complete,
            "responses_complete": responses_complete,
        }
        records.append(record)
        if not params_complete:
            missing = [column for column in param_cols
                       if numbers.get(column) is None]
            skipped.append({"locator": row["locator"],
                            "reason": "missing/non-numeric parameter values",
                            "columns": missing})
        elif not objective_complete:
            skipped.append({"locator": row["locator"],
                            "reason": "missing/non-numeric objective value",
                            "columns": [objective_column]})
    return records, skipped


def check_row(record, constraints, bounds, param_cols, response_cols):
    violations, unknown = [], []
    for item in constraints:
        if item.get("mapping") not in ("response_check", "row_check"):
            continue
        column = item.get("column")
        value = record["numbers"].get(column)
        if value is None:
            unknown.append({"constraint_id": item.get("id"), "column": column,
                            "reason": "value missing/non-numeric"})
            continue
        bound = item.get("value")
        if not finite(bound):
            continue
        if item.get("op") == "max" and value > bound + 1e-12:
            violations.append({"constraint_id": item.get("id"), "column": column,
                               "value": value, "op": "<=", "bound": bound,
                               "kind": item.get("kind")})
        elif item.get("op") == "min" and value < bound - 1e-12:
            violations.append({"constraint_id": item.get("id"), "column": column,
                               "value": value, "op": ">=", "bound": bound,
                               "kind": item.get("kind")})
        elif item.get("op") == "eq" and abs(value - bound) > 1e-12:
            violations.append({"constraint_id": item.get("id"), "column": column,
                               "value": value, "op": "==", "bound": bound,
                               "kind": item.get("kind")})
    for column in param_cols:
        value = record["numbers"].get(column)
        if value is None:
            continue
        info = bounds.get(column) or {}
        lower, upper = info.get("lower"), info.get("upper")
        if upper is not None and value > upper + 1e-12:
            violations.append({"constraint_id": "bounds:%s" % column,
                               "column": column, "value": value,
                               "op": "<=", "bound": upper,
                               "kind": "parameter_bound"})
        if lower is not None and value < lower - 1e-12:
            violations.append({"constraint_id": "bounds:%s" % column,
                               "column": column, "value": value,
                               "op": ">=", "bound": lower,
                               "kind": "parameter_bound"})
    return violations, unknown


def row_evidence(record, supports):
    evidence = {"path": None, "locator": record["locator"],
                "quote": record.get("quote") or "",
                "supports": supports}
    return evidence


def evidence_from_row(dataset, record, supports):
    evidence = row_evidence(record, supports)
    evidence["path"] = (record.get("source")
                        or (dataset["files"][0] if dataset.get("files") else ""))
    if not evidence["quote"]:
        evidence["quote_note"] = ("JSON row; cite path + locator. Add a literal "
                                  "quote only if it exists in the source file.")
    return evidence


def recommend(records, feasible_flags, objective, param_cols, response_cols,
               bounds, constraints, dataset, args):
    column = objective.get("column")
    direction = objective.get("direction") or "max"
    not_computable = []
    if not param_cols:
        not_computable.append({
            "item": "parameters",
            "reason": "no controllable parameter column was identified",
            "required_data": "numeric parameter columns or --parameters",
        })
        return None, not_computable
    if not column or column not in response_cols + list(records[0]["numbers"].keys() if records else []):
        not_computable.append({
            "item": "objective",
            "reason": "no objective response column could be identified",
            "required_data": "a numeric response column plus maximize/minimize goal",
        })
        return None, not_computable
    eligible = []
    for index, record in enumerate(records):
        if record["numbers"].get(column) is None:
            continue
        if feasible_flags[index]:
            eligible.append((index, record))
    assumed_no_constraints = not constraints
    if not eligible:
        not_computable.append({
            "item": "recommendation",
            "reason": "no row satisfies the identified hard constraints",
            "required_data": "at least one feasible historical run, or relaxed bounds",
        })
        return None, not_computable
    best_index, best = (max(eligible, key=lambda pair: pair[1]["numbers"][column])
                        if direction != "min" else
                        min(eligible, key=lambda pair: pair[1]["numbers"][column]))
    best_value = best["numbers"][column]
    tolerance = abs(best_value) * args.tolerance if best_value != 0 else args.tolerance
    tolerance = max(tolerance, 1e-9)
    neighborhood = []
    for index, record in eligible:
        value = record["numbers"][column]
        if direction == "min":
            inside = value <= best_value + tolerance
        else:
            inside = value >= best_value - tolerance
        if inside:
            neighborhood.append((index, record))
    parameter_items = []
    for parameter in param_cols:
        values = [record["numbers"].get(parameter) for _, record in neighborhood]
        values = [value for value in values if value is not None]
        observed = [min(values), max(values)] if values else [None, None]
        info = bounds.get(parameter) or {}
        lower, upper = info.get("lower"), info.get("upper")
        low = max([value for value in (observed[0], lower) if value is not None],
                  default=None)
        high = min([value for value in (observed[1], upper) if value is not None],
                   default=None)
        item = {"name": parameter, "unit": column_unit(parameter),
                "observed_interval": observed,
                "recommended_value": best["numbers"].get(parameter),
                "recommended_interval": [low, high],
                "hard_bounds": {"lower": lower, "upper": upper,
                                "status": info.get("status", "observed_only")},
                "status": "computed"}
        pair = next((row for row in args.pair_lookup
                     if row["parameter"] == parameter and row["response"] == column),
                    None)
        if pair:
            item["sensitivity"] = {
                "basis": pair.get("basis"),
                "n": pair.get("basis_n"),
                "pearson_r": pair.get("basis_pearson_r"),
                "spearman_rho": pair.get("basis_spearman_rho"),
                "direction": pair.get("basis_direction"),
                "strength": pair.get("basis_strength"),
                "all_rows_pearson_r": pair.get("pearson_r"),
                "loocv_rmse": pair.get("basis_loocv_rmse"),
            }
        if low is not None and high is not None and low > high + 1e-12:
            item["status"] = "not_computable"
            item["reason"] = "hard bounds do not intersect the observed interval"
            not_computable.append({
                "item": "parameter:%s" % parameter,
                "reason": item["reason"], "required_data": "consistent bounds",
            })
        elif len(values) <= 1 or (observed[0] is not None
                                  and abs(observed[1] - observed[0]) < 1e-12):
            item["caveat"] = ("only one observed level in the feasible neighborhood; "
                              "do not claim an optimum for this parameter")
        if item["status"] == "computed":
            model_prediction = None
            if pair and pair.get("status") == "computed" and pair.get("feasible_subset", {}).get("status") == "computed":
                subset = pair["feasible_subset"]
                if subset.get("slope_per_unit") is not None:
                    predicted = (subset["intercept"]
                                 + subset["slope_per_unit"] * item["recommended_value"])
                    observed_min = min(record["numbers"][parameter]
                                       for _, record in eligible)
                    observed_max = max(record["numbers"][parameter]
                                       for _, record in eligible)
                    model_prediction = {
                        "type": "linear_model_on_feasible_subset",
                        "value": predicted,
                        "loocv_rmse": subset.get("loocv_rmse"),
                        "r2": subset.get("r2"),
                        "n": subset.get("n"),
                        "extrapolation": bool(
                            item["recommended_value"] < observed_min - 1e-12
                            or item["recommended_value"] > observed_max + 1e-12),
                        "caveat": "descriptive OLS; not a validated surrogate model",
                    }
            item["predicted_response"] = {
                "column": column,
                "direction": direction,
                "observed_best": best_value,
                "observed_row": best["locator"],
                "linear_model": model_prediction,
                "uncertainty": ("not estimated from replicates"
                                if not any_duplicate(records, param_cols)
                                else "observed spread among duplicate parameter sets"),
            }
            support = [evidence_from_row(dataset, best,
                                         "feasible best row for the objective")]
            for index, record in neighborhood:
                if record is best:
                    continue
                support.append(evidence_from_row(
                    dataset, record, "row inside the objective tolerance window"))
            item["support_rows"] = support[:6]
            item["basis"] = {
                "method": "best feasible historical point + tolerance window",
                "objective_column": column,
                "objective_direction": direction,
                "tolerance_fraction": args.tolerance,
                "tolerance_absolute": tolerance,
                "constraint_bounds": info,
                "assumed_no_constraints": assumed_no_constraints,
                "sensitivity_direction": (pair or {}).get("basis_direction"),
            }
            item["confidence"] = confidence_label(item, assumed_no_constraints)
            item["caveats"] = build_caveats(item, pair, assumed_no_constraints)
        parameter_items.append(item)
    recommendation = {
        "method": "constraint-aware best-feasible-point recommendation",
        "objective": {"column": column, "direction": direction,
                      "source": objective.get("source"),
                      "needs_review": bool(objective.get("needs_review"))},
        "tolerance_fraction": args.tolerance,
        "tolerance_absolute": tolerance,
        "baseline_row": best["locator"],
        "baseline_values": {column: best["numbers"].get(column)
                            for column in param_cols},
        "objective_value": best_value,
        "parameter_items": parameter_items,
        "confidence": ("low" if any(item.get("confidence") == "low"
                                    for item in parameter_items) else "medium"),
        "caveats": [
            "recommendation is an observed feasible point, not a new optimum",
            "predicted responses come from descriptive statistics; validate before scale-up",
        ],
    }
    return recommendation, not_computable


def any_duplicate(records, param_cols):
    seen = set()
    for record in records:
        if not record["params_complete"]:
            continue
        key = tuple(record["numbers"].get(column) for column in param_cols)
        if key in seen:
            return True
        seen.add(key)
    return False


def confidence_label(item, assumed_no_constraints):
    sensitivity = item.get("sensitivity") or {}
    if assumed_no_constraints:
        return "low"
    if item.get("status") != "computed":
        return "low"
    if item.get("hard_bounds", {}).get("status") == "conflict":
        return "low"
    interval = item.get("recommended_interval") or [None, None]
    if interval[0] is not None and interval[1] is not None \
            and abs(interval[1] - interval[0]) < 1e-12:
        return "low"
    n = sensitivity.get("n") or 0
    rho = sensitivity.get("spearman_rho")
    if n < 5 or rho is None:
        return "low"
    if abs(rho) >= 0.4:
        return "medium"
    return "low"


def build_caveats(item, pair, assumed_no_constraints):
    caveats = []
    if assumed_no_constraints:
        caveats.append("no hard constraint was found; feasibility is assumed, not verified")
    if item.get("caveat"):
        caveats.append(item["caveat"])
    if pair:
        if pair.get("basis") == "all_rows":
            caveats.append("sensitivity uses all rows because feasible pairs are insufficient")
        for warning in pair.get("warnings") or []:
            caveats.append(warning)
    interval = item.get("recommended_interval") or [None, None]
    if interval[0] is not None and interval[1] is not None \
            and abs(interval[1] - interval[0]) > 0:
        caveats.append("interval is the observed feasible window, not an optimizer result")
    bounds = item.get("hard_bounds") or {}
    if bounds.get("status") == "bounded":
        caveats.append("proposed values must stay inside the cited hard bounds")
    return caveats


def tradeoff_analysis(records, response_cols, best_record, all_records, objective):
    pairs = []
    for first_index, first in enumerate(response_cols):
        for second in response_cols[first_index + 1:]:
            xs, ys = [], []
            for record in records:
                x = record["numbers"].get(first)
                y = record["numbers"].get(second)
                if x is not None and y is not None:
                    xs.append(x)
                    ys.append(y)
            stats = pair_stats(xs, ys)
            entry = {"first": first, "second": second}
            entry.update(stats)
            if stats.get("status") == "computed":
                entry["note"] = "response-response association; both may be driven by the same parameter"
            pairs.append(entry)
    selected = {}
    if best_record is not None:
        for column in response_cols:
            selected[column] = {
                "value": best_record["numbers"].get(column),
                "unit": column_unit(column),
            }
    return {"selected_row_responses": selected,
            "response_pairs": pairs,
            "objective": objective.get("column") if objective else None,
            "caveat": "correlation is not causation; use validation runs to test tradeoffs"}


def design_analysis(records, param_cols):
    """Detect correlated parameter settings in the historical design."""
    pairs = []
    for first_index, first in enumerate(param_cols):
        for second in param_cols[first_index + 1:]:
            xs, ys = [], []
            for record in records:
                x = record["numbers"].get(first)
                y = record["numbers"].get(second)
                if x is not None and y is not None:
                    xs.append(x)
                    ys.append(y)
            stats = pair_stats(xs, ys)
            entry = {"first": first, "second": second}
            entry.update(stats)
            pairs.append(entry)
    confounded = [pair for pair in pairs
                  if pair.get("status") == "computed"
                  and pair.get("pearson_r") is not None
                  and abs(pair["pearson_r"]) >= 0.5]
    return {
        "parameter_pairs": pairs,
        "confounded_pairs": confounded,
        "is_orthogonal_design": not confounded,
        "caveat": ("historical parameter settings are correlated; one-at-a-time "
                   "effects can be confounded and must be tested with a designed "
                   "validation grid rather than inferred causally"),
    }


def all_rows_best(records, objective_column):
    rows = [record for record in records
            if record["numbers"].get(objective_column) is not None]
    if not rows:
        return None
    return max(rows, key=lambda record: record["numbers"][objective_column])


# ---------------------------------------------------------------------------
# validation plan and anomalies
# ---------------------------------------------------------------------------

def build_validation_plan(recommendation, bounds, constraints, dataset, args,
                          objective):
    budget_items = [item for item in constraints
                    if item.get("mapping") == "budget"]
    budget = None
    if budget_items:
        budget = int(min(item["value"] for item in budget_items))
    limits = [value for value in (budget, args.max_runs) if value is not None]
    limit = min(limits) if limits else None
    if recommendation is None:
        return {"status": "not_computable",
                "reason": "no recommendation was produced",
                "runs": [], "stopping_rules": []}
    baseline = dict(recommendation["baseline_values"])
    parameters = [item for item in recommendation["parameter_items"]
                  if item.get("status") == "computed"]
    runs = []
    seen_keys = set()
    replicates = max(1, args.replicates)
    checks = set()
    for item in constraints:
        if item.get("mapping") == "response_check" and finite(item.get("value")):
            checks.add((item.get("column"), item.get("op"), float(item["value"])))
    for name, info in bounds.items():
        if info.get("upper") is not None:
            checks.add((name, "max", float(info["upper"])))
        if info.get("lower") is not None:
            checks.add((name, "min", float(info["lower"])))
    check_rows = [{"column": column, "op": op, "value": value}
                  for column, op, value in sorted(checks)]

    def add_run(changed, purpose, expected, extrapolation=False, replicate=False):
        if limit is not None and len(runs) >= limit:
            return
        values = dict(baseline)
        values.update(changed)
        key = tuple((name, float(values[name]) if finite(values.get(name)) else values.get(name))
                    for name in sorted(values))
        if key in seen_keys and not replicate:
            return
        seen_keys.add(key)
        run_id = "V%02d" % (len(runs) + 1)
        runs.append({
            "run_id": run_id,
            "parameters": values,
            "purpose": purpose,
            "expected": expected,
            "extrapolation": bool(extrapolation),
            "measure": {"objective": objective.get("column")},
            "constraint_checks": check_rows,
        })

    for replicate in range(replicates):
        add_run({}, "baseline replicate %d (noise estimate)" % (replicate + 1),
                "confirm the feasible center is reproducible",
                replicate=replicate > 0)
    for item in parameters:
        interval = item.get("recommended_interval") or [None, None]
        low, high = interval
        center = item.get("recommended_value")
        direction = (item.get("sensitivity") or {}).get("direction")
        if low is not None and center is not None and abs(low - center) > 1e-12:
            add_run({item["name"]: low},
                    "one-at-a-time low edge of %s" % item["name"],
                    "effect direction: %s" % direction)
        if high is not None and center is not None and abs(high - center) > 1e-12:
            add_run({item["name"]: high},
                    "one-at-a-time high edge of %s" % item["name"],
                    "effect direction: %s" % direction)
        info = bounds.get(item["name"]) or {}
        for kind, bound in (("upper", info.get("upper")),
                            ("lower", info.get("lower"))):
            if bound is None or center is None or abs(bound - center) < 1e-12:
                continue
            improving = ((kind == "upper" and direction != "negative")
                         or (kind == "lower" and direction != "positive"))
            if not improving:
                continue
            add_run({item["name"]: bound},
                    "probe the binding hard %s bound of %s" % (kind, item["name"]),
                    "extrapolation beyond the observed window; test whether the "
                    "bound is executable and whether impurity/cost rise",
                    extrapolation=True)
    stopping_rules = [
        {"id": "S1", "condition": "any hard-bound or response constraint is violated at the center in 2 consecutive replicates",
         "action": "stop, record violations, and escalate to the equipment/safety owner"},
        {"id": "S2", "condition": "objective improvement is below the tolerance for 3 consecutive runs",
         "action": "stop; the local response has plateaued; report the final interval"},
        {"id": "S3", "condition": "objective is within the predicted window and all constraints hold in %d of %d center replicates" % (replicates, replicates),
         "action": "stop as confirmed; update the recommendation with measured values"},
        {"id": "S4", "condition": "any measured value exceeds an explicit safety/equipment limit",
         "action": "immediate stop; do not continue the grid"},
        {"id": "S5", "condition": "validation budget is exhausted",
         "action": "stop and report inconclusive or partial confirmation"},
    ]
    cost_column = next((column for column in dataset["columns"]
                        if hint_match(column, COST_HINTS)), None)
    per_run_cost = None
    best_locator = recommendation.get("baseline_row")
    best_record = next((record for record in dataset.get("records", [])
                        if record.get("locator") == best_locator), None)
    if cost_column and best_record:
        per_run_cost = best_record["numbers"].get(cost_column)
    return {
        "status": "suggested_not_executed",
        "budget_runs": budget,
        "max_runs_applied": limit,
        "replicates": replicates,
        "runs": runs,
        "runs_planned": len(runs),
        "measured_columns": sorted({objective.get("column")} |
                                   set(item.get("column") for item in constraints
                                       if item.get("mapping") == "response_check")),
        "stopping_rules": stopping_rules,
        "estimated_cost": {
            "column": cost_column,
            "per_run_observation": per_run_cost,
            "total_for_planned_runs": (per_run_cost * len(runs)
                                       if per_run_cost is not None else None),
            "caveat": "historical cost per run, not a vendor quote or a forecast",
        },
        "caveat": "rule-based suggestions; no future outcome is claimed",
    }


def build_anomalies(records, infeasible, all_best, best_record, objective,
                    bounds, conflicts, duplicates, param_cols):
    anomalies = []
    for conflict in conflicts:
        anomalies.append({"type": "constraint_conflict",
                          "detail": conflict.get("message"),
                          "column": conflict.get("column"),
                          "values": conflict.get("values")})
    if all_best is not None and best_record is not None \
            and all_best["locator"] != best_record["locator"]:
        objective_column = objective.get("column")
        anomalies.append({
            "type": "infeasible_best_row",
            "detail": "the highest/lowest historical objective is not feasible; "
                      "do not recommend it without a documented constraint waiver",
            "row": all_best["locator"],
            "objective_value": all_best["numbers"].get(objective_column),
            "feasible_best": best_record["locator"],
            "feasible_objective": best_record["numbers"].get(objective_column),
        })
    for item in infeasible[:10]:
        if all_best is not None and item["locator"] == all_best["locator"]:
            continue
        anomalies.append({
            "type": ("row_unknown" if item.get("status") == "unknown"
                     else "infeasible_row"),
            "detail": ("row has missing values; feasibility is unknown"
                       if item.get("status") == "unknown"
                       else "row violates hard constraints"),
            "row": item["locator"],
            "violations": item["violations"],
        })
    for group in duplicates.get("conflicting", []):
        anomalies.append({"type": "conflicting_duplicates",
                          "detail": "identical parameter settings have different responses",
                          "key": group.get("key"), "rows": group.get("locators")})
    for column in param_cols:
        values = [record["numbers"].get(column) for record in records
                  if record["numbers"].get(column) is not None]
        if values and max(values) - min(values) < 1e-12:
            anomalies.append({"type": "single_level_parameter",
                              "detail": "parameter never varies in the data; "
                                        "sensitivity cannot be estimated",
                              "column": column, "value": values[0]})
    return anomalies


def duplicate_analysis(records, param_cols, objective_column):
    groups = {}
    for record in records:
        if not record["params_complete"]:
            continue
        key = tuple(record["numbers"].get(column) for column in param_cols)
        groups.setdefault(key, []).append(record)
    duplicates, conflicts = [], []
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        values = [row["numbers"].get(objective_column) for row in rows
                  if row["numbers"].get(objective_column) is not None]
        entry = {"key": {column: key[index] for index, column in enumerate(param_cols)},
                 "locators": [row["locator"] for row in rows],
                 "objective_values": values}
        duplicates.append(entry)
        if values and max(values) - min(values) > 1e-12:
            conflicts.append(entry)
    return {"groups": duplicates, "conflicting": conflicts}


# ---------------------------------------------------------------------------
# report rendering
# ---------------------------------------------------------------------------

def render_report(analysis):
    dataset = analysis.get("dataset") or {}
    detection = analysis.get("detection") or {}
    quality = analysis.get("data_quality") or {}
    constraints = analysis.get("constraints") or {}
    domain = analysis.get("feasible_domain") or {}
    sensitivity = analysis.get("sensitivity") or {}
    recommendation = analysis.get("recommendations") or {}
    tradeoffs = analysis.get("tradeoffs") or {}
    design = analysis.get("design") or {}
    validation = analysis.get("validation_plan") or {}
    anomalies = analysis.get("anomalies") or []
    missing = analysis.get("not_computable") or []
    lines = [
        "# 参数敏感性与推荐分析（脚本生成）",
        "",
        "- case_id: `%s`" % analysis.get("case_id"),
        "- 状态: **%s**" % analysis.get("status"),
        "- 生成时间: %s" % analysis.get("generated_at"),
        "- 数据指纹: `%s`" % dataset.get("fingerprint", "n/a"),
        "- 输入文件: %s" % ", ".join("`%s`" % name for name in dataset.get("files") or []) \
        + ("" if dataset.get("files") else "（无可用表格）"),
        "",
        "> 本报告只使用 `/input` 中的真实表格与约束材料；不补造测量值。",
        "> 相关性/回归仅作描述，不构成因果或已验证的代理模型。",
        "",
        "## 1. 数据质量",
        "",
        "- 行数：%s；可用于目标计算的完整行：%s；跳过：%s" % (
            quality.get("rows_total", 0),
            quality.get("rows_complete_for_objective", 0),
            quality.get("rows_skipped", 0)),
        "- 记录类型：%s（%s）" % (dataset.get("record_type"),
                                dataset.get("record_type_caveat")),
        "- 判别使用启发式：%s" % ("是" if detection.get("heuristic") else "否"),
    ]
    for warning in quality.get("warnings") or []:
        lines.append("- 警告：%s" % warning)
    if quality.get("columns"):
        lines += ["", "| 列 | 单位 | 数值 | 缺失 | 非数值 |", "|---|---|---:|---:|---:|"]
        for column in quality["columns"]:
            lines.append("| `%s` | %s | %s | %s | %s |" % (
                column.get("column"), column.get("unit") or "—",
                column.get("numeric"), column.get("missing"),
                column.get("non_numeric")))
    lines += ["", "## 2. 参数与响应识别", ""]
    lines.append("- 可控参数：%s" % ("；".join(
        "`%s`%s" % (item.get("column"),
                    " (%s)" % item.get("unit") if item.get("unit") else "")
        for item in detection.get("parameters") or []) or "未识别"))
    lines.append("- 响应指标：%s" % ("；".join(
        "`%s`%s" % (item.get("column"),
                    " (%s)" % item.get("unit") if item.get("unit") else "")
        for item in detection.get("responses") or []) or "未识别"))
    objective = detection.get("objective") or {}
    lines.append("- 优化目标：`%s`（%s；来源：%s%s）" % (
        objective.get("column"), objective.get("direction"),
        objective.get("source"),
        "；需复核" if objective.get("needs_review") else ""))
    if objective.get("ambiguity"):
        lines.append("- 目标歧义：%s；候选：%s" % (
            objective["ambiguity"],
            "；".join("`%s`(%s) @ %s %s" % (
                item.get("column"), item.get("direction"),
                item.get("path"), item.get("locator"))
                for item in objective.get("candidates") or [])))
    if detection.get("unclassified"):
        lines.append("- 未分类列：%s" % "、".join(
            "`%s`" % column for column in detection["unclassified"]))
    lines += ["", "## 3. 约束与可行域", ""]
    lines.append("- 约束状态：**%s**" % constraints.get("status"))
    if constraints.get("items"):
        lines += ["", "| ID | 列 | 条件 | 类型 | 来源 | 需复核 |", "|---|---|---|---|---|---|"]
        for item in constraints["items"]:
            lines.append("| %s | `%s` | %s %s | %s | %s %s | %s |" % (
                item.get("id"), item.get("column"), item.get("op"),
                fmt(item.get("value")), item.get("kind"),
                item.get("path") or item.get("source") or "",
                item.get("locator") or "",
                "是" if item.get("needs_review") else "否"))
    for conflict in constraints.get("conflicts") or []:
        lines.append("- 约束冲突：`%s` %s；采用最严格值 %s。" % (
            conflict.get("column"), conflict.get("message"),
            fmt(conflict.get("binding"))))
    for column, info in (domain.get("bounds") or {}).items():
        lines.append("- `%s`：观测 [%s, %s]；硬边界 [%s, %s]；可执行区间 [%s, %s]（%s）" % (
            column, fmt((info.get("observed") or [None, None])[0]),
            fmt((info.get("observed") or [None, None])[1]),
            fmt(info.get("lower")), fmt(info.get("upper")),
            fmt((info.get("feasible") or [None, None])[0]),
            fmt((info.get("feasible") or [None, None])[1]),
            info.get("status")))
    lines.append("- 可行行：%s；不可行行：%s；无法判定：%s" % (
        domain.get("rows_feasible"), domain.get("rows_infeasible"),
        domain.get("rows_unknown")))
    lines += ["", "## 4. 敏感性与趋势（描述性）", ""]
    pairs = [pair for pair in sensitivity.get("pairs") or []
             if pair.get("status") == "computed"]
    if pairs:
        lines += ["| 参数 | 响应 | n(可行/全部) | Pearson r(可行) | Spearman rho(可行) | 斜率(可行) | LOOCV RMSE(可行) | 方向 | 强度 | 依据 |",
                  "|---|---|---:|---:|---:|---:|---:|---|---|---|"]
        for pair in pairs:
            lines.append("| `%s` | `%s` | %s/%s | %s | %s | %s | %s | %s | %s | %s |" % (
                pair["parameter"], pair["response"], pair.get("basis_n"),
                pair.get("n"),
                fmt(pair.get("basis_pearson_r")),
                fmt(pair.get("basis_spearman_rho")),
                fmt(pair.get("basis_slope_per_unit")),
                fmt(pair.get("basis_loocv_rmse")),
                pair.get("basis_direction"), pair.get("basis_strength"),
                pair.get("basis")))
    else:
        lines.append("- 无可计算的参数-响应配对（见第 8 节）。")
    for response, drivers in (sensitivity.get("drivers_by_response") or {}).items():
        if drivers:
            lines.append("- `%s` 的主要参数：%s" % (
                response, "；".join("%s (rho=%s, %s)" % (
                    driver["parameter"], fmt(driver["spearman_rho"]),
                    driver["strength"]) for driver in drivers[:5])))
    lines += ["", "## 5. 推荐值与推荐区间", ""]
    parameter_items = recommendation.get("parameter_items") or []
    if parameter_items:
        lines.append("- 基线行：%s；目标 `%s` = %s；置信度：%s" % (
            recommendation.get("baseline_row"),
            (recommendation.get("objective") or {}).get("column"),
            fmt(recommendation.get("objective_value")),
            recommendation.get("confidence")))
        lines += ["", "| 参数 | 推荐值 | 推荐区间 | 硬边界 | 依据 | 置信度 |", "|---|---:|---|---|---|---|"]
        for item in parameter_items:
            interval = item.get("recommended_interval") or [None, None]
            hard = item.get("hard_bounds") or {}
            lines.append("| `%s` | %s %s | [%s, %s] | [%s, %s] | %s | %s |" % (
                item.get("name"), fmt(item.get("recommended_value")),
                item.get("unit") or "", fmt(interval[0]), fmt(interval[1]),
                fmt(hard.get("lower")), fmt(hard.get("upper")),
                "基线行 + 容差邻域", item.get("confidence") or "—"))
        for item in parameter_items:
            for caveat in item.get("caveats") or []:
                lines.append("- `%s` 注意：%s" % (item.get("name"), caveat))
            support = item.get("support_rows") or []
            if support:
                lines.append("- `%s` 证据：%s" % (
                    item.get("name"),
                    "；".join("`%s` %s" % (row.get("path"), row.get("locator"))
                             for row in support)))
    else:
        lines.append("- 未生成推荐（见第 8 节 not_computable）。")
    lines += ["", "## 6. 权衡", ""]
    selected = tradeoffs.get("selected_row_responses") or {}
    if selected:
        lines.append("- 推荐行响应：%s" % "；".join(
            "`%s` = %s %s" % (column, fmt(info.get("value")), info.get("unit") or "")
            for column, info in selected.items()))
    for pair in tradeoffs.get("response_pairs") or []:
        if pair.get("status") == "computed":
            lines.append("- `%s` vs `%s`：Pearson r = %s，n = %s。" % (
                pair["first"], pair["second"], fmt(pair.get("pearson_r")),
                pair.get("n")))
    if design.get("confounded_pairs"):
        lines.append("- 参数设计警告：%s" % design.get("caveat"))
        lines.append("- 历史参数相关对：%s" % "；".join(
            "%s vs %s (r=%s)" % (pair["first"], pair["second"],
                                 fmt(pair.get("pearson_r")))
            for pair in design["confounded_pairs"]))
    lines += ["", "## 7. 验证实验与停止条件", ""]
    if validation.get("runs"):
        lines.append("- 状态：%s；计划 %s 次；预算上限 %s；每点重复 %s。" % (
            validation.get("status"), validation.get("runs_planned"),
            validation.get("budget_runs"), validation.get("replicates")))
        lines += ["", "| run | 参数 | 目的 | 预期方向 |", "|---|---|---|---|"]
        for run in validation["runs"]:
            lines.append("| %s | %s | %s | %s |" % (
                run["run_id"],
                "；".join("%s=%s" % (key, fmt(value))
                         for key, value in run["parameters"].items()),
                run["purpose"], run.get("expected") or "—"))
        cost = validation.get("estimated_cost") or {}
        if cost.get("per_run_observation") is not None:
            lines.append("- 成本口径：历史单次 `%s` = %s；计划合计约 %s（非报价）。" % (
                cost.get("column"), fmt(cost.get("per_run_observation")),
                fmt(cost.get("total_for_planned_runs"))))
        lines.append("- 停止条件：")
        for rule in validation.get("stopping_rules") or []:
            lines.append("  - `%s` 当 %s → %s" % (
                rule.get("id"), rule.get("condition"), rule.get("action")))
    else:
        lines.append("- 无可执行的验证计划：%s" % validation.get("reason"))
    lines += ["", "## 8. 不可计算项与异常复核", ""]
    if missing:
        for item in missing:
            lines.append("- 不可计算 `%s`：%s（需要：%s）" % (
                item.get("item"), item.get("reason"), item.get("required_data")))
    if anomalies:
        for anomaly in anomalies:
            lines.append("- 异常 `%s`：%s %s" % (
                anomaly.get("type"), anomaly.get("detail"),
                anomaly.get("row") or anomaly.get("column") or ""))
    if not missing and not anomalies:
        lines.append("- 未发现不可计算项或异常。")
    lines += ["", "## 9. 证据定位", ""]
    evidence = analysis.get("evidence") or []
    if evidence:
        for item in evidence[:MAX_EVIDENCE]:
            quote = item.get("quote")
            if quote:
                lines.append("- `%s` %s：`%s`（%s）" % (
                    item.get("path"), item.get("locator"), quote,
                    item.get("supports")))
            else:
                lines.append("- `%s` %s：%s" % (
                    item.get("path"), item.get("locator"),
                    item.get("quote_note") or item.get("supports")))
    else:
        lines.append("- 无可用证据行。")
    lines += ["", "## 10. 复跑命令", "", "```bash",
              "python3 scripts/parameter_optimizer.py --input /input --output /app/output",
              "```", "",
              "相同输入与参数下，行选择、约束过滤与统计量可复算；",
              "若显式传入 `--parameters/--responses/--objective`，请在报告中记录同样参数。",
              ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def analyze(input_dir, output_dir, args):
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    case = read_json(input_dir / "case.json", {})
    if not isinstance(case, dict):
        case = {}
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    files = scan_files(input_dir)
    materials = []
    for row in files:
        if row["kind"] == "text" and row["bytes"] <= MAX_DATA_BYTES:
            try:
                text = (input_dir / row["path"]).read_text(
                    encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            materials.append({"path": row["path"], "text": text})

    tables, ignored_files = [], []
    for row in files:
        if row["kind"] == "csv":
            tables.append(load_csv_table(input_dir / row["path"], row["path"]))
        elif row["kind"] == "json" and row["path"] != "case.json":
            tables.extend(load_json_tables(input_dir / row["path"], row["path"]))
    usable = [table for table in tables if table.get("columns") and table.get("rows")]
    for table in tables:
        if table not in usable:
            ignored_files.append({"path": table.get("path"),
                                  "reason": "; ".join(table.get("warnings") or [])
                                  or "no data rows"})
    if not usable:
        payload = base_payload(case_id, "input_error", input_dir, output_dir)
        payload["not_computable"] = [{
            "item": "dataset",
            "reason": "no readable CSV/JSON table with at least one data row was found",
            "required_data": "a CSV/JSON history table with parameter and response columns",
        }]
        payload["inputs"]["files"] = files
        payload["inputs"]["ignored"] = ignored_files
        payload["summary"] = "未找到可用的参数/结果表格，未生成任何数值结论。"
        return payload

    contract_tables = [table for table in usable
                       if table.get("record_type") == "contract_table"]
    data_tables = [table for table in usable
                   if table.get("record_type") != "contract_table"]
    for table in contract_tables:
        ignored_files.append({
            "path": table.get("path"),
            "reason": "resource/price/booking contract table; read it as a "
                      "constraint source, not as parameter-response measurements",
        })
    if not data_tables:
        payload = base_payload(case_id, "not_computable", input_dir, output_dir)
        payload["not_computable"] = [{
            "item": "dataset",
            "reason": "only resource/price/booking contract tables were found; "
                      "no parameter-response observations",
            "required_data": "a history/observation table with parameter and response columns",
        }]
        payload["inputs"]["files"] = files
        payload["inputs"]["ignored"] = ignored_files
        payload["summary"] = "仅找到资源/价格/预约表，不能据此计算敏感性或推荐值。"
        return payload
    usable = data_tables

    wanted = args.file or case.get("data_file") or case.get("dataset") or case.get("data_files")
    if isinstance(wanted, list):
        wanted = wanted[0] if wanted else None
    if wanted:
        wanted_text = str(wanted)
        selected = [table for table in usable
                    if table.get("path") == wanted_text
                    or Path(str(table.get("path"))).name == wanted_text
                    or str(table.get("path")).endswith(wanted_text)]
        if selected:
            usable = selected
        else:
            ignored_files.append({"path": wanted_text,
                                  "reason": "requested data file not found; using all tables"})
            if args.file:
                payload = base_payload(case_id, "input_error", input_dir, output_dir)
                payload["not_computable"] = [{
                    "item": "dataset",
                    "reason": "explicit --file was not found: %s" % wanted_text,
                    "required_data": "a readable CSV/JSON file at that path",
                }]
                payload["inputs"]["files"] = files
                return payload

    groups = {}
    for table in usable:
        groups.setdefault((tuple(table["columns"]),
                           table.get("record_type", "measurements")), []).append(table)
    (columns, record_type), group = max(
        groups.items(),
        key=lambda item: (1 if item[0][1] == "measurements" else 0,
                          sum(len(table["rows"]) for table in item[1]),
                          len(item[1]), item[0][0]))
    for other_key, other_tables in groups.items():
        if other_key == (columns, record_type) and other_tables is group:
            continue
        for table in other_tables:
            ignored_files.append({
                "path": table.get("path"),
                "reason": "column set or record type differs from the selected dataset",
            })
    dataset = {
        "files": [table["path"] for table in group],
        "columns": list(columns),
        "record_type": record_type,
        "record_type_caveat": (
            "candidate plans are proposals/predictions, not measured outcomes; "
            "recommendations are descriptive arithmetic on those plans and "
            "require experimental validation"
            if record_type == "candidate_plans" else
            "rows are treated as measured or reported observations"),
        "rows": [row for table in group for row in table["rows"]],
        "ignored_tables": ignored_files,
    }
    fingerprint_payload = json.dumps(
        {"columns": dataset["columns"],
         "rows": [row["raw"] for row in dataset["rows"]]},
        ensure_ascii=False, sort_keys=True, default=str)
    dataset["fingerprint"] = "sha256:" + hashlib.sha256(
        fingerprint_payload.encode("utf-8")).hexdigest()

    detection = classify_columns(columns, case, args)
    param_cols = detection["parameters"]
    response_cols = detection["responses"]
    if args.objective:
        column_name, _ = parse_objective_value(args.objective)
        column = resolve_column(column_name, columns)
        if column and column not in response_cols:
            response_cols.append(column)

    objective = find_objective(case, materials, response_cols, columns, args)
    if objective.get("column") and objective["column"] not in response_cols:
        response_cols.append(objective["column"])
    if objective.get("column") not in columns:
        objective["column"] = None

    records, skipped = build_records(dataset, columns, param_cols, response_cols,
                                     objective.get("column"))
    dataset["records"] = records
    dataset["row_count"] = len(records)

    structured, structured_notes = structured_constraints(case)
    text_items = extract_text_constraints(materials)
    applied, unmapped = apply_constraints(structured + text_items, columns,
                                          param_cols, response_cols)
    bounds, bound_conflicts = effective_bounds(applied, param_cols)
    budget_items = [item for item in applied if item.get("mapping") == "budget"]
    constraints_block = {
        "status": ("applied" if applied and not unmapped else
                   "partial" if applied else "not_provided"),
        "items": applied,
        "unmapped": unmapped,
        "structured_notes": structured_notes,
        "conflicts": bound_conflicts,
        "budget": {
            "validation_runs_max": (min(item["value"] for item in budget_items)
                                    if budget_items else None),
            "sources": budget_items,
        },
    }
    constraint_for_rows = [item for item in applied
                           if item.get("mapping") in ("parameter_bound",
                                                      "response_check", "row_check")]
    feasible_flags, infeasible = [], []
    for record in records:
        if not record["params_complete"]:
            feasible_flags.append(False)
            infeasible.append({"locator": record["locator"],
                               "values": {column: record["numbers"].get(column)
                                          for column in param_cols},
                               "violations": [{"reason": "incomplete parameter values"}],
                               "status": "unknown"})
            continue
        violations, unknown = check_row(record, constraint_for_rows, bounds,
                                        param_cols, response_cols)
        if violations:
            feasible_flags.append(False)
            infeasible.append({"locator": record["locator"],
                               "values": {column: record["numbers"].get(column)
                                          for column in param_cols},
                               "violations": violations})
        elif unknown and constraint_for_rows:
            feasible_flags.append(False)
            infeasible.append({"locator": record["locator"],
                               "values": {column: record["numbers"].get(column)
                                          for column in param_cols},
                               "violations": unknown,
                               "status": "unknown"})
        else:
            feasible_flags.append(True)

    observed_bounds = {}
    for column in param_cols:
        values = [record["numbers"].get(column) for record in records
                  if record["numbers"].get(column) is not None]
        observed = [min(values), max(values)] if values else [None, None]
        info = bounds.get(column) or {}
        lower, upper = info.get("lower"), info.get("upper")
        feasible_low = max(value for value in (observed[0], lower)
                           if value is not None) if (observed[0] is not None or lower is not None) else None
        feasible_high = min(value for value in (observed[1], upper)
                            if value is not None) if (observed[1] is not None or upper is not None) else None
        status = "bounded" if (lower is not None or upper is not None) else "observed_only"
        if feasible_low is not None and feasible_high is not None \
                and feasible_low > feasible_high + 1e-12:
            status = "no_overlap"
        observed_bounds[column] = {
            "lower": lower, "upper": upper, "observed": observed,
            "feasible": [feasible_low, feasible_high], "status": status,
        }
    domain = {
        "bounds": observed_bounds,
        "rows_feasible": sum(1 for flag in feasible_flags if flag),
        "rows_infeasible": len(infeasible),
        "rows_unknown": sum(1 for item in infeasible if item.get("status") == "unknown"),
        "infeasible_rows": infeasible,
        "assumed_no_constraints": not constraint_for_rows,
    }

    pairs, drivers = sensitivity_analysis(records, param_cols, response_cols,
                                          feasible_flags)
    args.pair_lookup = pairs
    recommendation, not_computable = recommend(
        records, feasible_flags, objective, param_cols, response_cols,
        bounds, constraint_for_rows, dataset, args)

    duplicates = duplicate_analysis(records, param_cols, objective.get("column"))
    best_record = None
    if recommendation is not None:
        best_record = next((record for record in records
                            if record["locator"] == recommendation.get("baseline_row")), None)
    all_best = (all_rows_best(records, objective["column"])
                if objective.get("column") else None)
    tradeoffs = tradeoff_analysis(records, response_cols, best_record, records, objective)
    design = design_analysis(records, param_cols)
    validation = build_validation_plan(recommendation, bounds, applied, dataset,
                                       args, objective)
    anomalies = build_anomalies(records, infeasible, all_best, best_record,
                                objective, bounds, bound_conflicts, duplicates,
                                param_cols)
    if design["confounded_pairs"]:
        anomalies.append({
            "type": "confounded_historical_design",
            "detail": design["caveat"],
            "pairs": [{"first": pair["first"], "second": pair["second"],
                       "pearson_r": pair.get("pearson_r")}
                      for pair in design["confounded_pairs"]],
        })
    if record_type == "candidate_plans":
        anomalies.append({
            "type": "candidate_plan_only",
            "detail": dataset["record_type_caveat"],
            "files": dataset["files"],
        })
        if recommendation:
            recommendation["record_type_caveat"] = dataset["record_type_caveat"]
            for item in recommendation.get("parameter_items") or []:
                item["confidence"] = "low"
                item.setdefault("caveats", []).append(
                    "input rows are candidate plans/predictions, not measurements")

    if not param_cols:
        not_computable.append({
            "item": "parameters",
            "reason": "no controllable parameter column was identified",
            "required_data": "columns with numeric settings or --parameters",
        })
    if not response_cols:
        not_computable.append({
            "item": "responses",
            "reason": "no response/outcome column was identified",
            "required_data": "columns with measured outcomes or --responses",
        })
    if not constraint_for_rows:
        not_computable.append({
            "item": "constraints",
            "reason": "no hard constraint was found; feasibility is assumed",
            "required_data": "equipment/material/safety/cost limits with sources",
        })
    if detection.get("missing_columns"):
        not_computable.append({
            "item": "missing_columns",
            "reason": "explicit columns not found in the dataset",
            "required_data": ", ".join(detection["missing_columns"]),
        })

    not_computable = dedupe(not_computable, ("item", "reason"))
    evidence = collect_evidence(dataset, materials, recommendation, objective,
                                applied, all_best, best_record)
    quality_columns = []
    for column in columns:
        numeric = missing = non_numeric = 0
        examples = []
        for record in records:
            value = record["numbers"].get(column)
            raw = record["raw"].get(column)
            if value is not None:
                numeric += 1
            elif raw in (None, "") or str(raw).strip().lower() in NULLS:
                missing += 1
            else:
                non_numeric += 1
                if len(examples) < 3:
                    examples.append({"locator": record["locator"], "raw": raw})
        quality_columns.append({"column": column, "unit": column_unit(column),
                                "numeric": numeric, "missing": missing,
                                "non_numeric": non_numeric,
                                "examples": examples})
    complete_for_objective = sum(
        1 for record in records
        if record["objective_complete"] and record["params_complete"])
    warnings = []
    if len(records) < 5:
        warnings.append("fewer than 5 rows: statistics are descriptive only")
    if detection.get("heuristic"):
        warnings.append("column roles were inferred by name heuristics; "
                        "confirm or rerun with explicit --parameters/--responses")
    if detection.get("missing_columns"):
        warnings.append("explicit columns missing: %s" % ", ".join(
            detection["missing_columns"]))
    quality = {
        "rows_total": len(records),
        "rows_complete_for_objective": complete_for_objective,
        "rows_skipped": len(skipped),
        "skipped_rows": skipped[:20],
        "columns": quality_columns,
        "duplicate_parameter_sets": duplicates["groups"],
        "conflicting_duplicates": duplicates["conflicting"],
        "warnings": warnings,
    }

    computed_pairs = sum(1 for pair in pairs if pair.get("status") == "computed")
    if recommendation and computed_pairs:
        status = "ok"
    elif recommendation or computed_pairs:
        status = "partial"
    else:
        status = "not_computable"
    if status == "ok" and not_computable:
        status = "partial"

    payload = base_payload(case_id, status, input_dir, output_dir)
    payload.update({
        "summary": summary_text(status, len(records), len(param_cols),
                                len(response_cols), computed_pairs,
                                recommendation),
        "inputs": {"input_dir": str(input_dir),
                   "files": files, "ignored": ignored_files},
        "dataset": {"files": dataset["files"], "columns": dataset["columns"],
                    "row_count": len(records),
                    "record_type": record_type,
                    "record_type_caveat": dataset["record_type_caveat"],
                    "fingerprint": dataset["fingerprint"],
                    "ignored_tables": ignored_files},
        "detection": {
            "parameters": [{"column": column, "unit": column_unit(column),
                            "source": detection["sources"].get(column, "heuristic")}
                           for column in param_cols],
            "responses": [{"column": column, "unit": column_unit(column),
                           "source": detection["sources"].get(column, "heuristic")}
                          for column in response_cols],
            "index_like": detection["index_like"],
            "unclassified": detection["unclassified"],
            "objective": objective,
            "heuristic": detection["heuristic"],
            "missing_columns": detection["missing_columns"],
        },
        "data_quality": quality,
        "constraints": constraints_block,
        "feasible_domain": domain,
        "sensitivity": {
            "method": {
                "pearson": "linear association on complete pairs",
                "spearman": "rank association (monotonic, tie-averaged)",
                "ols": "simple linear fit y = intercept + slope*x",
                "loocv_rmse": "leave-one-out CV RMSE, n>=4",
                "strength_thresholds": {"strong": 0.7, "moderate": 0.4,
                                        "weak": 0.2},
                "caveat": "descriptive; no causal or predictive guarantee",
            },
            "pairs": pairs,
            "drivers_by_response": drivers,
            "computable_pairs": computed_pairs,
            "not_computable_pairs": len(pairs) - computed_pairs,
        },
        "recommendations": recommendation or {
            "status": "not_computable",
            "reason": "no feasible recommendation could be computed",
            "parameter_items": [],
        },
        "tradeoffs": tradeoffs,
        "design": design,
        "validation_plan": validation,
        "anomalies": anomalies,
        "not_computable": not_computable,
        "evidence": evidence[:MAX_EVIDENCE],
        "assumptions": [
            "only /input tables and text are used; no external data",
            "columns are numeric after unit/percent stripping; ambiguous cells are skipped",
            "hard constraints come from case.json or explicit text lines; regex-extracted "
            "constraints are marked needs_review and must be confirmed by the model",
            dataset["record_type_caveat"],
        ],
        "next_actions": next_actions(recommendation, not_computable, anomalies),
    })
    return payload


def base_payload(case_id, status, input_dir, output_dir):
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "scripts/parameter_optimizer.py",
        "generated_at": now_iso(),
        "case_id": case_id,
        "status": status,
        "summary": "",
        "inputs": {"input_dir": str(input_dir), "files": [], "ignored": []},
        "dataset": {},
        "detection": {},
        "data_quality": {},
        "constraints": {},
        "feasible_domain": {},
        "sensitivity": {},
        "recommendations": {},
        "tradeoffs": {},
        "design": {},
        "validation_plan": {},
        "anomalies": [],
        "not_computable": [],
        "evidence": [],
        "assumptions": [],
        "next_actions": [],
        "outputs": {
            "json": str(Path(output_dir) / "parameter-analysis.json"),
            "markdown": str(Path(output_dir) / "parameter-analysis.md"),
        },
    }


def summary_text(status, rows, params, responses, pairs, recommendation):
    if status == "input_error":
        return "未找到可用数据表，未生成数值结论。"
    if recommendation:
        return ("从 %d 行数据、%d 个参数、%d 个响应中计算了 %d 个可计算配对；"
                "推荐基于可行历史点，需验证实验确认。" % (
                    rows, params, responses, pairs))
    return ("从 %d 行数据、%d 个参数、%d 个响应中计算了 %d 个可计算配对；"
            "推荐不可计算，缺口见 not_computable。" % (
                rows, params, responses, pairs))


def next_actions(recommendation, missing, anomalies):
    actions = []
    if recommendation:
        actions.append("核对推荐区间与硬边界；把 support_rows 逐字写入 work evidence")
        actions.append("按 validation_plan 执行验证，并在停止条件触发时记录结果")
    if any(item.get("item") == "constraints" for item in missing):
        actions.append("补充设备/材料/安全/成本约束材料；不要把无约束当成可行")
    if any(item.get("item") == "missing_columns" for item in missing):
        actions.append("检查列名或显式传入 --parameters/--responses")
    if any(item.get("type") == "infeasible_best_row" for item in anomalies):
        actions.append("不要推荐不可行的历史最优点；保留冲突证据并转人工确认")
    if any(item.get("type") == "constraint_conflict" for item in anomalies):
        actions.append("按最严格边界执行，并请设备/材料负责人书面确认")
    if any(item.get("type") == "confounded_historical_design" for item in anomalies):
        actions.append("用 one-at-a-time 或正交设计验证，避免把相关历史解释成因果")
    if any(item.get("type") == "candidate_plan_only" for item in anomalies):
        actions.append("候选方案不是实测值；先做验证实验再宣称最优")
    if not actions:
        actions.append("复核 not_computable 与 anomalous 行后再写报告")
    return actions


def collect_evidence(dataset, materials, recommendation, objective, applied,
                     all_best, best_record):
    evidence = []
    if objective.get("path") and objective.get("quote"):
        evidence.append({"path": objective["path"],
                         "locator": objective.get("locator") or "",
                         "quote": objective["quote"],
                         "supports": "optimization objective"})
    if recommendation:
        for item in recommendation.get("parameter_items") or []:
            for row in item.get("support_rows") or []:
                evidence.append(row)
    if all_best is not None:
        evidence.append(evidence_from_row(
            dataset, all_best, "historical objective extreme (check feasibility)"))
    if best_record is not None:
        evidence.append(evidence_from_row(
            dataset, best_record, "feasible recommended baseline row"))
    for item in applied:
        if item.get("quote") and item.get("path"):
            evidence.append({"path": item["path"],
                             "locator": item.get("locator") or "",
                             "quote": item["quote"],
                             "supports": "constraint: %s %s %s" % (
                                 item.get("column"), item.get("op"),
                                 fmt(item.get("value")))})
    for material in materials:
        for line_no, line in enumerate(material["text"].splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if re.search(r"(?i)highest|maximum|violat|conflict|最高|冲突|矛盾", stripped) \
                    and len(stripped) >= 20:
                evidence.append({"path": material["path"],
                                 "locator": "L%d" % line_no,
                                 "quote": stripped,
                                 "supports": "reported extreme/conflict note"})
                break
    evidence = dedupe(evidence, ("path", "locator", "quote"))
    return evidence


def render_markdown_from_payload(payload):
    return render_report(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Constraint-aware parameter sensitivity and recommendation analysis")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--parameters", default="",
                        help="comma-separated controllable parameter columns")
    parser.add_argument("--responses", default="",
                        help="comma-separated response columns")
    parser.add_argument("--objective", default="",
                        help="objective column with optional direction, e.g. yield_pct:max")
    parser.add_argument("--file", default="",
                        help="restrict loading to one CSV/JSON data file")
    parser.add_argument("--tolerance", type=float, default=0.05,
                        help="relative objective tolerance for the recommendation window")
    parser.add_argument("--max-runs", type=int, default=12,
                        help="upper bound on suggested validation runs")
    parser.add_argument("--replicates", type=int, default=2,
                        help="baseline replicates in the validation plan")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    args = parser.parse_args(argv)

    output_dir = Path(args.output)
    json_path = Path(args.json_out) if args.json_out else output_dir / "parameter-analysis.json"
    md_path = Path(args.md_out) if args.md_out else output_dir / "parameter-analysis.md"
    try:
        payload = analyze(Path(args.input), output_dir, args)
        payload["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
        write_json(json_path, payload)
        write_text(md_path, render_markdown_from_payload(payload))
        print(json.dumps({
            "script": "parameter_optimizer.py",
            "case_id": payload.get("case_id"),
            "status": payload.get("status"),
            "json": str(json_path),
            "markdown": str(md_path),
            "parameters": len((payload.get("detection") or {}).get("parameters") or []),
            "responses": len((payload.get("detection") or {}).get("responses") or []),
            "sensitivity_pairs": (payload.get("sensitivity") or {}).get("computable_pairs"),
            "recommendations": len((payload.get("recommendations") or {}).get("parameter_items") or []),
            "not_computable": len(payload.get("not_computable") or []),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_by": "scripts/parameter_optimizer.py",
            "generated_at": now_iso(),
            "case_id": "CASE",
            "status": "input_error",
            "summary": "analysis crashed before producing a result",
            "error": "%s: %s" % (type(exc).__name__, exc),
            "traceback": traceback.format_exc()[-4000:],
            "not_computable": [{
                "item": "analysis",
                "reason": "unexpected script error",
                "required_data": "inspect the traceback and input files",
            }],
            "outputs": {"json": str(json_path), "markdown": str(md_path)},
        }
        try:
            write_json(json_path, payload)
            write_text(md_path, "# 参数分析失败\n\n脚本未完成：`%s`\n\n见 JSON 中的 traceback。\n"
                       % payload["error"])
        except OSError as write_exc:
            print("cannot write analysis outputs: %s" % write_exc, file=sys.stderr)
            return 2
        print(json.dumps({"script": "parameter_optimizer.py",
                          "status": "input_error", "error": payload["error"],
                          "json": str(json_path)}, ensure_ascii=False, indent=2),
              file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
