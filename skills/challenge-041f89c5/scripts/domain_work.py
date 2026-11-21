#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, source-bound first pass for the research topic-selection skill.

Only records that actually appear in the provided material are used.  Novelty
is never claimed globally: each claim is checked against the provided corpus
with a transparent lexical-overlap rule and labelled ``within_corpus``.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
ARXIV_RE = re.compile(r"arXiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?", re.I)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
CLAIM_RE = re.compile(
    r"novel|novelty|first|no prior|unprecedented|we propose|we introduce|our contribution|"
    r"创新|首次|尚无人|未见报道|我们提出|本文提出|贡献", re.I)
RETRACT_RE = re.compile(r"retract|withdraw|撤稿|撤回", re.I)
HOLD_RE = re.compile(r"corrigendum|erratum|勘误|更正", re.I)


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
    return {x for x in re.findall(r"[a-z0-9]{3,}|[一-鿿]{2,}", str(text or "").lower())}


def overlap(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


class Source:
    def __init__(self, item):
        self.path = str(item.get("path") or item.get("rel_path") or "")
        self.text = str(item.get("text") or "")
        self.kind = str(item.get("kind") or "")
        self.rows = item.get("rows") or []
        self.data = item.get("json")
        self.lines = self.text.splitlines()

    def evidence_for(self, needle):
        needle_n = norm(needle).lower()
        if not needle_n:
            return None
        for index, line in enumerate(self.lines, 1):
            if needle_n in norm(line).lower():
                return {"path": self.path, "locator": "L%d" % index,
                        "quote": line.strip()}
        return None

    def record_evidence(self, record):
        for needle in (record.get("doi"), record.get("arxiv"), record.get("title")):
            if needle:
                ev = self.evidence_for(str(needle))
                if ev:
                    return ev
        return None


def load_sources(work):
    materials = read_json(Path(work) / "materials.json", {})
    docs = materials.get("documents") or []
    return [Source(doc) for doc in docs
            if str(doc.get("text") or "").strip() or doc.get("rows") or doc.get("json")]


def table_rows(source):
    text = source.text or ""
    if not text.strip():
        return source.rows or []
    sample = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "	" if "	" in sample else (
        ";" if ";" in sample and "," not in sample else ",")
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        if rows and len(rows[0]) >= 2:
            return rows
    except csv.Error:
        pass
    return source.rows or []


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


def json_records(value, source):
    records = []
    items = []
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        for key in ("records", "references", "papers", "articles", "items"):
            if isinstance(value.get(key), list):
                items = value[key]
                break
        else:
            if any(key in value for key in ("title", "doi", "arxiv")):
                items = [value]
    for item in items:
        if not isinstance(item, dict):
            continue
        title = norm(item.get("title") or item.get("name") or "")
        doi = norm(item.get("doi") or item.get("DOI") or "")
        arxiv = ""
        match = ARXIV_RE.search(doi or norm(item.get("arxiv") or ""))
        if match:
            arxiv = "arXiv:%s" % match.group(1)
        if not (title or doi or arxiv):
            continue
        record = {
            "id": "R%02d" % (len(records) + 1),
            "title": title,
            "doi": doi or None,
            "arxiv": arxiv or None,
            "year": str(item.get("year") or item.get("date") or "")[:4] or None,
            "authors": norm(item.get("authors") or item.get("author") or "") or None,
            "venue": norm(item.get("venue") or item.get("journal") or "") or None,
            "abstract": norm(item.get("abstract") or item.get("summary") or ""),
            "status": ("retracted" if RETRACT_RE.search(
                norm(item.get("status") or item.get("abstract") or "")) else
                "corrected" if HOLD_RE.search(norm(item.get("status") or "")) else
                "unknown"),
            "source": source.path,
        }
        record["evidence"] = source.record_evidence(record)
        records.append(record)
    return records


def csv_records(source):
    records = []
    rows = table_rows(source)
    if not rows or not isinstance(rows[0], list):
        return records
    header = [str(x).strip().lower() for x in rows[0]]

    def col(*names):
        for name in names:
            for index, value in enumerate(header):
                if name == value or name in value:
                    return index
        return None

    i_title = col("title", "标题")
    i_doi = col("doi")
    i_year = col("year", "date", "年份")
    i_authors = col("authors", "author", "作者")
    i_venue = col("venue", "journal", "期刊")
    i_abstract = col("abstract", "摘要")
    i_status = col("status", "状态")
    for row in rows[1:]:
        if not isinstance(row, list):
            continue
        def get(index):
            return norm(row[index]) if index is not None and index < len(row) else ""
        title, doi = get(i_title), get(i_doi)
        if not (title or doi):
            continue
        record = {
            "id": "R%02d" % (len(records) + 1), "title": title, "doi": doi or None,
            "arxiv": ("arXiv:%s" % ARXIV_RE.search(doi).group(1)
                      if doi and ARXIV_RE.search(doi) else None),
            "year": get(i_year)[:4] or None, "authors": get(i_authors) or None,
            "venue": get(i_venue) or None, "abstract": get(i_abstract),
            "status": ("retracted" if RETRACT_RE.search(get(i_status)) else
                       "corrected" if HOLD_RE.search(get(i_status)) else "unknown"),
            "source": source.path,
        }
        record["evidence"] = source.record_evidence(record) or (
            source.evidence_for(title) if title else None)
        records.append(record)
    return records


def text_records(source):
    records = []
    for index, line in enumerate(source.lines, 1):
        text = norm(line)
        if not text or len(text) < 12:
            continue
        doi_match = DOI_RE.search(text)
        title_match = re.search(r"(?:title|标题)\s*[:：]\s*(.+)", text, re.I)
        if not (doi_match or title_match):
            continue
        title = norm(title_match.group(1)) if title_match else re.sub(
            r"^[#*\-\d.\s]+", "", text)
        record = {
            "id": "R%02d" % (len(records) + 1), "title": title,
            "doi": doi_match.group(0) if doi_match else None,
            "arxiv": ("arXiv:%s" % ARXIV_RE.search(text).group(1)
                      if ARXIV_RE.search(text) else None),
            "year": (YEAR_RE.search(text).group(0) if YEAR_RE.search(text) else None),
            "authors": None, "venue": None, "abstract": "",
            "status": ("retracted" if RETRACT_RE.search(text) else
                       "corrected" if HOLD_RE.search(text) else "unknown"),
            "source": source.path,
            "evidence": {"path": source.path, "locator": "L%d" % index,
                         "quote": line.strip()},
        }
        records.append(record)
    return records


def extract_records(sources):
    records = []
    for source in sources:
        if source.data is not None:
            records.extend(json_records(source.data, source))
        if source.rows:
            records.extend(csv_records(source))
        if not (source.data is not None or source.rows):
            records.extend(text_records(source))
    # Deduplicate by DOI/arXiv/normalized title; keep the first real occurrence.
    seen, out = {}, []
    for record in records:
        key = (record.get("doi") or record.get("arxiv")
               or norm(record.get("title")).lower())
        if not key:
            continue
        if key in seen:
            seen[key]["merged_from"] = list(seen[key].get("merged_from") or []) + [record["id"]]
            continue
        seen[key] = record
        out.append(record)
    for index, record in enumerate(out, 1):
        record["id"] = "R%02d" % index
    return out


def extract_query(sources):
    patterns = [
        r"(?:research question|研究问题|研究目标|aim|objective)\s*[:：]\s*(.+)",
        r"(?:检索式|query)\s*[:：]\s*(.+)",
    ]
    for pattern in patterns:
        for source in sources:
            for index, line in enumerate(source.lines, 1):
                match = re.search(pattern, line, re.I)
                if match:
                    return norm(match.group(1)), {
                        "path": source.path, "locator": "L%d" % index,
                        "quote": line.strip()}
    return "", None


def extract_problem(sources, records):
    question, question_ev = extract_query(sources)
    text = "\n".join(source.text for source in sources)
    def find(pattern):
        match = re.search(pattern, text, re.I)
        if not match:
            return None, None
        for source in sources:
            ev = source.evidence_for(match.group(0))
            if ev:
                return norm(match.group(1) if match.groups() else match.group(0)), ev
        return norm(match.group(1) if match.groups() else match.group(0)), None
    population, population_ev = find(r"(?:population|研究对象|人群|样本)\s*[:：]\s*(.+)")
    variables, variables_ev = find(r"(?:variables?|变量|指标)\s*[:：]\s*(.+)")
    baseline, baseline_ev = find(r"(?:baseline|对照|比较基线)\s*[:：]\s*(.+)")
    outcome, outcome_ev = find(r"(?:outcome|expected conclusion|预期结论|主要终点)\s*[:：]\s*(.+)")
    falsify, falsify_ev = find(
        r"(?:falsif\w*|refut\w*|证伪|推翻条件)\s*[:：]\s*(.+)")
    return {
        "question": question or "材料未给出明确研究问题（to_confirm）",
        "population": population or "not_stated_in_materials",
        "variables": variables or "not_stated_in_materials",
        "baseline": baseline or "not_stated_in_materials",
        "expected_conclusion": outcome or "not_stated_in_materials",
        "falsification_conditions": ([falsify] if falsify else ["not_stated_in_materials"]),
        "scope": "provided_corpus_only",
        "testable": bool(question and (variables or outcome)),
        "evidence": unique_evidence([x for x in (question_ev, population_ev, variables_ev,
                                                 baseline_ev, outcome_ev, falsify_ev) if x]),
        "warnings": [] if all((population, variables, baseline, outcome)) else [
            "problem_definition fields marked not_stated_in_materials must be filled with the PI"
        ],
    }


def extract_claims(sources, records):
    claims, evidence = [], []
    for source in sources:
        for index, line in enumerate(source.lines, 1):
            text = norm(line)
            if len(text) < 10 or not CLAIM_RE.search(text):
                continue
            claim_id = "N%02d" % (len(claims) + 1)
            if re.search(r"方法|method|algorithm|模型|objective|pretrain|framework|机制", text, re.I):
                kind = "method"
            elif re.search(r"数据|dataset|corpus|样本", text, re.I):
                kind = "data"
            elif re.search(r"场景|应用|domain|setting", text, re.I):
                kind = "scenario_transfer"
            elif re.search(r"结论|修正|correct|revise", text, re.I):
                kind = "conclusion_revision"
            else:
                kind = "unknown"
            scored = []
            for record in records:
                score = overlap(text, "%s %s" % (record.get("title") or "",
                                                 record.get("abstract") or ""))
                if score >= 0.18:
                    scored.append({"record_id": record["id"], "overlap": round(score, 3),
                                   "title": record.get("title")})
            scored.sort(key=lambda x: (-x["overlap"], x["record_id"]))
            if scored and scored[0]["overlap"] >= 0.35:
                verdict = "possibly_covered"
            elif scored:
                verdict = "weakly_related"
            else:
                verdict = "no_overlap_in_provided_corpus"
            global_words = bool(re.search(r"首次|尚无人|未见报道|\bfirst\b|\bno prior\b", text, re.I))
            claims.append({
                "id": claim_id,
                "claim": text,
                "type": kind,
                "scope": "provided_corpus_only",
                "search_basis": "lexical_overlap_on_provided_corpus",
                "covered_by": scored[:5],
                "verdict": verdict,
                "global_novelty_supported": False if global_words else None,
                "note": ("该行含“首次/尚无人”措辞；仅凭提供的语料不能支持全球新颖性，"
                         "必须做在线检索并保留检索式/日期/快照" if global_words else
                         "新颖性只在所提供语料范围内判定"),
                "evidence": {"path": source.path, "locator": "L%d" % index,
                             "quote": line.strip()},
            })
            evidence.append(claims[-1]["evidence"])
    return claims, unique_evidence(evidence)


def comparison_matrix(records):
    rows = []
    for record in records:
        fields = {
            "task": record.get("task") or "not_extracted",
            "method": record.get("method") or "not_extracted",
            "data": record.get("data") or "not_extracted",
            "evaluation": record.get("evaluation") or "not_extracted",
            "conclusion": record.get("conclusion") or "not_extracted",
        }
        extracted = sum(1 for value in fields.values() if value != "not_extracted")
        rows.append({
            "record_id": record["id"],
            "option": record.get("title") or record.get("doi") or record["id"],
            "dimension": "task/method/data/evaluation/conclusion",
            "value": fields,
            "directly_comparable": extracted >= 3,
            "reason": ("至少三个比较字段来自材料，可做有限比较" if extracted >= 3 else
                       "材料仅给出标题/标识符，缺少可比较字段；不能据此判定等效或创新"),
            "evidence": record.get("evidence"),
        })
    return rows


def extract_feasibility(sources):
    text = "\n".join(source.text for source in sources)
    premises, conditions, evidence = [], [], []
    specs = [
        ("data", r"dataset|data availability|数据|样本|语料", "数据可用性"),
        ("compute", r"gpu|compute|算力|计算资源|集群", "算力/计算资源"),
        ("time", r"timeline|time window|周期|时间窗|排期", "时间窗口"),
        ("personnel", r"personnel|team|人员|团队|能力", "人员能力"),
        ("ethics", r"ethic|irb|iacuc|伦理|审批", "伦理/安全审批"),
        ("instrument", r"instrument|equipment|设备|仪器|平台", "设备/平台"),
    ]
    for key, pattern, label in specs:
        hit = None
        for source in sources:
            for index, line in enumerate(source.lines, 1):
                if re.search(pattern, line, re.I):
                    hit = {"path": source.path, "locator": "L%d" % index,
                           "quote": line.strip()}
                    break
            if hit:
                break
        status = "evidence_in_material" if hit else "unknown"
        premises.append({
            "premise": label,
            "value": hit["quote"] if hit else "not_stated_in_materials",
            "source": hit["path"] if hit else None,
            "locator": hit["locator"] if hit else None,
            "status": status,
        })
        if hit:
            evidence.append(hit)
        else:
            conditions.append("补齐%s的书面前提（提供方/数量/时间/限制）" % label)
    evidence_in_material = any(p["status"] == "evidence_in_material" for p in premises)
    conclusion = "conditional"
    if not evidence_in_material:
        conclusion = "insufficient_information"
    return {
        "premises": premises,
        "calculation": "每个前提只标 evidence_in_material / unknown；不把 unknown 当满足",
        "conclusion": conclusion,
        "conditions": conditions,
        "risks": [{"risk": "前提未核实即开工", "impact": "medium",
                   "mitigation": "按前提清单逐项取证并记录版本"}],
        "evidence": unique_evidence(evidence),
    }, unique_evidence(evidence)


def gap_analysis(claims, records):
    gaps = []
    for claim in claims:
        if claim.get("verdict") in ("no_overlap_in_provided_corpus", "weakly_related"):
            gaps.append({
                "gap": "在提供的 %d 条记录中，声明 %s 没有被直接覆盖" % (
                    len(records), claim["id"]),
                "basis": "%s（仅限所提供语料）" % claim["id"],
                "claim_id": claim["id"],
                "status": "candidate_gap_requires_live_search",
                "evidence": claim.get("evidence"),
            })
    retracted = [r for r in records if r.get("status") == "retracted"]
    for record in retracted:
        gaps.append({
            "gap": "记录 %s 为撤稿状态，不能作为支持证据" % record["id"],
            "basis": record.get("title") or record.get("doi"),
            "claim_id": None,
            "status": "conflict_evidence",
            "evidence": record.get("evidence"),
        })
    if not gaps:
        gaps.append({
            "gap": "在所提供语料范围内未发现明显空白或冲突；这不等于全球范围没有先验工作",
            "basis": "provided_corpus_only",
            "claim_id": None,
            "status": "no_gap_identified_within_scope",
            "evidence": None,
        })
    return gaps


def build_plan(sources):
    records = extract_records(sources)
    problem = extract_problem(sources, records)
    claims, claim_ev = extract_claims(sources, records)
    matrix = comparison_matrix(records)
    feasibility, feas_ev = extract_feasibility(sources)
    gaps = gap_analysis(claims, records)
    evidence = unique_evidence(
        (problem.get("evidence") or []) + claim_ev + feas_ev
        + [r.get("evidence") for r in records if r.get("evidence")])
    uncertainties = []
    if not sources:
        uncertainties.append({"item": "materials", "reason": "未找到可读材料",
                              "impact": "无法评估选题", "action": "提供文献/方案材料后重跑"})
    if not records:
        uncertainties.append({"item": "references", "reason": "材料中没有可解析的文献记录",
                              "impact": "比较矩阵与新颖性判断只能为空",
                              "action": "提供带标题/DOI 的文献清单或原文"})
    if problem.get("warnings"):
        for warning in problem["warnings"]:
            uncertainties.append({"item": "problem_definition", "reason": warning,
                                  "impact": "问题尚未完全可检验",
                                  "action": "由研究问题负责人补齐对象/变量/基线/证伪条件"})
    for claim in claims:
        if claim.get("global_novelty_supported") is False:
            uncertainties.append({
                "item": "global_novelty", "reason": claim.get("note"),
                "impact": "不能宣称全球首次", "action": "执行在线检索并保存检索式、日期与快照",
            })
    if any(record.get("doi") and not DOI_RE.fullmatch(record["doi"])
           for record in records):
        uncertainties.append({"item": "reference_identifiers",
                              "reason": "部分 DOI 仅从文本解析，可能被截断",
                              "impact": "引用真实性需回源核对",
                              "action": "逐条在 Crossref/出版社页面核实"})
    findings = []
    for index, claim in enumerate(claims[:20], 1):
        findings.append({
            "id": "F%02d" % index,
            "statement": "%s：%s（%s）" % (claim["id"], claim["verdict"],
                                           claim.get("note")),
            "sources": [row["record_id"] for row in claim.get("covered_by") or []],
            "evidence": [claim.get("evidence")] if claim.get("evidence") else [],
        })
    plan = {
        "case_id": "",
        "mode": "topic-selection",
        "summary": ("基于所提供语料评估问题定义、已有工作、创新边界与可行性；"
                    "结论只覆盖 provided_corpus_only。"),
        "question": problem.get("question"),
        "problem_definition": problem,
        "references": records,
        "comparison_matrix": matrix,
        "novelty_claims": claims,
        "gap_analysis": gaps,
        "feasibility": feasibility,
        "conditions": feasibility.get("conditions") or [],
        "findings": findings,
        "evidence": evidence,
        "uncertainties": uncertainties,
        "sources": [{"path": source.path, "chars": len(source.text),
                     "kind": source.kind} for source in sources],
        "review_scope": {
            "materials_read": len(sources),
            "records_extracted": len(records),
            "corpus_scope": "provided_corpus_only",
            "not_claimed": ["global_novelty", "completed_experiments",
                            "full_text_reading_when_only_metadata"],
        },
        "generated_at": now_iso(),
        "_generated_by": "domain_work.py",
    }
    checks = domain_checks(plan)
    plan["_domain_checks"] = checks
    return plan


def domain_checks(plan):
    problems, warnings = [], []
    problem = plan.get("problem_definition") or {}
    for field in ("question", "population", "variables", "baseline",
                  "falsification_conditions", "scope"):
        if not problem.get(field):
            problems.append("problem_definition.%s is missing" % field)
    if not problem.get("testable"):
        warnings.append("problem_definition is not yet testable without PI input")
    for index, row in enumerate(plan.get("comparison_matrix") or [], 1):
        for field in ("option", "dimension", "value", "directly_comparable", "reason"):
            if row.get(field) in (None, ""):
                problems.append("comparison row %d lacks %s" % (index, field))
        if row.get("evidence") is None:
            warnings.append("comparison row %d has no evidence" % index)
    if plan.get("references") and not plan.get("comparison_matrix"):
        problems.append("references exist but comparison_matrix is empty")
    for claim in plan.get("novelty_claims") or []:
        if not claim.get("scope"):
            problems.append("novelty claim %s lacks scope" % claim.get("id"))
        text = str(claim.get("claim") or "")
        if re.search(r"首次|尚无人|未见报道|\bfirst\b|\bno prior\b", text, re.I) \
                and claim.get("scope") != "live_search":
            problems.append("novelty claim %s uses global wording without live_search scope"
                            % claim.get("id"))
        if claim.get("verdict") not in ("possibly_covered", "weakly_related",
                                        "no_overlap_in_provided_corpus"):
            problems.append("novelty claim %s has an invalid verdict" % claim.get("id"))
    for premise in (plan.get("feasibility") or {}).get("premises") or []:
        if not premise.get("status"):
            problems.append("feasibility premise lacks status")
        if premise.get("status") == "evidence_in_material" and not premise.get("source"):
            problems.append("feasibility premise claims evidence without source")
    for record in plan.get("references") or []:
        doi = record.get("doi")
        if doi and not DOI_RE.fullmatch(doi):
            problems.append("reference %s has an invalid DOI" % record.get("id"))
        if not record.get("evidence"):
            warnings.append("reference %s has no locatable evidence" % record.get("id"))
    for evidence in plan.get("evidence") or []:
        for field in ("path", "locator", "quote"):
            if not evidence.get(field):
                problems.append("evidence lacks %s" % field)
    return {"problems": problems, "warnings": warnings,
            "blocking": len(problems),
            "source_count": len(plan.get("sources") or []),
            "evidence_count": len(plan.get("evidence") or [])}


def render_report(plan):
    problem = plan.get("problem_definition") or {}
    lines = [
        "# 科研选题与创新性评估报告", "",
        "- case_id: `%s`" % plan.get("case_id"),
        "- 生成时间: %s" % plan.get("generated_at"),
        "- 证据范围: **provided_corpus_only**（材料 %d 份，文献记录 %d 条）" % (
            len(plan.get("sources") or []), len(plan.get("references") or [])),
        "",
        "> 本报告不宣称全球首次，也不代替在线检索；所有“未被覆盖”只指所提供语料。",
        "", "## 1. 问题定义（可检验性）", "",
        "- 研究问题：%s" % problem.get("question"),
        "- 研究对象：%s" % problem.get("population"),
        "- 关键变量：%s" % problem.get("variables"),
        "- 比较基线：%s" % problem.get("baseline"),
        "- 预期结论：%s" % problem.get("expected_conclusion"),
        "- 证伪条件：%s" % "；".join(problem.get("falsification_conditions") or []),
        "- 当前可检验：%s" % ("是" if problem.get("testable") else "否（需 PI 补齐）"),
        "", "## 2. 已有工作比较", "",
        "| 记录 | 可比较 | 比较字段 | 理由 |", "|---|---|---|---|",
    ]
    for row in plan.get("comparison_matrix") or []:
        lines.append("| %s | %s | %s | %s |" % (
            row.get("record_id"), "是" if row.get("directly_comparable") else "否",
            ", ".join("%s=%s" % (k, v) for k, v in (row.get("value") or {}).items()),
            str(row.get("reason")).replace("|", "/")))
    lines += ["", "## 3. 创新点识别（逐条划界）", ""]
    for claim in plan.get("novelty_claims") or []:
        lines.append("- `%s` [%s] %s" % (claim.get("id"), claim.get("type"),
                                         claim.get("claim")))
        lines.append("  - 判定：`%s`；范围：`%s`；覆盖记录：%s" % (
            claim.get("verdict"), claim.get("scope"),
            ", ".join(x["record_id"] for x in claim.get("covered_by") or []) or "无"))
        lines.append("  - 边界：%s" % claim.get("note"))
    if not plan.get("novelty_claims"):
        lines.append("- 材料未给出可识别的创新陈述（to_confirm）")
    lines += ["", "## 4. 可行性判断（带条件）", ""]
    for premise in (plan.get("feasibility") or {}).get("premises") or []:
        lines.append("- %s：`%s`（%s）" % (
            premise.get("premise"), premise.get("status"),
            premise.get("value") if premise.get("status") == "evidence_in_material"
            else "材料未写，需补证"))
    lines.append("- 结论：**%s**" % (plan.get("feasibility") or {}).get("conclusion"))
    for condition in plan.get("conditions") or []:
        lines.append("- 条件：%s" % condition)
    lines += ["", "## 5. 研究空白与冲突证据", ""]
    for gap in plan.get("gap_analysis") or []:
        lines.append("- [%s] %s（依据：%s）" % (gap.get("status"), gap.get("gap"),
                                               gap.get("basis")))
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
              "python3 scripts/run_skill.py --input /input --work /app/work --output /app/output",
              "```",
              "同一语料与同一规则重跑，可复现相同的覆盖判定；在线检索结果不得由本脚本伪造。", ""]
    return "\n".join(lines)


def contract_payloads(plan):
    return {
        "研究产物": {"report": "research-report.md",
                     "problem_definition": plan.get("problem_definition"),
                     "comparison_matrix": plan.get("comparison_matrix"),
                     "novelty_claims": plan.get("novelty_claims"),
                     "feasibility": plan.get("feasibility")},
        "证据矩阵": plan.get("comparison_matrix"),
        "证据矩阵表": plan.get("comparison_matrix"),
        "不确定性": plan.get("uncertainties"),
        "参考文献": plan.get("references"),
        "文献清单": plan.get("references"),
        "问题定义": plan.get("problem_definition"),
        "比较矩阵": plan.get("comparison_matrix"),
        "创新点": plan.get("novelty_claims"),
        "可行性": plan.get("feasibility"),
        "研究空白": plan.get("gap_analysis"),
        "证据": plan.get("evidence"),
        "evidence_record": plan.get("evidence"),
        "research_report": {"path": "research-report.md",
                            "summary": plan.get("summary")},
        "evidence_matrix": plan.get("comparison_matrix"),
        "references": plan.get("references"),
        "novelty_claims": plan.get("novelty_claims"),
        "feasibility": plan.get("feasibility"),
        "status_note": plan.get("uncertainties"),
    }


def write_contract_artifacts(output, contract, plan, fill_only=False):
    payloads = contract_payloads(plan)
    keys = [str(k) for k in (contract.keys() if isinstance(contract, dict)
                             else contract if isinstance(contract, list) else [])]
    written = {}
    for key in keys:
        value = payloads.get(key)
        if value is None:
            value = {"field": key, "status": "partial",
                     "note": "该契约键没有专用字段；以 research-report.md 与 result.json 为准",
                     "deliverable": "research-report.md",
                     "uncertainties": plan.get("uncertainties")}
        path = output / (safe_name(key) + ".json")
        emit_json(path, value, fill_only=fill_only)
        written[key] = path.name
    return written


def merge_work(existing, generated, fill_only=False):
    if not isinstance(existing, dict) or not existing:
        return generated
    out = dict(existing)
    protected = {"case_id", "mode", "_generated_by", "_domain_checks"}
    for key, value in generated.items():
        if key in protected:
            out.setdefault(key, value)
            continue
        if key not in out or out.get(key) in (None, "", [], {}):
            out[key] = value
    out["_generated_by"] = "domain_work.py" if fill_only or existing.get("_generated_by") \
        else "model+domain_work.py"
    out["_domain_checks"] = generated.get("_domain_checks")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--fill-missing", action="store_true")
    args = parser.parse_args(argv)
    work, output = Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    case = read_json(Path(args.input) / "case.json", {})
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    sources = load_sources(work)
    plan = build_plan(sources)
    plan["case_id"] = case_id
    work_path = work / "research-plan.json"
    plan = merge_work(read_json(work_path, {}), plan, fill_only=args.fill_missing)
    plan["case_id"] = case_id
    write_json(work_path, plan)

    fill_only = bool(args.fill_missing)
    emit_text(output / "research-report.md", render_report(plan), fill_only)
    emit_json(output / "evidence-matrix.json", {
        "case_id": case_id, "scope": "provided_corpus_only",
        "records": plan.get("references"),
        "matrix": plan.get("comparison_matrix"),
        "claims": plan.get("novelty_claims"),
        "checks": plan.get("_domain_checks"),
    }, fill_only)
    emit_json(output / "references.json", {
        "case_id": case_id,
        "references": plan.get("references"),
        "note": "Only identifiers actually parsed from the provided material are listed.",
    }, fill_only)
    contract = case.get("output_contract") or {}
    written = write_contract_artifacts(output, contract, plan, fill_only=fill_only)
    audit = {
        "case_id": case_id, "generated_at": now_iso(), "script": "domain_work.py",
        "source_count": len(sources),
        "source_paths": [source.path for source in sources],
        "records_extracted": len(plan.get("references") or []),
        "evidence_count": len(plan.get("evidence") or []),
        "checks": plan.get("_domain_checks"),
        "contract_artifacts": written,
        "no_materials": len(sources) == 0,
    }
    write_json(work / "domain-status.json", audit)
    write_json(output / "domain-audit.json", audit)
    print(json.dumps({"script": "domain_work.py", "case_id": case_id,
                      "sources": len(sources),
                      "records": len(plan.get("references") or []),
                      "blocking_checks": (audit.get("checks") or {}).get("blocking", 0),
                      "contract_artifacts": written}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"script": "domain_work.py", "status": "error",
                          "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(0)
