#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mind-map toolkit: stdlib-only helpers shared by this skill.

Used by extract_material.py, build_mindmap.py, check_mindmap.py and
make_result.py. No network, no third-party packages.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import zlib
from pathlib import Path

MAX_NODES_DEFAULT = 40
MAX_DEPTH_DEFAULT = 4
MIN_BRANCHES = 3
MAX_BRANCHES = 7

NODE_TYPES = ("topic", "concept", "question", "method", "evidence",
              "result", "claim", "risk", "gap", "task")
NODE_KINDS = ("reported", "task", "inferred")
IMPORTANCE = ("core", "supporting", "detail")
RELATIONS = ("supports", "contrasts", "depends", "explains", "causes",
             "precedes", "part_of", "conflicts", "cites", "link")

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".tex", ".rst", ".json",
                 ".csv", ".tsv", ".yaml", ".yml", ".html", ".htm", ".xml"}

BRANCH_SPECS = (
    ("background", "背景", "concept",
     ("background", "introduction", "intro", "motivation",
      "背景", "引言", "介绍", "概述", "研究背景")),
    ("problem", "研究问题", "question",
     ("problem", "question", "objective", "aim", "hypothesis", "gap",
      "问题", "目标", "假设", "科学问题", "研究目标", "挑战", "是否", "能否")),
    ("method", "方法", "method",
     ("method", "approach", "experiment", "material", "procedure",
      "protocol", "方法", "实验", "材料", "流程", "设计", "表征")),
    ("result", "结果", "result",
     ("result", "finding", "conclusion", "discussion", "outcome",
      "结果", "发现", "结论", "讨论", "数据", "性能")),
    ("limitation", "局限与风险", "risk",
     ("limitation", "risk", "weakness", "threat", "bias", "caveat",
      "不足", "局限", "风险", "限制", "偏差", "挑战")),
    ("future", "展望", "gap",
     ("future", "outlook", "recommend", "next step", "perspective",
      "展望", "后续", "建议", "未来", "下一步")),
    ("context", "要点", "concept", ()),
)
BRANCH_BY_KEY = {spec[0]: spec for spec in BRANCH_SPECS}

RELATION_CUES = (
    ("causes", ("导致", "引起", "造成", "使得", "因此", "因而", "从而",
                "cause", "causes", "lead to", "leads to", "result in",
                "results in", "enable", "enables")),
    ("supports", ("支持", "表明", "证明", "验证", "显示", "说明",
                  "support", "supports", "indicate", "indicates",
                  "confirm", "confirms", "show", "shows")),
    ("contrasts", ("相比", "然而", "但是", "相反", "高于", "低于",
                   "versus", "whereas", "contrast", "compared with",
                   "higher than", "lower than")),
    ("depends", ("依赖", "取决于", "基于", "depend", "depends",
                 "based on", "requires", "required")),
    ("explains", ("解释", "机制", "原因是", "归因于", "explain",
                  "explains", "mechanism", "attribute", "attributed")),
    ("precedes", ("首先", "随后", "之后", "然后", "before", "after",
                  "then", "followed by")),
    ("conflicts", ("冲突", "不一致", "矛盾", "contradict", "conflicts",
                   "inconsistent", "disagree")),
    ("part_of", ("属于", "组成", "包括", "包含", "part of", "include",
                 "includes", "consists of")),
    ("cites", ("引用", "参见", "cite", "cites", "et al", "doi")),
)

STOP_PREFIXES = ("但是", "然而", "因此", "所以", "此外", "同时", "另外",
                 "并且", "而且", "不过", "其中", "由此", "因此可知",
                 "however", "moreover", "therefore", "thus", "also",
                 "in addition", "meanwhile", "overall")

GENERIC_SHINGLES = {
    "研究表明", "实验结果", "实验表明", "结果显示", "研究结果", "本文提出",
    "研究工作", "数据分析", "结果表明", "可以发现", "值得注意", "进一步",
    "在此基础上", "综上所述", "对于", "通过", "以及", "能够", "可以",
    "具有", "进行", "采用", "使用", "我们", "本文", "该研究", "这项",
}


def configure_stdout() -> None:
    """Avoid UnicodeEncodeError on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def dump_json(path, data) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


def read_text(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            for block in iter(lambda: fh.read(65536), b""):
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def clean_inline(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^[\s>*\-+]+", "", text)
    text = re.sub(r"[`*_]{1,3}", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[ \t　]+", " ", text)
    return text.strip()


def normalize_for_match(text: str) -> str:
    low = clean_inline(text).lower()
    return re.sub(r"[\W_]+", "", low, flags=re.UNICODE)


def source_obj(value, default_path=None):
    """Normalize a node/edge source into {path, locator, quote?}."""
    result = {"path": "", "locator": ""}
    if isinstance(value, dict):
        result["path"] = str(value.get("path") or value.get("file") or "")
        result["locator"] = str(value.get("locator") or value.get("page")
                                or value.get("section") or "")
        if value.get("quote"):
            result["quote"] = str(value["quote"])
    elif value is not None:
        text = str(value).strip()
        if text:
            result["locator"] = text
            if default_path and default_path not in text:
                result["path"] = str(default_path)
    if not result["path"] and default_path:
        result["path"] = str(default_path)
    return result


def source_text(value) -> str:
    src = source_obj(value)
    text = " ".join(part for part in (src.get("path"), src.get("locator")) if part)
    return text.strip()


def source_ok(value) -> bool:
    src = source_obj(value)
    text = (src.get("path", "") + " " + src.get("locator", "")).strip().lower()
    return bool(text) and text not in ("not_provided", "unknown", "n/a",
                                       "none", "null")


def source_is_task(value) -> bool:
    src = source_obj(value)
    return "task" in (src.get("path", "") + " " + src.get("locator", "")).lower()


def config_stdout():
    configure_stdout()


def parse_constraints(task_text: str) -> dict:
    text = str(task_text or "")
    lowered = text.lower()
    max_nodes = MAX_NODES_DEFAULT
    for pattern in (r"(?:不超过|最多|≤|<=|at most|no more than)\s*(\d+)\s*(?:个)?\s*(?:节点|node)",
                    r"(?:节点数|节点|nodes?)\s*(?:不超过|最多|≤|<=|at most|no more than)\s*(\d+)",
                    r"(?:≤|<=)\s*(\d+)\s*(?:个)?\s*(?:节点|nodes?)"):
        match = re.search(pattern, lowered)
        if match:
            max_nodes = max(3, min(120, int(match.group(1))))
            break
    max_depth = MAX_DEPTH_DEFAULT
    match = re.search(r"(?:层级|层数|树深|深度|depth)[^\d]{0,8}(\d+)", lowered)
    if match:
        max_depth = max(2, min(6, int(match.group(1))))
    branch_range = None
    for pattern in (
            r"(?:一级)?(?:分支|branches?)\s*[:：]?\s*(\d+)\s*[-–—~至到]\s*(\d+)",
            r"(\d+)\s*[-–—~至到]\s*(\d+)\s*(?:个)?\s*(?:一级)?(?:分支|branches?)"):
        match = re.search(pattern, lowered)
        if match:
            low = max(2, int(match.group(1)))
            high = min(9, int(match.group(2)))
            if low <= high:
                branch_range = (low, high)
                break
    language = "zh" if re.search(r"[一-鿿]", text) else "en"
    output_format = "svg"
    if re.search(r"(?:只|仅|输出|交付)[^\n]{0,20}json", lowered) and "svg" not in lowered:
        output_format = "json"
    if re.search(r"png|pdf", lowered) and "svg" not in lowered and "json" not in lowered:
        output_format = "svg"
    return {"max_nodes": max_nodes, "max_depth": max_depth,
            "branch_range": branch_range, "language": language,
            "output_format": output_format,
            "parse_notes": []}


def _heading_match(line: str):
    stripped = line.strip()
    if not stripped or len(stripped) > 90:
        return None
    match = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", stripped)
    if match:
        return len(match.group(1)), clean_inline(match.group(2))
    match = re.match(r"^(\d+(?:\.\d+){0,3})[.)、]\s+(.+)$", stripped)
    if match and len(match.group(2)) <= 70:
        return match.group(1).count(".") + 1, clean_inline(match.group(2))
    match = re.match(r"^([一二三四五六七八九十]+)[、.]\s*(.+)$", stripped)
    if match and len(match.group(2)) <= 70:
        return 1, clean_inline(match.group(2))
    if stripped.startswith("**") and stripped.endswith("**") and len(stripped) <= 70:
        return 2, clean_inline(stripped)
    return None


def split_sections(text: str, path: str):
    """Split material into heading sections with line locators."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections = []
    current = {"heading": "正文", "level": 0, "path": path,
               "line": 1, "lines": []}
    for number, line in enumerate(lines, start=1):
        heading = _heading_match(line)
        if heading:
            if current["lines"] or current["heading"] != "正文":
                sections.append(current)
            level, title = heading
            current = {"heading": title or "正文", "level": level,
                       "path": path, "line": number, "lines": []}
        else:
            current["lines"].append(line)
    if current["lines"] or not sections:
        sections.append(current)
    output = []
    for section in sections:
        body = "\n".join(section["lines"]).strip()
        if not body and section["heading"] in ("正文", ""):
            continue
        section["text"] = body
        section["locator"] = "§%s L%d" % (section["heading"], section["line"])
        output.append(section)
    return output


def split_sentences(text: str):
    text = str(text or "").replace("\r\n", "\n")
    pieces = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    sentences = []
    for piece in pieces:
        piece = clean_inline(piece)
        if not piece:
            continue
        # English full stops, but never inside decimals or abbreviations.
        sub = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])", piece)
        for item in sub:
            item = item.strip()
            if len(item) >= 6 and not re.fullmatch(r"[\W\d_]+", item):
                sentences.append(item)
    return sentences


def branch_for_heading(heading: str):
    low = str(heading or "").lower()
    for key, label, node_type, cues in BRANCH_SPECS[:-1]:
        for cue in cues:
            if cue and cue in low:
                return key
    return ""


def branch_for_text(text: str):
    low = str(text or "").lower()
    scores = []
    for key, label, node_type, cues in BRANCH_SPECS[:-1]:
        score = sum(low.count(cue) for cue in cues if cue)
        if score:
            scores.append((score, key))
    if not scores:
        return "context"
    scores.sort(reverse=True)
    return scores[0][1]


def branch_label(key: str) -> str:
    spec = BRANCH_BY_KEY.get(key)
    return spec[1] if spec else "要点"


def branch_type(key: str) -> str:
    spec = BRANCH_BY_KEY.get(key)
    return spec[2] if spec else "concept"


def relation_for_text(text: str):
    low = str(text or "").lower()
    for relation, cues in RELATION_CUES:
        for cue in cues:
            if cue in low:
                return relation
    return ""


def compress_label(sentence: str, focus_words=(), limit: int = 24) -> str:
    """Return a source-substring label (never invent wording)."""
    text = clean_inline(sentence)
    text = re.sub(r"^[（(]?\d+[）)]?\s*", "", text)
    text = re.sub(r"^(?:核心问题是|研究问题是|本文提出|本研究发现|"
                  r"研究发现|结果表明|实验表明)[，,：:]?\s*", "", text)
    # Keep ":" inside clauses: ratios like 1:2 are meaningful.
    clauses = [clean_inline(part) for part in
               re.split(r"[，,；;。！？!?]\s*", text) if clean_inline(part)]
    if not clauses:
        clauses = [text]
    chosen = ""
    focus = [word for word in focus_words if word]
    for clause in clauses:
        if focus and any(word in clause for word in focus) and 4 <= len(clause) <= limit:
            chosen = clause
            break
    if not chosen and focus:
        for clause in clauses:
            if any(word in clause for word in focus) and len(clause) > limit:
                chosen = clause
                break
    if not chosen:
        for clause in clauses:
            if 5 <= len(clause) <= limit:
                chosen = clause
                break
    if not chosen:
        chosen = max(clauses, key=len)
    for prefix in STOP_PREFIXES:
        if chosen.lower().startswith(prefix.lower()) and len(chosen) > len(prefix) + 3:
            chosen = chosen[len(prefix):].lstrip(" ，,、:：")
            break
    chosen = chosen.strip(" ，,；;。！？!?、:：")
    if len(chosen) > limit:
        cut = chosen[:limit]
        last_space = max(cut.rfind(" "), cut.rfind("，"), cut.rfind(","))
        if last_space >= limit // 2:
            cut = cut[:last_space]
        chosen = re.sub(r"[的而在与和或为随把将对从向以于]$", "", cut.strip())
    return chosen or text[:limit].strip()


def shingles(text: str, size: int = 4):
    norm = normalize_for_match(text)
    if len(norm) < size:
        return set()
    return {norm[i:i + size] for i in range(len(norm) - size + 1)}


def shares_term(left: str, right: str, size: int = 4) -> bool:
    overlap = shingles(left, size) & shingles(right, size)
    return any(item not in GENERIC_SHINGLES for item in overlap)


def extract_pdf_text(path):
    """Best-effort PDF text extraction, stdlib only.

    Returns (text, status, note).  Encrypted/CID-font PDFs are reported as
    ocr_needed instead of pretending extraction succeeded.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return "", "unsupported", "cannot read PDF: %s" % exc
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        blob = match.group(1).strip(b"\r\n")
        try:
            blob = zlib.decompress(blob)
        except (zlib.error, ValueError):
            pass
        for text_match in re.finditer(rb"\((?:\\.|[^()\\])*\)", blob):
            token = text_match.group(0)[1:-1]
            token = re.sub(rb"\\([()\\])", rb"\1", token)
            token = token.replace(b"\\n", b" ").replace(b"\\r", b" ")
            try:
                decoded = token.decode("utf-8", errors="replace")
            except Exception:
                decoded = token.decode("latin-1", errors="replace")
            if decoded.strip():
                chunks.append(decoded)
    text = re.sub(r"\s+", " ", " ".join(chunks)).strip()
    if len(text) < 40:
        return "", "ocr_needed", "PDF text layer missing or unreadable; OCR needed"
    return text, "read", "heuristic PDF text extraction; verify page locators"


def flatten_json_strings(value, depth: int = 0):
    if depth > 8:
        return []
    if isinstance(value, str):
        return [value] if len(value.strip()) >= 4 else []
    if isinstance(value, dict):
        out = []
        for key, item in value.items():
            if str(key).lower() in ("case_id", "sha256", "id", "path"):
                continue
            out.extend(flatten_json_strings(item, depth + 1))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(flatten_json_strings(item, depth + 1))
        return out
    return []


def strip_markup(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text)


def iter_case_paths(value, base: Path):
    """Yield existing paths mentioned inside case.json (relative to input)."""
    candidates = []
    if isinstance(value, str):
        candidates.append(value)
    elif isinstance(value, list):
        for item in value:
            candidates.extend(iter_case_paths(item, base))
        return candidates
    elif isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in ("path", "file", "files", "source_documents",
                                    "materials", "documents"):
                candidates.extend(iter_case_paths(item, base))
        return candidates
    results = []
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or len(candidate) > 400:
            continue
        p = Path(candidate)
        if not p.is_absolute():
            p = base / candidate
        try:
            resolved = p.resolve()
            resolved.relative_to(base.resolve())
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            results.append(resolved)
    return results


def discover_input_files(input_dir):
    """Return readable source entries with honest status per file."""
    try:
        base = Path(input_dir).resolve()
    except OSError:
        base = Path(input_dir)
    entries, seen = [], set()

    def add(path):
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in seen or not resolved.is_file():
            return
        seen.add(resolved)
        entries.append(resolved)

    case_path = base / "case.json"
    if case_path.is_file():
        try:
            case = load_json(case_path)
            for p in iter_case_paths(case, base):
                add(p)
        except (OSError, json.JSONDecodeError):
            pass
    for sub in ("materials", "sources", "files", "docs", "documents"):
        folder = base / sub
        if folder.is_dir():
            for path in sorted(folder.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    add(path)
    for path in sorted(base.iterdir()):
        if path.is_file() and path.name not in ("case.json", "task.md"):
            add(path)

    output = []
    for path in entries:
        suffix = path.suffix.lower()
        rel = path.relative_to(base).as_posix()
        entry = {"path": rel, "abs": str(path), "suffix": suffix,
                 "sha256": sha256_file(path), "chars": 0,
                 "status": "read", "text": "", "notes": []}
        if suffix == ".pdf":
            text, status, note = extract_pdf_text(path)
            entry.update({"text": text, "status": status, "chars": len(text)})
            if note:
                entry["notes"].append(note)
        elif suffix in TEXT_SUFFIXES:
            raw = read_text(path)
            if suffix == ".json":
                try:
                    text = "\n".join(flatten_json_strings(load_json(path)))
                except (OSError, json.JSONDecodeError):
                    text = raw
            elif suffix in (".html", ".htm", ".xml"):
                text = strip_markup(raw)
            elif suffix in (".csv", ".tsv"):
                text = raw.replace("\t", " | ").replace(",", " | ")
            else:
                text = raw
            text = text.strip()
            entry.update({"text": text, "chars": len(text),
                          "status": "read" if text else "empty"})
        else:
            entry.update({"status": "unsupported"})
            entry["notes"].append("unsupported suffix: %s" % suffix)
        output.append(entry)
    return output


def wrap_label(label: str, max_chars: int = 12, max_lines: int = 3):
    """Wrap text by approximate display width (CJK = 1, ASCII = 0.55)."""
    text = clean_inline(label)
    if not text:
        return [""]
    lines, current, width = [], "", 0.0
    for char in text:
        weight = 0.55 if ord(char) < 128 else 1.0
        if width + weight > max_chars and current:
            lines.append(current.rstrip())
            current, width = "", 0.0
            if len(lines) >= max_lines:
                break
        current += char
        width += weight
    if current and len(lines) < max_lines:
        lines.append(current.rstrip())
    if not lines:
        lines = [text[:max_chars]]
    if len(lines) == max_lines and sum(len(line) for line in lines) < len(text):
        lines[-1] = lines[-1][:-1] + "…" if len(lines[-1]) > 1 else lines[-1]
    return lines


def xml_escape(text) -> str:
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def infer_depth(nodes) -> int:
    by_id = {str(n.get("node_id") or n.get("id")): n for n in nodes}
    maximum = 0
    for nid in by_id:
        depth, seen, current = 0, set(), nid
        while current and current in by_id:
            if current in seen:
                break
            seen.add(current)
            parent = by_id[current].get("parent")
            current = str(parent) if parent else ""
            depth += 1
        maximum = max(maximum, depth - 1 if depth else 0)
    return maximum


def file_size(path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0
