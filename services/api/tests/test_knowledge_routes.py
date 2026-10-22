"""Knowledge base & RAG routes testing (plan V2.0 section 6)."""

from bioadapt_policies.access import Role
from bioadapt_schemas.enums import PrivacyTier
from conftest import ANALYST, PI, PROJECT_ID

BASE = f"/v1/projects/{PROJECT_ID}/knowledge"


def test_ingest_and_query_knowledge_document(client, auth):
    headers = auth(ANALYST, [Role.ANALYST])

    # 1. Ingest Markdown document
    payload = {
        "title": "Single-cell atlas of human lung adenocarcinoma",
        "filename": "luad_treg_paper.md",
        "doc_type": "paper",
        "content_text": """# Single-cell atlas of human lung adenocarcinoma

## Abstract
We profiled 50,000 cells from human lung adenocarcinoma tumors.

## Marker Expressions
FOXP3, IL2RA (CD25), and CTLA4 define immunosuppressive Treg cells in Cluster 5.
CD8A and GZMB define cytotoxic T cells in Cluster 2.
""",
        "author": "BioAdapt Lab",
        "tissue": "Lung",
        "privacy_tier": "restricted",
        "license_class": "internal_only",
    }
    resp = client.post(f"{BASE}/documents", json=payload, headers=headers)
    assert resp.status_code == 201
    doc = resp.json()
    doc_id = doc["doc_id"]
    assert doc["title"] == "Single-cell atlas of human lung adenocarcinoma"
    assert doc["chunk_count"] >= 2

    # 2. List documents
    resp_list = client.get(f"{BASE}/documents", headers=headers)
    assert resp_list.status_code == 200
    docs = resp_list.json()
    assert any(d["doc_id"] == doc_id for d in docs)

    # 3. RAG Ask
    ask_payload = {
        "query": "Which markers define Treg cells in Cluster 5?",
        "top_k": 3,
    }
    resp_ask = client.post(f"{BASE}/ask", json=ask_payload, headers=headers)
    assert resp_ask.status_code == 200
    answer = resp_ask.json()
    assert len(answer["citations"]) > 0
    assert "FOXP3" in answer["answer"]
    assert answer["confidence"] > 0.5

    # 4. Suggest for cluster
    suggest_payload = {
        "cluster_id": "cluster_5",
        "markers": ["FOXP3", "IL2RA", "CTLA4", "CD4"],
        "tissue": "Lung adenocarcinoma",
    }
    resp_suggest = client.post(f"{BASE}/suggest", json=suggest_payload, headers=headers)
    assert resp_suggest.status_code == 200
    suggestion = resp_suggest.json()
    assert suggestion["cluster_id"] == "cluster_5"
    assert any("Treg" in ct for ct in suggestion["suggested_cell_types"])
    assert len(suggestion["citations"]) > 0

    # 5. Raw binary upload
    raw_md_content = b"# Lab Log 2026-10-21\nValidation of Cluster 3 Monocytes with CD14 and CD68."
    resp_raw = client.post(
        f"{BASE}/upload-raw?filename=monocyte_log.md&title=Cluster+3+Monocyte+Log",
        content=raw_md_content,
        headers={**headers, "Content-Type": "text/markdown"},
    )
    assert resp_raw.status_code == 201
    raw_doc = resp_raw.json()
    assert raw_doc["title"] == "Cluster 3 Monocyte Log"
    assert raw_doc["chunk_count"] >= 1

    # 6. Delete document
    resp_del = client.delete(f"{BASE}/documents/{doc_id}", headers=headers)
    assert resp_del.status_code == 204

    # Verify deleted
    resp_list_after = client.get(f"{BASE}/documents", headers=headers)
    assert not any(d["doc_id"] == doc_id for d in resp_list_after.json())


ATLAS = """# Cytotoxic T cells in the tumour

## Abstract
{}

## Markers
CD8A and GZMB define cytotoxic T cells. FOXP3 and IL2RA define Treg cells.
""".format(
    " ".join(
        f" Sentence {index} counts CD8 T cells among the tumour-infiltrating lymphocytes."
        for index in range(120)
    )
)


def _ingest(client, auth) -> str:
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    response = client.post(
        f"{BASE}/documents",
        json={
            "title": "Cytotoxic T cells in the tumour",
            "filename": "cd8_atlas.md",
            "doc_type": "paper",
            "content_text": ATLAS,
            "privacy_tier": "restricted",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["doc_id"]


def test_a_citation_opens_at_the_place_it_was_cut_from(client, auth):
    """A reviewer checks a citation by finding it, so the route has to give the position the parse took."""
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    doc_id = _ingest(client, auth)

    answer = client.post(
        f"{BASE}/ask", json={"query": "Which markers define cytotoxic T cells?", "top_k": 3}, headers=headers
    ).json()
    assert answer["citations"], "the fixture has to be retrievable, or nothing is being tested"
    cited = answer["citations"][0]["chunk"]
    assert cited["char_start"] is not None
    assert cited["char_end"] > cited["char_start"]

    reading = client.get(f"{BASE}/chunks/{cited['chunk_id']}", headers=headers)
    assert reading.status_code == 200, reading.text
    body = reading.json()
    assert body["chunk"]["content"] == cited["content"]
    assert body["chunk"]["char_start"] == cited["char_start"]
    assert body["document"]["doc_id"] == doc_id
    assert body["document"]["title"] == "Cytotoxic T cells in the tumour"
    assert [chunk["chunk_index"] for chunk in body["before"]] == [body["chunk"]["chunk_index"] - 1]
    # The file is not re-read here: what comes back is what the index stored, so the context cannot drift
    # from the citation the answer was built from.
    assert all(chunk["doc_id"] == doc_id for chunk in body["before"] + body["after"])


def test_a_chunk_nobody_stored_is_absent_rather_than_empty(client, auth):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    _ingest(client, auth)
    response = client.get(f"{BASE}/chunks/chk_missing_000", headers=headers)
    assert response.status_code == 404
    assert "chk_missing_000" in response.json()["message"]


def test_the_context_window_is_a_number_the_caller_asks_for_and_the_route_caps(client, auth):
    """A reader wanting more surrounding text gets it; a wild radius is refused, not served."""
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    _ingest(client, auth)
    answer = client.post(f"{BASE}/ask", json={"query": "FOXP3 Treg markers", "top_k": 3}, headers=headers).json()
    chunk_id = answer["citations"][0]["chunk"]["chunk_id"]

    narrow = client.get(f"{BASE}/chunks/{chunk_id}?radius=0", headers=headers).json()
    assert narrow["before"] == [] and narrow["after"] == []

    wide = client.get(f"{BASE}/chunks/{chunk_id}?radius=3", headers=headers).json()
    assert len(wide["before"]) <= 3 and len(wide["after"]) <= 3

    refused = client.get(f"{BASE}/chunks/{chunk_id}?radius=9", headers=headers)
    assert refused.status_code == 400
    assert refused.json()["field"] == "radius"
