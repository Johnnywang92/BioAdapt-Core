"""Projects, plans and client-side direct upload (sections 5.8, 5.9, 9.3, 9.7)."""

import hashlib

import pytest
from bioadapt_policies.access import MAX_PRESIGNED_TTL_SECONDS, Role
from bioadapt_schemas.enums import PrivacyTier, UsageScope
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from bioadapt_storage.objectstore import BUCKET, object_key
from conftest import ANALYST, ATTESTED_CUTS, OTHER_PROJECT_ID, PI, PROJECT_ID, SERVICE, asset, other_asset, plan, project


@pytest.fixture(autouse=True)
def stage_0_cuts(catalog):
    """Every test here is about the confirmation act, so the project starts in stage 0: un-attested cuts."""
    catalog.set_scope(PROJECT_ID).update_project_thresholds(DEFAULT_THRESHOLDS)
    return catalog.get_project().thresholds


def _declaration(**overrides):
    payload = overrides.pop("payload", b"counts-matrix-bytes")
    fields = {
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "asset_id": "as_upload",
    }
    fields.update(overrides)
    return asset(**fields).model_dump(mode="json")


def test_project_creation_echoes_the_tier_and_is_readable(client, auth):
    new_project = "prj_new"
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR], project_ids=[new_project])
    created = client.post("/v1/projects", json=project(new_project).model_dump(mode="json"), headers=headers)
    assert created.status_code == 201
    assert created.json()["privacy_tier"] == str(PrivacyTier.RESTRICTED)
    read = client.get(f"/v1/projects/{new_project}", headers=headers)
    assert read.status_code == 200
    assert read.json()["project_id"] == new_project
    #: A registration is not a confirmation: the project it creates judges against un-attested cuts (§1.5).
    assert read.json()["thresholds"]["confirmed"] is False


def test_a_registration_cannot_attest_its_own_cut_set(client, auth, catalog):
    """§1.5: otherwise the request that opens a project would also open its conclusion exit."""
    self_attested = project("prj_selfattested", thresholds=ATTESTED_CUTS).model_dump(mode="json")
    response = client.post(
        "/v1/projects",
        json=self_attested,
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR], project_ids=["prj_selfattested"]),
    )
    assert response.status_code == 400
    assert response.json()["field"] == "thresholds.confirmed"
    with pytest.raises(LookupError):
        catalog.set_scope("prj_selfattested").get_project()


#: Appendix C1: a confirmation re-states every §1.5 number, so this is what the request body looks like.
MEASURED = "acme-2026-10-baseline"


def _cut_set(**overrides) -> dict:
    stated = DEFAULT_THRESHOLDS.model_dump(mode="json")
    stated.pop("confirmed")
    stated["version"] = MEASURED
    stated.update(overrides)
    return stated


MEASURED_RATIONALE = "counted the candidate distribution of three past projects"


def _confirm(client, auth, *, headers=None, thresholds=None, rationale=MEASURED_RATIONALE):
    return client.post(
        f"/v1/projects/{PROJECT_ID}/thresholds/confirm",
        json={"thresholds": _cut_set() if thresholds is None else thresholds, "rationale": rationale},
        headers=headers or auth(PI, [Role.PRINCIPAL_INVESTIGATOR], amr=["totp"]),
    )


def _confirmations(catalog) -> list[dict]:
    """Only the attestation rows: an allowed decision is audited under the same action too."""
    rows = catalog.audit_entries(action="thresholds:confirm", limit=20)
    return [row for row in rows if "to_version" in row["details"]]


def test_a_confirmation_states_the_measured_cuts_and_lands_on_the_project(client, auth, catalog):
    response = _confirm(client, auth)
    assert response.status_code == 200, response.text
    stored = response.json()["thresholds"]
    assert (stored["version"], stored["confirmed"]) == (MEASURED, True)
    assert catalog.get_project().thresholds.version == MEASURED
    read = client.get(f"/v1/projects/{PROJECT_ID}", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert read.json()["thresholds"]["confirmed"] is True
    assert [row["subject_id"] for row in _confirmations(catalog)] == [PI]
    assert _confirmations(catalog)[0]["details"] == {
        "from_version": "v0-unconfirmed",
        "to_version": MEASURED,
        "rationale": MEASURED_RATIONALE,
    }


def test_confirming_a_cut_set_is_a_human_act_with_a_second_factor(client, auth):
    no_mfa = _confirm(client, auth, headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert no_mfa.status_code == 403
    assert "second factor" in no_mfa.json()["message"]
    analyst = _confirm(client, auth, headers=auth(ANALYST, [Role.ANALYST], amr=["totp"]))
    assert analyst.status_code == 403
    assert "thresholds:confirm" in analyst.json()["message"]
    machine = _confirm(client, auth, headers=auth(SERVICE, [Role.SERVICE_AGENT], service=True, amr=["totp"]))
    assert machine.status_code == 403
    assert "human" in machine.json()["message"]


def test_a_confirmation_that_leaves_a_placeholder_out_or_attests_itself_is_refused(client, auth, catalog):
    partial = _cut_set()
    partial.pop("rare_min_donors")
    response = _confirm(client, auth, thresholds=partial)
    assert response.status_code == 400
    assert response.json()["field"] == "thresholds"
    assert "rare_min_donors" in response.json()["message"]

    attested = _confirm(client, auth, thresholds=_cut_set(confirmed=True))
    assert attested.status_code == 400
    assert attested.json()["field"] == "thresholds.confirmed"
    assert catalog.get_project().thresholds.confirmed is False


def test_the_same_cut_set_is_not_confirmed_twice(client, auth, catalog):
    assert _confirm(client, auth).status_code == 200
    again = _confirm(client, auth)
    assert again.status_code == 409
    assert "already the one in force" in again.json()["message"]
    #: The refusal is the point: a re-stated version does not silently re-file the attestation.
    assert len(_confirmations(catalog)) == 1


def test_upload_handout_clamps_the_ttl_and_stays_inside_the_project_prefix(client, auth):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    response = client.post(
        f"/v1/projects/{PROJECT_ID}/assets?ttl_seconds=9999", json=_declaration(), headers=headers
    )
    assert response.status_code == 200
    grant = response.json()
    assert grant["ttl_seconds"] == MAX_PRESIGNED_TTL_SECONDS
    assert grant["method"] == "PUT"
    assert grant["key"].startswith(f"{BUCKET}/{PROJECT_ID}/")
    assert f"method=PUT" in grant["upload_url"]


def test_a_ttl_the_policy_refuses_is_a_client_error(client, auth):
    response = client.post(
        f"/v1/projects/{PROJECT_ID}/assets?ttl_seconds=0", json=_declaration(), headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    )
    assert response.status_code == 400
    assert "positive" in response.json()["message"]


def test_registration_is_refused_until_the_bytes_are_verified(client, auth, state):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    declaration = _declaration(asset_id="as_missing_object")
    key = client.post(f"/v1/projects/{PROJECT_ID}/assets", json=declaration, headers=headers).json()["key"]
    nothing = client.post(
        f"/v1/projects/{PROJECT_ID}/assets/as_missing_object/complete", json=declaration, headers=headers
    )
    assert nothing.status_code == 409 and "nothing has been uploaded" in nothing.json()["message"]

    state.objects.put(key, b"different bytes than declared")
    mismatch = client.post(
        f"/v1/projects/{PROJECT_ID}/assets/as_missing_object/complete", json=declaration, headers=headers
    )
    assert mismatch.status_code == 409
    assert "file_sha256" in mismatch.json()["message"]


def test_size_is_checked_too(client, auth, state):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    payload = b"declared-one-byte"
    declaration = _declaration(asset_id="as_size", payload=payload)
    declaration = {**declaration, "size_bytes": len(payload) + 500}
    key = client.post(f"/v1/projects/{PROJECT_ID}/assets", json=declaration, headers=headers).json()["key"]
    state.objects.put(key, payload)
    response = client.post(f"/v1/projects/{PROJECT_ID}/assets/as_size/complete", json=declaration, headers=headers)
    assert response.status_code == 409
    assert "size" in response.json()["message"]


def test_a_verified_upload_becomes_a_readable_asset_with_the_platform_uri(client, auth, state):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    payload = b"verified-counts"
    declaration = _declaration(asset_id="as_verified", payload=payload)
    grant = client.post(f"/v1/projects/{PROJECT_ID}/assets", json=declaration, headers=headers).json()
    state.objects.put(grant["key"], payload)
    completed = client.post(f"/v1/projects/{PROJECT_ID}/assets/as_verified/complete", json=declaration, headers=headers)
    assert completed.status_code == 201
    assert completed.json()["uri"] == f"s3://{grant['key']}"
    read = client.get(f"/v1/projects/{PROJECT_ID}/assets/as_verified", headers=headers).json()
    assert read["asset_id"] == "as_verified"
    download = client.get(f"/v1/projects/{PROJECT_ID}/assets/as_verified/download", headers=headers)
    assert download.status_code == 200
    assert download.json()["method"] == "GET"
    assert download.json()["key"] == grant["key"]


def test_the_storage_quota_blocks_a_grant_before_any_bytes_move(client, auth):
    declaration = _declaration(asset_id="as_big", payload=b"x" * 20_000, size_bytes=20_000)
    response = client.post(f"/v1/projects/{PROJECT_ID}/assets", json=declaration, headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert response.status_code == 429
    assert response.json()["class"] == str(ErrorClass.RESOURCE_EXCEEDED)
    assert response.json()["retryable"] is True
    assert response.headers["Retry-After"] == "30"


def test_listing_assets_filters_by_retention_class(client, auth):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    ephemeral = client.get(f"/v1/projects/{PROJECT_ID}/assets?retention_class=ephemeral", headers=headers).json()
    assert [item["asset_id"] for item in ephemeral["items"]] == ["as_0001"]
    archived = client.get(f"/v1/projects/{PROJECT_ID}/assets?retention_class=archive", headers=headers).json()
    assert archived["items"] == []


def test_downloading_an_asset_without_stored_bytes_is_a_conflict(client, auth):
    response = client.get(
        f"/v1/projects/{PROJECT_ID}/assets/as_0001/download", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    )
    assert response.status_code == 409


def test_a_reusable_asset_from_another_project_resolves_to_its_own_prefix(client, auth, state, catalog):
    catalog.set_scope(OTHER_PROJECT_ID)
    shared = other_asset()
    catalog.record_asset(shared)
    catalog.set_scope(PROJECT_ID)
    headers = auth(ANALYST, [Role.ANALYST], project_ids=[PROJECT_ID])
    missing = client.get(f"/v1/projects/{PROJECT_ID}/assets/{shared.asset_id}/download", headers=headers)
    assert missing.status_code == 409
    state.objects.put(
        object_key(
            project_id=OTHER_PROJECT_ID,
            asset_id=shared.asset_id,
            file_sha256=shared.file_sha256,
            retention_class=shared.retention_class,
            extension=shared.format,
        ),
        b"reference counts from the other project",
    )
    response = client.get(f"/v1/projects/{PROJECT_ID}/assets/{shared.asset_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["project_id"] == OTHER_PROJECT_ID
    download = client.get(f"/v1/projects/{PROJECT_ID}/assets/{shared.asset_id}/download", headers=headers)
    assert download.status_code == 200
    assert download.json()["key"].startswith(f"{BUCKET}/{OTHER_PROJECT_ID}/")


def test_a_project_local_asset_from_another_project_does_not_exist_here(client, auth, catalog):
    catalog.set_scope(OTHER_PROJECT_ID)
    private = other_asset(asset_id="as_private", usage_scope=UsageScope.THIS_PROJECT_ONLY, file_sha256="ef" * 32)
    catalog.record_asset(private)
    catalog.set_scope(PROJECT_ID)
    response = client.get(
        f"/v1/projects/{PROJECT_ID}/assets/as_private", headers=auth(ANALYST, [Role.ANALYST])
    )
    assert response.status_code == 404


def test_a_plan_is_stored_content_addressed_and_immutable(client, auth):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    body = plan(plan_version="2").model_dump(mode="json")
    first = client.post(f"/v1/projects/{PROJECT_ID}/plans", json=body, headers=headers)
    assert first.status_code == 201
    assert len(first.json()["plan_sha256"]) == 64
    again = client.post(f"/v1/projects/{PROJECT_ID}/plans", json=body, headers=headers)
    assert again.status_code == 409


def test_a_plan_naming_another_project_is_rejected(client, auth):
    body = plan(project_id=OTHER_PROJECT_ID).model_dump(mode="json")
    response = client.post(f"/v1/projects/{PROJECT_ID}/plans", json=body, headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert response.status_code == 400
    assert response.json()["field"] == "project_id"


def test_asset_write_needs_a_role_that_holds_it(client, auth):
    response = client.post(
        f"/v1/projects/{PROJECT_ID}/assets", json=_declaration(), headers=auth(ANALYST, [Role.DOMAIN_EXPERT])
    )
    assert response.status_code == 403
    assert response.json()["code"] == "authorization_denied"


def test_direct_upload_asset_endpoint(client, auth):
    decl = _declaration(asset_id="as_direct_01")
    response = client.post(
        f"/v1/projects/{PROJECT_ID}/assets/direct-upload",
        json={"asset": decl, "content_text": '{"cells": ["cell1"], "genes": ["CD4"]}'},
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert response.status_code == 201
    assert response.json()["asset_id"] == "as_direct_01"
    assert response.json()["uri"].startswith("s3://")

