"""Tests for DeterministicTfidfEmbedder and cosine similarity."""

import math
from bioadapt_knowledge.vectorizer import DeterministicTfidfEmbedder, cosine_similarity


def test_embedder_dimension_and_determinism():
    embedder = DeterministicTfidfEmbedder(dimension=128)
    assert embedder.dimension == 128

    text = "CD4+ T cells express CD3D and IL7R in peripheral blood."
    v1 = embedder.embed_query(text)
    v2 = embedder.embed_query(text)
    assert len(v1) == 128
    assert v1 == v2

    # L2 norm should be approximately 1.0
    norm = math.sqrt(sum(x * x for x in v1))
    assert math.isclose(norm, 1.0, rel_tol=1e-5)


def test_embedder_similarity():
    embedder = DeterministicTfidfEmbedder(dimension=256)

    t_tcell = "CD4 positive helper T lymphocytes and regulatory T cells."
    t_tcell_similar = "Helper T cell activation and CD4 expression."
    t_bcell = "B cell maturation and immunoglobulin CD19 MS4A1 secretion."

    v_a = embedder.embed_query(t_tcell)
    v_b = embedder.embed_query(t_tcell_similar)
    v_c = embedder.embed_query(t_bcell)

    sim_ab = cosine_similarity(v_a, v_b)
    sim_ac = cosine_similarity(v_a, v_c)

    # Similar T-cell texts should have higher cosine similarity than unrelated B-cell text
    assert sim_ab > sim_ac
    assert sim_ab > 0.15
