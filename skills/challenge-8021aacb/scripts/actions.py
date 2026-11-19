#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn findings into executable edits and (only when required) revised.md.

Usage:
    python3 actions.py --work /app/work --output /app/output \
        [--review PATH] [--json]

Review-only (default): every finding gets ``apply: false`` and the change log
records that the manuscript was not touched.

Revise mode (task.md / output_contract explicitly asks for an edited
manuscript): findings with ``apply: true`` are applied. Supported edit types
are rewrite / narrow / relabel / insert / add_transition / delete / move /
merge / split. Reorder is reported as a manual action because whole-chapter
blocks are not moved automatically.

Safety: automatic edits never add, drop or rewrite numbers, units or citation
keys; edits that would violate this are skipped and logged. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import structure_kit as kit  # noqa: E402

CONTENT_EDITS = {"rewrite", "narrow", "relabel"}
INSERT_EDITS = {"insert", "add_transition"}
STRUCTURAL_EDITS = {"move", "merge", "split", "reorder", "delete"}
SUPPORTED_EDITS = CONTENT_EDITS | INSERT_EDITS | {"delete", "move", "merge",
                                                  "split"}


def load_json(path, default=None):
    return kit.read_json(path, default=default)


def _paragraph_order(map_data):
    """Return [(paragraph_id, original_text, current_text)] in document order."""
    ordered = []
    for chapter in map_data.get("chapters") or []:
        for paragraph in chapter.get("paragraphs") or []:
            if paragraph.get("paragraph_id"):
                text = paragraph.get("text") or ""
                ordered.append((paragraph["paragraph_id"], text, text))
        for section in chapter.get("sections") or []:
            for paragraph in section.get("paragraphs") or []:
                if paragraph.get("paragraph_id"):
                    text = paragraph.get("text") or ""
                    ordered.append((paragraph["paragraph_id"], text, text))
    return ordered


def _locate_paragraph(ordered, pid, before, occurrence=None):
    for index, (candidate, _, text) in enumerate(ordered):
        if candidate == pid:
            if before in text:
                return index
            break
    matches = [index for index, (_, _, text) in enumerate(ordered)
               if before and before in text]
    if not matches:
        return None
    if occurrence == "last":
        return matches[-1]
    if occurrence == "first":
        return matches[0]
    if len(matches) == 1:
        return matches[0]
    return None


def _join_sentences(sentences, text):
    separator = " " if kit.detect_language(text) == "en" else ""
    return separator.join(sentences)


def _split_text(text):
    sentences = kit.split_sentences(text)
    if len(sentences) < 4:
        return None
    middle = max(1, len(sentences) // 2)
    head = _join_sentences(sentences[:middle], text)
    tail = _join_sentences(sentences[middle:], text)
    return head + "\n\n" + tail


def _check_edit_guard(finding):
    """Return None when the edit is safe, else a reason string."""
    edit = finding.get("edit") or {}
    edit_type = edit.get("type")
    before = edit.get("before") or ""
    after = edit.get("after") or ""
    if edit_type not in SUPPORTED_EDITS:
        return "edit.type=%s 不是可自动执行的类型" % edit_type
    if not before:
        return "edit.before 为空，无法定位原文"
    if edit_type in (CONTENT_EDITS | INSERT_EDITS):
        if not after:
            return "%s 缺少 edit.after" % edit_type
    if edit_type in CONTENT_EDITS:
        before_tokens = kit.guard_tokens(before)
        after_tokens = kit.guard_tokens(after)
        if before_tokens != after_tokens:
            return ("数字/引用保护：before=%s, after=%s"
                    % (sorted(before_tokens), sorted(after_tokens)))
        missing_conditions = (kit.condition_tokens(before)
                              - kit.condition_tokens(after))
        if missing_conditions:
            return ("条件保护：改写后缺少 %s"
                    % ", ".join(sorted(missing_conditions)))
    if edit_type in INSERT_EDITS:
        if kit.guard_tokens(after):
            return "新增文字不得引入数字、单位或引用"
    if edit_type == "delete":
        if kit.guard_tokens(before):
            return "待删除句包含数字/引用，删除会丢失事实锚点"
    return None


def _apply_one(ordered, finding):
    """Apply one finding to the paragraph list; returns (ok, reason, diff)."""
    edit = finding.get("edit") or {}
    edit_type = edit.get("type")
    before = edit.get("before") or ""
    after = edit.get("after") or ""
    target = edit.get("target") or ""
    occurrence = edit.get("occurrence")
    pid_hint = finding.get("location", {}).get("paragraph_id", "")
    index = _locate_paragraph(ordered, pid_hint, before,
                              occurrence=occurrence)
    if index is None and edit_type in INSERT_EDITS and pid_hint:
        # A previous edit may already have rewritten the anchor sentence.
        # For insert/add_transition the paragraph id is enough: append to the
        # paragraph end or prepend to its start.
        index = next((position for position, (candidate, _, _)
                      in enumerate(ordered) if candidate == pid_hint), None)
        if index is not None:
            pid, original, text = ordered[index]
            separator = " " if kit.detect_language(text) == "en" else ""
            if edit_type == "insert":
                new_text = (text.rstrip() + separator + after).strip()
            else:
                new_text = (after + separator + text.lstrip()).strip()
            ordered[index] = (pid, original, new_text)
            return True, "", "- [anchor rewritten earlier]\n+ %s" % (
                kit.truncate(after, 220))
    if index is None:
        return False, "找不到唯一的原文段落/句子（可能需要人工定位）", ""

    pid, original, text = ordered[index]
    if edit_type in CONTENT_EDITS:
        new_text = text.replace(before, after, 1)
        if new_text == text:
            return False, "edit.before 不在目标段落中", ""
        ordered[index] = (pid, original, new_text)
        return True, "", "- %s\n+ %s" % (kit.truncate(before, 220),
                                          kit.truncate(after, 220))

    if edit_type == "insert":
        if before not in text:
            return False, "插入锚点不在目标段落中", ""
        new_text = text.replace(before, before + " " + after, 1)
        ordered[index] = (pid, original, new_text)
        return True, "", "- %s\n+ %s %s" % (
            kit.truncate(before, 180), kit.truncate(before, 180),
            kit.truncate(after, 180))

    if edit_type == "add_transition":
        if before not in text:
            return False, "过渡句锚点不在目标段落中", ""
        new_text = text.replace(before, after + " " + before, 1)
        ordered[index] = (pid, original, new_text)
        return True, "", "- %s\n+ %s %s" % (
            kit.truncate(before, 180), kit.truncate(after, 180),
            kit.truncate(before, 180))

    if edit_type == "delete":
        new_text = text.replace(before, "", 1)
        if new_text == text:
            return False, "edit.before 不在目标段落中", ""
        ordered[index] = (pid, original, kit.norm_text(new_text))
        return True, "", "- %s\n+ [deleted]" % kit.truncate(before, 240)

    if edit_type == "move":
        target_index = None
        for position, (candidate, _, _) in enumerate(ordered):
            if candidate == target:
                target_index = position
                break
        if target_index is None:
            return False, "move.target=%s 不在段落索引中" % target, ""
        if target_index == index:
            return False, "move.target 与来源段落相同", ""
        if before not in text:
            return False, "待移动句不在来源段落中", ""
        source_text = text.replace(before, "", 1).strip()
        target_pid, target_original, target_text = ordered[target_index]
        separator = " " if kit.detect_language(target_text) == "en" else ""
        ordered[index] = (pid, original, kit.norm_text(source_text))
        ordered[target_index] = (
            target_pid, target_original,
            (target_text.rstrip() + separator + before).strip())
        return True, "", "- %s\n+ [moved to %s]" % (
            kit.truncate(before, 240), target)

    if edit_type == "merge":
        target_index = None
        for position, (candidate, _, _) in enumerate(ordered):
            if candidate == target:
                target_index = position
                break
        if target_index is None or target_index == index:
            return False, "merge.target 无效", ""
        target_pid, target_original, target_text = ordered[target_index]
        merged = (target_text.rstrip() + " " + text.strip()).strip()
        ordered[target_index] = (target_pid, target_original, merged)
        ordered[index] = (pid, original, "")
        return True, "", "- %s\n+ [merged into %s]" % (
            kit.truncate(before or text, 220), target)

    if edit_type == "split":
        new_text = _split_text(text)
        if new_text is None:
            return False, "段落句数不足，无法确定拆分点", ""
        ordered[index] = (pid, original, new_text)
        return True, "", "- %s\n+ [split into two paragraphs]" % (
            kit.truncate(text, 220))

    return False, "未支持的 edit.type=%s" % edit_type, ""


def _reassemble(original_text, ordered):
    """Replace each mapped paragraph with its edited text, in document order."""
    pieces = []
    cursor = 0
    for _, original, current in ordered:
        position = original_text.find(original, cursor)
        if position < 0:
            return None
        pieces.append(original_text[cursor:position])
        pieces.append(current)
        cursor = position + len(original)
    pieces.append(original_text[cursor:])
    return "".join(pieces)


def process(review, workorder, manuscript_text, map_data, output_dir,
            work_dir):
    output_dir = Path(output_dir)
    work_dir = Path(work_dir)
    mode = workorder.get("mode")
    findings = review.get("findings") or []
    report = {
        "mode": mode,
        "requested": 0,
        "applied": 0,
        "skipped": 0,
        "reasons": {},
        "revised_written": False,
    }

    if mode != "revise":
        for finding in findings:
            finding["apply"] = False
        review["mode"] = "review_only"
        review["change_log"] = [{
            "finding_id": "none",
            "action": "未改动原稿；仅提交审稿与编辑方案",
            "diff": "无（review-only）",
            "applied": False,
        }]
        review["revision_stats"] = report
        return review, None, report

    ordered = _paragraph_order(map_data)
    requested = [finding for finding in findings
                 if finding.get("apply") is True]
    report["requested"] = len(requested)
    change_log = []
    applied_ids = set()
    for finding in findings:
        finding_id = finding.get("finding_id") or "unknown"
        if finding.get("apply") is not True:
            reason = finding.get("apply_reason") or (
                "未请求自动应用；保留为作者决策的编辑方案")
            change_log.append({
                "finding_id": finding_id,
                "action": "未自动应用：%s" % reason,
                "diff": "无",
                "applied": False,
            })
            continue
        reason = _check_edit_guard(finding)
        if reason:
            finding["apply"] = False
            finding["apply_reason"] = reason
            report["skipped"] += 1
            report["reasons"][finding_id] = reason
            change_log.append({
                "finding_id": finding_id,
                "action": "未自动应用：%s" % reason,
                "diff": "无",
                "applied": False,
            })
            continue
        ok, reason, diff = _apply_one(ordered, finding)
        if not ok:
            finding["apply"] = False
            finding["apply_reason"] = reason
            report["skipped"] += 1
            report["reasons"][finding_id] = reason
            change_log.append({
                "finding_id": finding_id,
                "action": "未自动应用：%s" % reason,
                "diff": "无",
                "applied": False,
            })
            continue
        finding["apply"] = True
        applied_ids.add(finding_id)
        report["applied"] += 1
        change_log.append({
            "finding_id": finding_id,
            "action": "已应用 %s 编辑：%s" % (
                (finding.get("edit") or {}).get("type"),
                kit.truncate(finding.get("suggestion") or "", 180)),
            "diff": diff,
            "locator": "/".join(str(value) for value in (
                finding.get("location", {}).get("chapter"),
                finding.get("location", {}).get("section"),
                finding.get("location", {}).get("paragraph_id")) if value),
            "applied": True,
        })

    revised = _reassemble(manuscript_text, ordered)
    if revised is None:
        revised = manuscript_text
        for finding in findings:
            if finding.get("finding_id") in applied_ids:
                finding["apply"] = False
                finding["apply_reason"] = "无法在原文中稳定重新定位段落，已回滚"
                report["applied"] -= 1
                report["skipped"] += 1
                report["reasons"][finding["finding_id"]] = \
                    "无法在原文中稳定重新定位段落，已回滚"
        for entry in change_log:
            if entry.get("applied"):
                entry["applied"] = False
                entry["action"] = "未自动应用：段落重定位失败，已回滚"
                entry["diff"] = "无"
        applied_ids = set()

    original_tokens = kit.guard_tokens(manuscript_text)
    revised_tokens = kit.guard_tokens(revised)
    if original_tokens != revised_tokens:
        # Roll back everything: never let an automatic edit change facts.
        revised = manuscript_text
        for finding in findings:
            if finding.get("finding_id") in applied_ids:
                finding["apply"] = False
                finding["apply_reason"] = "数字/引用保护检查未通过，已回滚"
            report["reasons"][finding.get("finding_id")] = \
                "global_guard_failed"
        for entry in change_log:
            if entry.get("applied"):
                entry["applied"] = False
                entry["action"] = "未自动应用：数字/引用保护检查未通过，已回滚"
                entry["diff"] = "无"
        report["applied"] = 0
        report["skipped"] = len(requested)
        applied_ids = set()

    revised_path = None
    if applied_ids:
        revised_path = output_dir / "revised.md"
        kit.write_text(revised_path, revised.rstrip() + "\n")
        report["revised_written"] = True
    else:
        report["reasons"].setdefault(
            "global", "no safe auto edit was applied; manual editing needed")

    if not change_log:
        change_log = [{
            "finding_id": "none",
            "action": "任务要求改稿，但没有可自动应用的 finding",
            "diff": "无",
            "applied": False,
        }]
    review["mode"] = "revise"
    review["change_log"] = change_log
    review["revision_stats"] = report
    if not applied_ids:
        review.setdefault("limitations", []).append(
            "任务要求改稿，但没有 finding 通过自动编辑保护；revised.md 未生成，需按编辑方案人工修改。")
    kit.write_json(work_dir / "apply-report.json", report)
    return review, (revised if applied_ids else None), report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--review", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    work_dir = Path(args.work)
    review_path = Path(args.review) if args.review else (
        work_dir / "diagnosis.json")
    review = load_json(review_path)
    workorder = load_json(work_dir / "workorder.json", default={})
    inventory = load_json(work_dir / "inventory.json", default={})
    map_data = load_json(work_dir / "structure-map.json", default={})
    if not review or not workorder or not inventory.get("manuscript"):
        print("ERROR: diagnosis/workorder/inventory missing", file=sys.stderr)
        return 2
    manuscript_text = kit.read_text_file(
        work_dir / (inventory["manuscript"].get("text_path")
                    or "manuscript.txt"))
    review, _, report = process(review, workorder, manuscript_text, map_data,
                                args.output, work_dir)
    kit.write_json(work_dir / "diagnosis-applied.json", review)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({
            "mode": report["mode"],
            "requested": report["requested"],
            "applied": report["applied"],
            "skipped": report["skipped"],
            "revised_written": report["revised_written"],
            "review": str(work_dir / "diagnosis-applied.json"),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
