#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deck planning and spec validation.

The model may author work/deck-spec.json (preferred).  When it does not, this
module builds a source-grounded outline from the real evidence index: every
sentence, number and figure keeps its file + locator.  Nothing is invented.
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

import discover

DEFAULT_PARTS = Path(__file__).resolve().parent.parent / "references" / "deck-parts.json"
DEFAULT_RULES = {
    "min_slides": 4, "max_slides": 30, "max_bullets": 5,
    "max_chars_per_bullet": 120, "max_title_units": 64,
    "notes_min_units": 20, "result_slide_ratio": 0.3,
    "result_ratio_min": 0.2, "method_ratio_max": 0.35,
    "required_roles_generate": ["title", "background", "method", "result", "conclusion"],
    "allowed_kinds": ["title", "section", "content", "stat", "stats", "metrics",
                      "timeline", "steps", "process", "comparison", "two-column",
                      "cards", "grid", "closing", "end", "quote", "statement",
                      "image-left", "image-right"],
}
SECTION_ORDER = ["intro", "methods", "results", "discussion", "limitations", "conclusion"]


def load_parts(path=None):
    """Load references/deck-parts.json; code falls back to the same defaults."""
    source = Path(path) if path else DEFAULT_PARTS
    data = {}
    try:
        loaded = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    rules = dict(DEFAULT_RULES)
    rules.update(data.get("rules") or {})
    data["rules"] = rules
    data.setdefault("parts", {})
    data["_source"] = str(source)
    return data
SECTION_LABEL_EN = {"abstract": "Abstract", "intro": "Background", "methods": "Methods",
                    "results": "Results", "discussion": "Discussion",
                    "limitations": "Limitations", "conclusion": "Conclusion",
                    "references": "References", "body": "Body"}


def _L(lang, zh, en):
    return zh if lang == "zh" else en


def cjk_len(text):
    return sum(2 if ord(ch) > 127 else 1 for ch in str(text))


def clip(text, limit):
    text = re.sub(r"\s+", " ", str(text)).strip()
    if cjk_len(text) <= limit:
        return text
    out, units = [], 0
    for ch in text:
        w = 2 if ord(ch) > 127 else 1
        if units + w > limit - 1:
            break
        out.append(ch)
        units += w
    return "".join(out).rstrip(" ,;，；、") + "…"


def clean_sentence(text):
    text = re.sub(r"\[[0-9,\-\s]+\]", "", str(text))
    text = re.sub(r"\((?:see|e\.g\.|i\.e\.)[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def first_clause(text, limit=46):
    text = clean_sentence(text)
    parts = re.split(r"[。！？!?]|;\s*|；", text)
    clause = parts[0].strip() if parts else text
    if cjk_len(clause) > limit:
        subs = [s for s in re.split(r"[,，:：]\s*", clause) if s.strip()]
        first = subs[0] if subs else clause
        if 10 <= cjk_len(first) <= limit * 1.8:
            clause = clip(first, limit)
        else:
            for sub in subs[1:]:
                if 10 <= cjk_len(sub) <= limit:
                    clause = sub
                    break
            else:
                clause = clip(clause, limit)
    return clause.rstrip(".,;:，。；：")


def action_title(text, fallback):
    title = first_clause(text, 48)
    title = re.sub(r"^(Here,?\s+we|We|Our (study|results?|analysis)|This (study|paper)|"
                   r"本文|本研究|我们)\s*", "", title, flags=re.I).strip()
    if not title or cjk_len(title) < 6:
        return clip(fallback, 48)
    return title


def split_bullets(text, max_items=3, max_chars=96):
    text = clean_sentence(text)
    chunks = re.split(r"(?<=[。！？!?;；])\s*|;\s*|；", text)
    chunks = [c.strip(" .;；。") for c in chunks if c.strip(" .;；。")]
    if not chunks:
        return []
    out = []
    for chunk in chunks:
        if cjk_len(chunk) <= max_chars:
            out.append(chunk)
        else:
            for sub in re.split(r"[,，]\s*", chunk):
                if cjk_len(sub) >= 8:
                    out.append(clip(sub, max_chars) if cjk_len(sub) > max_chars + 10 else sub)
        if len(out) >= max_items:
            break
    return out[:max_items]


STAT_STOP = {"the", "a", "an", "of", "to", "in", "for", "at", "by", "with", "and",
             "or", "versus", "vs", "compared", "from", "on", "after", "before",
             "over", "under", "was", "were", "is", "are", "reached", "achieved",
             "decreased", "increased", "remained", "maintained", "showed", "than",
             "more", "less", "approximately", "about", "only", "also", "respectively"}


def number_stats(text, max_stats=3):
    """Extract value/label pairs; labels must be literal nearby words."""
    stats = []
    pattern = re.compile(
        r"(\d+(?:[.,]\d+)?\s*(?:wt%|at%|vol%|mol%|%|‰|°C|K|h|min|s|ms|nm|µm|μm|mm|cm|"
        r"mL|µL|μL|L|mg|g|kg|M|mM|µM|μM|eV|keV|MeV|V|mA|W|Hz|kHz|MHz|GHz|"
        r"Da|kDa|bp|kb|GPa|MPa|Pa))(?![A-Za-z])"
        r"\s*([A-Za-z一-鿿][A-Za-z一-鿿 \-]{1,48})?", re.I)
    for m in pattern.finditer(text):
        value = m.group(1).strip()
        tail = (m.group(2) or "").strip(" -,;:：")
        words = re.split(r"\s+", tail) if tail else []
        while words and words[0].lower() in STAT_STOP:
            words.pop(0)
        cleaned = []
        for word in words:
            if word.lower() in ("at", "in", "on", "for", "versus", "vs", "compared",
                                "after", "before", "over", "under", "with", "by"):
                break
            cleaned.append(word)
        label = " ".join(cleaned).strip(" -,;:：")
        if not label:
            prefix = text[:m.start()]
            words = [w for w in re.split(r"[\s,;:，；：]+", prefix)
                     if w and w.lower() not in STAT_STOP and not re.fullmatch(r"[\d.%]+", w)]
            after = text[m.end():]
            following = [w for w in re.split(r"[\s,;:，；：]+", after)
                         if w and w.lower() not in STAT_STOP and not re.fullmatch(r"[\d.%]+", w)]
            label = " ".join(words[-2:] or following[:2])
        if not label or len(label) > 34 or label.lower() in ("wt", "w", "h", "s"):
            continue
        stats.append({"value": value, "label": label})
        if len(stats) >= max_stats:
            break
    return stats


def _pick(pool, n, used, max_similar=0.62):
    picked = []
    for item in sorted(pool, key=lambda e: -float(e.get("score", 0))):
        if item["eid"] in used:
            continue
        if any(discover.similar(item["text"], p["text"]) for p in picked):
            continue
        picked.append(item)
        used.add(item["eid"])
        if len(picked) >= n:
            break
    return picked


def _source_ref(item):
    return {"path": item["path"], "locator": item["locator"]}


def _footer(item):
    return f"{Path(item['path']).name} · {item['locator']}"


def _notes_for(lead, support, item, lang="zh"):
    pieces = []
    if lead:
        pieces.append(_L(lang, "讲稿：", "Speaker notes: ") + clean_sentence(lead))
    if support:
        pieces.append(_L(lang, "补充：", "Support: ")
                      + _L(lang, "；", "; ").join(clean_sentence(s) for s in support[:2]))
    pieces.append(_L(lang, f"依据：{Path(item['path']).name} {item['locator']}。",
                     f"Evidence: {Path(item['path']).name} {item['locator']}."))
    return " ".join(pieces)


def _paper_title(brief):
    raw = brief.get("raw_case") or {}
    for key in ("paper_title", "title_zh", "title", "name"):
        value = raw.get(key)
        if isinstance(value, str) and 6 <= len(value) <= 180:
            return value.strip()
    for source in brief.get("sources", []):
        for locator, chunk in source["pages"][:2]:
            for line in chunk.splitlines():
                line = re.sub(r"\s+", " ", line).strip()
                if 12 <= len(line) <= 180 and not re.match(
                        r"^(abstract|摘要|keywords|doi|http|www)", line, re.I):
                    return line
    task_title = brief.get("title")
    if task_title and "生成与编辑" not in task_title:
        return task_title
    if brief.get("sources"):
        return Path(brief["sources"][0]["path"]).stem.replace("_", " ")
    return task_title or "学术汇报"


def _agenda_names(buckets, lang="zh"):
    names = []
    labels = discover.SECTION_LABEL if lang == "zh" else SECTION_LABEL_EN
    for section in ("intro", "methods", "results", "discussion", "limitations", "conclusion"):
        if buckets.get(section):
            names.append(labels.get(section, section))
    return names[:6]


def _figure_for(figures, slide_no, used):
    for fig in figures:
        path = fig.get("path")
        if path and path not in used:
            used.add(path)
            locator = fig.get("source_locator", "file")
            src = fig.get("source_path", path)
            return fig, f"{Path(src).name} · {locator}（原始图，未重绘）"
    return None, None


def _chart_from_table(table, out_dir):
    try:
        return _chart_from_table_impl(table, out_dir)
    except Exception:
        return None


def _chart_from_table_impl(table, out_dir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception:
        return None
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    header = [str(c) for c in table.get("columns", [])]
    rows = table.get("rows", [])
    if len(header) < 2 or len(rows) < 2:
        return None

    def num(value):
        try:
            return float(str(value).replace(",", "").replace("%", ""))
        except (TypeError, ValueError):
            return None

    numeric_cols = []
    for j in range(len(header)):
        vals = [num(r[j]) for r in rows if j < len(r)]
        if sum(v is not None for v in vals) >= max(2, len(rows) // 2):
            numeric_cols.append(j)
    label_col = next((j for j in range(len(header)) if j not in numeric_cols), None)
    if label_col is None or not numeric_cols:
        return None
    labels = [str(r[label_col]) for r in rows if label_col < len(r)]
    series = []
    for j in numeric_cols[:2]:
        vals = [num(r[j]) if j < len(r) else None for r in rows]
        if sum(v is not None for v in vals) >= 2:
            series.append((header[j], vals))
    if not series:
        return None
    names = {f.name for f in font_manager.fontManager.ttflist}
    font = next((n for n in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
                             "WenQuanYi Zen Hei", "DejaVu Sans") if n in names), "DejaVu Sans")
    plt.rcParams.update({"font.family": font, "pdf.fonttype": 42})
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=180)
    x = list(range(len(labels)))
    if len(labels) <= 8:
        width = 0.8 / max(1, len(series))
        for k, (name, vals) in enumerate(series):
            clean_vals = [v if v is not None else 0 for v in vals]
            ax.bar([i + (k - (len(series) - 1) / 2) * width for i in x],
                   clean_vals, width=width, label=name)
    else:
        for name, vals in series:
            ax.plot(x, [v if v is not None else float("nan") for v in vals],
                    marker="o", linewidth=1.6, label=name)
    ax.set_xticks(x)
    ax.set_xticklabels([clip(l, 18) for l in labels], fontsize=7, rotation=25, ha="right")
    ax.set_ylabel(series[0][0], fontsize=8)
    ax.tick_params(axis="y", labelsize=7)
    ax.legend(fontsize=7, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)
    fig.tight_layout()
    dest = out_dir / "chart-from-table.png"
    fig.savefig(dest, dpi=180)
    plt.close(fig)
    cols = ", ".join([header[label_col]] + [name for name, _ in series])
    return {"path": str(dest), "source_path": table["path"],
            "source_locator": table.get("locator", ""),
            "caption": f"{Path(table['path']).name} · {table.get('locator','')} · "
                       f"按真实数据表重绘（{cols}）",
            "kind": "chart-from-table"}


def auto_plan(brief, work_dir, parts=None):
    """Create a complete, evidence-grounded deck spec.  No invented facts."""
    parts = parts or load_parts()
    rules = parts.get("rules") or DEFAULT_RULES
    max_bullets = int(rules.get("max_bullets", 5))
    buckets = defaultdict(list)
    for item in brief.get("evidence", []):
        buckets[item.get("section", "body")].append(item)
    used = set()
    target = int(brief.get("slide_count") or 12)
    target = max(int(rules.get("min_slides", 4)),
                 min(int(rules.get("max_slides", 30)), target))
    language = brief.get("language") or "zh"
    title = _paper_title(brief)
    steps_names = _agenda_names(buckets, language)
    slides = []
    warnings = []

    # title
    subtitle_bits = []
    if brief.get("audience"):
        subtitle_bits.append(brief["audience"])
    if brief.get("duration_min"):
        subtitle_bits.append(_L(language, f"{brief['duration_min']} 分钟",
                                f"{brief['duration_min']} min"))
    subtitle_bits.append(_L(language, f"{target} 页", f"{target} slides"))
    subtitle = " · ".join(subtitle_bits)
    first_source = brief.get("sources", [{}])[0] if brief.get("sources") else {}
    first_ref = None
    if first_source.get("path"):
        first_ref = {"path": first_source["path"],
                     "locator": (first_source.get("pages") or [["file", ""]])[0][0]}
    slides.append({
        "kind": "title", "role": "title", "title": title,
        "subtitle": subtitle,
        "eyebrow": "ACADEMIC BRIEFING" if language != "zh" else "学术汇报",
        "source": (f"{Path(first_source['path']).name}" if first_source.get("path") else ""),
        "notes": _L(language,
                    f"本次汇报围绕「{title}」展开。主线："
                    + ("、".join(steps_names) if steps_names else "背景、方法、结果与结论")
                    + "。每页数字与图号均来自源材料，页脚给出定位。",
                    f"This briefing presents \"{title}\". Main line: "
                    + (" / ".join(steps_names) if steps_names else "background, methods, results, conclusion")
                    + ". Every number and figure locator traces back to the supplied material."),
        "claims": [], "source_ref": first_ref,
    })

    # agenda
    if target >= 9 and steps_names:
        slides.append({
            "kind": "steps", "role": "agenda",
            "title": _L(language, "汇报结构：从问题到证据", "Structure: from question to evidence"),
            "steps": [{"title": name, "text": ""} for name in steps_names],
            "notes": _L(language,
                        "先交代研究问题与方法，再逐条给出结果，最后讨论局限与结论。"
                        "页序对应源材料的论证顺序。",
                        "We first state the question and methods, then walk through results, "
                        "and finish with limitations and conclusions. The order follows the source."),
            "source": _L(language, "汇报大纲（由源材料章节生成）",
                         "Outline derived from source sections"),
            "claims": [],
        })

    # research question is selected before background so its sentence is not
    # consumed as a background bullet
    question_pool = [e for e in brief.get("evidence", [])
                     if "?" in e["text"] or "？" in e["text"]
                     or discover.QUESTION_CUES.search(e["text"])]
    qids = {e["eid"] for e in question_pool}

    # background: prefer intro/abstract context, never result-only sentences
    bg_pool = []
    for e in (buckets.get("intro", []) + buckets.get("abstract", [])
              + buckets.get("body", [])):
        if e["eid"] in qids:
            continue
        if discover.RESULT_CUES.search(e["text"]) and "?" not in e["text"]:
            continue
        adjusted = dict(e)
        adjusted["score"] = (float(e.get("score", 0))
                             + (1.6 if e.get("section") == "intro" else 0.0)
                             - (1.0 if e.get("section") == "body" else 0.0))
        bg_pool.append(adjusted)
    bg = _pick(bg_pool, 4, used)
    if not bg:
        bg = _pick([dict(e) for e in buckets.get("abstract", []) + buckets.get("intro", [])
                    if e["eid"] not in qids], 4, used)
    if bg:
        lead, support = bg[0], bg[1:3]
        slides.append({
            "kind": "content", "role": "background",
            "title": action_title(lead["text"], _L(language, "研究背景与问题", "Background and question")),
            "bullets": split_bullets(lead["text"], 1) + [b for item in support for b in split_bullets(item["text"], 1)],
            "callout": {"title": _L(language, "为什么重要", "Why it matters"),
                        "bullets": split_bullets(support[0]["text"], 1) if support else []},
            "source": _footer(lead),
            "notes": _notes_for(lead["text"], [s["text"] for s in support], lead, language),
            "claims": [{"claim": clean_sentence(x["text"]), "source": _source_ref(x)} for x in bg],
            "source_ref": _source_ref(lead),
        })

    # research question
    q = _pick(question_pool, 1, used) or (bg[:1])
    if q:
        item = q[0]
        slides.append({
            "kind": "statement", "role": "background", "subrole": "question",
            "title": action_title(item["text"], _L(language, "研究问题", "Research question")),
            "quote": clip(clean_sentence(item["text"]), 180),
            "attribution": _footer(item),
            "source": _footer(item),
            "notes": _L(language,
                        "本页只陈述研究问题，不引入材料外结论。"
                        + f"依据：{item['locator']}。",
                        "This page states the research question only; no claim goes beyond "
                        + f"the supplied material. Evidence: {item['locator']}."),
            "claims": [{"claim": clean_sentence(item["text"]), "source": _source_ref(item)}],
            "source_ref": _source_ref(item),
        })

    # methods
    method_pool = buckets.get("methods", []) + [e for e in buckets.get("body", [])
                                                if discover.METHOD_CUES.search(e["text"])]
    methods = _pick(method_pool, 4, used)
    if methods:
        steps = []
        for item in methods[:4]:
            steps.append({"title": clip(action_title(item["text"], _L(language, "方法步骤", "Method step")), 34),
                          "text": clip(clean_sentence(item["text"]), 110)})
        slides.append({
            "kind": "steps", "role": "method",
            "title": _L(language, "方法路线：可复现的关键步骤",
                        "Method pipeline: reproducible steps"),
            "steps": steps,
            "source": _footer(methods[0]),
            "notes": _notes_for(methods[0]["text"], [m["text"] for m in methods[1:3]],
                                methods[0], language),
            "claims": [{"claim": clean_sentence(m["text"]), "source": _source_ref(m)} for m in methods],
            "source_ref": _source_ref(methods[0]),
        })

    # results
    result_pool = buckets.get("results", []) + [e for e in brief.get("evidence", [])
                                                if discover.RESULT_CUES.search(e["text"])
                                                and e["section"] not in ("methods", "references")]
    result_groups = []
    for item in sorted(result_pool, key=lambda e: -float(e.get("score", 0))):
        if item["eid"] in used:
            continue
        if any(discover.similar(item["text"], g[0]["text"]) for g in result_groups):
            continue
        group = [item]
        for other in sorted(result_pool, key=lambda e: -float(e.get("score", 0))):
            if other["eid"] == item["eid"] or other["eid"] in used:
                continue
            if other["path"] != item["path"]:
                continue
            if discover.similar(other["text"], item["text"]):
                continue
            if other["locator"] == item["locator"] or len(group) < 3:
                group.append(other)
            if len(group) >= 3:
                break
        result_groups.append(group)
        for x in group:
            used.add(x["eid"])
        result_target = max(1, min(5, round(target * float(
            rules.get("result_slide_ratio", 0.3)))))
        if len(result_groups) >= result_target:
            break
    figures = list(brief.get("figures", []))
    used_figs = set()
    chart = None
    if not figures and brief.get("tables"):
        chart = _chart_from_table(brief["tables"][0], Path(work_dir) / "figures")
    for gi, group in enumerate(result_groups):
        lead = group[0]
        support = group[1:]
        entry = {
            "kind": "content", "role": "result",
            "title": action_title(lead["text"], _L(language, "结果页", "Result page")),
            "bullets": [b for item in group for b in split_bullets(item["text"], 1)][:max_bullets],
            "source": _footer(lead),
            "notes": _notes_for(lead["text"], [s["text"] for s in support], lead, language),
            "claims": [{"claim": clean_sentence(x["text"]), "source": _source_ref(x)} for x in group],
            "source_ref": _source_ref(lead),
        }
        if gi == 0 and chart:
            entry["image"] = chart["path"]
            entry["caption"] = chart["caption"]
            entry["figure_source"] = {"path": chart["source_path"],
                                      "locator": chart["source_locator"]}
        else:
            fig, caption = _figure_for(figures, gi, used_figs)
            if fig:
                entry["image"] = fig["path"]
                entry["caption"] = caption
                entry["figure_source"] = {"path": fig.get("source_path", fig["path"]),
                                          "locator": fig.get("source_locator", "file")}
        stats = number_stats(lead["text"], 3)
        if len(stats) >= 2 and not entry.get("image"):
            entry["kind"] = "stat"
            entry["stats"] = stats
        slides.append(entry)

    # discussion + limitations
    disc = _pick(buckets.get("discussion", []), 2, used)
    if disc:
        slides.append({
            "kind": "content", "role": "discussion",
            "title": action_title(disc[0]["text"], _L(language, "讨论：结果意味着什么",
                                                      "Discussion: what the results mean")),
            "bullets": [b for item in disc for b in split_bullets(item["text"], 2)][:4],
            "source": _footer(disc[0]),
            "notes": _notes_for(disc[0]["text"], [d["text"] for d in disc[1:]], disc[0], language),
            "claims": [{"claim": clean_sentence(d["text"]), "source": _source_ref(d)} for d in disc],
            "source_ref": _source_ref(disc[0]),
        })
    limits = _pick(buckets.get("limitations", []) + [e for e in buckets.get("discussion", [])
                                                     if discover.LIMIT_CUES.search(e["text"])],
                   2, used)
    if limits:
        slides.append({
            "kind": "content", "role": "limitations",
            "title": action_title(limits[0]["text"], _L(language, "局限与适用边界",
                                                        "Limitations and scope")),
            "bullets": [b for item in limits for b in split_bullets(item["text"], 2)][:4],
            "callout": {"title": _L(language, "不适用范围", "Out of scope"),
                        "bullets": [_L(language, "仅限源材料报告的条件与样本",
                                       "Only the conditions and samples reported in the source")]},
            "source": _footer(limits[0]),
            "notes": _notes_for(limits[0]["text"], [l["text"] for l in limits[1:]], limits[0], language),
            "claims": [{"claim": clean_sentence(l["text"]), "source": _source_ref(l)} for l in limits],
            "source_ref": _source_ref(limits[0]),
        })
    else:
        warnings.append("no explicit limitations sentence found in the supplied material")

    # conclusion
    conclusion_pool = buckets.get("conclusion", []) + buckets.get("abstract", []) + buckets.get("results", [])
    concl = _pick(conclusion_pool, 3, used)
    if concl:
        slides.append({
            "kind": "closing", "role": "conclusion",
            "title": _L(language, "结论与下一步", "Conclusions and next steps"),
            "bullets": [clip(clean_sentence(item["text"]), 110) for item in concl[:3]],
            "source": _footer(concl[0]),
            "notes": _L(language,
                        "收束三条结论，均可在源材料对应位置回查。"
                        + f"依据：{concl[0]['locator']}。",
                        "Three closing points, each traceable to the supplied source. "
                        + f"Evidence: {concl[0]['locator']}."),
            "claims": [{"claim": clean_sentence(c["text"]), "source": _source_ref(c)} for c in concl],
            "source_ref": _source_ref(concl[0]),
        })

    # evidence map slide
    refs = []
    seen = set()
    for slide in slides:
        for claim in slide.get("claims", []):
            src = claim.get("source") or {}
            key = (src.get("path"), src.get("locator"))
            if key[0] and key not in seen:
                seen.add(key)
                refs.append(f"{Path(key[0]).name} · {key[1]}")
    if refs:
        slides.append({
            "kind": "content", "role": "evidence",
            "title": _L(language, "证据索引：每页内容可回查",
                        "Evidence map: every page is traceable"),
            "bullets": refs[:7],
            "body_size": 12,
            "source": _L(language, "源材料文件与定位", "Source files and locators"),
            "notes": _L(language,
                        "本页列出本次汇报引用的文件与定位；图源和数字均按此索引复核。",
                        "This page lists every file and locator used; figures and numbers "
                        "should be checked against this index."),
            "claims": [],
        })

    # trim / extend toward requested page count, never with empty pages.
    # Keep at least one page for every required role; drop optional/duplicate
    # pages in a fixed order instead of blindly deleting the background.
    required = set(rules.get("required_roles_generate") or [])
    while len(slides) > target:
        counts = defaultdict(int)
        for slide in slides:
            counts[str(slide.get("role") or "")] += 1
        removable = None
        for role in ("discussion", "agenda", "evidence"):
            if role in counts:
                removable = max(i for i, s in enumerate(slides)
                                if str(s.get("role") or "") == role)
                break
        if removable is None:
            for role in ("result", "background", "method"):
                minimum = 2 if role == "result" else 1
                if counts.get(role, 0) > minimum:
                    removable = max(i for i, s in enumerate(slides)
                                    if str(s.get("role") or "") == role)
                    break
        if removable is None:
            break
        slides.pop(removable)
    if len(slides) < target:
        extra_pool = [e for e in brief.get("evidence", [])
                      if e["eid"] not in used and e["section"] not in ("references",)]
        while len(slides) < target and extra_pool:
            extra = _pick(extra_pool, 2, used)
            if not extra:
                break
            item = extra[0]
            slide = {
                "kind": "content", "role": "background",
                "title": action_title(item["text"], _L(language, "补充证据", "Additional evidence")),
                "bullets": [b for x in extra for b in split_bullets(x["text"], 2)][:4],
                "source": _footer(item),
                "notes": _notes_for(item["text"], [x["text"] for x in extra[1:]], item, language),
                "claims": [{"claim": clean_sentence(x["text"]), "source": _source_ref(x)} for x in extra],
                "source_ref": _source_ref(item),
            }
            slides.insert(max(1, len(slides) - 2), slide)
            extra_pool = [e for e in extra_pool if e["eid"] not in used]

    spec = {
        "title": title,
        "subtitle": subtitle,
        "size": brief.get("aspect") or "16:9",
        "theme": {"name": "scholar"},
        "language": language,
        "audience": brief.get("audience"),
        "duration_min": brief.get("duration_min"),
        "source_files": [{"path": s["path"], "sha256": s["sha256"],
                          "chars": s["chars"]} for s in brief.get("sources", [])],
        "slides": slides,
        "planning": {
            "origin": "source-grounded auto-plan",
            "requested_slides": brief.get("slide_count"),
            "built_slides": len(slides),
            "warnings": warnings,
        },
    }
    return spec


# ------------------------------------------------------------------ spec I/O
def resolve_image_paths(spec, roots):
    roots = [Path(r) for r in roots if r]
    fixed = 0
    for slide in spec.get("slides", []) or []:
        img = slide.get("image")
        if not img:
            continue
        p = Path(str(img))
        if p.is_file():
            continue
        candidates = [r / p for r in roots] + [r / "materials" / p for r in roots]
        for candidate in candidates:
            if candidate.is_file():
                slide["image"] = str(candidate.resolve())
                fixed += 1
                break
    return fixed


def ensure_grounded_notes(spec):
    """Fill missing notes from the slide's own bullets + locator (never new facts)."""
    added = 0
    for slide in spec.get("slides", []) or []:
        if str(slide.get("notes") or "").strip():
            continue
        claims = slide.get("claims") or []
        bullets = slide.get("bullets") or []
        source = slide.get("source") or ""
        if bullets:
            slide["notes"] = "讲稿：" + "；".join(str(b) for b in bullets[:3]) + \
                             (f"。依据：{source}" if source else "。")
            added += 1
        elif claims:
            slide["notes"] = "讲稿：" + str(claims[0].get("claim", "")) + \
                             (f"。依据：{source}" if source else "。")
            added += 1
    return added


def normalize_spec(spec, brief, spec_path=None, parts=None):
    parts = parts or load_parts()
    rules = parts.get("rules") or DEFAULT_RULES
    allowed = set(rules.get("allowed_kinds") or DEFAULT_RULES["allowed_kinds"])
    max_bullets = int(rules.get("max_bullets", 5))
    max_bullet_chars = int(rules.get("max_chars_per_bullet", 120))
    max_title_units = int(rules.get("max_title_units", 64))
    issues, warnings = [], []
    if not isinstance(spec, dict):
        return None, ["spec is not a JSON object"], [], 0
    spec.setdefault("title", _paper_title(brief))
    spec.setdefault("size", brief.get("aspect") or "16:9")
    spec.setdefault("theme", {"name": "scholar"})
    slides = spec.get("slides")
    if not isinstance(slides, list) or not slides:
        return None, ["spec has no slides"], warnings, 0
    if len(slides) > int(rules.get("max_slides", 30)):
        warnings.append(f"{len(slides)} slides exceeds deck-parts max_slides")
    roots = [Path(spec_path).parent if spec_path else Path(brief["work_dir"]),
             Path(brief["work_dir"]), Path(brief["input_dir"]),
             Path(brief["input_dir"]) / "materials"]
    fixed_images = resolve_image_paths(spec, roots)
    for i, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            issues.append(f"slide {i}: not an object")
            continue
        kind = str(slide.get("kind") or slide.get("layout") or "content").lower()
        if kind not in allowed:
            warnings.append(f"slide {i}: unknown kind '{kind}', treated as content")
            slide["kind"] = kind = "content"
        slide.setdefault("role", kind)
        title = str(slide.get("title") or "").strip()
        if not title:
            issues.append(f"slide {i}: empty title")
        elif cjk_len(title) > max_title_units:
            warnings.append(f"slide {i}: long title ({cjk_len(title)} units)")
        role = str(slide.get("role") or kind)
        part_spec = (parts.get("parts") or {}).get(role) or {}
        role_max_bullets = int(part_spec.get("max_bullets", max_bullets))
        if kind not in ("title", "section", "closing", "end", "quote", "statement"):
            visual = any(slide.get(k) for k in
                         ("bullets", "stats", "steps", "cards", "left", "right", "image"))
            if not visual:
                issues.append(f"slide {i}: content page has no bullets/stats/visual")
            if isinstance(slide.get("bullets"), list):
                slide["bullets"] = [clip(b, max_bullet_chars)
                                    if cjk_len(b) > max_bullet_chars + 20 else b
                                    for b in slide["bullets"]]
                if len(slide["bullets"]) > role_max_bullets:
                    warnings.append(f"slide {i}: more than {role_max_bullets} bullets "
                                    f"for role {role}")
                    slide["bullets"] = slide["bullets"][:role_max_bullets]
        notes = str(slide.get("notes") or "").strip()
        if kind not in ("title", "section") and cjk_len(notes) < int(rules.get("notes_min_units", 20)):
            warnings.append(f"slide {i}: speaker notes shorter than "
                            f"{rules.get('notes_min_units', 20)} units")
        if slide.get("image") and not Path(str(slide["image"])).is_file():
            warnings.append(f"slide {i}: image missing: {slide['image']}")
            slide.pop("image", None)
        for claim in slide.get("claims") or []:
            src = claim.get("source") if isinstance(claim, dict) else None
            if not src or not src.get("path") or not src.get("locator"):
                warnings.append(f"slide {i}: claim without source/locator")
    roles = {str(s.get("role") or "") for s in slides}
    required = (rules.get("required_roles_generate") or []) if not brief.get("edits") else \
               (rules.get("required_roles_edit") or [])
    missing_roles = [r for r in required if r not in roles]
    if missing_roles and not brief.get("edits"):
        warnings.append(f"outline misses recommended role(s): {', '.join(missing_roles)}")
    result_count = sum(1 for s in slides if s.get("role") == "result")
    method_count = sum(1 for s in slides if s.get("role") == "method")
    if slides and result_count / len(slides) < float(rules.get("result_ratio_min", 0.2)):
        warnings.append("result slides below deck-parts result_ratio_min")
    if slides and method_count / len(slides) > float(rules.get("method_ratio_max", 0.35)):
        warnings.append("method slides above deck-parts method_ratio_max")
    if brief.get("slide_count"):
        if len(slides) != int(brief["slide_count"]):
            warnings.append(
                f"slide count {len(slides)} != requested {brief['slide_count']}")
    added_notes = ensure_grounded_notes(spec)
    spec.setdefault("planning", {})
    spec["planning"].update({
        "normalized": True,
        "parts_source": parts.get("_source"),
        "notes_filled_from_slide": added_notes,
        "images_relocated": fixed_images,
        "issues": issues,
        "warnings": warnings,
        "roles": sorted(roles),
    })
    return spec, issues, warnings, added_notes


def load_spec(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
