#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a source-bound citation graph from a finite document set.

Nodes are the supplied documents plus DOI references found in their text.
Edges keep the source line and a direction when the text makes it explicit;
otherwise the edge is recorded in ``direction_unknown``.  Paths are BFS over
the observed graph only; no remote citation or coverage claim is made.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


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


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def title_of(doc):
    text = doc.get("text") or doc.get("excerpt") or ""
    for line in text.splitlines():
        line = norm(line)
        if len(line) >= 20 and not DOI_RE.search(line):
            return line[:200]
    return norm(doc.get("path") or doc.get("rel_path") or "untitled")


def own_doi(doc):
    text = doc.get("text") or doc.get("excerpt") or ""
    # Prefer a DOI on a title/DOI line; fall back to the first DOI.
    for line in text.splitlines():
        if re.search(r"\bdoi\b", line, re.I):
            match = DOI_RE.search(line)
            if match:
                return match.group(0).lower()
    match = DOI_RE.search(text)
    return match.group(0).lower() if match else ""


def reference_lines(doc):
    text = doc.get("text") or doc.get("excerpt") or ""
    out = []
    for index, line in enumerate(text.splitlines(), 1):
        line = norm(line)
        if not line:
            continue
        for doi in DOI_RE.findall(line):
            out.append({"doi": doi.lower(), "locator": "L%d" % index,
                        "quote": line})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    inp, work, out = Path(args.input), Path(args.work), Path(args.output)
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    case = read_json(inp / "case.json", {})
    materials = read_json(work / "materials.json", {})
    docs = materials.get("documents") or materials.get("materials") or []
    nodes, seeds, edges, direction_unknown = {}, [], [], {}
    for index, doc in enumerate(docs, 1):
        path = doc.get("path") or doc.get("rel_path") or ""
        title = title_of(doc)
        doi = own_doi(doc)
        node_id = "D%03d" % index
        nodes[node_id] = {"id": node_id, "path": path, "title": title,
                          "doi": doi, "kind": "document"}
        seeds.append(node_id)
        for ref in reference_lines(doc):
            if ref["doi"] == doi:
                continue
            target = "DOI:" + ref["doi"]
            nodes.setdefault(target, {"id": target, "doi": ref["doi"],
                                      "title": "", "kind": "reference"})
            line = ref["quote"]
            if re.search(r"cited by|cites|citing", line, re.I):
                direction = "forward" if re.search(r"cited by", line, re.I) else "backward"
            elif re.search(r"references|bibliography|\[[0-9]+\]", line, re.I):
                direction = "backward"
            else:
                direction = "unknown"
                direction_unknown[target] = direction_unknown.get(target, 0) + 1
            edges.append({"id": "E%03d" % (len(edges) + 1),
                          "from": node_id, "to": target,
                          "source": path, "target": target,
                          "direction": direction,
                          "evidence": {"path": path, "locator": ref["locator"],
                                       "quote": line}})
    # Identity groups by DOI and normalized title.
    groups = {}
    for node in nodes.values():
        key = node.get("doi") or norm(node.get("title", "")).lower()
        if key:
            groups.setdefault(key, []).append(node["id"])
    identity_groups = [{"key": key, "nodes": ids,
                        "basis": "doi" if key.startswith("10.") else "title"}
                       for key, ids in groups.items()]
    for node in nodes.values():
        if not any(node["id"] in group["nodes"] for group in identity_groups):
            identity_groups.append({
                "key": node.get("doi") or norm(node.get("title", "")).lower()
                or node["id"], "nodes": [node["id"]], "basis": "singleton"})
    # Related works by shared reference targets with the seed set.
    seed_targets = {}
    for edge in edges:
        if edge["from"] in seeds:
            seed_targets.setdefault(edge["from"], set()).add(edge["to"])
    related = []
    for node_id in seeds:
        mine = seed_targets.get(node_id, set())
        for other in seeds:
            if other == node_id:
                continue
            shared = mine & seed_targets.get(other, set())
            if shared:
                related.append({
                    "id": other, "related_to": node_id,
                    "shared_references": sorted(shared),
                    "shared_count": len(shared),
                    "title": nodes[other]["title"],
                })
    related.sort(key=lambda row: (-row["shared_count"], row["id"]))
    if not related:
        related = [{
            "id": other, "related_to": seed,
            "shared_references": [], "shared_count": 0,
            "title": nodes[other]["title"],
            "basis": "no shared reference in the supplied finite subset",
        } for seed in seeds for other in seeds if other != seed]
    # BFS paths over the observed directed graph.
    adjacency = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], []).append(edge["to"])
    paths = []
    for seed in seeds[:3]:
        for target in [n["id"] for n in nodes.values()
                       if n["kind"] == "reference"][:30]:
            queue = deque([(seed, [seed])])
            seen = {seed}
            while queue:
                current, path = queue.popleft()
                if current == target:
                    hop_edges = []
                    for left, right in zip(path, path[1:]):
                        hop_edges.append(next((e["id"] for e in edges
                                               if e["from"] == left
                                               and e["to"] == right),
                                              "%s->%s" % (left, right)))
                    paths.append({"id": "P%03d" % (len(paths) + 1),
                                  "seed": seed, "target": target,
                                  "nodes": path, "edges": hop_edges,
                                  "path": path, "hops": len(path) - 1})
                    break
                if len(path) > 4:
                    continue
                for nxt in adjacency.get(current, []):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append((nxt, path + [nxt]))
    evidence = [edge["evidence"] for edge in edges[:500]]
    plan = {
        "case_id": case.get("case_id") or "UNKNOWN",
        "mode": "citation-tracking",
        "summary": "从 %d 个文档构建 %d 个节点、%d 条引用边、%d 条路径；方向未知边 %d。" % (
            len(docs), len(nodes), len(edges), len(paths),
            sum(direction_unknown.values())),
        "queries": case.get("queries") or [{
            "id": "Q1", "round": 1, "query": case.get("question") or "citation tracking",
            "source": "provided corpus", "purpose": "backward/forward tracking",
            "stop_condition": "all supplied documents scanned"}],
        "candidates": [{"id": node["id"], "title": node["title"],
                        "doi": node.get("doi", ""), "year": "",
                        "authors": "", "venue": "",
                        "relevance": "seed" if node["id"] in seeds else "reference",
                        "rank": index, "score": 1.0 if node["id"] in seeds else 0.5,
                        "reason": "observed in supplied documents",
                        "evidence": next((e["evidence"] for e in edges
                                          if e["from"] == node["id"]),
                                         {"path": node.get("path", ""),
                                          "locator": "L1",
                                          "quote": node.get("title", "")})}
                       for index, node in enumerate(
                           [n for n in nodes.values() if n["kind"] == "document"], 1)],
        "tracking_scope": {"documents": len(docs), "nodes": len(nodes),
                           "edges": len(edges), "finite_snapshot": True},
        "seeds": seeds, "edges": edges, "paths": paths[:100],
        "identity_groups": identity_groups,
        "related_works": related[:100],
        "direction_unknown": {"counts": direction_unknown,
                              "note": "edges without explicit direction are not treated as forward/backward"},
        "evidence": evidence,
        "findings": [{"id": "F%03d" % (i + 1),
                      "statement": "document %s shares %d references with %s" % (
                          row["id"], row["shared_count"], row["related_to"]),
                      "evidence": next((e["evidence"] for e in edges
                                        if e["from"] == row["id"] or
                                        e["from"] == row["related_to"]),
                                       {"path": nodes[row["id"]].get("path", ""),
                                        "locator": "L1",
                                        "quote": nodes[row["id"]].get("title", "")})}
                     for i, row in enumerate(related[:20])],
        "uncertainties": [
            {"item": "direction_unknown",
             "reason": "%d edges lack an explicit citing direction" % sum(direction_unknown.values()),
             "impact": "forward/backward counts cannot be claimed for them",
             "action": "read the reference line or use an authorized citation index"},
            {"item": "finite_snapshot",
             "reason": "only the supplied documents/reference lines were scanned",
             "impact": "no global citation coverage claim",
             "action": "treat as a source-bound subgraph"},
        ],
        "generated_at": now_iso(), "generated_by": "build_reference_graph.py",
    }
    write_json(work / "search-plan.json", plan)
    write_json(out / "search-results.json", {
        "case_id": plan["case_id"], "summary": plan["summary"],
        "nodes": list(nodes.values()), "edges": edges, "paths": paths[:100],
        "identity_groups": identity_groups, "related_works": related[:100],
        "direction_unknown": plan["direction_unknown"],
        "tracking_scope": plan["tracking_scope"], "evidence": evidence})
    write_json(out / "search-log.json", {
        "case_id": plan["case_id"], "queries": plan["queries"],
        "scope": plan["tracking_scope"], "generated_at": now_iso()})
    write_json(out / "evidence.json", {
        "case_id": plan["case_id"], "evidence": evidence})
    with (out / "candidates.csv").open("w", encoding="utf-8", newline="") as fh:
        import csv
        writer = csv.writer(fh)
        writer.writerow(["id", "kind", "title", "doi", "path"])
        for node in nodes.values():
            writer.writerow([node["id"], node["kind"], node.get("title", ""),
                             node.get("doi", ""), node.get("path", "")])
    summary = {"ok": bool(edges), "documents": len(docs), "nodes": len(nodes),
               "edges": len(edges), "paths": len(paths),
               "identity_groups": len(identity_groups),
               "related_works": len(related)}
    print(json.dumps(summary, ensure_ascii=False, indent=2)
          if args.json else json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
