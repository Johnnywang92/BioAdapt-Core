"""Orchestration routes: the journal, the gates and both retry sides (sections 9.1, 9.2, 9.7, 11.6).

These tests drive the API only: what a workflow engine can do with a service token, what only a
person can do, and what the run's journal says afterwards about the difference.
"""

from bioadapt_policies.access import Role
from bioadapt_schemas.enums import ApprovalState, RunEventKind, RunState, SubjectKind
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.plan import LoopBudget
from conftest import ANALYST, COMPLIANCE, EXPERT, PI, PROJECT_ID, RUN_ID, SERVICE, node, plan

BASE = f"/v1/projects/{PROJECT_ID}"
PI_ROLE = [Role.PRINCIPAL_INVESTIGATOR]
ANALYST_ROLE = [Role.ANALYST]


def engine(auth):
    """The workflow engine's token: it reports what happened, it does not decide what may happen."""
    return auth(SERVICE, [Role.SERVICE_AGENT], service=True)


def move(client, auth, to_state, *, headers=None, **body):
    fields = {"to_state": str(to_state), "reason": "engine reported progress"}
    fields.update(body)
    return client.post(
        f"{BASE}/runs/{RUN_ID}/transitions", json=fields, headers=headers or engine(auth)
    )


def open_plan_gate(client, auth):
    """An analyst asks for the design to be signed off; a person who is not they answers."""
    requested = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "plan"},
        headers=auth(ANALYST, ANALYST_ROLE),
    )
    assert requested.status_code == 201, requested.text
    approval_id = requested.json()["approval_id"]
    decided = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "design is sound"},
        headers=auth(PI, PI_ROLE),
    )
    assert decided.status_code == 200, decided.text
    return approval_id


def to_analysis(client, auth, *, approval_id=None):
    for state in (RunState.PREFLIGHT_RUNNING, RunState.PREFLIGHT_PASSED, RunState.PLAN_DRAFTED):
        assert move(client, auth, state).status_code == 201
    granted = client.post(
        f"{BASE}/runs/{RUN_ID}/transitions",
        json={"to_state": str(RunState.PLAN_APPROVED), "reason": "run the signed-off design", "approval_id": approval_id},
        headers=engine(auth),
    )
    assert granted.status_code == 201, granted.text
    assert move(client, auth, RunState.ANALYSIS_RUNNING).status_code == 201
    return approval_id


# --------------------------------------------------------------------- transitions


def test_the_engine_moves_a_run_and_the_journal_says_who_acted(client, auth):
    moved = move(client, auth, RunState.PREFLIGHT_RUNNING, reason="preflight started")
    assert moved.status_code == 201
    body = moved.json()
    assert body["state"] == str(RunState.PREFLIGHT_RUNNING)
    event = body["event"]
    assert event["actor_id"] == SERVICE and event["actor_kind"] == str(SubjectKind.SERVICE)
    assert event["reason"] == "preflight started"
    assert event["kind"] == str(RunEventKind.STATE)
    assert client.get(f"{BASE}/runs/{RUN_ID}", headers=auth(ANALYST, ANALYST_ROLE)).json()["state"] == str(
        RunState.PREFLIGHT_RUNNING
    )


def test_a_transition_without_a_reason_is_not_accepted(client, auth):
    response = client.post(
        f"{BASE}/runs/{RUN_ID}/transitions", json={"to_state": str(RunState.PREFLIGHT_RUNNING)}, headers=engine(auth)
    )
    assert response.status_code == 400
    assert response.json()["field"] == "reason"


def test_a_transition_the_plan_does_not_describe_is_a_conflict(client, auth):
    response = move(client, auth, RunState.REPORT_READY, headers=auth(EXPERT, [Role.DOMAIN_EXPERT]))
    assert response.status_code == 409
    assert response.json()["class"] == str(ErrorClass.CONFLICT)
    assert "CREATED" in response.json()["message"]


def test_a_repeated_callback_is_answered_from_the_journal(client, auth):
    """A redelivery names the state it observed; without it the API cannot tell a duplicate from a rewind."""
    first = move(client, auth, RunState.PREFLIGHT_RUNNING, attempt=1, from_state=RunState.CREATED)
    again = move(client, auth, RunState.PREFLIGHT_RUNNING, attempt=1, from_state=RunState.CREATED)
    assert again.status_code == 200
    assert again.headers["Idempotency-Replayed"] == "true"
    assert again.json()["event"]["event_id"] == first.json()["event"]["event_id"]
    journal = client.get(f"{BASE}/runs/{RUN_ID}/transitions", headers=auth(ANALYST, ANALYST_ROLE)).json()
    assert len(journal["items"]) == 1

    blind = move(client, auth, RunState.PREFLIGHT_RUNNING, attempt=1)
    assert blind.status_code == 409
    assert "cannot go" in blind.json()["message"]


def test_a_callback_about_a_state_the_run_was_never_in_is_refused(client, auth):
    move(client, auth, RunState.PREFLIGHT_RUNNING)
    response = move(client, auth, RunState.PREFLIGHT_PASSED, from_state=RunState.PLAN_DRAFTED)
    assert response.status_code == 409
    assert "claims it was in" in response.json()["message"]


def test_a_move_to_a_terminal_state_is_a_persons_act(client, auth):
    refused = move(client, auth, RunState.FAILED)
    assert refused.status_code == 403
    assert refused.json()["class"] == str(ErrorClass.POLICY_BLOCKED)
    assert "only a person" in refused.json()["message"]

    ended = client.post(f"{BASE}/runs/{RUN_ID}/cancel", json={"reason": "the donor coverage is too thin"}, headers=auth(PI, PI_ROLE))
    assert ended.status_code == 201
    assert ended.json()["state"] == str(RunState.FAILED)
    assert client.get(f"{BASE}/runs/{RUN_ID}", headers=auth(PI, PI_ROLE)).json()["state"] == str(RunState.FAILED)


def test_a_service_token_cannot_cancel_a_run_at_all(client, auth):
    response = client.post(f"{BASE}/runs/{RUN_ID}/cancel", json={"reason": "give up"}, headers=engine(auth))
    assert response.status_code == 403
    assert "service_agent" in response.json()["message"]


def test_spend_and_cancellation_are_readable_without_the_engine_journal(client, auth):
    """§11.6: the journal belongs to the run; the compliance log has to name the spend and who ended it."""
    client.post(
        f"{BASE}/runs/{RUN_ID}/nodes/cluster/usage",
        json={"compute_minutes": 10, "model_cost_usd": 0.5, "reason": "the cluster step"},
        headers=engine(auth),
    )
    client.post(
        f"{BASE}/runs/{RUN_ID}/cancel", json={"reason": "the donor coverage is too thin"}, headers=auth(PI, PI_ROLE)
    )
    headers = auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER])
    spent = client.get(f"{BASE}/audit?action=run:usage", headers=headers).json()["items"]
    assert [row["details"]["compute_minutes"] for row in spent] == [10]
    assert spent[0]["subject_kind"] == "service"
    #: run:cancel is both the authorization row the enforcement writes and the outcome row this route adds.
    cancelled = client.get(f"{BASE}/audit?action=run:cancel", headers=headers).json()["items"]
    ended = [row for row in cancelled if "event_id" in row["details"]]
    assert [row["details"]["reason"] for row in ended] == ["the donor coverage is too thin"]
    assert ended[0]["subject_kind"] == "human"
    assert ended[0]["details"]["state"] == str(RunState.FAILED)


def test_a_journal_read_is_paged_and_filterable_by_kind(client, auth):
    for state in (RunState.PREFLIGHT_RUNNING, RunState.PREFLIGHT_PASSED):
        move(client, auth, state)
    page = client.get(f"{BASE}/runs/{RUN_ID}/transitions", params={"limit": 1}, headers=auth(ANALYST, ANALYST_ROLE)).json()
    assert len(page["items"]) == 1 and page["next_offset"] == 1
    assert page["items"][0]["to_state"] == str(RunState.PREFLIGHT_RUNNING)
    only_usage = client.get(
        f"{BASE}/runs/{RUN_ID}/transitions", params={"kind": str(RunEventKind.USAGE)}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()
    assert only_usage["items"] == []


def test_an_unknown_run_is_not_found(client, auth):
    response = client.post(
        f"{BASE}/runs/run_nope/transitions", json={"to_state": str(RunState.PREFLIGHT_RUNNING), "reason": "x"}, headers=engine(auth)
    )
    assert response.status_code == 404
    assert response.json()["class"] == str(ErrorClass.NOT_FOUND)


# --------------------------------------------------------------------- the plan gate


def test_the_plan_approved_state_only_reflects_a_decision_a_person_made(client, auth):
    for state in (RunState.PREFLIGHT_RUNNING, RunState.PREFLIGHT_PASSED, RunState.PLAN_DRAFTED):
        assert move(client, auth, state).status_code == 201
    missing = move(client, auth, RunState.PLAN_APPROVED)
    assert missing.status_code == 403
    assert missing.json()["class"] == str(ErrorClass.POLICY_BLOCKED)
    assert "has not been requested" in missing.json()["message"]

    approval_id = open_plan_gate(client, auth)
    granted = move(client, auth, RunState.PLAN_APPROVED, approval_id=approval_id)
    assert granted.status_code == 201
    assert granted.json()["state"] == str(RunState.PLAN_APPROVED)


def test_an_open_gate_does_not_let_the_run_through(client, auth):
    for state in (RunState.PREFLIGHT_RUNNING, RunState.PREFLIGHT_PASSED, RunState.PLAN_DRAFTED):
        move(client, auth, state)
    requested = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    )
    approval_id = requested.json()["approval_id"]
    assert requested.json()["state"] == str(ApprovalState.PENDING)
    assert requested.json()["overdue"] is False

    early = move(client, auth, RunState.PLAN_APPROVED, approval_id=approval_id)
    assert early.status_code == 403
    assert "is pending" in early.json()["message"]


def test_a_rejected_gate_stops_at_that_plan_version(client, auth):
    for state in (RunState.PREFLIGHT_RUNNING, RunState.PREFLIGHT_PASSED, RunState.PLAN_DRAFTED):
        move(client, auth, state)
    approval_id = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()["approval_id"]
    refused = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": False, "reason": "the design aliases batch"},
        headers=auth(PI, PI_ROLE),
    )
    assert refused.json()["state"] == str(ApprovalState.REJECTED)
    around = move(client, auth, RunState.PLAN_APPROVED, approval_id=approval_id)
    assert around.status_code == 403
    assert "rejected" in around.json()["message"]


# --------------------------------------------------------------------- who may decide


def test_an_agent_may_not_record_an_approval_decision(client, auth):
    approval_id = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()["approval_id"]
    response = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "self approval"},
        headers=engine(auth),
    )
    assert response.status_code == 403
    assert "must be performed by a human" in response.json()["message"]


def test_the_subject_that_asked_for_a_gate_may_not_open_it(client, auth):
    deputy = "user_pi_02"
    approval_id = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan", "assigned_to": deputy}, headers=auth(PI, PI_ROLE)
    ).json()["approval_id"]
    response = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "I trust my own judgement"},
        headers=auth(PI, PI_ROLE),
    )
    assert response.status_code == 403
    assert "may not open it" in response.json()["message"]


def test_an_analyst_has_no_route_to_open_a_gate(client, auth):
    approval_id = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()["approval_id"]
    response = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "I asked, I answer"},
        headers=auth(ANALYST, ANALYST_ROLE),
    )
    assert response.status_code == 403
    assert "grants approval:decide" in response.json()["message"]


def test_a_gate_can_be_delegated_and_the_delegate_is_named_in_the_record(client, auth):
    deputy = "user_pi_02"
    approval_id = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()["approval_id"]
    hurried = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "I am in a hurry"},
        headers=auth(deputy, PI_ROLE),
    )
    assert hurried.status_code == 403
    assert "accountable to" in hurried.json()["message"]

    delegated = client.post(
        f"{BASE}/approvals/{approval_id}/delegation", json={"to_subject": deputy}, headers=auth(PI, PI_ROLE)
    )
    assert delegated.status_code == 200, delegated.text
    assert delegated.json()["assigned_to"] == deputy and delegated.json()["delegated_from"] == PI

    decided = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "checked the design"},
        headers=auth(deputy, PI_ROLE),
    )
    assert decided.status_code == 200
    assert decided.json()["state"] == str(ApprovalState.APPROVED)
    assert decided.json()["decided_by"] == deputy


def test_the_inbox_lists_what_is_waiting_for_a_person(client, auth):
    approval_id = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()["approval_id"]
    inbox = client.get(f"{BASE}/approvals", params={"state": str(ApprovalState.PENDING)}, headers=auth(PI, PI_ROLE))
    assert inbox.status_code == 200
    assert [a["approval_id"] for a in inbox.json()["items"]] == [approval_id]
    assert inbox.json()["items"][0]["run_id"] == RUN_ID

    lapsed = client.get(f"{BASE}/approvals", params={"overdue": "true"}, headers=auth(PI, PI_ROLE)).json()
    assert lapsed["items"] == []


def test_the_inbox_pages_in_the_index(client, auth):
    """Section 9.7: the page a person reads is the page the index produced, not a slice of the table."""
    pending = {"state": str(ApprovalState.PENDING)}
    plan_gate = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()["approval_id"]
    node_gate = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "node", "node_id": "cluster"},
        headers=auth(ANALYST, ANALYST_ROLE),
    ).json()["approval_id"]

    whole = client.get(f"{BASE}/approvals", params=pending, headers=auth(PI, PI_ROLE)).json()
    assert {a["approval_id"] for a in whole["items"]} == {plan_gate, node_gate}

    first = client.get(f"{BASE}/approvals", params={**pending, "limit": 1}, headers=auth(PI, PI_ROLE)).json()
    assert len(first["items"]) == 1
    assert first["next_offset"] == 1
    second = client.get(
        f"{BASE}/approvals", params={**pending, "limit": 1, "offset": 1}, headers=auth(PI, PI_ROLE)
    ).json()
    assert {first["items"][0]["approval_id"], second["items"][0]["approval_id"]} == {plan_gate, node_gate}
    tail = client.get(f"{BASE}/approvals", params={**pending, "limit": 1, "offset": 2}, headers=auth(PI, PI_ROLE)).json()
    assert tail["items"] == []
    assert tail["next_offset"] is None


def test_the_inbox_is_not_readable_by_a_service_token(client, auth):
    response = client.get(f"{BASE}/approvals", headers=engine(auth))
    assert response.status_code == 403
    assert "grants approval:read" in response.json()["message"]


# --------------------------------------------------------------------- bounded loop and spend


def test_spend_past_the_plan_ceiling_hands_the_run_to_a_person(client, auth):
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)
    reported = client.post(
        f"{BASE}/runs/{RUN_ID}/nodes/cluster/usage",
        json={"compute_minutes": 300, "model_cost_usd": 1.0, "reason": "the cluster step ran long"},
        headers=engine(auth),
    )
    assert reported.status_code == 201
    body = reported.json()
    assert body["escalated"] is True
    assert "compute minutes 300 of 240 allowed" in body["reasons"][0]
    assert body["state"] == str(RunState.ESCALATED_TO_HUMAN)

    budget = client.get(f"{BASE}/runs/{RUN_ID}/budget", headers=auth(ANALYST, ANALYST_ROLE)).json()
    assert budget["usage"]["compute_minutes"] == 300
    assert budget["ceiling"]["max_compute_minutes"] == 240
    assert budget["exhausted"] == ["compute minutes 300 of 240 allowed"]


def test_a_duplicate_usage_report_is_not_charged_twice(client, auth):
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)
    for _ in range(2):
        response = client.post(
            f"{BASE}/runs/{RUN_ID}/nodes/cluster/usage",
            json={"compute_minutes": 10, "reason": "the cluster step"},
            headers=engine(auth),
        )
    assert response.status_code == 200
    assert response.json()["replayed"] is True
    assert client.get(f"{BASE}/runs/{RUN_ID}/budget", headers=auth(ANALYST, ANALYST_ROLE)).json()["usage"]["compute_minutes"] == 10


def test_the_loop_passes_are_counted_as_one_budget(client, auth):
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)
    for state in (RunState.QC_REVIEW, RunState.CANDIDATE_DISCOVERY, RunState.EVIDENCE_BUILDING):
        assert move(client, auth, state).status_code == 201

    first = move(client, auth, RunState.TARGETED_ANALYSIS, reason="first pass")
    assert first.status_code == 201 and first.json()["escalated"] is False
    second = move(client, auth, RunState.TARGETED_ANALYSIS, reason="second pass")
    assert second.status_code == 201
    assert second.json()["state"] == str(RunState.ESCALATED_TO_HUMAN)
    assert "iterations 2 > 1" in second.json()["reasons"][0]


def test_an_escalated_run_comes_back_only_on_a_larger_budget_a_person_approved(client, auth, catalog):
    """Section 9.2: the loop stops for a human, and only a raised, approved ceiling sends it back."""
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)
    reported = client.post(
        f"{BASE}/runs/{RUN_ID}/nodes/cluster/usage",
        json={"compute_minutes": 300, "reason": "the cluster step ran long"},
        headers=engine(auth),
    )
    assert reported.json()["state"] == str(RunState.ESCALATED_TO_HUMAN)

    bare = client.post(
        f"{BASE}/runs/{RUN_ID}/transitions",
        json={"to_state": str(RunState.ANALYSIS_RUNNING), "reason": "run it again", "attempt": 2},
        headers=engine(auth),
    )
    assert bare.status_code == 403
    assert "needs an escalation approval" in bare.json()["message"]

    catalog.record_plan(
        plan(plan_version="2", loop_budget=LoopBudget(max_iterations=2, max_compute_minutes=480, max_token_cost_usd=5.0)),
        created_by=ANALYST,
    )
    requested = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "escalation", "plan_version": "2"},
        headers=auth(ANALYST, ANALYST_ROLE),
    )
    assert requested.status_code == 201, requested.text
    escalation_id = requested.json()["approval_id"]
    granted = client.post(
        f"{BASE}/approvals/{escalation_id}/decision",
        json={"approved": True, "reason": "twice the room, once the loop"},
        headers=auth(PI, PI_ROLE),
    )
    assert granted.status_code == 200, granted.text

    resumed = client.post(
        f"{BASE}/runs/{RUN_ID}/transitions",
        json={
            "to_state": str(RunState.ANALYSIS_RUNNING),
            "reason": "restart under the raised budget",
            "attempt": 2,
            "approval_id": escalation_id,
            "resume_with_plan_version": "2",
        },
        headers=engine(auth),
    )
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["state"] == str(RunState.ANALYSIS_RUNNING)

    budget = client.get(f"{BASE}/runs/{RUN_ID}/budget", headers=auth(ANALYST, ANALYST_ROLE)).json()
    assert budget["ceiling"]["max_compute_minutes"] == 480
    assert budget["usage"]["compute_minutes"] == 300
    assert budget["exhausted"] == []


# --------------------------------------------------------------------- retries


def test_the_engines_node_retries_are_registered_against_the_run(client, auth):
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)
    response = client.post(
        f"{BASE}/runs/{RUN_ID}/retries",
        json={"node_id": "cluster", "attempt": 1, "level": "node", "reason": "transient io"},
        headers=engine(auth),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["event"]["kind"] == str(RunEventKind.NODE_RETRY)
    assert body["tolerance_exhausted"] is True
    assert client.get(f"{BASE}/runs/{RUN_ID}", headers=auth(PI, PI_ROLE)).json()["state"] == str(RunState.ANALYSIS_RUNNING)


def test_a_retry_beyond_what_the_plan_allows_is_refused(client, auth):
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)
    response = client.post(
        f"{BASE}/runs/{RUN_ID}/retries",
        json={"node_id": "cluster", "attempt": 2, "level": "node", "reason": "one more for luck"},
        headers=engine(auth),
    )
    assert response.status_code == 409
    assert response.json()["class"] == str(ErrorClass.RETRYABLE_FAILURE)
    assert "max_attempts 1" in response.json()["message"]


def test_the_state_machine_owns_its_retry_and_waits_for_the_backoff(client, auth):
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)
    assert move(client, auth, RunState.RETRYABLE_FAILURE, reason="the container died").status_code == 201

    too_early = client.post(
        f"{BASE}/runs/{RUN_ID}/retries",
        json={"node_id": "cluster", "attempt": 1, "level": "business", "reason": "start it again"},
        headers=engine(auth),
    )
    assert too_early.status_code == 429
    assert too_early.json()["retryable"] is True
    assert too_early.headers["Retry-After"] == "30"

    restart = client.post(
        f"{BASE}/runs/{RUN_ID}/transitions",
        json={"to_state": str(RunState.ANALYSIS_RUNNING), "reason": "restart after the backoff", "attempt": 2},
        headers=engine(auth),
    )
    assert restart.status_code == 201
    kinds = [e["kind"] for e in client.get(f"{BASE}/runs/{RUN_ID}/transitions", headers=auth(PI, PI_ROLE)).json()["items"]]
    assert kinds.count(str(RunEventKind.STATE)) == 7


# --------------------------------------------------------------------- gated nodes


def test_a_gated_node_blocks_the_run_until_a_person_clears_it(client, auth, catalog):
    gated = plan(
        plan_version="2",
        nodes=[
            node("cluster"),
            node(
                "discover",
                tool_name="scanpy_candidate_discovery",
                inputs=["clusters"],
                outputs=["candidate_sets"],
                requires_approval=True,
            ),
        ],
    )
    catalog.record_plan(gated, created_by=ANALYST)
    catalog.set_run_plan(
        RUN_ID, plan_version="2", tool_versions={n.tool_name: n.tool_version for n in gated.nodes}
    )
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)

    blocked = client.get(f"{BASE}/runs/{RUN_ID}/nodes/discover/approval", headers=engine(auth))
    assert blocked.status_code == 403
    assert "has not been requested" in blocked.json()["message"]

    gate = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "node", "node_id": "discover"},
        headers=auth(ANALYST, ANALYST_ROLE),
    )
    assert gate.status_code == 201, gate.text
    node_approval_id = gate.json()["approval_id"]
    cleared_by = client.post(
        f"{BASE}/approvals/{node_approval_id}/decision",
        json={"approved": True, "reason": "cleared for this project"},
        headers=auth(PI, PI_ROLE),
    )
    assert cleared_by.status_code == 200, cleared_by.text

    cleared = client.get(f"{BASE}/runs/{RUN_ID}/nodes/discover/approval", headers=engine(auth))
    assert cleared.status_code == 200
    assert cleared.json()["approval_id"] == node_approval_id


def test_a_node_that_needs_no_approval_is_not_a_gate(client, auth):
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)
    response = client.get(f"{BASE}/runs/{RUN_ID}/nodes/cluster/approval", headers=engine(auth))
    assert response.status_code == 400
    assert "needs no approval" in response.json()["message"]
