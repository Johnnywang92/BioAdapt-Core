"""Tests for modality conflict visibility in EvidenceCards (plan V2.0 section 7.5)."""

from bioadapt_schemas.entities import CandidateSet
from bioadapt_schemas.enums import CandidateType, FinalStatus
from bioadapt_schemas.identifiers import derive_candidate

from bioadapt_multiomics.builder import synthesize_multiomics_card
from bioadapt_multiomics.entities import (
    CrossModalObservation,
    ModalityAgreement,
    ModalityKind,
    MultiOmicsClusterReport,
)


def test_conflict_visibility_in_evidence_card():
    # Setup candidate
    cells = [("spc_01", "AAAC"), ("spc_01", "TTGG"), ("spc_02", "GGCT")] * 10
    cand_id, fgp, size = derive_candidate(cells, "run_mo_10")
    candidate_set = CandidateSet(
        candidate_id=cand_id,
        run_id="run_mo_10",
        member_fingerprint=fgp,
        size=size,
        donor_count=2,
        display_name="cluster_conflict_demo",
        cluster_origin={"method": "wnn", "resolution": 0.8},
        candidate_types=[CandidateType.C4_CROSS_MODAL_DISCORDANT],
    )

    # Observation 1: Concordant CD3D
    obs_cd3 = CrossModalObservation(
        gene_symbol="CD3D",
        feature_name="CD3_TotalSeq",
        target_modality=ModalityKind.PROTEIN,
        rna_expression_mean=3.2,
        target_modality_mean=4.1,
        spearman_rho=0.65,
        p_value=0.001,
        agreement=ModalityAgreement.CONCORDANT,
        conflict_detected=False,
    )

    # Observation 2: Conflicting CD4 (high RNA, but absent ADT)
    obs_cd4 = CrossModalObservation(
        gene_symbol="CD4",
        feature_name="CD4_TotalSeq",
        target_modality=ModalityKind.PROTEIN,
        rna_expression_mean=3.8,
        target_modality_mean=0.08,
        spearman_rho=-0.42,
        p_value=0.01,
        agreement=ModalityAgreement.DISCORDANT,
        conflict_detected=True,
        conflict_rationale="High CD4 RNA but negative surface protein",
    )

    report = MultiOmicsClusterReport(
        cluster_id="cluster_conflict_demo",
        cell_count=size,
        donor_count=2,
        candidate_type=CandidateType.C4_CROSS_MODAL_DISCORDANT,
        modalities_evaluated=[ModalityKind.RNA, ModalityKind.PROTEIN],
        cross_modal_observations=[obs_cd3, obs_cd4],
        has_unresolved_conflict=True,
        conflict_summary="CD4 expression contradicts surface ADT phenotype",
    )

    card = synthesize_multiomics_card(
        run_id="run_mo_10",
        asset_id="as_paired_h5ad",
        report=report,
        candidate_set=candidate_set,
        candidate_label="T cell subpopulation with CD4 discordance",
    )

    # Assertions guaranteeing Plan V2.0 section 7.5 compliance:
    assert card.unresolved_conflict is True
    assert card.final_status == FinalStatus.CONFLICT
    assert len(card.contradicting_evidence_ids) >= 1
    assert any("cd4" in cid for cid in card.contradicting_evidence_ids)
    assert len(card.supporting_evidence_ids) >= 1
    assert any("cd3d" in sid for sid in card.supporting_evidence_ids)

    # Conflict must be visible in contradicting evidence item
    contra_ev = card.evidence[card.contradicting_evidence_ids[0]]
    assert contra_ev.content["conflict_detected"] is True
    assert "High CD4 RNA" in contra_ev.content["conflict_rationale"]
