"""The ad-hoc grant route: the point where one PI decision becomes a permission (sections 9.6, 11.6, 13.1).

Everything these tests check is the same question asked from a different side: who is on the row when the
grant is issued, and can a caller put anyone there? The gate answers whether a person approved the node;
the route under test turns that approval into the one execution §9.6 says it buys, and it is the only
place in the platform where a ``tool_grant`` row is written.

The bytes of the submission live on the plan node the gate names, not in the request: the plan is what
pins them, so a grant carries the plan's bytes and a request cannot supply its own.
"""

import copy
from datetime import timedelta

from bioadapt_policies.access import Role
from bioadapt_schemas.entities import sha256_of_json
from bioadapt_schemas.enums import ApprovalKind, ApprovalState, ToolClass
from bioadapt_tool_registry import ToolSpec
from conftest import ANALYST, NOW, PI, PROJECT_ID, RUN_ID, node, plan

BASE = f"/v1/projects/{PROJECT_ID}"
GRANT_BASE = f"{BASE}/runs/{RUN_ID}/nodes/adhoc_score/adhoc-grants"
PI_ROLE = [Role.PRINCIPAL_INVESTIGATOR]
ANALYST_ROLE = [Role.ANALYST]
PI2 = "user_pi_02"
ADHOC_TOOL = "project_qc_probe"
ADHOC_VERSION = "0.1.0"

#: A submission built the way §9.6 describes one: caged, documented, and licence-clean. Tests break one
#: property at a time from here, so a refusal names the property rather than an unrelated gap.
def spec_body(**overrides) -> dict:
    fields = {
        "tool_name": ADHOC_TOOL,
        "tool_version": ADHOC_VERSION,
        "task": "qc",
        "tool_class": str(ToolClass.PROJECT_ADHOC),
        "supported_modalities": ["RNA"],
        "supported_species": ["human"],
        "input_schema": {"name": "scrnaseq_counts", "version": "v1"},
        "output_schema": {"name": "adhoc_output", "version": "v1"},
        "container_image": {
            "repository": "registry.internal/bioadapt/project-probe",
            "tag": "0.1.0",
            "digest": "sha256:" + "ab" * 32,
        },
        "resources": {
            "cpu": 4,
            "memory_mb": 8192,
            "max_minutes": 60,
            "measured_peak_memory_mb": 6100,
            "measured_on": "run_scrnaseq_calibration",
        },
        "license": {
            "spdx_id": "MIT",
            "display_name": "project probe",
            "review_status": "approved",
            "reviewed_at": "2026-04-01",
        },
        "quality_checks": ["adhoc_result_is_marked_unregistered"],
        "provenance_fields": ["input_hashes", "parameters", "container_image_digest", "tool_version"],
        "assumptions": ["submitted for this project only"],
        "invalid_when": ["the result is the only support for a delivered conclusion"],
        "reference_compatibility": {"requires_counts": True, "id_system": "ensembl"},
        "failure_policy": "halt",
        "retry_limit": 0,
        "network": {"mode": "closed"},
    }
    fields.update(overrides)
    return copy.deepcopy(fields)


def adhoc_node(node_id: str = "adhoc_score", **overrides):
    """A plan node for an ad-hoc tool, carrying the submission it runs (§9.6)."""
    fields = {
        "tool_name": ADHOC_TOOL,
        "tool_version": ADHOC_VERSION,
        "tool_class": ToolClass.PROJECT_ADHOC,
        "requires_approval": True,
        "adhoc_spec": spec_body(),
    }
    fields.update(overrides)
    return node(node_id, **fields)


def adhoc_plan(catalog, *, spec: dict | None = None) -> None:
    """Point the run at a plan whose ad-hoc nodes are the submissions under test (§9.4)."""
    submission = spec_body() if spec is None else spec
    gated = plan(
        plan_version="2",
        nodes=[
            node("cluster"),
            adhoc_node("adhoc_score", adhoc_spec=submission),
            adhoc_node("adhoc_probe", adhoc_spec=submission),
        ],
    )
    catalog.set_scope(PROJECT_ID)
    catalog.record_plan(gated, created_by=ANALYST)
    catalog.set_run_plan(RUN_ID, plan_version="2", tool_versions={n.tool_name: n.tool_version for n in gated.nodes})


def open_gate(client, auth, *, requested_by=ANALYST, decided_by=PI, node_id="adhoc_score", assigned_to=None) -> str:
    """The two human steps before a grant exists: an analyst asks, a PI answers (§9.4)."""
    body = {"kind": str(ApprovalKind.ADHOC_TOOL), "node_id": node_id}
    if assigned_to is not None:
        body["assigned_to"] = assigned_to
    gate = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json=body,
        headers=auth(requested_by, PI_ROLE if requested_by == PI else ANALYST_ROLE),
    )
    assert gate.status_code == 201, gate.text
    approval_id = gate.json()["approval_id"]
    decided = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": "sandboxed, one submission, results stay unregistered"},
        headers=auth(decided_by, PI_ROLE),
    )
    assert decided.status_code == 200, decided.text
    return approval_id


def grant(client, auth, approval_id, *, headers=None, node_id="adhoc_score", **body_fields) -> dict:
    """The request a grant is issued over: a gate id, and nothing about the tool."""
    body = {"approval_id": approval_id}
    body.update(body_fields)
    return client.post(
        f"{BASE}/runs/{RUN_ID}/nodes/{node_id}/adhoc-grants",
        json=body,
        headers=headers or auth(PI, PI_ROLE, amr=["totp"]),
    )


class TestIssuingAGrant:
    def test_the_pi_who_decided_the_gate_issues_it(self, client, auth, catalog):
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        made = grant(client, auth, approval_id)
        assert made.status_code == 201, made.text
        body = made.json()
        assert body["approver_id"] == PI and body["requested_by"] == ANALYST
        assert body["project_id"] == PROJECT_ID and body["approval_id"] == approval_id
        assert body["run_id"] == RUN_ID and body["node_id"] == "adhoc_score"
        assert body["plan_id"] == "plan_001" and body["plan_version"] == "2"
        assert body["approved_at"] == NOW.isoformat().replace("+00:00", "Z")
        assert body["live"] is True and body["spent_at"] is None
        #: The row stores the spec the server parsed, defaults and all, and cites the digest of those bytes:
        #: what the engine later runs is checked against this, not against whatever the caller sent.
        assert body["spec"] == ToolSpec(**spec_body()).model_dump(mode="json")
        assert body["spec_sha256"] == sha256_of_json(body["spec"])
        assert body["spec"]["retry_limit"] == 0
        assert body["reason"] == "sandboxed, one submission, results stay unregistered"

    def test_the_grant_and_the_decision_are_two_rows_in_the_audit_trail(self, client, auth, catalog):
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        grant_id = grant(client, auth, approval_id).json()["grant_id"]
        rows = catalog.audit_entries(action="tool:grant_adhoc", limit=20)
        #: Two rows, one decision: the authorisation the policy answered, and the fact the ledger recorded.
        assert {row["resource_id"] for row in rows} == {grant_id}
        issued = [row for row in rows if "spec_sha256" in row["details"]]
        assert len(issued) == 1
        details = issued[0]["details"]
        assert details["approval_id"] == approval_id
        assert details["requested_by"] == ANALYST
        assert details["tool"] == f"{ADHOC_TOOL}@{ADHOC_VERSION}"

    def test_the_window_is_the_deployments_and_a_request_may_only_shorten_it(self, client, auth, catalog, state):
        adhoc_plan(catalog)
        short = grant(client, auth, open_gate(client, auth), valid_for_minutes=60).json()
        assert short["expires_at"] == (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        #: A week is more than the deployment offers, and asking for it does not buy it: the ceiling is the
        #: one the engine reads the row against, not one each request sets for itself.
        stretched = grant(
            client, auth, open_gate(client, auth, node_id="adhoc_probe"), node_id="adhoc_probe",
            valid_for_minutes=60 * 24 * 7,
        ).json()
        assert stretched["expires_at"] == (NOW + state.adhoc_grant_ttl).isoformat().replace("+00:00", "Z")

    def test_a_grant_needs_a_gate_and_not_a_promise(self, client, auth, catalog):
        adhoc_plan(catalog)
        missing = grant(client, auth, "apr_never_opened")
        assert missing.status_code == 404
        assert missing.json()["code"] == "approval_not_found"

    def test_a_pending_gate_is_not_a_grant(self, client, auth, catalog):
        adhoc_plan(catalog)
        gate = client.post(
            f"{BASE}/runs/{RUN_ID}/approvals",
            json={"kind": str(ApprovalKind.ADHOC_TOOL), "node_id": "adhoc_score"},
            headers=auth(ANALYST, ANALYST_ROLE),
        )
        refused = grant(client, auth, gate.json()["approval_id"])
        assert refused.status_code == 409
        assert "a grant follows a decision" in refused.json()["message"]

    def test_a_nodes_gate_does_not_buy_a_tool(self, client, auth, catalog):
        adhoc_plan(catalog)
        gate = client.post(
            f"{BASE}/runs/{RUN_ID}/approvals",
            json={"kind": "node", "node_id": "adhoc_score"},
            headers=auth(ANALYST, ANALYST_ROLE),
        )
        approval_id = gate.json()["approval_id"]
        client.post(
            f"{BASE}/approvals/{approval_id}/decision",
            json={"approved": True, "reason": "cleared for this project"},
            headers=auth(PI, PI_ROLE),
        )
        wrong_kind = grant(client, auth, approval_id)
        assert wrong_kind.status_code == 400
        assert "an ad-hoc grant is issued only over the gate" in wrong_kind.json()["message"]

    def test_a_gate_cannot_be_answered_for_another_node(self, client, auth, catalog):
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        elsewhere = client.post(
            f"{BASE}/runs/{RUN_ID}/nodes/cluster/adhoc-grants",
            json={"approval_id": approval_id},
            headers=auth(PI, PI_ROLE, amr=["totp"]),
        )
        assert elsewhere.status_code == 400
        assert "is about run_project001_02/adhoc_score, not run_project001_02/cluster" in elsewhere.json()["message"]


class TestWhoMayGrant:
    def test_only_a_pi_holds_the_grant(self, client, auth, catalog):
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        refused = grant(client, auth, approval_id, headers=auth(ANALYST, ANALYST_ROLE, amr=["totp"]))
        assert refused.status_code == 403
        assert f"no role of ['analyst'] grants tool:grant_adhoc" in refused.json()["message"]

    def test_the_engine_may_not_grant_itself_a_tool(self, client, auth, catalog):
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        refused = grant(
            client, auth, approval_id, headers=auth("svc_orchestrator", [Role.SERVICE_AGENT], service=True)
        )
        assert refused.status_code == 403
        assert "must be performed by a human" in refused.json()["message"]

    def test_granting_a_tool_needs_a_second_factor(self, client, auth, catalog):
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        refused = grant(client, auth, approval_id, headers=auth(PI, PI_ROLE))
        assert refused.status_code == 403
        assert "second factor" in refused.json()["message"]

    def test_the_subject_that_submitted_the_tool_may_not_grant_it(self, client, auth, catalog):
        """Section 9.6's separation of duties: two PIs, because the analyst who submits is not one."""
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth, requested_by=PI, decided_by=PI2, assigned_to=PI2)
        refused = grant(client, auth, approval_id, headers=auth(PI, PI_ROLE, amr=["totp"]))
        assert refused.status_code == 403
        assert "may not grant it" in refused.json()["message"]

    def test_a_grant_is_issued_by_the_person_who_decided_and_by_no_one_else(self, client, auth, catalog):
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth, decided_by=PI)
        refused = grant(client, auth, approval_id, headers=auth(PI2, PI_ROLE, amr=["totp"]))
        assert refused.status_code == 403
        assert f"this grant must be issued by {PI}" in refused.json()["message"]

    def test_the_request_cannot_name_its_own_approver_or_window(self, client, auth, catalog):
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        for field in ("approver_id", "requested_by", "project_id", "expires_at", "spent_at"):
            forged = client.post(
                GRANT_BASE,
                json={"approval_id": approval_id, field: "user_pi_99"},
                headers=auth(PI, PI_ROLE, amr=["totp"]),
            )
            assert forged.status_code == 400, (field, forged.text)
            assert forged.json()["field"] == field

    def test_a_request_cannot_supply_its_own_bytes(self, client, auth, catalog):
        """The bytes live on the plan node: a body that carries its own copy is a rejected request."""
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        supplied = client.post(
            GRANT_BASE,
            json={"approval_id": approval_id, "spec": spec_body()},
            headers=auth(PI, PI_ROLE, amr=["totp"]),
        )
        assert supplied.status_code == 400
        assert supplied.json()["field"] == "spec"


class TestWhatMayBeGranted:
    """Section 9.6: the cage and the submission's own documentation, checked where the bytes enter.

    A plan is the boundary: the bytes are checked when a plan is recorded, and again when a grant reads
    them off the plan. The second check is not redundant - it is what stops a plan recorded outside this
    API, or recorded before the deployment's caps were tightened, from buying a grant.
    """

    def test_a_submission_outside_the_cage_is_refused_by_the_plan(self, client, auth):
        over_cpu = spec_body(resources={
            "cpu": 64, "memory_mb": 8192, "max_minutes": 60,
            "measured_peak_memory_mb": 6100, "measured_on": "run_scrnaseq_calibration",
        })
        refused = client.post(
            f"{BASE}/plans",
            json=plan(
                plan_version="2",
                nodes=[node("cluster"), adhoc_node("adhoc_score", adhoc_spec=over_cpu)],
            ).model_dump(mode="json"),
            headers=auth(ANALYST, ANALYST_ROLE),
        )
        assert refused.status_code == 400, refused.text
        assert "exceeds ad-hoc cap" in refused.json()["message"]

    def test_a_submission_outside_the_cage_is_refused_again_when_it_is_granted(self, client, auth, catalog):
        adhoc_plan(catalog, spec=spec_body(resources={
            "cpu": 64, "memory_mb": 8192, "max_minutes": 60,
            "measured_peak_memory_mb": 6100, "measured_on": "run_scrnaseq_calibration",
        }))
        approval_id = open_gate(client, auth)
        over_cpu = grant(client, auth, approval_id)
        assert over_cpu.status_code == 400
        assert "exceeds ad-hoc cap" in over_cpu.json()["message"]

    def test_a_submission_that_would_retry_is_refused_by_the_plan(self, client, auth):
        retrying = client.post(
            f"{BASE}/plans",
            json=plan(
                plan_version="2",
                nodes=[node("cluster"), adhoc_node("adhoc_score", adhoc_spec=spec_body(retry_limit=2))],
            ).model_dump(mode="json"),
            headers=auth(ANALYST, ANALYST_ROLE),
        )
        assert retrying.status_code == 400, retrying.text
        assert "bypass the one-time approval" in retrying.json()["message"]

    def test_a_submission_that_would_retry_is_refused_again_when_it_is_granted(self, client, auth, catalog):
        adhoc_plan(catalog, spec=spec_body(retry_limit=2))
        approval_id = open_gate(client, auth)
        retrying = grant(client, auth, approval_id)
        assert retrying.status_code == 400
        assert "bypass the one-time approval" in retrying.json()["message"]

    def test_an_undocumented_submission_is_refused_before_anyone_signs_it(self, client, auth):
        vague = client.post(
            f"{BASE}/plans",
            json=plan(
                plan_version="2",
                nodes=[node("cluster"), adhoc_node("adhoc_score", adhoc_spec=spec_body(assumptions=[]))],
            ).model_dump(mode="json"),
            headers=auth(ANALYST, ANALYST_ROLE),
        )
        assert vague.status_code == 400, vague.text
        assert "assumptions are not documented" in vague.json()["message"]

    def test_a_submission_for_a_step_this_workflow_does_not_run_is_refused(self, client, auth):
        """The engine dispatches on the task, so a task with no runner could never be granted (§9.6)."""
        unknown = client.post(
            f"{BASE}/plans",
            json=plan(
                plan_version="2",
                nodes=[node("cluster"), adhoc_node("adhoc_score", adhoc_spec=spec_body(task="not_a_step"))],
            ).model_dump(mode="json"),
            headers=auth(ANALYST, ANALYST_ROLE),
        )
        assert unknown.status_code == 400, unknown.text
        assert "has no implementation in this workflow" in unknown.json()["message"]

    def test_a_registered_node_is_not_an_ad_hoc_one(self, client, auth, catalog):
        """The gate is the wrong kind of ask: a registered tool is admitted, not granted (§9.6)."""
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth, node_id="cluster")
        registered = client.post(
            f"{BASE}/runs/{RUN_ID}/nodes/cluster/adhoc-grants",
            json={"approval_id": approval_id},
            headers=auth(PI, PI_ROLE, amr=["totp"]),
        )
        assert registered.status_code == 400
        assert "an ad-hoc grant covers only the plan's ad-hoc nodes" in registered.json()["message"]

    def test_the_submission_must_be_the_one_the_node_plans(self, client, auth, catalog):
        """A plan that names one tool and carries another cannot be granted (§9.6)."""
        adhoc_plan(catalog, spec=spec_body(tool_name="something_else"))
        approval_id = open_gate(client, auth)
        mismatched = grant(client, auth, approval_id)
        assert mismatched.status_code == 400
        assert (
            "the submission names something_else@0.1.0, but node adhoc_score runs project_qc_probe@0.1.0"
            in mismatched.json()["message"]
        )

    def test_a_run_that_does_not_exist_has_no_nodes_to_grant(self, client, auth, catalog):
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        nowhere = client.post(
            "/v1/projects/prj_002/runs/run_none/nodes/adhoc_score/adhoc-grants",
            json={"approval_id": approval_id},
            headers=auth(PI, PI_ROLE, amr=["totp"]),
        )
        assert nowhere.status_code == 404
        assert nowhere.json()["code"] == "run_not_found"


class TestOneGateOneGrant:
    def test_asking_twice_for_the_same_bytes_returns_the_row_that_stands(self, client, auth, catalog):
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        first = grant(client, auth, approval_id)
        again = grant(client, auth, approval_id)
        assert again.status_code == 200, again.text
        assert again.headers["Idempotency-Replayed"] == "true"
        assert again.json()["grant_id"] == first.json()["grant_id"]
        assert len(catalog.set_scope(PROJECT_ID).list_tool_grants(run_id=RUN_ID)) == 1

    def test_a_second_submission_needs_a_second_decision(self, client, auth, catalog):
        """The plan pins the bytes, so a second submission is a second plan - and a second decision."""
        adhoc_plan(catalog)
        approval_id = open_gate(client, auth)
        grant(client, auth, approval_id)
        revised = plan(
            plan_version="3",
            nodes=[
                node("cluster"),
                adhoc_node("adhoc_score", adhoc_spec=spec_body(resources={
                    "cpu": 8, "memory_mb": 8192, "max_minutes": 60,
                    "measured_peak_memory_mb": 6100, "measured_on": "run_scrnaseq_calibration",
                })),
            ],
        )
        catalog.set_scope(PROJECT_ID)
        catalog.record_plan(revised, created_by=ANALYST)
        catalog.set_run_plan(
            RUN_ID, plan_version="3", tool_versions={n.tool_name: n.tool_version for n in revised.nodes}
        )
        changed = grant(client, auth, approval_id)
        assert changed.status_code == 409
        assert "a second submission needs a second decision" in changed.json()["message"]

    def test_a_new_gate_buys_a_new_grant_for_the_same_tool(self, client, auth, catalog):
        """Two decisions are two permissions, even over one tool (§9.4: the gate addresses the grant)."""
        adhoc_plan(catalog)
        first = grant(client, auth, open_gate(client, auth))
        probe = open_gate(client, auth, node_id="adhoc_probe")
        second = grant(client, auth, probe, node_id="adhoc_probe")
        assert second.status_code == 201, second.text
        assert second.json()["grant_id"] != first.json()["grant_id"]
        assert second.json()["node_id"] == "adhoc_probe"


class TestReadingTheLedger:
    def test_the_runs_grants_are_listed_with_their_liveness(self, client, auth, catalog):
        adhoc_plan(catalog)
        made = grant(client, auth, open_gate(client, auth)).json()
        listed = client.get(
            f"{BASE}/runs/{RUN_ID}/adhoc-grants", headers=auth(ANALYST, ANALYST_ROLE)
        ).json()
        assert [row["grant_id"] for row in listed["items"]] == [made["grant_id"]]
        assert listed["items"][0]["live"] is True
        single = client.get(f"{BASE}/adhoc-grants/{made['grant_id']}", headers=auth(PI, PI_ROLE))
        assert single.json()["approval_id"] == made["approval_id"]

    def test_a_spent_grant_is_still_readable_and_no_longer_live(self, client, auth, catalog):
        adhoc_plan(catalog)
        made = grant(client, auth, open_gate(client, auth)).json()
        catalog.set_scope(PROJECT_ID).spend_tool_grant(made["grant_id"])
        assert client.get(f"{BASE}/adhoc-grants/{made['grant_id']}", headers=auth(PI, PI_ROLE)).json()["live"] is False
        live = client.get(
            f"{BASE}/runs/{RUN_ID}/adhoc-grants", params={"live_only": True}, headers=auth(PI, PI_ROLE)
        ).json()
        assert live["items"] == []

    def test_another_projects_ledger_does_not_hold_this_projects_grant(self, client, auth, catalog):
        adhoc_plan(catalog)
        made = grant(client, auth, open_gate(client, auth)).json()
        elsewhere = client.get(
            f"/v1/projects/prj_002/adhoc-grants/{made['grant_id']}",
            headers=auth(PI2, PI_ROLE, project_ids=["prj_002"]),
        )
        assert elsewhere.status_code == 404
        assert elsewhere.json()["code"] == "adhoc_grant_not_found"
