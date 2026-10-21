"""Tests for C4 continuous state trajectory and C5 cross-modal candidate discovery."""

from bioadapt_schemas.enums import CandidateType

from bioadapt_multiomics.novelty import classify_multiomics_candidate, evaluate_continuous_state


def test_evaluate_continuous_state_terminal_gradient():
    # Pseudotime scale [0.0 - 1.0]. A cluster tightly located at 0.90 - 0.98 is a terminal gradient
    pt_values = [0.1 * i for i in range(10)] + [0.92, 0.95, 0.94, 0.98, 0.96]
    cluster_indices = [10, 11, 12, 13, 14]

    traj = evaluate_continuous_state(
        pseudotime_values=pt_values,
        cluster_cell_indices=cluster_indices,
        axis_name="CD8_Exhaustion_Axis",
        gradient_tail_threshold=0.85,
    )

    assert traj.is_terminal_state is True
    assert traj.novelty_vs_gradient == "gradient_tail"
    assert traj.pseudotime_start >= 0.90


def test_classify_candidate_as_c4():
    pt_values = [0.92, 0.95, 0.94, 0.98]
    cluster_indices = [0, 1, 2, 3]
    traj = evaluate_continuous_state(pt_values, cluster_indices)

    cand_type = classify_multiomics_candidate(
        cluster_id="cluster_exhausted_t",
        cell_barcodes=["bc1", "bc2", "bc3", "bc4"],
        barcode_to_donor={"bc1": "d1", "bc2": "d1", "bc3": "d2", "bc4": "d2"},
        is_unimodal_separable=True,
        is_multimodal_separable=True,
        has_cross_modal_conflict=True,
        continuous_trajectory=traj,
    )

    assert cand_type == CandidateType.C4_CROSS_MODAL_DISCORDANT


def test_classify_candidate_as_c5():
    # Not separable in unimodal RNA alone, but clearly separated in multi-omics
    cand_type = classify_multiomics_candidate(
        cluster_id="cluster_wnn_hidden",
        cell_barcodes=["bc1", "bc2", "bc3", "bc4"],
        barcode_to_donor={"bc1": "d1", "bc2": "d1", "bc3": "d2", "bc4": "d2"},
        is_unimodal_separable=False,  # RNA alone cannot separate
        is_multimodal_separable=True,   # Multi-modal WNN separates cleanly
        has_cross_modal_conflict=False,
        continuous_trajectory=None,
    )

    assert cand_type == CandidateType.C5_COMPOSITION_OUTLIER
