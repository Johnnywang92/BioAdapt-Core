"""The optional scanpy/anndata adapter, tested without the scientific stack (§4.1, §4.4, §9.6).

Two promises are checked here. The first is that the repository stays runnable where scanpy is not
installed: the backend is located without importing it, and every path that really needs it comes back
as a classified ``NEEDS_INPUT`` failure, because a missing dependency is a deployment fact a person
resolves - install it in the container, or run the reference executor - not a crash in the platform.

The second is section 4.1's. An h5ad read into the platform's own contract keeps one matrix and drops
whatever the contract has no field for, so the adapter has to say which slots it dropped, whether the
original file was kept alongside them, and refuse to invent an obs column or a gene universe the file
does not carry. The mapping is duck-typed, so the doubles below are ordinary objects rather than
anndata classes - which is also what lets this module run here.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping, Optional

import pytest

from bioadapt_schemas.entities import Genome
from bioadapt_schemas.enums import GenomeIdSystem
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.identifiers import canonical_cell_key

from bioadapt_scrnaseq import scanpy_adapter
from bioadapt_scrnaseq.errors import StepFailure
from bioadapt_scrnaseq.matrix import CountMatrix
from bioadapt_scrnaseq.scanpy_adapter import (
    REQUIRED_PACKAGES,
    AnnDataRead,
    availability,
    from_anndata,
    is_available,
    missing_packages,
    read_h5ad,
    require,
    to_anndata,
)

GENOME = Genome(
    build="gencode-v44",
    annotation_source="GENCODE",
    version="44",
    id_system=GenomeIdSystem.HGNC_SYMBOL,
)
GENES = ("ACTB", "B2M", "MT-CO1", "RPL13A")
BARCODES = ("AAACCTG-1", "AAACCTG-2", "CTAACG-1")
OBS: dict[str, list[str]] = {
    "specimen_id": ["spc_a", "spc_a", "spc_b"],
    "donor_id": ["donor_1", "donor_1", "donor_2"],
    "group": ["control", "control", "case"],
}
DENSE = [
    [1.0, 0.0, 3.0, 0.0],
    [0.0, 2.0, 1.0, 0.0],
    [4.0, 0.0, 0.0, 5.0],
]


@dataclass(frozen=True)
class Frame:
    """An AnnData-shaped object: the adapter never asks what class it is, only what it exposes."""

    X: Any
    obs: Any
    var: Any
    obs_names: Any = None
    layers: Any = None
    obsm: Any = None
    uns: Any = None
    raw: Any = None
    obsp: Any = None


@dataclass(frozen=True)
class Index:
    """The ``.index`` a pandas frame exposes."""

    index: tuple


@dataclass(frozen=True)
class Columns:
    """A frame-like obs: columns reached by name, and nothing else."""

    columns: Mapping[str, list]
    index: tuple

    def __getitem__(self, key: str) -> list:
        if key not in self.columns:
            raise KeyError(key)
        return self.columns[key]


@dataclass(frozen=True)
class Coo:
    row: tuple
    col: tuple
    data: tuple


@dataclass(frozen=True)
class Sparse:
    """A scipy-sparse-shaped block: the adapter asks for ``tocoo`` and reads three parallel arrays."""

    coo: Coo

    def tocoo(self) -> Coo:
        return self.coo


def frame(**overrides: Any) -> Frame:
    fields: dict[str, Any] = {
        "X": DENSE,
        "obs": OBS,
        "var": {"index": list(GENES)},
        "obs_names": list(BARCODES),
    }
    fields.update(overrides)
    return Frame(**fields)


def fake_backend(
    monkeypatch: pytest.MonkeyPatch,
    *,
    installed: Optional[Mapping[str, Optional[str]]] = None,
    import_error: Optional[str] = None,
    stubs: Optional[Mapping[str, Any]] = None,
) -> None:
    """Make the adapter believe the stack is there, and point the imports at stubs instead."""
    versions = dict(
        installed
        if installed is not None
        else {name: f"1.{index}.0" for index, name in enumerate(REQUIRED_PACKAGES)}
    )
    monkeypatch.setattr(scanpy_adapter, "_installed", lambda name: versions.get(name))
    given = dict(stubs or {})

    def import_module(name: str) -> Any:
        if name == import_error:
            raise ImportError(f"{name} is present but broken")
        if name in given:
            return given[name]
        return SimpleNamespace(__version__=versions[name])

    monkeypatch.setattr(scanpy_adapter, "importlib", SimpleNamespace(import_module=import_module))


class StubAnnData:
    """What ``anndata.AnnData`` would be handed; the test reads the arguments back off it."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.uns: dict[str, Any] = {}


# ------------------------------------------------------------------ the stack stays optional


def test_looking_the_backend_up_never_imports_it() -> None:
    """A preflight that asks whether scanpy exists must not load numpy, or fail when it is absent."""
    already = {name for name in REQUIRED_PACKAGES if name in sys.modules}
    found = availability()
    assert list(found) == list(REQUIRED_PACKAGES)
    assert all(version is None or isinstance(version, str) for version in found.values())
    assert [name for name in REQUIRED_PACKAGES if name in sys.modules and name not in already] == []


def test_an_absent_backend_is_a_needs_input_stop_not_an_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_backend(monkeypatch, installed={name: None for name in REQUIRED_PACKAGES})
    assert missing_packages() == REQUIRED_PACKAGES
    assert is_available() is False
    with pytest.raises(StepFailure) as raised:
        require()
    failure = raised.value
    assert failure.error_class is ErrorClass.NEEDS_INPUT
    assert failure.code == "optional_backend_unavailable"
    assert failure.field == "backend" and failure.retryable is False
    assert "scanpy backend needs anndata, scanpy" in failure.message
    assert "reference executor" in failure.message


def test_a_listed_package_that_cannot_be_imported_stops_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken wheel is the same deployment fact as an absent one as far as a run is concerned."""
    fake_backend(monkeypatch, import_error="scanpy")
    with pytest.raises(StepFailure) as raised:
        require()
    assert raised.value.error_class is ErrorClass.NEEDS_INPUT
    assert "scanpy, which is listed but cannot be imported" in raised.value.message


def test_reading_a_file_the_backend_cannot_open_is_an_input_a_person_resolves() -> None:
    """Unstubbed: whichever half is missing - the package or the file - the run stops for input."""
    with pytest.raises(StepFailure) as raised:
        read_h5ad("/definitely/not/here/sample.h5ad", genome=GENOME)
    failure = raised.value
    assert failure.error_class is ErrorClass.NEEDS_INPUT
    assert failure.code in {"optional_backend_unavailable", "input_missing"}


def test_a_file_that_is_not_there_is_a_missing_input_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backend installed and the node pointed at nothing is still a needs-input stop (§9.2)."""

    def missing(path: str) -> Any:
        raise FileNotFoundError(path)

    fake_backend(monkeypatch, stubs={"anndata": SimpleNamespace(__version__="0.11.4", read_h5ad=missing)})
    with pytest.raises(StepFailure) as raised:
        read_h5ad("sample.h5ad", genome=GENOME)
    assert raised.value.error_class is ErrorClass.NEEDS_INPUT
    assert raised.value.code == "input_missing" and raised.value.field == "path"
    assert "admitted as an asset" in raised.value.message


# ------------------------------------------------------------------ the mapping is duck-typed


def test_from_anndata_maps_a_dense_frame_onto_the_contract() -> None:
    read = from_anndata(frame(), genome=GENOME)
    matrix = read.product.matrix
    assert read.cells_with_counts == 3
    assert matrix.genes == GENES
    assert [cell.cell_key for cell in matrix.cells] == sorted(
        canonical_cell_key(OBS["specimen_id"][index], BARCODES[index]) for index in range(3)
    )
    first = matrix.cells[0]
    assert (first.specimen_id, first.donor_id, first.group) == ("spc_a", "donor_1", "control")
    assert matrix.row(first.cell_key) == {"ACTB": 1.0, "MT-CO1": 3.0}
    assert read.product.transform == "counts" and read.product.counts is True
    assert read.product.parameters == {"source": "X", "counts_layer": None, "n_obs": 3, "n_vars": 4}


def test_the_same_counts_read_from_a_sparse_block_and_a_dense_one_are_the_same_matrix() -> None:
    sparse = Sparse(Coo(row=(0, 0, 1, 1, 2, 2), col=(0, 2, 1, 2, 0, 3), data=(1.0, 3.0, 2.0, 1.0, 4.0, 5.0)))
    from_sparse = from_anndata(frame(X=sparse), genome=GENOME).product.matrix
    from_dense = from_anndata(frame(), genome=GENOME).product.matrix
    assert from_sparse.digest() == from_dense.digest()


def test_a_frame_shaped_like_a_data_frame_is_read_the_same_way() -> None:
    obs = Columns(columns=OBS, index=BARCODES)
    as_frames = frame(obs=obs, var=Index(index=GENES))
    assert as_frames.obs.columns is OBS and isinstance(as_frames.var, Index)
    read = from_anndata(as_frames, genome=GENOME)
    assert read.product.matrix.digest() == from_anndata(frame(), genome=GENOME).product.matrix.digest()
    assert read.product.matrix.n_genes == 4


def test_a_gene_measured_zero_in_every_cell_stays_in_the_matrix_it_was_declared_for() -> None:
    """§10.1: var is the universe the file was measured against, not the genes that came out non-zero."""
    genes = (*GENES, "NPS")
    rows = [row + [0.0] for row in DENSE]
    matrix = from_anndata(frame(var={"index": list(genes)}, X=rows), genome=GENOME).product.matrix
    assert matrix.genes == tuple(sorted(genes))
    assert matrix.column("NPS") == {}


def test_transform_is_a_declaration_the_adapter_does_not_infer_from_magnitudes() -> None:
    """Reading a normalised file as counts would let it into a pseudobulk sum (§4.1)."""
    read = from_anndata(frame(X=DENSE), genome=GENOME, transform="log2_cpm", slot="expression")
    assert read.product.counts is False
    assert read.product.slot == "expression" and read.product.transform == "log2_cpm"


def test_a_matrix_wider_than_its_gene_index_is_refused() -> None:
    """Reading the extra genes as absent would be exactly the silent drop this section forbids."""
    with pytest.raises(StepFailure, match="shape and its gene index disagree") as raised:
        from_anndata(frame(X=[[0.0, 0.0, 0.0, 0.0, 7.0]] * 3), genome=GENOME)
    assert raised.value.field == "var"


def test_a_counts_layer_the_file_does_not_have_is_refused() -> None:
    with pytest.raises(StepFailure, match="counts in layer 'counts' but this object has \\['raw'\\]"):
        from_anndata(frame(layers={"raw": DENSE}), genome=GENOME, counts_layer="counts")


def test_an_obs_column_the_file_does_not_have_is_refused_rather_than_guessed() -> None:
    """A donor the file never names cannot be read out of the columns that happen to be there."""
    obs = {key: values for key, values in OBS.items() if key != "donor_id"}
    with pytest.raises(StepFailure, match="obs has no column 'donor_id'"):
        from_anndata(frame(obs=obs), genome=GENOME)
    with pytest.raises(StepFailure, match="obs has no column 'batch'"):
        from_anndata(frame(obs=Columns(columns=OBS, index=BARCODES)), genome=GENOME, obs_keys={"group": "batch"})


def test_obs_columns_that_do_not_cover_every_cell_are_refused() -> None:
    obs = {**OBS, "group": ["case"]}
    with pytest.raises(StepFailure, match="while the file names 3 cells"):
        from_anndata(frame(obs=obs), genome=GENOME)


# ------------------------------------------------------------------ what a read drops, in words


def test_slots_the_contract_cannot_hold_are_named_and_the_loss_is_reported() -> None:
    read = from_anndata(
        frame(obsm={"X_umap": None, "X_pca": None}, uns={"rank_genes_groups": {}}, obsp={"connectivities": 1}, raw=1),
        genome=GENOME,
    )
    assert read.dropped_slots == ("obsm[X_pca]", "obsm[X_umap]", "obsp", "raw", "uns")
    assert read.report.lossy is True and read.report.dropped == read.dropped_slots
    assert read.product.conversion_warnings[0].endswith(str(list(read.dropped_slots)))
    assert "retained on the other asset" in read.product.conversion_warnings[1]
    assert len(read.warnings) == 2
    assert any("read 3 cells x 4 genes" in warning for warning in read.warnings)


def test_keeping_only_the_converted_side_is_reported_as_a_breach() -> None:
    """§4.1: the h5ad has to stay an asset, or the dropped slots are gone with nothing to re-read."""
    read = from_anndata(frame(obsm={"X_pca": None}), genome=GENOME, both_sides_kept=False)
    assert "the pre-conversion asset was not kept" in read.product.conversion_warnings[1]
    assert read.report.as_warning() == read.product.conversion_warnings[1]


def test_a_file_the_contract_can_hold_whole_reports_no_loss() -> None:
    read = from_anndata(frame(), genome=GENOME)
    assert read.dropped_slots == ()
    assert read.report.lossy is False and read.report.as_warning() is None
    assert len(read.product.conversion_warnings) == 1
    assert "no field in the platform contract: []" in read.product.conversion_warnings[0]
    assert len(read.warnings) == 1


def test_reading_a_counts_layer_reports_the_block_it_left_behind() -> None:
    """One matrix per asset: picking a layer up means the other side of the file is not carried."""
    read = from_anndata(
        frame(layers={"counts": Sparse(Coo(row=(0,), col=(0,), data=(1.0,))), "logcounts": DENSE}),
        genome=GENOME,
        counts_layer="counts",
    )
    assert read.dropped_slots == ("X", "other layers")
    assert read.product.parameters["source"] == "layers[counts]"
    assert read.cells_with_counts == 1


# ------------------------------------------------------------------ the two ends of the pipe


def test_read_h5ad_records_the_backend_version_it_ran_with(monkeypatch: pytest.MonkeyPatch) -> None:
    """The node parameter says which optional backend produced the asset (§9.4)."""
    fake_backend(
        monkeypatch,
        stubs={"anndata": SimpleNamespace(__version__="0.11.4", read_h5ad=lambda path: frame())},
    )
    read = read_h5ad("sample.h5ad", genome=GENOME)
    assert isinstance(read, AnnDataRead)
    assert read.product.parameters["backend"] == "anndata@0.11.4"
    assert read.product.parameters["path"] == "sample.h5ad"


def test_the_reverse_direction_writes_one_matrix_and_says_where_the_other_side_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_backend(monkeypatch, stubs={"anndata": SimpleNamespace(__version__="0.11.4", AnnData=StubAnnData)})
    product = from_anndata(frame(), genome=GENOME, transform="log2_cpm").product
    adata = to_anndata(product)
    assert adata.kwargs["X"] == [[float(value) for value in row] for row in DENSE]
    assert "layers" not in adata.kwargs, "X holds the log values; writing counts too would look self-contained"
    assert adata.kwargs["obs"]["donor_id"] == ["donor_1", "donor_1", "donor_2"]
    note = adata.uns["bioadapt"]
    assert note["slot"] == "counts" and note["transform"] == "log2_cpm"
    assert note["file_sha256"] == product.file_sha256()
    assert "expression asset" in note["counts_kept_on"]


def test_writing_a_product_back_out_without_the_backend_stops_the_same_way(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_backend(monkeypatch, installed={name: None for name in REQUIRED_PACKAGES})
    product = from_anndata(frame(), genome=GENOME).product
    assert isinstance(product.matrix, CountMatrix)
    with pytest.raises(StepFailure) as raised:
        to_anndata(product)
    assert raised.value.error_class is ErrorClass.NEEDS_INPUT
