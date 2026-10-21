"""Candidates, evidence cards and human review (plan V2.0 sections 5.4, 5.6, 9.3, 11.6, 13.1).

Two invariants shape every route here: a cluster label is only an alias, never a resource key
(section 5.6), and a card write path always passes the status gate before it is persisted
(section 5.4) - so neither a model nor a reviewer can talk a card above its ceiling.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from bioadapt_evidence import CardDraft, build_queue
from bioadapt_policies.access import Action
from bioadapt_schemas.alignment import classify_alignment
from bioadapt_schemas.entities import CandidateSet, Contract, ReviewEvent
from bioadapt_schemas.enums import FieldSource, FinalStatus, ReviewDecision, ReviewScope, SignOff
from bioadapt_schemas.evidence import EvidenceCard, FieldProvenance
from bioadapt_schemas.gates import enforce_card_status

from .deps import SessionDep
from .errors import conflict, invalid, not_found
from .paging import Page, page_params

router = APIRouter(tags=["findings"])

CARD_SCOPE_DECISIONS = {
    ReviewDecision.MARK_CONFLICT: FinalStatus.CONFLICT,
    ReviewDecision.MARK_UNKNOWN: FinalStatus.UNKNOWN,
    ReviewDecision.REJECT: FinalStatus.REJECTED,
}


class ReviewRequest(Contract):
    """Section 9.3: reviews state their scope, what they supersede and what they carry over."""

    scope: ReviewScope
    decision: ReviewDecision
    rationale: str
    supersedes_review_id: Optional[str] = None
    carry_over_from_candidate_id: Optional[str] = None
    retraction_reason: Optional[str] = None
    new_value: Optional[dict[str, Any]] = None
    previous_value: Optional[dict[str, Any]] = None


class SignOffRequest(Contract):
    sign_off: SignOff
    reason: Optional[str] = None


class DraftRequest(Contract):
    """Section 9.3: drafting is addressed by the run whose products are read, never by a cluster label."""

    candidate_ids: Optional[list[str]] = None
    annotation_version_id: Optional[str] = None


def new_review_id() -> str:
    return f"rev_{uuid.uuid4().hex[:12]}"


def _get_candidate(session: SessionDep, candidate_id: str) -> CandidateSet:
    try:
        return session.catalog.get_candidate(candidate_id)
    except LookupError:
        raise not_found("candidate", candidate_id) from None


def _find_by_label(session: SessionDep, *, run_id: str, cluster_id: str) -> CandidateSet:
    try:
        return session.catalog.find_candidate(run_id=run_id, display_name=cluster_id)
    except LookupError:
        raise not_found("cluster label", f"{cluster_id} in run {run_id}") from None


def _card_for(session: SessionDep, candidate_id: str) -> EvidenceCard:
    try:
        return session.catalog.get_card_for_candidate(candidate_id)
    except LookupError:
        raise not_found("evidence card", candidate_id) from None


def _get_card(session: SessionDep, card_id: str) -> EvidenceCard:
    try:
        return session.catalog.get_card(card_id)
    except LookupError:
        raise not_found("evidence card", card_id) from None


def _write(session: SessionDep, card: EvidenceCard, updates: dict[str, Any]) -> EvidenceCard:
    """Section 5.4: rewrite through validation and the gate, and only persist what survives both."""
    revised = EvidenceCard.model_validate({**card.model_dump(), **updates})
    enforce_card_status(revised)
    session.catalog.update_card(revised)
    return revised


def _read_evidence(session: SessionDep, candidate: CandidateSet) -> dict:
    card = _card_for(session, candidate.candidate_id)
    session.enforce(Action.RUN_READ, card.card_id, "evidence_card")
    return {
        "candidate_id": candidate.candidate_id,
        "run_id": candidate.run_id,
        "cluster_id": candidate.display_name,
        "evidence_card": card.model_dump(mode="json"),
    }


@router.get("/projects/{project_id}/candidates/{candidate_id}/evidence")
def read_candidate_evidence(candidate_id: str, session: SessionDep) -> dict:
    session.enforce(Action.RUN_READ, candidate_id, "candidate_set")
    return _read_evidence(session, _get_candidate(session, candidate_id))


@router.get("/projects/{project_id}/evidence")
def read_evidence_by_alias(
    session: SessionDep,
    candidate_id: Optional[str] = Query(None),
    cluster_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
) -> dict:
    """Section 9.3: ``cluster_id`` is accepted only as a query alias, and the answer names its run."""
    if (candidate_id is None) == (cluster_id is None):
        raise invalid("candidate_id", "give exactly one of candidate_id or cluster_id")
    if candidate_id is not None:
        if run_id is not None:
            raise invalid("run_id", "a candidate id already names its run")
        session.enforce(Action.RUN_READ, candidate_id, "candidate_set")
        candidate = _get_candidate(session, candidate_id)
    else:
        if run_id is None:
            raise invalid("run_id", "a cluster label is only unique inside the run that produced it")
        session.enforce(Action.RUN_READ, run_id, "run")
        candidate = _find_by_label(session, run_id=run_id, cluster_id=cluster_id or "")
    return _read_evidence(session, candidate)


@router.get("/projects/{project_id}/candidates/{candidate_id}/alignment")
def read_alignment(candidate_id: str, session: SessionDep, page: Page = Depends(page_params)) -> dict:
    """Section 5.6: the history is reported against the thresholds in force, so a reclassification is visible."""
    session.enforce(Action.RUN_READ, candidate_id, "candidate_set")
    candidate = _get_candidate(session, candidate_id)
    thresholds = session.state.card_thresholds
    history = [
        {
            **record.model_dump(mode="json"),
            "kind_under_current_thresholds": str(classify_alignment(record.jaccard, thresholds=thresholds)),
        }
        for record in candidate.alignment_history
    ]
    recorded = {record.other_candidate_id for record in candidate.alignment_history}
    same_label = session.catalog.list_candidates(
        display_name=candidate.display_name,
        exclude_candidate_id=candidate.candidate_id,
        limit=page.limit,
        offset=page.offset,
    )
    return {
        "candidate_id": candidate.candidate_id,
        "run_id": candidate.run_id,
        "cluster_id": candidate.display_name,
        "member_fingerprint": candidate.member_fingerprint,
        "thresholds_version": thresholds.version,
        "alignment_history": history,
        "same_label_in_other_runs": [
            {
                "candidate_id": item.candidate_id,
                "run_id": item.run_id,
                "size": item.size,
                "donor_count": item.donor_count,
                "aligned": item.candidate_id in recorded,
            }
            for item in same_label
        ],
    }


@router.get("/projects/{project_id}/candidates")
def list_candidates(
    session: SessionDep,
    page: Page = Depends(page_params),
    run_id: Optional[str] = Query(None),
    cluster_id: Optional[str] = Query(None),
) -> dict:
    session.enforce(Action.RUN_READ, f"{session.project_id}:candidate_list", "candidate_collection")
    rows = session.catalog.list_candidates(
        display_name=cluster_id, run_id=run_id, limit=page.limit, offset=page.offset
    )
    return page.envelope([row.model_dump(mode="json") for row in rows])


def _draft_body(draft: CardDraft) -> dict[str, Any]:
    """What a reviewer is shown: the draft card, and the ceiling and reasons the gates derived beside it."""
    return {
        "card_id": draft.card_id,
        "candidate_id": draft.candidate_id,
        "ceiling": str(draft.ceiling),
        "gate_reasons": list(draft.gate_reasons),
        "notes": list(draft.notes),
        "support_met": draft.support_met,
        "underpowered_design": draft.underpowered_design,
        "blocking_risks": list(draft.blocking),
        "evidence_count": len(draft.card.evidence),
        "card": draft.card.model_dump(mode="json"),
    }


def _run_candidates(session: SessionDep, run_id: str, candidate_ids: Optional[list[str]]) -> list[CandidateSet]:
    """Candidates named by the caller still have to belong to this run: a card is only ever built from one."""
    if not candidate_ids:
        return []
    found: list[CandidateSet] = []
    for candidate_id in candidate_ids:
        candidate = _get_candidate(session, candidate_id)
        if candidate.run_id != run_id:
            raise invalid(
                "candidate_ids",
                f"candidate {candidate_id} was produced by run {candidate.run_id}, not {run_id}",
            )
        found.append(candidate)
    return found


@router.post("/projects/{project_id}/runs/{run_id}/cards/draft")
def draft_cards(run_id: str, body: DraftRequest, session: SessionDep) -> dict:
    """Section 9.1: this route files drafts whose hypothesis is still unknown, and refuses a card a
    person has already acted on. The label, the ontology term and the status come from the review route."""
    session.enforce(Action.ASSET_WRITE, f"{run_id}:card_draft", "evidence_card_collection")
    candidates = _run_candidates(session, run_id, body.candidate_ids)
    service = session.evidence_cards(run_id)
    drafts = [
        service.persist(draft, annotation_version_id=body.annotation_version_id)
        for draft in (service.drafts(candidates=candidates) if candidates else service.drafts())
    ]
    session.audit(
        "card:draft",
        run_id,
        details={
            "card_ids": [draft.card_id for draft in drafts],
            "ceilings": {draft.card_id: str(draft.ceiling) for draft in drafts},
            "annotation_version_id": body.annotation_version_id,
        },
    )
    return {
        "run_id": run_id,
        "thresholds_version": service.thresholds.version,
        "cards": [_draft_body(draft) for draft in drafts],
    }


@router.get("/projects/{project_id}/runs/{run_id}/cards/queue")
def read_expert_queue(
    run_id: str,
    session: SessionDep,
    client_ask: Optional[str] = Query(None, description="candidate ids the project asked about, comma separated"),
    limit: Optional[int] = Query(None, ge=1, description="cap on the queue; the plan's own number by default"),
) -> dict:
    """Section 13.4: the short list, in the order the rules put it there, with the reason for every place.

    The ceiling is a gate reading of the stored numbers, never a column on the card, so the order can only
    be produced by re-deriving the drafts - which is also why re-running a queue cannot drift from the run.
    """
    session.enforce(Action.RUN_READ, f"{run_id}:card_queue", "evidence_card_collection")
    drafts = session.evidence_cards(run_id).drafts()
    asked = [item for item in (client_ask or "").split(",") if item.strip()]
    queue = build_queue(
        drafts, client_ask=asked, thresholds=session.state.card_thresholds, limit=limit
    )
    return {"run_id": run_id, "drafted": len(drafts), **queue.as_dict()}


@router.post("/projects/{project_id}/cards/{card_id}/reviews")
def review_card(card_id: str, body: ReviewRequest, session: SessionDep) -> dict:
    """Section 9.3: only a card-scope decision rewrites the card; a membership one is recorded for the next run."""
    session.enforce(Action.CARD_REVIEW, card_id, "evidence_card")
    card = _get_card(session, card_id)
    review_id = new_review_id()
    previous = body.previous_value
    if body.scope is ReviewScope.FIELD:
        card, previous = _apply_field_revision(session, card, body, review_id=review_id)
    elif body.scope is ReviewScope.CARD and body.decision in CARD_SCOPE_DECISIONS:
        card = _apply_card_decision(session, card, body)
    event = _event(session, card_id, body, review_id=review_id, previous_value=previous)
    session.catalog.append_review(event)
    session.audit(
        f"card:review:{body.decision}",
        card_id,
        details={"review_id": event.review_id, "scope": str(body.scope), "rationale": event.rationale},
    )
    return {
        "review_id": event.review_id,
        "card_id": card_id,
        "final_status": str(card.final_status),
        "card": card.model_dump(mode="json"),
    }


def _event(session: SessionDep, card_id: str, body: ReviewRequest, *, review_id: str, previous_value: Any) -> ReviewEvent:
    return ReviewEvent(
        review_id=review_id,
        object_id=card_id,
        object_type="evidence_card",
        scope=body.scope,
        reviewer_id=session.actor.subject_id,
        decision=body.decision,
        rationale=body.rationale,
        timestamp=session.state.clock(),
        supersedes_review_id=body.supersedes_review_id,
        carry_over_from_candidate_id=body.carry_over_from_candidate_id,
        retraction_reason=body.retraction_reason,
        previous_value=previous_value,
        new_value=body.new_value,
    )


def _apply_field_revision(
    session: SessionDep, card: EvidenceCard, body: ReviewRequest, *, review_id: str
) -> tuple[EvidenceCard, dict[str, Any]]:
    """A field revision re-labels the overwritten numbers as human-sourced and keeps the original value reviewable."""
    if not body.new_value:
        raise invalid("new_value", "a field review states which card fields it overwrites")
    stored = card.model_dump()
    previous = {name: stored.get(name) for name in body.new_value}
    if body.previous_value is not None and body.previous_value != previous:
        raise conflict(
            "the card changed since you read it; re-read it and review the value it now holds",
            field="previous_value",
        )
    provenance = {
        **card.field_provenance,
        **{
            name: FieldProvenance(
                source=FieldSource.HUMAN, object_id=card.card_id, note=f"review {review_id}: {body.rationale}"
            )
            for name in body.new_value
        },
    }
    return _write(session, card, {**body.new_value, "field_provenance": provenance}), previous


def _apply_card_decision(session: SessionDep, card: EvidenceCard, body: ReviewRequest) -> EvidenceCard:
    status = CARD_SCOPE_DECISIONS[body.decision]
    updates: dict[str, Any] = {
        "final_status": status,
        "reviewer_id": session.actor.subject_id,
        "reviewed_at": session.state.clock(),
        "review_reason": body.rationale,
    }
    if status is FinalStatus.CONFLICT:
        updates["unresolved_conflict"] = True
    if status is FinalStatus.REJECTED:
        updates["sign_off"] = SignOff.NOT_SIGNED
    return _write(session, card, updates)


@router.get("/projects/{project_id}/cards/{card_id}/reviews")
def list_reviews(card_id: str, session: SessionDep) -> dict:
    session.enforce(Action.RUN_READ, card_id, "evidence_card")
    _get_card(session, card_id)
    events = session.catalog.reviews(card_id)
    return {"card_id": card_id, "reviews": [event.model_dump(mode="json") for event in events]}


def _conclusion_authors(session: SessionDep, card: EvidenceCard) -> list[str]:
    """Section 13.1: whoever produced the conclusion - the plan author or the reviewing analyst - cannot sign it."""
    authors = [card.reviewer_id] if card.reviewer_id else []
    try:
        manifest = session.catalog.get_run(card.run_id)
        authors.append(session.catalog.plan_author(manifest.plan_id, manifest.plan_version))
    except LookupError:
        pass
    return authors


@router.post("/projects/{project_id}/cards/{card_id}/sign_off")
def sign_off_card(card_id: str, body: SignOffRequest, session: SessionDep) -> dict:
    card = _get_card(session, card_id)
    #: Section 13.1: the author list is only what the separation-of-duties test reads, so an empty list
    #: - a card whose run or plan cannot be resolved - must not read as "no objection". The check runs at
    #: least once either way, and that single call carries both the role test and the author test.
    authors = list(dict.fromkeys(_conclusion_authors(session, card)))
    for author in authors or [None]:
        session.enforce(Action.CARD_SIGN_OFF, card_id, "evidence_card", attributes={"authored_by": author})
    if body.sign_off is SignOff.NOT_SIGNED and card.sign_off is not SignOff.NOT_SIGNED and not body.reason:
        raise invalid("reason", "withdrawing a sign-off has to record why")
    updates: dict[str, Any] = {"sign_off": body.sign_off}
    if body.reason:
        updates["review_reason"] = body.reason
    if body.sign_off is not SignOff.NOT_SIGNED:
        updates.setdefault("reviewer_id", session.actor.subject_id)
        updates["reviewed_at"] = session.state.clock()
    signed = _write(session, card, updates)
    session.audit(
        "card:sign_off",
        card_id,
        details={"sign_off": str(signed.sign_off), "final_status": str(signed.final_status), "reason": body.reason},
    )
    return {"card_id": card_id, "sign_off": str(signed.sign_off), "final_status": str(signed.final_status)}
