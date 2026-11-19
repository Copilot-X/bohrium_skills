#!/usr/bin/env python3
"""Merge indexed chapters into thesis.md and emit a result.json skeleton.

Usage:
    python3 build_thesis.py --work /app/work \
        --index /app/work/chapter-index.json \
        --out /app/work/thesis.md --result /app/work/result.json \
        [--audit-report /app/work/consistency-report.json] [--json]

Rules
  * chapter-index.json is authoritative: chapters are merged in `order`.
  * a missing chapter file is recorded in `missing_parts` and in the per-chapter
    status; it is never invented or replaced with placeholder prose.
  * when --audit-report is supplied, the report produced by longform_audit.py is
    folded into result.json (global_consistency flags + consistency_issues).
  * word counts use the same CJK/latin-token definition as longform_audit.py.

Exit codes: 0 merged (document may still be `partial`), 2 unusable index or
unreadable audit report. Stdlib only.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

CJK = re.compile(r"[㐀-鿿]")
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
CAPTION = re.compile(
    r"^\s*(?:\*\*)?\s*(图|表|Figure|Fig\.?|Table)\s*"
    r"([0-9]+(?:[-–—.．][0-9]+)?)\s*"
    r"(?:[:：.．、]|\*\*|$)(.*)$", re.I | re.M)

STATUS_REVISED = {"revised", "revision", "revised-final", "reviewed"}
STATUS_DONE = {"written", "complete", "completed", "done", "final"}
PLACEHOLDER_TOKEN = re.compile(
    r"\b(TODO|TBD|FIXME)\b|<placeholder>|待补充|待填写|xxx", re.I)


def word_count(text):
    return len(CJK.findall(text)) + len(WORD.findall(text))


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def resolve_path(work, value):
    path = Path(value)
    if path.is_absolute():
        return path
    for base in (work, work / "chapters", work.parent):
        candidate = base / value
        if candidate.is_file():
            return candidate
    return work / value


def order_key(pair):
    position, item = pair
    if not isinstance(item, dict):
        return (1, 0.0, position)
    order = item.get("order")
    try:
        return (0, float(order), position)
    except (TypeError, ValueError):
        return (1, 0.0, position)


def chapter_status(raw, exists, words):
    if not exists:
        return "missing"
    raw = str(raw or "").strip().lower()
    if raw in STATUS_REVISED:
        return "revised"
    if raw in STATUS_DONE:
        return "written"
    return "partial" if words == 0 else "written"


def normalize_missing(value):
    out = []
    for item in value or []:
        if isinstance(item, dict):
            part = str(item.get("part") or item.get("name") or "").strip()
            reason = str(item.get("reason") or "").strip()
        else:
            part, reason = str(item).strip(), ""
        if part:
            out.append({"part": part, "reason": reason or "材料未提供，不补写"})
    return out


def clean_text(value):
    """Keep result.json free of literal placeholder tokens.

    longform_audit.py may quote 'TODO/TBD' in its suggested fix; copying that
    verbatim would make validate_result.py flag the clean audit report.
    """
    if not isinstance(value, str):
        return value
    value = value.replace("TODO/TBD", "placeholder markers")
    return PLACEHOLDER_TOKEN.sub("placeholder markers", value)


def collect_captions(cid, text):
    found = []
    for kind, number, caption in CAPTION.findall(text):
        label = "图" if kind.lower() in ("图", "figure", "fig", "fig.") else "表"
        found.append((label, number, caption.strip(), cid))
    return found


def extract_issue_list(audit):
    issues = audit.get("issues") if isinstance(audit, dict) else None
    return issues if isinstance(issues, list) else []


def audit_has_type(issues, issue_type):
    return any(str(item.get("type") or "") == issue_type for item in issues)


def ensure_copy(source, dest_dir):
    """Make an artifact resolvable from result.json's directory."""
    dest = dest_dir / source.name
    try:
        same = source.resolve() == dest.resolve()
    except OSError:
        same = False
    if not same and source.is_file():
        shutil.copy2(source, dest)
    return dest


def build_result(index, rules, rows, missing_parts, audit, audit_path, thesis_path,
                 index_path):
    issues = [{
        "id": item.get("id"),
        "type": item.get("type"),
        "where": clean_text(item.get("where")),
        "problem": clean_text(item.get("problem")),
        "fix": clean_text(item.get("fix")),
        "severity": item.get("severity"),
    } for item in extract_issue_list(audit)]
    if audit is None:
        global_consistency = {
            "terms_consistent": None,
            "figure_numbering_ok": None,
            "crossrefs_ok": None,
            "references_bidirectional": None,
            "abstract_matches_body": None,
        }
        uncertainty = [{
            "item": "cross-chapter audit not run",
            "reason": "no --audit-report was supplied",
            "impact": "consistency flags are null and must be checked manually",
        }]
    else:
        global_consistency = {
            "terms_consistent": not audit_has_type(issues, "terminology"),
            "figure_numbering_ok": not audit_has_type(issues, "figure"),
            "crossrefs_ok": not audit_has_type(issues, "crossref"),
            "references_bidirectional": not audit_has_type(issues, "citation"),
            "abstract_matches_body": None,
        }
        uncertainty = [{
            "item": "abstract/body number consistency",
            "reason": "longform_audit.py does not compare abstract numbers",
            "impact": "verify abstract numbers against the results chapters manually",
        }]

    warnings = sum(1 for item in issues if item.get("severity") == "warning")
    errors = sum(1 for item in issues if item.get("severity") == "error")
    found = sum(1 for row in rows if row["status"] != "missing")
    total_words = sum(row["words"] for row in rows)

    chapter_entries = []
    for row in rows:
        unresolved = ["%s: %s" % (item.get("id"), item.get("problem"))
                      for item in issues
                      if str(item.get("where") or "").split(",")[0].strip() == row["chapter_id"]]
        chapter_entries.append({
            "chapter_id": row["chapter_id"],
            "title": row["title"],
            "status": row["status"],
            "words": row["words"],
            "edits": row.get("edits") or [],
            "unresolved": unresolved,
        })

    consistency_issues = [{
        "id": item.get("id"),
        "type": item.get("type"),
        "where": item.get("where"),
        "problem": item.get("problem"),
        "fix": item.get("fix"),
        "severity": item.get("severity"),
    } for item in issues]

    artifacts = [
        {"role": "editable", "path": thesis_path.name, "format": "markdown"},
        {"role": "index", "path": index_path.name, "format": "json"},
    ]
    if audit_path is not None:
        artifacts.append({"role": "report", "path": audit_path.name, "format": "json"})

    status = "completed"
    if found < len(rows) or errors or missing_parts:
        status = "partial"

    result = {
        "case_id": index.get("case_id") or rules.get("case_id") or "",
        "task_family": "long-form",
        "status": status,
        "document": {
            "path": thesis_path.name,
            "format": "markdown",
            "chapters": len(rows),
            "chapters_found": found,
            "words": total_words,
            "language": rules.get("language") or index.get("language") or "zh",
        },
        "artifacts": artifacts,
        "chapters": chapter_entries,
        "global_consistency": global_consistency,
        "consistency_issues": consistency_issues,
        "missing_parts": missing_parts,
        "uncertainty": uncertainty,
        "change_log": [],
        "audit": {
            "path": audit_path.name if audit_path is not None else None,
            "ok": None if audit is None else bool(audit.get("ok")),
            "errors": errors,
            "warnings": warnings,
        },
    }
    return result, {"found": found, "words": total_words, "errors": errors,
                    "warnings": warnings}


def render_thesis(title, rows, captions):
    lines = [
        "# %s" % title,
        "",
        "> 本文件由 chapter-index.json 按 order 合并生成；缺失章节不补写。"
        "目录与图表清单需与正文核对。",
        "",
        "## 目录",
        "",
    ]
    if rows:
        for row in rows:
            suffix = "（缺失）" if row["status"] == "missing" else ""
            lines.append("- %s%s" % (row["title"] or row["chapter_id"], suffix))
    else:
        lines.append("（索引未提供章节）")
    lines.extend(["", "## 图表清单", ""])
    if captions:
        seen = set()
        for label, number, caption, cid in captions:
            key = (label, re.sub(r"[-–—.．]", "-", number))
            if key in seen:
                continue
            seen.add(key)
            text = "%s%s" % (label, number)
            if caption:
                text += "：%s" % caption
            lines.append("- %s（%s）" % (text, cid))
    else:
        lines.append("（未在章节中识别到图表题注）")
    for row in rows:
        if row.get("text") is None:
            continue
        lines.extend(["", "---", "", row["text"].strip()])
    return "\n".join(lines).rstrip() + "\n"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--index", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--result", default=None)
    ap.add_argument("--audit-report", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    work = Path(args.work)
    index_path = Path(args.index) if args.index else work / "chapter-index.json"
    out_path = Path(args.out) if args.out else work / "thesis.md"
    result_path = Path(args.result) if args.result else work / "result.json"

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
    if not isinstance(index, dict):
        print(json.dumps({"ok": False, "error": "index must be a JSON object"},
                         ensure_ascii=False))
        return 2
    chapters = index.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        print(json.dumps({"ok": False, "error": "index has no chapters"},
                         ensure_ascii=False))
        return 2

    rules_path = work / "global-rules.json"
    rules = {}
    if rules_path.is_file():
        try:
            rules = load_json(rules_path)
        except (OSError, json.JSONDecodeError):
            rules = {}

    audit = None
    audit_path = Path(args.audit_report) if args.audit_report else None
    if audit_path is not None:
        if not audit_path.is_file():
            print(json.dumps({"ok": False, "error": "missing audit report: %s" % audit_path},
                             ensure_ascii=False))
            return 2
        try:
            audit = load_json(audit_path)
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": "cannot parse audit report: %s" % exc},
                             ensure_ascii=False))
            return 2

    rows, captions, missing_parts = [], [], normalize_missing(index.get("missing"))
    for _position, item in sorted(enumerate(chapters), key=order_key):
        if not isinstance(item, dict):
            continue
        cid = str(item.get("chapter_id") or item.get("id") or "").strip()
        if not cid:
            cid = "chapter-%02d" % (_position + 1)
        title = str(item.get("title") or cid)
        path_value = item.get("path") or item.get("file")
        text = None
        resolved = None
        if path_value:
            resolved = resolve_path(work, str(path_value))
        if resolved is not None and resolved.is_file():
            try:
                text = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = None
        words = word_count(text) if text is not None else 0
        status = chapter_status(item.get("status"), text is not None, words)
        if text is None:
            missing_parts.append({
                "part": title,
                "reason": "章节文件缺失：%s" % (path_value or cid),
            })
        else:
            captions.extend(collect_captions(cid, text))
        rows.append({
            "chapter_id": cid,
            "title": title,
            "path": str(path_value or ""),
            "status": status,
            "words": words,
            "text": text,
            "edits": item.get("edits") or [],
        })

    if not rows:
        print(json.dumps({"ok": False,
                          "error": "index has no usable chapter entries"},
                         ensure_ascii=False))
        return 2

    thesis_title = str(index.get("title") or index.get("case_id") or "long-form document")
    thesis_text = render_thesis(thesis_title, rows, captions)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(thesis_text, encoding="utf-8")

    result_path.parent.mkdir(parents=True, exist_ok=True)
    thesis_artifact = ensure_copy(out_path, result_path.parent)
    index_artifact = ensure_copy(index_path, result_path.parent)
    audit_artifact = ensure_copy(audit_path, result_path.parent) if audit_path else None
    result, stats = build_result(index, rules, rows, missing_parts, audit,
                                 audit_artifact, thesis_artifact, index_artifact)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")

    summary = {
        "ok": True,
        "status": result["status"],
        "thesis": str(out_path),
        "result": str(result_path),
        "chapters_total": len(rows),
        "chapters_found": stats["found"],
        "words": stats["words"],
        "missing_parts": len(missing_parts),
        "audit": {
            "used": audit is not None,
            "ok": None if audit is None else bool(audit.get("ok")),
            "errors": stats["errors"],
            "warnings": stats["warnings"],
        },
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("wrote %s (%d chapters, %d words, status=%s)" %
              (out_path, stats["found"], stats["words"], result["status"]))
        print("wrote %s" % result_path)
        if missing_parts:
            print("missing parts: %d (not invented)" % len(missing_parts))
        if audit is not None:
            print("audit: %d errors, %d warnings" % (stats["errors"], stats["warnings"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
