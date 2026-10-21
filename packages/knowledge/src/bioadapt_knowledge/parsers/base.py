"""Base types and protocols for document parsing (plan V2.0 section 6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ParsedSection:
    title: str
    content: str
    level: int = 1
    path: str = ""

    @property
    def full_path(self) -> str:
        return f"{self.path} / {self.title}" if self.path else self.title


@dataclass
class ParsedDocument:
    title: str
    sections: list[ParsedSection] = field(default_factory=list)
    full_text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class DocumentParser(Protocol):
    def parse(self, content: bytes | str, filename: str) -> ParsedDocument: ...
