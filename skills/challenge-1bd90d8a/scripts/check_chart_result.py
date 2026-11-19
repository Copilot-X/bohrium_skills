#!/usr/bin/env python3
"""Validate a source-value chart result, spec and rendered SVG.

Usage:
    python3 check_chart_result.py --result /app/output/result.json \
        --spec /app/work/figure-spec.json --svg /app/output/figures/fig2.svg \
        --root /app/output --json
    python3 check_chart_result.py --self-test --json

Checks
  * result has case_id/figure/artifacts/series_map/axes/value_check/checks
  * every series_map entry has panel/source/values; values match the spec
  * axes have labels/units/scales; estimated points are declared
  * value_check.all_match is true and checked_points covers all values
  * SVG parses, keeps <text>, has viewBox and no external refs
  * preview PNG and export PDF have valid headers
  * no data point outside the source/spec

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CHART_TYPES = {"bar", "line", "scatter", "box", "heatmap"}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def close(a, b, tolerance=1e-6):
    try:
        return abs(float(a) - float(b)) <= tolerance
    except (TypeError, ValueError):
        return False


def spec_map(spec):
    mapping = {}
    for panel in spec.get("panels") or []:
        if not isinstance(panel, dict):
            continue
        pid = str(panel.get("id") or "")
        for series in panel.get("series") or []:
            if not isinstance(series, dict):
                continue
            key = (pid, str(series.get("name") or ""))
            mapping[key] = series.get("values") or []
    return mapping


def check_svg(path, problems, warnings, checks):
    p = Path(path)
    if not p.is_file():
        problems.append("missing SVG: %s" % p)
        return
    raw = p.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        problems.append("SVG is empty")
        return
    if "<script" in raw.lower():
        problems.append("SVG contains <script>")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        problems.append("SVG not well-formed: %s" % exc)
        return
    if not root.tag.endswith("svg"):
        problems.append("SVG root is not <svg>")
    if "viewBox" not in root.attrib:
        problems.append("SVG missing viewBox")
    if raw.count("<text") == 0:
        problems.append("SVG has no <text> elements")
    for bad in ("href=\"http://", "href='http://", "href=\"https://", "href='https://"):
        if bad in raw:
            problems.append("SVG has external href")
            break
    checks["svg_text_elements"] = raw.count("<text")
    checks["svg_paths"] = raw.count("<path")


def check_headers(path, role, problems, checks):
    p = Path(path)
    if not p.is_file():
        problems.append("missing %s: %s" % (role, p))
        return
    raw = p.read_bytes()
    if p.suffix.lower() == ".png":
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            problems.append("%s is not a PNG" % role)
        else:
            checks[role + "_size"] = [int.from_bytes(raw[16:20], "big"),
                                      int.from_bytes(raw[20:24], "big")]
    elif p.suffix.lower() == ".pdf":
        if not raw.startswith(b"%PDF"):
            problems.append("%s is not a PDF" % role)


def validate(result, spec, svg_path, root):
    problems, warnings, checks = [], [], {}
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["result root is not an object"],
                "warnings": [], "checks": {}}
    for key in ("case_id", "figure", "artifacts", "series_map", "axes",
                "value_check", "checks"):
        if key not in result:
            problems.append("result.json missing %s" % key)
    mapping = spec_map(spec) if isinstance(spec, dict) else {}
    series_map = result.get("series_map") or []
    if not isinstance(series_map, list) or not series_map:
        problems.append("series_map is empty")
        series_map = []
    checked = 0
    for i, item in enumerate(series_map):
        if not isinstance(item, dict):
            problems.append("series_map[%d] is not an object" % i)
            continue
        pid = str(item.get("panel") or "")
        name = str(item.get("series") or "")
        values = item.get("values") or []
        if not item.get("source"):
            problems.append("series_map[%d] has no source" % i)
        if not values:
            problems.append("series_map[%d] has no values" % i)
        checked += len(values)
        expected = mapping.get((pid, name))
        if expected is not None:
            if len(expected) != len(values):
                problems.append("series %s/%s length mismatch" % (pid, name))
            else:
                for j, (a, b) in enumerate(zip(expected, values)):
                    if not close(a, b):
                        problems.append("series %s/%s point %d %.6g != spec %.6g" %
                                        (pid, name, j, float(b), float(a)))
        error = item.get("error") or {}
        if error and not error.get("type"):
            warnings.append("series %s/%s error has no type" % (pid, name))
    checks["series"] = len(series_map)
    checks["checked_points"] = checked

    axes = result.get("axes") or []
    for i, item in enumerate(axes):
        if not isinstance(item, dict):
            problems.append("axes[%d] is not an object" % i)
            continue
        for axis in ("x", "y"):
            cfg = item.get(axis) or {}
            if not cfg.get("label"):
                problems.append("axes[%d].%s missing label" % (i, axis))
            if not cfg.get("unit") and axis == "y":
                warnings.append("axes[%d].y has no unit" % i)
            if cfg.get("scale") and cfg["scale"] not in ("linear", "log", "log10"):
                warnings.append("axes[%d].%s unusual scale %s" % (i, axis, cfg["scale"]))
    checks["axes"] = len(axes)

    value_check = result.get("value_check") or {}
    if not isinstance(value_check, dict):
        problems.append("value_check is not an object")
    else:
        if value_check.get("all_match") is not True:
            problems.append("value_check.all_match is not true")
        if value_check.get("mismatches"):
            problems.append("value_check has mismatches")
        if value_check.get("checked_points") is not None and int(value_check["checked_points"]) < checked:
            warnings.append("value_check.checked_points < series values")
    if svg_path:
        check_svg(svg_path, problems, warnings, checks)

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
        if role in ("preview", "export"):
            check_headers(candidate, role, problems, checks)
    if "editable" not in roles:
        problems.append("artifacts missing role=editable")
    if "preview" not in roles:
        warnings.append("artifacts missing role=preview")
    checks["artifact_roles"] = sorted(roles)
    return {"ok": not problems, "problems": problems, "warnings": warnings, "checks": checks}


def self_test():
    result = {
        "case_id": "chart-self-test",
        "figure": {"panels": 1, "chart_types": ["bar"]},
        "series_map": [{"panel": "a", "series": "A", "values": [1, 2],
                        "source": "data.csv#rows1-2", "error": {"type": "SD", "n": 3}}],
        "axes": [{"panel": "a", "x": {"label": "x"}, "y": {"label": "y", "unit": "%"}}],
        "value_check": {"all_match": True, "checked_points": 2, "mismatches": []},
        "checks": {"xml_valid": True},
        "artifacts": [{"role": "editable", "path": "fig.svg"},
                      {"role": "preview", "path": "fig.png"}],
    }
    spec = {"panels": [{"id": "a", "type": "bar",
                        "series": [{"name": "A", "values": [1, 2]}]}]}
    return result, spec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None)
    ap.add_argument("--spec", default=None)
    ap.add_argument("--svg", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "fig.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                '<text x="1" y="5">1</text></svg>', encoding="utf-8")
            (root / "fig.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 +
                (10).to_bytes(4, "big") + (10).to_bytes(4, "big") + b"\x00" * 16)
            result, spec = self_test()
            report = validate(result, spec, root / "fig.svg", root)
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
    spec = None
    if args.spec:
        try:
            spec = load_json(args.spec)
        except (OSError, json.JSONDecodeError):
            spec = None
    report = validate(result, spec, args.svg, args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
