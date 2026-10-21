"""The stored shape: canonical bytes, the count matrix, and what a conversion admits to dropping.

Section 10.1 promises byte-level reproducibility, and that promise is entirely about serialization -
keys sorted, floats quantized, no dependence on the order a caller happened to write its dictionary
in. Section 4.1 separately promises that a conversion which loses reductions or raw counts says so.
Neither is a step, so neither is exercised by ``test_steps.py``; both are checked here against the
numbers a reviewer could recompute from the asset alone (§5.2, §5.6).
"""

from __future__ import annotations

import json
import math

import pytest

from bioadapt_schemas.enums import GenomeIdSystem
from bioadapt_schemas.entities import Genome

from bioadapt_scrnaseq.codec import FLOAT_PRECISION, canonical_bytes, quantize, round_floats
from bioadapt_scrnaseq.matrix import (
    EMPTY_GENOME,
    LOSSY_CONVERSIONS,
    CellMeta,
    CountMatrix,
    ConversionReport,
    convert,
    genome_of,
    require_digest,
)

GENOME = Genome(
    build="gencode-v44",
    annotation_source="GENCODE",
    version="44",
    id_system=GenomeIdSystem.HGNC_SYMBOL,
)


def cell(key: str, *, donor: str, group: str, specimen: str = "spc_1") -> CellMeta:
    return CellMeta(cell_key=key, specimen_id=specimen, donor_id=donor, group=group)


def built(cells: list[CellMeta], counts: dict[str, dict[str, float]], *, genome: Genome = GENOME) -> CountMatrix:
    return CountMatrix.build(cells=cells, counts=counts, genome=genome)


#: A matrix small enough that every assertion below can be done by hand.
CELLS = [cell("c1", donor="donor_a", group="control"), cell("c2", donor="donor_b", group="case")]
COUNTS = {
    "c1": {"GAPDH": 100.0, "ACTB": 0.0, "MT-CO1": 5.0},
    "c2": {"GAPDH": 40.0, "RPL13A": 7.0},
}


@pytest.fixture(scope="module")
def matrix() -> CountMatrix:
    return built(CELLS, COUNTS)


# ------------------------------------------------------------------ canonical bytes (§10.1)


def test_the_order_a_payload_was_written_in_does_not_change_its_bytes() -> None:
    assert canonical_bytes({"b": 1, "a": 2}) == canonical_bytes({"a": 2, "b": 1})
    assert canonical_bytes([{"b": 1, "a": 2}]) == canonical_bytes([{"a": 2, "b": 1}])


def test_floats_are_stored_at_the_precision_a_reviewer_can_reproduce() -> None:
    assert canonical_bytes({"x": 0.1 + 0.2}) == b'{"x":0.3}'
    assert quantize(0.1 + 0.2) == 0.3


def test_a_value_that_rounds_to_zero_is_stored_as_zero_not_as_negative_zero() -> None:
    assert round_floats(-0.0) == 0.0
    assert json.loads(canonical_bytes({"x": -1e-12}))["x"] == 0.0


def test_a_product_holding_a_non_finite_number_is_refused_rather_than_stored() -> None:
    """JSON writes ``NaN`` and ``Infinity``, which no later reader can treat as a measurement."""
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError, match="NaN or infinity"):
            canonical_bytes({"x": value})


def test_rounding_reaches_every_number_in_a_nested_payload() -> None:
    rounded = round_floats({"rows": [[1.23456789, {"v": 2.34567891}]], "kept": "text", "n": 3})
    assert rounded["rows"][0][0] == 1.234568
    assert rounded["rows"][0][1]["v"] == 2.345679
    assert rounded["kept"] == "text" and rounded["n"] == 3


def test_the_quantized_number_is_the_number_the_stored_bytes_hold() -> None:
    """A fingerprint taken from full precision could not be recomputed from the asset (§5.6)."""
    value = 1.2345678901234
    assert json.loads(canonical_bytes([value]))[0] == quantize(value)
    assert quantize(value, ndigits=2) == 1.23


def test_the_stored_precision_is_named_not_assumed() -> None:
    assert FLOAT_PRECISION == 6
    assert quantize(1.0000004) == 1.0 and quantize(1.0000006) == 1.000001


# ------------------------------------------------------------------ the count matrix (§5.2)


def test_cells_and_genes_are_ordered_by_name_whatever_they_arrived_as() -> None:
    shuffled = built(
        list(reversed(CELLS)),
        {"c2": dict(reversed(list(COUNTS["c2"].items()))), "c1": dict(reversed(list(COUNTS["c1"].items())))},
    )
    assert [c.cell_key for c in shuffled.cells] == ["c1", "c2"]
    assert shuffled.genes == ("ACTB", "GAPDH", "MT-CO1", "RPL13A")
    assert [key for key, _ in shuffled.rows] == ["c1", "c2"]


def test_a_zero_count_is_absent_rather_than_stored(matrix: CountMatrix) -> None:
    assert matrix.row("c1") == {"GAPDH": 100.0, "MT-CO1": 5.0}
    assert matrix.column("ACTB") == {}


def test_the_digest_does_not_depend_on_how_the_counts_were_supplied(matrix: CountMatrix) -> None:
    resupplied = built(
        CELLS, {"c1": {"MT-CO1": 5.0, "GAPDH": 100.0, "ACTB": 0.0}, "c2": {"RPL13A": 7.0, "GAPDH": 40.0}}
    )
    assert resupplied.digest() == matrix.digest()
    require_digest(matrix.digest(), "matrix digest")


def test_counts_for_a_gene_outside_the_declared_reference_are_refused() -> None:
    """Otherwise a typo in a gene list reads as a gene the assay never measured."""
    with pytest.raises(ValueError, match="outside the matrix's gene list"):
        CountMatrix.build(cells=CELLS, counts={"c1": {"NO_TYPO_ALLOWED": 1.0}}, genome=GENOME, genes=["GAPDH"])


def test_a_gene_measured_at_zero_in_every_cell_survives_the_stored_asset(matrix: CountMatrix) -> None:
    """The reference it was measured against is part of the asset, not a fact the rows imply (§10.1)."""
    assert "ACTB" in matrix.genes
    restored = CountMatrix.from_payload(json.loads(matrix.json_bytes()))
    assert restored.genes == matrix.genes
    assert restored.detection_rate("ACTB") == 0.0


def test_a_count_for_a_cell_the_matrix_does_not_hold_is_refused() -> None:
    with pytest.raises(ValueError, match="not in the matrix"):
        built(CELLS, {"c1": {"GAPDH": 1.0}, "c3": {"GAPDH": 1.0}})


def test_the_same_cell_key_twice_is_refused_rather_than_merged() -> None:
    with pytest.raises(ValueError, match="unique"):
        built([cell("c1", donor="donor_a", group="control"), cell("c1", donor="donor_b", group="case")], COUNTS)


def test_a_row_and_its_column_agree_because_both_come_from_the_same_stored_rows(matrix) -> None:
    for cell_key, values in matrix.rows:
        for gene, value in values:
            assert matrix.column(gene)[cell_key] == value


def test_the_payload_round_trip_recovers_the_same_bytes(matrix) -> None:
    restored = CountMatrix.from_payload(json.loads(matrix.json_bytes()))
    assert restored.json_bytes() == matrix.json_bytes()
    assert restored.genome == matrix.genome


def test_library_size_and_detected_genes_ignore_the_absent_entries(matrix) -> None:
    assert matrix.total_counts("c1") == 105.0
    assert matrix.detected_genes("c1") == 2
    with pytest.raises(KeyError):
        matrix.row("c40")


def test_a_gene_fraction_is_measured_over_the_library_not_over_the_genes_named(matrix) -> None:
    assert matrix.fraction_of("c1", ["MT-CO1"]) == pytest.approx(5.0 / 105.0)
    assert matrix.gene_set_counts("c2", ["MT-CO1", "RPL13A"]) == 7.0
    assert matrix.fraction_of("c2", ["absent_gene"]) == 0.0


def test_a_cell_with_no_counts_has_a_zero_fraction_rather_than_a_division_error() -> None:
    empty = built([cell("c9", donor="donor_a", group="case")], {"c9": {}})
    assert empty.fraction_of("c9", ["GAPDH"]) == 0.0
    assert empty.total_counts("c9") == 0.0


def test_detection_rate_is_over_every_cell_including_the_ones_that_missed_it(matrix) -> None:
    assert matrix.detection_rate("GAPDH") == 1.0
    assert matrix.detection_rate("RPL13A") == 0.5
    assert matrix.detection_rate("ACTB") == 0.0


def test_the_design_is_readable_off_the_matrix_because_the_donor_is_carried_per_cell(matrix) -> None:
    assert matrix.donors() == ("donor_a", "donor_b")
    assert matrix.groups() == ("case", "control")
    assert matrix.cells_by_donor() == {"donor_a": ("c1",), "donor_b": ("c2",)}


def test_an_empty_matrix_reports_zero_rates_instead_of_failing() -> None:
    empty = CountMatrix.build(cells=[], counts={}, genome=GENOME)
    assert empty.n_cells == 0 and empty.detection_rate("GAPDH") == 0.0


def test_a_subset_names_its_absent_cells_instead_of_ignoring_them(matrix) -> None:
    with pytest.raises(KeyError, match="not in the matrix"):
        matrix.subset(cells=["c1", "c40"])


def test_a_subset_names_an_absent_gene_the_same_way(matrix) -> None:
    with pytest.raises(KeyError, match="not in the matrix's reference"):
        matrix.subset(cells=["c1"], genes=["GAPDH", "NOT_IN_THE_REFERENCE"])


def test_a_subset_keeps_the_genome_and_the_cells_it_was_given(matrix) -> None:
    picked = matrix.subset(cells=["c1"], genes=["GAPDH"])
    assert picked.genes == ("GAPDH",)
    assert picked.genome == matrix.genome
    assert picked.row("c1") == {"GAPDH": 100.0}
    assert matrix.subset(cells=["c1"]).genes == matrix.genes


def test_a_matrix_built_without_an_annotation_source_still_names_an_id_system() -> None:
    """Section 10.1 rejects a run whose identifier space is unknown, so it has to be sayable at all."""
    assert genome_of(None) == EMPTY_GENOME
    assert EMPTY_GENOME.id_system is GenomeIdSystem.OTHER
    declared = genome_of(
        {"build": "gencode-v44", "annotation_source": "GENCODE", "version": "44", "id_system": "hgnc_symbol"}
    )
    assert declared.id_system is GenomeIdSystem.HGNC_SYMBOL


# ------------------------------------------------------------------ declared conversions (§4.1)


def test_a_conversion_that_loses_anything_lists_exactly_what_it_lost() -> None:
    report = convert("h5ad", "seurat", both_sides_kept=True)
    assert report.lossy and report.dropped == ("reductions", "graphs", "obsm")
    assert LOSSY_CONVERSIONS[("h5ad", "seurat")] == report.dropped


def test_a_lossy_conversion_that_kept_both_sides_says_where_the_other_half_is() -> None:
    warning = convert("h5ad", "seurat", both_sides_kept=True).as_warning()
    assert warning is not None and "lossy" in warning and "retained on the other asset" in warning


def test_a_lossy_conversion_that_did_not_keep_the_source_is_reported_as_a_breach() -> None:
    """Section 4.1 keeps both derived assets; a conversion that only kept one has lost data."""
    warning = convert("h5ad", "seurat", both_sides_kept=False).as_warning()
    assert warning is not None and "the pre-conversion asset was not kept" in warning


def test_a_conversion_that_drops_nothing_is_not_reported_as_lossy() -> None:
    report = ConversionReport(source="json", target="json", dropped=(), both_sides_kept=False)
    assert not report.lossy and report.as_warning() is None


def test_the_lossy_round_trips_are_recorded_as_data_not_as_prose() -> None:
    assert set(LOSSY_CONVERSIONS) == {("h5ad", "seurat"), ("seurat", "h5ad"), ("counts", "log1p")}
    assert convert("seurat", "counts", both_sides_kept=True).dropped == ()
