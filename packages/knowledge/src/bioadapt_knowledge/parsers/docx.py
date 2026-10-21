"""Native DOCX parser extracting paragraphs, headings and tables using standard zipfile and xml."""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .base import ParsedDocument, ParsedSection

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def parse_docx(content: bytes | str, filename: str) -> ParsedDocument:
    raw_bytes = content.encode("utf-8") if isinstance(content, str) else content
    title = Path(filename).stem
    sections: list[ParsedSection] = []

    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            if "word/document.xml" not in z.namelist():
                return ParsedDocument(title=title, full_text="", metadata={"error": "invalid docx archive"})
            xml_data = z.read("word/document.xml")
    except Exception as err:
        return ParsedDocument(title=title, full_text="", metadata={"error": f"corrupt zip: {err}"})

    root = ET.fromstring(xml_data)
    body = root.find("w:body", NS)
    if body is None:
        return ParsedDocument(title=title, full_text="")

    current_title = "Overview"
    current_level = 1
    current_paragraphs: list[str] = []
    full_paragraphs: list[str] = []
    found_first_title = False

    for elem in body:
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "p":
            # Extract paragraph text
            texts = [node.text for node in elem.iter(f"{{{W_NS}}}t") if node.text]
            p_text = "".join(texts).strip()
            if not p_text:
                continue

            # Check if this paragraph is a Heading
            p_style = elem.find(".//w:pStyle", NS)
            style_val = p_style.attrib.get(f"{{{W_NS}}}val", "") if p_style is not None else ""
            is_heading = False
            heading_level = 1

            if style_val.lower().startswith("heading") or style_val.lower().startswith("标题"):
                m = re.search(r"\d+", style_val)
                heading_level = int(m.group(0)) if m else 1
                is_heading = True

            if is_heading:
                if not found_first_title and heading_level == 1:
                    title = p_text
                    found_first_title = True
                if current_paragraphs:
                    sec_content = "\n\n".join(current_paragraphs).strip()
                    if sec_content:
                        sections.append(
                            ParsedSection(title=current_title, content=sec_content, level=current_level)
                        )
                    current_paragraphs = []
                current_title = p_text
                current_level = heading_level
            else:
                current_paragraphs.append(p_text)
                full_paragraphs.append(p_text)

        elif tag == "tbl":
            # Extract table rows as markdown table
            rows_data: list[list[str]] = []
            for row in elem.findall(".//w:tr", NS):
                row_cells: list[str] = []
                for cell in row.findall(".//w:tc", NS):
                    cell_texts = [n.text for n in cell.iter(f"{{{W_NS}}}t") if n.text]
                    row_cells.append(" ".join(cell_texts).strip())
                if row_cells:
                    rows_data.append(row_cells)

            if rows_data:
                # Format as simple table text
                table_lines = [" | ".join(row) for row in rows_data]
                table_str = "\n".join(table_lines)
                current_paragraphs.append(table_str)
                full_paragraphs.append(table_str)

    if current_paragraphs:
        sec_content = "\n\n".join(current_paragraphs).strip()
        if sec_content:
            sections.append(ParsedSection(title=current_title, content=sec_content, level=current_level))

    return ParsedDocument(
        title=title,
        sections=sections,
        full_text="\n\n".join(full_paragraphs),
        metadata={"format": "docx"},
    )
