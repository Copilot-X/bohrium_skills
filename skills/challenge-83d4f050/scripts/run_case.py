#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single entrypoint: /input -> /app/output for a mechanism/pathway figure.

Pipeline:
    inventory -> resolve ledger -> spec -> editable SVG -> PDF/PNG ->
    audit (XML/text/semantics/coverage/export/pixels) -> result.json

The semantic ledger is authored from the source by the agent (or supplied by
case.json / figure-workorder.json).  When no ledger exists, a conservative
source-bound draft is built from paper.txt and the result is marked partial; an
extraction draft is never presented as a completed answer.

Exit codes: 0 = completed/partial/input_error (result.json always written),
2 = the run could not start or result.json could not be written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import traceback
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import audit as audit_mod  # noqa: E402
import export as export_mod  # noqa: E402
import pathway_svg  # noqa: E402
import result_graphics  # noqa: E402

TEXT_EXT = {".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".svg"}
ENTITY_KEYS = ("compartments", "molecules", "interactions", "nodes", "edges",
               "entities", "relations", "elements")
VERBS = [
    (r"\b(?:activates?|activated|promotes?|promoted|induces?|induced|"
     r"upregulates?|upregulated|recruits?|recruited|stimulates?|stimulated)\b",
     "activate"),
    (r"\b(?:inhibits?|inhibited|suppresses?|suppressed|represses?|repressed|"
     r"blocks?|blocked|reduces?|reduced|attenuates?|attenuated)\b",
     "inhibit"),
    (r"\b(?:is associated with|are associated with|associates? with|"
     r"associated with|correlates? with|correlated with|co-varies? with)\b",
     "association"),
    (r"\b(?:binds?|bound|binding to|forms? (?:a )?complex with|co-?ip)\b",
     "bind"),
    (r"\b(?:translocates?|translocated|imports?|imported|exports?|exported|"
     r"shuttles?|shuttled|moves? to|moved to)\b",
     "translocate"),
    (r"\b(?:phosphorylates?|phosphorylated|ubiquitinates?|ubiquitinated|"
     r"acetylates?|acetylated|cleaves?|cleaved|modifies?|modified)\b",
     "modify"),
]
COMPARTMENT_WORDS = [
    ("extracellular", "Extracellular"), ("extracellular space", "Extracellular"),
    ("plasma membrane", "Plasma membrane"), ("membrane", "Plasma membrane"),
    ("cytoplasm", "Cytoplasm"), ("cytosol", "Cytoplasm"),
    ("nucleus", "Nucleus"), ("nuclear", "Nucleus"),
    ("mitochond", "Mitochondria"), ("endoplasmic", "ER"),
]


def sha256(path, limit=2 * 1024 * 1024):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        data = fh.read(limit)
        h.update(data)
    return h.hexdigest()


def inventory(input_dir):
    root = Path(input_dir)
    files = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or any(p.startswith(".") for p in path.parts):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            files.append({
                "path": str(path),
                "relative": path.relative_to(root).as_posix(),
                "kind": "text" if path.suffix.lower() in TEXT_EXT else "other",
                "bytes": size,
                "sha256": sha256(path),
            })
    return {"root": str(root), "exists": root.is_dir(), "files": files,
            "total": len(files)}


def has_entities(data):
    """True only for a usable ledger, not for a bare workorder/case header."""
    if not isinstance(data, dict):
        return False
    root = data.get("pathway") if isinstance(data.get("pathway"), dict) else data
    if not isinstance(root, dict):
        return False
    mols = []
    for key in ("molecules", "nodes", "entities", "elements", "required_entities"):
        value = root.get(key)
        if isinstance(value, list) and value:
            mols = value
            break
    ints = []
    for key in ("interactions", "edges", "relations", "connections",
                "required_relations"):
        value = root.get(key)
        if isinstance(value, list) and value:
            ints = value
            break
    if not mols or not ints:
        return False
    if any(isinstance(root.get(key), list) and root.get(key)
           for key in ("compartments", "groups", "regions")):
        return True
    return any(isinstance(m, dict) and
               (m.get("compartment") or m.get("compartment_id"))
               for m in mols)


def looks_like_ledger(data):
    if not isinstance(data, dict):
        return False
    root = data.get("pathway") if isinstance(data.get("pathway"), dict) else data
    return any(key in root for key in ENTITY_KEYS)


def candidate_jsons(input_dir, work_dir, exclude=()):
    seen, out = set(), []
    excluded = []
    for item in exclude:
        try:
            excluded.append(Path(item).resolve())
        except OSError:
            pass

    def add(path):
        if not path:
            return
        path = Path(path)
        try:
            resolved = path.resolve()
            key = str(resolved).lower()
            if any(resolved == ex or ex in resolved.parents for ex in excluded):
                return
        except OSError:
            key = str(path).lower()
        if key not in seen and path.is_file():
            seen.add(key)
            out.append(path)

    add(work_dir / "pathway.json")
    add(Path(input_dir) / "case.json")
    add(Path(input_dir) / "pathway.json")
    add(Path(input_dir) / "figure-workorder.json")
    add(Path(input_dir) / "artifacts" / "figure-workorder.json")
    add(Path(input_dir) / "example-input.json")
    for path in sorted(Path(input_dir).rglob("*.json")):
        add(path)
    return out


def load_json_safe(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def resolve_inputs(args):
    """Return (case_data, ledger_data, paths, notes)."""
    notes = []
    case_path = Path(args.case) if args.case else None
    if case_path and not case_path.is_file():
        notes.append("explicit --case not found: %s" % case_path)
        case_path = None
    ledger_path = Path(args.ledger) if args.ledger else None
    if ledger_path and not ledger_path.is_file():
        notes.append("explicit --ledger not found: %s" % ledger_path)
        ledger_path = None

    candidates = []
    if case_path:
        candidates.append(case_path)
    if ledger_path:
        candidates.append(ledger_path)
    if case_path or ledger_path:
        # Explicit paths are authoritative; do not let another unrelated JSON
        # in /input silently replace the chosen case.
        candidates = list(dict.fromkeys(candidates))
    else:
        candidates.extend(candidate_jsons(
            args.input, Path(args.work), exclude=(args.out, args.work)))

    case_data, ledger_data = None, None
    case_from, ledger_from = None, None
    for path in candidates:
        data = load_json_safe(path)
        if not isinstance(data, dict):
            continue
        if case_data is None and (data.get("case_id") or data.get("workorder") or
                                  data.get("source_materials") or
                                  data.get("task_id")):
            case_data, case_from = data, path
        if ledger_data is None and has_entities(data):
            ledger_data, ledger_from = data, path
    if ledger_path and load_json_safe(ledger_path) is not None:
        ledger_data = load_json_safe(ledger_path)
        ledger_from = ledger_path
    if case_path and load_json_safe(case_path) is not None:
        case_data = load_json_safe(case_path)
        case_from = case_path
        if looks_like_ledger(case_data):
            ledger_data = case_data
            ledger_from = case_path
    return case_data, ledger_data, {"case": case_from, "ledger": ledger_from}, notes


def find_text_sources(input_dir, work_dir):
    sources = []
    for base in (Path(input_dir), Path(work_dir)):
        if not base.is_dir():
            continue
        for name in ("paper.txt", "task.md", "materials.md", "results.md",
                     "source.md"):
            path = base / name
            if path.is_file():
                sources.append(path)
        if base == Path(input_dir):
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.suffix.lower() in (".txt", ".md"):
                    if path not in sources:
                        sources.append(path)
    return sources


def clean_entity(text):
    text = re.sub(r"^[^A-Za-z0-9一-鿿]+|[^A-Za-z0-9一-鿿]+$", "",
                  text.strip())
    for _ in range(2):
        text = re.sub(r"^(?:into|to|from|in|within|at|by|with|of|the|a|an)\s+",
                      "", text, flags=re.I)
        text = re.sub(r"^(?:activated|phosphorylated|nuclear|cytoplasmic|"
                      r"inhibitor|ligand|receptor|kinase|phosphatase|"
                      r"transcription factor)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(?:was|were|is|are|been|being|and|or|which|that)$",
                  "", text, flags=re.I)
    text = re.sub(r"\s+(?:binding|expression|activity|signaling|transcription|"
                  r"promoter)$", "", text, flags=re.I)
    return text.strip()


def compartment_for(text, compartments):
    low = text.lower()
    for needle, label in COMPARTMENT_WORDS:
        if needle in low:
            for comp in compartments:
                if comp["label"].lower() == label.lower():
                    return comp["id"]
    for comp in compartments:
        if comp["label"].lower() == "cytoplasm":
            return comp["id"]
    return compartments[0]["id"]


def draft_from_sources(input_dir, work_dir, case_data):
    """Conservative relation extraction used only as a partial draft."""
    texts = find_text_sources(input_dir, work_dir)
    if not texts:
        return None, ["no paper/task text found for a partial draft"]
    joined = []
    for path in texts[:4]:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for index, line in enumerate(raw.splitlines(), start=1):
            if line.strip():
                joined.append((path, index, line.strip()))
    if not joined:
        return None, ["source text is empty"]

    comp_labels = []
    for _path, _line_no, line in joined:
        low = line.lower()
        for _needle, label in COMPARTMENT_WORDS:
            if _needle in low and label not in comp_labels:
                comp_labels.append(label)
    if not comp_labels:
        comp_labels = ["Reported system"]
    compartments = [{
        "id": "C%d" % (i + 1), "label": label, "order": i,
        "source": {"path": str(Path(texts[0]).resolve()).replace("\\", "/"),
                   "locator": "whole text"},
    } for i, label in enumerate(comp_labels)]

    relations = []
    for path, line_no, line in joined:
        for pattern, effect in VERBS:
            for match in re.finditer(pattern, line, flags=re.I):
                left = line[:match.start()]
                right = line[match.end():]
                left = re.split(r"[,;]", left)[-1]
                subj = ""
                obj = ""
                subj_match = re.search(
                    r"([A-Za-z0-9一-鿿][A-Za-z0-9 _\-/\.一-鿿]{1,40})$",
                    left)
                obj_match = re.match(
                    r"\s*([A-Za-z0-9一-鿿][A-Za-z0-9 _\-/\.一-鿿]{1,40}?)(?:\s+(?:and|which|that|in|to|via|through|at|by|with|of|,|\.|;)\b|[,.;]|$)",
                    right)
                if subj_match:
                    subj = clean_entity(subj_match.group(1))
                if obj_match:
                    obj = clean_entity(obj_match.group(1))
                if not subj or not obj or subj.lower() == obj.lower():
                    continue
                if len(subj) > 34 or len(obj) > 34:
                    continue
                if subj.lower() in ("cell", "cells", "activity", "protein",
                                    "gene", "nucleus", "cytoplasm"):
                    continue
                if effect == "translocate" and any(
                        word in obj.lower() for _n, word in COMPARTMENT_WORDS):
                    continue
                relations.append({
                    "subject": subj, "object": obj, "effect": effect,
                    "path": path, "line": line_no, "quote": line,
                })
                break
    if not relations:
        return None, ["no explicit relation pattern found; author the ledger"]

    molecules, by_label = [], {}
    for rel in relations:
        for label in (rel["subject"], rel["object"]):
            key = label.lower()
            if key not in by_label:
                mid = "M%d" % (len(molecules) + 1)
                by_label[key] = mid
                molecules.append({
                    "id": mid, "label": label, "entity_kind": "protein",
                    "type": "protein",
                    "compartment": compartment_for(rel["quote"], compartments),
                    "source": {"path": str(Path(rel["path"]).resolve()).replace("\\", "/"),
                               "locator": "line %d" % rel["line"],
                               "quote": rel["quote"]},
                })
    interactions = []
    for index, rel in enumerate(relations, start=1):
        interactions.append({
            "id": "I%d" % index,
            "from": by_label[rel["subject"].lower()],
            "to": by_label[rel["object"].lower()],
            "effect": rel["effect"],
            "kind": "reported",
            "mechanism": "",
            "source": {"path": str(Path(rel["path"]).resolve()).replace("\\", "/"),
                       "locator": "line %d" % rel["line"],
                       "quote": rel["quote"]},
        })
    draft = {
        "case_id": str((case_data or {}).get("case_id") or "unknown"),
        "_auto_draft": True,
        "figure_id": "Fig.draft",
        "title": "Draft from source sentences",
        "compartments": compartments,
        "molecules": molecules,
        "interactions": interactions,
        "uncertainty": ["automatic relation draft; review every label against "
                        "the source before treating the figure as final"],
    }
    return draft, ["auto_draft_from_sources"]


def merge_case_metadata(ledger, case_data):
    if not isinstance(case_data, dict) or not isinstance(ledger, dict):
        return ledger
    out = dict(ledger)
    for key in ("case_id", "figure_id", "title", "canvas", "naming", "legend",
                "data_encoding", "omitted", "conflicts", "disclaimers",
                "uncertainty", "caption", "alt_text", "footnote", "workorder"):
        if key not in out and key in case_data:
            out[key] = case_data[key]
    if "canvas" not in out:
        figure = case_data.get("figure") if isinstance(case_data.get("figure"), dict) else {}
        if figure:
            out["canvas"] = {k: figure[k] for k in
                             ("width_mm", "height_mm", "direction")
                             if k in figure}
    return out


def rel_path(path, root):
    if not path:
        return ""
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def source_paths_missing(ledger, roots):
    missing = []
    items = (list(ledger.get("compartments") or []) +
             list(ledger.get("molecules") or []) +
             list(ledger.get("interactions") or []))
    for item in items:
        path, _locator, _quote = pathway_svg.source_parts(item.get("source"))
        if not path:
            continue
        candidate = Path(path)
        if candidate.is_absolute():
            if not candidate.is_file():
                missing.append(path)
            continue
        found = False
        for root in roots:
            try:
                if (Path(root) / path).is_file():
                    found = True
                    break
            except OSError:
                continue
        if not found:
            missing.append(path)
    return sorted(set(missing))


def safe_base(spec, args):
    if args.basename:
        return args.basename
    raw = str(spec.get("figure_id") or "figure")
    base = re.sub(r"[^A-Za-z0-9._-]+", "", raw).strip("._-")
    return base or "figure"


def input_error_result(args, out_root, case_id, detail, warnings=None):
    spec = {"figure_id": "Fig.pathway", "width_mm": 180, "height_mm": 120,
            "direction": "LR", "caption": "", "alt_text": "",
            "disclaimers": []}
    audit = {
        "ok": False,
        "errors": [{"code": "input_error", "detail": detail}],
        "compliance_checks": {
            "xml_valid": False, "text_as_text": False, "no_external_refs": True,
            "viewbox_present": False, "named_groups": 0,
            "compartment_groups": "0/0", "molecule_groups": "0/0",
            "edge_groups": "0/0", "all_reported_edges_sourced": False,
            "all_elements_sourced": False, "element_coverage": 0.0,
            "source_coverage": 0.0, "legend_covers_edge_kinds": False,
            "edge_semantics_ok": False, "inferred_edges_dashed": False,
            "inferred_edges_gray": False,
            "text_fit": False, "arrow_clearance": False,
            "export_artifact_present": False, "pixel_reviewed": False,
            "source_paths_resolvable": False, "source_paths_missing": [],
        },
        "coverage": {"total": 0, "mapped": 0, "element_coverage": 0.0,
                     "source_coverage": 0.0, "reported_source_coverage": 0.0},
        "svg": {}, "png": {}, "pdf": {},
    }
    result = result_graphics.build_result(
        {"case_id": case_id or "unknown", "compartments": [], "molecules": [],
         "interactions": []}, spec, audit, [], case_id=case_id,
        status_override="input_error", warnings=warnings or [],
        change_log=[{"action": "input_error", "detail": detail}])
    path = result_graphics.write_result(result, Path(out_root) / "result.json")
    return result, path


def main():
    ap = argparse.ArgumentParser(description="Pathway figure end-to-end pipeline")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--out", default="/app/output")
    ap.add_argument("--work", default=None)
    ap.add_argument("--case", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--formats", default="pdf,png")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--basename", default=None)
    ap.add_argument("--reviewed", action="store_true",
                    help="extra confirmation after visual inspection")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    out_root = Path(args.out)
    work_dir = Path(args.work) if args.work else (
        Path("/app/work") if Path("/app").is_dir() else out_root.parent / "work")
    try:
        out_root.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(json.dumps({"ok": False, "status": "cannot_start",
                          "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    warnings, notes = [], []
    inv = inventory(args.input)
    try:
        (work_dir / "inventory.json").write_text(
            json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

    try:
        case_data, ledger_data, paths, resolve_notes = resolve_inputs(args)
        notes.extend(resolve_notes)
        if ledger_data is None:
            ledger_data, draft_notes = draft_from_sources(
                args.input, work_dir, case_data)
            notes.extend(draft_notes)
            if ledger_data is None:
                detail = "; ".join(notes) or "no case.json / pathway ledger found"
                result, path = input_error_result(
                    args, out_root, (case_data or {}).get("case_id"),
                    "cannot build a source-bound pathway ledger: %s" % detail,
                    warnings=notes)
                print(json.dumps({"ok": False, "status": result["status"],
                                  "result": str(path), "notes": notes},
                                 ensure_ascii=False, indent=2))
                return 0
            warnings.append("automatic draft: review all entities and edges "
                            "against the source")
        ledger_data = merge_case_metadata(ledger_data, case_data)
        if paths.get("ledger") is None and ledger_data is not None:
            draft_name = ("pathway-draft.json" if ledger_data.get("_auto_draft")
                          else "pathway.json")
            try:
                (work_dir / draft_name).write_text(
                    json.dumps(ledger_data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                paths["ledger"] = work_dir / draft_name
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001
        detail = "%s: %s" % (type(exc).__name__, exc)
        result, path = input_error_result(
            args, out_root, None, "input resolution failed: %s" % detail,
            warnings=[detail])
        print(json.dumps({"ok": False, "status": result["status"],
                          "result": str(path), "notes": [detail]},
                         ensure_ascii=False, indent=2))
        return 0

    ledger = pathway_svg.normalize_ledger(ledger_data, warnings)
    errors, warns = pathway_svg.validate_ledger(ledger)
    for item in warns:
        if item not in warnings:
            warnings.append(item)
    if errors:
        detail = "; ".join(errors)
        result, path = input_error_result(
            args, out_root, ledger.get("case_id"),
            "ledger validation failed: %s" % detail, warnings=warnings)
        print(json.dumps({"ok": False, "status": result["status"],
                          "result": str(path), "errors": errors,
                          "warnings": warnings}, ensure_ascii=False, indent=2))
        return 0

    spec = pathway_svg.build_spec(ledger, warnings)
    base = safe_base(spec, args)
    svg_path = out_root / "figures" / (base + ".svg")
    pdf_path = out_root / "figures" / (base + ".pdf")
    png_path = out_root / "preview" / (base + ".png")
    spec_path = work_dir / "figure-spec.json"
    coverage_path = work_dir / "coverage.json"
    report_path = work_dir / "run-report.json"
    artifacts = []
    export_report = {}
    change_log = []

    try:
        layout = pathway_svg.render_to_file(spec, svg_path)
        spec["layout"] = layout
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        artifacts.append({"role": "editable", "format": "svg",
                          "path": rel_path(svg_path, out_root)})
        change_log.append({"action": "rendered", "tool": "pathway_svg.py",
                           "output": rel_path(svg_path, out_root),
                           "renderer": "pathway_svg",
                           "nodes": len(spec.get("nodes") or []),
                           "edges": len(spec.get("edges") or [])})
    except Exception as exc:  # noqa: BLE001
        warnings.append("render failed: %s: %s" % (type(exc).__name__, exc))
        spec["layout"] = {}

    if artifacts:
        try:
            export_report = export_mod.export_svg(
                svg_path, outdir=out_root / "figures", formats=args.formats,
                dpi=args.dpi, basename=base, out_png=png_path)
        except Exception as exc:  # noqa: BLE001
            export_report = {"ok": False,
                             "exceptions": [{"code": "export_unavailable",
                                             "detail": "%s: %s" %
                                                       (type(exc).__name__, exc)}],
                             "attempts": []}
        stray_png = out_root / "figures" / (base + ".png")
        if stray_png.is_file() and not png_path.is_file():
            try:
                stray_png.replace(png_path)
            except OSError:
                pass
        elif stray_png.is_file() and png_path.is_file():
            try:
                stray_png.unlink()
            except OSError:
                pass
        if pdf_path.is_file() and pdf_path.read_bytes()[:4] == b"%PDF":
            artifacts.append({"role": "export", "format": "pdf",
                              "path": rel_path(pdf_path, out_root)})
        if png_path.is_file() and png_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n":
            dims = export_mod.png_dimensions(png_path) or (0, 0)
            artifacts.append({"role": "preview", "format": "png",
                              "path": rel_path(png_path, out_root),
                              "width_px": dims[0], "height_px": dims[1],
                              "dpi": args.dpi})
        change_log.append({"action": "exported", "tool": "export.py",
                           "formats": [a["format"] for a in artifacts
                                       if a["role"] != "editable"],
                           "tools": export_report.get("tools") or {},
                           "attempts": export_report.get("attempts") or []})

    audit_report = audit_mod.audit_figure(
        ledger, spec, svg_path, pdf_path if pdf_path.is_file() else None,
        png_path if png_path.is_file() else None, {})
    roots = [Path(args.input), work_dir]
    for key in ("case", "ledger"):
        if paths.get(key):
            roots.append(Path(paths[key]).parent)
    missing_sources = source_paths_missing(ledger, roots)
    audit_report["compliance_checks"]["source_paths_resolvable"] = \
        not missing_sources
    audit_report["compliance_checks"]["source_paths_missing"] = missing_sources
    if missing_sources:
        audit_report["ok"] = False
        audit_report.setdefault("errors", []).append({
            "code": "SOURCE_PATH_MISSING",
            "detail": "source paths not found under the input/work roots: %s" %
                      ", ".join(missing_sources[:8]),
        })
    try:
        coverage_path.write_text(json.dumps(audit_report, ensure_ascii=False,
                                            indent=2), encoding="utf-8")
    except OSError:
        pass
    change_log.append({"action": "audited", "tool": "audit.py",
                       "ok": audit_report.get("ok"),
                       "errors": [e.get("code") for e in
                                  audit_report.get("errors") or []]})

    auto_draft = ("auto_draft_from_sources" in notes or
                  ledger.get("_auto_draft") is True)
    status_override = "partial" if auto_draft else None
    result = result_graphics.build_result(
        ledger, spec, audit_report, artifacts,
        case_id=ledger.get("case_id"), status_override=status_override,
        warnings=warnings, change_log=change_log,
        inventory={"root": inv.get("root"), "total": inv.get("total"),
                   "counts": {"text": sum(1 for f in inv.get("files") or []
                                          if f.get("kind") == "text"),
                              "other": sum(1 for f in inv.get("files") or []
                                           if f.get("kind") != "text")},
                   "files": [{k: f[k] for k in ("relative", "kind", "bytes",
                                                "sha256")}
                             for f in (inv.get("files") or [])[:60]]})
    if auto_draft:
        result["exceptions"].append({
            "code": "auto_draft",
            "detail": "entities and relations were drafted automatically from "
                      "source sentences; review the ledger before treating the "
                      "figure as final",
        })
    if args.reviewed:
        result["checks"]["reviewed_confirmed"] = True
    try:
        result_path = result_graphics.write_result(result, out_root / "result.json")
    except OSError as exc:
        print(json.dumps({"ok": False, "status": "cannot_start",
                          "error": "cannot write result.json: %s" % exc},
                         ensure_ascii=False, indent=2))
        return 2

    report = {
        "ok": result["status"] in ("answered", "completed"),
        "status": result["status"],
        "task_status": result["task_status"],
        "case_id": result["case_id"],
        "result": str(result_path),
        "editable_source": result["editable_source"],
        "export_artifact": result["export_artifact"],
        "coverage": result["coverage"],
        "checks": result["checks"],
        "exceptions": result["exceptions"],
        "warnings": warnings,
        "notes": notes,
        "paths": {k: str(v) if v else None for k, v in paths.items()},
    }
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except OSError:
        pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def write_fatal_result(out_dir, detail):
    """Last-resort honest result: official fields, no fabricated artifacts."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = {
        "case_id": "unknown",
        "output_contract": ["case_id", "status", "editable_source",
                            "export_artifact", "content_map",
                            "compliance_checks", "exceptions"],
        "task_family": "pathway",
        "workorder": "biological_mechanism_pathway",
        "status": "input_error",
        "task_status": "input_error",
        "figure": {},
        "artifacts": [],
        "pathway": {"compartments": [], "molecules": [], "interactions": []},
        "semantic_check": {"reviewed": False, "all_effects_sourced": False},
        "legend": {"edge_semantics": []},
        "data_encoding": [], "omitted": [], "conflicts": [],
        "schematic_disclaimers": [], "caption": "", "alt_text": "",
        "checks": {"official_fields": True},
        "editable_source": None,
        "export_artifact": None,
        "content_map": [],
        "compliance_checks": {"xml_valid": False, "status": "input_error",
                              "status_vocabulary": "media"},
        "exceptions": [{"code": "internal_error", "detail": str(detail)}],
        "uncertainty": [str(detail)],
        "change_log": [{"action": "failed", "detail": str(detail)}],
    }
    path = out / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never leak a traceback; leave an honest result
        try:
            out_dir = Path("/app/output")
            if not Path("/app").is_dir():
                out_dir = Path.cwd() / "output"
            path = write_fatal_result(out_dir, "%s: %s" %
                                      (type(exc).__name__, exc))
            print(json.dumps({"ok": False, "status": "input_error",
                              "result": str(path),
                              "error": "%s: %s" % (type(exc).__name__, exc)},
                             ensure_ascii=False, indent=2))
            sys.exit(0)
        except Exception:
            traceback.print_exc()
            sys.exit(2)
