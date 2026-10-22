"""Wiring for the API tests: a single-node app over an in-memory catalog and object plane."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bioadapt_api.app import create_app
from bioadapt_api.deps import ApiState, InMemoryQueue, SingleNodeCatalogs
from bioadapt_api.identity import Hs256Verifier, IdTokenClaims
from bioadapt_api.observability import MemoryLogSink
from bioadapt_api.quota import ProjectQuotas, StaticQuotas
from bioadapt_orchestrator.approvals import ApprovalPolicy
from bioadapt_policies.access import Role
from bioadapt_policies.providers import ProviderTerms
from bioadapt_storage.traces import InMemoryTraceStore
from bioadapt_schemas.entities import (
    AlignmentRecord,
    CandidateSet,
    DataAsset,
    Project,
    RunManifest,
)
from bioadapt_schemas.enums import (
    AlignmentKind,
    CandidateType,
    EvidenceKind,
    EvidencePartition,
    EvidenceRole,
    FieldSource,
    FinalStatus,
    FilteringState,
    HypothesisType,
    LabelLevel,
    PerturbationKind,
    PrivacyTier,
    ReproducibilityLevel,
    RetentionClass,
    ScreeningResult,
    ToolClass,
    UsageScope,
)
from bioadapt_schemas.evidence import (
    ArtifactScreening,
    Evidence,
    EvidenceCard,
    EvidenceSource,
    FieldProvenance,
    OntologyRef,
    PerturbationResult,
    ScreeningItem,
    StabilitySummary,
)
from bioadapt_schemas.identifiers import derive_candidate
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
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from bioadapt_storage.objectstore import HmacSigner, InMemoryObjectStore
from bioadapt_storage.catalog import Catalog

NOW = datetime(2026, 5, 1, tzinfo=UTC)
ISS = "https://idp.example/bioadapt"
AUD = "bioadapt-api"
SECRET = b"reference-verification-secret"

PROJECT_ID = "prj_001"
OTHER_PROJECT_ID = "prj_002"
LOCAL_PROJECT_ID = "prj_local"
HGR_PROJECT_ID = "prj_hgr"
PUBLIC_PROJECT_ID = "prj_public"
RUN_ID = "run_project001_02"
SHA = "ab" * 32
FINGERPRINT = "cd" * 32

PI = "user_pi_01"
ANALYST = "user_analyst_01"
EXPERT = "user_expert_01"
EXPERT2 = "user_expert_02"
COMPLIANCE = "user_compliance_01"
SERVICE = "svc_orchestrator"
CLOUD_ENDPOINT = "https://models.example.com"
LOCAL_ENDPOINT = "https://model.internal"


#: Section 1.5: the harness's projects have been through stage 0, so a person has attested the cut set.
#: The values and the version stay the appendix C1 ones - every card fixture cites "v0-unconfirmed", and the
#: §7.3 drift guard compares that against what the project registers, so only the flag moves here. Tests of
#: the attestation itself set the flag back to False.
ATTESTED_CUTS = DEFAULT_THRESHOLDS.model_copy(update={"confirmed": True})


def project(project_id: str = PROJECT_ID, **overrides) -> Project:
    fields = {
        "project_id": project_id,
        "study_goal": "find rare immune states",
        "species": "human",
        "privacy_tier": PrivacyTier.RESTRICTED,
        "created_by": PI,
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


def other_asset(asset_id: str = "as_shared", **overrides) -> DataAsset:
    fields = {
        "asset_id": asset_id,
        "project_id": OTHER_PROJECT_ID,
        "uri": f"s3://bioadapt/{OTHER_PROJECT_ID}/reference.h5ad",
        "file_sha256": "12" * 32,
        "content_fingerprint": FINGERPRINT,
        "format": "h5ad",
        "schema_version": "1",
        "size_bytes": 2048,
        "privacy_tier": PrivacyTier.RESTRICTED,
        "usage_scope": UsageScope.CROSS_PROJECT_ALLOWED,
        "retention_class": RetentionClass.DELIVERABLE,
        "filtering_state": FilteringState.NONE,
        "created_at": NOW,
    }
    fields.update(overrides)
    return DataAsset(**fields)


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
        "data_partitions": DataPartitions(
            rule="holdout_donors_v1", discovery_specimens=["spc_01", "spc_02"], confirmation_specimens=["spc_03"]
        ),
        "qc_policy_id": "qc_policy_v1",
        "loop_budget": LoopBudget(max_compute_minutes=240, max_token_cost_usd=5.0),
        "root_inputs": ["asset_counts"],
        "nodes": [node("cluster")],
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


def candidate(run_id: str = RUN_ID, display_name: str = "cluster_7", **overrides) -> CandidateSet:
    candidate_id, fingerprint, size = derive_candidate(
        [("spc_01", "AAAC"), ("spc_01", "TTGG"), ("spc_02", "GGCT")] * 20, run_id
    )
    fields = {
        "candidate_id": candidate_id,
        "run_id": run_id,
        "member_fingerprint": fingerprint,
        "size": size,
        "donor_count": 2,
        "display_name": display_name,
        "cluster_origin": {"method": "leiden", "resolution": 1.0},
        "candidate_types": [CandidateType.C3_RARE_STABLE],
    }
    fields.update(overrides)
    return CandidateSet(**fields)


def evidence(evidence_id: str = "ev_0001", **overrides) -> Evidence:
    fields = {
        "evidence_id": evidence_id,
        "kind": EvidenceKind.DIFFERENTIAL_EXPRESSION,
        "role": EvidenceRole.SUPPORTING,
        "partition": EvidencePartition.DISCOVERY,
        "source": EvidenceSource(run_id=RUN_ID, asset_id="as_0001"),
        "provenance": FieldProvenance(source=FieldSource.TOOL, object_id="as_0001"),
        "independence_group": "atlas_a",
        "used_cell_set_fingerprint": FINGERPRINT,
        "content": {"fdr": 0.001, "log2fc": 1.7},
    }
    fields.update(overrides)
    return Evidence(**fields)


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
        "field_provenance": {"evidence.*.content.*": FieldProvenance(source=FieldSource.TOOL, object_id="as_0001")},
        "support_rule_version": "supp_rule_v1",
        "thresholds_version": "v0-unconfirmed",
        "final_status": FinalStatus.UNKNOWN,
        "status_reason": "awaiting independent confirmation",
    }
    fields.update(overrides)
    return EvidenceCard(**fields)


def _screened() -> ArtifactScreening:
    """Section 1.5: every artefact risk has an independent tool output, and none of them blocked."""
    item = ScreeningItem(result=ScreeningResult.PASS, tool="scrublet", tool_version="0.2.3")
    return ArtifactScreening(
        doublet=item,
        ambient=item,
        low_quality=item,
        batch_specific=item,
        stress_cycle=item,
        over_split=item,
    )


def _perturbed() -> StabilitySummary:
    """Section 7.5: the mandatory perturbation classes, each clearing the cuts."""
    return StabilitySummary(
        protocol_version="stab_min5_v1",
        results=[
            PerturbationResult(kind=kind, cc_ari=0.72, jaccard=0.68, replicates=5)
            for kind in PerturbationKind.mandatory_protocol()
        ],
    )


def named_card(**overrides) -> EvidenceCard:
    """A card a person has already named: a field review put the hypothesis and the label on it.

    Its numbers are the ones a finished run leaves behind - every artefact risk screened, the mandatory
    perturbation protocol run, a marker that cleared the cuts - so the ceiling is provisional, or supported
    when an independent confirmation backs the card (section 1.5).
    """
    c = candidate()
    records = {
        "ev_mark": evidence(
            evidence_id="ev_mark", kind=EvidenceKind.MARKER_SPECIFICITY, independence_group="atlas_a"
        ),
        "ev_ref": evidence(
            evidence_id="ev_ref", kind=EvidenceKind.REFERENCE_COVERAGE, independence_group="atlas_b"
        ),
    }
    supporting = ["ev_mark", "ev_ref"]
    confirmation: list[str] = []
    if overrides.pop("confirmed", False):
        records["ev_conf"] = evidence(
            evidence_id="ev_conf",
            kind=EvidenceKind.DIFFERENTIAL_EXPRESSION,
            partition=EvidencePartition.CONFIRMATION,
            independence_group="atlas_c",
        )
        supporting.append("ev_conf")
        confirmation = ["ev_conf"]
    fields = {
        "card_id": "card_named",
        "candidate_id": c.candidate_id,
        "run_id": RUN_ID,
        "display_name": c.display_name,
        "candidate_types": [CandidateType.C1_UNASSIGNED],
        "hypothesis_type": HypothesisType.CELL_TYPE,
        "candidate_label": "CD8+ effector T",
        "label_level": LabelLevel.CELL_TYPE_BROAD,
        "ontology": OntologyRef(
            term_id="CL:0000629", ontology_name="Cell Ontology", ontology_version="2026-05-01"
        ),
        "evidence": records,
        "supporting_evidence_ids": supporting,
        "confirmation_evidence_ids": confirmation,
        "artifact_screening": _screened(),
        "stability_summary": _perturbed(),
        "field_provenance": {
            "evidence.*.content.*": FieldProvenance(source=FieldSource.TOOL, object_id="as_0001"),
            **{
                f"stability_summary.results[{index}].{number}": FieldProvenance(
                    source=FieldSource.TOOL, object_id="as_stability"
                )
                for index in range(len(PerturbationKind.mandatory_protocol()))
                for number in ("cc_ari", "jaccard", "replicates")
            },
        },
        "support_rule_version": "supp_rule_v1",
        "thresholds_version": "v0-unconfirmed",
        "final_status": FinalStatus.UNKNOWN,
        "status_reason": "named by the reviewer; the status is still to be decided",
    }
    fields.update(overrides)
    return EvidenceCard(**fields)


def terms(**overrides) -> ProviderTerms:
    fields = {
        "provider": "example",
        "endpoint": CLOUD_ENDPOINT,
        "zero_retention": True,
        "no_training_use": True,
        "subprocessors_disclosed": True,
        "data_region": "eu",
        "dpa_signed": True,
        "reviewed_at": NOW.date(),
        "reviewed_by": COMPLIANCE,
    }
    fields.update(overrides)
    return ProviderTerms(**fields)


def bearer(verifier: Hs256Verifier, subject_id: str, roles: list[Role], **kwargs: Any) -> dict[str, str]:
    claims = IdTokenClaims(
        sub=subject_id,
        iss=ISS,
        aud=AUD,
        roles=roles,
        project_ids=list(kwargs.pop("project_ids", [PROJECT_ID])),
        is_service_account=kwargs.pop("service", False),
        solo_mode=kwargs.pop("solo_mode", False),
        amr=list(kwargs.pop("amr", [])),
        exp=int((NOW + timedelta(seconds=kwargs.pop("ttl", 300))).timestamp()),
    )
    return {"Authorization": f"Bearer {verifier.issue(claims)}"}


@pytest.fixture
def catalog():
    store = Catalog(":memory:", clock=lambda: NOW, check_same_thread=False)
    for registration in (
        project(),
        project(OTHER_PROJECT_ID),
        project(LOCAL_PROJECT_ID, privacy_tier=PrivacyTier.STRICT_LOCAL),
        project(HGR_PROJECT_ID, privacy_tier=PrivacyTier.HGR_CONTROLLED),
        project(PUBLIC_PROJECT_ID, privacy_tier=PrivacyTier.PUBLIC),
    ):
        store.create_project(registration)
        #: Section 1.5: stage 0 has run in this harness, which is the one thing a registration cannot say
        #: for itself - so the cut set is attested through the same write the confirmation route uses.
        store.set_scope(registration.project_id).update_project_thresholds(ATTESTED_CUTS)
    store.set_scope(PROJECT_ID)
    store.record_plan(plan(), created_by=ANALYST)
    store.record_asset(asset())
    store.record_run(manifest(), state="CREATED")
    yield store
    store.close()


@pytest.fixture
def state(catalog) -> ApiState:
    return ApiState(
        catalogs=SingleNodeCatalogs(catalog),
        objects=InMemoryObjectStore(),
        signer=HmacSigner(SECRET),
        verifier=Hs256Verifier(SECRET, issuer=ISS, audience=AUD),
        quotas=StaticQuotas(
            default=ProjectQuotas(
                max_storage_bytes=10_000, max_active_runs=4, max_run_compute_minutes=480, max_run_token_cost_usd=10.0
            ),
            overrides={OTHER_PROJECT_ID: ProjectQuotas(
                max_storage_bytes=10_000, max_active_runs=5, max_run_compute_minutes=480, max_run_token_cost_usd=10.0
            )},
        ),
        queue=InMemoryQueue(running_slots=1),
        traces=InMemoryTraceStore(),
        log_sink=MemoryLogSink(),
        clock=lambda: NOW,
        approval_policy=ApprovalPolicy(working_days=3),
        local_hosts=frozenset({LOCAL_ENDPOINT}),
        cloud_allowlist=frozenset({CLOUD_ENDPOINT}),
        provider_terms={CLOUD_ENDPOINT: terms()},
    )


@pytest.fixture
def client(state) -> TestClient:
    with TestClient(create_app(state)) as test_client:
        yield test_client


@pytest.fixture
def auth(state):
    def _auth(subject_id: str, roles: list[Role], **kwargs: Any) -> dict[str, str]:
        return bearer(state.verifier, subject_id, roles, **kwargs)

    return _auth


@pytest.fixture
def seeded(state, catalog) -> dict[str, str]:
    """One candidate per run under the same cluster label, each with its card, plus a recorded alignment."""
    other_run = "run_project001_03"
    catalog.set_scope(PROJECT_ID)
    catalog.record_run(manifest(run_id=other_run), state="CREATED")
    second = candidate(run_id=other_run)
    first = candidate(
        alignment_history=[
            AlignmentRecord(
                other_candidate_id=second.candidate_id,
                other_run_id=other_run,
                kind=AlignmentKind.UNCERTAIN,
                jaccard=0.65,
            )
        ]
    )
    catalog.record_candidate(first)
    catalog.record_candidate(second)
    catalog.record_card(card(candidate_id=first.candidate_id))
    catalog.record_card(card(card_id="card_002", candidate_id=second.candidate_id, run_id=other_run))
    return {
        "candidate_id": first.candidate_id,
        "other_candidate_id": second.candidate_id,
        "card_id": "card_001",
        "other_card_id": "card_002",
        "run_id": RUN_ID,
        "other_run_id": other_run,
        "cluster_id": first.display_name,
    }
