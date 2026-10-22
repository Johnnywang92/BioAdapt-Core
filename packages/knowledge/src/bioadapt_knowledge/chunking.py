"""Section-aware chunking for scientific and laboratory documents (plan V2.0 section 6)."""

from __future__ import annotations

import hashlib
import re
from typing import Optional, Sequence

from .entities import DocumentChunk
from .parsers.base import ParsedDocument, ParsedSection

DEFAULT_CHUNK_SIZE = 600
DEFAULT_CHUNK_OVERLAP = 100


def _flush(current: list[tuple[str, int, int]]) -> Optional[tuple[str, int, int]]:
    """Turn the parts gathered so far into one piece, with the span it actually occupies."""
    if not current:
        return None
    raw = "".join(part for part, _, _ in current)
    piece = raw.strip()
    if not piece:
        return None
    lead = len(raw) - len(raw.lstrip())
    tail = len(raw) - len(raw.rstrip())
    return (piece, current[0][1] + lead, current[-1][2] - tail)


def _split_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[tuple[str, int, int]]:
    """Splits a long paragraph or section into overlapping chunks on sentence/line boundaries.

    Each piece comes back with the span it holds in ``text``, because a citation a person can only read
    and not find is half a citation. The split keeps every separator, so the parts walk the text from left
    to right without gaps and the spans are the text's own, not an estimate.
    """
    if len(text) <= chunk_size:
        return [(text, 0, len(text))]

    # Split by paragraphs or sentence boundaries
    parts = re.split(r"(\n\n+|(?<=[。！？\.\?!])\s+)", text)
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for part in parts:
        spans.append((part, cursor, cursor + len(part)))
        cursor += len(part)

    chunks: list[tuple[str, int, int]] = []
    current: list[tuple[str, int, int]] = []
    current_len = 0

    for part in spans:
        if not part[0]:
            continue
        if current_len + len(part[0]) > chunk_size and current:
            flushed = _flush(current)
            if flushed:
                chunks.append(flushed)
            # Sliding overlap
            overlap_acc: list[tuple[str, int, int]] = []
            overlap_len = 0
            for prev_part in reversed(current):
                overlap_acc.insert(0, prev_part)
                overlap_len += len(prev_part[0])
                if overlap_len >= overlap:
                    break
            current = overlap_acc
            current_len = overlap_len

        current.append(part)
        current_len += len(part[0])

    flushed = _flush(current)
    if flushed and (not chunks or flushed[0] != chunks[-1][0]):
        chunks.append(flushed)

    return chunks or [(text, 0, len(text))]


def chunk_document(
    doc_id: str,
    parsed: ParsedDocument,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[DocumentChunk]:
    """Transforms a parsed document into structured DocumentChunks tagged with section paths."""
    chunks: list[DocumentChunk] = []
    chunk_idx = 0

    sections = parsed.sections
    if not sections and parsed.full_text:
        # The one section is the whole text, so its span is the whole text and nothing is lost.
        sections = [
            ParsedSection(
                title="General",
                content=parsed.full_text,
                level=1,
                char_start=0,
                char_end=len(parsed.full_text),
            )
        ]

    for section in sections:
        sec_path = section.full_path or section.title or "General"
        raw = section.content
        text = raw.strip()
        if not text:
            continue
        # The section's span counts from its own un-stripped text, so the strip is taken off before adding
        # it; a chunk that starts inside the section then lands where the document text really says it does.
        base = None
        if section.char_start is not None:
            base = section.char_start + (len(raw) - len(raw.lstrip()))

        for piece, start, end in _split_text(text, chunk_size=chunk_size, overlap=overlap):
            # Generate deterministic chunk id
            content_digest = hashlib.sha256(piece.encode("utf-8")).hexdigest()[:12]
            chunk_id = f"chk_{doc_id[:8]}_{chunk_idx:03d}_{content_digest}"
            # Approximate tokens: ~0.75 words/chars
            approx_tokens = max(1, len(piece) // 3)

            chunks.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    chunk_index=chunk_idx,
                    section_path=sec_path,
                    content=piece,
                    token_count=approx_tokens,
                    page=section.page,
                    char_start=None if base is None else base + start,
                    char_end=None if base is None else base + end,
                    metadata={"section_title": section.title, "level": section.level},
                )
            )
            chunk_idx += 1

    return chunks
