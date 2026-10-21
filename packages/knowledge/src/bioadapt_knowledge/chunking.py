"""Section-aware chunking for scientific and laboratory documents (plan V2.0 section 6)."""

from __future__ import annotations

import hashlib
import re
from typing import Sequence

from .entities import DocumentChunk
from .parsers.base import ParsedDocument, ParsedSection

DEFAULT_CHUNK_SIZE = 600
DEFAULT_CHUNK_OVERLAP = 100


def _split_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> list[str]:
    """Splits a long paragraph or section into overlapping chunks on sentence/line boundaries."""
    if len(text) <= chunk_size:
        return [text]

    # Split by paragraphs or sentence boundaries
    parts = re.split(r"(\n\n+|(?<=[。！？\.\?!])\s+)", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for part in parts:
        if not part:
            continue
        if current_len + len(part) > chunk_size and current:
            joined = "".join(current).strip()
            if joined:
                chunks.append(joined)
            # Sliding overlap
            overlap_acc = []
            overlap_len = 0
            for prev_part in reversed(current):
                overlap_acc.insert(0, prev_part)
                overlap_len += len(prev_part)
                if overlap_len >= overlap:
                    break
            current = overlap_acc
            current_len = overlap_len

        current.append(part)
        current_len += len(part)

    if current:
        joined = "".join(current).strip()
        if joined and (not chunks or joined != chunks[-1]):
            chunks.append(joined)

    return chunks or [text]


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
        sections = [ParsedSection(title="General", content=parsed.full_text, level=1)]

    for section in sections:
        sec_path = section.full_path or section.title or "General"
        text = section.content.strip()
        if not text:
            continue

        raw_chunks = _split_text(text, chunk_size=chunk_size, overlap=overlap)
        for piece in raw_chunks:
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
                    metadata={"section_title": section.title, "level": section.level},
                )
            )
            chunk_idx += 1

    return chunks
