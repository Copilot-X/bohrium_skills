#!/usr/bin/env python3
"""Validate a workorder-first multi-document comparison/synthesis result.

Usage:
    python3 check_synthesis.py --result /app/output/result.json \
        --workorder /input/comparison-workorder.json --json
    python3 check_synthesis.py --result /app/output/result.json \
        --input /input --json
    python3 check_synthesis.py --self-test --json

Checks
  * result keys and official reading aliases (status/result/claims/uncertainties)
  * documents use exact source_document_id; comparison_table covers every document
  * every cell has the seven fields and a legal status
  * task/metric/units/material/pollutant/budget gate is complete and reasoned
  * gate_summary is consistent with the gate (no pooling across incomparable items)
  * agreements need >=2 distinct source_document_id
  * single-document conclusions stay in synthesis.single_document_only
  * noncomparable/nonreported arrays match the matrix and gate
  * fewer than 2 documents is reported as not-a-multi-document-comparison

Stdlib only. Exit 0 = pass, 1 = problems, 2 = unreadable result.
"""
import argparse
import json
import sys
from pathlib import Path

CELL_STATUS = {"reported", "not_reported", "incomparable", "unreadable"}
DOC_TYPES = {"research", "review", "standard", "dataset", "opinion", "unknown"}
GATE_ITEMS = ("task", "metric", "units", "material", "pollutant", "budget")
VERDICTS = {"comparable", "incomparable", "unknown"}
GATE_SUMMARY = {"allowed", "restricted", "blocked"}
OFFICIAL_STATUS = {"answered", "partial", "insufficient_evidence", "input_error"}
RESOLUTIONS = {"unresolved", "explained_by_condition"}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def doc_id(value):
    """Return the exact source_document_id, with doc_id fallback for v0.4 data."""
    if not isinstance(value, dict):
        return None
    did = value.get("source_document_id")
    if did not in (None, ""):
        return str(did)
    did = value.get("doc_id")
    if did not in (None, ""):
        return str(did)
    return None


def collect_refs(value):
    """Collect {source_document_id, paragraph_id} from nested support/sides/evidence."""
    refs = []
    if isinstance(value, list):
        for item in value:
            refs.extend(collect_refs(item))
    elif isinstance(value, dict):
        did = doc_id(value)
        if did:
            refs.append({"source_document_id": did,
                         "paragraph_id": value.get("paragraph_id")
                         or value.get("location")})
        for key in ("support", "sides", "side", "evidence"):
            if key in value:
                refs.extend(collect_refs(value[key]))
    return refs


def distinct_docs(value):
    return {ref["source_document_id"] for ref in collect_refs(value)}


def extract_ids(workorder):
    """Extract source document ids from common workorder key names."""
    if not isinstance(workorder, dict):
        return []
    for key in ("source_document_ids", "source_documents", "documents",
                "document_ids", "corpus"):
        value = workorder.get(key)
        if isinstance(value, dict):
            if value:
                return [str(item) for item in value]
            continue
        if not isinstance(value, list):
            continue
        ids = []
        for item in value:
            if isinstance(item, str) and item:
                ids.append(item)
            elif isinstance(item, dict):
                did = doc_id(item) or item.get("id")
                if did not in (None, ""):
                    ids.append(str(did))
        if ids:
            return ids
    return []


def extract_dimensions(workorder):
    if not isinstance(workorder, dict):
        return []
    for key in ("comparison_dimensions", "dimensions", "fields",
                "comparison_fields"):
        value = workorder.get(key)
        if isinstance(value, list):
            dims = []
            for item in value:
                if isinstance(item, str) and item:
                    dims.append(item)
                elif isinstance(item, dict):
                    name = (item.get("name") or item.get("dimension")
                            or item.get("id"))
                    if name not in (None, ""):
                        dims.append(str(name))
            return dims
        if isinstance(value, dict):
            return [str(key_name) for key_name in value]
    return []


def extract_rule_list(workorder, keys):
    if not isinstance(workorder, dict):
        return []
    for key in keys:
        value = workorder.get(key)
        if isinstance(value, list):
            return [str(item) for item in value if str(item)]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    return []


def validate(result, workorder=None, workorder_error=None):
    problems, warnings, checks = [], [], {}
    if workorder_error:
        warnings.append("cannot read workorder: %s" % workorder_error)
    if not isinstance(result, dict):
        return {"ok": False, "problems": ["result root is not an object"],
                "warnings": [], "checks": {}}

    required = ("case_id", "status", "workorder", "documents", "dimensions",
                "comparison_table", "comparability_gate", "gate_summary",
                "multi_document", "synthesis", "conflicts", "uncertainty",
                "change_log", "result", "claims", "uncertainties")
    for key in required:
        if key not in result:
            problems.append("result.json missing %s" % key)
    if "support_files_used" not in result:
        warnings.append("result.json missing support_files_used (recommended)")

    status = result.get("status")
    if status not in OFFICIAL_STATUS:
        problems.append("status is not one of %s: %s" %
                        (sorted(OFFICIAL_STATUS), status))

    # ---- documents -----------------------------------------------------
    documents = result.get("documents")
    doc_ids = set()
    doc_types = {}
    if not isinstance(documents, list):
        problems.append("documents is not an array")
        documents = []
    for i, doc in enumerate(documents):
        if not isinstance(doc, dict):
            problems.append("document[%d] is not an object" % i)
            continue
        did = doc_id(doc)
        if not did:
            problems.append("document[%d] missing source_document_id" % i)
            continue
        if "source_document_id" not in doc:
            problems.append("document %s uses doc_id; expected exact "
                            "source_document_id" % did)
        if did in doc_ids:
            problems.append("duplicate source_document_id: %s" % did)
        doc_ids.add(did)
        dtype = str(doc.get("type") or "unknown")
        if dtype not in DOC_TYPES:
            warnings.append("document %s unknown type %s" % (did, dtype))
        doc_types[did] = dtype
    checks["documents"] = len(doc_ids)

    # ---- workorder copied into the result ------------------------------
    wo = result.get("workorder")
    if not isinstance(wo, dict):
        problems.append("workorder is not an object")
        wo = {}
    if not isinstance(wo.get("loaded"), bool):
        problems.append("workorder.loaded must be true or false")
    internal_ids = [str(item) for item in (wo.get("source_document_ids") or [])
                    if str(item)]
    for did in internal_ids:
        if did not in doc_ids:
            problems.append("workorder.source_document_ids references unknown "
                            "document %s" % did)
    if wo.get("loaded") is False and not any(
            "workorder" in str(item).lower() for item in result.get("uncertainty") or []):
        warnings.append("workorder not loaded; record workorder_missing in uncertainty")

    # ---- dimensions ----------------------------------------------------
    dimensions = result.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        problems.append("dimensions must be a non-empty array")
        dimensions = []
    dim_set = set()
    for dim in dimensions:
        dim = str(dim)
        if dim in dim_set:
            problems.append("duplicate dimension: %s" % dim)
        dim_set.add(dim)

    # ---- comparison table ----------------------------------------------
    table = result.get("comparison_table")
    if not isinstance(table, list) or not table:
        problems.append("comparison_table is empty")
        table = []
    row_dims = set()
    not_reported_cells = set()
    reported_cells = 0
    for i, row in enumerate(table):
        if not isinstance(row, dict):
            problems.append("comparison_table[%d] is not an object" % i)
            continue
        dim = row.get("dimension")
        if not dim:
            problems.append("comparison_table[%d] missing dimension" % i)
            continue
        dim = str(dim)
        if dim in row_dims:
            problems.append("duplicate comparison_table dimension: %s" % dim)
        row_dims.add(dim)
        cells = row.get("cells")
        if not isinstance(cells, list):
            problems.append("%s cells is not an array" % dim)
            continue
        seen = set()
        for j, cell in enumerate(cells):
            if not isinstance(cell, dict):
                problems.append("%s cell[%d] is not an object" % (dim, j))
                continue
            did = doc_id(cell)
            if not did:
                problems.append("%s cell[%d] missing source_document_id" % (dim, j))
                continue
            if "source_document_id" not in cell:
                problems.append("%s/%s uses doc_id; expected exact "
                                "source_document_id" % (dim, did))
            if did not in doc_ids:
                problems.append("%s cell references unknown document %s" % (dim, did))
            if did in seen:
                problems.append("%s has duplicate cell for %s" % (dim, did))
            seen.add(did)
            for key in ("paragraph_id", "dimension", "value", "unit",
                        "condition", "status"):
                if key not in cell:
                    problems.append("%s/%s cell missing %s" % (dim, did, key))
            cell_dim = cell.get("dimension")
            if cell_dim and str(cell_dim) != dim:
                problems.append("%s/%s cell.dimension=%s does not match row"
                                % (dim, did, cell_dim))
            cell_status = cell.get("status")
            if cell_status not in CELL_STATUS:
                problems.append("%s/%s invalid status: %s" %
                                (dim, did, cell_status))
            value = cell.get("value")
            paragraph_id = cell.get("paragraph_id")
            reason = cell.get("reason")
            if cell_status == "reported":
                reported_cells += 1
                if value in (None, ""):
                    problems.append("%s/%s reported but value is empty" % (dim, did))
                if paragraph_id in (None, ""):
                    problems.append("%s/%s reported but paragraph_id is missing"
                                    % (dim, did))
                if "unit" in cell and cell.get("unit") in (None, ""):
                    warnings.append("%s/%s reported with null unit; record why"
                                    % (dim, did))
                if "condition" in cell and cell.get("condition") in (None, ""):
                    warnings.append("%s/%s reported with null condition; record why"
                                    % (dim, did))
            elif cell_status == "not_reported":
                not_reported_cells.add((dim, did))
                if value not in (None, ""):
                    warnings.append("%s/%s not_reported but has a value" % (dim, did))
                if paragraph_id not in (None, ""):
                    warnings.append("%s/%s not_reported but has paragraph_id"
                                    % (dim, did))
                if not reason:
                    warnings.append("%s/%s not_reported without reason" % (dim, did))
            elif cell_status == "incomparable":
                if value in (None, ""):
                    problems.append("%s/%s incomparable but value is empty"
                                    % (dim, did))
                if not reason:
                    problems.append("%s/%s incomparable without reason" % (dim, did))
                if paragraph_id in (None, ""):
                    warnings.append("%s/%s incomparable without paragraph_id"
                                    % (dim, did))
            elif cell_status == "unreadable":
                if value not in (None, ""):
                    warnings.append("%s/%s unreadable but has a value" % (dim, did))
                if not reason:
                    problems.append("%s/%s unreadable without reason" % (dim, did))
        missing = sorted(doc_ids - seen)
        if missing:
            problems.append("%s missing cells for: %s" % (dim, ", ".join(missing)))
    checks["dimensions"] = sorted(row_dims)
    checks["reported_cells"] = reported_cells
    if not reported_cells:
        warnings.append("no reported cells; comparison may be empty")

    # ---- comparability gate -------------------------------------------
    gate = result.get("comparability_gate")
    gate_by_item = {}
    if not isinstance(gate, list):
        problems.append("comparability_gate is not an array")
        gate = []
    for i, item in enumerate(gate):
        if not isinstance(item, dict):
            problems.append("comparability_gate[%d] is not an object" % i)
            continue
        item_name = item.get("item")
        if item_name not in GATE_ITEMS:
            warnings.append("comparability_gate[%d] unknown item: %s" %
                            (i, item_name))
            continue
        if item_name in gate_by_item:
            problems.append("duplicate comparability_gate item: %s" % item_name)
        gate_by_item[item_name] = item
        verdict = item.get("verdict")
        if verdict not in VERDICTS:
            problems.append("gate %s invalid verdict: %s" % (item_name, verdict))
        reason = item.get("reason")
        if not reason:
            problems.append("gate %s missing reason" % item_name)
        affected = item.get("affected_documents")
        if affected is None:
            affected = []
        if not isinstance(affected, list):
            problems.append("gate %s affected_documents is not an array" % item_name)
            affected = []
        for did in affected:
            if str(did) not in doc_ids:
                problems.append("gate %s references unknown document %s" %
                                (item_name, did))
        evidence = item.get("evidence")
        if evidence is None:
            evidence = []
        if not isinstance(evidence, list):
            problems.append("gate %s evidence is not an array" % item_name)
            evidence = []
        refs = collect_refs(evidence)
        if verdict == "incomparable":
            if len(affected) < 2:
                problems.append("gate %s incomparable needs at least 2 "
                                "affected_documents" % item_name)
            if not refs:
                problems.append("gate %s incomparable needs evidence" % item_name)
            elif not any(ref.get("paragraph_id") for ref in refs):
                warnings.append("gate %s evidence has no paragraph_id" % item_name)
        elif verdict == "unknown":
            if not reason:
                problems.append("gate %s unknown needs a reason" % item_name)
    missing_gate = [item for item in GATE_ITEMS if item not in gate_by_item]
    if missing_gate:
        problems.append("comparability_gate missing items: %s" %
                        ", ".join(missing_gate))
    checks["gate_items"] = sorted(gate_by_item)

    # ---- gate summary --------------------------------------------------
    summary = result.get("gate_summary")
    if not isinstance(summary, dict):
        problems.append("gate_summary is not an object")
        summary = {}
    quantitative = summary.get("quantitative_synthesis")
    if quantitative not in GATE_SUMMARY:
        problems.append("gate_summary.quantitative_synthesis invalid: %s" %
                        quantitative)
    if not summary.get("reason"):
        problems.append("gate_summary missing reason")
    subset = summary.get("permitted_subset")
    if subset is None:
        subset = []
    if not isinstance(subset, list):
        problems.append("gate_summary.permitted_subset is not an array")
        subset = []
    for i, entry in enumerate(subset):
        if not isinstance(entry, dict):
            problems.append("permitted_subset[%d] is not an object" % i)
            continue
        if not entry.get("dimension"):
            warnings.append("permitted_subset[%d] missing dimension" % i)
        dids = entry.get("source_document_ids")
        if not isinstance(dids, list) or len(dids) < 2:
            problems.append("permitted_subset[%d] needs at least 2 "
                            "source_document_ids" % i)
            dids = []
        for did in dids:
            if str(did) not in doc_ids:
                problems.append("permitted_subset[%d] unknown document %s" %
                                (i, did))
    if quantitative == "restricted" and not subset:
        problems.append("gate_summary=restricted but permitted_subset is empty")
    if quantitative == "allowed":
        blocked = [item for item in GATE_ITEMS
                   if (gate_by_item.get(item) or {}).get("verdict") != "comparable"]
        if blocked:
            problems.append("gate_summary=allowed but gate items are not "
                            "comparable: %s" % ", ".join(blocked))
    if quantitative == "blocked" and subset:
        warnings.append("gate_summary=blocked but permitted_subset is not empty")

    # ---- synthesis -----------------------------------------------------
    synthesis = result.get("synthesis")
    if not isinstance(synthesis, dict):
        problems.append("synthesis is not an object")
        synthesis = {}
    for key in ("agreements", "divergences", "gaps", "noncomparable",
                "nonreported", "single_document_only"):
        if key not in synthesis:
            problems.append("synthesis missing %s" % key)

    agreements = synthesis.get("agreements")
    if not isinstance(agreements, list):
        problems.append("synthesis.agreements is not an array")
        agreements = []
    multi_doc_agreements = 0
    for i, item in enumerate(agreements):
        if not isinstance(item, dict):
            problems.append("agreement[%d] is not an object" % i)
            continue
        if not item.get("text"):
            problems.append("agreement[%d] missing text" % i)
        refs = collect_refs(item.get("support"))
        docs = {ref["source_document_id"] for ref in refs}
        if len(docs) < 2:
            problems.append("agreement[%d] needs >=2 distinct "
                            "source_document_id; single-document conclusions "
                            "belong in synthesis.single_document_only" % i)
        else:
            multi_doc_agreements += 1
        for ref in refs:
            if ref["source_document_id"] not in doc_ids:
                problems.append("agreement[%d] references unknown document %s" %
                                (i, ref["source_document_id"]))
        if refs and all(doc_types.get(ref["source_document_id"]) == "review"
                        for ref in refs):
            warnings.append("agreement[%d] relies only on review documents" % i)

    divergences = synthesis.get("divergences")
    if not isinstance(divergences, list):
        problems.append("synthesis.divergences is not an array")
        divergences = []
    multi_doc_divergences = 0
    for i, item in enumerate(divergences):
        if not isinstance(item, dict):
            problems.append("divergence[%d] is not an object" % i)
            continue
        if not item.get("text"):
            problems.append("divergence[%d] missing text" % i)
        sides = item.get("sides") or item.get("side") or []
        if not isinstance(sides, list):
            problems.append("divergence[%d] sides is not an array" % i)
            sides = []
        if len(sides) < 2:
            problems.append("divergence[%d] needs at least two sides" % i)
        docs = {ref["source_document_id"] for ref in collect_refs(sides)}
        if len(docs) < 2:
            problems.append("divergence[%d] needs sides from >=2 documents" % i)
        else:
            multi_doc_divergences += 1
        for ref in collect_refs(sides):
            if ref["source_document_id"] not in doc_ids:
                problems.append("divergence[%d] references unknown document %s" %
                                (i, ref["source_document_id"]))
        resolution = item.get("resolution")
        if resolution not in RESOLUTIONS:
            warnings.append("divergence[%d] resolution unusual: %s" %
                            (i, resolution))
        if resolution == "explained_by_condition" and not item.get("reason"):
            warnings.append("divergence[%d] explained_by_condition without reason" % i)

    gaps = synthesis.get("gaps")
    if not isinstance(gaps, list):
        problems.append("synthesis.gaps is not an array")
        gaps = []
    for i, item in enumerate(gaps):
        if not isinstance(item, dict):
            problems.append("gap[%d] is not an object" % i)
            continue
        if not item.get("text"):
            problems.append("gap[%d] missing text" % i)
        if not item.get("affected_dimension"):
            warnings.append("gap[%d] missing affected_dimension" % i)

    noncomparable = synthesis.get("noncomparable")
    if not isinstance(noncomparable, list):
        problems.append("synthesis.noncomparable is not an array")
        noncomparable = []
    noncomparable_items = set()
    for i, item in enumerate(noncomparable):
        if not isinstance(item, dict):
            problems.append("noncomparable[%d] is not an object" % i)
            continue
        item_name = item.get("item")
        if item_name in GATE_ITEMS:
            noncomparable_items.add(item_name)
        elif item_name not in dim_set:
            warnings.append("noncomparable[%d] item is neither a gate item nor "
                            "a dimension: %s" % (i, item_name))
        dids = item.get("source_document_ids")
        if not isinstance(dids, list) or len(dids) < 2:
            problems.append("noncomparable[%d] needs at least 2 "
                            "source_document_ids" % i)
            dids = []
        for did in dids:
            if str(did) not in doc_ids:
                problems.append("noncomparable[%d] unknown document %s" % (i, did))
        if not item.get("reason"):
            problems.append("noncomparable[%d] missing reason" % i)
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            warnings.append("noncomparable[%d] missing evidence" % i)
    if len(doc_ids) >= 2:
        for item_name in GATE_ITEMS:
            verdict = (gate_by_item.get(item_name) or {}).get("verdict")
            if verdict in ("incomparable", "unknown") and item_name not in noncomparable_items:
                problems.append("gate %s is %s but synthesis.noncomparable has no "
                                "entry for it" % (item_name, verdict))

    nonreported = synthesis.get("nonreported")
    if not isinstance(nonreported, list):
        problems.append("synthesis.nonreported is not an array")
        nonreported = []
    nonreported_pairs = set()
    for i, item in enumerate(nonreported):
        if not isinstance(item, dict):
            problems.append("nonreported[%d] is not an object" % i)
            continue
        did = doc_id(item)
        if did not in doc_ids:
            problems.append("nonreported[%d] unknown document %s" % (i, did))
        dim = item.get("dimension")
        if not dim:
            problems.append("nonreported[%d] missing dimension" % i)
        else:
            nonreported_pairs.add((str(dim), did))
        if not item.get("reason"):
            problems.append("nonreported[%d] missing reason" % i)
    for dim, did in sorted(not_reported_cells):
        if (dim, did) not in nonreported_pairs:
            problems.append("cell %s/%s is not_reported but has no "
                            "synthesis.nonreported entry" % (dim, did))

    single_only = synthesis.get("single_document_only")
    if not isinstance(single_only, list):
        problems.append("synthesis.single_document_only is not an array")
        single_only = []
    for i, item in enumerate(single_only):
        if not isinstance(item, dict):
            problems.append("single_document_only[%d] is not an object" % i)
            continue
        if not item.get("text"):
            problems.append("single_document_only[%d] missing text" % i)
        dids = set()
        for key in ("source_document_id", "source_document_ids", "doc_id"):
            value = item.get(key)
            if isinstance(value, list):
                dids.update(str(v) for v in value if v)
            elif value not in (None, ""):
                dids.add(str(value))
        for ref in collect_refs(item.get("evidence")):
            dids.add(ref["source_document_id"])
        if len(dids) != 1:
            problems.append("single_document_only[%d] must reference exactly "
                            "one document; got %s" % (i, sorted(dids)))
        for did in dids:
            if did not in doc_ids:
                problems.append("single_document_only[%d] unknown document %s" %
                                (i, did))
        if item.get("paragraph_id") in (None, "") and not any(
                ref.get("paragraph_id") for ref in collect_refs(item.get("evidence"))):
            warnings.append("single_document_only[%d] has no paragraph_id" % i)

    # ---- multi-document vs single-document -----------------------------
    multi = result.get("multi_document")
    if not isinstance(multi, dict):
        problems.append("multi_document is not an object")
        multi = {}
    count = multi.get("document_count")
    if not isinstance(count, int):
        problems.append("multi_document.document_count must be an integer")
    elif count != len(doc_ids):
        warnings.append("multi_document.document_count=%s but documents=%s" %
                        (count, len(doc_ids)))
    satisfied = multi.get("satisfied")
    if not isinstance(satisfied, bool):
        problems.append("multi_document.satisfied must be true or false")
    if len(doc_ids) < 2:
        problems.append("multi-document comparison requires at least 2 "
                        "documents; got %d" % len(doc_ids))
        if satisfied:
            problems.append("multi_document.satisfied must be false when fewer "
                            "than 2 documents are available")
        if status == "answered":
            problems.append("status must be insufficient_evidence or partial "
                            "when fewer than 2 documents are available")
    elif satisfied and not (multi_doc_agreements or multi_doc_divergences):
        problems.append("multi_document.satisfied=true but no agreement or "
                        "divergence has >=2 documents")
    checks["multi_document_agreements"] = multi_doc_agreements
    checks["multi_document_divergences"] = multi_doc_divergences

    # ---- conflicts and official aliases --------------------------------
    conflicts = result.get("conflicts")
    if not isinstance(conflicts, list):
        problems.append("conflicts is not an array")
        conflicts = []
    for i, item in enumerate(conflicts):
        if not isinstance(item, dict):
            problems.append("conflict[%d] is not an object" % i)
            continue
        if not item.get("item"):
            warnings.append("conflict[%d] missing item" % i)
        if not item.get("positions"):
            warnings.append("conflict[%d] has no positions" % i)

    if "result" not in result:
        problems.append("official alias result is missing")
    if not isinstance(result.get("claims"), list):
        problems.append("official alias claims must be an array")
    if not isinstance(result.get("uncertainties"), list):
        problems.append("official alias uncertainties must be an array")
    for i, claim in enumerate(result.get("claims") or []):
        if not isinstance(claim, dict):
            problems.append("claim[%d] is not an object" % i)
            continue
        for key in ("claim_id", "kind", "text", "source_id", "location"):
            if not claim.get(key):
                warnings.append("claim[%d] missing %s" % (i, key))
        if not isinstance(claim.get("evidence"), list):
            warnings.append("claim[%d] evidence is not an array" % i)

    # ---- external workorder consistency --------------------------------
    if workorder is not None:
        external_ids = extract_ids(workorder)
        if external_ids:
            if len(external_ids) < 2:
                problems.append("workorder has fewer than 2 source_document_ids; "
                                "multi-document comparison is not satisfied")
            missing = sorted(set(external_ids) - doc_ids)
            if missing:
                problems.append("workorder documents not covered: %s" %
                                ", ".join(missing))
            extra = sorted(doc_ids - set(external_ids))
            if extra:
                warnings.append("result has documents not listed in workorder: %s" %
                                ", ".join(extra))
            if internal_ids and set(internal_ids) != set(external_ids):
                warnings.append("result.workorder.source_document_ids differs "
                                "from the workorder file")
        external_dims = extract_dimensions(workorder)
        if external_dims:
            missing_dims = [dim for dim in external_dims if dim not in dim_set]
            if missing_dims:
                problems.append("workorder comparison_dimensions not represented "
                                "in result.dimensions: %s" %
                                ", ".join(missing_dims))
        for keys, label in ((
                ("target_material", "target_materials", "material"),
                "target_material"), (
                ("target_pollutant", "target_pollutants", "pollutant", "dye"),
                "target_pollutant"), (
                ("incomparability_rules", "comparability_rules", "rules",
                 "constraints", "notes"),
                "incomparability_rules")):
            expected = extract_rule_list(workorder, keys)
            if expected and not wo.get(label):
                warnings.append("workorder has %s but result.workorder.%s is "
                                "empty" % (label, label))
        rules_text = " ".join(str(x).lower() for x in extract_rule_list(
            workorder,
            ("incomparability_rules", "comparability_rules", "rules",
             "constraints", "notes")))
        if any(key in rules_text for key in ("dye", "染料", "dataset", "数据集",
                                             "pool", "合并")) and quantitative == "allowed":
            warnings.append("workorder has pooling/incomparability rules but "
                            "gate_summary=allowed")

    checks["documents"] = len(doc_ids)
    checks["dimensions"] = sorted(row_dims)
    checks["permitted_subset"] = len(subset)
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "checks": checks}


def _doc(did, title, dtype="research"):
    return {"source_document_id": did, "title": title, "type": dtype,
            "read_scope": ["p1", "p6"], "included": True, "reason": "直接相关"}


def self_test():
    """Return a small valid result; used by --self-test and negative assertions."""
    dims = ["material", "degradation_efficiency"]
    result = {
        "case_id": "synthesis-self-test",
        "status": "answered",
        "task_family": "reading",
        "workorder": {
            "path": "/input/comparison-workorder.json",
            "loaded": True,
            "source_document_ids": ["DOC-A", "DOC-B", "DOC-C"],
            "paragraph_id_scheme": "p1..p6 within each document",
            "comparison_dimensions": dims,
            "target_material": "anatase TiO2",
            "target_pollutant": "methylene blue",
            "incomparability_rules": ["Do not pool different dyes."],
            "key_map": {},
        },
        "documents": [_doc("DOC-A", "A"), _doc("DOC-B", "B"), _doc("DOC-C", "C")],
        "dimensions": dims,
        "comparison_table": [
            {"dimension": "material", "cells": [
                {"source_document_id": "DOC-A", "paragraph_id": "p1",
                 "dimension": "material", "value": "anatase TiO2", "unit": None,
                 "condition": "methylene blue", "status": "reported"},
                {"source_document_id": "DOC-B", "paragraph_id": "p4",
                 "dimension": "material", "value": "anatase TiO2", "unit": None,
                 "condition": "methylene blue", "status": "reported"},
                {"source_document_id": "DOC-C", "paragraph_id": "p4",
                 "dimension": "material", "value": "anatase TiO2", "unit": None,
                 "condition": "rhodamine B", "status": "reported"},
            ]},
            {"dimension": "degradation_efficiency", "cells": [
                {"source_document_id": "DOC-A", "paragraph_id": "p3",
                 "dimension": "degradation_efficiency", "value": 92, "unit": "%",
                 "condition": "0.5 g/L, 60 min, MB", "status": "reported"},
                {"source_document_id": "DOC-B", "paragraph_id": "p5",
                 "dimension": "degradation_efficiency", "value": 0.81,
                 "unit": "fraction of initial absorbance",
                 "condition": "0.5 g/L, 60 min, MB", "status": "incomparable",
                 "reason": "unit/basis differs; no conversion reported"},
                {"source_document_id": "DOC-C", "paragraph_id": None,
                 "dimension": "degradation_efficiency", "value": None, "unit": None,
                 "condition": None, "status": "not_reported",
                 "reason": "DOC-C reports RhB, not MB"},
            ]},
        ],
        "comparability_gate": [
            {"item": "task", "verdict": "comparable", "reason": "same question",
             "affected_documents": ["DOC-A", "DOC-B", "DOC-C"],
             "evidence": [{"source_document_id": "DOC-A", "paragraph_id": "p1"}]},
            {"item": "metric", "verdict": "comparable", "reason": "same metric",
             "affected_documents": ["DOC-A", "DOC-B"], "evidence": []},
            {"item": "units", "verdict": "incomparable",
             "reason": "% vs fraction of initial absorbance",
             "affected_documents": ["DOC-A", "DOC-B"],
             "evidence": [{"source_document_id": "DOC-B", "paragraph_id": "p5"}]},
            {"item": "material", "verdict": "comparable", "reason": "same material",
             "affected_documents": ["DOC-A", "DOC-B", "DOC-C"], "evidence": []},
            {"item": "pollutant", "verdict": "incomparable",
             "reason": "MB vs RhB", "affected_documents": ["DOC-A", "DOC-B", "DOC-C"],
             "evidence": [{"source_document_id": "DOC-C", "paragraph_id": "p1"}]},
            {"item": "budget", "verdict": "incomparable",
             "reason": "0.5 vs 1.0 g/L", "affected_documents": ["DOC-A", "DOC-C"],
             "evidence": [{"source_document_id": "DOC-C", "paragraph_id": "p2"}]},
        ],
        "gate_summary": {
            "quantitative_synthesis": "blocked", "permitted_subset": [],
            "reason": "no >=2-document subset passes units/pollutant/budget",
        },
        "multi_document": {"document_count": 3, "satisfied": True,
                           "basis": "3 documents and a multi-document agreement"},
        "synthesis": {
            "agreements": [
                {"text": "all three identify anatase TiO2", "support": [
                    {"source_document_id": "DOC-A", "paragraph_id": "p1"},
                    {"source_document_id": "DOC-B", "paragraph_id": "p4"},
                    {"source_document_id": "DOC-C", "paragraph_id": "p4"}]},
            ],
            "divergences": [
                {"text": "A and B use different units", "sides": [
                    {"source_document_id": "DOC-A", "paragraph_id": "p3",
                     "claim": "92%"},
                    {"source_document_id": "DOC-B", "paragraph_id": "p5",
                     "claim": "0.81 fraction"}],
                 "resolution": "explained_by_condition",
                 "reason": "different measurement basis"},
            ],
            "gaps": [{"text": "no same-budget cross-pollutant comparison",
                      "affected_dimension": "pollutant",
                      "support": [{"source_document_id": "DOC-C",
                                   "paragraph_id": "p2"}]}],
            "noncomparable": [
                {"item": "units", "dimension": "degradation_efficiency",
                 "source_document_ids": ["DOC-A", "DOC-B"],
                 "reason": "unit/basis differs",
                 "evidence": [{"source_document_id": "DOC-B",
                               "paragraph_id": "p5"}]},
                {"item": "pollutant", "dimension": "degradation_efficiency",
                 "source_document_ids": ["DOC-A", "DOC-B", "DOC-C"],
                 "reason": "MB vs RhB",
                 "evidence": [{"source_document_id": "DOC-C",
                               "paragraph_id": "p1"}]},
                {"item": "budget", "dimension": "degradation_efficiency",
                 "source_document_ids": ["DOC-A", "DOC-C"],
                 "reason": "0.5 vs 1.0 g/L",
                 "evidence": [{"source_document_id": "DOC-C",
                               "paragraph_id": "p2"}]},
            ],
            "nonreported": [
                {"source_document_id": "DOC-C", "dimension":
                 "degradation_efficiency", "paragraph_id": None,
                 "search_scope": "p1-p6", "reason": "DOC-C reports RhB only"},
            ],
            "single_document_only": [
                {"text": "DOC-A reports 42% {001} facet exposure",
                 "source_document_id": "DOC-A", "paragraph_id": "p5",
                 "note": "single-document conclusion, not a field consensus"},
            ],
        },
        "conflicts": [],
        "uncertainty": ["synthetic self-test"],
        "change_log": [{"step": 1, "action": "self-test", "evidence": "built-in"}],
        "support_files_used": ["scripts/check_synthesis.py"],
        "result": {"summary": "self-test result", "quantitative_synthesis": "blocked"},
        "claims": [
            {"claim_id": "C1", "kind": "agreement",
             "text": "all three identify anatase TiO2",
             "evidence": [{"source_document_id": "DOC-A", "paragraph_id": "p1"}],
             "source_id": "DOC-A", "location": "p1"},
        ],
        "uncertainties": ["synthetic self-test"],
    }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None)
    ap.add_argument("--workorder", default=None)
    ap.add_argument("--input", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        report = validate(self_test(), None)
        single = {
            "case_id": "synthesis-self-test-single",
            "status": "insufficient_evidence",
            "task_family": "reading",
            "workorder": {
                "path": "/input/comparison-workorder.json", "loaded": True,
                "source_document_ids": ["DOC-A"],
                "paragraph_id_scheme": "p1..p6",
                "comparison_dimensions": ["material"],
                "target_material": "anatase TiO2",
                "target_pollutant": "methylene blue",
                "incomparability_rules": ["single document is not multi-document"],
                "key_map": {},
            },
            "documents": [_doc("DOC-A", "A")],
            "dimensions": ["material"],
            "comparison_table": [{"dimension": "material", "cells": [
                {"source_document_id": "DOC-A", "paragraph_id": "p1",
                 "dimension": "material", "value": "anatase TiO2", "unit": None,
                 "condition": "methylene blue", "status": "reported"}]}],
            "comparability_gate": [
                {"item": item, "verdict": "unknown",
                 "reason": "only one document; no cross-document comparison",
                 "affected_documents": ["DOC-A"], "evidence": []}
                for item in GATE_ITEMS
            ],
            "gate_summary": {
                "quantitative_synthesis": "blocked", "permitted_subset": [],
                "reason": "single document; no multi-document comparison",
            },
            "multi_document": {"document_count": 1, "satisfied": False,
                               "basis": "single document only"},
            "synthesis": {
                "agreements": [], "divergences": [], "gaps": [],
                "noncomparable": [], "nonreported": [],
                "single_document_only": [
                    {"text": "DOC-A reports anatase TiO2",
                     "source_document_id": "DOC-A", "paragraph_id": "p1",
                     "note": "single-document fact"},
                ],
            },
            "conflicts": [], "uncertainty": ["single document only"],
            "change_log": [], "support_files_used": [],
            "result": {"summary": "single document; not a multi-document comparison"},
            "claims": [{"claim_id": "C1", "kind": "single_document",
                        "text": "DOC-A reports anatase TiO2",
                        "evidence": [{"source_document_id": "DOC-A",
                                      "paragraph_id": "p1"}],
                        "source_id": "DOC-A", "location": "p1"}],
            "uncertainties": ["single document only"],
        }
        single_report = validate(single, None)
        passed = report["ok"] and not single_report["ok"] and any(
            "at least 2 documents" in problem
            for problem in single_report["problems"])
        print(json.dumps({"ok": passed, "self_test": True,
                          "valid_result_ok": report["ok"],
                          "single_document_flagged": not single_report["ok"],
                          "problems": report["problems"],
                          "warnings": report["warnings"],
                          "single_document_problems": single_report["problems"]},
                         ensure_ascii=False, indent=2))
        return 0 if passed else 1

    if not args.result:
        ap.error("--result is required unless --self-test is used")
    try:
        result = load_json(args.result)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "problems": ["cannot read result: %s" % exc]},
                         ensure_ascii=False))
        return 2

    workorder_path = args.workorder
    if not workorder_path and args.input:
        candidate = Path(args.input)
        if candidate.is_dir():
            candidate = candidate / "comparison-workorder.json"
        else:
            candidate = candidate.parent / "comparison-workorder.json"
        if candidate.is_file():
            workorder_path = str(candidate)
    workorder, workorder_error = None, None
    if workorder_path:
        try:
            workorder = load_json(workorder_path)
        except (OSError, json.JSONDecodeError) as exc:
            workorder_error = "%s: %s" % (workorder_path, exc)

    report = validate(result, workorder, workorder_error)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
