"""Builds standardized Evidence and EvidenceCard from multi-omics analysis results.

Adheres to:
- Plan V2.0 section 7.5: Modality conflict visibility in evidence cards.
- Evidence contracts from bioadapt_schemas.evidence.
"""

from __future__ import annotations

import hashlib
from typing import Mapping, Sequence

from bioadapt_schemas.entities import CandidateSet
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
    SignOff,
)
from bioadapt_schemas.evidence import (
    ArtifactScreening,
    ContinuousStateCheck,
    Evidence,
    EvidenceCard,
    EvidenceSource,
    FieldProvenance,
    IdentityVsState,
    OntologyRef,
    StabilitySummary,
)
from bioadapt_schemas.identifiers import derive_candidate

from .entities import CrossModalObservation, MultiOmicsClusterReport


def build_cross_modal_evidence(
    run_id: str,
    asset_id: str,
    cluster_id: str,
    observations: Sequence[CrossModalObservation],
) -> tuple[list[Evidence], list[str], list[str]]:
    """Transform cross-modal observations into positive and contradicting Evidence items.

    Returns:
        (evidence_list, supporting_ids, contradicting_ids)
    """
    evidence_items: list[Evidence] = []
    supporting_ids: list[str] = []
    contradicting_ids: list[str] = []

    for i, obs in enumerate(observations):
        ev_id = f"ev_cm_{cluster_id}_{obs.gene_symbol.lower()}_{i:02d}"
        role = EvidenceRole.CONTRADICTING if obs.conflict_detected else EvidenceRole.SUPPORTING
        
        content = {
            "gene_symbol": obs.gene_symbol,
            "target_modality": obs.target_modality.value,
            "rna_expression_mean": obs.rna_expression_mean,
            "target_modality_mean": obs.target_modality_mean,
            "spearman_rho": obs.spearman_rho,
            "p_value": obs.p_value,
            "agreement": obs.agreement.value,
            "conflict_detected": obs.conflict_detected,
            "conflict_rationale": obs.conflict_rationale,
        }

        ev = Evidence(
            evidence_id=ev_id,
            kind=EvidenceKind.CROSS_MODAL,
            role=role,
            partition=EvidencePartition.DISCOVERY,
            source=EvidenceSource(run_id=run_id, asset_id=asset_id),
            provenance=FieldProvenance(
                source=FieldSource.TOOL,
                object_id=asset_id,
                note=f"Cross-modal integration {obs.target_modality.value.upper()}",
            ),
            independence_group=f"multiomics_{obs.target_modality.value}",
            used_cell_set_fingerprint=f"fgp_{cluster_id}",
            content=content,
        )

        evidence_items.append(ev)
        if obs.conflict_detected:
            contradicting_ids.append(ev_id)
        else:
            supporting_ids.append(ev_id)

    return evidence_items, supporting_ids, contradicting_ids


def synthesize_multiomics_card(
    run_id: str,
    asset_id: str,
    report: MultiOmicsClusterReport,
    candidate_set: CandidateSet,
    candidate_label: str = "Unassigned Multi-omics Cluster",
    support_rule_version: str = "support_rules_v1",
    thresholds_version: str = "default_v1",
) -> EvidenceCard:
    """Synthesize an integrated EvidenceCard enforcing modality conflict visibility."""
    ev_items, supp_ids, contra_ids = build_cross_modal_evidence(
        run_id=run_id,
        asset_id=asset_id,
        cluster_id=report.cluster_id,
        observations=report.cross_modal_observations,
    )

    ev_dict = {ev.evidence_id: ev for ev in ev_items}
    has_conflict = len(contra_ids) > 0 or report.has_unresolved_conflict

    # Plan V2.0 section 7.5: A card with unresolved cross-modal conflict cannot reach provisional or supported
    final_status = FinalStatus.CONFLICT if has_conflict else FinalStatus.UNKNOWN
    cross_modal_status = CrossModalStatus.DISCORDANT if has_conflict else (
        CrossModalStatus.CONSISTENT if supp_ids else CrossModalStatus.NOT_YET_VALIDATED
    )
    status_reason = (
        report.conflict_summary or "Cross-modal conflict detected between transcriptomic and orthogonal signals"
        if has_conflict
        else "Multi-omics initial candidate pending domain expert review"
    )

    is_continuous = bool(report.continuous_state and report.continuous_state.is_terminal_state)
    identity_claim = candidate_label if not is_continuous else None
    state_claim = (
        f"Continuous gradient on {report.continuous_state.axis_name}"
        if is_continuous and report.continuous_state
        else None
    )

    continuous_check = (
        ContinuousStateCheck(
            method="pseudotime_diffusion",
            is_axis_tail=report.continuous_state.is_terminal_state,
            statistic=report.continuous_state.branch_divergence_p,
        )
        if report.continuous_state
        else None
    )

    card = EvidenceCard(
        card_id=f"card_mo_{report.cluster_id}",
        candidate_id=candidate_set.candidate_id,
        run_id=run_id,
        display_name=f"{report.cluster_id} (Multi-omics)",
        candidate_types=[report.candidate_type],
        hypothesis_type=HypothesisType.CONTINUOUS_STATE if is_continuous else HypothesisType.CELL_TYPE,
        candidate_label=candidate_label,
        ontology=OntologyRef(term_id=None, ontology_name=None, ontology_version=None, mapping_source="multiomics_pipeline"),
        label_level=LabelLevel.PROVISIONAL_PROJECT,
        identity_vs_state=IdentityVsState(
            identity_claim=identity_claim,
            state_claim=state_claim,
            continuous_state_check=continuous_check,
        ),
        evidence=ev_dict,
        supporting_evidence_ids=supp_ids,
        confirmation_evidence_ids=[],
        contradicting_evidence_ids=contra_ids,
        unresolved_conflict=has_conflict,
        cross_modal_status=cross_modal_status,
        final_status=final_status,
        status_reason=status_reason,
        sign_off=SignOff.NOT_SIGNED,
        field_provenance={
            "evidence.*": FieldProvenance(source=FieldSource.TOOL, object_id=asset_id),
            "identity_vs_state.*": FieldProvenance(source=FieldSource.TOOL, object_id=asset_id),
        },
        support_rule_version=support_rule_version,
        thresholds_version=thresholds_version,
    )

    return card
