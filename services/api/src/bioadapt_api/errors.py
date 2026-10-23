"""HTTP error model (plan V2.0 section 9.7).

One wire shape for every failure: ``{code, class, field, message, retryable, retry_after, trace_id}``,
with ``class`` taken from the shared :class:`ErrorClass` so the API, the state machine and the
§11.3 acceptance cases speak about the same thing.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from bioadapt_evidence.errors import EvidenceError
from bioadapt_orchestrator.errors import OrchestrationError
from bioadapt_schemas.errors import ApiError, ErrorClass
from bioadapt_schemas.gates import GateViolation
from bioadapt_storage.catalog import ConcurrentWriteError, ScopeError

from .observability import emit


class ApiProblem(Exception):
    def __init__(self, error: ApiError, status_code: int) -> None:
        super().__init__(error.message)
        self.error = error
        self.status_code = status_code


def problem(
    status_code: int,
    error_class: ErrorClass,
    code: str,
    message: str,
    *,
    field: Optional[str] = None,
    retryable: bool = False,
    retry_after: Optional[int] = None,
) -> ApiProblem:
    return ApiProblem(
        ApiError(
            code=code,
            error_class=error_class,
            message=message,
            field=field,
            retryable=retryable,
            retry_after=retry_after,
        ),
        status_code,
    )


def unauthorized(message: str = "a valid platform token is required") -> ApiProblem:
    return problem(401, ErrorClass.POLICY_BLOCKED, "unauthenticated", message)


def forbidden(denials: list[str], resource_id: str) -> ApiProblem:
    return problem(
        403,
        ErrorClass.POLICY_BLOCKED,
        "authorization_denied",
        f"{resource_id}: " + "; ".join(denials),
    )


def not_found(kind: str, identifier: str) -> ApiProblem:
    return problem(404, ErrorClass.NOT_FOUND, f"{kind}_not_found", f"{kind} {identifier!r} is not in this project")


def not_found_in_deployment(kind: str, identifier: str) -> ApiProblem:
    """A platform-level resource: these rows belong to no project, so the project wording is wrong."""
    return problem(404, ErrorClass.NOT_FOUND, f"{kind}_not_found", f"{kind} {identifier!r} is not in this deployment")


def invalid(field: str, message: str) -> ApiProblem:
    return problem(400, ErrorClass.VALIDATION, "invalid_request", message, field=field)


def conflict(message: str, *, field: Optional[str] = None) -> ApiProblem:
    return problem(409, ErrorClass.CONFLICT, "conflict", message, field=field)


def error_body(error: ApiError, trace_id: str) -> dict[str, Any]:
    # `class` is a Python keyword, so the contract keeps `error_class` and the wire shape uses `class`.
    return {
        "code": error.code,
        "class": str(error.error_class),
        "field": error.field,
        "message": error.message,
        "retryable": error.retryable,
        "retry_after": error.retry_after,
        "trace_id": trace_id,
    }


def classify(exc: Exception) -> ApiProblem:
    if isinstance(exc, ApiProblem):
        return exc
    if isinstance(exc, GateViolation):
        return problem(422, ErrorClass.GATE_BLOCKED, "card_gate_blocked", str(exc))
    if isinstance(exc, EvidenceError):
        return problem(
            exc.status_code,
            exc.error_class,
            exc.code,
            exc.message,
            field=exc.field,
            retryable=exc.retryable,
        )
    if isinstance(exc, OrchestrationError):
        return problem(
            exc.status_code,
            exc.error_class,
            exc.code,
            exc.message,
            field=exc.field,
            retryable=exc.retryable,
            retry_after=exc.retry_after,
        )
    if isinstance(exc, RequestValidationError):
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ())[1:]) or None
        return invalid(field or "body", str(first.get("msg", "invalid request")))
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ())) or None
        return invalid(field or "body", str(first.get("msg", "invalid value")))
    if isinstance(exc, StarletteHTTPException):
        if exc.status_code == 404:
            return not_found("route", str(exc.detail))
        return problem(exc.status_code, ErrorClass.FAILED, f"http_{exc.status_code}", str(exc.detail))
    if isinstance(exc, ScopeError):
        return problem(403, ErrorClass.POLICY_BLOCKED, "project_scope_missing", str(exc))
    if isinstance(exc, PermissionError):
        return problem(403, ErrorClass.POLICY_BLOCKED, "permission_denied", str(exc))
    if isinstance(exc, LookupError):
        return not_found("resource", str(exc))
    if isinstance(exc, ConcurrentWriteError):
        #: The gate is still open and still pending - this write just was not the one that moved it, so
        #: the caller re-reads and replays against what the gate says now.
        return problem(409, ErrorClass.CONFLICT, "concurrent_write", str(exc), retryable=True)
    if isinstance(exc, sqlite3.IntegrityError):
        return conflict(str(exc))
    if isinstance(exc, ValueError):
        return problem(400, ErrorClass.VALIDATION, "invalid_request", str(exc))
    return problem(500, ErrorClass.FAILED, "internal_error", "the request failed; correlate it with the trace id")


def register_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiProblem)
    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    @app.exception_handler(GateViolation)
    @app.exception_handler(EvidenceError)
    @app.exception_handler(OrchestrationError)
    @app.exception_handler(LookupError)
    @app.exception_handler(PermissionError)
    @app.exception_handler(ScopeError)
    @app.exception_handler(ConcurrentWriteError)
    @app.exception_handler(StarletteHTTPException)
    @app.exception_handler(sqlite3.IntegrityError)
    async def handled(request: Request, exc: Exception) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", "trace_unassigned")
        problem_exc = classify(exc)
        if problem_exc.status_code >= 500:
            emit(request.app.state.api.log_sink, "error", trace_id=trace_id, exception=type(exc).__name__)
        return JSONResponse(
            status_code=problem_exc.status_code,
            content=error_body(problem_exc.error, trace_id),
            headers={"Retry-After": str(problem_exc.error.retry_after)} if problem_exc.error.retry_after else None,
        )

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError) -> JSONResponse:
        return await handled(request, exc)  # type: ignore[misc]

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        return await handled(request, exc)  # type: ignore[misc]
