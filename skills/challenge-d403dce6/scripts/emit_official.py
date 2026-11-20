#!/usr/bin/env python3
"""Add the organizer's top-level output-contract fields to a result.json.

Usage:
    python3 emit_official.py --result /app/output/result.json \
        --profile writing --json
    python3 emit_official.py --result out.json --profile reading \
        --main-field answers --json
    python3 emit_official.py --self-test --json

The original task-specific fields are preserved. Only aliases required by the
public organizer contract are added:

  reading: status, result, claims, uncertainties
  writing: deliverable, preserved_facts, evidence_map, change_log
  media:   status, editable_source, export_artifact, content_map,
           compliance_checks, exceptions

Status is normalized to answered|partial|insufficient_evidence|input_error.
This is a deterministic adapter, not a quality judge.

Stdlib only. Exit 0 = written, 2 = unreadable result.
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

OFFICIAL_STATUS = {"answered", "partial", "insufficient_evidence", "input_error"}
STATUS_MAP = {
    "completed": "answered", "complete": "answered", "ok": "answered",
    "success": "answered", "done": "answered", "answered": "answered",
    "partial": "partial", "insufficient_input": "input_error",
    "input_error": "input_error", "failed": "input_error",
    "insufficient_evidence": "insufficient_evidence",
}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))


def artifact(result, roles):
    for item in result.get("artifacts") or []:
        if isinstance(item, dict) and str(item.get("role") or "").lower() in roles:
            return {"path": item.get("path"), "format": item.get("format")}
    return None


def first_path(result):
    for key in ("manuscript", "proposal", "document", "client", "brief", "mindmap"):
        value = result.get(key)
        if isinstance(value, dict) and value.get("path"):
            return {"path": value["path"], "format": value.get("format")}
    if isinstance(result.get("path"), str):
        return {"path": result["path"], "format": result.get("format")}
    return None


def flatten_claims(result):
    if isinstance(result.get("claims"), list):
        return result["claims"]
    claims = []
    for answer in result.get("answers") or []:
        if isinstance(answer, dict):
            claims.extend(answer.get("claims") or [])
    if claims:
        return claims
    if isinstance(result.get("extractions"), list):
        return result["extractions"]
    return []


def normalize_status(value):
    text = str(value or "").strip().lower()
    return STATUS_MAP.get(text, text if text in OFFICIAL_STATUS else "partial")


def adapt(result, profile, main_field=None):
    out = dict(result)
    out["official_contract"] = {"profile": profile,
                                "source": "organizer public contract adapter"}
    if profile == "reading":
        payload = result.get(main_field) if main_field else result.get("result")
        if payload is None:
            payload = {k: v for k, v in result.items()
                       if k not in ("claims", "uncertainties", "official_contract")}
        out["status"] = normalize_status(result.get("status"))
        out["result"] = payload
        out["claims"] = flatten_claims(result)
        out["uncertainties"] = (result.get("uncertainty")
                                if "uncertainty" in result else result.get("uncertainties")) or []
    elif profile == "writing":
        out["deliverable"] = (result.get("deliverable") or artifact(result, ("editable",))
                              or first_path(result))
        out["preserved_facts"] = (result.get("preserved_facts")
                                  or result.get("source_map") or result.get("claims")
                                  or result.get("results") or result.get("elements") or [])
        out["evidence_map"] = (result.get("evidence_map") or result.get("source_map")
                               or result.get("claims") or result.get("page_evidence") or [])
        out["change_log"] = result.get("change_log") or []
    elif profile == "media":
        out["status"] = normalize_status(result.get("status"))
        out["editable_source"] = (result.get("editable_source")
                                  or artifact(result, ("editable",)) or first_path(result))
        out["export_artifact"] = (result.get("export_artifact")
                                  or artifact(result, ("export", "render", "preview")))
        out["content_map"] = (result.get("content_map")
                              or result.get("element_source_map")
                              or result.get("source_map")
                              or result.get("series_map")
                              or result.get("page_evidence") or [])
        out["compliance_checks"] = result.get("compliance_checks") or result.get("checks") or {}
        out["exceptions"] = (result.get("exceptions") or result.get("uncertainty")
                             or result.get("uncertainties") or result.get("limitations") or [])
    else:
        raise ValueError("unknown profile: %s" % profile)
    return out


def self_test():
    reading = {"case_id": "r", "status": "completed", "answers": [
        {"claims": [{"text": "x", "evidence": []}]}], "uncertainty": []}
    writing = {"case_id": "w", "status": "partial", "source_map": [{"fact_id": "F1"}],
               "artifacts": [{"role": "editable", "path": "proposal.md"}],
               "change_log": []}
    media = {"case_id": "m", "status": "completed",
             "artifacts": [{"role": "editable", "path": "fig.svg"},
                           {"role": "preview", "path": "fig.png"}],
             "checks": {"xml_valid": True}, "uncertainty": []}
    assert adapt(reading, "reading")["status"] == "answered"
    assert adapt(writing, "writing")["deliverable"]["path"] == "proposal.md"
    assert adapt(media, "media")["editable_source"]["path"] == "fig.svg"
    return {"reading": adapt(reading, "reading"),
            "writing": adapt(writing, "writing"),
            "media": adapt(media, "media")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--profile", choices=("reading", "writing", "media"), default=None)
    ap.add_argument("--main-field", default=None)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        print(json.dumps({"ok": True, "self_test": True, "result": self_test()},
                         ensure_ascii=False, indent=2))
        return 0
    if not args.result or not args.profile:
        ap.error("--result and --profile are required unless --self-test is used")
    try:
        result = load_json(args.result)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": "cannot read result: %s" % exc},
                         ensure_ascii=False))
        return 2
    out = adapt(result, args.profile, args.main_field)
    target = Path(args.out or args.result)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    print(json.dumps({"ok": True, "out": str(target), "profile": args.profile,
                      "official_fields": [k for k in out
                                          if k in ("status", "result", "claims",
                                                   "uncertainties", "deliverable",
                                                   "preserved_facts", "evidence_map",
                                                   "editable_source", "export_artifact",
                                                   "content_map", "compliance_checks",
                                                   "exceptions")]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
