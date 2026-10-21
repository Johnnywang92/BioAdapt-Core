"""Trace identifiers and log field discipline (sections 9.7, 11.2).

Prompt and model response bodies never reach the general log: they are written to the trace store
in :mod:`bioadapt_storage.traces`, which the API hands out only to an ``audit:read`` subject, and the
log line carries a pointer instead.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Optional, Protocol

LOGGER = logging.getLogger("bioadapt.api")

MASKED = "***"
#: Values that must never appear in a log line or an error body.
SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "password",
        "secret",
        "signing_secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "signature",
        "credential",
        "credentials",
        "private_key",
    }
)
#: Model conversation content: trace store only (section 9.7 and 11.6).
PROMPT_KEYS = frozenset({"prompt", "response", "completion", "messages", "transcript", "raw_model_output"})
#: Cell- and donor-level content is not a log field (section 11.5).
SUBJECT_KEYS = frozenset({"barcode", "cell_key", "donor_id", "specimen_id"})

#: Section 9.7: one field spec for every API log line.
LOG_FIELDS = (
    "timestamp",
    "level",
    "trace_id",
    "project_id",
    "subject_id",
    "action",
    "resource_id",
    "status",
    "latency_ms",
)


def new_trace_id() -> str:
    return "tr_" + uuid.uuid4().hex[:16]


def _leaf(value: Any, key: str) -> Any:
    if key in PROMPT_KEYS:
        return "<trace-store:content-withheld>"
    if key in SENSITIVE_KEYS or key in SUBJECT_KEYS:
        return MASKED
    return value


def mask(value: Any, key: str = "") -> Any:
    withheld = _leaf(value, key)
    if withheld is not value:
        return withheld
    if isinstance(value, dict):
        return {name: mask(item, name) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask(item, key) for item in value]
    if hasattr(value, "model_dump"):
        return mask(value.model_dump(mode="json"), key)
    return value


class LogSink(Protocol):
    def emit(self, event: dict[str, Any]) -> None: ...


class StdlibLogSink:
    def emit(self, event: dict[str, Any]) -> None:
        LOGGER.info(json.dumps(event, sort_keys=True, default=str))


class MemoryLogSink:
    """Reference sink for tests and the single-node deployment."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def lines(self) -> str:
        return json.dumps(self.events, default=str)


def emit(sink: LogSink, level: str, *, timestamp: Optional[datetime] = None, **fields: Any) -> None:
    payload = {"level": level, "timestamp": timestamp or datetime.now(UTC)}
    payload.update({key: value for key, value in fields.items() if value is not None})
    ordered = {name: payload.pop(name) for name in LOG_FIELDS if name in payload}
    ordered.update(payload)
    sink.emit(mask(ordered))
