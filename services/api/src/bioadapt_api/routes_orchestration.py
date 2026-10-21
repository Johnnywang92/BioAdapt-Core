"""State machine, run journal and approval routes (plan V2.0 sections 9.1, 9.2, 9.7, 11.2, 11.6).

Two things happen here that happen nowhere else: a run changes state only through the orchestrator,
so every move carries an actor, a reason and the versions it was made under; and a gate is opened
only by a person who is not the one that asked for it. The workflow engine may report a transition
the platform has already authorised - it may not authorise one.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import Field

from bioadapt_orchestrator.graph import TERMINAL
from bioadapt_orchestrator.service import ActorRef, TransitionResult
from bioadapt_policies.access import Action
from bioadapt_schemas.entities import Contract
from bioadapt_schemas.enums import ApprovalKind, ApprovalState, RunEventKind, RunState
from bioadapt_schemas.orchestration import Approval, RunEvent

from .deps import SessionDep
from .errors import not_found
from .paging import Page, page_params

router = APIRouter(tags=["orchestration"])

#: Section 9.2: the workflow engine's node-level tolerance and the state machine's own retry are
#: different events in the same journal, and the caller says which one it is reporting.
RetryLevel = Literal["node", "business"]


class TransitionRequest(Contract):
    to_state: RunState
    reason: str = Field(min_length=1)
    attempt: int = Field(1, ge=1)
    #: The state the callback believes it is reporting from; a stale one is refused, not applied.
    from_state: Optional[RunState] = None
    approval_id: Optional[str] = None
    input_hash: Optional[str] = None
    resume_with_plan_version: Optional[str] = None


class RetryRequest(Contract):
    node_id: str
    attempt: int = Field(ge=1)
    level: RetryLevel
    reason: str = Field(min_length=1)


class UsageRequest(Contract):
    compute_minutes: float = Field(0.0, ge=0)
    model_cost_usd: float = Field(0.0, ge=0)
    attempt: int = Field(1, ge=1)
    reason: str = Field(min_length=1)


class CancelRequest(Contract):
    reason: str = Field(min_length=1)


class GateRequest(Contract):
    kind: ApprovalKind
    node_id: Optional[str] = None
    #: Who is accountable for the decision; the project owner answers when it is left open.
    assigned_to: Optional[str] = None
    plan_version: Optional[str] = None


class GateDecision(Contract):
    approved: bool
    reason: str = Field(min_length=1)


class GateDelegation(Contract):
    to_subject: str = Field(min_length=1)


def _require_run(session: SessionDep, run_id: str, action: Action = Action.RUN_READ) -> None:
    session.enforce(action, run_id, "run")
    try:
        session.catalog.run_state(run_id)
    except LookupError:
        raise not_found("run", run_id) from None


def _release_slot(session: SessionDep, run_id: str, state: RunState) -> None:
    """Section 9.7: the slot a launch took is held for a live run, so the terminal state gives it back."""
    if state in TERMINAL:
        session.state.queue.finish(run_id)


def _event_body(event: RunEvent) -> dict:
    return event.model_dump(mode="json")


def _result_body(result: TransitionResult) -> dict:
    return {
        "event": _event_body(result.event),
        "state": str(result.state),
        "replayed": result.replayed,
        "escalated": result.escalated,
        "reasons": result.reasons,
        "tolerance_exhausted": result.tolerance_exhausted,
    }


def _approval_body(approval: Approval, session: SessionDep) -> dict:
    policy = session.state.approval_policy
    now = session.state.clock()
    overdue = policy.is_overdue(approval, now)
    return {
        **approval.model_dump(mode="json"),
        "overdue": overdue,
        "overdue_reason": policy.overdue_reason(approval, now) if overdue else None,
    }


@router.post("/projects/{project_id}/runs/{run_id}/transitions", status_code=201)
def apply_transition(run_id: str, body: TransitionRequest, session: SessionDep):
    """Section 9.2: the only way a run moves, and every move is justified in the journal."""
    _require_run(session, run_id, Action.RUN_UPDATE)
    result = session.orchestrator.transition(
        run_id,
        body.to_state,
        actor=ActorRef.of(session.actor),
        reason=body.reason,
        attempt=body.attempt,
        from_state=body.from_state,
        approval_id=body.approval_id,
        input_hash=body.input_hash,
        resume_with_plan_version=body.resume_with_plan_version,
    )
    session.audit(
        "run:transition",
        run_id,
        details={"to_state": str(body.to_state), "attempt": body.attempt, "event_id": result.event.event_id},
    )
    _release_slot(session, run_id, result.state)
    status = 200 if result.replayed else 201
    headers = {"Idempotency-Replayed": "true"} if result.replayed else None
    return JSONResponse(status_code=status, content=_result_body(result), headers=headers)


@router.get("/projects/{project_id}/runs/{run_id}/transitions")
def read_journal(
    run_id: str,
    session: SessionDep,
    page: Page = Depends(page_params),
    kind: Optional[RunEventKind] = Query(None),
) -> dict:
    """The journal is the run's audit trail: transitions, both retry sides, and spend."""
    _require_run(session, run_id)
    events = session.orchestrator.journal(
        run_id, kind=str(kind) if kind else None, limit=page.limit, offset=page.offset
    )
    return page.envelope([_event_body(event) for event in events])


@router.post("/projects/{project_id}/runs/{run_id}/retries", status_code=201)
def register_retry(run_id: str, body: RetryRequest, session: SessionDep):
    """Section 9.2: the engine's node-level tolerance and the state machine's own retry are both registered."""
    _require_run(session, run_id, Action.RUN_UPDATE)
    orchestrator = session.orchestrator
    if body.level == "node":
        result = orchestrator.register_node_retry(
            run_id, body.node_id, attempt=body.attempt, actor=ActorRef.of(session.actor), reason=body.reason
        )
    else:
        result = orchestrator.business_retry(
            run_id, body.node_id, attempt=body.attempt, actor=ActorRef.of(session.actor), reason=body.reason
        )
    session.audit(
        f"run:{body.level}_retry",
        run_id,
        details={"node_id": body.node_id, "attempt": body.attempt, "event_id": result.event.event_id},
    )
    return JSONResponse(status_code=200 if result.replayed else 201, content=_result_body(result))


@router.post("/projects/{project_id}/runs/{run_id}/nodes/{node_id}/usage", status_code=201)
def report_usage(run_id: str, node_id: str, body: UsageRequest, session: SessionDep):
    """Spend is counted where the plan bounds it: past a ceiling the run is handed to a person."""
    _require_run(session, run_id, Action.RUN_UPDATE)
    result = session.orchestrator.report_usage(
        run_id,
        node_id,
        compute_minutes=body.compute_minutes,
        model_cost_usd=body.model_cost_usd,
        attempt=body.attempt,
        actor=ActorRef.of(session.actor),
        reason=body.reason,
    )
    #: Spend is a compliance fact, not only a run fact: the journal says the engine reported it, this row
    #: says who was holding the token when the ceiling was crossed and whether it went to a person.
    session.audit(
        "run:usage",
        run_id,
        details={
            "node_id": node_id,
            "attempt": body.attempt,
            "compute_minutes": body.compute_minutes,
            "model_cost_usd": body.model_cost_usd,
            "event_id": result.event.event_id,
            "escalated": result.escalated,
        },
    )
    return JSONResponse(status_code=200 if result.replayed else 201, content=_result_body(result))


@router.get("/projects/{project_id}/runs/{run_id}/budget")
def read_loop_budget(run_id: str, session: SessionDep) -> dict:
    _require_run(session, run_id)
    orchestrator = session.orchestrator
    usage = orchestrator.usage(run_id)
    ceiling = orchestrator.ceiling(run_id)
    return {
        "run_id": run_id,
        "usage": {
            "iterations": usage.iterations,
            "compute_minutes": usage.compute_minutes,
            "model_cost_usd": usage.model_cost_usd,
        },
        "ceiling": {
            "max_iterations": ceiling.max_iterations,
            "max_compute_minutes": ceiling.max_compute_minutes,
            "max_model_cost_usd": ceiling.max_model_cost_usd,
        },
        "exhausted": ceiling.exhausted(usage),
    }


@router.post("/projects/{project_id}/runs/{run_id}/cancel", status_code=201)
def cancel_run(run_id: str, body: CancelRequest, session: SessionDep):
    """Section 9.1: ending a run is a person's act, so this route is not open to a service token."""
    _require_run(session, run_id, Action.RUN_CANCEL)
    result = session.orchestrator.cancel(run_id, actor=ActorRef.of(session.actor), reason=body.reason)
    session.audit(
        "run:cancel",
        run_id,
        details={"event_id": result.event.event_id, "state": str(result.state), "reason": body.reason},
    )
    _release_slot(session, run_id, result.state)
    return _result_body(result)


@router.get("/projects/{project_id}/runs/{run_id}/nodes/{node_id}/approval")
def check_node_gate(run_id: str, node_id: str, session: SessionDep) -> dict:
    """Section 9.6: the engine asks this before it starts a node the plan marks as gated."""
    _require_run(session, run_id)
    approval = session.orchestrator.require_node_approval(run_id, node_id)
    return _approval_body(approval, session)


@router.post("/projects/{project_id}/runs/{run_id}/approvals", status_code=201)
def request_gate(run_id: str, body: GateRequest, session: SessionDep):
    _require_run(session, run_id, Action.APPROVAL_REQUEST)
    approval = session.orchestrator.request_approval(
        run_id,
        body.kind,
        actor=ActorRef.of(session.actor),
        node_id=body.node_id,
        assigned_to=body.assigned_to,
        plan_version=body.plan_version,
    )
    session.audit("approval:request", approval.approval_id, details={"kind": str(body.kind), "run_id": run_id})
    return _approval_body(approval, session)


@router.get("/projects/{project_id}/approvals")
def read_approvals(
    session: SessionDep,
    page: Page = Depends(page_params),
    state: Optional[ApprovalState] = Query(None),
    run_id: Optional[str] = Query(None),
    overdue: bool = Query(False, description="pending gates whose window has lapsed"),
) -> dict:
    """The approval inbox, and the overdue view of it (section 9.2: a lapsed window is a reminder, not a grant)."""
    session.enforce(Action.APPROVAL_READ, f"{session.project_id}:approval_list", "approval_collection")
    orchestrator = session.orchestrator
    if overdue:
        found = orchestrator.overdue_approvals(limit=page.limit, offset=page.offset)
    else:
        found = orchestrator.pending_approvals(state=state, run_id=run_id, limit=page.limit, offset=page.offset)
    return page.envelope([_approval_body(a, session) for a in found])


@router.post("/projects/{project_id}/approvals/{approval_id}/decision")
def decide_gate(approval_id: str, body: GateDecision, session: SessionDep):
    """Section 11.6: a person who did not ask for the gate is the only one who can open it."""
    orchestrator = session.orchestrator
    try:
        pending = orchestrator.approval_of(approval_id)
    except LookupError:
        raise not_found("approval", approval_id) from None
    session.enforce(
        Action.APPROVAL_DECIDE,
        approval_id,
        "approval",
        requesting_subject_id=pending.requested_by,
        attributes={"kind": str(pending.kind), "run_id": pending.run_id},
    )
    decided = orchestrator.decide_approval(
        approval_id, actor=ActorRef.of(session.actor), approved=body.approved, reason=body.reason
    )
    session.audit(
        "approval:decision",
        approval_id,
        details={"approved": body.approved, "run_id": decided.run_id, "kind": str(decided.kind)},
    )
    return _approval_body(decided, session)


@router.post("/projects/{project_id}/approvals/{approval_id}/delegation")
def delegate_gate(approval_id: str, body: GateDelegation, session: SessionDep):
    """Section 9.2: an unanswered gate moves to a named delegate, and the move is recorded."""
    orchestrator = session.orchestrator
    try:
        held = orchestrator.approval_of(approval_id)
    except LookupError:
        raise not_found("approval", approval_id) from None
    session.enforce(Action.APPROVAL_DELEGATE, approval_id, "approval", attributes={"assigned_to": held.assigned_to})
    delegated = orchestrator.delegate_approval(approval_id, actor=ActorRef.of(session.actor), to_subject=body.to_subject)
    session.audit(
        "approval:delegation",
        approval_id,
        details={"delegated_from": delegated.delegated_from, "delegated_to": delegated.assigned_to},
    )
    return _approval_body(delegated, session)
