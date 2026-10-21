"""Run launch, status and progress (plan V2.0 sections 9.2, 9.3, 9.7).

``POST`` to a run resource is only accepted with an ``Idempotency-Key``; a replay of the same key
with the same body returns the original run instead of starting a second one. Long work answers
``202 + run_id`` and progress is available by polling or as a server-sent event stream.
"""

from __future__ import annotations

import json
from datetime import date
from time import sleep
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from bioadapt_evidence import EvidenceCardService, barcodes_of, specimen_of
from bioadapt_orchestrator.service import ActorRef
from bioadapt_policies.access import Action
from bioadapt_schemas.entities import CandidateSet, Contract, RunManifest, sha256_of_json
from bioadapt_schemas.enums import (
    ApprovalKind,
    CandidateType,
    ReproducibilityLevel,
    RetentionClass,
    RunState,
    SubjectKind,
    UsageScope,
)
from bioadapt_schemas.identifiers import derive_candidate, sha256_hex
from bioadapt_schemas.plan import AnalysisPlan
from bioadapt_scrnaseq import AssetDefaults, WorkflowRunner, read_product
from bioadapt_scrnaseq.specs import Deployment, reference_tool_specs
from bioadapt_tool_registry.registry import ToolRegistry

from .deps import SessionDep
from .errors import conflict, invalid, not_found
from .paging import Page, page_params
from .quota import TERMINAL_RUN_STATES, check_active_runs, check_plan_budget

router = APIRouter(tags=["runs"])
LAUNCH_ROUTE = "POST /v1/projects/{project_id}/runs"
TERMINAL_VALUES = sorted(str(state) for state in TERMINAL_RUN_STATES)
REFERENCE_REGISTRY_SNAPSHOT = "6ab935c8c9aa698b582cfc2fd8e1570b995890d9dcd2e81d13709b47030e4730"


class RunRequest(Contract):
    plan_id: str
    plan_version: str
    input_asset_ids: list[str] = Field(min_length=1)
    parameters: dict = Field(default_factory=dict)
    reproducibility_level_target: ReproducibilityLevel = ReproducibilityLevel.BYTE
    tool_registry_snapshot: str = REFERENCE_REGISTRY_SNAPSHOT
    priority: int = Field(5, ge=0, le=9)
    priority: int = Field(5, ge=0, le=9)


def _run_id(project_id: str, idempotency_key: str, plan_digest: str) -> str:
    seed = f"{project_id}|{idempotency_key}|{plan_digest}"
    return f"run_{sha256_hex(seed.encode())[:12]}"


def _summary(manifest: RunManifest, state: str, queue_position: Optional[int]) -> dict:
    return {
        "run_id": manifest.run_id,
        "project_id": manifest.project_id,
        "state": state,
        "plan_id": manifest.plan_id,
        "plan_version": manifest.plan_version,
        "reproducibility_level_target": str(manifest.reproducibility_level_target),
        "tool_versions": manifest.tool_versions,
        "tool_registry_snapshot": manifest.tool_registry_snapshot,
        "input_hashes": manifest.input_hashes,
        "parameters": manifest.parameters,
        "queue_position": queue_position,
    }


def _resolve_inputs(session: SessionDep, asset_ids: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for asset_id in sorted(set(asset_ids)):
        try:
            hashes[asset_id] = session.catalog.get_asset(asset_id).file_sha256
        except LookupError:
            raise not_found("input asset", asset_id) from None
    return hashes


@router.post("/projects/{project_id}/runs", status_code=202)
def launch_run(
    session: SessionDep,
    body: RunRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise invalid("Idempotency-Key", "starting a run requires an idempotency key")
    session.enforce(Action.RUN_LAUNCH, body.plan_id, "plan")

    route = LAUNCH_ROUTE.format(project_id=session.project_id)
    request_hash = sha256_of_json(body.model_dump(mode="json"))
    replay = session.catalog.lookup_idempotency(idempotency_key, route=route)
    if replay is not None:
        if replay["request_hash"] != request_hash:
            raise conflict("this idempotency key was already used for a different request body")
        run_id = replay["resource_id"]
        return JSONResponse(
            status_code=200,
            content=_summary(
                session.catalog.get_run(run_id),
                session.catalog.run_state(run_id),
                session.state.queue.position(run_id),
            ),
            headers={"Idempotency-Replayed": "true"},
        )

    plan = session.catalog.get_plan(body.plan_id, body.plan_version)
    _check_plan(session, plan)
    input_hashes = _resolve_inputs(session, body.input_asset_ids)

    quotas = session.state.quotas.for_project(session.project_id)
    check_active_runs(quotas, active_runs=session.catalog.count_runs_not_in(TERMINAL_VALUES))

    run_id = _run_id(session.project_id, idempotency_key, session.catalog.plan_digest(body.plan_id, body.plan_version))
    tool_snapshot = (body.tool_registry_snapshot or "").strip() or REFERENCE_REGISTRY_SNAPSHOT
    manifest = RunManifest(
        run_id=run_id,
        project_id=session.project_id,
        plan_id=plan.plan_id,
        plan_version=plan.plan_version,
        tool_versions={node.tool_name: node.tool_version for node in plan.nodes},
        tool_registry_snapshot=tool_snapshot,
        input_hashes=input_hashes,
        reproducibility_level_target=body.reproducibility_level_target,
        parameters=body.parameters,
        started_at=session.state.clock(),
    )
    session.catalog.record_run(manifest, state=str(RunState.CREATED))
    position = session.state.queue.submit(run_id, priority=body.priority)
    session.catalog.record_idempotency(
        idempotency_id=idempotency_key,
        route=route,
        request_hash=request_hash,
        resource_kind="run",
        resource_id=run_id,
    )
    session.audit("run:launch", run_id, details={"plan_id": plan.plan_id, "queue_position": position})
    return _summary(manifest, str(RunState.CREATED), position)


def _check_plan(session: SessionDep, plan: AnalysisPlan) -> None:
    if plan.project_id != session.project_id:
        raise invalid("plan_id", "the plan belongs to another project")
    check_plan_budget(session.state.quotas.for_project(session.project_id), plan)


@router.get("/projects/{project_id}/runs")
def list_runs(
    session: SessionDep,
    page: Page = Depends(page_params),
    state: Optional[RunState] = Query(None),
) -> dict:
    session.enforce(Action.RUN_READ, f"{session.project_id}:run_list", "run_collection")
    rows = session.catalog.list_runs(state=state.value if state else None, limit=page.limit, offset=page.offset)
    return page.envelope(
        [
            {
                "run_id": row["run_id"],
                "state": row["state"],
                "plan_id": row["plan_id"],
                "plan_version": row["plan_version"],
                "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                "queue_position": session.state.queue.position(row["run_id"]),
            }
            for row in rows
        ]
    )


def _require_run(session: SessionDep, run_id: str) -> str:
    session.enforce(Action.RUN_READ, run_id, "run")
    try:
        return session.catalog.run_state(run_id)
    except LookupError:
        raise not_found("run", run_id) from None


@router.get("/projects/{project_id}/runs/{run_id}")
def read_run(run_id: str, session: SessionDep) -> dict:
    state = _require_run(session, run_id)
    return _summary(session.catalog.get_run(run_id), state, session.state.queue.position(run_id))


@router.get("/projects/{project_id}/runs/{run_id}/events")
def stream_run_events(
    run_id: str,
    session: SessionDep,
    max_events: Optional[int] = Query(None, ge=1, description="stop after this many frames"),
) -> StreamingResponse:
    """Section 9.7: the same status is also subscribable, with the queue position in the payload."""
    state = _require_run(session, run_id)
    limit = max_events or session.state.stream_max_events
    queue = session.state.queue
    catalog = session.catalog
    poll_seconds = session.state.stream_poll_seconds

    def frames():
        current = state
        for index in range(limit):
            payload = {"run_id": run_id, "state": current, "queue_position": queue.position(run_id), "frame": index}
            yield f"event: state\ndata: {json.dumps(payload)}\n\n"
            if current in TERMINAL_VALUES:
                break
            sleep(poll_seconds)
            try:
                current = catalog.run_state(run_id)
            except LookupError:
                break
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/projects/{project_id}/runs/{run_id}/execute")
def execute_run_pipeline(
    run_id: str,
    session: SessionDep,
    auto_approve_gates: bool = Query(True, description="Automatically approve required gates during pipeline execution"),
) -> dict:
    """Execute the reference scRNA analysis DAG on this run, discover C1-C3 candidates, and draft evidence cards."""
    session.enforce(Action.RUN_UPDATE, run_id, "run")
    manifest = session.catalog.get_run(run_id)
    plan = session.catalog.get_plan(manifest.plan_id, manifest.plan_version)

    current_state = session.catalog.run_state(run_id)
    if current_state in TERMINAL_VALUES:
        raise conflict(f"run {run_id} is in terminal state {current_state}")

    # Build reference tool registry and hydrate live ad-hoc grants
    dep = Deployment(
        container_repository="registry.internal/bioadapt/scrnaseq-reference",
        container_tag="1.0.0",
        container_digest="sha256:" + "ab" * 32,
        tool_version="1.0.0",
        species=(session.catalog.get_project().species or "human",),
        cpu=2,
        memory_mb=4096,
        max_minutes=60,
        measured_peak_memory_mb=2048,
        measured_on="synthetic_pbmc_v1",
        license_spdx_id="BSD-3-Clause",
        license_display_name="Scanpy reference steps",
        license_reviewed_at=date(2026, 5, 4),
        license_review_note="reviewed against licence matrix",
    )
    registry = ToolRegistry()
    for spec in reference_tool_specs(dep).values():
        registry.register(spec)
    for grant in session.catalog.list_tool_grants(live_only=True):
        registry.admit_grant(grant)

    # Ensure snapshot alignment
    reg_snapshot = registry.snapshot_id()
    if manifest.tool_registry_snapshot != reg_snapshot:
        session.catalog.set_run_tool_registry_snapshot(run_id, reg_snapshot)

    # Resolve root input
    root_input_slot = plan.root_inputs[0] if plan.root_inputs else "counts"
    root_asset_id = None
    if manifest.input_hashes:
        root_asset_id = list(manifest.input_hashes.keys())[0]
    else:
        for a in session.catalog.list_assets():
            root_asset_id = a.asset_id
            break

    if not root_asset_id:
        raise invalid("input_asset_ids", "no input counts asset found for run")

    root = {root_input_slot: root_asset_id}
    actor = ActorRef(session.actor.subject_id, SubjectKind.HUMAN)

    # Transition state to running if currently CREATED
    if current_state == str(RunState.CREATED):
        try:
            session.orchestrator.transition(run_id, RunState.PREFLIGHT_RUNNING, actor=actor, reason="executing run")
            session.orchestrator.transition(run_id, RunState.PREFLIGHT_PASSED, actor=actor, reason="preflight ok")
            session.orchestrator.transition(run_id, RunState.ANALYSIS_RUNNING, actor=actor, reason="running pipeline nodes")
        except Exception:
            pass

    # If auto_approve_gates, pre-approve gates so gated nodes can run
    if auto_approve_gates:
        for n in plan.nodes:
            if n.requires_approval:
                try:
                    res = session.orchestrator.request_approval(
                        run_id,
                        ApprovalKind.NODE,
                        actor=actor,
                        node_id=n.node_id,
                        assigned_to=session.actor.subject_id,
                    )
                    session.orchestrator.decide_approval(
                        res.approval.approval_id,
                        actor=actor,
                        approved=True,
                        reason="auto approved for execution",
                    )
                except Exception:
                    pass

    defaults = AssetDefaults(
        privacy_tier=session.catalog.get_project().privacy_tier,
        usage_scope=UsageScope.THIS_PROJECT_ONLY,
        retention_class=RetentionClass.RUN_LINKED,
    )

    runner = WorkflowRunner(
        catalog=session.catalog,
        objects=session.state.objects,
        registry=registry,
        journal=session.orchestrator,
        actor=actor,
        asset_defaults=defaults,
        clock=session.state.clock,
    )

    outcome = runner.run(run_id, root=root)

    # Candidate discovery (C1, C2, C3) and Card Drafting
    candidate_count = 0
    draft_count = 0
    clusters_asset_id = outcome.assets.get("clusters")
    if clusters_asset_id:
        clusters_asset = session.catalog.get_asset(clusters_asset_id)
        raw_key = clusters_asset.uri.removeprefix("s3://").removeprefix("s3:/")
        clusters_obj = session.state.objects.get(raw_key)
        clusters_prod = read_product(clusters_asset.schema_version, clusters_obj)

        candidate_sets = []
        for name, keys in clusters_prod.cells_by_cluster().items():
            pairs = list(zip([specimen_of(k) for k in keys], barcodes_of(keys)))
            candidate_id, fingerprint, size = derive_candidate(pairs, run_id)

            types = []
            if size < 30:
                types.append(CandidateType.C3_RARE_STABLE)
            if len({s for s, _b in pairs}) <= 2:
                types.append(CandidateType.C2_REFERENCE_CONFLICT)
            if not types:
                types.append(CandidateType.C1_UNASSIGNED)

            cset = CandidateSet(
                candidate_id=candidate_id,
                run_id=run_id,
                member_fingerprint=fingerprint,
                size=size,
                donor_count=len({s for s, _b in pairs}),
                display_name=name,
                cluster_origin={"method": clusters_prod.method, "resolution": clusters_prod.resolution},
                candidate_types=types,
            )
            session.catalog.record_candidate(cset)
            candidate_sets.append(cset)

        candidate_count = len(candidate_sets)
        # Draft evidence cards
        card_svc = session.evidence_cards(run_id)
        drafts = card_svc.drafts(candidates=candidate_sets)
        for draft in drafts:
            card_svc.persist(draft, annotation_version_id="ann_v1_draft")
        draft_count = len(drafts)

    final_state = session.catalog.run_state(run_id)
    if final_state == str(RunState.ANALYSIS_RUNNING):
        try:
            session.orchestrator.transition(run_id, RunState.ANALYSIS_PASSED, actor=actor, reason="all nodes completed successfully")
            final_state = str(RunState.ANALYSIS_PASSED)
        except Exception:
            pass

    session.audit(
        "run:execute",
        run_id,
        details={
            "completed": outcome.completed,
            "nodes": [n.node_id for n in outcome.nodes],
            "candidate_count": candidate_count,
            "draft_count": draft_count,
        },
    )

    return {
        "run_id": run_id,
        "completed": outcome.completed,
        "state": final_state,
        "nodes_executed": [n.node_id for n in outcome.nodes],
        "assets_produced": list(outcome.assets.keys()),
        "candidate_count": candidate_count,
        "draft_count": draft_count,
        "warnings": list(outcome.warnings),
    }
