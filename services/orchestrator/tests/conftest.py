"""Wiring for the orchestrator tests: a journal-backed state machine over the SQLite catalog."""

from datetime import UTC, datetime, timedelta

import pytest

from bioadapt_orchestrator.approvals import ApprovalPolicy
from bioadapt_orchestrator.service import ActorRef, Orchestrator
from bioadapt_schemas.entities import DataAsset, Project, RunManifest, sha256_of_json
from bioadapt_schemas.enums import (
    ApprovalKind,
    FilteringState,
    PrivacyTier,
    ReproducibilityLevel,
    RetentionClass,
    RunState,
    SubjectKind,
    ToolClass,
    UsageScope,
)
from bioadapt_schemas.plan import (
    MANDATORY_STOP_CONDITIONS,
    AnalysisPlan,
    ConfoundingCheck,
    DataPartitions,
    DesignMatrix,
    LoopBudget,
    NodeResources,
    PlanNode,
)
from bioadapt_storage.catalog import Catalog

NOW = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)  # a Friday, so the approval window crosses a weekend

PROJECT_ID = "prj_001"
RUN_ID = "run_project001_02"
NODE_ID = "cluster"
SHA = "ab" * 32

PI = "user_pi_01"
ANALYST = "user_analyst_01"
EXPERT = "user_expert_01"
DEPUTY = "user_pi_02"
SERVICE = "svc_pipeline"

HUMAN_PI = ActorRef(PI, SubjectKind.HUMAN)
HUMAN_ANALYST = ActorRef(ANALYST, SubjectKind.HUMAN)
HUMAN_EXPERT = ActorRef(EXPERT, SubjectKind.HUMAN)
HUMAN_DEPUTY = ActorRef(DEPUTY, SubjectKind.HUMAN)
AGENT = ActorRef(SERVICE, SubjectKind.SERVICE)


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


class Ceilings:
    def __init__(self, *, max_compute_minutes: int = 480, max_model_cost_usd: float = 10.0) -> None:
        self.max_compute_minutes = max_compute_minutes
        self.max_model_cost_usd = max_model_cost_usd


class Quotas:
    def __init__(self, ceilings: Ceilings | None = None) -> None:
        self.ceilings = ceilings or Ceilings()

    def for_project(self, project_id: str) -> Ceilings:
        return self.ceilings


def node(node_id: str = NODE_ID, **overrides) -> PlanNode:
    fields = {
        "node_id": node_id,
        "tool_name": "scanpy_pipeline",
        "tool_version": "1.11.0",
        "tool_class": ToolClass.REGISTERED,
        "inputs": ["asset_counts"],
        "outputs": ["clusters"],
        "resources": NodeResources(cpu=4, memory_gb=16, max_minutes=120),
        "privacy_policy": "local_only",
    }
    fields.update(overrides)
    return PlanNode(**fields)


def gated_node(**overrides) -> PlanNode:
    fields = {
        "node_id": "discover",
        "tool_name": "cluster_candidate_discovery",
        "inputs": ["clusters"],
        "outputs": ["candidate_sets"],
        "requires_approval": True,
    }
    fields.update(overrides)
    return node(**fields)


def plan(budget: LoopBudget | None = None, **overrides) -> AnalysisPlan:
    fields = {
        "plan_id": "plan_001",
        "plan_version": "1",
        "project_id": PROJECT_ID,
        "objective": "investigate_uncharacterized_cell_population",
        "design_matrix": DesignMatrix(
            formula="~ condition + batch",
            group_variable="condition",
            min_n_per_group=3,
            confounding_checks=[ConfoundingCheck(name="batch_vs_condition", tool="design_check", status="PASS")],
        ),
        "data_partitions": DataPartitions(
            rule="holdout_donors_v1", discovery_specimens=["spc_01", "spc_02"], confirmation_specimens=["spc_03"]
        ),
        "qc_policy_id": "qc_policy_v1",
        "loop_budget": budget or LoopBudget(max_iterations=2, max_compute_minutes=180, max_token_cost_usd=5.0),
        "root_inputs": ["asset_counts"],
        "nodes": [node()],
        "stop_conditions": list(MANDATORY_STOP_CONDITIONS),
        "provenance_policy": "run_manifest_v1",
    }
    fields.update(overrides)
    return AnalysisPlan(**fields)


def manifest(**overrides) -> RunManifest:
    fields = {
        "run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "plan_id": "plan_001",
        "plan_version": "1",
        "tool_versions": {"scanpy_pipeline": "1.11.0"},
        "tool_registry_snapshot": "snap_0001",
        "input_hashes": {"as_0001": SHA},
        "reproducibility_level_target": ReproducibilityLevel.BYTE,
        "started_at": NOW,
    }
    fields.update(overrides)
    return RunManifest(**fields)


def asset(asset_id: str = "as_0001") -> DataAsset:
    return DataAsset(
        asset_id=asset_id,
        project_id=PROJECT_ID,
        uri="s3://bioadapt/prj_001/count_matrix.h5ad",
        file_sha256=SHA,
        content_fingerprint="cd" * 32,
        format="h5ad",
        schema_version="1",
        size_bytes=1024,
        privacy_tier=PrivacyTier.RESTRICTED,
        usage_scope=UsageScope.THIS_PROJECT_ONLY,
        retention_class=RetentionClass.EPHEMERAL,
        filtering_state=FilteringState.NONE,
        created_at=NOW,
    )


def plan_digest(the_plan: AnalysisPlan) -> str:
    return sha256_of_json(the_plan.model_dump(mode="json"))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def ceilings() -> Ceilings:
    return Ceilings()


@pytest.fixture
def catalog(clock) -> Catalog:
    store = Catalog(":memory:", clock=clock, check_same_thread=False)
    store.create_project(
        Project(
            project_id=PROJECT_ID,
            study_goal="find rare immune states",
            species="human",
            privacy_tier=PrivacyTier.RESTRICTED,
            created_by=PI,
            annotation_granularity_target="cell_type_fine",
            review_load_budget_hours_per_week=4.0,
            created_at=NOW,
        )
    )
    store.set_scope(PROJECT_ID)
    store.record_asset(asset())
    return store


@pytest.fixture
def orchestrator(catalog, clock, ceilings) -> Orchestrator:
    return Orchestrator(
        catalog, policy=ApprovalPolicy(working_days=3), quotas=Quotas(ceilings), clock=clock
    )


@pytest.fixture
def running(catalog, orchestrator) -> Orchestrator:
    """A run parked in ANALYSIS_RUNNING with its plan gate already passed."""
    catalog.record_plan(plan(), created_by=ANALYST)
    catalog.record_run(manifest(), state="CREATED")
    approve_plan(orchestrator)
    walk(
        orchestrator,
        [
            RunState.PREFLIGHT_RUNNING,
            RunState.PREFLIGHT_PASSED,
            RunState.PLAN_DRAFTED,
            RunState.PLAN_APPROVED,
            RunState.ANALYSIS_RUNNING,
        ],
    )
    return orchestrator


def approve_plan(orchestrator: Orchestrator, *, plan_version: str = "1") -> str:
    """The plan gate: an analyst submits, a person who is not the submitter approves."""
    approval = orchestrator.request_approval(
        RUN_ID, ApprovalKind.PLAN, actor=HUMAN_ANALYST, assigned_to=PI, plan_version=plan_version
    ).approval
    orchestrator.decide_approval(approval.approval_id, actor=HUMAN_PI, approved=True, reason="design is sound")
    return approval.approval_id


def approve_node(
    orchestrator: Orchestrator,
    node_id: str,
    *,
    actor: ActorRef = HUMAN_ANALYST,
    assigned_to: str = PI,
    decided_by: ActorRef = HUMAN_PI,
    reason: str = "cleared for this project",
) -> str:
    approval = orchestrator.request_approval(
        RUN_ID, ApprovalKind.NODE, actor=actor, node_id=node_id, assigned_to=assigned_to
    ).approval
    orchestrator.decide_approval(approval.approval_id, actor=decided_by, approved=True, reason=reason)
    return approval.approval_id


def walk(orchestrator: Orchestrator, states, *, actor: ActorRef = AGENT, reason: str = "engine reported progress"):
    for state in states:
        orchestrator.transition(RUN_ID, state, actor=actor, reason=reason)
    return orchestrator.state_of(RUN_ID)
