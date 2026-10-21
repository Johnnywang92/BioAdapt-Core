"""Orchestration failures (plan V2.0 sections 9.2, 9.7).

Each error already carries the run state it belongs to, so the API's error body, the state machine
and the §11.3 acceptance cases all name the same thing instead of translating between vocabularies.
"""

from __future__ import annotations

from typing import Optional

from bioadapt_schemas.enums import RunState
from bioadapt_schemas.errors import ErrorClass


class OrchestrationError(Exception):
    error_class: ErrorClass = ErrorClass.VALIDATION
    code: str = "orchestration_error"
    status_code: int = 400
    run_state: Optional[RunState] = None

    def __init__(
        self,
        message: str,
        *,
        field: Optional[str] = None,
        retryable: bool = False,
        retry_after: Optional[int] = None,
        run_state: Optional[RunState] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.retryable = retryable
        self.retry_after = retry_after
        self.run_state = run_state if run_state is not None else type(self).run_state


class IllegalTransition(OrchestrationError):
    error_class = ErrorClass.CONFLICT
    code = "illegal_transition"
    status_code = 409


class ActorNotAllowed(OrchestrationError):
    error_class = ErrorClass.POLICY_BLOCKED
    code = "actor_not_allowed"
    status_code = 403


class ApprovalRequired(OrchestrationError):
    error_class = ErrorClass.POLICY_BLOCKED
    code = "approval_required"
    status_code = 403


class ApprovalRefused(OrchestrationError):
    error_class = ErrorClass.POLICY_BLOCKED
    code = "approval_refused"
    status_code = 403


class RetryRefused(OrchestrationError):
    error_class = ErrorClass.RETRYABLE_FAILURE
    code = "retry_refused"
    status_code = 409
    run_state = RunState.RETRYABLE_FAILURE


class BackoffPending(OrchestrationError):
    error_class = ErrorClass.RETRYABLE_FAILURE
    code = "retry_backoff"
    status_code = 429
    run_state = RunState.RETRYABLE_FAILURE

    def __init__(self, message: str, *, retry_after: int, **kwargs) -> None:
        super().__init__(message, retryable=True, retry_after=retry_after, **kwargs)


class BudgetRefused(OrchestrationError):
    error_class = ErrorClass.BUDGET_EXCEEDED
    code = "budget_exceeded"
    status_code = 403
    run_state = RunState.BUDGET_EXCEEDED
