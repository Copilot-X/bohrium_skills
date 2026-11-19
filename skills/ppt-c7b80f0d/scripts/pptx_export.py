#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export an editable PPTX to a real PDF, preferring the platform renderer.

Probe order: office-renderer -> LibreOffice/soffice -> content-faithful
matplotlib fallback.  The fallback is explicitly labelled; it is never
presented as a native PowerPoint render.

Usage:
    python3 pptx_export.py --pptx /app/output/presentation.pptx \
        --out /app/output/preview/presentation.pdf \
        --png-dir /app/output/preview --json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def emit(data, code=0):
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return code


def run(cmd, timeout=240):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "not found"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError as exc:
        return False, "cannot execute: %s" % exc
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return False, "exit %s: %s" % (proc.returncode, out[:500])
    return True, out[:500]


def valid_pdf(path):
    p = Path(path)
    return p.is_file() and p.stat().st_size > 0 and p.read_bytes()[:5] == b"%PDF-"


def office_convert(pptx, out_pdf):
    attempts = []
    binaries = []
    for name in ("office-renderer", "soffice", "libreoffice"):
        if shutil.which(name):
            binaries.append((name, shutil.which(name)))
    for name, binary in binaries:
        if name == "office-renderer":
            cmds = [
                [binary, "--input", str(pptx), "--output", str(out_pdf)],
                [binary, str(pptx), str(out_pdf)],
            ]
        else:
            tmp = Path(tempfile.mkdtemp(prefix="pptx-pdf-"))
            cmds = [
                [binary, "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(pptx)],
            ]
        for cmd in cmds:
            ok, detail = run(cmd)
            attempts.append({"tool": name, "ok": ok, "detail": detail})
            if ok:
                candidate = out_pdf
                if name != "office-renderer":
                    candidate = tmp / (Path(pptx).stem + ".pdf")
                if valid_pdf(candidate):
                    if candidate != out_pdf:
                        out_pdf.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(candidate, out_pdf)
                    return True, {"renderer": name, "attempts": attempts}
    return False, {"attempts": attempts}


def pdf_to_png(pdf, png_dir, dpi=150):
    tool = shutil.which("pdftoppm")
    if not tool:
        return []
    png_dir.mkdir(parents=True, exist_ok=True)
    prefix = png_dir / "slide"
    ok, detail = run([tool, "-png", "-r", str(dpi), str(pdf), str(prefix)])
    if not ok:
        return []
    return [str(p) for p in sorted(png_dir.glob("slide*.png"))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--png-dir", default=None)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    pptx = Path(args.pptx)
    out = Path(args.out)
    if not pptx.is_file():
        return emit({"ok": False, "error": "PPTX not found: %s" % pptx,
                     "exceptions": [{"code": "input_error", "detail": "missing pptx"}]}, 2)
    out.parent.mkdir(parents=True, exist_ok=True)

    ok, detail = office_convert(pptx, out)
    if ok:
        png = pdf_to_png(out, Path(args.png_dir), args.dpi) if args.png_dir else []
        return emit({"ok": True, "pptx": str(pptx), "pdf": str(out),
                     "png": png, "engine": detail.get("renderer"),
                     "native": True, "attempts": detail.get("attempts", [])})

    # Fallback: content-faithful preview from the PPTX text and images.
    fallback = Path(__file__).with_name("pptx_to_preview.py")
    if not fallback.is_file():
        return emit({"ok": False, "error": "no native renderer; fallback missing",
                     "attempts": detail.get("attempts", []),
                     "exceptions": [{"code": "render_unavailable",
                                     "detail": "no office renderer and no fallback"}]}, 1)
    import importlib
    sys.path.insert(0, str(fallback.parent))
    preview = importlib.import_module("pptx_to_preview")
    spec = preview.build_spec(pptx)
    deck_pdf = importlib.import_module("deck_pdf")
    good, extra = deck_pdf.render(spec, out, args.png_dir, args.dpi)
    if good:
        return emit({"ok": True, "pptx": str(pptx), "pdf": str(out),
                     "png": extra.get("png", []), "engine": "content-fallback",
                     "native": False, "attempts": detail.get("attempts", []),
                     "note": "not a pixel-faithful template render"})
    return emit({"ok": False, "error": extra, "attempts": detail.get("attempts", []),
                 "exceptions": [{"code": "render_unavailable", "detail": str(extra)}]}, 1)


if __name__ == "__main__":
    raise SystemExit(main())
