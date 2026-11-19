#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalise the case contract into an executable work order.

Usage:
    python3 workorder.py --input /input --work /app/work \
        [--mode auto|review|revise] [--json]

Reads ``/input/case.json`` and ``/input/task.md`` (not the case label alone)
and decides:

* ``mode``: ``review_only`` by default; ``revise`` only when task.md or the
  output contract explicitly asks for an edited manuscript;
* six-dimension scope D1-D6 (A1/A2 optional when charts/terms are requested);
* case_id, deliverable names, contract fields and preservation constraints.

Writes ``<work>/workorder.json``. Stdlib only, no network.
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

DIMENSIONS = [
    ("D1", "研究问题与贡献主张"),
    ("D2", "章节结构与叙事顺序"),
    ("D3", "论证链与论据对应"),
    ("D4", "段落关系与衔接"),
    ("D5", "重复与冗余"),
    ("D6", "结论边界与表述尺度"),
]
NEGATIVE_REVISION = (
    "不需要改稿", "无需改稿", "不要改稿", "不要求修改稿件", "仅审稿",
    "只审稿", "只给建议", "review-only", "review only", "no rewrite",
    "do not rewrite", "do not edit the manuscript", "unsolicited rewrite",
)
POSITIVE_REVISION = (
    "请修改", "直接修改", "改稿", "修订稿", "修改后的稿件", "返回修改",
    "生成修订", "修改稿件", "改写成", "revise the manuscript",
    "edit the manuscript", "return a revised", "return the revised",
    "apply the edits", "apply edits", "produce a revised",
    "revised.md", "修订版本",
)
CONTRACT_REVISION_KEYS = (
    "requires_revision", "revision_required", "apply_edits", "needs_revision",
    "edit_required", "rewrite_required",
)
PATH_NAME_REVISION = ("revised", "修订", "改稿", "revision")
OFFICIAL_FIELDS = ("case_id", "deliverable", "preserved_facts",
                   "evidence_map", "change_log")


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "required",
                                         "revise", "revision"}
    return False


def _strings(value, depth=0):
    if depth > 4:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, depth + 1)


def read_task_text(input_dir):
    parts = []
    root = Path(input_dir)
    for name in ("task.md", "task_instruction.md", "instruction.md"):
        path = root / name
        if path.is_file():
            parts.append(kit.read_text_file(path))
    return "\n".join(parts)


def infer_mode(case_data, task_text, override=None):
    basis = []
    contract = case_data.get("output_contract")
    contract = contract if isinstance(contract, dict) else {}
    negative = any(phrase in task_text.lower() for phrase in NEGATIVE_REVISION)

    explicit = None
    for source, data in (("case", case_data), ("output_contract", contract)):
        for key in CONTRACT_REVISION_KEYS:
            if key in data and data.get(key) is not None:
                value = _truthy(data.get(key))
                explicit = value
                basis.append("%s.%s=%s" % (source, key, str(value).lower()))
                break
        if explicit is not None:
            break

    deliverable_names = []
    for key in ("deliverables", "deliverable", "outputs", "required_files",
                "artifacts"):
        if key in contract:
            deliverable_names.extend(_strings(contract[key]))
        if key in case_data:
            deliverable_names.extend(_strings(case_data[key]))
    revised_name_seen = any(
        any(marker in name.lower() for marker in PATH_NAME_REVISION)
        for name in deliverable_names)
    if revised_name_seen:
        basis.append("contract deliverable names a revised manuscript")
        explicit = True if explicit is None else explicit

    positive_hit = [phrase for phrase in POSITIVE_REVISION
                    if phrase in task_text.lower()]
    if positive_hit and not negative:
        basis.append("task.md: %s" % positive_hit[0])
        if explicit is None:
            explicit = True
    if negative:
        basis.append("task.md explicitly asks for review-only output")
        if not revised_name_seen and explicit is None:
            explicit = False

    if override == "revise":
        return "revise", basis + ["cli/mode override=revise"], True
    if override == "review":
        return "review_only", basis + ["cli/mode override=review"], False
    if explicit is True:
        return "revise", basis, True
    if explicit is False:
        return "review_only", basis, False
    basis.append("default review-only: no explicit revision request found")
    return "review_only", basis, False


def infer_genre(case_data, task_text):
    blob = kit.norm_label(" ".join(_strings(case_data)) + " " + task_text)
    if re.search(r"综述|文献综述|systematic\s+review|meta-analysis|scoping", blob):
        return "review_article"
    if re.search(r"理论|模型推导|概念分析|theoretical|conceptual", blob):
        return "theoretical_article"
    if re.search(r"实证|实验|调查|问卷|数据|样本|empirical|experiment|survey",
                 blob):
        return "empirical_article"
    return "general_article"


def expected_sections(genre):
    if genre == "empirical_article":
        return ["introduction", "methods", "results", "discussion",
                "conclusion"]
    if genre == "review_article":
        return ["introduction", "conclusion"]
    if genre == "theoretical_article":
        return ["introduction", "conclusion"]
    return ["introduction", "conclusion"]


def build(input_dir, work_dir, inventory=None, mode_override=None):
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    case_data = kit.read_json(input_dir / "case.json", default={})
    if not isinstance(case_data, dict):
        case_data = {}
    task_text = read_task_text(input_dir)
    mode, basis, revision_requested = infer_mode(
        case_data, task_text, override=mode_override)
    contract = case_data.get("output_contract")
    contract = contract if isinstance(contract, dict) else {}
    genre = infer_genre(case_data, task_text)
    language = kit.detect_language(task_text) if task_text else "unknown"

    required_fields = []
    for key in ("required_fields", "required_keys", "fields"):
        value = contract.get(key)
        if isinstance(value, list):
            required_fields.extend(str(item) for item in value)
    for field in OFFICIAL_FIELDS:
        if field not in required_fields:
            required_fields.append(field)

    audience = ""
    for key in ("audience", "target_audience", "reader"):
        value = case_data.get(key) or contract.get(key)
        if value:
            audience = str(value)
            break
    if not audience:
        if re.search(r"审稿人|reviewer", task_text):
            audience = "reviewer"
        elif re.search(r"编辑|editor", task_text):
            audience = "editor"
        else:
            audience = "author"

    case_id = (case_data.get("case_id") or contract.get("case_id")
               or (inventory or {}).get("case_id") or "unknown-case")
    order = {
        "schema": "workorder/v1",
        "case_id": str(case_id),
        "mode": mode,
        "revision_requested": bool(revision_requested),
        "mode_basis": basis,
        "genre": genre,
        "expected_sections": expected_sections(genre),
        "language": language,
        "audience": audience,
        "dimensions": [{"id": dim, "name": name, "required": True}
                       for dim, name in DIMENSIONS],
        "optional_dimensions": [
            {"id": "A1", "name": "图表—正文引用", "required": False},
            {"id": "A2", "name": "术语与指代", "required": False},
        ],
        "contract": contract,
        "contract_fields": required_fields,
        "deliverables": {
            "review": "structure-review.json",
            "result": "result.json",
            "revised": "revised.md" if mode == "revise" else None,
        },
        "preserve": {
            "numbers": True,
            "conditions": True,
            "citations": True,
            "note": "自动改稿不得新增、删除或改写数字、条件与引用",
        },
        "task_text_chars": len(task_text),
        "created_at": kit.now_iso(),
    }
    kit.write_json(work_dir / "workorder.json", order)
    return order


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--mode", choices=["auto", "review", "revise"],
                        default="auto")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    override = None if args.mode == "auto" else args.mode
    order = build(args.input, args.work, mode_override=override)
    if args.json:
        print(json.dumps(order, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({
            "case_id": order["case_id"],
            "mode": order["mode"],
            "revision_requested": order["revision_requested"],
            "genre": order["genre"],
            "mode_basis": order["mode_basis"],
            "workorder": str(Path(args.work) / "workorder.json"),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
