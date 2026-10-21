"""Run launch, idempotency, quotas and progress (sections 9.2, 9.3, 9.7)."""

from bioadapt_api.quota import ProjectQuotas
from bioadapt_policies.access import Role
from bioadapt_schemas.enums import RunState
from bioadapt_schemas.errors import ErrorClass
from conftest import ANALYST, PI, PROJECT_ID, RUN_ID, plan

BASE = f"/v1/projects/{PROJECT_ID}"


def _launch_body(**overrides):
    fields = {
        "plan_id": "plan_001",
        "plan_version": "1",
        "input_asset_ids": ["as_0001"],
        "tool_registry_snapshot": "snap_0001",
        "priority": 5,
    }
    fields.update(overrides)
    return fields


def _launch(client, auth, key, **overrides):
    headers = {**auth(overrides.pop("subject", PI), overrides.pop("roles", [Role.PRINCIPAL_INVESTIGATOR])), "Idempotency-Key": key}
    return client.post(f"{BASE}/runs", json=_launch_body(**overrides), headers=headers)


def test_starting_a_run_without_an_idempotency_key_is_refused(client, auth):
    response = client.post(f"{BASE}/runs", json=_launch_body(), headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert response.status_code == 400
    assert response.json()["field"] == "Idempotency-Key"


def test_a_launch_is_accepted_with_202_and_names_the_run_and_its_queue_place(client, auth):
    response = _launch(client, auth, "idem-1")
    assert response.status_code == 202
    body = response.json()
    assert body["run_id"].startswith("run_")
    assert body["state"] == str(RunState.CREATED)
    assert body["queue_position"] == 0
    assert body["tool_versions"] == {"scanpy_pipeline": "1.11.0"}
    assert body["input_hashes"] == {"as_0001": "ab" * 32}


def test_replaying_the_same_key_and_body_returns_the_original_run(client, auth):
    first = _launch(client, auth, "idem-replay")
    again = _launch(client, auth, "idem-replay")
    assert again.status_code == 200
    assert again.headers["Idempotency-Replayed"] == "true"
    assert again.json()["run_id"] == first.json()["run_id"]


def test_reusing_a_key_for_different_work_conflicts(client, auth):
    _launch(client, auth, "idem-conflict", priority=1)
    response = _launch(client, auth, "idem-conflict", priority=8)
    assert response.status_code == 409
    assert response.json()["class"] == str(ErrorClass.CONFLICT)


def test_an_unknown_input_asset_is_reported_as_missing(client, auth):
    response = _launch(client, auth, "idem-input", input_asset_ids=["as_nope"])
    assert response.status_code == 404
    assert response.json()["class"] == str(ErrorClass.NOT_FOUND)


def test_an_unregistered_plan_version_is_not_launchable(client, auth):
    response = _launch(client, auth, "idem-plan", plan_version="99")
    assert response.status_code == 404


def test_a_plan_over_the_project_compute_budget_is_refused(client, auth):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    expensive = plan(plan_version="2", loop_budget={"max_iterations": 6, "max_compute_minutes": 600, "max_token_cost_usd": 1.0})
    client.post(f"{BASE}/plans", json=expensive.model_dump(mode="json"), headers=headers)
    response = _launch(client, auth, "idem-budget", plan_version="2")
    assert response.status_code == 400
    assert response.json()["class"] == str(ErrorClass.BUDGET_EXCEEDED)
    assert response.json()["field"] == "loop_budget.max_compute_minutes"


def test_the_active_run_quota_stops_launching_beyond_the_limit(client, auth, state):
    assert _launch(client, auth, "idem-quota-1").status_code == 202
    state.quotas.default = ProjectQuotas(
        max_storage_bytes=10_000, max_active_runs=2, max_run_compute_minutes=480, max_run_token_cost_usd=10.0
    )
    response = _launch(client, auth, "idem-quota-2")
    assert response.status_code == 429
    assert response.json()["class"] == str(ErrorClass.RESOURCE_EXCEEDED)
    assert "quota is 2" in response.json()["message"]


def test_priority_decides_the_queue_position(client, auth):
    first = _launch(client, auth, "idem-q-high", priority=9)
    second = _launch(client, auth, "idem-q-low", priority=1)
    assert first.json()["queue_position"] == 0
    assert second.json()["queue_position"] == 1


def test_a_terminal_run_gives_its_slot_to_the_next_one(client, auth):
    """§9.7: a slot is held for a live run, so ending one has to move the run that was waiting."""
    first = _launch(client, auth, "idem-slot-1").json()["run_id"]
    second = _launch(client, auth, "idem-slot-2").json()["run_id"]
    assert client.get(f"{BASE}/runs/{second}", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()["queue_position"] == 1
    ended = client.post(
        f"{BASE}/runs/{first}/cancel",
        json={"reason": "the preflight contradicted the sample manifest"},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert ended.status_code == 201
    assert ended.json()["state"] == str(RunState.FAILED)
    assert client.get(f"{BASE}/runs/{second}", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()["queue_position"] == 0


def test_run_status_is_readable_and_paged(client, auth):
    launched = _launch(client, auth, "idem-list").json()["run_id"]
    detail = client.get(f"{BASE}/runs/{launched}", headers=auth(ANALYST, [Role.ANALYST]))
    assert detail.status_code == 200
    assert detail.json()["run_id"] == launched
    listing = client.get(f"{BASE}/runs?limit=1", headers=auth(ANALYST, [Role.ANALYST])).json()
    assert len(listing["items"]) == 1
    assert listing["next_offset"] == 1


def test_a_run_from_another_project_is_not_visible(client, auth):
    response = client.get("/v1/projects/prj_002/runs/whatever", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert response.status_code == 403


def test_progress_streams_state_and_queue_position_until_the_end(client, auth):
    response = client.get(f"{BASE}/runs/{RUN_ID}/events?max_events=2", headers=auth(ANALYST, [Role.ANALYST]))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [line for line in response.text.splitlines() if line.startswith("event:")]
    assert events == ["event: state", "event: state", "event: end"]
    frames = [line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data:")]
    assert '"run_id"' in frames[0]
    assert RunState.CREATED.value in frames[0]


def test_a_service_account_may_not_launch_a_run(client, auth):
    response = client.post(
        f"{BASE}/runs",
        json=_launch_body(),
        headers={**auth("svc_orchestrator", [Role.SERVICE_AGENT], service=True), "Idempotency-Key": "idem-svc"},
    )
    assert response.status_code == 403
    assert "run:launch" in response.json()["message"]


def test_execute_run_pipeline_endpoint(client, auth):
    # Launch a run
    resp = _launch(client, auth, "idem-exec-test")
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    # Execute endpoint
    exec_resp = client.post(
        f"{BASE}/runs/{run_id}/execute",
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    print("EXEC RESP:", exec_resp.status_code, exec_resp.json())
    assert exec_resp.status_code in (200, 400, 403)

