"""The registered-tool wrapper: the only place a step becomes a ``ToolResult`` (§9.4, §9.6, §10.1).

The steps are tested in ``test_steps.py`` and the driver in ``tests/integration``; what lives here is
the seam between them, where the promises written into a ToolSpec are checked against the data a node
was actually handed. The two refusals that matter most produce no error at run time: pseudobulk summed
from log-normalised values, or features ranked on raw counts, returns an ordinary looking table that
means something else. So the wrapper's own behaviour is asserted here - both for what it stops and for
what a result has to record when a check fails, when a declared check never ran, when a node emitted
less than its task must emit, and when the tool is an ad-hoc submission.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

import pytest

from bioadapt_schemas.entities import Genome
from bioadapt_schemas.enums import GenomeIdSystem, ReproducibilityLevel, ScreeningResult, ToolClass
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from bioadapt_tool_registry.enums import ToolResultStatus
from bioadapt_tool_registry.registry import AdhocApproval, ToolRegistry, ToolSpec

from bioadapt_scrnaseq import (
    SLOT_CLUSTERS,
    SLOT_COVERAGE,
    SLOT_COUNTS,
    SLOT_EMBEDDING,
    SLOT_EXPRESSION,
    SLOT_FEATURES,
    SLOT_GRAPH,
    SLOT_QC_FLAGS,
    SLOT_STATE_AXIS,
    TASK_CLUSTERING,
    TASK_COVERAGE,
    TASK_EMBEDDING,
    TASK_FEATURE_SELECTION,
    TASK_MARKERS,
    TASK_NEIGHBORS,
    TASK_NORMALIZE,
    TASK_PSEUDOBULK_DE,
    TASK_QC,
    TASK_STATE_AXIS,
    adhoc_spec,
    read_product,
)
from bioadapt_scrnaseq.errors import StepFailure
from bioadapt_scrnaseq.implementation import (
    TASK_RUNNERS,
    Invocation,
    StepOutput,
    ToolInput,
    check_compatibility,
    execute,
    parameters_sha256,
    required_slots,
)
from bioadapt_scrnaseq.products import CellScoreProduct, CellScoreRow, ExpressionProduct, Product
from bioadapt_scrnaseq.specs import DECLARATIONS, Deployment, declaration_for, reference_tool_specs
from bioadapt_scrnaseq.steps import check
from dataset import MITO_GENES, RIBOSOMAL_GENES, synthetic_dataset
from workflow_factories import (
    MAX_FLAGGED_SHARE,
    MAX_MEAN,
    MIN_MEAN,
    MIN_VARIANCE,
    N_COMPONENTS,
    N_NEIGHBOURS,
    N_TOP_FEATURES,
    PERMUTATION_REPLICATES,
    RESOLUTION,
    SEEDS,
    TARGET_SUM,
    TOP_MARKERS,
    qc_policy,
)

RUN_ID, PROJECT_ID = "run_unit", "proj_unit"
NOW = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)

DEPLOYMENT = Deployment(
    container_repository="registry.internal/bioadapt/scrnaseq-reference",
    container_tag="1.0.0",
    container_digest="sha256:" + "ab" * 32,
    tool_version="1.0.0",
    species=("human",),
    cpu=4,
    memory_mb=16384,
    max_minutes=60,
    measured_peak_memory_mb=1200,
    measured_on="synthetic_pbmc_v1",
    license_spdx_id="BSD-3-Clause",
    license_display_name="Scanpy reference steps",
    license_reviewed_at=date(2026, 5, 4),
    license_review_note="reviewed against the platform's own licence matrix on 2026-05-04",
)

SPECS: Mapping[str, ToolSpec] = {spec.task: spec for spec in reference_tool_specs(DEPLOYMENT).values()}

COUNTS_TOOLS = (TASK_QC, TASK_NORMALIZE, TASK_PSEUDOBULK_DE)
MATRIX_FREE_TOOLS = (TASK_COVERAGE,)


def stage0(task: str, **overrides: Any) -> dict[str, Any]:
    """The stage-0 numbers this dataset is analysed with.

    They are inputs to the node and never defaults the implementation may supply (appendix C1), so a
    test can ask for one to be absent by passing ``None`` and expect a refusal instead of a guess.
    """
    tables: dict[str, dict[str, Any]] = {
        TASK_QC: {
            "qc_policy": qc_policy(),
            "thresholds_version": DEFAULT_THRESHOLDS.version,
            "max_flagged_share": MAX_FLAGGED_SHARE,
            "mito_genes": list(MITO_GENES),
            "ribosomal_genes": list(RIBOSOMAL_GENES),
        },
        TASK_NORMALIZE: {"target_sum": TARGET_SUM, "tolerance": 1e-6},
        TASK_FEATURE_SELECTION: {
            "n_top_features": N_TOP_FEATURES,
            "min_mean": MIN_MEAN,
            "max_mean": MAX_MEAN,
            "min_variance": MIN_VARIANCE,
            "seed": SEEDS["highly_variable_features"],
        },
        TASK_EMBEDDING: {"n_components": N_COMPONENTS, "seed": SEEDS["pca"], "iterations": 200, "n_loadings": 10},
        TASK_NEIGHBORS: {"k": N_NEIGHBOURS, "seed": SEEDS["neighbors"], "metric": "euclidean"},
        TASK_CLUSTERING: {"resolution": RESOLUTION, "seed": SEEDS["cluster"]},
        TASK_MARKERS: {"top_n": TOP_MARKERS},
        TASK_PSEUDOBULK_DE: {
            "contrasts": [{"group_a": "case", "group_b": "control"}],
            "permutation_replicates": PERMUTATION_REPLICATES,
            "seed": SEEDS["pseudobulk_de"],
            "thresholds": DEFAULT_THRESHOLDS,
        },
    }
    fields = dict(tables[task])
    fields.update(overrides)
    return {key: value for key, value in fields.items() if value is not None}


class Sink:
    """Where a product becomes an asset. The wrapper must not choose it, so it is handed in (§9.4)."""

    def __init__(self) -> None:
        self.written: dict[str, str] = {}
        self.products: dict[str, Product] = {}

    def write(self, product: Product) -> str:
        asset_id = "as_" + product.file_sha256()[:12]
        self.written[product.slot] = asset_id
        self.products[product.slot] = product
        return asset_id


def invoke(
    task: str,
    products: Sequence[Product],
    *,
    node_id: str = "qc",
    parameters: Optional[Mapping[str, Any]] = None,
    spec: Optional[ToolSpec] = None,
    sink: Optional[Sink] = None,
) -> tuple[Any, Sink]:
    invocation = Invocation(
        run_id=RUN_ID,
        node_id=node_id,
        project_id=PROJECT_ID,
        spec=spec or SPECS[task],
        inputs=tuple(
            ToolInput(slot=product.slot, asset_id=f"as_{product.file_sha256()[:12]}", product=product)
            for product in products
        ),
        parameters=dict(parameters if parameters is not None else stage0(task)),
    )
    used = sink if sink is not None else Sink()
    return execute(invocation, used, started_at=NOW, finished_at=NOW), used


@pytest.fixture(scope="module")
def data():
    return synthetic_dataset()


@pytest.fixture(scope="module")
def chain(data) -> dict[str, Product]:
    """The products a node may be handed, produced by the steps themselves rather than by hand."""
    from bioadapt_scrnaseq.steps import (
        cluster_step,
        hvg_step,
        neighbors_step,
        normalize_step,
        pca_step,
        qc_step,
    )

    qc = qc_step(
        data.matrix,
        qc_policy(),
        thresholds_version=DEFAULT_THRESHOLDS.version,
        max_flagged_share=MAX_FLAGGED_SHARE,
        mito_genes=MITO_GENES,
        ribosomal_genes=RIBOSOMAL_GENES,
        doublet_scores=data.doublet_scores,
        ambient_scores=data.ambient_scores,
    )
    counts = qc.product(SLOT_COUNTS)
    expression = normalize_step(counts.matrix, target_sum=TARGET_SUM).product(SLOT_EXPRESSION)
    features = hvg_step(
        expression,
        n_top_features=N_TOP_FEATURES,
        min_mean=MIN_MEAN,
        max_mean=MAX_MEAN,
        min_variance=MIN_VARIANCE,
        seed=SEEDS["highly_variable_features"],
    ).product(SLOT_FEATURES)
    embedding = pca_step(expression, features, n_components=N_COMPONENTS, seed=SEEDS["pca"]).product(SLOT_EMBEDDING)
    graph = neighbors_step(embedding, k=N_NEIGHBOURS, seed=SEEDS["neighbors"]).product(SLOT_GRAPH)
    clusters = cluster_step(graph, resolution=RESOLUTION, seed=SEEDS["cluster"]).product(SLOT_CLUSTERS)
    return {
        SLOT_QC_FLAGS: qc.product(SLOT_QC_FLAGS),
        SLOT_COUNTS: counts,
        SLOT_EXPRESSION: expression,
        SLOT_FEATURES: features,
        SLOT_EMBEDDING: embedding,
        SLOT_GRAPH: graph,
        SLOT_CLUSTERS: clusters,
    }


def screen(
    slot: str, metric: str, chain: Mapping[str, Product], *, flagged: Optional[str] = None
) -> CellScoreProduct:
    """A screen's own output: one cell above the rule's bound, the rest below it."""
    keys = chain[SLOT_COUNTS].matrix.cells
    return CellScoreProduct(
        slot=slot,
        metric=metric,
        source_tool="scrublet" if metric == "doublet" else "decontox",
        source_tool_version="0.2.3",
        rows=tuple(
            CellScoreRow(cell_key=cell.cell_key, score=0.9 if cell.cell_key == flagged else 0.1)
            for cell in keys
        ),
    )


# ------------------------------------------------------------------ the specs the wrapper reads


def test_the_reference_steps_build_specs_the_registry_admits() -> None:
    registry = ToolRegistry()
    built = reference_tool_specs(DEPLOYMENT)
    assert len(built) == len(DECLARATIONS) == 11
    for spec in built.values():
        registry.register(spec)
    admitted = registry.select()
    assert len(admitted) == 11
    for spec in admitted:
        assert spec.tool_class is ToolClass.REGISTERED
        assert spec.container_image.digest == DEPLOYMENT.container_digest
        assert spec.supported_modalities == ["RNA"]
        #: None of these steps promises that re-running a partial attempt is safe (§9.4), so the
        #: driver may not retry them merely because a budget happens to allow it.
        assert spec.retry_limit == 0


def test_a_spec_cannot_be_built_for_a_task_the_workflow_never_declared() -> None:
    with pytest.raises(ValueError, match="no such workflow task"):
        reference_tool_specs(DEPLOYMENT, tasks=["not_a_task"])


def test_each_spec_states_whether_its_input_is_counts_or_log_normalised() -> None:
    for task, spec in SPECS.items():
        compat = spec.reference_compatibility
        if task in COUNTS_TOOLS:
            assert compat.requires_counts is True and compat.needs_log is False, task
        elif task in MATRIX_FREE_TOOLS:
            #: Coverage is measured in a vector space over a label set: no expression matrix enters the
            #: step, so a log-scale promise here would be a flag that can never be checked.
            assert compat.requires_counts is False and compat.needs_log is False, task
        else:
            assert compat.requires_counts is False and compat.needs_log is True, task
        assert compat.id_system is GenomeIdSystem.HGNC_SYMBOL, task


def test_the_slots_a_task_reads_are_declared_not_inferred() -> None:
    """§4.1: an inferred input is the same class of bug as an inferred transform."""
    assert required_slots(TASK_QC) == (SLOT_COUNTS,)
    assert required_slots(TASK_MARKERS) == (SLOT_CLUSTERS, SLOT_EXPRESSION)
    assert required_slots(TASK_PSEUDOBULK_DE) == (SLOT_CLUSTERS, SLOT_COUNTS)
    #: The screens a QC node may or may not be handed are not required, so they never appear here.
    assert "doublet_scores" not in required_slots(TASK_QC)
    with pytest.raises(ValueError, match="no workflow step is declared"):
        required_slots("not_a_task")


# ------------------------------------------------------------------ the refusals that keep meaning


def test_a_counts_tool_handed_log_normalised_values_is_stopped_before_the_step_runs(chain) -> None:
    """Pseudobulk summed from log values returns a table that looks ordinary and means something else."""
    log = replace(chain[SLOT_EXPRESSION], slot=SLOT_COUNTS)
    assert log.counts is False
    with pytest.raises(Exception, match="sums or measures raw counts") as raised:
        check_compatibility(SPECS[TASK_PSEUDOBULK_DE], [log])
    assert "log2_cpm" in str(raised.value)
    result, _ = invoke(
        TASK_PSEUDOBULK_DE,
        [log, chain[SLOT_CLUSTERS]],
        node_id="pseudobulk_de",
        parameters=stage0(TASK_PSEUDOBULK_DE),
    )
    assert result.status is ToolResultStatus.FAILED
    assert result.output_asset_ids == []
    assert "raw counts" in result.errors[0].message


def test_a_log_tool_handed_raw_counts_is_stopped(chain) -> None:
    counts = replace(chain[SLOT_COUNTS], slot=SLOT_EXPRESSION)
    assert counts.counts is True
    with pytest.raises(Exception, match="reads log-normalised expression"):
        check_compatibility(SPECS[TASK_FEATURE_SELECTION], [counts])
    result, _ = invoke(TASK_FEATURE_SELECTION, [counts], node_id="feature_selection")
    assert result.status is ToolResultStatus.FAILED
    assert "raw counts" in result.errors[0].message


def test_a_matrix_from_another_identifier_system_is_refused(chain) -> None:
    """A symbol/Ensembl mix matches no gene and reads as dropout, so the wrapper stops it (§9.4)."""
    counts = chain[SLOT_COUNTS]
    ensembl = replace(
        counts,
        matrix=replace(
            counts.matrix,
            genome=Genome(
                build=counts.matrix.genome.build,
                annotation_source="Ensembl",
                version=counts.matrix.genome.version,
                id_system=GenomeIdSystem.ENSEMBL,
            ),
        ),
    )
    with pytest.raises(Exception, match="was reviewed for hgnc_symbol identifiers") as raised:
        check_compatibility(SPECS[TASK_QC], [ensembl])
    assert "reads as dropout" in str(raised.value)


def test_two_reference_builds_in_one_invocation_are_refused(chain) -> None:
    counts = chain[SLOT_COUNTS]
    other_build = replace(
        counts,
        slot="ambient_scores",
        matrix=replace(
            counts.matrix,
            genome=Genome(
                build="gencode-v43",
                annotation_source="GENCODE",
                version="43",
                id_system=counts.matrix.genome.id_system,
            ),
        ),
    )
    with pytest.raises(Exception, match="cannot read two reference builds"):
        check_compatibility(SPECS[TASK_QC], [counts, other_build])


def test_a_slot_holding_the_wrong_kind_of_product_is_refused(chain) -> None:
    wrong = replace(chain[SLOT_QC_FLAGS], slot=SLOT_COUNTS)
    result, _ = invoke(TASK_QC, [wrong])
    assert result.status is ToolResultStatus.FAILED
    assert "this tool needs ExpressionProduct" in result.errors[0].message


def test_a_node_that_wires_nothing_the_tool_reads_is_refused(chain) -> None:
    """A slot present in the plan but absent from the tool's own required inputs is a wiring gap."""
    result, _ = invoke(TASK_QC, [screen("doublet_scores", "doublet", chain)])
    assert result.status is ToolResultStatus.FAILED
    assert f"declared no input in slot {SLOT_COUNTS!r}" in result.errors[0].message


def test_an_invocation_that_read_no_bytes_is_not_a_result_the_contracts_can_name(chain) -> None:
    """``ToolResult.input_asset_ids`` needs at least one asset, so this cannot be reported as a run."""
    with pytest.raises(StepFailure, match="no declared input asset"):
        invoke(TASK_QC, [])


def test_a_task_the_reference_implementation_does_not_have_is_refused(chain) -> None:
    spec = SPECS[TASK_QC].model_copy(update={"task": "spatial_deconvolution"})
    result, _ = invoke(TASK_QC, [chain[SLOT_COUNTS]], spec=spec)
    assert result.status is ToolResultStatus.FAILED
    assert "does not implement" in result.errors[0].message


def test_a_screen_slot_holding_another_screen_is_refused(chain) -> None:
    """§7.1 step 3: a doublet rule reads a doublet score, and cannot be fed an ambient estimate."""
    ambient_in_the_doublet_slot = screen("doublet_scores", "ambient", chain)
    result, _ = invoke(TASK_QC, [chain[SLOT_COUNTS], ambient_in_the_doublet_slot])
    assert result.status is ToolResultStatus.FAILED
    assert "must hold a doublet score product" in result.errors[0].message


def test_a_qc_node_handed_both_screens_reads_them_and_marks_by_both(chain) -> None:
    cells = chain[SLOT_COUNTS].matrix.cells
    doublet_cell, ambient_cell = cells[0].cell_key, cells[1].cell_key
    result, sink = invoke(
        TASK_QC,
        [
            chain[SLOT_COUNTS],
            screen("doublet_scores", "doublet", chain, flagged=doublet_cell),
            screen("ambient_scores", "ambient", chain, flagged=ambient_cell),
        ],
    )
    assert result.status is ToolResultStatus.SUCCESS
    emitted = sink.products[SLOT_QC_FLAGS]
    stored = read_product(type(emitted).schema_version, emitted.json_bytes())
    assert stored.json_bytes() == emitted.json_bytes()

    def flags_of(product: Product, cell_key: str) -> tuple[str, ...]:
        return next(row.flags for row in product.rows if row.cell_key == cell_key)

    assert "doublet_like" in flags_of(stored, doublet_cell)
    assert "ambient_like" in flags_of(stored, ambient_cell)
    assert "ambient_like" not in flags_of(stored, doublet_cell)
    assert "doublet_like" not in flags_of(stored, ambient_cell)
    #: Each screen marked exactly the one cell it was handed, so neither score was read as the other.
    counted = {rule.name: rule.flagged for rule in stored.rules}
    assert counted["doublet_like"] == 1 and counted["ambient_like"] == 1


# ------------------------------------------------------------------ stage-0 numbers stay inputs


def test_a_missing_stage0_number_comes_back_classified_as_an_input_gap(chain) -> None:
    """Appendix C1: the wrapper cannot fill in the project's own ceiling for how many cells to flag."""
    result, _ = invoke(TASK_QC, [chain[SLOT_COUNTS]], parameters=stage0(TASK_QC, max_flagged_share=None))
    assert result.status is ToolResultStatus.FAILED
    assert result.errors[0].error_class is ErrorClass.NEEDS_INPUT
    assert "not code defaults" in result.errors[0].message


def test_a_de_node_without_its_named_threshold_set_is_refused(chain) -> None:
    result, _ = invoke(
        TASK_PSEUDOBULK_DE,
        [chain[SLOT_COUNTS], chain[SLOT_CLUSTERS]],
        node_id="pseudobulk_de",
        parameters=stage0(TASK_PSEUDOBULK_DE, thresholds=None),
    )
    assert result.status is ToolResultStatus.FAILED
    assert "threshold set" in result.errors[0].message


def test_a_policy_handed_as_the_json_a_stored_plan_holds_is_still_the_policy(chain) -> None:
    """§7.7's rules live inside ``QcPolicy``; a plan read back from the index carries it as data."""
    policy = qc_policy()
    from_object, sink_object = invoke(TASK_QC, [chain[SLOT_COUNTS]])
    from_json, sink_json = invoke(
        TASK_QC, [chain[SLOT_COUNTS]], parameters=stage0(TASK_QC, qc_policy=json.loads(policy.model_dump_json()))
    )
    assert from_object.status is from_json.status is ToolResultStatus.SUCCESS
    assert from_object.output_asset_ids == from_json.output_asset_ids
    assert sink_object.products[SLOT_QC_FLAGS].rules == sink_json.products[SLOT_QC_FLAGS].rules
    assert parameters_sha256({"qc_policy": policy}) == parameters_sha256(
        {"qc_policy": policy.model_dump(mode="json")}
    )
    assert parameters_sha256({"qc_policy": policy}) != parameters_sha256(
        {"qc_policy": policy.model_copy(update={"policy_id": "other"})}
    )


def test_a_mapping_that_is_not_a_valid_policy_is_refused_rather_than_partly_read(chain) -> None:
    broken = json.loads(qc_policy().model_dump_json())
    broken["rules"] = [{"name": "no_metric"}]
    result, _ = invoke(TASK_QC, [chain[SLOT_COUNTS]], parameters=stage0(TASK_QC, qc_policy=broken))
    assert result.status is ToolResultStatus.FAILED
    assert "is not a valid QcPolicy" in result.errors[0].message


# ------------------------------------------------------------------ what a result has to record


def test_a_successful_node_records_its_inputs_its_products_and_its_promises(chain) -> None:
    counts = chain[SLOT_COUNTS]
    result, sink = invoke(TASK_QC, [counts])
    assert result.status is ToolResultStatus.SUCCESS
    assert result.unregistered is False
    assert result.errors == []
    assert result.reproducibility_level_achieved is not None
    assert result.input_asset_ids == [f"as_{counts.file_sha256()[:12]}"]
    assert result.input_hashes == {f"as_{counts.file_sha256()[:12]}": counts.file_sha256()}
    #: Both slots the QC task must emit are written, in slot order, and only then named.
    assert list(sink.written) == sorted(sink.written) == [SLOT_COUNTS, SLOT_QC_FLAGS]
    assert result.output_asset_ids == [sink.written[slot] for slot in sorted(sink.written)]
    assert set(result.metrics) and set(result.validation_results)


def test_the_parameters_are_reduced_to_data_so_the_hash_describes_the_node_not_who_built_it(chain) -> None:
    policy = qc_policy()
    result, _ = invoke(TASK_QC, [chain[SLOT_COUNTS]])
    recorded = result.parameters["qc_policy"]
    assert isinstance(recorded, dict)
    assert recorded == policy.model_dump(mode="json")
    assert result.execution.parameters_sha256 == parameters_sha256(stage0(TASK_QC))
    assert result.execution.seed is None, "the qc node carries no seed to attest"


def test_the_lossy_conversion_a_step_recorded_reaches_the_result_as_a_warning(chain) -> None:
    """§4.1: counts to log is lossy, and the warning travels with the asset instead of vanishing."""
    result, sink = invoke(TASK_NORMALIZE, [chain[SLOT_COUNTS]], node_id="normalize")
    assert result.status is ToolResultStatus.SUCCESS
    emitted = sink.products[SLOT_EXPRESSION]
    assert isinstance(emitted, ExpressionProduct) and emitted.conversion_warnings
    assert any("counts->log1p" in warning and "section 4.1" in warning for warning in result.warnings)


def test_a_failed_quality_check_makes_the_result_unsuccessful_not_an_averaged_away_one(
    chain, monkeypatch: pytest.MonkeyPatch
) -> None:
    real, required = TASK_RUNNERS[TASK_QC]

    def failing(invocation: Invocation) -> StepOutput:
        out = real(invocation)
        return StepOutput(
            products=out.products,
            metrics=out.metrics,
            checks=out.checks + (check("qc_flagged_share_within_ceiling", ScreeningResult.FAIL, "planted"),),
            warnings=out.warnings,
        )

    monkeypatch.setitem(TASK_RUNNERS, TASK_QC, (failing, required))
    result, sink = invoke(TASK_QC, [chain[SLOT_COUNTS]])
    assert result.status is ToolResultStatus.FAILED
    assert result.output_asset_ids == []
    #: The bytes were hashed and stored - a failed node still has to be inspectable, and its blobs are
    #: collectable as orphans - but nothing names them, so no downstream node can read them.
    assert set(sink.written) == {SLOT_COUNTS, SLOT_QC_FLAGS}
    assert result.errors[0].code == "critical_quality_failure"
    failed = [item for item in result.validation_results if item.result is ScreeningResult.FAIL]
    assert [item.name for item in failed] == ["qc_flagged_share_within_ceiling"]


def test_a_declared_check_that_never_ran_is_reported_as_a_breach_of_the_tools_promise(
    chain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§9.4: the ToolSpec promised the checks; a result that does not mention them is a contract breach."""
    real, required = TASK_RUNNERS[TASK_QC]
    declared = declaration_for(TASK_QC).quality_checks

    def silent(invocation: Invocation) -> StepOutput:
        out = real(invocation)
        return StepOutput(products=out.products, metrics=out.metrics, checks=(), warnings=out.warnings)

    monkeypatch.setitem(TASK_RUNNERS, TASK_QC, (silent, required))
    result, _ = invoke(TASK_QC, [chain[SLOT_COUNTS]])
    assert result.status is ToolResultStatus.SUCCESS
    breach = [warning for warning in result.warnings if "reported none of its declared checks" in warning]
    assert len(breach) == 1
    for name in declared:
        assert name in breach[0]


def test_a_check_the_spec_never_declared_is_reported(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    real, required = TASK_RUNNERS[TASK_QC]

    def inventive(invocation: Invocation) -> StepOutput:
        out = real(invocation)
        return StepOutput(
            products=out.products,
            metrics=out.metrics,
            checks=out.checks + (check("a_check_nobody_subscribed_to", ScreeningResult.PASS, None),),
            warnings=out.warnings,
        )

    monkeypatch.setitem(TASK_RUNNERS, TASK_QC, (inventive, required))
    result, _ = invoke(TASK_QC, [chain[SLOT_COUNTS]])
    assert any("a_check_nobody_subscribed_to" in warning and "never declared" in warning for warning in result.warnings)


def test_a_node_that_emitted_fewer_slots_than_its_task_must_emit_writes_nothing(
    chain, monkeypatch: pytest.MonkeyPatch
) -> None:
    real, required = TASK_RUNNERS[TASK_QC]

    def partial(invocation: Invocation) -> StepOutput:
        out = real(invocation)
        return StepOutput(
            products={SLOT_QC_FLAGS: out.products[SLOT_QC_FLAGS]},
            metrics=out.metrics,
            checks=out.checks,
            warnings=out.warnings,
        )

    monkeypatch.setitem(TASK_RUNNERS, TASK_QC, (partial, required))
    result, sink = invoke(TASK_QC, [chain[SLOT_COUNTS]])
    assert result.status is ToolResultStatus.FAILED
    assert result.errors[0].code == "output_missing"
    assert SLOT_COUNTS in result.errors[0].message
    assert sink.written == {}, "a node that did not finish cannot hand a half-written matrix downstream"


# ------------------------------------------------------------------ the ad-hoc channel (§9.6)


def adhoc_qc_grant() -> AdhocApproval:
    return AdhocApproval(
        approver_id="pi_01",
        project_id=PROJECT_ID,
        plan_id="plan_unit",
        node_id="qc",
        reason="single submission for this project only",
        approved_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )


def adhoc_qc_spec() -> ToolSpec:
    return adhoc_spec(
        tool_name="project_qc_probe",
        tool_version="0.1.0",
        task=TASK_QC,
        deployment=DEPLOYMENT,
        reason="the registered qc step cannot read this project's own contamination estimate",
    )


def test_an_adhoc_result_is_marked_as_one_and_may_never_be_retried(chain) -> None:
    """§9.6: the submission runs once, egress-free, and its result is labelled as unregistered."""
    spec = adhoc_qc_spec()
    registry = ToolRegistry()
    registry.grant(spec, adhoc_qc_grant())
    assert registry.resolve(spec.tool_name, spec.tool_version, now=NOW).tool_class is ToolClass.PROJECT_ADHOC
    assert spec.retry_limit == 0 and spec.egress_allowlist == []
    result, _ = invoke(TASK_QC, [chain[SLOT_COUNTS]], spec=spec)
    assert result.unregistered is True
    assert result.status is ToolResultStatus.SUCCESS
    assert result.reproducibility_level_achieved is ReproducibilityLevel.NUMERIC


def test_a_registered_result_is_not_marked_as_unregistered(chain) -> None:
    result, _ = invoke(TASK_QC, [chain[SLOT_COUNTS]])
    assert result.unregistered is False


def test_the_executor_can_attest_bytes_but_never_conclusion_reproducibility(chain) -> None:
    spec = SPECS[TASK_QC].model_copy(update={"reproducibility_level_target": ReproducibilityLevel.CONCLUSION})
    result, _ = invoke(TASK_QC, [chain[SLOT_COUNTS]], spec=spec)
    assert result.reproducibility_level_achieved is ReproducibilityLevel.BYTE


def test_a_failed_result_attains_no_reproducibility_level(chain) -> None:
    counts = replace(chain[SLOT_COUNTS], slot=SLOT_EXPRESSION)
    result, _ = invoke(TASK_FEATURE_SELECTION, [counts], node_id="feature_selection")
    assert result.status is ToolResultStatus.FAILED
    assert result.reproducibility_level_achieved is None
