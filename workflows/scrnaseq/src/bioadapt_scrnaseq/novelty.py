"""Section 7.6: reference coverage and the continuous-state check, measured and kept apart.

The plan's rule is that novelty and coverage are never merged into one number, because "the reference
does not cover these cells" and "these cells are a new type" are different claims with different
evidence. So the two steps here only measure. ``coverage_step`` says how far a candidate sits from an
annotated reference, read against the reference's own distances; ``state_axis_step`` says how much of
the candidate's deviation is explained by a gradient the project declared. The four-condition verdict
lives in ``gates.novelty_verdict``, where it can be reviewed - no product in this module holds a field
that could be read as a novelty claim.

Both steps refuse to invent their own numbers. ``k`` and the ratio come from the project's threshold
set, so they are versioned and shown beside the conclusion; the axis cuts come from the node's
parameters, because appendix C1 marks them stage-0 placeholders.
"""

from __future__ import annotations

import math
from typing import Mapping, Optional, Sequence

from bioadapt_schemas.enums import ScreeningResult
from bioadapt_schemas.thresholds import Thresholds

from .errors import invalid_input, missing_parameter
from .numeric import mean, median, quantile, stdev
from .products import (
    ClusterProduct,
    CoverageProduct,
    CoverageRow,
    EmbeddingProduct,
    ExpressionProduct,
    StateAxisProduct,
    StateAxisRow,
)
from .steps import StepOutput, check

SLOT_COVERAGE = "coverage"
SLOT_STATE_AXIS = "state_axis"

#: The reference executor's own partition method. Labels that came from it are this run's guess about
#: its own cells, not an external reference, so coverage measured against them is separation, not novelty.
SELF_ANNOTATION_METHOD = "greedy_modularity_local_moving"

#: The distribution the plan asks to report, not just its centre.
QUANTILE_LABELS = ((0.5, "p50"), (0.9, "p90"), (0.99, "p99"))


def _euclidean(first: Sequence[float], second: Sequence[float]) -> float:
    if len(first) != len(second):
        raise invalid_input(
            f"embedding vectors of different lengths ({len(first)} and {len(second)}) are not the same space",
            field="embedding",
        )
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))


def _ranked_quantiles(values: Sequence[float]) -> dict[str, float]:
    return {label: quantile(values, q) for q, label in QUANTILE_LABELS}


def _nearest(vector: Sequence[float], pool: Sequence[tuple[str, Sequence[float]]], k: int) -> tuple[float, str]:
    """Mean distance to the ``k`` closest pool cells, ties broken by cell key so the answer is one number."""
    ranked = sorted(
        ((_euclidean(vector, values), key) for key, values in pool), key=lambda item: (item[0], item[1])
    )
    return mean([distance for distance, _key in ranked[:k]]), ranked[0][1]


def _percentile_of(value: float, distribution: Sequence[float]) -> float:
    """Where one distance sits in the calibration set's empirical distribution."""
    return sum(1 for other in distribution if other <= value) / len(distribution)


def coverage_step(
    embedding: EmbeddingProduct,
    reference: ClusterProduct,
    *,
    candidate_clusters: Sequence[str],
    thresholds: Thresholds,
) -> StepOutput:
    """Measure a candidate's distance to the annotated reference against the reference's own distances."""
    if not candidate_clusters:
        raise invalid_input(
            "the coverage node names the clusters it is measuring; a coverage number for the whole dataset "
            "describes the reference, not a candidate",
            field="candidate_clusters",
        )
    unknown = sorted(set(candidate_clusters) - set(reference.clusters))
    if unknown:
        raise invalid_input(f"clusters the reference annotation does not name: {unknown}", field="candidate_clusters")

    vectors = {row.cell_key: row.values for row in embedding.vectors}
    labels = {row.cell_key: row.cluster for row in reference.labels}
    unvectoried = sorted(set(labels) - set(vectors))
    if unvectoried:
        raise invalid_input(
            f"{len(unvectoried)} labelled cells have no vector in the embedding this node was handed "
            f"({embedding.slot}): coverage is measured in one space, not across two",
            field="embedding",
        )

    wanted = int(thresholds.coverage_knn)
    candidates = sorted(key for key, cluster in labels.items() if cluster in set(candidate_clusters))
    pool = sorted(key for key, cluster in labels.items() if cluster not in set(candidate_clusters))
    if not candidates:
        raise invalid_input(
            f"clusters {sorted(candidate_clusters)} hold no cell in this annotation", field="candidate_clusters"
        )
    if len(pool) < wanted:
        raise invalid_input(
            f"a {wanted}-neighbour coverage measurement needs at least {wanted} reference cells, and the "
            f"annotation leaves {len(pool)}: the step reports a gap rather than quietly shortening k",
            field="reference",
        )

    pool_vectors = [(key, vectors[key]) for key in pool]
    distances: dict[str, float] = {}
    nearest: dict[str, str] = {}
    for key in candidates:
        distance, closest = _nearest(vectors[key], pool_vectors, wanted)
        distances[key] = distance
        nearest[key] = closest

    # A reference cell is its own nearest neighbour at distance zero, so the calibration set has to be
    # measured with the cell removed - otherwise "normal" is 0 and every candidate looks uncovered.
    calibration: dict[str, float] = {}
    for key in pool:
        without_self = [(other, vectors[other]) for other in pool if other != key]
        if len(without_self) < wanted:
            raise invalid_input(
                f"the calibration set cannot be measured leave-one-out with {len(pool)} reference cells and "
                f"k={wanted}",
                field="reference",
            )
        calibration[key] = _nearest(vectors[key], without_self, wanted)[0]

    calibration_values = [calibration[key] for key in pool]
    rows = tuple(
        CoverageRow(
            cell_key=key,
            distance=distances[key],
            calibration_percentile=_percentile_of(distances[key], calibration_values),
            nearest_reference=nearest[key],
        )
        for key in candidates
    )
    candidate_values = [distances[key] for key in candidates]
    ratio_floor = float(thresholds.coverage_distance_ratio_min)
    product = CoverageProduct(
        slot=SLOT_COVERAGE,
        method="knn_to_annotated_reference_leave_one_out_calibration",
        embedding_digest=embedding.file_sha256(),
        reference_digest=reference.file_sha256(),
        criteria={"knn": float(wanted), "distance_ratio_min": ratio_floor},
        thresholds_version=thresholds.version,
        reference_cells=len(pool),
        calibration_cells=len(calibration),
        calibration_quantiles=_ranked_quantiles(calibration_values),
        candidate_quantiles=_ranked_quantiles(candidate_values),
        median_candidate_distance=median(candidate_values),
        median_calibration_distance=median(calibration_values),
        rows=rows,
    )

    checks = [
        check(
            "reference_set_named",
            ScreeningResult.PASS,
            f"{len(pool)} reference cells over {len(set(labels.values()) - set(candidate_clusters))} labelled "
            f"population(s), annotation {product.reference_digest[:12]}, space {product.embedding_digest[:12]}",
        ),
        check(
            "calibration_is_leave_one_out",
            ScreeningResult.PASS,
            f"each of the {len(calibration)} calibration distances was measured with its own cell removed, so "
            "a normal reference cell's distance is not trivially zero",
        ),
        check(
            "coverage_measured_against_the_reference_distribution",
            ScreeningResult.PASS,
            f"candidate median {product.median_candidate_distance} against calibration median "
            f"{product.median_calibration_distance}; quantiles reported as {QUANTILE_LABELS} of each "
            "distribution rather than as one number",
        ),
        check(
            "coverage_is_not_a_novelty_claim",
            ScreeningResult.PASS,
            "this product holds distances and a ratio only; section 7.6 keeps novelty a separate verdict over "
            "coverage anomaly, stability, artefact exclusion and the continuous-state check, so nothing here "
            "asserts a new cell type",
        ),
        check(
            "coverage_gap_measured",
            ScreeningResult.WARN if product.coverage_gap else ScreeningResult.PASS,
            f"distance ratio {product.distance_ratio:.4f} against the recorded floor {ratio_floor} from "
            f"threshold set {thresholds.version}: "
            + (
                "the reference does not cover this candidate, which is a coverage finding and not yet a "
                "novelty one"
                if product.coverage_gap
                else "the reference covers this candidate"
            ),
        ),
    ]
    if product.reference_cells < 4 * wanted:
        checks.append(
            check(
                "thin_calibration_distribution",
                ScreeningResult.WARN,
                f"{product.reference_cells} reference cells give {product.reference_cells} calibration points "
                f"for k={wanted}: the empirical percentile is coarse, so a gap near the floor is not decided "
                "by this measurement alone",
            )
        )
    if reference.method == SELF_ANNOTATION_METHOD:
        checks.append(
            check(
                "reference_labels_are_external",
                ScreeningResult.WARN,
                f"the labels come from this run's own partition ({reference.method}), so the measurement reads "
                "as separation from the other clusters of this dataset. Coverage against an external, "
                "versioned reference annotation is what section 7.6 asks for before any novelty reading",
            )
        )

    return StepOutput(
        products={SLOT_COVERAGE: product},
        metrics={
            "candidate_cells": float(len(candidates)),
            "reference_cells": float(len(pool)),
            "calibration_cells": float(len(calibration)),
            "knn": float(wanted),
            "median_candidate_distance": product.median_candidate_distance,
            "median_calibration_distance": product.median_calibration_distance,
            "distance_ratio": product.distance_ratio,
            "uncovered_share": product.uncovered_share(),
        },
        checks=tuple(checks),
    )


def state_axis_step(
    expression: ExpressionProduct,
    clusters: ClusterProduct,
    *,
    candidate_clusters: Sequence[str],
    axis: Optional[Mapping[str, float]] = None,
    tail_quantile: Optional[float] = None,
    tail_share_min: Optional[float] = None,
    axis_explained_min: Optional[float] = None,
) -> StepOutput:
    """Ask whether the candidate is only the end of a gradient the project already named."""
    wanted_axis = _required_mapping(axis, "axis", where="state axis")
    limit = float(_required(tail_quantile, "tail_quantile", where="state axis"))
    share_floor = float(_required(tail_share_min, "tail_share_min", where="state axis"))
    explained_floor = float(_required(axis_explained_min, "axis_explained_min", where="state axis"))
    if not 0.5 < limit < 1.0:
        raise invalid_input(f"tail_quantile {limit} is not a tail", field="tail_quantile")
    if not 0.0 <= share_floor <= 1.0 or not 0.0 <= explained_floor <= 1.0:
        raise invalid_input("tail_share_min and axis_explained_min are shares between 0 and 1", field="criteria")
    if not candidate_clusters:
        raise invalid_input("the continuous-state node names the clusters it is judging", field="candidate_clusters")
    if all(weight == 0.0 for weight in wanted_axis.values()):
        raise invalid_input("an axis of all-zero weights projects every cell onto one point", field="axis")

    matrix = expression.matrix
    present = set(matrix.genes)
    absent = sorted(gene for gene in wanted_axis if gene not in present)
    if absent:
        raise invalid_input(
            f"the declared axis is defined on genes this matrix does not measure: {absent}. A gradient "
            "cannot be measured on unmeasured genes, so the step refuses instead of scoring them as zero",
            field="axis",
        )

    labels = {row.cell_key: row.cluster for row in clusters.labels}
    unknown = sorted(set(candidate_clusters) - set(labels.values()))
    if unknown:
        raise invalid_input(f"clusters the partition does not name: {unknown}", field="candidate_clusters")
    candidates = sorted(key for key, cluster in labels.items() if cluster in set(candidate_clusters))
    reference = sorted(key for key, cluster in labels.items() if cluster not in set(candidate_clusters))
    if len(reference) < 2:
        raise invalid_input(
            f"a gradient is measured against the cells that are not the candidate, and only {len(reference)} "
            "remain",
            field="reference",
        )

    columns = {gene: matrix.column(gene) for gene in wanted_axis}

    def projection(key: str) -> float:
        return sum(weight * columns[gene].get(key, 0.0) for gene, weight in wanted_axis.items())

    reference_projections = [projection(key) for key in reference]
    spread = stdev(reference_projections)
    if spread <= 0.0:
        raise invalid_input(
            "the reference cells show no spread along this axis, so no tail can be measured: the axis either "
            "is not present in the data or is carried entirely by the candidate",
            field="axis",
        )
    centre = median(reference_projections)

    def mean_profile(keys: Sequence[str]) -> dict[str, float]:
        return {gene: mean([columns[gene].get(key, 0.0) for key in keys]) for gene in wanted_axis}

    candidate_profile = mean_profile(candidates)
    reference_profile = mean_profile(reference)
    deviation = [candidate_profile[gene] - reference_profile[gene] for gene in sorted(wanted_axis)]
    weights = [wanted_axis[gene] for gene in sorted(wanted_axis)]
    deviation_norm = math.sqrt(sum(value**2 for value in deviation))
    weight_norm = math.sqrt(sum(value**2 for value in weights))
    explained = (
        abs(sum(a * b for a, b in zip(deviation, weights))) / (deviation_norm * weight_norm)
        if deviation_norm > 0.0
        else 0.0
    )

    direction = 1.0 if mean([projection(key) for key in candidates]) >= centre else -1.0
    cut_up = quantile(reference_projections, limit)
    cut_down = quantile(reference_projections, 1.0 - limit)
    rows: list[StateAxisRow] = []
    beyond = 0
    for key in candidates:
        value = projection(key)
        is_tail = value > cut_up if direction > 0.0 else value < cut_down
        beyond += int(is_tail)
        rows.append(
            StateAxisRow(
                cell_key=key,
                projection=value,
                z=(value - centre) / spread,
                beyond_reference_tail=is_tail,
            )
        )
    tail_share = beyond / len(candidates)

    product = StateAxisProduct(
        slot=SLOT_STATE_AXIS,
        method="marker_gradient_projection",
        axis=dict(wanted_axis),
        expression_digest=expression.file_sha256(),
        cluster_digest=clusters.file_sha256(),
        criteria={"tail_quantile": limit, "tail_share_min": share_floor, "axis_explained_min": explained_floor},
        reference_cells=len(reference),
        candidate_cells=len(candidates),
        axis_explained=explained,
        tail_share=tail_share,
        reference_median=centre,
        reference_stdev=spread,
        max_abs_z=max(abs(row.z) for row in rows),
        rows=tuple(rows),
    )

    checks = [
        check(
            "axis_is_a_declared_gradient",
            ScreeningResult.PASS,
            f"axis over {len(wanted_axis)} measured gene(s) with signed weights, sorted {sorted(wanted_axis.items())}; "
            "the MVP method is a marker gradient, not diffusion pseudotime, and it answers only the question "
            "those genes can ask",
        ),
        check(
            "state_axis_cuts_are_inputs",
            ScreeningResult.PASS,
            f"tail_quantile={limit}, tail_share_min={share_floor}, axis_explained_min={explained_floor} came from "
            "this node's parameters (appendix C1); the verdict is derived from them when the product is read",
        ),
        check(
            "continuous_state_measured",
            ScreeningResult.PASS,
            f"{tail_share:.4f} of {len(candidates)} candidate cells sit beyond the reference's own "
            f"p{limit:g} tail on the axis, and {explained:.4f} of the candidate's mean deviation lies along it "
            f"(reference spread {spread:.6f} about {centre:.6f})",
        ),
        check(
            "candidate_explained_by_known_axis",
            ScreeningResult.WARN if product.is_axis_tail else ScreeningResult.PASS,
            "the candidate is consistent with the tail of an axis the data already carries, so an identity "
            "claim about it must survive that explanation first"
            if product.is_axis_tail
            else "the candidate's deviation is not reproduced by the declared axis alone",
        ),
    ]

    return StepOutput(
        products={SLOT_STATE_AXIS: product},
        metrics={
            "candidate_cells": float(len(candidates)),
            "reference_cells": float(len(reference)),
            "tail_share": tail_share,
            "axis_explained": explained,
            "max_abs_z": product.max_abs_z,
            "reference_stdev": spread,
        },
        checks=tuple(checks),
        warnings=(
            "a marker gradient can be carried by two genes that also separate a real population, so a "
            "not-a-tail reading here is evidence about this axis and not about the candidate's identity",
        ),
    )


def _required(value: object, name: str, *, where: str) -> object:
    if value is None:
        raise missing_parameter(name, where=where)
    return value


def _required_mapping(value: Optional[Mapping[str, float]], name: str, *, where: str) -> Mapping[str, float]:
    if not value:
        raise missing_parameter(name, where=where)
    return {str(gene): float(weight) for gene, weight in value.items()}
