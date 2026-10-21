"""Run launch, status and progress (plan V2.0 sections 9.2, 9.3, 9.7).

``POST`` to a run resource is only accepted with an ``Idempotency-Key``; a replay of the same key
with the same body returns the original run instead of starting a second one. Long work answers
``202 + run_id`` and progress is available by polling or as a server-sent event stream.
"""

from __future__ import annotations

import json
from time import sleep
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from bioadapt_policies.access import Action
from bioadapt_schemas.entities import Contract, RunManifest, sha256_of_json
from bioadapt_schemas.enums import ReproducibilityLevel, RunState
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_schemas.plan import AnalysisPlan

from .deps import SessionDep
from .errors import conflict, invalid, not_found
from .paging import Page, page_params
from .quota import TERMINAL_RUN_STATES, check_active_runs, check_plan_budget

router = APIRouter(tags=["runs"])
LAUNCH_ROUTE = "POST /v1/projects/{project_id}/runs"
TERMINAL_VALUES = sorted(str(state) for state in TERMINAL_RUN_STATES)


class RunRequest(Contract):
    plan_id: str
    plan_version: str
    input_asset_ids: list[str] = Field(min_length=1)
    parameters: dict = Field(default_factory=dict)
    reproducibility_level_target: ReproducibilityLevel = ReproducibilityLevel.BYTE
    tool_registry_snapshot: str
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
    manifest = RunManifest(
        run_id=run_id,
        project_id=session.project_id,
        plan_id=plan.plan_id,
        plan_version=plan.plan_version,
        tool_versions={node.tool_name: node.tool_version for node in plan.nodes},
        tool_registry_snapshot=body.tool_registry_snapshot,
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
