"""Dispatcher for document parsing based on file format and extension."""

from __future__ import annotations

from pathlib import Path
from typing import Union

from .base import ParsedDocument, ParsedSection
from .docx import parse_docx
from .markdown import parse_markdown
from .pdf import parse_pdf


def parse_document(content: Union[bytes, str], filename: str) -> ParsedDocument:
    ext = Path(filename).suffix.lower()

    if ext in (".md", ".markdown"):
        return parse_markdown(content, filename)
    elif ext == ".docx":
        return parse_docx(content, filename)
    elif ext == ".pdf":
        return parse_pdf(content, filename)
    else:
        # Fallback for plain text or generic files
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
        title = Path(filename).stem
        sections = [ParsedSection(title="Content", content=text, level=1)] if text.strip() else []
        return ParsedDocument(title=title, sections=sections, full_text=text, metadata={"format": ext or "text"})
