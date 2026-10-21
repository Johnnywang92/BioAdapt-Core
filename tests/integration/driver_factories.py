"""Wiring for the closed-loop tests: registry, index, object store and state machine around one plan.

These tests live at the workspace root rather than under ``workflows/`` because they join the two
layers §9.5 keeps apart on purpose. The driver is the seam between them, and a seam is only proved by
running the real state machine against the real executor - a fake journal would test the driver's own
guess about what the journal does, which is the bug class §9.1 and §9.6 are about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

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
from bioadapt_schemas.orchestration import AdhocToolGrant, new_tool_grant_id
from bioadapt_schemas.plan import (
    MANDATORY_STOP_CONDITIONS,
    AnalysisPlan,
    ConfoundingCheck,
    DataPartitions,
    DesignMatrix,
    LoopBudget,
    NodeResources,
    PlanNode,
    RetryPolicy,
)
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from bioadapt_scrnaseq import (
    SLOT_COUNTS,
    TASK_CLUSTERING,
    TASK_COVERAGE,
    TASK_EMBEDDING,
    TASK_FEATURE_SELECTION,
    TASK_MARKERS,
    TASK_NEIGHBORS,
    TASK_NORMALIZE,
    TASK_PSEUDOBULK_DE,
    TASK_QC,
    TASK_ROBUSTNESS,
    TASK_RUNNERS,
    TASK_STATE_AXIS,
    AssetDefaults,
    Deployment,
    ExpressionProduct,
    RunOutcome,
    WorkflowRunner,
    adhoc_spec,
    reference_tool_specs,
    write_asset,
)
from bioadapt_storage.catalog import Catalog
from bioadapt_storage.objectstore import InMemoryObjectStore
from bioadapt_tool_registry.registry import AdhocApproval, ToolRegistry
from bioadapt_tool_registry.spec import ToolSpec
from dataset import synthetic_dataset

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)  # a Monday, so an approval window can cross a weekend

PROJECT_ID = "prj_scrnaseq"
RUN_ID = "run_scrnaseq_01"
PLAN_ID = "plan_scrnaseq"

PI = "user_pi_01"
ANALYST = "user_analyst_01"
SERVICE = "svc_workflow_engine"
HUMAN_PI = ActorRef(PI, SubjectKind.HUMAN)
HUMAN_ANALYST = ActorRef(ANALYST, SubjectKind.HUMAN)
ENGINE = ActorRef(SERVICE, SubjectKind.SERVICE)

#: The stage-0 registration facts. They are spelled out here rather than defaulted in the workflow,
#: because appendix C1 makes every one of them a number a person confirmed before admission.
CPU = 4
MEMORY_GB = 16
MAX_MINUTES = 60
MEASURED_PEAK_MEMORY_MB = 1200
TOOL_VERSION = "1.0.0"

#: Section 1.5: this harness drives a project that has finished stage 0, so its cut set is attested. The
#: values and the version stay the appendix C1 ones - every product written below cites
#: ``DEFAULT_THRESHOLDS.version``, and the §7.3 drift guard compares that citation against this row.
#: What moves is only the flag, and only because the sign-off and export exits read it.
ATTESTED_CUTS = DEFAULT_THRESHOLDS.model_copy(update={"confirmed": True})

DEPLOYMENT = Deployment(
    container_repository="registry.internal/bioadapt/scrnaseq-reference",
    container_tag="1.0.0",
    container_digest="sha256:" + "ab" * 32,
    tool_version=TOOL_VERSION,
    species=("human",),
    cpu=CPU,
    memory_mb=MEMORY_GB * 1024,
    max_minutes=MAX_MINUTES,
    measured_peak_memory_mb=MEASURED_PEAK_MEMORY_MB,
    measured_on="synthetic_pbmc_v1",
    license_spdx_id="BSD-3-Clause",
    license_display_name="Scanpy reference steps",
    license_reviewed_at=date(2026, 5, 4),
    license_review_note="reviewed against the platform's own licence matrix on 2026-05-04",
)

#: Execution order doubles as the dependency chain, so the plan's topological order is this tuple.
CHAIN: tuple[tuple[str, str], ...] = (
    ("qc", TASK_QC),
    ("normalize", TASK_NORMALIZE),
    ("feature_selection", TASK_FEATURE_SELECTION),
    ("embedding", TASK_EMBEDDING),
    ("neighbors", TASK_NEIGHBORS),
    ("clustering", TASK_CLUSTERING),
    ("markers", TASK_MARKERS),
    ("pseudobulk_de", TASK_PSEUDOBULK_DE),
)
TOOL_NAMES = {
    TASK_QC: "scrnaseq_qc",
    TASK_NORMALIZE: "scrnaseq_normalize",
    TASK_FEATURE_SELECTION: "scrnaseq_feature_selection",
    TASK_EMBEDDING: "scrnaseq_pca",
    TASK_NEIGHBORS: "scrnaseq_neighbors",
    TASK_CLUSTERING: "scrnaseq_cluster",
    TASK_MARKERS: "scrnaseq_markers",
    TASK_PSEUDOBULK_DE: "scrnaseq_pseudobulk_de",
    TASK_ROBUSTNESS: "scrnaseq_robustness",
    TASK_COVERAGE: "scrnaseq_coverage",
    TASK_STATE_AXIS: "scrnaseq_state_axis",
}

#: The candidate follow-up nodes of sections 7.5 and 7.6. They stay out of ``CHAIN`` because the eight-node
#: plan is the deliverable the other tests count, and a robustness arm re-runs the chain on its own.
FOLLOW_UP: tuple[tuple[str, str], ...] = (
    ("robustness", TASK_ROBUSTNESS),
    ("coverage", TASK_COVERAGE),
    ("state_axis", TASK_STATE_AXIS),
)
TASK_OF: dict[str, str] = {**dict(CHAIN), **dict(FOLLOW_UP)}

#: One charged attempt, fixed rather than measured, so a budget assertion says what it means.
MINUTES_PER_ATTEMPT = 0.5

#: The cluster the reference executor gives the dataset's planted ``rare_state`` population. The
#: follow-up nodes measure one named candidate rather than the whole partition, which is what §7.6 asks.
CANDIDATE_CLUSTER = "c002"

ASSET_DEFAULTS = AssetDefaults(
    privacy_tier=PrivacyTier.RESTRICTED,
    usage_scope=UsageScope.THIS_PROJECT_ONLY,
    retention_class=RetentionClass.RUN_LINKED,
    filtering_state=FilteringState.NONE,
    ephemeral_slots=frozenset({"embedding", "graph"}),
)

#: The slots each node reads. Restated here rather than imported from the wrapper, so that a test can
#: deliberately wire a node wrong: the driver must notice, and it cannot if the plan is generated from
#: the same table it is being checked against (§4.1).
DEFAULT_INPUTS = {
    "qc": (SLOT_COUNTS,),
    "normalize": (SLOT_COUNTS,),
    "feature_selection": ("expression",),
    "embedding": ("expression", "features"),
    "neighbors": ("embedding",),
    "clustering": ("graph",),
    "markers": ("expression", "clusters"),
    "pseudobulk_de": (SLOT_COUNTS, "clusters"),
    "robustness": ("expression", "clusters"),
    "coverage": ("embedding", "clusters"),
    "state_axis": ("expression", "clusters"),
}


class Clock:
    """A logical clock the test moves: the driver must not depend on wall-clock time passing."""

    def __init__(self, start: datetime = NOW) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


class Ceilings:
    max_compute_minutes = 480
    max_model_cost_usd = 20.0


class Quotas:
    def for_project(self, project_id: str) -> Ceilings:
        return Ceilings()


def stage0_parameters() -> dict[str, dict[str, Any]]:
    """The stage-0 numbers each node runs with, keyed by node id."""
    from workflow_factories import (
        MAX_FLAGGED_SHARE,
        MAX_MEAN,
        MIN_MEAN,
        MIN_VARIANCE,
        N_COMPONENTS,
        N_NEIGHBOURS,
        N_TOP_FEATURES,
        PERMUTATION_REPLICATES,
        RESOLUTION,
        SEEDS,
        TARGET_SUM,
        TOP_MARKERS,
        chain_parameter_values,
        qc_policy,
        robustness_protocol,
    )
    from dataset import MITO_GENES, RIBOSOMAL_GENES

    return {
        "qc": {
            "qc_policy": qc_policy(),
            "thresholds_version": DEFAULT_THRESHOLDS.version,
            "max_flagged_share": MAX_FLAGGED_SHARE,
            "mito_genes": list(MITO_GENES),
            "ribosomal_genes": list(RIBOSOMAL_GENES),
        },
        "normalize": {"target_sum": TARGET_SUM, "tolerance": 1e-6},
        "feature_selection": {
            "n_top_features": N_TOP_FEATURES,
            "min_mean": MIN_MEAN,
            "max_mean": MAX_MEAN,
            "min_variance": MIN_VARIANCE,
            "seed": SEEDS["highly_variable_features"],
        },
        "embedding": {
            "n_components": N_COMPONENTS,
            "seed": SEEDS["pca"],
            "iterations": 200,
            "n_loadings": 10,
        },
        "neighbors": {"k": N_NEIGHBOURS, "seed": SEEDS["neighbors"], "metric": "euclidean"},
        "clustering": {"resolution": RESOLUTION, "seed": SEEDS["cluster"]},
        "markers": {"top_n": TOP_MARKERS},
        "pseudobulk_de": {
            "contrasts": [{"group_a": "case", "group_b": "control", "cluster": None}],
            "permutation_replicates": PERMUTATION_REPLICATES,
            "seed": SEEDS["pseudobulk_de"],
            "thresholds": DEFAULT_THRESHOLDS,
        },
        #: The candidate follow-ups. A real run gets its candidate cluster names from the triage step that
        #: proposed the candidate; here the deterministic clustering gives them, so the plan can name one.
        "robustness": {
            **chain_parameter_values(),
            "robustness_protocol": robustness_protocol(),
            "thresholds": DEFAULT_THRESHOLDS,
            "candidate_clusters": [CANDIDATE_CLUSTER],
        },
        "coverage": {
            "thresholds": DEFAULT_THRESHOLDS,
            "candidate_clusters": [CANDIDATE_CLUSTER],
        },
        "state_axis": {
            "candidate_clusters": ["c001"],
            "axis_genes": {"CD14": 1.0, "CD3D": -1.0},
            "tail_quantile": 0.95,
            "tail_share_min": 0.5,
            "axis_explained_min": 0.8,
        },
    }


def _previous(node_id: str) -> tuple[str, ...]:
    ids = [nid for nid, _task in CHAIN]
    if node_id not in ids:
        #: A follow-up node reads what the chain produced, so it hangs off the clustering node.
        return ("clustering",)
    index = ids.index(node_id)
    return () if index == 0 else (ids[index - 1],)


def node(
    node_id: str,
    *,
    tool_name: Optional[str] = None,
    tool_version: str = TOOL_VERSION,
    tool_class: ToolClass = ToolClass.REGISTERED,
    inputs: Optional[Sequence[str]] = None,
    outputs: Optional[Sequence[str]] = None,
    parameters: Optional[Mapping[str, Any]] = None,
    depends_on: Optional[Sequence[str]] = None,
    requires_approval: bool = False,
    retry: Optional[RetryPolicy] = None,
    memory_gb: int = MEMORY_GB,
    max_minutes: int = MAX_MINUTES,
) -> PlanNode:
    task = TASK_OF[node_id]
    return PlanNode(
        node_id=node_id,
        tool_name=tool_name or TOOL_NAMES[task],
        tool_version=tool_version,
        tool_class=tool_class,
        inputs=list(inputs if inputs is not None else DEFAULT_INPUTS[node_id]),
        outputs=list(outputs if outputs is not None else TASK_RUNNERS[task][1]),
        parameters=dict(parameters if parameters is not None else stage0_parameters()[node_id]),
        dependency_node_ids=list(depends_on if depends_on is not None else _previous(node_id)),
        resources=NodeResources(cpu=CPU, memory_gb=memory_gb, max_minutes=max_minutes),
        privacy_policy="project_local",
        requires_approval=requires_approval,
        retry_policy=retry or RetryPolicy(max_attempts=3, backoff_seconds=0),
    )


def plan(
    *,
    plan_version: str = "1",
    gated: tuple[str, ...] = (),
    parameters_for: Optional[Mapping[str, Mapping[str, Any]]] = None,
    nodes: Optional[Sequence[PlanNode]] = None,
    loop_budget: Optional[LoopBudget] = None,
) -> AnalysisPlan:
    if nodes is None:
        defaults = stage0_parameters()
        per_node: dict[str, dict[str, Any]] = {node_id: {} for node_id, _task in CHAIN}
        for node_id in gated:
            per_node[node_id]["requires_approval"] = True
        for node_id, extra in (parameters_for or {}).items():
            per_node[node_id]["parameters"] = {**defaults[node_id], **dict(extra)}
        nodes = [node(node_id, **kwargs) for node_id, kwargs in per_node.items()]
    return AnalysisPlan(
        plan_id=PLAN_ID,
        plan_version=plan_version,
        project_id=PROJECT_ID,
        objective="resolve_an_uncharacterized_population",
        design_matrix=DesignMatrix(
            formula="~ group",
            group_variable="group",
            min_n_per_group=4,
            confounding_checks=[
                ConfoundingCheck(name="group_vs_donor", tool="design_check", status="PASS")
            ],
        ),
        data_partitions=DataPartitions(
            rule="holdout_donors_v1",
            discovery_specimens=["spc_donor_a", "spc_donor_b", "spc_donor_c", "spc_donor_d"],
            confirmation_specimens=["spc_donor_e", "spc_donor_f", "spc_donor_g", "spc_donor_h"],
        ),
        qc_policy_id="qc_pbmc_reference",
        loop_budget=loop_budget
        or LoopBudget(max_iterations=1, max_compute_minutes=180, max_token_cost_usd=5.0),
        root_inputs=[SLOT_COUNTS],
        nodes=list(nodes),
        stop_conditions=list(MANDATORY_STOP_CONDITIONS),
        expected_deliverables=["markers", "de_table"],
        provenance_policy="run_manifest_v1",
    )


def chain_nodes(**overrides: Mapping[str, Any]) -> list[PlanNode]:
    """The eight nodes in execution order, with per-node keyword overrides.

    A test that means to break one node - to under-budget it, to un-wire its inputs, to run it through
    the ad-hoc channel - says so here, and the rest of the chain stays the plan that works.
    """
    return [node(node_id, **dict(overrides.get(node_id) or {})) for node_id, _task in CHAIN]


def follow_up_nodes(**overrides: Mapping[str, Any]) -> list[PlanNode]:
    """The eight chain nodes plus the section 7.5 and 7.6 measurements, in dependency order.

    A candidate is not a different kind of computation: it is a plan node with its own declared inputs and
    outputs, so it goes through admission, budgeting and journaling like every other node.
    """
    order = [node_id for node_id, _task in CHAIN] + [node_id for node_id, _task in FOLLOW_UP]
    return [node(node_id, **dict(overrides.get(node_id) or {})) for node_id in order]


#: The ad-hoc gate a hand-built grant stands for when a test has not run the real decision (§9.6).
ADHOC_GATE_ID = "apr_adhoc_qc_01"


def adhoc_qc_spec(**overrides) -> ToolSpec:
    """A one-submission QC tool, built through §9.6's sandbox shape."""
    fields: dict[str, Any] = {
        "tool_name": "project_qc_probe",
        "tool_version": "0.1.0",
        "task": TASK_QC,
        "deployment": DEPLOYMENT,
        "reason": "the registered qc step cannot read this project's own contamination estimate",
    }
    fields.update(overrides)
    return adhoc_spec(**fields)


def adhoc_grant(**overrides) -> AdhocApproval:
    fields: dict[str, Any] = {
        "approver_id": PI,
        "requested_by": ANALYST,
        "gate_approval_id": ADHOC_GATE_ID,
        "project_id": PROJECT_ID,
        "plan_id": PLAN_ID,
        "node_id": "qc",
        "reason": "single submission for this project only",
        "approved_at": NOW,
        "expires_at": NOW + timedelta(days=7),
    }
    fields.update(overrides)
    return AdhocApproval(**fields)


def reference_registry(**overrides) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in reference_tool_specs(DEPLOYMENT, **overrides).values():
        registry.register(spec)
    return registry


def hydrate(registry: ToolRegistry, catalog: Catalog) -> ToolRegistry:
    """Load every grant this project still holds into a registry, as a launch does (§9.6).

    A spent or expired row is not admitted, so the permission a person granted a while ago cannot come
    back by way of a restart: what the ledger says is live is the whole of what the engine may run. The
    registry is the copy the compute plane works from, and this is the only supported way to fill its
    ad-hoc side - the in-process ``grant()`` call that tests use stands in for a channel no deployment
    has, which is why the ledger is consulted again at spend time.
    """
    for grant in catalog.list_tool_grants(live_only=True):
        registry.admit_grant(grant)
    return registry


def root_product(data) -> ExpressionProduct:
    """The counts the run starts from, in the platform's own product contract."""
    return ExpressionProduct(
        slot=SLOT_COUNTS,
        matrix=data.matrix,
        transform="counts",
        parameters={"staged_by": "project upload"},
    )


def digest(catalog: Catalog, asset_id: str) -> str:
    return catalog.get_asset(asset_id).file_sha256


@dataclass
class Harness:
    """One project's whole control plane: index, objects, registry, journal and driver."""

    catalog: Catalog
    objects: InMemoryObjectStore
    registry: ToolRegistry
    journal: Orchestrator
    runner: WorkflowRunner
    clock: Clock
    data: Any
    root: dict[str, str]
    plan: AnalysisPlan
    run_id: str = RUN_ID

    def run(self, *, run_id: Optional[str] = None) -> RunOutcome:
        return self.runner.run(run_id or self.run_id, root=dict(self.root))

    def node(self, node_id: str) -> PlanNode:
        return next(n for n in self.plan.nodes if n.node_id == node_id)

    def approve_node(self, node_id: str, *, run_id: Optional[str] = None) -> str:
        approval = self.journal.request_approval(
            run_id or self.run_id,
            ApprovalKind.NODE,
            actor=HUMAN_ANALYST,
            node_id=node_id,
            assigned_to=PI,
        ).approval
        self.journal.decide_approval(
            approval.approval_id, actor=HUMAN_PI, approved=True, reason="cleared for this project"
        )
        return approval.approval_id

    def grant_adhoc(
        self, node_id: str, spec: ToolSpec, *, run_id: Optional[str] = None, valid_for_days: int = 7
    ) -> AdhocToolGrant:
        """Ask, answer, issue: the three steps §9.6 puts between a submission and an execution.

        The row is built the way the grant route builds it - submitter and approver both read off the gate
        that was just decided, the window taken from this clock - so a driver test meets the same ledger a
        real launch finds, rather than a permission only the process that made it up can see.
        """
        run = run_id or self.run_id
        gate = self.journal.request_approval(
            run, ApprovalKind.ADHOC_TOOL, actor=HUMAN_ANALYST, node_id=node_id, assigned_to=PI
        ).approval
        decided = self.journal.decide_approval(
            gate.approval_id,
            actor=HUMAN_PI,
            approved=True,
            reason="sandboxed, one submission, results stay unregistered",
        )
        manifest = self.catalog.get_run(run)
        dumped = spec.model_dump(mode="json")
        now = self.clock()
        grant = AdhocToolGrant(
            grant_id=new_tool_grant_id(decided.approval_id, spec.tool_name, spec.tool_version, node_id),
            project_id=PROJECT_ID,
            run_id=run,
            node_id=node_id,
            plan_id=manifest.plan_id,
            plan_version=manifest.plan_version,
            approval_id=decided.approval_id,
            tool_name=spec.tool_name,
            tool_version=spec.tool_version,
            spec=dumped,
            spec_sha256=sha256_of_json(dumped),
            requested_by=decided.requested_by,
            approver_id=decided.decided_by,
            reason=decided.decision_reason or "",
            approved_at=now,
            expires_at=now + timedelta(days=valid_for_days),
        )
        self.catalog.record_tool_grant(grant)
        return grant

    def pin_registry_snapshot(self, run_id: Optional[str] = None) -> str:
        """Section 4.1: a grant that lands after the manifest was written changes the registry it names."""
        snapshot = self.registry.snapshot_id()
        self.catalog.conn.execute(
            "update run_manifest set tool_registry_snapshot = ? where project_id = ? and run_id = ?",
            (snapshot, PROJECT_ID, run_id or self.run_id),
        )
        return snapshot

    def approve_plan(self, run_id: str, *, plan_version: str = "1") -> str:
        approval = self.journal.request_approval(
            run_id, ApprovalKind.PLAN, actor=HUMAN_ANALYST, assigned_to=PI, plan_version=plan_version
        ).approval
        self.journal.decide_approval(
            approval.approval_id, actor=HUMAN_PI, approved=True, reason="design is sound"
        )
        return approval.approval_id

    def walk(self, run_id: str, states: Sequence[RunState]) -> None:
        for state in states:
            self.journal.transition(run_id, state, actor=ENGINE, reason="engine reported progress")

    def start_run(self, run_id: str) -> str:
        """A second run in the same project: same plan, same tools, same root input."""
        manifest = RunManifest(
            run_id=run_id,
            project_id=PROJECT_ID,
            plan_id=self.plan.plan_id,
            plan_version=self.plan.plan_version,
            tool_versions={n.tool_name: n.tool_version for n in self.plan.nodes},
            tool_registry_snapshot=self.registry.snapshot_id(),
            input_hashes={self.root[SLOT_COUNTS]: digest(self.catalog, self.root[SLOT_COUNTS])},
            reproducibility_level_target=ReproducibilityLevel.BYTE,
            started_at=self.clock(),
        )
        self.catalog.record_run(manifest, state=str(RunState.CREATED))
        self.approve_plan(run_id, plan_version=self.plan.plan_version)
        self.walk(
            run_id,
            [
                RunState.PREFLIGHT_RUNNING,
                RunState.PREFLIGHT_PASSED,
                RunState.PLAN_DRAFTED,
                RunState.PLAN_APPROVED,
                RunState.ANALYSIS_RUNNING,
            ],
        )
        return run_id

    def usage(self, run_id: Optional[str] = None) -> Any:
        return self.journal.usage(run_id or self.run_id)

    def events(self, run_id: Optional[str] = None, *, kind: Optional[str] = None) -> list[Any]:
        return self.journal.journal(run_id or self.run_id, kind=kind)

    def asset(self, asset_id: str) -> DataAsset:
        return self.catalog.get_asset(asset_id)

    def rewrite_object(self, asset_id: str, payload: bytes) -> None:
        """Replace a stored object's bytes in place, leaving the index row as it was."""
        uri = self.catalog.get_asset(asset_id).uri
        self.objects.put(uri[len("s3://") :], payload)

    def reindex(self, replacement: Optional[AnalysisPlan] = None, *, registry_snapshot: Optional[str] = None) -> AnalysisPlan:
        """Point the run at another plan version, as a person editing a plan would."""
        target = replacement if replacement is not None else self.plan
        self.catalog.record_plan(target, created_by=ANALYST)
        self.catalog.set_run_plan(
            self.run_id,
            plan_version=target.plan_version,
            tool_versions={n.tool_name: n.tool_version for n in target.nodes},
        )
        self.plan = self.catalog.get_plan(target.plan_id, target.plan_version)
        if registry_snapshot is not None:
            self.catalog.conn.execute(
                "update run_manifest set tool_registry_snapshot = ? where project_id = ? and run_id = ?",
                (registry_snapshot, PROJECT_ID, self.run_id),
            )
        return self.plan

    def resume(self, run_id: Optional[str] = None) -> None:
        """Send a branch-parked run back to the state it was parked from (§9.2), as an approval does."""
        self.journal.transition(
            run_id or self.run_id, RunState.ANALYSIS_RUNNING, actor=ENGINE, reason="the person answered"
        )


FORWARD_TO_ANALYSIS = [
    RunState.PREFLIGHT_RUNNING,
    RunState.PREFLIGHT_PASSED,
    RunState.PLAN_DRAFTED,
    RunState.PLAN_APPROVED,
    RunState.ANALYSIS_RUNNING,
]


def harness(
    *,
    the_plan: Optional[AnalysisPlan] = None,
    registry: Optional[ToolRegistry] = None,
    data=None,
    asset_defaults: AssetDefaults = ASSET_DEFAULTS,
    run_id: str = RUN_ID,
) -> Harness:
    """Build the control plane, with a run already parked in ANALYSIS_RUNNING."""
    clock = Clock()
    catalog = Catalog(":memory:", clock=clock, check_same_thread=False)
    catalog.create_project(
        Project(
            project_id=PROJECT_ID,
            study_goal="resolve an uncharacterized immune population",
            species="human",
            privacy_tier=PrivacyTier.RESTRICTED,
            created_by=PI,
            annotation_granularity_target="cell_type_fine",
            review_load_budget_hours_per_week=6.0,
            created_at=clock(),
            thresholds=ATTESTED_CUTS,
        )
    )
    objects = InMemoryObjectStore()
    the_data = data if data is not None else synthetic_dataset()
    product = root_product(the_data)
    root_id = write_asset(
        catalog,
        objects,
        product,
        project_id=PROJECT_ID,
        defaults=asset_defaults,
        created_at=clock(),
    )
    the_registry = registry if registry is not None else reference_registry()
    the_plan = the_plan if the_plan is not None else plan()
    catalog.record_plan(the_plan, created_by=ANALYST)
    manifest = RunManifest(
        run_id=run_id,
        project_id=PROJECT_ID,
        plan_id=the_plan.plan_id,
        plan_version=the_plan.plan_version,
        tool_versions={n.tool_name: n.tool_version for n in the_plan.nodes},
        tool_registry_snapshot=the_registry.snapshot_id(),
        input_hashes={root_id: product.file_sha256()},
        reproducibility_level_target=ReproducibilityLevel.BYTE,
        started_at=clock(),
    )
    catalog.record_run(manifest, state=str(RunState.CREATED))
    journal = Orchestrator(
        catalog, policy=ApprovalPolicy(working_days=3), quotas=Quotas(), clock=clock
    )
    runner = WorkflowRunner(
        catalog=catalog,
        objects=objects,
        registry=the_registry,
        journal=journal,
        actor=ENGINE,
        asset_defaults=asset_defaults,
        clock=clock,
        usage_minutes=lambda _node, _result, _elapsed: MINUTES_PER_ATTEMPT,
    )
    made = Harness(
        catalog=catalog,
        objects=objects,
        registry=the_registry,
        journal=journal,
        runner=runner,
        clock=clock,
        data=the_data,
        root={SLOT_COUNTS: root_id},
        plan=the_plan,
        run_id=run_id,
    )
    made.approve_plan(run_id, plan_version=the_plan.plan_version)
    made.walk(run_id, FORWARD_TO_ANALYSIS)
    return made
