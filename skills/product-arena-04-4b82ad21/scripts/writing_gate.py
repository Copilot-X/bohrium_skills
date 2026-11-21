#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-finalize self-check for the introduction / related-work skill.

Deterministic checks only: structure, citation keys, numbers, groups, gap,
contributions, claim-evidence, citation metadata and verbatim evidence.
Scientific quality still needs the author's judgement.

Writes ``output/self-check.json``.  Exits 1 when ``problems`` is non-empty so
the agent can repair the draft; a crash is reported as ``status=error`` with
a valid self-check file and exit 0.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

CITE_BRACKET = re.compile(r"\[@([^\[\]]+)\]")
CITE_TEX = re.compile(r"\\cite[a-zA-Z]*\{([^}]+)\}")
NUMERIC_REF = re.compile(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)(?![A-Za-z0-9_])")
EMPTY_PHRASES = [
    "近年来", "受到广泛关注", "具有重要意义", "随着", "大量研究表明",
    "in recent years", "has attracted", "great importance",
    "many studies have shown", "novel", "first", "unprecedented",
]


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return default


def load_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_space(text):
    text = str(text or "").replace("–", "-").replace("—", "-").replace("−", "-")
    return re.sub(r"\s+", " ", text).strip()


def quote_found(source, quote):
    source = norm_space(source)
    quote = norm_space(quote)
    if not quote:
        return False, "empty quote"
    if quote in source:
        return True, "exact"
    parts = [p for p in re.split(r"…|\.\.\.", quote) if norm_space(p)]
    if len(parts) > 1:
        start = 0
        for part in parts:
            part = norm_space(part)
            if len(part) < 3:
                continue
            pos = source.find(part, start)
            if pos < 0:
                return False, "ellipsis part not found: %s" % part[:40]
            start = pos + len(part)
        return True, "ellipsis"
    return False, "quote not found verbatim"


def strip_citations(text):
    text = CITE_BRACKET.sub(" ", text or "")
    text = CITE_TEX.sub(" ", text)
    return text


def numbers_in(text):
    return {token for token in NUMBER.findall(strip_citations(text))}


def keys_in(text):
    keys = set()
    for match in CITE_BRACKET.finditer(text or ""):
        for key in re.split(r"\s*[;,]\s*", match.group(1)):
            key = key.strip().lstrip("@")
            if key:
                keys.add(key)
    for match in CITE_TEX.finditer(text or ""):
        for key in re.split(r"\s*,\s*", match.group(1)):
            key = key.strip()
            if key:
                keys.add(key)
    return keys


def numeric_refs_in(text):
    refs = set()
    for match in NUMERIC_REF.finditer(text or ""):
        for part in re.split(r"\s*[,;]\s*", match.group(1)):
            part = part.strip()
            if not part:
                continue
            if re.fullmatch(r"\d+", part):
                refs.add(int(part))
            else:
                bounds = re.split(r"[–-]", part)
                if len(bounds) == 2 and all(x.strip().isdigit() for x in bounds):
                    low, high = int(bounds[0]), int(bounds[1])
                    if 0 <= high - low <= 100:
                        refs.update(range(low, high + 1))
    return refs


class Sources:
    def __init__(self, input_dir):
        self.texts = {}
        base = Path(input_dir)
        if not base.exists():
            return
        for path in base.rglob("*"):
            if not path.is_file() or path.stat().st_size > 4_000_000:
                continue
            if path.suffix.lower() not in (".md", ".txt", ".tex", ".bib", ".ris",
                                           ".json", ".csv", ".yaml", ".yml"):
                continue
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            rel = path.relative_to(base).as_posix()
            self.texts[rel] = text
            self.texts.setdefault(path.name, text)

    def find(self, path):
        path = str(path or "")
        if not path:
            return None
        if path in self.texts:
            return self.texts[path]
        norm = path.replace("\\", "/")
        if norm in self.texts:
            return self.texts[norm]
        name = Path(norm).name
        if name in self.texts:
            return self.texts[name]
        for key, text in self.texts.items():
            if key.endswith(norm) or norm.endswith(key):
                return text
        return None


def load_sections(output):
    output = Path(output)
    intro = read_text(output / "introduction.md")
    related = read_text(output / "related-work.md")
    combined = read_text(output / "sections.md")
    if not related and combined:
        headings = list(re.finditer(r"(?im)^#{1,6}\s+(.+)$", combined))
        for index, heading in enumerate(headings):
            title = heading.group(1).strip().lower()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(combined)
            body = combined[heading.end():end].strip()
            if ("introduction" in title or "引言" in title) and not intro:
                intro = body
            elif ("related" in title or "相关" in title) and not related:
                related = body
    if not intro and not related and combined:
        intro = combined
    return intro, related, combined


def evaluate(input_dir, work_dir, output_dir):
    input_dir, work_dir, output_dir = Path(input_dir), Path(work_dir), Path(output_dir)
    case = load_json(input_dir / "case.json", {})
    plan = load_json(work_dir / "writing-plan.json", {})
    evidence = load_json(work_dir / "evidence.json", {})
    bibliography = load_json(work_dir / "bibliography.json", {})
    sources = Sources(input_dir)
    problems, warnings = [], []
    intro, related, combined = load_sections(output_dir)
    case_id = str(case.get("case_id") or "")
    if case_id and str(plan.get("case_id") or "") not in (case_id, ""):
        problems.append("plan case_id %r != input case_id %r"
                        % (plan.get("case_id"), case_id))
    if not intro.strip():
        problems.append("introduction.md is missing or empty")
    elif len(intro.strip()) < 100:
        warnings.append("introduction.md is shorter than 100 characters")
    if not related.strip():
        problems.append("related-work.md is missing or empty")
    elif len(related.strip()) < 100:
        warnings.append("related-work.md is shorter than 100 characters")
    text = (intro + "\n\n" + related).strip()
    if not text:
        return {
            "case_id": case_id or str(plan.get("case_id") or "CASE"),
            "status": "fail",
            "problems": problems,
            "warnings": warnings,
            "coverage": {},
        }

    entries = bibliography.get("entries") if isinstance(bibliography, dict) else []
    if not isinstance(entries, list):
        entries = []
    known_keys = {str(e.get("key")) for e in entries if isinstance(e, dict) and e.get("key")}
    used_keys = keys_in(text)
    unknown = sorted(k for k in used_keys if known_keys and k not in known_keys)
    if unknown:
        problems.append("unknown citation keys: %s" % ", ".join(unknown))
    if not known_keys and used_keys:
        warnings.append("no bibliography entries found; citation keys cannot be verified")

    numeric = numeric_refs_in(text)
    if numeric:
        if entries and max(numeric) > len(entries):
            problems.append("numeric citation out of range: %s (bibliography has %d entries)"
                            % (sorted(numeric), len(entries)))
        warnings.append("numeric citations [n] are fragile; prefer [@key]")

    source_text = "\n\n".join(
        sources.texts.get(p, "") for p in sorted({p for p in sources.texts}))
    if not source_text:
        source_text = "\n\n".join(str(s.get("text") or s.get("quote") or "")
                                  for s in (evidence.get("segments") or []))
    invented = sorted(n for n in numbers_in(text) if n not in numbers_in(source_text))
    if invented:
        problems.append("numbers not present in the manuscript: %s" % ", ".join(invented[:20]))

    groups = plan.get("related_work_groups") or []
    if not isinstance(groups, list) or not 2 <= len(groups) <= 4:
        problems.append("related_work_groups must contain 2-4 groups (got %s)"
                        % (len(groups) if isinstance(groups, list) else "non-list"))
        groups = groups if isinstance(groups, list) else []
    for index, group in enumerate(groups, 1):
        if not isinstance(group, dict):
            problems.append("group %d is not an object" % index)
            continue
        name = str(group.get("group") or "group %d" % index)
        if not group.get("group"):
            problems.append("group %d lacks group name" % index)
        if group.get("dimension") not in ("problem", "method", "data", "metric"):
            problems.append("%s: invalid dimension %r" % (name, group.get("dimension")))
        works = group.get("works") or []
        if not isinstance(works, list) or len(works) < 2:
            problems.append("%s: fewer than 2 works" % name)
            works = works if isinstance(works, list) else []
        for work in works:
            if not isinstance(work, dict):
                problems.append("%s: work entry is not an object" % name)
                continue
            key = str(work.get("citation_key") or "")
            if not key:
                problems.append("%s: work lacks citation_key" % name)
            elif known_keys and key not in known_keys:
                problems.append("%s: unknown citation_key %r" % (name, key))
            if len(str(work.get("summary") or "")) < 8:
                problems.append("%s/%s: summary too short" % (name, key or "?"))
            if len(str(work.get("limitation") or "")) < 8:
                problems.append("%s/%s: limitation too short" % (name, key or "?"))
            if key and key not in used_keys:
                warnings.append("%s/%s: planned work is not cited in the sections"
                                % (name, key))
        if len(str(group.get("synthesis") or "")) < 10:
            problems.append("%s: synthesis is missing or too short" % name)
        cited_in_related = {w.get("citation_key") for w in works
                            if isinstance(w, dict) and w.get("citation_key")
                            and w.get("citation_key") in keys_in(related)}
        if not cited_in_related:
            warnings.append("%s: no group work is cited in related-work.md" % name)

    gap = plan.get("gap") or {}
    if not isinstance(gap, dict):
        gap = {}
        problems.append("gap is not an object")
    if len(str(gap.get("statement") or "")) < 15:
        problems.append("gap.statement is missing or too short")
    derived = gap.get("derived_from") or []
    if not isinstance(derived, list) or len(derived) < 2:
        problems.append("gap.derived_from must contain at least 2 keys")
        derived = derived if isinstance(derived, list) else []
    for key in derived:
        key = str(key)
        if known_keys and key not in known_keys:
            problems.append("gap.derived_from unknown key %r" % key)
        if key not in used_keys:
            problems.append("gap.derived_from key %r is not cited in the sections" % key)

    contributions = plan.get("contributions") or []
    if not isinstance(contributions, list) or not 1 <= len(contributions) <= 4:
        problems.append("contributions must contain 1-4 items")
        contributions = contributions if isinstance(contributions, list) else []
    for index, contribution in enumerate(contributions, 1):
        if not isinstance(contribution, dict):
            problems.append("contribution %d is not an object" % index)
            continue
        cid = str(contribution.get("id") or "C%d" % index)
        if len(str(contribution.get("statement") or "")) < 10:
            problems.append("%s: statement too short" % cid)
        linked = contribution.get("linked_sections") or []
        evid = contribution.get("evidence") or []
        if not linked and not evid:
            problems.append("%s: needs linked_sections or evidence" % cid)
        for item in evid:
            if isinstance(item, dict):
                quote = str(item.get("quote") or "")
                path = str(item.get("path") or "")
                source = sources.find(path)
                if not source:
                    problems.append("%s: evidence path not found: %s" % (cid, path))
                elif quote and not quote_found(source, quote)[0]:
                    problems.append("%s: evidence quote not verbatim" % cid)
            elif isinstance(item, str) and ":" in item:
                path, quote = item.split(":", 1)
                source = sources.find(path.strip())
                if source and not quote_found(source, quote.strip())[0]:
                    warnings.append("%s: evidence pointer quote not found verbatim" % cid)

    claim_rows = plan.get("claim_evidence")
    if claim_rows is None:
        claim_rows = []
    if not isinstance(claim_rows, list):
        problems.append("claim_evidence is not a list")
        claim_rows = []
    if (used_keys or contributions) and not claim_rows:
        problems.append("claim_evidence is empty while citations/contributions exist")
    claim_ids = set()
    claim_cited_keys = set()
    for index, row in enumerate(claim_rows, 1):
        if not isinstance(row, dict):
            problems.append("claim_evidence[%d] is not an object" % index)
            continue
        cid = str(row.get("claim_id") or "")
        if not cid:
            problems.append("claim_evidence[%d] lacks claim_id" % index)
        elif cid in claim_ids:
            problems.append("duplicate claim_id %s" % cid)
        claim_ids.add(cid)
        if len(str(row.get("claim") or "")) < 5:
            problems.append("%s: claim too short" % (cid or index))
        if row.get("section") not in ("Introduction", "Related Work"):
            problems.append("%s: invalid section %r" % (cid or index, row.get("section")))
        if not str(row.get("claim_locator") or "").strip():
            problems.append("%s: missing claim_locator" % (cid or index))
        for key in row.get("citation_keys") or []:
            key = str(key)
            claim_cited_keys.add(key)
            if known_keys and key not in known_keys:
                problems.append("%s: unknown citation key %r" % (cid or index, key))
        items = row.get("evidence") or []
        if not isinstance(items, list) or not items:
            problems.append("%s: no evidence" % (cid or index))
            items = []
        for item in items:
            if not isinstance(item, dict):
                problems.append("%s: evidence entry is not an object" % (cid or index))
                continue
            path = str(item.get("path") or "")
            quote = str(item.get("quote") or "")
            source = sources.find(path)
            if not source:
                problems.append("%s: evidence path not found: %s" % (cid or index, path))
            elif not quote:
                problems.append("%s: empty evidence quote" % (cid or index))
            elif not quote_found(source, quote)[0]:
                problems.append("%s: evidence quote not verbatim in %s" % (cid or index, path))
    if used_keys and claim_rows:
        missing_claims = sorted(k for k in used_keys if k not in claim_cited_keys)
        if missing_claims:
            warnings.append("used keys not linked from claim_evidence.citation_keys: %s"
                            % ", ".join(missing_claims))

    verification = plan.get("citation_verification")
    if verification is None:
        verification = []
    if not isinstance(verification, list):
        problems.append("citation_verification is not a list")
        verification = []
    verified_keys = {}
    for index, row in enumerate(verification, 1):
        if not isinstance(row, dict):
            problems.append("citation_verification[%d] is not an object" % index)
            continue
        key = str(row.get("key") or "")
        if not key:
            problems.append("citation_verification[%d] lacks key" % index)
        if key in verified_keys:
            warnings.append("citation key %s verified twice" % key)
        verified_keys[key] = row
        if row.get("used_in") not in ("Introduction", "Related Work"):
            problems.append("%s: invalid used_in %r" % (key or index, row.get("used_in")))
        meta = str(row.get("metadata_status") or "")
        if meta not in ("verified", "incomplete", "missing"):
            problems.append("%s: invalid metadata_status %r" % (key or index, meta))
        support = str(row.get("support_status") or "")
        if support not in ("full", "partial", "none", "unverifiable"):
            problems.append("%s: invalid support_status %r" % (key or index, support))
        if meta == "verified" and not (row.get("title") and row.get("year")
                                       and row.get("authors")):
            problems.append("%s: metadata_status=verified but title/year/authors incomplete"
                            % (key or index))
        quote = str(row.get("source_quote") or "")
        path = str(row.get("source_path") or "")
        if quote and path:
            source = sources.find(path)
            if not source:
                problems.append("%s: source_path not found: %s" % (key or index, path))
            elif not quote_found(source, quote)[0]:
                warnings.append("%s: source_quote not verbatim in %s" % (key or index, path))
    missing_verification = sorted(k for k in used_keys if k not in verified_keys)
    if used_keys and missing_verification:
        problems.append("citation_verification missing used keys: %s"
                        % ", ".join(missing_verification))

    lowered = text.lower()
    for phrase in EMPTY_PHRASES:
        if phrase in lowered:
            if phrase not in source_text.lower():
                warnings.append("possible unsupported stock phrase: %r" % phrase)

    intro_paras = [p for p in re.split(r"\n\s*\n", intro) if p.strip()]
    related_paras = [p for p in re.split(r"\n\s*\n", related) if p.strip()]
    if len(intro_paras) < 2:
        warnings.append("introduction has fewer than 2 paragraphs")
    if len(related_paras) < 2:
        warnings.append("related work has fewer than 2 paragraphs")
    if related and related.count("\n- ") + related.count("\n* ") > len(related_paras):
        warnings.append("related work looks like a bullet list; use comparative prose")

    contract = case.get("output_contract")
    if isinstance(contract, dict):
        canonical_or_alias = {
            "case_id", "status", "result", "claims", "uncertainties",
            "deliverable", "preserved_facts", "evidence_map", "change_log",
            "artifacts", "section_text", "citation_map",
            "目标产物", "事实与来源映射", "版本与边界", "目标产物路径",
        }
        for key in contract:
            candidates = [output_dir / "results" / (key + ".json"),
                          output_dir / "results" / key,
                          output_dir / (key + ".json"),
                          output_dir / key]
            if any(p.exists() for p in candidates):
                continue
            if key in plan or key in canonical_or_alias:
                continue
            problems.append("output_contract key %r has no plan field or "
                            "output/results/%s.json" % (key, key))

    coverage = {
        "intro_chars": len(intro.strip()),
        "related_chars": len(related.strip()),
        "used_keys": sorted(used_keys),
        "known_keys": sorted(known_keys),
        "unknown_keys": unknown,
        "groups": len(groups),
        "contributions": len(contributions),
        "claim_evidence_rows": len(claim_rows),
        "citation_verification_rows": len(verification),
        "invented_numbers": invented,
    }
    return {
        "case_id": case_id or str(plan.get("case_id") or "CASE"),
        "status": "pass" if not problems else "fail",
        "problems": problems,
        "warnings": warnings,
        "coverage": coverage,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="introduction writing pre-finalize gate")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--apply", action="store_true",
                        help="downgrade an existing result.json to partial when problems remain")
    args = parser.parse_args(argv)
    output = Path(args.output)
    try:
        payload = evaluate(args.input, args.work, args.output)
    except Exception as exc:  # noqa: BLE001
        payload = {
            "case_id": "CASE",
            "status": "error",
            "problems": ["gate crashed: %s" % exc],
            "warnings": [],
            "traceback": traceback.format_exc()[-4000:],
            "coverage": {},
        }
    payload["generated_at"] = now_iso()
    write_json(output / "self-check.json", payload)
    applied = None
    if args.apply and payload.get("problems"):
        result_path = output / "result.json"
        existing = load_json(result_path, None)
        if isinstance(existing, dict):
            status = existing.get("status")
            if status not in ("input_error", "partial"):
                existing["status"] = "partial"
                existing["task_status"] = "partial"
                uncertainties = existing.get("uncertainties")
                if not isinstance(uncertainties, list):
                    uncertainties = []
                message = "pre-finalize gate problems remain: %s" % "; ".join(
                    str(x) for x in (payload.get("problems") or [])[:8])
                if not any(isinstance(item, dict) and item.get("item") == "self_check"
                           for item in uncertainties):
                    uncertainties.append({
                        "item": "self_check",
                        "reason": message,
                        "impact": "the delivered result was downgraded to partial",
                        "action": "fix the work file/report and rerun finalize",
                    })
                existing["uncertainties"] = uncertainties
                existing["uncertainty"] = uncertainties
                write_json(result_path, existing)
                applied = {"status": "partial", "result": str(result_path)}
    print(json.dumps({
        "status": payload.get("status"),
        "problems": len(payload.get("problems") or []),
        "warnings": len(payload.get("warnings") or []),
        "coverage": payload.get("coverage"),
        "self_check": str(output / "self-check.json"),
        "applied": applied,
    }, ensure_ascii=False, indent=2))
    for problem in (payload.get("problems") or [])[:30]:
        print("PROBLEM: %s" % problem, file=sys.stderr)
    return 1 if payload.get("status") == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
