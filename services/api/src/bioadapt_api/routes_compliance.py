"""Egress decisions, approvals, audit and trace reads (plan V2.0 sections 9.7, 11.2, 11.5, 11.6, 13.1).

Egress is decided by the whitelist evaluator, never by a route that "looks safe": the request and the
resulting decision are both persisted, an approval is a separate human act that cannot come from the
requesting subject, and read-side endpoints hand back masked rows so that no cell-level or model
conversation content can leave through a log query.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from bioadapt_policies.access import Action
from bioadapt_policies.egress import EgressApproval, EgressRequest, evaluate_egress
from bioadapt_schemas.entities import Contract
from bioadapt_schemas.errors import ErrorClass

from .deps import SessionDep
from .errors import invalid, not_found, problem
from .observability import mask
from .paging import Page, page_params

router = APIRouter(tags=["compliance"])


class EgressRequestBody(EgressRequest):
    """Section 11.2: a restricted-tier request names the approval it relies on, if any."""

    approval_id: Optional[str] = None


class ApprovalRequest(Contract):
    requester_id: str
    purpose: str
    approved_on: date
    expires_on: date


def _approval_id() -> str:
    return f"egr_{uuid.uuid4().hex[:12]}"


def _load_approval(session: SessionDep, approval_id: str) -> EgressApproval:
    row = session.catalog.egress_approval(approval_id)
    return EgressApproval(
        approval_id=row["approval_id"],
        approver_id=row["approver_id"],
        project_id=session.project_id,
        purpose=row["purpose"],
        approved_on=date.fromisoformat(row["approved_on"]),
        expires_on=date.fromisoformat(row["expires_on"]),
    )


@router.post("/projects/{project_id}/egress/requests")
def request_egress(body: EgressRequestBody, session: SessionDep) -> dict:
    if body.project_id != session.project_id:
        raise problem(
            400, ErrorClass.VALIDATION, "project_mismatch", "the egress request names a different project", field="project_id"
        )
    #: Section 5.9/11.2: the project row's tier governs egress - a self-declared tier in the body
    #: would let a strict_local caller claim `public` and skip both the empty allowlist and the
    #: restricted-tier approval requirement. The floor is read from the catalog, never asserted.
    project = session.catalog.get_project()
    if body.privacy_tier is not project.privacy_tier:
        raise invalid(
            "privacy_tier",
            f"this project is tier {project.privacy_tier}; an egress request may not declare another",
        )
    session.enforce(
        Action.EGRESS_REQUEST,
        f"{session.project_id}:{body.destination}",
        "egress_request",
        privacy_tier=project.privacy_tier,
        attributes={"purpose": body.purpose, "field_count": len(body.fields)},
    )
    approval = None
    if body.approval_id is not None:
        try:
            approval = _load_approval(session, body.approval_id)
        except LookupError:
            raise not_found("egress approval", body.approval_id) from None
    decision = evaluate_egress(
        body,
        today=session.state.clock().date(),
        local_hosts=set(session.state.local_hosts),
        cloud_allowlist=set(session.state.cloud_allowlist),
        approval=approval,
        provider_terms=session.state.provider_terms.get(body.destination),
    )
    session.catalog.record_egress_decision(
        decision.decision_id(),
        request=body.model_dump(mode="json"),
        decision=decision.model_dump(mode="json"),
        allowed=decision.allowed,
    )
    session.audit(
        "egress:decide",
        decision.decision_id(),
        decision_id=decision.decision_id(),
        details={"allowed": decision.allowed, "denials": [d.model_dump(mode="json") for d in decision.denials]},
    )
    if not decision.allowed:
        raise problem(
            403,
            ErrorClass.POLICY_BLOCKED,
            "egress_denied",
            f"decision {decision.decision_id()}: " + "; ".join(decision.blocked_reasons + [d.rule for d in decision.denials]),
        )
    return {"decision_id": decision.decision_id(), "egress": decision.model_dump(mode="json")}


@router.get("/projects/{project_id}/egress/decisions/{decision_id}")
def read_egress_decision(decision_id: str, session: SessionDep) -> dict:
    session.enforce(Action.AUDIT_READ, decision_id, "egress_decision")
    try:
        return session.catalog.egress_decision(decision_id)
    except LookupError:
        raise not_found("egress decision", decision_id) from None


@router.post("/projects/{project_id}/egress/approvals", status_code=201)
def record_approval(body: ApprovalRequest, session: SessionDep) -> dict:
    """Section 13.1: the approver must be a different, second-factored human than the requester."""
    session.enforce(
        Action.EGRESS_APPROVE,
        f"{session.project_id}:{body.purpose}",
        "egress_approval",
        requesting_subject_id=body.requester_id,
    )
    approval_id = _approval_id()
    session.catalog.record_egress_approval(
        approval_id=approval_id,
        approver_id=session.actor.subject_id,
        purpose=body.purpose,
        approved_on=body.approved_on.isoformat(),
        expires_on=body.expires_on.isoformat(),
    )
    session.audit(
        "egress:approve",
        approval_id,
        details={"requester_id": body.requester_id, "purpose": body.purpose, "expires_on": str(body.expires_on)},
    )
    return {
        "approval_id": approval_id,
        "project_id": session.project_id,
        "approver_id": session.actor.subject_id,
        "purpose": body.purpose,
        "approved_on": str(body.approved_on),
        "expires_on": str(body.expires_on),
    }


@router.get("/projects/{project_id}/audit")
def read_audit(
    session: SessionDep,
    page: Page = Depends(page_params),
    subject_kind: Optional[str] = Query(None, pattern="^(human|service)$"),
    action: Optional[str] = Query(None),
    trace_id: Optional[str] = Query(None),
) -> dict:
    session.enforce(Action.AUDIT_READ, f"{session.project_id}:audit", "audit_collection")
    rows = session.catalog.audit_entries(
        subject_kind=subject_kind, action=action, trace_id=trace_id, limit=page.limit, offset=page.offset
    )
    return page.envelope([mask(row) for row in rows])


@router.get("/projects/{project_id}/traces/{trace_id}")
def read_trace(trace_id: str, session: SessionDep) -> dict:
    """Section 9.7: prompt and response content is reachable only here, and only for an audit reader."""
    session.enforce(Action.AUDIT_READ, trace_id, "trace")
    record = session.state.traces.get(trace_id, session.project_id)
    if record is None:
        raise not_found("trace", trace_id)
    return record.model_dump(mode="json")
