"""PDF parser with optional pypdf/pymupdf backends and native stream fallback."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from .base import ParsedDocument, ParsedSection


def _parse_with_pypdf(raw_bytes: bytes) -> tuple[str, list[ParsedSection]]:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
    sections: list[ParsedSection] = []
    full_texts: list[str] = []

    for idx, page in enumerate(reader.pages):
        page_text = page.extract_text() or ""
        if page_text.strip():
            sections.append(
                ParsedSection(
                    title=f"Page {idx + 1}",
                    content=page_text.strip(),
                    level=1,
                    path=f"Page {idx + 1}",
                    page=idx + 1,
                )
            )
            full_texts.append(page_text.strip())

    return "\n\n".join(full_texts), sections


def _parse_with_pymupdf(raw_bytes: bytes) -> tuple[str, list[ParsedSection]]:
    import fitz
    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    sections: list[ParsedSection] = []
    full_texts: list[str] = []

    for idx, page in enumerate(doc):
        page_text = page.get_text() or ""
        if page_text.strip():
            sections.append(
                ParsedSection(
                    title=f"Page {idx + 1}",
                    content=page_text.strip(),
                    level=1,
                    path=f"Page {idx + 1}",
                    page=idx + 1,
                )
            )
            full_texts.append(page_text.strip())

    return "\n\n".join(full_texts), sections


def _parse_native_stream(raw_bytes: bytes) -> tuple[str, list[ParsedSection]]:
    """Native stream text extractor for basic text PDFs when no external library is installed."""
    text_parts: list[str] = []
    # Search for text within BT ... ET blocks
    bt_blocks = re.findall(rb"BT[\r\n\s]+(.*?)[\r\n\s]+ET", raw_bytes, re.DOTALL)
    for block in bt_blocks:
        # Match (string) Tj or [ (string) ... ] TJ
        strings = re.findall(rb"\((.*?)\)\s*Tj", block)
        if strings:
            decoded = [s.decode("latin1", errors="replace") for s in strings]
            text_parts.append(" ".join(decoded))
        else:
            array_strings = re.findall(rb"\[(.*?)\]\s*TJ", block)
            for arr in array_strings:
                subs = re.findall(rb"\((.*?)\)", arr)
                if subs:
                    decoded = [s.decode("latin1", errors="replace") for s in subs]
                    text_parts.append("".join(decoded))

    full_text = "\n".join(text_parts).strip()
    if not full_text:
        # Fallback to general printable ASCII string extraction
        words = re.findall(rb"[A-Za-z0-9_\-\.,;:!?'\"\(\)\[\]]{3,}", raw_bytes)
        full_text = " ".join([w.decode("ascii", errors="replace") for w in words[:2000]])

    sections = [ParsedSection(title="Extracted Content", content=full_text, level=1)] if full_text else []
    return full_text, sections


def parse_pdf(content: bytes | str, filename: str) -> ParsedDocument:
    raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
    title = Path(filename).stem
    backend = "native"

    try:
        full_text, sections = _parse_with_pymupdf(raw_bytes)
        backend = "pymupdf"
    except ImportError:
        try:
            full_text, sections = _parse_with_pypdf(raw_bytes)
            backend = "pypdf"
        except ImportError:
            full_text, sections = _parse_native_stream(raw_bytes)

    return ParsedDocument(
        title=title,
        sections=sections,
        full_text=full_text,
        metadata={"format": "pdf", "backend": backend},
    )
