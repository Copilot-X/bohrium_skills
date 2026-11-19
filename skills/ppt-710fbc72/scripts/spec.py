#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spec: input discovery, workorder normalisation and real layout planning.

This module never guesses layout names from habit: every mapping is selected
from ``deckkit.audit_template`` results for the actual template supplied with
the case.  Content itself is written by the model into ``deck-spec.json``; no
extractive fallback is labelled completed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import deckkit

KINDS = {"pptx", "potx"}
BUILD_MODES = {"build", "generate", "new", "content", "template+content",
               "template_content", "compose"}
ADAPT_MODES = {"adapt", "retarget", "edit", "starting-deck", "starting_deck"}
ROLE_ALIASES = {
    "cover": "title", "title-slide": "title", "opening": "title",
    "agenda": "section", "chapter": "section", "part": "section",
    "bullets": "content", "text": "content", "body": "content",
    "two_column": "two-column", "twocolumn": "two-column",
    "comparison": "two-column", "image": "figure", "picture": "figure",
    "photo": "figure", "chart": "figure", "conclusion": "closing",
    "end": "closing", "thank-you": "closing", "thanks": "closing",
}


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _resolve(ref, input_dir):
    if not ref or not isinstance(ref, str):
        return None
    ref = ref.strip()
    if not ref:
        return None
    path = Path(ref)
    candidates = [path] if path.is_absolute() else [
        input_dir / path, input_dir.parent / path, Path.cwd() / path,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    hits = [p for p in input_dir.rglob(path.name) if p.is_file()]
    if hits:
        return sorted(hits, key=lambda p: len(str(p)))[0].resolve()
    return None


def _json_with_keys(files, keys):
    for path in files:
        data = read_json(path)
        if isinstance(data, dict) and any(key in data for key in keys):
            return path, data
    return None, None


def _pick_deck_file(files, include, exclude):
    hits = []
    for path in files:
        if path.suffix.lower().lstrip(".") not in KINDS:
            continue
        name = path.name.lower()
        if any(token in name for token in exclude):
            continue
        if any(token in name for token in include):
            hits.append(path)
    if not hits:
        return None
    return sorted(hits, key=lambda p: (len(str(p)), str(p)))[0].resolve()


def discover(input_dir, work_dir=None, spec_arg=None):
    input_dir = Path(input_dir)
    if input_dir.is_file():
        input_dir = input_dir.parent
    input_dir = input_dir.resolve()
    work_dir = Path(work_dir).resolve() if work_dir else None
    files = sorted(p for p in input_dir.rglob("*") if p.is_file()) \
        if input_dir.is_dir() else []
    json_files = [p for p in files if p.suffix.lower() == ".json"]
    result = {"input_dir": str(input_dir), "files": [str(p) for p in files],
              "warnings": []}

    case_path = input_dir / "case.json"
    if not case_path.is_file():
        case_path, _ = _json_with_keys(json_files, ("case_id", "output_contract"))
    if case_path is None:
        case_path, _ = _json_with_keys(json_files, ("source_materials",))
    case = read_json(case_path) if case_path else {}
    case = case if isinstance(case, dict) else {}
    if isinstance(case.get("input"), dict):
        merged = dict(case)
        merged.update(case["input"])
        case = merged
    result.update({"case_path": str(case_path) if case_path else None,
                   "case": case,
                   "task_path": str(input_dir / "task.md")
                   if (input_dir / "task.md").is_file() else None,
                   "task_text": read_text(input_dir / "task.md")})

    workorder_candidates = [
        input_dir / "artifacts" / "slide-workorder.json",
        input_dir / "slide-workorder.json",
        input_dir / "workorder.json",
    ]
    workorder_candidates.extend(sorted(input_dir.glob("artifacts/*workorder*.json")))
    workorder_candidates.extend(sorted(input_dir.glob("*workorder*.json")))
    if work_dir:
        workorder_candidates.append(work_dir / "workorder.json")
    workorder_path, workorder = None, {}
    for candidate in workorder_candidates:
        if candidate.is_file():
            data = read_json(candidate)
            if isinstance(data, dict):
                workorder_path, workorder = candidate, data
                break
    if isinstance(workorder.get("workorder"), dict):
        workorder = workorder["workorder"]
    result.update({"workorder_path": str(workorder_path) if workorder_path else None,
                   "workorder_raw": workorder})

    template_ref = (workorder.get("target_template") or workorder.get("template")
                    or workorder.get("template_path") or case.get("template")
                    or case.get("target_template"))
    deck_ref = (workorder.get("starting_deck") or workorder.get("start_deck")
                or workorder.get("deck") or case.get("starting_deck")
                or case.get("startingDeck") or case.get("deck"))
    template = _resolve(template_ref, input_dir)
    deck = _resolve(deck_ref, input_dir)
    if template is None:
        template = _pick_deck_file(
            files, ("template", "master", "母版", "target"),
            ("starting", "start-deck", "source-deck", "input-deck"))
    if deck is None:
        deck = _pick_deck_file(
            files, ("starting", "start-deck", "source-deck", "input-deck"),
            ("template", "master", "target"))
    if deck is None and template is not None:
        others = [p for p in files if p.suffix.lower().lstrip(".") in KINDS
                  and p.resolve() != template]
        if len(others) == 1:
            deck = others[0].resolve()
    if template is None and deck is not None:
        others = [p for p in files if p.suffix.lower().lstrip(".") in KINDS
                  and p.resolve() != deck]
        if len(others) == 1:
            template = others[0].resolve()
    result.update({"template": str(template) if template else None,
                   "deck": str(deck) if deck else None})

    spec_candidates = []
    if spec_arg:
        spec_candidates.append(Path(spec_arg))
    if work_dir:
        spec_candidates.extend([work_dir / "deck-spec.json",
                                work_dir / "deck_spec.json"])
    spec_candidates.extend([input_dir / "deck-spec.json",
                            input_dir / "artifacts" / "deck-spec.json"])
    spec = None
    for candidate in spec_candidates:
        if candidate.is_file():
            data = read_json(candidate)
            if isinstance(data, dict) and "slides" in data:
                spec = candidate.resolve()
                break
    result.update({"spec": str(spec) if spec else None,
                   "spec_candidates": [str(p) for p in spec_candidates]})

    materials = []
    for key in ("source_materials", "materials", "sources", "inputs"):
        values = workorder.get(key) or case.get(key) or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            path = _resolve(value, input_dir)
            if path:
                materials.append(str(path))
    for path in files:
        if path.suffix.lower() in (".pdf", ".docx", ".txt", ".csv", ".tsv",
                                   ".md", ".json", ".pptx"):
            if path.name.lower() in ("case.json", "task.md") or path.name.endswith(
                    ("workorder.json", "deck-spec.json")):
                continue
            materials.append(str(path))
    result["materials"] = sorted(set(materials))
    return result


def normalize_workorder(discovery):
    raw = discovery.get("workorder_raw") or {}
    case = discovery.get("case") or {}
    deck = discovery.get("deck")
    spec = discovery.get("spec")
    mode = str(raw.get("mode") or raw.get("operation") or "").strip().lower()
    if mode in ("template+content", "template_content"):
        mode = "build"
    if mode not in BUILD_MODES | ADAPT_MODES:
        if deck:
            mode = "adapt"
        elif spec:
            mode = "build"
        else:
            mode = "build"
    case_id = (raw.get("case_id") or raw.get("id") or case.get("case_id")
               or case.get("id") or Path(discovery["input_dir"]).name)
    edits = raw.get("edits") or raw.get("edit_requests") or []
    if not isinstance(edits, list):
        edits = []
    normalized = {
        "case_id": str(case_id),
        "mode": "adapt" if mode in ADAPT_MODES else "build",
        "target_template": discovery.get("template"),
        "starting_deck": discovery.get("deck"),
        "slide_count": raw.get("slide_count") or raw.get("slides")
        or case.get("slide_count"),
        "audience": raw.get("audience") or case.get("audience"),
        "duration_min": raw.get("duration_min") or raw.get("duration")
        or case.get("duration_min"),
        "language": raw.get("language") or case.get("language"),
        "edits": edits,
        "preserve": raw.get("preserve") or [],
        "required_points": raw.get("required_points")
        or case.get("required_points") or [],
        "output_contract": raw.get("output_contract")
        or case.get("output_contract") or [],
        "layout_map": raw.get("layout_map") or [],
        "slides": (raw.get("slides") if isinstance(raw.get("slides"), list)
                   else case.get("slides") if isinstance(case.get("slides"), list)
                   else []),
        "source_materials": raw.get("source_materials")
        or case.get("source_materials") or [],
        "sources": discovery.get("materials") or [],
        "original": raw,
        "warnings": [],
    }
    if normalized["slide_count"] is not None:
        try:
            normalized["slide_count"] = int(normalized["slide_count"])
        except (TypeError, ValueError):
            normalized["slide_count"] = None
    return normalized


def constraints_from(discovery, workorder):
    case = discovery.get("case") or {}
    task_text = discovery.get("task_text") or ""
    text = task_text + "\n" + json.dumps(case, ensure_ascii=False)
    lang = (workorder.get("language")
            or ("zh-CN" if re.search(r"[一-鿿]", text) else "en"))
    body_floor = 18
    match = re.search(r"(?:正文|body|正文字号)[^\d]{0,16}(\d{2})", task_text,
                      re.I)
    if match:
        body_floor = max(12, min(28, int(match.group(1))))
    title_floor = 24
    match = re.search(r"(?:标题|title)[^\d]{0,16}(\d{2})", task_text, re.I)
    if match:
        title_floor = max(body_floor + 4, min(48, int(match.group(1))))
    title = (workorder.get("deck_title") or case.get("deck_title")
             or workorder.get("title") or case.get("title")
             or case.get("title_zh") or "学术汇报")
    return {
        "case_id": workorder.get("case_id"),
        "slide_count": workorder.get("slide_count"),
        "audience": workorder.get("audience") or "",
        "duration_min": workorder.get("duration_min"),
        "language": lang,
        "title": str(title).strip() or "学术汇报",
        "body_size_min": body_floor,
        "title_size_min": title_floor,
        "caption_size": 12, "footer_size": 10,
        "output_contract": workorder.get("output_contract") or [],
        "required_points": workorder.get("required_points") or [],
        "edits": workorder.get("edits") or [],
        "preserve": workorder.get("preserve") or [],
    }


def normalize_role(value):
    role = str(value or "content").strip().lower().replace(" ", "-")
    return ROLE_ALIASES.get(role, role if role in (
        "title", "section", "content", "two-column", "figure", "quote",
        "closing") else "content")


def infer_deck_roles(deck_path):
    parts = deckkit.read_parts(deck_path)
    layouts = {layout["part"]: layout
               for layout in deckkit.parse_layouts(parts)}
    entries = []
    slides = deckkit.ordered_slides(parts)
    for index, part in enumerate(slides, 1):
        layout_part = deckkit.slide_layout(parts, part)
        layout = layouts.get(layout_part)
        title = ""
        texts = []
        if part in parts:
            root = deckkit.parse_xml(parts[part], part)
            for sp in root.findall(".//p:sp", deckkit.NS):
                ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", deckkit.NS)
                text = deckkit.xml_text(sp).strip()
                if not text:
                    continue
                texts.append(text)
                if ph is not None and (ph.get("type") or "") in deckkit.TITLE_TYPES:
                    title = text
        role = deckkit.role_from_layout(layout) if layout else "content"
        if index == 1 and role not in ("section",):
            role = "title"
        if layout and "section" in (layout.get("name") or "").lower():
            role = "section"
        short = len(texts) <= 5 and all(len(t) < 90 for t in texts)
        if index > 1 and short and title and re.match(
                r"^\s*(0?\d+[\.、\s]|[一二三四五六七八九十]+[、\.．]|"
                r"(Part|Section|Chapter)\s+\d|[0-9]{1,2}\s+)", title, re.I):
            role = "section"
        if index == len(slides) and any(
                token in " ".join(texts).lower() for token in
                ("谢谢", "致谢", "结论", "总结", "q&a", "questions", "thank")):
            role = "closing"
        if layout and "conclusion" in (layout.get("name") or "").lower():
            role = "closing"
        if index not in (1, len(slides)) and role in ("title", "closing"):
            role = "content"
        entries.append({"slide": index, "role": role,
                        "layout": layout.get("name") if layout else "",
                        "previous_layout_part": layout_part,
                        "title": title})
    return entries


def _resolve_image(value, base_dirs):
    if not value or not isinstance(value, str):
        return value
    path = Path(value)
    if path.is_absolute() and path.is_file():
        return str(path)
    for base in base_dirs:
        candidate = Path(base) / path
        if candidate.is_file():
            return str(candidate.resolve())
    return value


def validate_deck_spec(raw, base_dirs):
    if isinstance(raw, list):
        raw = {"slides": raw}
    if not isinstance(raw, dict):
        return {"deck_title": "", "slides": [], "warnings": ["spec is not an object"]}
    slides_raw = raw.get("slides") or raw.get("pages") or []
    warnings, slides = [], []
    for index, item in enumerate(slides_raw, 1):
        if not isinstance(item, dict):
            warnings.append(f"slide {index} ignored: not an object")
            continue
        role = normalize_role(item.get("role") or item.get("kind")
                              or item.get("type"))
        bullets = item.get("bullets")
        if bullets is None:
            bullets = item.get("body") or item.get("points") or []
        if isinstance(bullets, str):
            bullets = [line for line in bullets.split("\n") if line.strip()]
        clean = []
        for bullet in bullets or []:
            if isinstance(bullet, dict):
                text = str(bullet.get("text") or bullet.get("bullet") or "")
                if text:
                    clean.append(dict(bullet, text=text))
            else:
                text = str(bullet)
                if text.strip():
                    clean.append(text)
        right = item.get("bullets_right") or item.get("right") or []
        if isinstance(right, str):
            right = [line for line in right.split("\n") if line.strip()]
        slide = dict(item)
        slide.update({
            "role": role,
            "title": str(item.get("title") or item.get("heading") or ""),
            "subtitle": str(item.get("subtitle") or item.get("subhead") or ""),
            "bullets": clean,
            "bullets_right": right,
            "image": _resolve_image(item.get("image") or item.get("figure"),
                                    base_dirs),
            "caption": str(item.get("caption") or item.get("figure_caption") or ""),
            "notes": str(item.get("notes") or item.get("speaker_notes") or ""),
            "source": str(item.get("source") or item.get("source_location")
                          or item.get("locator") or ""),
            "claims": item.get("claims") or [],
            "layout": item.get("layout") or item.get("layout_name"),
            "footer": str(item.get("footer") or ""),
        })
        if role == "content" and slide["image"] and not slide["bullets"]:
            slide["role"] = "figure"
        slides.append(slide)
    return {"deck_title": str(raw.get("title") or raw.get("deck_title") or ""),
            "source": str(raw.get("source") or ""),
            "slides": slides, "warnings": warnings}


def choose_role_layouts(audit, roles):
    layouts = audit["layouts"]
    cfg = {}
    for role in roles:
        layout, fallback = deckkit.choose_layout(layouts, role)
        if layout:
            cfg[role] = {"layout": layout["name"], "layout_part": layout["part"],
                         "fallback": fallback}
    return cfg


def plan_from_spec(audit, spec, constraints):
    layouts = audit["layouts"]
    body_size = max(int(constraints.get("body_size_min", 18)), 18)
    title_size = max(32, int(constraints.get("title_size_min", 24)),
                     body_size + 6)
    mapping, warnings, uncertainty = [], [], []
    roles = {}
    for index, slide in enumerate(spec["slides"], 1):
        role = slide.get("role") or "content"
        wanted = slide.get("layout")
        layout, fallback = deckkit.choose_layout(layouts, role, wanted)
        if layout is None:
            layout, fallback = deckkit.choose_layout(layouts, "content")
        if layout is None:
            raise ValueError(f"slide {index}: template has no usable layout")
        if fallback:
            warnings.append(f"slide {index}: {fallback}")
            uncertainty.append({"slide": index, "issue": "layout_fallback",
                                "detail": fallback})
        slide_title_size = int(slide.get("title_size") or title_size)
        slide_body_size = int(slide.get("body_size") or body_size)
        if slide_title_size <= slide_body_size:
            old = slide_title_size
            slide_title_size = slide_body_size + 6
            warnings.append(
                f"slide {index}: title size {old}pt <= body {slide_body_size}pt; "
                f"raised to {slide_title_size}pt for hierarchy")
            uncertainty.append({"slide": index, "issue": "title_hierarchy_adjusted",
                                "detail": f"{old} -> {slide_title_size}"})
        slide["title_size"] = slide_title_size
        slide["body_size"] = slide_body_size
        roles.setdefault(role, {"layout": layout["name"],
                                "layout_part": layout["part"],
                                "title_size": slide_title_size,
                                "body_size": slide_body_size})
        mapping.append({"slide": index, "role": role,
                        "layout": layout["name"],
                        "layout_part": layout["part"],
                        "title": slide.get("title", ""),
                        "source": slide.get("source", ""),
                        "notes_present": bool(slide.get("notes")),
                        "fallback": fallback})
    return mapping, roles, warnings, uncertainty


def plan_from_deck(audit, deck_path, workorder):
    layouts = audit["layouts"]
    by_name = {layout["name"].strip().lower(): layout for layout in layouts}
    inferred = infer_deck_roles(deck_path)
    requested = {}
    for item in workorder.get("layout_map") or []:
        if isinstance(item, dict) and item.get("slide"):
            requested[int(item["slide"])] = item
    mapping, warnings, uncertainty = [], [], []
    roles = {}
    for index, entry in enumerate(inferred, 1):
        role = entry["role"]
        wanted = None
        req = requested.get(index)
        if req and req.get("layout"):
            wanted = req.get("layout")
        current = by_name.get(str(entry.get("layout") or "").strip().lower())
        if wanted is None and current is not None:
            current_role = deckkit.role_from_layout(current)
            wrong_section = current_role == "section" and role != "section"
            wrong_title = role == "title" and current_role not in ("title",)
            if not wrong_section and not wrong_title:
                wanted = current["name"]
        layout, fallback = deckkit.choose_layout(layouts, role, wanted)
        if layout is None:
            layout, fallback = deckkit.choose_layout(layouts, "content")
        if layout is None:
            raise ValueError(f"slide {index}: template has no usable layout")
        if fallback:
            warnings.append(f"slide {index}: {fallback}")
            uncertainty.append({"slide": index, "issue": "layout_fallback",
                                "detail": fallback})
        roles.setdefault(role, {"layout": layout["name"],
                                "layout_part": layout["part"]})
        mapping.append({"slide": index, "role": role,
                        "layout": layout["name"],
                        "layout_part": layout["part"],
                        "previous_layout": entry.get("layout", ""),
                        "previous_layout_part": entry.get("previous_layout_part"),
                        "title": entry.get("title", ""),
                        "fallback": fallback})
    return mapping, roles, warnings, uncertainty


def plan_empty(audit, constraints):
    roles = choose_role_layouts(
        audit, ("title", "section", "content", "two-column", "figure",
                "quote", "closing"))
    count = constraints.get("slide_count") or 1
    mapping = []
    for index in range(1, int(count) + 1):
        role = "title" if index == 1 else (
            "closing" if index == count and count > 1 else "content")
        cfg = roles.get(role) or roles.get("content") or {}
        mapping.append({"slide": index, "role": role,
                        "layout": cfg.get("layout", ""),
                        "layout_part": cfg.get("layout_part", ""),
                        "title": "", "source": "", "notes_present": False,
                        "fallback": cfg.get("fallback")})
    return mapping, roles, [], [{
        "issue": "deck_spec_missing",
        "detail": "no model-written deck-spec.json; a partial scaffold will be built",
    }]


def skeleton_spec(constraints, workorder):
    count = constraints.get("slide_count") or 1
    slides = []
    for index in range(1, int(count) + 1):
        role = "title" if index == 1 else (
            "closing" if index == count and count > 1 else "content")
        slides.append({
            "role": role,
            "title": constraints.get("title", "") if index == 1 else "",
            "subtitle": "",
            "bullets": [],
            "notes": "",
            "source": "",
            "layout": "",
        })
    return {
        "_skeleton": True,
        "_instructions": (
            "MODEL MUST REPLACE THIS FILE: read task.md and source materials, "
            "write real per-slide titles, claims, bullets, figure paths, "
            "speaker notes and source locators; then save as work/deck-spec.json."),
        "title": constraints.get("title", ""),
        "source": ", ".join(workorder.get("source_materials") or []),
        "slides": slides,
    }


def font_inventory():
    tool = shutil.which("font-discovery")
    if tool:
        try:
            proc = subprocess.run([tool, "--list"], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=20)
            if proc.returncode == 0:
                names = set()
                for line in proc.stdout.decode("utf-8", "replace").splitlines():
                    name = line.split(":")[0].strip().lower()
                    if name and len(name) < 120:
                        names.add(name)
                if names:
                    return names
        except (OSError, subprocess.TimeoutExpired):
            pass
    tool = shutil.which("fc-list")
    if tool:
        try:
            proc = subprocess.run([tool, ":", "family"], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, timeout=20)
            if proc.returncode == 0:
                names = set()
                for line in proc.stdout.decode("utf-8", "replace").split(","):
                    name = line.strip().lower()
                    if name:
                        names.add(name)
                if names:
                    return names
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def font_availability(audit):
    inventory = font_inventory()
    theme_fonts = audit.get("theme_fonts") or []
    if inventory is None:
        return {"checked": False, "available_count": None, "missing": [],
                "theme_fonts": theme_fonts,
                "note": "font inventory command unavailable; fonts were not changed"}
    missing = [font for font in theme_fonts
               if font.lower() not in inventory
               and font.lower() not in {"", "+mn-lt", "+mj-lt"}]
    return {"checked": True, "available_count": len(inventory),
            "missing": missing, "theme_fonts": theme_fonts,
            "note": "template font declarations were preserved unchanged"}


def build_plan(audit, discovery, workorder, constraints):
    spec = None
    spec_path = discovery.get("spec")
    if spec_path:
        spec = validate_deck_spec(read_json(spec_path) or {},
                                  [Path(spec_path).parent, Path(discovery["input_dir"]),
                                   Path(discovery.get("work_dir") or ".")])
    elif workorder.get("slides"):
        spec = validate_deck_spec({"slides": workorder["slides"]},
                                  [Path(discovery["input_dir"])])
    mode = workorder.get("mode") or ("adapt" if discovery.get("deck") else "build")
    if mode == "adapt" and not discovery.get("deck"):
        mode = "build"
    missing_spec = False
    if mode == "build" and spec is None and discovery.get("deck"):
        # Workorder asked for new content but the model has not written a spec.
        # Adapt the starting deck only as an honest partial deliverable.
        mode = "adapt"
        missing_spec = True
    if mode == "build" and (spec is None or not spec.get("slides")):
        missing_spec = True
        if discovery.get("deck"):
            mapping, roles, warnings, uncertainty = plan_from_deck(
                audit, discovery["deck"], workorder)
            mode = "adapt"
        else:
            mapping, roles, warnings, uncertainty = plan_empty(audit, constraints)
    elif mode == "adapt":
        mapping, roles, warnings, uncertainty = plan_from_deck(
            audit, discovery["deck"], workorder)
    else:
        mapping, roles, warnings, uncertainty = plan_from_spec(
            audit, spec, constraints)

    body_size = max(int(constraints.get("body_size_min", 18)), 18)
    title_size = max(32, int(constraints.get("title_size_min", 24)),
                     body_size + 6)
    for cfg in roles.values():
        cfg.setdefault("title_size", title_size)
        cfg.setdefault("body_size", body_size)
    default_roles = choose_role_layouts(
        audit, ("title", "section", "content", "two-column", "figure",
                "quote", "closing"))
    for role, cfg in default_roles.items():
        roles.setdefault(role, cfg)
    format_spec = {
        "template": discovery.get("template"),
        "mode": mode,
        "slide_size": audit.get("slide_size"),
        "aspect_ratio": audit.get("aspect_ratio"),
        "language": constraints.get("language") or "zh-CN",
        "roles": roles,
        "typography": {
            "title_size": title_size, "body_size": body_size,
            "caption_size": constraints.get("caption_size", 12),
            "footer_size": constraints.get("footer_size", 10),
        },
        "fonts": {
            "theme_fonts": audit.get("theme_fonts") or [],
            "theme_colors": audit.get("theme_colors") or [],
            "substitutions": [],
            "policy": "never_substitute_silently",
        },
        "constraints": constraints,
        "layouts_available": [layout["name"] for layout in audit.get("layouts", [])],
    }
    return {
        "mode": mode,
        "spec": spec,
        "spec_path": spec_path,
        "missing_spec": missing_spec,
        "slides": ((spec or {}).get("slides") or []) if mode == "build" else [],
        "deck_title": (spec or {}).get("deck_title") or constraints.get("title"),
        "layout_map": {"slides": mapping},
        "format_spec": format_spec,
        "warnings": warnings,
        "uncertainty": uncertainty,
        "font_availability": font_availability(audit),
    }
