"""Step outputs, in the shape the platform stores and fingerprints them (plan V2.0 sections 5.2, 5.6, 9.4).

Every product carries the numbers section 9.4 requires a tool to emit, because the evidence cards and
gates read them from here rather than from anything a model writes. The two digests are different on
purpose: ``file_sha256`` is the byte identity of this serialization, and ``content_fingerprint`` is the
order-independent structural identity of the data behind it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Mapping, Optional, Sequence

from bioadapt_schemas.identifiers import content_fingerprint, index_hash, sha256_hex
from bioadapt_schemas.thresholds import Thresholds

from .codec import canonical_bytes, quantize
from .matrix import CountMatrix
from .numeric import median


def _rows_as_dicts(rows: Sequence[Any]) -> list[dict[str, Any]]:
    return [asdict(row) for row in rows]


@dataclass(frozen=True)
class Product:
    """Base of every workflow output that becomes an asset. ``slot`` is the plan's output name."""

    slot: str

    #: The asset container format and the contract version of the payload inside it.
    format_name: ClassVar[str] = "json"
    schema_version: ClassVar[str] = "product_v1"

    def payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def fingerprint_parts(self) -> dict[str, Any]:
        raise NotImplementedError

    def document(self) -> dict[str, Any]:
        """The stored form: the data, plus which slot it answers for and which contract it follows.

        The two metadata keys are part of the bytes, so an asset's content-derived id covers the fact
        that a QC product and a clustering product are different things even if their tables matched.
        """
        return {"slot": self.slot, "schema_version": self.schema_version, **self.payload()}

    def json_bytes(self) -> bytes:
        return canonical_bytes(self.document())

    def file_sha256(self) -> str:
        return sha256_hex(self.json_bytes())

    def content_fingerprint(self) -> str:
        parts = self.fingerprint_parts()
        if parts["n_rows"] <= 0 or parts["n_cols"] <= 0:
            raise ValueError(f"{self.slot} produced an empty table; a step that outputs nothing must fail, not store")
        return content_fingerprint(**parts)


@dataclass(frozen=True)
class CellQcRow:
    cell_key: str
    total_counts: float
    detected_genes: int
    #: None when the run cannot tell which genes are mitochondrial: unknown is recorded, not zero.
    mitochondrial_fraction: Optional[float]
    ribosomal_fraction: Optional[float]
    doublet_score: Optional[float]
    ambient_score: Optional[float]
    #: Risk labels from the QcPolicy rules that fired. Section 7.7: the default output is a label.
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class QcRuleSummary:
    name: str
    metric: str
    action: str
    bound: Optional[float]
    threshold_source: str
    source_ref: Optional[str]
    flagged: int


@dataclass(frozen=True)
class QcProduct(Product):
    """Section 7.1 step 3: QC is a policy choice plus a filtering-sensitivity comparison."""

    schema_version: ClassVar[str] = "qc_flags_v1"

    policy_id: str = ""
    policy_version: str = ""
    thresholds_version: str = ""
    #: "mark" is the MVP default; "mark_and_drop" is only reachable with an approving owner.
    actions: tuple[str, ...] = ("mark",)
    rules: tuple[QcRuleSummary, ...] = ()
    rows: tuple[CellQcRow, ...] = ()
    dropped: tuple[str, ...] = ()
    cell_deletion_approved_by: Optional[str] = None
    deletion_annotated_in_report: bool = False
    #: Scenario name -> numbers, e.g. {"unfiltered_baseline": {...}, "flagged_excluded": {...}}.
    sensitivity: Mapping[str, Mapping[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.dropped and not self.cell_deletion_approved_by:
            raise ValueError(
                f"{len(self.dropped)} cells were removed but no project owner approved the deletion "
                "(section 7.7)"
            )
        if self.dropped and not self.deletion_annotated_in_report:
            raise ValueError("deleted cells must be annotated in the report (section 7.7)")
        if self.dropped and "drop" not in self.actions:
            raise ValueError("the product dropped cells while reporting a marks-only policy")
        if not self.sensitivity:
            raise ValueError(f"{self.slot} has no filtering-sensitivity comparison")
        shapes = {tuple(sorted(values)) for values in self.sensitivity.values()}
        if len(shapes) > 1:
            raise ValueError("sensitivity scenarios must report the same columns so they can be compared")

    @property
    def marked_cells(self) -> int:
        return sum(1 for row in self.rows if row.flags)

    def payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "thresholds_version": self.thresholds_version,
            "actions": list(self.actions),
            "rules": _rows_as_dicts(self.rules),
            "cells": _rows_as_dicts(self.rows),
            "dropped": list(self.dropped),
            "cell_deletion_approved_by": self.cell_deletion_approved_by,
            "deletion_annotated_in_report": self.deletion_annotated_in_report,
            "sensitivity": {name: dict(values) for name, values in sorted(self.sensitivity.items())},
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "QcProduct":
        return cls(
            slot=slot,
            policy_id=payload["policy_id"],
            policy_version=payload["policy_version"],
            thresholds_version=payload["thresholds_version"],
            actions=tuple(payload["actions"]),
            rules=tuple(QcRuleSummary(**row) for row in payload["rules"]),
            rows=tuple(
                CellQcRow(**{**row, "flags": tuple(row["flags"])}) for row in payload["cells"]
            ),
            dropped=tuple(payload["dropped"]),
            cell_deletion_approved_by=payload["cell_deletion_approved_by"],
            deletion_annotated_in_report=payload["deletion_annotated_in_report"],
            sensitivity={name: dict(values) for name, values in sorted(payload["sensitivity"].items())},
        )

    def fingerprint_parts(self) -> dict[str, Any]:
        columns = [
            "total_counts",
            "detected_genes",
            "mitochondrial_fraction",
            "ribosomal_fraction",
            "doublet_score",
            "ambient_score",
            "flags",
        ]
        return {
            "n_rows": len(self.rows),
            "n_cols": len(columns),
            "obs_index_hash": index_hash(row.cell_key for row in self.rows),
            "var_index_hash": index_hash(columns),
            "nnz": sum(len(row.flags) for row in self.rows),
            "numeric_summaries": [
                ("dropped_cells", len(self.dropped)),
                ("marked_cells", self.marked_cells),
                ("total_counts_sum", round(sum(quantize(row.total_counts) for row in self.rows), 6)),
            ],
        }


@dataclass(frozen=True)
class ExpressionProduct(Product):
    """A matrix in a declared state; section 4.1 forbids a tool inferring counts vs log-normalised."""

    schema_version: ClassVar[str] = "expression_v1"

    matrix: CountMatrix
    transform: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    #: Lossy-conversion warnings from section 4.1, recorded rather than dropped quietly.
    conversion_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.matrix.n_cells == 0:
            raise ValueError(f"{self.slot} holds an empty matrix")

    @property
    def counts(self) -> bool:
        return self.transform == "counts"

    def payload(self) -> dict[str, Any]:
        return {
            "transform": self.transform,
            "parameters": dict(sorted(self.parameters.items())),
            "matrix": self.matrix.to_payload(),
            "conversion_warnings": list(self.conversion_warnings),
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        value_sum = sum(quantize(value) for _key, vals in self.matrix.rows for _gene, value in vals)
        return {
            "n_rows": self.matrix.n_cells,
            "n_cols": self.matrix.n_genes,
            "obs_index_hash": index_hash(cell.cell_key for cell in self.matrix.cells),
            "var_index_hash": index_hash(self.matrix.genes),
            "nnz": sum(1 for _key, vals in self.matrix.rows for _gene, value in vals if quantize(value) != 0.0),
            "numeric_summaries": [("value_sum", round(value_sum, 6))],
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "ExpressionProduct":
        return cls(
            slot=slot,
            matrix=CountMatrix.from_payload(payload["matrix"]),
            transform=payload["transform"],
            parameters=dict(payload["parameters"]),
            conversion_warnings=tuple(payload["conversion_warnings"]),
        )


@dataclass(frozen=True)
class GeneStatRow:
    gene: str
    mean: float
    variance: float
    detection_rate: float


@dataclass(frozen=True)
class FeatureSelectionProduct(Product):
    schema_version: ClassVar[str] = "feature_selection_v1"

    genes: tuple[str, ...]
    method: str
    n_top_features: int
    seed: int
    #: Mean, variance and ratio per selected gene: section 7.5 varies this set, so the choice must be auditable.
    stats: tuple[GeneStatRow, ...] = ()

    def __post_init__(self) -> None:
        if len(self.genes) != len(set(self.genes)):
            raise ValueError("selected features must be unique")
        if not self.genes:
            raise ValueError("feature selection returned no genes")
        if len(self.genes) > self.n_top_features:
            raise ValueError(f"{len(self.genes)} genes selected under a cap of {self.n_top_features}")

    def payload(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "n_top_features": self.n_top_features,
            "seed": self.seed,
            "genes": list(self.genes),
            "stats": _rows_as_dicts(self.stats),
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        return {
            "n_cols": 1,
            "n_rows": len(self.genes),
            "nnz": len(self.genes),
            "numeric_summaries": [("seed", self.seed), ("selected", len(self.genes))],
            "obs_index_hash": index_hash(self.genes),
            "var_index_hash": index_hash(["selected"]),
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "FeatureSelectionProduct":
        return cls(
            slot=slot,
            genes=tuple(payload["genes"]),
            method=payload["method"],
            n_top_features=payload["n_top_features"],
            seed=payload["seed"],
            stats=tuple(GeneStatRow(**row) for row in payload["stats"]),
        )


@dataclass(frozen=True)
class CellVectorRow:
    cell_key: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class GeneLoadingRow:
    component: int
    gene: str
    weight: float


@dataclass(frozen=True)
class EmbeddingProduct(Product):
    schema_version: ClassVar[str] = "embedding_v1"

    method: str
    n_components: int
    seed: int
    vectors: tuple[CellVectorRow, ...]
    #: Variance explained per component, in component order.
    explained_variance_ratio: tuple[float, ...] = ()
    loadings: tuple[GeneLoadingRow, ...] = ()

    def __post_init__(self) -> None:
        widths = {len(row.values) for row in self.vectors}
        if len(widths) != 1:
            raise ValueError("embedding rows must all have the same number of components")
        if widths != {self.n_components}:
            raise ValueError(f"n_components={self.n_components} but vectors have widths {sorted(widths)}")
        if self.explained_variance_ratio and abs(sum(self.explained_variance_ratio) - 1.0) > 1e-3:
            raise ValueError("explained variance ratios must sum to about one over the retained components")

    def payload(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "n_components": self.n_components,
            "seed": self.seed,
            "explained_variance_ratio": list(self.explained_variance_ratio),
            "cells": _rows_as_dicts(self.vectors),
            "loadings": _rows_as_dicts(self.loadings),
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        return {
            "n_cols": self.n_components,
            "n_rows": len(self.vectors),
            "nnz": sum(1 for row in self.vectors if any(quantize(value) != 0.0 for value in row.values)),
            "numeric_summaries": [("components", self.n_components), ("seed", self.seed)],
            "obs_index_hash": index_hash(row.cell_key for row in self.vectors),
            "var_index_hash": index_hash(f"pc{i + 1}" for i in range(self.n_components)),
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "EmbeddingProduct":
        return cls(
            slot=slot,
            method=payload["method"],
            n_components=payload["n_components"],
            seed=payload["seed"],
            vectors=tuple(
                CellVectorRow(cell_key=row["cell_key"], values=tuple(row["values"])) for row in payload["cells"]
            ),
            explained_variance_ratio=tuple(payload["explained_variance_ratio"]),
            loadings=tuple(GeneLoadingRow(**row) for row in payload["loadings"]),
        )


@dataclass(frozen=True)
class NeighborRow:
    cell_key: str
    #: (neighbour, distance) pairs in distance order.
    neighbours: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class NeighborProduct(Product):
    """The neighbour graph. Section 4.1: it is exactly what an h5ad-to-Seurat round trip loses."""

    schema_version: ClassVar[str] = "neighborhood_graph_v1"

    k: int
    metric: str
    seed: int
    embedding_source: str
    rows: tuple[NeighborRow, ...] = ()

    def __post_init__(self) -> None:
        too_many = [row.cell_key for row in self.rows if len(row.neighbours) > self.k]
        if too_many:
            raise ValueError(f"cells with more than k={self.k} neighbours: {too_many[:3]}")
        self_loops = [row.cell_key for row in self.rows if any(n == row.cell_key for n, _d in row.neighbours)]
        if self_loops:
            raise ValueError(f"a cell may not be its own neighbour: {self_loops[:3]}")

    @property
    def edge_count(self) -> int:
        return sum(len(row.neighbours) for row in self.rows)

    def payload(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "metric": self.metric,
            "seed": self.seed,
            "embedding_source": self.embedding_source,
            "edges": [
                {"cell_key": row.cell_key, "neighbours": [[n, d] for n, d in row.neighbours]} for row in self.rows
            ],
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        return {
            "n_cols": self.k,
            "n_rows": len(self.rows),
            "nnz": self.edge_count,
            "numeric_summaries": [("edges", self.edge_count), ("k", self.k), ("seed", self.seed)],
            "obs_index_hash": index_hash(row.cell_key for row in self.rows),
            "var_index_hash": index_hash(f"neighbour_{i}" for i in range(self.k)),
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "NeighborProduct":
        return cls(
            slot=slot,
            k=payload["k"],
            metric=payload["metric"],
            seed=payload["seed"],
            embedding_source=payload["embedding_source"],
            rows=tuple(
                NeighborRow(
                    cell_key=edge["cell_key"],
                    neighbours=tuple((str(n), float(d)) for n, d in edge["neighbours"]),
                )
                for edge in payload["edges"]
            ),
        )


@dataclass(frozen=True)
class CellLabelRow:
    cell_key: str
    cluster: str


@dataclass(frozen=True)
class ClusterProduct(Product):
    schema_version: ClassVar[str] = "cluster_labels_v1"

    method: str
    resolution: float
    seed: int
    graph_digest: str
    labels: tuple[CellLabelRow, ...]
    #: Modularity of the partition on the recorded graph - the honest stand-in for a Leiden score.
    modularity: float = 0.0
    #: Cells that ended up alone in a cluster, reported so an over-split check has a number.
    singleton_share: float = 0.0

    def __post_init__(self) -> None:
        if not self.labels:
            raise ValueError("clustering returned no cells")

    @property
    def clusters(self) -> tuple[str, ...]:
        return tuple(sorted({row.cluster for row in self.labels}))

    def cells_by_cluster(self) -> dict[str, tuple[str, ...]]:
        by_cluster: dict[str, list[str]] = {}
        for row in self.labels:
            by_cluster.setdefault(row.cluster, []).append(row.cell_key)
        return {cluster: tuple(sorted(keys)) for cluster, keys in sorted(by_cluster.items())}

    def payload(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "resolution": self.resolution,
            "seed": self.seed,
            "modularity": self.modularity,
            "graph_digest": self.graph_digest,
            "singleton_share": self.singleton_share,
            "n_clusters": len(self.clusters),
            "labels": _rows_as_dicts(self.labels),
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        return {
            "n_cols": 1,
            "n_rows": len(self.labels),
            "nnz": len(self.clusters),
            "numeric_summaries": [
                ("clusters", len(self.clusters)),
                ("modularity", round(self.modularity, 6)),
                ("seed", self.seed),
            ],
            "obs_index_hash": index_hash(row.cell_key for row in self.labels),
            "var_index_hash": index_hash(["cluster"]),
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "ClusterProduct":
        return cls(
            slot=slot,
            method=payload["method"],
            resolution=payload["resolution"],
            seed=payload["seed"],
            graph_digest=payload["graph_digest"],
            labels=tuple(CellLabelRow(**row) for row in payload["labels"]),
            modularity=payload["modularity"],
            singleton_share=payload["singleton_share"],
        )


@dataclass(frozen=True)
class MarkerRow:
    cluster: str
    gene: str
    rank: int
    mean_in: float
    mean_out: float
    detection_in: float
    detection_out: float
    log2fc: float


@dataclass(frozen=True)
class MarkerProduct(Product):
    """Section 7.8 item 5: this ranking is an ordering aid, never identity evidence by itself."""

    schema_version: ClassVar[str] = "markers_v1"

    method: str
    top_n: int
    cluster_digest: str
    rows: tuple[MarkerRow, ...]
    #: Ranking scores set this so a caller cannot mistake a rank for support.
    rank_only: bool = True

    def payload(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "top_n": self.top_n,
            "rank_only": self.rank_only,
            "cluster_digest": self.cluster_digest,
            "markers": _rows_as_dicts(self.rows),
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        return {
            "n_cols": 1,
            "n_rows": len(self.rows),
            "nnz": len(self.rows),
            "numeric_summaries": [("clusters", len({row.cluster for row in self.rows})), ("markers", len(self.rows))],
            "obs_index_hash": index_hash(f"{row.cluster}|{row.gene}" for row in self.rows),
            "var_index_hash": index_hash(["rank"]),
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "MarkerProduct":
        return cls(
            slot=slot,
            method=payload["method"],
            top_n=payload["top_n"],
            cluster_digest=payload["cluster_digest"],
            rows=tuple(MarkerRow(**row) for row in payload["markers"]),
            rank_only=payload["rank_only"],
        )


@dataclass(frozen=True)
class DeRow:
    gene: str
    group_a: str
    group_b: str
    log2fc: float
    p_value: float
    padj: float
    detection_a: float
    detection_b: float
    donors_tested: int
    donors_same_direction: int

    @property
    def donor_consistency(self) -> float:
        if self.donors_tested <= 0:
            return 0.0
        return self.donors_same_direction / self.donors_tested


@dataclass(frozen=True)
class DeProduct(Product):
    """Pseudobulk differential expression with the section 7.8 criteria carried beside the table."""

    schema_version: ClassVar[str] = "de_table_v1"

    method: str
    #: Section 7.8 item 4: the strategy is recorded, never implied.
    multiple_testing: str
    #: Which section 1.5 threshold set the support criteria will be read against.
    thresholds_version: str
    permutation_replicates: int
    seed: int
    #: Section 7.8 item 1: the unit of replication is the donor, not the cell.
    unit: str
    donors_per_group: Mapping[str, int]
    rows: tuple[DeRow, ...]
    contrasts: tuple[tuple[str, str], ...] = ()
    #: Per contrast: how many donors stood in each arm, how many were shared, and which test that
    #: made possible. Section 7.8 reads the design before it reads the p-values.
    contrast_details: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.multiple_testing:
            raise ValueError("section 7.8 item 4: the multiple-testing strategy must be recorded explicitly")
        if not self.thresholds_version:
            raise ValueError("a DE table must name the threshold set its support criteria will use")
        if self.unit != "donor":
            raise ValueError(f"pseudobulk unit is {self.unit!r}; section 7.8 tests biological replicates, not cells")
        if not self.donors_per_group:
            raise ValueError("a pseudobulk result must report how many donors backed each group")
        # Details are keyed by contrast name, which carries the cluster scope, so the pairing is done
        # on the arms the detail records rather than on a name rebuilt here.
        arms = {(str(detail.get("group_a")), str(detail.get("group_b"))) for detail in self.contrast_details.values()}
        missing = [pair for pair in self.contrasts if tuple(pair) not in arms]
        if missing:
            raise ValueError(f"contrasts without a recorded design: {sorted(missing)}")
        for row in self.rows:
            if not 0.0 <= row.p_value <= 1.0 or not 0.0 <= row.padj <= 1.0:
                raise ValueError(f"{row.gene}: p and padj must be probabilities")
            if row.donors_same_direction > row.donors_tested:
                raise ValueError(f"{row.gene}: more agreeing donors than tested")

    @property
    def tested_genes(self) -> int:
        return len(self.rows)

    def assess(self, thresholds: Thresholds, gene: str) -> dict[str, bool]:
        """Which section 7.8 identity criteria a gene meets; the numbers themselves are tool output."""
        row = next((r for r in self.rows if r.gene == gene), None)
        if row is None:
            raise KeyError(gene)
        return {
            "significant": row.padj <= thresholds.de_fdr_max,
            "effect_size": abs(row.log2fc) >= thresholds.de_min_abs_log2fc,
            "detected_in_group": row.detection_a >= thresholds.detection_frequency_in_min,
            "absent_in_excluded": row.detection_b <= thresholds.detection_frequency_excluded_max,
            "donor_consistent": row.donor_consistency >= thresholds.donor_consistency_min,
            "powered": all(n >= thresholds.min_n_per_group for n in self.donors_per_group.values()),
        }

    def supporting(self, thresholds: Thresholds) -> tuple[str, ...]:
        return tuple(sorted(gene for gene in (r.gene for r in self.rows) if all(self.assess(thresholds, gene).values())))

    def payload(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "multiple_testing": self.multiple_testing,
            "thresholds_version": self.thresholds_version,
            "permutation_replicates": self.permutation_replicates,
            "seed": self.seed,
            "unit": self.unit,
            "contrasts": [[a, b] for a, b in self.contrasts],
            "contrast_details": {
                name: dict(sorted(detail.items())) for name, detail in sorted(self.contrast_details.items())
            },
            "donors_per_group": dict(sorted(self.donors_per_group.items())),
            "table": _rows_as_dicts(self.rows),
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        return {
            "n_cols": 1,
            "n_rows": len(self.rows),
            "nnz": len(self.rows),
            "numeric_summaries": [
                ("replicates", self.permutation_replicates),
                ("seed", self.seed),
                ("tested_genes", self.tested_genes),
            ],
            "obs_index_hash": index_hash(f"{r.group_a}|{r.group_b}|{r.gene}" for r in self.rows),
            "var_index_hash": index_hash(["log2fc", "padj"]),
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "DeProduct":
        return cls(
            slot=slot,
            method=payload["method"],
            multiple_testing=payload["multiple_testing"],
            thresholds_version=payload["thresholds_version"],
            permutation_replicates=payload["permutation_replicates"],
            seed=payload["seed"],
            unit=payload["unit"],
            donors_per_group={str(group): int(n) for group, n in payload["donors_per_group"].items()},
            rows=tuple(DeRow(**row) for row in payload["table"]),
            contrasts=tuple(tuple(pair) for pair in payload["contrasts"]),
            contrast_details={name: dict(detail) for name, detail in sorted(payload["contrast_details"].items())},
        )


@dataclass(frozen=True)
class CellScoreRow:
    cell_key: str
    score: float


@dataclass(frozen=True)
class CellScoreProduct(Product):
    """A per-cell score computed by an upstream screen (doublet, ambient RNA).

    Section 7.1 step 3 reads these but does not produce them: scrublet and the decontamination tools
    are separate registered tools with their own assumptions. Carrying the score as a product rather
    than as a node parameter keeps the QC step's parameter hash small and makes the screen's origin a
    lineage edge, so a QC result can be read back to the tool and version that scored each cell.
    """

    schema_version: ClassVar[str] = "cell_score_v1"

    metric: str
    source_tool: str
    source_tool_version: str
    rows: tuple[CellScoreRow, ...]
    #: What the screen's model assumed, copied from its own ToolSpec so QC can be read without the registry.
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.metric:
            raise ValueError("a score product must name the metric it holds")
        if not self.rows:
            raise ValueError(f"{self.slot} scored no cells")
        if not self.source_tool or not self.source_tool_version:
            raise ValueError(f"{self.metric} scores must name the tool and version that produced them")
        keys = [row.cell_key for row in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.metric} scores repeat a cell key")
        for row in self.rows:
            if not 0.0 <= row.score <= 1.0:
                raise ValueError(f"{row.cell_key}: {self.metric} score {row.score} is not a probability")

    def scores(self) -> dict[str, float]:
        return {row.cell_key: row.score for row in self.rows}

    def payload(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "source_tool": self.source_tool,
            "source_tool_version": self.source_tool_version,
            "assumptions": list(self.assumptions),
            "scores": [{"cell_key": row.cell_key, "score": row.score} for row in self.rows],
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        return {
            "n_cols": 1,
            "n_rows": len(self.rows),
            "nnz": sum(1 for row in self.rows if quantize(row.score) > 0),
            "numeric_summaries": [
                ("mean_score", round(sum(quantize(row.score) for row in self.rows) / len(self.rows), 6))
            ],
            "obs_index_hash": index_hash(row.cell_key for row in self.rows),
            "var_index_hash": index_hash([self.metric]),
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "CellScoreProduct":
        return cls(
            slot=slot,
            metric=payload["metric"],
            source_tool=payload["source_tool"],
            source_tool_version=payload["source_tool_version"],
            rows=tuple(CellScoreRow(**row) for row in payload["scores"]),
            assumptions=tuple(payload["assumptions"]),
        )


#: Stability criteria a stored product can be re-read against, named the way the product stores them.
STABILITY_CRITERIA_KEYS = ("cc_ari_min", "jaccard_min", "pass_ratio_min", "small_cluster_max_cells")


@dataclass(frozen=True)
class StabilityArmRow:
    """One candidate cluster measured under one perturbation replicate."""

    kind: str
    arm: str
    baseline_cluster: str
    #: Cells the candidate held in the baseline partition.
    baseline_cells: int
    #: Cells it still has where this arm's partition was computed - zero means the set vanished.
    cells_compared: int
    #: Partition-level chance-corrected agreement for the whole arm, recomputed over shared cells.
    cc_ari: Optional[float]
    #: Overlap of the candidate against its best-matching cluster in the perturbed partition.
    jaccard: Optional[float]
    #: Same-size random-set null for that overlap, so a small cluster is read against chance (§7.5).
    permutation_jaccard: Optional[float]
    #: Top-marker gene-set overlap between the candidate and its match.
    marker_overlap: Optional[float]
    matched_cluster: Optional[str]
    matched_cells: int = 0

    @property
    def vanished(self) -> bool:
        return self.cells_compared == 0


@dataclass(frozen=True)
class StabilityProduct(Product):
    """Section 7.5: the perturbation numbers a candidate survived, as the tool measured them.

    Only measurements and the criteria the step applied are stored. Whether the candidate is *stable*
    is derived, so no driver - and no model writing a card - can put "stable" into an asset that does
    not support it; a reviewer re-reads the same rows and gets the same verdict.
    """

    schema_version: ClassVar[str] = "stability_v1"

    protocol_id: str
    protocol_version: str
    thresholds_version: str
    #: The partition every arm is compared against, named by digest rather than described.
    baseline_clusters_digest: str
    baseline_expression_digest: str
    #: Section 1.5 numbers actually applied, plus the small-cluster cut drawn from ``rare_min_cells``.
    criteria: Mapping[str, float]
    #: Perturbation classes the protocol ran. ``omitted_arms`` records what a deviation gave up.
    covered_kinds: tuple[str, ...]
    permutation_replicates: int
    seed: int
    omitted_arms: tuple[str, ...] = ()
    deviation: Optional[str] = None
    #: Baseline cluster id -> cell count, so a card can show what the candidate was.
    baseline_sizes: Mapping[str, int] = field(default_factory=dict)
    rows: tuple[StabilityArmRow, ...] = ()

    def __post_init__(self) -> None:
        missing = sorted(key for key in STABILITY_CRITERIA_KEYS if key not in self.criteria)
        if missing:
            raise ValueError(f"stability product has no recorded value for {missing}")
        for key in ("cc_ari_min", "jaccard_min", "pass_ratio_min"):
            value = float(self.criteria[key])
            if not -1.0 <= value <= 1.0:
                raise ValueError(f"criteria[{key!r}] = {value} is outside the range the metric can take")
        if self.baseline_sizes:
            for name in sorted({row.baseline_cluster for row in self.rows} - set(self.baseline_sizes)):
                raise ValueError(f"cluster {name!r} was judged but the baseline partition does not name it")
        if not self.rows:
            raise ValueError("a stability product with no perturbation rows records no stability evidence")
        if not self.covered_kinds:
            raise ValueError("a stability product must name the perturbation classes it covers")
        seen: set[tuple[str, str, str]] = set()
        for row in self.rows:
            key = (row.kind, row.arm, row.baseline_cluster)
            if key in seen:
                raise ValueError(f"perturbation {key} is recorded twice; medians would count it twice")
            seen.add(key)
            if row.baseline_cells < 1:
                raise ValueError(f"{row.arm}/{row.baseline_cluster}: a candidate with no cells is not a candidate")
            if row.cc_ari is not None and not -1.0 <= row.cc_ari <= 1.0:
                raise ValueError(f"{row.arm}: chance-corrected agreement {row.cc_ari} is not an index")
            for name in ("jaccard", "permutation_jaccard", "marker_overlap"):
                value = getattr(row, name)
                if value is not None and not 0.0 <= value <= 1.0:
                    raise ValueError(f"{row.arm}: {name} {value} is not a set overlap")
            if row.cells_compared == 0 and row.jaccard is not None:
                raise ValueError(f"{row.arm}: nothing was compared, so no overlap may be recorded")

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted({row.kind for row in self.rows}))

    @property
    def small_cluster_max_cells(self) -> int:
        return int(self.criteria["small_cluster_max_cells"])

    def rows_for(self, kind: str) -> tuple[StabilityArmRow, ...]:
        return tuple(row for row in self.rows if row.kind == kind)

    def _median(self, rows: Sequence[StabilityArmRow], attribute: str) -> Optional[float]:
        """The median over the values as they are stored, so a product re-read from the asset store
        judges the same numbers the step wrote and the fingerprint does not move on a reread."""
        values = [
            quantize(value) for row in rows if (value := getattr(row, attribute)) is not None
        ]
        return median(values) if values else None

    def judgement(self, kind: str) -> str:
        """What the recorded numbers say about one perturbation class, against the recorded criteria."""
        rows = self.rows_for(kind)
        if not rows:
            return "absent"
        if any(row.vanished for row in rows):
            return "fail"
        ari = self._median(rows, "cc_ari")
        jac = self._median(rows, "jaccard")
        if ari is None or jac is None:
            return "unmeasurable"
        if ari < float(self.criteria["cc_ari_min"]) or jac < float(self.criteria["jaccard_min"]):
            return "fail"
        small = [row for row in rows if row.baseline_cells <= self.small_cluster_max_cells]
        if small:
            null = self._median(small, "permutation_jaccard")
            if null is None:
                return "unmeasurable"
            if (self._median(small, "jaccard") or 0.0) <= null:
                return "fail"
        return "pass"

    @property
    def passed_kinds(self) -> tuple[str, ...]:
        return tuple(kind for kind in self.kinds if self.judgement(kind) == "pass")

    @property
    def unmeasurable_kinds(self) -> tuple[str, ...]:
        return tuple(kind for kind in self.kinds if self.judgement(kind) == "unmeasurable")

    @property
    def agreement_ratio(self) -> float:
        return len(self.passed_kinds) / len(self.kinds) if self.kinds else 0.0

    @property
    def stable(self) -> bool:
        return self.agreement_ratio >= float(self.criteria["pass_ratio_min"])

    @property
    def protocol_complete(self) -> bool:
        return not self.omitted_arms

    def verdict(self) -> str:
        """One line for a card, always with the reason rather than a bare verdict word."""
        if self.stable and self.protocol_complete:
            return "stable"
        if self.stable:
            return f"stable over {len(self.kinds)} classes, with a recorded deviation: {list(self.omitted_arms)}"
        return (
            f"not stable: {len(self.passed_kinds)}/{len(self.kinds)} classes met the criteria; "
            f"failed {sorted(set(self.kinds) - set(self.passed_kinds) - set(self.unmeasurable_kinds))}, "
            f"unmeasurable {list(self.unmeasurable_kinds)}"
        )

    def payload(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "protocol_version": self.protocol_version,
            "thresholds_version": self.thresholds_version,
            "baseline_clusters_digest": self.baseline_clusters_digest,
            "baseline_expression_digest": self.baseline_expression_digest,
            "criteria": {key: float(self.criteria[key]) for key in sorted(self.criteria)},
            "covered_kinds": list(self.covered_kinds),
            "omitted_arms": list(self.omitted_arms),
            "deviation": self.deviation,
            "permutation_replicates": self.permutation_replicates,
            "seed": self.seed,
            "baseline_sizes": dict(sorted(self.baseline_sizes.items())),
            "arms": _rows_as_dicts(self.rows),
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        return {
            "n_cols": 2,
            "n_rows": len(self.rows),
            "nnz": sum(1 for row in self.rows if row.jaccard is not None),
            "numeric_summaries": [
                ("arms", len(self.rows)),
                ("classes", len(self.kinds)),
                ("passed_classes", len(self.passed_kinds)),
                ("median_jaccard", round(self._median(self.rows, "jaccard") or 0.0, 6)),
                ("median_cc_ari", round(self._median(self.rows, "cc_ari") or 0.0, 6)),
            ],
            "obs_index_hash": index_hash(f"{row.kind}|{row.arm}|{row.baseline_cluster}" for row in self.rows),
            "var_index_hash": index_hash(["cc_ari", "jaccard"]),
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "StabilityProduct":
        return cls(
            slot=slot,
            protocol_id=payload["protocol_id"],
            protocol_version=payload["protocol_version"],
            thresholds_version=payload["thresholds_version"],
            baseline_clusters_digest=payload["baseline_clusters_digest"],
            baseline_expression_digest=payload["baseline_expression_digest"],
            criteria={key: float(value) for key, value in payload["criteria"].items()},
            covered_kinds=tuple(payload["covered_kinds"]),
            omitted_arms=tuple(payload["omitted_arms"]),
            deviation=payload["deviation"],
            permutation_replicates=int(payload["permutation_replicates"]),
            seed=int(payload["seed"]),
            baseline_sizes={str(key): int(value) for key, value in payload["baseline_sizes"].items()},
            rows=tuple(StabilityArmRow(**row) for row in payload["arms"]),
        )


#: The coverage criteria a stored product can be re-read against, named the way the product stores them.
COVERAGE_CRITERIA_KEYS = ("knn", "distance_ratio_min")


@dataclass(frozen=True)
class CoverageRow:
    """One cell's distance to the reference, beside the same measurement on a calibration cell."""

    cell_key: str
    #: Mean distance to its ``k`` nearest reference cells, in the embedding's own coordinates.
    distance: float
    #: Where that distance sits inside the calibration set's empirical distribution, as a percentile.
    calibration_percentile: float
    nearest_reference: str


@dataclass(frozen=True)
class CoverageProduct(Product):
    """Section 7.6: how far a candidate sits from the reference, measured against normal reference cells.

    This is a *coverage* measurement and deliberately not a novelty claim. Section 7.6 requires the two
    to stay separate fields: coverage says where the cells sit relative to a reference, novelty is a
    verdict over four conditions of which coverage is only one. So nothing here can be read as saying
    that a new cell type was found, and no field stores the word.
    """

    schema_version: ClassVar[str] = "coverage_v1"

    method: str
    #: The embedding these distances were measured in, named by digest rather than described.
    embedding_digest: str
    reference_digest: str
    criteria: Mapping[str, float]
    thresholds_version: str
    #: Reference cells the query is measured against.
    reference_cells: int
    #: The calibration set: reference cells whose own leave-one-out distances define "normal".
    calibration_cells: int
    #: Distance quantiles of the two distributions the ratio is read from, because the plan asks for the
    #: distribution rather than one number.
    calibration_quantiles: Mapping[str, float]
    candidate_quantiles: Mapping[str, float]
    median_candidate_distance: float
    median_calibration_distance: float
    rows: tuple[CoverageRow, ...]

    def __post_init__(self) -> None:
        missing = sorted(key for key in COVERAGE_CRITERIA_KEYS if key not in self.criteria)
        if missing:
            raise ValueError(f"coverage product has no recorded value for {missing}")
        if self.criteria["knn"] < 1:
            raise ValueError("coverage needs at least one reference neighbour")
        if self.criteria["distance_ratio_min"] < 1.0:
            raise ValueError("a distance ratio ceiling below 1.0 would call any distance a gap")
        if not self.rows:
            raise ValueError("a coverage product with no cells measured records no coverage")
        if self.reference_cells < 2 or self.calibration_cells < 2:
            raise ValueError("coverage needs reference cells and a calibration set drawn from them")
        keys = [row.cell_key for row in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError("coverage measured a cell twice")
        for name in ("calibration_quantiles", "candidate_quantiles"):
            values = getattr(self, name)
            if not values:
                raise ValueError(f"{name} must hold the distribution the ratio was read from")
            if any(value < 0.0 for value in values.values()):
                raise ValueError(f"{name}: a distance cannot be negative")
        for row in self.rows:
            if row.distance < 0.0:
                raise ValueError(f"{row.cell_key}: distance {row.distance} is not a distance")
            if not 0.0 <= row.calibration_percentile <= 1.0:
                raise ValueError(f"{row.cell_key}: calibration percentile {row.calibration_percentile} is not one")

    @property
    def distance_ratio(self) -> float:
        """How many times further the candidate sits than a normal reference cell does."""
        if self.median_calibration_distance <= 0.0:
            raise ValueError(
                "the calibration set's median distance is zero, so no ratio against it means anything: "
                "the calibration cells are the query cells"
            )
        return self.median_candidate_distance / self.median_calibration_distance

    @property
    def coverage_gap(self) -> bool:
        """Derived from the stored ratio and the stored criterion, never written by a caller."""
        return self.distance_ratio >= float(self.criteria["distance_ratio_min"])

    def uncovered_share(self) -> float:
        return sum(1 for row in self.rows if row.calibration_percentile >= 1.0) / len(self.rows)

    def payload(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "embedding_digest": self.embedding_digest,
            "reference_digest": self.reference_digest,
            "criteria": {key: float(self.criteria[key]) for key in sorted(self.criteria)},
            "thresholds_version": self.thresholds_version,
            "reference_cells": self.reference_cells,
            "calibration_cells": self.calibration_cells,
            "calibration_quantiles": {
                key: self.calibration_quantiles[key] for key in sorted(self.calibration_quantiles)
            },
            "candidate_quantiles": {key: self.candidate_quantiles[key] for key in sorted(self.candidate_quantiles)},
            "median_candidate_distance": self.median_candidate_distance,
            "median_calibration_distance": self.median_calibration_distance,
            "rows": _rows_as_dicts(self.rows),
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        return {
            "n_cols": 2,
            "n_rows": len(self.rows),
            "nnz": sum(1 for row in self.rows if quantize(row.distance) > 0),
            "numeric_summaries": [
                ("cells", len(self.rows)),
                ("median_distance", round(quantize(self.median_candidate_distance), 6)),
                ("median_calibration", round(quantize(self.median_calibration_distance), 6)),
            ],
            "obs_index_hash": index_hash(row.cell_key for row in self.rows),
            "var_index_hash": index_hash(["distance", "calibration_percentile"]),
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "CoverageProduct":
        return cls(
            slot=slot,
            method=payload["method"],
            embedding_digest=payload["embedding_digest"],
            reference_digest=payload["reference_digest"],
            criteria={key: float(value) for key, value in payload["criteria"].items()},
            thresholds_version=payload["thresholds_version"],
            reference_cells=int(payload["reference_cells"]),
            calibration_cells=int(payload["calibration_cells"]),
            calibration_quantiles={key: float(value) for key, value in payload["calibration_quantiles"].items()},
            candidate_quantiles={key: float(value) for key, value in payload["candidate_quantiles"].items()},
            median_candidate_distance=float(payload["median_candidate_distance"]),
            median_calibration_distance=float(payload["median_calibration_distance"]),
            rows=tuple(CoverageRow(**row) for row in payload["rows"]),
        )


#: The continuous-state criteria a stored product can be re-read against.
STATE_AXIS_CRITERIA_KEYS = ("tail_quantile", "tail_share_min", "axis_explained_min")


@dataclass(frozen=True)
class StateAxisRow:
    cell_key: str
    projection: float
    #: The projection in units of the reference's own spread along the axis.
    z: float
    beyond_reference_tail: bool


@dataclass(frozen=True)
class StateAxisProduct(Product):
    """Section 7.6's continuous-state check: is the candidate only the end of an axis the data already has?

    The MVP method is a marker gradient, stated as such in ``method``: the axis is a signed gene set the
    project declared, so the measurement answers a question a reviewer can read. ``is_axis_tail`` is
    derived from the two recorded shares against the two recorded cuts, which makes it recomputable and
    not writable.
    """

    schema_version: ClassVar[str] = "state_axis_v1"

    method: str
    #: The declared axis: gene -> signed weight, sorted so the bytes do not depend on insertion order.
    axis: Mapping[str, float]
    expression_digest: str
    cluster_digest: str
    criteria: Mapping[str, float]
    reference_cells: int
    candidate_cells: int
    #: Cosine between the candidate's mean deviation from the reference and the declared axis.
    axis_explained: float
    #: Share of candidate cells beyond the reference's own tail on the axis.
    tail_share: float
    reference_median: float
    reference_stdev: float
    max_abs_z: float
    rows: tuple[StateAxisRow, ...]

    def __post_init__(self) -> None:
        missing = sorted(key for key in STATE_AXIS_CRITERIA_KEYS if key not in self.criteria)
        if missing:
            raise ValueError(f"state axis product has no recorded value for {missing}")
        if not self.axis:
            raise ValueError("a continuous-state check must name the axis it measured")
        if all(weight == 0.0 for weight in self.axis.values()):
            raise ValueError("an all-zero axis projects every cell onto the same point")
        if not self.rows:
            raise ValueError("a state axis product that measured no cells answers nothing")
        if self.reference_cells < 2:
            raise ValueError("the reference spread needs at least two cells")
        if not 0.0 <= self.axis_explained <= 1.0:
            raise ValueError(f"axis_explained {self.axis_explained} is not a cosine")
        if not 0.0 <= self.tail_share <= 1.0:
            raise ValueError(f"tail_share {self.tail_share} is not a share")
        if self.reference_stdev <= 0.0:
            raise ValueError("the reference shows no spread along this axis, so no tail can be measured")
        keys = [row.cell_key for row in self.rows]
        if len(set(keys)) != len(keys):
            raise ValueError("state axis measured a cell twice")

    @property
    def is_axis_tail(self) -> bool:
        """Derived: both recorded shares at or past the recorded cuts, or the answer is not "tail"."""
        return (
            self.tail_share >= float(self.criteria["tail_share_min"])
            and self.axis_explained >= float(self.criteria["axis_explained_min"])
        )

    def payload(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "axis": {gene: float(self.axis[gene]) for gene in sorted(self.axis)},
            "expression_digest": self.expression_digest,
            "cluster_digest": self.cluster_digest,
            "criteria": {key: float(self.criteria[key]) for key in sorted(self.criteria)},
            "reference_cells": self.reference_cells,
            "candidate_cells": self.candidate_cells,
            "axis_explained": self.axis_explained,
            "tail_share": self.tail_share,
            "reference_median": self.reference_median,
            "reference_stdev": self.reference_stdev,
            "max_abs_z": self.max_abs_z,
            "rows": _rows_as_dicts(self.rows),
        }

    def fingerprint_parts(self) -> dict[str, Any]:
        return {
            "n_cols": 2,
            "n_rows": len(self.rows),
            "nnz": sum(1 for row in self.rows if quantize(row.projection) > 0),
            "numeric_summaries": [
                ("cells", len(self.rows)),
                ("axis_explained", round(quantize(self.axis_explained), 6)),
                ("tail_share", round(quantize(self.tail_share), 6)),
            ],
            "obs_index_hash": index_hash(row.cell_key for row in self.rows),
            "var_index_hash": index_hash(["projection", "z"]),
        }

    @classmethod
    def from_payload(cls, slot: str, payload: Mapping[str, Any]) -> "StateAxisProduct":
        return cls(
            slot=slot,
            method=payload["method"],
            axis={str(gene): float(weight) for gene, weight in sorted(payload["axis"].items())},
            expression_digest=payload["expression_digest"],
            cluster_digest=payload["cluster_digest"],
            criteria={key: float(value) for key, value in payload["criteria"].items()},
            reference_cells=int(payload["reference_cells"]),
            candidate_cells=int(payload["candidate_cells"]),
            axis_explained=float(payload["axis_explained"]),
            tail_share=float(payload["tail_share"]),
            reference_median=float(payload["reference_median"]),
            reference_stdev=float(payload["reference_stdev"]),
            max_abs_z=float(payload["max_abs_z"]),
            rows=tuple(StateAxisRow(**row) for row in payload["rows"]),
        )


def read_product(schema_version: str, raw: bytes) -> Product:
    """Rebuild a stored product, insisting that the index row and the bytes name the same contract.

    A driver that read a blob and then guessed which of its own products it was holding would be
    making exactly the assumption section 4.1 exists to forbid, so both names have to agree.
    """
    document = json.loads(raw)
    stored = str(document.get("schema_version", ""))
    if stored != schema_version:
        raise ValueError(f"asset is indexed as {schema_version!r} but its bytes declare {stored!r}")
    cls = PRODUCT_SCHEMAS.get(schema_version)
    if cls is None:
        raise KeyError(f"no product class for schema_version {schema_version!r}")
    return cls.from_payload(str(document["slot"]), document)


#: Output contract name -> product class, used by the wrapper to check what a node emitted.
PRODUCT_SCHEMAS: dict[str, type[Product]] = {
    cls.schema_version: cls
    for cls in (
        QcProduct,
        ExpressionProduct,
        FeatureSelectionProduct,
        EmbeddingProduct,
        NeighborProduct,
        ClusterProduct,
        MarkerProduct,
        DeProduct,
        CellScoreProduct,
        StabilityProduct,
        CoverageProduct,
        StateAxisProduct,
    )
}
