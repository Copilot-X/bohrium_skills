#!/usr/bin/env python3
"""Validate an academic talk plan and its result.json.

Usage:
    python3 check_talk_plan.py --plan /app/work/talk-plan.json --json
    python3 check_talk_plan.py --plan /app/work/talk-plan.json \
        --result /app/output/result.json --root /app/output --json
    python3 check_talk_plan.py --plan /app/work/talk-plan.json \
        --out /app/work/talk-plan-check.json --strict

Checks
  plan:
    * talk metadata: occasion/audience/total/QA/throughline
    * speaking time = total - QA - buffer; per-slide seconds sum within tolerance
    * slide budget (explicit or derived from speaking minutes)
    * section -> slide assignment: no gaps, no duplicates, minutes add up
    * every content slide has a conclusion-style title, one claim, evidence with
      source+locator, notes, transition and positive seconds
    * result pages require a visual or a figure/table locator
    * must_cover items are covered; dropped nice_to_have items have tradeoffs
    * limitation page present when the talk is long enough
    * Q&A count/class/answer time/evidence locator/backup page references
  result.json:
    * case_id/status/talk fields match the plan
    * slides match plan by number (title, seconds, claim, evidence, notes)
    * timing_check recomputes planned/estimated/deviation/within_tolerance
    * artifacts exist, are inside --root, PPTX/PNG are real files
    * support_files_used mentions this checker and the playbook

Exit codes: 0 pass, 1 problems, 2 unreadable or invalid JSON.
Stdlib only.
"""
import argparse
import json
import math
import re
import sys
import zipfile
from pathlib import Path

ROLES = {
    "title", "outline", "motivation", "question", "approach", "method",
    "result", "discussion", "limitation", "conclusion", "summary",
    "transition", "acknowledgment",
}
CONTENT_ROLES = {
    "motivation", "question", "approach", "method", "result",
    "discussion", "limitation", "conclusion", "summary",
}
CLOSING_ROLES = {"conclusion", "summary"}
QA_CLASSES = {"method", "data", "scope", "literature", "application",
              "hostile", "logistics"}
STRENGTHS = {"strong", "moderate", "weak"}
DISPOSITIONS = {"dropped", "backup", "merged", "deferred"}
STATUSES = {"completed", "partial", "insufficient_evidence", "input_error", "failed"}

GENERIC_TITLES = {
    "研究背景", "背景", "引言", "介绍", "相关工作", "文献综述", "方法",
    "方法介绍", "研究方法", "实验方法", "结果", "研究结果", "结果与讨论",
    "讨论", "结论", "总结", "总结与展望", "结论与展望", "下一步", "展望",
    "目录", "提纲", "致谢", "问答", "谢谢",
    "background", "introduction", "relatedwork", "methods", "method",
    "methodology", "results", "result", "discussion", "conclusion",
    "conclusions", "summary", "outline", "acknowledgments", "acknowledgements",
    "questions", "thankyou", "qa",
}
PLACEHOLDER = re.compile(
    r"(TODO|TBD|FIXME|待补充|待填写|\bxxx\b|<\s*[^<>]{1,48}\s*>)", re.IGNORECASE)
LOCATOR_RE = re.compile(
    r"(slide|page|p\.|backup)\s*\d+|第\s*\d+\s*页|备份\s*\d+|"
    r"图\s*[0-9A-Za-z.\-]+|表\s*[0-9A-Za-z.\-]+|"
    r"Fig\.?\s*[0-9A-Za-z.\-]+|Table\s*[0-9A-Za-z.\-]+|"
    r"§\s*[0-9A-Za-z.\-]+|materials/",
    re.IGNORECASE)
VISUAL_LOCATOR_RE = re.compile(
    r"(图|表|Fig\.?|Table|panel|chart|diagram|示意图|曲线|数据图)",
    re.IGNORECASE)


# ---------------------------------------------------------------- helpers

def add(bucket, code, where, message):
    bucket.append({"code": code, "where": where, "message": message})


def to_number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def to_int(value):
    number = to_number(value)
    if number is None or number != int(number):
        return None
    return int(number)


def text(value):
    return value.strip() if isinstance(value, str) else ""


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def normalize_title(title):
    raw = text(title).lower()
    raw = re.sub(r"[\(\)（）\[\]【】:：、,，.。!?！？\-—_/\\'\"“”‘’\s]+", "", raw)
    raw = re.sub(r"^\d+", "", raw)
    raw = re.sub(r"\d+$", "", raw)
    return raw


def is_generic_title(title):
    normalized = normalize_title(title)
    return not normalized or normalized in GENERIC_TITLES


def evidence_items(slide):
    for key in ("evidence", "supports", "claims"):
        value = slide.get(key)
        if isinstance(value, list) and value:
            return value
    return []


def one_evidence(item):
    """Return (ok, reason) for one evidence object/string."""
    if isinstance(item, str):
        return (bool(item.strip()), "empty evidence string")
    if not isinstance(item, dict):
        return (False, "evidence is neither object nor string")
    source = item.get("source")
    if isinstance(source, dict):
        source = source.get("path") or source.get("locator")
    source = source or item.get("path") or item.get("file")
    locator = (item.get("locator") or item.get("ref") or item.get("page")
               or item.get("figure") or item.get("table"))
    if not source:
        return (False, "evidence missing source/path")
    if not locator:
        return (False, "evidence missing locator")
    return (True, "")


def has_placeholder(*values):
    for value in values:
        if isinstance(value, str) and PLACEHOLDER.search(value):
            return True
    return False


def parse_slides(plan, errors):
    slides = plan.get("slides")
    if not isinstance(slides, list) or not slides:
        add(errors, "PLAN_SLIDES", "plan.slides", "slides[] is missing or empty")
        return [], [], {}
    presented, backup, by_no = [], [], {}
    seen = set()
    for index, slide in enumerate(slides):
        where = "slides[%d]" % index
        if not isinstance(slide, dict):
            add(errors, "SLIDE_TYPE", where, "slide is not an object")
            continue
        number = to_int(slide.get("no"))
        if number is None:
            add(errors, "SLIDE_NO", where, "slide.no is missing or not an integer")
            continue
        if number in seen:
            add(errors, "SLIDE_DUP", where, "duplicate slide.no=%d" % number)
        seen.add(number)
        by_no[number] = slide
        if bool(slide.get("backup")):
            backup.append(slide)
        else:
            presented.append(slide)
    return presented, backup, by_no


# ------------------------------------------------------------ plan checks

def check_talk(talk, errors, warnings, stats):
    if not isinstance(talk, dict):
        add(errors, "TALK_TYPE", "plan.talk", "talk must be an object")
        return None
    for key in ("occasion", "audience", "total_min", "qa_min", "throughline"):
        if talk.get(key) in (None, ""):
            add(errors, "TALK_FIELD", "plan.talk", "missing required field: %s" % key)
    total = to_number(talk.get("total_min"))
    qa = to_number(talk.get("qa_min"))
    buffer_min = to_number(talk.get("buffer_min")) or 0.0
    if total is None or total <= 0:
        add(errors, "TALK_TOTAL", "plan.talk.total_min", "total_min must be > 0")
        return None
    if qa is None or qa < 0:
        add(errors, "TALK_QA", "plan.talk.qa_min", "qa_min must be >= 0")
        qa = 0.0
    if buffer_min < 0:
        add(errors, "TALK_BUFFER", "plan.talk.buffer_min", "buffer_min must be >= 0")
        buffer_min = 0.0
    speaking = total - qa - buffer_min
    if speaking <= 0:
        add(errors, "TALK_SPEAKING", "plan.talk",
            "speaking time = total - qa - buffer must be > 0")
        speaking = max(total - qa, 0.5)
    if total >= 10 and qa <= 0:
        add(warnings, "TALK_QA_RATIO", "plan.talk.qa_min",
            "long talk has no Q&A budget; 15-25% of total is recommended")
    ratio = qa / total if total else 0
    if total >= 10 and (ratio < 0.10 or ratio > 0.35):
        add(warnings, "TALK_QA_RATIO", "plan.talk.qa_min",
            "Q&A is %.0f%% of total; recommended range is 15-25%%" % (ratio * 100))

    throughline = text(talk.get("throughline"))
    if throughline:
        if len(throughline) < 10:
            add(warnings, "THROUGHLINE_SHORT", "plan.talk.throughline",
                "throughline is very short; write an audience-outcome sentence")
        if re.search(r"^(随着|基于|关于|本次汇报|本报告)", throughline) or \
                re.search(r"(具有重要意义|非常重要|综上所述)", throughline):
            add(warnings, "THROUGHLINE_CLICHE", "plan.talk.throughline",
                "throughline uses a generic opener/cliche; replace with a concrete claim")

    must_cover = talk.get("must_cover")
    if not isinstance(must_cover, list) or not must_cover:
        add(warnings, "TALK_MUST_COVER", "plan.talk.must_cover",
            "must_cover is missing or empty; coverage cannot be audited")
        must_cover = []
    nice = talk.get("nice_to_have")
    if nice is not None and not isinstance(nice, list):
        add(warnings, "TALK_NICE", "plan.talk.nice_to_have", "nice_to_have must be a list")

    stats.update({"total_min": round(total, 2), "qa_min": round(qa, 2),
                  "buffer_min": round(buffer_min, 2),
                  "speaking_min": round(speaking, 2)})
    return {"total": total, "qa": qa, "buffer": buffer_min,
            "speaking": speaking, "must_cover": [str(x) for x in must_cover],
            "nice": [str(x) for x in (nice or [])]}


def check_evidence(slide, where, errors, warnings, stats):
    items = evidence_items(slide)
    if not items:
        add(errors, "EVIDENCE_MISSING", where,
            "%s slide has no evidence" % slide.get("role"))
        return
    stats["evidence_items"] += len(items)
    for index, item in enumerate(items):
        ok, reason = one_evidence(item)
        if not ok:
            add(errors, "EVIDENCE_INVALID", "%s.evidence[%d]" % (where, index), reason)
            continue
        if isinstance(item, dict):
            strength = item.get("strength")
            if strength is not None and strength not in STRENGTHS:
                add(warnings, "EVIDENCE_STRENGTH", "%s.evidence[%d]" % (where, index),
                    "unknown strength: %s" % strength)
            if slide.get("role") == "result" and strength in (None, ""):
                add(warnings, "EVIDENCE_STRENGTH", "%s.evidence[%d]" % (where, index),
                    "result evidence has no strength label (strong/moderate/weak)")
            for key in ("source", "locator"):
                if has_placeholder(item.get(key)):
                    add(warnings, "PLACEHOLDER", "%s.evidence[%d].%s" % (where, index, key),
                        "placeholder left in evidence %s" % key)


def check_slide_base(slide, index, errors, warnings, stats, is_last):
    where = "slides[%d:%s]" % (index, slide.get("no"))
    role = slide.get("role")
    title = text(slide.get("title"))
    claim = text(slide.get("claim")) or text(slide.get("message"))
    notes = text(slide.get("notes")) or text(slide.get("speaker_notes"))
    transition = text(slide.get("transition"))
    backup = bool(slide.get("backup"))

    if role not in ROLES:
        add(errors, "SLIDE_ROLE", where, "unknown role: %r" % (role,))
    if not title:
        add(errors, "SLIDE_TITLE", where, "missing title")
    elif is_generic_title(title):
        if role in ("transition", "outline", "acknowledgment"):
            add(warnings, "TITLE_LABEL_OK", where,
                "section-divider title %r is a label; keep it only for navigation" % title)
        else:
            add(errors, "TITLE_GENERIC", where,
                "generic section-style title %r; rewrite as a conclusion" % title)
    else:
        if len(title) > 90:
            add(warnings, "TITLE_LONG", where, "title is longer than 90 characters")
        if has_placeholder(title):
            add(warnings, "PLACEHOLDER", where + ".title", "placeholder left in title")

    if backup:
        if role in CONTENT_ROLES:
            if not claim:
                add(errors, "SLIDE_CLAIM", where, "backup content slide missing claim")
            check_evidence(slide, where, errors, warnings, stats)
        if not notes:
            add(errors, "SLIDE_NOTES", where, "backup slide missing notes")
        return

    stats["presented"] += 1
    if role in CONTENT_ROLES:
        stats["content_slides"] += 1
        if not claim:
            add(errors, "SLIDE_CLAIM", where, "content slide missing claim/message")
        elif len(claim) < 6:
            add(warnings, "CLAIM_SHORT", where, "claim is very short")
        if has_placeholder(claim):
            add(warnings, "PLACEHOLDER", where + ".claim", "placeholder left in claim")
        check_evidence(slide, where, errors, warnings, stats)
        if role == "result":
            visual = text(slide.get("visual"))
            locator_text = " ".join(
                text(item.get("locator")) if isinstance(item, dict) else text(item)
                for item in evidence_items(slide))
            if not visual and not VISUAL_LOCATOR_RE.search(locator_text):
                add(errors, "RESULT_VISUAL", where,
                    "result slide has neither visual nor a figure/table locator")

    if not notes:
        add(errors, "SLIDE_NOTES", where, "missing speaker notes")
    else:
        if len(notes) < 12:
            add(warnings, "NOTES_SHORT", where, "speaker notes are very short")
        if notes == title or (claim and notes == claim):
            add(errors, "NOTES_COPY", where,
                "speaker notes merely copy the title/claim")
        if has_placeholder(notes):
            add(warnings, "PLACEHOLDER", where + ".notes", "placeholder left in notes")
    if not transition and not is_last:
        if role in CONTENT_ROLES:
            add(errors, "TRANSITION_MISSING", where,
                "content slide needs a transition sentence")
        else:
            add(warnings, "TRANSITION_MISSING", where, "missing transition sentence")

    seconds = to_number(slide.get("seconds"))
    if seconds is None or seconds <= 0:
        add(errors, "SLIDE_SECONDS", where, "seconds must be a positive number")
    else:
        stats["presented_seconds"] += seconds
        if seconds < 20 and role != "title":
            add(warnings, "SLIDE_SECONDS_RANGE", where,
                "%.0fs is short for a presented slide" % seconds)
        elif seconds > 120:
            add(warnings, "SLIDE_SECONDS_RANGE", where,
                "%.0fs is long; consider splitting the slide" % seconds)
    rhythm = slide.get("page_rhythm")
    if rhythm is not None and rhythm not in ("dense", "breathing"):
        add(warnings, "PAGE_RHYTHM", where, "unknown page_rhythm: %r" % (rhythm,))


def check_sections(plan, presented, by_no, ctx, errors, warnings, stats):
    sections = plan.get("sections")
    if not isinstance(sections, list) or not sections:
        add(errors, "SECTIONS_MISSING", "plan.sections", "sections[] is missing or empty")
        return
    if len(sections) < 3:
        add(warnings, "SECTIONS_COUNT", "plan.sections",
            "only %d sections; 3-5 acts keep the causal arc visible" % len(sections))
    if len(sections) > 8:
        add(errors, "SECTIONS_COUNT", "plan.sections", "more than 8 sections")
    elif len(sections) > 5:
        add(warnings, "SECTIONS_COUNT", "plan.sections",
            "%d sections is a lot; merge related acts" % len(sections))

    assignment = {}
    minutes_sum = 0.0
    for index, section in enumerate(sections):
        where = "sections[%d]" % index
        if not isinstance(section, dict):
            add(errors, "SECTION_TYPE", where, "section is not an object")
            continue
        if not text(section.get("name")):
            add(warnings, "SECTION_NAME", where, "section has no name")
        if not text(section.get("purpose")):
            add(warnings, "SECTION_PURPOSE", where,
                "section has no purpose; the causal bridge is not auditable")
        minutes = to_number(section.get("minutes"))
        if minutes is None or minutes <= 0:
            add(errors, "SECTION_MINUTES", where, "minutes must be > 0")
            minutes = 0.0
        minutes_sum += minutes
        numbers = section.get("slides")
        if not isinstance(numbers, list) or not numbers:
            add(errors, "SECTION_SLIDES", where, "slides[] is missing or empty")
            continue
        section_seconds = 0.0
        previous = None
        for raw in numbers:
            number = to_int(raw)
            if number is None or number not in by_no:
                add(errors, "SECTION_SLIDE_REF", where,
                    "references unknown slide no=%r" % (raw,))
                continue
            if bool(by_no[number].get("backup")):
                add(errors, "SECTION_BACKUP", where,
                    "backup slide %d cannot belong to a presented section" % number)
                continue
            if number in assignment:
                add(errors, "SECTION_DUP", where,
                    "slide %d is assigned to both %s and %s" %
                    (number, assignment[number], where))
                continue
            assignment[number] = where
            seconds = to_number(by_no[number].get("seconds"))
            if seconds and seconds > 0:
                section_seconds += seconds
            if previous is not None and number < previous:
                add(warnings, "SECTION_ORDER", where,
                    "slide numbers are not ascending inside the section")
            previous = number
        if minutes > 0 and section_seconds > 0:
            section_dev = abs(section_seconds / 60.0 - minutes) / minutes
            if section_dev > 0.35:
                add(warnings, "SECTION_TIME", where,
                    "slide seconds (%.2f min) disagree with section minutes (%.1f)"
                    % (section_seconds / 60.0, minutes))

    for slide in presented:
        number = to_int(slide.get("no"))
        if number is not None and number not in assignment:
            add(errors, "SECTION_GAP", "slides[%s]" % number,
                "presented slide is not assigned to any section")

    speaking = ctx["speaking"]
    diff = abs(minutes_sum - speaking)
    if diff > max(0.75, speaking * 0.15):
        add(errors, "SECTION_TIME_SUM", "plan.sections",
            "section minutes sum to %.2f but speaking time is %.2f" %
            (minutes_sum, speaking))
    elif diff > 0.25:
        add(warnings, "SECTION_TIME_SUM", "plan.sections",
            "section minutes sum to %.2f, speaking time is %.2f" %
            (minutes_sum, speaking))
    stats["sections"] = len(sections)


def check_sequence(presented, ctx, errors, warnings, stats):
    ordered = sorted(presented, key=lambda s: to_int(s.get("no")) or 0)
    roles = [s.get("role") for s in ordered]
    if not roles:
        return
    if roles[0] != "title":
        if "title" in roles:
            add(warnings, "TITLE_ORDER", "slides", "title slide is not first")
        else:
            add(errors, "TITLE_MISSING", "slides", "no slide with role=title")
    if not any(role in CLOSING_ROLES for role in roles):
        add(errors, "CONCLUSION_MISSING", "slides",
            "no conclusion/summary slide; the talk has no landing")
    elif roles[-1] not in CLOSING_ROLES:
        add(warnings, "CONCLUSION_ORDER", "slides",
            "the last presented slide is role=%r, not conclusion/summary" % roles[-1])

    method_index = next((i for i, role in enumerate(roles)
                         if role in ("approach", "method")), None)
    for index, role in enumerate(roles):
        if role == "result" and method_index is None:
            add(errors, "METHOD_MISSING", "slides",
                "result slides appear without any approach/method slide")
            break
        if role == "result" and method_index is not None and index < method_index:
            add(errors, "RESULT_ORDER", "slides",
                "a result slide appears before the method/approach")
            break

    has_limitation = "limitation" in roles
    limitation_text = " ".join(
        text(s.get("title")) + " " + text(s.get("claim")) + " " + text(s.get("notes"))
        for s in ordered)
    mentions_limit = bool(re.search(r"(局限|边界|限制|caveat|limitation)", limitation_text,
                                    re.IGNORECASE))
    requires_limit = any(re.search(r"(局限|边界|limitation)", item, re.IGNORECASE)
                         for item in ctx["must_cover"])
    if requires_limit and not has_limitation and not mentions_limit:
        add(errors, "LIMITATION_MISSING", "slides",
            "must_cover requires limitations but no limitation slide/mention exists")
    elif ctx["speaking"] >= 6 and not has_limitation:
        add(warnings, "LIMITATION_MISSING", "slides",
            "no dedicated limitation slide; state the boundary in the conclusion")
    stats["has_limitation"] = has_limitation


def check_coverage(presented, ctx, plan, errors, warnings, stats):
    must = ctx["must_cover"]
    if not must:
        return
    covered = {}
    for item in must:
        covered[item] = False
    for slide in presented:
        covers = slide.get("covers")
        if covers is None:
            covers = []
        if not isinstance(covers, list):
            add(warnings, "COVERS_TYPE", "slides[%s]" % slide.get("no"),
                "covers must be a list")
            covers = []
        searchable = " ".join([
            text(slide.get("title")), text(slide.get("claim")),
            text(slide.get("notes")), " ".join(str(x) for x in covers),
        ])
        for item in covers:
            label = str(item)
            if label not in covered:
                add(warnings, "COVERS_UNKNOWN", "slides[%s]" % slide.get("no"),
                    "covers item %r is not in must_cover" % label)
            else:
                covered[label] = True
        for item in must:
            if item in searchable:
                covered[item] = True
    missing = [item for item, ok in covered.items() if not ok]
    for item in missing:
        add(errors, "MUST_COVER_MISSING", "plan.slides",
            "must_cover item is not covered by any presented slide: %s" % item)
    stats["must_cover_covered"] = len(must) - len(missing)

    nice = ctx["nice"]
    tradeoff_text = json.dumps(plan.get("tradeoffs") or [], ensure_ascii=False)
    for item in nice:
        appears = any(item in " ".join([text(s.get("title")), text(s.get("claim")),
                                        text(s.get("notes"))])
                      for s in presented)
        if not appears and item not in tradeoff_text:
            add(warnings, "NICE_TO_HAVE", "plan.tradeoffs",
                "nice_to_have item %r is neither on a slide nor in tradeoffs" % item)


def check_qa(plan, ctx, by_no, errors, warnings, stats):
    qa = plan.get("qa")
    required = to_int(plan.get("talk", {}).get("qa_min_required")) or \
        (8 if ctx["total"] >= 12 else 5)
    stats["qa_required"] = required
    if not isinstance(qa, list) or not qa:
        if ctx["speaking"] >= 6:
            add(errors, "QA_MISSING", "plan.qa",
                "qa[] is missing; prepare at least %d questions" % required)
        else:
            add(warnings, "QA_MISSING", "plan.qa", "qa[] is missing")
        stats["qa_items"] = 0
        return
    stats["qa_items"] = len(qa)
    if len(qa) < required:
        add(errors, "QA_COUNT", "plan.qa",
            "%d Q&A items, need at least %d" % (len(qa), required))
    classes = set()
    for index, item in enumerate(qa):
        where = "qa[%d]" % index
        if not isinstance(item, dict):
            add(errors, "QA_TYPE", where, "Q&A item is not an object")
            continue
        question = text(item.get("question"))
        if len(question) < 6:
            add(errors, "QA_QUESTION", where, "question is missing or too short")
        category = item.get("class")
        if category not in QA_CLASSES:
            add(warnings, "QA_CLASS", where, "unknown class: %r" % (category,))
        else:
            classes.add(category)
        seconds = to_number(item.get("answer_seconds"))
        if seconds is None or seconds <= 0:
            add(errors, "QA_SECONDS", where, "answer_seconds must be > 0")
        elif seconds < 15 or seconds > 60:
            add(warnings, "QA_SECONDS", where,
                "%.0fs answer; 20-40s is easier to deliver" % seconds)
        evidence = item.get("evidence")
        if isinstance(evidence, list):
            evidence_text = " ".join(str(x) for x in evidence)
        else:
            evidence_text = text(evidence)
        if not evidence_text:
            add(errors, "QA_EVIDENCE", where, "Q&A answer has no evidence location")
        elif not LOCATOR_RE.search(evidence_text):
            add(errors, "QA_EVIDENCE", where,
                "Q&A evidence %r has no slide/figure/table locator" % evidence_text[:60])
        backup_no = to_int(item.get("backup_slide"))
        if backup_no:
            target = by_no.get(backup_no)
            if target is None:
                add(errors, "QA_BACKUP", where,
                    "backup_slide %d does not exist" % backup_no)
            elif not bool(target.get("backup")):
                add(errors, "QA_BACKUP", where,
                    "slide %d is not marked backup" % backup_no)
    if len(classes) < 2:
        add(warnings, "QA_CLASS_COVERAGE", "plan.qa",
            "Q&A only covers %d class(es); mix method/data/scope" % len(classes))
    for wanted in ("method", "scope"):
        if classes and wanted not in classes:
            add(warnings, "QA_CLASS_COVERAGE", "plan.qa",
                "no %s question prepared" % wanted)


def check_tradeoffs(plan, errors, warnings, stats):
    tradeoffs = plan.get("tradeoffs")
    if tradeoffs is None:
        add(warnings, "TRADEOFFS_MISSING", "plan.tradeoffs",
            "tradeoffs[] is missing; the cut/backup decisions cannot be audited")
        stats["tradeoffs"] = 0
        return
    if not isinstance(tradeoffs, list):
        add(warnings, "TRADEOFFS_TYPE", "plan.tradeoffs", "tradeoffs must be a list")
        stats["tradeoffs"] = 0
        return
    stats["tradeoffs"] = len(tradeoffs)
    for index, item in enumerate(tradeoffs):
        where = "tradeoffs[%d]" % index
        if not isinstance(item, dict):
            add(warnings, "TRADEOFF_TYPE", where, "tradeoff is not an object")
            continue
        if not text(item.get("cut")):
            add(errors, "TRADEOFF_CUT", where, "tradeoff has no 'cut' item")
        if not text(item.get("reason")):
            add(errors, "TRADEOFF_REASON", where, "tradeoff has no reason")
        disposition = item.get("disposition")
        if disposition not in DISPOSITIONS:
            add(warnings, "TRADEOFF_DISPOSITION", where,
                "disposition should be one of %s" % sorted(DISPOSITIONS))


def check_budget(plan, presented, backup, ctx, errors, warnings, stats):
    budget = plan.get("budget") or {}
    if not isinstance(budget, dict):
        add(warnings, "BUDGET_TYPE", "plan.budget", "budget must be an object")
        budget = {}
    declared = to_int(budget.get("slide_budget"))
    count = len(presented)
    speaking = ctx["speaking"]
    low = max(2, math.ceil(speaking * 60 / 90.0))
    high = max(low, math.ceil(speaking * 60 / 40.0))
    stats["slide_budget"] = declared
    stats["slide_range"] = [low, high]
    if declared is not None:
        if declared <= 0:
            add(errors, "BUDGET_SLIDES", "plan.budget.slide_budget", "must be > 0")
        elif count != declared:
            add(errors, "BUDGET_SLIDES", "plan.budget.slide_budget",
                "planned %d presented slides but slide_budget is %d" % (count, declared))
        checks_match = declared == count
    else:
        checks_match = None
        if count < low or count > high:
            add(warnings, "BUDGET_SLIDES", "plan.slides",
                "%d slides is outside the %d-%d range for %.1f speaking minutes"
                % (count, low, high, speaking))
    declared_backup = to_int(budget.get("backup_slides"))
    if declared_backup is not None and declared_backup != len(backup):
        add(errors, "BUDGET_BACKUP", "plan.budget.backup_slides",
            "declared %d backup slides but found %d" % (declared_backup, len(backup)))
    return checks_match


def check_timing(ctx, stats, tolerance, errors, warnings):
    speaking = ctx["speaking"]
    planned_seconds = stats.get("presented_seconds", 0.0)
    estimated = planned_seconds / 60.0
    deviation = abs(estimated - speaking) / speaking if speaking else 1.0
    stats["planned_seconds"] = round(planned_seconds, 1)
    stats["estimated_min"] = round(estimated, 2)
    stats["deviation"] = round(deviation, 4)
    stats["tolerance"] = tolerance
    if deviation > tolerance:
        add(errors, "TIMING_SUM", "plan.slides",
            "slide seconds total %.2f min vs speaking %.2f min "
            "(deviation %.1f%% > %.0f%%)"
            % (estimated, speaking, deviation * 100, tolerance * 100))
    elif deviation > tolerance * 0.5:
        add(warnings, "TIMING_SUM", "plan.slides",
            "timing deviation is %.1f%%; consider rebalancing" % (deviation * 100))
    return deviation


def check_plan(plan, tolerance_override):
    errors, warnings, checks = [], [], {}
    stats = {"presented": 0, "content_slides": 0, "presented_seconds": 0.0,
             "evidence_items": 0, "backup_slides": 0}
    if not isinstance(plan, dict):
        add(errors, "PLAN_TYPE", "plan", "plan root is not an object")
        return {"stats": stats, "checks": checks, "errors": errors,
                "warnings": warnings, "ctx": None}
    if not text(plan.get("case_id")):
        add(errors, "PLAN_CASE_ID", "plan.case_id", "case_id is missing or empty")
    ctx = check_talk(plan.get("talk"), errors, warnings, stats)
    if ctx is None:
        return {"stats": stats, "checks": checks, "errors": errors,
                "warnings": warnings, "ctx": None}

    presented, backup, by_no = parse_slides(plan, errors)
    stats["backup_slides"] = len(backup)
    if not presented:
        add(errors, "PLAN_PRESENTED", "plan.slides", "no presented slides")

    ordered = sorted(presented, key=lambda s: to_int(s.get("no")) or 0)
    for index, slide in enumerate(presented):
        check_slide_base(slide, index, errors, warnings, stats,
                         is_last=(slide is ordered[-1] if ordered else False))
    for slide in backup:
        check_slide_base(slide, slide.get("no") or 0, errors, warnings, stats,
                         is_last=True)
        if slide.get("role") == "limitation":
            add(errors, "LIMITATION_BACKUP", "slides[%s]" % slide.get("no"),
                "limitation slide must be presented, not hidden in backup")

    budget = plan.get("budget") or {}
    tolerance = tolerance_override
    if tolerance is None:
        tolerance = to_number(budget.get("time_tolerance"))
    if tolerance is None:
        tolerance = 0.15
    if tolerance <= 0 or tolerance > 0.5:
        add(warnings, "TIMING_TOLERANCE", "plan.budget.time_tolerance",
            "unusual tolerance %.2f; using 0.15" % tolerance)
        tolerance = 0.15
    ctx["tolerance"] = tolerance

    checks["slide_budget_match"] = check_budget(
        plan, presented, backup, ctx, errors, warnings, stats)
    check_sections(plan, presented, by_no, ctx, errors, warnings, stats)
    check_sequence(presented, ctx, errors, warnings, stats)
    check_coverage(presented, ctx, plan, errors, warnings, stats)
    check_qa(plan, ctx, by_no, errors, warnings, stats)
    check_tradeoffs(plan, errors, warnings, stats)

    # Pacing: more than 3 consecutive dense slides is a fatigue risk.
    run = 0
    for slide in ordered:
        if slide.get("page_rhythm") == "dense":
            run += 1
            if run == 4:
                add(warnings, "PACING", "slides[%s]" % slide.get("no"),
                    "4+ consecutive dense pages; insert a breathing page")
        else:
            run = 0

    checks["timing_within_tolerance"] = (
        check_timing(ctx, stats, tolerance, errors, warnings) <= tolerance)
    checks["slides"] = len(presented)
    checks["has_limitation"] = stats.get("has_limitation", False)
    checks["must_cover_covered"] = stats.get("must_cover_covered", 0)
    ctx["presented"] = presented
    ctx["backup"] = backup
    ctx["by_no"] = by_no
    ctx["required_qa"] = stats.get("qa_required", 5)
    return {"stats": stats, "checks": checks, "errors": errors,
            "warnings": warnings, "ctx": ctx}


# ---------------------------------------------------------- result checks

def inspect_pptx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        return None, "not a valid pptx zip: %s" % exc
    slides = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)]
    notes = [n for n in names if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)]
    if "ppt/presentation.xml" not in names:
        return None, "pptx missing ppt/presentation.xml"
    return {"slides": len(slides), "notes": len(notes)}, ""


def check_result(result, plan, ctx, root, errors, warnings, checks):
    where = "result"
    if not isinstance(result, dict):
        add(errors, "RESULT_TYPE", where, "result root is not an object")
        return
    for key in ("case_id", "status", "talk", "slides", "timing_check",
                "qa", "artifacts", "checks"):
        if key not in result:
            add(errors, "RESULT_FIELD", where, "missing top-level key: %s" % key)
    if text(result.get("case_id")) != text(plan.get("case_id")):
        add(errors, "RESULT_CASE", "result.case_id",
            "case_id %r does not match plan %r" %
            (result.get("case_id"), plan.get("case_id")))
    status = result.get("status")
    if status not in STATUSES:
        add(errors, "RESULT_STATUS", "result.status", "unknown status: %r" % (status,))

    talk = result.get("talk") or {}
    if not isinstance(talk, dict):
        add(errors, "RESULT_TALK", "result.talk", "talk must be an object")
        talk = {}
    plan_talk = plan.get("talk") or {}
    for key in ("throughline", "audience", "occasion"):
        if text(talk.get(key)) != text(plan_talk.get(key)):
            add(errors, "RESULT_TALK", "result.talk.%s" % key,
                "does not match plan.talk.%s" % key)
    total = to_number(talk.get("total_min"))
    if total is None or abs(total - ctx["total"]) > 0.01:
        add(errors, "RESULT_TALK", "result.talk.total_min",
            "total_min does not match plan (%.2f)" % ctx["total"])
    for key, expected in (("speaking_min", ctx["speaking"]),
                          ("qa_min", ctx["qa"])):
        value = to_number(talk.get(key))
        if value is None or abs(value - expected) > 0.25:
            add(errors, "RESULT_TALK", "result.talk.%s" % key,
                "expected %.2f from the plan" % expected)

    plan_meta = result.get("plan")
    if not isinstance(plan_meta, dict) or not text(plan_meta.get("path")):
        add(warnings, "RESULT_PLAN", "result.plan",
            "plan metadata is missing; point to talk-plan.json")
    else:
        declared_slides = to_int(plan_meta.get("slides"))
        if declared_slides is not None and \
                declared_slides != len(ctx["presented"]):
            add(warnings, "RESULT_PLAN", "result.plan.slides",
                "declared %d presented slides, plan has %d"
                % (declared_slides, len(ctx["presented"])))

    presented = ctx["presented"]
    by_no = ctx["by_no"]
    result_slides = result.get("slides")
    if not isinstance(result_slides, list) or not result_slides:
        add(errors, "RESULT_SLIDES", "result.slides", "slides[] is missing or empty")
        result_slides = []
    seen = {}
    seconds_sum = 0.0
    for index, slide in enumerate(result_slides):
        rwhere = "result.slides[%d]" % index
        if not isinstance(slide, dict):
            add(errors, "RESULT_SLIDE_TYPE", rwhere, "slide is not an object")
            continue
        number = to_int(slide.get("no"))
        if number is None:
            add(errors, "RESULT_SLIDE_NO", rwhere, "missing slide no")
            continue
        if number in seen:
            add(errors, "RESULT_SLIDE_DUP", rwhere, "duplicate slide no=%d" % number)
        seen[number] = slide
        planned = by_no.get(number)
        if planned is None:
            if not bool(slide.get("backup")):
                add(errors, "RESULT_SLIDE_UNKNOWN", rwhere,
                    "slide no=%d is not in the plan" % number)
            continue
        if bool(planned.get("backup")):
            if not bool(slide.get("backup")):
                add(warnings, "RESULT_BACKUP_FLAG", rwhere,
                    "slide %d is backup in the plan but not in the result" % number)
            continue
        if text(slide.get("title")) != text(planned.get("title")):
            add(errors, "RESULT_TITLE", rwhere + ".title",
                "title %r does not match plan %r"
                % (text(slide.get("title")), text(planned.get("title"))))
        role = slide.get("role")
        if role and role != planned.get("role"):
            add(warnings, "RESULT_ROLE", rwhere + ".role",
                "role %r differs from plan %r" % (role, planned.get("role")))
        if planned.get("role") in CONTENT_ROLES:
            message = text(slide.get("message")) or text(slide.get("claim"))
            if not message:
                add(errors, "RESULT_MESSAGE", rwhere,
                    "content slide missing message/claim")
            items = evidence_items(slide)
            if not items:
                add(errors, "RESULT_EVIDENCE", rwhere,
                    "content slide missing evidence/supports")
            else:
                for j, item in enumerate(items):
                    ok, reason = one_evidence(item)
                    if not ok:
                        add(errors, "RESULT_EVIDENCE", "%s.evidence[%d]" % (rwhere, j),
                            reason)
        notes = text(slide.get("notes")) or text(slide.get("speaker_notes"))
        if not notes:
            add(errors, "RESULT_NOTES", rwhere, "missing speaker notes")
        seconds = to_number(slide.get("seconds"))
        planned_seconds = to_number(planned.get("seconds"))
        if seconds is None or seconds <= 0:
            add(errors, "RESULT_SECONDS", rwhere, "seconds must be > 0")
        else:
            seconds_sum += seconds
            if planned_seconds and abs(seconds - planned_seconds) > 5:
                add(errors, "RESULT_SECONDS", rwhere,
                    "%.0fs differs from plan %.0fs" % (seconds, planned_seconds))
            elif planned_seconds and abs(seconds - planned_seconds) > 1:
                add(warnings, "RESULT_SECONDS", rwhere,
                    "%.0fs differs from plan %.0fs" % (seconds, planned_seconds))
        if presented:
            last_no = to_int(presented[-1].get("no"))
            if not text(slide.get("transition")) and number != last_no:
                add(warnings, "RESULT_TRANSITION", rwhere, "missing transition")

    for planned in presented:
        number = to_int(planned.get("no"))
        if number not in seen:
            add(errors, "RESULT_SLIDE_MISSING", "result.slides",
                "presented slide %d is missing from result.slides" % number)

    timing = result.get("timing_check") or {}
    if not isinstance(timing, dict):
        add(errors, "RESULT_TIMING", "result.timing_check", "must be an object")
        timing = {}
    planned_min = to_number(timing.get("planned_min"))
    if planned_min is None or abs(planned_min - ctx["speaking"]) > 0.1:
        add(errors, "RESULT_TIMING", "result.timing_check.planned_min",
            "expected speaking %.2f from the plan" % ctx["speaking"])
    estimated = seconds_sum / 60.0
    declared_estimated = to_number(timing.get("estimated_min"))
    if declared_estimated is None or abs(declared_estimated - estimated) > 0.1:
        add(errors, "RESULT_TIMING", "result.timing_check.estimated_min",
            "expected %.2f from result slide seconds" % estimated)
    deviation = abs(estimated - ctx["speaking"]) / ctx["speaking"] \
        if ctx["speaking"] else 1.0
    declared_dev = to_number(timing.get("deviation"))
    if declared_dev is None or abs(declared_dev - deviation) > 0.02:
        add(errors, "RESULT_TIMING", "result.timing_check.deviation",
            "expected %.4f from the plan and slide seconds" % deviation)
    within = timing.get("within_tolerance")
    expected_within = deviation <= ctx["tolerance"] + 1e-9
    if not isinstance(within, bool) or within != expected_within:
        add(errors, "RESULT_TIMING", "result.timing_check.within_tolerance",
            "expected %s" % expected_within)
    checks["result_timing_recomputed"] = round(deviation, 4)

    qa = result.get("qa")
    if not isinstance(qa, list):
        add(errors, "RESULT_QA", "result.qa", "qa[] is missing or not a list")
        qa = []
    if len(qa) < ctx["required_qa"]:
        add(errors, "RESULT_QA_COUNT", "result.qa",
            "%d items, plan requires at least %d" % (len(qa), ctx["required_qa"]))
    for index, item in enumerate(qa):
        rwhere = "result.qa[%d]" % index
        if not isinstance(item, dict):
            add(errors, "RESULT_QA_TYPE", rwhere, "Q&A item is not an object")
            continue
        if not text(item.get("question")):
            add(errors, "RESULT_QA_QUESTION", rwhere, "missing question")
        evidence = item.get("evidence")
        evidence_text = " ".join(str(x) for x in evidence) \
            if isinstance(evidence, list) else text(evidence)
        if not evidence_text or not LOCATOR_RE.search(evidence_text):
            add(errors, "RESULT_QA_EVIDENCE", rwhere,
                "answer needs a slide/figure/table locator")

    artifacts = result.get("artifacts")
    base = Path(root) if root else None
    roles = {}
    if not isinstance(artifacts, list) or not artifacts:
        add(errors, "RESULT_ARTIFACTS", "result.artifacts",
            "artifacts[] is missing or empty")
        artifacts = []
    for index, item in enumerate(artifacts):
        awhere = "result.artifacts[%d]" % index
        if not isinstance(item, dict):
            add(errors, "RESULT_ARTIFACT_TYPE", awhere, "artifact is not an object")
            continue
        role = text(item.get("role")).lower()
        rel = text(item.get("path"))
        if not role or not rel:
            add(errors, "RESULT_ARTIFACT_FIELD", awhere, "artifact needs role and path")
            continue
        if role in roles:
            add(warnings, "RESULT_ARTIFACT_DUP", awhere, "duplicate role %r" % role)
        candidate = Path(rel)
        if not candidate.is_absolute() and base is not None:
            candidate = base / rel
        if not candidate.is_file():
            add(errors, "RESULT_ARTIFACT_MISSING", awhere,
                "artifact file does not exist: %s" % rel)
            continue
        roles[role] = candidate
        suffix = candidate.suffix.lower()
        if suffix == ".pptx":
            info, why = inspect_pptx(candidate)
            if info is None:
                add(errors, "RESULT_ARTIFACT_PPTX", awhere, why)
            else:
                checks["pptx_slides"] = info["slides"]
                checks["pptx_notes"] = info["notes"]
                if info["slides"] < len(presented):
                    add(errors, "RESULT_ARTIFACT_PPTX", awhere,
                        "deck has %d slides, fewer than %d presented slides"
                        % (info["slides"], len(presented)))
                if info["notes"] < len(presented):
                    add(warnings, "RESULT_ARTIFACT_PPTX", awhere,
                        "deck has %d notes parts for %d presented slides"
                        % (info["notes"], len(presented)))
        elif suffix == ".png":
            head = candidate.read_bytes()[:8]
            if head != b"\x89PNG\r\n\x1a\n":
                add(errors, "RESULT_ARTIFACT_PNG", awhere,
                    "preview is not a valid PNG")

    plan_roles = {"plan", "outline"}
    if not plan_roles & set(roles):
        add(errors, "RESULT_ARTIFACT_PLAN", "result.artifacts",
            "need an artifact with role=plan or role=outline")
    if "qa" not in roles:
        add(errors, "RESULT_ARTIFACT_QA", "result.artifacts",
            "need an artifact with role=qa (qa-prep.md)")
    deck_required = (plan.get("budget") or {}).get("deck_required", True)
    if status == "completed" and deck_required and not ({"editable", "deck"} & set(roles)):
        add(errors, "RESULT_ARTIFACT_DECK", "result.artifacts",
            "completed status needs an editable/deck artifact")
    elif status == "partial" and not ({"editable", "deck"} & set(roles)):
        add(warnings, "RESULT_ARTIFACT_DECK", "result.artifacts",
            "partial result has no deck; state this limit in uncertainty")

    result_checks = result.get("checks")
    if not isinstance(result_checks, dict):
        add(errors, "RESULT_CHECKS", "result.checks", "checks must be an object")
    else:
        for key in ("slides_parse", "page_count_match",
                    "every_content_slide_has_message", "limitation_slide"):
            if key not in result_checks:
                add(warnings, "RESULT_CHECKS", "result.checks", "missing check: %s" % key)

    used = result.get("support_files_used")
    used_text = " ".join(str(x) for x in used) if isinstance(used, list) else text(used)
    if not used_text:
        add(warnings, "RESULT_SUPPORT_USED", "result.support_files_used",
            "support_files_used is missing; record the playbook and checker")
    else:
        if "check_talk_plan.py" not in used_text:
            add(warnings, "RESULT_SUPPORT_USED", "result.support_files_used",
                "check_talk_plan.py is not listed as used")
        if "talk-narrative-playbook.md" not in used_text:
            add(warnings, "RESULT_SUPPORT_USED", "result.support_files_used",
                "talk-narrative-playbook.md is not listed as used")
    if not isinstance(result.get("uncertainty"), list):
        add(warnings, "RESULT_UNCERTAINTY", "result.uncertainty",
            "uncertainty[] is missing; record open items honestly")
    if not isinstance(result.get("change_log"), list):
        add(warnings, "RESULT_CHANGE_LOG", "result.change_log",
            "change_log[] is missing; record title/order edits")
    checks["artifact_roles"] = sorted(roles)


# ------------------------------------------------------------------ main

def build_report(plan_path, result_path, root, tolerance, strict):
    errors, warnings, checks = [], [], {}
    plan = load_json(plan_path)
    plan_report = check_plan(plan, tolerance)
    errors.extend(plan_report["errors"])
    warnings.extend(plan_report["warnings"])
    checks.update(plan_report["checks"])
    stats = plan_report["stats"]
    ctx = plan_report["ctx"]

    if result_path:
        if ctx is None:
            add(errors, "RESULT_SKIPPED", "result",
                "plan is not usable, result.json cannot be cross-checked")
        else:
            result = load_json(result_path)
            check_result(result, plan, ctx, root, errors, warnings, checks)
    ok = not errors and not (strict and warnings)
    return {
        "ok": ok,
        "strict": strict,
        "plan": str(plan_path),
        "result": str(result_path) if result_path else None,
        "stats": stats,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def print_human(report):
    print("ok: %s" % ("true" if report["ok"] else "false"))
    stats = report["stats"]
    print("slides: %s presented / %s backup; speaking: %s min; "
          "planned: %s min; deviation: %s"
          % (stats.get("presented", 0), stats.get("backup_slides", 0),
             stats.get("speaking_min", "?"), stats.get("estimated_min", "?"),
             stats.get("deviation", "?")))
    print("qa: %s (required %s); sections: %s; tradeoffs: %s"
          % (stats.get("qa_items", 0), stats.get("qa_required", "?"),
             stats.get("sections", 0), stats.get("tradeoffs", 0)))
    print("errors: %d" % len(report["errors"]))
    for item in report["errors"][:30]:
        print("  [%s] %s: %s" % (item["code"], item["where"], item["message"]))
    if len(report["errors"]) > 30:
        print("  ... %d more" % (len(report["errors"]) - 30))
    print("warnings: %d" % len(report["warnings"]))
    for item in report["warnings"][:20]:
        print("  [%s] %s: %s" % (item["code"], item["where"], item["message"]))
    if len(report["warnings"]) > 20:
        print("  ... %d more" % (len(report["warnings"]) - 20))


def main():
    parser = argparse.ArgumentParser(
        description="Validate an academic talk plan and its result.json.")
    parser.add_argument("--plan", required=True, help="talk-plan.json")
    parser.add_argument("--result", default=None, help="result.json to cross-check")
    parser.add_argument("--root", default=None,
                        help="root for relative artifact paths (default: result dir)")
    parser.add_argument("--time-tolerance", type=float, default=None,
                        help="override timing tolerance (default: plan or 0.15)")
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as a failure")
    parser.add_argument("--json", action="store_true", help="print JSON report")
    parser.add_argument("--out", default=None, help="write JSON report to this path")
    args = parser.parse_args()

    try:
        report = build_report(args.plan, args.result, args.root,
                              args.time_tolerance, args.strict)
    except (OSError, json.JSONDecodeError) as exc:
        report = {
            "ok": False,
            "strict": args.strict,
            "plan": args.plan,
            "result": args.result,
            "stats": {},
            "checks": {},
            "errors": [{"code": "UNREADABLE", "where": "input",
                        "message": str(exc)}],
            "warnings": [],
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_human(report)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
