"""Tests for CrossModalEvaluator and Spearman correlation."""

from bioadapt_multiomics.cross_modal import CrossModalEvaluator, compute_spearman_rho
from bioadapt_multiomics.entities import ModalityAgreement, ModalityKind


def test_spearman_rho_calculation():
    # Identical ranking
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [10.0, 20.0, 30.0, 40.0, 50.0]
    rho = compute_spearman_rho(x, y)
    assert abs(rho - 1.0) < 1e-5

    # Inverted ranking
    y_rev = [50.0, 40.0, 30.0, 20.0, 10.0]
    rho_rev = compute_spearman_rho(x, y_rev)
    assert abs(rho_rev - (-1.0)) < 1e-5


def test_concordant_evaluation():
    evaluator = CrossModalEvaluator()
    cluster_indices = [0, 1, 2, 3]
    rna_vals = [2.5, 3.1, 2.8, 3.0]
    adt_vals = [4.2, 5.0, 4.8, 5.1]  # Both elevated

    obs = evaluator.evaluate_feature(
        gene_symbol="CD4",
        feature_name="CD4_TotalSeqC",
        target_modality=ModalityKind.PROTEIN,
        cluster_cell_indices=cluster_indices,
        rna_values=rna_vals,
        target_values=adt_vals,
    )

    assert obs.agreement == ModalityAgreement.CONCORDANT
    assert obs.conflict_detected is False
    assert obs.rna_expression_mean > 2.0
    assert obs.target_modality_mean > 4.0


def test_conflict_detection_when_protein_absent():
    evaluator = CrossModalEvaluator()
    cluster_indices = [0, 1, 2, 3]
    rna_vals = [3.5, 4.0, 3.8, 4.2]  # Elevated mRNA
    adt_vals = [0.05, 0.02, 0.08, 0.01]  # Near zero protein

    obs = evaluator.evaluate_feature(
        gene_symbol="FOXP3",
        feature_name="FOXP3_Intracellular_ADT",
        target_modality=ModalityKind.PROTEIN,
        cluster_cell_indices=cluster_indices,
        rna_values=rna_vals,
        target_values=adt_vals,
    )

    assert obs.agreement == ModalityAgreement.DISCORDANT
    assert obs.conflict_detected is True
    assert "Modality conflict" in (obs.conflict_rationale or "")
    assert "RNA expression is elevated" in (obs.conflict_rationale or "")
