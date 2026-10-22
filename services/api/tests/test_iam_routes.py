"""Key-file login over HTTP (plan V2.0 sections 9.7, 11.2, 11.6, 13.1).

One contract holds this whole shape up: what a session may do is written in the directory by an admin,
never by the key that presents itself - and a revocation is worth something on the next request, not on
the next expiry. Every case below is read against that.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization

from bioadapt_api.deps import DeploymentCapabilities
from bioadapt_api.errors import ApiProblem
from bioadapt_api.login_keys import (
    Ed25519Verifier,
    MAX_ASSERTION_TTL_SECONDS,
    generate_login_key,
    sign_assertion,
)
from bioadapt_policies.access import GLOBAL_SCOPE, Role

from conftest import ANALYST, AUD, ISS, PI, PROJECT_ID, SERVICE

ADMIN = "user_platform_admin_01"


@pytest.fixture
def key_directory(state):
    """A deployment that runs on key files: the verifier reads the directory the admin writes."""
    state.login_keys = Ed25519Verifier(state.catalogs, issuer=ISS, audience=AUD)
    return state.login_keys


def admin_auth(auth):
    return auth(ADMIN, [Role.PLATFORM_ADMIN], project_ids=[GLOBAL_SCOPE], amr=["pwd+mfa"])


def register(
    client,
    auth,
    *,
    key=None,
    subject=ANALYST,
    roles=(Role.ANALYST,),
    projects=(PROJECT_ID,),
    amr=("pwd+mfa",),
    service=False,
    solo=False,
):
    key = key or generate_login_key()
    body = {
        "subject_id": subject,
        "public_key": key.public_pem,
        "roles": [str(role) for role in roles],
        "project_ids": list(projects),
        "amr": list(amr),
        "solo_mode": solo,
        "is_service_account": service,
        "label": "workstation",
    }
    return client.post("/v1/iam/credentials", json=body, headers=admin_auth(auth)), key


def assertion(key, subject=ANALYST, ttl=60):
    return sign_assertion(
        key.private_pem, kid=key.kid, subject_id=subject, issuer=ISS, audience=AUD, ttl_seconds=ttl
    )


def exchange(client, token_assertion):
    return client.post("/v1/token/exchange", json={"assertion": token_assertion})


def payload_of(token: str) -> dict:
    segment = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def sign_anything(key, kid: str, payload: dict, *, alg: str = "EdDSA") -> str:
    """A presentation built by hand, to show what the verifier refuses to read off a signature."""
    private = serialization.load_pem_private_key(key.private_pem.encode(), password=None)

    def segment(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = segment(json.dumps({"alg": alg, "typ": "JWT", "kid": kid}, separators=(",", ":")).encode())
    body = segment(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    return f"{header}.{body}.{segment(private.sign(signing_input))}"


class TestRegistration:
    def test_an_admin_gives_a_key_its_roles_and_the_key_does_not(self, client, auth, catalog):
        response, key = register(client, auth, roles=(Role.ANALYST, Role.DOMAIN_EXPERT), amr=())
        assert response.status_code == 201, response.json()
        row = response.json()
        assert row["kid"] == key.kid and row["fingerprint"] == key.fingerprint
        assert row["roles"] == ["analyst", "domain_expert"]
        #: The directory text is the verifier's business, not a screen's.
        assert "public_key" not in row
        stored = catalog.set_scope(GLOBAL_SCOPE).get_credential(key.kid)
        assert stored["public_key"] == key.public_pem
        assert stored["created_by"] == ADMIN

    def test_granting_a_role_is_a_human_act_with_a_second_factor(self, client, auth):
        no_factor = auth(ADMIN, [Role.PLATFORM_ADMIN], project_ids=[GLOBAL_SCOPE])
        refused = client.post(
            "/v1/iam/credentials",
            json={
                "subject_id": ANALYST,
                "public_key": generate_login_key().public_pem,
                "roles": ["analyst"],
                "project_ids": [PROJECT_ID],
            },
            headers=no_factor,
        )
        assert refused.status_code == 403
        assert "iam:grant_role requires a second factor" in refused.json()["message"]

        outsider = auth(PI, [Role.PRINCIPAL_INVESTIGATOR], amr=["pwd+mfa"])
        denied = client.post(
            "/v1/iam/credentials",
            json={
                "subject_id": "user_new_analyst",
                "public_key": generate_login_key().public_pem,
                "roles": ["analyst"],
                "project_ids": [PROJECT_ID],
            },
            headers=outsider,
        )
        assert denied.status_code == 403
        assert "no role of ['principal_investigator'] grants iam:grant_role" in denied.json()["message"]

    def test_a_key_is_not_granted_roles_a_live_session_would_refuse(self, client, auth):
        """§13.1 is checked where the credential is written, not discovered at the first login."""
        response, _ = register(client, auth, roles=(Role.EVALUATION_LEAD, Role.ANALYST))
        assert response.status_code == 400
        assert response.json()["field"] == "roles"
        assert "may not be held together" in response.json()["message"]

    def test_a_key_registered_as_a_machine_may_hold_no_other_duty(self, client, auth):
        """§11.4: being an engine is a fact of the row, so the row cannot also carry a person's roles."""
        response, _ = register(client, auth, roles=(Role.ANALYST,), service=True)
        assert response.status_code == 400
        assert response.json()["field"] == "roles"
        assert "a service account may only hold the service_agent role" in response.json()["message"]

    def test_the_same_key_registered_twice_is_a_conflict(self, client, auth):
        key = generate_login_key()
        first, _ = register(client, auth, key=key, subject=ANALYST)
        assert first.status_code == 201
        second, _ = register(client, auth, key=key, subject="user_other")
        assert second.status_code == 409
        assert key.kid in second.json()["message"]

    def test_only_a_global_scope_actor_reads_the_directory(self, client, auth):
        response = client.get("/v1/iam/credentials", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
        assert response.status_code == 403
        listed = client.get("/v1/iam/credentials", headers=admin_auth(auth))
        assert listed.status_code == 200
        assert listed.json()["items"] == []


class TestExchange:
    def test_a_key_file_buys_a_short_token_that_carries_the_directory_row(self, client, auth, state, key_directory):
        response, key = register(client, auth, roles=(Role.ANALYST,), amr=("pwd+mfa",))
        assert response.status_code == 201
        state.login_token_ttl = timedelta(minutes=3)
        bought = exchange(client, assertion(key))
        assert bought.status_code == 200, bought.json()
        body = bought.json()
        assert body["expires_in"] == 180
        assert body["roles"] == ["analyst"] and body["project_ids"] == [PROJECT_ID]
        claims = payload_of(body["access_token"])
        assert claims["sub"] == ANALYST and claims["amr"] == ["pwd+mfa"]
        #: The minted token is aged by the same clock every other route checks it against; §9.2 keeps the
        #: window a deployment input rather than a number this test could disagree with.
        assert claims["exp"] - int(state.clock().timestamp()) == 180
        read = client.get(f"/v1/projects/{PROJECT_ID}", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert read.status_code == 200

    def test_an_engine_key_buys_a_session_that_is_still_not_a_person(self, client, auth, key_directory):
        """§11.4 does not bend because a pipeline now signs in the way a person does.

        The flag is what the gate reads, not the role name, so a key registered as a machine has to
        produce a machine session - and every action only a person may take stays closed to it.
        """
        response, key = register(
            client, auth, subject=SERVICE, roles=(Role.SERVICE_AGENT,), service=True, amr=()
        )
        assert response.status_code == 201, response.json()
        assert response.json()["is_service_account"] is True
        body = exchange(client, assertion(key, subject=SERVICE)).json()
        assert payload_of(body["access_token"])["is_service_account"] is True
        refused = client.post(
            "/v1/iam/credentials",
            json={
                "subject_id": "user_new_analyst",
                "public_key": generate_login_key().public_pem,
                "roles": ["analyst"],
                "project_ids": [PROJECT_ID],
            },
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert refused.status_code == 403
        assert "must be performed by a human, not a service account" in refused.json()["message"]

    def test_an_assertion_states_who_and_nothing_about_authority(self, client, auth, key_directory):
        response, key = register(client, auth)
        assert response.status_code == 201
        forged = sign_anything(
            key,
            key.kid,
            {"sub": ANALYST, "iss": ISS, "aud": AUD, "iat": 1, "exp": 2, "roles": ["platform_admin"]},
        )
        refused = exchange(client, forged)
        assert refused.status_code == 401
        assert "sub, iss, aud, iat and exp only" in refused.json()["message"]

    def test_a_signature_over_a_token_of_another_algorithm_is_refused(self, client, auth, key_directory):
        response, key = register(client, auth)
        assert response.status_code == 201
        body = {"sub": ANALYST, "iss": ISS, "aud": AUD, "iat": 1, "exp": 2}
        refused = exchange(client, sign_anything(key, key.kid, body, alg="HS256"))
        assert refused.status_code == 401
        assert "only a EdDSA login assertion is accepted here" in refused.json()["message"]

    def test_a_stolen_key_cannot_open_someone_elses_session(self, client, auth, key_directory):
        response, key = register(client, auth, subject=ANALYST)
        assert response.status_code == 201
        refused = exchange(client, assertion(key, subject=PI))
        assert refused.status_code == 401
        assert f"registered to {ANALYST}" in refused.json()["message"]

    def test_an_assertion_signed_for_longer_than_the_window_is_refused(self, client, auth, key_directory):
        """The bound is on the signed bytes, not on the two clocks agreeing.

        A login helper refuses to sign a wider window, but a key holder with their own tooling can, and
        the platform's answer has to be no either way.
        """
        response, key = register(client, auth)
        assert response.status_code == 201
        started = int(datetime.now(UTC).timestamp())
        wide = {"sub": ANALYST, "iss": ISS, "aud": AUD, "iat": started, "exp": started + MAX_ASSERTION_TTL_SECONDS + 60}
        refused = exchange(client, sign_anything(key, key.kid, wide))
        assert refused.status_code == 401
        assert "signed for at most 120 seconds" in refused.json()["message"]
        with pytest.raises(ApiProblem):
            assertion(key, ttl=MAX_ASSERTION_TTL_SECONDS + 60)

    def test_the_exchange_closes_when_no_directory_is_installed(self, client, state):
        assert state.login_keys is None
        refused = exchange(client, assertion(generate_login_key()))
        assert refused.status_code == 401
        assert "registers no login keys" in refused.json()["message"]


class TestRevocation:
    def test_revoking_a_key_is_worth_something_on_the_next_request(
        self, client, auth, state, catalog, key_directory
    ):
        response, key = register(client, auth)
        assert response.status_code == 201
        assert exchange(client, assertion(key)).status_code == 200

        revoked = client.post(f"/v1/iam/credentials/{key.kid}/revoke", headers=admin_auth(auth))
        assert revoked.status_code == 200
        assert revoked.json()["revoked_by"] == ADMIN

        refused = exchange(client, assertion(key))
        assert refused.status_code == 401
        assert "not registered here, or it has been revoked" in refused.json()["message"]

        entries = catalog.set_scope(GLOBAL_SCOPE).audit_entries(action="credential:login", limit=10)
        assert [entry["subject_id"] for entry in entries] == [ANALYST]
        again = client.post(f"/v1/iam/credentials/{key.kid}/revoke", headers=admin_auth(auth))
        assert again.status_code == 409

    def test_a_revoked_key_does_not_become_registerable_again(self, client, auth):
        """Revocation is final for the key pair: reinstating a person is a new key, not an un-revoke."""
        response, key = register(client, auth)
        assert response.status_code == 201
        assert client.post(f"/v1/iam/credentials/{key.kid}/revoke", headers=admin_auth(auth)).status_code == 200
        again, _ = register(client, auth, key=key)
        assert again.status_code == 409
        assert key.kid in again.json()["message"]

    def test_a_revocation_needs_the_same_human_authority_as_the_grant(self, client, auth):
        response, key = register(client, auth)
        assert response.status_code == 201
        refused = client.post(
            f"/v1/iam/credentials/{key.kid}/revoke", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
        )
        assert refused.status_code == 403


class TestDevelopmentTokens:
    def test_a_configured_deployment_hands_out_no_roles_to_whoever_asks(self, client, state):
        assert "solo" in client.get("/v1/development/tokens").json()
        state.deployment = DeploymentCapabilities(mode="configured")
        assert client.get("/v1/development/tokens").json() == {}
