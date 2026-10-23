"""Ad-hoc tool grant routes (plan V2.0 sections 9.6, 11.6, 13.1).

Section 9.6 lets one project's unaudited code run once, inside a cage, when a PI approves that exact
submission. The platform had the cage and the registry-side permission, but no landing point for the
decision: an ``AdhocApproval`` was an object any process could build for itself, and its approver was
whatever that process claimed about itself. These routes are the landing point, and the chain they close
is 申请 → 决定 → 发放: a grant is only ever issued over an ``adhoc_tool`` gate a person has already
approved, by the very subject whose token is on this request.

Nothing here takes a caller's word for who approved, in which project, or when. The body carries a gate id
and nothing else about the tool: the bytes are read from the plan node the gate names, because the plan is
what pinned them and a grant that carries its own copy of the bytes would be a second place for them to
live. The row that lands records the authenticated actor as approver, the run's own project, the submitter
read off the gate, and two timestamps from the clock the deployment owns. ``extra="forbid"`` means an
``approver_id`` or ``expires_at`` in the request is a rejected request, not a suggested value.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError

from bioadapt_policies.access import Action
from bioadapt_schemas.entities import Contract, sha256_of_json
from bioadapt_schemas.enums import ApprovalKind, ApprovalState, ToolClass
from bioadapt_schemas.orchestration import AdhocToolGrant, new_tool_grant_id
from bioadapt_schemas.plan import PlanNode
from bioadapt_tool_registry import ToolSpec

from .deps import Session, SessionDep
from .errors import conflict, forbidden, invalid, not_found
from .paging import Page, page_params
from .toolset import reference_registry, registry_body, submission_violations

router = APIRouter(tags=["tools"])


class AdhocGrantRequest(Contract):
    #: The ad-hoc gate whose approved decision this grant executes (§9.4).
    approval_id: str = Field(min_length=1)
    #: A person's own words for the record; the gate's decision reason is used when this is left open.
    reason: Optional[str] = None
    #: The deployment's window is the ceiling; a request may only ask for less time.
    valid_for_minutes: Optional[int] = Field(None, ge=1)
    #: The bytes of the submission are not a request field: they live on the plan node the gate names
    #: (§9.6), so what a person signed and what the platform executes are the same bytes by construction
    #: rather than by a comparison somebody can forget to make.


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


def _spec_violations(session: Session, spec: ToolSpec, node: PlanNode) -> list[str]:
    """Section 9.6: the cage is checked again on the way in, not only on the way out.

    The engine re-runs these over the bytes the row carries, so a grant must not be issuable for a spec
    the engine would refuse - otherwise a person signs a permission that buys nothing, and the refusal
    arrives as a preflight error instead of a rejected request.
    """
    return submission_violations(spec, adhoc_limits=session.state.adhoc_limits, node=node)


def _submission_of(node: PlanNode) -> ToolSpec:
    """The bytes the plan pins for this node (§9.6).

    A plan that carries no submission has nothing to grant: the node names a tool the registry does not
    hold, and the refusal says so rather than letting a request supply bytes the record never saw.
    """
    if node.adhoc_spec is None:
        raise invalid(
            "node_id",
            f"node {node.node_id} carries no submission, so there are no bytes to grant: the plan has to "
            "state the ad-hoc tool it runs before a grant can execute it",
        )
    try:
        return ToolSpec(**node.adhoc_spec)
    except ValidationError as error:
        raise invalid(
            "node_id",
            f"node {node.node_id} carries a submission the tool contract does not accept: {error}",
        ) from None


@router.get("/projects/{project_id}/tools")
def tool_catalog(session: SessionDep) -> dict:
    """The tools this deployment can run for this project, and the snapshot a launch has to pin (§4.1, §9.4).

    A plan node names a tool by name and version and a run is approved against a registry snapshot, so
    both have to come from the registry the executor hydrates: a screen that offers a tool is offering
    one the engine holds, or it says nothing. An ad-hoc row appears only while its grant is still live.
    """
    session.enforce(Action.PROJECT_READ, f"{session.project_id}:tool_catalog", "tool_collection")
    return registry_body(
        reference_registry(session.catalog),
        now=session.clock(),
        adhoc_limits=session.state.adhoc_limits,
    )


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
    node = _planned_node(session, manifest, node_id)
    if node.tool_class is not ToolClass.PROJECT_ADHOC:
        raise invalid(
            "node_id",
            f"plan node {node_id} runs {node.tool_class} tool {node.tool_name}; an ad-hoc grant covers only "
            "the plan's ad-hoc nodes",
        )
    spec = _submission_of(node)
    violations = _spec_violations(session, spec, node)
    if violations:
        raise invalid("node_id", "; ".join(violations))
    grant_id = new_tool_grant_id(gate.approval_id, spec.tool_name, spec.tool_version, node_id)
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

    spec_dump = spec.model_dump(mode="json")
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
    is_solo = bool(getattr(session.actor, "solo_mode", False))
    grant = AdhocToolGrant(
        grant_id=grant_id,
        project_id=session.project_id,
        run_id=run_id,
        node_id=node_id,
        plan_id=manifest.plan_id,
        plan_version=manifest.plan_version,
        approval_id=gate.approval_id,
        tool_name=spec.tool_name,
        tool_version=spec.tool_version,
        spec=spec_dump,
        spec_sha256=spec_sha256,
        requested_by=gate.requested_by,
        approver_id=session.actor.subject_id,
        reason=body.reason or gate.decision_reason or "approved as submitted",
        approved_at=approved_at,
        expires_at=approved_at + window,
        solo_mode=is_solo,
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
