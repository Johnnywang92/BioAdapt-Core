"""Tests for parsers (Markdown, Docx, PDF)."""

import io
import zipfile
import pytest

from bioadapt_knowledge.parsers.registry import parse_document
from bioadapt_knowledge.parsers.docx import parse_docx
from bioadapt_knowledge.parsers.pdf import parse_pdf


def test_parse_markdown():
    md_text = """# Single-cell Analysis of CD4+ T Cells
This paper studies CD4+ T regulatory cells in tumor microenvironment.

## Materials and Methods
We used 10x Genomics Chromium platform to sequence 10,000 cells.

### Sequencing Depth
Average depth was 50,000 reads per cell.

## Results
FOXP3 and IL2RA were significantly upregulated in Cluster 3.
"""
    parsed = parse_document(md_text, "study_sc_tcell.md")
    assert parsed.title == "Single-cell Analysis of CD4+ T Cells"
    assert len(parsed.sections) >= 3
    paths = [s.full_path for s in parsed.sections]
    assert any("Materials and Methods" in p for p in paths)
    assert any("Results" in p for p in paths)


def test_parse_docx_pure_python():
    # Construct a minimal docx in-memory zip
    buffer = io.BytesIO()
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
        <w:body>
            <w:p>
                <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
                <w:r><w:t>Lab Experiment Log 2026-10-21</w:t></w:r>
            </w:p>
            <w:p>
                <w:r><w:t>Today we validated the viability of PBMC samples.</w:t></w:r>
            </w:p>
            <w:p>
                <w:pPr><w:pStyle w:val="Heading2"/></w:pPr>
                <w:r><w:t>Cluster 2 Marker Validation</w:t></w:r>
            </w:p>
            <w:p>
                <w:r><w:t>FACS confirmation of CD19+ and MS4A1+ B cells.</w:t></w:r>
            </w:p>
        </w:body>
    </w:document>
    """
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", document_xml)

    docx_bytes = buffer.getvalue()
    parsed = parse_docx(docx_bytes, "lab_log.docx")
    assert parsed.title == "Lab Experiment Log 2026-10-21"
    assert len(parsed.sections) >= 2
    assert "PBMC" in parsed.full_text
    assert "FACS confirmation" in parsed.full_text


def test_parse_pdf_fallback():
    # Even with raw text or minimal PDF stream, fallback parses gracefully
    raw_pdf_stream = b"%PDF-1.4\n1 0 obj\n<< /Length 50 >>\nstream\nBT\n/F1 12 Tf\n(Marker Gene FOXP3 in Treg Cells) Tj\nET\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"
    parsed = parse_pdf(raw_pdf_stream, "test_article.pdf")
    assert parsed.title == "test_article"
    assert "FOXP3" in parsed.full_text
