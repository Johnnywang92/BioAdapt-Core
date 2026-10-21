"""BioAdapt Knowledge - Offline Private Knowledge Base & RAG for Bioinformatics.

Features:
- Pure offline parsing for Markdown, Word (.docx), and PDF documents.
- Section-aware chunking for scientific and laboratory reports.
- SQLite + FTS5 BM25 + Vector hybrid retrieval.
- 100% offline RAG answering and single-cell cluster biological suggestion.
- Strict privacy tier enforcement (STRICT_LOCAL, RESTRICTED).
"""

from bioadapt_knowledge.chunking import chunk_document
from bioadapt_knowledge.entities import (
    BioSuggestion,
    DocumentChunk,
    DocumentType,
    KnowledgeDocument,
    RagAnswer,
    RetrievalHit,
)
from bioadapt_knowledge.index import KnowledgeIndex
from bioadapt_knowledge.parsers.registry import parse_document
from bioadapt_knowledge.rag import KnowledgeAssistant
from bioadapt_knowledge.vectorizer import DeterministicTfidfEmbedder, Embedder, cosine_similarity

__all__ = [
    "BioSuggestion",
    "DeterministicTfidfEmbedder",
    "DocumentChunk",
    "DocumentType",
    "Embedder",
    "KnowledgeAssistant",
    "KnowledgeDocument",
    "KnowledgeIndex",
    "RagAnswer",
    "RetrievalHit",
    "chunk_document",
    "cosine_similarity",
    "parse_document",
]
