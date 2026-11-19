#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source-preserving revision workflow CLI for sectioned long documents.

The organizer workorder supplies paragraph ids (for example ``C01-P002``) and
asks for a real local revision workflow:

  * an actual import / task receipt,
  * proposed changes that are individually accepted or rejected,
  * a real version comparison plus rollback and reapplication,
  * a controlled invalid-operation exception with unchanged state,
  * an editable manuscript, original/revised records, an evidence map and a
    real HTML export preview.

This tool implements that loop with the Python standard library.  It never
rewrites the supplied manuscript: originals are copied to ``<work>/baseline/``
and the export is rebuilt from those bytes.  Editorial annotations are inserted
as clearly marked blocks after the target paragraph; all other blocks stay
byte-identical.

Typical use::

    python3 revision_flow.py init --input /input --work /app/work --json
    # read work/edit-plan.json and work/annotations.template.json
    # write work/annotations.json with verbatim source quotations
    python3 revision_flow.py propose --work /app/work --spec /app/work/annotations.json --json
    python3 revision_flow.py accept E01 --work /app/work --json
    python3 revision_flow.py reject E03 --work /app/work --reason "改写原句，超出工单" --json
    python3 revision_flow.py compare --work /app/work --from v1 --to v3 --json
    python3 revision_flow.py rollback --work /app/work --json
    python3 revision_flow.py reapply --work /app/work --json
    python3 revision_flow.py invalidate --work /app/work --operation accept-unknown --json
    python3 revision_flow.py export --work /app/work --out /app/output --json
    python3 revision_flow.py verify --work /app/work --out /app/output --json

Exit codes: 0 ok, 1 verification failed / guard failed, 2 missing or unreadable
input, 3 blocked operation (unknown target, bad quote, illegal state change).
The ``invalidate`` command exits 0 when the deliberate fault was caught and
``state.json`` stayed byte-identical, which is the required behaviour.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import html
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.5.0"
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_INPUT = 2
EXIT_BLOCKED = 3

CJK = re.compile(r"[\u3400-\u9fff]")
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
CITE = re.compile(r"\[([0-9]+(?:\s*[,，\-–]\s*[0-9]+)*)\]")
NUMBER = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:[-+]?\d+(?:[.,]\d+)?"
    r"(?:\s?[×xX]\s?10\s?[-−–]?\s?\d+)?"
    r"(?:\s?%|\s?(?:mm|cm|µm|μm|nm|km|kg|mg|g|m|mL|L|h|min|s|Hz|kHz|MHz|"
    r"MPa|GPa|Pa|°C|K|wt%|vol%)(?![A-Za-z]))?)"
)
ANNOTATION_TYPES = {"scientific_condition", "limitation", "clarification"}
TYPE_ALIASES = {
    "condition": "scientific_condition",
    "scientific-condition": "scientific_condition",
    "scientific_condition": "scientific_condition",
    "limitation": "limitation",
    "limitations": "limitation",
    "traceable_clarification": "clarification",
    "traceable-clarification": "clarification",
    "clarification": "clarification",
}
EXCLUDE_NAMES = {
    "task.md", "case.json", "input.json", "organizer-edit-workorder.json",
    "output-contract.md", "example-input.json", "public-manifest.json",
    "manifest.json", "result.json", "readme.md", "skill.md", "usage.md",
}
TEXT_EXT = {".md", ".markdown", ".txt"}
DOCX_EXT = {".docx"}
PDF_EXT = {".pdf"}
ANNOTATION_OPEN = "<!-- editorial-annotation"
ANNOTATION_CLOSE = "<!-- /editorial-annotation"
MIN_ANNOTATION_CHARS = 30
MIN_QUOTE_CHARS = 6


class RevisionError(Exception):
    """A controlled, user-actionable failure."""

    exit_code = EXIT_BLOCKED


def setup_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(text: str) -> str:
    return sha_bytes(text.encode("utf-8"))


def sha_json(value) -> str:
    return sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")))


def read_text(path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def write_text(path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(normalize_text(text))
    return path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def dump_json(path, value) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(str(tmp), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    tmp.replace(path)
    return path


def word_count(text: str) -> int:
    return len(CJK.findall(text or "")) + len(WORD.findall(text or ""))


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r\n", "\n")).strip()


def normalize_compare(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def excerpt(text: str, limit: int = 140) -> str:
    value = normalize_ws(text)
    return value if len(value) <= limit else value[:limit - 1] + "…"


def first(*values):
    for value in values:
        if value is not None and value != "":
            return value
    return None


def safe_name(value: str) -> str:
    value = re.sub(r"[\/:*?\"<>|\s]+", "-", str(value or "")).strip("-")
    return value or "chapter"


def rel_to(path, base) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(base).resolve()))
    except (ValueError, OSError):
        return str(path)


def natural_key(value):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", str(value))]


def deep_get(data, *keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


# ---------------------------------------------------------------------------
# paragraph / block parsing
# ---------------------------------------------------------------------------
ID_TOKEN = r"(?:[A-Za-z][A-Za-z0-9_]{0,15}-)?P\d{2,5}[A-Za-z]?"
ID_LINE = re.compile(
    r"^\s*(?:\[\s*|\(\s*|<!--\s*)?(" + ID_TOKEN +
    r")(?:\s*\]|\s*\)|\s*-->)?\s*(?:[:：\-–—]\s*)?(?=\S|$)")
STANDALONE_ID = re.compile(
    r"^\s*(?:\[\s*|\(\s*|<!--\s*)?(" + ID_TOKEN +
    r")(?:\s*\]|\s*\)|\s*-->)?\s*$")


def split_groups(text: str):
    """Split text into groups of non-empty lines, keeping 1-based line ranges."""
    lines = normalize_text(text).split("\n")
    groups = []
    current = []
    start = 0
    for index, line in enumerate(lines):
        if HEADING.match(line):
            if current:
                groups.append((start, index - 1, current))
                current = []
            groups.append((index, index, [line]))
            start = index + 1
            continue
        if line.strip() == "":
            if current:
                groups.append((start, index - 1, current))
                current = []
            start = index + 1
            continue
        if not current:
            start = index
        current.append(line)
    if current:
        groups.append((start, len(lines) - 1, current))
    return groups


def group_raw(group) -> str:
    return "\n".join(group[2])


def is_standalone_id_group(group) -> bool:
    raw = group_raw(group).strip()
    return bool(STANDALONE_ID.match(raw)) and "\n" not in raw


def merge_id_groups(groups):
    out = []
    pending = None
    for group in groups:
        if is_standalone_id_group(group):
            if pending is None:
                pending = group
            else:
                pending = (pending[0], group[1],
                           pending[2] + [""] * max(0, group[0] - pending[1] - 1) + group[2])
            continue
        if pending is not None:
            between = [""] * max(0, group[0] - pending[1] - 1)
            group = (pending[0], group[1], pending[2] + between + group[2])
            pending = None
        out.append(group)
    if pending is not None:
        out.append(pending)
    return out


def extract_block_id(raw: str):
    for line in raw.split("\n"):
        if not line.strip():
            continue
        match = ID_LINE.match(line)
        if match:
            return match.group(1)
        comment = re.match(r"^\s*<!--\s*(" + ID_TOKEN + r")\s*-->\s*$", line)
        if comment:
            return comment.group(1)
        break
    return None


def classify_block(raw: str) -> str:
    content = [line for line in raw.split("\n")
               if line.strip() and not STANDALONE_ID.match(line.strip())]
    if not content:
        return "paragraph"
    if len(content) == 1 and HEADING.match(content[0]):
        return "heading"
    has_table_rows = any(line.lstrip().startswith("|") for line in content)
    has_separator = any(re.match(r"^\s*\|?\s*:?-{3,}", line) for line in content)
    if has_table_rows and (has_separator or len(content) >= 2):
        return "table"
    joined = "\n".join(content)
    if "$$" in joined or r"\[" in joined or r"\begin{" in joined:
        return "formula"
    return "paragraph"


def choose_prefix(chapter_id: str, order: int, target_prefixes) -> str:
    cid = str(chapter_id or "")
    order_prefix = "C%02d" % order
    lower_targets = {str(p).lower(): str(p) for p in target_prefixes}
    if cid and cid.lower() in lower_targets:
        return lower_targets[cid.lower()]
    if order_prefix.lower() in lower_targets:
        return lower_targets[order_prefix.lower()]
    for candidate in (cid, "ch%02d" % order, order_prefix):
        if candidate and candidate.lower() in lower_targets:
            return lower_targets[candidate.lower()]
    return cid or order_prefix


def parse_chapter(text: str, chapter_id: str, order: int, target_prefixes):
    prefix = choose_prefix(chapter_id, order, target_prefixes)
    groups = merge_id_groups(split_groups(text))
    used = set()
    p_index = 0
    h_index = 0
    blocks = []
    for start, end, group in groups:
        raw = "\n".join(group).rstrip("\n")
        block_id = extract_block_id(raw)
        kind = classify_block(raw)
        if not block_id:
            if kind == "heading":
                h_index += 1
                block_id = "%s-H%03d" % (prefix, h_index)
            else:
                p_index += 1
                block_id = "%s-P%03d" % (prefix, p_index)
        if block_id in used:
            suffix = 2
            base = block_id
            while "%s-DUP%d" % (base, suffix) in used:
                suffix += 1
            block_id = "%s-DUP%d" % (base, suffix)
        used.add(block_id)
        blocks.append({
            "block_id": block_id,
            "kind": kind,
            "line_start": start + 1,
            "line_end": end + 1,
            "sha256": sha_text(raw),
            "excerpt": excerpt(raw),
            "raw": raw,
        })
    return blocks


def chapter_text_from_docx(path: Path) -> str:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(str(path)) as archive:
        xml = archive.read("word/document.xml")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    blocks = []
    for para in root.iter(ns + "p"):
        text = "".join(node.text or "" for node in para.iter(ns + "t"))
        if not text.strip():
            continue
        style_node = para.find(".//" + ns + "pStyle")
        style = style_node.get(ns + "val", "") if style_node is not None else ""
        match = re.search(r"heading\s*([1-6])", style, re.I)
        if match:
            blocks.append("#" * int(match.group(1)) + " " + text.strip())
        elif style.lower() in ("title", "subtitle"):
            blocks.append("# " + text.strip())
        else:
            blocks.append(text.strip())
    return "\n\n".join(blocks) + ("\n" if blocks else "")


# ---------------------------------------------------------------------------
# input discovery
# ---------------------------------------------------------------------------
def find_case(input_root: Path):
    for name in ("case.json", "input.json"):
        candidate = Path(input_root) / name
        if candidate.is_file():
            return candidate
    return None


def load_case(input_root: Path):
    warnings = []
    path = find_case(input_root)
    case = {}
    if path is None:
        warnings.append("case.json not found under %s" % input_root)
    else:
        try:
            case = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append("cannot parse %s: %s" % (path, exc))
            case = {}
    if not isinstance(case, dict):
        case = {"content": case}
    return case, path, warnings


def find_workorder_in(value, depth=0):
    if depth > 5:
        return None
    if isinstance(value, dict):
        for key in ("organizer_edit_workorder", "organizer-edit-workorder"):
            if isinstance(value.get(key), dict):
                return value[key]
        for child in value.values():
            found = find_workorder_in(child, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_workorder_in(child, depth + 1)
            if found is not None:
                return found
    return None


def load_workorder(case: dict, input_root: Path):
    workorder = first(case.get("organizer_edit_workorder"),
                      case.get("organizer-edit-workorder"))
    if not isinstance(workorder, dict):
        workorder = find_workorder_in(case)
    source = "case.json"
    if not isinstance(workorder, dict):
        for name in ("organizer-edit-workorder.json", "edit-workorder.json"):
            candidate = Path(input_root) / name
            if candidate.is_file():
                try:
                    value = load_json(candidate)
                    if isinstance(value, dict):
                        workorder = value
                        source = str(candidate)
                        break
                except (OSError, json.JSONDecodeError):
                    pass
    if not isinstance(workorder, dict):
        workorder = {}
        source = None
    return workorder, source


def target_ids_from_workorder(workorder: dict):
    ids = []
    raw = workorder.get("target_ids") or workorder.get("targets") or []
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                ids.append(item.strip())
            elif isinstance(item, dict):
                value = first(item.get("target_id"), item.get("paragraph_id"),
                              item.get("id"))
                if value:
                    ids.append(str(value).strip())
    edits = workorder.get("edits")
    if isinstance(edits, list):
        for item in edits:
            if isinstance(item, dict):
                value = first(item.get("target_id"), item.get("paragraph_id"))
                if value and str(value).strip() not in ids:
                    ids.append(str(value).strip())
    return ids


def target_prefixes(target_ids):
    prefixes = set()
    for target in target_ids:
        match = re.match(r"^(.+?)-P\d", str(target), re.I)
        if match:
            prefixes.add(match.group(1))
    return prefixes


def resolve_source(value: str, input_root: Path, case_dir: Path):
    candidate = Path(value)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()
    bases = [Path(input_root), Path(input_root) / "chapters",
             Path(input_root) / "sections", case_dir]
    for base in bases:
        for rel in (value, Path(value).name):
            path = base / rel
            if path.is_file():
                return path.resolve()
    return None


def paragraph_text(item):
    if isinstance(item, str):
        return item, None
    if isinstance(item, dict):
        pid = first(item.get("id"), item.get("paragraph_id"), item.get("block_id"),
                    item.get("pid"))
        text = first(item.get("text"), item.get("content"), item.get("body"),
                     item.get("markdown"))
        if text is None:
            return None, None
        text = str(text)
        if pid:
            return "[%s] %s" % (pid, text), str(pid)
        return text, None
    return None, None


def paragraphs_to_entries(items):
    groups, order = {}, []
    for item in items:
        text, pid = paragraph_text(item)
        if text is None:
            continue
        prefix = str(pid).split("-P")[0] if pid and "-P" in str(pid) else "C01"
        if prefix not in groups:
            groups[prefix] = []
            order.append(prefix)
        groups[prefix].append(text)
    return [{"chapter_id": prefix, "order": index + 1,
             "content": "\n\n".join(groups[prefix])}
            for index, prefix in enumerate(order)]


def normalize_chapter_entries(case: dict, input_root: Path, case_dir: Path):
    entries = []
    raw = first(case.get("chapters"), deep_get(case, "manuscript", "chapters"),
                deep_get(case, "document", "chapters"), case.get("sections"),
                deep_get(case, "source", "chapters"))
    if raw is None and isinstance(case.get("manuscript"), (list, dict)):
        raw = case.get("manuscript")
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, str):
                entries.append({"chapter_id": key, "path": value})
            elif isinstance(value, dict):
                item = dict(value)
                item.setdefault("chapter_id", key)
                entries.append(item)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                entries.append({"path": item})
            elif isinstance(item, dict):
                entries.append(dict(item))
    elif isinstance(raw, str):
        entries.append({"content": raw})
    if not entries:
        manuscript = first(case.get("manuscript"), case.get("full_text"),
                           case.get("text"), deep_get(case, "source", "text"))
        if isinstance(manuscript, str) and manuscript.strip():
            entries.append({"chapter_id": "C01", "title": "Supplied manuscript",
                            "content": manuscript})
    if not entries:
        paragraphs = first(case.get("paragraphs"), case.get("blocks"),
                           deep_get(case, "source", "paragraphs"),
                           deep_get(case, "source", "blocks"))
        if isinstance(paragraphs, list) and paragraphs:
            entries.extend(paragraphs_to_entries(paragraphs))
    normalized = []
    for index, item in enumerate(entries):
        path_value = first(item.get("path"), item.get("file"), item.get("filename"),
                           item.get("src"), item.get("source_path"))
        source = resolve_source(str(path_value), input_root, case_dir) if path_value else None
        content = first(item.get("content"), item.get("text"), item.get("body"),
                        item.get("markdown"))
        if content is None:
            sub = item.get("paragraphs") or item.get("blocks")
            if isinstance(sub, list):
                content = "\n\n".join(
                    text for text, _pid in (paragraph_text(value) for value in sub)
                    if text)
        elif isinstance(content, list):
            content = "\n\n".join(
                text for text, _pid in (paragraph_text(value) for value in content)
                if text)
        if source is None and isinstance(path_value, str) and not content:
            if "\n" in path_value or len(path_value) > 180:
                content = path_value
        if source is None and content is None:
            continue
        cid = first(item.get("chapter_id"), item.get("id"), item.get("chapter"),
                    item.get("name"))
        if not cid and source is not None:
            cid = source.stem
        if not cid:
            cid = "C%02d" % (index + 1)
        title = first(item.get("title"), item.get("heading"), item.get("name"))
        try:
            order = int(item.get("order"))
        except (TypeError, ValueError):
            order = index + 1
        normalized.append({
            "chapter_id": safe_name(str(cid)),
            "title": str(title) if title else None,
            "order": order,
            "source_path": source,
            "content": content,
            "notes": list(item.get("notes") or []),
        })
    if not normalized:
        search_dirs = [Path(input_root) / "chapters", Path(input_root) / "sections",
                       Path(input_root) / "source", Path(input_root) / "manuscript",
                       Path(input_root)]
        seen = set()
        for directory in search_dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*"), key=lambda p: natural_key(str(p))):
                if not path.is_file() or path.suffix.lower() not in (TEXT_EXT | DOCX_EXT):
                    continue
                if path.name.lower() in EXCLUDE_NAMES or path.name.startswith("."):
                    continue
                resolved = str(path.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                normalized.append({
                    "chapter_id": safe_name(path.stem),
                    "title": None,
                    "order": len(normalized) + 1,
                    "source_path": path.resolve(),
                    "content": None,
                    "notes": [],
                })
    used = set()
    for item in normalized:
        base = item["chapter_id"]
        cid = base
        suffix = 2
        while cid in used:
            cid = "%s-%d" % (base, suffix)
            suffix += 1
        if cid != base:
            item["notes"].append("chapter_id %s renamed to %s" % (base, cid))
        item["chapter_id"] = cid
        used.add(cid)
    normalized.sort(key=lambda item: (item["order"], natural_key(item["chapter_id"])))
    return normalized


# ---------------------------------------------------------------------------
# workspace, state and receipts
# ---------------------------------------------------------------------------
def workspace_paths(work):
    work = Path(work)
    return {
        "work": work,
        "baseline": work / "baseline",
        "diffs": work / "diffs",
        "proposals": work / "proposals",
        "versions": work / "versions",
        "state": work / "state.json",
        "receipts": work / "receipts.jsonl",
        "import_receipt": work / "import-receipt.json",
        "plan": work / "edit-plan.json",
        "template": work / "annotations.template.json",
        "invalid_receipt": work / "invalid-operation-receipt.json",
        "input_error": work / "input-error.json",
    }


def ensure_workspace(work) -> dict:
    paths = workspace_paths(work)
    for key in ("baseline", "diffs", "proposals", "versions"):
        paths[key].mkdir(parents=True, exist_ok=True)
    paths["work"].mkdir(parents=True, exist_ok=True)
    return paths


def load_state(work):
    path = Path(work) / "state.json"
    if not path.is_file():
        raise RevisionError("workspace is not initialized: %s" % path)
    try:
        state = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise RevisionError("cannot read state.json: %s" % exc) from exc
    if not isinstance(state, dict):
        raise RevisionError("state.json must contain a JSON object")
    return state


def save_state(work, state) -> Path:
    return dump_json(Path(work) / "state.json", state)


def file_sha(path) -> str:
    return sha_bytes(Path(path).read_bytes()) if Path(path).is_file() else ""


def append_receipt(work, operation, ok, details=None, exit_code=EXIT_OK):
    paths = workspace_paths(work)
    path = paths["receipts"]
    path.parent.mkdir(parents=True, exist_ok=True)
    seq = 1
    if path.is_file():
        try:
            seq = sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                      if line.strip()) + 1
        except OSError:
            seq = 1
    receipt = {
        "seq": seq,
        "operation": operation,
        "at": now_iso(),
        "ok": bool(ok),
        "exit_code": int(exit_code),
        "details": details or {},
        "state_sha256": file_sha(paths["state"]),
    }
    with open(str(path), "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False) + "\n")
    return receipt


def load_receipts(work):
    path = Path(work) / "receipts.jsonl"
    receipts = []
    if not path.is_file():
        return receipts
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                receipts.append(value)
        except json.JSONDecodeError:
            continue
    return receipts


def find_version(state, version_id):
    for item in state.get("versions") or []:
        if str(item.get("version")) == str(version_id):
            return item
    raise RevisionError("unknown version: %s" % version_id)


def current_version(state):
    return find_version(state, state.get("current_version"))


def effective_lists(state):
    version = current_version(state)
    accepted = list(version.get("accepted") or [])
    rejected = list(version.get("rejected") or [])
    return accepted, rejected


def add_version(state, label, accepted, rejected, reason=""):
    version_id = "v%d" % len(state.get("versions") or [])
    snapshot = {
        "version": version_id,
        "created_at": now_iso(),
        "label": label,
        "reason": reason,
        "accepted": sorted(set(accepted)),
        "rejected": sorted(set(rejected)),
        "changes_sha256": sha_json({
            "accepted": sorted(set(accepted)),
            "rejected": sorted(set(rejected)),
        }),
    }
    state.setdefault("versions", []).append(snapshot)
    state["current_version"] = version_id
    return snapshot


def apply_version_statuses(state):
    accepted, rejected = effective_lists(state)
    accepted_set, rejected_set = set(accepted), set(rejected)
    for change_id, change in (state.get("changes") or {}).items():
        if change_id in accepted_set:
            change["status"] = "accepted"
        elif change_id in rejected_set:
            change["status"] = "rejected"
        else:
            change["status"] = "proposed" if change.get("annotation") else "unknown"
            if change["status"] == "proposed":
                change["decided_at"] = None


def options_for(status="proposed"):
    return {"status": status}


def register_change(state, change, write_diff=True, work=None):
    change_id = change["change_id"]
    if change_id in (state.get("changes") or {}):
        existing = state["changes"][change_id]
        if existing.get("status") in ("accepted", "rejected"):
            raise RevisionError(
                "change %s is already %s; rollback or create a new change id"
                % (change_id, existing.get("status")))
    state.setdefault("changes", {})[change_id] = change
    apply_version_statuses(state)
    return change


# ---------------------------------------------------------------------------
# composition and annotation blocks
# ---------------------------------------------------------------------------
def accepted_changes(state, accepted_ids=None):
    if accepted_ids is None:
        accepted_ids, _ = effective_lists(state)
    accepted = set(accepted_ids)
    return [state["changes"][cid] for cid in sorted(accepted)
            if cid in (state.get("changes") or {})]


def annotation_block(change) -> str:
    annotation = str(change.get("annotation") or "")
    annotation = annotation.replace("<!--", "&lt;!--").replace("-->", "--&gt;")
    quote = str(change.get("basis_quote") or "").replace("<!--", "&lt;!--")
    claim = str(change.get("claim") or "").replace("<!--", "&lt;!--")
    uncertainty = str(change.get("uncertainty") or "").replace("<!--", "&lt;!--")
    if not uncertainty:
        uncertainty = "No additional uncertainty beyond the supplied source text / 以原文为准。"
    kind = change.get("annotation_type") or "clarification"
    lines = [
        "%s id=%s target=%s type=%s -->" % (
            ANNOTATION_OPEN, change["change_id"], change["target_id"], kind),
        "> **Editorial annotation %s (target %s; not original text) / 编辑批注（非原文）**"
        % (change["change_id"], change["target_id"]),
        "> ",
        "> %s" % annotation.replace("\n", "\n> "),
        "> ",
        "> Basis quote / 依据原文: “%s” (%s)" % (quote, change.get("locator") or change["target_id"]),
    ]
    if claim:
        lines.append("> Claim / 论断: %s" % claim.replace("\n", " "))
    lines.append("> Uncertainty / 不确定性: %s" % uncertainty.replace("\n", " "))
    lines.append("%s id=%s -->" % (ANNOTATION_CLOSE, change["change_id"]))
    return "\n".join(lines)


def compose_chapter_text(chapter, changes_for_chapter):
    """Insert annotation blocks after target blocks without touching originals."""
    baseline_path = Path(chapter.get("baseline_path") or "")
    if not baseline_path.is_file():
        raise RevisionError("missing baseline file for chapter %s: %s"
                            % (chapter.get("chapter_id"), baseline_path))
    text = read_text(baseline_path)
    lines = normalize_text(text).split("\n")
    # lines has a trailing empty item; insertion is 0-based after line_end-1.
    inserts = {}
    for change in changes_for_chapter:
        block = None
        wanted_block = change.get("block_id") or change.get("target_id")
        for candidate in chapter.get("blocks") or []:
            if candidate.get("block_id") == wanted_block:
                block = candidate
                break
        if block is None:
            continue
        index = min(len(lines), int(block.get("line_end") or 0))
        payload = annotation_block(change).split("\n") + [""]
        inserts.setdefault(index, []).extend(payload)
    out = []
    for index, line in enumerate(lines):
        if index in inserts:
            out.extend(inserts[index])
        out.append(line)
    if len(lines) in inserts:
        out.extend(inserts[len(lines)])
    return "\n".join(out)


def compose_text(state, accepted_ids=None):
    changes = accepted_changes(state, accepted_ids)
    chunks = []
    for chapter in state.get("chapters") or []:
        chapter_changes = [change for change in changes
                           if change.get("chapter_id") == chapter.get("chapter_id")]
        chunks.append(compose_chapter_text(chapter, chapter_changes))
    return "\n\n".join(chunk.rstrip("\n") for chunk in chunks) + "\n"


def strip_annotation_blocks(text: str) -> str:
    out = []
    skip = False
    for line in (text or "").split("\n"):
        if re.match(r"^\s*" + re.escape(ANNOTATION_OPEN) + r"\b", line):
            skip = True
            continue
        if skip:
            if re.match(r"^\s*" + re.escape(ANNOTATION_CLOSE) + r"\b", line):
                skip = False
            continue
        out.append(line)
    return "\n".join(out)


def annotation_ids_in(text: str):
    return re.findall(r"<!--\s*editorial-annotation\s+id=([A-Za-z0-9_-]+)", text or "")


# ---------------------------------------------------------------------------
# verification: preservation, citations, formulas, tables, headings
# ---------------------------------------------------------------------------
def citation_tokens(text: str):
    out = []
    for group in CITE.findall(text or ""):
        for part in re.split(r"[,，]", group):
            part = part.strip()
            if re.match(r"^\d+\s*[-–]\s*\d+$", part):
                bits = [x.strip() for x in re.split(r"[-–]", part)]
                if all(bit.isdigit() for bit in bits):
                    out.extend(str(n) for n in range(int(bits[0]), int(bits[1]) + 1))
            elif part.isdigit():
                out.append(part)
    return sorted(out, key=lambda x: int(x))


def blocks_by_kind(blocks, kind):
    return [block.get("raw", "") for block in blocks if block.get("kind") == kind]


def verify_state(state, out_dir=None, revised_text=None):
    checks = []
    summaries = {}

    def add(check_id, ok, detail, severity="error"):
        checks.append({"id": check_id, "ok": bool(ok), "detail": detail,
                       "severity": severity})

    chapters = state.get("chapters") or []
    changes = state.get("changes") or {}
    accepted, rejected = effective_lists(state)
    accepted_set, rejected_set = set(accepted), set(rejected)
    targets = state.get("targets") or []
    found_targets = [t for t in targets if t.get("found")]
    target_ids = {t.get("target_id") for t in found_targets}

    baseline_intact = True
    for chapter in chapters:
        path = Path(chapter.get("baseline_path") or "")
        if not path.is_file() or file_sha(path) != chapter.get("baseline_sha256"):
            baseline_intact = False
    add("baseline_files_intact", baseline_intact,
        "%d/%d baseline files match their import hashes"
        % (sum(1 for c in chapters if Path(c.get("baseline_path") or "").is_file()
               and file_sha(Path(c.get("baseline_path"))) == c.get("baseline_sha256")),
           len(chapters)))

    all_text_preserved = True
    all_blocks_preserved = True
    unrequested_ok = True
    target_blocks_ok = True
    citations_before, citations_after = [], []
    formulas_before, formulas_after = [], []
    tables_before, tables_after = [], []
    headings_before, headings_after = [], []
    total_blocks = 0
    unrequested_count = 0
    preserved_chapters = 0
    block_mismatches = []
    changed_blocks = []

    for chapter in chapters:
        baseline_path = Path(chapter.get("baseline_path") or "")
        if not baseline_path.is_file():
            all_text_preserved = False
            all_blocks_preserved = False
            continue
        baseline_text = read_text(baseline_path)
        chapter_changes = [changes[cid] for cid in sorted(accepted_set)
                           if cid in changes and changes[cid].get("chapter_id") == chapter.get("chapter_id")]
        revised_chapter = compose_chapter_text(chapter, chapter_changes)
        stripped = strip_annotation_blocks(revised_chapter)
        text_ok = normalize_compare(stripped) == normalize_compare(baseline_text)
        if text_ok:
            preserved_chapters += 1
        else:
            all_text_preserved = False
        before_blocks = chapter.get("blocks") or []
        after_blocks = parse_chapter(stripped, chapter.get("chapter_id", ""),
                                     int(chapter.get("order") or 1),
                                     {str(t.get("target_id", "")).split("-P")[0]
                                      for t in found_targets})
        total_blocks += len(before_blocks)
        if len(before_blocks) != len(after_blocks):
            all_blocks_preserved = False
            block_mismatches.append("%s: %d -> %d blocks"
                                    % (chapter.get("chapter_id"), len(before_blocks),
                                       len(after_blocks)))
        else:
            for index, (before, after) in enumerate(zip(before_blocks, after_blocks)):
                same = (before.get("raw") == after.get("raw")
                        and before.get("kind") == after.get("kind"))
                if not same:
                    all_blocks_preserved = False
                    block_mismatches.append("%s block %d changed"
                                            % (chapter.get("chapter_id"), index + 1))
                    changed_blocks.append(before.get("block_id"))
                if before.get("block_id") not in target_ids:
                    unrequested_count += 1
                    if not same:
                        unrequested_ok = False
                else:
                    if not same:
                        target_blocks_ok = False
        citations_before.extend(citation_tokens("\n".join(
            block.get("raw", "") for block in before_blocks)))
        citations_after.extend(citation_tokens("\n".join(
            block.get("raw", "") for block in after_blocks)))
        formulas_before.extend(blocks_by_kind(before_blocks, "formula"))
        formulas_after.extend(blocks_by_kind(after_blocks, "formula"))
        tables_before.extend(blocks_by_kind(before_blocks, "table"))
        tables_after.extend(blocks_by_kind(after_blocks, "table"))
        headings_before.extend(blocks_by_kind(before_blocks, "heading"))
        headings_after.extend(blocks_by_kind(after_blocks, "heading"))

    add("original_text_preserved", all_text_preserved,
        "%d/%d chapters match after removing annotation blocks"
        % (preserved_chapters, len(chapters)))
    add("all_original_blocks_preserved", all_blocks_preserved,
        "%d blocks compared; mismatches: %s"
        % (total_blocks, "; ".join(block_mismatches[:4]) or "none"))
    add("unrequested_blocks_unchanged", unrequested_ok,
        "%d unrequested blocks checked" % unrequested_count)
    if found_targets:
        add("target_paragraphs_unchanged", target_blocks_ok,
            "%d target paragraphs checked" % len(found_targets))
    else:
        add("target_paragraphs_unchanged", True, "no workorder targets",
            severity="warning")
    unresolved_targets = [target.get("target_id") for target in targets
                          if not target.get("found")]
    if targets:
        add("workorder_targets_resolved", not unresolved_targets,
            "unresolved targets: %s" % (", ".join(unresolved_targets) or "none"))
    else:
        add("workorder_targets_resolved", True, "no workorder targets",
            severity="warning")

    if revised_text is None:
        revised_text = compose_text(state)
    annotated = []
    for target in found_targets:
        match = [changes[cid] for cid in accepted_set
                 if cid in changes and changes[cid].get("target_id") == target.get("target_id")]
        if match:
            annotated.append(target.get("target_id"))
    missing_annotations = [t.get("target_id") for t in found_targets
                           if t.get("target_id") not in annotated]
    markers_ok = all(
        re.search(r"<!--\s*editorial-annotation\s+id=%s\s+target=%s\b"
                  % (re.escape(cid), re.escape(changes[cid].get("target_id", ""))),
                  revised_text)
        for cid in accepted_set if cid in changes)
    if found_targets:
        add("target_annotations_present", not missing_annotations and markers_ok,
            "annotated %d/%d targets; missing: %s"
            % (len(annotated), len(found_targets), ", ".join(missing_annotations) or "none"))
    else:
        add("target_annotations_present", True, "no workorder targets",
            severity="warning")

    rejected_absent = True
    for cid in rejected_set:
        if re.search(r"<!--\s*editorial-annotation\s+id=%s\b" % re.escape(cid), revised_text):
            rejected_absent = False
    add("rejected_changes_absent", rejected_absent,
        "%d rejected changes checked" % len(rejected_set))

    add("citations_unchanged", citations_before == citations_after,
        "%d citation markers before, %d after" % (len(citations_before), len(citations_after)))
    add("formulas_unchanged", formulas_before == formulas_after,
        "%d formula blocks before, %d after" % (len(formulas_before), len(formulas_after)))
    add("tables_unchanged", tables_before == tables_after,
        "%d table blocks before, %d after" % (len(tables_before), len(tables_after)))
    add("headings_unchanged", headings_before == headings_after,
        "%d heading blocks before, %d after" % (len(headings_before), len(headings_after)))

    orphan = [cid for cid in annotation_ids_in(revised_text) if cid not in accepted_set]
    add("no_orphan_annotations", not orphan,
        "orphan annotation ids: %s" % (", ".join(orphan) or "none"))

    if out_dir is not None:
        preview = Path(out_dir) / "preview.html"
        add("html_preview_exists", preview.is_file() and preview.stat().st_size > 0,
            str(preview), severity="warning")

    summaries.update({
        "chapters": len(chapters),
        "total_blocks": total_blocks,
        "unrequested_blocks": unrequested_count,
        "targets_total": len(targets),
        "targets_found": len(found_targets),
        "targets_annotated": len(annotated),
        "accepted": len(accepted_set),
        "rejected": len(rejected_set),
        "citations_before": len(citations_before),
        "citations_after": len(citations_after),
        "formulas": len(formulas_before),
        "tables": len(tables_before),
        "headings": len(headings_before),
        "changed_blocks": changed_blocks,
    })
    error_checks = [check for check in checks if check["severity"] == "error"]
    return {
        "ok": all(check["ok"] for check in error_checks),
        "checks": checks,
        "summary": summaries,
        "generated_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# result / records / evidence / change log
# ---------------------------------------------------------------------------
REQUIRED_OPERATIONS = [
    "import", "plan", "propose", "accept", "reject", "compare",
    "rollback", "reapply", "invalidate", "export", "verify",
]


def source_text(state) -> str:
    chunks = []
    for chapter in state.get("chapters") or []:
        path = Path(chapter.get("baseline_path") or "")
        if path.is_file():
            chunks.append(read_text(path))
    return "\n".join(chunks)


def find_quote_locator(state, quote):
    wanted = normalize_ws(quote)
    if not wanted:
        return None
    for chapter in state.get("chapters") or []:
        for block in chapter.get("blocks") or []:
            if wanted in normalize_ws(block.get("raw", "")):
                return block.get("block_id")
    return None


def target_block(state, target_id):
    for chapter in state.get("chapters") or []:
        for block in chapter.get("blocks") or []:
            if block.get("block_id") == target_id:
                return chapter, block
    return None, None


def build_preserved_facts(state, verification):
    facts = []
    seen = set()
    chapters = {chapter.get("chapter_id"): chapter for chapter in state.get("chapters") or []}
    targets = [t for t in state.get("targets") or [] if t.get("found")]
    for target in targets:
        chapter, block = target_block(state, target.get("target_id"))
        if block is None:
            continue
        raw = block.get("raw", "")
        source = chapter.get("source_path") or chapter.get("baseline_path")
        for token in NUMBER.findall(raw):
            value = token.strip()
            if len(value) < 1 or value in (".", ","):
                continue
            key = (value, target.get("target_id"))
            if key in seen:
                continue
            seen.add(key)
            facts.append({
                "fact": value,
                "kind": "number_or_unit",
                "locator": target.get("target_id"),
                "source": source,
                "preserved": True,
                "context": excerpt(raw, 100),
            })
        for token in citation_tokens(raw):
            value = "[%s]" % token
            key = (value, target.get("target_id"))
            if key in seen:
                continue
            seen.add(key)
            facts.append({
                "fact": value,
                "kind": "citation",
                "locator": target.get("target_id"),
                "source": source,
                "preserved": True,
                "context": excerpt(raw, 100),
            })
    notes = state.get("agent_notes") or {}
    corpus = normalize_ws(source_text(state))
    for item in notes.get("preserved_facts") or []:
        if isinstance(item, str):
            fact, locator, kind = item, "agent_note", "agent_declared"
        elif isinstance(item, dict):
            fact = str(first(item.get("fact"), item.get("value"), ""))
            locator = str(first(item.get("locator"), item.get("target_id"), "agent_note"))
            kind = str(item.get("kind") or "agent_declared")
        else:
            continue
        if not fact:
            continue
        verified = normalize_ws(fact) in corpus
        if not verified:
            continue
        key = (fact, locator)
        if key in seen:
            continue
        seen.add(key)
        facts.append({
            "fact": fact,
            "kind": kind,
            "locator": locator,
            "source": "supplied source (verbatim match)",
            "preserved": True,
            "context": excerpt(fact, 100),
        })
    facts.sort(key=lambda item: (str(item.get("locator")), str(item.get("fact"))))
    return facts[:40]


def build_evidence_map(state):
    evidence = []
    for cid in sorted((state.get("changes") or {}).keys()):
        change = state["changes"][cid]
        chapter, block = target_block(state, change.get("target_id"))
        source = chapter.get("source_path") if chapter else None
        items = change.get("evidence")
        if not isinstance(items, list) or not items:
            items = [{
                "claim": change.get("claim"),
                "quote": change.get("basis_quote"),
                "locator": change.get("locator") or change.get("target_id"),
            }]
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            evidence.append({
                "claim_id": "%s-C%d" % (cid, index),
                "change_id": cid,
                "target_id": change.get("target_id"),
                "claim": item.get("claim") or change.get("claim"),
                "quote": item.get("quote") or change.get("basis_quote"),
                "locator": item.get("locator") or change.get("locator")
                or change.get("target_id"),
                "observed_locator": change.get("observed_locator"),
                "quote_verified": change.get("quote_verified"),
                "annotation_type": change.get("annotation_type"),
                "source": source,
                "decision": change.get("status"),
                "note": item.get("note"),
            })
    return evidence


def build_change_log(state, verification):
    log = []
    for cid in sorted((state.get("changes") or {}).keys()):
        change = state["changes"][cid]
        status = change.get("status") or "unknown"
        action = {
            "accepted": "accept_annotation",
            "rejected": "reject_annotation",
            "proposed": "propose_annotation",
        }.get(status, "record_change")
        entry = {
            "change_id": cid,
            "target_id": change.get("target_id"),
            "action": action,
            "status": status,
            "annotation_type": change.get("annotation_type"),
            "proposed_at": change.get("proposed_at"),
            "decided_at": change.get("decided_at"),
            "reason": change.get("reason") or change.get("claim"),
            "quote_verified": change.get("quote_verified"),
            "diff_path": change.get("diff_path"),
            "original_paragraph_preserved": True,
        }
        log.append(entry)
    for item in state.get("rollback_history") or []:
        log.append({
            "action": "rollback_or_reapply",
            "status": item.get("action"),
            "from_version": item.get("from"),
            "to_version": item.get("to"),
            "at": item.get("at"),
            "reason": item.get("reason") or "",
        })
    checks = {check["id"]: check for check in verification.get("checks") or []}
    log.append({
        "action": "verify_original_blocks",
        "status": "passed" if verification.get("ok") else "failed",
        "original_text_preserved": checks.get("original_text_preserved", {}).get("ok"),
        "all_original_blocks_preserved": checks.get("all_original_blocks_preserved", {}).get("ok"),
        "citations_unchanged": checks.get("citations_unchanged", {}).get("ok"),
        "formulas_unchanged": checks.get("formulas_unchanged", {}).get("ok"),
        "tables_unchanged": checks.get("tables_unchanged", {}).get("ok"),
        "headings_unchanged": checks.get("headings_unchanged", {}).get("ok"),
    })
    return log


def build_operation_coverage(receipts):
    coverage = {}
    for operation in REQUIRED_OPERATIONS:
        count = sum(1 for receipt in receipts if receipt.get("operation") == operation)
        coverage[operation] = {"present": count > 0, "count": count}
    return coverage


def build_uncertainty(state, verification):
    items = []
    for warning in state.get("warnings") or []:
        items.append({"item": "input_warning", "reason": warning,
                      "impact": "review the import receipt before trusting the export"})
    if not (state.get("workorder") or {}).get("id") and not state.get("targets"):
        items.append({"item": "workorder_missing",
                      "reason": "no organizer-edit-workorder target ids were found",
                      "impact": "the manuscript is preserved but no target annotation can be proposed"})
    for target in state.get("targets") or []:
        if not target.get("found"):
            items.append({"item": "target_not_found",
                          "reason": "%s: %s" % (target.get("target_id"),
                                                target.get("reason") or "not located"),
                          "impact": "this target remains unresolved; do not invent an annotation"})
    for cid, change in (state.get("changes") or {}).items():
        if change.get("status") == "rejected":
            continue
        if change.get("quote_verified") is False:
            items.append({"item": "quote_unverified",
                          "reason": "%s basis quote was not found verbatim" % cid,
                          "impact": "the evidence map marks it as unverified"})
        uncertainty = change.get("uncertainty")
        if isinstance(uncertainty, list):
            for value in uncertainty:
                if value:
                    items.append({"item": "annotation_uncertainty",
                                  "reason": "%s: %s" % (cid, value),
                                  "impact": "human confirmation needed"})
        elif uncertainty:
            items.append({"item": "annotation_uncertainty",
                          "reason": "%s: %s" % (cid, uncertainty),
                          "impact": "human confirmation needed"})
    for check in verification.get("checks") or []:
        if not check.get("ok"):
            items.append({"item": "verification_failed",
                          "reason": "%s: %s" % (check.get("id"), check.get("detail")),
                          "impact": "inspect verify-report.json before submission"})
    return items


def build_limitations(state):
    limitations = [
        "Annotations are inserted as Markdown blocks with HTML comment markers; "
        "no Word tracked-change XML is produced.",
        "Fact checking is limited to the supplied files; no external bibliographic "
        "database or network lookup is used.",
        "PDF-only inputs require the platform PDF text tool; this CLI reads "
        "Markdown, TXT and DOCX text.",
        "Scientific judgement in each annotation remains the author's; a human "
        "should confirm wording, scope and journal conventions.",
        "Source-derived working chapters are not a completed degree thesis.",
    ]
    notes = state.get("agent_notes") or {}
    for item in notes.get("limitations") or []:
        if item and str(item) not in limitations:
            limitations.append(str(item))
    if state.get("pdf_sources"):
        limitations.append("PDF files were present but not parsed by this CLI: %s"
                           % ", ".join(state["pdf_sources"][:5]))
    return limitations


def build_manual_confirmations(state):
    confirmations = [
        "Confirm every annotation is scientifically correct and clearly marked as "
        "editorial rather than original research.",
        "Confirm target paragraph ids and boundaries against the supplied source.",
        "Confirm that unrequested text, tables, formulas, citations and headings "
        "must remain untouched.",
    ]
    notes = state.get("agent_notes") or {}
    for item in notes.get("manual_confirmations") or []:
        if item and str(item) not in confirmations:
            confirmations.append(str(item))
    return confirmations


def build_original_records(state):
    chapters = []
    total_blocks = 0
    total_paragraphs = 0
    for chapter in state.get("chapters") or []:
        blocks = []
        for block in chapter.get("blocks") or []:
            total_blocks += 1
            if block.get("kind") == "paragraph":
                total_paragraphs += 1
            blocks.append({
                "block_id": block.get("block_id"),
                "kind": block.get("kind"),
                "line_start": block.get("line_start"),
                "line_end": block.get("line_end"),
                "sha256": block.get("sha256"),
                "excerpt": block.get("excerpt"),
            })
        chapters.append({
            "chapter_id": chapter.get("chapter_id"),
            "title": chapter.get("title"),
            "source_path": chapter.get("source_path"),
            "baseline_path": chapter.get("baseline_path"),
            "baseline_sha256": chapter.get("baseline_sha256"),
            "block_count": len(blocks),
            "blocks": blocks,
        })
    return {
        "case_id": state.get("case_id"),
        "workorder_id": (state.get("workorder") or {}).get("id"),
        "generated_at": now_iso(),
        "chapters": chapters,
        "total_blocks": total_blocks,
        "total_paragraphs": total_paragraphs,
    }


def build_revision_record(state, verification, artifacts, receipts):
    accepted, rejected = effective_lists(state)
    coverage = build_operation_coverage(receipts)
    return {
        "case_id": state.get("case_id"),
        "workorder_id": (state.get("workorder") or {}).get("id"),
        "tool": "revision_flow.py %s" % VERSION,
        "generated_at": now_iso(),
        "current_version": state.get("current_version"),
        "versions": state.get("versions") or [],
        "rollback_history": state.get("rollback_history") or [],
        "accepted": accepted,
        "rejected": rejected,
        "changes": [state["changes"][cid] for cid in sorted(state.get("changes") or {})],
        "targets": state.get("targets") or [],
        "verification": verification,
        "operation_receipts": receipts,
        "operation_coverage": coverage,
        "artifacts": artifacts,
        "preserved_facts": build_preserved_facts(state, verification),
        "evidence_map": build_evidence_map(state),
        "change_log": build_change_log(state, verification),
        "manual_confirmations": build_manual_confirmations(state),
        "uncertainty": build_uncertainty(state, verification),
        "limitations": build_limitations(state),
    }


def document_language(state):
    text = source_text(state)
    cjk = len(CJK.findall(text))
    latin = len(WORD.findall(text))
    if cjk >= 20 and cjk >= latin * 0.2:
        return "zh" if latin < cjk * 2 else "mixed"
    return "en" if latin else "unknown"


def overall_status(state, verification):
    targets = state.get("targets") or []
    if not targets:
        return "partial"
    if any(not target.get("found") for target in targets):
        return "partial"
    found = [target for target in targets if target.get("found")]
    if not found:
        return "partial"
    accepted, _ = effective_lists(state)
    changes = state.get("changes") or {}
    annotated = {changes[cid].get("target_id") for cid in accepted if cid in changes}
    if any(target.get("target_id") not in annotated for target in found):
        return "partial"
    if not verification.get("ok"):
        return "partial"
    return "completed"


def build_result(state, verification, artifacts, receipts, revised_text):
    accepted, rejected = effective_lists(state)
    changes = state.get("changes") or {}
    targets_out = []
    for target in state.get("targets") or []:
        match = [cid for cid in accepted
                 if cid in changes and changes[cid].get("target_id") == target.get("target_id")]
        if not target.get("found"):
            status = "blocked"
        elif match:
            status = "annotated"
        elif any(changes[cid].get("status") == "rejected"
                 for cid in changes
                 if changes[cid].get("target_id") == target.get("target_id")):
            status = "rejected"
        else:
            status = "unresolved"
        targets_out.append({
            "target_id": target.get("target_id"),
            "chapter_id": target.get("chapter_id"),
            "found": target.get("found"),
            "status": status,
            "change_id": match[0] if match else None,
            "reason": target.get("reason"),
        })
    words_original = sum(word_count(read_text(Path(chapter.get("baseline_path"))))
                         for chapter in state.get("chapters") or []
                         if Path(chapter.get("baseline_path") or "").is_file())
    result = {
        "case_id": state.get("case_id") or "unknown-case",
        "task_family": "source-preserving-revision",
        "status": overall_status(state, verification),
        "workorder": {
            "id": (state.get("workorder") or {}).get("id"),
            "project_mode": (state.get("workorder") or {}).get("project_mode"),
            "target_ids": [target.get("target_id") for target in state.get("targets") or []],
            "operation_requirements": (state.get("workorder") or {}).get("operation_requirements") or [],
            "task": (state.get("workorder") or {}).get("task"),
            "source_fidelity": (state.get("workorder") or {}).get("source_fidelity"),
        },
        "deliverable": {
            "path": "revised.md",
            "format": "markdown",
        },
        "document": {
            "path": "revised.md",
            "format": "markdown",
            "chapters": len(state.get("chapters") or []),
            "words_original": words_original,
            "words_revised": word_count(revised_text),
            "language": document_language(state),
        },
        "preserved_facts": build_preserved_facts(state, verification),
        "evidence_map": build_evidence_map(state),
        "change_log": build_change_log(state, verification),
        "operation_receipts": receipts,
        "operation_coverage": build_operation_coverage(receipts),
        "artifacts": artifacts,
        "targets": targets_out,
        "revision": {
            "current_version": state.get("current_version"),
            "versions": state.get("versions") or [],
            "accepted": accepted,
            "rejected": rejected,
            "rollback_history": state.get("rollback_history") or [],
            "original_preserved": bool(
                verification.get("summary", {}).get("chapters")
                and all(check.get("ok") for check in verification.get("checks") or []
                        if check.get("id") in ("original_text_preserved",
                                               "all_original_blocks_preserved"))),
        },
        "verification": verification,
        "uncertainty": build_uncertainty(state, verification),
        "limitations": build_limitations(state),
        "manual_confirmations": build_manual_confirmations(state),
    }
    return result


# ---------------------------------------------------------------------------
# HTML preview (self-contained, no external resources)
# ---------------------------------------------------------------------------
CSS = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif;
       margin: 0; background: #f6f7f9; color: #1f2328; line-height: 1.65; }
header { background: #ffffff; border-bottom: 1px solid #d0d7de; padding: 20px 28px; }
main { max-width: 980px; margin: 0 auto; padding: 24px 28px 80px; }
h1 { font-size: 22px; margin: 0 0 8px; }
.meta { color: #57606a; font-size: 13px; }
.legend { margin-top: 10px; font-size: 13px; }
.legend span { display: inline-block; margin-right: 16px; }
.chapter { background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px;
           padding: 20px 24px; margin: 20px 0; }
.chapter h2 { margin-top: 0; font-size: 18px; border-bottom: 1px solid #eaeef2;
              padding-bottom: 8px; }
h3, h4, h5, h6 { margin-top: 18px; }
p { margin: 10px 0; }
.pid { color: #8c959f; font-size: 11px; font-family: ui-monospace, monospace;
       margin-top: 14px; }
table { border-collapse: collapse; margin: 12px 0; width: 100%; }
th, td { border: 1px solid #d0d7de; padding: 6px 9px; text-align: left; font-size: 14px; }
th { background: #f6f8fa; }
pre.formula { background: #f6f8fa; border: 1px solid #eaeef2; border-radius: 6px;
              padding: 10px 12px; overflow-x: auto; }
.annotation { border-left: 4px solid #bf8700; background: #fff8c5; border-radius: 4px;
              padding: 10px 14px; margin: 12px 0; color: #4d2d00; }
.annotation .ann-title { font-weight: 600; margin-bottom: 6px; }
.annotation .ann-field { margin: 4px 0; font-size: 14px; }
.annotation .ann-field b { color: #7d4e00; }
.toc a { color: #0969da; text-decoration: none; margin-right: 14px; }
.note { color: #57606a; font-size: 13px; }
@media (prefers-color-scheme: dark) {
  body { background: #0d1117; color: #e6edf3; }
  header, .chapter { background: #161b22; border-color: #30363d; }
  .chapter h2 { border-color: #30363d; }
  .meta, .note, .pid { color: #8b949e; }
  th, td { border-color: #30363d; }
  th, pre.formula { background: #21262d; }
  .annotation { background: #3a2d00; color: #f0d58c; border-color: #d29922; }
  .annotation .ann-field b { color: #e3b341; }
  .toc a { color: #58a6ff; }
}
"""


def html_text(value) -> str:
    return html.escape(str(value or ""), quote=False)


def render_table_html(raw):
    rows = []
    for line in raw.split("\n"):
        if "|" not in line:
            continue
        stripped = line.strip()
        if re.match(r"^\|?\s*:?-{3,}", stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if any(cells):
            rows.append(cells)
    if not rows:
        return "<p>%s</p>" % html_text(raw)
    out = ["<table>"]
    for index, cells in enumerate(rows):
        tag = "th" if index == 0 else "td"
        out.append("<tr>" + "".join("<%s>%s</%s>" % (tag, html_text(cell), tag)
                                    for cell in cells) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


def render_block_html(block):
    raw = block.get("raw", "")
    kind = block.get("kind")
    if kind == "heading":
        match = HEADING.search(raw)
        if match:
            level = min(6, max(3, len(match.group(1)) + 1))
            return "<h%d>%s</h%d>" % (level, html_text(match.group(2)), level)
        return "<h3>%s</h3>" % html_text(raw)
    if kind == "table":
        return render_table_html(raw)
    if kind == "formula":
        return "<pre class=\"formula\">%s</pre>" % html_text(raw)
    lines = [line for line in raw.split("\n") if line.strip()]
    return "<p>%s</p>" % "<br>".join(html_text(line) for line in lines)


def render_annotation_html(change):
    return (
        "<aside class=\"annotation\" id=\"ann-%s\">"
        "<div class=\"ann-title\">Editorial annotation %s / 编辑批注（非原文）</div>"
        "<div class=\"ann-field\"><b>Target / 目标：</b>%s</div>"
        "<div class=\"ann-field\"><b>Type / 类型：</b>%s</div>"
        "<div class=\"ann-field\"><b>Annotation / 批注：</b>%s</div>"
        "<div class=\"ann-field\"><b>Basis quote / 依据原文：</b>“%s”</div>"
        "<div class=\"ann-field\"><b>Locator / 定位：</b>%s</div>"
        "<div class=\"ann-field\"><b>Claim / 论断：</b>%s</div>"
        "<div class=\"ann-field\"><b>Uncertainty / 不确定性：</b>%s</div>"
        "</aside>"
        % (html_text(change.get("change_id")), html_text(change.get("change_id")),
           html_text(change.get("target_id")), html_text(change.get("annotation_type")),
           html_text(change.get("annotation")), html_text(change.get("basis_quote")),
           html_text(change.get("locator") or change.get("target_id")),
           html_text(change.get("claim")), html_text(change.get("uncertainty")
                                                      or "以原文为准 / see source")))


def render_html(state, verification, revised_text):
    accepted, rejected = effective_lists(state)
    changes = state.get("changes") or {}
    accepted_changes_list = [changes[cid] for cid in accepted if cid in changes]
    by_target = {}
    for change in accepted_changes_list:
        by_target.setdefault(change.get("target_id"), []).append(change)
    toc = []
    sections = []
    for chapter in state.get("chapters") or []:
        cid = chapter.get("chapter_id")
        title = chapter.get("title") or cid
        toc.append("<a href=\"#ch-%s\">%s</a>" % (html_text(cid), html_text(title)))
        parts = ["<section class=\"chapter\" id=\"ch-%s\">" % html_text(cid),
                 "<h2>%s</h2>" % html_text(title)]
        for block in chapter.get("blocks") or []:
            parts.append("<div class=\"pid\">%s · %s</div>"
                         % (html_text(block.get("block_id")), html_text(block.get("kind"))))
            parts.append(render_block_html(block))
            for change in by_target.get(block.get("block_id"), []):
                parts.append(render_annotation_html(change))
        parts.append("</section>")
        sections.append("\n".join(parts))
    checks_ok = sum(1 for check in verification.get("checks") or [] if check.get("ok"))
    checks_total = len(verification.get("checks") or [])
    header = (
        "<header><h1>Source-preserving revision preview / 源文保留修订预览</h1>"
        "<div class=\"meta\">case_id: %s · workorder: %s · version: %s · "
        "accepted: %d · rejected: %d · verification: %d/%d checks"
        "</div><div class=\"legend\">"
        "<span>Original text is shown unchanged.</span>"
        "<span>Yellow blocks are editorial annotations, not original research.</span>"
        "</div><div class=\"toc\">%s</div></header>"
        % (html_text(state.get("case_id")),
           html_text((state.get("workorder") or {}).get("id")),
           html_text(state.get("current_version")), len(accepted), len(rejected),
           checks_ok, checks_total, " ".join(toc))
    )
    if not accepted_changes_list:
        sections.insert(0, "<p class=\"note\">No accepted annotation at export time. "
                           "The preview shows the preserved source only.</p>")
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Revision preview %s</title><style>%s</style></head><body>%s"
        "<main>%s</main></body></html>\n"
        % (html_text(state.get("case_id")), CSS, header, "\n".join(sections))
    )


# ---------------------------------------------------------------------------
# commands: import / plan / propose / decisions / status
# ---------------------------------------------------------------------------
def emit(payload, as_json, text_lines=None):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif text_lines:
        for line in text_lines:
            print(line)
    return EXIT_OK if payload.get("ok", True) else EXIT_FAIL


def infer_chapter_for_target(target_id, chapters):
    prefix = str(target_id).split("-P")[0].lower()
    for chapter in chapters:
        cid = str(chapter.get("chapter_id") or "")
        if cid.lower() == prefix:
            return chapter
    match = re.search(r"(\d+)$", prefix)
    if match:
        wanted = int(match.group(1))
        for chapter in chapters:
            if int(chapter.get("order") or 0) == wanted:
                return chapter
    return None


def locate_targets(chapters, target_ids, workorder):
    targets = []
    task = str(workorder.get("task") or
               "Add one substantive source-grounded editorial annotation at this target.")
    for target_id in target_ids:
        found_chapter = None
        found_block = None
        for chapter in chapters:
            for block in chapter.get("blocks") or []:
                if block.get("block_id") == target_id:
                    found_chapter, found_block = chapter, block
                    break
            if found_block is not None:
                break
        if found_block is None:
            needle = str(target_id)
            for chapter in chapters:
                for block in chapter.get("blocks") or []:
                    if needle in (block.get("raw") or ""):
                        found_chapter, found_block = chapter, block
                        break
                if found_block is not None:
                    break
        if found_block is not None:
            targets.append({
                "target_id": target_id,
                "chapter_id": found_chapter.get("chapter_id"),
                "block_id": found_block.get("block_id"),
                "found": True,
                "paragraph_sha256": found_block.get("sha256"),
                "paragraph_excerpt": found_block.get("excerpt"),
                "instruction": task,
                "reason": None,
            })
        else:
            chapter = infer_chapter_for_target(target_id, chapters)
            targets.append({
                "target_id": target_id,
                "chapter_id": chapter.get("chapter_id") if chapter else None,
                "block_id": None,
                "found": False,
                "paragraph_sha256": None,
                "paragraph_excerpt": None,
                "instruction": task,
                "reason": "no block with this id was located in the supplied chapters",
            })
    return targets


def read_chapter_source(entry, input_root, work):
    source = entry.get("source_path")
    notes = list(entry.get("notes") or [])
    source_info = None
    pdf_only = False
    if source is None:
        text = normalize_text(str(entry.get("content") or ""))
        source_info = {
            "path": "(inline in case.json)",
            "format": "inline",
            "bytes": len(text.encode("utf-8")),
            "sha256": sha_text(text),
        }
        return text, source_info, notes, pdf_only
    source = Path(source)
    suffix = source.suffix.lower()
    raw = source.read_bytes()
    source_info = {
        "path": str(source),
        "format": suffix.lstrip(".") or "text",
        "bytes": len(raw),
        "sha256": sha_bytes(raw),
    }
    if suffix in PDF_EXT:
        return "", source_info, notes + ["pdf_not_parsed"], True
    if suffix in DOCX_EXT:
        try:
            text = chapter_text_from_docx(source)
            notes.append("converted from docx; verify tables/equations by eye")
            return normalize_text(text), source_info, notes, False
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            raise RevisionError("cannot read docx %s: %s" % (source, exc)) from exc
    return normalize_text(read_text(source)), source_info, notes, False


def make_plan_payload(state):
    targets = state.get("targets") or []
    blocked = [target for target in targets if not target.get("found")]
    return {
        "case_id": state.get("case_id"),
        "workorder_id": (state.get("workorder") or {}).get("id"),
        "project_mode": (state.get("workorder") or {}).get("project_mode"),
        "task": (state.get("workorder") or {}).get("task"),
        "workflow_requirements": (state.get("workorder") or {}).get("operation_requirements") or [],
        "targets": targets,
        "blocked": blocked,
        "instructions": (
            "For each target, write one substantive editorial annotation grounded in an "
            "exact source quotation. Fill work/annotations.json from "
            "work/annotations.template.json, then run `propose`. Annotation types: "
            "scientific_condition, limitation, clarification. Do not rewrite the target "
            "paragraph and do not annotate blocks that the workorder did not name."
        ),
    }


def make_annotation_template(state):
    annotations = []
    index = 0
    for target in state.get("targets") or []:
        index += 1
        item = {
            "change_id": "E%02d" % index,
            "target_id": target.get("target_id"),
            "annotation_type": "clarification",
            "annotation": "",
            "claim": "",
            "basis_quote": "",
            "locator": target.get("target_id"),
            "uncertainty": "",
        }
        if not target.get("found"):
            item["blocked"] = True
            item["blocked_reason"] = target.get("reason")
        annotations.append(item)
    return {
        "_instructions": (
            "Copy this file to annotations.json and fill every field for each found target. "
            "basis_quote must be copied verbatim from the supplied source; propose refuses "
            "quotes that are not found unless --allow-unverified-quote is used. Add extra "
            "candidate changes only when they have a real reason; reject the candidates you "
            "do not accept. Empty strings and an empty annotations list are valid when no "
            "target is supplied."
        ),
        "case_id": state.get("case_id"),
        "annotations": annotations,
    }


def write_plan_files(state, work):
    paths = ensure_workspace(work)
    plan = make_plan_payload(state)
    template = make_annotation_template(state)
    dump_json(paths["plan"], plan)
    dump_json(paths["template"], template)
    return plan, template


def write_input_error(work, payload):
    paths = ensure_workspace(work)
    dump_json(paths["input_error"], payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return EXIT_INPUT


def cmd_init(args):
    input_root = Path(args.input)
    work = Path(args.work)
    ensure_workspace(work)
    state_path = workspace_paths(work)["state"]
    if state_path.is_file() and not getattr(args, "force", False):
        try:
            state = load_state(work)
            return emit({
                "ok": True,
                "already_initialized": True,
                "case_id": state.get("case_id"),
                "workorder_id": (state.get("workorder") or {}).get("id"),
                "chapters": len(state.get("chapters") or []),
                "targets": len(state.get("targets") or []),
                "work": str(work),
            }, args.json)
        except RevisionError:
            pass
    if not input_root.exists():
        return write_input_error(work, {
            "ok": False, "status": "input_error",
            "reason": "input root does not exist: %s" % input_root,
            "input_root": str(input_root),
        })
    if getattr(args, "force", False):
        paths = workspace_paths(work)
        for key in ("baseline", "diffs", "proposals", "versions"):
            shutil.rmtree(paths[key], ignore_errors=True)
        for key in ("state", "receipts", "import_receipt", "plan", "template",
                    "invalid_receipt", "input_error"):
            try:
                paths[key].unlink()
            except OSError:
                pass
        ensure_workspace(work)
    case, case_path, warnings = load_case(input_root)
    workorder, workorder_source = load_workorder(case, input_root)
    target_ids = target_ids_from_workorder(workorder)
    prefixes = target_prefixes(target_ids)
    case_dir = case_path.parent if case_path else input_root
    entries = normalize_chapter_entries(case, input_root, case_dir)
    if not entries:
        return write_input_error(work, {
            "ok": False, "status": "input_error",
            "reason": "no chapter files or inline chapters were found",
            "input_root": str(input_root),
            "checked": ["case.json chapters[]", "chapters/", "sections/",
                        "source/", "manuscript/", "*.md", "*.txt", "*.docx"],
            "next_step": "use the platform PDF text tool to extract readable chapters, "
                         "then rerun init with that input directory",
        })
    chapters = []
    source_files = []
    pdf_sources = []
    chapter_warnings = []
    baseline_dir = workspace_paths(work)["baseline"]
    for order, entry in enumerate(entries, start=1):
        try:
            text, source_info, notes, is_pdf = read_chapter_source(entry, input_root, work)
        except RevisionError as exc:
            chapter_warnings.append(str(exc))
            continue
        source_files.append(source_info)
        if is_pdf:
            pdf_sources.append(source_info["path"])
            chapter_warnings.append("PDF not parsed by stdlib CLI: %s" % source_info["path"])
            continue
        if not text.strip():
            chapter_warnings.append("empty chapter skipped: %s" % entry.get("chapter_id"))
            continue
        cid = entry["chapter_id"]
        baseline_path = baseline_dir / (cid + ".md")
        write_text(baseline_path, text)
        blocks = parse_chapter(text, cid, order, prefixes)
        chapters.append({
            "chapter_id": cid,
            "title": entry.get("title") or cid,
            "order": order,
            "source_path": (source_info or {}).get("path"),
            "source_format": (source_info or {}).get("format"),
            "baseline_path": str(baseline_path.resolve()),
            "baseline_sha256": sha_text(normalize_text(text)),
            "words": word_count(text),
            "blocks": blocks,
            "notes": notes,
        })
    if not chapters:
        return write_input_error(work, {
            "ok": False, "status": "input_error",
            "reason": "chapters were discovered but none contained readable text",
            "input_root": str(input_root),
            "pdf_sources": pdf_sources,
            "warnings": chapter_warnings,
        })
    targets = locate_targets(chapters, target_ids, workorder)
    case_id = first(case.get("case_id"), case.get("caseId"), case.get("id"),
                    "unknown-case")
    state = {
        "schema_version": 1,
        "tool": "revision_flow.py %s" % VERSION,
        "case_id": str(case_id),
        "workorder_id": workorder.get("id"),
        "workorder_source": workorder_source,
        "input_root": str(input_root.resolve()),
        "work_root": str(work.resolve()),
        "imported_at": now_iso(),
        "workorder": workorder,
        "targets": targets,
        "chapters": chapters,
        "source": {
            "files": source_files,
            "pdf_sources": pdf_sources,
            "total_blocks": sum(len(chapter["blocks"]) for chapter in chapters),
            "total_paragraphs": sum(1 for chapter in chapters
                                    for block in chapter["blocks"]
                                    if block.get("kind") == "paragraph"),
        },
        "changes": {},
        "versions": [],
        "current_version": "v0",
        "last_rollback": None,
        "rollback_history": [],
        "agent_notes": {},
        "warnings": warnings + chapter_warnings,
    }
    v0 = {
        "version": "v0",
        "created_at": state["imported_at"],
        "label": "import baseline",
        "reason": "original source imported; no annotation accepted",
        "accepted": [],
        "rejected": [],
        "changes_sha256": sha_json({"accepted": [], "rejected": []}),
    }
    state["versions"] = [v0]
    save_state(work, state)
    import_receipt = {
        "case_id": state["case_id"],
        "workorder_id": workorder.get("id"),
        "workorder_source": workorder_source,
        "imported_at": state["imported_at"],
        "input_root": state["input_root"],
        "work_root": state["work_root"],
        "source_files": source_files,
        "pdf_sources": pdf_sources,
        "chapters": [{
            "chapter_id": chapter["chapter_id"],
            "title": chapter["title"],
            "source_path": chapter["source_path"],
            "baseline_path": chapter["baseline_path"],
            "baseline_sha256": chapter["baseline_sha256"],
            "blocks": len(chapter["blocks"]),
        } for chapter in chapters],
        "target_ids": target_ids,
        "targets_found": sum(1 for target in targets if target.get("found")),
        "targets_blocked": sum(1 for target in targets if not target.get("found")),
        "warnings": state["warnings"],
    }
    dump_json(workspace_paths(work)["import_receipt"], import_receipt)
    append_receipt(work, "import", True, {
        "case_id": state["case_id"],
        "workorder_id": workorder.get("id"),
        "chapters": len(chapters),
        "source_files": len(source_files),
        "targets": len(targets),
        "targets_found": import_receipt["targets_found"],
    })
    plan_payload, _template = write_plan_files(state, work)
    append_receipt(work, "plan", True, {
        "targets": len(plan_payload.get("targets") or []),
        "blocked": len(plan_payload.get("blocked") or []),
        "auto_after_import": True,
    })
    return emit({
        "ok": True,
        "case_id": state["case_id"],
        "workorder_id": workorder.get("id"),
        "chapters": len(chapters),
        "blocks": state["source"]["total_blocks"],
        "targets": [{
            "target_id": target.get("target_id"),
            "found": target.get("found"),
            "chapter_id": target.get("chapter_id"),
            "reason": target.get("reason"),
        } for target in targets],
        "work": str(work),
        "plan": str(workspace_paths(work)["plan"]),
        "annotations_template": str(workspace_paths(work)["template"]),
        "import_receipt": str(workspace_paths(work)["import_receipt"]),
    }, args.json, [
        "initialized %s" % state["case_id"],
        "chapters: %d, blocks: %d" % (len(chapters), state["source"]["total_blocks"]),
        "targets: %d found / %d total" % (import_receipt["targets_found"], len(targets)),
        "next: fill %s then run propose" % workspace_paths(work)["template"],
    ])


def cmd_plan(args):
    work = Path(args.work)
    state = load_state(work)
    plan_payload, _template = write_plan_files(state, work)
    append_receipt(work, "plan", True, {
        "targets": len(plan_payload.get("targets") or []),
        "blocked": len(plan_payload.get("blocked") or []),
    })
    return emit({
        "ok": True,
        "case_id": state.get("case_id"),
        "targets": plan_payload.get("targets"),
        "blocked": plan_payload.get("blocked"),
        "plan": str(workspace_paths(work)["plan"]),
        "annotations_template": str(workspace_paths(work)["template"]),
    }, args.json, [
        "targets: %d, blocked: %d" % (len(plan_payload.get("targets") or []),
                                      len(plan_payload.get("blocked") or [])),
        "plan: %s" % workspace_paths(work)["plan"],
        "template: %s" % workspace_paths(work)["template"],
    ])


def next_change_id(state):
    used = set((state.get("changes") or {}).keys())
    index = 1
    while "E%02d" % index in used:
        index += 1
    return "E%02d" % index


def load_annotation_spec(args):
    items = []
    notes = {}
    if args.spec:
        path = Path(args.spec)
        if not path.is_file():
            raise RevisionError("annotation spec not found: %s" % path)
        try:
            spec = load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            raise RevisionError("cannot parse annotation spec: %s" % exc) from exc
        if isinstance(spec, list):
            items.extend(spec)
        elif isinstance(spec, dict):
            raw = spec.get("annotations") or spec.get("changes") or []
            if isinstance(raw, dict):
                raw = [raw]
            if isinstance(raw, list):
                items.extend(raw)
            for key in ("preserved_facts", "manual_confirmations", "limitations",
                        "consistency_notes"):
                if key in spec:
                    notes[key] = spec[key]
            if spec.get("case_id"):
                notes["spec_case_id"] = spec.get("case_id")
        else:
            raise RevisionError("annotation spec must be an object or a list")
    if args.target or args.annotation:
        items.append({
            "change_id": args.change_id,
            "target_id": args.target,
            "annotation_type": args.type,
            "annotation": args.annotation,
            "claim": args.claim,
            "basis_quote": args.basis_quote,
            "locator": args.locator,
            "uncertainty": args.uncertainty,
        })
    return items, notes


def cmd_propose(args):
    work = Path(args.work)
    state = load_state(work)
    if not args.spec:
        default_spec = workspace_paths(work)["work"] / "annotations.json"
        if default_spec.is_file():
            args.spec = str(default_spec)
    items, notes = load_annotation_spec(args)
    if not items:
        raise RevisionError("no annotations supplied; use --spec or --target/--annotation")
    targets = {target.get("target_id"): target for target in state.get("targets") or []}
    corpus = normalize_ws(source_text(state))
    proposed = []
    for item in items:
        if not isinstance(item, dict):
            raise RevisionError("each annotation must be a JSON object")
        target_id = str(first(item.get("target_id"), item.get("paragraph_id"), "")).strip()
        if not target_id:
            raise RevisionError("annotation is missing target_id")
        target = targets.get(target_id)
        if target is None:
            raise RevisionError("target %s is not in the workorder" % target_id)
        if not target.get("found"):
            raise RevisionError("target %s is blocked: %s"
                                % (target_id, target.get("reason")))
        annotation = str(item.get("annotation") or "").strip()
        if len(annotation) < MIN_ANNOTATION_CHARS:
            raise RevisionError("annotation for %s is too short (%d chars; need >= %d)"
                                % (target_id, len(annotation), MIN_ANNOTATION_CHARS))
        quote = str(first(item.get("basis_quote"), item.get("quote"), "")).strip()
        if len(quote) < MIN_QUOTE_CHARS:
            raise RevisionError("basis_quote for %s is missing or too short" % target_id)
        quote_verified = normalize_ws(quote) in corpus
        if not quote_verified and not args.allow_unverified_quote:
            raise RevisionError(
                "basis_quote for %s was not found verbatim in the supplied source; "
                "copy an exact substring or pass --allow-unverified-quote" % target_id)
        kind = str(item.get("annotation_type") or item.get("type") or "clarification")
        kind = TYPE_ALIASES.get(kind.strip().lower(), kind.strip().lower())
        if kind not in ANNOTATION_TYPES:
            raise RevisionError("unknown annotation_type %r; use one of %s"
                                % (kind, ", ".join(sorted(ANNOTATION_TYPES))))
        change_id = str(first(item.get("change_id"), "") or "").strip()
        existing = state.get("changes") or {}
        if change_id and change_id in existing \
                and existing[change_id].get("status") in ("accepted", "rejected"):
            raise RevisionError("change %s is already %s"
                                % (change_id, existing[change_id].get("status")))
        if not change_id:
            change_id = next_change_id(state)
        if change_id in existing and existing[change_id].get("status") == "proposed":
            state["changes"].pop(change_id, None)
        chapter = next((chapter for chapter in state.get("chapters") or []
                        if chapter.get("chapter_id") == target.get("chapter_id")), None)
        block = None
        if chapter:
            block = next((block for block in chapter.get("blocks") or []
                          if block.get("block_id") == target.get("block_id")), None)
        if block is None:
            raise RevisionError("target block %s is missing from chapter %s"
                                % (target.get("block_id"), target.get("chapter_id")))
        change = {
            "change_id": change_id,
            "target_id": target_id,
            "block_id": target.get("block_id"),
            "chapter_id": target.get("chapter_id"),
            "annotation": annotation,
            "claim": str(first(item.get("claim"), "") or annotation.split("。")[0]).strip(),
            "basis_quote": quote,
            "locator": str(first(item.get("locator"), target_id)).strip(),
            "observed_locator": find_quote_locator(state, quote),
            "quote_verified": quote_verified,
            "annotation_type": kind,
            "uncertainty": item.get("uncertainty") or "",
            "evidence": item.get("evidence") or [],
            "reason": item.get("reason") or "",
            "status": "proposed",
            "proposed_at": now_iso(),
            "decided_at": None,
            "before_sha256": block.get("sha256"),
            "diff_path": None,
            "proposal_path": None,
        }
        before = block.get("raw", "")
        after = before + "\n\n" + annotation_block(change)
        diff = "\n".join(difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile="%s (original)" % target_id,
            tofile="%s (proposed annotation)" % target_id, lineterm=""))
        diff_path = workspace_paths(work)["diffs"] / ("%s.diff" % change_id)
        write_text(diff_path, diff + "\n")
        proposal_path = workspace_paths(work)["proposals"] / ("%s.json" % change_id)
        dump_json(proposal_path, change)
        change["diff_path"] = rel_to(diff_path, work)
        change["proposal_path"] = rel_to(proposal_path, work)
        state.setdefault("changes", {})[change_id] = change
        apply_version_statuses(state)
        proposed.append(change_id)
    state.setdefault("agent_notes", {}).update({
        key: value for key, value in notes.items() if key in
        ("preserved_facts", "manual_confirmations", "limitations", "consistency_notes")
    })
    save_state(work, state)
    append_receipt(work, "propose", True, {
        "change_ids": proposed,
        "unverified_quotes": [cid for cid in proposed
                              if not state["changes"][cid].get("quote_verified")],
    })
    return emit({
        "ok": True,
        "proposed": proposed,
        "changes": [{
            "change_id": cid,
            "target_id": state["changes"][cid].get("target_id"),
            "quote_verified": state["changes"][cid].get("quote_verified"),
            "diff": state["changes"][cid].get("diff_path"),
        } for cid in proposed],
        "next": "review the diffs, then accept or reject each change individually",
    }, args.json, ["proposed: %s" % ", ".join(proposed),
                   "next: accept/reject each change; diffs are in work/diffs/"])


def cmd_accept(args):
    work = Path(args.work)
    state = load_state(work)
    change_id = args.change_id
    changes = state.get("changes") or {}
    change = changes.get(change_id)
    if change is None:
        raise RevisionError("unknown change id: %s" % change_id)
    accepted, rejected = effective_lists(state)
    if change_id in accepted:
        raise RevisionError("change %s is already accepted" % change_id)
    if change_id in rejected:
        raise RevisionError("change %s is rejected; rollback or propose a new change" % change_id)
    if not args.allow_multi:
        for other in accepted:
            if changes.get(other, {}).get("target_id") == change.get("target_id"):
                raise RevisionError(
                    "target %s already has accepted change %s; reject it first or use "
                    "--allow-multi" % (change.get("target_id"), other))
    new_accepted = sorted(set(accepted) | {change_id})
    version = add_version(state, "accept %s" % change_id, new_accepted, rejected,
                          reason=change.get("claim") or "")
    change["status"] = "accepted"
    change["decided_at"] = version["created_at"]
    state["last_rollback"] = None
    save_state(work, state)
    append_receipt(work, "accept", True, {
        "change_id": change_id,
        "target_id": change.get("target_id"),
        "version": version["version"],
        "accepted": new_accepted,
    })
    return emit({
        "ok": True,
        "change_id": change_id,
        "version": version["version"],
        "accepted": new_accepted,
        "rejected": rejected,
    }, args.json, ["accepted %s -> %s" % (change_id, version["version"])])


def cmd_reject(args):
    work = Path(args.work)
    state = load_state(work)
    change_id = args.change_id
    reason = str(args.reason or "").strip()
    if not reason:
        raise RevisionError("reject requires --reason so the decision is traceable")
    changes = state.get("changes") or {}
    change = changes.get(change_id)
    if change is None:
        raise RevisionError("unknown change id: %s" % change_id)
    accepted, rejected = effective_lists(state)
    if change_id in rejected:
        raise RevisionError("change %s is already rejected" % change_id)
    if change_id in accepted:
        raise RevisionError("change %s is accepted; rollback before rejecting it" % change_id)
    new_rejected = sorted(set(rejected) | {change_id})
    version = add_version(state, "reject %s" % change_id, accepted, new_rejected,
                          reason=reason)
    change["status"] = "rejected"
    change["decided_at"] = version["created_at"]
    change["reason"] = reason
    state["last_rollback"] = None
    save_state(work, state)
    append_receipt(work, "reject", True, {
        "change_id": change_id,
        "target_id": change.get("target_id"),
        "reason": reason,
        "version": version["version"],
        "rejected": new_rejected,
    })
    return emit({
        "ok": True,
        "change_id": change_id,
        "reason": reason,
        "version": version["version"],
        "accepted": accepted,
        "rejected": new_rejected,
    }, args.json, ["rejected %s -> %s" % (change_id, version["version"])])


def cmd_status(args):
    work = Path(args.work)
    state = load_state(work)
    accepted, rejected = effective_lists(state)
    receipts = load_receipts(work)
    changes = state.get("changes") or {}
    next_actions = []
    if not state.get("targets"):
        next_actions.append("no workorder targets; preserve-only export will be partial")
    for target in state.get("targets") or []:
        if not target.get("found"):
            next_actions.append("resolve blocked target %s" % target.get("target_id"))
    for cid in sorted(changes):
        if changes[cid].get("status") == "proposed":
            next_actions.append("accept or reject %s" % cid)
    if accepted and not any(receipt.get("operation") == "export" for receipt in receipts):
        next_actions.append("export the revised manuscript")
    if not any(receipt.get("operation") == "verify" for receipt in receipts):
        next_actions.append("run verify after export")
    if not next_actions:
        next_actions.append("review result.json, revised.md and preview.html")
    payload = {
        "ok": True,
        "case_id": state.get("case_id"),
        "workorder_id": (state.get("workorder") or {}).get("id"),
        "current_version": state.get("current_version"),
        "versions": [{
            "version": item.get("version"),
            "label": item.get("label"),
            "accepted": item.get("accepted"),
            "rejected": item.get("rejected"),
            "created_at": item.get("created_at"),
        } for item in state.get("versions") or []],
        "accepted": accepted,
        "rejected": rejected,
        "changes": [{
            "change_id": cid,
            "target_id": changes[cid].get("target_id"),
            "status": changes[cid].get("status"),
            "annotation_type": changes[cid].get("annotation_type"),
            "quote_verified": changes[cid].get("quote_verified"),
        } for cid in sorted(changes)],
        "targets": [{
            "target_id": target.get("target_id"),
            "found": target.get("found"),
            "reason": target.get("reason"),
        } for target in state.get("targets") or []],
        "receipts": {"total": len(receipts),
                     "operations": sorted({receipt.get("operation") for receipt in receipts})},
        "next_actions": next_actions,
    }
    return emit(payload, args.json, [
        "case: %s, version: %s" % (state.get("case_id"), state.get("current_version")),
        "accepted: %s" % (", ".join(accepted) or "none"),
        "rejected: %s" % (", ".join(rejected) or "none"),
        "proposed: %s" % (", ".join(cid for cid in sorted(changes)
                                    if changes[cid].get("status") == "proposed") or "none"),
        "next: %s" % "; ".join(next_actions),
    ])

# ---------------------------------------------------------------------------
# commands: compare / rollback / reapply / invalidate / export / verify
# ---------------------------------------------------------------------------
def cmd_compare(args):
    work = Path(args.work)
    state = load_state(work)
    from_version = find_version(state, args.from_version)
    to_version = find_version(state, args.to_version)
    from_text = compose_text(state, from_version.get("accepted") or [])
    to_text = compose_text(state, to_version.get("accepted") or [])
    diff = "\n".join(difflib.unified_diff(
        from_text.splitlines(), to_text.splitlines(),
        fromfile="%s (%s)" % (from_version["version"], from_version.get("label")),
        tofile="%s (%s)" % (to_version["version"], to_version.get("label")),
        lineterm=""))
    out = workspace_paths(work)["versions"] / (
        "compare-%s-%s.diff" % (from_version["version"], to_version["version"]))
    write_text(out, diff + "\n")
    added = sorted(set(to_version.get("accepted") or [])
                   - set(from_version.get("accepted") or []))
    removed = sorted(set(from_version.get("accepted") or [])
                     - set(to_version.get("accepted") or []))
    rejected_added = sorted(set(to_version.get("rejected") or [])
                            - set(from_version.get("rejected") or []))
    rejected_removed = sorted(set(from_version.get("rejected") or [])
                              - set(to_version.get("rejected") or []))
    state_sha = file_sha(workspace_paths(work)["state"])
    append_receipt(work, "compare", True, {
        "from": from_version["version"],
        "to": to_version["version"],
        "diff_path": rel_to(out, work),
        "added": added,
        "removed": removed,
        "rejected_added": rejected_added,
        "rejected_removed": rejected_removed,
        "state_sha256_before": state_sha,
        "state_sha256_after": state_sha,
        "state_unchanged": True,
        "diff_lines": len(diff.splitlines()),
    })
    return emit({
        "ok": True,
        "from": from_version["version"],
        "to": to_version["version"],
        "added": added,
        "removed": removed,
        "rejected_added": rejected_added,
        "rejected_removed": rejected_removed,
        "diff_path": rel_to(out, work),
        "diff_lines": len(diff.splitlines()),
    }, args.json, [
        "compare %s -> %s" % (from_version["version"], to_version["version"]),
        "added: %s; removed: %s" % (", ".join(added) or "none",
                                    ", ".join(removed) or "none"),
        "diff: %s" % out,
    ])


def cmd_rollback(args):
    work = Path(args.work)
    state = load_state(work)
    versions = state.get("versions") or []
    current = current_version(state)
    index = next((i for i, item in enumerate(versions)
                  if item.get("version") == current.get("version")), 0)
    if args.to:
        target = find_version(state, args.to)
    else:
        if index == 0:
            raise RevisionError("already at baseline v0; nothing to roll back")
        target = versions[index - 1]
    if target.get("version") == current.get("version"):
        raise RevisionError("rollback target equals current version %s" % current.get("version"))
    state["last_rollback"] = {
        "from": current.get("version"),
        "to": target.get("version"),
        "at": now_iso(),
        "reason": args.reason or "",
    }
    state.setdefault("rollback_history", []).append({
        "action": "rollback",
        "from": current.get("version"),
        "to": target.get("version"),
        "at": now_iso(),
        "reason": args.reason or "",
    })
    state["current_version"] = target.get("version")
    apply_version_statuses(state)
    save_state(work, state)
    accepted, rejected = effective_lists(state)
    append_receipt(work, "rollback", True, {
        "from": current.get("version"),
        "to": target.get("version"),
        "accepted": accepted,
        "rejected": rejected,
        "reason": args.reason or "",
    })
    return emit({
        "ok": True,
        "from": current.get("version"),
        "to": target.get("version"),
        "accepted": accepted,
        "rejected": rejected,
        "next": "reapply to restore the version, or continue from this version",
    }, args.json, ["rolled back %s -> %s" % (current.get("version"), target.get("version"))])


def cmd_reapply(args):
    work = Path(args.work)
    state = load_state(work)
    last = state.get("last_rollback")
    if args.to:
        target = find_version(state, args.to)
    elif last:
        target = find_version(state, last.get("from"))
    else:
        raise RevisionError("no previous rollback to reapply")
    current = current_version(state)
    if target.get("version") == current.get("version"):
        raise RevisionError("reapply target equals current version %s" % current.get("version"))
    state["rollback_history"].append({
        "action": "reapply",
        "from": current.get("version"),
        "to": target.get("version"),
        "at": now_iso(),
        "reason": args.reason or "",
    })
    state["current_version"] = target.get("version")
    state["last_rollback"] = None
    apply_version_statuses(state)
    save_state(work, state)
    accepted, rejected = effective_lists(state)
    append_receipt(work, "reapply", True, {
        "from": current.get("version"),
        "to": target.get("version"),
        "accepted": accepted,
        "rejected": rejected,
        "reason": args.reason or "",
    })
    return emit({
        "ok": True,
        "from": current.get("version"),
        "to": target.get("version"),
        "accepted": accepted,
        "rejected": rejected,
    }, args.json, ["reapplied %s -> %s" % (current.get("version"), target.get("version"))])


def cmd_invalidate(args):
    work = Path(args.work)
    state_path = workspace_paths(work)["state"]
    before = state_path.read_bytes()
    state = load_state(work)
    accepted, _rejected = effective_lists(state)
    changes = state.get("changes") or {}
    attempted = args.operation
    error_message = ""
    try:
        if attempted == "accept-unknown":
            cid = args.change_id or "E9999"
            if cid in changes:
                raise RevisionError("guard expected an unknown change id; %s exists" % cid)
            raise RevisionError("unknown change id: %s" % cid)
        if attempted == "reject-unproposed":
            cid = args.change_id or (accepted[0] if accepted else "E9999")
            if cid not in changes:
                raise RevisionError("unknown change id: %s" % cid)
            status = changes[cid].get("status")
            if status == "proposed":
                raise RevisionError("guard expected a decided change; %s is proposed" % cid)
            raise RevisionError("change %s is %s; reject is illegal without rollback"
                                % (cid, status))
        if attempted == "propose-missing-target":
            target = args.target or "C99-P999"
            if any(item.get("target_id") == target and item.get("found")
                   for item in state.get("targets") or []):
                raise RevisionError("guard expected a missing target; %s exists" % target)
            raise RevisionError("target not found in the supplied chapters: %s" % target)
        if attempted == "duplicate-accept":
            cid = args.change_id or (accepted[0] if accepted else "E9999")
            if cid not in changes:
                raise RevisionError("unknown change id: %s" % cid)
            if cid not in accepted:
                raise RevisionError("change %s is not accepted; duplicate accept is not "
                                    "the chosen fault" % cid)
            raise RevisionError("change %s is already accepted" % cid)
        raise RevisionError("unsupported fault injection: %s" % attempted)
    except RevisionError as exc:
        error_message = str(exc)
    after = state_path.read_bytes()
    unchanged = before == after
    restored = False
    if not unchanged:
        state_path.write_bytes(before)
        restored = True
    payload = {
        "ok": unchanged,
        "controlled_exception": True,
        "fault_injection": True,
        "attempted_operation": attempted,
        "error_type": "RevisionError",
        "error_message": error_message,
        "state_sha256_before": sha_bytes(before),
        "state_sha256_after": sha_bytes(after),
        "state_unchanged": unchanged,
        "state_restored_from_snapshot": restored,
        "at": now_iso(),
        "note": "This is a deliberate fault injection required by the workorder; "
                "it is not a remote platform failure.",
    }
    dump_json(workspace_paths(work)["invalid_receipt"], payload)
    append_receipt(work, "invalidate", unchanged, {
        "fault_injection": True,
        "attempted_operation": attempted,
        "error_type": payload["error_type"],
        "error_message": error_message,
        "state_sha256_before": payload["state_sha256_before"],
        "state_sha256_after": payload["state_sha256_after"],
        "state_unchanged": unchanged,
        "state_restored_from_snapshot": restored,
    }, exit_code=EXIT_OK if unchanged else EXIT_FAIL)
    return emit(payload, args.json, [
        "controlled invalid operation: %s" % attempted,
        "error: %s" % error_message,
        "state unchanged: %s" % unchanged,
    ]) if unchanged else EXIT_FAIL


def cmd_export(args):
    work = Path(args.work)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(work)
    revised_text = compose_text(state)
    write_text(out_dir / "revised.md", revised_text)
    preview_error = None
    try:
        preview_text = render_html(state, verify_state(state, out_dir=None,
                                                       revised_text=revised_text),
                                   revised_text)
    except Exception as exc:  # noqa: BLE001 - preview must not block delivery
        preview_error = "%s: %s" % (type(exc).__name__, exc)
        preview_text = ("<!doctype html><html><head><meta charset=\"utf-8\">"
                        "<title>Revision preview</title></head><body>"
                        "<h1>Revision preview</h1><p>HTML renderer failed; "
                        "plain text follows.</p><pre>%s</pre></body></html>\n"
                        % html.escape(revised_text))
    write_text(out_dir / "preview.html", preview_text)
    verification = verify_state(state, out_dir=out_dir, revised_text=revised_text)
    dump_json(out_dir / "verify-report.json", verification)
    append_receipt(work, "verify", verification.get("ok"), {
        "auto_during_export": True,
        "ok": verification.get("ok"),
        "failed_checks": [check.get("id") for check in verification.get("checks") or []
                          if not check.get("ok")],
        "output": str(out_dir / "verify-report.json"),
    }, exit_code=EXIT_OK if verification.get("ok") else EXIT_FAIL)
    original_records = build_original_records(state)
    dump_json(out_dir / "original-records.json", original_records)
    paths = workspace_paths(work)
    for name, source in (("import-receipt.json", paths["import_receipt"]),
                         ("edit-plan.json", paths["plan"])):
        if Path(source).is_file():
            shutil.copy2(source, out_dir / name)
    if preview_error:
        state.setdefault("agent_notes", {}).setdefault("limitations", []).append(
            "preview_render_failed: %s" % preview_error)
        save_state(work, state)
    artifacts = [
        {"role": "editable", "path": "revised.md", "format": "markdown"},
        {"role": "preview", "path": "preview.html", "format": "html"},
        {"role": "revision_record", "path": "revision-record.json", "format": "json"},
        {"role": "original_record", "path": "original-records.json", "format": "json"},
        {"role": "import_receipt", "path": "import-receipt.json", "format": "json"},
        {"role": "plan", "path": "edit-plan.json", "format": "json"},
        {"role": "verify", "path": "verify-report.json", "format": "json"},
    ]
    if args.copy_tool:
        source_tool = Path(__file__)
        if source_tool.is_file():
            target_tool = out_dir / "revision_flow.py"
            shutil.copy2(source_tool, target_tool)
            artifacts.append({"role": "workflow", "path": "revision_flow.py",
                              "format": "python"})
    status = overall_status(state, verification)
    append_receipt(work, "export", True, {
        "out": str(out_dir),
        "status": status,
        "verification_ok": verification.get("ok"),
        "artifacts": [item["path"] for item in artifacts],
        "current_version": state.get("current_version"),
        "preview_render_failed": preview_error,
    })
    receipts = load_receipts(work)
    record = build_revision_record(state, verification, artifacts, receipts)
    dump_json(out_dir / "revision-record.json", record)
    result_path = Path(args.result) if args.result else out_dir / "result.json"
    result = None
    if not args.no_result:
        result = build_result(state, verification, artifacts, receipts, revised_text)
        result["artifacts"] = artifacts
        dump_json(result_path, result)
    return emit({
        "ok": True,
        "case_id": state.get("case_id"),
        "status": status,
        "current_version": state.get("current_version"),
        "out": str(out_dir),
        "result": str(result_path) if result is not None else None,
        "revised": str(out_dir / "revised.md"),
        "preview": str(out_dir / "preview.html"),
        "revision_record": str(out_dir / "revision-record.json"),
        "verification": verification.get("ok"),
        "accepted": effective_lists(state)[0],
        "rejected": effective_lists(state)[1],
        "artifacts": [item["path"] for item in artifacts],
    }, args.json, [
        "exported %s (%s)" % (state.get("case_id"), status),
        "manuscript: %s" % (out_dir / "revised.md"),
        "preview: %s" % (out_dir / "preview.html"),
        "record: %s" % (out_dir / "revision-record.json"),
        "verification: %s" % ("ok" if verification.get("ok") else "failed"),
    ])


def cmd_verify(args):
    work = Path(args.work)
    state = load_state(work)
    revised_text = compose_text(state)
    out_dir = Path(args.out) if args.out else None
    verification = verify_state(state, out_dir=out_dir, revised_text=revised_text)
    report_path = (out_dir / "verify-report.json") if out_dir else (work / "verify-report.json")
    dump_json(report_path, verification)
    append_receipt(work, "verify", verification.get("ok"), {
        "ok": verification.get("ok"),
        "failed_checks": [check.get("id") for check in verification.get("checks") or []
                          if not check.get("ok")],
        "output": str(report_path),
        "auto_during_export": False,
    }, exit_code=EXIT_OK if verification.get("ok") else EXIT_FAIL)
    payload = {
        "ok": verification.get("ok"),
        "verification": verification,
        "report": str(report_path),
    }
    emit(payload, args.json, [
        "verification: %s" % ("ok" if verification.get("ok") else "failed"),
        "report: %s" % report_path,
    ] + ["failed: %s" % check.get("id") for check in verification.get("checks") or []
         if not check.get("ok")])
    return EXIT_OK if verification.get("ok") else EXIT_FAIL


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def add_common(parser, with_input=False):
    if with_input:
        parser.add_argument("--input", default="/input")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--json", action="store_true")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Source-preserving revision workflow (stdlib only).")
    parser.add_argument("--version", action="version", version="revision_flow %s" % VERSION)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="import the case, build baseline and import receipt")
    add_common(p_init, with_input=True)
    p_init.add_argument("--force", action="store_true", help="reset the work directory")
    p_init.set_defaults(func=cmd_init)

    p_import = sub.add_parser("import", help="alias for init; import baseline and receipt")
    add_common(p_import, with_input=True)
    p_import.add_argument("--force", action="store_true")
    p_import.set_defaults(func=cmd_init)

    p_plan = sub.add_parser("plan", help="read the organizer workorder and write the edit plan")
    add_common(p_plan)
    p_plan.set_defaults(func=cmd_plan)

    p_propose = sub.add_parser("propose", help="propose annotation changes without writing them back")
    add_common(p_propose)
    p_propose.add_argument("--spec", default=None,
                           help="annotations JSON (default: work/annotations.json)")
    p_propose.add_argument("--target", default=None)
    p_propose.add_argument("--annotation", default=None)
    p_propose.add_argument("--basis-quote", dest="basis_quote", default=None)
    p_propose.add_argument("--locator", default=None)
    p_propose.add_argument("--claim", default=None)
    p_propose.add_argument("--type", dest="type", default="clarification")
    p_propose.add_argument("--uncertainty", default=None)
    p_propose.add_argument("--change-id", dest="change_id", default=None)
    p_propose.add_argument("--allow-unverified-quote", dest="allow_unverified_quote",
                           action="store_true")
    p_propose.set_defaults(func=cmd_propose)

    p_accept = sub.add_parser("accept", help="accept one proposed change")
    p_accept.add_argument("change_id")
    add_common(p_accept)
    p_accept.add_argument("--allow-multi", dest="allow_multi", action="store_true")
    p_accept.set_defaults(func=cmd_accept)

    p_reject = sub.add_parser("reject", help="reject one proposed change with a reason")
    p_reject.add_argument("change_id")
    add_common(p_reject)
    p_reject.add_argument("--reason", required=True)
    p_reject.set_defaults(func=cmd_reject)

    p_status = sub.add_parser("status", help="show versions, decisions and next actions")
    add_common(p_status)
    p_status.set_defaults(func=cmd_status)

    p_compare = sub.add_parser("compare", help="compare two real versions with a unified diff")
    add_common(p_compare)
    p_compare.add_argument("--from", dest="from_version", required=True)
    p_compare.add_argument("--to", dest="to_version", required=True)
    p_compare.set_defaults(func=cmd_compare)

    p_rollback = sub.add_parser("rollback", help="roll back to a previous version")
    add_common(p_rollback)
    p_rollback.add_argument("--to", default=None)
    p_rollback.add_argument("--reason", default="")
    p_rollback.set_defaults(func=cmd_rollback)

    p_reapply = sub.add_parser("reapply", help="reapply the version removed by the last rollback")
    add_common(p_reapply)
    p_reapply.add_argument("--to", default=None)
    p_reapply.add_argument("--reason", default="")
    p_reapply.set_defaults(func=cmd_reapply)

    p_invalidate = sub.add_parser(
        "invalidate", help="deliberately trigger an illegal operation; verify state is unchanged")
    add_common(p_invalidate)
    p_invalidate.add_argument("--operation", default="accept-unknown",
                              choices=("accept-unknown", "reject-unproposed",
                                       "propose-missing-target", "duplicate-accept"))
    p_invalidate.add_argument("--change-id", dest="change_id", default=None)
    p_invalidate.add_argument("--target", default=None)
    p_invalidate.set_defaults(func=cmd_invalidate)

    p_export = sub.add_parser("export", help="write revised.md, preview.html, records and result.json")
    add_common(p_export)
    p_export.add_argument("--out", default="/app/output")
    p_export.add_argument("--result", default=None)
    p_export.add_argument("--no-result", dest="no_result", action="store_true")
    p_export.add_argument("--no-copy-tool", dest="copy_tool", action="store_false")
    p_export.set_defaults(copy_tool=True)
    p_export.set_defaults(func=cmd_export)

    p_verify = sub.add_parser("verify", help="verify original blocks, targets, citations, tables")
    add_common(p_verify)
    p_verify.add_argument("--out", default=None)
    p_verify.set_defaults(func=cmd_verify)

    return parser


def main(argv=None):
    setup_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RevisionError as exc:
        payload = {"ok": False, "status": "blocked", "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return getattr(exc, "exit_code", EXIT_BLOCKED)
    except (OSError, json.JSONDecodeError) as exc:
        payload = {"ok": False, "status": "input_error", "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
