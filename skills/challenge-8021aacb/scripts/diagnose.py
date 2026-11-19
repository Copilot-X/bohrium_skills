#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Six-dimension structural diagnosis over an inventoried manuscript.

Usage:
    python3 diagnose.py --work /app/work [--json]

Reads ``inventory.json`` and ``workorder.json`` from ``<work>``, builds a
chapter/section/paragraph index with :func:`structure_kit.build_map`, then checks:

    D1 research question vs contribution claim
    D2 section structure, order, empty sections, balance
    D3 argument chain: new results introduced in discussion/conclusion
    D4 paragraph relations: abrupt section openings, overlong paragraphs
    D5 duplicate sentences (same wording, multiple locations)
    D6 conclusion boundary: causal overreach, universal wording, missing limits

Writes ``<work>/structure-map.json`` and ``<work>/diagnosis.json``. Every
finding carries a chapter/paragraph locator, a verbatim quote, structured
evidence and an executable edit. Clean manuscripts legitimately yield zero
findings. Stdlib only, no network.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import structure_kit as kit  # noqa: E402

DIMENSIONS = OrderedDict([
    ("D1", "研究问题与贡献主张"),
    ("D2", "章节结构与叙事顺序"),
    ("D3", "论证链与论据对应"),
    ("D4", "段落关系与衔接"),
    ("D5", "重复与冗余"),
    ("D6", "结论边界与表述尺度"),
])
SEVERITY_PRIORITY = {"blocker": "P1", "major": "P2", "minor": "P3"}
SEVERITY_RANK = {"P1": 0, "P2": 1, "P3": 2}
RESULT_CUES = ("发现", "表明", "显示", "观察到", "found", "observed",
               "showed", "show that", "indicate", "revealed")
CONTRIBUTION_CUES = ("贡献", "本文提出", "本研究提出", "首次", "填补空白",
                     "we propose", "we present", "we show", "contribution",
                     "first study", "novel")
QUESTION_CUES = ("研究问题", "本文研究", "本研究", "我们研究", "本文考察",
                 "本研究考察", "本文检验", "本研究检验", "research question",
                 "aim of this study", "this study examines", "we investigate",
                 "we examine")
OVERCLAIM_CUES = ("首次", "填补空白", "证明了", "证明", "first study",
                  "first time", "prove", "proves", "proven")
CAUSAL_CUES = ("证明", "证实", "导致", "使得", "驱动", "引起",
               "prove", "proves", "proven", "causes", "caused",
               "leads to", "lead to", "demonstrates that", "establishes that",
               "confirms that")
NEGATION_CUES = ("不能", "无法", "不应", "尚不", "未", "not ", "cannot",
                 "can not", "does not", "did not", "no evidence")
UNIVERSAL_CUES = ("所有", "全部", "任何", "总是", "必然", "always", "all ",
                  "every ", "never ")
SCOPE_CUES = ("本研究", "本样本", "该样本", "所观察", "在当前", "在本研究",
              "in this sample", "within this", "our sample", "observed",
              "may", "可能", "条件下", "范围内")
LIMITATION_CUES = ("局限", "限制", "不足", "未来研究", "外推", "limitation",
                   "future work", "future research", "caveat", "caution")
CORRELATIONAL_CUES = ("横断面", "相关分析", "相关性", "关联分析", "观察性",
                      "cross-sectional", "correlational", "observational",
                      "survey", "问卷")
STRONG_CAUSAL_DESIGN = ("随机对照", "随机分组", "双盲", "准实验", "工具变量",
                        "difference-in-differences", "randomized controlled",
                        "randomized trial", "rct", "instrumental variable")
CONTRAST_OPENERS = ("但是", "然而", "不过", "可是", "相反", "however",
                    "nevertheless", "by contrast", "in contrast")
EDIT_TYPES = {"move", "reorder", "merge", "split", "delete", "insert",
              "add_transition", "rewrite", "narrow", "relabel"}
MAX_PER_DIMENSION = 4


def load_json(path, default=None):
    return kit.read_json(path, default=default)


def chapter_label(chapter):
    title = str(chapter.get("title") or "")
    match = re.match(r"^\s*(\d+(?:\.\d+)*)", title)
    return match.group(1) if match else str(chapter.get("number") or "")


def chapter_function(chapter):
    function = chapter.get("function") or "unknown"
    title = kit.norm_label(chapter.get("title") or "")
    if re.search(r"conclusion|结论|结语", title):
        return "conclusion"
    if re.search(r"discussion|讨论", title):
        return "discussion"
    if re.search(r"abstract|摘要", title):
        return "abstract"
    return function


def iter_paragraphs(map_data):
    for chapter in map_data.get("chapters") or []:
        label = chapter_label(chapter)
        for paragraph in chapter.get("paragraphs") or []:
            yield {
                "chapter": label,
                "chapter_number": str(chapter.get("number") or ""),
                "chapter_title": chapter.get("title") or "",
                "function": chapter_function(chapter),
                "section": "",
                "section_title": "",
                "paragraph": paragraph,
            }
        for section in chapter.get("sections") or []:
            for paragraph in section.get("paragraphs") or []:
                yield {
                    "chapter": label,
                    "chapter_number": str(chapter.get("number") or ""),
                    "chapter_title": chapter.get("title") or "",
                    "function": chapter_function(chapter),
                    "section": str(section.get("section_id") or ""),
                    "section_title": section.get("title") or "",
                    "paragraph": paragraph,
                }


def paragraphs_by_function(paras, functions):
    return [item for item in paras if item["function"] in functions]


def join_text(paras):
    return "\n".join(item["paragraph"].get("text") or "" for item in paras)


def make_evidence(kind, locator, quote=""):
    item = {"kind": kind, "locator": locator}
    if quote:
        item["quote"] = quote
    return item


def make_finding(dimension, severity, where, quote, problem, evidence,
                 suggestion, edit, apply, extra=None):
    finding = {
        "finding_id": "",
        "dimension": dimension,
        "severity": severity,
        "priority": SEVERITY_PRIORITY[severity],
        "location": {
            "chapter": where.get("chapter", ""),
            "chapter_title": where.get("chapter_title", ""),
            "section": where.get("section", ""),
            "paragraph_id": where.get("paragraph_id", ""),
            "quote": quote,
        },
        "problem": problem,
        "evidence": evidence,
        "suggestion": suggestion,
        "edit": edit,
        "apply": bool(apply),
    }
    if extra:
        finding.update(extra)
    return finding


def soften_causal(text):
    out = text
    out = out.replace("证明了", "提示").replace("证实了", "提示")
    out = out.replace("证明", "提示").replace("证实", "提示")
    out = re.sub(
        r"(.+?)(?:导致|使得|引起)([^。！？!?；;]+)([。！？!?；;]?)$",
        r"\1与\2相关，尚不能推断因果关系\3", out)
    out = re.sub(r"(?i)\bproves\b", "suggests", out)
    out = re.sub(r"(?i)\bprove\b", "suggest", out)
    out = re.sub(r"(?i)\bdemonstrates\b", "indicates", out)
    out = re.sub(r"(?i)\bcauses\b", "is associated with", out)
    out = re.sub(r"(?i)\bcause\b", "is associated with", out)
    out = re.sub(r"(?i)\bleads to\b", "is associated with", out)
    out = re.sub(r"(?i)\blead to\b", "is associated with", out)
    return out


def soften_universal(text):
    replacements = [
        ("所有", "所观察样本中的"), ("全部", "所观察的"),
        ("任何", "所观察的"), ("总是", "在本研究中"),
        ("必然", "在本研究条件下"),
        ("always", "in this sample"), ("never", "not observed to"),
        ("every ", "each observed "), ("all ", "the observed "),
    ]
    out = text
    for before, after in replacements:
        if before in out:
            out = re.sub(re.escape(before), after, out, flags=re.IGNORECASE)
    # A correlational study cannot support a prescriptive "must use as the
    # main lever" conclusion.  Soften the policy claim as well as the scope.
    out = out.replace("作为提升成绩的主要手段", "作为后续干预研究的候选方向之一")
    out = out.replace("作为提高成绩的主要手段", "作为后续干预研究的候选方向之一")
    out = out.replace("主要手段", "候选方向之一")
    out = out.replace("应据此", "可据此考虑")
    out = out.replace("应把", "可考虑把")
    out = re.sub(r"\bshould\b", "could consider", out, flags=re.IGNORECASE)
    return out


def check_d1(paras, mode):
    findings = []
    abstract = paragraphs_by_function(paras, {"abstract"})
    intro = paragraphs_by_function(paras, {"introduction"})
    pool = abstract + intro
    question = ""
    contribution = ""
    for item in pool:
        for sentence in kit.split_sentences(
                item["paragraph"].get("text") or ""):
            if not question and kit.contains_any(sentence, QUESTION_CUES):
                question = sentence
                question_where = item
            if not contribution and kit.contains_any(
                    sentence, CONTRIBUTION_CUES):
                contribution = sentence
                contribution_where = item
    methods_text = join_text(paragraphs_by_function(paras, {"methods"}))
    correlational = (kit.contains_any(methods_text, CORRELATIONAL_CUES)
                     and not kit.contains_any(methods_text,
                                                 STRONG_CAUSAL_DESIGN))
    if contribution and correlational and kit.contains_any(
            contribution, OVERCLAIM_CUES):
        where = contribution_where
        quote = contribution
        after = soften_causal(quote)
        if after != quote:
            findings.append(make_finding(
                "D1", "major", {
                    "chapter": where["chapter"],
                    "chapter_title": where["chapter_title"],
                    "section": where["section"],
                    "paragraph_id": where["paragraph"]["paragraph_id"],
                }, quote,
                "贡献句使用首创/因果强度，但方法部分只能支持关联性证据",
                [make_evidence("text", where["paragraph"]["paragraph_id"],
                               quote),
                 make_evidence("structure", "方法/设计段落",
                               kit.first_sentence(methods_text, 160))],
                "把贡献主张降到与设计一致的强度，保留已完成的工作",
                {"type": "narrow", "before": quote, "after": after},
                mode == "revise"))
    if question:
        basis = "检出研究问题句：“%s”" % kit.truncate(question, 120)
    else:
        basis = "未检出显式问句；按引言主题句与摘要主张核对"
    if contribution:
        basis += "；贡献句：“%s”" % kit.truncate(contribution, 100)
    else:
        basis += "；未检出单独贡献句"
    if findings:
        basis += "；发现 %d 处贡献—设计强度不一致" % len(findings)
    else:
        basis += "；未发现贡献主张与实际分析冲突"
    return findings, basis, bool(paras)


def check_d2(map_data, paras, workorder, mode):
    findings = []
    if not map_data.get("chapters"):
        return findings, "未识别到章节标题，章节结构维度无法可靠评估", False
    detected = {chapter_function(chapter)
                for chapter in map_data["chapters"]}
    has_methods_results = bool(detected & {"methods", "results"})
    empirical = has_methods_results or workorder.get("genre") == "empirical_article"
    expected = workorder.get("expected_sections") or []
    if not empirical:
        expected = [name for name in expected
                    if name in {"introduction", "conclusion"}]
    first_para = paras[0] if paras else None
    anchor_title = (map_data["chapters"][0].get("title") or "document")
    for name in expected:
        if name in detected:
            continue
        if name == "introduction" and "abstract" in detected:
            # A background/引言 chapter is usually present; do not guess when
            # only an abstract exists.
            pass
        where = {
            "chapter": chapter_label(map_data["chapters"][0]),
            "chapter_title": anchor_title,
            "section": "",
            "paragraph_id": (first_para["paragraph"]["paragraph_id"]
                             if first_para else "document"),
        }
        label = {"methods": "方法", "results": "结果", "discussion": "讨论",
                 "conclusion": "结论", "introduction": "引言"}[name]
        findings.append(make_finding(
            "D2", "major" if name in {"methods", "results"} else "minor",
            where, anchor_title,
            "稿件缺少承担“%s”功能的章节（章节树中未检出）" % label,
            [make_evidence("structure", "章节树；detected=%s"
                           % ",".join(sorted(detected)))],
            "补建“%s”章节，或用现有章节标题明确承担该功能" % label,
            {"type": "insert", "before": anchor_title,
             "after": "\n# %s\n" % label},
            False))
        if len(findings) >= MAX_PER_DIMENSION:
            break

    signals = map_data.get("signals") or {}
    function_order = []
    for chapter in map_data["chapters"]:
        function_order.append((chapter_function(chapter), chapter_label(chapter),
                               chapter.get("title") or ""))
    for anomaly in signals.get("function_order_anomalies") or []:
        where = {
            "chapter": function_order[-1][1] if function_order else "",
            "chapter_title": function_order[-1][2] if function_order else "",
            "section": "",
            "paragraph_id": (paras[-1]["paragraph"]["paragraph_id"]
                             if paras else "document"),
        }
        findings.append(make_finding(
            "D2", "major", where,
            function_order[-1][2] if function_order else anomaly,
            "章节功能顺序异常：%s" % anomaly,
            [make_evidence("structure", "章节功能序列；%s"
                           % (" → ".join(":".join(item[:2])
                                         for item in function_order)))],
            "按“问题→方法→结果→讨论→结论”的重排受影响章节，并检查跨节引用",
            {"type": "reorder", "before": where["chapter_title"],
             "target": "按论证顺序重排"},
            False))
        if len(findings) >= MAX_PER_DIMENSION:
            break

    for empty in signals.get("empty_sections") or []:
        where = {
            "chapter": chapter_label(map_data["chapters"][0]),
            "chapter_title": anchor_title,
            "section": str(empty).split(" ")[0],
            "paragraph_id": (first_para["paragraph"]["paragraph_id"]
                             if first_para else "document"),
        }
        title = " ".join(str(empty).split(" ")[1:]) or str(empty)
        findings.append(make_finding(
            "D2", "minor", where, title,
            "空章节只有标题、没有承担论证的段落：%s" % empty,
            [make_evidence("structure", "章节树；empty_section=%s" % empty)],
            "删除该空标题，或补写承担该节功能的段落；不要保留空壳",
            {"type": "delete", "before": title},
            False))
        if len(findings) >= MAX_PER_DIMENSION:
            break

    body_words = sum(chapter.get("word_count") or 0
                     for chapter in map_data["chapters"])
    intro_words = sum(chapter.get("word_count") or 0
                      for chapter in map_data["chapters"]
                      if chapter_function(chapter) == "introduction")
    if body_words >= 600 and intro_words / body_words >= 0.5 and intro:
        where = intro[0]
        quote = kit.first_sentence(where["paragraph"].get("text") or "")
        findings.append(make_finding(
            "D2", "minor", {
                "chapter": where["chapter"],
                "chapter_title": where["chapter_title"],
                "section": where["section"],
                "paragraph_id": where["paragraph"]["paragraph_id"],
            }, quote,
            "引言占正文 %.0f%%，篇幅挤压了方法与结果的信息释放"
            % (100.0 * intro_words / body_words),
            [make_evidence("structure", "篇幅统计；intro=%d/body=%d"
                           % (intro_words, body_words))],
            "把文献综述细节后移到讨论或附录，只保留与当前问题直接相关的铺垫",
            {"type": "move", "before": quote,
             "target": "讨论或附录"},
            False))
        if len(findings) >= MAX_PER_DIMENSION:
            findings = findings[:MAX_PER_DIMENSION]

    order_text = " → ".join(
        "%s(%s)" % (chapter_function(chapter), chapter_label(chapter))
        for chapter in map_data["chapters"])
    basis = "章节功能序列：%s" % order_text
    if findings:
        basis += "；检出 %d 处缺章/错序/空节/篇幅问题" % len(findings)
    else:
        basis += "；未发现缺章、错序、空节或极端篇幅失衡"
    return findings, basis, True


def check_d3(paras, map_data, mode):
    findings = []
    results = paragraphs_by_function(paras, {"results"})
    discussion = paragraphs_by_function(paras, {"discussion", "conclusion"})
    results_text = join_text(results)
    results_tokens = kit.quantity_tokens(results_text)
    results_label = results[0]["chapter"] if results else "结果章节"
    target_pid = ""
    if results:
        target_pid = results[-1]["paragraph"]["paragraph_id"]
    for item in discussion:
        paragraph = item["paragraph"]
        for sentence in kit.split_sentences(paragraph.get("text") or ""):
            if not kit.contains_any(sentence, RESULT_CUES):
                continue
            tokens = kit.quantity_tokens(sentence)
            new_tokens = sorted(tokens - results_tokens)
            if not new_tokens:
                continue
            if not target_pid:
                continue
            quote = sentence[:300]
            findings.append(make_finding(
                "D3", "major", {
                    "chapter": item["chapter"],
                    "chapter_title": item["chapter_title"],
                    "section": item["section"],
                    "paragraph_id": paragraph["paragraph_id"],
                }, quote,
                "讨论/结论报告了结果部分未出现的新数值发现（%s），论证链断裂"
                % ", ".join(new_tokens[:4]),
                [make_evidence("text", paragraph["paragraph_id"], quote),
                 make_evidence("omission", "结果章节 %s；未出现 %s"
                               % (results_label,
                                  ", ".join(new_tokens[:4])))],
                "把该句移入结果章节并补对应分析，或删除该新增结果；讨论只解释已报告的结果",
                {"type": "move", "before": quote, "target": target_pid,
                 "target_label": "结果章节末尾"},
                mode == "revise"))
            if len(findings) >= MAX_PER_DIMENSION:
                break
        if len(findings) >= MAX_PER_DIMENSION:
            break
    basis = ("结果章节含 %d 个可定位数值；讨论/结论中的结果句均已回溯"
             % len(results_tokens))
    if findings:
        basis = ("讨论/结论中检出 %d 句带入结果部分未出现的数值；结果章节含 "
                 "%d 个可定位数值" % (len(findings), len(results_tokens)))
    elif not results:
        basis = "无独立结果章节；未发现讨论中首次报告新数值结果"
    return findings, basis, bool(paras)


def check_d4(paras, map_data, mode):
    findings = []
    for chapter in map_data.get("chapters") or []:
        for section in chapter.get("sections") or []:
            section_paras = section.get("paragraphs") or []
            if not section_paras:
                continue
            first = section_paras[0]
            text = first.get("text") or ""
            sentence = kit.first_sentence(text)
            if not kit.contains_any(sentence[:12], CONTRAST_OPENERS):
                continue
            title = section.get("title") or chapter.get("title") or "本节"
            clean_title = re.sub(
                r"^\s*\d+(?:\.\d+)*\s*[.、．]?\s*", "", str(title)).strip()
            clean_title = clean_title or "本节"
            after = ("承接上文，本节围绕“%s”展开。" % clean_title
                     if kit.detect_language(title) == "zh"
                     else "Building on the previous section, this section "
                          "examines %s." % clean_title)
            findings.append(make_finding(
                "D4", "minor", {
                    "chapter": chapter_label(chapter),
                    "chapter_title": chapter.get("title") or "",
                    "section": str(section.get("section_id") or ""),
                    "paragraph_id": first.get("paragraph_id") or "",
                }, sentence,
                "小节首段以转折词开头，但承接对象不在本节内，读者无法确定“但是”针对什么",
                [make_evidence("text", first.get("paragraph_id") or "",
                               sentence)],
                "在本段之前补一句承接上一节结论的过渡，再进入转折",
                {"type": "add_transition", "before": sentence, "after": after},
                mode == "revise"))
            if len(findings) >= MAX_PER_DIMENSION:
                break
        if len(findings) >= MAX_PER_DIMENSION:
            break

    if len(findings) < MAX_PER_DIMENSION:
        for item in paras:
            paragraph = item["paragraph"]
            words = paragraph.get("word_count") or 0
            sentences = paragraph.get("sentence_count") or 0
            if words < 280 or sentences < 9:
                continue
            if item["function"] in {"references", "appendix", "abstract"}:
                continue
            quote = kit.first_sentence(paragraph.get("text") or "")
            findings.append(make_finding(
                "D4", "minor", {
                    "chapter": item["chapter"],
                    "chapter_title": item["chapter_title"],
                    "section": item["section"],
                    "paragraph_id": paragraph["paragraph_id"],
                }, quote,
                "单段承载 %d 句/%d 词，段内至少有两个主题却缺少拆分与过渡"
                % (sentences, words),
                [make_evidence("text", paragraph["paragraph_id"], quote)],
                "在第二个主题开始处拆段，并给新段补主题句",
                {"type": "split", "before": quote, "target": "段落中部"},
                mode == "revise"))
            if len(findings) >= MAX_PER_DIMENSION:
                break

    basis = "已检查 %d 个段落的小节起始衔接与段内负载" % len(paras)
    if findings:
        basis += "；检出 %d 处衔接/拆分问题" % len(findings)
    else:
        basis += "；未发现无承接转折或超长多主题段落"
    return findings, basis, bool(paras)


def check_d5(paras, mode):
    findings = []
    occurrences = OrderedDict()
    for item in paras:
        if item["function"] in {"references", "appendix", "abstract"}:
            continue
        for sentence in kit.split_sentences(
                item["paragraph"].get("text") or ""):
            key = kit.compact(sentence)
            if len(key) < 12:
                continue
            occurrences.setdefault(key, []).append((item, sentence))
    used_chapters = set()
    for key, items in occurrences.items():
        if len(items) < 2:
            continue
        unique_pids = {entry[0]["paragraph"]["paragraph_id"]
                       for entry in items}
        if len(unique_pids) < 2:
            continue
        chapters = []
        for entry in items:
            label = entry[0]["chapter"] or entry[0]["chapter_title"]
            if label not in chapters:
                chapters.append(label)
        if len(chapters) < 2:
            continue
        later_item, later_sentence = items[-1]
        if later_item["chapter"] in used_chapters:
            continue
        used_chapters.add(later_item["chapter"])
        evidence = [make_evidence(
            "text", entry[0]["paragraph"]["paragraph_id"],
            entry[1][:240]) for entry in items[:4]]
        quote = later_sentence[:240]
        findings.append(make_finding(
            "D5", "minor", {
                "chapter": later_item["chapter"],
                "chapter_title": later_item["chapter_title"],
                "section": later_item["section"],
                "paragraph_id": later_item["paragraph"]["paragraph_id"],
            }, quote,
            "同一句在 %d 个位置逐字重复（章节 %s），后一次没有新增信息"
            % (len(items), "、".join(str(chapter) for chapter in chapters[:4])),
            evidence,
            "保留首次出现的位置，删除后一次；若确需呼应，改为一句交叉引用",
            {"type": "delete", "before": quote},
            mode == "revise"))
        if len(findings) >= MAX_PER_DIMENSION:
            break
    basis = "已比较 %d 个句子（排除摘要与参考文献）" % sum(
        len(entry) for entry in occurrences.values())
    if findings:
        basis += "；检出 %d 组跨章节逐字重复" % len(findings)
    else:
        basis += "；未发现跨章节逐字重复"
    return findings, basis, bool(paras)


def check_d6(paras, mode):
    findings = []
    conclusion = paragraphs_by_function(paras, {"conclusion"})
    discussion = paragraphs_by_function(paras, {"discussion"})
    if not conclusion:
        conclusion = [
            item for item in discussion
            if kit.contains_any(
                item["paragraph"].get("text") or "",
                ("综上", "总之", "in summary", "in conclusion"))]
    methods_text = join_text(paragraphs_by_function(paras, {"methods"}))
    correlational = (kit.contains_any(methods_text, CORRELATIONAL_CUES)
                     and not kit.contains_any(methods_text,
                                                 STRONG_CAUSAL_DESIGN))
    design_sentence = ""
    for sentence in kit.split_sentences(methods_text):
        if kit.contains_any(sentence, CORRELATIONAL_CUES):
            design_sentence = sentence[:200]
            break
    used_text = set()

    for item in conclusion:
        paragraph = item["paragraph"]
        for sentence in kit.split_sentences(paragraph.get("text") or ""):
            if not kit.contains_any(sentence, CAUSAL_CUES):
                continue
            if kit.contains_any(sentence, NEGATION_CUES):
                continue
            after = soften_causal(sentence)
            if after == sentence:
                continue
            quote = sentence[:300]
            evidence = [make_evidence("text", paragraph["paragraph_id"], quote)]
            if design_sentence:
                evidence.append(make_evidence(
                    "text", "方法/设计段落", design_sentence))
            findings.append(make_finding(
                "D6", "major", {
                    "chapter": item["chapter"],
                    "chapter_title": item["chapter_title"],
                    "section": item["section"],
                    "paragraph_id": paragraph["paragraph_id"],
                }, quote,
                "结论使用因果动词，但研究设计为关联性设计，表述超出数据允许的范围",
                evidence,
                "把因果动词收窄为关联表述，并说明不能据此推断因果",
                {"type": "narrow", "before": quote, "after": after},
                mode == "revise"))
            used_text.add(kit.compact(quote))
            if len(findings) >= MAX_PER_DIMENSION:
                break
        if len(findings) >= MAX_PER_DIMENSION:
            break

    if len(findings) < MAX_PER_DIMENSION:
        for item in conclusion:
            paragraph = item["paragraph"]
            text = paragraph.get("text") or ""
            for sentence in kit.split_sentences(text):
                if not kit.contains_any(sentence, UNIVERSAL_CUES):
                    continue
                if kit.contains_any(sentence, SCOPE_CUES):
                    continue
                after = soften_universal(sentence)
                if after == sentence:
                    continue
                quote = sentence[:300]
                findings.append(make_finding(
                    "D6", "minor", {
                        "chapter": item["chapter"],
                        "chapter_title": item["chapter_title"],
                        "section": item["section"],
                        "paragraph_id": paragraph["paragraph_id"],
                    }, quote,
                    "结论使用全称量词，未限定样本或研究条件",
                    [make_evidence("text", paragraph["paragraph_id"], quote)],
                    "给全称表述加上样本/条件限定，或改为所观察范围内的表述",
                    {"type": "narrow", "before": quote, "after": after},
                    mode == "revise"))
                if len(findings) >= MAX_PER_DIMENSION:
                    break
            if len(findings) >= MAX_PER_DIMENSION:
                break

    conclusion_text = join_text(conclusion)
    discussion_text = join_text(discussion)
    has_limits = (kit.contains_any(conclusion_text, LIMITATION_CUES)
                  or kit.contains_any(discussion_text, LIMITATION_CUES))
    if conclusion and not has_limits and len(findings) < MAX_PER_DIMENSION:
        last_item = conclusion[-1]
        paragraph = last_item["paragraph"]
        sentences = kit.split_sentences(paragraph.get("text") or "")
        if sentences:
            last = sentences[-1]
            boundary = ("本研究结论受样本与设计条件限制，外推需谨慎。"
                        if kit.detect_language(conclusion_text) == "zh"
                        else "These conclusions are limited by the sample and "
                             "design; generalization requires caution.")
            findings.append(make_finding(
                "D6", "minor", {
                    "chapter": last_item["chapter"],
                    "chapter_title": last_item["chapter_title"],
                    "section": last_item["section"],
                    "paragraph_id": paragraph["paragraph_id"],
                }, last,
                "结论与讨论均未交代研究限制，读者无法判断结论边界",
                [make_evidence("omission",
                               "结论/讨论章节；未检出局限、限制、外推或 future work")],
                "在结论末补一句样本与设计边界，不新增具体事实",
                {"type": "insert", "before": last, "after": boundary},
                mode == "revise"))

    basis = "已检查结论/讨论中的因果动词、全称量词与限制说明"
    if correlational:
        basis += "；方法部分为关联性设计"
    if findings:
        basis += "；检出 %d 处边界问题" % len(findings)
    else:
        basis += "；未发现因果越界、全称夸大或限制缺失"
    if not conclusion:
        basis = "未识别到结论章节，结论边界按讨论末段评估"
    return findings, basis, bool(paras)


def preserved_facts(paras, limit=40):
    facts = []
    seen = set()
    for item in paras:
        if item["function"] in {"references", "appendix"}:
            continue
        paragraph = item["paragraph"]
        picked = 0
        for sentence in kit.split_sentences(
                paragraph.get("text") or ""):
            for token in sorted(kit.quantity_tokens(sentence)):
                key = (token, paragraph["paragraph_id"])
                if key in seen:
                    continue
                seen.add(key)
                match = re.match(r"^([-+]?\d+(?:\.\d+)?)(.*)$", token)
                facts.append({
                    "fact_id": "PF-%02d" % (len(facts) + 1),
                    "value": match.group(1) if match else token,
                    "unit": match.group(2) if match else "",
                    "token": token,
                    "context": kit.truncate(sentence, 200),
                    "locator": paragraph["paragraph_id"],
                    "chapter": item["chapter"],
                    "section": item["section"],
                    "quote": sentence[:240],
                    "note": "审稿未改动",
                })
                picked += 1
                if picked >= 2 or len(facts) >= limit:
                    break
            if picked >= 2 or len(facts) >= limit:
                break
        if len(facts) >= limit:
            break
    return facts


def build_evidence_map(findings, manuscript_path):
    entries = []
    for finding in findings:
        location = finding.get("location") or {}
        locator = "/".join(str(value) for value in (
            location.get("chapter"), location.get("section"),
            location.get("paragraph_id")) if value)
        base = {
            "claim_id": finding.get("finding_id"),
            "claim": finding.get("problem") or "",
            "source": manuscript_path or "manuscript",
            "locator": locator,
            "quote": location.get("quote") or "",
        }
        evidence = finding.get("evidence") or []
        if not evidence:
            entries.append(base)
            continue
        for index, item in enumerate(evidence, 1):
            entry = dict(base)
            entry["evidence_id"] = "%s-E%d" % (
                finding.get("finding_id"), index)
            entry["kind"] = item.get("kind")
            entry["locator"] = item.get("locator") or locator
            entry["quote"] = item.get("quote") or base["quote"]
            entries.append(entry)
    return entries


def build_patterns(findings):
    patterns = []
    by_dimension = OrderedDict()
    for finding in findings:
        by_dimension.setdefault(finding["dimension"], []).append(finding)
    for dimension, items in by_dimension.items():
        if len(items) < 2:
            continue
        patterns.append({
            "pattern_id": "P-%02d" % (len(patterns) + 1),
            "dimension": dimension,
            "description": "%s 在稿件中出现 %d 次，属于同类结构问题"
                           % (DIMENSIONS.get(dimension, dimension),
                              len(items)),
            "occurrences": len(items),
            "finding_ids": [item["finding_id"] for item in items],
        })
    return patterns


def _dimension_entry(dim, name, findings, basis, assessed):
    dim_findings = [item for item in findings if item["dimension"] == dim]
    if not assessed:
        verdict = "not_assessed"
    elif any(item["severity"] in {"major", "blocker"} for item in dim_findings):
        verdict = "needs_revision"
    elif dim_findings:
        verdict = "minor_issues"
    else:
        verdict = "ok"
    return {
        "id": dim,
        "name": name,
        "verdict": verdict,
        "findings": len(dim_findings),
        "basis": basis,
    }


def build_review(inventory, workorder, map_data):
    manuscript = inventory.get("manuscript") or {}
    source = manuscript.get("source_abs_path") or ""
    if not manuscript or not source or not Path(source).is_file():
        return error_review(workorder, "没有可读稿件，无法进行六维诊断")
    try:
        map_data = kit.build_map(Path(source), manuscript.get("discovery",
                                                              "explicit"))
    except Exception as exc:  # noqa: BLE001
        return error_review(workorder, "稿件解析失败：%s" % exc)
    kit.write_json(Path(workorder["_work_dir"]) / "structure-map.json",
                      map_data)
    paras = list(iter_paragraphs(map_data))
    mode = workorder.get("mode")
    checks = OrderedDict()
    for dim, fn in (
            ("D1", lambda: check_d1(paras, mode)),
            ("D2", lambda: check_d2(map_data, paras, workorder, mode)),
            ("D3", lambda: check_d3(paras, map_data, mode)),
            ("D4", lambda: check_d4(paras, map_data, mode)),
            ("D5", lambda: check_d5(paras, mode)),
            ("D6", lambda: check_d6(paras, mode))):
        try:
            findings, basis, assessed = fn()
        except Exception as exc:  # noqa: BLE001
            findings, basis, assessed = [], "规则执行失败：%s" % exc, False
        checks[dim] = {
            "findings": findings,
            "basis": basis,
            "assessed": assessed,
        }

    all_findings = []
    for dim in DIMENSIONS:
        for finding in checks[dim]["findings"][:MAX_PER_DIMENSION]:
            finding["finding_id"] = "F-%02d" % (len(all_findings) + 1)
            all_findings.append(finding)
    dimensions = []
    not_assessed = []
    for dim, name in DIMENSIONS.items():
        assessed = checks[dim]["assessed"]
        dimensions.append(_dimension_entry(
            dim, name, all_findings, checks[dim]["basis"], assessed))
        if not assessed:
            not_assessed.append({
                "dimension": dim,
                "reason": checks[dim]["basis"] or "材料不足，无法评估该维度",
            })
    status = "completed" if not not_assessed else "partial"

    manuscript_info = {
        "path": manuscript.get("path") or "",
        "format": manuscript.get("format") or "text",
        "language": manuscript.get("language") or "unknown",
        "sections": len(map_data.get("chapters") or []),
        "paragraphs": len(paras),
        "word_count": (map_data.get("manuscript") or {}).get("word_count", 0),
    }
    uncertainty = []
    for item in map_data.get("uncertainty") or []:
        uncertainty.append({
            "item": item.get("item") or "structure parsing",
            "reason": item.get("reason") or "",
            "impact": item.get("impact") or "",
            "next_check": item.get("next_check") or "",
        })
    for item in inventory.get("uncertainty") or []:
        uncertainty.append(dict(item))
    uncertainty.append({
        "item": "heuristic_diagnosis",
        "reason": "D1-D6 由可观察锚点启发式检出，不是学术对错裁决",
        "impact": "每条 finding 都需作者按 location/quote 复核后采纳",
        "next_check": "逐条回到原文核对 quote 与 edit.before",
    })
    limitations = [
        "自动诊断只覆盖可观察的章节、段落、重复与表述信号，不评价学术贡献的真实性。",
        "findings 是带原文锚点的候选问题；作者可采纳、部分采纳或驳回。",
        "未改动原稿；review-only 模式不输出 revised.md。",
    ]
    if not all_findings:
        limitations.append(
            "本稿未检出六维问题；这不等于论文完美，仍建议人工通读。")
    facts = preserved_facts(paras)
    if not facts:
        limitations.append(
            "稿件中未提取到可定位的数值事实；preserved_facts 为空。")

    priority_order = [
        item["finding_id"] for item in sorted(
            all_findings,
            key=lambda item: (SEVERITY_RANK.get(item["priority"], 9),
                              item["finding_id"]))
    ]
    review = {
        "schema": "structure-review/v0.6",
        "skill_version": kit.VERSION,
        "case_id": workorder.get("case_id") or "unknown-case",
        "task_family": "structure-review",
        "status": status,
        "mode": "revise" if mode == "revise" else "review_only",
        "manuscript": manuscript_info,
        "dimensions": dimensions,
        "findings": all_findings,
        "patterns": [],
        "priority_order": priority_order,
        "artifacts": [{"role": "review", "path": "structure-review.json",
                       "format": "json"}],
        "not_assessed": not_assessed,
        "uncertainty": uncertainty,
        "change_log": [],
        "preserved_facts": facts,
        "evidence_map": [],
        "limitations": limitations,
        "diagnosis": {
            "engine": kit.TOOL,
            "version": kit.VERSION,
            "rules": {
                dim: {"assessed": checks[dim]["assessed"],
                      "findings": len(checks[dim]["findings"]),
                      "basis": checks[dim]["basis"]}
                for dim in DIMENSIONS
            },
            "section_outline": [
                {
                    "chapter": chapter_label(chapter),
                    "title": chapter.get("title") or "",
                    "function": chapter_function(chapter),
                    "word_count": chapter.get("word_count") or 0,
                    "proportion": chapter.get("proportion") or 0.0,
                }
                for chapter in map_data.get("chapters") or []
            ],
        },
    }
    review["patterns"] = build_patterns(all_findings)
    review["evidence_map"] = build_evidence_map(
        all_findings, manuscript_info["path"])
    return review


def error_review(workorder, reason):
    dimensions = [{
        "id": dim,
        "name": name,
        "verdict": "not_assessed",
        "findings": 0,
        "basis": reason,
    } for dim, name in DIMENSIONS.items()]
    return {
        "schema": "structure-review/v0.6",
        "skill_version": kit.VERSION,
        "case_id": workorder.get("case_id") or "unknown-case",
        "task_family": "structure-review",
        "status": "input_error",
        "mode": ("revise" if workorder.get("mode") == "revise"
                 else "review_only"),
        "manuscript": {"path": "", "format": "", "language": "",
                       "sections": 0, "paragraphs": 0, "word_count": 0},
        "dimensions": dimensions,
        "findings": [],
        "patterns": [],
        "priority_order": [],
        "artifacts": [{"role": "review", "path": "structure-review.json",
                       "format": "json"}],
        "not_assessed": [{"dimension": dim, "reason": reason}
                         for dim, _ in DIMENSIONS.items()],
        "uncertainty": [{
            "item": "input_error",
            "reason": reason,
            "impact": "no structure finding can be grounded",
            "next_check": "provide a readable manuscript and rerun",
        }],
        "change_log": [{
            "finding_id": "none",
            "action": "未改动原稿；输入不可读，未产生诊断",
            "diff": "无",
            "applied": False,
        }],
        "preserved_facts": [],
        "evidence_map": [],
        "limitations": [reason],
    }


def run(work_dir, mode_override=None):
    work_dir = Path(work_dir)
    inventory = load_json(work_dir / "inventory.json", default={}) or {}
    workorder = load_json(work_dir / "workorder.json", default={}) or {}
    if not workorder:
        raise ValueError("workorder.json missing; run workorder first")
    if mode_override:
        workorder["mode"] = "revise" if mode_override == "revise" else "review_only"
        workorder["revision_requested"] = workorder["mode"] == "revise"
        workorder.setdefault("mode_basis", []).append(
            "diagnose override=%s" % mode_override)
    workorder["_work_dir"] = str(work_dir)
    review = build_review(inventory, workorder, {})
    workorder.pop("_work_dir", None)
    if review.get("status") != "input_error":
        review["mode"] = ("revise" if workorder.get("mode") == "revise"
                          else "review_only")
    kit.write_json(work_dir / "diagnosis.json", review)
    return review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--mode", choices=["review", "revise"], default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    review = run(args.work, mode_override=args.mode)
    summary = {
        "status": review["status"],
        "mode": review.get("mode"),
        "findings": len(review.get("findings") or []),
        "dimensions": {item["id"]: item["verdict"]
                       for item in review.get("dimensions") or []},
        "diagnosis": str(Path(args.work) / "diagnosis.json"),
        "structure_map": str(Path(args.work) / "structure-map.json"),
    }
    if args.json:
        print(json.dumps(review, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
