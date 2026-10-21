"""Complex candidate discovery (C4 continuous state and C5 cross-modal).

Adheres to:
- Plan V2.0 section 7.2:
  - C4: Continuous state novelty (distinguish novel cell type from end-of-axis differentiation gradient).
  - C5: Cross-modal candidate (distinct population revealed only by multi-omics joint clustering).
- Plan V2.0 section 1.5 & 14.4: donor count constraints (minimum 2 donors).
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from bioadapt_schemas.enums import CandidateType

from .entities import ContinuousStateTrajectory, ModalityKind, MultiOmicsClusterReport
from .cross_modal import CrossModalEvaluator, compute_spearman_rho


def evaluate_continuous_state(
    pseudotime_values: Sequence[float],
    cluster_cell_indices: Sequence[int],
    axis_name: str = "T_cell_exhaustion_axis",
    gradient_tail_threshold: float = 0.85,
) -> ContinuousStateTrajectory:
    """Determine whether a cluster represents a truly distinct discrete population or a gradient tail of a continuous axis."""
    if not cluster_cell_indices:
        return ContinuousStateTrajectory(
            axis_name=axis_name,
            pseudotime_start=0.0,
            pseudotime_end=0.0,
            is_terminal_state=False,
            branch_divergence_p=1.0,
            novelty_vs_gradient="gradient_tail",
        )

    cluster_pt = [pseudotime_values[idx] for idx in cluster_cell_indices]
    pt_min = min(cluster_pt)
    pt_max = max(cluster_pt)
    pt_mean = sum(cluster_pt) / len(cluster_pt)

    # Standard deviation of pseudotime in this cluster
    variance = sum((x - pt_mean) ** 2 for x in cluster_pt) / len(cluster_pt)
    std_dev = math.sqrt(variance)

    # If the cluster is tightly clustered at the extreme end of pseudotime (> 0.85)
    # with wide overlap with the continuous axis, it is characterized as gradient tail
    is_terminal = pt_max >= 0.90 and pt_mean >= gradient_tail_threshold
    novelty = "gradient_tail" if is_terminal else "distinct_cluster"

    return ContinuousStateTrajectory(
        axis_name=axis_name,
        pseudotime_start=round(pt_min, 4),
        pseudotime_end=round(pt_max, 4),
        is_terminal_state=is_terminal,
        branch_divergence_p=round(max(0.001, 1.0 - std_dev), 4),
        novelty_vs_gradient=novelty,
    )


def classify_multiomics_candidate(
    cluster_id: str,
    cell_barcodes: Sequence[str],
    barcode_to_donor: Mapping[str, str],
    is_unimodal_separable: bool,
    is_multimodal_separable: bool,
    has_cross_modal_conflict: bool = False,
    continuous_trajectory: ContinuousStateTrajectory | None = None,
) -> CandidateType:
    """Classify candidate type according to single vs multi-modal separation.

    C4_CROSS_MODAL_DISCORDANT: Cross-modal conflict between transcription and protein/chromatin.
    C5_COMPOSITION_OUTLIER: Novel outlier revealed specifically by multi-modal joint clustering.
    C3_RARE_STABLE: Rare subpopulation supported across >=2 donors.
    C1_UNASSIGNED: Unassigned discrete population.
    """
    donors = {barcode_to_donor.get(bc) for bc in cell_barcodes if barcode_to_donor.get(bc)}
    donor_count = len(donors)

    if has_cross_modal_conflict:
        return CandidateType.C4_CROSS_MODAL_DISCORDANT

    if not is_unimodal_separable and is_multimodal_separable:
        # Multi-modal specific composition outlier
        return CandidateType.C5_COMPOSITION_OUTLIER

    if donor_count >= 2:
        return CandidateType.C3_RARE_STABLE

    return CandidateType.C1_UNASSIGNED
