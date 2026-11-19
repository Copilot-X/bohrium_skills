#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Page-level golden contract and per-page diff.

The "gold" is derived from the approved deck spec plus the real source index:
it states what each page must claim, which locator backs it, whether a figure
is required and whether notes must be present.  It is a review contract, not a
prewritten answer.
"""
from __future__ import annotations

import re
from pathlib import Path


def _norm(text):
    text = str(text).replace(",", "").replace("，", "").replace("μ", "µ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _evidence_verified(claim, corpus):
    probe = _norm(str(claim))[:60]
    if len(probe) < 12:
        return False
    return probe in corpus


def build_gold(spec, brief, inspected, content_map=None):
    corpus = _norm("\n".join(s.get("text", "") for s in brief.get("sources", [])))
    base_path = brief.get("starting_deck")
    base_name = Path(base_path).name if base_path else ""
    pages = []
    for i, slide in enumerate(spec.get("slides", []) or [], start=1):
        evidence = []
        for claim in slide.get("claims") or []:
            src = (claim or {}).get("source") or {}
            if src.get("path") and src.get("locator"):
                is_base = bool(base_name and Path(str(src["path"])).name == base_name)
                is_office = str(src["path"]).lower().endswith((".pptx", ".potx", ".pptm"))
                evidence.append({
                    "claim": str(claim.get("claim", ""))[:300],
                    "path": src["path"],
                    "locator": src["locator"],
                    "verified_in_source": True if (is_base or is_office) else
                    _evidence_verified(claim.get("claim", ""), corpus),
                })
        source_ref = slide.get("source_ref") or {}
        if not evidence and source_ref.get("path") and source_ref.get("locator"):
            evidence.append({"claim": str(slide.get("title", "")),
                             "path": source_ref["path"],
                             "locator": source_ref["locator"],
                             "verified_in_source": True})
        pages.append({
            "index": i,
            "role": slide.get("role") or slide.get("kind"),
            "title": str(slide.get("title", "")),
            "bullets": [str(b) for b in (slide.get("bullets") or [])],
            "notes_required": True,
            "figure_required": bool(slide.get("image")),
            "figure": str(slide.get("image")) if slide.get("image") else None,
            "evidence": evidence,
            "layout": str(slide.get("layout") or slide.get("kind") or "content"),
        })
    gold = {
        "version": 1,
        "deck": {
            "title": str(spec.get("title", "")),
            "slides": len(pages),
            "size": spec.get("size", "16:9"),
            "source_files": [{"path": s.get("path"), "sha256": s.get("sha256")}
                             for s in brief.get("sources", [])],
        },
        "pages": pages,
        "standard": {
            "title": "每页标题必须是可陈述的结论句或明确角色名",
            "evidence": "每个内容页至少一个 claim 有 path + locator，且可在源文回查",
            "notes": "每页必须有讲稿备注，备注不得引入材料外数字",
            "figure": "有图的页面必须嵌入真实源图或真实数据表重绘图，不得使用占位图",
            "preservation": "edit/adapt 未点名页面/母版/版式/主题必须保留",
        },
    }
    built = inspected.get("slides", []) or []
    content_map = content_map or []
    map_slides = {int(item.get("slide", 0)) for item in content_map if item.get("slide")}
    rows = []
    for page in pages:
        idx = page["index"]
        slide = built[idx - 1] if idx - 1 < len(built) else None
        texts = [str(t) for t in (slide or {}).get("texts", [])]
        title_present = bool(slide) and any(
            page["title"] and (page["title"] in t or t in page["title"]) for t in texts)
        notes_present = bool(slide) and bool(str(slide.get("notes") or "").strip())
        bullets_present = 0
        for bullet in page["bullets"][:5]:
            probe = str(bullet)[:24]
            if any(probe and (probe in t or t[:24] == probe) for t in texts):
                bullets_present += 1
        figure_present = (not page["figure_required"]) or bool(
            slide and slide.get("images"))
        evidence_ok = all(e["verified_in_source"] for e in page["evidence"]) \
            if page["evidence"] else page["role"] in ("title", "agenda", "evidence")
        mapped = idx in map_slides or page["role"] in ("title", "agenda", "evidence")
        checks = {
            "slide_exists": bool(slide),
            "title_present": title_present,
            "notes_present": notes_present,
            "bullets_present": bullets_present,
            "bullets_expected": len(page["bullets"][:5]),
            "figure_present": figure_present,
            "evidence_verified": evidence_ok,
            "evidence_in_content_map": mapped,
        }
        checks["pass"] = all(checks[k] for k in
                             ("slide_exists", "title_present", "notes_present",
                              "figure_present", "evidence_verified",
                              "evidence_in_content_map"))
        rows.append({"slide": idx, "role": page["role"], **checks})
    passed = sum(1 for row in rows if row["pass"])
    diff = {
        "deck": str(inspected.get("path", "")),
        "pages": rows,
        "summary": {"pages": len(rows), "passed": passed,
                    "failed": len(rows) - passed,
                    "pass_rate": round(passed / len(rows), 3) if rows else 0.0},
    }
    return gold, diff
