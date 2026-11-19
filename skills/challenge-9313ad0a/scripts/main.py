#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entry point for the mind-map skill.

Pipeline: extract -> build -> check -> result -> final check.

Usage:
    python3 main.py run --input /input --work /app/work --output /app/output --json
    python3 main.py run --input /input --work /app/work --output /app/output --layout radial

The entry point only orchestrates the sibling scripts. It never invents
concepts: when a stage fails, it degrades to the task root / JSON deliverable
and still writes result.json with the official fields.

Exit codes:
    0 = result.json written (completed or partial; input errors included)
    2 = the process could not start at all (output directory cannot be created)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
OFFICIAL_FIELDS = ("deliverable", "preserved_facts", "evidence_map",
                   "change_log")


def configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def resolve_script(name: str) -> Path:
    """Resolve a sibling script both in the package (.py) and on disk (.py.md)."""
    for candidate in (HERE / (name + ".py"), HERE / (name + ".py.md")):
        if candidate.is_file():
            return candidate
    return HERE / (name + ".py")


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8",
                                               errors="replace"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def dump_json(path, data) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


def run_step(name: str, extra, timeout: int = 240):
    script = resolve_script(name)
    if not script.is_file():
        return {"name": name, "ok": False, "returncode": 127,
                "error": "script missing: %s" % script, "seconds": 0.0}
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(script)] + [str(item) for item in extra],
            cwd=str(script.parent), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": name, "ok": False, "returncode": 124,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "seconds": round(time.time() - started, 2)}
    stdout = (proc.stdout or "").strip()
    payload = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            start, end = stdout.find("{"), stdout.rfind("}")
            if start >= 0 and end > start:
                payload = load_json_text(stdout[start:end + 1])
    return {
        "name": name,
        "ok": proc.returncode == 0 and (payload is None
                                        or payload.get("ok") is not False),
        "returncode": proc.returncode,
        "payload": payload,
        "stdout_tail": stdout[-1200:],
        "stderr_tail": (proc.stderr or "").strip()[-1200:],
        "seconds": round(time.time() - started, 2),
    }


def load_json_text(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def case_info(input_dir: Path):
    case = load_json(input_dir / "case.json", {})
    if not isinstance(case, dict):
        case = {}
    task_text = ""
    try:
        task_text = (input_dir / "task.md").read_text(encoding="utf-8",
                                                     errors="replace")
    except OSError:
        pass
    title = ""
    for key in ("title", "name", "topic", "subject"):
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            title = value.strip()
            break
    if not title:
        for line in task_text.splitlines():
            line = line.strip().lstrip("#").strip()
            if 2 <= len(line) <= 80:
                title = line
                break
    return case, (title or "思维导图")


def ensure_material(input_dir: Path, work_dir: Path) -> Path:
    """Create a root-only ledger if extraction did not produce one."""
    path = work_dir / "material.json"
    if path.is_file():
        return path
    case, title = case_info(input_dir)
    case_id = str(case.get("case_id") or case.get("id") or "").strip()
    material = {
        "case_id": case_id or "unknown-case",
        "title": title,
        "task_instruction": str(case.get("task_instruction") or ""),
        "constraints": {},
        "output_contract": case.get("output_contract") or {},
        "sources": [],
        "sections": [],
        "nodes": [{
            "node_id": "n0", "parent": None, "label": title[:30],
            "type": "topic", "detail": "未提取到可读材料，仅保留任务根节点",
            "source": "task", "kind": "task", "importance": "core",
        }],
        "cross_links": [],
        "preserved_facts": [],
        "evidence_map": [],
        "coverage": {"sections_covered": [], "not_covered": [],
                     "files_read": 0, "files_total": 0},
        "stats": {"nodes": 1, "branches": 0, "cross_links": 0},
        "warnings": [],
        "uncertainty": [{
            "item": "materials",
            "reason": "no readable material text was available",
            "impact": "deliverable is a partial task-root map",
        }],
        "degraded": True,
    }
    dump_json(path, material)
    return path


def node_source(node):
    value = node.get("source")
    if isinstance(value, dict):
        source = {"path": str(value.get("path") or ""),
                  "locator": str(value.get("locator") or "")}
        if value.get("quote"):
            source["quote"] = str(value["quote"])
        return source
    if isinstance(value, str) and value.strip():
        return {"path": "", "locator": value.strip()}
    return {"path": "", "locator": ""}


def derive_edges(nodes, cross_links):
    by_id = {str(node.get("node_id")): node for node in nodes}
    edges = []
    for node in nodes:
        parent = str(node.get("parent") or "")
        if parent and parent in by_id:
            edges.append({
                "edge_id": "e-%s" % node.get("node_id"),
                "from": parent, "to": node.get("node_id"),
                "relation": "has_child", "kind": "hierarchy",
                "source": node_source(node),
            })
    for index, link in enumerate(cross_links or []):
        if not isinstance(link, dict):
            continue
        edges.append({
            "edge_id": "x-%d" % (index + 1),
            "from": str(link.get("from") or ""),
            "to": str(link.get("to") or ""),
            "relation": str(link.get("relation") or "link"),
            "kind": "cross", "confidence": link.get("kind") or "reported",
            "source": node_source(link),
        })
    return edges


def derive_facts(nodes):
    facts = []
    for node in nodes:
        if node.get("node_id") == "n0" or node.get("kind") != "reported":
            continue
        source = node_source(node)
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


def derive_evidence(nodes):
    entries = []
    for node in nodes:
        if node.get("kind") != "reported":
            continue
        source = node_source(node)
        if not source.get("path"):
            continue
        entries.append({
            "claim_id": node.get("node_id"),
            "claim": node.get("label", ""),
            "node_type": node.get("type", "claim"),
            "kind": "reported", "source": source,
        })
        if len(entries) >= 40:
            break
    return entries


def finalize_result(result_path: Path, input_dir: Path, work_dir: Path,
                    output_dir: Path, final_ok: bool, input_ok: bool) -> dict:
    """Guarantee the official fields and real artifact paths in result.json."""
    case, title = case_info(input_dir)
    material = load_json(work_dir / "material.json", {}) or {}
    outline = load_json(work_dir / "mindmap.json", {}) or {}
    result = load_json(result_path, None)
    if not isinstance(result, dict):
        result = {}

    nodes = result.get("nodes") or outline.get("nodes") or []
    if not nodes:
        nodes = [{
            "node_id": "n0", "parent": None, "label": title[:30],
            "type": "topic", "detail": "未找到大纲，保留任务根节点",
            "source": "task", "kind": "task", "importance": "core",
        }]
    cross_links = result.get("cross_links") or outline.get("cross_links") or []
    edges = result.get("edges") or derive_edges(nodes, cross_links)

    outline_target = output_dir / "mindmap.json"
    if not outline_target.is_file():
        dump_json(outline_target, {
            "case_id": (case.get("case_id") or material.get("case_id")
                        or outline.get("case_id") or "unknown-case"),
            "title": title, "nodes": nodes, "cross_links": cross_links,
            "coverage": outline.get("coverage") or {},
        })
    spec_path = output_dir / "mindmap-spec.json"
    svg_path = output_dir / "mindmap.svg"

    if svg_path.is_file():
        deliverable = {"path": svg_path.name, "format": "svg",
                       "role": "editable", "editable": True,
                       "nodes": len(nodes), "edges": len(edges)}
    else:
        deliverable = {"path": outline_target.name, "format": "json",
                       "role": "editable", "editable": True,
                       "nodes": len(nodes), "edges": len(edges)}

    artifacts = [item for item in (result.get("artifacts") or [])
                 if isinstance(item, dict) and item.get("path")]
    roles = {str(item.get("role")) for item in artifacts}
    if "structure" not in roles:
        artifacts.append({"role": "structure", "path": outline_target.name,
                          "format": "json", "editable": True,
                          "nodes": len(nodes), "edges": len(edges),
                          "bytes": outline_target.stat().st_size})
    if spec_path.is_file() and "spec" not in roles:
        artifacts.append({"role": "spec", "path": spec_path.name,
                          "format": "json", "nodes": len(nodes),
                          "edges": len(edges),
                          "bytes": spec_path.stat().st_size})
    if "editable" not in roles:
        target = svg_path if svg_path.is_file() else outline_target
        artifacts.append({"role": "editable", "path": target.name,
                          "format": "svg" if target is svg_path else "json",
                          "editable": True, "nodes": len(nodes),
                          "edges": len(edges), "bytes": target.stat().st_size})

    facts = result.get("preserved_facts") or material.get("preserved_facts") \
        or derive_facts(nodes)
    evidence = result.get("evidence_map") or material.get("evidence_map") \
        or derive_evidence(nodes)
    uncertainty = result.get("uncertainty")
    if not isinstance(uncertainty, list):
        uncertainty = list(material.get("uncertainty") or [])
    if not input_ok:
        uncertainty.append({
            "item": "input", "reason": "input directory was not readable",
            "impact": "partial task-root map; case files were not parsed",
        })
    if not final_ok and not any(isinstance(item, dict)
                                and item.get("item") == "verification"
                                for item in uncertainty):
        uncertainty.append({
            "item": "verification",
            "reason": "final structural check did not pass",
            "impact": "status=partial; inspect work/check-report-final.json",
        })

    status = "completed"
    if (not final_ok or not input_ok or len(nodes) < 2
            or material.get("degraded") or result.get("status") == "partial"):
        status = "partial"

    change_log = result.get("change_log")
    if not isinstance(change_log, list) or not change_log:
        change_log = [
            {"step": 1, "action": "读取输入与材料",
             "evidence": "input/case.json, input/task.md"},
            {"step": 2, "action": "抽取概念、证据与关系",
             "evidence": "work/material.json", "nodes": len(nodes)},
            {"step": 3, "action": "生成可编辑导图",
             "evidence": deliverable["path"], "format": deliverable["format"]},
            {"step": 4, "action": "结构、来源与官方字段校验",
             "evidence": "work/check-report-final.json",
             "passed": bool(final_ok)},
            {"step": 5, "action": "写入 result.json 并保留 case_id",
             "evidence": "output/result.json", "status": status},
        ]
    if uncertainty and not any(isinstance(item, dict)
                               and "uncertainty" in str(item.get("action", ""))
                               for item in change_log):
        change_log.append({
            "step": len(change_log) + 1,
            "action": "记录不确定性与人工确认项",
            "evidence": "result.uncertainty", "items": len(uncertainty),
        })

    case_id = str(case.get("case_id") or case.get("id")
                  or material.get("case_id") or outline.get("case_id")
                  or result.get("case_id") or "unknown-case").strip()
    root = next((node for node in nodes if not node.get("parent")), nodes[0])
    result.update({
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
            "nodes": len(nodes), "edges": len(edges),
            "depth": (result.get("mindmap") or {}).get("depth"),
            "branches": [str(node.get("label")) for node in nodes
                         if str(node.get("parent") or "")
                         == str(root.get("node_id"))][:10],
            "structure_type": outline.get("structure_type") or "left_right_tree",
            "layout": outline.get("layout") or "tree",
        },
        "nodes": nodes,
        "edges": edges,
        "cross_links": cross_links,
        "coverage": result.get("coverage") or material.get("coverage") or {},
        "artifacts": artifacts,
        "uncertainty": uncertainty,
        "warnings": result.get("warnings") or material.get("warnings") or [],
        "output_contract": (result.get("output_contract")
                            or material.get("output_contract")
                            or case.get("output_contract") or {}),
        "generated_by": "mindmap-builder/main.py",
    })
    dump_json(result_path, result)
    return result


def command_run(args) -> int:
    input_dir = Path(args.input)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        input_dir = input_dir.resolve()
        work_dir = work_dir.resolve()
        output_dir = output_dir.resolve()
    except OSError as exc:
        print(json.dumps({"ok": False, "error": "cannot create work/output: %s"
                          % exc}, ensure_ascii=False), file=sys.stderr)
        return 2

    input_ok = input_dir.is_dir()
    material = work_dir / "material.json"
    outline = work_dir / "mindmap.json"
    spec = work_dir / "mindmap-spec.json"
    svg = output_dir / "mindmap.svg"
    result = output_dir / "result.json"
    first_check = work_dir / "check-report.json"
    final_check = work_dir / "check-report-final.json"

    steps = []
    steps.append(run_step("extract_material",
                          ["--input", str(input_dir.resolve()),
                           "--work", str(work_dir), "--out", str(material),
                           "--json"]))
    ensure_material(input_dir, work_dir)
    steps.append(run_step("build_mindmap",
                          ["--input", str(material),
                           "--out-outline", str(outline),
                           "--out-spec", str(spec), "--out-svg", str(svg),
                           "--layout", args.layout, "--json"]))
    check_args = ["--material", str(material), "--outline", str(outline),
                  "--spec", str(spec), "--svg", str(svg),
                  "--max-nodes", str(args.max_nodes), "--json"]
    first = run_step("check_mindmap", check_args)
    steps.append(first)
    first_report = first.get("payload")
    if not isinstance(first_report, dict):
        first_report = {"ok": False, "problems": [
            "check_mindmap produced no JSON report"], "warnings": [],
            "checks": {}}
    dump_json(first_check, first_report)

    steps.append(run_step("make_result",
                          ["--input", str(input_dir.resolve()),
                           "--work", str(work_dir),
                           "--output", str(output_dir), "--out", str(result),
                           "--outline", str(outline), "--spec", str(spec),
                           "--svg", str(svg), "--check", str(first_check),
                           "--json"]))
    final = run_step("check_mindmap",
                     check_args + ["--result", str(result),
                                   "--root", str(output_dir)])
    steps.append(final)
    final_report = final.get("payload")
    if not isinstance(final_report, dict):
        final_report = {"ok": False, "problems": [
            "final check produced no JSON report"], "warnings": [],
            "checks": {}}
    dump_json(final_check, final_report)

    final_ok = bool(final_report.get("ok"))
    try:
        summary_result = finalize_result(result, input_dir, work_dir,
                                         output_dir, final_ok, input_ok)
    except Exception as exc:  # noqa: BLE001 - always leave a real result
        summary_result = {
            "case_id": "unknown-case", "status": "partial",
            "deliverable": {"path": "mindmap.json", "format": "json",
                            "role": "editable"},
            "preserved_facts": [], "evidence_map": [], "change_log": [
                {"step": 1, "action": "入口降级写结果",
                 "evidence": "output/result.json",
                 "error": "%s: %s" % (type(exc).__name__, exc)}],
            "uncertainty": [{"item": "entry", "reason": str(exc),
                             "impact": "partial result"}],
        }
        try:
            dump_json(result, summary_result)
        except OSError:
            return 2

    summary = {
        "ok": bool(result.is_file()) and summary_result.get("status")
              == "completed",
        "status": summary_result.get("status"),
        "input": str(input_dir), "work": str(work_dir),
        "output": str(output_dir),
        "case_id": summary_result.get("case_id"),
        "deliverable": (summary_result.get("deliverable") or {}).get("path"),
        "artifacts": {
            "material": material.is_file(),
            "outline": outline.is_file(),
            "spec": spec.is_file(),
            "svg": svg.is_file(),
            "result": result.is_file(),
            "check_report": first_check.is_file(),
            "final_check_report": final_check.is_file(),
        },
        "nodes": len(summary_result.get("nodes") or []),
        "edges": len(summary_result.get("edges") or []),
        "problems": final_report.get("problems") or [],
        "warnings": (final_report.get("warnings") or [])[:12],
        "degraded": (not input_ok) or any(
            step.get("returncode") not in (0, None) for step in steps
            if step.get("name") != "check_mindmap"),
        "steps": [{key: step.get(key) for key in
                   ("name", "returncode", "seconds")} for step in steps],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.is_file() else 2


def absolute(value):
    try:
        return str(Path(value).resolve())
    except (OSError, TypeError):
        return value


def command_passthrough(args, name):
    script_name = {"extract": "extract_material", "build": "build_mindmap",
                   "check": "check_mindmap", "result": "make_result"}[name]
    if name == "extract":
        extra = ["--input", absolute(args.input)]
        if args.work:
            extra += ["--work", absolute(args.work)]
        if args.out:
            extra += ["--out", absolute(args.out)]
    elif name == "build":
        extra = (["--input", absolute(args.input)] if args.input
                 else ["--outline", absolute(args.outline)])
        for flag in ("out_outline", "out_spec", "out_svg"):
            value = getattr(args, flag, None)
            if value:
                extra += ["--" + flag.replace("_", "-"), absolute(value)]
        extra += ["--layout", getattr(args, "layout", "tree")]
    elif name == "check":
        extra = []
        for flag in ("material", "outline", "spec", "svg", "result", "root"):
            value = getattr(args, flag, None)
            if value:
                extra += ["--" + flag, absolute(value)]
        if getattr(args, "max_nodes", None):
            extra += ["--max-nodes", str(args.max_nodes)]
        if getattr(args, "self_test", False):
            extra.append("--self-test")
    else:
        extra = ["--input", absolute(args.input)]
        if args.work:
            extra += ["--work", absolute(args.work)]
        if args.output:
            extra += ["--output", absolute(args.output)]
        for flag in ("out", "outline", "spec", "svg", "check"):
            value = getattr(args, flag, None)
            if value:
                extra += ["--" + flag, absolute(value)]
    extra.append("--json")
    step = run_step(script_name, extra)
    payload = step.get("payload")
    print(json.dumps(payload if payload is not None else {
        key: step.get(key) for key in
        ("name", "returncode", "error", "stdout_tail", "stderr_tail")},
        ensure_ascii=False, indent=2))
    return step.get("returncode", 1)


def main() -> int:
    configure_stdout()
    argv = sys.argv[1:]
    known = ("run", "extract", "build", "check", "result", "-h", "--help")
    if not argv or argv[0] not in known:
        argv = ["run"] + argv
    parser = argparse.ArgumentParser(description="Mind-map skill entry point")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="extract -> build -> check -> result")
    run.add_argument("--input", default="/input")
    run.add_argument("--work", default="/app/work")
    run.add_argument("--output", default="/app/output")
    run.add_argument("--layout", default="tree", choices=("tree", "radial"))
    run.add_argument("--max-nodes", type=int, default=40)
    run.add_argument("--json", action="store_true")

    extract = sub.add_parser("extract", help="material ledger only")
    extract.add_argument("--input", default="/input")
    extract.add_argument("--work", default="/app/work")
    extract.add_argument("--out", default=None)
    extract.add_argument("--json", action="store_true")

    build = sub.add_parser("build", help="render JSON/SVG from an outline")
    build.add_argument("--input", default=None)
    build.add_argument("--outline", default=None)
    build.add_argument("--out-outline", default=None)
    build.add_argument("--out-spec", default=None)
    build.add_argument("--out-svg", default=None)
    build.add_argument("--layout", default="tree", choices=("tree", "radial"))
    build.add_argument("--json", action="store_true")

    check = sub.add_parser("check", help="validate outline/spec/SVG/result")
    check.add_argument("--material", default=None)
    check.add_argument("--outline", default=None)
    check.add_argument("--spec", default=None)
    check.add_argument("--svg", default=None)
    check.add_argument("--result", default=None)
    check.add_argument("--root", default=None)
    check.add_argument("--max-nodes", type=int, default=40)
    check.add_argument("--self-test", action="store_true")
    check.add_argument("--json", action="store_true")

    result = sub.add_parser("result", help="official result.json only")
    result.add_argument("--input", default="/input")
    result.add_argument("--work", default="/app/work")
    result.add_argument("--output", default="/app/output")
    result.add_argument("--out", default=None)
    result.add_argument("--outline", default=None)
    result.add_argument("--spec", default=None)
    result.add_argument("--svg", default=None)
    result.add_argument("--check", default=None)
    result.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command in (None, "run"):
        try:
            return command_run(args)
        except Exception:  # noqa: BLE001 - never crash without a trace
            traceback.print_exc()
            return 2
    if args.command == "check" and args.self_test and not args.outline:
        step = run_step("check_mindmap", ["--self-test", "--json"])
        print(json.dumps(step.get("payload") or {
            "ok": step.get("returncode") == 0,
            "stderr": step.get("stderr_tail")}, ensure_ascii=False, indent=2))
        return step.get("returncode", 1)
    return command_passthrough(args, args.command)


if __name__ == "__main__":
    sys.exit(main())
