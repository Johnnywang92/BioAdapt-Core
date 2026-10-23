import pytest
from pydantic import ValidationError

from bioadapt_schemas.enums import LabelProvenance, ToolClass
from bioadapt_schemas.plan import (
    CONCLUSION_OUTPUTS,
    MANDATORY_STOP_CONDITIONS,
    AnalysisPlan,
    ConfoundingCheck,
    DataPartitions,
    DesignMatrix,
    LoopBudget,
    NodeResources,
    PlanNode,
    ReferenceBinding,
    RetryPolicy,
)


def resources(**overrides):
    fields = {"cpu": 4, "memory_gb": 16, "max_minutes": 120}
    fields.update(overrides)
    return NodeResources(**fields)


def node(node_id, *, inputs=(), outputs=(), deps=(), **overrides):
    fields = {
        "node_id": node_id,
        "tool_name": "scanpy_pipeline",
        "tool_version": "1.11.0",
        "tool_class": ToolClass.REGISTERED,
        "inputs": list(inputs),
        "outputs": list(outputs),
        "dependency_node_ids": list(deps),
        "resources": resources(),
        "privacy_policy": "local_only",
    }
    fields.update(overrides)
    return PlanNode(**fields)


def design(**overrides):
    fields = {
        "formula": "~ condition + batch",
        "group_variable": "condition",
        "min_n_per_group": 3,
        "confounding_checks": [
            ConfoundingCheck(name="batch_vs_condition", tool="design_check", status="PASS")
        ],
    }
    fields.update(overrides)
    return DesignMatrix(**fields)


def partitions(**overrides):
    fields = {
        "rule": "holdout_donors_v1",
        "discovery_specimens": ["spc_01", "spc_02", "spc_03"],
        "confirmation_specimens": ["spc_04"],
    }
    fields.update(overrides)
    return DataPartitions(**fields)


def make_plan(**overrides):
    fields = {
        "plan_id": "plan_001",
        "plan_version": "1",
        "project_id": "prj_001",
        "objective": "investigate_uncharacterized_cell_population",
        "design_matrix": design(),
        "data_partitions": partitions(),
        "qc_policy_id": "qc_policy_v1",
        "loop_budget": LoopBudget(max_compute_minutes=240, max_token_cost_usd=5.0),
        "root_inputs": ["asset_counts"],
        "nodes": [
            node("qc", outputs=["qc_dataset", "qc_report"]),
            node("cluster", inputs=["qc_dataset"], outputs=["clusters"], deps=["qc"]),
            node(
                "candidate_discovery",
                inputs=["clusters"],
                outputs=["candidate_sets"],
                deps=["cluster"],
                requires_approval=True,
            ),
        ],
        "stop_conditions": list(MANDATORY_STOP_CONDITIONS),
        "provenance_policy": "run_manifest_v1",
    }
    fields.update(overrides)
    return AnalysisPlan(**fields)


class TestNodePolicies:
    def test_resource_defaults_allow_cpu_only_work(self):
        assert resources().gpu == 0
        for kwargs in ({"cpu": 0}, {"memory_gb": 0}, {"max_minutes": 0}, {"gpu": -1}):
            with pytest.raises(Exception):
                resources(**kwargs)

    def test_retry_policy_defaults_to_a_single_attempt(self):
        policy = RetryPolicy()
        assert policy.max_attempts == 1 and policy.backoff_seconds == 30

    def test_ad_hoc_tools_require_explicit_approval(self):
        with pytest.raises(ValidationError, match="ad-hoc"):
            node("adhoc_cluster", tool_class=ToolClass.PROJECT_ADHOC)
        assert node(
            "adhoc_cluster",
            tool_class=ToolClass.PROJECT_ADHOC,
            requires_approval=True,
            adhoc_spec={"tool_name": "scanpy_pipeline"},
        )

    def test_an_ad_hoc_node_carries_the_submission_it_runs(self):
        """Section 9.6: the plan pins the bytes, so a node without them names nothing to run."""
        with pytest.raises(ValidationError, match="must carry the submission"):
            node("adhoc_cluster", tool_class=ToolClass.PROJECT_ADHOC, requires_approval=True)
        with pytest.raises(ValidationError, match="needs no submission"):
            node("registered_cluster", adhoc_spec={"tool_name": "scanpy_pipeline"})

    def test_conclusion_assets_require_approval(self):
        for output in sorted(CONCLUSION_OUTPUTS):
            with pytest.raises(Exception, match="conclusion assets"):
                node("make_conclusions", outputs=[output])
        assert node("make_conclusions", outputs=["qc_report"])

    def test_nodes_keep_lineage_constraints_and_quality_checks(self):
        n = node(
            "integrate",
            inputs=["qc_dataset"],
            outputs=["embedding"],
            input_asset_lineage_constraints=["asset_counts"],
            quality_checks=["no_batch_condition_confounding"],
        )
        assert n.input_asset_lineage_constraints == ["asset_counts"]


class TestDesignAndPartitions:
    def test_identifiability_check_runs_before_execution(self):
        with pytest.raises(Exception, match="confounding check"):
            design(confounding_checks=[])
        with pytest.raises(Exception, match="confounding check"):
            design(confounding_checks=[ConfoundingCheck(name="x", tool="t", status="NOT_RUN")])

    def test_a_failed_check_is_still_an_executed_check(self):
        matrix = design(
            confounding_checks=[ConfoundingCheck(name="x", tool="t", status="FAIL", detail="fully confounded")]
        )
        assert matrix.confounding_checks[0].status == "FAIL"

    def test_specimens_cannot_be_in_both_partitions(self):
        with pytest.raises(Exception, match="both discovery and confirmation"):
            partitions(confirmation_specimens=["spc_03"])

    def test_discovery_partition_cannot_be_empty(self):
        with pytest.raises(Exception, match="must not be empty"):
            partitions(discovery_specimens=[])

    def test_reference_binding_records_label_provenance(self):
        binding = ReferenceBinding(
            dataset_id="cellxgene_census", version="2026-05", label_provenance=LabelProvenance.CURATED_EXPERT
        )
        assert binding.label_provenance is LabelProvenance.CURATED_EXPERT


class TestPlanGraph:
    def test_valid_plan_orders_nodes_topologically(self):
        plan = make_plan()
        assert [n.node_id for n in plan.topological_order()] == ["qc", "cluster", "candidate_discovery"]

    def test_duplicate_node_ids_are_rejected(self):
        with pytest.raises(ValidationError, match="duplicate node_id"):
            make_plan(nodes=[node("qc", outputs=["a"]), node("qc", inputs=["a"], outputs=["b"])])

    def test_unknown_dependencies_are_rejected(self):
        with pytest.raises(ValidationError, match="unknown nodes"):
            make_plan(nodes=[node("qc", outputs=["a"], deps=["ghost"])])

    def test_fallback_nodes_must_exist_and_not_be_self(self):
        with pytest.raises(ValidationError, match="fallback is unknown"):
            make_plan(nodes=[node("qc", outputs=["a"], fallback_node_id="ghost")])
        with pytest.raises(ValidationError, match="own fallback"):
            make_plan(nodes=[node("qc", outputs=["a"], fallback_node_id="qc")])

    def test_dependency_cycles_are_rejected(self):
        with pytest.raises(ValidationError, match="cycle"):
            make_plan(
                nodes=[
                    node("a", inputs=["asset_counts"], outputs=["x"], deps=["b"]),
                    node("b", inputs=["x"], outputs=["y"], deps=["a"]),
                ]
            )

    def test_inputs_must_be_produced_upstream(self):
        with pytest.raises(ValidationError, match="root input provides"):
            make_plan(nodes=[node("qc", inputs=["asset_from_thin_air"], outputs=["qc_dataset"])])

    def test_mandatory_stop_conditions_cannot_be_dropped(self):
        for missing in MANDATORY_STOP_CONDITIONS:
            with pytest.raises(ValidationError, match="mandatory stop conditions"):
                make_plan(stop_conditions=[c for c in MANDATORY_STOP_CONDITIONS if c != missing])


class TestDesignPower:
    def test_donors_shared_across_partitions_are_reported(self):
        plan = make_plan(
            data_partitions=partitions(
                discovery_specimens=["spc_01", "spc_02"], confirmation_specimens=["spc_03"]
            )
        )
        donor_of = {"spc_01": "donor_a", "spc_02": "donor_b", "spc_03": "donor_a"}
        assert plan.shared_partition_donors(donor_of) == ["donor_a"]
        assert plan.shared_partition_donors({"spc_01": "donor_a", "spc_02": "donor_b", "spc_03": "donor_c"}) == []

    def test_group_power_is_counted_in_donors_not_cells(self):
        plan = make_plan(
            data_partitions=partitions(
                discovery_specimens=["spc_01", "spc_02", "spc_03", "spc_04"], confirmation_specimens=[]
            )
        )
        donor_of = {"spc_01": "donor_a", "spc_02": "donor_a", "spc_03": "donor_b", "spc_04": "donor_c"}
        group_of = {"spc_01": "case", "spc_02": "case", "spc_03": "control", "spc_04": "control"}
        assert plan.donors_per_group(donor_of, group_of) == {"case": 1, "control": 2}
        assert plan.underpowered_groups(donor_of, group_of) == {"case": 1, "control": 2}

    def test_unassigned_specimens_form_their_own_group(self):
        plan = make_plan(data_partitions=partitions(discovery_specimens=["spc_01"]))
        assert plan.donors_per_group({}, {}) == {"unassigned": 1}
