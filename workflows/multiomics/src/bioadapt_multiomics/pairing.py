"""Cell pairing and alignment validation across omics modalities (plan V2.0 section 7.5, 12.3)."""

from __future__ import annotations

from typing import Sequence
from pydantic import Field

from bioadapt_schemas.entities import ImmutableContract
from .entities import ModalityKind


class PairedOmicsDataset(ImmutableContract):
    """Holds aligned cell barcodes and aligned matrix values across modalities."""

    project_id: str
    run_id: str
    modalities: list[ModalityKind]
    aligned_barcodes: list[str]
    donor_map: dict[str, str]  # barcode -> donor_id
    total_rna_cells: int
    total_other_cells: int
    unpaired_dropped_cells: int
    alignment_rate: float


def pair_modalities(
    project_id: str,
    run_id: str,
    rna_barcodes: Sequence[str],
    rna_donors: dict[str, str],
    other_barcodes: Sequence[str],
    other_donors: dict[str, str],
    other_modality: ModalityKind = ModalityKind.PROTEIN,
    min_alignment_rate: float = 0.50,
) -> PairedOmicsDataset:
    """Validate and align cells between RNA and a secondary modality (ATAC or ADT).

    Raises:
        ValueError if the alignment rate between modalities is below threshold or donor maps conflict.
    """
    rna_set = set(rna_barcodes)
    other_set = set(other_barcodes)
    common_barcodes = sorted(rna_set & other_set)

    total_cells = max(len(rna_set), len(other_set))
    if total_cells == 0:
        raise ValueError("Cannot pair empty modalities")

    alignment_rate = len(common_barcodes) / total_cells
    if alignment_rate < min_alignment_rate:
        raise ValueError(
            f"Multi-omics cell pairing failed: alignment rate {alignment_rate:.2%} is below required {min_alignment_rate:.2%}"
        )

    # Validate donor identity consistency across modalities
    donor_map: dict[str, str] = {}
    for bc in common_barcodes:
        d1 = rna_donors.get(bc)
        d2 = other_donors.get(bc)
        if d1 and d2 and d1 != d2:
            raise ValueError(
                f"Donor discrepancy for cell barcode {bc}: RNA says {d1}, while {other_modality.value} says {d2}"
            )
        donor_map[bc] = d1 or d2 or "unknown_donor"

    return PairedOmicsDataset(
        project_id=project_id,
        run_id=run_id,
        modalities=[ModalityKind.RNA, other_modality],
        aligned_barcodes=common_barcodes,
        donor_map=donor_map,
        total_rna_cells=len(rna_set),
        total_other_cells=len(other_set),
        unpaired_dropped_cells=total_cells - len(common_barcodes),
        alignment_rate=round(alignment_rate, 4),
    )
