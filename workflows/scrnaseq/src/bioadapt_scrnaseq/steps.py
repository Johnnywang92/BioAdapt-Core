"""The reference implementation of the scRNA-seq steps (plan V2.0 sections 7.1, 7.7, 7.8).

Two rules shape this module. Every number that section 7 or appendix C1 marks as a stage-0 placeholder
is an explicit argument, so nothing here can be "the default" that quietly gates a real project. And
each step takes the upstream *product*, not a bare value, because the output has to name the digest of
the graph or clustering it was computed over: a stability or support number that cannot point back at
its input is repeatable, not reproducible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Optional, Sequence

from bioadapt_policies.qc_policy import QcAction, QcMetric, QcPolicy
from bioadapt_schemas.enums import ScreeningResult
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.thresholds import Thresholds
from bioadapt_tool_registry.result import ValidationOutcome

from .errors import StepFailure, critical_quality, invalid_input, missing_parameter
from .matrix import CountMatrix, convert
from .numeric import (
    Rng,
    benjamini_hochberg,
    build_adjacency,
    explained_variance_ratio,
    greedy_modularity_partition,
    mean,
    median,
    modularity,
    permutation_p_value,
    principal_components,
    stdev,
)
from .products import (
    CellLabelRow,
    CellQcRow,
    CellVectorRow,
    ClusterProduct,
    DeProduct,
    DeRow,
    EmbeddingProduct,
    ExpressionProduct,
    FeatureSelectionProduct,
    GeneLoadingRow,
    GeneStatRow,
    MarkerProduct,
    MarkerRow,
    NeighborProduct,
    NeighborRow,
    Product,
    QcProduct,
    QcRuleSummary,
)

SLOT_QC_FLAGS = "qc_flags"
SLOT_COUNTS = "counts"
SLOT_EXPRESSION = "expression"
SLOT_FEATURES = "features"
SLOT_EMBEDDING = "embedding"
SLOT_GRAPH = "graph"
SLOT_CLUSTERS = "clusters"
SLOT_MARKERS = "markers"
SLOT_DE_TABLE = "de_table"

SENSITIVITY_BASELINE = "unfiltered_baseline"
SENSITIVITY_EXCLUDING_MARKED = "risk_marked_excluded"


@dataclass(frozen=True)
class StepOutput:
    products: Mapping[str, Product]
    metrics: Mapping[str, float]
    checks: tuple[ValidationOutcome, ...] = ()
    warnings: tuple[str, ...] = ()

    def product(self, slot: str) -> Product:
        try:
            return self.products[slot]
        except KeyError:
            raise StepFailure(
                code="output_missing",
                error_class=ErrorClass.FAILED,
                message=f"step produced no {slot!r} output",
                field=slot,
            ) from None


def check(name: str, result: ScreeningResult, detail: Optional[str] = None) -> ValidationOutcome:
    return ValidationOutcome(name=name, result=result, detail=detail)


def _pass(name: str, detail: str) -> ValidationOutcome:
    return check(name, ScreeningResult.PASS, detail)


def _warn(name: str, detail: str) -> ValidationOutcome:
    return check(name, ScreeningResult.WARN, detail)


def _require(value: Optional[Any], name: str, *, where: str) -> Any:
    if value is None:
        raise missing_parameter(name, where=where)
    return value


def _values_at(matrix: CountMatrix, gene: str) -> list[float]:
    column = matrix.column(gene)
    return [column.get(cell.cell_key, 0.0) for cell in matrix.cells]


def _scenario(numbers: Sequence[CellQcRow], donor_of: Mapping[str, str]) -> dict[str, float]:
    mito = [row.mitochondrial_fraction for row in numbers if row.mitochondrial_fraction is not None]
    return {
        "cells": float(len(numbers)),
        "donors": float(len({donor_of[row.cell_key] for row in numbers})),
        "flagged_cells": float(sum(1 for row in numbers if row.flags)),
        "median_total_counts": median([row.total_counts for row in numbers]) if numbers else 0.0,
        "median_detected_genes": median([float(row.detected_genes) for row in numbers]) if numbers else 0.0,
        "median_mito_fraction": median(mito) if mito else 0.0,
        "mito_measured_cells": float(len(mito)),
    }


def _sensitivity(rows: Sequence[CellQcRow], matrix: CountMatrix) -> dict[str, dict[str, float]]:
    """Section 7.1 step 3: the unfiltered baseline runs beside the marked view, never instead of it."""
    donor_of = {cell.cell_key: cell.donor_id for cell in matrix.cells}
    baseline = _scenario(rows, donor_of)
    clean = [row for row in rows if not row.flags]
    return {SENSITIVITY_BASELINE: baseline, SENSITIVITY_EXCLUDING_MARKED: _scenario(clean, donor_of)}


def _metric_value(metric: QcMetric, row: CellQcRow) -> Optional[float]:
    if metric is QcMetric.TOTAL_COUNTS:
        return row.total_counts
    if metric is QcMetric.DETECTED_GENES:
        return float(row.detected_genes)
    if metric is QcMetric.MITOCHONDRIAL_FRACTION:
        return row.mitochondrial_fraction
    if metric is QcMetric.RIBOSOMAL_FRACTION:
        return row.ribosomal_fraction
    if metric is QcMetric.DOUBLET_SCORE:
        return row.doublet_score
    if metric is QcMetric.AMBIENT_SCORE:
        return row.ambient_score
    raise invalid_input(f"metric {metric} has no value on a QC row", field="metric")


def qc_step(
    matrix: CountMatrix,
    policy: QcPolicy,
    *,
    thresholds_version: Optional[str] = None,
    max_flagged_share: Optional[float] = None,
    mito_genes: Optional[Sequence[str]] = None,
    ribosomal_genes: Optional[Sequence[str]] = None,
    doublet_scores: Optional[Mapping[str, float]] = None,
    ambient_scores: Optional[Mapping[str, float]] = None,
) -> StepOutput:
    """Apply a versioned QC policy: label cells at risk, keep them, and report the sensitivity contrast."""
    version = str(_require(thresholds_version, "thresholds_version", where="qc"))
    ceiling = float(_require(max_flagged_share, "max_flagged_share", where="qc"))
    if not 0.0 <= ceiling <= 1.0:
        raise invalid_input("max_flagged_share must be a share between 0 and 1", field="max_flagged_share")
    mito = set(mito_genes or ())
    ribosomal = set(ribosomal_genes or ())
    unknown_mito = sorted(mito - set(matrix.genes))
    if unknown_mito:
        raise invalid_input(f"mitochondrial gene set names genes outside the matrix: {unknown_mito[:5]}", field="mito_genes")
    unknown_ribosomal = sorted(ribosomal - set(matrix.genes))
    if unknown_ribosomal:
        raise invalid_input(f"ribosomal gene set names genes outside the matrix: {unknown_ribosomal[:5]}", field="ribosomal_genes")

    rows: list[CellQcRow] = []
    flagged_by_rule = {rule.name: 0 for rule in policy.rules}
    unevaluated: list[str] = []
    dropped: list[str] = []
    for cell in matrix.cells:
        base = CellQcRow(
            cell_key=cell.cell_key,
            total_counts=matrix.total_counts(cell.cell_key),
            detected_genes=matrix.detected_genes(cell.cell_key),
            mitochondrial_fraction=matrix.fraction_of(cell.cell_key, mito) if mito else None,
            ribosomal_fraction=matrix.fraction_of(cell.cell_key, ribosomal) if ribosomal else None,
            doublet_score=(doublet_scores or {}).get(cell.cell_key),
            ambient_score=(ambient_scores or {}).get(cell.cell_key),
        )
        fired: list[str] = []
        for rule in policy.rules:
            value = _metric_value(rule.metric, base)
            if value is None:
                if rule.name not in unevaluated:
                    unevaluated.append(rule.name)
                continue
            if rule.flags(value):
                fired.append(rule.name)
                flagged_by_rule[rule.name] += 1
                if rule.action is QcAction.DROP and cell.cell_key not in dropped:
                    dropped.append(cell.cell_key)
        rows.append(replace(base, flags=tuple(sorted(fired))))

    marked = sum(1 for row in rows if row.flags)
    share = marked / len(rows) if rows else 0.0
    if share > ceiling:
        raise critical_quality(
            f"{marked}/{len(rows)} cells ({share:.3f}) carry a risk label, above the agreed ceiling {ceiling}; "
            "the library is not analysable as supplied"
        )

    removed = set(dropped)
    kept_matrix = matrix.subset(cells=[row.cell_key for row in rows if row.cell_key not in removed]) if removed else matrix
    checks = [
        _pass(
            "qc_marks_by_default",
            "every rule marks a cell; no cell was removed"
            if policy.marks_only()
            else f"{len(dropped)} cells removed by {[rule.name for rule in policy.drop_rules()]}, approved by "
            f"{policy.cell_deletion_approved_by} and annotated in the report",
        ),
        _pass("qc_sensitivity_reported", f"{SENSITIVITY_BASELINE} reported beside {SENSITIVITY_EXCLUDING_MARKED}"),
        _pass("qc_flagged_share_within_ceiling", f"{share:.3f} at or below the ceiling {ceiling}"),
    ]
    if unevaluated:
        checks.append(
            _warn(
                "qc_rules_not_evaluated",
                f"rules {sorted(unevaluated)} had no value for these cells; the metric is absent from the input, "
                "which is not the same as being below the bound",
            )
        )
    if policy.lint():
        checks.append(_warn("qc_policy_lint", "; ".join(policy.lint())))

    product = QcProduct(
        slot=SLOT_QC_FLAGS,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        thresholds_version=version,
        actions=("mark",) if policy.marks_only() else ("mark", "drop"),
        rules=tuple(
            QcRuleSummary(
                name=rule.name,
                metric=str(rule.metric),
                action=str(rule.action),
                bound=rule.bound(),
                threshold_source=str(rule.threshold_source),
                source_ref=rule.source_ref,
                flagged=flagged_by_rule[rule.name],
            )
            for rule in sorted(policy.rules, key=lambda item: item.name)
        ),
        rows=tuple(rows),
        dropped=tuple(sorted(dropped)),
        cell_deletion_approved_by=policy.cell_deletion_approved_by,
        deletion_annotated_in_report=policy.deletion_annotated_in_report,
        sensitivity=_sensitivity(rows, matrix),
    )
    expression = ExpressionProduct(
        slot=SLOT_COUNTS,
        matrix=kept_matrix,
        transform="counts",
        parameters={"max_flagged_share": ceiling, "policy": f"{policy.policy_id}@{policy.version}"},
    )
    return StepOutput(
        products={SLOT_QC_FLAGS: product, SLOT_COUNTS: expression},
        metrics={
            "cells": float(len(rows)),
            "marked_cells": float(marked),
            "flagged_share": share,
            "dropped_cells": float(len(dropped)),
            "median_total_counts": median([row.total_counts for row in rows]) if rows else 0.0,
            "rules": float(len(policy.rules)),
        },
        checks=tuple(checks),
    )


def normalize_step(
    matrix: CountMatrix,
    *,
    target_sum: Optional[float],
    tolerance: float = 1e-6,
) -> StepOutput:
    """Library-size normalisation followed by log2(1+x), so a mean difference is a log2 fold change."""
    target = float(_require(target_sum, "target_sum", where="normalize"))
    if target <= 0:
        raise invalid_input("target_sum must be positive", field="target_sum")
    scaled: dict[str, dict[str, float]] = {}
    drift = 0.0
    largest = 0.0
    for cell in matrix.cells:
        row = matrix.row(cell.cell_key)
        total = sum(row.values())
        if total <= 0:
            scaled[cell.cell_key] = {}
            continue
        scaled[cell.cell_key] = {}
        for gene, value in row.items():
            transformed = math.log2(1.0 + value * (target / total))
            scaled[cell.cell_key][gene] = transformed
            largest = max(largest, transformed)
        back = sum(2.0 ** t - 1.0 for t in scaled[cell.cell_key].values()) * (total / target)
        drift = max(drift, abs(back - total) / total)
    normalized = CountMatrix.build(cells=matrix.cells, counts=scaled, genome=matrix.genome)
    warning = convert("counts", "log1p", both_sides_kept=True).as_warning()
    detail = (
        f"the log2 round trip recovers each library size to within {drift:.3e} relative error "
        f"(tolerance {tolerance:.1e})"
    )
    checks = [
        _pass("log2_transform", "values are log2(1 + count * target_sum / library_size)"),
        _warn("library_size_normalised", detail) if drift > tolerance else _pass("library_size_normalised", detail),
    ]
    product = ExpressionProduct(
        slot=SLOT_EXPRESSION,
        matrix=normalized,
        transform="log2_cpm",
        parameters={"target_sum": target, "tolerance": tolerance},
        conversion_warnings=(warning,) if warning else (),
    )
    return StepOutput(
        products={SLOT_EXPRESSION: product},
        metrics={
            "cells": float(normalized.n_cells),
            "genes": float(normalized.n_genes),
            "max_log2": largest,
            "normalisation_drift": drift,
        },
        checks=tuple(checks),
        warnings=(warning,) if warning else (),
    )


def hvg_step(
    expression: ExpressionProduct,
    *,
    n_top_features: Optional[int],
    min_mean: Optional[float],
    max_mean: Optional[float],
    min_variance: Optional[float],
    seed: Optional[int] = None,
) -> StepOutput:
    """Rank features by cell-to-cell variance inside an expression band, keeping the top ``n_top_features``.

    This is the log-space variance cut, not a mean-variance model: no vst or seurat-style regression is
    fitted here, so expression level is partly baked into the ranking and the output says so. Section
    7.5 treats this as a knob to perturb rather than a result to trust, and every retained gene keeps
    its numbers so a reviewer can see what the run actually selected.
    """
    matrix = expression.matrix
    if expression.counts:
        raise invalid_input(
            "features are ranked on log-normalised expression, not on raw counts", field="expression"
        )
    cap = int(_require(n_top_features, "n_top_features", where="highly_variable_features"))
    low = float(_require(min_mean, "min_mean", where="highly_variable_features"))
    high = float(_require(max_mean, "max_mean", where="highly_variable_features"))
    floor = float(_require(min_variance, "min_variance", where="highly_variable_features"))
    rng_seed = int(_require(seed, "seed", where="highly_variable_features"))
    if not 0.0 <= low < high:
        raise invalid_input("min_mean must be below max_mean", field="min_mean")
    if cap < 1 or floor < 0:
        raise invalid_input("n_top_features must be positive and min_variance non-negative")

    stats: list[GeneStatRow] = []
    for gene in matrix.genes:
        values = _values_at(matrix, gene)
        detected = sum(1 for value in values if value > 0)
        stats.append(
            GeneStatRow(
                gene=gene,
                mean=mean(values),
                variance=stdev(values) ** 2,
                detection_rate=detected / len(values) if values else 0.0,
            )
        )
    candidates = [
        row for row in stats if low <= row.mean <= high and row.variance >= floor and row.detection_rate > 0
    ]
    if not candidates:
        raise invalid_input(
            "the expression band and variance floor retained no genes; the feature filter excludes the whole dataset"
        )
    selected = sorted(candidates, key=lambda row: (-row.variance, row.gene))[:cap]
    checks = [
        _pass(
            "features_ranked_deterministically",
            f"ordered by (-variance, gene); seed {rng_seed} recorded for the section 7.5 feature subsets",
        )
    ]
    if len(candidates) < cap:
        checks.append(
            _warn(
                "feature_filter_too_strict",
                f"the band retained {len(candidates)} of {len(stats)} genes, fewer than the {cap} requested; the "
                "smaller set is used and the run records that the requested width was not available",
            )
        )
    product = FeatureSelectionProduct(
        slot=SLOT_FEATURES,
        genes=tuple(row.gene for row in selected),
        method="logspace_variance_ranking",
        n_top_features=cap,
        seed=rng_seed,
        stats=tuple(selected),
    )
    return StepOutput(
        products={SLOT_FEATURES: product},
        metrics={
            "candidate_genes": float(len(candidates)),
            "selected_genes": float(len(selected)),
            "median_mean_expression": median([row.mean for row in selected]),
            "min_selected_variance": min(row.variance for row in selected),
        },
        checks=tuple(checks),
        warnings=(
            "features are ranked by raw log-space variance with no mean-variance model, so an merely "
            "high-expressed gene can out-rank a biologically variable one; section 7.5 answers this by "
            "varying the feature set, not by trusting one ranking",
        ),
    )


def pca_step(
    expression: ExpressionProduct,
    features: FeatureSelectionProduct,
    *,
    n_components: Optional[int],
    seed: Optional[int],
    iterations: int = 200,
    n_loadings: int = 10,
) -> StepOutput:
    """Deterministic PCA by deflated power iteration over the selected features."""
    components = int(_require(n_components, "n_components", where="pca"))
    rng_seed = int(_require(seed, "seed", where="pca"))
    matrix = expression.matrix
    wanted = set(features.genes)
    genes = [gene for gene in matrix.genes if gene in wanted]
    if not genes:
        raise invalid_input("the selected features are not present in the expression matrix", field="features")
    if len(genes) < len(features.genes):
        raise invalid_input(
            f"{len(features.genes) - len(genes)} selected features are absent from the matrix", field="features"
        )
    if components > len(genes) or components > matrix.n_cells:
        raise invalid_input(
            f"{components} components cannot come from {len(genes)} features over {matrix.n_cells} cells",
            field="n_components",
        )
    cells = [cell.cell_key for cell in matrix.cells]
    columns = {gene: [matrix.row(cell).get(gene, 0.0) for cell in cells] for gene in genes}
    scaled: list[list[float]] = []
    for gene in genes:
        values = columns[gene]
        mu = mean(values)
        sigma = stdev(values)
        scaled.append([(value - mu) / sigma if sigma > 1e-12 else 0.0 for value in values])
    width = len(genes)
    covariance = [
        [sum(scaled[a][i] * scaled[b][i] for i in range(len(cells))) / max(len(cells) - 1, 1) for b in range(width)]
        for a in range(width)
    ]
    eigenvalues, vectors = principal_components(covariance, components, seed=rng_seed, iterations=iterations)
    if not vectors:
        raise invalid_input("the feature matrix has no variance, so PCA cannot reduce it")
    ratios = explained_variance_ratio(eigenvalues)
    vectors = vectors[: len(ratios)]
    projections = tuple(
        CellVectorRow(
            cell_key=cell_key,
            values=tuple(sum(vector[g] * scaled[g][index] for g in range(width)) for vector in vectors),
        )
        for index, cell_key in enumerate(cells)
    )
    loadings: list[GeneLoadingRow] = []
    for component, vector in enumerate(vectors, start=1):
        order = sorted(range(width), key=lambda g: (-abs(vector[g]), genes[g]))
        loadings.extend(
            GeneLoadingRow(component=component, gene=genes[g], weight=vector[g]) for g in order[: max(n_loadings, 1)]
        )
    product = EmbeddingProduct(
        slot=SLOT_EMBEDDING,
        method="power_iteration_pca",
        n_components=len(ratios),
        seed=rng_seed,
        vectors=projections,
        explained_variance_ratio=ratios,
        loadings=tuple(loadings),
    )
    return StepOutput(
        products={SLOT_EMBEDDING: product},
        metrics={
            "components": float(len(ratios)),
            "explained_variance_first": ratios[0] if ratios else 0.0,
            "explained_variance_total": sum(ratios),
            "features": float(width),
        },
        checks=(_pass("explained_variance_scope", "ratios are normalised over the retained components, not over the full variance"),),
    )


def neighbors_step(
    embedding: EmbeddingProduct,
    *,
    k: Optional[int],
    seed: Optional[int],
    metric: str = "euclidean",
) -> StepOutput:
    vectors = {row.cell_key: row.values for row in embedding.vectors}
    size = int(_require(k, "k", where="neighbors"))
    rng_seed = int(_require(seed, "seed", where="neighbors"))
    if size < 1:
        raise invalid_input("k must be at least one", field="k")
    if size >= len(vectors):
        raise invalid_input(f"k={size} cannot exceed the {len(vectors) - 1} other cells", field="k")
    keys = sorted(vectors)
    rows: list[NeighborRow] = []
    for key in keys:
        own = vectors[key]
        distances = [
            (math.sqrt(sum((a - b) ** 2 for a, b in zip(own, vectors[other]))), other)
            for other in keys
            if other != key
        ]
        nearest = sorted(distances, key=lambda item: (item[0], item[1]))[:size]
        rows.append(NeighborRow(cell_key=key, neighbours=tuple((other, distance) for distance, other in nearest)))
    outgoing = {row.cell_key: {neighbour for neighbour, _d in row.neighbours} for row in rows}
    edges = sum(len(row.neighbours) for row in rows)
    mutual = sum(1 for row in rows for neighbour, _d in row.neighbours if row.cell_key in outgoing[neighbour])
    product = NeighborProduct(
        slot=SLOT_GRAPH,
        k=size,
        metric=metric,
        seed=rng_seed,
        embedding_source=embedding.slot,
        rows=tuple(rows),
    )
    checks = [_pass("neighbour_graph_built", f"{edges} directed edges over {len(keys)} cells")]
    empty = [row.cell_key for row in rows if not row.neighbours]
    if empty:
        checks.append(_warn("cells_without_neighbours", f"{len(empty)} cells ended up with no neighbour"))
    return StepOutput(
        products={SLOT_GRAPH: product},
        metrics={"edges": float(edges), "mutual_share": mutual / edges if edges else 0.0, "k": float(size)},
        checks=tuple(checks),
    )


def graph_weights(graph: NeighborProduct) -> dict[tuple[str, str], float]:
    """Edge weight from distance: closer neighbours weigh more, and a shared edge takes the stronger side."""
    weights: dict[tuple[str, str], float] = {}
    for row in graph.rows:
        for neighbour, distance in row.neighbours:
            pair = (min(row.cell_key, neighbour), max(row.cell_key, neighbour))
            weights[pair] = max(weights.get(pair, 0.0), 1.0 / (1.0 + distance))
    return weights


def cluster_step(
    graph: NeighborProduct,
    *,
    resolution: Optional[float],
    seed: Optional[int],
) -> StepOutput:
    """Greedy modularity maximisation over the recorded graph - the Leiden stand-in."""
    gamma = float(_require(resolution, "resolution", where="cluster"))
    rng_seed = int(_require(seed, "seed", where="cluster"))
    nodes = sorted({row.cell_key for row in graph.rows})
    if not nodes:
        raise invalid_input("the neighbourhood graph holds no cells")
    weights = graph_weights(graph)
    assignment = greedy_modularity_partition(nodes, weights, resolution=gamma, seed=rng_seed)
    adjacency = build_adjacency(nodes, weights)
    sizes: dict[str, int] = {}
    for cluster in assignment.values():
        sizes[cluster] = sizes.get(cluster, 0) + 1
    singletons = sum(1 for count in sizes.values() if count == 1)
    product = ClusterProduct(
        slot=SLOT_CLUSTERS,
        method="greedy_modularity_local_moving",
        resolution=gamma,
        seed=rng_seed,
        graph_digest=graph.file_sha256(),
        labels=tuple(CellLabelRow(cell_key=key, cluster=assignment[key]) for key in nodes),
        modularity=modularity(assignment, adjacency, gamma),
        singleton_share=singletons / len(nodes),
    )
    return StepOutput(
        products={SLOT_CLUSTERS: product},
        metrics={
            "clusters": float(len(sizes)),
            "modularity": product.modularity,
            "singleton_share": product.singleton_share,
            "smallest_cluster": float(min(sizes.values())),
        },
        checks=(_pass("partition_names_its_graph", f"computed over graph {graph.file_sha256()[:12]}"),),
        warnings=(
            "the reference partition maximises modularity by local moves only: it is not Leiden and does not "
            "claim Leiden's connectedness guarantee",
        ),
    )


def markers_step(expression: ExpressionProduct, clusters: ClusterProduct, *, top_n: Optional[int]) -> StepOutput:
    """Per-cluster ranking. Section 7.8 item 5: an ordering aid, never identity evidence by itself."""
    limit = int(_require(top_n, "top_n", where="markers"))
    matrix = expression.matrix
    by_cluster = clusters.cells_by_cluster()
    every_cell = [cell.cell_key for cell in matrix.cells]
    rows: list[MarkerRow] = []
    for cluster, members in sorted(by_cluster.items()):
        outside = [key for key in every_cell if key not in set(members)]
        scored: list[tuple[float, str, MarkerRow]] = []
        for gene in matrix.genes:
            column = matrix.column(gene)
            inside_values = [column.get(key, 0.0) for key in members]
            outside_values = [column.get(key, 0.0) for key in outside]
            mean_in = mean(inside_values)
            mean_out = mean(outside_values) if outside_values else 0.0
            detection_in = sum(1 for value in inside_values if value > 0) / len(inside_values)
            detection_out = (sum(1 for value in outside_values if value > 0) / len(outside_values)) if outside_values else 0.0
            log2fc = mean_in - mean_out
            scored.append(
                (
                    log2fc * detection_in,
                    gene,
                    MarkerRow(
                        cluster=cluster,
                        gene=gene,
                        rank=0,
                        mean_in=mean_in,
                        mean_out=mean_out,
                        detection_in=detection_in,
                        detection_out=detection_out,
                        log2fc=log2fc,
                    ),
                )
            )
        ordered = sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
        for rank, (_score, _gene, row) in enumerate(ordered, start=1):
            rows.append(replace(row, rank=rank))
    product = MarkerProduct(
        slot=SLOT_MARKERS,
        method="mean_difference_ranking",
        top_n=limit,
        cluster_digest=clusters.file_sha256(),
        rows=tuple(rows),
        rank_only=True,
    )
    return StepOutput(
        products={SLOT_MARKERS: product},
        metrics={"clusters": float(len(by_cluster)), "markers": float(len(rows))},
        checks=(
            _pass(
                "markers_are_rankings_only",
                f"score is log2fc x detection frequency over cluster assignment {clusters.file_sha256()[:12]}; "
                "section 7.8 item 5 forbids reading this ranking as identity evidence on its own",
            ),
        ),
    )


DE_ARMS = ("condition", "cluster")


@dataclass(frozen=True)
class DeContrast:
    group_a: str
    group_b: str
    #: None compares the arms over every cell; a cluster id restricts the comparison to that cluster.
    cluster: Optional[str] = None
    #: What the two arms name. "condition" compares sample groups; "cluster" compares two populations
    #: of the same sample, which is the only way section 7.8 item 2 can ask a candidate to be measured
    #: against a *named excluded population* instead of against the other condition.
    arms: str = "condition"

    def __post_init__(self) -> None:
        if self.arms not in DE_ARMS:
            raise invalid_input(f"arms must be one of {list(DE_ARMS)}, got {self.arms!r}", field="contrasts")
        if self.group_a == self.group_b:
            raise invalid_input("a contrast's two arms must name different populations", field="contrasts")
        if self.arms == "cluster" and self.cluster is not None:
            raise invalid_input(
                "a cluster-arms contrast compares two clusters against each other, so it cannot also "
                "scope every arm to one cluster",
                field="contrasts",
            )

    @property
    def name(self) -> str:
        if self.cluster is not None:
            return f"{self.group_a}_vs_{self.group_b}_in_{self.cluster}"
        if self.arms == "cluster":
            return f"{self.group_a}_vs_{self.group_b}_between_clusters"
        return f"{self.group_a}_vs_{self.group_b}"

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "DeContrast":
        try:
            return cls(
                group_a=str(payload["group_a"]),
                group_b=str(payload["group_b"]),
                cluster=payload.get("cluster"),
                arms=str(payload.get("arms", "condition")),
            )
        except KeyError as error:
            raise invalid_input(f"contrast {payload!r} lacks {error.args[0]!r}", field="contrasts") from None


def parse_contrasts(values: Optional[Sequence[Mapping[str, Any]]]) -> tuple[DeContrast, ...]:
    if not values:
        raise missing_parameter("contrasts", where="pseudobulk_de")
    parsed = tuple(DeContrast.from_payload(value) for value in values)
    if len({contrast.name for contrast in parsed}) != len(parsed):
        raise invalid_input("contrasts must be unique", field="contrasts")
    return parsed


def _arm_of_cells(contrast: DeContrast, cluster_of: Mapping[str, str]) -> Callable[[Any], str]:
    """Return the function that assigns a cell to one of the contrast's two arms."""
    if contrast.arms == "cluster":
        return lambda cell: cluster_of[cell.cell_key]
    return lambda cell: cell.group


def _pseudobulk(matrix: CountMatrix, cells: Sequence[Any]) -> tuple[dict[str, float], float]:
    """Donor-level profile: counts summed across the donor's cells, then log2 CPM."""
    totals: dict[str, float] = {}
    library = 0.0
    for cell in cells:
        for gene, value in matrix.row(cell.cell_key).items():
            totals[gene] = totals.get(gene, 0.0) + value
            library += value
    if library <= 0:
        return {}, 0.0
    scale = 1e6 / library
    return {gene: math.log2(1.0 + value * scale) for gene, value in totals.items()}, library


def pseudobulk_de_step(
    counts: ExpressionProduct,
    clusters: ClusterProduct,
    *,
    contrasts: Sequence[DeContrast],
    permutation_replicates: Optional[int],
    seed: Optional[int],
    thresholds: Optional[Thresholds],
) -> StepOutput:
    """Differential expression over donors, recording every number section 7.8 asks for.

    The unit of replication is the donor: cells of one donor are summed into one pseudobulk profile
    before any comparison, so a donor with many cells cannot outvote a donor with few. A contrast whose
    donors appear in both groups is tested by sign-flipping those paired differences; otherwise donor
    labels are permuted. Both are permutation tests, and the p-value can never be zero on a finite
    number of shuffles.
    """
    reps = int(_require(permutation_replicates, "permutation_replicates", where="pseudobulk_de"))
    rng_seed = int(_require(seed, "seed", where="pseudobulk_de"))
    limits: Thresholds = _require(thresholds, "thresholds", where="pseudobulk_de")
    if reps < 1:
        raise invalid_input("permutation_replicates must be at least one", field="permutation_replicates")
    matrix = counts.matrix
    if not counts.counts:
        raise invalid_input("pseudobulk must be summed from raw counts, not from a log-normalised matrix")
    cluster_of = {row.cell_key: row.cluster for row in clusters.labels}
    missing = sorted({cell.cell_key for cell in matrix.cells} - set(cluster_of))
    if missing:
        raise invalid_input(
            f"{len(missing)} cells in the matrix carry no cluster label, e.g. {missing[:3]}", field="clusters"
        )

    rows: list[DeRow] = []
    donors_per_group: dict[str, int] = {}
    details: dict[str, dict[str, Any]] = {}
    power_notes: list[str] = []
    partial_pairs: list[str] = []
    rng = Rng(rng_seed)
    for contrast in contrasts:
        scoped = [cell for cell in matrix.cells if contrast.cluster is None or cluster_of[cell.cell_key] == contrast.cluster]
        arm_of = _arm_of_cells(contrast, cluster_of)
        by_group: dict[str, dict[str, list[Any]]] = {}
        for cell in scoped:
            if arm_of(cell) in (contrast.group_a, contrast.group_b):
                by_group.setdefault(arm_of(cell), {}).setdefault(cell.donor_id, []).append(cell)
        for group in (contrast.group_a, contrast.group_b):
            if group not in by_group:
                raise invalid_input(
                    f"contrast {contrast.name} names group {group!r}, which the scoped cells do not contain",
                    field="contrasts",
                )
        donors_per_group[contrast.group_a] = max(donors_per_group.get(contrast.group_a, 0), len(by_group[contrast.group_a]))
        donors_per_group[contrast.group_b] = max(donors_per_group.get(contrast.group_b, 0), len(by_group[contrast.group_b]))

        profiles: dict[tuple[str, str], dict[str, float]] = {}
        for group, donors in by_group.items():
            for donor, donor_cells in donors.items():
                profiles[(group, donor)], _ = _pseudobulk(matrix, donor_cells)
        donors_a = sorted(by_group[contrast.group_a])
        donors_b = sorted(by_group[contrast.group_b])
        paired = sorted(set(donors_a) & set(donors_b))
        every_donor = sorted(set(donors_a) | set(donors_b))
        is_paired = len(paired) >= 2 and len(paired) == len(every_donor)
        if paired and not is_paired:
            partial_pairs.append(f"{contrast.name}: {len(paired)} of {len(every_donor)} donors appear in both arms")
        donors = paired if is_paired else every_donor
        base_labels = (
            ["+"] * len(donors)
            if is_paired
            else [contrast.group_a if donor in donors_a else contrast.group_b for donor in donors]
        )
        details[contrast.name] = {
            "group_a": contrast.group_a,
            "group_b": contrast.group_b,
            "arms": contrast.arms,
            "donors_a": len(donors_a),
            "donors_b": len(donors_b),
            "paired_donors": len(paired),
            "design": "paired" if is_paired else "unpaired",
            "cluster": contrast.cluster if contrast.cluster is not None else "all_cells",
            "donors_tested": len(donors),
            # How many distinct arrangements the null can actually reach. A permutation p-value is a
            # rank inside this set, so this number, not the effect size, sets where the p-values stop.
            "null_assignments": 2 ** len(donors) if is_paired else math.comb(len(donors), len(donors_a)),
        }

        def reduce_statistic(observations: Sequence[float], labels: Sequence[str]) -> float:
            if not observations or len(observations) != len(labels):
                return 0.0
            if is_paired:
                return mean([value if sign == "+" else -value for value, sign in zip(observations, labels)])
            first = [value for value, label in zip(observations, labels) if label == contrast.group_a]
            second = [value for value, label in zip(observations, labels) if label == contrast.group_b]
            if not first or not second:
                return 0.0
            return mean(first) - mean(second)

        def observations_for(gene: str) -> list[float]:
            if is_paired:
                return [
                    profiles[(contrast.group_a, donor)].get(gene, 0.0) - profiles[(contrast.group_b, donor)].get(gene, 0.0)
                    for donor in donors
                ]
            return [
                profiles[(contrast.group_a if donor in donors_a else contrast.group_b, donor)].get(gene, 0.0)
                for donor in donors
            ]

        p_values: list[float] = []
        observed_by_gene: dict[str, float] = {}
        for gene in matrix.genes:
            observations = observations_for(gene)
            observed = reduce_statistic(observations, base_labels)
            observed_by_gene[gene] = observed

            def statistic(assignment: Sequence[str], observations=observations) -> float:
                # The permutation keeps each donor's value and re-reads it against shuffled arms.
                return reduce_statistic(observations, assignment)

            p_values.append(permutation_p_value(observed, statistic, base_labels, rng=rng, replicates=reps, paired=is_paired))
        adjusted = benjamini_hochberg(p_values)

        detection: dict[str, tuple[float, float]] = {}
        for gene in matrix.genes:
            column = matrix.column(gene)
            per_group: dict[str, list[float]] = {contrast.group_a: [], contrast.group_b: []}
            for cell in scoped:
                if arm_of(cell) in per_group:
                    per_group[arm_of(cell)].append(column.get(cell.cell_key, 0.0))
            detection[gene] = tuple(
                (sum(1 for value in per_group[group] if value > 0) / len(per_group[group])) if per_group[group] else 0.0
                for group in (contrast.group_a, contrast.group_b)
            )

        for index, gene in enumerate(matrix.genes):
            observed = observed_by_gene[gene]
            observations = observations_for(gene)
            agreeing = 0
            for position in range(len(donors)):
                remaining = [value for step, value in enumerate(observations) if step != position]
                labels = [label for step, label in enumerate(base_labels) if step != position]
                leave_one_out = reduce_statistic(remaining, labels)
                if abs(leave_one_out) <= 1e-12:
                    continue
                if (leave_one_out > 0) == (observed > 0):
                    agreeing += 1
            rows.append(
                DeRow(
                    gene=gene,
                    group_a=contrast.group_a,
                    group_b=contrast.group_b,
                    log2fc=observed,
                    p_value=p_values[index],
                    padj=adjusted[index],
                    detection_a=detection[gene][0],
                    detection_b=detection[gene][1],
                    donors_tested=len(donors),
                    donors_same_direction=agreeing,
                )
            )
        donors_by_group = (len(donors_a), len(donors_b))
        if min(donors_by_group) < limits.min_n_per_group:
            power_notes.append(
                f"{contrast.name}: donors {donors_by_group[0]} vs {donors_by_group[1]} against "
                f"min_n_per_group={limits.min_n_per_group}"
            )

    product = DeProduct(
        slot=SLOT_DE_TABLE,
        method="pseudobulk_log2cpm_permutation",
        multiple_testing="benjamini_hochberg_within_contrast",
        thresholds_version=limits.version,
        permutation_replicates=reps,
        seed=rng_seed,
        unit="donor",
        donors_per_group=dict(sorted(donors_per_group.items())),
        rows=tuple(rows),
        contrasts=tuple((contrast.group_a, contrast.group_b) for contrast in contrasts),
        contrast_details=dict(sorted(details.items())),
    )
    null_sizes = "; ".join(f"{name}: {detail['null_assignments']}" for name, detail in sorted(details.items()))
    checks = [
        _pass(
            "pseudobulk_unit_is_donor",
            f"{len(donors_per_group)} groups, {reps} permutations, Benjamini-Hochberg applied within each contrast",
        ),
        _pass(
            "pseudobulk_design_recorded",
            "; ".join(
                f"{name}: {detail['design']} over {detail['donors_tested']} donors "
                f"({detail['donors_a']} vs {detail['donors_b']}, {detail['paired_donors']} shared)"
                for name, detail in sorted(details.items())
            ),
        ),
        _pass("donor_direction_is_leave_one_out", "donor consistency is leave-one-donor-out sign agreement, not per-cell counts"),
        _pass(
            "permutation_null_resolution",
            f"distinct donor assignments the null can reach ({null_sizes}) over {reps} shuffles, so the lowest "
            f"p-value this test is capable of producing is {1.0 / (reps + 1.0):.4f} and the observed split is "
            "only one rank among that small set. A p-ordering is therefore not an effect-size ordering: a gene "
            "whose donors agree tightly can outrank a marker switched on in one arm alone. Section 7.8 support "
            "is read as the conjunction of FDR, |log2FC|, detection frequency and donor consistency, so it "
            "comes from DeProduct.supporting rather than from the head of the table.",
        ),
    ]
    if partial_pairs:
        checks.append(
            _warn(
                "pseudobulk_pairing",
                "only some donors appear in both arms, so the paired test is unavailable and the unpaired "
                "permutation is what these p-values came from: " + "; ".join(partial_pairs),
            )
        )
    if power_notes:
        checks.append(
            _warn(
                "pseudobulk_power",
                "below the section 7.4 power floor, so no between-group significance claim is allowed: "
                + "; ".join(power_notes),
            )
        )
    return StepOutput(
        products={SLOT_DE_TABLE: product},
        metrics={
            "contrasts": float(len(contrasts)),
            "tested_genes": float(product.tested_genes),
            "supporting_genes": float(len(product.supporting(limits))),
            "min_padj": min((row.padj for row in rows), default=1.0),
        },
        checks=tuple(checks),
    )
