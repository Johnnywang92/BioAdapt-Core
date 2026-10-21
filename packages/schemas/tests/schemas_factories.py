from datetime import datetime, timezone

from bioadapt_schemas.enums import (
    CandidateType,
    CrossModalStatus,
    EvidenceKind,
    EvidencePartition,
    EvidenceRole,
    FieldSource,
    FinalStatus,
    HypothesisType,
    LabelLevel,
    PerturbationKind,
    ScreeningResult,
    SignOff,
)
from bioadapt_schemas.evidence import (
    ArtifactScreening,
    CompositionCheck,
    ContinuousStateCheck,
    Evidence,
    EvidenceCard,
    EvidenceSource,
    FieldProvenance,
    IdentityVsState,
    NoveltyVsCoverage,
    OntologyRef,
    PerturbationResult,
    ScreeningItem,
    StabilitySummary,
)
from bioadapt_schemas.gates import audited_numeric_paths
from bioadapt_schemas.identifiers import derive_candidate

RUN_ID = "run_project001_02"
UTC = timezone.utc
SCREEN_KEYS = ("doublet", "ambient", "low_quality", "batch_specific", "stress_cycle", "over_split")


def screen_item(result: ScreeningResult = ScreeningResult.PASS) -> ScreeningItem:
    return ScreeningItem(
        result=result, tool="scrublet", tool_version="0.2.3", threshold_source="qc_policy_v1"
    )


def make_screening(**overrides) -> ArtifactScreening:
    items = {name: screen_item() for name in SCREEN_KEYS}
    items.update(overrides)
    return ArtifactScreening(**items)


def make_composition(**overrides) -> CompositionCheck:
    """Section 7.2's C5 set: the composition test, its result, and the strategy that held its error rate."""
    fields = {
        "method": "chi_square_composition_across_groups",
        "p_value": 0.004,
        "fdr_adjusted": 0.021,
        "multiplicity_strategy": "benjamini_hochberg",
        "result": "outlier",
    }
    fields.update(overrides)
    return CompositionCheck(**fields)


def make_stability(**overrides) -> StabilitySummary:
    fields = {
        "protocol_version": "stab_min5_v1",
        "results": [
            PerturbationResult(kind=kind, cc_ari=0.72, jaccard=0.68, replicates=5)
            for kind in PerturbationKind.mandatory_protocol()
        ],
    }
    fields.update(overrides)
    return StabilitySummary(**fields)


def make_evidence(
    eid: str,
    role: EvidenceRole,
    partition: EvidencePartition,
    group: str,
    content: dict | None = None,
    kind: EvidenceKind | None = None,
) -> Evidence:
    source = (
        EvidenceSource(dataset_id="ref_x", version="2026-05")
        if role is EvidenceRole.TRIGGER
        else EvidenceSource(run_id=RUN_ID, asset_id=f"asset_{eid}")
    )
    if kind is None:
        kind = EvidenceKind.DIFFERENTIAL_EXPRESSION if role is EvidenceRole.SUPPORTING else EvidenceKind.REFERENCE_COVERAGE
    return Evidence(
        evidence_id=eid,
        kind=kind,
        role=role,
        partition=partition,
        source=source,
        provenance=FieldProvenance(source=FieldSource.TOOL, object_id=f"asset_{eid}"),
        independence_group=group,
        used_cell_set_fingerprint="f" * 64,
        content=content if content is not None else {"fdr": 0.001, "log2fc": 1.8, "detection_frequency_in": 0.82},
    )


def make_card(status: FinalStatus = FinalStatus.PROVISIONAL, **overrides) -> EvidenceCard:
    candidate_id, _fingerprint, _size = derive_candidate(
        [("spc_01", "AAAC"), ("spc_01", "TTGG"), ("spc_02", "GGCT")] * 20, RUN_ID
    )
    signed = status is FinalStatus.SUPPORTED
    fields: dict = {
        "card_id": "card_001",
        "candidate_id": candidate_id,
        "run_id": RUN_ID,
        "display_name": "cluster_7",
        "candidate_types": [CandidateType.C3_RARE_STABLE],
        "hypothesis_type": HypothesisType.CELL_TYPE,
        "candidate_label": "candidate_myeloid_subtype",
        "label_level": LabelLevel.PROVISIONAL_PROJECT,
        "ontology": OntologyRef(),
        "evidence": {
            e.evidence_id: e
            for e in (
                make_evidence("ev_support", EvidenceRole.SUPPORTING, EvidencePartition.DISCOVERY, "atlas_a"),
                make_evidence("ev_confirm", EvidenceRole.SUPPORTING, EvidencePartition.CONFIRMATION, "holdout_donors"),
                make_evidence("ev_contra", EvidenceRole.CONTRADICTING, EvidencePartition.DISCOVERY, "panel_b"),
                make_evidence("ev_trigger", EvidenceRole.TRIGGER, EvidencePartition.DISCOVERY, "ref_x", {"distance_ratio": 3.1}),
            )
        },
        "supporting_evidence_ids": ["ev_support", "ev_confirm"] if signed else ["ev_support"],
        "confirmation_evidence_ids": ["ev_confirm"] if signed else [],
        "contradicting_evidence_ids": ["ev_contra"],
        "trigger_signal_ids": ["ev_trigger"],
        "artifact_screening": make_screening(),
        #: Section 7.2's C3 set includes the continuous-state check, so the default card carries one: the
        #: factory builds a card that *should* pass, and tests break exactly one thing at a time.
        "identity_vs_state": IdentityVsState(
            continuous_state_check=ContinuousStateCheck(
                method="marker_gradient_projection", is_axis_tail=False, statistic=0.12
            )
        ),
        "novelty_vs_coverage": NoveltyVsCoverage(
            knn=20, reference_coverage_score=0.02, distance_ratio=3.1, coverage_gap=True
        ),
        "stability_summary": make_stability(),
        "cross_modal_status": CrossModalStatus.NOT_YET_VALIDATED,
        "support_rule_version": "supp_rule_v1",
        "thresholds_version": "v0-unconfirmed",
        "final_status": status,
        "status_reason": "signed" if signed else "awaiting independent confirmation",
        "reviewer_id": "user_expert_01" if signed else None,
        "review_reason": "approved" if signed else None,
        "sign_off": SignOff.FULL if signed else SignOff.NOT_SIGNED,
        "reviewed_at": datetime(2026, 10, 19, tzinfo=UTC) if signed else None,
    }
    provenance_overrides = overrides.pop("field_provenance", None)
    fields.update(overrides)
    card = EvidenceCard(**fields)
    provenance = {
        path: FieldProvenance(source=FieldSource.TOOL, object_id="asset_tool_output")
        for path in audited_numeric_paths(card)
    }
    for path, entry in (provenance_overrides or {}).items():
        provenance[path] = entry
    return card.model_copy(update={"field_provenance": provenance})
