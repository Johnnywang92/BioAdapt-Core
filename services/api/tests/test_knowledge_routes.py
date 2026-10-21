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
