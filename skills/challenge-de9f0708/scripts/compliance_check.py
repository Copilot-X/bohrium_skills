#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic academic-compliance extraction for challenge-de9f0708.

The script is the evidence side of the Skill: it reads the supplied material,
locates compliance declarations and applicability statements, and emits a
status matrix with verbatim ``path + locator + quote`` evidence. It never
writes the final review prose and it never invents an approval number, consent
scope, repository DOI, licence, journal rule or data-availability claim.

Status vocabulary (also used by ``result.json``):

``pass``
    A declaration exists in the supplied material and no deficiency was
    detected *within that material*. This is not a certification.
``issue``
    The facet applies and the declaration is absent, or the declaration
    directly contradicts other supplied text. "Not reported" is an issue,
    never a pass.
``needs_recheck``
    A declaration exists but is incomplete/ambiguous, or an external document
    (approval, consent form, permission, journal guideline) is required to
    decide. A human must review it.
``not_checked``
    The supplied material is insufficient to decide (for example no plagiarism
    corpus, no target-journal guideline), or the facet cannot apply based on
    the text. Absence of a verdict must not be read as compliance.
``not_applicable``
    The material explicitly states that the facet does not apply (for example
    "no human or animal subjects") and no contradicting indicator was found.

Usage::

    python3 compliance_check.py --input /input --work /app/work --output /app/output

    # Also fill empty fields of work/review-findings.json without overwriting
    # any model-authored value:
    python3 compliance_check.py ... --merge-work

Outputs (always written, including on empty input)::

    output/compliance-matrix.json       matrix + findings + uncertainties
    output/compliance-findings.json     findings only
    output/compliance-evidence-map.json flattened verbatim evidence
    output/compliance-check.md          human-readable draft, not a verdict
    work/compliance-draft.json          work-file-shaped draft
    work/review-findings.json           filled only with --merge-work

Exit code is 0 on every handled outcome (including no material and internal
errors) so the Skill entry point can continue to ``finalize``. ``--strict``
returns 1 when the top status is not ``pass``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

TOOL = "compliance_check"
VERSION = "1.0.0"
STATUS_ZH = {
    "pass": "通过（仅限已提供材料）",
    "issue": "问题/缺口",
    "needs_recheck": "待复核",
    "not_checked": "未检查",
    "not_applicable": "不适用",
    "input_error": "输入错误",
}
SEVERITIES = ("critical", "major", "minor", "info")

RULES = {
    "ethics_approval": ("CMP-ETH-01", "伦理审批与批件信息"),
    "informed_consent": ("CMP-CON-01", "知情同意或豁免"),
    "data_availability": ("CMP-DAT-01", "数据可用性"),
    "conflict_of_interest": ("CMP-COI-01", "利益冲突"),
    "funding": ("CMP-FUN-01", "基金与资助"),
    "author_contributions": ("CMP-AUT-01", "作者贡献"),
    "acknowledgments": ("CMP-ACK-01", "致谢"),
    "duplicate_publication": ("CMP-DUP-01", "重复发表/版本披露"),
    "plagiarism": ("CMP-PLG-01", "抄袭/查重可核验性"),
    "text_reuse": ("CMP-PLG-02", "输入材料内部文字复用"),
    "copyright_license": ("CMP-CPY-01", "第三方材料版权与许可"),
    "privacy": ("CMP-PRV-01", "隐私与可识别信息"),
    "submission_requirements": ("CMP-SUB-01", "目标期刊/会议要求"),
    "reporting_checklist": ("CMP-REP-01", "报告规范/清单"),
}
FACET_ORDER = [
    "ethics_approval", "informed_consent", "data_availability",
    "conflict_of_interest", "funding", "author_contributions",
    "acknowledgments", "duplicate_publication", "plagiarism", "text_reuse",
    "copyright_license", "privacy", "submission_requirements",
    "reporting_checklist",
]
TYPE_BY_FACET = {
    "privacy": "privacy",
    "copyright_license": "license",
    "duplicate_publication": "duplicate_publication",
    "text_reuse": "text_reuse",
}

# ---------------------------------------------------------------------------
# IO and text helpers
# ---------------------------------------------------------------------------
TEXT_SUFFIXES = {
    ".md", ".txt", ".rst", ".org", ".tex", ".latex", ".json", ".csv",
    ".yaml", ".yml", ".toml", ".bib", ".ris", ".xml", ".html",
}
BINARY_SUFFIXES = {".pdf", ".docx", ".doc"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".webp"}
SKIP_NAMES = {"case.json", "task.md"}
SKIP_DIRS = {"__pycache__", ".git", ".ocr", ".pytest_cache"}
MAX_TEXT = 600000


def read_text(path, default=""):
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return default


def load_json(path, default=None):
    try:
        return json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return {} if default is None else default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def norm_space(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.replace("­", "").replace("﻿", "")
    return re.sub(r"\s+", " ", text).strip()


def norm_key(text):
    return re.sub(r"\s+", "", norm_space(text).lower())


def cjk_count(text):
    return len(re.findall(r"[一-鿿]", str(text or "")))


def md_cell(value):
    return norm_space(value).replace("|", "\\|")


def sentence_around(text, start, end, limit=420):
    boundaries = "。！？.!?\n"
    left = 0
    for char in boundaries:
        pos = text.rfind(char, 0, max(0, start))
        left = max(left, pos + 1)
    right = len(text)
    for char in boundaries:
        pos = text.find(char, min(len(text), end))
        if pos >= 0:
            right = min(right, pos + 1)
    quote = text[left:right].strip()
    if not quote or len(quote) > limit:
        quote = text[max(0, start - 70):min(len(text), end + 250)].strip()
    if not quote:
        quote = text[max(0, start - 40):min(len(text), end + 120)].strip()
    return quote


def quote_in(source, quote):
    """Mirror of the finalize check: whitespace/NFKC folding + ellipsis parts."""
    source = norm_key(source)
    parts = [p for p in re.split(r"(?:…|\.\.\.)", str(quote or "")) if norm_key(p)]
    return bool(parts) and all(norm_key(p) in source for p in parts)


# ---------------------------------------------------------------------------
# Document loading and paragraph locators
# ---------------------------------------------------------------------------
def is_standalone(line):
    text = line.strip()
    if not text:
        return False
    if text.startswith("#") or text.startswith("|"):
        return False
    if re.match(r"^(?:[-*+]|\d{1,3}[.)])\s+", text):
        return True
    return len(text) <= 240 and bool(re.search(r"[.!?。！？；;:：]$", text))


def heading_text(raw):
    text = raw.strip()
    match = re.match(r"^#{1,6}\s+(.+?)\s*#*$", text)
    if match:
        return match.group(1).strip()
    match = re.match(r"^\\(?:sub)*section\*?\{([^}]+)\}", text)
    if match:
        return match.group(1).strip()
    match = re.match(r"^\*\*(.+?)\*\*$", text)
    if match:
        return match.group(1).strip()
    return ""


def build_blocks(text):
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    raw_units, buffer, start = [], [], None
    for index, line in enumerate(lines, 1):
        if line.strip():
            if heading_text(line.strip()):
                if buffer:
                    raw_units.append((start, index - 1, buffer))
                    buffer, start = [], None
                raw_units.append((index, index, [line]))
                continue
            if start is None:
                start = index
            buffer.append(line)
        elif buffer:
            raw_units.append((start, index - 1, buffer))
            buffer, start = [], None
    if buffer:
        raw_units.append((start, len(lines), buffer))
    units = []
    for start, end, chunk in raw_units:
        if len(chunk) > 1 and all(is_standalone(line) for line in chunk):
            for offset, line in enumerate(chunk):
                units.append((start + offset, start + offset, [line]))
        else:
            units.append((start, end, chunk))
    blocks, section = [], ""
    for index, (start, end, chunk) in enumerate(units, 1):
        raw = "\n".join(chunk).strip()
        if not raw:
            continue
        heading = heading_text(raw)
        if heading:
            section = heading
        blocks.append({
            "index": index,
            "locator": "P%d" % index,
            "line_start": start,
            "line_end": end,
            "section": section,
            "text": raw,
            "is_heading": bool(heading),
        })
    return blocks


def make_doc(path, kind, text, warnings=None):
    text = str(text or "")[:MAX_TEXT]
    return {
        "path": str(path).replace("\\", "/"),
        "name": Path(str(path)).name,
        "kind": kind,
        "text": text,
        "warnings": list(warnings or []),
        "blocks": build_blocks(text),
    }


def try_parsers():
    here = Path(__file__).resolve().parent
    candidates = [here, here.parent.parent / "shared" / "scripts"]
    for candidate in candidates:
        if (candidate / "parsers.py").is_file() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    try:
        import parsers  # type: ignore
        return parsers
    except Exception:  # noqa: BLE001
        return None


def load_documents(input_dir, work_dir):
    input_dir, work_dir = Path(input_dir), Path(work_dir)
    docs, warnings, seen = [], [], set()
    materials = load_json(work_dir / "materials.json", {})
    for item in materials.get("documents") or []:
        path = str(item.get("path") or "")
        if not path or Path(path).name in SKIP_NAMES:
            continue
        text = str(item.get("text") or "")
        if not text.strip():
            warnings.append({"path": path, "reason": "no extractable text",
                             "warnings": item.get("warnings") or []})
            continue
        docs.append(make_doc(path, item.get("kind") or "text", text,
                             item.get("warnings") or []))
        seen.add(path)
    if docs:
        return docs, warnings
    if not input_dir.exists():
        return [], [{"path": str(input_dir), "reason": "input directory missing"}]
    parsers = None
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts) or path.name.startswith("."):
            continue
        rel = path.relative_to(input_dir).as_posix()
        if path.name in SKIP_NAMES or rel in seen:
            continue
        suffix = path.suffix.lower()
        text, kind, extra = "", "text", []
        if suffix in TEXT_SUFFIXES:
            text = read_text(path)
        elif suffix in BINARY_SUFFIXES:
            kind = "pdf" if suffix == ".pdf" else "docx"
            parsers = parsers or try_parsers()
            if parsers:
                try:
                    payload = parsers.extract_any(path, ocr=False)
                    text = str(payload.get("text") or "")
                    extra = payload.get("warnings") or []
                except Exception as exc:  # noqa: BLE001
                    extra = ["extract failed: %s" % exc]
            else:
                extra = ["no parser available; inspect the file manually"]
        elif suffix in IMAGE_SUFFIXES:
            warnings.append({"path": rel, "reason": "image requires manual inspection",
                             "warnings": []})
            continue
        else:
            text = read_text(path)
        if not text.strip():
            warnings.append({"path": rel, "reason": "no extractable text",
                             "warnings": extra})
            continue
        docs.append(make_doc(rel, kind, text, extra))
        seen.add(rel)
    return docs, warnings


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
P = {
    "human": [
        r"\bpatients?\b", r"\bparticipants?\b", r"\bvolunteers?\b",
        r"\brespondents?\b", r"\bhuman subjects?\b", r"\binterviews?\b",
        r"\bsurvey(?:s|ed)?\b", r"\bquestionnaires?\b",
        r"\bblood samples?\b", r"\bclinical (?:trial|study|data)\b",
        r"受试者", r"患者", r"参与者", r"志愿者", r"问卷", r"访谈",
        r"血液样本", r"临床研究", r"人体",
    ],
    "animal": [
        r"\bmice\b", r"\brats?\b", r"\banimals?\b",
        r"\banimal experiments?\b", r"动物实验", r"小鼠", r"大鼠", r"实验动物",
    ],
    "no_scope": [
        r"no human (?:or animal )?(?:subjects|participants|data)",
        r"(?:does not|did not) involve human", r"not involve human",
        r"不涉及人体", r"不包含人体", r"无人体", r"不涉及人(?:体|类)",
        r"不涉及动物",
    ],
    "ethics": [
        r"ethic(?:s|al)? (?:approval|committee|review|board)",
        r"institutional review board", r"\bIRB\b", r"\bIEC\b",
        r"approved by (?:the )?[^.\n]{0,60}(?:committee|board)",
        r"ethical approval", r"research ethics",
        r"伦理委员会", r"伦理(?:审批|审查|批件|批准)", r"批准号",
        r"批件号", r"审批号",
    ],
    "ethics_id": [
        r"(?:approval|protocol|reference|ethics|IRB|IEC)[^.\n]{0,30}"
        r"(?:number|no\.?|#|编号|号)\s*[:：]?\s*[A-Za-z0-9][A-Za-z0-9._/-]{2,}",
        r"IRB[-\s]?[A-Za-z0-9-]{3,}", r"(?:批准|批件|审批)号\s*[:：]?\s*[A-Za-z0-9-]{2,}",
    ],
    "consent": [
        r"informed consent", r"written consent",
        r"consent (?:was|were|has been|had been) obtained",
        r"consent from (?:all )?(?:participants|patients|subjects)",
        r"obtained (?:written )?consent", r"知情同意", r"签署(?:了)?知情同意",
        r"同意书", r"获得.{0,8}同意",
    ],
    "consent_waiver": [
        r"waiver of (?:informed )?consent",
        r"consent (?:was|were) waived", r"waived (?:the )?(?:requirement for )?"
        r"(?:informed )?consent", r"豁免知情同意", r"知情同意.{0,6}豁免",
        r"免知情同意",
    ],
    "consent_image": [
        r"consent[^.\n]{0,80}(?:photo|image|photograph|publish|publication|公开|发表|照片|影像)",
        r"(?:photo|image|photograph)[^.\n]{0,80}consent",
        r"(?:照片|影像|图片)[^。\n]{0,40}(?:同意|授权)",
        r"(?:同意|授权)[^。\n]{0,40}(?:照片|影像|图片)",
    ],
    "identifiable": [
        r"identifiable (?:photograph|image|photo)[^.\n]{0,80}"
        r"(?:participant|patient|subject|person|individual)",
        r"(?:participant|patient|subject)s?[^.\n]{0,60}(?:identifiable|recognizable)"
        r"[^.\n]{0,30}(?:photo|image|photograph)",
        r"photographs? of (?:patients|participants|subjects|volunteers)",
        r"(?:可识别|面部|正脸)[^。\n]{0,30}(?:照片|影像|图像|患者|受试者)",
        r"(?:患者|受试者|参与者)[^。\n]{0,30}(?:照片|影像|图像)",
    ],
    "data_statement": [
        r"data (?:are|is|will be) (?:not )?(?:publicly )?available",
        r"data (?:can|may) be (?:obtained|accessed|downloaded)",
        r"data (?:have|has) been (?:deposited|archived)",
        r"dataset (?:is|are|was|were|has been) (?:available|deposited|archived)",
        r"data availability",
        r"available (?:from|on|at|upon|in) [^.\n]{0,80}",
        r"upon (?:reasonable )?request", r"available from the (?:corresponding )?authors?",
        r"available on request", r"数据(?:可|将)?(?:向|在|通过|可从)[^。\n]{0,40}"
        r"(?:获取|获得|下载|索取)", r"数据可向[^。\n]{0,30}索取",
        r"数据(?:可)?(?:根据|按)请求提供", r"数据(?:不)?(?:公开|可用|可获取)",
        r"数据受限", r"数据集(?:已)?(?:存储|归档|可用)", r"补充材料",
    ],
    "data_repo": [
        r"\bdoi\b", r"\b10\.\d{4,9}/\S+", r"accession (?:number|code|no\.?)",
        r"\b(?:figshare|zenodo|dryad|osf|github|gene expression omnibus|"
        r"GEO|ArrayExpress|SRA|ENA|GenBank)\b", r"repository", r"存储库",
        r"数据仓库", r"登录号", r"supplementary (?:data|material|information)",
    ],
    "data_request": [
        r"upon (?:reasonable )?request", r"available (?:from|on) request",
        r"from the (?:corresponding )?authors?(?: upon request)?",
        r"向作者(?:索取|请求)", r"可向作者", r"按(?:合理)?请求提供",
    ],
    "data_restricted": [
        r"not publicly available", r"restricted access",
        r"data (?:are|is) not available",
        r"due to (?:privacy|confidentiality|ethical)",
        r"cannot be (?:shared|released|made public)", r"受限", r"不公开",
        r"保密", r"涉及隐私.{0,20}(?:不能|无法)(?:公开|共享)",
    ],
    "data_na": [
        r"no new data", r"no data (?:were|was) (?:generated|collected|analyzed)",
        r"data (?:are|is) not applicable", r"not applicable.{0,30}data",
        r"不涉及(?:新)?数据", r"无新数据", r"数据不适用",
    ],
    "coi": [
        r"competing interests?", r"conflicts? of interest", r"conflict of interest",
        r"declaration of (?:competing )?interests?", r"financial (?:disclosure|interest)",
        r"利益冲突", r"竞争性利益", r"财务披露",
    ],
    "coi_none": [
        r"no (?:competing|conflict of) interests?",
        r"authors? (?:declare|have) no (?:competing|conflict of) interests?",
        r"none declared", r"无利益冲突", r"不存在利益冲突",
        r"作者声明无(?:利益冲突|竞争性利益)",
    ],
    "commercial": [
        r"\b(?:inc|ltd|llc|gmbh|corp(?:oration)?|company|pharma(?:ceutical)?)\b",
        r"employee of", r"员工", r"公司", r"企业",
    ],
    "funding": [
        r"\bfunding\b", r"\bfunded by\b", r"\bsupported by\b",
        r"\bgrant (?:number|no\.?|#|award)",
        r"\baward (?:number|no\.?)", r"基金", r"资助", r"项目编号", r"课题号",
        r"supported by [^.\n]{0,80}",
    ],
    "funding_none": [
        r"no (?:specific |external )?funding", r"no financial support",
        r"not funded", r"no grant", r"无(?:专项)?(?:基金|资助|经费)",
        r"未获(?:得)?(?:基金|资助)", r"没有(?:基金|资助)",
    ],
    "contribution": [
        r"author contributions?", r"authors? contributed", r"\bCRediT\b",
        r"contributions?\s*[:：]", r"作者贡献", r"贡献声明",
    ],
    "contribution_role": [
        r"conceiv|design|collect|analy|wrote|written|perform|supervis|"
        r"interpret|draft|review|approv", r"设计|收集|分析|撰写|监督|实施|解释|修改",
    ],
    "acknowledgment": [r"acknowledg(?:e)?ments?", r"致谢"],
    "duplicate": [
        r"previously published", r"published (?:previously|elsewhere)", r"preprint",
        r"conference (?:paper|version|proceedings)",
        r"part of (?:a |the )?(?:thesis|dissertation)", r"secondary analysis",
        r"reanalysis", r"重复发表", r"预印本", r"会议论文", r"学位论文",
        r"二次分析", r"已(?:经)?发表",
    ],
    "plagiarism": [
        r"plagiar", r"text recycl", r"reused text", r"similarity (?:report|index)",
        r"查重", r"抄袭", r"剽窃", r"文字复用", r"重复率",
    ],
    "reproduced": [
        r"reproduced (?:from|with)", r"adapted (?:from|with)", r"taken from",
        r"reprinted (?:from|with)", r"转载自", r"改编自", r"引自",
        r"reproduced from a previous publication",
    ],
    "permission": [
        r"with permission", r"permission (?:was|has been|is) (?:obtained|granted)",
        r"licen[cs]ed under", r"\bCC[ -]?BY\b", r"copyright (?:holder|permission)",
        r"许可", r"授权", r"版权持有人",
    ],
    "no_permission": [
        r"without (?:a )?permission", r"no permission",
        r"permission (?:was )?not (?:obtained|granted)", r"未经(?:许可|授权)",
        r"未获(?:得)?(?:许可|授权)", r"无(?:许可|授权)",
    ],
    "third_party": [
        r"third[- ]party", r"copyrighted", r"all rights reserved", r"source:",
        r"图片来源", r"版权",
    ],
    "pii_id": [
        r"\b\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
        r"\b\d{15,18}[\dXx]\b",
    ],
    "pii_contact": [
        r"(?:phone|mobile|tel|电话|手机)\s*[:：]?\s*\+?\d[\d\s-]{7,}",
        r"(?:address|住址|家庭住址)\s*[:：]\s*[^\n]{6,}",
        r"\b\d{1,6}\s+[A-Z][A-Za-z]+\s+(?:Street|St|Road|Rd|Avenue|Ave)\b",
    ],
    "pii_explicit": [
        r"patient (?:names?|identifiers?|addresses?)",
        r"personal (?:data|information)",
        r"identifiable (?:personal )?information",
        r"患者姓名", r"个人信息", r"身份证", r"联系方式", r"住址",
    ],
    "guideline": [
        r"author guidelines?", r"instructions for authors",
        r"submission guidelines?", r"guide for authors",
        r"article (?:types?|processing charge)", r"word limit", r"reference style",
        r"投稿指南", r"作者须知", r"投稿要求", r"期刊要求",
    ],
    "target_journal": [
        r"target journal", r"submitted to", r"journal\s*[:：]",
        r"拟投(?:期刊|会议)", r"投稿期刊", r"目标期刊",
        r"target (?:conference|venue)",
    ],
    "design": [
        r"randomi[sz]ed (?:controlled )?trial", r"\bRCT\b", r"systematic review",
        r"meta-analysis", r"cohort study", r"case[- ]control", r"cross-sectional",
        r"animal experiment", r"随机对照", r"系统综述", r"荟萃分析",
        r"队列研究", r"病例对照", r"横断面", r"动物实验",
    ],
    "checklist": [
        r"\bCONSORT\b", r"\bPRISMA\b", r"\bSTROBE\b", r"\bARRIVE\b",
        r"reporting (?:guideline|checklist)", r"报告规范", r"报告清单",
    ],
    "declaration_heading": [
        r"declarations?", r"statements?", r"ethics", r"consent", r"conflict",
        r"competing", r"data availability", r"author contributions", r"funding",
        r"acknowledg", r"permissions?", r"copyright", r"licen[cs]e",
        r"声明", r"伦理", r"知情同意", r"利益冲突", r"数据可用性",
        r"作者贡献", r"基金", r"致谢", r"授权", r"许可", r"版权",
    ],
}
COMPILED = {key: [re.compile(p, re.I) for p in value] for key, value in P.items()}
SUPPLY_NOTE_RE = re.compile(
    r"(?:not|never)\s+(?:been\s+)?(?:supplied|provided|specified|reported|stated)"
    r"|(?:was|were|is|are)\s+not\s+(?:supplied|provided|specified|reported)"
    r"|not (?:been )?supplied|not specified|not provided|not reported"
    r"|未提供|未说明|未指定|未报告|没有提供|尚未提供", re.I)


def matched(text, key):
    return any(pattern.search(text or "") for pattern in COMPILED[key])


def block_key(item):
    return (item["doc"]["path"], item["block"]["locator"])


def find_blocks(docs, key, skip_headings=True, limit=30):
    found = []
    for doc in docs:
        for block in doc["blocks"]:
            if skip_headings and block["is_heading"]:
                continue
            for pattern in COMPILED[key]:
                match = pattern.search(block["text"])
                if match:
                    found.append({"doc": doc, "block": block, "match": match,
                                  "pattern": pattern.pattern})
                    break
            if len(found) >= limit:
                return found
    return found


def split_notes(items):
    normal, absent = [], []
    for item in items:
        if SUPPLY_NOTE_RE.search(item["block"]["text"]):
            absent.append(item)
        else:
            normal.append(item)
    return normal, absent


def evidence_item(item, supports="declaration", quote=None):
    doc, block, match = item["doc"], item["block"], item.get("match")
    if quote is None:
        if match is not None:
            quote = sentence_around(block["text"], match.start(), match.end())
        else:
            quote = block["text"][:420]
    quote = norm_space(quote)
    return {
        "path": doc["path"],
        "locator": block["locator"],
        "section": block["section"],
        "line_start": block["line_start"],
        "line_end": block["line_end"],
        "quote": quote,
        "supports": supports,
    }


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------
def dedupe_items(items):
    seen, out = set(), []
    for item in items or []:
        key = (item.get("path"), item.get("locator"), item.get("quote"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


class Collector:
    def __init__(self):
        self.findings = []
        self.uncertainties = []
        self.evidence = []
        self._unc_keys = set()

    def add_finding(self, facet, severity, issue_type, fix, evidence, impact,
                    false_positive, status="open", absence=None):
        evidence = dedupe_items([item for item in evidence
                                 if item and item.get("quote")])
        if not evidence:
            self.add_uncertainty(
                facet, "缺项没有可逐字引用的定位段落",
                "无法在报告中把该问题指回原文",
                "人工通读材料并记录章节/段落位置后再判定")
            return None
        if severity not in SEVERITIES:
            severity = "major"
        fid = "C%03d" % (len(self.findings) + 1)
        primary = evidence[0]
        finding = {
            "id": fid,
            "check_id": facet,
            "facet": facet,
            "rule_id": RULES[facet][0],
            "rule_version": "v1",
            "locator": {"path": primary["path"], "locator": primary["locator"],
                        "line_start": primary.get("line_start"),
                        "line_end": primary.get("line_end")},
            "original_quote": primary["quote"],
            "severity": severity,
            "type": TYPE_BY_FACET.get(facet, "compliance"),
            "issue_type": issue_type,
            "status": status,
            "fix": fix,
            "impact": impact,
            "false_positive_check": false_positive,
            "evidence": evidence,
        }
        if absence is not None:
            finding["absence_check"] = absence
        self.findings.append(finding)
        for item in evidence:
            self.evidence.append({**item, "claim_id": fid, "facet": facet})
        return fid

    def add_uncertainty(self, facet, reason, impact, action, location=None):
        key = (facet, reason)
        if key in self._unc_keys:
            return
        self._unc_keys.add(key)
        item = {
            "item": facet,
            "facet": facet,
            "reason": reason,
            "impact": impact,
            "action": action,
        }
        if location:
            item["location"] = location
        self.uncertainties.append(item)


# ---------------------------------------------------------------------------
# Facet evaluation
# ---------------------------------------------------------------------------
class Analyzer:
    def __init__(self, docs):
        self.docs = docs
        self.col = Collector()
        self.rows = []

    # -- helpers ---------------------------------------------------------
    def find(self, key, skip_headings=True, limit=30):
        return find_blocks(self.docs, key, skip_headings=skip_headings, limit=limit)

    def notes(self, key, skip_headings=True, limit=30):
        return split_notes(self.find(key, skip_headings=skip_headings, limit=limit))

    def informative(self, key, skip_headings=True, limit=30):
        return self.notes(key, skip_headings=skip_headings, limit=limit)[0]

    def ev(self, item, supports="declaration", quote=None):
        return evidence_item(item, supports=supports, quote=quote)

    def anchor(self):
        headings = [item for item in self.find("declaration_heading", skip_headings=False, limit=80)
                    if item["block"]["is_heading"]]
        if headings:
            return self.ev(headings[0], "absence_search_anchor")
        if self.docs and self.docs[0]["blocks"]:
            first = self.docs[0]["blocks"][0]
            return evidence_item({"doc": self.docs[0], "block": first, "match": None},
                                 "absence_search_anchor")
        return None

    def _exclude_no_scope(self, items):
        spans = {}
        for item in self.find("no_scope"):
            match = item.get("match")
            if match is None:
                continue
            spans.setdefault(block_key(item), []).append(
                (match.start(), match.end()))
        kept = []
        for item in items:
            match = item.get("match")
            if match is not None and any(
                    start <= match.start() and match.end() <= end
                    for start, end in spans.get(block_key(item), [])):
                continue
            kept.append(item)
        return kept

    def human_items(self):
        return self._exclude_no_scope(self.find("human"))

    def animal_items(self):
        return self._exclude_no_scope(self.find("animal"))

    def commercial_items(self):
        fund, _ = self.notes("funding")
        ack, _ = self.notes("acknowledgment")
        pool = {block_key(item) for item in fund + ack}
        return [item for item in self.find("commercial", limit=60)
                if block_key(item) in pool]

    def row(self, facet, status, reason, evidence=None, gaps=None, actions=None,
            severity="minor", applicability="applicable", manual_review=False,
            confidence="medium", finding_ids=None):
        rule_id, label = RULES[facet]
        self.rows.append({
            "check_id": facet,
            "facet": facet,
            "label_zh": label,
            "rule_id": rule_id,
            "rule_version": "v1",
            "status": status,
            "status_zh": STATUS_ZH[status],
            "applicability": applicability,
            "severity_if_issue": severity,
            "reason": reason,
            "evidence": dedupe_items(evidence),
            "gaps": gaps or [],
            "actions": actions or [],
            "manual_review": bool(manual_review),
            "confidence": confidence,
            "finding_ids": finding_ids or [],
        })

    # -- facets ----------------------------------------------------------
    def ethics(self):
        human, animal = self.human_items(), self.animal_items()
        no_scope = self.find("no_scope")
        ethics, ethics_absent = self.notes("ethics")
        identifiers = self.informative("ethics_id")
        if no_scope and not human and not animal:
            self.row(
                "ethics_approval", "not_applicable",
                "材料明确说明不涉及人或动物研究；未发现相反指标。",
                [self.ev(no_scope[0], "applicability")],
                confidence="high", manual_review=False)
            return
        if ethics:
            if identifiers:
                self.row(
                    "ethics_approval", "pass",
                    "报告了伦理审批并给出可识别的批件/编号信息；仍需人工核对批件真伪、有效期与覆盖范围。",
                    [self.ev(ethics[0]), self.ev(identifiers[0])],
                    manual_review=True, confidence="medium")
            else:
                fid = self.col.add_finding(
                    "ethics_approval", "major", "incomplete_declaration",
                    "在伦理声明中补充伦理委员会全称、批件编号、批准日期与覆盖范围"
                    "（研究、样本与数据）；若为豁免，写明豁免机构与依据。",
                    [self.ev(ethics[0])],
                    "仅有审批声明但缺少可核对编号与委员会名称，无法确认审批范围覆盖本研究。",
                    "已检出审批声明，问题是信息不足而不是未审批；具体必填字段以目标期刊/机构要求为准。",
                    status="needs_recheck")
                self.row(
                    "ethics_approval", "needs_recheck",
                    "有伦理审批声明，但未提供批件编号或委员会名称。",
                    [self.ev(ethics[0])],
                    gaps=["缺少伦理委员会名称、批件编号、批准日期与覆盖范围"],
                    actions=["补充上述字段；如为豁免，写明豁免依据与批准机构"],
                    severity="major", manual_review=True, confidence="medium",
                    finding_ids=[fid] if fid else [])
            if ethics_absent:
                self.col.add_uncertainty(
                    "ethics_approval", "输入中另有说明称伦理审批信息未提供",
                    "该说明可能与正文审批声明冲突",
                    "向作者确认哪一份材料为准",
                    ethics_absent[0]["doc"]["path"])
            return
        if human or animal:
            primary = (human or animal)[0]
            severity = "critical" if human else "major"
            fid = self.col.add_finding(
                "ethics_approval", severity, "missing_declaration",
                "补充伦理委员会全称、批件编号、批准日期与适用范围；若研究获豁免，"
                "写明豁免机构、依据与日期。",
                [self.ev(primary, "applicability")],
                "材料显示涉及人/动物研究，但未报告伦理审批；这是投稿前必须补齐的合规缺口。",
                "仅表示输入材料未检出审批声明，不推断实际未审批；若审批材料在补充文件中，"
                "应在声明里给出可核对信息。")
            self.row(
                "ethics_approval", "issue",
                "材料显示涉及人/动物研究，但未检出伦理审批声明。",
                [self.ev(primary, "applicability")],
                gaps=["未报告伦理审批、批件编号或豁免依据"],
                actions=["补充伦理委员会/批件信息；如为豁免，写明豁免依据"],
                severity=severity, manual_review=True, confidence="medium",
                finding_ids=[fid] if fid else [])
            return
        self.row(
            "ethics_approval", "not_checked",
            "材料未显示是否涉及人或动物研究，无法判断伦理审批是否适用。",
            [self.ev(ethics_absent[0], "applicability")] if ethics_absent else [],
            gaps=["未提供研究类型或伦理适用性说明"],
            actions=["确认研究是否涉及人/动物；如涉及，补充审批信息"],
            severity="major", applicability="unknown", manual_review=True,
            confidence="low")

    def consent(self):
        human = self.human_items()
        identifiable = self.find("identifiable")
        consent, consent_absent = self.notes("consent")
        waiver = self.informative("consent_waiver")
        image_consent = self.informative("consent_image")
        if waiver:
            self.row(
                "informed_consent", "pass",
                "材料报告了知情同意豁免；仍需人工核对豁免机构、依据与适用范围。",
                [self.ev(waiver[0])], manual_review=True, confidence="medium")
            return
        if consent:
            if identifiable and not image_consent:
                fid = self.col.add_finding(
                    "informed_consent", "major", "incomplete_declaration",
                    "在知情同意的说明中明确覆盖可识别照片/影像的发表范围、传播渠道与"
                    "撤回方式；若同意书未覆盖，需重新取得发表授权或对图像去标识化。",
                    [self.ev(consent[0], "declaration"),
                     self.ev(identifiable[0], "applicability")],
                    "材料含可识别人物图像，但同意声明未说明是否覆盖图像发表。",
                    "同意声明本身存在，问题只在图像发表范围；不要据此推断未取得同意。",
                    status="needs_recheck")
                self.row(
                    "informed_consent", "needs_recheck",
                    "有知情同意声明，但未明确覆盖可识别照片/影像的发表。",
                    [self.ev(consent[0]), self.ev(identifiable[0])],
                    gaps=["同意范围未覆盖可识别图像的公开发表"],
                    actions=["补充图像发表授权或去标识化/打码"],
                    severity="major", manual_review=True, confidence="medium",
                    finding_ids=[fid] if fid else [])
            else:
                self.row(
                    "informed_consent", "pass",
                    "检出了知情同意声明；同意书原件与签署范围仍需人工核对。",
                    [self.ev(consent[0])], manual_review=True, confidence="medium")
            if consent_absent:
                self.col.add_uncertainty(
                    "informed_consent", "输入中另有说明称同意书或其范围未提供",
                    "无法核对同意书覆盖范围（尤其是图像发表）",
                    "向作者索取同意书模板与签署范围", consent_absent[0]["doc"]["path"])
            return
        if human or identifiable:
            primary = (human or identifiable)[0]
            severity = "critical" if identifiable else "major"
            fid = self.col.add_finding(
                "informed_consent", severity, "missing_declaration",
                "补充知情同意说明（谁、何时、以何种形式取得）或豁免依据；涉及可识别"
                "图像时，单独说明发表授权。",
                [self.ev(primary, "applicability")],
                "材料显示涉及人或可识别图像，但未检出知情同意/豁免声明。",
                "仅表示输入材料未检出同意声明，不推断实际未取得同意；如同意在补充材料中，"
                "正文应给出可核对说明。")
            self.row(
                "informed_consent", "issue",
                "材料显示涉及人/可识别图像，但未检出知情同意或豁免声明。",
                [self.ev(primary, "applicability")],
                gaps=["未报告知情同意或豁免"],
                actions=["补充同意说明或豁免依据；图像单列发表授权"],
                severity=severity, manual_review=True, confidence="medium",
                finding_ids=[fid] if fid else [])
            return
        self.row(
            "informed_consent", "not_checked",
            "未检出人研究或可识别图像的适用性说明，无法判断知情同意是否适用。",
            [self.ev(consent_absent[0], "applicability")] if consent_absent else [],
            severity="major", applicability="unknown", manual_review=True,
            confidence="low")

    def data_availability(self):
        statements, absent = self.notes("data_statement")
        repo, request = self.informative("data_repo"), self.informative("data_request")
        restricted = self.informative("data_restricted")
        not_applicable = self.informative("data_na")
        if statements:
            if not_applicable:
                self.row(
                    "data_availability", "pass",
                    "声明无新数据或数据可用性不适用；仍需人工确认没有未披露的支撑数据。",
                    [self.ev(statements[0])], manual_review=True, confidence="medium")
            elif repo:
                self.row(
                    "data_availability", "pass",
                    "报告了数据可用性并给出仓库/DOI/登录号/补充材料等可核对入口。",
                    [self.ev(statements[0]), self.ev(repo[0])],
                    manual_review=True, confidence="medium")
            elif restricted:
                fid = self.col.add_finding(
                    "data_availability", "major", "incomplete_declaration",
                    "说明受限原因、访问条件、申请流程、审批主体与联系人；在不违反隐私/"
                    "伦理的前提下给出可复核的最小数据集或访问入口。",
                    [self.ev(statements[0])],
                    "数据被标注受限，但没有说明如何申请访问，读者/审稿人无法核验可获取性。",
                    "受限数据本身可能是合规的；问题仅是访问条件未报告。",
                    status="needs_recheck")
                self.row(
                    "data_availability", "needs_recheck",
                    "声明数据受限，但未说明访问条件与申请流程。",
                    [self.ev(statements[0])],
                    gaps=["受限数据缺访问条件/申请流程"],
                    actions=["补充受限原因、申请流程与联系人"],
                    severity="major", manual_review=True,
                    finding_ids=[fid] if fid else [])
            elif request:
                fid = self.col.add_finding(
                    "data_availability", "major", "incomplete_declaration",
                    "优先给出仓库/DOI/登录号；若只能按请求提供，补充数据范围、联系人、"
                    "响应时限与使用条件。",
                    [self.ev(statements[0])],
                    "仅承诺按请求提供数据，缺少可核对的仓库或访问条件，复现成本高。",
                    "按请求提供是常见做法，是否可接受取决于目标期刊/资助方政策；"
                    "本项只报可核验性不足。",
                    status="needs_recheck")
                self.row(
                    "data_availability", "needs_recheck",
                    "有数据可用性声明，但仅承诺按请求提供，未给出可核对入口。",
                    [self.ev(statements[0])],
                    gaps=["未给出仓库/DOI/登录号或访问条件"],
                    actions=["补充仓库/DOI；或写明数据范围、联系人与使用条件"],
                    severity="major", manual_review=True,
                    finding_ids=[fid] if fid else [])
            else:
                fid = self.col.add_finding(
                    "data_availability", "minor", "incomplete_declaration",
                    "补充明确的获取方式（仓库/DOI/补充材料/申请流程）与使用条件。",
                    [self.ev(statements[0])],
                    "有数据可用性声明，但未说明具体获取方式。",
                    "可能是声明写法过于笼统，不等于数据不可用。",
                    status="needs_recheck")
                self.row(
                    "data_availability", "needs_recheck",
                    "有数据可用性声明，但未说明具体获取方式。",
                    [self.ev(statements[0])],
                    gaps=["缺具体获取入口/条件"],
                    actions=["补充仓库/DOI 或申请流程"],
                    severity="minor", manual_review=True,
                    finding_ids=[fid] if fid else [])
            return
        fid = self.col.add_finding(
            "data_availability", "major", "missing_declaration",
            "补充数据可用性声明：可用数据给出仓库/DOI/登录号；受限数据说明原因与"
            "申请流程；无新数据时明确说明。",
            [self.anchor()],
            "材料未报告数据可用性，读者无法判断数据能否获取与复现。",
            "不同期刊/学科要求不同；本项只报'未报告'，不判定学术不端。",
            absence={"scope": "all supplied material", "patterns_checked":
                     ["data availability", "available from/upon request",
                      "repository/DOI/accession", "数据可用性/索取"],
                     "matches": 0})
        self.row(
            "data_availability", "issue",
            "材料未报告数据可用性声明。",
            [self.anchor()] if self.anchor() else [],
            gaps=["未报告数据可用性/获取条件"],
            actions=["按目标期刊要求补充数据声明"],
            severity="major", manual_review=True,
            finding_ids=[fid] if fid else [])
        if absent:
            self.col.add_uncertainty(
                "data_availability", "输入中另有说明称数据政策/声明未提供",
                "无法核对开放数据或受限数据的实际条件",
                "向作者/资助方索取数据政策与声明", absent[0]["doc"]["path"])

    def conflict_of_interest(self):
        coi, coi_absent = self.notes("coi")
        none = [item for item in coi if matched(item["block"]["text"], "coi_none")]
        commercial = self.commercial_items()
        if none and commercial:
            fid = self.col.add_finding(
                "conflict_of_interest", "major", "inconsistent_declaration",
                "逐项披露与商业机构的关系（资助、设备、编辑/写作协助、顾问、股权等），"
                "并说明作者是否参与数据解释与投稿决定；不要只写'无利益冲突'。",
                [self.ev(none[0]), self.ev(commercial[0], "applicability")],
                "声明'无利益冲突'，但基金/致谢显示商业机构资助或参与，声明与材料不一致。",
                "商业资助不自动等于利益冲突；问题是声明未覆盖已出现的商业关系，需作者澄清。",
                status="needs_recheck")
            self.row(
                "conflict_of_interest", "needs_recheck",
                "利益冲突声明与基金/致谢中的商业关系不一致。",
                [self.ev(none[0]), self.ev(commercial[0])],
                gaps=["未披露商业资助/企业参与/编辑协助"],
                actions=["补充逐项披露并说明作者角色"],
                severity="major", manual_review=True,
                finding_ids=[fid] if fid else [])
            return
        if coi:
            self.row(
                "conflict_of_interest", "pass",
                "检出了利益冲突声明；仍需人工核对声明是否覆盖所有作者与商业关系。",
                [self.ev(coi[0])], manual_review=True, confidence="medium")
            return
        if commercial:
            fid = self.col.add_finding(
                "conflict_of_interest", "major", "missing_declaration",
                "补充利益冲突声明，逐项披露商业资助、设备/材料提供、编辑协助与其他"
                "可能影响判断的关系。",
                [self.ev(commercial[0], "applicability")],
                "材料显示商业机构资助或参与，但未检出利益冲突声明。",
                "仅表示未检出声明；商业资助本身不构成不端，需作者完整披露。")
            self.row(
                "conflict_of_interest", "issue",
                "存在商业资助/企业参与，但未检出利益冲突声明。",
                [self.ev(commercial[0], "applicability")],
                gaps=["未报告利益冲突"],
                actions=["补充逐项披露"],
                severity="major", manual_review=True,
                finding_ids=[fid] if fid else [])
            return
        anchor = self.anchor()
        fid = self.col.add_finding(
            "conflict_of_interest", "major", "missing_declaration",
            "补充利益冲突声明；无冲突时写明'所有作者声明无利益冲突'。",
            [anchor],
            "材料未报告利益冲突，读者无法判断作者是否存在可能影响结论的关系。",
            "部分期刊允许在投稿系统中单独填写；本项只报正文/声明材料未报告。",
            absence={"scope": "all supplied material", "patterns_checked":
                     ["competing/conflict of interest", "利益冲突"], "matches": 0})
        self.row(
            "conflict_of_interest", "issue",
            "材料未报告利益冲突声明。",
            [anchor] if anchor else [],
            gaps=["未报告利益冲突"],
            actions=["补充无冲突声明或逐项披露"],
            severity="major", manual_review=True,
            finding_ids=[fid] if fid else [])
        if coi_absent:
            self.col.add_uncertainty(
                "conflict_of_interest", "输入中另有说明称利益冲突信息未提供",
                "无法核对是否存在未披露关系",
                "向所有作者收集利益冲突声明", coi_absent[0]["doc"]["path"])

    def funding(self):
        funding, absent = self.notes("funding")
        commercial = self.commercial_items()
        no_funding = self.informative("funding_none")
        if funding:
            if no_funding:
                self.row(
                    "funding", "pass",
                    "材料明确声明无资助；仍需人工确认致谢中无未披露的经费/企业支持。",
                    [self.ev(funding[0])], manual_review=True, confidence="medium")
                return
            text = norm_space(" ".join(item["block"]["text"] for item in funding))
            specific = bool(re.search(r"[A-Z]{2,}[- ]?\d|\b\d{3,}\b|基金|grant|项目", text, re.I))
            if specific:
                self.row(
                    "funding", "pass",
                    "检出了基金/资助信息与可识别的资助方或项目编号。",
                    [self.ev(funding[0])], manual_review=True, confidence="medium")
            else:
                self.row(
                    "funding", "needs_recheck",
                    "有资助声明，但缺少可核对的资助方或项目编号。",
                    [self.ev(funding[0])],
                    gaps=["资助信息过于笼统"],
                    actions=["补充资助方全称与项目编号；无资助时明确说明"],
                    severity="minor", manual_review=True, confidence="medium")
                self.col.add_uncertainty(
                    "funding", "资助声明存在但信息不足以核对",
                    "无法确认资助方与项目范围",
                    "向作者索取项目编号或资助方确认", funding[0]["doc"]["path"])
            return
        if commercial:
            fid = self.col.add_finding(
                "funding", "major", "missing_declaration",
                "补充基金声明，写明资助方、项目编号与资助范围；同时核对是否需要披露"
                "设备/材料提供的商业关系。",
                [self.ev(commercial[0], "applicability")],
                "致谢/材料中出现商业机构参与，但未检出基金或资助声明。",
                "商业机构提供设备或协助不一定属于资助；仍需作者在基金/致谢/利益冲突"
                "中说明。")
            self.row(
                "funding", "issue",
                "出现商业机构参与，但未检出基金/资助声明。",
                [self.ev(commercial[0], "applicability")],
                gaps=["未报告资助来源", "商业参与性质未说明"],
                actions=["补充基金声明并同步披露商业关系"],
                severity="major", manual_review=True,
                finding_ids=[fid] if fid else [])
            return
        self.row(
            "funding", "needs_recheck",
            "未检出基金/资助声明；也可能确实无资助，需作者确认。",
            [self.ev(absent[0], "applicability")] if absent else [],
            gaps=["未报告资助或'无资助'声明"],
            actions=["补充资助信息或明确写'无专项资助'"],
            severity="minor", applicability="unknown", manual_review=True,
            confidence="low")
        self.col.add_uncertainty(
            "funding", "未检出基金/资助声明",
            "无法判断是确实无资助还是漏报",
            "向作者确认并补充'无资助'或项目信息")

    def author_contributions(self):
        contributions, absent = self.notes("contribution")
        if contributions:
            text = norm_space(" ".join(item["block"]["text"] for item in contributions))
            if matched(text, "contribution_role"):
                self.row(
                    "author_contributions", "pass",
                    "检出了带角色描述的作者贡献声明；仍需对照作者列表核对覆盖范围。",
                    [self.ev(contributions[0])], manual_review=True, confidence="medium")
                self.col.add_uncertainty(
                    "author_contributions", "输入未提供完整作者列表",
                    "无法核对贡献声明是否覆盖所有作者",
                    "对照投稿系统作者列表逐人核对")
            else:
                fid = self.col.add_finding(
                    "author_contributions", "minor", "incomplete_declaration",
                    "按 CRediT 或目标期刊要求写明每位作者的具體角色（构思、方法、"
                    "数据、分析、写作、监督等）。",
                    [self.ev(contributions[0])],
                    "贡献声明缺少可核对的具体角色。",
                    "部分学科使用简写贡献声明；若目标期刊接受则该写法可通过，需人工确认。",
                    status="needs_recheck")
                self.row(
                    "author_contributions", "needs_recheck",
                    "有贡献声明，但缺少具体角色描述。",
                    [self.ev(contributions[0])],
                    gaps=["贡献声明未写具体角色"],
                    actions=["按 CRediT/期刊要求补充角色"],
                    severity="minor", manual_review=True,
                    finding_ids=[fid] if fid else [])
            return
        anchor = self.anchor()
        fid = self.col.add_finding(
            "author_contributions", "major", "missing_declaration",
            "补充作者贡献声明，逐人写明角色；通讯作者对数据与结论负责。",
            [anchor],
            "材料未报告作者贡献，无法核对署名与责任分配。",
            "部分期刊允许在投稿系统填写；本项只报声明材料未报告。",
            absence={"scope": "all supplied material", "patterns_checked":
                     ["author contributions", "CRediT", "作者贡献"], "matches": 0})
        self.row(
            "author_contributions", "issue",
            "材料未报告作者贡献声明。",
            [anchor] if anchor else [],
            gaps=["未报告作者贡献"],
            actions=["补充逐人贡献声明"],
            severity="major", manual_review=True,
            finding_ids=[fid] if fid else [])
        if absent:
            self.col.add_uncertainty(
                "author_contributions", "输入中另有说明称贡献信息未提供",
                "无法核对署名与责任",
                "向通讯作者索取贡献声明", absent[0]["doc"]["path"])

    def acknowledgments(self):
        ack, _ = self.notes("acknowledgment")
        if ack:
            self.row(
                "acknowledgments", "pass",
                "检出了致谢；仍需人工核对被致谢者是否同意、是否涉及商业关系。",
                [self.ev(ack[0])], manual_review=True, confidence="medium")
        else:
            self.row(
                "acknowledgments", "not_checked",
                "未检出致谢；致谢通常是可选项，不能据此判定为问题。",
                applicability="unknown", manual_review=True, confidence="low")

    def duplicate_publication(self):
        duplicate, _ = self.notes("duplicate")
        if duplicate:
            text = norm_space(" ".join(item["block"]["text"] for item in duplicate))
            severity = "major" if re.search(r"previously published|已(?:经)?发表", text, re.I) else "minor"
            fid = self.col.add_finding(
                "duplicate_publication", severity, "disclosure_requires_check",
                "提供先前版本/预印本/会议论文/学位论文的完整引用，说明增量、重复使用"
                "范围与许可；按目标期刊重复发表政策核对。",
                [self.ev(duplicate[0])],
                "材料披露了先前版本或二次分析，需核对与本次投稿的重复程度及许可。",
                "披露本身通常是合规行为；问题在于需要比对版本与许可，不能仅凭声明判定。",
                status="needs_recheck")
            self.row(
                "duplicate_publication", "needs_recheck",
                "材料披露了先前版本/预印本/二次分析，需人工比对重复程度。",
                [self.ev(duplicate[0])],
                gaps=["缺少先前版本引用、增量说明或许可"],
                actions=["补全引用与增量说明，核对目标期刊政策"],
                severity=severity, manual_review=True,
                finding_ids=[fid] if fid else [])
            return
        self.row(
            "duplicate_publication", "not_checked",
            "输入未提供历史发表清单或查重语料，本工具不联网检索，无法判断重复发表。",
            applicability="unknown", manual_review=True, confidence="low")
        self.col.add_uncertainty(
            "duplicate_publication", "未提供历史发表清单/检索结果",
            "无法判断是否存在重复发表或文本复用",
            "人工检索作者历史发表与预印本，或要求作者提供声明")

    def plagiarism(self):
        plagiarism, _ = self.notes("plagiarism")
        if plagiarism:
            self.row(
                "plagiarism", "needs_recheck",
                "材料提及查重/相似度报告，但未提供报告本体，无法核对。",
                [self.ev(plagiarism[0])],
                gaps=["缺少查重报告/相似度结果"],
                actions=["提供查重报告并人工核对高相似片段"],
                severity="major", manual_review=True,
                finding_ids=[])
        else:
            self.row(
                "plagiarism", "not_checked",
                "未提供查重报告或可比对语料；本工具不做联网相似度检索，也不把"
                "'未发现'写成'无抄袭'。",
                applicability="unknown", manual_review=True, confidence="low")
        self.col.add_uncertainty(
            "plagiarism", "无外部查重语料/相似度报告",
            "无法对抄袭做肯定或否定判断",
            "使用机构查重系统并人工核对高相似片段")

    def text_reuse(self):
        index = {}
        for doc in self.docs:
            for block in doc["blocks"]:
                if block["is_heading"]:
                    continue
                for match in re.finditer(r"[^。！？.!?\n]{20,400}[。！？.!?]?", block["text"]):
                    sentence = norm_space(match.group(0))
                    if len(sentence) < 60 and cjk_count(sentence) < 30:
                        continue
                    key = norm_key(sentence)
                    index.setdefault(key, []).append((doc, block, sentence))
        pairs, seen = [], set()
        for key, occurrences in index.items():
            if len(occurrences) < 2:
                continue
            first = occurrences[0]
            for other in occurrences[1:]:
                if first[0]["path"] == other[0]["path"]:
                    continue
                pair_key = tuple(sorted([first[0]["path"] + first[1]["locator"],
                                         other[0]["path"] + other[1]["locator"]]))
                if pair_key in seen:
                    continue
                seen.add(pair_key)
                pairs.append((first, other))
                if len(pairs) >= 5:
                    break
            if len(pairs) >= 5:
                break
        if pairs:
            evidence = []
            for first, other in pairs[:3]:
                evidence.append(evidence_item(
                    {"doc": first[0], "block": first[1], "match": None},
                    "reuse_a", first[2]))
                evidence.append(evidence_item(
                    {"doc": other[0], "block": other[1], "match": None},
                    "reuse_b", other[2]))
            fid = self.col.add_finding(
                "text_reuse", "minor", "potential_text_reuse",
                "逐段核对重复句是否为方法模板/政策文本/合理引用；如为重复使用，"
                "补充引用、改写或说明许可。",
                evidence,
                "输入材料之间存在逐字重复句，需排除合理模板与引用后再判断。",
                "可能是方法模板、伦理声明或期刊要求的标准表述；此信号不是抄袭结论。",
                status="needs_recheck")
            self.row(
                "text_reuse", "needs_recheck",
                "输入材料内部检出 %d 组跨文件逐字重复句。" % len(pairs),
                evidence[:4],
                gaps=["需人工核对重复句的性质与许可"],
                actions=["比对上下文，必要时改写并引用来源"],
                severity="minor", manual_review=True,
                finding_ids=[fid] if fid else [])
        else:
            self.row(
                "text_reuse", "not_checked",
                "未做外部查重；仅在输入材料内部未检出足够长的跨文件逐字重复句。",
                applicability="unknown", manual_review=True, confidence="low")

    def copyright_license(self):
        reproduced = self.find("reproduced")
        permission = self.informative("permission")
        no_permission = self.find("no_permission")
        third_party = self.find("third_party")
        if reproduced:
            if no_permission:
                fid = self.col.add_finding(
                    "copyright_license", "major", "permission_gap",
                    "补充版权持有人书面许可或替换为原创图/表；标注来源、许可类型、"
                    "适用版本与传播范围。",
                    [self.ev(reproduced[0]), self.ev(no_permission[0], "applicability")],
                    "材料明确提到复用第三方图/表但缺少许可，属于可核验的版权缺口。",
                    "复用声明本身不等于侵权；问题仅是输入材料未提供许可信息。")
                self.row(
                    "copyright_license", "issue",
                    "复用第三方图/表但未提供许可声明。",
                    [self.ev(reproduced[0]), self.ev(no_permission[0])],
                    gaps=["缺少许可/授权信息"],
                    actions=["取得书面许可或替换材料"],
                    severity="major", manual_review=True,
                    finding_ids=[fid] if fid else [])
            elif permission:
                self.row(
                    "copyright_license", "pass",
                    "检出复用声明与许可/授权信息；仍需人工核对许可地域、格式与传播范围。",
                    [self.ev(reproduced[0]), self.ev(permission[0])],
                    manual_review=True, confidence="medium")
            else:
                fid = self.col.add_finding(
                    "copyright_license", "major", "incomplete_declaration",
                    "补充来源、版权持有人与许可类型；若许可未覆盖本文用途，需重新取得授权。",
                    [self.ev(reproduced[0])],
                    "检出第三方材料复用，但未说明许可范围。",
                    "可能许可信息在补充材料中；本项只报输入材料未报告。",
                    status="needs_recheck")
                self.row(
                    "copyright_license", "needs_recheck",
                    "复用第三方材料，但未说明许可范围。",
                    [self.ev(reproduced[0])],
                    gaps=["缺少来源/许可范围"],
                    actions=["补充许可信息或替换材料"],
                    severity="major", manual_review=True,
                    finding_ids=[fid] if fid else [])
            return
        if third_party:
            self.row(
                "copyright_license", "needs_recheck",
                "材料提及第三方/版权材料，但未给出完整来源与许可。",
                [self.ev(third_party[0])],
                gaps=["第三方材料来源/许可不完整"],
                actions=["逐项登记来源、许可与适用条件"],
                severity="minor", manual_review=True)
        else:
            self.row(
                "copyright_license", "not_checked",
                "输入未提供图表/代码/数据的来源与许可清单，无法核验第三方材料版权。",
                applicability="unknown", manual_review=True, confidence="low")
            self.col.add_uncertainty(
                "copyright_license", "未提供第三方材料清单与许可",
                "无法核验图片、表格、代码或数据集的复用条件",
                "逐项登记来源、许可类型与适用条件")

    def privacy(self):
        identifiable = self.find("identifiable")
        pii = (self.informative("pii_id") + self.informative("pii_contact")
               + self.informative("pii_explicit"))
        image_consent = self.informative("consent_image")
        if pii:
            fid = self.col.add_finding(
                "privacy", "critical", "potential_privacy_risk",
                "删除或脱敏个人标识信息；如确需保留，说明法律/伦理依据、访问控制与"
                "最小化措施。",
                [self.ev(pii[0], "applicability")],
                "材料中出现可识别个人信息，存在隐私与数据保护风险。",
                "该文本可能是作者联系方式或示例数据；需人工确认主体与公开范围，"
                "不能仅凭正则命中判定违规。",
                status="open")
            self.row(
                "privacy", "issue",
                "检出可能的可识别个人信息。",
                [self.ev(pii[0])],
                gaps=["未说明去标识化/访问控制措施"],
                actions=["脱敏或取得明确授权，并说明数据保护措施"],
                severity="critical", manual_review=True,
                finding_ids=[fid] if fid else [])
            return
        if identifiable:
            if image_consent:
                self.row(
                    "privacy", "pass",
                    "可识别图像存在，且检出了覆盖图像发表的同意/授权；仍需人工核对"
                    "同意书范围与去标识化措施。",
                    [self.ev(identifiable[0]), self.ev(image_consent[0])],
                    manual_review=True, confidence="medium")
            else:
                fid = self.col.add_finding(
                    "privacy", "major", "potential_privacy_risk",
                    "对图像去标识化/打码，或取得覆盖公开发表的书面同意，并说明访问控制。",
                    [self.ev(identifiable[0], "applicability")],
                    "材料含可识别人物图像，但未见覆盖发表的同意/授权。",
                    "可识别图像不等于违规；问题在于公开范围与授权未在材料中说明。",
                    status="needs_recheck")
                self.row(
                    "privacy", "needs_recheck",
                    "含可识别人物图像，但未说明发表授权/去标识化。",
                    [self.ev(identifiable[0])],
                    gaps=["缺少图像发表授权或去标识化说明"],
                    actions=["取得发表授权或打码/去标识化"],
                    severity="major", manual_review=True,
                    finding_ids=[fid] if fid else [])
            return
        self.row(
            "privacy", "not_checked",
            "未检出可识别个人信息或图像；输入也未提供数据管理/去标识化说明，"
            "无法做全面隐私核验。",
            applicability="unknown", manual_review=True, confidence="low")
        self.col.add_uncertainty(
            "privacy", "未提供数据管理/去标识化说明",
            "无法核验受限数据、个人信息与访问控制",
            "人工检查原始数据流与补充材料")

    def submission_requirements(self):
        guidelines, guide_absent = self.notes("guideline")
        targets, target_absent = self.notes("target_journal")
        if guidelines:
            self.row(
                "submission_requirements", "needs_recheck",
                "输入提供了作者指南类文本；本工具不自动判定全部格式条款，需逐条对照"
                "版本与日期。",
                [self.ev(guidelines[0])],
                gaps=["未逐条核验字数、图表、参考文献格式与材料清单"],
                actions=["按指南版本/日期建立核对表并逐条映射稿件"],
                severity="major", manual_review=True, confidence="medium")
            self.col.add_uncertainty(
                "submission_requirements", "指南文本已提供但未机器核验全部条款",
                "无法确认是否满足所有投稿要求",
                "人工逐条对照指南版本/日期", guidelines[0]["doc"]["path"])
            return
        if targets:
            fid = self.col.add_finding(
                "submission_requirements", "major", "missing_declaration",
                "提供目标期刊/会议的作者指南版本与日期，并逐条核对格式、字数、图表、"
                "参考文献与声明要求；在此之前不做合规判定。",
                [self.ev(targets[0], "target_journal")],
                "给出了目标期刊/会议，但没有提供可核对的指南文本。",
                "未提供指南不等于稿件不合规；本项只报无法核验。",
                status="needs_recheck")
            self.row(
                "submission_requirements", "needs_recheck",
                "给出了目标期刊/会议，但未提供指南文本。",
                [self.ev(targets[0])],
                gaps=["缺少作者指南版本/日期"],
                actions=["提供指南并逐条核对"],
                severity="major", manual_review=True,
                finding_ids=[fid] if fid else [])
            return
        evidence = []
        if guide_absent:
            evidence.append(self.ev(guide_absent[0], "applicability"))
        if target_absent:
            evidence.append(self.ev(target_absent[0], "applicability"))
        self.row(
            "submission_requirements", "not_checked",
            "输入未提供目标期刊/会议或作者指南，无法判定投稿要求合规。",
            evidence,
            gaps=["缺少目标期刊/会议指南"],
            actions=["提供指南版本/日期后再判定"],
            severity="major", applicability="unknown", manual_review=True,
            confidence="low")
        self.col.add_uncertainty(
            "submission_requirements", "未提供目标期刊/会议作者指南",
            "无法判定字数、格式、图表与声明要求",
            "提供指南版本/日期，或明确说明不按特定期刊要求审查")

    def reporting_checklist(self):
        designs = self.find("design")
        checklists = self.informative("checklist")
        if designs and checklists:
            self.row(
                "reporting_checklist", "pass",
                "检出研究设计指标与对应报告清单的引用；仍需人工核对清单版本与条目覆盖。",
                [self.ev(designs[0]), self.ev(checklists[0])],
                manual_review=True, confidence="medium")
        elif designs:
            fid = self.col.add_finding(
                "reporting_checklist", "minor", "incomplete_declaration",
                "按研究设计引用并提交对应报告清单（如 CONSORT/PRISMA/STROBE/ARRIVE，"
                "以目标期刊要求为准），并核对版本。",
                [self.ev(designs[0], "applicability")],
                "材料显示了可识别的报告规范适用设计，但未检出清单引用。",
                "不同期刊对报告清单要求不同；本项只报未检出，不判定违规。",
                status="needs_recheck")
            self.row(
                "reporting_checklist", "needs_recheck",
                "材料显示可识别的报告规范适用设计，但未检出清单引用。",
                [self.ev(designs[0])],
                gaps=["未检出报告清单/规范引用"],
                actions=["按目标期刊要求补充报告清单"],
                severity="minor", manual_review=True,
                finding_ids=[fid] if fid else [])
        else:
            self.row(
                "reporting_checklist", "not_checked",
                "未检出可识别的报告规范适用设计（RCT/系统综述/队列/动物实验等）。",
                applicability="unknown", manual_review=True, confidence="low")

    def run(self):
        methods = {
            "ethics_approval": self.ethics,
            "informed_consent": self.consent,
            "data_availability": self.data_availability,
            "conflict_of_interest": self.conflict_of_interest,
            "funding": self.funding,
            "author_contributions": self.author_contributions,
            "acknowledgments": self.acknowledgments,
            "duplicate_publication": self.duplicate_publication,
            "plagiarism": self.plagiarism,
            "text_reuse": self.text_reuse,
            "copyright_license": self.copyright_license,
            "privacy": self.privacy,
            "submission_requirements": self.submission_requirements,
            "reporting_checklist": self.reporting_checklist,
        }
        for name in FACET_ORDER:
            methods[name]()
        return self.rows, self.col


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def status_of(rows):
    if not rows:
        return "input_error"
    if any(row["status"] == "issue" for row in rows):
        return "issues"
    if any(row["status"] in ("needs_recheck", "not_checked") for row in rows):
        return "needs_recheck"
    return "pass"


def check_status(row):
    mapping = {
        "pass": "pass",
        "not_applicable": "pass",
        "issue": "fail",
        "needs_recheck": "needs_recheck",
        "not_checked": "not_checked",
    }
    return mapping.get(row["status"], "not_checked")


def guideline_source(docs, rows):
    guideline_evidence = None
    for row in rows:
        if row["check_id"] != "submission_requirements":
            continue
        if row["status"] not in ("needs_recheck", "pass"):
            continue
        for item in row["evidence"]:
            if item.get("supports") == "declaration":
                guideline_evidence = item
                break
        if guideline_evidence:
            break
    if guideline_evidence:
        evidence = guideline_evidence
        text = evidence.get("quote") or ""
        for doc in docs:
            if doc.get("path") != evidence.get("path"):
                continue
            for block in doc.get("blocks") or []:
                if block.get("locator") == evidence.get("locator"):
                    text = block.get("text") or text
                    break
            break
        match = re.search(r"(?:version|v)\s*([0-9][0-9A-Za-z._-]*)", text, re.I)
        version = match.group(1) if match else "unspecified"
        date = re.search(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\b", text)
        return {
            "name": evidence.get("section") or Path(evidence["path"]).name,
            "version": version,
            "date": date.group(1) if date else "",
            "source_path": evidence["path"],
            "locator": evidence["locator"],
            "note": "指南文本已由输入提供；本工具未逐条核验全部条款。",
        }
    return {
        "name": "未提供目标期刊/会议作者指南",
        "version": "unavailable",
        "date": "",
        "source_path": "",
        "locator": "",
        "used_for_verdicts": False,
        "note": "输入未提供可核对的指南版本/日期；本工具不做期刊格式合规判定。",
    }


def dedupe_evidence(evidence):
    seen, out = set(), []
    for item in evidence:
        key = (item.get("path"), item.get("locator"), item.get("quote"),
               item.get("claim_id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_payload(case_id, docs, warnings, rows, collector):
    status = status_of(rows)
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    evidence_map = dedupe_evidence(collector.evidence)
    matrix_evidence = []
    for row in rows:
        for item in row["evidence"]:
            matrix_evidence.append({**item, "claim_id": row["check_id"],
                                    "facet": row["check_id"]})
    evidence_map = dedupe_evidence(matrix_evidence + evidence_map)
    uncertainties = list(collector.uncertainties)
    for item in warnings:
        uncertainties.append({
            "item": "materials",
            "facet": "input",
            "reason": "%s: %s" % (item.get("path"), item.get("reason")),
            "impact": "该文件没有被完整读取",
            "action": "人工查看原文件或提供可解析版本",
            "location": item.get("path"),
        })
    manual_review = []
    for row in rows:
        if row["manual_review"] or row["status"] in ("needs_recheck", "not_checked"):
            manual_review.append("%s（%s）：%s" % (
                row["label_zh"], row["status_zh"], row["reason"]))
    manual_review.extend([
        "本工具与报告不替代机构审查、伦理委员会、法务或目标期刊编辑的正式意见。",
        "声明存在性来自输入文本的关键词/段落规则；未命中不等于不存在，需人工通读确认。",
        "不联网、不查重、不核验批件/文献/DOI/期刊指南真伪；外部材料缺失时只报'待复核/未检查'。",
        "PDF/扫描件无文字层或图片内容需人工查看；未读取区域不作为已检查区域。",
    ])
    summary = ("自动合规矩阵：%d 项通过、%d 项问题、%d 项待复核、%d 项未检查、"
               "%d 项不适用；未报告/未检查项不视为合规。" % (
                   counts.get("pass", 0), counts.get("issue", 0),
                   counts.get("needs_recheck", 0), counts.get("not_checked", 0),
                   counts.get("not_applicable", 0)))
    if status == "input_error":
        summary = "输入没有可读取材料；未生成任何合规结论，只登记输入缺口。"
    payload = {
        "case_id": case_id,
        "tool": TOOL,
        "tool_version": VERSION,
        "generated_at": now_iso(),
        "status": status,
        "status_zh": STATUS_ZH.get(status, status),
        "summary": summary,
        "counts": counts,
        "matrix": rows,
        "findings": collector.findings,
        "uncertainties": uncertainties,
        "evidence_map": evidence_map,
        "manual_review_items": manual_review,
        "guideline_source": guideline_source(docs, rows),
        "inputs": [{"path": doc["path"], "kind": doc["kind"],
                    "chars": len(doc["text"]), "blocks": len(doc["blocks"]),
                    "warnings": doc["warnings"]} for doc in docs],
        "input_warnings": warnings,
        "limitations": [
            "规则命中是启发式，不是机构审查结论。",
            "未提供目标期刊指南、批件、同意书、许可或查重语料时，不做对应的合规/违规判定。",
            "不编造审批号、数据、DOI、许可或期刊条款；缺失一律写入 gaps/uncertainties。",
        ],
    }
    return payload


def work_payload(payload):
    rows = payload.get("matrix") or []
    checklist = []
    checks = []
    for row in rows:
        primary = (row.get("evidence") or [None])[0]
        checklist.append({
            "facet": row["facet"],
            "check_id": row["check_id"],
            "label_zh": row["label_zh"],
            "rule_id": row["rule_id"],
            "rule_version": row["rule_version"],
            "status": check_status(row),
            "matrix_status": row["status"],
            "type": "compliance",
            "detail": row["reason"],
            "gaps": row["gaps"],
            "actions": row["actions"],
            "fix": (row["actions"] or [""])[0],
            "evidence": primary,
        })
        checks.append({
            "item": row["label_zh"],
            "check_id": row["check_id"],
            "status": check_status(row),
            "matrix_status": row["status"],
            "detail": row["reason"],
        })
    not_checked = ["%s: %s" % (row["label_zh"], row["reason"])
                   for row in rows
                   if row["status"] in ("needs_recheck", "not_checked")]
    return {
        "case_id": payload.get("case_id"),
        "mode": "compliance-review",
        "summary": payload.get("summary", ""),
        "verdict": payload.get("status"),
        "review_status": payload.get("status")
        if payload.get("status") in ("pass", "issues", "needs_recheck", "not_checked")
        else "not_checked",
        "guideline_source": payload.get("guideline_source") or {},
        "compliance_checklist": checklist,
        "compliance_matrix": rows,
        "findings": payload.get("findings") or [],
        "checks": checks,
        "evidence": payload.get("evidence_map") or [],
        "uncertainties": payload.get("uncertainties") or [],
        "human_review_items": payload.get("manual_review_items") or [],
        "not_checked": not_checked,
        "artifacts": [],
    }


def merge_work(work_path, draft):
    existing = load_json(work_path, {})
    if not isinstance(existing, dict) or not existing:
        existing = {}
    merged = []
    for key in ("case_id", "mode", "summary", "verdict", "review_status",
                "guideline_source", "compliance_checklist", "compliance_matrix",
                "findings", "checks", "evidence", "uncertainties",
                "human_review_items", "not_checked"):
        value = draft.get(key)
        if not existing.get(key) and value:
            existing[key] = value
            merged.append(key)
        elif key == "evidence" and isinstance(value, list):
            current = existing.get(key)
            if not isinstance(current, list):
                current = []
            seen = {(item.get("path"), item.get("locator"), item.get("quote"))
                    for item in current if isinstance(item, dict)}
            for item in value:
                key_item = (item.get("path"), item.get("locator"), item.get("quote"))
                if key_item not in seen:
                    current.append(item)
                    seen.add(key_item)
            existing[key] = current
    write_json(work_path, existing)
    return merged


# ---------------------------------------------------------------------------
# Markdown draft
# ---------------------------------------------------------------------------
def render_markdown(payload):
    lines = [
        "# 合规检查矩阵（自动抽取草案）",
        "",
        "> 本文件由 `scripts/compliance_check.py` 基于 `/input` 自动生成，是证据索引",
        "> 与缺口清单，不是最终审查报告，也不替代机构审查/伦理委员会/法务意见。",
        "> 所有示例与测试材料均为 synthetic；正式结论必须由模型复核后写入",
        "> `output/review-report.md`。",
        "",
        "## 状态与口径",
        "",
        "| 状态 | 含义 |",
        "|---|---|",
        "| pass | 已提供材料中有声明且未发现缺口；不是认证 |",
        "| issue | 应报告而未报告，或声明与材料直接矛盾 |",
        "| needs_recheck | 有声明但不完整/需外部文件核对 |",
        "| not_checked | 输入不足，无法判断；不等于合规 |",
        "| not_applicable | 材料明确说明不适用 |",
        "",
        "**硬规则：未报告 ≠ 合规；未检查 ≠ 通过。**",
        "",
        "## 输入",
        "",
        "| 文件 | 类型 | 字符数 | 段落数 | 警告 |",
        "|---|---|---:|---:|---|",
    ]
    for item in payload.get("inputs") or []:
        lines.append("| %s | %s | %d | %d | %s |" % (
            md_cell(item.get("path")), md_cell(item.get("kind")),
            item.get("chars", 0), item.get("blocks", 0),
            md_cell("; ".join(item.get("warnings") or []))))
    if not payload.get("inputs"):
        lines.append("| （无可读材料） | - | 0 | 0 | 见 uncertainties |")
    lines += [
        "",
        "## 合规矩阵",
        "",
        "| 检查项 | 状态 | 依据位置 | 缺口 | 整改建议 | 人工复核 |",
        "|---|---|---|---|---|---|",
    ]
    for row in payload.get("matrix") or []:
        locations = "; ".join(
            "%s %s (L%s-L%s)" % (item.get("path"), item.get("locator"),
                                 item.get("line_start"), item.get("line_end"))
            for item in row.get("evidence") or []) or "—"
        lines.append("| %s (%s) | %s | %s | %s | %s | %s |" % (
            md_cell(row.get("label_zh")), md_cell(row.get("check_id")),
            md_cell(row.get("status")),
            md_cell(locations),
            md_cell("；".join(row.get("gaps") or []) or "—"),
            md_cell("；".join(row.get("actions") or []) or "—"),
            "是" if row.get("manual_review") else "否"))
    lines += ["", "## Findings", ""]
    findings = payload.get("findings") or []
    if not findings:
        lines.append("（未生成 findings；这不表示材料合规，见 uncertainties。）")
    for finding in findings:
        lines += [
            "### %s · %s · %s" % (finding.get("id"), finding.get("severity"),
                                   finding.get("issue_type")),
            "",
            "- 规则：`%s` (%s)" % (finding.get("rule_id"), finding.get("rule_version")),
            "- 位置：`%s` `%s` (L%s-L%s)" % (
                finding.get("locator", {}).get("path"),
                finding.get("locator", {}).get("locator"),
                finding.get("locator", {}).get("line_start"),
                finding.get("locator", {}).get("line_end")),
            "- 原文：> %s" % finding.get("original_quote", ""),
            "- 影响：%s" % finding.get("impact", ""),
            "- 整改：%s" % finding.get("fix", ""),
            "- 误报检查：%s" % finding.get("false_positive_check", ""),
        ]
        for item in (finding.get("evidence") or [])[1:]:
            lines.append("- 补充证据：`%s` `%s` > %s" % (
                item.get("path"), item.get("locator"), item.get("quote", "")))
        lines.append("")
    lines += ["## Uncertainties / 人工复核", ""]
    uncertainties = payload.get("uncertainties") or []
    if uncertainties:
        lines += ["| 项目 | 原因 | 影响 | 处理 |", "|---|---|---|---|"]
        for item in uncertainties:
            lines.append("| %s | %s | %s | %s |" % (
                md_cell(item.get("item")), md_cell(item.get("reason")),
                md_cell(item.get("impact")), md_cell(item.get("action"))))
    else:
        lines.append("（无）")
    lines += ["", "## 人工复核边界", ""]
    for item in payload.get("manual_review_items") or []:
        lines.append("- %s" % item)
    lines += ["", "## 复跑", "",
              "```bash",
              "python3 \"$SKILL_DIR/scripts/compliance_check.py\" \\",
              "  --input /input --work /app/work --output /app/output --merge-work",
              "```",
              ""]
    return "\n".join(lines)


def error_payload(case_id, exc):
    return {
        "case_id": case_id,
        "tool": TOOL,
        "tool_version": VERSION,
        "generated_at": now_iso(),
        "status": "input_error",
        "status_zh": STATUS_ZH["input_error"],
        "summary": "脚本内部错误；未生成任何合规结论。",
        "counts": {},
        "matrix": [],
        "findings": [],
        "uncertainties": [{
            "item": "tool_error", "facet": "tool",
            "reason": "%s: %s" % (type(exc).__name__, exc),
            "impact": "本次自动抽取不可用",
            "action": "检查输入路径与脚本日志；人工完成审查",
        }],
        "evidence_map": [],
        "manual_review_items": ["脚本内部错误；必须人工复核。"],
        "guideline_source": {
            "name": "未提供目标期刊/会议作者指南",
            "version": "unavailable", "date": "",
            "used_for_verdicts": False,
        },
        "inputs": [],
        "input_warnings": [{"path": "", "reason": str(exc)}],
        "limitations": ["脚本内部错误；未做任何合规判定。"],
        "error": {"type": type(exc).__name__, "message": str(exc),
                  "traceback": traceback.format_exc()[-3000:]},
    }


def write_outputs(payload, output_dir, work_dir):
    output_dir, work_dir = Path(output_dir), Path(work_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "compliance-matrix.json", payload)
    write_json(output_dir / "compliance-findings.json", {
        "case_id": payload.get("case_id"),
        "tool": TOOL,
        "status": payload.get("status"),
        "findings": payload.get("findings") or [],
        "uncertainties": payload.get("uncertainties") or [],
    })
    write_json(output_dir / "compliance-evidence-map.json", {
        "case_id": payload.get("case_id"),
        "tool": TOOL,
        "evidence_count": len(payload.get("evidence_map") or []),
        "evidence_map": payload.get("evidence_map") or [],
    })
    write_text(output_dir / "compliance-check.md", render_markdown(payload))
    draft = work_payload(payload)
    write_json(work_dir / "compliance-draft.json", draft)
    return draft


def run(input_dir, work_dir, output_dir, merge=False):
    input_dir, work_dir, output_dir = Path(input_dir), Path(work_dir), Path(output_dir)
    case = load_json(input_dir / "case.json", {})
    case_id = str(case.get("case_id") or case.get("id") or "CASE")
    docs, warnings = load_documents(input_dir, work_dir)
    if not docs:
        payload = {
            "case_id": case_id, "tool": TOOL, "tool_version": VERSION,
            "generated_at": now_iso(), "status": "input_error",
            "status_zh": STATUS_ZH["input_error"],
            "summary": "输入没有可读取材料；未生成任何合规结论，只登记输入缺口。",
            "counts": {}, "matrix": [], "findings": [], "uncertainties": [{
                "item": "materials", "facet": "input",
                "reason": "no readable material in /input",
                "impact": "无法开始合规检查",
                "action": "提供稿件、声明、指南或可解析的 PDF/DOCX",
            }], "evidence_map": [], "manual_review_items": [
                "没有可读材料；必须人工确认输入与格式。"],
            "guideline_source": {
                "name": "未提供目标期刊/会议作者指南", "version": "unavailable",
                "date": "", "used_for_verdicts": False},
            "inputs": [], "input_warnings": warnings,
            "limitations": ["空输入；未做任何合规判定。"],
        }
        draft = write_outputs(payload, output_dir, work_dir)
        if merge:
            merge_work(work_dir / "review-findings.json", draft)
        return payload
    analyzer = Analyzer(docs)
    rows, collector = analyzer.run()
    payload = build_payload(case_id, docs, warnings, rows, collector)
    draft = write_outputs(payload, output_dir, work_dir)
    if merge:
        merged = merge_work(work_dir / "review-findings.json", draft)
        payload["merged_work_fields"] = merged
        write_json(output_dir / "compliance-matrix.json", payload)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="academic compliance matrix extractor")
    parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--merge-work", action="store_true",
                        help="fill empty fields of work/review-findings.json")
    parser.add_argument("--strict", action="store_true",
                        help="return 1 unless the top status is pass")
    args = parser.parse_args(argv)
    try:
        payload = run(args.input, args.work, args.output, merge=args.merge_work)
        code = 0
    except Exception as exc:  # noqa: BLE001
        case = load_json(Path(args.input) / "case.json", {})
        payload = error_payload(str(case.get("case_id") or "CASE"), exc)
        try:
            write_outputs(payload, args.output, args.work)
        except Exception:  # noqa: BLE001
            pass
        code = 0
    summary = {
        "tool": TOOL,
        "version": VERSION,
        "status": payload.get("status"),
        "counts": payload.get("counts"),
        "findings": len(payload.get("findings") or []),
        "uncertainties": len(payload.get("uncertainties") or []),
        "outputs": [
            str(Path(args.output) / "compliance-matrix.json"),
            str(Path(args.output) / "compliance-findings.json"),
            str(Path(args.output) / "compliance-evidence-map.json"),
            str(Path(args.output) / "compliance-check.md"),
            str(Path(args.work) / "compliance-draft.json"),
        ],
        "merged_work_fields": payload.get("merged_work_fields") or [],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.strict and payload.get("status") != "pass":
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
