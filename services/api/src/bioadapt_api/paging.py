"""Forced pagination (plan V2.0 section 9.7): default 50, hard cap 500.

Large tables are not paged deeper than that; the plan sends those to the asynchronous export
resource instead, so the API never turns a list endpoint into a full-table scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from fastapi import Query

from bioadapt_policies.access import page_size

from .errors import invalid


@dataclass(frozen=True)
class Page:
    limit: int
    offset: int

    def envelope(self, items: list[Any]) -> dict[str, Any]:
        return {
            "items": items,
            "limit": self.limit,
            "offset": self.offset,
            "next_offset": self.offset + self.limit if len(items) == self.limit else None,
        }


def page_params(
    limit: Optional[int] = Query(None, ge=1, description="page size, capped at the policy maximum"),
    offset: int = Query(0, ge=0, description="rows to skip"),
) -> Page:
    try:
        size = page_size(limit)
    except ValueError as exc:
        raise invalid("limit", str(exc)) from exc
    return Page(limit=size, offset=offset)
