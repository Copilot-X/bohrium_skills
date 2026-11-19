#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Manuscript engine for challenge-308a6fdc (stdlib only, no network).

The engine does **not** write the paper for the model.  It reads the real input
materials, builds a writing contract, extracts a locatable evidence ledger, and
assembles/checks the manuscript written by the model.  A clearly labelled
PARTIAL scaffold is produced only when no model draft exists; it is never
reported as ``completed``.

Public functions
----------------
inventory(input_dir)          scan input, extract readable text
build_contract(case, task)    genre/language/sections/budget/declarations
extract_sources(...)          locatable source units + reference entries
extract_facts(...)            numeric evidence ledger with path+locator
plan_outline(...)             section plan bound to fact ids
compose_scaffold(...)         PARTIAL skeleton (never a finished draft)
writing_brief(...)            model-facing writing brief
parse_manuscript(...)         headings -> sections
bind_text(...)                sentence -> fact ids + citation numbers
build_result(...)             write manuscript + official-fields result.json
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".tex", ".csv", ".tsv", ".json",
                 ".yaml", ".yml", ".bib", ".py", ".xml", ".html", ".rst"}
META_NAMES = {"case.json", "task.md"}
CJK_RE = re.compile(r"[一-鿿]")
CITE_RE = re.compile(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
VALUE_RE = re.compile(
    r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)\s*"
    r"(?:%|wt%|vol%|°C|℃|h|hr|min|s|bar|kPa|MPa|Pa|mL|µL|uL|L|mg|µg|ug|g|kg|"
    r"K|nm|µm|um|mm|cm|m|eV|keV|ppm|M|mM|µM|uM|Da|kDa|%RH)?")
PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|待补充|待填写|待核实)\b", re.I)
CONFLICT_RE = re.compile(r"(conflict|inconsistent|discrepan|contradict|"
                         r"冲突|不一致|矛盾|相差较大)", re.I)
STOPWORDS = {
    "the", "and", "with", "from", "that", "this", "these", "those", "were",
    "was", "are", "for", "into", "than", "then", "have", "has", "had", "not",
    "but", "its", "their", "our", "after", "before", "under", "between",
    "using", "used", "can", "may", "also", "such", "which", "while", "more",
    "less", "when", "where", "each", "both", "all", "any", "per",
    "材料", "实验", "结果", "方法", "本文", "我们", "以及", "进行", "通过",
    "表明", "可以", "没有", "提供", "数据", "研究", "使用", "显示",
}
SECTION_TITLES = {
    "abstract": ("Abstract", "摘要"),
    "introduction": ("Introduction", "引言"),
    "methods": ("Materials and Methods", "材料与方法"),
    "results": ("Results", "结果"),
    "discussion": ("Discussion", "讨论"),
    "conclusion": ("Conclusion", "结论"),
    "main_body": ("Main Body", "主体论述"),
    "case_presentation": ("Case Presentation", "病例资料"),
    "references": ("References", "参考文献"),
    "declarations": ("Declarations", "声明"),
    "data_availability": ("Data Availability", "数据可得性"),
    "conflict_of_interest": ("Conflict of Interest", "利益冲突"),
    "author_contributions": ("Author Contributions", "作者贡献"),
    "ethics": ("Ethics Statement", "伦理声明"),
    "funding": ("Funding", "基金资助"),
}
SECTION_ALIASES = {
    "abstract": "abstract", "summary": "abstract", "摘要": "abstract",
    "introduction": "introduction", "background": "introduction",
    "引言": "introduction", "前言": "introduction", "背景": "introduction",
    "methods": "methods", "method": "methods", "materials and methods": "methods",
    "methodology": "methods", "材料与方法": "methods", "方法": "methods",
    "results": "results", "result": "results", "结果": "results",
    "discussion": "discussion", "讨论": "discussion",
    "conclusion": "conclusion", "conclusions": "conclusion", "结论": "conclusion",
    "main body": "main_body", "主体": "main_body", "正文": "main_body",
    "case presentation": "case_presentation", "病例资料": "case_presentation",
    "references": "references", "reference": "references", "参考文献": "references",
    "declarations": "declarations", "声明": "declarations",
    "data availability": "data_availability", "数据可得性": "data_availability",
    "conflict of interest": "conflict_of_interest", "利益冲突": "conflict_of_interest",
    "author contributions": "author_contributions", "作者贡献": "author_contributions",
    "ethics": "ethics", "ethics statement": "ethics", "伦理": "ethics",
    "伦理声明": "ethics",
    "funding": "funding", "资助": "funding", "基金": "funding",
}
DEFAULT_SECTIONS = {
    "research-article": ["abstract", "introduction", "methods", "results",
                         "discussion", "conclusion", "references"],
    "review": ["abstract", "introduction", "main_body", "discussion",
               "conclusion", "references"],
    "letter": ["abstract", "introduction", "methods", "results", "discussion",
               "references"],
    "case-report": ["abstract", "introduction", "case_presentation",
                    "discussion", "conclusion", "references"],
    "abstract": ["abstract"],
}
DECLARATION_WORDS = {
    "data_availability": r"数据可得性|数据可用性|data availability|availability of data",
    "conflict_of_interest": r"利益冲突|conflict of interest|competing interests",
    "author_contributions": r"作者贡献|author contributions|CRediT",
    "ethics": r"伦理|ethic|IRB|知情同意",
    "funding": r"基金|资助|funding|grant",
}
SCAFFOLD_MARKER = "PARTIAL SCAFFOLD"
OFFICIAL_FIELDS = ("deliverable", "preserved_facts", "evidence_map", "change_log")


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def norm_number(value):
    text = str(value or "").strip().replace("，", ",").replace("％", "%")
    return re.sub(r"\s+", "", text).replace(",", "")


def numbers_in(text):
    out = []
    cleaned = re.sub(r"\[\d+(?:\s*[,\-–]\s*\d+)*\]", "", str(text or ""))
    for match in VALUE_RE.finditer(cleaned):
        token = norm_number(match.group(0))
        if token and token not in out:
            out.append(token)
    return out


def split_sentences(text):
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", str(text or ""))
    out, buf = [], []
    for i, ch in enumerate(text):
        buf.append(ch)
        if ch in "。！？":
            out.append("".join(buf).strip())
            buf = []
        elif ch == "." and i + 1 < len(text) and text[i + 1].isspace():
            nxt = text[i + 2:i + 3]
            if not nxt or nxt.isupper() or CJK_RE.match(nxt):
                out.append("".join(buf).strip())
                buf = []
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [item for item in out if len(item) > 3]


def tokens(text):
    low = str(text or "").lower()
    words = {w for w in re.findall(r"[a-z][a-z0-9\-]{3,}", low)
             if w not in STOPWORDS}
    for run in re.findall(r"[一-鿿]{2,}", low):
        for i in range(len(run) - 1):
            words.add(run[i:i + 2])
    return words


def overlap(a, b):
    return len(tokens(a) & tokens(b))


def safe_rel(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).name


def word_count(text, language="en"):
    if language == "zh":
        return len(CJK_RE.findall(str(text or ""))) + len(
            re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)*", str(text or "")))
    return len(re.findall(r"[A-Za-z0-9µ°%]+(?:[-'][A-Za-z0-9]+)*", str(text or "")))


def unit_of(token):
    match = re.search(r"(%|wt%|vol%|°C|℃|h|hr|min|s|bar|kPa|MPa|Pa|mL|µL|uL|L|"
                      r"mg|µg|ug|g|kg|K|nm|µm|um|mm|cm|m|eV|keV|ppm|M|mM|µM|uM)$",
                      str(token or ""))
    return match.group(1) if match else ""


def title_for(section, language="en"):
    titles = SECTION_TITLES.get(section, (section, section))
    return titles[1] if language == "zh" else titles[0]


# ---------------------------------------------------------------------------
# inventory: read files, never trust labels
# ---------------------------------------------------------------------------

def _decode_bytes(data):
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def _read_docx(path):
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p\s*>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(xml)


def _read_pdf(path):
    exe = shutil.which("pdftotext") or shutil.which("pdf-text-tool")
    if not exe:
        return None, "no pdftotext/pdf-text-tool on PATH"
    try:
        proc = subprocess.run([exe, str(path), "-"], capture_output=True,
                              timeout=90)
        if proc.returncode != 0:
            return None, "pdf extractor exit %s" % proc.returncode
        return proc.stdout.decode("utf-8", errors="replace"), "pdftotext"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "pdf extractor failed: %s" % exc


def read_material_text(path):
    """Return (text, method, error).  PDFs degrade honestly when no tool."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            return _read_docx(path), "docx-zip", None
        except (OSError, KeyError, zipfile.BadZipFile) as exc:
            return None, "docx-zip", str(exc)
    if suffix == ".pdf":
        return _read_pdf(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        return None, "size-limit", "file exceeds 4 MB text limit"
    text = _decode_bytes(path.read_bytes())
    if not text:
        return None, "binary", "no supported text decoding"
    return text, "text", None


def inventory(input_dir):
    """Scan the input tree.  Returns (report, texts) with texts path->content."""
    root = Path(input_dir)
    report = {"root": str(root), "files": [], "materials": [], "unreadable": [],
              "material_count": 0, "readable_texts": 0, "file_count": 0}
    texts = {}
    if not root.is_dir():
        return report, texts
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = safe_rel(path, root)
        if any(part.startswith(".") for part in Path(rel).parts):
            continue
        data = path.read_bytes()
        record = {
            "path": rel, "name": path.name, "suffix": path.suffix.lower(),
            "size": len(data), "sha256": sha256_bytes(data),
            "role": "meta" if rel in META_NAMES else "material",
        }
        text, method, error = None, "skipped", None
        if rel in META_NAMES or path.suffix.lower() in TEXT_SUFFIXES or \
                path.suffix.lower() in (".docx", ".pdf"):
            text, method, error = read_material_text(path)
        record.update({"readable": text is not None, "method": method,
                       "chars": len(text or "")})
        if text is None and error:
            record["error"] = error
            if rel not in META_NAMES:
                report["unreadable"].append({"path": rel, "error": error})
        if text is not None:
            texts[rel] = text
        report["files"].append(record)
        if record["role"] == "material":
            report["materials"].append(record)
    report["file_count"] = len(report["files"])
    report["material_count"] = len(report["materials"])
    report["readable_texts"] = sum(1 for item in report["materials"]
                                   if item.get("readable"))
    return report, texts


# ---------------------------------------------------------------------------
# contract: task.md first, then case.json, then genre defaults
# ---------------------------------------------------------------------------

def _detect_genre(text):
    low = str(text or "").lower()
    if re.search(r"综述|文献综述|\breview article\b|\bsystematic review\b", low):
        return "review"
    if re.search(r"病例报告|\bcase report\b", low):
        return "case-report"
    if re.search(r"快报|通讯|\bletter\b|\bcorrespondence\b", low):
        return "letter"
    if re.search(r"只写摘要|仅摘要|abstract only|standalone abstract", low):
        return "abstract"
    return "research-article"


def _detect_language(text):
    sample = str(text or "")
    cjk = len(CJK_RE.findall(sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    return "zh" if cjk >= 20 and cjk >= latin * 0.15 else "en"


def _detect_sections(text, genre):
    low = str(text or "").lower()
    found = []
    for key, pattern in (
            ("abstract", r"摘要|\babstract\b"),
            ("introduction", r"引言|前言|\bintroduction\b|\bbackground\b"),
            ("methods", r"材料与方法|\bmethods?\b|\bmethodology\b|实验方法|方法"),
            ("results", r"\bresults?\b|结果"),
            ("discussion", r"\bdiscussion\b|讨论"),
            ("conclusion", r"\bconclusions?\b|结论"),
            ("references", r"\breferences?\b|参考文献"),
            ("main_body", r"\bmain body\b|主体论述|主体部分")):
        if re.search(pattern, low):
            found.append(key)
    if genre == "abstract":
        return ["abstract"]
    default = list(DEFAULT_SECTIONS.get(genre, DEFAULT_SECTIONS["research-article"]))
    if len(found) < 3:
        return default
    if genre == "review" and "main_body" not in found:
        found = [s for s in found if s not in ("methods", "results")]
        found.append("main_body")
    # Keep only sections actually named by the task, but in the canonical
    # genre order: prose mentions earlier in task.md must not reorder IMRaD.
    ordered = [s for s in default if s in found]
    ordered += [s for s in found
                if s not in ordered and s not in ("references",)]
    return ordered


def _detect_budget(text, genre):
    match = re.search(r"(\d{2,5})\s*(?:词|字|words?|characters?)", str(text or ""),
                      re.I)
    if match:
        return int(match.group(1))
    return {"research-article": 4000, "review": 8000, "letter": 2500,
            "case-report": 3000, "abstract": 250}.get(genre, 4000)


def _detect_citation_style(text):
    low = str(text or "").lower()
    styles = (("GB/T 7714", r"gb/?t\s*7714"), ("APA", r"\bapa\b"),
              ("IEEE", r"\bieee\b"), ("Vancouver", r"vancouver"),
              ("Nature", r"\bnature\b"))
    for name, pattern in styles:
        if re.search(pattern, low):
            return name
    return "author-year"


def build_contract(case, task_text):
    task_text = str(task_text or "")
    case = case or {}
    constraints = case.get("constraints") or {}
    if not isinstance(constraints, dict):
        constraints = {}
    instruction = str(case.get("task_instruction") or "")
    combined = "\n".join([task_text, instruction,
                          json.dumps(constraints, ensure_ascii=False)])
    genre = str(constraints.get("genre") or _detect_genre(combined))
    language = str(constraints.get("language") or _detect_language(combined))
    sections = _detect_sections(combined, genre)
    declarations = []
    for key, pattern in DECLARATION_WORDS.items():
        if re.search(pattern, combined, re.I):
            declarations.append(key)
    if not declarations and genre in ("research-article", "review", "letter",
                                      "case-report"):
        declarations = ["data_availability", "conflict_of_interest"]
    if genre != "abstract":
        if "references" not in sections:
            sections.append("references")
        if declarations and "declarations" not in sections:
            sections.append("declarations")
    output_contract = case.get("output_contract") or {}
    if isinstance(output_contract, list):
        output_contract = {str(item): None for item in output_contract}
    return {
        "case_id": case.get("case_id") or "unknown-case",
        "title": (case.get("source") or {}).get("title") or case.get("title"),
        "genre": genre,
        "language": language,
        "sections": sections,
        "required_sections": [s for s in sections
                              if s not in ("references", "declarations")],
        "required_declarations": declarations,
        "word_budget": int(constraints.get("word_budget")
                           or _detect_budget(combined, genre)),
        "citation_style": str(constraints.get("citation_style")
                              or _detect_citation_style(combined)),
        "output_contract": output_contract,
        "instruction": instruction or task_text[:1200],
        "unmet": [],
    }


# ---------------------------------------------------------------------------
# sources and references
# ---------------------------------------------------------------------------

def _split_blocks(text):
    """Paragraph blocks with 1-based line locators."""
    blocks = []
    lines = str(text or "").splitlines()
    start, buf, depth = 1, [], 0
    for number, line in enumerate(lines, 1):
        if not buf:
            start = number
        buf.append(line)
        depth += line.count("{") - line.count("}")
        if (line.strip() == "" or re.match(r"^#{1,6}\s", line)) and depth == 0:
            body = "\n".join(buf).strip()
            if body:
                blocks.append({"locator": "lines %d-%d" % (start, number),
                               "text": body})
            buf = []
    body = "\n".join(buf).strip()
    if body:
        blocks.append({"locator": "lines %d-%d" % (start, len(lines)),
                       "text": body})
    return blocks


def _csv_blocks(text):
    try:
        rows = list(csv.reader(str(text or "").splitlines()))
    except csv.Error:
        return []
    if not rows:
        return []
    header = [item.strip() for item in rows[0]]
    blocks = []
    for index, row in enumerate(rows[1:], 2):
        pairs = ["%s=%s" % (key, value.strip())
                 for key, value in zip(header, row) if str(value).strip()]
        if pairs:
            blocks.append({"locator": "row %d" % index,
                           "text": "; ".join(pairs)})
    return blocks


def _bib_blocks(text):
    blocks = []
    for match in re.finditer(r"@(\w+)\s*\{\s*([^,]+),(.*?)\n\}", text, re.S):
        blocks.append({"locator": "entry %s" % match.group(2).strip(),
                       "text": "%s: %s" % (match.group(1), match.group(3).strip())})
    return blocks


def plain_heading(line):
    return re.sub(r"^#{1,6}\s+", "", str(line or "")).strip().lower().rstrip(":")


def _reference_lines(text):
    """Extract numbered reference entries from a References-like section."""
    lines = str(text or "").splitlines()
    heading_at = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*(?:#{1,6}\s*)?(references|参考文献)\s*$", line, re.I):
            heading_at = i
            break
    if heading_at is None:
        return []
    entries = []
    for offset, line in enumerate(lines[heading_at + 1:], heading_at + 2):
        stripped = line.strip()
        if re.match(r"^#{1,6}\s", stripped) and plain_heading(stripped) not in (
                "references", "参考文献"):
            break
        match = re.match(r"^\[?(\d+)\]?[.)]?\s+(.+)$", stripped)
        if match and len(match.group(2)) > 20:
            entries.append({"number": int(match.group(1)),
                            "text": match.group(2).strip(),
                            "locator": "line %d" % offset})
    return entries


def _parse_bib_entries(text):
    entries = []
    for match in re.finditer(r"@(\w+)\s*\{\s*([^,]+),(.*?)\n\}", text, re.S):
        body = match.group(3)
        fields = {}
        for field in re.finditer(r"(\w+)\s*=\s*\{(.*?)\}", body, re.S):
            fields[field.group(1).lower()] = re.sub(r"\s+", " ",
                                                    field.group(2)).strip()
        title = fields.get("title", "not_available")
        authors = fields.get("author", fields.get("editor", "not_available"))
        year = fields.get("year", "not_available")
        venue = (fields.get("journal") or fields.get("booktitle")
                 or fields.get("publisher") or "not_available")
        doi = fields.get("doi", "not_available")
        entries.append({
            "citation_number": 0, "key": match.group(2).strip(),
            "fields": fields,
            "display": "%s (%s). %s. %s. doi:%s" % (authors, year, title,
                                                     venue, doi),
            "locator": "entry %s" % match.group(2).strip()})
    return entries


def _parse_reference_entries(text, path):
    return [{"citation_number": item["number"], "key": "ref-%d" % item["number"],
             "fields": {},
             "display": item["text"], "locator": item["locator"],
             "path": path} for item in _reference_lines(text)]


def extract_sources(texts, input_root, work_dir):
    """Build source records, blocks and reference entries."""
    del input_root  # kept for signature clarity; paths are relative already
    sources, references = [], []
    index = 0
    for rel, text in sorted(texts.items()):
        if rel in META_NAMES:
            continue
        index += 1
        suffix = Path(rel).suffix.lower()
        is_bib = suffix == ".bib" or bool(re.search(
            r"@\w+\s*\{[^,]+,[\s\S]{0,120}?(?:author|title)\s*=", text, re.I))
        if suffix in (".csv", ".tsv"):
            kind, blocks = "table", _csv_blocks(text)
        elif is_bib:
            kind, blocks = "bibliography", _bib_blocks(text)
        else:
            kind, blocks = "document", _split_blocks(text)
        first_line = next((line.strip() for line in str(text).splitlines()
                           if line.strip()), "")
        title = re.sub(r"^#{1,6}\s*", "", first_line)[:160] or Path(rel).stem
        source_id = "SRC%02d" % index
        text_path = Path("texts") / (source_id + ".txt")
        write_text(Path(work_dir) / text_path, text)
        sources.append({
            "source_id": source_id, "path": rel, "kind": kind,
            "title": title, "locator_scheme": "block locator",
            "text_path": text_path.as_posix(),
            "block_count": len(blocks), "blocks": blocks[:120],
        })
        if kind == "bibliography":
            references.extend(_parse_bib_entries(text))
        else:
            references.extend(_parse_reference_entries(text, rel))
    seen, numbered = set(), []
    for entry in references:
        key = (entry.get("key"), re.sub(r"\s+", " ", entry.get("display", "")))
        if key in seen:
            continue
        seen.add(key)
        entry["citation_number"] = len(numbered) + 1
        display = re.sub(r"^\[\d+\]\s*", "", entry.get("display") or "")
        entry["display"] = "[%d] %s" % (entry["citation_number"], display)
        if "path" not in entry:
            entry["path"] = "materials"
        numbered.append(entry)
    return {"sources": sources, "source_count": len(sources),
            "units_total": sum(len(item["blocks"]) for item in sources),
            "references": numbered, "reference_count": len(numbered)}


# ---------------------------------------------------------------------------
# evidence ledger and outline
# ---------------------------------------------------------------------------

def _condition_of(sentence, block_text):
    match = re.search(r"(?:at|under|after|during|in|在|于)[^,;。\n]{0,90}"
                      r"(?:\d|n\s*=)", sentence, re.I)
    if match:
        return match.group(0).strip()
    for token in re.findall(r"\bn\s*=\s*\d+", block_text):
        return token
    return "condition not stated in source"


def extract_facts(sources, limit=80):
    facts = []
    seen = set()
    for source in sources:
        if source.get("kind") == "bibliography":
            continue
        for block in source.get("blocks") or []:
            for sentence in split_sentences(block.get("text") or ""):
                if len(sentence) < 18:
                    continue
                values = numbers_in(sentence)
                if not values:
                    continue
                citation_numbers = []
                for group in CITE_RE.findall(sentence):
                    for part in re.split(r"[,，]", group):
                        if part.strip().isdigit():
                            citation_numbers.append(int(part.strip()))
                status = ("estimated" if re.search(r"约|大约|近似|approx|~|估计",
                                                  sentence, re.I) else "verified")
                for value in values[:8]:
                    key = (source["path"], block.get("locator"),
                           re.sub(r"\s+", " ", sentence).strip()[:280], value)
                    if key in seen:
                        continue
                    seen.add(key)
                    facts.append({
                        "fact_id": "F%03d" % (len(facts) + 1),
                        "statement": re.sub(r"\s+", " ", sentence).strip()[:280],
                        "value": value, "unit": unit_of(value),
                        "condition": _condition_of(sentence,
                                                   block.get("text") or ""),
                        "kind": "reported", "verification_status": status,
                        "source": {"path": source["path"],
                                   "locator": block.get("locator", "block")},
                        "quote": re.sub(r"\s+", " ",
                                        block.get("text") or "").strip()[:300],
                        "citation_numbers": citation_numbers, "used_in": [],
                    })
                    if len(facts) >= limit:
                        return facts
    return facts


def plan_outline(contract, facts, references):
    verified = [f for f in facts if f.get("verification_status") == "verified"]
    result_facts = (verified or facts)[:10]
    outline = []
    for section in contract.get("sections") or []:
        if section == "abstract":
            ids = [f["fact_id"] for f in (result_facts or facts)[:3]]
            citations = [e["citation_number"] for e in references[:2]]
        elif section == "introduction":
            ids = [f["fact_id"] for f in (result_facts or facts)[:2]]
            citations = [e["citation_number"] for e in references[:3]]
        elif section == "methods":
            ids = [f["fact_id"] for f in result_facts[:6]]
            citations = []
        elif section in ("results", "main_body", "case_presentation"):
            ids = [f["fact_id"] for f in (result_facts or facts)]
            citations = []
        elif section == "discussion":
            ids = [f["fact_id"] for f in (result_facts or facts)[2:6]]
            citations = [e["citation_number"] for e in references[:3]]
        elif section == "conclusion":
            ids = [f["fact_id"] for f in (result_facts or facts)[:2]]
            citations = []
        elif section == "references":
            ids = []
            citations = [e["citation_number"] for e in references]
        else:
            ids, citations = [], []
        if section in ("references", "declarations") or \
                section in (contract.get("required_declarations") or []):
            status = "planned"
        else:
            status = "planned" if ids else "not_supported"
        outline.append({
            "section_id": section,
            "title": title_for(section, contract.get("language", "en")),
            "fact_ids": ids, "citation_numbers": citations, "status": status,
        })
    return {"sections": outline,
            "unsupported_sections": [item["section_id"] for item in outline
                                     if item["status"] == "not_supported"]}


# ---------------------------------------------------------------------------
# scaffold (PARTIAL only) and model-facing brief
# ---------------------------------------------------------------------------

def _section_heading(section, language):
    title = title_for(section, language)
    order = [s for s in DEFAULT_SECTIONS["research-article"]
             if s != "references"]
    try:
        return "## %d. %s" % (order.index(section) + 1, title)
    except ValueError:
        return "## %s" % title


def compose_scaffold(contract, outline, facts, references):
    """Create a clearly-labelled PARTIAL scaffold.  Never a finished draft."""
    language = contract.get("language", "en")
    title = contract.get("title") or "Manuscript scaffold"
    lines = ["# %s" % title, ""]
    lines.append("<!-- %s: this is not a finished manuscript. Replace this file "
                 "with the model-written draft, then run `python3 scripts/main.py "
                 "assemble`. The entry point will not mark a scaffold as "
                 "completed. -->" % SCAFFOLD_MARKER)
    lines.append("")
    plans = {item["section_id"]: item
             for item in outline.get("sections") or []}
    for section in contract.get("sections") or []:
        lines.append(_section_heading(section, language))
        lines.append("")
        if section == "references":
            if references:
                for entry in references:
                    lines.append(entry.get("display") or
                                 "[%d] %s" % (entry["citation_number"],
                                              entry.get("key")))
            else:
                lines.append("_The materials provide no reference entries; the "
                             "reference list is intentionally empty._")
        elif section == "declarations":
            for item in contract.get("required_declarations") or []:
                lines.append("- **%s:** not_provided_in_materials"
                             % title_for(item, language))
        else:
            plan = plans.get(section) or {}
            ids = plan.get("fact_ids") or []
            if ids:
                lines.append("_Supporting evidence is available in the evidence "
                             "ledger (work/evidence-ledger.json). Write this "
                             "section from that ledger; do not copy source "
                             "sentences verbatim._")
            else:
                lines.append("_not_supported: the provided materials do not "
                             "contain content for this section._")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def writing_brief(contract, inv, sources, facts, outline):
    """Model-facing brief: contract, evidence table and per-section plan."""
    lines = ["# Writing brief (from the actual input files)", ""]
    lines += ["- case_id: `%s`" % contract.get("case_id"),
              "- genre: `%s`" % contract.get("genre"),
              "- language: `%s`" % contract.get("language"),
              "- word_budget: %s" % contract.get("word_budget"),
              "- citation_style: `%s`" % contract.get("citation_style"),
              "- sections: %s" % ", ".join(contract.get("sections") or []),
              "- declarations: %s" % (", ".join(
                  contract.get("required_declarations") or []) or "none"),
              ""]
    instruction = re.sub(r"\s+", " ", contract.get("instruction") or "").strip()
    if instruction:
        lines += ["## Task instruction", "", instruction[:1800], ""]
    lines += ["## Inventory", "",
              "- files: %d; materials: %d; readable: %d" %
              (inv.get("file_count", 0), inv.get("material_count", 0),
               inv.get("readable_texts", 0))]
    for item in inv.get("unreadable") or []:
        lines.append("- unreadable: `%s` (%s)" % (item["path"], item.get("error")))
    lines.append("")
    lines += ["## Evidence ledger (numbers must match these values)", ""]
    if facts:
        lines += ["| fact | value | condition | source | statement |",
                  "|---|---|---|---|---|"]
        for fact in facts:
            lines.append("| %s | `%s` | %s | `%s` / %s | %s |" % (
                fact["fact_id"], fact["value"], fact["condition"],
                fact["source"]["path"], fact["source"]["locator"],
                fact["statement"].replace("|", "\\|")[:160]))
    else:
        lines.append("- no numeric statement was found in the materials.")
    lines.append("")
    lines += ["## References available in the materials", ""]
    if sources.get("references"):
        for entry in sources["references"]:
            lines.append("- [%d] %s" % (entry["citation_number"],
                                        entry.get("display", "")))
    else:
        lines.append("- none; do not invent citations.")
    lines.append("")
    lines += ["## Section plan", ""]
    for item in outline.get("sections") or []:
        lines.append("- `%s` (%s): facts=%s citations=%s" % (
            item["section_id"], item["status"],
            ",".join(item.get("fact_ids") or []) or "none",
            ",".join(map(str, item.get("citation_numbers") or [])) or "none"))
    lines += ["", "## Next step", "",
              "Write the manuscript to `/app/output/manuscript.md` (or "
              "`/app/work/draft.md`), then run:", "",
              "```bash",
              "python3 scripts/main.py assemble --input /input --work /app/work "
              "--output /app/output --draft /app/work/draft.md",
              "```", "",
              "Every numeric sentence must reuse a ledger value; every `[n]` "
              "must exist in the reference list.  Never submit this scaffold."]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# parse, bind, coverage, result
# ---------------------------------------------------------------------------

def _slug(title):
    key = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s*", "", str(title or "")).strip().lower()
    key = key.rstrip(":：").strip()
    if key in SECTION_ALIASES:
        return SECTION_ALIASES[key]
    for alias, slug in SECTION_ALIASES.items():
        if alias and alias in key:
            return slug
    return re.sub(r"[^a-z0-9一-鿿]+", "-", key).strip("-") or "section"


def parse_manuscript(text):
    title, sections = None, []
    current = None
    for line in str(text or "").splitlines():
        match = HEADING_RE.match(line)
        if match:
            level, heading = len(match.group(1)), match.group(2).strip()
            if level == 1 and title is None:
                title = heading
                continue
            if level <= 3:
                current = {"id": _slug(heading), "title": heading, "lines": []}
                sections.append(current)
                continue
        if current is not None:
            current["lines"].append(line)
        elif title is None and line.strip():
            title = line.strip()[:160]
    for index, section in enumerate(sections):
        section["text"] = "\n".join(section.pop("lines")).strip()
        section["words"] = word_count(section["text"])
        base = section["id"]
        while sum(1 for s in sections[:index] if s["id"] == section["id"]):
            section["id"] = base + "-%d" % (index + 1)
    return {"title": title or "Untitled manuscript", "sections": sections}


def _fact_match(sentence, facts):
    sent_numbers = set(numbers_in(sentence))
    ids = []
    for fact in facts:
        value = norm_number(fact.get("value"))
        if value and value in sent_numbers:
            ids.append(fact["fact_id"])
    if not ids:
        for fact in facts:
            if len(sentence) > 24 and overlap(sentence, fact.get("statement")) >= 3:
                ids.append(fact["fact_id"])
    return list(dict.fromkeys(ids))[:8]


def bind_text(parsed, facts):
    sentences = []
    used, citation_used = set(), set()
    for section in parsed["sections"]:
        for sentence in split_sentences(section["text"]):
            fact_ids = _fact_match(sentence, facts)
            used.update(fact_ids)
            cites = []
            for group in CITE_RE.findall(sentence):
                for part in re.split(r"[,，]", group):
                    if part.strip().isdigit():
                        cites.append(int(part.strip()))
            citation_used.update(cites)
            sentences.append({
                "sentence_id": "s%03d" % (len(sentences) + 1),
                "section": section["id"], "text": sentence[:400],
                "fact_ids": fact_ids,
                "citation_numbers": sorted(set(cites)),
            })
    for fact in facts:
        fact["used_in"] = sorted({item["section"] for item in sentences
                                  if fact["fact_id"] in item["fact_ids"]})
    for section in parsed["sections"]:
        section_sentences = [s for s in sentences if s["section"] == section["id"]]
        section["claims"] = sorted({fid for s in section_sentences
                                    for fid in s["fact_ids"]})
        low = section["text"].lower()
        explicit_gap = ("not_supported" in low or "do not provide" in low
                        or "未提供" in section["text"] or "材料未提供" in section["text"]
                        or "insufficient" in low or "不足" in section["text"])
        if explicit_gap and section["id"] not in (
                "references", "declarations", "data_availability",
                "conflict_of_interest", "author_contributions", "ethics",
                "funding"):
            status = "not_supported"
        elif section["id"] in ("references", "declarations") or \
                section["id"] in ("data_availability", "conflict_of_interest",
                                  "author_contributions", "ethics", "funding"):
            status = "written"
        elif not section["claims"] and section["words"] < 25:
            status = "not_supported"
        elif not section["claims"]:
            status = "partial"
        else:
            status = "written"
        section["status"] = status
        section["citations"] = sorted({c for s in section_sentences
                                       for c in s["citation_numbers"]})
    return {"sentences": sentences, "used_fact_ids": sorted(used),
            "used_citation_numbers": sorted(citation_used),
            "fact_by_id": {fact["fact_id"]: fact for fact in facts}}


def _coverage(contract, parsed, outline):
    present = {section["id"]: section for section in parsed["sections"]}
    required = list(contract.get("sections") or [])
    missing = [section for section in required if section not in present]
    unsupported = [section for section in required
                   if section in present and present[section]["status"] == "not_supported"]
    low_words = [section for section in required
                 if section in present and present[section]["words"] < 15]
    return {
        "required_sections": required,
        "present_sections": list(present),
        "missing_sections": missing,
        "unsupported_sections": unsupported,
        "short_sections": low_words,
        "words": sum(section["words"] for section in parsed["sections"]),
        "word_budget": contract.get("word_budget"),
        "planned_unsupported": (outline or {}).get("unsupported_sections", []),
    }


def _detect_conflicts(sources):
    conflicts = []
    for source in sources:
        for block in source.get("blocks") or []:
            text = block.get("text") or ""
            if CONFLICT_RE.search(text):
                conflicts.append({
                    "topic": "material-reported inconsistency",
                    "a": re.sub(r"\s+", " ", text)[:300],
                    "b": "see source block",
                    "handling": "retained without adjudication; human confirmation required",
                    "source": {"path": source["path"],
                               "locator": block.get("locator")},
                })
    return conflicts[:20]


def _uncertainty(inv, contract, facts, references, coverage, scaffold,
                 extra_notes=None):
    items = []
    for item in inv.get("unreadable") or []:
        items.append({"item": item["path"], "reason": item.get("error") or "unreadable",
                      "impact": "content not used; may leave sections partial"})
    if not inv.get("readable_texts"):
        items.append({"item": "materials", "reason": "no readable material text",
                      "impact": "no evidence-based manuscript can be produced"})
    for section in coverage.get("missing_sections") or []:
        items.append({"item": section, "reason": "required section not present in draft",
                      "impact": "structure incomplete; section must be added"})
    for section in coverage.get("unsupported_sections") or []:
        items.append({"item": section, "reason": "no supporting material for this section",
                      "impact": "section marked not_supported and left editable"})
    for declaration in contract.get("required_declarations") or []:
        items.append({"item": declaration,
                      "reason": "declaration value not reported in materials",
                      "impact": "written as not_provided_in_materials; author must confirm"})
    if not facts:
        items.append({"item": "quantitative evidence",
                      "reason": "no numeric statement found in materials",
                      "impact": "results/conclusion cannot be supported numerically"})
    if not references:
        items.append({"item": "references",
                      "reason": "no reference entries found in materials",
                      "impact": "no citations are fabricated; reference list is empty"})
    if scaffold:
        items.append({"item": "draft mode",
                      "reason": "PARTIAL scaffold generated because no model draft was installed",
                      "impact": "replace it with a model-written manuscript before submission"})
    estimated = [fact for fact in facts
                 if fact.get("verification_status") == "estimated"]
    if estimated:
        items.append({"item": "estimated values",
                      "reason": "%d facts carry approximate/estimated wording" % len(estimated),
                      "impact": "values are labelled estimated and kept out of causal claims"})
    for note in extra_notes or []:
        if note:
            items.append({"item": "draft", "reason": note,
                          "impact": "the entry point used the available file"})
    return items


def _change_log(mode, inv, contract, sources, facts, parsed, coverage, audit,
                scaffold):
    log = [
        {"step": 1, "action": "inventory input files",
         "reason": "read actual materials instead of trusting labels",
         "evidence": "%d files, %d readable materials" %
                     (inv.get("file_count", 0), inv.get("readable_texts", 0))},
        {"step": 2, "action": "build manuscript contract",
         "reason": "task.md/case.json define genre, sections, language and budget",
         "evidence": "genre=%s; language=%s; sections=%s" %
                     (contract.get("genre"), contract.get("language"),
                      ",".join(contract.get("sections") or []))},
        {"step": 3, "action": "extract sources and evidence ledger",
         "reason": "every fact needs a locatable source",
         "evidence": "%d source units, %d facts, %d references" %
                     (sources.get("units_total", 0), len(facts),
                      sources.get("reference_count", 0))},
        {"step": 4, "action": "install manuscript",
         "reason": "the manuscript is the primary deliverable",
         "evidence": "mode=%s; scaffold=%s; %d sections; %d words" %
                     (mode, scaffold, len(parsed.get("sections") or []),
                      coverage.get("words", 0))},
        {"step": 5, "action": "bind sentences to facts and citations",
         "reason": "numbers and claims must be traceable",
         "evidence": "%d sections carry %d distinct fact bindings" %
                     (len(parsed.get("sections") or []),
                      len({fid for s in parsed.get("sections") or []
                           for fid in s.get("claims") or []}))},
        {"step": 6, "action": "run manuscript checks",
         "reason": "detect structure, citation, numeric and boundary gaps",
         "evidence": "audit_ok=%s; problems=%d; warnings=%d" %
                     (audit.get("ok"), len(audit.get("problems") or []),
                      len(audit.get("warnings") or []))},
    ]
    for problem in (audit.get("problems") or [])[:8]:
        log.append({"step": 7, "action": "record unresolved audit problem",
                    "reason": problem,
                    "evidence": "kept in uncertainty; draft remains editable"})
    return log


def _preserved(facts, bindings, scaffold):
    used = set(bindings["used_fact_ids"])
    chosen = [fact for fact in facts if fact["fact_id"] in used]
    if not chosen and facts and not scaffold:
        chosen = facts[:12]
    return [{
        "fact_id": fact["fact_id"], "statement": fact["statement"],
        "value": fact["value"], "unit": fact["unit"],
        "condition": fact["condition"], "kind": fact["kind"],
        "verification_status": fact["verification_status"],
        "source": fact["source"], "used_in": fact.get("used_in") or [],
        "citation_numbers": fact.get("citation_numbers") or [],
    } for fact in chosen]


def _evidence_map(sentences, bindings):
    out = []
    for sentence in sentences:
        if not sentence["fact_ids"]:
            continue
        evidence = []
        for fact_id in sentence["fact_ids"]:
            fact = bindings["fact_by_id"].get(fact_id)
            if fact:
                evidence.append({"fact_id": fact_id, "source": fact["source"],
                                 "quote": fact.get("quote", "")})
        out.append({"claim": sentence["text"], "sentence_id": sentence["sentence_id"],
                    "section": sentence["section"], "text": sentence["text"],
                    "fact_ids": sentence["fact_ids"], "evidence": evidence})
    return out


def _output_contract_aliases(result, contract):
    """Keep original output_contract key names as aliases of the four fields."""
    contract_map = contract.get("output_contract") or {}
    if not isinstance(contract_map, dict):
        return result
    for key in contract_map:
        if key in result:
            continue
        low = str(key).lower()
        if "deliver" in low or "产物" in str(key) or "稿件" in str(key):
            result[key] = result["deliverable"]
        elif "fact" in low or "事实" in str(key):
            result[key] = result["preserved_facts"]
        elif "evidence" in low or "证据" in str(key):
            result[key] = result["evidence_map"]
        elif "change" in low or "修改" in str(key) or "变更" in str(key):
            result[key] = result["change_log"]
    return result


def build_result(case, contract, inv, sources, facts, manuscript_text, *,
                 work_dir, output_dir, mode="assemble", scaffold=False,
                 extra_notes=None):
    """Write manuscript.md, evidence artifacts and official-fields result.json."""
    output_dir, work_dir = Path(output_dir), Path(work_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    manuscript_text = str(manuscript_text or "")
    write_text(output_dir / "manuscript.md", manuscript_text)
    parsed = parse_manuscript(manuscript_text)
    bindings = bind_text(parsed, facts)
    outline = read_json(work_dir / "outline.json", {}) or {}
    coverage = _coverage(contract, parsed, outline)
    references = sources.get("references") or []
    conflicts = _detect_conflicts(sources.get("sources") or [])
    uncertainty = _uncertainty(inv, contract, facts, references, coverage,
                               scaffold, extra_notes)
    artifacts = [
        {"role": "editable", "path": "manuscript.md", "format": "markdown"},
        {"role": "evidence", "path": "source-map.json", "format": "json"},
        {"role": "log", "path": "change-log.json", "format": "json"},
        {"role": "plan", "path": "manuscript-plan.json", "format": "json"},
        {"role": "audit", "path": "manuscript-audit.json", "format": "json"},
    ]
    result = {
        "case_id": contract.get("case_id"),
        "task_family": "manuscript",
        "status": "partial",
        "input": {
            "case_id": (case or {}).get("case_id"),
            "task_instruction": (case or {}).get("task_instruction"),
            "file_count": inv.get("file_count", 0),
            "readable_materials": inv.get("readable_texts", 0),
            "unreadable": inv.get("unreadable") or [],
        },
        "contract": contract,
        "manuscript": {
            "path": "manuscript.md",
            "genre": contract.get("genre"),
            "language": contract.get("language"),
            "word_count": coverage.get("words", 0),
            "scaffold": bool(scaffold),
            "sections": [{"id": section["id"], "title": section["title"],
                          "words": section["words"],
                          "claims": section.get("claims") or [],
                          "status": section["status"]}
                         for section in parsed["sections"]],
        },
        "artifacts": artifacts,
        "sources": {"count": sources.get("source_count", 0),
                    "units": sources.get("units_total", 0),
                    "files": [{"source_id": item["source_id"],
                               "path": item["path"], "kind": item["kind"],
                               "block_count": item["block_count"]}
                              for item in sources.get("sources") or []]},
        "source_map": facts,
        "sentence_map": bindings["sentences"],
        "citations": {
            "style": contract.get("citation_style"),
            "entries": references,
            "used_numbers": bindings["used_citation_numbers"],
            "unused_numbers": sorted(
                {entry["citation_number"] for entry in references} -
                set(bindings["used_citation_numbers"])),
        },
        "conflicts": conflicts,
        "missing_materials": sorted({
            item["item"] for item in uncertainty
            if "no numeric" in item.get("reason", "")
            or "no reference" in item.get("reason", "")
            or "not reported" in item.get("reason", "")}),
        "coverage": coverage,
        "uncertainty": uncertainty,
        "change_log": [],
        "deliverable": {
            "path": "manuscript.md", "format": "markdown",
            "media_type": "text/markdown", "editable": True,
            "genre": contract.get("genre"), "language": contract.get("language"),
            "word_count": coverage.get("words", 0),
            "sections": [section["id"] for section in parsed["sections"]],
        },
        "preserved_facts": _preserved(facts, bindings, scaffold),
        "evidence_map": _evidence_map(bindings["sentences"], bindings),
        "support_files_used": [
            "references/manuscript-contract.md",
            "references/limits.md",
            "scripts/writing_lib.py",
            "scripts/task_specific.py",
        ],
        "generated_by": "scripts/main.py (%s)" % mode,
    }
    # Materialise declared artifact paths before auditing so every path is real.
    write_json(output_dir / "source-map.json",
               {"case_id": result["case_id"], "facts": facts})
    write_json(output_dir / "change-log.json", [])
    write_json(output_dir / "manuscript-plan.json",
               {"contract": contract, "outline": outline})
    write_json(output_dir / "manuscript-audit.json", {})
    try:
        try:
            from . import task_specific
        except ImportError:
            import task_specific
        audit = task_specific.audit(result, manuscript_text, contract)
    except Exception as exc:  # noqa: BLE001 - checks must not break delivery
        audit = {"ok": False, "problems": ["task_specific failed: %s" % exc],
                 "warnings": [], "checks": [], "stats": {}}
    result["audit"] = {"task_specific": audit}
    result["change_log"] = _change_log(mode, inv, contract, sources, facts,
                                       parsed, coverage, audit, scaffold)
    compact_length = len(re.sub(r"\s+", "", manuscript_text))
    if scaffold:
        status = "insufficient_input" if not inv.get("readable_texts") else "partial"
    elif not inv.get("readable_texts"):
        status = "insufficient_input"
    elif compact_length < 200 or coverage.get("missing_sections") or \
            audit.get("problems"):
        status = "partial"
    else:
        status = "completed"
    result["status"] = status
    result["audit_summary"] = {
        "ok": bool(audit.get("ok")),
        "problems": audit.get("problems") or [],
        "warnings": audit.get("warnings") or [],
    }
    result = _output_contract_aliases(result, contract)
    # Write artifacts before result.json so every declared path exists.
    write_json(output_dir / "source-map.json",
               {"case_id": result["case_id"], "facts": facts})
    write_json(output_dir / "change-log.json", result["change_log"])
    write_json(output_dir / "manuscript-plan.json",
               {"contract": contract, "outline": outline})
    write_json(output_dir / "manuscript-audit.json", result["audit"])
    write_json(output_dir / "result.json", result)
    write_json(work_dir / "result-summary.json", {
        "case_id": result["case_id"], "status": status,
        "deliverable": "manuscript.md", "scaffold": bool(scaffold),
        "words": coverage.get("words"), "sections": len(parsed["sections"]),
        "facts": len(facts), "evidence_map": len(result["evidence_map"]),
        "audit_ok": result["audit_summary"]["ok"],
        "problems": result["audit_summary"]["problems"][:10],
    })
    return result
