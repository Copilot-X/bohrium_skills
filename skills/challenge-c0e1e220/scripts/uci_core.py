#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic UCI profile engine (Concrete / Iris / Wine).

This module is the executable half of the skill.  It is deliberately
standard-library only; numpy is imported inside a try/except block solely to
cross-check a few summary statistics and is never required.

Two ways to use it:

* imported by ``uci_profile.py`` to build the derived snapshot and config;
* copied verbatim to ``work/analysis.py`` and executed by the harness
  (``python work/analysis.py``, config read from ``work/uci-run.json``).

It never invents a row or a number.  Every number written to the outputs is
computed from the snapshot rows named in the config; every SQL statement is
executed in read-only mode; missing data, duplicates, outliers and
assumption failures are reported, not silently repaired.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import re
import sqlite3
import statistics
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

CORE_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Canonical dataset profiles
# ---------------------------------------------------------------------------

# (canonical key, human label, unit, description)
SPECS = {
    "concrete": {
        "label": "混凝土抗压强度（Concrete，1030 行级公开快照）",
        "columns": [
            ("cement", "Cement (component 1)", "kg/m3", "水泥用量"),
            ("blast_furnace_slag", "Blast Furnace Slag (component 2)", "kg/m3", "高炉矿渣用量"),
            ("fly_ash", "Fly Ash (component 3)", "kg/m3", "粉煤灰用量"),
            ("water", "Water (component 4)", "kg/m3", "用水量"),
            ("superplasticizer", "Superplasticizer (component 5)", "kg/m3", "减水剂用量"),
            ("coarse_aggregate", "Coarse Aggregate (component 6)", "kg/m3", "粗骨料用量"),
            ("fine_aggregate", "Fine Aggregate (component 7)", "kg/m3", "细骨料用量"),
            ("age", "Age (day)", "day", "养护龄期"),
            ("compressive_strength", "Concrete compressive strength(MPa)", "MPa", "混凝土抗压强度"),
        ],
        "group": None,
        "target": "compressive_strength",
        "expected_rows": 1030,
    },
    "iris": {
        "label": "鸢尾花测量（Iris，150 行 bezdek 源变体）",
        "columns": [
            ("sepal_length", "sepal length in cm", "cm", "花萼长度"),
            ("sepal_width", "sepal width in cm", "cm", "花萼宽度"),
            ("petal_length", "petal length in cm", "cm", "花瓣长度"),
            ("petal_width", "petal width in cm", "cm", "花瓣宽度"),
            ("species", "class", "", "鸢尾花类别（setosa/versicolor/virginica）"),
        ],
        "group": "species",
        "target": "petal_length",
        "expected_rows": 150,
    },
    "wine": {
        "label": "葡萄酒理化指标（Wine，178 行公开快照）",
        "columns": [
            ("wine_class", "class", "", "葡萄酒品种类别代码（原始编码 1/2/3）"),
            ("alcohol", "Alcohol", "", "酒精含量（原始数据未标注单位）"),
            ("malic_acid", "Malic acid", "", "苹果酸（原始数据未标注单位）"),
            ("ash", "Ash", "", "灰分（原始数据未标注单位）"),
            ("alcalinity_of_ash", "Alcalinity of ash", "", "灰分碱度（原始数据未标注单位）"),
            ("magnesium", "Magnesium", "", "镁含量（原始数据未标注单位）"),
            ("total_phenols", "Total phenols", "", "总酚（原始数据未标注单位）"),
            ("flavanoids", "Flavanoids", "", "类黄酮（原始数据未标注单位）"),
            ("nonflavanoid_phenols", "Nonflavanoid phenols", "", "非类黄酮酚（原始数据未标注单位）"),
            ("proanthocyanins", "Proanthocyanins", "", "原花青素（原始数据未标注单位）"),
            ("color_intensity", "Color intensity", "", "颜色强度（原始数据未标注单位）"),
            ("hue", "Hue", "", "色调（原始数据未标注单位）"),
            ("od280_od315", "OD280/OD315 of diluted wines", "", "OD280/OD315 吸光度比（无量纲）"),
            ("proline", "Proline", "", "脯氨酸（原始数据未标注单位）"),
        ],
        "group": "wine_class",
        "target": "alcohol",
        "expected_rows": 178,
    },
}

# Order used when a headerless file is identified by file name.
HEADERLESS_ORDER = {
    "concrete": [c[0] for c in SPECS["concrete"]["columns"]],
    "iris": [c[0] for c in SPECS["iris"]["columns"]],
    "wine": [c[0] for c in SPECS["wine"]["columns"]],
}

# Alias patterns are checked in order; more specific patterns come first.
ALIAS_PATTERNS = [
    (r"compressive.?strength|concrete.?compressive|strength.?mpa", "compressive_strength"),
    (r"blast.?furnace|slag", "blast_furnace_slag"),
    (r"fly.?ash", "fly_ash"),
    (r"super.?plastic", "superplasticizer"),
    (r"coarse.?aggregate|coarse.?agg", "coarse_aggregate"),
    (r"fine.?aggregate|fine.?agg", "fine_aggregate"),
    (r"^cement|cement.?component", "cement"),
    (r"^water|water.?component", "water"),
    (r"^age$|age.?day|^age_", "age"),
    (r"sepal.?length", "sepal_length"),
    (r"sepal.?width", "sepal_width"),
    (r"petal.?length", "petal_length"),
    (r"petal.?width", "petal_width"),
    (r"non.?flavanoid|non.?flavonoid", "nonflavanoid_phenols"),
    (r"flavanoids?|flavonoids?", "flavanoids"),
    (r"alcalinity|alkalinity", "alcalinity_of_ash"),
    (r"od.?280|od.?315", "od280_od315"),
    (r"proanthocyanin", "proanthocyanins"),
    (r"color.?intensity|colour.?intensity", "color_intensity"),
    (r"total.?phenols?", "total_phenols"),
    (r"malic", "malic_acid"),
    (r"magnesium", "magnesium"),
    (r"^ash$", "ash"),
    (r"^hue$", "hue"),
    (r"proline", "proline"),
    (r"^alcohol$|alcohol.?content", "alcohol"),
    (r"wine.?class|cultivar", "class_label"),
    (r"^species$|iris.?class|^class$|^variety$|^target$", "class_label"),
]

# Conservative screening bounds.  Violations are flagged for review, never
# deleted.  The bounds are wide on purpose: they catch impossible values
# (negative concentrations, zero/negative curing age), not plausible extremes.
RANGE_RULES = {
    "cement": (0.0, 1000.0), "blast_furnace_slag": (0.0, 1000.0),
    "fly_ash": (0.0, 1000.0), "water": (0.0, 1000.0),
    "superplasticizer": (0.0, 100.0), "coarse_aggregate": (0.0, 1500.0),
    "fine_aggregate": (0.0, 1500.0), "age": (1.0, 3650.0),
    "compressive_strength": (0.0, 200.0),
    "sepal_length": (0.0, 30.0), "sepal_width": (0.0, 30.0),
    "petal_length": (0.0, 30.0), "petal_width": (0.0, 30.0),
    "alcohol": (0.0, 25.0), "malic_acid": (0.0, 20.0),
    "ash": (0.0, 10.0), "alcalinity_of_ash": (0.0, 50.0),
    "magnesium": (0.0, 500.0), "total_phenols": (0.0, 10.0),
    "flavanoids": (0.0, 10.0), "nonflavanoid_phenols": (0.0, 10.0),
    "proanthocyanins": (0.0, 10.0), "color_intensity": (0.0, 50.0),
    "hue": (0.0, 5.0), "od280_od315": (0.0, 10.0), "proline": (0.0, 5000.0),
}

MISSING_TOKENS = {"", "na", "n/a", "nan", "null", "none", "?", "-", "--",
                  "missing", "not available", "unknown"}

# ---------------------------------------------------------------------------
# Basic IO and numeric helpers
# ---------------------------------------------------------------------------


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return default


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def load_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {} if default is None else default


def _json_safe(value):
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def write_json(path, payload):
    return write_text(path, json.dumps(_json_safe(payload), ensure_ascii=False,
                                       indent=2, allow_nan=False) + "\n")


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def sha256_text(text):
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def rel(path, base):
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).as_posix()


def quote_ident(name):
    return '"%s"' % str(name).replace('"', '""')


def normalize_name(text):
    value = str(text or "").strip().lower()
    value = value.replace("µ", "u").replace("μ", "u")
    value = value.replace("%", " pct ")
    value = value.replace("°", " ")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def match_canonical(raw):
    name = normalize_name(raw)
    if not name:
        return ""
    for pattern, key in ALIAS_PATTERNS:
        if re.search(pattern, name):
            return key
    return ""


def safe_float(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if math.isfinite(numeric) else None
    text = str(value).strip()
    if text.lower() in MISSING_TOKENS:
        return None
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def is_missing(value):
    return value is None or (isinstance(value, str)
                             and value.strip().lower() in MISSING_TOKENS)


def format_p(value):
    """Human-readable p value; 0.0 means below floating-point resolution."""
    if value is None:
        return "NA"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric == 0.0:
        return "<1e-300"
    if numeric < 1e-4:
        return "%.2e" % numeric
    return "%.4g" % numeric


def round_or_none(value, digits=6):
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


def quantile(sorted_values, q):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return float(sorted_values[low])
    weight = pos - low
    return float(sorted_values[low] * (1.0 - weight)
                 + sorted_values[high] * weight)


# ---------------------------------------------------------------------------
# Distribution functions (stdlib implementations)
# ---------------------------------------------------------------------------


def norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _betacf(a, b, x):
    max_iter, eps, fpmin = 240, 3.0e-14, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    try:
        bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                      + a * math.log(x) + b * math.log(1.0 - x))
    except ValueError:
        return None
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t, df):
    if t is None or df is None or df <= 0:
        return None
    try:
        return betai(df / 2.0, 0.5, df / (df + t * t))
    except (ValueError, OverflowError):
        return None


def t_cdf(t, df):
    p_two = t_two_sided_p(t, df)
    if p_two is None:
        return None
    return 1.0 - p_two / 2.0 if t > 0 else p_two / 2.0


def t_ppf(p, df):
    """Inverse t CDF by bisection; accurate to about 1e-8."""
    if df is None or df <= 0:
        return None
    if not 0.0 < p < 1.0:
        return None
    low, high = -1.0e4, 1.0e4
    for _ in range(200):
        mid = (low + high) / 2.0
        cdf = t_cdf(mid, df)
        if cdf is None:
            return None
        if cdf < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def f_cdf(f, d1, d2):
    if f is None or d1 is None or d2 is None or d1 <= 0 or d2 <= 0:
        return None
    if f <= 0:
        return 0.0
    value = betai(d1 / 2.0, d2 / 2.0, d1 * f / (d1 * f + d2))
    return None if value is None else value


def _gammainc_p(a, x):
    if a <= 0 or x < 0:
        return None
    if x == 0:
        return 0.0
    if x < a + 1.0:
        ap, term, total = a, 1.0 / a, 1.0 / a
        for _ in range(500):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 3.0e-14:
                break
        return total * math.exp(-x + a * math.log(x) - math.lgamma(a))
    b = x + 1.0 - a
    c = 1.0e300
    d = 1.0 / b if b else 1.0e-300
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1.0e-300:
            d = 1.0e-300
        c = b + an / c
        if abs(c) < 1.0e-300:
            c = 1.0e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3.0e-14:
            break
    q = math.exp(-x + a * math.log(x) - math.lgamma(a)) * h
    return 1.0 - q


def chi2_sf(x, df):
    if x is None or df is None or df <= 0:
        return None
    value = _gammainc_p(df / 2.0, x / 2.0)
    return None if value is None else max(0.0, min(1.0, 1.0 - value))


# ---------------------------------------------------------------------------
# Statistical estimators
# ---------------------------------------------------------------------------


def describe(values):
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    n = len(clean)
    base = {"n": n, "missing": len(values) - n}
    if not clean:
        return base
    ordered = sorted(clean)
    mean = statistics.fmean(clean)
    sd = statistics.stdev(clean) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 0 else None
    q1 = quantile(ordered, 0.25)
    med = quantile(ordered, 0.5)
    q3 = quantile(ordered, 0.75)
    iqr = (q3 - q1) if q1 is not None and q3 is not None else None
    skew = None
    kurt = None
    if n > 2 and sd > 0:
        m2 = sum((x - mean) ** 2 for x in clean) / n
        m3 = sum((x - mean) ** 3 for x in clean) / n
        m4 = sum((x - mean) ** 4 for x in clean) / n
        g1 = m3 / (m2 ** 1.5) if m2 > 0 else None
        if g1 is not None:
            skew = math.sqrt(n * (n - 1.0)) / (n - 2.0) * g1
        g2 = m4 / (m2 * m2) - 3.0 if m2 > 0 else None
        if g2 is not None:
            kurt = ((n - 1.0) / ((n - 2.0) * (n - 3.0))) * (
                (n + 1.0) * g2 + 6.0) if n > 3 else g2
    ci_low = ci_high = None
    if n > 1 and se is not None:
        crit = t_ppf(0.975, n - 1)
        if crit is not None:
            ci_low, ci_high = mean - crit * se, mean + crit * se
    outliers_iqr, outliers_z = [], []
    if iqr is not None:
        low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outliers_iqr = [x for x in clean if x < low or x > high]
    if n >= 3 and sd > 0:
        outliers_z = [x for x in clean if abs((x - mean) / sd) > 3.0]
    base.update({
        "mean": round_or_none(mean), "sd": round_or_none(sd),
        "se": round_or_none(se), "min": round_or_none(min(clean)),
        "q1": round_or_none(q1), "median": round_or_none(med),
        "q3": round_or_none(q3), "max": round_or_none(max(clean)),
        "iqr": round_or_none(iqr), "skewness": round_or_none(skew),
        "excess_kurtosis": round_or_none(kurt),
        "ci95": [round_or_none(ci_low), round_or_none(ci_high)],
        "outliers_iqr_count": len(outliers_iqr),
        "outliers_z3_count": len(outliers_z),
    })
    return base


def jarque_bera(values):
    clean = [float(v) for v in values if v is not None]
    n = len(clean)
    if n < 8:
        return {"n": n, "status": "insufficient_n", "jb": None, "p": None,
                "note": "需要 n>=8 才能做 Jarque-Bera 筛查"}
    mean = statistics.fmean(clean)
    sd = statistics.stdev(clean)
    if sd == 0:
        return {"n": n, "status": "zero_variance", "jb": None, "p": None,
                "note": "组内方差为 0，正态性筛查无定义"}
    m3 = sum((x - mean) ** 3 for x in clean) / n
    m4 = sum((x - mean) ** 4 for x in clean) / n
    g1 = m3 / sd ** 3
    g2 = m4 / sd ** 4 - 3.0
    jb = n / 6.0 * (g1 ** 2 + g2 ** 2 / 4.0)
    p = chi2_sf(jb, 2)
    return {"n": n, "status": "ok", "jb": round_or_none(jb),
            "p": round_or_none(p),
            "skewness": round_or_none(g1), "excess_kurtosis": round_or_none(g2),
            "note": "Jarque-Bera 是大样本筛查，不等同于 Shapiro-Wilk"}


def welch_ttest(a, b):
    x = [float(v) for v in a if v is not None]
    y = [float(v) for v in b if v is not None]
    n1, n2 = len(x), len(y)
    if n1 < 2 or n2 < 2:
        return {"status": "insufficient_n", "n1": n1, "n2": n2,
                "p": None, "t": None, "df": None}
    m1, m2 = statistics.fmean(x), statistics.fmean(y)
    s1 = statistics.stdev(x)
    s2 = statistics.stdev(y)
    v1, v2 = s1 * s1 / n1, s2 * s2 / n2
    se = math.sqrt(v1 + v2)
    diff = m1 - m2
    if se == 0:
        return {"status": "zero_variance", "n1": n1, "n2": n2,
                "mean1": round_or_none(m1), "mean2": round_or_none(m2),
                "diff": round_or_none(diff), "p": None, "t": None, "df": None,
                "note": "两组标准误均为 0，Welch t 检验无定义"}
    t = diff / se
    df = (v1 + v2) ** 2 / (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1))
    p = t_two_sided_p(t, df)
    crit = t_ppf(0.975, df)
    ci_low = ci_high = None
    if crit is not None:
        ci_low, ci_high = diff - crit * se, diff + crit * se
    pooled_sd = math.sqrt(((n1 - 1) * s1 * s1 + (n2 - 1) * s2 * s2)
                          / (n1 + n2 - 2))
    d = diff / pooled_sd if pooled_sd > 0 else None
    correction = 1.0 - 3.0 / (4.0 * (n1 + n2) - 9.0) if n1 + n2 > 6 else 1.0
    g = d * correction if d is not None else None
    return {
        "status": "ok", "n1": n1, "n2": n2,
        "mean1": round_or_none(m1), "mean2": round_or_none(m2),
        "diff": round_or_none(diff), "se": round_or_none(se),
        "t": round_or_none(t), "df": round_or_none(df),
        "p": round_or_none(p), "ci95_diff": [round_or_none(ci_low),
                                             round_or_none(ci_high)],
        "cohen_d": round_or_none(d), "hedges_g": round_or_none(g),
        "method": "Welch two-sample t (two-sided); CI uses t(df)",
    }


def one_way_anova(groups):
    """groups: list of (label, values)."""
    usable = [(label, [float(v) for v in values if v is not None])
              for label, values in groups]
    usable = [(label, values) for label, values in usable if values]
    k = len(usable)
    n_total = sum(len(values) for _, values in usable)
    if k < 2 or n_total <= k:
        return {"status": "insufficient_groups", "k": k, "n": n_total,
                "p": None, "F": None}
    means = {label: statistics.fmean(values) for label, values in usable}
    grand = sum(sum(values) for _, values in usable) / n_total
    ss_between = sum(len(values) * (means[label] - grand) ** 2
                     for label, values in usable)
    ss_within = sum((len(values) - 1) * statistics.variance(values)
                    for label, values in usable if len(values) > 1)
    df1, df2 = k - 1, n_total - k
    if ss_within <= 0:
        return {"status": "zero_within_variance", "k": k, "n": n_total,
                "p": None, "F": None, "ss_between": round_or_none(ss_between),
                "ss_within": round_or_none(ss_within)}
    ms_between = ss_between / df1
    ms_within = ss_within / df2
    f = ms_between / ms_within
    p = None
    cdf = f_cdf(f, df1, df2)
    if cdf is not None:
        p = 1.0 - cdf
    eta2 = ss_between / (ss_between + ss_within)
    omega2 = (ss_between - df1 * ms_within) / (
        ss_between + ss_within + ms_within)
    return {
        "status": "ok", "k": k, "n": n_total, "F": round_or_none(f),
        "df1": df1, "df2": df2, "p": round_or_none(p),
        "ss_between": round_or_none(ss_between),
        "ss_within": round_or_none(ss_within),
        "ms_within": round_or_none(ms_within),
        "eta_squared": round_or_none(eta2),
        "omega_squared": round_or_none(omega2),
        "grand_mean": round_or_none(grand), "group_means": {
            str(label): round_or_none(value) for label, value in means.items()},
        "method": "classical one-way ANOVA (two-sided F test)",
    }


def welch_anova(groups):
    usable = [(label, [float(v) for v in values if v is not None])
              for label, values in groups]
    usable = [(label, values) for label, values in usable if len(values) >= 2]
    k = len(usable)
    if k < 2:
        return {"status": "insufficient_groups", "k": k, "p": None}
    means = [statistics.fmean(values) for _, values in usable]
    variances = [statistics.variance(values) for _, values in usable]
    ns = [len(values) for _, values in usable]
    if any(v <= 0 for v in variances):
        return {"status": "zero_variance", "k": k, "p": None,
                "note": "至少一组方差为 0，Welch ANOVA 无定义"}
    weights = [n / v for n, v in zip(ns, variances)]
    total_w = sum(weights)
    weighted_mean = sum(w * m for w, m in zip(weights, means)) / total_w
    a = sum(w * (m - weighted_mean) ** 2 for w, m in zip(weights, means)) / (k - 1)
    term = sum((1.0 - w / total_w) ** 2 / (n - 1)
               for w, n in zip(weights, ns))
    b = 1.0 + 2.0 * (k - 2.0) / (k * k - 1.0) * term
    f = a / b
    df2 = (k * k - 1.0) / (3.0 * term)
    p = None
    cdf = f_cdf(f, k - 1, df2)
    if cdf is not None:
        p = 1.0 - cdf
    return {"status": "ok", "k": k, "F": round_or_none(f), "df1": k - 1,
            "df2": round_or_none(df2), "p": round_or_none(p),
            "method": "Welch ANOVA (unequal variances)"}


def brown_forsythe(groups):
    converted = []
    for label, values in groups:
        clean = [float(v) for v in values if v is not None]
        if len(clean) < 2:
            continue
        med = statistics.median(clean)
        converted.append((label, [abs(x - med) for x in clean]))
    if len(converted) < 2:
        return {"status": "insufficient_groups", "p": None}
    result = one_way_anova(converted)
    result["method"] = "Brown-Forsythe (absolute deviations from group medians)"
    result["note"] = "p<0.05 提示组间离散程度不同；不等方差时优先看 Welch 结果"
    return result


def pearson(xs, ys):
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys)
             if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return {"status": "insufficient_n", "n": n, "r": None, "p": None}
    x = [p[0] for p in pairs]
    y = [p[1] for p in pairs]
    mx, my = statistics.fmean(x), statistics.fmean(y)
    ssx = sum((v - mx) ** 2 for v in x)
    ssy = sum((v - my) ** 2 for v in y)
    if ssx == 0 or ssy == 0:
        return {"status": "zero_variance", "n": n, "r": None, "p": None}
    r = sum((a - mx) * (b - my) for a, b in pairs) / math.sqrt(ssx * ssy)
    r = max(-1.0, min(1.0, r))
    if abs(r) >= 1.0:
        return {"status": "perfect_linear", "n": n, "r": round_or_none(r),
                "p": 0.0, "ci95": [round_or_none(r), round_or_none(r)]}
    t = r * math.sqrt((n - 2) / (1.0 - r * r))
    p = t_two_sided_p(t, n - 2)
    z = math.atanh(r)
    se = 1.0 / math.sqrt(n - 3) if n > 3 else None
    ci = None
    if se is not None:
        ci = [round_or_none(math.tanh(z - 1.959964 * se)),
              round_or_none(math.tanh(z + 1.959964 * se))]
    return {"status": "ok", "n": n, "r": round_or_none(r),
            "p": round_or_none(p), "ci95": ci,
            "method": "Pearson r (two-sided t test); CI via Fisher z"}


def holm(pvalues):
    indexed = [(index, value) for index, value in enumerate(pvalues)
               if value is not None]
    if not indexed:
        return [None] * len(pvalues)
    indexed.sort(key=lambda item: item[1])
    m = len(indexed)
    adjusted = [None] * len(pvalues)
    running = 0.0
    for rank, (index, value) in enumerate(indexed):
        candidate = min(1.0, (m - rank) * value)
        running = max(running, candidate)
        adjusted[index] = running
    return adjusted


# ---------------------------------------------------------------------------
# Snapshot loading
# ---------------------------------------------------------------------------


def connect_ro(path):
    uri = "file:%s?mode=ro" % Path(path).resolve().as_posix()
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def table_count(conn, table):
    try:
        return conn.execute("SELECT COUNT(*) FROM %s" % quote_ident(table)).fetchone()[0]
    except sqlite3.Error:
        return None


def load_snapshot_tables(cfg):
    path = Path(cfg.get("snapshot_path") or "")
    if not path.is_file():
        return []
    conn = connect_ro(path)
    tables = []
    try:
        for spec in cfg.get("tables") or []:
            name = spec.get("name")
            if not name:
                continue
            columns = spec.get("columns") or []
            select = ", ".join(quote_ident(c["sql_name"]) for c in columns)
            rows = []
            try:
                cursor = conn.execute("SELECT %s FROM %s" % (
                    select, quote_ident(name)))
                rows = [list(row) for row in cursor.fetchall()]
            except sqlite3.Error as exc:
                spec["load_error"] = str(exc)
                continue
            tables.append({
                "name": name, "dataset": spec.get("dataset") or "generic",
                "source": spec.get("source") or "", "source_kind": spec.get("source_kind") or "",
                "columns": columns, "rows": rows,
                "n_rows": len(rows),
                "group_column": spec.get("group_column") or "",
                "target_column": spec.get("target_column") or "",
                "n_expected": spec.get("n_expected"),
            })
    finally:
        conn.close()
    return tables


def column_values(table, key):
    for index, column in enumerate(table["columns"]):
        if column.get("canonical") == key or column.get("sql_name") == key:
            return [row[index] for row in table["rows"]]
    return []


def column_index(table, key):
    for index, column in enumerate(table["columns"]):
        if column.get("canonical") == key or column.get("sql_name") == key:
            return index
    return None


def numeric_columns(table):
    return [c for c in table["columns"] if c.get("dtype") == "NUMERIC"]


def text_columns(table):
    return [c for c in table["columns"] if c.get("dtype") == "TEXT"]


def distinct_values(values, limit=200):
    seen, out = set(), []
    for value in values:
        if is_missing(value):
            continue
        key = str(value)
        if key not in seen:
            seen.add(key)
            out.append(key)
            if len(out) >= limit:
                break
    return out


def quality_profile(table):
    rows = table["rows"]
    columns = table["columns"]
    n_rows = len(rows)
    n_cols = len(columns)
    missing_cells = 0
    per_column = {}
    for index, column in enumerate(columns):
        values = [row[index] if index < len(row) else None for row in rows]
        missing = sum(1 for value in values if is_missing(value))
        missing_cells += missing
        entry = {
            "column": column.get("sql_name"), "canonical": column.get("canonical"),
            "dtype": column.get("dtype"), "unit": column.get("unit", ""),
            "n": n_rows, "missing": missing,
            "missing_rate": round(missing / n_rows, 6) if n_rows else None,
            "n_unique": len({str(v) for v in values if not is_missing(v)}),
        }
        if column.get("dtype") == "NUMERIC":
            numeric = [safe_float(v) for v in values]
            entry.update(describe(numeric))
            rule = RANGE_RULES.get(column.get("canonical") or "")
            if rule and entry.get("min") is not None:
                low, high = rule
                violations = [value for value in numeric
                              if value is not None and (value < low or value > high)]
                entry["range_rule"] = [low, high]
                entry["range_violations"] = len(violations)
            sentinels = {}
            for candidate in (-999.0, 999.0, 9999.0, -9999.0):
                count = sum(1 for value in numeric if value == candidate)
                if count:
                    sentinels[str(int(candidate))] = count
            if sentinels:
                entry["possible_sentinel_values"] = sentinels
        else:
            top = {}
            for value in values:
                if is_missing(value):
                    continue
                top[str(value)] = top.get(str(value), 0) + 1
            entry["top_values"] = sorted(top.items(), key=lambda item: (-item[1],
                                                                       item[0]))[:10]
        per_column[column.get("sql_name")] = entry
    exact = {}
    for row in rows:
        key = tuple("" if is_missing(v) else str(v) for v in row)
        exact[key] = exact.get(key, 0) + 1
    duplicate_rows = sum(count - 1 for count in exact.values() if count > 1)
    key_checks = []
    for index, column in enumerate(columns):
        name = normalize_name(column.get("sql_name"))
        if not (name == "id" or name.endswith("_id") or name in (
                "sample", "sample_no", "index", "record", "record_id")):
            continue
        values = [row[index] if index < len(row) else None for row in rows]
        seen, duplicate = set(), 0
        for value in values:
            if is_missing(value):
                continue
            key = str(value)
            if key in seen:
                duplicate += 1
            seen.add(key)
        key_checks.append({"column": column.get("sql_name"),
                           "distinct": len(seen), "duplicate_keys": duplicate})
    constant = [name for name, entry in per_column.items()
                if entry.get("n_unique") == 1 and entry.get("missing", 0) < n_rows]
    return {
        "n_rows": n_rows, "n_columns": n_cols,
        "missing_cells": missing_cells,
        "total_cells": n_rows * n_cols if n_cols else 0,
        "missing_rate": round(missing_cells / (n_rows * n_cols), 6)
                        if n_rows and n_cols else None,
        "duplicate_rows_exact": duplicate_rows,
        "duplicate_row_rate": round(duplicate_rows / n_rows, 6) if n_rows else None,
        "duplicate_key_checks": key_checks,
        "constant_columns": constant,
        "columns": per_column,
        "notes": [
            "重复行、缺失和异常值只做标记，不静默删除。",
            "异常值按 Tukey 1.5*IQR 与 |z|>3 两套口径分别计数。",
            "范围筛查使用保守的领域边界，越界只提示复核。",
        ],
    }


def dataset_overview(tables):
    out = []
    for table in tables:
        quality = quality_profile(table)
        numeric = [c for c in table["columns"] if c.get("dtype") == "NUMERIC"]
        out.append({
            "dataset": table["dataset"], "table": table["name"],
            "source": table["source"], "source_kind": table["source_kind"],
            "rows": table["n_rows"],
            "rows_expected": table.get("n_expected"),
            "rows_match_expected": (table.get("n_expected") is None
                                    or table["n_rows"] == table.get("n_expected")),
            "columns": len(table["columns"]),
            "numeric_columns": len(numeric),
            "text_columns": len(table["columns"]) - len(numeric),
            "missing_cells": quality["missing_cells"],
            "duplicate_rows_exact": quality["duplicate_rows_exact"],
            "group_column": table.get("group_column") or "",
            "target_column": table.get("target_column") or "",
        })
    return out


# ---------------------------------------------------------------------------
# Descriptive and inferential statistics
# ---------------------------------------------------------------------------


def basic_validation(tables, quality):
    checks = []
    for table in tables:
        expected = table.get("n_expected")
        checks.append({
            "check": "row_count:%s" % table["name"],
            "status": "pass" if (expected is None or table["n_rows"] == expected)
                      else "attention",
            "expected": expected, "actual": table["n_rows"],
        })
    for name, entry in (quality or {}).items():
        checks.append({
            "check": "duplicate_rows:%s" % name,
            "status": "attention" if entry.get("duplicate_rows_exact") else "pass",
            "value": entry.get("duplicate_rows_exact"),
        })
        checks.append({
            "check": "missing_cells:%s" % name,
            "status": "attention" if entry.get("missing_cells") else "pass",
            "value": entry.get("missing_cells"),
        })
    failed = [row for row in checks if row["status"] == "fail"]
    return {
        "status": "fail" if failed else "pass",
        "checks": checks,
        "method": ("公开快照行数核对 + 缺失/完全重复计数；"
                   "attention 表示需要在报告中说明，不等于运行失败。"),
    }


def dictionary_mapping(tables, sql=""):
    mapping = {}
    for table in tables:
        for column in table["columns"]:
            key = "%s.%s" % (table["name"], column["sql_name"])
            source = (column.get("dictionary_source")
                      or "builtin_profile_dictionary")
            mapping[key] = {
                "dictionary_field": key,
                "dictionary_description": column.get("description") or "",
                "source": source,
                "path": source,
                "locator": column.get("dictionary_locator") or "$.dictionary.%s" % key,
                "quote": column.get("dictionary_quote") or "",
                "schema_table": table["name"],
                "schema_column": column["sql_name"],
                "canonical": column.get("canonical", ""),
                "dtype": column.get("dtype", ""),
                "unit": column.get("unit", ""),
                "exists_in_schema": True,
                "appears_in_query": (normalize_name(column["sql_name"])
                                     in normalize_name(sql)) if sql else None,
                "appears_in_result": None,
            }
    return mapping


def age_band(value):
    numeric = safe_float(value)
    if numeric is None:
        return "missing"
    if numeric <= 28:
        return "<=28 d"
    if numeric <= 90:
        return "29-90 d"
    if numeric <= 180:
        return "91-180 d"
    return ">180 d"


def choose_grouping(table):
    """Return (group_column, labels, derived_kind)."""
    group_key = table.get("group_column") or ""
    if group_key and column_index(table, group_key) is not None:
        values = column_values(table, group_key)
        return group_key, [str(v) if not is_missing(v) else "missing" for v in values], "native"
    if table.get("dataset") == "concrete":
        age_index = column_index(table, "age")
        if age_index is not None:
            values = [row[age_index] if age_index < len(row) else None
                      for row in table["rows"]]
            return "age_band", [age_band(value) for value in values], "derived"
    # Generic fallback: first low-cardinality text column.
    for column in text_columns(table):
        values = column_values(table, column.get("sql_name"))
        distinct = distinct_values(values, limit=50)
        if 2 <= len(distinct) <= 12:
            return column.get("sql_name"), [
                str(v) if not is_missing(v) else "missing" for v in values], "generic"
    return "", [], "none"


def group_values(table, labels, group_key):
    mapping = {}
    for row_index, label in enumerate(labels):
        if label == "missing":
            continue
        mapping.setdefault(label, []).append(row_index)
    return mapping


def analyze_outcome(table, outcome_key, group_key, labels, grouping_kind):
    outcome_values = column_values(table, outcome_key)
    numeric = [safe_float(value) for value in outcome_values]
    column = next((c for c in table["columns"]
                   if c.get("canonical") == outcome_key), {})
    overall = describe(numeric)
    group_rows = group_values(table, labels, group_key) if group_key else {}
    groups = []
    for label in sorted(group_rows):
        values = [numeric[index] for index in group_rows[label]]
        groups.append((label, values))
    by_group = {label: describe(values) for label, values in groups}
    entry = {
        "outcome": outcome_key,
        "sql_column": column.get("sql_name"),
        "unit": column.get("unit", ""),
        "description": column.get("description", ""),
        "overall": overall, "by_group": by_group,
        "grouping": grouping_kind,
        "group_column": group_key,
    }
    if len(groups) < 2:
        entry["inference"] = {"status": "no_grouping",
                              "note": "没有可用的分组列，只报告描述统计与相关"}
        return entry
    anova = one_way_anova(groups)
    welch = welch_anova(groups)
    brown = brown_forsythe(groups)
    jb = {label: jarque_bera(values) for label, values in groups}
    primary = "welch_anova"
    if brown.get("p") is not None and brown.get("p") >= 0.05:
        primary = "classical_anova"
    primary_result = welch if primary == "welch_anova" else anova
    if primary_result.get("status") != "ok":
        primary_result = anova if anova.get("status") == "ok" else primary_result
        primary = "classical_anova" if primary_result is anova else primary
    pairwise = []
    if len(groups) <= 8:
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                test = welch_ttest(groups[i][1], groups[j][1])
                pairwise.append({
                    "group_a": groups[i][0], "group_b": groups[j][0],
                    "method": test.get("method", "Welch t"),
                    "n_a": test.get("n1"), "n_b": test.get("n2"),
                    "mean_a": test.get("mean1"), "mean_b": test.get("mean2"),
                    "diff": test.get("diff"), "ci95_diff": test.get("ci95_diff"),
                    "t": test.get("t"), "df": test.get("df"),
                    "p": test.get("p"), "cohen_d": test.get("cohen_d"),
                    "hedges_g": test.get("hedges_g"),
                    "status": test.get("status"),
                })
        adjusted = holm([row.get("p") for row in pairwise])
        for row, adjusted_p in zip(pairwise, adjusted):
            row["p_holm"] = round_or_none(adjusted_p)
            row["significant_holm_0_05"] = (adjusted_p is not None
                                            and adjusted_p < 0.05)
    normality_flags = [label for label, result in jb.items()
                       if result.get("p") is not None and result["p"] < 0.05]
    variance_flag = (brown.get("p") is not None and brown["p"] < 0.05)
    assumptions = {
        "normality_screen": "Jarque-Bera (screening only, not Shapiro-Wilk)",
        "normality_by_group": jb,
        "normality_flags_0_05": normality_flags,
        "variance_homogeneity": brown,
        "variance_flag_0_05": variance_flag,
        "sample_sizes": {label: len(values) for label, values in groups},
        "small_group_flag": [label for label, values in groups if len(values) < 5],
        "note": ("Jarque-Bera 与 Brown-Forsythe 都是筛查；"
                 "方差不齐时主检验改用 Welch ANOVA，"
                 "正态性偏离时结论按近似处理并在不确定性中说明。"),
    }
    entry["inference"] = {
        "status": "ok" if primary_result.get("status") == "ok" else primary_result.get("status"),
        "primary_test": primary,
        "primary": primary_result,
        "classical_anova": anova,
        "welch_anova": welch,
        "assumptions": assumptions,
        "pairwise_welch_holm": pairwise,
        "effect_size": {
            "eta_squared": anova.get("eta_squared"),
            "omega_squared": anova.get("omega_squared"),
            "interpretation": _eta_interpretation(anova.get("eta_squared")),
        },
        "conclusion": _outcome_conclusion(entry, primary_result, anova, pairwise),
    }
    return entry


def _eta_interpretation(eta2):
    if eta2 is None:
        return "无法计算"
    if eta2 < 0.01:
        return "极小"
    if eta2 < 0.06:
        return "小"
    if eta2 < 0.14:
        return "中等"
    return "大"


def _outcome_conclusion(entry, primary_result, anova, pairwise):
    if primary_result.get("status") != "ok" or primary_result.get("p") is None:
        return "检验不适用或无法计算，仅报告描述统计。"
    p = primary_result["p"]
    eta2 = anova.get("eta_squared")
    magnitude = _eta_interpretation(eta2)
    if p < 0.05:
        sig_pairs = [row for row in pairwise if row.get("significant_holm_0_05")]
        pair_text = "；".join("%s vs %s (p_holm=%s)" % (
            row["group_a"], row["group_b"], format_p(row["p_holm"]))
            for row in sig_pairs[:6])
        return ("按 %s，组间差异有统计显著性（p=%s，eta^2=%s，效应量%s）。"
                "这是组间关联描述，不构成因果结论。Holm 校正后显著的组对：%s。" % (
                    primary_result.get("method", "检验"), format_p(p), eta2,
                    magnitude, pair_text or "无"))
    return ("按 %s，未检出统计显著差异（p=%s，eta^2=%s）。"
            "不能据此证明组间相同；需要结合样本量与置信区间判断。" % (
                primary_result.get("method", "检验"), format_p(p), eta2))


def analyze_table_statistics(table):
    group_key, labels, grouping_kind = choose_grouping(table)
    target = table.get("target_column") or ""
    numeric = numeric_columns(table)
    outcomes = []
    for column in numeric:
        key = column.get("canonical") or column.get("sql_name")
        if key == group_key:
            continue
        outcomes.append(analyze_outcome(table, key, group_key, labels,
                                        grouping_kind))
    # Family-wise Holm correction across outcome-level primary tests.
    pvals = [entry.get("inference", {}).get("primary", {}).get("p")
             for entry in outcomes]
    adjusted = holm(pvals)
    for entry, adjusted_p in zip(outcomes, adjusted):
        inference = entry.get("inference") or {}
        inference["p_holm_across_outcomes"] = round_or_none(adjusted_p)
    correlations = []
    if target and column_index(table, target) is not None:
        target_values = [safe_float(v) for v in column_values(table, target)]
        for column in numeric:
            key = column.get("canonical") or column.get("sql_name")
            if key == target:
                continue
            values = [safe_float(v) for v in column_values(table, key)]
            result = pearson(values, target_values)
            result.update({"outcome": key, "target": target,
                           "unit": column.get("unit", "")})
            correlations.append(result)
        adjusted = holm([row.get("p") for row in correlations])
        for row, adjusted_p in zip(correlations, adjusted):
            row["p_holm"] = round_or_none(adjusted_p)
    sensitivity = None
    primary_outcome = None
    if outcomes:
        primary_outcome = max(outcomes, key=lambda entry: (
            entry.get("inference", {}).get("effect_size", {}).get("eta_squared")
            or -1.0))
        primary_key = primary_outcome["outcome"]
        if outcomes[0].get("grouping") in ("native", "derived") and group_key:
            values = [safe_float(v) for v in column_values(table, primary_key)]
            iqr_low = primary_outcome["overall"].get("q1")
            iqr_high = primary_outcome["overall"].get("q3")
            if iqr_low is not None and iqr_high is not None:
                iqr = iqr_high - iqr_low
                low, high = iqr_low - 1.5 * iqr, iqr_high + 1.5 * iqr
                kept_groups = {}
                for index, label in enumerate(labels):
                    if label == "missing" or values[index] is None:
                        continue
                    if low <= values[index] <= high:
                        kept_groups.setdefault(label, []).append(values[index])
                filtered = sorted(kept_groups.items())
                sensitivity = {
                    "analysis": "exclude IQR outliers (Tukey 1.5*IQR)",
                    "outcome": primary_key,
                    "removed": sum(1 for value in values
                                   if value is not None and (value < low or value > high)),
                    "anova": one_way_anova(filtered),
                    "note": "敏感性分析只用于判断异常值是否改变结论；主分析保留全部数据。",
                }
    return {
        "dataset": table["dataset"], "table": table["name"],
        "rows": table["n_rows"], "group_column": group_key,
        "grouping": grouping_kind, "outcomes": outcomes,
        "correlations": correlations, "sensitivity": sensitivity,
        "quality": quality_profile(table),
    }


def analyze_statistics(cfg, tables):
    reports = [analyze_table_statistics(table) for table in tables]
    results = []
    for report in reports:
        dataset = report["dataset"]
        table_name = report["table"]
        results.append(_metric("table_rows", report["rows"], "rows", dataset,
                               table_name, n=report["rows"]))
        quality = report["quality"]
        results.append(_metric("duplicate_rows_exact",
                               quality["duplicate_rows_exact"], "rows",
                               dataset, table_name, n=report["rows"]))
        results.append(_metric("missing_cells", quality["missing_cells"],
                               "cells", dataset, table_name,
                               n=quality["total_cells"]))
        for outcome in report["outcomes"]:
            key = outcome["outcome"]
            overall = outcome["overall"]
            results.append(_metric("%s_mean" % key, overall.get("mean"),
                                   outcome.get("unit", ""), dataset, table_name,
                                   n=overall.get("n"), outcome=key,
                                   analysis="descriptive"))
            results.append(_metric("%s_median" % key, overall.get("median"),
                                   outcome.get("unit", ""), dataset, table_name,
                                   n=overall.get("n"), outcome=key,
                                   analysis="descriptive"))
            results.append(_metric("%s_sd" % key, overall.get("sd"),
                                   outcome.get("unit", ""), dataset, table_name,
                                   n=overall.get("n"), outcome=key,
                                   analysis="descriptive"))
            inference = outcome.get("inference") or {}
            primary = inference.get("primary") or {}
            if primary.get("p") is not None:
                results.append(_metric("%s_primary_p" % key, primary.get("p"),
                                       "p-value", dataset, table_name,
                                       n=primary.get("n"), outcome=key,
                                       analysis=inference.get("primary_test"),
                                       p_value=primary.get("p"),
                                       p_adjusted=inference.get(
                                           "p_holm_across_outcomes")))
            effect = inference.get("effect_size") or {}
            if effect.get("eta_squared") is not None:
                results.append(_metric("%s_eta_squared" % key,
                                       effect.get("eta_squared"), "ratio",
                                       dataset, table_name,
                                       n=primary.get("n"), outcome=key,
                                       analysis="effect_size"))
            for pair in inference.get("pairwise_welch_holm") or []:
                results.append(_metric(
                    "%s_diff_%s_vs_%s" % (key, pair["group_a"], pair["group_b"]),
                    pair.get("diff"), outcome.get("unit", ""), dataset,
                    table_name,
                    n=((pair.get("n_a") or 0) + (pair.get("n_b") or 0)
                       if pair.get("status") == "ok" else None),
                    outcome=key, group="%s vs %s" % (pair["group_a"],
                                                     pair["group_b"]),
                    analysis="pairwise_welch", p_value=pair.get("p"),
                    p_adjusted=pair.get("p_holm"),
                    effect_size=pair.get("hedges_g"),
                    effect_type="Hedges g",
                    ci_low=(pair.get("ci95_diff") or [None, None])[0],
                    ci_high=(pair.get("ci95_diff") or [None, None])[1]))
        for corr in report["correlations"]:
            results.append(_metric("correlation_%s_vs_%s" % (
                corr["outcome"], corr["target"]), corr.get("r"), "r",
                dataset, table_name, n=corr.get("n"),
                outcome="%s vs %s" % (corr["outcome"], corr["target"]),
                analysis="pearson", p_value=corr.get("p"),
                p_adjusted=corr.get("p_holm")))
    results = [row for row in results if row.get("value") is not None]
    for index, row in enumerate(results, 1):
        row["id"] = "R%03d" % index
    quality = {table["name"]: quality_profile(table) for table in tables}
    payload = {
        "status": "ok",
        "case_id": cfg.get("case_id"),
        "mode": "statistics",
        "core_version": CORE_VERSION,
        "generated_at": now_iso(),
        "snapshot": cfg.get("snapshot_meta") or {},
        "datasets": dataset_overview(tables),
        "reports": reports,
        "results": results,
        "quality": quality,
        "schema_mapping": dictionary_mapping(tables),
        "validation": basic_validation(tables, quality),
        "uncertainties": _statistics_uncertainties(reports),
    }
    return payload


def _metric(metric, value, unit, dataset, table, n=None, outcome="",
            group="", analysis="", p_value=None, p_adjusted=None,
            effect_size=None, effect_type="", ci_low=None, ci_high=None):
    return {
        "id": "", "metric": metric, "value": round_or_none(value),
        "unit": unit or "", "dataset": dataset, "table": table,
        "outcome": outcome, "group": group, "analysis": analysis,
        "n": n if isinstance(n, int) else None,
        "p_value": round_or_none(p_value),
        "p_adjusted": round_or_none(p_adjusted),
        "effect_size": round_or_none(effect_size),
        "effect_type": effect_type,
        "ci_low": round_or_none(ci_low), "ci_high": round_or_none(ci_high),
    }


def _statistics_uncertainties(reports):
    uncertainties = []
    for report in reports:
        quality = report["quality"]
        if quality["missing_cells"]:
            uncertainties.append({
                "item": "missing_values",
                "reason": "%s.%s 有 %d 个缺失单元格" % (
                    report["dataset"], report["table"], quality["missing_cells"]),
                "impact": "每个统计量使用该变量的完整观测，n 可能不同",
                "action": "报告 n 与缺失率；不做静默插补",
            })
        if quality["duplicate_rows_exact"]:
            uncertainties.append({
                "item": "duplicate_rows",
                "reason": "%s.%s 检出 %d 条完全重复行" % (
                    report["dataset"], report["table"],
                    quality["duplicate_rows_exact"]),
                "impact": "重复行可能使样本量偏大",
                "action": "已保留并标记；确认是否为真实重复记录后再决定是否去重",
            })
        for outcome in report["outcomes"]:
            inference = outcome.get("inference") or {}
            assumptions = inference.get("assumptions") or {}
            if assumptions.get("normality_flags_0_05"):
                uncertainties.append({
                    "item": "normality_screen",
                    "reason": "%s 的组内 Jarque-Bera p<0.05：%s" % (
                        outcome["outcome"],
                        ", ".join(assumptions["normality_flags_0_05"])),
                    "impact": "参数检验的 p 值应按近似解释",
                    "action": "结合分组样本量与效应量；必要时改用非参数方法复核",
                })
            if assumptions.get("variance_flag_0_05"):
                uncertainties.append({
                    "item": "variance_homogeneity",
                    "reason": "%s 的 Brown-Forsythe p<0.05" % outcome["outcome"],
                    "impact": "经典 ANOVA 的等方差前提不满足",
                    "action": "主检验已优先使用 Welch ANOVA",
                })
            if assumptions.get("small_group_flag"):
                uncertainties.append({
                    "item": "small_group",
                    "reason": "%s 存在 n<5 的组：%s" % (
                        outcome["outcome"],
                        ", ".join(assumptions["small_group_flag"])),
                    "impact": "正态近似与小样本置信区间不稳定",
                    "action": "把结论标为探索性",
                })
    return uncertainties


# ---------------------------------------------------------------------------
# Database query mode
# ---------------------------------------------------------------------------


READ_ONLY_START = re.compile(r"^\s*(?:with\b|select\b|explain\b)", re.I)
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|"
    r"pragma|vacuum|reindex|grant|revoke)\b", re.I)


def check_sql(sql):
    text = (sql or "").strip()
    if not text:
        return "the query is empty"
    cleaned = re.sub(r"--[^\n]*", " ", text)
    cleaned = re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.S)
    if not READ_ONLY_START.match(cleaned):
        return "only SELECT/WITH/EXPLAIN queries are allowed"
    if FORBIDDEN.search(cleaned):
        return "read-only snapshot rejected a write/DDL keyword"
    if _statement_count(text) != 1:
        return "exactly one SQL statement is required"
    return ""


def _statement_count(sql):
    buffer, count = "", 0
    for char in sql:
        buffer += char
        if sqlite3.complete_statement(buffer):
            if re.sub(r"--[^\n]*", "", buffer).strip(" ;\n\t"):
                count += 1
            buffer = ""
    if re.sub(r"--[^\n]*", "", buffer).strip(" ;\n\t"):
        count += 1
    return count


def select_primary_table(cfg, tables):
    question = str(cfg.get("question_text") or "").lower()
    hints = [("concrete", ("concrete", "混凝土", "强度", "compressive")),
             ("iris", ("iris", "鸢尾", "sepal", "petal")),
             ("wine", ("wine", "葡萄酒", "alcohol", "flavanoid"))]
    for dataset, words in hints:
        if any(word in question for word in words):
            for table in tables:
                if table["dataset"] == dataset:
                    return table, "question_keyword"
    recognized = [table for table in tables if table["dataset"] != "generic"]
    pool = recognized or tables
    table = max(pool, key=lambda item: item["n_rows"])
    return table, "largest_table"


def build_auto_sql(cfg, table):
    target = cfg.get("query_target") or {}
    column_map = target.get("column_map") or {}
    table_name = target.get("table") or table["name"]

    def col(key):
        return quote_ident(column_map.get(key, key))

    def safe_col(key):
        return key in column_map or column_index(table, key) is not None

    dataset = table["dataset"]
    lines = []
    if dataset == "iris":
        features = [key for key in ("sepal_length", "sepal_width",
                                    "petal_length", "petal_width")
                    if safe_col(key)]
        lines.append("SELECT %s AS species, COUNT(*) AS n_rows" % col("species"))
        for key in features:
            lines.append(", ROUND(AVG(%s), 6) AS mean_%s" % (col(key), key))
            lines.append(", MIN(%s) AS min_%s, MAX(%s) AS max_%s" % (
                col(key), key, col(key), key))
        lines.append("FROM %s" % quote_ident(table_name))
        lines.append("GROUP BY %s ORDER BY %s;" % (col("species"), col("species")))
    elif dataset == "wine":
        features = [key for key in ("alcohol", "malic_acid", "flavanoids",
                                    "color_intensity", "proline")
                    if safe_col(key)]
        lines.append("SELECT %s AS wine_class, COUNT(*) AS n_rows"
                     % col("wine_class"))
        for key in features:
            lines.append(", ROUND(AVG(%s), 6) AS mean_%s" % (col(key), key))
        lines.append("FROM %s" % quote_ident(table_name))
        lines.append("GROUP BY %s ORDER BY %s;" % (
            col("wine_class"), col("wine_class")))
    elif dataset == "concrete":
        age_col = col("age")
        strength_col = col("compressive_strength")
        lines.append(
            "SELECT CASE WHEN %s <= 28 THEN '<=28 d' "
            "WHEN %s <= 90 THEN '29-90 d' WHEN %s <= 180 THEN '91-180 d' "
            "ELSE '>180 d' END AS age_band, COUNT(*) AS n_rows" % (
                age_col, age_col, age_col))
        lines.append(", ROUND(AVG(%s), 6) AS mean_compressive_strength"
                     % strength_col)
        lines.append(", MIN(%s) AS min_compressive_strength" % strength_col)
        lines.append(", MAX(%s) AS max_compressive_strength" % strength_col)
        for key in ("cement", "water"):
            if safe_col(key):
                lines.append(", ROUND(AVG(%s), 6) AS mean_%s" % (col(key), key))
        lines.append("FROM %s" % quote_ident(table_name))
        lines.append("GROUP BY age_band ORDER BY MIN(%s);" % age_col)
    else:
        group_key = table.get("group_column") or ""
        target_key = table.get("target_column") or ""
        if group_key and safe_col(group_key):
            lines.append("SELECT %s AS group_label, COUNT(*) AS n_rows"
                         % col(group_key))
            if target_key and safe_col(target_key):
                lines.append(", ROUND(AVG(%s), 6) AS mean_%s"
                             % (col(target_key), target_key))
            lines.append("FROM %s" % quote_ident(table_name))
            lines.append("GROUP BY %s ORDER BY %s;" % (
                col(group_key), col(group_key)))
        else:
            numeric = numeric_columns(table)
            lines.append("SELECT COUNT(*) AS n_rows")
            for column in numeric[:5]:
                key = column.get("canonical") or column.get("sql_name")
                lines.append(", ROUND(AVG(%s), 6) AS mean_%s" % (
                    quote_ident(column_map.get(key, column["sql_name"])), key))
            lines.append("FROM %s;" % quote_ident(table_name))
    return " ".join(lines)


def execute_sql(cfg, sql):
    target = cfg.get("query_target") or {}
    path = Path(target.get("path") or cfg.get("snapshot_path") or "")
    if not path.is_file():
        return None, [], [], "snapshot not found: %s" % path
    conn = connect_ro(path)
    try:
        started = time.perf_counter()
        cursor = conn.execute(sql)
        columns = [item[0] for item in (cursor.description or [])]
        rows = [list(row) for row in cursor.fetchall()]
        elapsed = (time.perf_counter() - started) * 1000.0
        return columns, rows, elapsed, ""
    except sqlite3.Error as exc:
        return None, [], 0.0, str(exc)
    finally:
        conn.close()


def group_coverage(cfg, table, columns, rows):
    target = cfg.get("query_target") or {}
    column_map = target.get("column_map") or {}
    group_key = table.get("group_column") or ""
    if not group_key:
        if table["dataset"] == "concrete":
            base = sorted({age_band(value) for value in
                           column_values(table, "age")})
            out_index = next((i for i, name in enumerate(columns)
                              if normalize_name(name) in ("age_band", "ageband")),
                             None)
            if out_index is None:
                return []
            returned = {str(row[out_index]) for row in rows}
            missing = [value for value in base if value not in returned]
            return [{"output_column": columns[out_index],
                     "source_table": table["name"], "source_column": "age_band",
                     "base_distinct": len(base), "output_distinct": len(returned),
                     "missing_groups": missing,
                     "status": "group_mismatch" if missing else "ok"}]
        return []
    base_values = sorted({str(value) for value in column_values(table, group_key)
                          if not is_missing(value)})
    out_index = next((i for i, name in enumerate(columns)
                      if normalize_name(name) in (
                          normalize_name(group_key),
                          normalize_name(column_map.get(group_key, group_key)))),
                     None)
    if out_index is None:
        return []
    returned = {str(row[out_index]) for row in rows}
    missing = [value for value in base_values if value not in returned]
    extra = sorted(returned - set(base_values))
    return [{"output_column": columns[out_index],
             "source_table": table["name"], "source_column": group_key,
             "base_distinct": len(base_values),
             "output_distinct": len(returned),
             "missing_groups": missing, "extra_groups": extra,
             "status": "group_mismatch" if missing else
                       ("extra_groups" if extra else "ok")}]


def cross_table_checks(tables):
    checks = []
    for left_index in range(len(tables)):
        for right_index in range(left_index + 1, len(tables)):
            left, right = tables[left_index], tables[right_index]
            left_cols = {normalize_name(c.get("sql_name")): c for c in left["columns"]}
            right_cols = {normalize_name(c.get("sql_name")): c for c in right["columns"]}
            common = sorted(set(left_cols) & set(right_cols))[:5]
            for name in common:
                left_values = [str(v) for v in column_values(
                    left, left_cols[name].get("sql_name")) if not is_missing(v)]
                right_values = [str(v) for v in column_values(
                    right, right_cols[name].get("sql_name")) if not is_missing(v)]
                left_counts, right_counts = {}, {}
                for value in left_values:
                    left_counts[value] = left_counts.get(value, 0) + 1
                for value in right_values:
                    right_counts[value] = right_counts.get(value, 0) + 1
                left_max = max(left_counts.values()) if left_counts else 0
                right_max = max(right_counts.values()) if right_counts else 0
                checks.append({
                    "left": left["name"], "right": right["name"], "key": name,
                    "left_rows_per_key_max": left_max,
                    "right_rows_per_key_max": right_max,
                    "one_to_one": left_max <= 1 and right_max <= 1,
                    "matched_keys": len(set(left_counts) & set(right_counts)),
                })
    return checks


def analyze_database_query(cfg, tables):
    primary, selection = select_primary_table(cfg, tables)
    target = cfg.get("query_target") or {}
    user_sql = read_text(cfg.get("user_sql_path") or "").strip()
    sql_problem = ""
    if user_sql:
        sql_problem = check_sql(user_sql)
        if sql_problem:
            user_sql = ""
    sql = user_sql or build_auto_sql(cfg, primary)
    if not sql.rstrip().endswith(";"):
        sql += ";"
    columns, rows, elapsed, error = execute_sql(cfg, sql)
    fallback_note = ""
    if error and user_sql:
        # The model-supplied SQL was syntactically read-only but failed on this
        # snapshot.  Keep the failure in the log and fall back to a generated
        # query instead of fabricating a result.
        fallback_note = "user SQL failed: %s" % error
        sql = build_auto_sql(cfg, primary)
        if not sql.rstrip().endswith(";"):
            sql += ";"
        columns, rows, elapsed, error = execute_sql(cfg, sql)
    if error:
        payload = {
            "status": "query_error", "case_id": cfg.get("case_id"),
            "mode": "database-query", "core_version": CORE_VERSION,
            "generated_at": now_iso(), "query": sql,
            "sql_sha256": sha256_text(sql), "error": error,
            "query_target": target, "snapshot": cfg.get("snapshot_meta") or {},
            "datasets": dataset_overview(tables),
            "results": [], "uncertainties": [{
                "item": "query_error", "reason": error,
                "impact": "没有结果表", "action": "修正 SQL 或使用自动生成的查询"}],
        }
        return payload
    quality = {table["name"]: quality_profile(table) for table in tables}
    coverage = group_coverage(cfg, primary, columns, rows)
    missing_groups = sorted({group for item in coverage
                             for group in item.get("missing_groups") or []})
    empty = {
        "is_empty": len(rows) == 0,
        "missing_groups": missing_groups,
        "coverage_checks": coverage,
        "statement": _empty_statement(len(rows), missing_groups),
    }
    if primary["dataset"] == "concrete":
        expected = len(distinct_values([age_band(v) for v in
                                        column_values(primary, "age")]))
    elif primary.get("group_column"):
        expected = len(distinct_values(column_values(primary, primary["group_column"])))
    else:
        expected = 1
    row_check = {
        "expected": expected, "actual": len(rows),
        "ok": expected == len(rows),
        "source": "auto: distinct groups in snapshot" if expected else "execution",
    }
    numeric_indexes = [index for index, name in enumerate(columns)
                       if rows and any(safe_float(row[index]) is not None
                                       for row in rows)]
    result_metrics = [{
        "id": "R001", "metric": "result_row_count", "value": len(rows),
        "unit": "rows", "evidence_key": "row_count",
    }]
    for index in numeric_indexes[:8]:
        values = [safe_float(row[index]) for row in rows]
        mean = statistics.fmean([v for v in values if v is not None]) if any(
            v is not None for v in values) else None
        result_metrics.append({
            "id": "R%03d" % (len(result_metrics) + 1),
            "metric": "column_%s_mean" % normalize_name(columns[index]),
            "value": round_or_none(mean), "unit": "",
            "n": sum(1 for v in values if v is not None),
            "evidence_key": "columns",
        })
    schema_mapping = dictionary_mapping(tables, sql)
    for entry in schema_mapping.values():
        entry["appears_in_result"] = any(
            normalize_name(name) == normalize_name(entry["schema_column"])
            for name in columns)
    primary_target = (target.get("table") or "")
    if not primary_target and primary:
        primary_target = primary["name"]
    snapshot = cfg.get("snapshot_meta") or {}
    joins = cross_table_checks(tables)
    uncertainties = _database_uncertainties(primary, quality, empty, joins,
                                            sql_problem, fallback_note)
    payload = {
        "status": "ok",
        "case_id": cfg.get("case_id"),
        "mode": "database-query",
        "core_version": CORE_VERSION,
        "generated_at": now_iso(),
        "dataset_selection": selection,
        "primary_table": primary["name"],
        "query_target": target,
        "snapshot": snapshot,
        "query": sql,
        "sql_sha256": sha256_text(sql),
        "permissions": {
            "status": "read_only", "mode": "ro+query_only",
            "write_allowed": False,
            "notes": "查询在只读快照上执行；未写入源数据库。",
        },
        "columns": columns, "row_count": len(rows),
        "rows": rows[:2000], "preview": rows[:50],
        "truncated_rows": max(0, len(rows) - 2000),
        "row_count_check": row_check,
        "empty_result": empty,
        "schema_mapping": schema_mapping,
        "join_checks": joins,
        "datasets": dataset_overview(tables),
        "quality": quality,
        "results": result_metrics,
        "execution": {
            "elapsed_ms": round(elapsed, 3),
            "sqlite": sqlite3.sqlite_version,
            "python": platform.python_version(),
            "returncode": 0,
        },
        "uncertainties": uncertainties,
    }
    return payload


def _empty_statement(row_count, missing_groups):
    if row_count == 0:
        return ("查询在记录的快照上返回 0 行；按空结果报告，"
                "不代表筛选条件之外不存在数据。")
    if missing_groups:
        return ("已登记但没有返回行的组：%s；已在空结果中显式保留。"
                % ", ".join(missing_groups))
    return "未检出空组或空结果。"


def _database_uncertainties(primary, quality, empty, joins, sql_problem,
                            fallback_note=""):
    uncertainties = []
    if sql_problem:
        uncertainties.append({
            "item": "user_sql_rejected", "reason": sql_problem,
            "impact": "未执行用户 SQL，改用自动生成的只读查询",
            "action": "把 SQL 改成单条 SELECT/WITH 后重跑",
        })
    if fallback_note:
        uncertainties.append({
            "item": "query_fallback", "reason": fallback_note,
            "impact": "用户 SQL 未产生结果，已改用自动生成的查询",
            "action": "核对列名/表名后重写 work/query.sql",
        })
    if empty["is_empty"]:
        uncertainties.append({
            "item": "empty_result", "reason": empty["statement"],
            "impact": "结果表没有数据行",
            "action": "核对筛选条件或快照版本",
        })
    if empty["missing_groups"]:
        uncertainties.append({
            "item": "missing_groups",
            "reason": "未返回的已登记组：%s" % ", ".join(empty["missing_groups"]),
            "impact": "组覆盖不完整",
            "action": "在报告中保留空组，不把未返回写成不存在",
        })
    if primary and primary.get("n_expected") and primary["n_rows"] != primary["n_expected"]:
        uncertainties.append({
            "item": "row_count_mismatch",
            "reason": "%s 实际 %d 行，公开快照应为 %d 行" % (
                primary["name"], primary["n_rows"], primary["n_expected"]),
            "impact": "快照可能被截断或换版",
            "action": "核对快照来源与哈希",
        })
    risky = [row for row in joins if not row["one_to_one"]]
    if risky:
        uncertainties.append({
            "item": "join_fanout",
            "reason": "检测到共享键的非一对一关系：%s" % "; ".join(
                "%s.%s<->%s.%s" % (row["left"], row["key"],
                                   row["right"], row["key"]) for row in risky),
            "impact": "直接连接可能造成行数膨胀",
            "action": "连接前先按问题所需的粒度聚合",
        })
    if len(quality) == 1:
        uncertainties.append({
            "item": "cross_table_scope",
            "reason": "快照中只有一个可识别表",
            "impact": "跨表连接分析不适用",
            "action": "如需跨表结论，提供共享键的关联表",
        })
    return uncertainties


# ---------------------------------------------------------------------------
# Code-generation mode: statistics plus independent validation
# ---------------------------------------------------------------------------


def validation_checks(cfg, tables):
    checks = []
    path = Path(cfg.get("snapshot_path") or "")
    if not path.is_file():
        return [{"check": "snapshot_exists", "status": "fail",
                 "detail": str(path)}]
    conn = connect_ro(path)
    try:
        for table in tables:
            sql_count = table_count(conn, table["name"])
            checks.append({
                "check": "row_count:%s" % table["name"],
                "status": "pass" if sql_count == table["n_rows"] else "fail",
                "sql": sql_count, "python": table["n_rows"],
            })
            for column in numeric_columns(table):
                values = [safe_float(v) for v in
                          column_values(table, column["sql_name"])]
                clean = [v for v in values if v is not None]
                python_mean = statistics.fmean(clean) if clean else None
                try:
                    sql_mean = conn.execute(
                        "SELECT AVG(%s) FROM %s" % (
                            quote_ident(column["sql_name"]),
                            quote_ident(table["name"]))).fetchone()[0]
                except sqlite3.Error:
                    sql_mean = None
                if python_mean is None or sql_mean is None:
                    status = "skipped"
                else:
                    tolerance = max(1e-9, abs(python_mean) * 1e-6)
                    status = ("pass" if abs(python_mean - sql_mean) <= tolerance
                              else "fail")
                checks.append({
                    "check": "mean:%s.%s" % (table["name"],
                                             column["sql_name"]),
                    "status": status,
                    "sql": round_or_none(sql_mean),
                    "python": round_or_none(python_mean),
                })
    finally:
        conn.close()
    return checks


def self_checks():
    checks = []

    def record(name, condition, detail):
        checks.append({"check": name, "status": "pass" if condition else "fail",
                       "detail": detail})

    empty = describe([])
    record("empty_values", empty.get("n") == 0 and empty.get("mean") is None,
           "describe([]) returns n=0 without raising")
    record("missing_column", column_index({"columns": []}, "nope") is None,
           "unknown column is reported as absent")
    record("non_numeric", safe_float("not-a-number") is None,
           "non-numeric text is not coerced to 0")
    zero = welch_ttest([1, 1, 1], [1, 1, 1])
    record("zero_variance", zero.get("status") == "zero_variance",
           "zero-variance t test is undefined, not p=0")
    too_small = one_way_anova([("a", [1.0]), ("b", [2.0])])
    record("insufficient_n", too_small.get("status") == "insufficient_groups",
           "insufficient group size is reported, not fabricated")
    record("read_only_guard", bool(check_sql("DELETE FROM t")) and
           check_sql("SELECT 1") == "",
           "write SQL is rejected and SELECT is accepted")
    return checks


def analyze_code_generation(cfg, tables):
    payload = analyze_statistics(cfg, tables)
    payload["mode"] = "code-generation"
    checks = validation_checks(cfg, tables) + self_checks()
    failures = [row for row in checks if row["status"] == "fail"]
    payload["validation"] = {
        "status": "pass" if not failures else "fail",
        "checks": checks,
        "failed": len(failures),
        "method": ("同一快照上独立执行 SQL 聚合与 Python 计算；"
                   "同时验证空输入、缺列、非数值、零方差与只读守卫。"),
    }
    payload["execution_contract"] = {
        "entrypoint": "work/analysis.py",
        "config": "work/uci-run.json",
        "dependencies": ["python-stdlib"],
        "optional_dependencies": ["numpy (verification only)"],
        "inputs": [row.get("source") for row in payload.get("datasets") or []],
        "outputs": ["output/results.json", "output/results.csv",
                    "output/statistics-report.json", "output/environment.json"],
        "error_policy": ("缺数据/缺列/零方差/空结果时写出 status 与原因，"
                         "不静默补值；非零退出只用于不可恢复的 I/O 故障。"),
    }
    return payload


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def write_environment(cfg, payload):
    numpy_version = ""
    try:
        import numpy  # noqa: F401
        numpy_version = numpy.__version__
    except Exception:  # noqa: BLE001
        numpy_version = ""
    environment = {
        "core_version": CORE_VERSION,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "sqlite_version": sqlite3.sqlite_version,
        "numpy_available": bool(numpy_version),
        "numpy_version": numpy_version,
        "dependency_policy": "standard library only; numpy optional and never required",
        "random_seed": None,
        "deterministic": True,
        "encoding": "utf-8",
        "generated_at": now_iso(),
        "command": "python work/analysis.py",
        "config": "work/uci-run.json",
    }
    payload["environment"] = environment
    return environment


def write_results_csv(path, results):
    fields = ["case_id", "dataset", "table", "analysis", "outcome", "group",
              "metric", "value", "unit", "n", "p_value", "p_adjusted",
              "effect_size", "effect_type", "ci_low", "ci_high"]
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: ("" if row.get(field) is None
                                     else row.get(field)) for field in fields})
    return path


def write_cleaned_table(cfg, tables, path):
    """A transparent, non-destructive cleaned view of the primary table."""
    if not tables:
        write_text(path, "case_id,note\n%s,no readable table\n" % cfg.get("case_id"))
        return
    primary = select_primary_table(cfg, tables)[0]
    columns = primary["columns"]
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([column["sql_name"] for column in columns]
                        + ["__missing_cells", "__exact_duplicate"])
        seen = {}
        for row in primary["rows"]:
            normalized = ["" if is_missing(value) else str(value) for value in row]
            key = tuple(normalized)
            seen[key] = seen.get(key, 0) + 1
            writer.writerow(normalized + [
                sum(1 for value in row if is_missing(value)),
                1 if seen[key] > 1 else 0,
            ])
    return path


def write_query_table(path, columns, rows):
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if value is None else value for value in row])
    return path


def write_outputs(cfg, payload):
    output = Path(cfg.get("output_dir") or ".")
    output.mkdir(parents=True, exist_ok=True)
    mode = payload.get("mode") or cfg.get("mode")
    environment = write_environment(cfg, payload)
    if mode == "database-query":
        write_text(output / "query.sql", (payload.get("query") or
                                          "-- no query was executed\n"))
        write_query_table(output / "result-table.csv",
                          payload.get("columns") or ["status", "message"],
                          payload.get("rows") or (
                              [[payload.get("status", "no_result"),
                                payload.get("error", "no rows")]]
                              if payload.get("status") != "ok" else []))
    else:
        write_results_csv(output / "results.csv", payload.get("results") or [])
        write_json(output / "statistics-report.json", {
            "case_id": payload.get("case_id"),
            "mode": mode,
            "status": payload.get("status"),
            "generated_at": payload.get("generated_at"),
            "datasets": payload.get("datasets") or [],
            "reports": payload.get("reports") or [],
            "validation": payload.get("validation") or {},
            "uncertainties": payload.get("uncertainties") or [],
            "environment": environment,
        })
        write_cleaned_table(cfg, load_snapshot_tables(cfg), output / "cleaned-table.csv")
        write_json(output / "quality-report.json", payload.get("quality") or {})
        write_json(output / "cleaning-rules.json", {
            "case_id": payload.get("case_id"),
            "rules": [
                {"rule": "missing_as_missing", "action": "不插补、不当 0；按变量报告 n"},
                {"rule": "duplicates_retained", "action": "完全重复行保留并计数"},
                {"rule": "outliers_flagged", "action": "Tukey 1.5*IQR 与 |z|>3 仅标记"},
                {"rule": "range_screening", "action": "越界值提示复核，不删除"},
                {"rule": "complete_case_per_variable",
                 "action": "每个统计量使用该变量的完整观测"},
            ],
        })
    write_json(output / "environment.json", environment)
    write_json(output / "results.json", payload)
    write_json(output / "core-output.json", payload)
    write_json(Path(cfg.get("work_dir") or ".") / "core-output.json", payload)
    return payload


def run_mode(cfg):
    tables = load_snapshot_tables(cfg)
    if not tables:
        return {
            "status": "input_error", "case_id": cfg.get("case_id"),
            "mode": cfg.get("mode"), "core_version": CORE_VERSION,
            "generated_at": now_iso(),
            "error": "no readable table in the recorded snapshot",
            "datasets": [], "results": [],
            "uncertainties": [{
                "item": "no_data", "reason": "快照中没有可读表",
                "impact": "无法产生查询或统计结果",
                "action": "提供 CSV/SQLite 数据快照后重跑"}],
        }
    mode = cfg.get("mode") or "statistics"
    if mode == "database-query":
        return analyze_database_query(cfg, tables)
    if mode == "code-generation":
        return analyze_code_generation(cfg, tables)
    return analyze_statistics(cfg, tables)


def main(argv=None):
    parser = argparse.ArgumentParser(description="UCI profile analysis pipeline")
    parser.add_argument("--config", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    config_path = Path(args.config) if args.config else (
        Path(__file__).resolve().with_name("uci-run.json"))
    cfg = load_json(config_path, {})
    if not cfg:
        # Last-resort legal output so the harness still has a readable result.
        fallback = {
            "status": "input_error", "case_id": "CASE", "mode": "unknown",
            "error": "uci-run.json not found or unreadable: %s" % config_path,
            "generated_at": now_iso(), "results": [],
        }
        target = Path(os.environ.get("SKILL_OUTPUT", "."))
        target.mkdir(parents=True, exist_ok=True)
        write_json(target / "results.json", fallback)
        print(json.dumps(fallback, ensure_ascii=False))
        return 0
    if args.mode:
        cfg["mode"] = args.mode
    started = time.perf_counter()
    try:
        payload = run_mode(cfg)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "status": "input_error", "case_id": cfg.get("case_id"),
            "mode": cfg.get("mode"), "core_version": CORE_VERSION,
            "generated_at": now_iso(), "error": str(exc),
            "traceback": traceback.format_exc()[-2000:], "results": [],
            "uncertainties": [{
                "item": "analysis_failure", "reason": str(exc),
                "impact": "输出不完整", "action": "查看 execution log"}],
        }
    payload["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    write_outputs(cfg, payload)
    summary = {
        "core": CORE_VERSION, "case_id": payload.get("case_id"),
        "mode": payload.get("mode"), "status": payload.get("status"),
        "datasets": len(payload.get("datasets") or []),
        "results": len(payload.get("results") or []),
        "rows": payload.get("row_count"),
        "snapshot_sha256": (payload.get("snapshot") or {}).get("sha256"),
        "validation": (payload.get("validation") or {}).get("status"),
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
