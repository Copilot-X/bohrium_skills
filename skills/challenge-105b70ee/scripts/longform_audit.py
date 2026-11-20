#!/usr/bin/env python3
"""Audit a long-form thesis/report for cross-chapter consistency.

Usage:
    python3 longform_audit.py --work /app/work --index /app/work/chapter-index.json \
        --rules /app/work/global-rules.json --out /app/work/consistency-report.json --json

Checks
  * index chapters resolve to real files; ids/order are unique
  * per-chapter and total word counts vs. targets
  * figure/table references have definitions, and definitions are used
  * "见 3.2 节" / "Section 3.2" cross-references point at real headings
  * numeric citations [n] are present in the bibliography, and vice versa
  * terminology variants listed in global-rules.terms do not leak into chapters
  * missing chapters / empty files are reported, never invented

This is a consistency gate, not a writing-quality score. Stdlib only.
Exit 0 = no errors (warnings allowed), 1 = errors, 2 = unreadable index.
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

CJK = re.compile(r"[㐀-鿿]")
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)
HEADING_NUM = re.compile(
    r"^\s*(?:第\s*)?(\d+(?:[.．]\d+)*)\s*(?:章|节|节)?\s*[、.．:：]?\s*(.*)$")
FIG_DEF = re.compile(
    r"^\s*(?:\*\*)?\s*(?:图|Figure|Fig\.?|表|Table)\s*"
    r"([0-9]+(?:[-–—.．][0-9]+)?)\s*(?:[:：.．、]|\*\*|$)", re.I | re.M)
FIG_REF = re.compile(r"(?:图|Figure|Fig\.?|表|Table)\s*([0-9]+(?:[-–—.．][0-9]+)?)", re.I)
CROSSREF = re.compile(
    r"(?:见|参见|详见|第)\s*([0-9]+(?:[.．][0-9]+)+)\s*(?:节|章|部分)?"
    r"|(?:Section|Sec\.)\s*([0-9]+(?:[.．][0-9]+)+)", re.I)
CITE_GROUP = re.compile(r"\[([0-9]+(?:[-,，]\s*[0-9]+)*)\]")
BIB_ENTRY = re.compile(r"^\s*\[([0-9]+)\]\s+", re.M)
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|lorem ipsum|待补充|待填写)\b", re.I)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def word_count(text):
    return len(CJK.findall(text)) + len(WORD.findall(text))


def normalize_number(value):
    return re.sub(r"[–—.．]", "-", str(value)).strip("-")


def resolve_path(work, value):
    path = Path(value)
    if path.is_absolute():
        return path
    for base in (work, work / "chapters", work.parent):
        candidate = base / value
        if candidate.is_file():
            return candidate
    return work / value


def parse_citations(value):
    out = []
    for group in CITE_GROUP.findall(value):
        for part in re.split(r"[,，]", group):
            part = part.strip()
            if "-" in part:
                bits = [x.strip() for x in part.split("-") if x.strip().isdigit()]
                if len(bits) == 2:
                    out.extend(range(int(bits[0]), int(bits[1]) + 1))
            elif part.isdigit():
                out.append(int(part))
    return out


def audit(work, index, rules):
    issues, stats = [], {}
    chapters = index.get("chapters") or []
    if not isinstance(chapters, list):
        raise ValueError("chapter-index.json: chapters must be a list")
    rules = rules or {}
    terms = rules.get("terms") or {}
    term_variants = {}
    for canonical, values in terms.items():
        if isinstance(values, str):
            values = [values]
        for value in values:
            if value and value != canonical:
                term_variants[str(value)] = str(canonical)

    seen_ids, seen_order = set(), set()
    total_words = 0
    total_target = 0
    heading_numbers = set()
    all_text = []
    chapter_rows = []

    for i, item in enumerate(chapters):
        if not isinstance(item, dict):
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "index",
                           "severity": "error", "where": "chapter[%d]" % i,
                           "problem": "chapter entry is not an object", "fix": "make it an object"})
            continue
        cid = str(item.get("chapter_id") or item.get("id") or "")
        if not cid:
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "index",
                           "severity": "error", "where": "chapter[%d]" % i,
                           "problem": "missing chapter_id", "fix": "add chapter_id"})
            continue
        if cid in seen_ids:
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "index",
                           "severity": "error", "where": cid,
                           "problem": "duplicate chapter_id", "fix": "make ids unique"})
        seen_ids.add(cid)
        order = item.get("order")
        if order is not None:
            if order in seen_order:
                issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "index",
                               "severity": "error", "where": cid,
                               "problem": "duplicate chapter order %s" % order,
                               "fix": "renumber the chapter index"})
            seen_order.add(order)
        path_value = item.get("path") or item.get("file")
        if not path_value:
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "index",
                           "severity": "error", "where": cid,
                           "problem": "missing chapter path", "fix": "add path"})
            continue
        path = resolve_path(work, path_value)
        row = {"chapter_id": cid, "title": item.get("title") or "",
               "path": str(path), "status": item.get("status") or "",
               "words_target": item.get("words_target") or item.get("words") or 0}
        if not path.is_file():
            row["status"] = "missing"
            row["words_actual"] = 0
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "missing",
                           "severity": "error", "where": cid,
                           "problem": "chapter file missing: %s" % path_value,
                           "fix": "write the chapter or mark it in missing_parts"})
            chapter_rows.append(row)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        all_text.append((cid, text))
        actual = word_count(text)
        row["words_actual"] = actual
        total_words += actual
        target = int(row["words_target"] or 0)
        total_target += target
        if target:
            if actual > target * 1.2:
                issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "budget",
                               "severity": "warning", "where": cid,
                               "problem": "chapter is %d words vs target %d (>20%% over)" %
                                          (actual, target),
                               "fix": "compress background/repetition or reallocate budget"})
            elif actual < target * 0.8 and actual > 0:
                issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "budget",
                               "severity": "warning", "where": cid,
                               "problem": "chapter is %d words vs target %d (>20%% under)" %
                                          (actual, target),
                               "fix": "add evidence/analysis or lower the target"})
        if PLACEHOLDER.search(text):
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "placeholder",
                           "severity": "error", "where": cid,
                           "problem": "chapter contains placeholder text",
                           "fix": "replace TODO/TBD with real content or mark partial"})
        for heading in HEADING.findall(text):
            match = HEADING_NUM.match(heading)
            if match:
                heading_numbers.add(normalize_number(match.group(1)))
        chapter_rows.append(row)

    # Figure/table definitions and references across the whole manuscript.
    definitions = Counter()
    references = Counter()
    for cid, text in all_text:
        for number in FIG_DEF.findall(text):
            definitions[normalize_number(number)] += 1
        for number in FIG_REF.findall(text):
            references[normalize_number(number)] += 1
    for number, count in definitions.items():
        if count > 1:
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "figure",
                           "severity": "warning", "where": "figure/table %s" % number,
                           "problem": "defined %d times" % count,
                           "fix": "keep one caption and renumber duplicates"})
    for number in sorted(references):
        if number not in definitions:
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "figure",
                           "severity": "warning", "where": "figure/table %s" % number,
                           "problem": "referenced but no caption/definition found",
                           "fix": "add the caption or correct the reference"})
    for number in sorted(definitions):
        if number not in references:
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "figure",
                           "severity": "warning", "where": "figure/table %s" % number,
                           "problem": "defined but never referenced in text",
                           "fix": "cite it in the body or remove it"})

    # Cross references.
    for cid, text in all_text:
        for part_a, part_b in CROSSREF.findall(text):
            number = normalize_number(part_a or part_b)
            if number and number not in heading_numbers:
                issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "crossref",
                               "severity": "warning", "where": cid,
                               "problem": "cross-reference %s has no matching heading" % number,
                               "fix": "update the section number or add the heading"})

    # Numeric citations vs. bibliography entries.
    cited = Counter()
    for _cid, text in all_text:
        body_text = "\n".join(line for line in text.splitlines()
                              if not BIB_ENTRY.match(line))
        for number in parse_citations(body_text):
            cited[number] += 1
    bib_numbers = set()
    for _cid, text in all_text:
        for number in BIB_ENTRY.findall(text):
            bib_numbers.add(int(number))
    if bib_numbers:
        for number in sorted(set(cited) - bib_numbers):
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "citation",
                           "severity": "warning", "where": "citation [%d]" % number,
                           "problem": "cited but missing from bibliography",
                           "fix": "add the reference entry or remove the citation"})
        for number in sorted(bib_numbers - set(cited)):
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "citation",
                           "severity": "warning", "where": "bibliography [%d]" % number,
                           "problem": "bibliography entry never cited",
                           "fix": "cite it or remove the entry"})

    # Terminology variants.
    for variant, canonical in term_variants.items():
        hits = [(cid, text.count(variant)) for cid, text in all_text if variant in text]
        if hits:
            total = sum(count for _cid, count in hits)
            issues.append({"id": "CI-%03d" % (len(issues) + 1), "type": "terminology",
                           "severity": "warning", "where": ", ".join(cid for cid, _ in hits[:4]),
                           "problem": "variant '%s' used %d times; canonical is '%s'" %
                                      (variant, total, canonical),
                           "fix": "replace the variant or document it in the term table"})

    stats.update({
        "chapters": len(chapters),
        "chapters_found": sum(1 for row in chapter_rows if row.get("status") != "missing"),
        "words_actual": total_words,
        "words_target": total_target,
        "figure_definitions": len(definitions),
        "figure_references": len(references),
        "heading_numbers": len(heading_numbers),
        "citations": len(cited),
        "bibliography_entries": len(bib_numbers),
        "issues": len(issues),
        "errors": sum(1 for item in issues if item["severity"] == "error"),
        "warnings": sum(1 for item in issues if item["severity"] == "warning"),
    })
    if total_target and total_words:
        stats["budget_ratio"] = round(total_words / total_target, 3)
    return {"ok": stats["errors"] == 0, "issues": issues, "chapters": chapter_rows,
            "stats": stats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--index", default=None)
    ap.add_argument("--rules", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    work = Path(args.work)
    index_path = Path(args.index) if args.index else work / "chapter-index.json"
    if not index_path.is_file():
        print(json.dumps({"ok": False, "error": "missing index: %s" % index_path},
                         ensure_ascii=False))
        return 2
    try:
        index = load_json(index_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": "cannot parse index: %s" % exc},
                         ensure_ascii=False))
        return 2
    rules = {}
    rules_path = Path(args.rules) if args.rules else work / "global-rules.json"
    if rules_path.is_file():
        try:
            rules = load_json(rules_path)
        except (OSError, json.JSONDecodeError):
            rules = {}
    try:
        report = audit(work, index, rules)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    report["index"] = str(index_path)
    report["rules"] = str(rules_path)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
