"""Annotation versions, their chain and membership review (sections 5.7, 9.3, 11.6)."""

from bioadapt_policies.access import Role
from bioadapt_schemas.enums import AnnotationStatus, LabelLevel, ReviewDecision, ReviewScope, UsageScope
from bioadapt_schemas.errors import ErrorClass
from conftest import (
    ANALYST,
    COMPLIANCE,
    EXPERT,
    OTHER_PROJECT_ID,
    PI,
    PROJECT_ID,
    RUN_ID,
    asset,
    other_asset,
)

BASE = f"/v1/projects/{PROJECT_ID}"
ASSET_ID = "as_0001"
COLUMN = "celltype"
LEVEL = str(LabelLevel.CELL_TYPE_FINE)


def _record(client, auth, *, asset_id=ASSET_ID, run_id=RUN_ID, label_column=COLUMN, **extra):
    body = {"run_id": run_id, "asset_id": asset_id, "label_column": label_column, "level": LEVEL}
    body.update(extra)
    return client.post(
        f"{BASE}/annotations", json=body, headers=auth(ANALYST, [Role.ANALYST])
    )


def _read(client, auth, version_id: str) -> dict:
    return client.get(f"{BASE}/annotations/{version_id}", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()


def test_an_annotation_version_is_indexed_and_points_at_its_asset(client, auth):
    created = _record(client, auth)
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == str(AnnotationStatus.ACTIVE)
    assert body["asset_id"] == ASSET_ID
    assert body["created_by"] == ANALYST
    read = _read(client, auth, body["version_id"])
    assert read["is_active"] is True
    assert read["label_column"] == COLUMN


def test_a_second_active_version_must_name_what_it_replaces(client, auth, catalog):
    first = _record(client, auth).json()["version_id"]
    catalog.set_scope(PROJECT_ID).record_asset(asset(asset_id="as_0002", file_sha256="ee" * 32))
    response = _record(client, auth, asset_id="as_0002")
    assert response.status_code == 409
    assert response.json()["field"] == "supersedes_version_id"
    assert first in response.json()["message"]
    assert _read(client, auth, first)["is_active"] is True


def test_recording_a_replacement_retires_the_version_it_supersedes(client, auth, catalog):
    first = _record(client, auth).json()["version_id"]
    catalog.set_scope(PROJECT_ID).record_asset(asset(asset_id="as_0002", file_sha256="ee" * 32))
    second = _record(client, auth, asset_id="as_0002", supersedes_version_id=first)
    assert second.status_code == 201
    retired = _read(client, auth, first)
    assert retired["status"] == str(AnnotationStatus.SUPERSEDED)
    assert retired["superseded_by"] == second.json()["version_id"]
    assert retired["is_active"] is False


def test_naming_a_replacement_for_a_run_without_labels_is_not_enough(client, auth):
    response = _record(client, auth, supersedes_version_id="av_never_recorded")
    assert response.status_code == 404
    assert response.json()["class"] == str(ErrorClass.NOT_FOUND)


def test_labels_cannot_be_attached_to_a_run_that_does_not_exist(client, auth):
    response = _record(client, auth, run_id="run_missing")
    assert response.status_code == 404
    assert response.json()["class"] == str(ErrorClass.NOT_FOUND)


def test_an_asset_that_is_not_here_cannot_be_labelled(client, auth, catalog):
    missing = _record(client, auth, asset_id="as_missing")
    assert missing.status_code == 404
    catalog.set_scope(OTHER_PROJECT_ID).record_asset(other_asset(asset_id="as_locked", usage_scope=UsageScope.THIS_PROJECT_ONLY))
    refused = _record(client, auth, asset_id="as_locked")
    assert refused.status_code == 404


def test_a_role_without_write_rights_cannot_record_labels(client, auth):
    response = client.post(
        f"{BASE}/annotations",
        json={"run_id": RUN_ID, "asset_id": ASSET_ID, "label_column": COLUMN, "level": LEVEL},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 403


def test_an_annotation_review_is_recorded_and_changes_nothing(client, auth, catalog):
    version_id = _record(client, auth).json()["version_id"]
    response = client.post(
        f"{BASE}/annotations/{version_id}/review",
        json={"decision": str(ReviewDecision.APPROVE), "rationale": "spot-checked 200 cells against the markers"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 200
    assert response.json()["status"] == str(AnnotationStatus.ACTIVE)
    listing = client.get(
        f"{BASE}/annotations/{version_id}/reviews", headers=auth(EXPERT, [Role.DOMAIN_EXPERT])
    ).json()["reviews"]
    assert len(listing) == 1
    assert listing[0]["scope"] == str(ReviewScope.MEMBERSHIP)
    assert listing[0]["object_type"] == "annotation_version"


def test_returning_a_label_set_for_analysis_is_its_own_record(client, auth):
    version_id = _record(client, auth).json()["version_id"]
    response = client.post(
        f"{BASE}/annotations/{version_id}/review",
        json={"decision": str(ReviewDecision.RETURN_FOR_ANALYSIS), "rationale": "the coarse level is not usable"},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert response.status_code == 200
    assert _read(client, auth, version_id)["is_active"] is True


def test_labels_cannot_be_rejected_in_place(client, auth):
    version_id = _record(client, auth).json()["version_id"]
    response = client.post(
        f"{BASE}/annotations/{version_id}/review",
        json={"decision": str(ReviewDecision.REJECT), "rationale": "wrong marker panel"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 400
    assert response.json()["field"] == "decision"
    assert "supersedes_version_id" in response.json()["message"]
    assert _read(client, auth, version_id)["is_active"] is True


def test_a_card_scope_review_is_not_accepted_on_an_annotation(client, auth):
    version_id = _record(client, auth).json()["version_id"]
    response = client.post(
        f"{BASE}/annotations/{version_id}/review",
        json={
            "scope": "card",
            "decision": str(ReviewDecision.MARK_UNKNOWN),
            "rationale": "trying to demote a label set",
        },
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 400


def test_the_chain_is_listed_for_one_run_and_column(client, auth, catalog):
    first = _record(client, auth).json()["version_id"]
    catalog.set_scope(PROJECT_ID).record_asset(asset(asset_id="as_0002", file_sha256="ee" * 32))
    second = _record(client, auth, asset_id="as_0002", supersedes_version_id=first).json()["version_id"]
    body = client.get(
        f"{BASE}/annotations?run_id={RUN_ID}&label_column={COLUMN}",
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    ).json()
    assert [item["version_id"] for item in body["items"]] == [first, second]
    assert body["next_offset"] is None


def test_the_chain_pages_like_every_other_list(client, auth, catalog):
    first = _record(client, auth).json()["version_id"]
    catalog.set_scope(PROJECT_ID).record_asset(asset(asset_id="as_0002", file_sha256="ee" * 32))
    _record(client, auth, asset_id="as_0002", supersedes_version_id=first)
    page = client.get(
        f"{BASE}/annotations?run_id={RUN_ID}&label_column={COLUMN}&limit=1",
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    ).json()
    assert [item["version_id"] for item in page["items"]] == [first]
    assert page["next_offset"] == 1


def test_listing_annotations_needs_both_chain_keys(client, auth):
    response = client.get(
        f"{BASE}/annotations?run_id={RUN_ID}", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    )
    assert response.status_code == 400
    assert response.json()["field"] == "label_column"


def test_a_reader_without_asset_rights_cannot_read_the_chain(client, auth):
    response = client.get(f"{BASE}/annotations/{RUN_ID}", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER]))
    assert response.status_code == 403


def test_recording_and_reviewing_are_audited(client, auth):
    version_id = _record(client, auth).json()["version_id"]
    client.post(
        f"{BASE}/annotations/{version_id}/review",
        json={"decision": str(ReviewDecision.APPROVE), "rationale": "checked"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    recorded = client.get(
        f"{BASE}/audit?action=annotation:record", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    ).json()["items"]
    assert [row["resource_id"] for row in recorded] == [version_id]
    reviewed = client.get(
        f"{BASE}/audit?action=annotation:review:approve", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    ).json()["items"]
    assert reviewed[0]["details"]["rationale"] == "checked"
