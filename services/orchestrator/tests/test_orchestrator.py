"""The orchestrator over a real journal (plan V2.0 sections 9.1, 9.2, 9.7, 11.6, 13.1)."""

import pytest
from bioadapt_schemas.entities import sha256_of_json
from bioadapt_schemas.enums import ApprovalKind, ApprovalState, RunEventKind, RunState, SubjectKind
from bioadapt_schemas.plan import LoopBudget, RetryPolicy
from conftest import (
    AGENT,
    ANALYST,
    DEPUTY,
    HUMAN_ANALYST,
    HUMAN_DEPUTY,
    HUMAN_EXPERT,
    HUMAN_PI,
    NODE_ID,
    PI,
    RUN_ID,
    SHA,
    gated_node,
    manifest,
    node,
    plan,
    plan_digest,
    walk,
)

from bioadapt_orchestrator.errors import (
    ActorNotAllowed,
    ApprovalRefused,
    ApprovalRequired,
    BackoffPending,
    IllegalTransition,
    RetryRefused,
)

CHAIN = [
    RunState.PREFLIGHT_RUNNING,
    RunState.PREFLIGHT_PASSED,
    RunState.PLAN_DRAFTED,
]

#: The cluster step plus a gated discovery step: the deliverable cannot be readied until the gate is signed off.
WITH_GATE = [node(), gated_node()]


def ready(orchestrator, *, nodes=None, budget=None, version="1"):
    """Record the plan and create the run on it; the plan gate is a separate, human step."""
    orchestrator.catalog.record_plan(
        plan(nodes=nodes or plan().nodes, budget=budget, plan_version=version), created_by=ANALYST
    )
    orchestrator.catalog.record_run(manifest(plan_version=version), state=str(RunState.CREATED))
    return orchestrator


# --------------------------------------------------------------------- the journal


def test_every_transition_records_who_when_why_and_over_what(catalog, orchestrator):
    catalog.record_plan(plan(), created_by=ANALYST)
    catalog.record_run(manifest(), state=str(RunState.CREATED))
    result = orchestrator.transition(RUN_ID, RunState.PREFLIGHT_RUNNING, actor=AGENT, reason="preflight started")

    event = result.event
    assert event.kind is RunEventKind.STATE
    assert event.actor_id == "svc_pipeline" and event.actor_kind is SubjectKind.SERVICE
    assert event.reason == "preflight started"
    assert event.at == catalog.clock()
    assert event.input_hash == sha256_of_json(manifest().input_hashes)
    assert event.versions == {
        "plan_id": "plan_001",
        "plan_version": "1",
        "tool_registry_snapshot": "snap_0001",
        "tool_versions": {"scanpy_pipeline": "1.11.0"},
    }


def test_the_state_and_its_journal_line_are_written_together(running):
    assert running.state_of(RUN_ID) is RunState.ANALYSIS_RUNNING
    journal = running.journal(RUN_ID)
    assert [e.to_state for e in journal if e.kind is RunEventKind.STATE] == [
        state for state in CHAIN + [RunState.PLAN_APPROVED, RunState.ANALYSIS_RUNNING]
    ]


def test_a_transition_the_plan_does_not_describe_is_refused(running):
    with pytest.raises(IllegalTransition, match="cannot go ANALYSIS_RUNNING -> ARCHIVED"):
        running.transition(RUN_ID, RunState.ARCHIVED, actor=HUMAN_PI, reason="ship it")
    assert running.state_of(RUN_ID) is RunState.ANALYSIS_RUNNING


def test_a_repeated_callback_is_answered_from_the_journal(running):
    first = running.transition(RUN_ID, RunState.QC_REVIEW, actor=AGENT, reason="qc started", attempt=1)
    again = running.transition(
        RUN_ID, RunState.QC_REVIEW, actor=AGENT, reason="qc started", attempt=1,
        from_state=RunState.ANALYSIS_RUNNING,
    )
    assert first.replayed is False and again.replayed is True
    assert again.event.event_id == first.event.event_id
    assert len(running.journal(RUN_ID, kind=str(RunEventKind.STATE))) == 6
    assert running.state_of(RUN_ID) is RunState.QC_REVIEW


def test_a_callback_that_claims_a_stale_state_is_refused(running):
    with pytest.raises(IllegalTransition, match="claims it was in"):
        running.transition(
            RUN_ID, RunState.QC_REVIEW, actor=AGENT, reason="late callback", from_state=RunState.CREATED
        )


def test_the_journal_is_paged_in_order(running):
    walk(running, [RunState.QC_REVIEW, RunState.CANDIDATE_DISCOVERY])
    page = running.journal(RUN_ID, limit=2)
    assert len(page) == 2 and page[0].to_state is RunState.PREFLIGHT_RUNNING


# --------------------------------------------------------------------- approvals


def test_plan_approval_only_reflects_a_decision_a_person_made(catalog, orchestrator):
    catalog.record_plan(plan(), created_by=ANALYST)
    catalog.record_run(manifest(), state=str(RunState.CREATED))
    walk(orchestrator, CHAIN)
    with pytest.raises(ApprovalRequired, match="has not been requested"):
        orchestrator.transition(RUN_ID, RunState.PLAN_APPROVED, actor=AGENT, reason="run the plan")

    approval_id = request(orchestrator)
    with pytest.raises(ApprovalRequired, match="is pending"):
        orchestrator.transition(
            RUN_ID, RunState.PLAN_APPROVED, actor=AGENT, reason="run the plan", approval_id=approval_id
        )

    orchestrator.decide_approval(approval_id, actor=HUMAN_PI, approved=True, reason="design is sound")
    result = orchestrator.transition(
        RUN_ID, RunState.PLAN_APPROVED, actor=AGENT, reason="run the plan", approval_id=approval_id
    )
    assert result.state is RunState.PLAN_APPROVED
    assert orchestrator.approval_of(approval_id).state is ApprovalState.APPROVED


def request(orchestrator, *, kind=ApprovalKind.PLAN, node_id=None, actor=HUMAN_ANALYST, assigned_to=PI, plan_version=None):
    """Ask for a gate. The answer is a person's to give, so the approval is still open here."""
    approval = orchestrator.request_approval(
        RUN_ID, kind, actor=actor, node_id=node_id, assigned_to=assigned_to, plan_version=plan_version
    )
    return approval.approval_id


def test_a_granted_approval_does_not_survive_a_change_to_what_it_covered(catalog, orchestrator):
    catalog.record_plan(plan(), created_by=ANALYST)
    catalog.record_plan(plan(plan_version="2"), created_by=ANALYST)
    catalog.record_run(manifest(), state=str(RunState.CREATED))
    approval_id = approve_and_grant(orchestrator)
    catalog.set_run_plan(RUN_ID, plan_version="2", tool_versions={"scanpy_pipeline": "1.11.0"})
    walk(orchestrator, CHAIN)
    with pytest.raises(ApprovalRequired, match="does not cover"):
        orchestrator.transition(
            RUN_ID, RunState.PLAN_APPROVED, actor=AGENT, reason="run the plan", approval_id=approval_id
        )


def test_a_rejected_approval_is_not_walked_around(catalog, orchestrator):
    catalog.record_plan(plan(), created_by=ANALYST)
    catalog.record_run(manifest(), state=str(RunState.CREATED))
    approval_id = request(orchestrator)
    orchestrator.decide_approval(approval_id, actor=HUMAN_PI, approved=False, reason="design aliases batch")
    walk(orchestrator, CHAIN)
    with pytest.raises(ApprovalRequired, match="rejected"):
        orchestrator.transition(
            RUN_ID, RunState.PLAN_APPROVED, actor=AGENT, reason="run it anyway", approval_id=approval_id
        )


def open_gate(orchestrator) -> str:
    """A gate nobody has answered yet. The plan gate of a running run is already decided, so these
    tests use a node gate: an approval is addressed by its subject, not by a fresh id per request."""
    return request(orchestrator, kind=ApprovalKind.NODE, node_id=NODE_ID)


def test_an_agent_cannot_record_an_approval_decision(running):
    approval_id = open_gate(running)
    with pytest.raises(ApprovalRefused, match="human decision"):
        running.decide_approval(approval_id, actor=AGENT, approved=True, reason="self approval")
    assert running.approval_of(approval_id).state is ApprovalState.PENDING


def test_the_submitter_cannot_be_the_approver_of_their_own_gate(running):
    with pytest.raises(ValueError, match="accountable"):
        request(running, actor=HUMAN_PI, assigned_to=PI)


def test_only_the_accountable_approver_or_their_delegate_may_decide(running):
    approval_id = open_gate(running)
    with pytest.raises(ApprovalRefused, match="accountable to"):
        running.decide_approval(approval_id, actor=HUMAN_DEPUTY, approved=True, reason="I am in a hurry")
    running.delegate_approval(approval_id, actor=HUMAN_PI, to_subject=DEPUTY)
    decided = running.decide_approval(approval_id, actor=HUMAN_DEPUTY, approved=True, reason="checked the design")
    assert decided.assigned_to == DEPUTY and decided.delegated_from == PI
    assert decided.delegated_at == decided.decided_at


def test_a_lapsed_window_escalates_without_changing_the_decision(running, clock):
    approval_id = open_gate(running)
    assert running.overdue_approvals() == []
    clock.advance(days=6)
    overdue = running.overdue_approvals()
    assert [a.approval_id for a in overdue] == [approval_id]
    assert running.approval_of(approval_id).state is ApprovalState.PENDING
    # The window having lapsed does not authorise anyone: the delegate route is how it gets decided.
    with pytest.raises(ApprovalRefused, match="accountable to"):
        running.decide_approval(approval_id, actor=HUMAN_EXPERT, approved=True, reason="overdue so I will")


def test_a_lapsed_window_is_reported_with_the_time_it_was_open_past(running, clock):
    approval_id = open_gate(running)
    clock.advance(days=6)
    overdue = running.overdue_approvals()[0]
    assert running.policy.overdue_reason(overdue, clock()) == "open 24 h past its 3-working-day window"
    assert running.approval_of(approval_id).state is ApprovalState.PENDING


def test_the_overdue_inbox_pages_from_the_index_and_agrees_with_the_window(running, clock):
    """The lapsed-window read moved into the index; it must still say what ``is_overdue`` says."""
    node_gate = open_gate(running)
    clock.advance(days=6)
    running.catalog.record_plan(plan(plan_version="2"), created_by=ANALYST)
    plan_gate = request(running, kind=ApprovalKind.PLAN, plan_version="2")
    assert [a.approval_id for a in running.overdue_approvals()] == [node_gate]

    clock.advance(days=6)
    expected = [
        a.approval_id
        for a in running.pending_approvals(state=ApprovalState.PENDING)
        if running.policy.is_overdue(a, clock())
    ]
    assert [a.approval_id for a in running.overdue_approvals()] == expected == [node_gate, plan_gate]
    assert [a.approval_id for a in running.overdue_approvals(limit=1)] == [node_gate]
    assert [a.approval_id for a in running.overdue_approvals(limit=1, offset=1)] == [plan_gate]
    assert running.overdue_approvals(offset=2) == []


def test_the_inbox_pages_open_gates_without_reading_the_whole_table(running, clock):
    running.catalog.record_plan(plan(plan_version="2"), created_by=ANALYST)
    node_gate = open_gate(running)
    clock.advance(days=1)
    plan_gate = request(running, kind=ApprovalKind.PLAN, plan_version="2")
    pending = {"state": ApprovalState.PENDING}
    assert [a.approval_id for a in running.pending_approvals(limit=1, **pending)] == [node_gate]
    assert [a.approval_id for a in running.pending_approvals(limit=1, offset=1, **pending)] == [plan_gate]
    assert running.pending_approvals(offset=2, **pending) == []
    #: the state filter is what keeps a decided gate off the page; without it the list is every gate
    assert [a.approval_id for a in running.pending_approvals(**pending)] == [node_gate, plan_gate]


def test_the_digest_an_approval_was_recorded_over_is_the_plan_digest(running):
    approval_id = request(running)
    approval = running.approval_of(approval_id)
    assert approval.subject_digest == plan_digest(running.plan_of(RUN_ID))
    assert approval.subject_digest == running.catalog.plan_digest("plan_001", "1")


# --------------------------------------------------------------------- agents (section 9.1)


def test_an_agent_may_not_end_a_run_or_ready_its_deliverable(running):
    with pytest.raises(ActorNotAllowed, match="only a person"):
        running.cancel(RUN_ID, actor=AGENT, reason="giving up on it")
    assert running.state_of(RUN_ID) is RunState.ANALYSIS_RUNNING
    assert running.cancel(RUN_ID, actor=HUMAN_PI, reason="the donor coverage is too thin to continue").state is RunState.FAILED
    assert running.catalog.get_run(RUN_ID).finished_at == running.catalog.clock()


def test_a_finished_run_never_moves_again(running):
    running.cancel(RUN_ID, actor=HUMAN_PI, reason="cancelled")
    with pytest.raises(IllegalTransition):
        running.transition(RUN_ID, RunState.ANALYSIS_RUNNING, actor=HUMAN_PI, reason="change of mind")


# --------------------------------------------------------------------- node gates


def test_a_gated_node_may_not_run_until_its_approval_is_granted(catalog, orchestrator):
    ready(orchestrator, nodes=WITH_GATE)
    approve_and_grant(orchestrator)
    walk(orchestrator, CHAIN + [RunState.PLAN_APPROVED, RunState.ANALYSIS_RUNNING])
    with pytest.raises(ApprovalRequired, match="has not been requested"):
        orchestrator.require_node_approval(RUN_ID, "discover")

    gate = approve_and_grant(orchestrator, ApprovalKind.NODE, node_id="discover")
    assert orchestrator.require_node_approval(RUN_ID, "discover").approval_id == gate


def approve_and_grant(orchestrator, kind=ApprovalKind.PLAN, *, node_id=None, plan_version=None):
    approval_id = request(orchestrator, kind=kind, node_id=node_id, plan_version=plan_version)
    orchestrator.decide_approval(approval_id, actor=HUMAN_PI, approved=True, reason="approved for this project")
    return approval_id


def test_a_deliverable_needs_every_gated_node_signed_off(catalog, orchestrator):
    ready(orchestrator, nodes=WITH_GATE)
    approve_and_grant(orchestrator)
    walk(
        orchestrator,
        CHAIN
        + [
            RunState.PLAN_APPROVED,
            RunState.ANALYSIS_RUNNING,
            RunState.QC_REVIEW,
            RunState.CANDIDATE_DISCOVERY,
            RunState.EVIDENCE_BUILDING,
            RunState.VALIDATION,
            RunState.HUMAN_REVIEW,
        ],
    )
    with pytest.raises(ApprovalRequired, match="discover"):
        orchestrator.transition(RUN_ID, RunState.REPORT_READY, actor=HUMAN_EXPERT, reason="ready")
    approve_and_grant(orchestrator, ApprovalKind.NODE, node_id="discover")
    result = orchestrator.transition(RUN_ID, RunState.REPORT_READY, actor=HUMAN_EXPERT, reason="ready")
    assert result.state is RunState.REPORT_READY


def test_an_ungated_node_does_not_ask_for_an_approval(running):
    with pytest.raises(ValueError, match="needs no approval"):
        running.require_node_approval(RUN_ID, NODE_ID)


# --------------------------------------------------------------------- bounded loop


def to_the_loop(orchestrator, *, budget=None, nodes=None):
    ready(orchestrator, budget=budget, nodes=nodes)
    approve_and_grant(orchestrator)
    walk(
        orchestrator,
        CHAIN
        + [
            RunState.PLAN_APPROVED,
            RunState.ANALYSIS_RUNNING,
            RunState.QC_REVIEW,
            RunState.CANDIDATE_DISCOVERY,
            RunState.EVIDENCE_BUILDING,
        ],
    )
    return orchestrator


def test_the_loop_runs_to_its_budget_and_then_stops_for_a_person(catalog, orchestrator):
    to_the_loop(orchestrator)
    assert orchestrator.transition(RUN_ID, RunState.TARGETED_ANALYSIS, actor=AGENT, reason="pass 1").state is RunState.TARGETED_ANALYSIS
    assert orchestrator.transition(RUN_ID, RunState.TARGETED_ANALYSIS, actor=AGENT, reason="pass 2").state is RunState.TARGETED_ANALYSIS

    third = orchestrator.transition(RUN_ID, RunState.TARGETED_ANALYSIS, actor=AGENT, reason="pass 3")
    assert third.escalated is True
    assert third.state is RunState.ESCALATED_TO_HUMAN
    assert "iterations 3 > 2" in third.reasons[0]
    assert orchestrator.state_of(RUN_ID) is RunState.ESCALATED_TO_HUMAN


def test_a_loop_entry_is_counted_even_when_it_lands_forward(catalog, orchestrator):
    to_the_loop(orchestrator, budget=LoopBudget(max_iterations=1, max_compute_minutes=600, max_token_cost_usd=9.0))
    assert orchestrator.transition(RUN_ID, RunState.TARGETED_ANALYSIS, actor=AGENT, reason="one pass").escalated is False
    assert orchestrator.transition(RUN_ID, RunState.VALIDATION, actor=AGENT, reason="done looping").state is RunState.VALIDATION
    assert orchestrator.usage(RUN_ID).iterations == 1


def test_spend_past_the_plan_ceiling_stops_the_run(catalog, orchestrator):
    to_the_loop(orchestrator)
    result = orchestrator.report_usage(
        RUN_ID, NODE_ID, actor=AGENT, compute_minutes=200.0, model_cost_usd=1.0
    )
    assert result.escalated is True
    assert "compute minutes 200 of 180" in result.reasons[0]
    assert orchestrator.usage(RUN_ID).compute_minutes == 200.0


def test_the_project_quota_caps_the_loop_below_what_the_plan_asked_for(catalog, orchestrator, ceilings):
    ceilings.max_compute_minutes = 90
    to_the_loop(orchestrator, budget=LoopBudget(max_iterations=5, max_compute_minutes=600, max_token_cost_usd=9.0))
    result = orchestrator.report_usage(RUN_ID, NODE_ID, actor=AGENT, compute_minutes=95.0)
    assert result.escalated is True and "of 90" in result.reasons[0]


def test_resuming_an_escalated_run_needs_a_bigger_approved_budget(catalog, orchestrator):
    to_the_loop(orchestrator)
    orchestrator.transition(RUN_ID, RunState.TARGETED_ANALYSIS, actor=AGENT, reason="pass 1")
    orchestrator.transition(RUN_ID, RunState.TARGETED_ANALYSIS, actor=AGENT, reason="pass 2")
    orchestrator.transition(RUN_ID, RunState.TARGETED_ANALYSIS, actor=AGENT, reason="pass 3")

    with pytest.raises(ApprovalRequired, match="escalation approval"):
        orchestrator.transition(RUN_ID, RunState.TARGETED_ANALYSIS, actor=HUMAN_PI, reason="one more pass")

    catalog.record_plan(
        plan(
            plan_version="2",
            budget=LoopBudget(max_iterations=4, max_compute_minutes=180, max_token_cost_usd=5.0),
        ),
        created_by=ANALYST,
    )
    gate = orchestrator.request_approval(
        RUN_ID, ApprovalKind.ESCALATION, actor=HUMAN_ANALYST, plan_version="2"
    )
    with pytest.raises(ApprovalRequired, match="is pending"):
        orchestrator.transition(
            RUN_ID,
            RunState.TARGETED_ANALYSIS,
            actor=HUMAN_PI,
            reason="one more pass",
            approval_id=gate.approval_id,
            resume_with_plan_version="2",
        )

    orchestrator.decide_approval(gate.approval_id, actor=HUMAN_PI, approved=True, reason="one more pass, funded")
    result = orchestrator.transition(
        RUN_ID,
        RunState.TARGETED_ANALYSIS,
        actor=HUMAN_PI,
        reason="one more pass",
        approval_id=gate.approval_id,
        resume_with_plan_version="2",
    )
    assert result.state is RunState.TARGETED_ANALYSIS
    assert catalog.get_run(RUN_ID).plan_version == "2"
    assert catalog.get_run(RUN_ID).tool_versions == {"scanpy_pipeline": "1.11.0"}


def test_an_escalation_approval_may_not_lower_a_ceiling(catalog, orchestrator):
    to_the_loop(orchestrator)
    for pass_number in (1, 2, 3):
        orchestrator.transition(RUN_ID, RunState.TARGETED_ANALYSIS, actor=AGENT, reason=f"pass {pass_number}")
    catalog.record_plan(
        plan(
            plan_version="2",
            budget=LoopBudget(max_iterations=4, max_compute_minutes=60, max_token_cost_usd=5.0),
        ),
        created_by=ANALYST,
    )
    gate = orchestrator.request_approval(
        RUN_ID, ApprovalKind.ESCALATION, actor=HUMAN_ANALYST, plan_version="2"
    )
    orchestrator.decide_approval(gate.approval_id, actor=HUMAN_PI, approved=True, reason="fewer, cheaper passes")
    with pytest.raises(ApprovalRequired, match="may not lower a budget ceiling"):
        orchestrator.transition(
            RUN_ID,
            RunState.TARGETED_ANALYSIS,
            actor=HUMAN_PI,
            reason="restart",
            approval_id=gate.approval_id,
            resume_with_plan_version="2",
        )


def test_an_escalation_approval_only_covers_the_plan_version_it_named(catalog, orchestrator):
    to_the_loop(orchestrator)
    for pass_number in (1, 2, 3):
        orchestrator.transition(RUN_ID, RunState.TARGETED_ANALYSIS, actor=AGENT, reason=f"pass {pass_number}")
    catalog.record_plan(
        plan(plan_version="2", budget=LoopBudget(max_iterations=4, max_compute_minutes=180, max_token_cost_usd=9.0)),
        created_by=ANALYST,
    )
    gate = orchestrator.request_approval(
        RUN_ID, ApprovalKind.ESCALATION, actor=HUMAN_ANALYST, plan_version="1"
    )
    orchestrator.decide_approval(gate.approval_id, actor=HUMAN_PI, approved=True, reason="wrong version")
    with pytest.raises(ApprovalRequired, match="different plan version"):
        orchestrator.transition(
            RUN_ID,
            RunState.TARGETED_ANALYSIS,
            actor=HUMAN_PI,
            reason="restart",
            approval_id=gate.approval_id,
            resume_with_plan_version="2",
        )


# --------------------------------------------------------------------- retries


def test_node_level_retries_are_registered_and_bounded_by_the_plan(catalog, orchestrator):
    ready(orchestrator, nodes=[node_with_policy(RetryPolicy(max_attempts=3, backoff_seconds=30))])
    approve_and_grant(orchestrator)
    walk(orchestrator, CHAIN + [RunState.PLAN_APPROVED, RunState.ANALYSIS_RUNNING])

    first = orchestrator.register_node_retry(RUN_ID, NODE_ID, attempt=1, actor=AGENT, reason="transient io")
    assert first.state is RunState.ANALYSIS_RUNNING and first.tolerance_exhausted is False
    third = orchestrator.register_node_retry(RUN_ID, NODE_ID, attempt=3, actor=AGENT, reason="still transient")
    assert third.tolerance_exhausted is True
    with pytest.raises(RetryRefused, match="max_attempts 3"):
        orchestrator.register_node_retry(RUN_ID, NODE_ID, attempt=4, actor=AGENT, reason="one more for luck")
    assert orchestrator.state_of(RUN_ID) is RunState.ANALYSIS_RUNNING


def node_with_policy(policy: RetryPolicy):
    return node(retry_policy=policy)


def test_a_retry_while_the_run_is_waiting_is_not_registered_as_engine_tolerance(catalog, orchestrator):
    ready(orchestrator)
    approve_and_grant(orchestrator)
    walk(orchestrator, CHAIN + [RunState.PLAN_APPROVED, RunState.ANALYSIS_RUNNING])
    orchestrator.transition(RUN_ID, RunState.NEEDS_INPUT, actor=AGENT, reason="donor metadata missing")
    with pytest.raises(IllegalTransition, match="while the run is working"):
        orchestrator.register_node_retry(RUN_ID, NODE_ID, attempt=1, actor=AGENT, reason="transient io")


def test_a_business_retry_waits_for_the_backoff_the_plan_set(catalog, orchestrator, clock):
    ready(orchestrator, nodes=[node_with_policy(RetryPolicy(max_attempts=2, backoff_seconds=120))])
    approve_and_grant(orchestrator)
    walk(orchestrator, CHAIN + [RunState.PLAN_APPROVED, RunState.ANALYSIS_RUNNING])
    orchestrator.transition(RUN_ID, RunState.RETRYABLE_FAILURE, actor=AGENT, reason="the container died")

    with pytest.raises(BackoffPending) as refused:
        orchestrator.business_retry(RUN_ID, NODE_ID, attempt=1, actor=AGENT, reason="start it again")
    assert refused.value.retry_after == 120
    assert orchestrator.state_of(RUN_ID) is RunState.RETRYABLE_FAILURE

    clock.advance(seconds=121)
    result = orchestrator.business_retry(RUN_ID, NODE_ID, attempt=1, actor=AGENT, reason="start it again")
    assert result.state is RunState.ANALYSIS_RUNNING
    kinds = [e.kind for e in orchestrator.journal(RUN_ID)]
    assert RunEventKind.BUSINESS_RETRY in kinds and kinds.count(RunEventKind.STATE) == 7


def test_a_business_retry_beyond_the_planned_attempts_is_refused(catalog, orchestrator):
    ready(orchestrator, nodes=[node_with_policy(RetryPolicy(max_attempts=2, backoff_seconds=0))])
    approve_and_grant(orchestrator)
    walk(orchestrator, CHAIN + [RunState.PLAN_APPROVED, RunState.ANALYSIS_RUNNING])
    for attempt in (1, 2):
        # The engine counts its own restarts, so the failure edge carries the attempt it belongs to.
        orchestrator.transition(
            RUN_ID, RunState.RETRYABLE_FAILURE, actor=AGENT, reason="the container died", attempt=attempt
        )
        orchestrator.business_retry(RUN_ID, NODE_ID, attempt=attempt, actor=AGENT, reason="retry")
    orchestrator.transition(
        RUN_ID, RunState.RETRYABLE_FAILURE, actor=AGENT, reason="the container died again", attempt=3
    )
    with pytest.raises(RetryRefused, match="max_attempts 2"):
        orchestrator.business_retry(RUN_ID, NODE_ID, attempt=3, actor=AGENT, reason="retry")
    assert orchestrator.state_of(RUN_ID) is RunState.RETRYABLE_FAILURE


def test_a_needs_input_branch_returns_to_the_state_it_stopped_in(catalog, orchestrator):
    ready(orchestrator)
    approve_and_grant(orchestrator)
    walk(orchestrator, CHAIN + [RunState.PLAN_APPROVED, RunState.ANALYSIS_RUNNING])
    orchestrator.transition(RUN_ID, RunState.NEEDS_INPUT, actor=AGENT, reason="donor metadata missing")
    with pytest.raises(IllegalTransition, match="cannot go NEEDS_INPUT -> QC_REVIEW"):
        orchestrator.transition(RUN_ID, RunState.QC_REVIEW, actor=AGENT, reason="guessed it")
    assert (
        orchestrator.transition(RUN_ID, RunState.ANALYSIS_RUNNING, actor=HUMAN_ANALYST, reason="metadata supplied").state
        is RunState.ANALYSIS_RUNNING
    )


# --------------------------------------------------------------------- quotas and inputs


def test_spend_is_reported_against_a_working_run_only(catalog, orchestrator):
    ready(orchestrator)
    approve_and_grant(orchestrator)
    walk(orchestrator, CHAIN + [RunState.PLAN_APPROVED, RunState.ANALYSIS_RUNNING])
    orchestrator.cancel(RUN_ID, actor=HUMAN_PI, reason="study stopped")
    with pytest.raises(IllegalTransition, match="FAILED"):
        orchestrator.report_usage(RUN_ID, NODE_ID, actor=AGENT, compute_minutes=10)


def test_a_duplicate_usage_report_does_not_double_charge(catalog, orchestrator):
    ready(orchestrator)
    approve_and_grant(orchestrator)
    walk(orchestrator, CHAIN + [RunState.PLAN_APPROVED, RunState.ANALYSIS_RUNNING])
    orchestrator.report_usage(RUN_ID, NODE_ID, actor=AGENT, compute_minutes=10, attempt=1)
    again = orchestrator.report_usage(RUN_ID, NODE_ID, actor=AGENT, compute_minutes=10, attempt=1)
    assert again.replayed is True
    assert orchestrator.usage(RUN_ID).compute_minutes == 10


def test_the_input_hash_of_a_transition_can_be_the_engine_s_own(catalog, orchestrator):
    ready(orchestrator)
    result = orchestrator.transition(
        RUN_ID, RunState.PREFLIGHT_RUNNING, actor=AGENT, reason="inputs resolved", input_hash=SHA
    )
    assert result.event.input_hash == SHA
