#!/usr/bin/env python3
"""Inventory claims-to-verify and rank candidate source paragraphs.

Usage:
    python3 inspect_claims.py /input --out /app/work/claim_index.json --json
    python3 inspect_claims.py /input/claims-to-verify.json --top 8
    python3 inspect_claims.py --self-test --json

Output (JSON):
  case_id, claims[] with markers and candidates[{paragraph_id, score, reasons,
  preview}], paragraphs[], stats, warnings.

The ranking is a recall aid only: always read the full paragraph (and its
neighbours) before assigning a verdict. Stdlib only.
Exit 0 = index written, 1 = no claims / unreadable input, 2 = unreadable.
"""
import argparse
import json
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

TEXT_SUFFIXES = {".md", ".txt", ".csv", ".tsv", ".tex", ".html", ".htm",
                 ".xml", ".yaml", ".yml"}
CLAIM_LIST_KEYS = ("claims", "claims_to_verify", "claimsToVerify",
                   "assertions", "questions")
PARAGRAPH_LIST_KEYS = ("paragraphs", "paragraph", "passages", "sentences")
CLAIM_ID_KEYS = ("claim_id", "claimId", "id", "question_id", "questionId",
                 "assertion_id")
CLAIM_TEXT_KEYS = ("text", "claim", "statement", "question", "content")
PID_KEYS = ("paragraph_id", "paragraphId", "pid", "id")
PTEXT_KEYS = ("text", "content", "paragraph", "sentence")
DOC_ID_KEYS = ("doc_id", "docId", "document_id", "documentId", "source_id",
               "sourceId", "paper_id", "paperId")
WS = re.compile(r"\s+")
LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
CJK = re.compile(r"[一-鿿]+")
NUMBER = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?")
STOPWORDS = {"the", "and", "was", "were", "with", "from", "that", "this",
             "has", "have", "had", "for", "not", "but", "are", "its",
             "their", "than", "then", "into", "using", "used", "show",
             "shows", "shown", "observed", "reported", "reached", "between",
             "below", "above", "after", "before", "during", "study",
             "group", "groups", "compared", "respectively", "also"}
COMPARATOR = re.compile(
    r"(高于|低于|超过|少于|大于|小于|相比|较之|优于|劣于|"
    r"more than|less than|higher than|lower than|greater than|fewer than|"
    r"compared (?:with|to)|versus|\bvs\.?\b)", re.IGNORECASE)
NEGATION = re.compile(
    r"(未|未见|未发现|无显著|没有|并非|不能|不支持|不再|不会|不显著|"
    r"\bnot\b|\bno\b|\bnever\b|\bwithout\b|did not|does not|failed to|"
    r"no significant)", re.IGNORECASE)
SIGNIFICANCE = re.compile(
    r"(显著|显著性|p\s*[<=>]\s*0?\.\d+|P\s*value|confidence interval|"
    r"95%\s*CI|\bsignificant(?:ly)?\b|\bsignificance\b)", re.IGNORECASE)
CAUSAL = re.compile(
    r"(导致|因为|由于|因而|使|证明|表明|cause[sd]?|lead to|result in|"
    r"prove[sd]?|due to|because|therefore)", re.IGNORECASE)
RANGE = re.compile(
    r"(至|到|不超过|至少|between|range|from .* to |–|—|-|±)", re.IGNORECASE)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8",
                                           errors="replace"))


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    return WS.sub(" ", text).strip().casefold()


def first_value(mapping, keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def numeric_values(text):
    return {m.group(0).replace(",", ".") for m in NUMBER.finditer(str(text or ""))}


def tokenize(text):
    normalized = normalize(text)
    latin = {m.group(0).lower() for m in LATIN.finditer(normalized)}
    latin -= STOPWORDS
    cjk = set()
    for sequence in CJK.findall(normalized):
        if len(sequence) < 2:
            cjk.add(sequence)
        else:
            for index in range(len(sequence) - 1):
                cjk.add(sequence[index:index + 2])
    return latin, cjk, numeric_values(normalized)


def collect_claims(data):
    claims, seen = [], set()

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in CLAIM_LIST_KEYS and isinstance(item, list):
                    for entry in item:
                        if not isinstance(entry, dict):
                            continue
                        cid = first_value(entry, CLAIM_ID_KEYS)
                        text = first_value(entry, CLAIM_TEXT_KEYS)
                        if cid is not None and text is not None:
                            cid = str(cid)
                            if cid not in seen:
                                seen.add(cid)
                                claims.append({"claim_id": cid,
                                               "text": str(text)})
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(data)
    return claims


def collect_paragraphs(data):
    paragraphs = []

    def visit(value, inherited_doc=None):
        if isinstance(value, dict):
            doc_id = first_value(value, DOC_ID_KEYS) or inherited_doc
            for key, item in value.items():
                if key in PARAGRAPH_LIST_KEYS and isinstance(item, list):
                    for entry in item:
                        if not isinstance(entry, dict):
                            continue
                        pid = first_value(entry, PID_KEYS)
                        text = first_value(entry, PTEXT_KEYS)
                        entry_doc = first_value(entry, DOC_ID_KEYS) or doc_id
                        if pid is not None and text is not None:
                            paragraphs.append({
                                "paragraph_id": str(pid),
                                "doc_id": str(entry_doc) if entry_doc else None,
                                "section": entry.get("section")
                                or entry.get("heading"),
                                "text": str(text),
                            })
                        visit(entry, entry_doc)
                else:
                    visit(item, doc_id)
        elif isinstance(value, list):
            for item in value:
                visit(item, inherited_doc)

    visit(data)
    return paragraphs


def load_input(input_path):
    path = Path(input_path)
    claims, paragraphs, texts = [], [], []
    case_id = None
    warnings = []
    json_files = []
    if not path.exists():
        return {"claims": [], "paragraphs": [], "texts": [], "case_id": None,
                "warnings": ["input path not found"], "json_files": []}
    files = [path] if path.is_file() else sorted(
        p for p in path.rglob("*") if p.is_file())
    files.sort(key=lambda p: (0 if "claim" in p.name.lower()
                              else 1 if "case" in p.name.lower() else 2))
    for file in files:
        if file.stat().st_size > 5_000_000:
            continue
        if file.suffix.lower() == ".json":
            try:
                payload = load_json(file)
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append("cannot parse %s: %s" % (file, exc))
                continue
            json_files.append(str(file))
            for claim in collect_claims(payload):
                if claim["claim_id"] not in {c["claim_id"] for c in claims}:
                    claims.append(claim)
            paragraphs.extend(collect_paragraphs(payload))
            if case_id is None and isinstance(payload, dict):
                case_id = payload.get("case_id") or payload.get("caseId")
        elif file.suffix.lower() in TEXT_SUFFIXES:
            try:
                texts.append(file.read_text(encoding="utf-8",
                                            errors="replace"))
            except OSError:
                pass
    # Deduplicate paragraphs by (doc_id, paragraph_id).
    unique, seen = [], set()
    for paragraph in paragraphs:
        key = (paragraph["doc_id"], paragraph["paragraph_id"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(paragraph)
    return {"claims": claims, "paragraphs": unique, "texts": texts,
            "case_id": case_id, "warnings": warnings,
            "json_files": json_files}


def markers(text):
    return {
        "has_comparator": bool(COMPARATOR.search(text)),
        "has_negation": bool(NEGATION.search(text)),
        "has_significance": bool(SIGNIFICANCE.search(text)),
        "has_causal": bool(CAUSAL.search(text)),
        "has_range": bool(RANGE.search(text)),
        "numbers": sorted(numeric_values(text)),
    }


def suggest_type(text):
    found = markers(text)
    if found["has_significance"]:
        return "significance"
    if found["has_comparator"]:
        return "comparison"
    if found["has_causal"]:
        return "causal"
    if found["has_range"] and found["numbers"]:
        return "range"
    if found["numbers"]:
        return "numeric"
    if found["has_negation"]:
        return "negation"
    return "fact"


def rank_candidates(claim_text, paragraphs, top):
    claim_latin, claim_cjk, claim_numbers = tokenize(claim_text)
    ranked = []
    for paragraph in paragraphs:
        para_latin, para_cjk, para_numbers = tokenize(paragraph["text"])
        shared_numbers = claim_numbers & para_numbers
        shared_latin = claim_latin & para_latin
        shared_cjk = claim_cjk & para_cjk
        score = 0.0
        if claim_numbers:
            score += 0.45 * len(shared_numbers) / len(claim_numbers)
        if claim_latin:
            score += 0.30 * len(shared_latin) / len(claim_latin)
        if claim_cjk:
            score += 0.25 * len(shared_cjk) / len(claim_cjk)
        if not (shared_numbers or shared_latin or shared_cjk):
            continue
        reasons = []
        if shared_numbers:
            reasons.append("numbers: " + ", ".join(sorted(shared_numbers)))
        if shared_latin:
            reasons.append("terms: " + ", ".join(sorted(shared_latin)[:4]))
        if shared_cjk:
            reasons.append("cjk: " + ", ".join(sorted(shared_cjk)[:4]))
        ranked.append({
            "paragraph_id": paragraph["paragraph_id"],
            "doc_id": paragraph["doc_id"],
            "section": paragraph["section"],
            "score": round(min(1.0, score), 3),
            "reasons": reasons,
            "preview": paragraph["text"][:160],
        })
    ranked.sort(key=lambda item: (
        -item["score"], item["doc_id"] or "", item["paragraph_id"]))
    return ranked[:top]


def build_index(input_path, top=5):
    bundle = load_input(input_path)
    claims = []
    for claim in bundle["claims"]:
        candidates = rank_candidates(claim["text"], bundle["paragraphs"], top)
        claims.append({
            "claim_id": claim["claim_id"],
            "text": claim["text"],
            "suggested_claim_type": suggest_type(claim["text"]),
            "markers": markers(claim["text"]),
            "candidates": candidates,
        })
    warnings = list(bundle["warnings"])
    if not claims:
        warnings.append("no claims found; check claims-to-verify.json or the "
                        "claims/claims_to_verify/assertions key")
    if claims and not bundle["paragraphs"]:
        warnings.append("no paragraph text found; extract PDF/Office text "
                        "before locating evidence")
    without = [claim["claim_id"] for claim in claims if not claim["candidates"]]
    if without:
        warnings.append("claims without any candidate: " + ", ".join(without))
    return {
        "ok": bool(claims),
        "case_id": bundle["case_id"],
        "claims": claims,
        "paragraphs": [
            {"paragraph_id": p["paragraph_id"], "doc_id": p["doc_id"],
             "section": p["section"], "chars": len(p["text"]),
             "preview": p["text"][:120]}
            for p in bundle["paragraphs"]
        ],
        "stats": {"claims": len(claims),
                  "paragraphs": len(bundle["paragraphs"]),
                  "claims_with_candidates": len(claims) - len(without),
                  "claims_without_candidates": len(without),
                  "json_files": bundle["json_files"]},
        "warnings": warnings,
        "usage": "Candidates are recall hints only; read full paragraphs and "
                 "neighbours, then record doc_id + paragraph_id + verbatim quote.",
    }


def run_self_test():
    input_data = {
        "case_id": "inspect-self-test",
        "documents": [{
            "doc_id": "d1",
            "paragraphs": [
                {"paragraph_id": "p1",
                 "text": "The catalyst was tested between 250 and 400 C."},
                {"paragraph_id": "p2",
                 "text": "At 350 C the CO2 conversion reached 92%."},
                {"paragraph_id": "p3",
                 "text": "At 250 C the conversion was below 50%."},
            ],
        }],
        "claims": [
            {"claim_id": "c1",
             "text": "At 350 C the CO2 conversion was 92%."},
            {"claim_id": "c2",
             "text": "At 250 C the conversion was below 50%."},
        ],
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        (root / "claims-to-verify.json").write_text(
            json.dumps(input_data, ensure_ascii=False), encoding="utf-8")
        index = build_index(root)
    first = {claim["claim_id"]: (claim["candidates"][0]["paragraph_id"]
                                 if claim["candidates"] else None)
             for claim in index["claims"]}
    checks = {
        "claims_extracted": index["stats"]["claims"] == 2,
        "paragraphs_extracted": index["stats"]["paragraphs"] == 3,
        "c1_ranked_p2": first.get("c1") == "p2",
        "c2_ranked_p3": first.get("c2") == "p3",
    }
    return {"ok": all(checks.values()), "self_test": True, "checks": checks,
            "sample": index}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", default="/input")
    parser.add_argument("--out", default=None)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        report = run_self_test()
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0 if report["ok"] else 1

    try:
        index = build_index(args.input, args.top)
    except OSError as exc:
        print(json.dumps({"ok": False,
                          "problems": ["cannot read input: %s" % exc]},
                         ensure_ascii=False, indent=2))
        return 2
    text = json.dumps(index, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    if args.json:
        print(text)
    else:
        print("case_id: %s" % index["case_id"])
        print("claims: %(claims)d  paragraphs: %(paragraphs)d" %
              index["stats"])
        for claim in index["claims"]:
            print("- %s [%s] %s" % (claim["claim_id"],
                                    claim["suggested_claim_type"],
                                    claim["text"][:80]))
            for candidate in claim["candidates"][:3]:
                print("    %s score=%.3f %s" %
                      (candidate["paragraph_id"], candidate["score"],
                       "; ".join(candidate["reasons"])))
        for warning in index["warnings"]:
            print("! " + warning)
    return 0 if index["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
