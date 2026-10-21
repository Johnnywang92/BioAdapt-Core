"""Evidence card contracts (plan V2.0 sections 3.3, 5.4, 7.3, 7.5-7.8).

Entity validators cover structural integrity; scientific ceilings live in ``gates``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field, model_validator

from .entities import Contract, ImmutableContract
from .enums import (
    ArtifactRisk,
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
    UnknownReason,
)


class OntologyRef(ImmutableContract):
    term_id: Optional[str] = None
    ontology_name: Optional[str] = None
    ontology_version: Optional[str] = None
    mapping_source: Optional[str] = None
    mapping_confidence: Optional[float] = Field(None, ge=0, le=1)

    @model_validator(mode="after")
    def _no_fabricated_id(self) -> "OntologyRef":
        if self.term_id and not (self.ontology_name and self.ontology_version):
            raise ValueError("ontology term_id requires ontology_name and ontology_version")
        if self.ontology_name and not self.term_id:
            raise ValueError("ontology_name given without term_id")
        return self


class FieldProvenance(ImmutableContract):
    source: FieldSource
    object_id: str
    note: Optional[str] = None


class PerturbationResult(ImmutableContract):
    kind: PerturbationKind
    cc_ari: Optional[float] = None
    jaccard: Optional[float] = None
    replicates: int = Field(ge=1)
    detail: dict[str, Any] = Field(default_factory=dict)


class StabilitySummary(ImmutableContract):
    protocol_version: str
    results: list[PerturbationResult]
    approved_deviation: Optional[str] = None

    @model_validator(mode="after")
    def _require_results(self) -> "StabilitySummary":
        if not self.results:
            raise ValueError("stability summary requires perturbation results")
        kinds = [r.kind for r in self.results]
        if len(set(kinds)) != len(kinds):
            raise ValueError("duplicate perturbation kinds")
        return self


class ScreeningItem(ImmutableContract):
    result: ScreeningResult
    tool: Optional[str] = None
    tool_version: Optional[str] = None
    threshold_source: Optional[str] = None
    detail: Optional[str] = None

    @model_validator(mode="after")
    def _screened_items_need_provenance(self) -> "ScreeningItem":
        if self.result is not ScreeningResult.NOT_RUN and not (self.tool and self.tool_version):
            raise ValueError("screened risk requires tool and tool_version")
        return self


class ArtifactScreening(ImmutableContract):
    doublet: ScreeningItem
    ambient: ScreeningItem
    low_quality: ScreeningItem
    batch_specific: ScreeningItem
    stress_cycle: ScreeningItem
    over_split: ScreeningItem

    @property
    def items(self) -> dict[ArtifactRisk, ScreeningItem]:
        return {
            ArtifactRisk.DOUBLET: self.doublet,
            ArtifactRisk.AMBIENT: self.ambient,
            ArtifactRisk.LOW_QUALITY: self.low_quality,
            ArtifactRisk.BATCH_SPECIFIC: self.batch_specific,
            ArtifactRisk.STRESS_CYCLE: self.stress_cycle,
            ArtifactRisk.OVER_SPLIT: self.over_split,
        }

    @property
    def blocked_by(self) -> list[ArtifactRisk]:
        return [risk for risk, item in self.items.items() if item.result is ScreeningResult.FAIL]

    @property
    def not_screened(self) -> list[ArtifactRisk]:
        return [risk for risk, item in self.items.items() if item.result is ScreeningResult.NOT_RUN]


class NoveltyVsCoverage(ImmutableContract):
    knn: int = Field(ge=1)
    reference_coverage_score: Optional[float] = None
    distance_ratio: Optional[float] = None
    coverage_gap: bool = False
    novelty_signal: bool = False
    note: Optional[str] = None


class ContinuousStateCheck(ImmutableContract):
    method: str
    is_axis_tail: Optional[bool] = None
    statistic: Optional[float] = None


class CompositionCheck(ImmutableContract):
    method: Optional[str] = None
    p_value: Optional[float] = Field(None, ge=0, le=1)
    fdr_adjusted: Optional[float] = Field(None, ge=0, le=1)
    multiplicity_strategy: Optional[str] = None
    result: Optional[str] = None


class IdentityVsState(ImmutableContract):
    identity_claim: Optional[str] = None
    state_claim: Optional[str] = None
    combined_expression: Optional[str] = None
    continuous_state_check: Optional[ContinuousStateCheck] = None


class EvidenceSource(ImmutableContract):
    claim_id: Optional[str] = None
    dataset_id: Optional[str] = None
    version: Optional[str] = None
    run_id: Optional[str] = None
    asset_id: Optional[str] = None
    doi: Optional[str] = None
    pmid: Optional[str] = None
    locator: Optional[str] = None
    license_class: Optional[str] = None
    usage_scope: Optional[str] = None

    @model_validator(mode="after")
    def _requires_one_identifier(self) -> "EvidenceSource":
        if not any([self.claim_id, self.dataset_id, self.run_id, self.asset_id, self.doi, self.pmid]):
            raise ValueError("evidence source needs at least one identifier")
        if (self.doi or self.pmid) and not self.locator:
            raise ValueError("literature evidence requires a locator (page/figure/paragraph)")
        if self.dataset_id and not self.version:
            raise ValueError("reference dataset evidence must pin the dataset version")
        return self


class Evidence(Contract):
    evidence_id: str
    kind: EvidenceKind
    role: EvidenceRole
    partition: EvidencePartition
    source: EvidenceSource
    provenance: FieldProvenance
    independence_group: Optional[str] = None
    used_cell_set_fingerprint: Optional[str] = None
    content: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _coerce_role_partition(self) -> "Evidence":
        if self.role is EvidenceRole.TRIGGER and self.partition is not EvidencePartition.DISCOVERY:
            raise ValueError("trigger signals are discovered on the discovery partition by definition")
        return self


class EvidenceCard(Contract):
    card_id: str
    candidate_id: str
    run_id: str
    display_name: str
    candidate_types: list[CandidateType]
    hypothesis_type: HypothesisType
    unknown_reason: Optional[UnknownReason] = None
    candidate_label: Optional[str] = None
    label_level: LabelLevel
    ontology: OntologyRef = Field(default_factory=OntologyRef)
    identity_vs_state: IdentityVsState = Field(default_factory=IdentityVsState)
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    confirmation_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    trigger_signal_ids: list[str] = Field(default_factory=list)
    unresolved_conflict: bool = False
    artifact_screening: Optional[ArtifactScreening] = None
    novelty_vs_coverage: Optional[NoveltyVsCoverage] = None
    stability_summary: Optional[StabilitySummary] = None
    composition_check: CompositionCheck = Field(default_factory=CompositionCheck)
    cross_modal_status: CrossModalStatus = CrossModalStatus.NOT_YET_VALIDATED
    field_provenance: dict[str, FieldProvenance] = Field(default_factory=dict)
    support_rule_version: str
    thresholds_version: str
    final_status: FinalStatus
    status_reason: Optional[str] = None
    reviewer_id: Optional[str] = None
    review_reason: Optional[str] = None
    sign_off: SignOff = SignOff.NOT_SIGNED
    reviewed_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _status_matches_hypothesis(self) -> "EvidenceCard":
        if self.hypothesis_type is HypothesisType.UNKNOWN:
            if self.unknown_reason is None:
                raise ValueError("unknown hypothesis requires unknown_reason")
            if self.candidate_label:
                raise ValueError("unknown hypothesis must not carry a candidate label")
        elif self.final_status in (FinalStatus.SUPPORTED, FinalStatus.PROVISIONAL) and not self.candidate_label:
            raise ValueError(f"{self.final_status} cards require a candidate_label")
        return self

    @model_validator(mode="after")
    def _label_level_matches_ontology(self) -> "EvidenceCard":
        if self.label_level is LabelLevel.PROVISIONAL_PROJECT and self.ontology.term_id:
            raise ValueError("provisional project labels must not carry an ontology id")
        if self.label_level in (LabelLevel.CELL_TYPE_BROAD, LabelLevel.CELL_TYPE_FINE) and not self.ontology.term_id:
            raise ValueError("cell type labels require a Cell Ontology reference")
        return self

    @model_validator(mode="after")
    def _evidence_references_resolve(self) -> "EvidenceCard":
        declared = set(self.evidence)
        referenced = set(self.supporting_evidence_ids) | set(self.contradicting_evidence_ids) | set(
            self.confirmation_evidence_ids
        ) | set(self.trigger_signal_ids)
        dangling = sorted(referenced - declared)
        if dangling:
            raise ValueError(f"evidence ids not present in card.evidence: {dangling}")
        if not set(self.confirmation_evidence_ids) <= set(self.supporting_evidence_ids):
            raise ValueError("confirmation evidence must also be listed as supporting evidence")
        for eid in self.confirmation_evidence_ids:
            if self.evidence[eid].partition is not EvidencePartition.CONFIRMATION:
                raise ValueError(f"{eid} is listed as confirmation but partition is {self.evidence[eid].partition}")
        for eid in self.trigger_signal_ids:
            if self.evidence[eid].role is not EvidenceRole.TRIGGER:
                raise ValueError(f"{eid} must have role=trigger to be a trigger signal")
        return self

    @model_validator(mode="after")
    def _supported_needs_signature(self) -> "EvidenceCard":
        if self.final_status is FinalStatus.SUPPORTED and not (
            self.reviewer_id and self.reviewed_at and self.sign_off is not SignOff.NOT_SIGNED
        ):
            raise ValueError("supported cards require reviewer_id, reviewed_at and a sign-off")
        if self.final_status is FinalStatus.CONFLICT and not self.contradicting_evidence_ids:
            raise ValueError("conflict cards must list contradicting evidence")
        return self

    @model_validator(mode="after")
    def _trigger_are_not_support(self) -> "EvidenceCard":
        overlap = set(self.trigger_signal_ids) & set(self.supporting_evidence_ids)
        if overlap:
            raise ValueError(f"trigger signals cannot be listed as supporting evidence: {sorted(overlap)}")
        return self
