#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract concepts, evidence and relations from task materials.

Reads ``/input/case.json``, ``/input/task.md`` and every readable file under
``/input`` (materials/, sources/, docs/ or case-declared paths).  Produces a
material ledger with candidate nodes, cross-links, preserved facts and an
evidence map.  Every reported node keeps path + locator + verbatim quote; no
concept outside the material is invented.

Usage:
    python3 extract_material.py --input /input --work /app/work --json
    python3 extract_material.py --input /input --out /app/work/material.json --json

Stdlib only.  Exit 0 = ledger written (possibly with warnings), 2 = bad input.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path


def _load_kit():
    try:
        import mindmap_kit  # type: ignore
        return mindmap_kit
    except ImportError:
        path = Path(__file__).resolve().with_name("mindmap_kit.py")
        if not path.is_file():
            path = path.with_name("mindmap_kit.py.md")
        spec = importlib.util.spec_from_loader(
            "mindmap_kit", SourceFileLoader("mindmap_kit", str(path)))
        module = importlib.util.module_from_spec(spec)
        sys.modules["mindmap_kit"] = module
        spec.loader.exec_module(module)
        return module


kit = _load_kit()

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|℃|°C|mg|mL|L|h|min|s|K|Pa|MPa|nm|eV|mA|倍)")
METRIC_RE = re.compile(
    r"([一-鿿A-Za-z][一-鿿A-Za-z /-]{1,14}?)\s*"
    r"(?:为|达到|约为|是|=|:|：)?\s*"
    r"(\d+(?:\.\d+)?)\s*(%|℃|°C|mg|mL|L|h|min|s|K|Pa|MPa|nm|eV|mA)(?![A-Za-z])")


def branch_cue_score(text: str, key: str) -> int:
    spec = kit.BRANCH_BY_KEY.get(key)
    if not spec:
        return 0
    low = text.lower()
    return sum(low.count(cue) for cue in spec[3] if cue)


def title_from_task(task_text: str) -> str:
    for line in str(task_text or "").splitlines():
        match = re.match(r"^\s*#{1,3}\s+(.+)$", line)
        if match:
            title = kit.clean_inline(match.group(1))
            if 2 <= len(title) <= 60:
                return title
    for line in str(task_text or "").splitlines():
        line = kit.clean_inline(line)
        if 4 <= len(line) <= 50:
            return line
    return ""


def case_title(case: dict) -> str:
    for key in ("title", "name", "topic", "subject"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            return kit.clean_inline(value)
    source = case.get("source")
    if isinstance(source, dict):
        for key in ("title", "name"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return kit.clean_inline(value)
    return ""


def collect_sentences(sources):
    """Return (candidates, all_sentences, sections, warnings)."""
    candidates, all_sentences, section_rows = [], [], []
    warnings = []
    index = 0
    for source in sources:
        if source.get("status") != "read" or not source.get("text"):
            continue
        sections = kit.split_sections(source["text"], source["path"])
        if len(sections) == 1 and sections[0]["heading"] == "正文":
            warnings.append("%s: no explicit headings; branches come from cues"
                            % source["path"])
        for section in sections:
            heading_branch = kit.branch_for_heading(section["heading"])
            section_branch = heading_branch or kit.branch_for_text(section["text"])
            section_rows.append({"heading": section["heading"],
                                 "level": section["level"],
                                 "path": source["path"],
                                 "locator": section["locator"],
                                 "chars": len(section["text"]),
                                 "branch": section_branch})
            for sentence in kit.split_sentences(section["text"]):
                index += 1
                # A matched heading governs its section; otherwise classify
                # each sentence so heading-less notes still form 3-7 branches.
                key = heading_branch or kit.branch_for_text(sentence) or "context"
                score = branch_cue_score(sentence, key) * 2
                if NUMBER_RE.search(sentence):
                    score += 3
                if 20 <= len(sentence) <= 240:
                    score += 1
                if len(sentence) > 420:
                    score -= 2
                candidate = {
                    "branch": key,
                    "text": sentence,
                    "path": source["path"],
                    "locator": section["locator"],
                    "heading": section["heading"],
                    "heading_matched": bool(kit.branch_for_heading(
                        section["heading"])),
                    "score": score,
                    "order": index,
                    "has_number": bool(NUMBER_RE.search(sentence)),
                }
                candidates.append(candidate)
                all_sentences.append({"text": sentence,
                                      "path": source["path"],
                                      "locator": section["locator"]})
    return candidates, all_sentences, section_rows, warnings


def select_candidates(candidates, max_nodes, max_branches=kit.MAX_BRANCHES):
    """Pick candidates per branch within the node/branch budget."""
    by_branch = {}
    for candidate in candidates:
        by_branch.setdefault(candidate["branch"], []).append(candidate)
    for key in by_branch:
        by_branch[key].sort(key=lambda item: (-item["score"], item["order"]))
    order = [spec[0] for spec in kit.BRANCH_SPECS]
    active = [key for key in order if by_branch.get(key)]
    max_branches = max(2, min(kit.MAX_BRANCHES, int(max_branches or 0) or
                              kit.MAX_BRANCHES))
    if len(active) > max_branches:
        active = sorted(active, key=lambda k: -len(by_branch[k]))[:max_branches]
        active = [key for key in order if key in active]
    if not active and candidates:
        active = [candidates[0]["branch"]]
    budget = max(1, max_nodes - 1 - len(active))
    selected = []
    # One core sentence per branch first, then fill by global score.
    for key in active:
        if budget <= 0:
            break
        selected.append(by_branch[key].pop(0))
        budget -= 1
    rest = [item for key in active for item in by_branch[key]]
    rest.sort(key=lambda item: (-item["score"], item["order"]))
    selected.extend(rest[:budget])
    branch_rank = {key: pos for pos, key in enumerate(active)}
    selected.sort(key=lambda item: (branch_rank.get(item["branch"], 99), item["order"]))
    return active, selected


def dedupe_candidates(selected):
    seen, output = set(), []
    for candidate in selected:
        focus = kit.BRANCH_BY_KEY.get(candidate["branch"], ("", "", "", ()))[3]
        label = kit.compress_label(candidate["text"], focus, 20)
        norm = kit.normalize_for_match(label)
        if len(norm) < 2 or norm in seen:
            continue
        seen.add(norm)
        candidate = dict(candidate)
        candidate["label"] = label
        output.append(candidate)
    return output


def build_nodes(selected, branch_sources, title):
    nodes = []
    root = {
        "node_id": "n0", "parent": None,
        "label": kit.clean_inline(title)[:30] or "中心主题",
        "type": "topic", "detail": "思维导图中心主题",
        "source": branch_sources.get("root") or "task",
        "kind": "task", "importance": "core",
    }
    nodes.append(root)
    branch_ids = {}
    active_keys = []
    for candidate in selected:
        if candidate["branch"] not in active_keys:
            active_keys.append(candidate["branch"])
    for pos, key in enumerate(active_keys, start=1):
        meta = branch_sources.get(key) or {}
        source = meta.get("source") or "task"
        kind = "reported" if meta.get("source") and not kit.source_is_task(source) else "task"
        label = meta.get("label") or kit.branch_label(key)
        branch_ids[key] = "b%d" % pos
        nodes.append({
            "node_id": branch_ids[key], "parent": "n0",
            "label": kit.clean_inline(label)[:18],
            "type": kit.branch_type(key),
            "detail": "组织性分支：%s" % kit.branch_label(key),
            "source": source, "kind": kind, "importance": "supporting",
            "structural": True,
        })
    leaf_index = 0
    for candidate in selected:
        leaf_index += 1
        node_type = kit.branch_type(candidate["branch"])
        if "?" in candidate["text"] or "？" in candidate["text"]:
            node_type = "question"
        if candidate["has_number"] and candidate["branch"] in ("result", "method"):
            node_type = "result" if candidate["branch"] == "result" else "method"
        detail = candidate["text"][:240]
        nodes.append({
            "node_id": "n%d" % leaf_index,
            "parent": branch_ids[candidate["branch"]],
            "label": candidate["label"],
            "type": node_type,
            "detail": detail,
            "source": {"path": candidate["path"],
                       "locator": candidate["locator"],
                       "quote": candidate["text"][:240]},
            "kind": "reported",
            "importance": "core" if candidate["has_number"] or candidate["score"] >= 6
                          else "supporting",
        })
    return nodes


def _owner_for(sentence, leaves):
    """Node whose own quote is this sentence (the evidence owner)."""
    target = kit.normalize_for_match(sentence)
    for node in leaves:
        quote = str((node.get("source") or {}).get("quote") or "")
        norm = kit.normalize_for_match(quote)
        if norm and (norm == target or target in norm or norm in target):
            return node
    return None


def build_links(nodes, sentences, warnings):
    """Only link an evidence-owner sentence to a node it actually mentions.

    Relation words alone are not enough: the sentence must belong to one node
    (its verbatim quote) and share a >=4-char term with the other node.  When
    the other node's full label is not present, the edge is marked inferred.
    """
    leaves = [node for node in nodes
              if node.get("kind") == "reported" and node.get("parent")]
    type_rank = {"result": 9, "evidence": 8, "method": 7, "claim": 6,
                 "question": 5, "risk": 4, "gap": 3, "concept": 2,
                 "task": 1, "topic": 0}
    positive = {"supports", "explains", "depends", "cites", "part_of"}
    links, seen = [], set()
    for sentence in sentences:
        relation = kit.relation_for_text(sentence["text"])
        if not relation:
            continue
        owner = _owner_for(sentence["text"], leaves)
        if owner is None:
            continue
        mentioned = []
        for node in leaves:
            if node["node_id"] == owner["node_id"]:
                continue
            label = str(node.get("label") or "")
            if (len(kit.normalize_for_match(label)) >= 4
                    and kit.shares_term(sentence["text"], label, 4)):
                mentioned.append(node)
        mentioned.sort(key=lambda node: -type_rank.get(str(node.get("type")), 0))
        if relation in positive:
            mentioned = [node for node in mentioned
                         if node.get("type") in ("claim", "evidence", "result",
                                                 "question", "method", "risk", "gap")]
        mentioned = [node for node in mentioned
                     if node.get("parent") != owner.get("parent")]
        if not mentioned:
            continue
        other = mentioned[0]
        if relation in ("causes", "precedes", "contrasts", "conflicts"):
            source_node, target_node = other, owner
        else:
            source_node, target_node = owner, other
        key = (source_node["node_id"], target_node["node_id"], relation)
        if key in seen:
            continue
        seen.add(key)
        label_norm = kit.normalize_for_match(other.get("label"))
        sentence_norm = kit.normalize_for_match(sentence["text"])
        kind = "reported" if label_norm and label_norm in sentence_norm else "inferred"
        links.append({
            "from": source_node["node_id"], "to": target_node["node_id"],
            "relation": relation, "kind": kind,
            "source": {"path": sentence["path"], "locator": sentence["locator"],
                       "quote": sentence["text"][:240]},
        })
        if len(links) >= 6:
            break
    if not links:
        warnings.append("no explicit cross-branch relation found; "
                        "hierarchy edges only (not fabricated)")
    return links


def collect_preserved_facts(nodes, sentences):
    """Numbers first (all source sentences), then core non-structural nodes."""
    facts, seen = [], set()
    for sentence in sentences:
        if not NUMBER_RE.search(sentence["text"]):
            continue
        key = kit.normalize_for_match(sentence["text"])
        if not key or key in seen:
            continue
        seen.add(key)
        number = NUMBER_RE.search(sentence["text"])
        facts.append({
            "fact_id": "f-num-%d" % (len(facts) + 1),
            "text": sentence["text"][:240],
            "value": number.group(0).strip() if number else "",
            "source": {"path": sentence["path"],
                       "locator": sentence["locator"],
                       "quote": sentence["text"][:240]},
            "node_id": None,
        })
        if len(facts) >= 12:
            break
    for node in nodes:
        if (node.get("node_id") == "n0" or node.get("kind") != "reported"
                or node.get("structural")):
            continue
        if node.get("importance") != "core" and not NUMBER_RE.search(
                str(node.get("detail") or "")):
            continue
        source = node.get("source") or {}
        fact = {"fact_id": "f-" + node["node_id"],
                "text": str(node.get("detail") or node.get("label") or "")[:240],
                "source": {"path": source.get("path", ""),
                           "locator": source.get("locator", ""),
                           "quote": source.get("quote", "")},
                "node_id": node["node_id"]}
        number = NUMBER_RE.search(fact["text"])
        if number:
            fact["value"] = number.group(0).strip()
        facts.append(fact)
        if len(facts) >= 20:
            break
    return facts


def collect_evidence(nodes):
    entries = []
    for node in nodes:
        if node.get("kind") != "reported" or node.get("structural"):
            continue
        source = node.get("source") or {}
        if not source.get("path"):
            continue
        entries.append({
            "claim_id": node["node_id"],
            "claim": node.get("label", ""),
            "node_type": node.get("type", "claim"),
            "kind": node.get("kind", "reported"),
            "source": {"path": source.get("path", ""),
                       "locator": source.get("locator", ""),
                       "quote": source.get("quote", "")},
        })
        if len(entries) >= 40:
            break
    return entries


def detect_number_conflicts(sentences, uncertainty):
    buckets = {}
    for sentence in sentences:
        for match in METRIC_RE.finditer(sentence["text"]):
            metric = kit.normalize_for_match(match.group(1))
            metric = re.sub(r"^(在|于|当|对|与|和|为)", "", metric)
            if len(metric) < 2:
                continue
            key = (metric, match.group(3))
            buckets.setdefault(key, set()).add(match.group(2))
    reported = 0
    for (metric, unit), values in buckets.items():
        if len(values) > 1 and reported < 3:
            uncertainty.append({
                "item": "%s（%s）" % (metric, unit),
                "reason": "material reports multiple values: %s"
                          % ", ".join(sorted(values)),
                "impact": "keep both values and mark for human confirmation",
            })
            reported += 1
    return reported


def make_branch_sources(selected):
    """Branch node source: real heading when one matched, else task."""
    meta = {}
    for candidate in selected:
        key = candidate["branch"]
        entry = meta.setdefault(key, {"count": 0})
        entry["count"] += 1
        if candidate.get("heading_matched") and candidate.get("heading"):
            if not entry.get("label"):
                entry["label"] = candidate["heading"]
                entry["source"] = {"path": candidate["path"],
                                   "locator": candidate["locator"]}
    return meta


def main():
    kit.configure_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--work", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.is_dir():
        print(json.dumps({"ok": False, "error": "input dir not found: %s" % input_dir},
                         ensure_ascii=False))
        return 2
    work_dir = Path(args.work) if args.work else input_dir.parent / "work"
    out_path = Path(args.out) if args.out else work_dir / "material.json"

    case, case_error = {}, ""
    case_path = input_dir / "case.json"
    if case_path.is_file():
        try:
            case = kit.load_json(case_path)
        except (OSError, json.JSONDecodeError) as exc:
            case_error = "cannot parse case.json: %s" % exc
    task_text = kit.read_text(input_dir / "task.md")
    constraints = kit.parse_constraints(task_text + "\n"
                                           + str(case.get("task_instruction") or ""))
    sources = kit.discover_input_files(input_dir)
    readable = [source for source in sources if source.get("status") == "read"]

    warnings, uncertainty = [], []
    if case_error:
        warnings.append(case_error)
        uncertainty.append({"item": "case.json", "reason": case_error,
                            "impact": "case_id may be missing"})
    title = (case_title(case) or title_from_task(task_text)
             or (readable[0]["path"] if readable else "") or "思维导图")
    for source in sources:
        if source.get("notes"):
            warnings.append("%s: %s" % (source["path"], "; ".join(source["notes"])))
    for source in sources:
        if source.get("status") == "unsupported":
            uncertainty.append({
                "item": source["path"],
                "reason": "unsupported or binary format; content not read",
                "impact": "possible missing concepts; convert to md/txt and rerun",
            })
        elif source.get("status") == "ocr_needed":
            uncertainty.append({
                "item": source["path"],
                "reason": "PDF has no usable text layer",
                "impact": "OCR is required before the map can cover this source",
            })
        elif source.get("status") == "empty":
            uncertainty.append({"item": source["path"], "reason": "file is empty",
                                "impact": "no nodes extracted from it"})

    if not readable:
        uncertainty.append({
            "item": "materials",
            "reason": "no readable text file found under the input directory",
            "impact": "deliverable will be a partial map with the task root only",
        })

    candidates, all_sentences, section_rows, extraction_warnings = \
        collect_sentences(readable)
    warnings.extend(extraction_warnings)
    branch_high = (constraints.get("branch_range") or
                   (kit.MIN_BRANCHES, kit.MAX_BRANCHES))[1]
    active, selected = select_candidates(candidates, constraints["max_nodes"],
                                         branch_high)
    selected = dedupe_candidates(selected)
    if constraints.get("branch_range"):
        low, high = constraints["branch_range"]
        if not (low <= len(set(item["branch"] for item in selected)) <= high):
            warnings.append("branch count %d outside task range %d-%d"
                            % (len(set(item["branch"] for item in selected)), low, high))
    branch_sources = make_branch_sources(selected)
    root_source = "task"
    if not task_text and not case_title(case):
        root_source = {"path": readable[0]["path"], "locator": "title"} if readable else "task"
    branch_sources["root"] = root_source
    nodes = build_nodes(selected, branch_sources, title)
    links = build_links(nodes, all_sentences, warnings)
    facts = collect_preserved_facts(nodes, all_sentences)
    evidence = collect_evidence(nodes)
    conflicts = detect_number_conflicts(all_sentences, uncertainty)
    if conflicts:
        warnings.append("%d metric(s) have conflicting values; see uncertainty" % conflicts)
    if len(section_rows) <= 1:
        uncertainty.append({
            "item": "structure", "reason": "material has no explicit section headings",
            "impact": "branches are semantic groupings; review before delivery",
        })

    sections_covered, sections_not_covered = [], []
    used_headings = set()
    for node in nodes:
        if node.get("node_id") == "n0" or node.get("kind") != "reported":
            continue
        source = node.get("source") or {}
        used_headings.add((source.get("path", ""), source.get("locator", "")))
    for row in section_rows:
        label = "%s (%s)" % (row["heading"], row["path"])
        if (row["path"], row["locator"]) in used_headings:
            if label not in sections_covered:
                sections_covered.append(label)
        elif len(sections_not_covered) < 20:
            sections_not_covered.append(label)

    case_id = str(case.get("case_id") or case.get("id") or "").strip()
    if not case_id:
        case_id = "unknown-case"
        warnings.append("case_id missing; result will use unknown-case")
    material = {
        "case_id": case_id,
        "title": title,
        "task_instruction": str(case.get("task_instruction") or task_text[:400]),
        "constraints": constraints,
        "output_contract": case.get("output_contract") or {
            "case_id": "原样保留",
            "deliverable": "可编辑的目标文稿/结构化产物",
            "preserved_facts": "数字、条件、术语和来源不可凭空改变",
            "evidence_map": "关键论断到来源定位的映射",
            "change_log": "修改动作、原因、人工确认项和未解决问题",
        },
        "sources": [{key: source.get(key) for key in
                     ("path", "suffix", "status", "chars", "sha256", "notes")}
                    for source in sources],
        "sections": section_rows,
        "nodes": nodes,
        "cross_links": links,
        "preserved_facts": facts,
        "evidence_map": evidence,
        "coverage": {"sections_covered": sections_covered,
                     "not_covered": sections_not_covered,
                     "files_read": len(readable),
                     "files_total": len(sources)},
        "stats": {
            "readable_files": len(readable),
            "chars": sum(source.get("chars", 0) for source in sources),
            "sections": len(section_rows),
            "candidates": len(candidates),
            "nodes": len(nodes),
            "branches": len(set(item["branch"] for item in selected)),
            "cross_links": len(links),
            "numeric_facts": sum(1 for fact in facts if fact.get("value")),
        },
        "warnings": warnings,
        "uncertainty": uncertainty,
        "degraded": not readable,
    }
    kit.dump_json(out_path, material)
    summary = {"ok": True, "material": str(out_path),
               "case_id": case_id, "nodes": len(nodes),
               "branches": len(set(item["branch"] for item in selected)),
               "cross_links": len(links),
               "preserved_facts": len(facts), "evidence_map": len(evidence),
               "degraded": not readable, "warnings": warnings[:8],
               "uncertainty": len(uncertainty)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
