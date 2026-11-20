#!/usr/bin/env python3
"""Export an editable pathway SVG with the first renderer that is actually
available.  Probe order: cairosvg (Python module) -> rsvg-convert -> inkscape.

Nothing is fabricated.  Each candidate output is verified by magic bytes
(PNG \\x89PNG / PDF %PDF) and size; an invalid file is deleted before the next
renderer is tried.  If no renderer works, a self-contained HTML preview with the
SVG inlined is written and the report carries an `export_unavailable` exception.

Usage:
    python3 export_pathway.py --svg /app/output/figures/fig3.svg \
        --outdir /app/output --dpi 300 --json
    python3 export_pathway.py --svg fig3.svg --formats png --dpi 300 --json
    python3 export_pathway.py --svg fig3.svg --outdir /app/output \
        --update-result /app/output/result.json --json

`--update-result` only touches the media-facing fields (artifacts,
editable_source, export_artifact, status/task_status, compliance_checks, checks,
exceptions).  It never invents a figure, never rewrites pathway/content_map and
never changes case_id.  A malformed result is reported, not overwritten.

Exit codes: 0 = every requested format exported, 1 = partial (SVG is still
delivered), 2 = SVG missing/unparseable or invalid arguments.  Stdlib only.
"""
import argparse
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

TOOL_ORDER = ("cairosvg", "rsvg-convert", "inkscape")
FORMATS = ("pdf", "png")


class ExportError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []

    def as_dict(self):
        return {"ok": False, "status": "input_error", "error_code": self.code,
                "message": self.message, "details": self.details}


def read_svg(path):
    p = Path(path)
    if not p.is_file():
        raise ExportError("svg_missing", "SVG not found: %s" % p,
                          ["write figures/fig3.svg before exporting"])
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ExportError("svg_unreadable", "cannot read SVG: %s" % exc)
    if not raw.strip():
        raise ExportError("svg_empty", "SVG is empty: %s" % p)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ExportError("svg_invalid", "SVG is not well-formed XML: %s" % exc,
                          ["fix the XML or hand-write a smaller SVG"])
    if root.tag.split("}")[-1] != "svg":
        raise ExportError("svg_invalid", "root element is not <svg>")
    notes = {
        "viewBox": root.get("viewBox"),
        "width": root.get("width"),
        "height": root.get("height"),
        "text_elements": raw.count("<text"),
        "named_groups": raw.count("<g id="),
        "has_script": "<script" in raw.lower(),
        "external_refs": sum(raw.count(x) for x in
                             ('href="http://', "href='http://",
                              'href="https://', "href='https://", 'href="file:')),
    }
    return raw, notes


def probe_tools():
    status = {"cairosvg": False, "rsvg-convert": False, "inkscape": False}
    try:
        import importlib.util
        status["cairosvg"] = importlib.util.find_spec("cairosvg") is not None
    except Exception:
        status["cairosvg"] = False
    for name in ("rsvg-convert", "inkscape"):
        status[name] = bool(shutil.which(name))
    return status


def _run(cmd):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return False, "binary not found"
    except subprocess.TimeoutExpired:
        return False, "timed out after 180 s"
    except OSError as exc:
        return False, "cannot execute: %s" % exc
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return False, "exit %d%s" % (proc.returncode,
                                     (": " + output[:400]) if output else "")
    return True, output[:400]


def try_cairosvg(fmt, svg, target, dpi):
    try:
        import cairosvg
    except Exception as exc:
        return False, "unavailable: %s" % exc
    try:
        if fmt == "pdf":
            cairosvg.svg2pdf(url=str(svg), write_to=str(target))
        else:
            cairosvg.svg2png(url=str(svg), write_to=str(target),
                             scale=max(0.1, dpi / 96.0))
        return True, "cairosvg"
    except Exception as exc:
        return False, "render failed: %s: %s" % (type(exc).__name__, exc)


def try_rsvg(fmt, svg, target, dpi):
    binary = shutil.which("rsvg-convert")
    if not binary:
        return False, "unavailable"
    if fmt == "pdf":
        cmd = [binary, "-f", "pdf", "-o", str(target), str(svg)]
    else:
        cmd = [binary, "-f", "png", "-d", str(dpi), "-p", str(dpi),
               "-o", str(target), str(svg)]
    return _run(cmd)


def try_inkscape(fmt, svg, target, dpi):
    binary = shutil.which("inkscape")
    if not binary:
        return False, "unavailable"
    if fmt == "pdf":
        cmd = [binary, str(svg), "--export-type=pdf", "--export-filename=%s" % target]
    else:
        cmd = [binary, str(svg), "--export-type=png", "--export-filename=%s" % target,
               "--export-dpi=%d" % dpi]
    ok, detail = _run(cmd)
    if ok:
        return True, detail
    # Inkscape 0.92 fallback syntax.
    if fmt == "pdf":
        legacy = [binary, "--export-pdf=%s" % target, str(svg)]
    else:
        legacy = [binary, "--export-png=%s" % target, "--export-dpi=%d" % dpi, str(svg)]
    ok2, detail2 = _run(legacy)
    if ok2:
        return True, detail2
    return False, "%s / legacy: %s" % (detail, detail2)


def verify_output(path, fmt):
    p = Path(path)
    if not p.is_file():
        return False, "no output file", {}
    size = p.stat().st_size
    if size <= 0:
        return False, "empty output file", {}
    try:
        with open(p, "rb") as handle:
            head = handle.read(24)
    except OSError as exc:
        return False, "cannot read output: %s" % exc, {}
    if fmt == "png":
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return False, "PNG signature missing", {}
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        if width <= 0 or height <= 0:
            return False, "PNG has invalid dimensions", {}
        return True, "", {"width_px": width, "height_px": height, "bytes": size}
    if not head.startswith(b"%PDF"):
        return False, "PDF signature missing", {}
    return True, "", {"bytes": size}


def make_html(svg_text, svg_path, attempts, out_path, note):
    start = svg_text.find("<svg")
    end = svg_text.rfind("</svg>")
    if start >= 0 and end > start:
        inline = svg_text[start:end + len("</svg>")]
    else:
        inline = "<pre>%s</pre>" % escape(svg_text[:2000])
    title = Path(svg_path).stem
    applied = ", ".join("%s:%s" % (a["format"], a["tool"]) for a in attempts) or "none"
    html = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>%(title)s - SVG preview</title>
<style>
  body { margin: 0; padding: 24px; background: #f3f3f3;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; color: #222; }
  .sheet { max-width: 1200px; margin: 0 auto; background: #fff; padding: 24px;
           border: 1px solid #ddd; border-radius: 6px; }
  h1 { font-size: 16px; font-weight: 600; margin: 0 0 16px; }
  .canvas svg { max-width: 100%%; height: auto; display: block; }
  .note { margin-top: 16px; font-size: 12px; color: #666; line-height: 1.5; }
  code { background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }
</style>
</head>
<body>
<main class="sheet">
<h1>%(title)s &mdash; editable SVG preview</h1>
<div class="canvas">
%(inline)s
</div>
<p class="note">%(note)s<br>
Source: <code>%(source)s</code>. Renderer probes: %(applied)s.
Generated %(stamp)s by export_pathway.py; this page inlines the SVG source and
is not a PNG/PDF export.</p>
</main>
</body>
</html>
""" % {"title": escape(title), "inline": inline, "note": escape(note),
       "source": escape(str(svg_path)), "applied": escape(applied),
       "stamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def rel_path(path, root):
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path).replace("\\", "/")


def update_result(result_path, svg_path, root, artifacts, export_artifact,
                  html_preview, exceptions, tools, dpi, warnings):
    """Merge honest media fields into an existing result.json."""
    result_path = Path(result_path)
    if not result_path.is_file():
        return {"updated": False, "reason": "result file not found"}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"updated": False, "reason": "cannot parse result: %s" % exc}
    if not isinstance(result, dict):
        return {"updated": False, "reason": "result root is not an object"}

    editable_rel = rel_path(svg_path, root)
    result["editable_source"] = {"path": editable_rel, "format": "svg"}

    previous = [a for a in result.get("artifacts") or [] if isinstance(a, dict)]
    kept = [a for a in previous
            if str(a.get("role") or "").lower() not in ("export", "preview")]
    merged = kept + artifacts
    seen = set()
    deduped = []
    for item in merged:
        key = (str(item.get("role")), str(item.get("path")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    result["artifacts"] = deduped

    result["export_artifact"] = export_artifact
    previous_status = str(result.get("status") or "")
    if previous_status == "input_error":
        status = "input_error"
    elif previous_status == "insufficient_evidence":
        status = "insufficient_evidence"
    else:
        status = "answered" if export_artifact else "partial"
    result["status"] = status
    result["task_status"] = {"answered": "completed", "partial": "partial",
                             "insufficient_evidence": "partial",
                             "input_error": "input_error"}.get(status, "partial")

    old_exceptions = [e for e in result.get("exceptions") or []
                      if isinstance(e, dict)
                      and str(e.get("code")) not in ("export_unavailable",
                                                     "export_pending")]
    if not export_artifact:
        old_exceptions.append({
            "code": "export_unavailable",
            "detail": "no working renderer among cairosvg, rsvg-convert, inkscape; "
                      "delivered SVG + HTML preview instead of fabricating PNG/PDF",
        })
    result["exceptions"] = old_exceptions + [e for e in exceptions
                                             if str(e.get("code")) not in
                                             ("export_unavailable", "export_pending")]

    checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
    checks["export_from_source"] = bool(export_artifact)
    checks["preview_present"] = bool(artifacts and any(
        str(a.get("role")) == "preview" for a in artifacts))
    checks["preview_format"] = ("html" if html_preview and not any(
        str(a.get("format")) == "png" for a in artifacts) else "png")
    result["checks"] = checks

    compliance = result.get("compliance_checks")
    if not isinstance(compliance, dict):
        compliance = {}
    compliance["export_artifact_present"] = bool(export_artifact)
    compliance["export_signature"] = (
        str(export_artifact.get("format")) if export_artifact else None)
    compliance["export_dpi"] = int(dpi) if export_artifact else None
    compliance["export_tools_available"] = tools
    compliance["html_preview"] = bool(html_preview)
    compliance["status"] = status
    result["compliance_checks"] = compliance

    tmp = result_path.with_suffix(result_path.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(result_path)
    return {"updated": True, "status": status}


def export(svg_path, outdir, paths, formats, dpi):
    svg_text, notes = read_svg(svg_path)
    warnings = []
    if not notes["viewBox"]:
        warnings.append("SVG has no viewBox; raster size may follow width/height")
    if notes["text_elements"] == 0:
        warnings.append("SVG has no <text> elements; labels may be outlines")
    if notes["has_script"]:
        warnings.append("SVG contains <script>; preview may not be fully static")
    if notes["external_refs"]:
        warnings.append("SVG references an external resource; it is not self-contained")

    tools = probe_tools()
    attempts = []
    artifacts = []
    exceptions = []
    exported = {}

    for fmt in formats:
        target = Path(paths[fmt])
        target.parent.mkdir(parents=True, exist_ok=True)
        for tool in TOOL_ORDER:
            target.unlink(missing_ok=True)
            if tool == "cairosvg":
                ok, detail = try_cairosvg(fmt, svg_path, target, dpi)
            elif tool == "rsvg-convert":
                ok, detail = try_rsvg(fmt, svg_path, target, dpi)
            else:
                ok, detail = try_inkscape(fmt, svg_path, target, dpi)
            entry = {"format": fmt, "tool": tool, "ok": False, "detail": detail}
            if ok:
                valid, why, meta = verify_output(target, fmt)
                entry["ok"] = valid
                if not valid:
                    entry["detail"] = "output rejected: %s" % why
                    target.unlink(missing_ok=True)
                else:
                    entry.update(meta)
                    entry["exported_by"] = tool
                    attempts.append(entry)
                    artifact = {"role": "export",
                                "path": rel_path(target, outdir),
                                "format": fmt,
                                "bytes": meta.get("bytes"),
                                "exported_by": tool}
                    if fmt == "png":
                        artifact["width_px"] = meta.get("width_px")
                        artifact["height_px"] = meta.get("height_px")
                        artifact["dpi"] = dpi
                        artifact["role"] = "preview"
                    artifacts.append(artifact)
                    exported[fmt] = artifact
                    break
            attempts.append(entry)
        if fmt not in exported:
            tried = ", ".join("%s=%s" % (t, "yes" if tools[t] else "no")
                              for t in TOOL_ORDER)
            exceptions.append({
                "code": "export_unavailable",
                "detail": "cannot export %s: no working renderer (%s)" % (fmt, tried),
            })

    html_preview = None
    if "png" not in exported:
        html_target = Path(paths["html"])
        try:
            html_preview = make_html(
                svg_text, svg_path, attempts, html_target,
                "No raster renderer was available, so this preview inlines the "
                "SVG instead of fabricating a PNG/PDF.")
            artifacts.append({"role": "preview",
                              "path": rel_path(html_target, outdir),
                              "format": "html"})
        except OSError as exc:
            warnings.append("cannot write HTML preview: %s" % exc)

    export_artifact = None
    if "pdf" in exported:
        export_artifact = dict(exported["pdf"])
        export_artifact["role"] = "export"
    elif "png" in exported:
        export_artifact = dict(exported["png"])
        export_artifact["role"] = "export"

    complete = len(exported) == len(formats)
    status = "answered" if complete else "partial"
    report = {
        "ok": complete,
        "status": status,
        "svg": str(svg_path),
        "formats_requested": list(formats),
        "dpi": dpi,
        "tools": tools,
        "svg_notes": notes,
        "attempts": attempts,
        "artifacts": artifacts,
        "export_artifact": export_artifact,
        "html_preview": ({"role": "preview", "path": rel_path(html_preview, outdir),
                          "format": "html"} if html_preview else None),
        "exceptions": exceptions,
        "warnings": warnings,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description="SVG -> PDF/PNG with honest fallback")
    parser.add_argument("--svg", required=True)
    parser.add_argument("--outdir", default=None)
    parser.add_argument("--pdf", default=None, help="path relative to --outdir")
    parser.add_argument("--png", default=None, help="path relative to --outdir")
    parser.add_argument("--html", default=None, help="path relative to --outdir")
    parser.add_argument("--formats", default="pdf,png")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--update-result", default=None,
                        help="existing result.json to merge media fields into")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    formats = tuple(f.strip().lower() for f in args.formats.replace(" ", ",").split(",")
                    if f.strip())
    invalid = [f for f in formats if f not in FORMATS]
    if invalid or not formats:
        print(json.dumps({"ok": False, "status": "input_error",
                          "error_code": "invalid_formats",
                          "message": "formats must be a subset of pdf,png",
                          "details": list(formats)}, ensure_ascii=False, indent=2))
        return 2
    if args.dpi < 72 or args.dpi > 1200:
        print(json.dumps({"ok": False, "status": "input_error",
                          "error_code": "invalid_dpi",
                          "message": "dpi must be within 72-1200",
                          "details": [args.dpi]}, ensure_ascii=False, indent=2))
        return 2

    svg_path = Path(args.svg)
    if args.outdir:
        outdir = Path(args.outdir)
    elif Path("/app/output").is_dir():
        outdir = Path("/app/output")
    else:
        outdir = svg_path.parent
    stem = svg_path.stem
    paths = {
        "pdf": Path(args.pdf) if args.pdf else Path("figures") / (stem + ".pdf"),
        "png": Path(args.png) if args.png else Path("preview") / (stem + ".png"),
        "html": Path(args.html) if args.html else Path("preview") / (stem + ".html"),
    }
    for key, value in list(paths.items()):
        if not value.is_absolute():
            paths[key] = outdir / value

    try:
        report = export(svg_path, outdir, paths, formats, args.dpi)
    except ExportError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:  # never leak a traceback
        print(json.dumps({"ok": False, "status": "input_error",
                          "error_code": "internal_error",
                          "message": "%s: %s" % (type(exc).__name__, exc),
                          "details": ["deliver the SVG and record export_unavailable"]},
                         ensure_ascii=False, indent=2))
        return 2

    if args.update_result:
        report["result_update"] = update_result(
            args.update_result, svg_path, outdir, report["artifacts"],
            report["export_artifact"], report["html_preview"],
            report["exceptions"], report["tools"], args.dpi, report["warnings"])

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("%s: %s" % (report["status"], svg_path))
        for item in report["artifacts"]:
            print("  %s -> %s" % (item["role"], item["path"]))
        for item in report["exceptions"]:
            print("  ! %s: %s" % (item["code"], item["detail"]))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never leak a traceback to the harness
        print(json.dumps({"ok": False, "status": "input_error",
                          "error_code": "internal_error",
                          "message": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False, indent=2))
        sys.exit(2)
