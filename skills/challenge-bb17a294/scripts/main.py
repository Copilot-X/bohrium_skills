#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entry point for the academic translation / bilingual writing skill.

Usage:
    python3 main.py prepare  --input /input --work /app/work --output /app/output
    #   ... write the complete /app/output/translation.md and bilingual.md ...
    python3 main.py finalize --work /app/work --output /app/output
    python3 main.py run      --input /input --work /app/work --output /app/output

``prepare`` reads case.json/task.md, finds the real source document, segments it
and writes glossary candidates plus a bilingual working scaffold.  It never
invents a translation.  ``finalize`` reads the agent's translation.md /
bilingual.md, audits fidelity, writes glossary.json and translation-check.json,
then writes result.json with the official writing aliases
``deliverable`` / ``preserved_facts`` / ``evidence_map`` / ``change_log``.
``run`` does both, degrading honestly when the translation is missing.

Translation-first rule: translation.md and bilingual.md are written before any
glossary/result assembly.  Audit findings never block delivery; they are
recorded in result.uncertainty and translation-check.json.

Stdlib only, no network.  Exit code 0 means the requested stage wrote its
files (even when the audit reports findings).
"""

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".tex", ".rst"}
UNSUPPORTED_SUFFIXES = {".pdf", ".docx", ".doc", ".odt"}
EXCLUDED_NAMES = {
    "case.json", "task.md", "output-contract.md", "example-input.json",
    "public-manifest.json", "manifest.json", "readme.md", "skill.md",
    "license", "license.md", "changelog.md",
}


def _load_kit():
    from importlib.machinery import SourceFileLoader
    for name in ("kit.py", "kit.py.md"):
        path = HERE / name
        if path.is_file():
            loader = SourceFileLoader("translation_kit", str(path))
            spec = importlib.util.spec_from_loader("translation_kit", loader)
            module = importlib.util.module_from_spec(spec)
            loader.exec_module(module)
            return module
    raise SystemExit("kit.py not found next to main.py")


KIT = _load_kit()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return default


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write_json(path, payload):
    return KIT.write_json(path, payload)


def one_line(text):
    return re.sub(r"\s+", " ", (text or "").strip())


# ---------------------------------------------------------------------------
# input discovery
# ---------------------------------------------------------------------------

def _case_paths(value, key=""):
    """Yield path-like values from the case.json source/material fields."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return
        suffix = Path(text).suffix.lower()
        if (suffix in TEXT_SUFFIXES or suffix in UNSUPPORTED_SUFFIXES
                or "/" in text or "\\" in text):
            yield text
    elif isinstance(value, dict):
        for child_key, child in value.items():
            lowered = str(child_key).lower()
            if lowered in ("path", "file", "filename", "source_path",
                           "file_path"):
                if isinstance(child, str):
                    yield child
            elif lowered in ("source", "sources", "materials", "files",
                             "input_files", "document", "manuscript", "paper",
                             "attachments", "data", "content"):
                yield from _case_paths(child, lowered)
    elif isinstance(value, list):
        for item in value:
            yield from _case_paths(item, key)


def _candidate_score(rel, text):
    score = 0
    low = rel.lower()
    for hint in ("source", "manuscript", "paper", "abstract", "material",
                 "document", "正文", "原文", "论文", "摘要", "材料"):
        if hint in low:
            score += 4
            break
    if low.endswith(".md"):
        score += 2
    if len(text) >= 200:
        score += 2
    if KIT.cjk_ratio(text) > 0.05:
        score += 1
    return score


def discover_source(input_dir, case, explicit=None):
    """Return (choice, candidates, unsupported, skipped, notes)."""
    input_dir = Path(input_dir)
    notes = []
    candidates = []
    unsupported = []
    skipped = []
    seen = set()

    explicit_values = []
    if explicit:
        explicit_values.append(str(explicit))
    for key in ("source_path", "source_file", "source_files", "input_files",
                "materials", "files", "document", "manuscript", "paper",
                "attachments", "source", "sources"):
        if isinstance(case, dict) and key in case:
            explicit_values.extend(_case_paths(case[key], key))

    for raw in explicit_values:
        path = Path(raw)
        if not path.is_absolute():
            path = input_dir / path
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        if not path.is_file():
            notes.append("declared source is missing: %s" % raw)
            continue
        suffix = path.suffix.lower()
        rel = path.name
        if suffix in UNSUPPORTED_SUFFIXES:
            unsupported.append({"path": str(path), "reason": "binary material; "
                                "extract it with the runtime PDF/DOCX tool first"})
            continue
        if suffix not in TEXT_SUFFIXES:
            skipped.append({"path": str(path), "reason": "unsupported suffix"})
            continue
        text = read_text(path)
        if not text.strip():
            skipped.append({"path": str(path), "reason": "empty or not UTF-8"})
            continue
        candidates.append({"path": str(path), "rel": rel, "text": text,
                           "score": 100, "declared": True})

    if not candidates:
        for path in sorted(input_dir.rglob("*")):
            if not path.is_file():
                continue
            name = path.name.lower()
            rel = path.relative_to(input_dir).as_posix()
            if name in EXCLUDED_NAMES:
                skipped.append({"path": rel, "reason": "task metadata"})
                continue
            if name.startswith("expected") or "expected-" in name \
                    or "/expected/" in "/" + rel.lower():
                skipped.append({"path": rel, "reason": "expected answer file"})
                continue
            if path.suffix.lower() in UNSUPPORTED_SUFFIXES:
                unsupported.append({"path": rel, "reason": "binary material; "
                                    "extract it with the runtime PDF/DOCX tool first"})
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = read_text(path)
            if not text.strip():
                skipped.append({"path": rel, "reason": "empty or not UTF-8"})
                continue
            candidates.append({"path": str(path), "rel": rel, "text": text,
                               "score": _candidate_score(rel, text),
                               "declared": False})

    candidates.sort(key=lambda item: (-item["score"], -len(item["text"]),
                                      item["rel"]))
    choice = candidates[0] if candidates else None
    if choice is None:
        notes.append("no readable UTF-8 text source found under %s" % input_dir)
    elif len(candidates) > 1:
        notes.append("multiple readable candidates; chose %s (others: %s)"
                     % (choice["rel"],
                        ", ".join(item["rel"] for item in candidates[1:5])))
    return choice, candidates, unsupported, skipped, notes


# ---------------------------------------------------------------------------
# direction / work order inference
# ---------------------------------------------------------------------------

def _language(value):
    text = json.dumps(value, ensure_ascii=False).lower() \
        if not isinstance(value, str) else value.lower()
    if any(token in text for token in ("zh", "chinese", "中文", "汉语", "简体")):
        return "zh"
    if any(token in text for token in ("en", "english", "英文", "英语")):
        return "en"
    return None


def infer_direction(case, task_text, source_text, explicit=None):
    if explicit in ("zh->en", "en->zh"):
        return explicit, "explicit --direction"
    case = case if isinstance(case, dict) else {}
    for key in ("direction", "translation_direction", "language_direction"):
        value = case.get(key)
        if isinstance(value, str):
            norm = value.lower().replace(" ", "").replace("→", "->").replace(">", "->")
            if "zh" in norm and "en" in norm:
                first_zh = norm.find("zh")
                first_en = norm.find("en")
                return ("zh->en" if first_zh < first_en else "en->zh",
                        "case.%s" % key)
    source_lang = None
    target_lang = None
    for key in ("source_language", "source_lang", "sourceLanguage",
                "language_from"):
        if key in case:
            source_lang = _language(case[key])
            if source_lang:
                break
    for key in ("target_language", "target_lang", "targetLanguage",
                "language_to"):
        if key in case:
            target_lang = _language(case[key])
            if target_lang:
                break
    if source_lang and target_lang and source_lang != target_lang:
        return "%s->%s" % (source_lang, target_lang), "case language fields"
    task_text = task_text or ""
    zh_to_en = re.search(
        r"中(?:译|翻)英|中文.{0,24}(?:译|翻译).{0,24}英文|"
        r"zh\s*(?:->|→|to)\s*en|chinese\s+to\s+english|"
        r"translat\w*.{0,80}into\s+english", task_text, re.I | re.S)
    en_to_zh = re.search(
        r"英(?:译|翻)中|英文.{0,24}(?:译|翻译).{0,24}中文|"
        r"en\s*(?:->|→|to)\s*zh|english\s+to\s+chinese|"
        r"translat\w*.{0,80}into\s+chinese", task_text, re.I | re.S)
    if zh_to_en and en_to_zh:
        if zh_to_en.start() <= en_to_zh.start():
            return "zh->en", "task.md instruction"
        return "en->zh", "task.md instruction"
    if zh_to_en:
        return "zh->en", "task.md instruction"
    if en_to_zh:
        return "en->zh", "task.md instruction"
    if source_lang and target_lang and source_lang != target_lang:
        return "%s->%s" % (source_lang, target_lang), "case language fields"
    if source_lang:
        other = "en" if source_lang == "zh" else "zh"
        return "%s->%s" % (source_lang, other), "case source language"
    if KIT.cjk_ratio(source_text or "") > 0.10:
        return "zh->en", "source text script"
    return "en->zh", "source text script"


GENRE_RULES = [
    ("abstract", r"摘要|abstract"),
    ("figure_caption", r"图注|表注|图注翻译|caption|figure legend"),
    ("response_letter", r"回复信|答复信|response to (?:the )?reviewers|rebuttal|"
                        r"point-by-point"),
    ("review_report", r"审稿意见|同行评审|review report|referee report"),
    ("methods", r"方法部分|实验部分|methods section|materials and methods"),
    ("full_paper", r"全文|正文|整篇|full (?:paper|text|manuscript)|manuscript"),
    ("contract", r"合同|协议|contract|agreement"),
]


def infer_workorder(case, task_text, source_text, direction, source_choice,
                    notes):
    case = case if isinstance(case, dict) else {}
    task = task_text or ""
    genre = "academic"
    for value, pattern in GENRE_RULES:
        if re.search(pattern, task, re.I):
            genre = value
            break
    output_mode = "translation"
    if re.search(r"双语|对照|bilingual|side-by-side", task, re.I):
        output_mode = "bilingual"
    if re.search(r"润色|polish|revise the english|language edit", task, re.I):
        output_mode = "polish"
    reader = "peer researcher"
    for pattern, value in (
            (r"审稿人|reviewer", "reviewer"),
            (r"编辑|editor", "editor"),
            (r"公众|public|general audience", "public"),
            (r"跨学科|cross-disciplinary", "cross-disciplinary reader")):
        if re.search(pattern, task, re.I):
            reader = value
            break
    variant = "en-US"
    if re.search(r"英式|british|en-GB|uk english", task, re.I):
        variant = "en-GB"
    elif re.search(r"美式|american|en-US|us english", task, re.I):
        variant = "en-US"
    constraints = []
    for match in re.finditer(r"(\d+)\s*(?:字|词|words?)", task, re.I):
        constraints.append({"kind": "length", "value": int(match.group(1)),
                            "text": match.group(0)})
    contract = case.get("output_contract")
    contract_fields = list(contract) if isinstance(contract, dict) else []
    source_meta = case.get("source") if isinstance(case.get("source"), dict) else {}
    title = source_meta.get("title") if isinstance(source_meta, dict) else None
    if not title and source_choice:
        title = Path(source_choice["rel"]).stem
    return {
        "case_id": case.get("case_id") or case.get("id") or "unknown-case",
        "direction": direction,
        "genre": genre,
        "output_mode": output_mode,
        "audience": reader,
        "english_variant": variant,
        "deliverable_name": "translation.md",
        "bilingual_name": "bilingual.md",
        "constraints": constraints,
        "contract_fields": contract_fields,
        "source_title": title,
        "source_language": direction.split("->")[0],
        "target_language": direction.split("->")[-1],
        "task_excerpt": one_line(task)[:800],
        "task_present": bool(task.strip()),
        "source_present": bool(source_choice),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# provisional result (prepare/input_error must still leave result.json)
# ---------------------------------------------------------------------------

def write_provisional_result(case, case_id, direction, segments, status,
                             output_dir, note):
    """Write an honest provisional result.json when finalize has not run.

    ``prepare`` and any degraded path must leave a parseable result with the
    official four fields and exit 0.  A later ``finalize`` overwrites it with
    the assembled result; an existing result is never clobbered here.
    """
    result_path = Path(output_dir) / "result.json"
    if result_path.is_file():
        return None
    glossary = KIT.normalize_glossary({"register": KIT.default_register()})
    glossary["case_id"] = case_id
    glossary["direction"] = direction
    result = KIT.build_result(
        case_id, direction, segments, {}, glossary,
        KIT.collect_artifacts(output_dir), status=status, untranslated=[],
        uncertainty=[note], change_log=[{
            "action": "provisional_result",
            "scope": "result.json",
            "reason": note,
            "human_review": False,
        }], audit_report=None, translation_text="", deliverable_path=None,
        case=case, back_translation_check=[])
    result["path_check"] = {
        "ok": False,
        "problems": ["provisional: deliverable pending until finalize"],
    }
    KIT.write_json(result_path, result)
    return result_path


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def prepare(args):
    input_dir = Path(args.input)
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = now_iso()

    case = load_json(input_dir / "case.json", {}) or {}
    task_text = read_text(input_dir / "task.md")
    notes = []
    choice, candidates, unsupported, skipped, discover_notes = discover_source(
        input_dir, case, explicit=getattr(args, "source", None))
    notes.extend(discover_notes)
    source_text = choice["text"] if choice else ""
    direction, direction_source = infer_direction(
        case, task_text, source_text,
        explicit=getattr(args, "direction", None))
    workorder = infer_workorder(case, task_text, source_text, direction,
                                choice, notes)
    workorder["direction_source"] = direction_source
    workorder["input_dir"] = str(input_dir)
    workorder["other_candidates"] = [
        {"path": item["rel"], "score": item["score"],
         "declared": item["declared"]}
        for item in candidates[1:10]
    ]
    workorder["unsupported"] = unsupported
    workorder["skipped"] = skipped[:30]

    segments = KIT.build_segments(source_text) if source_text.strip() else []
    candidates_terms = KIT.candidate_terms(segments) if segments else []
    status = "ready" if segments else "input_error"
    workorder["status"] = status
    uncertainty = []
    if status == "input_error":
        uncertainty.append(
            "input_error: no readable UTF-8 source text; provide --source or "
            "extract PDF/DOCX with the runtime tool, then re-run prepare")
    if choice is None and unsupported:
        uncertainty.append(
            "binary source(s) present (%s); extraction is required before translation"
            % ", ".join(item["path"] for item in unsupported[:5]))

    write_json(work_dir / "case.json", case)
    write_json(work_dir / "workorder.json", workorder)
    write_json(work_dir / "source.json", {
        "path": choice["path"] if choice else None,
        "relative_path": choice["rel"] if choice else None,
        "sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        if source_text else None,
        "chars": len(source_text),
        "cjk_ratio": round(KIT.cjk_ratio(source_text), 4),
        "candidates": [{"path": item["rel"], "score": item["score"],
                        "declared": item["declared"]} for item in candidates],
        "unsupported": unsupported,
        "skipped": skipped[:50],
        "notes": notes,
        "selected_at": started,
    })
    write_json(work_dir / "segments.json", {
        "schema_version": "1.0",
        "case_id": workorder["case_id"],
        "direction": direction,
        "source": choice["path"] if choice else None,
        "count": len(segments),
        "segments": segments,
    })
    write_json(work_dir / "glossary-candidates.json", {
        "schema_version": "1.0",
        "source": choice["path"] if choice else None,
        "count": len(candidates_terms),
        "candidates": candidates_terms,
    })
    if source_text:
        (work_dir / "source.txt").write_text(source_text, encoding="utf-8")
        source_lang, target_lang = direction.split("->")
        scaffold = ["# Bilingual Working Draft (source side)", "",
                    "<!-- Fill each Target line, then save as /app/output/bilingual.md. -->",
                    "<!-- Write /app/output/translation.md first; do not edit Source lines. -->",
                    ""]
        for seg in segments:
            scaffold.extend([
                "## %s" % seg["segment_id"], "",
                "- **Source (%s):** %s" % (source_lang,
                                           one_line(seg["source"])),
                "- **Target (%s):** " % target_lang,
                "",
            ])
        (work_dir / "bilingual-source.md").write_text(
            "\n".join(scaffold), encoding="utf-8")

    provisional_status = "input_error" if status == "input_error" else "partial"
    provisional_note = (
        "provisional result: %d source segment(s) prepared; write translation.md "
        "and bilingual.md, then run finalize" % len(segments)
        if segments else
        "provisional result: no readable source text (input_error); provide the "
        "real material or extract PDF/DOCX, then re-run prepare")
    provisional_path = write_provisional_result(
        case, workorder["case_id"], direction, segments, provisional_status,
        output_dir, provisional_note)

    summary = {
        "stage": "prepare",
        "case_id": workorder["case_id"],
        "status": status,
        "direction": direction,
        "direction_source": direction_source,
        "genre": workorder["genre"],
        "output_mode": workorder["output_mode"],
        "source": choice["path"] if choice else None,
        "source_relative": choice["rel"] if choice else None,
        "source_chars": len(source_text),
        "segments": len(segments),
        "candidates": len(candidates_terms),
        "other_candidates": len(candidates) - 1 if candidates else 0,
        "unsupported": unsupported,
        "notes": notes,
        "uncertainty": uncertainty,
        "next_step": ("write the complete translation.md and bilingual.md, then "
                      "run: python3 main.py finalize"),
        "segments_file": str(work_dir / "segments.json"),
        "workorder_file": str(work_dir / "workorder.json"),
        "bilingual_scaffold": str(work_dir / "bilingual-source.md"),
        "result": str(provisional_path or (output_dir / "result.json")),
        "result_status": provisional_status,
        "started_at": started,
        "finished_at": now_iso(),
    }
    write_json(work_dir / "prepare-summary.json", summary)
    return summary


# ---------------------------------------------------------------------------
# finalize helpers
# ---------------------------------------------------------------------------

TARGET_MARKER_RE = re.compile(r"<!--\s*(S\d+)\s*-->")


def _strip_comments(text):
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    return text


def gather_targets(segments, work_dir, output_dir, args):
    """Collect per-segment targets from translation.md, targets.json and bilingual."""
    targets = {}
    notes = []
    translation_path = None
    if getattr(args, "translation", None):
        translation_path = Path(args.translation)
    else:
        for candidate in (Path(output_dir) / "translation.md",
                          Path(work_dir) / "translation.md"):
            if candidate.is_file():
                translation_path = candidate
                break
    translation_text = read_text(translation_path) if translation_path else ""

    if translation_text.strip() and segments:
        matches = list(TARGET_MARKER_RE.finditer(translation_text))
        if matches:
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) \
                    else len(translation_text)
                body = _strip_comments(translation_text[match.end():end])
                sid = match.group(1)
                if any(seg["segment_id"] == sid for seg in segments):
                    targets[sid] = one_line(body)
            notes.append("targets_mapped_from_markers: %d" % len(targets))
        else:
            blocks = [block.strip() for block in
                      re.split(r"\n\s*\n", translation_text) if block.strip()]
            if len(blocks) == len(segments):
                targets = {seg["segment_id"]: one_line(block)
                           for seg, block in zip(segments, blocks)}
                notes.append("targets_mapped_from_blocks: %d" % len(targets))
            else:
                notes.append(
                    "translation.md has %d blocks for %d segments; "
                    "per-segment mapping unavailable"
                    % (len(blocks), len(segments)))

    targets_path = getattr(args, "targets", None)
    if targets_path:
        payload = load_json(targets_path)
        if isinstance(payload, dict):
            if isinstance(payload.get("targets"), dict):
                for sid, value in payload["targets"].items():
                    if isinstance(value, str) and value.strip():
                        targets[sid] = one_line(value)
            elif isinstance(payload.get("segments"), list):
                for item in payload["segments"]:
                    if isinstance(item, dict) and item.get("segment_id"):
                        value = item.get("target") or ""
                        if value.strip():
                            targets[item["segment_id"]] = one_line(value)
            else:
                for sid, value in payload.items():
                    if isinstance(value, str) and re.fullmatch(r"S\d+", str(sid)):
                        targets[sid] = one_line(value)
        notes.append("targets_loaded: %s" % targets_path)

    bilingual_path = None
    if getattr(args, "bilingual", None):
        bilingual_path = Path(args.bilingual)
    else:
        for candidate in (Path(output_dir) / "bilingual.md",
                          Path(work_dir) / "bilingual.md"):
            if candidate.is_file():
                bilingual_path = candidate
                break
    bilingual = None
    if bilingual_path and bilingual_path.is_file():
        bilingual = KIT.parse_bilingual(read_text(bilingual_path))
        for sid, value in bilingual["targets"].items():
            if value.strip():
                targets[sid] = value
        notes.append("bilingual_loaded: %d sections" % len(bilingual["order"]))
    return targets, translation_text, translation_path, bilingual, bilingual_path, notes


def ensure_deliverables(segments, targets, translation_text, bilingual,
                        translation_path, bilingual_path, output_dir,
                        direction, change_log, notes):
    """Write translation.md / bilingual.md from available data; never invent text."""
    output_dir = Path(output_dir)
    translation_target = output_dir / "translation.md"
    bilingual_target = output_dir / "bilingual.md"
    source_lang, target_lang = direction.split("->")

    if not translation_text.strip() and any(value.strip() for value in targets.values()):
        parts = [targets.get(seg["segment_id"], "").strip()
                 for seg in segments]
        text = "\n\n".join(part for part in parts if part)
        if text.strip():
            translation_target.write_text(text + "\n", encoding="utf-8")
            translation_text = text
            change_log.append({
                "action": "assemble_translation",
                "scope": "translation.md",
                "reason": "joined aligned targets from bilingual/targets input",
                "human_review": False,
            })
            notes.append("translation.md assembled from aligned targets")

    if not bilingual_path or not Path(bilingual_path).is_file():
        if any(value.strip() for value in targets.values()):
            text = KIT.emit_bilingual(segments, targets, source_lang, target_lang,
                                      generated=True)
            bilingual_target.write_text(text, encoding="utf-8")
            bilingual_path = bilingual_target
            change_log.append({
                "action": "emit_bilingual",
                "scope": "bilingual.md",
                "reason": "generated segment alignment from existing targets; "
                          "verify targets before delivery",
                "human_review": False,
            })
            notes.append("bilingual.md generated from targets")
    return translation_text, translation_path, bilingual, bilingual_path


def load_glossary_for_finalize(work_dir, output_dir, args, segments, targets,
                               change_log):
    explicit = getattr(args, "glossary", None)
    candidate_paths = []
    if explicit:
        candidate_paths.append(Path(explicit))
    candidate_paths.extend([Path(output_dir) / "glossary.json",
                            Path(work_dir) / "glossary.json"])
    doc = None
    source_note = None
    for path in candidate_paths:
        if path.is_file():
            payload = load_json(path)
            if isinstance(payload, dict):
                doc = payload
                source_note = "loaded: %s" % path
                break
    if doc is None:
        candidates_doc = load_json(work_dir / "glossary-candidates.json", {}) or {}
        candidates = candidates_doc.get("candidates", []) \
            if isinstance(candidates_doc, dict) else []
        doc = KIT.derive_glossary(candidates, segments, targets)
        source_note = "derived from glossary-candidates (only explicit bilingual pairs are locked)"
        change_log.append({
            "action": "derive_glossary",
            "scope": "glossary.json",
            "reason": source_note,
            "human_review": False,
        })
    glossary = KIT.normalize_glossary(doc)
    glossary["case_id"] = (load_json(work_dir / "workorder.json", {}) or {}).get("case_id")
    glossary["direction"] = (load_json(work_dir / "workorder.json", {}) or {}).get("direction")
    KIT.write_json(Path(output_dir) / "glossary.json", glossary)
    return glossary, source_note


def load_optional_list(path, key=None):
    payload = load_json(path)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and key and isinstance(payload.get(key), list):
        return payload[key]
    return []


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def finalize(args):
    work_dir = Path(args.work)
    output_dir = Path(args.output)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = now_iso()
    prepare_summary = None

    workorder = load_json(work_dir / "workorder.json")
    if not isinstance(workorder, dict):
        input_dir = Path(args.input)
        if input_dir.is_dir():
            try:
                prepare_summary = prepare(args)
                workorder = load_json(work_dir / "workorder.json", {}) or {}
            except Exception as exc:  # noqa: BLE001 - degrade honestly
                workorder = {}
                prepare_summary = {"stage": "prepare", "status": "error",
                                   "error": "%s: %s" % (type(exc).__name__, exc)}
        else:
            workorder = {}

    segments_doc = None
    if getattr(args, "segments", None):
        segments_doc = load_json(args.segments)
    if not isinstance(segments_doc, dict):
        segments_doc = load_json(work_dir / "segments.json", {}) or {}
    segments = segments_doc.get("segments", []) if isinstance(segments_doc, dict) else []
    if not isinstance(segments, list):
        segments = []

    case = load_json(work_dir / "case.json")
    if not isinstance(case, dict):
        case = load_json(Path(args.input) / "case.json", {}) or {}
    case_id = (case.get("case_id") if isinstance(case, dict) else None) \
        or workorder.get("case_id") or "unknown-case"
    direction = (getattr(args, "direction", None)
                 or workorder.get("direction")
                 or segments_doc.get("direction") or "zh->en")
    if direction not in ("zh->en", "en->zh"):
        direction = "zh->en"

    change_log = load_optional_list(work_dir / "change-log.json", "change_log")
    uncertainty = KIT._unique_strings(
        load_optional_list(work_dir / "uncertainty.json", "uncertainty"))
    if segments:
        change_log.append({
            "action": "prepare",
            "scope": "source segmentation",
            "reason": "split source into %d alignable segments" % len(segments),
            "human_review": False,
        })
    if workorder.get("status") == "input_error":
        uncertainty.append(
            "workorder_status_input_error: %s"
            % "; ".join(workorder.get("notes") or [])[:500])

    targets, translation_text, translation_path, bilingual, bilingual_path, \
        mapping_notes = gather_targets(segments, work_dir, output_dir, args)
    for note in mapping_notes:
        if "unavailable" in note:
            uncertainty.append("target_mapping: %s" % note)

    translation_text, translation_path, bilingual, bilingual_path = \
        ensure_deliverables(segments, targets, translation_text, bilingual,
                            translation_path, bilingual_path, output_dir,
                            direction, change_log, mapping_notes)
    if translation_text.strip():
        change_log.append({
            "action": "translated",
            "scope": "all segments",
            "reason": "target-language translation was written before result assembly",
            "human_review": False,
        })

    try:
        glossary, glossary_note = load_glossary_for_finalize(
            work_dir, output_dir, args, segments, targets, change_log)
    except Exception as exc:  # noqa: BLE001 - glossary is advisory, not a gate
        glossary = KIT.normalize_glossary(
            {"register": KIT.default_register()})
        glossary["case_id"] = case_id
        glossary["direction"] = direction
        glossary_note = "unavailable: %s: %s" % (type(exc).__name__, exc)
        uncertainty.append("glossary_unavailable: %s" % glossary_note)
        change_log.append({
            "action": "glossary_degrade",
            "scope": "glossary.json",
            "reason": "glossary could not be built; result.glossary is empty",
            "human_review": True,
        })
    if glossary_note and glossary_note.startswith("derived"):
        uncertainty.append(
            "glossary_derived: only explicit bilingual pairs were locked; "
            "review tentative terms before using this as a final glossary")

    audit = None
    audit_error = None
    if segments and not getattr(args, "no_audit", False):
        try:
            audit = KIT.audit_document(
                segments, targets, glossary, direction,
                bilingual=bilingual, translation_text=translation_text)
            KIT.write_json(output_dir / "translation-check.json", audit)
            change_log.append({
                "action": "audit",
                "scope": "numbers/units/citations/terms/negation/modality",
                "reason": "%d error(s), %d warning(s); audit is non-blocking"
                          % (audit["summary"]["errors"],
                             audit["summary"]["warnings"]),
                "human_review": False,
            })
        except Exception as exc:  # noqa: BLE001 - audit must never block
            audit_error = "%s: %s" % (type(exc).__name__, exc)
            KIT.write_json(output_dir / "translation-check.json", {
                "tool": "translation-kit", "version": KIT.VERSION,
                "status": "error", "error": audit_error,
                "generated_at": now_iso(),
                "summary": {"errors": None, "warnings": None},
                "computed_preservation": {},
                "findings": [],
                "limitations": ["audit crashed; delivery continues with manual checks"],
            })
            uncertainty.append("audit_unavailable: %s" % audit_error)
    elif getattr(args, "no_audit", False):
        uncertainty.append("audit_skipped: --no-audit; preservation flags stay false "
                           "unless the agent verifies them manually")

    missing = [seg["segment_id"] for seg in segments
               if not (targets.get(seg["segment_id"]) or "").strip()]
    if not segments:
        status = "input_error"
        if not uncertainty:
            uncertainty.append("input_error: no source segments were available")
    elif getattr(args, "status", None) in ("partial", "input_error"):
        status = args.status
    elif not translation_text.strip() and not any(
            value.strip() for value in targets.values()):
        status = "partial"
        uncertainty.append(
            "translation_missing: no translated text was found; write the complete "
            "translation.md and bilingual.md first, then re-run finalize")
    elif missing:
        status = "partial"
        uncertainty.append("untranslated_segments: %s" % ", ".join(missing[:20]))
    else:
        status = "completed"

    deliverable_path = None
    for name in ("translation.md", "bilingual.md"):
        path = output_dir / name
        if path.is_file() and path.stat().st_size > 0:
            deliverable_path = name
            break
    if deliverable_path is None:
        if status == "completed":
            status = "partial"
        uncertainty.append(
            "deliverable_missing: neither translation.md nor bilingual.md exists "
            "under the output directory")

    back_translation = []
    back_path = getattr(args, "back_translation", None) or \
        (work_dir / "back-translation.json")
    if Path(back_path).is_file():
        back_translation = load_optional_list(back_path, "checks")

    artifacts = KIT.collect_artifacts(output_dir)
    result = KIT.build_result(
        case_id, direction, segments, targets, glossary, artifacts,
        status=status, untranslated=[], uncertainty=uncertainty,
        change_log=change_log, audit_report=audit,
        translation_text=translation_text, deliverable_path=deliverable_path,
        case=case, back_translation_check=back_translation)
    if audit_error:
        result.setdefault("uncertainty", []).append(
            "audit_error_recorded: %s" % audit_error)
    problems = KIT.validate_result_paths(result, output_dir)
    if problems:
        result["path_check"] = {"ok": False, "problems": problems}
        result.setdefault("uncertainty", []).extend(
            "path_check: %s" % problem for problem in problems)
        if status == "completed" and any(
                "deliverable" in problem for problem in problems):
            result["status"] = "partial"
    else:
        result["path_check"] = {"ok": True, "problems": []}
    KIT.write_json(output_dir / "result.json", result)

    summary = {
        "stage": "finalize",
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "direction": direction,
        "deliverable": result.get("deliverable", {}).get("path"),
        "segments": len(segments),
        "segments_with_target": len(segments) - len(missing),
        "untranslated": [item.get("segment_id") for item in
                         result.get("untranslated") or []],
        "glossary": len(result.get("glossary") or []),
        "glossary_source": glossary_note,
        "preserved_facts": len(result.get("preserved_facts") or []),
        "evidence_map": len(result.get("evidence_map") or []),
        "change_log": len(result.get("change_log") or []),
        "audit": (audit or {}).get("summary"),
        "audit_error": audit_error,
        "uncertainty": result.get("uncertainty") or [],
        "path_check": result.get("path_check"),
        "source": (load_json(work_dir / "source.json", {}) or {}).get("path"),
        "result": str(output_dir / "result.json"),
        "prepare_run": bool(prepare_summary),
        "started_at": started,
        "finished_at": now_iso(),
    }
    KIT.write_json(work_dir / "finalize-summary.json", summary)
    return summary


def run(args):
    prepare_summary = prepare(args)
    finalize_summary = finalize(args)
    combined = {
        "stage": "run",
        "case_id": finalize_summary.get("case_id"),
        "status": finalize_summary.get("status"),
        "prepare": prepare_summary,
        "finalize": finalize_summary,
        "result": finalize_summary.get("result"),
    }
    KIT.write_json(Path(args.work) / "run-summary.json", combined)
    return combined


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "finalize", "run"):
        part = sub.add_parser(name)
        part.add_argument("--input", default="/input")
        part.add_argument("--work", default="/app/work")
        part.add_argument("--output", default="/app/output")
        part.add_argument("--source", default=None,
                          help="explicit source text file (md/txt/tex)")
        part.add_argument("--segments", default=None,
                          help="precomputed segments.json")
        part.add_argument("--translation", default=None,
                          help="agent translation.md (default: output/translation.md)")
        part.add_argument("--bilingual", default=None,
                          help="agent bilingual.md (default: output/bilingual.md)")
        part.add_argument("--targets", default=None,
                          help="optional JSON with per-segment targets")
        part.add_argument("--glossary", default=None,
                          help="optional glossary.json")
        part.add_argument("--back-translation", dest="back_translation",
                          default=None,
                          help="optional back-translation.json")
        part.add_argument("--direction", choices=["zh->en", "en->zh"],
                          default=None)
        part.add_argument("--status", choices=["completed", "partial",
                                               "input_error"], default=None)
        part.add_argument("--no-audit", dest="no_audit", action="store_true",
                          help="skip the advisory audit")
        part.add_argument("--json", action="store_true")
    return parser


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] not in ("prepare", "finalize", "run"):
        # One entry point: bare ``python3 main.py`` behaves like ``run``.
        argv = ["run"] + argv
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            payload = prepare(args)
        elif args.command == "finalize":
            payload = finalize(args)
        else:
            payload = run(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001 - write an honest degradation record
        failure = {
            "stage": getattr(args, "command", "unknown"),
            "status": "error",
            "error": "%s: %s" % (type(exc).__name__, exc),
            "traceback": traceback.format_exc(limit=3),
            "generated_at": now_iso(),
            "work": getattr(args, "work", None),
            "output": getattr(args, "output", None),
        }
        try:
            failure_path = Path(getattr(args, "work", "/app/work")) / \
                "main-error.json"
            KIT.write_json(failure_path, failure)
            failure["error_file"] = str(failure_path)
        except Exception:  # noqa: BLE001
            pass
        # Any degraded path must leave an honest result.json and exit 0; exit 2
        # is reserved for "could not even write a result".
        wrote_result = False
        try:
            out_dir = Path(getattr(args, "output", "/app/output"))
            out_dir.mkdir(parents=True, exist_ok=True)
            result_path = out_dir / "result.json"
            if result_path.is_file():
                wrote_result = True
            else:
                work_dir = Path(getattr(args, "work", "/app/work"))
                case = load_json(work_dir / "case.json", {}) or {}
                if not case:
                    case = load_json(Path(getattr(args, "input", "/input"))
                                     / "case.json", {}) or {}
                fallback = {
                    "case_id": case.get("case_id") or "unknown-case",
                    "task_family": "translation",
                    "mode": "translation",
                    "status": "input_error",
                    "direction": getattr(args, "direction", None) or "zh->en",
                    "artifacts": [],
                    "segments": [],
                    "glossary": [],
                    "preservation": {key: False
                                     for key in KIT.PRESERVATION_KEYS},
                    "back_translation_check": [],
                    "untranslated": [],
                    "uncertainty": ["main_error: %s" % failure["error"]],
                    "change_log": [{
                        "action": "degrade",
                        "scope": "result.json",
                        "reason": "scripts/main.py failed before assembly; "
                                  "see work/main-error.json",
                        "human_review": False,
                    }],
                    "translation_check": {
                        "path": None, "exit_code": None, "errors": None,
                        "warnings": None,
                        "note": "audit unavailable because main.py failed",
                    },
                    "deliverable": {"path": None, "format": "markdown"},
                    "preserved_facts": [],
                    "evidence_map": [],
                    "path_check": {"ok": False,
                                   "problems": ["main_error: no deliverable"]},
                }
                contract = case.get("output_contract")
                if isinstance(contract, dict):
                    for key, value in contract.items():
                        if key not in fallback:
                            fallback[key] = value
                KIT.write_json(result_path, fallback)
                failure["fallback_result"] = str(result_path)
                wrote_result = True
        except Exception:  # noqa: BLE001
            pass
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 0 if wrote_result else 2


if __name__ == "__main__":
    sys.exit(main())

