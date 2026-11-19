#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selfcheck: build a tiny real template, adapt/build a deck, verify it.

Run directly:
    python3 scripts/selfcheck.py

This is a local smoke test shipped with the skill.  It does not contain any
case answer; the synthetic template and three filler slides only exercise the
OOXML pipeline.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _bootstrap_wrapped():
    if all((HERE / (name + ".py")).is_file() for name in
           ("deckkit", "spec", "verify")):
        return
    mirror = Path(tempfile.mkdtemp(prefix="deck-selfcheck-scripts-"))
    for source in HERE.glob("*.py.md"):
        shutil.copy2(source, mirror / source.name[:-3])
    for source in HERE.glob("*.py"):
        target = mirror / source.name
        if not target.exists():
            shutil.copy2(source, target)
    sys.path.insert(0, str(mirror))


_bootstrap_wrapped()
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:
    from . import deckkit
    from . import spec
    from . import verify
except ImportError:
    import deckkit
    import spec
    import verify

P_HEAD = (f'xmlns:a="{deckkit.A_NS}" xmlns:r="{deckkit.R_NS}" '
          f'xmlns:p="{deckkit.P_NS}"')


def _theme():
    colors = {"dk1": "1F2A30", "lt1": "FFFFFF", "dk2": "203430",
              "lt2": "F0EDE6", "accent1": "A57361", "accent2": "CD9979",
              "accent3": "4E6C67", "accent4": "BA958C", "accent5": "A08C60",
              "accent6": "956969", "hlink": "0F6E78", "folHlink": "5E827E"}
    scheme = "".join(f'<a:{name}><a:srgbClr val="{value}"/></a:{name}>'
                     for name, value in colors.items())
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<a:theme xmlns:a="{deckkit.A_NS}" name="SelfCheck">'
        '<a:themeElements>'
        f'<a:clrScheme name="SelfCheck">{scheme}</a:clrScheme>'
        '<a:fontScheme name="SelfCheck">'
        '<a:majorFont><a:latin typeface="Cambria"/><a:ea typeface="SimSun"/>'
        '</a:majorFont>'
        '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface="SimHei"/>'
        '</a:minorFont></a:fontScheme>'
        '<a:fmtScheme name="SelfCheck">'
        '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '</a:fillStyleLst>'
        '<a:lnStyleLst><a:ln w="6350"/></a:lnStyleLst>'
        '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>'
        '</a:effectStyleLst>'
        '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
        '</a:bgFillStyleLst></a:fmtScheme>'
        '</a:themeElements></a:theme>')


def _sp(sid, name, ph_type, idx, box, text):
    xfrm = deckkit.xfrm_xml(box)
    attrs = f'type="{ph_type}"' if ph_type in ("title", "ctrTitle", "subTitle") else ""
    if ph_type not in ("title", "ctrTitle") and idx is not None:
        attrs += f' idx="{idx}"'
        attrs = attrs.strip()
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/>'
            '<p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            f'<p:nvPr><p:ph {attrs}/></p:nvPr></p:nvSpPr>'
            f'<p:spPr>{xfrm}</p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>'
            '<a:p><a:r><a:rPr lang="zh-CN" sz="3200"/><a:t>'
            f'{deckkit.escape(text)}</a:t></a:r></a:p>'
            '</p:txBody></p:sp>')


def _layout(index, name, ltype, placeholders):
    shapes = []
    for sid, (pname, ptype, idx, box, text) in enumerate(placeholders, 2):
        shapes.append(_sp(sid, pname, ptype, idx, box, text))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sldLayout {P_HEAD} type="{ltype}" preserve="1">'
        f'<p:cSld name="{deckkit.escape(name)}"><p:spTree>'
        f'{deckkit.group_props()}{"".join(shapes)}</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')


def _master(layout_count):
    ids = "".join(f'<p:sldLayoutId id="{2147483649 + i}" r:id="rId{i + 1}"/>'
                  for i in range(layout_count))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sldMaster {P_HEAD}><p:cSld name="SelfCheck Master">'
        f'<p:spTree>{deckkit.group_props()}</p:spTree></p:cSld>'
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
        'accent2="accent2" accent3="accent3" accent4="accent4" '
        'accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        f'<p:sldLayoutIdLst>{ids}</p:sldLayoutIdLst>'
        '<p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>'
        '</p:sldMaster>')


def _presentation():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:presentation {P_HEAD}>'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/>'
        '</p:sldMasterIdLst><p:sldIdLst/>'
        '<p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>'
        '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>')


def make_package(path, with_slides=False):
    parts = {}
    parts["_rels/.rels"] = deckkit.rels_xml([
        ("rId1", deckkit.P_NS + "/officeDocument", "ppt/presentation.xml"),
    ]).encode("utf-8")
    parts["ppt/presentation.xml"] = _presentation().encode("utf-8")
    parts["ppt/_rels/presentation.xml.rels"] = deckkit.rels_xml([
        ("rId1", deckkit.P_NS + "/slideMaster", "slideMasters/slideMaster1.xml"),
        ("rId2", deckkit.R_NS + "/theme", "theme/theme1.xml"),
    ]).encode("utf-8")
    parts["ppt/theme/theme1.xml"] = _theme().encode("utf-8")
    title_phs = [
        ("Title", "ctrTitle", None, {"x": 700000, "y": 1100000, "cx": 10800000, "cy": 1300000},
         "Click to edit title"),
        ("Subtitle", "subTitle", "1", {"x": 700000, "y": 2900000, "cx": 10800000, "cy": 1500000},
         "Click to edit subtitle"),
    ]
    content_phs = [
        ("Title", "title", None, {"x": 650000, "y": 420000, "cx": 10900000, "cy": 950000},
         "Click to edit title"),
        ("Body", "obj", "1", {"x": 700000, "y": 1700000, "cx": 10800000, "cy": 4400000},
         "Click to edit body"),
    ]
    section_phs = [
        ("Title", "title", None, {"x": 800000, "y": 2000000, "cx": 10500000, "cy": 1100000},
         "Click to edit title"),
        ("Body", "body", "1", {"x": 800000, "y": 3300000, "cx": 10500000, "cy": 1200000},
         "Click to edit body"),
    ]
    layout_defs = [
        ("Title 1", "title", title_phs),
        ("Content 1", "obj", content_phs),
        ("Section Header", "secHead", section_phs),
    ]
    for i, (name, ltype, phs) in enumerate(layout_defs, 1):
        parts[f"ppt/slideLayouts/slideLayout{i}.xml"] = \
            _layout(i, name, ltype, phs).encode("utf-8")
        parts[f"ppt/slideLayouts/_rels/slideLayout{i}.xml.rels"] = \
            deckkit.rels_xml([("rId1", deckkit.P_NS + "/slideMaster",
                               "../slideMasters/slideMaster1.xml")]).encode("utf-8")
    parts["ppt/slideMasters/slideMaster1.xml"] = \
        _master(len(layout_defs)).encode("utf-8")
    master_rels = [("rId1", deckkit.P_NS + "/slideLayout",
                    "../slideLayouts/slideLayout1.xml"),
                   ("rId2", deckkit.P_NS + "/slideLayout",
                    "../slideLayouts/slideLayout2.xml"),
                   ("rId3", deckkit.P_NS + "/slideLayout",
                    "../slideLayouts/slideLayout3.xml"),
                   ("rId4", deckkit.R_NS + "/theme", "../theme/theme1.xml")]
    parts["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = \
        deckkit.rels_xml(master_rels).encode("utf-8")
    if with_slides:
        slides = [
            (1, [("Title", "ctrTitle", None, None, "合成自测标题"),
                 ("Subtitle", "subTitle", "1", None, "模板适配冒烟测试")]),
            (2, [("Title", "title", None, None, "问题与旧结论 old"),
                 ("Body", "obj", "1", None, "旧数值 74% 保持不变")]),
            (3, [("Title", "title", None, None, "机制与边界"),
                 ("Body", "obj", "1", None, "仅实验室规模")]),
        ]
        id_tags = []
        rels = [("rId1", deckkit.P_NS + "/slideMaster",
                 "slideMasters/slideMaster1.xml"),
                ("rId2", deckkit.R_NS + "/theme", "theme/theme1.xml")]
        for i, (layout_index, shapes) in enumerate(slides, 1):
            shape_xml = []
            for sid, (name, ptype, idx, _box, text) in enumerate(shapes, 2):
                box = {"x": 700000, "y": 1000000 + (sid - 2) * 1400000,
                       "cx": 10800000, "cy": 1000000}
                shape_xml.append(_sp(sid, name, ptype, idx, box, text))
            xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   f'<p:sld {P_HEAD}><p:cSld><p:spTree>'
                   f'{deckkit.group_props()}{"".join(shape_xml)}</p:spTree>'
                   '</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
                   '</p:sld>')
            parts[f"ppt/slides/slide{i}.xml"] = xml.encode("utf-8")
            parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = deckkit.rels_xml([
                ("rId1", deckkit.P_NS + "/slideLayout",
                 f"../slideLayouts/slideLayout{layout_index}.xml")]).encode("utf-8")
            id_tags.append(f'<p:sldId id="{256 + i}" r:id="rIdSlide{i}"/>')
            rels.append((f"rIdSlide{i}", deckkit.P_NS + "/slide",
                         f"slides/slide{i}.xml"))
        presentation = parts["ppt/presentation.xml"].decode("utf-8")
        presentation = presentation.replace(
            "<p:sldIdLst/>", f'<p:sldIdLst>{"".join(id_tags)}</p:sldIdLst>')
        parts["ppt/presentation.xml"] = presentation.encode("utf-8")
        parts["ppt/_rels/presentation.xml.rels"] = \
            deckkit.rels_xml(rels).encode("utf-8")
    deckkit.ensure_content_types(parts)
    ok, problems = deckkit.package_ok(parts)
    if not ok:
        raise RuntimeError("synthetic package invalid: " + "; ".join(problems))
    deckkit.write_parts(parts, path)
    return path


def run(verbose=False):
    tmp = Path(tempfile.mkdtemp(prefix="deck-selfcheck-"))
    checks = {}
    try:
        template = make_package(tmp / "template.pptx")
        starting = make_package(tmp / "starting.pptx", with_slides=True)
        audit = deckkit.audit_template(template)
        checks["template_audited"] = bool(audit["ok"] and audit["layout_count"] == 3)
        checks["content_layout_is_not_section"] = (
            deckkit.choose_layout(audit["layouts"], "content")[0]["name"] == "Content 1")
        checks["section_layout_selected"] = (
            deckkit.choose_layout(audit["layouts"], "section")[0]["name"]
            == "Section Header")

        edits = [{"edit_id": "E1", "slide": 2, "action": "replace",
                  "from": "old", "to": "new"}]
        workorder = {"mode": "adapt", "case_id": "SELF-CHECK",
                     "slide_count": 3, "edits": edits, "source_materials": []}
        discovery = {"template": str(template), "deck": str(starting),
                     "input_dir": str(tmp), "work_dir": str(tmp),
                     "case": {"case_id": "SELF-CHECK"},
                     "task_text": "标题 32 正文 18", "warnings": []}
        constraints = spec.constraints_from(discovery, workorder)
        plan = spec.build_plan(audit, discovery, workorder, constraints)
        checks["plan_mode_adapt"] = plan["mode"] == "adapt"
        checks["plan_three_slides"] = len(plan["layout_map"]["slides"]) == 3
        adapted = tmp / "adapted.pptx"
        adapt_report = deckkit.retarget_deck(
            starting, template, plan["layout_map"], adapted, edits=edits)
        checks["retarget_ok"] = bool(adapt_report.get("ok"))
        checks["edit_applied"] = bool(adapt_report["edits"]["applied"])
        verify_report = verify.run(
            adapted, template, plan["format_spec"], baseline_path=starting,
            plan=plan, strict_sizes=False,
            edits=workorder.get("edits") or [])
        checks["verify_ok"] = bool(verify_report.get("ok"))
        checks["fidelity_all"] = all(
            verify_report["checks"]["template"].values())
        checks["content_retention"] = (
            verify_report["content_retention"]["ratio"] >= 0.98)
        checks["slide_count_3"] = verify_report["slide_count"] == 3

        deck_spec = {"title": "自测", "slides": [
            {"role": "title", "title": "合成自测", "subtitle": "构建路径"},
            {"role": "content", "title": "结论", "bullets": ["条目一", "条目二"],
             "notes": "讲稿"},
            {"role": "closing", "title": "谢谢", "subtitle": "Q&A"},
        ]}
        built = tmp / "built.pptx"
        build_report = deckkit.build_from_spec(
            template, deck_spec, plan["format_spec"], built)
        checks["build_ok"] = bool(build_report.get("ok"))
        built_verify = verify.run(built, template, plan["format_spec"],
                                  baseline_path=None, plan=plan,
                                  strict_sizes=True)
        checks["build_verify_ok"] = bool(built_verify.get("ok"))
        checks["build_three_slides"] = built_verify["slide_count"] == 3
        checks["build_uses_real_layouts"] = all(
            row["layout_in_template"] for row in built_verify["layouts"])
        if verbose:
            print(json.dumps({"checks": checks,
                              "adapt": adapt_report.get("edits"),
                              "verify": verify_report["checks"]},
                             ensure_ascii=False, indent=2))
        return {"ok": all(checks.values()), "checks": checks,
                "layout_names": [layout["name"] for layout in audit["layouts"]]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    outcome = run(verbose=True)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["ok"] else 1)
