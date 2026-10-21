"""Section 7.6: the coverage measurement and the continuous-state check, run and kept apart (plan V2.0).

The plan splits what a reviewer would call "new" into two numbers that must never merge: how far a
candidate sits from the reference it was compared against, and whether that deviation is only the end
of a gradient the data already carries. Both steps here therefore measure and refuse to conclude -
the verdict over the four conditions lives in ``gates.novelty_verdict``. What the tests check is that
split: that the products hold distances, quantiles and cuts, that the flag a reader would act on is
recomputed from those stored numbers rather than stored itself, and that a step refuses to invent a
reference it was not given.
"""

from dataclasses import replace
from typing import Any

import pytest
from dataset import synthetic_dataset
from workflow_factories import embedding_steps

from bioadapt_schemas.enums import ScreeningResult
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS, Thresholds
from bioadapt_scrnaseq.novelty import (
    QUANTILE_LABELS,
    SELF_ANNOTATION_METHOD,
    SLOT_COVERAGE,
    SLOT_STATE_AXIS,
    coverage_step,
    state_axis_step,
)
from bioadapt_scrnaseq.products import CoverageProduct, StateAxisProduct, read_product
from bioadapt_scrnaseq.specs import TASK_COVERAGE, TASK_STATE_AXIS, declaration_for
from bioadapt_scrnaseq.steps import SLOT_CLUSTERS, SLOT_EMBEDDING, SLOT_EXPRESSION

#: ``rare_state`` is the population the dataset plants as the unusual one, and the executor finds it as
#: its own cluster. It is the candidate most tests measure: an identity claim about it is the case the
#: plan wrote section 7.6 for.
CANDIDATE = "c002"
MONOCYTE = "c001"

#: A signed gradient over the two genes that separate the two largest populations. It is the axis a
#: reviewer can read off the dataset, which is the point of a marker-gradient MVP.
AXIS = {"CD14": 1.0, "CD3D": -1.0}
CUTS = {"tail_quantile": 0.95, "tail_share_min": 0.5, "axis_explained_min": 0.8}


def thresholds(**updates: float) -> Thresholds:
    """A recorded threshold set with one number moved, so a test can name the criterion it is about."""
    return DEFAULT_THRESHOLDS.model_copy(update=updates)


@pytest.fixture(scope="module")
def data() -> Any:
    return synthetic_dataset()


@pytest.fixture(scope="module")
def products(data) -> dict[str, Any]:
    steps = embedding_steps(data)
    return {
        SLOT_EMBEDDING: steps["pca"].product(SLOT_EMBEDDING),
        SLOT_EXPRESSION: steps["normalize"].product(SLOT_EXPRESSION),
        SLOT_CLUSTERS: steps["cluster"].product(SLOT_CLUSTERS),
    }


@pytest.fixture(scope="module")
def coverage(products) -> tuple[Any, CoverageProduct]:
    out = coverage_step(
        products[SLOT_EMBEDDING],
        products[SLOT_CLUSTERS],
        candidate_clusters=[CANDIDATE],
        thresholds=DEFAULT_THRESHOLDS,
    )
    return out, out.product(SLOT_COVERAGE)


@pytest.fixture(scope="module")
def axis(products) -> tuple[Any, StateAxisProduct]:
    out = state_axis_step(
        products[SLOT_EXPRESSION],
        products[SLOT_CLUSTERS],
        candidate_clusters=[MONOCYTE],
        axis=AXIS,
        **CUTS,
    )
    return out, out.product(SLOT_STATE_AXIS)


# ------------------------------------------------------------------ coverage: the reference distance


def test_the_candidate_is_measured_against_the_references_own_distribution(coverage, products) -> None:
    """§7.6: a coverage score is a candidate's distance read against normal cells, not a raw number."""
    product = coverage[1]
    sizes = products[SLOT_CLUSTERS].cells_by_cluster()
    assert len(product.rows) == len(sizes[CANDIDATE])
    assert product.median_candidate_distance > product.median_calibration_distance
    assert product.distance_ratio == pytest.approx(
        product.median_candidate_distance / product.median_calibration_distance
    )
    #: The plan asks for the distribution, so both are reported as quantiles rather than collapsed.
    for name in ("calibration_quantiles", "candidate_quantiles"):
        values = getattr(product, name)
        assert sorted(values) == sorted(label for _q, label in QUANTILE_LABELS)
        assert values["p50"] <= values["p90"] <= values["p99"]
    assert {row.cell_key for row in product.rows} == set(sizes[CANDIDATE])
    assert product.thresholds_version == DEFAULT_THRESHOLDS.version
    assert product.criteria == {
        "knn": float(DEFAULT_THRESHOLDS.coverage_knn),
        "distance_ratio_min": float(DEFAULT_THRESHOLDS.coverage_distance_ratio_min),
    }


def test_a_normal_reference_cell_is_not_trivially_zero_distance_from_itself(coverage) -> None:
    """The calibration set has to be measured leave-one-out, or every candidate looks uncovered."""
    product = coverage[1]
    assert product.calibration_cells == product.reference_cells
    assert product.median_calibration_distance > 0.0
    assert product.calibration_quantiles["p50"] > 0.0
    #: A candidate's nearest reference is one of the reference cells, never another candidate cell.
    measured = {row.cell_key for row in product.rows}
    assert all(row.nearest_reference not in measured for row in product.rows)
    assert all(0.0 <= row.calibration_percentile <= 1.0 for row in product.rows)


def test_the_gap_flag_is_recomputed_from_the_stored_numbers_not_stored(coverage) -> None:
    """Nothing in a coverage product may hold the conclusion, so a reader re-derives it (§7.6)."""
    product = coverage[1]
    assert product.coverage_gap is False
    payload = product.payload()
    assert "coverage_gap" not in payload and "novel" not in str(sorted(payload))
    lowered = replace(product, criteria={**product.criteria, "distance_ratio_min": 1.2})
    assert lowered.coverage_gap is True
    raised = replace(product, criteria={**product.criteria, "distance_ratio_min": 100.0})
    assert raised.coverage_gap is False
    assert raised.uncovered_share() == product.uncovered_share()


def test_the_coverage_node_refuses_a_reference_it_cannot_measure_against(products) -> None:
    """k comes from the threshold set: the step reports a gap in coverage rather than shortening k."""
    with pytest.raises(Exception, match="needs at least 24 reference cells, and the annotation leaves 22"):
        coverage_step(
            products[SLOT_EMBEDDING],
            products[SLOT_CLUSTERS],
            candidate_clusters=[MONOCYTE, CANDIDATE, "c000", "c003"],
            thresholds=thresholds(coverage_knn=24),
        )
    with pytest.raises(Exception, match="names the clusters it is measuring"):
        coverage_step(
            products[SLOT_EMBEDDING], products[SLOT_CLUSTERS], candidate_clusters=[], thresholds=DEFAULT_THRESHOLDS
        )
    with pytest.raises(Exception, match="clusters the reference annotation does not name"):
        coverage_step(
            products[SLOT_EMBEDDING],
            products[SLOT_CLUSTERS],
            candidate_clusters=["c099"],
            thresholds=DEFAULT_THRESHOLDS,
        )


def test_a_labelled_cell_the_embedding_does_not_hold_is_refused(products) -> None:
    """Coverage is measured in one space: dropping the cell would quietly move the answer."""
    embedding = products[SLOT_EMBEDDING]
    missing = products[SLOT_CLUSTERS].labels[0].cell_key
    short = replace(embedding, vectors=tuple(row for row in embedding.vectors if row.cell_key != missing))
    with pytest.raises(Exception, match="have no vector in the embedding this node was handed"):
        coverage_step(
            short, products[SLOT_CLUSTERS], candidate_clusters=[CANDIDATE], thresholds=DEFAULT_THRESHOLDS
        )


def test_labels_from_this_runs_own_partition_are_flagged_as_not_external(coverage, products) -> None:
    """§7.6 wants an external reference annotation; separation from this run's own clusters is not that."""
    emitted = {check.name: check.result for check in coverage[0].checks}
    assert coverage[1].reference_digest == products[SLOT_CLUSTERS].file_sha256()
    assert emitted["reference_labels_are_external"] is ScreeningResult.WARN
    external = replace(products[SLOT_CLUSTERS], method="external_pbmc_annotation_v1")
    out = coverage_step(
        products[SLOT_EMBEDDING], external, candidate_clusters=[CANDIDATE], thresholds=DEFAULT_THRESHOLDS
    )
    assert "reference_labels_are_external" not in {check.name for check in out.checks}
    assert SELF_ANNOTATION_METHOD == "greedy_modularity_local_moving"


def test_a_thin_calibration_distribution_is_recorded_rather_than_quietly_trusted(products) -> None:
    pool = coverage_step(
        products[SLOT_EMBEDDING],
        products[SLOT_CLUSTERS],
        candidate_clusters=["c000", "c001", "c002", "c003"],
        thresholds=thresholds(coverage_knn=20),
    )
    product = pool.product(SLOT_COVERAGE)
    emitted = {check.name: check.result for check in pool.checks}
    assert product.reference_cells == 22 < 4 * int(DEFAULT_THRESHOLDS.coverage_knn)
    assert emitted["thin_calibration_distribution"] is ScreeningResult.WARN
    #: A coarse empirical percentile and a thin pool: the step still measures, and says what it cannot decide.
    assert emitted["coverage_gap_measured"] is (
        ScreeningResult.WARN if product.coverage_gap else ScreeningResult.PASS
    )


def test_the_coverage_step_reports_only_what_its_spec_declared(coverage) -> None:
    declaration = declaration_for(TASK_COVERAGE)
    emitted = {check.name for check in coverage[0].checks}
    assert emitted <= set(declaration.declared_checks)
    assert set(declaration.quality_checks) <= emitted


def test_the_coverage_bytes_are_reproducible_and_reread_to_the_same_judgement(coverage, products) -> None:
    again = coverage_step(
        products[SLOT_EMBEDDING],
        products[SLOT_CLUSTERS],
        candidate_clusters=[CANDIDATE],
        thresholds=DEFAULT_THRESHOLDS,
    )
    raw = coverage[1].json_bytes()
    assert again.product(SLOT_COVERAGE).json_bytes() == raw
    reread = read_product("coverage_v1", raw)
    assert isinstance(reread, CoverageProduct)
    assert reread.coverage_gap == coverage[1].coverage_gap
    assert reread.content_fingerprint() == coverage[1].content_fingerprint()


# ------------------------------------------------------------------ the continuous-state check


def test_a_candidate_at_the_end_of_a_declared_gradient_is_measured_as_one(axis) -> None:
    """§7.6: the fourth condition asks whether the deviation is only the tail of a known axis."""
    product = axis[1]
    assert product.method == "marker_gradient_projection"
    assert "velocity" not in product.method
    assert product.axis == pytest.approx(AXIS)
    assert product.criteria == pytest.approx(CUTS)
    assert product.reference_stdev > 0.0
    assert product.axis_explained > 0.9
    assert product.tail_share == 1.0
    assert product.is_axis_tail is True
    assert {check.name: check.result for check in axis[0].checks}["candidate_explained_by_known_axis"] is (
        ScreeningResult.WARN
    )
    #: Every row's z is the stored projection in units of the stored reference spread.
    for row in product.rows:
        assert row.z == pytest.approx((row.projection - product.reference_median) / product.reference_stdev)
    assert product.max_abs_z == max(abs(row.z) for row in product.rows)


def test_a_candidate_that_is_not_on_the_axis_is_reported_as_not_on_it(products) -> None:
    out = state_axis_step(
        products[SLOT_EXPRESSION],
        products[SLOT_CLUSTERS],
        candidate_clusters=[CANDIDATE],
        axis=AXIS,
        **CUTS,
    )
    product = out.product(SLOT_STATE_AXIS)
    assert product.tail_share == 0.0
    assert product.is_axis_tail is False
    assert {check.name: check.result for check in out.checks}["candidate_explained_by_known_axis"] is (
        ScreeningResult.PASS
    )


def test_the_tail_verdict_follows_the_recorded_cuts_not_a_stored_answer(axis) -> None:
    product = axis[1]
    assert "is_axis_tail" not in product.payload()
    strict = replace(
        product, criteria={**product.criteria, "tail_share_min": 1.0, "axis_explained_min": product.axis_explained}
    )
    assert strict.is_axis_tail is True
    stricter = replace(product, criteria={**product.criteria, "axis_explained_min": 0.99})
    assert stricter.is_axis_tail is False
    #: One condition alone is not the answer: the plan requires both shares past their cuts.
    partial = replace(product, criteria={**product.criteria, "tail_share_min": 1.5})
    assert partial.is_axis_tail is False


def test_the_axis_step_refuses_an_axis_it_cannot_measure(products) -> None:
    expression = products[SLOT_EXPRESSION]
    clusters = products[SLOT_CLUSTERS]
    with pytest.raises(Exception, match="defined on genes this matrix does not measure"):
        state_axis_step(
            expression, clusters, candidate_clusters=[MONOCYTE], axis={"NOPE1": 1.0}, **CUTS
        )
    with pytest.raises(Exception, match="all-zero weights"):
        state_axis_step(expression, clusters, candidate_clusters=[MONOCYTE], axis={"CD14": 0.0}, **CUTS)
    with pytest.raises(Exception, match="is not a tail"):
        state_axis_step(
            expression,
            clusters,
            candidate_clusters=[MONOCYTE],
            axis=AXIS,
            tail_quantile=0.4,
            tail_share_min=0.5,
            axis_explained_min=0.8,
        )
    with pytest.raises(Exception, match="no spread along this axis"):
        #: The reference here is the b-cell cluster, which expresses neither of the axis genes at all.
        state_axis_step(
            expression,
            clusters,
            candidate_clusters=["c001", "c002", "c003", "c004"],
            axis={"CD14": 1.0},
            **CUTS,
        )


def test_the_stage0_cuts_are_inputs_and_not_defaults(products) -> None:
    """Appendix C1: a tail cut written into the node would be a threshold with no source."""
    with pytest.raises(Exception, match="requires parameter 'tail_quantile'"):
        state_axis_step(
            products[SLOT_EXPRESSION],
            products[SLOT_CLUSTERS],
            candidate_clusters=[MONOCYTE],
            axis=AXIS,
            tail_share_min=0.5,
            axis_explained_min=0.8,
        )
    with pytest.raises(Exception, match="requires parameter 'axis'"):
        state_axis_step(
            products[SLOT_EXPRESSION],
            products[SLOT_CLUSTERS],
            candidate_clusters=[MONOCYTE],
            axis=None,
            **CUTS,
        )


def test_the_state_axis_step_reports_only_what_its_spec_declared(axis) -> None:
    declaration = declaration_for(TASK_STATE_AXIS)
    emitted = {check.name for check in axis[0].checks}
    assert emitted <= set(declaration.declared_checks)
    assert set(declaration.quality_checks) <= emitted
    assert axis[0].warnings


def test_the_state_axis_bytes_are_reproducible_and_reread_to_the_same_judgment(axis, products) -> None:
    again = state_axis_step(
        products[SLOT_EXPRESSION],
        products[SLOT_CLUSTERS],
        candidate_clusters=[MONOCYTE],
        axis=AXIS,
        **CUTS,
    ).product(SLOT_STATE_AXIS)
    raw = again.json_bytes()
    assert raw == axis[1].json_bytes()
    reread = read_product("state_axis_v1", raw)
    assert isinstance(reread, StateAxisProduct)
    assert reread.is_axis_tail == axis[1].is_axis_tail
    assert reread.content_fingerprint() == axis[1].content_fingerprint()
