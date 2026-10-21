"""The count matrix the reference workflow runs on (plan V2.0 sections 5.2, 5.6, 7.1).

Pure Python and deterministic: every iteration is over sorted keys, every digest is over a
canonical payload. This is the platform's own exchange shape - section 4.1 requires tool interfaces
rather than cross-language object assumptions, and h5ad/Seurat round trips lose reductions and
graphs, so a conversion that drops anything has to say so and keep both derived assets.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from bioadapt_schemas.entities import Genome, require_hex64, sha256_hex
from bioadapt_schemas.enums import GenomeIdSystem

from .codec import canonical_bytes

#: Section 4.1: conversions that lose information, and exactly what they lose. Kept as data so a
#: wrapper cannot quietly claim a round trip is faithful.
LOSSY_CONVERSIONS: dict[tuple[str, str], tuple[str, ...]] = {
    ("h5ad", "seurat"): ("reductions", "graphs", "obsm"),
    ("seurat", "h5ad"): ("misc", "tools"),
    ("counts", "log1p"): ("raw_counts",),
}


class ConversionError(ValueError):
    pass


@dataclass(frozen=True)
class ConversionReport:
    """What a format conversion dropped, and whether both sides were kept (§4.1)."""

    source: str
    target: str
    dropped: tuple[str, ...]
    both_sides_kept: bool

    @property
    def lossy(self) -> bool:
        return bool(self.dropped)

    def as_warning(self) -> Optional[str]:
        if not self.lossy:
            return None
        if not self.both_sides_kept:
            return (
                f"conversion {self.source}->{self.target} dropped {list(self.dropped)} and the "
                "pre-conversion asset was not kept"
            )
        return f"conversion {self.source}->{self.target} is lossy: {list(self.dropped)} retained on the other asset"


def convert(source: str, target: str, *, both_sides_kept: bool) -> ConversionReport:
    dropped = LOSSY_CONVERSIONS.get((source, target), ())
    return ConversionReport(source=source, target=target, dropped=dropped, both_sides_kept=both_sides_kept)


@dataclass(frozen=True)
class CellMeta:
    """One cell, and the design-matrix row it belongs to.

    The donor is carried per cell because every robustness and rarity judgement in sections 7.2 and
    7.5 counts donors, not cells; a run that cannot say which donor a cell came from cannot claim a
    candidate is stable.
    """

    cell_key: str
    specimen_id: str
    donor_id: str
    group: str

    def to_payload(self) -> dict[str, str]:
        return {
            "cell_key": self.cell_key,
            "specimen_id": self.specimen_id,
            "donor_id": self.donor_id,
            "group": self.group,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CellMeta":
        return cls(
            cell_key=str(payload["cell_key"]),
            specimen_id=str(payload["specimen_id"]),
            donor_id=str(payload["donor_id"]),
            group=str(payload["group"]),
        )


@dataclass(frozen=True)
class CountMatrix:
    """Sparse counts keyed by cell then gene; absent means zero."""

    cells: tuple[CellMeta, ...]
    genes: tuple[str, ...]
    genome: Genome
    rows: tuple[tuple[str, tuple[tuple[str, float], ...]], ...]

    @classmethod
    def build(
        cls,
        *,
        cells: Sequence[CellMeta],
        counts: Mapping[str, Mapping[str, float]],
        genome: Genome,
        genes: Optional[Iterable[str]] = None,
    ) -> "CountMatrix":
        """The gene list is the universe the matrix was measured against, not the genes that came out
        non-zero: a gene measured at zero in every cell has still been looked for, and a stored asset
        that lost it would answer a different question when it was read back (§10.1).
        """
        keys = [cell.cell_key for cell in cells]
        if len(set(keys)) != len(keys):
            raise ValueError("cell keys must be unique within a matrix")
        known = set(keys)
        stray = sorted(set(counts) - known)
        if stray:
            raise ValueError(f"counts recorded for cells that are not in the matrix: {stray[:5]}")
        detected = {gene for row in counts.values() for gene in row}
        gene_names = sorted(genes) if genes is not None else sorted(detected)
        outside = sorted(detected - set(gene_names))
        if outside:
            raise ValueError(f"counts recorded for genes outside the matrix's gene list: {outside[:5]}")
        rows = []
        for cell_key in sorted(counts):
            row = {gene: float(value) for gene, value in sorted(counts[cell_key].items()) if float(value) != 0.0}
            rows.append((cell_key, tuple(row.items())))
        return cls(cells=tuple(sorted(cells, key=lambda c: c.cell_key)), genes=tuple(gene_names), genome=genome,
                   rows=tuple(rows))

    @property
    def n_cells(self) -> int:
        return len(self.cells)

    @property
    def n_genes(self) -> int:
        return len(self.genes)

    def _row(self, cell_key: str) -> tuple[tuple[str, float], ...]:
        position = bisect_left(self.rows, cell_key, key=lambda row: row[0])
        if position < len(self.rows) and self.rows[position][0] == cell_key:
            return self.rows[position][1]
        raise KeyError(cell_key)

    def _columns(self) -> dict[str, dict[str, float]]:
        cached = getattr(self, "_column_index", None)
        if cached is None:
            cached = {gene: {} for gene in self.genes}
            for cell_key, values in self.rows:
                for gene, value in values:
                    cached[gene][cell_key] = value
            object.__setattr__(self, "_column_index", cached)
        return cached

    def row(self, cell_key: str) -> dict[str, float]:
        return dict(self._row(cell_key))

    def column(self, gene: str) -> dict[str, float]:
        return dict(self._columns().get(gene, {}))

    def total_counts(self, cell_key: str) -> float:
        return sum(value for _gene, value in self._row(cell_key))

    def detected_genes(self, cell_key: str) -> int:
        return len(self._row(cell_key))

    def gene_set_counts(self, cell_key: str, genes: Iterable[str]) -> float:
        wanted = set(genes)
        return sum(value for gene, value in self._row(cell_key) if gene in wanted)

    def fraction_of(self, cell_key: str, genes: Iterable[str]) -> float:
        total = self.total_counts(cell_key)
        if total <= 0:
            return 0.0
        return self.gene_set_counts(cell_key, genes) / total

    def detection_rate(self, gene: str) -> float:
        if not self.cells:
            return 0.0
        return len(self.column(gene)) / self.n_cells

    def donors(self) -> tuple[str, ...]:
        return tuple(sorted({cell.donor_id for cell in self.cells}))

    def groups(self) -> tuple[str, ...]:
        return tuple(sorted({cell.group for cell in self.cells}))

    def cells_by_donor(self) -> dict[str, tuple[str, ...]]:
        by_donor: dict[str, list[str]] = {}
        for cell in self.cells:
            by_donor.setdefault(cell.donor_id, []).append(cell.cell_key)
        return {donor: tuple(sorted(keys)) for donor, keys in sorted(by_donor.items())}

    def subset(self, *, cells: Iterable[str], genes: Optional[Iterable[str]] = None) -> "CountMatrix":
        keep_cells = set(cells)
        missing = sorted(keep_cells - {cell.cell_key for cell in self.cells})
        if missing:
            raise KeyError(f"cells not in the matrix: {missing[:5]}")
        keep_genes = set(genes) if genes is not None else None
        if keep_genes is not None:
            absent = sorted(keep_genes - set(self.genes))
            if absent:
                raise KeyError(f"genes not in the matrix's reference: {absent[:5]}")
        selected = [cell for cell in self.cells if cell.cell_key in keep_cells]
        counts = {}
        for cell_key in keep_cells:
            counts[cell_key] = {
                gene: value
                for gene, value in self._row(cell_key)
                if keep_genes is None or gene in keep_genes
            }
        return CountMatrix.build(
            cells=selected,
            counts=counts,
            genome=self.genome,
            genes=self.genes if keep_genes is None else keep_genes,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "cells": [cell.to_payload() for cell in self.cells],
            "genes": list(self.genes),
            "genome": self.genome.model_dump(mode="json"),
            "rows": [[cell_key, [[gene, value] for gene, value in values]] for cell_key, values in self.rows],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "CountMatrix":
        cells = tuple(CellMeta.from_payload(cell) for cell in payload["cells"])
        rows: dict[str, dict[str, float]] = {}
        for cell_key, values in payload["rows"]:
            rows[cell_key] = {str(gene): float(value) for gene, value in values}
        genome = payload.get("genome")
        return cls.build(
            cells=cells,
            counts=rows,
            genome=Genome(**genome) if genome else EMPTY_GENOME,
            genes=payload.get("genes") or None,
        )

    def digest(self) -> str:
        return sha256_hex(self.json_bytes())

    def json_bytes(self) -> bytes:
        return canonical_bytes(self.to_payload())


#: A matrix built without an annotation source still has to name an id system: the L1 checks in
#: section 10.1 reject a run whose gene identifier space is unknown.
EMPTY_GENOME = Genome(build="unknown", annotation_source="unknown", version="0", id_system=GenomeIdSystem.OTHER)


def genome_of(asset_genome: Optional[Mapping[str, Any]]) -> Genome:
    """Read the genome an asset declares, so a wrapper can refuse a mismatched id system."""
    if asset_genome is None:
        return EMPTY_GENOME
    return Genome(**dict(asset_genome))


def require_digest(value: str, label: str) -> str:
    return require_hex64(value, label)
