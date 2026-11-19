#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify: structural + fidelity + content checks for an adapted deck.

Every check reports the real value it measured.  The verifier never rewrites
the deck: font substitution, content loss and overflow are reported as
exceptions so the model can decide what to change.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import deckkit

NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?\s*(?:%|°C|K|eV|nm|µm|um|mm|cm|"
                       r"mL|L|mg|g|kg|h|min|s|ppm|mol)?")


def _tokens(text):
    return set(re.findall(r"[a-z0-9%]+|[一-鿿]", text.lower()))


def _numbers(text):
    return {m.group(0).replace(" ", "") for m in NUMBER_RE.finditer(text)}


def slide_texts(parts):
    texts = []
    for part in deckkit.ordered_slides(parts):
        root = deckkit.parse_xml(parts[part], part)
        texts.append("\n".join(node.text or "" for node in
                               root.iter(f"{{{deckkit.A_NS}}}t")))
    return texts


def _layout_maps(parts):
    by_part = {layout["part"]: layout
               for layout in deckkit.parse_layouts(parts)}
    return by_part


def _slide_layout_part(parts, slide_part):
    return deckkit.slide_layout(parts, slide_part)


def _explicit_fonts(parts):
    fonts, sizes = set(), []
    for name in parts:
        if not deckkit.SLIDE_RE.match(name):
            continue
        raw = parts[name].decode("utf-8", "replace")
        fonts.update(re.findall(r'typeface="([^"]+)"', raw))
        for value in re.findall(r'\bsz="(\d+)"', raw):
            try:
                sizes.append(int(value) / 100.0)
            except ValueError:
                pass
    return fonts, sizes


def _slide_shapes(parts, slide_part, layout):
    root = deckkit.parse_xml(parts[slide_part], slide_part)
    layout_phs = {}
    if layout:
        for ph in layout.get("placeholders", []):
            layout_phs[(ph.get("type"), ph.get("idx", "0"))] = ph
    shapes = []
    for sp in root.findall(".//p:sp", deckkit.NS):
        ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", deckkit.NS)
        attrs = {}
        if ph is not None:
            attrs = {"type": ph.get("type") or
                     ("title" if ph.get("idx", "0") == "0" else "obj"),
                     "idx": ph.get("idx", "0")}
        box = deckkit.shape_box(sp)
        if box is None and attrs:
            slot = layout_phs.get((attrs.get("type"), attrs.get("idx")))
            if slot is None:
                slot = layout_phs.get((attrs.get("type"), "0"))
            if slot:
                box = slot.get("box")
        text = deckkit.xml_text(sp).strip()
        sizes = _shape_sizes(sp)
        shapes.append({"type": attrs.get("type"), "idx": attrs.get("idx"),
                       "box": box, "text": text, "sizes": sizes,
                       "is_title": attrs.get("type") in deckkit.TITLE_TYPES,
                       "body_pr": sp.find("./p:txBody/a:bodyPr", deckkit.NS)})
    return shapes


def _shape_sizes(sp):
    # Walk rPr children directly; ET keeps sz on a:rPr.
    sizes = []
    for node in sp.iter(f"{{{deckkit.A_NS}}}rPr"):
        value = node.get("sz")
        if value:
            try:
                sizes.append(int(value) / 100.0)
            except ValueError:
                pass
    return sizes


def _shape_body_pr(sp):
    return sp.find("./p:txBody/a:bodyPr", deckkit.NS)


def _estimate_overflow(shape, slide_cx, slide_cy):
    box, text = shape["box"], shape["text"]
    if not box or not text:
        return None
    body_pr = shape.get("body_pr")
    if body_pr is not None:
        if body_pr.get("spAutoFit") is not None or \
                body_pr.find("a:normAutofit", deckkit.NS) is not None:
            return None
    sizes = shape.get("sizes") or []
    size = max(sizes) if sizes else 18.0
    usable = max(box["cx"] * 0.90, 1)
    lines = 0
    for line in text.split("\n"):
        if not line:
            lines += 1
            continue
        cjk = len(re.findall(r"[一-鿿]", line))
        latin = len(line) - cjk
        char_emu = size * deckkit.EMU_PT * (1.0 if cjk else 0.56)
        weighted = cjk * size * deckkit.EMU_PT + latin * size * deckkit.EMU_PT * 0.56
        per_line = max(usable, size * deckkit.EMU_PT)
        lines += max(1, math.ceil(weighted / per_line))
    needed = lines * size * deckkit.EMU_PT * 1.18 + box["cy"] * 0.06
    return needed / box["cy"] if box["cy"] else None


def _check_template_fidelity(deck_parts, template_parts, audit):
    deck_masters = {n for n in deck_parts if deckkit.MASTER_RE.match(n)}
    template_masters = {n for n in template_parts if deckkit.MASTER_RE.match(n)}
    deck_layouts = {n for n in deck_parts if deckkit.LAYOUT_RE.match(n)}
    template_layouts = {n for n in template_parts if deckkit.LAYOUT_RE.match(n)}
    deck_theme_part = deckkit.master_theme_part(deck_parts)
    template_theme = deckkit.master_theme_part(template_parts)
    deck_theme = deckkit.parse_theme(deck_parts).get(deck_theme_part, {})
    target_theme = deckkit.parse_theme(template_parts).get(template_theme, {})
    theme_ok = (deck_theme.get("fonts") == target_theme.get("fonts") and
                deck_theme.get("colors") == target_theme.get("colors"))
    size_ok = deckkit.slide_size(deck_parts) == audit.get("slide_size")
    names_ok = True
    deck_names = {layout["name"] for layout in deckkit.parse_layouts(deck_parts)}
    target_names = {layout["name"] for layout in audit.get("layouts", [])}
    if not target_names.issubset(deck_names):
        names_ok = False
    return {
        "master": template_masters.issubset(deck_masters),
        "layouts": template_layouts.issubset(deck_layouts) and names_ok,
        "theme": theme_ok,
        "slide_size": size_ok,
    }, {
        "masters_missing": sorted(template_masters - deck_masters),
        "layouts_missing": sorted(template_layouts - deck_layouts),
        "layout_names_missing": sorted(target_names - deck_names),
        "theme_part": deck_theme_part,
    }


def run(deck_path, template_path, format_spec=None, baseline_path=None,
        plan=None, strict_sizes=False, edits=None):
    format_spec = format_spec or {}
    deck = deckkit.read_parts(deck_path)
    template = deckkit.read_parts(template_path)
    audit = deckkit.audit_template(template_path)
    ok_pkg, package_problems = deckkit.package_ok(deck)
    errors = list(package_problems) if not ok_pkg else []
    warnings, exceptions = [], []
    layouts = audit.get("layouts", [])
    deck_layouts = {layout["part"]: layout
                    for layout in deckkit.parse_layouts(deck)}
    slides = deckkit.ordered_slides(deck)
    template_size = audit.get("slide_size") or deckkit.slide_size(deck) or \
        {"cx": 12192000, "cy": 6858000}
    slide_cx, slide_cy = template_size["cx"], template_size["cy"]
    typo = format_spec.get("typography") or {}
    body_floor = float(typo.get("body_size", 18))
    title_floor = float(typo.get("title_size", 32))
    min_body = float((format_spec.get("constraints") or {}).get("body_size_min", 18))
    min_footer = float((format_spec.get("constraints") or {}).get("footer_size", 10))

    fidelity, fidelity_detail = _check_template_fidelity(deck, template, audit)
    if not all(fidelity.values()):
        errors.append("template fidelity incomplete: " +
                      str({k: v for k, v in fidelity.items() if not v}))

    layout_rows, placeholder_issues = [], []
    font_sizes, title_sizes, body_sizes, caption_sizes = [], [], [], []
    overflow_risks, out_of_bounds, images = [], [], []
    notes_count = 0
    all_slide_fonts = set()
    for index, slide_part in enumerate(slides, 1):
        layout_part = _slide_layout_part(deck, slide_part)
        layout = deck_layouts.get(layout_part)
        layout_name = layout.get("name") if layout else ""
        row = {"slide": index, "layout": layout_name,
               "layout_part": layout_part,
               "layout_in_template": layout_part in
               {item["part"] for item in layouts}}
        if not layout_part or layout_part not in deck:
            errors.append(f"slide {index}: missing slide layout relationship")
        elif not row["layout_in_template"]:
            errors.append(f"slide {index}: layout '{layout_name}' is not from target template")
        layout_rows.append(row)
        root = deckkit.parse_xml(deck[slide_part], slide_part)
        layout_phs = set()
        layout_idxs = set()
        if layout:
            for ph in layout.get("placeholders", []):
                if ph["type"] not in deckkit.CONTROL_PH:
                    layout_phs.add((ph["type"], ph.get("idx", "0")))
                    layout_idxs.add(ph.get("idx", "0"))
        for sp in root.findall(".//p:sp", deckkit.NS):
            ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", deckkit.NS)
            if ph is not None:
                explicit_type = ph.get("type")
                ptype = explicit_type or ("title" if ph.get("idx", "0") == "0"
                                          else "obj")
                key = (ptype, ph.get("idx", "0"))
                matched = (key in layout_phs if explicit_type
                           else key[1] in layout_idxs)
                if ptype not in deckkit.CONTROL_PH and not matched:
                    placeholder_issues.append({
                        "slide": index, "type": ptype, "idx": key[1],
                        "layout": layout_name})
            text = deckkit.xml_text(sp).strip()
            sizes = _shape_sizes(sp)
            font_sizes.extend(sizes)
            if ph is not None and (ph.get("type") or "") in deckkit.TITLE_TYPES:
                if not text:
                    exceptions.append({"code": "EMPTY_TITLE", "slide": index,
                                       "severity": "warning",
                                       "detail": "title placeholder is empty"})
                elif sizes:
                    title_sizes.append(max(sizes))
            elif sizes:
                if any(token in layout_name.lower() for token in
                       ("caption", "photo")):
                    caption_sizes.extend(sizes)
                else:
                    body_sizes.extend(sizes)
            box = deckkit.shape_box(sp)
            if box is None and ph is not None and layout:
                key = (ph.get("type") or "obj", ph.get("idx", "0"))
                for slot in layout.get("placeholders", []):
                    if (slot["type"], slot.get("idx", "0")) == key:
                        box = slot.get("box")
                        break
            if box:
                if (box["x"] < -slide_cx * 0.01 or box["y"] < -slide_cy * 0.01 or
                        box["x"] + box["cx"] > slide_cx * 1.01 or
                        box["y"] + box["cy"] > slide_cy * 1.01):
                    out_of_bounds.append({"slide": index, "box": box,
                                          "name": sp.find(
                                              "./p:nvSpPr/p:cNvPr",
                                              deckkit.NS).get("name", "")
                                          if sp.find("./p:nvSpPr/p:cNvPr",
                                                     deckkit.NS) is not None else ""})
            raw = deck[slide_part].decode("utf-8", "replace")
            all_slide_fonts.update(re.findall(r'typeface="([^"]+)"', raw))
        # notes
        has_notes = any(rel.get("Type", "").endswith("/notesSlide")
                        for rel in deckkit.rels_of(deck, slide_part)[1])
        if has_notes:
            notes_count += 1
        # pictures
        for pic in root.findall(".//p:pic", deckkit.NS):
            box = deckkit.shape_box(pic)
            blip = pic.find(".//a:blip", deckkit.NS)
            rid = blip.get(f"{{{deckkit.R_NS}}}embed") if blip is not None else None
            media = None
            for rel in deckkit.rels_of(deck, slide_part)[1]:
                if rel.get("Id") == rid:
                    media = deckkit.resolve_target(
                        slide_part.rsplit("/", 1)[0], rel.get("Target", ""))
                    break
            pixels = deckkit.image_size(deck[media]) if media in deck else None
            deviation = None
            if box and pixels and pixels[1]:
                box_ratio = box["cx"] / box["cy"] if box["cy"] else 0
                media_ratio = pixels[0] / pixels[1]
                if media_ratio:
                    deviation = abs(box_ratio / media_ratio - 1)
            images.append({"slide": index, "media": media,
                           "aspect_deviation": round(deviation, 4)
                           if deviation is not None else None,
                           "stretched": deviation is not None and deviation > 0.03})
        for shape in _slide_shapes(deck, slide_part, layout):
            ratio = _estimate_overflow(shape, slide_cx, slide_cy)
            if ratio is not None and ratio > 1.25:
                overflow_risks.append({"slide": index, "ratio": round(ratio, 2),
                                       "text": shape["text"][:60]})
        # title hierarchy
        if strict_sizes:
            title_text = ""
            title_size = None
            body_min = None
            for sp in root.findall(".//p:sp", deckkit.NS):
                ph = sp.find("./p:nvSpPr/p:nvPr/p:ph", deckkit.NS)
                picked = _shape_sizes(sp)
                if ph is not None and (ph.get("type") or "") in deckkit.TITLE_TYPES:
                    title_text = deckkit.xml_text(sp).strip()
                    title_size = max(picked) if picked else title_floor
                elif picked:
                    body_min = min(picked) if body_min is None else min(body_min,
                                                                        min(picked))
            if title_text and body_min and title_size and title_size <= body_min:
                exceptions.append({
                    "code": "TITLE_HIERARCHY", "slide": index,
                    "severity": "warning",
                    "detail": f"title {title_size}pt <= body {body_min}pt"})

    if placeholder_issues:
        exceptions.append({
            "code": "PLACEHOLDER_NOT_IN_LAYOUT", "severity": "warning",
            "detail": f"{len(placeholder_issues)} slide placeholder(s) have no "
                      "matching slot in their layout"})
    if out_of_bounds:
        exceptions.append({"code": "SHAPE_OUT_OF_BOUNDS", "severity": "warning",
                           "detail": f"{len(out_of_bounds)} shape(s) exceed the slide"})
    if overflow_risks:
        exceptions.append({"code": "OVERFLOW_RISK", "severity": "warning",
                           "detail": f"{len(overflow_risks)} estimated overflow risk(s)"})
    stretched = [image for image in images if image["stretched"]]
    if stretched:
        exceptions.append({"code": "IMAGE_STRETCHED", "severity": "warning",
                           "detail": f"{len(stretched)} picture(s) deviate >3% from "
                                     "their media aspect ratio"})
    if strict_sizes:
        too_small = [size for size in body_sizes if size < min_body]
        if too_small:
            exceptions.append({
                "code": "FONT_TOO_SMALL", "severity": "warning",
                "detail": f"min body size {min(too_small)}pt < {min_body}pt"})
        if font_sizes and min(font_sizes) < min_footer:
            exceptions.append({
                "code": "FONT_TOO_SMALL", "severity": "info",
                "detail": f"smallest explicit run {min(font_sizes)}pt"})

    theme_fonts = {font for font in (format_spec.get("fonts", {})
                                     .get("theme_fonts") or audit.get("theme_fonts") or [])
                   if font}
    outside_fonts = sorted(font for font in all_slide_fonts
                           if font not in theme_fonts)
    if outside_fonts:
        exceptions.append({
            "code": "FONT_OUTSIDE_TEMPLATE", "severity": "warning",
            "detail": f"slide-level fonts outside template theme: {outside_fonts}"})

    # content retention
    deck_texts = slide_texts(deck)
    retention = {"ratio": 1.0, "missing_numbers": [], "must_contain": [],
                 "baseline": str(baseline_path) if baseline_path else None}
    if baseline_path:
        baseline = deckkit.read_parts(baseline_path)
        base_texts = slide_texts(baseline)
        base_all, deck_all = " ".join(base_texts), " ".join(deck_texts)
        base_tokens, deck_tokens = _tokens(base_all), _tokens(deck_all)
        edited_tokens = set()
        if edits:
            for edit in edits:
                old = str(edit.get("from") or edit.get("old") or "")
                edited_tokens |= _tokens(old)
        comparable = base_tokens - edited_tokens
        if comparable:
            retention["ratio"] = round(
                len(comparable & deck_tokens) / len(comparable), 4)
        elif base_tokens:
            retention["ratio"] = 1.0
        missing_numbers = sorted(_numbers(base_all) - _numbers(deck_all))
        if edits:
            for edit in edits:
                old = str(edit.get("from") or edit.get("old") or "")
                for number in _numbers(old):
                    if number in missing_numbers:
                        missing_numbers.remove(number)
        retention["missing_numbers"] = missing_numbers
    elif not any(text.strip() for text in deck_texts):
        retention["ratio"] = 0.0
    if retention["ratio"] < 0.95:
        exceptions.append({"code": "CONTENT_LOSS", "severity": "warning",
                           "detail": f"content retention {retention['ratio']:.3f}"})
    if retention["missing_numbers"]:
        exceptions.append({"code": "NUMBER_MISSING", "severity": "warning",
                           "detail": retention["missing_numbers"][:12]})

    expected_count = None
    if plan:
        expected_count = len((plan.get("layout_map") or {}).get("slides") or [])
    page_count_match = expected_count is None or expected_count == len(slides)
    placeholders_ok = not placeholder_issues
    fonts_ok = not outside_fonts
    sizes_ok = True
    if strict_sizes:
        sizes_ok = not any(exc["code"] == "FONT_TOO_SMALL" and
                           exc["severity"] == "warning" for exc in exceptions)
    content_ok = (retention["ratio"] >= 0.95 and not retention["missing_numbers"])
    images_ok = not stretched
    overflow_ok = not overflow_risks and not out_of_bounds
    checks = {
        "zip_valid": ok_pkg,
        "xml_valid": ok_pkg,
        "relationships_resolved": ok_pkg,
        "template": fidelity,
        "page_count_match": page_count_match,
        "placeholders_ok": placeholders_ok,
        "fonts_ok": fonts_ok,
        "sizes_ok": sizes_ok,
        "colors_ok": fidelity["theme"],
        "images_ok": images_ok,
        "content_ok": content_ok,
        "overflow_ok": overflow_ok,
        "notes_ok": notes_count > 0 or len(slides) == 0,
    }
    if not page_count_match:
        exceptions.append({"code": "PAGE_COUNT_MISMATCH", "severity": "warning",
                           "detail": f"plan {expected_count} vs deck {len(slides)}"})
    if not ok_pkg:
        errors.extend(package_problems)
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "exceptions": exceptions,
        "checks": checks,
        "slide_count": len(slides),
        "layouts": layout_rows,
        "placeholders": placeholder_issues,
        "fonts": {"used": sorted(all_slide_fonts),
                  "theme": sorted(theme_fonts),
                  "outside_template": outside_fonts},
        "colors": {"outside_theme": []},
        "content_retention": retention,
        "images": images,
        "overflow_risks": overflow_risks,
        "out_of_bounds": out_of_bounds,
        "title_hierarchy": {
            "title_sizes": sorted(set(title_sizes)),
            "body_sizes": sorted(set(body_sizes)),
            "title_floor": title_floor,
            "body_floor": min_body,
        },
        "notes": {"slides_with_notes": notes_count,
                  "slide_count": len(slides)},
        "fidelity": fidelity,
        "fidelity_detail": fidelity_detail,
        "slide_size": template_size,
    }
