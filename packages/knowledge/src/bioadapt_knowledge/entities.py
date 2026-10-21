"""Data models for local private knowledge base and RAG (plan V2.0 section 6).

Adheres to:
- Section 6.5: claims require provenance and licensing (license_class).
- Section 6.7: license_class -> usage_scope -> privacy_tier filtering.
- Section 6.8: isolation attestation and benchmark evaluation leak prevention.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Optional

from pydantic import Field

from bioadapt_schemas.entities import ImmutableContract
from bioadapt_schemas.enums import PrivacyTier


class DocumentType(StrEnum):
    PAPER = "paper"
    LAB_NOTE = "lab_note"
    ANALYSIS_LOG = "analysis_log"
    PROTOCOL = "protocol"
    OTHER = "other"


class KnowledgeDocument(ImmutableContract):
    doc_id: str
    project_id: str
    filename: str
    doc_type: DocumentType = DocumentType.OTHER
    title: str
    author: Optional[str] = None
    species: Optional[str] = None
    tissue: Optional[str] = None
    privacy_tier: PrivacyTier = PrivacyTier.RESTRICTED
    license_class: str = "internal_only"
    file_sha256: str
    chunk_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentChunk(ImmutableContract):
    chunk_id: str
    doc_id: str
    chunk_index: int
    section_path: str = "General"
    content: str
    token_count: int = 0
    vector: Optional[list[float]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalHit(ImmutableContract):
    chunk: DocumentChunk
    score: float
    dense_rank: Optional[int] = None
    sparse_rank: Optional[int] = None
    source_doc: Optional[KnowledgeDocument] = None

    @property
    def citation_label(self) -> str:
        title = self.source_doc.title if self.source_doc else self.chunk.doc_id
        section = f" § {self.chunk.section_path}" if self.chunk.section_path else ""
        return f"[{title}{section}]"


class RagAnswer(ImmutableContract):
    query: str
    answer: str
    citations: list[RetrievalHit] = Field(default_factory=list)
    confidence: float = 0.8
    grounded_facts: list[str] = Field(default_factory=list)


class BioSuggestion(ImmutableContract):
    cluster_id: str
    markers: list[str] = Field(default_factory=list)
    suggested_cell_types: list[str] = Field(default_factory=list)
    rationale: str
    citations: list[RetrievalHit] = Field(default_factory=list)
    confidence: float = 0.75
