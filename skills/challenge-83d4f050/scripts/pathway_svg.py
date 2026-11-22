#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pathway ledger -> spec -> editable SVG (stdlib only).

The renderer never invents compartments, molecules or relations.  It reads a
source-bound ledger (case.json / pathway.json), validates it, assigns ranks and
writes a self-contained SVG whose semantics stay machine-checkable:

    <g id="C1" data-role="compartment">
    <g id="M1" data-shape="rounded" data-entity-kind="protein">
    <g id="I1" data-effect="inhibit" data-kind="reported">

Edge semantics: activate (solid arrow), inhibit (solid T-bar), bind (thin
solid, no arrow), translocate (dashed arrow), modify (solid arrow + P),
association (thin dotted, no arrow), inferred (gray dashed, keeps polarity),
unknown_link (dashed arrow + ?).  All text is <text>; no external references.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

MM_TO_PX = 96.0 / 25.4
PT_TO_PX = 96.0 / 72.0

EFFECTS = {
    "activate", "inhibit", "bind", "translocate", "modify",
    "association", "unknown_link", "inferred",
}
REPORTED_EFFECTS = {"activate", "inhibit", "bind", "translocate", "modify",
                    "association", "unknown_link"}
KINDS = {"reported", "inferred"}
EVIDENCE_STRENGTH = {"strong", "moderate", "weak", "speculative"}
TYPE_ALIASES = {
    "配体": "ligand", "受体": "receptor", "激酶": "kinase",
    "磷酸酶": "phosphatase", "gtp酶": "gtpase", "小g蛋白": "gtpase",
    "转录因子": "tf", "复合物": "complex", "复合体": "complex",
    "代谢物": "metabolite", "基因": "gene", "通道": "channel",
    "细胞": "cell", "蛋白": "protein", "蛋白质": "protein",
    "其他": "other", "transcription_factor": "tf", "transcription factor": "tf",
    "enzyme": "protein", "small_molecule": "ligand", "ion": "metabolite",
    "ligand_receptor": "complex", "signaling_protein": "protein",
}
TYPE_SHAPE = {
    "ligand": "circle", "receptor": "rounded", "kinase": "rounded",
    "phosphatase": "rounded", "gtpase": "rounded", "tf": "hexagon",
    "complex": "rect", "metabolite": "diamond", "gene": "rect",
    "rna": "rect", "channel": "rounded", "cell": "rounded",
    "protein": "rounded", "other": "rect",
}
TYPE_FILL = {
    "ligand": "#e6f2fa", "receptor": "#e3edf7", "kinase": "#fde9d9",
    "phosphatase": "#fbe3e0", "gtpase": "#fde9d9", "tf": "#ece4f3",
    "complex": "#e4f2ec", "metabolite": "#f7f0cf", "gene": "#efefef",
    "rna": "#eef4e8", "channel": "#e3edf7", "cell": "#fceef2",
    "protein": "#eef1f4", "other": "#f2f2f2",
}
EFFECT_LABEL = {
    "activate": "+", "inhibit": "-", "bind": "binds",
    "translocate": "translocate", "modify": "P", "association": "assoc",
    "unknown_link": "?", "inferred": "inferred",
}
LEGEND_LABEL = {
    "activate": "activation", "inhibit": "inhibition",
    "translocate": "translocation", "bind": "binding",
    "modify": "modification (P)", "association": "association",
    "unknown_link": "unknown link", "inferred": "inferred / uncertain",
}
EFFECT_ALIASES = {
    "activation": "activate", "promote": "activate", "promotes": "activate",
    "induce": "activate", "upregulate": "activate", "recruit": "activate",
    "inhibition": "inhibit", "suppress": "inhibit", "repress": "inhibit",
    "block": "inhibit", "reduce": "inhibit", "negative": "inhibit",
    "binding": "bind", "binds": "bind", "complex": "bind",
    "transport": "translocate", "translocation": "translocate",
    "import": "translocate", "export": "translocate", "shuttle": "translocate",
    "phosphorylation": "modify", "modification": "modify",
    "phosphorylate": "modify", "ubiquitinate": "modify", "cleave": "modify",
    "related": "association", "associated": "association",
    "unknown": "unknown_link", "unknown_relation": "unknown_link",
    "inferred": "inferred", "dashed": "inferred",
}
MOVE_WORDS = ("translocat", "nuclear import", "nuclear export", "shuttle",
              "traffic", "transport", "import", "export", "secretion",
              "endocyt", "exocyt")


def warn(bucket, message):
    if message not in bucket:
        bucket.append(message)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def first_list(data, keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, list) and value:
            return value
    return []


def source_parts(value):
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        path = str(value.get("path") or value.get("file") or "").strip()
        locator = str(value.get("locator") or value.get("page") or
                      value.get("lines") or "").strip()
        quote = str(value.get("quote") or value.get("text") or "").strip()
        return path, locator, quote
    text = str(value or "").strip()
    return text, "", ""


def source_dict(value):
    path, locator, quote = source_parts(value)
    item = {}
    if path:
        item["path"] = path
    if locator:
        item["locator"] = locator
    if quote:
        item["quote"] = quote
    return item


def source_text(value):
    path, locator, _quote = source_parts(value)
    if not path and not locator:
        return "not_provided"
    return " ".join(x for x in (path, locator) if x).strip()


def source_ok(value):
    path, locator, _quote = source_parts(value)
    text = (path or locator).strip().lower()
    return bool(text) and text not in ("not_provided", "unknown", "n/a", "none")


def normalize_type(value):
    text = str(value or "").strip()
    low = text.lower()
    if low in TYPE_SHAPE:
        return low
    return TYPE_ALIASES.get(low, TYPE_ALIASES.get(text, "other"))


def normalize_effect(value):
    text = str(value or "").strip()
    low = text.lower().replace(" ", "_").replace("-", "_")
    return EFFECT_ALIASES.get(low, low)


def normalize_ledger(data, warnings=None):
    """Accept case.json / figure-workorder.json / pathway.json layouts."""
    warnings = warnings if warnings is not None else []
    if not isinstance(data, dict):
        return {}
    root = data.get("pathway") if isinstance(data.get("pathway"), dict) else data
    COMP_KEYS = ("compartments", "groups", "regions")
    MOL_KEYS = ("molecules", "nodes", "entities", "elements", "required_entities")
    INT_KEYS = ("interactions", "edges", "relations", "connections",
                "required_relations")
    comps = first_list(root, COMP_KEYS)
    mols = first_list(root, MOL_KEYS)
    ints = first_list(root, INT_KEYS)
    if not mols:
        mols = first_list(data, MOL_KEYS)
    if not ints:
        ints = first_list(data, INT_KEYS)
    if not comps:
        comps = first_list(data, COMP_KEYS)

    norm_comps = []
    for index, comp in enumerate(comps):
        if not isinstance(comp, dict):
            continue
        cid = str(comp.get("id") or comp.get("element_id") or
                  "C%d" % (index + 1))
        norm_comps.append({
            "id": cid,
            "label": str(comp.get("label") or comp.get("name") or cid),
            "order": comp.get("order", comp.get("rank", index)),
            "fill": comp.get("fill"),
            "stroke": comp.get("stroke"),
            "dashed": comp.get("dashed", True),
            "source": source_dict(comp.get("source") or comp.get("evidence")),
        })
    comp_by_label = {c["label"].strip().lower(): c["id"] for c in norm_comps}

    norm_mols = []
    seen_mol = set()
    for index, mol in enumerate(mols):
        if not isinstance(mol, dict):
            continue
        mid = str(mol.get("id") or mol.get("element_id") or "M%d" % (index + 1))
        if mid in seen_mol:
            mid = "%s_%d" % (mid, index + 1)
        seen_mol.add(mid)
        raw_kind = str(mol.get("entity_kind") or "").lower()
        if raw_kind not in ("gene", "protein", "complex", "cell",
                            "metabolite", "rna"):
            raw_kind = ""
        mtype = normalize_type(mol.get("type") or mol.get("entity_type") or
                               mol.get("role") or raw_kind)
        entity_kind = raw_kind or ("gene" if mtype == "gene" else "protein")
        if entity_kind in ("protein_complex", "multi_protein_complex"):
            entity_kind = "complex"
        compartment = str(mol.get("compartment") or
                          mol.get("compartment_id") or "").strip()
        if compartment and compartment not in {c["id"] for c in norm_comps}:
            compartment = comp_by_label.get(compartment.lower(), compartment)
        display = mol.get("display") if isinstance(mol.get("display"), dict) else {}
        norm_mols.append({
            "id": mid,
            "label": str(mol.get("label") or mol.get("name") or
                         mol.get("symbol") or mid),
            "symbol": str(mol.get("symbol") or ""),
            "entity_kind": entity_kind,
            "type": mtype,
            "compartment": compartment,
            "rank": mol.get("rank"),
            "order": mol.get("order"),
            "sublabel": str(mol.get("sublabel") or mol.get("state") or ""),
            "aliases": list(mol.get("aliases") or []),
            "fill": mol.get("fill"),
            "stroke": mol.get("stroke"),
            "dashed": bool(mol.get("dashed", False)),
            "italic": display.get("italic") if "italic" in display else
                      mol.get("italic"),
            "max_text_px": mol.get("max_text_px"),
            "source": source_dict(mol.get("source") or mol.get("evidence")),
        })

    known_comp_ids = {c["id"] for c in norm_comps}
    for mol in norm_mols:
        compartment = str(mol.get("compartment") or "").strip()
        if not compartment:
            if not norm_comps:
                norm_comps.append({
                    "id": "C1", "label": "Compartment", "order": 0,
                    "fill": None, "stroke": None, "dashed": True,
                    "source": dict(mol.get("source") or {}),
                })
                known_comp_ids.add("C1")
            mol["compartment"] = norm_comps[0]["id"]
            continue
        if compartment in known_comp_ids:
            continue
        resolved = comp_by_label.get(compartment.lower())
        if resolved:
            mol["compartment"] = resolved
            continue
        cid = "C%d" % (len(norm_comps) + 1)
        norm_comps.append({
            "id": cid, "label": compartment, "order": len(norm_comps),
            "fill": None, "stroke": None, "dashed": True,
            "source": dict(mol.get("source") or {}),
        })
        comp_by_label[compartment.lower()] = cid
        known_comp_ids.add(cid)
        mol["compartment"] = cid

    mol_by_label = {m["label"].strip().lower(): m["id"] for m in norm_mols}
    mol_ids = {m["id"] for m in norm_mols}

    norm_ints = []
    seen_edge = set()
    for index, edge in enumerate(ints):
        if not isinstance(edge, dict):
            continue
        raw_src = edge.get("from") or edge.get("subject") or edge.get("src")
        if not raw_src and isinstance(edge.get("source"), str):
            raw_src = edge["source"]
        raw_dst = edge.get("to") or edge.get("target") or edge.get("object") or \
            edge.get("dst")
        src = str(raw_src or "").strip()
        dst = str(raw_dst or "").strip()
        if src not in mol_ids:
            src = mol_by_label.get(src.lower(), src)
        if dst not in mol_ids:
            dst = mol_by_label.get(dst.lower(), dst)
        effect = normalize_effect(edge.get("effect") or edge.get("relation") or
                                  edge.get("type") or "unknown_link")
        kind = str(edge.get("kind") or edge.get("status") or "").lower()
        if effect == "inferred" or kind == "inferred" or edge.get("inferred") is True:
            kind = "inferred"
        else:
            kind = "reported"
        base = normalize_effect(edge.get("base_effect") or edge.get("polarity") or
                                edge.get("sign") or "")
        if base not in REPORTED_EFFECTS:
            polarity = str(edge.get("polarity") or edge.get("sign") or "").lower()
            if polarity in ("-", "negative", "inhibit", "inhibition"):
                base = "inhibit"
            elif effect in REPORTED_EFFECTS and effect not in (
                    "association", "unknown_link"):
                base = effect
            else:
                base = "activate"
        eid = str(edge.get("id") or edge.get("element_id") or
                  "I%d" % (index + 1))
        if eid in seen_edge:
            eid = "%s_%d" % (eid, index + 1)
        seen_edge.add(eid)
        norm_ints.append({
            "id": eid,
            "from": src,
            "to": dst,
            "effect": effect,
            "base_effect": base,
            "kind": kind,
            "mechanism": str(edge.get("mechanism") or edge.get("note") or ""),
            "label": str(edge.get("label") or "").strip(),
            "route": str(edge.get("route") or "").strip(),
            "direction": str(edge.get("direction") or "forward"),
            "evidence_strength": edge.get("evidence_strength"),
            "source": source_dict(edge.get("source") or edge.get("evidence")),
        })

    out = dict(data)
    out["compartments"] = norm_comps
    out["molecules"] = norm_mols
    out["interactions"] = norm_ints
    if root is not data:
        for key in ("canvas", "naming", "title", "figure_id", "legend",
                    "data_encoding", "omitted", "conflicts", "disclaimers",
                    "uncertainty", "caption", "alt_text", "footnote"):
            if key in root and key not in out:
                out[key] = root[key]
    return out


def validate_ledger(data):
    errors, warnings = [], []
    if not isinstance(data, dict):
        return ["ledger root is not an object"], warnings
    comps = data.get("compartments")
    mols = data.get("molecules")
    ints = data.get("interactions")
    if not isinstance(comps, list) or not comps:
        errors.append("compartments[] must be a non-empty list")
    if not isinstance(mols, list) or not mols:
        errors.append("molecules[] must be a non-empty list")
    if not isinstance(ints, list) or not ints:
        errors.append("interactions[] must be a non-empty list")
    if errors:
        return errors, warnings

    comp_ids = set()
    for index, comp in enumerate(comps):
        cid = str(comp.get("id") or "").strip()
        if not cid:
            errors.append("compartment[%d] missing id" % index)
            continue
        if cid in comp_ids:
            errors.append("duplicate compartment id: %s" % cid)
        comp_ids.add(cid)
        if not comp.get("label"):
            warn(warnings, "compartment %s missing label" % cid)
        if not source_ok(comp.get("source")):
            warn(warnings, "compartment %s has no usable source" % cid)

    mol_ids = set()
    for index, mol in enumerate(mols):
        mid = str(mol.get("id") or "").strip()
        if not mid:
            errors.append("molecule[%d] missing id" % index)
            continue
        if mid in mol_ids:
            errors.append("duplicate molecule id: %s" % mid)
        mol_ids.add(mid)
        if not mol.get("label"):
            errors.append("molecule %s missing label" % mid)
        compartment = str(mol.get("compartment") or "").strip()
        if compartment not in comp_ids:
            errors.append("molecule %s references unknown compartment: %s" %
                          (mid, compartment))
        if not source_ok(mol.get("source")):
            warn(warnings, "molecule %s has no usable source" % mid)

    int_ids = set()
    for index, edge in enumerate(ints):
        iid = str(edge.get("id") or "").strip()
        if not iid:
            errors.append("interaction[%d] missing id" % index)
            continue
        if iid in int_ids:
            errors.append("duplicate interaction id: %s" % iid)
        int_ids.add(iid)
        if str(edge.get("from") or "") not in mol_ids:
            errors.append("interaction %s has unknown from: %s" %
                          (iid, edge.get("from")))
        if str(edge.get("to") or "") not in mol_ids:
            errors.append("interaction %s has unknown to: %s" %
                          (iid, edge.get("to")))
        effect = str(edge.get("effect") or "")
        if effect not in EFFECTS:
            errors.append("interaction %s has unknown effect: %s" % (iid, effect))
        kind = str(edge.get("kind") or "reported")
        if kind not in KINDS:
            errors.append("interaction %s has unknown kind: %s" % (iid, kind))
        strength = edge.get("evidence_strength")
        if strength and str(strength) not in EVIDENCE_STRENGTH:
            warn(warnings, "interaction %s has unknown evidence_strength: %s" %
                 (iid, strength))
        if kind == "reported" and not source_ok(edge.get("source")):
            warn(warnings, "reported interaction %s has no usable source" % iid)
        if kind == "inferred" and not source_ok(edge.get("source")):
            warn(warnings, "inferred interaction %s has no usable source" % iid)
        if effect == "translocate":
            by_id = {str(m.get("id")): m for m in mols}
            if str(by_id.get(str(edge.get("from")), {}).get("compartment")) == \
                    str(by_id.get(str(edge.get("to")), {}).get("compartment")):
                warn(warnings, "translocation %s does not cross compartments" % iid)
        if effect in ("activate", "inhibit", "modify"):
            by_id = {str(m.get("id")): m for m in mols}
            src_comp = by_id.get(str(edge.get("from")), {}).get("compartment")
            dst_comp = by_id.get(str(edge.get("to")), {}).get("compartment")
            if src_comp != dst_comp:
                mechanism = str(edge.get("mechanism") or "").lower()
                if any(word in mechanism for word in MOVE_WORDS):
                    warn(warnings, "interaction %s crosses compartments and mentions "
                                   "transport; consider effect=translocate" % iid)
                else:
                    warn(warnings, "interaction %s crosses compartments as signaling; "
                                   "confirm the molecule itself does not move" % iid)
    return errors, warnings


def compartment_order(data):
    order = {}
    for index, comp in enumerate(data.get("compartments") or []):
        cid = str(comp.get("id"))
        try:
            order[cid] = int(comp.get("order", index))
        except (TypeError, ValueError):
            order[cid] = index
    return order


def assign_ranks(data, warnings):
    molecules = data["molecules"]
    interactions = data["interactions"]
    comp_order = compartment_order(data)
    explicit = [m for m in molecules if m.get("rank") is not None]
    ranks = {}
    if len(explicit) == len(molecules):
        for mol in molecules:
            try:
                ranks[str(mol["id"])] = max(0, int(mol["rank"]))
            except (TypeError, ValueError):
                ranks[str(mol["id"])] = 0
    else:
        if explicit:
            warn(warnings, "rank missing on %d molecule(s); derived from compartments "
                           "and interaction order" % (len(molecules) - len(explicit)))
        for mol in molecules:
            ranks[str(mol["id"])] = comp_order.get(str(mol.get("compartment")), 0)
        flow = [e for e in interactions
                if str(e.get("effect")) not in ("bind", "association", "inferred")]
        for _ in range(len(molecules) + 1):
            changed = False
            for edge in flow:
                src, dst = str(edge.get("from")), str(edge.get("to"))
                if src in ranks and dst in ranks and ranks[dst] < ranks[src] + 1:
                    ranks[dst] = ranks[src] + 1
                    changed = True
            if not changed:
                break
    counters = {}
    orders = {}
    for mol in molecules:
        mid = str(mol["id"])
        rank = ranks[mid]
        if mol.get("order") is not None:
            try:
                order = int(mol["order"])
            except (TypeError, ValueError):
                order = counters.get(rank, 0)
        else:
            order = counters.get(rank, 0)
        counters[rank] = max(counters.get(rank, 0), order + 1)
        orders[mid] = order
    return ranks, orders


def build_spec(data, warnings=None):
    warnings = warnings if warnings is not None else []
    comp_order = compartment_order(data)
    ranks, orders = assign_ranks(data, warnings)
    nodes, fills = [], {}
    gene_italic = str((data.get("naming") or {}).get("gene_style") or "").lower() == "italic"
    for mol in data["molecules"]:
        mid = str(mol["id"])
        mtype = normalize_type(mol.get("type"))
        node = {
            "id": mid,
            "label": str(mol.get("label") or mid),
            "shape": TYPE_SHAPE.get(mtype, "rect"),
            "fill": mol.get("fill") or TYPE_FILL.get(mtype, "#f2f2f2"),
            "stroke": mol.get("stroke") or "#444444",
            "stroke_width": 1.1,
            "rank": ranks[mid],
            "order": orders[mid],
            "molecule_type": mtype,
            "entity_kind": str(mol.get("entity_kind") or "protein"),
            "sublabel": str(mol.get("sublabel") or ""),
            "dashed": bool(mol.get("dashed", False)),
            "max_text_px": float(mol.get("max_text_px") or 118),
            "source": source_text(mol.get("source")),
        }
        italic = mol.get("italic")
        if italic is None:
            italic = gene_italic and str(mol.get("entity_kind")) == "gene"
        node["italic"] = bool(italic)
        fills[mid] = node["fill"]
        nodes.append(node)

    groups = []
    for comp in sorted(data["compartments"],
                       key=lambda c: comp_order.get(str(c.get("id")), 0)):
        cid = str(comp.get("id"))
        members = [str(m["id"]) for m in data["molecules"]
                   if str(m.get("compartment")) == cid]
        if not members:
            continue
        groups.append({
            "id": cid,
            "label": str(comp.get("label") or cid),
            "nodes": members,
            "fill": comp.get("fill") or "#f7f7f7",
            "stroke": comp.get("stroke") or "#b8b8b8",
            "dashed": bool(comp.get("dashed", True)),
        })

    by_id = {str(m["id"]): m for m in data["molecules"]}
    edges = []
    for edge in data["interactions"]:
        iid = str(edge["id"])
        effect = str(edge["effect"])
        inferred = str(edge.get("kind")) == "inferred" or effect == "inferred"
        base = str(edge.get("base_effect") or "activate")
        if base not in REPORTED_EFFECTS:
            base = "activate"
        src, dst = str(edge["from"]), str(edge["to"])
        explicit_route = str(edge.get("route") or "")
        if explicit_route:
            route = explicit_route
        elif ranks[src] == ranks[dst]:
            route = "straight"
        elif ranks[dst] < ranks[src]:
            route = "curve"
        else:
            route = "orthogonal"
        label = str(edge.get("label") or "")
        if not label:
            label = EFFECT_LABEL.get(base if inferred else effect,
                                     EFFECT_LABEL.get("activate"))
        item = {
            "id": iid,
            "from": src,
            "to": dst,
            "effect": "inferred" if inferred else effect,
            "base_effect": base,
            "kind": "inferred" if inferred else "reported",
            "label": label,
            "route": route,
            "mechanism": str(edge.get("mechanism") or ""),
            "direction": str(edge.get("direction") or "forward"),
            "source": source_text(edge.get("source")),
            "source_ok": source_ok(edge.get("source")),
            "dashed": bool(inferred or effect in ("translocate", "unknown_link")),
        }
        edges.append(item)

    legend, seen = [], set()
    for edge in edges:
        kind = str(edge["effect"])
        if kind in seen:
            continue
        seen.add(kind)
        legend.append({"effect": kind,
                       "label": LEGEND_LABEL.get(kind, kind)})
    for item in ((data.get("legend") or {}).get("edge_semantics") or []):
        if isinstance(item, dict) and item.get("effect"):
            effect = normalize_effect(item.get("effect"))
            for entry in legend:
                if entry["effect"] == effect and item.get("label"):
                    entry["label"] = str(item["label"])

    disclaimers = list(data.get("disclaimers") or [])
    for edge in edges:
        if edge["kind"] != "inferred":
            continue
        iid = edge["id"]
        if not any(iid in str(text) for text in disclaimers):
            disclaimers.append(
                "%s (%s to %s) is inferred: the available material reports an "
                "association or incomplete link, not a direct perturbation." %
                (iid, edge["from"], edge["to"]))
    for item in data.get("uncertainty") or []:
        if str(item) not in disclaimers:
            disclaimers.append("uncertainty: %s" % item)

    canvas = data.get("canvas") or {}
    width_mm = float(canvas.get("width_mm") or data.get("width_mm") or 180)
    height_mm = float(canvas.get("height_mm") or data.get("height_mm") or 120)
    direction = str(canvas.get("direction") or data.get("direction") or "LR").upper()
    spec = {
        "case_id": str(data.get("case_id") or data.get("id") or "unknown"),
        "figure_id": str(data.get("figure_id") or "Fig.pathway"),
        "title": str(data.get("title") or ""),
        "width_mm": width_mm,
        "height_mm": height_mm,
        "direction": direction if direction in ("LR", "TB") else "LR",
        "font_family": str(canvas.get("font_family") or
                           data.get("font_family") or "Liberation Sans"),
        "font_size_pt": float(canvas.get("font_size_pt") or
                              data.get("font_size_pt") or 8),
        "groups": groups,
        "nodes": nodes,
        "edges": edges,
        "legend": legend,
        "footnote": str(data.get("footnote") or
                        "All elements are transcribed from the case materials. "
                        "Gray dashed edges are inferred and keep their observed "
                        "polarity (arrow or T-bar); see disclaimers."),
        "caption": str(data.get("caption") or default_caption(data)),
        "alt_text": str(data.get("alt_text") or default_alt_text(data)),
        "omitted": list(data.get("omitted") or []),
        "conflicts": list(data.get("conflicts") or []),
        "data_encoding": list(data.get("data_encoding") or []),
        "disclaimers": disclaimers,
        "warnings": list(warnings),
    }
    return spec


def default_caption(data):
    comps = len(data.get("compartments") or [])
    mols = len(data.get("molecules") or [])
    edges = data.get("interactions") or []
    inferred = sum(1 for e in edges if str(e.get("kind")) == "inferred" or
                   str(e.get("effect")) == "inferred")
    return ("Schematic with %d compartment(s), %d molecule(s) and %d typed "
            "interaction(s); %d interaction(s) are marked inferred." %
            (comps, mols, len(edges), inferred))


def default_alt_text(data):
    names = ", ".join(str(m.get("label") or m.get("id"))
                      for m in (data.get("molecules") or [])[:8])
    return ("Pathway diagram showing %s connected by directional and "
            "non-directional relations; line style and arrowheads encode "
            "activation, inhibition, binding, translocation, modification or "
            "inference." % (names or "the reported molecules"))


# ---------------------------------------------------------------------------
# Text and geometry helpers
# ---------------------------------------------------------------------------
def text_width(text, size_px):
    total = 0.0
    for ch in str(text):
        if ord(ch) > 0x2E80:
            total += size_px
        elif ch in "il.,:;'|!()[]":
            total += size_px * 0.34
        elif ch in "mwMW@%":
            total += size_px * 0.95
        else:
            total += size_px * 0.56
    return total


def wrap_label(label, max_width, size_px):
    tokens, buf = [], ""
    for ch in str(label):
        if ord(ch) > 0x2E80:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ch == " ":
            if buf:
                tokens.append(buf)
            tokens.append(" ")
            buf = ""
        else:
            buf += ch
    if buf:
        tokens.append(buf)
    lines, cur = [], ""
    for tok in tokens:
        candidate = cur + tok
        if text_width(candidate, size_px) <= max_width or not cur:
            cur = candidate
        else:
            lines.append(cur.rstrip())
            cur = "" if tok == " " else tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def _attr(value):
    return escape(str(value), {'"': "&quot;"})


def _fit_text(label, sublabel, max_width, base_px, max_lines=2):
    for factor in (1.0, 0.94, 0.88, 0.82, 0.76):
        size = base_px * factor
        lines = wrap_label(label, max_width, size)
        sub_lines = wrap_label(sublabel, max_width, size * 0.86) if sublabel else []
        if len(lines) <= max_lines and len(sub_lines) <= 1:
            return lines, sub_lines, size, True
    lines = wrap_label(label, max_width, base_px * 0.76)[:max_lines]
    return lines, [], base_px * 0.76, False


def _anchor(node, tx, ty):
    cx, cy = node["_cx"], node["_cy"]
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    half_w, half_h = node["_w"] / 2.0, node["_h"] / 2.0
    if node.get("shape") == "ellipse":
        denom = ((dx * dx) / (half_w * half_w) +
                 (dy * dy) / (half_h * half_h)) ** 0.5
        t = 1.0 / denom if denom else 0.0
        return cx + dx * t, cy + dy * t
    if node.get("shape") == "diamond":
        t = 1.0 / (abs(dx) / half_w + abs(dy) / half_h or 1.0)
        return cx + dx * t, cy + dy * t
    tx_ = half_w / abs(dx) if dx else float("inf")
    ty_ = half_h / abs(dy) if dy else float("inf")
    t = min(tx_, ty_)
    return cx + dx * t, cy + dy * t


def _points_path(points):
    return "M" + " L".join("%.2f,%.2f" % (x, y) for x, y in points)


def _edge_points(edge, by_id, direction):
    src = by_id.get(str(edge.get("from")))
    dst = by_id.get(str(edge.get("to")))
    if not src or not dst:
        return None
    x1, y1 = _anchor(src, dst["_cx"], dst["_cy"])
    x2, y2 = _anchor(dst, src["_cx"], src["_cy"])
    # Keep arrowheads/T-bars outside the node fill (nodes paint after edges).
    gap = 2.5
    d1 = math.hypot(dst["_cx"] - src["_cx"], dst["_cy"] - src["_cy"])
    if d1 > 0:
        x1 += (dst["_cx"] - src["_cx"]) / d1 * gap
        y1 += (dst["_cy"] - src["_cy"]) / d1 * gap
        x2 += (src["_cx"] - dst["_cx"]) / d1 * gap
        y2 += (src["_cy"] - dst["_cy"]) / d1 * gap
    route = str(edge.get("route") or "straight")
    if route == "curve":
        mx = (x1 + x2) / 2.0
        return {"kind": "curve", "points": [(x1, y1), (x2, y2)],
                "d": "M%.2f,%.2f C%.2f,%.2f %.2f,%.2f %.2f,%.2f" %
                     (x1, y1, mx, y1, mx, y2, x2, y2)}
    if route == "orthogonal":
        if direction == "TB":
            mid = (y1 + y2) / 2.0
            points = [(x1, y1), (x1, mid), (x2, mid), (x2, y2)]
        else:
            mid = (x1 + x2) / 2.0
            points = [(x1, y1), (mid, y1), (mid, y2), (x2, y2)]
        return {"kind": "poly", "points": points, "d": _points_path(points)}
    return {"kind": "poly", "points": [(x1, y1), (x2, y2)],
            "d": _points_path([(x1, y1), (x2, y2)])}


def _lerp_along(points, t):
    lengths = []
    total = 0.0
    for a, b in zip(points, points[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        lengths.append(seg)
        total += seg
    if total <= 0:
        return points[0]
    target = total * t
    acc = 0.0
    for (a, b), seg in zip(zip(points, points[1:]), lengths):
        if acc + seg >= target:
            f = (target - acc) / seg if seg else 0.0
            return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
        acc += seg
    return points[-1]


def _rects_overlap(a, b, pad=0.0):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or
                a[3] + pad <= b[1] or b[3] + pad <= a[1])


def _label_position(points, boxes, label, size):
    width = text_width(label, size) + 6.0
    height = size * 1.25

    def box_at(x, y):
        return (x - width / 2.0, y - height / 2.0,
                x + width / 2.0, y + height / 2.0)

    def clear(box):
        return not any(_rects_overlap(box, node_box, 5.0) for node_box in boxes)

    for t in (0.5, 0.38, 0.62, 0.28, 0.72):
        x, y = _lerp_along(points, t)
        box = box_at(x, y)
        if clear(box):
            return x, y, box
    # Dense adjacent nodes: move the label off the edge, perpendicular to it.
    x, y = _lerp_along(points, 0.5)
    if len(points) >= 2:
        ax, ay = points[0]
        bx, by = points[-1]
        length = math.hypot(bx - ax, by - ay)
        if length > 0:
            nx, ny = -(by - ay) / length, (bx - ax) / length
            max_half = max((max(b[3] - b[1], b[2] - b[0]) / 2.0
                            for b in boxes), default=height)
            for factor in (1.0, 1.6, 2.3, 3.1):
                for sign in (1.0, -1.0):
                    off = (max_half + height * 0.8) * factor * sign
                    cx, cy = x + nx * off, y + ny * off
                    box = box_at(cx, cy)
                    if clear(box):
                        return cx, cy, box
    box = box_at(x, y)
    return x, y, box


def _shape_markup(node):
    cx, cy = node["_cx"], node["_cy"]
    w, h = node["_w"], node["_h"]
    x, y = cx - w / 2.0, cy - h / 2.0
    fill = _attr(node.get("fill") or "#ffffff")
    stroke = _attr(node.get("stroke") or "#444444")
    sw = float(node.get("stroke_width", 1.1))
    dash = ' stroke-dasharray="5 3"' if node.get("dashed") else ""
    shape = node.get("shape") or "rect"
    if shape == "circle":
        return ('<circle cx="%.2f" cy="%.2f" r="%.2f" fill="%s" stroke="%s" '
                'stroke-width="%.2f"%s/>' %
                (cx, cy, min(w, h) / 2.0, fill, stroke, sw, dash))
    if shape == "ellipse":
        return ('<ellipse cx="%.2f" cy="%.2f" rx="%.2f" ry="%.2f" fill="%s" '
                'stroke="%s" stroke-width="%.2f"%s/>' %
                (cx, cy, w / 2.0, h / 2.0, fill, stroke, sw, dash))
    if shape == "hexagon":
        points = " ".join("%.2f,%.2f" % (
            cx + (w / 2.0) * math.cos(math.radians(angle)),
            cy + (h / 2.0) * math.sin(math.radians(angle)))
            for angle in (0, 60, 120, 180, 240, 300))
        return ('<polygon points="%s" fill="%s" stroke="%s" stroke-width="%.2f"%s/>' %
                (points, fill, stroke, sw, dash))
    if shape == "diamond":
        points = "%.2f,%.2f %.2f,%.2f %.2f,%.2f %.2f,%.2f" % (
            cx, y, x + w, cy, cx, y + h, x, cy)
        return ('<polygon points="%s" fill="%s" stroke="%s" stroke-width="%.2f"%s/>' %
                (points, fill, stroke, sw, dash))
    radius = min(w, h) * 0.18 if shape == "rounded" else 0.0
    return ('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="%.2f" ry="%.2f" '
            'fill="%s" stroke="%s" stroke-width="%.2f"%s/>' %
            (x, y, w, h, radius, radius, fill, stroke, sw, dash))


def _edge_style(effect, inferred):
    if inferred:
        base = effect if effect in REPORTED_EFFECTS else "activate"
        if base == "inhibit":
            return "#7a7a7a", "inhibit-gray", 1.3, "4 3"
        return "#7a7a7a", "arrow-gray", 1.3, "4 3"
    styles = {
        "activate": ("#0072B2", "arrow-blue", 1.6, None),
        "inhibit": ("#D55E00", "inhibit-orange", 1.6, None),
        "bind": ("#5f6b73", None, 1.2, None),
        "translocate": ("#264653", "arrow-dark", 1.4, "5 3"),
        "modify": ("#7B3294", "arrow-purple", 1.5, None),
        "association": ("#7a7a7a", None, 1.0, "1 3"),
        "unknown_link": ("#7a7a7a", "arrow-gray", 1.2, "5 3"),
    }
    return styles.get(effect, styles["activate"])


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------
def render_spec(spec):
    width_mm = float(spec.get("width_mm", 180))
    height_mm = float(spec.get("height_mm", 120))
    width = width_mm * MM_TO_PX
    height = height_mm * MM_TO_PX
    font_family = spec.get("font_family") or "Liberation Sans"
    font_size = float(spec.get("font_size_pt", 8)) * PT_TO_PX
    direction = str(spec.get("direction", "LR")).upper()
    margin = max(13.0, font_size * 1.7)
    title = str(spec.get("title") or "")
    title_lines, _title_sub, title_size, _title_fit = _fit_text(
        title, "", max(60.0, width - 2 * margin), font_size * 1.08, max_lines=2)
    label_reserve = font_size * 1.35 + max(8.0, font_size * 1.1)
    top = (margin + label_reserve +
           (len(title_lines) * title_size * 1.28 if title else 0.0))
    legend = spec.get("legend") or []
    footnote_lines = []
    footnote_width = (width - 2 * margin) * 0.9
    for text in list(spec.get("disclaimers") or [])[:4]:
        footnote_lines.extend(wrap_label(str(text), footnote_width,
                                         font_size * 0.76))
    if spec.get("footnote"):
        footnote_lines.extend(wrap_label(str(spec["footnote"]),
                                         footnote_width, font_size * 0.76))
    legend_size = font_size * 0.88
    legend_rows = 1
    if legend:
        cursor = margin
        for item in legend:
            label = str(item.get("label") or item.get("effect") or "")
            item_w = (legend_size * 2.7 + text_width(label, legend_size) +
                      legend_size * 1.8)
            if cursor > margin and cursor + item_w > width - margin:
                legend_rows += 1
                cursor = margin
            cursor += item_w + legend_size * 1.8
    legend_h = legend_rows * legend_size * 1.55 if legend else 0.0
    bottom = (margin + legend_h + legend_size * 0.6 +
              len(footnote_lines) * font_size * 1.02)
    usable_w = max(80.0, width - 2 * margin)
    usable_h = max(60.0, height - top - bottom)

    nodes = [dict(n) for n in spec.get("nodes") or []]
    edges = [dict(e) for e in spec.get("edges") or []]
    groups = spec.get("groups") or []
    by_id = {str(n["id"]): n for n in nodes}
    ranks = sorted({int(n.get("rank", 0)) for n in nodes})
    rank_index = {rank: index for index, rank in enumerate(ranks)}
    by_rank = {}
    for node in nodes:
        by_rank.setdefault(int(node.get("rank", 0)), []).append(node)
    for members in by_rank.values():
        members.sort(key=lambda n: (int(n.get("order", 0)), str(n.get("id"))))

    if direction == "TB":
        rows = max(1, len(ranks))
        cols = max(1, max(len(v) for v in by_rank.values()))
        cell_w = usable_w / cols
        cell_h = usable_h / rows
    else:
        cols = max(1, len(ranks))
        rows = max(1, max(len(v) for v in by_rank.values()))
        cell_w = usable_w / cols
        cell_h = usable_h / rows

    text_fit_all = True
    for rank, members in by_rank.items():
        count = len(members)
        for index, node in enumerate(members):
            if direction == "TB":
                node["_cx"] = margin + usable_w / 2.0 + (index - (count - 1) / 2.0) * cell_w
                node["_cy"] = top + cell_h * (rank_index[rank] + 0.5)
                max_text = max(42.0, min(cell_w - 14.0,
                                         float(node.get("max_text_px", 118))))
            else:
                node["_cx"] = margin + cell_w * (rank_index[rank] + 0.5)
                node["_cy"] = top + usable_h / 2.0 + (index - (count - 1) / 2.0) * cell_h
                max_text = max(42.0, min(cell_w - 12.0,
                                         float(node.get("max_text_px", 118))))
            lines, sub_lines, size, fit = _fit_text(
                str(node.get("label") or ""), str(node.get("sublabel") or ""),
                max_text, font_size)
            text_fit_all = text_fit_all and fit
            node["_lines"] = lines
            node["_sub_lines"] = sub_lines
            node["_text_size"] = size
            node["_text_fit"] = fit
            node["_w"] = min(max(46.0, max(text_width(line, size)
                                            for line in lines) + 20.0),
                             max(46.0, cell_w - 10.0))
            node["_h"] = min(46.0 if sub_lines else 34.0,
                             max(26.0, cell_h - 10.0))
            node["_box"] = (node["_cx"] - node["_w"] / 2.0,
                            node["_cy"] - node["_h"] / 2.0,
                            node["_cx"] + node["_w"] / 2.0,
                            node["_cy"] + node["_h"] / 2.0)

    node_boxes = [n["_box"] for n in nodes]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" width="%.1fmm" height="%.1fmm" '
             'viewBox="0 0 %.2f %.2f" role="img">' %
             (width_mm, height_mm, width, height)]
    parts.append('<title>%s</title>' % escape(str(spec.get("title") or
                                                   spec.get("figure_id") or
                                                   "Pathway figure")))
    parts.append('<desc>%s</desc>' % escape(str(spec.get("alt_text") or "")))
    parts.append('<defs>')
    markers = (
        ("arrow-blue", "#0072B2", "arrow"),
        ("arrow-dark", "#264653", "arrow"),
        ("arrow-purple", "#7B3294", "arrow"),
        ("arrow-gray", "#7a7a7a", "arrow"),
        ("inhibit-orange", "#D55E00", "bar"),
        ("inhibit-gray", "#7a7a7a", "bar"),
    )
    for marker_id, color, kind in markers:
        if kind == "bar":
            parts.append('<marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" '
                         'markerWidth="5" markerHeight="5" orient="auto">'
                         '<path d="M5,1 L5,9" stroke="%s" stroke-width="2.2" '
                         'fill="none"/></marker>' % (marker_id, color))
        else:
            parts.append('<marker id="%s" viewBox="0 0 10 10" refX="9" refY="5" '
                         'markerWidth="6" markerHeight="6" orient="auto">'
                         '<path d="M1,1 L9,5 L1,9 z" fill="%s"/></marker>' %
                         (marker_id, color))
    parts.append('</defs>')
    parts.append('<rect x="0" y="0" width="%.2f" height="%.2f" fill="#ffffff"/>' %
                 (width, height))

    # Compartment layers: bands when rank ranges are unique, else bounding boxes.
    ranges = []
    for group in groups:
        members = [by_id[mid] for mid in group.get("nodes") or [] if mid in by_id]
        if not members:
            continue
        idx = [rank_index[int(n.get("rank", 0))] for n in members]
        ranges.append((group, min(idx), max(idx)))
    band_keys = {}
    for group, lo, hi in ranges:
        band_keys.setdefault((lo, hi), []).append(group.get("id"))
    group_boxes = []
    for group, lo, hi in ranges:
        members = [by_id[mid] for mid in group.get("nodes") or [] if mid in by_id]
        unique_band = len(band_keys[(lo, hi)]) == 1
        pad = max(8.0, font_size * 1.1)
        if unique_band:
            if direction == "TB":
                x0 = min(n["_cx"] - n["_w"] / 2.0 for n in members) - pad
                x1 = max(n["_cx"] + n["_w"] / 2.0 for n in members) + pad
                y0 = top + cell_h * lo + 3.0
                y1 = top + cell_h * (hi + 1) - 3.0
            else:
                x0 = margin + cell_w * lo + 3.0
                x1 = margin + cell_w * (hi + 1) - 3.0
                y0 = top - pad - (font_size * 1.35 if group.get("label") else 0.0)
                y1 = top + usable_h + pad * 0.25
        else:
            x0 = min(n["_box"][0] for n in members) - pad
            x1 = max(n["_box"][2] for n in members) + pad
            y0 = min(n["_box"][1] for n in members) - pad - (
                font_size * 1.4 if group.get("label") else 0.0)
            y1 = max(n["_box"][3] for n in members) + pad
        area = max(1.0, (x1 - x0) * (y1 - y0))
        group_boxes.append((area, group, x0, y0, x1, y1))
    group_boxes.sort(key=lambda item: -item[0])
    for _area, group, x0, y0, x1, y1 in group_boxes:
        dash = ' stroke-dasharray="6 4"' if group.get("dashed", True) else ""
        parts.append('<g id="%s" data-role="compartment">' % _attr(group.get("id")))
        parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="%.2f" '
                     'fill="%s" fill-opacity="0.40" stroke="%s" stroke-width="0.9"%s/>' %
                     (x0, y0, x1 - x0, y1 - y0, font_size * 0.55,
                      _attr(group.get("fill") or "#f7f7f7"),
                      _attr(group.get("stroke") or "#b8b8b8"), dash))
        if group.get("label"):
            label_size = font_size * 0.9
            max_label_w = max(28.0, (x1 - x0) - font_size * 1.2)
            label_lines, _sub, label_size, _fit = _fit_text(
                str(group["label"]), "", max_label_w, label_size, max_lines=2)
            for line_index, line in enumerate(label_lines):
                parts.append('<text x="%.2f" y="%.2f" font-family="%s" '
                             'font-size="%.2f" fill="#555555" font-style="italic">%s'
                             '</text>' %
                             (x0 + font_size * 0.62,
                              y0 + label_size * (1.05 + 1.05 * line_index),
                              _attr(font_family), label_size, escape(line)))
        parts.append('</g>')

    # Edges (under nodes).
    label_boxes, label_overlap_free = [], True
    for edge in edges:
        geom = _edge_points(edge, by_id, direction)
        if not geom:
            continue
        effect = str(edge.get("effect") or "activate")
        inferred = str(edge.get("kind")) == "inferred" or effect == "inferred"
        color, marker, line_width, dash = _edge_style(
            str(edge.get("base_effect")) if inferred else effect, inferred)
        dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
        marker_attr = ' marker-end="url(#%s)"' % marker if marker else ""
        source = str(edge.get("source") or "not_provided")
        parts.append('<g id="%s" data-effect="%s" data-kind="%s" data-source="%s">' %
                     (_attr(edge.get("id")), _attr(edge.get("effect")),
                      _attr(edge.get("kind")), _attr(source)))
        parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" '
                     'stroke-linejoin="round"%s%s/>' %
                     (geom["d"], color, line_width, dash_attr, marker_attr))
        label = str(edge.get("label") or "")
        if label:
            size = font_size * 0.9
            x, y, box = _label_position(geom["points"], node_boxes, label, size)
            label_boxes.append(box)
            if any(_rects_overlap(box, nb, 1.0) for nb in node_boxes):
                label_overlap_free = False
            tw = text_width(label, size)
            parts.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" '
                         'fill="#ffffff" fill-opacity="0.94" stroke="none"/>' %
                         (x - tw / 2.0 - 2.0, y - size * 0.78,
                          tw + 4.0, size * 1.16))
            parts.append('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f" '
                         'text-anchor="middle" fill="%s">%s</text>' %
                         (x, y + size * 0.12, _attr(font_family), size, color,
                          escape(label)))
        parts.append('</g>')

    # Nodes.
    for node in nodes:
        nid = str(node.get("id"))
        parts.append('<g id="%s" data-shape="%s" data-entity-kind="%s" '
                     'data-source="%s">' %
                     (_attr(nid), _attr(node.get("shape") or "rect"),
                      _attr(node.get("entity_kind") or "protein"),
                      _attr(node.get("source") or "not_provided")))
        parts.append(_shape_markup(node))
        inner = node["_text_size"]
        line_h = inner * 1.24
        total = len(node["_lines"]) * line_h + len(node["_sub_lines"]) * line_h * 0.9
        ty = node["_cy"] - total / 2.0 + inner * 0.86
        italic = ' font-style="italic"' if node.get("italic") else ""
        for line in node["_lines"]:
            parts.append('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f" '
                         'text-anchor="middle" fill="#222222"%s>%s</text>' %
                         (node["_cx"], ty, _attr(font_family), inner, italic,
                          escape(line)))
            ty += line_h
        for line in node["_sub_lines"]:
            ty += line_h * 0.08
            parts.append('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f" '
                         'text-anchor="middle" fill="#444444" fill-opacity="0.9">%s</text>' %
                         (node["_cx"], ty, _attr(font_family), inner * 0.86,
                          escape(line)))
            ty += line_h * 0.9
        parts.append('</g>')

    if title:
        parts.append('<g id="header" data-role="header">')
        for line_index, line in enumerate(title_lines):
            parts.append('<text x="%.2f" y="%.2f" font-family="%s" '
                         'font-size="%.2f" font-weight="bold" fill="#222222">%s'
                         '</text>' %
                         (margin, margin + title_size * (1.1 + 1.28 * line_index),
                          _attr(font_family), title_size, escape(line)))
        parts.append('</g>')

    if legend:
        size = legend_size
        y = (height - margin - len(footnote_lines) * font_size * 1.02 -
             (legend_rows - 1) * size * 1.45 - size * 0.6)
        x = margin
        parts.append('<g id="legend" data-role="legend">')
        for item in legend:
            effect = str(item.get("effect") or "activate")
            label = str(item.get("label") or effect)
            item_w = size * 2.7 + text_width(label, size)
            if x > margin and x + item_w > width - margin:
                x = margin
                y += size * 1.45
            color, marker, line_width, dash = _edge_style(effect, effect == "inferred")
            dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
            marker_attr = ' marker-end="url(#%s)"' % marker if marker else ""
            parts.append('<g data-effect="%s" data-role="legend-item">' % _attr(effect))
            parts.append('<path d="M%.2f,%.2f L%.2f,%.2f" fill="none" stroke="%s" '
                         'stroke-width="%.2f"%s%s/>' %
                         (x, y, x + size * 2.2, y, color, line_width,
                          dash_attr, marker_attr))
            parts.append('<text x="%.2f" y="%.2f" font-family="%s" font-size="%.2f" '
                         'fill="#333333">%s</text>' %
                         (x + size * 2.7, y + size * 0.34, _attr(font_family),
                          size, escape(label)))
            parts.append('</g>')
            x += item_w + size * 1.8
        parts.append('</g>')

    footnote_line_h = font_size * 1.02
    footnote_start = (height - margin -
                      max(0, len(footnote_lines) - 1) * footnote_line_h)
    for index, line in enumerate(footnote_lines):
        parts.append('<g id="footnote-%d" data-role="footnote"><text x="%.2f" '
                     'y="%.2f" font-family="%s" font-size="%.2f" fill="#666666">%s'
                     '</text></g>' %
                     (index + 1, margin,
                      footnote_start + index * footnote_line_h,
                      _attr(font_family), font_size * 0.76, escape(line)))
    parts.append('</svg>')

    layout = {
        "canvas_px": [round(width, 2), round(height, 2)],
        "text_fit": bool(text_fit_all),
        "label_overlap_free": bool(label_overlap_free),
        "nodes": {str(n["id"]): {"box": [round(v, 2) for v in n["_box"]],
                                  "cx": round(n["_cx"], 2),
                                  "cy": round(n["_cy"], 2)}
                  for n in nodes},
        "label_boxes": [[round(v, 2) for v in box] for box in label_boxes],
    }
    return "\n".join(parts) + "\n", layout


def render_to_file(spec, out_svg):
    svg_text, layout = render_spec(spec)
    out = Path(out_svg)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg_text, encoding="utf-8")
    return layout


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Ledger -> editable pathway SVG")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out-spec", default=None)
    ap.add_argument("--out-svg", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    warnings = []
    try:
        data = normalize_ledger(load_json(args.ledger), warnings)
        errors, warns = validate_ledger(data)
        warnings.extend(x for x in warns if x not in warnings)
        if errors:
            print(json.dumps({"ok": False, "status": "input_error",
                              "errors": errors, "warnings": warnings},
                             ensure_ascii=False, indent=2))
            return 2
        spec = build_spec(data, warnings)
        layout = render_to_file(spec, args.out_svg)
        if args.out_spec:
            Path(args.out_spec).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out_spec).write_text(
                json.dumps({**spec, "layout": layout}, ensure_ascii=False, indent=2),
                encoding="utf-8")
        print(json.dumps({"ok": True, "status": "generated", "svg": args.out_svg,
                          "spec": args.out_spec, "layout": layout,
                          "warnings": warnings}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "status": "render_error",
                          "errors": ["%s: %s" % (type(exc).__name__, exc)]},
                         ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
