#!/usr/bin/env python3
"""Calibrate figure pixel coordinates to axis values (linear or log).

Usage:
    python3 calibrate_figure_axes.py --calib axis-calibration.json \
        --points pixel-points.json --out readings.json --json
    python3 calibrate_figure_axes.py --self-test --json

Calibration format:
    {"x": {"pixel": [120, 620], "value": [300, 400], "scale": "linear", "unit": "C",
           "pixel_error": 1.0},
     "y": {"pixel": [380, 40], "value": [0, 100], "scale": "linear", "unit": "%"}}

Points format:
    [{"id": "p1", "px": 320, "py": 120}]
    or {"points": [...]}

Output readings carry estimated=true, value error from the calibration slope and
the source units. It never guesses an axis scale: log axes must be declared.

Stdlib only. Exit 0 = calibrated, 1 = warnings/skipped, 2 = unreadable input.
"""
import argparse
import json
import math
import sys
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def interp(pixel, p1, p2, v1, v2, scale):
    if p2 == p1:
        raise ValueError("calibration pixels must differ")
    if scale == "log":
        if v1 <= 0 or v2 <= 0:
            raise ValueError("log calibration needs positive values")
        l1, l2 = math.log10(v1), math.log10(v2)
        value = 10 ** (l1 + (pixel - p1) * (l2 - l1) / (p2 - p1))
        error = abs(value * math.log(10) * (l2 - l1) / (p2 - p1))
    else:
        value = v1 + (pixel - p1) * (v2 - v1) / (p2 - p1)
        error = abs((v2 - v1) / (p2 - p1))
    return value, error


def parse_axis(config, name):
    if not isinstance(config, dict):
        return None
    pixels = config.get("pixel") or config.get("pixels")
    values = config.get("value") or config.get("values")
    if not (isinstance(pixels, list) and len(pixels) == 2 and
            isinstance(values, list) and len(values) == 2):
        raise ValueError("%s calibration needs pixel[2] and value[2]" % name)
    p1, p2 = float(pixels[0]), float(pixels[1])
    v1, v2 = float(values[0]), float(values[1])
    return {"p1": p1, "p2": p2, "v1": v1, "v2": v2,
            "scale": str(config.get("scale") or "linear").lower(),
            "unit": str(config.get("unit") or ""),
            "pixel_error": float(config.get("pixel_error") or 1.0)}


def calibrate(calib, points):
    warnings = []
    axes = {}
    for name in ("x", "y"):
        if name in calib:
            try:
                axes[name] = parse_axis(calib[name], name)
            except ValueError as exc:
                warnings.append(str(exc))
    if not axes:
        raise ValueError("no x/y calibration found")
    readings = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            warnings.append("point[%d] is not an object" % index)
            continue
        row = {"id": str(point.get("id") or "p%d" % (index + 1)),
               "px": point.get("px", point.get("x_px")),
               "py": point.get("py", point.get("y_px")),
               "estimated": True}
        for name, axis in axes.items():
            pixel = row["px"] if name == "x" else row["py"]
            if pixel is None:
                warnings.append("%s: missing %spx" % (row["id"], name))
                row[name] = None
                continue
            value, slope_error = interp(float(pixel), axis["p1"], axis["p2"],
                                        axis["v1"], axis["v2"], axis["scale"])
            row[name] = round(value, 6)
            row[name + "_unit"] = axis["unit"]
            row[name + "_error"] = round(slope_error * axis["pixel_error"], 6)
            row[name + "_scale"] = axis["scale"]
        readings.append(row)
    return {"ok": not warnings, "readings": readings, "warnings": warnings,
            "axes": {name: {"unit": axis["unit"], "scale": axis["scale"]}
                     for name, axis in axes.items()}}


def self_test():
    calib = {
        "x": {"pixel": [100, 600], "value": [300, 400], "scale": "linear", "unit": "C"},
        "y": {"pixel": [400, 0], "value": [1, 100], "scale": "log", "unit": "a.u."},
    }
    points = [{"id": "p1", "px": 350, "py": 200}]
    out = calibrate(calib, points)
    row = out["readings"][0]
    assert abs(row["x"] - 350) < 1e-6, row
    assert abs(row["y"] - 10) < 1e-6, row
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", default=None)
    ap.add_argument("--points", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        print(json.dumps({"ok": True, "self_test": True, "result": self_test()},
                         ensure_ascii=False, indent=2))
        return 0
    if not args.calib or not args.points:
        ap.error("--calib and --points are required unless --self-test is used")
    try:
        calib = load_json(args.calib)
        points = load_json(args.points)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": "cannot read input: %s" % exc},
                         ensure_ascii=False))
        return 2
    if isinstance(points, dict):
        points = points.get("points") or []
    if not isinstance(points, list):
        print(json.dumps({"ok": False, "error": "points must be a list"},
                         ensure_ascii=False))
        return 2
    try:
        report = calibrate(calib, points)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
