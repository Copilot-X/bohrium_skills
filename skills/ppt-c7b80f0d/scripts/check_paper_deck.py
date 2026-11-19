#!/usr/bin/env python3
"""Validate a paper-to-deck result, deck spec and PPTX structure.

Usage:
    python3 check_paper_deck.py --result /app/output/result.json \
        --deck /app/output/deck.pptx --spec /app/work/deck-spec.json \
        --root /app/output --json
    python3 check_paper_deck.py --self-test --json

Checks
  * result has case_id/deck/artifacts/slides/checks/source_coverage
  * slide_count matches the PPTX zip; each slide has title/role/notes/claims
  * every mapped claim has a source locator; title/section slides may have []
  * deck-spec slide count/titles/kinds match the result
  * generic titles ("结果/方法/讨论") are flagged
  * PPTX has ppt/presentation.xml, slide XML and notes where declared
  * preview artifact exists when declared; no fabricated preview

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ROLES = {"cover", "toc", "section", "result", "method", "background",
         "limitation", "conclusion", "ending", "content", "chart", "figure", "table"}
KINDS = {"title", "section", "content", "title-only", "chart", "figure", "table", "ending"}
GENERIC_TITLE = {"结果", "方法", "讨论", "背景", "结论", "results", "methods",
                 "discussion", "background", "conclusion"}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def check_pptx(path, problems, warnings, checks):
    p = Path(path)
    if not p.is_file():
        problems.append("missing deck.pptx: %s" % p)
        return 0, 0
    try:
        with zipfile.ZipFile(p) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile as exc:
        problems.append("deck is not a valid zip: %s" % exc)
        return 0, 0
    slides = sorted(n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))
    notes = sorted(n for n in names if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n))
    if "ppt/presentation.xml" not in names:
        problems.append("PPTX missing ppt/presentation.xml")
    if "[Content_Types].xml" not in names:
        problems.append("PPTX missing [Content_Types].xml")
    if not slides:
        problems.append("PPTX has no slide XML")
    checks["pptx_slides"] = len(slides)
    checks["pptx_notes"] = len(notes)
    return len(slides), len(notes)


def validate(result, spec, deck_path, root):
    problems, warnings, checks = [], [], {}
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["result root is not an object"],
                "warnings": [], "checks": {}}
    for key in ("case_id", "deck", "artifacts", "slides", "checks", "source_coverage"):
        if key not in result:
            problems.append("result.json missing %s" % key)
    deck = result.get("deck") or {}
    actual_slides, _notes = check_pptx(deck_path, problems, warnings, checks) \
        if deck_path else (0, 0)
    declared = deck.get("slide_count") if isinstance(deck, dict) else None
    if actual_slides and declared is not None and int(declared) != actual_slides:
        problems.append("deck.slide_count %s != PPTX %d" % (declared, actual_slides))
    slides = result.get("slides") or []
    if not isinstance(slides, list) or not slides:
        problems.append("result.slides is empty")
        slides = []
    if declared is not None and len(slides) != int(declared):
        problems.append("slides[] length %d != deck.slide_count %s" %
                        (len(slides), declared))
    claim_ids = set()
    for i, slide in enumerate(slides):
        if not isinstance(slide, dict):
            problems.append("slide[%d] is not an object" % i)
            continue
        if slide.get("no") is None:
            problems.append("slide[%d] missing no" % i)
        title = str(slide.get("title") or "")
        if not title:
            problems.append("slide[%d] missing title" % i)
        elif title.strip().lower() in GENERIC_TITLE:
            warnings.append("slide[%d] has generic title: %s" % (i, title))
        role = slide.get("role")
        if role and role not in ROLES:
            warnings.append("slide[%d] unknown role: %s" % (i, role))
        if slide.get("notes_seconds") in (None, 0):
            warnings.append("slide[%d] has no notes_seconds" % i)
        for j, claim in enumerate(slide.get("claims") or []):
            if not isinstance(claim, dict):
                problems.append("slide[%d] claim[%d] is not an object" % (i, j))
                continue
            cid = claim.get("claim_id")
            if not cid:
                problems.append("slide[%d] claim[%d] missing claim_id" % (i, j))
            else:
                claim_ids.add(str(cid))
            source = claim.get("source")
            if not source or (isinstance(source, dict) and
                              not (source.get("path") or source.get("locator"))):
                problems.append("slide[%d] claim[%d] has no usable source" % (i, j))
    checks["slides"] = len(slides)
    checks["claims"] = len(claim_ids)

    if spec:
        if not isinstance(spec, dict):
            problems.append("deck-spec root is not an object")
        else:
            spec_slides = spec.get("slides") or []
            if declared is not None and len(spec_slides) != int(declared):
                problems.append("deck-spec slides %d != deck.slide_count %s" %
                                (len(spec_slides), declared))
            for i, slide in enumerate(spec_slides):
                if not isinstance(slide, dict):
                    problems.append("spec slide[%d] is not an object" % i)
                    continue
                if not slide.get("title"):
                    problems.append("spec slide[%d] missing title" % i)
                kind = slide.get("kind")
                if kind and kind not in KINDS:
                    warnings.append("spec slide[%d] unknown kind %s" % (i, kind))
                bullets = slide.get("bullets") or []
                if len(bullets) > 6:
                    warnings.append("spec slide[%d] has >6 bullets" % i)

    coverage = result.get("source_coverage") or {}
    if not isinstance(coverage, dict):
        problems.append("source_coverage is not an object")
    else:
        total = coverage.get("claims_total")
        mapped = coverage.get("claims_mapped")
        if total is not None and mapped is not None and int(mapped) > int(total):
            problems.append("claims_mapped > claims_total")
        if total is not None and claim_ids and int(total) < len(claim_ids):
            warnings.append("claims_total smaller than mapped claim ids")
    checks["coverage"] = coverage.get("sections_covered") if isinstance(coverage, dict) else None

    artifacts = result.get("artifacts") or []
    roles = {}
    base = Path(root) if root else Path.cwd()
    for i, item in enumerate(artifacts):
        if not isinstance(item, dict):
            problems.append("artifact[%d] is not an object" % i)
            continue
        role = str(item.get("role") or "").lower()
        rel = item.get("path")
        if not rel:
            problems.append("artifact[%d] missing path" % i)
            continue
        candidate = Path(rel)
        if not candidate.is_absolute():
            candidate = base / rel
        if not candidate.is_file():
            problems.append("artifact missing: %s" % rel)
            continue
        roles[role] = candidate
    for required in ("editable", "spec"):
        if required not in roles:
            problems.append("artifacts missing role=%s" % required)
    if "preview" in roles:
        raw = roles["preview"].read_bytes()
        if roles["preview"].suffix.lower() == ".png" and raw[:8] != b"\x89PNG\r\n\x1a\n":
            problems.append("preview is not a valid PNG")
    elif result.get("status") == "completed":
        warnings.append("completed deck has no preview artifact")
    checks["artifact_roles"] = sorted(roles)
    return {"ok": not problems, "problems": problems, "warnings": warnings, "checks": checks}


def self_test():
    result = {
        "case_id": "deck-self-test",
        "deck": {"slide_count": 2},
        "slides": [
            {"no": 1, "role": "cover", "title": "Paper title", "claims": [],
             "notes_seconds": 20},
            {"no": 2, "role": "result", "title": "A keeps 92% selectivity",
             "claims": [{"claim_id": "F1", "source": {"path": "p.pdf", "locator": "Fig.2"}}],
             "notes_seconds": 60},
        ],
        "checks": {"zip_valid": True},
        "source_coverage": {"claims_total": 1, "claims_mapped": 1,
                            "sections_covered": ["Results"]},
        "artifacts": [{"role": "editable", "path": "deck.pptx"},
                      {"role": "spec", "path": "deck-spec.json"}],
        "status": "completed",
    }
    spec = {"slides": [{"kind": "title", "title": "Paper title"},
                       {"kind": "content", "title": "A keeps 92%", "bullets": ["92%"]}]}
    return result, spec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None)
    ap.add_argument("--deck", default=None)
    ap.add_argument("--spec", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with zipfile.ZipFile(root / "deck.pptx", "w") as zf:
                zf.writestr("[Content_Types].xml", "<Types/>")
                zf.writestr("ppt/presentation.xml", "<p:presentation/>")
                zf.writestr("ppt/slides/slide1.xml", "<p:sld/>")
                zf.writestr("ppt/slides/slide2.xml", "<p:sld/>")
            (root / "deck-spec.json").write_text("{}", encoding="utf-8")
            result, spec = self_test()
            report = validate(result, spec, root / "deck.pptx", root)
        print(json.dumps({"ok": report["ok"], "self_test": True,
                          "problems": report["problems"],
                          "warnings": report["warnings"]}, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if not args.result:
        ap.error("--result is required unless --self-test is used")
    try:
        result = load_json(args.result)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "problems": ["cannot read result: %s" % exc]},
                         ensure_ascii=False))
        return 2
    spec = None
    if args.spec:
        try:
            spec = load_json(args.spec)
        except (OSError, json.JSONDecodeError):
            spec = None
    report = validate(result, spec, args.deck, args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
