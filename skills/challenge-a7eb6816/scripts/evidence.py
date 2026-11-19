#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a locatable evidence ledger from the inventoried material.

Usage:
    python3 evidence.py --input /input --input /input --work /app/work [--json]

Every entry keeps the source path, a line/page locator, a verbatim quote and
an evidence tier (fact / inference / speculation).  Restricted sentences are
kept in the ledger but marked ``disclosable=false`` so they never leak into
the outbound draft.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # package mode (python -m scripts.evidence)
    from . import transfer_kit as kit
except ImportError:  # direct mode (python scripts/evidence.py)
    kit = __import__("transfer_kit")

UNIT_ALT = (
    r"mol%|vol%|wt%|at%|mg/kg|μg/kg|ug/kg|mg/mL|μg/mL|ug/mL|mg/L|g/L|"
    r"mmol/L|mol/L|μmol|umol|nmol|mmol|mol|μg|ug|ng|pg|mg|kg|ml|μl|ul|l|"
    r"kda|da|kbp|bp|kb|gb|mb|ppm|ppb|rpm|r/min|iu|"
    r"°c|℃|k|mbar|mpa|kpa|pa|bar|hrs|hr|min|ms|"
    r"h|d|wk|mo|s|a|"
    r"小时|分钟|毫秒|秒|天|周|个月|月|年|次|例|人|只|个|倍|"
    r"毫伏|千伏|伏|毫安|安|千瓦|兆瓦|瓦|千赫兹|兆赫兹|吉赫兹|赫兹|"
    r"分贝|纳米|微米|毫米|厘米|千米|米|毫升|微升|升|毫克|微克|纳克|克|千克|公斤|"
    r"毫摩尔|微摩尔|摩尔|"
    r"m2|m3|cm2|cm3|m²|m³|cm²|cm³|"
    r"mv|kv|v|ma|kw|mw|w|khz|mhz|ghz|hz|db|nm|um|mm|cm|km|m|"
    r"%|‰|×"
)
NUM_UNIT_RE = re.compile(
    r"(?<![\w\[\.])(\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?|\.\d+)\s*("
    + UNIT_ALT + r")(?![\w])", re.I)
RANGE_RE = re.compile(
    r"(?<![\w\[\.])(\d+(?:\.\d+)?)\s*[–—~～\-至到]\s*(\d+(?:\.\d+)?)\s*("
    + UNIT_ALT + r")(?![\w])", re.I)
SPECIAL_RE = re.compile(
    r"\b(?:n|N)\s*=\s*(\d+)|(?:\bp\s*[<>=]\s*0?\.\d+)|"
    r"(?:R\s*(?:2|²)\s*=\s*0?\.\d+)|(?:pH\s*(?:=|\s)\s*\d+(?:\.\d+)?)", re.I)

RESULT_VERBS = re.compile(
    r"发现|表明|显示|证明|提示|认为|提出|观察到|测得|达到|提高|降低|增加|"
    r"减少|显著|相关|导致|改善|抑制|促进|支持|验证|实现|获得|优于|高于|低于|"
    r"表明|说明|found|showed|shows|demonstrat|increas|decreas|reduc|"
    r"improv|achiev|report|observ|suggest|indicat|correlat|caus", re.I)
SPECULATION = re.compile(
    r"可能|有望|预计|预测|假设|前景|未来|潜在|推测|估计|或许|或可|倘若|"
    r"\bmay\b|\bmight\b|\bcould\b|\bwould\b|potential|forecast|expected|"
    r"speculat|likely|probably", re.I)
INFERENCE = re.compile(
    r"提示|表明|说明|支持|推断|由此|因此|意味着|可见|consistent\s+with|"
    r"suggest|indicate|imply|therefore|thus", re.I)
LIMITATION = re.compile(
    r"局限|限制|不足|尚未|未做|未进行|需要进一步|有待|不能|无法|仅|只在|仅限|"
    r"样本量小|初步|未验证|待验证|limitation|further|not\s+yet|only|"
    r"preliminary|small\s+sample|remain", re.I)
RESTRICTED = re.compile(
    r"保密|confidential|未公开|内部资料|内部使用|禁止披露|不得外传|不得公开|"
    r"proprietary|embargoed|trade\s+secret", re.I)
DEFINITION = re.compile(
    r"是指|定义为|称为|简称|即指|refers?\s+to|defined\s+as|called", re.I)
CONDITION_RES = [
    re.compile(r"(?:在|于)?\s*\d+(?:\.\d+)?\s*(?:°C|℃|K|bar|mbar|MPa|kPa|Pa|"
               r"V|mA|h|min|s|小时|分钟|天|周|月|年)(?:[^，。；\n]{0,20})", re.I),
    re.compile(r"\bn\s*=\s*\d+", re.I),
    re.compile(r"(?:实验室|小试|中试|工业|现场|临床|动物实验|细胞实验|"
               r"体外|体内|模拟|示范)(?:规模|条件|环境|阶段|验证)?", re.I),
    re.compile(r"(?:样本量|样本|受试者|患者|小鼠|大鼠|细胞|批次|重复)"
               r"\s*(?:为|共|n\s*=\s*)?\d+", re.I),
    re.compile(r"pH\s*(?:=|\s)?\d+(?:\.\d+)?", re.I),
]
COMPARISON = re.compile(
    r"相比|相较于|与[^，。；\n]{1,24}相比|对比|参比|对照|相同条件|"
    r"高于|低于|优于|劣于|增加到|降至|从[^，。；\n]{1,20}(?:到|至)[^，。；\n]{1,20}|分别|"
    r"compared|versus|vs\.?|higher|lower|increase|decrease", re.I)
DENOMINATOR = re.compile(
    r"占[^，。；\n]{0,20}|其中[^，。；\n]{0,20}|总计|合计|共\s*\d+|"
    r"denominator|out\s+of|of\s+the\s+\d+", re.I)
MATURITY_RULES = [
    ("commercial", re.compile(r"量产|商业化|工业规模|commercial|mass\s+production", re.I)),
    ("field", re.compile(r"现场|field\s+(?:test|trial)", re.I)),
    ("clinical", re.compile(r"临床|clinical", re.I)),
    ("prototype", re.compile(r"原型|样机|prototype", re.I)),
    ("pilot", re.compile(r"中试|pilot", re.I)),
    ("bench", re.compile(r"小试|bench", re.I)),
    ("lab", re.compile(r"实验室|lab(?:oratory)?", re.I)),
]
METRIC = re.compile(
    r"转化率|选择性|产率|收率|效率|纯度|强度|容量|活性|稳定性|寿命|"
    r"conversion|selectivity|yield|efficiency|purity|strength|capacity|"
    r"activity|stability|lifetime", re.I)
CITATION_PATTERNS = [
    ("doi", re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")),
    ("bracket", re.compile(r"\[\d+(?:\s*[,–\-]\s*\d+)*\]")),
    ("author_year", re.compile(r"\([A-Z][A-Za-z\-]+(?:\s+et\s+al\.)?,?\s*"
                               r"(?:19|20)\d{2}[a-z]?\)")),
    ("figure", re.compile(r"(?:图|Figure|Fig\.?)\s*\d+[A-Za-z]?", re.I)),
    ("table", re.compile(r"(?:表|Table)\s*\d+[A-Za-z]?", re.I)),
]
REFERENCE_LINE = re.compile(r"^\s*(?:\[\d+\]|\d+\.)\s+\S")
ABBREV_RE = re.compile(
    r"([一-鿿A-Za-z][一-鿿A-Za-z0-9\- ]{1,40})[（(]([A-Za-z][A-Za-z0-9\-]{1,12})[）)]")
TERM_DEF_RE = re.compile(r"([一-鿿]{2,14})(?:是指|定义为|即)([^。；\n]{2,60})")


def extract_quantities(text):
    text = kit.nfkc(text)
    out = []
    occupied = []
    for m in RANGE_RE.finditer(text):
        unit = m.group(3)
        for value in (m.group(1), m.group(2)):
            out.append({"raw": m.group(0), "value": kit.normalize_number(value),
                        "unit": unit, "normalized_unit": kit.normalize_unit(unit),
                        "comparison": "range"})
        occupied.append(m.span())
    for m in NUM_UNIT_RE.finditer(text):
        if any(m.start() >= a and m.end() <= b for a, b in occupied):
            continue
        unit = m.group(2)
        out.append({"raw": m.group(0), "value": kit.normalize_number(m.group(1)),
                    "unit": unit, "normalized_unit": kit.normalize_unit(unit),
                    "comparison": None})
    for m in SPECIAL_RE.finditer(text):
        raw = m.group(0)
        if "pH" in raw:
            unit, value = "pH", re.sub(r"[^\d.]", "", raw)
        elif re.search(r"\bn\s*=", raw, re.I):
            unit, value = "n", re.sub(r"[^\d]", "", raw)
        elif re.search(r"R\s*(?:2|²)", raw):
            unit, value = "R2", re.sub(r"^[^=]*=\s*", "", raw)
        else:
            unit, value = "p", re.sub(r"^[^<>=]*[<>=]\s*", "", raw)
        out.append({"raw": raw, "value": kit.normalize_number(value),
                    "unit": unit, "normalized_unit": kit.normalize_unit(unit),
                    "comparison": None})
    dedup, seen = [], set()
    for q in out:
        key = (q["value"], q["normalized_unit"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(q)
    return dedup


def extract_conditions(text):
    found = []
    for pat in CONDITION_RES:
        for m in pat.finditer(text):
            found.append(kit.normalize_space(m.group(0)).strip("，。；,; "))
    return kit.unique(found)[:8]


def extract_comparison(text):
    m = COMPARISON.search(text)
    return kit.excerpt(m.group(0), 120) if m else None


def extract_denominator(text):
    m = DENOMINATOR.search(text)
    return kit.excerpt(m.group(0), 120) if m else None


def detect_maturity(text):
    levels = []
    for name, pat in MATURITY_RULES:
        if pat.search(text):
            levels.append(name)
    if not levels:
        return "unknown"
    return levels[-1] if "commercial" not in levels else "commercial"


def classify(sentence, quantities, is_reference=False):
    if is_reference or REFERENCE_LINE.match(sentence):
        return {"kind": "citation", "tier": "fact", "disclosable": True}
    if RESTRICTED.search(sentence):
        return {"kind": "restricted_fact", "tier": "fact", "disclosable": False,
                "restricted_reason": "material marks this as confidential/unpublished"}
    if DEFINITION.search(sentence):
        return {"kind": "definition", "tier": "fact", "disclosable": True}
    if SPECULATION.search(sentence):
        return {"kind": "speculation", "tier": "speculation", "disclosable": True}
    if LIMITATION.search(sentence):
        return {"kind": "limitation", "tier": "fact", "disclosable": True}
    if INFERENCE.search(sentence):
        return {"kind": "inference", "tier": "inference", "disclosable": True}
    if quantities:
        return {"kind": "quantitative", "tier": "fact", "disclosable": True}
    if RESULT_VERBS.search(sentence):
        return {"kind": "observation", "tier": "fact", "disclosable": True}
    return {"kind": "author_claim", "tier": "fact", "disclosable": True}


def score_evidence(entry):
    score = 0
    if entry.get("quantities"):
        score += 3
    if RESULT_VERBS.search(entry["statement"]):
        score += 2
    if entry.get("comparison"):
        score += 1
    if entry.get("conditions"):
        score += 1
    if entry["kind"] == "limitation":
        score += 1
    if entry["kind"] == "inference":
        score += 1
    if entry["kind"] == "speculation":
        score += 1
    if len(entry["statement"]) > 420:
        score -= 2
    return score


def extract_citations(text):
    found = []
    for kind, pat in CITATION_PATTERNS:
        for m in pat.finditer(text):
            found.append({"kind": kind, "raw": m.group(0).strip()})
    for line in text.splitlines():
        if REFERENCE_LINE.match(line) and len(line.strip()) > 12:
            found.append({"kind": "reference_entry", "raw": line.strip()[:300]})
    dedup, seen = [], set()
    for c in found:
        key = (c["kind"], kit.normalize_space(c["raw"]).lower())
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)
    return dedup[:80]


def extract_terminology(text):
    terms = []
    for m in ABBREV_RE.finditer(text):
        term, abbrev = m.group(1).strip(" ，,。；;：:"), m.group(2)
        if len(term) >= 2:
            terms.append({"term": term, "abbreviation": abbrev,
                          "definition": None, "source": "explicit_expansion"})
    for m in TERM_DEF_RE.finditer(text):
        terms.append({"term": m.group(1), "abbreviation": None,
                      "definition": m.group(2).strip(), "source": "definition"})
    dedup, seen = [], set()
    for t in terms:
        key = (t["term"].lower(), (t["abbreviation"] or "").lower())
        if key in seen:
            continue
        seen.add(key)
        dedup.append(t)
    return dedup[:40]


CONFLICT_WORDS = re.compile(
    r"矛盾|不一致|冲突|contradict|inconsistent|discrepan", re.I)


def _condition_signature(entry):
    signature = []
    for cond in entry.get("conditions") or []:
        text = kit.normalize_space(cond).lower()
        if text:
            signature.append(text)
    return tuple(sorted(set(signature)))


def _metric_quantity_pairs(entry):
    """Attach each quantity to its nearest metric word, not to every metric."""
    statement = entry.get("statement", "")
    metrics = [(m.start(), m.group(0).lower()) for m in METRIC.finditer(statement)]
    if not metrics:
        return []
    pairs = []
    for q in entry.get("quantities") or []:
        raw = q.get("raw") or ""
        pos = statement.find(raw)
        if pos < 0:
            pos = 0
        metric = min(metrics, key=lambda item: abs(item[0] - pos))[1]
        pairs.append((metric, q))
    return pairs


def detect_conflicts(entries):
    conflicts = []
    for entry in entries:
        if CONFLICT_WORDS.search(entry.get("statement", "")):
            conflicts.append({
                "item": "possible_data_conflict",
                "metric": "explicit_wording",
                "unit": None,
                "values": [q["raw"] for q in entry.get("quantities") or []],
                "conditions": entry.get("conditions") or [],
                "source": entry.get("source"),
                "action": "source itself signals a conflict; present both readings and do not pick one",
            })
    # Only compare values that share the same metric, unit and condition
    # signature, and that are not explicitly a comparison/control.  This keeps
    # a legitimate condition series (350/400/450 °C) from being called a
    # contradiction.
    groups = {}
    for entry in entries:
        if not entry.get("quantities") or entry["kind"] not in ("quantitative", "observation"):
            continue
        if entry.get("comparison"):
            continue
        if re.search(r"参比|对照|相比|对比|高于|低于|优于|相同条件", entry["statement"]):
            continue
        signature = _condition_signature(entry)
        for metric, q in _metric_quantity_pairs(entry):
            key = (metric, q["normalized_unit"], signature)
            groups.setdefault(key, set()).add(q["value"])
    for (metric, unit, signature), values in groups.items():
        if len(values) > 1:
            conflicts.append({
                "item": "possible_data_conflict",
                "metric": metric,
                "unit": unit,
                "values": sorted(values),
                "conditions": list(signature),
                "action": "same metric/unit/conditions has multiple values; re-check the table before quoting",
            })
    return conflicts


def extract(inventory, work_dir, workorder=None):
    work_dir = Path(work_dir)
    sources = []
    entries = []
    uncertainty = list(inventory.get("uncertainty") or [])
    for f in inventory.get("files", []):
        if f.get("role") == "task_metadata":
            continue
        if not f.get("readable") or not f.get("text_path"):
            continue
        text_path = work_dir / f["text_path"]
        try:
            text = kit.read_text(text_path)
        except OSError as exc:
            uncertainty.append({"item": "text_read_error", "path": f["path"],
                                "detail": str(exc), "action": "mark draft partial"})
            continue
        sources.append({"path": f["path"], "sha256": f["sha256"],
                        "chars": len(text), "locator_style": f.get("locator_style")})
        units = kit.paragraph_units(
            text, page_style=(f.get("locator_style") == "page"))
        for unit in units:
            for sentence in kit.split_sentences(unit["text"]):
                if len(sentence) < 12:
                    continue
                quantities = extract_quantities(sentence)
                is_ref = bool(REFERENCE_LINE.match(sentence))
                if not (quantities or RESULT_VERBS.search(sentence)
                        or LIMITATION.search(sentence) or SPECULATION.search(sentence)
                        or INFERENCE.search(sentence) or DEFINITION.search(sentence)
                        or RESTRICTED.search(sentence) or is_ref):
                    continue
                cls = classify(sentence, quantities, is_ref)
                entry = {
                    "statement": kit.normalize_space(sentence),
                    "quote": kit.normalize_space(sentence),
                    "kind": cls["kind"],
                    "tier": cls["tier"],
                    "quantities": quantities,
                    "value": quantities[0]["value"] if quantities else None,
                    "unit": quantities[0]["unit"] if quantities else None,
                    "conditions": extract_conditions(sentence),
                    "comparison": extract_comparison(sentence),
                    "denominator": extract_denominator(sentence),
                    "maturity": detect_maturity(sentence),
                    "source": {"path": f["path"], "locator": unit["locator"],
                               "quote": kit.excerpt(sentence, 240)},
                    "confidence": ("high" if quantities and cls["tier"] == "fact"
                                   else "medium" if cls["tier"] == "fact" else "low"),
                    "disclosable": cls["disclosable"],
                    "restricted_reason": cls.get("restricted_reason"),
                    "assumptions": [],
                    "to_be_validated": None,
                }
                if entry["tier"] == "speculation":
                    entry["assumptions"] = ["从材料条件外推到未验证场景",
                                            "假设外部条件在放大后不变"]
                    entry["to_be_validated"] = "需要中试/现场数据或独立验证"
                entry["score"] = score_evidence(entry)
                entries.append(entry)

    # Deduplicate by normalised statement, keeping the highest score.
    dedup = {}
    for entry in entries:
        key = kit.normalize_space(entry["statement"]).lower()
        if key not in dedup or entry["score"] > dedup[key]["score"]:
            dedup[key] = entry
    entries = sorted(dedup.values(), key=lambda e: (-e["score"], e["source"]["path"],
                                                    e["source"]["locator"]))[:120]
    for i, entry in enumerate(entries, 1):
        entry["evidence_id"] = "E%03d" % i

    quantitative = [e for e in entries
                    if e["quantities"] and e.get("disclosable", True)]
    key_facts = sorted(quantitative, key=lambda e: -e["score"])[:25]
    claims = [e for e in entries if e["kind"] not in ("definition", "citation",
                                                      "restricted_fact")][:36]
    for i, entry in enumerate(claims, 1):
        entry["claim_id"] = "C%03d" % i
    restricted = [e for e in entries if not e["disclosable"]]
    conflicts = detect_conflicts(entries)
    if conflicts:
        uncertainty.extend(conflicts)
    metrics = {}
    for entry in quantitative:
        for metric in METRIC.findall(entry["statement"]):
            metrics.setdefault(metric.lower(), []).append(entry["evidence_id"])
    all_text = "\n".join(kit.read_text(work_dir / f["text_path"])
                         for f in inventory.get("files", [])
                         if f.get("role") != "task_metadata"
                         and f.get("readable") and f.get("text_path"))
    result = {
        "status": "ok" if entries else "empty",
        "case_id": (workorder or {}).get("case_id"),
        "sources": sources,
        "evidence": entries,
        "key_facts": [e["evidence_id"] for e in key_facts],
        "claims": claims,
        "terminology": extract_terminology(all_text),
        "citations": extract_citations(all_text),
        "restricted": [{"evidence_id": e["evidence_id"],
                        "statement": e["statement"],
                        "reason": e.get("restricted_reason"),
                        "source": {"path": e["source"]["path"],
                                   "locator": e["source"]["locator"]}}
                       for e in restricted],
        "metrics": {k: kit.unique(v)[:20] for k, v in metrics.items()},
        "uncertainty": uncertainty,
        "stats": {
            "evidence": len(entries),
            "quantitative": len(quantitative),
            "claims": len(claims),
            "restricted": len(restricted),
            "citations": len(extract_citations(all_text)),
        },
        "generated_at": kit.now_iso(),
    }
    kit.write_json(work_dir / "evidence.json", result)
    kit.write_json(work_dir / "claims.json", {
        "case_id": result["case_id"],
        "claims": claims,
        "key_facts": result["key_facts"],
        "restricted": result["restricted"],
    })
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    inventory = kit.read_json(Path(args.work) / "inventory.json")
    if inventory is None:
        print("inventory.json missing; run inventory.py first", file=sys.stderr)
        return 2
    workorder = kit.read_json(Path(args.work) / "workorder.json", default={})
    result = extract(inventory, args.work, workorder)
    print(json.dumps({"status": result["status"], "stats": result["stats"],
                      "evidence": str(Path(args.work) / "evidence.json"),
                      "uncertainty": result["uncertainty"]},
                     ensure_ascii=False, indent=2))
    return 0 if result["evidence"] else 1


if __name__ == "__main__":
    sys.exit(main())
