"""Magazine PDF processing: render each page to an image and extract its text.

Uses PyMuPDF (fitz). Born-digital publisher PDFs (e.g. 금수강산) should yield
clean Korean text per page; image-only pages return empty text and are shown
without a translation (graceful). Degrades to no-op if PyMuPDF is unavailable.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from . import config

log = logging.getLogger("magazines")


def issue_id_from_url(url: str) -> str:
    """Derive a stable id from the PDF URL (the numeric timestamp filename)."""
    stem = Path(url.split("?")[0]).stem
    m = re.search(r"\d{6,}", stem)
    return m.group(0) if m else (stem or "issue")


def render_and_extract(pdf_path, out_dir, max_pages: int | None = None) -> list[dict]:
    """Render each page to a PNG in out_dir and extract its text.

    Returns [{"img": "p001.png", "ko": "<page text>"}], one per page.
    Returns [] if PyMuPDF can't be loaded or the PDF can't be opened."""
    try:
        import fitz  # PyMuPDF
    except Exception as e:
        log.warning("magazines: PyMuPDF unavailable: %s", e)
        return []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        log.warning("magazines: cannot open PDF %s: %s", pdf_path, e)
        return []

    cap = max_pages or config.MAGAZINE_MAX_PAGES
    pages: list[dict] = []
    for i, page in enumerate(doc):
        if cap and i >= cap:
            break
        name = f"p{i + 1:03d}.png"
        try:
            pix = page.get_pixmap(dpi=config.MAGAZINE_DPI)
            pix.save(str(out_dir / name))
        except Exception as e:
            log.warning("magazines: render page %d failed: %s", i + 1, e)
            continue
        try:
            text = (page.get_text("text") or "").strip()
        except Exception:
            text = ""
        pages.append({"img": name, "ko": text})
    doc.close()
    ko_pages = sum(1 for p in pages if p["ko"])
    log.info("magazines: rendered %d page(s), %d with extractable text", len(pages), ko_pages)
    return pages
