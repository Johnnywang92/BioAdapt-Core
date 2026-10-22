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
from bioadapt_schemas.enums import RunState
from bioadapt_schemas.plan import MANDATORY_STOP_CONDITIONS
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


def test_a_plan_the_wizard_builds_from_the_catalog_runs_to_qc_review(client, auth, state, catalog):
    """§9.4, §9.1: the rows a person chooses from are the rows the engine hydrates, and they execute."""
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
