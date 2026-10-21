"""The registered-tool wrapper around the reference steps (plan V2.0 sections 9.4, 9.6, 10.1).

This module is the only place a step becomes a ``ToolResult``, so it is where the promises in a
ToolSpec get checked against the data actually handed over. Two refusals matter more than the rest:

* the spec says whether its input is counts or log-normalised, and the product says which it is. A
  mismatch is not a rounding problem - pseudobulk summed from log values, or features ranked on raw
  counts, produces a table that looks ordinary and means something else, so the wrapper stops it; and
* every matrix in one invocation has to come from one genome build and one id system, because
  features from another reference would otherwise be dropped silently as "not present".

What a step returns is recorded as it returned it. The checks land in ``validation_results`` (§9.4)
and a failed check makes the result non-successful instead of being averaged away; a declared check
that never ran is reported as a breach of the tool's own promise; and an ad-hoc tool's result is
marked ``unregistered`` because §9.6 forbids it from being the only support for a delivered claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from bioadapt_policies.qc_policy import QcPolicy
from bioadapt_policies.robustness import RobustnessProtocol
from bioadapt_schemas.enums import ReproducibilityLevel, ScreeningResult, ToolClass
from bioadapt_schemas.errors import ApiError, ErrorClass
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_schemas.thresholds import Thresholds
from bioadapt_tool_registry.enums import ToolResultStatus
from bioadapt_tool_registry.result import ToolExecution, ToolResult
from bioadapt_tool_registry.spec import ToolSpec

from .codec import canonical_bytes
from .errors import StepFailure, invalid_input
from .matrix import CountMatrix
from .novelty import SLOT_COVERAGE, SLOT_STATE_AXIS, coverage_step, state_axis_step
from .products import (
    CellScoreProduct,
    ClusterProduct,
    ExpressionProduct,
    FeatureSelectionProduct,
    EmbeddingProduct,
    NeighborProduct,
    Product,
)
from .robustness import SLOT_STABILITY, ChainParameters, robustness_step
from .specs import (
    TASK_CLUSTERING,
    TASK_COVERAGE,
    TASK_EMBEDDING,
    TASK_FEATURE_SELECTION,
    TASK_MARKERS,
    TASK_NEIGHBORS,
    TASK_NORMALIZE,
    TASK_PSEUDOBULK_DE,
    TASK_QC,
    TASK_ROBUSTNESS,
    TASK_STATE_AXIS,
    declaration_for,
)
from .steps import (
    SLOT_CLUSTERS,
    SLOT_COUNTS,
    SLOT_DE_TABLE,
    SLOT_EMBEDDING,
    SLOT_EXPRESSION,
    SLOT_FEATURES,
    SLOT_GRAPH,
    SLOT_MARKERS,
    SLOT_QC_FLAGS,
    StepOutput,
    cluster_step,
    hvg_step,
    markers_step,
    neighbors_step,
    normalize_step,
    parse_contrasts,
    pca_step,
    qc_step,
    pseudobulk_de_step,
)

#: Screen slots a QC node may be handed, and the metric name the QcPolicy rules call them by.
SCREEN_SLOTS = {"doublet_scores": "doublet", "ambient_scores": "ambient"}


@dataclass(frozen=True)
class ToolInput:
    """One declared input: the slot the node names, the asset it was read from, the product inside it."""

    slot: str
    asset_id: str
    product: Product


@dataclass(frozen=True)
class Invocation:
    run_id: str
    node_id: str
    project_id: str
    spec: ToolSpec
    inputs: tuple[ToolInput, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def by_slot(self) -> dict[str, Product]:
        return {item.slot: item.product for item in self.inputs}


class OutputSink(Protocol):
    """Where a product becomes an asset. The runner owns storage; the wrapper must not choose it."""

    def write(self, product: Product) -> str: ...


def _plain(value: Any) -> Any:
    """Reduce contract objects to their own payload so the parameter hash is stable and readable."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_payload"):
        return value.to_payload()
    return str(value)


def parameters_sha256(parameters: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_bytes({key: _plain(value) for key, value in sorted(parameters.items())}))


def _require_slot(invocation: Invocation, slot: str, product_type: type) -> Product:
    found = invocation.by_slot().get(slot)
    if found is None:
        raise invalid_input(f"node {invocation.node_id} declared no input in slot {slot!r}", field=slot)
    if not isinstance(found, product_type):
        raise invalid_input(
            f"slot {slot!r} holds {type(found).__name__}; this tool needs {product_type.__name__}", field=slot
        )
    return found


def _contract(value: Any, kind: type, *, field: str) -> Any:
    """A project policy handed over as an object, or as the JSON a stored plan holds.

    A plan read back from the index is data, so its policy parameter arrives as a mapping here rather
    than as the object its author built. Rebuilding it through the contract's own validators is not
    ceremony: §7.7 makes a DROP rule illegal without an approving owner, and that rule lives inside
    ``QcPolicy`` - a step that read bare keys instead would run the policy the plan could not state.
    """
    if isinstance(value, kind):
        return value
    if isinstance(value, Mapping):
        try:
            return kind(**value)
        except (TypeError, ValueError) as error:
            raise invalid_input(
                f"{field} is not a valid {kind.__name__}: {error}", field=field
            ) from error
    raise invalid_input(
        f"{field} must be a {kind.__name__} or the mapping that describes one, not {type(value).__name__}",
        field=field,
    )


def _matrix_of(product: Product) -> CountMatrix:
    matrix = getattr(product, "matrix", None)
    if not isinstance(matrix, CountMatrix):
        raise invalid_input(f"{type(product).__name__} in slot {product.slot!r} carries no matrix", field=product.slot)
    return matrix


def check_compatibility(spec: ToolSpec, products: Sequence[Product]) -> None:
    """Refuse data the ToolSpec says this tool cannot read (§9.4 ``reference_compatibility``)."""
    compat = spec.reference_compatibility
    matrices: list[CountMatrix] = []
    for product in products:
        matrix = getattr(product, "matrix", None)
        if isinstance(matrix, CountMatrix):
            matrices.append(matrix)
            if compat.id_system is not None and matrix.genome.id_system is not compat.id_system:
                raise invalid_input(
                    f"{spec.tool_name} was reviewed for {compat.id_system} identifiers but this matrix is "
                    f"{matrix.genome.id_system}; a symbol/Ensembl mix matches no gene and reads as dropout",
                    field=product.slot,
                )
    if len({(matrix.genome.build, matrix.genome.version) for matrix in matrices}) > 1:
        mixed = sorted({f"{m.genome.build}@{m.genome.version}" for m in matrices})
        raise invalid_input(f"one invocation cannot read two reference builds: {mixed}", field=matrices[0].genome.build)
    for product in products:
        if not isinstance(product, ExpressionProduct):
            continue
        if compat.requires_counts and not product.counts:
            raise invalid_input(
                f"{spec.tool_name} sums or measures raw counts but slot {product.slot!r} is "
                f"{product.transform!r}; the table would not mean what its numbers appear to mean",
                field=product.slot,
            )
        if compat.needs_log and product.counts:
            raise invalid_input(
                f"{spec.tool_name} reads log-normalised expression but slot {product.slot!r} is raw counts",
                field=product.slot,
            )


def _run_qc(invocation: Invocation) -> StepOutput:
    expression = _require_slot(invocation, SLOT_COUNTS, ExpressionProduct)
    assert isinstance(expression, ExpressionProduct)
    policy = invocation.parameters.get("qc_policy")
    if policy is None:
        raise invalid_input(
            "qc needs the QcPolicy this project registered - a threshold written into the node without its "
            "source is exactly what section 7.7 forbids",
            field="qc_policy",
        )
    policy = _contract(policy, QcPolicy, field="qc_policy")
    screens: dict[str, Mapping[str, float]] = {}
    for slot, metric in SCREEN_SLOTS.items():
        found = invocation.by_slot().get(slot)
        if found is None:
            continue
        if not isinstance(found, CellScoreProduct) or found.metric != metric:
            raise invalid_input(
                f"slot {slot!r} must hold a {metric} score product from the screen that computed it", field=slot
            )
        screens[metric] = found.scores()
    gene_lists = {
        name: tuple(values)
        for name in ("mito_genes", "ribosomal_genes")
        if (values := invocation.parameters.get(name)) is not None
    }
    return qc_step(
        _matrix_of(expression),
        policy,
        thresholds_version=invocation.parameters.get("thresholds_version"),
        max_flagged_share=invocation.parameters.get("max_flagged_share"),
        mito_genes=gene_lists.get("mito_genes"),
        ribosomal_genes=gene_lists.get("ribosomal_genes"),
        doublet_scores=screens.get("doublet"),
        ambient_scores=screens.get("ambient"),
    )


def _run_normalize(invocation: Invocation) -> StepOutput:
    expression = _require_slot(invocation, SLOT_COUNTS, ExpressionProduct)
    assert isinstance(expression, ExpressionProduct)
    return normalize_step(
        _matrix_of(expression),
        target_sum=invocation.parameters.get("target_sum"),
        tolerance=float(invocation.parameters.get("tolerance", 1e-6)),
    )


def _run_features(invocation: Invocation) -> StepOutput:
    expression = _require_slot(invocation, SLOT_EXPRESSION, ExpressionProduct)
    assert isinstance(expression, ExpressionProduct)
    parameters = invocation.parameters
    return hvg_step(
        expression,
        n_top_features=parameters.get("n_top_features"),
        min_mean=parameters.get("min_mean"),
        max_mean=parameters.get("max_mean"),
        min_variance=parameters.get("min_variance"),
        seed=parameters.get("seed"),
    )


def _run_embedding(invocation: Invocation) -> StepOutput:
    expression = _require_slot(invocation, SLOT_EXPRESSION, ExpressionProduct)
    features = _require_slot(invocation, SLOT_FEATURES, FeatureSelectionProduct)
    assert isinstance(expression, ExpressionProduct) and isinstance(features, FeatureSelectionProduct)
    return pca_step(
        expression,
        features,
        n_components=invocation.parameters.get("n_components"),
        seed=invocation.parameters.get("seed"),
        iterations=int(invocation.parameters.get("iterations", 200)),
        n_loadings=int(invocation.parameters.get("n_loadings", 10)),
    )


def _run_neighbors(invocation: Invocation) -> StepOutput:
    embedding = _require_slot(invocation, SLOT_EMBEDDING, EmbeddingProduct)
    assert isinstance(embedding, EmbeddingProduct)
    return neighbors_step(
        embedding,
        k=invocation.parameters.get("k"),
        seed=invocation.parameters.get("seed"),
        metric=str(invocation.parameters.get("metric", "euclidean")),
    )


def _run_clustering(invocation: Invocation) -> StepOutput:
    graph = _require_slot(invocation, SLOT_GRAPH, NeighborProduct)
    assert isinstance(graph, NeighborProduct)
    return cluster_step(
        graph, resolution=invocation.parameters.get("resolution"), seed=invocation.parameters.get("seed")
    )


def _run_markers(invocation: Invocation) -> StepOutput:
    expression = _require_slot(invocation, SLOT_EXPRESSION, ExpressionProduct)
    clusters = _require_slot(invocation, SLOT_CLUSTERS, ClusterProduct)
    assert isinstance(expression, ExpressionProduct) and isinstance(clusters, ClusterProduct)
    return markers_step(expression, clusters, top_n=invocation.parameters.get("top_n"))


def _run_pseudobulk_de(invocation: Invocation) -> StepOutput:
    counts = _require_slot(invocation, SLOT_COUNTS, ExpressionProduct)
    clusters = _require_slot(invocation, SLOT_CLUSTERS, ClusterProduct)
    assert isinstance(counts, ExpressionProduct) and isinstance(clusters, ClusterProduct)
    thresholds = invocation.parameters.get("thresholds")
    if thresholds is None:
        raise invalid_input(
            "a DE node names the section 1.5 threshold set its support criteria are read against; the "
            "project's own set comes from the plan, it is not defaulted here (appendix C1)",
            field="thresholds",
        )
    thresholds = _contract(thresholds, Thresholds, field="thresholds")
    return pseudobulk_de_step(
        counts,
        clusters,
        contrasts=parse_contrasts(invocation.parameters.get("contrasts")),
        permutation_replicates=invocation.parameters.get("permutation_replicates"),
        seed=invocation.parameters.get("seed"),
        thresholds=thresholds,
    )


def _run_robustness(invocation: Invocation) -> StepOutput:
    expression = _require_slot(invocation, SLOT_EXPRESSION, ExpressionProduct)
    clusters = _require_slot(invocation, SLOT_CLUSTERS, ClusterProduct)
    assert isinstance(expression, ExpressionProduct) and isinstance(clusters, ClusterProduct)
    parameters = invocation.parameters
    protocol = parameters.get("robustness_protocol")
    if protocol is None:
        raise invalid_input(
            "a robustness node carries the section 7.5 protocol the project registered, including who "
            "approved any reduced arm; the step will not run a protocol it cannot show the origin of",
            field="robustness_protocol",
        )
    thresholds = parameters.get("thresholds")
    if thresholds is None:
        raise invalid_input(
            "the stability criteria are the project's section 1.5 thresholds, not a constant in the step "
            "(appendix C1)",
            field="thresholds",
        )
    candidates = parameters.get("candidate_clusters")
    if not candidates:
        raise invalid_input(
            "the node names the candidate clusters to re-derive under each perturbation",
            field="candidate_clusters",
        )
    return robustness_step(
        expression,
        clusters,
        protocol=_contract(protocol, RobustnessProtocol, field="robustness_protocol"),
        thresholds=_contract(thresholds, Thresholds, field="thresholds"),
        chain=ChainParameters.from_parameters(parameters),
        candidate_clusters=tuple(str(cluster) for cluster in candidates),
    )


def _coverage_thresholds(parameters: Mapping[str, Any]) -> Thresholds:
    thresholds = parameters.get("thresholds")
    if thresholds is None:
        raise invalid_input(
            "the coverage node names the section 1.5 threshold set its k and ratio floor are read "
            "against; neither is a constant in the step (appendix C1)",
            field="thresholds",
        )
    return _contract(thresholds, Thresholds, field="thresholds")


def _candidate_clusters(parameters: Mapping[str, Any], *, where: str) -> tuple[str, ...]:
    candidates = parameters.get("candidate_clusters")
    if not candidates:
        raise invalid_input(
            f"the {where} node names the candidate clusters it is judging; a measurement over every cluster "
            "in the dataset answers a question nobody asked",
            field="candidate_clusters",
        )
    return tuple(str(cluster) for cluster in candidates)


def _run_coverage(invocation: Invocation) -> StepOutput:
    embedding = _require_slot(invocation, SLOT_EMBEDDING, EmbeddingProduct)
    reference = _require_slot(invocation, SLOT_CLUSTERS, ClusterProduct)
    assert isinstance(embedding, EmbeddingProduct) and isinstance(reference, ClusterProduct)
    return coverage_step(
        embedding,
        reference,
        candidate_clusters=_candidate_clusters(invocation.parameters, where="coverage"),
        thresholds=_coverage_thresholds(invocation.parameters),
    )


def _run_state_axis(invocation: Invocation) -> StepOutput:
    expression = _require_slot(invocation, SLOT_EXPRESSION, ExpressionProduct)
    clusters = _require_slot(invocation, SLOT_CLUSTERS, ClusterProduct)
    assert isinstance(expression, ExpressionProduct) and isinstance(clusters, ClusterProduct)
    parameters = invocation.parameters
    axis = parameters.get("axis_genes")
    if not axis:
        raise invalid_input(
            "the continuous-state check measures a gradient the project declared as signed gene weights; "
            "an axis chosen by the step would decide the answer it is asked to measure",
            field="axis_genes",
        )
    return state_axis_step(
        expression,
        clusters,
        candidate_clusters=_candidate_clusters(invocation.parameters, where="continuous-state"),
        axis={str(gene): float(weight) for gene, weight in axis.items()},
        tail_quantile=parameters.get("tail_quantile"),
        tail_share_min=parameters.get("tail_share_min"),
        axis_explained_min=parameters.get("axis_explained_min"),
    )


#: task -> (runner, output slots that must be present before anything is written)
TASK_RUNNERS: dict[str, tuple[Callable[[Invocation], StepOutput], tuple[str, ...]]] = {
    TASK_QC: (_run_qc, (SLOT_QC_FLAGS, SLOT_COUNTS)),
    TASK_NORMALIZE: (_run_normalize, (SLOT_EXPRESSION,)),
    TASK_FEATURE_SELECTION: (_run_features, (SLOT_FEATURES,)),
    TASK_EMBEDDING: (_run_embedding, (SLOT_EMBEDDING,)),
    TASK_NEIGHBORS: (_run_neighbors, (SLOT_GRAPH,)),
    TASK_CLUSTERING: (_run_clustering, (SLOT_CLUSTERS,)),
    TASK_MARKERS: (_run_markers, (SLOT_MARKERS,)),
    TASK_PSEUDOBULK_DE: (_run_pseudobulk_de, (SLOT_DE_TABLE,)),
    TASK_ROBUSTNESS: (_run_robustness, (SLOT_STABILITY,)),
    TASK_COVERAGE: (_run_coverage, (SLOT_COVERAGE,)),
    TASK_STATE_AXIS: (_run_state_axis, (SLOT_STATE_AXIS,)),
}


#: Slots each task reads. Optional entries are the per-cell screens a QC node may or may not be
#: handed. The driver wires the plan's slots against this table rather than inferring it from the
#: step: §4.1 makes an inferred input the same kind of bug as an inferred transform.
TASK_INPUT_SLOTS: dict[str, tuple[tuple[str, bool], ...]] = {
    TASK_QC: ((SLOT_COUNTS, True), ("doublet_scores", False), ("ambient_scores", False)),
    TASK_NORMALIZE: ((SLOT_COUNTS, True),),
    TASK_FEATURE_SELECTION: ((SLOT_EXPRESSION, True),),
    TASK_EMBEDDING: ((SLOT_EXPRESSION, True), (SLOT_FEATURES, True)),
    TASK_NEIGHBORS: ((SLOT_EMBEDDING, True),),
    TASK_CLUSTERING: ((SLOT_GRAPH, True),),
    TASK_MARKERS: ((SLOT_EXPRESSION, True), (SLOT_CLUSTERS, True)),
    TASK_PSEUDOBULK_DE: ((SLOT_COUNTS, True), (SLOT_CLUSTERS, True)),
    TASK_ROBUSTNESS: ((SLOT_EXPRESSION, True), (SLOT_CLUSTERS, True)),
    TASK_COVERAGE: ((SLOT_EMBEDDING, True), (SLOT_CLUSTERS, True)),
    TASK_STATE_AXIS: ((SLOT_EXPRESSION, True), (SLOT_CLUSTERS, True)),
}


def required_slots(task: str) -> tuple[str, ...]:
    try:
        entries = TASK_INPUT_SLOTS[task]
    except KeyError:
        raise ValueError(f"no workflow step is declared for task {task!r}") from None
    return tuple(sorted(slot for slot, required in entries if required))


def _failed_checks(out: StepOutput) -> tuple[str, ...]:
    return tuple(sorted(check.name for check in out.checks if check.result is ScreeningResult.FAIL))


def _is_adhoc(spec: ToolSpec) -> bool:
    return spec.tool_class is ToolClass.PROJECT_ADHOC


def _status_for_error(error_class: ErrorClass) -> ToolResultStatus:
    return ToolResultStatus.POLICY_BLOCKED if error_class is ErrorClass.POLICY_BLOCKED else ToolResultStatus.FAILED


def _attained_level(spec: ToolSpec) -> ReproducibilityLevel:
    """The executor can attest byte reproducibility. Conclusion reproducibility is not its to grant."""
    if spec.reproducibility_level_target is ReproducibilityLevel.CONCLUSION:
        return ReproducibilityLevel.BYTE
    return spec.reproducibility_level_target


def _refusal(
    invocation: Invocation,
    execution: ToolExecution,
    errors: Sequence[ApiError],
    *,
    status: ToolResultStatus = ToolResultStatus.FAILED,
    checks: Sequence[Any] = (),
) -> ToolResult:
    spec = invocation.spec
    return ToolResult(
        run_id=invocation.run_id,
        node_id=invocation.node_id,
        project_id=invocation.project_id,
        tool_name=spec.tool_name,
        tool_version=spec.tool_version,
        status=status,
        execution=execution,
        input_asset_ids=[item.asset_id for item in invocation.inputs],
        input_hashes={item.asset_id: item.product.file_sha256() for item in invocation.inputs},
        parameters={key: _plain(value) for key, value in sorted(invocation.parameters.items())},
        errors=list(errors),
        validation_results=list(checks),
        unregistered=_is_adhoc(spec),
    )


def execute(
    invocation: Invocation,
    sink: OutputSink,
    *,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
) -> ToolResult:
    """Run one node and record it as a ToolResult.

    Everything a step refuses - a missing stage-0 number, a counts/log mismatch, a policy ceiling -
    comes back as a result carrying a classified error, because the orchestrator's exception branches
    route on an error class rather than on an exception someone has to guess the meaning of. The one
    exception is an invocation that read nothing: ``ToolResult.input_asset_ids`` requires at least one
    asset (§9.4), so no result can name a node that read no bytes. That is a caller error, and the
    driver's own wiring check refuses such a node before it reaches here.
    """
    spec = invocation.spec
    execution = ToolExecution(
        container_image_digest=spec.container_image.digest,
        tool_version=spec.tool_version,
        parameters_sha256=parameters_sha256(invocation.parameters),
        seed=invocation.parameters.get("seed"),
    )
    if not invocation.inputs:
        raise invalid_input(
            f"node {invocation.node_id} ran with no declared input asset, which no ToolResult can name: "
            "every result records at least one asset it read (§9.4)",
            field="inputs",
        )

    try:
        runner, required_slots = TASK_RUNNERS[spec.task]
    except KeyError:
        return _refusal(
            invocation,
            execution,
            [
                ApiError(
                    code="tool_not_runnable",
                    error_class=ErrorClass.FAILED,
                    message=f"{spec.tool_name} {spec.tool_version} declares task {spec.task!r}, which the "
                    "reference implementation does not implement",
                    field="task",
                )
            ],
        )

    try:
        check_compatibility(spec, [item.product for item in invocation.inputs])
        out = runner(invocation)
    except StepFailure as failure:
        return _refusal(
            invocation, execution, [failure.as_api_error()], status=_status_for_error(failure.error_class)
        )

    missing = sorted(slot for slot in required_slots if slot not in out.products)
    if missing:
        return _refusal(
            invocation,
            execution,
            [
                ApiError(
                    code="output_missing",
                    error_class=ErrorClass.FAILED,
                    message=f"{spec.tool_name} emitted no product for {missing}",
                    field=missing[0],
                )
            ],
            checks=out.checks,
        )

    emitted = {check.name for check in out.checks}
    declared = declaration_for(spec.task)
    warnings = list(out.warnings)
    not_run = sorted(set(declared.quality_checks) - emitted)
    if not_run:
        warnings.append(
            f"{spec.tool_name} reported none of its declared checks: {not_run}. The step did not run what "
            "its ToolSpec promised (section 9.4)."
        )
    undeclared = sorted(emitted - set(declared.declared_checks))
    if undeclared:
        warnings.append(f"{spec.tool_name} reported checks its ToolSpec never declared: {undeclared}")

    output_ids: list[str] = []
    for slot in sorted(out.products):
        output_ids.append(sink.write(out.products[slot]))
    for product in out.products.values():
        for warning in getattr(product, "conversion_warnings", ()):
            warnings.append(f"{product.slot}: {warning} (section 4.1: both derived assets are kept)")

    failed = _failed_checks(out)
    status = ToolResultStatus.FAILED if failed else ToolResultStatus.SUCCESS
    result = ToolResult(
        run_id=invocation.run_id,
        node_id=invocation.node_id,
        project_id=invocation.project_id,
        tool_name=spec.tool_name,
        tool_version=spec.tool_version,
        status=status,
        execution=execution,
        input_asset_ids=[item.asset_id for item in invocation.inputs],
        input_hashes={item.asset_id: item.product.file_sha256() for item in invocation.inputs},
        parameters={key: _plain(value) for key, value in sorted(invocation.parameters.items())},
        output_asset_ids=output_ids if status is ToolResultStatus.SUCCESS else [],
        metrics={key: value for key, value in sorted(out.metrics.items())},
        warnings=warnings,
        errors=(
            [
                ApiError(
                    code="critical_quality_failure",
                    error_class=ErrorClass.FAILED,
                    message=f"{spec.tool_name} failed its own quality checks: {failed}",
                    field=failed[0],
                )
            ]
            if failed
            else []
        ),
        validation_results=list(out.checks),
        reproducibility_level_achieved=_attained_level(spec) if status is ToolResultStatus.SUCCESS else None,
        unregistered=_is_adhoc(spec),
        started_at=started_at,
        finished_at=finished_at,
    )
    return result
