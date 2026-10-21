"""Error model shared by API, orchestrator and gates (plan V2.0 section 9.7)."""

from __future__ import annotations

from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .enums import RunState


class ErrorClass(StrEnum):
    VALIDATION = "validation"
    RETRYABLE_FAILURE = "retryable_failure"
    NEEDS_INPUT = "needs_input"
    POLICY_BLOCKED = "policy_blocked"
    RESOURCE_EXCEEDED = "resource_exceeded"
    BUDGET_EXCEEDED = "budget_exceeded"
    GATE_BLOCKED = "gate_blocked"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    FAILED = "failed"


class ApiError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    error_class: ErrorClass
    message: str
    field: Optional[str] = None
    retryable: bool = False
    retry_after: Optional[int] = Field(None, ge=0)
    trace_id: Optional[str] = None
    run_state: Optional[RunState] = None


ERROR_CLASS_TO_RUN_STATE: dict[ErrorClass, RunState] = {
    ErrorClass.RETRYABLE_FAILURE: RunState.RETRYABLE_FAILURE,
    ErrorClass.NEEDS_INPUT: RunState.NEEDS_INPUT,
    ErrorClass.POLICY_BLOCKED: RunState.POLICY_BLOCKED,
    ErrorClass.RESOURCE_EXCEEDED: RunState.RESOURCE_EXCEEDED,
    ErrorClass.BUDGET_EXCEEDED: RunState.BUDGET_EXCEEDED,
    ErrorClass.GATE_BLOCKED: RunState.BLOCKED,
    ErrorClass.FAILED: RunState.FAILED,
}


def to_run_state(error_class: ErrorClass) -> Optional[RunState]:
    return ERROR_CLASS_TO_RUN_STATE.get(error_class)
