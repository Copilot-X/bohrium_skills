#!/usr/bin/env python3
"""Compare two editable SVGs and audit minimal-edit scope (optional).

Usage:
    python3 compare_svg.py --before start.svg --after out.svg \
        --workorder workorder.json --json
    python3 compare_svg.py --self-test --json

Workorder format (preferred):
    {"targets": ["E2"], "add": ["E6"], "remove": ["E3"],
     "source": "task.md §edit"}

A bare list ["E2", "R1"] is accepted and treated as edit targets; a plain
string (for example the workorder family name) sets no scope targets, because
it cannot identify elements.  Missing/unusable workorder input is reported as
a warning, not a crash.

The comparison is structural: it reads <g id="..."> groups and the text inside
each group.  Coordinate-only moves are NOT detected; record those in the
result JSON's edit.diff and verify visually.

Stdlib only. Exit 0 = scope preserved, 1 = scope violation, 2 = unreadable
SVG/workorder.  Errors are structured JSON, never a traceback.
"""
import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class SvgError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []

    def as_dict(self):
        return {"ok": False, "error_code": self.code, "message": self.message,
                "details": self.details}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def text_of(element):
    parts = []
    for node in element.iter():
        if node.tag.split("}")[-1] == "text" and node.text:
            parts.append(" ".join(node.text.split()))
    return " | ".join(parts)


def has_named_child_group(element):
    """Container layers (e.g. <g id="layer-nodes">) aggregate child text and
    would produce false 'modified' hits; compare leaf element groups only."""
    for child in element.iter():
        if child is element:
            continue
        if child.tag.split("}")[-1] == "g" and child.get("id"):
            return True
    return False


def inventory(path):
    p = Path(path)
    if not p.is_file():
        raise SvgError("missing_file", "SVG not found: %s" % p,
                       ["pass the real starting/output SVG path"])
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SvgError("unreadable", "cannot read SVG: %s" % exc)
    if not raw.strip():
        raise SvgError("empty_file", "SVG is empty: %s" % p)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise SvgError("xml_error", "SVG is not well-formed: %s" % exc)
    if root.tag.split("}")[-1] != "svg":
        raise SvgError("not_svg", "root element is not <svg>: %s" % p)
    items = {}
    for element in root.iter():
        if element.tag.split("}")[-1] != "g":
            continue
        eid = element.get("id")
        if eid and not has_named_child_group(element):
            items[eid] = {"text": text_of(element), "tag": "g"}
    if not items:
        raise SvgError("no_named_groups",
                       "SVG has no <g id> groups: %s" % p,
                       ["name every node/edge group, e.g. <g id=\"E1\">"])
    return items


def _strings(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value]
    return [str(value)]


def parse_workorder(value):
    targets, add, remove, warnings = set(), set(), set(), []
    if value is None:
        warnings.append("no workorder supplied: every change is out of scope")
        return targets, add, remove, warnings
    if isinstance(value, str):
        warnings.append("workorder is a single string (%r); it sets no element "
                        "targets, so every change is out of scope" % value)
        return targets, add, remove, warnings
    if isinstance(value, list):
        targets = set(_strings(value))
        warnings.append("workorder is a bare list; items were treated as edit targets")
    elif isinstance(value, dict):
        known = {"targets", "modify", "add", "remove", "source", "note", "workorder"}
        for key in value:
            if key not in known:
                warnings.append("unknown workorder key: %s" % key)
        for key in ("targets", "modify"):
            targets |= set(_strings(value.get(key)))
        add = set(_strings(value.get("add")))
        remove = set(_strings(value.get("remove")))
        if not (targets or add or remove):
            warnings.append("workorder has no targets/add/remove; every change is out of scope")
    else:
        warnings.append("workorder is neither an object nor a list; ignored")
    return targets, add, remove, warnings


def compare(before, after, workorder=None):
    targets, add_allowed, remove_allowed, warnings = parse_workorder(workorder)
    before_ids, after_ids = set(before), set(after)
    added = sorted(after_ids - before_ids)
    removed = sorted(before_ids - after_ids)
    common = before_ids & after_ids
    modified = sorted(eid for eid in common
                      if before[eid]["text"] != after[eid]["text"])
    preserved = sorted(eid for eid in common
                       if before[eid]["text"] == after[eid]["text"])
    known = before_ids | after_ids
    for target in sorted((targets | add_allowed | remove_allowed) - known):
        warnings.append("workorder target not present in either SVG: %s" % target)
    violations = []
    for eid in removed:
        if eid not in remove_allowed and eid not in targets:
            violations.append("removed without workorder: %s" % eid)
    for eid in modified:
        if eid not in targets:
            violations.append("modified without workorder: %s" % eid)
    for eid in added:
        if eid not in add_allowed and eid not in targets:
            violations.append("added without workorder: %s" % eid)
    details = {"added": added, "removed": removed, "modified": modified,
               "preserved": preserved}
    return {
        "ok": not violations,
        "added": added,
        "removed": removed,
        "modified": modified,
        "preserved": preserved,
        "unchanged_targets": sorted(eid for eid in targets if eid in preserved),
        "violations": violations,
        "warnings": warnings,
        "details": details,
        "scope_enforced": bool(targets or add_allowed or remove_allowed),
        "counts": {"before": len(before_ids), "after": len(after_ids)},
    }


def self_test():
    before = {"E1": {"text": "A"}, "E2": {"text": "B"}, "R1": {"text": "flow"}}
    after = {"E1": {"text": "A"}, "E2": {"text": "B2"}, "R1": {"text": "flow"}}
    good = compare(before, after, {"targets": ["E2"]})
    assert good["ok"] and good["modified"] == ["E2"], good
    assert good["preserved"] == ["E1", "R1"], good
    bad = compare(before, {"E1": {"text": "A"}}, {"targets": []})
    assert not bad["ok"] and "E2" in bad["removed"] + bad["modified"], bad
    empty = compare(before, after, "")
    assert not empty["scope_enforced"], empty
    try:
        inventory("definitely-missing-file.svg")
        raise AssertionError("missing file should raise")
    except SvgError as exc:
        assert exc.code == "missing_file"
    return {"ok": True, "self_test": True, "good": good, "bad": bad,
            "missing_file_error": "missing_file"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", default=None)
    parser.add_argument("--after", default=None)
    parser.add_argument("--workorder", default=None)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        report = self_test()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if not args.before or not args.after:
        print(json.dumps({"ok": False, "error_code": "missing_arguments",
                          "message": "--before and --after are required unless "
                                     "--self-test is used",
                          "details": []}, ensure_ascii=False, indent=2))
        return 2

    try:
        before = inventory(args.before)
        after = inventory(args.after)
        workorder = None
        if args.workorder:
            if not Path(args.workorder).is_file():
                raise SvgError("workorder_missing",
                               "workorder not found: %s" % args.workorder)
            try:
                workorder = load_json(args.workorder)
            except (OSError, json.JSONDecodeError) as exc:
                raise SvgError("workorder_unreadable",
                               "cannot read workorder: %s" % exc)
    except SvgError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:  # last-resort guard: never leak a traceback
        print(json.dumps({"ok": False, "error_code": "internal_error",
                          "message": "%s: %s" % (type(exc).__name__, exc),
                          "details": []}, ensure_ascii=False, indent=2))
        return 2

    report = compare(before, after, workorder)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
