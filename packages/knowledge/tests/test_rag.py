"""Tests for KnowledgeAssistant (offline QA and bioinformatics suggestions)."""

import pytest
from bioadapt_schemas.enums import PrivacyTier

from bioadapt_knowledge.entities import DocumentType, KnowledgeDocument
from bioadapt_knowledge.index import KnowledgeIndex
from bioadapt_knowledge.rag import KnowledgeAssistant


@pytest.fixture
def populated_assistant():
    idx = KnowledgeIndex(":memory:")
    doc = KnowledgeDocument(
        doc_id="doc_immuno_101",
        project_id="proj_sc_tumor",
        filename="tumor_infiltrating_lymphocytes.md",
        doc_type=DocumentType.PAPER,
        title="Tumor-Infiltrating Regulatory T Cells in Lung Cancer",
        privacy_tier=PrivacyTier.RESTRICTED,
        license_class="internal_only",
        file_sha256="sha_abc_789",
    )
    content = """# Tumor-Infiltrating Regulatory T Cells in Lung Cancer

## Abstract
Regulatory T cells (Tregs) play a critical role in mediating immune tolerance in the tumor microenvironment.

## Cluster 4 Characterization
Cells in Cluster 4 exhibit high expression of FOXP3, IL2RA (CD25), and CTLA4.
Inhibition of TGF-beta signaling reverses the suppressive function of these cells.
"""
    idx.add_document(doc, content)
    assistant = KnowledgeAssistant(idx)
    yield assistant
    idx.close()


def test_rag_ask_offline(populated_assistant: KnowledgeAssistant):
    res = populated_assistant.ask("What markers characterize Treg cells in Cluster 4?")
    assert res.answer != ""
    assert len(res.citations) > 0
    assert "FOXP3" in res.citations[0].chunk.content
    assert res.confidence > 0.5
    assert len(res.grounded_facts) > 0


def test_rag_suggest_for_cluster(populated_assistant: KnowledgeAssistant):
    suggestion = populated_assistant.suggest_for_cluster(
        cluster_id="cluster_04",
        markers=["FOXP3", "IL2RA", "CTLA4", "CD4"],
        tissue="Lung Cancer",
    )
    assert suggestion.cluster_id == "cluster_04"
    assert "Regulatory T Cell (Treg)" in suggestion.suggested_cell_types
    assert suggestion.confidence >= 0.6
    assert len(suggestion.citations) > 0
    assert "FOXP3" in suggestion.rationale
