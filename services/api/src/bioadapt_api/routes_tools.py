"""Ad-hoc tool grant routes (plan V2.0 sections 9.6, 11.6, 13.1).

Section 9.6 lets one project's unaudited code run once, inside a cage, when a PI approves that exact
submission. The platform had the cage and the registry-side permission, but no landing point for the
decision: an ``AdhocApproval`` was an object any process could build for itself, and its approver was
whatever that process claimed about itself. These routes are the landing point, and the chain they close
is 申请 → 决定 → 发放: a grant is only ever issued over an ``adhoc_tool`` gate a person has already
approved, by the very subject whose token is on this request.

Nothing here takes a caller's word for who approved, in which project, or when. The body carries a tool
spec and a gate id; the row that lands records the authenticated actor as approver, the run's own project,
the submitter read off the gate, and two timestamps from the clock the deployment owns. ``extra="forbid"``
means an ``approver_id`` or ``expires_at`` in the request is a rejected request, not a suggested value.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import Field

from bioadapt_policies.access import Action
from bioadapt_schemas.entities import Contract, sha256_of_json
from bioadapt_schemas.enums import ApprovalKind, ApprovalState, ToolClass
from bioadapt_schemas.orchestration import AdhocToolGrant, new_tool_grant_id
from bioadapt_schemas.plan import PlanNode
from bioadapt_tool_registry import ToolSpec

from .deps import Session, SessionDep
from .errors import conflict, forbidden, invalid, not_found
from .paging import Page, page_params

router = APIRouter(tags=["tools"])


class AdhocGrantRequest(Contract):
    #: The ad-hoc gate whose approved decision this grant executes (§9.4).
    approval_id: str = Field(min_length=1)
    spec: ToolSpec
    #: A person's own words for the record; the gate's decision reason is used when this is left open.
    reason: Optional[str] = None
    #: The deployment's window is the ceiling; a request may only ask for less time.
    valid_for_minutes: Optional[int] = Field(None, ge=1)


def _grant_body(grant: AdhocToolGrant, *, live: bool) -> dict:
    return {**grant.model_dump(mode="json"), "live": live}


def _run_manifest(session: Session, run_id: str):
    try:
        return session.catalog.get_run(run_id)
    except LookupError:
        raise not_found("run", run_id) from None


def _gate(session: Session, approval_id: str):
    try:
        return session.catalog.get_approval(approval_id)
    except LookupError:
        raise not_found("approval", approval_id) from None


def _planned_node(session: Session, manifest, node_id: str) -> PlanNode:
    plan = session.catalog.get_plan(manifest.plan_id, manifest.plan_version)
    for node in plan.nodes:
        if node.node_id == node_id:
            return node
    raise not_found("node", node_id)


def _spec_violations(session: Session, spec: ToolSpec) -> list[str]:
    """Section 9.6: the cage is checked again on the way in, not only on the way out.

    The engine re-runs these over the bytes the row carries, so a grant must not be issuable for a spec
    the engine would refuse - otherwise a person signs a permission that buys nothing, and the refusal
    arrives as a preflight error instead of a rejected request.
    """
    found = []
    if spec.tool_class is not ToolClass.PROJECT_ADHOC:
        found.append(f"{spec.tool_name} is {spec.tool_class}; only an ad-hoc submission needs a grant")
    found.extend(spec.admission_violations())
    found.extend(session.state.adhoc_limits.violations(spec))
    return found


@router.post("/projects/{project_id}/runs/{run_id}/nodes/{node_id}/adhoc-grants", status_code=201)
def issue_adhoc_grant(run_id: str, node_id: str, body: AdhocGrantRequest, session: SessionDep):
    """Issue the one execution a PI's decision over an ad-hoc gate is worth (§9.6)."""
    manifest = _run_manifest(session, run_id)
    gate = _gate(session, body.approval_id)
    if gate.kind is not ApprovalKind.ADHOC_TOOL:
        raise invalid(
            "approval_id",
            f"gate {gate.approval_id} is a {gate.kind} gate; an ad-hoc grant is issued only over the gate "
            "that asks for this node's ad-hoc tool",
        )
    if (gate.run_id, gate.node_id) != (run_id, node_id):
        raise invalid(
            "approval_id",
            f"gate {gate.approval_id} is about {gate.run_id}/{gate.node_id}, not {run_id}/{node_id}",
        )
    grant_id = new_tool_grant_id(gate.approval_id, body.spec.tool_name, body.spec.tool_version, node_id)
    #: The submitter is named by the gate, so separation of duties is decided against the record rather
    #: than against whoever asserted it in the body (§11.6).
    session.enforce(
        Action.TOOL_GRANT_ADHOC,
        grant_id,
        "tool_grant",
        requesting_subject_id=gate.requested_by,
        attributes={"kind": str(gate.kind), "run_id": run_id, "node_id": node_id, "gate_state": str(gate.state)},
    )
    if gate.state is not ApprovalState.APPROVED:
        raise conflict(
            f"gate {gate.approval_id} is {gate.state}; a grant follows a decision, it does not stand in for one"
        )
    if gate.decided_by != session.actor.subject_id:
        raise forbidden(
            [
                f"this grant must be issued by {gate.decided_by}, the person who decided the gate; "
                "a grant is the execution of one named decision, not of any PI's agreement with it"
            ],
            grant_id,
        )
    node = _planned_node(session, manifest, node_id)
    if node.tool_class is not ToolClass.PROJECT_ADHOC:
        raise invalid(
            "node_id",
            f"plan node {node_id} runs {node.tool_class} tool {node.tool_name}; an ad-hoc grant covers only "
            "the plan's ad-hoc nodes",
        )
    if (node.tool_name, node.tool_version) != (body.spec.tool_name, body.spec.tool_version):
        raise invalid(
            "spec",
            f"node {node_id} is planned as {node.tool_name}@{node.tool_version}, not "
            f"{body.spec.tool_name}@{body.spec.tool_version}",
        )
    violations = _spec_violations(session, body.spec)
    if violations:
        raise invalid("spec", "; ".join(violations))

    spec_dump = body.spec.model_dump(mode="json")
    spec_sha256 = sha256_of_json(spec_dump)
    existing = session.catalog.grant_of_approval(gate.approval_id)
    if existing is not None:
        if existing.spec_sha256 == spec_sha256:
            #: The same bytes under the same gate are the same permission: the row that stands is the answer.
            return JSONResponse(
                status_code=200,
                content=_grant_body(existing, live=existing.is_live(session.clock())),
                headers={"Idempotency-Replayed": "true"},
            )
        raise conflict(
            f"gate {gate.approval_id} already granted {existing.tool_name}@{existing.tool_version} under "
            "a different spec; a second submission needs a second decision"
        )

    approved_at = session.clock()
    window = session.state.adhoc_grant_ttl
    if body.valid_for_minutes is not None:
        window = min(window, timedelta(minutes=body.valid_for_minutes))
    grant = AdhocToolGrant(
        grant_id=grant_id,
        project_id=session.project_id,
        run_id=run_id,
        node_id=node_id,
        plan_id=manifest.plan_id,
        plan_version=manifest.plan_version,
        approval_id=gate.approval_id,
        tool_name=body.spec.tool_name,
        tool_version=body.spec.tool_version,
        spec=spec_dump,
        spec_sha256=spec_sha256,
        requested_by=gate.requested_by,
        approver_id=session.actor.subject_id,
        reason=body.reason or gate.decision_reason or "approved as submitted",
        approved_at=approved_at,
        expires_at=approved_at + window,
    )
    session.catalog.record_tool_grant(grant)
    #: The grant is a second row about the same decision, so it is audited as its own fact: the journal
    #: answers who opened the gate, this answers who turned that opening into a permission (§13.1).
    session.audit(
        "tool:grant_adhoc",
        grant_id,
        details={
            "approval_id": gate.approval_id,
            "run_id": run_id,
            "node_id": node_id,
            "tool": f"{grant.tool_name}@{grant.tool_version}",
            "spec_sha256": spec_sha256,
            "requested_by": grant.requested_by,
            "expires_at": grant.expires_at.isoformat(),
        },
    )
    return _grant_body(grant, live=True)


@router.get("/projects/{project_id}/runs/{run_id}/adhoc-grants")
def read_adhoc_grants(
    run_id: str,
    session: SessionDep,
    page: Page = Depends(page_params),
    live_only: bool = Query(False, description="grants that are unspent and still inside their window"),
) -> dict:
    """The grant ledger for one run: which submissions were made executable, and whether they still are."""
    _run_manifest(session, run_id)
    session.enforce(Action.APPROVAL_READ, f"{run_id}:adhoc_grant_list", "tool_grant_collection")
    now = session.clock()
    grants = session.catalog.list_tool_grants(
        run_id=run_id, live_only=live_only, limit=page.limit, offset=page.offset
    )
    return page.envelope([_grant_body(grant, live=grant.is_live(now)) for grant in grants])


@router.get("/projects/{project_id}/adhoc-grants/{grant_id}")
def read_adhoc_grant(grant_id: str, session: SessionDep) -> dict:
    session.enforce(Action.APPROVAL_READ, grant_id, "tool_grant")
    try:
        grant = session.catalog.get_tool_grant(grant_id)
    except LookupError:
        raise not_found("adhoc_grant", grant_id) from None
    return _grant_body(grant, live=grant.is_live(session.clock()))
