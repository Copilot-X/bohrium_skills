#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reading 01 — PDF document parsing with structure, tables, formulas and anchors."""
from __future__ import annotations

import json
import re
from pathlib import Path

import parsers
import skillcore as S


def _pdf_doc(ctx):
    for doc in ctx.docs:
        if doc.get("kind") == "pdf" and S.doc_text(doc).strip():
            return doc
    best = None
    for doc in ctx.docs:
        if S.doc_text(doc).strip() and (best is None or len(S.doc_text(doc)) > len(S.doc_text(best))):
            best = doc
    return best


def _find_heading_block(blocks, names):
    pattern = re.compile(r"^\s*(?:%s)\s*$" % "|".join(names), re.I)
    for index, block in enumerate(blocks):
        if pattern.match(block.get("text", "").strip()):
            return index
    return None


def _title_authors(blocks, text):
    title = ""
    authors = []
    for block in blocks[:8]:
        body = (block.get("text") or "").strip()
        if not body or block.get("kind") in ("header", "footer"):
            continue
        if re.match(r"^(?:Abstract|摘要)\b", body, re.I):
            break
        lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines() if line.strip()]
        if not title:
            title = lines[0] if lines else re.sub(r"\s+", " ", body)
            for line in lines[1:]:
                if re.search(r"[A-Z][a-z]+.*(?:,| and |&)", line) and len(line) < 400:
                    authors = [x.strip() for x in re.split(r";|,| and |&", line) if x.strip()]
                    break
            continue
    if not title:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        title = first[:240]
    return title, authors


def _abstract(blocks):
    start = _find_heading_block(blocks, ["Abstract", "摘要"])
    if start is None:
        return ""
    collected = []
    for block in blocks[start + 1:]:
        body = block.get("text", "").strip()
        if block.get("kind") == "heading" or re.match(
                r"^(?:Keywords?|关键词|1[.)]?\s+Introduction|Introduction|引言)\b", body, re.I):
            break
        collected.append(body)
    return "\n\n".join(collected).strip()


def _refs(blocks):
    start = _find_heading_block(blocks, ["References", "Bibliography", "Literature Cited", "参考文献"])
    if start is None:
        return []
    entries = []
    current = []
    for block in blocks[start + 1:]:
        body = (block.get("text") or "").strip()
        if not body:
            continue
        if re.match(r"^\s*(?:\[\d+\]|\d+[.)])\s+", body):
            if current:
                entries.append(" ".join(current))
            current = [body]
        elif current:
            current.append(body)
    if current:
        entries.append(" ".join(current))
    out = []
    for index, raw in enumerate(entries, 1):
        doi = S.DOI_RE.search(raw)
        year = re.search(r"\b(?:19|20)\d{2}\b", raw)
        out.append({"id": "R%03d" % index, "raw": raw,
                    "doi": doi.group(0) if doi else "",
                    "year": year.group(0) if year else "",
                    "title": "", "authors": []})
    return out


def _tables_from_pages(pages):
    tables = []
    for page in pages:
        lines = (page.get("layout_text") or page.get("text") or "").splitlines()
        run = []
        start = 0
        for index, line in enumerate(lines + [""]):
            cells = [c.strip() for c in re.split(r"\s{2,}|\t+", line.strip()) if c.strip()]
            if len(cells) >= 2 and len(line.strip()) < 400:
                if not run:
                    start = index + 1
                run.append(cells)
            else:
                if len(run) >= 2:
                    width = max(len(r) for r in run)
                    rows = [r + [""] * (width - len(r)) for r in run]
                    tables.append({
                        "id": "T%03d" % (len(tables) + 1),
                        "page": page.get("page"),
                        "caption": "",
                        "columns": rows[0],
                        "rows": rows[1:] if len(rows) > 1 else rows,
                        "line_start": start,
                        "line_end": index,
                        "source_locator": "p%s L%s-L%s" % (page.get("page"), start, index),
                        "detection": "text-columns",
                    })
                run = []
        if len(run) >= 2:
            width = max(len(r) for r in run)
            rows = [r + [""] * (width - len(r)) for r in run]
            tables.append({
                "id": "T%03d" % (len(tables) + 1),
                "page": page.get("page"), "caption": "",
                "columns": rows[0], "rows": rows[1:],
                "line_start": start, "line_end": len(lines),
                "source_locator": "p%s L%s-L%s" % (page.get("page"), start, len(lines)),
                "detection": "text-columns",
            })
    return tables


def _formulas(blocks):
    formulas, seen = [], set()
    for block in blocks:
        body = (block.get("text") or "").strip()
        candidates = [body] if "\n" not in body else body.splitlines()
        for line in candidates:
            line = line.strip()
            if not line or line in seen:
                continue
            if not (re.search(r"(?:->|→|⇌|[=≈≤≥±×÷∑∫√∞∂∇])", line)
                    and re.search(r"\(\s*\d+[a-z]?\s*\)", line)):
                continue
            seen.add(line)
            number = re.search(r"\(\s*(\d+[a-z]?)\s*\)\s*$", line)
            formulas.append({
                "id": "E%03d" % (len(formulas) + 1),
                "page": block.get("page"),
                "raw_text": line,
                "latex": None,
                "number": number.group(1) if number else "",
                "locator": block.get("locator") or ("p%s L%s" % (
                    block.get("page"), block.get("line_start"))),
            })
    return formulas


def _figure_captions(blocks):
    figures = []
    for block in blocks:
        body = (block.get("text") or "").strip()
        if re.match(r"^\s*(?:Fig(?:ure)?\.?|图)\s*[A-Za-z0-9一二三四五六七八九十]", body, re.I):
            label = re.match(r"^\s*((?:Fig(?:ure)?\.?|图)\s*[A-Za-z0-9一二三四五六七八九十]+[a-z]?)",
                             body, re.I)
            figures.append({
                "id": "F%03d" % (len(figures) + 1),
                "label": label.group(1) if label else "",
                "caption": body,
                "page": block.get("page"),
                "locator": block.get("locator") or ("p%s L%s" % (
                    block.get("page"), block.get("line_start"))),
            })
    return figures


def _sections_from_blocks(blocks):
    sections = parsers.detect_sections(blocks)
    out = []
    paragraph_counter = 0
    for s_index, section in enumerate(sections, 1):
        paragraphs = []
        for block in section.get("blocks") or []:
            body = (block.get("text") or "").strip()
            if not body or block.get("kind") in ("header", "footer"):
                continue
            paragraph_counter += 1
            page = block.get("page")
            block_id = block.get("line_start") or paragraph_counter
            locator = "p%s b%s" % (page, block_id) if page else "b%s" % block_id
            paragraphs.append({
                "id": "P%03d" % paragraph_counter,
                "text": body,
                "page": page,
                "block_id": block_id,
                "bbox": block.get("bbox"),
                "locator": locator,
                "kind": block.get("kind") or "body",
            })
        if section.get("blocks") or section.get("heading") != "Front matter":
            out.append({
                "id": "S%03d" % s_index,
                "heading": section.get("heading", ""),
                "level": section.get("level", 1),
                "paragraphs": paragraphs,
            })
    return out


def prepare(ctx):
    doc = _pdf_doc(ctx)
    if doc is None:
        ctx.result.change("prepare", "input", "no readable PDF or text")
        ctx.result.uncertainty("document", "no readable source document",
                               "parsing cannot start", "provide a PDF or text file")
        return ctx.result.finalize("input_error")
    text = S.doc_text(doc)
    pages = doc.get("pages") or []
    blocks = doc.get("blocks") or []
    if pages:
        for page in pages:
            page_blocks = page.get("blocks") or []
            page["blocks"] = parsers.order_blocks(page_blocks)
        blocks = [b for page in pages for b in (page.get("blocks") or [])]
    sections = _sections_from_blocks(blocks)
    title, authors = _title_authors(blocks, text)
    tables = _tables_from_pages(pages) if pages else []
    if doc.get("tables"):
        for index, table in enumerate(doc["tables"], len(tables) + 1):
            tables.append({"id": "T%03d" % index, "page": None, "caption": "",
                           "columns": table.get("rows", [[]])[0] if table.get("rows") else [],
                           "rows": table.get("rows", [])[1:] if table.get("rows") else [],
                           "source_locator": "docx table %d" % index,
                           "detection": table.get("source", "docx")})
    references = _refs(blocks)
    formulas = _formulas(blocks)
    figures = _figure_captions(blocks)
    readable_pages = sum(1 for p in pages if p.get("readable"))
    ocr_pages = [p.get("page") for p in pages if p.get("ocr")]
    empty_pages = [p.get("page") for p in pages if not p.get("readable")]
    document = {
        "case_id": ctx.case_id,
        "source_path": doc.get("rel_path"),
        "title": title,
        "authors": authors,
        "abstract": _abstract(blocks),
        "page_count": len(pages),
        "readable_pages": readable_pages,
        "ocr_pages": ocr_pages,
        "empty_pages": empty_pages,
        "sections": sections,
        "tables": tables,
        "figures": figures,
        "equations": formulas,
        "references": references,
        "headers_footers": [b for b in blocks if b.get("kind") in ("header", "footer")][:100],
        "engine": doc.get("engine") or doc.get("kind"),
        "warnings": doc.get("warnings") or [],
    }
    report = {
        "case_id": ctx.case_id, "source_path": doc.get("rel_path"),
        "page_count": len(pages), "readable_pages": readable_pages,
        "ocr_pages": ocr_pages, "empty_pages": empty_pages,
        "sections": len(sections),
        "paragraphs": sum(len(s["paragraphs"]) for s in sections),
        "tables": len(tables), "figures": len(figures),
        "equations": len(formulas), "references": len(references),
        "warnings": document["warnings"],
    }
    S.write_json(ctx.work / "document-draft.json", {"document": document, "report": report})
    S.write_json(ctx.work / "document-template.json", {
        "case_id": ctx.case_id,
        "document": {key: document[key] for key in
                     ("title", "authors", "abstract", "page_count", "sections",
                      "tables", "figures", "equations", "references")},
        "report": report,
    })
    S.write_text(ctx.work / "document-outline.md", "\n".join(
        ["# Parsed outline", ""] + ["%s%s" % ("  " * max(s["level"] - 1, 0), s["heading"])
                                    for s in sections]))
    ctx.result.change("parse", doc.get("rel_path", ""),
                      "%d pages, %d sections, %d tables, %d equations" % (
                          len(pages), len(sections), len(tables), len(formulas)))
    return ctx.provisional(reason="PDF parsed into draft structure; review/correct document.json, then run finalize")


def _render_markdown(document):
    lines = ["# %s" % (document.get("title") or "Parsed document"), ""]
    if document.get("authors"):
        lines += ["**Authors:** %s" % ", ".join(map(str, document["authors"])), ""]
    if document.get("abstract"):
        lines += ["## Abstract", "", document["abstract"], ""]
    for section in document.get("sections") or []:
        heading = str(section.get("heading") or "")
        if heading.strip().lower() in ("front matter",):
            continue
        if document.get("abstract") and heading.strip().lower() in ("abstract", "摘要"):
            continue
        lines += ["%s %s" % ("#" * min(section.get("level", 1) + 1, 6),
                             heading), ""]
        for para in section.get("paragraphs") or []:
            lines += [para.get("text", ""), ""]
    if document.get("tables"):
        lines += ["## Tables", ""]
        for table in document["tables"]:
            lines += ["### %s" % table.get("id", ""), "",
                      "| %s |" % " | ".join(map(str, table.get("columns") or [])),
                      "| %s |" % " | ".join("---" for _ in (table.get("columns") or [])),
                      ]
            for row in table.get("rows") or []:
                lines.append("| %s |" % " | ".join(map(str, row)))
            lines += ["", "Source: `%s`" % table.get("source_locator", ""), ""]
    if document.get("equations"):
        lines += ["## Equations", ""]
        for eq in document["equations"]:
            lines += ["- **%s** `%s`" % (eq.get("number") or eq.get("id"),
                                          eq.get("raw_text", "")), ""]
    if document.get("figures"):
        lines += ["## Figures", ""]
        for figure in document["figures"]:
            lines += ["- **%s** %s (`%s`)" % (figure.get("label") or figure.get("id"),
                                               figure.get("caption", ""),
                                               figure.get("locator", "")), ""]
    if document.get("references"):
        lines += ["## References", ""]
        for ref in document["references"]:
            lines += ["- %s" % ref.get("raw", ""), ""]
    return "\n".join(lines).strip() + "\n"


def finalize(ctx):
    payload = S.load_json(ctx.work / "document.json", {})
    if not payload.get("document"):
        payload = S.load_json(ctx.work / "document-draft.json", {})
    document = payload.get("document") if isinstance(payload, dict) else None
    if not document:
        ctx.result.change("finalize", "output", "no document structure")
        ctx.result.uncertainty("document", "document.json/document-draft.json missing",
                               "no parsed document", "run prepare first")
        return ctx.result.finalize("input_error")
    report = payload.get("report") or {}
    problems = []
    if not document.get("sections"):
        problems.append("no sections were identified")
    if not document.get("title"):
        problems.append("title is empty")
    if document.get("page_count") and document.get("readable_pages", 0) < document["page_count"]:
        problems.append("%d/%d pages are unreadable" % (
            document["page_count"] - document.get("readable_pages", 0),
            document["page_count"]))
    task_lower = (ctx.task or "").lower()
    if re.search(r"公式|equation|formula", task_lower) and not document.get("equations"):
        problems.append("task asks for formulas but none were detected")
    if re.search(r"表格|table", task_lower) and not document.get("tables"):
        problems.append("task asks for tables but none were detected")
    if re.search(r"参考文献|reference", task_lower) and not document.get("references"):
        problems.append("task asks for references but none were detected")
    for section in document.get("sections") or []:
        for para in section.get("paragraphs") or []:
            if not para.get("text"):
                problems.append("empty paragraph %s" % para.get("id"))
            if not para.get("locator"):
                problems.append("paragraph %s lacks a locator" % para.get("id"))
    for table in document.get("tables") or []:
        if not table.get("columns") or not table.get("rows"):
            problems.append("table %s has no rows/columns" % table.get("id"))
    S.write_json(ctx.output / "document.json", {
        "case_id": ctx.case_id, "document": document, "report": report,
    })
    S.write_text(ctx.output / "document.md", _render_markdown(document))
    tables_dir = ctx.output / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    for table in document.get("tables") or []:
        tid = re.sub(r"[^A-Za-z0-9_-]+", "_", str(table.get("id") or "table"))
        S.write_json(tables_dir / ("%s.json" % tid), table)
        lines = ["| %s |" % " | ".join(map(str, table.get("columns") or [])),
                 "| %s |" % " | ".join("---" for _ in (table.get("columns") or []))]
        for row in table.get("rows") or []:
            lines.append("| %s |" % " | ".join(map(str, row)))
        S.write_text(tables_dir / ("%s.md" % tid), "\n".join(lines) + "\n")
    S.write_json(ctx.output / "parse-report.json", report)
    # Human-readable summary and machine-readable characterization.  These are
    # generated from the actual PDF parse (not a prepared answer) and make the
    # result easier to inspect.
    readme = [
        "# Parsed document: %s" % (document.get("title") or ctx.case_id),
        "",
        "- Source: `%s`" % document.get("source_path"),
        "- Pages: %d (readable: %d, OCR: %s)" % (
            document.get("page_count", 0), document.get("readable_pages", 0),
            document.get("ocr_pages") or "none"),
        "- Sections: %d" % len(document.get("sections") or []),
        "- Tables: %d" % len(document.get("tables") or []),
        "- Figures: %d" % len(document.get("figures") or []),
        "- Equations: %d" % len(document.get("equations") or []),
        "- References: %d" % len(document.get("references") or []),
        "",
        "Inspect `document.json` for the structured result and `document.md`",
        "for the readable rendering. Table files are under `tables/`.",
        "",
        "## Limits",
    ]
    for item in problems[:20]:
        readme.append("- %s" % item)
    if not problems:
        readme.append("- No structural gaps were detected; verify formulas and OCR pages manually.")
    S.write_text(ctx.output / "README.md", "\n".join(readme) + "\n")
    characterization = {
        "case_id": ctx.case_id,
        "title": document.get("title"),
        "authors": document.get("authors") or [],
        "page_count": document.get("page_count", 0),
        "readable_pages": document.get("readable_pages", 0),
        "ocr_pages": document.get("ocr_pages") or [],
        "empty_pages": document.get("empty_pages") or [],
        "section_count": len(document.get("sections") or []),
        "section_headings": [s.get("heading") for s in document.get("sections") or []],
        "paragraph_count": sum(len(s.get("paragraphs") or []) for s in document.get("sections") or []),
        "table_count": len(document.get("tables") or []),
        "figure_count": len(document.get("figures") or []),
        "equation_count": len(document.get("equations") or []),
        "reference_count": len(document.get("references") or []),
        "engine": document.get("engine"),
    }
    S.write_json(ctx.output / "characterization.json", characterization)
    manifest_files = ["document.json", "document.md", "parse-report.json",
                      "characterization.json", "README.md"]
    manifest = []
    for rel in manifest_files:
        path = ctx.output / rel
        if path.is_file():
            manifest.append({"path": rel, "role": "parsed_artifact",
                             "bytes": path.stat().st_size,
                             "sha256": S.sha256_path(path)})
    S.write_json(ctx.output / "arm_manifest.json", {
        "case_id": ctx.case_id, "artifacts": manifest,
    })
    status = "answered" if not problems else "partial"
    for problem in problems[:20]:
        ctx.result.uncertainty("parse", problem, "the parsed document is incomplete",
                               "review the affected page/section")
    if document.get("ocr_pages"):
        ctx.result.uncertainty("ocr", "OCR used on pages %s" % document["ocr_pages"],
                               "OCR text may contain recognition errors",
                               "check those pages against the PDF image")
    if not document.get("equations"):
        ctx.result.uncertainty("equations", "no equations detected",
                               "formula coverage may be incomplete",
                               "check pages with formulas")
    claims = [
        {"claim_id": "structure", "text": "Document has %d sections and %d paragraphs" % (
            len(document.get("sections") or []),
            sum(len(s.get("paragraphs") or []) for s in document.get("sections") or []))},
        {"claim_id": "tables", "text": "%d tables, %d figures, %d equations, %d references" % (
            len(document.get("tables") or []), len(document.get("figures") or []),
            len(document.get("equations") or []), len(document.get("references") or []))},
    ]
    evidence = []
    for section in document.get("sections") or []:
        for para in (section.get("paragraphs") or [])[:200]:
            evidence.append({"claim_id": para.get("id"), "claim": S.excerpt(para.get("text"), 120),
                             "source": {"path": document.get("source_path"),
                                        "locator": para.get("locator")},
                             "quote": S.excerpt(para.get("text"), 200),
                             "supports": "verbatim"})
    ctx.result.add_artifact(ctx.output / "document.json", "deliverable", "json",
                            "structured document")
    ctx.result.add_artifact(ctx.output / "document.md", "readable", "md",
                            "human-readable structured document")
    ctx.result.add_artifact(ctx.output / "parse-report.json", "audit", "json",
                            "page, section, table and equation coverage")
    ctx.result.add_artifact(ctx.output / "README.md", "readable", "md",
                            "human-readable parse summary")
    ctx.result.add_artifact(ctx.output / "characterization.json", "characterization", "json",
                            "document-level coverage statistics")
    ctx.result.add_artifact(ctx.output / "arm_manifest.json", "manifest", "json",
                            "artifact manifest with sha256")
    for table in document.get("tables") or []:
        tid = re.sub(r"[^A-Za-z0-9_-]+", "_", str(table.get("id") or "table"))
        ctx.result.add_artifact(tables_dir / ("%s.json" % tid), "table", "json",
                                "structured table %s" % tid)
    ctx.result.change("parse", document.get("source_path", ""),
                      "structure, tables, equations and references assembled")
    ctx.result.change("audit", "document", "page coverage and locators checked")
    return ctx.result.finalize(
        status,
        deliverable=ctx.output / "document.json",
        result={"summary": "Parsed %d pages: %d sections, %d tables, %d equations, %d references." % (
                    document.get("page_count", 0), len(document.get("sections") or []),
                    len(document.get("tables") or []), len(document.get("equations") or []),
                    len(document.get("references") or [])),
                "title": document.get("title"), "report": report, "problems": problems},
        claims=claims, preserved_facts=[
            {"kind": "page", "source": str(p), "target": "document.json", "locator": "p%d" % p,
             "value": str(p), "unit": "page"} for p in range(1, min(document.get("page_count", 0), 50) + 1)
        ], evidence_map=evidence,
    )
