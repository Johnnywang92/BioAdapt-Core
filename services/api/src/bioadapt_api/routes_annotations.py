"""Annotation versions and membership review (plan V2.0 sections 5.7, 9.3, 11.6).

A label set is immutable: no route rewrites an annotation version in place. A review records a
judgement, and labels only change when a replacement version is recorded and named as the successor -
so "the model relabelled the cells" can never become a silent edit of stored truth.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from bioadapt_policies.access import Action
from bioadapt_schemas.entities import AnnotationVersion, Contract, ReviewEvent
from bioadapt_schemas.enums import AnnotationStatus, LabelLevel, ReviewDecision, ReviewScope
from bioadapt_schemas.identifiers import sha256_hex

from .deps import SessionDep
from .errors import conflict, invalid, not_found
from .paging import Page, page_params
from .routes_data import readable_asset
from .routes_findings import new_review_id

router = APIRouter(tags=["annotations"])

ANNOTATION_OBJECT_TYPE = "annotation_version"
RECORDED_DECISIONS = (ReviewDecision.APPROVE, ReviewDecision.RETURN_FOR_ANALYSIS)


class AnnotationRequest(Contract):
    run_id: str
    asset_id: str
    label_column: str
    level: LabelLevel
    supersedes_version_id: Optional[str] = None


class MembershipReviewRequest(Contract):
    """Section 9.3: annotation reviews keep the scope and supersede semantics of a card review."""

    decision: ReviewDecision
    rationale: str
    supersedes_review_id: Optional[str] = None


def _version_id(run_id: str, label_column: str, asset_id: str) -> str:
    return f"av_{sha256_hex(f'{run_id}|{label_column}|{asset_id}'.encode())[:12]}"


def _get_version(session: SessionDep, version_id: str) -> AnnotationVersion:
    try:
        return session.catalog.get_annotation_version(version_id)
    except LookupError:
        raise not_found("annotation version", version_id) from None


@router.post("/projects/{project_id}/annotations", status_code=201)
def record_annotation(body: AnnotationRequest, session: SessionDep) -> dict:
    """Section 5.7: only the index row is written here - the labels themselves stay in the object plane."""
    session.enforce(Action.ASSET_WRITE, body.asset_id, "asset")
    try:
        session.catalog.get_run(body.run_id)
    except LookupError:
        raise not_found("run", body.run_id) from None
    readable_asset(session, body.asset_id)
    active = session.catalog.effective_annotation_version(body.run_id, body.label_column)
    if active is not None and body.supersedes_version_id != active.version_id:
        raise conflict(
            f"run {body.run_id} already has an active {body.label_column} version {active.version_id}; "
            "record the replacement and name it in supersedes_version_id",
            field="supersedes_version_id",
        )
    if active is None and body.supersedes_version_id is not None:
        raise not_found("active annotation version", body.supersedes_version_id)
    version = AnnotationVersion(
        version_id=_version_id(body.run_id, body.label_column, body.asset_id),
        run_id=body.run_id,
        asset_id=body.asset_id,
        label_column=body.label_column,
        level=body.level,
        created_by=session.actor.subject_id,
        status=AnnotationStatus.ACTIVE,
    )
    session.catalog.record_annotation_version(version, supersedes_version_id=body.supersedes_version_id)
    session.audit(
        "annotation:record",
        version.version_id,
        details={
            "run_id": body.run_id,
            "label_column": body.label_column,
            "asset_id": body.asset_id,
            "supersedes_version_id": body.supersedes_version_id,
        },
    )
    return version.model_dump(mode="json")


@router.get("/projects/{project_id}/annotations/{version_id}")
def read_annotation(version_id: str, session: SessionDep) -> dict:
    session.enforce(Action.ASSET_READ, version_id, ANNOTATION_OBJECT_TYPE)
    version = _get_version(session, version_id)
    return {
        **version.model_dump(mode="json"),
        "is_active": version.status is AnnotationStatus.ACTIVE,
    }


@router.get("/projects/{project_id}/annotations")
def list_annotations(
    session: SessionDep,
    page: Page = Depends(page_params),
    run_id: str = Query(...),
    label_column: str = Query(...),
) -> dict:
    session.enforce(Action.ASSET_READ, f"{session.project_id}:annotation_list", "annotation_collection")
    chain = session.catalog.annotation_chain(run_id, label_column)
    window = chain[page.offset : page.offset + page.limit]
    return page.envelope([version.model_dump(mode="json") for version in window])


@router.post("/projects/{project_id}/annotations/{version_id}/review")
def review_annotation(version_id: str, body: MembershipReviewRequest, session: SessionDep) -> dict:
    """Section 11.6: a review is a human record; retiring labels is the replacement version's job."""
    session.enforce(Action.CARD_REVIEW, version_id, ANNOTATION_OBJECT_TYPE)
    version = _get_version(session, version_id)
    if body.decision not in RECORDED_DECISIONS:
        raise invalid(
            "decision",
            "an annotation review approves or returns the label set; retiring it means recording a "
            "replacement version that names this one in supersedes_version_id",
        )
    event = ReviewEvent(
        review_id=new_review_id(),
        object_id=version_id,
        object_type=ANNOTATION_OBJECT_TYPE,
        scope=ReviewScope.MEMBERSHIP,
        reviewer_id=session.actor.subject_id,
        decision=body.decision,
        rationale=body.rationale,
        timestamp=session.state.clock(),
        supersedes_review_id=body.supersedes_review_id,
    )
    session.catalog.append_review(event)
    session.audit(
        f"annotation:review:{body.decision}",
        version_id,
        details={"review_id": event.review_id, "rationale": event.rationale},
    )
    return {
        "review_id": event.review_id,
        "version_id": version_id,
        "status": str(version.status),
        "superseded_by": version.superseded_by,
    }


@router.get("/projects/{project_id}/annotations/{version_id}/reviews")
def list_annotation_reviews(version_id: str, session: SessionDep) -> dict:
    session.enforce(Action.ASSET_READ, version_id, ANNOTATION_OBJECT_TYPE)
    _get_version(session, version_id)
    events = session.catalog.reviews(version_id)
    return {"version_id": version_id, "reviews": [event.model_dump(mode="json") for event in events]}
