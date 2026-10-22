"""Where a cited passage sits in the document it was cut from (plan V2.0 sections 6.2, 6.5).

Section 6.5 gives provenance to the numbers on a card; the same argument applies to a retrieved sentence.
A citation a person cannot find is a citation they have to take on trust, so a chunk carries the page and
the character span the parse actually produced - and carries nothing when the parse could not say.
"""

import sqlite3

from bioadapt_schemas.enums import PrivacyTier

from bioadapt_knowledge.chunking import chunk_document
from bioadapt_knowledge.entities import DocumentChunk, DocumentType, KnowledgeDocument
from bioadapt_knowledge.index import KnowledgeIndex
from bioadapt_knowledge.parsers.base import ParsedDocument, ParsedSection
from bioadapt_knowledge.parsers.registry import parse_document

ABSTRACT = " ".join(
    f" Sentence {index} counts CD8 T cells among the tumour-infiltrating lymphocytes." for index in range(120)
)
MARKERS = "FOXP3 and IL2RA define Treg cells."
TEXT = f"# Atlas\n\n## Abstract\n{ABSTRACT}\n\n## Markers\n{MARKERS}\n"


def _doc(doc_id: str = "doc_locator_001", project_id: str = "proj_locator") -> KnowledgeDocument:
    return KnowledgeDocument(
        doc_id=doc_id,
        project_id=project_id,
        filename="atlas.md",
        doc_type=DocumentType.PAPER,
        title="Atlas",
        privacy_tier=PrivacyTier.RESTRICTED,
        license_class="internal_only",
        file_sha256="sha_locator",
    )


def _index() -> KnowledgeIndex:
    index = KnowledgeIndex(":memory:")
    index.add_document(_doc(), TEXT)
    return index


def _stored_chunks(index: KnowledgeIndex) -> list[DocumentChunk]:
    """Every chunk the index holds, in reading order, reached the way a caller reaches one: by searching."""
    hits = index.search("counts CD8 tumour lymphocytes", mode="keyword", top_k=200)
    return sorted({hit.chunk.chunk_id: hit.chunk for hit in hits}.values(), key=lambda chunk: chunk.chunk_index)


def test_a_section_knows_where_it_sits_in_the_text_the_parser_read():
    parsed = parse_document(TEXT, "atlas.md")
    by_title = {section.title: section for section in parsed.sections}
    abstract = by_title["Abstract"]
    assert parsed.full_text[abstract.char_start : abstract.char_end] == abstract.content
    markers = by_title["Markers"]
    assert markers.char_start > abstract.char_start
    assert parsed.full_text[markers.char_start : markers.char_end] == markers.content


def test_every_chunk_reproduces_itself_from_the_document_it_locates():
    parsed = parse_document(TEXT, "atlas.md")
    chunks = chunk_document("doc_locator_001", parsed, chunk_size=200, overlap=60)
    assert len(chunks) > 2, "the fixture has to be long enough to split, or the test proves nothing"
    for chunk in chunks:
        assert parsed.full_text[chunk.char_start : chunk.char_end] == chunk.content
        assert chunk.char_end > chunk.char_start


def test_a_chunk_the_parser_could_not_place_carries_no_locator():
    """A span that does not point at the text is worse than no span, because it reads as though it did."""
    parsed = ParsedDocument(
        title="Rewritten",
        sections=[ParsedSection(title="Body", content="reworded by the backend", level=1)],
        full_text="the backend joined things differently and this text is not in it",
    )
    chunks = chunk_document("doc_locator_002", parsed)
    assert [chunk.content for chunk in chunks] == ["reworded by the backend"]
    assert chunks[0].char_start is None
    assert chunks[0].char_end is None
    assert chunks[0].page is None


def test_a_page_is_carried_onto_the_chunks_it_came_from():
    """PDF backends hand over one section per page, so a chunk can name the page to turn to."""
    parsed = ParsedDocument(
        title="paper",
        sections=[
            ParsedSection(title="Page 1", content="first page text", level=1, path="Page 1", page=1),
            ParsedSection(title="Page 2", content="second page text", level=1, path="Page 2", page=2),
        ],
        full_text="first page text\n\nsecond page text",
    )
    chunks = chunk_document("doc_locator_003", parsed)
    assert [chunk.page for chunk in chunks] == [1, 2]
    # No offset pass ran over this hand-built parse, so only the page locates it.
    assert [chunk.char_start for chunk in chunks] == [None, None]


def test_a_cited_chunk_comes_back_with_the_pieces_the_index_kept_around_it():
    index = _index()
    cited = _stored_chunks(index)[3]
    assert cited.char_start is not None

    reading = index.read_chunk(cited.chunk_id, project_id="proj_locator")
    assert reading is not None
    assert reading.chunk.chunk_id == cited.chunk_id
    assert reading.chunk.content == cited.content
    assert reading.document is not None
    assert reading.document.doc_id == "doc_locator_001"
    # A citation is a window onto a document, so the pieces either side are the honest context: the index
    # never re-reads the file, and it does not pretend to.
    assert [chunk.chunk_index for chunk in reading.before] == [cited.chunk_index - 1]
    assert [chunk.chunk_index for chunk in reading.after] == [cited.chunk_index + 1]
    index.close()


def test_the_context_window_is_the_radius_asked_for_not_more():
    index = _index()
    chunks = _stored_chunks(index)
    middle = chunks[len(chunks) // 2]
    for radius in (0, 1, 3):
        reading = index.read_chunk(middle.chunk_id, project_id="proj_locator", radius=radius)
        assert reading is not None
        assert len(reading.before) == radius
        assert len(reading.after) == radius
        assert [chunk.chunk_index for chunk in reading.before] == [
            middle.chunk_index - step for step in range(radius, 0, -1)
        ]
        assert [chunk.chunk_index for chunk in reading.after] == [
            middle.chunk_index + step for step in range(1, radius + 1)
        ]
    index.close()


def test_a_chunk_id_from_another_project_reads_as_absent():
    """Chunks are addressed by id, so another project's passage must not arrive by guessing one."""
    index = _index()
    cited = _stored_chunks(index)[0]
    assert index.read_chunk(cited.chunk_id, project_id="proj_somebody_elses") is None
    assert index.read_chunk(cited.chunk_id, project_id="proj_locator") is not None
    index.close()


def test_an_index_that_predates_the_locator_columns_keeps_its_text(tmp_path):
    """Old rows gain empty columns rather than disappearing, and read as unlocatable instead of invented.

    The screen is told by a ``None``, so a document ingested before locators existed is not retroactively
    given a position nobody recorded for it.
    """
    path = tmp_path / "knowledge.db"
    index = KnowledgeIndex(str(path))
    index.add_document(_doc(), TEXT)
    cited = _stored_chunks(index)[0].chunk_id
    index.close()

    conn = sqlite3.connect(path)
    for column in ("page", "char_start", "char_end"):
        conn.execute(f"alter table knowledge_chunks drop column {column}")
    conn.commit()
    conn.close()

    reopened = KnowledgeIndex(str(path))
    reading = reopened.read_chunk(cited, project_id="proj_locator")
    assert reading is not None, "the rows an old index wrote are still there"
    assert reading.chunk.char_start is None
    assert reading.chunk.char_end is None
    assert reading.chunk.page is None
    assert reading.chunk.content
    assert reading.document is not None
    reopened.close()
