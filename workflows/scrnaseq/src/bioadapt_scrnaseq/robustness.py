"""The section 7.5 minimum robustness protocol, run by the reference executor.

Section 7.5 is a protocol, not a number: a candidate is only called stable after it has been re-derived
under five classes of perturbation and the agreement has been read against chance. This module runs the
chain the plan describes - features, embedding, neighbours, partition - once per arm and records what
each arm measured. The verdict itself is derived in ``StabilityProduct`` from those numbers, so nothing
here writes "stable" into an asset.

Three decisions change the answer and are worth stating:

* each arm re-runs the chain from the expression product rather than editing the baseline partition, so
  a perturbation moves everything upstream of the last label, the way the plan's table says it should;
* the seeds are per stage, because that is how the plan's own nodes were run - the seed arm offsets all
  of them together, since "five seeds" perturbs the run, not one step inside it; and
* the leave-one-donor and subsampling arms compare against the cells that still exist in that arm, so a
  candidate that merely has fewer cells left is not read as an unstable one - while a candidate whose
  cells have all gone is recorded as vanished, which is the finding the arm exists to make.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterator, Mapping, Optional, Sequence

from bioadapt_policies.robustness import RobustnessProtocol
from bioadapt_schemas.enums import PerturbationKind, ScreeningResult
from bioadapt_schemas.thresholds import Thresholds

from .errors import StepFailure, invalid_input, missing_parameter
from .matrix import CountMatrix
from .numeric import Rng, adjusted_rand_index, jaccard, mean, quantile
from .products import (
    ClusterProduct,
    EmbeddingProduct,
    ExpressionProduct,
    FeatureSelectionProduct,
    NeighborProduct,
    StabilityArmRow,
    StabilityProduct,
)
from .steps import (
    SLOT_CLUSTERS,
    SLOT_EMBEDDING,
    SLOT_FEATURES,
    SLOT_GRAPH,
    StepOutput,
    check,
    cluster_step,
    hvg_step,
    neighbors_step,
    pca_step,
)

SLOT_STABILITY = "stability"


@dataclass(frozen=True)
class ChainParameters:
    """The plan's own settings for the chain every arm re-runs; none of them are defaulted here.

    These are the stage-0 numbers the workflow was run with (appendix C1). The step cannot invent them,
    because a stability measured around a different clustering is a stability of a different run.
    """

    n_top_features: int
    min_mean: float
    max_mean: float
    min_variance: float
    n_components: int
    k: int
    resolution: float
    features_seed: int
    embedding_seed: int
    neighbors_seed: int
    cluster_seed: int
    marker_top_n: int = 5

    def __post_init__(self) -> None:
        if self.n_top_features < 1 or self.n_components < 1 or self.k < 1:
            raise ValueError("n_top_features, n_components and k must all be at least one")
        if not 0.0 <= self.min_mean < self.max_mean:
            raise ValueError("min_mean must be below max_mean")
        if self.min_variance < 0 or self.resolution <= 0:
            raise ValueError("min_variance must be non-negative and resolution positive")
        if self.marker_top_n < 1:
            raise ValueError("marker_top_n must be at least one")
        for name in ("features_seed", "embedding_seed", "neighbors_seed", "cluster_seed"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @classmethod
    def from_parameters(cls, parameters: Mapping[str, Any]) -> "ChainParameters":
        """Read the chain out of a node's parameters, refusing to guess any part of it."""

        def value(name: str) -> Any:
            found = parameters.get(name)
            if found is None:
                raise missing_parameter(name, where="robustness chain")
            return found

        return cls(
            n_top_features=int(value("n_top_features")),
            min_mean=float(value("min_mean")),
            max_mean=float(value("max_mean")),
            min_variance=float(value("min_variance")),
            n_components=int(value("n_components")),
            k=int(value("k")),
            resolution=float(value("resolution")),
            features_seed=int(value("features_seed")),
            embedding_seed=int(value("embedding_seed")),
            neighbors_seed=int(value("neighbors_seed")),
            cluster_seed=int(value("cluster_seed")),
            marker_top_n=int(parameters.get("marker_top_n", 5)),
        )

    def at_resolution(self, gamma: float) -> "ChainParameters":
        return replace(self, resolution=gamma)

    def at_feature_count(self, cap: int) -> "ChainParameters":
        return replace(self, n_top_features=max(cap, 1))

    def at_seed_offset(self, delta: int) -> "ChainParameters":
        """Every randomised stage moves together; one stage alone is a different perturbation."""
        return replace(
            self,
            features_seed=self.features_seed + delta,
            embedding_seed=self.embedding_seed + delta,
            neighbors_seed=self.neighbors_seed + delta,
            cluster_seed=self.cluster_seed + delta,
        )


def _labels(product: ClusterProduct) -> dict[str, str]:
    return {row.cell_key: row.cluster for row in product.labels}


def _partition_sets(labels: Mapping[str, str]) -> frozenset[frozenset[str]]:
    """A partition without its names, so re-derivation is checked on the cells it groups, not on a label."""
    return frozenset(
        frozenset(key for key, cluster in labels.items() if cluster == name) for name in set(labels.values())
    )


def _stage(out: StepOutput, slot: str, kind: type) -> Any:
    product = out.product(slot)
    if not isinstance(product, kind):
        raise invalid_input(f"the chain handed back a {type(product).__name__} for slot {slot!r}", field=slot)
    return product


def run_chain(expression: ExpressionProduct, chain: ChainParameters, *, cells: Optional[Sequence[str]] = None) -> ClusterProduct:
    """Re-derive features, embedding, neighbourhood and partition for one arm of the protocol."""
    matrix = expression if cells is None else replace(expression, matrix=expression.matrix.subset(cells=cells))
    features = _stage(
        hvg_step(
            matrix,
            n_top_features=chain.n_top_features,
            min_mean=chain.min_mean,
            max_mean=chain.max_mean,
            min_variance=chain.min_variance,
            seed=chain.features_seed,
        ),
        SLOT_FEATURES,
        FeatureSelectionProduct,
    )
    embedding = _stage(
        pca_step(
            matrix, features, n_components=chain.n_components, seed=chain.embedding_seed, iterations=200, n_loadings=10
        ),
        SLOT_EMBEDDING,
        EmbeddingProduct,
    )
    graph = _stage(neighbors_step(embedding, k=chain.k, seed=chain.neighbors_seed), SLOT_GRAPH, NeighborProduct)
    return _stage(
        cluster_step(graph, resolution=chain.resolution, seed=chain.cluster_seed), SLOT_CLUSTERS, ClusterProduct
    )


def _marker_set(matrix: CountMatrix, members: Sequence[str], outside: Sequence[str], top_n: int) -> tuple[str, ...]:
    """The same one-vs-rest ranking as the markers step, reduced to the gene names it puts on top."""
    if not members:
        return ()
    scored: list[tuple[float, str]] = []
    for gene in matrix.genes:
        column = matrix.column(gene)
        inside = [column.get(key, 0.0) for key in members]
        rest = [column.get(key, 0.0) for key in outside]
        mean_in = mean(inside)
        mean_out = mean(rest) if rest else 0.0
        detection = sum(1 for value in inside if value > 0) / len(inside)
        scored.append(((mean_in - mean_out) * detection, gene))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return tuple(gene for _score, gene in scored[:top_n])


def permutation_overlap_null(
    clusters: Mapping[str, Sequence[str]],
    *,
    size: int,
    replicates: int,
    seed: int,
) -> float:
    """The 95th percentile overlap a same-size random set reaches against the best-matching cluster.

    The observed number is itself a best match over clusters, so comparing it with the *average* random
    match would flatter it: the null takes the same maximum the measurement took. Section 7.5 asks for
    exactly this on small clusters, where a raw overlap of two thirds can be dealt by luck alone.
    """
    pool = sorted({key for members in clusters.values() for key in members})
    if not pool:
        raise ValueError("the permutation null needs cells to draw from")
    draw_size = min(max(size, 1), len(pool))
    rng = Rng(seed)
    values = [
        max(jaccard(rng.sampled(pool, draw_size), members) for members in clusters.values())
        for _replicate in range(replicates)
    ]
    return quantile(values, 0.95)


def _arm_partitions(
    expression: ExpressionProduct,
    protocol: RobustnessProtocol,
    chain: ChainParameters,
) -> Iterator[tuple[PerturbationKind, str, Optional[ClusterProduct], Optional[str]]]:
    """Yield one entry per replicate: the perturbed partition, or the reason it could not be run."""
    matrix = expression.matrix
    every_cell = sorted(cell.cell_key for cell in matrix.cells)
    by_donor = matrix.cells_by_donor()
    try:
        names = protocol.arms_for(len(by_donor))
    except ValueError as refused:
        raise invalid_input(str(refused), field="robustness_protocol") from refused

    def attempt(arm: str, kind: PerturbationKind, perturbed: ChainParameters, cells: Optional[Sequence[str]] = None):
        try:
            return kind, arm, run_chain(expression, perturbed, cells=cells), None
        except StepFailure as failure:
            return kind, arm, None, f"{arm}: {failure.message}"

    for arm, factor in zip(names.get(PerturbationKind.RESOLUTION, ()), protocol.perturbing_factors(PerturbationKind.RESOLUTION)):
        yield attempt(arm, PerturbationKind.RESOLUTION, chain.at_resolution(chain.resolution * factor))
    for arm, offset in zip(names.get(PerturbationKind.SEED, ()), protocol.seed_offsets()):
        yield attempt(arm, PerturbationKind.SEED, chain.at_seed_offset(offset))
    for arm, factor in zip(names.get(PerturbationKind.FEATURE_SUBSET, ()), protocol.perturbing_factors(PerturbationKind.FEATURE_SUBSET)):
        yield attempt(arm, PerturbationKind.FEATURE_SUBSET, chain.at_feature_count(round(chain.n_top_features * factor)))
    for arm, donor in zip(names.get(PerturbationKind.LEAVE_ONE_DONOR, ()), sorted(by_donor)):
        kept = [key for key in every_cell if key not in set(by_donor[donor])]
        yield attempt(arm, PerturbationKind.LEAVE_ONE_DONOR, chain, cells=kept)
    for arm in names.get(PerturbationKind.SUBSAMPLING, ()):
        fraction = float(protocol.subsample_fraction or 0.5)
        keep = max(round(matrix.n_cells * fraction), 2)
        yield attempt(arm, PerturbationKind.SUBSAMPLING, chain, cells=Rng(chain.cluster_seed).sampled(every_cell, keep))


def robustness_step(
    expression: ExpressionProduct,
    baseline: ClusterProduct,
    *,
    protocol: RobustnessProtocol,
    thresholds: Thresholds,
    chain: ChainParameters,
    candidate_clusters: Sequence[str],
) -> StepOutput:
    """Re-derive every named candidate under the protocol's arms and record the agreement."""
    if not candidate_clusters:
        raise invalid_input(
            "the robustness node names the clusters it is judging; running it over every cluster would "
            "report a stability for groups nobody asked about",
            field="candidate_clusters",
        )
    unknown = sorted(set(candidate_clusters) - set(baseline.clusters))
    if unknown:
        raise invalid_input(f"clusters the baseline partition does not name: {unknown}", field="candidate_clusters")

    small_max = thresholds.rare_min_cells
    criteria = {
        "cc_ari_min": thresholds.stability_cc_ari_min,
        "jaccard_min": thresholds.stability_jaccard_min,
        "pass_ratio_min": thresholds.stability_pass_ratio,
        "small_cluster_max_cells": float(small_max),
    }

    reproduced = run_chain(expression, chain)
    if _partition_sets(_labels(reproduced)) != _partition_sets(_labels(baseline)):
        raise invalid_input(
            "re-running the chain with the parameters this node was handed does not reproduce the clustering "
            f"it was given ({reproduced.file_sha256()[:12]} against {baseline.file_sha256()[:12]}): the "
            "perturbations would be measured against a partition this run cannot account for",
            field=SLOT_CLUSTERS,
        )

    baseline_sets = baseline.cells_by_cluster()
    baseline_labels = _labels(baseline)
    rows: list[StabilityArmRow] = []
    skipped: list[str] = []
    vanished: list[str] = []
    unmeasurable: list[str] = []
    top_per_candidate = {
        candidate: _marker_set(
            expression.matrix,
            list(baseline_sets[candidate]),
            [key for key in sorted(baseline_labels) if baseline_labels[key] != candidate],
            chain.marker_top_n,
        )
        for candidate in candidate_clusters
    }

    for kind, arm, partition, reason in _arm_partitions(expression, protocol, chain):
        if partition is None:
            skipped.append(reason or f"{arm}: not run")
            continue
        labels = _labels(partition)
        members = partition.cells_by_cluster()
        arm_matrix = (
            expression.matrix
            if len(labels) == expression.matrix.n_cells
            else expression.matrix.subset(cells=sorted(labels))
        )
        ari = adjusted_rand_index(baseline_labels, labels) if len(set(baseline_labels) & set(labels)) >= 2 else None
        if ari is None:
            unmeasurable.append(f"{arm}: no chance-corrected agreement exists between these two partitions")
        for candidate in sorted(candidate_clusters):
            base_set = list(baseline_sets[candidate])
            still = [key for key in base_set if key in labels]
            if not still:
                vanished.append(f"{arm}/{candidate}")
                rows.append(
                    StabilityArmRow(
                        kind=kind.value, arm=arm, baseline_cluster=candidate, baseline_cells=len(base_set),
                        cells_compared=0, cc_ari=ari, jaccard=None, permutation_jaccard=None,
                        marker_overlap=None, matched_cluster=None, matched_cells=0,
                    )
                )
                continue
            best_cluster, best_members = sorted(
                members.items(), key=lambda item: (-jaccard(still, item[1]), item[0])
            )[0]
            overlap = jaccard(still, best_members)
            null = None
            if min(len(base_set), len(still)) <= small_max:
                null = permutation_overlap_null(
                    members,
                    size=len(still),
                    replicates=protocol.permutation_replicates,
                    seed=chain.cluster_seed,
                )
            outside_new = [key for key in sorted(labels) if labels[key] != best_cluster]
            top_new = _marker_set(arm_matrix, list(best_members), outside_new, chain.marker_top_n)
            marker_overlap = (
                jaccard(top_per_candidate[candidate], top_new) if top_per_candidate[candidate] and top_new else None
            )
            rows.append(
                StabilityArmRow(
                    kind=kind.value, arm=arm, baseline_cluster=candidate, baseline_cells=len(base_set),
                    cells_compared=len(still), cc_ari=ari, jaccard=overlap, permutation_jaccard=null,
                    marker_overlap=marker_overlap, matched_cluster=best_cluster, matched_cells=len(best_members),
                )
            )

    if not rows:
        raise invalid_input(
            "the protocol ran no perturbation this dataset can support: " + "; ".join(skipped), field="protocol"
        )

    covered = tuple(sorted({row.kind for row in rows}))
    planned = tuple(kind.value for kind in protocol.covered_arms())
    silent = sorted(set(planned) - set(covered))
    product = StabilityProduct(
        slot=SLOT_STABILITY,
        protocol_id=protocol.protocol_id,
        protocol_version=protocol.version,
        thresholds_version=thresholds.version,
        baseline_clusters_digest=baseline.file_sha256(),
        baseline_expression_digest=expression.file_sha256(),
        criteria=criteria,
        covered_kinds=covered,
        omitted_arms=tuple(protocol.missing_arms()),
        deviation=protocol.deviation_note(),
        permutation_replicates=protocol.permutation_replicates,
        seed=chain.cluster_seed,
        baseline_sizes={cluster: len(keys) for cluster, keys in sorted(baseline_sets.items())},
        rows=tuple(rows),
    )

    small_candidates = sorted(cluster for cluster in candidate_clusters if len(baseline_sets[cluster]) <= small_max)
    checks = [
        check(
            "protocol_arms_covered",
            ScreeningResult.PASS,
            f"classes measured: {list(covered)}; arms the protocol omits: {list(product.omitted_arms)}"
            + (f" - {product.deviation}" if product.deviation else ""),
        ),
        check(
            "perturbation_metrics_recorded",
            ScreeningResult.PASS,
            f"{len(rows)} arm measurements over {len(candidate_clusters)} candidate cluster(s), each naming the "
            f"partition it was compared against ({product.baseline_clusters_digest[:12]})",
        ),
        check(
            "stability_criteria_recorded",
            ScreeningResult.PASS,
            f"criteria applied: {sorted(criteria.items())} from threshold set {thresholds.version}; the verdict "
            "is derived when the product is read, never stored as a field a caller could set",
        ),
        check(
            "small_cluster_permutation_null",
            ScreeningResult.PASS,
            f"{len(small_candidates)} candidate(s) at or below the {small_max}-cell small-cluster line "
            f"{small_candidates} were each judged against a same-size random-set null of "
            f"{protocol.permutation_replicates} draws",
        ),
        check(
            "robustness_below_criteria",
            ScreeningResult.PASS if product.stable else ScreeningResult.WARN,
            f"{len(product.passed_kinds)}/{len(product.kinds)} classes met the criteria: {product.verdict()}"
            + (
                ""
                if product.stable
                else "; the candidate stays provisional until the failed classes are explained or a reviewer "
                "approves a narrower protocol"
            ),
        ),
    ]
    if silent:
        checks.append(
            check(
                "planned_class_unmeasured",
                ScreeningResult.WARN,
                f"the protocol planned {list(silent)} but no arm of it produced a measurement; the pass ratio "
                f"below is over {list(covered)} classes only",
            )
        )
    if skipped:
        checks.append(
            check(
                "perturbation_arm_not_runnable",
                ScreeningResult.WARN,
                "arms this dataset could not support are recorded as gaps rather than silently dropped: "
                + "; ".join(skipped),
            )
        )
    if vanished:
        checks.append(
            check(
                "candidate_set_vanished",
                ScreeningResult.WARN,
                f"the candidate held none of its cells in these arms: {sorted(vanished)}; a cluster that exists "
                "only while one donor is present is a donor effect, not a stable population",
            )
        )
    if unmeasurable:
        checks.append(
            check(
                "agreement_not_measurable",
                ScreeningResult.WARN,
                "a partition pair with no chance-corrected index is reported as unmeasurable rather than as a 1: "
                + "; ".join(unmeasurable[:5]),
            )
        )

    return StepOutput(
        products={SLOT_STABILITY: product},
        metrics={
            "perturbation_rows": float(len(rows)),
            "classes_covered": float(len(covered)),
            "classes_passed": float(len(product.passed_kinds)),
            "agreement_ratio": product.agreement_ratio,
            "candidate_clusters": float(len(candidate_clusters)),
            "smallest_candidate_cells": float(min(len(baseline_sets[c]) for c in candidate_clusters)),
            "arms_not_runnable": float(len(skipped)),
        },
        checks=tuple(checks),
        warnings=(
            "stability here measures the pipeline's own consistency: the reference executor's partition is a "
            "greedy modularity local-moving stand-in for Leiden, so agreement across the arms is not evidence "
            "that a Leiden run would agree with itself",
        ),
    )
