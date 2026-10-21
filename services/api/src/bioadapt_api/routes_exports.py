"""Async exports (plan V2.0 sections 9.3, 9.7, 11.2).

Pulling a large table out of the platform is a job, not a list request: the API accepts it with
``202 + export_id``, and a download URL only exists once a worker has written the artifact into the
object plane and pointed the job at it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse

from bioadapt_policies.access import MAX_PRESIGNED_TTL_SECONDS, Action
from bioadapt_schemas.entities import Contract, sha256_of_json
from bioadapt_schemas.identifiers import sha256_hex

from .deps import SessionDep
from .errors import conflict, invalid, not_found
from .paging import Page, page_params
from .routes_data import object_key_for, readable_asset, sign_url

router = APIRouter(tags=["exports"])

EXPORTS_PATH = "/v1/projects/{project_id}/exports"
EXPORT_ROUTE = "POST " + EXPORTS_PATH


class ExportKind(StrEnum):
    """Section 9.3: the four artefacts a delivery actually asks for."""

    TABLES = "tables"
    FIGURES = "figures"
    CODE_BUNDLE = "code_bundle"
    DELIVERY_ZIP = "delivery_zip"


class ExportStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"


class ExportRequest(Contract):
    kind: ExportKind
    filters: dict[str, Any] = {}


def _export_id(project_id: str, idempotency_key: str, request_hash: str) -> str:
    return f"exp_{sha256_hex(f'{project_id}|{idempotency_key}|{request_hash}'.encode())[:12]}"


def _view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def _get_export(session: SessionDep, export_id: str) -> dict[str, Any]:
    try:
        return session.catalog.get_export(export_id)
    except LookupError:
        raise not_found("export", export_id) from None


def _envelope(row: dict[str, Any], project_id: str) -> dict[str, Any]:
    return {
        "export_id": row["export_id"],
        "status": row["status"],
        "kind": row["kind"],
        "poll": f"{EXPORTS_PATH.format(project_id=project_id)}/{row['export_id']}",
    }


@router.post("/projects/{project_id}/exports", status_code=202)
def create_export(
    body: ExportRequest,
    session: SessionDep,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise invalid("Idempotency-Key", "queueing an export requires an idempotency key")
    project = session.catalog.get_project()
    #: Section 5.9/11.2: a strict-local project cannot export at all, whatever the caller's role.
    session.enforce(
        Action.ASSET_EXPORT,
        f"{session.project_id}:{body.kind}",
        "export",
        privacy_tier=project.privacy_tier,
        attributes={"kind": str(body.kind)},
    )
    #: Section 1.5: the export is where a conclusion leaves the platform, so it is the second exit - after
    #: sign-off - that refuses to be built from a cut set nobody measured.
    session.require_measured_cuts(Action.ASSET_EXPORT, f"{session.project_id}:{body.kind}")
    run_id = body.filters.get("run_id")
    if run_id is not None:
        try:
            session.catalog.run_state(run_id)
        except LookupError:
            raise not_found("run", str(run_id)) from None

    route = EXPORT_ROUTE.format(project_id=session.project_id)
    request_hash = sha256_of_json(body.model_dump(mode="json"))
    replay = session.catalog.lookup_idempotency(idempotency_key, route=route)
    if replay is not None:
        if replay["request_hash"] != request_hash:
            raise conflict("this idempotency key was already used for a different export request")
        return JSONResponse(
            status_code=200,
            content=_envelope(session.catalog.get_export(replay["resource_id"]), session.project_id),
            headers={"Idempotency-Replayed": "true"},
        )

    export_id = _export_id(session.project_id, idempotency_key, request_hash)
    session.catalog.create_export(
        export_id=export_id,
        kind=str(body.kind),
        filters=body.filters,
        requested_by=session.actor.subject_id,
    )
    session.catalog.record_idempotency(
        idempotency_id=idempotency_key,
        route=route,
        request_hash=request_hash,
        resource_kind="export",
        resource_id=export_id,
    )
    session.audit("export:queue", export_id, details={"kind": str(body.kind), "filters": body.filters})
    return _envelope(
        {"export_id": export_id, "status": str(ExportStatus.QUEUED), "kind": str(body.kind)}, session.project_id
    )


@router.get("/projects/{project_id}/exports")
def list_exports(
    session: SessionDep,
    page: Page = Depends(page_params),
    status: Optional[ExportStatus] = Query(None),
) -> dict:
    session.enforce(Action.ASSET_EXPORT, f"{session.project_id}:export_list", "export_collection")
    rows = session.catalog.list_exports(status=status.value if status else None, limit=page.limit, offset=page.offset)
    return page.envelope([_view(row) for row in rows])


@router.get("/projects/{project_id}/exports/{export_id}")
def read_export(export_id: str, session: SessionDep, ttl_seconds: int = Query(MAX_PRESIGNED_TTL_SECONDS)) -> dict:
    row = _get_export(session, export_id)
    session.enforce(Action.ASSET_READ, export_id, "export")
    view = _view(row)
    if row["status"] != str(ExportStatus.READY):
        return view
    if not row.get("artifact_asset_id"):
        raise invalid("artifact_asset_id", f"export {export_id} reports READY without an artefact")
    asset = readable_asset(session, row["artifact_asset_id"])
    key = object_key_for(asset.project_id, asset)
    grant = sign_url(session, key, "GET", ttl_seconds, authorized_project_id=asset.project_id)
    return {
        **view,
        "download_url": grant.url,
        "expires_at": grant.expires_at.isoformat(),
        "ttl_seconds": grant.ttl_seconds,
    }
