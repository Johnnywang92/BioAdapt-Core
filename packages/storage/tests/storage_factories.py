"""Factories for the storage tests - deliberately independent of the schemas test helpers."""

from datetime import datetime, timezone

from bioadapt_schemas.entities import (
    AnnotationVersion,
    AssetLineage,
    CandidateSet,
    DataAsset,
    Project,
    ReviewEvent,
    RunManifest,
)
from bioadapt_schemas.enums import (
    AnnotationStatus,
    AssetRole,
    CandidateType,
    EvidenceKind,
    EvidencePartition,
    EvidenceRole,
    FieldSource,
    FinalStatus,
    FilteringState,
    HypothesisType,
    LabelLevel,
    PrivacyTier,
    ReproducibilityLevel,
    RetentionClass,
    ReviewDecision,
    ReviewScope,
    RunEventKind,
    RunState,
    SubjectKind,
    ToolClass,
    UsageScope,
)
from bioadapt_schemas.evidence import Evidence, EvidenceCard, EvidenceSource, FieldProvenance
from bioadapt_schemas.identifiers import derive_candidate
from bioadapt_schemas.nodes import ExecutionNode, NodeCapabilities, NodePosition
from bioadapt_schemas.orchestration import ExecutionReceipt, RunEvent
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

UTC = timezone.utc
NOW = datetime(2026, 5, 1, tzinfo=UTC)
PROJECT_ID = "prj_001"
OTHER_PROJECT_ID = "prj_002"
RUN_ID = "run_project001_02"
SHA = "ab" * 32
FINGERPRINT = "cd" * 32


def project(project_id: str = PROJECT_ID, **overrides) -> Project:
    fields = {
        "project_id": project_id,
        "study_goal": "find rare immune states",
        "species": "human",
        "privacy_tier": PrivacyTier.RESTRICTED,
        "created_by": "user_pi_01",
        "annotation_granularity_target": "cell_type_fine",
        "review_load_budget_hours_per_week": 4.0,
        "created_at": NOW,
    }
    fields.update(overrides)
    return Project(**fields)


def asset(asset_id: str = "as_0001", **overrides) -> DataAsset:
    fields = {
        "asset_id": asset_id,
        "project_id": PROJECT_ID,
        "uri": "s3://bioadapt/prj_001/count_matrix.h5ad",
        "file_sha256": SHA,
        "content_fingerprint": FINGERPRINT,
        "format": "h5ad",
        "schema_version": "1",
        "size_bytes": 1024,
        "privacy_tier": PrivacyTier.RESTRICTED,
        "usage_scope": UsageScope.THIS_PROJECT_ONLY,
        "retention_class": RetentionClass.EPHEMERAL,
        "filtering_state": FilteringState.NONE,
        "created_at": NOW,
    }
    fields.update(overrides)
    return DataAsset(**fields)


def lineage(asset_id: str = "as_0001", role: AssetRole = AssetRole.INPUT, **overrides) -> AssetLineage:
    fields = {
        "run_id": RUN_ID,
        "node_id": "node_qc",
        "asset_id": asset_id,
        "role": role,
        "ordinal": 0,
    }
    fields.update(overrides)
    return AssetLineage(**fields)


def manifest(**overrides) -> RunManifest:
    fields = {
        "run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "plan_id": "plan_001",
        "plan_version": "3",
        "tool_versions": {"scrublet": "0.2.3"},
        "tool_registry_snapshot": "snap_0001",
        "input_hashes": {"as_0001": SHA},
        "reproducibility_level_target": ReproducibilityLevel.BYTE,
        "started_at": NOW,
    }
    fields.update(overrides)
    return RunManifest(**fields)


def event(
    event_id: str = "evt_0001",
    *,
    kind: RunEventKind = RunEventKind.STATE,
    from_state: RunState = RunState.CREATED,
    to_state: RunState = RunState.ANALYSIS_RUNNING,
    **overrides,
) -> RunEvent:
    fields = {
        "event_id": event_id,
        "run_id": RUN_ID,
        "kind": kind,
        "from_state": from_state,
        "to_state": to_state,
        "actor_id": "svc_pipeline",
        "actor_kind": SubjectKind.SERVICE,
        "reason": "engine reported progress",
        "input_hash": SHA,
    }
    fields.update(overrides)
    return RunEvent(**fields)


def annotation(version_id: str = "av_0001", asset_id: str = "as_0001", **overrides) -> AnnotationVersion:
    fields = {
        "version_id": version_id,
        "run_id": RUN_ID,
        "asset_id": asset_id,
        "label_column": "celltype",
        "level": LabelLevel.CELL_TYPE_FINE,
        "created_by": "user_analyst_01",
        "status": AnnotationStatus.ACTIVE,
    }
    fields.update(overrides)
    return AnnotationVersion(**fields)


def evidence(evidence_id: str = "ev_0001") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        kind=EvidenceKind.DIFFERENTIAL_EXPRESSION,
        role=EvidenceRole.SUPPORTING,
        partition=EvidencePartition.DISCOVERY,
        source=EvidenceSource(run_id=RUN_ID, asset_id="as_0001"),
        provenance=FieldProvenance(source=FieldSource.TOOL, object_id="as_0001"),
        independence_group="atlas_a",
        used_cell_set_fingerprint=FINGERPRINT,
        content={"fdr": 0.001, "log2fc": 1.7},
    )


def candidate(*, run_id: str = RUN_ID, **overrides) -> CandidateSet:
    #: §5.6: a candidate is addressed by its members inside one run, so the id and the fingerprint are
    #: derived from the run the caller asks for - three runs give three distinct rows over one label.
    candidate_id, fingerprint, size = derive_candidate(
        [("spc_01", "AAAC"), ("spc_01", "TTGG"), ("spc_02", "GGCT")] * 20, run_id
    )
    fields = {
        "candidate_id": candidate_id,
        "run_id": run_id,
        "member_fingerprint": fingerprint,
        "size": size,
        "donor_count": 2,
        "display_name": "cluster_7",
        "cluster_origin": {"method": "leiden", "resolution": 1.0},
        "candidate_types": [CandidateType.C3_RARE_STABLE],
    }
    fields.update(overrides)
    return CandidateSet(**fields)


def card(**overrides) -> EvidenceCard:
    c = candidate()
    fields = {
        "card_id": "card_001",
        "candidate_id": c.candidate_id,
        "run_id": RUN_ID,
        "display_name": c.display_name,
        "candidate_types": c.candidate_types,
        "hypothesis_type": HypothesisType.UNKNOWN,
        "unknown_reason": "insufficient_evidence",
        "label_level": LabelLevel.UNKNOWN_COARSE,
        "evidence": {evidence().evidence_id: evidence()},
        "support_rule_version": "supp_rule_v1",
        "thresholds_version": "v0-unconfirmed",
        "final_status": FinalStatus.UNKNOWN,
        "status_reason": "awaiting independent confirmation",
    }
    fields.update(overrides)
    return EvidenceCard(**fields)


def review(review_id: str = "rev_0001", object_id: str = "card_001", **overrides) -> ReviewEvent:
    fields = {
        "review_id": review_id,
        "object_id": object_id,
        "object_type": "card",
        "scope": ReviewScope.CARD,
        "reviewer_id": "user_expert_01",
        "decision": ReviewDecision.APPROVE,
        "rationale": "evidence holds across donors",
        "timestamp": NOW,
    }
    fields.update(overrides)
    return ReviewEvent(**fields)


def execution_node(node_id: str = "node_laptop", **overrides) -> ExecutionNode:
    fields = {
        "node_id": node_id,
        "label": "workstation",
        "position": NodePosition.LOCAL,
        "capabilities": NodeCapabilities(cpu=8, memory_mb=16384, can_run_containers=True),
        "registered_by": "user_platform_admin_01",
        "registered_at": NOW,
    }
    fields.update(overrides)
    return ExecutionNode(**fields)


def receipt(**overrides) -> ExecutionReceipt:
    fields = {
        "run_id": RUN_ID,
        "node_id": "qc",
        "attempt": 1,
        "tool_name": "scanpy_pipeline",
        "tool_version": "1.11.0",
        "input_digest": "12" * 32,
        "parameters_sha256": "34" * 32,
        "registry_snapshot": "56" * 32,
        "outcome_digest": "78" * 32,
        "status": "success",
        "output_asset_ids": ["as_0001"],
        "output_digests": {"as_0001": SHA},
        "executed_on": "node_laptop",
        "compute_minutes": 1.5,
        "at": NOW,
    }
    fields.update(overrides)
    return ExecutionReceipt(**fields)


def node(node_id: str, **overrides) -> PlanNode:
    fields = {
        "node_id": node_id,
        "tool_name": "scanpy_pipeline",
        "tool_version": "1.11.0",
        "tool_class": ToolClass.REGISTERED,
        "outputs": ["clusters"],
        "resources": NodeResources(cpu=4, memory_gb=16, max_minutes=120),
        "privacy_policy": "local_only",
    }
    fields.update(overrides)
    return PlanNode(**fields)


def plan(**overrides) -> AnalysisPlan:
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
        "data_partitions": DataPartitions(rule="holdout_donors_v1", discovery_specimens=["spc_01", "spc_02"]),
        "qc_policy_id": "qc_policy_v1",
        "loop_budget": LoopBudget(max_compute_minutes=240, max_token_cost_usd=5.0),
        "root_inputs": ["asset_counts"],
        "nodes": [node("cluster")],
        "stop_conditions": list(MANDATORY_STOP_CONDITIONS),
        "provenance_policy": "run_manifest_v1",
    }
    fields.update(overrides)
    return AnalysisPlan(**fields)
