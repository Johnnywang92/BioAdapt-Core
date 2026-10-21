"""Projects, plans and assets (plan V2.0 sections 5.8-5.9, 9.3, 9.7, 11.2).

Client-side direct upload is the only write path for object bytes: the API hands out a short-lived
PUT signature for a key it derived itself, and the registration is accepted only after the object
plane reports the digest and size the client declared.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from bioadapt_policies.access import MAX_PRESIGNED_TTL_SECONDS, Action, presigned_url_ttl
from bioadapt_schemas.entities import Contract, DataAsset, Project
from bioadapt_schemas.enums import RetentionClass
from bioadapt_schemas.plan import AnalysisPlan
from bioadapt_schemas.thresholds import Thresholds
from bioadapt_storage.objectstore import SignedUrl, issue_signed_url, object_key

from .deps import ClaimsDep, SessionDep, StateDep, open_session, request_trace_id
from .errors import conflict, invalid
from .paging import Page, page_params
from .quota import check_storage

router = APIRouter(tags=["data"])


def object_key_for(project_id: str, asset: DataAsset) -> str:
    return object_key(
        project_id=project_id,
        asset_id=asset.asset_id,
        file_sha256=asset.file_sha256,
        retention_class=asset.retention_class,
        extension=asset.format,
    )


def sign_url(
    session: SessionDep, key: str, method: str, ttl_seconds: int, *, authorized_project_id: str
) -> SignedUrl:
    """The prefix handed out is the one the caller was authorized for, not whatever the key says."""
    now = session.state.clock()
    signature = session.state.signer.sign(key=key, method=method, expires_at=now)
    return issue_signed_url(
        key,
        project_id=authorized_project_id,
        now=now,
        signature=signature,
        ttl_seconds=presigned_url_ttl(ttl_seconds),
        method=method,
    )


def _declared_in_path(session: SessionDep, asset: DataAsset) -> None:
    if asset.project_id != session.project_id:
        raise invalid("project_id", "the asset declares a different project than the request path")


@router.post("/projects", status_code=201)
def create_project(body: Project, request: Request, state: StateDep, claims: ClaimsDep) -> dict:
    session = open_session(state, claims, body.project_id, request_trace_id(request))
    session.enforce(Action.PROJECT_WRITE, body.project_id, "project")
    #: Section 1.5: registration may carry the cuts a project intends to judge with, but attesting them is
    #: its own human act with its own second factor. Left open, a project could register itself straight
    #: past the conclusion-exit gate in the request that created it.
    if body.thresholds.confirmed:
        raise invalid(
            "thresholds.confirmed",
            "a registration cannot attest its own cut set; POST /v1/projects/{project_id}/thresholds/confirm does",
        )
    session.catalog.create_project(body)
    session.audit("project:create", body.project_id, details={"privacy_tier": str(body.privacy_tier)})
    return {"project_id": body.project_id, "privacy_tier": str(body.privacy_tier)}


@router.get("/projects/{project_id}")
def read_project(session: SessionDep) -> dict:
    session.enforce(Action.PROJECT_READ, session.project_id, "project")
    return session.catalog.get_project().model_dump(mode="json")


#: Appendix C1: a confirmation re-states the whole cut set, so no placeholder can be attested by being
#: left out of the request. ``confirmed`` is the one field the caller is not allowed to state.
CONFIRMABLE_CUTS = tuple(name for name in Thresholds.model_fields if name != "confirmed")


class ThresholdConfirmation(Contract):
    thresholds: Thresholds
    rationale: str = Field(min_length=1)


@router.post("/projects/{project_id}/thresholds/confirm")
def confirm_thresholds(body: ThresholdConfirmation, session: SessionDep) -> dict:
    """Section 1.5: the stage-0 placeholder cuts are replaced by the numbers the project measured.

    Until this happens the flag stays false and the conclusion exit is shut (§14.4 locks the thresholds
    after the stage-0 baseline, so a run may be computed and drafted against placeholders - a finding
    may not be signed off or exported on them).
    """
    session.enforce(Action.THRESHOLDS_CONFIRM, f"{session.project_id}:thresholds", "threshold_set")
    if body.thresholds.confirmed:
        raise invalid("thresholds.confirmed", "the platform sets this from the decision below, not the caller")
    missing = [name for name in CONFIRMABLE_CUTS if name not in body.thresholds.model_fields_set]
    if missing:
        raise invalid("thresholds", f"a confirmation has to state every cut, missing {missing}")
    if not body.thresholds.version.strip():
        raise invalid("thresholds.version", "the measured cut set has to be named, the products cite that name")
    current = session.catalog.get_project().thresholds
    if current.version == body.thresholds.version:
        raise conflict(
            f"threshold set {body.thresholds.version!r} is already the one in force", field="thresholds.version"
        )
    confirmed = body.thresholds.model_copy(update={"confirmed": True})
    project = session.catalog.update_project_thresholds(confirmed)
    session.audit(
        "thresholds:confirm",
        f"{session.project_id}:thresholds",
        details={
            "from_version": current.version,
            "to_version": project.thresholds.version,
            "rationale": body.rationale,
        },
    )
    return {"project_id": session.project_id, "thresholds": project.thresholds.model_dump(mode="json")}


@router.post("/projects/{project_id}/plans", status_code=201)
def record_plan(body: AnalysisPlan, session: SessionDep) -> dict:
    if body.project_id != session.project_id:
        raise invalid("project_id", "the plan declares a different project than the request path")
    session.enforce(Action.RUN_LAUNCH, body.plan_id, "plan")
    digest = session.catalog.record_plan(body, created_by=session.actor.subject_id)
    session.audit("plan:record", body.plan_id, details={"plan_version": body.plan_version, "plan_sha256": digest})
    return {"plan_id": body.plan_id, "plan_version": body.plan_version, "plan_sha256": digest}


@router.post("/projects/{project_id}/assets")
def initiate_upload(
    body: DataAsset,
    session: SessionDep,
    ttl_seconds: int = Query(MAX_PRESIGNED_TTL_SECONDS),
) -> dict:
    _declared_in_path(session, body)
    session.enforce(
        Action.ASSET_WRITE, body.asset_id, "asset", privacy_tier=body.privacy_tier, usage_scope=body.usage_scope
    )
    quotas = session.state.quotas.for_project(session.project_id)
    check_storage(quotas, used_bytes=session.catalog.storage_bytes(), incoming_bytes=body.size_bytes)
    key = object_key_for(session.project_id, body)
    grant = sign_url(session, key, "PUT", ttl_seconds, authorized_project_id=session.project_id)
    session.audit("asset:upload_grant", body.asset_id, details={"key": key, "ttl_seconds": grant.ttl_seconds})
    return {
        "asset_id": body.asset_id,
        "key": key,
        "method": grant.method,
        "upload_url": grant.url,
        "expires_at": grant.expires_at.isoformat(),
        "ttl_seconds": grant.ttl_seconds,
    }


@router.post("/projects/{project_id}/assets/{asset_id}/complete", status_code=201)
def complete_upload(asset_id: str, body: DataAsset, session: SessionDep) -> dict:
    if body.asset_id != asset_id:
        raise invalid("asset_id", "the path and the body name different assets")
    _declared_in_path(session, body)
    session.enforce(
        Action.ASSET_WRITE, asset_id, "asset", privacy_tier=body.privacy_tier, usage_scope=body.usage_scope
    )
    key = object_key_for(session.project_id, body)
    head = session.state.objects.head(key)
    if head is None:
        raise conflict(f"nothing has been uploaded to {key}")
    if head.sha256 != body.file_sha256:
        raise conflict("the uploaded bytes do not match the declared file_sha256")
    if head.size_bytes != body.size_bytes:
        raise conflict(f"declared size {body.size_bytes} but the object holds {head.size_bytes} bytes")
    recorded = session.catalog.record_asset(body.model_copy(update={"uri": f"s3://{key}"}))
    session.audit("asset:record", recorded, details={"key": key, "file_sha256": head.sha256})
    return {"asset_id": recorded, "key": key, "uri": f"s3://{key}", "content_fingerprint": body.content_fingerprint}


@router.get("/projects/{project_id}/assets")
def list_assets(
    session: SessionDep,
    page: Page = Depends(page_params),
    retention_class: Optional[RetentionClass] = Query(None),
    run_id: Optional[str] = Query(None),
) -> dict:
    session.enforce(Action.ASSET_READ, f"{session.project_id}:asset_list", "asset_collection")
    assets = session.catalog.list_assets(
        retention_class=retention_class.value if retention_class else None,
        run_id=run_id,
        limit=page.limit,
        offset=page.offset,
    )
    return page.envelope([asset.model_dump(mode="json") for asset in assets])


def readable_asset(session: SessionDep, asset_id: str) -> DataAsset:
    """Section 5.8: another project's asset is reachable only when its usage scope allows reuse."""
    try:
        asset = session.catalog.get_asset(asset_id)
    except LookupError:
        shared = session.catalog.shared_asset(asset_id)
        if shared is None:
            raise
        asset = shared
    session.enforce(
        Action.ASSET_READ,
        asset_id,
        "asset",
        privacy_tier=asset.privacy_tier,
        usage_scope=asset.usage_scope,
        project_id=asset.project_id,
    )
    return asset


@router.get("/projects/{project_id}/assets/{asset_id}")
def read_asset(asset_id: str, session: SessionDep) -> dict:
    return readable_asset(session, asset_id).model_dump(mode="json")


@router.get("/projects/{project_id}/assets/{asset_id}/download")
def download_url(asset_id: str, session: SessionDep, ttl_seconds: int = Query(MAX_PRESIGNED_TTL_SECONDS)) -> dict:
    asset = readable_asset(session, asset_id)
    key = object_key_for(asset.project_id, asset)
    if session.state.objects.head(key) is None:
        raise conflict(f"no object is stored for asset {asset_id}")
    grant = sign_url(session, key, "GET", ttl_seconds, authorized_project_id=asset.project_id)
    return {
        "asset_id": asset_id,
        "key": key,
        "method": grant.method,
        "download_url": grant.url,
        "expires_at": grant.expires_at.isoformat(),
        "ttl_seconds": grant.ttl_seconds,
    }
