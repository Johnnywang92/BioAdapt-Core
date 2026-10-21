"""Native Markdown document parser preserving hierarchical sections."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import ParsedDocument, ParsedSection


def parse_markdown(content: bytes | str, filename: str) -> ParsedDocument:
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
    lines = text.splitlines()

    title = Path(filename).stem
    sections: list[ParsedSection] = []
    current_title = "Overview"
    current_level = 1
    current_path: list[str] = []
    current_lines: list[str] = []
    found_first_title = False

    header_re = re.compile(r"^(#{1,6})\s+(.+)$")

    for line in lines:
        match = header_re.match(line)
        if match:
            level = len(match.group(1))
            header_text = match.group(2).strip()

            if not found_first_title and level == 1:
                title = header_text
                found_first_title = True

            # Save previous section if it has content
            if current_lines:
                sec_text = "\n".join(current_lines).strip()
                if sec_text:
                    sections.append(
                        ParsedSection(
                            title=current_title,
                            content=sec_text,
                            level=current_level,
                            path=" / ".join(current_path),
                        )
                    )
                current_lines = []

            # Update hierarchy path
            while len(current_path) >= level:
                current_path.pop()
            current_path.append(header_text)

            current_title = header_text
            current_level = level
        else:
            current_lines.append(line)

    if current_lines:
        sec_text = "\n".join(current_lines).strip()
        if sec_text:
            sections.append(
                ParsedSection(
                    title=current_title,
                    content=sec_text,
                    level=current_level,
                    path=" / ".join(current_path[:-1]) if len(current_path) > 1 else "",
                )
            )

    return ParsedDocument(
        title=title,
        sections=sections,
        full_text=text,
        metadata={"format": "markdown", "lines": len(lines)},
    )
