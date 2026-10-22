"""SQLite + FTS5 BM25 + Vector hybrid knowledge index.

Features:
- Pure SQLite storage: zero external vector database daemon required.
- SQLite FTS5 for exact keyword and biological nomenclature matching (gene names, cell types).
- In-process vector indexing and cosine ranking for semantic retrieval.
- Hybrid search via Reciprocal Rank Fusion (RRF) combining keyword and dense scores.
- Document lifecycle (ingest, update, delete, list, count).
- Full compliance with privacy tier filtering (STRICT_LOCAL, RESTRICTED, etc.).
"""

from __future__ import annotations

import json
import sqlite3
import struct
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Sequence

from bioadapt_schemas.enums import PrivacyTier

from .chunking import chunk_document
from .entities import ChunkReading, DocumentChunk, DocumentType, KnowledgeDocument, RetrievalHit
from .parsers.registry import parse_document
from .vectorizer import DeterministicTfidfEmbedder, Embedder, cosine_similarity

SearchMode = Literal["hybrid", "keyword", "vector"]


class KnowledgeIndex:
    """Offline hybrid index for scientific papers, lab notebooks, and analysis reports."""

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        default_embedder: Embedder | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._embedder = default_embedder or DeterministicTfidfEmbedder()
        self._lock = threading.RLock()
        # For :memory: to be shared across threads safely in-process:
        sqlite_uri = True if "cache=shared" in self._db_path or "file:" in self._db_path else False
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False, uri=sqlite_uri)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> KnowledgeIndex:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    doc_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    doc_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    author TEXT,
                    species TEXT,
                    tissue TEXT,
                    privacy_tier TEXT NOT NULL,
                    license_class TEXT NOT NULL,
                    file_sha256 TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    summary TEXT,
                    metadata_json TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    section_path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    token_count INTEGER NOT NULL,
                    page INTEGER,
                    char_start INTEGER,
                    char_end INTEGER,
                    vector_blob BLOB,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY (doc_id) REFERENCES knowledge_documents(doc_id) ON DELETE CASCADE
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chunks_doc_id
                ON knowledge_chunks(doc_id);
                """
            )
            #: An index built before chunks carried a locator keeps its rows and gains empty columns;
            #: re-ingesting a document fills them. Until then those chunks read as unlocatable, which is
            #: what the screen is told — it never shows a position the parse did not record.
            columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(knowledge_chunks)")}
            for column in ("page", "char_start", "char_end"):
                if column not in columns:
                    self._conn.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN {column} INTEGER;")
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    section_path,
                    content,
                    tokenize='unicode61'
                );
                """
            )

    @staticmethod
    def _pack_vector(vec: Sequence[float]) -> bytes:
        return struct.pack(f"{len(vec)}f", *vec)

    @staticmethod
    def _unpack_vector(blob: bytes) -> list[float]:
        n = len(blob) // 4
        return list(struct.unpack(f"{n}f", blob))

    def add_document(
        self,
        doc: KnowledgeDocument,
        content: bytes | str,
        embedder: Embedder | None = None,
    ) -> int:
        """Parse content, generate chunks, compute vectors, and store to index."""
        active_embedder = embedder or self._embedder
        parsed = parse_document(content, doc.filename)
        chunks = chunk_document(doc.doc_id, parsed)

        # Compute embeddings
        if active_embedder and chunks:
            texts = [f"{c.section_path}\n{c.content}" for c in chunks]
            vectors = active_embedder.embed_documents(texts)
            chunks = [
                c.model_copy(update={"vector": vec})
                for c, vec in zip(chunks, vectors)
            ]

        # Update chunk count
        chunk_count = len(chunks)

        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO knowledge_documents (
                    doc_id, project_id, filename, doc_type, title,
                    author, species, tissue, privacy_tier, license_class,
                    file_sha256, chunk_count, created_at, summary, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    filename=excluded.filename,
                    doc_type=excluded.doc_type,
                    title=excluded.title,
                    author=excluded.author,
                    species=excluded.species,
                    tissue=excluded.tissue,
                    privacy_tier=excluded.privacy_tier,
                    license_class=excluded.license_class,
                    file_sha256=excluded.file_sha256,
                    chunk_count=excluded.chunk_count,
                    created_at=excluded.created_at,
                    summary=excluded.summary,
                    metadata_json=excluded.metadata_json;
                """,
                (
                    doc.doc_id,
                    doc.project_id,
                    doc.filename,
                    doc.doc_type.value,
                    doc.title,
                    doc.author,
                    doc.species,
                    doc.tissue,
                    doc.privacy_tier.value,
                    doc.license_class,
                    doc.file_sha256,
                    chunk_count,
                    doc.created_at.isoformat(),
                    doc.summary,
                    json.dumps(doc.metadata, ensure_ascii=False),
                ),
            )

            # Clear existing chunks for this doc in both tables
            self._conn.execute("DELETE FROM knowledge_chunks WHERE doc_id = ?;", (doc.doc_id,))
            self._conn.execute(
                "DELETE FROM knowledge_chunks_fts WHERE chunk_id IN (SELECT chunk_id FROM knowledge_chunks WHERE doc_id = ?);",
                (doc.doc_id,),
            )

            for chunk in chunks:
                vec_blob = self._pack_vector(chunk.vector) if chunk.vector else None
                self._conn.execute(
                    """
                    INSERT INTO knowledge_chunks (
                        chunk_id, doc_id, chunk_index, section_path,
                        content, token_count, page, char_start, char_end,
                        vector_blob, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.chunk_index,
                        chunk.section_path,
                        chunk.content,
                        chunk.token_count,
                        chunk.page,
                        chunk.char_start,
                        chunk.char_end,
                        vec_blob,
                        json.dumps(chunk.metadata, ensure_ascii=False),
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO knowledge_chunks_fts (chunk_id, section_path, content)
                    VALUES (?, ?, ?);
                    """,
                    (chunk.chunk_id, chunk.section_path, chunk.content),
                )

        return chunk_count

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document and all related chunks and index entries."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                DELETE FROM knowledge_chunks_fts
                WHERE chunk_id IN (SELECT chunk_id FROM knowledge_chunks WHERE doc_id = ?);
                """,
                (doc_id,),
            )
            cur = self._conn.execute("DELETE FROM knowledge_documents WHERE doc_id = ?;", (doc_id,))
            return cur.rowcount > 0

    def get_document(self, doc_id: str) -> KnowledgeDocument | None:
        """Retrieve document metadata by doc_id."""
        with self._lock:
            cur = self._conn.execute("SELECT * FROM knowledge_documents WHERE doc_id = ?;", (doc_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_document(row)

    def read_chunk(
        self,
        chunk_id: str,
        *,
        project_id: str | None = None,
        radius: int = 1,
    ) -> ChunkReading | None:
        """One stored chunk, the document it came from, and the pieces the same parse put either side.

        The index keeps chunks rather than the file, so the widest honest context is the neighbouring
        pieces. ``project_id`` is not decoration: chunks are addressed by id, and an id belonging to
        another project's index has to read as absent rather than as somebody else's passage.
        """
        sql = """
            SELECT
                c.chunk_id, c.doc_id, c.chunk_index, c.section_path,
                c.content, c.token_count, c.page, c.char_start, c.char_end,
                c.metadata_json as chunk_meta,
                d.project_id, d.filename, d.doc_type, d.title, d.author,
                d.species, d.tissue, d.privacy_tier, d.license_class,
                d.file_sha256, d.chunk_count, d.created_at, d.summary,
                d.metadata_json as doc_meta
            FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.doc_id = c.doc_id
            WHERE c.chunk_id = ?
        """
        params: list[Any] = [chunk_id]
        if project_id:
            sql += " AND d.project_id = ?"
            params.append(project_id)
        sql += ";"

        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            if row is None:
                return None
            #: The join is on the id the two tables share, so the chunk's doc_id is the document's.
            chunk = self._row_to_chunk(row)
            document = self._row_to_document(row)
            before = self._neighbours(chunk, operator="<", radius=radius)
            after = self._neighbours(chunk, operator=">", radius=radius)

        return ChunkReading(chunk=chunk, document=document, before=before, after=after)

    def _neighbours(self, chunk: DocumentChunk, *, operator: str, radius: int) -> list[DocumentChunk]:
        """The stored pieces just before or just after one chunk, in reading order."""
        sql = f"""
            SELECT
                chunk_id, doc_id, chunk_index, section_path, content, token_count,
                page, char_start, char_end, metadata_json as chunk_meta
            FROM knowledge_chunks
            WHERE doc_id = ? AND chunk_index {operator} ?
            ORDER BY chunk_index {"DESC" if operator == "<" else "ASC"} LIMIT ?;
        """
        rows = self._conn.execute(sql, (chunk.doc_id, chunk.chunk_index, radius)).fetchall()
        pieces = [self._row_to_chunk(row) for row in rows]
        return pieces[::-1] if operator == "<" else pieces

    def list_documents(
        self,
        project_id: str | None = None,
        doc_type: DocumentType | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KnowledgeDocument]:
        """List documents, optionally filtered by project or doc_type."""
        query = "SELECT * FROM knowledge_documents WHERE 1=1"
        params: list[Any] = []
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if doc_type:
            query += " AND doc_type = ?"
            params.append(doc_type.value)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self._lock:
            cur = self._conn.execute(query, params)
            return [self._row_to_document(r) for r in cur.fetchall()]

    def count_documents(self, project_id: str | None = None) -> int:
        with self._lock:
            if project_id:
                cur = self._conn.execute("SELECT COUNT(*) FROM knowledge_documents WHERE project_id = ?;", (project_id,))
            else:
                cur = self._conn.execute("SELECT COUNT(*) FROM knowledge_documents;")
            return int(cur.fetchone()[0])

    def count_chunks(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM knowledge_chunks;")
            return int(cur.fetchone()[0])

    def search(
        self,
        query: str,
        project_id: str | None = None,
        top_k: int = 5,
        mode: SearchMode = "hybrid",
        doc_type: DocumentType | None = None,
        allowed_privacy_tiers: Sequence[PrivacyTier] | None = None,
    ) -> list[RetrievalHit]:
        """Search knowledge base with keyword, vector, or reciprocal rank fusion."""
        if not query.strip():
            return []

        doc_type_val = doc_type.value if doc_type else None
        tier_vals = [t.value for t in allowed_privacy_tiers] if allowed_privacy_tiers else None

        if mode == "keyword":
            return self._search_keyword(query, project_id, top_k, doc_type_val, tier_vals)
        if mode == "vector":
            return self._search_vector(query, project_id, top_k, doc_type_val, tier_vals)
        return self._search_hybrid(query, project_id, top_k, doc_type_val, tier_vals)

    def _sanitize_fts_query(self, query: str) -> str:
        """Sanitize query string for SQLite FTS5."""
        terms = [w for w in query.replace('"', " ").replace("'", " ").split() if w]
        if not terms:
            return ""
        escaped_terms = [f'"{t}"' for t in terms]
        return " OR ".join(escaped_terms)

    def _search_keyword(
        self,
        query: str,
        project_id: str | None,
        top_k: int,
        doc_type: str | None,
        privacy_tiers: Sequence[str] | None,
    ) -> list[RetrievalHit]:
        fts_query = self._sanitize_fts_query(query)
        if not fts_query:
            return []

        sql = """
            SELECT
                c.chunk_id, c.doc_id, c.chunk_index, c.section_path,
                c.content, c.token_count, c.page, c.char_start, c.char_end,
                c.metadata_json as chunk_meta,
                d.project_id, d.filename, d.doc_type, d.title, d.author,
                d.species, d.tissue, d.privacy_tier, d.license_class,
                d.file_sha256, d.chunk_count, d.created_at, d.summary,
                d.metadata_json as doc_meta,
                bm25(knowledge_chunks_fts) as rank_score
            FROM knowledge_chunks_fts
            JOIN knowledge_chunks c ON c.chunk_id = knowledge_chunks_fts.chunk_id
            JOIN knowledge_documents d ON d.doc_id = c.doc_id
            WHERE knowledge_chunks_fts MATCH ?
        """
        params: list[Any] = [fts_query]

        if project_id:
            sql += " AND d.project_id = ?"
            params.append(project_id)
        if doc_type:
            sql += " AND d.doc_type = ?"
            params.append(doc_type)
        if privacy_tiers:
            placeholders = ",".join("?" for _ in privacy_tiers)
            sql += f" AND d.privacy_tier IN ({placeholders})"
            params.extend(privacy_tiers)

        sql += " ORDER BY rank_score ASC LIMIT ?;"
        params.append(top_k)

        try:
            with self._lock:
                cur = self._conn.execute(sql, params)
                rows = cur.fetchall()
        except sqlite3.OperationalError:
            return []

        hits: list[RetrievalHit] = []
        for rank, row in enumerate(rows, start=1):
            bm25_raw = float(row["rank_score"])
            score = 1.0 / (1.0 + max(0.0, -bm25_raw))
            chunk = self._row_to_chunk(row)
            doc = self._row_to_document(row)
            hits.append(
                RetrievalHit(
                    chunk=chunk,
                    score=score,
                    sparse_rank=rank,
                    source_doc=doc,
                )
            )
        return hits

    def _search_vector(
        self,
        query: str,
        project_id: str | None,
        top_k: int,
        doc_type: str | None,
        privacy_tiers: Sequence[str] | None,
    ) -> list[RetrievalHit]:
        if not self._embedder:
            return []
        q_vec = self._embedder.embed_query(query)

        sql = """
            SELECT
                c.chunk_id, c.doc_id, c.chunk_index, c.section_path,
                c.content, c.token_count, c.page, c.char_start, c.char_end,
                c.vector_blob, c.metadata_json as chunk_meta,
                d.project_id, d.filename, d.doc_type, d.title, d.author,
                d.species, d.tissue, d.privacy_tier, d.license_class,
                d.file_sha256, d.chunk_count, d.created_at, d.summary,
                d.metadata_json as doc_meta
            FROM knowledge_chunks c
            JOIN knowledge_documents d ON d.doc_id = c.doc_id
            WHERE c.vector_blob IS NOT NULL
        """
        params: list[Any] = []
        if project_id:
            sql += " AND d.project_id = ?"
            params.append(project_id)
        if doc_type:
            sql += " AND d.doc_type = ?"
            params.append(doc_type)
        if privacy_tiers:
            placeholders = ",".join("?" for _ in privacy_tiers)
            sql += f" AND d.privacy_tier IN ({placeholders})"
            params.extend(privacy_tiers)

        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()

        scored_hits: list[tuple[float, Any]] = []
        for row in rows:
            blob = row["vector_blob"]
            chunk_vec = self._unpack_vector(blob)
            sim = cosine_similarity(q_vec, chunk_vec)
            scored_hits.append((sim, row))

        scored_hits.sort(key=lambda x: x[0], reverse=True)
        top_rows = scored_hits[:top_k]

        hits: list[RetrievalHit] = []
        for rank, (sim, row) in enumerate(top_rows, start=1):
            chunk = self._row_to_chunk(row)
            doc = self._row_to_document(row)
            hits.append(
                RetrievalHit(
                    chunk=chunk,
                    score=sim,
                    dense_rank=rank,
                    source_doc=doc,
                )
            )
        return hits

    def _search_hybrid(
        self,
        query: str,
        project_id: str | None,
        top_k: int,
        doc_type: str | None,
        privacy_tiers: Sequence[str] | None,
        rrf_k: int = 60,
    ) -> list[RetrievalHit]:
        """Reciprocal Rank Fusion (RRF) between keyword (BM25) and vector cosine similarity."""
        candidate_k = max(top_k * 3, 20)
        kw_hits = self._search_keyword(query, project_id, candidate_k, doc_type, privacy_tiers)
        vec_hits = self._search_vector(query, project_id, candidate_k, doc_type, privacy_tiers)

        if not kw_hits:
            return vec_hits[:top_k]
        if not vec_hits:
            return kw_hits[:top_k]

        rrf_scores: dict[str, float] = {}
        hit_map: dict[str, RetrievalHit] = {}

        for hit in kw_hits:
            cid = hit.chunk.chunk_id
            rank = hit.sparse_rank or 1
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (rrf_k + rank))
            hit_map[cid] = hit

        for hit in vec_hits:
            cid = hit.chunk.chunk_id
            rank = hit.dense_rank or 1
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (rrf_k + rank))
            if cid not in hit_map:
                hit_map[cid] = hit
            else:
                # Merge dense rank into existing entry
                existing = hit_map[cid]
                hit_map[cid] = RetrievalHit(
                    chunk=existing.chunk,
                    score=existing.score,
                    dense_rank=hit.dense_rank,
                    sparse_rank=existing.sparse_rank,
                    source_doc=existing.source_doc,
                )

        sorted_cids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        results: list[RetrievalHit] = []
        for cid, score in sorted_cids:
            hit = hit_map[cid]
            results.append(
                RetrievalHit(
                    chunk=hit.chunk,
                    score=round(score * 100.0, 4),
                    dense_rank=hit.dense_rank,
                    sparse_rank=hit.sparse_rank,
                    source_doc=hit.source_doc,
                )
            )
        return results

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> KnowledgeDocument:
        meta_json = row["doc_meta"] if "doc_meta" in row.keys() else row["metadata_json"]
        return KnowledgeDocument(
            doc_id=row["doc_id"],
            project_id=row["project_id"],
            filename=row["filename"],
            doc_type=DocumentType(row["doc_type"]),
            title=row["title"],
            author=row["author"],
            species=row["species"],
            tissue=row["tissue"],
            privacy_tier=PrivacyTier(row["privacy_tier"]),
            license_class=row["license_class"],
            file_sha256=row["file_sha256"],
            chunk_count=row["chunk_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            summary=row["summary"],
            metadata=json.loads(meta_json) if meta_json else {},
        )

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> DocumentChunk:
        meta_json = row["chunk_meta"] if "chunk_meta" in row.keys() else row["metadata_json"]
        keys = row.keys()
        return DocumentChunk(
            chunk_id=row["chunk_id"],
            doc_id=row["doc_id"],
            chunk_index=row["chunk_index"],
            section_path=row["section_path"],
            content=row["content"],
            token_count=row["token_count"],
            page=row["page"] if "page" in keys else None,
            char_start=row["char_start"] if "char_start" in keys else None,
            char_end=row["char_end"] if "char_end" in keys else None,
            metadata=json.loads(meta_json) if meta_json else {},
        )
