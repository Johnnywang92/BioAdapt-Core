"""The bodies the run-tracking screens read: ``GET /runs?state=`` and the run's journal (§9.2).

``app/projects/[projectId]/runs/page.tsx`` renders one row per run and filters by state on the server;
``runs/[runId]/page.tsx`` renders the journal as a table whose columns are named after fields on the
event. Neither screen composes a state of its own - a tracker that inferred "it is probably clustering"
would be the one place a run could be wrong about itself. This test pins the two bodies so that a
renamed field shows up as a failing test rather than as an empty column on somebody's tracking screen.
"""

from bioadapt_policies.access import Role
from bioadapt_schemas.enums import RunEventKind, RunState
from conftest import ANALYST, PI, PROJECT_ID, RUN_ID, SERVICE

BASE = f"/v1/projects/{PROJECT_ID}"
ANALYST_ROLE = [Role.ANALYST]
PI_ROLE = [Role.PRINCIPAL_INVESTIGATOR]

#: the `<td>`s the runs list renders, read off ``runs/page.tsx``
RUN_ROW_FIELDS = {"run_id", "state", "plan_id", "plan_version", "started_at", "queue_position"}

#: the `<td>`s the journal panel renders, including the ones it only shows for some kinds
JOURNAL_FIELDS = {
    "event_id",
    "run_id",
    "kind",
    "from_state",
    "to_state",
    "actor_id",
    "actor_kind",
    "reason",
    "attempt",
    "node_id",
    "loop_iteration",
    "compute_minutes",
    "model_cost_usd",
    "at",
}


def engine(auth):
    return auth(SERVICE, [Role.SERVICE_AGENT], service=True)


def move(client, auth, to_state, *, headers=None):
    return client.post(
        f"{BASE}/runs/{RUN_ID}/transitions",
        json={"to_state": str(to_state), "reason": "engine reported progress"},
        headers=headers or engine(auth),
    )


def test_the_run_list_row_carries_the_columns_the_screen_renders(client, auth):
    listed = client.get(f"{BASE}/runs", headers=auth(ANALYST, ANALYST_ROLE)).json()
    assert {"items", "limit", "offset", "next_offset"} <= set(listed)
    row = next(item for item in listed["items"] if item["run_id"] == RUN_ID)
    assert RUN_ROW_FIELDS <= set(row), sorted(RUN_ROW_FIELDS - set(row))
    #: the state filter is the server's enum, so an unstarted run reads back as CREATED
    assert row["state"] == str(RunState.CREATED)
    #: a run that has started reports its own timestamp; the screen prints it and never re-formats it
    assert isinstance(row["started_at"], str) and "T" in row["started_at"]


def test_the_state_filter_answers_from_the_server_not_from_the_screen(client, auth):
    assert move(client, auth, RunState.PREFLIGHT_RUNNING).status_code == 201

    def ids(state):
        response = client.get(
            f"{BASE}/runs", params={"state": state}, headers=auth(ANALYST, ANALYST_ROLE)
        )
        assert response.status_code == 200, response.text
        return [item["run_id"] for item in response.json()["items"]]

    assert RUN_ID in ids(str(RunState.PREFLIGHT_RUNNING))
    assert RUN_ID not in ids(str(RunState.CREATED))
    #: a state no run sits in is an empty list, not an error - the screen says so rather than guessing
    assert client.get(f"{BASE}/runs", params={"state": str(RunState.ARCHIVED)}, headers=auth(ANALYST, ANALYST_ROLE)).status_code == 200


def test_a_journal_line_carries_the_columns_the_panel_renders(client, auth):
    for state in (RunState.PREFLIGHT_RUNNING, RunState.PREFLIGHT_PASSED):
        assert move(client, auth, state).status_code == 201
    spent = client.post(
        f"{BASE}/runs/{RUN_ID}/nodes/cluster/usage",
        json={"compute_minutes": 10, "model_cost_usd": 0.5, "reason": "the cluster step"},
        headers=engine(auth),
    )
    assert spent.status_code == 201, spent.text

    items = client.get(f"{BASE}/runs/{RUN_ID}/transitions", headers=auth(ANALYST, ANALYST_ROLE)).json()["items"]
    assert len(items) == 3
    for item in items:
        missing = JOURNAL_FIELDS - set(item)
        assert not missing, sorted(missing)
    assert {item["kind"] for item in items} == {str(RunEventKind.STATE), str(RunEventKind.USAGE)}

    #: a transition is about the run and never carries spend; a usage line names the node and the spend
    state_event = next(item for item in items if item["kind"] == str(RunEventKind.STATE))
    assert state_event["node_id"] is None and state_event["compute_minutes"] == 0
    usage_event = next(item for item in items if item["kind"] == str(RunEventKind.USAGE))
    assert usage_event["node_id"] == "cluster" and usage_event["compute_minutes"] == 10
    #: the reason is the server's own sentence, and the panel shows it verbatim
    assert state_event["reason"] == "engine reported progress"

    retried = client.post(
        f"{BASE}/runs/{RUN_ID}/retries",
        json={"level": "node", "node_id": "cluster", "attempt": 1, "reason": "the matrix was re-staged"},
        headers=engine(auth),
    )
    assert retried.status_code == 201, retried.text
    line = retried.json()["event"]
    assert line["kind"] == str(RunEventKind.NODE_RETRY) and line["node_id"] == "cluster"
    assert line["from_state"] == line["to_state"], "a retry line reports work inside a state, not a move"

    #: the plan, not the caller, sets how often a node is retried, so the journal cannot fill up with
    #: retry lines the plan never allowed - which is why the panel's attempt column can be trusted
    beyond = client.post(
        f"{BASE}/runs/{RUN_ID}/retries",
        json={"level": "node", "node_id": "cluster", "attempt": 2, "reason": "again"},
        headers=engine(auth),
    )
    assert beyond.status_code == 409
    assert "retry_policy.max_attempts" in beyond.json()["message"]

    again = client.get(f"{BASE}/runs/{RUN_ID}/transitions", headers=auth(ANALYST, ANALYST_ROLE)).json()["items"]
    assert len(again) == 4
    assert {item["kind"] for item in again} == {
        str(RunEventKind.STATE),
        str(RunEventKind.USAGE),
        str(RunEventKind.NODE_RETRY),
    }


def test_every_kind_the_panel_offers_is_a_value_the_api_accepts(client, auth):
    """``EVENT_KINDS`` in the screen is the enum, not a label of its own."""
    for kind in ("state", "node_retry", "business_retry", "usage"):
        response = client.get(
            f"{BASE}/runs/{RUN_ID}/transitions", params={"kind": kind}, headers=auth(ANALYST, ANALYST_ROLE)
        )
        assert response.status_code == 200, (kind, response.text)
        assert all(item["kind"] == kind for item in response.json()["items"])

    refused = client.get(
        f"{BASE}/runs/{RUN_ID}/transitions", params={"kind": "whatever"}, headers=auth(ANALYST, ANALYST_ROLE)
    )
    assert refused.status_code == 400
    assert "kind" in refused.text


def test_a_person_who_ends_a_run_writes_the_line_the_journal_shows(client, auth):
    ended = client.post(
        f"{BASE}/runs/{RUN_ID}/cancel",
        json={"reason": "the donor coverage is too thin"},
        headers=auth(PI, PI_ROLE),
    )
    assert ended.status_code == 201, ended.text
    last = client.get(f"{BASE}/runs/{RUN_ID}/transitions", headers=auth(ANALYST, ANALYST_ROLE)).json()["items"][-1]
    assert last["to_state"] == str(RunState.FAILED)
    assert last["actor_kind"] == "human", "the tracking screen shows who moved it, so the row has to say a person did"
    assert last["reason"] == "the donor coverage is too thin"
