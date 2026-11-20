#!/usr/bin/env python3
"""Optional semantic audit for a biological pathway figure.

Usage:
    python3 check_pathway_result.py --pathway /app/work/pathway.json \
        --spec /app/work/figure-spec.json \
        --svg /app/output/figures/fig3.svg \
        --result /app/output/result.json \
        --materials-root /input --root /app/output --json
    python3 check_pathway_result.py --self-test --json

This script is an AUDIT, not a delivery gate.  A non-zero exit code means the
audit found things worth fixing; it never means "do not deliver".  Always keep
the real SVG and result.json and record audit failures in `exceptions`
(`audit_failed` / `audit_unavailable`).

Checks
  * entity table: unique ids, endpoints, effects, compartments, sources
  * source integrity: path/locator present; quoted text found in the material
  * relation semantics: inhibit=T-bar, translocate=dashed and cross-compartment,
    bind=no arrowhead, inferred=dashed and covered by a disclaimer
  * figure spec mirrors the table (ids, counts, kinds, groups, dashes)
  * SVG parses, keeps <text>, has viewBox, no external refs/scripts, and every
    node/edge group carries the expected shape/marker/dash/source
  * result.json contract (including media aliases), artifact files, content_map
    coverage, compliance_checks type, semantic_check counts and stale claims

Stdlib only.  Exit 0 = audit passed, 1 = audit problems, 2 = unreadable input.
"""
import argparse
import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

EFFECTS = {"activate", "inhibit", "translocate", "bind", "modify",
           "association", "unknown_link"}
KINDS = {"reported", "inferred"}
STRENGTHS = {"strong", "moderate", "weak", "speculative"}
EFFECT_KIND = {
    "activate": "activate", "inhibit": "inhibit", "translocate": "translocate",
    "bind": "bind", "modify": "default", "association": "bind",
    "unknown_link": "inferred",
}
TYPE_SHAPE = {
    "ligand": "circle", "receptor": "rounded", "kinase": "ellipse",
    "phosphatase": "ellipse", "gtpase": "ellipse", "tf": "hexagon",
    "complex": "rect", "metabolite": "diamond", "gene": "rect",
    "rna": "rect", "channel": "rounded", "cell": "rounded",
    "protein": "rounded", "other": "rect",
}
TYPE_ALIASES = {
    "配体": "ligand", "受体": "receptor", "激酶": "kinase",
    "磷酸酶": "phosphatase", "gtp酶": "gtpase", "小g蛋白": "gtpase",
    "转录因子": "tf", "复合物": "complex", "复合体": "complex",
    "代谢物": "metabolite", "基因": "gene", "通道": "channel",
    "细胞": "cell", "蛋白": "protein", "蛋白质": "protein",
    "其他": "other", "transcription_factor": "tf", "transcription factor": "tf",
    "enzyme": "protein", "small_molecule": "ligand", "ion": "metabolite",
}
MOVE_WORDS = ("translocat", "nuclear import", "nuclear export", "shuttle",
              "traffic", "transport", "import", "export", "secretion",
              "endocyt", "exocyt")
REQUIRED_RESULT = ("case_id", "task_family", "workorder", "status", "figure",
                   "artifacts", "pathway", "semantic_check", "checks",
                   "schematic_disclaimers", "omitted")
OFFICIAL_MEDIA_FIELDS = ("editable_source", "export_artifact", "content_map",
                         "compliance_checks", "exceptions")
MEDIA_STATUSES = {"answered", "partial", "insufficient_evidence", "input_error"}
LEGACY_STATUSES = {"completed", "failed"}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def norm(value):
    return " ".join(str(value or "").split())


def source_parts(value):
    if isinstance(value, dict):
        return (str(value.get("path") or "").strip(),
                str(value.get("locator") or "").strip(),
                str(value.get("quote") or "").strip())
    text = str(value or "").strip()
    return text, "", ""


def source_present(value):
    path, locator, _quote = source_parts(value)
    return bool(path or locator) and (path or locator).lower() not in (
        "not_provided", "unknown", "n/a")


def is_inferred_edge(edge):
    return (str(edge.get("kind") or "reported") == "inferred" or
            str(edge.get("effect") or "") == "unknown_link")


def is_reported_edge(edge):
    return not is_inferred_edge(edge)


def normalize_type(value):
    text = str(value or "").strip()
    low = text.lower()
    if low in TYPE_SHAPE:
        return low
    return TYPE_ALIASES.get(low, TYPE_ALIASES.get(text, "other"))


def local(tag):
    return tag.split("}")[-1]


def resolve_material(path, materials_root):
    p = Path(path)
    if p.is_absolute():
        return p
    if materials_root:
        return Path(materials_root) / p
    return p


def verify_source(owner, source, required, materials_root, problems, warnings, checks):
    path, locator, quote = source_parts(source)
    if not path and not locator:
        if required:
            problems.append("%s has no usable source" % owner)
        else:
            warnings.append("%s has no usable source" % owner)
        return False
    if not path:
        warnings.append("%s source has locator but no path" % owner)
        return False
    if not locator:
        warnings.append("%s source has no locator (page/section/figure)" % owner)
    if quote:
        material = resolve_material(path, materials_root)
        text_suffixes = {".md", ".txt", ".csv", ".json", ".html", ".htm",
                         ".xml", ".yaml", ".yml", ".tsv"}
        if not material.is_file():
            warnings.append("%s quote cannot be verified: %s not found" % (owner, path))
        elif material.suffix.lower() not in text_suffixes:
            warnings.append("%s quote not auto-verified for non-text source: %s" %
                            (owner, path))
        else:
            text = norm(material.read_text(encoding="utf-8", errors="replace"))
            if norm(quote) not in text:
                problems.append("%s quote not found in %s: %r" %
                                (owner, path, quote[:90]))
            else:
                checks["quotes_verified"] = checks.get("quotes_verified", 0) + 1
    elif required:
        warnings.append("%s has no quote; locator-only source" % owner)
    return True


def check_table(data, materials_root, problems, warnings, checks):
    if not isinstance(data, dict):
        problems.append("pathway root is not an object")
        return {}, {}, {}
    compartments = data.get("compartments") or []
    molecules = data.get("molecules") or []
    interactions = data.get("interactions") or []
    if not isinstance(compartments, list) or not compartments:
        problems.append("pathway has no compartments[]")
    if not isinstance(molecules, list) or not molecules:
        problems.append("pathway has no molecules[]")
    if not isinstance(interactions, list) or not interactions:
        problems.append("pathway has no interactions[]")
    comp_by_id, mol_by_id = {}, {}
    label_map = {}
    for index, comp in enumerate(compartments):
        if not isinstance(comp, dict):
            problems.append("compartment[%d] is not an object" % index)
            continue
        cid = str(comp.get("id") or "").strip()
        if not cid:
            problems.append("compartment[%d] missing id" % index)
            continue
        if cid in comp_by_id:
            problems.append("duplicate compartment id: %s" % cid)
        comp_by_id[cid] = comp
        if not comp.get("label"):
            warnings.append("compartment %s missing label" % cid)
        verify_source("compartment %s" % cid, comp.get("source"), False,
                      materials_root, problems, warnings, checks)
    for index, mol in enumerate(molecules):
        if not isinstance(mol, dict):
            problems.append("molecule[%d] is not an object" % index)
            continue
        mid = str(mol.get("id") or "").strip()
        if not mid:
            problems.append("molecule[%d] missing id" % index)
            continue
        if mid in mol_by_id:
            problems.append("duplicate molecule id: %s" % mid)
        mol_by_id[mid] = mol
        if not mol.get("label"):
            problems.append("molecule %s missing label" % mid)
        mtype = normalize_type(mol.get("type"))
        if mtype == "other" and str(mol.get("type") or "").strip().lower() not in TYPE_SHAPE:
            warnings.append("molecule %s has unknown type: %s" % (mid, mol.get("type")))
        cid = str(mol.get("compartment") or "").strip()
        if cid not in comp_by_id:
            problems.append("molecule %s references unknown compartment: %s" % (mid, cid))
        label = norm(mol.get("label")).lower()
        if label:
            if label in label_map:
                warnings.append("molecules %s and %s share label %r; add a sublabel"
                                % (label_map[label], mid, mol.get("label")))
            label_map[label] = mid
        entity_kind = str(mol.get("entity_kind") or "").lower()
        if entity_kind == "gene" and mtype != "gene":
            warnings.append("molecule %s entity_kind=gene but type=%s" % (mid, mtype))
        if mtype == "gene" and entity_kind and entity_kind != "gene":
            warnings.append("molecule %s type=gene but entity_kind=%s" % (mid, entity_kind))
        if mtype == "receptor":
            label_text = str((comp_by_id.get(cid) or {}).get("label") or "").lower()
            if "membrane" not in label_text and "膜" not in label_text:
                warnings.append("receptor %s is not placed in a membrane compartment" % mid)
        if mtype == "tf":
            label_text = str((comp_by_id.get(cid) or {}).get("label") or "").lower()
            if "nucle" not in label_text and "核" not in label_text:
                warnings.append("transcription factor %s is not placed in the nucleus" % mid)
        if mtype == "ligand" and "nucle" in str(
                (comp_by_id.get(cid) or {}).get("label") or "").lower():
            warnings.append("ligand %s is placed in the nucleus" % mid)
        verify_source("molecule %s" % mid, mol.get("source"), True,
                      materials_root, problems, warnings, checks)
        strength = mol.get("evidence_strength")
        if strength and str(strength) not in STRENGTHS:
            warnings.append("molecule %s has unknown evidence_strength: %s" % (mid, strength))

    int_by_id = {}
    for index, edge in enumerate(interactions):
        if not isinstance(edge, dict):
            problems.append("interaction[%d] is not an object" % index)
            continue
        iid = str(edge.get("id") or "").strip()
        if not iid:
            problems.append("interaction[%d] missing id" % index)
            continue
        if iid in int_by_id:
            problems.append("duplicate interaction id: %s" % iid)
        int_by_id[iid] = edge
        src = str(edge.get("from") or "").strip()
        dst = str(edge.get("to") or "").strip()
        if src not in mol_by_id:
            problems.append("interaction %s has unknown from: %s" % (iid, src))
        if dst not in mol_by_id:
            problems.append("interaction %s has unknown to: %s" % (iid, dst))
        effect = str(edge.get("effect") or "").strip()
        if effect not in EFFECTS:
            problems.append("interaction %s has unknown effect: %s" % (iid, effect))
        kind = str(edge.get("kind") or "reported").strip()
        if kind not in KINDS:
            problems.append("interaction %s has unknown kind: %s" % (iid, kind))
        inferred = is_inferred_edge(edge)
        if inferred and kind == "reported" and effect == "unknown_link":
            warnings.append("interaction %s is unknown_link; treated as inferred" % iid)
        strength = edge.get("evidence_strength")
        if strength and str(strength) not in STRENGTHS:
            warnings.append("interaction %s has unknown evidence_strength: %s" %
                            (iid, strength))
        if not inferred and str(strength) == "speculative":
            warnings.append("interaction %s is reported but marked speculative" % iid)
        required = not inferred
        verify_source("interaction %s" % iid, edge.get("source"), required,
                      materials_root, problems, warnings, checks)
        if src in mol_by_id and dst in mol_by_id:
            src_comp = str(mol_by_id[src].get("compartment") or "")
            dst_comp = str(mol_by_id[dst].get("compartment") or "")
            mechanism = str(edge.get("mechanism") or "").lower()
            if effect == "translocate":
                if src_comp == dst_comp:
                    problems.append("translocation %s does not cross compartments" % iid)
                if kind == "reported" and not mechanism:
                    warnings.append("translocation %s has no mechanism" % iid)
            elif effect in ("activate", "inhibit", "modify") and src_comp != dst_comp:
                if any(word in mechanism for word in MOVE_WORDS):
                    problems.append(
                        "interaction %s crosses compartments and mentions transport; "
                        "use effect=translocate" % iid)
                else:
                    warnings.append(
                        "interaction %s crosses compartments as signaling; confirm "
                        "the molecule itself does not move" % iid)
    checks["compartments"] = len(compartments)
    checks["molecules"] = len(molecules)
    checks["interactions"] = len(interactions)
    checks["reported"] = sum(1 for e in interactions
                             if isinstance(e, dict) and is_reported_edge(e))
    checks["inferred"] = sum(1 for e in interactions
                             if isinstance(e, dict) and is_inferred_edge(e))
    checks["inhibitions"] = sum(1 for e in interactions
                                if isinstance(e, dict)
                                and str(e.get("effect")) == "inhibit")
    checks["translocations"] = sum(1 for e in interactions
                                   if isinstance(e, dict)
                                   and str(e.get("effect")) == "translocate")
    return comp_by_id, mol_by_id, int_by_id


def check_spec(spec, comp_by_id, mol_by_id, int_by_id, problems, warnings, checks):
    if not isinstance(spec, dict):
        problems.append("figure spec root is not an object")
        return
    nodes = spec.get("nodes") or []
    edges = spec.get("edges") or []
    groups = spec.get("groups") or []
    node_by_id, edge_by_id = {}, {}
    for index, node in enumerate(nodes):
        nid = str(node.get("id") or "")
        if not nid:
            problems.append("spec node[%d] missing id" % index)
            continue
        if nid in node_by_id:
            problems.append("duplicate spec node id: %s" % nid)
        node_by_id[nid] = node
        if nid not in mol_by_id:
            problems.append("spec has extra node not in pathway: %s" % nid)
        if not node.get("label"):
            problems.append("spec node %s missing label" % nid)
        if not source_present(node.get("source")):
            warnings.append("spec node %s has no usable source" % nid)
        expected_shape = TYPE_SHAPE.get(normalize_type((mol_by_id.get(nid) or {}).get("type")))
        if expected_shape and str(node.get("shape")) != expected_shape:
            warnings.append("spec node %s shape=%s, expected %s for its type" %
                            (nid, node.get("shape"), expected_shape))
    for mid in mol_by_id:
        if mid not in node_by_id:
            problems.append("molecule %s is missing from the figure spec" % mid)
    for index, edge in enumerate(edges):
        eid = str(edge.get("id") or "")
        if not eid:
            problems.append("spec edge[%d] missing id" % index)
            continue
        if eid in edge_by_id:
            problems.append("duplicate spec edge id: %s" % eid)
        edge_by_id[eid] = edge
        if eid not in int_by_id:
            problems.append("spec has extra edge not in pathway: %s" % eid)
            continue
        source = int_by_id[eid]
        effect = str(source.get("effect"))
        inferred = is_inferred_edge(source)
        expected_kind = "inferred" if inferred else EFFECT_KIND.get(effect)
        if expected_kind and str(edge.get("kind")) != expected_kind:
            problems.append("spec edge %s kind=%s, expected %s for effect=%s" %
                            (eid, edge.get("kind"), expected_kind, effect))
        if str(edge.get("from")) != str(source.get("from")) or \
                str(edge.get("to")) != str(source.get("to")):
            problems.append("spec edge %s endpoints differ from pathway" % eid)
        if inferred and not edge.get("dashed"):
            problems.append("spec edge %s is inferred but not dashed" % eid)
        if not inferred and not source_present(edge.get("source")):
            warnings.append("spec edge %s has no usable source" % eid)
    for iid in int_by_id:
        if iid not in edge_by_id:
            problems.append("interaction %s is missing from the figure spec" % iid)
    group_by_id = {}
    for index, group in enumerate(groups):
        gid = str(group.get("id") or "")
        if not gid:
            problems.append("spec group[%d] missing id" % index)
            continue
        group_by_id[gid] = group
        for nid in group.get("nodes") or []:
            if str(nid) not in node_by_id:
                problems.append("spec group %s references missing node %s" % (gid, nid))
    for cid, comp in comp_by_id.items():
        members = {mid for mid, mol in mol_by_id.items()
                   if str(mol.get("compartment")) == cid}
        if not members:
            continue
        group = group_by_id.get(cid)
        if not group:
            problems.append("compartment %s has no group in the figure spec" % cid)
            continue
        if {str(x) for x in group.get("nodes") or []} != members:
            problems.append("group %s membership does not match compartment %s" %
                            (cid, cid))
    linked = set()
    for edge in edges:
        linked.update((str(edge.get("from")), str(edge.get("to"))))
    for node in nodes:
        if str(node.get("id")) not in linked and len(nodes) > 1:
            warnings.append("spec node %s is isolated" % node.get("id"))
    checks["spec_nodes"] = len(nodes)
    checks["spec_edges"] = len(edges)
    checks["spec_groups"] = len(groups)


def check_svg(svg_path, spec, mol_by_id, int_by_id, problems, warnings, checks):
    p = Path(svg_path)
    if not p.is_file():
        problems.append("missing SVG: %s" % p)
        return
    raw = p.read_text(encoding="utf-8", errors="replace")
    if not raw.strip():
        problems.append("SVG is empty")
        return
    lowered = raw.lower()
    if "<script" in lowered:
        problems.append("SVG contains <script>")
    if "href=\"http" in lowered or "href='http" in lowered:
        problems.append("SVG has an external http(s) reference")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        problems.append("SVG is not well-formed: %s" % exc)
        return
    if local(root.tag) != "svg":
        problems.append("SVG root is not <svg>")
    if not root.get("viewBox"):
        problems.append("SVG missing viewBox")
    if raw.count("<text") == 0:
        problems.append("SVG has no <text> elements")
    groups = {}
    for elem in root.iter():
        if local(elem.tag) != "g":
            continue
        gid = elem.get("id")
        if not gid:
            continue
        path_el = next((child for child in elem.iter() if local(child.tag) == "path"), None)
        texts = []
        for child in elem.iter():
            if local(child.tag) == "text":
                texts.append(child)
        groups[gid] = {
            "data_shape": elem.get("data-shape"),
            "data_kind": elem.get("data-kind"),
            "data_source": elem.get("data-source") or "",
            "path": path_el,
            "texts": texts,
        }
    node_groups = {gid for gid, item in groups.items() if item["data_shape"]}
    edge_groups = {gid for gid, item in groups.items() if item["data_kind"]}
    for mid, mol in mol_by_id.items():
        item = groups.get(mid)
        if not item or not item["data_shape"]:
            problems.append("SVG has no node group for molecule %s" % mid)
            continue
        if spec and isinstance(spec, dict):
            spec_node = next((n for n in spec.get("nodes") or []
                              if str(n.get("id")) == mid), None)
            if spec_node and item["data_shape"] != str(spec_node.get("shape")):
                problems.append("SVG node %s shape=%s, spec says %s" %
                                (mid, item["data_shape"], spec_node.get("shape")))
        if source_present(mol.get("source")) and not item["data_source"].strip():
            warnings.append("SVG node %s has no data-source" % mid)
        if str(mol.get("entity_kind") or "").lower() == "gene":
            italic = any(t.get("font-style") == "italic" for t in item["texts"])
            if not italic:
                warnings.append("gene node %s is not italic in the SVG" % mid)
    for iid, edge in int_by_id.items():
        item = groups.get(iid)
        if not item or not item["data_kind"]:
            problems.append("SVG has no edge group for interaction %s" % iid)
            continue
        effect = str(edge.get("effect"))
        inferred = is_inferred_edge(edge)
        expected_kind = "inferred" if inferred else EFFECT_KIND.get(effect)
        if expected_kind and item["data_kind"] != expected_kind:
            problems.append("SVG edge %s data-kind=%s, expected %s" %
                            (iid, item["data_kind"], expected_kind))
        path_el = item["path"]
        if path_el is None:
            problems.append("SVG edge %s has no path" % iid)
            continue
        style = path_el.get("style") or ""
        marker_end = path_el.get("marker-end") or ""
        has_marker = bool(marker_end) or "marker-end" in style
        marker = (marker_end + " " + style).lower()
        dash = ("stroke-dasharray" in path_el.attrib) or ("stroke-dasharray" in style)
        if inferred:
            if not dash:
                problems.append("SVG edge %s is inferred but not dashed" % iid)
            if not has_marker:
                problems.append("SVG edge %s is inferred but has no arrowhead" % iid)
        else:
            if effect == "inhibit" and "arrow-inhibit" not in marker:
                problems.append("SVG edge %s is an inhibition without a T-bar marker" % iid)
            if effect == "activate" and "arrow" not in marker:
                problems.append("SVG edge %s is an activation without an arrowhead" % iid)
            if effect in ("bind", "association") and has_marker:
                problems.append("SVG edge %s is a binding with an arrowhead" % iid)
            if effect == "translocate" and not dash:
                problems.append("SVG edge %s is a translocation but not dashed" % iid)
        if not inferred and source_present(edge.get("source")) and \
                not item["data_source"].strip():
            warnings.append("SVG edge %s has no data-source" % iid)
    checks["svg_text_elements"] = raw.count("<text")
    checks["svg_named_groups"] = len(groups)
    checks["svg_node_groups"] = len(node_groups)
    checks["svg_edge_groups"] = len(edge_groups)
    if len(node_groups) < len(mol_by_id):
        problems.append("SVG has %d node groups for %d molecules" %
                        (len(node_groups), len(mol_by_id)))
    if len(edge_groups) < len(int_by_id):
        problems.append("SVG has %d edge groups for %d interactions" %
                        (len(edge_groups), len(int_by_id)))


def png_size(path):
    p = Path(path)
    if not p.is_file():
        return None
    raw = p.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or len(raw) < 24:
        return None
    return [int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")]


def check_result(result, table, spec, svg_path, root, problems, warnings, checks):
    if not isinstance(result, dict):
        problems.append("result root is not an object")
        return
    for key in REQUIRED_RESULT:
        if key not in result:
            problems.append("result.json missing %s" % key)
    if str(result.get("case_id")) != str(table.get("case_id")):
        problems.append("result.case_id does not match pathway.case_id")
    if result.get("task_family") not in (None, "pathway"):
        warnings.append("result.task_family is not 'pathway': %s" %
                        result.get("task_family"))
    if result.get("workorder") not in (None, "biological_mechanism_pathway"):
        warnings.append("result.workorder is not biological_mechanism_pathway")
    status = str(result.get("status") or "")
    if status not in MEDIA_STATUSES:
        if status in LEGACY_STATUSES:
            warnings.append("result.status=%r is legacy; official media vocabulary "
                            "is answered|partial|insufficient_evidence|input_error" % status)
        else:
            problems.append("result.status is not a recognized value: %s" % status)
    for key in OFFICIAL_MEDIA_FIELDS:
        if key not in result:
            problems.append("result.json missing official media field: %s" % key)
    if not isinstance(result.get("compliance_checks"), dict) and \
            "compliance_checks" in result:
        problems.append("result.compliance_checks must be an object, not %s" %
                        type(result.get("compliance_checks")).__name__)
    if "exceptions" in result and not isinstance(result.get("exceptions"), list):
        problems.append("result.exceptions must be a list")
    figure = result.get("figure") or {}
    expected = {
        "compartments": len(table.get("compartments") or []),
        "molecules": len(table.get("molecules") or []),
        "interactions": len(table.get("interactions") or []),
        "reported": checks.get("reported", 0),
        "inferred": checks.get("inferred", 0),
    }
    for key, value in expected.items():
        if key in figure and int(figure[key]) != value:
            problems.append("result.figure.%s=%s but pathway has %d" %
                            (key, figure[key], value))
    if spec and isinstance(spec, dict) and figure.get("canvas_mm"):
        canvas = figure["canvas_mm"]
        if (len(canvas) != 2 or float(canvas[0]) != float(spec.get("width_mm")) or
                float(canvas[1]) != float(spec.get("height_mm"))):
            warnings.append("result.figure.canvas_mm does not match the spec")
    if not figure.get("layout_type"):
        warnings.append("result.figure.layout_type is missing")

    pathway = result.get("pathway") or {}
    for key in ("compartments", "molecules", "interactions"):
        items = pathway.get(key)
        if not isinstance(items, list) or not items:
            problems.append("result.pathway.%s is missing or empty" % key)
            continue
        result_ids = {str(item.get("id")) for item in items if isinstance(item, dict)}
        table_ids = {str(item.get("id")) for item in table.get(key) or []}
        if result_ids != table_ids:
            problems.append("result.pathway.%s ids differ from the entity table" % key)

    artifacts = result.get("artifacts") or []
    if not isinstance(artifacts, list) or not artifacts:
        problems.append("result.artifacts is missing or empty")
    roles = {}
    base = Path(root) if root else Path(result.get("_path") or ".")

    def resolve_artifact(rel):
        candidate = Path(str(rel))
        if candidate.is_absolute():
            return candidate
        first = base / candidate
        if first.is_file():
            return first
        if root:
            second = Path(root) / candidate
            if second.is_file():
                return second
        return first

    for index, item in enumerate(artifacts):
        if not isinstance(item, dict):
            problems.append("artifact[%d] is not an object" % index)
            continue
        role = str(item.get("role") or "").lower()
        rel = item.get("path")
        if not rel:
            problems.append("artifact[%d] missing path" % index)
            continue
        candidate = resolve_artifact(rel)
        if not candidate.is_file():
            problems.append("artifact missing: %s" % rel)
            continue
        roles.setdefault(role, candidate)
        fmt = str(item.get("format") or candidate.suffix.lstrip(".")).lower()
        if role == "preview":
            if fmt == "png":
                size = png_size(candidate)
                if not size:
                    problems.append("preview is not a valid PNG: %s" % rel)
                else:
                    checks["preview_size"] = size
                    if item.get("width_px") and int(item["width_px"]) != size[0]:
                        warnings.append("preview width_px does not match the file")
                    if item.get("height_px") and int(item["height_px"]) != size[1]:
                        warnings.append("preview height_px does not match the file")
                    if item.get("dpi") in (None, ""):
                        warnings.append("preview artifact has no dpi; record the "
                                        "export resolution")
            elif fmt == "html":
                text = candidate.read_text(encoding="utf-8", errors="replace")
                if "<svg" not in text:
                    warnings.append("HTML preview does not inline the SVG: %s" % rel)
            else:
                warnings.append("unexpected preview format: %s" % fmt)
        elif fmt == "pdf":
            if candidate.read_bytes()[:4] != b"%PDF":
                problems.append("PDF artifact has no %%PDF header: %s" % rel)
        elif fmt == "svg":
            text = candidate.read_text(encoding="utf-8", errors="replace")
            if "<svg" not in text:
                problems.append("SVG artifact has no <svg> root: %s" % rel)
    if "editable" not in roles:
        problems.append("result.artifacts missing role=editable")
    if "preview" not in roles:
        if status == "answered":
            warnings.append("answered result has no preview artifact; export a PNG "
                            "or honest HTML preview")
        else:
            warnings.append("result has no preview artifact")
    if svg_path and "editable" in roles:
        if Path(svg_path).resolve() != roles["editable"].resolve():
            warnings.append("result editable artifact differs from --svg")
    checks["artifact_roles"] = sorted(roles)

    editable = result.get("editable_source")
    if editable is None:
        if status != "input_error":
            problems.append("result.editable_source is null but status is %s" % status)
    elif isinstance(editable, dict):
        path = editable.get("path")
        if not path:
            problems.append("result.editable_source has no path")
        else:
            candidate = resolve_artifact(path)
            if not candidate.is_file():
                problems.append("editable_source does not exist: %s" % path)
            elif candidate.suffix.lower() != ".svg":
                problems.append("editable_source is not an SVG: %s" % path)
            else:
                text = candidate.read_text(encoding="utf-8", errors="replace")
                if "<svg" not in text:
                    problems.append("editable_source has no <svg> root: %s" % path)
    else:
        problems.append("result.editable_source must be an object or null")

    export = result.get("export_artifact")
    if export is None:
        if status == "answered":
            problems.append("answered result has export_artifact=null; use partial "
                            "when no real PDF/PNG exists")
    elif isinstance(export, dict):
        path = export.get("path")
        fmt = str(export.get("format") or "").lower()
        if not path:
            problems.append("result.export_artifact has no path")
        elif fmt not in ("pdf", "png"):
            problems.append("export_artifact format must be pdf or png, not %r" % fmt)
        else:
            candidate = resolve_artifact(path)
            if not candidate.is_file():
                problems.append("export_artifact does not exist: %s" % path)
            elif fmt == "pdf" and candidate.read_bytes()[:4] != b"%PDF":
                problems.append("export_artifact is not a valid PDF: %s" % path)
            elif fmt == "png" and not png_size(candidate):
                problems.append("export_artifact is not a valid PNG: %s" % path)
    else:
        problems.append("result.export_artifact must be an object or null")

    content_map = result.get("content_map")
    if not isinstance(content_map, list):
        problems.append("result.content_map must be a list")
    else:
        mapped = {str(item.get("element_id")) for item in content_map
                  if isinstance(item, dict)}
        expected_ids = ({str(x.get("id")) for x in table.get("compartments") or []}
                        | {str(x.get("id")) for x in table.get("molecules") or []}
                        | {str(x.get("id")) for x in table.get("interactions") or []})
        missing = sorted(expected_ids - mapped)
        if missing:
            problems.append("content_map does not cover: %s" % ", ".join(missing))
        for item in content_map:
            if not isinstance(item, dict):
                problems.append("content_map item is not an object")
                continue
            if item.get("kind") in ("reported", "inferred") and \
                    not source_present(item.get("maps_to")):
                warnings.append("content_map %s is %s but has no usable source" %
                                (item.get("element_id"), item.get("kind")))

    semantic = result.get("semantic_check") or {}
    computed_reported = checks.get("reported", 0)
    computed_inferred = checks.get("inferred", 0)
    computed_inhibitions = checks.get("inhibitions", 0)
    if semantic.get("arrows_reported") not in (None, computed_reported):
        problems.append("semantic_check.arrows_reported=%s but table has %d" %
                        (semantic.get("arrows_reported"), computed_reported))
    if semantic.get("arrows_inferred") not in (None, computed_inferred):
        problems.append("semantic_check.arrows_inferred=%s but table has %d" %
                        (semantic.get("arrows_inferred"), computed_inferred))
    computed_sourced = all(
        source_present(edge.get("source"))
        for edge in table.get("interactions") or []
        if isinstance(edge, dict) and is_reported_edge(edge))
    claimed_sourced = semantic.get("all_effects_sourced")
    if claimed_sourced is True and not computed_sourced:
        problems.append("semantic_check.all_effects_sourced=true but some reported "
                        "interactions have no source")
    if claimed_sourced is False and computed_sourced:
        warnings.append("semantic_check.all_effects_sourced=false but all reported "
                        "interactions have sources")
    if semantic.get("all_elements_sourced") is False:
        warnings.append("semantic_check.all_elements_sourced=false: some interaction "
                        "has no source; keep status=partial or add the missing source")
    if status in ("completed", "answered") and not computed_sourced:
        problems.append("answered/completed result has reported interactions "
                        "without a source")
    total_inhibitions = semantic.get("inhibitions_total", computed_inhibitions)
    if total_inhibitions not in (None, computed_inhibitions):
        problems.append("semantic_check.inhibitions_total=%s but table has %d" %
                        (total_inhibitions, computed_inhibitions))
    verified = int(semantic.get("inhibitions_verified") or 0)
    if verified > computed_inhibitions:
        problems.append("semantic_check.inhibitions_verified exceeds the number of "
                        "inhibition edges")
    if status in ("completed", "answered") and verified < computed_inhibitions:
        problems.append("answered/completed result has unverified inhibitions (%d/%d)" %
                        (verified, computed_inhibitions))
    if status in ("completed", "answered") and semantic.get("reviewed") is not True:
        problems.append("answered/completed result is not marked reviewed")
    if status == "partial" and computed_inhibitions and verified < computed_inhibitions:
        warnings.append("partial result keeps %d/%d inhibitions verified" %
                        (verified, computed_inhibitions))

    disclaimers = result.get("schematic_disclaimers") or []
    for edge in table.get("interactions") or []:
        if not isinstance(edge, dict) or not is_inferred_edge(edge):
            continue
        iid = str(edge.get("id"))
        if not any(iid in str(item) for item in disclaimers):
            problems.append("inferred interaction %s has no disclaimer" % iid)
    for index, item in enumerate(result.get("omitted") or []):
        if not isinstance(item, dict):
            problems.append("omitted[%d] is not an object" % index)
            continue
        if not item.get("reason"):
            problems.append("omitted[%d] has no reason" % index)
    legend = result.get("legend")
    if not isinstance(legend, dict) or not legend:
        warnings.append("result.legend is missing; explain line styles and encodings")
    else:
        present_effects = {str(edge.get("effect")) for edge in table.get("interactions") or []
                           if isinstance(edge, dict)}
        for item in legend.get("edge_semantics") or []:
            if isinstance(item, dict) and item.get("effect") not in present_effects:
                warnings.append("result.legend declares effect %s not present in "
                                "interactions" % item.get("effect"))
    if not isinstance(result.get("data_encoding"), list):
        warnings.append("result.data_encoding is missing; state whether values are encoded")

    claimed_checks = result.get("checks") or {}
    if claimed_checks.get("xml_valid") is False and checks.get("svg_text_elements"):
        warnings.append("result.checks.xml_valid is stale/false")
    if claimed_checks.get("text_as_text") is False:
        warnings.append("result.checks.text_as_text is false")
    if claimed_checks.get("external_refs") not in (None, 0):
        warnings.append("result.checks.external_refs is not zero")
    if claimed_checks.get("node_count") not in (None, expected["molecules"]):
        warnings.append("result.checks.node_count does not match the table")
    if claimed_checks.get("edge_count") not in (None, expected["interactions"]):
        warnings.append("result.checks.edge_count does not match the table")
    support = [str(x) for x in result.get("support_files_used") or []]
    for required in ("scripts/pathway_to_spec.py",):
        if not any(required in item for item in support):
            warnings.append("support_files_used missing %s" % required)
    if not isinstance(result.get("uncertainty"), list):
        warnings.append("result.uncertainty should be a list")
    if not isinstance(result.get("change_log"), list):
        warnings.append("result.change_log should be a list")


def run_checks(pathway_path, spec_path, svg_path, result_path, root, materials_root):
    problems, warnings, checks = [], [], {}
    try:
        table = load_json(pathway_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "problems": ["cannot read pathway: %s" % exc],
                "warnings": [], "checks": {}}
    comp_by_id, mol_by_id, int_by_id = check_table(
        table, materials_root, problems, warnings, checks)
    spec = None
    if spec_path:
        try:
            spec = load_json(spec_path)
        except (OSError, json.JSONDecodeError) as exc:
            problems.append("cannot read spec: %s" % exc)
        if isinstance(spec, dict):
            check_spec(spec, comp_by_id, mol_by_id, int_by_id,
                       problems, warnings, checks)
        else:
            problems.append("figure spec root is not an object")
    if svg_path:
        check_svg(svg_path, spec, mol_by_id, int_by_id, problems, warnings, checks)
    if result_path:
        try:
            result = load_json(result_path)
        except (OSError, json.JSONDecodeError) as exc:
            problems.append("cannot read result: %s" % exc)
            result = None
        if isinstance(result, dict):
            check_result(result, table, spec, svg_path, root,
                         problems, warnings, checks)
    upgraded = []
    for item in warnings:
        if item.startswith("interaction ") and "crosses compartments as signaling" in item:
            upgraded.append({"warning": item, "remedy":
                             "set effect=translocate if the molecule itself moves; "
                             "otherwise keep activate/inhibit and state the relay mechanism"})
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "checks": checks, "cross_compartment_notes": upgraded}


def _write_self_fixture(root, invalid=False):
    root = Path(root)
    (root / "materials").mkdir(parents=True, exist_ok=True)
    (root / "materials" / "notes.md").write_text(
        "Ligand L binds receptor R at the cell surface.\n", encoding="utf-8")
    quote = "Ligand L binds receptor R at the cell surface."
    table = {
        "case_id": "pathway-self-test",
        "compartments": [
            {"id": "C1", "label": "Extracellular", "source": {"path": "materials/notes.md",
                                                              "locator": "p.1"}},
            {"id": "C2", "label": "Plasma membrane", "source": {"path": "materials/notes.md",
                                                                "locator": "p.1"}},
        ],
        "molecules": [
            {"id": "M1", "label": "Ligand L", "type": "ligand", "compartment": "C1",
             "source": {"path": "materials/notes.md", "locator": "p.1", "quote": quote}},
            {"id": "M2", "label": "Receptor R", "type": "receptor", "compartment": "C2",
             "source": {"path": "materials/notes.md", "locator": "p.1", "quote": quote}},
        ],
        "interactions": [
            {"id": "I1", "from": "M1", "to": "M2", "effect": "bind",
             "kind": "reported", "mechanism": "binding",
             "source": {"path": "materials/notes.md", "locator": "p.1", "quote": quote}},
        ],
        "legend": {"edge_semantics": [{"effect": "bind", "label": "binding"}]},
        "data_encoding": [], "omitted": [], "conflicts": [], "disclaimers": [],
    }
    spec = {
        "figure_id": "Fig.self", "width_mm": 180, "height_mm": 90,
        "direction": "LR", "groups": [
            {"id": "C1", "label": "Extracellular", "nodes": ["M1"]},
            {"id": "C2", "label": "Plasma membrane", "nodes": ["M2"]}],
        "nodes": [{"id": "M1", "label": "Ligand L", "shape": "circle", "source": "m"},
                  {"id": "M2", "label": "Receptor R", "shape": "rounded", "source": "m"}],
        "edges": [{"id": "I1", "from": "M1", "to": "M2", "kind": "bind",
                   "label": "bind", "source": "m"}],
        "legend": [{"kind": "bind", "label": "binding"}],
    }
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
        '<g id="C1" data-role="compartment"><rect x="1" y="1" width="48" height="48" '
        'fill="#f7f7f7" stroke="#bbb"/></g>'
        '<g id="C2" data-role="compartment"><rect x="51" y="1" width="48" height="48" '
        'fill="#f4f4f4" stroke="#bbb"/></g>'
        '<g id="I1" data-kind="bind" data-source="m"><path d="M4,5 L20,5" '
        'fill="none" stroke="#666"/></g>'
        '<g id="M1" data-shape="circle" data-source="m"><text x="10" y="10">L</text></g>'
        '<g id="M2" data-shape="rounded" data-source="m"><text x="30" y="10">R</text></g>'
        '</svg>'
    )
    result = {
        "case_id": "pathway-self-test", "task_family": "pathway",
        "workorder": "biological_mechanism_pathway", "status": "answered",
        "task_status": "completed",
        "figure": {"figure_id": "Fig.self", "compartments": 2, "molecules": 2,
                   "interactions": 1, "canvas_mm": [180, 90], "layout_type": "LR"},
        "artifacts": [{"role": "editable", "path": "fig.svg", "format": "svg"},
                      {"role": "preview", "path": "fig.png", "format": "png",
                       "width_px": 10, "height_px": 10}],
        "pathway": {"compartments": table["compartments"],
                    "molecules": table["molecules"],
                    "interactions": table["interactions"]},
        "semantic_check": {"arrows_reported": 1, "arrows_inferred": 0,
                           "inhibitions_total": 0, "inhibitions_verified": 0,
                           "all_effects_sourced": True, "reviewed": True,
                           "removed_unsupported": []},
        "legend": table["legend"], "data_encoding": [], "omitted": [],
        "conflicts": [], "schematic_disclaimers": [],
        "checks": {"xml_valid": True, "text_as_text": True, "external_refs": 0,
                   "node_count": 2, "edge_count": 1},
        "support_files_used": ["scripts/pathway_to_spec.py"],
        "uncertainty": [], "change_log": [],
        "editable_source": {"path": "fig.svg", "format": "svg"},
        "export_artifact": {"path": "fig.png", "format": "png"},
        "content_map": [
            {"element_id": "C1", "element_type": "compartment",
             "kind": "context", "maps_to": {"path": "materials/notes.md",
                                            "locator": "p.1"}},
            {"element_id": "C2", "element_type": "compartment",
             "kind": "context", "maps_to": {"path": "materials/notes.md",
                                            "locator": "p.1"}},
            {"element_id": "M1", "element_type": "molecule", "kind": "reported",
             "maps_to": {"path": "materials/notes.md", "locator": "p.1"}},
            {"element_id": "M2", "element_type": "molecule", "kind": "reported",
             "maps_to": {"path": "materials/notes.md", "locator": "p.1"}},
            {"element_id": "I1", "element_type": "interaction", "kind": "reported",
             "maps_to": {"path": "materials/notes.md", "locator": "p.1"}},
        ],
        "compliance_checks": {"xml_valid": True, "text_as_text": True,
                              "export_artifact_present": True,
                              "status": "answered"},
        "exceptions": [],
    }
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 +
           (10).to_bytes(4, "big") + (10).to_bytes(4, "big") + b"\x00" * 16)
    if invalid:
        table["interactions"].append(
            {"id": "I2", "from": "M2", "to": "M1", "effect": "translocate",
             "kind": "reported", "mechanism": "transport",
             "source": {"path": "materials/notes.md", "locator": "p.9",
                        "quote": "This sentence does not exist."}})
        table["interactions"][0].pop("source", None)
        spec["edges"].append({"id": "I2", "from": "M2", "to": "M1",
                              "kind": "translocate", "label": "transport",
                              "source": "m"})
        spec["edges"][0].pop("source", None)
        svg = svg.replace(
            "</svg>",
            '<g id="I2" data-kind="translocate" data-source="m">'
            '<path d="M20,20 L4,20" fill="none" stroke="#333"/></g></svg>')
        result["figure"]["interactions"] = 2
        result["pathway"]["interactions"] = table["interactions"]
        result["semantic_check"]["arrows_reported"] = 2
        result["checks"]["edge_count"] = 2
        result["schematic_disclaimers"] = []
    (root / "pathway.json").write_text(json.dumps(table, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    (root / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (root / "fig.svg").write_text(svg, encoding="utf-8")
    (root / "fig.png").write_bytes(png)
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    return root


def self_test():
    with tempfile.TemporaryDirectory() as td:
        good = _write_self_fixture(Path(td) / "good", invalid=False)
        good_report = run_checks(good / "pathway.json", good / "spec.json",
                                 good / "fig.svg", good / "result.json",
                                 good, good)
        bad = _write_self_fixture(Path(td) / "bad", invalid=True)
        bad_report = run_checks(bad / "pathway.json", bad / "spec.json",
                                bad / "fig.svg", bad / "result.json",
                                bad, bad)
    report = {
        "ok": good_report["ok"] and not bad_report["ok"],
        "valid_case": {"ok": good_report["ok"],
                       "problems": good_report["problems"],
                       "warnings": good_report["warnings"]},
        "invalid_case": {"ok": bad_report["ok"],
                         "problems": bad_report["problems"]},
    }
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pathway", default=None)
    ap.add_argument("--spec", default=None)
    ap.add_argument("--svg", default=None)
    ap.add_argument("--result", default=None)
    ap.add_argument("--materials-root", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        report = self_test()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    if not args.pathway:
        ap.error("--pathway is required unless --self-test is used")
    if not Path(args.pathway).is_file():
        print(json.dumps({"ok": False, "problems": [
            "missing pathway file: %s" % args.pathway]}, ensure_ascii=False))
        return 2
    report = run_checks(args.pathway, args.spec, args.svg, args.result,
                        args.root, args.materials_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["problems"] and not report.get("checks"):
        return 2
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
