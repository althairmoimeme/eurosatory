"""PDF text extractor (PyMuPDF)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None  # type: ignore[assignment]


@dataclass
class PdfText:
    url: str
    page_count: int
    text: str
    error: Optional[str] = None


def extract_pdf_text(content: bytes, url: str = "", max_pages: int = 30, max_chars: int = 80_000) -> PdfText:
    """Return cleaned text from a PDF byte string.

    Trims to ``max_pages`` and ``max_chars`` so we never feed gigantic catalogs
    into the rule-based extractor or LLM.
    """
    if fitz is None:
        return PdfText(url=url, page_count=0, text="", error="pymupdf_not_installed")
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        logger.warning("pdf open failed for {}: {}", url, e)
        return PdfText(url=url, page_count=0, text="", error=f"open_failed:{e}")
    try:
        chunks: list[str] = []
        page_count = min(len(doc), max_pages)
        total = 0
        for i in range(page_count):
            page = doc[i]
            try:
                txt = page.get_text("text") or ""
            except Exception:  # noqa: BLE001
                txt = ""
            txt = " ".join(txt.split())  # collapse whitespace
            if not txt:
                continue
            chunks.append(txt)
            total += len(txt)
            if total >= max_chars:
                break
        text = "\n".join(chunks)
        if len(text) > max_chars:
            text = text[:max_chars]
        return PdfText(url=url, page_count=page_count, text=text)
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
