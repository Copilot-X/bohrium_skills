#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalise the task into a genre-first writing work order.

Usage:
    python3 workorder.py --input /input --work /app/work [--json]

Reads ``case.json`` and ``task.md`` (never assumes labels are correct), then
writes ``<work>/workorder.json`` with genre, audience, length, contract and
content decisions.  Genre detection is keyword based; an ambiguous task is
marked ``generic_rewrite`` with an explicit uncertainty rather than silently
inventing a commercial plan.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # package mode (python -m scripts.workorder)
    from . import transfer_kit as kit
except ImportError:  # direct mode (python scripts/workorder.py)
    kit = __import__("transfer_kit")

GENRE_RULES = [
    {
        "genre": "investment_memo",
        "filename": "investment-memo.md",
        "patterns": [r"投资\s*memo", r"投资备忘录", r"投资建议", r"商业计划",
                     r"商业企划", r"融资", r"投资人", r"investment\s+memo",
                     r"business\s+plan", r"venture"],
        "sections": ["摘要", "技术事实与成熟度", "知识产权", "应用场景与合作路径",
                     "证据与风险", "待验证事项与里程碑"],
    },
    {
        "genre": "project_application",
        "filename": "proposal.md",
        "patterns": [r"项目申报", r"申报书", r"项目申请", r"立项", r"可行性研究",
                     r"proposal", r"application", r"grant"],
        "sections": ["问题与背景", "目标与考核指标", "技术路线", "现有基础与证据",
                     "阶段计划", "风险与应对", "预期成果"],
    },
    {
        "genre": "impact_statement",
        "filename": "impact-statement.md",
        "patterns": [r"impact\s+statement", r"影响力陈述", r"影响力声明",
                     r"社会影响", r"影响陈述", r"impact"],
        "sections": ["问题", "贡献", "受益对象", "证据", "边界与未验证部分"],
    },
    {
        "genre": "popular_article",
        "filename": "article.md",
        "patterns": [r"科普", r"公众", r"大众", r"非专业", r"通俗", r"面向社会",
                     r"popular", r"general\s+public", r"public\s+article"],
        "sections": ["为什么值得关注", "研究做了什么", "看到了什么",
                     "这意味着什么，还不意味着什么", "接下来需要回答的问题"],
    },
    {
        "genre": "tech_brief",
        "filename": "technical-brief.md",
        "patterns": [r"技术简报", r"技术摘要", r"技术报告", r"技术备忘",
                     r"tech(nical)?\s+brief", r"technical\s+report"],
        "sections": ["摘要", "技术事实与指标", "成熟度与证据", "对比与优势",
                     "风险、合规与边界", "结论与下一步"],
    },
]

FILENAME_RE = re.compile(
    r"(?:输出|保存为|文件名|deliverable|output(?:\s+file)?)\s*[:：为]?\s*"
    r"[`\"']?([A-Za-z0-9_\-一-鿿]+\.(?:md|docx|txt))[`\"']?", re.I)
LENGTH_RE = re.compile(
    r"(?:不超过|最多|上限|within|at\s+most|max(?:imum)?|约)\s*"
    r"(\d{2,6})\s*(字|字符|词|words?|pages?|页)", re.I)
EXPLICIT_LENGTH_RE = re.compile(
    r"(\d{2,6})\s*(字|字符|词|words?|pages?|页)\s*(?:以内|以下|左右|上限)?", re.I)
GENRE_BOILERPLATE = re.compile(
    r"(可以|允许|例如|比如|如|或|优先考虑|可选择|五个方向|体裁包括)")


def _load_json(path):
    data = kit.read_json(path, default=None)
    if data is None:
        return {}, "case_json_missing_or_invalid: %s" % path
    if not isinstance(data, dict):
        return {}, "case_json_not_object: %s" % path
    return data, None


def _collect_text(case, task_text):
    parts = []
    instr = case.get("task_instruction") or case.get("instruction") or ""
    if isinstance(instr, list):
        instr = " ".join(str(x) for x in instr)
    if instr:
        parts.append(("instruction", str(instr)))
    if task_text:
        parts.append(("task_md", task_text))
    return parts


def infer_genre(texts):
    scores = {}
    evidence = {}
    for source, text in texts:
        for rule in GENRE_RULES:
            for pat in rule["patterns"]:
                for m in re.finditer(pat, text, re.I):
                    # Prefer explicit task wording; ignore generic list boilerplate.
                    window = text[max(0, m.start() - 20): m.end() + 20]
                    weight = 1
                    if source == "instruction":
                        weight += 1
                    if GENRE_BOILERPLATE.search(window) and source == "task_md":
                        weight = max(1, weight - 1)
                    scores[rule["genre"]] = scores.get(rule["genre"], 0) + weight
                    evidence.setdefault(rule["genre"], []).append(
                        kit.excerpt(window, 70))
    if not scores:
        return "generic_rewrite", 0.15, []
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top, top_score = ordered[0]
    second = ordered[1][1] if len(ordered) > 1 else 0
    confidence = 0.85 if top_score >= second + 2 else 0.6
    if top_score == second:
        confidence = 0.4
    return top, confidence, kit.unique(evidence.get(top, []))[:4]


def genre_meta(genre):
    for rule in GENRE_RULES:
        if rule["genre"] == genre:
            return rule
    return {"genre": "generic_rewrite", "filename": "rewrite.md",
            "sections": ["背景与问题", "材料给出的发现", "意义与适用条件",
                         "边界与未验证部分", "下一步"]}


def infer_audience(text, genre):
    audience = {"role": "unknown", "background": "unknown",
                "reading_goal": "unknown", "evidence": []}
    role_patterns = [
        ("public", r"公众|大众|社会公众|非专业读者|普通人"),
        ("reviewers", r"评审专家|评审|专家委员会|立项评审|同行评议"),
        ("industry", r"企业|产业界|合作方|工程师|技术转移|厂商"),
        ("investors", r"投资人|投资机构|基金|资本|VC|PE"),
        ("policymakers", r"政策|管理部门|政府|监管"),
        ("students", r"学生|教学|课堂"),
        ("peers", r"同行|科研人员|研究者|领域专家"),
    ]
    background_patterns = [
        ("none", r"无专业背景|非专业|没有专业背景|不具备专业背景|普通读者"),
        ("cross_domain", r"跨领域|跨学科|非本领域|相邻领域"),
        ("same_domain", r"同领域|本领域|专业读者|领域内"),
    ]
    goal_patterns = [
        ("understand_significance", r"理解意义|了解意义|为什么重要|科普"),
        ("evaluate_feasibility", r"评估可行性|判断可行性|评审|立项"),
        ("judge_value", r"判断合作价值|合作价值|投资价值|商业价值"),
        ("understand_method", r"了解方法|技术细节|方法要点"),
    ]
    for key, pat in role_patterns:
        m = re.search(pat, text, re.I)
        if m:
            audience["role"] = key
            audience["evidence"].append(m.group(0))
            break
    for key, pat in background_patterns:
        m = re.search(pat, text, re.I)
        if m:
            audience["background"] = key
            audience["evidence"].append(m.group(0))
            break
    for key, pat in goal_patterns:
        m = re.search(pat, text, re.I)
        if m:
            audience["reading_goal"] = key
            audience["evidence"].append(m.group(0))
            break
    defaults = {
        "popular_article": ("public", "none", "understand_significance"),
        "project_application": ("reviewers", "cross_domain", "evaluate_feasibility"),
        "impact_statement": ("policymakers", "cross_domain", "understand_significance"),
        "tech_brief": ("industry", "cross_domain", "evaluate_feasibility"),
        "investment_memo": ("investors", "cross_domain", "judge_value"),
        "generic_rewrite": ("unknown", "unknown", "unknown"),
    }
    d_role, d_bg, d_goal = defaults.get(genre, defaults["generic_rewrite"])
    audience["role"] = audience["role"] if audience["role"] != "unknown" else d_role
    audience["background"] = (audience["background"]
                              if audience["background"] != "unknown" else d_bg)
    audience["reading_goal"] = (audience["reading_goal"]
                                if audience["reading_goal"] != "unknown" else d_goal)
    audience["source"] = ("task_explicit" if audience["evidence"]
                          else "genre_default")
    return audience


def infer_length(text):
    m = LENGTH_RE.search(text)
    if not m:
        m = EXPLICIT_LENGTH_RE.search(text)
    if not m:
        return {"raw": None, "kind": "none", "value": None}
    value = int(m.group(1))
    unit = m.group(2).lower()
    if unit in ("字", "字符"):
        kind = "characters"
    elif unit in ("词", "word", "words"):
        kind = "words"
    else:
        kind = "pages"
    return {"raw": m.group(0), "kind": kind, "value": value}


def infer_language(text, inventory):
    explicit = re.search(r"(?:使用|用|语言|language)\s*[:：]?\s*(中文|汉语|英文|english)", text, re.I)
    if explicit:
        return "zh" if explicit.group(1).lower() in ("中文", "汉语") else "en"
    lang = kit.guess_language(text)
    if lang != "unknown":
        return lang
    for f in inventory.get("files", []):
        if f.get("readable") and f.get("language") in ("zh", "en"):
            return f["language"]
    return "zh"


def extract_clauses(text, markers):
    out = []
    for line in text.splitlines():
        for m in markers:
            idx = line.find(m)
            if idx >= 0:
                clause = line[idx + len(m):].strip(" ：:，,;；")
                if 2 <= len(clause) <= 160:
                    out.append(clause)
                break
    return kit.unique(out)


def extract_requirements(text):
    reqs = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^(?:[-*+]|\d+[.)、])\s+", stripped):
            item = re.sub(r"^(?:[-*+]|\d+[.)、])\s+", "", stripped)
            if 8 <= len(item) <= 220 and not item.startswith(("http", "题目", "赛题")):
                reqs.append(item)
    return kit.unique(reqs)[:14]


def extract_sections(text, default_sections):
    """Use an explicit task outline only when the task says 'structure/outline'."""
    trigger = re.search(r"(?:按以下|按如下|采用以下|结构|提纲|outline|structure)"
                        r"[^\n]{0,30}(?:结构|提纲|组织|安排|写|包括)?", text, re.I)
    if not trigger:
        return [{"heading": h, "source": "genre_default"} for h in default_sections]
    tail = text[trigger.end():]
    headings = []
    for line in tail.splitlines()[:20]:
        stripped = line.strip()
        m = re.match(r"^(?:[-*+]|\d+[.)、])\s*([^\n]{2,40})$", stripped)
        if m:
            headings.append(m.group(1).strip(" ：:"))
    if 2 <= len(headings) <= 10:
        return [{"heading": h, "source": "task_explicit"} for h in headings]
    return [{"heading": h, "source": "genre_default"} for h in default_sections]


def contract_fields(case, uncertainty):
    contract = case.get("output_contract")
    if isinstance(contract, str):
        contract_obj = {"raw": contract}
    elif isinstance(contract, dict):
        contract_obj = dict(contract)
    else:
        contract_obj = {}
        uncertainty.append({
            "item": "output_contract_missing",
            "detail": "case.json has no output_contract object",
            "action": "use the official five fields and state the assumption",
        })
    defaults = {
        "case_id": "原样保留输入标识",
        "deliverable": "可编辑的目标文稿/结构化产物",
        "preserved_facts": "数字、条件、术语和来源不可凭空改变",
        "evidence_map": "关键论断到来源定位的映射",
        "change_log": "修改动作、原因、人工确认项和未解决问题",
    }
    fields = []
    for key, requirement in defaults.items():
        present = key in contract_obj
        if not present:
            uncertainty.append({
                "item": "contract_field_added",
                "detail": key,
                "action": "official writing contract field added with default semantics",
            })
        fields.append({
            "field": key,
            "requirement": contract_obj.get(key, requirement),
            "source": "output_contract" if present else "official_default",
        })
    for key, requirement in contract_obj.items():
        if key not in defaults:
            fields.append({"field": key, "requirement": requirement,
                           "source": "output_contract"})
    return contract_obj, fields


def build(input_dir, work_dir):
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    uncertainty = []
    case_path = input_dir / "case.json"
    task_path = input_dir / "task.md"
    case, case_err = _load_json(case_path)
    if case_err:
        uncertainty.append({"item": "case_json", "detail": case_err,
                            "action": "proceed from task.md and materials"})
    try:
        task_text = kit.read_text(task_path) if task_path.is_file() else ""
    except OSError as exc:
        task_text = ""
        uncertainty.append({"item": "task_md", "detail": str(exc),
                            "action": "proceed from case.json"})
    if not task_text:
        uncertainty.append({"item": "task_md_missing",
                            "detail": "no task.md; genre inferred from case.json",
                            "action": "state the inferred genre in result.json"})
    combined = "\n".join(t for _, t in _collect_text(case, task_text))
    if not combined.strip():
        uncertainty.append({"item": "task_description_missing",
                            "detail": "both case.json and task.md are empty",
                            "action": "write a generic evidence-based rewrite and mark partial"})

    genre, confidence, genre_evidence = infer_genre(_collect_text(case, task_text))
    meta = genre_meta(genre)
    if genre == "generic_rewrite":
        uncertainty.append({
            "item": "genre_ambiguous",
            "detail": "no canonical genre keyword found",
            "action": "use generic evidence-based structure; do not add TRL/market/IP sections",
        })
    elif confidence < 0.6:
        uncertainty.append({
            "item": "genre_low_confidence",
            "detail": "top genre scores are close: %s" % genre,
            "action": "confirm against task.md wording; keep genre-specific claims minimal",
        })

    audience = infer_audience(combined, genre)
    length = infer_length(combined)
    inventory = kit.read_json(work_dir / "inventory.json", default={}) or {}
    language = infer_language(combined, inventory)
    explicit_file = FILENAME_RE.search(combined)
    deliverable_name = (explicit_file.group(1)
                        if explicit_file else meta["filename"])
    if not deliverable_name.lower().endswith((".md", ".txt")):
        deliverable_name = Path(deliverable_name).stem + ".md"

    must_keep = extract_clauses(
        combined, ["必须保留", "需保留", "保留以下", "must keep", "preserve"])
    must_not_add = extract_clauses(
        combined, ["不得添加", "不要添加", "不能添加", "不得写", "不要写",
                   "禁止", "must not", "do not add"])
    can_compress = extract_clauses(
        combined, ["可以压缩", "可压缩", "可以省略", "can compress"])
    if not must_keep:
        must_keep = ["关键数字与单位", "实验/样本条件", "限定语与不确定性",
                     "图表编号与引用", "同一概念的术语"]
    if not can_compress:
        can_compress = ["背景综述", "方法细节", "文献回顾", "重复性描述"]
    if not must_not_add:
        must_not_add = ["材料没有的市场规模/竞品参数", "未经证实的因果结论",
                        "夸大或无法核实的措辞", "保密内容"]

    contract_obj, fields = contract_fields(case, uncertainty)
    source = case.get("source") if isinstance(case.get("source"), dict) else {}
    case_id = (case.get("case_id") or contract_obj.get("case_id")
               or "unknown-case")
    technology = case.get("technology")
    title = source.get("title") or case.get("title")
    if not title and isinstance(technology, dict):
        title = technology.get("name")
    if not title:
        first_heading = re.search(r"^#{1,2}\s+(.+)$", task_text, re.M)
        title = first_heading.group(1).strip() if first_heading else "研究成果转化稿"

    workorder = {
        "case_id": str(case_id),
        "source_title": str(title),
        "genre": genre,
        "genre_confidence": confidence,
        "genre_evidence": genre_evidence,
        "audience": audience,
        "language": language,
        "length_limit": length,
        "deliverable_name": deliverable_name,
        "output_format": "markdown",
        "required_sections": extract_sections(combined, meta["sections"]),
        "must_keep": must_keep,
        "can_compress": can_compress,
        "must_not_add": must_not_add,
        "task_requirements": extract_requirements(task_text or combined),
        "output_contract": contract_obj,
        "contract_fields": fields,
        "constraints": {
            "no_network": True,
            "editable_output": True,
            "source_files": [f["path"] for f in
                             (kit.read_json(work_dir / "inventory.json", {}) or {}).get("files", [])
                             if f.get("role") != "task_metadata"],
        },
        "uncertainty": uncertainty,
        "generated_at": kit.now_iso(),
    }
    kit.write_json(work_dir / "workorder.json", workorder)
    return workorder


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    wo = build(args.input, args.work)
    print(json.dumps({
        "case_id": wo["case_id"], "genre": wo["genre"],
        "audience": wo["audience"]["role"],
        "language": wo["language"], "deliverable": wo["deliverable_name"],
        "length_limit": wo["length_limit"], "uncertainty": wo["uncertainty"],
        "workorder": str(Path(args.work) / "workorder.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
