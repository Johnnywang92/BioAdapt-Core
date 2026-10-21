"""Shared fixtures for the reference-executor tests: one policy, one parameter set, one pipeline.

The stage-0 numbers a real project must confirm appear here as named constants so that a test can say
which number it is asserting about. They are inputs to the steps, never defaults inside them
(appendix C1), and each one is a deliberate choice for this synthetic dataset rather than a claim
about real data.
"""

from __future__ import annotations

from bioadapt_policies.qc_policy import CellFilter, QcAction, QcMetric, QcPolicy, QcRule, ThresholdSource
from bioadapt_policies.robustness import (
    MINIMUM_FEATURE_FACTORS,
    MINIMUM_RESOLUTION_FACTORS,
    MINIMUM_SEED_COUNT,
    RobustnessProtocol,
)
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS, Thresholds

from bioadapt_scrnaseq.products import ExpressionProduct
from bioadapt_scrnaseq.robustness import ChainParameters
from bioadapt_scrnaseq.steps import (
    SLOT_CLUSTERS,
    SLOT_COUNTS,
    SLOT_EXPRESSION,
    DeContrast,
    StepOutput,
    cluster_step,
    hvg_step,
    neighbors_step,
    normalize_step,
    pca_step,
    pseudobulk_de_step,
    qc_step,
)
from dataset import MITO_GENES, RIBOSOMAL_GENES, SyntheticData

TARGET_SUM = 10000.0
N_TOP_FEATURES = 50
MIN_MEAN = 0.5
MAX_MEAN = 8.0
MIN_VARIANCE = 0.05
N_COMPONENTS = 10
N_NEIGHBOURS = 15
RESOLUTION = 1.0
TOP_MARKERS = 4
PERMUTATION_REPLICATES = 200
MAX_FLAGGED_SHARE = 0.35
SEEDS = {
    "highly_variable_features": 7,
    "pca": 11,
    "neighbors": 13,
    "cluster": 17,
    "markers": 19,
    "pseudobulk_de": 23,
}

#: Each bound sits between the planted defects and the rest of the dataset - the gap itself is
#: asserted in ``test_steps.py``. A rule that fires on the damaged cells and nothing else is the rule
#: the plan asks for.
QC_RULES: tuple[QcRule, ...] = (
    QcRule(
        name="shallow_library",
        metric=QcMetric.TOTAL_COUNTS,
        apply=CellFilter.BELOW,
        lower=3000.0,
        threshold_source=ThresholdSource.DISTRIBUTION_DIAGNOSTIC,
        source_ref="quantile_p05_counts",
    ),
    QcRule(
        name="few_genes",
        metric=QcMetric.DETECTED_GENES,
        apply=CellFilter.BELOW,
        lower=90.0,
        threshold_source=ThresholdSource.DISTRIBUTION_DIAGNOSTIC,
        source_ref="quantile_p05_genes",
    ),
    QcRule(
        name="deep_library",
        metric=QcMetric.TOTAL_COUNTS,
        apply=CellFilter.ABOVE,
        upper=18000.0,
        threshold_source=ThresholdSource.DISTRIBUTION_DIAGNOSTIC,
        source_ref="quantile_p95_counts",
    ),
    QcRule(
        name="mito_high",
        metric=QcMetric.MITOCHONDRIAL_FRACTION,
        apply=CellFilter.ABOVE,
        upper=0.2,
        threshold_source=ThresholdSource.DISTRIBUTION_DIAGNOSTIC,
        source_ref="quantile_p99_mito",
    ),
    QcRule(
        name="doublet_like",
        metric=QcMetric.DOUBLET_SCORE,
        apply=CellFilter.ABOVE,
        upper=0.5,
        threshold_source=ThresholdSource.MANUAL,
        reason="scrublet default carried over until a calibration set replaces it",
    ),
    QcRule(
        name="ambient_like",
        metric=QcMetric.AMBIENT_SCORE,
        apply=CellFilter.ABOVE,
        upper=0.3,
        threshold_source=ThresholdSource.MANUAL,
        reason="decontex default carried over until a calibration set replaces it",
    ),
)


def qc_policy(*, rules: tuple[QcRule, ...] = QC_RULES, **overrides) -> QcPolicy:
    fields: dict = {
        "policy_id": "qc_pbmc_reference",
        "version": "1",
        "species": "human",
        "tissue": "pbmc",
        "platform": "10x_3v3",
        "dissociation": "none_required_blood",
        "expected_artifacts": ["doublets", "ambient_rna"],
        "artifact_screens": ["scrublet"],
        "rules": list(rules),
    }
    fields.update(overrides)
    return QcPolicy(**fields)


def drop_policy() -> QcPolicy:
    """The same policy with deletion approved, which is the only way a DROP rule may run."""
    dropping = tuple(
        rule.model_copy(update={"action": QcAction.DROP}) if rule.name == "doublet_like" else rule
        for rule in QC_RULES
    )
    return qc_policy(
        rules=dropping,
        cell_deletion_approved_by="project_owner_01",
        cell_deletion_reason="doublet-like cells removed from the clustering input only",
        deletion_annotated_in_report=True,
    )


def qc_step_for(data: SyntheticData, policy: QcPolicy | None = None, **overrides) -> StepOutput:
    fields: dict = {
        "thresholds_version": DEFAULT_THRESHOLDS.version,
        "max_flagged_share": MAX_FLAGGED_SHARE,
        "mito_genes": MITO_GENES,
        "ribosomal_genes": RIBOSOMAL_GENES,
        "doublet_scores": data.doublet_scores,
        "ambient_scores": data.ambient_scores,
    }
    fields.update(overrides)
    return qc_step(data.matrix, policy if policy is not None else qc_policy(), **fields)


def embedding_steps(data: SyntheticData, policy: QcPolicy | None = None) -> dict[str, StepOutput]:
    """QC through clustering, keeping every intermediate so a test can name the step it checks."""
    qc = qc_step_for(data, policy)
    norm = normalize_step(qc.product(SLOT_COUNTS).matrix, target_sum=TARGET_SUM)
    expression = norm.product(SLOT_EXPRESSION)
    hvg = hvg_step(
        expression,
        n_top_features=N_TOP_FEATURES,
        min_mean=MIN_MEAN,
        max_mean=MAX_MEAN,
        min_variance=MIN_VARIANCE,
        seed=SEEDS["highly_variable_features"],
    )
    pca = pca_step(expression, hvg.product("features"), n_components=N_COMPONENTS, seed=SEEDS["pca"])
    neighbours = neighbors_step(pca.product("embedding"), k=N_NEIGHBOURS, seed=SEEDS["neighbors"])
    cluster = cluster_step(
        neighbours.product("graph"), resolution=RESOLUTION, seed=SEEDS["cluster"]
    )
    return {"qc": qc, "normalize": norm, "hvg": hvg, "pca": pca, "neighbors": neighbours, "cluster": cluster}


def clustering(data: SyntheticData):
    return embedding_steps(data)["cluster"].product(SLOT_CLUSTERS)


def markers(data: SyntheticData, *, top_n: int = TOP_MARKERS):
    from bioadapt_scrnaseq.steps import markers_step

    steps = embedding_steps(data)
    return markers_step(steps["normalize"].product(SLOT_EXPRESSION), steps["cluster"].product(SLOT_CLUSTERS), top_n=top_n)


def de_steps(
    data: SyntheticData,
    *,
    genes: tuple[str, ...] | None = None,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    permutations: int = PERMUTATION_REPLICATES,
    cluster: str | None = None,
    seed: int = SEEDS["pseudobulk_de"],
    contrasts: list[DeContrast] | None = None,
):
    counts = embedding_steps(data)["qc"].product(SLOT_COUNTS)
    matrix = counts.matrix
    if genes is not None:
        matrix = matrix.subset(cells=[cell.cell_key for cell in matrix.cells], genes=genes)
    scoped = ExpressionProduct(
        slot=SLOT_COUNTS, matrix=matrix, transform=counts.transform, parameters=counts.parameters
    )
    return pseudobulk_de_step(
        scoped,
        clustering(data),
        contrasts=(
            contrasts if contrasts is not None else [DeContrast(group_a="case", group_b="control", cluster=cluster)]
        ),
        permutation_replicates=permutations,
        seed=seed,
        thresholds=thresholds,
    )


#: Draws behind the small-cluster null. The count moves the percentile, never what is measured, so the
#: tests use a small number to stay quick while the protocol object still records what it used.
NULL_DRAWS = 20


def chain_parameters(**overrides) -> ChainParameters:
    """The chain the clustering node ran with, so a robustness arm perturbs that run and not another."""
    fields = {
        "n_top_features": N_TOP_FEATURES,
        "min_mean": MIN_MEAN,
        "max_mean": MAX_MEAN,
        "min_variance": MIN_VARIANCE,
        "n_components": N_COMPONENTS,
        "k": N_NEIGHBOURS,
        "resolution": RESOLUTION,
        "features_seed": SEEDS["highly_variable_features"],
        "embedding_seed": SEEDS["pca"],
        "neighbors_seed": SEEDS["neighbors"],
        "cluster_seed": SEEDS["cluster"],
        "marker_top_n": TOP_MARKERS,
    }
    fields.update(overrides)
    return ChainParameters(**fields)


def chain_parameter_values(**overrides) -> dict:
    """The same settings as a node's parameters, which is how the wrapper hands them to the step."""
    settings = chain_parameters(**overrides)
    return {name: getattr(settings, name) for name in settings.__dataclass_fields__}


def robustness_protocol(**overrides) -> RobustnessProtocol:
    """Section 7.5's minimum protocol: all five classes, at their minimum factor sets."""
    fields = {
        "protocol_id": "robustness_pbmc",
        "version": "1",
        "resolution_factors": list(MINIMUM_RESOLUTION_FACTORS),
        "seed_count": MINIMUM_SEED_COUNT,
        "feature_factors": list(MINIMUM_FEATURE_FACTORS),
        "leave_one_donor": True,
        "subsample_fraction": 0.5,
        "permutation_replicates": NULL_DRAWS,
    }
    fields.update(overrides)
    return RobustnessProtocol(**fields)
