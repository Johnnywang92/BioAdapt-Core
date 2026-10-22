"""Base types and protocols for document parsing (plan V2.0 section 6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class ParsedSection:
    title: str
    content: str
    level: int = 1
    path: str = ""
    page: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None

    @property
    def full_path(self) -> str:
        return f"{self.path} / {self.title}" if self.path else self.title


@dataclass
class ParsedDocument:
    title: str
    sections: list[ParsedSection] = field(default_factory=list)
    full_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def attach_offsets(text: str, sections: list[ParsedSection]) -> None:
    """Record where each section sits inside the document text, so a citation can be located (§6.5).

    Sections come out of a parser in reading order, so walking the text with a cursor gives an exact span
    for each of them. A section the parser reworded rather than sliced is left without a span: a locator
    that does not point at the text is worse than no locator, because it reads as though it did.
    """
    cursor = 0
    for section in sections:
        if not section.content:
            continue
        found = text.find(section.content, cursor)
        if found < 0:
            continue
        section.char_start = found
        section.char_end = found + len(section.content)
        cursor = section.char_end


class DocumentParser(Protocol):
    def parse(self, content: bytes | str, filename: str) -> ParsedDocument: ...
