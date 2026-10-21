"""ToolSpecs for the reference workflow (plan V2.0 sections 9.4, 9.6, appendix C1).

A ToolSpec is the platform's promise about a tool, so the fields that carry risk are supplied by the
person registering it rather than written here: the pinned image digest, the resource request and its
measured peak, the licence review date, and the species list. Appendix C1 makes those stage-0 numbers
inputs, so ``Deployment`` has no defaults at all and a spec cannot be built by accident.

The declarative half - what each step assumes, when its output stops meaning what it seems to mean,
which quality checks it emits - is fixed here, because that is what the tool does and §9.4 requires it
to be documented before admission, not after a reviewer asks.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Optional, Sequence

from bioadapt_schemas.enums import GenomeIdSystem, LicenseReviewStatus, ReproducibilityLevel, ToolClass
from bioadapt_tool_registry.enums import FailurePolicy, NetworkMode
from bioadapt_tool_registry.registry import DEFAULT_ADHOC_LIMITS, AdhocSandboxLimits
from bioadapt_tool_registry.spec import (
    REQUIRED_PROVENANCE_FIELDS,
    ContainerImage,
    DataAccessPolicy,
    LicenseInfo,
    NetworkPolicy,
    ReferenceCompatibility,
    ResourceProfile,
    SchemaRef,
    ToolSpec,
)

MODALITY = "RNA"
#: Every step here identifies genes by symbol; a run whose matrix uses Ensembl IDs is refused by
#: ``implementation.py`` rather than silently matched against the wrong namespace.
ID_SYSTEM = GenomeIdSystem.HGNC_SYMBOL

TASK_QC = "qc"
TASK_NORMALIZE = "normalize"
TASK_FEATURE_SELECTION = "feature_selection"
TASK_EMBEDDING = "embedding"
TASK_NEIGHBORS = "neighbors"
TASK_CLUSTERING = "clustering"
TASK_MARKERS = "markers"
TASK_PSEUDOBULK_DE = "pseudobulk_de"
TASK_ROBUSTNESS = "robustness"
TASK_COVERAGE = "coverage"
TASK_STATE_AXIS = "state_axis"


@dataclass(frozen=True)
class Deployment:
    """The registration facts a human supplies; none of them are code defaults (appendix C1)."""

    container_repository: str
    container_tag: str
    container_digest: str
    tool_version: str
    species: tuple[str, ...]
    cpu: int
    memory_mb: int
    max_minutes: int
    measured_peak_memory_mb: int
    measured_on: str
    license_spdx_id: str
    license_display_name: str
    license_reviewed_at: date
    license_review_note: str

    def __post_init__(self) -> None:
        if not self.species:
            raise ValueError("a tool registration must name the species it was reviewed for")
        if not self.measured_on:
            raise ValueError("peak memory must be measured on a named dataset, not estimated")
        if "*" in self.species and not self.license_review_note:
            raise ValueError("a wildcard species claim needs the scope note that admission asks for")


@dataclass(frozen=True)
class StepDeclaration:
    """What one step promises, independent of who deploys it.

    ``quality_checks`` names the checks the step always reports; ``conditional_checks`` names the ones
    that only appear when they fire. The split exists because the wrapper compares the result against
    the declaration: a declared check that never ran is a contract breach, but a warning that had
    nothing to warn about is a clean run, and treating the two alike would flag every good result.
    """

    tool_name: str
    task: str
    input_schema: str
    output_schema: str
    quality_checks: tuple[str, ...]
    assumptions: tuple[str, ...]
    invalid_when: tuple[str, ...]
    requires_counts: Optional[bool]
    needs_log: Optional[bool]
    conditional_checks: tuple[str, ...] = ()
    embedding_source: Optional[str] = None
    supported_tissue_notes: Optional[str] = None
    upstream_reference_sources: tuple[str, ...] = ()
    reproducibility_level_target: ReproducibilityLevel = ReproducibilityLevel.BYTE
    failure_policy: FailurePolicy = FailurePolicy.HALT
    retry_limit: int = 0

    @property
    def declared_checks(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.quality_checks) | set(self.conditional_checks)))


def _matrix_inputs() -> tuple[str, ...]:
    return (
        "the matrix is one cell per row in the platform's own cell-key order, with gene symbols as columns",
        "the caller supplied the mitochondrial and ribosomal gene lists; an unclassified gene is recorded as unknown, never as zero",
    )


DECLARATIONS: tuple[StepDeclaration, ...] = (
    StepDeclaration(
        tool_name="scrnaseq_qc",
        task=TASK_QC,
        input_schema="scrnaseq_counts",
        output_schema="qc_flags",
        quality_checks=(
            "qc_marks_by_default",
            "qc_sensitivity_reported",
            "qc_flagged_share_within_ceiling",
        ),
        conditional_checks=("qc_rules_not_evaluated", "qc_policy_lint"),
        assumptions=(
            *_matrix_inputs(),
            "the default action is to label a cell at risk and keep it, so a downstream step can still be compared against the unfiltered baseline",
            "doublet and ambient screens are only evaluated when an upstream tool supplied per-cell scores; this tool does not model them itself",
            "each bound comes from a QcPolicy rule that names where its threshold came from",
        ),
        invalid_when=(
            "more than max_flagged_share of cells are flagged: the run is not a filtered dataset, it is an unanalysable one",
            "a rule's metric cannot be computed because its gene class or score column is absent",
            "cells are dropped without a project owner approving the deletion and annotating it in the report (section 7.7)",
        ),
        requires_counts=True,
        needs_log=False,
        supported_tissue_notes=(
            "high-mitochondrial tissues (heart, muscle, tumour biopsies) need a rule override rather than the "
            "blood-tissue bound; the policy records the override and who approved it"
        ),
        upstream_reference_sources=("10x Genomics single cell 3' v3 user guide", "scRNA-seq QC threshold diagnostics"),
    ),
    StepDeclaration(
        tool_name="scrnaseq_normalize",
        task=TASK_NORMALIZE,
        input_schema="scrnaseq_counts",
        output_schema="expression",
        quality_checks=("log2_transform", "library_size_normalised"),
        assumptions=(
            _matrix_inputs()[0],
            "target_sum is a per-cell count budget chosen for this dataset, not a platform constant",
            "log2(1 + x) is taken after scaling, so a later difference of means reads as a log2 fold change",
        ),
        invalid_when=(
            "the input is already log-normalised: normalising twice flattens the counts and the fold changes stop meaning anything",
            "a cell has zero total counts, which cannot be scaled and is reported rather than dropped silently",
        ),
        requires_counts=True,
        needs_log=False,
        upstream_reference_sources=("Scanpy normalize_total semantics",),
    ),
    StepDeclaration(
        tool_name="scrnaseq_feature_selection",
        task=TASK_FEATURE_SELECTION,
        input_schema="expression",
        output_schema="features",
        quality_checks=("features_ranked_deterministically",),
        conditional_checks=("feature_filter_too_strict",),
        assumptions=(
            "ranking is by cell-to-cell variance inside a mean-expression band on log-normalised values",
            "no mean-variance model (vst, seurat-style regression) is fitted, so expression level is partly baked into the ranking",
            "the band and the variance floor are stage-0 inputs; section 7.5 perturbs their count rather than trusting one cut",
        ),
        invalid_when=(
            "the input is raw counts, whose variance tracks library size rather than biology",
            "the filters leave fewer genes than the embedding needs, which is reported as a refusal rather than a smaller PCA",
        ),
        requires_counts=False,
        needs_log=True,
        upstream_reference_sources=("Scanpy highly variable_genes semantics",),
    ),
    StepDeclaration(
        tool_name="scrnaseq_pca",
        task=TASK_EMBEDDING,
        input_schema="expression",
        output_schema="embedding",
        quality_checks=("explained_variance_scope",),
        assumptions=(
            "centring is over the cells actually present and the selected features only",
            "components come from deflated power iteration, so the result is deterministic for a given seed and matrix",
            "explained-variance ratios are normalised over the retained components, not over the full variance",
        ),
        invalid_when=(
            "n_components exceeds the number of selected features or the cell count minus one",
            "a selected feature is missing from the matrix, which means the feature product and the expression product are not from the same run",
        ),
        requires_counts=False,
        needs_log=True,
        upstream_reference_sources=("Scanpy pca semantics",),
    ),
    StepDeclaration(
        tool_name="scrnaseq_neighbors",
        task=TASK_NEIGHBORS,
        input_schema="embedding",
        output_schema="graph",
        quality_checks=("neighbour_graph_built",),
        conditional_checks=("cells_without_neighbours",),
        assumptions=(
            "distances are computed in the recorded embedding's space, and the product names which embedding that was",
            "edge weight is 1 / (1 + distance); this is a stand-in for a shared-nearest-neighbour graph",
        ),
        invalid_when=(
            "k is at or above the number of cells, which describes a complete graph rather than a neighbourhood",
            "the embedding the graph was built over is not the embedding the clustering is handed",
        ),
        requires_counts=False,
        needs_log=True,
        embedding_source=TASK_EMBEDDING,
        upstream_reference_sources=("Scanpy neighbors semantics",),
    ),
    StepDeclaration(
        tool_name="scrnaseq_cluster",
        task=TASK_CLUSTERING,
        input_schema="graph",
        output_schema="clusters",
        quality_checks=("partition_names_its_graph",),
        assumptions=(
            "the partition maximises modularity greedily under the recorded resolution; it is NOT Leiden and says so in its warnings",
            "ties are broken by cell key so the same graph and seed give the same labels byte for byte",
        ),
        invalid_when=(
            "the graph digest in the product does not match the graph that was passed in",
            "a resolution is quoted as a result rather than as one of the perturbations section 7.5 requires",
        ),
        requires_counts=False,
        needs_log=True,
        embedding_source=TASK_NEIGHBORS,
        upstream_reference_sources=("Scanpy leiden / leidenalg semantics",),
    ),
    StepDeclaration(
        tool_name="scrnaseq_markers",
        task=TASK_MARKERS,
        input_schema="expression",
        output_schema="markers",
        quality_checks=("markers_are_rankings_only",),
        assumptions=(
            "score is log2FC weighted by detection inside the cluster, over mean expression, without a dispersion model",
            "one-vs-rest: a cluster that is a fragment of a population ranks the population's own genes correctly and looks novel anyway",
        ),
        invalid_when=(
            "the ranking is offered as identity evidence: section 7.8 item 5 makes a per-cluster ordering an aid to reading, never support on its own",
            "the cluster labels were not the ones this table was computed over",
        ),
        requires_counts=False,
        needs_log=True,
        upstream_reference_sources=("Scanpy rank_genes_groups semantics",),
    ),
    StepDeclaration(
        tool_name="scrnaseq_pseudobulk_de",
        task=TASK_PSEUDOBULK_DE,
        input_schema="scrnaseq_counts",
        output_schema="de_table",
        quality_checks=(
            "pseudobulk_unit_is_donor",
            "pseudobulk_design_recorded",
            "donor_direction_is_leave_one_out",
            "permutation_null_resolution",
        ),
        conditional_checks=("pseudobulk_pairing", "pseudobulk_power"),
        assumptions=(
            "cells of one donor are summed into one profile before any comparison, so the donor is the unit of replication",
            "a contrast whose donors appear in both arms is tested by flipping the sign of each paired difference; any other contrast permutes donor labels",
            "the p-value is a rank inside the assignments the null can reach, so its floor is set by the permutation count and the donor count",
            "Benjamini-Hochberg is applied within a contrast, and the threshold set the support criteria read against is named in the product",
        ),
        invalid_when=(
            "the unit of replication is a cell: technical replicates would be counted as biology",
            "fewer donors than min_n_per_group stand in a group, which is descriptive only (section 7.4)",
            "the table is read by its p-value ordering: a low-variance gene outranks a large effect",
            "raw counts are absent, because a pseudobulk profile built from log values is not a sum of expression",
        ),
        requires_counts=True,
        needs_log=False,
        upstream_reference_sources=("pseudo-scRNA-seq (Tibshirani lab) semantics", "Scanpy / decoupler pseudobulk conventions"),
    ),
    StepDeclaration(
        tool_name="scrnaseq_robustness",
        task=TASK_ROBUSTNESS,
        input_schema="expression",
        output_schema="stability",
        quality_checks=(
            "protocol_arms_covered",
            "perturbation_metrics_recorded",
            "stability_criteria_recorded",
            "small_cluster_permutation_null",
            "robustness_below_criteria",
        ),
        conditional_checks=(
            "planned_class_unmeasured",
            "perturbation_arm_not_runnable",
            "candidate_set_vanished",
            "agreement_not_measurable",
        ),
        assumptions=(
            _matrix_inputs()[0],
            "every arm re-runs feature selection, embedding, neighbourhood and partition from the expression "
            "product, so a perturbation changes the pipeline and not only the last label",
            "the protocol's arms come from a RobustnessProtocol object the project registered; a reduced "
            "protocol is only constructable with a named human approval (section 7.5)",
            "agreement is measured on cell sets: a chance-corrected index over shared cells and the best-match "
            "overlap of the candidate, never on cluster names",
            "a candidate at or below the small-cluster cell count is judged against a same-size random-set null",
            "the leave-one-donor and subsampling arms compare against the cells that still exist in that arm",
        ),
        invalid_when=(
            "the clustering handed to the node is not the one these chain parameters produce: the arms would "
            "perturb a partition this run cannot account for",
            "the node names no candidate clusters: stability of every group in the dataset is not the question "
            "the candidate list asked",
            "a class the protocol planned produced no measurement: the pass ratio would be computed over a "
            "smaller denominator than the card shows",
        ),
        requires_counts=False,
        needs_log=True,
        embedding_source=TASK_CLUSTERING,
        upstream_reference_sources=("Scanpy / leiden stability conventions", "section 7.5 minimum perturbation protocol"),
    ),
    StepDeclaration(
        tool_name="scrnaseq_coverage",
        task=TASK_COVERAGE,
        input_schema="embedding",
        output_schema="coverage",
        quality_checks=(
            "reference_set_named",
            "calibration_is_leave_one_out",
            "coverage_measured_against_the_reference_distribution",
            "coverage_is_not_a_novelty_claim",
            "coverage_gap_measured",
        ),
        conditional_checks=("thin_calibration_distribution", "reference_labels_are_external"),
        assumptions=(
            "the embedding is the space every distance is measured in, so the query and the reference must "
            "come from one run's embedding product",
            "the reference is the annotation the node was handed; the plan's calibration set is that same "
            "annotation's cells, each measured with itself removed so a normal cell's distance is not zero",
            "k and the ratio floor are read from the project's section 1.5 threshold set and recorded with "
            "its version (appendix C1)",
        ),
        invalid_when=(
            "labelled cells have no vector in the embedding handed to the node: the measurement would span "
            "two spaces and report one number",
            "the annotation leaves fewer reference cells than k: the step reports the gap instead of quietly "
            "shortening the neighbourhood",
            "the candidate clusters are the whole dataset: coverage of everything measures the reference, not "
            "a candidate",
        ),
        requires_counts=False,
        needs_log=False,
        embedding_source=TASK_CLUSTERING,
        upstream_reference_sources=("section 7.6 reference coverage versus novelty separation",),
    ),
    StepDeclaration(
        tool_name="scrnaseq_state_axis",
        task=TASK_STATE_AXIS,
        input_schema="expression",
        output_schema="state_axis",
        quality_checks=(
            "axis_is_a_declared_gradient",
            "state_axis_cuts_are_inputs",
            "continuous_state_measured",
            "candidate_explained_by_known_axis",
        ),
        assumptions=(
            _matrix_inputs()[0],
            "the axis is a signed gene set the project declared; the MVP method is that gradient, not "
            "diffusion pseudotime, and it can only answer the question those genes ask",
            "the reference is every cell the partition does not put in the candidate clusters",
            "RNA velocity is deliberately not used here (section 7.6): it needs spliced/unspliced counts this "
            "workflow does not produce",
        ),
        invalid_when=(
            "the declared axis names genes this matrix does not measure: scoring them as zero would invent a "
            "gradient the data does not hold",
            "the reference cells show no spread along the axis, so there is no tail for the candidate to be an "
            "end of",
            "fewer than two cells remain outside the candidate: a gradient needs a reference to be measured "
            "against",
        ),
        requires_counts=False,
        needs_log=True,
        embedding_source=TASK_CLUSTERING,
        upstream_reference_sources=("section 7.6 continuous-state check, MVP marker-gradient variant",),
    ),
)


def declaration_by_task() -> dict[str, StepDeclaration]:
    return {declaration.task: declaration for declaration in DECLARATIONS}


def declaration_for(task: str) -> StepDeclaration:
    """The promises attached to one task, so a result can be read against its own declaration."""
    try:
        return declaration_by_task()[task]
    except KeyError:
        raise ValueError(f"no workflow step is declared for task {task!r}") from None


def build_spec(
    declaration: StepDeclaration,
    deployment: Deployment,
    *,
    tool_class: ToolClass = ToolClass.REGISTERED,
    gpu: int = 0,
    data_access: Optional[DataAccessPolicy] = None,
    network: Optional[NetworkPolicy] = None,
) -> ToolSpec:
    """Turn one step's declaration plus one deployment's facts into a registrable ToolSpec."""
    return ToolSpec(
        tool_name=declaration.tool_name,
        tool_version=deployment.tool_version,
        task=declaration.task,
        tool_class=tool_class,
        supported_modalities=[MODALITY],
        supported_species=list(deployment.species),
        supported_tissue_notes=declaration.supported_tissue_notes,
        input_schema=SchemaRef(name=declaration.input_schema, version="v1"),
        output_schema=SchemaRef(name=declaration.output_schema, version="v1"),
        container_image=ContainerImage(
            repository=deployment.container_repository,
            tag=deployment.container_tag,
            digest=deployment.container_digest,
        ),
        resources=ResourceProfile(
            cpu=deployment.cpu,
            memory_mb=deployment.memory_mb,
            gpu=gpu,
            max_minutes=deployment.max_minutes,
            measured_peak_memory_mb=deployment.measured_peak_memory_mb,
            measured_on=deployment.measured_on,
        ),
        gpu_required=gpu > 0,
        data_access=data_access or DataAccessPolicy(),
        network=network or NetworkPolicy(mode=NetworkMode.CLOSED),
        quality_checks=list(declaration.declared_checks),
        failure_policy=declaration.failure_policy,
        retry_limit=declaration.retry_limit,
        provenance_fields=list(REQUIRED_PROVENANCE_FIELDS),
        reproducibility_level_target=declaration.reproducibility_level_target,
        license=LicenseInfo(
            spdx_id=deployment.license_spdx_id,
            display_name=deployment.license_display_name,
            review_status=LicenseReviewStatus.APPROVED,
            reviewed_at=deployment.license_reviewed_at,
            note=deployment.license_review_note,
        ),
        assumptions=list(declaration.assumptions),
        invalid_when=list(declaration.invalid_when),
        reference_compatibility=ReferenceCompatibility(
            requires_counts=declaration.requires_counts,
            id_system=ID_SYSTEM,
            needs_log=declaration.needs_log,
            embedding_source=declaration.embedding_source,
        ),
        upstream_reference_sources=list(declaration.upstream_reference_sources),
    )


def reference_tool_specs(deployment: Deployment, *, tasks: Optional[Sequence[str]] = None) -> dict[str, ToolSpec]:
    """Build the workflow's specs, keyed by tool name, ready for ``ToolRegistry.register``."""
    wanted = set(tasks) if tasks is not None else None
    specs: dict[str, ToolSpec] = {}
    for declaration in DECLARATIONS:
        if wanted is not None and declaration.task not in wanted and declaration.tool_name not in wanted:
            continue
        specs[declaration.tool_name] = build_spec(declaration, deployment)
    if wanted is not None and len(specs) != len(wanted):
        unknown = sorted(wanted - {value.task for value in specs.values()} - set(specs))
        raise ValueError(f"no such workflow task: {unknown}")
    return specs


def adhoc_spec(
    *,
    tool_name: str,
    tool_version: str,
    task: str,
    deployment: Deployment,
    reason: str,
    limits: AdhocSandboxLimits = DEFAULT_ADHOC_LIMITS,
) -> ToolSpec:
    """One submission's spec, built to the §9.6 sandbox shape rather than to the registry's.

    The sandbox is the point of the ad-hoc channel, so the constraints are not left to the submitter:
    no network, no egress, no retries, and a halt-on-failure policy. Resource and time caps still come
    from the deployment and are checked against the same ``AdhocSandboxLimits`` the registry applies, so
    an over-large ad-hoc tool fails here with the reason rather than at the compute node. The version is
    the submission's own, because name and version together are the key the grant is held under and the
    key the plan node later asks for (§9.6).
    """
    if not reason:
        raise ValueError("an ad-hoc submission must state why the registered tools do not answer the question")
    declared = StepDeclaration(
        tool_name=tool_name,
        task=task,
        input_schema="scrnaseq_counts",
        output_schema="adhoc_output",
        quality_checks=("adhoc_result_is_marked_unregistered",),
        assumptions=(f"submitted for this project only: {reason}",),
        invalid_when=("the result is the only support for a delivered conclusion (section 9.6)",),
        requires_counts=True,
        needs_log=None,
        failure_policy=FailurePolicy.HALT,
        reproducibility_level_target=ReproducibilityLevel.NUMERIC,
    )
    spec = build_spec(
        declared,
        deployment=replace(deployment, tool_version=tool_version),
        tool_class=ToolClass.PROJECT_ADHOC,
        network=NetworkPolicy(mode=NetworkMode.CLOSED),
    )
    violations = limits.violations(
        spec.model_copy(update={"retry_limit": 0, "egress_allowlist": [], "resources": spec.resources})
    )
    if violations:
        raise ValueError("ad-hoc submission outside the sandbox: " + "; ".join(violations))
    return spec.model_copy(update={"retry_limit": 0, "egress_allowlist": []})
