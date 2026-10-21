"""The section 7.5 robustness protocol, executed (plan V2.0 sections 7.5, 9.4).

The protocol is expensive by construction - every arm re-runs the chain - so the tests share one full
run and only build their own data where the point of the test is the refusal. What is checked here is
the shape of the evidence: that all five classes were measured, that each measurement names the
partition it came from, that chance is subtracted where the plan demands it, and that the verdict is a
function of the recorded numbers rather than a field someone could write.
"""

from dataclasses import replace
from typing import Any

import pytest
from dataset import synthetic_dataset
from workflow_factories import (
    NULL_DRAWS,
    chain_parameter_values,
    chain_parameters,
    embedding_steps,
    robustness_protocol,
)

from bioadapt_policies.robustness import ProtocolDeviation
from bioadapt_schemas.enums import PerturbationKind, ScreeningResult
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from bioadapt_scrnaseq.errors import StepFailure
from bioadapt_scrnaseq.numeric import median
from bioadapt_scrnaseq.products import StabilityProduct, read_product
from bioadapt_scrnaseq.robustness import (
    SLOT_STABILITY,
    ChainParameters,
    permutation_overlap_null,
    robustness_step,
    run_chain,
)
from bioadapt_scrnaseq.specs import TASK_ROBUSTNESS, declaration_for
from bioadapt_scrnaseq.steps import SLOT_CLUSTERS, SLOT_EXPRESSION


@pytest.fixture(scope="module")
def data() -> Any:
    return synthetic_dataset()


@pytest.fixture(scope="module")
def chain_input(data) -> dict[str, Any]:
    steps = embedding_steps(data)
    return {
        "expression": steps["normalize"].product(SLOT_EXPRESSION),
        "baseline": steps["cluster"].product(SLOT_CLUSTERS),
    }


def run_protocol(chain_input, candidates, *, protocol=None, chain=None):
    return robustness_step(
        chain_input["expression"],
        chain_input["baseline"],
        protocol=protocol if protocol is not None else robustness_protocol(),
        thresholds=DEFAULT_THRESHOLDS,
        chain=chain if chain is not None else chain_parameters(),
        candidate_clusters=candidates,
    )


@pytest.fixture(scope="module")
def candidates(chain_input) -> tuple[str, str]:
    """The largest and the smallest group: one the protocol should keep, one it is likeliest to lose."""
    sizes = chain_input["baseline"].cells_by_cluster()
    ordered = sorted(sizes, key=lambda name: (-len(sizes[name]), name))
    return (ordered[0], ordered[-1])


@pytest.fixture(scope="module")
def run(chain_input, candidates):
    out = run_protocol(chain_input, candidates)
    return out, out.product(SLOT_STABILITY)


# ------------------------------------------------------------------ the protocol actually ran


def test_all_five_classes_are_measured_and_every_planned_arm_appears(run, candidates) -> None:
    product = run[1]
    assert product.kinds == tuple(sorted(kind.value for kind in PerturbationKind.mandatory_protocol()))
    assert product.covered_kinds == product.kinds
    #: 4 resolutions, 4 perturbed seeds, the two feature counts that move the run, one replicate per
    #: donor, 1 subsample. The 1.0 feature factor is the reference the multipliers are applied to.
    assert {kind: len(product.rows_for(kind)) for kind in product.kinds} == {
        "resolution": 4 * len(candidates),
        "seed": 4 * len(candidates),
        "feature_subset": 2 * len(candidates),
        "leave_one_donor": 8 * len(candidates),
        "subsampling": 1 * len(candidates),
    }
    assert {row.arm for row in product.rows_for("seed")} == {"seed+1", "seed+2", "seed+3", "seed+4"}
    assert product.omitted_arms == ()
    assert product.deviation is None


def test_the_step_reports_only_what_its_spec_declared(run) -> None:
    """§9.4: a declared check that never ran is a breach, and an undeclared one is a surprise."""
    declaration = declaration_for(TASK_ROBUSTNESS)
    emitted = {check.name for check in run[0].checks}
    assert emitted <= set(declaration.declared_checks)
    assert set(declaration.quality_checks) <= emitted


def test_a_stable_candidate_is_stable_because_the_numbers_say_so(run) -> None:
    product = run[1]
    assert product.stable is True
    assert product.agreement_ratio == 1.0
    assert product.verdict() == "stable"
    for kind in product.kinds:
        rows = product.rows_for(kind)
        assert product.judgement(kind) == "pass"
        assert median([row.cc_ari for row in rows]) >= product.criteria["cc_ari_min"]
        assert median([row.jaccard for row in rows]) >= product.criteria["jaccard_min"]
    #: The weakest arm in the whole protocol still beat the random sets that overlap it by luck, which is
    #: what lets a class pass on its median rather than on its worst replicate.
    weakest = min(product.rows, key=lambda row: row.jaccard)
    assert weakest.jaccard > weakest.permutation_jaccard


def test_the_verdict_is_derived_from_the_recorded_numbers_not_stored(run) -> None:
    product = run[1]
    document = product.document()
    assert "stable" not in document
    assert all("stable" not in key and value != "stable" for row in document["arms"] for key, value in row.items())

    impossible = replace(product, criteria={**product.criteria, "cc_ari_min": 0.999, "jaccard_min": 0.999})
    assert impossible.stable is False
    assert impossible.verdict().startswith("not stable")
    assert replace(product, criteria={**product.criteria, "pass_ratio_min": 0.0}).stable is True

    #: One weak replicate is recorded but does not sink its class; a class whose median falls does.
    first = product.rows[0]
    kind = first.kind
    dragged = [replace(row, jaccard=0.01) if row is first else row for row in product.rows]
    assert replace(product, rows=tuple(dragged)).judgement(kind) == "pass"
    sunk = replace(
        product, rows=tuple(replace(row, jaccard=0.01) if row.kind == kind else row for row in product.rows)
    )
    assert sunk.judgement(kind) == "fail"


def test_every_measurement_names_the_partition_it_was_compared_against(run, chain_input, candidates) -> None:
    product = run[1]
    assert product.baseline_clusters_digest == chain_input["baseline"].file_sha256()
    assert product.baseline_expression_digest == chain_input["expression"].file_sha256()
    assert product.thresholds_version == DEFAULT_THRESHOLDS.version
    for row in product.rows:
        assert row.baseline_cluster in candidates
        assert row.baseline_cells == len(chain_input["baseline"].cells_by_cluster()[row.baseline_cluster])
        assert row.matched_cluster is not None and row.matched_cells >= 1
        assert 0.0 <= row.marker_overlap <= 1.0
        #: Every candidate here sits under the small-cluster line, so chance must have been measured.
        assert row.permutation_jaccard is not None
        assert row.jaccard > row.permutation_jaccard


def test_the_product_survives_the_store_and_reread_identically(run) -> None:
    product = run[1]
    raw = product.json_bytes()
    stored = read_product("stability_v1", raw)
    assert isinstance(stored, StabilityProduct)
    assert stored.json_bytes() == raw
    assert stored.verdict() == product.verdict()
    assert stored.content_fingerprint() == product.content_fingerprint()


def test_the_metrics_a_card_reads_are_the_step_s_own(run, candidates) -> None:
    metrics = run[0].metrics
    assert metrics["classes_covered"] == 5.0
    assert metrics["classes_passed"] == 5.0
    assert metrics["perturbation_rows"] == 19.0 * len(candidates)
    assert metrics["arms_not_runnable"] == 0.0
    assert metrics["candidate_clusters"] == float(len(candidates))
    assert metrics["smallest_candidate_cells"] > 0


# ------------------------------------------------------------------ refusals worth paying for


def test_a_baseline_the_parameters_do_not_produce_is_refused(chain_input, candidates) -> None:
    """§4.1: stability measured around a partition this run cannot reproduce is not evidence."""
    with pytest.raises(StepFailure, match="does not reproduce the clustering") as raised:
        run_protocol(chain_input, candidates, chain=chain_parameters(resolution=2.0))
    assert raised.value.error_class is ErrorClass.FAILED
    assert raised.value.field == SLOT_CLUSTERS


def test_a_candidate_list_the_partition_does_not_name_is_refused(chain_input) -> None:
    with pytest.raises(StepFailure, match="does not name") as raised:
        run_protocol(chain_input, ("c999",))
    assert raised.value.field == "candidate_clusters"


def test_a_node_that_judges_nothing_is_refused(chain_input) -> None:
    with pytest.raises(StepFailure, match="names the clusters it is judging"):
        run_protocol(chain_input, ())


def test_a_chain_number_that_is_missing_stays_an_input_a_person_resolves() -> None:
    """Appendix C1: the executor may not quietly pick a resolution for the project."""
    values = chain_parameter_values()
    del values["n_components"]
    with pytest.raises(StepFailure, match="requires parameter 'n_components'") as raised:
        ChainParameters.from_parameters(values)
    assert raised.value.error_class is ErrorClass.NEEDS_INPUT


# ------------------------------------------------------------------ reduced protocols and nulls


def test_a_deviation_is_carried_into_the_product_and_shown_on_the_card(chain_input, candidates) -> None:
    deviation = ProtocolDeviation(
        approved_by="project_owner_01",
        reason="the run budget does not cover eight leave-one-donor replicates",
    )
    out = run_protocol(
        chain_input,
        candidates,
        protocol=robustness_protocol(
            leave_one_donor=False,
            feature_factors=[1.0],
            subsample_fraction=None,
            seed_count=1,
            deviation=deviation,
        ),
    )
    product = out.product(SLOT_STABILITY)
    assert product.kinds == ("resolution",)
    assert len(product.omitted_arms) == 4
    assert "project_owner_01" in product.deviation
    assert product.protocol_complete is False
    assert "recorded deviation" in product.verdict()
    covered = next(check for check in out.checks if check.name == "protocol_arms_covered")
    assert covered.result is ScreeningResult.PASS and "arms the protocol omits" in covered.detail


def test_the_protocol_is_byte_reproducible_across_repeats(chain_input, candidates) -> None:
    protocol = robustness_protocol(
        resolution_factors=[0.5],
        feature_factors=[],
        leave_one_donor=False,
        subsample_fraction=None,
        seed_count=1,
        deviation=ProtocolDeviation(approved_by="project_owner_01", reason="repeatability probe"),
    )
    first = run_protocol(chain_input, candidates, protocol=protocol)
    second = run_protocol(chain_input, candidates, protocol=protocol)
    assert first.product(SLOT_STABILITY).json_bytes() == second.product(SLOT_STABILITY).json_bytes()


def test_a_class_the_dataset_cannot_support_is_recorded_as_a_gap_not_dropped(chain_input, candidates) -> None:
    """A perturbation that refuses to run has to show up, or the pass ratio reads over a smaller set."""
    out = run_protocol(chain_input, candidates, protocol=robustness_protocol(subsample_fraction=0.02))
    by_name = {check.name: check for check in out.checks}
    #: 2% of 128 cells is three cells: no neighbourhood, let alone a ten-component embedding, can be built.
    assert by_name["perturbation_arm_not_runnable"].result is ScreeningResult.WARN
    assert "subsampling" in by_name["planned_class_unmeasured"].detail
    assert by_name["planned_class_unmeasured"].result is ScreeningResult.WARN
    assert out.metrics["classes_covered"] == 4.0


def test_the_random_set_null_rises_as_the_drawn_set_grows() -> None:
    clusters = {
        "c000": tuple(f"cell{index:03d}" for index in range(30)),
        "c001": tuple(f"cell{index:03d}" for index in range(30, 60)),
        "c002": tuple(f"cell{index:03d}" for index in range(60, 100)),
    }
    small = permutation_overlap_null(clusters, size=5, replicates=NULL_DRAWS, seed=1)
    large = permutation_overlap_null(clusters, size=40, replicates=NULL_DRAWS, seed=1)
    assert 0.0 <= small < 0.5
    assert large > small
    assert permutation_overlap_null(clusters, size=5, replicates=NULL_DRAWS, seed=1) == small


def test_the_chain_reproducer_is_the_same_pipeline_the_workflow_ran(chain_input) -> None:
    """run_chain is what the arms perturb, so it has to return the clustering the plan already made."""
    baseline = chain_input["baseline"]
    reproduced = run_chain(chain_input["expression"], chain_parameters())
    assert {row.cell_key: row.cluster for row in reproduced.labels} == {
        row.cell_key: row.cluster for row in baseline.labels
    }
    assert reproduced.resolution == chain_parameters().resolution
    #: The graph the partition was read off is the same graph the workflow's neighbours step built.
    assert reproduced.graph_digest == baseline.graph_digest
