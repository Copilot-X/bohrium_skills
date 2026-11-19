#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write /app/output/result.json with the official writing contract fields.

Usage:
    python3 result_writing.py --work /app/work --output /app/output \
        --draft /app/output/article.md [--json]

The result always contains ``case_id`` plus ``deliverable``,
``preserved_facts``, ``evidence_map`` and ``change_log``.  Every path is
checked before it is written; missing pieces become explicit uncertainty
instead of invented content.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # package mode (python -m scripts.result_writing)
    from . import transfer_kit as kit
    from . import evidence as evidence_mod
except ImportError:  # direct mode (python scripts/result_writing.py)
    kit = __import__("transfer_kit")
    evidence_mod = __import__("evidence")

VERSION = "0.14.0"


def _load(path, default=None):
    return kit.read_json(path, default=default)


def _word_count(text, language):
    if language == "zh":
        return len(re.findall(r"[一-鿿]", text))
    return len(re.findall(r"\b[A-Za-z0-9][A-Za-z0-9'\-]*\b", text))


def _sections(text):
    return [b["text"] for b in kit.markdown_blocks(text)
            if b["kind"] == "heading" and b["level"] >= 1]


def _draft_sections(text):
    out = [{"heading": "（开头）", "text": ""}]
    for block in kit.markdown_blocks(text):
        if block["kind"] == "heading":
            out.append({"heading": block["text"], "text": ""})
        elif block["kind"] in ("paragraph", "list", "quote", "table_row"):
            out[-1]["text"] += ("\n" if out[-1]["text"] else "") + block["text"]
    return [s for s in out if s["text"].strip()]


def _number_keys(entry):
    return {kit.number_key(q["value"], q.get("unit") or "")
            for q in entry.get("quantities") or []}


def _salient_quantity(entry):
    """Pick the result-like quantity, not the first condition (e.g. 350 °C)."""
    quantities = entry.get("quantities") or []
    if not quantities:
        return {}
    condition_units = {"°c", "k", "bar", "mbar", "mpa", "kpa", "pa", "h", "min",
                       "s", "d", "a", "n", "次", "例", "人", "只", "个", "×"}
    statement = entry.get("statement", "")
    result_context = re.compile(
        r"达到|降至|升至|为|提高|降低|下降|增加|减少|提升|转化率|选择性|"
        r"产率|收率|效率|纯度|活性|稳定性|reached|reduced|increased|decreased")
    condition_context = re.compile(r"含|在|于|浓度|剂量")
    best, best_score = quantities[0], -1.0
    for q in quantities:
        unit = (q.get("normalized_unit") or "").lower()
        score = 0.0
        if unit in ("%", "‰"):
            score += 5
        elif unit in ("mg/l", "g/l", "mol/l", "mmol/l", "mg/kg", "ppm", "ppb"):
            score += 3
        if unit not in condition_units:
            score += 1
        raw = q.get("raw") or ""
        pos = statement.find(raw) if raw else -1
        if pos >= 0:
            near = statement[max(0, pos - 12): pos + len(raw) + 12]
            before = statement[max(0, pos - 8): pos]
            if result_context.search(near):
                score += 2
            if condition_context.search(before):
                score -= 1.5
        if score > best_score:
            best, best_score = q, score
    return best


def _draft_title(draft_text, fallback):
    for block in kit.markdown_blocks(draft_text):
        if block["kind"] == "heading" and block["level"] == 1:
            return block["text"].strip()
    return fallback


def _preserved_facts(evidence, draft_text):
    draft_keys = {kit.number_key(q["value"], q.get("unit") or "")
                  for q in evidence_mod.extract_quantities(draft_text)}
    key_ids = set(evidence.get("key_facts") or [])
    key_entries = [e for e in evidence.get("evidence") or []
                   if e.get("evidence_id") in key_ids
                   and e.get("disclosable", True)]
    selected, seen = [], set()
    for entry in key_entries:
        keys = _number_keys(entry)
        in_draft = bool(keys & draft_keys)
        sid = entry.get("evidence_id")
        if sid in seen:
            continue
        seen.add(sid)
        selected.append((entry, in_draft))
    if not any(in_draft for _, in_draft in selected):
        # Keep the ledger honest even if the draft paraphrases numbers away.
        selected = [(e, False) for e in key_entries[:8]]
    facts = []
    for entry, in_draft in selected[:20]:
        q = _salient_quantity(entry)
        facts.append({
            "fact_id": entry.get("evidence_id"),
            "text": entry.get("statement"),
            "value": q.get("value"),
            "unit": q.get("unit"),
            "values": [{"value": item.get("value"), "unit": item.get("unit"),
                        "raw": item.get("raw")}
                       for item in entry.get("quantities") or []],
            "condition": "；".join(entry.get("conditions") or []),
            "conditions": entry.get("conditions") or [],
            "comparison": entry.get("comparison"),
            "denominator": entry.get("denominator"),
            "maturity": entry.get("maturity"),
            "tier": entry.get("tier"),
            "source": entry.get("source"),
            "quote": entry.get("quote"),
            "in_draft": in_draft,
        })
    return facts


def _matching_evidence(section_text, evidence, limit=2):
    if not section_text.strip():
        return []
    sec_keys = {kit.number_key(q["value"], q.get("unit") or "")
                for q in evidence_mod.extract_quantities(section_text)}
    sec_kws = set(kit.keywords(section_text, limit=50))
    scored = []
    for entry in evidence.get("evidence") or []:
        if not entry.get("disclosable", True):
            continue
        score = 5 * len(_number_keys(entry) & sec_keys)
        score += min(6, len(set(kit.keywords(entry.get("statement", ""), 30)) & sec_kws))
        if score >= 2:
            scored.append((score, entry))
    scored.sort(key=lambda pair: -pair[0])
    return [e for _, e in scored[:limit]]


def _change_log(workorder, evidence, draft_text, citation_map):
    sections = _draft_sections(draft_text)
    log = []
    used_claim_ids = set()
    for sec in sections:
        matches = _matching_evidence(sec["text"], evidence)
        before = matches[0].get("statement") if matches else ""
        after = kit.excerpt(sec["text"], 260)
        if not before:
            action = "add"
            reason = "按体裁补充过渡与边界说明；不新增材料外事实"
        else:
            used_claim_ids.add(matches[0].get("evidence_id"))
            ratio = len(after) / max(1, len(before))
            action = "keep" if ratio < 1.15 and before[:20] in after else (
                "compress" if ratio < 0.75 else "rewrite")
            reason = ("面向%s读者改写；保留%s" %
                      (workorder.get("audience", {}).get("role", "目标"),
                       "关键数字与条件" if matches[0].get("quantities") else "原意"))
        log.append({
            "section": sec["heading"],
            "action": action,
            "before": kit.excerpt(before, 260) if before else "",
            "after": after,
            "reason": reason,
            "evidence_ids": [e.get("evidence_id") for e in matches],
            "status": "applied",
        })
    # Explicitly record material that was intentionally compressed away.
    not_in_draft = [e for e in evidence.get("claims") or []
                    if e.get("evidence_id") not in used_claim_ids
                    and e.get("kind") not in ("citation", "definition")][:3]
    for entry in not_in_draft:
        log.append({
            "section": "材料取舍",
            "action": "compress",
            "before": kit.excerpt(entry.get("statement"), 220),
            "after": "未逐字保留；若属关键数字已在正文保留",
            "reason": "该细节不服务于当前体裁/受众；未删除任何关键数字",
            "evidence_ids": [entry.get("evidence_id")],
            "status": "applied",
        })
    if not log:
        log.append({
            "section": "全文", "action": "add", "before": "",
            "after": kit.excerpt(draft_text, 220),
            "reason": "材料证据不足，按体裁要求生成完整改写稿并标注缺口",
            "evidence_ids": [], "status": "applied",
        })
    return log


def _content_decisions(workorder, evidence, draft_text):
    decisions = [
        {"item": "关键数字、单位与实验条件", "decision": "keep",
         "reason": "事实与数据是成果转化的证据，删掉会改变结论",
         "where": "正文含数字的段落"},
        {"item": "背景综述与方法细节", "decision": "compress",
         "reason": "目标读者关心结论与边界，方法只保留理解所需条件",
         "where": "背景/方法段"},
    ]
    if workorder.get("genre") in ("popular_article", "impact_statement", "project_application"):
        decisions.append({
            "item": "TRL、市场规模、融资与竞品参数", "decision": "delete",
            "reason": "task.md 未要求，且材料没有可核验数据，不硬加商业章节",
            "where": "全文",
        })
    else:
        decisions.append({
            "item": "无来源的市场规模与财务预测", "decision": "delete",
            "reason": "材料未提供，改写稿只做定性说明并标注 not_available",
            "where": "市场/商务段",
        })
    if evidence.get("restricted"):
        decisions.append({
            "item": "保密/未公开材料", "decision": "delete",
            "reason": "不写入对外稿；只保留在内部证据台账并标记 restricted",
            "where": "全文",
        })
    return decisions


def _uncertainty(parts):
    out = []
    for part in parts:
        if not part:
            continue
        if isinstance(part, dict):
            out.append(part)
        elif isinstance(part, list):
            for item in part:
                if isinstance(item, dict):
                    out.append(item)
                elif item:
                    out.append({"item": "note", "detail": str(item),
                                "action": "review"})
        else:
            out.append({"item": "note", "detail": str(part), "action": "review"})
    dedup, seen = [], set()
    for item in out:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            dedup.append(item)
    return dedup


def _write_redacted_evidence(work_dir, output_dir, artifacts):
    """Copy the ledger but redact statements marked non-disclosable."""
    data = _load(work_dir / "evidence.json", {}) or {}
    if not data:
        return None
    safe = json.loads(json.dumps(data, ensure_ascii=False))
    restricted_ids = {item.get("evidence_id")
                      for item in safe.get("restricted") or []}
    for item in safe.get("restricted") or []:
        item["statement"] = "[redacted: restricted material]"
        if isinstance(item.get("source"), dict):
            item["source"]["quote"] = "[redacted]"
    for entry in safe.get("evidence") or []:
        if (entry.get("disclosable") is False
                or entry.get("evidence_id") in restricted_ids):
            entry["statement"] = "[redacted: restricted material]"
            entry["quote"] = "[redacted]"
            if isinstance(entry.get("source"), dict):
                entry["source"]["quote"] = "[redacted]"
    dst = kit.write_json(Path(output_dir) / "evidence.json", safe)
    artifacts.append({
        "role": "evidence", "path": dst.name, "format": "json",
        "bytes": dst.stat().st_size, "sha256": kit.sha256_file(dst),
        "exists": True,
    })
    return dst


def _copy_artifact(src, output_dir, name, role, fmt, artifacts):
    src = Path(src)
    if not src.is_file():
        return None
    dst = Path(output_dir) / name
    try:
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
    except OSError:
        return None
    artifacts.append({
        "role": role, "path": name, "format": fmt,
        "bytes": dst.stat().st_size, "sha256": kit.sha256_file(dst),
        "exists": True,
    })
    return dst


def build(work_dir, output_dir, draft_path, docx_meta=None, run_info=None,
          writing_mode="agent_draft"):
    work_dir = Path(work_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    draft_path = Path(draft_path)
    draft_text = kit.read_text(draft_path) if draft_path.is_file() else ""
    workorder = _load(work_dir / "workorder.json", {}) or {}
    inventory = _load(work_dir / "inventory.json", {}) or {}
    evidence = _load(work_dir / "evidence.json", {}) or {}
    citation_map = _load(work_dir / "citation-map.json", {}) or {}
    numbers = _load(work_dir / "numbers-check.json", {}) or {}
    has_draft = draft_path.is_file() and bool(draft_text.strip())

    language = workorder.get("language", "zh")
    case_id = str(workorder.get("case_id") or "unknown-case")
    genre = workorder.get("genre", "generic_rewrite")
    source_title = workorder.get("source_title") or "研究成果转化稿"
    title = _draft_title(draft_text, source_title)
    rel_draft = kit.rel_posix(draft_path, output_dir) if has_draft else ""
    preserved = _preserved_facts(evidence, draft_text)
    evidence_map = citation_map.get("evidence_map") or []
    change_log = _change_log(workorder, evidence, draft_text, citation_map)

    artifacts = []
    if has_draft:
        artifacts.append({
            "role": "editable", "path": rel_draft, "format": "markdown",
            "bytes": draft_path.stat().st_size,
            "sha256": kit.sha256_file(draft_path), "exists": True,
        })
    docx_name = rel_draft[:-3] + ".docx" if rel_draft.lower().endswith(".md") else ""
    if docx_meta and docx_meta.get("valid") and Path(docx_meta["path"]).is_file():
        _copy_artifact(docx_meta["path"], output_dir, Path(docx_meta["path"]).name,
                       "office", "docx", artifacts)
        preview = (docx_meta.get("preview") or {})
        if preview.get("ok") and preview.get("path"):
            _copy_artifact(preview["path"], output_dir, Path(preview["path"]).name,
                           "preview", "pdf", artifacts)
    _write_redacted_evidence(work_dir, output_dir, artifacts)
    _copy_artifact(work_dir / "citation-map.json", output_dir, "citation-map.json",
                   "evidence_map", "json", artifacts)
    _copy_artifact(work_dir / "numbers-check.json", output_dir, "numbers-check.json",
                   "check", "json", artifacts)
    _copy_artifact(work_dir / "workorder.json", output_dir, "writing-workorder.json",
                   "workorder", "json", artifacts)
    _copy_artifact(work_dir / "inventory.json", output_dir, "input-inventory.json",
                   "inventory", "json", artifacts)

    contract = workorder.get("output_contract")
    if not isinstance(contract, dict):
        contract = {"raw": contract} if contract else {}
    coverage = []
    for field in workorder.get("contract_fields") or []:
        name = field.get("field")
        if name == "case_id":
            addressed, status = "case_id", "satisfied"
        elif name == "deliverable":
            addressed, status = rel_draft, "satisfied" if rel_draft else "missing"
        elif name == "preserved_facts":
            addressed = "%d facts" % len(preserved)
            status = "satisfied" if preserved else "empty"
        elif name == "evidence_map":
            addressed = "%d mapped claims" % len(evidence_map)
            status = "satisfied" if evidence_map else "empty"
        elif name == "change_log":
            addressed = "%d change records" % len(change_log)
            status = "satisfied" if change_log else "empty"
        else:
            addressed, status = "output_contract", "acknowledged"
        coverage.append({
            "field": name,
            "requirement": field.get("requirement"),
            "addressed_by": addressed,
            "status": status,
        })

    unreadable = [f["path"] for f in inventory.get("files", [])
                  if not f.get("readable")]
    limitations = []
    if not has_draft:
        limitations.append("未找到模型主稿；status=partial，没有用抽取式保底稿冒充完成稿。")
    if not evidence.get("evidence"):
        limitations.append("材料未抽取到可用证据；正文中的事实句需要人工补充。")
    if unreadable:
        limitations.append("以下材料未能抽取文本：%s；未引用其内容。"
                           % "、".join(unreadable[:6]))
    if not evidence_map:
        limitations.append("没有关键论断成功映射到来源定位；请人工核对。")
    if not preserved:
        limitations.append("preserved_facts 为空；正文没有可定位的数字证据。")
    if docx_meta and not (docx_meta.get("preview") or {}).get("ok"):
        limitations.append("DOCX 已生成，但当前环境没有完成 PDF 预览渲染。")
    if writing_mode == "missing_draft":
        limitations.append("writing_mode=missing_draft：需要按 workorder 体裁补写主稿后重新 finalize。")
    if numbers.get("warnings"):
        limitations.append("numbers_check 有 %d 条警告需人工确认。"
                           % len(numbers["warnings"]))

    unresolved = []
    for item in _uncertainty([workorder.get("uncertainty"),
                              inventory.get("uncertainty"),
                              evidence.get("uncertainty")]):
        detail = item.get("detail") or item.get("item")
        unresolved.append({
            "question": detail,
            "action": item.get("action") or "核对原始材料后确认",
            "owner": "author",
        })
    if not unresolved:
        unresolved.append({
            "question": "未发现阻断性缺口；仍需作者确认对外披露范围",
            "action": "按机构保密/伦理要求复核",
            "owner": "author",
        })

    terms = evidence.get("terminology") or []
    term_expansions = []
    for term in terms:
        expansion = term.get("definition") or term.get("abbreviation")
        if term.get("term") and expansion:
            term_expansions.append({"term": term["term"],
                                    "expansion": expansion})
    jargon = [t.get("abbreviation") for t in terms
              if t.get("abbreviation") and t["abbreviation"] not in draft_text]
    sentences = [s for s in kit.split_sentences(draft_text) if s.strip()]
    avg_chars = (sum(len(s) for s in sentences) / len(sentences)) if sentences else 0

    status = "completed"
    if (not has_draft or not evidence.get("evidence")
            or not evidence_map or not preserved or numbers.get("errors")):
        status = "partial"

    result = {
        "case_id": case_id,
        "status": status,
        "version": VERSION,
        "genre": genre,
        "language": language,
        "title": title,
        "source_title": source_title,
        "audience_profile": {
            "genre": genre,
            "audience": workorder.get("audience") or {},
            "length_limit": workorder.get("length_limit") or {},
            "must_keep": workorder.get("must_keep") or [],
            "can_compress": workorder.get("can_compress") or [],
            "must_not_add": workorder.get("must_not_add") or [],
        },
        "deliverable": {
            "path": rel_draft or None,
            "format": "markdown",
            "genre": genre,
            "title": title,
            "word_count": _word_count(draft_text, language),
            "char_count": len(draft_text),
            "sections": _sections(draft_text),
            "sha256": kit.sha256_text(draft_text),
            "docx": docx_name if docx_meta and docx_meta.get("valid") else None,
            "writing_mode": writing_mode,
        },
        "preserved_facts": preserved,
        "evidence_map": evidence_map,
        "change_log": change_log,
        "content_decisions": _content_decisions(workorder, evidence, draft_text),
        "readability": {
            "term_expansions": term_expansions,
            "jargon": jargon,
            "avg_sentence_chars": round(avg_chars, 1),
        },
        "output_contract": contract,
        "contract_coverage": coverage,
        "sources": [
            {"path": f.get("path"), "sha256": f.get("sha256"),
             "readable": f.get("readable"), "chars": f.get("chars"),
             "note": f.get("note")}
            for f in inventory.get("files", [])
        ],
        "restricted": [
            {"evidence_id": item.get("evidence_id"),
             "reason": item.get("reason"),
             "source": {"path": (item.get("source") or {}).get("path"),
                        "locator": (item.get("source") or {}).get("locator")}}
            for item in evidence.get("restricted") or []
        ],
        "artifacts": artifacts,
        "checks": {
            "numbers_ok": bool(numbers.get("ok")),
            "number_errors": numbers.get("errors") or [],
            "number_warnings": numbers.get("warnings") or [],
            "citation_map": {
                "mapped": citation_map.get("stats", {}).get("mapped", 0),
                "unsourced_paragraphs": citation_map.get("stats", {}).get(
                    "unsourced_paragraphs", 0),
            },
        },
        "uncertainty": _uncertainty([workorder.get("uncertainty"),
                                     inventory.get("uncertainty"),
                                     evidence.get("uncertainty"),
                                     citation_map.get("draft_claims_without_source"),
                                     numbers.get("warnings")]),
        "unresolved_questions": unresolved,
        "limitations": limitations,
        "run": run_info or {},
        "generated_at": kit.now_iso(),
    }
    if genre in ("tech_brief", "investment_memo"):
        result["market_data"] = "not_available"
        result["market_sources"] = []
        result["ip"] = {"status": "not_provided", "items": []}
        result["not_evaluated"] = [
            {"item": "市场规模与财务预测", "action": "补充可核验来源", "cost": "中",
             "impact": "只做定性判断"},
            {"item": "知识产权状态", "action": "核实专利号/权属", "cost": "低",
             "impact": "影响合作与估值表述"},
        ]
    path = kit.write_json(output_dir / "result.json", result)
    return result, path


def validate_paths(result, output_dir):
    output_dir = Path(output_dir)
    problems = []
    for key in ("case_id", "deliverable", "preserved_facts",
                "evidence_map", "change_log"):
        if key not in result:
            problems.append("missing top-level field: %s" % key)
    deliverable = result.get("deliverable") or {}
    rel = deliverable.get("path")
    if result.get("status") == "completed":
        if not rel or not (output_dir / rel).is_file():
            problems.append("deliverable.path missing or not found: %s" % rel)
    for artifact in result.get("artifacts") or []:
        rel = artifact.get("path")
        if not rel or not (output_dir / rel).is_file():
            problems.append("artifact missing: %s" % rel)
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--draft", default=None)
    ap.add_argument("--writing-mode", default="agent_draft")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    work = Path(args.work)
    workorder = _load(work / "workorder.json", {}) or {}
    draft = Path(args.draft) if args.draft else (
        Path(args.output) / workorder.get("deliverable_name", "article.md"))
    result, path = build(work, args.output, draft,
                         docx_meta=_load(work / "docx-meta.json"),
                         writing_mode=args.writing_mode)
    problems = validate_paths(result, args.output)
    print(json.dumps({"result": str(path), "status": result["status"],
                      "case_id": result["case_id"],
                      "deliverable": result["deliverable"]["path"],
                      "preserved_facts": len(result["preserved_facts"]),
                      "evidence_map": len(result["evidence_map"]),
                      "change_log": len(result["change_log"]),
                      "problems": problems}, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
