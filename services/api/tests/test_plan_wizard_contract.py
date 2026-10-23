"""The wizard's own path over HTTP: bytes → asset → plan built from GET /tools → gate → run (§9.4, §9.1).

``PlanWizard.tsx`` assembles its plan from what ``GET /v1/projects/{id}/tools`` reports, from the
project's own cut set and from the slots the data document carries - it never types a tool id or a
resource number of its own. This test runs that same assembly against the real engine, so a plan the
screen can build is proven to be a plan the platform can launch, approve, execute and land in
QC_REVIEW with candidates and card drafts behind it. The assembly below is deliberately a line-for-line
reading of the component: if the two drift, this test is where it shows up.
"""

import hashlib
import json
import sys
from pathlib import Path

from bioadapt_api.routes_data import CONFIRMABLE_CUTS
from bioadapt_policies.access import Role
from bioadapt_schemas.entities import sha256_of_json
from bioadapt_schemas.enums import RunState
from bioadapt_schemas.plan import MANDATORY_STOP_CONDITIONS
from bioadapt_tool_registry import ToolSpec
from conftest import ANALYST, PI, PROJECT_ID

ROOT = Path(__file__).resolve().parents[3]
for entry in (ROOT / "workflows" / "scrnaseq" / "tests",):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import dataset  # noqa: E402 - the same synthetic PBMC set the reference chain runs on
import workflow_factories as WF  # noqa: E402

BASE = f"/v1/projects/{PROJECT_ID}"


def counts_document() -> bytes:
    """The counts file a person would pick in the data wizard, as the platform writes that contract."""
    from bioadapt_scrnaseq.products import ExpressionProduct
    from bioadapt_scrnaseq.steps import SLOT_COUNTS

    matrix = dataset.synthetic_dataset().matrix
    document = ExpressionProduct(
        slot=SLOT_COUNTS, matrix=matrix, transform="counts", parameters={"staged_by": "project upload"}
    ).document()
    return json.dumps(document, sort_keys=True).encode()


def wizard_plan(body: dict, *, project: dict, tools: list[dict], conclusion_outputs: list[str]) -> dict:
    """What ``PlanWizard.tsx`` sends: nodes from the registry rows, roots from the named slot, cuts verbatim.

    ``included`` is the chain the screen offers (the follow-up steps are withheld there, with a reason),
    ``root_slot`` is read off the previewed document, and each node takes the registry's own floor for
    resources and the registry's own gating for conclusion outputs.
    """
    root_slot = "counts"
    chain = ["qc", "normalize", "feature_selection", "embedding", "neighbors", "clustering", "markers", "pseudobulk_de"]
    included = [row for row in tools if row["task"] in chain]
    assert len(included) == len(chain), [row["task"] for row in included]

    producers: dict[str, str] = {}
    for row in included:
        for slot in row["output_slots"]:
            if slot != root_slot:
                producers[slot] = row["task"]

    policy = WF.qc_policy().model_dump(mode="json")
    mito, ribosomal = list(dataset.MITO_GENES), list(dataset.RIBOSOMAL_GENES)
    step_parameters = {
        "qc": {
            "qc_policy": policy,
            "thresholds_version": project["thresholds"]["version"],
            "max_flagged_share": WF.MAX_FLAGGED_SHARE,
            "mito_genes": mito,
            "ribosomal_genes": ribosomal,
        },
        "normalize": {"target_sum": WF.TARGET_SUM},
        "feature_selection": {
            "n_top_features": WF.N_TOP_FEATURES,
            "min_mean": WF.MIN_MEAN,
            "max_mean": WF.MAX_MEAN,
            "min_variance": WF.MIN_VARIANCE,
            "seed": WF.SEEDS["highly_variable_features"],
        },
        "embedding": {"n_components": WF.N_COMPONENTS, "seed": WF.SEEDS["pca"]},
        "neighbors": {"k": WF.N_NEIGHBOURS, "seed": WF.SEEDS["neighbors"]},
        "clustering": {"resolution": WF.RESOLUTION, "seed": WF.SEEDS["cluster"]},
        "markers": {"top_n": WF.TOP_MARKERS},
        "pseudobulk_de": {
            "contrasts": [{"group_a": "case", "group_b": "control", "arms": "condition"}],
            "permutation_replicates": WF.PERMUTATION_REPLICATES,
            "seed": WF.SEEDS["pseudobulk_de"],
            "thresholds": project["thresholds"],
        },
    }

    nodes = []
    for row in included:
        task = row["task"]
        required = [entry["slot"] for entry in row["input_slots"] if entry["required"]]
        optional = [
            entry["slot"] for entry in row["input_slots"] if not entry["required"] and entry["slot"] in producers
        ]
        gated = row["requires_approval"] or any(slot in conclusion_outputs for slot in row["output_slots"])
        nodes.append(
            {
                "node_id": task,
                "tool_name": row["tool_name"],
                "tool_version": row["tool_version"],
                "tool_class": row["tool_class"],
                "inputs": [*required, *optional],
                "outputs": row["output_slots"],
                "parameters": step_parameters[task],
                "dependency_node_ids": sorted({producers[slot] for slot in [*required, *optional] if slot in producers}),
                "resources": row["min_resources"],
                "privacy_policy": row["privacy_policy"],
                "requires_approval": gated,
            }
        )

    return {
        "plan_id": body["plan_id"],
        "plan_version": "1",
        "project_id": PROJECT_ID,
        "objective": "resolve_an_uncharacterized_population",
        "design_matrix": {
            "formula": "~ group",
            "group_variable": "group",
            "min_n_per_group": 4,
            "confounding_checks": [{"name": "group_vs_donor", "tool": "design_check", "status": "PASS"}],
        },
        "data_partitions": {
            "rule": "holdout_donors_v1",
            "discovery_specimens": ["spc_donor_a", "spc_donor_b", "spc_donor_c", "spc_donor_d"],
            "confirmation_specimens": ["spc_donor_e", "spc_donor_f", "spc_donor_g", "spc_donor_h"],
        },
        "qc_policy_id": policy["policy_id"],
        "loop_budget": {"max_iterations": 1, "max_compute_minutes": 180, "max_token_cost_usd": 5.0},
        "root_inputs": [root_slot],
        "nodes": nodes,
        "stop_conditions": list(MANDATORY_STOP_CONDITIONS),
        "provenance_policy": "run_manifest_v1",
    }


def upload_counts(client, auth, state) -> str:
    """The counts file a person would pick in the data wizard, registered as the run's input."""
    payload = counts_document()
    digest = hashlib.sha256(payload).hexdigest()
    asset_id = f"as_{digest[:12]}"
    #: The fixture's quota is sized for fixture bytes; this is a real counts document.
    state.quotas.default = state.quotas.default.model_copy(update={"max_storage_bytes": len(payload) + 10_000})

    declared = {
        "asset_id": asset_id,
        "project_id": PROJECT_ID,
        "uri": "pending://signed-upload",
        "file_sha256": digest,
        "content_fingerprint": digest,
        "format": "json",
        "schema_version": "expression_v1",
        "size_bytes": len(payload),
        "privacy_tier": "restricted",
        "usage_scope": "this_project_only",
        "retention_class": "run_linked",
        "filtering_state": "none",
        # The screen stamps the declaration when a person submits it; the contract wants that time.
        "created_at": "2026-05-01T00:00:00+00:00",
    }
    uploaded = client.post(
        f"{BASE}/assets/direct-upload",
        json={"asset": declared, "content_text": payload.decode()},
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert uploaded.status_code == 201, uploaded.json()
    return asset_id


def test_a_plan_the_wizard_builds_from_the_catalog_runs_to_qc_review(client, auth, state, catalog):
    """§9.4, §9.1: the rows a person chooses from are the rows the engine hydrates, and they execute."""
    asset_id = upload_counts(client, auth, state)

    cataloged = client.get(f"{BASE}/tools", headers=auth(ANALYST, [Role.ANALYST]))
    assert cataloged.status_code == 200, cataloged.json()
    catalog_body = cataloged.json()
    project = client.get(BASE, headers=auth(ANALYST, [Role.ANALYST])).json()

    recorded = client.post(
        f"{BASE}/plans",
        json=wizard_plan(
            {"plan_id": "plan_wizard_001"},
            project=project,
            tools=catalog_body["tools"],
            conclusion_outputs=catalog_body["conclusion_outputs"],
        ),
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert recorded.status_code == 201, recorded.json()

    launched = client.post(
        f"{BASE}/runs",
        json={
            "plan_id": "plan_wizard_001",
            "plan_version": "1",
            "input_asset_ids": [asset_id],
            "tool_registry_snapshot": catalog_body["snapshot_id"],
            "priority": 5,
        },
        headers={**auth(PI, [Role.PRINCIPAL_INVESTIGATOR]), "Idempotency-Key": "idem-wizard-e2e"},
    )
    assert launched.status_code == 202, launched.json()
    run_id = launched.json()["run_id"]

    #: A run works only over a plan gate a second subject has decided (§9.1, §11.6).
    gate = client.post(
        f"{BASE}/runs/{run_id}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, [Role.ANALYST])
    )
    assert gate.status_code == 201, gate.json()
    decided = client.post(
        f"{BASE}/approvals/{gate.json()['approval_id']}/decision",
        json={"approved": True, "reason": "reviewed the plan the wizard assembled"},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert decided.status_code == 200, decided.json()

    executed = client.post(f"{BASE}/runs/{run_id}/execute", headers=auth(ANALYST, [Role.ANALYST]))
    assert executed.status_code == 200, executed.json()
    outcome = executed.json()
    assert outcome["completed"] is True, outcome
    assert outcome["state"] == str(RunState.QC_REVIEW)
    assert outcome["gated_nodes"] == []
    assert len(outcome["nodes_executed"]) == 8
    assert outcome["candidate_count"] > 0
    assert outcome["draft_count"] > 0
    assert {"qc_flags", "clusters", "markers", "de_table"} <= set(outcome["assets_produced"])

    cards = client.get(f"{BASE}/candidates?limit=50", headers=auth(ANALYST, [Role.ANALYST])).json()
    assert cards["items"], "the run produced candidates the review screens have nothing to show"


def test_the_wizard_never_sends_a_confirmed_flag_and_the_gate_stays_where_it_was(client, auth, catalog):
    """§1.5: the cut set the wizard hands to the DE node is the project's, flag and all.

    The screen copies ``project.thresholds`` verbatim, so under stage 0 the plan still records and runs -
    it is the conclusion exits that refuse - and the copy carries no caller-set ``confirmed``.
    """
    from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS

    catalog.set_scope(PROJECT_ID).update_project_thresholds(DEFAULT_THRESHOLDS)
    project = client.get(BASE, headers=auth(ANALYST, [Role.ANALYST])).json()
    assert project["thresholds"]["confirmed"] is False

    tools = client.get(f"{BASE}/tools", headers=auth(ANALYST, [Role.ANALYST])).json()
    body = wizard_plan(
        {"plan_id": "plan_wizard_stage0"}, project=project, tools=tools["tools"],
        conclusion_outputs=tools["conclusion_outputs"],
    )
    de_node = next(node for node in body["nodes"] if node["node_id"] == "pseudobulk_de")
    assert "confirmed" in de_node["parameters"]["thresholds"]
    assert de_node["parameters"]["thresholds"]["confirmed"] is False
    assert de_node["parameters"]["thresholds"]["version"] == DEFAULT_THRESHOLDS.version

    recorded = client.post(f"{BASE}/plans", json=body, headers=auth(ANALYST, [Role.ANALYST]))
    assert recorded.status_code == 201, recorded.json()
    listed = client.get(f"{BASE}/plans", headers=auth(ANALYST, [Role.ANALYST])).json()
    row = next(item for item in listed["items"] if item["plan_id"] == "plan_wizard_stage0")
    assert len(row["nodes"]) == 8, "the stage-0 plan is stored whole, node for node"


def test_every_cut_the_wizard_restates_is_one_the_confirmation_route_demands(client, auth):
    """The screen's field list and ``CONFIRMABLE_CUTS`` are the same 24 names, or the form cannot be sent.

    ``ThresholdConfirm.tsx`` builds its body from the project record rather than a list of its own, so
    this pins the contract side: a cut added to the schema is a cut the confirmation has to restate.
    """
    project = client.get(BASE, headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()
    stated = set(project["thresholds"]) - {"confirmed"}
    assert stated == set(CONFIRMABLE_CUTS)


def project_submission() -> dict:
    """The bytes the wizard's form produces for one step (§9.6).

    ``AdhocSpecForm.specOf`` builds exactly this shape: the lines the screen asks the owner of the
    code for, the cage settings the deployment fixes (no network, no GPU, no retry), and the four
    provenance fields the contract requires of every tool. The plan node carries them, because the
    plan is what pins them.
    """
    return {
        "tool_name": "project_qc_probe",
        "tool_version": "0.1.0",
        "task": "qc",
        "tool_class": "project_adhoc",
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
            "note": None,
        },
        "quality_checks": ["adhoc_result_is_marked_unregistered"],
        "provenance_fields": ["input_hashes", "parameters", "container_image_digest", "tool_version"],
        "assumptions": ["the registered QC step does not answer this project's question"],
        "invalid_when": ["the result is the only support for a delivered conclusion"],
        "reference_compatibility": {
            "requires_counts": True,
            "id_system": "hgnc_symbol",
            "needs_log": None,
            "embedding_source": None,
        },
        "failure_policy": "halt",
        "retry_limit": 0,
        "network": {"mode": "closed"},
        "reproducibility_level_target": "numeric",
    }


def own_tool_plan(body: dict, *, project: dict, tools: list[dict], conclusion_outputs: list[str]) -> dict:
    """The wizard's plan after the project put its own tool in one step's place (§9.6).

    ``PlanWizard.tsx`` keeps that step's slots and parameters and replaces everything that names the
    tool: the identity the outputs are recorded under, the resource floor the engine measures the
    node against, and the approval it carries. The submission's bytes ride along on the node.
    """
    plan_body = wizard_plan(
        body, project=project, tools=tools, conclusion_outputs=conclusion_outputs
    )
    submission = project_submission()
    measured = submission["resources"]["measured_peak_memory_mb"]
    for node in plan_body["nodes"]:
        if node["node_id"] != "qc":
            continue
        node.update(
            {
                "tool_name": submission["tool_name"],
                "tool_version": submission["tool_version"],
                "tool_class": "project_adhoc",
                "requires_approval": True,
                "resources": {
                    "cpu": submission["resources"]["cpu"],
                    "memory_gb": max(1, -(-measured // 1024)),
                    "gpu": 0,
                    "max_minutes": submission["resources"]["max_minutes"],
                },
                "adhoc_spec": submission,
            }
        )
    return plan_body


def open_gate(client, auth, run_id: str, kind: str, node_id: str, *, reason: str) -> str:
    """One gate, asked for by the analyst and decided by the PI (§9.4, §11.6)."""
    gate = client.post(
        f"{BASE}/runs/{run_id}/approvals",
        json={"kind": kind, "node_id": node_id},
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert gate.status_code == 201, gate.text
    approval_id = gate.json()["approval_id"]
    decided = client.post(
        f"{BASE}/approvals/{approval_id}/decision",
        json={"approved": True, "reason": reason},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert decided.status_code == 200, decided.text
    return approval_id


def resume(client, auth, run_id: str, reason: str, attempt: int) -> None:
    """Send a waiting run back onto the chain: the state machine's act, not the driver's (§9.2).

    The attempt numbers the send-back: transitions are deduplicated by attempt, so the second time
    a run is sent back from the same branch has to say it is the second time.
    """
    moved = client.post(
        f"{BASE}/runs/{run_id}/transitions",
        json={"to_state": "ANALYSIS_RUNNING", "reason": reason, "attempt": attempt},
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert moved.status_code == 201, moved.text


def test_the_projects_own_tool_runs_only_through_two_gates_and_the_grant(client, auth, state, catalog):
    """§9.6: the wizard's own tool is gated twice, and the grant executes exactly the plan's bytes.

    The chain the workbench offers is the one this walks: the plan pins the submission, the run page
    opens the node gate and the ad-hoc gate together, the PI decides both and issues the grant, and
    only then does the step run - under the name the submission carries.
    """
    asset_id = upload_counts(client, auth, state)
    catalog_body = client.get(f"{BASE}/tools", headers=auth(ANALYST, [Role.ANALYST])).json()
    project = client.get(BASE, headers=auth(ANALYST, [Role.ANALYST])).json()

    recorded = client.post(
        f"{BASE}/plans",
        json=own_tool_plan(
            {"plan_id": "plan_wizard_own_tool"},
            project=project,
            tools=catalog_body["tools"],
            conclusion_outputs=catalog_body["conclusion_outputs"],
        ),
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert recorded.status_code == 201, recorded.json()

    #: The grant form reads the bytes off the plan node, so the listing has to carry them: what a PI
    #: is asked to sign is the submission, not a summary of it. The plan stores them as the tool
    #: contract read them, defaults and all, so what a grant executes is exactly what was pinned.
    listed = client.get(f"{BASE}/plans", headers=auth(ANALYST, [Role.ANALYST])).json()
    row = next(item for item in listed["items"] if item["plan_id"] == "plan_wizard_own_tool")
    planned = next(node for node in row["nodes"] if node["node_id"] == "qc")
    assert planned["adhoc_spec"] == ToolSpec(**project_submission()).model_dump(mode="json")
    assert planned["tool_class"] == "project_adhoc" and planned["requires_approval"] is True

    launched = client.post(
        f"{BASE}/runs",
        json={
            "plan_id": "plan_wizard_own_tool",
            "plan_version": "1",
            "input_asset_ids": [asset_id],
            "tool_registry_snapshot": catalog_body["snapshot_id"],
            "priority": 5,
        },
        headers={**auth(PI, [Role.PRINCIPAL_INVESTIGATOR]), "Idempotency-Key": "idem-wizard-own-tool"},
    )
    assert launched.status_code == 202, launched.json()
    run_id = launched.json()["run_id"]

    plan_gate = client.post(
        f"{BASE}/runs/{run_id}/approvals", json={"kind": "plan"}, headers=auth(ANALYST, [Role.ANALYST])
    )
    assert plan_gate.status_code == 201, plan_gate.text
    decided = client.post(
        f"{BASE}/approvals/{plan_gate.json()['approval_id']}/decision",
        json={"approved": True, "reason": "reviewed the plan the wizard assembled"},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert decided.status_code == 200, decided.json()

    #: The step waits: the engine reads the node's own approval, and there is none yet.
    waiting = client.post(f"{BASE}/runs/{run_id}/execute", headers=auth(ANALYST, [Role.ANALYST]))
    assert waiting.status_code == 200, waiting.text
    assert waiting.json()["gated_nodes"] == ["qc"]
    assert waiting.json()["completed"] is False
    assert waiting.json()["state"] == str(RunState.NEEDS_INPUT)

    #: The run page's one click: both gates of that step, asked for by the analyst.
    node_gate = open_gate(client, auth, run_id, "node", "qc", reason="the step may run in this project")
    adhoc_gate = open_gate(
        client, auth, run_id, "adhoc_tool", "qc", reason="one submission, caged, results stay unregistered"
    )
    assert node_gate != adhoc_gate

    #: The run is waiting in a branch, and a branch is left by the state machine, not by the driver.
    resume(client, auth, run_id, "both gates of the step are decided", attempt=1)

    #: Two gates are not a grant: the ledger still holds no row, so the step still may not execute.
    granted_yet = client.post(f"{BASE}/runs/{run_id}/execute", headers=auth(ANALYST, [Role.ANALYST]))
    assert granted_yet.status_code == 200, granted_yet.text
    assert granted_yet.json()["completed"] is False
    assert granted_yet.json()["state"] == str(RunState.NEEDS_INPUT)
    assert "waits on the execution grant" in granted_yet.json()["reason"]

    issued = client.post(
        f"{BASE}/runs/{run_id}/nodes/qc/adhoc-grants",
        json={"approval_id": adhoc_gate},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR], amr=["totp"]),
    )
    assert issued.status_code == 201, issued.text
    grant = issued.json()
    #: The grant carries the plan's bytes and cites their digest: what runs is what was signed.
    assert grant["spec"] == ToolSpec(**project_submission()).model_dump(mode="json")
    assert grant["spec_sha256"] == sha256_of_json(grant["spec"])
    assert grant["approver_id"] == PI and grant["requested_by"] == ANALYST

    #: The run left the chain again to wait for the grant; sending it back is the state machine's act.
    resume(client, auth, run_id, "the grant is on the record", attempt=2)

    executed = client.post(f"{BASE}/runs/{run_id}/execute", headers=auth(ANALYST, [Role.ANALYST]))
    assert executed.status_code == 200, executed.text
    outcome = executed.json()
    assert outcome["completed"] is True, outcome
    assert outcome["state"] == str(RunState.QC_REVIEW)
    assert outcome["gated_nodes"] == []
    assert len(outcome["nodes_executed"]) == 8

    #: The step's output is recorded under the submission's name: that is what the approval bought.
    checkpoints = catalog.set_scope(PROJECT_ID).list_checkpoints(run_id)
    qc_checkpoint = next(one for one in checkpoints if one.node_id == "qc")
    assert qc_checkpoint.tool_name == "project_qc_probe"
    assert qc_checkpoint.tool_version == "0.1.0"

    #: And the grant is spent by that one execution: a second run cannot buy another one with it.
    spent = client.get(
        f"{BASE}/runs/{run_id}/adhoc-grants", headers=auth(ANALYST, [Role.ANALYST])
    ).json()
    assert spent["items"][0]["live"] is False

