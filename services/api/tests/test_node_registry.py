"""The compute-node registry over HTTP (plan V2.0 sections 4.2, 4.4, 9.4, 9.6, 11.1).

A deployment runs its runs somewhere, and the registry is where that somewhere is written down. The
cases below hold one line: a node is a *declaration*, it is somebody's act, and a run is matched
against what the machine says it has rather than against a named machine.
"""

from __future__ import annotations

import pytest

from bioadapt_policies.access import GLOBAL_SCOPE, Role

from conftest import ANALYST, PI, PROJECT_ID, SERVICE

ADMIN = "user_platform_admin_01"

ROOMY = {
    "node_id": "nod_server_01",
    "label": "lab server",
    "position": "local",
    "capabilities": {
        "cpu": 16,
        "memory_mb": 65536,
        "can_run_containers": True,
        "network_mode": "allowlist",
        "allowed_hosts": ["oss.example.com"],
        "data_access": {"oss_buckets": ["seq-raw"], "local_paths": ["/data/seq"], "public_sources": ["cellxgene"]},
    },
}

TIGHT = {
    "node_id": "nod_laptop_01",
    "label": "analyst laptop",
    "position": "local",
    "capabilities": {"cpu": 1, "memory_mb": 2048, "can_run_containers": False},
}


@pytest.fixture
def admin_auth(auth):
    return auth(ADMIN, [Role.PLATFORM_ADMIN], project_ids=[GLOBAL_SCOPE], amr=["pwd+mfa"])


@pytest.fixture
def analyst_auth(auth):
    return auth(ANALYST, [Role.ANALYST], project_ids=[GLOBAL_SCOPE], amr=["pwd+mfa"])


def register(client, auth, body, **kwargs) -> dict:
    return client.post("/v1/nodes", json=body, headers=auth, **kwargs)


class TestRegistration:
    def test_a_declaration_records_who_made_it(self, client, admin_auth) -> None:
        response = register(client, admin_auth, ROOMY)
        assert response.status_code == 201
        body = response.json()
        #: The declarer and the moment are the session's, not the request's: a person pressed the
        #: button, and the row says so.
        assert body["registered_by"] == ADMIN
        assert body["registered_at"]

    def test_the_same_node_cannot_be_declared_twice(self, client, admin_auth) -> None:
        assert register(client, admin_auth, ROOMY).status_code == 201
        assert register(client, admin_auth, ROOMY).status_code == 409

    def test_a_service_account_cannot_declare_a_machine(self, client, auth) -> None:
        engine = auth(SERVICE, [Role.SERVICE_AGENT], project_ids=[GLOBAL_SCOPE], service=True)
        assert register(client, engine, ROOMY).status_code == 403

    def test_an_analyst_cannot_declare_a_machine(self, client, analyst_auth) -> None:
        assert register(client, analyst_auth, ROOMY).status_code == 403

    def test_a_declaration_without_a_second_factor_is_refused(self, client, auth) -> None:
        no_mfa = auth(ADMIN, [Role.PLATFORM_ADMIN], project_ids=[GLOBAL_SCOPE], amr=["pwd"])
        assert register(client, no_mfa, ROOMY).status_code == 403

    def test_a_token_outside_the_global_scope_cannot_reach_the_registry(self, client, auth) -> None:
        scoped = auth(ADMIN, [Role.PLATFORM_ADMIN], project_ids=[PROJECT_ID], amr=["pwd+mfa"])
        assert register(client, scoped, ROOMY).status_code == 403


class TestTheEndpoint:
    def test_a_declaration_says_where_the_machine_is_reached(self, client, admin_auth) -> None:
        """A declared machine that says nothing about how to reach it leaves the run nowhere to go."""
        assert register(client, admin_auth, {**ROOMY, "endpoint": "http://10.0.0.7:8765"}).status_code == 201
        detail = client.get("/v1/nodes/nod_server_01", headers=admin_auth)
        assert detail.json()["endpoint"] == "http://10.0.0.7:8765"

    def test_a_machine_without_an_endpoint_is_still_declared(self, client, admin_auth) -> None:
        assert register(client, admin_auth, ROOMY).status_code == 201
        detail = client.get("/v1/nodes/nod_server_01", headers=admin_auth)
        assert detail.json()["endpoint"] is None

    def test_an_address_that_is_not_an_address_is_refused(self, client, admin_auth) -> None:
        response = register(client, admin_auth, {**ROOMY, "endpoint": "not-an-address"})
        assert response.status_code == 400


class TestReading:
    def test_a_declared_node_is_listed(self, client, admin_auth) -> None:
        register(client, admin_auth, ROOMY)
        response = client.get("/v1/nodes", headers=admin_auth)
        assert response.status_code == 200
        assert [row["node_id"] for row in response.json()["items"]] == ["nod_server_01"]

    def test_one_node_is_read_by_its_id(self, client, admin_auth) -> None:
        register(client, admin_auth, ROOMY)
        response = client.get("/v1/nodes/nod_server_01", headers=admin_auth)
        assert response.status_code == 200
        assert response.json()["label"] == "lab server"

    def test_an_unknown_node_is_not_in_this_deployment(self, client, admin_auth) -> None:
        response = client.get("/v1/nodes/nod_nothing_01", headers=admin_auth)
        assert response.status_code == 404
        assert response.json()["code"] == "compute_node_not_found"


class TestPlacement:
    def test_a_node_with_room_is_a_candidate_for_the_tool(self, client, admin_auth) -> None:
        register(client, admin_auth, ROOMY)
        response = client.get("/v1/nodes", params={"can_run": "scrnaseq_qc@1.0.0"}, headers=admin_auth)
        assert response.status_code == 200
        assert [row["node_id"] for row in response.json()["items"]] == ["nod_server_01"]

    def test_a_node_without_room_is_not_a_candidate_and_says_why(self, client, admin_auth) -> None:
        register(client, admin_auth, TIGHT)
        listed = client.get("/v1/nodes", params={"can_run": "scrnaseq_qc@1.0.0"}, headers=admin_auth)
        assert listed.json()["items"] == []
        detail = client.get(
            "/v1/nodes/nod_laptop_01", params={"can_run": "scrnaseq_qc@1.0.0"}, headers=admin_auth
        )
        assert detail.json()["fits"] is False
        assert any("memory" in reason for reason in detail.json()["unmet_requirements"])

    def test_a_node_that_fits_says_it_fits(self, client, admin_auth) -> None:
        register(client, admin_auth, ROOMY)
        detail = client.get(
            "/v1/nodes/nod_server_01", params={"can_run": "scrnaseq_qc@1.0.0"}, headers=admin_auth
        )
        assert detail.json()["fits"] is True
        assert detail.json()["unmet_requirements"] == []

    def test_an_unregistered_tool_is_not_a_placement_question(self, client, admin_auth) -> None:
        response = client.get("/v1/nodes", params={"can_run": "no_such_tool@9.9.9"}, headers=admin_auth)
        assert response.status_code == 404

    def test_a_tool_named_without_a_version_is_refused(self, client, admin_auth) -> None:
        response = client.get("/v1/nodes", params={"can_run": "scrnaseq_qc"}, headers=admin_auth)
        assert response.status_code == 400

    def test_a_fitting_machine_is_found_even_when_it_is_not_on_the_first_page(self, client, admin_auth) -> None:
        register(client, admin_auth, TIGHT)
        register(client, admin_auth, ROOMY)
        #: The page is cut after the filter, not before: otherwise the answer would be "no machine
        #: fits" while the machine that fits waits behind the one that does not.
        response = client.get(
            "/v1/nodes", params={"can_run": "scrnaseq_qc@1.0.0", "limit": 1}, headers=admin_auth
        )
        assert [row["node_id"] for row in response.json()["items"]] == ["nod_server_01"]


class TestDataReach:
    def test_a_node_that_reaches_the_bytes_is_a_candidate(self, client, admin_auth) -> None:
        register(client, admin_auth, ROOMY)
        response = client.get(
            "/v1/nodes", params={"reaches": "oss://seq-raw/run1/matrix.h5ad"}, headers=admin_auth
        )
        assert [row["node_id"] for row in response.json()["items"]] == ["nod_server_01"]

    def test_a_node_that_cannot_reach_the_bytes_is_not(self, client, admin_auth) -> None:
        register(client, admin_auth, TIGHT)
        response = client.get(
            "/v1/nodes", params={"reaches": "oss://seq-raw/run1/matrix.h5ad"}, headers=admin_auth
        )
        assert response.json()["items"] == []

    def test_one_node_says_whether_it_reaches(self, client, admin_auth) -> None:
        register(client, admin_auth, ROOMY)
        response = client.get(
            "/v1/nodes/nod_server_01",
            params={"reaches": "accession://cellxgene/8f2c1d"},
            headers=admin_auth,
        )
        assert response.json()["reaches"] is True
