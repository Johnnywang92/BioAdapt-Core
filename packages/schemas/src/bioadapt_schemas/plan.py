"""Analysis plan contract (plan V2.0 section 5.3)."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field, model_validator

from .entities import Contract, ImmutableContract
from .enums import LabelProvenance, ToolClass

MANDATORY_STOP_CONDITIONS = (
    "input_schema_invalid",
    "critical_quality_failure",
    "unresolved_design_confounding",
)

CONCLUSION_OUTPUTS = frozenset({"candidate_sets", "evidence_cards", "de_table_support", "stability_summary"})


class PlanValidationError(ValueError):
    pass


class NodeResources(ImmutableContract):
    cpu: int = Field(ge=1)
    memory_gb: int = Field(ge=1)
    gpu: int = Field(ge=0, default=0)
    max_minutes: int = Field(ge=1)


class RetryPolicy(ImmutableContract):
    max_attempts: int = Field(ge=1, default=1)
    backoff_seconds: int = Field(ge=0, default=30)


class ConfoundingCheck(ImmutableContract):
    name: str
    tool: str
    status: Literal["PASS", "FAIL", "NOT_RUN"]
    detail: Optional[str] = None


class DesignMatrix(Contract):
    formula: str
    group_variable: str
    paired_by: Optional[str] = None
    covariates: list[str] = Field(default_factory=list)
    min_n_per_group: int = Field(ge=1)
    confounding_checks: list[ConfoundingCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_identifiability_check(self) -> "DesignMatrix":
        ran = [c for c in self.confounding_checks if c.status in ("PASS", "FAIL")]
        if not ran:
            raise ValueError("design matrix requires a confounding check before execution")
        return self


class DataPartitions(Contract):
    rule: str
    discovery_specimens: list[str]
    confirmation_specimens: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _specimens_disjoint(self) -> "DataPartitions":
        overlap = sorted(set(self.discovery_specimens) & set(self.confirmation_specimens))
        if overlap:
            raise ValueError(f"specimens cannot be in both discovery and confirmation: {overlap}")
        if not self.discovery_specimens:
            raise ValueError("discovery partition must not be empty")
        return self


class LoopBudget(ImmutableContract):
    max_iterations: int = Field(ge=0, default=1)
    max_compute_minutes: int = Field(ge=1)
    max_token_cost_usd: float = Field(ge=0)


class ReferenceBinding(ImmutableContract):
    dataset_id: str
    version: str
    label_provenance: LabelProvenance


class PlanNode(Contract):
    node_id: str
    tool_name: str
    tool_version: str
    tool_class: ToolClass
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    dependency_node_ids: list[str] = Field(default_factory=list)
    resources: NodeResources
    privacy_policy: str
    requires_approval: bool = False
    quality_checks: list[str] = Field(default_factory=list)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    fallback_node_id: Optional[str] = None
    input_asset_lineage_constraints: list[str] = Field(default_factory=list)
    #: Section 9.6: the bytes of an ad-hoc submission. A node whose tool the registry does not hold
    #: carries its submission here, so the plan is what pins those bytes and the grant is what executes
    #: them - one source, so what was approved and what runs cannot drift apart.
    #: The type is a plain mapping because the contract package cannot import the tool registry (the
    #: dependency runs the other way); the API validates these bytes against ToolSpec before a plan is
    #: recorded, and the grant route reads them back from here.
    adhoc_spec: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def _adhoc_needs_approval(self) -> "PlanNode":
        if self.tool_class is ToolClass.PROJECT_ADHOC and not self.requires_approval:
            raise ValueError("ad-hoc tool nodes require explicit approval")
        if set(self.outputs) & CONCLUSION_OUTPUTS and not self.requires_approval:
            raise ValueError(f"nodes producing conclusion assets must require approval: {self.node_id}")
        if self.tool_class is ToolClass.PROJECT_ADHOC and self.adhoc_spec is None:
            raise ValueError(
                f"node {self.node_id} runs an ad-hoc tool, so it must carry the submission it runs"
            )
        if self.tool_class is not ToolClass.PROJECT_ADHOC and self.adhoc_spec is not None:
            raise ValueError(
                f"node {self.node_id} runs a registered tool, which needs no submission: {self.tool_name}"
            )
        return self


class AnalysisPlan(Contract):
    plan_id: str
    plan_version: str
    project_id: str
    objective: str
    design_matrix: DesignMatrix
    data_partitions: DataPartitions
    qc_policy_id: str
    reference_binding: Optional[ReferenceBinding] = None
    loop_budget: LoopBudget
    root_inputs: list[str] = Field(default_factory=list)
    nodes: list[PlanNode]
    stop_conditions: list[str]
    expected_deliverables: list[str] = Field(default_factory=list)
    provenance_policy: str

    @model_validator(mode="after")
    def _validate_graph(self) -> "AnalysisPlan":
        ids = [n.node_id for n in self.nodes]
        if len(set(ids)) != len(ids):
            raise PlanValidationError("duplicate node_id")
        known = set(ids)
        for node in self.nodes:
            unknown = sorted(set(node.dependency_node_ids) - known)
            if unknown:
                raise PlanValidationError(f"node {node.node_id} depends on unknown nodes: {unknown}")
            if node.fallback_node_id and node.fallback_node_id not in known:
                raise PlanValidationError(f"node {node.node_id} fallback is unknown")
            if node.fallback_node_id == node.node_id:
                raise PlanValidationError(f"node {node.node_id} cannot be its own fallback")
        produced: set[str] = set(self.root_inputs)
        for node in self.topological_order():
            for input_name in node.inputs:
                if input_name not in produced:
                    raise PlanValidationError(
                        f"node {node.node_id} consumes {input_name!r} which no upstream node or root input provides"
                    )
            produced.update(node.outputs)
        return self

    @model_validator(mode="after")
    def _validate_stop_conditions(self) -> "AnalysisPlan":
        missing = [c for c in MANDATORY_STOP_CONDITIONS if c not in self.stop_conditions]
        if missing:
            raise PlanValidationError(f"mandatory stop conditions missing: {missing}")
        return self

    def topological_order(self) -> list["PlanNode"]:
        by_id = {n.node_id: n for n in self.nodes}
        visited: set[str] = set()
        visiting: list[str] = []
        ordered: list[PlanNode] = []

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                raise PlanValidationError(f"dependency cycle: {' -> '.join([*visiting, node_id])}")
            visiting.append(node_id)
            for dep in by_id[node_id].dependency_node_ids:
                visit(dep)
            visiting.pop()
            visited.add(node_id)
            ordered.append(by_id[node_id])

        for node_id in by_id:
            visit(node_id)
        return ordered

    def shared_partition_donors(self, donor_of_specimen: dict[str, str]) -> list[str]:
        """Donors present in both discovery and confirmation partitions (violates the plan rule)."""
        discovery = {donor_of_specimen.get(s, s) for s in self.data_partitions.discovery_specimens}
        confirmation = {donor_of_specimen.get(s, s) for s in self.data_partitions.confirmation_specimens}
        return sorted(discovery & confirmation)

    def donors_per_group(self, donor_of_specimen: dict[str, str], group_of_specimen: dict[str, str]) -> dict[str, int]:
        groups: dict[str, set[str]] = {}
        for specimen in self.data_partitions.discovery_specimens:
            group = group_of_specimen.get(specimen, "unassigned")
            groups.setdefault(group, set()).add(donor_of_specimen.get(specimen, specimen))
        return {group: len(donors) for group, donors in sorted(groups.items())}

    def underpowered_groups(
        self, donor_of_specimen: dict[str, str], group_of_specimen: dict[str, str]
    ) -> dict[str, int]:
        return {
            group: count
            for group, count in self.donors_per_group(donor_of_specimen, group_of_specimen).items()
            if count < self.design_matrix.min_n_per_group
        }
