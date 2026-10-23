import sqlite3
from datetime import timedelta
from typing import Optional

import pytest
from pydantic import ValidationError

from bioadapt_schemas.entities import sha256_of_json
from bioadapt_schemas.enums import (
    AnnotationStatus,
    ApprovalKind,
    ApprovalState,
    AssetRole,
    FinalStatus,
    HypothesisType,
    LabelLevel,
    RetentionClass,
    RunEventKind,
    RunState,
    UsageScope,
)
from bioadapt_schemas.nodes import NodePosition
from bioadapt_schemas.orchestration import (
    AdhocToolGrant,
    Approval,
    ExecutionReceipt,
    new_tool_grant_id,
)
from bioadapt_schemas.thresholds import Thresholds
from bioadapt_storage import (
    PLATFORM_SCOPE,
    REUSABLE_SCOPES,
    Catalog,
    ConcurrentWriteError,
    ExecutionConflictError,
    ScopeError,
    lint,
)
from storage_factories import (
    NOW,
    OTHER_PROJECT_ID,
    PROJECT_ID,
    RUN_ID,
    SHA,
    annotation,
    asset,
    candidate,
    card,
    event,
    execution_node,
    lineage,
    manifest,
    plan,
    project,
    receipt,
    review,
)


@pytest.fixture
def catalog():
    c = Catalog(clock=lambda: NOW)
    c.create_project(project())
    yield c
    c.close()


class TestSessionScope:
    def test_statements_without_a_scope_are_denied(self):
        fresh = Catalog(clock=lambda: NOW)
        with pytest.raises(ScopeError, match="denied by default"):
            fresh.record_asset(asset())

    def test_writing_another_project_asset_is_refused_by_the_trigger(self, catalog):
        catalog.set_scope(OTHER_PROJECT_ID)
        with pytest.raises(sqlite3.IntegrityError, match="outside the session project scope"):
            catalog.record_asset(asset())

    def test_rows_of_other_projects_are_invisible_to_reads(self, catalog):
        catalog.record_asset(asset())
        catalog.set_scope(OTHER_PROJECT_ID)
        with pytest.raises(LookupError):
            catalog.get_asset("as_0001")

    def test_moving_a_row_to_another_project_is_refused(self, catalog):
        catalog.record_asset(asset())
        with pytest.raises(sqlite3.IntegrityError, match="outside the session project scope"):
            catalog.conn.execute(
                "update data_asset set project_id = ? where asset_id = 'as_0001'", (OTHER_PROJECT_ID,)
            )

    def test_a_missing_project_lookup_fails(self):
        fresh = Catalog(clock=lambda: NOW)
        fresh.set_scope(PROJECT_ID)
        with pytest.raises(LookupError, match="not registered"):
            fresh.get_project()

    def test_project_round_trip_keeps_typed_thresholds(self, catalog):
        loaded = catalog.get_project()
        assert loaded.project_id == PROJECT_ID
        assert loaded.thresholds.rare_share_max == 0.01
        assert loaded.created_at == NOW

    def test_replacing_the_cut_set_moves_nothing_else_in_the_registration(self, catalog):
        catalog.update_project_thresholds(
            Thresholds(version="v1-measured", confirmed=True, rare_share_max=0.005)
        )
        stored = catalog.get_project()
        assert (stored.thresholds.version, stored.thresholds.confirmed) == ("v1-measured", True)
        assert stored.thresholds.rare_share_max == 0.005
        registered = project()
        assert stored.study_goal == registered.study_goal
        assert stored.privacy_tier is registered.privacy_tier
        assert stored.created_by == registered.created_by


class TestAssets:
    def test_identical_bytes_resolve_to_the_existing_asset(self, catalog):
        first = catalog.record_asset(asset())
        again = catalog.record_asset(asset(asset_id="as_9999"))
        assert first == again == "as_0001"
        assert catalog.list_assets() == [asset()]

    def test_asset_fields_survive_the_round_trip(self, catalog):
        catalog.record_asset(
            asset(
                retention_class=RetentionClass.DELIVERABLE,
                usage_scope=UsageScope.CROSS_PROJECT_ALLOWED,
                created_by_run_id="run_project001_02",
            )
        )
        loaded = catalog.get_asset("as_0001")
        assert loaded.retention_class is RetentionClass.DELIVERABLE
        assert loaded.usage_scope is UsageScope.CROSS_PROJECT_ALLOWED
        assert loaded.created_at == NOW

    def test_filtering_by_retention_class_and_run(self, catalog):
        catalog.record_asset(asset())
        catalog.record_asset(
            asset(asset_id="as_0002", created_by_run_id="run_x", file_sha256="ee" * 32)
        )
        assert [a.asset_id for a in catalog.list_assets(run_id="run_x")] == ["as_0002"]
        assert catalog.list_assets(retention_class=RetentionClass.ARCHIVE.value) == []

    def test_case_library_only_sees_reusable_scopes(self, catalog):
        catalog.record_asset(asset(usage_scope=UsageScope.CROSS_PROJECT_ALLOWED, file_sha256=SHA))
        catalog.record_asset(asset(asset_id="as_0002", usage_scope=UsageScope.THIS_PROJECT_ONLY, file_sha256="ff" * 32))
        catalog.set_scope(OTHER_PROJECT_ID)
        assert [a.asset_id for a in catalog.shared_assets()] == ["as_0001"]
        assert REUSABLE_SCOPES == (UsageScope.INTERNAL_IMPROVEMENT, UsageScope.CROSS_PROJECT_ALLOWED)

    def test_one_shared_asset_is_read_by_id_without_scanning_the_library(self, catalog):
        catalog.record_asset(asset(usage_scope=UsageScope.CROSS_PROJECT_ALLOWED, file_sha256=SHA))
        catalog.record_asset(asset(asset_id="as_0002", usage_scope=UsageScope.THIS_PROJECT_ONLY, file_sha256="ff" * 32))
        catalog.set_scope(OTHER_PROJECT_ID)
        assert catalog.shared_asset("as_0001").asset_id == "as_0001"
        assert catalog.shared_asset("as_0002") is None
        assert catalog.shared_asset("as_missing") is None
        catalog.set_scope(PROJECT_ID)
        #: §5.8: a row of this project is not a shared row - reading it is get_asset's job or nothing.
        assert catalog.shared_asset("as_0001") is None

    def test_asset_quota_counters_are_scoped_and_paged(self, catalog):
        catalog.record_asset(asset())
        catalog.record_asset(asset(asset_id="as_0002", file_sha256="ee" * 32))
        assert catalog.storage_bytes() == 2048
        assert [a.asset_id for a in catalog.list_assets(limit=1)] == ["as_0001"]
        assert [a.asset_id for a in catalog.list_assets(limit=1, offset=1)] == ["as_0002"]

    def test_lineage_is_recorded_and_read_back(self, catalog):
        catalog.record_asset(asset())
        catalog.record_lineage(lineage())
        catalog.record_lineage(lineage(role=AssetRole.OUTPUT, ordinal=1))
        rows = catalog.lineage("as_0001")
        assert [r.role for r in rows] == [AssetRole.INPUT, AssetRole.OUTPUT]

    def test_lineage_cannot_be_edited(self, catalog):
        catalog.record_asset(asset())
        catalog.record_lineage(lineage())
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            catalog.conn.execute("update asset_lineage set ordinal = 9")


class TestRunsAndInvocations:
    def test_run_state_transitions_are_tracked_separately_from_the_manifest(self, catalog):
        catalog.record_run(manifest(), state="CREATED")
        catalog.set_run_state("run_project001_02", "ANALYSIS_RUNNING")
        assert catalog.run_state("run_project001_02") == "ANALYSIS_RUNNING"
        assert catalog.get_run("run_project001_02").tool_versions == {"scrublet": "0.2.3"}

    def test_active_run_counts_and_listings_drive_the_quota(self, catalog):
        catalog.record_run(manifest(), state="CREATED")
        catalog.record_run(
            manifest(run_id="run_project001_03", input_hashes={"as_0001": "ee" * 32}), state="ARCHIVED"
        )
        assert catalog.count_runs_not_in(["ARCHIVED", "FAILED"]) == 1
        assert [row["run_id"] for row in catalog.list_runs(state="ARCHIVED")] == ["run_project001_03"]
        assert len(catalog.list_runs(limit=1)) == 1
        with pytest.raises(ValueError, match="at least one state"):
            catalog.count_runs_not_in([])

    def test_the_newest_line_into_a_state_is_read_without_the_journal(self, catalog):
        catalog.record_run(manifest(), state="CREATED")
        catalog.append_run_event(event("evt_1", from_state=RunState.CREATED, to_state=RunState.ANALYSIS_RUNNING))
        catalog.append_run_event(
            event("evt_2", from_state=RunState.ANALYSIS_RUNNING, to_state=RunState.RETRYABLE_FAILURE)
        )
        catalog.append_run_event(
            event("evt_3", from_state=RunState.PREFLIGHT_RUNNING, to_state=RunState.RETRYABLE_FAILURE)
        )
        catalog.append_run_event(
            event(
                "evt_4",
                kind=RunEventKind.BUSINESS_RETRY,
                from_state=RunState.RETRYABLE_FAILURE,
                to_state=RunState.RETRYABLE_FAILURE,
                node_id="cluster",
            )
        )
        #: §9.2: a waiting run returns to the state the *latest* entry into it came from.
        resumed = catalog.last_event_into_state(RUN_ID, str(RunState.RETRYABLE_FAILURE), kind=str(RunEventKind.STATE))
        assert resumed.event_id == "evt_3"
        assert resumed.from_state is RunState.PREFLIGHT_RUNNING
        #: the retry backoff reads the last line of any kind that landed in the failure state
        assert catalog.last_event_into_state(RUN_ID, str(RunState.RETRYABLE_FAILURE)).event_id == "evt_4"
        assert catalog.last_event_into_state(RUN_ID, str(RunState.ARCHIVED)) is None
        scanned = next(e for e in reversed(catalog.list_run_events(RUN_ID)) if e.to_state is RunState.RETRYABLE_FAILURE)
        assert scanned.event_id == catalog.last_event_into_state(RUN_ID, str(RunState.RETRYABLE_FAILURE)).event_id

    def test_unknown_run_lookups_fail(self, catalog):
        with pytest.raises(LookupError):
            catalog.set_run_state("run_missing", "FAILED")
        with pytest.raises(LookupError):
            catalog.get_run("run_missing")

    def test_model_invocation_id_is_content_addressed(self, catalog):
        invocation = evidence_invocation()
        first = catalog.record_model_invocation(invocation, run_id="run_project001_02")
        second = catalog.record_model_invocation(invocation.model_copy(update={"cost_usd": 0.2}), run_id="run_project001_02")
        assert first != second
        assert catalog.record_model_invocation(invocation, run_id="run_project001_02") == first

    def test_invocations_are_append_only(self, catalog):
        catalog.record_model_invocation(evidence_invocation(), run_id="run_project001_02")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            catalog.conn.execute("delete from model_invocation")


class TestDurability:
    def test_a_write_is_on_disk_by_the_time_the_call_returns(self, tmp_path):
        path = str(tmp_path / "index.sqlite3")
        first = Catalog(path, clock=lambda: NOW)
        first.create_project(project())
        first.set_scope(PROJECT_ID)
        first.record_asset(asset())
        first.close()
        reopened = Catalog(path, clock=lambda: NOW)
        assert reopened.set_scope(PROJECT_ID).get_asset("as_0001").file_sha256 == SHA
        reopened.close()


class TestAnnotationVersions:
    def test_only_one_active_version_per_run_and_label_column(self, catalog):
        catalog.record_asset(asset())
        catalog.record_asset(asset(asset_id="as_0002", file_sha256="ee" * 32))
        catalog.record_annotation_version(annotation())
        with pytest.raises(sqlite3.IntegrityError, match="annotation_version"):
            catalog.record_annotation_version(annotation(version_id="av_0002", asset_id="as_0002"))

    def test_superseding_links_the_version_chain(self, catalog):
        catalog.record_asset(asset())
        catalog.record_asset(asset(asset_id="as_0002", file_sha256="ee" * 32))
        catalog.record_annotation_version(annotation())
        catalog.supersede_annotation_version("av_0001", "av_0002")
        catalog.record_annotation_version(annotation(version_id="av_0002", asset_id="as_0002"))
        assert catalog.effective_annotation_version("run_project001_02", "celltype").version_id == "av_0002"
        chain = catalog.annotation_chain("run_project001_02", "celltype")
        assert [(v.version_id, v.status) for v in chain] == [
            ("av_0001", AnnotationStatus.SUPERSEDED),
            ("av_0002", AnnotationStatus.ACTIVE),
        ]
        assert chain[0].superseded_by == "av_0002"

    def test_reports_reference_a_pinned_version(self, catalog):
        catalog.record_asset(asset())
        catalog.record_annotation_version(annotation())
        pinned = catalog.effective_annotation_version("run_project001_02", "celltype")
        assert pinned.asset_id == "as_0001"

    def test_superseding_a_retired_version_is_refused(self, catalog):
        catalog.record_asset(asset())
        catalog.record_annotation_version(annotation())
        catalog.supersede_annotation_version("av_0001", "av_0002")
        with pytest.raises(LookupError, match="no active annotation version"):
            catalog.supersede_annotation_version("av_0001", "av_0003")

    def test_a_replacement_moves_the_chain_in_one_step(self, catalog):
        catalog.record_asset(asset())
        catalog.record_asset(asset(asset_id="as_0002", file_sha256="ee" * 32))
        catalog.record_annotation_version(annotation())
        catalog.record_annotation_version(
            annotation(version_id="av_0002", asset_id="as_0002"), supersedes_version_id="av_0001"
        )
        chain = catalog.annotation_chain("run_project001_02", "celltype")
        assert [(v.version_id, v.status) for v in chain] == [
            ("av_0001", AnnotationStatus.SUPERSEDED),
            ("av_0002", AnnotationStatus.ACTIVE),
        ]

    def test_a_failed_replacement_leaves_the_active_version_in_place(self, catalog):
        catalog.record_asset(asset())
        catalog.record_annotation_version(annotation())
        with pytest.raises(sqlite3.IntegrityError):
            catalog.record_annotation_version(
                annotation(version_id="av_0001", asset_id="as_0001", label_column="celltype"),
                supersedes_version_id="av_0001",
            )
        assert catalog.effective_annotation_version("run_project001_02", "celltype").status is AnnotationStatus.ACTIVE
        assert catalog.get_annotation_version("av_0001").superseded_by is None


class TestCandidatesAndCards:
    def test_candidate_round_trip(self, catalog):
        c = candidate()
        catalog.record_candidate(c)
        assert catalog.get_candidate(c.candidate_id) == c

    def test_card_json_round_trip_keeps_nested_evidence(self, catalog):
        catalog.record_card(card())
        loaded = catalog.get_card("card_001")
        assert loaded == card()
        assert loaded.evidence["ev_0001"].content["log2fc"] == 1.7

    def test_a_label_group_pages_without_the_row_the_reader_stands_on(self, catalog):
        rows = [candidate(run_id=f"run_{i}") for i in range(3)]
        for row in rows:
            catalog.record_candidate(row)
        label = rows[0].display_name
        assert [c.run_id for c in catalog.list_candidates(display_name=label, limit=2)] == ["run_0", "run_1"]
        #: §5.6: a cluster label is an alias inside its run, so the group across runs is what a reader
        #: pages through - excluding its own row must not cost a row from the page.
        others = catalog.list_candidates(
            display_name=label, exclude_candidate_id=rows[0].candidate_id, limit=2
        )
        assert [c.run_id for c in others] == ["run_1", "run_2"]
        last = catalog.list_candidates(display_name=label, exclude_candidate_id=rows[2].candidate_id, limit=2)
        assert [c.run_id for c in last] == ["run_0", "run_1"]

    def test_card_status_is_projected_for_queries_and_refreshed_on_update(self, catalog):
        catalog.record_card(card(), annotation_version_id="av_0001")
        assert catalog.card_final_status("card_001") == FinalStatus.UNKNOWN.value
        revised = card(
            hypothesis_type=HypothesisType.CELL_TYPE,
            unknown_reason=None,
            final_status=FinalStatus.PROVISIONAL,
            candidate_label="candidate_x",
            label_level=LabelLevel.PROVISIONAL_PROJECT,
            status_reason="marker support",
        )
        catalog.update_card(revised, annotation_version_id="av_0001")
        assert catalog.card_final_status("card_001") == "provisional"
        assert catalog.get_card("card_001").candidate_label == "candidate_x"

    def test_missing_card_lookups_fail(self, catalog):
        with pytest.raises(LookupError):
            catalog.get_card("card_missing")


class TestReviewsDecisionsAudit:
    def test_review_history_is_ordered_and_immutable(self, catalog):
        catalog.append_review(review(review_id="rev_0002", timestamp=NOW.replace(hour=1)))
        catalog.append_review(review(review_id="rev_0001", timestamp=NOW))
        order = [r.review_id for r in catalog.reviews("card_001")]
        assert order == ["rev_0001", "rev_0002"]
        for stmt in ("update review_event set rationale = 'x'", "delete from review_event"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                catalog.conn.execute(stmt)

    def test_egress_decisions_are_stored_for_the_compliance_trail(self, catalog):
        catalog.record_egress_decision("dec_0001", request={"purpose": "x"}, decision={"allowed": False}, allowed=False)
        stored = catalog.egress_decision("dec_0001")
        assert stored["allowed"] is False
        assert stored["request"] == {"purpose": "x"}
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            catalog.conn.execute("update egress_decision set allowed = 1")

    def test_audit_distinguishes_service_accounts_from_humans(self, catalog):
        catalog.audit(subject_id="svc_orchestrator", subject_kind="service", action="asset:write", resource_id="as_0001")
        catalog.audit(
            subject_id="user_pi_01",
            subject_kind="human",
            action="egress:approve",
            resource_id="egr_0001",
            decision_id="dec_0001",
            trace_id="tr_0001",
            details={"approval_id": "egr_0001"},
        )
        assert len(catalog.audit_entries(limit=100)) == 2
        humans = catalog.audit_entries(subject_kind="human", limit=100)
        assert [h["action"] for h in humans] == ["egress:approve"]
        assert humans[0]["details"] == {"approval_id": "egr_0001"}
        assert humans[0]["trace_id"] == "tr_0001"
        assert [h["action"] for h in catalog.audit_entries(trace_id="tr_0001", limit=10)] == ["egress:approve"]

    def test_egress_approvals_are_scoped_and_immutable(self, catalog):
        catalog.record_egress_approval(
            approval_id="egr_0001",
            approver_id="user_compliance_01",
            purpose="cloud_model_call",
            approved_on="2026-01-01",
            expires_on="2026-02-01",
        )
        stored = catalog.egress_approval("egr_0001")
        assert stored["purpose"] == "cloud_model_call"
        assert stored["expires_on"] == "2026-02-01"
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            catalog.conn.execute("update egress_approval set expires_on = '2030-01-01'")

    def test_audit_is_append_only(self, catalog):
        catalog.audit(subject_id="u", subject_kind="human", action="x", resource_id="r")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            catalog.conn.execute("delete from audit_log")

    def test_the_shipped_schema_passes_lint(self):
        assert lint() == []


class TestPlansIdempotencyExports:
    def test_plan_round_trips_and_is_pinned_by_a_digest(self, catalog):
        p = plan()
        digest = catalog.record_plan(p, created_by="user_analyst_01")
        assert catalog.get_plan("plan_001", "1") == p
        assert catalog.plan_digest("plan_001", "1") == digest

    def test_stored_plans_cannot_be_rewritten(self, catalog):
        catalog.record_plan(plan(), created_by="user_analyst_01")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            catalog.conn.execute("update analysis_plan set objective = 'x'")

    def test_idempotency_replay_returns_the_original_resource(self, catalog):
        catalog.record_idempotency(
            idempotency_id="idem_1",
            route="POST /v1/runs",
            request_hash="h1",
            resource_kind="run",
            resource_id="run_project001_02",
        )
        assert catalog.lookup_idempotency("idem_1", route="POST /v1/runs") == {
            "request_hash": "h1",
            "resource_kind": "run",
            "resource_id": "run_project001_02",
        }
        assert catalog.lookup_idempotency("idem_1", route="POST /v1/exports") is None

    def test_idempotency_records_are_project_scoped(self, catalog):
        catalog.record_idempotency(
            idempotency_id="idem_1",
            route="POST /v1/runs",
            request_hash="h1",
            resource_kind="run",
            resource_id="run_project001_02",
        )
        catalog.set_scope(OTHER_PROJECT_ID)
        assert catalog.lookup_idempotency("idem_1", route="POST /v1/runs") is None

    def test_export_jobs_move_through_states(self, catalog):
        catalog.create_export(
            export_id="exp_0001", kind="table_csv", filters={"run_id": RUN_ID}, requested_by="user_analyst_01"
        )
        assert catalog.get_export("exp_0001")["status"] == "QUEUED"
        catalog.update_export("exp_0001", status="READY", artifact_asset_id="as_0009")
        ready = catalog.get_export("exp_0001")
        assert (ready["status"], ready["artifact_asset_id"]) == ("READY", "as_0009")
        assert ready["filters"] == {"run_id": RUN_ID}
        assert [e["export_id"] for e in catalog.list_exports(status="READY", limit=50)] == ["exp_0001"]
        with pytest.raises(LookupError):
            catalog.update_export("exp_missing", status="FAILED", failure_reason="disk full")

    def test_exports_are_paged(self, catalog):
        for index in range(3):
            catalog.create_export(
                export_id=f"exp_{index:04d}", kind="figure_bundle", filters={}, requested_by="user_analyst_01"
            )
        assert [e["export_id"] for e in catalog.list_exports(limit=2)] == ["exp_0000", "exp_0001"]


class TestApprovalWrites:
    """Section 9.2: a gate is closed from what the caller read, and a move under it loses the write."""

    PI = "user_pi_01"
    DEPUTY = "user_deputy_01"

    def _gate(self, catalog):
        catalog.set_scope(PROJECT_ID)
        catalog.record_approval(
            Approval(
                approval_id="apr_cas_01",
                run_id=RUN_ID,
                kind=ApprovalKind.PLAN,
                subject_digest=SHA,
                requested_by="user_analyst_01",
                assigned_to=self.PI,
                requested_at=NOW,
                sla_at=NOW + timedelta(days=3),
            )
        )
        return catalog.get_approval("apr_cas_01")

    def test_a_delegate_that_beats_a_decision_owners_it(self, catalog):
        gate = self._gate(catalog)
        #: Two requests read the same pending gate. The delegation lands first, so the decision - made by
        #: the person who no longer holds it - is refused instead of quietly overwriting the delegation.
        catalog.delegate_approval(
            gate.approval_id, from_subject=gate.assigned_to, to_subject=self.DEPUTY, at=NOW, read_by=gate.assigned_to
        )
        with pytest.raises(ConcurrentWriteError, match="no longer held by user_pi_01"):
            catalog.decide_approval(
                gate.approval_id,
                state=str(ApprovalState.APPROVED),
                decided_by=self.PI,
                decided_at=NOW,
                reason="I still think it is mine",
                read_by=gate.assigned_to,
            )
        moved = catalog.get_approval(gate.approval_id)
        assert (moved.state, moved.assigned_to, moved.decided_by) == (ApprovalState.PENDING, self.DEPUTY, None)

        #: The gate is not lost: the delegate it was handed to can still answer it.
        decided = catalog.decide_approval(
            gate.approval_id,
            state=str(ApprovalState.APPROVED),
            decided_by=self.DEPUTY,
            decided_at=NOW,
            reason="cleared",
            read_by=self.DEPUTY,
        )
        assert (decided.state, decided.decided_by, decided.delegated_from) == (
            ApprovalState.APPROVED,
            self.DEPUTY,
            self.PI,
        )

    def test_a_gate_that_already_closed_is_not_a_race(self, catalog):
        gate = self._gate(catalog)
        catalog.decide_approval(
            gate.approval_id,
            state=str(ApprovalState.APPROVED),
            decided_by=self.PI,
            decided_at=NOW,
            reason="first",
            read_by=self.PI,
        )
        with pytest.raises(LookupError, match="no pending approval"):
            catalog.decide_approval(
                gate.approval_id,
                state=str(ApprovalState.REJECTED),
                decided_by=self.PI,
                decided_at=NOW,
                reason="second",
                read_by=self.PI,
            )
        assert catalog.get_approval(gate.approval_id).decision_reason == "first"

    def test_a_gate_is_recognised_as_opened_before_anything_is_written_to_it(self, catalog):
        catalog.set_scope(PROJECT_ID)
        assert catalog.approval_exists("apr_cas_01") is False
        gate = self._gate(catalog)
        assert catalog.approval_exists(gate.approval_id) is True

        #: The second ask carries a different requester and holder, but it is the same subject: the row
        #: a person is accountable to stays the row they answer (§9.2).
        catalog.record_approval(
            Approval(
                approval_id=gate.approval_id,
                run_id=RUN_ID,
                kind=ApprovalKind.PLAN,
                subject_digest=SHA,
                requested_by="user_analyst_02",
                assigned_to=self.DEPUTY,
                requested_at=NOW,
                sla_at=NOW + timedelta(days=3),
            )
        )
        stored = catalog.get_approval(gate.approval_id)
        assert (stored.state, stored.requested_by, stored.assigned_to) == (
            ApprovalState.PENDING,
            "user_analyst_01",
            self.PI,
        )

    def test_another_projects_gate_is_not_this_projects_gate(self, catalog):
        gate = self._gate(catalog)
        catalog.set_scope(OTHER_PROJECT_ID)
        assert catalog.approval_exists(gate.approval_id) is False


class TestAdHocToolGrants:
    """Section 9.6: one PI decision buys one execution of the exact bytes it covered."""

    APPROVAL_ID = "apr_adhoc_01"

    def _grant(self, **overrides) -> AdhocToolGrant:
        spec = {"tool_name": "project_qc_probe", "tool_version": "0.1.0", "task": "qc"}
        fields = {
            "project_id": PROJECT_ID,
            "run_id": RUN_ID,
            "node_id": "qc",
            "plan_id": "plan_001",
            "plan_version": "1",
            "approval_id": self.APPROVAL_ID,
            "tool_name": "project_qc_probe",
            "tool_version": "0.1.0",
            "spec": spec,
            "spec_sha256": sha256_of_json(spec),
            "requested_by": "user_analyst_01",
            "approver_id": "user_pi_01",
            "reason": "single submission for this project only",
            "approved_at": NOW,
            "expires_at": NOW + timedelta(days=7),
        }
        fields.update(overrides)
        fields["grant_id"] = new_tool_grant_id(
            fields["approval_id"], fields["tool_name"], fields["tool_version"], fields["node_id"]
        )
        return AdhocToolGrant(**fields)

    def _record(self, catalog, **overrides) -> AdhocToolGrant:
        catalog.set_scope(PROJECT_ID)
        grant = self._grant(**overrides)
        catalog.record_tool_grant(grant)
        return grant

    def test_a_grant_round_trips_with_the_bytes_it_covers(self, catalog):
        made = self._record(catalog)
        stored = catalog.get_tool_grant(made.grant_id)
        assert stored.spec == made.spec
        assert stored.spec_sha256 == sha256_of_json(stored.spec)
        assert stored.approver_id == "user_pi_01" and stored.requested_by == "user_analyst_01"
        found = catalog.find_tool_grant("project_qc_probe", "0.1.0", run_id=RUN_ID, node_id="qc")
        assert found is not None and found.grant_id == made.grant_id
        assert catalog.grant_of_approval(self.APPROVAL_ID).grant_id == made.grant_id

    def test_the_gate_that_bought_a_grant_cannot_buy_a_second_one(self, catalog):
        self._record(catalog)
        with pytest.raises(sqlite3.IntegrityError):
            catalog.set_scope(PROJECT_ID)
            #: A second node under the same decision is a different grant id, but one gate is still one
            #: permission, so the row it already bought is the row that stands.
            catalog.record_tool_grant(self._grant(node_id="cluster"))

    def test_a_grant_buys_one_execution_even_after_the_process_that_held_it_ends(self, catalog):
        made = self._record(catalog)
        assert catalog.spend_tool_grant(made.grant_id, at=NOW + timedelta(hours=1)).spent_at == NOW + timedelta(
            hours=1
        )
        assert catalog.spend_tool_grant(made.grant_id) is None
        assert catalog.list_tool_grants(live_only=True) == []
        assert [g.grant_id for g in catalog.list_tool_grants()] == [made.grant_id]

    def test_the_bytes_a_grant_covers_are_the_tool_it_names(self, catalog):
        with pytest.raises(ValidationError, match="its spec declares"):
            self._grant(spec={"tool_name": "something_else", "tool_version": "9.9.9", "task": "qc"})

    def test_a_grant_cannot_cite_a_digest_other_than_the_bytes_it_carries(self, catalog):
        with pytest.raises(ValidationError, match="does not match the spec"):
            self._grant(spec_sha256="ab" * 32)

    def test_a_grant_written_under_another_scope_is_refused_before_it_lands(self, catalog):
        catalog.set_scope(OTHER_PROJECT_ID)
        with pytest.raises(ScopeError):
            catalog.record_tool_grant(self._grant())

    def test_the_submitter_of_a_tool_is_never_the_name_on_its_grant(self, catalog):
        with pytest.raises(ValidationError, match="may not be the subject that submitted"):
            self._grant(approver_id="user_analyst_01")

    def test_a_grant_buys_nothing_once_its_window_has_passed(self, catalog):
        made = self._record(catalog)
        stale = self._record(catalog, approval_id="apr_adhoc_02", expires_at=NOW + timedelta(minutes=1))
        assert {g.grant_id for g in catalog.list_tool_grants(live_only=True)} == {made.grant_id, stale.grant_id}
        assert catalog.spend_tool_grant(stale.grant_id, at=NOW + timedelta(days=1)) is None
        assert catalog.spend_tool_grant(made.grant_id, at=NOW + timedelta(days=1)) is not None

    def test_another_projects_grant_is_not_this_projects_ledger(self, catalog):
        made = self._record(catalog)
        catalog.set_scope(OTHER_PROJECT_ID)
        assert catalog.grant_of_approval(self.APPROVAL_ID) is None
        assert catalog.list_tool_grants() == []
        assert catalog.spend_tool_grant(made.grant_id) is None
        with pytest.raises(LookupError):
            catalog.get_tool_grant(made.grant_id)


class TestExecutionReceipts:
    """Section 3.3: one attempt of one node has one answer, and the index keeps it that way."""

    def _receipt(self, **overrides) -> ExecutionReceipt:
        return receipt(**overrides)

    def _lookup(self, catalog, made: ExecutionReceipt) -> Optional[ExecutionReceipt]:
        return catalog.execution_receipt(
            made.run_id,
            made.node_id,
            made.attempt,
            tool_name=made.tool_name,
            tool_version=made.tool_version,
            input_digest=made.input_digest,
            parameters_sha256=made.parameters_sha256,
            registry_snapshot=made.registry_snapshot,
        )

    def test_a_receipt_round_trips_with_the_answer_it_records(self, catalog):
        made = self._receipt()
        catalog.record_execution_receipt(made)
        stored = self._lookup(catalog, made)
        assert stored is not None
        assert stored.outcome_digest == made.outcome_digest
        assert stored.output_asset_ids == ["as_0001"]
        assert stored.output_digests == {"as_0001": SHA}
        assert stored.executed_on == "node_laptop"
        assert stored.compute_minutes == 1.5
        assert stored.at == NOW

    def test_the_same_answer_twice_is_absorbed_once(self, catalog):
        made = self._receipt()
        first = catalog.record_execution_receipt(made)
        again = catalog.record_execution_receipt(made)
        assert again.outcome_digest == first.outcome_digest
        assert len(catalog.list_execution_receipts(RUN_ID)) == 1

    def test_an_attempt_that_answers_differently_is_refused(self, catalog):
        catalog.record_execution_receipt(self._receipt())
        with pytest.raises(ExecutionConflictError, match="one attempt has one answer"):
            catalog.record_execution_receipt(self._receipt(outcome_digest="9a" * 32))
        assert len(catalog.list_execution_receipts(RUN_ID)) == 1

    def test_an_attempt_of_a_re_planned_run_is_its_own_answer(self, catalog):
        """§9.2: a run may be re-planned, and the new plan's attempt is not the old plan's."""
        catalog.record_execution_receipt(self._receipt())
        replanned = self._receipt(parameters_sha256="9a" * 32, outcome_digest="9b" * 32)
        catalog.record_execution_receipt(replanned)
        assert len(catalog.list_execution_receipts(RUN_ID)) == 2
        assert self._lookup(catalog, replanned) is not None

    def test_another_projects_receipt_is_not_this_projects_record(self, catalog):
        catalog.record_execution_receipt(self._receipt())
        catalog.set_scope(OTHER_PROJECT_ID)
        assert self._lookup(catalog, self._receipt()) is None
        assert catalog.list_execution_receipts(RUN_ID) == []

    def test_a_receipt_cannot_be_rewritten(self, catalog):
        catalog.record_execution_receipt(self._receipt())
        with pytest.raises(sqlite3.IntegrityError):
            catalog.conn.execute(
                "update execution_receipt set outcome_digest = ? where project_id = ?",
                ("9a" * 32, PROJECT_ID),
            )


class TestIdentityDirectory:
    """The rows a login key is checked against (§11.2): one place, stamped, never a secret."""

    FIELDS = {
        "algorithm": "ed25519",
        "public_key": "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA\n-----END PUBLIC KEY-----\n",
        "fingerprint": "SHA256:AAAA",
        "roles": ["analyst"],
        "project_ids": [PROJECT_ID],
        "amr": ["pwd+mfa"],
        "solo_mode": False,
        "is_service_account": False,
        "label": "workstation",
        "created_by": "user_platform_admin_01",
    }

    def register(self, catalog, kid: str, **overrides):
        catalog.set_scope(PLATFORM_SCOPE)
        catalog.register_credential(kid=kid, subject_id="user_analyst_01", **{**self.FIELDS, **overrides})
        return kid

    def test_a_revocation_is_a_stamp_on_the_row_that_granted(self, catalog):
        kid = self.register(catalog, "key_1")
        row = catalog.active_credential(kid)
        assert row["roles"] == ["analyst"] and row["revoked_at"] is None
        #: Whether a key belongs to a person or to a pipeline is a fact of the row, because §11.4 reads
        #: the human-only gate from the session this row is turned into.
        assert row["is_service_account"] is False
        engine = self.register(
            catalog, "key_engine", roles=["service_agent"], is_service_account=True, fingerprint="SHA256:ENGINE"
        )
        assert catalog.active_credential(engine)["is_service_account"] is True
        revoked = catalog.revoke_credential(kid, revoked_by="user_platform_admin_02")
        assert revoked["revoked_by"] == "user_platform_admin_02" and revoked["revoked_at"] == NOW
        #: The key stops working, the record does not disappear - an audit of who held what survives.
        assert catalog.active_credential(kid) is None
        assert catalog.get_credential(kid)["public_key"] == self.FIELDS["public_key"]
        with pytest.raises(LookupError):
            catalog.revoke_credential(kid, revoked_by="user_platform_admin_02")

    def test_two_keys_for_one_subject_are_two_rows_and_one_key_material_is_not_two(self, catalog):
        first = self.register(catalog, "key_1")
        second = self.register(catalog, "key_2", fingerprint="SHA256:BBBB")
        assert {row["kid"] for row in catalog.list_credentials(subject_id="user_analyst_01", limit=10)} == {
            first,
            second,
        }
        with pytest.raises(sqlite3.IntegrityError):
            self.register(catalog, "key_3")

    def test_a_project_scoped_session_cannot_read_the_directory(self, catalog):
        """A scoped read here would return the one project's keys and call that the whole list."""
        self.register(catalog, "key_1")
        catalog.set_scope(PROJECT_ID)
        with pytest.raises(ScopeError):
            catalog.list_credentials(limit=10)
        with pytest.raises(ScopeError):
            catalog.active_credential("key_1")


class TestComputeNodes:
    """Section 4.2: a node serves every project, so its registry is platform-level."""

    def test_a_declared_node_round_trips_with_its_capabilities(self, catalog):
        catalog.set_scope(PLATFORM_SCOPE)
        catalog.register_node(execution_node())
        stored = catalog.get_node("node_laptop")
        assert stored.label == "workstation"
        assert stored.position is NodePosition.LOCAL
        assert (stored.capabilities.cpu, stored.capabilities.memory_mb) == (8, 16384)
        assert stored.capabilities.can_run_containers is True
        assert stored.registered_by == "user_platform_admin_01"
        assert stored.registered_at == NOW

    def test_a_node_is_not_one_projects_property(self, catalog):
        catalog.set_scope(PLATFORM_SCOPE)
        catalog.register_node(execution_node())
        catalog.set_scope(PROJECT_ID)
        #: A scoped read would show one project's idea of the hardware and call it the hardware.
        with pytest.raises(ScopeError):
            catalog.list_nodes()
        with pytest.raises(ScopeError):
            catalog.get_node("node_laptop")
        with pytest.raises(ScopeError):
            catalog.register_node(execution_node(node_id="node_server"))

    def test_every_declared_node_is_listed_in_a_stable_order(self, catalog):
        catalog.set_scope(PLATFORM_SCOPE)
        catalog.register_node(execution_node("node_server", position=NodePosition.PRIVATE))
        catalog.register_node(execution_node("node_laptop"))
        assert [n.node_id for n in catalog.list_nodes()] == ["node_laptop", "node_server"]
        assert [n.node_id for n in catalog.list_nodes(limit=1)] == ["node_laptop"]
        assert [n.node_id for n in catalog.list_nodes(limit=1, offset=1)] == ["node_server"]

    def test_a_missing_node_is_not_in_the_registry(self, catalog):
        catalog.set_scope(PLATFORM_SCOPE)
        with pytest.raises(LookupError):
            catalog.get_node("node_missing")


def evidence_invocation():
    from bioadapt_schemas.entities import ModelInvocation

    return ModelInvocation(
        model_id="flagship-2026-04",
        purpose="claim_extraction",
        sampling_params={"temperature": 0.0},
        prompt_sha256="e" * 64,
        output_sha256="f" * 64,
        input_tokens=120,
        output_tokens=240,
        cost_usd=0.01,
        policy_decision="dec_0001",
    )
