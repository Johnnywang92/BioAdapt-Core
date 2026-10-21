"""Tests for KnowledgeIndex: hybrid search, CRUD, and privacy filtering."""

import pytest
from bioadapt_schemas.enums import PrivacyTier

from bioadapt_knowledge.entities import DocumentType, KnowledgeDocument
from bioadapt_knowledge.index import KnowledgeIndex


@pytest.fixture
def index():
    idx = KnowledgeIndex(":memory:")
    yield idx
    idx.close()


def test_index_crud_and_hybrid_search(index: KnowledgeIndex):
    doc1 = KnowledgeDocument(
        doc_id="doc_tcell_001",
        project_id="proj_immunology",
        filename="nature_tcell_2025.md",
        doc_type=DocumentType.PAPER,
        title="Dissecting Tumor-Infiltrating CD4+ and CD8+ T Cells",
        privacy_tier=PrivacyTier.RESTRICTED,
        license_class="internal_only",
        file_sha256="dummy_sha_123",
    )
    content1 = """# Dissecting Tumor-Infiltrating CD4+ and CD8+ T Cells

## Abstract
Single-cell RNA sequencing reveals distinct activation states of CD8 cytotoxic T lymphocytes.

## Key Markers
CD8A, GZMA, GZMB, and PRF1 mark active cytotoxic T cells.
FOXP3 and IL2RA identify immunosuppressive Treg cells.
"""
    chunk_count1 = index.add_document(doc1, content1)
    assert chunk_count1 >= 2
    assert index.count_documents() == 1
    assert index.count_chunks() >= 2

    # Add second document
    doc2 = KnowledgeDocument(
        doc_id="doc_bcell_002",
        project_id="proj_immunology",
        filename="b_cell_lab_notebook.md",
        doc_type=DocumentType.LAB_NOTE,
        title="PBMC B Cell Differentiation Experiment",
        privacy_tier=PrivacyTier.RESTRICTED,
        license_class="internal_only",
        file_sha256="dummy_sha_456",
    )
    content2 = """# PBMC B Cell Differentiation Experiment

## Observation
Flow cytometry confirms high expression of CD19 and MS4A1 (CD20) on mature B cells.
Plasmablasts downregulate CD20 and express SDC1 (CD138).
"""
    index.add_document(doc2, content2)
    assert index.count_documents() == 2

    # Test keyword search
    kw_hits = index.search("FOXP3 cytotoxic", mode="keyword")
    assert len(kw_hits) > 0
    assert any("CD8" in h.chunk.content or "FOXP3" in h.chunk.content for h in kw_hits)

    # Test vector search
    vec_hits = index.search("plasmablasts antibody secretion", mode="vector")
    assert len(vec_hits) > 0

    # Test hybrid search
    hybrid_hits = index.search("CD19 MS4A1 B cell", mode="hybrid")
    assert len(hybrid_hits) > 0
    assert hybrid_hits[0].source_doc is not None
    assert hybrid_hits[0].source_doc.doc_id == "doc_bcell_002"

    # Test privacy tier filter
    # STRICT_LOCAL query should not match RESTRICTED documents if not allowed
    hits_strict = index.search("CD19", allowed_privacy_tiers=[PrivacyTier.PUBLIC])
    assert len(hits_strict) == 0

    hits_allowed = index.search("CD19", allowed_privacy_tiers=[PrivacyTier.RESTRICTED])
    assert len(hits_allowed) > 0

    # Test delete
    deleted = index.delete_document("doc_tcell_001")
    assert deleted is True
    assert index.count_documents() == 1
    assert index.get_document("doc_tcell_001") is None
