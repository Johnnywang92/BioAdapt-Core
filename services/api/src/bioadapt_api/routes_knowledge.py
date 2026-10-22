"""Knowledge base and bioinformatics RAG endpoints (plan V2.0 section 6).

Adheres to:
- Section 6.1-6.9: local private knowledge base, strict provenance, privacy tier filtering.
- Pure offline hybrid search and LLM/offline synthesis.
- Biological suggestions for single-cell cluster markers.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Optional
from datetime import UTC, datetime

from fastapi import APIRouter, Header, Query, Request, status
from pydantic import BaseModel, Field

from bioadapt_knowledge import (
    BioSuggestion,
    ChunkReading,
    DocumentType,
    KnowledgeAssistant,
    KnowledgeDocument,
    KnowledgeIndex,
    RagAnswer,
)
from bioadapt_policies.access import Action
from bioadapt_schemas.enums import PrivacyTier

from .deps import SessionDep
from .errors import invalid, not_found

router = APIRouter(tags=["knowledge"])

# Global in-memory/disk fallback index for API state
_DEFAULT_INDEX = KnowledgeIndex()


def get_knowledge_index(session: SessionDep) -> KnowledgeIndex:
    if hasattr(session.state, "knowledge_index") and session.state.knowledge_index is not None:
        return session.state.knowledge_index
    return _DEFAULT_INDEX


class IngestDocumentRequest(BaseModel):
    title: str
    filename: str
    doc_type: DocumentType = DocumentType.OTHER
    content_b64: Optional[str] = None
    content_text: Optional[str] = None
    author: Optional[str] = None
    species: Optional[str] = None
    tissue: Optional[str] = None
    privacy_tier: PrivacyTier = PrivacyTier.RESTRICTED
    license_class: str = "internal_only"
    summary: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AskRequest(BaseModel):
    query: str
    top_k: int = 4
    doc_type: Optional[DocumentType] = None
    allowed_privacy_tiers: Optional[list[PrivacyTier]] = None


class SuggestRequest(BaseModel):
    cluster_id: str
    markers: list[str]
    tissue: str = ""
    top_k: int = 3


@router.post(
    "/projects/{project_id}/knowledge/documents",
    status_code=status.HTTP_201_CREATED,
    response_model=KnowledgeDocument,
)
def ingest_document(
    project_id: str,
    payload: IngestDocumentRequest,
    session: SessionDep,
) -> KnowledgeDocument:
    """Ingest a paper, lab notebook, or analysis report into the private knowledge base."""
    if not payload.content_b64 and not payload.content_text:
        raise invalid("content", "Either content_b64 or content_text must be provided")

    if payload.content_b64:
        content_bytes = base64.b64decode(payload.content_b64)
    else:
        content_bytes = (payload.content_text or "").encode("utf-8")

    file_sha256 = hashlib.sha256(content_bytes).hexdigest()
    doc_id = f"doc_{file_sha256[:12]}"

    session.enforce(
        Action.ASSET_WRITE,
        resource_id=doc_id,
        resource_type="knowledge_document",
        privacy_tier=payload.privacy_tier,
    )

    doc = KnowledgeDocument(
        doc_id=doc_id,
        project_id=project_id,
        filename=payload.filename,
        doc_type=payload.doc_type,
        title=payload.title,
        author=payload.author,
        species=payload.species,
        tissue=payload.tissue,
        privacy_tier=payload.privacy_tier,
        license_class=payload.license_class,
        file_sha256=file_sha256,
        chunk_count=0,
        created_at=session.clock(),
        summary=payload.summary,
        metadata=payload.metadata,
    )

    index = get_knowledge_index(session)
    index.add_document(doc, content_bytes)
    return index.get_document(doc_id) or doc


@router.post(
    "/projects/{project_id}/knowledge/upload-raw",
    status_code=status.HTTP_201_CREATED,
    response_model=KnowledgeDocument,
)
async def upload_document_raw(
    project_id: str,
    request: Request,
    session: SessionDep,
    filename: str = Query(..., description="Original filename with extension e.g. paper.pdf or lab.docx"),
    title: Optional[str] = Query(None),
    doc_type: DocumentType = Query(DocumentType.OTHER),
    privacy_tier: PrivacyTier = Query(PrivacyTier.RESTRICTED),
    license_class: str = Query("internal_only"),
    author: Optional[str] = Query(None),
    tissue: Optional[str] = Query(None),
) -> KnowledgeDocument:
    """Upload a raw binary stream directly without requiring python-multipart."""
    content_bytes = await request.body()
    if not content_bytes:
        raise invalid("body", "Request body cannot be empty")

    file_sha256 = hashlib.sha256(content_bytes).hexdigest()
    doc_id = f"doc_{file_sha256[:12]}"
    final_title = title or filename

    session.enforce(
        Action.ASSET_WRITE,
        resource_id=doc_id,
        resource_type="knowledge_document",
        privacy_tier=privacy_tier,
    )

    doc = KnowledgeDocument(
        doc_id=doc_id,
        project_id=project_id,
        filename=filename,
        doc_type=doc_type,
        title=final_title,
        author=author,
        tissue=tissue,
        privacy_tier=privacy_tier,
        license_class=license_class,
        file_sha256=file_sha256,
        chunk_count=0,
        created_at=session.clock(),
    )

    index = get_knowledge_index(session)
    index.add_document(doc, content_bytes)
    return index.get_document(doc_id) or doc


@router.get(
    "/projects/{project_id}/knowledge/documents",
    response_model=list[KnowledgeDocument],
)
def list_documents(
    project_id: str,
    session: SessionDep,
    doc_type: Optional[DocumentType] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[KnowledgeDocument]:
    """List documents stored in the private knowledge base for this project."""
    session.enforce(
        Action.ASSET_READ,
        resource_id=f"project/{project_id}/knowledge",
        resource_type="knowledge_document",
    )
    index = get_knowledge_index(session)
    return index.list_documents(project_id=project_id, doc_type=doc_type, limit=limit, offset=offset)


@router.get(
    "/projects/{project_id}/knowledge/chunks/{chunk_id}",
    response_model=ChunkReading,
)
def read_chunk(
    project_id: str,
    chunk_id: str,
    session: SessionDep,
    radius: int = Query(1, ge=0, le=3, description="stored pieces to show on each side of the cited one"),
) -> ChunkReading:
    """Where a cited passage sits, and what the index kept on either side of it.

    A citation nobody can open is a citation a reviewer has to take on trust, so this route returns the
    locator the parse recorded and nothing it did not: the offsets count characters in the text the parser
    produced from the file, the page is the page the backend reported, and a chunk indexed before locators
    existed simply carries none. The file itself is not re-read here.
    """
    session.enforce(
        Action.ASSET_READ,
        resource_id=f"project/{project_id}/knowledge_chunk/{chunk_id}",
        resource_type="knowledge_document",
    )
    index = get_knowledge_index(session)
    reading = index.read_chunk(chunk_id, project_id=project_id, radius=radius)
    if reading is None:
        raise not_found("knowledge_chunk", chunk_id)
    return reading


@router.delete(
    "/projects/{project_id}/knowledge/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_document(
    project_id: str,
    doc_id: str,
    session: SessionDep,
) -> None:
    """Delete a document and all related index entries."""
    session.enforce(
        Action.ASSET_WRITE,
        resource_id=doc_id,
        resource_type="knowledge_document",
    )
    index = get_knowledge_index(session)
    doc = index.get_document(doc_id)
    if not doc or doc.project_id != project_id:
        raise not_found("knowledge_document", doc_id)
    index.delete_document(doc_id)


@router.post(
    "/projects/{project_id}/knowledge/ask",
    response_model=RagAnswer,
)
def ask_knowledge(
    project_id: str,
    payload: AskRequest,
    session: SessionDep,
) -> RagAnswer:
    """Ask a question to the knowledge base using local RAG."""
    session.enforce(
        Action.ASSET_READ,
        resource_id=f"project/{project_id}/knowledge_query",
        resource_type="knowledge_document",
    )
    index = get_knowledge_index(session)
    assistant = KnowledgeAssistant(index)
    return assistant.ask(
        query=payload.query,
        project_id=project_id,
        top_k=payload.top_k,
        doc_type=payload.doc_type,
        allowed_privacy_tiers=payload.allowed_privacy_tiers,
    )


@router.post(
    "/projects/{project_id}/knowledge/suggest",
    response_model=BioSuggestion,
)
def suggest_for_cluster(
    project_id: str,
    payload: SuggestRequest,
    session: SessionDep,
) -> BioSuggestion:
    """Get cell type predictions and biological suggestions for a single-cell cluster."""
    session.enforce(
        Action.ASSET_READ,
        resource_id=f"project/{project_id}/knowledge_query",
        resource_type="knowledge_document",
    )
    index = get_knowledge_index(session)
    assistant = KnowledgeAssistant(index)
    return assistant.suggest_for_cluster(
        cluster_id=payload.cluster_id,
        markers=payload.markers,
        tissue=payload.tissue,
        project_id=project_id,
        top_k=payload.top_k,
    )
