#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Split a source document into alignable segments and propose glossary candidates.

The script is deliberately conservative about structure: headings, fenced code,
display formulas, tables and list blocks are kept as single units, and ordinary
paragraphs are split only on blank lines.  It never reorders or rewrites text.

Glossary candidates are *suggestions* only.  A human or the agent must confirm
the translation in ``glossary.json`` before translating; the checker only warns
about candidates that never reached the locked glossary.

Usage:
    python3 segment_source.py --source /input/source.md \
        --out /app/work/segments.json \
        --candidates /app/work/glossary-candidates.json --json

Exit codes: 0 ok, 2 unreadable/empty input.
Stdlib only.
"""

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+\S")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+)")
TABLE_RE = re.compile(r"^\s*\|")
MATH_START_RE = re.compile(
    r"^\s*(?:\$\$|\\\[|\\begin\{(?:equation|align|eqnarray|gather|multline)\*?\})"
)
MATH_ENV_RE = re.compile(r"\\begin\{([^}]+)\}")
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")

STOP_ACRONYMS = {
    "A", "I", "AN", "AND", "AS", "AT", "BE", "BY", "DO", "ET", "FOR", "FROM",
    "FIG", "IN", "IS", "IT", "NO", "NOT", "OF", "ON", "OR", "THE", "TO", "VS",
    "WE", "ALL", "ANY", "ARE", "HAS", "HAVE", "THIS", "THAT", "THESE", "THOSE",
    "WAS", "WERE", "WITH", "PDF", "DOI", "URL", "HTTP", "HTTPS", "HTML", "JSON",
    "API", "SI", "MD", "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",
}

# Common Chinese phrases that are not useful glossary terms.  The list is
# intentionally short; candidates are reviewed before locking.
CJK_STOP = {
    "本文", "我们", "研究", "结果", "方法", "实验", "样品", "材料", "表面",
    "可以", "通过", "以及", "其中", "由于", "因此", "然而", "但是", "并且",
    "采用", "所有", "具有", "进行", "表明", "显示", "分析", "计算", "观察",
    "发现", "增加", "降低", "提高", "影响", "作用", "条件", "温度", "时间",
    "浓度", "结构", "性能", "一个", "一种", "不同", "相关", "主要", "显著",
    "这些", "那些", "该方", "该研", "其对", "与其", "在此", "同时", "分别",
    "进一步", "目前", "已经", "能够", "需要", "可能", "约为", "之间", "以上",
    "以下", "之后", "之前", "中的", "中的", "中的", "值为", "且在", "并对",
    "活性", "表征", "出现", "对应", "条件", "过程", "变化", "趋势", "结果",
}

# Leading function words / reporting phrases that the pair regex may swallow
# before a Chinese term.  Strip longest first.
PAIR_PREFIXES = [
    "本文采用", "本研究采用", "我们采用", "本文使用", "本研究使用",
    "采用", "使用", "利用", "基于", "通过", "本文", "本研究", "我们",
    "是", "为", "的", "与", "和", "及", "对", "在", "由", "将", "把",
]


def trim_pair_term(term):
    changed = True
    while changed:
        changed = False
        for prefix in PAIR_PREFIXES:
            if term.startswith(prefix) and len(term) - len(prefix) >= 2:
                term = term[len(prefix):]
                changed = True
                break
    return term

LATIN_STOP = {
    "the", "and", "with", "from", "that", "this", "these", "those", "were",
    "was", "are", "have", "has", "been", "using", "used", "result", "results",
    "sample", "samples", "figure", "table", "study", "studies", "however",
    "therefore", "which", "their", "there", "where", "while", "after",
    "before", "between", "during", "shows", "show", "observed", "compared",
    "respectively", "significant", "significantly", "increase", "increased",
    "decrease", "decreased", "higher", "lower", "also", "than", "into", "such",
}


def read_text(path):
    return Path(path).read_text(encoding="utf-8-sig")


def split_blocks(text):
    """Return blocks with 1-based line ranges.  Blank lines separate paragraphs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    blocks = []
    buf = []
    buf_start = None

    def flush(end_idx):
        nonlocal buf, buf_start
        if not buf:
            return
        while buf and not buf[-1].strip():
            buf.pop()
        if buf:
            blocks.append({
                "lines": list(buf),
                "line_start": (buf_start if buf_start is not None else 0) + 1,
                "line_end": end_idx,
            })
        buf = []
        buf_start = None

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            flush(i)
            i += 1
            continue

        if HEADING_RE.match(line):
            flush(i)
            blocks.append({"lines": [line.rstrip()], "line_start": i + 1, "line_end": i + 1})
            i += 1
            continue

        fence = FENCE_RE.match(line)
        if fence:
            flush(i)
            marker = fence.group(1)
            start = i
            buf = [line.rstrip("\n")]
            i += 1
            closing = re.compile(r"^\s*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$")
            while i < n:
                buf.append(lines[i].rstrip("\n"))
                if closing.match(lines[i]):
                    i += 1
                    break
                i += 1
            blocks.append({"lines": list(buf), "line_start": start + 1, "line_end": i})
            buf = []
            buf_start = None
            continue

        if MATH_START_RE.match(line) and not buf:
            flush(i)
            start = i
            stripped = line.strip()
            closing = None
            if stripped.startswith("$$"):
                closing = "$$"
            elif stripped.startswith("\\["):
                closing = "\\]"
            else:
                env = MATH_ENV_RE.match(stripped)
                if env:
                    closing = "\\end{" + env.group(1) + "}"
            one_line = closing == "$$" and stripped.count("$$") >= 2
            buf = [line.rstrip("\n")]
            i += 1
            while closing and not one_line and i < n:
                buf.append(lines[i].rstrip("\n"))
                if closing in lines[i]:
                    i += 1
                    break
                i += 1
            blocks.append({"lines": list(buf), "line_start": start + 1, "line_end": i})
            buf = []
            buf_start = None
            continue

        if not buf:
            buf_start = i
        buf.append(line.rstrip("\n"))
        i += 1

    flush(n)
    return blocks


def block_type(lines):
    first = lines[0].strip()
    if HEADING_RE.match(lines[0]):
        return "heading"
    if FENCE_RE.match(lines[0]):
        return "code"
    if first.startswith("$$") or first.startswith("\\[") or first.startswith("\\begin{"):
        return "formula"
    if TABLE_RE.match(lines[0]):
        return "table"
    if LIST_RE.match(lines[0]):
        return "list"
    return "paragraph"


def build_segments(text):
    segments = []
    for block in split_blocks(text):
        body = "\n".join(block["lines"]).strip("\n")
        if not body.strip():
            continue
        segments.append({
            "segment_id": "S%02d" % (len(segments) + 1),
            "type": block_type(block["lines"]),
            "line_start": block["line_start"],
            "line_end": block["line_end"],
            "source": body,
        })
    return segments


def add_candidate(store, term, kind, segment_id, confidence, suggested_target=None):
    term = term.strip()
    if not term:
        return
    key = term.casefold()
    rec = store.get(key)
    if rec is None:
        rec = {
            "term": term,
            "kind": kind,
            "count": 0,
            "segment_ids": [],
            "confidence": confidence,
        }
        if suggested_target:
            rec["suggested_target"] = suggested_target.strip()
        store[key] = rec
    rec["count"] += 1
    if segment_id not in rec["segment_ids"]:
        rec["segment_ids"].append(segment_id)
    order = {"high": 3, "medium": 2, "low": 1}
    if order.get(confidence, 0) > order.get(rec.get("confidence", "low"), 0):
        rec["confidence"] = confidence


def extract_candidates(segments, min_count, max_candidates):
    store = OrderedDict()

    # 1) Acronyms: always proposed, even with one occurrence, because an
    #    unexpanded or inconsistently translated acronym is a real defect.
    for seg in segments:
        for match in re.finditer(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,7}(?![A-Za-z0-9])", seg["source"]):
            token = match.group(0)
            if token in STOP_ACRONYMS or token.isdigit():
                continue
            add_candidate(store, token, "acronym", seg["segment_id"], "high")

    # 2) Explicit bilingual glosses such as "氧空位（oxygen vacancy）".
    pair_re = re.compile(
        r"([\u4e00-\u9fff]{2,12})\s*[（(]\s*([A-Za-z][A-Za-z0-9 \-/]{1,60}?)\s*[)）]"
    )
    for seg in segments:
        for match in pair_re.finditer(seg["source"]):
            term = trim_pair_term(match.group(1))
            if len(term) < 2:
                continue
            add_candidate(
                store, term, "bilingual_pair", seg["segment_id"], "high",
                suggested_target=match.group(2),
            )

    # 3) Repeated Chinese n-grams (2-4 chars).
    counter = Counter()
    seg_ids = defaultdict(set)
    for seg in segments:
        for run in CJK_RUN_RE.findall(seg["source"]):
            for size in (2, 3, 4):
                for j in range(len(run) - size + 1):
                    gram = run[j:j + size]
                    if gram in CJK_STOP:
                        continue
                    if len(gram) == 2 and any(ch in "的了和与在为中该其此等及或" for ch in gram):
                        continue
                    counter[gram] += 1
                    seg_ids[gram].add(seg["segment_id"])

    ordered = sorted(counter.items(), key=lambda kv: (-len(kv[0]), -kv[1], kv[0]))
    selected = []
    for gram, count in ordered:
        if count < min_count:
            continue
        if any(gram in longer for longer, other_count in selected if other_count == count):
            continue
        selected.append((gram, count))
    for gram, count in selected[:25]:
        confidence = "medium" if count >= 3 else "low"
        for sid in sorted(seg_ids[gram]):
            add_candidate(store, gram, "cjk_ngram", sid, confidence, None)
        # add_candidate increments once per segment; fix the count to the
        # real occurrence count after all segments were added.
        store[gram.casefold()]["count"] = count

    # 4) Repeated Latin content words (length >= 6, at least 3 times).
    latin_counter = Counter()
    latin_case = {}
    latin_segments = defaultdict(set)
    for seg in segments:
        for match in re.finditer(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z\-]{5,}(?![A-Za-z0-9])", seg["source"]):
            token = match.group(0)
            key = token.casefold()
            if key in LATIN_STOP:
                continue
            latin_counter[key] += 1
            latin_case.setdefault(key, token)
            latin_segments[key].add(seg["segment_id"])
    for key, count in latin_counter.items():
        if count < 3:
            continue
        for sid in sorted(latin_segments[key]):
            add_candidate(store, latin_case[key], "latin_term", sid, "medium")
        store[key]["count"] = count

    candidates = sorted(
        store.values(),
        key=lambda c: ({"high": 0, "medium": 1, "low": 2}.get(c["confidence"], 3), -c["count"], c["term"]),
    )
    return candidates[:max_candidates]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="source document (.md/.txt)")
    parser.add_argument("--out", default=None, help="write segments JSON here")
    parser.add_argument("--candidates", default=None, help="write glossary candidates JSON here")
    parser.add_argument("--min-count", type=int, default=2, help="minimum repeats for Chinese n-grams")
    parser.add_argument("--max-candidates", type=int, default=40)
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args()

    try:
        text = read_text(args.source)
    except OSError as exc:
        payload = {"tool": "segment_source", "status": "error", "error": "cannot read source: %s" % exc}
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    if not text.strip():
        payload = {"tool": "segment_source", "status": "error", "error": "source is empty"}
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    segments = build_segments(text)
    if not segments:
        payload = {"tool": "segment_source", "status": "error", "error": "no alignable segments found"}
        print(json.dumps(payload, ensure_ascii=False))
        return 2

    candidates = extract_candidates(segments, args.min_count, args.max_candidates)
    type_counts = Counter(seg["type"] for seg in segments)
    report = {
        "tool": "segment_source",
        "version": "1.0.0",
        "status": "ok",
        "source": str(args.source),
        "count": len(segments),
        "type_counts": dict(sorted(type_counts.items())),
        "segments": segments,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "schema_version": "1.0",
            "source": str(args.source),
            "count": len(segments),
            "segments": segments,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        report["segments_written"] = str(out)

    if args.candidates:
        cand_path = Path(args.candidates)
        cand_path.parent.mkdir(parents=True, exist_ok=True)
        cand_path.write_text(json.dumps({
            "schema_version": "1.0",
            "source": str(args.source),
            "count": len(candidates),
            "candidates": candidates,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        report["candidates_written"] = str(cand_path)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("source: %s" % args.source)
        print("segments: %d %s" % (len(segments), dict(type_counts)))
        print("candidates: %d" % len(candidates))
        if args.out:
            print("segments written: %s" % args.out)
        if args.candidates:
            print("candidates written: %s" % args.candidates)
    return 0


if __name__ == "__main__":
    sys.exit(main())
