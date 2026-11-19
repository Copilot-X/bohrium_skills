#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reading 15 — evidence-bound literature knowledge graph with real queries."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import skillcore as S

NODE_TYPES = {"researcher", "material", "method", "task", "metric", "finding",
              "concept", "dataset", "condition", "organism", "model", "other"}
EDGE_TYPES = {"uses", "compares", "outperforms", "underperforms", "supports",
              "contradicts", "measured_by", "part_of", "is_a", "predicts",
              "causes", "associated_with", "reports", "evaluated_on",
              "trained_on", "derived_from", "related_to", "enhances",
              "reduces", "modulates", "interacts_with", "co_occurs_with",
              "has_part", "has_property", "measured_as", "expresses",
              "encodes", "treats", "prevents", "induces", "represses", "other"}


def _json_files(ctx):
    out = {}
    for doc in ctx.docs:
        if doc.get("kind") == "json" and isinstance(doc.get("data"), (dict, list)):
            out[Path(doc.get("rel_path") or "").name.lower()] = doc
    return out


def _dicts(value):
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        for key in ("papers", "documents", "items", "corpus", "material",
                    "materials", "records", "entries"):
            if isinstance(value.get(key), list):
                return [x for x in value[key] if isinstance(x, dict)]
        return [value]
    return []


def _paper_from_dict(item, index):
    source_id = str(item.get("source_id") or item.get("id") or item.get("paper_id")
                    or "P%d" % index)
    title = str(item.get("title") or item.get("name") or "")
    authors = item.get("authors") or item.get("author") or []
    if isinstance(authors, str):
        authors = [x.strip() for x in re.split(r";|,", authors) if x.strip()]
    paragraphs = []
    for p_index, para in enumerate(item.get("paragraphs") or item.get("sections") or [], 1):
        if isinstance(para, dict):
            pid = str(para.get("paragraph_id") or para.get("id") or "p%d" % p_index)
            text = str(para.get("text") or para.get("content") or "")
        else:
            pid = "p%d" % p_index
            text = str(para)
        if text.strip():
            paragraphs.append({"source_id": source_id, "paragraph_id": pid,
                               "text": text, "locator": "%s %s" % (source_id, pid)})
    return {"source_id": source_id, "doi": str(item.get("doi") or ""),
            "title": title, "authors": authors,
            "license": item.get("license") or item.get("rights") or "",
            "version": item.get("version") or "",
            "paragraphs": paragraphs}


def _papers_from_doc(doc, start_index=1):
    data = doc.get("data")
    papers = []
    for index, item in enumerate(_dicts(data), start_index):
        paper = _paper_from_dict(item, index)
        if not paper["paragraphs"]:
            paper["paragraphs"] = _paragraphs_from_text(S.doc_text(doc), paper["source_id"])
        papers.append(paper)
    return papers


def _load_corpus(ctx):
    files = _json_files(ctx)
    papers, by_id = [], {}

    def add(paper):
        sid = paper["source_id"]
        if sid in by_id:
            existing = by_id[sid]
            for key in ("doi", "title", "authors", "license", "version"):
                if not existing.get(key) and paper.get(key):
                    existing[key] = paper[key]
            if not existing.get("paragraphs") and paper.get("paragraphs"):
                existing["paragraphs"] = paper["paragraphs"]
            return
        by_id[sid] = paper
        papers.append(paper)

    # Explicit material files are the most reliable source.
    for name in ("primary-material.json", "secondary-material.json", "material.json"):
        doc = files.get(name)
        if doc:
            for paper in _papers_from_doc(doc, len(papers) + 1):
                add(paper)
    # The manifest supplies DOI/title/author metadata and may add papers.
    manifest = files.get("corpus-manifest.json")
    if manifest:
        for index, item in enumerate(_dicts(manifest.get("data")), 1):
            paper = _paper_from_dict(item, index)
            if not paper["paragraphs"]:
                target = str(item.get("path") or item.get("file") or "")
                for doc in ctx.docs:
                    if target and Path(doc.get("rel_path") or "").name == Path(target).name:
                        if doc.get("kind") == "json" and isinstance(doc.get("data"), dict):
                            nested = _paper_from_dict(doc.get("data"), index)
                            paper["paragraphs"] = nested.get("paragraphs") or []
                        if not paper["paragraphs"]:
                            paper["paragraphs"] = _paragraphs_from_text(
                                S.doc_text(doc), paper["source_id"])
                        break
            add(paper)
    if not papers:
        for index, doc in enumerate([d for d in ctx.docs if S.doc_text(d).strip()], 1):
            text = S.doc_text(doc)
            match = S.DOI_RE.search(text)
            add({
                "source_id": "P%d" % index,
                "doi": match.group(0) if match else "",
                "title": Path(doc.get("rel_path") or "").stem,
                "authors": [], "license": "", "version": "",
                "paragraphs": _paragraphs_from_text(text, "P%d" % index),
            })
    # Ensure paragraph IDs are unique inside each paper.
    for paper in papers:
        used = set()
        for pid_index, para in enumerate(paper["paragraphs"], 1):
            pid = para.get("paragraph_id") or "p%d" % pid_index
            while pid in used:
                pid += "x"
            used.add(pid)
            para["paragraph_id"] = pid
            para["source_id"] = paper["source_id"]
            para["locator"] = "%s %s" % (paper["source_id"], pid)
    return papers


def _paragraphs_from_text(text, source_id):
    segments = S.segment_blocks(text, source_id=source_id, path="")
    return [{"source_id": source_id, "paragraph_id": s["id"],
             "text": s["text"], "locator": "%s %s" % (source_id, s["id"])}
            for s in segments]


def prepare(ctx):
    corpus = _load_corpus(ctx)
    if not corpus:
        ctx.result.change("prepare", "input", "no readable corpus")
        ctx.result.uncertainty("corpus", "no readable paper text or manifest",
                               "graph cannot be built", "provide the corpus files")
        return ctx.result.finalize("input_error")
    index = {}
    for paper in corpus:
        for para in paper["paragraphs"]:
            index["%s:%s" % (para["source_id"], para["paragraph_id"])] = para
    S.write_json(ctx.work / "corpus.json", {
        "case_id": ctx.case_id, "papers": corpus,
        "paragraph_count": len(index),
        "paragraph_index": index,
        "input_revision": ctx.case.get("input_revision") or "",
    })
    S.write_json(ctx.work / "graph-template.json", {
        "case_id": ctx.case_id,
        "nodes": [{"id": "N1", "label": "", "type": "material|method|task|metric|finding|researcher|concept",
                   "aliases": [], "source_ids": ["P1"],
                   "evidence": [{"source_id": "P1", "paragraph_id": "P001", "quote": ""}]}],
        "edges": [{"id": "E1", "source": "N1", "target": "N2",
                   "type": "uses|compares|outperforms|supports|contradicts|measured_by|part_of|is_a",
                   "direction": "forward|reverse|undirected", "conditions": [],
                   "evidence": [{"source_id": "P1", "paragraph_id": "P001", "quote": ""}],
                   "strength": "direct|indirect"}],
        "merges": [{"candidate_ids": ["N1", "N2"], "decision": "merge|keep_separate",
                    "basis": "", "conflict": ""}],
        "queries": [{"id": "Q1", "question": "",
                     "start_node_ids": [], "edge_types": [], "result_node_ids": [],
                     "answer": "", "evidence": [], "uncertain": []}],
        "node_types": sorted(NODE_TYPES), "edge_types": sorted(EDGE_TYPES),
    })
    S.write_json(ctx.work / "query-template.json", {
        "case_id": ctx.case_id,
        "queries": [{"id": "Q1", "question": "real question answerable by the graph",
                     "start_node_ids": [], "edge_types": [], "result_node_ids": [],
                     "answer": "", "evidence": [], "uncertain": []}],
        "minimum_queries": 3,
    })
    ctx.result.change("corpus", "input", "%d papers, %d paragraphs" % (
        len(corpus), len(index)))
    return ctx.provisional(reason="corpus indexed; write graph.json with nodes, edges, merges and 3 queries")


def _node_ids(graph):
    return {str(n.get("id")): n for n in graph.get("nodes") or [] if isinstance(n, dict)}


def _edge_ids(graph):
    return {str(e.get("id")): e for e in graph.get("edges") or [] if isinstance(e, dict)}


def _evidence_problems(evidence, paragraph_index):
    problems = []
    for index, item in enumerate(evidence or [], 1):
        if not isinstance(item, dict):
            problems.append("evidence %d is not an object" % index)
            continue
        sid = str(item.get("source_id") or "")
        pid = str(item.get("paragraph_id") or "")
        if not sid or not pid:
            problems.append("evidence %d lacks source_id/paragraph_id" % index)
            continue
        para = paragraph_index.get("%s:%s" % (sid, pid))
        if para is None:
            problems.append("evidence %d: paragraph %s:%s not found" % (index, sid, pid))
            continue
        quote = item.get("quote") or ""
        ok, reason = S.quote_supported(para.get("text", ""), quote)
        if not ok:
            problems.append("evidence %d: %s" % (index, reason))
        locator = str(item.get("locator") or "")
        if re.search(r"\bpage\b|\bp\.\s*\d+", locator, re.I):
            problems.append("evidence %d uses a page locator; use paragraph IDs" % index)
    return problems


def _query_subgraph(query, nodes, edges):
    start = [str(x) for x in (query.get("start_node_ids") or query.get("start_nodes") or [])]
    types = {str(x).lower() for x in (query.get("edge_types") or [])}
    explicit = [str(x) for x in (query.get("result_node_ids") or [])]
    if explicit:
        selected = set(explicit) | set(start)
    else:
        selected = set(start)
        for _ in range(3):
            for edge in edges.values():
                if types and str(edge.get("type", "")).lower() not in types:
                    continue
                if str(edge.get("source")) in selected or str(edge.get("target")) in selected:
                    selected.add(str(edge.get("source")))
                    selected.add(str(edge.get("target")))
    sub_edges = [edge for edge in edges.values()
                 if str(edge.get("source")) in selected and str(edge.get("target")) in selected]
    return sorted(selected), sub_edges


def finalize(ctx):
    graph = S.load_json(ctx.work / "graph.json", {})
    if not isinstance(graph, dict) or not (graph.get("nodes") or graph.get("edges")):
        ctx.result.change("finalize", "output", "graph.json missing or empty")
        ctx.result.uncertainty("graph", "model-authored graph.json is missing",
                               "no knowledge graph", "write graph.json")
        return ctx.result.finalize("partial")
    corpus = S.load_json(ctx.work / "corpus.json", {})
    paragraph_index = corpus.get("paragraph_index") or {}
    nodes = _node_ids(graph)
    edges = _edge_ids(graph)
    problems = []
    for nid, node in nodes.items():
        if not node.get("label"):
            problems.append("node %s has no label" % nid)
        ntype = str(node.get("type") or "").lower()
        if ntype not in NODE_TYPES:
            problems.append("node %s has unknown type %s" % (nid, ntype))
        if not node.get("source_ids"):
            problems.append("node %s has no source_ids" % nid)
        problems.extend(_evidence_problems(node.get("evidence") or [], paragraph_index))
    for eid, edge in edges.items():
        if str(edge.get("source")) not in nodes or str(edge.get("target")) not in nodes:
            problems.append("edge %s references an unknown node" % eid)
        etype = str(edge.get("type") or "").lower()
        if etype not in EDGE_TYPES:
            problems.append("edge %s has unknown type %s" % (eid, etype))
        if not edge.get("evidence"):
            problems.append("edge %s has no evidence" % eid)
        problems.extend(_evidence_problems(edge.get("evidence") or [], paragraph_index))
    merges = graph.get("merges") or []
    for merge in merges:
        candidates = [str(x) for x in (merge.get("candidate_ids") or [])]
        if any(c not in nodes for c in candidates):
            problems.append("merge references unknown candidate: %s" % candidates)
        if merge.get("decision") not in ("merge", "keep_separate"):
            problems.append("merge decision must be merge or keep_separate")
        if not merge.get("basis"):
            problems.append("merge lacks a basis")
    queries = graph.get("queries") or []
    if len(queries) < 3:
        problems.append("at least 3 executed queries are required")
    query_results = []
    for index, query in enumerate(queries, 1):
        qid = str(query.get("id") or "Q%d" % index)
        selected, sub_edges = _query_subgraph(query, nodes, edges)
        if not selected:
            problems.append("query %s returned no nodes" % qid)
        if not query.get("question"):
            problems.append("query %s has no question" % qid)
        if not query.get("answer"):
            problems.append("query %s has no answer" % qid)
        query_results.append({
            "id": qid, "question": query.get("question"),
            "start_node_ids": query.get("start_node_ids") or [],
            "edge_types": query.get("edge_types") or [],
            "result_nodes": [{"id": nid, "label": nodes[nid].get("label"),
                              "type": nodes[nid].get("type")} for nid in selected if nid in nodes],
            "result_edges": [{"id": e.get("id"), "source": e.get("source"),
                              "target": e.get("target"), "type": e.get("type")}
                             for e in sub_edges],
            "answer": query.get("answer"),
            "evidence": query.get("evidence") or [],
            "uncertain": query.get("uncertain") or [],
        })
    # GraphML and CSV exports.
    graphml = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
               '<key id="label" for="node" attr.name="label" attr.type="string"/>',
               '<key id="type" for="node" attr.name="type" attr.type="string"/>',
               '<key id="source_ids" for="node" attr.name="source_ids" attr.type="string"/>',
               '<key id="aliases" for="node" attr.name="aliases" attr.type="string"/>',
               '<key id="etype" for="edge" attr.name="type" attr.type="string"/>',
               '<key id="conditions" for="edge" attr.name="conditions" attr.type="string"/>',
               '<graph id="%s" edgedefault="directed">' % _esc(ctx.case_id)]
    for nid, node in nodes.items():
        graphml.append('<node id="%s"><data key="label">%s</data><data key="type">%s</data>'
                       '<data key="source_ids">%s</data><data key="aliases">%s</data></node>' % (
                           _esc(nid), _esc(node.get("label", "")), _esc(node.get("type", "")),
                           _esc(",".join(map(str, node.get("source_ids") or []))),
                           _esc(",".join(map(str, node.get("aliases") or [])))))
    for eid, edge in edges.items():
        graphml.append('<edge id="%s" source="%s" target="%s"><data key="etype">%s</data>'
                       '<data key="conditions">%s</data></edge>' % (
                           _esc(eid), _esc(edge.get("source")), _esc(edge.get("target")),
                           _esc(edge.get("type", "")),
                           _esc("; ".join(map(str, edge.get("conditions") or [])))))
    graphml.append("</graph></graphml>")
    S.write_text(ctx.output / "graph.graphml", "\n".join(graphml) + "\n")
    with (ctx.output / "nodes.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "label", "type", "aliases", "source_ids", "evidence_count"])
        for nid, node in nodes.items():
            writer.writerow([nid, node.get("label"), node.get("type"),
                             "; ".join(map(str, node.get("aliases") or [])),
                             "; ".join(map(str, node.get("source_ids") or [])),
                             len(node.get("evidence") or [])])
    with (ctx.output / "edges.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "source", "target", "type", "conditions", "strength"])
        for eid, edge in edges.items():
            writer.writerow([eid, edge.get("source"), edge.get("target"),
                             edge.get("type"), "; ".join(map(str, edge.get("conditions") or [])),
                             edge.get("strength", "")])
    S.write_json(ctx.output / "graph.json", {
        "case_id": ctx.case_id, "papers": corpus.get("papers") or [],
        "nodes": list(nodes.values()), "edges": list(edges.values()),
        "merges": merges, "queries": [q.get("question") for q in queries],
        "input_revision": corpus.get("input_revision", ""),
    })
    S.write_json(ctx.output / "queries.json", {
        "case_id": ctx.case_id, "queries": query_results,
    })
    S.write_json(ctx.output / "merge-report.json", {
        "case_id": ctx.case_id, "merges": merges,
        "merge_count": sum(1 for m in merges if m.get("decision") == "merge"),
        "keep_separate_count": sum(1 for m in merges if m.get("decision") == "keep_separate"),
    })
    lines = ["# Literature knowledge graph", "",
             "- Nodes: %d" % len(nodes), "- Edges: %d" % len(edges),
             "- Papers: %d" % len(corpus.get("papers") or []), "",
             "## Queries", ""]
    for q in query_results:
        lines.append("### %s" % q["question"])
        lines.append("")
        lines.append(q.get("answer", ""))
        lines.append("")
        lines.append("- Nodes: %s" % ", ".join(n.get("label", n.get("id")) for n in q["result_nodes"][:20]))
        lines.append("")
    S.write_text(ctx.output / "graph.md", "\n".join(lines))

    status = "answered" if not problems else "partial"
    for problem in problems[:25]:
        ctx.result.uncertainty("graph", problem, "graph has incomplete evidence or structure",
                               "fix the node/edge/query")
    if not corpus.get("input_revision"):
        ctx.result.uncertainty("input_revision", "no explicit input revision was supplied",
                               "cross-paper identity must be checked manually",
                               "read corpus-manifest.json and material.json carefully")

    preserved = []
    for node in nodes.values():
        for ev in node.get("evidence") or []:
            for token in S.number_tokens(ev.get("quote", ""))[:10]:
                preserved.append({"kind": "number", "source": token["raw"],
                                  "target": token["raw"],
                                  "locator": "%s %s" % (ev.get("source_id"), ev.get("paragraph_id")),
                                  "value": token["raw"], "unit": ""})
    evidence_map = []
    for nid, node in nodes.items():
        for ev in node.get("evidence") or []:
            evidence_map.append({"claim_id": nid, "claim": node.get("label"),
                                 "source": {"path": "corpus",
                                            "locator": "%s %s" % (ev.get("source_id"), ev.get("paragraph_id"))},
                                 "quote": ev.get("quote"), "supports": "node"})
    for eid, edge in edges.items():
        for ev in edge.get("evidence") or []:
            evidence_map.append({"claim_id": eid, "claim": edge.get("type"),
                                 "source": {"path": "corpus",
                                            "locator": "%s %s" % (ev.get("source_id"), ev.get("paragraph_id"))},
                                 "quote": ev.get("quote"), "supports": "edge"})

    ctx.result.add_artifact(ctx.output / "graph.json", "deliverable", "json",
                            "nodes, edges, merges and queries")
    ctx.result.add_artifact(ctx.output / "graph.graphml", "graph_export", "graphml",
                            "GraphML export")
    ctx.result.add_artifact(ctx.output / "nodes.csv", "graph_export", "csv", "nodes table")
    ctx.result.add_artifact(ctx.output / "edges.csv", "graph_export", "csv", "edges table")
    ctx.result.add_artifact(ctx.output / "queries.json", "queries", "json",
                            "executed query results and subgraphs")
    ctx.result.add_artifact(ctx.output / "merge-report.json", "merge_report", "json",
                            "merge and keep-separate decisions")
    ctx.result.add_artifact(ctx.output / "graph.md", "readable", "md",
                            "human-readable graph summary")
    ctx.result.change("graph", corpus.get("case_id", ""),
                      "%d nodes, %d edges, %d queries" % (len(nodes), len(edges), len(queries)))
    ctx.result.change("audit", "graph", "evidence, types, merges and queries checked")
    return ctx.result.finalize(
        status,
        deliverable=ctx.output / "graph.json",
        result={"summary": "Knowledge graph with %d nodes, %d edges and %d queries." % (
                    len(nodes), len(edges), len(queries)),
                "node_count": len(nodes), "edge_count": len(edges),
                "query_count": len(queries), "checks": {"problems": problems}},
        claims=[{"claim_id": nid, "text": node.get("label"),
                 "evidence": node.get("evidence")} for nid, node in nodes.items()],
        preserved_facts=preserved, evidence_map=evidence_map,
    )


def _esc(value):
    return xml_escape(str(value or ""), {'"': "&quot;"})


from xml.sax.saxutils import escape as xml_escape  # noqa: E402
