"""BioAdapt Multi-Omics - Cross-modal integration, pairing, and conflict visibility.

Adheres to:
- Plan V2.0 Section 7.2: C4 (continuous state) & C5 (cross-modal) candidate discovery.
- Plan V2.0 Section 7.5: Modality conflict visibility and strict prevention of conflict obscuration.
- Plan V2.0 Section 12.3: Go/No-go gate 3 -> 4.
"""

from .builder import build_cross_modal_evidence, synthesize_multiomics_card
from .cross_modal import CrossModalEvaluator, compute_spearman_rho
from .entities import (
    ContinuousStateTrajectory,
    CrossModalObservation,
    ModalityAgreement,
    ModalityKind,
    MultiOmicsClusterReport,
)
from .novelty import classify_multiomics_candidate, evaluate_continuous_state
from .pairing import PairedOmicsDataset, pair_modalities

__all__ = [
    "ContinuousStateTrajectory",
    "CrossModalEvaluator",
    "CrossModalObservation",
    "ModalityAgreement",
    "ModalityKind",
    "MultiOmicsClusterReport",
    "PairedOmicsDataset",
    "build_cross_modal_evidence",
    "classify_multiomics_candidate",
    "compute_spearman_rho",
    "evaluate_continuous_state",
    "pair_modalities",
    "synthesize_multiomics_card",
]
