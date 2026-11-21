#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, source-bound first pass for the experiment feasibility skill.

The script never invents equipment availability, approvals, prices or quotes.
It extracts only what is present in the provided material, builds a six-category
feasibility matrix, derives risks/confirmation items from *missing or
conditional* evidence, and records every judgement with path + locator + quote.

Usage:
    python3 domain_work.py --input /input --work /app/work --output /app/output
    python3 domain_work.py ... --fill-missing     # keep model content, fill gaps
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

NUM_UNIT_RE = re.compile(
    r"(?<![\w./])(\d+(?:\.\d+)?)\s*"
    r"(mg/kg|mg/mL|µg/mL|μg/mL|ng/mL|pg/mL|mg|µg|μg|ng|g|kg|mL|µL|μL|L|"
    r"°C|℃|h|hr|hrs|hour|hours|min|mins|minute|minutes|day|days|week|weeks|"
    r"%|rpm|nm|mM|µM|μM|M|V|mA|W|Hz|kPa|MPa|bar)(?![A-Za-z])", re.I)
CURRENCY_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(万元|元|CNY|RMB|USD|EUR|€|\$|£)", re.I)
SENT_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")

SAFETY_RULES = [
    ("biohazard", re.compile(r"recombinant|bsl-?\d|biohazard|pathogen|病原|生物安全|基因工程", re.I),
     "生物安全等级/废弃物灭活未在材料中确认", "按 BSL 要求完成风险评估与废弃物灭活，必要时转生物安全委员会"),
    ("radiation", re.compile(r"radioactiv|radiation|isotope|放射|同位素|辐照", re.I),
     "放射源许可、剂量监测与废物处置未在材料中确认", "补充辐射安全许可与剂量计记录，未获批前不开始"),
    ("chemical", re.compile(r"toxic|poison|corrosive|flammable|carcinogen|剧毒|危险化学|腐蚀|易燃|致癌", re.I),
     "危险化学品的 SDS、通风柜与废液处置未在材料中确认", "完成 SDS 核对、通风柜预约与危废登记"),
    ("high_pressure", re.compile(r"high[- ]pressure|高压|autoclave|灭菌锅|反应釜", re.I),
     "高压设备检定/操作资质未在材料中确认", "核验设备检定证书与操作人员资质"),
    ("human", re.compile(r"human|patient|participant|人体|受试者|临床|知情同意", re.I),
     "人体样本/受试者伦理审批与知情同意状态未在材料中确认", "取得 IRB/伦理批件与知情同意后再采集"),
    ("animal", re.compile(r"animal|mouse|mice|rat|rabbit|mice|动物|小鼠|大鼠", re.I),
     "动物伦理批件与饲养条件未在材料中确认", "IACUC 批件生效后再订购动物"),
]
ETHICS_RE = re.compile(
    r"ethic|iacuc|irb|approval|approved|pending|consent|伦理|审批|批准|待批|知情同意", re.I)
EQUIP_STATUS = [
    ("not_available", re.compile(r"unavailable|not available|out of service|不具备|不可用|故障", re.I)),
    ("procurement_required", re.compile(r"to[_ -]?order|purchase|procure|采购|订货|货期|lead time", re.I)),
    ("conditional", re.compile(r"booked|reserved|queue|排期|预约|shared|共享", re.I)),
    ("available", re.compile(r"available|ready|in[- ]house|已具备|可用|在用|正常", re.I)),
]
MATERIAL_RE = re.compile(r"kit|reagent|consumable|antibod|primer|medium|试剂|耗材|抗体|引物|培养基|耗材", re.I)
TIME_UNITS = {
    "h": ("h", 1.0), "hr": ("h", 1.0), "hrs": ("h", 1.0), "hour": ("h", 1.0),
    "hours": ("h", 1.0), "min": ("h", 1 / 60), "mins": ("h", 1 / 60),
    "minute": ("h", 1 / 60), "minutes": ("h", 1 / 60),
    "day": ("d", 1.0), "days": ("d", 1.0), "week": ("d", 7.0), "weeks": ("d", 7.0),
}


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


def emit_text(path, text, fill_only=False):
    path = Path(path)
    if fill_only and path.exists() and path.stat().st_size > 0:
        return False
    write_text(path, text)
    return True


def emit_json(path, payload, fill_only=False):
    path = Path(path)
    if fill_only and path.exists() and path.stat().st_size > 0:
        return False
    write_json(path, payload)
    return True


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def safe_name(key):
    return re.sub(r"[^\w一-鿿.-]+", "_", str(key or "field")).strip("._") or "field"


def tokens(text):
    return set(re.findall(r"[a-z0-9]{3,}|[一-鿿]{2,}", str(text or "").lower()))


class Source:
    """A material file with line-addressable text."""

    def __init__(self, item):
        self.path = str(item.get("path") or item.get("rel_path") or "")
        self.text = str(item.get("text") or "")
        self.kind = str(item.get("kind") or "")
        self.rows = item.get("rows") or []
        self.data = item.get("json")
        self.lines = self.text.splitlines()

    def line_no(self, needle):
        needle_n = norm(needle).lower()
        if not needle_n:
            return 0
        for index, line in enumerate(self.lines, 1):
            if needle_n in norm(line).lower():
                return index
        return 0

    def evidence(self, needle):
        line_no = self.line_no(needle)
        if not line_no:
            return None
        line = self.lines[line_no - 1].strip()
        if not line:
            return None
        return {"path": self.path, "locator": "L%d" % line_no, "quote": line}


def load_sources(work):
    materials = read_json(Path(work) / "materials.json", {})
    docs = materials.get("documents") or []
    sources = [Source(doc) for doc in docs if str(doc.get("text") or "").strip()
               or doc.get("rows") or doc.get("json")]
    return sources, materials


def table_rows(source):
    """Reparse CSV/TSV with the stdlib reader so quoted commas stay intact."""
    text = source.text or ""
    if not text.strip():
        return source.rows or []
    sample = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if "\t" in sample else (
        ";" if ";" in sample and "," not in sample else ",")
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        if rows and len(rows[0]) >= 2:
            return rows
    except csv.Error:
        pass
    return source.rows or []


def first_evidence(sources, pattern):
    if isinstance(pattern, str):
        pattern = re.compile(pattern, re.I)
    for source in sources:
        for index, line in enumerate(source.lines, 1):
            if pattern.search(line):
                return {"path": source.path, "locator": "L%d" % index,
                        "quote": line.strip()}
    return None


def unique_evidence(items):
    seen, out = set(), []
    for item in items:
        if not item or not item.get("quote"):
            continue
        key = (item.get("path"), norm(item.get("quote")))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def parse_variables(sources):
    text = "\n".join(source.text for source in sources)
    out = {"independent": [], "dependent": [], "controls": [], "replicates": [],
           "randomization": []}
    patterns = {
        "independent": r"(?:independent variable|自变量|处理因素|干预)[:：]?\s*(.+)",
        "dependent": r"(?:dependent variable|因变量|结局指标|观测指标)[:：]?\s*(.+)",
        "controls": r"(?:control|对照|对照组)[:：]?\s*(.+)",
        "replicates": r"(?:n\s*=|replicate|重复|样本量)[:：]?\s*(.+)",
        "randomization": r"(?:randomi[sz](?:ation|ed)?|随机化?)\s*[:：]\s*(.+)",
    }
    evidence = []
    for key, pattern in patterns.items():
        for source in sources:
            for index, line in enumerate(source.lines, 1):
                match = re.search(pattern, line, re.I)
                if match:
                    value = norm(match.group(1)) or norm(line)
                    value = re.split(r"\.\s+(?=[A-Z])|。|；", value)[0].strip()
                    if value and value not in out[key]:
                        out[key].append(value)
                    evidence.append({"path": source.path, "locator": "L%d" % index,
                                     "quote": line.strip()})
                    break
            if out[key]:
                break
    return out, unique_evidence(evidence)


def parse_steps(sources):
    steps, evidence = [], []
    number = 0
    step_re = re.compile(r"^\s*(?:step|步骤|阶段)\s*([A-Za-z0-9.-]+)?\s*[:：.)-]?\s*(.+)$", re.I)
    for source in sources:
        for index, line in enumerate(source.lines, 1):
            match = step_re.match(line)
            if not match:
                continue
            number += 1
            action = norm(match.group(2))
            params = {}
            for value, unit in NUM_UNIT_RE.findall(action):
                params.setdefault("value_%d" % (len(params) + 1), "%s %s" % (value, unit))
            steps.append({
                "id": "S%d" % number,
                "action": action,
                "parameters": params,
                "duration": next(("%s %s" % (v, u) for v, u in NUM_UNIT_RE.findall(action)
                                  if u.lower() in TIME_UNITS), "not_stated"),
                "materials": "",
                "basis": {"path": source.path, "locator": "L%d" % index},
            })
            evidence.append({"path": source.path, "locator": "L%d" % index,
                             "quote": line.strip()})
    if not steps and sources:
        source = sources[0]
        for index, line in enumerate(source.lines[:40], 1):
            text = line.strip()
            if len(text) >= 20 and not text.startswith("#"):
                steps.append({"id": "S1", "action": text, "parameters": {},
                              "duration": "not_stated", "materials": "",
                              "basis": {"path": source.path, "locator": "L%d" % index}})
                evidence.append({"path": source.path, "locator": "L%d" % index,
                                 "quote": text})
                break
    return steps, unique_evidence(evidence)


def parse_parameters(sources):
    params, evidence = [], []
    seen = set()
    for source in sources:
        for index, line in enumerate(source.lines, 1):
            for value, unit in NUM_UNIT_RE.findall(line):
                key = (value, unit.lower())
                if key in seen:
                    continue
                seen.add(key)
                params.append({
                    "name": "parameter_%d" % (len(params) + 1),
                    "value": value, "unit": unit,
                    "basis": "verbatim line %s L%d" % (source.path, index),
                    "evidence": {"path": source.path, "locator": "L%d" % index,
                                 "quote": line.strip()},
                })
                evidence.append({"path": source.path, "locator": "L%d" % index,
                                 "quote": line.strip()})
    return params, unique_evidence(evidence)


def classify_status(text):
    for status, pattern in EQUIP_STATUS:
        if pattern.search(text or ""):
            return status
    return "unknown"


def equipment_items(sources):
    items, evidence = [], []
    for source in sources:
        header, rows = [], table_rows(source)
        if rows and isinstance(rows[0], list):
            header = [str(x).strip().lower() for x in rows[0]]
            rows = rows[1:]
        if not header:
            continue
        joined = " ".join(header)
        if not re.search(r"item|equipment|device|instrument|设备|仪器|名称", joined):
            continue
        def col(*names):
            for name in names:
                for index, value in enumerate(header):
                    if name in value:
                        return index
            return None
        i_item = col("item", "equipment", "device", "instrument", "设备", "仪器", "名称")
        i_model = col("model", "型号")
        i_status = col("status", "availability", "状态")
        i_booking = col("booking", "schedule", "排期", "预约")
        i_lead = col("lead", "货期")
        if i_item is None:
            continue
        for row in rows:
            if not isinstance(row, list) or len(row) <= i_item:
                continue
            name = norm(row[i_item])
            if not name:
                continue
            status_text = " ".join(str(row[i]) for i in (i_status, i_booking, i_lead)
                                   if i is not None and i < len(row))
            status = classify_status(status_text)
            ev = source.evidence(name)
            if ev is None:
                raw_line = next((line for line in source.lines if name in line), "")
                if raw_line.strip():
                    ev = {"path": source.path,
                          "locator": "L%d" % max(1, source.line_no(name)),
                          "quote": raw_line.strip()}
            entry = {
                "item": name,
                "model": norm(row[i_model]) if i_model is not None and i_model < len(row) else "",
                "status": status,
                "raw_status": status_text,
                "condition": "",
                "evidence": ev,
            }
            if status == "conditional":
                entry["condition"] = "确认预约/排期窗口：%s" % (status_text or "材料未写明时间")
            elif status == "procurement_required":
                entry["condition"] = "完成采购/到货验收：%s" % (status_text or "货期未写明")
            elif status == "not_available":
                entry["condition"] = "需替代设备或外协，并确认替代方案等效性"
            elif status == "unknown":
                entry["condition"] = "材料未给出可用状态，需设备管理员书面确认"
            items.append(entry)
            evidence.append(ev)
    # Text-only equipment mentions.
    if not items:
        for source in sources:
            for index, line in enumerate(source.lines, 1):
                if re.search(r"equipment|instrument|device|仪器|设备", line, re.I):
                    name = norm(re.sub(r"^[#*\-\d.\s]+", "", line))
                    items.append({"item": name[:160], "model": "",
                                  "status": classify_status(line), "raw_status": line.strip(),
                                  "condition": "材料仅提及设备，未给出可用状态；需设备管理员确认",
                                  "evidence": {"path": source.path, "locator": "L%d" % index,
                                               "quote": line.strip()}})
                    evidence.append(items[-1]["evidence"])
                    break
            if items:
                break
    return items, unique_evidence(evidence)


def material_items(sources):
    items, evidence = [], []
    for source in sources:
        for index, line in enumerate(source.lines, 1):
            if not MATERIAL_RE.search(line):
                continue
            status = classify_status(line)
            entry = {"item": norm(line)[:200], "status": status,
                     "condition": "", "evidence": {
                         "path": source.path, "locator": "L%d" % index,
                         "quote": line.strip()}}
            if status == "procurement_required":
                entry["condition"] = "按下单/货期安排，到货验收后才能开始"
            elif status == "unknown":
                entry["condition"] = "材料未写库存/货期，需库管确认"
            items.append(entry)
            evidence.append(entry["evidence"])
            if len(items) >= 20:
                break
    return items, unique_evidence(evidence)


def time_estimate(sources, steps):
    """Prefer distinct step durations; never double-count the same event."""
    stated = []
    evidence = []
    seen = set()
    for step in steps or []:
        duration = str(step.get("duration") or "")
        match = re.search(r"(\d+(?:\.\d+)?)\s*([A-Za-z]+)", duration)
        if not match:
            continue
        value, unit = match.group(1), match.group(2).lower()
        if unit not in TIME_UNITS:
            continue
        key = (value, unit, step.get("id"))
        if key in seen:
            continue
        seen.add(key)
        canonical, factor = TIME_UNITS[unit]
        hours = float(value) * factor * (24 if canonical == "d" else 1)
        basis = step.get("basis") or {}
        stated.append({"value": value, "unit": unit,
                       "step": step.get("id"),
                       "basis": "%s %s" % (basis.get("path", ""),
                                           basis.get("locator", ""))})
        if basis.get("path") and basis.get("locator"):
            line_no = int(re.sub(r"\D", "", str(basis.get("locator"))) or 0)
            source = next((s for s in sources if s.path == basis.get("path")), None)
            if source and 0 < line_no <= len(source.lines):
                evidence.append({"path": source.path, "locator": basis.get("locator"),
                                 "quote": source.lines[line_no - 1].strip()})
    source_of_time = "step durations"
    if not stated:
        source_of_time = "explicit time mentions (no step duration)"
        for source in sources:
            for index, line in enumerate(source.lines, 1):
                for value, unit in NUM_UNIT_RE.findall(line):
                    unit_key = unit.lower()
                    if unit_key not in TIME_UNITS:
                        continue
                    key = (value, unit_key, source.path, index)
                    if key in seen:
                        continue
                    seen.add(key)
                    canonical, factor = TIME_UNITS[unit_key]
                    hours = float(value) * factor * (24 if canonical == "d" else 1)
                    stated.append({"value": value, "unit": unit,
                                   "basis": "%s L%d" % (source.path, index)})
                    evidence.append({"path": source.path, "locator": "L%d" % index,
                                     "quote": line.strip()})
    total_hours = 0.0
    for item in stated:
        unit_key = item["unit"].lower()
        canonical, factor = TIME_UNITS.get(unit_key, ("h", 0))
        try:
            total_hours += float(item["value"]) * factor * (24 if canonical == "d" else 1)
        except (TypeError, ValueError):
            continue
    has_time = bool(stated)
    return {
        "status": "conditional" if has_time else "unknown",
        "estimated_active_hours": round(total_hours, 2) if has_time else None,
        "items": stated[:50],
        "basis": source_of_time if has_time else "none",
        "condition": ("仅累加互不重复的步骤时长（%s）；等待、检测周转、返工与设备排队未计入，"
                      "需项目负责人补齐并确认排期" % source_of_time) if has_time else
                     ("材料未给出任何步骤时长，无法估算；需补充每步人工/设备占用与周转"),
        "evidence": unique_evidence(evidence),
    }


def budget_estimate(sources):
    by_currency = {}
    items, evidence = [], []
    for source in sources:
        for index, line in enumerate(source.lines, 1):
            for value, currency in CURRENCY_RE.findall(line):
                currency = currency.upper()
                try:
                    amount = float(value)
                except ValueError:
                    continue
                by_currency[currency] = round(by_currency.get(currency, 0.0) + amount, 2)
                items.append({"value": amount, "currency": currency,
                              "basis": "%s L%d" % (source.path, index)})
                evidence.append({"path": source.path, "locator": "L%d" % index,
                                 "quote": line.strip()})
    has_budget = bool(items)
    return {
        "status": "conditional" if has_budget else "unknown",
        "totals": by_currency,
        "items": items[:50],
        "condition": ("为材料中显式金额的直接相加；未含税费、运费、人工与超支缓冲，"
                      "需财务按实际报价复核") if has_budget else
                     ("材料未给出预算或单价，无法判断经费是否足够；需补充预算表"),
        "evidence": unique_evidence(evidence),
    }


def safety_assessment(sources):
    items, evidence = [], []
    for key, pattern, risk, mitigation in SAFETY_RULES:
        hit = None
        for source in sources:
            for index, line in enumerate(source.lines, 1):
                if pattern.search(line):
                    hit = {"path": source.path, "locator": "L%d" % index,
                           "quote": line.strip()}
                    break
            if hit:
                break
        status = "conditional" if hit else "unknown"
        items.append({
            "category": key,
            "observed": bool(hit),
            "status": status,
            "condition": (risk if hit else
                          "材料未提及该风险类别；不能据此判定不存在，需安全负责人按实际试剂/样本核对"),
            "mitigation": mitigation,
            "evidence": hit,
        })
        if hit:
            evidence.append(hit)
    return {
        "status": "conditional" if evidence else "unknown",
        "items": items,
        "condition": "按实际试剂清单与样本类型完成风险评估后方可执行",
        "evidence": unique_evidence(evidence),
    }, unique_evidence(evidence)


def ethics_assessment(sources):
    items, evidence = [], []
    for source in sources:
        for index, line in enumerate(source.lines, 1):
            if not ETHICS_RE.search(line):
                continue
            lowered = line.lower()
            if re.search(r"pending|submitted|under review|待批|审核中|已提交", lowered):
                status = "approval_pending"
                condition = "批件未生效前不得开始涉及该材料/样本的步骤"
            elif re.search(r"approved|approval number|批准|批件号", lowered):
                status = "approved"
                condition = "执行前核对批件号、有效期与批准范围"
            else:
                status = "to_confirm"
                condition = "材料未给出审批状态，需伦理办公室书面确认"
            items.append({"item": norm(line)[:220], "status": status,
                          "condition": condition,
                          "evidence": {"path": source.path, "locator": "L%d" % index,
                                       "quote": line.strip()}})
            evidence.append(items[-1]["evidence"])
    if not items and sources:
        items.append({"item": "材料未提供伦理审批记录", "status": "to_confirm",
                      "condition": "若涉及动物、人体样本或个人数据，先取得对应伦理批件",
                      "evidence": None})
    return {
        "status": ("approval_pending" if any(i["status"] == "approval_pending" for i in items)
                   else "to_confirm" if items else "unknown"),
        "items": items,
        "condition": "涉及动物/人体样本/个人数据时，以伦理批件生效为开始条件",
        "evidence": unique_evidence(evidence),
    }, unique_evidence(evidence)


def sample_assessment(sources):
    items, evidence = [], []
    for source in sources:
        for index, line in enumerate(source.lines, 1):
            if not re.search(r"sample|specimen|blood|serum|tissue|样本|血|血清|组织|细胞", line, re.I):
                continue
            items.append({"item": norm(line)[:220], "status": "to_confirm",
                          "condition": "核对来源、数量、保存条件与伦理审批范围",
                          "evidence": {"path": source.path, "locator": "L%d" % index,
                                       "quote": line.strip()}})
            evidence.append(items[-1]["evidence"])
            if len(items) >= 10:
                break
    return {
        "status": "conditional" if items else "unknown",
        "items": items,
        "condition": "样本来源/数量/保存条件需与方案和伦理批件逐项对齐",
        "evidence": unique_evidence(evidence),
    }, unique_evidence(evidence)


def category_status(items, default="unknown"):
    if not items:
        return default
    statuses = [str(i.get("status") or "unknown") for i in items]
    if all(s == "available" for s in statuses):
        return "available_with_confirmation"
    if any(s == "not_available" for s in statuses):
        return "blocked"
    if any(s == "procurement_required" for s in statuses):
        return "procurement_required"
    if any(s == "approval_pending" for s in statuses):
        return "approval_pending"
    if any(s in ("conditional", "to_confirm", "unknown") for s in statuses):
        return "conditional"
    return "conditional"


def build_risks(feasibility):
    risks = []
    counter = 0
    for category, block in feasibility.items():
        if not isinstance(block, dict):
            continue
        items = block.get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "")
            if status in ("available", "approved"):
                continue
            counter += 1
            label = item.get("item") or item.get("category") or category
            risks.append({
                "id": "R%02d" % counter,
                "risk": "%s：%s 未确认或不满足" % (category, label),
                "category": category,
                "trigger": item.get("raw_status") or item.get("condition") or status,
                "impact": "blocking" if status in ("not_available", "approval_pending")
                          else "medium",
                "mitigation": item.get("mitigation") or item.get("condition")
                              or "补齐材料并取得负责人书面确认",
                "fallback": ("改用等效替代方案/外协，并记录等效性验证；无法替代则暂停该步骤"
                             if status == "not_available" else
                             "延期该步骤并在确认项关闭前不开始"),
                "owner": "项目负责人/设备管理员/伦理办公室",
                "evidence": item.get("evidence"),
            })
    if not risks:
        risks.append({
            "id": "R00",
            "risk": "材料未暴露明显阻塞项，但“未提及”不等于“已满足”",
            "category": "general",
            "trigger": "材料完整性未知",
            "impact": "medium",
            "mitigation": "按确认项清单逐项向设备、材料、伦理负责人取证",
            "fallback": "无法取证时降级为纸面审查并标注未核实范围",
            "owner": "项目负责人",
            "evidence": None,
        })
    return risks


def build_confirmation_items(feasibility, risks):
    items = []
    for category, block in feasibility.items():
        if not isinstance(block, dict):
            continue
        for item in block.get("items") or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "")
            if status in ("available", "approved"):
                continue
            items.append({
                "item": item.get("item") or item.get("category") or category,
                "category": category,
                "why": "状态=%s；%s" % (status or "unknown", item.get("condition") or ""),
                "action": item.get("condition") or "取得书面确认",
                "blocking": status in ("not_available", "approval_pending", "procurement_required"),
                "evidence": item.get("evidence"),
            })
    if not items:
        items.append({
            "item": "材料完整性核对",
            "category": "general",
            "why": "材料未给出可判定的缺失项",
            "action": "按七类检查项向方案负责人补证并记录版本",
            "blocking": False,
            "evidence": None,
        })
    return items


def build_plan(sources, materials):
    text = "\n".join(source.text for source in sources)
    hypothesis_ev = first_evidence(sources, r"(?:hypothes|假设|研究假设|预期)")
    hypothesis = ""
    if hypothesis_ev:
        hypothesis = re.sub(r"^[#*\-\d.\s]*(?:hypothes[ie]s|假设|研究假设|预期)\s*[:：]?\s*",
                            "", hypothesis_ev["quote"], flags=re.I)
    if not hypothesis:
        hypothesis = "材料未给出可检验假设；需把研究目标改写为可证伪的陈述"
    variables, var_ev = parse_variables(sources)
    steps, step_ev = parse_steps(sources)
    parameters, param_ev = parse_parameters(sources)

    equipment, eq_ev = equipment_items(sources)
    materials_items, mat_ev = material_items(sources)
    time_block = time_estimate(sources, steps)
    budget_block = budget_estimate(sources)
    safety_block, safety_ev = safety_assessment(sources)
    ethics_block, ethics_ev = ethics_assessment(sources)
    samples_block, sample_ev = sample_assessment(sources)

    feasibility = {
        "equipment": {"status": category_status(equipment), "items": equipment,
                      "condition": "逐台核对型号、精度/附件、可用时段与操作资质",
                      "evidence": eq_ev},
        "materials": {"status": category_status(materials_items), "items": materials_items,
                      "condition": "逐项核对规格、库存、批次与到货时间",
                      "evidence": mat_ev},
        "time": time_block,
        "budget": budget_block,
        "safety": safety_block,
        "ethics": ethics_block,
        "samples": samples_block,
    }
    risks = build_risks(feasibility)
    confirmations = build_confirmation_items(feasibility, risks)

    evidence = unique_evidence(
        ([hypothesis_ev] if hypothesis_ev else []) + var_ev + step_ev + param_ev
        + eq_ev + mat_ev + time_block.get("evidence", []) + budget_block.get("evidence", [])
        + safety_ev + ethics_ev + sample_ev)

    uncertainties = []
    if not sources:
        uncertainties.append({"item": "materials", "reason": "未找到可读材料",
                              "impact": "无法做任何事实性判断", "action": "提供方案/设备/预算/伦理材料后重跑"})
    for category, block in feasibility.items():
        if isinstance(block, dict) and not (block.get("items") or block.get("estimated_active_hours")):
            uncertainties.append({"item": "%s_missing" % category,
                                  "reason": "材料未提供该类别信息",
                                  "impact": "该类别只能标为 unknown/to_confirm",
                                  "action": "向对应负责人补证；不要把未提及当成满足"})
    for category, block in feasibility.items():
        if isinstance(block, dict) and block.get("condition"):
            pass
    if max([len(source.text) for source in sources], default=0) > 200000:
        uncertainties.append({"item": "truncated_material",
                              "reason": "材料超过单文档读取上限，可能截断",
                              "impact": "审查范围可能不完整", "action": "分段复核并标注未覆盖页"})

    overall = "insufficient_information" if not sources else (
        "blocked" if any(r.get("impact") == "blocking" for r in risks) else "conditional")
    conditions = [c["action"] for c in confirmations if c.get("action")]
    plan = {
        "case_id": "",
        "mode": "feasibility",
        "summary": "基于所提供材料逐项核对七类约束；结论为 %s，未确认项不得写成已具备。" % overall,
        "conclusion": overall,
        "hypothesis": hypothesis,
        "variables": variables,
        "steps": steps,
        "parameters": parameters,
        "feasibility": feasibility,
        "conditions": conditions,
        "risks": risks,
        "confirmation_items": confirmations,
        "evidence": evidence,
        "uncertainties": uncertainties,
        "sources": [{"path": source.path, "chars": len(source.text),
                     "kind": source.kind} for source in sources],
        "review_scope": {
            "materials_read": len(sources),
            "source_chars": sum(len(source.text) for source in sources),
            "checks": ["assumptions_variables", "controls", "equipment_materials",
                       "time_cost", "parameter_range", "risk_exception",
                       "ethics_safety", "samples"],
        },
        "generated_at": now_iso(),
        "_generated_by": "domain_work.py",
    }
    checks = domain_checks(plan)
    plan["_domain_checks"] = checks
    return plan


def domain_checks(plan):
    problems, warnings = [], []
    feasibility = plan.get("feasibility") or {}
    for key in ("equipment", "materials", "time", "budget", "safety", "ethics"):
        block = feasibility.get(key)
        if not isinstance(block, dict) or not block:
            problems.append("feasibility.%s is missing or empty" % key)
            continue
        if not block.get("condition"):
            problems.append("feasibility.%s has no explicit condition" % key)
        status = str(block.get("status") or "")
        if status in ("conditional", "procurement_required", "approval_pending",
                      "blocked") and not block.get("condition"):
            problems.append("feasibility.%s conditional without condition" % key)
        if status != "available_with_confirmation" and not (block.get("items") or
                                                            block.get("estimated_active_hours")):
            warnings.append("feasibility.%s has no item-level evidence" % key)
    for index, item in enumerate(feasibility.get("equipment", {}).get("items") or [], 1):
        if item.get("status") not in ("available", "unknown") and not item.get("condition"):
            problems.append("equipment item %d conditional without condition" % index)
    for risk in plan.get("risks") or []:
        for field in ("risk", "impact", "mitigation", "fallback"):
            if not risk.get(field):
                problems.append("risk %s lacks %s" % (risk.get("id", "?"), field))
    for item in plan.get("confirmation_items") or []:
        for field in ("item", "category", "why", "action"):
            if not item.get(field):
                problems.append("confirmation item lacks %s" % field)
    if plan.get("conclusion") == "conditional" and not plan.get("conditions"):
        problems.append("conditional conclusion without actionable conditions")
    for evidence in plan.get("evidence") or []:
        for field in ("path", "locator", "quote"):
            if not evidence.get(field):
                problems.append("evidence lacks %s" % field)
    return {"problems": problems, "warnings": warnings,
            "blocking": len(problems),
            "source_count": len(plan.get("sources") or []),
            "evidence_count": len(plan.get("evidence") or [])}


def render_report(plan):
    feas = plan.get("feasibility") or {}
    lines = [
        "# 实验方案可行性审查报告",
        "",
        "- case_id: `%s`" % plan.get("case_id"),
        "- 生成时间: %s" % plan.get("generated_at"),
        "- 总体结论: **%s**" % plan.get("conclusion"),
        "- 审查材料数: %d；证据条数: %d" % (len(plan.get("sources") or []),
                                          len(plan.get("evidence") or [])),
        "",
        "> 本报告只依据输入材料。材料未写的内容一律标为 `unknown`/`to_confirm`，",
        "> 不把“没写”当成“已满足”；所有判断附 path/locator/quote。",
        "",
        "## 1. 假设与变量",
        "",
        "- 假设：%s" % plan.get("hypothesis"),
    ]
    for key, label in (("independent", "自变量"), ("dependent", "因变量"),
                       ("controls", "对照"), ("replicates", "重复/样本量"),
                       ("randomization", "随机化")):
        values = (plan.get("variables") or {}).get(key) or []
        lines.append("- %s：%s" % (label, "；".join(values) if values else "材料未写明（to_confirm）"))
    lines += ["", "## 2. 步骤、参数与判读", ""]
    if plan.get("steps"):
        for step in plan["steps"]:
            lines.append("- `%s` %s（时长：%s；参数：%s）" % (
                step.get("id"), step.get("action"), step.get("duration") or "未写明",
                ", ".join(step.get("parameters", {}).values()) or "未写明"))
    else:
        lines.append("- 材料未给出可执行步骤（to_confirm）")
    lines.append("- 参数：%s" % ("；".join(
        "%s=%s %s" % (p.get("name"), p.get("value"), p.get("unit"))
        for p in (plan.get("parameters") or [])[:12]) or "材料未给出参数"))
    lines += ["", "## 3. 七类约束可行性矩阵", "",
              "| 类别 | 状态 | 条件 / 依据 | 条目数 |", "|---|---|---|---|"]
    for key, label in (("equipment", "设备"), ("materials", "材料"),
                       ("time", "时间"), ("budget", "预算"),
                       ("safety", "安全"), ("ethics", "伦理"), ("samples", "样本")):
        block = feas.get(key) or {}
        count = len(block.get("items") or [])
        if key == "time" and block.get("estimated_active_hours") is not None:
            count = "%s h（显式累加）" % block.get("estimated_active_hours")
        if key == "budget" and block.get("totals"):
            count = ", ".join("%s %s" % (v, k) for k, v in block["totals"].items())
        lines.append("| %s | %s | %s | %s |" % (
            label, block.get("status") or "unknown",
            (block.get("condition") or "未写明").replace("|", "/"), count))
    lines += ["", "### 3.1 设备/材料逐项状态", ""]
    for key, label in (("equipment", "设备"), ("materials", "材料")):
        for item in (feas.get(key) or {}).get("items") or []:
            ev = item.get("evidence") or {}
            lines.append("- [%s] %s — 状态 `%s`；条件：%s；证据：%s %s" % (
                label, item.get("item"), item.get("status"), item.get("condition") or "—",
                ev.get("path") or "—", ev.get("locator") or ""))
    lines += ["", "## 4. 风险与异常处置", ""]
    for risk in plan.get("risks") or []:
        lines.append("- `%s` **%s**（影响：%s）" % (
            risk.get("id"), risk.get("risk"), risk.get("impact")))
        lines.append("  - 触发：%s" % risk.get("trigger"))
        lines.append("  - 处置：%s" % risk.get("mitigation"))
        lines.append("  - 降级/转人工：%s" % risk.get("fallback"))
    lines += ["", "## 5. 确认项（关闭前不得写成可执行）", ""]
    for item in plan.get("confirmation_items") or []:
        lines.append("- [%s] %s：%s（阻塞：%s）" % (
            "x" if item.get("blocking") else " ", item.get("item"),
            item.get("action"), "是" if item.get("blocking") else "否"))
    lines += ["", "## 6. 证据与不确定性", ""]
    for ev in (plan.get("evidence") or [])[:40]:
        lines.append("- `%s` %s：%s" % (ev.get("path"), ev.get("locator"), ev.get("quote")))
    if plan.get("uncertainties"):
        lines += ["", "### 未确认 / 缺口"]
        for item in plan["uncertainties"]:
            lines.append("- %s：%s → %s" % (item.get("item"), item.get("reason"),
                                            item.get("action")))
    lines += ["", "## 7. 复跑说明", "",
              "```bash",
              "python3 scripts/run_case.py prepare --input /input --work /app/work --output /app/output",
              "python3 scripts/domain_work.py --input /input --work /app/work --output /app/output",
              "python3 scripts/run_case.py finalize --input /input --work /app/work --output /app/output",
              "```",
              "", "相同输入重跑时，矩阵、风险与确认项由同一规则生成，可用于版本间比较。", ""]
    return "\n".join(lines)


def contract_payloads(plan):
    feas = plan.get("feasibility") or {}
    return {
        "实验方案": {"hypothesis": plan.get("hypothesis"), "variables": plan.get("variables"),
                     "steps": plan.get("steps"), "parameters": plan.get("parameters"),
                     "acceptance": "判读标准需在材料中明确；未写明部分见 uncertainties"},
        "依据与风险": {"evidence": plan.get("evidence"), "risks": plan.get("risks"),
                       "conditions": plan.get("conditions")},
        "确认项": plan.get("confirmation_items"),
        "可行性": feas,
        "可行性审查": feas,
        "风险": plan.get("risks"),
        "风险清单": plan.get("risks"),
        "证据": plan.get("evidence"),
        "证据记录": plan.get("evidence"),
        "状态说明": plan.get("uncertainties"),
        "不确定性": plan.get("uncertainties"),
        "feasibility": feas,
        "feasibility_review": feas,
        "risk_assessment": plan.get("risks"),
        "confirmation_items": plan.get("confirmation_items"),
        "experiment_plan": {"hypothesis": plan.get("hypothesis"),
                            "variables": plan.get("variables"),
                            "steps": plan.get("steps"),
                            "parameters": plan.get("parameters")},
        "evidence_record": plan.get("evidence"),
        "status_note": plan.get("uncertainties"),
    }


def write_contract_artifacts(output, contract, plan, fill_only=False):
    payloads = contract_payloads(plan)
    written = {}
    if isinstance(contract, list):
        keys = [str(x) for x in contract]
    elif isinstance(contract, dict):
        keys = [str(x) for x in contract]
    else:
        keys = []
    for key in keys:
        value = payloads.get(key)
        if value is None:
            value = {"field": key, "status": "partial",
                     "note": "该契约键没有专用字段；请以 experiment-report.md 与 result.json 为准",
                     "deliverable": "experiment-report.md",
                     "uncertainties": plan.get("uncertainties")}
        path = output / (safe_name(key) + ".json")
        if emit_json(path, value, fill_only=fill_only):
            written[key] = path.name
        else:
            written[key] = path.name
    return written


def merge_work(existing, generated, fill_only=False):
    if not isinstance(existing, dict) or not existing:
        return generated
    protected = {"case_id", "mode", "_generated_by", "_domain_checks"}
    out = dict(existing)
    if fill_only or existing.get("_generated_by"):
        for key, value in generated.items():
            if key in protected:
                out.setdefault(key, value)
                continue
            if key not in out or out.get(key) in (None, "", [], {}):
                out[key] = value
        out.setdefault("_generated_by", "domain_work.py")
        out["_domain_checks"] = generated.get("_domain_checks")
        return out
    # Model-authored content: keep it, only fill missing keys.
    for key, value in generated.items():
        if key in protected:
            continue
        if key not in out or out.get(key) in (None, "", [], {}):
            out[key] = value
    out.setdefault("_generated_by", "model+domain_work.py")
    out["_domain_checks"] = generated.get("_domain_checks")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--fill-missing", action="store_true")
    args = parser.parse_args(argv)

    input_dir = Path(args.input)
    work = Path(args.work)
    output = Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    case = read_json(input_dir / "case.json", {})
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    sources, materials = load_sources(work)

    plan = build_plan(sources, materials)
    plan["case_id"] = case_id

    work_path = work / "experiment-plan.json"
    existing = read_json(work_path, {})
    plan = merge_work(existing, plan, fill_only=args.fill_missing)
    plan["case_id"] = case_id
    write_json(work_path, plan)

    fill_only = bool(args.fill_missing)
    emit_text(output / "experiment-report.md", render_report(plan), fill_only)
    emit_json(output / "feasibility-review.json", {
        "case_id": case_id, "generated_at": plan.get("generated_at"),
        "conclusion": plan.get("conclusion"),
        "matrix": plan.get("feasibility"),
        "conditions": plan.get("conditions"),
        "checks": plan.get("_domain_checks"),
    }, fill_only)
    emit_json(output / "risk-assessment.json", {
        "case_id": case_id, "risks": plan.get("risks"),
        "fallback_paths": [{"risk_id": r.get("id"), "fallback": r.get("fallback")}
                           for r in plan.get("risks") or []],
    }, fill_only)
    emit_json(output / "confirmation-items.json", {
        "case_id": case_id, "items": plan.get("confirmation_items"),
        "blocking_count": sum(1 for item in plan.get("confirmation_items") or []
                              if item.get("blocking")),
    }, fill_only)
    contract = case.get("output_contract") or {}
    written = write_contract_artifacts(output, contract, plan, fill_only=fill_only)

    audit = {
        "case_id": case_id,
        "generated_at": now_iso(),
        "script": "domain_work.py",
        "source_count": len(sources),
        "source_paths": [source.path for source in sources],
        "evidence_count": len(plan.get("evidence") or []),
        "checks": plan.get("_domain_checks"),
        "contract_artifacts": written,
        "fill_missing": bool(args.fill_missing),
        "no_materials": len(sources) == 0,
    }
    write_json(work / "domain-status.json", audit)
    write_json(output / "domain-audit.json", audit)
    print(json.dumps({"script": "domain_work.py", "case_id": case_id,
                      "sources": len(sources), "conclusion": plan.get("conclusion"),
                      "blocking_checks": (audit.get("checks") or {}).get("blocking", 0),
                      "contract_artifacts": written}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        # The wrapper will still run finalize and write result.json; never crash the run.
        print(json.dumps({"script": "domain_work.py", "status": "error",
                          "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(0)
