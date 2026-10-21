"""Cross-modal concordance and conflict visibility engine.

Strictly adheres to Plan V2.0 Section 7.5:
- When RNA and Protein (CITE-seq) or Chromatin (ATAC) disagree, the platform MUST explicitly
  record the contradiction.
- Strictly prohibit combining discordant signals into a single weighted score that obscures the disagreement.
- Generates `CrossModalObservation` records for evidence cards.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from .entities import CrossModalObservation, ModalityAgreement, ModalityKind


def _rank_vector(values: Sequence[float]) -> list[float]:
    """Compute fractional ranks for Spearman correlation."""
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    n = len(values)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and math.isclose(indexed[j + 1][1], indexed[i][1], abs_tol=1e-7):
            j += 1
        avg_rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def compute_spearman_rho(x: Sequence[float], y: Sequence[float]) -> float:
    """Compute Spearman's rank correlation coefficient between two numeric vectors."""
    if len(x) != len(y) or len(x) < 3:
        return 0.0
    rx = _rank_vector(x)
    ry = _rank_vector(y)
    n = len(x)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n

    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((rx[i] - mean_rx) ** 2 for i in range(n))
    var_y = sum((ry[i] - mean_ry) ** 2 for i in range(n))

    if var_x == 0.0 or var_y == 0.0:
        return 0.0
    return float(cov / math.sqrt(var_x * var_y))


class CrossModalEvaluator:
    """Evaluates concordance between transcriptomic (RNA) and orthogonal modalities (ADT/ATAC)."""

    def __init__(
        self,
        min_rna_threshold: float = 0.5,
        min_target_threshold: float = 0.5,
        concordance_rho_cutoff: float = 0.35,
    ) -> None:
        self.min_rna_thresh = min_rna_threshold
        self.min_target_thresh = min_target_threshold
        self.concordance_rho = concordance_rho_cutoff

    def evaluate_feature(
        self,
        gene_symbol: str,
        feature_name: str,
        target_modality: ModalityKind,
        cluster_cell_indices: Sequence[int],
        rna_values: Sequence[float],
        target_values: Sequence[float],
    ) -> CrossModalObservation:
        """Compare RNA expression against protein or chromatin values for a cluster's cells."""
        if not cluster_cell_indices:
            return CrossModalObservation(
                gene_symbol=gene_symbol,
                feature_name=feature_name,
                target_modality=target_modality,
                rna_expression_mean=0.0,
                target_modality_mean=0.0,
                spearman_rho=0.0,
                p_value=1.0,
                agreement=ModalityAgreement.AMBIGUOUS,
                conflict_detected=False,
            )

        sub_rna = [rna_values[idx] for idx in cluster_cell_indices]
        sub_target = [target_values[idx] for idx in cluster_cell_indices]

        mean_rna = float(sum(sub_rna) / len(sub_rna))
        mean_target = float(sum(sub_target) / len(sub_target))
        rho = compute_spearman_rho(sub_rna, sub_target)

        # Approximate p-value for Spearman rho
        n = len(sub_rna)
        t_stat = rho * math.sqrt(max(0.001, (n - 2) / (1.0 - min(0.999, rho * rho))))
        # Heuristic 2-tailed p-value proxy
        p_val = math.exp(-0.5 * (t_stat * t_stat)) if n >= 5 else 0.5

        # Conflict Detection (Plan V2.0 section 7.5):
        # Case A: High RNA expression but negative target modality (e.g. mRNA present, protein completely absent)
        # Case B: Significant negative correlation where positive co-expression was required
        conflict = False
        conflict_rationale: str | None = None
        agreement = ModalityAgreement.CONCORDANT

        if mean_rna >= self.min_rna_thresh and mean_target < (self.min_target_thresh * 0.4):
            conflict = True
            agreement = ModalityAgreement.DISCORDANT
            conflict_rationale = (
                f"Modality conflict for {gene_symbol}: RNA expression is elevated (mean={mean_rna:.2f}), "
                f"but {target_modality.value.upper()} signal is absent or suppressed (mean={mean_target:.2f}). "
                "Possible post-transcriptional repression or technical artifact."
            )
        elif mean_rna < (self.min_rna_thresh * 0.4) and mean_target >= self.min_target_thresh:
            conflict = True
            agreement = ModalityAgreement.DISCORDANT
            conflict_rationale = (
                f"Modality conflict for {gene_symbol}: {target_modality.value.upper()} signal is abundant (mean={mean_target:.2f}), "
                f"but RNA transcription is undetectable (mean={mean_rna:.2f}). "
                "Possible stable surface protein retention or ambient antibody capture."
            )
        elif rho < -0.30 and p_val < 0.05:
            conflict = True
            agreement = ModalityAgreement.DISCORDANT
            conflict_rationale = (
                f"Significant inverse correlation detected for {gene_symbol} between RNA and {target_modality.value.upper()} "
                f"(rho={rho:.2f}, p={p_val:.4f}). Signals actively contradict co-linear hypothesis."
            )
        elif rho >= self.concordance_rho or (mean_rna >= self.min_rna_thresh and mean_target >= self.min_target_thresh):
            agreement = ModalityAgreement.CONCORDANT
        else:
            agreement = ModalityAgreement.AMBIGUOUS

        return CrossModalObservation(
            gene_symbol=gene_symbol,
            feature_name=feature_name,
            target_modality=target_modality,
            rna_expression_mean=round(mean_rna, 4),
            target_modality_mean=round(mean_target, 4),
            spearman_rho=round(rho, 4),
            p_value=round(p_val, 5),
            agreement=agreement,
            conflict_detected=conflict,
            conflict_rationale=conflict_rationale,
        )
