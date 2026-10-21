"""Tests for multi-omics cell barcode pairing and donor alignment."""

import pytest
from bioadapt_multiomics.entities import ModalityKind
from bioadapt_multiomics.pairing import pair_modalities


def test_pairing_success_with_matching_donors():
    rna_barcodes = ["bc_01", "bc_02", "bc_03", "bc_04"]
    rna_donors = {"bc_01": "donor_A", "bc_02": "donor_A", "bc_03": "donor_B", "bc_04": "donor_B"}

    adt_barcodes = ["bc_02", "bc_03", "bc_04", "bc_05"]
    adt_donors = {"bc_02": "donor_A", "bc_03": "donor_B", "bc_04": "donor_B", "bc_05": "donor_C"}

    paired = pair_modalities(
        project_id="proj_paired_01",
        run_id="run_mo_01",
        rna_barcodes=rna_barcodes,
        rna_donors=rna_donors,
        other_barcodes=adt_barcodes,
        other_donors=adt_donors,
        other_modality=ModalityKind.PROTEIN,
        min_alignment_rate=0.50,
    )

    assert paired.aligned_barcodes == ["bc_02", "bc_03", "bc_04"]
    assert paired.total_rna_cells == 4
    assert paired.alignment_rate == 0.75  # 3 common out of max(4, 4) = 4
    assert paired.donor_map["bc_02"] == "donor_A"


def test_pairing_fails_on_donor_conflict():
    rna_barcodes = ["bc_01"]
    rna_donors = {"bc_01": "donor_A"}

    atac_barcodes = ["bc_01"]
    atac_donors = {"bc_01": "donor_B"}  # Same cell barcode claimed by different donors!

    with pytest.raises(ValueError, match="Donor discrepancy"):
        pair_modalities(
            project_id="proj_paired_02",
            run_id="run_mo_02",
            rna_barcodes=rna_barcodes,
            rna_donors=rna_donors,
            other_barcodes=atac_barcodes,
            other_donors=atac_donors,
            other_modality=ModalityKind.ATAC,
        )


def test_pairing_fails_on_low_alignment_rate():
    rna_barcodes = [f"rna_bc_{i}" for i in range(100)]
    other_barcodes = [f"other_bc_{i}" for i in range(100)]
    # 0 overlap
    with pytest.raises(ValueError, match="below required"):
        pair_modalities(
            project_id="proj_low_align",
            run_id="run_mo_03",
            rna_barcodes=rna_barcodes,
            rna_donors={},
            other_barcodes=other_barcodes,
            other_donors={},
            min_alignment_rate=0.30,
        )
