"""Authentication, the error model, forced pagination and trace propagation (sections 9.7, 11.2)."""

from datetime import timedelta

from bioadapt_api.errors import classify, error_body
from bioadapt_api.identity import Hs256Verifier, IdTokenClaims
from bioadapt_policies.access import MAX_PAGE_SIZE, Role
from bioadapt_schemas.errors import ErrorClass
from bioadapt_storage.catalog import ConcurrentWriteError
from conftest import ANALYST, AUD, ISS, NOW, OTHER_PROJECT_ID, PI, PROJECT_ID, SECRET


def test_missing_token_is_401_in_the_standard_shape(client):
    response = client.get(f"/v1/projects/{PROJECT_ID}")
    assert response.status_code == 401
    body = response.json()
    assert set(body) == {"code", "class", "field", "message", "retryable", "retry_after", "trace_id"}
    assert body["class"] == str(ErrorClass.POLICY_BLOCKED)
    assert body["code"] == "unauthenticated"


def test_a_tampered_signature_is_401(client, auth):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    token = headers["Authorization"].removeprefix("Bearer ")
    head, payload, _ = token.split(".")
    #: A well-formed token whose payload was rewritten still carries three parts, so the signature
    #: check is what stops it - and the answer has to say that, not merely differ from 200.
    forged = client.get(f"/v1/projects/{PROJECT_ID}", headers={"Authorization": f"Bearer {head}.{payload}.AAAA"})
    assert forged.status_code == 401
    assert forged.json()["code"] == "unauthenticated"
    assert "signature" in forged.json()["message"]

    #: A token that is not a compact JWS at all fails on shape, before any key is touched.
    truncated = client.get(f"/v1/projects/{PROJECT_ID}", headers={"Authorization": f"Bearer {head}.{payload}"})
    assert truncated.status_code == 401
    assert truncated.json()["code"] == "unauthenticated"
    assert "compact JWS" in truncated.json()["message"]


def test_an_expired_token_is_401(client, auth):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR], ttl=-60)
    response = client.get(f"/v1/projects/{PROJECT_ID}", headers=headers)
    assert response.status_code == 401
    assert "expired" in response.json()["message"]


def test_a_token_for_another_audience_is_rejected(client):
    claims = IdTokenClaims(
        sub=PI, iss=ISS, aud="some-other-service", roles=[Role.PRINCIPAL_INVESTIGATOR],
        project_ids=[PROJECT_ID], exp=int(NOW.timestamp()) + 300,
    )
    foreign = Hs256Verifier(SECRET, issuer=ISS, audience="some-other-service")
    response = client.get(f"/v1/projects/{PROJECT_ID}", headers={"Authorization": f"Bearer {foreign.issue(claims)}"})
    assert response.status_code == 401
    assert "audience" in response.json()["message"]


def test_reading_another_projects_data_is_denied_and_audited(client, auth, catalog):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR], project_ids=[PROJECT_ID])
    response = client.get(f"/v1/projects/{OTHER_PROJECT_ID}", headers=headers)
    assert response.status_code == 403
    assert response.json()["class"] == str(ErrorClass.POLICY_BLOCKED)
    # The denial row belongs to the project the resource is in, and a session's scope no longer leaks
    # into whoever reads next, so the audit trail is read under that scope.
    catalog.set_scope(OTHER_PROJECT_ID)
    denials = [
        entry for entry in catalog.audit_entries(action="project:read", limit=50) if not entry["details"]["allowed"]
    ]
    assert denials and denials[0]["subject_id"] == PI
    assert denials[0]["decision_id"]


def test_lists_default_to_50_and_are_capped_at_the_policy_maximum(client, auth, state):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    default = client.get(f"/v1/projects/{PROJECT_ID}/assets", headers=headers).json()
    assert default["limit"] == 50
    oversized = client.get(f"/v1/projects/{PROJECT_ID}/assets?limit=100000", headers=headers).json()
    assert oversized["limit"] == MAX_PAGE_SIZE == 500


def test_a_page_past_the_end_is_empty_not_an_error(client, auth):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    page = client.get(f"/v1/projects/{PROJECT_ID}/assets?limit=1&offset=10", headers=headers).json()
    assert page["items"] == []
    assert page["next_offset"] is None


def test_an_unknown_route_still_uses_the_error_model(client, auth):
    response = client.get("/v1/nope", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert response.status_code == 404
    assert response.json()["class"] == str(ErrorClass.NOT_FOUND)


def test_a_rejected_body_names_the_offending_field(client, auth):
    response = client.post("/v1/projects", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]), json={"project_id": "p"})
    assert response.status_code == 400
    body = response.json()
    assert body["class"] == str(ErrorClass.VALIDATION)
    assert body["field"]


def test_a_gate_write_that_lost_the_race_is_a_retryable_conflict():
    """§9.2: the write that arrived second is told the gate moved, not that the gate is missing."""
    problem = classify(ConcurrentWriteError("approval apr_01 is no longer held by user_pi_01"))
    assert problem.status_code == 409
    body = error_body(problem.error, "trace_cas_01")
    assert (body["code"], body["class"], body["retryable"]) == ("concurrent_write", str(ErrorClass.CONFLICT), True)
    assert "no longer held by" in body["message"]


def test_the_trace_id_is_reused_and_echoed_and_every_request_logs_one_line(client, auth, state):
    headers = auth(ANALYST, [Role.ANALYST])
    before = len(state.log_sink.events)
    response = client.get(f"/v1/projects/{PROJECT_ID}", headers={**headers, "X-Trace-Id": "tr_supplied_0001"})
    assert response.headers["X-Trace-Id"] == "tr_supplied_0001"
    lines = state.log_sink.events[before:]
    assert len(lines) == 1
    assert lines[0]["trace_id"] == "tr_supplied_0001"
    assert lines[0]["action"] == f"GET /v1/projects/{PROJECT_ID}"
    assert lines[0]["status"] == "200"


def test_a_malformed_inbound_trace_id_is_replaced_not_passed_through(client, auth, state):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    response = client.get(f"/v1/projects/{PROJECT_ID}", headers={**headers, "X-Trace-Id": "forged (trace) id"})
    assert response.headers["X-Trace-Id"].startswith("tr_")


def test_authorization_decisions_are_written_even_when_they_allow(client, auth, catalog):
    catalog.set_scope(PROJECT_ID)
    before = len(catalog.audit_entries(limit=500))
    client.get(f"/v1/projects/{PROJECT_ID}", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    entries = catalog.audit_entries(limit=500)
    assert len(entries) > before
    assert all(entry["decision_id"] for entry in entries[before:])
    assert all(entry["subject_kind"] == "human" for entry in entries[before:])


def test_the_clock_drives_token_expiry(client, auth, state):
    state.clock = lambda: NOW + timedelta(seconds=400)
    response = client.get(f"/v1/projects/{PROJECT_ID}", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert response.status_code == 401
