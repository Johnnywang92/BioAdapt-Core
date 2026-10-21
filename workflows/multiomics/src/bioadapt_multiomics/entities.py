"""Data contracts and schemas for multi-omics integration and conflict detection.

Adheres to:
- Plan V2.0 section 7.2: C4 (continuous state) and C5 (cross-modal) candidate discovery.
- Plan V2.0 section 7.5: Modality conflict visibility (strictly prohibit obscuring conflict with weighted sum).
- Plan V2.0 section 12.3: Go/No-go gate 3 -> 4 (pairing correctness, independent lineage tracking).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Optional
from pydantic import Field

from bioadapt_schemas.entities import ImmutableContract
from bioadapt_schemas.enums import CandidateType, EvidenceKind


class ModalityKind(StrEnum):
    RNA = "rna"
    ATAC = "atac"
    PROTEIN = "protein"  # ADT / CITE-seq


class ModalityAgreement(StrEnum):
    CONCORDANT = "concordant"        # RNA and Protein/ATAC agree strongly
    DISCORDANT = "discordant"        # RNA and Protein/ATAC contradict (Conflict)
    COMPLEMENTARY = "complementary"  # One modality provides resolution where other is silent
    AMBIGUOUS = "ambiguous"          # Low coverage or noisy signal


class CrossModalObservation(ImmutableContract):
    """Observation comparing RNA transcription with protein or chromatin accessibility."""

    gene_symbol: str
    feature_name: str
    target_modality: ModalityKind
    rna_expression_mean: float
    target_modality_mean: float
    spearman_rho: float
    p_value: float
    agreement: ModalityAgreement
    conflict_detected: bool = False
    conflict_rationale: Optional[str] = None


class ContinuousStateTrajectory(ImmutableContract):
    """Pseudotime / differentiation axis analysis for C4 candidates (section 7.2)."""

    axis_name: str
    pseudotime_start: float
    pseudotime_end: float
    is_terminal_state: bool
    branch_divergence_p: float
    novelty_vs_gradient: str  # "distinct_cluster" vs "gradient_tail"


class MultiOmicsClusterReport(ImmutableContract):
    """Integrated report for a cluster across RNA, ATAC, and/or ADT modalities."""

    cluster_id: str
    cell_count: int
    donor_count: int
    candidate_type: CandidateType
    modalities_evaluated: list[ModalityKind]
    cross_modal_observations: list[CrossModalObservation] = Field(default_factory=list)
    continuous_state: Optional[ContinuousStateTrajectory] = None
    has_unresolved_conflict: bool = False
    conflict_summary: Optional[str] = None
