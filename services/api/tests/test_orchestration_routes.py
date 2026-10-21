"""Orchestration routes: the journal, the gates and both retry sides (sections 9.1, 9.2, 9.7, 11.6).

These tests drive the API only: what a workflow engine can do with a service token, what only a
person can do, and what the run's journal says afterwards about the difference.
"""

from datetime import timedelta

from bioadapt_policies.access import Role
from bioadapt_schemas.enums import ApprovalKind, ApprovalState, RunEventKind, RunState, SubjectKind
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.plan import LoopBudget
from conftest import ANALYST, COMPLIANCE, EXPERT, NOW, PI, PROJECT_ID, RUN_ID, SERVICE, node, plan

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


def test_a_redelivery_is_answered_from_the_journal_even_after_the_run_moved_on(client, auth):
    """Section 9.2: a duplicate is recognised before the current state is, so it cannot read as a rewind.

    The engine's retry queue can redeliver a callback long after the run advanced. The answer is the first
    delivery's own result - including the state it produced - so a caller that needs to know where the run
    stands now has to read the run, not the replay.
    """
    first = move(client, auth, RunState.PREFLIGHT_RUNNING, attempt=1, from_state=RunState.CREATED)
    assert move(
        client, auth, RunState.PREFLIGHT_PASSED, attempt=1, from_state=RunState.PREFLIGHT_RUNNING
    ).status_code == 201

    late = move(client, auth, RunState.PREFLIGHT_RUNNING, attempt=1, from_state=RunState.CREATED)
    assert late.status_code == 200
    assert late.headers["Idempotency-Replayed"] == "true"
    assert late.json()["event"]["event_id"] == first.json()["event"]["event_id"]
    assert late.json()["state"] == str(RunState.PREFLIGHT_RUNNING)
    assert client.get(f"{BASE}/runs/{RUN_ID}", headers=auth(ANALYST, ANALYST_ROLE)).json()["state"] == str(
        RunState.PREFLIGHT_PASSED
    )
    journal = client.get(f"{BASE}/runs/{RUN_ID}/transitions", headers=auth(ANALYST, ANALYST_ROLE)).json()
    assert len(journal["items"]) == 2

    #: The same edge under another attempt is a different claim, not a duplicate: it says the run was where
    #: it no longer is, and the journal stays as it was.
    resent = move(client, auth, RunState.PREFLIGHT_RUNNING, attempt=2, from_state=RunState.CREATED)
    assert resent.status_code == 409
    assert "claims it was in" in resent.json()["message"]
    assert len(client.get(f"{BASE}/runs/{RUN_ID}/transitions", headers=auth(ANALYST, ANALYST_ROLE)).json()["items"]) == 2


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


def test_re_asking_for_a_gate_that_was_already_answered_shows_the_answer(client, auth):
    """Section 9.2: a gate is addressed by its subject, so asking again resolves to the row that exists.

    A script that asks again after the PI has decided must not be told a second pending gate opened:
    the gate it is pointed at is already closed, and its next call would be refused.
    """
    for state in (RunState.PREFLIGHT_RUNNING, RunState.PREFLIGHT_PASSED, RunState.PLAN_DRAFTED):
        move(client, auth, state)
    approval_id = open_plan_gate(client, auth)

    again = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    )
    assert again.status_code == 200, again.text
    assert again.headers["Idempotency-Replayed"] == "true"
    assert again.json()["approval_id"] == approval_id
    assert again.json()["state"] == str(ApprovalState.APPROVED)
    assert again.json()["decided_by"] == PI

    inbox = client.get(f"{BASE}/approvals", headers=auth(PI, PI_ROLE)).json()
    assert [a["approval_id"] for a in inbox["items"]] == [approval_id]


def test_re_asking_for_an_open_gate_does_not_hand_it_to_a_new_owner(client, auth):
    deputy = "user_pi_02"
    first = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    )
    assert first.status_code == 201, first.text

    again = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "plan", "assigned_to": deputy},
        headers=auth(ANALYST, ANALYST_ROLE),
    )
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["approval_id"] == first.json()["approval_id"]
    assert body["state"] == str(ApprovalState.PENDING)
    assert body["assigned_to"] == PI and body["requested_by"] == ANALYST


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


def test_a_decided_gate_is_closed_to_both_routes_afterwards(client, auth):
    """Section 11.6: a gate is answered once. A second decision or a later delegation is refused, not merged."""
    approval_id = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()["approval_id"]
    first = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "checked the design"},
        headers=auth(PI, PI_ROLE),
    )
    assert first.status_code == 200, first.text

    changed_mind = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": False, "reason": "the first one was a mistake"},
        headers=auth(PI, PI_ROLE),
    )
    assert changed_mind.status_code == 403
    assert changed_mind.json()["code"] == "approval_refused"
    assert "already approved" in changed_mind.json()["message"]

    after_the_fact = client.post(
        f"{BASE}/approvals/{approval_id}/delegation",
        json={"to_subject": "user_pi_02"},
        headers=auth(PI, PI_ROLE),
    )
    assert after_the_fact.status_code == 403
    assert "already approved" in after_the_fact.json()["message"]

    #: The row still carries the one decision that was made, by the one person who made it.
    rows = client.get(
        f"{BASE}/approvals", params={"state": str(ApprovalState.APPROVED)}, headers=auth(PI, PI_ROLE)
    ).json()["items"]
    assert [row["approval_id"] for row in rows] == [approval_id]
    assert rows[0]["decided_by"] == PI and rows[0]["delegated_from"] is None


def test_a_write_about_a_gate_that_was_never_opened_is_not_found(client, auth):
    """A typo'd approval id is a 404 on both write sides, not a 403 that leaks what the id means."""
    for verb, body in (("decision", {"approved": True, "reason": "anything"}), ("delegation", {"to_subject": PI})):
        response = client.post(
            f"{BASE}/approvals/apr_never_requested/{verb}", json=body, headers=auth(PI, PI_ROLE)
        )
        assert response.status_code == 404, response.text
        assert response.json()["class"] == str(ErrorClass.NOT_FOUND)
        assert "apr_never_requested" in response.json()["message"]


def test_only_the_person_a_gate_is_accountable_to_can_move_it(client, auth):
    """Section 9.2: delegation moves accountability, and every way of abusing that is refused."""
    deputy, onward = "user_pi_02", "user_pi_03"
    approval_id = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()["approval_id"]

    def delegate(headers, to_subject):
        return client.post(f"{BASE}/approvals/{approval_id}/delegation", json={"to_subject": to_subject}, headers=headers)

    not_holder = delegate(auth(deputy, PI_ROLE), onward)
    assert not_holder.status_code == 403
    assert f"only {PI} may delegate" in not_holder.json()["message"]

    #: A PI outside the project cannot reach the gate at all, which is a different refusal from this one.
    elsewhere = delegate(auth(deputy, PI_ROLE, project_ids=["prj_002"]), onward)
    assert elsewhere.status_code == 403
    assert "project scope" in elsewhere.json()["message"]

    to_self = delegate(auth(PI, PI_ROLE), PI)
    assert to_self.status_code == 403
    assert "delegating to yourself" in to_self.json()["message"]

    #: Section 11.6: the point of the second pair of eyes is that it cannot be handed back.
    back_to_requester = delegate(auth(PI, PI_ROLE), ANALYST)
    assert back_to_requester.status_code == 403
    assert "delegated back to the subject that requested" in back_to_requester.json()["message"]

    by_service = delegate(engine(auth), deputy)
    assert by_service.status_code == 403
    assert "must be performed by a human" in by_service.json()["message"]

    moved = delegate(auth(PI, PI_ROLE), deputy)
    assert moved.status_code == 200, moved.text
    assert moved.json()["assigned_to"] == deputy and moved.json()["delegated_from"] == PI

    chain = delegate(auth(deputy, PI_ROLE), onward)
    assert chain.status_code == 200, chain.text
    assert chain.json()["assigned_to"] == onward and chain.json()["delegated_from"] == deputy

    #: The person who handed the gate away has no route back onto it.
    stranded = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "I had it first"},
        headers=auth(PI, PI_ROLE),
    )
    assert stranded.status_code == 403
    assert "accountable to" in stranded.json()["message"]


def test_a_gate_written_twice_from_one_snapshot_leaves_the_late_write_a_conflict(client, auth, catalog, monkeypatch):
    """Section 9.2: two writers who both read a pending gate cannot both write it.

    The person-level check is made against a snapshot, so it passes for both of them; the update is what
    has to restate the holder it read. Without that the PI who had already handed the gate to a delegate
    would record the decision anyway, and the row would say both things happened.
    """
    approval_id = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()["approval_id"]
    deputy = "user_pi_02"
    #: What the deciding request had read before its own write ran.
    snapshot = catalog.get_approval(approval_id)
    delegated = client.post(
        f"{BASE}/approvals/{approval_id}/delegation", json={"to_subject": deputy}, headers=auth(PI, PI_ROLE)
    )
    assert delegated.status_code == 200, delegated.text

    real = catalog.get_approval

    def as_read(queried_id: str):
        return snapshot if queried_id == approval_id else real(queried_id)

    monkeypatch.setattr(catalog, "get_approval", as_read)
    late = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "it was mine when I looked"},
        headers=auth(PI, PI_ROLE),
    )
    monkeypatch.undo()

    assert late.status_code == 409
    assert late.json()["code"] == "concurrent_write"
    assert late.json()["retryable"] is True
    gate = catalog.get_approval(approval_id)
    assert (gate.state, gate.assigned_to, gate.decided_by) == (ApprovalState.PENDING, deputy, None)


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


def test_a_lapsed_window_reminds_nobody_into_granting_it(client, auth, state):
    """Section 9.2: the overdue inbox is a read over the clock, and a lapsed gate still grants nothing.

    The whole point of the view is that it fires because time passed, so the clock really has to move -
    the token it is read with is issued long enough to outlive the window it is checking.
    """
    approval_id = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, ANALYST_ROLE)
    ).json()["approval_id"]
    for state_name in (RunState.PREFLIGHT_RUNNING, RunState.PREFLIGHT_PASSED, RunState.PLAN_DRAFTED):
        assert move(client, auth, state_name).status_code == 201

    long_lived = 365 * 86400
    state.clock = lambda: NOW + timedelta(days=30)

    lapsed = client.get(
        f"{BASE}/approvals", params={"overdue": "true"}, headers=auth(PI, PI_ROLE, ttl=long_lived)
    ).json()
    assert [row["approval_id"] for row in lapsed["items"]] == [approval_id]
    assert lapsed["items"][0]["overdue"] is True
    assert "past its 3-working-day window" in lapsed["items"][0]["overdue_reason"]

    #: A lapsed window has not expired the gate: it is still the open item the inbox showed, and the
    #: engine may not use it as consent.
    still_open = client.get(
        f"{BASE}/approvals",
        params={"state": str(ApprovalState.PENDING)},
        headers=auth(PI, PI_ROLE, ttl=long_lived),
    ).json()["items"]
    assert [row["approval_id"] for row in still_open] == [approval_id]
    refused = client.post(
        f"{BASE}/runs/{RUN_ID}/transitions",
        json={
            "to_state": str(RunState.PLAN_APPROVED),
            "reason": "the window lapsed, treat it as approved",
            "approval_id": approval_id,
        },
        headers=auth(SERVICE, [Role.SERVICE_AGENT], service=True, ttl=long_lived),
    )
    assert refused.status_code == 403
    assert refused.json()["code"] == "approval_required"
    assert "a human decision is required" in refused.json()["message"]

    #: The lapse does not revoke the person it was assigned to; their decision is what opens it.
    decided = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "late, but a decision"},
        headers=auth(PI, PI_ROLE, ttl=long_lived),
    )
    assert decided.status_code == 200, decided.text
    assert client.get(f"{BASE}/approvals", params={"overdue": "true"}, headers=auth(PI, PI_ROLE, ttl=long_lived)).json()["items"] == []
    granted = client.post(
        f"{BASE}/runs/{RUN_ID}/transitions",
        json={"to_state": str(RunState.PLAN_APPROVED), "reason": "now signed off", "approval_id": approval_id},
        headers=auth(SERVICE, [Role.SERVICE_AGENT], service=True, ttl=long_lived),
    )
    assert granted.status_code == 201, granted.text


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


def test_model_money_is_a_ceiling_of_its_own_not_a_note_on_compute(client, auth):
    """Section 9.7: money and minutes are separate ceilings, and the loop stops on either.

    A run can sit well inside its compute minutes and still be out of model budget. The reason string is
    what a person reads when the escalated run reaches them, so it names the axis that actually broke.
    """
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)
    reported = client.post(
        f"{BASE}/runs/{RUN_ID}/nodes/cluster/usage",
        json={"compute_minutes": 30, "model_cost_usd": 6.0, "reason": "the decision layer was called twice"},
        headers=engine(auth),
    )
    assert reported.status_code == 201, reported.text
    assert reported.json()["escalated"] is True
    assert reported.json()["reasons"] == ["model cost 6.00 USD of 5.00 allowed"]
    assert reported.json()["state"] == str(RunState.ESCALATED_TO_HUMAN)

    budget = client.get(f"{BASE}/runs/{RUN_ID}/budget", headers=auth(ANALYST, ANALYST_ROLE)).json()
    assert budget["usage"] == {"iterations": 0, "compute_minutes": 30, "model_cost_usd": 6.0}
    assert budget["ceiling"]["max_model_cost_usd"] == 5.0
    assert budget["exhausted"] == ["model cost 6.00 USD of 5.00 allowed"]


def test_the_project_quota_caps_a_plan_that_asks_for_more_than_the_project_has(client, auth, catalog):
    """Section 9.7: the smaller of plan and quota wins, axis by axis, and it wins before the loop runs."""
    approval_id = open_plan_gate(client, auth)
    to_analysis(client, auth, approval_id=approval_id)
    generous = plan(
        plan_version="2",
        loop_budget=LoopBudget(max_iterations=4, max_compute_minutes=900, max_token_cost_usd=20.0),
    )
    catalog.record_plan(generous, created_by=ANALYST)
    catalog.set_run_plan(
        RUN_ID, plan_version="2", tool_versions={n.tool_name: n.tool_version for n in generous.nodes}
    )

    ceiling = client.get(f"{BASE}/runs/{RUN_ID}/budget", headers=auth(ANALYST, ANALYST_ROLE)).json()["ceiling"]
    assert ceiling == {"max_iterations": 4, "max_compute_minutes": 480, "max_model_cost_usd": 10.0}

    reported = client.post(
        f"{BASE}/runs/{RUN_ID}/nodes/cluster/usage",
        json={"compute_minutes": 500, "model_cost_usd": 12.0, "reason": "a pass that ignored both ceilings"},
        headers=engine(auth),
    )
    assert reported.status_code == 201, reported.text
    assert reported.json()["escalated"] is True
    assert reported.json()["reasons"] == [
        "compute minutes 500 of 480 allowed",
        "model cost 12.00 USD of 10.00 allowed",
    ]


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


def test_an_ad_hoc_tool_gate_is_its_own_kind_and_unlocks_nothing_else(client, auth, catalog):
    """Section 9.4: a project_adhoc tool is gated by its own approval kind, not by a node sign-off.

    Section 9.6: two gates raised over the same node are two records. Clearing the ad-hoc one leaves the
    run's node gate as unrequested as it was, because the kind is part of the grant's identity.
    """
    gated = plan(
        plan_version="2",
        nodes=[node("cluster"), node("adhoc_score", tool_name="custom_marker_score", requires_approval=True)],
    )
    catalog.record_plan(gated, created_by=ANALYST)
    catalog.set_run_plan(RUN_ID, plan_version="2", tool_versions={n.tool_name: n.tool_version for n in gated.nodes})

    adhoc = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "adhoc_tool", "node_id": "adhoc_score"},
        headers=auth(ANALYST, ANALYST_ROLE),
    )
    assert adhoc.status_code == 201, adhoc.text
    body = adhoc.json()
    assert body["kind"] == str(ApprovalKind.ADHOC_TOOL)
    assert body["node_id"] == "adhoc_score"
    assert body["state"] == str(ApprovalState.PENDING)
    assert body["requested_by"] == ANALYST and body["assigned_to"] == PI

    node_gate = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "node", "node_id": "adhoc_score"},
        headers=auth(ANALYST, ANALYST_ROLE),
    )
    assert node_gate.status_code == 201, node_gate.text
    assert node_gate.json()["approval_id"] != body["approval_id"]

    refused = client.post(
        f"{BASE}/approvals/{body['approval_id']}/decision",
        json={"approved": True, "reason": "the agent that wrote the tool approves its own run"},
        headers=auth(SERVICE, [Role.SERVICE_AGENT], service=True),
    )
    assert refused.status_code == 403
    assert "must be performed by a human" in refused.json()["message"]

    granted = client.post(
        f"{BASE}/approvals/{body['approval_id']}/decision",
        json={"approved": True, "reason": "sandboxed, one submission, results stay unregistered"},
        headers=auth(PI, PI_ROLE),
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["kind"] == str(ApprovalKind.ADHOC_TOOL)

    #: The node gate raised over the same node is untouched by the ad-hoc grant: it is still pending,
    #: and a pending gate is exactly what the engine may not treat as consent.
    still_gated = client.get(f"{BASE}/runs/{RUN_ID}/nodes/adhoc_score/approval", headers=engine(auth))
    assert still_gated.status_code == 403
    assert "is pending; a human decision is required" in still_gated.json()["message"]
    assert still_gated.json()["code"] == "approval_required"

    for state_name in (RunState.PREFLIGHT_RUNNING, RunState.PREFLIGHT_PASSED, RunState.PLAN_DRAFTED):
        assert move(client, auth, state_name).status_code == 201
    not_this_kind = client.post(
        f"{BASE}/runs/{RUN_ID}/transitions",
        json={
            "to_state": str(RunState.PLAN_APPROVED),
            "reason": "an ad-hoc approval was granted, count it for the plan too",
            "approval_id": body["approval_id"],
        },
        headers=engine(auth),
    )
    assert not_this_kind.status_code == 403
    assert not_this_kind.json()["code"] == "approval_required"
    assert "does not cover plan of run" in not_this_kind.json()["message"]


def test_an_approval_kind_and_a_node_id_are_a_pair_not_a_choice(client, auth):
    """Section 9.4: run-wide gates name no node and node gates must, in both directions."""
    missing = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "adhoc_tool"},
        headers=auth(ANALYST, ANALYST_ROLE),
    )
    assert missing.status_code == 400
    assert missing.json()["class"] == str(ErrorClass.VALIDATION)
    assert "names the node it gates" in missing.json()["message"]

    for kind in (ApprovalKind.PLAN, ApprovalKind.ESCALATION):
        over = client.post(
            f"{BASE}/runs/{RUN_ID}/approvals",
            json={"kind": str(kind), "node_id": "cluster"},
            headers=auth(ANALYST, ANALYST_ROLE),
        )
        assert over.status_code == 400, over.text
        assert "not one node" in over.json()["message"]

    elsewhere = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "adhoc_tool", "node_id": "node_nobody_planned"},
        headers=auth(ANALYST, ANALYST_ROLE),
    )
    assert elsewhere.status_code == 404
    assert elsewhere.json()["class"] == str(ErrorClass.NOT_FOUND)
    assert "node_nobody_planned" in elsewhere.json()["message"]
