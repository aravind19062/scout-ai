"""Content extraction and cleaning for HTML and PDF documents."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import trafilatura
from pypdf import PdfReader

from app.core.errors import ExtractionError

MIN_TEXT_CHARS = 200
MAX_PDF_PAGES = 200


@dataclass
class ExtractedDocument:
    title: str
    text: str
    published_at: str | None
    method: str


def clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # de-hyphenate line breaks (PDFs)
    lines = [ln.strip() for ln in text.splitlines()]
    out, blank = [], 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank == 1:
                out.append("")
            continue
        blank = 0
        out.append(ln)
    return "\n".join(out).strip()


def extract_html(body: bytes | str, url: str | None = None) -> ExtractedDocument:
    html = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_precision=True)
    meta = trafilatura.extract_metadata(html, default_url=url)
    title = (meta.title if meta and meta.title else "") or ""
    published = meta.date if meta and meta.date else None
    method = "trafilatura"
    if not text or len(text) < MIN_TEXT_CHARS:
        # Fallback: trafilatura's baseline extractor is more permissive.
        _, text_bl, _ = trafilatura.baseline(html)
        if text_bl and len(text_bl) > len(text or ""):
            text, method = text_bl, "trafilatura-baseline"
    text = clean_text(text or "")
    if len(text) < MIN_TEXT_CHARS:
        raise ExtractionError("Page contains too little readable text")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    return ExtractedDocument(title=title[:500], text=text, published_at=published, method=method)


def extract_pdf(body: bytes) -> ExtractedDocument:
    try:
        reader = PdfReader(io.BytesIO(body))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as e:
                raise ExtractionError("PDF is encrypted") from e
        pages = []
        for page in reader.pages[:MAX_PDF_PAGES]:
            pages.append(page.extract_text() or "")
        meta = reader.metadata or {}
        title = str(meta.get("/Title") or "").strip()
        created = meta.get("/CreationDate")
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"Could not parse PDF: {type(e).__name__}") from e
    text = clean_text("\n\n".join(pages))
    if len(text) < MIN_TEXT_CHARS:
        raise ExtractionError("PDF contains too little extractable text (scanned images are not OCR'd)")
    published = None
    if created and isinstance(created, str):
        m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?", created)
        if m:
            published = "-".join(g for g in m.groups() if g)
    return ExtractedDocument(title=title[:500], text=text, published_at=published, method="pypdf")


def extract_plain(body: bytes) -> ExtractedDocument:
    text = clean_text(body.decode("utf-8", errors="replace"))
    if len(text) < 20:
        raise ExtractionError("Document is empty")
    return ExtractedDocument(title="", text=text, published_at=None, method="plain")


def extract_by_type(body: bytes, content_type: str, url: str | None = None) -> ExtractedDocument:
    if content_type == "application/pdf" or body[:5] == b"%PDF-":
        return extract_pdf(body)
    if content_type in ("text/plain", "text/markdown"):
        return extract_plain(body)
    return extract_html(body, url)
