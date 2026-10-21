"""Optional scanpy/anndata adapter behind the same contract (plan V2.0 sections 4.1, 4.4, 9.6).

The repository must stay testable on a machine with no scientific stack, so nothing here is imported
at module load: ``availability()`` looks the packages up without executing them, and the two functions
that really need them raise a classified ``NEEDS_INPUT`` failure rather than an ``ImportError``. A
missing dependency is a deployment fact a person has to resolve - install it in the container, or run
the reference executor - which is exactly what the state machine's needs-input branch is for.

The mapping itself is duck-typed, so it is the same code whether the object came from ``anndata`` or
from a test double: the adapter never asks an object what class it is, only whether it exposes X, obs,
var and the layers it was pointed at.

Section 4.1 is the reason this file exists at all. An h5ad read into the platform's own contract drops
whatever the contract does not model, and a Seurat object read back drops ``reductions`` and ``graphs``.
The adapter therefore reports what it dropped, and refuses to be the only copy of either side.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from bioadapt_schemas.entities import Genome
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.identifiers import canonical_cell_key

from .errors import StepFailure
from .matrix import CellMeta, ConversionReport, CountMatrix, convert
from .products import ExpressionProduct
from .steps import SLOT_COUNTS

#: Imported lazily, in this order; the first name that is absent is the one reported.
REQUIRED_PACKAGES = ("anndata", "scanpy")
BACKEND = "scanpy"

#: obs columns the platform contract needs per cell. Names are configurable because a real h5ad
#: inherits whatever the upstream pipeline called them; the values here are the platform's own.
DEFAULT_OBS_KEYS = {"specimen_id": "specimen_id", "donor_id": "donor_id", "group": "group"}


def _installed(name: str) -> Optional[str]:
    """The version of an importable distribution, or None when it is not there.

    Locating a package is deliberately not the same as importing it: a preflight or a workbench that
    asks "is the scanpy backend here?" must not pay for loading numpy to answer, and must not crash
    when the answer is no.
    """
    try:
        if importlib.util.find_spec(name) is None:
            return None
    except (ImportError, ValueError):
        return None
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def availability() -> dict[str, Optional[str]]:
    """Package name to installed version, or None. Looks the package up without importing it."""
    return {name: _installed(name) for name in REQUIRED_PACKAGES}


def missing_packages() -> tuple[str, ...]:
    return tuple(name for name, version in availability().items() if version is None)


def is_available() -> bool:
    return not missing_packages()


def unavailable(detail: str) -> StepFailure:
    return StepFailure(
        code="optional_backend_unavailable",
        error_class=ErrorClass.NEEDS_INPUT,
        message=(
            f"{detail}; install it in the tool container or select the reference executor for this node "
            "(the reference path is the one the platform's own tests cover)"
        ),
        field="backend",
        retryable=False,
    )


def require() -> Mapping[str, str]:
    """Return the versions this node is about to depend on, or raise a classified failure.

    A distribution can be listed and still not import - a broken numpy, a half-finished wheel. That is
    the same deployment fact as an absent one as far as a run is concerned, so it raises the same
    needs-input failure rather than letting an ``ImportError`` reach the orchestrator.
    """
    missing = missing_packages()
    if missing:
        raise unavailable(f"{BACKEND} backend needs {', '.join(missing)}")
    loaded: dict[str, str] = {}
    for name in REQUIRED_PACKAGES:
        try:
            module = importlib.import_module(name)
        except Exception as error:
            raise unavailable(f"{BACKEND} backend needs {name}, which is listed but cannot be imported") from error
        loaded[name] = str(getattr(module, "__version__", _installed(name) or "unknown"))
    return loaded


def _index_of(frame: Any) -> Sequence[Any]:
    index = getattr(frame, "index", None)
    if index is not None:
        return list(index)
    if isinstance(frame, Mapping):
        return list(range(len(next(iter(frame.values())))))
    return list(range(len(frame)))


def _missing_column(key: str) -> StepFailure:
    return StepFailure(
        code="input_schema_invalid",
        error_class=ErrorClass.FAILED,
        message=(
            f"obs has no column {key!r}; every cell needs one before a run can place it in the design "
            "matrix, and the adapter will not read a column that is not there as one that is (§4.1)"
        ),
        field="obs",
    )


def _column_values(obs: Any, key: str) -> Sequence[Any]:
    """One obs column as a plain sequence, from a DataFrame or from a dict of columns."""
    if isinstance(obs, Mapping):
        if key not in obs:
            raise _missing_column(key)
        column = obs[key]
        if isinstance(column, Mapping):
            return list(column.values())
        return list(column)
    try:
        return list(obs[key])
    except KeyError as error:
        raise _missing_column(key) from error


def _rows_of_matrix(x: Any, n_rows: int, n_cols: int) -> list[dict[int, float]]:
    """Dense, COO-sparse or dict-of-dict input, read as one {column index: value} per row."""

    def check_column(column: int) -> None:
        if column >= n_cols:
            raise StepFailure(
                code="input_schema_invalid",
                error_class=ErrorClass.FAILED,
                message=f"the matrix reaches column {column} but var names only {n_cols} genes; this file's "
                "shape and its gene index disagree, and reading it as-is would drop the extra genes silently",
                field="var",
            )

    if hasattr(x, "tocoo"):
        sparse = x.tocoo()
        rows: list[dict[int, float]] = [{} for _ in range(n_rows)]
        for row, column, value in zip(sparse.row, sparse.col, sparse.data):
            if float(value) != 0.0:
                check_column(int(column))
                rows[int(row)][int(column)] = float(value)
        return rows
    if hasattr(x, "toarray"):
        x = x.toarray()
    if isinstance(x, Mapping):
        rows = []
        for key in sorted(x, key=str):
            row = {
                int(column): float(value)
                for column, value in x[key].items()
                if float(value) != 0.0
            }
            for column in row:
                check_column(column)
            rows.append(row)
        return rows
    dense: list[dict[int, float]] = []
    for row in x:
        items = {int(j): float(v) for j, v in enumerate(row) if float(v) != 0.0}
        for column in items:
            check_column(column)
        dense.append(items)
    return dense


@dataclass(frozen=True)
class AnnDataRead:
    """The product plus the honest note about what the contract could not carry (§4.1)."""

    product: ExpressionProduct
    report: ConversionReport
    #: obsm/uns/var entries the platform contract has no field for.
    dropped_slots: tuple[str, ...]
    #: How many cells the layer named as counts actually covered; a partial layer is a red flag.
    cells_with_counts: int

    @property
    def warnings(self) -> tuple[str, ...]:
        note = self.report.as_warning()
        carried = (
            f"read {self.product.matrix.n_cells} cells x {self.product.matrix.n_genes} genes into slot "
            f"{self.product.slot!r}; dropped slots {list(self.dropped_slots)}"
        )
        return tuple(item for item in (note, carried) if item)


def gene_names(var: Any) -> tuple[str, ...]:
    """The gene index, from a DataFrame's ``index`` or from a dict that names its own index column."""
    index = getattr(var, "index", None)
    if index is not None:
        return tuple(str(name) for name in index)
    if isinstance(var, Mapping):
        for key in ("index", "_index", "gene_symbol", "symbol"):
            if key in var:
                return tuple(str(name) for name in var[key])
        return tuple(str(key) for key in var)
    return tuple(str(name) for name in var)


def from_anndata(
    adata: Any,
    *,
    genome: Genome,
    slot: str = SLOT_COUNTS,
    transform: str = "counts",
    counts_layer: Optional[str] = None,
    obs_keys: Mapping[str, str] = DEFAULT_OBS_KEYS,
    both_sides_kept: bool = True,
) -> AnnDataRead:
    """Map an AnnData-shaped object onto the platform contract, naming everything it cannot hold.

    ``transform`` is a declaration, never a guess: the contract records which state the matrix is in
    and every downstream tool checks it, so an adapter that inferred counts-vs-log from the magnitudes
    would let a normalised matrix into a pseudobulk sum.
    """
    obs = adata.obs
    var = adata.var
    genes = gene_names(var)
    barcodes = [str(barcode) for barcode in getattr(adata, "obs_names", None) or _index_of(obs)]
    n_rows = len(barcodes)
    layers = getattr(adata, "layers", None)
    source = "X"
    x = adata.X
    if counts_layer is not None:
        names = sorted(layers.keys()) if layers is not None else []
        if counts_layer not in names:
            raise StepFailure(
                code="input_schema_invalid",
                error_class=ErrorClass.FAILED,
                message=f"the node asked for counts in layer {counts_layer!r} but this object has {names}",
                field="counts_layer",
            )
        x, source = layers[counts_layer], f"layers[{counts_layer}]"

    values = _rows_of_matrix(x, n_rows, len(genes))
    columns = {name: _column_values(obs, key) for name, key in obs_keys.items()}
    short = sorted(name for name, values in columns.items() if len(values) != n_rows)
    if short:
        raise StepFailure(
            code="input_schema_invalid",
            error_class=ErrorClass.FAILED,
            message=(
                f"obs columns {short} hold {[len(columns[name]) for name in short]} values while the file "
                f"names {n_rows} cells; a cell that runs past its own column would be counted in no group"
            ),
            field="obs",
        )
    counts: dict[str, dict[str, float]] = {}
    cells: list[CellMeta] = []
    for index, barcode in enumerate(barcodes):
        specimen = str(columns["specimen_id"][index])
        donor = str(columns["donor_id"][index])
        group = str(columns["group"][index])
        key = canonical_cell_key(specimen, barcode)
        cells.append(CellMeta(cell_key=key, specimen_id=specimen, donor_id=donor, group=group))
        row = values[index] if index < len(values) else {}
        if row:
            counts[key] = {genes[column]: value for column, value in sorted(row.items())}

    matrix = CountMatrix.build(cells=cells, counts=counts, genome=genome, genes=genes)
    dropped = _unmodelled_slots(adata, counts_layer=counts_layer)
    #: The loss is this file's own dropped slots, not a table entry: what an h5ad cannot carry into the
    #: contract depends on what it holds. ``both_sides_kept`` stays the §4.1 question either way - if
    #: the original file was not admitted as an asset, nothing downstream can recover a slot.
    report = ConversionReport(
        source="h5ad", target="platform_contract", dropped=dropped, both_sides_kept=both_sides_kept
    )
    note = report.as_warning()
    product = ExpressionProduct(
        slot=slot,
        matrix=matrix,
        transform=transform,
        parameters={"source": source, "counts_layer": counts_layer, "n_obs": n_rows, "n_vars": len(genes)},
        conversion_warnings=tuple(
            [f"h5ad slots with no field in the platform contract: {list(dropped)}"] + ([note] if note else [])
        ),
    )
    return AnnDataRead(
        product=product, report=report, dropped_slots=dropped, cells_with_counts=len(counts)
    )


def _unmodelled_slots(adata: Any, *, counts_layer: Optional[str]) -> tuple[str, ...]:
    """Every part of the object the contract has nowhere to put, so the loss is stated, not silent."""
    dropped: list[str] = []
    obsm = getattr(adata, "obsm", None)
    keys = sorted(str(key) for key in (obsm.keys() if obsm is not None else []))
    if keys:
        dropped.extend(f"obsm[{key}]" for key in keys)
    uns = getattr(adata, "uns", None)
    if uns is not None and sorted(str(key) for key in uns.keys()):
        dropped.append("uns")
    if getattr(adata, "raw", None) is not None:
        dropped.append("raw")
    obsp = getattr(adata, "obsp", None)
    if obsp is not None and sorted(str(key) for key in obsp.keys()):
        dropped.append("obsp")
    #: One matrix per asset is what the contract keeps, so whatever the read did not pick up is gone -
    #: including ``X`` itself when the node asked for a counts layer.
    layers = getattr(adata, "layers", None)
    names = sorted(str(key) for key in (layers.keys() if layers is not None else []))
    if counts_layer is not None:
        dropped.append("X")
        if len(names) > 1:
            dropped.append("other layers")
    elif names:
        dropped.append("other layers")
    return tuple(sorted(set(dropped)))


def read_h5ad(
    path: str,
    *,
    genome: Genome,
    slot: str = SLOT_COUNTS,
    transform: str = "counts",
    counts_layer: Optional[str] = None,
    obs_keys: Mapping[str, str] = DEFAULT_OBS_KEYS,
    both_sides_kept: bool = True,
) -> AnnDataRead:
    """Read an h5ad through anndata. Requires the optional backend (§4.4)."""
    versions = require()
    anndata = importlib.import_module("anndata")
    try:
        adata = anndata.read_h5ad(path)
    except OSError as error:
        raise StepFailure(
            code="input_missing",
            error_class=ErrorClass.NEEDS_INPUT,
            message=(
                f"the {BACKEND} backend is installed but {path!r} cannot be read: {error}. A file a node "
                "points at has to be admitted as an asset first, so this is an input a person resolves"
            ),
            field="path",
            retryable=False,
        ) from error
    read = from_anndata(
        adata,
        genome=genome,
        slot=slot,
        transform=transform,
        counts_layer=counts_layer,
        obs_keys=obs_keys,
        both_sides_kept=both_sides_kept,
    )
    return AnnDataRead(
        product=ExpressionProduct(
            slot=read.product.slot,
            matrix=read.product.matrix,
            transform=read.product.transform,
            parameters={**dict(read.product.parameters), "backend": f"anndata@{versions['anndata']}", "path": str(path)},
            conversion_warnings=read.product.conversion_warnings,
        ),
        report=read.report,
        dropped_slots=read.dropped_slots,
        cells_with_counts=read.cells_with_counts,
    )


def to_anndata(product: ExpressionProduct):
    """Build an AnnData from a contract product. The reverse direction is lossy by declaration.

    ``X`` is the only matrix the contract keeps per cell, so a product whose transform is log-normalised
    is written as ``X`` with no ``counts`` layer, and the conversion report says the raw counts live on
    the other asset. Writing them into ``X`` as well would make the file look self-contained when it
    is not.
    """
    require()
    anndata = importlib.import_module("anndata")
    matrix = product.matrix
    report = convert("platform_contract", "h5ad", both_sides_kept=True)
    rows = [matrix.row(cell.cell_key) for cell in matrix.cells]
    dense = [[float(rows[index].get(gene, 0.0)) for gene in matrix.genes] for index in range(len(matrix.cells))]
    obs = {
        "specimen_id": [cell.specimen_id for cell in matrix.cells],
        "donor_id": [cell.donor_id for cell in matrix.cells],
        "group": [cell.group for cell in matrix.cells],
    }
    adata = anndata.AnnData(
        X=dense,
        obs=obs,
        var={"gene_symbol": list(matrix.genes)},
        dtype="float32",
    )
    adata.uns["bioadapt"] = {
        "slot": product.slot,
        "transform": product.transform,
        "file_sha256": product.file_sha256(),
        "lossy_conversion": report.dropped,
        "counts_kept_on": "the expression asset; the platform stores both sides",
    }
    return adata


__all__ = [
    "BACKEND",
    "DEFAULT_OBS_KEYS",
    "AnnDataRead",
    "availability",
    "from_anndata",
    "is_available",
    "missing_packages",
    "read_h5ad",
    "require",
    "to_anndata",
    "unavailable",
]
