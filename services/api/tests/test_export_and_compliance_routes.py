"""Async exports and the compliance read side (sections 9.3, 9.7, 11.2, 11.4, 11.5, 13.1)."""

import re
from datetime import timedelta

from bioadapt_policies.access import MAX_PRESIGNED_TTL_SECONDS, Role
from bioadapt_policies.egress import FieldCategory
from bioadapt_schemas.enums import PrivacyTier, RetentionClass
from bioadapt_schemas.errors import ErrorClass
from bioadapt_storage.traces import TraceRecord
from bioadapt_api.routes_data import object_key_for
from conftest import (
    ANALYST,
    CLOUD_ENDPOINT,
    COMPLIANCE,
    EXPERT,
    HGR_PROJECT_ID,
    LOCAL_ENDPOINT,
    LOCAL_PROJECT_ID,
    NOW,
    PI,
    PROJECT_ID,
    PUBLIC_PROJECT_ID,
    RUN_ID,
    SERVICE,
    asset,
)

BASE = f"/v1/projects/{PROJECT_ID}"
DECISION_ID = re.compile(r"decision ([0-9a-f]{64})")

#: Section 5.9: the egress floor comes from the project row, so a test that wants to exercise another
#: tier has to open a project that is actually filed at it.
TIERS = {
    PROJECT_ID: PrivacyTier.RESTRICTED,
    PUBLIC_PROJECT_ID: PrivacyTier.PUBLIC,
    LOCAL_PROJECT_ID: PrivacyTier.STRICT_LOCAL,
    HGR_PROJECT_ID: PrivacyTier.HGR_CONTROLLED,
}


def _queue(client, auth, key: str, **body) -> str:
    response = client.post(
        f"{BASE}/exports", json=body, headers={**auth(PI, [Role.PRINCIPAL_INVESTIGATOR]), "Idempotency-Key": key}
    )
    assert response.status_code == 202, response.json()
    return response.json()["export_id"]


def _egress(
    client, auth, *, purpose, project_id=PROJECT_ID, tier=None, destination=LOCAL_ENDPOINT, fields=None, **extra
):
    body = {
        "project_id": project_id,
        "privacy_tier": str(TIERS[project_id] if tier is None else tier),
        "purpose": purpose,
        "destination": destination,
        "fields": (
            fields
            if fields is not None
            else [{"path": "task.goal", "category": str(FieldCategory.TASK_SEMANTICS)}]
        ),
    }
    body.update(extra)
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR], project_ids=[project_id])
    return client.post(f"/v1/projects/{project_id}/egress/requests", json=body, headers=headers)


def _approval(client, auth, purpose, **overrides) -> str:
    body = {
        "requester_id": PI,
        "purpose": purpose,
        "approved_on": str(NOW.date()),
        "expires_on": str((NOW + timedelta(days=30)).date()),
    }
    body.update(overrides)
    response = client.post(
        f"{BASE}/egress/approvals",
        json=body,
        headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER], amr=["totp"]),
    )
    assert response.status_code == 201, response.json()
    return response.json()["approval_id"]


def _aggregate(path: str) -> dict:
    return {"path": path, "category": str(FieldCategory.AGGREGATE_STATISTICS), "unit": "group"}


def test_an_export_is_a_job_not_a_list_request(client, auth):
    response = client.post(
        f"{BASE}/exports",
        json={"kind": "tables", "filters": {"run_id": RUN_ID}},
        headers={**auth(PI, [Role.PRINCIPAL_INVESTIGATOR]), "Idempotency-Key": "exp-key-1"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["poll"] == f"{BASE}/exports/{body['export_id']}"


def test_replaying_the_idempotency_key_returns_the_same_job(client, auth):
    headers = {**auth(PI, [Role.PRINCIPAL_INVESTIGATOR]), "Idempotency-Key": "exp-key-2"}
    first = client.post(f"{BASE}/exports", json={"kind": "figures"}, headers=headers)
    second = client.post(f"{BASE}/exports", json={"kind": "figures"}, headers=headers)
    assert second.status_code == 200
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.json()["export_id"] == first.json()["export_id"]


def test_the_same_key_with_a_different_export_is_a_conflict(client, auth):
    headers = {**auth(PI, [Role.PRINCIPAL_INVESTIGATOR]), "Idempotency-Key": "exp-key-3"}
    client.post(f"{BASE}/exports", json={"kind": "tables"}, headers=headers)
    response = client.post(f"{BASE}/exports", json={"kind": "delivery_zip"}, headers=headers)
    assert response.status_code == 409
    assert "different export request" in response.json()["message"]


def test_an_export_without_an_idempotency_key_is_refused(client, auth):
    response = client.post(f"{BASE}/exports", json={"kind": "tables"}, headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert response.status_code == 400
    assert response.json()["field"] == "Idempotency-Key"


def test_an_export_of_a_run_that_does_not_exist_is_not_queued(client, auth):
    response = client.post(
        f"{BASE}/exports",
        json={"kind": "tables", "filters": {"run_id": "run_missing"}},
        headers={**auth(PI, [Role.PRINCIPAL_INVESTIGATOR]), "Idempotency-Key": "exp-key-4"},
    )
    assert response.status_code == 404
    assert response.json()["class"] == str(ErrorClass.NOT_FOUND)


def test_a_strict_local_project_cannot_export_at_all(client, auth):
    response = client.post(
        f"/v1/projects/{LOCAL_PROJECT_ID}/exports",
        json={"kind": "tables"},
        headers={
            **auth(PI, [Role.PRINCIPAL_INVESTIGATOR], project_ids=[PROJECT_ID, LOCAL_PROJECT_ID]),
            "Idempotency-Key": "exp-key-5",
        },
    )
    assert response.status_code == 403
    assert "data plane" in response.json()["message"]


def test_a_role_without_export_rights_cannot_queue_one(client, auth):
    response = client.post(
        f"{BASE}/exports",
        json={"kind": "tables"},
        headers={**auth(ANALYST, [Role.ANALYST]), "Idempotency-Key": "exp-key-6"},
    )
    assert response.status_code == 403


def test_polling_a_running_export_offers_no_download(client, auth, seeded):
    export_id = _queue(client, auth, "exp-key-7", kind="tables", filters={"run_id": RUN_ID})
    body = client.get(f"{BASE}/exports/{export_id}", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()
    assert body["status"] == "QUEUED"
    assert "download_url" not in body


def test_a_ready_export_hands_out_a_short_lived_url_inside_its_prefix(client, auth, catalog, state):
    export_id = _queue(client, auth, "exp-key-8", kind="tables")
    artifact = asset(
        asset_id="as_export", retention_class=RetentionClass.DELIVERABLE, file_sha256="ef" * 32, size_bytes=5
    )
    catalog.set_scope(PROJECT_ID).record_asset(artifact)
    key = object_key_for(PROJECT_ID, artifact)
    state.objects.put(key, b"table")
    catalog.update_export(export_id, status="READY", artifact_asset_id="as_export")
    body = client.get(f"{BASE}/exports/{export_id}", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()
    assert body["status"] == "READY"
    assert body["ttl_seconds"] <= MAX_PRESIGNED_TTL_SECONDS
    assert body["download_url"].startswith(f"https://object.internal/{key}")


def test_a_ready_export_without_an_artifact_is_reported_not_fabricated(client, auth, catalog):
    export_id = _queue(client, auth, "exp-key-9", kind="tables")
    catalog.set_scope(PROJECT_ID).update_export(export_id, status="READY")
    response = client.get(f"{BASE}/exports/{export_id}", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert response.status_code == 400
    assert response.json()["field"] == "artifact_asset_id"


def test_exports_are_listed_and_filtered_by_status(client, auth, catalog):
    queued = {_queue(client, auth, "exp-key-10", kind="tables"), _queue(client, auth, "exp-key-11", kind="code_bundle")}
    running = _queue(client, auth, "exp-key-12", kind="figures")
    catalog.set_scope(PROJECT_ID).update_export(running, status="RUNNING")
    everything = client.get(f"{BASE}/exports", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()
    assert len(everything["items"]) == 3
    body = client.get(f"{BASE}/exports?status=QUEUED", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()
    assert {item["export_id"] for item in body["items"]} == queued
    assert all(item["status"] == "QUEUED" for item in body["items"])


def test_a_local_egress_of_allowed_fields_is_decided_and_stored(client, auth, catalog):
    response = _egress(
        client,
        auth,
        project_id=PUBLIC_PROJECT_ID,
        purpose="atlas comparison",
        fields=[{"path": "task.goal", "category": str(FieldCategory.TASK_SEMANTICS)}, _aggregate("stats.group.mean")],
    )
    assert response.status_code == 200
    decision = response.json()["egress"]
    assert decision["allowed"] is True
    assert decision["allowed_paths"] == ["task.goal", "stats.group.mean"]
    assert decision["requires_approval"] is False
    stored = catalog.set_scope(PUBLIC_PROJECT_ID).egress_decision(response.json()["decision_id"])
    assert stored["allowed"] is True
    assert stored["request"]["purpose"] == "atlas comparison"


def test_cell_level_content_is_denied_by_name_and_the_rest_of_the_request_stands(client, auth):
    response = _egress(
        client,
        auth,
        project_id=PUBLIC_PROJECT_ID,
        purpose="marker check",
        fields=[
            {"path": "task.goal", "category": str(FieldCategory.TASK_SEMANTICS)},
            {"path": "cells.barcode", "category": str(FieldCategory.CELL_LEVEL), "unit": "cell"},
            {"path": "stats.cell.count", "category": str(FieldCategory.AGGREGATE_STATISTICS), "unit": "cell"},
        ],
    )
    assert response.status_code == 200
    decision = response.json()["egress"]
    assert decision["allowed_paths"] == ["task.goal"]
    assert {d["path"] for d in decision["denials"]} == {"cells.barcode", "stats.cell.count"}
    assert any("never leaves the data plane" in d["rule"] for d in decision["denials"])
    assert any("cell-level statistics" in d["rule"] for d in decision["denials"])


def test_a_restricted_egress_needs_an_approval_that_covers_the_purpose(client, auth):
    purpose = "cross-atlas comparison"
    blocked = _egress(client, auth, purpose=purpose, fields=[_aggregate("stats.group.mean")])
    assert blocked.status_code == 403
    assert blocked.json()["class"] == str(ErrorClass.POLICY_BLOCKED)
    assert "field-scoped approval" in blocked.json()["message"]
    approval_id = _approval(client, auth, purpose)
    granted = _egress(client, auth, purpose=purpose, fields=[_aggregate("stats.group.mean")], approval_id=approval_id)
    assert granted.status_code == 200
    assert granted.json()["egress"]["requires_approval"] is True


def test_an_expired_approval_does_not_unlock_egress(client, auth):
    purpose = "expired purpose"
    approval_id = _approval(
        client,
        auth,
        purpose,
        approved_on=str((NOW - timedelta(days=60)).date()),
        expires_on=str((NOW - timedelta(days=30)).date()),
    )
    response = _egress(client, auth, purpose=purpose, fields=[_aggregate("stats.group.mean")], approval_id=approval_id)
    assert response.status_code == 403
    assert "field-scoped approval" in response.json()["message"]


def test_an_approval_for_a_different_purpose_does_not_unlock_egress(client, auth):
    approval_id = _approval(client, auth, "an unrelated purpose")
    response = _egress(client, auth, purpose="this purpose", fields=[_aggregate("stats.group.mean")], approval_id=approval_id)
    assert response.status_code == 403


def test_an_approval_window_is_checked_where_it_is_written(client, auth, catalog):
    """Section 11.2: an inverted window must not become somebody else's 400 further down the line."""
    response = client.post(
        f"{BASE}/egress/approvals",
        json={
            "requester_id": PI,
            "purpose": "a window that ends before it starts",
            "approved_on": str(NOW.date()),
            "expires_on": str((NOW - timedelta(days=1)).date()),
        },
        headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER], amr=["totp"]),
    )
    assert response.status_code == 400
    assert response.json()["field"] == "expires_on"
    #: The write that failed left nothing behind: a later request cannot cite an approval that was
    #: never granted, so it fails for the reason it really has - no approval - not for a broken row.
    blocked = _egress(client, auth, purpose="a window that ends before it starts", fields=[_aggregate("stats.group.mean")])
    assert blocked.status_code == 403
    assert "field-scoped approval" in blocked.json()["message"]


def test_a_decision_is_readable_by_its_id_whichever_way_it_went(client, auth):
    """Section 11.5: the verdict is an indexed fact, so a refusal can be quoted back by id."""
    allowed = _egress(
        client,
        auth,
        project_id=PUBLIC_PROJECT_ID,
        purpose="readable verdict",
        fields=[{"path": "task.goal", "category": str(FieldCategory.TASK_SEMANTICS)}, _aggregate("stats.group.mean")],
    )
    assert allowed.status_code == 200
    public = auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER], project_ids=[PUBLIC_PROJECT_ID])
    body = client.get(
        f"/v1/projects/{PUBLIC_PROJECT_ID}/egress/decisions/{allowed.json()['decision_id']}", headers=public
    ).json()
    assert body["allowed"] is True
    assert body["decision"]["allowed_paths"] == ["task.goal", "stats.group.mean"]
    assert body["request"]["purpose"] == "readable verdict"

    refused = _egress(client, auth, purpose="without an approval", fields=[_aggregate("stats.group.mean")])
    assert refused.status_code == 403
    denied_id = DECISION_ID.search(refused.json()["message"]).group(1)
    denied = client.get(f"{BASE}/egress/decisions/{denied_id}", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER])).json()
    assert denied["allowed"] is False
    assert denied["decision_id"] == denied_id
    assert any("field-scoped approval" in reason for reason in denied["decision"]["blocked_reasons"])


def test_a_decision_read_needs_the_audit_role_and_the_right_id(client, auth):
    granted = _egress(client, auth, project_id=PUBLIC_PROJECT_ID, purpose="scoped read")
    decision_id = granted.json()["decision_id"]
    scoped = f"/v1/projects/{PUBLIC_PROJECT_ID}/egress/decisions"
    missing = client.get(
        f"{scoped}/as_never_decided", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER], project_ids=[PUBLIC_PROJECT_ID])
    )
    assert missing.status_code == 404
    assert missing.json()["class"] == str(ErrorClass.NOT_FOUND)
    analyst = client.get(
        f"{scoped}/{decision_id}",
        headers=auth(ANALYST, [Role.ANALYST], project_ids=[PUBLIC_PROJECT_ID]),
    )
    assert analyst.status_code == 403
    assert "audit:read" in analyst.json()["message"]


def test_a_denied_egress_is_still_written_to_the_decision_and_audit_log(client, auth, catalog):
    response = _egress(
        client,
        auth,
        purpose="off allowlist",
        tier=PrivacyTier.RESTRICTED,
        destination="https://models.example.org",
        fields=[_aggregate("stats.group.mean")],
    )
    assert response.status_code == 403
    decision_id = DECISION_ID.search(response.json()["message"]).group(1)
    stored = catalog.set_scope(PROJECT_ID).egress_decision(decision_id)
    assert stored["allowed"] is False
    assert any("cloud allowlist" in reason for reason in stored["decision"]["blocked_reasons"])
    audit = client.get(
        f"{BASE}/audit?action=egress:decide", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER])
    ).json()["items"]
    assert [row["resource_id"] for row in audit] == [decision_id]


def test_a_local_only_tier_is_blocked_before_any_field_is_examined(client, auth):
    for project_id in (LOCAL_PROJECT_ID, HGR_PROJECT_ID):
        response = _egress(client, auth, project_id=project_id, purpose=f"{project_id} attempt")
        assert response.status_code == 403
        assert "keeps all content inside the data plane" in response.json()["message"]
        audit = f"/v1/projects/{project_id}/audit"
        headers = auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER], project_ids=[project_id])
        assert client.get(f"{audit}?action=egress:decide", headers=headers).json()["items"] == []
        denied = client.get(f"{audit}?action=egress:request", headers=headers).json()["items"]
        assert [row["details"]["allowed"] for row in denied] == [False]


def test_an_unknown_approval_id_is_not_found(client, auth):
    response = _egress(client, auth, purpose="unknown approval", approval_id="egr_missing")
    assert response.status_code == 404
    assert response.json()["class"] == str(ErrorClass.NOT_FOUND)


def test_cloud_egress_is_only_for_an_allowlisted_endpoint_with_terms(client, auth):
    allowed = _egress(client, auth, project_id=PUBLIC_PROJECT_ID, purpose="cloud annotation", destination=CLOUD_ENDPOINT)
    assert allowed.status_code == 200
    assert allowed.json()["egress"]["allowed"] is True
    denied = _egress(
        client,
        auth,
        project_id=PUBLIC_PROJECT_ID,
        purpose="cloud annotation off allowlist",
        destination="https://models.example.org",
    )
    assert denied.status_code == 403
    assert "cloud allowlist" in denied.json()["message"]


def test_an_egress_request_may_not_declare_a_weaker_tier_than_the_project_row(client, auth):
    response = _egress(client, auth, purpose="claim to be public", tier=PrivacyTier.PUBLIC)
    assert response.status_code == 400
    assert response.json()["field"] == "privacy_tier"
    assert "may not declare another" in response.json()["message"]
    headers = auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER])
    assert client.get(f"{BASE}/audit?action=egress:decide", headers=headers).json()["items"] == []


def test_an_egress_request_that_names_another_project_is_refused(client, auth):
    response = client.post(
        f"{BASE}/egress/requests",
        json={
            "project_id": LOCAL_PROJECT_ID,
            "privacy_tier": str(PrivacyTier.PUBLIC),
            "purpose": "wrong project",
            "destination": LOCAL_ENDPOINT,
            "fields": [],
        },
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert response.status_code == 400
    assert response.json()["field"] == "project_id"


def test_an_approval_is_a_separate_human_act_with_a_second_factor(client, auth):
    body = {
        "requester_id": PI,
        "purpose": "second factor missing",
        "approved_on": str(NOW.date()),
        "expires_on": str((NOW + timedelta(days=30)).date()),
    }
    no_mfa = client.post(f"{BASE}/egress/approvals", json=body, headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER]))
    assert no_mfa.status_code == 403
    assert "second factor" in no_mfa.json()["message"]
    same_subject = client.post(
        f"{BASE}/egress/approvals",
        json={**body, "requester_id": COMPLIANCE},
        headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER], amr=["totp"]),
    )
    assert same_subject.status_code == 403
    assert "different subjects" in same_subject.json()["message"]
    machine = client.post(
        f"{BASE}/egress/approvals",
        json=body,
        headers=auth(SERVICE, [Role.SERVICE_AGENT], service=True, amr=["totp"]),
    )
    assert machine.status_code == 403
    assert "human" in machine.json()["message"]


def test_analysts_cannot_read_the_audit_log(client, auth):
    response = client.get(f"{BASE}/audit", headers=auth(ANALYST, [Role.ANALYST]))
    assert response.status_code == 403


def test_the_audit_log_is_scoped_to_the_authorized_project(client, auth, catalog):
    catalog.set_scope(LOCAL_PROJECT_ID).audit(
        subject_id=PI, subject_kind="human", action="other:project", resource_id="elsewhere", details={}
    )
    rows = client.get(f"{BASE}/audit", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER])).json()["items"]
    assert all(row["project_id"] == PROJECT_ID for row in rows)
    assert "other:project" not in {row["action"] for row in rows}


def test_audit_rows_are_masked_before_they_leave_the_api(client, auth, catalog):
    catalog.set_scope(PROJECT_ID).audit(
        subject_id=PI,
        subject_kind="human",
        action="model:call",
        resource_id="llm_001",
        details={
            "prompt": "full conversation text",
            "barcode": "AAACGGGTTT",
            "donor_id": "donor_07",
            "api_key": "sk-live",
            "usage": {"input_tokens": 120, "output_tokens": 40},
        },
    )
    rows = client.get(
        f"{BASE}/audit?action=model:call", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER])
    ).json()["items"]
    assert rows[0]["details"] == {
        "prompt": "<trace-store:content-withheld>",
        "barcode": "***",
        "donor_id": "***",
        "api_key": "***",
        "usage": {"input_tokens": 120, "output_tokens": 40},
    }


def test_the_audit_log_pages_like_every_other_list(client, auth):
    client.get(f"{BASE}/candidates", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    body = client.get(
        f"{BASE}/audit?limit=1", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER])
    ).json()
    assert len(body["items"]) == 1
    assert body["next_offset"] == 1


def test_every_authorization_decision_is_audited_with_its_trace_id(client, auth):
    response = client.get(
        f"{BASE}/candidates",
        headers={**auth(PI, [Role.PRINCIPAL_INVESTIGATOR]), "X-Trace-Id": "tr_pinned_1"},
    )
    assert response.headers["X-Trace-Id"] == "tr_pinned_1"
    rows = client.get(
        f"{BASE}/audit?trace_id=tr_pinned_1", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER])
    ).json()["items"]
    assert [row["action"] for row in rows] == ["run:read"]
    assert rows[0]["details"]["allowed"] is True
    assert rows[0]["subject_id"] == PI


def test_a_denied_decision_is_audited_too(client, auth, seeded):
    client.post(
        f"{BASE}/cards/card_001/sign_off",
        json={"sign_off": "full"},
        headers=auth(SERVICE, [Role.SERVICE_AGENT], service=True),
    )
    rows = client.get(
        f"{BASE}/audit?action=card:sign_off", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER])
    ).json()["items"]
    assert rows[0]["details"]["allowed"] is False
    assert any("human" in denial for denial in rows[0]["details"]["denials"])


def test_prompt_content_is_readable_only_through_the_trace_endpoint(client, auth, state):
    state.traces.put(
        TraceRecord(
            trace_id="tr_llm_001",
            project_id=PROJECT_ID,
            run_id=RUN_ID,
            captured_at=NOW,
            kind="model_invocation",
            content={"prompt": "cell level summary", "response": "provisional label"},
        )
    )
    body = client.get(f"{BASE}/traces/tr_llm_001", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER])).json()
    assert body["content"]["prompt"] == "cell level summary"
    refused = client.get(f"{BASE}/traces/tr_llm_001", headers=auth(EXPERT, [Role.DOMAIN_EXPERT]))
    assert refused.status_code == 403


def test_a_trace_is_not_readable_across_projects(client, auth, state):
    state.traces.put(
        TraceRecord(
            trace_id="tr_local", project_id=LOCAL_PROJECT_ID, kind="model_invocation", content={"prompt": "x"}
        )
    )
    response = client.get(f"{BASE}/traces/tr_local", headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER]))
    assert response.status_code == 404


def test_the_access_log_line_carries_only_the_pinned_fields(client, auth, state):
    response = client.get(f"{BASE}/candidates", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    #: The sink is shared by every request in the test session, so the line this request owns is the one
    #: carrying the trace id it was answered with - not whatever happened to be appended last.
    lines = [event for event in state.log_sink.events if event["trace_id"] == response.headers["X-Trace-Id"]]
    assert len(lines) == 1
    line = lines[0]
    assert set(line) <= {
        "level",
        "timestamp",
        "trace_id",
        "project_id",
        "subject_id",
        "action",
        "resource_id",
        "status",
        "latency_ms",
    }
    assert line["status"] == "200"
    assert line["action"] == f"GET {BASE}/candidates"
