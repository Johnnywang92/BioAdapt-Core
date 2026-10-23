"""End-to-end tests for Solo Mode (单人全流程模式).

Proves that a single user holding a solo_mode token can perform the entire
analytical and governance lifecycle without being blocked by Separation of Duties (SoD):
1. Stage-0 Thresholds confirmation (as PI)
2. Self-requesting and self-deciding approval gates
3. Issuing ad-hoc tool execution grants (as PI)
4. Signing off findings drafted by the same author (as Expert)
"""

from bioadapt_policies import Action, Role
from bioadapt_schemas import ApprovalKind, DEFAULT_THRESHOLDS
from conftest import PROJECT_ID, RUN_ID, node, plan

BASE = f"/v1/projects/{PROJECT_ID}"


def test_solo_mode_development_tokens_minted(client):
    """The development tokens endpoint exposes a solo operator token."""
    res = client.get("/v1/development/tokens")
    assert res.status_code == 200
    tokens = res.json()
    assert "solo" in tokens
    assert "analyst" in tokens
    assert "pi" in tokens
    assert "expert" in tokens


def test_solo_mode_complete_lifecycle(client, auth, seeded):
    """Solo mode operator can perform all restricted operations end-to-end."""
    solo_headers = auth(
        "user_solo_operator",
        [Role.PRINCIPAL_INVESTIGATOR, Role.ANALYST, Role.DOMAIN_EXPERT],
        solo_mode=True,
        amr=["pwd+mfa"],
    )

    # 1. PI Stage-0 Threshold confirmation
    cut_set = DEFAULT_THRESHOLDS.model_dump(mode="json")
    cut_set.pop("confirmed", None)
    cut_set["version"] = "v1.0-solo-confirmed"

    confirm_res = client.post(
        f"{BASE}/thresholds/confirm",
        json={"thresholds": cut_set, "rationale": "counted the candidate distribution of three past projects"},
        headers=solo_headers,
    )
    assert confirm_res.status_code == 200
    assert confirm_res.json()["thresholds"]["confirmed"] is True

    # 2. Self-request and self-approve escalation gate
    gate_req = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "escalation", "assigned_to": "user_solo_operator"},
        headers=solo_headers,
    )
    assert gate_req.status_code == 201
    gate_id = gate_req.json()["approval_id"]

    gate_decide = client.post(
        f"{BASE}/approvals/{gate_id}/decision",
        json={"approved": True, "reason": "solo self-approval for automated testing"},
        headers=solo_headers,
    )
    assert gate_decide.status_code == 200
    assert gate_decide.json()["decided_by"] == "user_solo_operator"
    assert gate_decide.json()["state"] == "approved"

    # 3. Author self-sign-off on drafted card (same author bypasses SoD)
    sign_res = client.post(
        f"{BASE}/cards/{seeded['card_id']}/sign_off",
        json={"sign_off": "full", "reason": "solo expert verified and signed off"},
        headers=solo_headers,
    )
    assert sign_res.status_code == 200
    assert sign_res.json()["sign_off"] == "full"


def test_solo_mode_adhoc_tool_grant_self_approval(client, auth, catalog):
    """Solo mode allows the submitter of an ad-hoc tool to also issue its execution grant."""
    from bioadapt_schemas.enums import ToolClass

    adhoc_node = node(
        "adhoc_score",
        tool_name="project_qc_probe",
        tool_version="0.1.0",
        tool_class=ToolClass.PROJECT_ADHOC,
        requires_approval=True,
    )
    gated = plan(plan_version="2", nodes=[node("cluster"), adhoc_node])
    catalog.set_scope(PROJECT_ID)
    catalog.record_plan(gated, created_by="user_solo_operator")
    catalog.set_run_plan(RUN_ID, plan_version="2", tool_versions={n.tool_name: n.tool_version for n in gated.nodes})

    solo_headers = auth(
        "user_solo_operator",
        [Role.PRINCIPAL_INVESTIGATOR, Role.ANALYST, Role.DOMAIN_EXPERT],
        solo_mode=True,
        amr=["pwd+mfa"],
    )

    # 1. Self-request adhoc tool gate
    gate_req = client.post(
        f"{BASE}/runs/{RUN_ID}/approvals",
        json={"kind": "adhoc_tool", "node_id": "adhoc_score", "assigned_to": "user_solo_operator"},
        headers=solo_headers,
    )
    assert gate_req.status_code == 201
    gate_id = gate_req.json()["approval_id"]

    # 2. Self-decide adhoc tool gate
    decide_res = client.post(
        f"{BASE}/approvals/{gate_id}/decision",
        json={"approved": True, "reason": "solo approved adhoc tool"},
        headers=solo_headers,
    )
    assert decide_res.status_code == 200

    # 3. Issue adhoc grant as solo operator (submitter == approver)
    grant_res = client.post(
        f"{BASE}/runs/{RUN_ID}/nodes/adhoc_score/adhoc-grants",
        json={"approval_id": gate_id},
        headers=solo_headers,
    )
    assert grant_res.status_code == 201, grant_res.json()
    assert grant_res.json()["approver_id"] == "user_solo_operator"
    assert grant_res.json()["live"] is True

    # 4. The project's tool catalog has to keep answering: the launch screen reads it, and hydrating the
    # live grant must not turn a self-approved row into an invalid approval (§9.6).
    tools_res = client.get(f"{BASE}/tools", headers=solo_headers)
    assert tools_res.status_code == 200, tools_res.json()
    rows = tools_res.json()["tools"]
    assert any(
        row["tool_name"] == "project_qc_probe" and row["tool_class"] == "project_adhoc" for row in rows
    )

