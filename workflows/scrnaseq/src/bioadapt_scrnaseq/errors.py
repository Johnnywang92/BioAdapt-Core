"""Failure classification for workflow execution (plan V2.0 sections 9.2, 9.7, 10.1).

A step either returns products or raises ``StepFailure`` carrying the error class the orchestrator
records, so "the file was truncated" and "the policy forbids this" cannot both arrive as a generic
crash. Stop conditions are failures too: they halt the run rather than retry it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from bioadapt_schemas.errors import ApiError, ErrorClass


@dataclass(frozen=True)
class StepFailure(Exception):
    code: str
    error_class: ErrorClass
    message: str
    field: Optional[str] = None
    retryable: bool = False

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    @property
    def is_stop_condition(self) -> bool:
        return self.error_class is ErrorClass.FAILED

    def as_api_error(self, *, trace_id: Optional[str] = None) -> ApiError:
        return ApiError(
            code=self.code,
            error_class=self.error_class,
            message=self.message,
            field=self.field,
            retryable=self.retryable,
            trace_id=trace_id,
        )


def missing_parameter(name: str, *, where: str) -> StepFailure:
    return StepFailure(
        code="parameter_missing",
        error_class=ErrorClass.NEEDS_INPUT,
        message=f"{where} requires parameter {name!r}; stage-0 numbers are inputs, not code defaults (appendix C1)",
        field=name,
    )


def invalid_input(detail: str, *, field: Optional[str] = None) -> StepFailure:
    return StepFailure(
        code="input_schema_invalid",
        error_class=ErrorClass.FAILED,
        message=f"input schema invalid: {detail}",
        field=field,
    )


def critical_quality(detail: str) -> StepFailure:
    return StepFailure(
        code="critical_quality_failure",
        error_class=ErrorClass.FAILED,
        message=f"critical quality failure: {detail}",
    )


def design_confounding(detail: str) -> StepFailure:
    return StepFailure(
        code="unresolved_design_confounding",
        error_class=ErrorClass.FAILED,
        message=f"unresolved design confounding: {detail}",
    )


def policy_blocked(detail: str, *, field: Optional[str] = None) -> StepFailure:
    return StepFailure(code="policy_blocked", error_class=ErrorClass.POLICY_BLOCKED, message=detail, field=field)


def resource_exceeded(detail: str) -> StepFailure:
    return StepFailure(code="resource_exceeded", error_class=ErrorClass.RESOURCE_EXCEEDED, message=detail, retryable=True)


def transient(detail: str) -> StepFailure:
    return StepFailure(code="retryable_failure", error_class=ErrorClass.RETRYABLE_FAILURE, message=detail, retryable=True)
