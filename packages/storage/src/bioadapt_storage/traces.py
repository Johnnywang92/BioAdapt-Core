"""Trace capture for model conversation content (plan V2.0 sections 4.5, 9.7, 11.2).

The index keeps only the digests of a model call (``model_invocation``); the prompt and response
bodies live behind this store, which the API hands out only to an audit reader. Two services that
build model calls therefore share one contract here rather than reaching into the API package.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Protocol

from pydantic import Field

from bioadapt_schemas.entities import Contract


class TraceRecord(Contract):
    trace_id: str
    project_id: str
    run_id: Optional[str] = None
    captured_at: Optional[datetime] = None
    kind: str = Field(min_length=1)
    content: dict[str, Any] = Field(default_factory=dict)


class TraceStore(Protocol):
    def put(self, record: TraceRecord) -> None: ...

    def get(self, trace_id: str, project_id: str) -> Optional[TraceRecord]: ...


class InMemoryTraceStore:
    """Reference store for the single-node deployment and the tests."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], TraceRecord] = {}

    def put(self, record: TraceRecord) -> None:
        self._records[(record.trace_id, record.project_id)] = record

    def get(self, trace_id: str, project_id: str) -> Optional[TraceRecord]:
        return self._records.get((trace_id, project_id))
