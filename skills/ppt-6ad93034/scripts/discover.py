#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discover runtime inputs and build a locator-bearing evidence index.

Works from the real files under /input; never assumes content from a label.
Supports case.json, task.md, material.json, artifacts/slide-workorder.json,
PDF/TXT/MD/DOCX/PPTX/CSV/XLSX materials, real images and optional embedded PDF
figures.  No LLM calls and no fabricated data.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

TEXT_EXT = {".md", ".txt", ".tex", ".rst", ".org", ".html", ".htm", ".yaml", ".yml"}
IMAGE_EXT = {".png", ".jpg", ".jpeg"}
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".xlsm"}
DOC_EXT = {".pdf", ".docx", ".pptx", ".potx", ".pptm"} | TEXT_EXT | TABLE_EXT | IMAGE_EXT
SKIP_NAMES = {"task.md", "case.json", "config.json", "output-contract.md",
              "example-input.json", "example_output.json", "example-input.md"}
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

SECTION_PATTERNS = [
    ("abstract", r"^\s*(abstract|summary|摘要)\b"),
    ("intro", r"^\s*(introduction|background|motivation|引言|背景|研究背景|问题的提出)\b"),
    ("methods", r"^\s*(methods?|materials and methods|methodology|experimental|"
                r"方法|材料与方法|实验方法|研究方法)\b"),
    ("results", r"^\s*(results?|findings|结果|研究结果)\b"),
    ("discussion", r"^\s*(discussion|讨论)\b"),
    ("limitations", r"^\s*(limitations?|limitation of|局限|局限性|不足)\b"),
    ("conclusion", r"^\s*(conclusions?|outlook|结论|总结|展望)\b"),
    ("references", r"^\s*(references|bibliography|参考文献)\b"),
]
SECTION_LABEL = {"abstract": "摘要", "intro": "背景", "methods": "方法",
                 "results": "结果", "discussion": "讨论", "limitations": "局限",
                 "conclusion": "结论", "references": "参考文献", "body": "正文"}

RESULT_CUES = re.compile(
    r"(increase[ds]?|decrease[ds]?|improv\w*|reduce[ds]?|higher|lower|significant|"
    r"correlat\w*|achiev\w*|yield\w*|selectiv\w*|conversion|p\s*[<=>]|"
    r"提高|降低|提升|减少|显著|相关|达到|产率|选择性|转化率|优于|相比)", re.I)
METHOD_CUES = re.compile(
    r"(we (used|measured|analy[sz]ed|collected|performed|constructed|trained)|"
    r"采用|使用|测量|分析|构建|采集|训练|实验|样本|数据集|模型)", re.I)
LIMIT_CUES = re.compile(
    r"(limitation|future work|caveat|further stud\w*|not (yet )?(tested|validated)|"
    r"局限|不足|未来工作|有待|尚未|仅在|样本量)", re.I)
QUESTION_CUES = re.compile(
    r"(we ask|we investigate|we examine|research question|aim of|objective|"
    r"hypothes\w*|本文|本研究|我们|旨在|问题|是否)", re.I)
NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s*(?:%|‰|°C|K|h|min|s|ms|"
                       r"nm|µm|μm|mm|cm|m|mL|µL|μL|L|mg|g|kg|M|mM|µM|μM|"
                       r"eV|keV|MeV|V|mA|W|Hz|kHz|MHz|GHz|Da|kDa|bp|kb|GPa|MPa|Pa)?",
                       re.I)


# -------------------------------------------------------------------- basics
def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def walk(obj, prefix=()):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield prefix + (str(key),), value
            yield from walk(value, prefix + (str(key),))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield prefix + (str(i),), value


def find_by_keys(obj, needles):
    hits = []
    for path, value in walk(obj):
        key = path[-1].lower() if path else ""
        if any(n in key for n in needles):
            hits.append((path, value))
    return hits


def resolve_path(value, roots):
    if not isinstance(value, (str, os.PathLike)):
        return None
    raw = str(value).strip().strip('"')
    if not raw or len(raw) > 500 or "\n" in raw:
        return None
    p = Path(raw)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        for root in roots:
            candidates.append(Path(root) / p)
            candidates.append(Path(root) / "materials" / p)
            candidates.append(Path(root) / "artifacts" / p)
    for c in candidates:
        if c.is_file():
            return str(c.resolve())
    return None


def office_path_value(value):
    if isinstance(value, dict):
        for key in ("path", "file", "filename", "source", "pptx"):
            if key in value:
                return value[key]
        return None
    if isinstance(value, list) and value:
        return office_path_value(value[0])
    return value


# ---------------------------------------------------------------- extraction
def _pdf_text(path):
    pages = []
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path))
        for i, page in enumerate(reader.pages, start=1):
            try:
                pages.append((f"p.{i}", page.extract_text() or ""))
            except Exception:
                pages.append((f"p.{i}", ""))
        if any(t.strip() for _, t in pages):
            return pages
    except Exception:
        pass
    exe = shutil.which("pdftotext")
    if exe:
        try:
            tmp = Path(tempfile.mkstemp(suffix=".txt")[1])
            subprocess.run([exe, "-layout", str(path), str(tmp)], check=True,
                           timeout=120, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            raw = tmp.read_text(encoding="utf-8", errors="replace")
            tmp.unlink(missing_ok=True)
            chunks = raw.split("\f")
            return [(f"p.{i}", chunk) for i, chunk in enumerate(chunks, start=1) if chunk.strip()]
        except (subprocess.SubprocessError, OSError):
            pass
    return []


def _docx_text(path):
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return []
    paras = []
    for i, p in enumerate(root.iter(f"{W_NS}p"), start=1):
        text = "".join(t.text or "" for t in p.iter(f"{W_NS}t")).strip()
        if text:
            paras.append((f"para.{i}", text))
    return paras


def _pptx_text(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted((n for n in z.namelist()
                            if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda n: int(re.search(r"(\d+)", Path(n).stem).group(1)))
            out = []
            for i, name in enumerate(names, start=1):
                try:
                    root = ET.fromstring(z.read(name))
                except ET.ParseError:
                    continue
                lines = []
                for p in root.iter(f"{A_NS}p"):
                    text = "".join(t.text or "" for t in p.iter(f"{A_NS}t")).strip()
                    if text:
                        lines.append(text)
                if lines:
                    out.append((f"slide {i}", "\n".join(lines)))
            return out
    except (zipfile.BadZipFile, KeyError):
        return []


def _xlsx_text(path):
    try:
        import openpyxl  # type: ignore
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    rows.append(",".join("" if v is None else str(v) for v in row))
            if rows:
                out.append((f"sheet {ws.title}", "\n".join(rows)))
        wb.close()
        return out
    except Exception:
        return []


def _html_text(raw):
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", raw)


def read_document(path):
    """Return {'kind','pages':[(locator,text)],'text'} for a real file."""
    p = Path(path)
    suffix = p.suffix.lower()
    pages = []
    if suffix == ".pdf":
        pages = _pdf_text(p)
    elif suffix == ".docx":
        pages = _docx_text(p)
    elif suffix == ".pptx":
        pages = _pptx_text(p)
    elif suffix in (".xlsx", ".xlsm"):
        pages = _xlsx_text(p)
    elif suffix in (".csv", ".tsv"):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
            pages = [(f"row {i}", line) for i, line in enumerate(raw.splitlines(), start=1)
                     if line.strip()]
        except OSError:
            pages = []
    elif suffix in (".md", ".txt", ".tex", ".rst", ".org", ".yaml", ".yml"):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
            pages = [(f"line {i}", line) for i, line in enumerate(raw.splitlines(), start=1)
                     if line.strip()]
        except OSError:
            pages = []
    elif suffix in (".html", ".htm"):
        try:
            pages = [("html", _html_text(p.read_text(encoding="utf-8", errors="replace")))]
        except OSError:
            pages = []
    elif suffix == ".json":
        data = load_json(p)
        if isinstance(data, dict):
            pieces = []
            for key in ("text", "content", "abstract", "body", "paper", "markdown"):
                if isinstance(data.get(key), str):
                    pieces.append(data[key])
            if pieces:
                pages = [("json", "\n".join(pieces))]
    text = "\n".join(t for _, t in pages)
    return {"kind": suffix.lstrip(".") or "file", "pages": pages, "text": text}


def extract_pdf_images(path, out_dir, limit=6):
    """Extract real embedded raster figures. No synthetic drawing."""
    made = []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path))
    except Exception:
        return made
    for page_no, page in enumerate(reader.pages, start=1):
        if len(made) >= limit:
            break
        try:
            images = list(page.images)
        except Exception:
            continue
        for j, image in enumerate(images):
            if len(made) >= limit:
                break
            try:
                data = image.data
                if len(data) < 12000:
                    continue
                name = str(getattr(image, "name", "") or f"img{j + 1}")
                ext = Path(name).suffix.lower() or ".png"
                if ext not in (".png", ".jpg", ".jpeg"):
                    ext = ".png"
                width = height = 0
                try:
                    from PIL import Image  # type: ignore
                    im = Image.open(io.BytesIO(data))
                    width, height = im.size
                    if width < 240 or height < 140:
                        continue
                except Exception:
                    pass
                dest = out_dir / f"pdf-p{page_no}-{j + 1}{ext}"
                dest.write_bytes(data)
                made.append({"path": str(dest), "source_path": str(path),
                             "source_locator": f"p.{page_no}", "width": width,
                             "height": height, "bytes": len(data),
                             "kind": "pdf-embedded-image"})
            except Exception:
                continue
    return made


def image_meta(path, source_locator="file"):
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    width = height = 0
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
    elif data[:2] == b"\xff\xd8":
        try:
            from PIL import Image  # type: ignore
            im = Image.open(io.BytesIO(data))
            width, height = im.size
        except Exception:
            pass
    if width and height and (width < 120 or height < 80):
        return None
    return {"path": str(path), "source_path": str(path), "source_locator": source_locator,
            "width": width, "height": height, "bytes": len(data),
            "sha256": sha256_bytes(data), "kind": "material-image"}


# --------------------------------------------------------------- evidence
def split_sentences(text):
    text = re.sub(r"\s+", " ", str(text)).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?])\s+|(?<=[.])\s+(?=[A-Z0-9一-鿿])", text)
    return [p.strip() for p in parts if len(p.strip()) >= 12]


def detect_section(line):
    low = str(line).strip()
    low = re.sub(r"^#{1,6}\s*", "", low)          # markdown heading
    low = re.sub(r"^(?:\d+[.)]|[-*•])\s+", "", low)
    low = low.strip("*_ ").lower()
    if len(low) > 80:
        low = low[:80]
    for section, pattern in SECTION_PATTERNS:
        if re.match(pattern, low, re.I):
            return section
    return None


def score_sentence(sentence, section):
    score = 0.0
    if NUMBER_RE.search(sentence):
        score += 2.0
    if RESULT_CUES.search(sentence):
        score += 1.6
    if METHOD_CUES.search(sentence):
        score += 0.8
    if LIMIT_CUES.search(sentence):
        score += 1.2
    if QUESTION_CUES.search(sentence):
        score += 0.6
    if section == "results":
        score += 1.2
    elif section in ("conclusion", "abstract"):
        score += 0.7
    elif section == "methods":
        score += 0.2
    if 45 <= len(sentence) <= 320:
        score += 0.7
    if len(sentence) > 500:
        score -= 1.0
    if re.match(r"^(fig(ure)?\.?|table|图|表)\s*\d+", sentence, re.I):
        score -= 0.8
    if re.match(r"^\[\d+\]", sentence):
        score -= 1.2
    return score


def build_evidence(sources, max_items=600):
    evidence = []
    seen = set()
    eid = 0
    for source in sources:
        current_section = "body"
        for locator, chunk in source["pages"]:
            lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
            if not lines:
                continue
            heading = detect_section(lines[0])
            if heading:
                current_section = heading
            text = " ".join(lines)
            for sentence in split_sentences(text):
                if len(sentence) < 25 or len(sentence) > 700:
                    continue
                norm = re.sub(r"\W+", "", sentence.lower())[:160]
                if norm and norm in seen:
                    continue
                seen.add(norm)
                section = detect_section(sentence) or current_section
                if section == "references":
                    continue
                eid += 1
                evidence.append({
                    "eid": f"E{eid:04d}",
                    "path": source["path"],
                    "locator": locator,
                    "text": sentence,
                    "section": section,
                    "score": round(score_sentence(sentence, section), 3),
                    "numbers": sorted(set(m.group(0).strip() for m in NUMBER_RE.finditer(sentence))),
                })
                if len(evidence) >= max_items:
                    return evidence
    evidence.sort(key=lambda e: (-e["score"], e["eid"]))
    return evidence


def similar(a, b):
    def grams(text):
        clean = re.sub(r"\W+", "", str(text).lower())
        return {clean[i:i + 4] for i in range(max(0, len(clean) - 3))}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return False
    return len(ga & gb) / len(ga | gb) > 0.62


# ------------------------------------------------------------------ parsing
def parse_task(text):
    info = {"slide_count": None, "aspect": None, "language": None,
            "audience": None, "duration_min": None, "title": None,
            "must_cover": [], "raw": text or ""}
    if not text:
        return info
    patterns = [
        r"(?:共|总计|固定|total(?:\s+of)?|exactly)\s*(\d{1,2})\s*(?:页|张|slides?|pages?)",
        r"(\d{1,2})\s*(?:页|张)\s*(?:PPT|幻灯片|slides?|pages?)",
        r"(?:slides?|pages?)\s*[:：=]\s*(\d{1,2})",
        r"(?:页数|幻灯片数量|slide count)\D{0,8}(\d{1,2})",
        r"(\d{1,2})\s*[-–]\s*(?:slide|page)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            value = int(m.group(1))
            if 4 <= value <= 60:
                info["slide_count"] = value
                break
    if info["slide_count"] is None:
        m = re.search(r"(?:不超过|最多|at most|no more than|maximum|max)\s*(\d{1,2})\s*(?:页|张|slides?|pages?)",
                      text, re.I)
        if m and 4 <= int(m.group(1)) <= 60:
            info["slide_count"] = int(m.group(1))
    if re.search(r"16\s*[:：]\s*9", text):
        info["aspect"] = "16:9"
    elif re.search(r"4\s*[:：]\s*3", text):
        info["aspect"] = "4:3"
    cjk = len(re.findall(r"[一-鿿]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    info["language"] = "zh" if cjk > latin * 0.25 else "en"
    audience = []
    for key, label in (("导师", "导师/课题组"), ("评审", "项目评审"), ("同行", "同行"),
                       ("答辩", "学位答辩"), ("组会", "组会"), ("基金", "基金申请"),
                       ("专家", "领域专家")):
        if key in text:
            audience.append(label)
    info["audience"] = "；".join(dict.fromkeys(audience)) or None
    m = re.search(r"(\d{1,3})\s*(?:分钟|min(?:ute)?s?)\b", text, re.I)
    if m:
        info["duration_min"] = int(m.group(1))
    for line in text.splitlines():
        m = re.match(r"^\s*#{1,4}\s+(.+?)\s*$", line)
        if m and not info["title"]:
            title = re.sub(r"^(学术\s*PPT\s*生成与编辑|学术 PPT 生成与编辑)[：:]\s*", "", m.group(1))
            if len(title) >= 4:
                info["title"] = title.strip()
                break
    capture = False
    for line in text.splitlines():
        if re.search(r"(必须覆盖|must cover|required content|内容要求)", line, re.I):
            capture = True
            continue
        if capture:
            if not line.strip():
                continue
            if re.match(r"^\s*#{1,4}\s", line) or re.match(r"^\s*\|", line):
                break
            info["must_cover"].append(line.strip(" -\t"))
    return info


def normalize_workorder(obj):
    if isinstance(obj, list):
        return {"mode": "edit", "edits": [x for x in obj if isinstance(x, (dict, str))],
                "raw": obj}
    if not isinstance(obj, dict):
        return {"mode": None, "edits": [], "raw": obj}
    mode = obj.get("mode") or obj.get("workflow_mode") or obj.get("workflow") or obj.get("task")
    is_edit = obj.get("edits") or obj.get("changes")
    if not mode and is_edit:
        mode = "edit"
    result = {
        "mode": str(mode).lower() if mode else None,
        "slide_count": obj.get("slide_count") or obj.get("target_slide_count")
        or obj.get("total_slides") or obj.get("slides_count"),
        "template": obj.get("template") or obj.get("target_template"),
        "starting_deck": obj.get("starting_deck") or obj.get("base_deck") or obj.get("deck"),
        "edits": [], "preserve": obj.get("preserve") or obj.get("preserved"),
        "raw": obj,
    }
    edits = obj.get("edits") or obj.get("changes") or obj.get("instructions") or []
    if isinstance(edits, dict):
        edits = list(edits.values())
    for item in edits if isinstance(edits, list) else []:
        if isinstance(item, dict):
            result["edits"].append(item)
        elif isinstance(item, str):
            result["edits"].append({"action": "replace", "to": item})
    for slide in obj.get("slides") or []:
        if not isinstance(slide, dict):
            continue
        slide_no = (slide.get("slide") or slide.get("slide_no")
                    or slide.get("slide_number") or slide.get("index")
                    or slide.get("page") or slide.get("page_no")
                    or slide.get("page_number") or slide.get("number"))
        for item in slide.get("edits") or slide.get("changes") or []:
            if isinstance(item, dict):
                item.setdefault("slide", slide_no)
                result["edits"].append(item)
    return result


def _mode_from_task(text):
    low = (text or "").lower()
    if re.search(r"(adapt|套用|套模板|目标模板|target[- ]template)", low):
        return "adapt"
    if re.search(r"\b(edit|revise|modify)\b|修改|改稿|编辑(?:现有|这份|该|第)|压缩|扩写|换受众|改写|修订", low):
        return "edit"
    if re.search(r"\b(generate|create|build)\b|生成|制作|做一份", low):
        return "generate"
    return None


# ------------------------------------------------------------------ figures
def discover_figures(materials_dir, work_dir, sources):
    figures, seen = [], set()
    mat = Path(materials_dir)
    if mat.is_dir():
        for path in sorted(mat.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXT:
                continue
            if path.name.startswith("."):
                continue
            meta = image_meta(path)
            if not meta or meta["sha256"] in seen:
                continue
            if path.stat().st_size < 6000:
                continue
            low = path.name.lower()
            meta["preferred"] = bool(re.search(r"(fig|figure|chart|plot|result|table|图)", low))
            seen.add(meta["sha256"])
            figures.append(meta)
    for source in sources:
        if source["kind"] != "pdf":
            continue
        for meta in extract_pdf_images(source["path"], Path(work_dir) / "figures",
                                       limit=4):
            key = sha256_file(meta["path"])
            if key in seen:
                continue
            seen.add(key)
            meta["sha256"] = key
            meta["preferred"] = True
            figures.append(meta)
    figures.sort(key=lambda f: (not f.get("preferred"), -(f.get("width", 0) * f.get("height", 0))))
    return figures


def discover_tables(materials_dir):
    tables = []
    mat = Path(materials_dir)
    if not mat.is_dir():
        return tables
    for path in sorted(mat.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TABLE_EXT:
            continue
        if path.name.startswith("~$"):
            continue
        try:
            if path.suffix.lower() in (".csv", ".tsv"):
                delim = "\t" if path.suffix.lower() == ".tsv" else ","
                with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                    rows = [r for r in csv.reader(fh, delimiter=delim) if any(c.strip() for c in r)]
                header = rows[0] if rows else []
                body = rows[1:31]
                tables.append({"path": str(path), "locator": "rows 1-{}".format(len(body) + 1),
                               "columns": header, "rows": body})
            else:
                import openpyxl  # type: ignore
                wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
                for ws in wb.worksheets:
                    rows = [[("" if v is None else v) for v in row]
                            for row in ws.iter_rows(values_only=True)]
                    rows = [r for r in rows if any(str(c).strip() for c in r)]
                    if len(rows) >= 3:
                        tables.append({"path": str(path), "locator": f"sheet {ws.title}",
                                       "columns": [str(c) for c in rows[0]],
                                       "rows": rows[1:31]})
                wb.close()
        except Exception:
            continue
    return tables


# ------------------------------------------------------------------- normalize
def normalize(input_dir, work_dir, spec_path=None, template_arg=None,
              starting_arg=None, slide_count_arg=None):
    input_dir = Path(input_dir)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    brief = {
        "input_dir": str(input_dir), "work_dir": str(work_dir),
        "case_id": None, "raw_case": {}, "task_text": "", "task": {},
        "mode": None, "mode_reason": None, "template": None, "starting_deck": None,
        "workorder": None, "workorder_path": None, "edits": [],
        "materials": [], "sources": [], "evidence": [], "figures": [], "tables": [],
        "constraints": [], "warnings": [], "errors": [], "case_file": None,
        "task_file": None, "material_file": None, "output_contract": [],
        "slide_count": None, "aspect": "16:9", "language": "zh",
        "audience": None, "duration_min": None, "title": None,
    }
    roots = [input_dir, input_dir / "materials", input_dir / "artifacts"]
    case_path = input_dir / "case.json"
    task_path = input_dir / "task.md"
    material_path = input_dir / "material.json"
    if not material_path.is_file():
        for candidate in (input_dir / "materials" / "material.json",
                          input_dir / "artifacts" / "material.json"):
            if candidate.is_file():
                material_path = candidate
                break
    case = load_json(case_path) if case_path.is_file() else None
    if isinstance(case, dict):
        brief["raw_case"] = case
        brief["case_file"] = str(case_path)
        brief["case_id"] = case.get("case_id") or case.get("id")
        oc = case.get("output_contract")
        if isinstance(oc, list):
            brief["output_contract"] = oc
        elif isinstance(oc, dict):
            brief["output_contract"] = list(oc.keys())
    elif case is not None:
        brief["warnings"].append("case.json is not a JSON object; ignored for preservation")
    material = load_json(material_path) if material_path.is_file() else None
    if isinstance(material, dict):
        brief["material_file"] = str(material_path)
        merged = dict(material)
        if isinstance(case, dict):
            merged = {**merged, **case}
    else:
        merged = case if isinstance(case, dict) else {}
    if task_path.is_file():
        brief["task_text"] = task_path.read_text(encoding="utf-8", errors="replace")
        brief["task_file"] = str(task_path)
    brief["task"] = parse_task(brief["task_text"])
    brief["slide_count"] = slide_count_arg or brief["task"]["slide_count"]
    brief["aspect"] = brief["task"]["aspect"] or "16:9"
    brief["language"] = brief["task"]["language"] or "zh"
    brief["audience"] = brief["task"]["audience"]
    brief["duration_min"] = brief["task"]["duration_min"]
    brief["title"] = brief["task"]["title"]
    brief["constraints"] = list(brief["task"]["must_cover"])

    # ---- explicit paths from case.json / material.json
    def first_path(needles, kind="pptx"):
        endings = (kind, ".potx", ".pptm", ".ppsm")
        for path, value in find_by_keys(merged, needles):
            candidate = office_path_value(value)
            found = resolve_path(candidate, roots)
            if found and found.lower().endswith(endings):
                return found
        return None

    brief["template"] = template_arg or first_path(["template"], ".pptx")
    brief["starting_deck"] = starting_arg or first_path(
        ["starting_deck", "start_deck", "base_deck", "baseline_deck", "input_deck",
         "starting", "baseline"], ".pptx")
    if brief["starting_deck"] == brief["template"]:
        brief["starting_deck"] = None

    # ---- workorder
    wo_candidates = [input_dir / "artifacts" / "slide-workorder.json",
                     input_dir / "slide-workorder.json",
                     input_dir / "workorder.json"]
    for path, value in find_by_keys(merged, ["workorder"]):
        candidate = office_path_value(value)
        found = resolve_path(candidate, roots)
        if found and found.lower().endswith(".json"):
            wo_candidates.insert(0, Path(found))
            break
    for candidate in wo_candidates:
        if candidate.is_file():
            data = load_json(candidate)
            if data is not None:
                brief["workorder"] = normalize_workorder(data)
                brief["workorder_path"] = str(candidate)
                break
    if brief["workorder"] is None:
        for path, value in find_by_keys(merged, ["workorder"]):
            if isinstance(value, dict):
                brief["workorder"] = normalize_workorder(value)
                break
    if brief["workorder"]:
        wo = brief["workorder"]
        brief["edits"] = wo.get("edits") or []
        if wo.get("template"):
            found = resolve_path(office_path_value(wo["template"]), roots)
            if found:
                brief["template"] = found
        if wo.get("starting_deck"):
            found = resolve_path(office_path_value(wo["starting_deck"]), roots)
            if found:
                brief["starting_deck"] = found
        if wo.get("slide_count"):
            try:
                brief["slide_count"] = int(wo["slide_count"])
            except (TypeError, ValueError):
                pass
    if not brief["edits"]:
        for path, value in find_by_keys(merged, ["edits", "changes", "instructions"]):
            if isinstance(value, list) and value and isinstance(value[0], (dict, str)):
                brief["edits"] = value
                break

    # ---- scan materials (materials/, artifacts/ and the input root)
    mat_dir = input_dir / "materials"
    scan_dirs = [d for d in (mat_dir, input_dir / "artifacts", input_dir) if d.is_dir()]
    seen_paths = set()

    def add_material(path):
        resolved = str(Path(path).resolve())
        if resolved in seen_paths:
            return
        seen_paths.add(resolved)
        brief["materials"].append({"path": resolved,
                                   "kind": Path(resolved).suffix.lower().lstrip("."),
                                   "bytes": Path(resolved).stat().st_size})

    for scan_dir in scan_dirs:
        for path in sorted(scan_dir.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.name in SKIP_NAMES or path.name.startswith("~$"):
                continue
            suffix = path.suffix.lower()
            if suffix not in DOC_EXT:
                continue
            if suffix in (".pptx", ".potx", ".pptm"):
                low = path.name.lower()
                if any(k in low for k in ("template", "模板", "master")):
                    if not brief["template"]:
                        brief["template"] = str(path.resolve())
                elif any(k in low for k in ("start", "base", "baseline", "deck", "原稿", "起始")):
                    if not brief["starting_deck"]:
                        brief["starting_deck"] = str(path.resolve())
                continue
            add_material(path)
    # explicit source paths inside case.json / material.json
    for _, value in walk(merged):
        candidate = office_path_value(value)
        if not isinstance(candidate, (str, os.PathLike)):
            continue
        suffix = Path(str(candidate)).suffix.lower()
        if suffix not in DOC_EXT or suffix in (".pptx", ".potx", ".pptm"):
            continue
        found = resolve_path(candidate, roots)
        if found and Path(found).name not in SKIP_NAMES:
            add_material(found)
    if brief["template"] and not Path(brief["template"]).is_file():
        brief["warnings"].append(f"template not found: {brief['template']}")
        brief["template"] = None
    if brief["starting_deck"] and not Path(brief["starting_deck"]).is_file():
        brief["warnings"].append(f"starting deck not found: {brief['starting_deck']}")
        brief["starting_deck"] = None

    # ---- text sources (real papers, not the task description)
    sources = []
    for item in brief["materials"]:
        path = item["path"]
        if item["kind"] in ("png", "jpg", "jpeg", "csv", "tsv", "xlsx", "xlsm"):
            continue
        doc = read_document(path)
        if not doc["text"].strip():
            if item["kind"] == "pdf":
                brief["warnings"].append(f"PDF text unavailable (scanned?): {path}")
            continue
        sources.append({"path": path, "kind": doc["kind"], "pages": doc["pages"],
                        "text": doc["text"], "chars": len(doc["text"]),
                        "sha256": sha256_file(path)})
    sources.sort(key=lambda s: -s["chars"])
    brief["sources"] = sources
    brief["evidence"] = build_evidence(sources)
    fig_dirs = [d for d in (mat_dir, input_dir / "artifacts") if d.is_dir()]
    if not fig_dirs:
        fig_dirs = [input_dir]
    figures = []
    for directory in fig_dirs:
        figures.extend(discover_figures(directory, work_dir, sources))
    deduped, seen_sha = [], set()
    for figure in sorted(figures, key=lambda f: (not f.get("preferred"),
                                                 -(f.get("width", 0) * f.get("height", 0)))):
        key = figure.get("sha256") or figure.get("path")
        if key in seen_sha:
            continue
        seen_sha.add(key)
        deduped.append(figure)
    brief["figures"] = deduped
    tables = []
    for directory in fig_dirs:
        tables.extend(discover_tables(directory))
    brief["tables"] = tables

    # ---- mode decision
    wo_mode = (brief["workorder"] or {}).get("mode")
    case_hint = ""
    if isinstance(case, dict):
        case_hint = " ".join(str(case.get(k, "")) for k in
                             ("workflow_mode", "mode", "task", "instruction", "instructions"))
    task_mode = _mode_from_task(brief["task_text"] + "\n" + case_hint)
    if wo_mode in ("generate", "edit", "adapt", "inspect"):
        brief["mode"] = wo_mode
        brief["mode_reason"] = "explicit workorder mode"
    elif brief["template"] and brief["starting_deck"]:
        brief["mode"], brief["mode_reason"] = "adapt", "template + starting deck detected"
    elif brief["starting_deck"] and (brief["edits"] or task_mode == "edit"):
        brief["mode"], brief["mode_reason"] = "edit", "starting deck + edit instructions"
    elif task_mode:
        brief["mode"], brief["mode_reason"] = task_mode, "task wording"
    elif brief["template"]:
        brief["mode"], brief["mode_reason"] = "adapt", "template detected"
    else:
        brief["mode"], brief["mode_reason"] = "generate", "no starting deck/template"

    if brief["slide_count"] is None:
        brief["slide_count"] = 12 if brief["mode"] == "generate" else None
    if brief["slide_count"]:
        brief["slide_count"] = max(4, min(30, int(brief["slide_count"])))

    if not (case_path.is_file() or task_path.is_file() or sources or brief["edits"]):
        brief["errors"].append(
            "no case.json/task.md, no readable source material and no edit instructions")
    if brief["edits"] and not brief["starting_deck"]:
        brief["warnings"].append("edit instructions found but no starting deck")
    if brief["mode"] in ("edit", "adapt") and not (brief["starting_deck"] or brief["template"]):
        brief["warnings"].append(f"mode={brief['mode']} but no base/template file resolved")
    return brief
