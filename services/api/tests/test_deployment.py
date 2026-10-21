"""Public deployment declarations are explicit hints, not health checks or access policy."""

from dataclasses import FrozenInstanceError

import pytest

from bioadapt_api.deps import DeploymentCapabilities
from bioadapt_policies.access import Role
from conftest import OTHER_PROJECT_ID, PI, PROJECT_ID


@pytest.mark.parametrize(
    "declaration, expected",
    [
        (
            None,
            {
                "mode": "unknown",
                "object_upload": "unknown",
                "run_execution": "unknown",
                "export_generation": "unknown",
            },
        ),
        (
            DeploymentCapabilities(
                mode="reference",
                object_upload="unavailable",
                run_execution="unavailable",
                export_generation="unavailable",
            ),
            {
                "mode": "reference",
                "object_upload": "unavailable",
                "run_execution": "unavailable",
                "export_generation": "unavailable",
            },
        ),
        (
            DeploymentCapabilities(
                mode="configured",
                object_upload="configured",
                run_execution="configured",
                export_generation="configured",
            ),
            {
                "mode": "configured",
                "object_upload": "configured",
                "run_execution": "configured",
                "export_generation": "configured",
            },
        ),
        (
            DeploymentCapabilities(mode="configured", object_upload="configured", run_execution="unavailable"),
            {
                "mode": "configured",
                "object_upload": "configured",
                "run_execution": "unavailable",
                "export_generation": "unknown",
            },
        ),
    ],
    ids=["default-unknown", "reference", "configured", "mixed"],
)
def test_deployment_is_anonymous_and_returns_only_declared_fields(client, state, monkeypatch, declaration, expected):
    # The same in-memory dependencies and internal URLs must not imply a deployment capability.
    if declaration is not None:
        state.deployment = declaration

    def unexpected_dependency(*args, **kwargs):
        pytest.fail("public deployment declarations must not resolve identity or project scope")

    monkeypatch.setattr(state.verifier, "verify", unexpected_dependency)
    monkeypatch.setattr(state.catalogs, "acquire", unexpected_dependency)
    response = client.get("/v1/deployment")
    assert response.status_code == 200
    # Exact equality excludes hosts, configuration, credentials, projects and body trace metadata.
    assert response.json() == expected


def test_deployment_defaults_are_frozen(state):
    assert state.deployment == DeploymentCapabilities()
    with pytest.raises(FrozenInstanceError):
        state.deployment.mode = "reference"


@pytest.mark.parametrize("supplied", [None, "tr_deployment_0001", "forged (trace) id"])
def test_deployment_uses_the_trace_middleware(client, state, supplied):
    before = len(state.log_sink.events)
    headers = {} if supplied is None else {"X-Trace-Id": supplied}
    response = client.get("/v1/deployment", headers=headers)
    assert response.status_code == 200
    trace_id = response.headers["X-Trace-Id"]
    if supplied == "tr_deployment_0001":
        assert trace_id == supplied
    else:
        assert trace_id.startswith("tr_")
        assert trace_id != supplied
    lines = state.log_sink.events[before:]
    assert len(lines) == 1
    assert lines[0]["trace_id"] == trace_id
    assert lines[0]["action"] == "GET /v1/deployment"
    assert lines[0]["status"] == "200"


@pytest.mark.parametrize("mode", ["unknown", "reference", "configured"])
def test_deployment_declarations_do_not_change_route_authorization(client, state, auth, mode):
    state.deployment = DeploymentCapabilities(
        mode=mode,
        object_upload="unavailable",
        run_execution="unavailable",
        export_generation="unavailable",
    )
    assert client.get(f"/v1/projects/{PROJECT_ID}").status_code == 401
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    assert client.get(f"/v1/projects/{PROJECT_ID}", headers=headers).status_code == 200
    assert client.get(f"/v1/projects/{OTHER_PROJECT_ID}", headers=headers).status_code == 403
