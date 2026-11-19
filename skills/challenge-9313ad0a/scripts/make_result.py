#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write the official result.json for a mind-map case.

Projects the outline/material/check report into the four contract fields
(``deliverable``, ``preserved_facts``, ``evidence_map``, ``change_log``) while
keeping the richer ``mindmap`` / ``nodes`` / ``edges`` / ``cross_links`` /
``coverage`` / ``uncertainty`` view used by the checker.

Usage:
    python3 make_result.py --input /input --work /app/work --output /app/output --json

All artifact paths are checked on disk.  A missing SVG degrades to the editable
JSON structure with status=partial instead of pretending a file exists.

Stdlib only. Exit 0 = result written (possibly partial), 2 = no outline/input.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
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


def load_optional(path):
    try:
        return kit.load_json(path)
    except (OSError, json.JSONDecodeError):
        return None


def copy_json_if_needed(source, target):
    if not source or not Path(source).is_file():
        return None
    data = load_optional(source)
    if data is None:
        return None
    kit.dump_json(target, data)
    return Path(target)


def fallback_outline(case, material, title):
    case_id = str((case or {}).get("case_id") or (material or {}).get("case_id")
                  or "unknown-case")
    root_label = title or "思维导图"
    return {
        "case_id": case_id,
        "title": root_label,
        "structure_type": "left_right_tree",
        "layout": "tree",
        "nodes": [{
            "node_id": "n0", "parent": None, "label": root_label[:30],
            "type": "topic", "detail": "未找到大纲文件，保留任务根节点",
            "source": "task", "kind": "task", "importance": "core",
        }],
        "cross_links": [],
        "coverage": {"sections_covered": [], "not_covered": []},
        "degraded_outline": True,
    }


def find_root(outline):
    for node in outline.get("nodes") or []:
        if not node.get("parent"):
            return node
    return (outline.get("nodes") or [{}])[0]


def make_edges(outline):
    nodes = outline.get("nodes") or []
    by_id = {str(node.get("node_id")): node for node in nodes}
    edges = []
    for node in nodes:
        parent = str(node.get("parent") or "")
        if parent and parent in by_id:
            source = kit.source_obj(node.get("source"))
            edges.append({
                "edge_id": "e-%s" % node.get("node_id"),
                "from": parent, "to": node.get("node_id"),
                "relation": "has_child", "kind": "hierarchy",
                "source": {"path": source.get("path", ""),
                           "locator": source.get("locator", ""),
                           "quote": source.get("quote", "")},
            })
    for index, link in enumerate(outline.get("cross_links") or []):
        source = kit.source_obj(link.get("source"))
        edges.append({
            "edge_id": "x-%d" % (index + 1),
            "from": link.get("from"), "to": link.get("to"),
            "relation": link.get("relation") or "link",
            "kind": "cross", "confidence": link.get("kind") or "reported",
            "source": {"path": source.get("path", ""),
                       "locator": source.get("locator", ""),
                       "quote": source.get("quote", "")},
        })
    return edges


def fallback_facts(outline):
    facts = []
    for node in outline.get("nodes") or []:
        if node.get("node_id") == "n0" or node.get("kind") != "reported":
            continue
        source = kit.source_obj(node.get("source"))
        if not source.get("path"):
            continue
        facts.append({
            "fact_id": "f-" + str(node.get("node_id")),
            "text": str(node.get("detail") or node.get("label") or "")[:240],
            "source": source, "node_id": node.get("node_id"),
        })
        if len(facts) >= 20:
            break
    return facts


def fallback_evidence(outline):
    entries = []
    for node in outline.get("nodes") or []:
        if node.get("kind") != "reported":
            continue
        source = kit.source_obj(node.get("source"))
        if not source.get("path"):
            continue
        entries.append({
            "claim_id": node.get("node_id"),
            "claim": node.get("label", ""),
            "node_type": node.get("type", "claim"),
            "kind": node.get("kind", "reported"),
            "source": source,
        })
        if len(entries) >= 40:
            break
    return entries


def branch_labels(outline):
    nodes = outline.get("nodes") or []
    root = find_root(outline)
    root_id = str(root.get("node_id") or "")
    labels = [str(node.get("label") or node.get("node_id"))
              for node in nodes if str(node.get("parent") or "") == root_id]
    return labels[:10]


def main():
    kit.configure_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--work", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--outline", default=None)
    parser.add_argument("--spec", default=None)
    parser.add_argument("--svg", default=None)
    parser.add_argument("--check", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    input_dir = Path(args.input)
    work_dir = Path(args.work) if args.work else input_dir.parent / "work"
    output_dir = Path(args.output) if args.output else input_dir.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else output_dir / "result.json"

    case = load_optional(input_dir / "case.json") or {}
    material = load_optional(work_dir / "material.json")
    outline_source = args.outline or work_dir / "mindmap.json"
    outline = load_optional(outline_source)
    if outline is None:
        title = str(case.get("title") or (material or {}).get("title") or "思维导图")
        outline = fallback_outline(case, material, kit.clean_inline(title))
        kit.dump_json(work_dir / "mindmap.json", outline)
        outline_source = work_dir / "mindmap.json"

    spec_source = args.spec or work_dir / "mindmap-spec.json"
    svg_source = Path(args.svg) if args.svg else output_dir / "mindmap.svg"
    check_report = load_optional(args.check or work_dir / "check-report.json") or {}

    structure = copy_json_if_needed(outline_source, output_dir / "mindmap.json")
    spec_copy = copy_json_if_needed(spec_source, output_dir / "mindmap-spec.json")
    if not svg_source.is_file() and (output_dir / "mindmap.svg").is_file():
        svg_source = output_dir / "mindmap.svg"
    if svg_source.is_file():
        target_svg = output_dir / "mindmap.svg"
        try:
            same = svg_source.resolve() == target_svg.resolve()
        except OSError:
            same = False
        if not same:
            shutil.copy2(svg_source, target_svg)
        svg_source = target_svg

    nodes = outline.get("nodes") or []
    edges = make_edges(outline)
    case_id = str(case.get("case_id") or (material or {}).get("case_id")
                  or outline.get("case_id") or "unknown-case")
    root = find_root(outline)
    depth = kit.infer_depth(nodes)

    artifacts = []
    if structure is not None:
        artifacts.append({
            "role": "structure", "path": structure.name, "format": "json",
            "nodes": len(nodes), "edges": len(edges),
            "bytes": kit.file_size(structure), "editable": True,
        })
    if spec_copy is not None:
        artifacts.append({
            "role": "spec", "path": spec_copy.name, "format": "json",
            "nodes": len(nodes), "edges": len(edges),
            "bytes": kit.file_size(spec_copy),
        })
    if svg_source.is_file():
        artifacts.append({
            "role": "editable", "path": svg_source.name, "format": "svg",
            "nodes": len(nodes), "edges": len(edges),
            "bytes": kit.file_size(svg_source), "editable": True,
            "text_based": True,
        })
        deliverable = {"path": svg_source.name, "format": "svg",
                       "role": "editable", "nodes": len(nodes),
                       "edges": len(edges), "editable": True}
    else:
        artifacts.append({
            "role": "editable", "path": "mindmap.json", "format": "json",
            "nodes": len(nodes), "edges": len(edges),
            "bytes": kit.file_size(output_dir / "mindmap.json"),
            "editable": True, "fallback": True,
        })
        deliverable = {"path": "mindmap.json", "format": "json",
                       "role": "editable", "nodes": len(nodes),
                       "edges": len(edges), "editable": True}

    material = material or {}
    facts = material.get("preserved_facts") or fallback_facts(outline)
    evidence = material.get("evidence_map") or fallback_evidence(outline)
    coverage = material.get("coverage") or {
        "sections_covered": [], "not_covered": [],
        "files_read": 0, "files_total": 0,
    }
    uncertainty = list(material.get("uncertainty") or [])
    problems = list(check_report.get("problems") or [])
    warnings = list(check_report.get("warnings") or [])
    if problems:
        uncertainty.append({
            "item": "structure check",
            "reason": "%d problem(s) reported by check_mindmap.py" % len(problems),
            "impact": "status=partial; inspect check-report.json",
        })
    if not check_report:
        uncertainty.append({
            "item": "verification",
            "reason": "no check report was available before writing result.json",
            "impact": "status=partial until check_mindmap.py passes",
        })
    if not svg_source.is_file():
        uncertainty.append({
            "item": "editable SVG",
            "reason": "SVG not found; the editable JSON outline is the deliverable",
            "impact": "downstream renderer may be required",
        })
    uncertainty.append({
        "item": "preview PNG",
        "reason": "no raster renderer is bundled (stdlib-only skill)",
        "impact": "SVG itself opens in browsers/vector editors; PNG not fabricated",
    })

    status = "completed"
    if (problems or not check_report or not svg_source.is_file()
            or len(nodes) < 2 or material.get("degraded")):
        status = "partial"
    if material.get("degraded"):
        uncertainty.append({
            "item": "material extraction",
            "reason": "no readable material text was available",
            "impact": "only the task root is present; status=partial",
        })

    change_log = [
        {"step": 1, "action": "读取输入与材料",
         "evidence": "input/case.json, input/task.md, %d readable file(s)"
                     % int((coverage or {}).get("files_read", 0) or 0),
         "case_id": case_id},
        {"step": 2, "action": "抽取概念与关系",
         "evidence": "work/material.json",
         "nodes": len(nodes), "cross_links": len(outline.get("cross_links") or []),
         "preserved_facts": len(facts), "evidence_map": len(evidence)},
        {"step": 3, "action": "生成可编辑导图",
         "evidence": deliverable["path"], "format": deliverable["format"],
         "layout": outline.get("layout") or (outline.get("render") or {}).get("layout"),
         "nodes": len(nodes), "edges": len(edges)},
        {"step": 4, "action": "结构、来源与官方字段校验",
         "evidence": args.check or "work/check-report.json",
         "problems": len(problems), "warnings": len(warnings)},
        {"step": 5, "action": "写入 result.json 并保留 case_id",
         "evidence": "output/result.json", "status": status},
    ]
    if uncertainty:
        change_log.append({
            "step": 6, "action": "记录不确定性与人工确认项",
            "evidence": "result.uncertainty", "items": len(uncertainty),
            "manual_confirmation": [item.get("item") for item in uncertainty[:6]
                                    if isinstance(item, dict)],
        })

    result = {
        "case_id": case_id,
        "task_family": "mindmap",
        "status": status,
        "deliverable": deliverable,
        "preserved_facts": facts,
        "evidence_map": evidence,
        "change_log": change_log,
        "mindmap": {
            "root": {"node_id": root.get("node_id"),
                     "label": root.get("label"),
                     "source": root.get("source")},
            "nodes": len(nodes),
            "edges": len(edges),
            "depth": depth,
            "branches": branch_labels(outline),
            "structure_type": outline.get("structure_type") or "left_right_tree",
            "layout": outline.get("layout") or "tree",
        },
        "nodes": nodes,
        "edges": edges,
        "cross_links": outline.get("cross_links") or [],
        "coverage": coverage,
        "artifacts": artifacts,
        "uncertainty": uncertainty,
        "warnings": warnings,
        "output_contract": (material.get("output_contract")
                            or case.get("output_contract") or {}),
        "generated_by": "mindmap-builder/make_result.py",
    }
    kit.dump_json(out_path, result)
    print(json.dumps({
        "ok": True, "result": str(out_path), "case_id": case_id,
        "status": status, "deliverable": deliverable["path"],
        "nodes": len(nodes), "edges": len(edges),
        "preserved_facts": len(facts), "evidence_map": len(evidence),
        "uncertainty": len(uncertainty), "problems": len(problems),
        "warnings": len(warnings),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
