"""Project statistics (plan V2.0 sections 5.1, 9.7, 11.2).

The screen behind these numbers is only honest if the numbers are read from the catalog rather than
counted from whatever page a browser received, so every assertion here reads the aggregate route the
page reads.
"""

from datetime import timedelta

from bioadapt_policies.access import Role
from bioadapt_schemas.entities import ReviewEvent
from bioadapt_schemas.enums import (
    ApprovalKind,
    ApprovalState,
    ReviewDecision,
    ReviewScope,
    RetentionClass,
    RunEventKind,
    RunState,
    SignOff,
    SubjectKind,
)
from bioadapt_schemas.orchestration import Approval, RunEvent
from conftest import (
    ANALYST,
    EXPERT,
    LOCAL_PROJECT_ID,
    NOW,
    OTHER_PROJECT_ID,
    PI,
    PROJECT_ID,
    RUN_ID,
    SERVICE,
    SHA,
    asset,
    candidate,
    card,
    manifest,
    other_asset,
    plan,
)

BASE = f"/v1/projects/{PROJECT_ID}/stats"


def _event(
    event_id: str,
    kind: RunEventKind,
    *,
    states: tuple[RunState, RunState] = (RunState.CREATED, RunState.CREATED),
    **overrides,
) -> RunEvent:
    from_state, to_state = states
    fields = {
        "event_id": event_id,
        "run_id": RUN_ID,
        "kind": kind,
        "from_state": from_state,
        "to_state": to_state,
        "actor_id": SERVICE,
        "actor_kind": SubjectKind.SERVICE,
        "reason": "reported by the engine",
        "input_hash": SHA,
        "at": NOW,
    }
    fields.update(overrides)
    return RunEvent(**fields)


def test_stats_report_the_fixture_counts(client, auth):
    response = client.get(BASE, headers=auth(ANALYST, [Role.ANALYST]))

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["project_id"] == PROJECT_ID
    assert body["generated_at"] == NOW.isoformat()
    assert body["project"]["thresholds_confirmed"] is True
    assert body["project"]["thresholds_version"] == "v0-unconfirmed"
    assert body["project"]["review_load_budget_hours_per_week"] == 4.0
    assert body["data"]["assets"] == {
        "total": 1,
        "size_bytes": 1024,
        "from_runs": 0,
        "registered_by_hand": 1,
        "by_retention_class": {"ephemeral": 1},
        "by_usage_scope": {"this_project_only": 1},
        "by_filtering_state": {"none": 1},
    }
    assert body["data"]["plans"] == {"versions": 1, "distinct": 1}
    assert body["analysis"]["runs"] == {
        "total": 1,
        "active": 1,
        "closed": 0,
        "by_state": {"CREATED": 1},
    }
    assert body["analysis"]["candidates"] == {"total": 0, "cells": 0, "donor_assignments": 0}
    assert body["analysis"]["cards"] == {
        "total": 0,
        "by_status": {},
        "reviewed": 0,
        "signed_off": 0,
    }
    assert body["analysis"]["reviews"] == {"total": 0}
    assert body["process"]["durations"] == {"runs": 0, "average_seconds": None, "median_seconds": None}
    assert body["process"]["retries"] == {"node": 0, "business": 0}
    assert body["process"]["usage"] == {"compute_minutes": 0.0, "model_cost_usd": 0.0}
    assert body["process"]["approvals"] == {
        "total": 0,
        "pending": 0,
        "overdue": 0,
        "by_kind": {},
        "average_decision_seconds": None,
    }
    assert body["process"]["exports"] == {"total": 0, "by_status": {}, "by_kind": {}}


def test_an_empty_project_reports_zeroes_not_a_missing_answer(client, auth):
    response = client.get(
        f"/v1/projects/{LOCAL_PROJECT_ID}/stats",
        headers=auth(ANALYST, [Role.ANALYST], project_ids=[LOCAL_PROJECT_ID]),
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["data"]["assets"]["total"] == 0
    assert body["data"]["assets"]["size_bytes"] == 0
    assert body["data"]["plans"] == {"versions": 0, "distinct": 0}
    assert body["analysis"]["runs"]["total"] == 0
    assert body["analysis"]["cards"]["total"] == 0
    assert body["process"]["exports"]["total"] == 0


def test_stats_aggregate_runs_cards_reviews_approvals_exports_and_usage(client, auth, catalog, seeded):
    catalog.set_scope(PROJECT_ID)
    catalog.apply_transition(
        _event(
            "ev_archive",
            RunEventKind.STATE,
            states=(RunState.CREATED, RunState.ARCHIVED),
            actor_id=PI,
            actor_kind=SubjectKind.HUMAN,
            reason="the run reached its terminal state",
            at=NOW + timedelta(minutes=30),
        ),
        finished_at=NOW + timedelta(minutes=30),
    )
    catalog.append_run_event(
        _event(
            "ev_node_retry",
            RunEventKind.NODE_RETRY,
            states=(RunState.ANALYSIS_RUNNING, RunState.ANALYSIS_RUNNING),
            node_id="cluster",
            attempt=2,
        )
    )
    catalog.append_run_event(
        _event(
            "ev_business_retry",
            RunEventKind.BUSINESS_RETRY,
            states=(RunState.ANALYSIS_RUNNING, RunState.ANALYSIS_RUNNING),
            node_id="cluster",
            attempt=2,
        )
    )
    catalog.append_run_event(
        _event(
            "ev_usage",
            RunEventKind.USAGE,
            states=(RunState.ARCHIVED, RunState.ARCHIVED),
            node_id="cluster",
            compute_minutes=12.5,
            model_cost_usd=0.75,
        )
    )
    catalog.append_review(
        ReviewEvent(
            review_id="rev_001",
            object_id=seeded["card_id"],
            object_type="evidence_card",
            scope=ReviewScope.CARD,
            reviewer_id=EXPERT,
            decision=ReviewDecision.MARK_UNKNOWN,
            rationale="one signal is not enough",
            timestamp=NOW,
        )
    )
    catalog.update_card(card(sign_off=SignOff.FULL, reviewer_id=EXPERT, reviewed_at=NOW))
    catalog.record_asset(
        asset(
            asset_id="as_run_out",
            file_sha256="ef" * 32,
            size_bytes=2048,
            retention_class=RetentionClass.RUN_LINKED,
            created_by_run_id=RUN_ID,
        )
    )
    catalog.record_plan(plan(plan_id="plan_002"), created_by=ANALYST)
    catalog.record_approval(
        Approval(
            approval_id="apr_overdue",
            run_id=RUN_ID,
            kind=ApprovalKind.PLAN,
            subject_digest=SHA,
            requested_by=ANALYST,
            assigned_to=PI,
            requested_at=NOW - timedelta(days=2),
            sla_at=NOW - timedelta(days=1),
        )
    )
    catalog.record_approval(
        Approval(
            approval_id="apr_decided",
            run_id=RUN_ID,
            kind=ApprovalKind.PLAN,
            subject_digest=SHA,
            requested_by=ANALYST,
            assigned_to=PI,
            requested_at=NOW - timedelta(hours=2),
            sla_at=NOW + timedelta(days=1),
            state=ApprovalState.APPROVED,
            decided_by=PI,
            decided_at=NOW,
            decision_reason="checked the design matrix",
        )
    )
    catalog.create_export(export_id="exp_001", kind="tables", filters={}, requested_by=PI)

    response = client.get(BASE, headers=auth(ANALYST, [Role.ANALYST]))

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["data"]["assets"]["total"] == 2
    assert body["data"]["assets"]["size_bytes"] == 3072
    assert body["data"]["assets"]["from_runs"] == 1
    assert body["data"]["assets"]["registered_by_hand"] == 1
    assert body["data"]["assets"]["by_retention_class"] == {"ephemeral": 1, "run_linked": 1}
    assert body["data"]["plans"] == {"versions": 2, "distinct": 2}
    assert body["analysis"]["runs"] == {
        "total": 2,
        "active": 1,
        "closed": 1,
        "by_state": {"ARCHIVED": 1, "CREATED": 1},
    }
    assert body["analysis"]["candidates"] == {"total": 2, "cells": 6, "donor_assignments": 4}
    assert body["analysis"]["cards"] == {
        "total": 2,
        "by_status": {"unknown": 2},
        "reviewed": 1,
        "signed_off": 1,
    }
    assert body["analysis"]["reviews"] == {"total": 1}
    assert body["process"]["durations"] == {
        "runs": 1,
        "average_seconds": 1800.0,
        "median_seconds": 1800.0,
    }
    assert body["process"]["retries"] == {"node": 1, "business": 1}
    assert body["process"]["usage"] == {"compute_minutes": 12.5, "model_cost_usd": 0.75}
    assert body["process"]["approvals"] == {
        "total": 2,
        "pending": 1,
        "overdue": 1,
        "by_kind": {"plan": 2},
        "average_decision_seconds": 7200.0,
    }
    assert body["process"]["exports"] == {"total": 1, "by_status": {"QUEUED": 1}, "by_kind": {"tables": 1}}


def test_stats_never_return_row_identifiers_or_reasons(client, auth, catalog, seeded):
    catalog.set_scope(PROJECT_ID)
    catalog.append_review(
        ReviewEvent(
            review_id="rev_001",
            object_id=seeded["card_id"],
            object_type="evidence_card",
            scope=ReviewScope.CARD,
            reviewer_id=EXPERT,
            decision=ReviewDecision.MARK_UNKNOWN,
            rationale="the reviewer's own words",
            timestamp=NOW,
        )
    )

    response = client.get(BASE, headers=auth(ANALYST, [Role.ANALYST]))

    assert response.status_code == 200
    text = response.text
    for identifier in (RUN_ID, seeded["card_id"], seeded["candidate_id"], EXPERT, "the reviewer's own words"):
        assert identifier not in text
    assert PROJECT_ID in text


def test_another_projects_rows_do_not_move_this_projects_numbers(client, auth, catalog):
    catalog.set_scope(OTHER_PROJECT_ID)
    catalog.record_asset(other_asset())
    catalog.record_run(manifest(run_id="run_other_01", project_id=OTHER_PROJECT_ID), state="CREATED")
    catalog.record_candidate(candidate(run_id="run_other_01"))
    catalog.set_scope(PROJECT_ID)

    response = client.get(BASE, headers=auth(ANALYST, [Role.ANALYST]))

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["data"]["assets"]["total"] == 1
    assert body["analysis"]["runs"]["total"] == 1
    assert body["analysis"]["candidates"]["total"] == 0


def test_a_token_without_project_read_is_refused(client, auth):
    response = client.get(BASE, headers=auth(SERVICE, [Role.SERVICE_AGENT]))

    assert response.status_code == 403
    assert "project:read" in response.json()["message"]
