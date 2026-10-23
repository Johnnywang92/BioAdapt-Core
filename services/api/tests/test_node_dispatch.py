"""Where a run's work goes once the deployment declares machines (§3.3, §4.2, §9.4, §9.6).

The execute route has always run the reference chain in-process, because the control plane was the
only machine the deployment claimed. These cases are the other side of that: declare a machine and
the run's work leaves this process, is recorded against the machine's declared id, and a deployment
that cannot reach a machine parks the run instead of quietly doing the work here.

The machine in these tests is a real agent on a real socket with the real reference worker behind
it, so what is proved is the journey the deployment takes - not a stub's opinion of it.
"""

from __future__ import annotations

import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

from bioadapt_policies.access import GLOBAL_SCOPE, Role
from bioadapt_schemas.enums import RunState
from bioadapt_tool_registry.node_agent import NodeAgent, build_server
from conftest import ANALYST, LOCAL_PROJECT_ID, PI, PROJECT_ID
from test_plan_wizard_contract import upload_counts, wizard_plan

BASE = f"/v1/projects/{PROJECT_ID}"
ADMIN = "user_platform_admin_01"
NODE_TOKEN = "node-shared-secret"

#: The machine that takes the work: room for the reference chain's own budget, with a container
#: runtime declared because an ad-hoc step would need one (§9.6).
ROOMY = {
    "node_id": "nod_server_01",
    "label": "lab server",
    "position": "local",
    "capabilities": {"cpu": 8, "memory_mb": 16384, "can_run_containers": True},
}

#: The machine that does not: a laptop that has not been given the room, which is exactly the
#: question §9.4 answers before anything is sent.
TIGHT = {
    "node_id": "nod_laptop_01",
    "label": "analyst laptop",
    "position": "local",
    "capabilities": {"cpu": 1, "memory_mb": 1024, "can_run_containers": False},
}

#: A machine with all the room in the world, in someone else's data centre. It is a perfectly good
#: compute node and still cannot hold a strict-local project's work (§4.2, §4.4).
CLOUD = {
    "node_id": "nod_cloud_01",
    "label": "cloud rack",
    "position": "cloud",
    "capabilities": {"cpu": 8, "memory_mb": 16384, "can_run_containers": True},
}


@pytest.fixture
def admin(auth):
    return auth(ADMIN, [Role.PLATFORM_ADMIN], project_ids=[GLOBAL_SCOPE], amr=["pwd+mfa"])


def start_node(tmp_path: Path) -> tuple[str, tuple[ThreadingHTTPServer, threading.Thread]]:
    """A node agent running the reference worker, listening on a free port."""
    agent = NodeAgent(
        command=[sys.executable, "-m", "bioadapt_scrnaseq.worker"],
        work_root=tmp_path / "work",
        token=NODE_TOKEN,
        node_name="nod_server_01",
        cpu=8,
        memory_mb=16384,
    )
    server = build_server(agent, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    return f"http://{host}:{port}", (server, thread)


def stop_node(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


def runnable_run(
    client,
    auth,
    state,
    *,
    plan_id: str = "plan_dispatch_001",
    project_id: str = PROJECT_ID,
    privacy_tier: str = "restricted",
) -> str:
    """A launched run over the reference chain, with its plan gate decided by a second subject.

    The plan is the one the wizard assembles from the tool catalog (§9.4), so the only thing these
    cases change is which machine runs it.
    """
    base = f"/v1/projects/{project_id}"
    access = auth(ANALYST, [Role.ANALYST], project_ids=[project_id])
    asset_id = upload_counts(client, auth, state, project_id=project_id, privacy_tier=privacy_tier)
    cataloged = client.get(f"{base}/tools", headers=access).json()
    project = client.get(base, headers=access).json()
    plan = wizard_plan(
        {"plan_id": plan_id},
        project=project,
        tools=cataloged["tools"],
        conclusion_outputs=cataloged["conclusion_outputs"],
    )
    plan["project_id"] = project_id
    recorded = client.post(f"{base}/plans", json=plan, headers=access)
    assert recorded.status_code == 201, recorded.json()

    launched = client.post(
        f"{base}/runs",
        json={
            "plan_id": plan_id,
            "plan_version": "1",
            "input_asset_ids": [asset_id],
            "tool_registry_snapshot": cataloged["snapshot_id"],
            "priority": 5,
        },
        headers={
            **auth(PI, [Role.PRINCIPAL_INVESTIGATOR], project_ids=[project_id]),
            "Idempotency-Key": f"idem-{plan_id}",
        },
    )
    assert launched.status_code == 202, launched.json()
    run_id = launched.json()["run_id"]

    gate = client.post(f"{base}/runs/{run_id}/approvals", json={"kind": "plan"}, headers=access)
    assert gate.status_code == 201, gate.json()
    decided = client.post(
        f"{base}/approvals/{gate.json()['approval_id']}/decision",
        json={"approved": True, "reason": "reviewed the plan this run dispatches"},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR], project_ids=[project_id]),
    )
    assert decided.status_code == 200, decided.json()
    return run_id


def execute(client, auth, run_id: str, *, project_id: str = PROJECT_ID) -> dict:
    response = client.post(
        f"/v1/projects/{project_id}/runs/{run_id}/execute",
        headers=auth(ANALYST, [Role.ANALYST], project_ids=[project_id]),
    )
    assert response.status_code == 200, response.json()
    return response.json()


def receipts_of(catalog, run_id: str, *, project_id: str = PROJECT_ID) -> list[Any]:
    catalog.set_scope(project_id)
    return catalog.list_execution_receipts(run_id)


class TestDispatching:
    def test_a_declared_machine_runs_the_work_and_every_receipt_names_it(
        self, client, auth, admin, state, catalog, tmp_path: Path
    ) -> None:
        endpoint, (server, thread) = start_node(tmp_path)
        state.node_token = NODE_TOKEN
        try:
            #: The laptop cannot hold the chain, so the server is the only candidate - the same
            #: question a person asks when they have a laptop and a server (§9.4).
            assert client.post("/v1/nodes", json=TIGHT, headers=admin).status_code == 201
            declared = client.post("/v1/nodes", json={**ROOMY, "endpoint": endpoint}, headers=admin)
            assert declared.status_code == 201, declared.json()

            body = execute(client, auth, runnable_run(client, auth, state))

            assert body["completed"] is True, body
            assert body["state"] == str(RunState.QC_REVIEW)
            assert body["gated_nodes"] == []
            receipts = receipts_of(catalog, body["run_id"])
            assert len(receipts) == len(body["nodes_executed"]) == 8
            assert {receipt.executed_on for receipt in receipts} == {"nod_server_01"}
            #: The receipt says what actually ran, not what the tool declares it is deployed in:
            #: the worker on the far machine names itself and the interpreter (§9.4).
            assert all(
                receipt.runtime and receipt.runtime.startswith("bioadapt-scrnaseq ")
                for receipt in receipts
            )
        finally:
            stop_node(server, thread)

    def test_a_deployment_with_no_credential_parks_the_run_instead_of_running_here(
        self, client, auth, admin, state, catalog, tmp_path: Path
    ) -> None:
        """A declared machine the control plane cannot open is a wait, not a licence to run locally."""
        endpoint, (server, thread) = start_node(tmp_path)
        try:
            declared = client.post(
                "/v1/nodes", json={**ROOMY, "endpoint": endpoint}, headers=admin
            )
            assert declared.status_code == 201, declared.json()
            run_id = runnable_run(client, auth, state)

            body = execute(client, auth, run_id)

            assert body["completed"] is False
            assert body["state"] == str(RunState.NEEDS_INPUT)
            assert "no credential" in body["reason"]
            #: Not one step's product exists on the run's wires - only the root input it was launched
            #: with - so nothing was quietly executed here instead.
            assert set(body["assets_produced"]) == {"counts"}
            assert receipts_of(catalog, run_id) == []
        finally:
            stop_node(server, thread)

    def test_a_deployment_whose_machines_are_all_too_small_says_why(
        self, client, auth, admin, state, catalog
    ) -> None:
        state.node_token = NODE_TOKEN
        declared = client.post("/v1/nodes", json=TIGHT, headers=admin)
        assert declared.status_code == 201, declared.json()
        run_id = runnable_run(client, auth, state)

        body = execute(client, auth, run_id)

        assert body["completed"] is False
        assert body["state"] == str(RunState.NEEDS_INPUT)
        assert "nod_laptop_01" in body["reason"]
        assert "the tool wants" in body["reason"], "a refusal without a reason is a dead end"
        assert receipts_of(catalog, run_id) == []


class TestPrivacy:
    """§4.2, §4.4: a strict-local project's work never leaves a machine that can keep the promise."""

    def test_a_strict_local_project_is_not_sent_to_a_cloud_machine(
        self, client, auth, admin, state, catalog, tmp_path: Path
    ) -> None:
        endpoint, (server, thread) = start_node(tmp_path)
        state.node_token = NODE_TOKEN
        try:
            #: The machine is reachable and big enough; only the project's tier rules it out, so the
            #: refusal cannot be explained by an unreachable endpoint or a small machine.
            declared = client.post("/v1/nodes", json={**CLOUD, "endpoint": endpoint}, headers=admin)
            assert declared.status_code == 201, declared.json()
            run_id = runnable_run(
                client,
                auth,
                state,
                plan_id="plan_privacy_001",
                project_id=LOCAL_PROJECT_ID,
                privacy_tier="strict_local",
            )

            body = execute(client, auth, run_id, project_id=LOCAL_PROJECT_ID)

            assert body["completed"] is False
            assert body["state"] == str(RunState.NEEDS_INPUT)
            assert "nod_cloud_01" in body["reason"]
            assert "strict-local" in body["reason"]
            #: Not one step's product exists, and no receipt claims one: the work did not go to the
            #: cloud machine and it did not quietly run in the control plane either.
            assert set(body["assets_produced"]) == {"counts"}
            assert receipts_of(catalog, run_id, project_id=LOCAL_PROJECT_ID) == []
        finally:
            stop_node(server, thread)
